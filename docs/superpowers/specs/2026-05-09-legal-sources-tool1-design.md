# Tool 1 — `lookup_norm` against gesetze-im-internet.de — Design Spec

**Date:** 2026-05-09
**Status:** Draft, awaiting user approval
**Scope:** V1 of the reusable legal-sources tool set. Tool 1 only. Tool 2 (`fetch_urteil`) is deferred per separate decision.

---

## 1. Goal

Provide a reusable, framework-free Python tool that returns the **official, current, full Wortlaut** of a single German legal paragraph by structured citation (e.g., `BGB § 535`) — sourced from `gesetze-im-internet.de` (the BMJ-operated authoritative portal) — and that can be deployed as an **AWS Lambda registered behind Bedrock AgentCore Gateway** in `eu-central-1` for use by multiple legal-domain agents.

Non-goals for V1:

- Free-text search across laws (`search_norm`) — out of scope.
- Cross-Gesetz indexing or table-of-contents (`list_normen`) — out of scope.
- Any rechtsprechung functionality — that is Tool 2.

## 2. User-facing contract

### Input

```json
{
  "gesetz": "BGB",          // Gesetz-Abkürzung, case-insensitive
  "paragraph": "535",       // Paragraph identifier; supports suffixes like "535a"
  "absatz": "1"             // optional; specific Absatz; if omitted, full § returned
}
```

Validated by Pydantic. Unknown fields are rejected. `gesetz` is normalized to lower-case for lookup.

### Output (success)

```json
{
  "gesetz": "BGB",
  "gesetz_titel": "Bürgerliches Gesetzbuch",
  "paragraph": "535",
  "absatz": "1",
  "titel": "Inhalt und Hauptpflichten des Mietvertrags",
  "wortlaut": "Durch den Mietvertrag …",
  "stand": "2026-05-08",
  "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
  "stand_warnung": null
}
```

`stand_warnung` is non-null when the corpus is older than 30 days (a tighter bound than the existing 6-month warning, since for live deployment we expect daily refresh and a stale corpus is itself a signal something is wrong).

### Output (error)

Errors are returned as structured Pydantic models, not raised exceptions, so the Lambda handler can serialize them cleanly:

```json
{
  "error": "norm_not_found",
  "message": "§ 535 BGB Absatz 5 existiert nicht (BGB hat nur Absätze 1-3).",
  "gesetz": "BGB",
  "paragraph": "535",
  "absatz": "5"
}
```

Error codes:

- `unknown_gesetz` — Gesetz-Abkürzung not in the corpus.
- `paragraph_not_found` — Gesetz exists but the requested § is not present in the curated subset (e.g., `BGB § 1` is outside the §§ 194–580a curation).
- `absatz_not_found` — § exists but the requested Absatz does not.
- `corpus_unavailable` — S3 read failed *and* no in-memory fallback. Lambda returns 503-equivalent.
- `validation_error` — Pydantic rejected the input.

The agent must handle these distinctly: `paragraph_not_found` should prompt the user to extend the curated subset or pick a different §, while `corpus_unavailable` is an operational issue.

## 3. Architecture

```
                                 ┌───────────────────────────────┐
                                 │  EventBridge (cron, daily 02:00 UTC)
                                 └──────────────┬────────────────┘
                                                │
                                                ▼
        ┌──────────────────────────────────────────────────────────┐
        │  ingest Lambda (eu-central-1)                            │
        │  - wraps existing knowledge/ingest.py                    │
        │  - for each Gesetz: download xml.zip → parse → filter    │
        │  - writes to s3://kira-legal-corpus-eu-central-1/        │
        │      gesetze/<abk>.json                                  │
        │  - also writes _manifest.json (versions + stand)         │
        └──────────────────────────┬───────────────────────────────┘
                                   │
                                   ▼
                    ┌───────────────────────────────┐
                    │  S3 bucket (versioned, KMS)   │
                    │  region-locked: eu-central-1  │
                    └──────────────┬────────────────┘
                                   │ read
                                   ▼
        ┌──────────────────────────────────────────────────────────┐
        │  lookup_norm Lambda (eu-central-1)                       │
        │  - on cold start: download all <abk>.json into /tmp      │
        │  - on warm: serve from /tmp (re-check _manifest after    │
        │    age > 5 min)                                          │
        │  - returns LookupNormResult or LookupNormError           │
        └──────────────────────────┬───────────────────────────────┘
                                   │
                                   ▼
                    ┌───────────────────────────────┐
                    │  AgentCore Gateway target     │
                    │  (Lambda target type)         │
                    │  → exposed as MCP tool        │
                    │     `lookup_norm`             │
                    └───────────────────────────────┘
```

### Why this shape

- **S3-cached corpus + scheduled refresh** (option 1c from brainstorm): authoritative source is hit at most once per Gesetz per day, regardless of agent traffic; tool latency is dominated by S3 GET (~50ms warm, ~200ms cold) rather than upstream HTML parsing; lawyers see a consistent `stand` date across all calls within a 24h window.
- **One Lambda for the tool, one Lambda for the ingest** — clean IAM separation: the tool Lambda has read-only access to the corpus bucket and no internet egress; the ingest Lambda has internet egress + write access to the bucket but is invoked only by EventBridge.
- **Manifest-driven warm reload**: on each invocation past 5 minutes since last manifest fetch, the tool Lambda re-reads `_manifest.json` (small, ~1 KB) and re-pulls only changed Gesetze. Avoids bouncing the Lambda just to pick up new corpus.
- **`/tmp` cache** (Lambda's 512 MB ephemeral storage is more than enough — full curated corpus is ~1 MB).

## 4. Module layout

```
src/kira/legal_sources/
├── __init__.py
├── _common/                  # ── no `kira.*` imports allowed below this line
│   ├── __init__.py
│   ├── errors.py             # LookupNormError, ToolError base
│   ├── region.py             # eu-central-1 enforcement helpers
│   └── s3_corpus.py          # S3 read + /tmp cache + manifest check
├── gesetze/                  # ── no `kira.*` imports allowed below this line
│   ├── __init__.py
│   ├── schema.py             # Pydantic input/output models
│   ├── lookup_norm.py        # framework-free function (the core)
│   └── corpus_format.py      # JSON shape contracts; re-defines Gesetz/Norm
└── adapters/                 # ── `kira.*` imports permitted here only
    ├── __init__.py
    ├── kira_registry.py      # registers lookup_norm into KIRA's Tool registry
    ├── agent_sdk.py          # @tool wrapper for Claude Agent SDK consumers
    ├── lookup_handler.py     # AWS Lambda entrypoint for lookup_norm
    └── ingest_handler.py     # AWS Lambda entrypoint for the daily corpus refresh
                              #   (this file may import kira.knowledge.ingest)

tests/legal_sources/
├── __init__.py
├── conftest.py
├── fixtures/
│   ├── bgb_subset.json       # tiny corpus snapshot for unit tests
│   ├── betrkv_subset.json
│   └── captured/             # captured xml.zip snapshots for ingest replay
├── unit/
│   ├── test_lookup_norm.py
│   ├── test_schema.py
│   └── test_s3_corpus.py     # uses moto for S3 mock
├── adapters/
│   ├── test_lookup_handler.py
│   ├── test_ingest_handler.py
│   └── test_kira_registry.py
└── live/
    └── test_live_smoke.py    # marked @pytest.mark.live, opt-in only
```

**The no-`kira.*`-imports rule applies to `_common/` and `gesetze/` only.** `adapters/` is the explicit seam where deployment-shape glue is allowed to bridge into both `kira.*` (registry, ingest pipeline reuse) and external SDKs (Lambda runtime, Claude Agent SDK).

The `_common/` package houses cross-tool plumbing (S3 caching, error base, region pinning); when Tool 2 lands it reuses it. The `gesetze/` package is Tool-1-specific.

## 5. Reuse of existing code

The existing `src/kira/knowledge/` package has battle-tested parsing of the gesetze-im-internet.de XML format (`xml_parser.py`, `loader.py`, `schema.py`). The new module reuses those internals via **a single, narrow seam**: `legal_sources/gesetze/corpus_format.py` re-defines the JSON shape it expects (so `legal_sources/` doesn't import from `kira.knowledge`), and the **ingest Lambda** is allowed to import `kira.knowledge.ingest` because it lives in `adapters/lambda_handler.py` (deployment glue, not library code).

The existing `Gesetz` Pydantic model in `kira.knowledge.schema` is duplicated under `legal_sources/gesetze/corpus_format.py` to honor the no-`kira.*`-imports rule. Two near-identical models is a small, intentional cost.

The existing offline tools (`kira.agent.tools.norm_lookup`, etc.) **continue to exist unchanged** for KIRA's manual-loop callers. The new tool is parallel, not a replacement, until KIRA is migrated in a separate effort.

## 6. Adapters

Each adapter is a thin shim. Total lines of code per adapter should be under 50.

### 6.1 Lambda handler (canonical deployment shape)

The lookup Lambda is invoked by AgentCore Gateway with a Lambda-target event of the shape `{"tool_name": "lookup_norm", "input": { ...args... }, "tool_use_id": "...", ...}`. The handler also accepts a direct-invoke shape (just the args dict) for local testing and for the round-trip smoke script.

```python
# adapters/lookup_handler.py — orientation pseudocode, not final code
from pydantic import ValidationError
from kira.legal_sources.gesetze.schema import LookupNormInput
from kira.legal_sources.gesetze.lookup_norm import lookup_norm

def handler(event: dict, context) -> dict:
    args = event.get("input") if isinstance(event, dict) and "input" in event else event
    try:
        payload = LookupNormInput.model_validate(args)
    except ValidationError as e:
        return {"isError": True, "content": [{"type": "text", "text": f"validation_error: {e}"}]}
    result = lookup_norm(payload)
    return {"isError": False, "content": [{"type": "text", "text": result.model_dump_json()}]}
```

Return shape mirrors the MCP `CallToolResult` so AgentCore Gateway forwards it to the calling agent without re-encoding. Lambda gets configured with `LEGAL_CORPUS_BUCKET` env var (set per-environment by CDK); the function reads it via `_common/s3_corpus.py`.

### 6.2 KIRA `Tool` registry adapter

```python
# adapters/kira_registry.py
from kira.agent.tools._registry import Tool, register
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import LookupNormInput

def _run(input_data: dict) -> str:
    payload = LookupNormInput.model_validate(input_data)
    return lookup_norm(payload).to_agent_text()  # human-readable Markdown

register(Tool(
    name="lookup_norm",
    description="Lädt den Wortlaut eines deutschen Paragraphen aus gesetze-im-internet.de…",
    input_schema=LookupNormInput.model_json_schema(),
    run=_run,
))
```

This adapter is the **one place** allowed to bridge into `kira.*` from the legal-sources side; it lives in `adapters/`, not in the framework-free module proper.

### 6.3 Claude Agent SDK `@tool` adapter

```python
# adapters/agent_sdk.py
from claude_agent_sdk import tool
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import LookupNormInput

@tool("lookup_norm", "…", LookupNormInput.model_json_schema())
async def lookup_norm_tool(args: dict) -> dict:
    payload = LookupNormInput.model_validate(args)
    return {"content": [{"type": "text", "text": lookup_norm(payload).to_agent_text()}]}
```

## 7. Test strategy

Four tiers, in increasing cost and decreasing CI frequency:

### Tier 1 — Unit (every commit, fast, no network, no AWS)

- `test_lookup_norm.py`: drives the framework-free function against `tests/legal_sources/fixtures/bgb_subset.json` (a hand-crafted minimal corpus). Covers happy path, suffix paragraphs (`535a`), absatz selection, missing §, missing Absatz, unknown Gesetz, stale corpus warning.
- `test_schema.py`: Pydantic validation — rejects extra fields, normalizes `gesetz` to lowercase, accepts/rejects suffix patterns.
- `test_s3_corpus.py`: uses **moto** to mock S3. Covers cold-start fetch, manifest-based reload, KMS-encrypted-bucket reads, partial failure (one Gesetz file missing — degrades gracefully, the others still serve).

### Tier 2 — Adapter (every commit, fast, no network)

- `test_lambda_handler.py`: invokes the handler with synthetic events (API Gateway proxy shape and AgentCore Gateway shape — both supported). Verifies content-type, error envelope, exception isolation (a panic must become a 500, not crash the runtime).
- `test_kira_registry.py`: verifies the tool shows up in KIRA's registry on import and produces the right Markdown.

### Tier 3 — Recorded HTTP (every commit, fast, no live network)

- The ingest pipeline is exercised against captured `xml.zip` fixtures stored under `tests/legal_sources/fixtures/captured/`. We do not record at unit-test time; fixtures are committed and refreshed manually when the upstream format changes.
- Replays use `respx` (httpx-native mocking).

### Tier 4 — Live smoke (opt-in, gated on `RUN_LIVE_TESTS=1`, run nightly + before release)

- `tests/legal_sources/live/test_live_smoke.py`, marked `@pytest.mark.live`, skipped by default in pyproject `addopts`.
- Hits gesetze-im-internet.de directly with a real BGB §535 lookup, validates the parsed structure matches what the unit fixtures encode. If this fails, upstream changed and we need to refresh fixtures + parser.

### Tier 5 — Gateway round-trip (manual, pre-release, in a sandbox AWS account)

- A small `scripts/legal_sources_smoke.py` that lists the deployed AgentCore Gateway tools, calls `lookup_norm` for `BGB §535` end-to-end, and asserts the response shape. Intended to be run by a developer after `cdk deploy`, not in CI.

### Coverage targets

- Tier 1+2 must hit ≥ 95% line coverage on `src/kira/legal_sources/_common/` and `src/kira/legal_sources/gesetze/` (the framework-free code). The `adapters/` subtree is exercised by Tier 2 and contributes to coverage but is not gated by the same threshold (it's mostly glue).
- Enforced via `pytest --cov=kira.legal_sources._common --cov=kira.legal_sources.gesetze --cov-fail-under=95`.
- Tier 3 fixtures cover the shared ingest pipeline (which lives in the existing `kira.knowledge` package) — not part of this coverage gate.
- Tier 4 + 5 are correctness checks, not coverage drivers, and run outside the default CI pytest invocation.

## 8. Deployment

### Stack: AWS CDK (Python)

A new directory `infra/legal_sources/` houses a CDK app that deploys:

- `kira-legal-corpus-${AWS::AccountId}-eu-central-1` S3 bucket (account-suffixed because S3 names are global; versioned, KMS-encrypted with a customer-managed key, blocked public access, region-locked via bucket policy).
- `kira-legal-ingest` Lambda — Python 3.11, 1024 MB, timeout 5 min, outbound HTTPS to gesetze-im-internet.de allowed (no VPC), **scheduled by EventBridge daily at 02:00 UTC**. Source: `adapters/ingest_handler.py`, which calls `kira.knowledge.ingest.ingest()` and writes the resulting JSON to S3. Idempotent: skips PUT when the SHA-256 of the new payload matches the current S3 object's metadata `x-amz-meta-content-sha256`, to avoid versioning churn.
- `kira-legal-lookup-norm` Lambda — Python 3.11, 512 MB, timeout 10 s, **makes no outbound HTTP calls** (the only egress is S3 GET against the corpus bucket; no VPC is required, but if org policy mandates one, a VPC endpoint for S3 is added). IAM: read-only on the corpus bucket and its KMS key.
- IAM role for AgentCore Gateway to invoke the Lambda.
- CloudWatch log groups with 30-day retention for both Lambdas.

### AgentCore Gateway target registration

CDK L2 constructs for AgentCore Gateway are limited at the time of writing; the Gateway target itself is registered via a small post-deploy `scripts/register_gateway_target.py` that uses `boto3.client("bedrock-agentcore-control")` to create the target with type `lambda` and ARN of the lookup Lambda. **This is called out as a known seam** — when first-class CDK constructs land, the script gets folded into the stack.

The exact event-shape AgentCore Gateway delivers to a Lambda target (`tool_name` / `input` / `tool_use_id` field names) is verified against the live API as part of acceptance criterion #5; if the field names differ from what `lookup_handler.py` expects, the handler is updated and a unit fixture for the real shape is added.

### Region pinning

Every CDK stack is constructed with `env=Environment(region="eu-central-1")`. A unit test under `tests/infra/test_region_pin.py` walks the synthesized template and asserts no resource has a different region declared.

### Secrets / config

- No secrets in this tool set. Public sources, public S3 reads (within the bucket policy).
- Bucket name is the only environment-specific config; passed as an env var to both Lambdas.

## 9. Operational concerns

- **Cost**: S3 storage <€0.01/month (corpus ~1 MB). Lambda invocations: free tier covers expected agent traffic. EventBridge: negligible. Total expected: well under €1/month for V1.
- **Stale corpus alarm**: a CloudWatch alarm fires if the `_manifest.json` LastModified is older than 36 hours — ensures nobody silently serves stale law text for days.
- **Idempotency**: ingest Lambda is idempotent — it writes only when the new payload's SHA-256 differs from the existing object's `x-amz-meta-content-sha256`. EventBridge cron with `RetryAttempts=2` on the asynchronous invocation. Two concurrent ingests would briefly race on S3 PUT but the bucket is versioned so no data is lost.
- **Audit log**: every successful invocation logs a structured JSON line with `gesetz`, `paragraph`, `absatz`, `corpus_stand`, `caller_principal`. CloudWatch log group is the audit trail. (No PII to redact — the tool only sees law identifiers.)

## 10. Acceptance criteria for V1

The following must all be true before we declare Tool 1 done and re-open the Tool 2 conversation:

1. `pytest tests/legal_sources/ --cov=kira.legal_sources --cov-fail-under=95` is green on a clean checkout.
2. `RUN_LIVE_TESTS=1 pytest tests/legal_sources/live/` is green.
3. `cdk deploy` succeeds against a sandbox AWS account in `eu-central-1`.
4. `scripts/register_gateway_target.py` registers the Lambda as an AgentCore Gateway target.
5. `scripts/legal_sources_smoke.py` performs a full Gateway → Lambda → S3 → response round-trip for `BGB §535` and prints the wortlaut + stand.
6. The CloudWatch stale-corpus alarm has been verified to fire (forced via a dry-run test).

## 11. Open questions / explicit deferrals

- **Cross-account deployment**: design assumes single AWS account; multi-account (e.g., a dedicated legal-tools account vs. a per-tenant agent account) is not addressed and would require Gateway cross-account invoke permissions.
- **Custom Gesetz curation**: the curated subset (BGB §§ 194–580a, BetrKV, HeizkostenV) is inherited from `kira.knowledge.ingest.GESETZE`. Extending it (e.g., adding WEG, ZPO §§ relevant for vollstreckungs-questions) is non-V1 work.
- **Cache invalidation on schema change**: if we change the corpus JSON schema, in-flight Lambdas with old `/tmp` cache could serve mismatched data for up to 5 minutes after deploy. Mitigation: include a `schema_version` in the manifest and force-reload on mismatch.
- **Tool 2 dependency**: `_common/s3_corpus.py` is intentionally generic so Tool 2's Bundesgericht index can reuse the same caching pattern.
- **Local-dev corpus source**: `_common/s3_corpus.py` should also honour a `LEGAL_CORPUS_LOCAL_DIR` env var that points at a directory on disk and bypasses S3 entirely. This lets developers run the tool against `./data/gesetze/` from KIRA's existing overlay (and the existing offline tools `kira.agent.tools.norm_lookup` continue to be the default for local KIRA workflows). Implementation detail, but worth pinning so the interface design accommodates it from day one.
- **Adapter auto-registration**: `adapters/kira_registry.py` must NOT be imported by `kira.agent.tools.__init__` automatically — otherwise every local KIRA invocation would try to read S3. It's only loaded by the Lambda runtime and by callers who explicitly opt in.

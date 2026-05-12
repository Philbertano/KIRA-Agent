# Wire legal-sources Lambdas into KIRA's agent loop — design

**Status:** approved, ready for implementation plan
**Date:** 2026-05-12
**Author:** Claude (Opus 4.7) + Philip
**Scope:** single feature branch off `main`

## Goal

Make KIRA's agent loop call the deployed AWS legal-sources Lambdas (`kira-legal-lookup-norm`, `kira-legal-search`) so that real lawyer queries are answered from the 6,474-Gesetz corpus rather than the bundled 3-law JSON snapshot. Production-ready: single source of truth, no fallback paths.

## Background

V1 shipped `lookup_norm` over a curated 3-law subset (BGB, BetrKV, HeizkostenV) as bundled JSON in `src/kira/knowledge/gesetze/`. V2 expanded to all ~6,474 Bundesgesetze + Rechtsverordnungen and added `search_norm` (semantic search via Cohere multilingual v3 + S3 Vectors), deployed as Lambdas in `eu-central-1`.

The Lambdas are live and verified end-to-end (see `scripts/invoke_legal_lambdas.py`), but `src/kira/agent/core.py` doesn't call them — it still routes through the V0 tools backed by bundled JSON. Until the agent loop calls the Lambdas, the V2 investment hasn't been validated against a real KIRA query, and we can't observe real usage patterns to inform follow-ups (aliasing, long-paragraph chunking, etc.).

## Non-goals

- **No AgentCore Gateway** — direct boto3 invoke from the agent process. Gateway is a future PR.
- **No AgentCore Observability** — structured stderr logs + automatic CloudWatch Lambda metrics are sufficient for "production-ready" at this single-process scale. AgentCore Observability is a future PR.
- **No common-name alias map** (`WEG`→`WoEigG`, etc.). The model handles `unknown_gesetz` via a system-prompt hint to try alternative spellings. A real alias table is a future PR.
- **No long-paragraph chunking** for the embedder's 2000-char cap. Future PR.
- **Tool 2 (`fetch_urteil`)** remains deferred. The existing `urteil_fetch` / `search_rechtsprechung` tools in `agent/tools/` stay as-is for this PR.

## Architecture

```
kira ask <sachverhalt>
  → Agent.run()
    → Pseudonymize + leakage check          (unchanged)
    → Bedrock messages loop                  (unchanged)
      ← tool_use block from model
    → agent/tools/_registry dispatch
      → norm_lookup ── LegalSourcesClient.lookup_norm() ── boto3.invoke('kira-legal-lookup-norm')
      → norm_search ── LegalSourcesClient.search_norm() ── boto3.invoke('kira-legal-search')
      → frist                                (unchanged, deterministic Python)
    ← tool_result → next Bedrock turn
  ← final answer → Re-personalize → return
```

Three layers between the model's `tool_use` block and AWS:

1. **`agent/tools/_registry.REGISTRY`** — Python dict mapping tool name → `Tool`. `Agent.run()` reads it for the Bedrock `tools=[...]` parameter and dispatches incoming `tool_use` blocks to the right handler. In-process, no AWS.
2. **`LegalSourcesClient`** — new class encapsulating "talk to AWS": region, function names, retries, timeouts, MCP envelope unwrapping, structured logging. Tools import the client; tests mock the client.
3. **`boto3.client('lambda').invoke()`** — actual AWS API call. Credentials resolved via the existing chain that KIRA already uses for Bedrock.

## Components

### New file: `src/kira/agent/legal_client.py`

```python
class LegalSourcesClient:
    def __init__(self, lambda_client=None, region=REQUIRED_REGION,
                 lookup_fn=None, search_fn=None): ...
    def lookup_norm(self, inp: dict) -> dict: ...
    def search_norm(self, inp: dict) -> dict: ...
    def _invoke(self, fn_name: str, payload: dict) -> dict: ...
```

Behavior:
- Defaults: `region="eu-central-1"`, `lookup_fn=os.environ.get("KIRA_LEGAL_LOOKUP_FN", "kira-legal-lookup-norm")`, `search_fn=os.environ.get("KIRA_LEGAL_SEARCH_FN", "kira-legal-search")`
- boto3 client config: `Config(retries={"max_attempts": 3, "mode": "adaptive"}, read_timeout=30, connect_timeout=10)`
- `_invoke` serializes payload as JSON, calls `client.invoke`, parses the MCP envelope `{isError, content:[{type:"text", text:"<json>"}]}`, returns the inner dict (whether success or functional error like `unknown_gesetz`)
- Raises `LegalSourceUnavailable` on: boto3 `ClientError`, `ReadTimeoutError`, `EndpointConnectionError`, Lambda 5xx, non-JSON envelope, or empty content array
- Emits one structured log line per invocation: `{"invocation_id", "function", "latency_ms", "status"}`

### Modified file: `src/kira/agent/tools/norm_lookup.py`

Today: loads bundled JSON via `kira.knowledge.loader`, returns formatted text.

After: thin wrapper over `LegalSourcesClient.lookup_norm`. Tool schema unchanged (`gesetz: str`, `paragraph: str`, optional `absatz: str`).

```python
def _run(args: dict) -> str:
    try:
        result = _client.lookup_norm(args)
    except LegalSourceUnavailable as exc:
        log.warning("legal-source unavailable: %s", exc)
        return "Fehler: Rechtsquelle gerade nicht erreichbar. Bitte später erneut versuchen."
    return _format_for_model(result)
```

`_format_for_model` handles three cases:
- Success: returns titel + wortlaut + Stand + Quelle URL (the model needs all of this for citation)
- `unknown_gesetz` / `paragraph_not_found`: returns the Lambda's message verbatim (in German, model-readable)
- `validation_error`: returns the message verbatim so the model can correct its args

### Modified file: `src/kira/agent/tools/norm_search.py`

Today: keyword search over bundled JSON.

After: thin wrapper over `LegalSourcesClient.search_norm`. Schema gains `gesetz_filter: list[str]` and `type_filter: list[Literal["Gesetz","Verordnung"]]` to match the Lambda's full surface.

Result formatting: enumerated hits with `gesetz §paragraph — titel (score=0.69)` plus a 400-char wortlaut excerpt and Quelle URL.

### Modified file: `src/kira/agent/system_prompts.py`

`JUNIOR_ASSOCIATE_DE` updated per Section 3 of the brainstorm:

- Corpus description: "alle Bundesgesetze und Rechtsverordnungen (~6.500 Gesetze, tagesaktuell von gesetze-im-internet.de)"
- Tool workflow table (when to use `norm_lookup` directly vs. `norm_search` for discovery)
- Citation rule unchanged: every §-citation in the answer requires a prior successful `norm_lookup` call; search excerpts are not citation-grade
- `unknown_gesetz` guidance: try alternative spellings (e.g. "WEG" → "WoEigG"), then `norm_search`, then honestly admit not found — never improvise §-contents
- All `norm_list` references removed

### Modified file: `src/kira/cli.py`

Remove the `ingest` subcommand (and any helper imports it pulls). The daily Lambda handles corpus refresh; there's nothing left to ingest locally.

### Modified files (follow the move of `xml_parser` and `_extract_xml_from_zip`):

- `src/kira/legal_sources/adapters/ingest_handler.py` — change `from kira.knowledge.ingest import _extract_xml_from_zip` to `from kira.legal_sources._common.zip_extract import extract_xml_from_zip` (and rename to drop the underscore now that it's public); same for `parse_gii_xml`
- `scripts/backfill_corpus.py` — same import updates

### Moves

| From | To |
|---|---|
| `src/kira/knowledge/xml_parser.py` | `src/kira/legal_sources/_common/xml_parser.py` |
| `_extract_xml_from_zip` in `src/kira/knowledge/ingest.py` | `src/kira/legal_sources/_common/zip_extract.py` (renamed `extract_xml_from_zip`, public) |
| `tests/test_xml_parser.py` | `tests/legal_sources/unit/test_xml_parser.py` |

Side-effect: fixes the existing CLAUDE.md rule violation ("no `kira.*` imports inside `legal_sources/`") that V2 left in place because the parser sat in `kira/knowledge/`.

### Deletions

| File | Why |
|---|---|
| `src/kira/agent/tools/norm_list.py` | Tool obsoleted; discovery via `norm_search` |
| `src/kira/knowledge/loader.py` | Bundled-JSON loader, dead with the corpus |
| `src/kira/knowledge/schema.py` | Pydantic models for bundled-JSON shape, dead |
| `src/kira/knowledge/ingest.py` | V0 ingest, replaced by daily Lambda |
| `src/kira/knowledge/gesetze/bgb.json` | Bundled corpus |
| `src/kira/knowledge/gesetze/betrkv.json` | Bundled corpus |
| `src/kira/knowledge/gesetze/heizkostenv.json` | Bundled corpus |
| `src/kira/knowledge/__init__.py` | After the moves, directory is empty |
| `tests/test_tools.py` | V0 tool tests, superseded by new agent-tool tests |

The whole `src/kira/knowledge/` directory disappears.

### Updated docs

`CLAUDE.md`:
- "Common commands" section: remove `kira ingest`
- "Knowledge / law corpus" section: replace bundled-JSON description with "agent calls deployed Lambdas via `LegalSourcesClient`; corpus lives in AWS S3 and refreshes daily"
- "Tools" section: drop `norm_list`, update `norm_lookup`/`norm_search` descriptions

## Data flow (worked example)

User: `kira ask data/beispielsachverhalte/mietminderung.md --frage "Welche §§ sind einschlägig?"`

1. `Agent.run()` pseudonymizes Sachverhalt (replaces names with structured placeholders), runs leakage check.
2. Bedrock receives messages + `tools=[norm_lookup, norm_search, frist, urteil_fetch, search_rechtsprechung]`.
3. Model emits `tool_use: norm_search(query="Mietminderung wegen Schimmel")`.
4. `_registry["norm_search"].handler` runs → `LegalSourcesClient.search_norm({"query": "Mietminderung wegen Schimmel", "k": 10})` → boto3 invokes `kira-legal-search` → returns hits including BGB §536 (score 0.69).
5. Tool result text returned to Bedrock as the next user turn.
6. Model emits `tool_use: norm_lookup(gesetz="BGB", paragraph="536")`.
7. `_registry["norm_lookup"].handler` runs → `LegalSourcesClient.lookup_norm({"gesetz": "BGB", "paragraph": "536"})` → boto3 invokes `kira-legal-lookup-norm` → returns full wortlaut + Stand + Quelle URL.
8. Model composes the answer citing `§ 536 BGB` with the wortlaut.
9. `Agent.run()` re-personalizes placeholders, returns the answer.

Latency budget (informational, not enforced): search ~500ms p50, lookup ~150ms p50. A two-tool query adds <1s to the Bedrock turn.

## Error handling

| Category | Example | Tool returns | Why |
|---|---|---|---|
| Functional "not found" | `unknown_gesetz`, `paragraph_not_found` | Lambda message verbatim | Valid answer; model handles per prompt rules |
| Validation error | bad gesetz/paragraph type | Lambda message verbatim | Model corrects on retry |
| Infrastructure | boto3 ClientError, timeout, 5xx | `"Fehler: Rechtsquelle gerade nicht erreichbar..."` | Model tells the user honestly |

boto3 adaptive retry (3 attempts, exponential backoff) handles transient throttling and 5xx before `LegalSourceUnavailable` is raised.

The agent's overall Bedrock turn timeout is much longer than the tool's 30s read timeout, so a slow tool call won't break the message loop.

## Testing

### Unit tests (offline, run in CI)

New tests go under `tests/agent/` (following the nested-by-module convention `tests/legal_sources/` established; the older flat files like `tests/test_pseudonymizer.py` stay put).

| File | Asserts |
|---|---|
| `tests/agent/test_legal_client.py` | Mocked boto3 — payload shape, MCP envelope parsing, error mapping, retry triggers on 5xx, structured log line emitted |
| `tests/agent/tools/test_norm_lookup.py` | Mocked `LegalSourcesClient` — success / not-found / validation-error / unavailable all format correctly for the model |
| `tests/agent/tools/test_norm_search.py` | Same shape, plus `gesetz_filter` and `type_filter` pass-through |
| `tests/agent/test_system_prompts.py` | Prompt contains new workflow rules, corpus-size language; `norm_list` references absent |
| `tests/test_cli.py` (new) | `kira ingest` subcommand absent; invoking it returns argparse error |
| `tests/legal_sources/unit/test_xml_parser.py` (moved) | Same assertions as before the move |

### Live tests (opt-in, `RUN_LIVE_TESTS=1`)

| File | Asserts |
|---|---|
| `tests/agent/test_legal_client_live.py` | Real boto3 → deployed Lambdas: lookup BGB §535 returns the wortlaut; search "Mietminderung" returns ≥1 hit |
| `tests/agent/test_end_to_end.py` | Full `Agent.run()` against a synthetic Sachverhalt using a stub `LLMClient` that emits a scripted tool_use sequence — verifies wiring without paying Bedrock |

### Manual regression before merge

- `.venv/bin/kira demo` produces an answer citing BGB §535 from the Lambda
- One fresh Sachverhalt outside Mietrecht (e.g. an Arbeitsrecht question) produces a sensible draft, demonstrating breadth beyond the V0 3-law corpus

### Coverage

New agent code at the existing 95% gate that `tests/legal_sources/` already meets.

## Open questions / future work

- **Common-name aliases** (WEG → WoEigG, BImSchG → BImSchG_2013): documented in CLAUDE.md as a known gap. Add an alias table in a follow-up PR after observing real failure cases.
- **Long-paragraph chunking** for the embedder's 2000-char limit. Currently long §§ lose their tail from the embedding. Real fix: split long paragraphs into multiple vectors (`bgb-535-0`, `bgb-535-1`). Future PR.
- **Bedrock AgentCore Observability**: planned follow-up, will replace the current stderr structured logs with AgentCore's traced telemetry.
- **4 Gesetze with stale slug.upper() abkuerzung**: self-corrects on next daily ingest if upstream becomes reachable. No action needed in this PR.

## Success criteria

1. `.venv/bin/kira demo` succeeds and cites a real § retrieved from the Lambda (not from bundled JSON).
2. A fresh non-Mietrecht Sachverhalt produces a sensible draft.
3. All unit tests pass (`pytest tests/ -q -m 'not live and not perf'`).
4. Live tests pass when `RUN_LIVE_TESTS=1`.
5. `src/kira/knowledge/` and `kira ingest` no longer exist; CLAUDE.md reflects the new architecture.
6. Ruff clean on changed files.

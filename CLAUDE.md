# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

KIRA is a junior-associate AI agent for German rental law (Mietrecht), built with the Anthropic Agent SDK. It assists licensed lawyers (Rechtsanwälte) with research, fact-pattern extraction, and draft writing — the lawyer remains the responsible legal professional (RDG-konform). All responses must cite German law only; foreign-law analogies are forbidden.

## Common commands

```bash
# install (creates .venv via stdlib venv first if not present)
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

# tests
.venv/bin/python -m pytest tests/ -m 'not live and not perf'   # full unit suite
.venv/bin/python -m pytest tests/agent/test_legal_client.py -v # one file
RUN_LIVE_TESTS=1 .venv/bin/python -m pytest tests/agent/test_legal_client_live.py tests/agent/test_end_to_end.py -v   # live AWS tests
.venv/bin/python -m pytest --cov=kira tests/        # with coverage (pytest-cov is in [dev])

# CLI (entry point: kira)
.venv/bin/kira demo                                 # runs the example case end-to-end
.venv/bin/kira ask <sachverhalt.md> --frage "..."   # ad-hoc question
.venv/bin/kira ask <…> --force-tier opus            # override routing (haiku|sonnet|opus); also on `demo`
# lint
.venv/bin/ruff check src/ tests/
```

## Environment

`AWS_REGION` defaults to `eu-central-1`. Standard AWS credential resolution applies (`~/.aws/credentials`, `AWS_PROFILE`, env vars). The Bedrock client refuses to start in non-EU regions.

Other knobs (see `.env.example`):

- `KIRA_DEFAULT_MODEL` — default tier when the router has no opinion (`haiku|sonnet|opus`).
- `KIRA_LOG_LEVEL` — standard logging level.
- `KIRA_LEGAL_LOOKUP_FN` / `KIRA_LEGAL_SEARCH_FN` — override the deployed legal-sources Lambda names (defaults: `kira-legal-lookup-norm`, `kira-legal-search`).
- `KIRA_CACHE_DIR` — overrides `./data/cache/` (used by `urteil_fetch`).
- `KIRA_ALLOW_DIRECT_API=1` — opt-in to the `anthropic_direct` backend; only for synthetic-data testing (see "LLM client abstraction" below).

## Architecture

The agent runs a **manual tool-use loop** (in `agent/core.py`) — not the higher-level Agent SDK harness — so we keep full control over model routing and tool dispatch. Each `Agent.run()` call walks this pipeline:

1. **Tool-use loop** against the routed Bedrock model. Tools registered in `agent/tools/_registry.py::REGISTRY`.
2. **Final assistant text** is returned verbatim — real names, real amounts, real dates — so the lawyer can paste directly into Outlook/Word.

PII handling: KIRA does **not** pseudonymize. Compliance (BRAO §43e, DSGVO Art. 28/32) is achieved via AWS Bedrock DPA + EU residency (eu-central-1) + Microsoft 365 EU Data Boundary + chained Verschwiegenheitsverpflichtung — pseudonymization was removed 2026-05-17 after legal review confirmed it isn't required when the DPA chain is in place. See `docs/superpowers/specs/` for rationale.

### Model router

`router/rule_based.py::route()` classifies the query via keyword match into a `TaskType`, then maps to `ModelTier` via `router/policy.py::POLICY`. Tiers are abstract (HAIKU/SONNET/OPUS); concrete model IDs live in `llm/models.py::MODEL_IDS` per backend. The router auto-escalates SONNET→OPUS for long/multi-clause queries (heuristic in `_complexity_signal`). `force_tier` always wins. There's also a Haiku-based classifier fallback in `router/classifier.py` that is **not yet wired into the main route()** — it's available but only called when explicitly invoked.

### Knowledge / law corpus

The agent reads its legal corpus from the deployed `kira-legal-lookup-norm`
and `kira-legal-search` Lambdas in `eu-central-1`. The corpus covers all
~6,474 Bundesgesetze + Rechtsverordnungen from gesetze-im-internet.de and
refreshes daily via the ingest Lambda.

`src/kira/agent/legal_client.py::LegalSourcesClient` is the boto3 wrapper
the tools use. Region, function names, retry/timeout, and structured
logging all live there — the agent tools and tests mock the client, not
boto3 directly. The function names default to `kira-legal-lookup-norm` and
`kira-legal-search` but can be overridden via `KIRA_LEGAL_LOOKUP_FN` /
`KIRA_LEGAL_SEARCH_FN`.

The XML parser and zip extractor used by the ingest Lambda live at
`src/kira/legal_sources/_common/xml_parser.py` and
`src/kira/legal_sources/_common/zip_extract.py`. The legal-sources module
has no `kira.*` imports — it's extractable as a standalone package.

### Tools

Tools register themselves on import via `_registry.register()`. Importing `kira.agent.tools` triggers all registrations. Each tool returns a plain string (the tool-result content). Categories:

- **Norm tools** (`lookup_norm`, `search_norm`) call the deployed AWS
  Lambdas via `LegalSourcesClient`. `lookup_norm` returns the authoritative
  wortlaut + Stand + Quelle URL for one paragraph; `search_norm` does
  semantic search and returns top-k candidate paragraphs with score
  (excerpts only — citation must flow through `lookup_norm`). The corpus
  is ~6,474 Bundesgesetze + Rechtsverordnungen.
- **Rechtsprechung tools** (`urteil_fetch`) hit the network but are constrained by `ALLOWED_DOMAINS` (whitelist of official German jurisprudence sources). Redirects are re-checked against the whitelist. Results are cached in `./data/cache/urteile/`.
- **`berechne_frist`** is deterministic Python — no LLM call. Used so the agent never invents dates.

When adding a new tool, register it via `register(Tool(...))` and import the module from `agent/tools/__init__.py`. The registry is the single source of truth that `Agent.run()` exposes to the model.

### LLM client abstraction

`llm/client.py::build_client()` returns an `LLMClient(backend, raw)` wrapper. Backend `bedrock_eu` is the default and refuses non-EU regions. Backend `anthropic_direct` is gated by `KIRA_ALLOW_DIRECT_API=1` and intended only for synthetic-data testing — never for client data, due to § 43e BRAO and DSGVO requirements (US data residency).

`raw` is the actual Anthropic SDK client (`Anthropic` or `AnthropicBedrock`); both share the `messages.create()` API, so `agent/core.py` doesn't need to branch on backend.

## System prompt rules

`agent/system_prompts.py::JUNIOR_ASSOCIATE_DE` enforces hard anti-hallucination rules: every § citation requires a prior `lookup_norm` call; every Aktenzeichen requires a prior `search_rechtsprechung` / `fetch_urteil` call; every date computation requires `berechne_frist`. When changing tool semantics or adding new ones, update this prompt — the agent's reliability depends on it being accurate.

The prompt also enforces structured output sections (Sachverhalt / Rechtliche Einschätzung / Belegte Quellen / Offene Punkte / Empfehlung) and tells the model to keep the structured placeholders intact (don't try to guess real names).

## Reusable legal-sources tools (in progress, V1)

A new module `src/kira/legal_sources/` is being built to host **reusable, framework-free** tools that query official German legal sources. They are intended for reuse across multiple future legal-domain agents and for deployment as **Lambda targets behind AWS Bedrock AgentCore Gateway in `eu-central-1`** — not just for KIRA's manual loop.

Hard rules for this module (and its tests):

- **No `kira.*` imports** inside `src/kira/legal_sources/` or `tests/legal_sources/`. The module must be extractable into a standalone package (`de-legal-sources`) with `git mv` once a second consumer exists. Allowed deps: stdlib, `pydantic`, `httpx`, `boto3`, `beautifulsoup4`/`lxml` for parsers.
- **Three adapters live alongside, never inside the module proper**: (1) KIRA `Tool` registry adapter, (2) Claude Agent SDK `@tool` adapter, (3) AWS Lambda handler — the canonical deployment shape.
- **Region pinned to `eu-central-1`** for every AWS resource (S3, Lambda, EventBridge, Gateway target). The existing `bedrock_eu` policy in `llm/client.py` already enforces this for Bedrock; the legal-sources module mirrors it for everything else.
- **PII boundary**: pseudonymization stays inside the agent process. The legal-sources tools only ever receive structured legal references (`§`-numbers, Aktenzeichen, Gericht) — never client text. Tool input schemas reject free-text fields that could leak.

V2 (deployed) covers all ~6,500 Bundesgesetze and Rechtsverordnungen with `lookup_norm` + `search_norm`. V3 (in this branch) wires those tools into KIRA's agent loop, replacing the bundled-JSON path. Tool 2 (`fetch_urteil` against rechtsprechung-im-internet.de, with an S3 Vectors index for future semantic search) is **explicitly deferred** until the wired lookup/search path is validated in production. Do not start Tool 2 work without re-confirmation.

The current design spec lives at `docs/superpowers/specs/2026-05-09-legal-sources-tool1-design.md`.

## Conventions

- Code, docstrings, comments and user-facing text are in **German** (matches the legal domain). Tests, however, use English-style identifiers.
- Never push to `main` without explicit instruction; ask before creating new remote branches.
- Only ruff is enforced (rules `E,F,I,B,UP,SIM,RUF`, line-length 100, target `py311` — see `pyproject.toml`). `mypy` is installed in `[dev]` but has no project config; treat type errors as advisory unless the user wires it up.

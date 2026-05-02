# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

KIRA is a junior-associate AI agent for German rental law (Mietrecht), built with the Anthropic Agent SDK. It assists licensed lawyers (Rechtsanwälte) with research, fact-pattern extraction, and draft writing — the lawyer remains the responsible legal professional (RDG-konform). All responses must cite German law only; foreign-law analogies are forbidden.

## Common commands

```bash
# install (creates .venv via stdlib venv first if not present)
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

# tests
.venv/bin/python -m pytest tests/                   # full suite (~56 tests)
.venv/bin/python -m pytest tests/test_pseudonymizer.py -v   # one file
.venv/bin/python -m pytest tests/test_tools.py::test_norm_lookup_bgb_535   # one test

# CLI (entry point: kira)
.venv/bin/kira check-pseudonymisierung data/beispielsachverhalte/001_mietminderung_schimmel.md
.venv/bin/kira demo                                 # runs the example case end-to-end
.venv/bin/kira ask <sachverhalt.md> --frage "..."   # ad-hoc question
.venv/bin/kira ingest [bgb betrkv heizkostenv]      # refresh law corpus from gesetze-im-internet.de

# lint
.venv/bin/ruff check src/ tests/
```

`AWS_REGION` defaults to `eu-central-1`. Standard AWS credential resolution applies (`~/.aws/credentials`, `AWS_PROFILE`, env vars). The Bedrock client refuses to start in non-EU regions.

## Architecture

The agent runs a **manual tool-use loop** (in `agent/core.py`) — not the higher-level Agent SDK harness — so we keep full control over pseudonymization and routing. Each `Agent.run()` call walks this pipeline:

1. **Pseudonymize** the user query against `Party` definitions (role/gender/kind/age-band) → produces structured placeholders like `[MIETER_1:m,nat,~60-69]`.
2. **Leakage check** — regex scan for residual PII and party names. Hard-fails (`LeakageError`) before any LLM call. If this triggers, do not bypass it; investigate.
3. **Tool-use loop** against the configured Bedrock model. Tools registered in `agent/tools/_registry.py::REGISTRY`.
4. **Re-personalize** the final assistant text locally (placeholders → real names) before returning.

### Pseudonymizer subtlety

In `pseudonymizer/pipeline.py`, **PII patterns (email/IBAN/phone/address) must run before party-name substitution** — otherwise names get replaced inside email addresses (`klaus.mueller@example.de` → `klaus.[MIETER_1]@example.de`). This bit us once; the test `test_email_replacement` enforces the order.

### Model router

`router/rule_based.py::route()` classifies the query via keyword match into a `TaskType`, then maps to `ModelTier` via `router/policy.py::POLICY`. Tiers are abstract (HAIKU/SONNET/OPUS); concrete model IDs live in `llm/models.py::MODEL_IDS` per backend. The router auto-escalates SONNET→OPUS for long/multi-clause queries (heuristic in `_complexity_signal`). `force_tier` always wins. There's also a Haiku-based classifier fallback in `router/classifier.py` that is **not yet wired into the main route()** — it's available but only called when explicitly invoked.

### Knowledge / law corpus

`knowledge/loader.py` loads German laws with this precedence:

1. Overlay directory `./data/gesetze/<abk>.json` (written by `kira ingest`).
2. Package-bundled JSON in `src/kira/knowledge/gesetze/`.

`kira ingest` downloads the official XML zip from gesetze-im-internet.de, parses it with `knowledge/xml_parser.py` (the gii-norm DTD format — `<norm>` containing `<metadaten>` and `<textdaten><text><Content><P>`), filters by paragraph range or list, writes JSON. The parser tolerates missing optional elements; tests use a synthetic fixture in `tests/test_xml_parser.py` rather than the live download.

Each loaded `Gesetz` carries a `stand` date. `loader.stand_warnung()` returns a warning string when the corpus is ≥ 6 months old; the lookup tools embed this in their output and the system prompt instructs the agent to surface it under "Offene Punkte für den Anwalt".

### Tools

Tools register themselves on import via `_registry.register()`. Importing `kira.agent.tools` triggers all registrations. Each tool returns a plain string (the tool-result content). Categories:

- **Norm tools** (`norm_lookup`, `norm_search`, `norm_list`) read from the local corpus only — they never hit the network.
- **Rechtsprechung tools** (`urteil_fetch`) hit the network but are constrained by `ALLOWED_DOMAINS` (whitelist of official German jurisprudence sources). Redirects are re-checked against the whitelist. Results are cached in `./data/cache/urteile/`.
- **`berechne_frist`** is deterministic Python — no LLM call. Used so the agent never invents dates.

When adding a new tool, register it via `register(Tool(...))` and import the module from `agent/tools/__init__.py`. The registry is the single source of truth that `Agent.run()` exposes to the model.

### LLM client abstraction

`llm/client.py::build_client()` returns an `LLMClient(backend, raw)` wrapper. Backend `bedrock_eu` is the default and refuses non-EU regions. Backend `anthropic_direct` is gated by `KIRA_ALLOW_DIRECT_API=1` and intended only for synthetic-data testing — never for client data, due to § 43e BRAO and DSGVO requirements (US data residency).

`raw` is the actual Anthropic SDK client (`Anthropic` or `AnthropicBedrock`); both share the `messages.create()` API, so `agent/core.py` doesn't need to branch on backend.

## System prompt rules

`agent/system_prompts.py::JUNIOR_ASSOCIATE_DE` enforces hard anti-hallucination rules: every § citation requires a prior `lookup_norm` call; every Aktenzeichen requires a prior `search_rechtsprechung` / `fetch_urteil` call; every date computation requires `berechne_frist`. When changing tool semantics or adding new ones, update this prompt — the agent's reliability depends on it being accurate.

The prompt also enforces structured output sections (Sachverhalt / Rechtliche Einschätzung / Belegte Quellen / Offene Punkte / Empfehlung) and tells the model to keep the structured placeholders intact (don't try to guess real names).

## Conventions

- Code, docstrings, comments and user-facing text are in **German** (matches the legal domain). Tests, however, use English-style identifiers.
- Tests for the pseudonymizer are the most safety-critical — if they go red, do not weaken assertions to make them pass; investigate. A pseudonymizer regression risks leaking client data to the cloud.
- Develop on branch `claude/rental-law-ai-agent-IzzsZ`. Don't push elsewhere without explicit instruction.
- Pyright/mypy are not currently wired up; ruff is.

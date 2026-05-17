# Phase 0 — SaaS foundation: multi-tenancy shape + M365 Custom Engine Agent on AgentCore Runtime

**Status:** approved, ready for implementation plan
**Date:** 2026-05-17
**Author:** Claude (Opus 4.7) + Philip
**Scope:** single feature branch off `main`
**Branch:** `feat/phase0-saas-foundation`

## Goal

Move KIRA from a local CLI tool to a cloud-hosted, multi-tenant-shaped, Outlook-integrated service. A licensed German tenancy-law lawyer can `@KIRA` in Microsoft 365 Copilot inside Outlook, ask a legal question in German, watch streaming progress as KIRA looks up §§, and receive a structured citation-quality answer — all without leaving Outlook.

## Background

KIRA is a German rental-law AI agent built for licensed lawyers (Rechtsanwälte). V1–V3 (now merged to `main`) built a local CLI with:
- Manual Anthropic tool-use loop
- Bedrock Claude (eu-central-1) routing via task-type policy
- Two deployed AWS Lambdas (`kira-legal-lookup-norm`, `kira-legal-search`) backed by ~6,474 Bundesgesetze + Rechtsverordnungen and 93,578 semantic embeddings (S3 Vectors + Cohere multilingual v3)
- Daily ingest Lambda + EventBridge schedule
- Cloudflare Worker proxy for upstream juris.de IP-block bypass

Phase 0 adds: an HTTPS service that wraps `Agent.run()`, exposes it via the Microsoft 365 Agents SDK as a "custom engine agent", validates Entra ID JWTs, resolves multi-tenant identity, streams progress, and emits a PII-free audit log.

After Phase 0, the system can scale to multiple firms with line-level changes (no architectural retrofit).

## Non-goals

- **AppSource publication** — Phase 3, requires compliance package
- **Billing infrastructure** — Phase 3, AppSource handles per-seat
- **Multi-tenant admin UI** — Phase 2+
- **Case packs / specialised tenancy expertise** — Phase 1, expert collaboration
- **`mandate_ref` input field** — Phase 2 (no Copilot UI for free-text fields)
- **Outlook web add-in** — separate workstream
- **Right-to-erasure self-service** — Phase 2 (manual support for MVP)
- **EU AI Act Annex III docs** — separate legal workstream
- **Operational tooling** (Grafana, alerts, on-call) — wait for paying customers
- **Pseudonymizer** — removed in pre-Phase-0 cleanup; not coming back

## Architecture

```
Lawyer in Outlook
  → opens Copilot pane: "@KIRA Was sagt § 573 BGB?"
    → M365 Copilot routes to custom engine agent
      ↓ HTTPS POST /api/messages (Bot Framework Activity Protocol)
      ↓ Authorization: Bearer <Entra ID JWT>
  ┌────────────────────────────────────────────────────────────┐
  │ API Gateway HTTP API (eu-central-1)                        │
  │   • JWT authorizer (Entra OIDC discovery)                  │
  │   • Validates iss, aud, exp, nbf, signature                │
  │   • Forwards claims to AgentCore Runtime                   │
  ├────────────────────────────────────────────────────────────┤
  │ AgentCore Runtime — kira-copilot-agent                     │
  │   container: Python 3.13 + M365 Agents SDK                 │
  │                                                            │
  │   on_message_activity(activity):                           │
  │     1. tenant = resolve_tenant(jwt_claims)                 │
  │     2. assert_subscription_active(tenant)                  │
  │     3. stream "Verarbeite Anfrage..." → Outlook            │
  │     4. routing = router.route(activity.text)               │
  │     5. result = Agent.run(query, routing, on_tool=stream)  │
  │     6. emit_audit_row(tenant, ..., result)                 │
  │     7. return final response activity                      │
  └────────────────────────────────────────────────────────────┘
        ↓                          ↓
   Bedrock Claude EU         DynamoDB (kira-audit)
   + legal-sources Lambdas
```

Three layers between Outlook and KIRA's existing agent loop:

1. **API Gateway** — JWT validation, request authentication, request forwarding. No business logic.
2. **M365 Agents SDK wrapper** (new `src/kira/copilot/`) — translates Bot Framework Activity Protocol ↔ KIRA's existing `Agent.run` interface. Handles streaming.
3. **Existing `kira.agent.Agent.run`** — unchanged. Stateless function: `(query, routing) → AgentResult`.

The Copilot module is purely an adapter. Zero changes to the agent loop, tools, system prompt, or legal-sources infrastructure.

## Components

### New files in `src/kira/copilot/`

| File | Responsibility |
|---|---|
| `__init__.py` | Package marker |
| `agent_app.py` | M365 Agents SDK app instance; `on_message_activity` handler |
| `tenant_resolver.py` | JWT claim → KIRA tenant record via DDB + allowlist fallback |
| `streamer.py` | Translates `Agent.run` tool-call progress into outbound Bot Framework activities |
| `audit.py` | DDB audit emitter, PII-free fields, per-tool args-summary curation |
| `subscription.py` | Active-subscription check (allowlist for MVP) |
| `config.py` | Environment-variable parsing (`AUDIT_TABLE_NAME`, `TENANTS_TABLE_NAME`, etc.) |
| `main.py` | uvicorn/AgentCore Runtime entry point |

### New files in `infra/copilot_agent/`

| File | Responsibility |
|---|---|
| `app.py` | CDK app entry |
| `stack.py` | API Gateway HTTP API + AgentCore Runtime + DDB tables + IAM |
| `cdk.json` | CDK config, points at `../../.venv/bin/python` |
| `requirements.txt` | aws-cdk-lib, constructs |
| `Dockerfile` | python:3.13-slim base; installs the kira wheel; exposes port 8080 |
| `manifest/manifest.json` | M365 app manifest (sideloaded into stepfather's tenant) |
| `manifest/icon-color.png`, `manifest/icon-outline.png` | App icons (placeholders for MVP) |

### Existing code reused

Unchanged:
- `src/kira/agent/legal_client.py::LegalSourcesClient`
- `src/kira/agent/tools/*`
- `src/kira/agent/system_prompts.py::JUNIOR_ASSOCIATE_DE`
- `src/kira/router/*` (TaskType, ModelTier routing)
- `src/kira/llm/client.py`
- All deployed AWS resources: legal-sources Lambdas, S3 bucket, S3 Vectors index, ingest schedule, Cloudflare Worker

Backwards-compatible extension:
- `src/kira/agent/core.py::Agent.run` gains two optional keyword-only callback parameters: `on_tool_start: Callable[[str, dict], None] | None = None` and `on_tool_end: Callable[[str, str], None] | None = None`. Defaults are no-ops (current behaviour preserved). The streamer passes real callbacks to translate tool-call lifecycle events into outbound Bot Framework activities. All existing call sites (`kira ask`, `kira demo`, end-to-end test) continue working unchanged.

### Existing tests reused unchanged

All `tests/agent/`, `tests/legal_sources/`, `tests/test_router.py`, `tests/test_cli.py` stay green throughout Phase 0. The callback addition to `Agent.run` is keyword-only with no-op defaults — no test signatures change.

## Multi-tenancy data model

### DDB table `kira-tenants`

```
PK: tenant_id  (string)
GSI: entra_tid-index  (PK: entra_tid)

Attributes:
  entra_tid               (Entra tenant ID — JWT.tid)
  firm_name               (display string)
  subscription_status     (active | suspended | cancelled)
  seats_allowed           (integer)
  case_packs_entitled     (list[string], empty in Phase 0)
  audit_retention_years   (integer, default 7 — BRAO Aufbewahrungsfrist)
  created_at, updated_at  (ISO 8601 strings)
```

**Seed row for MVP** (created by CDK as a CustomResource):
```json
{
  "tenant_id": "default",
  "entra_tid": "<stepfather's Entra tenant ID — supplied via CDK context>",
  "firm_name": "Kanzlei [name]",
  "subscription_status": "active",
  "seats_allowed": 5,
  "case_packs_entitled": [],
  "audit_retention_years": 7
}
```

### Tenant resolver logic

```python
def resolve_tenant(jwt_claims: dict) -> Tenant:
    entra_tid = jwt_claims["tid"]

    # Primary: DDB lookup by Entra tenant ID
    response = ddb.query(
        TableName=TENANTS_TABLE,
        IndexName="entra_tid-index",
        KeyConditionExpression="entra_tid = :tid",
        ExpressionAttributeValues={":tid": {"S": entra_tid}},
    )
    if response["Items"]:
        tenant = Tenant.from_ddb(response["Items"][0])
        if tenant.subscription_status != "active":
            raise UnauthorizedTenant(
                f"Subscription not active for tenant {entra_tid}"
            )
        return tenant

    # Fallback (Phase 0 only): hardcoded allowlist
    if entra_tid in ALLOWLIST_ENTRA_TIDS:
        return Tenant.default(entra_tid)

    raise UnauthorizedTenant(f"No subscription record for tenant {entra_tid}")
```

Phase 2 removes the allowlist fallback — DDB is authoritative.

## Auth flow

1. Lawyer is signed into Microsoft 365 with Entra ID (already required to use Outlook)
2. Lawyer invokes `@KIRA` — M365 Copilot mints an OBO (on-behalf-of) JWT for the KIRA app registration with scope `kira.invoke`
3. Copilot POSTs Activity to API Gateway with `Authorization: Bearer <JWT>`
4. API Gateway JWT authorizer:
   - Fetches Entra OIDC discovery: `https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration`
   - Validates `iss` (Microsoft Entra), `aud` (KIRA's app client ID), `exp`, `nbf`, signature against Entra JWKS
   - On success: forwards request with claims injected as `requestContext.authorizer.jwt.claims`
   - On failure: returns 401 directly (request never reaches AgentCore)
5. AgentCore Runtime container reads claims from request context, calls `resolve_tenant`

### Entra app registration (manual one-time setup)

Documented in `docs/operations/entra-app-registration.md` (created during implementation):

1. Register KIRA as a multi-tenant app in Entra (Azure portal or `az` CLI)
2. Expose API: scope `kira.invoke` (delegated permission, lawyer-consented at install time)
3. Add Web platform redirect URI for AppSource (Phase 3 placeholder)
4. Generate client ID + tenant ID — supplied to CDK as context parameters
5. Configure as known Microsoft 365 agent in M365 admin center (Phase 3 detail; for sideload, just include in manifest)

## Audit log schema

### DDB table `kira-audit`

```
PK: tenant_id          (string)
SK: timestamp_ms#session_id#sequence  (composite string for chronological + dedup)

Attributes:
  user_oid          (Entra object ID — globally unique per identity)
  user_upn          (Entra UPN — display only, e.g. lawyer@firm.de)
  session_id        (Bot Framework conversation ID)
  request_type      (message | continue | feedback)
  routing_tier      (haiku | sonnet | opus)
  task_type         (norm_lookup | rechtliche_wuerdigung | ...)
  model_id          (concrete Bedrock inference profile)
  tool_calls        (list of {tool_name, args_summary, latency_ms, status})
  total_latency_ms  (end-to-end agent loop wall clock)
  iteration_count   (number of Bedrock turns)
  response_hash     (sha256 hex of final response — tamper detection)
  status            (ok | error | unauthorized | timeout)
  error_code        (optional, on error)

TTL attribute: ttl_epoch_seconds
  = timestamp_ms / 1000 + (audit_retention_years × 365 × 86400)
```

### PII-stripping rules (per tool)

Each tool has a `args_summary` builder that yields **structured legal metadata only**, never free text:

| Tool | Args → Summary |
|---|---|
| `norm_lookup` | `{gesetz: "BGB", paragraph: "535"}` → `"BGB §535"` |
| `norm_search` | `{query: "...", k: 3, gesetz_filter: ["BGB"]}` → `"search:k=3:filter=BGB"` (drops query) |
| `berechne_frist` | `{typ: "verjaehrung_regulaer", ...}` → `"frist:verjaehrung_regulaer"` |
| `fetch_urteil` | `{url: "..."}` → `"urteil:" + url_hostname` (drops path) |
| `search_rechtsprechung` | `{query: "..."}` → `"rechtsprechung_suche"` (drops query) |

The full query text and full tool outputs **never enter the audit row**. Only structured metadata. This makes the audit table PII-free by construction, eliminating Art. 17 (right-to-erasure) burden — no client-data deletion required from this table.

## Streaming flow

Bot Framework Activity Protocol supports mid-turn outbound activities. The streamer translates `Agent.run`'s tool-call progress events into outbound activities:

```python
async def on_message_activity(turn_context: TurnContext):
    query = turn_context.activity.text

    # Send "thinking" activity immediately
    await turn_context.send_activity(typing_activity())

    streamer = OutlookStreamer(turn_context)
    routing = route(query)

    # Run with progress callback that maps tool calls to activities
    result = await asyncio.to_thread(
        Agent(client).run,
        query,
        routing=routing,
        on_tool_start=streamer.tool_started,
        on_tool_end=streamer.tool_ended,
    )

    audit.emit(...)
    await turn_context.send_activity(MessageFactory.text(result.final_text))
```

`Agent.run` gains two optional callback parameters (`on_tool_start`, `on_tool_end`) — these are NOT new behaviour, just hooks into the existing tool dispatch loop. Backwards-compatible default = no-op callbacks.

**Streamer's outbound activity examples**:
- "Suche relevante §§..." (when `search_norm` starts)
- "Prüfe § 536 BGB..." (when `lookup_norm` starts with gesetz=BGB, paragraph=536)
- "Berechne Verjährungsfrist..." (when `berechne_frist` starts)

Lawyer sees these as streaming chat bubbles in Outlook before the final answer arrives.

## Error handling

| Error | HTTP status | Lawyer sees | Audit row |
|---|---|---|---|
| Missing/invalid JWT | 401 | Generic Copilot auth error | none (didn't reach KIRA) |
| Unauthorized tenant (no subscription) | 403 | "Ihre Lizenz für KIRA ist nicht aktiv. Bitte kontaktieren Sie Ihren Administrator." | status=unauthorized |
| Bedrock throttled or 5xx | 200 (graceful) | "KI-Modell gerade überlastet. Bitte in ein paar Sekunden erneut versuchen." | status=error, error_code=bedrock_throttle |
| Legal-sources Lambda unavailable | 200 (graceful, surfaced from existing tool layer) | "Rechtsquelle nicht erreichbar..." (existing tool message) | status=ok (tool error captured in tool_calls) |
| Agent exceeded max iterations | 200 | Final assistant text returned + audit logged | status=ok, iteration_count = max |
| Unexpected exception | 200 (graceful) | "Es ist ein unerwarteter Fehler aufgetreten. Bitte erneut versuchen." | status=error, error_code=internal |

Per the established fail-loud principle (memory: feedback_legal_fail_loud), errors surface honestly to the lawyer rather than silently masked.

## Deployment

### CDK stack `KiraCopilotAgent`

Resources:
- `kira-tenants` DDB table (on-demand, with `entra_tid-index` GSI)
- `kira-audit` DDB table (on-demand, with TTL attribute `ttl_epoch_seconds`)
- API Gateway HTTP API
  - JWT authorizer using Entra OIDC
  - Single route: `POST /api/messages` → AgentCore Runtime integration
- AgentCore Runtime
  - Container from local ECR repo
  - Min 1 instance, max 10 (auto-scale on request count)
  - Environment variables:
    - `LEGAL_LOOKUP_FN=kira-legal-lookup-norm`
    - `LEGAL_SEARCH_FN=kira-legal-search`
    - `AUDIT_TABLE_NAME=kira-audit`
    - `TENANTS_TABLE_NAME=kira-tenants`
    - `ENTRA_APP_CLIENT_ID=<context value>`
    - `AWS_REGION=eu-central-1`
- IAM execution role with policies:
  - `lambda:InvokeFunction` on the two legal-sources Lambdas
  - `bedrock:InvokeModel` on Claude inference profiles in eu-central-1
  - `dynamodb:Query/PutItem/UpdateItem` on the two tables
  - CloudWatch Logs write
- CustomResource: seed `kira-tenants` with the "default" row using stepfather's Entra tenant ID supplied via CDK context

### M365 app manifest

Sideloaded into stepfather's M365 tenant during MVP:

- App ID matches the Entra app registration client ID
- `validDomains` includes the API Gateway hostname
- Bot endpoint = `https://{api-gateway-hostname}/api/messages`
- Scope: `personal` (lawyer chats with KIRA 1:1 in Copilot)
- AppSource certification flags omitted (Phase 3 work)

### Deployment command sequence

```bash
# One-time Entra setup (manual via az CLI, documented in operations doc):
#   az ad app create ... → client_id, tenant_id
# Provided as CDK context:
cdk deploy --context entraClientId=$CLIENT_ID --context stepfatherEntraTid=$TENANT_ID

# After successful deploy:
# 1. Package manifest with the deployed API Gateway URL
# 2. Sideload .zip via Microsoft 365 admin → Integrated apps → Upload
# 3. Test in Outlook Copilot pane
```

## Testing strategy

### Unit tests (offline, run in CI)

| File | Asserts |
|---|---|
| `tests/copilot/__init__.py` | Package marker (empty) |
| `tests/copilot/test_tenant_resolver.py` | DDB lookup happy path (active tenant); inactive subscription → UnauthorizedTenant; missing tenant + allowlist hit → default Tenant; missing tenant + no allowlist → UnauthorizedTenant |
| `tests/copilot/test_audit.py` | Per-tool args-summary stripping (no PII in summary), TTL calculation correctness, sequence numbering for multiple tool calls in one turn |
| `tests/copilot/test_streamer.py` | tool_started → typing activity; tool_ended → progress message; final answer → MessageFactory.text |
| `tests/copilot/test_agent_app.py` | Activity → query parsing; on_message_activity end-to-end with mocked Agent.run, asserts audit emit + streaming sequence; auth failures produce expected error activity |
| `tests/copilot/test_subscription.py` | Active/suspended/cancelled handling |
| `tests/copilot/test_config.py` | Environment variable parsing, defaults |

### Live tests (opt-in via `RUN_LIVE_TESTS=1`)

| File | Asserts |
|---|---|
| `tests/copilot/test_agent_app_live.py` | End-to-end: real DDB lookup → real Bedrock + legal-sources Lambda calls → real audit row written. "Ask BGB §535 → get cited answer + verify audit row appears in DDB with PII-free summary" |

### Manual smoke test (deployment validation)

1. `curl https://{api-gateway}/api/messages` without JWT → 401
2. `curl https://{api-gateway}/api/messages` with valid JWT but no body → 400 (BF Activity required)
3. Bot Framework Emulator connects to `https://{api-gateway}/api/messages` with JWT, types question, receives streaming response + final answer
4. Sideloaded manifest works in stepfather's Outlook Copilot

### Coverage gate

95% on new `src/kira/copilot/` code, matching the existing `tests/legal_sources/` and `tests/agent/` standard.

## Success criteria

1. **Stepfather can chat with KIRA in Outlook**: Opens Outlook → opens Copilot pane → `@KIRA Was sagt § 535 BGB?` → sees streaming progress → receives a cited answer within 30 seconds.
2. **Tools fire end-to-end**: `search_norm` + `lookup_norm` (and others as relevant) get invoked against the deployed Lambdas; results flow back into the model's reasoning; final answer cites real §§ from the corpus.
3. **Audit table is PII-free**: every invocation produces exactly one DDB row with `tool_calls[].args_summary` containing structured metadata only — query text, tool outputs, and response text never appear in the table.
4. **Unauthorised tenants are rejected**: a different Entra tenant's JWT receives 403, no audit row, no Bedrock spend.
5. **Multi-tenancy is shape-only**: code refers to `tenant_id` everywhere, DDB has a `tenant_id` partition, but exactly one row exists (the default tenant). Adding a second tenant in Phase 2 is a DDB row insert + Entra consent flow, not a code change.
6. **Unit tests + live tests green**: 95% coverage on new code; CDK stack deploys to eu-central-1 from a fresh checkout; the live test exercises the deployed endpoint.

## Open follow-ups (explicitly out of scope, listed for Phase 1+ planning)

- **Case packs** (Phase 1): expert-curated tenancy-law case templates with specialized system prompts + required §§ + output quality checklists. Stepfather + Philip co-design 8–12 packs.
- **Compliance package** (Phase 2, parallel): DPA template, Verschwiegenheitsverpflichtung, subprocessor registry, EU AI Act Annex III docs, mandate disclosure clauses, professional liability insurance.
- **AppSource publication** (Phase 3): Microsoft Partner Center account, app certification, billing via AppSource subscription, public listing.
- **Outlook web add-in** (separate workstream): TypeScript pane with mandate file integration, deeper workflow than chat.
- **EU AI Act compliance** (Phase 2): full Annex III package — risk management, logging Art. 12, transparency Art. 13/50, human oversight Art. 14 (already satisfied by KIRA's UX).
- **`mandate_ref` field** (Phase 2): once the add-in has a UI for it.
- **Right-to-erasure self-service** (Phase 2): API + UI for the lawyer to delete a session's audit row.

## References

- [M365 Custom Engine Agents](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/overview-custom-engine-agent)
- [M365 Agents SDK — Create and Deploy](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/create-deploy-agents-sdk)
- [Bedrock AgentCore Runtime — Frankfurt + 14 regions](https://aws.amazon.com/bedrock/agentcore/)
- [Bedrock AgentCore Pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)
- [BRAK Leitfaden KI-Einsatz (Dec 2024)](https://www.brak.de/fileadmin/service/publikationen/Handlungshinweise/BRAK_Leitfaden_mit_Hinweisen_zum_KI-Einsatz_Stand_12_2024.pdf)
- [§ 43e BRAO — dejure.org](https://dejure.org/gesetze/BRAO/43e.html)
- Previous spec: `docs/superpowers/specs/2026-05-12-wire-legal-sources-into-agent-loop-design.md`
- Memory: `project_pseudonymizer_removed.md`, `project_legal_sources_arch.md`, `feedback_legal_fail_loud.md`

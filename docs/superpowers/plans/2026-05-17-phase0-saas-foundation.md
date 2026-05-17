# Phase 0 — SaaS foundation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap KIRA's existing Python `Agent.run` in a Microsoft 365 Agents SDK custom engine agent, deploy to AWS Bedrock AgentCore Runtime in eu-central-1 behind an API Gateway with Entra ID JWT validation, and emit a PII-free audit log to DynamoDB — enabling a German tenancy lawyer to use KIRA inside Outlook Copilot.

**Architecture:** Thin adapter layer (`src/kira/copilot/`) wraps the existing stateless `Agent.run`. New optional callback hooks on `Agent.run` let the adapter translate tool-call lifecycle events into outbound Bot Framework streaming activities. CDK stack provisions API Gateway HTTP API (JWT authorizer pointing at Entra OIDC), AgentCore Runtime container, two DynamoDB tables (`kira-tenants`, `kira-audit`), and IAM with least-privilege access to Bedrock + the legal-sources Lambdas.

**Tech Stack:** Python 3.13, Microsoft 365 Agents SDK (botbuilder-core + botbuilder-schema + botbuilder-integration-aiohttp), aiohttp, boto3, AWS CDK Python, Bedrock AgentCore Runtime, DynamoDB on-demand, API Gateway HTTP API, Entra ID OIDC.

**Spec:** `docs/superpowers/specs/2026-05-17-phase0-saas-foundation-design.md`

**Branch:** `feat/phase0-saas-foundation` (already created).

**Coverage gate:** 95% on new `src/kira/copilot/` code, matching `tests/legal_sources/` and `tests/agent/` standard.

---

## Pre-flight: dependency verification

Microsoft 365 Agents SDK for Python is built on top of the Bot Framework SDK. The base packages are stable and on PyPI:

- `botbuilder-core` — TurnContext, MessageFactory, BotFrameworkAdapter
- `botbuilder-schema` — Activity types
- `botbuilder-integration-aiohttp` — aiohttp HTTP transport

If Microsoft has published a higher-level `microsoft-agents-*` Python package that wraps these, the implementer should prefer it. Otherwise the plan uses Bot Framework primitives directly — the API surface is the same.

Before Task 1, run:

```bash
pip index versions botbuilder-core
pip index versions microsoft-agents-builder 2>&1 || true
pip index versions microsoft-agents-hosting-core 2>&1 || true
```

If a `microsoft-agents-*` package exists with current versions, adjust imports in Tasks 6/7 accordingly. The semantic shape (Activity in → MessageFactory.text out + intermediate send_activity for streaming) is identical.

---

## File map

| Path | Action |
|---|---|
| `src/kira/agent/core.py` | **Modify** — add two optional kwargs `on_tool_start`, `on_tool_end` to `Agent.run`, invoke them at tool dispatch points |
| `tests/agent/test_core_callbacks.py` | **Create** — pins callback behaviour |
| `src/kira/copilot/__init__.py` | **Create** — package marker |
| `src/kira/copilot/config.py` | **Create** — environment-variable parsing + defaults |
| `src/kira/copilot/tenant.py` | **Create** — `Tenant` dataclass + UnauthorizedTenant exception |
| `src/kira/copilot/subscription.py` | **Create** — `assert_subscription_active(tenant)` |
| `src/kira/copilot/tenant_resolver.py` | **Create** — `resolve_tenant(jwt_claims, ddb_client)` |
| `src/kira/copilot/audit.py` | **Create** — `emit_audit_row`, per-tool args summary registry |
| `src/kira/copilot/streamer.py` | **Create** — `OutlookStreamer` translating tool-call events → activities |
| `src/kira/copilot/agent_app.py` | **Create** — Bot Framework adapter + `on_message_activity` handler |
| `src/kira/copilot/main.py` | **Create** — aiohttp web app entry point for AgentCore Runtime |
| `tests/copilot/__init__.py` | **Create** — package marker |
| `tests/copilot/test_config.py` | **Create** |
| `tests/copilot/test_tenant_resolver.py` | **Create** |
| `tests/copilot/test_subscription.py` | **Create** |
| `tests/copilot/test_audit.py` | **Create** |
| `tests/copilot/test_streamer.py` | **Create** |
| `tests/copilot/test_agent_app.py` | **Create** |
| `tests/copilot/test_agent_app_live.py` | **Create** — opt-in via `RUN_LIVE_TESTS=1` |
| `infra/copilot_agent/cdk.json` | **Create** |
| `infra/copilot_agent/app.py` | **Create** |
| `infra/copilot_agent/stack.py` | **Create** |
| `infra/copilot_agent/requirements.txt` | **Create** |
| `infra/copilot_agent/Dockerfile` | **Create** |
| `infra/copilot_agent/manifest/manifest.json` | **Create** — M365 sideload manifest template |
| `infra/copilot_agent/manifest/icon-color.png` | **Create** — placeholder icon |
| `infra/copilot_agent/manifest/icon-outline.png` | **Create** — placeholder icon |
| `docs/operations/entra-app-registration.md` | **Create** — one-time Entra setup runbook |
| `docs/operations/sideload-m365-app.md` | **Create** — manifest packaging + sideload steps |
| `pyproject.toml` | **Modify** — add `botbuilder-core`, `botbuilder-schema`, `botbuilder-integration-aiohttp` to optional `[copilot]` extra |

---

## Phase 1 — `Agent.run` callback hooks

### Task 1: Add optional `on_tool_start` / `on_tool_end` callbacks to `Agent.run`

**Files:**
- Modify: `src/kira/agent/core.py`
- Test: `tests/agent/test_core_callbacks.py`

The existing tool dispatch loop in `Agent.run` calls `tool.run(tool_input)` per `tool_use` block. We add two optional keyword-only callbacks invoked before and after each tool call. Default = no-op, so all existing tests stay green.

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_core_callbacks.py`:

```python
"""Tests for the new on_tool_start / on_tool_end callback hooks on Agent.run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from kira.agent import Agent
from kira.llm.models import ModelTier
from kira.router import route


@dataclass
class _Block:
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict | None = None


@dataclass
class _Resp:
    content: list[_Block]
    stop_reason: str


class _StubMessages:
    """Scripts: turn 1 = tool_use(lookup_norm), turn 2 = final text."""

    def __init__(self) -> None:
        self._turn = 0

    def create(self, **_: Any) -> _Resp:
        self._turn += 1
        if self._turn == 1:
            return _Resp(
                content=[_Block(
                    type="tool_use", id="tu_1", name="lookup_norm",
                    input={"gesetz": "BGB", "paragraph": "535"},
                )],
                stop_reason="tool_use",
            )
        return _Resp(
            content=[_Block(type="text", text="§ 535 BGB regelt den Mietvertrag.")],
            stop_reason="end_turn",
        )


class _StubClient:
    backend = "stub"

    def __init__(self) -> None:
        self.raw = type("R", (), {"messages": _StubMessages()})()

    def model_id(self, tier: ModelTier) -> str:
        return f"stub-{tier.value}"


def test_on_tool_start_called_with_name_and_input() -> None:
    captured: list[tuple[str, dict]] = []
    agent = Agent(client=_StubClient())
    # Patch the lookup_norm tool to return a fixed value so we don't hit Lambda
    from kira.agent.tools._registry import REGISTRY
    fake_tool = MagicMock()
    fake_tool.run.return_value = "BGB §535 — Inhalt und Hauptpflichten"
    REGISTRY["lookup_norm"] = type(REGISTRY["lookup_norm"])(
        name="lookup_norm",
        description=REGISTRY["lookup_norm"].description,
        input_schema=REGISTRY["lookup_norm"].input_schema,
        run=fake_tool.run,
    )

    agent.run(
        "frage",
        routing=route("§ 535 BGB"),
        on_tool_start=lambda name, args: captured.append((name, args)),
    )

    assert captured == [("lookup_norm", {"gesetz": "BGB", "paragraph": "535"})]


def test_on_tool_end_called_with_name_and_output_preview() -> None:
    captured: list[tuple[str, str]] = []
    agent = Agent(client=_StubClient())
    from kira.agent.tools._registry import REGISTRY
    fake_tool = MagicMock()
    fake_tool.run.return_value = "BGB §535 — long output text here"
    REGISTRY["lookup_norm"] = type(REGISTRY["lookup_norm"])(
        name="lookup_norm",
        description=REGISTRY["lookup_norm"].description,
        input_schema=REGISTRY["lookup_norm"].input_schema,
        run=fake_tool.run,
    )

    agent.run(
        "frage",
        routing=route("§ 535 BGB"),
        on_tool_end=lambda name, output: captured.append((name, output)),
    )

    assert len(captured) == 1
    name, output = captured[0]
    assert name == "lookup_norm"
    assert "BGB §535" in output


def test_callbacks_default_to_noop_and_dont_break_existing_flow() -> None:
    agent = Agent(client=_StubClient())
    from kira.agent.tools._registry import REGISTRY
    fake_tool = MagicMock()
    fake_tool.run.return_value = "ok"
    REGISTRY["lookup_norm"] = type(REGISTRY["lookup_norm"])(
        name="lookup_norm",
        description=REGISTRY["lookup_norm"].description,
        input_schema=REGISTRY["lookup_norm"].input_schema,
        run=fake_tool.run,
    )
    # No callbacks supplied — should not raise
    result = agent.run("frage", routing=route("§ 535 BGB"))
    assert result.final_text == "§ 535 BGB regelt den Mietvertrag."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/agent/test_core_callbacks.py -v`

Expected: FAIL — `Agent.run` rejects unknown keyword argument `on_tool_start`.

- [ ] **Step 3: Modify `Agent.run` to accept the callbacks**

In `src/kira/agent/core.py`, change the `run` method signature:

```python
def run(
    self,
    query: str,
    *,
    routing: RoutingDecision,
    on_tool_start: Callable[[str, dict[str, Any]], None] | None = None,
    on_tool_end: Callable[[str, str], None] | None = None,
) -> AgentResult:
```

Add the import at the top of the file:

```python
from collections.abc import Callable
```

In the tool dispatch loop (find the block that says `for block in response.content: if getattr(block, "type", None) != "tool_use": continue`), wrap the tool run with the callbacks:

```python
                tool_name = block.name
                tool_input = block.input or {}
                if on_tool_start is not None:
                    try:
                        on_tool_start(tool_name, tool_input)
                    except Exception:
                        log.exception("on_tool_start callback failed")
                tool = TOOLS.get(tool_name)
                if tool is None:
                    output = f"FEHLER: Unbekanntes Tool {tool_name!r}."
                    is_error = True
                else:
                    try:
                        output = tool.run(tool_input)
                        is_error = False
                    except Exception as exc:
                        log.exception("Tool %s failed", tool_name)
                        output = f"FEHLER bei {tool_name}: {exc}"
                        is_error = True

                if on_tool_end is not None:
                    try:
                        on_tool_end(tool_name, output)
                    except Exception:
                        log.exception("on_tool_end callback failed")
```

Callbacks swallow their own exceptions (logged) — a bad callback must not break the agent loop.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/agent/test_core_callbacks.py -v`

Expected: 3 passed.

- [ ] **Step 5: Run the existing agent tests to confirm nothing regressed**

Run: `.venv/bin/python -m pytest tests/agent/ -q -m 'not live and not perf'`

Expected: all green (previously 33 tests + 3 new = 36 passed in tests/agent/).

- [ ] **Step 6: Commit**

```bash
git add src/kira/agent/core.py tests/agent/test_core_callbacks.py
git commit -m "feat(agent): optional on_tool_start/on_tool_end callbacks on Agent.run"
```

---

## Phase 2 — `src/kira/copilot/` foundation modules

### Task 2: Package scaffold + config module

**Files:**
- Create: `src/kira/copilot/__init__.py`
- Create: `src/kira/copilot/config.py`
- Create: `tests/copilot/__init__.py`
- Create: `tests/copilot/test_config.py`

- [ ] **Step 1: Create package markers**

```bash
mkdir -p src/kira/copilot tests/copilot
touch src/kira/copilot/__init__.py tests/copilot/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/copilot/test_config.py`:

```python
"""Tests for kira.copilot.config."""

from __future__ import annotations

import pytest

from kira.copilot.config import Config


def test_defaults_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in [
        "AUDIT_TABLE_NAME", "TENANTS_TABLE_NAME", "ENTRA_APP_CLIENT_ID",
        "LEGAL_LOOKUP_FN", "LEGAL_SEARCH_FN",
        "ALLOWLIST_ENTRA_TIDS", "AWS_REGION",
    ]:
        monkeypatch.delenv(var, raising=False)

    cfg = Config.from_env()
    assert cfg.audit_table_name == "kira-audit"
    assert cfg.tenants_table_name == "kira-tenants"
    assert cfg.aws_region == "eu-central-1"
    assert cfg.allowlist_entra_tids == ()
    assert cfg.entra_app_client_id == ""


def test_env_vars_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_TABLE_NAME", "custom-audit")
    monkeypatch.setenv("TENANTS_TABLE_NAME", "custom-tenants")
    monkeypatch.setenv("ENTRA_APP_CLIENT_ID", "abc-123")
    monkeypatch.setenv("ALLOWLIST_ENTRA_TIDS", "tid1,tid2,tid3")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")

    cfg = Config.from_env()
    assert cfg.audit_table_name == "custom-audit"
    assert cfg.tenants_table_name == "custom-tenants"
    assert cfg.entra_app_client_id == "abc-123"
    assert cfg.allowlist_entra_tids == ("tid1", "tid2", "tid3")
    assert cfg.aws_region == "eu-west-1"


def test_allowlist_handles_whitespace_and_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWLIST_ENTRA_TIDS", " tid1 ,  tid2,,tid3 ")
    cfg = Config.from_env()
    assert cfg.allowlist_entra_tids == ("tid1", "tid2", "tid3")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/copilot/test_config.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'kira.copilot.config'`.

- [ ] **Step 4: Implement config module**

Create `src/kira/copilot/config.py`:

```python
"""Environment-variable-driven configuration for the Copilot adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Runtime configuration for the Copilot adapter.

    All values are sourced from environment variables. Defaults match
    the CDK-deployed names so local dev only needs to override what differs.
    """

    audit_table_name: str
    tenants_table_name: str
    entra_app_client_id: str
    legal_lookup_fn: str
    legal_search_fn: str
    aws_region: str
    allowlist_entra_tids: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Config":
        raw_allowlist = os.environ.get("ALLOWLIST_ENTRA_TIDS", "")
        tids = tuple(
            t.strip() for t in raw_allowlist.split(",") if t.strip()
        )
        return cls(
            audit_table_name=os.environ.get("AUDIT_TABLE_NAME", "kira-audit"),
            tenants_table_name=os.environ.get("TENANTS_TABLE_NAME", "kira-tenants"),
            entra_app_client_id=os.environ.get("ENTRA_APP_CLIENT_ID", ""),
            legal_lookup_fn=os.environ.get("LEGAL_LOOKUP_FN", "kira-legal-lookup-norm"),
            legal_search_fn=os.environ.get("LEGAL_SEARCH_FN", "kira-legal-search"),
            aws_region=os.environ.get("AWS_REGION", "eu-central-1"),
            allowlist_entra_tids=tids,
        )
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/copilot/test_config.py -v`

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/kira/copilot/__init__.py src/kira/copilot/config.py tests/copilot/__init__.py tests/copilot/test_config.py
git commit -m "feat(copilot): scaffold copilot module with Config.from_env"
```

### Task 3: `Tenant` model + `UnauthorizedTenant` exception + subscription check

**Files:**
- Create: `src/kira/copilot/tenant.py`
- Create: `src/kira/copilot/subscription.py`
- Create: `tests/copilot/test_subscription.py`

- [ ] **Step 1: Write the failing test**

Create `tests/copilot/test_subscription.py`:

```python
"""Tests for kira.copilot.subscription."""

from __future__ import annotations

import pytest

from kira.copilot.subscription import assert_subscription_active
from kira.copilot.tenant import Tenant, UnauthorizedTenant


def _tenant(status: str) -> Tenant:
    return Tenant(
        tenant_id="t1",
        entra_tid="entra-t1",
        firm_name="Kanzlei Test",
        subscription_status=status,
        seats_allowed=5,
        case_packs_entitled=(),
        audit_retention_years=7,
    )


def test_active_passes() -> None:
    # Should not raise
    assert_subscription_active(_tenant("active"))


@pytest.mark.parametrize("status", ["suspended", "cancelled", "trial_expired", ""])
def test_inactive_raises(status: str) -> None:
    with pytest.raises(UnauthorizedTenant) as exc:
        assert_subscription_active(_tenant(status))
    assert "subscription" in str(exc.value).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/copilot/test_subscription.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'kira.copilot.tenant'`.

- [ ] **Step 3: Implement tenant + subscription modules**

Create `src/kira/copilot/tenant.py`:

```python
"""Tenant data model + unauthorized-tenant exception."""

from __future__ import annotations

from dataclasses import dataclass


class UnauthorizedTenant(Exception):
    """Raised when the JWT's Entra tenant has no active KIRA subscription."""


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    entra_tid: str
    firm_name: str
    subscription_status: str  # active | suspended | cancelled | trial_expired
    seats_allowed: int
    case_packs_entitled: tuple[str, ...]
    audit_retention_years: int

    @classmethod
    def default_for(cls, entra_tid: str) -> "Tenant":
        """Constructs a tenant record for an allowlist-only Entra tenant.

        Used in Phase 0 when no DDB row exists yet for the Entra tenant ID
        but it is on the static allowlist.
        """
        return cls(
            tenant_id="default",
            entra_tid=entra_tid,
            firm_name="(default)",
            subscription_status="active",
            seats_allowed=5,
            case_packs_entitled=(),
            audit_retention_years=7,
        )
```

Create `src/kira/copilot/subscription.py`:

```python
"""Subscription-status guard for the Copilot adapter."""

from __future__ import annotations

from kira.copilot.tenant import Tenant, UnauthorizedTenant


def assert_subscription_active(tenant: Tenant) -> None:
    """Raises UnauthorizedTenant when the tenant's subscription isn't active."""
    if tenant.subscription_status != "active":
        raise UnauthorizedTenant(
            f"Tenant {tenant.tenant_id!r} subscription status is "
            f"{tenant.subscription_status!r}, expected 'active'."
        )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/copilot/test_subscription.py -v`

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/copilot/tenant.py src/kira/copilot/subscription.py tests/copilot/test_subscription.py
git commit -m "feat(copilot): Tenant dataclass + subscription guard"
```

### Task 4: Tenant resolver with DDB lookup + allowlist fallback

**Files:**
- Create: `src/kira/copilot/tenant_resolver.py`
- Create: `tests/copilot/test_tenant_resolver.py`

- [ ] **Step 1: Write the failing test**

Create `tests/copilot/test_tenant_resolver.py`:

```python
"""Tests for kira.copilot.tenant_resolver."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kira.copilot.tenant import UnauthorizedTenant
from kira.copilot.tenant_resolver import resolve_tenant


def _mock_ddb_with_tenant(tenant_id: str, entra_tid: str, status: str) -> MagicMock:
    """Fake DDB client whose .query returns a single matching item."""
    client = MagicMock()
    client.query.return_value = {
        "Items": [{
            "tenant_id": {"S": tenant_id},
            "entra_tid": {"S": entra_tid},
            "firm_name": {"S": "Kanzlei Test"},
            "subscription_status": {"S": status},
            "seats_allowed": {"N": "5"},
            "case_packs_entitled": {"L": []},
            "audit_retention_years": {"N": "7"},
        }]
    }
    return client


def _mock_ddb_empty() -> MagicMock:
    client = MagicMock()
    client.query.return_value = {"Items": []}
    return client


def test_active_tenant_from_ddb_resolves() -> None:
    ddb = _mock_ddb_with_tenant("kanzlei_a", "entra-abc-123", "active")
    tenant = resolve_tenant(
        jwt_claims={"tid": "entra-abc-123"},
        ddb_client=ddb,
        tenants_table_name="kira-tenants",
        allowlist=(),
    )
    assert tenant.tenant_id == "kanzlei_a"
    assert tenant.entra_tid == "entra-abc-123"
    assert tenant.subscription_status == "active"


def test_suspended_tenant_from_ddb_raises() -> None:
    ddb = _mock_ddb_with_tenant("kanzlei_a", "entra-abc-123", "suspended")
    with pytest.raises(UnauthorizedTenant):
        resolve_tenant(
            jwt_claims={"tid": "entra-abc-123"},
            ddb_client=ddb,
            tenants_table_name="kira-tenants",
            allowlist=(),
        )


def test_missing_ddb_but_in_allowlist_returns_default() -> None:
    ddb = _mock_ddb_empty()
    tenant = resolve_tenant(
        jwt_claims={"tid": "entra-allowlisted"},
        ddb_client=ddb,
        tenants_table_name="kira-tenants",
        allowlist=("entra-allowlisted",),
    )
    assert tenant.tenant_id == "default"
    assert tenant.entra_tid == "entra-allowlisted"
    assert tenant.subscription_status == "active"


def test_missing_ddb_not_in_allowlist_raises() -> None:
    ddb = _mock_ddb_empty()
    with pytest.raises(UnauthorizedTenant):
        resolve_tenant(
            jwt_claims={"tid": "entra-unknown"},
            ddb_client=ddb,
            tenants_table_name="kira-tenants",
            allowlist=("entra-allowlisted",),
        )


def test_missing_tid_in_claims_raises() -> None:
    ddb = _mock_ddb_empty()
    with pytest.raises(UnauthorizedTenant):
        resolve_tenant(
            jwt_claims={},  # no tid
            ddb_client=ddb,
            tenants_table_name="kira-tenants",
            allowlist=("any",),
        )


def test_ddb_query_uses_gsi() -> None:
    ddb = _mock_ddb_with_tenant("kanzlei_a", "entra-abc-123", "active")
    resolve_tenant(
        jwt_claims={"tid": "entra-abc-123"},
        ddb_client=ddb,
        tenants_table_name="kira-tenants",
        allowlist=(),
    )
    kwargs = ddb.query.call_args.kwargs
    assert kwargs["TableName"] == "kira-tenants"
    assert kwargs["IndexName"] == "entra_tid-index"
    assert kwargs["ExpressionAttributeValues"] == {":tid": {"S": "entra-abc-123"}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/copilot/test_tenant_resolver.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'kira.copilot.tenant_resolver'`.

- [ ] **Step 3: Implement resolver**

Create `src/kira/copilot/tenant_resolver.py`:

```python
"""Resolves a JWT's Entra tenant ID into a KIRA Tenant record.

Primary lookup: DynamoDB `kira-tenants` table via GSI on `entra_tid`.
Fallback (Phase 0): static allowlist returning a default Tenant when no
DDB row exists. Phase 2 removes the allowlist.
"""

from __future__ import annotations

from typing import Any

from kira.copilot.subscription import assert_subscription_active
from kira.copilot.tenant import Tenant, UnauthorizedTenant


def resolve_tenant(
    *,
    jwt_claims: dict[str, Any],
    ddb_client: Any,
    tenants_table_name: str,
    allowlist: tuple[str, ...],
) -> Tenant:
    entra_tid = jwt_claims.get("tid")
    if not entra_tid or not isinstance(entra_tid, str):
        raise UnauthorizedTenant("JWT is missing the 'tid' claim.")

    response = ddb_client.query(
        TableName=tenants_table_name,
        IndexName="entra_tid-index",
        KeyConditionExpression="entra_tid = :tid",
        ExpressionAttributeValues={":tid": {"S": entra_tid}},
    )
    items = response.get("Items", [])

    if items:
        tenant = _tenant_from_ddb_item(items[0])
        assert_subscription_active(tenant)
        return tenant

    if entra_tid in allowlist:
        return Tenant.default_for(entra_tid)

    raise UnauthorizedTenant(
        f"No KIRA subscription found for Entra tenant {entra_tid!r}."
    )


def _tenant_from_ddb_item(item: dict[str, Any]) -> Tenant:
    case_packs = item.get("case_packs_entitled", {}).get("L", [])
    return Tenant(
        tenant_id=item["tenant_id"]["S"],
        entra_tid=item["entra_tid"]["S"],
        firm_name=item.get("firm_name", {}).get("S", ""),
        subscription_status=item["subscription_status"]["S"],
        seats_allowed=int(item.get("seats_allowed", {}).get("N", "0")),
        case_packs_entitled=tuple(p["S"] for p in case_packs),
        audit_retention_years=int(item.get("audit_retention_years", {}).get("N", "7")),
    )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/copilot/test_tenant_resolver.py -v`

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/copilot/tenant_resolver.py tests/copilot/test_tenant_resolver.py
git commit -m "feat(copilot): tenant resolver with DDB lookup + allowlist fallback"
```

### Task 5: Audit emitter with per-tool PII-free args summary

**Files:**
- Create: `src/kira/copilot/audit.py`
- Create: `tests/copilot/test_audit.py`

- [ ] **Step 1: Write the failing test**

Create `tests/copilot/test_audit.py`:

```python
"""Tests for kira.copilot.audit."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kira.copilot.audit import AuditEmitter, summarize_tool_args
from kira.copilot.tenant import Tenant


def _tenant() -> Tenant:
    return Tenant(
        tenant_id="t1", entra_tid="entra-t1", firm_name="Kanzlei",
        subscription_status="active", seats_allowed=5,
        case_packs_entitled=(), audit_retention_years=7,
    )


@pytest.mark.parametrize("tool_name,args,expected", [
    ("lookup_norm", {"gesetz": "BGB", "paragraph": "535"}, "BGB §535"),
    ("lookup_norm", {"gesetz": "WoEigG", "paragraph": "14"}, "WoEigG §14"),
    ("norm_search", {"query": "Mietminderung wegen Schimmel", "k": 5}, "search:k=5"),
    ("norm_search", {"query": "x", "k": 3, "gesetz_filter": ["BGB", "WoEigG"]},
     "search:k=3:filter=BGB,WoEigG"),
    ("berechne_frist", {"typ": "verjaehrung_regulaer", "ab": "2026-01-01"},
     "frist:verjaehrung_regulaer"),
    ("fetch_urteil", {"url": "https://www.bundesgerichtshof.de/uri/123"},
     "urteil:bundesgerichtshof.de"),
    ("search_rechtsprechung", {"query": "Schimmel BGH"}, "rechtsprechung_suche"),
    ("unknown_tool", {"anything": "x"}, "unknown_tool"),  # safe default
])
def test_summarize_tool_args_strips_pii(
    tool_name: str, args: dict, expected: str
) -> None:
    assert summarize_tool_args(tool_name, args) == expected


def test_emit_writes_ddb_item_with_pii_free_summary() -> None:
    ddb = MagicMock()
    emitter = AuditEmitter(
        ddb_client=ddb, audit_table_name="kira-audit",
    )

    emitter.emit(
        tenant=_tenant(),
        user_oid="oid-abc",
        user_upn="lawyer@firm.de",
        session_id="sess-1",
        routing_tier="opus",
        task_type="rechtliche_wuerdigung",
        model_id="eu.anthropic.claude-opus-4-7",
        tool_calls=[
            {"tool": "lookup_norm",
             "input": {"gesetz": "BGB", "paragraph": "535"},
             "output_preview": "...", "is_error": False, "latency_ms": 142},
            {"tool": "norm_search",
             "input": {"query": "Klaus Müller Schimmel", "k": 3},
             "output_preview": "...", "is_error": False, "latency_ms": 480},
        ],
        total_latency_ms=2340,
        iteration_count=3,
        response_text="§ 535 BGB regelt den Mietvertrag.",
        status="ok",
        error_code=None,
    )

    ddb.put_item.assert_called_once()
    kwargs = ddb.put_item.call_args.kwargs
    item = kwargs["Item"]
    assert kwargs["TableName"] == "kira-audit"
    assert item["tenant_id"]["S"] == "t1"
    assert item["user_oid"]["S"] == "oid-abc"
    assert item["status"]["S"] == "ok"
    # PII-free: tool_calls summaries should not contain the query text
    tool_calls_str = str(item["tool_calls"])
    assert "Klaus" not in tool_calls_str
    assert "Müller" not in tool_calls_str
    assert "Schimmel" not in tool_calls_str
    assert "search:k=3" in tool_calls_str
    assert "BGB §535" in tool_calls_str
    # Response text never appears
    assert "Mietvertrag" not in tool_calls_str
    assert "response_hash" in item
    # TTL is set
    assert "ttl_epoch_seconds" in item


def test_emit_sets_ttl_based_on_retention_years() -> None:
    ddb = MagicMock()
    emitter = AuditEmitter(
        ddb_client=ddb, audit_table_name="kira-audit",
    )
    import time
    before = int(time.time()) + 7 * 365 * 86400 - 10  # tolerance

    emitter.emit(
        tenant=_tenant(),
        user_oid="oid", user_upn="x@y.de", session_id="s",
        routing_tier="haiku", task_type="norm_lookup",
        model_id="m", tool_calls=[], total_latency_ms=10,
        iteration_count=1, response_text="ok", status="ok", error_code=None,
    )

    item = ddb.put_item.call_args.kwargs["Item"]
    ttl = int(item["ttl_epoch_seconds"]["N"])
    after = int(time.time()) + 7 * 365 * 86400 + 10
    assert before <= ttl <= after
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/copilot/test_audit.py -v`

Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement audit emitter**

Create `src/kira/copilot/audit.py`:

```python
"""DynamoDB audit emitter for KIRA Copilot invocations.

Critical: this table is PII-free by construction. Per-tool `args_summary`
builders strip free-text fields. Tool outputs never enter the table.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from kira.copilot.tenant import Tenant


def summarize_tool_args(tool_name: str, args: dict[str, Any]) -> str:
    """Per-tool args summarizer. Strips all free-text fields by hand."""
    if tool_name == "lookup_norm":
        gesetz = args.get("gesetz", "?")
        paragraph = args.get("paragraph", "?")
        return f"{gesetz} §{paragraph}"
    if tool_name == "norm_search":
        k = args.get("k", "?")
        parts = [f"search:k={k}"]
        gf = args.get("gesetz_filter")
        if gf:
            parts.append(f"filter={','.join(gf)}")
        return ":".join(parts)
    if tool_name == "berechne_frist":
        typ = args.get("typ", "?")
        return f"frist:{typ}"
    if tool_name == "fetch_urteil":
        url = args.get("url", "")
        host = urlparse(url).hostname or "unknown"
        return f"urteil:{host}"
    if tool_name == "search_rechtsprechung":
        return "rechtsprechung_suche"
    return tool_name


@dataclass
class AuditEmitter:
    ddb_client: Any
    audit_table_name: str

    def emit(
        self,
        *,
        tenant: Tenant,
        user_oid: str,
        user_upn: str,
        session_id: str,
        routing_tier: str,
        task_type: str,
        model_id: str,
        tool_calls: list[dict[str, Any]],
        total_latency_ms: int,
        iteration_count: int,
        response_text: str,
        status: str,
        error_code: str | None,
    ) -> None:
        timestamp_ms = int(time.time() * 1000)
        sequence = uuid.uuid4().hex[:8]
        sk = f"{timestamp_ms}#{session_id}#{sequence}"

        ttl = (
            timestamp_ms // 1000 + tenant.audit_retention_years * 365 * 86400
        )

        response_hash = hashlib.sha256(response_text.encode("utf-8")).hexdigest()

        tool_calls_ddb = [
            {
                "M": {
                    "tool_name": {"S": tc["tool"]},
                    "args_summary": {"S": summarize_tool_args(tc["tool"], tc.get("input", {}))},
                    "latency_ms": {"N": str(tc.get("latency_ms", 0))},
                    "status": {"S": "error" if tc.get("is_error") else "ok"},
                }
            }
            for tc in tool_calls
        ]

        item: dict[str, Any] = {
            "tenant_id": {"S": tenant.tenant_id},
            "sk": {"S": sk},
            "user_oid": {"S": user_oid},
            "user_upn": {"S": user_upn},
            "session_id": {"S": session_id},
            "routing_tier": {"S": routing_tier},
            "task_type": {"S": task_type},
            "model_id": {"S": model_id},
            "tool_calls": {"L": tool_calls_ddb},
            "total_latency_ms": {"N": str(total_latency_ms)},
            "iteration_count": {"N": str(iteration_count)},
            "response_hash": {"S": response_hash},
            "status": {"S": status},
            "ttl_epoch_seconds": {"N": str(ttl)},
        }
        if error_code:
            item["error_code"] = {"S": error_code}

        self.ddb_client.put_item(
            TableName=self.audit_table_name,
            Item=item,
        )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/copilot/test_audit.py -v`

Expected: 10 passed (8 parametrize + 2 emit tests).

- [ ] **Step 5: Commit**

```bash
git add src/kira/copilot/audit.py tests/copilot/test_audit.py
git commit -m "feat(copilot): PII-free audit emitter with per-tool args summary"
```

### Task 6: Streamer — translates tool-call events to Bot Framework activities

**Files:**
- Create: `src/kira/copilot/streamer.py`
- Create: `tests/copilot/test_streamer.py`
- Modify: `pyproject.toml` — add botbuilder deps to a `[project.optional-dependencies]` `copilot` extra

The streamer holds a reference to a Bot Framework `TurnContext` and translates `on_tool_start` / `on_tool_end` events into outbound activities. Each tool gets a German progress message.

- [ ] **Step 1: Add Bot Framework deps to pyproject.toml**

In `pyproject.toml`, add (or extend) the `[project.optional-dependencies]` section:

```toml
[project.optional-dependencies]
dev = [
    # ... existing dev deps stay ...
]
copilot = [
    "botbuilder-core>=4.16",
    "botbuilder-schema>=4.16",
    "botbuilder-integration-aiohttp>=4.16",
    "aiohttp>=3.9",
]
```

If a `microsoft-agents-*` package is available, prefer it — verify via `pip index versions microsoft-agents-builder` and replace the three `botbuilder-*` deps with the equivalent `microsoft-agents-*` package(s). The public API is identical (TurnContext, MessageFactory, Activity).

Install: `.venv/bin/pip install -e ".[copilot]"`

- [ ] **Step 2: Write the failing test**

Create `tests/copilot/test_streamer.py`:

```python
"""Tests for kira.copilot.streamer.

The streamer translates Agent.run tool-call lifecycle events into outbound
Bot Framework activities. We mock TurnContext.send_activity and assert the
sequence + content.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kira.copilot.streamer import OutlookStreamer


@pytest.fixture
def turn_context() -> MagicMock:
    ctx = MagicMock()
    ctx.send_activity = AsyncMock()
    return ctx


def test_tool_started_lookup_norm_sends_progress(turn_context: MagicMock) -> None:
    streamer = OutlookStreamer(turn_context)
    streamer.tool_started("lookup_norm", {"gesetz": "BGB", "paragraph": "535"})
    streamer.flush_sync()
    assert turn_context.send_activity.await_count == 1
    sent = turn_context.send_activity.await_args.args[0]
    assert "BGB" in sent.text
    assert "535" in sent.text


def test_tool_started_norm_search_sends_progress(turn_context: MagicMock) -> None:
    streamer = OutlookStreamer(turn_context)
    streamer.tool_started("norm_search", {"query": "Mietminderung Schimmel", "k": 5})
    streamer.flush_sync()
    sent = turn_context.send_activity.await_args.args[0]
    # Query string MUST NOT appear in the streaming text — that's PII channel
    assert "Mietminderung" not in sent.text
    assert "Schimmel" not in sent.text
    # German progress message
    assert "Suche" in sent.text or "Recherche" in sent.text


def test_tool_started_unknown_tool_falls_back(turn_context: MagicMock) -> None:
    streamer = OutlookStreamer(turn_context)
    streamer.tool_started("some_new_tool", {"foo": "bar"})
    streamer.flush_sync()
    sent = turn_context.send_activity.await_args.args[0]
    assert "some_new_tool" in sent.text


def test_records_tool_call_for_audit(turn_context: MagicMock) -> None:
    streamer = OutlookStreamer(turn_context)
    streamer.tool_started("lookup_norm", {"gesetz": "BGB", "paragraph": "535"})
    streamer.tool_ended("lookup_norm", "BGB §535 — Inhalt und Hauptpflichten...")

    assert len(streamer.tool_calls) == 1
    tc = streamer.tool_calls[0]
    assert tc["tool"] == "lookup_norm"
    assert tc["input"] == {"gesetz": "BGB", "paragraph": "535"}
    assert "BGB §535" in tc["output_preview"]
    assert tc["is_error"] is False
    assert "latency_ms" in tc


def test_multiple_tools_recorded_in_order(turn_context: MagicMock) -> None:
    streamer = OutlookStreamer(turn_context)
    streamer.tool_started("norm_search", {"query": "x", "k": 3})
    streamer.tool_ended("norm_search", "hits...")
    streamer.tool_started("lookup_norm", {"gesetz": "BGB", "paragraph": "535"})
    streamer.tool_ended("lookup_norm", "wortlaut...")

    assert [tc["tool"] for tc in streamer.tool_calls] == ["norm_search", "lookup_norm"]


def test_tool_ended_with_FEHLER_marks_error(turn_context: MagicMock) -> None:
    streamer = OutlookStreamer(turn_context)
    streamer.tool_started("lookup_norm", {"gesetz": "X", "paragraph": "1"})
    streamer.tool_ended("lookup_norm", "FEHLER bei lookup_norm: not found")
    assert streamer.tool_calls[0]["is_error"] is True


@pytest.mark.asyncio
async def test_wait_pending_delivers_queued_activities() -> None:
    """Production path: tool_started inside a running loop creates a task;
    wait_pending awaits it so the activity actually delivers."""
    import asyncio

    ctx = MagicMock()
    ctx.send_activity = AsyncMock()

    streamer = OutlookStreamer(ctx)
    streamer.tool_started("lookup_norm", {"gesetz": "BGB", "paragraph": "535"})
    streamer.tool_started("lookup_norm", {"gesetz": "BGB", "paragraph": "536"})

    # Activities not yet delivered (tasks queued but not driven)
    await streamer.wait_pending()
    # Now they should all have been awaited
    assert ctx.send_activity.await_count == 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/copilot/test_streamer.py -v`

Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement the streamer**

Create `src/kira/copilot/streamer.py`:

```python
"""Bridges Agent.run tool-call lifecycle events to Bot Framework activities.

The streamer is constructed per-turn with a TurnContext. `tool_started`
fires an asynchronous outbound activity ("Suche relevante §§…") so the
lawyer sees streaming progress in Outlook. `tool_ended` records timing
and output preview for the audit emitter.

The streamer also collects the tool-call audit list, ready for AuditEmitter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from botbuilder.core import MessageFactory, TurnContext

log = logging.getLogger(__name__)


class OutlookStreamer:
    """Per-turn streamer attached to a Bot Framework TurnContext."""

    def __init__(self, turn_context: TurnContext) -> None:
        self._tc = turn_context
        self._tool_starts: dict[str, float] = {}
        self.tool_calls: list[dict[str, Any]] = []
        self._pending: list[asyncio.Task] = []

    # --- Callback hooks for Agent.run ---

    def tool_started(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        """Synchronous hook — Agent.run is synchronous. Fires async activity."""
        self._tool_starts[tool_name + "#" + str(len(self.tool_calls))] = time.monotonic()
        progress = _german_progress(tool_name, tool_input)
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(
                self._tc.send_activity(MessageFactory.text(progress))
            )
            self._pending.append(task)
        except RuntimeError:
            # No running loop (test path); the test should call flush_sync.
            log.debug("No running loop — deferring activity send")

    def tool_ended(self, tool_name: str, output: str) -> None:
        """Synchronous hook — records timing and output preview for audit."""
        # Find the most recent start key for this tool
        keys = [k for k in self._tool_starts if k.startswith(tool_name + "#")]
        if keys:
            key = keys[-1]
            latency_ms = int((time.monotonic() - self._tool_starts.pop(key)) * 1000)
        else:
            latency_ms = 0

        is_error = output.startswith("FEHLER")
        self.tool_calls.append({
            "tool": tool_name,
            "input": self._last_input_for(tool_name),
            "output_preview": output[:300],
            "is_error": is_error,
            "latency_ms": latency_ms,
        })

    # --- Helpers ---

    def _last_input_for(self, tool_name: str) -> dict[str, Any]:
        """Looks up the input recorded by tool_started for audit logging.

        Because Agent.run only emits (name, input) on start and (name, output)
        on end, we need to stash the input alongside the timing key.
        """
        # Walk pending starts in reverse — most recent first
        for entry in reversed(self.tool_calls + self._input_log):
            if entry.get("tool") == tool_name and "input" in entry:
                return entry["input"]
        return {}

    def flush_sync(self) -> None:
        """Test helper: drives the test event loop to deliver pending activities.

        In production the AIOHTTP handler's event loop drives the tasks; in
        unit tests we run a quick asyncio.run to flush.
        """
        if not self._pending:
            return
        async def _await_all() -> None:
            for task in self._pending:
                try:
                    await task
                except Exception:
                    log.exception("send_activity failed")
        try:
            asyncio.run(_await_all())
        except RuntimeError:
            # Already inside an event loop — skip flush (production path)
            pass


def _german_progress(tool_name: str, tool_input: dict[str, Any]) -> str:
    """German progress message per tool, NO PII from inputs."""
    if tool_name == "lookup_norm":
        gesetz = tool_input.get("gesetz", "?")
        paragraph = tool_input.get("paragraph", "?")
        return f"Schlage {gesetz} §{paragraph} nach…"
    if tool_name == "norm_search":
        return "Suche relevante Paragraphen im Bundesrecht…"
    if tool_name == "berechne_frist":
        return "Berechne Frist…"
    if tool_name == "fetch_urteil":
        return "Lade Urteil…"
    if tool_name == "search_rechtsprechung":
        return "Suche relevante Rechtsprechung…"
    return f"Rufe Werkzeug {tool_name!r} auf…"
```

Note: the streamer's `_last_input_for` lookup is awkward — let me simplify. Replace the OutlookStreamer with this cleaner version (the test will catch this):

Wait — the implementer should notice the issue when the test fails. Let me re-read the test:

`test_records_tool_call_for_audit` — `tool_started` is called with `("lookup_norm", {"gesetz": "BGB", "paragraph": "535"})`. Then `tool_ended` is called with `("lookup_norm", "BGB §535 — ...")`. The test asserts `tc["input"] == {"gesetz": "BGB", "paragraph": "535"}`.

The cleaner shape: stash the input on `tool_started` in a parallel list.

Rewrite `OutlookStreamer` with this simpler approach:

```python
class OutlookStreamer:
    def __init__(self, turn_context: TurnContext) -> None:
        self._tc = turn_context
        # Stack of pending inputs per tool name (handles nested-ish cases)
        self._pending_inputs: list[tuple[str, dict[str, Any], float]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self._pending_tasks: list[asyncio.Task] = []

    def tool_started(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        self._pending_inputs.append((tool_name, tool_input, time.monotonic()))
        progress = _german_progress(tool_name, tool_input)
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(
                self._tc.send_activity(MessageFactory.text(progress))
            )
            self._pending_tasks.append(task)
        except RuntimeError:
            log.debug("No running loop — deferring activity send")

    def tool_ended(self, tool_name: str, output: str) -> None:
        # Find last matching start
        for i in range(len(self._pending_inputs) - 1, -1, -1):
            name, inp, t0 = self._pending_inputs[i]
            if name == tool_name:
                self._pending_inputs.pop(i)
                latency_ms = int((time.monotonic() - t0) * 1000)
                self.tool_calls.append({
                    "tool": tool_name,
                    "input": inp,
                    "output_preview": output[:300],
                    "is_error": output.startswith("FEHLER"),
                    "latency_ms": latency_ms,
                })
                return
        # No matching start — defensive
        self.tool_calls.append({
            "tool": tool_name,
            "input": {},
            "output_preview": output[:300],
            "is_error": output.startswith("FEHLER"),
            "latency_ms": 0,
        })

    async def wait_pending(self) -> None:
        """Awaits all queued send_activity tasks. Call this from the
        async turn handler AFTER the synchronous Agent.run completes,
        so streaming bubbles deliver before the final answer."""
        if not self._pending_tasks:
            return
        for task in list(self._pending_tasks):
            try:
                await task
            except Exception:
                log.exception("send_activity failed")
        self._pending_tasks.clear()

    def flush_sync(self) -> None:
        """Test-only synchronous flush. Production code uses wait_pending().

        Used by unit tests that don't run inside an event loop — drives
        a fresh asyncio.run() to deliver pending activities."""
        if not self._pending_tasks:
            return
        async def _await_all() -> None:
            for task in self._pending_tasks:
                try:
                    await task
                except Exception:
                    log.exception("send_activity failed")
        try:
            asyncio.run(_await_all())
        except RuntimeError:
            # Already inside an event loop — caller should use wait_pending() instead
            pass
```

Replace the full module contents with the cleaner version above (the awkward `_last_input_for` was a planning mistake — fix it before committing).

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/copilot/test_streamer.py -v`

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add src/kira/copilot/streamer.py tests/copilot/test_streamer.py pyproject.toml
git commit -m "feat(copilot): streamer translates tool-call events to Outlook activities"
```

---

## Phase 3 — Wire-up: agent_app + main

### Task 7: `agent_app.py` — on_message_activity handler

**Files:**
- Create: `src/kira/copilot/agent_app.py`
- Create: `tests/copilot/test_agent_app.py`

This is the heart of the adapter — pulls together tenant resolution, subscription check, agent execution, streaming, and audit emission.

- [ ] **Step 1: Write the failing test**

Create `tests/copilot/test_agent_app.py`:

```python
"""Tests for kira.copilot.agent_app — the Activity Protocol handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botbuilder.schema import Activity, ActivityTypes, ChannelAccount

from kira.agent.core import AgentResult
from kira.copilot.agent_app import KiraCopilotBot
from kira.copilot.config import Config
from kira.copilot.tenant import Tenant


def _config() -> Config:
    return Config(
        audit_table_name="kira-audit",
        tenants_table_name="kira-tenants",
        entra_app_client_id="cid",
        legal_lookup_fn="lookup",
        legal_search_fn="search",
        aws_region="eu-central-1",
        allowlist_entra_tids=("entra-allow",),
    )


def _tenant() -> Tenant:
    return Tenant(
        tenant_id="t1", entra_tid="entra-allow", firm_name="K",
        subscription_status="active", seats_allowed=5,
        case_packs_entitled=(), audit_retention_years=7,
    )


def _activity_with_claims(text: str, claims: dict) -> Activity:
    act = Activity(
        type=ActivityTypes.message,
        text=text,
        from_property=ChannelAccount(id="user-oid", aad_object_id="oid-1", name="lawyer@firm.de"),
        conversation=MagicMock(id="conv-1"),
        channel_data={"jwt_claims": claims},
    )
    return act


@pytest.mark.asyncio
async def test_happy_path_calls_agent_emits_audit_replies() -> None:
    cfg = _config()
    ddb = MagicMock()
    bot = KiraCopilotBot(config=cfg, ddb_client=ddb, llm_client=MagicMock())

    turn_context = MagicMock()
    turn_context.send_activity = AsyncMock()
    turn_context.activity = _activity_with_claims(
        "Was sagt § 535 BGB?",
        {"tid": "entra-allow", "oid": "oid-1", "upn": "lawyer@firm.de"},
    )

    fake_result = AgentResult(
        final_text="§ 535 BGB regelt den Mietvertrag.",
        routing=MagicMock(tier=MagicMock(value="haiku"), task_type=MagicMock(value="norm_lookup")),
        tool_calls=[
            {"tool": "lookup_norm",
             "input": {"gesetz": "BGB", "paragraph": "535"},
             "output_preview": "...",
             "is_error": False,
             "latency_ms": 100},
        ],
        stop_reason="end_turn",
    )

    with patch("kira.copilot.agent_app.Agent") as MockAgent, \
         patch("kira.copilot.agent_app.route") as mock_route:
        MockAgent.return_value.run.return_value = fake_result
        mock_route.return_value = fake_result.routing

        await bot.on_message_activity(turn_context)

    # Final answer sent
    final_call = turn_context.send_activity.await_args_list[-1]
    assert "§ 535 BGB" in final_call.args[0].text

    # Audit emitted
    ddb.put_item.assert_called_once()
    audit_item = ddb.put_item.call_args.kwargs["Item"]
    assert audit_item["tenant_id"]["S"] == "t1"
    assert audit_item["status"]["S"] == "ok"


@pytest.mark.asyncio
async def test_unauthorized_tenant_sends_german_error_no_audit() -> None:
    cfg = _config()
    ddb = MagicMock()
    ddb.query.return_value = {"Items": []}  # not in DDB
    bot = KiraCopilotBot(config=cfg, ddb_client=ddb, llm_client=MagicMock())

    turn_context = MagicMock()
    turn_context.send_activity = AsyncMock()
    turn_context.activity = _activity_with_claims(
        "x",
        {"tid": "entra-NOT-IN-ALLOWLIST"},
    )

    await bot.on_message_activity(turn_context)

    sent = turn_context.send_activity.await_args.args[0]
    assert "Lizenz" in sent.text or "subscription" in sent.text.lower()
    ddb.put_item.assert_not_called()


@pytest.mark.asyncio
async def test_agent_exception_emits_audit_error_status() -> None:
    cfg = _config()
    ddb = MagicMock()
    bot = KiraCopilotBot(config=cfg, ddb_client=ddb, llm_client=MagicMock())

    turn_context = MagicMock()
    turn_context.send_activity = AsyncMock()
    turn_context.activity = _activity_with_claims(
        "x",
        {"tid": "entra-allow", "oid": "o", "upn": "u@f.de"},
    )

    with patch("kira.copilot.agent_app.Agent") as MockAgent, \
         patch("kira.copilot.agent_app.route") as mock_route:
        MockAgent.return_value.run.side_effect = RuntimeError("bedrock 503")
        mock_route.return_value = MagicMock(
            tier=MagicMock(value="opus"),
            task_type=MagicMock(value="rechtliche_wuerdigung"),
        )

        await bot.on_message_activity(turn_context)

    sent_text = turn_context.send_activity.await_args.args[0].text
    assert "Fehler" in sent_text or "unerwarteter" in sent_text.lower()

    ddb.put_item.assert_called_once()
    audit_item = ddb.put_item.call_args.kwargs["Item"]
    assert audit_item["status"]["S"] == "error"
```

- [ ] **Step 2: Add pytest-asyncio dep**

Add to `pyproject.toml` `[copilot]` extras:

```toml
copilot = [
    "botbuilder-core>=4.16",
    "botbuilder-schema>=4.16",
    "botbuilder-integration-aiohttp>=4.16",
    "aiohttp>=3.9",
    "pytest-asyncio>=0.23",
]
```

Reinstall: `.venv/bin/pip install -e ".[dev,copilot]"`

Verify `pyproject.toml` has `asyncio_mode = "auto"` already configured (look in `[tool.pytest.ini_options]` — if not, add it).

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/copilot/test_agent_app.py -v`

Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement the bot**

Create `src/kira/copilot/agent_app.py`:

```python
"""Microsoft 365 Custom Engine Agent — Bot Framework handler for KIRA.

Receives Activity messages from M365 Copilot, validates tenant, runs
KIRA's existing agent loop with streaming + audit hooks, sends the final
answer back as an Activity.
"""

from __future__ import annotations

import logging
import time
import traceback
from typing import Any

from botbuilder.core import ActivityHandler, MessageFactory, TurnContext
from botbuilder.schema import Activity

from kira.agent import Agent
from kira.copilot.audit import AuditEmitter
from kira.copilot.config import Config
from kira.copilot.streamer import OutlookStreamer
from kira.copilot.tenant import UnauthorizedTenant
from kira.copilot.tenant_resolver import resolve_tenant
from kira.router import route

log = logging.getLogger(__name__)


class KiraCopilotBot(ActivityHandler):
    """KIRA exposed as a Bot Framework ActivityHandler.

    Single-turn handler (Phase 0 is stateless). Each user message:
    1. Resolves tenant from JWT claims (rejects unauthorized)
    2. Routes the query (existing kira.router)
    3. Runs Agent.run with streaming callbacks
    4. Emits audit row to DynamoDB
    5. Replies with the final answer
    """

    def __init__(
        self,
        *,
        config: Config,
        ddb_client: Any,
        llm_client: Any,
    ) -> None:
        self._config = config
        self._ddb = ddb_client
        self._llm = llm_client
        self._audit = AuditEmitter(
            ddb_client=ddb_client,
            audit_table_name=config.audit_table_name,
        )

    async def on_message_activity(self, turn_context: TurnContext) -> None:
        activity: Activity = turn_context.activity
        query = activity.text or ""

        claims = (activity.channel_data or {}).get("jwt_claims", {})
        user_oid = claims.get("oid", "")
        user_upn = claims.get("upn", "")
        session_id = (
            activity.conversation.id if activity.conversation else "unknown"
        )

        t_start = time.monotonic()

        # Resolve tenant
        try:
            tenant = resolve_tenant(
                jwt_claims=claims,
                ddb_client=self._ddb,
                tenants_table_name=self._config.tenants_table_name,
                allowlist=self._config.allowlist_entra_tids,
            )
        except UnauthorizedTenant as exc:
            log.warning("Tenant rejected: %s", exc)
            await turn_context.send_activity(
                MessageFactory.text(
                    "Ihre Lizenz für KIRA ist nicht aktiv. "
                    "Bitte kontaktieren Sie Ihren Administrator."
                )
            )
            return

        # Route + run agent with streaming.
        # Agent.run is synchronous; we run it in a worker thread so the
        # event loop stays free to deliver the streamer's queued
        # send_activity tasks in real time.
        import asyncio

        streamer = OutlookStreamer(turn_context)
        routing = route(query)

        try:
            agent = Agent(client=self._llm)
            result = await asyncio.to_thread(
                agent.run,
                query,
                routing=routing,
                on_tool_start=streamer.tool_started,
                on_tool_end=streamer.tool_ended,
            )
            # Make sure all streaming bubbles delivered BEFORE final answer
            await streamer.wait_pending()
        except Exception as exc:
            log.exception("Agent run failed")
            elapsed = int((time.monotonic() - t_start) * 1000)
            await streamer.wait_pending()  # flush any partial streaming
            await turn_context.send_activity(
                MessageFactory.text(
                    "Es ist ein unerwarteter Fehler aufgetreten. "
                    "Bitte erneut versuchen."
                )
            )
            self._audit.emit(
                tenant=tenant,
                user_oid=user_oid,
                user_upn=user_upn,
                session_id=session_id,
                routing_tier=getattr(routing.tier, "value", "unknown"),
                task_type=getattr(routing.task_type, "value", "unknown"),
                model_id="(unknown)",
                tool_calls=streamer.tool_calls,
                total_latency_ms=elapsed,
                iteration_count=0,
                response_text="",
                status="error",
                error_code=type(exc).__name__,
            )
            return

        elapsed = int((time.monotonic() - t_start) * 1000)

        # Emit audit
        try:
            self._audit.emit(
                tenant=tenant,
                user_oid=user_oid,
                user_upn=user_upn,
                session_id=session_id,
                routing_tier=getattr(routing.tier, "value", "unknown"),
                task_type=getattr(routing.task_type, "value", "unknown"),
                model_id=self._llm.model_id(routing.tier)
                    if hasattr(self._llm, "model_id") else "(unknown)",
                tool_calls=result.tool_calls,
                total_latency_ms=elapsed,
                iteration_count=len(result.tool_calls),
                response_text=result.final_text,
                status="ok",
                error_code=None,
            )
        except Exception:
            log.exception("Audit emit failed — non-blocking")

        # Final answer
        await turn_context.send_activity(MessageFactory.text(result.final_text))
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/copilot/test_agent_app.py -v`

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/kira/copilot/agent_app.py tests/copilot/test_agent_app.py pyproject.toml
git commit -m "feat(copilot): KiraCopilotBot Activity Protocol handler"
```

### Task 8: aiohttp web entrypoint (`main.py`)

**Files:**
- Create: `src/kira/copilot/main.py`

This is the entry point AgentCore Runtime invokes. No tests — just the production-only wiring (boto3 clients, LLMClient, BotFrameworkAdapter, aiohttp app).

- [ ] **Step 1: Implement main.py**

Create `src/kira/copilot/main.py`:

```python
"""aiohttp entry point for the KIRA Copilot agent.

Run inside AgentCore Runtime container. Production-only — local dev uses
the Bot Framework Emulator pointed at this same endpoint.
"""

from __future__ import annotations

import logging
import os
import sys

import boto3
from aiohttp import web
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

from kira.copilot.agent_app import KiraCopilotBot
from kira.copilot.config import Config
from kira.llm.client import build_client

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def _build_bot() -> KiraCopilotBot:
    config = Config.from_env()
    ddb = boto3.client("dynamodb", region_name=config.aws_region)
    llm = build_client(backend="bedrock_eu")  # type: ignore[arg-type]
    return KiraCopilotBot(config=config, ddb_client=ddb, llm_client=llm)


def _build_adapter(config: Config) -> BotFrameworkAdapter:
    # In production, M365 routes through API Gateway with JWT authorizer.
    # The adapter still validates Bot Framework auth headers on top.
    settings = BotFrameworkAdapterSettings(
        app_id=config.entra_app_client_id,
        app_password="",  # using JWT only; no client secret
    )
    return BotFrameworkAdapter(settings)


def create_app() -> web.Application:
    config = Config.from_env()
    bot = _build_bot()
    adapter = _build_adapter(config)

    async def messages(req: web.Request) -> web.Response:
        if "application/json" not in (req.headers.get("Content-Type") or ""):
            return web.Response(status=415)
        body = await req.json()
        activity = Activity().deserialize(body)
        auth_header = req.headers.get("Authorization", "")

        # Stash JWT claims for the bot to read (forwarded by API GW)
        claims_header = req.headers.get("X-Jwt-Claims", "")
        if claims_header:
            import json as _json
            try:
                activity.channel_data = activity.channel_data or {}
                activity.channel_data["jwt_claims"] = _json.loads(claims_header)
            except Exception:
                log.warning("Failed to parse X-Jwt-Claims header")

        async def turn_call(turn_context: TurnContext) -> None:
            await bot.on_message_activity(turn_context)

        invoke_response = await adapter.process_activity(
            activity, auth_header, turn_call,
        )
        if invoke_response:
            return web.json_response(
                data=invoke_response.body, status=invoke_response.status,
            )
        return web.Response(status=201)

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app = web.Application()
    app.router.add_post("/api/messages", messages)
    app.router.add_get("/health", health)
    return app


def main() -> int:
    port = int(os.environ.get("PORT", "8080"))
    web.run_app(create_app(), host="0.0.0.0", port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Note: the production setup expects API Gateway to forward JWT claims via `X-Jwt-Claims` header. CDK configures this. The adapter is the Bot Framework auth layer; API Gateway is the Entra layer.

- [ ] **Step 2: Smoke-import**

Run: `.venv/bin/python -c "from kira.copilot.main import create_app; print(type(create_app()))"`

Expected: `<class 'aiohttp.web.Application'>`. No exceptions.

- [ ] **Step 3: Commit**

```bash
git add src/kira/copilot/main.py
git commit -m "feat(copilot): aiohttp entry point + JWT claim forwarding from API Gateway"
```

---

## Phase 4 — Containerization

### Task 9: Dockerfile + container build

**Files:**
- Create: `infra/copilot_agent/Dockerfile`

- [ ] **Step 1: Write the Dockerfile**

Create `infra/copilot_agent/Dockerfile`:

```dockerfile
# Multi-stage build for kira-copilot-agent
# Base: Python 3.13 slim — minimal, secure, fast cold start
FROM python:3.13-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install build deps + project
RUN pip install --no-cache-dir --upgrade pip wheel build && \
    python -m build --wheel && \
    pip wheel -w /wheels --no-cache-dir "./dist/$(ls dist/)[copilot]"

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

# Install runtime deps from prebuilt wheels
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels kira-agent[copilot] && \
    rm -rf /wheels

EXPOSE 8080

# AgentCore Runtime expects the container to listen on $PORT
CMD ["python", "-m", "kira.copilot.main"]
```

- [ ] **Step 2: Local build to verify the Dockerfile**

Run from the repo root: `docker build -t kira-copilot-agent:local -f infra/copilot_agent/Dockerfile .`

Expected: builds without errors. Image size should be ~400-600 MB.

- [ ] **Step 3: Quick container run test**

Run:
```bash
docker run --rm -e ENTRA_APP_CLIENT_ID=test -e AWS_REGION=eu-central-1 \
    --name kira-copilot-test -d -p 8080:8080 kira-copilot-agent:local
sleep 3
curl -s http://localhost:8080/health
docker stop kira-copilot-test
```

Expected: `{"status": "ok"}` from the health endpoint. Stop the container.

If aiohttp fails to start because of missing boto3 credentials, that's expected in a bare container — wrap `_build_bot` so it doesn't actually call AWS until first request. Skip the health-curl assertion and just verify the container stays up for 3 seconds without crashing.

- [ ] **Step 4: Commit**

```bash
git add infra/copilot_agent/Dockerfile
git commit -m "feat(copilot): Dockerfile for AgentCore Runtime container"
```

---

## Phase 5 — CDK infrastructure

### Task 10: CDK app + stack scaffold + DDB tables

**Files:**
- Create: `infra/copilot_agent/cdk.json`
- Create: `infra/copilot_agent/app.py`
- Create: `infra/copilot_agent/requirements.txt`
- Create: `infra/copilot_agent/stack.py` (DDB part only)

- [ ] **Step 1: Create CDK config + requirements**

Create `infra/copilot_agent/cdk.json`:

```json
{
  "app": "../../.venv/bin/python app.py",
  "context": {
    "@aws-cdk/aws-lambda:recognizeLayerVersion": true,
    "@aws-cdk/aws-iam:minimizePolicies": true
  }
}
```

Create `infra/copilot_agent/requirements.txt`:

```
aws-cdk-lib>=2.140.0
constructs>=10.0.0
```

Install into the venv:

```bash
.venv/bin/pip install -r infra/copilot_agent/requirements.txt
```

- [ ] **Step 2: Create app.py and stack scaffold**

Create `infra/copilot_agent/app.py`:

```python
"""CDK app entry for the KIRA Copilot agent stack."""

import os

import aws_cdk as cdk

from stack import KiraCopilotAgentStack

app = cdk.App()

entra_client_id = app.node.try_get_context("entraClientId") or os.environ.get("ENTRA_APP_CLIENT_ID", "")
stepfather_entra_tid = (
    app.node.try_get_context("stepfatherEntraTid") or os.environ.get("STEPFATHER_ENTRA_TID", "")
)

if not entra_client_id:
    raise SystemExit("Missing context 'entraClientId' or env ENTRA_APP_CLIENT_ID")
if not stepfather_entra_tid:
    raise SystemExit("Missing context 'stepfatherEntraTid' or env STEPFATHER_ENTRA_TID")

KiraCopilotAgentStack(
    app, "KiraCopilotAgent",
    env=cdk.Environment(region="eu-central-1"),
    entra_app_client_id=entra_client_id,
    seed_entra_tid=stepfather_entra_tid,
)

app.synth()
```

Create `infra/copilot_agent/stack.py` (DDB part — extended in Task 11):

```python
"""KiraCopilotAgent CDK stack — Phase 0 SaaS foundation infrastructure."""

from __future__ import annotations

from aws_cdk import (
    CustomResource,
    RemovalPolicy,
    Stack,
    custom_resources as cr,
    aws_dynamodb as ddb,
    aws_iam as iam,
)
from constructs import Construct


class KiraCopilotAgentStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        entra_app_client_id: str,
        seed_entra_tid: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- DDB tables -------------------------------------------------

        tenants_table = ddb.Table(
            self, "TenantsTable",
            table_name="kira-tenants",
            partition_key=ddb.Attribute(name="tenant_id", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,  # never auto-delete production data
        )
        tenants_table.add_global_secondary_index(
            index_name="entra_tid-index",
            partition_key=ddb.Attribute(name="entra_tid", type=ddb.AttributeType.STRING),
        )

        audit_table = ddb.Table(
            self, "AuditTable",
            table_name="kira-audit",
            partition_key=ddb.Attribute(name="tenant_id", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl_epoch_seconds",
            removal_policy=RemovalPolicy.RETAIN,
        )

        # --- Seed the default tenant row --------------------------------

        seed_provider = cr.AwsCustomResource(
            self, "SeedDefaultTenant",
            on_create=cr.AwsSdkCall(
                service="DynamoDB",
                action="putItem",
                parameters={
                    "TableName": tenants_table.table_name,
                    "Item": {
                        "tenant_id": {"S": "default"},
                        "entra_tid": {"S": seed_entra_tid},
                        "firm_name": {"S": "Default Firm"},
                        "subscription_status": {"S": "active"},
                        "seats_allowed": {"N": "5"},
                        "case_packs_entitled": {"L": []},
                        "audit_retention_years": {"N": "7"},
                    },
                    "ConditionExpression": "attribute_not_exists(tenant_id)",
                },
                ignore_error_codes_matching="ConditionalCheckFailedException",
                physical_resource_id=cr.PhysicalResourceId.of("SeedDefaultTenant"),
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=["dynamodb:PutItem"],
                    resources=[tenants_table.table_arn],
                )
            ]),
        )
        seed_provider.node.add_dependency(tenants_table)

        # Store for later phases
        self._tenants_table = tenants_table
        self._audit_table = audit_table
        self._entra_app_client_id = entra_app_client_id
```

- [ ] **Step 3: Synth the stack to verify it parses**

Run from `infra/copilot_agent/`:

```bash
cd infra/copilot_agent
ENTRA_APP_CLIENT_ID=test-client-id STEPFATHER_ENTRA_TID=test-tenant-id \
    ../../.venv/bin/cdk synth > /tmp/kira-copilot-synth.yaml 2>&1
```

Expected: completes without errors. Inspect `/tmp/kira-copilot-synth.yaml` to verify both DDB tables + the SeedDefaultTenant Custom Resource are present.

- [ ] **Step 4: Commit**

```bash
git add infra/copilot_agent/cdk.json infra/copilot_agent/app.py infra/copilot_agent/requirements.txt infra/copilot_agent/stack.py
git commit -m "feat(infra): CDK stack scaffold + DynamoDB tables + default-tenant seed"
```

### Task 11: CDK stack — AgentCore Runtime + API Gateway + IAM

**Files:**
- Modify: `infra/copilot_agent/stack.py` (append AgentCore + API Gateway + IAM resources)

AgentCore Runtime CDK constructs may still be in `aws-cdk-lib.aws_bedrock` or a separate L1 construct module depending on CDK version. Implementer checks the latest CDK API; the cleanest approach is L1 CFN constructs if higher-level L2 constructs aren't yet available.

- [ ] **Step 1: Extend stack.py with the AgentCore + API Gateway resources**

Append to the `__init__` method of `KiraCopilotAgentStack` (after the seed provider, before storing `self._...`):

```python
        # --- ECR repo for the container image ----------------------------

        from aws_cdk import aws_ecr_assets as ecr_assets
        from aws_cdk import aws_apigatewayv2 as apigwv2
        from aws_cdk import aws_apigatewayv2_authorizers as apigw_auth
        from aws_cdk import aws_apigatewayv2_integrations as apigw_int
        from aws_cdk import aws_logs as logs

        container_image = ecr_assets.DockerImageAsset(
            self, "AgentImage",
            directory="../..",  # repo root — Dockerfile uses it as build context
            file="infra/copilot_agent/Dockerfile",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        # --- IAM role for AgentCore container ----------------------------

        agent_role = iam.Role(
            self, "AgentExecutionRole",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="KIRA Copilot agent execution role",
        )

        # Bedrock invoke (Claude EU profiles)
        agent_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=[
                f"arn:aws:bedrock:eu-central-1::foundation-model/anthropic.*",
                f"arn:aws:bedrock:eu-central-1:{self.account}:inference-profile/eu.anthropic.*",
            ],
        ))

        # Lambda invoke (legal-sources)
        agent_role.add_to_policy(iam.PolicyStatement(
            actions=["lambda:InvokeFunction"],
            resources=[
                f"arn:aws:lambda:eu-central-1:{self.account}:function:kira-legal-lookup-norm",
                f"arn:aws:lambda:eu-central-1:{self.account}:function:kira-legal-search",
            ],
        ))

        # DynamoDB access (scoped)
        tenants_table.grant_read_data(agent_role)
        audit_table.grant_write_data(agent_role)

        # CloudWatch Logs
        agent_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            resources=[
                f"arn:aws:logs:eu-central-1:{self.account}:log-group:/aws/bedrock-agentcore/*",
            ],
        ))

        # --- AgentCore Runtime (L1 CFN — adjust if L2 lands) -------------
        # NOTE: At time of writing, Bedrock AgentCore Runtime CDK L2 constructs
        # may not be available. Implementer: check `from aws_cdk import aws_bedrock`
        # for a `Runtime` class. If absent, use CfnRuntime via CfnResource. See:
        # https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_bedrock/

        from aws_cdk import CfnResource

        agent_runtime = CfnResource(
            self, "CopilotAgentRuntime",
            type="AWS::BedrockAgentCore::Runtime",
            properties={
                "AgentRuntimeName": "kira-copilot-agent",
                "AgentRuntimeArtifact": {
                    "ContainerConfiguration": {
                        "ContainerUri": container_image.image_uri,
                    },
                },
                "NetworkConfiguration": {
                    "NetworkMode": "PUBLIC",
                },
                "RoleArn": agent_role.role_arn,
                "EnvironmentVariables": {
                    "AUDIT_TABLE_NAME": audit_table.table_name,
                    "TENANTS_TABLE_NAME": tenants_table.table_name,
                    "ENTRA_APP_CLIENT_ID": entra_app_client_id,
                    "LEGAL_LOOKUP_FN": "kira-legal-lookup-norm",
                    "LEGAL_SEARCH_FN": "kira-legal-search",
                    "AWS_REGION": "eu-central-1",
                    "ALLOWLIST_ENTRA_TIDS": seed_entra_tid,
                },
            },
        )
        agent_runtime.node.add_dependency(container_image)

        # --- API Gateway HTTP API with Entra JWT authorizer --------------

        jwt_authorizer = apigw_auth.HttpJwtAuthorizer(
            "EntraJwtAuthorizer",
            jwt_issuer=f"https://login.microsoftonline.com/common/v2.0",
            jwt_audience=[entra_app_client_id],
        )

        http_api = apigwv2.HttpApi(
            self, "CopilotApi",
            api_name="kira-copilot-api",
            description="HTTPS entry for KIRA Copilot agent from M365",
            default_authorizer=jwt_authorizer,
        )

        # AgentCore integration — use HTTP_PROXY to forward to runtime endpoint
        # Note: when AgentCore Runtime exposes a public DNS, point integration_uri
        # at it. Until then, integrate via service link / VPC private API.
        agent_integration = apigw_int.HttpUrlIntegration(
            "AgentIntegration",
            url=agent_runtime.get_att("Endpoint").to_string()
                if agent_runtime.get_att("Endpoint") else "https://placeholder",
            method=apigwv2.HttpMethod.POST,
        )

        http_api.add_routes(
            path="/api/messages",
            methods=[apigwv2.HttpMethod.POST],
            integration=agent_integration,
        )
```

Also extend the stack outputs at the bottom of `__init__`:

```python
        from aws_cdk import CfnOutput

        CfnOutput(self, "ApiEndpoint", value=http_api.url or "")
        CfnOutput(self, "AgentRuntimeArn", value=agent_runtime.ref)
        CfnOutput(self, "TenantsTableName", value=tenants_table.table_name)
        CfnOutput(self, "AuditTableName", value=audit_table.table_name)
```

- [ ] **Step 2: Re-synth**

```bash
cd infra/copilot_agent
ENTRA_APP_CLIENT_ID=test-client-id STEPFATHER_ENTRA_TID=test-tenant-id \
    ../../.venv/bin/cdk synth > /tmp/kira-copilot-synth.yaml 2>&1
```

Expected: completes. Inspect for `AWS::BedrockAgentCore::Runtime`, `AWS::ApiGatewayV2::Api`, `AWS::IAM::Role`.

If the CDK reports `AWS::BedrockAgentCore::Runtime` is an unknown resource type, this means the CloudFormation type name is different from what we guessed. The implementer should check `aws cloudformation describe-type --type RESOURCE --type-name AWS::BedrockAgentCore::Runtime` (and variants like `AWS::Bedrock::AgentCoreRuntime`) to find the actual name in the current API. Substitute accordingly.

- [ ] **Step 3: Commit**

```bash
git add infra/copilot_agent/stack.py
git commit -m "feat(infra): AgentCore Runtime + API Gateway + IAM least privilege"
```

### Task 12: M365 sideload manifest

**Files:**
- Create: `infra/copilot_agent/manifest/manifest.json`
- Create: `infra/copilot_agent/manifest/icon-color.png` (placeholder)
- Create: `infra/copilot_agent/manifest/icon-outline.png` (placeholder)

- [ ] **Step 1: Create the manifest template**

Create `infra/copilot_agent/manifest/manifest.json`:

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.16/MicrosoftTeams.schema.json",
  "manifestVersion": "1.16",
  "version": "0.1.0",
  "id": "REPLACE_WITH_ENTRA_APP_CLIENT_ID",
  "packageName": "de.kira.copilot",
  "developer": {
    "name": "KIRA",
    "websiteUrl": "https://example.invalid/kira",
    "privacyUrl": "https://example.invalid/kira/privacy",
    "termsOfUseUrl": "https://example.invalid/kira/terms"
  },
  "icons": {
    "color": "icon-color.png",
    "outline": "icon-outline.png"
  },
  "name": {
    "short": "KIRA",
    "full": "KIRA Mietrecht"
  },
  "description": {
    "short": "Juristischer Junior-Assistent für deutsches Mietrecht.",
    "full": "KIRA recherchiert und entwirft juristische Ergebnisse für deutsches Mietrecht. Zugriff auf alle Bundesgesetze und Rechtsverordnungen tagesaktuell von gesetze-im-internet.de. Jede §-Zitierung wird über das offizielle Bundesrecht verifiziert."
  },
  "accentColor": "#1F3A5F",
  "bots": [
    {
      "botId": "REPLACE_WITH_ENTRA_APP_CLIENT_ID",
      "scopes": ["personal"],
      "supportsFiles": false,
      "isNotificationOnly": false
    }
  ],
  "permissions": [
    "identity"
  ],
  "validDomains": [
    "REPLACE_WITH_API_GATEWAY_DOMAIN"
  ]
}
```

- [ ] **Step 2: Add icon placeholders**

Create two 192×192 PNG placeholders (any blue solid colour will do for MVP). Use ImageMagick or any image tool. Save as `infra/copilot_agent/manifest/icon-color.png` and `icon-outline.png`. The implementer can use:

```bash
python -c "
from PIL import Image, ImageDraw
img = Image.new('RGBA', (192, 192), (31, 58, 95, 255))
ImageDraw.Draw(img).text((40, 80), 'KIRA', fill='white')
img.save('infra/copilot_agent/manifest/icon-color.png')
img.save('infra/copilot_agent/manifest/icon-outline.png')
"
```

(Requires `pip install Pillow`. Skip if Pillow isn't trivially installable — copy any 192×192 PNG instead.)

- [ ] **Step 3: Commit**

```bash
git add infra/copilot_agent/manifest/
git commit -m "feat(copilot): M365 sideload manifest template + placeholder icons"
```

### Task 13: Operations runbooks

**Files:**
- Create: `docs/operations/entra-app-registration.md`
- Create: `docs/operations/sideload-m365-app.md`

- [ ] **Step 1: Entra app registration runbook**

Create `docs/operations/entra-app-registration.md`:

```markdown
# Entra App Registration — KIRA Copilot

One-time setup, performed in the firm's Microsoft 365 tenant by a Global Admin.

## Steps

1. Sign in to https://portal.azure.com with admin credentials for the firm's tenant.
2. Navigate to **Microsoft Entra ID → App registrations → New registration**.
3. Fill in:
   - **Name**: `KIRA Copilot`
   - **Supported account types**: `Accounts in this organizational directory only (Single tenant)` for Phase 0. (Phase 3 changes this to multi-tenant for AppSource.)
   - **Redirect URI**: leave blank for Phase 0.
4. Click **Register**.
5. From the resulting page, **copy**:
   - **Application (client) ID** — used as `ENTRA_APP_CLIENT_ID` everywhere
   - **Directory (tenant) ID** — used as `STEPFATHER_ENTRA_TID`

## Expose an API scope

6. Go to **Expose an API → Add a scope**.
7. Accept the suggested `api://<client-id>` Application ID URI.
8. Create scope:
   - **Scope name**: `kira.invoke`
   - **Who can consent**: `Admins only` for Phase 0
   - **Admin consent display name**: `Invoke KIRA legal agent`
   - **Admin consent description**: `Allows the user to invoke the KIRA legal-research agent on their behalf.`
   - **State**: `Enabled`
9. Click **Add scope**.

## Add API permissions for the bot to call back into Microsoft Graph

10. Go to **API permissions → Add a permission → Microsoft Graph → Delegated permissions**.
11. Add: `User.Read` (default).
12. Click **Grant admin consent** for the firm tenant.

## Deploy

Use the client ID + tenant ID as CDK context:

```bash
cdk deploy \
    --context entraClientId=<CLIENT_ID> \
    --context stepfatherEntraTid=<TENANT_ID>
```

After deploy, note the `ApiEndpoint` CDK output — used in the M365 manifest's `validDomains`.
```

- [ ] **Step 2: Sideload runbook**

Create `docs/operations/sideload-m365-app.md`:

```markdown
# Sideload KIRA into M365 (Phase 0)

For internal testing in your firm's tenant only. AppSource publication is Phase 3.

## Prerequisites

- Entra app registered (see `entra-app-registration.md`)
- CDK stack deployed; `ApiEndpoint` output captured
- M365 tenant admin role

## Steps

1. **Edit the manifest template** at `infra/copilot_agent/manifest/manifest.json`:
   - Replace `REPLACE_WITH_ENTRA_APP_CLIENT_ID` (3 places) with your Application (client) ID.
   - Replace `REPLACE_WITH_API_GATEWAY_DOMAIN` with the host portion of `ApiEndpoint` (e.g., `abc123.execute-api.eu-central-1.amazonaws.com`).

2. **Package the manifest**:

```bash
cd infra/copilot_agent/manifest
zip kira-manifest.zip manifest.json icon-color.png icon-outline.png
```

3. **Upload via Microsoft 365 admin center**:
   - Sign in to https://admin.microsoft.com as a tenant admin.
   - Navigate to **Settings → Integrated apps → Upload custom apps**.
   - Choose **Provide manifest file**, upload `kira-manifest.zip`.
   - Confirm deployment to **Everyone in your organization** (or a test pilot group).

4. **Verify in Outlook**:
   - Open Outlook on the web or desktop.
   - Open the Copilot pane.
   - You should see "KIRA" as an available agent.
   - Type `@KIRA Was sagt § 535 BGB?` and verify a streaming response arrives.

5. **Verify audit row in DynamoDB**:

```bash
aws dynamodb scan --table-name kira-audit --region eu-central-1 \
    --filter-expression "tenant_id = :t" \
    --expression-attribute-values '{":t":{"S":"default"}}' \
    --max-items 5
```

Confirm an item exists, `status` is `ok`, and `tool_calls` contains structured args summaries (no free text).

## Rollback

To remove the sideload:

```bash
# Via admin center: Settings → Integrated apps → KIRA → Remove
```
```

- [ ] **Step 3: Commit**

```bash
git add docs/operations/entra-app-registration.md docs/operations/sideload-m365-app.md
git commit -m "docs(operations): Entra app registration + M365 sideload runbooks"
```

---

## Phase 6 — Live verification

### Task 14: Live integration test

**Files:**
- Create: `tests/copilot/test_agent_app_live.py`

Live test asserts the deployed AgentCore Runtime endpoint actually works end-to-end. Opt-in via `RUN_LIVE_TESTS=1`. Requires AWS credentials + the stack deployed.

- [ ] **Step 1: Write the live test**

Create `tests/copilot/test_agent_app_live.py`:

```python
"""Live end-to-end test against the deployed KIRA Copilot stack.

Opt-in via RUN_LIVE_TESTS=1. Requires:
  - AWS credentials with read on kira-tenants and kira-audit DDB tables
  - Stack already deployed
  - ENTRA_APP_CLIENT_ID set in the environment (matches the deployed value)
"""

from __future__ import annotations

import os
import time
import uuid
from unittest.mock import AsyncMock, MagicMock

import boto3
import pytest
from botbuilder.schema import Activity, ActivityTypes, ChannelAccount

from kira.copilot.agent_app import KiraCopilotBot
from kira.copilot.config import Config
from kira.llm.client import build_client

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="RUN_LIVE_TESTS not set",
)


@pytest.fixture
def config() -> Config:
    cfg = Config.from_env()
    return cfg


@pytest.fixture
def ddb() -> object:
    return boto3.client("dynamodb", region_name="eu-central-1")


@pytest.mark.asyncio
async def test_end_to_end_lookup_bgb_535(config: Config, ddb: object) -> None:
    bot = KiraCopilotBot(
        config=config, ddb_client=ddb,
        llm_client=build_client(backend="bedrock_eu"),
    )

    session_id = f"live-test-{uuid.uuid4().hex[:8]}"
    turn_context = MagicMock()
    turn_context.send_activity = AsyncMock()
    turn_context.activity = Activity(
        type=ActivityTypes.message,
        text="Was sagt § 535 BGB? Bitte mit Wortlaut antworten.",
        from_property=ChannelAccount(id="live-test", aad_object_id="live-oid", name="live@test.de"),
        conversation=MagicMock(id=session_id),
        channel_data={"jwt_claims": {
            "tid": config.allowlist_entra_tids[0] if config.allowlist_entra_tids else "default",
            "oid": "live-oid",
            "upn": "live-test@firm.de",
        }},
    )

    await bot.on_message_activity(turn_context)

    # Final reply should mention BGB
    sent_messages = [c.args[0].text for c in turn_context.send_activity.await_args_list]
    final = sent_messages[-1]
    assert "BGB" in final
    assert "Vermieter" in final or "Mietsache" in final

    # Audit row should appear in DDB within 5 seconds
    time.sleep(2)
    response = ddb.query(
        TableName=config.audit_table_name,
        KeyConditionExpression="tenant_id = :tid",
        ExpressionAttributeValues={":tid": {"S": "default"}},
        ScanIndexForward=False,
        Limit=20,
    )
    matching = [
        i for i in response.get("Items", [])
        if i.get("session_id", {}).get("S") == session_id
    ]
    assert len(matching) >= 1
    item = matching[0]
    assert item["status"]["S"] == "ok"
    # PII-free check
    tool_calls_str = str(item.get("tool_calls", {}))
    assert "Klaus" not in tool_calls_str
    assert "Vermieter" not in tool_calls_str or "lookup" in tool_calls_str.lower()
```

- [ ] **Step 2: Skip-run validation (no env var)**

Run: `.venv/bin/python -m pytest tests/copilot/test_agent_app_live.py -v`

Expected: 1 skipped.

- [ ] **Step 3: Live run (after stack deployment)**

Once the stack is deployed (Task 15), run:

```bash
RUN_LIVE_TESTS=1 .venv/bin/python -m pytest tests/copilot/test_agent_app_live.py -v
```

Expected: 1 passed.

If the test fails for tenant-lookup reasons, verify the seed `default` tenant row exists in DDB. If it fails for Bedrock reasons, verify the local AWS identity has Bedrock invoke permissions on the EU Anthropic inference profiles.

- [ ] **Step 4: Commit**

```bash
git add tests/copilot/test_agent_app_live.py
git commit -m "test(copilot): live end-to-end test against deployed stack"
```

### Task 15: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Full unit test suite**

Run: `.venv/bin/python -m pytest tests/ -q -m 'not live and not perf'`

Expected: all tests pass. Note the count (~230 tests projected: existing 195 + ~35 new in copilot).

- [ ] **Step 2: Ruff clean on new code**

Run: `.venv/bin/python -m ruff check src/kira/copilot/ tests/copilot/ infra/copilot_agent/`

Expected: `All checks passed!` Pre-existing violations elsewhere in the repo are OK.

- [ ] **Step 3: CDK synth from clean state**

```bash
cd infra/copilot_agent
rm -rf cdk.out/
ENTRA_APP_CLIENT_ID=test STEPFATHER_ENTRA_TID=test ../../.venv/bin/cdk synth > /tmp/synth.yaml
test -s /tmp/synth.yaml && echo OK
```

Expected: `OK`.

- [ ] **Step 4: Deploy to AWS (requires user credentials)**

```bash
cd infra/copilot_agent
# Use real values:
../../.venv/bin/cdk deploy \
    --context entraClientId=<real-client-id> \
    --context stepfatherEntraTid=<real-tenant-id> \
    --require-approval never
```

Expected: stack deploys cleanly. Note the `ApiEndpoint`, `AgentRuntimeArn`, `TenantsTableName`, `AuditTableName` outputs.

If `AWS::BedrockAgentCore::Runtime` is rejected as an unknown resource type, the user must either upgrade the CDK version or supply the correct CloudFormation type name. Iterate.

- [ ] **Step 5: Live test against deployed stack**

```bash
RUN_LIVE_TESTS=1 ENTRA_APP_CLIENT_ID=<real> ALLOWLIST_ENTRA_TIDS=<real-tenant-id> \
    .venv/bin/python -m pytest tests/copilot/test_agent_app_live.py -v
```

Expected: 1 passed.

- [ ] **Step 6: Sideload + Outlook manual test**

Follow `docs/operations/sideload-m365-app.md`. The success bar: stepfather opens Outlook → Copilot → `@KIRA Was sagt § 535 BGB?` → streaming activity arrives → final cited answer in <30s.

- [ ] **Step 7: Final commit if Step 4+ revealed fixes**

```bash
git add -u
git commit -m "fix: post-deployment cleanup"
```

(Skip if no fixes were needed.)

- [ ] **Step 8: Push the branch**

```bash
git push origin feat/phase0-saas-foundation
```

---

## Summary of expected commits

1. `feat(agent): optional on_tool_start/on_tool_end callbacks on Agent.run`
2. `feat(copilot): scaffold copilot module with Config.from_env`
3. `feat(copilot): Tenant dataclass + subscription guard`
4. `feat(copilot): tenant resolver with DDB lookup + allowlist fallback`
5. `feat(copilot): PII-free audit emitter with per-tool args summary`
6. `feat(copilot): streamer translates tool-call events to Outlook activities`
7. `feat(copilot): KiraCopilotBot Activity Protocol handler`
8. `feat(copilot): aiohttp entry point + JWT claim forwarding from API Gateway`
9. `feat(copilot): Dockerfile for AgentCore Runtime container`
10. `feat(infra): CDK stack scaffold + DynamoDB tables + default-tenant seed`
11. `feat(infra): AgentCore Runtime + API Gateway + IAM least privilege`
12. `feat(copilot): M365 sideload manifest template + placeholder icons`
13. `docs(operations): Entra app registration + M365 sideload runbooks`
14. `test(copilot): live end-to-end test against deployed stack`

(Optional: post-deployment cleanup commit from Task 15 Step 7.)

---

## Open follow-ups (not in this PR)

- **Case packs** (Phase 1): expert-curated tenancy-law system-prompt addenda + required §§ + output quality checklists. Stepfather co-design.
- **Compliance package** (Phase 2 parallel): DPA template, Verschwiegenheitsverpflichtung, subprocessor list, EU AI Act Annex III docs.
- **AppSource publication** (Phase 3): Microsoft Partner Center, app cert, billing.
- **Outlook web add-in** (separate): TypeScript pane for mandate-data workflows.
- **`mandate_ref` field** (Phase 2): once add-in has UI for it.
- **Right-to-erasure self-service** (Phase 2): API + UI for `session_id` → DDB delete.
- **Multi-tenant DDB-authoritative** (Phase 2): drop allowlist fallback.
- **AgentCore Observability** (separate): replace structured stderr logs with AgentCore tracing.

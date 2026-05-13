"""End-to-end test: real Agent.run, real Lambdas, stubbed Bedrock.

Skipped unless RUN_LIVE_TESTS=1. The stub LLMClient emits a scripted
tool-use sequence so the test is deterministic and doesn't cost Bedrock
tokens — but the legal-sources Lambdas are invoked for real.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import pytest

from kira.agent import Agent
from kira.llm.models import ModelTier
from kira.pseudonymizer import EntityKind, Gender, Party, Role
from kira.router import route

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="RUN_LIVE_TESTS not set",
)


@dataclass
class _StubBlock:
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None


@dataclass
class _StubResponse:
    content: list[_StubBlock]
    stop_reason: str
    usage: Any = None  # placeholder; some core.py paths may read this


class _StubMessages:
    """Scripted Bedrock-compatible client.

    Turn 1: emit tool_use(search_norm)
    Turn 2: emit tool_use(lookup_norm, BGB §536)
    Turn 3: emit final text
    """

    def __init__(self) -> None:
        self._turn = 0

    def create(self, **_: Any) -> _StubResponse:
        self._turn += 1
        if self._turn == 1:
            return _StubResponse(
                content=[
                    _StubBlock(type="tool_use", id="tu_1", name="search_norm",
                               input={"query": "Mietminderung wegen Schimmel", "k": 3}),
                ],
                stop_reason="tool_use",
            )
        if self._turn == 2:
            return _StubResponse(
                content=[
                    _StubBlock(type="tool_use", id="tu_2", name="lookup_norm",
                               input={"gesetz": "BGB", "paragraph": "536"}),
                ],
                stop_reason="tool_use",
            )
        return _StubResponse(
            content=[_StubBlock(
                type="text",
                text="Einschlägig ist § 536 BGB (Mietminderung bei Mängeln).",
            )],
            stop_reason="end_turn",
        )


class _StubLLMClient:
    """Minimal LLMClient stand-in that satisfies Agent.__init__ and Agent.run."""

    backend = "bedrock_eu"

    def __init__(self) -> None:
        self.raw = type("StubRaw", (), {"messages": _StubMessages()})()

    def model_id(self, tier: ModelTier) -> str:
        return f"stub-model-{tier}"


def test_end_to_end_search_then_lookup_then_answer() -> None:
    parties = [
        Party(
            real_name="Klaus Müller",
            role=Role.MIETER,
            kind=EntityKind.nat,
            gender=Gender.m,
            age_band=None,
            aliases=[],
        ),
    ]
    routing = route("Mietminderung wegen Schimmel?")
    agent = Agent(client=_StubLLMClient())
    result = agent.run(
        "Mietminderung wegen Schimmel?\n\nMein Mandant Klaus Müller hat Schimmel.",
        parties=parties,
        routing=routing,
    )
    assert "§ 536 BGB" in result.final_text
    # Both tools should have been called
    tool_names = [c["tool"] for c in result.tool_calls]
    assert "search_norm" in tool_names
    assert "lookup_norm" in tool_names

"""Agent-Loop mit Tool-Use.

Implementiert den klassischen Anthropic-Tool-Use-Zyklus manuell, damit
wir vollständige Kontrolle über Pseudonymisierung, Leakage-Check und
Modell-Routing haben.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from kira.agent.system_prompts import JUNIOR_ASSOCIATE_DE
from kira.agent.tools import REGISTRY  # noqa: F401  Side-Effect: registriert Tools
from kira.agent.tools._registry import REGISTRY as TOOLS
from kira.llm.client import LLMClient
from kira.llm.models import ModelTier
from kira.pseudonymizer import LeakageError, Party, Pseudonymizer, check_for_leaks
from kira.router import RoutingDecision

log = logging.getLogger(__name__)


@dataclass
class AgentResult:
    final_text: str  # bereits re-personalisiert
    pseudonymized_text: str  # zur Nachprüfung im Audit
    routing: RoutingDecision
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None


class Agent:
    """Junior-Associate-Agent mit Pseudonymisierung und Routing."""

    def __init__(
        self,
        client: LLMClient,
        *,
        max_tokens: int = 4096,
        max_iterations: int = 8,
        system_prompt: str = JUNIOR_ASSOCIATE_DE,
    ) -> None:
        self._client = client
        self._max_tokens = max_tokens
        self._max_iterations = max_iterations
        self._system = system_prompt

    def run(
        self,
        query: str,
        *,
        parties: list[Party],
        routing: RoutingDecision,
    ) -> AgentResult:
        # 1. Pseudonymisieren
        pseudo = Pseudonymizer(parties=parties)
        prepared = pseudo.process(query)

        # 2. Leakage-Check (Hard-Fail vor LLM-Call)
        leak_report = check_for_leaks(prepared.text, prepared.parties)
        if leak_report:
            details = ", ".join(f"{label}={value!r}" for label, value in leak_report.leaks[:5])
            raise LeakageError(
                f"Pseudonymisierung unvollständig — Klardaten im Text: {details}. "
                f"LLM-Call abgebrochen."
            )

        # 3. Tool-Use-Loop
        messages: list[dict[str, Any]] = [{"role": "user", "content": prepared.text}]
        tool_specs = [t.to_anthropic_spec() for t in TOOLS.values()]
        tool_log: list[dict[str, Any]] = []
        stop_reason: str | None = None

        for iteration in range(self._max_iterations):
            response = self._client.raw.messages.create(
                model=self._client.model_id(routing.tier),
                max_tokens=self._max_tokens,
                system=self._system,
                tools=tool_specs,
                messages=messages,
            )
            stop_reason = response.stop_reason

            # Assistant-Antwort an Verlauf hängen
            messages.append({"role": "assistant", "content": response.content})

            if stop_reason != "tool_use":
                break

            # Tool-Calls ausführen
            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                tool_name = block.name
                tool_input = block.input or {}
                tool = TOOLS.get(tool_name)
                if tool is None:
                    output = f"FEHLER: Unbekanntes Tool {tool_name!r}."
                    is_error = True
                else:
                    try:
                        output = tool.run(tool_input)
                        is_error = False
                    except Exception as exc:  # pragma: no cover  defensiv
                        log.exception("Tool %s failed", tool_name)
                        output = f"FEHLER bei {tool_name}: {exc}"
                        is_error = True

                tool_log.append(
                    {
                        "tool": tool_name,
                        "input": tool_input,
                        "output_preview": output[:300],
                        "is_error": is_error,
                    }
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                        "is_error": is_error,
                    }
                )

            messages.append({"role": "user", "content": tool_results})
        else:
            log.warning("Max iterations (%d) erreicht", self._max_iterations)

        # 4. Finale Antwort extrahieren + re-personalisieren
        final_blocks = messages[-1]["content"]
        if isinstance(final_blocks, list):
            text = "".join(
                getattr(b, "text", "")
                for b in final_blocks
                if getattr(b, "type", None) == "text"
            )
        else:
            text = str(final_blocks)

        repersonalized = prepared.repersonalize(text)

        return AgentResult(
            final_text=repersonalized,
            pseudonymized_text=text,
            routing=routing,
            tool_calls=tool_log,
            stop_reason=stop_reason,
        )

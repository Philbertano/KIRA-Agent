"""LLM-basierter Komplexitäts-Klassifikator (Haiku).

Wird nur aufgerufen, wenn der regelbasierte Router auf TaskType.UNKNOWN landet.
Hält die Latenz / Kosten gering und gibt strukturiertes JSON zurück.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from kira.llm.client import LLMClient
from kira.llm.models import ModelTier
from kira.router.policy import RoutingDecision, TaskType


class ClassifierResult(BaseModel):
    task_type: Literal[
        "extract_sachverhalt",
        "norm_lookup",
        "frist_berechnen",
        "schriftsatz_vorlage",
        "recherche",
        "sachverhalt_vergleich",
        "rechtliche_wuerdigung",
        "gutachten",
        "unknown",
    ]
    complexity: int = Field(ge=1, le=5)
    reasoning: str


_PROMPT = """Du bist ein Klassifikator für juristische Anfragen einer
Mietrechts-Kanzlei. Du gibst NUR JSON aus, kein Fließtext.

Aufgabentypen:
- extract_sachverhalt: Fakten aus Mandanten-E-Mail / PDF strukturieren
- norm_lookup: § oder Norm nachschlagen
- frist_berechnen: Frist berechnen (Kündigung, Verjährung, Widerspruch)
- schriftsatz_vorlage: Anwaltsschreiben oder Schriftsatz entwerfen
- recherche: Rechtsprechung suchen / zusammenfassen
- sachverhalt_vergleich: aktuellen Fall mit eigenen Mandaten vergleichen
- rechtliche_wuerdigung: einzelne Anspruchsgrundlage prüfen
- gutachten: vollumfängliche rechtliche Prüfung mehrerer Aspekte
- unknown: passt zu nichts davon

Komplexität 1 (trivial) bis 5 (sehr komplex, mehrstufiges Reasoning).

Antworte mit JSON:
{"task_type": "...", "complexity": <1-5>, "reasoning": "<knapp>"}
"""


def classify_with_llm(client: LLMClient, query: str) -> RoutingDecision:
    response = client.raw.messages.create(
        model=client.model_id(ModelTier.HAIKU),
        max_tokens=300,
        system=_PROMPT,
        messages=[{"role": "user", "content": query}],
    )

    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    # Robustes JSON-Extracting (LLM kann gelegentlich Fließtext drumherum schreiben)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return RoutingDecision(
            tier=ModelTier.SONNET,
            task_type=TaskType.UNKNOWN,
            reason="Klassifikator-JSON nicht parsebar — Default Sonnet",
        )

    try:
        result = ClassifierResult.model_validate_json(text[start : end + 1])
    except Exception as exc:
        return RoutingDecision(
            tier=ModelTier.SONNET,
            task_type=TaskType.UNKNOWN,
            reason=f"Klassifikator-Parsing fehlgeschlagen: {exc} — Default Sonnet",
        )

    if result.complexity >= 4:
        tier = ModelTier.OPUS
    elif result.complexity >= 2:
        tier = ModelTier.SONNET
    else:
        tier = ModelTier.HAIKU

    return RoutingDecision(
        tier=tier,
        task_type=TaskType(result.task_type),
        reason=f"Haiku-Klassifikator: complexity={result.complexity}, {result.reasoning}",
    )

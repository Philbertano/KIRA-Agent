"""Regelbasierter Router.

Klassifiziert die Anfrage anhand von Schlüsselwörtern und Längen-Heuristik.
Bei Unsicherheit wird ein Haiku-Klassifikator nachgelagert (siehe classifier.py).
"""

from __future__ import annotations

import re
from typing import Final

from kira.llm.models import ModelTier
from kira.router.policy import POLICY, RoutingDecision, TaskType


_KEYWORDS: Final[dict[TaskType, list[str]]] = {
    TaskType.GUTACHTEN: [
        "gutachten", "rechtsgutachten", "umfassende prüfung", "sämtliche ansprüche",
        "alle anspruchsgrundlagen",
    ],
    TaskType.RECHTLICHE_WUERDIGUNG: [
        "würdigung", "wuerdigung", "rechtliche bewertung", "rechtslage",
        "erfolgsaussicht", "anspruchsprüfung", "anspruchspruefung",
        "unterlassungsanspruch", "schadensersatz",
    ],
    TaskType.SCHRIFTSATZ_VORLAGE: [
        "schriftsatz", "anschreiben", "kündigung formulieren", "kuendigung formulieren",
        "abmahnung", "mahnung", "klageschrift", "entwirf", "entwurf",
    ],
    TaskType.SACHVERHALT_VERGLEICH: [
        "ähnliche fälle", "aehnliche faelle", "präzedenzfall", "praezedenzfall",
        "vergleichbar", "frühere mandate", "fruehere mandate",
    ],
    TaskType.RECHERCHE: [
        "recherchiere", "suche rechtsprechung", "bgh-rechtsprechung", "urteile zu",
        "fundstelle",
    ],
    TaskType.EXTRACT_SACHVERHALT: [
        "extrahiere", "fasse zusammen", "fass zusammen", "wer hat wen", "extraktion",
    ],
    TaskType.NORM_LOOKUP: [
        "§", "paragraph", "wortlaut", "norm", "bgb", "betrkv", "heizkostenv",
    ],
    TaskType.FRIST_BERECHNEN: [
        "frist", "kündigungsfrist", "kuendigungsfrist", "verjährung", "verjaehrung",
        "bis wann", "wann läuft", "wann laeuft",
    ],
}


def _classify(query: str) -> TaskType:
    q = query.lower()

    # Spezifischste Tasks zuerst
    for task in [
        TaskType.GUTACHTEN,
        TaskType.RECHTLICHE_WUERDIGUNG,
        TaskType.SCHRIFTSATZ_VORLAGE,
        TaskType.SACHVERHALT_VERGLEICH,
        TaskType.RECHERCHE,
        TaskType.FRIST_BERECHNEN,
        TaskType.EXTRACT_SACHVERHALT,
        TaskType.NORM_LOOKUP,
    ]:
        for kw in _KEYWORDS.get(task, []):
            if kw in q:
                return task

    return TaskType.UNKNOWN


def _complexity_signal(query: str) -> int:
    """Heuristik: Wortzahl + Frage-Komplexität als zusätzliches Eskalations-Signal."""
    words = len(re.findall(r"\w+", query))
    score = 0
    if words > 200:
        score += 2
    elif words > 80:
        score += 1
    if re.search(r"\b(zudem|außerdem|ausserdem|ferner|ferner|darüber hinaus)\b", query, re.I):
        score += 1
    if query.count("?") >= 3:
        score += 1
    return score


def route(query: str, *, force_tier: ModelTier | None = None) -> RoutingDecision:
    """Bestimmt das Modell für eine Anfrage.

    `force_tier` überstimmt alles — der Anwalt hat das letzte Wort.
    """
    if force_tier is not None:
        return RoutingDecision(
            tier=force_tier,
            task_type=TaskType.UNKNOWN,
            reason="Anwalt-Override",
            forced=True,
        )

    task = _classify(query)
    tier = POLICY[task]

    # Eskalation: Wenn Sonnet-Aufgabe sehr komplex aussieht → Opus
    complexity = _complexity_signal(query)
    if tier == ModelTier.SONNET and complexity >= 2:
        return RoutingDecision(
            tier=ModelTier.OPUS,
            task_type=task,
            reason=f"Eskaliert von Sonnet zu Opus (Komplexitäts-Score {complexity})",
        )

    return RoutingDecision(
        tier=tier,
        task_type=task,
        reason=f"Policy-Match auf {task.value}",
    )

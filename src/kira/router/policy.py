"""Routing-Policy-Tabelle.

Übersicht, wann welches Modell verwendet wird. Anwälte können das
mit `force_tier` immer überschreiben.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kira.llm.models import ModelTier


class TaskType(str, Enum):
    EXTRACT_SACHVERHALT = "extract_sachverhalt"
    NORM_LOOKUP = "norm_lookup"
    FRIST_BERECHNEN = "frist_berechnen"
    SCHRIFTSATZ_VORLAGE = "schriftsatz_vorlage"
    RECHERCHE = "recherche"
    SACHVERHALT_VERGLEICH = "sachverhalt_vergleich"
    RECHTLICHE_WUERDIGUNG = "rechtliche_wuerdigung"
    GUTACHTEN = "gutachten"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RoutingDecision:
    tier: ModelTier
    task_type: TaskType
    reason: str
    forced: bool = False


# Statische Routing-Tabelle.
POLICY: dict[TaskType, ModelTier] = {
    TaskType.EXTRACT_SACHVERHALT: ModelTier.HAIKU,
    TaskType.NORM_LOOKUP: ModelTier.SONNET,
    TaskType.FRIST_BERECHNEN: ModelTier.HAIKU,
    TaskType.SCHRIFTSATZ_VORLAGE: ModelTier.SONNET,
    TaskType.RECHERCHE: ModelTier.SONNET,
    TaskType.SACHVERHALT_VERGLEICH: ModelTier.SONNET,
    TaskType.RECHTLICHE_WUERDIGUNG: ModelTier.OPUS,
    TaskType.GUTACHTEN: ModelTier.OPUS,
    TaskType.UNKNOWN: ModelTier.SONNET,  # sicherer Default
}

"""Tests für den regelbasierten Router."""

from __future__ import annotations

from kira.llm.models import ModelTier
from kira.router import route
from kira.router.policy import TaskType


def test_routes_norm_lookup_to_sonnet() -> None:
    # V3: norm_lookup over the full 6,500-law corpus needs real reasoning to
    # pick candidates (search_norm → lookup_norm pipeline), so Sonnet beats Haiku.
    decision = route("Was steht in § 535 BGB?")
    assert decision.tier == ModelTier.SONNET
    assert decision.task_type == TaskType.NORM_LOOKUP


def test_routes_frist_to_haiku() -> None:
    decision = route("Wann läuft die Kündigungsfrist ab?")
    assert decision.tier == ModelTier.HAIKU
    assert decision.task_type == TaskType.FRIST_BERECHNEN


def test_routes_schriftsatz_to_sonnet() -> None:
    decision = route("Entwirf bitte einen Schriftsatz für die Mietminderung.")
    assert decision.tier == ModelTier.SONNET
    assert decision.task_type == TaskType.SCHRIFTSATZ_VORLAGE


def test_routes_gutachten_to_opus() -> None:
    decision = route("Bitte erstelle ein umfassendes Rechtsgutachten zum Eigenbedarf.")
    assert decision.tier == ModelTier.OPUS
    assert decision.task_type == TaskType.GUTACHTEN


def test_routes_wuerdigung_to_opus() -> None:
    decision = route("Wie ist die rechtliche Würdigung des Sachverhalts?")
    assert decision.tier == ModelTier.OPUS


def test_force_tier_overrides() -> None:
    decision = route("§ 535 BGB", force_tier=ModelTier.OPUS)
    assert decision.tier == ModelTier.OPUS
    assert decision.forced is True


def test_default_for_unknown_is_sonnet() -> None:
    decision = route("Hallo, kannst du mir helfen?")
    assert decision.tier == ModelTier.SONNET


def test_complexity_escalation_to_opus() -> None:
    long_query = (
        "Recherchiere bitte alle relevanten Urteile. "
        "Außerdem brauche ich eine Bewertung. Ferner darüber hinaus "
        + "noch viele Details über die Sache. " * 50
    )
    decision = route(long_query)
    # Recherche → Sonnet, aber lange + mehrere Konjunktionen → eskaliert auf Opus
    assert decision.tier == ModelTier.OPUS

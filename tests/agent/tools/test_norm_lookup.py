"""Tests for the rewritten norm_lookup tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kira.agent.legal_client import LegalSourceUnavailable
from kira.agent.tools import norm_lookup


def _success_response() -> dict:
    return {
        "gesetz": "BGB",
        "gesetz_titel": "Bürgerliches Gesetzbuch",
        "paragraph": "535",
        "absatz": None,
        "titel": "Inhalt und Hauptpflichten des Mietvertrags",
        "wortlaut": "(1) Durch den Mietvertrag wird der Vermieter…\n\n(2) Der Mieter ist verpflichtet…",
        "stand": "2026-05-11",
        "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
        "stand_warnung": None,
    }


def test_success_formats_full_norm_text() -> None:
    fake = MagicMock()
    fake.lookup_norm.return_value = _success_response()
    with patch.object(norm_lookup, "_client", fake):
        output = norm_lookup.run({"gesetz": "BGB", "paragraph": "535"})
    assert "BGB §535" in output
    assert "Inhalt und Hauptpflichten" in output
    assert "Durch den Mietvertrag" in output
    assert "Stand: 2026-05-11" in output
    assert "https://www.gesetze-im-internet.de/bgb/__535.html" in output


def test_unknown_gesetz_passes_message_through() -> None:
    fake = MagicMock()
    fake.lookup_norm.return_value = {
        "error": "unknown_gesetz",
        "message": "Gesetz 'XYZ' ist nicht im Korpus.",
        "gesetz": "XYZ",
    }
    with patch.object(norm_lookup, "_client", fake):
        output = norm_lookup.run({"gesetz": "XYZ", "paragraph": "1"})
    assert "unknown_gesetz" in output or "nicht im Korpus" in output


def test_paragraph_not_found_passes_message_through() -> None:
    fake = MagicMock()
    fake.lookup_norm.return_value = {
        "error": "paragraph_not_found",
        "message": "§ 99999 BGB ist nicht im Korpus. Nahe Treffer: …",
    }
    with patch.object(norm_lookup, "_client", fake):
        output = norm_lookup.run({"gesetz": "BGB", "paragraph": "99999"})
    assert "99999" in output
    assert "nicht im Korpus" in output


def test_unavailable_returns_german_error_string() -> None:
    fake = MagicMock()
    fake.lookup_norm.side_effect = LegalSourceUnavailable("network down")
    with patch.object(norm_lookup, "_client", fake):
        output = norm_lookup.run({"gesetz": "BGB", "paragraph": "535"})
    assert "Fehler" in output
    assert "Rechtsquelle" in output


def test_tool_is_registered() -> None:
    from kira.agent.tools._registry import REGISTRY
    assert "lookup_norm" in REGISTRY
    assert REGISTRY["lookup_norm"].run is norm_lookup.run

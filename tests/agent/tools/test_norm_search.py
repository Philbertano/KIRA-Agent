"""Tests for the rewritten norm_search tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kira.agent.legal_client import LegalSourceUnavailable
from kira.agent.tools import norm_search


def _hits_response() -> dict:
    return {
        "query": "Mietminderung",
        "hits": [
            {
                "gesetz": "BGB",
                "paragraph": "536",
                "titel": "Mietminderung bei Sach- und Rechtsmängeln",
                "wortlaut": (
                    "(1) Hat die Mietsache zur Zeit der Überlassung "
                    "an den Mieter einen Mangel…"
                ),
                "score": 0.689,
                "quelle_url": "https://www.gesetze-im-internet.de/bgb/__536.html",
                "stand": "2026-05-11",
            },
        ],
    }


def test_hits_format_with_score_titel_and_excerpt() -> None:
    fake = MagicMock()
    fake.search_norm.return_value = _hits_response()
    with patch.object(norm_search, "_client", fake):
        output = norm_search.run({"query": "Mietminderung"})
    assert "BGB §536" in output
    assert "Mietminderung bei Sach-" in output
    assert "0.69" in output
    assert "Mietsache" in output
    assert "bgb/__536.html" in output


def test_no_hits_returns_clear_message() -> None:
    fake = MagicMock()
    fake.search_norm.return_value = {"query": "xyz", "hits": []}
    with patch.object(norm_search, "_client", fake):
        output = norm_search.run({"query": "xyz"})
    assert "Keine Treffer" in output


def test_passes_gesetz_filter_through() -> None:
    fake = MagicMock()
    fake.search_norm.return_value = {"query": "x", "hits": []}
    with patch.object(norm_search, "_client", fake):
        norm_search.run({"query": "x", "gesetz_filter": ["BGB", "WoEigG"]})
    assert fake.search_norm.call_args.args[0]["gesetz_filter"] == ["BGB", "WoEigG"]


def test_passes_type_filter_through() -> None:
    fake = MagicMock()
    fake.search_norm.return_value = {"query": "x", "hits": []}
    with patch.object(norm_search, "_client", fake):
        norm_search.run({"query": "x", "type_filter": ["Verordnung"]})
    assert fake.search_norm.call_args.args[0]["type_filter"] == ["Verordnung"]


def test_unavailable_returns_german_error_string() -> None:
    fake = MagicMock()
    fake.search_norm.side_effect = LegalSourceUnavailable("network")
    with patch.object(norm_search, "_client", fake):
        output = norm_search.run({"query": "x"})
    assert "Fehler" in output
    assert "Rechtsquelle" in output


def test_validation_error_passes_through() -> None:
    fake = MagicMock()
    fake.search_norm.return_value = {
        "error": "validation_error",
        "message": "Field required: query",
    }
    with patch.object(norm_search, "_client", fake):
        output = norm_search.run({"query": ""})
    assert "Field required" in output or "validation_error" in output


def test_tool_is_registered() -> None:
    from kira.agent.tools._registry import REGISTRY
    assert "search_norm" in REGISTRY
    assert REGISTRY["search_norm"].run is norm_search.run

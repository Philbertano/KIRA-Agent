"""Tests for the JUNIOR_ASSOCIATE_DE system prompt invariants."""

from __future__ import annotations

from kira.agent.system_prompts import JUNIOR_ASSOCIATE_DE


def test_prompt_describes_full_bundesrecht_corpus() -> None:
    assert "Bundesgesetze" in JUNIOR_ASSOCIATE_DE
    assert "Rechtsverordnungen" in JUNIOR_ASSOCIATE_DE
    assert "gesetze-im-internet.de" in JUNIOR_ASSOCIATE_DE


def test_prompt_keeps_citation_rule() -> None:
    assert "lookup_norm" in JUNIOR_ASSOCIATE_DE
    # Citation must always flow through lookup_norm, never from search excerpts
    assert "bevor du" in JUNIOR_ASSOCIATE_DE.lower() or "vor jeder" in JUNIOR_ASSOCIATE_DE.lower()


def test_prompt_describes_search_to_lookup_workflow() -> None:
    assert "search_norm" in JUNIOR_ASSOCIATE_DE
    text = JUNIOR_ASSOCIATE_DE.lower()
    assert "search_norm" in text
    assert "kandidat" in text or "entdeck" in text or "wenn du" in text


def test_prompt_mentions_unknown_gesetz_fallback() -> None:
    assert "unknown_gesetz" in JUNIOR_ASSOCIATE_DE or "nicht im Korpus" in JUNIOR_ASSOCIATE_DE


def test_prompt_no_longer_references_norm_list_or_kira_ingest() -> None:
    assert "list_normen" not in JUNIOR_ASSOCIATE_DE
    assert "norm_list" not in JUNIOR_ASSOCIATE_DE
    assert "kira ingest" not in JUNIOR_ASSOCIATE_DE


def test_prompt_no_longer_lists_v0_three_law_corpus() -> None:
    text = JUNIOR_ASSOCIATE_DE.lower()
    assert "lokaler korpus" not in text
    assert "betrkv" not in text or "alle bundesgesetze" in text


def test_prompt_unchanged_safety_rules() -> None:
    assert "RDG" in JUNIOR_ASSOCIATE_DE
    assert "deutschem Recht" in JUNIOR_ASSOCIATE_DE
    assert "berechne_frist" in JUNIOR_ASSOCIATE_DE
    assert "fetch_urteil" in JUNIOR_ASSOCIATE_DE

import json
from pathlib import Path

import pytest

from kira.legal_sources.gesetze.corpus_format import GesetzKorpus
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import (
    LookupNormError,
    LookupNormErrorCode,
    LookupNormInput,
    LookupNormSuccess,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def bgb_korpus() -> GesetzKorpus:
    payload = json.loads((FIXTURES / "bgb_subset.json").read_text(encoding="utf-8"))
    return GesetzKorpus.model_validate(payload)


def test_returns_full_paragraph_when_no_absatz(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="535")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})

    assert isinstance(result, LookupNormSuccess)
    assert result.gesetz == "BGB"
    assert result.paragraph == "535"
    assert result.absatz is None
    assert "Durch den Mietvertrag" in result.wortlaut
    assert "Der Vermieter hat" in result.wortlaut  # both Absätze concatenated
    assert result.stand == "2026-05-08"
    assert result.quelle_url.endswith("__535.html")
    assert result.stand_warnung is None


def test_returns_specific_absatz_when_requested(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="535", absatz="2")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})

    assert isinstance(result, LookupNormSuccess)
    assert result.absatz == "2"
    assert "Der Vermieter hat die Mietsache" in result.wortlaut
    assert "Durch den Mietvertrag" not in result.wortlaut


def test_paragraph_with_letter_suffix_supported(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="535a")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})

    assert isinstance(result, LookupNormSuccess)
    assert result.paragraph == "535a"
    assert "Suffix-Test." in result.wortlaut


def test_unknown_gesetz_returns_error(bgb_korpus):
    inp = LookupNormInput(gesetz="ABC", paragraph="1")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.UNKNOWN_GESETZ
    assert result.gesetz == "ABC"


def test_paragraph_not_in_corpus_returns_error(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="1")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.PARAGRAPH_NOT_FOUND
    assert "§§ 535–540" in result.message  # range from fixture meta


def test_absatz_not_in_norm_returns_error(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="535", absatz="9")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.ABSATZ_NOT_FOUND
    assert result.absatz == "9"

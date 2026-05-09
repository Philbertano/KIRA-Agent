import json
from datetime import date
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
    assert "§§ 535–540" in result.message  # range from fixture meta  # noqa: RUF001


def test_absatz_not_in_norm_returns_error(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="535", absatz="9")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.ABSATZ_NOT_FOUND
    assert result.absatz == "9"


def test_stand_warning_set_when_corpus_older_than_30_days(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="535")
    # fixture stand is 2026-05-08; pretend today is 60 days later.
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus}, today=date(2026, 7, 7))
    assert isinstance(result, LookupNormSuccess)
    assert result.stand_warnung is not None
    assert "60 Tage alt" in result.stand_warnung


def test_stand_warning_absent_when_recent(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="535")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus}, today=date(2026, 5, 10))
    assert isinstance(result, LookupNormSuccess)
    assert result.stand_warnung is None


def test_unparseable_stand_emits_warning(bgb_korpus):
    bgb_korpus.meta.stand = "not-a-date"
    inp = LookupNormInput(gesetz="BGB", paragraph="535")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})
    assert isinstance(result, LookupNormSuccess)
    assert result.stand_warnung is not None
    assert "unleserlich" in result.stand_warnung


def test_error_to_agent_text_formats_correctly():
    """Test that LookupNormError.to_agent_text() properly formats error messages."""
    from kira.legal_sources.gesetze.schema import LookupNormError, LookupNormErrorCode

    err = LookupNormError(
        error=LookupNormErrorCode.UNKNOWN_GESETZ,
        message="Test error message",
    )
    text = err.to_agent_text()
    assert "FEHLER" in text
    assert "unknown_gesetz" in text
    assert "Test error message" in text


def test_norm_with_no_absaetze_returns_empty_text():
    """Test edge case where a norm exists but has no absaetze list."""
    from kira.legal_sources.gesetze.corpus_format import Norm
    from kira.legal_sources.gesetze.lookup_norm import _select_text

    norm_no_absatz = Norm(paragraph="535", titel="Test", absaetze=[], quelle_url="http://test")
    text, absatz_num = _select_text(norm_no_absatz, None)
    assert text == ""
    assert absatz_num is None

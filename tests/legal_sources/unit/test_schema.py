import pytest
from pydantic import ValidationError

from kira.legal_sources.gesetze.schema import LookupNormInput


def test_minimal_input_validates():
    payload = LookupNormInput.model_validate({"gesetz": "BGB", "paragraph": "535"})
    assert payload.gesetz == "bgb"  # normalized lowercase
    assert payload.paragraph == "535"
    assert payload.absatz is None


def test_paragraph_with_suffix_accepted():
    payload = LookupNormInput.model_validate({"gesetz": "bgb", "paragraph": "535a"})
    assert payload.paragraph == "535a"


def test_paragraph_must_not_be_empty():
    with pytest.raises(ValidationError):
        LookupNormInput.model_validate({"gesetz": "BGB", "paragraph": ""})


def test_paragraph_must_match_pattern():
    with pytest.raises(ValidationError):
        LookupNormInput.model_validate({"gesetz": "BGB", "paragraph": "Sec.5"})


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        LookupNormInput.model_validate(
            {"gesetz": "BGB", "paragraph": "535", "free_text": "client says..."}
        )


def test_absatz_optional_and_validates():
    payload = LookupNormInput.model_validate(
        {"gesetz": "BGB", "paragraph": "535", "absatz": "1"}
    )
    assert payload.absatz == "1"

import pytest
from pydantic import ValidationError

from kira.legal_sources.gesetze.schema import (
    LookupNormError,
    LookupNormErrorCode,
    LookupNormInput,
    LookupNormResult,
    LookupNormSuccess,
)


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


def test_success_serializes_with_all_fields():
    payload = LookupNormSuccess(
        gesetz="BGB",
        gesetz_titel="Bürgerliches Gesetzbuch",
        paragraph="535",
        absatz=None,
        titel="Inhalt und Hauptpflichten des Mietvertrags",
        wortlaut="Durch den Mietvertrag …",
        stand="2026-05-08",
        quelle_url="https://www.gesetze-im-internet.de/bgb/__535.html",
        stand_warnung=None,
    )
    dumped = payload.model_dump()
    assert dumped["paragraph"] == "535"
    assert dumped["stand_warnung"] is None


def test_error_carries_code_and_context():
    err = LookupNormError(
        error=LookupNormErrorCode.PARAGRAPH_NOT_FOUND,
        message="§ 1 BGB ist nicht im Korpus",
        gesetz="BGB",
        paragraph="1",
        absatz=None,
    )
    assert err.error == "paragraph_not_found"


def test_result_union_discriminator():
    # LookupNormResult is the union the tool returns.
    success = LookupNormSuccess(
        gesetz="BGB",
        gesetz_titel="Bürgerliches Gesetzbuch",
        paragraph="535",
        absatz=None,
        titel="X",
        wortlaut="Y",
        stand="2026-05-08",
        quelle_url="https://example.test",
        stand_warnung=None,
    )
    err = LookupNormError(
        error=LookupNormErrorCode.UNKNOWN_GESETZ,
        message="Unbekannt",
        gesetz="ABC",
        paragraph="1",
        absatz=None,
    )
    assert isinstance(success, LookupNormResult.__args__)  # type: ignore[attr-defined]
    assert isinstance(err, LookupNormResult.__args__)  # type: ignore[attr-defined]


from kira.legal_sources.gesetze.schema import (
    SearchNormError,
    SearchNormErrorCode,
    SearchNormHit,
    SearchNormInput,
    SearchNormResult,
    SearchNormSuccess,
)


def test_search_input_minimal_validates():
    inp = SearchNormInput.model_validate({"query": "Mietminderung Schimmel"})
    assert inp.query == "Mietminderung Schimmel"
    assert inp.k == 10  # default
    assert inp.gesetz_filter is None
    assert inp.type_filter is None


def test_search_input_k_capped():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SearchNormInput.model_validate({"query": "x", "k": 51})
    with pytest.raises(ValidationError):
        SearchNormInput.model_validate({"query": "x", "k": 0})


def test_search_input_query_must_be_nonempty():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SearchNormInput.model_validate({"query": ""})


def test_search_input_query_max_length():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SearchNormInput.model_validate({"query": "x" * 5001})


def test_search_input_extra_field_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SearchNormInput.model_validate({"query": "x", "rogue": True})


def test_search_input_filters_normalize_lowercase():
    inp = SearchNormInput.model_validate(
        {"query": "x", "gesetz_filter": ["BGB", "weg"]}
    )
    assert inp.gesetz_filter == ["bgb", "weg"]


def test_search_input_type_filter_validates_enum():
    from pydantic import ValidationError
    SearchNormInput.model_validate({"query": "x", "type_filter": ["Gesetz"]})
    with pytest.raises(ValidationError):
        SearchNormInput.model_validate({"query": "x", "type_filter": ["Sonstiges"]})


def test_search_success_serializes():
    s = SearchNormSuccess(
        query="x",
        hits=[
            SearchNormHit(
                gesetz="BGB",
                paragraph="535",
                absatz=None,
                titel="t",
                wortlaut="w",
                quelle_url="https://example.test",
                stand="2026-05-09",
                score=0.94,
            )
        ],
    )
    dumped = s.model_dump()
    assert dumped["hits"][0]["gesetz"] == "BGB"


def test_search_result_union():
    success = SearchNormSuccess(query="x", hits=[])
    err = SearchNormError(
        error=SearchNormErrorCode.EMBEDDING_UNAVAILABLE,
        message="bedrock down",
    )
    assert isinstance(success, SearchNormResult.__args__)  # type: ignore[attr-defined]
    assert isinstance(err, SearchNormResult.__args__)  # type: ignore[attr-defined]

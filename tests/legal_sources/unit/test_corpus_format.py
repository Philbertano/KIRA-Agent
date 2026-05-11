import pytest

from kira.legal_sources.gesetze.corpus_format import (
    Absatz,
    GesetzMeta,
    Norm,
    NormIndexEntry,
)


def test_norm_validates_minimal_payload():
    payload = {
        "gesetz": "BGB",
        "paragraph": "535",
        "titel": "Inhalt und Hauptpflichten des Mietvertrags",
        "absaetze": [
            {"nummer": "1", "text": "Durch den Mietvertrag ..."},
            {"nummer": "2", "text": "Der Mieter ..."},
        ],
        "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
    }
    n = Norm.model_validate(payload)
    assert n.gesetz == "BGB"
    assert n.paragraph == "535"
    assert n.absaetze[0].nummer == "1"


def test_norm_extra_fields_ignored():
    payload = {
        "gesetz": "BGB",
        "paragraph": "535",
        "titel": "x",
        "absaetze": [],
        "quelle_url": "https://example.test",
        "legacy_field": "ignore me",
    }
    n = Norm.model_validate(payload)  # no exception
    assert n.titel == "x"


def test_gesetz_meta_validates_with_paragraph_index():
    payload = {
        "abkuerzung": "BGB",
        "titel": "Bürgerliches Gesetzbuch",
        "type": "Gesetz",
        "stand": "2026-05-10",
        "quelle": "gesetze-im-internet.de",
        "quelle_url": "https://www.gesetze-im-internet.de/bgb",
        "upstream_xml_zip_url": "https://www.gesetze-im-internet.de/bgb/xml.zip",
        "paragraphen": {
            "535": {
                "titel": "Inhalt und Hauptpflichten des Mietvertrags",
                "key": "gesetze/bgb/535.json",
                "content_sha256": "abc",
            }
        },
    }
    m = GesetzMeta.model_validate(payload)
    assert m.abkuerzung == "BGB"
    assert "535" in m.paragraphen
    entry = m.paragraphen["535"]
    assert isinstance(entry, NormIndexEntry)
    assert entry.key == "gesetze/bgb/535.json"


def test_norm_index_entry_extra_fields_ignored():
    entry = NormIndexEntry.model_validate(
        {
            "titel": "x",
            "key": "gesetze/bgb/535.json",
            "content_sha256": "abc",
            "future": "field",
        }
    )
    assert entry.titel == "x"


def test_absatz_round_trip():
    a = Absatz(nummer="1", text="hello")
    assert a.model_dump() == {"nummer": "1", "text": "hello"}


def test_gesetz_meta_rejects_unknown_type():
    payload = {
        "abkuerzung": "X",
        "titel": "x",
        "type": "Ratgeber",  # not in {"Gesetz","Verordnung"}
        "stand": "2026-05-10",
        "quelle": "x",
        "quelle_url": "https://example.test",
        "upstream_xml_zip_url": "https://example.test/xml.zip",
        "paragraphen": {},
    }
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        GesetzMeta.model_validate(payload)

from datetime import date

from kira.legal_sources.gesetze.corpus_format import (
    Absatz,
    GesetzMeta,
    Norm,
)
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import (
    LookupNormError,
    LookupNormErrorCode,
    LookupNormInput,
    LookupNormSuccess,
)


def _bgb_meta() -> GesetzMeta:
    return GesetzMeta.model_validate(
        {
            "abkuerzung": "BGB",
            "titel": "Bürgerliches Gesetzbuch",
            "type": "Gesetz",
            "stand": "2026-05-08",
            "quelle": "gesetze-im-internet.de",
            "quelle_url": "https://www.gesetze-im-internet.de/bgb",
            "upstream_xml_zip_url": "https://www.gesetze-im-internet.de/bgb/xml.zip",
            "paragraphen": {
                "535": {
                    "titel": "Inhalt und Hauptpflichten des Mietvertrags",
                    "key": "gesetze/bgb/535.json",
                    "content_sha256": "abc",
                },
                "536": {
                    "titel": "Mietminderung bei Sach- und Rechtsmängeln",
                    "key": "gesetze/bgb/536.json",
                    "content_sha256": "def",
                },
                "535a": {
                    "titel": "Suffix-Norm",
                    "key": "gesetze/bgb/535a.json",
                    "content_sha256": "ghi",
                },
            },
        }
    )


def _bgb_535() -> Norm:
    return Norm(
        gesetz="BGB",
        paragraph="535",
        titel="Inhalt und Hauptpflichten des Mietvertrags",
        absaetze=[
            Absatz(nummer="1", text="Durch den Mietvertrag wird der Vermieter verpflichtet, ..."),
            Absatz(nummer="2", text="Der Mieter ist verpflichtet, ..."),
        ],
        quelle_url="https://www.gesetze-im-internet.de/bgb/__535.html",
    )


def _make_loaders(meta: GesetzMeta | None, norms: dict[str, Norm] | None = None):
    norms = norms or {}

    def load_meta(abk: str) -> GesetzMeta | None:
        return meta if abk == "bgb" and meta is not None else None

    def load_norm(meta_key: str, norm_key: str) -> Norm | None:
        return norms.get(norm_key)

    return load_meta, load_norm


def test_returns_full_paragraph_when_no_absatz():
    meta = _bgb_meta()
    load_meta, load_norm = _make_loaders(meta, {"gesetze/bgb/535.json": _bgb_535()})
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="535"),
        load_meta=load_meta,
        load_norm=load_norm,
    )
    assert isinstance(result, LookupNormSuccess)
    assert "Durch den Mietvertrag" in result.wortlaut
    assert "Der Mieter" in result.wortlaut
    assert result.absatz is None
    assert result.stand == "2026-05-08"


def test_returns_specific_absatz_when_requested():
    meta = _bgb_meta()
    load_meta, load_norm = _make_loaders(meta, {"gesetze/bgb/535.json": _bgb_535()})
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="535", absatz="2"),
        load_meta=load_meta,
        load_norm=load_norm,
    )
    assert isinstance(result, LookupNormSuccess)
    assert result.absatz == "2"
    assert "Der Mieter" in result.wortlaut
    assert "Durch den Mietvertrag" not in result.wortlaut


def test_unknown_gesetz_returns_error():
    load_meta, load_norm = _make_loaders(None)
    result = lookup_norm(
        LookupNormInput(gesetz="ABC", paragraph="1"),
        load_meta=load_meta,
        load_norm=load_norm,
    )
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.UNKNOWN_GESETZ


def test_paragraph_not_found_lists_near_misses():
    meta = _bgb_meta()
    load_meta, load_norm = _make_loaders(meta)
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="537"),  # not present
        load_meta=load_meta,
        load_norm=load_norm,
    )
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.PARAGRAPH_NOT_FOUND
    # Near-miss list includes existing close paragraphs
    assert "535" in result.message or "536" in result.message


def test_absatz_not_found_returns_error():
    meta = _bgb_meta()
    load_meta, load_norm = _make_loaders(meta, {"gesetze/bgb/535.json": _bgb_535()})
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="535", absatz="9"),
        load_meta=load_meta,
        load_norm=load_norm,
    )
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.ABSATZ_NOT_FOUND


def test_norm_load_returns_none_treated_as_corpus_unavailable():
    """If meta says §535 exists but the underlying file can't be loaded."""
    meta = _bgb_meta()
    load_meta, load_norm = _make_loaders(meta, {})  # empty norms dict
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="535"),
        load_meta=load_meta,
        load_norm=load_norm,
    )
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.CORPUS_UNAVAILABLE


def test_stand_warning_when_meta_old():
    meta = _bgb_meta()
    load_meta, load_norm = _make_loaders(meta, {"gesetze/bgb/535.json": _bgb_535()})
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="535"),
        load_meta=load_meta,
        load_norm=load_norm,
        today=date(2026, 7, 7),  # 60 days after 2026-05-08
    )
    assert isinstance(result, LookupNormSuccess)
    assert result.stand_warnung is not None
    assert "60 Tage" in result.stand_warnung


def test_paragraph_with_letter_suffix():
    meta = _bgb_meta()
    norm_535a = Norm(
        gesetz="BGB",
        paragraph="535a",
        titel="Suffix-Norm",
        absaetze=[Absatz(nummer="1", text="Suffix-Test.")],
        quelle_url="https://www.gesetze-im-internet.de/bgb/__535a.html",
    )
    load_meta, load_norm = _make_loaders(meta, {"gesetze/bgb/535a.json": norm_535a})
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="535a"),
        load_meta=load_meta,
        load_norm=load_norm,
    )
    assert isinstance(result, LookupNormSuccess)
    assert result.paragraph == "535a"


def test_select_text_with_no_absaetze_and_none_requested():
    """_select_text with empty absaetze list and absatz=None returns ('', None)."""
    from kira.legal_sources.gesetze.lookup_norm import _select_text
    norm = Norm(
        gesetz="BGB",
        paragraph="999",
        titel="Empty Norm",
        absaetze=[],
        quelle_url="https://example.test",
    )
    wortlaut, used_absatz = _select_text(norm, None)
    assert wortlaut == ""
    assert used_absatz is None


def test_to_sort_key_with_malformed_paragraph():
    """_to_sort_key returns 0.0 for malformed paragraph strings."""
    from kira.legal_sources.gesetze.lookup_norm import _to_sort_key
    assert _to_sort_key("abc") == 0.0
    assert _to_sort_key("§535") == 0.0
    assert _to_sort_key("") == 0.0

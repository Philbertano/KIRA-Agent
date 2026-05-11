from pathlib import Path

import httpx
import respx

from kira.legal_sources._common.toc import (
    TocEntry,
    fetch_toc,
    is_citable,
    parse_toc,
    slug_for,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "captured"


def test_parse_toc_returns_entries_for_each_item():
    raw = (FIXTURES / "gii_toc_subset.xml").read_bytes()
    entries = parse_toc(raw)
    assert len(entries) == 6
    titles = [e.title for e in entries]
    assert "Bürgerliches Gesetzbuch" in titles


def test_slug_extracted_from_xml_zip_url():
    assert slug_for("https://www.gesetze-im-internet.de/bgb/xml.zip") == "bgb"
    assert slug_for("https://www.gesetze-im-internet.de/woeigg/xml.zip") == "woeigg"


def test_is_citable_accepts_real_laws():
    assert is_citable(TocEntry(
        title="Bürgerliches Gesetzbuch",
        link="https://www.gesetze-im-internet.de/bgb/xml.zip",
    ))
    assert is_citable(TocEntry(
        title="Wohnungseigentumsgesetz",
        link="https://www.gesetze-im-internet.de/woeigg/xml.zip",
    ))


def test_is_citable_rejects_bekanntmachung_by_slug():
    assert not is_citable(TocEntry(
        title="Bekanntmachung über Beispielverordnung",
        link="https://www.gesetze-im-internet.de/beispielbek/xml.zip",
    ))


def test_is_citable_rejects_geschaeftsordnung_by_slug():
    assert not is_citable(TocEntry(
        title="Geschäftsordnung des Rats",
        link="https://www.gesetze-im-internet.de/ratsgo/xml.zip",
    ))


def test_is_citable_rejects_repealed_by_title():
    assert not is_citable(TocEntry(
        title="Bundesentschädigungsgesetz (aufgehoben)",
        link="https://www.gesetze-im-internet.de/beg/xml.zip",
    ))


def test_fetch_toc_via_proxy_url(monkeypatch):
    monkeypatch.setenv(
        "LEGAL_INGEST_PROXY_URL",
        "https://kira-legaltext-gii-proxy.example.workers.dev",
    )
    raw = (FIXTURES / "gii_toc_subset.xml").read_bytes()
    with respx.mock(assert_all_called=True) as mock:
        mock.get(
            "https://kira-legaltext-gii-proxy.example.workers.dev/",
            params={"url": "https://www.gesetze-im-internet.de/gii-toc.xml"},
        ).mock(return_value=httpx.Response(200, content=raw))
        with httpx.Client() as client:
            entries = fetch_toc(client)
    assert len(entries) == 6


def test_fetch_toc_directly_when_no_proxy(monkeypatch):
    monkeypatch.delenv("LEGAL_INGEST_PROXY_URL", raising=False)
    raw = (FIXTURES / "gii_toc_subset.xml").read_bytes()
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://www.gesetze-im-internet.de/gii-toc.xml").mock(
            return_value=httpx.Response(200, content=raw)
        )
        with httpx.Client() as client:
            entries = fetch_toc(client)
    assert len(entries) == 6

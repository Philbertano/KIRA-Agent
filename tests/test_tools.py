"""Tests für Tools (norm_lookup, norm_search, norm_list, frist, urteil_fetch).

Hinweis: urteil_fetch hat externe HTTP-Calls; wir testen nur die
Whitelist-Logik, nicht den tatsächlichen Abruf.
"""

from __future__ import annotations

from datetime import date

from kira.agent.tools import frist, norm_list, norm_lookup, norm_search, urteil_fetch
from kira.knowledge import loader


# --- norm_lookup ---


def test_norm_lookup_bgb_535() -> None:
    output = norm_lookup.run({"paragraph": "535"})
    assert "535" in output
    assert "Hauptpflichten" in output
    assert "vertragsgemäßen Gebrauch" in output
    assert "Stand:" in output  # Stand wird ausgegeben


def test_norm_lookup_handles_paragraph_format() -> None:
    output = norm_lookup.run({"paragraph": "§ 536 BGB"})
    assert "536" in output
    assert "Mangel" in output


def test_norm_lookup_with_letter_suffix() -> None:
    output = norm_lookup.run({"paragraph": "536a"})
    assert "536a" in output
    assert "Schadens" in output or "Aufwendungs" in output


def test_norm_lookup_unknown_paragraph() -> None:
    output = norm_lookup.run({"paragraph": "999"})
    assert "nicht enthalten" in output
    # Soll keine erfundenen Inhalte liefern, aber verfügbare auflisten
    assert "Verfügbar" in output
    assert "ingest" in output  # Hinweis auf ingest-Befehl


def test_norm_lookup_other_gesetz_betrkv() -> None:
    output = norm_lookup.run({"paragraph": "1", "gesetz": "BetrKV"})
    assert "BetrKV" in output
    assert "Betriebskosten" in output


def test_norm_lookup_other_gesetz_heizkostenv() -> None:
    output = norm_lookup.run({"paragraph": "9", "gesetz": "HeizkostenV"})
    assert "HeizkostenV" in output
    assert "Kürzung" in output or "kürzen" in output


def test_norm_lookup_unknown_gesetz() -> None:
    output = norm_lookup.run({"paragraph": "1", "gesetz": "XYZ"})
    assert "FEHLER" in output
    assert "BGB" in output  # zeigt Verfügbares an


# --- search_norm ---


def test_search_norm_finds_schimmel_via_mangel() -> None:
    output = norm_search.run({"query": "Mangel Mietsache"})
    # Sollte mehrere §§ aus dem Mietrecht-Mangelbereich finden
    assert "536" in output
    assert "lookup_norm" in output  # gibt nächsten Schritt an


def test_search_norm_finds_eigenbedarf() -> None:
    output = norm_search.run({"query": "Eigenbedarf"})
    assert "573" in output


def test_search_norm_filtered_to_betrkv() -> None:
    output = norm_search.run({"query": "Heizung Brennstoff", "gesetz": "BetrKV"})
    assert "BetrKV" in output


def test_search_norm_no_hits() -> None:
    output = norm_search.run({"query": "InternationalesPrivatrechtKollisionsnorm"})
    assert "Keine Treffer" in output


def test_search_norm_empty_query() -> None:
    output = norm_search.run({"query": ""})
    assert "FEHLER" in output


# --- list_normen ---


def test_list_normen_overview() -> None:
    output = norm_list.run({})
    assert "BGB" in output
    assert "BetrKV" in output
    assert "HeizkostenV" in output


def test_list_normen_bgb_inhaltsverzeichnis() -> None:
    output = norm_list.run({"gesetz": "BGB"})
    assert "535" in output
    assert "573" in output
    # Abschnittsüberschriften sollten erscheinen
    assert "Mietverhältnisse" in output or "Verjährung" in output


def test_list_normen_unknown_gesetz() -> None:
    output = norm_list.run({"gesetz": "ABC"})
    assert "FEHLER" in output


# --- Stand-Warnung ---


def test_stand_warnung_fresh() -> None:
    today = date(2026, 5, 1)
    fresh = date(2026, 4, 1)
    assert loader.stand_warnung(fresh, today=today) is None


def test_stand_warnung_old() -> None:
    today = date(2026, 5, 1)
    old = date(2025, 6, 1)  # ~11 Monate alt
    warn = loader.stand_warnung(old, today=today)
    assert warn is not None
    assert "kira ingest" in warn


# --- Fristen ---


def test_frist_kuendigung_mieter() -> None:
    output = frist.run(
        {"typ": "ordentliche_kuendigung_mieter", "startdatum": "2026-05-02"}
    )
    assert "ordentliche_kuendigung_mieter" in output
    assert "§ 573c" in output
    assert "vertragsende" in output


def test_frist_kuendigung_vermieter_lange_mietdauer() -> None:
    output = frist.run(
        {
            "typ": "ordentliche_kuendigung_vermieter",
            "startdatum": "2026-05-02",
            "ueberlassen_seit": "2014-04-01",
        }
    )
    # 12 Jahre Mietdauer → 9 Monate Kündigungsfrist
    assert "kuendigungsfrist_monate: 9" in output


def test_frist_verjaehrung() -> None:
    output = frist.run({"typ": "verjaehrung_regulaer", "startdatum": "2024-06-15"})
    # Anspruch entstanden 2024 → Frist beginnt 31.12.2024 → endet 31.12.2027
    assert "2027-12-31" in output


def test_frist_unknown_type() -> None:
    output = frist.run({"typ": "nicht_existent"})
    assert "FEHLER" in output


# --- Urteil-Fetch (Whitelist) ---


def test_urteil_fetch_blocks_non_whitelisted_domain() -> None:
    output = urteil_fetch.run_fetch_urteil({"url": "https://example.com/urteil/123"})
    assert "FEHLER" in output
    assert "Whitelist" in output


def test_urteil_fetch_blocks_foreign_jurisdiction() -> None:
    output = urteil_fetch.run_fetch_urteil({"url": "https://www.ris.bka.gv.at/some-urteil"})
    assert "FEHLER" in output


def test_urteil_fetch_whitelist_accepts() -> None:
    from kira.agent.tools.urteil_fetch import _is_allowed

    assert _is_allowed("https://www.rechtsprechung-im-internet.de/jportal/...")
    assert _is_allowed("https://openjur.de/u/12345.html")
    assert _is_allowed("https://dejure.org/dienste/vernetzung/...")
    assert not _is_allowed("https://example.com/")
    assert not _is_allowed("https://www.ris.bka.gv.at/")

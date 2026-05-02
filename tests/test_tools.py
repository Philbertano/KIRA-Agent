"""Tests für Tools (norm_lookup, frist, urteil_fetch).

Hinweis: urteil_fetch hat externe HTTP-Calls; wir testen nur die
Whitelist-Logik, nicht den tatsächlichen Abruf.
"""

from __future__ import annotations

from datetime import date

from kira.agent.tools import frist, norm_lookup, urteil_fetch


def test_norm_lookup_returns_text() -> None:
    output = norm_lookup.run({"paragraph": "535"})
    assert "535" in output
    assert "Hauptpflichten" in output
    assert "vertragsgemäßen Gebrauch" in output


def test_norm_lookup_handles_paragraph_format() -> None:
    output = norm_lookup.run({"paragraph": "§ 536 BGB"})
    assert "536" in output
    assert "Mietminderung" in output or "Mangel" in output


def test_norm_lookup_unknown_paragraph() -> None:
    output = norm_lookup.run({"paragraph": "999"})
    assert "nicht enthalten" in output
    # Soll keine erfundenen Inhalte liefern
    assert "Verfügbar" in output


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


def test_urteil_fetch_blocks_non_whitelisted_domain() -> None:
    output = urteil_fetch.run_fetch_urteil({"url": "https://example.com/urteil/123"})
    assert "FEHLER" in output
    assert "Whitelist" in output


def test_urteil_fetch_blocks_foreign_jurisdiction() -> None:
    # Österreichische RIS soll geblockt werden
    output = urteil_fetch.run_fetch_urteil({"url": "https://www.ris.bka.gv.at/some-urteil"})
    assert "FEHLER" in output


def test_urteil_fetch_accepts_whitelisted() -> None:
    """Whitelist-Logik isoliert prüfen, ohne tatsächlichen HTTP-Call."""
    from kira.agent.tools.urteil_fetch import _is_allowed

    assert _is_allowed("https://www.rechtsprechung-im-internet.de/jportal/...")
    assert _is_allowed("https://openjur.de/u/12345.html")
    assert _is_allowed("https://dejure.org/dienste/vernetzung/...")
    assert not _is_allowed("https://example.com/")
    assert not _is_allowed("https://www.ris.bka.gv.at/")


def test_dispatch_today_is_known() -> None:
    """Sanity: dass datetime-Imports klappen."""
    assert isinstance(date.today(), date)

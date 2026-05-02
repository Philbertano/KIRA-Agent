"""Tests für den Pseudonymizer.

Schwerpunkt: Leakage-Tests. Wenn diese Tests fehlschlagen, gehen
Klardaten potenziell an die Cloud — das ist kritisch.
"""

from __future__ import annotations

import pytest

from kira.pseudonymizer import (
    EntityKind,
    Gender,
    LeakageError,
    Party,
    Pseudonymizer,
    Role,
    check_for_leaks,
)
from kira.pseudonymizer.leakage_check import assert_no_leaks


@pytest.fixture
def parties() -> list[Party]:
    return [
        Party(
            real_name="Klaus Müller",
            role=Role.MIETER,
            gender=Gender.MAENNLICH,
            aliases=["Herr Müller", "K. Müller"],
        ),
        Party(
            real_name="ABC Immobilien GmbH",
            role=Role.VERMIETER,
            kind=EntityKind.JURISTISCH,
            aliases=["ABC GmbH"],
        ),
    ]


def test_basic_replacement(parties: list[Party]) -> None:
    text = "Herr Müller mietet von der ABC Immobilien GmbH eine Wohnung."
    result = Pseudonymizer(parties).process(text)
    assert "Müller" not in result.text
    assert "ABC" not in result.text
    assert "[MIETER_1:m,nat]" in result.text
    assert "[VERMIETER_1:u,jur]" in result.text


def test_alias_replacement(parties: list[Party]) -> None:
    text = "K. Müller hat heute mit der ABC GmbH telefoniert."
    result = Pseudonymizer(parties).process(text)
    assert "Müller" not in result.text
    assert "ABC GmbH" not in result.text


def test_umlaut_variants(parties: list[Party]) -> None:
    text = "Herr Mueller meldet einen Mangel."
    result = Pseudonymizer(parties).process(text)
    assert "Mueller" not in result.text
    assert "[MIETER_1:m,nat]" in result.text


def test_iban_replacement(parties: list[Party]) -> None:
    text = "Herr Müller überweist von DE89 3704 0044 0532 0130 00."
    result = Pseudonymizer(parties).process(text)
    assert "DE89" not in result.text
    assert "[IBAN_1]" in result.text


def test_email_replacement(parties: list[Party]) -> None:
    text = "Erreichbar unter klaus.mueller@example.de."
    result = Pseudonymizer(parties).process(text)
    assert "@example.de" not in result.text
    assert "[EMAIL_1]" in result.text


def test_phone_replacement(parties: list[Party]) -> None:
    text = "Telefon: +49 30 12345678."
    result = Pseudonymizer(parties).process(text)
    assert "+49" not in result.text
    assert "[TEL_1]" in result.text


def test_address_replacement(parties: list[Party]) -> None:
    text = "Wohnt in der Berliner Straße 12, 10115 Berlin."
    result = Pseudonymizer(parties).process(text)
    assert "Berliner Straße 12" not in result.text
    # PLZ + Stadt
    assert "10115 Berlin" not in result.text


def test_repersonalization(parties: list[Party]) -> None:
    text = "Klaus Müller mietet von der ABC Immobilien GmbH."
    pseudo = Pseudonymizer(parties)
    result = pseudo.process(text)

    llm_output = (
        "[MIETER_1:m,nat] hat einen Mietminderungsanspruch gegen "
        "[VERMIETER_1:u,jur] gemäß § 536 BGB."
    )
    repers = result.repersonalize(llm_output)
    assert "Klaus Müller" in repers
    assert "ABC Immobilien GmbH" in repers
    assert "[MIETER_1" not in repers


def test_leakage_check_clean(parties: list[Party]) -> None:
    clean = "[MIETER_1:m,nat] mietet von [VERMIETER_1:u,jur]."
    report = check_for_leaks(clean, parties)
    assert not report


def test_leakage_check_dirty(parties: list[Party]) -> None:
    dirty = "Herr Müller mietet von [VERMIETER_1:u,jur]."
    report = check_for_leaks(dirty, parties)
    assert report
    labels = {label for label, _ in report.leaks}
    assert "PARTEI_NAME" in labels


def test_assert_no_leaks_raises(parties: list[Party]) -> None:
    with pytest.raises(LeakageError):
        assert_no_leaks("Klaus Müller hat angerufen.", parties)


def test_role_indexing_multiple_mieter() -> None:
    parties = [
        Party("Klaus Müller", Role.MIETER, gender=Gender.MAENNLICH),
        Party("Sabine Müller", Role.MITMIETER, gender=Gender.WEIBLICH),
    ]
    text = "Klaus Müller und Sabine Müller wohnen zusammen."
    result = Pseudonymizer(parties).process(text)
    assert "[MIETER_1:m,nat]" in result.text
    assert "[MITMIETER_1:w,nat]" in result.text


def test_age_band_preserved() -> None:
    parties = [
        Party("Klaus Müller", Role.MIETER, gender=Gender.MAENNLICH, age_band="60-69"),
    ]
    result = Pseudonymizer(parties).process("Klaus Müller ist 64.")
    # Age-band steht im Platzhalter — Sozialklausel-relevant
    assert "~60-69" in result.text


def test_juristische_person_kennzeichnung() -> None:
    parties = [
        Party("ABC GmbH", Role.VERMIETER, kind=EntityKind.JURISTISCH),
    ]
    result = Pseudonymizer(parties).process("Die ABC GmbH ist Vermieterin.")
    assert ",jur" in result.text


def test_keine_falschen_treffer_bei_kurzen_namen() -> None:
    """Ein 2-Buchstaben-Alias soll nicht halb in Wörtern matchen."""
    parties = [
        Party("Albert Adler", Role.MIETER, aliases=["AA"]),
    ]
    # 'AA' kommt in 'Mahnschreiben' nicht als eigenes Wort vor → kein Match
    text = "Das Mahnschreiben ging am Mittwoch raus."
    result = Pseudonymizer(parties).process(text)
    assert "Mahnschreiben" in result.text

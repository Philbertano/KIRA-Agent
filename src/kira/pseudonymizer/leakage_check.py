"""Sicherheitsnetz: prüft pseudonymisierten Text vor dem LLM-Call auf PII-Leaks.

Wenn hier etwas durchrutscht, wird der Call hart abgebrochen — lieber Fehler
als Klardaten in der Cloud. Pflicht-Lauf vor jedem messages.create().
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from kira.pseudonymizer.pipeline import (
    _EMAIL_RE,
    _IBAN_RE,
    _PHONE_RE,
    _PLZ_CITY_RE,
    _STREET_RE,
    Party,
)


class LeakageError(RuntimeError):
    """Pseudonymisierung hat versagt — Klardaten würden an LLM gesendet."""


@dataclass
class LeakReport:
    leaks: list[tuple[str, str]]  # (label, matched_text)

    def __bool__(self) -> bool:
        return bool(self.leaks)


_LEAK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("IBAN", _IBAN_RE),
    ("EMAIL", _EMAIL_RE),
    ("TEL", _PHONE_RE),
    ("ADRESSE", _STREET_RE),
    ("ORT", _PLZ_CITY_RE),
]


def check_for_leaks(text: str, parties: list[Party]) -> LeakReport:
    """Scannt pseudonymisierten Text auf bekannte PII-Muster und Klarnamen.

    Returnt einen Report. Aufrufer entscheidet, ob Hard-Fail oder Logging.
    """
    leaks: list[tuple[str, str]] = []

    # 1. Generische PII-Muster
    for label, pattern in _LEAK_PATTERNS:
        for match in pattern.finditer(text):
            leaks.append((label, match.group(0)))

    # 2. Klarnamen aus Parteien
    for party in parties:
        for variant in party.all_name_variants():
            if len(variant) < 3:
                continue  # zu kurz, würde False Positives produzieren
            pattern = re.compile(rf"\b{re.escape(variant)}\b", flags=re.IGNORECASE)
            if pattern.search(text):
                leaks.append(("PARTEI_NAME", variant))

    return LeakReport(leaks=leaks)


def assert_no_leaks(text: str, parties: list[Party]) -> None:
    """Hard-Fail-Variante. Vor jedem LLM-Call aufrufen."""
    report = check_for_leaks(text, parties)
    if report:
        details = ", ".join(f"{label}={value!r}" for label, value in report.leaks[:5])
        raise LeakageError(
            f"Pseudonymisierung unvollständig — Klardaten im Text: {details}. "
            f"LLM-Call abgebrochen."
        )

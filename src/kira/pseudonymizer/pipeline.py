"""Pseudonymisierungs-Pipeline.

Designprinzipien:

1. Strukturierte Eingabe schlägt NER. Der Anwalt benennt die Parteien explizit
   (Mandant, Gegner, weitere). NER ist nur Sicherheitsnetz für Freitext.

2. Strukturierte Platzhalter. Statt "[PERSON_1]" verwenden wir
   "[MIETER_1:m,nat]" — Geschlecht, Person-Typ und Rolle bleiben erhalten,
   weil der Agent sie für die rechtliche Würdigung braucht.

3. Bidirektionales Mapping pro Mandat. Wird verschlüsselt persistiert
   (siehe mapping_store.py).

4. Was NICHT pseudonymisiert wird:
   - Daten, Fristen, Beträge, Quadratmeter (rechtlich relevant!)
   - §§, Aktenzeichen, Gerichte (öffentlich)
   - Fachbegriffe und Mängelbeschreibungen
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class Role(str, Enum):
    MIETER = "MIETER"
    VERMIETER = "VERMIETER"
    MITMIETER = "MITMIETER"
    BUERGE = "BUERGE"
    HAUSVERWALTUNG = "HAUSVERWALTUNG"
    MAKLER = "MAKLER"
    NACHBAR = "NACHBAR"
    ZEUGE = "ZEUGE"
    SONSTIGE = "SONSTIGE"


class Gender(str, Enum):
    MAENNLICH = "m"
    WEIBLICH = "w"
    DIVERS = "d"
    UNBEKANNT = "u"


class EntityKind(str, Enum):
    NATUERLICH = "nat"
    JURISTISCH = "jur"


@dataclass
class Party:
    """Eine Partei im Mandat. Vom Anwalt strukturiert eingegeben."""

    real_name: str
    role: Role
    kind: EntityKind = EntityKind.NATUERLICH
    gender: Gender = Gender.UNBEKANNT
    aliases: list[str] = field(default_factory=list)
    age_band: str | None = None  # z.B. "60-69" für § 574 BGB Sozialklausel

    def all_name_variants(self) -> list[str]:
        """Alle Schreibweisen, unter denen diese Partei im Freitext auftauchen kann."""
        variants: set[str] = {self.real_name, *self.aliases}

        # Nachname allein, falls "Vorname Nachname"
        parts = self.real_name.split()
        if len(parts) >= 2 and self.kind == EntityKind.NATUERLICH:
            variants.add(parts[-1])  # Nachname
            # Initial + Nachname: "K. Müller"
            variants.add(f"{parts[0][0]}. {parts[-1]}")

        # Umlaut-Varianten
        for v in list(variants):
            ascii_v = (
                v.replace("ä", "ae")
                .replace("ö", "oe")
                .replace("ü", "ue")
                .replace("Ä", "Ae")
                .replace("Ö", "Oe")
                .replace("Ü", "Ue")
                .replace("ß", "ss")
            )
            if ascii_v != v:
                variants.add(ascii_v)

        return sorted(variants, key=len, reverse=True)


@dataclass
class PseudonymizedText:
    """Ergebnis der Pseudonymisierung."""

    text: str
    mapping: dict[str, str]  # placeholder -> real value
    parties: list[Party]

    def repersonalize(self, llm_output: str) -> str:
        """Ersetzt Platzhalter im LLM-Output zurück durch Klarnamen.

        Wird nur lokal beim Anwalt aufgerufen, *nachdem* die Antwort vom
        LLM zurück ist. Klarnamen verlassen das System nie.
        """
        result = llm_output
        # Längste Platzhalter zuerst, damit [MIETER_10] nicht von [MIETER_1] ersetzt wird
        for placeholder in sorted(self.mapping.keys(), key=len, reverse=True):
            result = result.replace(placeholder, self.mapping[placeholder])
        return result


# --- PII-Patterns (Sicherheitsnetz) ---
# Konservativ gehalten: lieber etwas zu viel ersetzen als zu wenig.

_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}\s?(?:\d{4}\s?){4}\d{0,4}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?:\+49|0049|0)\s?(?:\(0\)\s?)?\d{2,5}[\s/-]?\d{3,12}"
)
# Deutsche PLZ + Stadt: "10115 Berlin"
_PLZ_CITY_RE = re.compile(r"\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß-]+(?:\s+[A-ZÄÖÜ][a-zäöüß-]+)?\b")
# Straße + Hausnummer: "Berliner Straße 12", "Hauptstr. 5a"
_STREET_RE = re.compile(
    r"\b[A-ZÄÖÜ][a-zäöüß]+(?:[- ][A-ZÄÖÜa-zäöüß]+)*"
    r"\s+(?:Str\.|Straße|Strasse|Weg|Allee|Platz|Gasse|Ring)\s+\d+[a-zA-Z]?\b"
)


class Pseudonymizer:
    """Wandelt Freitext-Sachverhalt in pseudonymisierten Text um.

    Verwendung::

        pseudo = Pseudonymizer(parties=[
            Party("Klaus Müller", Role.MIETER, gender=Gender.MAENNLICH),
            Party("ABC Immobilien GmbH", Role.VERMIETER, kind=EntityKind.JURISTISCH),
        ])
        result = pseudo.process(sachverhalt_text)
        # result.text geht an LLM, result.mapping bleibt lokal.
    """

    def __init__(self, parties: Iterable[Party]):
        self._parties = list(parties)
        self._role_counters: dict[Role, int] = {}
        self._mapping: dict[str, str] = {}
        self._reverse: dict[str, str] = {}  # real -> placeholder (für Coreference)
        self._build_party_placeholders()

    def _build_party_placeholders(self) -> None:
        for party in self._parties:
            self._role_counters.setdefault(party.role, 0)
            self._role_counters[party.role] += 1
            idx = self._role_counters[party.role]

            attrs = [party.gender.value, party.kind.value]
            if party.age_band:
                attrs.append(f"~{party.age_band}")
            attr_str = ",".join(attrs)
            placeholder = f"[{party.role.value}_{idx}:{attr_str}]"

            self._mapping[placeholder] = party.real_name
            for variant in party.all_name_variants():
                self._reverse[variant] = placeholder

    def process(self, text: str) -> PseudonymizedText:
        # 1. PII-Muster ZUERST (E-Mail, IBAN, Tel, Adresse).
        #    Sonst würde "klaus.mueller@example.de" durch Namen-Replacement
        #    in der Mitte verstümmelt.
        result = self._replace_generic(text)

        # 2. Bekannte Parteien (längste Strings zuerst)
        ordered_variants = sorted(self._reverse.keys(), key=len, reverse=True)
        for variant in ordered_variants:
            placeholder = self._reverse[variant]
            # Wortgrenzen, case-insensitive
            pattern = re.compile(rf"\b{re.escape(variant)}\b", flags=re.IGNORECASE)
            result = pattern.sub(placeholder, result)

        return PseudonymizedText(
            text=result,
            mapping=dict(self._mapping),
            parties=list(self._parties),
        )

    def _replace_generic(self, text: str) -> str:
        counters: dict[str, int] = {}

        def _next(label: str) -> str:
            counters[label] = counters.get(label, 0) + 1
            placeholder = f"[{label}_{counters[label]}]"
            return placeholder

        def _sub(pattern: re.Pattern[str], label: str, s: str) -> str:
            def replacer(match: re.Match[str]) -> str:
                placeholder = _next(label)
                self._mapping[placeholder] = match.group(0)
                return placeholder

            return pattern.sub(replacer, s)

        # Reihenfolge: spezifischste zuerst
        text = _sub(_IBAN_RE, "IBAN", text)
        text = _sub(_EMAIL_RE, "EMAIL", text)
        text = _sub(_PHONE_RE, "TEL", text)
        text = _sub(_STREET_RE, "ADRESSE", text)
        text = _sub(_PLZ_CITY_RE, "ORT", text)
        return text


def normalize_for_match(s: str) -> str:
    """Hilfsfunktion: Unicode-Normalisierung für robusteres Matching."""
    return unicodedata.normalize("NFC", s)

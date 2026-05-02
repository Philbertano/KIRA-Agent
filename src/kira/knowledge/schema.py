"""Datenmodell für Gesetze und Normen.

Eine ``Norm`` ist ein einzelner Paragraph einer ``Gesetz``-Sammlung. Mehrere
Gesetze (BGB, BetrKV, HeizkostenV, ...) leben nebeneinander im Korpus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class Norm:
    """Ein einzelner Paragraph (z.B. § 535 BGB)."""

    gesetz: str  # juristische Abkürzung, z.B. "BGB", "BetrKV", "HeizkostenV"
    paragraph: str  # "535", "536a", "578"
    titel: str
    absaetze: list[str] = field(default_factory=list)
    abschnitt: str | None = None  # gliederungstitel, z.B. "Mietverhältnisse..."
    fundstelle: str | None = None  # offizielle BGBl-Fundstelle der letzten Änderung
    quelle_url: str | None = None  # zurück zur offiziellen Quelle

    @property
    def zitation(self) -> str:
        return f"§ {self.paragraph} {self.gesetz}"

    @property
    def volltext(self) -> str:
        return "\n".join(self.absaetze)

    def to_display(self, stand: date | None = None) -> str:
        """Anwender-freundliche Textdarstellung mit Stand-Hinweis."""
        lines = [f"{self.zitation} — {self.titel}"]
        if self.abschnitt:
            lines.append(f"(Abschnitt: {self.abschnitt})")
        if stand:
            lines.append(f"(Stand: {stand.isoformat()}, Quelle: gesetze-im-internet.de)")
        if self.quelle_url:
            lines.append(f"URL: {self.quelle_url}")
        lines.append("")
        lines.extend(self.absaetze)
        return "\n".join(lines)


@dataclass(frozen=True)
class Gesetz:
    """Ein vollständiges (oder gefiltertes) Gesetz mit Stand-Information."""

    abkuerzung: str  # "BGB", "BetrKV", ...
    titel: str
    stand: date  # Tag der letzten Aktualisierung des lokalen Korpus
    quelle: str  # z.B. "gesetze-im-internet.de"
    normen: dict[str, Norm] = field(default_factory=dict)
    gefiltert_auf: tuple[str, ...] | None = None  # z.B. ("§§ 535-580a", "Mietrecht")

    def get(self, paragraph: str) -> Norm | None:
        return self.normen.get(_normalize_paragraph(paragraph))

    def list_paragraphen(self) -> list[str]:
        return sorted(self.normen.keys(), key=_paragraph_sort_key)


def _normalize_paragraph(query: str) -> str:
    """'§ 535', '§535', '535', '536a', '§ 536 BGB' → '535' / '536a' / '536'

    Stripped Gesetzes-Namen ein, damit Aufrufe wie '§ 536 BGB' nicht
    als '536B' interpretiert werden.
    """
    import re

    # Bekannte Gesetzes-Abkürzungen entfernen, dann § und Whitespace
    cleaned = re.sub(
        r"\b(BGB|BetrKV|HeizkostenV|EGBGB|ZPO|StGB|GG|HGB|StPO|VwGO|SGB)\b",
        "",
        query,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[§\s]", "", cleaned)
    # Zahl gefolgt von optionalem Kleinbuchstaben-Suffix (a/b/c/...)
    match = re.match(r"^(\d+)([a-z]?)$", cleaned, flags=re.IGNORECASE)
    if match:
        return match.group(1) + match.group(2).lower()
    # Fallback: nur die führende Zahl
    digits = re.match(r"^(\d+)", cleaned)
    return digits.group(1) if digits else cleaned


def _paragraph_sort_key(paragraph: str) -> tuple[int, str]:
    """Sortiert '535' vor '536' vor '536a' vor '536b' vor '537'."""
    import re

    m = re.match(r"^(\d+)([a-zA-Z]?)$", paragraph)
    if not m:
        return (0, paragraph)
    return (int(m.group(1)), m.group(2))


def norm_from_dict(gesetz: str, paragraph: str, data: dict[str, Any]) -> Norm:
    """Konstruiert Norm aus dem JSON-Korpus-Format."""
    return Norm(
        gesetz=gesetz,
        paragraph=paragraph,
        titel=data.get("titel", ""),
        absaetze=list(data.get("absaetze", [])),
        abschnitt=data.get("abschnitt"),
        fundstelle=data.get("fundstelle"),
        quelle_url=data.get("quelle_url"),
    )


def norm_to_dict(norm: Norm) -> dict[str, Any]:
    out: dict[str, Any] = {"titel": norm.titel, "absaetze": list(norm.absaetze)}
    if norm.abschnitt:
        out["abschnitt"] = norm.abschnitt
    if norm.fundstelle:
        out["fundstelle"] = norm.fundstelle
    if norm.quelle_url:
        out["quelle_url"] = norm.quelle_url
    return out

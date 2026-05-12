"""Parser for the gesetze-im-internet.de gii-norm XML format.

Self-contained: no kira.* imports. Used by the legal-sources ingest
Lambda and (transitively) by the backfill script.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

_PARAGRAPH_RE = re.compile(r"§\s*(\d+[a-zA-Z]?)")


@dataclass(frozen=True)
class Norm:
    """A single paragraph extracted from a gii-norm XML document."""

    gesetz: str
    paragraph: str
    titel: str
    absaetze: list[str] = field(default_factory=list)
    abschnitt: str | None = None
    fundstelle: str | None = None
    quelle_url: str | None = None


@dataclass
class ParseResult:
    abkuerzung: str
    titel: str
    normen: dict[str, Norm]


def parse_gii_xml(xml_bytes: bytes) -> ParseResult:
    """Parse a gii-norm XML document into a ParseResult."""
    root = ET.fromstring(xml_bytes)
    abkuerzung: str | None = None
    titel: str | None = None
    normen: dict[str, Norm] = {}
    aktueller_abschnitt: str | None = None

    for norm_el in root.iter("norm"):
        meta = norm_el.find("metadaten")
        if meta is None:
            continue

        if abkuerzung is None:
            jurabk = meta.findtext("jurabk")
            if jurabk:
                abkuerzung = jurabk.strip()

        gliederung = meta.find("gliederungseinheit")
        if gliederung is not None:
            gtitel = gliederung.findtext("gliederungstitel")
            if gtitel:
                aktueller_abschnitt = _clean_text(gtitel)

        enbez = meta.findtext("enbez")
        if not enbez:
            if titel is None:
                kurzue = meta.findtext("kurzue") or meta.findtext("langue")
                if kurzue:
                    titel = _clean_text(kurzue)
            continue

        paragraph = _extract_paragraph_number(enbez)
        if not paragraph:
            continue

        norm_titel = _clean_text(meta.findtext("titel") or "")
        absaetze = _extract_absaetze(norm_el)
        fundstelle = _extract_fundstelle(meta)

        normen[paragraph] = Norm(
            gesetz=abkuerzung or "?",
            paragraph=paragraph,
            titel=norm_titel,
            absaetze=absaetze,
            abschnitt=aktueller_abschnitt,
            fundstelle=fundstelle,
            quelle_url=None,
        )

    return ParseResult(
        abkuerzung=abkuerzung or "?",
        titel=titel or abkuerzung or "?",
        normen=normen,
    )


def normalize_paragraph(query: str) -> str:
    """'§ 535', '§535', '535', '536a', '§ 536 BGB' → '535' / '536a' / '536'."""
    cleaned = re.sub(
        r"\b(BGB|BetrKV|HeizkostenV|EGBGB|ZPO|StGB|GG|HGB|StPO|VwGO|SGB)\b",
        "",
        query,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[§\s]", "", cleaned)
    match = re.match(r"^(\d+)([a-z]?)$", cleaned, flags=re.IGNORECASE)
    if match:
        return match.group(1) + match.group(2).lower()
    digits = re.match(r"^(\d+)", cleaned)
    return digits.group(1) if digits else cleaned


def _extract_paragraph_number(enbez: str) -> str | None:
    match = _PARAGRAPH_RE.search(enbez)
    if match:
        return match.group(1)
    art_match = re.search(r"Art\.?\s*(\d+[a-zA-Z]?)", enbez)
    if art_match:
        return art_match.group(1)
    digits = re.search(r"(\d+[a-zA-Z]?)", enbez)
    return digits.group(1) if digits else None


def _extract_absaetze(norm_el: ET.Element) -> list[str]:
    absaetze: list[str] = []
    for content_el in norm_el.iter("Content"):
        for p_el in content_el.iter("P"):
            text = _flatten_text(p_el)
            if text:
                absaetze.append(text)
        if absaetze:
            return absaetze
    textdaten = norm_el.find("textdaten")
    if textdaten is not None:
        text = _flatten_text(textdaten).strip()
        if text:
            return [text]
    return []


def _extract_fundstelle(meta: ET.Element) -> str | None:
    fst = meta.find("fundstelle")
    if fst is None:
        return None
    periodikum = fst.findtext("periodikum") or ""
    zitstelle = fst.findtext("zitstelle") or ""
    out = " ".join(part for part in [periodikum, zitstelle] if part).strip()
    return out or None


def _flatten_text(el: ET.Element) -> str:
    parts: list[str] = []

    def _walk(node: ET.Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            tag = child.tag.lower()
            if tag in {"br"}:
                parts.append("\n")
            else:
                _walk(child)
            if child.tail:
                parts.append(child.tail)

    _walk(el)
    return _clean_text("".join(parts))


def _clean_text(text: str) -> str:
    return re.sub(r"[ \t\r]+", " ", text).strip()

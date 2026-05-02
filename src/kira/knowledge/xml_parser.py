"""Parser für das XML-Format von gesetze-im-internet.de (gii-norm DTD).

Struktur (vereinfacht)::

    <dokumente>
      <norm doknr="..." builddate="...">
        <metadaten>
          <jurabk>BGB</jurabk>
          <enbez>§ 535</enbez>
          <titel>Inhalt und Hauptpflichten...</titel>
          <gliederungseinheit>
            <gliederungstitel>Mietverhältnisse über Wohnraum</gliederungstitel>
          </gliederungseinheit>
          <fundstelle .../>
        </metadaten>
        <textdaten>
          <text format="XML">
            <Content>
              <P>(1) Durch den Mietvertrag...</P>
              <P>(2) Der Mieter ist...</P>
            </Content>
          </text>
        </textdaten>
      </norm>
      ...
    </dokumente>

Wir parsen robust: unbekannte/fehlende Elemente werden ignoriert, statt zu crashen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator
from xml.etree import ElementTree as ET

from kira.knowledge.schema import Norm, _normalize_paragraph


_PARAGRAPH_RE = re.compile(r"§\s*(\d+[a-zA-Z]?)")


@dataclass
class ParseResult:
    abkuerzung: str
    titel: str
    normen: dict[str, Norm]


def parse_gii_xml(xml_bytes: bytes) -> ParseResult:
    """Parst eine gii-norm-XML-Datei.

    Die erste ``<norm>`` ohne ``<enbez>`` enthält üblicherweise die Rahmen-
    Metadaten (Gesetzestitel etc.); alle weiteren ``<norm>`` mit ``<enbez>``
    sind die Einzelnormen.
    """
    root = ET.fromstring(xml_bytes)
    abkuerzung: str | None = None
    titel: str | None = None
    normen: dict[str, Norm] = {}
    aktueller_abschnitt: str | None = None

    for norm_el in root.iter("norm"):
        meta = norm_el.find("metadaten")
        if meta is None:
            continue

        # Gesetzes-Abkürzung beim ersten Auftreten merken
        if abkuerzung is None:
            jurabk = meta.findtext("jurabk")
            if jurabk:
                abkuerzung = jurabk.strip()

        # Gliederungs-Header (kein <enbez>) → Abschnittstitel merken
        gliederung = meta.find("gliederungseinheit")
        if gliederung is not None:
            gtitel = gliederung.findtext("gliederungstitel")
            if gtitel:
                aktueller_abschnitt = _clean_text(gtitel)

        enbez = meta.findtext("enbez")
        if not enbez:
            # Header-Norm (Gesetzestitel) — Titel merken, dann skip
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
            quelle_url=None,  # wird vom Ingest gesetzt
        )

    return ParseResult(
        abkuerzung=abkuerzung or "?",
        titel=titel or abkuerzung or "?",
        normen=normen,
    )


def _extract_paragraph_number(enbez: str) -> str | None:
    """'§ 535', '§535', 'Art. 1' → '535' / '1'"""
    match = _PARAGRAPH_RE.search(enbez)
    if match:
        return match.group(1)
    # Artikel statt §
    art_match = re.search(r"Art\.?\s*(\d+[a-zA-Z]?)", enbez)
    if art_match:
        return art_match.group(1)
    # Fallback: erste Zahl
    digits = re.search(r"(\d+[a-zA-Z]?)", enbez)
    return digits.group(1) if digits else None


def _extract_absaetze(norm_el: ET.Element) -> list[str]:
    """Liest <Content><P>...</P></Content> und liefert eine Liste pro <P>."""
    absaetze: list[str] = []
    for content_el in norm_el.iter("Content"):
        for p_el in content_el.iter("P"):
            text = _flatten_text(p_el)
            if text:
                absaetze.append(text)
        if absaetze:
            return absaetze

    # Fallback: ganzer textdaten-Bereich
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
    """Sammelt Text inkl. <I>, <B>, <BR/>-Inline-Markup."""
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


def filter_normen(
    result: ParseResult,
    *,
    paragraphen: list[str] | None = None,
    paragraph_range: tuple[str, str] | None = None,
) -> dict[str, Norm]:
    """Filtert die geparsten Normen.

    - ``paragraphen``: explizite Whitelist
    - ``paragraph_range``: ('535', '580a') → alle inkl. Buchstabensuffix
    """
    if paragraphen:
        wanted = {_normalize_paragraph(p) for p in paragraphen}
        return {k: v for k, v in result.normen.items() if k in wanted}

    if paragraph_range:
        start_num = _split_paragraph(paragraph_range[0])
        end_num = _split_paragraph(paragraph_range[1])

        def _in_range(p: str) -> bool:
            num, _ = _split_paragraph(p)
            return start_num[0] <= num <= end_num[0]

        return {
            k: v for k, v in result.normen.items() if _in_range(k)
        }

    return dict(result.normen)


def _split_paragraph(p: str) -> tuple[int, str]:
    p = _normalize_paragraph(p)
    m = re.match(r"^(\d+)([a-zA-Z]?)$", p)
    if not m:
        return (0, p)
    return (int(m.group(1)), m.group(2))


def _normalize_paragraph_helper_for_export(p: str) -> str:
    """Re-export für Tests (vermeidet zirkulären Import)."""
    return _normalize_paragraph(p)

"""Tests für den gesetze-im-internet.de XML-Parser.

Wir testen gegen eine synthetische XML-Fixture, die die wichtigsten
Strukturmerkmale des gii-norm-Formats nachbildet. Damit ist der Parser
gegen Regressionen geschützt, ohne dass wir bei jedem Testlauf
gesetze-im-internet.de erreichen müssen.
"""

from __future__ import annotations

import textwrap

import pytest

from kira.legal_sources._common.xml_parser import normalize_paragraph, parse_gii_xml

FIXTURE_BGB_AUSZUG = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="utf-8"?>
    <dokumente>
      <norm doknr="BJNR001950896BJNG006002314" builddate="20260101000000">
        <metadaten>
          <jurabk>BGB</jurabk>
          <amtabk>BGB</amtabk>
          <ausfertigung-datum>1896-08-18</ausfertigung-datum>
          <kurzue>Bürgerliches Gesetzbuch</kurzue>
          <langue>Bürgerliches Gesetzbuch</langue>
        </metadaten>
        <textdaten/>
      </norm>
      <norm doknr="BJNR001950896BJNE051709377" builddate="20260101000000">
        <metadaten>
          <jurabk>BGB</jurabk>
          <enbez>§ 535</enbez>
          <gliederungseinheit>
            <gliederungsbez>Untertitel 1</gliederungsbez>
            <gliederungstitel>Allgemeine Vorschriften für Mietverhältnisse</gliederungstitel>
          </gliederungseinheit>
          <titel format="XML">Inhalt und Hauptpflichten des Mietvertrags</titel>
          <fundstelle typ="amtlich">
            <periodikum>BGBl I</periodikum>
            <zitstelle>2001, 1149</zitstelle>
          </fundstelle>
        </metadaten>
        <textdaten>
          <text format="XML">
            <Content>
              <P>(1) Durch den Mietvertrag wird der Vermieter verpflichtet, dem Mieter den Gebrauch der Mietsache während der Mietzeit zu gewähren.</P>
              <P>(2) Der Mieter ist verpflichtet, dem Vermieter die vereinbarte Miete zu entrichten.</P>
            </Content>
          </text>
        </textdaten>
      </norm>
      <norm doknr="BJNR001950896BJNE051709378" builddate="20260101000000">
        <metadaten>
          <jurabk>BGB</jurabk>
          <enbez>§ 536</enbez>
          <titel format="XML">Mietminderung bei Sach- und Rechtsmängeln</titel>
        </metadaten>
        <textdaten>
          <text format="XML">
            <Content>
              <P>(1) Hat die Mietsache einen <I>Mangel</I>, so ist der Mieter von der Miete befreit.</P>
            </Content>
          </text>
        </textdaten>
      </norm>
      <norm doknr="BJNR001950896BJNE051709379" builddate="20260101000000">
        <metadaten>
          <jurabk>BGB</jurabk>
          <enbez>§ 536a</enbez>
          <titel format="XML">Schadensersatz</titel>
        </metadaten>
        <textdaten>
          <text format="XML">
            <Content>
              <P>(1) Schadensersatzanspruch des Mieters.</P>
            </Content>
          </text>
        </textdaten>
      </norm>
      <norm doknr="BJNR001950896BJNE051709400" builddate="20260101000000">
        <metadaten>
          <jurabk>BGB</jurabk>
          <enbez>§ 600</enbez>
          <titel format="XML">Außerhalb des Mietrechts</titel>
        </metadaten>
        <textdaten>
          <text format="XML">
            <Content>
              <P>Diese Norm sollte nicht in einem Mietrecht-Filter erscheinen.</P>
            </Content>
          </text>
        </textdaten>
      </norm>
    </dokumente>
    """
).encode("utf-8")


def test_parse_extracts_paragraphen() -> None:
    result = parse_gii_xml(FIXTURE_BGB_AUSZUG)
    assert result.abkuerzung == "BGB"
    assert "535" in result.normen
    assert "536" in result.normen
    assert "536a" in result.normen
    assert "600" in result.normen


def test_parse_titel_korrekt() -> None:
    result = parse_gii_xml(FIXTURE_BGB_AUSZUG)
    assert "Hauptpflichten" in result.normen["535"].titel


def test_parse_absaetze_separiert() -> None:
    result = parse_gii_xml(FIXTURE_BGB_AUSZUG)
    absaetze = result.normen["535"].absaetze
    assert len(absaetze) == 2
    assert absaetze[0].startswith("(1)")
    assert absaetze[1].startswith("(2)")


def test_parse_inline_markup_flattened() -> None:
    """<I>Mangel</I> soll als Text bleiben, nicht den Absatz teilen."""
    result = parse_gii_xml(FIXTURE_BGB_AUSZUG)
    text = result.normen["536"].absaetze[0]
    assert "Mangel" in text
    assert "<I>" not in text


def test_parse_abschnitt_propagiert() -> None:
    """gliederungstitel soll an die folgende Norm angehängt werden."""
    result = parse_gii_xml(FIXTURE_BGB_AUSZUG)
    assert result.normen["535"].abschnitt is not None
    assert "Mietverhältnisse" in result.normen["535"].abschnitt
    # § 536 erbt den Abschnitt (kein neuer gliederungseinheit-Block)
    assert result.normen["536"].abschnitt is not None


def test_parse_fundstelle_extrahiert() -> None:
    result = parse_gii_xml(FIXTURE_BGB_AUSZUG)
    fundstelle = result.normen["535"].fundstelle
    assert fundstelle is not None
    assert "BGBl" in fundstelle


def test_parse_handles_missing_optional_fields() -> None:
    """Wenn fundstelle/gliederung fehlen, soll der Parser nicht crashen."""
    minimal = textwrap.dedent(
        """\
        <?xml version="1.0" encoding="utf-8"?>
        <dokumente>
          <norm>
            <metadaten>
              <jurabk>TEST</jurabk>
              <enbez>§ 1</enbez>
              <titel>Minimal</titel>
            </metadaten>
            <textdaten>
              <text format="XML">
                <Content><P>Inhalt</P></Content>
              </text>
            </textdaten>
          </norm>
        </dokumente>
        """
    ).encode("utf-8")

    result = parse_gii_xml(minimal)
    assert "1" in result.normen
    norm = result.normen["1"]
    assert norm.fundstelle is None
    assert norm.abschnitt is None
    assert norm.absaetze == ["Inhalt"]


@pytest.mark.parametrize("raw,expected", [
    ("§ 535", "535"),
    ("§535", "535"),
    ("535", "535"),
    ("536a", "536a"),
    ("§ 536 BGB", "536"),
    ("§ 1 BGB", "1"),
])
def test_normalize_paragraph_strips_paragraph_marker_and_gesetz_suffix(raw: str, expected: str) -> None:
    assert normalize_paragraph(raw) == expected

"""Ingest-Pipeline für gesetze-im-internet.de XML-Korpora.

Lädt das offizielle ZIP eines Gesetzes herunter, parst das XML, filtert auf
die für KIRA relevanten Paragraphen und schreibt einen aktualisierten
JSON-Korpus nach ``./data/gesetze/<abk>.json``.

Verwendung::

    kira ingest bgb
    kira ingest betrkv heizkostenv
    kira ingest --all
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from kira.knowledge.loader import DEFAULT_OVERLAY_DIR
from kira.knowledge.schema import norm_to_dict
from kira.knowledge.xml_parser import filter_normen, parse_gii_xml

log = logging.getLogger(__name__)


GII_BASE = "https://www.gesetze-im-internet.de"
USER_AGENT = "KIRA-Agent/0.1 (juristischer Junior-Assistent; Anwaltskanzlei)"


@dataclass(frozen=True)
class GesetzKonfiguration:
    """Welche Paragraphen wir aus einem Gesetz speichern."""

    abkuerzung: str
    titel: str
    pfad: str  # URL-Pfad-Segment auf gesetze-im-internet.de
    paragraph_range: tuple[str, str] | None = None  # (von, bis), beide inkl.
    paragraphen: list[str] | None = None  # explizite Liste alternativ

    @property
    def zip_url(self) -> str:
        return f"{GII_BASE}/{self.pfad}/xml.zip"

    @property
    def base_url(self) -> str:
        return f"{GII_BASE}/{self.pfad}"


# Welche Gesetze für Mietrecht relevant sind.
GESETZE: dict[str, GesetzKonfiguration] = {
    "bgb": GesetzKonfiguration(
        abkuerzung="BGB",
        titel="Bürgerliches Gesetzbuch",
        pfad="bgb",
        paragraph_range=("194", "580a"),  # Verjährung, Verzug, Mietrecht
    ),
    "betrkv": GesetzKonfiguration(
        abkuerzung="BetrKV",
        titel="Verordnung über die Aufstellung von Betriebskosten (Betriebskostenverordnung)",
        pfad="betrkv",
        # komplett (klein)
    ),
    "heizkostenv": GesetzKonfiguration(
        abkuerzung="HeizkostenV",
        titel="Verordnung über Heizkostenabrechnung (Heizkostenverordnung)",
        pfad="heizkostenv",
        # komplett (klein)
    ),
}


def ingest(
    abkuerzungen: list[str] | None = None,
    *,
    output_dir: Path | None = None,
    timeout: float = 30.0,
) -> dict[str, Path]:
    """Lädt und speichert die angegebenen Gesetze. Gibt geschriebene Pfade zurück."""
    output_dir = output_dir or DEFAULT_OVERLAY_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    keys = abkuerzungen or list(GESETZE.keys())
    written: dict[str, Path] = {}

    with httpx.Client(
        timeout=httpx.Timeout(timeout, connect=10.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        for key in keys:
            cfg = GESETZE.get(key.lower())
            if cfg is None:
                log.warning("Unbekanntes Gesetz: %s — übersprungen", key)
                continue
            path = _ingest_one(client, cfg, output_dir)
            written[cfg.abkuerzung] = path

    return written


def _ingest_one(client: httpx.Client, cfg: GesetzKonfiguration, output_dir: Path) -> Path:
    log.info("Lade %s von %s", cfg.abkuerzung, cfg.zip_url)
    response = client.get(cfg.zip_url)
    response.raise_for_status()

    xml_bytes = _extract_xml_from_zip(response.content)
    parsed = parse_gii_xml(xml_bytes)

    filtered = filter_normen(
        parsed,
        paragraphen=cfg.paragraphen,
        paragraph_range=cfg.paragraph_range,
    )
    if not filtered:
        raise RuntimeError(
            f"Keine Paragraphen für {cfg.abkuerzung} extrahiert — Filter prüfen "
            f"(range={cfg.paragraph_range}, list={cfg.paragraphen})."
        )

    # Quelle-URL pro Paragraph hinzufügen
    payload = {
        "_meta": {
            "abkuerzung": cfg.abkuerzung,
            "titel": cfg.titel,
            "stand": date.today().isoformat(),
            "quelle": "gesetze-im-internet.de",
            "quelle_url": cfg.base_url,
            "gefiltert_auf": (
                [f"§§ {cfg.paragraph_range[0]}–{cfg.paragraph_range[1]}"]
                if cfg.paragraph_range
                else (cfg.paragraphen or ["vollständig"])
            ),
            "anzahl_normen": len(filtered),
        },
        "paragraphen": {
            paragraph: {
                **norm_to_dict(norm),
                "quelle_url": f"{cfg.base_url}/__{paragraph}.html",
            }
            for paragraph, norm in sorted(filtered.items(), key=_sort_key)
        },
    }

    out_path = output_dir / f"{cfg.abkuerzung.lower()}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "  → %s mit %d Normen gespeichert (%s)",
        cfg.abkuerzung,
        len(filtered),
        out_path,
    )
    return out_path


def _extract_xml_from_zip(zip_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not xml_names:
            raise RuntimeError("Keine XML-Datei im ZIP gefunden.")
        with zf.open(xml_names[0]) as f:
            return f.read()


def _sort_key(item: tuple[str, object]) -> tuple[int, str]:
    import re

    p = item[0]
    m = re.match(r"^(\d+)([a-zA-Z]?)$", p)
    if not m:
        return (0, p)
    return (int(m.group(1)), m.group(2))

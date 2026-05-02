"""Lädt den Gesetzes-Korpus aus dem Package und (optional) lokalen Overlay.

Suchreihenfolge:
1. Lokales Overlay-Verzeichnis (z.B. ./data/gesetze/) — wenn der Anwender den
   Ingest-Befehl ausgeführt hat. Hat Vorrang, da aktueller.
2. Im Package gebündelte kuratierte JSONs (Übergangslösung).

Dadurch funktioniert KIRA out-of-the-box mit kuratiertem Korpus, kann aber
direkt nach `kira ingest` ohne Code-Änderung den vollständigen offiziellen
Korpus nutzen.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from kira.knowledge.schema import Gesetz, norm_from_dict

log = logging.getLogger(__name__)


# Standard-Speicherort für lokales Overlay (per Env überschreibbar)
DEFAULT_OVERLAY_DIR = Path(os.environ.get("KIRA_GESETZE_DIR", "./data/gesetze"))


_GESETZ_CACHE: dict[str, Gesetz] = {}


def load_gesetz(abkuerzung: str, *, refresh: bool = False) -> Gesetz | None:
    """Lädt ein Gesetz aus Overlay oder Package. ``None`` falls unbekannt."""
    key = abkuerzung.lower()
    if not refresh and key in _GESETZ_CACHE:
        return _GESETZ_CACHE[key]

    data = _load_from_overlay(key) or _load_from_package(key)
    if data is None:
        return None

    gesetz = _gesetz_from_dict(key, data)
    _GESETZ_CACHE[key] = gesetz
    return gesetz


def list_gesetze() -> list[str]:
    """Verfügbare Gesetzes-Abkürzungen (Overlay + Package, dedupliziert)."""
    seen: set[str] = set()

    # Overlay
    if DEFAULT_OVERLAY_DIR.is_dir():
        for path in DEFAULT_OVERLAY_DIR.glob("*.json"):
            seen.add(path.stem.lower())

    # Package
    try:
        package = resources.files("kira.knowledge.gesetze")
        for entry in package.iterdir():
            name = entry.name
            if name.endswith(".json") and not name.startswith("_"):
                seen.add(name[:-5].lower())
    except (ModuleNotFoundError, AttributeError):  # pragma: no cover
        pass

    return sorted(seen)


def clear_cache() -> None:
    _GESETZ_CACHE.clear()


# --- Quellen ---


def _load_from_overlay(key: str) -> dict[str, Any] | None:
    path = DEFAULT_OVERLAY_DIR / f"{key}.json"
    if not path.is_file():
        return None
    log.debug("Lade Gesetz %s aus Overlay %s", key, path)
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _load_from_package(key: str) -> dict[str, Any] | None:
    try:
        package = resources.files("kira.knowledge.gesetze")
        candidate = package.joinpath(f"{key}.json")
        if not candidate.is_file():
            return None
        log.debug("Lade Gesetz %s aus Package", key)
        with candidate.open(encoding="utf-8") as f:
            return json.load(f)
    except (ModuleNotFoundError, FileNotFoundError):
        return None


def _gesetz_from_dict(key: str, data: dict[str, Any]) -> Gesetz:
    meta = data.get("_meta", {})
    stand_str = meta.get("stand", "1970-01-01")
    try:
        stand = datetime.fromisoformat(stand_str).date()
    except ValueError:
        stand = date(1970, 1, 1)

    normen_dict = data.get("paragraphen", {})
    abkuerzung = meta.get("abkuerzung", key.upper())
    normen = {
        para: norm_from_dict(abkuerzung, para, payload)
        for para, payload in normen_dict.items()
    }

    return Gesetz(
        abkuerzung=abkuerzung,
        titel=meta.get("titel", abkuerzung),
        stand=stand,
        quelle=meta.get("quelle", "unbekannt"),
        normen=normen,
        gefiltert_auf=tuple(meta.get("gefiltert_auf", [])) or None,
    )


def stand_warnung(stand: date, today: date | None = None) -> str | None:
    """Warnung, wenn der Korpus älter als 6 Monate ist."""
    today = today or date.today()
    monate = (today.year - stand.year) * 12 + (today.month - stand.month)
    if monate >= 6:
        return (
            f"WARNUNG: Lokaler Gesetzes-Stand ist {monate} Monate alt "
            f"({stand.isoformat()}). Bitte 'kira ingest' ausführen, um die "
            f"aktuellen Texte von gesetze-im-internet.de zu laden."
        )
    return None

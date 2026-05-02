"""Tool: lookup_norm — schlägt einen § aus dem BGB-Mietrecht nach.

Phase 1: lokaler kuratierter Korpus (siehe knowledge/gesetze/bgb_mietrecht.json).
Phase 2: Fallback auf gesetze-im-internet.de mit lokalem Cache.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any

from kira.agent.tools._registry import Tool, register


def _load_corpus() -> dict[str, Any]:
    with resources.files("kira.knowledge.gesetze").joinpath("bgb_mietrecht.json").open(
        encoding="utf-8"
    ) as f:
        return json.load(f)


_CORPUS_CACHE: dict[str, Any] | None = None


def _corpus() -> dict[str, Any]:
    global _CORPUS_CACHE
    if _CORPUS_CACHE is None:
        _CORPUS_CACHE = _load_corpus()
    return _CORPUS_CACHE


def _normalize_paragraph(query: str) -> str:
    """Akzeptiert '§ 535', '§535 BGB', '535', '536a' usw."""
    # Entferne § und 'BGB', behalte Buchstabensuffix wie 'a', 'b'
    cleaned = re.sub(r"§|BGB", "", query, flags=re.IGNORECASE).strip()
    match = re.match(r"^\s*(\d+[a-z]?)\s*", cleaned)
    if not match:
        return cleaned
    return match.group(1)


def run(input_data: dict[str, Any]) -> str:
    paragraph = _normalize_paragraph(str(input_data.get("paragraph", "")))
    if not paragraph:
        return "FEHLER: Kein Paragraph angegeben."

    paragraphen = _corpus().get("paragraphen", {})
    norm = paragraphen.get(paragraph)
    if not norm:
        verfuegbar = ", ".join(sorted(paragraphen.keys()))
        return (
            f"§ {paragraph} BGB ist im lokalen Mietrechts-Korpus nicht enthalten. "
            f"Verfügbar (Phase 1): {verfuegbar}. "
            f"Bitte für die rechtliche Würdigung kennzeichnen, dass diese Norm nicht "
            f"verifiziert werden konnte."
        )

    meta = _corpus().get("_meta", {})
    body = "\n".join(norm["absaetze"])
    return (
        f"§ {paragraph} BGB — {norm['titel']}\n"
        f"(Quelle: {meta.get('quelle', 'unbekannt')}, Stand {meta.get('stand', 'n/a')})\n\n"
        f"{body}"
    )


TOOL = register(
    Tool(
        name="lookup_norm",
        description=(
            "Schlägt eine Vorschrift aus dem BGB-Mietrecht (§§ 535–580a) im Wortlaut nach. "
            "Verwende dieses Tool, BEVOR du eine Norm zitierst — niemals aus dem Gedächtnis."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "paragraph": {
                    "type": "string",
                    "description": "Paragraph, z.B. '535', '536a', '§ 573 BGB'.",
                }
            },
            "required": ["paragraph"],
        },
        run=run,
    )
)

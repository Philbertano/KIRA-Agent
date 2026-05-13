"""Tool: search_norm — semantic search via the AWS legal-sources Lambda.

Returns top-k candidate paragraphs with a score and a wortlaut excerpt.
The model must call lookup_norm afterwards for the authoritative text —
search excerpts are truncated.
"""

from __future__ import annotations

import logging
from typing import Any

from kira.agent.legal_client import LegalSourcesClient, LegalSourceUnavailable
from kira.agent.tools._registry import Tool, register

log = logging.getLogger(__name__)

_client = LegalSourcesClient()

_EXCERPT_LEN = 400


def run(input_data: dict[str, Any]) -> str:
    try:
        result = _client.search_norm(input_data)
    except LegalSourceUnavailable as exc:
        log.warning("search_norm unavailable: %s", exc)
        return (
            "Fehler: Rechtsquelle gerade nicht erreichbar. "
            "Bitte später erneut versuchen."
        )
    if "error" in result:
        return result.get("message") or f"Fehler: {result['error']}"
    return _format_hits(result)


def _format_hits(r: dict[str, Any]) -> str:
    hits = r.get("hits") or []
    if not hits:
        return f"Keine Treffer für: {r.get('query', '')!r}."

    lines = [f"Treffer für {r.get('query', '')!r} ({len(hits)}):", ""]
    for i, h in enumerate(hits, 1):
        score = h.get("score", 0.0)
        lines.append(
            f"{i}. {h['gesetz']} §{h['paragraph']} — {h.get('titel', '')}  (score={score:.2f})"
        )
        wortlaut = h.get("wortlaut") or ""
        if len(wortlaut) > _EXCERPT_LEN:
            wortlaut = wortlaut[: _EXCERPT_LEN - 1].rstrip() + "…"
        if wortlaut:
            lines.append(f"   {wortlaut}")
        if h.get("quelle_url"):
            lines.append(f"   Quelle: {h['quelle_url']}")
        lines.append("")
    lines.append(
        "Hinweis: Wortlaut oben ist ein Auszug. Für die Zitierung "
        "lookup_norm aufrufen."
    )
    return "\n".join(lines).rstrip()


TOOL = register(
    Tool(
        name="search_norm",
        description=(
            "Semantische Suche im vollständigen deutschen Bundesrecht (~6.500 "
            "Gesetze und Verordnungen). Nutze dieses Tool, wenn du den passenden "
            "§ noch nicht kennst — z.B. 'Mietminderung wegen Schimmel', "
            "'Eigenbedarfskündigung juristische Person', 'Verzug Mahnung'. "
            "Liefert Top-k Kandidaten mit Score und Auszug. Den Wortlaut zur "
            "Zitierung holst du anschließend per lookup_norm."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natürlichsprachige Suchanfrage in Deutsch.",
                },
                "k": {
                    "type": "integer",
                    "description": "Anzahl Treffer (1-50, Default 10).",
                    "default": 10,
                },
                "gesetz_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional: Liste kanonischer Abkürzungen (z.B. ['BGB', 'WoEigG']). "
                        "Filter ist case-sensitiv — verwende die jurabk-Schreibweise."
                    ),
                },
                "type_filter": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["Gesetz", "Verordnung"]},
                    "description": "Optional: 'Gesetz', 'Verordnung' oder beide.",
                },
            },
            "required": ["query"],
        },
        run=run,
    )
)

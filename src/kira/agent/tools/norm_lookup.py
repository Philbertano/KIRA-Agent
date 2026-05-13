"""Tool: lookup_norm — looks up a specific § via the AWS legal-sources Lambda.

Thin wrapper over LegalSourcesClient. The corpus and all parsing live
in AWS; this tool only formats the response for the model.
"""

from __future__ import annotations

import logging
from typing import Any

from kira.agent.legal_client import LegalSourcesClient, LegalSourceUnavailable
from kira.agent.tools._registry import Tool, register

log = logging.getLogger(__name__)

_client = LegalSourcesClient()


def run(input_data: dict[str, Any]) -> str:
    try:
        result = _client.lookup_norm(input_data)
    except LegalSourceUnavailable as exc:
        log.warning("lookup_norm unavailable: %s", exc)
        return (
            "Fehler: Rechtsquelle gerade nicht erreichbar. "
            "Bitte später erneut versuchen oder dem Anwalt mitteilen."
        )
    if "error" in result:
        return result.get("message") or f"Fehler: {result['error']}"
    return _format_success(result)


def _format_success(r: dict[str, Any]) -> str:
    lines = [f"{r['gesetz']} §{r['paragraph']} — {r.get('titel', '')}".rstrip(" —")]
    if r.get("gesetz_titel"):
        lines.append(f"({r['gesetz_titel']})")
    lines.append("")
    if r.get("wortlaut"):
        lines.append(r["wortlaut"])
    lines.append("")
    if r.get("stand"):
        lines.append(f"Stand: {r['stand']}")
    if r.get("quelle_url"):
        lines.append(f"Quelle: {r['quelle_url']}")
    if r.get("stand_warnung"):
        lines.append(f"WARNUNG: {r['stand_warnung']}")
    return "\n".join(lines).rstrip()


TOOL = register(
    Tool(
        name="lookup_norm",
        description=(
            "Schlägt einen einzelnen Paragraphen aus einem deutschen Bundesgesetz "
            "oder einer Rechtsverordnung im Wortlaut nach. Der Korpus umfasst alle "
            "~6.500 Gesetze von gesetze-im-internet.de und wird täglich aktualisiert. "
            "Verwende dieses Tool IMMER, BEVOR du eine Norm zitierst — niemals aus "
            "dem Gedächtnis. Output enthält Wortlaut, Stand und Quellen-URL."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "gesetz": {
                    "type": "string",
                    "description": (
                        "Kanonische Gesetzes-Abkürzung wie im <jurabk>-Feld von "
                        "gesetze-im-internet.de, z.B. 'BGB', 'WoEigG', 'BetrKV'."
                    ),
                },
                "paragraph": {
                    "type": "string",
                    "description": "Paragraph-Nummer, z.B. '535', '536a'.",
                },
                "absatz": {
                    "type": "string",
                    "description": "Optional: einzelner Absatz, z.B. '1'.",
                },
            },
            "required": ["gesetz", "paragraph"],
        },
        run=run,
    )
)

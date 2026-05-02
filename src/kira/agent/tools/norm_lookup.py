"""Tool: lookup_norm — schlägt eine Vorschrift in einem deutschen Gesetz nach.

Multi-Gesetz-Support: BGB, BetrKV, HeizkostenV, ... (alles, was in
``kira.knowledge.gesetze/*.json`` oder im Overlay-Verzeichnis liegt).

Der Stand des Korpus wird im Output mit ausgegeben — Tools warnen, wenn
der lokale Stand älter als 6 Monate ist.
"""

from __future__ import annotations

from typing import Any

from kira.agent.tools._registry import Tool, register
from kira.knowledge.loader import list_gesetze, load_gesetz, stand_warnung


def run(input_data: dict[str, Any]) -> str:
    paragraph = str(input_data.get("paragraph", "")).strip()
    if not paragraph:
        return "FEHLER: Kein Paragraph angegeben."

    abkuerzung = str(input_data.get("gesetz", "BGB")).strip() or "BGB"
    gesetz = load_gesetz(abkuerzung)
    if gesetz is None:
        verfuegbar = ", ".join(g.upper() for g in list_gesetze())
        return (
            f"FEHLER: Gesetz {abkuerzung!r} nicht im Korpus. "
            f"Verfügbar: {verfuegbar}."
        )

    norm = gesetz.get(paragraph)
    if norm is None:
        verfuegbare = ", ".join(gesetz.list_paragraphen())
        return (
            f"§ {paragraph} {gesetz.abkuerzung} ist im lokalen Korpus nicht enthalten.\n"
            f"Verfügbar: {verfuegbare}.\n"
            f"Hinweis: Wenn die Vorschrift existiert, aber im Korpus fehlt, bitte "
            f"'kira ingest {gesetz.abkuerzung.lower()}' ausführen oder den Anwalt "
            f"informieren — NICHT aus dem Gedächtnis zitieren."
        )

    output = norm.to_display(stand=gesetz.stand)
    warnung = stand_warnung(gesetz.stand)
    if warnung:
        output = f"{warnung}\n\n{output}"
    return output


TOOL = register(
    Tool(
        name="lookup_norm",
        description=(
            "Schlägt eine Vorschrift aus einem deutschen Gesetz im Wortlaut nach "
            "(BGB, BetrKV, HeizkostenV — vollständige Liste via list_gesetze). "
            "Verwende dieses Tool IMMER, BEVOR du eine Norm zitierst — niemals "
            "aus dem Gedächtnis. Output enthält Stand-Datum und Quellen-URL zur "
            "Verifikation durch den Anwalt."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "paragraph": {
                    "type": "string",
                    "description": "Paragraph-Nummer, z.B. '535', '536a', '§ 573 BGB'.",
                },
                "gesetz": {
                    "type": "string",
                    "description": (
                        "Gesetzes-Abkürzung. Default: 'BGB'. "
                        "Andere: 'BetrKV', 'HeizkostenV'."
                    ),
                    "default": "BGB",
                },
            },
            "required": ["paragraph"],
        },
        run=run,
    )
)

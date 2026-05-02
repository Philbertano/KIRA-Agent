"""Tool: list_normen — Inhaltsverzeichnis eines Gesetzes / Übersicht aller Gesetze."""

from __future__ import annotations

from typing import Any

from kira.agent.tools._registry import Tool, register
from kira.knowledge.loader import list_gesetze, load_gesetz, stand_warnung


def run(input_data: dict[str, Any]) -> str:
    abkuerzung = input_data.get("gesetz")

    if not abkuerzung:
        # Übersicht aller Gesetze
        gesetze_keys = list_gesetze()
        if not gesetze_keys:
            return "Lokaler Korpus ist leer. 'kira ingest' ausführen."
        lines = ["Verfügbare Gesetze im lokalen Korpus:", ""]
        for key in gesetze_keys:
            g = load_gesetz(key)
            if g is None:
                continue
            warn = stand_warnung(g.stand)
            warn_marker = " [VERALTET]" if warn else ""
            scope = (
                f" (gefiltert auf {', '.join(g.gefiltert_auf)})"
                if g.gefiltert_auf
                else ""
            )
            lines.append(
                f"• {g.abkuerzung} — {g.titel} "
                f"({len(g.normen)} Normen, Stand {g.stand.isoformat()}){scope}{warn_marker}"
            )
        lines.append("")
        lines.append("Nutze list_normen(gesetz='BGB') für das Inhaltsverzeichnis eines Gesetzes.")
        return "\n".join(lines)

    # Inhaltsverzeichnis eines Gesetzes
    gesetz = load_gesetz(str(abkuerzung))
    if gesetz is None:
        verfuegbar = ", ".join(g.upper() for g in list_gesetze())
        return f"FEHLER: Gesetz {abkuerzung!r} nicht im Korpus. Verfügbar: {verfuegbar}."

    lines = [
        f"{gesetz.abkuerzung} — {gesetz.titel}",
        f"Stand: {gesetz.stand.isoformat()} | Quelle: {gesetz.quelle}",
        "",
    ]
    if gesetz.gefiltert_auf:
        lines.append(f"Gefiltert auf: {', '.join(gesetz.gefiltert_auf)}")
        lines.append("")

    aktueller_abschnitt: str | None = None
    for paragraph in gesetz.list_paragraphen():
        norm = gesetz.normen[paragraph]
        if norm.abschnitt and norm.abschnitt != aktueller_abschnitt:
            lines.append(f"\n## {norm.abschnitt}")
            aktueller_abschnitt = norm.abschnitt
        lines.append(f"  § {paragraph} — {norm.titel}")

    warn = stand_warnung(gesetz.stand)
    if warn:
        lines.append("")
        lines.append(warn)

    return "\n".join(lines)


TOOL = register(
    Tool(
        name="list_normen",
        description=(
            "Listet das Inhaltsverzeichnis eines Gesetzes (mit allen verfügbaren §§) "
            "oder — ohne Parameter — alle Gesetze im lokalen Korpus. "
            "Hilfreich, um vor einer Würdigung zu prüfen, welche Vorschriften "
            "nachgeschlagen werden können."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "gesetz": {
                    "type": "string",
                    "description": (
                        "Optional: Gesetzes-Abkürzung (z.B. 'BGB'). "
                        "Ohne Angabe: Übersicht aller Gesetze."
                    ),
                },
            },
        },
        run=run,
    )
)

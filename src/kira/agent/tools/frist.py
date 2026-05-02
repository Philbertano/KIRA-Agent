"""Tool: berechne_frist — deterministische Fristen-Berechnung im Mietrecht.

Wichtig: KEIN LLM. Reine Rechen-Logik mit den BGB-/ZPO-Regeln.
Der Agent ruft das Tool auf, wenn er eine Frist braucht, statt sie
selbst zu schätzen — verhindert Halluzination.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from dateutil.parser import parse as parse_date
from dateutil.relativedelta import relativedelta

from kira.agent.tools._registry import Tool, register


# § 188 ff. BGB Fristberechnung — vereinfachte deutsche Feiertage (bundesweit).
# Für rechtssichere Stichtagsberechnung müsste hier das jeweilige Bundesland-
# Feiertagskalender angeschlossen werden. Phase 1: Hinweis im Output.

_BUNDESWEITE_FEIERTAGE: dict[int, list[date]] = {}


def _ist_werktag(d: date) -> bool:
    return d.weekday() < 6  # Montag=0 bis Samstag=5 (Werktag i.S.d. § 193 BGB)


def _naechster_werktag(d: date) -> date:
    while not _ist_werktag(d):
        d += timedelta(days=1)
    return d


# --- Frist-Typen ---


def _ordentliche_kuendigung_mieter(start: date) -> dict[str, Any]:
    """§ 573c Abs. 1 BGB: Mieter kann immer mit 3 Monaten kündigen,
    spätestens am 3. Werktag des Monats zum Ablauf des übernächsten Monats."""
    # Wenn 'start' schon nach dem 3. Werktag, gilt die Kündigung erst im Folgemonat
    drei_werktag = _drei_werktage_nach_monatsanfang(start.year, start.month)
    if start <= drei_werktag:
        kuendigung_im_monat = start.month
        kuendigung_im_jahr = start.year
    else:
        nm = start + relativedelta(months=1)
        kuendigung_im_monat = nm.month
        kuendigung_im_jahr = nm.year

    ende = (
        date(kuendigung_im_jahr, kuendigung_im_monat, 1)
        + relativedelta(months=3)
        - timedelta(days=1)
    )
    return {
        "frist_typ": "ordentliche_kuendigung_mieter",
        "rechtsgrundlage": "§ 573c Abs. 1 BGB",
        "spaetester_zugang": _drei_werktage_nach_monatsanfang(start.year, start.month).isoformat(),
        "vertragsende": ende.isoformat(),
        "hinweis": (
            "Werktag i.S.d. § 193 BGB schließt Sonn- und Feiertage aus. "
            "Bundesländer-spezifische Feiertage werden in Phase 1 nicht "
            "berücksichtigt — Anwalt bitte prüfen."
        ),
    }


def _ordentliche_kuendigung_vermieter(start: date, ueberlassen_seit: date) -> dict[str, Any]:
    """§ 573c Abs. 1 BGB: Vermieter — verlängerte Frist nach 5/8 Jahren."""
    jahre = relativedelta(start, ueberlassen_seit).years
    if jahre >= 8:
        monate = 9
    elif jahre >= 5:
        monate = 6
    else:
        monate = 3

    drei_werktag = _drei_werktage_nach_monatsanfang(start.year, start.month)
    if start <= drei_werktag:
        kuendigung_im_monat = start.month
        kuendigung_im_jahr = start.year
    else:
        nm = start + relativedelta(months=1)
        kuendigung_im_monat = nm.month
        kuendigung_im_jahr = nm.year

    ende = (
        date(kuendigung_im_jahr, kuendigung_im_monat, 1)
        + relativedelta(months=monate)
        - timedelta(days=1)
    )
    return {
        "frist_typ": "ordentliche_kuendigung_vermieter",
        "rechtsgrundlage": "§ 573c Abs. 1 BGB",
        "mietdauer_jahre": jahre,
        "kuendigungsfrist_monate": monate,
        "spaetester_zugang": drei_werktag.isoformat(),
        "vertragsende": ende.isoformat(),
    }


def _verjaehrung_regulaer(start: date) -> dict[str, Any]:
    """§§ 195, 199 Abs. 1 BGB: 3 Jahre, beginnend mit Schluss des Jahres."""
    jahresende = date(start.year, 12, 31)
    verjaehrt_am = date(jahresende.year + 3, 12, 31)
    return {
        "frist_typ": "verjaehrung_regulaer",
        "rechtsgrundlage": "§§ 195, 199 Abs. 1 BGB",
        "fristbeginn": jahresende.isoformat(),
        "verjaehrt_am": verjaehrt_am.isoformat(),
    }


def _widerspruch_kuendigung(zugang_kuendigung: date, vertragsende: date) -> dict[str, Any]:
    """§ 574b Abs. 2 BGB: Widerspruch spätestens 2 Monate vor Beendigung."""
    spaetester_widerspruch = vertragsende - relativedelta(months=2)
    spaetester_widerspruch = _naechster_werktag(spaetester_widerspruch)
    return {
        "frist_typ": "widerspruch_kuendigung",
        "rechtsgrundlage": "§ 574b Abs. 2 BGB",
        "vertragsende": vertragsende.isoformat(),
        "spaetester_widerspruch": spaetester_widerspruch.isoformat(),
    }


def _drei_werktage_nach_monatsanfang(jahr: int, monat: int) -> date:
    d = date(jahr, monat, 1)
    werktage = 0
    while werktage < 3:
        if _ist_werktag(d):
            werktage += 1
            if werktage == 3:
                return d
        d += timedelta(days=1)
    return d


_DISPATCH = {
    "ordentliche_kuendigung_mieter": _ordentliche_kuendigung_mieter,
    "ordentliche_kuendigung_vermieter": _ordentliche_kuendigung_vermieter,
    "verjaehrung_regulaer": _verjaehrung_regulaer,
    "widerspruch_kuendigung": _widerspruch_kuendigung,
}


def run(input_data: dict[str, Any]) -> str:
    typ = str(input_data.get("typ", "")).strip()
    fn = _DISPATCH.get(typ)
    if not fn:
        verfuegbar = ", ".join(_DISPATCH.keys())
        return f"FEHLER: Unbekannter Fristtyp {typ!r}. Verfügbar: {verfuegbar}."

    try:
        if typ == "ordentliche_kuendigung_mieter":
            result = fn(parse_date(input_data["startdatum"]).date())
        elif typ == "ordentliche_kuendigung_vermieter":
            result = fn(
                parse_date(input_data["startdatum"]).date(),
                parse_date(input_data["ueberlassen_seit"]).date(),
            )
        elif typ == "verjaehrung_regulaer":
            result = fn(parse_date(input_data["startdatum"]).date())
        elif typ == "widerspruch_kuendigung":
            result = fn(
                parse_date(input_data["zugang_kuendigung"]).date(),
                parse_date(input_data["vertragsende"]).date(),
            )
        else:  # pragma: no cover
            result = {"FEHLER": "Dispatch-Lücke"}
    except KeyError as exc:
        return f"FEHLER: Pflichtparameter fehlt: {exc}"
    except ValueError as exc:
        return f"FEHLER: Datum konnte nicht geparst werden: {exc}"

    lines = [f"Fristberechnung ({typ}):"]
    for k, v in result.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


TOOL = register(
    Tool(
        name="berechne_frist",
        description=(
            "Berechnet juristische Fristen im Mietrecht (Kündigung, Widerspruch, Verjährung) "
            "deterministisch nach BGB. Verwende dieses Tool IMMER, wenn ein Datum ausgerechnet "
            "werden muss — schätze niemals selbst.\n\n"
            "Verfügbare Typen:\n"
            "- ordentliche_kuendigung_mieter (Parameter: startdatum)\n"
            "- ordentliche_kuendigung_vermieter (Parameter: startdatum, ueberlassen_seit)\n"
            "- verjaehrung_regulaer (Parameter: startdatum = Anspruchsentstehung)\n"
            "- widerspruch_kuendigung (Parameter: zugang_kuendigung, vertragsende)"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "typ": {
                    "type": "string",
                    "enum": list(_DISPATCH.keys()),
                    "description": "Fristtyp.",
                },
                "startdatum": {"type": "string", "description": "ISO-Datum oder deutsches Datum."},
                "ueberlassen_seit": {
                    "type": "string",
                    "description": "Beginn des Mietverhältnisses (für Vermieter-Kündigung).",
                },
                "zugang_kuendigung": {"type": "string"},
                "vertragsende": {"type": "string"},
            },
            "required": ["typ"],
        },
        run=run,
    )
)

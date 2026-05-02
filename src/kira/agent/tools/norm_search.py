"""Tool: search_norm — Volltext-Suche im lokalen Gesetzes-Korpus.

Hilft dem Agenten, die richtige Norm zu finden, wenn er nur Stichworte hat
(z.B. 'Schimmel', 'Eigenbedarf', 'Verjährung'). Liefert Treffer mit
Score und kurzem Auszug — der Agent muss anschließend lookup_norm aufrufen,
um den vollen Wortlaut zu erhalten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from kira.agent.tools._registry import Tool, register
from kira.knowledge.loader import list_gesetze, load_gesetz, stand_warnung
from kira.knowledge.schema import Norm


@dataclass
class SearchHit:
    norm: Norm
    score: int
    snippet: str


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _score_norm(norm: Norm, query_tokens: list[str]) -> tuple[int, str]:
    """Einfaches Scoring auf Titel + Volltext.

    Title-Treffer zählen 3x, Volltext-Treffer 1x. Substring-Matching, damit
    'Heizung' auch in 'Heizungsanlage' (deutsche Komposita) trifft. Gibt
    zusätzlich einen Auszug zurück, der den ersten Match-Bereich enthält.
    """
    if not query_tokens:
        return 0, ""

    body = norm.volltext
    titel_lower = norm.titel.lower()
    body_lower = body.lower()

    score = 0
    for t in query_tokens:
        if len(t) < 3:
            continue  # zu kurze Tokens (etwa 'in', 'zu') überspringen
        score += 3 * titel_lower.count(t)
        score += body_lower.count(t)

    if score == 0:
        return 0, ""

    # Snippet um den ersten Token-Match herum
    first_pos = -1
    for t in query_tokens:
        pos = body_lower.find(t)
        if pos != -1 and (first_pos == -1 or pos < first_pos):
            first_pos = pos
    if first_pos == -1:
        snippet = body[:160]
    else:
        start = max(0, first_pos - 60)
        end = min(len(body), first_pos + 160)
        snippet = ("…" if start > 0 else "") + body[start:end] + ("…" if end < len(body) else "")

    return score, snippet


def _gesetze_to_search(filter_abk: str | None) -> Iterable[str]:
    if filter_abk:
        yield filter_abk.lower()
        return
    yield from list_gesetze()


def run(input_data: dict[str, Any]) -> str:
    query = str(input_data.get("query", "")).strip()
    if not query:
        return "FEHLER: Keine Suchanfrage angegeben."

    gesetz_filter = input_data.get("gesetz")
    max_results = int(input_data.get("max_results", 5))

    tokens = _tokenize(query)
    if not tokens:
        return "FEHLER: Suchanfrage enthält keine durchsuchbaren Tokens."

    hits: list[SearchHit] = []
    warnungen: list[str] = []
    for key in _gesetze_to_search(gesetz_filter):
        gesetz = load_gesetz(key)
        if gesetz is None:
            continue
        warn = stand_warnung(gesetz.stand)
        if warn:
            warnungen.append(f"  - {gesetz.abkuerzung}: {warn}")
        for norm in gesetz.normen.values():
            score, snippet = _score_norm(norm, tokens)
            if score > 0:
                hits.append(SearchHit(norm=norm, score=score, snippet=snippet))

    if not hits:
        return (
            f"Keine Treffer im lokalen Korpus für: {query!r}.\n"
            f"Hinweis: Falls die Norm existieren sollte, 'kira ingest' ausführen "
            f"oder explizit per lookup_norm versuchen."
        )

    hits.sort(key=lambda h: h.score, reverse=True)
    top = hits[:max_results]

    lines = [f"Treffer für {query!r} (Top {len(top)} von {len(hits)}):", ""]
    for hit in top:
        lines.append(
            f"• {hit.norm.zitation} — {hit.norm.titel} (Score {hit.score})"
        )
        if hit.norm.abschnitt:
            lines.append(f"  Abschnitt: {hit.norm.abschnitt}")
        lines.append(f"  Auszug: {hit.snippet}")
        lines.append(f"  → Volltext via: lookup_norm(paragraph='{hit.norm.paragraph}', "
                     f"gesetz='{hit.norm.gesetz}')")
        lines.append("")

    if warnungen:
        lines.append("Stand-Warnungen:")
        lines.extend(warnungen)

    return "\n".join(lines).rstrip()


TOOL = register(
    Tool(
        name="search_norm",
        description=(
            "Volltext-Suche nach Stichworten im lokalen Gesetzes-Korpus "
            "(BGB, BetrKV, HeizkostenV). Nutze dieses Tool, wenn du den passenden "
            "§ noch nicht kennst, z.B. 'Schimmel Wohnraum', 'Eigenbedarf juristische "
            "Person', 'Heizkostenabrechnung Kürzungsrecht'. Liefert Treffer mit "
            "Auszug — den Volltext holst du anschließend per lookup_norm."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Stichworte, z.B. 'Schimmel Mietminderung'.",
                },
                "gesetz": {
                    "type": "string",
                    "description": "Optional: nur in einem Gesetz suchen (z.B. 'BGB').",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Anzahl Treffer (Default 5, Max 20).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        run=run,
    )
)

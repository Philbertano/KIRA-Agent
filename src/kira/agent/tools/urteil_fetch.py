"""Tool: search_rechtsprechung & fetch_urteil — deutsche Rechtsprechung.

Strenge Domain-Whitelist: der Agent kann NICHT auf beliebige URLs zugreifen.
Damit wird verhindert, dass aus Versehen ausländische Quellen oder Blogs
herangezogen werden.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup

from kira.agent.tools._registry import Tool, register


# Streng kuratierte Whitelist offizieller deutscher Rechtsprechungsquellen.
ALLOWED_DOMAINS: frozenset[str] = frozenset(
    {
        "rechtsprechung-im-internet.de",
        "www.rechtsprechung-im-internet.de",
        "openjur.de",
        "www.openjur.de",
        "dejure.org",
        "www.dejure.org",
        "bundesgerichtshof.de",
        "www.bundesgerichtshof.de",
        "gesetze-im-internet.de",
        "www.gesetze-im-internet.de",
    }
)

CACHE_DIR = Path("./data/cache/urteile")
USER_AGENT = "KIRA-Agent/0.1 (juristischer Junior-Assistent; Anwaltskanzlei)"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _is_allowed(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    return host in ALLOWED_DOMAINS


def _cache_key(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{digest}.html"


def _fetch_with_cache(url: str) -> str:
    cache_path = _cache_key(url)
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
        # Final-URL nochmal prüfen (Redirect könnte rausfallen)
        if not _is_allowed(str(response.url)):
            raise PermissionError(
                f"Redirect-Ziel {response.url!r} steht nicht auf der Whitelist."
            )
        text = response.text

    cache_path.write_text(text, encoding="utf-8")
    return text


def _extract_main_text(html: str, max_chars: int = 12000) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[…gekürzt…]"
    return text


# --- Tool 1: fetch_urteil (gezielter Abruf eines bekannten Urteils) ---


def run_fetch_urteil(input_data: dict[str, Any]) -> str:
    url = str(input_data.get("url", "")).strip()
    if not url:
        return "FEHLER: Keine URL angegeben."
    if not _is_allowed(url):
        allowed = ", ".join(sorted(ALLOWED_DOMAINS))
        return (
            f"FEHLER: Domain steht nicht auf der Whitelist. "
            f"Nur deutsche Rechtsprechungsquellen erlaubt: {allowed}."
        )
    try:
        html = _fetch_with_cache(url)
    except httpx.HTTPError as exc:
        return f"FEHLER beim Abruf: {exc}"
    except PermissionError as exc:
        return f"FEHLER: {exc}"

    body = _extract_main_text(html)
    return f"Quelle: {url}\n\n{body}"


FETCH_URTEIL = register(
    Tool(
        name="fetch_urteil",
        description=(
            "Lädt ein konkretes Urteil von einer deutschen Rechtsprechungsquelle "
            "(rechtsprechung-im-internet.de, openjur.de, dejure.org, bundesgerichtshof.de). "
            "Andere Domains sind nicht erlaubt. Verwende dieses Tool, um Urteilstext "
            "und Aktenzeichen verlässlich nachzuweisen, BEVOR du sie zitierst."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Vollständige URL des Urteils auf einer Whitelist-Domain.",
                }
            },
            "required": ["url"],
        },
        run=run_fetch_urteil,
    )
)


# --- Tool 2: search_rechtsprechung (semantische Suche via openjur.de) ---


def run_search_rechtsprechung(input_data: dict[str, Any]) -> str:
    query = str(input_data.get("query", "")).strip()
    gericht = str(input_data.get("gericht", "")).strip()
    if not query:
        return "FEHLER: Keine Suchanfrage angegeben."

    # openjur hat eine GET-Suche; gezielte Filter via Query-Erweiterung.
    full_query = query
    if gericht:
        full_query = f"{query} {gericht}"
    url = f"https://openjur.de/suche.html?q={quote_plus(full_query)}"

    try:
        html = _fetch_with_cache(url)
    except httpx.HTTPError as exc:
        return f"FEHLER beim Suchabruf: {exc}"

    soup = BeautifulSoup(html, "lxml")
    results: list[str] = []
    # openjur listet Treffer in <div class="result"> oder ähnlichem; wir extrahieren
    # robust alle Links ins eigene Urteilsarchiv.
    for link in soup.select("a[href*='/u/']"):
        href = link.get("href", "")
        title = link.get_text(strip=True)
        if not href or not title:
            continue
        full_url = href if href.startswith("http") else f"https://openjur.de{href}"
        results.append(f"- {title}\n  {full_url}")
        if len(results) >= 10:
            break

    if not results:
        return f"Keine Treffer für: {full_query!r} (Quelle: openjur.de)"

    return (
        f"Suche: {full_query!r} (Quelle: openjur.de)\n"
        f"Top-Treffer:\n\n" + "\n\n".join(results) + "\n\n"
        f"Verwende fetch_urteil mit einer dieser URLs, um den Volltext zu lesen."
    )


SEARCH_RECHTSPRECHUNG = register(
    Tool(
        name="search_rechtsprechung",
        description=(
            "Sucht deutsche Rechtsprechung (Schwerpunkt openjur.de). "
            "Gibt eine Liste relevanter Urteile mit URL zurück. "
            "Optional: Filter nach Gericht (z.B. 'BGH', 'LG Berlin'). "
            "Ausschließlich deutsche Quellen — keine ausländische Rechtsprechung."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Suchbegriffe, z.B. 'Mietminderung Schimmel Schlafzimmer'.",
                },
                "gericht": {
                    "type": "string",
                    "description": "Optional: Gerichtsfilter, z.B. 'BGH', 'LG Berlin', 'AG München'.",
                },
            },
            "required": ["query"],
        },
        run=run_search_rechtsprechung,
    )
)

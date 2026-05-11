"""Discover all Gesetze + Verordnungen via gii-toc.xml."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import quote
from xml.etree import ElementTree as ET

import httpx

GII_TOC_URL = "https://www.gesetze-im-internet.de/gii-toc.xml"

_REJECT_SLUG_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"bek$", re.IGNORECASE),
    re.compile(r"verfg$", re.IGNORECASE),
    re.compile(r"erl$", re.IGNORECASE),
    re.compile(r"vorschr$", re.IGNORECASE),
    re.compile(r"go\d*$", re.IGNORECASE),
    re.compile(r"geschoangleg$", re.IGNORECASE),
    re.compile(r"hauseigung$", re.IGNORECASE),
]
_REJECT_TITLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\(aufgehoben\)", re.IGNORECASE),
    re.compile(r"\(außer\s+Kraft\)", re.IGNORECASE),
]


@dataclass(frozen=True)
class TocEntry:
    title: str
    link: str


def parse_toc(raw_xml: bytes) -> list[TocEntry]:
    root = ET.fromstring(raw_xml)
    out: list[TocEntry] = []
    for item in root.iter("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        if title_el is None or link_el is None:
            continue
        title = (title_el.text or "").strip()
        link = (link_el.text or "").strip()
        if title and link:
            out.append(TocEntry(title=title, link=link))
    return out


def slug_for(link: str) -> str:
    parts = [p for p in link.split("/") if p]
    if len(parts) < 2:
        return ""
    return parts[-2].lower()


def is_citable(entry: TocEntry) -> bool:
    slug = slug_for(entry.link)
    for pat in _REJECT_SLUG_PATTERNS:
        if pat.search(slug):
            return False
    for pat in _REJECT_TITLE_PATTERNS:
        if pat.search(entry.title):
            return False
    return True


def fetch_toc(client: httpx.Client) -> list[TocEntry]:
    proxy = os.environ.get("LEGAL_INGEST_PROXY_URL")
    if proxy:
        url = f"{proxy.rstrip('/')}/?url={quote(GII_TOC_URL, safe='')}"
    else:
        url = GII_TOC_URL
    resp = client.get(url)
    resp.raise_for_status()
    return parse_toc(resp.content)

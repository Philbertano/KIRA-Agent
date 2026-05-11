"""V2 corpus types — per-paragraph storage layout.

V1's `GesetzKorpus` (whole-Gesetz blob) is intentionally absent; if you
encounter it in code, the call site is on V1 and needs migration.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Absatz(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nummer: str
    text: str


class Norm(BaseModel):
    """Single paragraph's content. Stored at gesetze/<abk>/<paragraph>.json."""

    model_config = ConfigDict(extra="ignore")

    gesetz: str
    paragraph: str
    titel: str = ""
    absaetze: list[Absatz] = Field(default_factory=list)
    quelle_url: str | None = None


class NormIndexEntry(BaseModel):
    """One entry in GesetzMeta.paragraphen — points at the per-paragraph file."""

    model_config = ConfigDict(extra="ignore")

    titel: str = ""
    key: str
    content_sha256: str


class GesetzMeta(BaseModel):
    """Per-Gesetz metadata. Stored at gesetze/<abk>/_meta.json."""

    model_config = ConfigDict(extra="ignore")

    abkuerzung: str
    titel: str
    type: Literal["Gesetz", "Verordnung"]
    stand: str
    quelle: str
    quelle_url: str
    upstream_xml_zip_url: str
    paragraphen: dict[str, NormIndexEntry] = Field(default_factory=dict)

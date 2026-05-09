"""Internal corpus types for legal_sources.

Re-defined locally so this module never imports from `kira.knowledge.*`.
The shape mirrors what `kira.knowledge.ingest` writes to S3, but is
maintained independently to honour the no-`kira.*`-imports rule.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Absatz(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nummer: str
    text: str


class Norm(BaseModel):
    model_config = ConfigDict(extra="ignore")

    paragraph: str
    titel: str = ""
    absaetze: list[Absatz] = Field(default_factory=list)
    quelle_url: str | None = None


class GesetzMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    abkuerzung: str
    titel: str
    stand: str  # ISO-Date
    quelle: str
    quelle_url: str
    gefiltert_auf: list[str] = Field(default_factory=list)
    anzahl_normen: int = 0


class GesetzKorpus(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    meta: GesetzMeta = Field(alias="_meta")
    paragraphen: dict[str, Norm] = Field(default_factory=dict)

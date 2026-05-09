"""Pydantic models for the lookup_norm tool — framework-free."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


_PARAGRAPH_PATTERN = re.compile(r"^\d+[a-zA-Z]?$")


class LookupNormInput(BaseModel):
    """Eingabe für das Tool `lookup_norm`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    gesetz: str = Field(..., min_length=1, description="Gesetz-Abkürzung, z.B. BGB.")
    paragraph: str = Field(..., min_length=1, description="Paragraph, z.B. '535' oder '535a'.")
    absatz: str | None = Field(default=None, description="Optional: konkreter Absatz.")

    @field_validator("gesetz")
    @classmethod
    def _normalize_gesetz(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("paragraph")
    @classmethod
    def _validate_paragraph(cls, v: str) -> str:
        v = v.strip()
        if not _PARAGRAPH_PATTERN.match(v):
            raise ValueError(
                f"Paragraph muss Format '<zahl>[<buchstabe>]' haben, war: {v!r}"
            )
        return v

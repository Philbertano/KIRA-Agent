"""Pydantic models for the lookup_norm tool — framework-free."""

from __future__ import annotations

import re
from enum import StrEnum

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


class LookupNormErrorCode(StrEnum):
    UNKNOWN_GESETZ = "unknown_gesetz"
    PARAGRAPH_NOT_FOUND = "paragraph_not_found"
    ABSATZ_NOT_FOUND = "absatz_not_found"
    CORPUS_UNAVAILABLE = "corpus_unavailable"
    VALIDATION_ERROR = "validation_error"


class LookupNormSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gesetz: str
    gesetz_titel: str
    paragraph: str
    absatz: str | None
    titel: str
    wortlaut: str
    stand: str
    quelle_url: str
    stand_warnung: str | None

    def to_agent_text(self) -> str:
        warn = f"\n\n⚠️ {self.stand_warnung}" if self.stand_warnung else ""
        absatz = f", Absatz {self.absatz}" if self.absatz else ""
        return (
            f"# {self.gesetz_titel} § {self.paragraph}{absatz} — {self.titel}\n\n"
            f"{self.wortlaut}\n\n"
            f"_Quelle: {self.quelle_url} | Stand: {self.stand}_{warn}"
        )


class LookupNormError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: LookupNormErrorCode
    message: str
    gesetz: str | None = None
    paragraph: str | None = None
    absatz: str | None = None

    def to_agent_text(self) -> str:
        return f"FEHLER ({self.error.value}): {self.message}"


LookupNormResult = LookupNormSuccess | LookupNormError

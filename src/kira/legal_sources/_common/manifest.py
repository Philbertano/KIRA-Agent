"""Manifest v2: catalog of all known Gesetze in the corpus."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ManifestVersionError(ValueError):
    """Raised when an incompatible manifest version is encountered."""


class GesetzManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    abkuerzung: str
    titel: str
    type: Literal["Gesetz", "Verordnung"]
    meta_key: str
    upstream_etag: str
    upstream_last_modified: str


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    stand: str
    gesetze: dict[str, GesetzManifestEntry]


def parse_manifest(payload: dict[str, Any]) -> Manifest:
    version = payload.get("version")
    if version != 2:
        raise ManifestVersionError(
            f"Unsupported manifest version {version!r} — V2 expects version 2. "
            "If you are reading a V1 manifest, run scripts/backfill_corpus.py "
            "to rewrite the corpus in V2 layout."
        )
    return Manifest.model_validate(payload)

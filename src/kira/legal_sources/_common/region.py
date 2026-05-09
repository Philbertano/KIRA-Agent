"""eu-central-1 region pinning for legal_sources AWS resources."""

from __future__ import annotations

REQUIRED_REGION: str = "eu-central-1"


def ensure_eu_region(region: str | None) -> None:
    if region != REQUIRED_REGION:
        raise RuntimeError(
            f"legal_sources requires region {REQUIRED_REGION!r}, got {region!r}. "
            "Refusing to operate outside eu-central-1 for data-residency reasons."
        )

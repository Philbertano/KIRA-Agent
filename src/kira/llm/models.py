"""Modell-IDs für verschiedene Backends.

Wir trennen logische Modell-Stufen (haiku/sonnet/opus) von konkreten Modell-IDs,
damit der Router gegen Tier-Namen routet und der Wechsel von Direct-API zu
Bedrock zu Vertex nur eine Mapping-Frage ist.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal


class ModelTier(str, Enum):
    """Logische Modellstufen, die der Router verwendet."""

    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


Backend = Literal["bedrock_eu", "anthropic_direct"]


MODEL_IDS: dict[Backend, dict[ModelTier, str]] = {
    "bedrock_eu": {
        ModelTier.HAIKU: "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
        ModelTier.SONNET: "eu.anthropic.claude-sonnet-4-6-v1:0",
        ModelTier.OPUS: "eu.anthropic.claude-opus-4-6-v1:0",
    },
    "anthropic_direct": {
        ModelTier.HAIKU: "claude-haiku-4-5-20251001",
        ModelTier.SONNET: "claude-sonnet-4-6",
        ModelTier.OPUS: "claude-opus-4-7",
    },
}

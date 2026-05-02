"""Client-Factory für Anthropic-Modelle.

Default ist AWS Bedrock in eu-central-1 (DSGVO/§43e BRAO konform).
Direct-API ist nur für lokale Entwicklung mit Synthetik-Daten gedacht.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from anthropic import Anthropic, AnthropicBedrock

from kira.llm.models import MODEL_IDS, Backend, ModelTier


@dataclass(frozen=True)
class LLMClient:
    """Wrapper, der Backend + Modell-Mapping zusammenhält."""

    backend: Backend
    raw: Any  # Anthropic | AnthropicBedrock — beide haben dieselbe messages-API

    def model_id(self, tier: ModelTier) -> str:
        return MODEL_IDS[self.backend][tier]


def build_client(backend: Backend | None = None) -> LLMClient:
    """Baut den LLM-Client gemäß Konfiguration.

    Standard: Bedrock EU. Override via KIRA_BACKEND=anthropic_direct
    nur für Tests / Synthetik-Daten.
    """
    backend = backend or os.environ.get("KIRA_BACKEND", "bedrock_eu")  # type: ignore[assignment]

    if backend == "bedrock_eu":
        region = os.environ.get("AWS_REGION", "eu-central-1")
        if not region.startswith("eu-"):
            raise RuntimeError(
                f"Bedrock-Backend muss in einer EU-Region laufen, "
                f"AWS_REGION={region!r} ist nicht zulässig."
            )
        raw = AnthropicBedrock(aws_region=region)
        return LLMClient(backend="bedrock_eu", raw=raw)

    if backend == "anthropic_direct":
        if os.environ.get("KIRA_ALLOW_DIRECT_API") != "1":
            raise RuntimeError(
                "Direct-API-Backend ist deaktiviert. Mandantendaten dürfen "
                "nicht über die US-API gesendet werden. Zum Aktivieren für "
                "Synthetik-Tests: KIRA_ALLOW_DIRECT_API=1 setzen."
            )
        raw = Anthropic()
        return LLMClient(backend="anthropic_direct", raw=raw)

    raise ValueError(f"Unbekanntes Backend: {backend}")

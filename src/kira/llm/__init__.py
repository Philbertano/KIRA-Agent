"""LLM-Client-Abstraktion (AWS Bedrock, Direct API als Fallback)."""

from kira.llm.client import build_client
from kira.llm.models import MODEL_IDS, ModelTier

__all__ = ["build_client", "MODEL_IDS", "ModelTier"]

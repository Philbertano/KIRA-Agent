"""Cohere multilingual v3 embedder via Bedrock InvokeModel."""

from __future__ import annotations

import json
from typing import Any, Literal

from botocore.exceptions import ClientError

from kira.legal_sources._common.errors import EmbeddingUnavailableError

EMBEDDING_DIMENSION = 1024
MODEL_ID = "cohere.embed-multilingual-v3"
DEFAULT_BATCH_SIZE = 96
DEFAULT_MAX_CHARS = 6000


class CohereMultilingualEmbedder:
    """Wraps `bedrock-runtime:InvokeModel` for Cohere multilingual v3.

    Splits inputs into batches of `batch_size` (Cohere's per-request cap is 96).
    Truncates each input to `max_chars` characters before sending.
    """

    def __init__(
        self,
        *,
        bedrock_client: Any,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        self._client = bedrock_client
        self._batch_size = batch_size
        self._max_chars = max_chars

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._invoke(texts, input_type="search_document")

    def embed_query(self, text: str) -> list[float]:
        result = self._invoke([text], input_type="search_query")
        return result[0]

    def _invoke(
        self,
        texts: list[str],
        *,
        input_type: Literal["search_document", "search_query"],
    ) -> list[list[float]]:
        if not texts:
            return []
        truncated = [t[: self._max_chars] for t in texts]
        out: list[list[float]] = []
        for start in range(0, len(truncated), self._batch_size):
            batch = truncated[start : start + self._batch_size]
            try:
                response = self._client.invoke_model(
                    modelId=MODEL_ID,
                    contentType="application/json",
                    accept="application/json",
                    body=json.dumps(
                        {
                            "texts": batch,
                            "input_type": input_type,
                            "embedding_types": ["float"],
                        }
                    ),
                )
            except ClientError as exc:
                raise EmbeddingUnavailableError(
                    f"Bedrock InvokeModel failed: {exc}"
                ) from exc
            payload = json.loads(response["body"].read())
            out.extend(payload["embeddings"])
        return out

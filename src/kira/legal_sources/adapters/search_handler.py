"""Lambda entrypoint for search_norm.

No S3 corpus access. Holds a Bedrock client + S3 Vectors client at module
scope so warm invocations skip client construction.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from pydantic import ValidationError

from kira.legal_sources._common.embedder import CohereMultilingualEmbedder
from kira.legal_sources._common.region import REQUIRED_REGION
from kira.legal_sources._common.vector_index import VectorIndex
from kira.legal_sources.gesetze.schema import (
    SearchNormError,
    SearchNormErrorCode,
    SearchNormInput,
)
from kira.legal_sources.gesetze.search_norm import search_norm

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

_INDEX_NAME = os.environ.get("LEGAL_VECTOR_INDEX_NAME", "kira-legal-norms")

_embedder = CohereMultilingualEmbedder(
    bedrock_client=boto3.client("bedrock-runtime", region_name=REQUIRED_REGION),
)
_index = VectorIndex(
    s3vectors_client=boto3.client("s3vectors", region_name=REQUIRED_REGION),
    index_name=_INDEX_NAME,
)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    args = event.get("input") if isinstance(event, dict) and "input" in event else event
    try:
        payload = SearchNormInput.model_validate(args if isinstance(args, dict) else {})
    except ValidationError as exc:
        return _err(SearchNormErrorCode.VALIDATION_ERROR, str(exc))
    result = search_norm(
        payload,
        embed=_embedder.embed_query,
        search=_index.query,
    )
    is_error = isinstance(result, SearchNormError)
    body = result.model_dump_json()
    log.info(
        "search_norm",
        extra={
            "query_len": len(payload.query),
            "k": payload.k,
            "hits": 0 if is_error else len(result.hits),
            "is_error": is_error,
        },
    )
    return {"isError": is_error, "content": [{"type": "text", "text": body}]}


def _err(code: SearchNormErrorCode, message: str) -> dict[str, Any]:
    body = SearchNormError(error=code, message=message).model_dump_json()
    return {"isError": True, "content": [{"type": "text", "text": body}]}

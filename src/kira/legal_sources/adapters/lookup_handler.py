"""AWS Lambda handler for the lookup_norm tool, invoked by AgentCore Gateway."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.s3_corpus import CorpusLoader
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import (
    LookupNormError,
    LookupNormErrorCode,
    LookupNormInput,
)


log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# Module-level loader: warm Lambdas reuse the same /tmp cache and manifest etag.
_LOADER = CorpusLoader.from_env()


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    args = _extract_args(event)
    try:
        payload = LookupNormInput.model_validate(args)
    except ValidationError as exc:
        return _err(LookupNormErrorCode.VALIDATION_ERROR, str(exc))
    try:
        corpus = _LOADER.load_all()
    except CorpusUnavailableError as exc:
        return _err(LookupNormErrorCode.CORPUS_UNAVAILABLE, str(exc))
    result = lookup_norm(payload, corpus=corpus)
    body = result.model_dump_json()
    is_error = isinstance(result, LookupNormError)
    log.info(
        "lookup_norm invocation",
        extra={
            "gesetz": payload.gesetz,
            "paragraph": payload.paragraph,
            "absatz": payload.absatz,
            "is_error": is_error,
            "corpus_stand": getattr(result, "stand", None),
        },
    )
    return {"isError": is_error, "content": [{"type": "text", "text": body}]}


def _extract_args(event: dict[str, Any]) -> dict[str, Any]:
    if isinstance(event, dict) and "input" in event and isinstance(event["input"], dict):
        return event["input"]
    return event if isinstance(event, dict) else {}


def _err(code: LookupNormErrorCode, message: str) -> dict[str, Any]:
    body = LookupNormError(error=code, message=message).model_dump_json()
    return {"isError": True, "content": [{"type": "text", "text": body}]}

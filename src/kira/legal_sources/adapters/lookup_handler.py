"""Lambda entrypoint for lookup_norm — wires lazy-load against the
LazyCorpusLoader."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.s3_corpus import LazyCorpusLoader
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import (
    LookupNormError,
    LookupNormErrorCode,
    LookupNormInput,
)

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# Module-level loader: warm Lambdas reuse the same /tmp + memory caches.
_LOADER = LazyCorpusLoader.from_env()


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    args = event.get("input") if isinstance(event, dict) and "input" in event else event
    try:
        payload = LookupNormInput.model_validate(args if isinstance(args, dict) else {})
    except ValidationError as exc:
        return _err(LookupNormErrorCode.VALIDATION_ERROR, str(exc))
    try:
        result = lookup_norm(
            payload,
            load_meta=_LOADER.load_meta,
            load_norm=lambda _gesetz, key: _LOADER.load_norm(key),
        )
    except CorpusUnavailableError as exc:
        return _err(LookupNormErrorCode.CORPUS_UNAVAILABLE, str(exc))
    body = result.model_dump_json()
    is_error = isinstance(result, LookupNormError)
    log.info(
        "lookup_norm",
        extra={
            "gesetz": payload.gesetz,
            "paragraph": payload.paragraph,
            "absatz": payload.absatz,
            "is_error": is_error,
        },
    )
    return {"isError": is_error, "content": [{"type": "text", "text": body}]}


def _err(code: LookupNormErrorCode, message: str) -> dict[str, Any]:
    body = LookupNormError(error=code, message=message).model_dump_json()
    return {"isError": True, "content": [{"type": "text", "text": body}]}

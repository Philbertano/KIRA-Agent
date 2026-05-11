"""Pure function: rank paragraphs against a query using injected embed + search."""

from __future__ import annotations

import logging
from typing import Any, Callable

from kira.legal_sources._common.errors import (
    CorpusUnavailableError,
    EmbeddingUnavailableError,
)
from kira.legal_sources._common.vector_index import VectorSearchHit
from kira.legal_sources.gesetze.schema import (
    SearchNormError,
    SearchNormErrorCode,
    SearchNormHit,
    SearchNormInput,
    SearchNormResult,
    SearchNormSuccess,
)

log = logging.getLogger(__name__)

EmbedFn = Callable[[str], list[float]]
SearchFn = Callable[..., list[VectorSearchHit]]

_REQUIRED_METADATA_FIELDS = ("gesetz", "paragraph", "titel", "wortlaut", "quelle_url", "stand")


def search_norm(
    input_data: SearchNormInput,
    *,
    embed: EmbedFn,
    search: SearchFn,
) -> SearchNormResult:
    """Embed the query, search vector index, format results.

    `embed` and `search` are injected so this function has no AWS deps and is
    fully unit-testable.
    """
    try:
        vector = embed(input_data.query)
    except EmbeddingUnavailableError as exc:
        return SearchNormError(
            error=SearchNormErrorCode.EMBEDDING_UNAVAILABLE,
            message=str(exc),
        )

    metadata_filter = _build_filter(input_data)

    try:
        raw_hits = search(
            vector=vector,
            k=input_data.k,
            metadata_filter=metadata_filter,
        )
    except CorpusUnavailableError as exc:
        return SearchNormError(
            error=SearchNormErrorCode.CORPUS_UNAVAILABLE,
            message=str(exc),
        )

    hits: list[SearchNormHit] = []
    for raw in raw_hits:
        formatted = _format_hit(raw)
        if formatted is not None:
            hits.append(formatted)

    return SearchNormSuccess(query=input_data.query, hits=hits)


def _build_filter(input_data: SearchNormInput) -> dict[str, Any] | None:
    f: dict[str, Any] = {}
    if input_data.gesetz_filter:
        f["abkuerzung"] = {"$in": input_data.gesetz_filter}
    if input_data.type_filter:
        f["type"] = {"$in": list(input_data.type_filter)}
    return f or None


def _format_hit(raw: VectorSearchHit) -> SearchNormHit | None:
    md = raw.metadata or {}
    missing = [f for f in _REQUIRED_METADATA_FIELDS if f not in md]
    if missing:
        log.warning(
            "Skipping hit %s — missing metadata fields: %s",
            raw.key,
            missing,
        )
        return None
    return SearchNormHit(
        gesetz=md["gesetz"],
        paragraph=md["paragraph"],
        absatz=md.get("absatz"),
        titel=md["titel"],
        wortlaut=md["wortlaut"],
        quelle_url=md["quelle_url"],
        stand=md["stand"],
        score=raw.score,
    )

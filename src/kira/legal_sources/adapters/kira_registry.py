"""KIRA `Tool` registry adapter for lookup_norm and search_norm.

This adapter is the only place inside legal_sources/adapters/ that imports
from kira.*. It is NOT auto-registered on import — callers explicitly call
`build_lookup_norm_tool()` and `build_search_norm_tool()` and register the
results with their loop.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from kira.agent.tools._registry import Tool
from kira.legal_sources._common.embedder import CohereMultilingualEmbedder
from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.s3_corpus import LazyCorpusLoader
from kira.legal_sources._common.vector_index import VectorIndex
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import LookupNormInput, SearchNormInput
from kira.legal_sources.gesetze.search_norm import search_norm

_LOOKUP_DESCRIPTION = (
    "Lädt den autoritativen Wortlaut eines deutschen Paragraphen aus "
    "gesetze-im-internet.de (via S3-gepflegtem Korpus). Eingaben: "
    "gesetz (z.B. 'BGB'), paragraph (z.B. '535' oder '535a'), "
    "absatz (optional, z.B. '1'). NUR autoritative Quellen — keine "
    "Aggregatoren, keine ausländischen Quellen."
)

_SEARCH_DESCRIPTION = (
    "Semantische Suche über alle deutschen Bundesgesetze + Rechtsverordnungen. "
    "Eingabe: query (freier Text, z.B. 'Pflichten des Vermieters zur Erhaltung "
    "der Mietsache'). Optional: k (1-50, Default 10), gesetz_filter (Liste von "
    "Abkürzungen), type_filter (['Gesetz'] oder ['Verordnung']). Liefert "
    "rangbasierte Treffer mit Wortlaut. Suche dient der Auffindung — für die "
    "autoritative Zitation IMMER zusätzlich lookup_norm aufrufen."
)


def build_lookup_norm_tool(*, loader: LazyCorpusLoader | None = None) -> Tool:
    loader = loader or LazyCorpusLoader.from_env()

    def _run(input_data: dict[str, Any]) -> str:
        try:
            payload = LookupNormInput.model_validate(input_data)
        except ValidationError as exc:
            return f"FEHLER (validation_error): {exc}"
        try:
            result = lookup_norm(
                payload,
                load_meta=loader.load_meta,
                load_norm=lambda abk, key: loader.load_norm(key),
            )
        except CorpusUnavailableError as exc:
            return f"FEHLER (corpus_unavailable): {exc}"
        return result.to_agent_text()

    return Tool(
        name="lookup_norm",
        description=_LOOKUP_DESCRIPTION,
        input_schema=LookupNormInput.model_json_schema(),
        run=_run,
    )


def build_search_norm_tool(
    *,
    embedder: CohereMultilingualEmbedder | None = None,
    index: VectorIndex | None = None,
) -> Tool:
    if embedder is None or index is None:
        raise ValueError(
            "build_search_norm_tool requires embedder + index dependencies"
        )

    def _run(input_data: dict[str, Any]) -> str:
        try:
            payload = SearchNormInput.model_validate(input_data)
        except ValidationError as exc:
            return f"FEHLER (validation_error): {exc}"
        result = search_norm(
            payload, embed=embedder.embed_query, search=index.query
        )
        return result.to_agent_text()

    return Tool(
        name="search_norm",
        description=_SEARCH_DESCRIPTION,
        input_schema=SearchNormInput.model_json_schema(),
        run=_run,
    )

"""Claude Agent SDK `@tool` wrapper for lookup_norm.

We expose two surfaces:

  - `make_lookup_norm_tool_function()` — returns a plain async callable that
    can be wrapped with `@tool` by the consumer. Easy to unit-test, no SDK
    dependency at import time.
  - `make_sdk_tool()` — convenience that imports `claude_agent_sdk` lazily
    and returns the decorated tool. Optional; consumer code may build its
    own decoration.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.s3_corpus import LazyCorpusLoader
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import LookupNormInput

TOOL_DESCRIPTION = (
    "Lädt den autoritativen Wortlaut eines deutschen Paragraphen aus "
    "gesetze-im-internet.de (via S3-gepflegtem Korpus). Eingaben: "
    "gesetz (z.B. 'BGB'), paragraph (z.B. '535' oder '535a'), "
    "absatz (optional, z.B. '1'). NUR autoritative Quellen — keine "
    "Aggregatoren, keine ausländischen Quellen."
)


def make_lookup_norm_tool_function(
    *, loader: LazyCorpusLoader | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    loader = loader or LazyCorpusLoader.from_env()

    async def _impl(args: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = LookupNormInput.model_validate(args)
        except ValidationError as exc:
            return _text(f"validation_error: {exc}")
        try:
            result = lookup_norm(
                payload,
                load_meta=loader.load_meta,
                load_norm=lambda abk, key: loader.load_norm(key),
            )
        except CorpusUnavailableError as exc:
            return _text(f"corpus_unavailable: {exc}")
        return _text(result.to_agent_text())

    return _impl


def make_sdk_tool(*, loader: LazyCorpusLoader | None = None):
    """Optional: wrap the function with claude_agent_sdk's @tool decorator."""
    from claude_agent_sdk import tool  # local import; SDK is optional dep

    fn = make_lookup_norm_tool_function(loader=loader)
    schema = LookupNormInput.model_json_schema()
    return tool("lookup_norm", TOOL_DESCRIPTION, schema)(fn)


def _text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


from kira.legal_sources._common.embedder import CohereMultilingualEmbedder
from kira.legal_sources._common.vector_index import VectorIndex
from kira.legal_sources.gesetze.schema import SearchNormInput
from kira.legal_sources.gesetze.search_norm import search_norm


def make_search_norm_tool_function(
    *,
    embedder: CohereMultilingualEmbedder,
    index: VectorIndex,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    async def _impl(args: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = SearchNormInput.model_validate(args)
        except ValidationError as exc:
            return _text(f"validation_error: {exc}")
        result = search_norm(payload, embed=embedder.embed_query, search=index.query)
        return _text(result.to_agent_text())

    return _impl

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
from kira.legal_sources._common.s3_corpus import CorpusLoader
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import LookupNormInput


def make_lookup_norm_tool_function(
    *, loader: CorpusLoader | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    loader = loader or CorpusLoader.from_env()

    async def _impl(args: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = LookupNormInput.model_validate(args)
        except ValidationError as exc:
            return _text(f"validation_error: {exc}")
        try:
            corpus = loader.load_all()
        except CorpusUnavailableError as exc:
            return _text(f"corpus_unavailable: {exc}")
        return _text(lookup_norm(payload, corpus=corpus).to_agent_text())

    return _impl


def make_sdk_tool(*, loader: CorpusLoader | None = None):
    """Optional: wrap the function with claude_agent_sdk's @tool decorator."""
    from claude_agent_sdk import tool  # local import; SDK is optional dep

    fn = make_lookup_norm_tool_function(loader=loader)
    schema = LookupNormInput.model_json_schema()
    return tool("lookup_norm", "…", schema)(fn)


def _text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}

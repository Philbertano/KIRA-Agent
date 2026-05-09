"""KIRA `Tool` registry adapter for lookup_norm.

This adapter is the only place inside legal_sources/adapters/ that imports
from kira.*. It is NOT auto-registered on import — callers explicitly call
`build_lookup_norm_tool()` and register the result with their loop.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from kira.agent.tools._registry import Tool
from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.s3_corpus import CorpusLoader
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import LookupNormInput


_DESCRIPTION = (
    "Lädt den autoritativen Wortlaut eines deutschen Paragraphen aus "
    "gesetze-im-internet.de (via S3-gepflegtem Korpus). Eingaben: "
    "gesetz (z.B. 'BGB'), paragraph (z.B. '535' oder '535a'), "
    "absatz (optional, z.B. '1'). NUR autoritative Quellen — keine "
    "Aggregatoren, keine ausländischen Quellen."
)


def build_lookup_norm_tool(*, loader: CorpusLoader | None = None) -> Tool:
    loader = loader or CorpusLoader.from_env()

    def _run(input_data: dict[str, Any]) -> str:
        try:
            payload = LookupNormInput.model_validate(input_data)
        except ValidationError as exc:
            return f"FEHLER (validation_error): {exc}"
        try:
            corpus = loader.load_all()
        except CorpusUnavailableError as exc:
            return f"FEHLER (corpus_unavailable): {exc}"
        result = lookup_norm(payload, corpus=corpus)
        return result.to_agent_text()

    return Tool(
        name="lookup_norm",
        description=_DESCRIPTION,
        input_schema=LookupNormInput.model_json_schema(),
        run=_run,
    )

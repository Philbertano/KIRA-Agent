"""Cross-tool error hierarchy for legal_sources."""

from __future__ import annotations


class ToolError(Exception):
    code: str = "tool_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        # If `code` is provided explicitly, override the class attribute on
        # this instance; otherwise the subclass's class-level `code` wins.
        if code is not None:
            self.code = code
        self.message = message
        super().__init__(f"{self.code}: {message}")


class CorpusUnavailableError(ToolError):
    code = "corpus_unavailable"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class EmbeddingUnavailableError(ToolError):
    code = "embedding_unavailable"

    def __init__(self, message: str) -> None:
        super().__init__(message)

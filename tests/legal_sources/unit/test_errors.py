import pytest

from kira.legal_sources._common.errors import CorpusUnavailableError, ToolError


def test_tool_error_carries_code_and_message():
    err = ToolError(code="custom", message="boom")
    assert err.code == "custom"
    assert str(err) == "custom: boom"


def test_corpus_unavailable_is_tool_error():
    err = CorpusUnavailableError("S3 GET failed")
    assert isinstance(err, ToolError)
    assert err.code == "corpus_unavailable"
    with pytest.raises(ToolError):
        raise err

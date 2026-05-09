import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def staged_local_corpus(tmp_path, monkeypatch):
    target = tmp_path / "gesetze"
    target.mkdir()
    (target / "bgb.json").write_text(
        (FIXTURES / "bgb_subset.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))


def test_handler_direct_invoke_shape(staged_local_corpus):
    from kira.legal_sources.adapters.lookup_handler import handler

    out = handler({"gesetz": "BGB", "paragraph": "535"}, context=None)
    assert out["isError"] is False
    body = json.loads(out["content"][0]["text"])
    assert body["paragraph"] == "535"


def test_handler_agentcore_gateway_shape(staged_local_corpus):
    from kira.legal_sources.adapters.lookup_handler import handler

    event = {
        "tool_name": "lookup_norm",
        "tool_use_id": "abc-123",
        "input": {"gesetz": "BGB", "paragraph": "535", "absatz": "2"},
    }
    out = handler(event, context=None)
    assert out["isError"] is False
    body = json.loads(out["content"][0]["text"])
    assert body["absatz"] == "2"


def test_handler_validation_error_isolated(staged_local_corpus):
    from kira.legal_sources.adapters.lookup_handler import handler

    out = handler({"input": {"gesetz": "", "paragraph": ""}}, context=None)
    assert out["isError"] is True
    assert "validation_error" in out["content"][0]["text"]


def test_handler_corpus_unavailable_returns_error(monkeypatch):
    monkeypatch.delenv("LEGAL_CORPUS_LOCAL_DIR", raising=False)
    monkeypatch.delenv("LEGAL_CORPUS_BUCKET", raising=False)
    # Need to reset module-level loader cache between tests.
    import importlib
    import kira.legal_sources.adapters.lookup_handler as mod
    importlib.reload(mod)

    out = mod.handler({"gesetz": "BGB", "paragraph": "535"}, context=None)
    assert out["isError"] is True
    assert "corpus_unavailable" in out["content"][0]["text"]

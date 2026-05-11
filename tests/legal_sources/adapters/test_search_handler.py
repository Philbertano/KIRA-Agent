import importlib
import json
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def aws_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("LEGAL_VECTOR_INDEX_NAME", "kira-legal-norms")


def _hit_metadata() -> dict:
    return {
        "gesetz": "BGB",
        "paragraph": "535",
        "abkuerzung": "BGB",
        "type": "Gesetz",
        "titel": "Inhalt und Hauptpflichten des Mietvertrags",
        "wortlaut": "(1) Durch den Mietvertrag ...",
        "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
        "stand": "2026-05-09",
    }


def _reload_handler():
    import kira.legal_sources.adapters.search_handler as mod
    importlib.reload(mod)
    return mod


def test_search_handler_happy_path():
    mod = _reload_handler()

    from kira.legal_sources._common.vector_index import VectorSearchHit

    with patch.object(mod, "_embedder") as mock_embedder, patch.object(
        mod, "_index"
    ) as mock_index:
        mock_embedder.embed_query.return_value = [0.1] * 1024
        mock_index.query.return_value = [
            VectorSearchHit(key="bgb-535", score=0.94, metadata=_hit_metadata())
        ]
        out = mod.handler({"query": "Mietminderung Schimmel", "k": 3}, None)

    assert out["isError"] is False
    body = json.loads(out["content"][0]["text"])
    assert body["hits"][0]["gesetz"] == "BGB"
    assert body["hits"][0]["score"] == 0.94


def test_search_handler_agentcore_shape():
    mod = _reload_handler()
    from kira.legal_sources._common.vector_index import VectorSearchHit

    with patch.object(mod, "_embedder") as mock_embedder, patch.object(
        mod, "_index"
    ) as mock_index:
        mock_embedder.embed_query.return_value = [0.0] * 1024
        mock_index.query.return_value = [
            VectorSearchHit(key="bgb-535", score=0.5, metadata=_hit_metadata())
        ]
        out = mod.handler(
            {
                "tool_name": "search_norm",
                "tool_use_id": "x",
                "input": {"query": "x"},
            },
            None,
        )
    assert out["isError"] is False
    body = json.loads(out["content"][0]["text"])
    assert body["query"] == "x"


def test_search_handler_embedding_failure():
    mod = _reload_handler()
    from kira.legal_sources._common.errors import EmbeddingUnavailableError

    with patch.object(mod, "_embedder") as mock_embedder:
        mock_embedder.embed_query.side_effect = EmbeddingUnavailableError("down")
        out = mod.handler({"query": "x"}, None)
    assert out["isError"] is True
    assert "embedding_unavailable" in out["content"][0]["text"]


def test_search_handler_validation_error():
    mod = _reload_handler()
    out = mod.handler({"query": ""}, None)
    assert out["isError"] is True
    assert "validation_error" in out["content"][0]["text"]


def test_search_handler_passes_gesetz_filter():
    mod = _reload_handler()

    with patch.object(mod, "_embedder") as mock_embedder, patch.object(
        mod, "_index"
    ) as mock_index:
        mock_embedder.embed_query.return_value = [0.0] * 1024
        mock_index.query.return_value = []
        mod.handler(
            {"query": "x", "gesetz_filter": ["BGB"], "type_filter": ["Gesetz"]}, None
        )

    kwargs = mock_index.query.call_args.kwargs
    assert kwargs["metadata_filter"] == {
        "abkuerzung": {"$in": ["bgb"]},
        "type": {"$in": ["Gesetz"]},
    }

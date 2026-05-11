import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from kira.legal_sources._common.embedder import (
    EMBEDDING_DIMENSION,
    CohereMultilingualEmbedder,
)
from kira.legal_sources._common.errors import EmbeddingUnavailableError

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _fake_bedrock_client(response_payload: dict) -> MagicMock:
    client = MagicMock()
    body = MagicMock()
    body.read.return_value = json.dumps(response_payload).encode("utf-8")
    client.invoke_model.return_value = {"body": body}
    return client


def test_embed_documents_returns_vectors_in_order():
    response = json.loads((FIXTURES / "cohere_embed_response.json").read_text())
    client = _fake_bedrock_client(response)
    embedder = CohereMultilingualEmbedder(bedrock_client=client)
    vectors = embedder.embed_documents(["a", "b"])
    assert len(vectors) == 2
    assert len(vectors[0]) == EMBEDDING_DIMENSION
    kwargs = client.invoke_model.call_args.kwargs
    body = json.loads(kwargs["body"])
    assert body["input_type"] == "search_document"
    assert body["texts"] == ["a", "b"]


def test_embed_query_uses_search_query_input_type():
    response = json.loads((FIXTURES / "cohere_embed_response.json").read_text())
    client = _fake_bedrock_client(response)
    embedder = CohereMultilingualEmbedder(bedrock_client=client)
    embedder.embed_query("Pflichten des Vermieters")
    body = json.loads(client.invoke_model.call_args.kwargs["body"])
    assert body["input_type"] == "search_query"
    assert body["texts"] == ["Pflichten des Vermieters"]


def test_embed_documents_chunks_above_batch_limit():
    response = json.loads((FIXTURES / "cohere_embed_response.json").read_text())
    # Pad response so each batch returns the expected length.
    big_response = {**response, "embeddings": [response["embeddings"][0]] * 96}
    client = _fake_bedrock_client(big_response)
    embedder = CohereMultilingualEmbedder(bedrock_client=client, batch_size=96)
    inputs = ["x"] * 200
    embedder.embed_documents(inputs)
    # 200 inputs / 96 batch = 3 calls
    assert client.invoke_model.call_count == 3


def test_embed_documents_truncates_long_input():
    response = json.loads((FIXTURES / "cohere_embed_response.json").read_text())
    client = _fake_bedrock_client(response)
    embedder = CohereMultilingualEmbedder(bedrock_client=client, max_chars=100)
    embedder.embed_documents(["x" * 500, "y" * 500])
    body = json.loads(client.invoke_model.call_args.kwargs["body"])
    assert all(len(t) <= 100 for t in body["texts"])


def test_bedrock_client_error_maps_to_embedding_unavailable():
    client = MagicMock()
    client.invoke_model.side_effect = ClientError(
        error_response={"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
        operation_name="InvokeModel",
    )
    embedder = CohereMultilingualEmbedder(bedrock_client=client)
    with pytest.raises(EmbeddingUnavailableError) as excinfo:
        embedder.embed_documents(["x"])
    assert "ThrottlingException" in str(excinfo.value)


def test_empty_input_returns_empty_list_without_calling_bedrock():
    client = MagicMock()
    embedder = CohereMultilingualEmbedder(bedrock_client=client)
    assert embedder.embed_documents([]) == []
    assert client.invoke_model.call_count == 0


def test_handles_real_cohere_response_with_nested_float_key():
    """Cohere v3 with `embedding_types: ["float"]` wraps the list under
    `embeddings.float`. The parser must unwrap that shape."""
    real_shape_response = {
        "id": "abc",
        "texts": ["a", "b"],
        "embeddings": {"float": [[0.1] * 1024, [0.2] * 1024]},
        "response_type": "embeddings_by_type",
    }
    client = _fake_bedrock_client(real_shape_response)
    embedder = CohereMultilingualEmbedder(bedrock_client=client)
    vectors = embedder.embed_documents(["a", "b"])
    assert len(vectors) == 2
    assert vectors[0][0] == 0.1
    assert vectors[1][0] == 0.2

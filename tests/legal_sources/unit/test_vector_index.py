from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.vector_index import (
    VectorIndex,
    VectorRecord,
    VectorSearchHit,
)


def test_upsert_passes_vectors_with_metadata_to_client():
    client = MagicMock()
    idx = VectorIndex(s3vectors_client=client, index_name="kira-legal-norms")
    records = [
        VectorRecord(key="bgb-535", vector=[0.1] * 1024, metadata={"gesetz": "BGB"}),
        VectorRecord(key="bgb-536", vector=[0.2] * 1024, metadata={"gesetz": "BGB"}),
    ]
    idx.upsert(records)
    client.put_vectors.assert_called_once()
    kwargs = client.put_vectors.call_args.kwargs
    assert kwargs["indexName"] == "kira-legal-norms"
    sent_vectors = kwargs["vectors"]
    assert {v["key"] for v in sent_vectors} == {"bgb-535", "bgb-536"}


def test_upsert_chunks_above_batch_limit():
    client = MagicMock()
    idx = VectorIndex(
        s3vectors_client=client, index_name="kira-legal-norms", upsert_batch_size=10
    )
    records = [
        VectorRecord(key=f"k{i}", vector=[0.0] * 1024, metadata={})
        for i in range(25)
    ]
    idx.upsert(records)
    assert client.put_vectors.call_count == 3


def test_upsert_empty_is_noop():
    client = MagicMock()
    idx = VectorIndex(s3vectors_client=client, index_name="x")
    idx.upsert([])
    assert client.put_vectors.call_count == 0


def test_query_returns_typed_hits():
    client = MagicMock()
    client.query_vectors.return_value = {
        "vectors": [
            {
                "key": "bgb-535",
                "distance": 0.06,
                "metadata": {
                    "gesetz": "BGB",
                    "paragraph": "535",
                    "titel": "Inhalt und Hauptpflichten...",
                    "wortlaut": "(1) ...",
                    "quelle_url": "https://example.test",
                    "stand": "2026-05-09",
                },
            }
        ]
    }
    idx = VectorIndex(s3vectors_client=client, index_name="kira-legal-norms")
    hits = idx.query(vector=[0.1] * 1024, k=5)
    assert len(hits) == 1
    h = hits[0]
    assert isinstance(h, VectorSearchHit)
    assert h.key == "bgb-535"
    assert h.score == pytest.approx(1.0 - 0.06)
    assert h.metadata["gesetz"] == "BGB"


def test_query_passes_metadata_filter_when_provided():
    client = MagicMock()
    client.query_vectors.return_value = {"vectors": []}
    idx = VectorIndex(s3vectors_client=client, index_name="kira-legal-norms")
    idx.query(
        vector=[0.0] * 1024,
        k=10,
        metadata_filter={"abkuerzung": {"$in": ["BGB", "WEG"]}},
    )
    kwargs = client.query_vectors.call_args.kwargs
    assert kwargs["filter"] == {"abkuerzung": {"$in": ["BGB", "WEG"]}}


def test_query_omits_filter_when_none():
    client = MagicMock()
    client.query_vectors.return_value = {"vectors": []}
    idx = VectorIndex(s3vectors_client=client, index_name="kira-legal-norms")
    idx.query(vector=[0.0] * 1024, k=10)
    kwargs = client.query_vectors.call_args.kwargs
    assert "filter" not in kwargs


def test_delete_keys_chunks_above_batch_limit():
    client = MagicMock()
    idx = VectorIndex(
        s3vectors_client=client, index_name="x", delete_batch_size=10
    )
    idx.delete([f"k{i}" for i in range(25)])
    assert client.delete_vectors.call_count == 3


def test_query_client_error_raises_corpus_unavailable():
    client = MagicMock()
    client.query_vectors.side_effect = ClientError(
        error_response={"Error": {"Code": "ServiceUnavailable", "Message": "down"}},
        operation_name="QueryVectors",
    )
    idx = VectorIndex(s3vectors_client=client, index_name="x")
    with pytest.raises(CorpusUnavailableError):
        idx.query(vector=[0.0] * 1024, k=1)

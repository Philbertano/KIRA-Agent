"""Opt-in V2 live smoke. Run with: RUN_LIVE_TESTS=1 pytest -m live."""

import os
import zipfile
from io import BytesIO

import boto3
import httpx
import pytest

pytestmark = [pytest.mark.live]

if not os.environ.get("RUN_LIVE_TESTS"):
    pytest.skip("RUN_LIVE_TESTS not set", allow_module_level=True)


def test_live_toc_in_expected_size_band():
    from kira.legal_sources._common.toc import fetch_toc, is_citable

    with httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        headers={"User-Agent": "KIRA-Agent/0.1 (live smoke v2)"},
        follow_redirects=True,
    ) as client:
        entries = fetch_toc(client)
    citable = [e for e in entries if is_citable(e)]
    assert 2000 <= len(citable) <= 3500, f"unexpected citable count: {len(citable)}"


def test_live_cohere_embedding_dimension():
    bedrock = boto3.client("bedrock-runtime", region_name="eu-central-1")
    from kira.legal_sources._common.embedder import (
        EMBEDDING_DIMENSION,
        CohereMultilingualEmbedder,
    )
    embedder = CohereMultilingualEmbedder(bedrock_client=bedrock)
    vectors = embedder.embed_documents(["Mietminderung wegen Schimmel"])
    assert len(vectors) == 1
    assert len(vectors[0]) == EMBEDDING_DIMENSION


def test_live_s3_vectors_roundtrip():
    """Requires `kira-legal-norms` index to exist (deployed by CDK)."""
    from kira.legal_sources._common.vector_index import VectorIndex, VectorRecord

    s3v = boto3.client("s3vectors", region_name="eu-central-1")
    idx = VectorIndex(s3vectors_client=s3v, index_name="kira-legal-norms")
    # Insert one test vector, query, delete.
    test_key = "__live_smoke_v2__"
    vec = [0.1] * 1024
    idx.upsert([
        VectorRecord(
            key=test_key,
            vector=vec,
            metadata={
                "gesetz": "TEST",
                "paragraph": "0",
                "abkuerzung": "TEST",
                "type": "Gesetz",
                "titel": "smoke",
                "wortlaut": "x",
                "quelle_url": "https://example.test",
                "stand": "2026-05-10",
                "content_sha256": "smoke",
            },
        )
    ])
    try:
        hits = idx.query(vector=vec, k=5, metadata_filter={"abkuerzung": {"$in": ["TEST"]}})
        assert any(h.key == test_key for h in hits)
    finally:
        idx.delete([test_key])

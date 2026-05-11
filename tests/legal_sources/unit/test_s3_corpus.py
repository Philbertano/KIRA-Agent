import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.s3_corpus import LazyCorpusLoader

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def aws_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.delenv("LEGAL_CORPUS_LOCAL_DIR", raising=False)


def _meta_payload() -> dict:
    return {
        "abkuerzung": "BGB",
        "titel": "Bürgerliches Gesetzbuch",
        "type": "Gesetz",
        "stand": "2026-05-09",
        "quelle": "gesetze-im-internet.de",
        "quelle_url": "https://www.gesetze-im-internet.de/bgb",
        "upstream_xml_zip_url": "https://www.gesetze-im-internet.de/bgb/xml.zip",
        "paragraphen": {
            "535": {
                "titel": "Inhalt und Hauptpflichten des Mietvertrags",
                "key": "gesetze/bgb/535.json",
                "content_sha256": "abc",
            }
        },
    }


def _norm_payload() -> dict:
    return {
        "gesetz": "BGB",
        "paragraph": "535",
        "titel": "Inhalt und Hauptpflichten des Mietvertrags",
        "absaetze": [{"nummer": "1", "text": "Durch den Mietvertrag ..."}],
        "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
    }


def _manifest_payload() -> dict:
    return {
        "version": 2,
        "stand": "2026-05-09",
        "gesetze": {
            "bgb": {
                "abkuerzung": "BGB",
                "titel": "Bürgerliches Gesetzbuch",
                "type": "Gesetz",
                "meta_key": "gesetze/bgb/_meta.json",
                "upstream_etag": "\"abc\"",
                "upstream_last_modified": "Wed, 06 May 2026 15:45:05 GMT",
            }
        },
    }


@pytest.fixture
def s3_corpus_bucket():
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket="test-corpus",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        s3.put_object(
            Bucket="test-corpus",
            Key="gesetze/_manifest.json",
            Body=json.dumps(_manifest_payload()).encode("utf-8"),
        )
        s3.put_object(
            Bucket="test-corpus",
            Key="gesetze/bgb/_meta.json",
            Body=json.dumps(_meta_payload()).encode("utf-8"),
        )
        s3.put_object(
            Bucket="test-corpus",
            Key="gesetze/bgb/535.json",
            Body=json.dumps(_norm_payload()).encode("utf-8"),
        )
        yield "test-corpus"


def test_load_manifest_returns_v2(monkeypatch, s3_corpus_bucket, tmp_path):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    loader = LazyCorpusLoader.from_env()
    m = loader.load_manifest()
    assert m.version == 2
    assert "bgb" in m.gesetze


def test_load_meta_then_norm(monkeypatch, s3_corpus_bucket, tmp_path):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    loader = LazyCorpusLoader.from_env()
    meta = loader.load_meta("bgb")
    assert meta is not None
    assert meta.abkuerzung == "BGB"
    norm = loader.load_norm("gesetze/bgb/535.json")
    assert norm is not None
    assert "Mietvertrag" in norm.absaetze[0].text


def test_load_meta_returns_none_for_unknown(
    monkeypatch, s3_corpus_bucket, tmp_path
):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    loader = LazyCorpusLoader.from_env()
    assert loader.load_meta("doesnotexist") is None


def test_load_meta_warm_hit_skips_s3(monkeypatch, s3_corpus_bucket, tmp_path):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    loader = LazyCorpusLoader.from_env()
    loader.load_meta("bgb")  # cold
    # Mutate S3 to a malformed payload; warm load must NOT fetch.
    s3 = boto3.client("s3", region_name="eu-central-1")
    s3.put_object(
        Bucket=s3_corpus_bucket,
        Key="gesetze/bgb/_meta.json",
        Body=b"not-json",
    )
    again = loader.load_meta("bgb")  # warm — served from memory
    assert again is not None and again.abkuerzung == "BGB"


def test_load_norm_falls_back_to_tmp_after_memory_eviction(
    monkeypatch, s3_corpus_bucket, tmp_path
):
    """After memory eviction, /tmp serves without re-hitting S3."""
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.NORM_MEMORY_MAX_ITEMS", 1
    )
    loader = LazyCorpusLoader.from_env()
    loader.load_norm("gesetze/bgb/535.json")  # cold path: S3 → /tmp → memory
    # Force memory eviction by loading the same key under a different key
    # → easier: directly reach in and clear the in-memory LRU.
    loader._norm_memory._data.clear()
    # Mutate S3 to garbage; if /tmp tier works, we still get the right norm.
    s3 = boto3.client("s3", region_name="eu-central-1")
    s3.put_object(
        Bucket=s3_corpus_bucket,
        Key="gesetze/bgb/535.json",
        Body=b"corrupt",
    )
    n = loader.load_norm("gesetze/bgb/535.json")
    assert n is not None
    assert "Mietvertrag" in n.absaetze[0].text


def test_no_env_set_raises_corpus_unavailable():
    with pytest.raises(CorpusUnavailableError):
        LazyCorpusLoader.from_env().load_manifest()


def test_load_manifest_warm_hit_skips_s3(monkeypatch, s3_corpus_bucket, tmp_path):
    """Second call to load_manifest within recheck window returns cached."""
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    loader = LazyCorpusLoader.from_env()
    m1 = loader.load_manifest()
    # Mutate S3 to malformed; warm load must NOT fetch.
    s3 = boto3.client("s3", region_name="eu-central-1")
    s3.put_object(
        Bucket=s3_corpus_bucket,
        Key="gesetze/_manifest.json",
        Body=b"not-json",
    )
    m2 = loader.load_manifest()  # warm hit, served from memory
    assert m2.version == 2
    assert m1.version == m2.version


def test_read_local_flat_fallback(monkeypatch, tmp_path):
    """_read_local falls back to flat filename when key has 'gesetze/' prefix."""
    monkeypatch.delenv("LEGAL_CORPUS_BUCKET", raising=False)
    local_dir = tmp_path / "corpus"
    local_dir.mkdir()
    # Write a flat file without gesetze/ subdir (V1-style)
    (local_dir / "535.json").write_bytes(b'{"test": "data"}')
    loader = LazyCorpusLoader(s3_bucket=None, local_dir=local_dir)
    # Request with gesetze/ prefix, should find flat 535.json
    result = loader._read_local("gesetze/bgb/535.json")
    assert result == b'{"test": "data"}'


def test_read_s3_client_error_non_suppressed(monkeypatch, tmp_path):
    """S3 ClientError with non-suppressed code raises CorpusUnavailableError."""
    from unittest.mock import MagicMock, patch

    from botocore.exceptions import ClientError

    monkeypatch.delenv("LEGAL_CORPUS_LOCAL_DIR", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", "test-corpus")
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )

    loader = LazyCorpusLoader.from_env()
    error_response = {
        "Error": {"Code": "InternalError", "Message": "Server error"}
    }
    # Mock boto3.client to return a mock client that raises ClientError
    mock_s3_client = MagicMock()
    mock_s3_client.get_object.side_effect = ClientError(error_response, "GetObject")

    with patch("boto3.client", return_value=mock_s3_client):
        with pytest.raises(CorpusUnavailableError) as exc_info:
            loader._read_s3("gesetze/bgb/535.json")
        assert "S3 GET" in str(exc_info.value)

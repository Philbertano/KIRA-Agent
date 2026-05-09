import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.s3_corpus import CorpusLoader

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("LEGAL_CORPUS_LOCAL_DIR", raising=False)
    monkeypatch.delenv("LEGAL_CORPUS_BUCKET", raising=False)


def test_loads_from_local_dir(tmp_path: Path, monkeypatch):
    src = json.loads((FIXTURES / "bgb_subset.json").read_text(encoding="utf-8"))
    target = tmp_path / "gesetze"
    target.mkdir()
    (target / "bgb.json").write_text(json.dumps(src), encoding="utf-8")
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    loader = CorpusLoader.from_env()
    corpus = loader.load_all()

    assert "bgb" in corpus
    assert corpus["bgb"].meta.abkuerzung == "BGB"


def test_local_dir_missing_raises_corpus_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path / "does-not-exist"))
    with pytest.raises(CorpusUnavailableError):
        CorpusLoader.from_env().load_all()


def test_no_env_set_raises_corpus_unavailable():
    with pytest.raises(CorpusUnavailableError):
        CorpusLoader.from_env().load_all()


@pytest.fixture
def aws_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")


@pytest.fixture
def s3_corpus_bucket(aws_creds):
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket="test-corpus",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        bgb = (FIXTURES / "bgb_subset.json").read_text(encoding="utf-8")
        s3.put_object(
            Bucket="test-corpus", Key="gesetze/bgb.json", Body=bgb.encode("utf-8")
        )
        manifest = json.dumps({"version": 1, "files": ["gesetze/bgb.json"]})
        s3.put_object(
            Bucket="test-corpus",
            Key="gesetze/_manifest.json",
            Body=manifest.encode("utf-8"),
        )
        yield "test-corpus"


def test_loads_from_s3(monkeypatch, s3_corpus_bucket, tmp_path):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    loader = CorpusLoader.from_env()
    corpus = loader.load_all()
    assert "bgb" in corpus
    assert corpus["bgb"].meta.abkuerzung == "BGB"


def test_warm_cache_skips_s3_within_recheck_window(monkeypatch, s3_corpus_bucket, tmp_path):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    loader = CorpusLoader.from_env()
    loader.load_all()  # populate the warm cache
    # Mutate S3 to add a new gesetz; warm load must NOT see it.
    s3 = boto3.client("s3", region_name="eu-central-1")
    second_payload = json.dumps({
        "_meta": {
            "abkuerzung": "BetrKV", "titel": "x", "stand": "2026-05-09",
            "quelle": "x", "quelle_url": "https://example.test",
            "gefiltert_auf": [], "anzahl_normen": 0,
        },
        "paragraphen": {},
    })
    s3.put_object(
        Bucket=s3_corpus_bucket,
        Key="gesetze/betrkv.json",
        Body=second_payload.encode("utf-8"),
    )
    second = loader.load_all()  # within recheck window
    assert "betrkv" not in second
    # Force recheck: backdate the manifest-checked-at timestamp
    loader._manifest_checked_at = 0.0
    new_manifest = json.dumps(
        {"version": 2, "files": ["gesetze/bgb.json", "gesetze/betrkv.json"]}
    )
    s3.put_object(
        Bucket=s3_corpus_bucket,
        Key="gesetze/_manifest.json",
        Body=new_manifest.encode("utf-8"),
    )
    third = loader.load_all()
    assert "betrkv" in third


def test_local_dir_with_malformed_json_skips_file(tmp_path, monkeypatch):
    """Test that malformed JSON files are skipped with a warning."""
    src = json.loads((FIXTURES / "bgb_subset.json").read_text(encoding="utf-8"))
    target = tmp_path / "gesetze"
    target.mkdir()
    # Add a good file
    (target / "bgb.json").write_text(json.dumps(src), encoding="utf-8")
    # Add a malformed file
    (target / "broken.json").write_text("{ invalid json }", encoding="utf-8")
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    loader = CorpusLoader.from_env()
    corpus = loader.load_all()

    # Should still load the good file and skip the broken one
    assert "bgb" in corpus
    assert "broken" not in corpus


def test_s3_manifest_read_error_raises_corpus_unavailable(monkeypatch, aws_creds, tmp_path):
    """Test that S3 manifest read error raises CorpusUnavailableError."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket="test-corpus",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        # Bucket exists but manifest doesn't
        monkeypatch.setenv("LEGAL_CORPUS_BUCKET", "test-corpus")
        monkeypatch.setattr(
            "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
            tmp_path / "cache",
        )
        with pytest.raises(CorpusUnavailableError):
            CorpusLoader.from_env().load_all()


def test_s3_with_malformed_corpus_file_skips_it(monkeypatch, aws_creds, tmp_path):
    """Test that malformed corpus files in S3 are skipped."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket="test-corpus",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        # Add a broken file and a good file
        bgb = (FIXTURES / "bgb_subset.json").read_text(encoding="utf-8")
        s3.put_object(
            Bucket="test-corpus", Key="gesetze/bgb.json", Body=bgb.encode("utf-8")
        )
        s3.put_object(
            Bucket="test-corpus", Key="gesetze/broken.json", Body=b"{ invalid }"
        )
        manifest = json.dumps(
            {"version": 1, "files": ["gesetze/bgb.json", "gesetze/broken.json"]}
        )
        s3.put_object(
            Bucket="test-corpus",
            Key="gesetze/_manifest.json",
            Body=manifest.encode("utf-8"),
        )

        monkeypatch.setenv("LEGAL_CORPUS_BUCKET", "test-corpus")
        monkeypatch.setattr(
            "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
            tmp_path / "cache",
        )
        loader = CorpusLoader.from_env()
        corpus = loader.load_all()

        # Should load bgb and skip the broken file
        assert "bgb" in corpus
        assert "broken" not in corpus


def test_s3_empty_manifest_raises_corpus_unavailable(monkeypatch, aws_creds, tmp_path):
    """Test that an S3 manifest with no usable files raises CorpusUnavailableError."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket="test-corpus",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        # Empty manifest
        manifest = json.dumps({"version": 1, "files": []})
        s3.put_object(
            Bucket="test-corpus",
            Key="gesetze/_manifest.json",
            Body=manifest.encode("utf-8"),
        )

        monkeypatch.setenv("LEGAL_CORPUS_BUCKET", "test-corpus")
        monkeypatch.setattr(
            "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
            tmp_path / "cache",
        )
        with pytest.raises(CorpusUnavailableError):
            CorpusLoader.from_env().load_all()

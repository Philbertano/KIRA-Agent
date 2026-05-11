import importlib
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

FIXTURES = Path(__file__).parent.parent / "fixtures"


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
        "absaetze": [
            {"nummer": "1", "text": "Durch den Mietvertrag ..."},
            {"nummer": "2", "text": "Der Mieter ..."},
        ],
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
                "upstream_last_modified": "...",
            }
        },
    }


@pytest.fixture(autouse=True)
def aws_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.delenv("LEGAL_CORPUS_LOCAL_DIR", raising=False)


@pytest.fixture
def populated_bucket(monkeypatch, tmp_path):
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
        monkeypatch.setenv("LEGAL_CORPUS_BUCKET", "test-corpus")
        monkeypatch.setattr(
            "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
            tmp_path / "cache",
        )
        # Reload the module so its module-level _LOADER picks up env + tmp.
        import kira.legal_sources.adapters.lookup_handler as mod
        importlib.reload(mod)
        yield mod


def test_handler_direct_invoke(populated_bucket):
    out = populated_bucket.handler({"gesetz": "BGB", "paragraph": "535"}, None)
    assert out["isError"] is False
    body = json.loads(out["content"][0]["text"])
    assert body["paragraph"] == "535"
    assert "Mietvertrag" in body["wortlaut"]


def test_handler_agentcore_gateway_shape(populated_bucket):
    out = populated_bucket.handler(
        {"tool_name": "lookup_norm", "tool_use_id": "x",
         "input": {"gesetz": "BGB", "paragraph": "535", "absatz": "2"}},
        None,
    )
    assert out["isError"] is False
    body = json.loads(out["content"][0]["text"])
    assert body["absatz"] == "2"


def test_handler_unknown_gesetz_returns_error(populated_bucket):
    out = populated_bucket.handler({"gesetz": "ABC", "paragraph": "1"}, None)
    assert out["isError"] is True
    body = json.loads(out["content"][0]["text"])
    assert body["error"] == "unknown_gesetz"


def test_handler_validation_error(populated_bucket):
    out = populated_bucket.handler({"gesetz": "", "paragraph": ""}, None)
    assert out["isError"] is True
    assert "validation_error" in out["content"][0]["text"]


def test_handler_corpus_unavailable_when_no_env(monkeypatch):
    monkeypatch.delenv("LEGAL_CORPUS_LOCAL_DIR", raising=False)
    monkeypatch.delenv("LEGAL_CORPUS_BUCKET", raising=False)
    import kira.legal_sources.adapters.lookup_handler as mod
    importlib.reload(mod)
    out = mod.handler({"gesetz": "BGB", "paragraph": "535"}, None)
    assert out["isError"] is True
    assert "corpus_unavailable" in out["content"][0]["text"]

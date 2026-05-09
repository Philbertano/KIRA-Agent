import json
from pathlib import Path

import boto3
import httpx
import pytest
import respx
from moto import mock_aws

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def aws_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")


@pytest.fixture
def s3_target():
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket="ingest-target",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        yield "ingest-target"


def test_ingest_writes_corpus_and_manifest(monkeypatch, s3_target):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)
    zip_bytes = (FIXTURES / "captured" / "bgb.zip").read_bytes()

    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, content=zip_bytes)
        )
        from kira.legal_sources.adapters.ingest_handler import handler
        result = handler({"gesetze": ["bgb"]}, context=None)

    assert result["written"] == ["bgb"]
    s3 = boto3.client("s3", region_name="eu-central-1")
    body = s3.get_object(Bucket=s3_target, Key="gesetze/bgb.json")["Body"].read()
    payload = json.loads(body)
    assert payload["_meta"]["abkuerzung"] == "BGB"
    assert "535" in payload["paragraphen"]
    manifest = json.loads(
        s3.get_object(Bucket=s3_target, Key="gesetze/_manifest.json")["Body"].read()
    )
    assert "gesetze/bgb.json" in manifest["files"]


def test_ingest_skips_put_when_hash_unchanged(monkeypatch, s3_target):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)
    zip_bytes = (FIXTURES / "captured" / "bgb.zip").read_bytes()

    from kira.legal_sources.adapters.ingest_handler import handler

    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, content=zip_bytes)
        )
        first = handler({"gesetze": ["bgb"]}, context=None)
        second = handler({"gesetze": ["bgb"]}, context=None)

    assert first["written"] == ["bgb"]
    assert second["written"] == []  # idempotent skip
    assert second["skipped"] == ["bgb"]


def test_ingest_output_round_trips_through_corpus_format(monkeypatch, s3_target):
    """End-to-end: ingest writes JSON that the lookup_norm path can actually read.

    Without this test the adapter could write data the lookup Lambda silently
    rejects with a ValidationError (which the loader skips via except), leaving
    a deployed system that ingests but serves nothing.
    """
    from kira.legal_sources.gesetze.corpus_format import GesetzKorpus
    from kira.legal_sources.gesetze.lookup_norm import lookup_norm
    from kira.legal_sources.gesetze.schema import (
        LookupNormInput,
        LookupNormSuccess,
    )

    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)
    zip_bytes = (FIXTURES / "captured" / "bgb.zip").read_bytes()

    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, content=zip_bytes)
        )
        from kira.legal_sources.adapters.ingest_handler import handler
        handler({"gesetze": ["bgb"]}, context=None)

    s3 = boto3.client("s3", region_name="eu-central-1")
    written = json.loads(
        s3.get_object(Bucket=s3_target, Key="gesetze/bgb.json")["Body"].read()
    )
    # Strict parse: this must validate cleanly against the corpus_format
    # contract that lookup_norm reads from.
    korpus = GesetzKorpus.model_validate(written)
    assert "535" in korpus.paragraphen
    norm = korpus.paragraphen["535"]
    assert norm.absaetze, "Norm should have at least one Absatz parsed"
    assert norm.absaetze[0].nummer == "1"
    assert "Mietvertrag" in norm.absaetze[0].text

    # And the actual lookup_norm function returns a success.
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="535"),
        corpus={"bgb": korpus},
    )
    assert isinstance(result, LookupNormSuccess)
    assert "Mietvertrag" in result.wortlaut

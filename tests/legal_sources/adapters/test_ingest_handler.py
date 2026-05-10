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


def test_ingest_routes_through_cloudflare_proxy_when_env_set(monkeypatch, s3_target):
    """When LEGAL_INGEST_PROXY_URL is set, fetches go through the worker.

    Verifies (a) the proxy URL is hit with the upstream URL as the `url=`
    query param and (b) the configured auth header is attached.
    """
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)
    monkeypatch.setenv(
        "LEGAL_INGEST_PROXY_URL",
        "https://kira-legaltext-gii-proxy.example.workers.dev",
    )
    monkeypatch.setenv("LEGAL_INGEST_PROXY_AUTH_VALUE", "test-secret")
    zip_bytes = (FIXTURES / "captured" / "bgb.zip").read_bytes()

    # respx without `pass_through` errors on any unmatched URL, so if the
    # Lambda fetched gesetze-im-internet.de directly the test would fail
    # with `Mock not matched`.
    with respx.mock(assert_all_called=True) as mock:
        proxy_route = mock.get(
            "https://kira-legaltext-gii-proxy.example.workers.dev/",
            params={"url": "https://www.gesetze-im-internet.de/bgb/xml.zip"},
        ).mock(return_value=httpx.Response(200, content=zip_bytes))

        from kira.legal_sources.adapters.ingest_handler import handler

        result = handler({"gesetze": ["bgb"]}, context=None)

    assert result["written"] == ["bgb"]
    assert proxy_route.called
    sent = proxy_route.calls.last.request
    assert sent.headers.get("X-Proxy-Auth") == "test-secret"
    # Confirm the upstream URL is intact in the proxy query string.
    assert "https%3A%2F%2Fwww.gesetze-im-internet.de%2Fbgb%2Fxml.zip" in str(
        sent.url
    )


def test_ingest_proxy_uses_custom_header_name(monkeypatch, s3_target):
    """LEGAL_INGEST_PROXY_AUTH_HEADER overrides the default X-Proxy-Auth name."""
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)
    monkeypatch.setenv(
        "LEGAL_INGEST_PROXY_URL",
        "https://proxy.example.test",
    )
    monkeypatch.setenv("LEGAL_INGEST_PROXY_AUTH_HEADER", "Authorization")
    monkeypatch.setenv("LEGAL_INGEST_PROXY_AUTH_VALUE", "Bearer xyz")
    zip_bytes = (FIXTURES / "captured" / "bgb.zip").read_bytes()

    with respx.mock(assert_all_called=True) as mock:
        proxy_route = mock.get(
            "https://proxy.example.test/",
            params={"url": "https://www.gesetze-im-internet.de/bgb/xml.zip"},
        ).mock(return_value=httpx.Response(200, content=zip_bytes))

        from kira.legal_sources.adapters.ingest_handler import handler

        handler({"gesetze": ["bgb"]}, context=None)

    sent = proxy_route.calls.last.request
    assert sent.headers.get("Authorization") == "Bearer xyz"
    assert sent.headers.get("X-Proxy-Auth") is None


def test_ingest_no_proxy_when_env_unset(monkeypatch, s3_target):
    """Without LEGAL_INGEST_PROXY_URL, fetches still go direct to upstream."""
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)
    monkeypatch.delenv("LEGAL_INGEST_PROXY_URL", raising=False)
    monkeypatch.delenv("LEGAL_INGEST_PROXY_AUTH_VALUE", raising=False)
    zip_bytes = (FIXTURES / "captured" / "bgb.zip").read_bytes()

    with respx.mock(assert_all_called=True) as mock:
        direct = mock.get(
            "https://www.gesetze-im-internet.de/bgb/xml.zip"
        ).mock(return_value=httpx.Response(200, content=zip_bytes))

        from kira.legal_sources.adapters.ingest_handler import handler

        result = handler({"gesetze": ["bgb"]}, context=None)

    assert result["written"] == ["bgb"]
    assert direct.called
    # No auth header on direct fetches.
    assert direct.calls.last.request.headers.get("X-Proxy-Auth") is None

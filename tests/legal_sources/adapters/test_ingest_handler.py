import json
from pathlib import Path
from unittest.mock import patch

import boto3
import httpx
import pytest
import respx
from moto import mock_aws

FIXTURES = Path(__file__).parent.parent / "fixtures"
TOC_FIXTURE = FIXTURES / "captured" / "gii_toc_subset.xml"


@pytest.fixture(autouse=True)
def aws_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.delenv("LEGAL_INGEST_PROXY_URL", raising=False)


@pytest.fixture
def s3_target():
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket="ingest-target",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        yield "ingest-target"


def _bgb_zip() -> bytes:
    return (FIXTURES / "captured" / "bgb.zip").read_bytes()


@pytest.fixture
def mock_aws_clients():
    """Mock only the Bedrock + S3 Vectors factories; leave moto-mocked S3 untouched."""
    with patch(
        "kira.legal_sources.adapters.ingest_handler._make_embedder"
    ) as make_embedder, patch(
        "kira.legal_sources.adapters.ingest_handler._make_vector_index"
    ) as make_vidx:
        embedder = make_embedder.return_value
        embedder.embed_documents.return_value = [[0.1] * 1024]
        vidx = make_vidx.return_value
        yield {"embedder": embedder, "vector_index": vidx}


def test_ingest_writes_per_paragraph_files_and_meta(
    monkeypatch, s3_target, mock_aws_clients
):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)
    mock_aws_clients["embedder"].embed_documents.return_value = (
        [[0.1] * 1024]  # one paragraph in fixture
    )

    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/gii-toc.xml").mock(
            return_value=httpx.Response(200, content=TOC_FIXTURE.read_bytes())
        )
        # 304 for BetrKV-like entries we don't have fixtures for: don't matter
        # because BetrKV/WEG xml endpoints aren't stubbed and the implementation
        # iterates only over what the fixture says. To keep this test focused,
        # stub all three xml.zip URLs.
        respx.head("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, headers={"ETag": "\"abc\""})
        )
        respx.get("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, content=_bgb_zip())
        )
        respx.head("https://www.gesetze-im-internet.de/woeigg/xml.zip").mock(
            return_value=httpx.Response(404)
        )
        respx.head("https://www.gesetze-im-internet.de/betrkv/xml.zip").mock(
            return_value=httpx.Response(404)
        )

        from kira.legal_sources.adapters.ingest_handler import handler
        handler({}, None)

    s3 = boto3.client("s3", region_name="eu-central-1")
    # _meta.json was written
    meta_body = s3.get_object(
        Bucket=s3_target, Key="gesetze/bgb/_meta.json"
    )["Body"].read()
    meta = json.loads(meta_body)
    assert meta["abkuerzung"] == "BGB"
    assert "535" in meta["paragraphen"]
    # per-§ JSON was written
    p535_body = s3.get_object(
        Bucket=s3_target, Key="gesetze/bgb/535.json"
    )["Body"].read()
    p535 = json.loads(p535_body)
    assert p535["paragraph"] == "535"
    # manifest written
    manifest = json.loads(
        s3.get_object(Bucket=s3_target, Key="gesetze/_manifest.json")["Body"].read()
    )
    assert manifest["version"] == 2
    assert "bgb" in manifest["gesetze"]
    # embed_documents was called once for §535
    assert mock_aws_clients["embedder"].embed_documents.call_count == 1
    assert mock_aws_clients["vector_index"].upsert.call_count == 1


def test_ingest_skips_unchanged_paragraph(monkeypatch, s3_target, mock_aws_clients):
    """Second invocation with no upstream change → no PUT for unchanged §s, no re-embed."""
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)
    mock_aws_clients["embedder"].embed_documents.return_value = [[0.1] * 1024]

    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/gii-toc.xml").mock(
            return_value=httpx.Response(200, content=TOC_FIXTURE.read_bytes())
        )
        respx.head("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, headers={"ETag": "\"abc\""})
        )
        respx.get("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, content=_bgb_zip())
        )
        respx.head("https://www.gesetze-im-internet.de/woeigg/xml.zip").mock(
            return_value=httpx.Response(404)
        )
        respx.head("https://www.gesetze-im-internet.de/betrkv/xml.zip").mock(
            return_value=httpx.Response(404)
        )

        from kira.legal_sources.adapters.ingest_handler import handler
        first = handler({}, None)

    # Second run: conditional HEAD returns 304 → skip whole Gesetz.
    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/gii-toc.xml").mock(
            return_value=httpx.Response(200, content=TOC_FIXTURE.read_bytes())
        )
        respx.head("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(304)
        )
        respx.head("https://www.gesetze-im-internet.de/woeigg/xml.zip").mock(
            return_value=httpx.Response(404)
        )
        respx.head("https://www.gesetze-im-internet.de/betrkv/xml.zip").mock(
            return_value=httpx.Response(404)
        )

        from kira.legal_sources.adapters.ingest_handler import handler
        # reset embedder mock so we can assert it isn't called again
        mock_aws_clients["embedder"].embed_documents.reset_mock()
        second = handler({}, None)

    assert "bgb" in first["written"]
    assert second["written"] == []
    assert "bgb" in second["skipped"]
    # No re-embedding on the skip path
    assert mock_aws_clients["embedder"].embed_documents.call_count == 0


def test_ingest_diff_only_re_embeds_changed_paragraphs(
    monkeypatch, s3_target, mock_aws_clients
):
    """If meta has a paragraph with content_sha256 = X and the new content's
    sha256 also = X, the paragraph is NOT re-PUT or re-embedded."""
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)

    # Pre-populate meta with an entry matching what the BGB fixture produces.
    # The simplest is: run ingest once, snapshot, then run again with
    # different ETag forcing a new download but unchanged paragraph content.
    mock_aws_clients["embedder"].embed_documents.return_value = [[0.1] * 1024]

    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/gii-toc.xml").mock(
            return_value=httpx.Response(200, content=TOC_FIXTURE.read_bytes())
        )
        respx.head("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, headers={"ETag": "\"v1\""})
        )
        respx.get("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, content=_bgb_zip())
        )
        respx.head("https://www.gesetze-im-internet.de/woeigg/xml.zip").mock(
            return_value=httpx.Response(404)
        )
        respx.head("https://www.gesetze-im-internet.de/betrkv/xml.zip").mock(
            return_value=httpx.Response(404)
        )

        from kira.legal_sources.adapters.ingest_handler import handler
        handler({}, None)

    mock_aws_clients["embedder"].embed_documents.reset_mock()
    mock_aws_clients["vector_index"].upsert.reset_mock()

    # Second run: ETag changes (forces full GET) but content is identical.
    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/gii-toc.xml").mock(
            return_value=httpx.Response(200, content=TOC_FIXTURE.read_bytes())
        )
        respx.head("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, headers={"ETag": "\"v2\""})
        )
        respx.get("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, content=_bgb_zip())
        )
        respx.head("https://www.gesetze-im-internet.de/woeigg/xml.zip").mock(
            return_value=httpx.Response(404)
        )
        respx.head("https://www.gesetze-im-internet.de/betrkv/xml.zip").mock(
            return_value=httpx.Response(404)
        )

        from kira.legal_sources.adapters.ingest_handler import handler
        handler({}, None)

    # Same content sha → no embedding, no upsert
    assert mock_aws_clients["embedder"].embed_documents.call_count == 0
    assert mock_aws_clients["vector_index"].upsert.call_count == 0


def test_ingest_uses_xml_jurabk_not_slug_upper(
    monkeypatch, s3_target, mock_aws_clients
):
    """The stored abkuerzung must come from the XML's <jurabk> element, not
    from `slug.upper()`. Regression: V2 deploy stored `WOEIGG` for the WEG
    because the slug-derived form differed from the canonical jurabk."""
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)
    mock_aws_clients["embedder"].embed_documents.return_value = [[0.1] * 1024]

    from kira.legal_sources._common.xml_parser import Norm, ParseResult

    fake_parse_result = ParseResult(
        abkuerzung="CANONICAL-ABK",
        titel="Test Gesetz",
        normen={
            "1": Norm(
                gesetz="CANONICAL-ABK",
                paragraph="1",
                titel="Test",
                absaetze=["(1) Test."],
            )
        },
    )

    with respx.mock, patch(
        "kira.legal_sources.adapters.ingest_handler.parse_gii_xml",
        return_value=fake_parse_result,
    ):
        respx.get("https://www.gesetze-im-internet.de/gii-toc.xml").mock(
            return_value=httpx.Response(200, content=TOC_FIXTURE.read_bytes())
        )
        respx.head("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, headers={"ETag": "\"abc\""})
        )
        respx.get("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, content=_bgb_zip())
        )
        respx.head("https://www.gesetze-im-internet.de/woeigg/xml.zip").mock(
            return_value=httpx.Response(404)
        )
        respx.head("https://www.gesetze-im-internet.de/betrkv/xml.zip").mock(
            return_value=httpx.Response(404)
        )

        from kira.legal_sources.adapters.ingest_handler import handler
        handler({}, None)

    s3 = boto3.client("s3", region_name="eu-central-1")
    meta = json.loads(
        s3.get_object(Bucket=s3_target, Key="gesetze/bgb/_meta.json")["Body"].read()
    )
    # The fix: abkuerzung is the XML's <jurabk> value, NOT slug.upper().
    assert meta["abkuerzung"] == "CANONICAL-ABK"
    assert meta["abkuerzung"] != "BGB"

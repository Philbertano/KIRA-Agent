"""Opt-in smoke tests against the real gesetze-im-internet.de.

Skipped by default; run with: RUN_LIVE_TESTS=1 pytest -m live tests/legal_sources/live/
If this fails, upstream HTML/XML changed and parser fixtures need refreshing.
"""

import os
import zipfile
from io import BytesIO

import httpx
import pytest

pytestmark = [pytest.mark.live]

if not os.environ.get("RUN_LIVE_TESTS"):
    pytest.skip("RUN_LIVE_TESTS not set", allow_module_level=True)


def test_live_bgb_xml_zip_parseable():
    url = "https://www.gesetze-im-internet.de/bgb/xml.zip"
    with httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        headers={"User-Agent": "KIRA-Agent/0.1 (live smoke)"},
        follow_redirects=True,
    ) as client:
        resp = client.get(url)
    assert resp.status_code == 200
    with zipfile.ZipFile(BytesIO(resp.content)) as zf:
        assert any(n.endswith(".xml") for n in zf.namelist())


@pytest.mark.skip(
    reason=(
        "needs rewrite after kira.knowledge removal;"
        " _build_payload no longer exists in ingest_handler"
    )
)
def test_live_lookup_norm_against_real_corpus(tmp_path, monkeypatch):
    """Run the actual ingest pipeline once, then lookup_norm against it.

    TODO: rewrite using the AWS ingest Lambda + S3 corpus after kira.knowledge deletion.
    """
    pass

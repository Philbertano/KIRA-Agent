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


def test_live_lookup_norm_against_real_corpus(tmp_path, monkeypatch):
    """Run the actual ingest pipeline once, then lookup_norm against it."""
    import json

    from kira.knowledge.ingest import GESETZE
    from kira.legal_sources._common.s3_corpus import CorpusLoader
    from kira.legal_sources.adapters.ingest_handler import _build_payload
    from kira.legal_sources.gesetze.lookup_norm import lookup_norm
    from kira.legal_sources.gesetze.schema import LookupNormInput

    target = tmp_path / "gesetze"
    target.mkdir()
    cfg = GESETZE["bgb"]
    with httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        headers={"User-Agent": "KIRA-Agent/0.1 (live smoke)"},
        follow_redirects=True,
    ) as client:
        payload = _build_payload(client, cfg)
    (target / "bgb.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    loader = CorpusLoader.from_env()
    result = lookup_norm(LookupNormInput(gesetz="BGB", paragraph="535"), corpus=loader.load_all())
    from kira.legal_sources.gesetze.schema import LookupNormSuccess
    assert isinstance(result, LookupNormSuccess)
    assert "Mietvertrag" in result.wortlaut

"""The agent_sdk adapter is structurally tested; we don't import claude_agent_sdk
in CI because it pulls a network-bound dependency. The adapter is thin enough
that we test its core function (`make_lookup_norm_tool_function`) directly."""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _stage_v2_corpus(tmp_path):
    """Create v2-layout corpus files at tmp_path that LazyCorpusLoader can serve."""
    (tmp_path / "gesetze").mkdir()
    (tmp_path / "gesetze" / "bgb").mkdir()
    (tmp_path / "gesetze" / "_manifest.json").write_text(
        json.dumps(
            {
                "version": 2,
                "stand": "2026-05-08",
                "gesetze": {
                    "bgb": {
                        "abkuerzung": "BGB",
                        "titel": "Bürgerliches Gesetzbuch",
                        "type": "Gesetz",
                        "meta_key": "gesetze/bgb/_meta.json",
                        "upstream_etag": '"abc"',
                        "upstream_last_modified": "2026-05-08T00:00:00Z",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "gesetze" / "bgb" / "_meta.json").write_text(
        json.dumps(
            {
                "abkuerzung": "BGB",
                "titel": "Bürgerliches Gesetzbuch",
                "type": "Gesetz",
                "stand": "2026-05-08",
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
        ),
        encoding="utf-8",
    )
    (tmp_path / "gesetze" / "bgb" / "535.json").write_text(
        json.dumps(
            {
                "gesetz": "BGB",
                "paragraph": "535",
                "titel": "Inhalt und Hauptpflichten des Mietvertrags",
                "absaetze": [
                    {
                        "nummer": "1",
                        "text": "Durch den Mietvertrag wird der Vermieter verpflichtet, "
                        "dem Mieter den Gebrauch der Mietsache während der Mietzeit "
                        "zu gewähren, und der Mieter verpflichtet, dem Vermieter "
                        "die vereinbarte Miete zu zahlen.",
                    }
                ],
                "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_make_tool_function_returns_mcp_shape(tmp_path, monkeypatch):
    _stage_v2_corpus(tmp_path)
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    from kira.legal_sources.adapters.agent_sdk import (
        make_lookup_norm_tool_function,
    )

    fn = make_lookup_norm_tool_function()
    out = await fn({"gesetz": "BGB", "paragraph": "535"})
    assert "content" in out
    assert out["content"][0]["type"] == "text"
    assert "Mietvertrag" in out["content"][0]["text"]


from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_search_norm_tool_function_returns_mcp_shape():
    from kira.legal_sources.adapters.agent_sdk import (
        make_search_norm_tool_function,
    )

    embedder = MagicMock()
    embedder.embed_query.return_value = [0.0] * 1024
    index = MagicMock()
    index.query.return_value = []

    fn = make_search_norm_tool_function(embedder=embedder, index=index)
    out = await fn({"query": "Mietminderung"})
    assert "content" in out
    assert out["content"][0]["type"] == "text"

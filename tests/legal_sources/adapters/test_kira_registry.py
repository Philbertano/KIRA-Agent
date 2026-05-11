import json
from pathlib import Path
from unittest.mock import MagicMock

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


def test_register_returns_tool_that_calls_lookup_norm(tmp_path, monkeypatch):
    # Stage v2-layout corpus
    _stage_v2_corpus(tmp_path)
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    from kira.legal_sources.adapters.kira_registry import build_lookup_norm_tool

    tool = build_lookup_norm_tool()
    assert tool.name == "lookup_norm"

    text = tool.run({"gesetz": "BGB", "paragraph": "535"})
    assert "Inhalt und Hauptpflichten" in text
    assert "Stand: 2026-05-08" in text


def test_validation_error_returned_as_error_text(tmp_path, monkeypatch):
    # Stage v2-layout corpus
    _stage_v2_corpus(tmp_path)
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    from kira.legal_sources.adapters.kira_registry import build_lookup_norm_tool

    tool = build_lookup_norm_tool()
    text = tool.run({"gesetz": "BGB"})  # missing paragraph
    assert "validation_error" in text or "FEHLER" in text


def test_build_search_norm_tool_calls_search():
    from kira.legal_sources.adapters.kira_registry import build_search_norm_tool

    fake_embedder = MagicMock()
    fake_embedder.embed_query.return_value = [0.1] * 1024
    fake_index = MagicMock()
    fake_index.query.return_value = []
    tool = build_search_norm_tool(embedder=fake_embedder, index=fake_index)
    assert tool.name == "search_norm"
    text = tool.run({"query": "Mietminderung"})
    assert "Keine Treffer" in text or "Suche" in text
    fake_embedder.embed_query.assert_called_once_with("Mietminderung")

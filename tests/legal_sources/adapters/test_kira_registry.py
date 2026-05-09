from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_register_returns_tool_that_calls_lookup_norm(tmp_path, monkeypatch):
    # Stage a local corpus
    target = tmp_path / "gesetze"
    target.mkdir()
    (target / "bgb.json").write_text(
        (FIXTURES / "bgb_subset.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    from kira.legal_sources.adapters.kira_registry import build_lookup_norm_tool

    tool = build_lookup_norm_tool()
    assert tool.name == "lookup_norm"

    text = tool.run({"gesetz": "BGB", "paragraph": "535"})
    assert "Inhalt und Hauptpflichten" in text
    assert "Stand: 2026-05-08" in text


def test_validation_error_returned_as_error_text(tmp_path, monkeypatch):
    target = tmp_path / "gesetze"
    target.mkdir()
    (target / "bgb.json").write_text(
        (FIXTURES / "bgb_subset.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    from kira.legal_sources.adapters.kira_registry import build_lookup_norm_tool

    tool = build_lookup_norm_tool()
    text = tool.run({"gesetz": "BGB"})  # missing paragraph
    assert "validation_error" in text or "FEHLER" in text

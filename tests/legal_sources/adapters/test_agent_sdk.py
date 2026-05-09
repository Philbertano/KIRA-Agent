"""The agent_sdk adapter is structurally tested; we don't import claude_agent_sdk
in CI because it pulls a network-bound dependency. The adapter is thin enough
that we test its core function (`make_lookup_norm_tool_function`) directly."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.mark.asyncio
async def test_make_tool_function_returns_mcp_shape(tmp_path, monkeypatch):
    target = tmp_path / "gesetze"
    target.mkdir()
    (target / "bgb.json").write_text(
        (FIXTURES / "bgb_subset.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    from kira.legal_sources.adapters.agent_sdk import (
        make_lookup_norm_tool_function,
    )

    fn = make_lookup_norm_tool_function()
    out = await fn({"gesetz": "BGB", "paragraph": "535"})
    assert "content" in out
    assert out["content"][0]["type"] == "text"
    assert "Mietvertrag" in out["content"][0]["text"]

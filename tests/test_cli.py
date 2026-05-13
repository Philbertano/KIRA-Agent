"""Tests for the kira CLI surface."""

from __future__ import annotations

from typer.testing import CliRunner

from kira.cli import app

runner = CliRunner()


def test_ingest_subcommand_is_gone() -> None:
    result = runner.invoke(app, ["ingest", "bgb"])
    # Typer prints help/usage on unknown command; exit code != 0
    assert result.exit_code != 0


def test_known_subcommands_still_present() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ask" in result.stdout
    assert "demo" in result.stdout
    assert "check-pseudonymisierung" in result.stdout
    assert "ingest" not in result.stdout

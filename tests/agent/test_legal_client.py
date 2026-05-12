"""Tests for kira.agent.legal_client.LegalSourcesClient."""

from __future__ import annotations

from kira.agent.legal_client import LegalSourceUnavailable, LegalSourcesClient


def test_legal_source_unavailable_is_exception() -> None:
    assert issubclass(LegalSourceUnavailable, Exception)


def test_client_constructs_with_defaults() -> None:
    client = LegalSourcesClient()
    assert client.lookup_fn_name == "kira-legal-lookup-norm"
    assert client.search_fn_name == "kira-legal-search"
    assert client.region == "eu-central-1"

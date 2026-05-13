"""Live tests against the deployed legal-sources Lambdas.

Skipped unless RUN_LIVE_TESTS=1 is set. Requires AWS credentials with
lambda:InvokeFunction on kira-legal-lookup-norm and kira-legal-search.
"""

from __future__ import annotations

import os

import pytest

from kira.agent.legal_client import LegalSourcesClient

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="RUN_LIVE_TESTS not set",
)


@pytest.fixture
def client() -> LegalSourcesClient:
    return LegalSourcesClient()


def test_lookup_bgb_535_returns_wortlaut(client: LegalSourcesClient) -> None:
    result = client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})
    assert result.get("gesetz") == "BGB"
    assert result.get("paragraph") == "535"
    assert "Vermieter" in (result.get("wortlaut") or "")
    assert "Mietsache" in (result.get("wortlaut") or "")


def test_lookup_unknown_gesetz_returns_functional_error(client: LegalSourcesClient) -> None:
    result = client.lookup_norm({"gesetz": "XYZ_NOT_REAL", "paragraph": "1"})
    assert result.get("error") == "unknown_gesetz"


def test_search_mietminderung_returns_bgb_536(client: LegalSourcesClient) -> None:
    result = client.search_norm({"query": "Mietminderung wegen Schimmel", "k": 5})
    hits = result.get("hits") or []
    assert any(h["gesetz"] == "BGB" and h["paragraph"] == "536" for h in hits)


def test_search_filter_canonical_case(client: LegalSourcesClient) -> None:
    """gesetz_filter is case-sensitive; canonical 'BetrKV' must match."""
    result = client.search_norm({
        "query": "Betriebskosten",
        "gesetz_filter": ["BetrKV"],
        "k": 3,
    })
    hits = result.get("hits") or []
    assert hits, "Expected at least one BetrKV hit"
    assert all(h["gesetz"] == "BetrKV" for h in hits)

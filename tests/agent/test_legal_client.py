"""Tests for kira.agent.legal_client.LegalSourcesClient."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock

from kira.agent.legal_client import LegalSourceUnavailable, LegalSourcesClient


def test_legal_source_unavailable_is_exception() -> None:
    assert issubclass(LegalSourceUnavailable, Exception)


def test_client_constructs_with_defaults() -> None:
    client = LegalSourcesClient()
    assert client.lookup_fn_name == "kira-legal-lookup-norm"
    assert client.search_fn_name == "kira-legal-search"
    assert client.region == "eu-central-1"


def _make_lambda(envelope: dict) -> MagicMock:
    mock = MagicMock()
    payload = io.BytesIO(json.dumps(envelope).encode("utf-8"))
    mock.invoke.return_value = {"Payload": payload, "StatusCode": 200}
    return mock


def test_invoke_unwraps_mcp_envelope() -> None:
    envelope = {
        "isError": False,
        "content": [{"type": "text", "text": json.dumps({"gesetz": "BGB", "paragraph": "535"})}],
    }
    client = LegalSourcesClient(lambda_client=_make_lambda(envelope))
    result = client._invoke("kira-legal-lookup-norm", {"gesetz": "BGB", "paragraph": "535"})
    assert result == {"gesetz": "BGB", "paragraph": "535"}


def test_invoke_passes_function_name_and_payload() -> None:
    envelope = {"isError": False, "content": [{"type": "text", "text": "{}"}]}
    fake = _make_lambda(envelope)
    client = LegalSourcesClient(lambda_client=fake)
    client._invoke("some-fn", {"a": 1})
    args, kwargs = fake.invoke.call_args
    assert kwargs["FunctionName"] == "some-fn"
    assert json.loads(kwargs["Payload"]) == {"a": 1}


def test_lookup_norm_invokes_lookup_function() -> None:
    envelope = {"isError": False, "content": [{"type": "text", "text": json.dumps({"gesetz": "BGB"})}]}
    fake = _make_lambda(envelope)
    client = LegalSourcesClient(lambda_client=fake, lookup_fn_name="lookup-fn")
    client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})
    assert fake.invoke.call_args.kwargs["FunctionName"] == "lookup-fn"


def test_search_norm_invokes_search_function() -> None:
    envelope = {"isError": False, "content": [{"type": "text", "text": json.dumps({"hits": []})}]}
    fake = _make_lambda(envelope)
    client = LegalSourcesClient(lambda_client=fake, search_fn_name="search-fn")
    client.search_norm({"query": "x"})
    assert fake.invoke.call_args.kwargs["FunctionName"] == "search-fn"

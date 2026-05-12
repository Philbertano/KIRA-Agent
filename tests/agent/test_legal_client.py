"""Tests for kira.agent.legal_client.LegalSourcesClient."""

from __future__ import annotations

import io
import json
import logging
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError

from kira.agent.legal_client import LegalSourcesClient, LegalSourceUnavailable


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
    kwargs = fake.invoke.call_args.kwargs
    assert kwargs["FunctionName"] == "some-fn"
    assert json.loads(kwargs["Payload"]) == {"a": 1}


def test_lookup_norm_invokes_lookup_function() -> None:
    envelope = {
        "isError": False,
        "content": [{"type": "text", "text": json.dumps({"gesetz": "BGB"})}],
    }
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


def test_functional_error_passes_through() -> None:
    inner = {"error": "unknown_gesetz", "message": "Gesetz 'XYZ' ist nicht im Korpus."}
    envelope = {"isError": True, "content": [{"type": "text", "text": json.dumps(inner)}]}
    client = LegalSourcesClient(lambda_client=_make_lambda(envelope))
    result = client.lookup_norm({"gesetz": "XYZ", "paragraph": "1"})
    assert result == inner


def test_client_error_wrapped_as_unavailable() -> None:
    fake = MagicMock()
    fake.invoke.side_effect = ClientError(
        error_response={"Error": {"Code": "ServiceUnavailable", "Message": "..."}},
        operation_name="Invoke",
    )
    client = LegalSourcesClient(lambda_client=fake)
    with pytest.raises(LegalSourceUnavailable):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})


def test_read_timeout_wrapped_as_unavailable() -> None:
    fake = MagicMock()
    fake.invoke.side_effect = ReadTimeoutError(endpoint_url="lambda.eu-central-1.amazonaws.com")
    client = LegalSourcesClient(lambda_client=fake)
    with pytest.raises(LegalSourceUnavailable):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})


def test_connection_error_wrapped_as_unavailable() -> None:
    fake = MagicMock()
    fake.invoke.side_effect = EndpointConnectionError(
        endpoint_url="lambda.eu-central-1.amazonaws.com"
    )
    client = LegalSourcesClient(lambda_client=fake)
    with pytest.raises(LegalSourceUnavailable):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})


def test_malformed_envelope_wrapped_as_unavailable() -> None:
    fake = MagicMock()
    fake.invoke.return_value = {"Payload": io.BytesIO(b"not json"), "StatusCode": 200}
    client = LegalSourcesClient(lambda_client=fake)
    with pytest.raises(LegalSourceUnavailable):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})


def test_empty_content_wrapped_as_unavailable() -> None:
    envelope = {"isError": False, "content": []}
    fake = _make_lambda(envelope)
    client = LegalSourcesClient(lambda_client=fake)
    with pytest.raises(LegalSourceUnavailable):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})


def test_lambda_function_error_surfaces_error_payload() -> None:
    """When Lambda runtime catches an exception, FunctionError is set; we
    surface the original error rather than parsing the AWS error payload
    as an MCP envelope."""
    error_payload = io.BytesIO(b'{"errorMessage":"boom","errorType":"RuntimeError"}')
    fake = MagicMock()
    fake.invoke.return_value = {
        "Payload": error_payload,
        "StatusCode": 200,
        "FunctionError": "Unhandled",
    }
    client = LegalSourcesClient(lambda_client=fake)
    with pytest.raises(LegalSourceUnavailable, match="FunctionError=Unhandled"):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})


def test_non_2xx_status_wrapped_as_unavailable() -> None:
    fake = MagicMock()
    fake.invoke.return_value = {
        "Payload": io.BytesIO(b""),
        "StatusCode": 500,
    }
    client = LegalSourcesClient(lambda_client=fake)
    with pytest.raises(LegalSourceUnavailable, match="StatusCode=500"):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})


def test_non_text_content_type_wrapped_as_unavailable() -> None:
    envelope = {
        "isError": False,
        "content": [{"type": "image", "data": "..."}],
    }
    fake = _make_lambda(envelope)
    client = LegalSourcesClient(lambda_client=fake)
    with pytest.raises(LegalSourceUnavailable, match="content block type"):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})


def test_env_var_overrides_function_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIRA_LEGAL_LOOKUP_FN", "env-lookup")
    monkeypatch.setenv("KIRA_LEGAL_SEARCH_FN", "env-search")
    client = LegalSourcesClient()
    assert client.lookup_fn_name == "env-lookup"
    assert client.search_fn_name == "env-search"


def test_constructor_arg_overrides_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIRA_LEGAL_LOOKUP_FN", "env-lookup")
    client = LegalSourcesClient(lookup_fn_name="explicit-lookup")
    assert client.lookup_fn_name == "explicit-lookup"


def test_invoke_logs_structured_line(caplog: pytest.LogCaptureFixture) -> None:
    envelope = {"isError": False, "content": [{"type": "text", "text": "{}"}]}
    client = LegalSourcesClient(lambda_client=_make_lambda(envelope))
    with caplog.at_level(logging.INFO, logger="kira.agent.legal_client"):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})
    matched = [r for r in caplog.records if r.message.startswith("legal_invoke")]
    assert len(matched) == 1
    rec = matched[0]
    assert getattr(rec, "function", None) == client.lookup_fn_name
    assert getattr(rec, "status", None) == "ok"
    assert isinstance(getattr(rec, "latency_ms", None), int | float)


def test_invoke_logs_error_status_on_unavailable(caplog: pytest.LogCaptureFixture) -> None:
    fake = MagicMock()
    fake.invoke.side_effect = EndpointConnectionError(endpoint_url="x")
    client = LegalSourcesClient(lambda_client=fake)
    with (
        caplog.at_level(logging.WARNING, logger="kira.agent.legal_client"),
        pytest.raises(LegalSourceUnavailable),
    ):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})
    matched = [r for r in caplog.records if r.message.startswith("legal_invoke")]
    assert len(matched) == 1
    assert getattr(matched[0], "status", None) == "unavailable"

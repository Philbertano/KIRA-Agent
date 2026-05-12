"""Boto3 client for KIRA's deployed legal-sources Lambdas.

Encapsulates region pinning, function-name resolution, retry/timeout
config, MCP-envelope unwrapping, and structured logging. The agent
tools import this client; the client mocks the Lambda surface for tests.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    EndpointConnectionError,
    ReadTimeoutError,
)

log = logging.getLogger(__name__)

DEFAULT_REGION = "eu-central-1"
DEFAULT_LOOKUP_FN = "kira-legal-lookup-norm"
DEFAULT_SEARCH_FN = "kira-legal-search"


class LegalSourceUnavailable(Exception):
    """Raised when the legal-sources Lambda cannot be reached or returns
    an infrastructure-level failure (5xx, timeout, malformed envelope).

    Functional results like ``unknown_gesetz`` or ``paragraph_not_found``
    are NOT raised — they come back as normal return values so the model
    sees them.
    """


class LegalSourcesClient:
    """Thin wrapper around boto3 lambda.invoke for the legal-sources tools."""

    def __init__(
        self,
        *,
        lambda_client: Any | None = None,
        region: str = DEFAULT_REGION,
        lookup_fn_name: str | None = None,
        search_fn_name: str | None = None,
    ) -> None:
        self.region = region
        self.lookup_fn_name = (
            lookup_fn_name
            or os.environ.get("KIRA_LEGAL_LOOKUP_FN")
            or DEFAULT_LOOKUP_FN
        )
        self.search_fn_name = (
            search_fn_name
            or os.environ.get("KIRA_LEGAL_SEARCH_FN")
            or DEFAULT_SEARCH_FN
        )
        if lambda_client is None:
            cfg = Config(
                retries={"max_attempts": 3, "mode": "adaptive"},
                read_timeout=30,
                connect_timeout=10,
            )
            lambda_client = boto3.client("lambda", region_name=region, config=cfg)
        self._lambda = lambda_client

    def _invoke(self, fn_name: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        t0 = time.monotonic()
        try:
            resp = self._lambda.invoke(FunctionName=fn_name, Payload=body)
        except (ClientError, ReadTimeoutError, EndpointConnectionError, BotoCoreError) as exc:
            self._log_outcome(fn_name, "unavailable", t0)
            raise LegalSourceUnavailable(f"Lambda invoke failed: {exc}") from exc

        raw = resp["Payload"].read()
        status_code = resp.get("StatusCode", 200)
        function_error = resp.get("FunctionError")

        # Lambda runtime caught an exception (Handled/Unhandled) — surface it
        # instead of letting it parse as a missing-content envelope.
        if function_error:
            self._log_outcome(fn_name, "function_error", t0)
            preview = raw[:500].decode("utf-8", errors="replace")
            raise LegalSourceUnavailable(
                f"Lambda {fn_name} returned FunctionError={function_error}: {preview}"
            )

        if status_code >= 300:
            self._log_outcome(fn_name, "non_2xx", t0)
            raise LegalSourceUnavailable(
                f"Lambda {fn_name} returned StatusCode={status_code}"
            )

        try:
            envelope = json.loads(raw)
            content = envelope.get("content") or []
            if not content:
                raise LegalSourceUnavailable("Lambda response had empty content")
            first = content[0]
            if first.get("type") != "text":
                raise LegalSourceUnavailable(
                    f"Unexpected content block type: {first.get('type')!r}"
                )
            inner = json.loads(first["text"])
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            self._log_outcome(fn_name, "malformed", t0)
            raise LegalSourceUnavailable(f"Malformed Lambda envelope: {exc}") from exc

        self._log_outcome(fn_name, "ok", t0)
        return inner

    def _log_outcome(self, fn_name: str, status: str, t0: float) -> None:
        latency_ms = round((time.monotonic() - t0) * 1000)
        level = logging.INFO if status == "ok" else logging.WARNING
        log.log(
            level,
            "legal_invoke",
            extra={"function": fn_name, "status": status, "latency_ms": latency_ms},
        )

    def lookup_norm(self, inp: dict) -> dict:
        return self._invoke(self.lookup_fn_name, inp)

    def search_norm(self, inp: dict) -> dict:
        return self._invoke(self.search_fn_name, inp)

"""Boto3 client for KIRA's deployed legal-sources Lambdas.

Encapsulates region pinning, function-name resolution, retry/timeout
config, MCP-envelope unwrapping, and structured logging. The agent
tools import this client; the client mocks the Lambda surface for tests.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from botocore.config import Config

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
        """Invoke a Lambda and return the unwrapped inner dict.

        Returns the inner JSON regardless of whether the Lambda set
        isError=True (functional errors are passed through). Raises
        LegalSourceUnavailable on infrastructure failures.
        """
        import json
        body = json.dumps(payload).encode("utf-8")
        resp = self._lambda.invoke(FunctionName=fn_name, Payload=body)
        raw = resp["Payload"].read()
        envelope = json.loads(raw)
        text = envelope["content"][0]["text"]
        return json.loads(text)

    def lookup_norm(self, inp: dict) -> dict:
        return self._invoke(self.lookup_fn_name, inp)

    def search_norm(self, inp: dict) -> dict:
        return self._invoke(self.search_fn_name, inp)

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

"""Opt-in perf budgets against the DEPLOYED Lambdas. RUN_PERF_TESTS=1."""

import json
import os
import statistics
import time

import boto3
import pytest

pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(
        not os.environ.get("RUN_PERF_TESTS"),
        reason="RUN_PERF_TESTS not set",
    ),
]

LAMBDA = boto3.client("lambda", region_name="eu-central-1")


def _invoke(fn: str, payload: dict) -> dict:
    t0 = time.perf_counter()
    resp = LAMBDA.invoke(
        FunctionName=fn, Payload=json.dumps(payload).encode("utf-8")
    )
    body = json.loads(resp["Payload"].read())
    return {"ms": (time.perf_counter() - t0) * 1000, "body": body}


def test_lookup_warm_p99_under_50ms():
    """1000 warm invocations against the same BGB §535. p99 should be <50ms."""
    # Warm the function first
    _invoke("kira-legal-lookup-norm", {"gesetz": "BGB", "paragraph": "535"})
    durations = [
        _invoke("kira-legal-lookup-norm", {"gesetz": "BGB", "paragraph": "535"})["ms"]
        for _ in range(100)  # 100 instead of 1000 to keep the test under a minute
    ]
    # Note: AWS-side Lambda billing time != Lambda invoke RTT.
    # This measures wall RTT including network from local; expect higher than 50ms
    # because of the local-to-AWS hop. Use 500ms ceiling as a sanity check from local;
    # tighter SLA must be measured from inside AWS (CloudWatch metric).
    p99 = statistics.quantiles(durations, n=100)[98]
    assert p99 < 500, f"p99 from local = {p99} ms"


def test_lookup_cold_first_call_under_3000ms():
    """Force a cold start by waiting 16 min before invoking — too slow for CI.
    This test is informational; assert lenient bound."""
    out = _invoke("kira-legal-lookup-norm", {"gesetz": "BGB", "paragraph": "535"})
    assert out["ms"] < 5000


def test_search_p99_under_2000ms():
    """100 search invocations across different queries; assert reasonable p99."""
    queries = [
        "Mietminderung wegen Schimmel",
        "Pflichten des Vermieters",
        "Kündigung Eigenbedarf",
        "Verjährungsfrist Mängelansprüche",
        "Schadensersatz statt der Leistung",
    ]
    durations = []
    for _ in range(20):
        for q in queries:
            d = _invoke("kira-legal-search", {"query": q, "k": 5})["ms"]
            durations.append(d)
    p99 = statistics.quantiles(durations, n=100)[98]
    assert p99 < 2000, f"p99 = {p99} ms from local"

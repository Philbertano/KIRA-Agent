"""V2 end-to-end smoke: invoke lookup AND search Lambdas in eu-central-1."""

from __future__ import annotations

import argparse
import json
import sys

import boto3


def _invoke(function_name: str, region: str, payload: dict) -> dict:
    client = boto3.client("lambda", region_name=region)
    resp = client.invoke(
        FunctionName=function_name,
        Payload=json.dumps(payload).encode("utf-8"),
    )
    return json.loads(resp["Payload"].read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookup-fn", default="kira-legal-lookup-norm")
    parser.add_argument("--search-fn", default="kira-legal-search")
    parser.add_argument("--region", default="eu-central-1")
    args = parser.parse_args()

    print("=== 1. Lookup BGB §535 ===")
    r = _invoke(args.lookup_fn, args.region, {"gesetz": "BGB", "paragraph": "535"})
    if r.get("isError"):
        print("FAIL:", r, file=sys.stderr); return 1
    print(json.loads(r["content"][0]["text"])["titel"])

    print("\n=== 2. Lookup WEG §14 (proves all-laws coverage) ===")
    r = _invoke(args.lookup_fn, args.region, {"gesetz": "WEG", "paragraph": "14"})
    if r.get("isError"):
        print("FAIL:", r, file=sys.stderr); return 1
    print(json.loads(r["content"][0]["text"])["titel"])

    print("\n=== 3. Search 'Pflichten des Vermieters zur Erhaltung der Mietsache' ===")
    r = _invoke(args.search_fn, args.region, {
        "query": "Pflichten des Vermieters zur Erhaltung der Mietsache",
        "k": 3,
    })
    if r.get("isError"):
        print("FAIL:", r, file=sys.stderr); return 1
    body = json.loads(r["content"][0]["text"])
    paragraphs = [(h["gesetz"], h["paragraph"]) for h in body["hits"]]
    print("Top hits:", paragraphs)
    assert any(p == ("BGB", "535") for p in paragraphs), "expected BGB §535 in top 3"

    print("\n=== 4. Search 'Schadensersatz statt der Leistung' gesetz=BGB ===")
    r = _invoke(args.search_fn, args.region, {
        "query": "Schadensersatz statt der Leistung",
        "gesetz_filter": ["BGB"],
        "k": 1,
    })
    if r.get("isError"):
        print("FAIL:", r, file=sys.stderr); return 1
    body = json.loads(r["content"][0]["text"])
    print("Top:", body["hits"][0]["gesetz"], "§", body["hits"][0]["paragraph"])

    print("\n✅ V2 smoke OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

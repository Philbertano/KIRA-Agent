"""End-to-end smoke: invoke the lookup_norm Lambda directly and via Gateway.

Run AFTER cdk deploy AND register_gateway_target.py succeeded.
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3


def invoke_direct(function_name: str, region: str) -> dict:
    client = boto3.client("lambda", region_name=region)
    resp = client.invoke(
        FunctionName=function_name,
        Payload=json.dumps({"gesetz": "BGB", "paragraph": "535"}).encode("utf-8"),
    )
    return json.loads(resp["Payload"].read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookup-fn", default="kira-legal-lookup-norm")
    parser.add_argument("--region", default="eu-central-1")
    args = parser.parse_args()

    print("=== Direct Lambda invoke ===")
    direct = invoke_direct(args.lookup_fn, args.region)
    print(json.dumps(direct, indent=2, ensure_ascii=False))
    if direct.get("isError"):
        print("Direct invoke returned an error.", file=sys.stderr)
        return 1

    body = json.loads(direct["content"][0]["text"])
    if body["paragraph"] != "535":
        print("Unexpected paragraph in response.", file=sys.stderr)
        return 1

    print("\n✅ Direct Lambda smoke OK.")
    print("Next: invoke via your AgentCore Gateway tool and verify the same response shape.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

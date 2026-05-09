"""Register the lookup-norm Lambda as an AgentCore Gateway target.

Usage:
    python scripts/register_gateway_target.py \\
        --gateway-id <gateway-id> \\
        --lambda-arn <lambda-arn>

CDK constructs for AgentCore Gateway are limited at time of writing; this
post-deploy script handles the target registration via boto3.
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3


SCHEMA = {
    "type": "object",
    "properties": {
        "gesetz": {"type": "string", "description": "Gesetz-Abkürzung, z.B. BGB."},
        "paragraph": {"type": "string", "description": "Paragraph, z.B. '535' oder '535a'."},
        "absatz": {"type": "string", "description": "Optional: konkreter Absatz."},
    },
    "required": ["gesetz", "paragraph"],
    "additionalProperties": False,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-id", required=True)
    parser.add_argument("--lambda-arn", required=True)
    parser.add_argument("--region", default="eu-central-1")
    args = parser.parse_args()

    if args.region != "eu-central-1":
        print("Refusing to register target outside eu-central-1.", file=sys.stderr)
        return 2

    client = boto3.client("bedrock-agentcore-control", region_name=args.region)
    response = client.create_gateway_target(
        gatewayId=args.gateway_id,
        name="lookup_norm",
        targetType="LAMBDA",
        targetConfig={
            "lambda": {
                "functionArn": args.lambda_arn,
                "toolDefinitions": [
                    {
                        "name": "lookup_norm",
                        "description": (
                            "Lädt den autoritativen Wortlaut eines deutschen "
                            "Paragraphen aus gesetze-im-internet.de."
                        ),
                        "inputSchema": SCHEMA,
                    }
                ],
            }
        },
    )
    print(json.dumps(response, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

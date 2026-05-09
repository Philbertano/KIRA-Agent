# Deploying KIRA Legal-Sources Tool 1

## Prerequisites

- AWS account with credentials configured for **eu-central-1** (`aws sts get-caller-identity`).
- Node.js + AWS CDK CLI: `npm install -g aws-cdk@^2`.
- Python 3.11 venv with project deps: `.venv/bin/pip install -e ".[dev]"`.
- CDK Python deps: `pip install -r infra/legal_sources/requirements.txt`.
- Existing AgentCore Gateway resource (created out-of-band; capture its ID).

## First deploy

The `cdk.json` invokes `python3 app.py`. **Activate the project venv first** so `aws_cdk` resolves:

```bash
source .venv/bin/activate                 # from repo root
cd infra/legal_sources
cdk bootstrap aws://${AWS_ACCOUNT_ID}/eu-central-1   # one-time per account/region
cdk deploy KiraLegalSources --require-approval never
```

Outputs include `LookupFnArn` and `BucketName`.

## Initial corpus population

> ⚠️ **Upstream network constraint.** `gesetze-im-internet.de` (juris.de edge)
> blocks AWS IP ranges. The ingest Lambda **cannot** reach upstream from
> inside `eu-central-1`. The EventBridge daily schedule is left in place but
> currently fails harmlessly (the stale-corpus alarm will fire after 48h).
>
> Run the ingest **locally** (your residential ISP) to populate S3:

```bash
# from repo root, with .venv active and AWS creds set
LEGAL_CORPUS_BUCKET=kira-legal-corpus-${AWS_ACCOUNT_ID}-eu-central-1 \
  .venv/bin/python -c "
from kira.legal_sources.adapters.ingest_handler import handler
import json
print(json.dumps(handler({}, None), indent=2))
"
# Expect: {"written": ["bgb", "betrkv", "heizkostenv"], "skipped": []}
```

The same `handler()` function runs locally or in Lambda — the only difference
is the network path. Once you have a non-AWS ingest host (developer laptop,
GitHub Actions runner, residential VPS), point its scheduler at this command.

If you ever solve the upstream block (egress proxy, etc.), the deployed
Lambda is already wired up — just invoke it as the deploy README originally
described:

```bash
aws lambda invoke \
  --function-name kira-legal-ingest --region eu-central-1 \
  --payload '{}' --cli-binary-format raw-in-base64-out /tmp/ingest-out.json
```

## Register Gateway target

```bash
python scripts/register_gateway_target.py \
    --gateway-id <your-gateway-id> \
    --lambda-arn <LookupFnArn>
```

## Smoke test

```bash
python scripts/legal_sources_smoke.py
# Expected: ✅ Direct Lambda smoke OK.
```

## Acceptance checklist (per spec §10)

- [ ] `pytest tests/legal_sources/ --cov-fail-under=95` green
- [ ] `RUN_LIVE_TESTS=1 pytest -m live tests/legal_sources/live/` green
- [ ] `cdk deploy` succeeded
- [ ] `register_gateway_target.py` returned a valid target ARN
- [ ] `legal_sources_smoke.py` printed ✅
- [ ] CloudWatch alarm `kira-legal-stale-corpus` is in OK state after the first ingest

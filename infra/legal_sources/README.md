# Deploying KIRA Legal-Sources Tool 1

## Prerequisites

- AWS account with credentials configured for **eu-central-1** (`aws sts get-caller-identity`).
- Node.js + AWS CDK CLI: `npm install -g aws-cdk@^2`.
- Python 3.11 venv with project deps: `.venv/bin/pip install -e ".[dev]"`.
- CDK Python deps: `pip install -r infra/legal_sources/requirements.txt`.
- Existing AgentCore Gateway resource (created out-of-band; capture its ID).

## First deploy

```bash
cd infra/legal_sources
cdk bootstrap aws://${AWS_ACCOUNT_ID}/eu-central-1   # one-time per account/region
cdk deploy KiraLegalSources --require-approval never
```

Outputs include `LookupFnArn` and `BucketName`.

## Initial corpus population

```bash
aws lambda invoke \
  --function-name kira-legal-ingest \
  --region eu-central-1 \
  --payload '{}' \
  /tmp/ingest-out.json
cat /tmp/ingest-out.json
# Expect: {"written": ["bgb", "betrkv", "heizkostenv"], "skipped": []}
```

The EventBridge rule will run daily at 02:00 UTC after this.

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

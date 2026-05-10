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

## Cloudflare Worker proxy (one-time setup)

`gesetze-im-internet.de` (juris.de edge) blocks AWS IP ranges, so the ingest
Lambda cannot fetch upstream directly. We route through a Cloudflare Worker
whose egress IPs are not on the blocklist. The Worker source lives in
`infra/cloudflare/juris-proxy/worker.js` (deploy via the Cloudflare dashboard
or `wrangler deploy`).

**Worker setup:**
1. Set the Worker secret in the Cloudflare dashboard:
   `kira-legaltext-gii-proxy → Settings → Variables and Secrets → Add Secret`
   - Name: `PROXY_SECRET`
   - Value: a random ~32-char string (e.g., `openssl rand -hex 32`)

**AWS-side setup (mirror the same secret):**
```bash
aws secretsmanager create-secret \
  --region eu-central-1 \
  --name kira-legal/juris-proxy-auth \
  --description "Bearer for kira-legaltext-gii-proxy Cloudflare Worker" \
  --secret-string "$YOUR_RANDOM_VALUE"
```

The CDK stack references this existing secret via `from_secret_name_v2`.
Rotate by `aws secretsmanager put-secret-value` + a `cdk deploy` (Lambda
env var resolves at deploy time).

## Initial corpus population

```bash
aws lambda invoke \
  --function-name kira-legal-ingest \
  --region eu-central-1 \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/ingest-out.json
cat /tmp/ingest-out.json
# Expect: {"written": ["bgb", "betrkv", "heizkostenv"], "skipped": []}
```

The EventBridge rule runs this daily at 02:00 UTC. Subsequent invocations
hash-skip unchanged content (`{"written": [], "skipped": ["bgb", ...]}`).

The same `handler()` function also runs locally without the proxy when
`LEGAL_INGEST_PROXY_URL` is unset — useful for ad-hoc dev refresh:

```bash
LEGAL_CORPUS_BUCKET=kira-legal-corpus-${AWS_ACCOUNT_ID}-eu-central-1 \
  .venv/bin/python -c "
from kira.legal_sources.adapters.ingest_handler import handler
import json
print(json.dumps(handler({}, None), indent=2))
"
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

# `kira-legaltext-gii-proxy` — Cloudflare Worker

A thin authenticated reverse-proxy in front of `gesetze-im-internet.de`.
Exists because juris.de's edge blocks AWS IP ranges, so the
`kira-legal-ingest` Lambda in `eu-central-1` cannot reach upstream
directly. Cloudflare's egress IPs aren't on that blocklist.

## Contract

```
GET /?url=https%3A%2F%2Fwww.gesetze-im-internet.de%2Fbgb%2Fxml.zip
Header  X-Proxy-Auth: <PROXY_SECRET>     (only required if PROXY_SECRET is set)
```

Allowed `url` values must start with `https://www.gesetze-im-internet.de`.
The Worker streams the upstream body through unchanged — no UTF-8
decoding, so binary `xml.zip` survives.

## Deploy

```bash
# Once: install wrangler and authenticate
npm install -g wrangler
wrangler login

# Deploy worker source
cd infra/cloudflare/juris-proxy
wrangler deploy

# Set the auth secret (must match the AWS Secrets Manager secret
# kira-legal/juris-proxy-auth consumed by the ingest Lambda)
wrangler secret put PROXY_SECRET
# (paste the same value used in `aws secretsmanager create-secret`)
```

Or use the dashboard:
- Workers → `kira-legaltext-gii-proxy` → Edit code → paste `worker.js`
- Workers → `kira-legaltext-gii-proxy` → Settings → Variables and Secrets
  → Add Secret → name `PROXY_SECRET`

## Test from the command line

```bash
SECRET=<the-value>
curl -H "X-Proxy-Auth: $SECRET" \
  "https://kira-legaltext-gii-proxy.philip-trempler.workers.dev/?url=https://www.gesetze-im-internet.de/bgb/xml.zip" \
  -o /tmp/bgb.zip
unzip -l /tmp/bgb.zip   # expect a single .xml entry
```

## Cost

Cloudflare Workers Free covers 100k req/day & 100 MB egress/day. Daily
ingest = 3 fetches × ~500 KB. Use is well under free tier indefinitely.

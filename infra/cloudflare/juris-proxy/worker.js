// Cloudflare Worker: gesetze-im-internet.de proxy.
//
// Deployed at https://kira-legaltext-gii-proxy.philip-trempler.workers.dev.
// Used by the kira-legal-ingest Lambda (eu-central-1) because juris.de's
// edge blocks AWS IP ranges directly. Cloudflare's egress IPs are not on
// that blocklist.
//
// Contract:
//   GET /?url=<encoded-upstream-url>
//   Header X-Proxy-Auth: <PROXY_SECRET>   (only required if PROXY_SECRET is set)
// Response: streams the upstream body unchanged (preserves binary content
// like xml.zip — earlier `await res.text()` corrupted bytes).

const ALLOWED_PREFIX = 'https://www.gesetze-im-internet.de';

export default {
  async fetch(request, env) {
    if (env.PROXY_SECRET) {
      const auth = request.headers.get('X-Proxy-Auth');
      if (auth !== env.PROXY_SECRET) {
        return new Response('unauthorized', { status: 401 });
      }
    }

    const url = new URL(request.url);
    const target = url.searchParams.get('url');
    if (!target || !target.startsWith(ALLOWED_PREFIX)) {
      return new Response('Missing or invalid URL', { status: 400 });
    }

    const headers = {
      'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
      Accept: '*/*',
    };

    try {
      const upstream = await fetch(target, { headers });
      // Stream the body through unchanged so binary content (xml.zip)
      // survives the proxy hop without UTF-8 decoding.
      return new Response(upstream.body, {
        status: upstream.status,
        headers: {
          'Content-Type':
            upstream.headers.get('content-type') || 'application/octet-stream',
        },
      });
    } catch (e) {
      return new Response('Fetch failed: ' + e.message, { status: 500 });
    }
  },
};

// Cloudflare Worker: gesetze-im-internet.de proxy.
//
// Streams binary bodies (xml.zip) through unchanged AND forwards conditional
// request headers (If-None-Match, If-Modified-Since) so the ingest Lambda
// can run cheap "did this change?" probes without re-downloading.

const ALLOWED_PREFIX = 'https://www.gesetze-im-internet.de';
const FORWARD_REQUEST_HEADERS = ['if-none-match', 'if-modified-since'];

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

    const upstreamHeaders = {
      'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
      Accept: '*/*',
    };
    for (const h of FORWARD_REQUEST_HEADERS) {
      const v = request.headers.get(h);
      if (v) upstreamHeaders[h] = v;
    }

    try {
      const upstream = await fetch(target, {
        method: request.method,  // pass-through GET or HEAD
        headers: upstreamHeaders,
      });
      const passThroughHeaders = {
        'Content-Type':
          upstream.headers.get('content-type') || 'application/octet-stream',
      };
      for (const h of ['etag', 'last-modified', 'content-length']) {
        const v = upstream.headers.get(h);
        if (v) passThroughHeaders[h] = v;
      }
      return new Response(upstream.body, {
        status: upstream.status,
        headers: passThroughHeaders,
      });
    } catch (e) {
      return new Response('Fetch failed: ' + e.message, { status: 500 });
    }
  },
};

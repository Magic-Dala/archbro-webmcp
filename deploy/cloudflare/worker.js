const ALLOWED_METHODS = new Set(['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']);

export default {
  async fetch(request, env) {
    if (!env.ARCHBRO_EDGE_TOKEN || !env.ARCHBRO_ORIGIN) {
      return new Response('Edge configuration unavailable.', {status: 503});
    }

    if (!ALLOWED_METHODS.has(request.method)) {
      return new Response('Method not allowed.', {status: 405});
    }

    const incomingUrl = new URL(request.url);
    const originUrl = new URL(incomingUrl.pathname + incomingUrl.search, env.ARCHBRO_ORIGIN);
    const headers = new Headers(request.headers);

    // Never trust a client-supplied origin credential. The Worker is the only
    // component allowed to add the private edge-to-origin credential.
    headers.delete('X-ArchBro-Edge-Token');
    headers.set('X-ArchBro-Edge-Token', env.ARCHBRO_EDGE_TOKEN);
    headers.set('X-Forwarded-Host', incomingUrl.host);
    headers.set('X-Forwarded-Proto', 'https');

    const init = {
      method: request.method,
      headers,
      redirect: 'manual',
    };
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      init.body = request.body;
    }

    const upstream = await fetch(originUrl.toString(), init);
    const responseHeaders = new Headers(upstream.headers);
    responseHeaders.set('X-ArchBro-Edge', 'cloudflare');

    const location = responseHeaders.get('Location');
    if (location && location.startsWith(env.ARCHBRO_ORIGIN)) {
      responseHeaders.set('Location', location.replace(env.ARCHBRO_ORIGIN, incomingUrl.origin));
    }

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  },
};

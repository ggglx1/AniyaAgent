export { AniyaAgentSession } from './session';

type Env = {
  ASSETS: Fetcher;
  ANIYAAGENT_SESSION: DurableObjectNamespace;
};

function parseCookie(header: string | null, name: string): string {
  if (!header) return '';
  const match = header.match(new RegExp(`(?:^|;\\s*)${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : '';
}

function sessionIdFrom(request: Request): string {
  const url = new URL(request.url);
  return url.searchParams.get('session') || parseCookie(request.headers.get('Cookie'), 'aniyaagent_session');
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === '/ws') {
      const sessionId = sessionIdFrom(request);
      if (!sessionId || sessionId === 'default') {
        return new Response('Missing session', { status: 400 });
      }
      const id = env.ANIYAAGENT_SESSION.idFromName(sessionId);
      return env.ANIYAAGENT_SESSION.get(id).fetch(request);
    }

    const sessionParam = url.searchParams.get('session');
    if (sessionParam && sessionParam !== 'default') {
      const clean = new URL(url);
      clean.searchParams.delete('session');
      return new Response(null, {
        status: 301,
        headers: {
          Location: clean.toString(),
          'Set-Cookie': `aniyaagent_session=${encodeURIComponent(sessionParam)}; Path=/; SameSite=Lax; Secure; Max-Age=31536000`,
        },
      });
    }

    return env.ASSETS.fetch(request);
  },
};

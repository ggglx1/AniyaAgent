import { createReadStream, existsSync, statSync } from 'node:fs';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { networkInterfaces } from 'node:os';
import { dirname, extname, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { WebSocket, WebSocketServer } from 'ws';

type ServerMessage =
  | { type: 'connection.ready'; data: { clientId: string } }
  | { type: 'agent.status'; data: { status: string; requestId?: string } }
  | { type: 'agent.run'; data: RunSnapshot }
  | { type: 'agent.output'; data: { role: 'assistant' | 'log' | 'error'; content: string } }
  | { type: 'agent.permission'; data: PermissionRequest }
  | { type: 'channels.list'; data: { channels: ChannelInfo[] } }
  | { type: 'models.list'; data: ModelProvidersPayload }
  | { type: 'models.error'; data: { message: string } }
  | { type: 'conversation.changed'; data: { track: TrackDescriptor } };

type ClientMessage =
  | { type: 'agent.send'; data?: { content?: string; track?: Partial<TrackDescriptor> } }
  | { type: 'agent.permission'; data?: { requestId?: string; allow?: boolean } }
  | { type: 'agent.resume'; data?: { requestId?: string; lastEventId?: number; track?: Partial<TrackDescriptor> } }
  | { type: 'channels.list'; data?: Record<string, never> }
  | { type: 'models.list'; data?: Record<string, never> }
  | { type: 'models.select'; data?: { provider?: string } }
  | { type: 'connection.ping'; data?: Record<string, never> };

type ChannelInfo = {
  channel_id: string;
  kind: string;
  trust_level: string;
};

type ModelProvider = {
  name: string;
  configured: boolean;
  active: boolean;
  base_url: string;
  model: string;
};

type ModelProvidersPayload = {
  active: string;
  providers: ModelProvider[];
};

type PermissionRequest = {
  requestId: string;
  tool: string;
  reason: string;
  input: unknown;
};

type ConversationMode = 'assistant' | 'coding' | 'qa';

type TrackDescriptor = {
  mode: ConversationMode;
  scope_id: string;
  track_id: string;
  repository_id: string;
  work_session_id: string;
  topic_id: string;
  can_send: boolean;
  unavailable_reason: string;
};

type SseEvent = {
  type?: string;
  event?: string;
  request_id?: string;
  status?: string;
  content?: string;
  error?: string;
  data?: unknown;
  tool?: string;
  reason?: string;
  input?: unknown;
  track?: TrackDescriptor;
  event_id?: number;
  event_sequence?: number;
  error_code?: string;
  metadata?: Record<string, unknown>;
};

type RunStatus = 'accepted' | 'queued' | 'running' | 'waiting_permission' | 'reconnecting' | 'completed' | 'failed' | 'cancelled' | 'timed_out' | 'unknown';

type RunSnapshot = {
  requestId: string;
  status: RunStatus;
  lastEventId: number;
  track: Partial<TrackDescriptor>;
  finalContent?: string;
  errorCode?: string;
  errorMessage?: string;
};

type RunConnection = RunSnapshot & {
  owner: WebSocket | null;
  terminal: boolean;
};

type RunApiState = {
  request_id: string;
  status: RunStatus;
  event_id: number;
  final_content?: string;
  error_code?: string;
  error_message?: string;
  track_id?: string;
};

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const clientRoot = resolve(__dirname, '..');
const repoRoot = resolve(clientRoot, '../..');
const publicDir = resolve(clientRoot, 'dist/public');
const sourcePublicDir = resolve(clientRoot, 'public');
const staticDir = existsSync(publicDir) ? publicDir : sourcePublicDir;
const runWebPath = resolve(repoRoot, 'main/channel/run_web.py');
const port = Number(process.env.ANIYAAGENT_CLIENT_PORT || process.env.PORT || 9527);
const webChannelPort = Number(process.env.ANIYAAGENT_WEB_CHANNEL_PORT || 9528);
const webChannelUrl = String(process.env.ANIYAAGENT_WEB_CHANNEL_URL || `http://127.0.0.1:${webChannelPort}`).replace(/\/$/, '');
const ownerToken = String(process.env.ANIYAAGENT_OWNER_TOKEN || '');
const ownerCookieName = 'aniya_owner_session';
const sessionTtlMs = Math.max(60 * 60 * 1000, Number(process.env.ANIYAAGENT_OWNER_SESSION_HOURS || 168) * 60 * 60 * 1000);
const maxLoginFailures = Math.max(3, Number(process.env.ANIYAAGENT_LOGIN_MAX_FAILURES || 5));
const loginBlockMs = Math.max(30_000, Number(process.env.ANIYAAGENT_LOGIN_BLOCK_SECONDS || 300) * 1000);
const webChannelToken = String(process.env.ANIYAAGENT_WEB_TOKEN || '');
const defaultCondaPython = resolve(process.env.USERPROFILE || '', 'anaconda3/envs/claude/python.exe');
const fallbackCondaPython = resolve(process.env.USERPROFILE || '', 'anaconda3/envs/Claude/python.exe');
const localVenvPython = resolve(repoRoot, 'main/.venv/Scripts/python.exe');
const pythonCommand = process.env.ANIYAAGENT_PYTHON
  || (existsSync(defaultCondaPython)
    ? defaultCondaPython
    : (existsSync(fallbackCondaPython)
      ? fallbackCondaPython
      : (existsSync(localVenvPython) ? localVenvPython : 'python')));

const mimeTypes: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
};

const clients = new Set<WebSocket>();
const ownerSessions = new Map<string, { expiresAt: number; createdAt: number }>();
const loginAttempts = new Map<string, { failures: number; blockedUntil: number }>();
let activeOwner: WebSocket | null = null;
let latestStatus = 'starting';

function sendJson(ws: WebSocket, message: ServerMessage): void {
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
}

function sendToOwner(message: ServerMessage): void {
  if (activeOwner) sendJson(activeOwner, message);
}

function broadcast(message: ServerMessage): void {
  for (const client of clients) sendJson(client, message);
}

function setStatus(status: string): void {
  latestStatus = status;
  sendToOwner({ type: 'agent.status', data: { status } });
}

function output(role: 'assistant' | 'log' | 'error', content: string): void {
  if (content.trim()) sendToOwner({ type: 'agent.output', data: { role, content } });
}

function clientId(): string {
  return `web_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function localUrls(): string[] {
  const urls = [`http://localhost:${port}`];
  for (const items of Object.values(networkInterfaces())) {
    for (const item of items || []) {
      if (item.family === 'IPv4' && !item.internal) urls.push(`http://${item.address}:${port}`);
    }
  }
  return urls;
}

function serveStatic(req: IncomingMessage, res: ServerResponse): void {
  const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
  if (!isOwner(req) && url.pathname !== '/assets/aniya-logo.jpg') {
    serveLogin(res);
    return;
  }
  let pathname = decodeURIComponent(url.pathname);
  if (pathname === '/') pathname = '/index.html';

  const candidate = resolve(staticDir, normalize(pathname).replace(/^[/\\]+/, ''));
  if (!candidate.startsWith(staticDir)) {
    res.writeHead(403);
    res.end('Forbidden');
    return;
  }

  const file = existsSync(candidate) && statSync(candidate).isFile()
    ? candidate
    : resolve(staticDir, 'index.html');

  if (!existsSync(file)) {
    res.writeHead(404);
    res.end('Run npm run build first.');
    return;
  }

  res.writeHead(200, {
    'content-type': mimeTypes[extname(file)] || 'application/octet-stream',
    'cache-control': file.endsWith('index.html') ? 'no-store' : 'public, max-age=60',
  });
  createReadStream(file).pipe(res);
}

function isOwner(req: IncomingMessage): boolean {
  const cookie = String(req.headers.cookie || '').split(';').map((value) => value.trim())
    .find((value) => value.startsWith(`${ownerCookieName}=`));
  const sessionId = cookie?.slice(ownerCookieName.length + 1) || '';
  const session = ownerSessions.get(sessionId);
  if (!session) return false;
  if (session.expiresAt <= Date.now()) {
    ownerSessions.delete(sessionId);
    return false;
  }
  return true;
}

function serveLogin(res: ServerResponse): void {
  const body = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>打开 Aniya</title><style>*{box-sizing:border-box}body{margin:0;min-height:100svh;display:grid;place-items:center;background:#f4f7f8;color:#172126;font:15px system-ui,-apple-system,"Segoe UI",sans-serif}.login{width:min(88vw,360px);text-align:center}.mark{width:68px;height:68px;margin:0 auto 24px;border-radius:50%;object-fit:cover;box-shadow:0 16px 40px #8ba8b333}h1{font-size:28px;font-weight:600;margin:0 0 8px}p{color:#6f7c82;margin:0 0 28px}.field{display:flex;gap:8px;padding:7px;border:1px solid #dbe3e6;border-radius:18px;background:#fff;box-shadow:0 18px 50px #59717a14}input{min-width:0;flex:1;border:0;outline:0;padding:10px 12px;font:inherit;background:transparent}button{border:0;border-radius:12px;padding:0 18px;background:#172126;color:#fff;font:inherit;cursor:pointer}</style></head><body><main class="login"><img class="mark" src="/assets/aniya-logo.jpg" alt=""><h1>欢迎回来</h1><p>验证身份后，Aniya 会继续陪在你身边。</p><form class="field" method="post" action="/auth"><input name="token" type="password" placeholder="Owner token" autocomplete="current-password" autofocus><button>打开</button></form></main></body></html>`;
  res.writeHead(401, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
  res.end(body);
}

function requestAddress(req: IncomingMessage): string {
  return String(req.headers['cf-connecting-ip'] || req.headers['x-forwarded-for'] || req.socket.remoteAddress || 'unknown').split(',')[0].trim();
}

function safeTokenEquals(value: string): boolean {
  const expected = Buffer.from(ownerToken);
  const received = Buffer.from(value);
  return expected.length === received.length && timingSafeEqual(expected, received);
}

function handleAuth(req: IncomingMessage, res: ServerResponse): void {
  const address = requestAddress(req);
  const attempt = loginAttempts.get(address);
  if (attempt && attempt.blockedUntil > Date.now()) {
    res.writeHead(429, { 'content-type': 'text/plain; charset=utf-8', 'retry-after': String(Math.ceil((attempt.blockedUntil - Date.now()) / 1000)) });
    res.end('登录尝试过多，请稍后再试。');
    return;
  }
  let raw = '';
  req.on('data', (chunk) => { raw += String(chunk); });
  req.on('end', () => {
    const token = new URLSearchParams(raw).get('token') || '';
    if (!ownerToken || !safeTokenEquals(token)) {
      const failures = (attempt?.failures || 0) + 1;
      loginAttempts.set(address, { failures, blockedUntil: failures >= maxLoginFailures ? Date.now() + loginBlockMs : 0 });
      serveLogin(res);
      return;
    }
    loginAttempts.delete(address);
    const sessionId = randomBytes(32).toString('base64url');
    ownerSessions.set(sessionId, { createdAt: Date.now(), expiresAt: Date.now() + sessionTtlMs });
    const secure = String(req.headers['x-forwarded-proto'] || '').includes('https') ? '; Secure' : '';
    res.writeHead(303, { location: '/', 'set-cookie': `${ownerCookieName}=${sessionId}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${Math.floor(sessionTtlMs / 1000)}${secure}` });
    res.end();
  });
}

function handleLogout(req: IncomingMessage, res: ServerResponse): void {
  const cookie = String(req.headers.cookie || '').split(';').map((value) => value.trim())
    .find((value) => value.startsWith(`${ownerCookieName}=`));
  if (cookie) ownerSessions.delete(cookie.slice(ownerCookieName.length + 1));
  res.writeHead(303, { location: '/', 'set-cookie': `${ownerCookieName}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0` });
  res.end();
}

class WebChannelBridge {
  private process: ChildProcessWithoutNullStreams | null = null;
  private starting: Promise<void> | null = null;
  private runs = new Map<string, RunConnection>();
  private consumers = new Map<string, Promise<void>>();

  async start(): Promise<void> {
    if (await this.healthy()) {
      if (!this.hasActiveRun()) setStatus('ready');
      return;
    }
    if (this.starting) return this.starting;
    this.starting = this.spawnAndWait();
    try {
      await this.starting;
    } finally {
      this.starting = null;
    }
  }

  async send(content: string, track: Partial<TrackDescriptor>, owner: WebSocket): Promise<void> {
    await this.start();
    latestStatus = 'busy';
    sendJson(owner, { type: 'agent.status', data: { status: 'busy' } });
    const submit = await this.postJson('/message', {
      text: content,
      user_id: 'local',
      ...track,
    });
    const requestId = String(submit.request_id || '');
    if (!requestId) throw new Error(`WebChannel did not return request_id: ${JSON.stringify(submit)}`);
    const resolvedTrack = (submit.track || track) as TrackDescriptor;
    const run: RunConnection = {
      requestId,
      status: 'accepted',
      lastEventId: 0,
      track: resolvedTrack,
      owner,
      terminal: false,
    };
    this.runs.set(requestId, run);
    this.notifyRun(run);
    await this.consumeRun(run);
  }

  async resume(requestId: string, lastEventId: number, track: Partial<TrackDescriptor>, owner: WebSocket): Promise<void> {
    if (!requestId) return;
    await this.start();
    let run = this.runs.get(requestId);
    if (!run) {
      run = {
        requestId,
        status: 'reconnecting',
        lastEventId: Math.max(0, Number(lastEventId) || 0),
        track,
        owner,
        terminal: false,
      };
      this.runs.set(requestId, run);
    } else {
      run.owner = owner;
      run.track = Object.keys(track).length ? track : run.track;
      run.lastEventId = Math.max(run.lastEventId, Math.max(0, Number(lastEventId) || 0));
    }

    const state = await this.runState(requestId);
    if (this.isTerminal(state.status)) {
      this.applyTerminalState(run, state, true);
      return;
    }
    run.status = 'reconnecting';
    this.notifyRun(run);
    await this.consumeRun(run);
  }

  hasActiveRun(): boolean {
    return [...this.runs.values()].some((run) => !run.terminal);
  }

  async listChannels(owner: WebSocket): Promise<void> {
    await this.start();
    const response = await fetch(`${webChannelUrl}/channels`, { headers: this.headers() });
    if (!response.ok) throw new Error(`GET /channels failed: ${response.status}`);
    const payload = await response.json() as { channels?: ChannelInfo[] };
    sendJson(owner, { type: 'channels.list', data: { channels: payload.channels || [] } });
  }

  async listModels(): Promise<ModelProvidersPayload> {
    await this.start();
    const response = await fetch(`${webChannelUrl}/llm/providers`, { headers: this.headers() });
    const payload = await response.json() as { ok?: boolean; error?: string } & Partial<ModelProvidersPayload>;
    if (!response.ok || payload.ok === false) {
      throw new Error(String(payload.error || `GET /llm/providers failed: ${response.status}`));
    }
    return {
      active: String(payload.active || ''),
      providers: Array.isArray(payload.providers) ? payload.providers : [],
    };
  }

  async selectModel(provider: string): Promise<ModelProvidersPayload> {
    await this.start();
    const payload = await this.postJson('/llm/provider', { provider }) as { active?: unknown; providers?: unknown };
    return {
      active: String(payload.active || ''),
      providers: Array.isArray(payload.providers) ? payload.providers as ModelProvider[] : [],
    };
  }

  async answerPermission(requestId: string, allow: boolean): Promise<void> {
    await this.start();
    await this.postJson('/permission', { request_id: requestId, allow });
  }

  async proxy(req: IncomingMessage, res: ServerResponse, pathname: string): Promise<void> {
    await this.start();
    const body = req.method === 'GET' || req.method === 'HEAD' ? undefined : await readRequestBody(req);
    const response = await fetch(`${webChannelUrl}${pathname}`, {
      method: req.method,
      headers: this.headers(body ? { 'content-type': String(req.headers['content-type'] || 'application/json') } : undefined),
      body: body ? new Uint8Array(body) : undefined,
    });
    const responseBody = Buffer.from(await response.arrayBuffer());
    res.writeHead(response.status, {
      'content-type': response.headers.get('content-type') || 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      'content-length': String(responseBody.length),
    });
    res.end(responseBody);
  }

  private async spawnAndWait(): Promise<void> {
    setStatus('starting WebChannel');
    this.process = spawn(
      pythonCommand,
      ['-u', runWebPath, '--host', '127.0.0.1', '--port', String(webChannelPort)],
      {
        cwd: repoRoot,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      },
    );

    this.process.stdout.on('data', (chunk) => output('log', chunk.toString('utf8').trimEnd()));
    this.process.stderr.on('data', (chunk) => output('log', chunk.toString('utf8').trimEnd()));
    this.process.on('exit', (code, signal) => {
      this.process = null;
      latestStatus = 'reconnecting';
      sendToOwner({ type: 'agent.status', data: { status: 'reconnecting' } });
      output('log', `WebChannel exited with code ${code ?? 'null'}${signal ? ` signal ${signal}` : ''}; recovering.`);
    });
    this.process.on('error', (error) => {
      this.process = null;
      setStatus('error');
      output('error', error.message);
    });

    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      if (await this.healthy()) {
        if (!this.hasActiveRun()) setStatus('ready');
        return;
      }
      await sleep(500);
    }
    throw new Error(`WebChannel did not become ready at ${webChannelUrl}`);
  }

  private async healthy(): Promise<boolean> {
    try {
      const response = await fetch(`${webChannelUrl}/health`, { signal: AbortSignal.timeout(1200) });
      return response.ok;
    } catch {
      return false;
    }
  }

  private async postJson(pathname: string, body: unknown): Promise<Record<string, unknown>> {
    const response = await fetch(`${webChannelUrl}${pathname}`, {
      method: 'POST',
      headers: this.headers({ 'content-type': 'application/json' }),
      body: JSON.stringify(body),
    });
    const payload = await response.json() as Record<string, unknown>;
    if (!response.ok || payload.ok === false) {
      throw new Error(String(payload.error || `${pathname} failed with ${response.status}`));
    }
    return payload;
  }

  private async consumeRun(run: RunConnection): Promise<void> {
    const existing = this.consumers.get(run.requestId);
    if (existing) return existing;
    const consumer = this.consumeRunLoop(run).finally(() => this.consumers.delete(run.requestId));
    this.consumers.set(run.requestId, consumer);
    return consumer;
  }

  private async consumeRunLoop(run: RunConnection): Promise<void> {
    let retry = 0;
    while (!run.terminal) {
      try {
        await this.start();
        const terminalSeen = await this.readStreamOnce(run);
        if (terminalSeen || run.terminal) return;
      } catch {
        // A transport failure is not an Agent failure. Query the durable run state below.
      }

      try {
        const state = await this.runState(run.requestId);
        if (this.isTerminal(state.status)) {
          this.applyTerminalState(run, state);
          return;
        }
      } catch {
        // The runtime may itself be restarting. Keep the run recoverable and retry.
      }

      run.status = 'reconnecting';
      latestStatus = 'reconnecting';
      this.notifyRun(run);
      if (run.owner) sendJson(run.owner, { type: 'agent.status', data: { status: 'reconnecting', requestId: run.requestId } });
      await sleep(Math.min(10_000, 500 * (2 ** Math.min(retry, 5))));
      retry += 1;
    }
  }

  private async readStreamOnce(run: RunConnection): Promise<boolean> {
    const query = new URLSearchParams({
      request_id: run.requestId,
      after_sequence: String(run.lastEventId),
    });
    const response = await fetch(`${webChannelUrl}/stream?${query}`, {
      headers: this.headers({ 'last-event-id': String(run.lastEventId) }),
      signal: AbortSignal.timeout(90_000),
    });
    if (!response.ok || !response.body) throw new Error(`GET /stream failed: ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) return false;
      buffer += decoder.decode(value, { stream: true });
      while (true) {
        const splitAt = buffer.indexOf('\n\n');
        if (splitAt < 0) break;
        const rawEvent = buffer.slice(0, splitAt);
        buffer = buffer.slice(splitAt + 2);
        const event = parseSse(rawEvent);
        if (!event) continue;
        const eventId = Number(event.event_id || event.event_sequence || 0);
        if (eventId > run.lastEventId) run.lastEventId = eventId;
        if (this.handleSseEvent(event, run)) return true;
        this.notifyRun(run);
      }
    }
  }

  private handleSseEvent(event: SseEvent, run: RunConnection): boolean {
    const owner = run.owner;
    switch (event.type) {
      case 'accepted':
      case 'queued':
      case 'running':
      case 'resumed':
        run.status = event.type === 'queued' ? 'queued' : event.type === 'accepted' ? 'accepted' : 'running';
        latestStatus = 'busy';
        if (owner) sendJson(owner, { type: 'agent.status', data: { status: 'busy', requestId: run.requestId } });
        return false;
      case 'llm_start':
        if (owner) sendJson(owner, { type: 'agent.output', data: { role: 'log', content: 'LLM request started' } });
        return false;
      case 'llm_end':
        if (owner) sendJson(owner, { type: 'agent.output', data: { role: 'log', content: 'LLM request completed' } });
        return false;
      case 'llm_error':
        if (owner) sendJson(owner, { type: 'agent.output', data: { role: 'log', content: stringifyEventData(event) } });
        return false;
      case 'tool_start':
        if (owner) sendJson(owner, { type: 'agent.output', data: { role: 'log', content: `Tool started: ${toolName(event)}` } });
        return false;
      case 'tool_end':
        if (owner) sendJson(owner, { type: 'agent.output', data: { role: 'log', content: `Tool completed: ${toolName(event)}` } });
        return false;
      case 'tool_blocked':
        if (owner) sendJson(owner, { type: 'agent.output', data: { role: 'error', content: `Tool blocked: ${toolName(event)}` } });
        return false;
      case 'permission_request':
        run.status = 'waiting_permission';
        if (owner) {
          sendJson(owner, {
            type: 'agent.permission',
            data: {
              requestId: String(event.request_id || ''),
              tool: String(event.tool || ''),
              reason: String(event.reason || ''),
              input: event.input,
            },
          });
        }
        return false;
      case 'completed':
      case 'failed':
      case 'cancelled':
      case 'timed_out':
        this.applyTerminalEvent(run, event);
        return true;
      default:
        return false;
    }
  }

  private async runState(requestId: string): Promise<RunApiState> {
    const response = await fetch(`${webChannelUrl}/runs/${encodeURIComponent(requestId)}`, { headers: this.headers() });
    const payload = await response.json() as { ok?: boolean; run?: RunApiState; error?: string };
    if (!response.ok || !payload.run) throw new Error(String(payload.error || `Run state unavailable: ${response.status}`));
    return payload.run;
  }

  private applyTerminalEvent(run: RunConnection, event: SseEvent): void {
    this.finishRun(run, String(event.type) as RunStatus, {
      finalContent: String(event.content || ''),
      errorCode: String(event.error_code || ''),
      errorMessage: String(event.error || ''),
    });
  }

  private applyTerminalState(run: RunConnection, state: RunApiState, replay = false): void {
    run.lastEventId = Math.max(run.lastEventId, Number(state.event_id || 0));
    this.finishRun(run, state.status, {
      finalContent: String(state.final_content || ''),
      errorCode: String(state.error_code || ''),
      errorMessage: String(state.error_message || ''),
    }, replay);
  }

  private finishRun(run: RunConnection, status: RunStatus, result: { finalContent?: string; errorCode?: string; errorMessage?: string }, replay = false): void {
    if (run.terminal && !replay) return;
    run.status = status;
    run.terminal = true;
    run.finalContent = result.finalContent || '';
    run.errorCode = result.errorCode || '';
    run.errorMessage = result.errorMessage || '';
    latestStatus = status === 'completed' ? 'ready' : status;
    const owner = run.owner;
    if (owner && status === 'completed' && run.finalContent) {
      sendJson(owner, { type: 'agent.output', data: { role: 'assistant', content: run.finalContent } });
    }
    if (owner && status !== 'completed' && run.errorMessage) {
      sendJson(owner, { type: 'agent.output', data: { role: 'error', content: run.errorMessage } });
    }
    if (owner) sendJson(owner, { type: 'agent.status', data: { status: latestStatus, requestId: run.requestId } });
    this.notifyRun(run);
    const track = run.track as TrackDescriptor;
    if (track?.track_id) broadcast({ type: 'conversation.changed', data: { track } });
  }

  private notifyRun(run: RunConnection): void {
    if (!run.owner) return;
    sendJson(run.owner, {
      type: 'agent.run',
      data: {
        requestId: run.requestId,
        status: run.status,
        lastEventId: run.lastEventId,
        track: run.track,
        finalContent: run.finalContent,
        errorCode: run.errorCode,
        errorMessage: run.errorMessage,
      },
    });
  }

  private isTerminal(status: string): status is RunStatus {
    return ['completed', 'failed', 'cancelled', 'timed_out'].includes(status);
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    return {
      ...(webChannelToken ? { authorization: `Bearer ${webChannelToken}` } : {}),
      ...extra,
    };
  }
}

function readRequestBody(req: IncomingMessage): Promise<Buffer> {
  return new Promise((resolveBody, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on('data', (chunk) => {
      const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      size += value.length;
      if (size > 2 * 1024 * 1024) {
        reject(new Error('Request body is too large.'));
        req.destroy();
        return;
      }
      chunks.push(value);
    });
    req.on('end', () => resolveBody(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

function parseSse(rawEvent: string): SseEvent | null {
  const lines = rawEvent.split(/\r?\n/);
  const dataLines = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart());
  if (!dataLines.length) return null;
  const idLine = lines.find((line) => line.startsWith('id:'));
  const eventLine = lines.find((line) => line.startsWith('event:'));
  const eventId = Number(idLine?.slice(3).trim() || 0);
  try {
    const parsed = JSON.parse(dataLines.join('\n')) as SseEvent;
    if (!parsed.type && eventLine) parsed.type = eventLine.slice(6).trim();
    if (eventId > 0) parsed.event_id = eventId;
    return parsed;
  } catch {
    return { type: eventLine?.slice(6).trim() || 'event', content: dataLines.join('\n'), event_id: eventId || undefined };
  }
}

function stringifyEventData(event: SseEvent): string {
  if (typeof event.data === 'string') return event.data;
  if (event.error) return String(event.error);
  return JSON.stringify(event.data ?? event, null, 2);
}

function toolName(event: SseEvent): string {
  const data = event.data as { tool?: { name?: string } } | undefined;
  return String(data?.tool?.name || 'unknown');
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const bridge = new WebChannelBridge();
void bridge.start().catch((error: unknown) => {
  setStatus('error');
  output('error', error instanceof Error ? error.message : String(error));
});

const server = createServer((req, res) => {
  if (req.method === 'POST' && req.url === '/auth') {
    handleAuth(req, res);
    return;
  }
  if (req.method === 'POST' && req.url === '/logout') {
    handleLogout(req, res);
    return;
  }
  const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
  if (url.pathname.startsWith('/api/')) {
    if (!isOwner(req)) {
      res.writeHead(401, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
      res.end(JSON.stringify({ ok: false, error: 'Unauthorized' }));
      return;
    }
    const allowed = new Set([
      '/conversation/state', '/conversation/history', '/conversation/track',
      '/memory/messages', '/memory/daily', '/memory/long-term', '/memory/export',
      '/memory/redact', '/memory/long-term/action', '/notifications',
      '/plans', '/plans/action', '/weixin/binding', '/weixin/binding/code', '/weixin/binding/invalidate',
    ]);
    const targetPath = url.pathname.slice(4);
    const runPathAllowed = /^\/runs\/(?:active|[A-Za-z0-9_-]+)$/.test(targetPath);
    if (!allowed.has(targetPath) && !runPathAllowed) {
      res.writeHead(404, { 'content-type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: 'Not found' }));
      return;
    }
    void bridge.proxy(req, res, `${targetPath}${url.search}`).catch((error: unknown) => {
      if (res.headersSent) return;
      res.writeHead(502, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
      res.end(JSON.stringify({ ok: false, error: error instanceof Error ? error.message : String(error) }));
    });
    return;
  }
  serveStatic(req, res);
});
const wss = new WebSocketServer({ noServer: true });

wss.on('connection', (ws) => {
  clients.add(ws);
  sendJson(ws, { type: 'connection.ready', data: { clientId: clientId() } });
  sendJson(ws, { type: 'agent.status', data: { status: latestStatus } });

  ws.on('message', (raw) => {
    let message: ClientMessage;
    try {
      message = JSON.parse(String(raw)) as ClientMessage;
    } catch {
      sendJson(ws, { type: 'agent.output', data: { role: 'error', content: 'Invalid JSON message.' } });
      return;
    }

    void handleClientMessage(message, ws).catch((error: unknown) => {
      if (message.type.startsWith('models.')) {
        sendJson(ws, {
          type: 'models.error',
          data: { message: error instanceof Error ? error.message : String(error) },
        });
        return;
      }
      if (message.type === 'agent.resume') {
        const requestId = String(message.data?.requestId || '');
        sendJson(ws, {
          type: 'agent.run',
          data: {
            requestId,
            status: 'unknown',
            lastEventId: Number(message.data?.lastEventId || 0),
            track: message.data?.track || {},
            errorCode: 'run_state_unavailable',
            errorMessage: '运行状态暂时无法确认。',
          },
        });
        sendJson(ws, { type: 'agent.status', data: { status: 'ready' } });
        return;
      }
      latestStatus = 'error';
      sendJson(ws, { type: 'agent.status', data: { status: 'error' } });
      sendJson(ws, { type: 'agent.output', data: { role: 'error', content: error instanceof Error ? error.message : String(error) } });
    });
  });

  ws.on('close', () => { clients.delete(ws); if (activeOwner === ws) activeOwner = null; });
  ws.on('error', () => { clients.delete(ws); if (activeOwner === ws) activeOwner = null; });
});

async function handleClientMessage(message: ClientMessage, owner: WebSocket): Promise<void> {
  if (message.type === 'connection.ping') {
    sendJson(owner, { type: 'agent.status', data: { status: latestStatus } });
    return;
  }

  if (message.type === 'agent.send') {
    const content = String(message.data?.content || '').trim();
    if (content) {
      if (activeOwner && activeOwner !== owner && bridge.hasActiveRun()) {
        throw new Error('Another authenticated device is currently running a request.');
      }
      activeOwner = owner;
      await bridge.send(content, message.data?.track || { mode: 'assistant' }, owner);
    }
    return;
  }

  if (message.type === 'agent.resume') {
    const requestId = String(message.data?.requestId || '').trim();
    if (requestId) {
      activeOwner = owner;
      await bridge.resume(
        requestId,
        Number(message.data?.lastEventId || 0),
        message.data?.track || {},
        owner,
      );
    }
    return;
  }

  if (message.type === 'channels.list') {
    await bridge.listChannels(owner);
    return;
  }

  if (message.type === 'models.list') {
    sendJson(owner, { type: 'models.list', data: await bridge.listModels() });
    return;
  }

  if (message.type === 'models.select') {
    const provider = String(message.data?.provider || '').trim();
    if (provider) sendJson(owner, { type: 'models.list', data: await bridge.selectModel(provider) });
    return;
  }

  if (message.type === 'agent.permission') {
    if (activeOwner !== owner) throw new Error('This permission request belongs to another device session.');
    const requestId = String(message.data?.requestId || '');
    if (requestId) await bridge.answerPermission(requestId, Boolean(message.data?.allow));
  }
}

server.on('upgrade', (req, socket, head) => {
  const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
  if (url.pathname !== '/ws' || !isOwner(req)) {
    socket.destroy();
    return;
  }
  wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws, req));
});

if (!ownerToken || ownerToken.startsWith('replace_') || ownerToken.length < 32) {
  throw new Error('ANIYAAGENT_OWNER_TOKEN is required. Refusing to start a private assistant without owner authentication.');
}

server.listen(port, '0.0.0.0', () => {
  console.log('AniyaAgent Web UI is running:');
  for (const url of localUrls()) console.log(`  ${url}`);
  console.log('');
  console.log(`WebChannel: ${webChannelUrl}`);
});

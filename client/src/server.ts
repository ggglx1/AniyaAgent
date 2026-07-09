import { createReadStream, existsSync, statSync } from 'node:fs';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { networkInterfaces } from 'node:os';
import { dirname, extname, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { WebSocket, WebSocketServer } from 'ws';

type ServerMessage =
  | { type: 'connection.ready'; data: { clientId: string } }
  | { type: 'agent.status'; data: { status: string } }
  | { type: 'agent.output'; data: { role: 'assistant' | 'log' | 'error'; content: string } }
  | { type: 'agent.permission'; data: PermissionRequest }
  | { type: 'channels.list'; data: { channels: ChannelInfo[] } };

type ClientMessage =
  | { type: 'agent.send'; data?: { content?: string } }
  | { type: 'agent.permission'; data?: { requestId?: string; allow?: boolean } }
  | { type: 'channels.list'; data?: Record<string, never> }
  | { type: 'connection.ping'; data?: Record<string, never> };

type ChannelInfo = {
  channel_id: string;
  kind: string;
  trust_level: string;
};

type PermissionRequest = {
  requestId: string;
  tool: string;
  reason: string;
  input: unknown;
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
};

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const clientRoot = resolve(__dirname, '..');
const repoRoot = resolve(clientRoot, '..');
const publicDir = resolve(clientRoot, 'dist/public');
const sourcePublicDir = resolve(clientRoot, 'public');
const staticDir = existsSync(publicDir) ? publicDir : sourcePublicDir;
const runWebPath = resolve(repoRoot, 'Channel/run_web.py');
const port = Number(process.env.ANIYAAGENT_CLIENT_PORT || process.env.PORT || 9527);
const webChannelPort = Number(process.env.ANIYAAGENT_WEB_CHANNEL_PORT || 9528);
const webChannelUrl = String(process.env.ANIYAAGENT_WEB_CHANNEL_URL || `http://127.0.0.1:${webChannelPort}`).replace(/\/$/, '');
const conversationId = String(process.env.ANIYAAGENT_WEB_CONVERSATION_ID || 'web');
const defaultCondaPython = resolve(process.env.USERPROFILE || '', 'anaconda3/envs/claude/python.exe');
const fallbackCondaPython = resolve(process.env.USERPROFILE || '', 'anaconda3/envs/Claude/python.exe');
const localVenvPython = resolve(repoRoot, 'Main/.venv/Scripts/python.exe');
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
let latestStatus = 'starting';

function sendJson(ws: WebSocket, message: ServerMessage): void {
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
}

function broadcast(message: ServerMessage): void {
  for (const client of clients) sendJson(client, message);
}

function setStatus(status: string): void {
  latestStatus = status;
  broadcast({ type: 'agent.status', data: { status } });
}

function output(role: 'assistant' | 'log' | 'error', content: string): void {
  if (content.trim()) broadcast({ type: 'agent.output', data: { role, content } });
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

class WebChannelBridge {
  private process: ChildProcessWithoutNullStreams | null = null;
  private starting: Promise<void> | null = null;

  async start(): Promise<void> {
    if (await this.healthy()) {
      setStatus('ready');
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

  async send(content: string): Promise<void> {
    await this.start();
    setStatus('busy');
    const submit = await this.postJson('/message', {
      text: content,
      conversation_id: conversationId,
      user_id: 'web',
    });
    const requestId = String(submit.request_id || '');
    if (!requestId) throw new Error(`WebChannel did not return request_id: ${JSON.stringify(submit)}`);
    await this.readStream(requestId);
  }

  async listChannels(): Promise<void> {
    await this.start();
    const response = await fetch(`${webChannelUrl}/channels`);
    if (!response.ok) throw new Error(`GET /channels failed: ${response.status}`);
    const payload = await response.json() as { channels?: ChannelInfo[] };
    broadcast({ type: 'channels.list', data: { channels: payload.channels || [] } });
  }

  async answerPermission(requestId: string, allow: boolean): Promise<void> {
    await this.start();
    await this.postJson('/permission', { request_id: requestId, allow });
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
      setStatus('offline');
      output('error', `WebChannel exited with code ${code ?? 'null'}${signal ? ` signal ${signal}` : ''}`);
    });
    this.process.on('error', (error) => {
      this.process = null;
      setStatus('error');
      output('error', error.message);
    });

    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      if (await this.healthy()) {
        setStatus('ready');
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
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    const payload = await response.json() as Record<string, unknown>;
    if (!response.ok || payload.ok === false) {
      throw new Error(String(payload.error || `${pathname} failed with ${response.status}`));
    }
    return payload;
  }

  private async readStream(requestId: string): Promise<void> {
    const response = await fetch(`${webChannelUrl}/stream?${new URLSearchParams({ request_id: requestId })}`);
    if (!response.ok || !response.body) throw new Error(`GET /stream failed: ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      while (true) {
        const splitAt = buffer.indexOf('\n\n');
        if (splitAt < 0) break;
        const rawEvent = buffer.slice(0, splitAt);
        buffer = buffer.slice(splitAt + 2);
        const parsed = parseSse(rawEvent);
        if (parsed && this.handleSseEvent(parsed)) return;
      }
    }
  }

  private handleSseEvent(event: SseEvent): boolean {
    switch (event.type) {
      case 'accepted':
        setStatus('busy');
        return false;
      case 'llm_start':
        output('log', 'LLM request started');
        return false;
      case 'llm_end':
        output('log', 'LLM request completed');
        return false;
      case 'llm_error':
        output('error', stringifyEventData(event));
        return false;
      case 'tool_start':
        output('log', `Tool started: ${toolName(event)}`);
        return false;
      case 'tool_end':
        output('log', `Tool completed: ${toolName(event)}`);
        return false;
      case 'tool_blocked':
        output('error', `Tool blocked: ${toolName(event)}`);
        return false;
      case 'permission_request':
        broadcast({
          type: 'agent.permission',
          data: {
            requestId: String(event.request_id || ''),
            tool: String(event.tool || ''),
            reason: String(event.reason || ''),
            input: event.input,
          },
        });
        return false;
      case 'done':
        if (event.error) output('error', String(event.error));
        if (event.content) output('assistant', String(event.content));
        setStatus(event.status === 'completed' ? 'ready' : String(event.status || 'ready'));
        return true;
      case 'error':
        output('error', String(event.error || 'WebChannel stream error'));
        setStatus('error');
        return true;
      default:
        return false;
    }
  }
}

function parseSse(rawEvent: string): SseEvent | null {
  const dataLines = rawEvent
    .split(/\r?\n/)
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart());
  if (!dataLines.length) return null;
  try {
    return JSON.parse(dataLines.join('\n')) as SseEvent;
  } catch {
    return { type: 'event', content: dataLines.join('\n') };
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

const server = createServer(serveStatic);
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

    void handleClientMessage(message).catch((error: unknown) => {
      setStatus('error');
      output('error', error instanceof Error ? error.message : String(error));
    });
  });

  ws.on('close', () => clients.delete(ws));
  ws.on('error', () => clients.delete(ws));
});

async function handleClientMessage(message: ClientMessage): Promise<void> {
  if (message.type === 'connection.ping') {
    broadcast({ type: 'agent.status', data: { status: latestStatus } });
    return;
  }

  if (message.type === 'agent.send') {
    const content = String(message.data?.content || '').trim();
    if (content) await bridge.send(content);
    return;
  }

  if (message.type === 'channels.list') {
    await bridge.listChannels();
    return;
  }

  if (message.type === 'agent.permission') {
    const requestId = String(message.data?.requestId || '');
    if (requestId) await bridge.answerPermission(requestId, Boolean(message.data?.allow));
  }
}

server.on('upgrade', (req, socket, head) => {
  const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
  if (url.pathname !== '/ws') {
    socket.destroy();
    return;
  }
  wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws, req));
});

server.listen(port, '0.0.0.0', () => {
  console.log('AniyaAgent Web UI is running:');
  for (const url of localUrls()) console.log(`  ${url}`);
  console.log('');
  console.log(`WebChannel: ${webChannelUrl}`);
});

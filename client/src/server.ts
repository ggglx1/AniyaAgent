import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { createReadStream, existsSync, statSync } from 'node:fs';
import { dirname, extname, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { networkInterfaces } from 'node:os';
import { WebSocketServer, WebSocket } from 'ws';

type ServerMessage =
  | { type: 'connection.ready'; data: { clientId: string } }
  | { type: 'connection.devices'; to?: string; data: { devices: { desktop: string; web: string } } }
  | { type: 'agent.status'; data: { status: string } }
  | { type: 'agent.output'; data: { role: 'assistant' | 'log' | 'error'; content: string } }
  | { type: 'agent.permission'; data: PermissionRequest };

type ClientMessage =
  | { type: 'agent.send'; data?: { content?: string } }
  | { type: 'agent.permission'; data?: { requestId?: string; allow?: boolean } }
  | { type: 'connection.ping'; data?: Record<string, never> };

type ClientPermissionMessage = Extract<ClientMessage, { type: 'agent.permission' }>;

type WireMessage = (ServerMessage | ClientMessage) & {
  to?: string;
  meta?: { clientId?: string; device?: string };
};

type BridgeEvent =
  | { type: 'ready'; model?: string }
  | { type: 'status'; status: string }
  | { type: 'output'; role: 'assistant' | 'log' | 'error'; content: string }
  | { type: 'permission_request'; request_id: string; tool: string; reason: string; input: unknown }
  | { type: 'error'; message: string };

type PermissionRequest = {
  requestId: string;
  tool: string;
  reason: string;
  input: unknown;
};

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const clientRoot = resolve(__dirname, '..');
const repoRoot = resolve(clientRoot, '..');
const publicDir = resolve(clientRoot, 'dist/public');
const sourcePublicDir = resolve(clientRoot, 'public');
const staticDir = existsSync(publicDir) ? publicDir : sourcePublicDir;
const bridgePath = resolve(clientRoot, 'python/happyclaude_bridge.py');
const port = Number(process.env.HAPPYCLAUDE_CLIENT_PORT || process.env.PORT || 9527);
const workerUrl = String(process.env.HAPPYCLAUDE_WORKER_URL || '').trim().replace(/\/$/, '');
const sessionId = String(process.env.HAPPYCLAUDE_SESSION_ID || '').trim() || randomUUID();
const defaultCondaPython = resolve(process.env.USERPROFILE || '', 'anaconda3/envs/Claude/python.exe');
const fallbackCondaPython = resolve(process.env.USERPROFILE || '', 'anaconda3/envs/claude/python.exe');
const localVenvPython = resolve(repoRoot, 'Main/.venv/Scripts/python.exe');
const pythonCommand = process.env.HAPPYCLAUDE_PYTHON
  || (existsSync(defaultCondaPython)
    ? defaultCondaPython
    : (existsSync(fallbackCondaPython)
      ? fallbackCondaPython
      : (existsSync(localVenvPython) ? localVenvPython : 'python3')));

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

function sendJson(ws: WebSocket, message: WireMessage): void {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(message));
  }
}

function broadcastLocal(clients: Set<WebSocket>, message: ServerMessage): void {
  for (const client of clients) sendJson(client, message);
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

function remoteAccessUrl(): string {
  if (!workerUrl) return '';
  return `${workerUrl}/?${new URLSearchParams({ session: sessionId }).toString()}`;
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

class JsonLineReader {
  private buffer = '';

  push(chunk: Buffer, onLine: (line: string) => void): void {
    this.buffer += chunk.toString('utf8');
    while (true) {
      const index = this.buffer.indexOf('\n');
      if (index < 0) break;
      const line = this.buffer.slice(0, index).trim();
      this.buffer = this.buffer.slice(index + 1);
      if (line) onLine(line);
    }
  }
}

class AgentBridge {
  private process: ChildProcessWithoutNullStreams | null = null;
  private reader = new JsonLineReader();
  private starting = false;

  constructor(private onEvent: (event: BridgeEvent) => void) {}

  start(): void {
    if (this.process || this.starting) return;
    this.starting = true;

    const child = spawn(pythonCommand, ['-u', bridgePath], {
      cwd: repoRoot,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    });
    this.process = child;
    this.starting = false;

    child.stdout.on('data', (chunk) => {
      this.reader.push(chunk, (line) => this.handleLine(line));
    });

    child.stderr.on('data', (chunk) => {
      this.onEvent({ type: 'output', role: 'log', content: chunk.toString('utf8').trimEnd() });
    });

    child.on('exit', (code, signal) => {
      this.process = null;
      this.onEvent({
        type: 'error',
        message: `Agent bridge exited with code ${code ?? 'null'}${signal ? ` signal ${signal}` : ''}`,
      });
    });

    child.on('error', (error) => {
      this.process = null;
      this.onEvent({ type: 'error', message: error.message });
    });
  }

  send(content: string): void {
    this.start();
    this.write({ type: 'send', content });
  }

  answerPermission(requestId: string, allow: boolean): void {
    this.write({ type: 'permission_response', request_id: requestId, allow });
  }

  private write(payload: unknown): void {
    if (!this.process || this.process.stdin.destroyed) {
      this.onEvent({ type: 'error', message: 'Agent bridge is not running.' });
      return;
    }
    this.process.stdin.write(`${JSON.stringify(payload)}\n`);
  }

  private handleLine(line: string): void {
    try {
      this.onEvent(JSON.parse(line) as BridgeEvent);
    } catch {
      this.onEvent({ type: 'output', role: 'log', content: line });
    }
  }
}

const clients = new Set<WebSocket>();
let latestStatus = 'starting';
let relay: RelayTransport | null = null;

function sendToWeb(message: ServerMessage): void {
  broadcastLocal(clients, message);
  relay?.send({ ...message, to: 'web' });
}

const bridge = new AgentBridge((event) => {
  if (event.type === 'ready') {
    latestStatus = event.model ? `ready (${event.model})` : 'ready';
    sendToWeb({ type: 'agent.status', data: { status: latestStatus } });
    return;
  }

  if (event.type === 'status') {
    latestStatus = event.status;
    sendToWeb({ type: 'agent.status', data: { status: latestStatus } });
    return;
  }

  if (event.type === 'output') {
    sendToWeb({ type: 'agent.output', data: { role: event.role, content: event.content } });
    return;
  }

  if (event.type === 'permission_request') {
    sendToWeb({
      type: 'agent.permission',
      data: {
        requestId: event.request_id,
        tool: event.tool,
        reason: event.reason,
        input: event.input,
      },
    });
    return;
  }

  if (event.type === 'error') {
    latestStatus = 'error';
    sendToWeb({ type: 'agent.status', data: { status: latestStatus } });
    sendToWeb({ type: 'agent.output', data: { role: 'error', content: event.message } });
  }
});

bridge.start();

const server = createServer(serveStatic);
const wss = new WebSocketServer({ noServer: true });

wss.on('connection', (ws) => {
  clients.add(ws);
  sendJson(ws, { type: 'connection.ready', data: { clientId: clientId() } });
  sendJson(ws, { type: 'agent.status', data: { status: latestStatus } });

  ws.on('message', (raw) => {
    let message: ClientMessage;
    try {
      message = JSON.parse(String(raw));
    } catch {
      sendJson(ws, { type: 'agent.output', data: { role: 'error', content: 'Invalid JSON message.' } });
      return;
    }

    if (message.type === 'connection.ping') {
      sendJson(ws, { type: 'agent.status', data: { status: latestStatus } });
      return;
    }

    if (message.type === 'agent.send') {
      const content = String(message.data?.content || '').trim();
      if (!content) return;
      bridge.send(content);
      return;
    }

    if (message.type === 'agent.permission') {
      const requestId = String(message.data?.requestId || '');
      if (!requestId) return;
      bridge.answerPermission(requestId, Boolean(message.data?.allow));
    }
  });

  ws.on('close', () => clients.delete(ws));
  ws.on('error', () => clients.delete(ws));
});

function handleClientMessage(message: WireMessage): void {
  if (message.type === 'connection.ping') {
    relay?.send({ type: 'agent.status', to: `web:${message.meta?.clientId || ''}`, data: { status: latestStatus } });
    return;
  }

  if (message.type === 'agent.send') {
    const content = String(message.data?.content || '').trim();
    if (content) bridge.send(content);
    return;
  }

  if (message.type === 'agent.permission') {
    const data = (message as ClientPermissionMessage).data;
    const requestId = String(data?.requestId || '');
    if (requestId) bridge.answerPermission(requestId, Boolean(data?.allow));
  }
}

class RelayTransport {
  private ws: WebSocket | null = null;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private printed = false;

  constructor(
    private baseUrl: string,
    private session: string,
    private onMessage: (message: WireMessage) => void,
  ) {}

  start(): void {
    this.connect();
  }

  send(message: WireMessage): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(message));
  }

  private connect(): void {
    const url = new URL(this.baseUrl);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    url.pathname = '/ws';
    url.search = new URLSearchParams({ session: this.session, device: 'desktop' }).toString();

    this.ws = new WebSocket(url.toString());
    this.ws.on('open', () => {
      if (this.printed) console.log('HappyClaude relay reconnected.');
      else {
        console.log('');
        console.log('HappyClaude Cloudflare relay is ready:');
        console.log(`  ${remoteAccessUrl()}`);
        console.log('');
        this.printed = true;
      }
      this.send({ type: 'agent.status', to: 'web', data: { status: latestStatus } });
    });
    this.ws.on('message', (raw) => {
      let message: WireMessage;
      try {
        message = JSON.parse(String(raw));
      } catch {
        return;
      }
      if (message.type === 'connection.ready' || message.type === 'connection.devices') return;
      this.onMessage(message);
    });
    this.ws.on('close', () => this.scheduleReconnect());
    this.ws.on('error', (error) => {
      console.error(`HappyClaude relay error: ${error.message}`);
    });
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    console.log('HappyClaude relay disconnected; reconnecting in 3s...');
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 3000);
  }
}

if (workerUrl) {
  relay = new RelayTransport(workerUrl, sessionId, handleClientMessage);
  relay.start();
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
  console.log('HappyClaude client is running:');
  for (const url of localUrls()) console.log(`  ${url}`);
  if (workerUrl) {
    console.log('');
    console.log('Cloudflare remote entry:');
    console.log(`  ${remoteAccessUrl()}`);
  }
});

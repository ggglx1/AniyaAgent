import { DurableObject } from 'cloudflare:workers';

type Attachment = {
  id: string;
  device: 'desktop' | 'web';
};

function socketId(): string {
  return crypto.randomUUID();
}

function parseDevice(request: Request): 'desktop' | 'web' | '' {
  const url = new URL(request.url);
  const device = url.searchParams.get('device');
  return device === 'desktop' || device === 'web' ? device : '';
}

export class AniyaAgentSession extends DurableObject {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname !== '/ws') return new Response('Not Found', { status: 404 });
    if (request.headers.get('Upgrade') !== 'websocket') {
      return new Response('Expected websocket', { status: 426 });
    }

    const device = parseDevice(request);
    if (!device) return new Response('Missing device', { status: 400 });

    const [client, server] = Object.values(new WebSocketPair());
    const id = socketId();
    this.ctx.acceptWebSocket(server, [device]);
    server.serializeAttachment({ id, device } satisfies Attachment);

    server.send(JSON.stringify({
      type: 'connection.ready',
      to: device,
      data: { clientId: id },
    }));
    this.broadcastDevices();

    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(String(message));
    } catch {
      return;
    }
    if (!payload.type) return;

    const att = (ws.deserializeAttachment() || {}) as Attachment;
    payload.meta = { ...(payload.meta as object || {}), clientId: att.id, device: att.device };

    if (payload.type === 'connection.ping') {
      ws.send(JSON.stringify({ type: 'connection.pong', to: att.device, data: {} }));
      return;
    }

    this.route(payload);
  }

  async webSocketClose(): Promise<void> {
    this.broadcastDevices();
  }

  async webSocketError(): Promise<void> {
    this.broadcastDevices();
  }

  private route(message: Record<string, unknown>): void {
    const target = message.to;
    let sockets: WebSocket[] = [];

    if (target === 'all') {
      sockets = this.ctx.getWebSockets();
    } else if (target === 'desktop' || target === 'web') {
      sockets = this.ctx.getWebSockets(target);
    } else if (typeof target === 'string' && target.startsWith('web:')) {
      const clientId = target.slice(4);
      sockets = this.ctx.getWebSockets('web').filter((ws) => {
        const att = (ws.deserializeAttachment() || {}) as Attachment;
        return att.id === clientId;
      });
    }

    if (!sockets.length) return;
    const text = JSON.stringify(message);
    for (const socket of sockets) {
      try {
        socket.send(text);
      } catch {
        // closed
      }
    }
  }

  private broadcastDevices(): void {
    const text = JSON.stringify({
      type: 'connection.devices',
      to: 'all',
      data: {
        devices: {
          desktop: this.ctx.getWebSockets('desktop').length > 0 ? 'connected' : 'disconnected',
          web: this.ctx.getWebSockets('web').length > 0 ? 'connected' : 'disconnected',
        },
      },
    });
    for (const socket of this.ctx.getWebSockets()) {
      try {
        socket.send(text);
      } catch {
        // closed
      }
    }
  }
}

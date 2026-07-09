type WsMessage =
  | { type: 'connection.ready'; data: { clientId: string } }
  | { type: 'agent.status'; data: { status: string } }
  | { type: 'agent.output'; data: { role: 'assistant' | 'log' | 'error'; content: string } }
  | { type: 'agent.permission'; data: { requestId: string; tool: string; reason: string; input: unknown } }
  | { type: 'channels.list'; data: { channels: ChannelInfo[] } };

type ChannelInfo = {
  channel_id: string;
  kind: string;
  trust_level: string;
};

const messages = document.querySelector<HTMLDivElement>('#messages')!;
const form = document.querySelector<HTMLFormElement>('#composer')!;
const input = document.querySelector<HTMLTextAreaElement>('#input')!;
const sendButton = document.querySelector<HTMLButtonElement>('#send')!;
const statusEl = document.querySelector<HTMLParagraphElement>('#status')!;
const dot = document.querySelector<HTMLSpanElement>('#dot')!;
const permission = document.querySelector<HTMLDivElement>('#permission')!;
const permissionReason = document.querySelector<HTMLParagraphElement>('#permission-reason')!;
const permissionInput = document.querySelector<HTMLPreElement>('#permission-input')!;
const allowButton = document.querySelector<HTMLButtonElement>('#allow')!;
const denyButton = document.querySelector<HTMLButtonElement>('#deny')!;
const channelsEl = document.querySelector<HTMLDivElement>('#channels')!;
const refreshChannelsButton = document.querySelector<HTMLButtonElement>('#refresh-channels')!;

let ws: WebSocket | null = null;
let pendingPermissionId = '';

function connect(): void {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${protocol}//${location.host}/ws?device=web`);
  setStatus('连接中...', 'pending');

  ws.onopen = () => {
    setStatus('已连接', 'connected');
    requestChannels();
  };

  ws.onmessage = (event) => {
    let message: WsMessage;
    try {
      message = JSON.parse(event.data) as WsMessage;
    } catch {
      addMessage('log', String(event.data));
      return;
    }

    if (message.type === 'agent.status') {
      const status = message.data.status;
      setStatus(status, status === 'error' || status === 'offline' ? 'offline' : 'connected');
      sendButton.disabled = status === 'busy' || status.startsWith('starting');
      return;
    }

    if (message.type === 'agent.output') {
      addMessage(message.data.role, message.data.content);
      return;
    }

    if (message.type === 'channels.list') {
      renderChannels(message.data.channels);
      return;
    }

    if (message.type === 'agent.permission') {
      pendingPermissionId = message.data.requestId;
      permissionReason.textContent = `${message.data.tool}: ${message.data.reason}`;
      permissionInput.textContent = JSON.stringify(message.data.input, null, 2);
      permission.classList.remove('hidden');
    }
  };

  ws.onclose = () => {
    setStatus('连接断开，重连中...', 'offline');
    sendButton.disabled = true;
    window.setTimeout(connect, 1500);
  };

  ws.onerror = () => {
    setStatus('连接异常', 'offline');
  };
}

function setStatus(text: string, state: 'pending' | 'connected' | 'offline'): void {
  statusEl.textContent = text;
  dot.className = `dot ${state === 'connected' ? 'connected' : state === 'offline' ? 'offline' : ''}`;
}

function addMessage(role: 'user' | 'assistant' | 'log' | 'error', content: string): void {
  if (!content.trim()) return;
  const item = document.createElement('div');
  item.className = `msg ${role}`;
  item.textContent = content;
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
}

function send(payload: unknown): void {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify(payload));
}

function requestChannels(): void {
  send({ type: 'channels.list' });
}

function renderChannels(channels: ChannelInfo[]): void {
  channelsEl.textContent = '';
  if (!channels.length) {
    channelsEl.textContent = '暂无已注册通道';
    return;
  }
  for (const channel of channels) {
    const item = document.createElement('div');
    item.className = `channel-card trust-${channel.trust_level}`;
    item.innerHTML = `
      <strong>${escapeHtml(channel.channel_id)}</strong>
      <span>${escapeHtml(channel.kind)} / ${escapeHtml(channel.trust_level)}</span>
    `;
    channelsEl.appendChild(item);
  }
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[char] || char));
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const content = input.value.trim();
  if (!content) return;
  addMessage('user', content);
  send({ type: 'agent.send', data: { content } });
  input.value = '';
  resizeInput();
});

input.addEventListener('input', resizeInput);
input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

allowButton.addEventListener('click', () => answerPermission(true));
denyButton.addEventListener('click', () => answerPermission(false));
refreshChannelsButton.addEventListener('click', requestChannels);

function answerPermission(allow: boolean): void {
  if (!pendingPermissionId) return;
  send({ type: 'agent.permission', data: { requestId: pendingPermissionId, allow } });
  pendingPermissionId = '';
  permission.classList.add('hidden');
}

function resizeInput(): void {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

addMessage('log', 'Web UI 已接入 AniyaAgent WebChannel 链路。');
connect();

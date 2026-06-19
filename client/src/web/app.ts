type WsMessage =
  | { type: 'connection.ready'; data: { clientId: string } }
  | { type: 'connection.devices'; data: { devices: { desktop?: string; web?: string } } }
  | { type: 'agent.status'; data: { status: string } }
  | { type: 'agent.output'; data: { role: 'assistant' | 'log' | 'error'; content: string } }
  | { type: 'agent.permission'; data: { requestId: string; tool: string; reason: string; input: unknown } };

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

let ws: WebSocket | null = null;
let pendingPermissionId = '';

function connect(): void {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${protocol}//${location.host}/ws?device=web`);
  setStatus('连接中...', 'pending');

  ws.onopen = () => {
    setStatus('已连接', 'connected');
  };

  ws.onmessage = (event) => {
    let message: WsMessage;
    try {
      message = JSON.parse(event.data);
    } catch {
      addMessage('log', String(event.data));
      return;
    }

    if (message.type === 'agent.status') {
      setStatus(message.data.status, message.data.status === 'error' ? 'offline' : 'connected');
      sendButton.disabled = message.data.status === 'busy';
      return;
    }

    if (message.type === 'connection.devices') {
      if (message.data.devices.desktop !== 'connected') {
        setStatus('等待本机 Agent 上线...', 'pending');
        sendButton.disabled = true;
      } else {
        setStatus('本机 Agent 已连接', 'connected');
        sendButton.disabled = false;
      }
      return;
    }

    if (message.type === 'agent.output') {
      addMessage(message.data.role, message.data.content);
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

addMessage('log', '打开的是本机 HappyClaude 客户端。手机和电脑在同一网络时，访问控制台打印的局域网地址。');
connect();

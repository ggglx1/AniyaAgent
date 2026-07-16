type WsMessage =
  | { type: 'connection.ready'; data: { clientId: string } }
  | { type: 'agent.status'; data: { status: string } }
  | { type: 'agent.output'; data: { role: 'assistant' | 'log' | 'error'; content: string } }
  | { type: 'agent.permission'; data: { requestId: string; tool: string; reason: string; input: unknown } }
  | { type: 'channels.list'; data: { channels: ChannelInfo[] } }
  | { type: 'models.list'; data: ModelProvidersPayload }
  | { type: 'models.error'; data: { message: string } };

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

type MessageRole = 'user' | 'assistant' | 'log' | 'error';
type ConnectionState = 'pending' | 'connected' | 'busy' | 'offline';

const messages = document.querySelector<HTMLElement>('#messages')!;
const form = document.querySelector<HTMLFormElement>('#composer')!;
const input = document.querySelector<HTMLTextAreaElement>('#input')!;
const sendButton = document.querySelector<HTMLButtonElement>('#send')!;
const statusEl = document.querySelector<HTMLElement>('#status')!;
const statusBadge = document.querySelector<HTMLElement>('#status-badge')!;
const dot = document.querySelector<HTMLElement>('#dot')!;
const greeting = document.querySelector<HTMLElement>('#greeting')!;
const currentTime = document.querySelector<HTMLElement>('#current-time')!;
const currentDate = document.querySelector<HTMLElement>('#current-date')!;
const presenceStatus = document.querySelector<HTMLElement>('#presence-status')!;
const activity = document.querySelector<HTMLElement>('#activity')!;
const activityText = document.querySelector<HTMLElement>('#activity-text')!;
const conversationOpen = document.querySelector<HTMLButtonElement>('#conversation-open')!;
const permission = document.querySelector<HTMLElement>('#permission')!;
const permissionReason = document.querySelector<HTMLElement>('#permission-reason')!;
const permissionInput = document.querySelector<HTMLElement>('#permission-input')!;
const allowButton = document.querySelector<HTMLButtonElement>('#allow')!;
const denyButton = document.querySelector<HTMLButtonElement>('#deny')!;
const channelsEl = document.querySelector<HTMLElement>('#channels')!;
const channelsCount = document.querySelector<HTMLElement>('#channels-count')!;
const channelsToggle = document.querySelector<HTMLButtonElement>('#channels-toggle')!;
const channelsPopover = document.querySelector<HTMLElement>('#channels-popover')!;
const refreshChannelsButton = document.querySelector<HTMLButtonElement>('#refresh-channels')!;
const modelsPopover = document.querySelector<HTMLElement>('#models-popover')!;
const activeModel = document.querySelector<HTMLElement>('#active-model')!;
const modelsSummary = document.querySelector<HTMLElement>('#models-summary')!;
const modelsEl = document.querySelector<HTMLElement>('#models')!;
const modelsFeedback = document.querySelector<HTMLElement>('#models-feedback')!;
const channelsFooter = channelsPopover.querySelector<HTMLElement>('.popover-foot')!;

channelsPopover.insertBefore(modelsPopover, channelsFooter);
modelsPopover.classList.remove('hidden');

const roleLabels: Record<MessageRole, string> = {
  user: '你',
  assistant: 'Aniya',
  log: '状态',
  error: '遇到了一点问题',
};

const roleMarks: Record<MessageRole, string> = {
  user: '你',
  assistant: 'A',
  log: '··',
  error: '!',
};

let ws: WebSocket | null = null;
let pendingPermissionId = '';
let reconnectTimer = 0;
let channelRefreshTimer = 0;
let activityTimer = 0;
let modelSelectionInFlight = false;

function connect(): void {
  window.clearTimeout(reconnectTimer);
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${protocol}//${location.host}/ws?device=web`);
  setStatus('connecting', 'pending');

  ws.onopen = () => {
    setStatus('ready', 'connected');
    requestChannels();
    requestModels();
  };

  ws.onmessage = (event) => {
    let message: WsMessage;
    try {
      message = JSON.parse(event.data) as WsMessage;
    } catch {
      return;
    }

    if (message.type === 'agent.status') {
      const status = message.data.status;
      const isBusy = status === 'busy' || status.startsWith('starting');
      const isOffline = status === 'error' || status === 'offline';
      setStatus(status, isOffline ? 'offline' : isBusy ? 'busy' : 'connected');
      sendButton.disabled = isBusy || isOffline;
      if (isBusy && activity.classList.contains('hidden')) setActivity('正在为你处理…');
      if (!isBusy) clearActivity(450);
      return;
    }

    if (message.type === 'agent.output') {
      if (message.data.role === 'log') {
        handleActivityLog(message.data.content);
      } else {
        clearActivity();
        addMessage(message.data.role, message.data.content);
      }
      return;
    }

    if (message.type === 'channels.list') {
      finishChannelRefresh();
      renderChannels(message.data.channels);
      return;
    }

    if (message.type === 'models.list') {
      modelSelectionInFlight = false;
      renderModels(message.data);
      return;
    }

    if (message.type === 'models.error') {
      modelSelectionInFlight = false;
      showModelFeedback(message.data.message);
      return;
    }

    if (message.type === 'agent.permission') {
      pendingPermissionId = message.data.requestId;
      permissionReason.textContent = `${message.data.tool}：${message.data.reason}`;
      permissionInput.textContent = JSON.stringify(message.data.input, null, 2);
      permission.classList.remove('hidden');
      allowButton.focus();
    }
  };

  ws.onclose = () => {
    finishChannelRefresh();
    clearActivity();
    setStatus('reconnecting', 'offline');
    sendButton.disabled = true;
    reconnectTimer = window.setTimeout(connect, 1500);
  };

  ws.onerror = () => {
    setStatus('error', 'offline');
  };
}

function setStatus(status: string, state: ConnectionState): void {
  statusEl.textContent = formatStatus(status);
  statusBadge.className = `status-badge ${state}`;
  dot.className = `dot ${state === 'connected' || state === 'busy' ? 'connected' : state === 'offline' ? 'offline' : ''}`;
  if (state === 'busy') presenceStatus.textContent = '正在专注处理';
  if (state === 'offline') presenceStatus.textContent = '正在等待重新连接';
}

function formatStatus(status: string): string {
  if (status.startsWith('starting')) return '正在醒来';
  const labels: Record<string, string> = {
    connecting: '正在醒来',
    reconnecting: '正在回来',
    ready: '在你身边',
    busy: '正在处理',
    offline: '暂时离开',
    error: '连接异常',
    completed: '在你身边',
  };
  return labels[status] || status;
}

function setWelcome(): void {
  const now = new Date();
  const hour = now.getHours();
  if (hour < 5) greeting.textContent = '夜深了，我在。';
  else if (hour < 11) greeting.textContent = '早上好。';
  else if (hour < 14) greeting.textContent = '中午好。';
  else if (hour < 18) greeting.textContent = '下午好。';
  else greeting.textContent = '晚上好。';

  currentTime.textContent = new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(now);
  currentDate.textContent = new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  }).format(now);
}

function setActivity(text: string, autoHideMs = 0): void {
  window.clearTimeout(activityTimer);
  activityText.textContent = text;
  activity.classList.remove('hidden');
  if (autoHideMs > 0) activityTimer = window.setTimeout(() => activity.classList.add('hidden'), autoHideMs);
}

function clearActivity(delayMs = 0): void {
  window.clearTimeout(activityTimer);
  if (delayMs > 0) {
    activityTimer = window.setTimeout(() => activity.classList.add('hidden'), delayMs);
  } else {
    activity.classList.add('hidden');
  }
}

function handleActivityLog(content: string): void {
  const value = content.toLowerCase();
  if (value.includes('llm request started')) {
    setActivity('正在理解你的话…');
  } else if (value.includes('tool started')) {
    setActivity('正在替你处理…');
  } else if (value.includes('completed') || value.includes('llm_end')) {
    setActivity('马上就好…', 1200);
  } else if (value.includes('starting') || value.includes('webchannel')) {
    setActivity('正在准备…', 1600);
  }
}

function addMessage(role: MessageRole, content: string): void {
  if (!content.trim()) return;
  document.querySelector('#empty-state')?.remove();

  const item = document.createElement('article');
  item.className = `msg ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'msg-avatar';
  avatar.setAttribute('aria-hidden', 'true');
  if (role === 'assistant') {
    const image = document.createElement('img');
    image.src = '/assets/aniya-chat-avatar.jpg';
    image.alt = '';
    avatar.appendChild(image);
  } else {
    avatar.textContent = roleMarks[role];
  }

  const body = document.createElement('div');
  body.className = 'msg-body';

  const meta = document.createElement('div');
  meta.className = 'msg-meta';

  const author = document.createElement('strong');
  author.textContent = roleLabels[role];

  const time = document.createElement('span');
  time.textContent = new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date());

  const text = document.createElement('div');
  text.className = 'msg-content';
  text.textContent = content;

  meta.append(author, time);
  body.append(meta, text);
  item.append(avatar, body);
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
}

function send(payload: unknown): boolean {
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(payload));
  return true;
}

function requestChannels(): void {
  if (!send({ type: 'channels.list' })) return;
  window.clearTimeout(channelRefreshTimer);
  refreshChannelsButton.disabled = true;
  refreshChannelsButton.classList.add('loading');
  channelRefreshTimer = window.setTimeout(finishChannelRefresh, 5000);
}

function finishChannelRefresh(): void {
  window.clearTimeout(channelRefreshTimer);
  refreshChannelsButton.disabled = false;
  refreshChannelsButton.classList.remove('loading');
}

function renderChannels(channels: ChannelInfo[]): void {
  channelsEl.replaceChildren();
  channelsCount.textContent = channels.length ? `${channels.length} 个通道连接正常` : '暂无可用连接';
  presenceStatus.textContent = channels.length ? `${channels.length} 个连接已就绪` : '正在等待连接';

  if (!channels.length) {
    const empty = document.createElement('div');
    empty.className = 'channel-empty';
    empty.textContent = '暂时没有可用通道';
    channelsEl.appendChild(empty);
    return;
  }

  for (const channel of channels) {
    const item = document.createElement('div');
    item.className = `channel-item trust-${channel.trust_level}`;

    const icon = document.createElement('div');
    icon.className = 'channel-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = channel.kind.slice(0, 2).toUpperCase();

    const copy = document.createElement('div');
    copy.className = 'channel-copy';

    const name = document.createElement('strong');
    name.textContent = formatChannelName(channel.channel_id);

    const kind = document.createElement('span');
    kind.textContent = channel.kind;

    const trust = document.createElement('span');
    trust.className = 'trust-badge';
    trust.textContent = formatTrust(channel.trust_level);

    copy.append(name, kind);
    item.append(icon, copy, trust);
    channelsEl.appendChild(item);
  }
}

function formatChannelName(channelId: string): string {
  const labels: Record<string, string> = {
    cli: '本机',
    web: '网页对话',
    cron: '定时任务',
    weixin: '微信',
  };
  return labels[channelId] || channelId;
}

function formatTrust(level: string): string {
  const labels: Record<string, string> = {
    high: '安全',
    medium: '受限',
    low: '谨慎',
  };
  return labels[level] || level;
}

function requestModels(): void {
  if (!send({ type: 'models.list' })) return;
  modelsSummary.textContent = '正在读取可用模型';
}

function renderModels(payload: ModelProvidersPayload): void {
  const active = payload.providers.find((provider) => provider.active) || payload.providers.find((provider) => provider.name === payload.active);
  const activeLabel = active ? formatProviderName(active.name) : '模型';
  activeModel.textContent = active ? active.model : activeLabel;
  modelsSummary.textContent = active
    ? `${activeLabel} · ${active.model}`
    : '暂无可用模型';
  modelsFeedback.classList.add('hidden');
  modelsFeedback.textContent = '';
  modelsEl.replaceChildren();

  if (!payload.providers.length) {
    const empty = document.createElement('div');
    empty.className = 'channel-empty';
    empty.textContent = '暂时没有可用模型';
    modelsEl.appendChild(empty);
    return;
  }

  for (const provider of payload.providers) {
    const option = document.createElement('button');
    option.className = `model-option${provider.active ? ' active' : ''}`;
    option.type = 'button';
    option.disabled = !provider.configured || provider.active || modelSelectionInFlight;
    option.setAttribute('aria-pressed', String(provider.active));
    option.title = provider.configured ? `切换到 ${formatProviderName(provider.name)}` : '此模型尚未配置密钥';

    const copy = document.createElement('span');
    copy.className = 'model-option-copy';
    const name = document.createElement('strong');
    name.textContent = formatProviderName(provider.name);
    const model = document.createElement('span');
    model.textContent = provider.model || provider.base_url;
    copy.append(name, model);

    const state = document.createElement('span');
    state.className = 'model-option-state';
    state.textContent = provider.active ? '当前使用' : (provider.configured ? '可切换' : '未配置');

    option.append(copy, state);
    option.addEventListener('click', () => selectModel(provider.name));
    modelsEl.appendChild(option);
  }
}

function selectModel(provider: string): void {
  if (modelSelectionInFlight || !send({ type: 'models.select', data: { provider } })) return;
  modelSelectionInFlight = true;
  modelsSummary.textContent = '正在切换模型';
  for (const option of modelsEl.querySelectorAll<HTMLButtonElement>('.model-option')) option.disabled = true;
}

function showModelFeedback(message: string): void {
  modelsFeedback.textContent = message || '模型切换失败';
  modelsFeedback.classList.remove('hidden');
  modelsSummary.textContent = '模型状态未更新';
  for (const option of modelsEl.querySelectorAll<HTMLButtonElement>('.model-option')) {
    if (!option.classList.contains('active')) option.disabled = false;
  }
}

function formatProviderName(name: string): string {
  const labels: Record<string, string> = {
    anthropic: 'Anthropic',
    openai: 'OpenAI',
  };
  return labels[name] || name;
}

function setChannelsOpen(open: boolean): void {
  channelsPopover.classList.toggle('hidden', !open);
  channelsToggle.setAttribute('aria-expanded', String(open));
  if (open) requestModels();
}

function setComposerOpen(open: boolean): void {
  form.classList.toggle('hidden', !open);
  conversationOpen.classList.toggle('hidden', open);
  if (open) window.requestAnimationFrame(() => input.focus());
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const content = input.value.trim();
  if (!content || sendButton.disabled) return;
  if (!send({ type: 'agent.send', data: { content } })) return;

  addMessage('user', content);
  input.value = '';
  resizeInput();
  setActivity('正在理解你的话…');
  setStatus('busy', 'busy');
  sendButton.disabled = true;
});

input.addEventListener('input', resizeInput);
input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

allowButton.addEventListener('click', () => answerPermission(true));
denyButton.addEventListener('click', () => answerPermission(false));
refreshChannelsButton.addEventListener('click', requestChannels);
conversationOpen.addEventListener('click', () => setComposerOpen(true));

channelsToggle.addEventListener('click', (event) => {
  event.stopPropagation();
  setChannelsOpen(channelsPopover.classList.contains('hidden'));
});

document.addEventListener('click', (event) => {
  const target = event.target as Node;
  if (!channelsPopover.classList.contains('hidden') && !document.querySelector('.nav-actions')?.contains(target)) {
    setChannelsOpen(false);
  }
  if (!form.classList.contains('hidden') && !form.contains(target) && !conversationOpen.contains(target)) {
    setComposerOpen(false);
  }
});

document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (!permission.classList.contains('hidden')) answerPermission(false);
  else if (!channelsPopover.classList.contains('hidden')) setChannelsOpen(false);
  else if (!form.classList.contains('hidden')) setComposerOpen(false);
});

function answerPermission(allow: boolean): void {
  if (!pendingPermissionId) return;
  send({ type: 'agent.permission', data: { requestId: pendingPermissionId, allow } });
  pendingPermissionId = '';
  permission.classList.add('hidden');
  input.focus();
}

function resizeInput(): void {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

setWelcome();
window.setInterval(setWelcome, 30_000);
connect();

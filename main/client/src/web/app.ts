type ConversationMode = 'assistant' | 'coding' | 'qa';
type MessageRole = 'user' | 'assistant' | 'tool' | 'system' | 'log' | 'error';
type ConnectionState = 'pending' | 'connected' | 'busy' | 'recovering' | 'failed' | 'offline';
type DrawerView = 'plans' | 'notifications' | 'daily' | 'memory' | 'binding';

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

type ConversationMessage = {
  message_id: string;
  role: MessageRole;
  content: unknown;
  created_at: string;
  day_date: string;
  track_sequence: number;
  redacted_at?: string;
};

type ChannelInfo = { channel_id: string; kind: string; trust_level: string };
type ModelProvider = { name: string; configured: boolean; active: boolean; base_url: string; model: string };
type ModelProvidersPayload = { active: string; providers: ModelProvider[] };

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

type WsMessage =
  | { type: 'connection.ready'; data: { clientId: string } }
  | { type: 'agent.status'; data: { status: string } }
  | { type: 'agent.run'; data: RunSnapshot }
  | { type: 'agent.output'; data: { role: 'assistant' | 'log' | 'error'; content: string } }
  | { type: 'agent.permission'; data: { requestId: string; tool: string; reason: string; input: unknown } }
  | { type: 'channels.list'; data: { channels: ChannelInfo[] } }
  | { type: 'models.list'; data: ModelProvidersPayload }
  | { type: 'models.error'; data: { message: string } }
  | { type: 'conversation.changed'; data: { track: TrackDescriptor } };

const messagesEl = document.querySelector<HTMLElement>('#messages')!;
const emptyState = document.querySelector<HTMLElement>('#empty-state')!;
const loadOlderButton = document.querySelector<HTMLButtonElement>('#load-older')!;
const form = document.querySelector<HTMLFormElement>('#composer')!;
const input = document.querySelector<HTMLTextAreaElement>('#input')!;
const sendButton = document.querySelector<HTMLButtonElement>('#send')!;
const conversationOpen = document.querySelector<HTMLButtonElement>('#conversation-open')!;
const statusEl = document.querySelector<HTMLElement>('#status')!;
const statusBadge = document.querySelector<HTMLElement>('#status-badge')!;
const dot = document.querySelector<HTMLElement>('#dot')!;
const greeting = document.querySelector<HTMLElement>('#greeting')!;
const currentTime = document.querySelector<HTMLElement>('#current-time')!;
const currentDate = document.querySelector<HTMLElement>('#current-date')!;
const presenceStatus = document.querySelector<HTMLElement>('#presence-status')!;
const welcomeCopy = document.querySelector<HTMLElement>('#welcome-copy')!;
const trackUnavailable = document.querySelector<HTMLElement>('#track-unavailable')!;
const trackLabel = document.querySelector<HTMLElement>('#track-label')!;
const modeToggle = document.querySelector<HTMLButtonElement>('#mode-toggle')!;
const modeMenu = document.querySelector<HTMLElement>('#mode-menu')!;
const modeMark = document.querySelector<HTMLElement>('#mode-mark')!;
const newTrackButton = document.querySelector<HTMLButtonElement>('#new-track')!;
const modeButtons = [...document.querySelectorAll<HTMLButtonElement>('[data-mode]')];
const activity = document.querySelector<HTMLElement>('#activity')!;
const activityText = document.querySelector<HTMLElement>('#activity-text')!;

const channelsToggle = document.querySelector<HTMLButtonElement>('#channels-toggle')!;
const channelsPopover = document.querySelector<HTMLElement>('#channels-popover')!;
const refreshChannelsButton = document.querySelector<HTMLButtonElement>('#refresh-channels')!;
const channelsEl = document.querySelector<HTMLElement>('#channels')!;
const channelsCount = document.querySelector<HTMLElement>('#channels-count')!;
const activeModel = document.querySelector<HTMLElement>('#active-model')!;
const modelsSummary = document.querySelector<HTMLElement>('#models-summary')!;
const modelsEl = document.querySelector<HTMLElement>('#models')!;
const modelsFeedback = document.querySelector<HTMLElement>('#models-feedback')!;
const openMemoryButton = document.querySelector<HTMLButtonElement>('#open-memory')!;
const logoutButton = document.querySelector<HTMLButtonElement>('#logout')!;

const drawer = document.querySelector<HTMLElement>('#memory-drawer')!;
const drawerScrim = document.querySelector<HTMLElement>('#drawer-scrim')!;
const closeMemoryButton = document.querySelector<HTMLButtonElement>('#close-memory')!;
const drawerContent = document.querySelector<HTMLElement>('#drawer-content')!;
const drawerSummary = document.querySelector<HTMLElement>('#drawer-summary')!;
const exportButton = document.querySelector<HTMLButtonElement>('#export-memory')!;
const newPlanButton = document.querySelector<HTMLButtonElement>('#new-plan')!;
const drawerTabs = [...document.querySelectorAll<HTMLButtonElement>('[data-view]')];

const permission = document.querySelector<HTMLElement>('#permission')!;
const permissionReason = document.querySelector<HTMLElement>('#permission-reason')!;
const permissionInput = document.querySelector<HTMLElement>('#permission-input')!;
const allowButton = document.querySelector<HTMLButtonElement>('#allow')!;
const denyButton = document.querySelector<HTMLButtonElement>('#deny')!;

const roleLabels: Record<MessageRole, string> = {
  user: '你', assistant: 'Aniya', tool: '处理记录', system: '系统', log: '动态', error: '未完成',
};
const roleMarks: Record<MessageRole, string> = {
  user: '你', assistant: 'A', tool: '·', system: '·', log: '·', error: '!',
};

let ws: WebSocket | null = null;
let reconnectTimer = 0;
let activityTimer = 0;
let channelRefreshTimer = 0;
let pendingPermissionId = '';
let connected = false;
let busy = false;
let modelSelectionInFlight = false;
let currentMode: ConversationMode = 'assistant';
let currentTrack: TrackDescriptor = defaultTrack('assistant');
let tracks = new Map<ConversationMode, TrackDescriptor>();
let records: ConversationMessage[] = [];
let hasMore = false;
let drawerView: DrawerView = 'plans';
let transientCounter = 0;
const activeRunStorageKey = 'aniya_active_run';
let activeRun: RunSnapshot | null = loadStoredRun();

function defaultTrack(mode: ConversationMode): TrackDescriptor {
  return {
    mode,
    scope_id: mode === 'assistant' ? 'personal' : mode === 'qa' ? 'knowledge' : '',
    track_id: mode === 'assistant' ? 'assistant:personal' : '',
    repository_id: '', work_session_id: '', topic_id: '',
    can_send: mode === 'assistant', unavailable_reason: '',
  };
}

function loadStoredRun(): RunSnapshot | null {
  try {
    const raw = localStorage.getItem(activeRunStorageKey);
    if (!raw) return null;
    const value = JSON.parse(raw) as RunSnapshot;
    return value.requestId ? value : null;
  } catch {
    localStorage.removeItem(activeRunStorageKey);
    return null;
  }
}

function storeRun(run: RunSnapshot | null): void {
  activeRun = run;
  if (run) localStorage.setItem(activeRunStorageKey, JSON.stringify(run));
  else localStorage.removeItem(activeRunStorageKey);
}

async function api<T>(pathname: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${pathname}`, {
    ...init,
    headers: { ...(init?.body ? { 'content-type': 'application/json' } : {}), ...(init?.headers || {}) },
  });
  const payload = await response.json() as T & { ok?: boolean; error?: string };
  if (!response.ok || payload.ok === false) throw new Error(payload.error || `${pathname} 请求失败`);
  return payload;
}

async function initializeConversation(): Promise<void> {
  try {
    const payload = await api<{ modes: TrackDescriptor[] }>('/conversation/state');
    for (const track of payload.modes || []) tracks.set(track.mode, track);
    currentTrack = tracks.get(currentMode) || defaultTrack(currentMode);
    await loadHistory(false);
  } catch (error) {
    showConversationError(error);
  }
  updateModeSurface();
}

async function loadHistory(older: boolean): Promise<void> {
  const before = older && records.length ? Math.min(...records.filter((item) => item.track_sequence > 0).map((item) => item.track_sequence)) : 0;
  const query = new URLSearchParams({
    mode: currentTrack.mode,
    scope_id: currentTrack.scope_id,
    track_id: currentTrack.track_id,
    repository_id: currentTrack.repository_id,
    work_session_id: currentTrack.work_session_id,
    topic_id: currentTrack.topic_id,
    limit: '50',
  });
  if (before) query.set('before_sequence', String(before));
  const previousHeight = messagesEl.scrollHeight;
  const payload = await api<{ track: TrackDescriptor; messages: ConversationMessage[]; has_more: boolean }>(`/conversation/history?${query}`);
  currentTrack = payload.track;
  tracks.set(currentMode, currentTrack);
  records = older ? mergeMessages(payload.messages, records) : payload.messages;
  hasMore = payload.has_more;
  renderMessages();
  if (older) messagesEl.scrollTop += messagesEl.scrollHeight - previousHeight;
  else messagesEl.scrollTop = messagesEl.scrollHeight;
  updateModeSurface();
}

function mergeMessages(...groups: ConversationMessage[][]): ConversationMessage[] {
  const merged = new Map<string, ConversationMessage>();
  for (const item of groups.flat()) merged.set(item.message_id, item);
  return [...merged.values()].sort((a, b) => (a.track_sequence || 0) - (b.track_sequence || 0));
}

function renderMessages(): void {
  for (const item of [...messagesEl.querySelectorAll('.msg')]) item.remove();
  loadOlderButton.classList.toggle('hidden', !hasMore || !records.length);
  emptyState.classList.toggle('hidden', records.length > 0);
  for (const record of records) messagesEl.appendChild(messageElement(record));
}

function messageElement(record: ConversationMessage): HTMLElement {
  const role = record.role in roleLabels ? record.role : 'system';
  const item = document.createElement('article');
  item.className = `msg ${role}${record.redacted_at ? ' redacted' : ''}`;
  item.dataset.messageId = record.message_id;

  const avatar = document.createElement('div');
  avatar.className = 'msg-avatar';
  avatar.setAttribute('aria-hidden', 'true');
  if (role === 'assistant') {
    const image = document.createElement('img'); image.src = '/assets/aniya-chat-avatar.jpg'; image.alt = ''; avatar.appendChild(image);
  } else avatar.textContent = roleMarks[role];

  const body = document.createElement('div'); body.className = 'msg-body';
  const meta = document.createElement('div'); meta.className = 'msg-meta';
  const author = document.createElement('strong'); author.textContent = roleLabels[role];
  const time = document.createElement('span'); time.textContent = formatTime(record.created_at);
  meta.append(author, time);

  if (!record.redacted_at && (role === 'user' || role === 'assistant') && !record.message_id.startsWith('temp_')) {
    const remove = document.createElement('button');
    remove.className = 'message-action'; remove.type = 'button'; remove.title = '删除这条记录'; remove.setAttribute('aria-label', '删除这条记录'); remove.textContent = '×';
    remove.addEventListener('click', () => requestRedaction(record, remove));
    meta.appendChild(remove);
  }

  const text = document.createElement('div'); text.className = 'msg-content';
  text.textContent = record.redacted_at ? '这条内容已被删除' : contentText(record.content);
  body.append(meta, text); item.append(avatar, body);
  return item;
}

function contentText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) return content.map((item) => contentText(item)).filter(Boolean).join('\n');
  if (content && typeof content === 'object') {
    const value = content as Record<string, unknown>;
    if (value.redacted) return '这条内容已被删除';
    if (typeof value.text === 'string') return value.text;
    if (typeof value.summary === 'string') return value.summary;
  }
  return content == null ? '' : JSON.stringify(content, null, 2);
}

async function requestRedaction(record: ConversationMessage, button: HTMLButtonElement): Promise<void> {
  if (button.dataset.confirm !== 'true') {
    button.dataset.confirm = 'true'; button.textContent = '删除'; button.classList.add('confirming');
    window.setTimeout(() => { button.dataset.confirm = ''; button.textContent = '×'; button.classList.remove('confirming'); }, 3000);
    return;
  }
  button.disabled = true;
  try {
    await api('/memory/redact', { method: 'POST', body: JSON.stringify({ message_id: record.message_id }) });
    await loadHistory(false);
  } catch (error) {
    showConversationError(error);
    button.disabled = false;
  }
}

function connect(): void {
  window.clearTimeout(reconnectTimer);
  setStatus('connecting', 'pending');
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onopen = () => { connected = true; send({ type: 'channels.list' }); send({ type: 'models.list' }); };
  ws.onmessage = (event) => handleWsMessage(JSON.parse(String(event.data)) as WsMessage);
  ws.onclose = () => {
    connected = false; busy = Boolean(activeRun); finishChannelRefresh();
    if (activeRun) {
      setActivity('连接中断，正在恢复…');
      setStatus('reconnecting', 'recovering');
    } else {
      clearActivity(); setStatus('reconnecting', 'offline');
    }
    updateComposerAvailability();
    reconnectTimer = window.setTimeout(connect, 1500);
  };
  ws.onerror = () => setStatus('error', 'offline');
}

function handleWsMessage(message: WsMessage): void {
  if (message.type === 'connection.ready') {
    connected = true;
    requestChannels(); requestModels();
    if (activeRun) {
      busy = true;
      setStatus('reconnecting', 'recovering');
      send({ type: 'agent.resume', data: { requestId: activeRun.requestId, lastEventId: activeRun.lastEventId, track: activeRun.track } });
    } else setStatus('ready', 'connected');
    return;
  }
  if (message.type === 'agent.run') {
    handleRunSnapshot(message.data);
    return;
  }
  if (message.type === 'agent.status') {
    const status = message.data.status;
    busy = ['busy', 'running', 'accepted', 'queued', 'waiting_permission', 'reconnecting'].includes(status);
    const state: ConnectionState = status === 'reconnecting'
      ? 'recovering'
      : busy
        ? 'busy'
        : ['ready', 'completed'].includes(status)
          ? 'connected'
          : ['failed', 'cancelled', 'timed_out'].includes(status)
            ? 'failed'
            : 'offline';
    setStatus(status, state);
    if (!busy) { clearActivity(700); }
    updateComposerAvailability();
    return;
  }
  if (message.type === 'agent.output') {
    if (message.data.role === 'log') { handleActivityLog(message.data.content); return; }
    if (message.data.role === 'assistant') addTransient('assistant', message.data.content);
    else addTransient('error', message.data.content);
    return;
  }
  if (message.type === 'conversation.changed') {
    if (sameTrack(message.data.track, currentTrack)) void loadHistory(false).catch(showConversationError);
    if (drawerView === 'notifications' && !drawer.classList.contains('hidden')) void loadDrawer();
    return;
  }
  if (message.type === 'channels.list') { finishChannelRefresh(); renderChannels(message.data.channels); return; }
  if (message.type === 'models.list') { modelSelectionInFlight = false; renderModels(message.data); return; }
  if (message.type === 'models.error') { modelSelectionInFlight = false; showModelFeedback(message.data.message); return; }
  if (message.type === 'agent.permission') {
    pendingPermissionId = message.data.requestId;
    permissionReason.textContent = `${message.data.tool}：${message.data.reason}`;
    permissionInput.textContent = JSON.stringify(message.data.input, null, 2);
    permission.classList.remove('hidden'); allowButton.focus();
  }
}

function handleRunSnapshot(run: RunSnapshot): void {
  if (['completed', 'failed', 'cancelled', 'timed_out', 'unknown'].includes(run.status)) {
    storeRun(null);
    busy = false;
    if (run.status === 'completed') {
      setStatus('completed', 'connected');
      clearActivity(500);
    } else if (run.status === 'unknown') {
      setStatus('unknown', 'failed');
      setActivity('上一次运行状态无法确认', 2600);
    } else {
      setStatus(run.status, 'failed');
      clearActivity(500);
    }
  } else {
    storeRun(run);
    busy = true;
    if (run.status === 'reconnecting') {
      setStatus('reconnecting', 'recovering');
      setActivity('连接中断，正在恢复…');
    } else {
      setStatus(run.status, 'busy');
    }
  }
  updateComposerAvailability();
}

function addTransient(role: 'assistant' | 'error', content: string): void {
  if (!content.trim()) return;
  records = records.filter((item) => !item.message_id.startsWith('temp_assistant_'));
  records.push({
    message_id: `temp_${role}_${++transientCounter}`, role, content,
    created_at: new Date().toISOString(), day_date: '', track_sequence: Number.MAX_SAFE_INTEGER - transientCounter,
  });
  renderMessages(); messagesEl.scrollTop = messagesEl.scrollHeight;
}

function sameTrack(left: Partial<TrackDescriptor>, right: Partial<TrackDescriptor>): boolean {
  return left.mode === right.mode && left.track_id === right.track_id;
}

async function switchMode(mode: ConversationMode): Promise<void> {
  setModeMenuOpen(false);
  if (mode === currentMode) return;
  currentMode = mode; currentTrack = tracks.get(mode) || defaultTrack(mode); records = []; hasMore = false;
  updateModeSurface(); renderMessages();
  try { await loadHistory(false); } catch (error) { showConversationError(error); }
}

async function createNewTrack(): Promise<void> {
  newTrackButton.disabled = true;
  try {
    const payload = await api<{ track: TrackDescriptor }>('/conversation/track', {
      method: 'POST', body: JSON.stringify({ ...currentTrack, mode: currentMode, action: 'new' }),
    });
    currentTrack = payload.track; tracks.set(currentMode, currentTrack); records = []; hasMore = false;
    await loadHistory(false); setComposerOpen(true);
  } catch (error) { showConversationError(error); }
  finally { newTrackButton.disabled = false; }
}

function updateModeSurface(): void {
  for (const button of modeButtons) {
    const active = button.dataset.mode === currentMode;
    button.setAttribute('aria-pressed', String(active)); button.classList.toggle('active', active);
    const state = button.querySelector<HTMLElement>('span:last-child');
    if (state) state.textContent = active ? '当前' : '';
  }
  const copy = {
    assistant: { label: '陪伴', welcome: '想聊聊，还是有什么事情想让我替你处理？', placeholder: '和 Aniya 说点什么…' },
    coding: { label: '项目', welcome: '我们专注在眼前的项目。', placeholder: '描述这次开发任务…' },
    qa: { label: '问答', welcome: '有什么想弄明白的？', placeholder: '问一个问题…' },
  }[currentMode];
  trackLabel.textContent = copy.label; welcomeCopy.textContent = copy.welcome; input.placeholder = copy.placeholder;
  modeMark.className = `mode-mark ${currentMode}`;
  newTrackButton.classList.toggle('hidden', currentMode === 'assistant');
  newTrackButton.title = currentMode === 'qa' ? '开始新主题' : '开始新工作会话';
  newTrackButton.setAttribute('aria-label', newTrackButton.title);
  trackUnavailable.textContent = currentTrack.unavailable_reason || '';
  trackUnavailable.classList.toggle('hidden', !currentTrack.unavailable_reason);
  updateComposerAvailability();
}

function setModeMenuOpen(open: boolean): void {
  modeMenu.classList.toggle('hidden', !open);
  modeToggle.setAttribute('aria-expanded', String(open));
}

function updateComposerAvailability(): void {
  const available = connected && currentTrack.can_send && !busy;
  sendButton.disabled = !available;
  input.disabled = !currentTrack.can_send;
  conversationOpen.disabled = !currentTrack.can_send;
}

function setWelcome(): void {
  const now = new Date(); const hour = now.getHours();
  greeting.textContent = hour < 5 ? '夜深了，我在。' : hour < 11 ? '早上好。' : hour < 14 ? '中午好。' : hour < 18 ? '下午好。' : '晚上好。';
  currentTime.textContent = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }).format(now);
  currentDate.textContent = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(now);
}

function setStatus(status: string, state: ConnectionState): void {
  statusEl.textContent = formatStatus(status); statusBadge.className = `status-badge ${state}`;
  dot.className = `dot ${state === 'connected' || state === 'busy' ? 'connected' : state === 'offline' || state === 'failed' ? 'offline' : state === 'recovering' ? 'recovering' : ''}`;
  if (state === 'busy') presenceStatus.textContent = '正在专注处理';
  else if (state === 'recovering') presenceStatus.textContent = '连接中断，正在恢复';
  else if (state === 'failed') presenceStatus.textContent = '这次没有处理完成';
  else if (state === 'offline') presenceStatus.textContent = '正在等待重新连接';
  else if (state === 'connected') presenceStatus.textContent = '已同步你的私人空间';
}

function formatStatus(status: string): string {
  if (status.startsWith('starting')) return '正在醒来';
  return ({ connecting: '正在醒来', reconnecting: '正在恢复', running: '正在处理', accepted: '正在处理', queued: '等待处理', waiting_permission: '等待确认', ready: '在你身边', busy: '正在处理', offline: '暂时离开', error: '连接异常', completed: '在你身边', failed: '处理失败', cancelled: '已取消', timed_out: '处理超时', unknown: '状态未知' } as Record<string, string>)[status] || status;
}

function setActivity(text: string, autoHideMs = 0): void {
  window.clearTimeout(activityTimer); activityText.textContent = text; activity.classList.remove('hidden');
  if (autoHideMs) activityTimer = window.setTimeout(() => activity.classList.add('hidden'), autoHideMs);
}

function clearActivity(delayMs = 0): void {
  window.clearTimeout(activityTimer);
  if (delayMs) activityTimer = window.setTimeout(() => activity.classList.add('hidden'), delayMs);
  else activity.classList.add('hidden');
}

function handleActivityLog(content: string): void {
  const value = content.toLowerCase();
  if (value.includes('llm request started')) setActivity('正在理解你的话…');
  else if (value.includes('tool started')) setActivity('正在替你处理…');
  else if (value.includes('completed')) setActivity('马上就好…', 1200);
  else if (value.includes('starting') || value.includes('webchannel')) setActivity('正在准备…', 1600);
}

function send(payload: unknown): boolean {
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(payload)); return true;
}

function requestChannels(): void {
  if (!send({ type: 'channels.list' })) return;
  window.clearTimeout(channelRefreshTimer); refreshChannelsButton.disabled = true; refreshChannelsButton.classList.add('loading');
  channelRefreshTimer = window.setTimeout(finishChannelRefresh, 5000);
}

function finishChannelRefresh(): void {
  window.clearTimeout(channelRefreshTimer); refreshChannelsButton.disabled = false; refreshChannelsButton.classList.remove('loading');
}

function renderChannels(channels: ChannelInfo[]): void {
  channelsEl.replaceChildren(); channelsCount.textContent = channels.length ? `${channels.length} 个连接已就绪` : '暂无可用连接';
  presenceStatus.textContent = channels.length ? '已同步你的私人空间' : '正在等待连接';
  for (const channel of channels) {
    const item = document.createElement('div'); item.className = `channel-item trust-${channel.trust_level}`;
    const icon = document.createElement('div'); icon.className = 'channel-icon'; icon.textContent = channel.kind.slice(0, 2).toUpperCase();
    const copy = document.createElement('div'); copy.className = 'channel-copy';
    const name = document.createElement('strong'); name.textContent = ({ web: '网页对话', weixin: '微信通知', cli: '本机', cron: '定时任务' } as Record<string, string>)[channel.channel_id] || channel.channel_id;
    const kind = document.createElement('span'); kind.textContent = channel.kind; copy.append(name, kind);
    const trust = document.createElement('span'); trust.className = 'trust-badge'; trust.textContent = ({ high: '安全', medium: '受限', low: '谨慎' } as Record<string, string>)[channel.trust_level] || channel.trust_level;
    item.append(icon, copy, trust); channelsEl.appendChild(item);
  }
  if (!channels.length) { const empty = document.createElement('div'); empty.className = 'channel-empty'; empty.textContent = '暂时没有可用通道'; channelsEl.appendChild(empty); }
}

function requestModels(): void { if (send({ type: 'models.list' })) modelsSummary.textContent = '正在读取可用模型'; }

function renderModels(payload: ModelProvidersPayload): void {
  const selected = payload.providers.find((provider) => provider.active) || payload.providers.find((provider) => provider.name === payload.active);
  activeModel.textContent = selected?.model || '未连接'; modelsSummary.textContent = selected ? formatProviderName(selected.name) : '暂无可用模型';
  modelsFeedback.classList.add('hidden'); modelsEl.replaceChildren();
  for (const provider of payload.providers) {
    const option = document.createElement('button'); option.className = `model-option${provider.active ? ' active' : ''}`; option.type = 'button';
    option.disabled = !provider.configured || provider.active || modelSelectionInFlight;
    const copy = document.createElement('span'); copy.className = 'model-option-copy';
    const name = document.createElement('strong'); name.textContent = formatProviderName(provider.name);
    const model = document.createElement('span'); model.textContent = provider.model || provider.base_url; copy.append(name, model);
    const state = document.createElement('span'); state.className = 'model-option-state'; state.textContent = provider.active ? '当前' : provider.configured ? '切换' : '未配置';
    option.append(copy, state); option.addEventListener('click', () => selectModel(provider.name)); modelsEl.appendChild(option);
  }
}

function selectModel(provider: string): void {
  if (modelSelectionInFlight || !send({ type: 'models.select', data: { provider } })) return;
  modelSelectionInFlight = true; modelsSummary.textContent = '正在切换模型';
  for (const option of modelsEl.querySelectorAll<HTMLButtonElement>('.model-option')) option.disabled = true;
}

function showModelFeedback(message: string): void { modelsFeedback.textContent = message || '模型切换失败'; modelsFeedback.classList.remove('hidden'); modelsSummary.textContent = '模型状态未更新'; }
function formatProviderName(name: string): string { return ({ anthropic: 'Anthropic', openai: 'OpenAI' } as Record<string, string>)[name] || name; }

function setChannelsOpen(open: boolean): void {
  channelsPopover.classList.toggle('hidden', !open); channelsToggle.setAttribute('aria-expanded', String(open));
  if (open) { requestChannels(); requestModels(); }
}

function setComposerOpen(open: boolean): void {
  if (open && !currentTrack.can_send) return;
  form.classList.toggle('hidden', !open); conversationOpen.classList.toggle('hidden', open);
  if (open) window.requestAnimationFrame(() => input.focus());
}

function setDrawerOpen(open: boolean): void {
  drawer.classList.toggle('hidden', !open); drawerScrim.classList.toggle('hidden', !open); document.body.classList.toggle('drawer-open', open);
  if (open) void loadDrawer();
}

async function loadDrawer(): Promise<void> {
  drawerContent.replaceChildren(skeletonBlock());
  newPlanButton.classList.toggle('hidden', drawerView !== 'plans');
  exportButton.classList.toggle('hidden', drawerView === 'binding');
  try {
    if (drawerView === 'plans') await renderPlans();
    else if (drawerView === 'notifications') await renderNotifications();
    else if (drawerView === 'daily') await renderDaily();
    else if (drawerView === 'memory') await renderLongTermMemory();
    else await renderWeixinBinding();
  } catch (error) {
    drawerContent.replaceChildren(emptyBlock(error instanceof Error ? error.message : String(error)));
  }
}

async function renderPlans(): Promise<void> {
  const payload = await api<{
    tasks: Array<Record<string, unknown>>;
    reminders: Array<Record<string, unknown>>;
    routines: Array<Record<string, unknown>>;
  }>('/plans');
  drawerSummary.textContent = '你的任务、提醒与 Routine'; drawerContent.replaceChildren();
  appendPlanSection('任务', payload.tasks || [], (item) => String(item.title || ''), (item) => [planStatus(String(item.status || '')), item.due_at ? formatDateTime(String(item.due_at)) : ''].filter(Boolean).join(' · '), (item, actions) => {
    if (!['done', 'cancelled'].includes(String(item.status))) {
      actions.append(actionButton('完成', () => void planAction('task', 'complete', String(item.id))));
      actions.append(actionButton('取消', () => void planAction('task', 'cancel', String(item.id)), true));
    } else actions.append(actionButton('重新打开', () => void planAction('task', 'reopen', String(item.id))));
  });
  appendPlanSection('提醒', payload.reminders || [], (item) => String(item.content || ''), (item) => `${planStatus(String(item.status || ''))} · ${formatDateTime(String(item.snoozed_until || item.scheduled_at || ''))}`, (item, actions) => {
    if (!['completed', 'cancelled'].includes(String(item.status))) {
      actions.append(actionButton('完成', () => void planAction('reminder', 'complete', String(item.id))));
      actions.append(actionButton('取消', () => void planAction('reminder', 'cancel', String(item.id)), true));
    }
  });
  appendPlanSection('Routine', payload.routines || [], (item) => String(item.name || ''), (item) => `${item.enabled ? '运行中' : '已暂停'} · ${String(item.cron || '')}`, (item, actions) => {
    actions.append(actionButton(item.enabled ? '暂停' : '启用', () => void planAction('routine', 'toggle', String(item.id))));
  });
  if (!payload.tasks?.length && !payload.reminders?.length && !payload.routines?.length) drawerContent.appendChild(emptyBlock('还没有计划'));
}

function appendPlanSection(
  title: string,
  items: Array<Record<string, unknown>>,
  titleFor: (item: Record<string, unknown>) => string,
  metaFor: (item: Record<string, unknown>) => string,
  actionsFor: (item: Record<string, unknown>, actions: HTMLElement) => void,
): void {
  if (!items.length) return;
  const heading = document.createElement('h3'); heading.className = 'archive-section-title'; heading.textContent = title; drawerContent.appendChild(heading);
  for (const data of items) {
    const item = document.createElement('article'); item.className = 'archive-item plan-item';
    const head = document.createElement('div'); head.className = 'archive-item-head';
    const strong = document.createElement('strong'); strong.textContent = titleFor(data);
    const state = document.createElement('span'); state.className = 'archive-state'; state.textContent = metaFor(data); head.append(strong, state);
    const actions = document.createElement('div'); actions.className = 'archive-actions'; actionsFor(data, actions);
    item.append(head, actions); drawerContent.appendChild(item);
  }
}

async function planAction(entity: string, action: string, id = '', fields: Record<string, unknown> = {}): Promise<void> {
  await api('/plans/action', { method: 'POST', body: JSON.stringify({ entity, action, id, ...fields }) });
  await loadDrawer();
}

function showPlanForm(): void {
  drawerContent.querySelector('.plan-create')?.remove();
  const form = document.createElement('form'); form.className = 'plan-create';
  const kind = document.createElement('select');
  kind.innerHTML = '<option value="task">任务</option><option value="reminder">提醒</option><option value="routine">Routine</option>';
  const fields = document.createElement('div'); fields.className = 'plan-create-fields';
  const submit = document.createElement('button'); submit.type = 'submit'; submit.textContent = '创建';
  const renderFields = () => {
    fields.replaceChildren();
    if (kind.value === 'task') {
      fields.append(planInput('title', '任务名称', true), planInput('due_at', '截止时间', false, 'datetime-local'));
    } else if (kind.value === 'reminder') {
      fields.append(planInput('content', '提醒内容', true), planInput('scheduled_at', '提醒时间', true, 'datetime-local'));
    } else {
      fields.append(planInput('name', 'Routine 名称', true));
      const routineType = document.createElement('select'); routineType.name = 'routine_type'; routineType.innerHTML = '<option value="morning_plan">晨间计划</option><option value="evening_review">晚间复盘</option><option value="weekly_review">每周复盘</option>';
      fields.append(routineType, planInput('cron', 'Cron，例如 0 8 * * *', true));
    }
  };
  kind.addEventListener('change', renderFields); renderFields(); form.append(kind, fields, submit);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const values: Record<string, unknown> = {};
    for (const control of [...form.querySelectorAll<HTMLInputElement | HTMLSelectElement>('input, select')]) {
      if (!control.name || control === kind || !control.value) continue;
      values[control.name] = control.type === 'datetime-local' ? new Date(control.value).toISOString() : control.value;
    }
    void planAction(kind.value, 'create', '', values).catch((error) => {
      const failure = document.createElement('p'); failure.className = 'archive-error'; failure.textContent = error instanceof Error ? error.message : String(error); form.appendChild(failure);
    });
  });
  drawerContent.prepend(form); form.querySelector<HTMLInputElement>('input')?.focus();
}

function planInput(name: string, placeholder: string, required: boolean, type = 'text'): HTMLInputElement {
  const field = document.createElement('input'); field.name = name; field.type = type; field.placeholder = placeholder; field.required = required; return field;
}

async function renderWeixinBinding(): Promise<void> {
  const payload = await api<{ binding: Record<string, unknown> | null }>('/weixin/binding');
  drawerSummary.textContent = '微信只用于接收 Aniya 的通知'; drawerContent.replaceChildren();
  const item = document.createElement('article'); item.className = 'binding-view';
  if (payload.binding) {
    const state = document.createElement('span'); state.className = 'binding-state connected'; state.textContent = '已验证';
    const title = document.createElement('h3'); title.textContent = '微信通知已绑定';
    const recipient = document.createElement('p'); recipient.textContent = `Recipient ${String(payload.binding.recipient_id || '')}`;
    const verified = document.createElement('p'); verified.textContent = `验证于 ${formatDateTime(String(payload.binding.verified_at || ''))}`;
    const invalidate = document.createElement('button'); invalidate.type = 'button'; invalidate.className = 'binding-command danger'; invalidate.textContent = '解除绑定';
    invalidate.addEventListener('click', () => void invalidateBinding(invalidate));
    item.append(state, title, recipient, verified, invalidate);
  } else {
    const state = document.createElement('span'); state.className = 'binding-state'; state.textContent = '未绑定';
    const title = document.createElement('h3'); title.textContent = '连接你的微信通知';
    const copy = document.createElement('p'); copy.textContent = '生成一次性绑定码后，在微信里发送“绑定 + 空格 + 绑定码”。';
    const create = document.createElement('button'); create.type = 'button'; create.className = 'binding-command'; create.textContent = '生成绑定码';
    create.addEventListener('click', () => void issueBindingCode(item));
    item.append(state, title, copy, create);
  }
  drawerContent.appendChild(item);
}

async function issueBindingCode(container: HTMLElement): Promise<void> {
  const payload = await api<{ code: string; expires_in: number }>('/weixin/binding/code', { method: 'POST', body: '{}' });
  container.querySelector('.binding-code')?.remove();
  const code = document.createElement('div'); code.className = 'binding-code';
  const value = document.createElement('strong'); value.textContent = payload.code;
  const hint = document.createElement('span'); hint.textContent = `${Math.round(payload.expires_in / 60)} 分钟内有效`;
  code.append(value, hint); container.appendChild(code);
}

async function invalidateBinding(button: HTMLButtonElement): Promise<void> {
  if (button.dataset.confirm !== 'true') {
    button.dataset.confirm = 'true'; button.textContent = '再次点击解除';
    window.setTimeout(() => { button.dataset.confirm = ''; button.textContent = '解除绑定'; }, 3000); return;
  }
  await api('/weixin/binding/invalidate', { method: 'POST', body: '{}' }); await loadDrawer();
}

async function renderNotifications(): Promise<void> {
  const payload = await api<{ notifications: Array<Record<string, unknown>> }>('/notifications');
  drawerSummary.textContent = '最近的投递状态'; drawerContent.replaceChildren();
  for (const notification of payload.notifications || []) {
    const state = String(notification.state || 'pending');
    const item = document.createElement('article'); item.className = `archive-item notification-${state}`;
    const head = document.createElement('div'); head.className = 'archive-item-head';
    const title = document.createElement('strong'); title.textContent = contentText((notification.payload as Record<string, unknown> | undefined)?.content || notification.reminder_id || '通知');
    const badge = document.createElement('span'); badge.className = 'archive-state'; badge.textContent = notificationState(state); head.append(title, badge);
    const meta = document.createElement('p'); meta.textContent = [formatDateTime(String(notification.created_at || '')), String(notification.channel_id || '')].filter(Boolean).join(' · ');
    item.append(head, meta);
    if (notification.error) { const error = document.createElement('p'); error.className = 'archive-error'; error.textContent = String(notification.error); item.appendChild(error); }
    if (notification.available_at && ['retry_scheduled', 'delivery_unknown'].includes(state)) { const retry = document.createElement('p'); retry.textContent = `下次处理 ${formatDateTime(String(notification.available_at))}`; item.appendChild(retry); }
    drawerContent.appendChild(item);
  }
  if (!payload.notifications?.length) drawerContent.appendChild(emptyBlock('还没有通知记录'));
}

async function renderDaily(): Promise<void> {
  const payload = await api<{ daily: Record<string, unknown> | null; days: Array<Record<string, unknown>> }>('/memory/daily');
  drawerSummary.textContent = '生活轨道的每日回望'; drawerContent.replaceChildren();
  for (const day of payload.days || []) {
    const item = document.createElement('article'); item.className = 'archive-item daily-item';
    const head = document.createElement('div'); head.className = 'archive-item-head';
    const title = document.createElement('strong'); title.textContent = formatDay(String(day.local_date || ''));
    const state = document.createElement('span'); state.className = 'archive-state'; state.textContent = dailyState(String(day.daily_memory_status || 'open')); head.append(title, state);
    const summary = document.createElement('p'); summary.className = 'daily-summary'; summary.textContent = String(day.summary || day.daily_memory_error || '等待当天结束后生成');
    item.append(head, summary);
    const loops = day.open_loops as unknown[] | undefined;
    if (loops?.length) { const label = document.createElement('span'); label.className = 'archive-caption'; label.textContent = `未完事项 ${loops.length}`; item.appendChild(label); }
    drawerContent.appendChild(item);
  }
  if (!payload.days?.length) drawerContent.appendChild(emptyBlock('还没有 Daily Memory'));
}

async function renderLongTermMemory(): Promise<void> {
  const payload = await api<{ memories: Array<Record<string, unknown>> }>('/memory/long-term');
  drawerSummary.textContent = '可确认、修正与遗忘的长期记忆'; drawerContent.replaceChildren();
  for (const memory of payload.memories || []) {
    const item = document.createElement('article'); item.className = 'archive-item memory-item';
    const head = document.createElement('div'); head.className = 'archive-item-head';
    const title = document.createElement('strong'); title.textContent = String(memory.content || '[已遗忘]');
    const state = document.createElement('span'); state.className = 'archive-state'; state.textContent = memoryState(String(memory.status || '')); head.append(title, state);
    const meta = document.createElement('p');
    const sources = Array.isArray(memory.source_message_ids) ? memory.source_message_ids as string[] : [];
    meta.textContent = `${String(memory.type || 'memory')}${sources.length ? ` · ${sources.length} 条事实来源` : ' · 无事实来源'}`;
    const actions = document.createElement('div'); actions.className = 'archive-actions';
    if (memory.status === 'pending_confirmation') actions.append(actionButton('确认', () => memoryAction(String(memory.id), 'confirm')));
    if (!['deleted', 'archived'].includes(String(memory.status))) {
      actions.append(actionButton('修正', () => showCorrection(item, memory)));
      actions.append(actionButton('归档', () => memoryAction(String(memory.id), 'archive')));
      actions.append(actionButton('遗忘', () => memoryAction(String(memory.id), 'forget'), true));
    }
    item.append(head, meta, actions); drawerContent.appendChild(item);
  }
  if (!payload.memories?.length) drawerContent.appendChild(emptyBlock('还没有长期记忆'));
}

function showCorrection(item: HTMLElement, memory: Record<string, unknown>): void {
  item.querySelector('.memory-correction')?.remove();
  const form = document.createElement('form'); form.className = 'memory-correction';
  const field = document.createElement('textarea'); field.value = String(memory.content || ''); field.rows = 3;
  const submit = document.createElement('button'); submit.type = 'submit'; submit.textContent = '保存修正'; form.append(field, submit);
  form.addEventListener('submit', (event) => { event.preventDefault(); void memoryAction(String(memory.id), 'correct', field.value); });
  item.appendChild(form); field.focus();
}

async function memoryAction(memoryId: string, action: string, content = ''): Promise<void> {
  await api('/memory/long-term/action', { method: 'POST', body: JSON.stringify({ memory_id: memoryId, action, content }) });
  await loadDrawer();
}

function actionButton(label: string, action: () => void, danger = false): HTMLButtonElement {
  const button = document.createElement('button'); button.type = 'button'; button.textContent = label; button.classList.toggle('danger', danger); button.addEventListener('click', action); return button;
}

function skeletonBlock(): HTMLElement { const item = document.createElement('div'); item.className = 'drawer-skeleton'; item.innerHTML = '<i></i><i></i><i></i>'; return item; }
function emptyBlock(message: string): HTMLElement { const item = document.createElement('div'); item.className = 'archive-empty'; item.textContent = message; return item; }
function notificationState(value: string): string { return ({ pending: '等待', claimed: '已认领', sending: '发送中', delivered: '已送达', retry_scheduled: '将重试', delivery_unknown: '待确认', failed: '失败', cancelled: '已取消' } as Record<string, string>)[value] || value; }
function dailyState(value: string): string { return ({ generated: '已生成', failed: '生成失败', needs_rebuild: '待重建', open: '进行中' } as Record<string, string>)[value] || value; }
function memoryState(value: string): string { return ({ active: '有效', pending_confirmation: '待确认', archived: '已归档', superseded: '已修正', deleted: '已遗忘' } as Record<string, string>)[value] || value; }
function planStatus(value: string): string { return ({ inbox: '收件箱', planned: '已计划', waiting: '等待中', in_progress: '进行中', done: '已完成', cancelled: '已取消', deferred: '已推迟', scheduled: '已安排', delivered: '已送达', snoozed: '已稍后', completed: '已完成', missed: '已错过', failed: '失败' } as Record<string, string>)[value] || value; }
function formatTime(value: string): string { const date = new Date(value); return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }).format(date); }
function formatDateTime(value: string): string { const date = new Date(value); return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(date); }
function formatDay(value: string): string { const date = new Date(`${value}T00:00:00`); return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' }).format(date); }
function showConversationError(error: unknown): void { addTransient('error', error instanceof Error ? error.message : String(error)); }

form.addEventListener('submit', (event) => {
  event.preventDefault(); const content = input.value.trim();
  if (!content || sendButton.disabled || !send({ type: 'agent.send', data: { content, track: currentTrack } })) return;
  storeRun(null);
  records.push({ message_id: `temp_user_${++transientCounter}`, role: 'user', content, created_at: new Date().toISOString(), day_date: '', track_sequence: Number.MAX_SAFE_INTEGER - transientCounter });
  renderMessages(); messagesEl.scrollTop = messagesEl.scrollHeight; input.value = ''; resizeInput(); setActivity('正在理解你的话…'); busy = true; setStatus('busy', 'busy'); updateComposerAvailability();
});
input.addEventListener('input', resizeInput);
input.addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); } });
for (const button of modeButtons) button.addEventListener('click', () => void switchMode(button.dataset.mode as ConversationMode));
modeToggle.addEventListener('click', (event) => { event.stopPropagation(); setModeMenuOpen(modeMenu.classList.contains('hidden')); });
loadOlderButton.addEventListener('click', () => void loadHistory(true).catch(showConversationError));
newTrackButton.addEventListener('click', () => void createNewTrack());
conversationOpen.addEventListener('click', () => setComposerOpen(true));
refreshChannelsButton.addEventListener('click', requestChannels);
channelsToggle.addEventListener('click', (event) => { event.stopPropagation(); setChannelsOpen(channelsPopover.classList.contains('hidden')); });
openMemoryButton.addEventListener('click', () => { setChannelsOpen(false); setDrawerOpen(true); });
newPlanButton.addEventListener('click', showPlanForm);
closeMemoryButton.addEventListener('click', () => setDrawerOpen(false)); drawerScrim.addEventListener('click', () => setDrawerOpen(false));
for (const tab of drawerTabs) tab.addEventListener('click', () => { drawerView = tab.dataset.view as DrawerView; for (const item of drawerTabs) item.setAttribute('aria-selected', String(item === tab)); void loadDrawer(); });
exportButton.addEventListener('click', async () => {
  const payload = await api<Record<string, unknown>>('/memory/export'); const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = `aniya-memory-${new Date().toISOString().slice(0, 10)}.json`; link.click(); URL.revokeObjectURL(link.href);
});
logoutButton.addEventListener('click', async () => { await fetch('/logout', { method: 'POST' }); location.assign('/'); });
allowButton.addEventListener('click', () => answerPermission(true)); denyButton.addEventListener('click', () => answerPermission(false));

document.addEventListener('click', (event) => {
  const target = event.target as Node;
  if (!channelsPopover.classList.contains('hidden') && !document.querySelector('.nav-actions')?.contains(target)) setChannelsOpen(false);
  if (!modeMenu.classList.contains('hidden') && !document.querySelector('.mode-control')?.contains(target)) setModeMenuOpen(false);
  if (!form.classList.contains('hidden') && !form.contains(target) && !document.querySelector('.dock-controls')?.contains(target)) setComposerOpen(false);
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (!permission.classList.contains('hidden')) answerPermission(false);
  else if (!drawer.classList.contains('hidden')) setDrawerOpen(false);
  else if (!channelsPopover.classList.contains('hidden')) setChannelsOpen(false);
  else if (!modeMenu.classList.contains('hidden')) setModeMenuOpen(false);
  else if (!form.classList.contains('hidden')) setComposerOpen(false);
});

function answerPermission(allow: boolean): void { if (!pendingPermissionId) return; send({ type: 'agent.permission', data: { requestId: pendingPermissionId, allow } }); pendingPermissionId = ''; permission.classList.add('hidden'); input.focus(); }
function resizeInput(): void { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 160)}px`; }

setWelcome(); window.setInterval(setWelcome, 30_000); updateModeSurface(); connect(); void initializeConversation();

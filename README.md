# AniyaAgent

AniyaAgent is a personal local Agent project for coding, task orchestration, and remote operation from a mobile browser.

It runs the Agent runtime on your own computer, supports local tool execution, persistent tasks, memory, background jobs, multi-agent collaboration, Git worktree isolation, and optional LangGraph workflow execution. A lightweight TypeScript client lets you operate the local Agent from a phone through LAN access or a Cloudflare Worker relay.

## Features

- ReAct-style Agent loop with tool calling, tool execution, and result feedback.
- Unified tool registry for file operations, shell commands, tasks, cron jobs, background jobs, team communication, and worktrees.
- Permission and hook system for risky local actions.
- Context engineering with memory, skills, prompt assembly, compaction, and error recovery.
- Persistent task board, autonomous teammate agents, protocol messages, and plan approval.
- LLM request gateway with queueing and task-based model routing.
- Mobile browser client for local or remote control.

## Architecture

```text
Phone Browser / CLI
  -> AniyaAgent Client
  -> Python Bridge
  -> Agent Runtime
  -> LLM API + Local Tools
```

Remote mode:

```text
Phone Browser -> Cloudflare Worker Relay <- Local AniyaAgent Client -> Python Agent
```

## Quick Start

Install Python dependencies:

```powershell
cd C:\Users\24021\Desktop\java\learnclaudecode\AniyaAgent\Main
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `Main/.env`:

```env
ANTHROPIC_API_KEY=your_api_key_here
MODEL_ID=your_model_id_here
ANTHROPIC_BASE_URL=https://api.anthropic.com
```

Run CLI:

```powershell
python MainLoop.py
```

Run LangGraph version:

```powershell
python MainLoopLangGraph.py
```

## Mobile Client

Start local web client:

```powershell
cd C:\Users\24021\Desktop\java\learnclaudecode\AniyaAgent\client
npm install
npm run build
npm start
```

Open the printed LAN URL from your phone, for example:

```text
http://192.168.x.x:9527
```

For remote access, deploy the Worker under `client/worker`, then start the local client with:

```powershell
$env:ANIYAAGENT_WORKER_URL="https://your-worker.workers.dev"
$env:ANIYAAGENT_SESSION_ID="replace-with-a-long-random-session"
npm start
```

## Configuration

Optional LLM gateway settings:

```env
LLM_MAX_CONCURRENT=1
TEAM_MODEL_ID=your_team_model
MEMORY_MODEL_ID=your_memory_model
COMPACT_MODEL_ID=your_compact_model
REPAIR_MODEL_ID=your_repair_model
MEMORY_MATCH_MODE=keyword
```

## Security

Keep real credentials in `Main/.env`; do not commit them.

The mobile client is intended for personal use. Use a long random session id for remote relay mode and do not expose it as a public multi-user service without authentication, authorization, audit logs, and stronger sandboxing.

## License

No license has been specified yet.

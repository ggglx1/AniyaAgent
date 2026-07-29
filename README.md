# AniyaAgent

[English](README.md) | [简体中文](README.zh-CN.md)

A local-first personal AI agent for turning conversation into useful work without handing your workspace to a hosted assistant.

AniyaAgent runs on your own computer and connects the parts of everyday work that usually live in separate places: conversations, tasks, files, schedules, reminders, coding work, and local tools. It can reason over a request, propose or execute an action, retain useful context, and keep long-running work moving while you stay in control.

The control plane stays local by default: credentials, attachments, memory, queued work, and tool execution remain on your device. Remote access is optional and protected rather than assumed.

## Core Capabilities

- **Conversation to action**: turns natural-language requests into explicit actions across local files, commands, tasks, and connected tools. Actions can be reviewed, queued, resumed, or cancelled instead of disappearing into a chat transcript.
- **Personal operating system**: keeps tasks, routines, schedules, reminders, background jobs, and follow-ups in one durable runtime.
- **Memory with boundaries**: separates factual, daily, and long-term memory so the agent keeps useful context without treating every message as permanent personal data.
- **Coding companion**: routes coding work through dedicated executors, budgets, artifacts, and project-aware context alongside normal assistant work.
- **Local and mobile access**: use the Web Client from a desktop or phone on your LAN, or add a Cloudflare Worker relay when remote access is genuinely needed.
- **Notifications, not another inbox**: sends reminders through WeChat without turning WeChat into the primary conversation channel.

## How It Differs

AniyaAgent is not a thin chat wrapper around an LLM. It is a local runtime that gives requests an execution path: context is assembled deliberately, work is routed to the right executor, actions and events are tracked, and long-running tasks can continue outside a single response. The result is an assistant that is useful for repeated personal workflows, not just one-off answers.

## Architecture

![AniyaAgent Architecture](AnyaArchitecture.png)

A request travels through five clear layers:

1. **Entry**: the Web Client serves desktop and mobile browsers; a Worker can relay remote access.
2. **Access**: an Owner Token protects private access, so a local assistant does not become a public service by accident.
3. **Runtime**: the Agent Runtime interprets requests, calls the LLM, orchestrates tools, and returns results to the conversation.
4. **State**: three memory layers retain facts, daily context, and confirmed long-term information; the scheduler handles tasks, routines, and reminders.
5. **Notifications**: when something needs attention, the runtime sends a WeChat reminder without taking over the conversation.

## Quick Start

Requirements: Python 3.10+ and a model service compatible with the Anthropic or OpenAI API.

```powershell
cd C:\Users\24021\Desktop\java\learnclaudecode\AniyaAgent
pip install -r main/requirements.txt
Copy-Item main/.env.example main/.env
```

Edit `main/.env`. At minimum, configure `ANIYAAGENT_OWNER_TOKEN` plus the API key and model ID for your selected model provider.

Start the Web Client. It launches the local Agent service automatically:

```powershell
cd main/client
npm install
npm run build
npm start
```

The terminal prints desktop and LAN URLs. Open the LAN URL on your phone, then enter the Owner Token.

For scheduled reminders, routines, and WeChat notifications, start the scheduler in another terminal:

```powershell
python -m main.channel.run_scheduler
```

You can also run the Agent directly in the terminal:

```powershell
python -m main.agent.main_loop
```

## Mobile Access

For local use, open the LAN URL printed by the Web Client. For remote access, deploy the Cloudflare Worker in `main/client/worker` and configure independent, high-entropy values for `ANIYAAGENT_WORKER_URL` and `ANIYAAGENT_SESSION_ID`.

## Security

`main/.env` contains model credentials and access tokens; never commit it. AniyaAgent is intended for private personal use. A multi-user deployment needs its own authentication, authorization, audit trail, and stronger execution isolation.

## License

No license has been specified yet.

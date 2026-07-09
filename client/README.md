# AniyaAgent Client

TypeScript + Cloudflare Worker client for connecting a phone browser to the Python agent under `AniyaAgent/Main`.

The local client actively connects to Cloudflare Worker. Your phone only visits the Worker URL, so the computer does not need to expose a public port.

```text
Phone Browser --https/wss--> Cloudflare Worker (Durable Object relay)
                            ^
                            |
Local client (TypeScript Node) --stdio/jsonl--> Python bridge --import--> MainLoop.agent_loop
```

## Deploy Cloudflare Worker

```powershell
cd C:\Users\24021\Desktop\java\learnclaudecode\AniyaAgent\client
npm install
npm run build
cd worker
npm install
Copy-Item wrangler.example.jsonc wrangler.jsonc
```

Edit `client/worker/wrangler.jsonc`:

- `name`: Worker name, for example `aniyaagent-client`
- Add `account_id` if your Wrangler setup requires it
- Add `routes` if you want a custom domain

Deploy:

```powershell
npm run deploy
```

The Worker URL will look like:

```text
https://aniyaagent-client.<your-subdomain>.workers.dev
```

## Start Local Client

```powershell
cd C:\Users\24021\Desktop\java\learnclaudecode\AniyaAgent\client
$env:ANIYAAGENT_WORKER_URL="https://aniyaagent-client.<your-subdomain>.workers.dev"
$env:ANIYAAGENT_SESSION_ID="replace-with-a-long-random-session"
npm start
```

The console prints a relay URL:

```text
AniyaAgent Cloudflare relay is ready:
  https://aniyaagent-client.<your-subdomain>.workers.dev/?session=your-session
```

Open that URL on your phone to connect to the local AniyaAgent instance.

## LAN Mode

If `ANIYAAGENT_WORKER_URL` is not set, the client runs in LAN mode:

```powershell
cd C:\Users\24021\Desktop\java\learnclaudecode\AniyaAgent\client
npm start
```

Open the printed `http://<your-lan-ip>:9527` URL from your phone.

## Python Environment

The default Python path is:

```text
C:\Users\24021\anaconda3\envs\Claude\python.exe
```

Override it when needed:

```powershell
$env:ANIYAAGENT_PYTHON="C:\path\to\python.exe"
npm start
```

Optional port and session overrides:

```powershell
$env:ANIYAAGENT_CLIENT_PORT="9530"
$env:ANIYAAGENT_SESSION_ID="my-fixed-session"
npm start
```

## Current Capabilities

- Send prompts from a phone web page to the local AniyaAgent runtime.
- Keep conversation `history` inside the same Python bridge process.
- Display agent logs, tool-call traces, and final assistant text.
- Surface permission confirmations for risky tools such as `write_file`, `edit_file`, and shell commands.
- Use Cloudflare Worker only as a WebSocket relay and static asset host; agent data stays local.

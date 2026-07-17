# Security

## Secrets

Do not commit real API keys, access tokens, cookies, private endpoints, or local credential files.

Use `main/.env` for local credentials:

```env
ANTHROPIC_API_KEY=your_api_key_here
MODEL_ID=your_model_id_here
ANTHROPIC_BASE_URL=https://api.anthropic.com
ANIYAAGENT_OWNER_TOKEN=use_a_long_random_value_at_least_32_characters
```

`main/.env` is ignored by Git. `main/.env.example` is safe to commit because it contains placeholders only.

## Before Making The Repository Public

Run a local scan:

```powershell
rg -n --hidden --glob '!/.git/**' --glob '!**/__pycache__/**' --glob '!**/.venv/**' "sk-[A-Za-z0-9_-]{20,}|API_KEY|SECRET|TOKEN" .
git grep -n "sk-[A-Za-z0-9_-]\{20,\}" HEAD -- .
```

If any real key appears in current files or Git history, revoke or rotate it before publishing.

## Private Web Access

The Web service refuses to start without `ANIYAAGENT_OWNER_TOKEN`. The token creates an HttpOnly owner session before static pages or WebSocket connections are served. Keep the service behind a private network or Cloudflare Access for remote deployment.

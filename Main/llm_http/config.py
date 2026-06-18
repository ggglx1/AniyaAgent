import os
from dataclasses import dataclass

try:
    from dotenv import find_dotenv, load_dotenv
except ImportError:  # Keep the package usable when env vars are already set.
    find_dotenv = None
    load_dotenv = None


if load_dotenv:
    env_path = find_dotenv(usecwd=True) if find_dotenv else ""
    load_dotenv(env_path or None, override=True)


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str


def get_settings() -> Settings:
    if os.getenv("ANTHROPIC_BASE_URL"):
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

    return Settings(
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/"),
        model=os.getenv("MODEL_ID", "claude-3-5-sonnet-latest"),
    )


def ensure_configured() -> Settings:
    settings = get_settings()
    if not settings.api_key:
        raise SystemExit("Missing ANTHROPIC_API_KEY. Put it in .env or set it in the shell.")
    return settings

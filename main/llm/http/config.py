import json
import os
from dataclasses import dataclass
from pathlib import Path

from main.llm.providers.base import ProviderConfig

try:
    from dotenv import find_dotenv, load_dotenv
except ImportError:
    find_dotenv = None
    load_dotenv = None


if load_dotenv:
    env_path = find_dotenv(usecwd=True) if find_dotenv else ""
    load_dotenv(env_path or None, override=True)
    project_env = Path(__file__).resolve().parents[2] / ".env"
    if project_env.exists():
        load_dotenv(project_env, override=True)


@dataclass(frozen=True)
class Settings:
    provider: str
    api_key: str
    base_url: str
    model: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SELECTION_PATH = Path(
    os.getenv("LLM_PROVIDER_CONFIG_PATH", str(PROJECT_ROOT / ".personal" / "llm_provider.json"))
)


def provider_configs() -> dict[str, ProviderConfig]:
    return {
        "anthropic": ProviderConfig(
            name="anthropic",
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/"),
            model=os.getenv("ANTHROPIC_MODEL_ID") or "claude-3-5-sonnet-latest",
        ),
        "openai": ProviderConfig(
            name="openai",
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            model=os.getenv("OPENAI_MODEL_ID") or "gpt-4.1",
        ),
    }


def selected_provider_name() -> str:
    try:
        data = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
        persisted = normalize_provider_name(data.get("provider"))
        if persisted:
            return persisted
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return normalize_provider_name(os.getenv("LLM_PROVIDER")) or "anthropic"


def normalize_provider_name(value) -> str:
    name = str(value or "").strip().lower()
    aliases = {
        "claude": "anthropic",
        "openai-compatible": "openai",
        "openai_compatible": "openai",
    }
    return aliases.get(name, name)


def persist_selected_provider(name: str) -> None:
    SELECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = SELECTION_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps({"provider": name}, indent=2), encoding="utf-8")
    temp.replace(SELECTION_PATH)


def get_settings(provider: str | None = None) -> Settings:
    name = normalize_provider_name(provider) if provider else selected_provider_name()
    config = provider_configs().get(name)
    if config is None:
        raise ValueError(f"Unknown LLM provider: {name}")
    return Settings(
        provider=config.name,
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
    )


def ensure_configured(provider: str | None = None) -> Settings:
    settings = get_settings(provider)
    if not settings.api_key:
        variable = "ANTHROPIC_API_KEY" if settings.provider == "anthropic" else "OPENAI_API_KEY"
        raise SystemExit(f"Missing {variable}. Put it in main/.env or set it in the shell.")
    return settings

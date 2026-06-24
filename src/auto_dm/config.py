"""Load application config from .env (secrets) + config.json (preferences)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from auto_dm.llm.base import LLMConfig

# Provider name -> env var holding its API key
_PROVIDER_ENV_MAP: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "glm": "GLM_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}


@dataclass
class AppConfig:
    """Top-level application config loaded from disk."""

    llm: LLMConfig
    language: str = "pt-BR"
    narrative_detail: str = "medium"
    rules_strictness: str = "high"
    save_dir: str = "saves"
    auto_save_every_n_turns: int = 5


def load_app_config(
    config_path: str | Path = "config.json",
    env_path: str | Path = ".env",
) -> AppConfig:
    """Load .env and config.json, return a fully-resolved AppConfig.

    Raises:
        FileNotFoundError: if config.json does not exist.
        RuntimeError: if the API key for the chosen provider is not in env.
        ValueError: if the provider name is unknown.
    """
    load_dotenv(env_path, override=False)

    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.json not found at {config_path}. "
            f"Copy config.example.json to {config_path} and edit it."
        )

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    provider_name = raw["provider"]
    api_key = _resolve_api_key(provider_name)

    llm = LLMConfig(
        name=provider_name,
        api_key=api_key,
        model=raw["model"],
        base_url=raw.get("base_url"),
        temperature=raw.get("temperature", 0.8),
        max_tokens=raw.get("max_tokens", 2048),
    )

    return AppConfig(
        llm=llm,
        language=raw.get("language", "pt-BR"),
        narrative_detail=raw.get("narrative_detail", "medium"),
        rules_strictness=raw.get("rules_strictness", "high"),
        save_dir=raw.get("save_dir", "saves"),
        auto_save_every_n_turns=raw.get("auto_save_every_n_turns", 5),
    )


def _resolve_api_key(provider_name: str) -> str:
    name = (provider_name or "").lower()
    env_var = _PROVIDER_ENV_MAP.get(name)
    if not env_var:
        raise ValueError(
            f"Unknown provider: {provider_name!r}. "
            f"Known: {sorted(_PROVIDER_ENV_MAP)}"
        )
    key = os.getenv(env_var)
    if not key:
        raise RuntimeError(
            f"API key for {provider_name!r} not found. "
            f"Set {env_var} in your .env file."
        )
    return key

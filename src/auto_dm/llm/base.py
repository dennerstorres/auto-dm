"""Base LLM provider interface and shared types."""
from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Message:
    """A single message in a conversation."""

    role: str  # "system" | "user" | "assistant"
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMConfig:
    """Configuration for an LLM provider."""

    name: str  # provider key: "minimax", "claude", etc
    api_key: str
    model: str
    base_url: str | None = None
    temperature: float = 0.8
    max_tokens: int = 2048
    # Provider-specific thinking mode. For MiniMax, "adaptive" enables extended
    # thinking. Other providers may ignore this. None means "use provider default".
    thinking: str | None = None
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, *, prefix: str = "AUTO_DM_") -> "LLMConfig":
        """Build an LLMConfig from environment variables.

        Recognized keys (all uppercased + prefixed):

        - ``{prefix}PROVIDER``      — provider key (e.g. ``minimax``)
        - ``{prefix}API_KEY``       — secret
        - ``{prefix}MODEL``         — model name
        - ``{prefix}BASE_URL``      — optional custom endpoint
        - ``{prefix}TEMPERATURE``   — default 0.8
        - ``{prefix}MAX_TOKENS``    — default 2048
        - ``{prefix}THINKING``      — optional thinking mode

        The default ``AUTO_DM_`` prefix matches what the backend
        reads (see ``web/server.py::_default_provider_factory``).
        """
        name = os.environ.get(f"{prefix}PROVIDER", "").strip().lower()
        api_key = os.environ.get(f"{prefix}API_KEY", "").strip()
        model = os.environ.get(f"{prefix}MODEL", "").strip()
        if not (name and api_key and model):
            missing = [k for k in ("PROVIDER", "API_KEY", "MODEL") if not os.environ.get(f"{prefix}{k}", "").strip()]
            raise RuntimeError(
                f"Missing required env vars: {', '.join(f'{prefix}{k}' for k in missing)}"
            )
        base_url = os.environ.get(f"{prefix}BASE_URL") or None
        try:
            temperature = float(os.environ.get(f"{prefix}TEMPERATURE", "0.8"))
        except ValueError:
            temperature = 0.8
        try:
            max_tokens = int(os.environ.get(f"{prefix}MAX_TOKENS", "2048"))
        except ValueError:
            max_tokens = 2048
        thinking = os.environ.get(f"{prefix}THINKING") or None
        return cls(
            name=name,
            api_key=api_key,
            model=model,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
        )


class LLMProvider(Protocol):
    """Protocol that all LLM providers implement.

    All providers must support:
    - `chat()` for synchronous single-shot completion
    - `stream()` for token-by-token streaming
    - `count_tokens()` for budget estimation
    """

    name: str
    config: LLMConfig

    def chat(self, messages: list[Message]) -> str: ...

    def stream(self, messages: list[Message]) -> Iterator[str]: ...

    def count_tokens(self, messages: list[Message]) -> int: ...

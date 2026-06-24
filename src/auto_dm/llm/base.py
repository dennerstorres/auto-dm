"""Base LLM provider interface and shared types."""
from __future__ import annotations

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

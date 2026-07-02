"""DM agent: wraps an LLM provider and returns structured narration + action.

The DM agent is the bridge between the LLM and the engine. It:

1. Builds the message list:
   - System prompt (DM_SYSTEM_PROMPT)
   - Context block (build_dm_context_block) describing current state
   - Player input as the latest user message
2. Calls the LLM provider's `chat()` method
3. Parses the LLM's text response into a DMResponse:
   - Narration (free text)
   - Optional Action (parsed from a fenced ```action``` block)

If the LLM response is malformed or no action block is present, the
agent returns narration only. Action parsing is forgiving — if a JSON
block is present but invalid, it is dropped (not raised) and logged.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Protocol

from auto_dm.agents.prompts import DM_SYSTEM_PROMPT, OPENING_INSTRUCTION, build_dm_context_block
from auto_dm.llm.base import Message
from auto_dm.llm.usage import UsageReport, chat_with_usage, iter_stream_with_usage
from auto_dm.state.manager import StateManager
from auto_dm.state.models import Action, ActionType


logger = logging.getLogger(__name__)


# Regex for the action block. The DM is told to use:
# ```action
# { ... json ... }
# ```
_ACTION_FENCE_RE = re.compile(
    r"```action\s*\n(?P<body>\{.*?\})\s*```",
    re.DOTALL,
)


# ============================================================================
# Response types
# ============================================================================


@dataclass
class DMResponse:
    """Result of asking the DM agent for narration.

    Attributes:
        narration: The free-text narration (always present).
        action: The structured Action if the DM emitted one; None otherwise.
        raw_text: The full LLM response text, useful for debugging.
        usage: Token-usage report for the underlying LLM call, if any.
    """

    narration: str
    action: Optional[Action] = None
    raw_text: str = ""
    usage: Optional[UsageReport] = None

    @property
    def has_action(self) -> bool:
        return self.action is not None


# ============================================================================
# LLMProvider narrowing for typing
# ============================================================================


class _ProviderLike(Protocol):
    """Subset of LLMProvider we need. Lets tests pass mocks easily."""

    def chat(self, messages: list[Message]) -> str: ...


# ============================================================================
# Parser
# ============================================================================


def parse_dm_response(
    raw_text: str, *, usage: Optional[UsageReport] = None
) -> DMResponse:
    """Parse the LLM's raw output into a DMResponse.

    Splits out the optional ```action``` fenced JSON block. If no block
    is present, the whole text is narration. If a block is present but
    malformed JSON, the block is dropped (logged) and narration is the
    whole text minus the block.
    """
    raw_text = raw_text or ""
    match = _ACTION_FENCE_RE.search(raw_text)
    if not match:
        # No action block — whole text is narration
        return DMResponse(narration=raw_text.strip(), raw_text=raw_text, usage=usage)

    body = match.group("body")
    narration = (raw_text[: match.start()] + raw_text[match.end() :]).strip()

    try:
        data = json.loads(body)
        action = _dict_to_action(data)
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        logger.warning("DM emitted a malformed action block: %s", exc)
        action = None

    return DMResponse(narration=narration, action=action, raw_text=raw_text, usage=usage)


def _dict_to_action(data: dict) -> Action:
    """Translate the DM's loose JSON into a typed Action.

    The DM prompt uses a slightly different vocabulary than the engine's
    ActionType enum (``attack`` vs ``ATTACK``, etc). We coerce types and
    raise ValueError on anything we can't make sense of.
    """
    if not isinstance(data, dict):
        raise ValueError("action block must be a JSON object")

    action_type_str = data.get("action_type")
    if not action_type_str:
        raise ValueError("action_type is required")

    try:
        action_type = ActionType(action_type_str)
    except ValueError as exc:
        raise ValueError(f"Unknown action_type: {action_type_str!r}") from exc

    actor_id = data.get("actor_id")
    if not actor_id:
        raise ValueError("actor_id is required")

    target_id = data.get("target_id") or None
    params = data.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    dialogue = data.get("dialogue") or None
    reasoning = data.get("reasoning") or None

    return Action(
        actor_id=actor_id,
        action_type=action_type,
        target_id=target_id,
        params=params,
        dialogue=dialogue,
        reasoning=reasoning,
    )


# ============================================================================
# Agent
# ============================================================================


@dataclass
class DMAgent:
    """Wraps an LLM provider as a narrator.

    Parameters:
        provider: An LLM provider (real or mock). Must implement ``chat()``.
        state_manager: Game state manager. Used to build the context block.
        system_prompt: Override the default DM system prompt if needed.
        last_n_history: How many past narrative entries to include as
            recent assistant/user turns in the conversation.
        extra_messages: Additional messages to inject (useful for tests).
    """

    provider: _ProviderLike
    state_manager: StateManager
    system_prompt: str = DM_SYSTEM_PROMPT
    last_n_history: int = 6
    extra_messages: list[Message] = field(default_factory=list)

    # ----- Public API --------------------------------------------------------

    def ask(self, player_input: str) -> DMResponse:
        """Send player input through the DM and return narration (+ action).

        This is a single LLM round-trip. The narrative loop may chain
        multiple ``ask`` calls when the engine intervenes (e.g. combat).
        The returned :class:`DMResponse` carries the token ``usage`` so
        the web layer can bill/limit it.
        """
        messages = self._build_messages(player_input)
        raw, usage = chat_with_usage(self.provider, messages)
        return parse_dm_response(raw, usage=usage)

    def stream(self, player_input: str):
        """Yield narration tokens as they arrive (no action parsing).

        Provided for the CLI's streaming UX. Use ``ask`` when you need
        the structured Action.
        """
        for tok, _ in self.stream_with_usage(player_input):
            if tok:
                yield tok

    def stream_with_usage(self, player_input: str):
        """Like :meth:`stream` but also yields a final ``UsageReport``.

        Yields ``(token, None)`` for each chunk and one
        ``("", UsageReport)`` at the end. The web/SSE layer uses this to
        bill streamed turns; the CLI uses the text-only :meth:`stream`.
        """
        messages = self._build_messages(player_input)
        yield from iter_stream_with_usage(self.provider, messages)

    def generate_opening(self) -> DMResponse:
        """Generate the campaign opening narration (no player input).

        Used on the very first DM turn, before the player has acted.
        Sends the :data:`OPENING_INSTRUCTION` trigger as the final user
        message so the DM establishes the scene, chooses a starting
        location, and emits a ``move`` action to record it. The result
        is parsed like any other response (narration + optional action).
        """
        messages = self._build_messages(OPENING_INSTRUCTION)
        raw, usage = chat_with_usage(self.provider, messages)
        return parse_dm_response(raw, usage=usage)

    def stream_opening_with_usage(self):
        """Stream the opening narration token-by-token.

        Like :meth:`stream_with_usage` but driven by the
        :data:`OPENING_INSTRUCTION` trigger instead of player input.
        Action parsing is the caller's responsibility (the stream only
        yields text); the web opening-SSE producer accumulates the full
        text and parses the ``move`` block at the end.
        """
        messages = self._build_messages(OPENING_INSTRUCTION)
        yield from iter_stream_with_usage(self.provider, messages)

    # ----- Internals ---------------------------------------------------------

    def _build_messages(self, player_input: str) -> list[Message]:
        """Build the full message list for one DM turn."""
        messages: list[Message] = []

        # 1. System prompt + state context, fused into a single system
        #    message so the LLM sees them together.
        context = build_dm_context_block(self.state_manager)
        system_content = f"{self.system_prompt}\n\n{context}"
        messages.append(Message(role="system", content=system_content))

        # 2. Recent narrative log entries (alternating user/assistant).
        for entry in self.state_manager.state.narrative_log[-self.last_n_history :]:
            role = _role_to_llm_role(entry.role)
            messages.append(Message(role=role, content=entry.content))

        # 3. Extra messages (e.g. test fixtures or runtime hints).
        messages.extend(self.extra_messages)

        # 4. Player input as the latest user message.
        messages.append(Message(role="user", content=player_input))
        return messages


def _role_to_llm_role(state_role: str) -> str:
    """Translate internal narrative roles to LLM roles.

    - "dm" / "system" -> "assistant"
    - "player" / "companion" -> "user"
    """
    s = (state_role or "").lower()
    if s in ("dm", "assistant", "system"):
        return "assistant"
    return "user"

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
   - Optional CheckRequest (parsed from a fenced ```check``` block —
     Phase 52: the DM asking for a skill check and fixing its DC)

If the LLM response is malformed or no block is present, the agent
returns narration only. Block parsing is forgiving — if a JSON block is
present but invalid, it is dropped (not raised) and logged.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Protocol

from auto_dm.agents.prompts import (
    DM_SYSTEM_PROMPT,
    OPENING_INSTRUCTION,
    build_dm_context_block,
    get_narration_directive,
)
from auto_dm.llm.base import Message
from auto_dm.llm.usage import UsageReport, chat_with_usage
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

# Phase 52 — the DM asks for a check and fixes its DC up front:
# ```check
# { "check": "investigação", "dc": 15, "reason": "..." }
# ```
_CHECK_FENCE_RE = re.compile(
    r"```check\s*\n(?P<body>\{.*?\})\s*```",
    re.DOTALL,
)


# ============================================================================
# Response types
# ============================================================================


@dataclass
class CheckRequest:
    """A skill/ability/save check the DM asked for, with its DC (Phase 52).

    The DM fixes the DC *before* seeing the roll — that's the whole point
    of publishing it as a structured block instead of leaving it in prose.
    ``dc`` is carried raw here; :func:`auto_dm.engine.checks.clamp_dc`
    normalizes it when the request is stored on the state.

    ``advantage``/``disadvantage`` are circumstantial grants from the DM
    ("você tem tempo de sobra", "está escuro"), not sheet-derived ones.
    """

    check: str
    dc: object = None
    kind: Optional[str] = None
    reason: str = ""
    character_id: Optional[str] = None
    advantage: bool = False
    disadvantage: bool = False


@dataclass
class DMResponse:
    """Result of asking the DM agent for narration.

    Attributes:
        narration: The free-text narration (always present).
        action: The structured Action if the DM emitted one; None otherwise.
        check_request: The CheckRequest if the DM asked for a roll (Phase 52).
        raw_text: The full LLM response text, useful for debugging.
        usage: Token-usage report for the underlying LLM call, if any.
    """

    narration: str
    action: Optional[Action] = None
    check_request: Optional[CheckRequest] = None
    raw_text: str = ""
    usage: Optional[UsageReport] = None

    @property
    def has_action(self) -> bool:
        return self.action is not None

    @property
    def has_check_request(self) -> bool:
        return self.check_request is not None


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

    Splits out the optional ```action``` and ```check``` fenced JSON
    blocks. Whatever is left over is the narration. If a block is present
    but malformed JSON, the block is dropped (logged) and narration is
    still the whole text minus that block — a bad block never leaks the
    raw JSON into the player's feed.
    """
    raw_text = raw_text or ""

    action_match = _ACTION_FENCE_RE.search(raw_text)
    check_match = _CHECK_FENCE_RE.search(raw_text)

    action: Optional[Action] = None
    if action_match:
        try:
            action = _dict_to_action(json.loads(action_match.group("body")))
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning("DM emitted a malformed action block: %s", exc)

    check_request: Optional[CheckRequest] = None
    if check_match:
        try:
            check_request = _dict_to_check_request(
                json.loads(check_match.group("body"))
            )
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning("DM emitted a malformed check block: %s", exc)

    narration = _strip_spans(
        raw_text, [m.span() for m in (action_match, check_match) if m]
    )

    return DMResponse(
        narration=narration,
        action=action,
        check_request=check_request,
        raw_text=raw_text,
        usage=usage,
    )


def _strip_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """Remove the given (start, end) slices from ``text`` and trim.

    Cut back-to-front so earlier spans keep their original offsets.
    """
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]
    return text.strip()


def _dict_to_check_request(data: dict) -> CheckRequest:
    """Translate the DM's ```check``` JSON into a typed CheckRequest.

    Only ``check`` is required — a request without a named skill is
    meaningless. A missing or nonsense ``dc`` is tolerated here and
    normalized downstream by ``clamp_dc`` (defaults to DC 15).
    """
    if not isinstance(data, dict):
        raise ValueError("check block must be a JSON object")

    check = data.get("check") or data.get("skill") or data.get("pericia")
    if not check or not isinstance(check, str):
        raise ValueError("check is required and must be a string")

    reason = data.get("reason") or data.get("motivo") or ""
    if not isinstance(reason, str):
        reason = str(reason)

    kind = data.get("kind")
    if kind is not None and not isinstance(kind, str):
        kind = None

    character_id = data.get("character_id") or None
    if character_id is not None and not isinstance(character_id, str):
        character_id = None

    return CheckRequest(
        check=check,
        dc=data.get("dc"),
        kind=kind,
        reason=reason,
        character_id=character_id,
        advantage=bool(data.get("advantage")),
        disadvantage=bool(data.get("disadvantage")),
    )


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

    # ----- Internals ---------------------------------------------------------

    def _build_messages(self, player_input: str) -> list[Message]:
        """Build the full message list for one DM turn."""
        messages: list[Message] = []

        # 1. System prompt + state context + per-campaign narration budget,
        #    all fused into a single system message so the LLM sees them
        #    together. The narration directive honors the player's choice
        #    (curto / medio / longo) at campaign creation.
        context = build_dm_context_block(self.state_manager)
        directive = get_narration_directive(self.state_manager.state.narration_length)
        system_content = f"{self.system_prompt}\n\n{context}\n\n{directive}"
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

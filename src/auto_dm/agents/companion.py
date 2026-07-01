"""Companion agent: LLM-driven decisions for AI-controlled party members.

The companion agent is structurally similar to the DM agent (it wraps
an LLM provider and parses a ```action``` JSON block out of the
response), but it speaks as a specific character with a personality
rather than as the narrator.

Key differences from DMAgent:
- The "system prompt" is COMPANION_SYSTEM_PROMPT + a per-character
  identity block (race/class/personality/ideals/bonds/flaws).
- The "user" message is a *situation description* (combat turn prompt,
  exploration choice) rather than the player's free-text input.
- The agent returns a :class:`CompanionDecision` containing the
  companion's first-person narration AND the optional Action. Both
  go into the narrative log so the DM can incorporate the intent.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional, Protocol

from auto_dm.agents.dm import _ACTION_FENCE_RE, _dict_to_action
from auto_dm.agents.prompts import (
    COMPANION_SYSTEM_PROMPT,
    build_companion_identity_block,
)
from auto_dm.llm.base import Message
from auto_dm.llm.usage import UsageReport, chat_with_usage
from auto_dm.state.manager import StateManager
from auto_dm.state.models import Action, Character


logger = logging.getLogger(__name__)


# ============================================================================
# Result types
# ============================================================================


@dataclass
class CompanionDecision:
    """The result of asking a companion what to do.

    Attributes:
        intent: First-person narration of the companion's reasoning
            ("Eu levanto meu escudo para bloquear o ataque").
        action: The structured Action the companion wants to take, if any.
        raw_text: The full LLM response (for debugging).
    """

    intent: str
    action: Optional[Action] = None
    raw_text: str = ""
    usage: Optional[UsageReport] = None

    @property
    def has_action(self) -> bool:
        return self.action is not None


# ============================================================================
# Provider protocol (subset)
# ============================================================================


class _ProviderLike(Protocol):
    def chat(self, messages: list[Message]) -> str: ...


# ============================================================================
# Parser (companion-specific — same shape as DM's)
# ============================================================================


def parse_companion_response(
    raw_text: str, *, default_actor_id: str, usage: Optional[UsageReport] = None
) -> CompanionDecision:
    """Parse the LLM output into a CompanionDecision.

    Same fence/regex as the DM parser, but the action's ``actor_id`` is
    filled in automatically if the LLM omits it (companions sometimes
    forget to include their own id).
    """
    raw_text = raw_text or ""
    match = _ACTION_FENCE_RE.search(raw_text)
    if not match:
        return CompanionDecision(intent=raw_text.strip(), raw_text=raw_text, usage=usage)

    body = match.group("body")
    intent = (raw_text[: match.start()] + raw_text[match.end() :]).strip()

    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("action block must be a JSON object")
        if not data.get("actor_id"):
            data["actor_id"] = default_actor_id
        action = _dict_to_action(data)
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        logger.warning("Companion emitted a malformed action block: %s", exc)
        action = None

    return CompanionDecision(intent=intent, action=action, raw_text=raw_text, usage=usage)


# ============================================================================
# CompanionAgent
# ============================================================================


@dataclass
class CompanionAgent:
    """Wraps an LLM provider as a specific party member.

    Parameters:
        provider: The LLM (real or mock) to call.
        character: This companion's Character sheet.
        state_manager: Shared game state. Used to build the context.
        system_prompt: Override the default companion system prompt.
        last_n_history: How many recent narrative entries to include.
        extra_messages: Additional messages to inject (useful for tests).
    """

    provider: _ProviderLike
    character: Character
    state_manager: StateManager
    system_prompt: str = COMPANION_SYSTEM_PROMPT
    last_n_history: int = 4
    extra_messages: list[Message] = field(default_factory=list)

    # ----- Public API --------------------------------------------------------

    def decide(self, situation: str) -> CompanionDecision:
        """Ask the companion what to do given the current situation.

        ``situation`` is a short prompt describing what's happening
        ("Your turn in combat. Enemies: goblin_a, goblin_b...").
        """
        messages = self._build_messages(situation)
        raw, usage = chat_with_usage(self.provider, messages)
        return parse_companion_response(
            raw, default_actor_id=self.character.id, usage=usage
        )

    def decide_in_combat(
        self, enemies: list[str], *, allies: list[str] | None = None
    ) -> CompanionDecision:
        """Convenience for combat turns: build a standard situation prompt.

        ``enemies`` and ``allies`` are lists of creature IDs visible to
        the companion. The engine validates target_id; this just gives
        the LLM enough context to pick a target.
        """
        ally_str = ", ".join(allies) if allies else "(nenhum)"
        enemy_str = ", ".join(enemies) if enemies else "(nenhum visível)"
        situation = (
            f"É o seu turno em combate. Aliados: {ally_str}. "
            f"Inimigos visíveis: {enemy_str}. "
            f"Seu HP atual: {self.character.hp_current}/{self.character.hp_max}. "
            f"Decida sua ação (uma ação ou movimento; seja conciso)."
        )
        return self.decide(situation)

    # ----- Internals ---------------------------------------------------------

    def _build_messages(self, situation: str) -> list[Message]:
        messages: list[Message] = []
        # System: prompt + identity + state summary (lighter than DM's,
        # since the companion only cares about its own situation).
        from auto_dm.agents.prompts import build_dm_context_block

        identity = build_companion_identity_block(self.character)
        context = build_dm_context_block(self.state_manager, last_n=self.last_n_history)
        system_content = f"{self.system_prompt}\n\n{identity}\n\n{context}"
        messages.append(Message(role="system", content=system_content))

        # Recent narrative as alternating user/assistant.
        for entry in self.state_manager.state.narrative_log[-self.last_n_history :]:
            role = "assistant" if entry.role in ("dm", "system", "companion") else "user"
            messages.append(Message(role=role, content=entry.content))

        # Extra messages (e.g. test fixtures).
        messages.extend(self.extra_messages)

        # Situation as the latest user message.
        messages.append(Message(role="user", content=situation))
        return messages

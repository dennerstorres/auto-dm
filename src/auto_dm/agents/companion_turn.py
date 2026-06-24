"""Companion turn orchestration.

When a companion's turn comes up in initiative, this module runs
their full turn:

1. Look up the companion's :class:`CompanionAgent` (created once and
   cached for the whole combat).
2. Ask the agent for a decision given the current situation.
3. Log the companion's intent in the narrative log.
4. Dispatch the chosen Action through :class:`CombatEngine`.
5. Log the result in the narrative log.

Returns a :class:`CompanionTurnResult` with everything the CLI or
narrative loop needs to display or follow up.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from auto_dm.agents.companion import CompanionAgent, CompanionDecision
from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    Action,
    ActionResult,
    Character,
    NarrativeEntry,
)


logger = logging.getLogger(__name__)


@dataclass
class CompanionTurnResult:
    """Outcome of one companion's turn."""

    actor_id: str
    actor_name: str
    intent: str
    decision: CompanionDecision
    action: Optional[Action] = None
    action_result: Optional[ActionResult] = None

    @property
    def has_action(self) -> bool:
        return self.action is not None and self.action_result is not None


def build_companion_agents(
    party: list[Character],
    state_manager: StateManager,
    provider_factory,
) -> dict[str, CompanionAgent]:
    """Create a :class:`CompanionAgent` for each non-player party member.

    The player's character is excluded (the player decides for themself).
    ``provider_factory`` is a callable that returns a fresh LLM provider
    instance for each companion. In tests this is just a fixture; in
    production it would return a provider configured with the same
    base settings as the DM.
    """
    agents: dict[str, CompanionAgent] = {}
    for c in party:
        if c.is_player:
            continue
        agents[c.id] = CompanionAgent(
            provider=provider_factory(),
            character=c,
            state_manager=state_manager,
        )
    return agents


def run_companion_turn(
    state_manager: StateManager,
    combat_engine: CombatEngine,
    agent: CompanionAgent,
    *,
    enemies: list[str],
    allies: Optional[list[str]] = None,
) -> CompanionTurnResult:
    """Execute one companion's combat turn.

    Steps:
        1. Verify it's this companion's turn.
        2. Ask the agent to decide.
        3. Log the intent in the narrative log.
        4. If the agent produced an Action, dispatch through the engine.
        5. Log the result message (success or refusal).
    """
    actor = agent.character
    if state_manager.state.in_combat:
        current = state_manager.current_actor_id()
        if current != actor.id:
            return CompanionTurnResult(
                actor_id=actor.id,
                actor_name=actor.name,
                intent="",
                decision=CompanionDecision(intent=""),
                action=None,
                action_result=ActionResult(
                    success=False,
                    message=(
                        f"Não é o turno de {actor.name} "
                        f"(é o turno de {current!r})."
                    ),
                ),
            )

    decision = agent.decide_in_combat(enemies=enemies, allies=allies)
    _log_intent(state_manager, actor, decision)

    if decision.action is None:
        # Companion had nothing to say / no action to take.
        return CompanionTurnResult(
            actor_id=actor.id,
            actor_name=actor.name,
            intent=decision.intent,
            decision=decision,
        )

    action_result = combat_engine.execute_action(state_manager, decision.action)
    _log_result(state_manager, actor, action_result)

    return CompanionTurnResult(
        actor_id=actor.id,
        actor_name=actor.name,
        intent=decision.intent,
        decision=decision,
        action=decision.action,
        action_result=action_result,
    )


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def _log_intent(
    state_manager: StateManager, actor: Character, decision: CompanionDecision
) -> None:
    if not decision.intent:
        return
    state_manager.append_narrative(
        NarrativeEntry(
            timestamp=datetime.now(timezone.utc),
            role="companion",
            speaker=actor.name,
            content=decision.intent,
        )
    )


def _log_result(
    state_manager: StateManager, actor: Character, result: ActionResult
) -> None:
    if not result.message:
        return
    state_manager.append_narrative(
        NarrativeEntry(
            timestamp=datetime.now(timezone.utc),
            role="companion",
            speaker=actor.name,
            content=result.message,
        )
    )

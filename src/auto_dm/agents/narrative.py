"""Narrative loop: bridge between player input, DM narration, and engine.

The loop is intentionally thin. Its responsibilities:

1. Forward the player's input to the DM agent.
2. Append the resulting narration to the state log.
3. If the DM emitted an Action, dispatch it through the engine.
4. If the action produced mechanical output, ask the DM to narrate it.

Out-of-combat actions that mutate world state directly (move, say,
short_rest, long_rest) are stubbed. Combat actions go through the
real CombatEngine.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Optional

from auto_dm.agents.companion_turn import CompanionTurnResult
from auto_dm.agents.dm import DMAgent, DMResponse
from auto_dm.agents.prompts import get_followup_max_sentences
from auto_dm.agents.summarizer import NarrativeSummarizer, summarize_once
from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.llm.usage import UsageReport
from auto_dm.state.manager import StateManager
from auto_dm.state.models import Action, ActionResult, ActionType, NarrativeEntry


# Phase 33 — tag the summarizer's LLM usage so the web layer (and any
# other consumer of ``result.usages``) can split billing by source.
# We use a plain string here to avoid an agents→web dependency cycle.
# Empty / "player" => player-turn cost; "summarizer" => background cost.
_SUMMARIZER_USAGE_KIND = "summarizer"


logger = logging.getLogger(__name__)


# Action types that are flavor-only and don't need engine dispatch.
_FLAVOR_ONLY = {ActionType.SAY}


@dataclass
class NarrativeResult:
    """The outcome of one turn of the narrative loop."""

    narration: str
    action: Optional[Action] = None
    action_result: Optional[ActionResult] = None
    follow_up_narration: Optional[str] = None
    # Phase 25h: companion turns that fired during this cycle (empty when
    # the player isn't in combat, or when no companion had a turn yet).
    companion_results: list[CompanionTurnResult] = field(default_factory=list)
    # Phase 30: token-usage reports for every LLM call this turn (DM
    # narration + follow-up + any companion turns). The web layer
    # persists one UsageEvent per entry for billing/limits.
    usages: list[UsageReport] = field(default_factory=list)

    @property
    def has_action(self) -> bool:
        return self.action is not None


# ============================================================================
# Main entry point
# ============================================================================


def process_player_action(
    state_manager: StateManager,
    player_input: str,
    dm_agent: DMAgent,
    *,
    combat_engine: Optional[CombatEngine] = None,
    summarizer: Optional[NarrativeSummarizer] = None,
) -> NarrativeResult:
    """Process one player turn end-to-end.

    Steps:
        1. Record the player input in the narrative log.
        2. Ask the DM agent for a response.
        3. Record the DM's narration in the log.
        4. If the DM emitted an Action, dispatch it (stubbed for combat).
        5. If dispatch produced output, optionally ask the DM once more
           so it can narrate the result.
        6. Run the periodic summarizer hook (Phase 33) — only at end of
           turn, so we always summarize a complete log slice.

    Returns a :class:`NarrativeResult` with everything the CLI needs
    to print and any state mutations already applied.
    """
    _log_player(state_manager, player_input)
    dm_response = dm_agent.ask(player_input)
    _log_dm(state_manager, dm_response)

    result = NarrativeResult(
        narration=dm_response.narration,
        action=dm_response.action,
    )
    if dm_response.usage is not None:
        result.usages.append(dm_response.usage)

    if dm_response.action is None:
        _maybe_summarize(state_manager, summarizer, result)
        return result

    # Dispatch the action
    action_result = _dispatch_action(
        state_manager,
        dm_response.action,
        combat_engine=combat_engine,
    )
    result.action_result = action_result

    # If the action produced mechanical output, give the DM a chance to
    # narrate it. This is a second round-trip and only happens when
    # there's something concrete to narrate (a hit, a miss, a save, etc).
    if action_result is not None and _should_narrate_result(action_result):
        follow_up = _narrate_action_result(
            state_manager, dm_agent, dm_response.action, action_result
        )
        if follow_up.narration:
            result.follow_up_narration = follow_up.narration
        if follow_up.usage is not None:
            result.usages.append(follow_up.usage)

    # Phase 33 — end-of-turn summarizer hook. Runs once after the
    # complete turn (player + optional follow-up). Outside any inner
    # loops so the summarizer sees a consistent log slice.
    _maybe_summarize(state_manager, summarizer, result)

    return result


def _maybe_summarize(
    state_manager: StateManager,
    summarizer: Optional[NarrativeSummarizer],
    result: NarrativeResult,
) -> None:
    """End-of-turn helper: run summarize_once, tag usage, append to result.

    Wraps the summarizer module's call so narrative.py never has to know
    about the trigger predicate. ``result.usages`` accumulates the LLM
    usage with ``kind="summarizer"`` so the web layer can bill it
    separately from player-turn cost.
    """
    if summarizer is None:
        return
    usage = summarize_once(state_manager, summarizer)
    if usage is not None:
        tagged = replace(usage, kind=_SUMMARIZER_USAGE_KIND)
        result.usages.append(tagged)


def generate_opening(
    state_manager: StateManager,
    dm_agent: DMAgent,
) -> NarrativeResult:
    """Generate the campaign opening narration (no player input).

    Runs on the very first DM turn, before the player has acted. Unlike
    :func:`process_player_action`, this does **not** log a player line —
    only the DM's opening narration is recorded (role ``dm``). The DM is
    instructed (via :data:`OPENING_INSTRUCTION`) to choose a starting
    location and emit a ``move`` action whose ``params.destination`` we
    apply to ``state.current_location`` so the chosen scene persists.

    The opening is pure narration — no rolls, damage, or combat — so we
    do not dispatch the action through the engine; we only read the
    ``destination`` from the ``move`` action to set the world location.

    Returns a :class:`NarrativeResult` shaped like a normal turn so the
    CLI/web layers can render and bill it uniformly.
    """
    dm_response = dm_agent.generate_opening()
    _log_dm(state_manager, dm_response)

    result = NarrativeResult(
        narration=dm_response.narration,
        action=dm_response.action,
    )
    if dm_response.usage is not None:
        result.usages.append(dm_response.usage)

    # Apply the chosen starting location if the DM emitted a move action.
    if dm_response.action is not None and dm_response.action.action_type == ActionType.MOVE:
        destination = (dm_response.action.params or {}).get("destination")
        if destination:
            state_manager.state.current_location = destination

    return result
# ============================================================================


def _dispatch_action(
    state_manager: StateManager,
    action: Action,
    *,
    combat_engine: Optional[CombatEngine],
) -> Optional[ActionResult]:
    """Run the action through whatever engine is available.

    Returns ``None`` for flavor-only actions (say) and for actions we
    can't resolve yet (combat stubs). When the real engine lands in
    Phase 7, this is where the heavy lifting happens.
    """
    if action.action_type in _FLAVOR_ONLY:
        # Dialogue is pure narration; no mechanical effect.
        return None

    # Combat actions go through the combat engine when one is provided.
    if combat_engine is not None and _is_combat_action(action):
        try:
            return combat_engine.execute_action(state_manager, action)
        except Exception as exc:  # noqa: BLE001 — surface for DM narration
            logger.exception("Combat engine failed on %s", action.action_type)
            return ActionResult(
                success=False,
                message=f"A ação {action.action_type.value} falhou: {exc}",
                mechanical={},
            )

    # Non-combat actions: stub for now (Phase 7+ for full effects).
    if action.action_type in {
        ActionType.MOVE,
        ActionType.SHORT_REST,
        ActionType.LONG_REST,
    }:
        return _stub_noncombat_action(state_manager, action)

    # Combat-flavored actions with no engine wired in: stub.
    logger.info(
        "Action %s dispatched as stub (combat engine not wired)", action.action_type
    )
    return _stub_combat_action(action)


def _is_combat_action(action: Action) -> bool:
    return action.action_type in {
        ActionType.ATTACK,
        ActionType.CAST_SPELL,
        ActionType.DASH,
        ActionType.DISENGAGE,
        ActionType.DODGE,
        ActionType.HELP,
        ActionType.HIDE,
        ActionType.READY,
        ActionType.SEARCH,
        ActionType.USE_OBJECT,
        ActionType.SHOVE,
        ActionType.GRAPPLE,
        ActionType.TWO_WEAPON_ATTACK,
        ActionType.OPPORTUNITY_ATTACK,
        ActionType.END_COMBAT,
        ActionType.DEATH_SAVE,
    }


def _stub_combat_action(action: Action) -> ActionResult:
    """Placeholder result for combat actions when the engine is offline.

    Lets the DM know an action was requested but couldn't resolve.
    Phase 7 will replace this with real combat resolution.
    """
    return ActionResult(
        success=False,
        message=(
            f"(stub) Ação de combate {action.action_type.value} ainda não "
            f"está integrada — Phase 7."
        ),
        mechanical={"stub": True},
    )


def _stub_noncombat_action(
    state_manager: StateManager, action: Action
) -> ActionResult:
    """Stub for out-of-combat world actions."""
    if action.action_type == ActionType.MOVE:
        destination = action.params.get("destination", "(destino não especificado)")
        return ActionResult(
            success=True,
            message=f"Você se move para {destination}.",
            mechanical={"destination": destination},
        )
    if action.action_type == ActionType.SHORT_REST:
        return ActionResult(
            success=True,
            message="Você faz um descanso curto.",
            mechanical={"rest": "short"},
        )
    if action.action_type == ActionType.LONG_REST:
        return ActionResult(
            success=True,
            message="Você faz um descanso longo.",
            mechanical={"rest": "long"},
        )
    return ActionResult(
        success=False,
        message=f"(stub) Ação {action.action_type.value} não tratada.",
        mechanical={},
    )


def _should_narrate_result(result: ActionResult) -> bool:
    """Decide whether the DM should narrate a mechanical result.

    We narrate when the action produced a real, positive mechanical
    outcome (a hit, a save, a move) — i.e. when the result has a
    message and the engine didn't just refuse the action. Rejections
    (wrong turn, bad target, unconscious actor) bubble up to the CLI
    as feedback and don't need a second LLM round-trip.
    """
    if not result.message:
        return False
    if result.mechanical.get("stub"):
        return False
    if not result.success:
        # Engine refused — don't ask the DM to narrate a refusal.
        return False
    return True


def _narrate_action_result(
    state_manager: StateManager,
    dm_agent: DMAgent,
    action: Action,
    result: ActionResult,
) -> DMResponse:
    """Second DM round-trip: ask the DM to narrate the action result.

    Returns the full :class:`DMResponse` (narration + usage) so the
    caller can bill the second LLM call too.
    """
    followup_budget = get_followup_max_sentences(state_manager.state.narration_length)
    prompt = (
        f"A ação {action.action_type.value} que você acabou de anunciar "
        f"produziu o seguinte resultado mecânico:\n\n"
        f"Sucesso: {result.success}\n"
        f"Mensagem: {result.message}\n"
        f"Detalhes: {result.mechanical}\n\n"
        f"Agora narre esse resultado {followup_budget} (pt-BR, segunda pessoa). "
        "Não invente novos números — apenas descreva o que aconteceu."
    )
    follow_up = dm_agent.ask(prompt)
    _log_dm(state_manager, follow_up)
    return follow_up


# ============================================================================
# Logging helpers
# ============================================================================


def _log_player(state_manager: StateManager, text: str) -> None:
    state_manager.append_narrative(
        NarrativeEntry(
            timestamp=datetime.now(timezone.utc),
            role="player",
            speaker="Jogador",
            content=text,
        )
    )


def _log_dm(state_manager: StateManager, response: DMResponse) -> None:
    if not response.narration:
        return
    state_manager.append_narrative(
        NarrativeEntry(
            timestamp=datetime.now(timezone.utc),
            role="dm",
            speaker="DM",
            content=response.narration,
        )
    )

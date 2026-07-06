"""Companion reaction heuristic (Phase 41c).

Companions are AI-controlled party members. The human player is only
prompted for their *own* character's reaction (the web modal in
``app.js::checkPendingReaction`` gates on the player). When an eligible
responder is a companion, we don't want the trigger to sit in
``pending_reaction`` until the TTL expires — the companion should just
*use* its reaction. That's what this module decides.

``choose_companion_reaction`` is a small, deterministic, defensive
heuristic (no LLM — project principle #1: mechanics are authoritative):

* **Revive a downed ally** — Healing Word is always taken when eligible;
  bringing someone back from 0 HP is the highest-value reaction in 5e.
* **Self-preservation below half HP** — prefer the damage-reduction
  reactions that resolve as a *refund* (Uncanny Dodge, then Parry) since
  by publication time the hit has already landed (see ``engine/reactions``);
  fall back to Shield, which helps against the *next* attack this round.
* **Otherwise decline** — don't burn the once-per-round reaction on a
  low-value trigger. Counterspell and Hellish Rebuke are intentionally
  *not* auto-used: they're too situational (slot cost, friendly-fire
  risk, revenge vs. survival) and are left for a future, richer AI.

The heuristic returns ``None`` to decline. ``publish_reaction_trigger``
calls this when the eligible responder is a companion and resolves the
chosen kind immediately via ``apply_reaction``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from auto_dm.engine.actions import TriggerEvent
from auto_dm.engine.reactions import ReactionKind, ReactionResolution, apply_reaction
from auto_dm.state.models import Character

if TYPE_CHECKING:  # pragma: no cover
    from auto_dm.engine.combat_engine import CombatEngine
    from auto_dm.state.manager import StateManager

# Below this HP fraction a companion defends itself instead of declining.
LOW_HP_FRACTION = 0.5


def choose_companion_reaction(
    companion: Character,
    trigger: TriggerEvent,
    eligible: list[ReactionKind],
) -> Optional[ReactionKind]:
    """Pick a reaction for ``companion``, or ``None`` to decline.

    ``eligible`` is the list returned by ``eligible_reactions`` for this
    companion + trigger (already gated on ``reaction_available`` and slot
    availability), so any kind we return here is legal to resolve.
    """
    eligible_set = set(eligible)

    # 1. Reviving a downed ally is always worth the reaction.
    if ReactionKind.HEALING_WORD in eligible_set:
        return ReactionKind.HEALING_WORD

    # 2. When wounded, defend. Prefer the refund-style reductions (they
    #    actually undo part of the hit that just landed); Shield only
    #    helps against subsequent attacks this round.
    max_hp = companion.hp_max or 0
    hp_ratio = (companion.hp_current / max_hp) if max_hp > 0 else 1.0
    if hp_ratio < LOW_HP_FRACTION:
        if ReactionKind.UNCANNY_DODGE in eligible_set:
            return ReactionKind.UNCANNY_DODGE
        if ReactionKind.PARRY in eligible_set:
            return ReactionKind.PARRY
        if ReactionKind.SHIELD in eligible_set:
            return ReactionKind.SHIELD

    # 3. Healthy and not reviving anyone — hold the reaction.
    return None


def auto_resolve_companion_reaction(
    engine: "CombatEngine",
    state_manager: "StateManager",
    companion: Character,
    trigger: TriggerEvent,
    eligible: list[ReactionKind],
) -> Optional[ReactionResolution]:
    """Resolve a companion's reaction in-place, or ``None`` if it declines.

    Thin wrapper: choose → ``apply_reaction``. Returns the resolution so
    the caller can surface it in narration; ``None`` means the companion
    held its reaction (nothing happened, nothing to narrate).
    """
    kind = choose_companion_reaction(companion, trigger, eligible)
    if kind is None:
        return None
    return apply_reaction(
        engine, state_manager, companion.id, kind, trigger,
    )

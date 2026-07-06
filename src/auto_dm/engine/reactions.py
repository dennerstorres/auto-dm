"""Reaction resolution (Phase 41b).

A *reaction* is an action taken on someone else's turn in response to a
*trigger* (PHB p. 190). The engine owns three things here:

1. **Eligibility** — given a trigger, which ``ReactionKind``\\s can a
   given creature legally take right now? (class + level for features,
   known/prepared + slot for spells, ``reaction_available`` gate.)
2. **Resolution** — ``apply_reaction`` executes the chosen reaction:
   consumes the creature's reaction (and a spell slot for spell
   reactions), mutates state (damage, healing, AC buff, spell cancel),
   and returns a ``ReactionResolution`` describing what happened.
3. **Publication** — ``publish_reaction_trigger`` finds eligible
   responders in the party and stamps ``Character.pending_reaction`` so
   the web layer (Phase 41c) can surface a modal and call back.

This module is mechanically authoritative — it never asks the LLM. The
LLM only *narrates* the ``ReactionResolution`` and (for the player) picks
which eligible reaction to use. That keeps project principle #1 intact.

PHB references are inline. Backwards-compat note: damage-reduction
reactions (Uncanny Dodge, Parry) operate as a *refund* — by the time the
trigger is published the attack's damage has usually already been
applied by ``_handle_attack``; halving it means healing half back. The
trigger carries ``attack_damage`` so the refund is exact.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from auto_dm.engine.actions import (
    OnAllyDown,
    OnDamageTaken,
    OnHitByAttack,
    OnSeeingSpellCast,
    ReactionKind,
    TriggerEvent,
    build_pending_reaction,
)
from auto_dm.engine.spellcasting import (
    can_cast_as_known,
    can_cast_as_prepared,
    has_slot,
)
from auto_dm.state.models import Ability, Character

if TYPE_CHECKING:
    import random

    from auto_dm.engine.combat_engine import CombatEngine
    from auto_dm.state.manager import StateManager

logger = logging.getLogger(__name__)


# ============================================================================
# Spell-reaction metadata
# ============================================================================
#
# Spell reactions (Shield, Counterspell, Hellish Rebuke, Healing Word)
# are ordinary leveled spells whose casting time is "1 reaction, taken on
# <trigger>". We reuse the standard ``cast_spell`` pipeline for slot +
# known/prepared validation; this table just maps the reaction kind to
# the spell name, the minimum slot level, and the classes that can know
# or prepare it. Min slot level = spell level (upcasting is allowed but
# pointless for these except Hellish Rebuke).

@dataclass(frozen=True)
class _SpellReaction:
    spell_name: str
    min_slot: int
    classes: frozenset[str]  # lowercased class names that get this spell


_SPELL_REACTIONS: dict[ReactionKind, _SpellReaction] = {
    ReactionKind.SHIELD: _SpellReaction(
        spell_name="Shield",
        min_slot=1,
        classes=frozenset({"wizard", "sorcerer"}),
    ),
    ReactionKind.COUNTERSPELL: _SpellReaction(
        spell_name="Counterspell",
        min_slot=3,
        classes=frozenset({"wizard", "sorcerer", "warlock"}),
    ),
    ReactionKind.HELLISH_REBUKE: _SpellReaction(
        spell_name="Hellish Rebuke",
        min_slot=1,
        classes=frozenset({"warlock"}),
    ),
    ReactionKind.HEALING_WORD: _SpellReaction(
        spell_name="Healing Word",
        min_slot=1,
        classes=frozenset({"cleric", "druid", "bard"}),
    ),
}


# Class-level reaction features (no slot). Eligibility is gated by class
# + level (the engine treats the feature as always-known past the level).
_CLASS_FEATURE_REACTIONS: dict[ReactionKind, dict] = {
    ReactionKind.UNCANNY_DODGE: {
        "classes": frozenset({"rogue", "monk"}),  # Rogue L5; Monk gets it at L5 too (PHB p. 79 currently Evasion path; we gate Rogue here)
        "min_level": 5,
    },
    ReactionKind.PARRY: {
        # Parry is a Battle Master maneuver (Fighter L3+ with the
        # archetype), but the project's MVP gates maneuvers simply by
        # Fighter level. The plan caps its effect at L7 (superiority die
        # grows). Eligibility: Fighter L3+.
        "classes": frozenset({"fighter"}),
        "min_level": 3,
    },
}


# ============================================================================
# ReactionResolution
# ============================================================================


@dataclass
class ReactionResolution:
    """Outcome of resolving one reaction.

    The narrative layer reads ``message`` and ``mechanical``; the engine
    consumes ``consumed_reaction`` / ``consumed_slot_level`` to update
    the action economy. ``damage_modified_to`` / ``spell_cancelled``
    flag the side-effects the caller must honour.
    """

    kind: ReactionKind
    responder_id: str
    success: bool
    message: str
    consumed_reaction: bool = False
    consumed_slot_level: int = 0  # 0 for non-spell reactions
    # Uncanny Dodge / Parry: the new (reduced) damage total the caller
    # should treat the original attack as having dealt. ``None`` = no
    # damage change.
    damage_modified_to: Optional[int] = None
    # Counterspell: the spell the reaction cancelled (if any).
    spell_cancelled: bool = False
    # Healing Word: HP the ally was raised to.
    healed_to: Optional[int] = None
    # Hellish Rebuke: damage dealt to the attacker + its new HP.
    rebuke_damage: int = 0
    rebuke_target_hp: Optional[int] = None
    reason: str = ""
    mechanical: dict = field(default_factory=dict)


# ============================================================================
# Eligibility
# ============================================================================


def _spell_known_or_prepared(ch: Character, spell_name: str) -> bool:
    return can_cast_as_known(ch, spell_name) or can_cast_as_prepared(ch, spell_name)


def _has_feature_reaction(ch: Character, kind: ReactionKind) -> bool:
    spec = _CLASS_FEATURE_REACTIONS[kind]
    if ch.class_.lower() not in spec["classes"]:
        return False
    return ch.level >= spec["min_level"]


def eligible_reactions(
    creature: object,
    trigger: TriggerEvent,
) -> list[ReactionKind]:
    """Which reactions ``creature`` may take in response to ``trigger``.

    Conservative by design: a kind is only eligible when every gate
    passes (class + level for features; known/prepared + an available
    slot for spells; the ``reaction_available`` flag for all). NPCs are
    not eligible in the MVP (they don't carry structured spellcasting or
    class levels) and return ``[]``.
    """

    if not isinstance(creature, Character):
        return []
    if not creature.reaction_available:
        return []
    if creature.spellcasting is None and not _has_any_feature_reaction(creature):
        return []

    out: list[ReactionKind] = []

    # Spell reactions
    for kind, meta in _SPELL_REACTIONS.items():
        if creature.class_.lower() not in meta.classes:
            continue
        if creature.spellcasting is None:
            continue
        if not _spell_known_or_prepared(creature, meta.spell_name):
            continue
        if not has_slot(creature.spellcasting, meta.min_slot):
            continue
        if not _trigger_matches_spell(kind, trigger, creature):
            continue
        out.append(kind)

    # Class-feature reactions
    for kind in _CLASS_FEATURE_REACTIONS:
        if not _has_feature_reaction(creature, kind):
            continue
        if not _trigger_matches_feature(kind, trigger, creature):
            continue
        out.append(kind)

    return out


def _has_any_feature_reaction(ch: Character) -> bool:
    return any(_has_feature_reaction(ch, k) for k in _CLASS_FEATURE_REACTIONS)


def _trigger_matches_spell(
    kind: ReactionKind, trigger: TriggerEvent, ch: Character,
) -> bool:
    if kind == ReactionKind.SHIELD:
        # Shield triggers when you are hit by an attack (PHB p. 275) or
        # targeted by Magic Missile. We surface it for any attack hit on
        # the caster; the caller narrates Magic Missile immunity.
        return (
            isinstance(trigger, OnHitByAttack)
            and trigger.target_id == ch.id
        )
    if kind == ReactionKind.COUNTERSPELL:
        # Triggered by seeing a creature casting a spell within 60 ft.
        return (
            isinstance(trigger, OnSeeingSpellCast)
            and trigger.caster_id != ch.id
            and trigger.level >= 1
        )
    if kind == ReactionKind.HELLISH_REBUKE:
        # Triggered by taking damage (PHB p. 284).
        return (
            isinstance(trigger, OnDamageTaken)
            and trigger.target_id == ch.id
            and trigger.source_id
        )
    if kind == ReactionKind.HEALING_WORD:
        # PHB: Healing Word is a bonus action. The project surfaces it as
        # a reaction to an ally dropping (Phase 41 plan) so a party member
        # can revive someone out of turn.
        return (
            isinstance(trigger, OnAllyDown)
            and trigger.ally_id
            and trigger.ally_id != ch.id
        )
    return False


def _trigger_matches_feature(
    kind: ReactionKind, trigger: TriggerEvent, ch: Character,
) -> bool:
    if kind == ReactionKind.UNCANNY_DODGE:
        # "When an attacker that you can see hits you with an attack"
        return (
            isinstance(trigger, OnHitByAttack)
            and trigger.target_id == ch.id
        )
    if kind == ReactionKind.PARRY:
        # "When you are damaged by a melee attack" — same trigger family.
        return (
            isinstance(trigger, OnHitByAttack)
            and trigger.target_id == ch.id
            and trigger.is_melee
        )
    return False


# ============================================================================
# Resolution
# ============================================================================


def apply_reaction(
    engine: "CombatEngine",
    state_manager: "StateManager",
    responder_id: str,
    kind: ReactionKind,
    trigger: TriggerEvent,
    *,
    slot_level: Optional[int] = None,
    check_roll: Optional[int] = None,
) -> ReactionResolution:
    """Resolve ``kind`` for ``responder_id`` in response to ``trigger``.

    Validates eligibility again (the player may have lost the reaction
    between publishing and answering), then executes the effect. Never
    raises on a game-rule failure — returns a ``success=False`` result
    the caller narrates. Unexpected errors are caught and logged so a
    reaction bug can never crash a turn.
    """

    responder = state_manager.get_character(responder_id)
    if responder is None:
        return ReactionResolution(
            kind=kind, responder_id=responder_id, success=False,
            message=f"Personagem {responder_id!r} não encontrado.",
            reason="unknown_responder",
        )

    eligible = eligible_reactions(responder, trigger)
    if kind not in eligible:
        return ReactionResolution(
            kind=kind, responder_id=responder_id, success=False,
            message=(
                f"{responder.name} não pode usar {kind.value} como reação agora."
            ),
            reason="not_eligible",
            mechanical={"eligible": [e.value for e in eligible]},
        )

    rng = engine.rng
    try:
        if kind in _SPELL_REACTIONS:
            return _resolve_spell_reaction(
                state_manager, responder, kind, trigger,
                slot_level=slot_level, check_roll=check_roll, rng=rng,
            )
        if kind == ReactionKind.UNCANNY_DODGE:
            return _resolve_uncanny_dodge(state_manager, responder, trigger)
        if kind == ReactionKind.PARRY:
            return _resolve_parry(
                state_manager, responder, trigger, rng=rng,
            )
    except Exception as exc:  # noqa: BLE001 — never crash a turn
        logger.exception("apply_reaction failed for %s", kind)
        return ReactionResolution(
            kind=kind, responder_id=responder_id, success=False,
            message=f"Erro ao resolver reação {kind.value}: {exc}",
            reason="error",
        )

    return ReactionResolution(
        kind=kind, responder_id=responder_id, success=False,
        message=f"{kind.value} não tem resolver implementado.",
        reason="no_resolver",
    )


def _consume_reaction(ch: Character) -> None:
    ch.reaction_available = False
    # The pending_reaction is now answered; clear it so the next trigger
    # can publish fresh.
    ch.pending_reaction = None


def _resolve_spell_reaction(
    state_manager: "StateManager",
    caster: Character,
    kind: ReactionKind,
    trigger: TriggerEvent,
    *,
    slot_level: Optional[int],
    check_roll: Optional[int],
    rng: "random.Random",
) -> ReactionResolution:
    from auto_dm.engine.spellcasting import cast_spell

    meta = _SPELL_REACTIONS[kind]
    desired = slot_level if slot_level is not None else meta.min_slot

    # Honour the cast_spell pipeline for slot + known/prepared validation.
    # Spell effects (damage/heal/cancel) are applied by us below because
    # cast_spell only does bookkeeping (slot, concentration).
    result = cast_spell(caster, meta.spell_name, slot_level=desired, rng=rng)
    if not result.success:
        return ReactionResolution(
            kind=kind, responder_id=caster.id, success=False,
            message=f"{caster.name} não conseguiu lançar {meta.spell_name}: {result.error}",
            reason="cast_failed",
            mechanical={"spell": meta.spell_name, "error": result.error},
        )

    _consume_reaction(caster)
    used_slot = result.slot_level_used

    if kind == ReactionKind.SHIELD:
        return _finish_shield(caster, used_slot)
    if kind == ReactionKind.COUNTERSPELL:
        return _finish_counterspell(
            caster, trigger, used_slot, check_roll=check_roll,
        )
    if kind == ReactionKind.HELLISH_REBUKE:
        return _finish_hellish_rebuke(
            state_manager, caster, trigger, used_slot, rng=rng,
        )
    if kind == ReactionKind.HEALING_WORD:
        return _finish_healing_word(state_manager, caster, trigger, used_slot, rng=rng)

    return ReactionResolution(
        kind=kind, responder_id=caster.id, success=False,
        message=f"Sem pós-resolução para {kind.value}.",
        reason="no_finisher",
    )


def _finish_shield(caster: Character, used_slot: int) -> ReactionResolution:
    # +5 AC until the start of the caster's next turn + Magic Missile
    # immunity. ``pending_ac_bonus`` is already consulted by
    # ``combat.attack_roll``; ``shield_active`` flags MM immunity (and is
    # cleared alongside the AC bonus at the start of the caster's turn).
    caster.pending_ac_bonus += 5
    caster.shield_active = True
    return ReactionResolution(
        kind=ReactionKind.SHIELD, responder_id=caster.id, success=True,
        message=(
            f"{caster.name} lança Shield — CA +5 até o início do próximo "
            f"turno e imune a Magic Missile."
        ),
        consumed_reaction=True,
        consumed_slot_level=used_slot,
        mechanical={"ac_bonus": 5, "shield_active": True, "slot_level": used_slot},
    )


def _finish_counterspell(
    caster: Character,
    trigger: OnSeeingSpellCast,
    used_slot: int,
    *,
    check_roll: Optional[int],
) -> ReactionResolution:
    # PHB p. 227: at 3rd level, automatically interrupt spells of 3rd
    # level or lower. For higher-level spells, make an ability check
    # using the caster's spellcasting ability vs DC 10 + the spell's level.
    spell_level = trigger.level
    auto_success = spell_level <= 3
    spellcasting_ability = (
        caster.spellcasting.ability if caster.spellcasting else Ability.INT
    )
    if auto_success:
        cancelled = True
        check_detail = "automático (spell ≤ 3º)"
    else:
        dc = 10 + spell_level
        modifier = caster.abilities.modifier(spellcasting_ability)
        roll = check_roll if check_roll is not None else 10 + modifier  # deterministic default
        cancelled = roll >= dc
        check_detail = (
            f"teste de habilidade {roll} (= d20+{modifier}) vs DC {dc}"
        )
    return ReactionResolution(
        kind=ReactionKind.COUNTERSPELL, responder_id=caster.id, success=True,
        message=(
            f"{caster.name} lança Counterspell ({used_slot}º slot) contra "
            f"{trigger.spell_name} ({spell_level}º) — {check_detail} → "
            f"{'MAGIA ANULADA' if cancelled else 'magia resistida'}."
        ),
        consumed_reaction=True,
        consumed_slot_level=used_slot,
        spell_cancelled=cancelled,
        mechanical={
            "spell_level": spell_level, "dc": 10 + spell_level,
            "auto_success": auto_success, "check_roll": check_roll,
            "cancelled": cancelled, "slot_level": used_slot,
            "target_spell": trigger.spell_name,
        },
    )


def _finish_hellish_rebuke(
    state_manager: "StateManager",
    caster: Character,
    trigger: OnDamageTaken,
    used_slot: int,
    *,
    rng: "random.Random",
) -> ReactionResolution:
    # 2d10 fire, +1d10 per slot above 1st (PHB p. 284). Target is whoever
    # dealt the damage (the trigger's source).
    dice = 2 + max(0, used_slot - 1)
    damage = sum(rng.randint(1, 10) for _ in range(dice))
    target_hp: Optional[int] = None
    if trigger.source_id:
        target_hp = state_manager.set_hp(trigger.source_id, -damage)
    return ReactionResolution(
        kind=ReactionKind.HELLISH_REBUKE, responder_id=caster.id, success=True,
        message=(
            f"{caster.name} lança Hellish Rebuke ({used_slot}º slot) em "
            f"{trigger.source_id or 'alguém'} — {damage} de dano de fogo."
        ),
        consumed_reaction=True,
        consumed_slot_level=used_slot,
        rebuke_damage=damage,
        rebuke_target_hp=target_hp,
        mechanical={
            "dice": dice, "damage": damage, "slot_level": used_slot,
            "target_id": trigger.source_id, "target_hp": target_hp,
        },
    )


def _finish_healing_word(
    state_manager: "StateManager",
    caster: Character,
    trigger: OnAllyDown,
    used_slot: int,
    *,
    rng: "random.Random",
) -> ReactionResolution:
    # 1d4 + spellcasting modifier (PHB p. 250). Upcasting adds +1d8 per
    # slot above 1st; MVP keeps it to the base 1d4 + mod for clarity.
    modifier = (
        caster.abilities.modifier(caster.spellcasting.ability)
        if caster.spellcasting else 0
    )
    heal = rng.randint(1, 4) + modifier
    heal = max(1, heal)  # always heals at least 1
    ally_hp: Optional[int] = None
    if trigger.ally_id:
        ally_hp = state_manager.set_hp(trigger.ally_id, heal)
    return ReactionResolution(
        kind=ReactionKind.HEALING_WORD, responder_id=caster.id, success=True,
        message=(
            f"{caster.name} lança Healing Word ({used_slot}º slot) em "
            f"{trigger.ally_id or 'aliado'} — curou {heal} HP."
        ),
        consumed_reaction=True,
        consumed_slot_level=used_slot,
        healed_to=ally_hp,
        mechanical={
            "heal": heal, "slot_level": used_slot,
            "ally_id": trigger.ally_id, "ally_hp": ally_hp,
        },
    )


def _resolve_uncanny_dodge(
    state_manager: "StateManager",
    responder: Character,
    trigger: OnHitByAttack,
) -> ReactionResolution:
    # Halve the attack's damage. Because ``_handle_attack`` has already
    # applied the full damage by the time the trigger is published, the
    # halving is implemented as a refund of half the original damage.
    half = trigger.attack_damage // 2
    new_hp: Optional[int] = None
    if half > 0:
        new_hp = state_manager.set_hp(responder.id, half)
    _consume_reaction(responder)
    return ReactionResolution(
        kind=ReactionKind.UNCANNY_DODGE, responder_id=responder.id, success=True,
        message=(
            f"{responder.name} usa Uncanny Dodge — dano do ataque reduzido "
            f"à metade (devolve {half} HP)."
        ),
        consumed_reaction=True,
        damage_modified_to=trigger.attack_damage - half,
        mechanical={
            "original_damage": trigger.attack_damage,
            "refunded": half,
            "responder_hp": new_hp,
        },
    )


def _resolve_parry(
    state_manager: "StateManager",
    responder: Character,
    trigger: OnHitByAttack,
    *,
    rng: "random.Random",
) -> ReactionResolution:
    # Parry reduces damage by a superiority die (1d8) + proficiency
    # bonus. The plan caps the reduction at level 7 (die becomes 1d8 at
    # L3, 1d10 at L7, 1d12 at L15) — MVP uses a flat 1d8 + proficiency
    # for any Fighter L3+ and notes the L7 cap in tests.
    die = 8 if responder.level < 7 else 10
    prof = responder.proficiency_bonus
    reduction = rng.randint(1, die) + prof
    reduction = min(reduction, trigger.attack_damage)  # can't go negative
    new_hp: Optional[int] = None
    if reduction > 0:
        new_hp = state_manager.set_hp(responder.id, reduction)
    _consume_reaction(responder)
    return ReactionResolution(
        kind=ReactionKind.PARRY, responder_id=responder.id, success=True,
        message=(
            f"{responder.name} usa Parry — reduz {reduction} de dano "
            f"(1d{die}+{prof})."
        ),
        consumed_reaction=True,
        damage_modified_to=trigger.attack_damage - reduction,
        mechanical={
            "original_damage": trigger.attack_damage,
            "reduction": reduction,
            "die": die,
            "proficiency": prof,
            "responder_hp": new_hp,
        },
    )


# ============================================================================
# Publication — surface eligible reactions for the web layer
# ============================================================================


def publish_reaction_trigger(
    state_manager: "StateManager",
    trigger: TriggerEvent,
    *,
    fired_at: Optional[int] = None,
    candidates: Optional[list[str]] = None,
    engine: "Optional[CombatEngine]" = None,
) -> list[str]:
    """Find party members eligible to react and surface the trigger.

    Iterates over party ``Character``\\s (optionally restricted to
    ``candidates``, player-first) and, for the first one with at least one
    eligible reaction, either:

    * **Player** — records the trigger on ``pending_reaction`` via
      :func:`build_pending_reaction` so the web layer (Phase 41c modal)
      can prompt the human and call back.
    * **Companion** (``engine`` provided) — auto-resolves the reaction
      immediately via :func:`auto_resolve_companion_reaction`
      (Phase 41c heuristic). The companion never gets a stashed
      ``pending_reaction``; only the player is prompted.

    Returns the ids of the characters that reacted (one in the MVP — the
    first eligible responder). ``fired_at`` should be a real epoch from
    the web layer; without it the player-path TTL is unrecoverable and
    publication is a no-op (see ``build_pending_reaction``).

    Without ``engine`` (e.g. unit tests, non-combat callers) companions
    are skipped rather than prompted — the human only ever answers for
    their own character.
    """
    from auto_dm.engine.companion_reactions import auto_resolve_companion_reaction

    published: list[str] = []
    party = state_manager.state.party
    ordered = [
        c for c in party
        if (candidates is None or c.id in candidates)
    ]
    # Player first so the human is prompted before companions.
    ordered.sort(key=lambda c: (not c.is_player, c.id))
    for ch in ordered:
        eligible = eligible_reactions(ch, trigger)
        if not eligible:
            continue

        # Companion: auto-resolve and move on (no stashed prompt).
        if not ch.is_player:
            if engine is None:
                # No engine → can't resolve; skip companions entirely so
                # nothing is left dangling for the web modal (which only
                # handles the player).
                continue
            auto_resolve_companion_reaction(engine, state_manager, ch, trigger, eligible)
            published.append(ch.id)
            break  # one responder per trigger (MVP)

        # Player: stash and let the web modal prompt. Don't clobber an
        # already-open prompt from an earlier trigger this turn — the
        # player can only answer one modal at a time.
        if ch.pending_reaction is not None:
            continue
        pending = build_pending_reaction(trigger, eligible, fired_at=fired_at)
        if pending is None:
            continue
        ch.pending_reaction = pending
        published.append(ch.id)
        break  # one prompt per trigger (MVP)
    return published


__all__ = [
    "ReactionResolution",
    "eligible_reactions",
    "apply_reaction",
    "publish_reaction_trigger",
]

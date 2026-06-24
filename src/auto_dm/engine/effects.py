"""Apply PHB poisons, traps, and diseases to creatures.

Poisons, traps, and diseases follow the same pattern:

1. The creature is exposed (touch, breath, weapon strike, etc.).
2. The creature makes a save (Constitution usually) against a DC.
3. On failure: damage (often half on success) and an applied condition
   (poisoned, blinded, etc.).
4. For ongoing effects: a save at the end of each turn may end the
   effect; damage can recur each round.

This module is pure — it returns :class:`EffectResult` objects. The
caller applies HP changes through :class:`StateManager`.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass

from auto_dm.engine.combat import saving_throw
from auto_dm.engine.dice import roll_dice
from auto_dm.phb.models import PHBDisease, PHBPoison, PHBTrap
from auto_dm.state.models import Ability, ActiveEffect, Character, Condition, NPC


_DURATION_RE = re.compile(r"(\d+d\d+|\d+)\s+(round|minute|hour|day|turn)s?", re.IGNORECASE)


# ============================================================================
# Result types
# ============================================================================


@dataclass
class EffectResult:
    """Outcome of a single poison/trap/disease application."""

    source: str
    target_id: str
    save_made: bool
    damage_dealt: int
    damage_type: str
    conditions_applied: list[str]
    effect_attached: bool  # True if an ActiveEffect was attached for ticking
    notes: str = ""


# ============================================================================
# Duration parsing
# ============================================================================


def parse_duration_rounds(text: str, *, default: int = 10) -> int:
    """Convert a PHB duration phrase to an approximate round count.

    PHB rounds = 6 seconds of in-game time. We approximate:
    - 1 round   = 1
    - 1 minute  = 10 rounds
    - 10 minutes = 100 rounds
    - 1 hour    = 600 rounds
    - 24 hours  = 14400 rounds

    For "4d6 hours" we use the average (3.5 * 600 = 2100 rounds).
    """
    m = _DURATION_RE.search(text)
    if not m:
        return default
    n_str, unit = m.group(1), m.group(2).lower()
    if "d" in n_str:
        # Roll the dice — but since we want a deterministic default for
        # engine state, use the average.
        n_dice, n_faces = n_str.split("d")
        n = (int(n_dice) * (int(n_faces) + 1)) // 2
    else:
        n = int(n_str)
    multiplier = {
        "round": 1, "turn": 1, "minute": 10,
        "hour": 600, "day": 14400,
    }.get(unit, 1)
    return max(1, n * multiplier)


# ============================================================================
# Save helper
# ============================================================================


def _ability_from_name(name: str) -> Ability:
    """Map lowercase ability name to enum. Defaults to CON."""
    try:
        return Ability(name.lower())
    except ValueError:
        return Ability.CON


def _make_save(
    target: Character | NPC,
    save_ability: Ability,
    dc: int,
    *,
    rng: random.Random | None = None,
) -> tuple[bool, int]:
    """Roll a save, return ``(success, natural_roll)``.

    Uses :func:`saving_throw` so condition-driven modifiers apply.
    """
    res = saving_throw(target, save_ability, dc, rng=rng)
    return res.is_success, res.roll


# ============================================================================
# Poison
# ============================================================================


def apply_poison(
    target: Character | NPC,
    poison: PHBPoison,
    *,
    rng: random.Random | None = None,
) -> EffectResult:
    """Expose a creature to a poison. Rolls the save and applies effects.

    On failed save: full damage + condition(s).
    On successful save: half damage (rounded down), no condition, no
    lingering effect (PHB "successful save" wording).
    """
    rng = rng or random.Random()
    save_ability = _ability_from_name(poison.save_ability)
    success, _ = _make_save(target, save_ability, poison.save_dc, rng=rng)

    if success:
        damage = _half_damage(poison.damage_dice, rng=rng)
        return EffectResult(
            source=poison.name,
            target_id=target.id,
            save_made=True,
            damage_dealt=damage,
            damage_type=poison.damage_type,
            conditions_applied=[],
            effect_attached=False,
            notes=f"Salvo contra {poison.name}; dano reduzido à metade.",
        )

    # Failed save: full damage + apply conditions
    damage = _full_damage(poison.damage_dice, rng=rng)
    conditions_applied: list[Condition] = []
    for cond_name in poison.applies_condition:
        try:
            c = Condition(cond_name)
            if c not in target.conditions:
                target.conditions.append(c)
                conditions_applied.append(c)
        except ValueError:
            pass

    # Attach an ActiveEffect for ticks / save-to-end.
    rounds = parse_duration_rounds(poison.duration, default=1)
    effect = ActiveEffect(
        source=poison.name,
        effect_type="poison",
        duration_rounds=rounds,
        save_dc=poison.save_dc,
        save_ability=save_ability,
        damage_dice=poison.damage_dice,
        damage_type=poison.damage_type,
        applies_condition=conditions_applied,
        notes=poison.notes,
    )
    target.active_effects.append(effect)

    return EffectResult(
        source=poison.name,
        target_id=target.id,
        save_made=False,
        damage_dealt=damage,
        damage_type=poison.damage_type,
        conditions_applied=[c.value for c in conditions_applied],
        effect_attached=True,
        notes=f"Falhou contra {poison.name}; tomou {damage} de dano {poison.damage_type}.",
    )


# ============================================================================
# Trap
# ============================================================================


def trigger_trap(
    target: Character | NPC,
    trap: PHBTrap,
    *,
    rng: random.Random | None = None,
) -> EffectResult:
    """A creature triggers a trap. Rolls the save and applies damage.

    Traps are usually one-shot (Collapsing Roof, Pit). For ongoing traps
    (e.g. poison gas cloud), the caller can attach an ActiveEffect
    separately.
    """
    rng = rng or random.Random()
    save_ability = _ability_from_name(trap.save_ability)
    success, _ = _make_save(target, save_ability, trap.save_dc, rng=rng)

    if success:
        damage = _half_damage(trap.damage_dice, rng=rng)
        return EffectResult(
            source=trap.name,
            target_id=target.id,
            save_made=True,
            damage_dealt=damage,
            damage_type=trap.damage_type,
            conditions_applied=[],
            effect_attached=False,
            notes=f"Salvo contra armadilha ({trap.name}); dano à metade.",
        )

    damage = _full_damage(trap.damage_dice, rng=rng)
    return EffectResult(
        source=trap.name,
        target_id=target.id,
        save_made=False,
        damage_dealt=damage,
        damage_type=trap.damage_type,
        conditions_applied=[],
        effect_attached=False,
        notes=f"Armadilha {trap.name} atingida; {damage} de dano {trap.damage_type}.",
    )


# ============================================================================
# Disease
# ============================================================================


def apply_disease(
    target: Character | NPC,
    disease: PHBDisease,
    *,
    rng: random.Random | None = None,
) -> EffectResult:
    """Contract a disease. PHB uses incubation, then recurring saves.

    For MVP we simplify: contract immediately on exposure, attach an
    ActiveEffect that ticks on long rests (when the disease's recurring
    save happens in the PHB).
    """
    rng = rng or random.Random()
    save_ability = _ability_from_name(disease.save_ability)
    success, _ = _make_save(target, save_ability, disease.save_dc, rng=rng)

    if success:
        return EffectResult(
            source=disease.name,
            target_id=target.id,
            save_made=True,
            damage_dealt=0,
            damage_type="",
            conditions_applied=[],
            effect_attached=False,
            notes=f"Imune a {disease.name} neste save.",
        )

    # Attach the disease as an ActiveEffect — long rests will tick the save.
    rounds = parse_duration_rounds("24 hours", default=14400)
    effect = ActiveEffect(
        source=disease.name,
        effect_type="disease",
        duration_rounds=rounds,
        save_dc=disease.save_dc,
        save_ability=save_ability,
        damage_dice="",
        damage_type="",
        applies_condition=[],
        notes=disease.description,
    )
    target.active_effects.append(effect)

    return EffectResult(
        source=disease.name,
        target_id=target.id,
        save_made=False,
        damage_dealt=0,
        damage_type="",
        conditions_applied=[],
        effect_attached=True,
        notes=f"Contraiu {disease.name}.",
    )


# ============================================================================
# Tick ongoing effects
# ============================================================================


def tick_effects(
    target: Character | NPC,
    *,
    rng: random.Random | None = None,
) -> list[EffectResult]:
    """Apply one round of ticking for all of ``target``'s ActiveEffects.

    For each effect:
    - Decrement duration; if 0, the effect is removed.
    - Roll recurring save (if any) — on success, effect ends early.
    - Roll damage if the effect has damage_dice.
    - Return one EffectResult per effect.
    """
    rng = rng or random.Random()
    results: list[EffectResult] = []
    surviving: list[ActiveEffect] = []
    for effect in target.active_effects:
        result = _tick_one(target, effect, rng=rng)
        if result is not None:
            results.append(result)
        # Effect survives unless save-ended or duration hit 0
        if effect in target.active_effects:
            surviving.append(effect)
    target.active_effects = surviving
    return results


def _tick_one(
    target: Character | NPC,
    effect: ActiveEffect,
    *,
    rng: random.Random,
) -> EffectResult | None:
    # Decrement duration
    if effect.duration_rounds > 0:
        effect.duration_rounds -= 1

    # Recurring save (only when duration hits 0 OR always? PHB says
    # "at end of each turn". We attempt every round.)
    res = saving_throw(target, effect.save_ability, effect.save_dc, rng=rng)
    if res.is_success:
        # Effect ends on successful save
        if effect in target.active_effects:
            target.active_effects.remove(effect)
        return EffectResult(
            source=effect.source,
            target_id=target.id,
            save_made=True,
            damage_dealt=0,
            damage_type="",
            conditions_applied=[],
            effect_attached=False,
            notes=f"Encerrou {effect.source} com sucesso no save.",
        )

    # Failed save: apply damage and conditions
    damage = _full_damage(effect.damage_dice, rng=rng)
    for c in effect.applies_condition:
        if c not in target.conditions:
            target.conditions.append(c)

    # Drop expired effects
    if effect.duration_rounds == 0:
        if effect in target.active_effects:
            target.active_effects.remove(effect)

    return EffectResult(
        source=effect.source,
        target_id=target.id,
        save_made=False,
        damage_dealt=damage,
        damage_type=effect.damage_type,
        conditions_applied=[c.value for c in effect.applies_condition],
        effect_attached=effect in target.active_effects,
        notes=f"{effect.source} tickou: {damage} dano {effect.damage_type}.",
    )


# ============================================================================
# Damage helpers
# ============================================================================


def _full_damage(dice: str, *, rng: random.Random) -> int:
    if not dice:
        return 0
    try:
        return roll_dice(dice, rng=rng).total
    except Exception:
        return 0


def _half_damage(dice: str, *, rng: random.Random) -> int:
    return _full_damage(dice, rng=rng) // 2

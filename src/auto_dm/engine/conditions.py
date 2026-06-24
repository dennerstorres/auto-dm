"""Mechanical effects of PHB conditions.

PHB condition rules are scattered across the Conditions appendix. This
module centralizes them so the engine doesn't have to reinvent the
wheels. The rule text lives in ``data/phb/Gamemastering/Conditions.md``
(loaded by :func:`auto_dm.phb.load_conditions`) — keep the two in sync.

Two kinds of effects are encoded:

- **Roll modifiers** — used by :mod:`auto_dm.engine.combat` to apply
  advantage / disadvantage / auto-fail / auto-crit.
- **Action gating** — used by :class:`auto_dm.engine.combat_engine.CombatEngine`
  to refuse actions that the condition forbids.

The helpers take either a Character or an NPC (both have the same
fields we care about: ``conditions``, ``exhaustion_level``, ``resistances``,
``vulnerabilities``, ``immunities``).
"""
from __future__ import annotations

from typing import Iterable, Protocol

from auto_dm.state.models import Ability, Character, Condition, NPC


# Anything that has conditions/exhaustion/resistances — Character and NPC.
class _HasStatus(Protocol):
    conditions: list[Condition]
    exhaustion_level: int
    resistances: list[str]
    vulnerabilities: list[str]
    immunities: list[str]


# ============================================================================
# Action gating
# ============================================================================


def can_take_actions(creature: _HasStatus) -> bool:
    """PHB: incapacitated, paralyzed, petrified, stunned, and unconscious
    creatures can't take actions or reactions."""
    blocking = {
        Condition.INCAPACITATED,
        Condition.PARALYZED,
        Condition.PETRIFIED,
        Condition.STUNNED,
        Condition.UNCONSCIOUS,
    }
    return not (set(creature.conditions) & blocking)


def movement_speed_zero(creature: _HasStatus) -> bool:
    """PHB: grappled, paralyzed, petrified, restrained, stunned, and
    unconscious creatures have speed 0."""
    zeroing = {
        Condition.GRAPPLED,
        Condition.PARALYZED,
        Condition.PETRIFIED,
        Condition.RESTRAINED,
        Condition.STUNNED,
        Condition.UNCONSCIOUS,
    }
    return bool(set(creature.conditions) & zeroing)


# ============================================================================
# Attack roll modifiers — PHB advantage/disadvantage rules
# ============================================================================


def attacker_advantage(attacker: _HasStatus) -> bool:
    """Attacker has advantage on attack rolls under these conditions."""
    return (
        Condition.INVISIBLE in attacker.conditions
        or Condition.HIDDEN in attacker.conditions
    )


def attacker_disadvantage(attacker: _HasStatus) -> bool:
    """Attacker has disadvantage on attack rolls under these conditions."""
    return (
        # Blinded: disadvantage on attack rolls
        Condition.BLINDED in attacker.conditions
        # Poisoned: disadvantage on attack rolls
        or Condition.POISONED in attacker.conditions
        # Exhaustion level 3+: disadvantage on attack rolls
        or exhaustion_disadvantage_attack(attacker)
    )


def target_advantage(target: _HasStatus) -> bool:
    """Attacks against the target have advantage under these conditions."""
    return (
        # Blinded: attacks against have advantage
        Condition.BLINDED in target.conditions
        # Paralyzed: attacks within 5 ft have advantage (caller adds; we
        # expose the flag regardless — it's "yes, advantage if in melee")
        or Condition.PARALYZED in target.conditions
        # Petrified: attacks against have advantage
        or Condition.PETRIFIED in target.conditions
        # Stunned: attacks against have advantage
        or Condition.STUNNED in target.conditions
        # Restrained: attacks against have advantage
        or Condition.RESTRAINED in target.conditions
        # Unconscious: attacks within 5 ft have advantage (caller checks
        # range; we return True and let caller decide)
        or Condition.UNCONSCIOUS in target.conditions
        # Prone: attacks against have advantage when attacker is within 5 ft
        or Condition.PRONE in target.conditions
    )


def target_disadvantage(target: _HasStatus) -> bool:
    """Attacks against the target have disadvantage under these conditions."""
    # Invisible: attacks against have disadvantage
    return Condition.INVISIBLE in target.conditions


def apply_attack_modifiers(
    attacker: _HasStatus,
    target: _HasStatus,
    *,
    is_ranged_attack: bool = False,
    is_melee_within_5ft: bool = True,
) -> tuple[bool, bool]:
    """Combine all condition-driven adv/disadvantage flags.

    Returns ``(advantage, disadvantage)``. Multiple sources stack but
    per PHB any advantage + any disadvantage cancels to a straight roll.
    """
    adv = False
    dis = False

    # Attacker conditions
    if attacker_advantage(attacker):
        adv = True
    if attacker_disadvantage(attacker):
        dis = True

    # Target conditions
    if target_advantage(target):
        # Paralyzed / Unconscious only grant advantage when attacker is
        # within 5 ft — caller must signal this.
        needs_melee = (
            Condition.PARALYZED in target.conditions
            or Condition.UNCONSCIOUS in target.conditions
            or Condition.PRONE in target.conditions
        )
        if needs_melee and is_melee_within_5ft:
            adv = True
        elif not needs_melee:
            adv = True
    if target_disadvantage(target):
        dis = True

    # Prone attacker using ranged: disadvantage (PHB)
    if Condition.PRONE in attacker.conditions and is_ranged_attack:
        dis = True
    # Prone target being shot at ranged: disadvantage to hit (the prone
    # target is hard to miss at range, so attack has ADVANTAGE instead).
    # The PHB rule: ranged attack against prone target has disadvantage
    # if the normal range is a factor. We model the simple form: ranged
    # against prone = disadvantage. The PHB "disadvantage from range is
    # cancelled by prone's advantage" is handled by the caller via
    # net adv/dis.
    if (
        Condition.PRONE in target.conditions
        and is_ranged_attack
        and not is_melee_within_5ft
    ):
        # Ranged against prone from >5ft: disadvantage to hit
        # (the prone target gives advantage, but the ranged attack
        # imposes disadvantage, they cancel — we just set both)
        adv = True
        dis = True

    return adv, dis


def attack_auto_crit(target: _HasStatus, *, is_melee_within_5ft: bool = True) -> bool:
    """PHB: attack is automatically a crit if the target is paralyzed or
    unconscious AND the attacker is within 5 feet."""
    if not is_melee_within_5ft:
        return False
    return (
        Condition.PARALYZED in target.conditions
        or Condition.UNCONSCIOUS in target.conditions
    )


# ============================================================================
# Saving throw modifiers
# ============================================================================


def auto_fail_str_or_dex_save(creature: _HasStatus) -> bool:
    """PHB: paralyzed, petrified, restrained, stunned, and unconscious
    creatures automatically fail STR and DEX saves."""
    return bool(
        set(creature.conditions)
        & {Condition.PARALYZED, Condition.PETRIFIED, Condition.RESTRAINED,
           Condition.STUNNED, Condition.UNCONSCIOUS}
    )


def save_advantage(creature: _HasStatus, ability: Ability) -> bool:
    """Condition-based save advantage."""
    # Magic can't put elves with Fey Ancestry to sleep — but they don't
    # get a blanket save advantage. No PHB condition grants save
    # advantage on its own (that's class/race territory).
    return False


def save_disadvantage(creature: _HasStatus, ability: Ability) -> bool:
    """Condition-based save disadvantage."""
    # Poisoned: disadvantage on attack rolls and ability checks (not saves)
    # Restrained: disadvantage on DEX saves
    if Condition.RESTRAINED in creature.conditions and ability == Ability.DEX:
        return True
    # Frightened: disadvantage on ability checks while source in sight
    # (not saves — leaving to LLM narration)
    return False


def apply_save_modifiers(
    creature: _HasStatus, ability: Ability,
) -> tuple[bool, bool, bool]:
    """Return ``(advantage, disadvantage, auto_fail)`` for a save."""
    adv = save_advantage(creature, ability)
    dis = save_disadvantage(creature, ability)
    fail = auto_fail_str_or_dex_save(creature) and ability in (Ability.STR, Ability.DEX)
    return adv, dis, fail


# ============================================================================
# Damage modifiers — resistance / vulnerability / immunity from conditions
# ============================================================================


def damage_multiplier(
    creature: _HasStatus, damage_type: str,
) -> float:
    """Return 0.0 / 0.5 / 1.0 / 2.0 based on resistance/vulnerability/immunity.

    Order: immunity > resistance > vulnerability > 1.0 (so an immune
    creature takes nothing even if also "vulnerable").
    """
    dt = damage_type.lower()
    if dt in {d.lower() for d in creature.immunities}:
        return 0.0
    is_resistant = dt in {d.lower() for d in creature.resistances}
    is_vulnerable = dt in {d.lower() for d in creature.vulnerabilities}
    if is_resistant and is_vulnerable:
        # Per DMG: resistance + vulnerability on the same damage = 1.0
        # (they cancel)
        return 1.0
    if is_resistant:
        return 0.5
    if is_vulnerable:
        return 2.0
    return 1.0


# ============================================================================
# Exhaustion — special PHB condition with 6 levels
# ============================================================================


# PHB p. 251 — each level adds one of these effects cumulatively.
# (level -> description, used by narrator and tests)
EXHAUSTION_EFFECTS: dict[int, str] = {
    1: "Disadvantage on ability checks",
    2: "Speed halved",
    3: "Disadvantage on attack rolls and saving throws",
    4: "Hit point maximum halved",
    5: "Speed reduced to 0",
    6: "Death",
}


def _exhaustion_set(creature: _HasStatus) -> set[Condition]:
    """Exhaustion isn't a Condition enum value — it's a level. Return an
    empty set here; callers should consult exhaustion_level directly."""
    return set()


def exhaustion_effect(creature: _HasStatus, level: int) -> str | None:
    """Return the text of the effect gained at ``level``, or None."""
    return EXHAUSTION_EFFECTS.get(level)


def exhaustion_applies(creature: _HasStatus, level: int) -> bool:
    """True if the creature has at least this level of exhaustion."""
    return creature.exhaustion_level >= level


def exhaustion_disadvantage_attack(creature: _HasStatus) -> bool:
    """Exhaustion level 3+ -> disadvantage on attack rolls and saves."""
    return creature.exhaustion_level >= 3


def exhaustion_halved_speed(creature: _HasStatus) -> bool:
    """Exhaustion level 2 -> speed halved."""
    return creature.exhaustion_level >= 2


def exhaustion_zero_speed(creature: _HasStatus) -> bool:
    """Exhaustion level 5 -> speed 0."""
    return creature.exhaustion_level >= 5


def exhaustion_halved_hp_max(creature: _HasStatus) -> bool:
    """Exhaustion level 4 -> HP max halved."""
    return creature.exhaustion_level >= 4


def increase_exhaustion(creature: _HasStatus, by: int = 1) -> int:
    """Bump exhaustion level by ``by``. Returns the new level. Capped at 6
    (death)."""
    creature.exhaustion_level = min(6, creature.exhaustion_level + by)
    return creature.exhaustion_level


def decrease_exhaustion(creature: _HasStatus, by: int = 1) -> int:
    """Lower exhaustion level by ``by`` (long rest removes 1, greater
    restoration removes all). Floored at 0."""
    creature.exhaustion_level = max(0, creature.exhaustion_level - by)
    return creature.exhaustion_level


# ============================================================================
# Visibility — blinded creatures effectively can't see, charmed creatures
# treat charmer as friendly, etc. Most of these are narrative; the engine
# only needs them when they mechanically interact (e.g. advantage on attacks).
# ============================================================================


def is_incapacitated(creature: Character | NPC) -> bool:
    return not can_take_actions(creature)


def list_active_conditions(creature: _HasStatus) -> list[str]:
    """For display: the names of currently active conditions plus the
    exhaustion level (if any)."""
    out = [c.value for c in creature.conditions]
    if creature.exhaustion_level > 0:
        out.append(f"exhaustion ({creature.exhaustion_level})")
    return out


def applies_to(creature: _HasStatus, conditions: Iterable[Condition]) -> bool:
    """True if creature has any of the listed conditions."""
    return bool(set(creature.conditions) & set(conditions))

"""Combat mechanics: attack rolls, damage, initiative, saving throws, death saves.

This module is **pure**: it returns result objects but does NOT mutate state.
The turn manager takes the result, decides what to narrate, and calls
StateManager to apply HP/condition changes. This separation keeps the
mechanical layer testable in isolation.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from auto_dm.engine.conditions import (
    apply_attack_modifiers,
    apply_save_modifiers,
    attack_auto_crit,
)
from auto_dm.engine.dice import roll_d20, roll_dice
from auto_dm.state.models import Ability, Character, NPC


# ============================================================================
# Result types
# ============================================================================


@dataclass
class AttackResult:
    """Outcome of a single attack roll."""

    attacker_id: str
    target_id: str
    attack_roll: int  # the d20 face
    attack_modifier: int
    attack_total: int
    target_ac: int
    is_hit: bool
    is_crit: bool  # natural 20
    is_fumble: bool  # natural 1
    weapon: str
    advantage: bool = False
    disadvantage: bool = False

    def __str__(self) -> str:
        if self.is_crit:
            outcome = "CRIT!"
        elif self.is_fumble:
            outcome = "FUMBLE!"
        elif self.is_hit:
            outcome = "HIT"
        else:
            outcome = "MISS"
        return (
            f"{self.attacker_id} attacks {self.target_id} with {self.weapon}: "
            f"d20({self.attack_roll}) + {self.attack_modifier} = {self.attack_total} "
            f"vs AC {self.target_ac} -> {outcome}"
        )


@dataclass
class DamageRoll:
    """Damage dealt by a successful attack (returned by damage_roll)."""

    total: int
    damage_type: str
    weapon: str
    is_crit: bool
    individual_rolls: list[int]
    modifier: int

    def __str__(self) -> str:
        rolls = "+".join(str(r) for r in self.individual_rolls)
        sign = "+" if self.modifier >= 0 else "-"
        return (
            f"{self.weapon} damage: [{rolls}] {sign}{abs(self.modifier)} "
            f"= {self.total} {self.damage_type}"
        )


@dataclass
class SaveResult:
    """Outcome of a saving throw."""

    creature_id: str
    ability: Ability
    dc: int
    roll: int
    modifier: int
    total: int
    is_success: bool
    is_crit: bool = False  # nat 20
    is_fumble: bool = False  # nat 1

    def __str__(self) -> str:
        if self.is_crit:
            outcome = "CRIT (auto-success)"
        elif self.is_fumble:
            outcome = "FUMBLE (auto-fail)"
        elif self.is_success:
            outcome = "SUCCESS"
        else:
            outcome = "FAIL"
        return (
            f"{self.creature_id} {self.ability.value} save: "
            f"d20({self.roll}) + {self.modifier} = {self.total} "
            f"vs DC {self.dc} -> {outcome}"
        )


@dataclass
class InitiativeResult:
    """Initiative order sorted by roll (highest acts first)."""

    entries: list[tuple[str, int]]  # (creature_id, total_initiative)

    def order(self) -> list[str]:
        """Return creature IDs in initiative order."""
        return [e[0] for e in self.entries]


# ============================================================================
# Attack roll
# ============================================================================


def attack_roll(
    attacker: Character | NPC,
    target: Character | NPC,
    *,
    advantage: bool = False,
    disadvantage: bool = False,
    proficient: bool = True,
    is_ranged: bool = False,
    is_melee_within_5ft: bool = True,
    rng: random.Random | None = None,
) -> AttackResult:
    """Roll an attack against a target.

    The attacker's main_hand weapon determines damage dice and ability.
    The roll is: d20 + ability_mod + (proficiency_bonus if proficient).

    Conditions on attacker and target contribute their own adv/disadvantage
    via :func:`apply_attack_modifiers`. The caller may pass an explicit
    advantage / disadvantage — these OR with the condition-derived flags
    (PHB: any advantage + any disadvantage = straight roll).

    Natural 20 -> crit (auto hit, double damage dice downstream).
    Natural 1 -> fumble (auto miss).
    Paralyzed / Unconscious target within 5 ft -> auto-crit (PHB).
    """
    rng = rng or random.Random()

    weapon_item = attacker.equipped.main_hand
    if weapon_item is None or weapon_item.weapon is None:
        # Unarmed strike: 1 bludgeoning, uses STR
        weapon_name = "Unarmed Strike"
        finesse = False
        ability = Ability.STR
    else:
        weapon_name = weapon_item.name
        wp = weapon_item.weapon
        finesse = wp.finesse
        # For MVP, finesse weapons use DEX. (PHB also allows thrown/ranged
        # to use DEX — that can be added in Phase 7.)
        ability = Ability.DEX if finesse else Ability.STR

    # Build modifier
    modifier = attacker.abilities.modifier(ability)
    if proficient and isinstance(attacker, Character):
        modifier += attacker.proficiency_bonus
    # Fighting Style (Archery: +2 with ranged weapons)
    if isinstance(attacker, Character) and weapon_item is not None:
        from auto_dm.engine.fighting_style import attack_bonus
        modifier += attack_bonus(attacker, weapon_item)
    # Magic weapon bonus (Phase 25d): +1/+2/+3 to attack rolls.
    if weapon_item is not None and weapon_item.magic_bonus:
        modifier += weapon_item.magic_bonus

    # Condition-driven adv/disadvantage (PHB)
    cond_adv, cond_dis = apply_attack_modifiers(
        attacker, target,
        is_ranged_attack=is_ranged,
        is_melee_within_5ft=is_melee_within_5ft,
    )
    effective_advantage = advantage or cond_adv
    effective_disadvantage = disadvantage or cond_dis
    # Pending advantage stacks from Inspiration / Help.
    if isinstance(attacker, Character) and attacker.pending_advantage > 0:
        attacker.pending_advantage -= 1
        effective_advantage = True
    # PHB: if both, they cancel
    if effective_advantage and effective_disadvantage:
        effective_advantage = False
        effective_disadvantage = False

    # Cover bonus on target (DMG p. 250)
    from auto_dm.engine.cover import cover_ac_bonus
    cover_bonus = cover_ac_bonus(target.cover)
    # Pending AC bonus from reactions like Shield
    shield_bonus = getattr(target, "pending_ac_bonus", 0)
    # Magic armor/shield bonus (Phase 25d): +1/+2/+3 from equipped
    # armor or shield's magic_bonus. Stored at character creation OR
    # set on items directly.
    magic_armor_bonus = 0
    target_equipped = getattr(target, "equipped", None)
    if target_equipped is not None:
        for slot_name in ("armor", "off_hand", "main_hand"):
            slot = getattr(target_equipped, slot_name, None)
            if slot is not None and getattr(slot, "magic_bonus", None):
                magic_armor_bonus += slot.magic_bonus
    effective_ac = (
        target.armor_class + cover_bonus + shield_bonus + magic_armor_bonus
    )

    # Roll
    result = roll_d20(
        advantage=effective_advantage,
        disadvantage=effective_disadvantage,
        modifier=modifier,
        rng=rng,
    )
    natural_roll = result.kept[0]

    is_crit = (natural_roll == 20) or attack_auto_crit(
        target, is_melee_within_5ft=is_melee_within_5ft,
    )
    is_fumble = natural_roll == 1
    is_hit = is_crit or (not is_fumble and result.total >= effective_ac)

    return AttackResult(
        attacker_id=attacker.id,
        target_id=target.id,
        attack_roll=natural_roll,
        attack_modifier=modifier,
        attack_total=result.total,
        target_ac=effective_ac,
        is_hit=is_hit,
        is_crit=is_crit,
        is_fumble=is_fumble,
        weapon=weapon_name,
        advantage=effective_advantage,
        disadvantage=effective_disadvantage,
    )


# ============================================================================
# Damage roll
# ============================================================================


def damage_roll(
    attacker: Character | NPC,
    *,
    is_crit: bool = False,
    versatile: bool = False,
    rng: random.Random | None = None,
) -> DamageRoll:
    """Roll damage for an attack.

    On crit, damage dice are doubled (e.g. 1d8 -> 2d8, 2d6 -> 4d6).
    STR/DEX modifier is added, floored at 0 (no negative damage).

    Returns a DamageRoll (not the total alone) so the caller can display
    the individual dice and the breakdown to the player.
    """
    rng = rng or random.Random()

    weapon_item = attacker.equipped.main_hand
    if weapon_item is None or weapon_item.weapon is None:
        # Unarmed strike: 1 bludgeoning + STR mod (no dice, PHB convention)
        modifier = max(0, attacker.abilities.modifier(Ability.STR))
        return DamageRoll(
            total=1 + modifier,
            damage_type="bludgeoning",
            weapon="Unarmed Strike",
            is_crit=is_crit,  # no crit on unarmed; pass through for consistency
            individual_rolls=[1],
            modifier=modifier,
        )
    else:
        weapon_name = weapon_item.name
        wp = weapon_item.weapon
        # Versatile: 2H grip uses a bigger damage die
        if versatile and wp.versatile_dice:
            damage_dice = wp.versatile_dice
        else:
            damage_dice = wp.damage_dice
        damage_type = wp.damage_type
        finesse = wp.finesse

    ability = Ability.DEX if finesse else Ability.STR

    # Crit: double the number of dice, plus brutal critical extra dice
    if is_crit and "d" in damage_dice:
        n_str, s_str = damage_dice.split("d")
        n = int(n_str)
        s = int(s_str)
        # Brutal Critical (Barbarian L9+): +1/+2/+3 dice on crit (PHB)
        brutal = 0
        if isinstance(attacker, Character):
            from auto_dm.engine.defenses import brutal_critical_extra_dice
            if attacker.class_.lower() == "barbarian":
                brutal = brutal_critical_extra_dice(attacker.level)
        # PHB crit: 2 × base dice + brutal extra
        damage_dice = f"{2 * n + brutal}d{s}"

    # Roll
    roll = roll_dice(damage_dice, rng=rng)
    modifier = max(0, attacker.abilities.modifier(ability))  # min 0

    # Rage bonus: only on STR melee attacks (no finesse) while raging.
    from auto_dm.engine.rage import is_raging, rage_damage_bonus
    if ability is Ability.STR and is_raging(attacker) and not finesse:
        if isinstance(attacker, Character):
            modifier += rage_damage_bonus(attacker.level)

    # Fighting Style (Dueling: +2 with one-handed melee, no off-hand weapon)
    if isinstance(attacker, Character):
        from auto_dm.engine.fighting_style import damage_bonus
        modifier += damage_bonus(attacker, weapon_item)
    # Magic weapon bonus (Phase 25d): +1/+2/+3 to damage rolls.
    if weapon_item is not None and weapon_item.magic_bonus:
        modifier += weapon_item.magic_bonus

    # Fighting Style (Great Weapon Fighting: reroll 1/2 on dmg dice for 2H melee)
    if isinstance(attacker, Character) and attacker.fighting_style == "great_weapon_fighting":
        from auto_dm.engine.fighting_style import apply_gwf
        # Only for two-handed or 2H-grip versatile melee
        wp = weapon_item.weapon if weapon_item else None
        if wp and not wp.range_normal and (wp.two_handed or versatile):
            rerolled = apply_gwf(roll.rolls, rng=rng)
            roll.total = sum(rerolled)
            roll.rolls = rerolled

    total = roll.total + modifier
    return DamageRoll(
        total=total,
        damage_type=damage_type,
        weapon=weapon_name,
        is_crit=is_crit,
        individual_rolls=roll.rolls,
        modifier=modifier,
    )


# ============================================================================
# Initiative
# ============================================================================


def roll_initiative(
    creatures: list[Character | NPC],
    *,
    rng: random.Random | None = None,
) -> InitiativeResult:
    """Roll initiative for all creatures: d20 + DEX modifier.

    Tiebreakers (in order):
        1. Higher total initiative
        2. Higher DEX score
        3. Alphabetical ID (stable)
    """
    rng = rng or random.Random()

    rows: list[tuple[str, int, int, str]] = []
    for c in creatures:
        dex_mod = c.abilities.modifier(Ability.DEX)
        roll = rng.randint(1, 20)
        total = roll + dex_mod
        rows.append((c.id, total, c.abilities.dexterity, c.id))

    # Sort: -total (highest first), -dex, +id
    rows.sort(key=lambda r: (-r[1], -r[2], r[3]))
    return InitiativeResult(entries=[(r[0], r[1]) for r in rows])


# ============================================================================
# Saving throw
# ============================================================================


def saving_throw(
    creature: Character | NPC,
    ability: Ability,
    dc: int,
    *,
    proficient: bool = False,
    advantage: bool = False,
    disadvantage: bool = False,
    rng: random.Random | None = None,
) -> SaveResult:
    """Roll a saving throw against a DC.

    modifier = ability_mod + (proficiency_bonus if proficient)
    Natural 20 -> auto success. Natural 1 -> auto fail.

    Conditions may impose advantage/disadvantage (e.g. Restrained gives
    disadvantage on DEX saves). Paralyzed / Petrified / Restrained /
    Stunned / Unconscious creatures auto-fail STR and DEX saves (PHB).
    """
    rng = rng or random.Random()
    modifier = creature.abilities.modifier(ability)
    if proficient and isinstance(creature, Character):
        modifier += creature.proficiency_bonus

    # Condition-driven modifiers
    cond_adv, cond_dis, auto_fail = apply_save_modifiers(creature, ability)
    # Exhaustion level 3+ gives disadvantage on saves
    from auto_dm.engine.conditions import exhaustion_disadvantage_attack
    if exhaustion_disadvantage_attack(creature):
        cond_dis = True
    # Rage: advantage on STR saves (PHB p. 48)
    from auto_dm.engine.rage import is_raging
    if ability is Ability.STR and is_raging(creature):
        cond_adv = True
    # Danger Sense (Barbarian L2): advantage on DEX saves vs visible effects
    if ability is Ability.DEX and isinstance(creature, Character):
        from auto_dm.engine.defenses import danger_sense_grants_advantage
        if danger_sense_grants_advantage(creature):
            cond_adv = True
    # Aura of Protection (Paladin L6+): +CHA mod to saves for self & allies in range
    if isinstance(creature, Character) and creature.has_aura_of_protection:
        modifier += creature.abilities.modifier(Ability.CHA)

    effective_advantage = advantage or cond_adv
    effective_disadvantage = disadvantage or cond_dis
    if effective_advantage and effective_disadvantage:
        effective_advantage = False
        effective_disadvantage = False

    # Pending advantage stacks from Inspiration.
    if isinstance(creature, Character) and creature.pending_advantage > 0:
        creature.pending_advantage -= 1
        effective_advantage = True

    result = roll_d20(
        advantage=effective_advantage,
        disadvantage=effective_disadvantage,
        modifier=modifier,
        rng=rng,
    )
    natural_roll = result.kept[0]

    is_crit = natural_roll == 20
    is_fumble = natural_roll == 1 or auto_fail
    is_success = is_crit or (not is_fumble and result.total >= dc)

    return SaveResult(
        creature_id=creature.id,
        ability=ability,
        dc=dc,
        roll=natural_roll,
        modifier=modifier,
        total=result.total,
        is_success=is_success,
        is_crit=is_crit,
        is_fumble=is_fumble,
    )


# ============================================================================
# Death save
# ============================================================================


def death_save(
    character: Character,
    *,
    rng: random.Random | None = None,
) -> tuple[SaveResult, bool]:
    """Roll a death saving throw.

    PHB rules:
        - Nat 20: regain 1 HP, reset death saves.
        - Nat 1: counts as 2 failures.
        - 10+: 1 success.
        - 1-9: 1 failure.
        - 3 successes: stabilized (at 0 HP, doesn't die).
        - 3 failures: dead.

    Mutates the character. Returns (SaveResult, died_bool).
    """
    rng = rng or random.Random()
    roll = rng.randint(1, 20)

    is_success = False
    if roll == 20:
        character.hp_current = 1
        character.death_save_successes = 0
        character.death_save_failures = 0
        is_success = True
    elif roll == 1:
        character.death_save_failures += 2
    elif roll >= 10:
        character.death_save_successes += 1
        is_success = True
    else:
        character.death_save_failures += 1

    # Cap at 3 each (3 successes = stabilized, doesn't increment further)
    character.death_save_successes = min(3, character.death_save_successes)
    character.death_save_failures = min(3, character.death_save_failures)

    died = character.death_save_failures >= 3

    result = SaveResult(
        creature_id=character.id,
        ability=Ability.CON,  # for display
        dc=10,
        roll=roll,
        modifier=0,
        total=roll,
        is_success=is_success,
        is_crit=roll == 20,
        is_fumble=roll == 1,
    )
    return result, died

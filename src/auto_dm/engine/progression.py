"""Progression rules: XP thresholds, level-up, ASI, Inspiration.

XP thresholds (PHB p. 15):

    Level 1: 0 XP
    Level 2: 300 XP
    Level 3: 900 XP
    Level 4: 2,700 XP
    Level 5: 6,500 XP
    Level 6: 14,000 XP
    Level 7: 23,000 XP
    Level 8: 34,000 XP
    Level 9: 48,000 XP
    Level 10: 64,000 XP
    Level 11: 85,000 XP
    Level 12: 100,000 XP
    Level 13: 120,000 XP
    Level 14: 140,000 XP
    Level 15: 165,000 XP
    Level 16: 195,000 XP
    Level 17: 225,000 XP
    Level 18: 265,000 XP
    Level 19: 305,000 XP
    Level 20: 355,000 XP

ASI: at levels 4, 8, 12, 16, and 19, the character gains either +2 to
one ability score or +1 to two ability scores. Max ability score is 20
for PCs at these levels (PHB p. 15).

Inspiration: a token the DM grants for good roleplay. Spending it gives
advantage on one d20 roll (attack, save, ability check). One character
can have at most one inspiration at a time (PHB p. 126 — "you can't
stockpile").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from auto_dm.engine.extra_attack import extra_attacks_for
from auto_dm.engine.dice import roll_dice
from auto_dm.state.models import Ability, AbilityScores, Character


# ============================================================================
# XP thresholds (PHB p. 15)
# ============================================================================


# Index by level: XP_THRESHOLDS[1] = 0, XP_THRESHOLDS[2] = 300, ...
XP_THRESHOLDS: list[int] = [
    0,        # Level 1
    300,      # Level 2
    900,      # Level 3
    2_700,    # Level 4
    6_500,    # Level 5
    14_000,   # Level 6
    23_000,   # Level 7
    34_000,   # Level 8
    48_000,   # Level 9
    64_000,   # Level 10
    85_000,   # Level 11
    100_000,  # Level 12
    120_000,  # Level 13
    140_000,  # Level 14
    165_000,  # Level 15
    195_000,  # Level 16
    225_000,  # Level 17
    265_000,  # Level 18
    305_000,  # Level 19
    355_000,  # Level 20
]


def level_for_xp(xp: int) -> int:
    """Return the highest level whose XP threshold is <= ``xp``.

    Clamps to 1 (minimum) and 20 (cap). PHB: a character never exceeds
    level 20 by XP alone; further progression requires DM fiat / epic
    boons.
    """
    if xp < 0:
        return 1
    level = 1
    for lvl, threshold in enumerate(XP_THRESHOLDS, start=1):
        if xp >= threshold:
            level = lvl
        else:
            break
    return min(level, 20)


def xp_to_next_level(character: Character) -> Optional[int]:
    """Return the XP needed to reach the next level.

    Returns ``None`` if the character is already at level 20 (cap).
    """
    if character.level >= 20:
        return None
    return XP_THRESHOLDS[character.level] - getattr(character, "xp", 0)


def proficiency_bonus_for(level: int) -> int:
    """Return the proficiency bonus for a given level (PHB p. 15).

    Level 1-4: +2
    Level 5-8: +3
    Level 9-12: +4
    Level 13-16: +5
    Level 17-20: +6
    """
    if level >= 17:
        return 6
    if level >= 13:
        return 5
    if level >= 9:
        return 4
    if level >= 5:
        return 3
    return 2


# ============================================================================
# Level-up
# ============================================================================


@dataclass
class LevelUpResult:
    """Result of a single level-up operation.

    Tracks what changed so the caller (DM agent, REPL, save system) can
    narrate or audit it.
    """

    old_level: int
    new_level: int
    hp_gained: int
    new_proficiency_bonus: int
    new_extra_attacks: int
    asi_pending: bool  # True if the new level is an ASI level (4/8/12/16/19)
    new_max_hp: int
    notes: list[str] = field(default_factory=list)


def level_up(
    character: Character,
    *,
    hp_roll: Optional[int] = None,
    con_modifier: Optional[int] = None,
    rng=None,
) -> LevelUpResult:
    """Advance ``character`` by one level.

    Mutates ``character.level``, ``character.proficiency_bonus``,
    ``character.hp_max``, ``character.hp_current`` (+hp_gained),
    ``character.extra_attacks``, and increments ``hit_dice_remaining``.

    Args:
        hp_roll: Caller-supplied hit-die roll (1dN). If None, the
            function rolls ``character.hit_dice`` for you.
        con_modifier: Override Constitution modifier. If None, computed
            from ``character.abilities.constitution``.
        rng: Random source (defaults to ``random.Random()``).

    Returns:
        :class:`LevelUpResult` summarizing the changes.

    Raises:
        ValueError: If the character is already at level 20.
    """
    if character.level >= 20:
        raise ValueError("Character is already at level 20 (cap).")

    old_level = character.level
    new_level = old_level + 1
    if hp_roll is None:
        # Roll the hit die (e.g. "1d10")
        roll = roll_dice(character.hit_dice, rng=rng)
        hp_gained = roll.total
    else:
        hp_gained = hp_roll
    # Minimum of 1 HP per level per PHB.
    hp_gained = max(1, hp_gained)

    if con_modifier is None:
        con_modifier = character.abilities.modifier(Ability.CON)
    hp_gained += con_modifier
    # PHB: minimum of 1 HP per level (after CON mod).
    if hp_gained < 1:
        hp_gained = 1

    character.level = new_level
    new_prof = proficiency_bonus_for(new_level)
    character.proficiency_bonus = new_prof
    character.hp_max = character.hp_max + hp_gained
    character.hp_current = character.hp_current + hp_gained
    character.hit_dice_remaining = character.hit_dice_remaining + 1
    character.extra_attacks = extra_attacks_for(character.class_, new_level)

    notes: list[str] = []
    if is_asi_level(new_level):
        notes.append(f"ASI available at level {new_level} (apply via /asi).")

    return LevelUpResult(
        old_level=old_level,
        new_level=new_level,
        hp_gained=hp_gained,
        new_proficiency_bonus=new_prof,
        new_extra_attacks=character.extra_attacks,
        asi_pending=is_asi_level(new_level),
        new_max_hp=character.hp_max,
        notes=notes,
    )


# ============================================================================
# ASI
# ============================================================================


# PHB p. 15: ASI is granted at levels 4, 8, 12, 16, 19
ASI_LEVELS: frozenset[int] = frozenset({4, 8, 12, 16, 19})


def is_asi_level(level: int) -> bool:
    """True if reaching ``level`` grants an ASI."""
    return level in ASI_LEVELS


def apply_asi(
    character: Character,
    primary: Ability,
    secondary: Ability | None = None,
) -> AbilityScores:
    """Apply an Ability Score Improvement to ``character``.

    PHB p. 15: +2 to one ability, OR +1 to two abilities. The cap is
    20 at this level (PHB allows >20 only via specific magic items).

    Mutates ``character.abilities`` in place. Returns the updated scores.
    Raises ValueError if a chosen ability would exceed the cap or if the
    split is invalid.
    """
    scores = character.abilities
    if secondary is None:
        # +2 to one ability, cap 20
        cur = getattr(scores, primary.value)
        if cur + 2 > 20:
            raise ValueError(
                f"{primary.value} would exceed 20 ({cur} + 2)."
            )
        setattr(scores, primary.value, cur + 2)
    else:
        if primary == secondary:
            raise ValueError("ASI split must use two different abilities.")
        cur_p = getattr(scores, primary.value)
        cur_s = getattr(scores, secondary.value)
        if cur_p + 1 > 20:
            raise ValueError(f"{primary.value} would exceed 20.")
        if cur_s + 1 > 20:
            raise ValueError(f"{secondary.value} would exceed 20.")
        setattr(scores, primary.value, cur_p + 1)
        setattr(scores, secondary.value, cur_s + 1)
    return scores


# ============================================================================
# Inspiration
# ============================================================================


def grant_inspiration(character: Character) -> bool:
    """Grant inspiration to a character. Returns True if newly granted.

    PHB: you can't stockpile — granting when already inspired is a no-op.
    """
    if character.inspiration:
        return False
    character.inspiration = True
    return True


def spend_inspiration(character: Character) -> bool:
    """Spend inspiration. Returns True if it was available and consumed.

    Adds 1 to pending_advantage; the next eligible d20 roll consumes it.
    """
    if not character.inspiration:
        return False
    character.inspiration = False
    character.pending_advantage += 1
    return True


def consume_pending_advantage(character: Character) -> bool:
    """Consume one stack of pending advantage for a single d20 roll.

    Returns True if there was one to consume.
    """
    if character.pending_advantage > 0:
        character.pending_advantage -= 1
        return True
    return False

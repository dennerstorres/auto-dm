"""Progression rules: Ability Score Improvement (ASI) and Inspiration.

ASI: at levels 4, 8, 12, 16, and 19, the character gains either +2 to
one ability score or +1 to two ability scores. Max ability score is 20
for PCs at these levels (PHB p. 15).

Inspiration: a token the DM grants for good roleplay. Spending it gives
advantage on one d20 roll (attack, save, ability check). One character
can have at most one inspiration at a time (PHB p. 126 — "you can't
stockpile").
"""
from __future__ import annotations

from auto_dm.state.models import Ability, AbilityScores, Character


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

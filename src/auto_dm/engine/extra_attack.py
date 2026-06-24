"""Extra Attack feature (PHB p. 72 etc.).

Several martial classes (Fighter, Barbarian, Paladin, Ranger, Monk)
gain additional attacks when they take the Attack action. The
progression (PHB standard):

    L1-4:  1 attack per Attack action (0 extra)
    L5-10: 2 attacks (1 extra)
    L11-17: 3 attacks (2 extra)
    L18-20: 4 attacks (3 extra)

The combat engine consults :func:`extra_attacks_for` to know how many
attacks an actor can make in a single Attack action.
"""
from __future__ import annotations

# Classes that follow the standard "Extra Attack" progression
MARTIAL_CLASSES = frozenset({
    "fighter", "barbarian", "paladin", "ranger", "monk",
})


def extra_attacks_for(class_name: str, level: int) -> int:
    """Number of extra attacks the character gains from Extra Attack.

    Returns 0 for non-martial classes (casters, half-casters don't
    all get it — only those in MARTIAL_CLASSES).
    """
    if class_name.lower() not in MARTIAL_CLASSES:
        return 0
    if level >= 18:
        return 3
    if level >= 11:
        return 2
    if level >= 5:
        return 1
    return 0


def attacks_per_action(character) -> int:
    """How many attacks the character can make in one Attack action."""
    return 1 + getattr(character, "extra_attacks", 0)
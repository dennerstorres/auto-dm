"""Dice rolling and standard notation parsing.

Supports:
    - ``XdY``              e.g. ``1d20``, ``2d6``
    - ``XdY+Z`` / ``XdY-Z`` e.g. ``1d20+5``, ``1d20-2``
    - ``XdYkhN``           keep highest N (e.g. ``4d6kh3`` for ability scores)
    - ``XdYklN``           keep lowest N  (e.g. ``2d20kl1`` for disadvantage)

All functions accept an optional ``rng`` argument for deterministic tests.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass

# Notation pattern: capture groups are count, sides, keep type, keep n,
# mod sign, mod value. Groups are optional.
_NOTATION_PATTERN = re.compile(
    r"^(\d*)d(\d+)(?:k([hl])(\d+))?(?:([+-])(\d+))?$"
)


@dataclass
class DiceRoll:
    """The full result of a dice roll, broken down for transparency."""

    notation: str
    rolls: list[int]      # every die that was rolled
    kept: list[int]       # dice that count toward the total
    dropped: list[int]    # dice that were dropped (kh/kl)
    modifier: int = 0
    total: int = 0

    def __str__(self) -> str:
        parts = [f"({self.notation})"]
        parts.append(f"rolls={self.rolls}")
        if self.dropped:
            parts.append(f"dropped={self.dropped}")
        if self.modifier:
            parts.append(f"mod={self.modifier:+d}")
        parts.append(f"total={self.total}")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Generic notation
# ---------------------------------------------------------------------------


def roll_dice(notation: str, *, rng: random.Random | None = None) -> DiceRoll:
    """Roll dice from a standard notation string.

    Args:
        notation: e.g. ``"1d20+5"``, ``"4d6kh3"``, ``"2d20kl1"``.
        rng: optional Random instance for deterministic testing.

    Returns:
        DiceRoll with full breakdown.

    Raises:
        ValueError: if notation is malformed or out of range.
    """
    rng = rng or random.Random()
    match = _NOTATION_PATTERN.match(notation.strip().lower().replace(" ", ""))
    if not match:
        raise ValueError(f"Invalid dice notation: {notation!r}")

    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    keep_type = match.group(3)  # 'h', 'l', or None
    keep_n = int(match.group(4)) if keep_type else None
    mod_sign = match.group(5) or "+"
    mod_value = int(match.group(6)) if match.group(6) else 0

    if not 1 <= count <= 100:
        raise ValueError(f"Dice count out of range (1-100): {count}")
    if not 2 <= sides <= 1000:
        raise ValueError(f"Dice sides out of range (2-1000): {sides}")
    if keep_n is not None and not 1 <= keep_n <= count:
        raise ValueError(f"keep_n ({keep_n}) must be in 1..{count}")

    rolls = [rng.randint(1, sides) for _ in range(count)]
    kept = list(rolls)
    dropped: list[int] = []

    if keep_type == "h":
        sorted_desc = sorted(rolls, reverse=True)
        kept = sorted_desc[:keep_n]
        dropped = sorted_desc[keep_n:]
    elif keep_type == "l":
        sorted_asc = sorted(rolls)
        kept = sorted_asc[:keep_n]
        dropped = sorted_asc[keep_n:]

    modifier = mod_value if mod_sign == "+" else -mod_value
    total = sum(kept) + modifier

    return DiceRoll(
        notation=notation,
        rolls=rolls,
        kept=kept,
        dropped=dropped,
        modifier=modifier,
        total=total,
    )


# ---------------------------------------------------------------------------
# d20 (the most common roll)
# ---------------------------------------------------------------------------


def roll_d20(
    *,
    advantage: bool = False,
    disadvantage: bool = False,
    modifier: int = 0,
    rng: random.Random | None = None,
) -> DiceRoll:
    """Roll a d20 with optional advantage/disadvantage and flat modifier.

    If both advantage and disadvantage are set, they cancel (PHB rule).
    """
    rng = rng or random.Random()

    if advantage and not disadvantage:
        a, b = rng.randint(1, 20), rng.randint(1, 20)
        chosen = max(a, b)
        return DiceRoll(
            notation=f"2d20kh1{modifier:+d}" if modifier else "2d20kh1",
            rolls=[a, b],
            kept=[chosen],
            dropped=[min(a, b)],
            modifier=modifier,
            total=chosen + modifier,
        )
    if disadvantage and not advantage:
        a, b = rng.randint(1, 20), rng.randint(1, 20)
        chosen = min(a, b)
        return DiceRoll(
            notation=f"2d20kl1{modifier:+d}" if modifier else "2d20kl1",
            rolls=[a, b],
            kept=[chosen],
            dropped=[max(a, b)],
            modifier=modifier,
            total=chosen + modifier,
        )

    # Normal
    roll = rng.randint(1, 20)
    return DiceRoll(
        notation=f"1d20{modifier:+d}" if modifier else "1d20",
        rolls=[roll],
        kept=[roll],
        dropped=[],
        modifier=modifier,
        total=roll + modifier,
    )


# ---------------------------------------------------------------------------
# Ability scores
# ---------------------------------------------------------------------------


def roll_4d6_keep_highest_3(
    *, rng: random.Random | None = None
) -> int:
    """Roll a single D&D ability score: 4d6, keep highest 3."""
    rng = rng or random.Random()
    rolls = [rng.randint(1, 6) for _ in range(4)]
    return sum(sorted(rolls, reverse=True)[:3])


def roll_stats(rng: random.Random | None = None) -> list[int]:
    """Roll six ability scores (4d6 keep highest 3, each)."""
    return [roll_4d6_keep_highest_3(rng=rng) for _ in range(6)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def roll_die(sides: int, *, rng: random.Random | None = None) -> int:
    """Roll a single die with N sides."""
    return (rng or random.Random()).randint(1, sides)

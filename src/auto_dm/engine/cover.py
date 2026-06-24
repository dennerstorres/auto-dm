"""Cover rules (DMG p. 250).

Three cover levels per the PHB / DMG:

- Half cover: +2 to AC and DEX saving throws.
- Three-quarters cover: +5 to AC and DEX saving throws.
- Total cover: target can't be targeted directly by attacks or spells.

Cover is set by the DM (or environmental reasoning). We don't auto-compute
it from environment — we trust the caller. This module exposes lookup
helpers and integrates with the attack roll.
"""
from __future__ import annotations


COVER_LEVELS = ("none", "half", "three_quarters", "total")


def cover_ac_bonus(cover: str) -> int:
    """AC bonus granted by the given cover level."""
    return {
        "none": 0,
        "half": 2,
        "three_quarters": 5,
        "total": 999,  # effectively untargetable
    }.get(cover, 0)


def cover_dex_save_bonus(cover: str) -> int:
    """DEX save bonus granted by the given cover level."""
    return cover_ac_bonus(cover)


def is_valid_cover(cover: str) -> bool:
    return cover in COVER_LEVELS

"""Pre-defined AI companion characters.

These are the ready-made party members players can choose from
when starting a campaign. Each is a factory function returning a
fully-built ``Character`` (level 1, standard array, distinct
personality).
"""
from auto_dm.companions.roster import (
    COMPANION_FACTORIES,
    build_companion,
    list_companion_keys,
    make_lyra,
    make_mira,
    make_thorgrim,
    make_vex,
)

__all__ = [
    "COMPANION_FACTORIES",
    "build_companion",
    "list_companion_keys",
    "make_lyra",
    "make_mira",
    "make_thorgrim",
    "make_vex",
]
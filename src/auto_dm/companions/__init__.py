"""Pre-defined AI companion characters.

These are the ready-made party members players can choose from
when starting a campaign. Each is a factory function returning a
fully-built ``Character`` (level 1, standard array, distinct
personality).

Phase 27 expands the roster from 4 to 12 factories covering every
PHB class, and adds :mod:`auto_dm.companions.selection` for
synergy-based party candidate rolls.
"""
from auto_dm.companions.roster import (
    COMPANION_BLURBS,
    COMPANION_FACTORIES,
    build_companion,
    list_companion_keys,
    make_brom,
    make_dax,
    make_eldra,
    make_garrick,
    make_kael,
    make_lyra,
    make_maren,
    make_mira,
    make_sage,
    make_thorgrim,
    make_tobias,
    make_vex,
)
from auto_dm.companions.selection import (
    ROLE_TAGS,
    roll_party_candidates,
)

__all__ = [
    "COMPANION_BLURBS",
    "COMPANION_FACTORIES",
    "ROLE_TAGS",
    "build_companion",
    "list_companion_keys",
    "make_brom",
    "make_dax",
    "make_eldra",
    "make_garrick",
    "make_kael",
    "make_lyra",
    "make_maren",
    "make_mira",
    "make_sage",
    "make_thorgrim",
    "make_tobias",
    "make_vex",
    "roll_party_candidates",
]
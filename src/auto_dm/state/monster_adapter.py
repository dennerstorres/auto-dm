"""Adapter from PHB ``Monster`` stat blocks to combat ``NPC`` state.

A ``Monster`` (from ``auto_dm.phb.models``) holds the full PHB stat block as
parsed from markdown. The combat engine, however, works against the
``auto_dm.state.models.NPC`` type — a flatter record keyed to what the
engine actually consumes.

This module is the bridge: ``monster_to_npc(monster)`` produces an NPC
ready to drop into ``GameState.npcs`` and use in combat. Multiple monsters
of the same kind (e.g. a goblin patrol of three) just need unique
``npc_id`` values.
"""
from __future__ import annotations

import re

from auto_dm.phb.models import Monster, MonsterAction
from auto_dm.state.models import NPC


def _slugify(name: str) -> str:
    """Convert a monster name to a snake_case id-safe slug.

    ``Adult Red Dragon (Chromatic)`` -> ``adult_red_dragon_chromatic``.
    """
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")


def slugify_monster_id(name: str) -> str:
    """Public alias for :func:`_slugify` for callers outside this module.

    Useful when the caller wants to generate a stable id for a Monster
    (e.g. ``/encounter`` spawning two of the same creature uses this
    to build ``goblin_1`` / ``goblin_2``).
    """
    return _slugify(name)


def _format_action(action: MonsterAction) -> str:
    """Compact one-line summary for ``NPC.actions`` (which is ``list[str]``).

    The full structured ``MonsterAction`` lives in the source Monster; this
    text is just what the DM (and ``render_combat_status``) sees in combat.
    """
    bits: list[str] = [action.name]
    if action.attack_type:
        atk = action.attack_type.replace("_", " ")
        bonus = f"+{action.attack_bonus}" if action.attack_bonus is not None else ""
        bits.append(f"({atk} {bonus})".strip())
    if action.damage_dice:
        dmg = action.damage_dice
        if action.damage_type:
            dmg += f" {action.damage_type}"
        if action.additional_damage_dice:
            rider = action.additional_damage_dice
            if action.additional_damage_type:
                rider += f" {action.additional_damage_type}"
            dmg += f" + {rider}"
        bits.append(f"[{dmg}]")
    if action.recharge:
        bits.append(f"(Recharge {action.recharge})")
    elif action.usages:
        bits.append(f"({action.usages})")
    return " ".join(bits)


def monster_to_npc(
    monster: Monster,
    *,
    npc_id: str | None = None,
    is_hostile: bool = True,
) -> NPC:
    """Convert a parsed Monster into an NPC ready for combat.

    Args:
        monster: The parsed PHB stat block.
        npc_id: Override the generated id. Defaults to a slug of the monster's
            name. If you spawn several of the same monster (e.g. a goblin
            patrol), pass distinct ids (``goblin_1``, ``goblin_2``, ...).
        is_hostile: Whether the NPC starts hostile. Friendly NPCs from the
            ``Monster`` list (e.g. Archmage, Acolyte) can override this.

    Returns:
        An ``NPC`` with HP/AC/speed/abilities/damage modifiers populated,
        and a flat ``actions`` list of human-readable summaries.
    """
    if npc_id is None:
        npc_id = _slugify(monster.name)

    description = f"{monster.size.value} {monster.type.value}"
    if monster.subtype:
        description += f" ({monster.subtype})"
    description += f", {monster.alignment}"

    return NPC(
        id=npc_id,
        name=monster.name,
        description=description,
        hp_current=monster.hp_average,
        hp_max=monster.hp_average,
        temp_hp=0,
        armor_class=monster.armor_class,
        speed=monster.speed_walk,
        abilities=monster.abilities,
        resistances=list(monster.damage_resistances),
        immunities=list(monster.damage_immunities),
        vulnerabilities=list(monster.damage_vulnerabilities),
        condition_immunities=list(monster.condition_immunities),
        actions=[_format_action(a) for a in monster.actions],
        is_hostile=is_hostile,
        challenge_rating=monster.challenge_rating,
    )
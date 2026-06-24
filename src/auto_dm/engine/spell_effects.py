"""Per-spell mechanical effects for the most-used PHB spells.

Critical spell implementations (covered here):
  - Magic Missile (auto-hit, 3 missiles at L1, +1 per slot above)
  - Healing Word (bonus action, 1d4 + casting mod heal)
  - Fireball (8d6 fire, DEX save half)
  - Shield (reaction, +5 AC until next turn)
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from auto_dm.engine.combat import saving_throw
from auto_dm.engine.dice import roll_dice
from auto_dm.state.models import Ability, Character, NPC, Spellcasting


@dataclass
class SpellEffectResult:
    """Mechanical outcome of a spell effect."""
    success: bool
    damage: int = 0
    healing: int = 0
    target_id: str = ""
    target_hp: int = 0
    consumed_slot_level: int = 0
    error: str = ""
    detail: str = ""


# ============================================================================
# Magic Missile (PHB p. 257)
# ============================================================================


def magic_missile_dart_count(slot_level: int) -> int:
    """PHB: 3 darts at L1, +1 per slot level above."""
    return 3 + max(0, slot_level - 1)


def roll_magic_missile(slot_level: int, *, rng=None) -> list[int]:
    """Roll damage for each dart: 1d4+1."""
    rng = rng or random.Random()
    n = magic_missile_dart_count(slot_level)
    return [roll_dice("1d4+1", rng=rng).total for _ in range(n)]


def cast_magic_missile(
    caster: Character, target, *, slot_level: int = 1, rng=None,
) -> SpellEffectResult:
    """Auto-hit dart(s) of force. Consumes 1 slot of slot_level."""
    rng = rng or random.Random()
    if caster.spellcasting is None:
        return SpellEffectResult(success=False, error=f"{caster.name} cannot cast")
    if not _consume_one_slot(caster.spellcasting, slot_level):
        return SpellEffectResult(success=False, error=f"no slot for level {slot_level}")
    damages = roll_magic_missile(slot_level, rng=rng)
    total = sum(damages)
    new_hp = max(0, target.hp_current - total)
    target.hp_current = new_hp
    return SpellEffectResult(
        success=True, damage=total, target_id=target.id, target_hp=new_hp,
        consumed_slot_level=slot_level,
        detail=f"{len(damages)} dardos: {damages} = {total} force",
    )


# ============================================================================
# Healing Word (PHB p. 250)
# ============================================================================


def roll_healing_word(caster: Character, *, slot_level: int = 1, rng=None) -> int:
    """PHB: 1d4 + casting ability mod. Higher levels: +1d4 per slot above."""
    rng = rng or random.Random()
    if caster.spellcasting is None:
        return 0
    n_dice = 1 + max(0, slot_level - 1)
    rolls = roll_dice(f"{n_dice}d4", rng=rng).rolls
    casting_mod = caster.abilities.modifier(caster.spellcasting.ability)
    return sum(rolls) + casting_mod


def cast_healing_word(
    caster: Character, target: Character, *, slot_level: int = 1, rng=None,
) -> SpellEffectResult:
    """Bonus action: heal 1d4 + casting mod (upcast: +1d4 per slot)."""
    rng = rng or random.Random()
    if caster.spellcasting is None:
        return SpellEffectResult(success=False, error=f"{caster.name} cannot cast")
    if not _consume_one_slot(caster.spellcasting, slot_level):
        return SpellEffectResult(success=False, error=f"no slot for level {slot_level}")
    healed = roll_healing_word(caster, slot_level=slot_level, rng=rng)
    new_hp = min(target.hp_max, target.hp_current + healed)
    target.hp_current = new_hp
    return SpellEffectResult(
        success=True, healing=healed, target_id=target.id, target_hp=new_hp,
        consumed_slot_level=slot_level, detail=f"heal {healed} HP",
    )


# ============================================================================
# Fireball (PHB p. 241)
# ============================================================================


def roll_fireball(slot_level: int = 3, *, is_save_success: bool = False, rng=None) -> int:
    """PHB: 8d6 fire, +1d6 per slot above 3rd. DEX save = half."""
    rng = rng or random.Random()
    n_dice = 8 + max(0, slot_level - 3)
    total = sum(roll_dice(f"{n_dice}d6", rng=rng).rolls)
    return total // 2 if is_save_success else total


def cast_fireball(
    caster: Character, target, *, slot_level: int = 3, save_dc: int | None = None, rng=None,
) -> SpellEffectResult:
    """3rd-level: 8d6 fire. DEX save = half."""
    rng = rng or random.Random()
    if caster.spellcasting is None:
        return SpellEffectResult(success=False, error=f"{caster.name} cannot cast")
    if not _consume_one_slot(caster.spellcasting, slot_level):
        return SpellEffectResult(success=False, error=f"no slot for level {slot_level}")
    dc = save_dc or caster.spellcasting.save_dc
    save = saving_throw(target, Ability.DEX, dc, rng=rng)
    dmg = roll_fireball(slot_level, is_save_success=save.is_success, rng=rng)
    new_hp = max(0, target.hp_current - dmg)
    target.hp_current = new_hp
    return SpellEffectResult(
        success=True, damage=dmg, target_id=target.id, target_hp=new_hp,
        consumed_slot_level=slot_level,
        detail=f"DC{dc} DEX save {save.total} -> {'half' if save.is_success else 'full'} = {dmg} fire",
    )


# ============================================================================
# Shield (PHB p. 275)
# ============================================================================


def apply_shield(target: Character) -> SpellEffectResult:
    """Reaction: +5 AC until start of next turn."""
    target.pending_ac_bonus += 5
    return SpellEffectResult(success=True, target_id=target.id, detail="+5 AC until next turn")


# ============================================================================
# Helpers
# ============================================================================


def _consume_one_slot(spellcasting: Spellcasting, level: int) -> bool:
    if spellcasting.spell_slots.get(level, 0) > 0:
        spellcasting.spell_slots[level] -= 1
        return True
    # Try upcast: lowest slot >= level
    candidates = sorted(
        lvl for lvl, rem in spellcasting.spell_slots.items()
        if lvl >= level and rem > 0
    )
    if not candidates:
        return False
    chosen = candidates[0]
    spellcasting.spell_slots[chosen] -= 1
    return True

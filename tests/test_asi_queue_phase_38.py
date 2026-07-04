"""Phase 38 — Companion ASI auto-resolve + player ASI queue.

Companions auto-resolve their ASI via ``auto_resolve_companion_asi``
(deterministic, no LLM). The player queues a ``pending_asi`` record
and resolves it through ``resolve_asi_choice`` (web modal path).

Covers:
- Primary target +2 path (default for low stats)
- 1/1 split path (when primary is 19 or 20 — capped)
- CON fallback (when both primary and secondary are capped)
- ``resolve_asi_choice`` happy path
- ``resolve_asi_choice`` validation: no queue, already resolved, cap
- ``companion_asi_to_pending`` shape mapping
- ``update_spell_slots_for_level`` wrapper
"""
from __future__ import annotations

import pytest

from auto_dm.character import (
    auto_resolve_companion_asi,
    companion_asi_to_pending,
    resolve_asi_choice,
    update_spell_slots_for_level,
)
from auto_dm.character.spells import get_spell_slots, prepare_caster_spells
from auto_dm.phb import get_class, get_spells_for_class
from auto_dm.state.models import (
    Ability,
    AbilityScores,
    Character,
)


# ============================================================================
# Helpers
# ============================================================================


def make_companion(
    class_name: str,
    level: int,
    *,
    str_score: int = 14,
    dex_score: int = 12,
    con_score: int = 14,
    int_score: int = 10,
    wis_score: int = 12,
    cha_score: int = 10,
) -> Character:
    return Character(
        id=f"asi_{class_name}_{level}",
        name=f"{class_name}-asi",
        race="Human",
        **{"class": class_name},
        subclass=None,
        level=level,
        background="Soldier",
        alignment="LN",
        abilities=AbilityScores(
            strength=str_score, dexterity=dex_score, constitution=con_score,
            intelligence=int_score, wisdom=wis_score, charisma=cha_score,
        ),
        hp_current=20, hp_max=20, armor_class=14, speed=30,
        proficiency_bonus=2,
        hit_dice="1d10", hit_dice_remaining=level,
    )


def make_player_with_pending_asi(level: int = 4) -> Character:
    """Build a player with an existing pending_asi queue (mimics the
    engine's queue after ``award_party_xp`` walks through L4)."""
    c = make_companion("Fighter", level)
    c.is_player = True
    c.pending_asi = {
        "level": level, "choices": ["primary"], "resolved": False,
        "primary": None, "secondary": None,
    }
    return c


# ============================================================================
# TestAutoResolveCompanionASI — heuristic paths
# ============================================================================


class TestAutoResolveCompanionASI:
    def test_path_a_primary_plus_two(self):
        """Barbarian L4 with STR=14 → +2 STR (primary <= 18)."""
        c = make_companion("Barbarian", 4, str_score=14)
        original_str = c.abilities.strength
        choice = auto_resolve_companion_asi(c)
        assert choice["applied"] is True
        assert choice["primary"] == Ability.STR
        assert choice["secondary"] is None
        assert c.abilities.strength == original_str + 2

    def test_path_b_split_when_primary_is_19(self):
        """Barbarian L8 with STR=19 → 1/1 split (STR + CON)."""
        c = make_companion("Barbarian", 8, str_score=19)
        original_str = c.abilities.strength
        original_con = c.abilities.constitution
        choice = auto_resolve_companion_asi(c)
        assert choice["applied"] is True
        assert choice["primary"] == Ability.STR
        assert choice["secondary"] == Ability.CON
        assert c.abilities.strength == original_str + 1
        assert c.abilities.constitution == original_con + 1

    def test_path_b_split_when_primary_is_20(self):
        """Primary at the cap (20) → ``apply_asi`` rejects even +1.
        Path A fails (+2), Path B also fails (+1). The function falls
        back to Path C (+2 CON, since the cap allows it)."""
        c = make_companion("Fighter", 12, str_score=20)
        original_con = c.abilities.constitution
        choice = auto_resolve_companion_asi(c)
        assert choice["applied"] is True
        # Path C: +2 CON (still under cap 20).
        assert choice["primary"] == Ability.CON
        assert choice["secondary"] is None
        assert c.abilities.strength == 20  # capped, untouched
        assert c.abilities.constitution == original_con + 2

    def test_path_c_con_fallback_when_both_capped(self):
        """STR=20 and CON=20 → all three paths fail (every bonus would
        exceed the cap). The function returns ``applied=False`` and
        leaves stats untouched. The level-up chain still succeeds —
        no exception is raised."""
        c = make_companion("Fighter", 16, str_score=20, con_score=20)
        choice = auto_resolve_companion_asi(c)
        assert choice["applied"] is False
        assert c.abilities.strength == 20
        assert c.abilities.constitution == 20

    def test_wizard_primary_is_int(self):
        c = make_companion("Wizard", 4, int_score=14)
        original_int = c.abilities.intelligence
        choice = auto_resolve_companion_asi(c)
        assert choice["primary"] == Ability.INT
        assert c.abilities.intelligence == original_int + 2

    def test_rogue_primary_is_dex(self):
        c = make_companion("Rogue", 4, dex_score=14)
        original_dex = c.abilities.dexterity
        choice = auto_resolve_companion_asi(c)
        assert choice["primary"] == Ability.DEX
        assert c.abilities.dexterity == original_dex + 2

    def test_sorcerer_primary_is_cha(self):
        c = make_companion("Sorcerer", 4, cha_score=14)
        original_cha = c.abilities.charisma
        choice = auto_resolve_companion_asi(c)
        assert choice["primary"] == Ability.CHA
        assert c.abilities.charisma == original_cha + 2

    def test_unknown_class_defaults_to_str(self):
        c = make_companion("Mystic", 4)  # not in primary table
        original_str = c.abilities.strength
        choice = auto_resolve_companion_asi(c)
        # Falls back to STR.
        assert choice["primary"] == Ability.STR
        assert c.abilities.strength == original_str + 2


# ============================================================================
# TestCompanionASIToPending — shape mapping
# ============================================================================


class TestCompanionASIToPending:
    def test_single_primary_shape(self):
        choice = {"primary": Ability.STR, "secondary": None, "applied": True}
        pending = companion_asi_to_pending(choice)
        assert pending["choices"] == ["primary"]
        assert pending["resolved"] is True
        assert pending["primary"] == "strength"
        assert pending["secondary"] is None
        # level=0 placeholder — caller fills it in.
        assert pending["level"] == 0

    def test_split_shape(self):
        choice = {"primary": Ability.STR, "secondary": Ability.CON, "applied": True}
        pending = companion_asi_to_pending(choice)
        assert pending["choices"] == ["primary", "secondary"]
        assert pending["primary"] == "strength"
        assert pending["secondary"] == "constitution"

    def test_no_primary_shape(self):
        """No-op path (capped, fallback failed). primary is None."""
        choice = {"primary": None, "secondary": None, "applied": False}
        pending = companion_asi_to_pending(choice)
        assert pending["choices"] == ["primary"]
        assert pending["primary"] is None
        assert pending["secondary"] is None


# ============================================================================
# TestResolveASIChoice — player-facing apply
# ============================================================================


class TestResolveASIChoice:
    def test_apply_plus_two(self):
        c = make_player_with_pending_asi(level=4)
        original_str = c.abilities.strength
        scores = resolve_asi_choice(c, primary=Ability.STR)
        assert c.abilities.strength == original_str + 2
        assert c.pending_asi is None  # queue cleared
        assert scores.strength == original_str + 2

    def test_apply_split(self):
        c = make_player_with_pending_asi(level=4)
        original_str = c.abilities.strength
        original_con = c.abilities.constitution
        resolve_asi_choice(c, primary=Ability.STR, secondary=Ability.CON)
        assert c.abilities.strength == original_str + 1
        assert c.abilities.constitution == original_con + 1
        assert c.pending_asi is None

    def test_raises_when_no_pending(self):
        c = make_companion("Fighter", 4)
        c.is_player = True
        # pending_asi is None.
        with pytest.raises(ValueError, match="não tem ASI pendente"):
            resolve_asi_choice(c, primary=Ability.STR)

    def test_raises_when_already_resolved(self):
        c = make_player_with_pending_asi(level=4)
        c.pending_asi["resolved"] = True
        with pytest.raises(ValueError, match="já foi resolvida"):
            resolve_asi_choice(c, primary=Ability.STR)

    def test_raises_when_choice_would_exceed_cap(self):
        """STR=20 → +2 raises to 22, exceeds cap."""
        c = make_player_with_pending_asi(level=4)
        c.abilities.strength = 20
        with pytest.raises(ValueError, match="exceed 20"):
            resolve_asi_choice(c, primary=Ability.STR)
        # Queue preserved on failure so the player can retry.
        assert c.pending_asi is not None
        assert c.pending_asi["resolved"] is False


# ============================================================================
# TestUpdateSpellSlotsForLevel — wrapper around the engine helper
# ============================================================================


class TestUpdateSpellSlotsForLevel:
    def _make_wizard(self, level: int = 1) -> Character:
        cls_data = get_class("Wizard")
        abilities = AbilityScores(
            strength=8, dexterity=14, constitution=13,
            intelligence=15, wisdom=12, charisma=10,
        )
        cantrips = [s.name for s in get_spells_for_class("Wizard") if s.level == 0][:3]
        spellbook = [s.name for s in get_spells_for_class("Wizard") if s.level == 1][:6]
        selection = prepare_caster_spells(
            cls_data, level, abilities, 2,
            cantrips=cantrips, spellbook=spellbook,
        )
        sc = selection.to_spellcasting(cls_data, abilities, 2)
        return Character(
            id=f"wiz_{level}", name="SlotTest", race="Human",
            **{"class": "Wizard"}, subclass="Evocation",
            level=level, background="Sage", alignment="LN",
            abilities=abilities, hp_current=10, hp_max=10,
            armor_class=12, speed=30, proficiency_bonus=2,
            hit_dice="1d6", hit_dice_remaining=level,
            spellcasting=sc,
        )

    def test_refresh_l1_to_l5(self):
        c = self._make_wizard(level=1)
        # L1 Wizard: 2 slots of 1º level.
        assert c.spellcasting.spell_slots_max == {1: 2}
        # L5 Wizard: 4 / 3 / 2.
        update_spell_slots_for_level(c, 5)
        assert c.spellcasting.spell_slots_max.get(1) == 4
        assert c.spellcasting.spell_slots_max.get(2) == 3
        assert c.spellcasting.spell_slots_max.get(3) == 2
        # Current pool also refreshed.
        assert c.spellcasting.spell_slots.get(1) == 4

    def test_no_spellcasting_is_noop(self):
        c = self._make_wizard(level=1)
        c.spellcasting = None
        # Should not raise.
        update_spell_slots_for_level(c, 5)
        assert c.spellcasting is None

    def test_idempotent_for_same_level(self):
        c = self._make_wizard(level=3)
        update_spell_slots_for_level(c, 3)
        expected = get_spell_slots("Wizard", 3)
        # First call populates the slot pool from the PHB table.
        assert c.spellcasting.spell_slots_max == expected
        # Second call is a no-op (idempotent).
        update_spell_slots_for_level(c, 3)
        assert c.spellcasting.spell_slots_max == expected
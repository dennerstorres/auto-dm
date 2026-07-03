"""Tests for engine/spellcasting.py — slots, preparation, concentration, rituals."""
from __future__ import annotations

import random

import pytest

from auto_dm.engine.combat_engine import _ACTION_HANDLERS
from auto_dm.engine.spellcasting import (
    ConcentrationResult,
    RitualResult,
    available_slot_levels,
    break_concentration,
    can_cast_as_known,
    can_cast_as_prepared,
    can_cast_as_ritual,
    can_know_count,
    can_prepare_count,
    cast_as_ritual,
    cast_spell,
    concentration_dc,
    concentration_save,
    consume_slot,
    has_slot,
    prepare_spell,
    refill_slots,
    slot_levels_for_level,
    start_concentration,
    unprepare_spell,
)
from auto_dm.phb import get_spell, set_phb_root
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    Action,
    ActionType,
    Ability,
    AbilityScores,
    Character,
    Condition,
    EquippedSlots,
    NPC,
    Spellcasting,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cleric() -> Character:
    """Level 3 cleric with prepared list, slots, concentration mid-spell."""
    return Character(
        id="c1", name="Sister Mara", race="Human", class_="Cleric", level=3,
        background="Acolyte", alignment="LG",
        abilities=AbilityScores(
            strength=10, dexterity=10, constitution=12,
            intelligence=10, wisdom=16, charisma=10,
        ),
        hp_current=24, hp_max=24, armor_class=16, speed=30,
        proficiency_bonus=2, hit_dice="1d8", hit_dice_remaining=3,
        spellcasting=Spellcasting(
            ability=Ability.WIS,
            save_dc=13, attack_bonus=5,
            cantrips_known=["Sacred Flame", "Light", "Guidance"],
            spells_known=[],
            spells_prepared=["Cure Wounds", "Bless", "Healing Word", "Shield of Faith"],
            spell_slots={1: 4, 2: 2},
            spell_slots_max={1: 4, 2: 2},
            concentration="Bless",
            ritual_casting=True,
        ),
    )


@pytest.fixture
def sorcerer() -> Character:
    """Level 3 sorcerer (known caster)."""
    return Character(
        id="c2", name="Vex", race="Half-Elf", class_="Sorcerer", level=3,
        background="Hermit", alignment="CN",
        abilities=AbilityScores(
            strength=8, dexterity=14, constitution=14,
            intelligence=10, wisdom=12, charisma=16,
        ),
        hp_current=20, hp_max=20, armor_class=12, speed=30,
        proficiency_bonus=2, hit_dice="1d6", hit_dice_remaining=3,
        spellcasting=Spellcasting(
            ability=Ability.CHA,
            save_dc=13, attack_bonus=5,
            cantrips_known=["Fire Bolt", "Mage Hand"],
            spells_known=["Burning Hands", "Shield"],
            spells_prepared=[],
            spell_slots={1: 4, 2: 2},
            spell_slots_max={1: 4, 2: 2},
            concentration=None,
            ritual_casting=False,
        ),
    )


# ---------------------------------------------------------------------------
# Slot tracking
# ---------------------------------------------------------------------------


class TestSlotTracking:
    def test_has_slot_exact(self, cleric: Character) -> None:
        assert has_slot(cleric.spellcasting, 1) is True
        assert has_slot(cleric.spellcasting, 2) is True

    def test_has_slot_upcast(self, cleric: Character) -> None:
        # No level 3 slot, but level 2 slot upcasts to 3.
        assert has_slot(cleric.spellcasting, 3) is False
        # But there's a level 2 slot — can upcast a level-1 spell to L2.
        assert has_slot(cleric.spellcasting, 1) is True

    def test_has_slot_no_slots(self, cleric: Character) -> None:
        cleric.spellcasting.spell_slots = {}
        assert has_slot(cleric.spellcasting, 1) is False

    def test_consume_exact_level(self, cleric: Character) -> None:
        used = consume_slot(cleric.spellcasting, 1)
        assert used == 1
        assert cleric.spellcasting.spell_slots[1] == 3

    def test_consume_upcasts_to_lowest_available(self, cleric: Character) -> None:
        # Drain L1 entirely; a L1 spell must now upcast to L2.
        cleric.spellcasting.spell_slots[1] = 0
        used = consume_slot(cleric.spellcasting, 1)
        assert used == 2
        assert cleric.spellcasting.spell_slots[2] == 1

    def test_consume_raises_when_no_slots(self, cleric: Character) -> None:
        cleric.spellcasting.spell_slots = {}
        with pytest.raises(ValueError, match="No spell slot"):
            consume_slot(cleric.spellcasting, 1)

    def test_refill_slots(self, cleric: Character) -> None:
        cleric.spellcasting.spell_slots = {1: 0, 2: 0}
        refill_slots(cleric.spellcasting)
        assert cleric.spellcasting.spell_slots == cleric.spellcasting.spell_slots_max

    def test_available_slot_levels_sorted(self, cleric: Character) -> None:
        cleric.spellcasting.spell_slots = {2: 1, 1: 2}
        levels = available_slot_levels(cleric.spellcasting)
        assert levels == [1, 2]

    def test_slot_levels_for_level_helper(self) -> None:
        slots = slot_levels_for_level("cleric", 3)
        assert slots == {1: 4, 2: 2}


# ---------------------------------------------------------------------------
# Preparation
# ---------------------------------------------------------------------------


class TestPreparation:
    def test_can_prepare_count_cleric(self, cleric: Character) -> None:
        # WIS mod 3 + level 3 = 6 (min 1).
        assert can_prepare_count(cleric) == 6

    def test_can_know_count_sorcerer(self, sorcerer: Character) -> None:
        # Sorcerer L3 knows 4 spells.
        assert can_know_count(sorcerer) == 4

    def test_prepare_spell_adds(self, cleric: Character) -> None:
        # Drain prepared list, then re-prepare "Cure Wounds".
        cleric.spellcasting.spells_prepared = []
        prepare_spell(cleric, "Cure Wounds")
        assert "Cure Wounds" in cleric.spellcasting.spells_prepared

    def test_prepare_idempotent(self, cleric: Character) -> None:
        before = list(cleric.spellcasting.spells_prepared)
        prepare_spell(cleric, "Cure Wounds")
        assert cleric.spellcasting.spells_prepared == before

    def test_prepare_unknown_spell_raises(self, cleric: Character) -> None:
        with pytest.raises(ValueError, match="Unknown spell"):
            prepare_spell(cleric, "Fakespell")

    def test_prepare_wrong_class_raises(self, cleric: Character) -> None:
        # Fireball is wizard/sorcerer, not cleric.
        cleric.spellcasting.spells_prepared = []
        with pytest.raises(ValueError, match="not on the Cleric spell list"):
            prepare_spell(cleric, "Fireball")

    def test_prepare_count_cap(self, cleric: Character) -> None:
        # Force prepared list to the cap; next prepare should fail.
        cleric.spellcasting.spells_prepared = [
            "Cure Wounds", "Bless", "Healing Word",
            "Shield of Faith", "Guidance", "Sacred Flame",
        ]
        with pytest.raises(ValueError, match="can prepare"):
            prepare_spell(cleric, "Light")

    def test_unprepare_removes(self, cleric: Character) -> None:
        unprepare_spell(cleric, "Bless")
        assert "Bless" not in cleric.spellcasting.spells_prepared

    def test_unprepare_no_spellcasting_is_noop(self) -> None:
        fighter = Character(
            id="f", name="Fighter", race="Human", class_="Fighter", level=1,
            background="Soldier", alignment="CN",
            abilities=AbilityScores(
                strength=10, dexterity=10, constitution=10,
                intelligence=10, wisdom=10, charisma=10,
            ),
            hp_current=10, hp_max=10, armor_class=14, speed=30,
            proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=1,
        )
        unprepare_spell(fighter, "Cure Wounds")  # must not raise

    def test_can_cast_as_prepared(self, cleric: Character) -> None:
        assert can_cast_as_prepared(cleric, "Cure Wounds") is True
        assert can_cast_as_prepared(cleric, "Fireball") is False

    def test_can_cast_as_known(self, sorcerer: Character) -> None:
        assert can_cast_as_known(sorcerer, "Burning Hands") is True
        assert can_cast_as_known(sorcerer, "Cure Wounds") is False


# ---------------------------------------------------------------------------
# Concentration
# ---------------------------------------------------------------------------


class TestConcentration:
    def test_dc_floor_10(self) -> None:
        assert concentration_dc(0) == 10
        assert concentration_dc(10) == 10  # 10 // 2 = 5, but floor is 10
        assert concentration_dc(19) == 10

    def test_dc_half_damage(self) -> None:
        assert concentration_dc(20) == 10  # exactly half
        assert concentration_dc(22) == 11
        assert concentration_dc(100) == 50

    def test_start_sets_concentration(self, cleric: Character) -> None:
        # Drop current concentration first
        cleric.spellcasting.concentration = None
        start_concentration(cleric, "Bless")
        assert cleric.spellcasting.concentration == "Bless"

    def test_start_idempotent_same_spell(self, cleric: Character) -> None:
        # Already concentrating on Bless.
        start_concentration(cleric, "Bless")
        assert cleric.spellcasting.concentration == "Bless"

    def test_start_raises_when_other_concentration_active(
        self, cleric: Character,
    ) -> None:
        with pytest.raises(ValueError, match="already concentrating"):
            start_concentration(cleric, "Shield of Faith")

    def test_break_returns_spell_name(self, cleric: Character) -> None:
        dropped = break_concentration(cleric)
        assert dropped == "Bless"
        assert cleric.spellcasting.concentration is None

    def test_break_no_concentration_returns_none(self, sorcerer: Character) -> None:
        assert break_concentration(sorcerer) is None

    def test_concentration_save_no_conc_is_noop(self, sorcerer: Character) -> None:
        result = concentration_save(sorcerer, 30, rng=random.Random(1))
        assert result.broken is False
        assert result.save_dc == 0

    def test_concentration_save_zero_damage_noop(self, cleric: Character) -> None:
        result = concentration_save(cleric, 0, rng=random.Random(1))
        assert result.broken is False
        assert result.save_dc == 0

    def test_concentration_save_natural_20_succeeds(self, cleric: Character) -> None:
        # Seed 7 of random.Random may not produce 20 — use AlwaysTwenty.
        class AlwaysTwenty:
            def randint(self, a, b):
                return 20
        result = concentration_save(cleric, 30, rng=AlwaysTwenty())  # type: ignore[arg-type]
        assert result.broken is False
        assert result.save_dc == 15

    def test_concentration_save_natural_1_breaks(self, cleric: Character) -> None:
        class AlwaysOne:
            def randint(self, a, b):
                return 1
        result = concentration_save(cleric, 10, rng=AlwaysOne())  # type: ignore[arg-type]
        assert result.broken is True
        assert cleric.spellcasting.concentration is None

    def test_concentration_save_returns_result_type(self, cleric: Character) -> None:
        result = concentration_save(cleric, 12, rng=random.Random(42))
        assert isinstance(result, ConcentrationResult)


# ---------------------------------------------------------------------------
# Rituals
# ---------------------------------------------------------------------------


class TestRituals:
    def test_can_cast_as_ritual_cleric_true(self, cleric: Character) -> None:
        # "Detect Magic" is a ritual on cleric list (PHB).
        cleric.spellcasting.spells_prepared.append("Detect Magic")
        cleric.spellcasting.spells_known.append("Detect Magic")
        allowed, _ = can_cast_as_ritual(cleric, "Detect Magic")
        assert allowed is True

    def test_can_cast_as_ritual_sorcerer_false(self, sorcerer: Character) -> None:
        # Sorcerer has no ritual casting.
        sorcerer.spellcasting.spells_known.append("Detect Magic")
        allowed, reason = can_cast_as_ritual(sorcerer, "Detect Magic")
        assert allowed is False
        assert "ritual" in reason.lower()

    def test_can_cast_as_ritual_non_ritual_spell(self, cleric: Character) -> None:
        # "Cure Wounds" is not a ritual.
        cleric.spellcasting.spells_prepared.append("Cure Wounds")
        allowed, reason = can_cast_as_ritual(cleric, "Cure Wounds")
        assert allowed is False
        assert "not a ritual" in reason.lower()

    def test_cast_as_ritual_does_not_consume_slot(self, cleric: Character) -> None:
        cleric.spellcasting.spells_prepared.append("Detect Magic")
        cleric.spellcasting.spells_known.append("Detect Magic")
        before = dict(cleric.spellcasting.spell_slots)
        result = cast_as_ritual(cleric, "Detect Magic")
        assert isinstance(result, RitualResult)
        assert result.success is True
        assert cleric.spellcasting.spell_slots == before

    def test_cast_as_ritual_failure(self, sorcerer: Character) -> None:
        sorcerer.spellcasting.spells_known.append("Detect Magic")
        result = cast_as_ritual(sorcerer, "Detect Magic")
        assert result.success is False


# ---------------------------------------------------------------------------
# cast_spell — entry point
# ---------------------------------------------------------------------------


class TestCastSpell:
    def test_cantrip_no_slot(self, sorcerer: Character) -> None:
        before = dict(sorcerer.spellcasting.spell_slots)
        result = cast_spell(sorcerer, "Fire Bolt")
        assert result.success is True
        assert result.slot_level_used == 0
        assert sorcerer.spellcasting.spell_slots == before

    def test_unlearned_cantrip_fails(self, sorcerer: Character) -> None:
        result = cast_spell(sorcerer, "Ray of Frost")
        assert result.success is False
        assert "not known" in result.error

    def test_prepared_spell_consumes_slot(self, cleric: Character) -> None:
        result = cast_spell(cleric, "Cure Wounds")
        assert result.success is True
        assert result.slot_level_used == 1
        assert cleric.spellcasting.spell_slots[1] == 3

    def test_known_spell_consumes_slot(self, sorcerer: Character) -> None:
        result = cast_spell(sorcerer, "Burning Hands")
        assert result.success is True
        assert result.slot_level_used == 1
        assert sorcerer.spellcasting.spell_slots[1] == 3

    def test_unprepared_fails_prepared_caster(self, cleric: Character) -> None:
        cleric.spellcasting.spells_prepared = []
        result = cast_spell(cleric, "Cure Wounds")
        assert result.success is False
        assert "not prepared" in result.error

    def test_unknown_fails_known_caster(self, sorcerer: Character) -> None:
        result = cast_spell(sorcerer, "Fireball")  # sorcerer doesn't know it
        assert result.success is False

    def test_no_slot_fails(self, cleric: Character) -> None:
        cleric.spellcasting.spell_slots = {}
        result = cast_spell(cleric, "Cure Wounds")
        assert result.success is False
        assert "no slot" in result.error

    def test_upcast_consumes_higher_slot(self, cleric: Character) -> None:
        # Cleric only has L1 and L2; upcasting L1 spell to L2 should consume L2.
        result = cast_spell(cleric, "Cure Wounds", slot_level=2)
        assert result.success is True
        assert result.upcast is True
        assert result.slot_level_used == 2
        assert cleric.spellcasting.spell_slots[2] == 1

    def test_undercast_rejected(self, cleric: Character) -> None:
        # Try to cast a L2 spell at L1 — invalid.
        # Add a L2 spell to prepared list.
        cleric.spellcasting.spells_prepared.append("Spiritual Weapon")
        result = cast_spell(cleric, "Spiritual Weapon", slot_level=1)
        assert result.success is False
        assert "below" in result.error

    def test_wrong_class_list(self, cleric: Character) -> None:
        cleric.spellcasting.spells_prepared.append("Fireball")
        result = cast_spell(cleric, "Fireball")
        assert result.success is False
        assert "not on" in result.error

    def test_concentration_starts(self, cleric: Character) -> None:
        cleric.spellcasting.concentration = None
        cleric.spellcasting.spells_prepared.append("Bless")
        result = cast_spell(cleric, "Bless")
        assert result.success is True
        assert result.started_concentration is True
        assert cleric.spellcasting.concentration == "Bless"

    def test_concentration_blocked_by_other(
        self, cleric: Character,
    ) -> None:
        # Already on Bless; try to concentrate on something else.
        cleric.spellcasting.spells_prepared.append("Shield of Faith")
        result = cast_spell(cleric, "Shield of Faith")
        assert result.success is True
        assert result.started_concentration is False
        assert cleric.spellcasting.concentration == "Bless"

    def test_non_caster_fails(self) -> None:
        fighter = Character(
            id="f", name="Fighter", race="Human", class_="Fighter", level=1,
            background="Soldier", alignment="CN",
            abilities=AbilityScores(
                strength=10, dexterity=10, constitution=10,
                intelligence=10, wisdom=10, charisma=10,
            ),
            hp_current=10, hp_max=10, armor_class=14, speed=30,
            proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=1,
        )
        result = cast_spell(fighter, "Fireball")
        assert result.success is False
        assert "cannot cast" in result.error

    def test_unknown_spell_name(self, sorcerer: Character) -> None:
        result = cast_spell(sorcerer, "NoSuchSpell")
        assert result.success is False
        assert "unknown" in result.error

    def test_targets_carried(self, sorcerer: Character) -> None:
        target = NPC(
            id="o1", name="Orc", hp_current=10, hp_max=10,
            armor_class=13, speed=30,
            abilities=AbilityScores(
                strength=10, dexterity=10, constitution=10,
                intelligence=10, wisdom=10, charisma=10,
            ),
        )
        result = cast_spell(sorcerer, "Burning Hands", targets=[target])
        assert result.success is True
        assert target.id in result.target_ids


# ---------------------------------------------------------------------------
# CombatEngine integration: CAST_SPELL handler
# ---------------------------------------------------------------------------


class TestCastSpellHandler:
    def test_handler_registered(self) -> None:
        assert ActionType.CAST_SPELL in _ACTION_HANDLERS

    def test_handler_runs_cantrip(self, sorcerer: Character) -> None:
        sm = StateManager(_make_state_with(sorcerer))
        action = Action(
            actor_id=sorcerer.id,
            action_type=ActionType.CAST_SPELL,
            params={"spell": "Fire Bolt"},
        )
        result = _ACTION_HANDLERS[ActionType.CAST_SPELL](
            _TestEngine(), sm, action,
        )
        assert result.success is True

    def test_handler_no_spell_param(self, sorcerer: Character) -> None:
        sm = StateManager(_make_state_with(sorcerer))
        action = Action(
            actor_id=sorcerer.id,
            action_type=ActionType.CAST_SPELL,
            params={},
        )
        result = _ACTION_HANDLERS[ActionType.CAST_SPELL](
            _TestEngine(), sm, action,
        )
        assert result.success is False


def _make_state_with(character: Character):
    from datetime import datetime
    from auto_dm.state.models import GameState
    return GameState(
        campaign_name="test",
        started_at=datetime.now(),
        party=[character],
        player_character_id=character.id,
    )


class _TestEngine:
    """Minimal stand-in for CombatEngine — only ``rng`` is needed."""

    rng = random.Random(0)


# ---------------------------------------------------------------------------
# Concentration break-on-damage (CombatEngine integration via concentration_save)
# ---------------------------------------------------------------------------


class TestConcentrationBreakOnDamage:
    def test_high_damage_breaks_low_con_save(self) -> None:
        # WIS 10 -> CON save mod 0. Damage 30 -> DC 15.
        # A nat-1 always breaks.
        char = Character(
            id="c", name="C", race="Human", class_="Wizard", level=3,
            background="Sage", alignment="N",
            abilities=AbilityScores(
                strength=10, dexterity=10, constitution=10,
                intelligence=10, wisdom=10, charisma=10,
            ),
            hp_current=20, hp_max=20, armor_class=12, speed=30,
            proficiency_bonus=2, hit_dice="1d6", hit_dice_remaining=3,
            spellcasting=Spellcasting(
                ability=Ability.INT, save_dc=12, attack_bonus=4,
                spells_known=[], spells_prepared=["Web"],
                spell_slots={2: 1}, spell_slots_max={2: 1},
                concentration="Web",
            ),
        )

        class AlwaysOne:
            def randint(self, a, b):
                return 1

        result = concentration_save(char, 30, rng=AlwaysOne())  # type: ignore[arg-type]
        assert result.broken is True
        assert char.spellcasting.concentration is None

    def test_low_damage_easy_save(self) -> None:
        char = Character(
            id="c", name="C", race="Human", class_="Wizard", level=3,
            background="Sage", alignment="N",
            abilities=AbilityScores(
                strength=10, dexterity=10, constitution=16,
                intelligence=10, wisdom=10, charisma=10,
            ),  # CON 16 -> +3
            hp_current=20, hp_max=20, armor_class=12, speed=30,
            proficiency_bonus=2, hit_dice="1d6", hit_dice_remaining=3,
            spellcasting=Spellcasting(
                ability=Ability.INT, save_dc=12, attack_bonus=4,
                spells_known=[], spells_prepared=["Web"],
                spell_slots={2: 1}, spell_slots_max={2: 1},
                concentration="Web",
            ),
        )
        # DC 10 (damage 0). AlwaysTwenty always passes.
        class AlwaysTwenty:
            def randint(self, a, b):
                return 20

        result = concentration_save(char, 0, rng=AlwaysTwenty())  # type: ignore[arg-type]
        assert result.broken is False  # 0 damage = no save at all
        # Now with damage 1, DC stays 10.
        result = concentration_save(char, 1, rng=AlwaysTwenty())  # type: ignore[arg-type]
        assert result.broken is False

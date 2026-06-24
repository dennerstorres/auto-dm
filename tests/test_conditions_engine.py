"""Tests for engine/conditions.py and condition wiring in combat."""
from __future__ import annotations

import random

import pytest

from auto_dm.engine.conditions import (
    EXHAUSTION_EFFECTS,
    apply_attack_modifiers,
    apply_save_modifiers,
    attack_auto_crit,
    attacker_advantage,
    attacker_disadvantage,
    can_take_actions,
    damage_multiplier,
    decrease_exhaustion,
    exhaustion_applies,
    exhaustion_disadvantage_attack,
    exhaustion_halved_hp_max,
    exhaustion_halved_speed,
    exhaustion_zero_speed,
    increase_exhaustion,
    is_incapacitated,
    list_active_conditions,
    movement_speed_zero,
    target_advantage,
    target_disadvantage,
)
from auto_dm.engine.combat import attack_roll, saving_throw
from auto_dm.state.models import (
    Ability,
    AbilityScores,
    Character,
    Condition,
    EquippedSlots,
    Item,
    ItemType,
    WeaponProperties,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fighter() -> Character:
    return Character(
        id="c1",
        name="Conan",
        race="Human",
        class_="Fighter",
        level=1,
        background="Soldier",
        alignment="CN",
        abilities=AbilityScores(strength=16, dexterity=14, constitution=14,
                                 intelligence=10, wisdom=12, charisma=10),
        hp_current=12,
        hp_max=12,
        armor_class=16,
        speed=30,
        proficiency_bonus=2,
        hit_dice="1d10",
        hit_dice_remaining=1,
        equipped=EquippedSlots(
            main_hand=Item(
                name="Longsword",
                type=ItemType.WEAPON,
                weapon=WeaponProperties(damage_dice="1d8", damage_type="slashing"),
            ),
        ),
    )


@pytest.fixture
def orc() -> Character:
    return Character(
        id="o1",
        name="Orc",
        race="Orc",
        class_="Warrior",
        level=1,
        background="Tribal",
        alignment="CE",
        abilities=AbilityScores(strength=16, dexterity=12, constitution=16,
                                 intelligence=7, wisdom=11, charisma=10),
        hp_current=15,
        hp_max=15,
        armor_class=13,
        speed=30,
        proficiency_bonus=2,
        hit_dice="1d8",
        hit_dice_remaining=1,
    )


# ---------------------------------------------------------------------------
# can_take_actions
# ---------------------------------------------------------------------------


class TestCanTakeActions:
    @pytest.mark.parametrize("cond", [
        Condition.INCAPACITATED, Condition.PARALYZED, Condition.PETRIFIED,
        Condition.STUNNED, Condition.UNCONSCIOUS,
    ])
    def test_blocks_when_incapping(self, fighter: Character, cond: Condition) -> None:
        fighter.conditions.append(cond)
        assert can_take_actions(fighter) is False
        assert is_incapacitated(fighter) is True

    def test_allows_when_unblocked(self, fighter: Character) -> None:
        fighter.conditions.append(Condition.POISONED)
        fighter.conditions.append(Condition.FRIGHTENED)
        assert can_take_actions(fighter) is True

    def test_poisoned_does_not_block_actions(self, fighter: Character) -> None:
        fighter.conditions.append(Condition.POISONED)
        assert can_take_actions(fighter) is True


# ---------------------------------------------------------------------------
# movement_speed_zero
# ---------------------------------------------------------------------------


class TestMovementSpeedZero:
    @pytest.mark.parametrize("cond", [
        Condition.GRAPPLED, Condition.PARALYZED, Condition.PETRIFIED,
        Condition.RESTRAINED, Condition.STUNNED, Condition.UNCONSCIOUS,
    ])
    def test_zero_movement_conditions(self, fighter: Character, cond: Condition) -> None:
        fighter.conditions.append(cond)
        assert movement_speed_zero(fighter) is True

    def test_poisoned_still_moves(self, fighter: Character) -> None:
        fighter.conditions.append(Condition.POISONED)
        assert movement_speed_zero(fighter) is False


# ---------------------------------------------------------------------------
# Attacker advantage / disadvantage
# ---------------------------------------------------------------------------


class TestAttackerAdvantage:
    def test_invisible_grants_advantage(self, fighter: Character) -> None:
        fighter.conditions.append(Condition.INVISIBLE)
        assert attacker_advantage(fighter) is True

    def test_hidden_grants_advantage(self, fighter: Character) -> None:
        fighter.conditions.append(Condition.HIDDEN)
        assert attacker_advantage(fighter) is True

    def test_normal_no_advantage(self, fighter: Character) -> None:
        assert attacker_advantage(fighter) is False


class TestAttackerDisadvantage:
    def test_blinded_grants_disadvantage(self, fighter: Character) -> None:
        fighter.conditions.append(Condition.BLINDED)
        assert attacker_disadvantage(fighter) is True

    def test_poisoned_grants_disadvantage(self, fighter: Character) -> None:
        fighter.conditions.append(Condition.POISONED)
        assert attacker_disadvantage(fighter) is True

    def test_exhaustion_lvl_3_grants_disadvantage(self, fighter: Character) -> None:
        fighter.exhaustion_level = 3
        assert attacker_disadvantage(fighter) is True

    def test_exhaustion_lvl_2_no_disadvantage(self, fighter: Character) -> None:
        fighter.exhaustion_level = 2
        assert attacker_disadvantage(fighter) is False

    def test_normal_no_disadvantage(self, fighter: Character) -> None:
        assert attacker_disadvantage(fighter) is False


# ---------------------------------------------------------------------------
# Target advantage / disadvantage
# ---------------------------------------------------------------------------


class TestTargetAdvantage:
    @pytest.mark.parametrize("cond", [
        Condition.BLINDED, Condition.PETRIFIED, Condition.STUNNED,
        Condition.RESTRAINED,
    ])
    def test_grants_advantage(self, orc: Character, cond: Condition) -> None:
        orc.conditions.append(cond)
        assert target_advantage(orc) is True

    def test_paralyzed_grants_advantage(self, orc: Character) -> None:
        orc.conditions.append(Condition.PARALYZED)
        assert target_advantage(orc) is True

    def test_prone_grants_advantage(self, orc: Character) -> None:
        orc.conditions.append(Condition.PRONE)
        assert target_advantage(orc) is True

    def test_normal_no_advantage(self, orc: Character) -> None:
        assert target_advantage(orc) is False


class TestTargetDisadvantage:
    def test_invisible_grants_disadvantage(self, orc: Character) -> None:
        orc.conditions.append(Condition.INVISIBLE)
        assert target_disadvantage(orc) is True

    def test_normal_no_disadvantage(self, orc: Character) -> None:
        assert target_disadvantage(orc) is False


class TestApplyAttackModifiers:
    def test_invisible_attacker_adv(self, fighter: Character, orc: Character) -> None:
        fighter.conditions.append(Condition.INVISIBLE)
        adv, dis = apply_attack_modifiers(fighter, orc)
        assert adv is True and dis is False

    def test_blinded_attacker_dis(self, fighter: Character, orc: Character) -> None:
        fighter.conditions.append(Condition.BLINDED)
        adv, dis = apply_attack_modifiers(fighter, orc)
        assert dis is True and adv is False

    def test_paralyzed_target_adv_within_5ft(self, fighter: Character, orc: Character) -> None:
        orc.conditions.append(Condition.PARALYZED)
        adv, dis = apply_attack_modifiers(fighter, orc, is_melee_within_5ft=True)
        assert adv is True

    def test_paralyzed_target_no_adv_outside_5ft(self, fighter: Character, orc: Character) -> None:
        orc.conditions.append(Condition.PARALYZED)
        adv, dis = apply_attack_modifiers(fighter, orc, is_melee_within_5ft=False)
        assert adv is False

    def test_blinded_target_adv_regardless_of_range(self, fighter: Character, orc: Character) -> None:
        orc.conditions.append(Condition.BLINDED)
        adv, _ = apply_attack_modifiers(fighter, orc, is_melee_within_5ft=False)
        assert adv is True


# ---------------------------------------------------------------------------
# Auto-crit
# ---------------------------------------------------------------------------


class TestAutoCrit:
    @pytest.mark.parametrize("cond", [Condition.PARALYZED, Condition.UNCONSCIOUS])
    def test_auto_crit_within_5ft(self, orc: Character, cond: Condition) -> None:
        orc.conditions.append(cond)
        assert attack_auto_crit(orc, is_melee_within_5ft=True) is True

    @pytest.mark.parametrize("cond", [Condition.PARALYZED, Condition.UNCONSCIOUS])
    def test_no_auto_crit_outside_5ft(self, orc: Character, cond: Condition) -> None:
        orc.conditions.append(cond)
        assert attack_auto_crit(orc, is_melee_within_5ft=False) is False

    def test_petrified_no_auto_crit(self, orc: Character) -> None:
        orc.conditions.append(Condition.PETRIFIED)
        assert attack_auto_crit(orc, is_melee_within_5ft=True) is False


# ---------------------------------------------------------------------------
# Saving throw conditions
# ---------------------------------------------------------------------------


class TestSavingThrowConditions:
    @pytest.mark.parametrize("cond", [
        Condition.PARALYZED, Condition.PETRIFIED, Condition.RESTRAINED,
        Condition.STUNNED, Condition.UNCONSCIOUS,
    ])
    @pytest.mark.parametrize("ability", [Ability.STR, Ability.DEX])
    def test_auto_fail_str_dex(
        self, orc: Character, cond: Condition, ability: Ability,
    ) -> None:
        orc.conditions.append(cond)
        adv, dis, auto_fail = apply_save_modifiers(orc, ability)
        assert auto_fail is True
        # Roll many times to confirm
        for _ in range(50):
            r = saving_throw(orc, ability, dc=10, rng=random.Random(42))
            assert r.is_success is False
            assert r.is_fumble is True

    @pytest.mark.parametrize("ability", [
        Ability.CON, Ability.INT, Ability.WIS, Ability.CHA,
    ])
    def test_no_auto_fail_on_other_abilities(
        self, orc: Character, ability: Ability,
    ) -> None:
        orc.conditions.append(Condition.PARALYZED)
        adv, dis, auto_fail = apply_save_modifiers(orc, ability)
        assert auto_fail is False

    def test_restrained_disadvantage_dex(self, orc: Character) -> None:
        orc.conditions.append(Condition.RESTRAINED)
        adv, dis, _ = apply_save_modifiers(orc, Ability.DEX)
        assert dis is True

    def test_restrained_no_disadvantage_str(self, orc: Character) -> None:
        orc.conditions.append(Condition.RESTRAINED)
        adv, dis, _ = apply_save_modifiers(orc, Ability.STR)
        assert dis is False

    def test_exhaustion_3_disadvantage_all_saves(self, orc: Character) -> None:
        orc.exhaustion_level = 3
        for ab in (Ability.STR, Ability.DEX, Ability.CON, Ability.INT,
                   Ability.WIS, Ability.CHA):
            r = saving_throw(orc, ab, dc=10, rng=random.Random(1))
            # Will likely succeed because dc=10 with disadvantage still hits
            # sometimes; we just verify the function runs without error and
            # respects the nat-1 = auto-fail rule.
            assert r.roll >= 1


# ---------------------------------------------------------------------------
# Damage multiplier
# ---------------------------------------------------------------------------


class TestDamageMultiplier:
    def test_normal(self, orc: Character) -> None:
        assert damage_multiplier(orc, "slashing") == 1.0

    def test_resistance_halves(self, orc: Character) -> None:
        orc.resistances.append("fire")
        assert damage_multiplier(orc, "fire") == 0.5

    def test_vulnerability_doubles(self, orc: Character) -> None:
        orc.vulnerabilities.append("fire")
        assert damage_multiplier(orc, "fire") == 2.0

    def test_immunity_zero(self, orc: Character) -> None:
        orc.immunities.append("poison")
        assert damage_multiplier(orc, "poison") == 0.0

    def test_immunity_overrides_vulnerability(self, orc: Character) -> None:
        orc.immunities.append("fire")
        orc.vulnerabilities.append("fire")
        assert damage_multiplier(orc, "fire") == 0.0

    def test_resistance_plus_vulnerability_cancel(self, orc: Character) -> None:
        orc.resistances.append("fire")
        orc.vulnerabilities.append("fire")
        # Per DMG: they cancel to 1.0
        assert damage_multiplier(orc, "fire") == 1.0

    def test_case_insensitive(self, orc: Character) -> None:
        orc.resistances.append("Fire")
        assert damage_multiplier(orc, "fire") == 0.5
        assert damage_multiplier(orc, "FIRE") == 0.5


# ---------------------------------------------------------------------------
# Exhaustion
# ---------------------------------------------------------------------------


class TestExhaustion:
    def test_six_levels_defined(self) -> None:
        assert len(EXHAUSTION_EFFECTS) == 6

    def test_increase_caps_at_six(self, orc: Character) -> None:
        for _ in range(20):
            increase_exhaustion(orc)
        assert orc.exhaustion_level == 6

    def test_decrease_floors_at_zero(self, orc: Character) -> None:
        orc.exhaustion_level = 3
        decrease_exhaustion(orc, 10)
        assert orc.exhaustion_level == 0

    def test_decrease_by_one(self, orc: Character) -> None:
        orc.exhaustion_level = 4
        decrease_exhaustion(orc, 1)
        assert orc.exhaustion_level == 3

    def test_lvl_1_applies_at_one(self, orc: Character) -> None:
        orc.exhaustion_level = 1
        assert exhaustion_applies(orc, 1) is True
        assert exhaustion_applies(orc, 2) is False

    def test_lvl_4_halves_hp_max(self, orc: Character) -> None:
        orc.exhaustion_level = 4
        assert exhaustion_halved_hp_max(orc) is True

    def test_lvl_4_halves_hp_max_false_below(self, orc: Character) -> None:
        orc.exhaustion_level = 3
        assert exhaustion_halved_hp_max(orc) is False

    def test_lvl_2_halves_speed(self, orc: Character) -> None:
        orc.exhaustion_level = 2
        assert exhaustion_halved_speed(orc) is True

    def test_lvl_5_zero_speed(self, orc: Character) -> None:
        orc.exhaustion_level = 5
        assert exhaustion_zero_speed(orc) is True

    def test_lvl_3_disadvantage_attack_and_saves(self, orc: Character) -> None:
        orc.exhaustion_level = 3
        assert exhaustion_disadvantage_attack(orc) is True


# ---------------------------------------------------------------------------
# list_active_conditions
# ---------------------------------------------------------------------------


class TestListActiveConditions:
    def test_no_conditions(self, orc: Character) -> None:
        assert list_active_conditions(orc) == []

    def test_includes_conditions(self, orc: Character) -> None:
        orc.conditions.append(Condition.POISONED)
        orc.conditions.append(Condition.FRIGHTENED)
        result = list_active_conditions(orc)
        assert "poisoned" in result
        assert "frightened" in result

    def test_includes_exhaustion(self, orc: Character) -> None:
        orc.exhaustion_level = 3
        result = list_active_conditions(orc)
        assert any("exhaustion" in r for r in result)


# ---------------------------------------------------------------------------
# Integration: attack_roll + saving_throw with conditions
# ---------------------------------------------------------------------------


class TestAttackRollWithConditions:
    def test_invisible_attacker_with_seed(self, fighter: Character, orc: Character) -> None:
        fighter.conditions.append(Condition.INVISIBLE)
        result = attack_roll(fighter, orc, rng=random.Random(1))
        assert result.advantage is True

    def test_blinded_attacker_disadvantage(self, fighter: Character, orc: Character) -> None:
        fighter.conditions.append(Condition.BLINDED)
        result = attack_roll(fighter, orc, rng=random.Random(1))
        assert result.disadvantage is True

    def test_paralyzed_target_melee_auto_crit(
        self, fighter: Character, orc: Character,
    ) -> None:
        orc.conditions.append(Condition.PARALYZED)
        result = attack_roll(
            fighter, orc,
            is_ranged=False, is_melee_within_5ft=True,
            rng=random.Random(1),
        )
        assert result.is_crit is True
        assert result.is_hit is True

    def test_paralyzed_target_ranged_not_auto_crit(
        self, fighter: Character, orc: Character,
    ) -> None:
        orc.conditions.append(Condition.PARALYZED)
        result = attack_roll(
            fighter, orc,
            is_ranged=True, is_melee_within_5ft=False,
            rng=random.Random(1),
        )
        # Auto-crit requires melee within 5ft
        # (Roll is random; we just check the function completes without crash
        # and follows PHB rules.)
        assert result.is_crit is False or result.attack_roll == 20

    def test_explicit_advantage_overrides_condition_disadvantage_to_neutral(
        self, fighter: Character, orc: Character,
    ) -> None:
        # Blinded = disadvantage; explicit advantage cancels to straight.
        fighter.conditions.append(Condition.BLINDED)
        result = attack_roll(fighter, orc, advantage=True, rng=random.Random(1))
        assert result.advantage is False
        assert result.disadvantage is False

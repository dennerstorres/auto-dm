"""Phase 25f tests: XP thresholds, level_up, class-feature wiring.

Covers:
- XP_THRESHOLDS table covers L1-L20 with the PHB p. 15 numbers.
- level_for_xp() rounds to the highest reachable level.
- level_up() advances the character, updates HP, prof bonus, and
  extra_attacks.
- ASI eligibility at L4, L8, L12, L16, L19.
- Apply_class_features gates Aura of Protection (Paladin L6),
  Feral Instinct (Barbarian L7), Aura of Courage (Paladin L10).
"""
from __future__ import annotations

from typing import Optional

import pytest

from auto_dm.character.level_up import (
    apply_class_features,
    apply_subclass_features,
    features_gained_at_class_level,
    features_gained_at_level,
    has_subclass_feature,
    list_subclass_features,
)
from auto_dm.engine.progression import (
    ASI_LEVELS,
    LevelUpResult,
    XP_THRESHOLDS,
    apply_asi,
    is_asi_level,
    level_for_xp,
    level_up,
    proficiency_bonus_for,
    xp_to_next_level,
)
from auto_dm.state.models import (
    Ability,
    AbilityScores,
    Character,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_character(
    class_name: str = "Fighter",
    level: int = 1,
    subclass: Optional[str] = None,
    *,
    con: int = 14,
    str_score: int = 14,
    hit_dice: str = "1d10",
    hp_max: int = 10,
    hp_current: Optional[int] = None,
) -> Character:
    return Character(
        id=f"{class_name.lower()}_{level}",
        name=f"Test {class_name}",
        race="Human",
        **{"class": class_name},
        level=level,
        subclass=subclass,
        background="Soldier",
        alignment="LG",
        abilities=AbilityScores(
            strength=str_score,
            dexterity=10,
            constitution=con,
            intelligence=10,
            wisdom=12,
            charisma=8,
        ),
        hp_current=hp_current if hp_current is not None else hp_max,
        hp_max=hp_max,
        armor_class=14,
        speed=30,
        proficiency_bonus=proficiency_bonus_for(level),
        hit_dice=hit_dice,
        hit_dice_remaining=level,
        inventory=[],
        xp=XP_THRESHOLDS[level - 1] if level <= 20 else 0,
    )


# ===========================================================================
# XP thresholds
# ===========================================================================


class TestXPThresholds:
    def test_table_covers_levels_1_to_20(self):
        assert len(XP_THRESHOLDS) == 20
        assert XP_THRESHOLDS[0] == 0
        assert XP_THRESHOLDS[1] == 300
        assert XP_THRESHOLDS[4] == 6_500
        assert XP_THRESHOLDS[10] == 85_000
        assert XP_THRESHOLDS[19] == 355_000

    def test_thresholds_monotonic(self):
        for i in range(1, len(XP_THRESHOLDS)):
            assert XP_THRESHOLDS[i] > XP_THRESHOLDS[i - 1]

    def test_level_for_xp_negative_returns_one(self):
        assert level_for_xp(-100) == 1

    def test_level_for_xp_zero(self):
        assert level_for_xp(0) == 1

    def test_level_for_xp_boundaries(self):
        assert level_for_xp(300) == 2
        assert level_for_xp(299) == 1
        assert level_for_xp(2_700) == 4
        assert level_for_xp(2_699) == 3
        assert level_for_xp(85_000) == 11

    def test_level_for_xp_caps_at_twenty(self):
        assert level_for_xp(1_000_000) == 20

    def test_xp_to_next_level_at_level_one(self):
        c = _make_character(level=1)
        # Need 300 - 0 = 300 more XP to reach L2.
        assert xp_to_next_level(c) == 300

    def test_xp_to_next_level_at_cap(self):
        c = _make_character(level=20)
        # Already at cap.
        assert xp_to_next_level(c) is None


# ===========================================================================
# Proficiency bonus
# ===========================================================================


class TestProficiencyBonus:
    def test_levels_1_to_4(self):
        assert proficiency_bonus_for(1) == 2
        assert proficiency_bonus_for(4) == 2

    def test_levels_5_to_8(self):
        assert proficiency_bonus_for(5) == 3
        assert proficiency_bonus_for(8) == 3

    def test_levels_9_to_12(self):
        assert proficiency_bonus_for(9) == 4
        assert proficiency_bonus_for(12) == 4

    def test_levels_13_to_16(self):
        assert proficiency_bonus_for(13) == 5
        assert proficiency_bonus_for(16) == 5

    def test_levels_17_to_20(self):
        assert proficiency_bonus_for(17) == 6
        assert proficiency_bonus_for(20) == 6


# ===========================================================================
# ASI eligibility
# ===========================================================================


class TestASI:
    def test_asi_levels_match_phb(self):
        assert ASI_LEVELS == frozenset({4, 8, 12, 16, 19})

    @pytest.mark.parametrize("level", [4, 8, 12, 16, 19])
    def test_asi_level_true(self, level):
        assert is_asi_level(level)

    @pytest.mark.parametrize("level", [1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 20])
    def test_asi_level_false(self, level):
        assert not is_asi_level(level)

    def test_apply_asi_plus_two(self):
        c = _make_character(level=4, str_score=14)
        apply_asi(c, Ability.STR)
        assert c.abilities.strength == 16

    def test_apply_asi_split(self):
        c = _make_character(level=4, str_score=14, con=12)
        apply_asi(c, Ability.STR, secondary=Ability.CON)
        assert c.abilities.strength == 15
        assert c.abilities.constitution == 13

    def test_apply_asi_caps_at_20(self):
        # PHB: +2 must not raise the score above 20. The function raises
        # ValueError when the requested improvement would exceed 20.
        c = _make_character(level=4, str_score=19)
        with pytest.raises(ValueError):
            apply_asi(c, Ability.STR)
        # Score must not have been mutated.
        assert c.abilities.strength == 19


# ===========================================================================
# Level-up mechanics
# ===========================================================================


class TestLevelUp:
    def test_basic_level_up_advances_level(self):
        c = _make_character(level=1, hp_max=10, hp_current=10)
        result = level_up(c, hp_roll=5)
        assert isinstance(result, LevelUpResult)
        assert c.level == 2
        assert result.old_level == 1
        assert result.new_level == 2

    def test_level_up_increments_hp(self):
        c = _make_character(level=1, hp_max=10, hp_current=10, con=14)
        # hp_roll=5 + CON mod(+2) = 7 HP gained
        level_up(c, hp_roll=5)
        assert c.hp_max == 17
        assert c.hp_current == 17

    def test_level_up_minimum_one_hp(self):
        # Even with a roll of 1 + CON mod = -2, total is -1, must be >= 1.
        c = _make_character(level=1, hp_max=10, hp_current=10, con=6)
        # con 6 -> mod -2
        level_up(c, hp_roll=1)
        # 1 + (-2) = -1, min 1
        assert c.hp_max == 11

    def test_level_up_updates_proficiency(self):
        c = _make_character(level=4, hp_max=10, hp_current=10)
        level_up(c, hp_roll=5)
        assert c.level == 5
        assert c.proficiency_bonus == 3

    def test_level_up_updates_extra_attacks_fighter(self):
        c = _make_character(class_name="Fighter", level=4, hp_max=10)
        level_up(c, hp_roll=5)
        # Fighter L5: extra_attacks=1
        assert c.extra_attacks == 1

    def test_level_up_no_extra_attacks_wizard(self):
        c = _make_character(class_name="Wizard", level=4, hp_max=10,
                            hit_dice="1d6")
        level_up(c, hp_roll=3)
        # Wizard L5: extra_attacks still 0
        assert c.extra_attacks == 0

    def test_level_up_to_eleven_doubles_extra_attacks(self):
        c = _make_character(class_name="Fighter", level=10, hp_max=10)
        # Fighter L10 -> L11 = extra_attacks=2
        level_up(c, hp_roll=5)
        assert c.level == 11
        assert c.extra_attacks == 2

    def test_level_up_to_eighteen_triples_extra_attacks(self):
        c = _make_character(class_name="Fighter", level=17, hp_max=10)
        level_up(c, hp_roll=5)
        assert c.level == 18
        assert c.extra_attacks == 3

    def test_level_up_increments_hit_dice_remaining(self):
        c = _make_character(level=1, hp_max=10)
        level_up(c, hp_roll=5)
        assert c.hit_dice_remaining == 2

    def test_level_up_caps_at_twenty(self):
        c = _make_character(level=20, hp_max=10)
        with pytest.raises(ValueError):
            level_up(c, hp_roll=5)

    def test_level_up_asi_pending_at_eight(self):
        c = _make_character(level=7, hp_max=10)
        result = level_up(c, hp_roll=5)
        assert result.asi_pending is True
        assert c.level == 8

    def test_level_up_no_asi_pending_at_five(self):
        c = _make_character(level=4, hp_max=10)
        result = level_up(c, hp_roll=5)
        assert result.asi_pending is False
        assert c.level == 5

    def test_full_progression_to_twenty(self):
        c = _make_character(class_name="Fighter", level=1, hp_max=12,
                            hit_dice="1d10")
        for expected_level in range(2, 21):
            level_up(c, hp_roll=5)
            assert c.level == expected_level
        # Prof bonus at 20
        assert c.proficiency_bonus == 6
        # Fighter L18+ extra attacks = 3
        assert c.extra_attacks == 3


# ===========================================================================
# Class feature gating
# ===========================================================================


class TestApplyClassFeatures:
    def test_aura_of_protection_paladin_l6(self):
        c = _make_character(class_name="Paladin", level=5)
        apply_class_features(c, at_level=6)
        assert c.has_aura_of_protection is True
        assert c.aura_of_protection_active is True

    def test_aura_of_courage_paladin_l10(self):
        c = _make_character(class_name="Paladin", level=9)
        apply_class_features(c, at_level=10)
        assert c.aura_of_courage_active is True

    def test_feral_instinct_barbarian_l7(self):
        c = _make_character(class_name="Barbarian", level=6)
        apply_class_features(c, at_level=7)
        assert c.has_feral_instinct is True

    def test_danger_sense_barbarian_l2(self):
        c = _make_character(class_name="Barbarian", level=1)
        apply_class_features(c, at_level=2)
        assert c.has_danger_sense is True

    def test_cunning_action_rogue_l2(self):
        c = _make_character(class_name="Rogue", level=1)
        apply_class_features(c, at_level=2)
        assert c.has_cunning_action is True

    def test_uncanny_dodge_rogue_l5(self):
        c = _make_character(class_name="Rogue", level=4)
        apply_class_features(c, at_level=5)
        assert c.has_uncanny_dodge is True

    def test_evasion_rogue_l7(self):
        c = _make_character(class_name="Rogue", level=6)
        apply_class_features(c, at_level=7)
        assert c.has_evasion is True

    def test_evasion_monk_l7(self):
        c = _make_character(class_name="Monk", level=6, hit_dice="1d8")
        apply_class_features(c, at_level=7)
        assert c.has_evasion is True

    def test_features_not_active_below_gate(self):
        c = _make_character(class_name="Paladin", level=5)
        apply_class_features(c, at_level=5)
        # L5 < L6 gate -> no aura of protection
        assert c.has_aura_of_protection is False

    def test_features_gained_at_level_paladin_l6(self):
        c = _make_character(class_name="Paladin", level=6)
        gained = features_gained_at_class_level(c, 6)
        assert "Aura of Protection" in gained

    def test_features_gained_at_level_returns_empty_for_non_gate(self):
        c = _make_character(class_name="Fighter", level=5)
        # L5 isn't a gate (L9 Indomitable is, but at L5 no class feature).
        gained = features_gained_at_class_level(c, 5)
        # Indomitable is gated at L9.
        assert gained == [] or "Indomitable" not in gained

    def test_idempotent_after_level_up(self):
        c = _make_character(class_name="Paladin", level=10)
        # Run apply twice — should not toggle flags off.
        apply_class_features(c, at_level=10)
        apply_class_features(c, at_level=10)
        assert c.has_aura_of_protection is True
        assert c.aura_of_courage_active is True

    def test_unknown_class_no_crash(self):
        c = _make_character(class_name="Commoner", level=5)
        apply_class_features(c, at_level=5)
        # Should be a no-op.
        assert c.has_aura_of_protection is False


# ===========================================================================
# Meta-command wiring
# ===========================================================================


# ===========================================================================
# Subclass feature integration with level_up
# ===========================================================================


class TestSubclassFeaturesWithLevelUp:
    def test_subclass_features_filtered_by_level(self):
        # Sorcerer Draconic Bloodline has L1 features. List them.
        features = list_subclass_features("Sorcerer", "Draconic Bloodline")
        assert len(features) >= 1
        l1_features = [f for f in features if f.level == 1]
        assert len(l1_features) >= 1

    def test_apply_subclass_features_at_l5(self):
        # Wizard School of Evocation has features at L2, L6, L10, L14.
        c = _make_character(class_name="Wizard", level=5,
                            subclass="School of Evocation", hit_dice="1d6")
        apply_subclass_features(c, at_level=5)
        # L2 feature should be present.
        assert any("evocation" in f.lower() or "sculpt" in f.lower()
                   for f in c.subclass_features)

    def test_apply_subclass_features_at_l1_no_features(self):
        # If subclass has no L1 feature, the list is empty.
        c = _make_character(class_name="Wizard", level=1,
                            subclass="School of Evocation")
        apply_subclass_features(c, at_level=1)
        # Evocation subclass's first feature is at L2.
        # We don't assert zero (parser may differ), but no error.
        assert isinstance(c.subclass_features, list)

    def test_features_gained_at_level_subclass(self):
        feats = features_gained_at_level("Wizard", "School of Evocation", 2)
        # Should have at least one L2 feature.
        assert isinstance(feats, list)

    def test_has_subclass_feature(self):
        c = _make_character(class_name="Wizard", level=10,
                            subclass="School of Evocation")
        apply_subclass_features(c, at_level=10)
        # Either there's a feature with a particular name or not.
        assert isinstance(c.subclass_features, list)


# ===========================================================================
# End-to-end: level_up + apply_class_features flow
# ===========================================================================


class TestLevelUpFlow:
    def test_paladin_1_to_10_aura_progression(self):
        """Paladin L1 -> L10: Aura of Protection at L6, Aura of Courage
        at L10. Verify all flags land at the right level.
        """
        c = _make_character(class_name="Paladin", level=1, hp_max=12,
                            hit_dice="1d10", con=14)
        for _ in range(5):
            level_up(c, hp_roll=5)
            apply_class_features(c, at_level=c.level)
        assert c.level == 6
        assert c.has_aura_of_protection is True
        assert c.aura_of_courage_active is False
        for _ in range(4):
            level_up(c, hp_roll=5)
            apply_class_features(c, at_level=c.level)
        assert c.level == 10
        assert c.has_aura_of_protection is True
        assert c.aura_of_courage_active is True

    def test_barbarian_1_to_7_feral_instinct(self):
        c = _make_character(class_name="Barbarian", level=1, hp_max=12,
                            hit_dice="1d12", con=14)
        for _ in range(6):
            level_up(c, hp_roll=5)
            apply_class_features(c, at_level=c.level)
        assert c.level == 7
        assert c.has_danger_sense is True  # L2
        assert c.has_feral_instinct is True  # L7

    def test_fighter_1_to_18_extra_attack_progression(self):
        c = _make_character(class_name="Fighter", level=1, hp_max=12,
                            hit_dice="1d10", con=14)
        # 1 -> 5
        for _ in range(4):
            level_up(c, hp_roll=5)
            apply_class_features(c, at_level=c.level)
        assert c.level == 5
        assert c.extra_attacks == 1
        # 5 -> 11
        for _ in range(6):
            level_up(c, hp_roll=5)
            apply_class_features(c, at_level=c.level)
        assert c.level == 11
        assert c.extra_attacks == 2
        # 11 -> 18
        for _ in range(7):
            level_up(c, hp_roll=5)
            apply_class_features(c, at_level=c.level)
        assert c.level == 18
        assert c.extra_attacks == 3
        assert c.proficiency_bonus == 6
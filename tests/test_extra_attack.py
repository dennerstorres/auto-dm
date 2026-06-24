"""Tests for engine/extra_attack.py — Extra Attack feature."""
from __future__ import annotations

from datetime import datetime

import pytest

from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.engine.extra_attack import (
    MARTIAL_CLASSES,
    attacks_per_action,
    extra_attacks_for,
)
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    Action,
    ActionType,
    AbilityScores,
    Character,
    EquippedSlots,
    GameState,
    Item,
    ItemType,
    NPC,
    WeaponProperties,
)


def make_fighter(level: int = 5) -> Character:
    return Character(
        id="f1", name="Conan", race="Human", class_="Fighter", level=level,
        background="Soldier", alignment="CN",
        abilities=AbilityScores(
            strength=16, dexterity=14, constitution=14,
            intelligence=10, wisdom=12, charisma=10,
        ),
        hp_current=12, hp_max=12, armor_class=16, speed=30,
        proficiency_bonus=2 + (level - 1) // 4, hit_dice="1d10", hit_dice_remaining=1,
        extra_attacks=extra_attacks_for("fighter", level),
        equipped=EquippedSlots(
            main_hand=Item(
                name="Longsword",
                type=ItemType.WEAPON,
                weapon=WeaponProperties(damage_dice="1d8", damage_type="slashing"),
            ),
        ),
    )


def make_orc() -> NPC:
    return NPC(
        id="o1", name="Orc", hp_current=15, hp_max=15,
        armor_class=13, speed=30,
        abilities=AbilityScores(
            strength=10, dexterity=10, constitution=10,
            intelligence=10, wisdom=10, charisma=10,
        ),
    )


def make_wizard(level: int = 5) -> Character:
    return Character(
        id="w1", name="Gandalf", race="Human", class_="Wizard", level=level,
        background="Sage", alignment="N",
        abilities=AbilityScores(
            strength=8, dexterity=14, constitution=12,
            intelligence=16, wisdom=12, charisma=10,
        ),
        hp_current=8, hp_max=8, armor_class=12, speed=30,
        proficiency_bonus=2 + (level - 1) // 4, hit_dice="1d6", hit_dice_remaining=1,
        extra_attacks=extra_attacks_for("wizard", level),
    )


def make_state_with(party, npcs):
    return StateManager(GameState(
        campaign_name="test",
        started_at=datetime.now(),
        party=party,
        player_character_id=party[0].id,
        npcs=npcs,
    ))


class TestExtraAttacksFor:
    @pytest.mark.parametrize("level,expected", [
        (1, 0), (4, 0),
        (5, 1), (10, 1),
        (11, 2), (17, 2),
        (18, 3), (20, 3),
    ])
    def test_fighter(self, level, expected):
        assert extra_attacks_for("Fighter", level) == expected

    @pytest.mark.parametrize("cls", ["Fighter", "Barbarian", "Paladin", "Ranger", "Monk"])
    def test_martial_classes_in_set(self, cls):
        assert cls.lower() in MARTIAL_CLASSES

    @pytest.mark.parametrize("cls", ["Wizard", "Sorcerer", "Cleric", "Bard"])
    def test_non_martial_classes_not_in_set(self, cls):
        assert cls.lower() not in MARTIAL_CLASSES

    def test_wizard_never_gets_extra_attack(self):
        for level in range(1, 21):
            assert extra_attacks_for("Wizard", level) == 0

    def test_barbarian_progression(self):
        assert extra_attacks_for("Barbarian", 4) == 0
        assert extra_attacks_for("Barbarian", 5) == 1
        assert extra_attacks_for("Barbarian", 18) == 3


class TestAttacksPerAction:
    def test_default(self):
        f = make_fighter(5)
        assert attacks_per_action(f) == 2

    def test_high_level(self):
        f = make_fighter(20)
        assert attacks_per_action(f) == 4

    def test_wizard_only_one(self):
        w = make_wizard(20)
        assert attacks_per_action(w) == 1


class TestExtraAttackInCombat:
    def test_fighter_can_attack_twice(self):
        f = make_fighter(5)
        orc = make_orc()
        sm = make_state_with([f], [orc])
        engine = CombatEngine(rng=__import__("random").Random(0))
        engine.start_combat(sm)
        # Force initiative order to put fighter first.
        sm.state.initiative_order = [f.id, orc.id]
        sm.state.current_turn_index = 0

        action = Action(
            actor_id=f.id,
            action_type=ActionType.ATTACK,
            target_id=orc.id,
        )
        r1 = engine.execute_action(sm, action)
        assert r1.success is True
        # Should still have 1 attack remaining
        assert engine._attacks_remaining[f.id] == 1
        # Second attack should work
        r2 = engine.execute_action(sm, action)
        assert r2.success is True
        # Now no more attacks
        assert engine._attacks_remaining[f.id] == 0
        # Third attack should fail
        r3 = engine.execute_action(sm, action)
        assert r3.success is False
        assert "todos os ataques" in r3.message.lower() or "ataques disponíveis" in r3.message.lower()

    def test_wizard_can_attack_once(self):
        w = make_wizard(5)
        orc = make_orc()
        sm = make_state_with([w], [orc])
        engine = CombatEngine(rng=__import__("random").Random(0))
        engine.start_combat(sm)
        sm.state.initiative_order = [w.id, orc.id]
        sm.state.current_turn_index = 0

        action = Action(
            actor_id=w.id,
            action_type=ActionType.ATTACK,
            target_id=orc.id,
        )
        r1 = engine.execute_action(sm, action)
        assert r1.success is True
        # Wizard has 1 attack total
        assert engine._attacks_remaining[w.id] == 0
        r2 = engine.execute_action(sm, action)
        assert r2.success is False

    def test_attack_budget_resets_on_next_turn(self):
        f = make_fighter(5)
        orc = make_orc()
        sm = make_state_with([f], [orc])
        engine = CombatEngine(rng=__import__("random").Random(0))
        engine.start_combat(sm)
        sm.state.initiative_order = [f.id, orc.id]
        sm.state.current_turn_index = 0

        # Fighter's first turn: 2 attacks
        action = Action(actor_id=f.id, action_type=ActionType.ATTACK, target_id=orc.id)
        engine.execute_action(sm, action)
        engine.execute_action(sm, action)
        # Now advance to orc's turn and back to fighter
        engine.next_turn(sm)  # to orc
        engine.next_turn(sm)  # back to fighter
        # Budget should be reset to 2
        assert engine._attacks_remaining[f.id] == 2
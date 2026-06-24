"""Tests for Wave B/C/D combat engine handlers.

Covers: SECOND_WIND, ACTION_SURGE, LAY_ON_HANDS, CHANNEL_DIVINITY,
BARDIC_INSPIRATION, FLURRY_OF_BLOWS, STUNNING_STRIKE, UNCANNY_DODGE,
RECKLESS_ATTACK, INDOMITABLE.
"""
from __future__ import annotations

import random
from datetime import datetime

import pytest

from auto_dm.engine.combat_engine import _ACTION_HANDLERS, CombatEngine
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    Action,
    ActionType,
    AbilityScores,
    Character,
    GameState,
    NPC,
)


def make_fighter(level: int = 5) -> Character:
    c = Character(
        id=f"f{random.randint(0,99999)}", name="Conan", race="Human",
        class_="Fighter", level=level, background="Soldier", alignment="CN",
        abilities=AbilityScores(
            strength=16, dexterity=14, constitution=14,
            intelligence=10, wisdom=12, charisma=10,
        ),
        hp_current=20, hp_max=40, armor_class=16, speed=30,
        proficiency_bonus=3, hit_dice="1d10", hit_dice_remaining=level,
    )
    c.action_surges_remaining = 1
    c.second_wind_used = False
    return c


def make_paladin(level: int = 5) -> Character:
    c = Character(
        id=f"p{random.randint(0,99999)}", name="Lyra", race="Human",
        class_="Paladin", level=level, background="Noble", alignment="LG",
        abilities=AbilityScores(
            strength=16, dexterity=10, constitution=14,
            intelligence=10, wisdom=12, charisma=14,
        ),
        hp_current=20, hp_max=40, armor_class=18, speed=30,
        proficiency_bonus=3, hit_dice="1d10", hit_dice_remaining=level,
    )
    c.lay_on_hands_pool = 25
    return c


def make_bard(level: int = 5) -> Character:
    c = Character(
        id=f"b{random.randint(0,99999)}", name="Finn", race="Half-Elf",
        class_="Bard", level=level, background="Entertainer", alignment="CG",
        abilities=AbilityScores(
            strength=10, dexterity=14, constitution=12,
            intelligence=12, wisdom=10, charisma=16,
        ),
        hp_current=30, hp_max=30, armor_class=14, speed=30,
        proficiency_bonus=3, hit_dice="1d8", hit_dice_remaining=level,
    )
    c.bardic_inspiration_die = 8
    c.bardic_inspiration_max = 3
    c.bardic_inspiration_uses = 3
    return c


def make_cleric(level: int = 5) -> Character:
    c = Character(
        id=f"c{random.randint(0,99999)}", name="Mira", race="Hill Dwarf",
        class_="Cleric", level=level, background="Acolyte", alignment="LG",
        abilities=AbilityScores(
            strength=14, dexterity=10, constitution=14,
            intelligence=10, wisdom=16, charisma=10,
        ),
        hp_current=32, hp_max=32, armor_class=18, speed=25,
        proficiency_bonus=3, hit_dice="1d8", hit_dice_remaining=level,
    )
    c.channel_divinity_remaining = 1
    return c


def make_monk(level: int = 5) -> Character:
    c = Character(
        id=f"m{random.randint(0,99999)}", name="Mo", race="Human",
        class_="Monk", level=level, background="Hermit", alignment="LN",
        abilities=AbilityScores(
            strength=12, dexterity=16, constitution=13,
            intelligence=10, wisdom=14, charisma=10,
        ),
        hp_current=30, hp_max=30, armor_class=15, speed=30,
        proficiency_bonus=3, hit_dice="1d8", hit_dice_remaining=level,
    )
    c.ki_max = 5
    c.ki_points = 5
    return c


def make_rogue(level: int = 5) -> Character:
    c = Character(
        id=f"r{random.randint(0,99999)}", name="Vex", race="Half-Elf",
        class_="Rogue", level=level, background="Criminal", alignment="CN",
        abilities=AbilityScores(
            strength=10, dexterity=16, constitution=12,
            intelligence=13, wisdom=12, charisma=14,
        ),
        hp_current=24, hp_max=24, armor_class=14, speed=30,
        proficiency_bonus=2, hit_dice="1d8", hit_dice_remaining=level,
    )
    c.has_uncanny_dodge = True
    return c


def make_barbarian(level: int = 5) -> Character:
    c = Character(
        id=f"bb{random.randint(0,99999)}", name="Grog", race="Half-Orc",
        class_="Barbarian", level=level, background="Outlander",
        alignment="CN",
        abilities=AbilityScores(
            strength=16, dexterity=13, constitution=16,
            intelligence=8, wisdom=12, charisma=10,
        ),
        hp_current=40, hp_max=40, armor_class=14, speed=30,
        proficiency_bonus=3, hit_dice="1d12", hit_dice_remaining=level,
    )
    return c


def make_state(party: list) -> StateManager:
    return StateManager(GameState(
        campaign_name="test",
        started_at=datetime.now(),
        party=party,
        player_character_id=party[0].id,
    ))


def make_target() -> NPC:
    return NPC(
        id="t1", name="Orc", hp_current=20, hp_max=20,
        armor_class=12, speed=30,
        abilities=AbilityScores(
            strength=14, dexterity=10, constitution=12,
            intelligence=7, wisdom=10, charisma=8,
        ),
    )


class TestHandlersRegistered:
    @pytest.mark.parametrize("action_type", [
        ActionType.SECOND_WIND,
        ActionType.ACTION_SURGE,
        ActionType.LAY_ON_HANDS,
        ActionType.CHANNEL_DIVINITY,
        ActionType.BARDIC_INSPIRATION,
        ActionType.FLURRY_OF_BLOWS,
        ActionType.STUNNING_STRIKE,
        ActionType.UNCANNY_DODGE,
        ActionType.RECKLESS_ATTACK,
        ActionType.INDOMITABLE,
    ])
    def test_registered(self, action_type):
        assert action_type in _ACTION_HANDLERS


class TestSecondWind:
    def test_heals(self):
        f = make_fighter()
        f.hp_current = 10
        sm = make_state([f])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=f.id, action_type=ActionType.SECOND_WIND)
        result = engine.execute_action(sm, action)
        assert result.success is True
        assert result.mechanical["heal"] >= 6  # 1d10 + 5
        assert f.hp_current > 10
        assert f.second_wind_used is True

    def test_non_fighter_rejected(self):
        p = make_paladin()
        sm = make_state([p])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=p.id, action_type=ActionType.SECOND_WIND)
        result = engine.execute_action(sm, action)
        assert result.success is False

    def test_already_used_rejected(self):
        f = make_fighter()
        f.second_wind_used = True
        sm = make_state([f])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=f.id, action_type=ActionType.SECOND_WIND)
        result = engine.execute_action(sm, action)
        assert result.success is False


class TestActionSurge:
    def test_spend(self):
        f = make_fighter()
        sm = make_state([f])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=f.id, action_type=ActionType.ACTION_SURGE)
        result = engine.execute_action(sm, action)
        assert result.success is True
        assert f.action_surges_remaining == 0

    def test_no_uses_rejected(self):
        f = make_fighter()
        f.action_surges_remaining = 0
        sm = make_state([f])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=f.id, action_type=ActionType.ACTION_SURGE)
        result = engine.execute_action(sm, action)
        assert result.success is False

    def test_non_fighter_rejected(self):
        p = make_paladin()
        sm = make_state([p])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=p.id, action_type=ActionType.ACTION_SURGE)
        result = engine.execute_action(sm, action)
        assert result.success is False


class TestLayOnHands:
    def test_heal_self(self):
        p = make_paladin()
        p.hp_current = 20
        sm = make_state([p])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=p.id, action_type=ActionType.LAY_ON_HANDS,
            target_id=p.id, params={"amount": 10},
        )
        result = engine.execute_action(sm, action)
        assert result.success is True
        assert p.hp_current == 30
        assert p.lay_on_hands_pool == 15

    def test_insufficient_pool(self):
        p = make_paladin()
        sm = make_state([p])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=p.id, action_type=ActionType.LAY_ON_HANDS,
            target_id=p.id, params={"amount": 100},
        )
        result = engine.execute_action(sm, action)
        assert result.success is False

    def test_non_paladin_rejected(self):
        f = make_fighter()
        sm = make_state([f])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=f.id, action_type=ActionType.LAY_ON_HANDS,
            params={"amount": 5},
        )
        result = engine.execute_action(sm, action)
        assert result.success is False


class TestChannelDivinity:
    def test_use(self):
        c = make_cleric()
        sm = make_state([c])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=c.id, action_type=ActionType.CHANNEL_DIVINITY,
            params={"effect": "turn_undead"},
        )
        result = engine.execute_action(sm, action)
        assert result.success is True
        assert c.channel_divinity_remaining == 0
        assert result.mechanical["effect"] == "turn_undead"

    def test_no_uses_rejected(self):
        c = make_cleric()
        c.channel_divinity_remaining = 0
        sm = make_state([c])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=c.id, action_type=ActionType.CHANNEL_DIVINITY,
        )
        result = engine.execute_action(sm, action)
        assert result.success is False

    def test_non_cleric_rejected(self):
        f = make_fighter()
        sm = make_state([f])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=f.id, action_type=ActionType.CHANNEL_DIVINITY,
        )
        result = engine.execute_action(sm, action)
        assert result.success is False


class TestBardicInspiration:
    def test_grant(self):
        b = make_bard()
        f = make_fighter()
        f.id = "ally1"
        sm = make_state([b, f])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=b.id, action_type=ActionType.BARDIC_INSPIRATION,
            target_id=f.id,
        )
        result = engine.execute_action(sm, action)
        assert result.success is True
        assert b.bardic_inspiration_uses == 2
        assert result.mechanical["die"] == 8

    def test_no_uses_rejected(self):
        b = make_bard()
        b.bardic_inspiration_uses = 0
        sm = make_state([b])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=b.id, action_type=ActionType.BARDIC_INSPIRATION,
        )
        result = engine.execute_action(sm, action)
        assert result.success is False

    def test_non_bard_rejected(self):
        f = make_fighter()
        sm = make_state([f])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=f.id, action_type=ActionType.BARDIC_INSPIRATION,
        )
        result = engine.execute_action(sm, action)
        assert result.success is False


class TestFlurryOfBlows:
    def test_use(self):
        m = make_monk()
        sm = make_state([m])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=m.id, action_type=ActionType.FLURRY_OF_BLOWS)
        result = engine.execute_action(sm, action)
        assert result.success is True
        assert m.ki_points == 4

    def test_no_ki_rejected(self):
        m = make_monk()
        m.ki_points = 0
        sm = make_state([m])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=m.id, action_type=ActionType.FLURRY_OF_BLOWS)
        result = engine.execute_action(sm, action)
        assert result.success is False

    def test_non_monk_rejected(self):
        f = make_fighter()
        sm = make_state([f])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=f.id, action_type=ActionType.FLURRY_OF_BLOWS)
        result = engine.execute_action(sm, action)
        assert result.success is False


class TestStunningStrike:
    def test_spend_ki(self):
        m = make_monk()
        m.ki_points = 1
        target = make_target()
        sm = make_state([m])
        # Need to add target to state
        from auto_dm.state.manager import StateManager as SM
        sm.state.npcs = [target]
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=m.id, action_type=ActionType.STUNNING_STRIKE,
            target_id=target.id,
        )
        result = engine.execute_action(sm, action)
        assert result.success is True
        assert m.ki_points == 0

    def test_no_ki_rejected(self):
        m = make_monk()
        m.ki_points = 0
        target = make_target()
        sm = make_state([m])
        sm.state.npcs = [target]
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=m.id, action_type=ActionType.STUNNING_STRIKE,
            target_id=target.id,
        )
        result = engine.execute_action(sm, action)
        assert result.success is False

    def test_non_monk_rejected(self):
        f = make_fighter()
        target = make_target()
        sm = make_state([f])
        sm.state.npcs = [target]
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=f.id, action_type=ActionType.STUNNING_STRIKE,
            target_id=target.id,
        )
        result = engine.execute_action(sm, action)
        assert result.success is False


class TestUncannyDodge:
    def test_use(self):
        r = make_rogue()
        sm = make_state([r])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=r.id, action_type=ActionType.UNCANNY_DODGE)
        result = engine.execute_action(sm, action)
        assert result.success is True
        assert result.mechanical["halves_next_attack"] is True

    def test_without_feature_rejected(self):
        f = make_fighter()
        f.has_uncanny_dodge = False
        sm = make_state([f])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=f.id, action_type=ActionType.UNCANNY_DODGE)
        result = engine.execute_action(sm, action)
        assert result.success is False


class TestRecklessAttack:
    def test_toggle(self):
        b = make_barbarian()
        sm = make_state([b])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=b.id, action_type=ActionType.RECKLESS_ATTACK)
        result = engine.execute_action(sm, action)
        assert result.success is True
        assert b.is_reckless is True

        # Toggle off
        result = engine.execute_action(sm, action)
        assert b.is_reckless is False

    def test_non_barbarian_rejected(self):
        f = make_fighter()
        sm = make_state([f])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=f.id, action_type=ActionType.RECKLESS_ATTACK)
        result = engine.execute_action(sm, action)
        assert result.success is False


class TestIndomitable:
    def test_use(self):
        f = make_fighter()
        sm = make_state([f])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=f.id, action_type=ActionType.INDOMITABLE)
        result = engine.execute_action(sm, action)
        assert result.success is True
        assert result.mechanical["reroll_next_save"] is True

    def test_non_fighter_rejected(self):
        m = make_monk()
        sm = make_state([m])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(actor_id=m.id, action_type=ActionType.INDOMITABLE)
        result = engine.execute_action(sm, action)
        assert result.success is False

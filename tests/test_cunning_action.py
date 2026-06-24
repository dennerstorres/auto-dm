"""Tests for Cunning Action (Rogue L2)."""
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
    EquippedSlots,
    GameState,
)


def make_rogue() -> Character:
    return Character(
        id="r1", name="Vex", race="Half-Elf", class_="Rogue", level=2,
        background="Criminal", alignment="CN",
        abilities=AbilityScores(
            strength=10, dexterity=16, constitution=12,
            intelligence=13, wisdom=12, charisma=14,
        ),
        hp_current=20, hp_max=20, armor_class=14, speed=30,
        proficiency_bonus=2, hit_dice="1d8", hit_dice_remaining=2,
        has_cunning_action=True,
    )


def make_fighter() -> Character:
    return Character(
        id="f1", name="Conan", race="Human", class_="Fighter", level=1,
        background="Soldier", alignment="CN",
        abilities=AbilityScores(
            strength=16, dexterity=14, constitution=14,
            intelligence=10, wisdom=12, charisma=10,
        ),
        hp_current=12, hp_max=12, armor_class=16, speed=30,
        proficiency_bonus=2, hit_dice="1d10", hit_dice_remaining=1,
    )


def make_state(party):
    return StateManager(GameState(
        campaign_name="test",
        started_at=datetime.now(),
        party=party,
        player_character_id=party[0].id,
    ))


class TestCunningActionHandler:
    def test_handler_registered(self):
        assert ActionType.CUNNING_ACTION in _ACTION_HANDLERS

    def _setup_combat(self, sm, engine, actor_id):
        engine.start_combat(sm)
        sm.state.initiative_order = [actor_id]
        sm.state.current_turn_index = 0

    def test_rogue_can_dash(self):
        r = make_rogue()
        sm = make_state([r])
        engine = CombatEngine(rng=random.Random(0))
        self._setup_combat(sm, engine, r.id)
        action = Action(
            actor_id=r.id, action_type=ActionType.CUNNING_ACTION,
            params={"subaction": "dash"},
        )
        result = engine.execute_action(sm, action)
        assert result.success is True

    def test_rogue_can_disengage(self):
        r = make_rogue()
        sm = make_state([r])
        engine = CombatEngine(rng=random.Random(0))
        self._setup_combat(sm, engine, r.id)
        action = Action(
            actor_id=r.id, action_type=ActionType.CUNNING_ACTION,
            params={"subaction": "disengage"},
        )
        result = engine.execute_action(sm, action)
        assert result.success is True

    def test_rogue_can_hide(self):
        r = make_rogue()
        sm = make_state([r])
        engine = CombatEngine(rng=random.Random(0))
        self._setup_combat(sm, engine, r.id)
        action = Action(
            actor_id=r.id, action_type=ActionType.CUNNING_ACTION,
            params={"subaction": "hide"},
        )
        result = engine.execute_action(sm, action)
        assert result.success is True

    def test_invalid_subaction_rejected(self):
        r = make_rogue()
        sm = make_state([r])
        engine = CombatEngine(rng=random.Random(0))
        # No combat needed: validation happens before turn check.
        action = Action(
            actor_id=r.id, action_type=ActionType.CUNNING_ACTION,
            params={"subaction": "fly"},
        )
        result = engine.execute_action(sm, action)
        assert result.success is False
        assert "subaction" in result.message.lower()

    def test_non_rogue_rejected(self):
        f = make_fighter()
        sm = make_state([f])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=f.id, action_type=ActionType.CUNNING_ACTION,
            params={"subaction": "dash"},
        )
        result = engine.execute_action(sm, action)
        assert result.success is False
        assert "Cunning Action" in result.message or "Ação Astuta" in result.message

    def test_rogue_without_feature_rejected(self):
        r = make_rogue()
        r.has_cunning_action = False
        sm = make_state([r])
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=r.id, action_type=ActionType.CUNNING_ACTION,
            params={"subaction": "dash"},
        )
        result = engine.execute_action(sm, action)
        assert result.success is False
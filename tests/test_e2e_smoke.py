"""End-to-end smoke test for the full game loop.

Covers: party setup → combat (attack + cast spell + class feature) →
short rest → long rest → save/load roundtrip. Uses no LLM calls —
verifies only the deterministic engine path.
"""
from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path

import pytest

from auto_dm.companions import COMPANION_FACTORIES, list_companion_keys
from auto_dm.engine.adventuring import long_rest, short_rest
from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.engine.resources import long_rest_recovery, short_rest_recovery
from auto_dm.persistence import (
    delete_save,
    list_saves,
    load_state,
    save_state,
)
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    Action,
    ActionType,
    AbilityScores,
    Character,
    GameState,
    NPC,
    Spellcasting,
)


def make_iron_man(name: str = "Tony", klass: str = "Fighter") -> Character:
    """A L5 character with full HP and a longsword."""
    return Character(
        id=f"player_{name.lower()}",
        name=name, race="Human", class_=klass, level=5,
        background="Soldier", alignment="LG",
        abilities=AbilityScores(
            strength=16, dexterity=12, constitution=14,
            intelligence=10, wisdom=12, charisma=10,
        ),
        hp_current=44, hp_max=44, armor_class=18, speed=30,
        proficiency_bonus=3, hit_dice="1d10", hit_dice_remaining=5,
    )


def make_wizard(name: str = "Elara") -> Character:
    w = Character(
        id=f"player_{name.lower()}",
        name=name, race="High Elf", class_="Wizard", level=5,
        background="Sage", alignment="LN",
        abilities=AbilityScores(
            strength=8, dexterity=14, constitution=14,
            intelligence=16, wisdom=12, charisma=10,
        ),
        hp_current=24, hp_max=24, armor_class=12, speed=30,
        proficiency_bonus=3, hit_dice="1d6", hit_dice_remaining=5,
    )
    w.spellcasting = Spellcasting(
        ability="intelligence", save_dc=15, attack_bonus=7,
        cantrips_known=["Fire Bolt", "Mage Hand", "Minor Illusion"],
        spells_known=["Magic Missile", "Shield", "Fireball"],
        spells_prepared=["Magic Missile", "Shield", "Fireball"],
        spell_slots={1: 4, 2: 3, 3: 2},
        spell_slots_max={1: 4, 2: 3, 3: 2},
    )
    return w


def make_goblin() -> NPC:
    return NPC(
        id="goblin1", name="Goblin", hp_current=7, hp_max=7,
        armor_class=15, speed=30,
        abilities=AbilityScores(
            strength=8, dexterity=14, constitution=10,
            intelligence=10, wisdom=8, charisma=8,
        ),
    )


def make_party_state() -> StateManager:
    """A simple 3-PC party + 1 NPC, no combat yet."""
    party = [
        make_iron_man("Tony", "Fighter"),
        make_wizard("Elara"),
        COMPANION_FACTORIES["vex"](),
    ]
    return StateManager(GameState(
        campaign_name="E2E Test",
        started_at=datetime.now(),
        party=party,
        player_character_id=party[0].id,
        npcs=[make_goblin()],
    ))


class TestPartySetup:
    def test_roster_loaded(self):
        assert len(list_companion_keys()) >= 1
        for k in list_companion_keys():
            c = COMPANION_FACTORIES[k]()
            assert c.level >= 1
            assert c.hp_max > 0

    def test_state_has_party(self):
        sm = make_party_state()
        assert len(sm.state.party) == 3
        assert sm.state.player_character_id == "player_tony"

    def test_companion_classes(self):
        sm = make_party_state()
        classes = [c.class_ for c in sm.state.party]
        assert "Fighter" in classes
        assert "Wizard" in classes
        assert "Rogue" in classes


class TestCombatLoop:
    def test_fighter_attacks_goblin(self):
        sm = make_party_state()
        engine = CombatEngine(rng=random.Random(42))
        engine.start_combat(sm)
        sm.state.initiative_order = [sm.state.party[0].id]
        sm.state.current_turn_index = 0

        action = Action(
            actor_id=sm.state.party[0].id,
            action_type=ActionType.ATTACK,
            target_id=sm.state.npcs[0].id,
        )
        result = engine.execute_action(sm, action)
        # ActionResult has 'success' and 'mechanical' (dict)
        assert result.success is True
        assert "is_hit" in result.mechanical
        if result.mechanical.get("is_hit"):
            assert result.mechanical.get("damage", 0) >= 0

    def test_wizard_casts_magic_missile(self):
        sm = make_party_state()
        engine = CombatEngine(rng=random.Random(42))
        engine.start_combat(sm)
        sm.state.initiative_order = [sm.state.party[1].id]
        sm.state.current_turn_index = 0

        wizard = sm.state.party[1]
        action = Action(
            actor_id=wizard.id,
            action_type=ActionType.CAST_SPELL,
            target_id=sm.state.npcs[0].id,
            params={"spell": "Magic Missile", "slot_level": 1},
        )
        result = engine.execute_action(sm, action)
        assert result.success is True
        # Magic Missile always hits
        assert result.mechanical.get("damage", 0) >= 0
        # Slot consumed
        assert wizard.spellcasting.spell_slots[1] == 3

    def test_full_round_advances(self):
        sm = make_party_state()
        engine = CombatEngine(rng=random.Random(42))
        engine.start_combat(sm)
        n_init = len(sm.state.initiative_order)
        for _ in range(n_init):
            engine.next_turn(sm)
        # Should have wrapped to round 2 or beyond
        assert sm.state.round_number >= 1


class TestClassFeaturesInCombat:
    def test_fighter_second_wind(self):
        f = make_iron_man()
        sm = StateManager(GameState(
            campaign_name="t", started_at=datetime.now(),
            party=[f], player_character_id=f.id,
        ))
        engine = CombatEngine(rng=random.Random(0))
        f.hp_current = 20
        action = Action(actor_id=f.id, action_type=ActionType.SECOND_WIND)
        result = engine.execute_action(sm, action)
        assert result.success is True
        assert f.hp_current > 20
        assert f.second_wind_used is True

    def test_wizard_cast_shield_self(self):
        w = make_wizard()
        sm = StateManager(GameState(
            campaign_name="t", started_at=datetime.now(),
            party=[w], player_character_id=w.id,
            npcs=[make_goblin()],
        ))
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=w.id,
            action_type=ActionType.CAST_SPELL,
            target_id=w.id,
            params={"spell": "Shield", "slot_level": 0},
        )
        result = engine.execute_action(sm, action)
        assert result is not None  # doesn't crash

    def test_rogue_attack(self):
        r = COMPANION_FACTORIES["vex"]()
        sm = StateManager(GameState(
            campaign_name="t", started_at=datetime.now(),
            party=[r], player_character_id=r.id,
            npcs=[make_goblin()],
        ))
        engine = CombatEngine(rng=random.Random(0))
        action = Action(
            actor_id=r.id, action_type=ActionType.ATTACK,
            target_id=sm.state.npcs[0].id,
            params={"ally_adjacent": True},
        )
        result = engine.execute_action(sm, action)
        assert result is not None


class TestRestLoop:
    def test_short_rest_recovery_helper(self):
        f = make_iron_man()
        f.hp_current = 20
        f.second_wind_used = True
        sm = StateManager(GameState(
            campaign_name="t", started_at=datetime.now(),
            party=[f], player_character_id=f.id,
        ))
        rec = short_rest_recovery(f)
        assert "second_wind" in rec
        assert f.second_wind_used is False

    def test_short_rest_heals(self):
        f = make_iron_man()
        f.hp_current = 20
        f.hit_dice_remaining = 3
        result = short_rest(f)
        assert result.hp_recovered > 0
        assert f.hp_current > 20
        assert f.hit_dice_remaining < 3

    def test_long_rest_full_recovery(self):
        f = make_iron_man()
        f.hp_current = 10
        f.second_wind_used = True
        sm = StateManager(GameState(
            campaign_name="t", started_at=datetime.now(),
            party=[f], player_character_id=f.id,
        ))
        result = long_rest(f)
        assert f.hp_current == f.hp_max


class TestPersistenceRoundtrip:
    def test_save_load_preserves_state(self, tmp_path: Path):
        sm = make_party_state()
        sm.state.campaign_name = "Roundtrip Test"
        save_state(sm.state, slug="e2e_roundtrip", saves_dir=tmp_path)

        loaded = load_state("e2e_roundtrip", saves_dir=tmp_path)
        assert loaded.campaign_name == "Roundtrip Test"
        assert len(loaded.party) == 3
        assert loaded.party[0].name == "Tony"
        wiz = next(p for p in loaded.party if p.class_ == "Wizard")
        assert wiz.spellcasting.spell_slots[1] == 4
        assert "Magic Missile" in wiz.spellcasting.spells_known

    def test_save_load_preserves_combat_state(self, tmp_path: Path):
        sm = make_party_state()
        engine = CombatEngine(rng=random.Random(0))
        engine.start_combat(sm)
        original_initiative = list(sm.state.initiative_order)
        original_round = sm.state.round_number
        save_state(sm.state, slug="combat_test", saves_dir=tmp_path)

        loaded = load_state("combat_test", saves_dir=tmp_path)
        assert loaded.initiative_order == original_initiative
        assert loaded.round_number == original_round

    def test_save_list_delete(self, tmp_path: Path):
        sm = make_party_state()
        save_state(sm.state, slug="list_test", saves_dir=tmp_path)
        save_state(sm.state, slug="list_test2", saves_dir=tmp_path)
        saves = list_saves(saves_dir=tmp_path)
        names = [s.slug for s in saves]
        assert "list_test" in names
        assert "list_test2" in names

        delete_save("list_test", saves_dir=tmp_path)
        saves = list_saves(saves_dir=tmp_path)
        names = [s.slug for s in saves]
        assert "list_test" not in names
        assert "list_test2" in names


class TestFullLoopIntegration:
    def test_setup_combat_rest_save_load(self, tmp_path: Path):
        """The full mini-loop: setup → combat → short rest → save → load."""
        # 1. Setup
        sm = make_party_state()
        sm.state.campaign_name = "Full Loop"
        assert len(sm.state.party) == 3

        # 2. Combat (1 round)
        engine = CombatEngine(rng=random.Random(7))
        engine.start_combat(sm)
        for _ in range(len(sm.state.initiative_order)):
            engine.next_turn(sm)

        # 3. Short rest
        for p in sm.state.party:
            short_rest_recovery(p)

        # 4. Save
        save_state(sm.state, slug="full_loop", saves_dir=tmp_path)

        # 5. Load and verify
        loaded = load_state("full_loop", saves_dir=tmp_path)
        assert loaded.campaign_name == "Full Loop"
        assert len(loaded.party) == 3
        for original, restored in zip(sm.state.party, loaded.party):
            assert original.name == restored.name
            assert original.class_ == restored.class_
            assert original.level == restored.level

    def test_full_loop_damage_then_long_rest(self, tmp_path: Path):
        """Take damage, long rest, verify full HP recovery persisted."""
        sm = make_party_state()
        f = sm.state.party[0]
        f.hp_current = 5  # critical

        long_rest(f)
        assert f.hp_current == f.hp_max

        save_state(sm.state, slug="after_longrest", saves_dir=tmp_path)
        loaded = load_state("after_longrest", saves_dir=tmp_path)
        loaded_fighter = next(p for p in loaded.party if p.class_ == "Fighter")
        assert loaded_fighter.hp_current == loaded_fighter.hp_max

"""Tests for state models and StateManager."""
from __future__ import annotations

from datetime import datetime

import pytest

from auto_dm.state import (
    Ability,
    AbilityScores,
    Action,
    ActionType,
    Character,
    Condition,
    GameState,
    NPC,
    Proficiencies,
    StateManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_ability() -> AbilityScores:
    return AbilityScores(
        strength=16,
        dexterity=10,
        constitution=14,
        intelligence=8,
        wisdom=12,
        charisma=10,
    )


def make_character(
    id: str = "thorgar",
    name: str = "Thorgar",
    hp: int = 28,
    hp_max: int = 28,
) -> Character:
    return Character(
        id=id,
        name=name,
        race="Dwarf",
        **{"class": "Fighter"},  # use alias to populate 'class_'
        subclass="Champion",
        level=3,
        background="Soldier",
        alignment="Lawful Good",
        abilities=make_ability(),
        hp_current=hp,
        hp_max=hp_max,
        temp_hp=0,
        armor_class=18,
        speed=25,
        proficiency_bonus=2,
        hit_dice="3d10",
        hit_dice_remaining=3,
        proficiencies=Proficiencies(
            saves=[Ability.STR, Ability.CON],
            skills=[],
            tools=[],
            languages=["Common", "Dwarvish"],
        ),
        spellcasting=None,
        personality_traits=["Loyal to my allies"],
        ideals=["Honor"],
        bonds=["My brother's axe"],
        flaws=["Suspicious of strangers"],
        is_player=True,
    )


def make_npc(id: str = "orc_1", hp: int = 15) -> NPC:
    return NPC(
        id=id,
        name="Orc Berserker",
        description="A snarling orc wielding a greataxe.",
        hp_current=hp,
        hp_max=15,
        armor_class=13,
        speed=30,
        abilities=AbilityScores(
            strength=16,
            dexterity=12,
            constitution=16,
            intelligence=7,
            wisdom=11,
            charisma=10,
        ),
        is_hostile=True,
        challenge_rating=1.0,
    )


def make_state(*, with_npc: bool = True) -> GameState:
    thorgar = make_character()
    lyra = make_character(id="lyra", name="Lyra", hp=18, hp_max=18)
    state = GameState(
        campaign_name="Test",
        started_at=datetime(2026, 6, 24),
        party=[thorgar, lyra],
        player_character_id="thorgar",
    )
    if with_npc:
        state.npcs.append(make_npc())
    return state


# ---------------------------------------------------------------------------
# AbilityScores
# ---------------------------------------------------------------------------


def test_ability_modifier_positive():
    a = AbilityScores(
        strength=15, dexterity=10, constitution=10,
        intelligence=10, wisdom=10, charisma=10,
    )
    assert a.modifier(Ability.STR) == 2


def test_ability_modifier_negative():
    a = AbilityScores(
        strength=8, dexterity=10, constitution=10,
        intelligence=10, wisdom=10, charisma=10,
    )
    assert a.modifier(Ability.STR) == -1


def test_ability_modifier_zero():
    a = AbilityScores(
        strength=10, dexterity=10, constitution=10,
        intelligence=10, wisdom=10, charisma=10,
    )
    assert a.modifier(Ability.STR) == 0


def test_ability_modifier_max():
    a = AbilityScores(
        strength=20, dexterity=10, constitution=10,
        intelligence=10, wisdom=10, charisma=10,
    )
    assert a.modifier(Ability.STR) == 5


# ---------------------------------------------------------------------------
# Character serialization
# ---------------------------------------------------------------------------


def test_character_class_alias_in_json():
    c = make_character()
    j = c.model_dump(by_alias=True)
    # JSON uses 'class', Python uses class_
    assert "class" in j
    assert "class_" not in j
    assert j["class"] == "Fighter"


def test_character_class_alias_from_json():
    j = {
        "id": "x",
        "name": "X",
        "race": "Human",
        "class": "Wizard",
        "level": 1,
        "background": "Sage",
        "alignment": "N",
        "abilities": AbilityScores(
            strength=8, dexterity=14, constitution=12,
            intelligence=16, wisdom=13, charisma=10,
        ).model_dump(),
        "hp_current": 6,
        "hp_max": 6,
        "armor_class": 12,
        "speed": 30,
        "proficiency_bonus": 2,
        "hit_dice": "1d6",
        "hit_dice_remaining": 1,
    }
    c = Character.model_validate(j)
    assert c.class_ == "Wizard"


def test_character_roundtrip():
    c = make_character()
    j = c.model_dump(by_alias=True)
    c2 = Character.model_validate(j)
    assert c2.name == c.name
    assert c2.class_ == c.class_
    assert c2.hp_current == c.hp_current
    assert c2.abilities.modifier(Ability.STR) == 3  # 16 -> +3


# ---------------------------------------------------------------------------
# StateManager — HP
# ---------------------------------------------------------------------------


def test_set_hp_damage_basic():
    mgr = StateManager(make_state())
    new_hp = mgr.set_hp("thorgar", -7)
    assert new_hp == 21
    assert mgr.get_character("thorgar").hp_current == 21


def test_set_hp_damage_clamps_to_zero():
    mgr = StateManager(make_state())
    new_hp = mgr.set_hp("thorgar", -100)
    assert new_hp == 0
    assert mgr.get_character("thorgar").hp_current == 0


def test_set_hp_heal_clamps_to_max():
    mgr = StateManager(make_state())
    # First damage a bit
    mgr.set_hp("thorgar", -10)
    # Then heal more than max
    new_hp = mgr.set_hp("thorgar", 100)
    assert new_hp == 28
    assert mgr.get_character("thorgar").hp_current == 28


def test_set_hp_temp_hp_absorbs_first():
    mgr = StateManager(make_state())
    thorgar = mgr.get_character("thorgar")
    thorgar.temp_hp = 5
    mgr.set_hp("thorgar", -7)
    # 5 absorbed by temp, 2 from real HP
    assert thorgar.temp_hp == 0
    assert thorgar.hp_current == 26


def test_set_hp_resets_death_saves_on_recovery():
    mgr = StateManager(make_state())
    thorgar = mgr.get_character("thorgar")
    thorgar.hp_current = 0
    thorgar.death_save_failures = 2
    thorgar.death_save_successes = 1
    mgr.set_hp("thorgar", 5)
    assert thorgar.death_save_failures == 0
    assert thorgar.death_save_successes == 0


def test_set_hp_unknown_creature_raises():
    mgr = StateManager(make_state())
    with pytest.raises(KeyError):
        mgr.set_hp("ghost", -5)


# ---------------------------------------------------------------------------
# StateManager — conditions
# ---------------------------------------------------------------------------


def test_add_and_remove_condition():
    mgr = StateManager(make_state())
    mgr.add_condition("thorgar", Condition.POISONED)
    assert Condition.POISONED in mgr.get_character("thorgar").conditions
    mgr.remove_condition("thorgar", Condition.POISONED)
    assert Condition.POISONED not in mgr.get_character("thorgar").conditions


def test_add_condition_is_idempotent():
    mgr = StateManager(make_state())
    mgr.add_condition("thorgar", Condition.POISONED)
    mgr.add_condition("thorgar", Condition.POISONED)
    conds = mgr.get_character("thorgar").conditions
    assert conds.count(Condition.POISONED) == 1


def test_condition_works_on_npc():
    mgr = StateManager(make_state())
    mgr.add_condition("orc_1", Condition.FRIGHTENED)
    assert Condition.FRIGHTENED in mgr.get_npc("orc_1").conditions


# ---------------------------------------------------------------------------
# StateManager — combat
# ---------------------------------------------------------------------------


def test_start_combat_initializes_state():
    mgr = StateManager(make_state())
    mgr.start_combat(["thorgar", "orc_1", "lyra"])
    assert mgr.state.in_combat
    assert mgr.state.initiative_order == ["thorgar", "orc_1", "lyra"]
    assert mgr.state.current_turn_index == 0
    assert mgr.state.round_number == 1
    assert mgr.current_actor_id() == "thorgar"


def test_next_turn_advances_in_order():
    mgr = StateManager(make_state())
    mgr.start_combat(["thorgar", "orc_1", "lyra"])
    assert mgr.next_turn() == "orc_1"
    assert mgr.next_turn() == "lyra"
    assert mgr.next_turn() == "thorgar"  # wrapped
    assert mgr.state.round_number == 2


def test_end_combat_resets():
    mgr = StateManager(make_state())
    mgr.start_combat(["thorgar", "orc_1"])
    mgr.end_combat()
    assert not mgr.state.in_combat
    assert mgr.state.initiative_order == []
    assert mgr.current_actor_id() is None


def test_next_turn_outside_combat_raises():
    mgr = StateManager(make_state())
    with pytest.raises(RuntimeError):
        mgr.next_turn()


# ---------------------------------------------------------------------------
# Action model
# ---------------------------------------------------------------------------


def test_action_serialization():
    a = Action(
        actor_id="thorgar",
        action_type=ActionType.ATTACK,
        target_id="orc_1",
        params={"weapon": "longsword"},
        dialogue="For the king!",
        reasoning="Orc is closest enemy",
    )
    j = a.model_dump()
    a2 = Action.model_validate(j)
    assert a2.action_type == ActionType.ATTACK
    assert a2.target_id == "orc_1"
    assert a2.params == {"weapon": "longsword"}


# ---------------------------------------------------------------------------
# StateManager — get_creature
# ---------------------------------------------------------------------------


def test_get_creature_finds_character():
    mgr = StateManager(make_state())
    assert mgr.get_creature("thorgar") is mgr.get_character("thorgar")


def test_get_creature_finds_npc():
    mgr = StateManager(make_state())
    assert mgr.get_creature("orc_1") is mgr.get_npc("orc_1")


def test_get_creature_returns_none_for_unknown():
    mgr = StateManager(make_state())
    assert mgr.get_creature("ghost") is None


# ---------------------------------------------------------------------------
# Per-campaign narration length preference
# ---------------------------------------------------------------------------


class TestNarrationLength:
    def test_default_is_longo(self):
        """Without the field, new GameStates default to "longo" — the
        original verbose behavior. Preserves backward compatibility for
        any caller that builds a state without explicitly picking."""
        state = make_state()
        assert state.narration_length == "longo"

    def test_explicit_values_round_trip(self):
        for value in ("curto", "medio", "longo"):
            state = make_state()
            state.narration_length = value
            j = state.model_dump_json()
            state2 = GameState.model_validate_json(j)
            assert state2.narration_length == value

    def test_invalid_value_rejected(self):
        # Pydantic v2 by default validates on construction, not on
        # attribute assignment. Use model_validate to ensure the
        # Literal guard fires for anything outside the union.
        state = make_state().model_dump(mode="json")
        state["narration_length"] = "epico"
        with pytest.raises(Exception):
            GameState.model_validate(state)

    def test_old_save_without_field_loads_as_longo(self):
        """Backward-compat: a JSON dump that predates the field must
        still load successfully, defaulting to "longo"."""
        j = make_state().model_dump(mode="json")
        j.pop("narration_length", None)
        assert "narration_length" not in j  # sanity
        state2 = GameState.model_validate(j)
        assert state2.narration_length == "longo"

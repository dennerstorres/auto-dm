"""Integration tests: narrative loop + real CombatEngine.

Verifies that when the DM emits an attack action, the live CombatEngine
runs it, mutates state, and the DM agent narrates the result.
"""
from __future__ import annotations

from datetime import datetime, timezone

from auto_dm.agents import DMAgent, process_player_action
from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.llm.base import LLMConfig, Message
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    AbilityScores,
    ActionType,
    Character,
    EquippedSlots,
    GameState,
    Item,
    ItemType,
    NPC,
    Proficiencies,
    WeaponProperties,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class ScriptedProvider:
    """Returns scripted responses in order. Tracks calls for assertions."""

    def __init__(self, scripted: list[str]) -> None:
        self.scripted = list(scripted)
        self.calls: list[list[Message]] = []
        self.name = "scripted"
        self.config = LLMConfig(name="scripted", api_key="test", model="test")

    def chat(self, messages: list[Message]) -> str:
        self.calls.append(messages)
        if not self.scripted:
            return ""
        if len(self.scripted) == 1:
            return self.scripted[0]
        return self.scripted.pop(0)

    def stream(self, messages):
        yield self.chat(messages)

    def count_tokens(self, messages):
        return sum(len(m.content) for m in messages)


def make_weapon(name: str = "Longsword", dice: str = "1d8") -> Item:
    return Item(
        name=name,
        type=ItemType.WEAPON,
        weapon=WeaponProperties(damage_dice=dice, damage_type="slashing"),
    )


def make_creature(
    *,
    id: str,
    name: str = "X",
    strength: int = 16,
    dexterity: int = 10,
    hp: int = 20,
    ac: int = 13,
    weapon: Item | None = None,
    is_character: bool = True,
) -> Character | NPC:
    abilities = AbilityScores(
        strength=strength,
        dexterity=dexterity,
        constitution=14,
        intelligence=8,
        wisdom=10,
        charisma=8,
    )
    if is_character:
        return Character(
            id=id,
            name=name,
            **{"class": "Fighter"},
            race="Human",
            level=1,
            background="Soldier",
            alignment="N",
            abilities=abilities,
            hp_current=hp,
            hp_max=hp,
            armor_class=ac,
            speed=30,
            proficiency_bonus=2,
            hit_dice="1d10",
            hit_dice_remaining=1,
            equipped=EquippedSlots(main_hand=weapon) if weapon else EquippedSlots(),
            proficiencies=Proficiencies(),
        )
    return NPC(
        id=id,
        name=name,
        hp_current=hp,
        hp_max=hp,
        armor_class=ac,
        speed=30,
        abilities=abilities,
        equipped=EquippedSlots(main_hand=weapon) if weapon else EquippedSlots(),
    )


def make_state() -> StateManager:
    player = make_creature(id="p1", name="Aragorn", weapon=make_weapon(), hp=20, ac=14)
    goblin = make_creature(
        id="g1", name="Goblin", is_character=False, hp=20, ac=12
    )
    return StateManager(
        GameState(
            campaign_name="combate de teste",
            started_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
            current_location="clareira",
            party=[player],
            npcs=[goblin],
            player_character_id=player.id,
        )
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNarrativeLoopWithRealEngine:
    def test_attack_through_narrative_loop_applies_damage(self):
        sm = make_state()
        # init(2) + attack d20 (1) + damage (1) for a hit
        engine = CombatEngine(rng=__import__("random").Random(42))
        # Use the DM's "ataco" emit
        dm_text = (
            "Você avança sobre o goblin.\n```action\n"
            '{"action_type": "attack", "actor_id": "p1", "target_id": "g1"}\n'
            "```"
        )
        provider = ScriptedProvider([dm_text, "O goblin recua sob o golpe."])
        agent = DMAgent(provider=provider, state_manager=sm)

        # Force p1 to act first so the action is legal.
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        sm.state.in_combat = True
        sm.state.round_number = 1

        before = sm.state.npcs[0].hp_current
        result = process_player_action(
            sm, "ataco o goblin", agent, combat_engine=engine
        )
        # The DM emitted an attack, engine resolved it, the DM narrated
        # the result in a follow-up round.
        assert result.action is not None
        assert result.action.action_type == ActionType.ATTACK
        assert result.action_result is not None
        assert result.action_result.mechanical.get("is_hit") is not None
        if result.action_result.mechanical["is_hit"]:
            assert sm.state.npcs[0].hp_current < before
        assert result.follow_up_narration == "O goblin recua sob o golpe."

    def test_attack_with_engine_validates_turn(self):
        sm = make_state()
        engine = CombatEngine(rng=__import__("random").Random(42))
        sm.state.initiative_order = ["g1", "p1"]  # goblin first
        sm.state.current_turn_index = 0
        sm.state.in_combat = True

        provider = ScriptedProvider(
            [
                "Você tenta atacar.\n```action\n"
                '{"action_type": "attack", "actor_id": "p1", "target_id": "g1"}\n'
                "```"
            ]
        )
        agent = DMAgent(provider=provider, state_manager=sm)
        result = process_player_action(
            sm, "ataco", agent, combat_engine=engine
        )
        # Engine refused because it's not p1's turn.
        assert not result.action_result.success
        assert "turno" in result.action_result.message.lower()
        # No follow-up DM call when action was rejected (it's still a stub
        # mechanical result, not a successful narrative beat).
        assert result.follow_up_narration is None

    def test_dash_through_narrative_loop(self):
        sm = make_state()
        engine = CombatEngine(rng=__import__("random").Random(42))
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        sm.state.in_combat = True

        provider = ScriptedProvider(
            [
                "Você corre.\n```action\n"
                '{"action_type": "dash", "actor_id": "p1"}\n```'
            ]
        )
        agent = DMAgent(provider=provider, state_manager=sm)
        result = process_player_action(sm, "corro", agent, combat_engine=engine)
        assert result.action_result.success
        assert "Dash" in result.action_result.message

    def test_dodge_adds_condition_via_narrative(self):
        sm = make_state()
        engine = CombatEngine(rng=__import__("random").Random(42))
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        sm.state.in_combat = True

        from auto_dm.state.models import Condition

        provider = ScriptedProvider(
            [
                "Você esquiva.\n```action\n"
                '{"action_type": "dodge", "actor_id": "p1"}\n```'
            ]
        )
        agent = DMAgent(provider=provider, state_manager=sm)
        process_player_action(sm, "esquivo", agent, combat_engine=engine)
        assert Condition.DODGING in sm.state.party[0].conditions

    def test_say_is_flavor_no_engine_call(self):
        sm = make_state()
        engine = CombatEngine(rng=__import__("random").Random(42))
        sm.state.in_combat = True

        provider = ScriptedProvider(
            [
                "Você fala.\n```action\n"
                '{"action_type": "say", "actor_id": "p1", '
                '"dialogue": "Renda-se!"}\n```'
            ]
        )
        agent = DMAgent(provider=provider, state_manager=sm)
        result = process_player_action(sm, "falo", agent, combat_engine=engine)
        # Say is flavor — engine is never called.
        assert result.action is not None
        assert result.action_result is None
        # No follow-up narration.
        assert result.follow_up_narration is None

    def test_end_combat_via_narrative_clears_state(self):
        sm = make_state()
        engine = CombatEngine(rng=__import__("random").Random(42))
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        sm.state.in_combat = True
        sm.state.round_number = 3

        provider = ScriptedProvider(
            [
                "Você ergue as mãos.\n```action\n"
                '{"action_type": "end_combat", "actor_id": "p1"}\n```',
                "Combate encerrado.",
            ]
        )
        agent = DMAgent(provider=provider, state_manager=sm)
        result = process_player_action(sm, "me rendo", agent, combat_engine=engine)
        assert result.action_result.success
        assert not sm.state.in_combat

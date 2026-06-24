"""Tests for companion agents, prompt building, parser, turn orchestration,
and the pre-defined companion roster.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from auto_dm.agents import (
    COMPANION_SYSTEM_PROMPT,
    CompanionAgent,
    build_companion_agents,
    build_companion_identity_block,
    parse_companion_response,
    run_companion_turn,
)
from auto_dm.companions import (
    build_companion,
    list_companion_keys,
    make_lyra,
    make_mira,
    make_thorgrim,
)
from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.llm.base import LLMConfig, Message
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    ActionType,
    GameState,
    NPC,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class ScriptedProvider:
    """Returns scripted responses in order."""

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


def companion_response(intent: str, action_json: dict | None = None) -> str:
    """Build a fake companion LLM response: intent + optional action block."""
    if action_json is None:
        return intent
    import json

    body = json.dumps(action_json)
    return f"{intent}\n```action\n{body}\n```"


# ---------------------------------------------------------------------------
# System prompt + identity block
# ---------------------------------------------------------------------------


class TestCompanionPrompt:
    def test_is_in_portuguese(self):
        assert "Dungeons & Dragons" in COMPANION_SYSTEM_PROMPT
        assert "pt-BR" in COMPANION_SYSTEM_PROMPT or "primeira pessoa" in COMPANION_SYSTEM_PROMPT

    def test_emphasizes_personality(self):
        assert "personalidade" in COMPANION_SYSTEM_PROMPT.lower()

    def test_specifies_action_format(self):
        assert "```action" in COMPANION_SYSTEM_PROMPT
        assert "action_type" in COMPANION_SYSTEM_PROMPT

    def test_authority_rule(self):
        assert "mecânica" in COMPANION_SYSTEM_PROMPT.lower() or "motor" in COMPANION_SYSTEM_PROMPT.lower()

    def test_identity_block_includes_basics(self):
        c = make_thorgrim()
        block = build_companion_identity_block(c)
        assert "Thorgrim" in block
        assert "Dwarf" in block
        assert "Fighter" in block
        assert "HP" in block
        assert "AC" in block

    def test_identity_block_includes_personality(self):
        c = make_mira()
        block = build_companion_identity_block(c)
        assert "personalidade" in block.lower() or "traços" in block.lower()
        # Mira's flaws include "Perdoo rápido demais"
        assert "Perdoo" in block

    def test_identity_block_omits_empty_sections(self):
        from auto_dm.state.models import Character, AbilityScores

        # A character with no personality fields set
        c = Character(
            id="x",
            name="Empty",
            race="Human",
            **{"class": "Fighter"},
            level=1,
            background="Soldier",
            alignment="N",
            abilities=AbilityScores(
                strength=10, dexterity=10, constitution=10,
                intelligence=10, wisdom=10, charisma=10,
            ),
            hp_current=10,
            hp_max=10,
            armor_class=12,
            speed=30,
            proficiency_bonus=2,
            hit_dice="1d10",
            hit_dice_remaining=1,
        )
        block = build_companion_identity_block(c)
        assert "Empty" in block
        # No personality/ideals/bonds/flaws sections
        assert "Traços" not in block
        assert "Ideais" not in block


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestParseCompanionResponse:
    def test_intent_only(self):
        d = parse_companion_response("Eu observo a clareira com cuidado.", default_actor_id="c1")
        assert d.intent == "Eu observo a clareira com cuidado."
        assert d.action is None

    def test_with_action_block(self):
        text = companion_response(
            "Eu avanco e golpeio.",
            {"action_type": "attack", "actor_id": "c1", "target_id": "g1"},
        )
        d = parse_companion_response(text, default_actor_id="c1")
        assert "avanco" in d.intent
        assert d.action is not None
        assert d.action.action_type == ActionType.ATTACK
        assert d.action.target_id == "g1"

    def test_missing_actor_id_filled_in(self):
        # LLM forgets to include actor_id
        text = companion_response(
            "Eu ataco.",
            {"action_type": "attack", "target_id": "g1"},
        )
        d = parse_companion_response(text, default_actor_id="c1")
        assert d.action is not None
        assert d.action.actor_id == "c1"

    def test_existing_actor_id_preserved(self):
        text = companion_response(
            "Eu ataco.",
            {"action_type": "attack", "actor_id": "other", "target_id": "g1"},
        )
        d = parse_companion_response(text, default_actor_id="c1")
        assert d.action.actor_id == "other"

    def test_malformed_action_drops_action(self):
        text = "Eu tento.\n```action\n{ not json }\n```"
        d = parse_companion_response(text, default_actor_id="c1")
        assert "tento" in d.intent
        assert d.action is None

    def test_empty_text(self):
        d = parse_companion_response("", default_actor_id="c1")
        assert d.intent == ""
        assert d.action is None


# ---------------------------------------------------------------------------
# CompanionAgent
# ---------------------------------------------------------------------------


class TestCompanionAgent:
    def _state_with(self, character):
        return StateManager(
            GameState(
                campaign_name="test",
                started_at=datetime.now(timezone.utc),
                party=[character],
                npcs=[],
                player_character_id="p1",
            )
        )

    def test_decide_calls_provider(self):
        c = make_thorgrim()
        sm = self._state_with(c)
        provider = ScriptedProvider(["Eu levanto meu escudo."])
        agent = CompanionAgent(provider=provider, character=c, state_manager=sm)
        decision = agent.decide("O que faço?")
        assert decision.intent == "Eu levanto meu escudo."
        assert len(provider.calls) == 1

    def test_messages_include_identity(self):
        c = make_lyra()
        sm = self._state_with(c)
        provider = ScriptedProvider(["ok"])
        agent = CompanionAgent(provider=provider, character=c, state_manager=sm)
        agent.decide("o que faço")
        sys_content = provider.calls[0][0].content
        assert "Lyra" in sys_content
        assert "Elf" in sys_content or "elf" in sys_content.lower()

    def test_decide_in_combat_prompt_lists_enemies(self):
        c = make_thorgrim()
        sm = self._state_with(c)
        provider = ScriptedProvider(["Eu avanco."])
        agent = CompanionAgent(provider=provider, character=c, state_manager=sm)
        agent.decide_in_combat(enemies=["gob1", "gob2"], allies=["p1"])
        last_user = provider.calls[0][-1]
        assert "gob1" in last_user.content
        assert "gob2" in last_user.content
        assert "p1" in last_user.content

    def test_decide_returns_action(self):
        c = make_thorgrim()
        sm = self._state_with(c)
        provider = ScriptedProvider(
            [
                companion_response(
                    "Eu golpeio o goblin.",
                    {"action_type": "attack", "actor_id": c.id, "target_id": "g1"},
                )
            ]
        )
        agent = CompanionAgent(provider=provider, character=c, state_manager=sm)
        decision = agent.decide("ataco")
        assert decision.has_action
        assert decision.action.target_id == "g1"

    def test_custom_system_prompt(self):
        c = make_thorgrim()
        sm = self._state_with(c)
        provider = ScriptedProvider(["ok"])
        agent = CompanionAgent(
            provider=provider,
            character=c,
            state_manager=sm,
            system_prompt="Você é um taverneiro, não um aventureiro.",
        )
        agent.decide("oi")
        assert "taverneiro" in provider.calls[0][0].content


# ---------------------------------------------------------------------------
# Pre-defined companion roster
# ---------------------------------------------------------------------------


class TestRoster:
    def test_four_companions(self):
        assert set(list_companion_keys()) == {"thorgrim", "lyra", "mira", "vex"}

    def test_thorgrim_is_dwarf_fighter(self):
        c = build_companion("thorgrim")
        assert c.name == "Thorgrim"
        assert "Dwarf" in c.race
        assert c.class_ == "Fighter"
        assert c.alignment == "LN"
        assert c.level == 1
        assert c.hp_current == c.hp_max
        assert c.armor_class >= 16  # chain mail + shield
        # Has personality
        assert len(c.personality_traits) >= 1
        assert len(c.ideals) >= 1
        assert len(c.bonds) >= 1
        assert len(c.flaws) >= 1

    def test_lyra_is_elf_ranger(self):
        c = build_companion("lyra")
        assert c.name == "Lyra"
        assert "Elf" in c.race
        assert c.class_ == "Ranger"
        assert c.alignment == "CG"

    def test_mira_is_halfling_cleric_with_spellcasting(self):
        c = build_companion("mira")
        assert c.name == "Mira"
        assert "Halfling" in c.race
        assert c.class_ == "Cleric"
        assert c.alignment == "LG"
        assert c.spellcasting is not None
        # Has at least one prepared spell
        assert len(c.spellcasting.spells_prepared) >= 1

    def test_vex_is_tiefling_rogue(self):
        c = build_companion("vex")
        assert c.name == "Vex"
        assert "Tiefling" in c.race
        assert c.class_ == "Rogue"
        assert c.alignment == "CN"

    def test_unknown_key_raises(self):
        with pytest.raises(KeyError):
            build_companion("nonexistent")

    def test_factories_return_fresh_copies(self):
        # Each call should produce a new Character (not a shared one),
        # so that mutations in one don't affect another.
        a = build_companion("thorgrim")
        b = build_companion("thorgrim")
        assert a is not b
        a.hp_current = 1
        assert b.hp_current == a.hp_max  # b untouched

    def test_all_companions_have_unique_ids(self):
        # Different characters from different factories should have
        # different default uuids (or at least: a.build() and b.build()
        # produce distinct instances).
        companions = [build_companion(k) for k in list_companion_keys()]
        # uuid4[:8] is very unlikely to collide for 4 short strings.
        ids = [c.id for c in companions]
        assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------------
# Turn orchestration
# ---------------------------------------------------------------------------


class TestRunCompanionTurn:
    def _setup_combat(self):
        thorgrim = make_thorgrim()
        sm = StateManager(
            GameState(
                campaign_name="test",
                started_at=datetime.now(timezone.utc),
                party=[thorgrim],
                npcs=[
                    NPC(
                        id="g1",
                        name="Goblin",
                        hp_current=10,
                        hp_max=10,
                        armor_class=12,
                        speed=30,
                        abilities=thorgrim.abilities.model_copy(),
                    )
                ],
                player_character_id="p1",
            )
        )
        engine = CombatEngine(rng=__import__("random").Random(42))
        engine.start_combat(sm)
        sm.state.initiative_order = [thorgrim.id, "g1"]
        sm.state.current_turn_index = 0
        return sm, engine, thorgrim

    def test_companion_attacks_via_engine(self):
        sm, engine, thorgrim = self._setup_combat()
        # 1 d20 for attack (high roll → hit)
        rolls = [18, 6]  # 18 + STR(3) + prof(2) = 23 vs AC 12 → hit, dmg 6
        from tests.test_combat_engine import ScriptedRNG

        engine.rng = ScriptedRNG(rolls)
        provider = ScriptedProvider(
            [
                companion_response(
                    "Eu golpeio o goblin.",
                    {"action_type": "attack", "actor_id": thorgrim.id, "target_id": "g1"},
                )
            ]
        )
        agent = CompanionAgent(provider=provider, character=thorgrim, state_manager=sm)
        result = run_companion_turn(
            sm, engine, agent, enemies=["g1"], allies=[]
        )
        assert result.has_action
        assert result.action_result.success
        # Goblin took damage
        assert sm.state.npcs[0].hp_current < 10

    def test_turn_logged_in_narrative(self):
        sm, engine, thorgrim = self._setup_combat()
        provider = ScriptedProvider(
            [companion_response("Eu golpeio.", {"action_type": "attack", "actor_id": thorgrim.id, "target_id": "g1"})]
        )
        agent = CompanionAgent(provider=provider, character=thorgrim, state_manager=sm)
        before = len(sm.state.narrative_log)
        run_companion_turn(sm, engine, agent, enemies=["g1"])
        after = len(sm.state.narrative_log)
        # At least 2 entries: intent + result
        assert after >= before + 1  # at least intent logged
        # The intent is in the log
        speakers = [e.speaker for e in sm.state.narrative_log]
        assert "Thorgrim" in speakers

    def test_rejects_out_of_turn_call(self):
        sm, engine, thorgrim = self._setup_combat()
        sm.state.current_turn_index = 1  # It's now g1's turn
        provider = ScriptedProvider(["Eu tento agir."])
        agent = CompanionAgent(provider=provider, character=thorgrim, state_manager=sm)
        result = run_companion_turn(
            sm, engine, agent, enemies=["g1"], allies=[]
        )
        assert not result.has_action
        assert "turno" in result.action_result.message.lower()

    def test_intent_only_no_action(self):
        sm, engine, thorgrim = self._setup_combat()
        provider = ScriptedProvider(["Eu olho ao redor, cauteloso."])
        agent = CompanionAgent(provider=provider, character=thorgrim, state_manager=sm)
        result = run_companion_turn(
            sm, engine, agent, enemies=["g1"], allies=[]
        )
        assert not result.has_action
        assert "olho ao redor" in result.intent


class TestBuildCompanionAgents:
    def test_builds_for_non_player_only(self):
        from auto_dm.companions import make_thorgrim, make_lyra
        from auto_dm.state.models import Character, AbilityScores

        # Player + 2 companions
        player = Character(
            id="p1",
            name="Player",
            race="Human",
            **{"class": "Fighter"},
            level=1,
            background="Soldier",
            alignment="LG",
            is_player=True,
            abilities=AbilityScores(
                strength=14, dexterity=12, constitution=13,
                intelligence=10, wisdom=11, charisma=8,
            ),
            hp_current=10,
            hp_max=10,
            armor_class=16,
            speed=30,
            proficiency_bonus=2,
            hit_dice="1d10",
            hit_dice_remaining=1,
        )
        thorgrim = make_thorgrim()
        lyra = make_lyra()
        sm = StateManager(
            GameState(
                campaign_name="test",
                started_at=datetime.now(timezone.utc),
                party=[player, thorgrim, lyra],
                npcs=[],
                player_character_id="p1",
            )
        )
        agents = build_companion_agents(
            sm.state.party, sm, provider_factory=lambda: ScriptedProvider(["ok"])
        )
        assert "p1" not in agents
        assert thorgrim.id in agents
        assert lyra.id in agents
        assert len(agents) == 2

    def test_provider_factory_called_per_companion(self):
        calls = []

        def factory():
            calls.append(1)
            return ScriptedProvider(["ok"])

        thorgrim = make_thorgrim()
        sm = StateManager(
            GameState(
                campaign_name="t",
                started_at=datetime.now(timezone.utc),
                party=[thorgrim],
                npcs=[],
                player_character_id="p1",
            )
        )
        build_companion_agents(sm.state.party, sm, provider_factory=factory)
        assert len(calls) == 1

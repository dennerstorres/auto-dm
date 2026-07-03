"""Tests for the DM agent, prompts, parser, and narrative loop.

These tests use a fake LLM provider so no network calls happen.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from auto_dm.agents import (
    COMPANION_SYSTEM_PROMPT,
    DM_SYSTEM_PROMPT,
    DMAgent,
    build_dm_context_block,
    generate_opening,
    get_action_json_schema_description,
    parse_dm_response,
    process_player_action,
)
from auto_dm.agents.prompts import OPENING_INSTRUCTION
from auto_dm.llm.base import LLMConfig, Message
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    AbilityScores,
    Action,
    ActionType,
    Character,
    GameState,
    NarrativeEntry,
)


# ============================================================================
# Helpers
# ============================================================================


def dm_response(narration: str, action_json: dict | None = None) -> str:
    """Build a fake LLM response: narration text, optional ```action``` block.

    Using triple-quoted f-string so we don't fight with escape sequences.
    """
    if action_json is None:
        return narration
    import json

    body = json.dumps(action_json)
    return f"{narration}\n```action\n{body}\n```"


# ============================================================================
# Fixtures: fake LLM provider and a minimal game state
# ============================================================================


class FakeProvider:
    """Records every chat() call and returns a scripted response.

    Tests set ``scripted`` to a list of strings; each ``chat()`` returns
    the next string in order (or the last one if the list runs out).
    """

    def __init__(self, scripted: list[str] | None = None) -> None:
        self.scripted: list[str] = list(scripted or [""])
        self.calls: list[list[Message]] = []
        self.config = LLMConfig(
            name="fake",
            api_key="test",
            model="fake-model",
        )
        self.name = "fake"

    def chat(self, messages: list[Message]) -> str:
        self.calls.append(messages)
        if not self.scripted:
            return ""
        if len(self.scripted) == 1:
            return self.scripted[0]
        return self.scripted.pop(0)

    def stream(self, messages):  # pragma: no cover — not used in tests
        yield self.chat(messages)

    def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content) for m in messages)


def _ability_scores() -> AbilityScores:
    return AbilityScores(
        strength=14,
        dexterity=12,
        constitution=13,
        intelligence=10,
        wisdom=11,
        charisma=8,
    )


def _make_character(
    name: str = "Aragorn",
    *,
    char_id: str = "p1",
    is_player: bool = True,
    hp_current: int = 12,
    hp_max: int = 12,
    ac: int = 16,
) -> Character:
    return Character(
        id=char_id,
        name=name,
        race="Human",
        **{"class": "Fighter"},
        level=1,
        background="Soldier",
        alignment="LG",
        abilities=_ability_scores(),
        hp_current=hp_current,
        hp_max=hp_max,
        armor_class=ac,
        speed=30,
        proficiency_bonus=2,
        hit_dice="1d10",
        hit_dice_remaining=1,
        is_player=is_player,
    )


def _make_state() -> GameState:
    player = _make_character()
    companion = _make_character(
        "Legolas", char_id="c1", is_player=False, hp_current=10, hp_max=10, ac=15
    )
    return GameState(
        campaign_name="A Jornada Começa",
        started_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
        current_location="Floresta de Fangorn",
        time_of_day="amanhecer",
        weather="neblina leve",
        party=[player, companion],
        player_character_id=player.id,
    )


@pytest.fixture
def state() -> StateManager:
    return StateManager(_make_state())


# ============================================================================
# DM system prompt
# ============================================================================


class TestDMSystemPrompt:
    def test_is_in_portuguese(self):
        assert "Mestre de RPG" in DM_SYSTEM_PROMPT
        assert "português" in DM_SYSTEM_PROMPT.lower()

    def test_emphasizes_mechanics_authority(self):
        # The DM must NEVER invent mechanical results.
        assert "mecânica" in DM_SYSTEM_PROMPT.lower()
        assert "motor" in DM_SYSTEM_PROMPT.lower()
        assert "narra" in DM_SYSTEM_PROMPT.lower()

    def test_specifies_output_format(self):
        assert "action" in DM_SYSTEM_PROMPT  # block marker
        assert "JSON" in DM_SYSTEM_PROMPT or "json" in DM_SYSTEM_PROMPT

    def test_mentions_level_cap(self):
        assert "1" in DM_SYSTEM_PROMPT and "5" in DM_SYSTEM_PROMPT


class TestCompanionPromptStub:
    def test_is_a_string(self):
        assert isinstance(COMPANION_SYSTEM_PROMPT, str)
        assert len(COMPANION_SYSTEM_PROMPT) > 50

    def test_no_longer_phase_8_stub(self):
        # The Phase 8 companion implementation is now real; the prompt
        # should not be marked as a stub. (Phase 6 had a "Phase 8" tag
        # which has been replaced by the real prompt.)
        assert isinstance(COMPANION_SYSTEM_PROMPT, str)
        assert "Phase 8" not in COMPANION_SYSTEM_PROMPT


# ============================================================================
# Context builder
# ============================================================================


class TestBuildDMContextBlock:
    def test_includes_world_state(self, state):
        block = build_dm_context_block(state)
        assert "Floresta de Fangorn" in block
        assert "amanhecer" in block
        assert "neblina" in block

    def test_includes_party(self, state):
        block = build_dm_context_block(state)
        assert "Aragorn" in block
        assert "Legolas" in block
        assert "[JOGADOR]" in block  # marks the player's character

    def test_includes_class_and_level(self, state):
        block = build_dm_context_block(state)
        assert "Fighter" in block
        assert "L1" in block

    def test_includes_narrative_log(self, state):
        state.append_narrative(
            NarrativeEntry(
                timestamp=datetime.now(timezone.utc),
                role="dm",
                speaker="DM",
                content="A névoa se adensa à sua frente.",
            )
        )
        block = build_dm_context_block(state)
        assert "névoa" in block

    def test_handles_empty_party(self):
        # Edge case: no companions yet
        s = StateManager(
            GameState(
                campaign_name="solo",
                started_at=datetime.now(timezone.utc),
                party=[],
                player_character_id="x",
            )
        )
        block = build_dm_context_block(s)
        assert "(vazia)" in block or "vazia" in block.lower()

    def test_includes_combat_marker(self):
        sm = StateManager(_make_state())
        sm.state.in_combat = True
        sm.state.round_number = 3
        block = build_dm_context_block(sm)
        assert "EM COMBATE" in block
        assert "3" in block

    def test_includes_quests(self):
        from auto_dm.state.models import Quest

        sm = StateManager(_make_state())
        sm.state.active_quests.append(
            Quest(id="q1", name="A Espada Perdida", description="Encontre a lâmina élfica")
        )
        block = build_dm_context_block(sm)
        assert "Espada Perdida" in block

    def test_respects_last_n(self, state):
        for i in range(10):
            state.append_narrative(
                NarrativeEntry(
                    timestamp=datetime.now(timezone.utc),
                    role="dm",
                    speaker="DM",
                    content=f"entrada número {i}",
                )
            )
        block = build_dm_context_block(state, last_n=3)
        assert "entrada número 9" in block
        assert "entrada número 8" in block
        assert "entrada número 7" in block
        assert "entrada número 0" not in block

    def test_action_schema_helper_returns_string(self):
        schema = get_action_json_schema_description()
        assert "action_type" in schema
        assert "actor_id" in schema


# ============================================================================
# Response parser
# ============================================================================


class TestParseDMResponse:
    def test_narration_only(self):
        resp = parse_dm_response("Você caminha pela floresta úmida.")
        assert resp.narration == "Você caminha pela floresta úmida."
        assert resp.action is None
        assert not resp.has_action

    def test_empty_input(self):
        resp = parse_dm_response("")
        assert resp.narration == ""
        assert resp.action is None

    def test_with_valid_action_block(self):
        text = dm_response(
            "Você saca sua espada e avança.",
            {
                "action_type": "attack",
                "actor_id": "p1",
                "target_id": "orc1",
                "params": {"weapon": "longsword"},
            },
        )
        resp = parse_dm_response(text)
        assert "espada" in resp.narration
        assert resp.action is not None
        assert resp.action.action_type == ActionType.ATTACK
        assert resp.action.actor_id == "p1"
        assert resp.action.target_id == "orc1"
        assert resp.action.params == {"weapon": "longsword"}

    def test_action_block_with_dialogue(self):
        text = dm_response(
            "Você ergue a voz.",
            {
                "action_type": "say",
                "actor_id": "p1",
                "dialogue": "Paz, viajante! Eu venho em amizade.",
            },
        )
        resp = parse_dm_response(text)
        assert resp.action.action_type == ActionType.SAY
        assert resp.action.dialogue is not None
        assert "amizade" in resp.action.dialogue

    def test_malformed_json_drops_action_but_keeps_narration(self):
        text = "Você ataca o goblin.\n```action\n{ this is not json }\n```"
        resp = parse_dm_response(text)
        assert "goblin" in resp.narration
        assert resp.action is None  # block was dropped

    def test_missing_required_field_drops_action(self):
        text = dm_response(
            "Você tenta algo estranho.",
            {"action_type": "attack"},  # no actor_id
        )
        resp = parse_dm_response(text)
        assert resp.action is None
        assert "estranho" in resp.narration

    def test_unknown_action_type_drops_action(self):
        text = dm_response(
            "Você faz algo impossível.",
            {"action_type": "fly_to_mars", "actor_id": "p1"},
        )
        resp = parse_dm_response(text)
        assert resp.action is None

    def test_action_block_only_no_preamble(self):
        text = dm_response(
            "",
            {"action_type": "move", "actor_id": "p1", "params": {"destination": "norte"}},
        )
        resp = parse_dm_response(text)
        assert resp.action is not None
        assert resp.action.action_type == ActionType.MOVE
        assert resp.narration == ""

    def test_raw_text_preserved(self):
        text = dm_response("narra", {})
        resp = parse_dm_response(text)
        assert resp.raw_text == text

    def test_multiple_action_blocks_only_first_used(self):
        text = (
            "A\n"
            + dm_response("", {"action_type": "say", "actor_id": "p1", "dialogue": "oi"})
            + "\nB\n"
            + dm_response("", {"action_type": "attack", "actor_id": "p1"})
            + "\n"
        )
        resp = parse_dm_response(text)
        # First block wins; second block ends up in narration.
        assert resp.action is not None
        assert resp.action.action_type == ActionType.SAY

    def test_preserves_internal_newlines_in_block(self):
        # Build the JSON with literal newlines inside the action fence.
        text = (
            "narra\n```action\n"
            + '{\n  "action_type": "attack",\n  "actor_id": "p1"\n}\n'
            + "```\npós"
        )
        resp = parse_dm_response(text)
        assert resp.action is not None
        assert resp.action.action_type == ActionType.ATTACK
        assert "pós" in resp.narration


# ============================================================================
# DMAgent (mocked LLM)
# ============================================================================


class TestDMAgent:
    def test_ask_calls_provider(self, state):
        provider = FakeProvider(scripted=["Você olha ao redor."])
        agent = DMAgent(provider=provider, state_manager=state)
        resp = agent.ask("O que você vê?")
        assert resp.narration == "Você olha ao redor."
        assert len(provider.calls) == 1

    def test_ask_includes_player_input(self, state):
        provider = FakeProvider(scripted=["ok"])
        agent = DMAgent(provider=provider, state_manager=state)
        agent.ask("Investigo a pedra.")
        messages = provider.calls[0]
        last_user = [m for m in messages if m.role == "user"][-1]
        assert "Investigo a pedra." in last_user.content

    def test_messages_start_with_system_prompt(self, state):
        provider = FakeProvider(scripted=["ok"])
        agent = DMAgent(provider=provider, state_manager=state)
        agent.ask("olá")
        messages = provider.calls[0]
        assert messages[0].role == "system"
        assert "Mestre de RPG" in messages[0].content

    def test_system_message_includes_state_context(self, state):
        provider = FakeProvider(scripted=["ok"])
        agent = DMAgent(provider=provider, state_manager=state)
        agent.ask("olá")
        sys_content = provider.calls[0][0].content
        assert "Floresta de Fangorn" in sys_content
        assert "Aragorn" in sys_content

    def test_recent_history_included(self, state):
        state.append_narrative(
            NarrativeEntry(
                timestamp=datetime.now(timezone.utc),
                role="dm",
                speaker="DM",
                content="Você entra na taverna.",
            )
        )
        state.append_narrative(
            NarrativeEntry(
                timestamp=datetime.now(timezone.utc),
                role="player",
                speaker="Jogador",
                content="Peço uma cerveja.",
            )
        )
        provider = FakeProvider(scripted=["ok"])
        agent = DMAgent(provider=provider, state_manager=state)
        agent.ask("olho ao redor")
        messages = provider.calls[0]
        contents = " ".join(m.content for m in messages)
        assert "taverna" in contents
        assert "cerveja" in contents

    def test_history_capped_at_last_n(self, state):
        for i in range(20):
            state.append_narrative(
                NarrativeEntry(
                    timestamp=datetime.now(timezone.utc),
                    role="dm",
                    speaker="DM",
                    content=f"entrada antiga {i}",
                )
            )
        provider = FakeProvider(scripted=["ok"])
        agent = DMAgent(provider=provider, state_manager=state, last_n_history=3)
        agent.ask("x")
        messages = provider.calls[0]
        # system + 3 history + 1 user = 5
        assert len(messages) == 5

    def test_ask_returns_action_when_present(self, state):
        provider = FakeProvider(
            scripted=[
                dm_response(
                    "Você ataca.",
                    {"action_type": "attack", "actor_id": "p1"},
                )
            ]
        )
        agent = DMAgent(provider=provider, state_manager=state)
        resp = agent.ask("ataco")
        assert resp.has_action
        assert resp.action.action_type == ActionType.ATTACK

    def test_extra_messages_injected(self, state):
        provider = FakeProvider(scripted=["ok"])
        agent = DMAgent(
            provider=provider,
            state_manager=state,
            extra_messages=[
                Message(role="system", content="Lembrete: hoje é sexta-feira.")
            ],
        )
        agent.ask("olá")
        messages = provider.calls[0]
        assert any("sexta-feira" in m.content for m in messages)

    def test_custom_system_prompt(self, state):
        provider = FakeProvider(scripted=["ok"])
        agent = DMAgent(
            provider=provider,
            state_manager=state,
            system_prompt="Você é um mestre sombrio.",
        )
        agent.ask("olá")
        assert "sombrio" in provider.calls[0][0].content


# ============================================================================
# Narrative loop
# ============================================================================


class TestProcessPlayerAction:
    def test_logs_player_and_dm(self, state):
        provider = FakeProvider(scripted=["Você ouve um galho quebrando."])
        agent = DMAgent(provider=provider, state_manager=state)
        before = len(state.state.narrative_log)
        result = process_player_action(state, "olho em volta", agent)
        after = len(state.state.narrative_log)
        assert after == before + 2  # player + dm
        assert result.narration == "Você ouve um galho quebrando."
        assert not result.has_action

    def test_player_entry_appears_first(self, state):
        provider = FakeProvider(scripted=["DM diz: olá"])
        agent = DMAgent(provider=provider, state_manager=state)
        process_player_action(state, "ação do jogador", agent)
        last_two = state.state.narrative_log[-2:]
        assert last_two[0].role == "player"
        assert last_two[0].content == "ação do jogador"
        assert last_two[1].role == "dm"
        assert last_two[1].speaker == "DM"

    def test_narration_only_no_followup(self, state):
        provider = FakeProvider(scripted=["narra puro"])
        agent = DMAgent(provider=provider, state_manager=state)
        result = process_player_action(state, "olho", agent)
        assert result.follow_up_narration is None
        assert len(provider.calls) == 1

    def test_say_action_is_flavor_only(self, state):
        provider = FakeProvider(
            scripted=[
                dm_response(
                    "Você fala.",
                    {
                        "action_type": "say",
                        "actor_id": "p1",
                        "dialogue": "Saudações.",
                    },
                )
            ]
        )
        agent = DMAgent(provider=provider, state_manager=state)
        result = process_player_action(state, "saúdo o guarda", agent)
        assert result.has_action
        assert result.action_result is None  # say is pure flavor
        assert result.follow_up_narration is None

    def test_move_action_returns_stub_result(self, state):
        provider = FakeProvider(
            scripted=[
                dm_response(
                    "Você caminha para o norte.",
                    {
                        "action_type": "move",
                        "actor_id": "p1",
                        "params": {"destination": "norte"},
                    },
                )
            ]
        )
        agent = DMAgent(provider=provider, state_manager=state)
        result = process_player_action(state, "vou para o norte", agent)
        assert result.has_action
        assert result.action_result is not None
        assert result.action_result.success
        assert "norte" in result.action_result.message

    def test_short_rest_stub(self, state):
        provider = FakeProvider(
            scripted=[
                dm_response(
                    "Você descansa.",
                    {"action_type": "short_rest", "actor_id": "p1"},
                )
            ]
        )
        agent = DMAgent(provider=provider, state_manager=state)
        result = process_player_action(state, "descanso curto", agent)
        assert result.action_result is not None
        assert result.action_result.mechanical.get("rest") == "short"

    def test_long_rest_stub(self, state):
        provider = FakeProvider(
            scripted=[
                dm_response(
                    "Você acampa.",
                    {"action_type": "long_rest", "actor_id": "p1"},
                )
            ]
        )
        agent = DMAgent(provider=provider, state_manager=state)
        result = process_player_action(state, "descanso longo", agent)
        assert result.action_result is not None
        assert result.action_result.mechanical.get("rest") == "long"

    def test_combat_action_stub_without_engine(self, state):
        provider = FakeProvider(
            scripted=[
                dm_response(
                    "Você ataca o goblin.",
                    {
                        "action_type": "attack",
                        "actor_id": "p1",
                        "target_id": "gob1",
                    },
                )
            ]
        )
        agent = DMAgent(provider=provider, state_manager=state)
        result = process_player_action(state, "ataco", agent)
        # Stubbed result — no follow-up narration.
        assert result.action_result is not None
        assert result.action_result.mechanical.get("stub") is True
        assert result.follow_up_narration is None

    def test_combat_engine_called_when_provided(self, state):
        received: list[Action] = []

        class FakeCombatEngine:
            def execute_action(self, sm: StateManager, action: Action):
                received.append(action)
                from auto_dm.state.models import ActionResult
                return ActionResult(
                    success=True,
                    message="Acerto! 7 de dano.",
                    mechanical={"damage": 7, "hit": True},
                )

        provider = FakeProvider(
            scripted=[
                dm_response(
                    "Você avança.",
                    {
                        "action_type": "attack",
                        "actor_id": "p1",
                        "target_id": "orc1",
                    },
                ),
                "O goblin cambaleia sob o golpe.",  # follow-up
            ]
        )
        agent = DMAgent(provider=provider, state_manager=state)
        result = process_player_action(
            state, "ataco", agent, combat_engine=FakeCombatEngine()
        )
        assert len(received) == 1
        assert received[0].action_type == ActionType.ATTACK
        assert result.action_result.success
        assert result.follow_up_narration is not None
        assert len(provider.calls) == 2

    def test_combat_engine_exception_caught(self, state):
        class BrokenEngine:
            def execute_action(self, sm: StateManager, action: Action):
                raise RuntimeError("engine down")

        provider = FakeProvider(
            scripted=[
                dm_response(
                    "Você tenta.",
                    {"action_type": "attack", "actor_id": "p1"},
                )
            ]
        )
        agent = DMAgent(provider=provider, state_manager=state)
        result = process_player_action(
            state, "ataco", agent, combat_engine=BrokenEngine()
        )
        assert result.action_result is not None
        assert not result.action_result.success
        assert "engine down" in result.action_result.message

    def test_follow_up_uses_fresh_dm_call(self, state):
        from auto_dm.state.models import ActionResult

        class Engine:
            def execute_action(self, sm, action):
                return ActionResult(
                    success=True, message="Acerto!", mechanical={"damage": 5}
                )

        provider = FakeProvider(
            scripted=[
                dm_response(
                    "Você ataca.",
                    {
                        "action_type": "attack",
                        "actor_id": "p1",
                        "target_id": "gob1",
                    },
                ),
                "O goblin cambaleia sob o golpe.",
            ]
        )
        agent = DMAgent(provider=provider, state_manager=state)
        result = process_player_action(state, "ataco", agent, combat_engine=Engine())
        assert result.follow_up_narration == "O goblin cambaleia sob o golpe."
        # The follow-up DM call should mention the damage.
        follow_up_prompt = provider.calls[1][-1].content
        assert "Acerto" in follow_up_prompt or "5" in follow_up_prompt


# ============================================================================
# Integration: full loop with state propagation
# ============================================================================


class TestIntegration:
    def test_history_grows_across_turns(self, state):
        provider = FakeProvider(
            scripted=[
                "Resposta 1",
                "Resposta 2",
            ]
        )
        agent = DMAgent(provider=provider, state_manager=state)
        process_player_action(state, "turno 1", agent)
        process_player_action(state, "turno 2", agent)
        # 2 turns × 2 entries each = 4 narrative entries.
        assert len(state.state.narrative_log) == 4

    def test_second_turn_sees_first_narration_in_history(self, state):
        provider = FakeProvider(scripted=["DM disse: primeira", "ok"])
        agent = DMAgent(provider=provider, state_manager=state)
        process_player_action(state, "turno 1", agent)
        process_player_action(state, "turno 2", agent)
        second_call_messages = provider.calls[1]
        contents = " ".join(m.content for m in second_call_messages)
        assert "primeira" in contents


# ============================================================================
# Campaign opening narration (no player input)
# ============================================================================


class TestOpeningNarration:
    """The opening is generated before the player acts, so the DM must
    establish the scene and choose a starting location on its own."""

    def test_generate_opening_uses_opening_trigger_as_last_user_message(self, state):
        provider = FakeProvider(scripted=["Uma névoa cobre o campo."])
        agent = DMAgent(provider=provider, state_manager=state)
        agent.generate_opening()
        messages = provider.calls[0]
        last_user = [m for m in messages if m.role == "user"][-1]
        assert last_user.content == OPENING_INSTRUCTION

    def test_generate_opening_returns_parsed_response(self, state):
        provider = FakeProvider(
            scripted=[
                dm_response(
                    "Você acorda numa estrada poeirenta.",
                    {
                        "action_type": "move",
                        "actor_id": "p1",
                        "params": {"destination": "Estrada do Norte"},
                    },
                )
            ]
        )
        agent = DMAgent(provider=provider, state_manager=state)
        resp = agent.generate_opening()
        assert "estrada poeirenta" in resp.narration
        assert resp.action is not None
        assert resp.action.action_type == ActionType.MOVE
        assert resp.action.params["destination"] == "Estrada do Norte"

    def test_narrative_generate_opening_logs_dm_only(self, state):
        provider = FakeProvider(
            scripted=[
                dm_response(
                    "O porto ferve de activity ao amanhecer.",
                    {
                        "action_type": "move",
                        "actor_id": "p1",
                        "params": {"destination": "Porto de Saltmarsh"},
                    },
                )
            ]
        )
        agent = DMAgent(provider=provider, state_manager=state)
        before = len(state.state.narrative_log)
        result = generate_opening(state, agent)
        # Exactly one new entry, and it's a DM entry — never a player line.
        assert len(state.state.narrative_log) == before + 1
        entry = state.state.narrative_log[-1]
        assert entry.role == "dm"
        assert entry.speaker == "DM"
        assert "porto" in entry.content
        assert not any(e.role == "player" for e in state.state.narrative_log[before:])
        assert result.narration == entry.content

    def test_narrative_generate_opening_applies_chosen_location(self, state):
        # Start with no defined location — the DM picks it.
        state.state.current_location = ""
        provider = FakeProvider(
            scripted=[
                dm_response(
                    "Fumaça sobe do acampamento mercenário.",
                    {
                        "action_type": "move",
                        "actor_id": "p1",
                        "params": {"destination": "Acampamento Mercenário"},
                    },
                )
            ]
        )
        agent = DMAgent(provider=provider, state_manager=state)
        generate_opening(state, agent)
        assert state.state.current_location == "Acampamento Mercenário"

    def test_narrative_generate_opening_without_move_keeps_location(self, state):
        # If the DM forgets the move action, we just log the narration;
        # current_location is left unchanged (no crash).
        state.state.current_location = ""
        provider = FakeProvider(scripted=["Uma vila adormecida."])
        agent = DMAgent(provider=provider, state_manager=state)
        result = generate_opening(state, agent)
        assert result.action is None
        assert state.state.current_location == ""
        assert state.state.narrative_log[-1].role == "dm"

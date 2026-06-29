"""Phase 25h tests: companion turn integration, meta-commands, rendering.

These tests cover the critical bug fix (companions now take turns
during combat) and the expanded REPL meta-commands
(``/encounter``, ``/look``, ``/inventory``, ``/conditions``,
``/spells``), plus the new :class:`CombatEngine` helpers
(``next_actor_id``, ``is_player_turn``, ``is_companion_turn``) and
the new :mod:`cli.rendering` helpers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import random

import pytest

from auto_dm.agents import NarrativeResult
from auto_dm.cli import (
    GameApp,
    render_conditions,
    render_inventory,
    render_spellbook,
)
from auto_dm.cli.app import META_COMMANDS
from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.llm.base import LLMConfig, Message
from auto_dm.phb import set_phb_root
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    AbilityScores,
    Character,
    Condition,
    GameState,
    Item,
    ItemType,
    NPC,
    Spellcasting,
)
from auto_dm.state.monster_adapter import monster_to_npc, slugify_monster_id


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_phb_cache():
    """Make sure PHB root points at the real data dir for monster
    lookups used by the new meta-commands."""
    from auto_dm.phb import get_phb_root as _gpr

    original_root = _gpr()
    real_root = Path(__file__).resolve().parents[1] / "data" / "phb"
    set_phb_root(real_root)
    yield
    set_phb_root(original_root)


class ScriptedProvider:
    """LLM provider that scripts DM and companion responses in order."""

    def __init__(self, scripted: Optional[list[str]] = None) -> None:
        self.scripted = list(scripted or [])
        self.calls: list[list[Message]] = []
        self.config = LLMConfig(name="scripted", api_key="test", model="test")
        self.name = "scripted"

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


def _ability() -> AbilityScores:
    return AbilityScores(
        strength=14, dexterity=12, constitution=13,
        intelligence=10, wisdom=11, charisma=8,
    )


def _make_player(name: str = "Aragorn", player_id: str = "p1") -> Character:
    return Character(
        id=player_id,
        name=name,
        race="Human",
        **{"class": "Fighter"},
        level=1,
        background="Soldier",
        alignment="LG",
        is_player=True,
        abilities=_ability(),
        hp_current=30,
        hp_max=30,
        armor_class=18,
        speed=30,
        proficiency_bonus=2,
        hit_dice="1d10",
        hit_dice_remaining=1,
    )


def _make_companion(
    name: str, cid: str, *, hp: int = 25,
) -> Character:
    return Character(
        id=cid,
        name=name,
        race="Dwarf",
        **{"class": "Cleric"},
        level=1,
        background="Acolyte",
        alignment="LG",
        is_player=False,
        abilities=_ability(),
        hp_current=hp,
        hp_max=hp,
        armor_class=16,
        speed=25,
        proficiency_bonus=2,
        hit_dice="1d8",
        hit_dice_remaining=1,
    )


def _make_npc(name: str, npc_id: str, *, hp: int = 7, ac: int = 12) -> NPC:
    return NPC(
        id=npc_id,
        name=name,
        hp_current=hp,
        hp_max=hp,
        armor_class=ac,
        speed=30,
        abilities=AbilityScores(
            strength=8, dexterity=14, constitution=10,
            intelligence=10, wisdom=8, charisma=8,
        ),
    )


def _make_state(
    *, include_companions: bool = True, include_npc: bool = True,
) -> GameState:
    party = [_make_player()]
    if include_companions:
        party.append(_make_companion("Mira", "c1"))
        party.append(_make_companion("Vex", "c2"))
    npcs = [_make_npc("Goblin", "g1")] if include_npc else []
    return GameState(
        campaign_name="Phase 25h Test",
        started_at=datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc),
        current_location="Floresta Sombria",
        party=party,
        npcs=npcs,
        player_character_id="p1",
    )


def _make_app(
    *,
    tmp_path: Path,
    scripted: Optional[list[str]] = None,
    state: Optional[GameState] = None,
    with_companions: bool = True,
    with_combat: bool = True,
    seed: Optional[int] = 42,
) -> GameApp:
    """Build a GameApp for testing.

    The provider factory returns the *same* ScriptedProvider instance on
    every call, so DM and companion agents share one scripted list. The
    list is consumed in call order (DM first, then companions in the
    order they actually get a turn — which depends on initiative).

    ``seed`` makes the combat engine deterministic so the companion
    order is predictable. Pass ``seed=None`` for random.
    """
    state = state or _make_state(include_companions=with_companions)
    shared_provider = ScriptedProvider(scripted)
    rng = random.Random(seed) if seed is not None else random.Random()
    app = GameApp(
        state_manager=StateManager(state),
        provider_factory=lambda: shared_provider,
        saves_dir=tmp_path / "saves",
        auto_save_every_n_turns=0,
        combat_engine=CombatEngine(rng=rng) if with_combat else None,
    )
    # Always initialize so the DM agent exists, even if the party has
    # only the player. ``with_companions`` controls only the *content*
    # of the party in the fixture state, not whether initialize() runs.
    app.initialize()
    return app


# ===========================================================================
# CombatEngine helpers (Phase 25h)
# ===========================================================================


class TestCombatEngineNextActor:
    def test_next_actor_id_returns_next_in_order(self, tmp_path):
        sm = StateManager(_make_state())
        engine = CombatEngine()
        engine.start_combat(sm)
        # The "next" actor should be the one at index +1 in the order
        # (wrapping around at the end).
        order = sm.state.initiative_order
        idx = sm.state.current_turn_index
        expected = order[(idx + 1) % len(order)]
        actual = engine.next_actor_id(sm)
        assert actual == expected
        assert actual in order

    def test_next_actor_id_wraps(self, tmp_path):
        sm = StateManager(_make_state())
        engine = CombatEngine()
        order = engine.start_combat(sm)
        # Manually advance to the last actor; next should wrap to first.
        sm.state.current_turn_index = len(order) - 1
        wrapped = engine.next_actor_id(sm)
        assert wrapped == order[0]

    def test_next_actor_id_none_when_not_in_combat(self, tmp_path):
        sm = StateManager(_make_state())
        engine = CombatEngine()
        assert engine.next_actor_id(sm) is None

    def test_is_player_turn_true_for_player(self, tmp_path):
        sm = StateManager(_make_state())
        engine = CombatEngine()
        engine.start_combat(sm)
        # Force player's index
        player_idx = sm.state.initiative_order.index(sm.state.player_character_id)
        sm.state.current_turn_index = player_idx
        assert engine.is_player_turn(sm) is True

    def test_is_player_turn_false_for_companion(self, tmp_path):
        sm = StateManager(_make_state())
        engine = CombatEngine()
        engine.start_combat(sm)
        companion_id = "c1"
        if companion_id in sm.state.initiative_order:
            sm.state.current_turn_index = sm.state.initiative_order.index(companion_id)
            assert engine.is_player_turn(sm) is False

    def test_is_companion_turn_for_companion(self, tmp_path):
        sm = StateManager(_make_state())
        engine = CombatEngine()
        engine.start_combat(sm)
        companion_id = "c1"
        sm.state.current_turn_index = sm.state.initiative_order.index(companion_id)
        assert engine.is_companion_turn(sm) is True
        assert engine.is_player_turn(sm) is False


# ===========================================================================
# NarrativeResult.companion_results field
# ===========================================================================


class TestNarrativeResultCompanionField:
    def test_companion_results_defaults_to_empty(self):
        # Construct with the minimum required args; field default empty list.
        r = NarrativeResult(narration="ok")
        assert r.companion_results == []
        assert isinstance(r.companion_results, list)

    def test_companion_results_can_be_set(self):
        r = NarrativeResult(narration="ok", companion_results=[])
        r.companion_results.append("anything")  # list is mutable
        assert len(r.companion_results) == 1


# ===========================================================================
# Companion turn cycle — the critical bug fix
# ===========================================================================


class TestCompanionCycle:
    def test_no_companion_turns_when_not_in_combat(self, tmp_path):
        app = _make_app(tmp_path=tmp_path)
        # Pre-populate scripted DM response
        app._dm_agent.provider.scripted = ["Você olha ao redor."]
        result = app.process_input("olho ao redor")
        assert result is not None
        assert result.companion_results == []

    def test_companion_turn_runs_after_player_in_combat(self, tmp_path):
        """The critical bug fix: companion acts after player."""
        app = _make_app(
            tmp_path=tmp_path,
            scripted=[
                # 1. DM response to player's "ataco" action
                "Você golpeia o goblin.\n```action\n"
                '{"action_type": "attack", "actor_id": "p1", "target_id": "g1"}\n'
                "```",
                # 2. DM follow-up narration for the result
                "O goblin recua.",
                # 3. Companion Mira's decision (intent + attack)
                "Mira avança.\n```action\n"
                '{"action_type": "attack", "actor_id": "c1", "target_id": "g1"}\n'
                "```",
                # 4. Companion Vex's decision
                "Vex ergue a clava.\n```action\n"
                '{"action_type": "attack", "actor_id": "c2", "target_id": "g1"}\n'
                "```",
            ],
        )
        # Force combat setup
        engine = app.combat_engine
        assert engine is not None
        order = engine.start_combat(app.state_manager)
        # Make sure p1 acts first.
        p1_idx = order.index("p1")
        app.state_manager.state.current_turn_index = p1_idx
        # Confirm there are 2 companions in the order.
        assert "c1" in order
        assert "c2" in order

        result = app.process_input("ataco o goblin")
        assert result is not None
        # Player's turn ran
        assert result.action is not None
        assert result.action.action_type.value == "attack"
        # Companion cycle ran both companions (c1 then c2)
        assert len(result.companion_results) == 2
        names = [t.actor_name for t in result.companion_results]
        assert names == ["Mira", "Vex"]

    def test_companion_turn_does_not_run_when_no_companion(self, tmp_path):
        """If party has only the player, cycle is empty."""
        app = _make_app(
            tmp_path=tmp_path,
            with_companions=False,
            scripted=[
                "Você golpeia.\n```action\n"
                '{"action_type": "attack", "actor_id": "p1", "target_id": "g1"}\n'
                "```",
            ],
        )
        engine = app.combat_engine
        assert engine is not None
        order = engine.start_combat(app.state_manager)
        p1_idx = order.index("p1")
        app.state_manager.state.current_turn_index = p1_idx

        result = app.process_input("ataco o goblin")
        assert result is not None
        assert result.companion_results == []

    def test_companion_turn_intent_logged(self, tmp_path):
        """At least one companion intent should appear in the narrative log."""
        app = _make_app(
            tmp_path=tmp_path,
            scripted=[
                # DM response for player's action
                "DM act.\n```action\n"
                '{"action_type": "attack", "actor_id": "p1", "target_id": "g1"}\n'
                "```",
                # DM followup
                "DM followup.",
                # Companion 1: attack (actor_id filled in by parser)
                "Companion brande a clava.\n```action\n"
                '{"action_type": "attack", "target_id": "g1"}\n'
                "```",
                # Companion 2: another attack
                "Outro companheiro ataca.\n```action\n"
                '{"action_type": "attack", "target_id": "g1"}\n'
                "```",
            ],
        )
        engine = app.combat_engine
        assert engine is not None
        order = engine.start_combat(app.state_manager)
        app.state_manager.state.current_turn_index = order.index("p1")

        result = app.process_input("ataco o goblin")
        # Both companions should have acted (we scripted responses for both)
        assert len(result.companion_results) == 2
        narrative_texts = [
            e.content for e in app.state_manager.state.narrative_log
        ]
        # The companion intents ("brande a clava" / "Outro companheiro ataca")
        # should appear in the narrative log.
        assert any("brande a clava" in t for t in narrative_texts)
        assert any("Outro companheiro ataca" in t for t in narrative_texts)


# ===========================================================================
# Meta-commands — Phase 25h additions
# ===========================================================================


class TestMetaCommandsList:
    def test_help_mentions_new_commands(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path)
        app.process_input("/help")
        out = capsys.readouterr().out
        assert "/encounter" in out
        assert "/look" in out
        assert "/inventory" in out
        assert "/conditions" in out
        assert "/spells" in out

    def test_meta_commands_dict_includes_new_entries(self):
        # Module-level dict used by external callers. Keys are the
        # command + signature hint, so we match by prefix.
        prefixes = ["/encounter", "/look", "/inventory", "/conditions", "/spells"]
        for prefix in prefixes:
            assert any(
                key == prefix or key.startswith(prefix + " ")
                for key in META_COMMANDS
            ), f"missing meta command starting with {prefix!r}"


class TestEncounterCommand:
    def test_encounter_spawns_monsters_and_starts_combat(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path, with_companions=False)
        assert len(app.state_manager.state.npcs) == 1  # the fixture's NPC
        app.process_input("/encounter Goblin, Goblin, Orc")
        # The /encounter command appends new NPCs; the fixture's npc is
        # still there, but at minimum the 3 new ones were added.
        out = capsys.readouterr().out
        assert "Combate iniciado" in out
        # 3 new NPCs were added (1 fixture + 3 spawned = 4 total)
        assert len(app.state_manager.state.npcs) == 4
        # IDs are unique and slug-based
        ids = {n.id for n in app.state_manager.state.npcs}
        assert "goblin_1" in ids
        assert "goblin_2" in ids
        assert "orc_3" in ids
        # Combat is active
        assert app.state_manager.state.in_combat

    def test_encounter_unknown_monster_is_skipped(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path, with_companions=False)
        app.process_input("/encounter Goblin, NonexistentBeast")
        out = capsys.readouterr().out
        assert "NonexistentBeast" in out
        # Goblin still spawned
        ids = {n.id for n in app.state_manager.state.npcs}
        assert "goblin_1" in ids
        assert app.state_manager.state.in_combat

    def test_encounter_empty_arg_shows_usage(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path, with_companions=False)
        app.process_input("/encounter")
        out = capsys.readouterr().out
        assert "Uso:" in out

    def test_encounter_refuses_when_already_in_combat(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path, with_companions=False)
        # Force in_combat
        app.state_manager.state.in_combat = True
        app.state_manager.state.initiative_order = ["p1"]
        app.process_input("/encounter Goblin")
        out = capsys.readouterr().out
        assert "combate" in out.lower()


class TestLookCommand:
    def test_look_shows_location_and_party(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path, with_companions=False)
        app.process_input("/look")
        out = capsys.readouterr().out
        assert "Floresta Sombria" in out
        assert "Aragorn" in out
        assert "Goblin" in out

    def test_look_marks_player_with_arrow(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path, with_companions=False)
        app.process_input("/look")
        out = capsys.readouterr().out
        # Player row should contain ▶ marker.
        assert "▶" in out


class TestInventoryCommand:
    def test_inventory_shows_player_items(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path, with_companions=False)
        # Add an item to the player
        app.state_manager.state.party[0].inventory.append(
            Item(name="Longsword", type=ItemType.WEAPON, quantity=1),
        )
        app.process_input("/inventory")
        out = capsys.readouterr().out
        assert "Longsword" in out

    def test_inventory_empty_player(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path, with_companions=False)
        app.process_input("/inventory")
        out = capsys.readouterr().out
        # Either "vazio" message or "Inventário" header is fine.
        assert "Inventário" in out or "vazio" in out

    def test_inventory_for_companion_by_name(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path, with_companions=True)
        # Add item to Mira
        mira = next(c for c in app.state_manager.state.party if c.name == "Mira")
        mira.inventory.append(Item(name="Mace", type=ItemType.WEAPON))
        app.process_input("/inventory Mira")
        out = capsys.readouterr().out
        assert "Mace" in out


class TestConditionsCommand:
    def test_conditions_for_clean_character(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path, with_companions=False)
        app.process_input("/conditions")
        out = capsys.readouterr().out
        assert "nenhuma" in out.lower() or "Condições" in out

    def test_conditions_show_active_condition(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path, with_companions=False)
        app.state_manager.state.party[0].conditions.append(Condition.POISONED)
        app.process_input("/conditions")
        out = capsys.readouterr().out
        assert "poisoned" in out


class TestSpellsCommand:
    def test_spells_for_non_caster(self, tmp_path, capsys):
        # Fighter doesn't get spellcasting.
        app = _make_app(tmp_path=tmp_path, with_companions=False)
        app.process_input("/spells")
        out = capsys.readouterr().out
        # The render_spellbook returns "X não é capaz de lançar magias."
        assert "capaz" in out.lower() or "magias" in out.lower()

    def test_spells_for_caster_with_slots(self, tmp_path, capsys):
        # Build a player cleric with spells. We need is_player=True so
        # _find_party_member("") resolves to her.
        cleric = Character(
            id="p1",
            name="Alia",
            race="Human",
            **{"class": "Cleric"},
            level=1,
            background="Acolyte",
            alignment="LG",
            is_player=True,
            abilities=_ability(),
            hp_current=20,
            hp_max=20,
            armor_class=16,
            speed=30,
            proficiency_bonus=2,
            hit_dice="1d8",
            hit_dice_remaining=1,
            spellcasting=Spellcasting(
                ability="wisdom",
                save_dc=13,
                attack_bonus=5,
                cantrips_known=["Sacred Flame", "Light"],
                spells_prepared=["Cure Wounds", "Bless"],
                spell_slots={1: 2, 2: 1},
                spell_slots_max={1: 2, 2: 1},
            ),
        )
        state = GameState(
            campaign_name="Phase 25h Test",
            started_at=datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc),
            current_location="Templo",
            party=[cleric],
            npcs=[],
            player_character_id="p1",
        )
        app = _make_app(tmp_path=tmp_path, state=state, with_companions=False)
        app.process_input("/spells")
        out = capsys.readouterr().out
        assert "Sacred Flame" in out
        assert "Cure Wounds" in out
        assert "1º: 2/2" in out
        assert "2º: 1/1" in out


# ===========================================================================
# Rendering helpers
# ===========================================================================


class TestRenderInventory:
    def test_empty_inventory_returns_panel(self):
        c = _make_player()
        renderable = render_inventory(c)
        # Should be a Panel or Table; either way it should be importable.
        assert renderable is not None

    def test_with_items_returns_table(self):
        c = _make_player()
        c.inventory.append(Item(name="Potion of Healing", type=ItemType.CONSUMABLE))
        renderable = render_inventory(c)
        # Either a Table or a Panel; both are valid renderables.
        assert renderable is not None


class TestRenderConditions:
    def test_no_conditions_shows_none(self):
        c = _make_player()
        renderable = render_conditions(c)
        assert "nenhuma" in str(renderable.renderable)

    def test_with_poisoned(self):
        c = _make_player()
        c.conditions.append(Condition.POISONED)
        renderable = render_conditions(c)
        assert "poisoned" in str(renderable.renderable)


class TestRenderSpellbook:
    def test_non_caster_message(self):
        c = _make_player()
        renderable = render_spellbook(c)
        assert "não é capaz" in str(renderable.renderable)

    def test_caster_shows_cantrips_and_slots(self):
        c = _make_player()
        c.spellcasting = Spellcasting(
            ability="wisdom",
            save_dc=13,
            cantrips_known=["Light"],
            spells_prepared=["Bless"],
            spell_slots={1: 1},
            spell_slots_max={1: 1},
        )
        renderable = render_spellbook(c)
        text = str(renderable.renderable)
        assert "Light" in text
        assert "Bless" in text
        assert "1º: 1/1" in text


# ===========================================================================
# Public slugify_monster_id export
# ===========================================================================


class TestSlugifyMonsterId:
    def test_simple(self):
        assert slugify_monster_id("Goblin") == "goblin"

    def test_multi_word(self):
        assert slugify_monster_id("Adult Red Dragon") == "adult_red_dragon"

    def test_monster_to_npc_uses_slug_id_by_default(self):
        from auto_dm.phb import get_monster

        m = get_monster("Goblin")
        assert m is not None
        npc = monster_to_npc(m)
        assert npc.id == "goblin"
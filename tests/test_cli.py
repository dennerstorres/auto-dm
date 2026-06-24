"""Integration tests for the CLI: REPL, meta-commands, rendering.

These tests exercise :class:`auto_dm.cli.app.GameApp` end-to-end
with a fake LLM provider. They do not touch the real network.

What's covered:
- Meta-commands (``/help``, ``/save``, ``/list``, ``/quit``,
  ``/status``, ``/load``, unknown).
- The normal-play path: input → narrative loop → result.
- Auto-save cadence.
- Save / load roundtrip through GameApp.
- Rich rendering helpers (HP bar, narration, combat status).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


from auto_dm.cli import (
    GameApp,
    render_action_result,
    render_combat_status,
    render_narration,
)
from auto_dm.cli.app import make_game_app
from auto_dm.llm.base import LLMConfig, Message
from auto_dm.persistence import (
    save_exists,
    save_state,
)
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    ActionResult,
    AbilityScores,
    Character,
    GameState,
    NPC,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeProvider:
    """Minimal LLM provider that scripts DM responses."""

    def __init__(self, scripted: list[str] | None = None) -> None:
        self.scripted = list(scripted or [])
        self.calls: list[list[Message]] = []
        self.config = LLMConfig(name="fake", api_key="test", model="fake")
        self.name = "fake"

    def chat(self, messages: list[Message]) -> str:
        self.calls.append(messages)
        if not self.scripted:
            return ""
        if len(self.scripted) == 1:
            return self.scripted[0]
        return self.scripted.pop(0)

    def stream(self, messages):
        yield self.chat(messages)

    def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content) for m in messages)


# A DM response with narration + a small free-form action block.
def dm_response(narration: str, action_json: Optional[str] = None) -> str:
    if action_json is None:
        return narration
    body = (
        f"{narration}\n\n"
        f"```action\n"
        f"{action_json}\n"
        f"```\n"
    )
    return body


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
        hp_current=10,
        hp_max=10,
        armor_class=16,
        speed=30,
        proficiency_bonus=2,
        hit_dice="1d10",
        hit_dice_remaining=1,
    )


def _make_npc(name: str = "Goblin", npc_id: str = "g1") -> NPC:
    return NPC(
        id=npc_id,
        name=name,
        hp_current=5,
        hp_max=7,
        armor_class=12,
        speed=30,
        abilities=AbilityScores(
            strength=8, dexterity=14, constitution=10,
            intelligence=10, wisdom=8, charisma=8,
        ),
    )


def _make_state() -> GameState:
    return GameState(
        campaign_name="Test Campaign",
        started_at=datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc),
        current_location="Forest",
        party=[_make_player()],
        npcs=[_make_npc()],
        player_character_id="p1",
    )


def _make_app(
    *,
    tmp_path: Path,
    scripted: Optional[list[str]] = None,
    auto_save_every_n_turns: int = 5,
) -> GameApp:
    sm = StateManager(_make_state())
    return GameApp(
        state_manager=sm,
        provider_factory=lambda: FakeProvider(scripted),
        saves_dir=tmp_path / "saves",
        auto_save_every_n_turns=auto_save_every_n_turns,
    )


# ---------------------------------------------------------------------------
# Meta-commands
# ---------------------------------------------------------------------------


class TestMetaCommands:
    def test_help_lists_commands(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path)
        assert app.process_input("/help") is None
        out = capsys.readouterr().out
        assert "/save" in out
        assert "/load" in out
        assert "/quit" in out

    def test_unknown_command_does_not_quit(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path)
        assert app.process_input("/bogus") is None
        assert not app.should_quit
        out = capsys.readouterr().out
        assert "Comando desconhecido" in out

    def test_quit_sets_flag(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path)
        assert app.process_input("/quit") is None
        assert app.should_quit

    def test_empty_input_returns_none(self, tmp_path):
        app = _make_app(tmp_path=tmp_path)
        assert app.process_input("") is None
        assert app.process_input("   ") is None


# ---------------------------------------------------------------------------
# Save / load through the REPL
# ---------------------------------------------------------------------------


class TestSaveLoadViaRepl:
    def test_save_creates_directory_and_file(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path)
        app.initialize()
        app.process_input("/save")
        assert save_exists(
            "test-campaign", saves_dir=tmp_path / "saves",
        )

    def test_save_with_explicit_slug(self, tmp_path):
        app = _make_app(tmp_path=tmp_path)
        app.initialize()
        app.process_input("/save my-run")
        assert save_exists("my-run", saves_dir=tmp_path / "saves")

    def test_list_empty(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path)
        app.process_input("/list")
        out = capsys.readouterr().out
        assert "nenhum save" in out

    def test_list_after_save(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path)
        app.initialize()
        app.process_input("/save")
        app.process_input("/list")
        out = capsys.readouterr().out
        # Either the table is printed (Rich) or a fallback message
        assert "Test Campaign" in out or "Saves" in out

    def test_load_replaces_state(self, tmp_path):
        # 1) Save a known state.
        sm = StateManager(_make_state())
        sm.state.current_location = "Dungeon"
        save_state(sm.state, slug="first", saves_dir=tmp_path / "saves")

        # 2) Spin up an app with a *different* state and load.
        app = _make_app(tmp_path=tmp_path)
        app.initialize()
        assert app.state_manager.state.current_location == "Forest"
        app.process_input("/load first")
        assert app.state_manager.state.current_location == "Dungeon"

    def test_load_missing_save_message(self, tmp_path, capsys):
        app = _make_app(tmp_path=tmp_path)
        app.initialize()
        app.process_input("/load nope")
        out = capsys.readouterr().out
        assert "não encontrado" in out

    def test_load_schema_mismatch(self, tmp_path, capsys):
        # Save with normal schema then tamper with version.
        sm = StateManager(_make_state())
        save_state(sm.state, slug="bad", saves_dir=tmp_path / "saves")
        import json
        path = tmp_path / "saves" / "bad" / "state.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_meta"]["schema_version"] = 99
        path.write_text(json.dumps(data), encoding="utf-8")

        app = _make_app(tmp_path=tmp_path)
        app.initialize()
        app.process_input("/load bad")
        out = capsys.readouterr().out
        assert "incompat" in out


# ---------------------------------------------------------------------------
# Normal play
# ---------------------------------------------------------------------------


class TestNormalPlay:
    def test_process_input_returns_narrative_result(self, tmp_path):
        narration = "Você entra na taverna."
        action = '{"action_type": "say", "actor_id": "p1"}'
        scripted = [dm_response(narration, action)]
        app = _make_app(tmp_path=tmp_path, scripted=scripted)
        app.initialize()
        result = app.process_input("Olho ao redor da taverna")
        assert result is not None
        assert result.narration == narration
        assert result.action is not None
        assert result.action.action_type.value == "say"

    def test_narrative_result_appends_to_log(self, tmp_path):
        narration = "Você entra na taverna."
        action = '{"action_type": "say", "actor_id": "p1"}'
        scripted = [dm_response(narration, action)]
        app = _make_app(tmp_path=tmp_path, scripted=scripted)
        app.initialize()
        before = len(app.state_manager.state.narrative_log)
        app.process_input("Olho ao redor")
        # DM narration + (possibly) follow-up appended
        assert len(app.state_manager.state.narrative_log) > before

    def test_action_result_in_result(self, tmp_path):
        # An attack action — narrative + result.
        narration = "Você avança com a espada!"
        action_json = (
            '{"action_type": "attack", "actor_id": "p1", '
            '"target_id": "g1", "weapon": "Longsword"}'
        )
        scripted = [dm_response(narration, action_json)]
        app = _make_app(tmp_path=tmp_path, scripted=scripted)
        # Pre-set initiative so the attack is legal
        app.state_manager.state.in_combat = True
        app.state_manager.state.initiative_order = ["p1", "g1"]
        app.state_manager.state.current_turn_index = 0
        app.initialize()
        result = app.process_input("Ataco o goblin")
        assert result is not None
        assert result.action is not None
        # Either the attack succeeded or was rejected; either way we
        # got back a structured result.
        assert result.action_result is not None

    def test_turn_counter_increments(self, tmp_path):
        narration = "Você entra na taverna."
        action = '{"action_type": "say", "actor_id": "p1"}'
        scripted = [dm_response(narration, action)] * 3
        app = _make_app(tmp_path=tmp_path, scripted=scripted)
        app.initialize()
        assert app._turn_counter == 0
        app.process_input("look")
        assert app._turn_counter == 1
        app.process_input("look")
        assert app._turn_counter == 2


# ---------------------------------------------------------------------------
# Auto-save
# ---------------------------------------------------------------------------


class TestAutoSave:
    def test_auto_save_at_cadence(self, tmp_path):
        narration = "ok"
        action = '{"action_type": "say", "actor_id": "p1"}'
        scripted = [dm_response(narration, action)] * 10
        app = _make_app(
            tmp_path=tmp_path,
            scripted=scripted,
            auto_save_every_n_turns=2,
        )
        app.initialize()
        app.process_input("x")  # turn 1
        assert not save_exists(
            "test-campaign", saves_dir=tmp_path / "saves",
        )
        app.process_input("x")  # turn 2 → auto-save
        assert save_exists(
            "test-campaign", saves_dir=tmp_path / "saves",
        )

    def test_auto_save_disabled(self, tmp_path):
        narration = "ok"
        action = '{"action_type": "say", "actor_id": "p1"}'
        scripted = [dm_response(narration, action)] * 10
        app = _make_app(
            tmp_path=tmp_path,
            scripted=scripted,
            auto_save_every_n_turns=0,
        )
        app.initialize()
        for _ in range(5):
            app.process_input("x")
        assert not save_exists(
            "test-campaign", saves_dir=tmp_path / "saves",
        )


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


class TestRendering:
    def test_render_narration_returns_panel(self):
        from rich.panel import Panel
        p = render_narration("DM", "Hello world")
        assert isinstance(p, Panel)

    def test_render_narration_role_color(self):
        from rich.panel import Panel
        for role in ("dm", "player", "companion", "system"):
            p = render_narration("X", "...", role=role)
            assert isinstance(p, Panel)

    def test_render_combat_status_out_of_combat(self, tmp_path):
        from rich.table import Table
        app = _make_app(tmp_path=tmp_path)
        app.initialize()
        t = render_combat_status(app.state_manager)
        assert isinstance(t, Table)
        assert t.row_count >= 1  # player + NPC

    def test_render_combat_status_in_combat_with_marker(self, tmp_path):
        app = _make_app(tmp_path=tmp_path)
        app.initialize()
        app.state_manager.state.in_combat = True
        app.state_manager.state.initiative_order = ["p1", "g1"]
        app.state_manager.state.current_turn_index = 0
        t = render_combat_status(app.state_manager)
        # The first row should have the "▶" marker
        from rich.console import Console
        import io
        buf = io.StringIO()
        Console(file=buf, force_terminal=False, width=200).print(t)
        rendered = buf.getvalue()
        assert "▶" in rendered

    def test_render_action_result_success(self):
        from rich.panel import Panel
        r = ActionResult(
            success=True,
            message="Hit!",
            mechanical={"is_hit": True},
        )
        p = render_action_result(r)
        assert isinstance(p, Panel)

    def test_render_action_result_failure(self):
        from rich.panel import Panel
        r = ActionResult(success=False, message="Nope", mechanical={})
        p = render_action_result(r)
        assert isinstance(p, Panel)


# ---------------------------------------------------------------------------
# make_game_app factory
# ---------------------------------------------------------------------------


class TestMakeGameApp:
    def test_initializes_dm_agent(self, tmp_path):
        scripted = []
        app = make_game_app(
            state=_make_state(),
            provider_factory=lambda: FakeProvider(scripted),
            saves_dir=tmp_path / "saves",
        )
        assert app._dm_agent is not None
        assert len(app.state_manager.state.party) == 1

    def test_attaches_extra_companions(self, tmp_path):
        scripted = []
        app = make_game_app(
            state=_make_state(),
            provider_factory=lambda: FakeProvider(scripted),
            saves_dir=tmp_path / "saves",
            extra_companions=["thorgrim"],
        )
        names = {c.name for c in app.state_manager.state.party}
        assert "Aragorn" in names
        assert "Thorgrim" in names

    def test_dm_agent_has_no_companion_for_player(self, tmp_path):
        scripted = []
        app = make_game_app(
            state=_make_state(),
            provider_factory=lambda: FakeProvider(scripted),
            saves_dir=tmp_path / "saves",
        )
        assert "p1" not in app._companion_agents

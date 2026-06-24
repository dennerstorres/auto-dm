"""Text-mode CLI for Auto DM.

The CLI is intentionally thin. It:

1. Loads config (provider, model, save dir, etc.).
2. Builds the LLM provider.
3. Either starts a new game (character creation + opening narration)
   or loads a saved game.
4. Runs the REPL: read player input → call
   :func:`auto_dm.agents.process_player_action` → print narration.
5. Handles meta-commands: ``/save <slug>``, ``/load <slug>``,
   ``/list``, ``/quit``.
6. Auto-saves every N turns (from config).

The :class:`GameApp` is the testable core. The Click wrapper in
``main.py`` is a thin shim that builds an app and runs it.
"""
from auto_dm.cli.app import GameApp, make_game_app
from auto_dm.cli.character_flow import create_character_interactive
from auto_dm.cli.rendering import (
    render_action_result,
    render_combat_status,
    render_narration,
    render_save_list,
)
from auto_dm.cli.setup import setup_new_game

__all__ = [
    "GameApp",
    "create_character_interactive",
    "make_game_app",
    "render_action_result",
    "render_combat_status",
    "render_narration",
    "render_save_list",
    "setup_new_game",
]

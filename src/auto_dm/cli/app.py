"""GameApp: the testable core of the CLI.

The :class:`GameApp` encapsulates the game session: a state manager,
a DM agent, an optional combat engine, and a save directory. It
exposes a small set of methods used by both the REPL driver and the
Click wrapper.

The :meth:`process_input` method takes a line of player input, runs
it through the narrative loop, and returns a structured result. The
caller is responsible for rendering and for driving the REPL. This
separation makes the loop easy to test with scripted input.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from auto_dm.agents import DMAgent, NarrativeResult, process_player_action
from auto_dm.agents.companion import CompanionAgent
from auto_dm.companions import (
    COMPANION_FACTORIES,
    build_companion,
    list_companion_keys,
    make_lyra,
    make_mira,
    make_thorgrim,
    make_vex,
)
from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.persistence import (
    SaveNotFoundError,
    SchemaMismatchError,
    list_saves,
    load_state,
    save_state,
)
from auto_dm.state.manager import StateManager
from auto_dm.state.models import GameState


logger = logging.getLogger(__name__)


# Meta-commands available in the REPL. Documented in the help string
# returned by ``help_text()``.
META_COMMANDS = {
    "/help": "Show available commands",
    "/save [slug]": "Save the current game (uses campaign name as slot)",
    "/load <slug>": "Load a saved game (replaces current session)",
    "/list": "List available saves",
    "/status": "Show party + combat status",
    "/quit": "Exit the game",
}


@dataclass
class GameApp:
    """A running game session.

    Parameters:
        state_manager: The game state.
        provider_factory: Callable returning a fresh LLM provider.
            Called once per agent that needs one (DM + companions).
        saves_dir: Where saves live. Defaults to ``./saves``.
        auto_save_every_n_turns: Auto-save cadence; 0 disables.
        combat_engine: Injected for tests. Defaults to a fresh
            :class:`CombatEngine` when the first combat starts.
        extra_companions: Pre-picked companion keys (from the roster)
            to add to the party at game start. The player's character
            must already be in ``state.party[0]``.
    """

    state_manager: StateManager
    provider_factory: Callable
    saves_dir: Path = field(default_factory=lambda: Path("saves"))
    auto_save_every_n_turns: int = 5
    combat_engine: Optional[CombatEngine] = None
    extra_companions: list[str] = field(default_factory=list)
    _dm_agent: Optional[DMAgent] = None
    _companion_agents: dict[str, CompanionAgent] = field(default_factory=dict)
    _turn_counter: int = 0
    _should_quit: bool = False

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Build DM + companion agents, attach companions to the party."""
        # Add pre-picked companions
        for key in self.extra_companions:
            companion = self._build_companion(key)
            self.state_manager.state.party.append(companion)
        # Build agents
        self._dm_agent = DMAgent(
            provider=self.provider_factory(),
            state_manager=self.state_manager,
        )
        for c in self.state_manager.state.party:
            if c.is_player:
                continue
            self._companion_agents[c.id] = CompanionAgent(
                provider=self.provider_factory(),
                character=c,
                state_manager=self.state_manager,
            )

    @staticmethod
    def _build_companion(key: str):
        if key not in COMPANION_FACTORIES:
            raise KeyError(
                f"Unknown companion {key!r}. "
                f"Available: {list(COMPANION_FACTORIES)}"
            )
        return COMPANION_FACTORIES[key]()

    # ------------------------------------------------------------------
    # REPL
    # ------------------------------------------------------------------

    def help_text(self) -> str:
        lines = ["Comandos disponíveis:"]
        for cmd, desc in META_COMMANDS.items():
            lines.append(f"  {cmd:18s} {desc}")
        return "\n".join(lines)

    @property
    def should_quit(self) -> bool:
        return self._should_quit

    def process_input(self, line: str) -> Optional[NarrativeResult]:
        """Process one line of player input.

        Returns a :class:`NarrativeResult` for normal play, or ``None``
        for meta-commands (which mutate state via side effects).
        """
        stripped = line.strip()
        if not stripped:
            return None

        # Meta-commands
        if stripped.startswith("/"):
            return self._handle_meta(stripped)

        # Normal play
        if self._dm_agent is None:
            raise RuntimeError("GameApp not initialized; call initialize() first")

        self._turn_counter += 1
        result = process_player_action(
            self.state_manager,
            stripped,
            self._dm_agent,
            combat_engine=self.combat_engine,
        )

        # Auto-save cadence
        if (
            self.auto_save_every_n_turns > 0
            and self._turn_counter % self.auto_save_every_n_turns == 0
        ):
            self._autosave()

        return result

    def _handle_meta(self, line: str) -> Optional[NarrativeResult]:
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        if cmd == "/help":
            print(self.help_text())
        elif cmd == "/save":
            self._autosave(slug=arg.strip() or None)
            print("[save] Jogo salvo.")
        elif cmd == "/load":
            self._load(arg.strip())
        elif cmd == "/list":
            self._list_saves()
        elif cmd == "/status":
            from auto_dm.cli.rendering import render_combat_status
            from rich.console import Console

            Console().print(render_combat_status(self.state_manager))
        elif cmd == "/quit":
            self._should_quit = True
            print("Até a próxima aventura!")
        else:
            print(f"[Comando desconhecido: {cmd}. Digite /help para ajuda.]")
        return None

    # ------------------------------------------------------------------
    # Save / load helpers
    # ------------------------------------------------------------------

    def _autosave(self, *, slug: Optional[str] = None) -> Path:
        self.saves_dir.mkdir(parents=True, exist_ok=True)
        path = save_state(
            self.state_manager.state,
            slug=slug,
            saves_dir=self.saves_dir,
        )
        logger.info("Auto-saved to %s", path)
        return path

    def _load(self, slug: str) -> None:
        if not slug:
            print("Uso: /load <slug>")
            return
        try:
            state = load_state(slug, saves_dir=self.saves_dir)
        except SaveNotFoundError:
            print(f"[Save {slug!r} não encontrado. Use /list.]")
            return
        except SchemaMismatchError as exc:
            print(f"[Save incompatível: {exc}]")
            return
        self.state_manager.state = state
        # Rebuild agents so they reference the new state
        self._dm_agent = DMAgent(
            provider=self.provider_factory(),
            state_manager=self.state_manager,
        )
        self._companion_agents = {
            c.id: CompanionAgent(
                provider=self.provider_factory(),
                character=c,
                state_manager=self.state_manager,
            )
            for c in state.party
            if not c.is_player
        }
        print(f"[Save {slug!r} carregado.]")

    def _list_saves(self) -> None:
        from auto_dm.cli.rendering import render_save_list
        from rich.console import Console

        saves = list_saves(saves_dir=self.saves_dir)
        if not saves:
            print("(nenhum save encontrado)")
            return
        Console().print(render_save_list(saves))


# ---------------------------------------------------------------------------
# Factory: build a GameApp from config + optional existing save
# ---------------------------------------------------------------------------


def make_game_app(
    *,
    state: GameState,
    provider_factory: Callable,
    saves_dir: Path = Path("saves"),
    auto_save_every_n_turns: int = 5,
    extra_companions: Optional[list[str]] = None,
) -> GameApp:
    """Build and initialize a :class:`GameApp` from a fresh :class:`GameState`."""
    app = GameApp(
        state_manager=StateManager(state),
        provider_factory=provider_factory,
        saves_dir=saves_dir,
        auto_save_every_n_turns=auto_save_every_n_turns,
        extra_companions=list(extra_companions or []),
    )
    app.initialize()
    return app


# Re-export so callers don't have to import the companions module separately
make_thorgrim  # silence linter
make_lyra
make_mira
make_vex
build_companion
list_companion_keys

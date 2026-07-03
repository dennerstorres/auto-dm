"""GameApp: the testable core of the CLI.

The :class:`GameApp` encapsulates the game session: a state manager,
a DM agent, an optional combat engine, and a save directory. It
exposes a small set of methods used by both the REPL driver and the
Click wrapper.

The :meth:`process_input` method takes a line of player input, runs
it through the narrative loop, and returns a structured result. The
caller is responsible for rendering and for driving the REPL. This
separation makes the loop easy to test with scripted input.

Phase 25h changes:
  * ``process_input`` now also runs companion turns during combat,
    so the player isn't the only one acting. The companion cycle
    stops when initiative wraps back to the player or combat ends.
  * New meta-commands: ``/encounter``, ``/look``, ``/inventory``,
    ``/conditions``, ``/spells``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from auto_dm.agents import DMAgent, NarrativeResult, generate_opening, process_player_action
from auto_dm.agents.summarizer import NarrativeSummarizer, summarize_once
from auto_dm.agents.companion import CompanionAgent
from auto_dm.agents.companion_turn import CompanionTurnResult, run_companion_turn
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
from auto_dm.phb import get_monster
from auto_dm.state.manager import StateManager
from auto_dm.state.models import Character, GameState, NPC
from auto_dm.state.monster_adapter import monster_to_npc, slugify_monster_id


logger = logging.getLogger(__name__)


# Meta-commands available in the REPL. Documented in the help string
# returned by ``help_text()``. Phase 25h expands the set with encounter
# spawning, look, inventory/conditions/spells inspection.
META_COMMANDS = {
    "/help": "Show available commands",
    "/save [slug]": "Save the current game (uses campaign name as slot)",
    "/load <slug>": "Load a saved game (replaces current session)",
    "/list": "List available saves",
    "/status": "Show party + combat status",
    "/encounter <mon1>, <mon2>, ...": "Spawn monsters (PHB) and start combat",
    "/look": "Describe current location and visible creatures",
    "/inventory [name]": "Show party member inventory (default: player)",
    "/conditions [name]": "Show party member conditions (default: player)",
    "/level-up [name]": "Advance a character one level (HP rolled automatically; use /asi after)",
    "/spells [name]": "Show party member spellbook (default: player)",
    "/summary": "Show summary status (default action when no subcommand)",
    "/summary on": "Enable periodic summarization",
    "/summary off": "Disable periodic summarization",
    "/summary status": "Show summary config + cursor state",
    "/summary force": "Summarize now and autosave",
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
    _summarizer: Optional[NarrativeSummarizer] = None
    _companion_agents: dict[str, CompanionAgent] = field(default_factory=dict)
    _turn_counter: int = 0
    _should_quit: bool = False

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Build DM + companion agents, attach companions to the party.

        Phase 33: also build a ``NarrativeSummarizer`` using the DM's
        provider. The summarizer is the same model class for player
        turns and companion turns, so it inherits the same provider
        configuration; both paths pass the same instance through.
        """
        # Add pre-picked companions
        for key in self.extra_companions:
            companion = self._build_companion(key)
            self.state_manager.state.party.append(companion)
        # Build agents
        self._dm_agent = DMAgent(
            provider=self.provider_factory(),
            state_manager=self.state_manager,
        )
        # Phase 33 — periodic summarizer (shares the DM's provider).
        self._summarizer = NarrativeSummarizer(
            provider=self.provider_factory(),
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

        Phase 25h: when the player acts in combat, the companion cycle
        runs after the player's turn until initiative wraps back to the
        player (or combat ends). Companion turn results are stashed in
        ``result.companion_results`` so the caller can render them.
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
            summarizer=self._summarizer,
        )

        # Companion cycle: only when we're still in combat and have a
        # combat engine. Stops at the player's next turn (or end of combat).
        if (
            self.combat_engine is not None
            and self.state_manager.state.in_combat
        ):
            result.companion_results = self._run_companion_cycle()

        # Auto-save cadence
        if (
            self.auto_save_every_n_turns > 0
            and self._turn_counter % self.auto_save_every_n_turns == 0
        ):
            self._autosave()

        return result

    def generate_opening(self) -> NarrativeResult:
        """Generate the campaign opening narration.

        Called once before the REPL accepts its first player input, so
        the very first thing the player sees is the DM establishing the
        scene — the player doesn't have to send anything to learn where
        they are. The DM also chooses the starting location and records
        it via a ``move`` action, which :func:`generate_opening` applies
        to ``state.current_location``.

        Idempotent: if the narrative log already has an entry, the
        opening was already generated (e.g. a loaded save) and this is
        a no-op returning an empty :class:`NarrativeResult`.
        """
        if self._dm_agent is None:
            raise RuntimeError("GameApp not initialized; call initialize() first")
        # Loaded games already have narration — don't regenerate.
        if self.state_manager.state.narrative_log:
            return NarrativeResult(narration="")
        result = generate_opening(self.state_manager, self._dm_agent)
        # Persist the opening so a reload shows it instead of regenerating.
        if self.auto_save_every_n_turns > 0:
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
        elif cmd == "/encounter":
            self._do_encounter(arg)
        elif cmd == "/look":
            self._do_look()
        elif cmd == "/inventory":
            self._do_inventory(arg.strip())
        elif cmd == "/conditions":
            self._do_conditions(arg.strip())
        elif cmd == "/spells":
            self._do_spells(arg.strip())
        elif cmd == "/level-up":
            self._do_level_up(arg.strip())
        elif cmd == "/summary":
            self._do_summary(arg.strip())
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
        # Phase 33 — rebuild the summarizer too (uses the new provider).
        self._summarizer = NarrativeSummarizer(
            provider=self.provider_factory(),
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

    # ------------------------------------------------------------------
    # Companion cycle (Phase 25h — the critical fix)
    # ------------------------------------------------------------------

    def _run_companion_cycle(self) -> list[CompanionTurnResult]:
        """Run companion turns in initiative order until the player's
        next turn (or end of combat).

        The flow:
            1. Advance past the player's turn (the player just acted).
            2. Loop: if it's a companion's turn, dispatch via the LLM
               agent and the combat engine. Skip NPC turns (enemies are
               narrated by the DM, not resolved mechanically here).
            3. Stop when initiative wraps back to the player or combat
               ends (one side wiped out).

        Returns the list of companion turn results (possibly empty).
        Bounded by ``2 * len(initiative_order)`` iterations as a safety
        net against pathological state (e.g. invalid ids in the order).
        """
        state = self.state_manager.state
        if not state.in_combat or not state.initiative_order:
            return []

        # Advance past the player's turn.
        if self.combat_engine is not None:
            self.combat_engine.next_turn(self.state_manager)

        results: list[CompanionTurnResult] = []
        order = list(state.initiative_order)
        max_iterations = max(1, len(order) * 2)
        player_id = state.player_character_id

        for _ in range(max_iterations):
            if not self.state_manager.state.in_combat:
                break
            current_id = self.state_manager.current_actor_id()
            if current_id is None:
                break
            if current_id == player_id:
                # Initiative wrapped back to the player — stop.
                break

            # It's either a companion or an NPC. We only run companions.
            current_char = self.state_manager.get_character(current_id)
            agent = self._companion_agents.get(current_id)
            if current_char is not None and agent is not None:
                enemies = self._enemy_ids()
                allies = self._ally_ids(exclude=current_id)
                turn = run_companion_turn(
                    self.state_manager,
                    self.combat_engine,
                    agent,
                    enemies=enemies,
                    allies=allies,
                    summarizer=self._summarizer,
                )
                results.append(turn)
                if not self.state_manager.state.in_combat:
                    break
            # Advance regardless (NPC turns are skipped but still count).
            if self.combat_engine is not None:
                self.combat_engine.next_turn(self.state_manager)

        return results

    def _enemy_ids(self) -> list[str]:
        """IDs of currently-alive NPCs (enemies by default)."""
        return [n.id for n in self.state_manager.state.npcs if n.hp_current > 0]

    def _ally_ids(self, *, exclude: Optional[str] = None) -> list[str]:
        """IDs of currently-alive party members, optionally excluding one."""
        return [
            c.id for c in self.state_manager.state.party
            if c.hp_current > 0 and (exclude is None or c.id != exclude)
        ]

    # ------------------------------------------------------------------
    # Meta-command handlers (Phase 25h)
    # ------------------------------------------------------------------

    def _find_party_member(self, name: str) -> Optional[Character]:
        """Find a party member by name (case-insensitive, partial)."""
        if not name:
            return next(
                (c for c in self.state_manager.state.party if c.is_player),
                None,
            )
        needle = name.lower()
        for c in self.state_manager.state.party:
            if c.name.lower() == needle:
                return c
        for c in self.state_manager.state.party:
            if needle in c.name.lower():
                return c
        return None

    def _do_encounter(self, arg: str) -> None:
        """``/encounter <name1>, <name2>, ...`` — spawn monsters and start combat."""
        if not arg.strip():
            print("Uso: /encounter <monstro1>, <monstro2>, ...")
            print("Exemplo: /encounter Goblin, Goblin, Orc")
            return
        if self.combat_engine is None:
            print("[Sem combat engine — não é possível iniciar encontro.]")
            return
        if self.state_manager.state.in_combat:
            print("[Já estamos em combate. Encerre-o antes de iniciar outro encontro.]")
            return

        names = [n.strip() for n in arg.split(",") if n.strip()]
        spawned: list[Character | NPC] = []
        for idx, name in enumerate(names, start=1):
            monster = get_monster(name)
            if monster is None:
                print(f"[Monstro '{name}' não encontrado no PHB.]")
                continue
            npc_id = f"{slugify_monster_id(monster.name)}_{idx}"
            npc = monster_to_npc(monster, npc_id=npc_id)
            spawned.append(npc)

        if not spawned:
            print("[Nenhum monstro válido. Encontro cancelado.]")
            return

        self.state_manager.state.npcs.extend(spawned)
        order = self.combat_engine.start_combat(self.state_manager)
        names_summary = ", ".join(n.name for n in spawned)
        print(
            f"[Encontro com {len(spawned)} criatura(s): {names_summary}. "
            f"Combate iniciado! Ordem: {', '.join(order)}]"
        )

    def _do_look(self) -> None:
        """``/look`` — describe current location and visible creatures."""
        state = self.state_manager.state
        print(f"\n[Local atual: {state.current_location}]")
        if state.party:
            print("  Party:")
            for c in state.party:
                marker = "▶" if c.id == state.player_character_id else " "
                print(
                    f"    {marker} {c.name} ({c.race} {c.class_} L{c.level}) — "
                    f"HP {c.hp_current}/{c.hp_max}, AC {c.armor_class}"
                )
        if state.npcs:
            print("  NPCs no local:")
            for n in state.npcs:
                print(
                    f"    - {n.name} (CR {n.challenge_rating or '?'}) — "
                    f"HP {n.hp_current}/{n.hp_max}, AC {n.armor_class}"
                )
        if not state.npcs:
            print("  (nenhum NPC presente)")

    def _do_inventory(self, name: str) -> None:
        """``/inventory [name]`` — show a party member's inventory."""
        member = self._find_party_member(name)
        if member is None:
            print(f"[Personagem '{name}' não encontrado na party.]")
            return
        from auto_dm.cli.rendering import render_inventory
        from rich.console import Console

        Console().print(render_inventory(member))

    def _do_conditions(self, name: str) -> None:
        """``/conditions [name]`` — show a party member's status."""
        member = self._find_party_member(name)
        if member is None:
            print(f"[Personagem '{name}' não encontrado na party.]")
            return
        from auto_dm.cli.rendering import render_conditions
        from rich.console import Console

        Console().print(render_conditions(member))

    def _do_spells(self, name: str) -> None:
        """``/spells [name]`` — show a party member's spellbook."""
        member = self._find_party_member(name)
        if member is None:
            print(f"[Personagem '{name}' não encontrado na party.]")
            return
        from auto_dm.cli.rendering import render_spellbook
        from rich.console import Console

        Console().print(render_spellbook(member))

    def _do_level_up(self, name: str) -> None:
        """``/level-up [name]`` — advance a party member one level.

        Default target is the player character. Rolls hit dice, applies
        proficiency-bonus and extra-attack updates, and reports which
        (sub)class features became active.
        """
        member = self._find_party_member(name)
        if member is None:
            print(f"[Personagem '{name}' não encontrado na party.]")
            return
        if member.level >= 20:
            print(f"[{member.name} já está no nível máximo (20).]")
            return
        from auto_dm.engine.progression import level_up, is_asi_level
        from auto_dm.character.level_up import (
            apply_class_features,
            features_gained_at_class_level,
        )

        result = level_up(member)
        new_features = apply_class_features(member, at_level=member.level)
        # Also list any subclass features gained at this level.
        sub_features = []
        if member.subclass:
            from auto_dm.phb import get_subclass as _gs
            sub = _gs(member.class_, member.subclass)
            if sub is not None:
                sub_features = [
                    f.name for f in sub.features
                    if f.level == member.level
                ]

        print(
            f"[level-up] {member.name} subiu de {result.old_level} -> "
            f"{result.new_level}. HP +{result.hp_gained} (max {result.new_max_hp}). "
            f"Proficiência +{result.new_proficiency_bonus}."
        )
        if result.new_extra_attacks > 0:
            print(
                f"  Extra Attack: {result.new_extra_attacks} ataques extras "
                f"({1 + result.new_extra_attacks} totais por Attack)."
            )
        if result.asi_pending:
            print(f"  ASI disponível! Use /asi para melhorar uma habilidade.")
        if new_features:
            print(f"  Features de classe: {', '.join(new_features)}.")
        if sub_features:
            print(f"  Features de subclasse: {', '.join(sub_features)}.")

    def _do_summary(self, arg: str) -> None:
        """``/summary [on|off|status|force]`` — inspect/toggle the summarizer.

        Subcommands:
        - ``/summary`` (no arg) or ``/summary status`` — print config
        - ``/summary on|off`` — toggle ``state.summary_enabled``
        - ``/summary force`` — run summarizer NOW and autosave
        """
        state = self.state_manager.state
        cmd = arg.strip().lower()
        if cmd in ("", "status"):
            entries = len(state.narrative_log)
            summaries = len(state.summary_history)
            print("[summary] status")
            print(f"  enabled: {state.summary_enabled}")
            print(f"  every_n_entries: {state.summary_every_n_entries}")
            print(f"  char_threshold: {state.summary_char_threshold}")
            print(
                f"  narrative_log: {entries} entries, "
                f"{sum(len(e.content) for e in state.narrative_log)} chars"
            )
            print(f"  summary_history: {summaries} entries")
            print(
                f"  cursors: last_summarized_at={state.last_summarized_at_index}, "
                f"last_attempt_at={state.last_summary_attempt_at_index}"
            )
            return
        if cmd == "on":
            state.summary_enabled = True
            print("[summary] Ativado.")
            return
        if cmd == "off":
            state.summary_enabled = False
            print("[summary] Desativado.")
            return
        if cmd == "force":
            if self._summarizer is None:
                print("[summary] Sem summarizer configurado.")
                return
            # Force a summary: temporarily set last_summarized_at_index
            # BACK so should_summarize fires regardless of cooldown.
            # We restore it after — but if the LLM fires successfully,
            # it'll advance the cursor naturally.
            before_cursor = state.last_summarized_at_index
            state.last_summarized_at_index = 0
            usage = summarize_once(self.state_manager, self._summarizer)
            if usage is None:
                # Nothing to summarize (log too short) — restore cursor.
                state.last_summarized_at_index = before_cursor
                print(
                    "[summary] Nada para resumir (narrative_log muito curto)."
                )
                return
            new = len(state.summary_history)
            self._autosave()
            print(
                f"[summary] Resumo adicionado. summary_history agora tem {new} "
                f"entrada(s). Autosave feito."
            )
            return
        print(f"[summary] Subcomando desconhecido: {cmd!r}. Use on|off|status|force.")


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

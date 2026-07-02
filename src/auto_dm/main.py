"""CLI entry point for Auto DM.

Subcommands:

- ``auto-dm`` (default): start a new game (character creation +
  REPL).
- ``--list-saves``: list saved games and exit.
- ``--load <slug>``: load a save and resume.
- ``--delete <slug>``: delete a save (with confirmation).

The smoke-test LLM ping from Phase 1 has been replaced by the real
game loop in :mod:`auto_dm.cli`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from auto_dm import __version__
from auto_dm.cli import (
    GameApp,
    make_game_app,
    render_narration,
    render_save_list,
    setup_new_game,
)
from auto_dm.config import load_app_config
from auto_dm.llm.factory import get_provider
from auto_dm.persistence import (
    SaveNotFoundError,
    SchemaMismatchError,
    delete_save,
    list_saves as list_save_files,
    load_state,
    save_exists,
)


console = Console()


@click.command()
@click.option(
    "--config",
    "config_path",
    default="config.json",
    type=click.Path(),
    help="Path to config.json (default: config.json in cwd).",
)
@click.option(
    "--model",
    default=None,
    help="Override model name from config.",
)
@click.option(
    "--list-saves",
    is_flag=True,
    help="List available saves and exit.",
)
@click.option(
    "--load",
    "load_slug",
    default=None,
    metavar="SLUG",
    help="Load an existing save instead of starting a new game.",
)
@click.option(
    "--delete",
    "delete_slug",
    default=None,
    metavar="SLUG",
    help="Delete a save and exit (with confirmation).",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompts (for --delete).",
)
@click.version_option(version=__version__, prog_name="auto-dm")
def main(
    config_path: str,
    model: str | None,
    list_saves: bool,
    load_slug: str | None,
    delete_slug: str | None,
    yes: bool,
) -> int:
    """Auto DM - AI-powered D&D 5e solo game master."""
    console.print(
        Panel.fit(
            f"[bold cyan]Auto DM[/bold cyan]  v{__version__}\n"
            "[dim]AI-powered D&D 5e solo game master[/dim]",
            border_style="cyan",
        )
    )

    # ---- Load config ----
    try:
        app = load_app_config(config_path)
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        console.print(f"[red]Config error:[/red] {e}")
        return 1

    if model:
        app.llm.model = model

    # ---- Subcommand: --list-saves ----
    if list_saves:
        saves = list_save_files(saves_dir=app.save_dir)
        if not saves:
            console.print("[dim](nenhum save encontrado)[/dim]")
            return 0
        console.print(render_save_list(saves))
        return 0

    # ---- Subcommand: --delete ----
    if delete_slug:
        if not save_exists(delete_slug, saves_dir=app.save_dir):
            console.print(f"[red]Save '{delete_slug}' não existe.[/red]")
            return 1
        if not yes:
            click.confirm(
                f"Tem certeza que quer deletar o save '{delete_slug}'?",
                abort=True,
            )
        deleted = delete_save(delete_slug, saves_dir=app.save_dir)
        if deleted:
            console.print(f"[green]Save '{delete_slug}' deletado.[/green]")
            return 0
        console.print(f"[red]Falha ao deletar '{delete_slug}'.[/red]")
        return 1

    # ---- Build provider ----
    try:
        provider = get_provider(app.llm)
    except ValueError as e:
        console.print(f"[red]Provider error:[/red] {e}")
        return 1

    console.print(
        f"\n[dim]Provider:[/dim] {provider.name}\n"
        f"[dim]Model:[/dim]    {provider.config.model}\n"
        f"[dim]Idioma:[/dim]  {app.language}\n"
        f"[dim]Saves:[/dim]   {app.save_dir}\n"
    )

    # ---- Subcommand: --load or new game ----
    if load_slug:
        try:
            state = load_state(load_slug, saves_dir=app.save_dir)
        except SaveNotFoundError:
            console.print(f"[red]Save '{load_slug}' não encontrado.[/red]")
            console.print("Use --list-saves para ver os disponíveis.")
            return 1
        except SchemaMismatchError as e:
            console.print(f"[red]Save incompatível:[/red] {e}")
            return 1
        game = make_game_app(
            state=state,
            provider_factory=lambda: provider,
            saves_dir=Path(app.save_dir),
            auto_save_every_n_turns=app.auto_save_every_n_turns,
        )
        console.print(
            f"[green]Save '{load_slug}' carregado.[/green] "
            f"Continuando em '{state.current_location}'."
        )
    else:
        # New game: drive setup flow
        state = setup_new_game()
        game = make_game_app(
            state=state,
            provider_factory=lambda: provider,
            saves_dir=Path(app.save_dir),
            auto_save_every_n_turns=app.auto_save_every_n_turns,
        )
        console.print(
            "\n[dim]Dica: digite /help para ver os comandos. "
            "Suas ações em texto livre narram a aventura.[/dim]\n"
        )

    # ---- Run the REPL ----
    return _run_repl(game)


def _run_repl(game: GameApp) -> int:
    """Drive the read-eval-print loop until the user quits."""
    console.print(f"[bold]{game.state_manager.state.campaign_name}[/bold]\n")
    # Opening narration: generate the first scene automatically so the
    # player knows where they are before having to type anything. The
    # DM also picks the starting location, which is now reflected below.
    with console.status("[dim]O mestre prepara a primeira cena...[/dim]", spinner="dots"):
        opening = game.generate_opening()
    if game.state_manager.state.current_location:
        console.print(f"[dim]Local: {game.state_manager.state.current_location}[/dim]\n")
    if opening.narration:
        _render_narrative(opening)
    while not game.should_quit:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Saindo...[/dim]")
            return 0
        if line.strip().startswith("/"):
            # Meta-commands are instant — no spinner.
            game.process_input(line)
            continue
        with console.status("[dim]Mestre está pensando...[/dim]", spinner="dots"):
            result = game.process_input(line)
        if result is None:
            # Meta-command (already printed feedback)
            continue
        _render_narrative(result)
    return 0


def _render_narrative(result) -> None:
    """Print a :class:`NarrativeResult` via Rich."""
    # Narration from DM
    console.print(render_narration(
        "Mestre", result.narration, role="dm",
    ))
    # Follow-up narration (post-action)
    if result.follow_up_narration:
        console.print(render_narration(
            "Mestre", result.follow_up_narration, role="dm",
        ))
    # Action result panel
    if result.action_result is not None:
        console.print(render_narration(
            "Sistema", result.action_result.message, role="system",
        ))
    # Companion turns (Phase 25h) — rendered after the player's turn.
    for turn in result.companion_results:
        if turn.intent:
            console.print(render_narration(
                turn.actor_name, turn.intent, role="companion",
            ))
        if turn.action_result is not None and turn.action_result.message:
            console.print(render_narration(
                "Sistema", turn.action_result.message, role="system",
            ))


if __name__ == "__main__":
    sys.exit(main())

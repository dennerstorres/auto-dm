"""Game initialization: campaign name + companion selection.

The :func:`setup_new_game` function drives the "start a new campaign"
flow after the player character has been built. It:

1. Asks for a campaign name (used as the default save slug).
2. Asks which pre-defined companions to add (default: all four).
3. Returns a fully-formed :class:`GameState` ready to hand to
   :class:`auto_dm.cli.app.GameApp`.

It mirrors :mod:`auto_dm.cli.character_flow` in being driven by
``input_fn`` and ``print_fn`` so tests can drive it with scripted
input.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from rich import print as rich_print
from rich.panel import Panel

from auto_dm.cli.character_flow import (
    InputFn,
    PrintFn,
    create_character_interactive,
)
from auto_dm.companions import (
    COMPANION_BLURBS,
    COMPANION_FACTORIES,
    roll_party_candidates,
)
from auto_dm.persistence import slugify
from auto_dm.state.models import Character, GameState


def setup_new_game(
    *,
    input_fn: Optional[InputFn] = None,
    print_fn: Optional[PrintFn] = None,
) -> GameState:
    """Drive campaign setup. Returns a fresh :class:`GameState`."""
    inp = input_fn or _default_input
    out = print_fn or rich_print

    out(Panel.fit(
        "[bold cyan]Nova campanha[/bold cyan]\n"
        "[dim]Configure os detalhes iniciais da sua aventura.[/dim]",
        border_style="cyan",
    ))

    campaign_name = _prompt_text(
        inp, out, "Nome da campanha", default="Crônicas da Aliança",
    )

    # Build the player character FIRST so companion selection can use
    # the player's class to roll a synergy-biased set of candidates
    # (Phase 27). Before this, we built the player after companions.
    out("\n[bold]Agora vamos criar seu personagem:[/bold]\n")
    player = create_character_interactive(input_fn=inp, print_fn=out)

    chosen = _prompt_companions(inp, out, player)

    out(Panel.fit(
        f"[bold]Resumo[/bold]\n"
        f"  Campanha: {campaign_name}\n"
        f"  Personagem: {player.name} ({getattr(player, 'class_', '?')})\n"
        f"  Companheiros: {', '.join(chosen) if chosen else '(nenhum)'}",
        border_style="green",
    ))

    party: list[Character] = [player]
    for key in chosen:
        companion = COMPANION_FACTORIES[key]()
        # Stable, unique id (avoid collisions with the player's "p1")
        companion = companion.model_copy(update={"id": f"c_{key}"})
        party.append(companion)

    state = GameState(
        campaign_name=campaign_name,
        started_at=datetime.now(tz=timezone.utc),
        current_location="Taverna da Aliança",
        party=party,
        npcs=[],
        player_character_id=player.id,
    )
    out(Panel.fit(
        f"[bold green]Campanha '{campaign_name}' iniciada![/bold green]\n"
        f"[dim]Personagem: {player.name} · "
        f"Companheiros: {len(chosen)} · "
        f"Slug: {slugify(campaign_name)}[/dim]",
        border_style="green",
    ))
    return state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prompt_text(
    inp: InputFn, out: PrintFn, label: str, *, default: str = "",
) -> str:
    raw = inp(f"{label} [{default}]: ").strip()
    return raw or default


def _prompt_companions(inp: InputFn, out: PrintFn, player: Character) -> list[str]:
    """Roll 4 synergy-biased candidates for ``player`` and let the user pick.

    Phase 27: instead of listing all 12 companions from the roster, we
    roll 4 candidates biased toward roles the player doesn't already
    fill (see ``auto_dm.companions.selection.roll_party_candidates``).
    The user still chooses any subset.
    """
    candidates = roll_party_candidates(player, k=4)
    out("\n[bold]Companheiros sugeridos[/bold] "
        "(escolha um subconjunto; deixe vazio para aceitar todos):")
    for i, key in enumerate(candidates, 1):
        out(f"  {i}) {key}: {COMPANION_BLURBS.get(key, '')}")
    out("  0) Nenhum (sozinho)")
    raw = inp(
        "  Escolha (ex: 1,3 ou 'todos' ou vazio) [todos]: "
    ).strip()
    if raw in ("", "todos", "all"):
        return list(candidates)
    if raw in ("0", "nenhum", "none"):
        return []
    chosen: list[str] = []
    for token in raw.replace(",", " ").split():
        if token.isdigit():
            idx = int(token) - 1
            if 0 <= idx < len(candidates):
                chosen.append(candidates[idx])
    # Preserve input order, dedupe
    seen: set[str] = set()
    out_list: list[str] = []
    for k in chosen:
        if k not in seen:
            seen.add(k)
            out_list.append(k)
    return out_list


def _default_input(prompt: str) -> str:
    from rich.prompt import Prompt
    return Prompt.ask(prompt)

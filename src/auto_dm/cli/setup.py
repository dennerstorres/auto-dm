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

    narration_length = _prompt_narration_length(inp, out)

    initial_scenario = _prompt_initial_scenario(inp, out)

    # Build the player character FIRST so companion selection can use
    # the player's class to roll a synergy-biased set of candidates
    # (Phase 27). Before this, we built the player after companions.
    out("\n[bold]Agora vamos criar seu personagem:[/bold]\n")
    player = create_character_interactive(input_fn=inp, print_fn=out)

    chosen = _prompt_companions(inp, out, player)

    out(Panel.fit(
        f"[bold]Resumo[/bold]\n"
        f"  Campanha: {campaign_name}\n"
        f"  Comprimento da narração: {narration_length}\n"
        f"  Cenário inicial: {_summarize_scenario(initial_scenario)}\n"
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
        # current_location intentionally left empty (default "") — the DM
        # chooses the starting scene during the opening narration.
        narration_length=narration_length,
        initial_scenario=initial_scenario,
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


# Per-campaign narration length. The user picks at campaign creation;
# "longo" preserves the original verbose behavior.
_NARRATION_LENGTH_CHOICES: list[tuple[str, str]] = [
    ("curto", "Curto (1-2 frases, tensão ainda mais seca)"),
    ("medio", "Médio (3-5 frases, com detalhe sensorial moderado)"),
    ("longo", "Longo (1-2 parágrafos, prosa rica — modo atual)"),
]


def _prompt_narration_length(inp: InputFn, out: PrintFn) -> str:
    """Ask the player how verbose the DM should be, and return the chosen key.

    Accepts "1"/"2"/"3" (positional), or the raw key ("curto"/"medio"/"longo",
    case-insensitive). Empty input falls back to "longo" — the original
    behavior — so that pressing Enter keeps the default.
    """
    out("\n[bold]Comprimento das narrações do DM[/bold]")
    out("Escolha o quanto o mestre deve narrar a cada resposta.")
    out("[dim]Dentro de cada nível, tensão/combate fica mais seco e exploração mais descritivo.[/dim]")
    for i, (_key, label) in enumerate(_NARRATION_LENGTH_CHOICES, 1):
        out(f"  {i}) {label}")
    raw = inp("  Escolha (1-3) [3 = longo]: ").strip()
    if not raw:
        return "longo"
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(_NARRATION_LENGTH_CHOICES):
            return _NARRATION_LENGTH_CHOICES[idx][0]
    lowered = raw.lower()
    for key, _label in _NARRATION_LENGTH_CHOICES:
        if lowered == key:
            return key
    return "longo"


# Per-campaign initial scenario. Optional free-form description from the
# player: where the party starts, what's in the world, factions, vibe, etc.
# Empty = the DM chooses freely (original behavior). Filled = injected into
# build_dm_context_block as the basis for the opening narration.
def _prompt_initial_scenario(inp: InputFn, out: PrintFn) -> str:
    """Ask the player to describe the starting scenario (optional).

    Accepts multi-line input terminated by an empty line or EOF.
    Empty input (just Enter / blank line on the first iteration) returns "" —
    the DM then chooses freely, preserving the original behavior.
    """
    out("\n[bold]Cenário inicial (opcional)[/bold]")
    out("Descreva onde a party começa, o que tem no mundo, facções, clima…")
    out("[dim]Deixe em branco para o mestre decidir livremente. "
        "Termine com uma linha vazia quando terminar.[/dim]")
    lines: list[str] = []
    while True:
        try:
            chunk = inp("  > ")
        except EOFError:
            break
        if chunk.strip() == "":
            # Blank line: ends input. If no content yet, the player skipped.
            break
        lines.append(chunk)
    return "\n".join(lines).strip()


def _summarize_scenario(scenario: str, *, max_len: int = 60) -> str:
    """Render a short preview of the scenario for the Resumo panel."""
    if not scenario:
        return "(não definido — mestre decide)"
    if len(scenario) <= max_len:
        return scenario
    return scenario[: max_len - 1] + "…"


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

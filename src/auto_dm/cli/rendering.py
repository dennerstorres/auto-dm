"""Rich-based rendering helpers for the CLI.

Each function returns a Rich renderable (string, Panel, or Table)
or prints to the console. They're separated from the game loop so
they can be tested by checking the output (or just by importing).
"""
from __future__ import annotations

from typing import Iterable, Union

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from auto_dm.persistence import SaveMetadata
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    ActionResult,
    Character,
    NPC,
)


# Colors used consistently across the CLI.
_COLOR_DM = "cyan"
_COLOR_PLAYER = "green"
_COLOR_COMPANION = "yellow"
_COLOR_SYSTEM = "dim"
_COLOR_HP_HIGH = "green"
_COLOR_HP_MID = "yellow"
COLOR_HP_LOW = "red"
_COLOR_ENEMY = "red"
_COLOR_ALLY = "green"


def render_narration(
    speaker: str, content: str, *, role: str = "dm"
) -> Panel:
    """Wrap a piece of narration in a colored panel.

    ``role`` is "dm" / "player" / "companion" / "system".
    """
    color = {
        "dm": _COLOR_DM,
        "player": _COLOR_PLAYER,
        "companion": _COLOR_COMPANION,
        "system": _COLOR_SYSTEM,
    }.get(role, "white")
    return Panel(
        Text(content, style=color),
        title=f"[bold]{speaker}[/bold]",
        border_style=color,
        expand=False,
    )


def render_action_result(result: ActionResult) -> Panel:
    """Render an engine action result as a small info panel."""
    if not result.success:
        return Panel(
            Text(result.message, style="red"),
            title="[bold]✗ Ação rejeitada[/bold]",
            border_style="red",
            expand=False,
        )
    # The is_hit is None for non-attack actions, so just color based on success.
    return Panel(
        Text(result.message, style="green"),
        title="[bold]✓ Resultado[/bold]",
        border_style="green",
        expand=False,
    )


def _hp_color(current: int, max_hp: int) -> str:
    if max_hp <= 0:
        return _COLOR_HP_HIGH
    ratio = current / max_hp
    if ratio > 0.6:
        return _COLOR_HP_HIGH
    if ratio > 0.3:
        return _COLOR_HP_MID
    return COLOR_HP_LOW


def _hp_bar(current: int, max_hp: int, width: int = 12) -> str:
    if max_hp <= 0:
        return "[" + " " * width + "]"
    filled = max(0, min(width, int(round((current / max_hp) * width))))
    color = _hp_color(current, max_hp)
    bar = "█" * filled + " " * (width - filled)
    return f"[{color}]{bar}[/{color}]"


def render_combat_status(state_manager: StateManager) -> Table:
    """Render a status table for combat: round, initiative, HP bars."""
    state = state_manager.state
    table = Table(
        title=f"Combate — Rodada {state.round_number}"
        if state.in_combat
        else "Fora de combate",
        expand=False,
    )
    table.add_column("Initiative", style="bold")
    table.add_column("Criatura", style="bold")
    table.add_column("HP", justify="right")
    table.add_column("AC", justify="right")

    # Use initiative order if in combat
    if state.in_combat and state.initiative_order:
        ordered_creatures: list = []
        for cid in state.initiative_order:
            c = state_manager.get_character(cid) or state_manager.get_npc(cid)
            if c is not None:
                ordered_creatures.append((c, cid == state.player_character_id))
        # Append any creatures not in the order (e.g. just-spawned NPCs)
        for c in state.party:
            if not any(o[0].id == c.id for o in ordered_creatures):
                ordered_creatures.append((c, c.id == state.player_character_id))
        for c in state.npcs:
            if not any(o[0].id == c.id for o in ordered_creatures):
                ordered_creatures.append((c, False))
    else:
        ordered_creatures = [(c, c.id == state.player_character_id) for c in state.party]
        ordered_creatures.extend((n, False) for n in state.npcs)

    for i, (c, is_player) in enumerate(ordered_creatures):
        marker = "▶" if state.in_combat and i == state.current_turn_index else " "
        color = _COLOR_ALLY if is_player else _COLOR_ENEMY
        table.add_row(
            f"{marker}",
            f"[{color}]{c.name}[/{color}]",
            f"{_hp_bar(c.hp_current, c.hp_max)} {c.hp_current}/{c.hp_max}",
            str(c.armor_class),
        )
    return table


def render_save_list(saves: Iterable[SaveMetadata]) -> Table:
    table = Table(title="Saves disponíveis", expand=False)
    table.add_column("Slot", style="bold")
    table.add_column("Campanha", style="bold")
    table.add_column("Salvo em")
    table.add_column("Schema", justify="right")

    for meta in saves:
        table.add_row(
            meta.slug,
            meta.campaign_name,
            meta.saved_at.strftime("%Y-%m-%d %H:%M"),
            str(meta.schema_version),
        )
    return table


# ============================================================================
# Inventory / conditions / spellbook (Phase 25h)
# ============================================================================


def render_inventory(character: Character) -> Union[Panel, Table]:
    """Show the character's inventory and currently equipped gear.

    Returns a small Panel if the inventory is empty; a Table otherwise.
    """
    if not character.inventory and not _has_any_equipped(character):
        return Panel(
            Text("(inventário vazio)", style="dim"),
            title=f"[bold]Inventário — {character.name}[/bold]",
            border_style="dim",
            expand=False,
        )

    table = Table(
        title=f"Inventário — {character.name}",
        expand=False,
        show_lines=False,
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("Item", style="bold")
    table.add_column("Tipo", style="dim")
    table.add_column("Qtd", justify="right")
    table.add_column("Equipado", justify="center")

    # Show equipped first
    equipped_names = _equipped_names(character)
    for idx, item in enumerate(character.inventory, start=1):
        is_equipped = item.name in equipped_names
        item_type = item.type.value if item.type else "misc"
        table.add_row(
            str(idx),
            item.name,
            item_type,
            str(item.quantity),
            "✓" if is_equipped else "",
        )
    return table


def _has_any_equipped(character: Character) -> bool:
    eq = character.equipped
    return any(
        getattr(eq, slot) is not None
        for slot in ("main_hand", "off_hand", "armor", "amulet", "ring_1", "ring_2", "cloak", "boots")
    )


def _equipped_names(character: Character) -> set[str]:
    """Names of items currently equipped in any slot."""
    eq = character.equipped
    names: set[str] = set()
    for slot in ("main_hand", "off_hand", "armor", "amulet", "ring_1", "ring_2", "cloak", "boots"):
        item = getattr(eq, slot)
        if item is not None:
            names.add(item.name)
    return names


def render_conditions(creature: Union[Character, NPC]) -> Panel:
    """Show the creature's active conditions, exhaustion, and concentration."""
    parts: list[str] = []
    if creature.conditions:
        # Highlight tactical states (DODGING, HIDDEN) separately
        standard = [
            c.value for c in creature.conditions
            if c.value not in {"dodging", "hidden"}
        ]
        tactical = [
            c.value for c in creature.conditions
            if c.value in {"dodging", "hidden"}
        ]
        if standard:
            parts.append(f"Condições: {', '.join(standard)}")
        if tactical:
            parts.append(f"Estado: {', '.join(tactical)}")
    else:
        parts.append("Condições: (nenhuma)")

    if creature.exhaustion_level > 0:
        parts.append(f"Exaustão: nível {creature.exhaustion_level}/6")

    if isinstance(creature, Character) and creature.spellcasting:
        if creature.spellcasting.concentration:
            parts.append(f"Concentrando em: {creature.spellcasting.concentration}")

    return Panel(
        Text(" | ".join(parts)),
        title=f"[bold]Status — {creature.name}[/bold]",
        border_style="yellow",
        expand=False,
    )


def render_spellbook(character: Character) -> Panel:
    """Show the character's prepared spells, slots, and concentration.

    Used by ``/spells`` meta-command. Falls back to a notice when the
    character has no spellcasting.
    """
    if character.spellcasting is None:
        return Panel(
            Text(f"{character.name} não é capaz de lançar magias.", style="dim"),
            title="[bold]Magias[/bold]",
            border_style="dim",
            expand=False,
        )
    sc = character.spellcasting
    lines: list[str] = []

    # Cantrips
    if sc.cantrips_known:
        lines.append(f"[bold]Truques:[/bold] {', '.join(sc.cantrips_known)}")

    # Prepared / known (depending on class)
    if sc.spells_prepared:
        lines.append(f"[bold]Preparadas:[/bold] {', '.join(sc.spells_prepared)}")
    elif sc.spells_known:
        lines.append(f"[bold]Conhecidas:[/bold] {', '.join(sc.spells_known)}")
    if sc.spellbook:
        lines.append(f"[bold]Grimorio:[/bold] {', '.join(sc.spellbook)}")

    # Spell slots
    if sc.spell_slots_max:
        slot_bits: list[str] = []
        for level in sorted(sc.spell_slots_max.keys()):
            if level == 0:
                continue
            max_n = sc.spell_slots_max.get(level, 0)
            cur_n = sc.spell_slots.get(level, 0)
            if max_n > 0:
                slot_bits.append(f"{level}º: {cur_n}/{max_n}")
        if slot_bits:
            lines.append(f"[bold]Slots:[/bold] {' | '.join(slot_bits)}")

    if sc.concentration:
        lines.append(f"[bold]Concentrando:[/bold] {sc.concentration}")
    if sc.ritual_casting:
        lines.append("[dim](capaz de rituais)[/dim]")

    if not lines:
        lines.append("(nenhuma magia)")

    return Panel(
        Text("\n".join(lines)),
        title=f"[bold]Magias — {character.name}[/bold]",
        border_style="magenta",
        expand=False,
    )

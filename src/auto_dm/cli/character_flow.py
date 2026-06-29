"""Interactive character creation flow for the CLI.

The :func:`create_character_interactive` function drives the user
through the PHB character creation steps:

1. Name
2. Race (+ subrace)
3. Class
4. Background
5. Alignment
6. Level (default 1)
7. Stats: standard array or rolled
8. Skill picks (validated against the class's allowed list)
9. For casters: cantrip + spell picks
10. Starting weapon + armor (with sensible defaults)

It's driven by two callbacks:

- ``input_fn`` — returns the next line of user input. Defaults to the
  builtin :func:`input`.
- ``print_fn`` — receives Rich renderables (strings, Panels) to
  display. Defaults to :func:`rich.print`.

This separation lets tests feed scripted input and capture output
without a real TTY.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from rich import print as rich_print
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from auto_dm.character.builder import (
    STANDARD_ARRAY,
    CharacterBuilder,
    parse_class_skill_options,
    parse_skill_name,
)
from auto_dm.character.spells import (
    prepare_caster_spells,
    select_cantrips,
)
from auto_dm.engine.dice import roll_stats
from auto_dm.phb import (
    get_class,
    get_classes,
    get_race,
    get_races,
    get_subclasses_for,
)
from auto_dm.state.models import Character


# Type aliases for the two callbacks
InputFn = Callable[[str], str]
PrintFn = Callable[..., None]


def create_character_interactive(
    *,
    input_fn: Optional[InputFn] = None,
    print_fn: Optional[PrintFn] = None,
) -> Character:
    """Drive a full character creation session and return a Character.

    The returned character has ``is_player=True`` so the game loop
    knows it's the human.
    """
    inp = input_fn or _default_input
    out = print_fn or rich_print

    out(Panel.fit(
        "[bold cyan]Criação de personagem[/bold cyan]\n"
        "[dim]Pressione Enter para aceitar o padrão mostrado entre [][/dim]",
        border_style="cyan",
    ))

    name = _prompt_text(inp, out, "Nome do personagem", default="Aventureiro")

    race_name, subrace = _prompt_race(inp, out)
    class_name = _prompt_class(inp, out)
    subclass_name = _prompt_subclass(inp, out, class_name)
    background = _prompt_text(
        inp, out, "Background", default=_default_background(class_name),
    )
    alignment = _prompt_alignment(inp, out)
    level = _prompt_int(inp, out, "Nível", default=1, min_val=1, max_val=5)

    abilities = _prompt_stats(inp, out)
    skills = _prompt_skills(inp, out, class_name)

    # Build the skeleton
    builder = (
        CharacterBuilder()
        .with_name(name)
        .with_race(race_name, subrace=subrace)
        .with_class(class_name, subclass=subclass_name)
        .with_background(background)
        .with_alignment(alignment)
        .with_level(level)
        .with_ability_scores(abilities)
        .with_skills(skills)
    )

    # Spell selection for casters
    char_class = get_class(class_name)
    if char_class is not None and char_class.spellcasting is not None:
        builder = _attach_spells(inp, out, builder, class_name, level, abilities)

    # Equipment defaults: starting weapon + armor by class
    builder = _attach_default_equipment(builder, class_name)

    draft = builder.build()
    out(Panel.fit(
        f"[bold green]Personagem criado:[/bold green] {draft.character.name}\n"
        f"{draft.character.race} {draft.character.class_} {draft.character.level}\n"
        f"HP {draft.character.hp_current}/{draft.character.hp_max} · "
        f"AC {draft.character.armor_class}",
        border_style="green",
    ))
    # Mark as player, give a stable id
    char = draft.character.model_copy(update={"is_player": True, "id": "p1"})
    return char


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _prompt_text(
    inp: InputFn, out: PrintFn, label: str, *, default: str = ""
) -> str:
    raw = inp(f"{label} [{default}]: ").strip()
    return raw or default


def _prompt_int(
    inp: InputFn, out: PrintFn, label: str, *,
    default: int, min_val: int, max_val: int,
) -> int:
    while True:
        raw = inp(f"{label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            v = int(raw)
        except ValueError:
            out(f"[red]Digite um número entre {min_val} e {max_val}.[/red]")
            continue
        if v < min_val or v > max_val:
            out(f"[red]Valor fora do intervalo ({min_val}–{max_val}).[/red]")
            continue
        return v


def _prompt_choice(
    inp: InputFn, out: PrintFn, label: str, choices: list[str], *,
    default_index: int = 0,
) -> str:
    """Ask the user to pick one of ``choices``."""
    default = choices[default_index]
    options = " / ".join(
        f"{i + 1}) {c}" for i, c in enumerate(choices)
    )
    while True:
        raw = inp(f"{label} ({options}) [{default_index + 1}]: ").strip()
        if not raw:
            return default
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        out(f"[red]Escolha um número de 1 a {len(choices)}.[/red]")


def _prompt_race(inp: InputFn, out: PrintFn) -> tuple[str, Optional[str]]:
    races = [r.name for r in get_races()]
    out(_race_table(races))
    race_name = _prompt_choice(inp, out, "Raça", races)
    race = get_race(race_name)
    if race is None:
        return race_name, None
    subrace_names = [s.name for s in race.subraces]
    if subrace_names:
        subrace = _prompt_choice(inp, out, "Sub-raça", subrace_names)
    else:
        subrace = None
    return race_name, subrace


def _prompt_class(inp: InputFn, out: PrintFn) -> str:
    classes = [c.name for c in get_classes()]
    out(_class_table(classes))
    return _prompt_choice(inp, out, "Classe", classes)


def _prompt_subclass(
    inp: InputFn, out: PrintFn, class_name: str,
) -> Optional[str]:
    """Ask the user to pick a subclass (Phase 25b).

    Returns ``None`` if the class has no subclasses. Many classes grant
    subclass features at L1 (Cleric Domain, Paladin Oath, Sorcerer
    Origin, Warlock Patron); others unlock at L3 but the choice is made
    at character creation (the wizard prompts immediately).
    """
    subclasses = [s.name for s in get_subclasses_for(class_name)]
    if not subclasses:
        return None
    out(f"\n[bold]Subclasse[/bold] ({class_name}) — escolha:")
    for i, sub in enumerate(subclasses, 1):
        out(f"  {i}) {sub}")
    return _prompt_choice(inp, out, "Subclasse", subclasses)


def _prompt_alignment(inp: InputFn, out: PrintFn) -> str:
    options = [
        "LG", "NG", "CG",
        "LN", "N", "CN",
        "LE", "NE", "CE",
    ]
    return _prompt_choice(inp, out, "Tendência", options, default_index=4)


def _prompt_stats(inp: InputFn, out: PrintFn) -> list[int]:
    out("\n[bold]Atributos[/bold] — duas opções:")
    out("  1) Array padrão (15, 14, 13, 12, 10, 8)")
    out("  2) Rolar 4d6kh3 seis vezes")
    while True:
        raw = inp("Estatísticas [1]: ").strip()
        if raw in ("", "1"):
            return list(STANDARD_ARRAY)
        if raw == "2":
            rolls = roll_stats()
            out(f"  Rolagens: {rolls}")
            return list(rolls)
        out("[red]Escolha 1 (array) ou 2 (rolar).[/red]")


def _prompt_skills(
    inp: InputFn, out: PrintFn, class_name: str,
) -> list[str]:
    char_class = get_class(class_name)
    # Find skill options from the class's text (e.g. "Choose two from ...")
    skill_text = ""
    if char_class is not None:
        for feature in char_class.features:
            text = feature.description or ""
            if "Choose" in text and "from" in text.lower():
                skill_text = text
                break
    if not skill_text:
        return []
    options = parse_class_skill_options(skill_text)
    if not options:
        return []
    # How many?  Look for "Choose two" / "Choose three" / etc.
    m = re.search(r"Choose\s+(\w+)", skill_text)
    n = {"one": 1, "two": 2, "three": 3, "four": 4}.get(
        (m.group(1) if m else "two").lower(), 2,
    )
    out(f"\n[bold]Perícias[/bold] — escolha {n} de:")
    for i, opt in enumerate(options, 1):
        out(f"  {i}) {opt.title()}")
    chosen: list[str] = []
    while len(chosen) < n:
        raw = inp(f"  Escolha #{len(chosen) + 1}: ").strip()
        if not raw.isdigit():
            out("[red]Digite o número da perícia.[/red]")
            continue
        idx = int(raw) - 1
        if not (0 <= idx < len(options)):
            out("[red]Número fora da lista.[/red]")
            continue
        pick = options[idx]
        if pick in chosen:
            out("[yellow]Perícia já escolhida, tente outra.[/yellow]")
            continue
        chosen.append(pick)
    # Validate through builder so the user gets feedback on illegal picks
    try:
        # Touch the parse to surface errors early
        for s in chosen:
            parse_skill_name(s)
    except ValueError as exc:
        out(f"[red]Erro: {exc}. Voltando para perícia vazia.[/red]")
        return []
    return chosen


def _attach_spells(
    inp: InputFn, out: PrintFn,
    builder: CharacterBuilder,
    class_name: str, level: int, abilities: list[int],
) -> CharacterBuilder:
    """Auto-pick spells deterministically.

    For MVP, the wizard does not ask the user to pick spells — it
    takes the first N cantrips from the PHB and leaves prepared
    spells empty. The character learns more during play.
    """
    from auto_dm.character.spells import (
        get_cantrips_known,
        SpellSelection,
    )
    from auto_dm.phb import get_class, get_spells_for_class

    char_class = get_class(class_name)
    if char_class is None or char_class.spellcasting is None:
        return builder

    out(f"\n[bold]Magias[/bold] ({class_name}, nível {level})")

    # First N cantrips from the class's PHB list (deterministic).
    n_cantrips = get_cantrips_known(class_name, level)
    class_cantrips = sorted({
        s.name
        for s in get_spells_for_class(class_name)
        if s.level == 0
    })
    chosen_cantrips = class_cantrips[:n_cantrips]
    if chosen_cantrips:
        validated = select_cantrips(
            char_class=char_class, level=level, picks=chosen_cantrips,
        )
        out(f"  Cantrips selecionados: {validated}")
    else:
        validated = []

    # Auto-pick prepared/known spells (best-effort; fall back to empty).
    ability_scores = _ability_scores_from_list(abilities)
    try:
        selection = prepare_caster_spells(
            char_class=char_class,
            level=level,
            abilities=ability_scores,
            proficiency_bonus=2,
            cantrips=validated,
            spells_known=[],
            spells_prepared=[],
            spellbook=[],
        )
        if selection.spells_prepared:
            out(f"  Magias preparadas: {selection.spells_prepared}")
        elif selection.spells_known:
            out(f"  Magias conhecidas: {selection.spells_known}")
    except (ValueError, TypeError):
        # No valid auto-pick (e.g. Bard/Sorcerer/Warlock need user picks).
        # Attach just the cantrips and let the character learn the rest.
        selection = SpellSelection(
            cantrips=validated, spells_prepared=[], spells_known=[],
        )
    return builder.with_spell_selection(selection)


def _ability_scores_from_list(values: list[int]):
    """Build an :class:`AbilityScores` from a list of 6 ints.

    Order follows the PHB standard: STR, DEX, CON, INT, WIS, CHA.
    """
    from auto_dm.state.models import AbilityScores
    s, d, c, i, w, ch = values
    return AbilityScores(
        strength=s, dexterity=d, constitution=c,
        intelligence=i, wisdom=w, charisma=ch,
    )


def _attach_default_equipment(
    builder: CharacterBuilder, class_name: str,
) -> CharacterBuilder:
    defaults = {
        "Fighter": ("Longsword", "Chain Mail", True),
        "Cleric": ("Mace", "Chain Mail", True),
        "Wizard": ("Quarterstaff", None, False),
        "Rogue": ("Shortsword", "Leather Armor", False),
        "Ranger": ("Longbow", "Leather Armor", False),
        "Paladin": ("Longsword", "Chain Mail", True),
        "Barbarian": ("Greataxe", None, False),
        "Bard": ("Rapier", "Leather Armor", False),
        "Druid": ("Scimitar", "Leather Armor", True),
        "Monk": ("Shortsword", None, False),
        "Sorcerer": ("Light Crossbow", None, False),
        "Warlock": ("Light Crossbow", "Leather Armor", False),
    }
    weapon, armor, shield = defaults.get(class_name, ("Dagger", None, False))
    builder = builder.with_starting_weapon(weapon)
    if armor:
        builder = builder.with_starting_armor(armor)
    if shield:
        builder = builder.with_shield()
    return builder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_background(class_name: str) -> str:
    return {
        "Fighter": "Soldier",
        "Rogue": "Criminal",
        "Wizard": "Sage",
        "Cleric": "Acolyte",
    }.get(class_name, "Folk Hero")


def _default_input(prompt: str) -> str:
    return Prompt.ask(prompt)


def _race_table(races: list[str]) -> Table:
    t = Table(title="Raças disponíveis (PHB)", expand=False)
    t.add_column("#", justify="right")
    t.add_column("Raça")
    for i, r in enumerate(races, 1):
        t.add_row(str(i), r)
    return t


def _class_table(classes: list[str]) -> Table:
    t = Table(title="Classes disponíveis (PHB)", expand=False)
    t.add_column("#", justify="right")
    t.add_column("Classe")
    for i, c in enumerate(classes, 1):
        t.add_row(str(i), c)
    return t


def make_player_character(*, input_fn=None, print_fn=None) -> Character:
    """Convenience entry point used by the main CLI."""
    return create_character_interactive(input_fn=input_fn, print_fn=print_fn)

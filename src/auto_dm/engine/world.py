"""World engine: random encounters, travel time, weather, and loot (Phase 40).

Pure state-mutating functions — no LLM, no I/O. ``resolve_travel`` is the
single entry point the narrative loop calls when a ``move`` action carries
a ``travel_hours`` param (see ``agents/narrative.py``). Everything here is
deterministic given a seed, so admin/replay tooling can reproduce any
travel roll from ``GameState.campaign_seed`` + the entry's timestamp.

Design (SPEC.md §12.3, deliberately engine-authoritative — see the
"desvio do plano" note in CLAUDE.md's Fase 40 bullet): the DM proposes
*that* travel is happening and *how long* it takes (a structured param on
an action it already emits); the engine alone decides whether an
encounter happens, what monsters, how much loot, and what the weather
does. The DM only narrates ``WorldEventList`` afterwards — it never rolls
or invents outcomes.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.engine.dice import roll_dice
from auto_dm.engine.inventory import add_item, resolve_catalog_item
from auto_dm.phb import (
    get_encounter_table,
    get_loot_table,
    get_monster,
    get_weather_table,
)
from auto_dm.phb.models import EncounterTableRow, LootTableRow
from auto_dm.state.manager import StateManager
from auto_dm.state.monster_adapter import monster_to_npc, slugify_monster_id

# One encounter check per this many in-game minutes of travel (SPEC §12.3).
ENCOUNTER_CHECK_MINUTES = 4 * 60

# One loot check per this many in-game minutes of travel, at this chance.
# Low odds keep it a nice surprise rather than a guaranteed drip-feed.
LOOT_CHECK_MINUTES = 24 * 60
LOOT_ROLL_CHANCE = 0.15

# Longest single resolve_travel call we'll process in one go. The narrative
# loop should call this once per "move" action; a hard cap keeps a single
# turn from silently looping over months of game time.
MAX_TRAVEL_HOURS = 7 * 24


# Daily travel finds always roll against this table (README.md: the
# `hoard_*` tables are loaded/testable via `compute_loot()` but are NOT
# wired into travel — they're reserved for a future post-combat reward
# feature, since Phase 40 doesn't tie loot to defeating an encounter).
TRAVEL_LOOT_TABLE_ID = "individual"


def _time_of_day_for_minute(minute_of_day: int) -> str:
    """Return "day" or "night" for a minute-of-day value (0-1439).

    Matches the vocabulary of ``EncounterTable.time_of_day`` (used to
    build the ``f"{biome}_{time_of_day}"`` table lookup key). Night is
    20:00-05:59; day is 06:00-19:59.
    """
    hour = (minute_of_day // 60) % 24
    return "night" if hour >= 20 or hour < 6 else "day"


def _display_time_of_day(minute_of_day: int) -> str:
    """Return a pt-BR label for ``GameState.time_of_day`` (shown to the DM).

    Finer-grained than the day/night split used for encounter tables —
    this is flavor text, not a lookup key.
    """
    hour = (minute_of_day // 60) % 24
    if hour < 6:
        return "madrugada"
    if hour < 12:
        return "manhã"
    if hour < 18:
        return "tarde"
    return "noite"


@dataclass
class LootDrop:
    """One loot roll's outcome, already applied to a character."""

    gold_gp: float = 0.0
    items: list[str] = field(default_factory=list)  # item names actually added
    unresolved_items: list[str] = field(default_factory=list)  # catalog misses
    notes: str = ""


@dataclass
class WorldEvent:
    """One thing that happened during a single ``resolve_travel`` call."""

    kind: str  # "encounter" | "loot" | "weather"
    description: str
    npc_ids: list[str] = field(default_factory=list)  # kind == "encounter"
    loot: Optional[LootDrop] = None  # kind == "loot"
    weather: Optional[str] = None  # kind == "weather"


@dataclass
class WorldEventList:
    """Everything that happened over a ``resolve_travel`` call."""

    events: list[WorldEvent] = field(default_factory=list)
    seed: str = ""
    elapsed_minutes: int = 0
    combat_started: bool = False

    @property
    def encounters(self) -> list[WorldEvent]:
        return [e for e in self.events if e.kind == "encounter"]

    def __bool__(self) -> bool:
        return bool(self.events)


def _row_for_roll(rows: list, roll: int):
    for row in rows:
        if row.roll_min <= roll <= row.roll_max:
            return row
    return None


def _resolve_count(count_str: str, rng: random.Random) -> int:
    """Resolve a monster-stack ``count`` (fixed digit or dice notation)."""
    count_str = (count_str or "1").strip()
    if count_str.isdigit():
        return max(1, int(count_str))
    return max(1, roll_dice(count_str, rng=rng).total)


def _spawn_row_monsters(
    state_manager: StateManager,
    row: EncounterTableRow,
    *,
    rng: random.Random,
) -> tuple[list[str], list[str]]:
    """Spawn every monster stack in an encounter row into ``state.npcs``.

    Returns ``(npc_ids, descriptions)``. Monster ids that don't resolve
    against the PHB monster catalog are skipped (curated tables are
    verified at authoring time, but this stays defensive against future
    edits).
    """
    npc_ids: list[str] = []
    descriptions: list[str] = []
    existing_ids = {n.id for n in state_manager.state.npcs}
    for entry in row.monsters:
        monster = get_monster(entry.id)
        if monster is None:
            continue
        count = _resolve_count(entry.count, rng)
        base_slug = slugify_monster_id(monster.name)
        for _ in range(count):
            npc_id = base_slug
            suffix = 1
            while npc_id in existing_ids:
                suffix += 1
                npc_id = f"{base_slug}_{suffix}"
            existing_ids.add(npc_id)
            npc = monster_to_npc(monster, npc_id=npc_id, is_hostile=True)
            state_manager.state.npcs.append(npc)
            npc_ids.append(npc_id)
        descriptions.append(f"{count}x {monster.name}" if count > 1 else monster.name)
    return npc_ids, descriptions


def roll_encounter(
    state_manager: StateManager,
    biome: str,
    time_of_day: str,
    *,
    rng: random.Random,
) -> Optional[WorldEvent]:
    """Roll one d100 encounter check for ``{biome}_{time_of_day}``.

    Returns ``None`` when there's no table for the combo, or when the
    row rolled has no monsters (the common "nothing happens" case).
    """
    table = get_encounter_table(f"{biome}_{time_of_day}")
    if table is None or not table.entries:
        return None
    roll = rng.randint(1, 100)
    row = _row_for_roll(table.entries, roll)
    if row is None or not row.monsters:
        return None
    npc_ids, descriptions = _spawn_row_monsters(state_manager, row, rng=rng)
    if not npc_ids:
        return None
    description = row.notes or f"Encontro: {', '.join(descriptions)}"
    return WorldEvent(kind="encounter", description=description, npc_ids=npc_ids)


def compute_loot(
    table_id: str,
    roll: int,
    *,
    rng: Optional[random.Random] = None,
) -> LootDrop:
    """Resolve a loot table row (looked up by ``table_id``) into gold + items.

    Does not touch any character — pure computation. Callers (e.g.
    ``resolve_travel``) apply the result via ``engine/inventory.py``.
    ``table_id`` is one of the ids in ``data/world_tables/loot/*.json``
    (``"individual"``, ``"hoard_low"``, ``"hoard_mid"``, ``"hoard_high"``).
    """
    rng = rng or random.Random()
    table = get_loot_table(table_id)
    if table is None or not table.entries:
        return LootDrop()
    row: Optional[LootTableRow] = _row_for_roll(table.entries, roll)
    if row is None:
        return LootDrop()
    gold_gp = 0.0
    if row.gold_dice:
        gold_gp = roll_dice(row.gold_dice, rng=rng).total * row.gold_multiplier
    return LootDrop(gold_gp=gold_gp, items=list(row.items), notes=row.notes)


def _apply_loot(state_manager: StateManager, drop: LootDrop) -> LootDrop:
    """Credit gold and add resolved items to the player character.

    Item names that don't resolve against the catalog are moved to
    ``unresolved_items`` (kept out of ``items`` so the caller only lists
    what was actually added) and never crash the travel resolution.
    """
    player = state_manager.get_character(state_manager.state.player_character_id)
    if player is None:
        return drop
    applied = LootDrop(gold_gp=drop.gold_gp, notes=drop.notes)
    player.gold_gp += drop.gold_gp
    for name in drop.items:
        item = resolve_catalog_item(name)
        if item is None:
            applied.unresolved_items.append(name)
            continue
        add_item(player, item)
        applied.items.append(item.name)
    return applied


def resolve_travel(
    state_manager: StateManager,
    hours: float,
    *,
    combat_engine: Optional[CombatEngine] = None,
    rng_seed: Optional[str] = None,
    biome: str = "road",
) -> WorldEventList:
    """Advance the game clock and roll world events for a travel segment.

    Algorithm (SPEC §12.3):
    1. Clamp ``hours`` to ``(0, MAX_TRAVEL_HOURS]``.
    2. One encounter check per ``ENCOUNTER_CHECK_MINUTES`` block, gated by
       ``GameState.world_event_cooldown_minutes`` since the last check
       (anti-abuse: repeated short travel calls can't be used to reroll
       until a fight doesn't happen).
    3. One weather roll for the whole call (weather doesn't change
       mid-segment at this level of simulation).
    4. One loot check per full day of travel, at ``LOOT_ROLL_CHANCE``.
    5. Advance ``elapsed_game_minutes`` and ``time_of_day`` regardless of
       whether anything rolled.
    6. If any encounter spawned hostile NPCs and a ``combat_engine`` was
       given, start combat immediately (the narrative loop then treats
       the rest of the turn as combat).

    Returns a :class:`WorldEventList` describing everything that
    happened, for the DM to narrate (never to invent).
    """
    state = state_manager.state
    hours = max(0.0, min(hours, MAX_TRAVEL_HOURS))
    total_minutes = int(round(hours * 60))

    seed = rng_seed or f"{state.campaign_seed}:{state.elapsed_game_minutes}"
    rng = random.Random(seed)

    events: list[WorldEvent] = []
    combat_started = False
    encounter_interrupted = False

    if total_minutes <= 0:
        return WorldEventList(events=events, seed=seed, elapsed_minutes=0)

    # --- Encounter checks, one per ENCOUNTER_CHECK_MINUTES block -------
    minutes_walked = 0
    while minutes_walked < total_minutes:
        block = min(ENCOUNTER_CHECK_MINUTES, total_minutes - minutes_walked)
        minutes_walked += block
        clock_at_check = state.elapsed_game_minutes + minutes_walked
        since_last = clock_at_check - state.last_world_event_minute
        if since_last < state.world_event_cooldown_minutes:
            continue
        time_of_day = _time_of_day_for_minute(clock_at_check % (24 * 60))
        event = roll_encounter(state_manager, biome, time_of_day, rng=rng)
        state.last_world_event_minute = clock_at_check
        if event is not None:
            events.append(event)
            encounter_interrupted = True
            if combat_engine is not None and event.npc_ids:
                combat_engine.start_combat(state_manager)
                combat_started = True
            # An encounter interrupts travel — the party isn't covering
            # more ground while dealing with it — so stop checking
            # further blocks, whether or not combat actually started.
            break

    # An encounter mid-journey means the party only actually covered
    # ``minutes_walked`` before it broke out — the rest of the requested
    # journey hasn't happened yet (the DM/player decide what to do once
    # it resolves). Otherwise the full request stands.
    minutes_traveled = minutes_walked if encounter_interrupted else total_minutes

    # --- Weather: one roll for the segment actually traveled ------------
    weather_table = get_weather_table()
    if weather_table is not None and weather_table.entries:
        roll = rng.randint(1, 20)
        row = _row_for_roll(weather_table.entries, roll)
        if row is not None:
            state.weather = row.weather
            events.append(
                WorldEvent(
                    kind="weather",
                    description=f"O clima muda para: {row.weather}.",
                    weather=row.weather,
                )
            )

    # --- Loot: one check per full day actually traveled -----------------
    full_days = minutes_traveled // LOOT_CHECK_MINUTES
    for _ in range(full_days):
        if rng.random() >= LOOT_ROLL_CHANCE:
            continue
        roll = rng.randint(1, 100)
        drop = compute_loot(TRAVEL_LOOT_TABLE_ID, roll, rng=rng)
        if drop.gold_gp <= 0 and not drop.items:
            continue
        applied = _apply_loot(state_manager, drop)
        parts = []
        if applied.gold_gp > 0:
            parts.append(f"{applied.gold_gp:g} po")
        parts.extend(applied.items)
        description = drop.notes or f"Achado no caminho: {', '.join(parts)}"
        events.append(WorldEvent(kind="loot", description=description, loot=applied))

    # --- Advance the clock by the time actually traveled ----------------
    state.elapsed_game_minutes += minutes_traveled
    state.time_of_day = _display_time_of_day(state.elapsed_game_minutes % (24 * 60))

    return WorldEventList(
        events=events,
        seed=seed,
        elapsed_minutes=minutes_traveled,
        combat_started=combat_started,
    )

"""Phase 40a — world_tables data integrity tests.

Covers the loader/lookup plumbing (``phb/loader.py``, ``phb/lookup.py``,
``phb/__init__.py``) for the curated encounter/loot/weather tables, plus
data integrity checks over the actual JSON content in
``data/world_tables/``:

- Every encounter table's d100 rows cover 1-100 with no gaps/overlaps.
- Every loot table's d100 rows cover 1-100 with no gaps/overlaps.
- The weather table's d20 rows cover 1-20 with no gaps/overlaps.
- Every monster ``id`` referenced resolves via ``get_monster``.
- Every item name referenced resolves via ``resolve_catalog_item``
  (the same resolver ``engine/world.py`` uses to apply loot).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from auto_dm.engine.inventory import resolve_catalog_item
from auto_dm.phb import (
    get_encounter_table,
    get_encounter_tables,
    get_loot_table,
    get_loot_tables,
    get_monster,
    get_weather_table,
    get_world_tables_root,
    set_world_tables_root,
)


# ============================================================================
# Loader / lookup plumbing
# ============================================================================


class TestEncounterTableLookup:
    def test_all_tables_loaded(self):
        tables = get_encounter_tables()
        ids = {t.id for t in tables}
        assert ids == {
            "forest_day", "forest_night",
            "road_day", "road_night",
            "dungeon_level_1", "dungeon_level_5",
        }

    def test_get_by_id(self):
        table = get_encounter_table("road_day")
        assert table is not None
        assert table.biome == "road"
        assert table.time_of_day == "day"
        assert table.name == "Estrada — Dia"

    def test_unknown_id_returns_none(self):
        assert get_encounter_table("ocean_day") is None


class TestLootTableLookup:
    def test_all_tables_loaded(self):
        tables = get_loot_tables()
        ids = {t.id for t in tables}
        assert ids == {"individual", "hoard_low", "hoard_mid", "hoard_high"}

    def test_get_by_id(self):
        table = get_loot_table("hoard_mid")
        assert table is not None
        assert table.tier == "mid"

    def test_unknown_id_returns_none(self):
        assert get_loot_table("hoard_ultra") is None


class TestWeatherTableLookup:
    def test_loaded(self):
        table = get_weather_table()
        assert table is not None
        assert len(table.entries) == 6


class TestSetWorldTablesRoot:
    def test_switching_root_clears_cache_and_reloads(self, tmp_path: Path):
        encounters_dir = tmp_path / "encounters"
        encounters_dir.mkdir()
        (encounters_dir / "stub_any.json").write_text(
            '{"id": "stub_any", "name": "Stub", "biome": "stub", '
            '"time_of_day": "any", "entries": []}',
            encoding="utf-8",
        )
        original_root = get_world_tables_root()
        try:
            set_world_tables_root(tmp_path)
            tables = get_encounter_tables()
            assert len(tables) == 1
            assert tables[0].id == "stub_any"
            assert get_loot_tables() == []
            assert get_weather_table() is None
        finally:
            set_world_tables_root(original_root)

    def test_full_tables_available_after_switch_back(self):
        assert len(get_encounter_tables()) == 6
        assert len(get_loot_tables()) == 4
        assert get_weather_table() is not None


# ============================================================================
# Data integrity: roll coverage
# ============================================================================


def _assert_full_d100_coverage(entries) -> None:
    spans = sorted((e.roll_min, e.roll_max) for e in entries)
    assert spans[0][0] == 1, f"coverage doesn't start at 1: {spans}"
    assert spans[-1][1] == 100, f"coverage doesn't end at 100: {spans}"
    expected_next = 1
    for lo, hi in spans:
        assert lo == expected_next, f"gap or overlap before roll {lo}: {spans}"
        assert hi >= lo
        expected_next = hi + 1


class TestEncounterTableCoverage:
    @pytest.mark.parametrize(
        "table_id",
        [
            "forest_day", "forest_night", "road_day", "road_night",
            "dungeon_level_1", "dungeon_level_5",
        ],
    )
    def test_full_d100_coverage(self, table_id):
        table = get_encounter_table(table_id)
        assert table is not None
        _assert_full_d100_coverage(table.entries)


class TestLootTableCoverage:
    @pytest.mark.parametrize(
        "table_id", ["individual", "hoard_low", "hoard_mid", "hoard_high"]
    )
    def test_full_d100_coverage(self, table_id):
        table = get_loot_table(table_id)
        assert table is not None
        _assert_full_d100_coverage(table.entries)


class TestWeatherTableCoverage:
    def test_full_d20_coverage(self):
        table = get_weather_table()
        spans = sorted((e.roll_min, e.roll_max) for e in table.entries)
        assert spans[0][0] == 1
        assert spans[-1][1] == 20
        expected_next = 1
        for lo, hi in spans:
            assert lo == expected_next, f"gap or overlap before roll {lo}: {spans}"
            expected_next = hi + 1


# ============================================================================
# Data integrity: referenced ids/names resolve against the real catalogs
# ============================================================================


class TestMonsterIdsResolve:
    @pytest.mark.parametrize(
        "table_id",
        [
            "forest_day", "forest_night", "road_day", "road_night",
            "dungeon_level_1", "dungeon_level_5",
        ],
    )
    def test_every_monster_id_resolves(self, table_id):
        table = get_encounter_table(table_id)
        for row in table.entries:
            for entry in row.monsters:
                assert get_monster(entry.id) is not None, (
                    f"{table_id}: unresolved monster id {entry.id!r}"
                )


class TestLootItemNamesResolve:
    @pytest.mark.parametrize(
        "table_id", ["individual", "hoard_low", "hoard_mid", "hoard_high"]
    )
    def test_every_item_name_resolves(self, table_id):
        table = get_loot_table(table_id)
        for row in table.entries:
            for name in row.items:
                assert resolve_catalog_item(name) is not None, (
                    f"{table_id}: unresolved item name {name!r}"
                )

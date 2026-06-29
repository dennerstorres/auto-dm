"""Tests for the monster lookup API."""
from __future__ import annotations

from pathlib import Path

import pytest

from auto_dm.phb import get_monster, get_monsters, set_phb_root


@pytest.fixture(autouse=True)
def _reset_phb_cache():
    from auto_dm.phb import get_phb_root as _gpr
    original_root = _gpr()
    real_root = Path(__file__).resolve().parents[1] / "data" / "phb"
    set_phb_root(real_root)
    yield
    set_phb_root(original_root)


class TestMonsterLookup:
    def test_get_monster_exact(self) -> None:
        goblin = get_monster("Goblin")
        assert goblin is not None
        assert goblin.name == "Goblin"
        assert goblin.hp_average == 7

    def test_get_monster_case_insensitive(self) -> None:
        assert get_monster("GOBLIN") is not None
        assert get_monster("goblin") is not None
        assert get_monster("GoBLiN") is not None

    def test_get_monster_partial_match(self) -> None:
        # Partial matches — useful for DM narration prompts.
        adult = get_monster("Adult Red Dragon")
        assert adult is not None
        assert "Red Dragon" in adult.name

    def test_get_monster_unknown(self) -> None:
        assert get_monster("Nonexistent Beast") is None

    def test_get_monsters_returns_all(self) -> None:
        all_monsters = get_monsters()
        assert len(all_monsters) == 318


class TestMonsterFilters:
    def test_filter_by_cr_max(self) -> None:
        low = get_monsters(cr_max=1)
        assert all(m.challenge_rating <= 1 for m in low)
        assert len(low) > 0

    def test_filter_by_cr_min(self) -> None:
        high = get_monsters(cr_min=15)
        assert all(m.challenge_rating >= 15 for m in high)
        assert len(high) > 0

    def test_filter_by_cr_range(self) -> None:
        mid = get_monsters(cr_min=5, cr_max=10)
        assert all(5 <= m.challenge_rating <= 10 for m in mid)
        assert len(mid) > 0

    def test_filter_by_type(self) -> None:
        dragons = get_monsters(type_="dragon")
        assert len(dragons) > 0
        assert all(m.type.value == "dragon" for m in dragons)

    def test_filter_by_type_undead(self) -> None:
        undead = get_monsters(type_="undead")
        assert len(undead) > 0
        assert all(m.type.value == "undead" for m in undead)

    def test_filter_combined(self) -> None:
        result = get_monsters(type_="dragon", cr_min=1, cr_max=10)
        assert len(result) > 0
        for m in result:
            assert m.type.value == "dragon"
            assert 1 <= m.challenge_rating <= 10

    def test_filter_no_match_returns_empty(self) -> None:
        # No CR 100 monster exists in PHB.
        assert get_monsters(cr_min=100) == []
        # No "kobold" type — actual type is "humanoid" with subtype "kobold".
        assert get_monsters(type_="kobold") == []


class TestCaching:
    def test_caches_are_stable(self) -> None:
        # Calling twice returns the same list reference (cached).
        a = get_monsters()
        b = get_monsters()
        # Same list object — caches are reused
        assert a is b

    def test_set_phb_root_resets_cache(self, tmp_path: Path) -> None:
        # When the PHB root changes, caches must reset so the new tree
        # is loaded fresh.
        get_monsters()  # populate cache
        set_phb_root(tmp_path)  # empty dir
        result = get_monsters()
        assert result == []
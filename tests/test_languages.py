"""Tests for the languages catalog loader/lookup."""
from __future__ import annotations

import pytest

from auto_dm.phb import (
    get_language,
    get_languages,
    set_phb_root,
)


@pytest.fixture(autouse=True)
def _reset_phb_cache(tmp_path):
    """Each test gets a fresh cache to avoid order dependencies."""
    from pathlib import Path
    real_root = Path(__file__).resolve().parents[1] / "data" / "phb"
    set_phb_root(real_root)
    yield


class TestLanguagesCatalog:
    def test_loads_standard_and_exotic(self) -> None:
        langs = get_languages()
        assert len(langs) >= 16  # 8 standard + 8 exotic

    def test_eight_standard_languages(self) -> None:
        standard = [l for l in get_languages() if l.category == "standard"]
        assert len(standard) == 8
        names = {l.name for l in standard}
        assert "Common" in names
        assert "Dwarvish" in names
        assert "Elvish" in names
        assert "Halfling" in names

    def test_eight_exotic_languages(self) -> None:
        exotic = [l for l in get_languages() if l.category == "exotic"]
        assert len(exotic) == 8
        names = {l.name for l in exotic}
        assert "Draconic" in names
        assert "Infernal" in names
        assert "Celestial" in names
        assert "Abyssal" in names

    def test_lookup_common(self) -> None:
        lang = get_language("Common")
        assert lang is not None
        assert lang.category == "standard"
        assert lang.script == "Common"

    def test_lookup_draconic(self) -> None:
        lang = get_language("Draconic")
        assert lang is not None
        assert lang.category == "exotic"
        assert "dragons" in lang.typical_speakers.lower()

    def test_lookup_case_insensitive(self) -> None:
        assert get_language("COMMON") is not None
        assert get_language("common") is not None

    def test_lookup_unknown_returns_none(self) -> None:
        assert get_language("Klingon") is None

    def test_primordial_in_exotic(self) -> None:
        lang = get_language("Primordial")
        assert lang is not None
        assert lang.category == "exotic"

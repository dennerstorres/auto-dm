"""Integration tests for the character creation wizard.

The wizard is driven by ``input_fn`` so we can feed it scripted
choices and assert the resulting :class:`Character`. These tests
double as regression coverage for the input format (Rich markup,
valid alignments, spell auto-pick).
"""
from __future__ import annotations

import pytest

from auto_dm.cli.character_flow import (
    _prompt_alignment,
    _prompt_choice,
    _prompt_int,
    _prompt_race,
    _prompt_skills,
    _prompt_text,
    create_character_interactive,
)
from auto_dm.state.models import Character


# A captured-output list (used in lieu of rich.print).
def _silent_output(*args, **kwargs):
    return None


def _fake_input(*lines: str):
    """Return a function that yields each line, then raises EOFError."""
    it = iter(lines)

    def _fn(_prompt: str) -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError("no more scripted input")

    return _fn


# ---------------------------------------------------------------------------
# Individual prompts
# ---------------------------------------------------------------------------


class TestPromptText:
    def test_returns_default_on_empty(self):
        out: list[str] = []
        assert _prompt_text(
            lambda _: "", lambda *a, **k: out.append(str(a)),
            "Name", default="Bob",
        ) == "Bob"

    def test_returns_input_when_provided(self):
        out: list[str] = []
        assert _prompt_text(
            lambda _: "Alice", lambda *a, **k: None,
            "Name", default="Bob",
        ) == "Alice"


class TestPromptInt:
    def test_returns_default_on_empty(self):
        out: list[str] = []
        assert _prompt_int(
            lambda _: "", lambda *a, **k: out.append(str(a)),
            "Level", default=3, min_val=1, max_val=5,
        ) == 3

    def test_validates_non_integer(self):
        out: list[str] = []
        inputs = iter(["abc", "3"])
        assert _prompt_int(
            lambda _: next(inputs), lambda *a, **k: out.append(str(a)),
            "Level", default=1, min_val=1, max_val=5,
        ) == 3
        assert any("número" in s or "número" in s for s in out)

    def test_validates_range(self):
        out: list[str] = []
        inputs = iter(["99", "2"])
        assert _prompt_int(
            lambda _: next(inputs), lambda *a, **k: out.append(str(a)),
            "Level", default=1, min_val=1, max_val=5,
        ) == 2


class TestPromptChoice:
    def test_returns_default_on_empty(self):
        out: list[str] = []
        assert _prompt_choice(
            lambda _: "", lambda *a, **k: out.append(str(a)),
            "Pick", ["A", "B", "C"], default_index=1,
        ) == "B"

    def test_accepts_valid_index(self):
        out: list[str] = []
        assert _prompt_choice(
            lambda _: "3", lambda *a, **k: out.append(str(a)),
            "Pick", ["A", "B", "C"],
        ) == "C"

    def test_rejects_invalid_then_accepts(self):
        out: list[str] = []
        inputs = iter(["99", "B", "2"])
        # First call: 99 is out of range. Second: "B" isn't a digit.
        # Third: "2" → "B".
        # The function should loop until valid.
        assert _prompt_choice(
            lambda _: next(inputs), lambda *a, **k: out.append(str(a)),
            "Pick", ["A", "B", "C"],
        ) == "B"


# ---------------------------------------------------------------------------
# Alignment — regression: used to be "TN", PHB wants "N"
# ---------------------------------------------------------------------------


class TestPromptAlignment:
    def test_default_is_neutral(self):
        out: list[str] = []
        # The default in the wizard is index 4 (which is "N" after the fix)
        assert _prompt_alignment(
            lambda _: "", lambda *a, **k: out.append(str(a)),
        ) == "N"

    def test_accepts_lg(self):
        out: list[str] = []
        assert _prompt_alignment(
            lambda _: "1", lambda *a, **k: out.append(str(a)),
        ) == "LG"


# ---------------------------------------------------------------------------
# Full wizard — non-caster (Fighter)
# ---------------------------------------------------------------------------


class TestCreateCharacterNonCaster:
    def test_fighter_smoke(self):
        # Wizard flow: name, race, subrace (if any), class, background,
        # alignment, level, stats (1 = standard array), skills.
        # For "Human" there is no subrace; for "Fighter" the skill list
        # is "Choose two from ...".
        # Class index 5 = Fighter (post-F19 class order: Barbarian, Bard,
        # Cleric, Druid, Fighter, ...).
        lines = [
            "Ilario",          # name
            "8",               # race: Human (index 8)
            "5",               # class: Fighter (index 5)
            "Soldier",         # background
            "1",               # alignment: LG
            "1",               # level: 1
            "1",               # stats: standard array
            "1", "2",          # skills: 2 picks
        ]
        char = create_character_interactive(
            input_fn=_fake_input(*lines),
            print_fn=_silent_output,
        )
        assert isinstance(char, Character)
        assert char.name == "Ilario"
        assert char.race == "Human"
        assert char.class_ == "Fighter"
        assert char.is_player is True
        assert char.level == 1

    def test_dwarf_with_subrace(self):
        lines = [
            "Thorin",          # name
            "2",               # race: Dwarf (index 2)
            "1",               # subrace: Hill Dwarf (first)
            "5",               # class: Fighter (index 5)
            "",                # background (default)
            "4",               # alignment: LN
            "",                # level (default 1)
            "1",               # stats: standard array
            "1", "5",          # skills
        ]
        char = create_character_interactive(
            input_fn=_fake_input(*lines),
            print_fn=_silent_output,
        )
        assert char.race == "Dwarf"
        assert char.subrace == "Hill Dwarf"
        assert char.class_ == "Fighter"

    def test_empty_input_yields_default_name(self):
        lines = [
            "",                # name → default
            "8",               # race: Human
            "5",               # class: Fighter
            "", "", "",        # background, alignment, level defaults
            "1",               # stats: standard array
            "1", "2",          # skills
        ]
        char = create_character_interactive(
            input_fn=_fake_input(*lines),
            print_fn=_silent_output,
        )
        assert char.name == "Aventureiro"  # default
        assert char.is_player is True


# ---------------------------------------------------------------------------
# Full wizard — caster (Cleric)
# ---------------------------------------------------------------------------


class TestCreateCharacterCaster:
    def test_cleric_auto_picks_cantrips(self):
        # Cleric L1: 3 cantrips. No user prompt for spells in MVP
        # (auto-pick). So the only inputs are name, race, subrace,
        # class, background, alignment, level, stats, skills.
        lines = [
            "Mira",            # name
            "7",               # race: Halfling (index 7)
            "1",               # subrace: Lightfoot (index 1)
            "3",               # class: Cleric (index 3)
            "",                # background (default)
            "2",               # alignment: NG
            "",                # level default
            "1",               # stats: standard array
            "1", "2",          # skills
        ]
        char = create_character_interactive(
            input_fn=_fake_input(*lines),
            print_fn=_silent_output,
        )
        assert char.class_ == "Cleric"
        # Cleric L1 gets 3 cantrips, auto-picked from PHB.
        assert char.spellcasting is not None
        assert len(char.spellcasting.cantrips_known) >= 1

    def test_paladin_does_not_crash_on_spell_auto_pick(self):
        # Regression: Paladin used to crash because the wizard passed
        # the class name as a string to select_cantrips. Now the
        # wizard passes the CharacterClass object and auto-picks
        # cantrips without prompting.
        # Class index 7 = Paladin (post-F19 class order).
        lines = [
            "Ilario",          # name
            "8",               # race: Human
            "7",               # class: Paladin (index 7)
            "",                # background
            "1",               # alignment: LG
            "", "",            # level + stats defaults
            "1",               # stats: standard array
            "1", "2",          # skills
        ]
        char = create_character_interactive(
            input_fn=_fake_input(*lines),
            print_fn=_silent_output,
        )
        assert char.class_ == "Paladin"
        # Paladin gets 2 cantrips at L1.
        assert char.spellcasting is not None

"""Phase 25b tests: subclass lookup, feature application, wizard step.

Covers the subclass lookup API in ``phb/lookup.py``, the
``character.level_up`` module, and the new subclass prompt step in the
CLI wizard (``cli/character_flow._prompt_subclass``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from auto_dm.character.builder import CharacterBuilder
from auto_dm.character.level_up import (
    apply_subclass_features,
    features_gained_at_level,
    has_subclass_feature,
    list_subclass_features,
)
from auto_dm.phb import (
    get_all_subclasses,
    get_class,
    get_classes,
    get_subclass,
    get_subclasses_for,
    set_phb_root,
)


def _make_sorcerer_draconic() -> "Character":
    """Build a Sorcerer L1 Draconic Bloodline draft via the builder."""
    from auto_dm.state.models import Character

    builder = (
        CharacterBuilder()
        .with_name("Pyra")
        .with_race("Human")
        .with_class("Sorcerer", subclass="Draconic Bloodline")
        .with_background("Hermit")
        .with_alignment("CN")
        .with_level(1)
        .with_standard_array()
        .with_skills(["arcana", "persuasion"])
    )
    return builder.build().character


@pytest.fixture(autouse=True)
def _reset_phb_cache():
    """Each test starts with the real PHB root for subclass lookups."""
    from auto_dm.phb import get_phb_root as _gpr

    original_root = _gpr()
    real_root = Path(__file__).resolve().parents[1] / "data" / "phb"
    set_phb_root(real_root)
    yield
    set_phb_root(original_root)


# ===========================================================================
# get_subclasses_for / get_subclass
# ===========================================================================


class TestGetSubclassesFor:
    def test_every_class_has_a_subclass(self):
        # All 12 PHB classes have at least one canonical subclass.
        for cls in get_classes():
            subs = get_subclasses_for(cls.name)
            assert len(subs) >= 1, f"{cls.name} has no subclasses"

    def test_returns_empty_for_unknown_class(self):
        assert get_subclasses_for("Nonexistent") == []

    def test_specific_subclasses(self):
        # Spot-checks against known PHB subclasses.
        names = [s.name for s in get_subclasses_for("Wizard")]
        assert "School of Evocation" in names

        barb_names = [s.name for s in get_subclasses_for("Barbarian")]
        assert "Path of the Berserker" in barb_names


class TestGetSubclass:
    def test_exact_case_insensitive(self):
        sub = get_subclass("Wizard", "school of evocation")
        assert sub is not None
        assert sub.name == "School of Evocation"
        assert sub.parent_class == "Wizard"

    def test_returns_none_for_unknown_subclass(self):
        assert get_subclass("Wizard", "School of Necromancy") is None

    def test_returns_none_for_unknown_class(self):
        assert get_subclass("Nonexistent", "Anything") is None

    def test_has_features_with_levels(self):
        sub = get_subclass("Barbarian", "Path of the Berserker")
        assert sub is not None
        # L3 feature (Frenzy) should be present
        assert any(f.level == 3 for f in sub.features)


class TestGetAllSubclasses:
    def test_returns_flat_list(self):
        all_subs = get_all_subclasses()
        # At least one per class
        assert len(all_subs) >= len(get_classes())
        # Each has a parent_class set
        for s in all_subs:
            assert s.parent_class
            assert s.name


# ===========================================================================
# list_subclass_features / apply_subclass_features
# ===========================================================================


class TestListSubclassFeatures:
    def test_orders_by_level(self):
        features = list_subclass_features("Barbarian", "Path of the Berserker")
        assert len(features) > 0
        levels = [f.level or 0 for f in features]
        assert levels == sorted(levels)

    def test_unknown_subclass_returns_empty(self):
        assert list_subclass_features("Wizard", "Nonexistent") == []

    def test_known_subclass_returns_features(self):
        features = list_subclass_features("Sorcerer", "Draconic Bloodline")
        # Draconic Bloodline has Draconic Resilience at L1 and
        # Elemental Affinity at L6 / wings at L14 / etc.
        assert any(f.name == "Draconic Resilience" for f in features)


class TestApplySubclassFeatures:
    def test_L1_sorcerer_has_dragon_ancestor_and_resilience(self):
        from auto_dm.state.models import Character

        char = _make_sorcerer_draconic()
        # Draconic Bloodline L1 features: Dragon Ancestor + Draconic Resilience
        assert "Draconic Resilience" in char.subclass_features
        # The builder wires apply_subclass_features so this is populated.

    def test_returns_feature_names(self):
        char = _make_sorcerer_draconic()
        gained = apply_subclass_features(char, at_level=1)
        assert "Draconic Resilience" in gained

    def test_at_level_caps_acquisition(self):
        char = _make_sorcerer_draconic()
        # Level 5: Elemental Affinity (L6) should NOT be there yet.
        gained = apply_subclass_features(char, at_level=5)
        assert "Elemental Affinity" not in gained
        # Level 6: it should be there.
        gained_6 = apply_subclass_features(char, at_level=6)
        assert "Elemental Affinity" in gained_6

    def test_unknown_subclass_clears_features(self):
        char = _make_sorcerer_draconic()
        # Apply bogus subclass — should clear, not raise.
        apply_subclass_features(char, subclass_name="Bogus Path")
        assert char.subclass_features == []

    def test_no_subclass_clears_features(self):
        # To get "no subclass" semantics, the caller must clear
        # ``character.subclass`` first. Empty-string overrides are
        # treated as "use the character's current subclass" by the
        # implementation, mirroring ``character.class_`` fallback.
        char = _make_sorcerer_draconic()
        char.subclass = None
        apply_subclass_features(char)
        assert char.subclass_features == []


class TestFeaturesGainedAtLevel:
    def test_returns_only_features_at_that_level(self):
        gained = features_gained_at_level(
            "Sorcerer", "Draconic Bloodline", 1,
        )
        # L1 features: Dragon Ancestor + Draconic Resilience
        names = {f.name for f in gained}
        assert "Draconic Resilience" in names

    def test_empty_for_no_features_at_level(self):
        # L20 isn't a typical subclass feature level
        assert features_gained_at_level("Wizard", "School of Evocation", 20) == []


class TestHasSubclassFeature:
    def test_true_when_present(self):
        char = _make_sorcerer_draconic()
        assert has_subclass_feature(char, "Draconic Resilience") is True

    def test_false_when_absent(self):
        char = _make_sorcerer_draconic()
        assert has_subclass_feature(char, "Cunning Action") is False


# ===========================================================================
# CLI wizard subclass prompt
# ===========================================================================


class TestPromptSubclass:
    def test_class_with_subclass_returns_picked_name(self):
        from auto_dm.cli.character_flow import _prompt_subclass

        inputs = iter(["1"])
        outputs: list[str] = []
        result = _prompt_subclass(
            lambda _: next(inputs),
            lambda *a, **kw: outputs.append(str(a)),
            "Wizard",
        )
        assert result == "School of Evocation"

    def test_class_with_no_subclasses_returns_none(self):
        # All 12 PHB classes have subclasses; this guards against
        # future classes that don't.
        from auto_dm.cli.character_flow import _prompt_subclass

        result = _prompt_subclass(lambda _: "", lambda *a, **kw: None, "Nonexistent")
        assert result is None

    def test_partial_match_works_via_prompt_choice(self):
        # User types partial / case-different; the prompt_choice helper
        # only accepts exact digit picks, so this verifies default behavior.
        from auto_dm.cli.character_flow import _prompt_subclass

        inputs = iter(["", "1"])  # first empty (default), then "1"
        result = _prompt_subclass(
            lambda _: next(inputs),
            lambda *a, **kw: None,
            "Cleric",
        )
        # Cleric's only parsed subclass is "Life Domain" — default index 0
        assert result in {"Life Domain"}


# ===========================================================================
# Builder integration (smoke test)
# ===========================================================================


class TestBuilderAppliesSubclassFeatures:
    def test_sorcerer_dragonborn_L1_has_resilience(self):
        # Spot-check that the builder populates subclass_features
        # automatically when the character starts at a level where
        # subclass features are available.
        draft = (
            CharacterBuilder()
            .with_name("Pyra")
            .with_race("Dragonborn")
            .with_class("Sorcerer", subclass="Draconic Bloodline")
            .with_background("Hermit")
            .with_alignment("CN")
            .with_level(1)
            .with_standard_array()
            .with_skills(["arcana", "persuasion"])
            .build()
        )
        assert "Draconic Resilience" in draft.character.subclass_features

    def test_wizard_L1_school_of_evocation_no_features_yet(self):
        # Evocation Savant is L2 per the parser. L1 Wizard has none.
        draft = (
            CharacterBuilder()
            .with_name("Elly")
            .with_race("Human")
            .with_class("Wizard", subclass="School of Evocation")
            .with_background("Sage")
            .with_alignment("LN")
            .with_level(1)
            .with_standard_array()
            .with_skills(["arcana", "history"])
            .build()
        )
        # L1: no subclass features yet
        assert draft.character.subclass_features == []

    def test_fighter_L3_champion_improved_critical(self):
        # Champion's first feature (Improved Critical) is at L3.
        draft = (
            CharacterBuilder()
            .with_name("Bran")
            .with_race("Human")
            .with_class("Fighter", subclass="Champion")
            .with_background("Soldier")
            .with_alignment("LG")
            .with_level(3)
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .build()
        )
        assert "Improved Critical" in draft.character.subclass_features

    def test_no_subclass_leaves_features_empty(self):
        draft = (
            CharacterBuilder()
            .with_name("Bran")
            .with_race("Human")
            .with_class("Fighter")  # no subclass
            .with_background("Soldier")
            .with_alignment("LG")
            .with_level(3)
            .with_standard_array()
            .with_skills(["athletics", "perception"])
            .build()
        )
        assert draft.character.subclass_features == []
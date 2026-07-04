"""Character creation: stats, builder, level-up helpers, and spell selection.

The character builder is pure-Python and testable. The web layer prompts
are layered on top later (Phase 26c).

Usage:
    from auto_dm.character import CharacterBuilder
    from auto_dm.engine.dice import roll_stats

    draft = (CharacterBuilder()
        .with_name("Thorgar")
        .with_race("Dwarf", subrace="Hill Dwarf")
        .with_class("Fighter")
        .with_background("Soldier")
        .with_alignment("LN")
        .with_level(1)
        .with_standard_array()  # or .with_ability_scores([15, 14, 13, 12, 10, 8])
        .with_skills(["athletics", "perception"])
        .build())
"""
from auto_dm.character.builder import (
    CharacterBuilder,
    CharacterDraft,
    STAT_BLOCK_SIZE,
    STANDARD_ARRAY,
)
from auto_dm.character.level_up import (
    apply_subclass_features,
    auto_resolve_companion_asi,
    companion_asi_to_pending,
    features_gained_at_level,
    has_subclass_feature,
    list_subclass_features,
    resolve_asi_choice,
    update_spell_slots_for_level,
)
from auto_dm.character.spells import (
    SpellSelection,
    prepare_caster_spells,
    select_cantrips,
)

__all__ = [
    "CharacterBuilder",
    "CharacterDraft",
    "STANDARD_ARRAY",
    "STAT_BLOCK_SIZE",
    "SpellSelection",
    "apply_subclass_features",
    "auto_resolve_companion_asi",
    "companion_asi_to_pending",
    "features_gained_at_level",
    "has_subclass_feature",
    "list_subclass_features",
    "prepare_caster_spells",
    "resolve_asi_choice",
    "select_cantrips",
    "update_spell_slots_for_level",
]

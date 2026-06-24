"""Game state models and manager.

Pydantic-based state representation. The rules engine in `engine/` mutates
state through `StateManager`, never by reaching into models directly. This
keeps every state transition validated and traceable.
"""
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    Ability,
    AbilityScores,
    Action,
    ActionResult,
    ActionType,
    ArmorProperties,
    Character,
    Condition,
    EquippedSlots,
    GameState,
    Item,
    ItemType,
    NarrativeEntry,
    NPC,
    Proficiencies,
    Quest,
    QuestObjective,
    Skill,
    SpellLevel,
    Spellcasting,
    WeaponProperties,
)

__all__ = [
    "Ability",
    "AbilityScores",
    "Action",
    "ActionResult",
    "ActionType",
    "ArmorProperties",
    "Character",
    "Condition",
    "EquippedSlots",
    "GameState",
    "Item",
    "ItemType",
    "NPC",
    "NarrativeEntry",
    "Proficiencies",
    "Quest",
    "QuestObjective",
    "Skill",
    "SpellLevel",
    "Spellcasting",
    "StateManager",
    "WeaponProperties",
]

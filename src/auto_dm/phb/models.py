"""Pydantic models for Player's Handbook content.

These models represent the structured data extracted from the PHB .md files.
They are designed to be:
- Looked up by name (case-insensitive)
- Serialized to JSON for caching
- Used by character creation and the rules engine
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from auto_dm.state.models import Ability


# ============================================================================
# Enums
# ============================================================================


class PHBSpellSchool(str, Enum):
    """The eight schools of magic."""

    ABJURATION = "abjuration"
    CONJURATION = "conjuration"
    DIVINATION = "divination"
    ENCHANTMENT = "enchantment"
    EVOCATION = "evocation"
    ILLUSION = "illusion"
    NECROMANCY = "necromancy"
    TRANSMUTATION = "transmutation"


class PHBSpellComponent(str, Enum):
    VERBAL = "V"
    SOMATIC = "S"
    MATERIAL = "M"


class PHBWeaponCategory(str, Enum):
    SIMPLE_MELEE = "simple_melee"
    SIMPLE_RANGED = "simple_ranged"
    MARTIAL_MELEE = "martial_melee"
    MARTIAL_RANGED = "martial_ranged"


class PHBArmorCategory(str, Enum):
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"
    SHIELD = "shield"


# ============================================================================
# Shared building blocks
# ============================================================================


class Trait(BaseModel):
    """A named feature on a race, subrace, class, or subclass.

    Examples: 'Darkvision', 'Rage', 'Dwarven Resilience', 'Frenzy'.
    """

    name: str
    description: str


class AbilityBonus(BaseModel):
    """An ability score adjustment from a race or subrace."""

    ability: Ability
    bonus: int


# ============================================================================
# Races
# ============================================================================


class Subrace(BaseModel):
    """A subrace variant (e.g. Hill Dwarf, High Elf)."""

    name: str
    parent_race: str
    description: str = ""
    ability_bonuses: list[AbilityBonus] = Field(default_factory=list)
    traits: list[Trait] = Field(default_factory=list)
    speed: Optional[int] = None  # overrides base if set


class Race(BaseModel):
    """A playable race (Dwarf, Elf, Human, etc.)."""

    name: str
    description: str = ""
    size: str = "Medium"  # PHB races are Medium or Small
    speed: int = 30
    ability_bonuses: list[AbilityBonus] = Field(default_factory=list)
    traits: list[Trait] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    subraces: list[Subrace] = Field(default_factory=list)


# ============================================================================
# Classes
# ============================================================================


class ClassProficiency(BaseModel):
    """A proficiency category granted by a class."""

    armor: list[str] = Field(default_factory=list)  # "Light armor", "Medium armor", "Shields"
    weapons: list[str] = Field(default_factory=list)  # "Simple weapons", "Martial weapons"
    tools: list[str] = Field(default_factory=list)
    saving_throws: list[Ability] = Field(default_factory=list)
    skills_choices: str = ""  # raw text like "Choose two from Animal Handling, ..."
    num_skill_choices: int = 0  # parsed number, 0 if "any"


class ClassFeature(BaseModel):
    """A class feature at a given level (e.g. Rage at 1, ASI at 4)."""

    name: str
    description: str
    level: int  # 1-20


class Subclass(BaseModel):
    """A subclass option (Path of the Berserker, School of Evocation)."""

    name: str
    parent_class: str
    description: str = ""
    features: list[ClassFeature] = Field(default_factory=list)


class SpellcastingInfo(BaseModel):
    """Spellcasting details for a class."""

    ability: Ability
    description: str = ""
    # At higher levels table is complex — we capture as raw text for now.
    # Phase 11 may parse it into structured slot counts.
    slots_table_text: str = ""


class CharacterClass(BaseModel):
    """A playable class (Barbarian, Wizard, etc.)."""

    name: str
    description: str = ""
    hit_dice: str  # "1d12"
    hit_points_at_1st: str  # "12 + your Constitution modifier"
    proficiencies: ClassProficiency = Field(default_factory=ClassProficiency)
    starting_equipment: list[str] = Field(default_factory=list)  # raw bullet text
    features: list[ClassFeature] = Field(default_factory=list)
    subclasses: list[Subclass] = Field(default_factory=list)
    spellcasting: Optional[SpellcastingInfo] = None


# ============================================================================
# Equipment — weapons and armor
# ============================================================================


class PHBWeapon(BaseModel):
    """A weapon from the PHB Weapons table."""

    name: str
    category: PHBWeaponCategory
    cost_gp: float  # 0.01 for 1cp, 0.1 for 1sp, 1.0 for 1gp
    damage_dice: str  # "1d8"
    damage_type: str  # "slashing"
    weight: float  # pounds (0 = "-" in table)

    # Parsed boolean properties
    finesse: bool = False
    light: bool = False
    heavy: bool = False
    reach: bool = False
    thrown: bool = False
    two_handed: bool = False
    ammunition: bool = False
    loading: bool = False
    special: bool = False

    versatile_dice: Optional[str] = None  # "1d10" for longsword 2H
    range_normal: Optional[int] = None
    range_long: Optional[int] = None

    # Raw properties text (preserved for display)
    properties_text: str = ""


class PHBArmor(BaseModel):
    """An armor piece or shield from the PHB Armor table."""

    name: str
    category: PHBArmorCategory
    cost_gp: float
    base_ac: int
    add_dex: bool
    max_dex: Optional[int] = None  # 2 for medium armor
    stealth_disadvantage: bool = False
    strength_required: Optional[int] = None
    weight: float
    is_shield: bool = False


# ============================================================================
# Spells
# ============================================================================


class PHBSpell(BaseModel):
    """A spell from the PHB."""

    name: str
    level: int  # 0 = cantrip, 1-9 = spell level
    school: PHBSpellSchool
    casting_time: str  # "1 action", "1 bonus action", etc.
    range_text: str  # "60 feet", "Self", "Touch"
    components: list[PHBSpellComponent] = Field(default_factory=list)
    material: Optional[str] = None  # the parenthetical material description
    duration: str  # "Instantaneous", "Concentration, up to 1 minute", etc.
    description: str
    higher_levels: Optional[str] = None  # the "At Higher Levels" block, if any
    classes: list[str] = Field(default_factory=list)  # populated from Spell Lists
    is_ritual: bool = False
    is_concentration: bool = False

    @property
    def is_cantrip(self) -> bool:
        return self.level == 0


# ============================================================================
# Conditions
# ============================================================================


class PHBCondition(BaseModel):
    """A condition from the PHB Conditions appendix."""

    name: str
    description: str  # the bullet list joined as text
    effects: list[str] = Field(default_factory=list)  # individual bullets
    # Exhaustion is special: it has levels
    has_levels: bool = False


class PHBLanguage(BaseModel):
    """A language from the PHB Languages appendix.

    PHB has two categories:
    - ``standard`` — the 8 "everyday" languages (Common, Dwarvish, ...).
    - ``exotic`` — the 8 rare languages (Abyssal, Draconic, ...).
    """

    name: str
    category: str  # "standard" or "exotic"
    typical_speakers: str = ""
    script: str = ""


# ============================================================================
# Poisons, Traps, Diseases — Gamemastering content
# ============================================================================


class PHBPoison(BaseModel):
    """A poison from the PHB.

    PHB has 4 delivery types: contact, ingested, inhaled, injury.
    """

    name: str
    delivery: str  # "contact" | "ingested" | "inhaled" | "injury"
    price_gp: float = 0.0
    save_dc: int = 10
    save_ability: str = "constitution"
    # Damage on failed save (empty if no damage, just condition).
    damage_dice: str = ""  # e.g. "1d12", "3d6", "7d6"
    damage_type: str = "poison"
    # Damage on successful save: usually half (set by engine).
    # Duration the poison condition lasts on failed save.
    duration: str = ""  # "24 hours", "1 minute", "instant", etc.
    # What happens on failed save beyond damage.
    applies_condition: list[str] = Field(default_factory=list)
    # Free-text for unusual rules (e.g. "wake up if takes damage").
    notes: str = ""


class PHBTrap(BaseModel):
    """A trap from the PHB Traps appendix.

    Traps have a detection DC, a disarm DC, and a triggering effect
    (save DC + damage). Complex traps (Rolling Sphere) involve more
    state — we capture the basics here.
    """

    name: str
    trap_type: str  # "mechanical" or "magic"
    detect_dc: int = 10
    disarm_dc: int = 15
    save_dc: int = 10
    save_ability: str = "dexterity"
    damage_dice: str = ""  # e.g. "4d10"
    damage_type: str = "bludgeoning"
    description: str = ""


class PHBDisease(BaseModel):
    """A disease from the PHB Diseases appendix."""

    name: str
    description: str = ""
    save_dc: int = 10
    save_ability: str = "constitution"
    # The disease's effects when the save fails: damage, exhaustion, etc.
    effects_on_fail: list[str] = Field(default_factory=list)
    incubation: str = ""  # "1d4 hours", "1d4 days"

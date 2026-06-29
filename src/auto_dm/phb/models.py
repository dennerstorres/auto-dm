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

from auto_dm.state.models import Ability, AbilityScores


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


# ============================================================================
# Monsters — D&D 5e Monster Manual stat blocks (319 files)
# ============================================================================


class MonsterType(str, Enum):
    """The 14 official D&D 5e creature types."""

    ABERRATION = "aberration"
    BEAST = "beast"
    CELESTIAL = "celestial"
    CONSTRUCT = "construct"
    DRAGON = "dragon"
    ELEMENTAL = "elemental"
    FEY = "fey"
    FIEND = "fiend"
    GIANT = "giant"
    HUMANOID = "humanoid"
    MONSTROSITY = "monstrosity"
    OOZE = "ooze"
    PLANT = "plant"
    UNDEAD = "undead"


class MonsterSize(str, Enum):
    """The 6 official D&D 5e creature sizes."""

    TINY = "Tiny"
    SMALL = "Small"
    MEDIUM = "Medium"
    LARGE = "Large"
    HUGE = "Huge"
    GARGANTUAN = "Gargantuan"


class MonsterTrait(BaseModel):
    """A monster trait (``***Name***``. description)."""

    name: str
    description: str


class MonsterAction(BaseModel):
    """An action available to a monster.

    Examples: a melee attack (``***Bite***``), a multiattack, a breath weapon,
    a paralyzing touch. Limited-usage notation (Recharge / X/Day / Costs N Actions)
    is parsed from the action name's parenthetical.
    """

    name: str
    description: str

    # Limited-usage notation parsed from the action name suffix.
    recharge: Optional[str] = None  # "5-6" (Recharge 5-6)
    usages: Optional[str] = None  # "3/Day"
    cost: Optional[int] = None  # N for "Costs N Actions" (legendary actions)

    # Attack details — None for non-attack actions like Multiattack.
    attack_type: Optional[str] = None  # "melee_weapon" | "ranged_weapon" | "melee_spell" | "ranged_spell"
    attack_bonus: Optional[int] = None  # +4
    reach_ft: Optional[int] = None  # 5
    range_normal_ft: Optional[int] = None  # 80
    range_long_ft: Optional[int] = None  # 320

    # Primary damage (after "Hit: ")
    damage_dice: Optional[str] = None  # "1d6+2"
    damage_type: Optional[str] = None  # "slashing"

    # Additional rider damage (e.g. "plus 7 (2d6) fire damage")
    additional_damage_dice: Optional[str] = None  # "2d6"
    additional_damage_type: Optional[str] = None  # "fire"


class Monster(BaseModel):
    """A complete monster stat block parsed from ``data/phb/Monsters/*.md``.

    This is the PHB-side model. To use a monster in combat, convert it via
    ``monster_to_npc`` (in ``state/monster_adapter.py``) into a state ``NPC``.
    """

    name: str
    size: MonsterSize
    type: MonsterType
    # Parenthesised type tag, e.g. "goblinoid" from "humanoid (goblinoid)".
    subtype: Optional[str] = None
    # Raw alignment string: "chaotic evil", "any evil alignment", "unaligned".
    alignment: str

    # Defense
    armor_class: int
    armor_description: str = ""  # "(natural armor)", "(leather armor, shield)"

    # Hit points
    hp_average: int
    hp_dice_formula: str  # "2d6", "19d12+133"

    # Speed (per movement mode; walk defaults to 0 if absent)
    speed_walk: int = 0
    speed_burrow: int = 0
    speed_climb: int = 0
    speed_fly: int = 0
    speed_swim: int = 0
    hover: bool = False  # parenthetical "(hover)" after fly speed

    # Ability scores
    abilities: AbilityScores

    # Proficiencies (parsed from optional sections)
    saves: dict[str, int] = Field(default_factory=dict)  # {"con": 13}
    skills: dict[str, int] = Field(default_factory=dict)  # {"stealth": 6}

    # Damage modifiers — stored as raw strings (the engine parses qualifiers
    # like "from nonmagical attacks" when applying damage).
    damage_resistances: list[str] = Field(default_factory=list)
    damage_immunities: list[str] = Field(default_factory=list)
    damage_vulnerabilities: list[str] = Field(default_factory=list)
    condition_immunities: list[str] = Field(default_factory=list)  # raw condition names

    # Senses
    senses: dict[str, int] = Field(default_factory=dict)  # {"darkvision": 60, "blindsight": 10}
    passive_perception: int = 0
    languages: list[str] = Field(default_factory=list)
    languages_note: str = ""  # "plus up to five other languages"

    # Challenge
    challenge_rating: float  # 0.25 for "1/4"
    challenge_rating_text: str  # "1/4" or "17"
    xp: int

    # Powers
    traits: list[MonsterTrait] = Field(default_factory=list)
    actions: list[MonsterAction] = Field(default_factory=list)
    reactions: list[MonsterAction] = Field(default_factory=list)
    legendary_actions: list[MonsterAction] = Field(default_factory=list)
    legendary_actions_count: int = 0  # "can take 3 legendary actions"
    legendary_resistances: int = 0  # "Legendary Resistance (3/Day)"

    # Debug
    source_file: str = ""  # relative path of the .md file this came from


# ============================================================================
# Backgrounds — D&D 5e character backgrounds (Phase 25c)
# ============================================================================


class Background(BaseModel):
    """A character background (Acolyte, Folk Hero, Criminal, etc.).

    Backgrounds grant narrative features plus mechanical benefits:
    skill proficiencies, tool proficiencies, languages, and starting
    equipment. The mechanical pieces are parsed individually so the
    CharacterBuilder can wire them in; the narrative pieces (description,
    feature prose, suggested characteristics) are preserved for the DM's
    narration.
    """

    name: str

    # Narrative
    description: str = ""  # 1-3 paragraphs about the background's flavor
    feature_name: str = ""  # "Shelter of the Faithful", "Shady Contacts", ...
    feature_description: str = ""  # the prose body of that feature
    suggested_characteristics: list[str] = Field(default_factory=list)

    # Mechanical — parsed for builder integration
    skill_proficiencies: list[str] = Field(default_factory=list)
    # Free-text skill names (e.g. "Insight", "Religion"). The builder
    # maps these through ``parse_skill_name`` from ``character.builder``.
    tool_proficiencies: list[str] = Field(default_factory=list)
    # Free-text tool names (e.g. "Thieves' tools", "Disguise kit").
    languages: str = ""
    # Either "Two of your choice" / "Any one of your choice" (deferred to
    # the wizard), or a specific comma-separated list. Kept as text so
    # the choice rule survives.
    equipment: str = ""
    # Raw text of the "**Equipment:** ..." line. The builder stores it
    # on the Character but does not (yet) populate inventory from it.


# ============================================================================
# Tools — D&D 5e tool proficiencies (Phase 25c)
# ============================================================================


class ToolCategory(str, Enum):
    """PHB tool category groupings."""

    ARTISAN = "artisan"  # Alchemist's supplies, Carpenter's tools, ...
    GAMING_SET = "gaming_set"  # Dice set, Playing card set
    MUSICAL_INSTRUMENT = "musical_instrument"  # Bagpipes, Lute, ...
    KIT = "kit"  # Disguise kit, Forgery kit, Herbalism kit, ...
    VEHICLE = "vehicle"  # Vehicles (land or water) — placeholder row
    OTHER = "other"


class PHBTool(BaseModel):
    """A tool from the PHB Tools table.

    Tools come in a few shapes — 16 artisan's tools under "Artisan's
    tools", 2 gaming sets, 10 musical instruments, and standalone kits
    (Disguise, Forgery, Herbalism, Navigator's, Poisoner's, Thieves').
    All share cost, weight, and a prose description.
    """

    name: str
    category: ToolCategory
    cost_gp: float = 0.0
    weight: float = 0.0
    description: str = ""


# ============================================================================
# Adventuring Gear (Phase 25c)
# ============================================================================


class GearCategory(str, Enum):
    """PHB Adventuring Gear table groupings."""

    AMMUNITION = "ammunition"
    ARCANE_FOCUS = "arcane_focus"
    DRUIDIC_FOCUS = "druidic_focus"
    HOLY_SYMBOL = "holy_symbol"
    STANDARD = "standard"  # default catch-all


class PHBGear(BaseModel):
    """An item from the PHB Adventuring Gear section.

    Combines the prose block (``***Acid***. ...``) at the top of
    ``Equipment/Gear.md`` with the ``**Table- Adventuring Gear**`` rows
    below it. Items with table rows use the structured cost/weight;
    prose-only items default to ``cost_gp=0``, ``weight=0``.
    """

    name: str
    category: GearCategory = GearCategory.STANDARD
    cost_gp: float = 0.0
    weight: float = 0.0
    description: str = ""


class PHBEquipmentPack(BaseModel):
    """A starting equipment pack from the PHB.

    Examples: Burglar's Pack (16 gp), Explorer's Pack (10 gp),
    Dungeoneer's Pack (12 gp). The ``contents`` list is parsed from the
    prose after ``Includes ...`` — best-effort; the original PHB text is
    also kept in ``description`` so the player can read it verbatim.
    """

    name: str
    cost_gp: float
    contents: list[str] = Field(default_factory=list)
    description: str = ""


# ============================================================================
# Magic Items (Phase 25d)
# ============================================================================


class Rarity(str, Enum):
    """The 5 standard magic item rarity tiers, plus artifact.

    Per DMG p. 135: rarity drives attunement, value, and which tables
    the item appears on when rolling loot.
    """

    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    VERY_RARE = "very_rare"
    LEGENDARY = "legendary"
    ARTIFACT = "artifact"


class MagicItemType(str, Enum):
    """The 9 broad categories of magic items (DMG p. 138)."""

    WEAPON = "weapon"
    ARMOR = "armor"
    SHIELD = "shield"
    POTION = "potion"
    RING = "ring"
    ROD = "rod"
    SCROLL = "scroll"
    STAFF = "staff"
    WAND = "wand"
    WONDROUS = "wondrous"


class MagicItem(BaseModel):
    """A magic item from the PHB/DMG.

    One item per file in ``data/phb/Treasure/``. The tagline
    ``*Type, rarity (requires attunement)*`` is parsed by the loader
    and stored as structured fields. Generic +1/+2/+3 weapons/armor
    store the bonus implicitly via rarity (uncommon=+1, rare=+2,
    very_rare=+3); ``magic_bonus`` is set by the engine based on
    rarity at equip time.

    Note: many items (Bag of Holding, Ring of Protection, etc.) carry
    their effects in prose. Phase 25d wires only the simplest cases
    (magic weapon bonus to attack/damage, magic armor bonus to AC,
    Ring of Protection +1 to saves). The rest of the descriptions
    are surfaced for the DM to narrate.
    """

    name: str
    item_type: MagicItemType
    rarity: Rarity

    # Attunement. Empty string if the item doesn't require attunement.
    attunement_requirement: str = ""

    # The full tagline (preserved for display, e.g.
    # "Weapon (any sword), legendary (requires attunement by a paladin)").
    tagline: str = ""

    # Body prose (multi-paragraph).
    description: str = ""

    # Source file (debug / round-tripping).
    source_file: str = ""

    @property
    def requires_attunement(self) -> bool:
        """True if this item requires attunement to use its magic."""
        return bool(self.attunement_requirement)

    @property
    def magic_bonus(self) -> int:
        """For +1/+2/+3 weapons, armor, shields, and ammunition:
        the magic bonus implied by rarity.

        - uncommon = +1
        - rare = +2
        - very_rare = +3
        - other rarities = 0
        """
        if self.item_type not in {
            MagicItemType.WEAPON, MagicItemType.ARMOR,
            MagicItemType.SHIELD,
        }:
            return 0
        return {
            Rarity.UNCOMMON: 1,
            Rarity.RARE: 2,
            Rarity.VERY_RARE: 3,
        }.get(self.rarity, 0)


# ============================================================================
# Mounts and Vehicles (Phase 25e)
# ============================================================================


class VehicleType(str, Enum):
    """PHB vehicle categories from the Transportation tables.

    - LAND: carriages, carts, chariots, sleds, wagons
    - WATER: galleys, keelboats, longships, rowboats, sailing ships, warships
    """

    LAND = "land"
    WATER = "water"


class Mount(BaseModel):
    """A mount from the PHB "Mounts and Other Animals" table.

    Examples: Camel (50 ft., 480 lb.), Donkey/mule (40 ft., 420 lb.),
    Horse, riding (60 ft., 480 lb.), Warhorse (60 ft., 540 lb.).
    """

    name: str
    cost_gp: float
    speed_ft: int
    carrying_capacity_lb: int
    # Default to Medium — the PHB table doesn't list sizes, but the
    # engine can size up Warhorses as Large if needed.
    size: str = "Medium"


class Vehicle(BaseModel):
    """A vehicle from the PHB "Tack, Harness, and Drawn Vehicles"
    or "Waterborne Vehicles" tables.

    Land vehicles (Carriage, Cart, Chariot, Sled, Wagon) have weight
    but no inherent speed — they're drawn by animals. Water vehicles
    (Galley, Keelboat, Longship, Rowboat, Sailing ship, Warship)
    carry a mph speed and a Crew/passenger capacity.
    """

    name: str
    vehicle_type: VehicleType
    cost_gp: float
    weight_lb: float = 0.0
    # Speed in mph for waterborne; 0 for land vehicles (they move at
    # the pulling animal's speed, capped by terrain).
    speed_mph: float = 0.0
    # Crew / passenger capacity — meaningful only for waterborne.
    capacity: int = 0
    # Multiplier entries on the Tack table (e.g. "Barding: ×4 / ×2")
    # are stored as descriptive text on the item's row, not as numbers.
    notes: str = ""

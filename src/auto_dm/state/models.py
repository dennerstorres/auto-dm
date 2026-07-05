"""Pydantic models for the entire game state.

Everything the engine and the LLM agents read or write lives here. Models
are designed to serialize cleanly to JSON for save/load, and to roundtrip
without loss via `model_validate(json)`.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# Abilities and skills
# ============================================================================


class Ability(str, Enum):
    """The six D&D ability scores."""

    STR = "strength"
    DEX = "dexterity"
    CON = "constitution"
    INT = "intelligence"
    WIS = "wisdom"
    CHA = "charisma"


class Skill(str, Enum):
    """The 18 D&D 5e skills."""

    ACROBATICS = "acrobatics"
    ANIMAL_HANDLING = "animal_handling"
    ARCANA = "arcana"
    ATHLETICS = "athletics"
    DECEPTION = "deception"
    HISTORY = "history"
    INSIGHT = "insight"
    INTIMIDATION = "intimidation"
    INVESTIGATION = "investigation"
    MEDICINE = "medicine"
    NATURE = "nature"
    PERCEPTION = "perception"
    PERFORMANCE = "performance"
    PERSUASION = "persuasion"
    RELIGION = "religion"
    SLEIGHT_OF_HAND = "sleight_of_hand"
    STEALTH = "stealth"
    SURVIVAL = "survival"


class AbilityScores(BaseModel):
    """The six raw ability scores, with helpers to compute modifiers."""

    strength: int
    dexterity: int
    constitution: int
    intelligence: int
    wisdom: int
    charisma: int

    def modifier(self, ability: Ability) -> int:
        """PHB standard: (score - 10) // 2."""
        return (getattr(self, ability.value) - 10) // 2

    @classmethod
    def all_seven(cls) -> "AbilityScores":
        """Factory for tests — common 13-ish statline used as default."""
        return cls(strength=13, dexterity=12, constitution=13, intelligence=10, wisdom=12, charisma=10)


class Proficiencies(BaseModel):
    """Saving throws, skills, tools, languages a character is proficient in."""

    saves: list[Ability] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


# ============================================================================
# Equipment
# ============================================================================


class ItemType(str, Enum):
    WEAPON = "weapon"
    ARMOR = "armor"
    SHIELD = "shield"
    CONSUMABLE = "consumable"
    TOOL = "tool"
    TREASURE = "treasure"
    MISC = "misc"


class WeaponProperties(BaseModel):
    damage_dice: str  # e.g. "1d8"
    damage_type: str  # e.g. "slashing"
    finesse: bool = False
    heavy: bool = False
    light: bool = False
    reach: bool = False
    thrown: bool = False
    two_handed: bool = False
    versatile_dice: Optional[str] = None  # e.g. "1d10" for longsword 2H
    range_normal: Optional[int] = None
    range_long: Optional[int] = None
    ammunition: bool = False  # for bows, crossbows
    loading: bool = False


class ArmorProperties(BaseModel):
    base_ac: int
    add_dex_modifier: bool = True
    max_dex_bonus: Optional[int] = None  # 2 for medium armor
    stealth_disadvantage: bool = False
    strength_required: Optional[int] = None  # 15 for plate, 13 for chain mail
    is_shield: bool = False


class Item(BaseModel):
    name: str
    type: ItemType = ItemType.MISC
    weight: float = 0.0
    value_gp: float = 0.0
    description: str = ""
    quantity: int = 1
    weapon: Optional[WeaponProperties] = None
    armor: Optional[ArmorProperties] = None
    # Magic item fields (Phase 25d). Default to mundane.
    magic_bonus: Optional[int] = None  # +1/+2/+3 for magic weapons/armor
    requires_attunement: bool = False
    rarity: Optional[str] = None  # "common"/"uncommon"/"rare"/"very_rare"/"legendary"


class EquippedSlots(BaseModel):
    """What the character is currently wearing/wielding."""

    main_hand: Optional[Item] = None
    off_hand: Optional[Item] = None  # weapon (2WF) or shield
    armor: Optional[Item] = None
    amulet: Optional[Item] = None
    ring_1: Optional[Item] = None
    ring_2: Optional[Item] = None
    cloak: Optional[Item] = None
    boots: Optional[Item] = None


class ShopItem(BaseModel):
    """One line of a vendor NPC's stock (Phase 39).

    ``item_id`` is the catalog name resolved against the PHB tables
    (weapons, armor, gear, magic items) by ``engine/inventory.py``.
    """

    item_id: str
    price_gp: float
    restock_daily: bool = False


# ============================================================================
# Spellcasting
# ============================================================================


class SpellLevel(int, Enum):
    CANTRIP = 0
    LEVEL_1 = 1
    LEVEL_2 = 2
    LEVEL_3 = 3
    LEVEL_4 = 4
    LEVEL_5 = 5
    LEVEL_6 = 6
    LEVEL_7 = 7
    LEVEL_8 = 8
    LEVEL_9 = 9


class Spellcasting(BaseModel):
    ability: Ability  # casting ability (INT, WIS, or CHA depending on class)
    save_dc: int = 0
    attack_bonus: int = 0
    cantrips_known: list[str] = Field(default_factory=list)
    spells_known: list[str] = Field(default_factory=list)
    spells_prepared: list[str] = Field(default_factory=list)
    spellbook: list[str] = Field(default_factory=list)
    spell_slots: dict[int, int] = Field(default_factory=dict)  # level -> remaining
    spell_slots_max: dict[int, int] = Field(default_factory=dict)  # level -> max
    concentration: Optional[str] = None  # spell name if currently concentrating
    ritual_casting: bool = False


# ============================================================================
# Conditions (the 13 official PHB conditions)
# ============================================================================


class Condition(str, Enum):
    BLINDED = "blinded"
    CHARMED = "charmed"
    DEAFENED = "deafened"
    FRIGHTENED = "frightened"
    GRAPPLED = "grappled"
    INCAPACITATED = "incapacitated"
    INVISIBLE = "invisible"
    PARALYZED = "paralyzed"
    PETRIFIED = "petrified"
    POISONED = "poisoned"
    PRONE = "prone"
    RESTRAINED = "restrained"
    STUNNED = "stunned"
    UNCONSCIOUS = "unconscious"
    # Engine-managed "tactical" states (not in PHB conditions table, but
    # useful for tracking per-turn state cleanly).
    DODGING = "dodging"  # used this turn's action on Dodge
    HIDDEN = "hidden"  # successfully used Hide this round


# ============================================================================
# Active effects (poisons, traps, diseases)
# ============================================================================


class ActiveEffect(BaseModel):
    """An ongoing effect on a creature — poison, disease, trap.

    PHB effects have:
    - A source name (poison name, disease name)
    - A duration (in rounds; 0 = one-shot)
    - A save DC and ability to end the effect early
    - A damage expression (dice notation)
    - A list of conditions the effect applies
    """

    source: str
    effect_type: str  # "poison" | "disease" | "trap"
    duration_rounds: int = 0  # 0 = one-shot
    save_dc: int = 10
    save_ability: Ability = Ability.CON
    damage_dice: str = ""  # e.g. "3d6"
    damage_type: str = "poison"
    applies_condition: list[Condition] = Field(default_factory=list)
    notes: str = ""


# ============================================================================
# Characters
# ============================================================================


# ============================================================================
# Characters
# ============================================================================


class Character(BaseModel):
    """A full character sheet — player or AI companion."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    name: str
    race: str
    subrace: Optional[str] = None
    class_: str = Field(alias="class")
    subclass: Optional[str] = None
    # Names of subclass features the character has gained so far (PHB p. ?).
    # Populated by ``character.level_up.apply_subclass_features`` when the
    # character is built or levels up. Order is by acquisition.
    subclass_features: list[str] = Field(default_factory=list)
    level: int
    background: str
    alignment: str

    abilities: AbilityScores

    # Combat
    hp_current: int
    hp_max: int
    temp_hp: int = 0
    armor_class: int
    speed: int
    proficiency_bonus: int
    hit_dice: str  # e.g. "1d10"
    hit_dice_remaining: int
    inspiration: bool = False
    # Stacks of "pending advantage" — Inspiration grants one, the Help
    # action grants one, etc. The engine consumes one per d20 roll.
    pending_advantage: int = 0
    # AC bonus to apply on the next incoming attack (e.g. Shield spell).
    # Cleared by the engine at end of attacker's turn.
    pending_ac_bonus: int = 0

    # Death saves (only relevant when hp_current == 0)
    death_save_successes: int = 0
    death_save_failures: int = 0

    proficiencies: Proficiencies = Field(default_factory=Proficiencies)

    # Equipment
    inventory: list[Item] = Field(default_factory=list)
    equipped: EquippedSlots = Field(default_factory=EquippedSlots)
    # Phase 39 — gold + attunement. Gold is a float because PHB prices
    # go below 1 gp (1 sp = 0.1 gp). Attuned items are stored by item
    # name; the PHB p. 138 cap of 3 is enforced by engine/inventory.py.
    gold_gp: float = 0.0
    attuned_items: list[str] = Field(default_factory=list)

    # Magic
    spellcasting: Optional[Spellcasting] = None

    # Active effects (poisons, diseases, ongoing traps)
    active_effects: list[ActiveEffect] = Field(default_factory=list)

    # Status
    conditions: list[Condition] = Field(default_factory=list)
    # Exhaustion is a PHB condition with 6 levels — track level separately.
    # 0 = not exhausted, 1-6 = level of exhaustion (6 = death).
    exhaustion_level: int = 0
    # Cover against incoming attacks (DMG p. 250):
    # "none" | "half" (+2 AC / DEX save) | "three_quarters" (+5) | "total" (untargetable).
    cover: str = "none"
    # Damage modifiers from race (e.g. Dwarf poison resistance), items, etc.
    # Stored as lowercase damage-type strings: "fire", "cold", "slashing"...
    resistances: list[str] = Field(default_factory=list)
    vulnerabilities: list[str] = Field(default_factory=list)
    immunities: list[str] = Field(default_factory=list)

    # Roleplay
    personality_traits: list[str] = Field(default_factory=list)
    ideals: list[str] = Field(default_factory=list)
    bonds: list[str] = Field(default_factory=list)
    flaws: list[str] = Field(default_factory=list)

    # Class resources
    # Barbarian's Rage (PHB p. 48): bonus action, lasts 1 minute, ends
    # early if no attack landed or no damage taken since last turn, or
    # if the barbarian is incapacitated. ``rages_used`` is reset on
    # long rest via the class table in `engine/rage.py`.
    is_raging: bool = False
    rages_used: int = 0
    rages_max: int = 0  # populated by builder for barbarian class
    rounds_raging: int = 0  # tracks duration; 10 rounds = 1 minute

    # Rogue's Sneak Attack (PHB p. 96): once per turn, +Xd6 on a hit
    # with advantage OR with an ally adjacent to the target, using
    # a finesse or ranged weapon. X = 1 + ((level - 1) // 2), max 5d6.
    sneak_attack_used_this_turn: bool = False

    # Monk's Ki (PHB p. 78): resource pool underlying Flurry, Stunning
    # Strike, Patient Defense, Step of the Wind, etc. Recovers on
    # short rest.
    ki_points: int = 0
    ki_max: int = 0

    # Sorcerer's Sorcery Points (PHB p. 101): resource for metamagic
    # and slot↔point conversion. Recovers on long rest.
    sorcery_points: int = 0
    sorcery_points_max: int = 0

    # Paladin's Lay on Hands (PHB p. 84): healing pool that refills on
    # long rest. 5 HP per paladin level.
    lay_on_hands_pool: int = 0

    # Fighter's Second Wind (PHB p. 72): bonus action heal, 1/short
    # rest (or 2 at L17).
    second_wind_used: bool = False

    # Fighter's Action Surge (PHB p. 72): extra action, 1/short rest
    # (2 at L17). ``action_surge_uses_this_turn`` toggles during
    # combat; ``action_surges_remaining`` decrements.
    action_surges_remaining: int = 0

    # Barbarian's Reckless Attack (PHB p. 48): opt-in flag that
    # grants advantage on STR melee but also gives advantage against
    # you. Cleared at the start of your next turn.
    is_reckless: bool = False

    # Fighting Style (PHB p. 72, also Paladin p. 84, Ranger p. 91):
    # "archery" | "defense" | "dueling" | "great_weapon_fighting" |
    # "protection" | "two_weapon_fighting". None for no style.
    fighting_style: Optional[str] = None

    # Extra Attack (PHB L5): number of attacks beyond the first on
    # Attack action. 0 at L1-4, 1 at L5-10, 2 at L11-17, 3 at L18-20.
    # 0 for non-martial classes by default.
    extra_attacks: int = 0

    # Cunning Action (Rogue L2): bonus action dash/disengage/hide.
    # Tracked as a flag (rogue gains this at L2).
    has_cunning_action: bool = False
    has_uncanny_dodge: bool = False

    # Wild Shape (Druid L2): the active beast form, or None.
    wild_shape_form: Optional[str] = None

    # Channel Divinity uses (Cleric L2+): 1/short rest (2 at L18).
    channel_divinity_remaining: int = 0

    # Unarmored Defense (Barbarian L1, Monk L1): when not wearing
    # armor, AC = 10 + DEX + (CON for Barbarian, WIS for Monk).
    # These flags are auto-set by class but can be cleared if the
    # character equips armor.
    uses_unarmored_defense: bool = False
    unarmored_defense_ability: Optional[Ability] = None  # CON or WIS

    # Evasion (Rogue L7, Monk L7): half damage on DEX save success
    # (instead of none). 0 = no, 1 = full evasion.
    has_evasion: bool = False

    # Metamagic options known (Sorcerer L3+): list of strings like
    # "twinned", "quickened", "heightened", "empowered", "subtle".
    metamagic_known: list[str] = Field(default_factory=list)

    # Paladin's Aura of Protection (PHB L6): +CHA mod to saves for
    # allies within 30 ft (becomes 60 ft at L18). 0 = inactive.
    aura_of_protection_active: bool = False
    has_aura_of_protection: bool = False
    # Paladin's Aura of Courage (PHB L10): self and allies in 10 ft
    # (becomes 30 ft at L18) can't be frightened.
    aura_of_courage_active: bool = False
    # Barbarian's Feral Instinct (PHB L7): advantage on initiative
    # rolls if not surprised; can act on the first round of combat
    # even when surprised.
    has_feral_instinct: bool = False

    # Bardic Inspiration die size (Bard L1): d6 / d8 / d10 / d12.
    # 0 = no bardic inspiration.
    bardic_inspiration_die: int = 0
    bardic_inspiration_uses: int = 0  # reset on short rest (Font L5+)
    bardic_inspiration_max: int = 0  # CHA mod, min 1

    # Danger Sense (Barbarian L2): adv on DEX saves vs. effects
    # the barbarian can see. Set automatically by class.
    has_danger_sense: bool = False

    # Brutal Critical (Barbarian L9+): extra weapon damage die on
    # crit. 0 / 1 / 2 / 3.
    brutal_critical_dice: int = 0

    # Favored Enemy (Ranger L1): list of creature types the ranger
    # has advantage tracking/recall on, and +d4 damage. Empty list
    # means none.
    favored_enemies: list[str] = Field(default_factory=list)

    # Eldritch Invocations (Warlock L2+): list of invocation names.
    eldritch_invocations: list[str] = Field(default_factory=list)

    # Mounted combat (PHB): True while riding a controlled mount. The
    # mount_id points at a creature id (party Character or NPC) that
    # the rider is mounted on. While mounted, melee attacks against
    # creatures within 5 ft of the mount hit the mount instead of the
    # rider (PHB p. 198 — the mount shares the rider's space).
    is_mounted: bool = False
    mount_id: Optional[str] = None

    # ----------------------------------------------------------------
    # Capstones & L17-L20 features (Phase 25g)
    # ----------------------------------------------------------------

    # Wizard L20: Signature Spells. Two spells of 3rd level or lower
    # are always prepared (don't count against the prepared cap) and
    # can each be cast once per short rest without expending a slot.
    # ``signature_spell_names`` are the chosen spells; per-spell uses
    # are tracked in ``signature_spell_uses_remaining`` (0 = spent).
    has_signature_spells: bool = False
    signature_spell_names: list[str] = Field(default_factory=list)
    signature_spell_uses_remaining: dict[str, int] = Field(default_factory=dict)

    # Sorcerer L20: Arcane Apotheosis. Sorcery point cap becomes 20
    # (was 0 at L19). Recover all sorcery points on a short rest.
    has_arcane_apotheosis: bool = False

    # Druid L20: Archdruid. Can use Wild Shape to cast spells.
    has_archdruid: bool = False

    # Monk L20: Perfect Self. When spending 4 ki points, recover all
    # expended ki. ``perfect_self_used`` toggles per short rest.
    has_perfect_self: bool = False
    perfect_self_used: bool = False

    # Ranger L20: Foe Slayer. Add WIS modifier to attack roll AND
    # damage roll against favored enemy, once per turn. Tracked by
    # the foe_slayer_used_this_turn flag (cleared at turn start).
    has_foe_slayer: bool = False
    foe_slayer_used_this_turn: bool = False

    # Rogue L20: Stroke of Luck. Once per short rest, turn a missed
    # attack into a hit, or succeed on a failed ability check. Tracked
    # by stroke_of_luck_uses_remaining (0 = expended).
    has_stroke_of_luck: bool = False
    stroke_of_luck_uses_remaining: int = 0

    # Warlock L20: Eldritch Master. Once per long rest (sunlight), can
    # refuel all expended Pact Magic slots. The flag is the gate; the
    # eldritch_master_used flag tracks per-day consumption.
    has_eldritch_master: bool = False
    eldritch_master_used: bool = False

    # Warlock L11+: Mystic Arcanum. Learns one spell of each level
    # (6th at L11, 7th at L13, 8th at L15, 9th at L17) that can be cast
    # once per long rest without using a slot. ``mystic_arcanum_known``
    # maps slot_level -> spell_name. ``mystic_arcanum_uses`` tracks
    # remaining casts (1 = available, 0 = expended).
    mystic_arcanum_known: dict[int, str] = Field(default_factory=dict)
    mystic_arcanum_uses: dict[int, int] = Field(default_factory=dict)

    # Barbarian L20: Primal Champion. +4 STR/CON (max 24), weapon dmg
    # die roll +2 (counts as +2 to damage).
    has_primal_champion: bool = False

    # Cleric L20: Divine Intervention Improvement. Calling Divine
    # Intervention no longer expends the use. The cleric can call it
    # on each long rest without consuming the daily use.
    has_divine_intervention_improvement: bool = False

    # Capstone side-effect bookkeeping. Phase 38's auto-level loop
    # calls ``apply_class_features`` after every level, so each capstone
    # side effect (Primal Champion +4 STR/CON, Arcane Apotheosis cap
    # raise, etc.) needs a guard flag to stay idempotent across
    # repeated invocations.
    primal_champion_applied: bool = False

    # Meta
    is_player: bool = False  # True only for the human-controlled character

    # Phase 38 — XP/ASI queue. ``pending_asi`` is set by ``level_up``
    # when the new level is in {4, 8, 12, 16, 19} (PHB p. 15) and the
    # player has not yet chosen. Shape:
    #     {"level": int, "choices": ["primary"] | ["primary", "secondary"],
    #      "resolved": bool, "primary": str | None, "secondary": str | None}
    # None means no choice is pending. Companion ASIs are auto-resolved
    # immediately (they never leave a non-None ``pending_asi``).
    pending_asi: Optional[dict] = Field(default=None)


# ============================================================================
# NPCs and creatures
# ============================================================================


class NPC(BaseModel):
    """Simplified stat block for non-party creatures (enemies, friendly NPCs)."""

    id: str
    name: str
    description: str = ""
    hp_current: int
    hp_max: int
    temp_hp: int = 0
    armor_class: int
    speed: int
    abilities: AbilityScores
    equipped: EquippedSlots = Field(default_factory=EquippedSlots)
    conditions: list[Condition] = Field(default_factory=list)
    exhaustion_level: int = 0
    cover: str = "none"
    resistances: list[str] = Field(default_factory=list)
    vulnerabilities: list[str] = Field(default_factory=list)
    immunities: list[str] = Field(default_factory=list)
    # Condition immunities from the source stat block (e.g. a Lich is
    # immune to charmed/exhaustion/frightened/paralyzed/poisoned). Stored
    # as raw strings because "exhaustion" doesn't map to a Condition enum
    # member — it's tracked via ``exhaustion_level`` instead.
    condition_immunities: list[str] = Field(default_factory=list)
    active_effects: list[ActiveEffect] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)  # attack names/descriptions
    is_hostile: bool = True
    challenge_rating: Optional[float] = None
    # Phase 38 — XP awarded when this NPC is defeated. Populated by the
    # ``monster_to_npc`` adapter from the source Monster.xp (PHB CR table).
    # None for friendly/hand-crafted NPCs where the DM doesn't pre-load
    # a defeat reward.
    xp: Optional[int] = None
    # Mount NPC fields (Phase 25e). An NPC can serve as a mount for
    # one rider; ``rider_id`` tracks who is currently mounted on it.
    is_mount: bool = False
    rider_id: Optional[str] = None
    # Vehicle NPC: a vehicle-as-creature is treated as an NPC with
    # vehicle flags. ``is_vehicle`` plus ``vehicle_type`` lets the
    # engine apply vehicle-specific rules (cover, AC, etc.).
    is_vehicle: bool = False
    vehicle_type: Optional[str] = None  # "land" | "water"
    # Vendor NPC (Phase 39): the DM flags an NPC as a merchant and the
    # shop endpoints expose ``shop_inventory`` as its stock.
    vendor: bool = False
    shop_inventory: list[ShopItem] = Field(default_factory=list)


# ============================================================================
# Quests
# ============================================================================


class QuestObjective(BaseModel):
    description: str
    completed: bool = False


class Quest(BaseModel):
    id: str
    name: str
    description: str
    objectives: list[QuestObjective] = Field(default_factory=list)
    completed: bool = False
    rewards: str = ""


# ============================================================================
# Narrative log
# ============================================================================


class NarrativeEntry(BaseModel):
    """A single message in the campaign history, used to feed the LLM."""

    timestamp: datetime
    role: str  # "system" | "dm" | "player" | "companion"
    speaker: str  # display name: "DM", "Thorgar", "Jogador"
    content: str


# ============================================================================
# Actions (the LLM-to-engine interface)
# ============================================================================


class ActionType(str, Enum):
    """The set of actions a character can attempt on their turn.

    These match the PHB actions table. The engine decides which are valid
    given current state (action economy, conditions, resources).
    """

    ATTACK = "attack"
    CAST_SPELL = "cast_spell"
    DASH = "dash"
    DISENGAGE = "disengage"
    DODGE = "dodge"
    HELP = "help"
    HIDE = "hide"
    READY = "ready"
    SEARCH = "search"
    USE_OBJECT = "use_object"
    SHOVE = "shove"
    GRAPPLE = "grapple"
    TWO_WEAPON_ATTACK = "two_weapon_attack"
    OPPORTUNITY_ATTACK = "opportunity_attack"
    MOVE = "move"
    SAY = "say"  # flavor only, no mechanical effect
    SHORT_REST = "short_rest"
    LONG_REST = "long_rest"
    # Engine-only actions (not in the PHB Actions table). Used by
    # CombatEngine to handle the meta-actions the engine itself owns.
    DEATH_SAVE = "death_save"  # roll a death saving throw while at 0 HP
    END_COMBAT = "end_combat"  # force-end the encounter (e.g. flee/surrender)
    RAGE = "rage"  # Barbarian's Rage (bonus action, PHB p. 48)
    # Class feature actions
    SMITE = "smite"  # Paladin's Divine Smite — augment a melee attack
    RECKLESS_ATTACK = "reckless_attack"  # Barbarian
    CUNNING_ACTION = "cunning_action"  # Rogue — bonus action dash/disengage/hide
    SECOND_WIND = "second_wind"  # Fighter
    ACTION_SURGE = "action_surge"  # Fighter
    UNCANNY_DODGE = "uncanny_dodge"  # Rogue reaction
    LAY_ON_HANDS = "lay_on_hands"  # Paladin
    CHANNEL_DIVINITY = "channel_divinity"  # Cleric
    BARDIC_INSPIRATION = "bardic_inspiration"  # Bard
    FLURRY_OF_BLOWS = "flurry_of_blows"  # Monk
    PATIENT_DEFENSE = "patient_defense"  # Monk
    STEP_OF_THE_WIND = "step_of_the_wind"  # Monk
    METAMAGIC = "metamagic"  # Sorcerer
    WILD_SHAPE = "wild_shape"  # Druid
    INDOMITABLE = "indomitable"  # Fighter
    STUNNING_STRIKE = "stunning_strike"  # Monk
    MOUNT = "mount"  # Phase 25e: mount a creature / vehicle
    DISMOUNT = "dismount"  # Phase 25e: dismount from current mount


class Action(BaseModel):
    """A structured action the LLM wants to take.

    The LLM emits this as JSON. The engine validates and executes it.
    """

    actor_id: str
    action_type: ActionType
    target_id: Optional[str] = None
    params: dict = Field(default_factory=dict)
    # e.g. params: {"weapon": "longsword"} or {"spell": "fireball", "slot_level": 3}
    dialogue: Optional[str] = None  # what the character says
    reasoning: Optional[str] = None  # for the LLM's own log/debug


class ActionResult(BaseModel):
    """The outcome of an Action, returned by the engine to the caller."""

    success: bool
    message: str  # human-readable description
    # Mechanical details — e.g. {"damage": 7, "target": "orc_1", "attack_roll": 18, "ac": 13}
    mechanical: dict = Field(default_factory=dict)


# ============================================================================
# Top-level game state
# ============================================================================


class GameState(BaseModel):
    """The complete state of a campaign at a point in time."""

    # Identity
    campaign_name: str
    started_at: datetime
    schema_version: int = 1  # for future migration

    # World
    current_location: str = ""
    time_of_day: str = "morning"
    weather: str = "clear"

    # Party (player + companions)
    party: list[Character]
    player_character_id: str

    # Combat
    in_combat: bool = False
    initiative_order: list[str] = Field(default_factory=list)
    current_turn_index: int = 0
    round_number: int = 0

    # Non-party creatures
    npcs: list[NPC] = Field(default_factory=list)

    # Quests
    active_quests: list[Quest] = Field(default_factory=list)
    completed_quests: list[Quest] = Field(default_factory=list)

    # History (used to feed the LLM with relevant context)
    narrative_log: list[NarrativeEntry] = Field(default_factory=list)
    summary_history: list[str] = Field(default_factory=list)

    # Phase 33 — periodic narrative summarizer config + cursor state.
    # Defaults preserve old saves (Pydantic fills missing keys).
    # Trigger condition: every `summary_every_n_entries` new entries
    # since `last_summarized_at_index`, OR total chars in narrative_log
    # exceed `summary_char_threshold`. `summary_enabled` is a runtime
    # kill switch (``/summary off`` in the CLI).
    summary_enabled: bool = True
    summary_every_n_entries: int = 20
    summary_char_threshold: int = 12_000
    last_summarized_at_index: int = 0
    last_summary_attempt_at_index: int = 0

    # Player-set campaign preferences
    # Default "longo" preserves the original behavior for old saves
    # (Pydantic fills the default when the key is missing).
    narration_length: Literal["curto", "medio", "longo"] = "longo"

    # Cenário inicial opcional definido pelo jogador na criação da campanha.
    # Vazio = LLM decide livremente (comportamento original). Não-vazio =
    # injetado em build_dm_context_block apenas na primeira cena
    # (narrative_log vazio), para o DM usar como base autoritativa da abertura.
    # Default "" preserva saves antigos (Pydantic preenche ao validar).
    initial_scenario: str = ""

    # Phase 38 — XP da party (compartilhado entre todos os membros).
    # Combat kills (Monster.xp) depositam aqui no `end_combat`, e o
    # meta-comando `/award-xp <n>` permite grants narrativos fora de
    # combate. Cruza os thresholds PHB p. 15 → auto-level-up de todos.
    # Default 0 preserva saves antigos.
    party_xp: int = 0

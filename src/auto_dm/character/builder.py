"""Character builder: turn PHB choices into a Pydantic Character.

The builder follows a fluent pattern: each ``with_*`` method returns
``self`` for chaining, and ``build()`` produces an immutable
``CharacterDraft`` containing the final ``Character``.

Choice validation:
- Skill picks must be from the class's allowed skill list and not exceed
  the number of choices the class grants.
- Subrace must belong to the chosen race.
- For spellcasters, spells/cantrips are picked via
  ``auto_dm.character.spells``.

The builder does NOT prompt the user — that's the job of the CLI / LLM
interface. This module just validates choices and computes derived
stats (HP, AC, proficiencies).
"""
from __future__ import annotations

import random
import re
import uuid
from dataclasses import dataclass
from typing import Optional

from auto_dm.engine.dice import roll_stats
from auto_dm.character.spells import SpellSelection, get_spell_slots
from auto_dm.phb import (
    CharacterClass,
    PHBArmor,
    PHBWeapon,
    Race,
    Subrace,
    get_armor,
    get_class,
    get_race,
    get_weapon,
)
from auto_dm.state.models import (
    Ability,
    AbilityScores,
    Character,
    EquippedSlots,
    Item,
    Proficiencies,
    Skill,
)


# ============================================================================
# Constants
# ============================================================================


STANDARD_ARRAY: tuple[int, ...] = (15, 14, 13, 12, 10, 8)
STAT_BLOCK_SIZE: int = 6
STAT_MIN: int = 1
STAT_MAX_AFTER_BONUSES: int = 20


# Skill name <-> Skill enum helpers (the PHB gives skills in title case
# like "Animal Handling" but our enum is SNAKE_CASE).
_PHB_SKILL_TO_ENUM = {
    "acrobatics": Skill.ACROBATICS,
    "animal handling": Skill.ANIMAL_HANDLING,
    "arcana": Skill.ARCANA,
    "athletics": Skill.ATHLETICS,
    "deception": Skill.DECEPTION,
    "history": Skill.HISTORY,
    "insight": Skill.INSIGHT,
    "intimidation": Skill.INTIMIDATION,
    "investigation": Skill.INVESTIGATION,
    "medicine": Skill.MEDICINE,
    "nature": Skill.NATURE,
    "perception": Skill.PERCEPTION,
    "performance": Skill.PERFORMANCE,
    "persuasion": Skill.PERSUASION,
    "religion": Skill.RELIGION,
    "sleight of hand": Skill.SLEIGHT_OF_HAND,
    "stealth": Skill.STEALTH,
    "survival": Skill.SURVIVAL,
}


def parse_skill_name(name: str) -> Skill:
    """Convert a PHB-style skill name ('Animal Handling') to Skill enum."""
    key = name.strip().lower()
    if key not in _PHB_SKILL_TO_ENUM:
        raise ValueError(f"Unknown skill: {name!r}")
    return _PHB_SKILL_TO_ENUM[key]


# ============================================================================
# Skill list parsing (from class proficiency text)
# ============================================================================


def parse_class_skill_options(text: str) -> list[str]:
    """Extract the list of skill names from a class's skill text.

    Input like: "Choose two from Animal Handling, Athletics, Intimidation,
    Nature, Perception, and Survival"
    Output: ["animal handling", "athletics", "intimidation", "nature",
    "perception", "survival"]
    """
    text = text.strip()
    if "from" not in text.lower():
        return []
    after = text[text.lower().index("from") + len("from") :].strip()
    after = after.rstrip(".")
    # Protect compound skill names from the "and" split
    compounds = {
        "animal handling": "ANIMAL_HANDLING",
        "sleight of hand": "SLEIGHT_OF_HAND",
    }
    protected = after
    for k, v in compounds.items():
        protected = re.sub(k, v, protected, flags=re.IGNORECASE)
    # Split on "," and " and "
    parts = re.split(r",|\band\b", protected)
    # Restore compound names
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        for k, v in compounds.items():
            p = re.sub(v, k, p, flags=re.IGNORECASE)
        out.append(p.lower())
    return out


# ============================================================================
# Draft result
# ============================================================================


@dataclass
class CharacterDraft:
    """Result of a successful build. Holds the Character + audit trail."""

    character: Character
    race: Race
    subrace: Optional[Subrace]
    char_class: CharacterClass
    ability_scores_base: list[int]  # before racial bonuses
    ability_scores_final: list[int]  # after racial bonuses
    skill_choices: list[str]
    equipment_choices: list[str]


# ============================================================================
# Builder
# ============================================================================


class CharacterBuilder:
    """Fluent builder for creating a level 1-5 PHB character.

    Required calls before build():
        with_name, with_race, with_class, with_ability_scores (or
        with_standard_array / with_rolled_stats), with_skills.

    Optional:
        with_subrace, with_subclass, with_background, with_alignment,
        with_level, with_starting_armor, with_starting_weapon,
        with_spell_selection.
    """

    def __init__(self) -> None:
        self._name: str = ""
        self._race_name: str = ""
        self._subrace_name: Optional[str] = None
        self._class_name: str = ""
        self._subclass_name: Optional[str] = None
        self._background: str = ""
        self._alignment: str = "N"
        self._level: int = 1
        self._ability_scores: Optional[list[int]] = None
        self._skill_choices: list[str] = []
        self._starting_armor: Optional[str] = None
        self._starting_weapon: Optional[str] = None
        self._starting_shield: bool = False
        self._starting_pack: Optional[str] = None  # Phase 25c: gear pack
        self._spell_selection = None  # filled in spells.py

    # ----- Identity ----------------------------------------------------------

    def with_name(self, name: str) -> "CharacterBuilder":
        self._name = name.strip()
        return self

    def with_race(self, race: str, *, subrace: Optional[str] = None) -> "CharacterBuilder":
        self._race_name = race.strip()
        self._subrace_name = subrace.strip() if subrace else None
        return self

    def with_class(
        self, class_name: str, *, subclass: Optional[str] = None
    ) -> "CharacterBuilder":
        self._class_name = class_name.strip()
        self._subclass_name = subclass.strip() if subclass else None
        return self

    def with_background(self, background: str) -> "CharacterBuilder":
        self._background = background.strip()
        return self

    def with_alignment(self, alignment: str) -> "CharacterBuilder":
        valid = {"LG", "NG", "CG", "LN", "N", "CN", "LE", "NE", "CE"}
        a = alignment.strip().upper()
        if a not in valid:
            raise ValueError(f"Invalid alignment {alignment!r}")
        self._alignment = a
        return self

    def with_level(self, level: int) -> "CharacterBuilder":
        if not 1 <= level <= 5:
            raise ValueError(f"Phase MVP supports levels 1-5, got {level}")
        self._level = level
        return self

    # ----- Stats -------------------------------------------------------------

    def with_ability_scores(self, scores: list[int]) -> "CharacterBuilder":
        if len(scores) != STAT_BLOCK_SIZE:
            raise ValueError(
                f"Expected {STAT_BLOCK_SIZE} scores, got {len(scores)}"
            )
        for s in scores:
            if not STAT_MIN <= s <= 20:
                raise ValueError(f"Score out of range: {s}")
        self._ability_scores = list(scores)
        return self

    def with_standard_array(self) -> "CharacterBuilder":
        self._ability_scores = list(STANDARD_ARRAY)
        return self

    def with_rolled_stats(
        self, rng: random.Random | None = None
    ) -> "CharacterBuilder":
        self._ability_scores = roll_stats(rng=rng)
        return self

    # ----- Choices -----------------------------------------------------------

    def with_skills(self, skills: list[str]) -> "CharacterBuilder":
        self._skill_choices = [s.strip().lower() for s in skills]
        return self

    def with_starting_weapon(self, weapon_name: str) -> "CharacterBuilder":
        self._starting_weapon = weapon_name.strip()
        return self

    def with_starting_armor(self, armor_name: str) -> "CharacterBuilder":
        self._starting_armor = armor_name.strip()
        return self

    def with_shield(self, equipped: bool = True) -> "CharacterBuilder":
        self._starting_shield = equipped
        return self

    def with_starting_pack(self, pack_name: str) -> "CharacterBuilder":
        """Override the class's starting equipment with a gear pack.

        Example: ``.with_starting_pack("Explorer's Pack")`` adds the
        pack's contents to the character's inventory. Only pack items
        that resolve to known gear items are added (others are
        silently skipped — the raw description is preserved on the
        Character if needed).
        """
        self._starting_pack = pack_name.strip()
        return self

    def with_spell_selection(self, selection: SpellSelection) -> "CharacterBuilder":
        """Attach a spell selection (only valid for spellcasting classes)."""
        self._spell_selection = selection
        return self

    # ----- Build -------------------------------------------------------------

    def build(self) -> CharacterDraft:
        # Validate required fields
        if not self._name:
            raise ValueError("Character must have a name (with_name)")
        if not self._race_name:
            raise ValueError("Character must have a race (with_race)")
        if not self._class_name:
            raise ValueError("Character must have a class (with_class)")
        if self._ability_scores is None:
            raise ValueError(
                "Character must have ability scores "
                "(with_ability_scores / with_standard_array / with_rolled_stats)"
            )

        # Look up PHB data
        race = get_race(self._race_name)
        if race is None:
            raise ValueError(f"Unknown race: {self._race_name!r}")
        subrace = self._resolve_subrace(race)
        char_class = get_class(self._class_name)
        if char_class is None:
            raise ValueError(f"Unknown class: {self._class_name!r}")

        # Apply racial bonuses
        base_scores = list(self._ability_scores)
        final_scores = self._apply_racial_bonuses(base_scores, race, subrace)

        # Validate chosen skills
        self._validate_skill_choices(char_class)

        # Compute derived stats
        abilities = AbilityScores(
            strength=final_scores[0],
            dexterity=final_scores[1],
            constitution=final_scores[2],
            intelligence=final_scores[3],
            wisdom=final_scores[4],
            charisma=final_scores[5],
        )
        hp = self._compute_hp(char_class, abilities, subrace, self._level)
        prof_bonus = self._proficiency_bonus_for_level(self._level)
        hit_dice_total = self._level  # 1dX per level
        speed = subrace.speed if (subrace and subrace.speed) else race.speed

        # Proficiencies
        profs = self._build_proficiencies(char_class, race, subrace)

        # Equipment (and AC from equipped items)
        inventory, equipped, ac = self._build_equipment(char_class, abilities)

        # Spellcasting (if applicable)
        spellcasting = None
        if char_class.spellcasting is not None and self._spell_selection is not None:
            spellcasting = self._spell_selection.to_spellcasting(
                char_class, abilities, prof_bonus
            )
            slots = get_spell_slots(char_class.name, self._level)
            spellcasting.spell_slots = dict(slots)
            spellcasting.spell_slots_max = dict(slots)

        character = Character(
            id=str(uuid.uuid4())[:8],
            name=self._name,
            race=race.name,
            subrace=subrace.name if subrace else None,
            **{"class": char_class.name},
            subclass=self._subclass_name,
            level=self._level,
            background=self._background or "Commoner",
            alignment=self._alignment,
            abilities=abilities,
            hp_current=hp,
            hp_max=hp,
            armor_class=ac,
            speed=speed,
            proficiency_bonus=prof_bonus,
            hit_dice=char_class.hit_dice,
            hit_dice_remaining=hit_dice_total,
            proficiencies=profs,
            inventory=inventory,
            equipped=equipped,
            spellcasting=spellcasting,
        )

        # Populate subclass features acquired at or below the character's
        # starting level (Phase 25b). For L1 characters of subclass-
        # granting classes this populates things like Sorcerer's Draconic
        # Resilience or Warlock's Dark One's Blessing (both L1).
        from auto_dm.character.level_up import apply_subclass_features
        apply_subclass_features(character, at_level=self._level)

        return CharacterDraft(
            character=character,
            race=race,
            subrace=subrace,
            char_class=char_class,
            ability_scores_base=base_scores,
            ability_scores_final=final_scores,
            skill_choices=self._skill_choices,
            equipment_choices=self._build_equipment_choices_log(),
        )

    # ----- Internals ---------------------------------------------------------

    def _resolve_subrace(self, race: Race) -> Optional[Subrace]:
        if not self._subrace_name:
            return None
        for s in race.subraces:
            if s.name.lower() == self._subrace_name.lower():
                return s
        raise ValueError(
            f"Subrace {self._subrace_name!r} not found for race {race.name!r}. "
            f"Available: {[s.name for s in race.subraces]}"
        )

    def _apply_racial_bonuses(
        self,
        scores: list[int],
        race: Race,
        subrace: Optional[Subrace],
    ) -> list[int]:
        """Add racial + subracial ability bonuses."""
        out = list(scores)
        ability_order = [
            Ability.STR,
            Ability.DEX,
            Ability.CON,
            Ability.INT,
            Ability.WIS,
            Ability.CHA,
        ]
        # Race bonuses
        for bonus in race.ability_bonuses:
            idx = ability_order.index(bonus.ability)
            out[idx] += bonus.bonus
        # Subrace bonuses (e.g. Hill Dwarf +1 WIS on top of Dwarf +2 CON)
        if subrace:
            for bonus in subrace.ability_bonuses:
                idx = ability_order.index(bonus.ability)
                out[idx] += bonus.bonus
        # Clamp to [1, 20] for sanity (PHB allows up to 24 at epic levels,
        # but MVP is 1-20)
        out = [max(STAT_MIN, min(STAT_MAX_AFTER_BONUSES, s)) for s in out]
        return out

    def _validate_skill_choices(self, char_class: CharacterClass) -> None:
        allowed = parse_class_skill_options(char_class.proficiencies.skills_choices)
        num_allowed = char_class.proficiencies.num_skill_choices
        if num_allowed == 0:
            # Class doesn't grant skill choices (e.g. Sorcerer uses 'any')
            return
        if len(self._skill_choices) > num_allowed:
            raise ValueError(
                f"{char_class.name} allows {num_allowed} skill picks, "
                f"got {len(self._skill_choices)}"
            )
        for skill in self._skill_choices:
            if skill not in allowed:
                raise ValueError(
                    f"{skill!r} is not in {char_class.name}'s skill list: {allowed}"
                )

    def _compute_hp(
        self,
        char_class: CharacterClass,
        abilities: AbilityScores,
        subrace: Optional[Subrace],
        level: int,
    ) -> int:
        """Compute max HP at level 1 (or higher)."""
        # Parse hit dice like "1d10" to get the max face
        import re
        m = re.match(r"(\d+)d(\d+)", char_class.hit_dice)
        if not m:
            return 0
        die_max = int(m.group(2))
        con_mod = abilities.modifier(Ability.CON)

        if level == 1:
            hp = die_max + con_mod
        else:
            # Levels 2+: avg (rounded up) per PHB
            avg = (die_max // 2) + 1
            hp = die_max + con_mod + (level - 1) * (avg + con_mod)

        # Hill Dwarf: +1 HP per level (Dwarven Toughness)
        if subrace and subrace.name.lower() == "hill dwarf":
            hp += level
        return max(1, hp)

    def _compute_ac(self, abilities: AbilityScores) -> int:
        """Base AC for unarmored (10 + DEX). Real AC is computed by _build_equipment."""
        return 10 + abilities.modifier(Ability.DEX)

    def _proficiency_bonus_for_level(self, level: int) -> int:
        """+2 for levels 1-4, +3 for 5-8, etc."""
        if level <= 4:
            return 2
        if level <= 8:
            return 3
        if level <= 12:
            return 4
        if level <= 16:
            return 5
        return 6

    def _build_proficiencies(
        self,
        char_class: CharacterClass,
        race: Race,
        subrace: Optional[Subrace],
    ) -> Proficiencies:
        saves = list(char_class.proficiencies.saving_throws)
        # Player-chosen skills (from with_skills) take priority.
        chosen_skills = {parse_skill_name(s) for s in self._skill_choices}
        # Phase 25c: pull skill proficiencies from the chosen background.
        # If the player already picked the same skill, skip the duplicate
        # (PHB rule: pick a different skill of the same kind instead).
        bg_skills: list[Skill] = []
        bg_tools: list[str] = []
        bg_languages: list[str] = []
        if self._background:
            from auto_dm.phb import get_background
            bg = get_background(self._background)
            if bg is not None:
                for raw_skill in bg.skill_proficiencies:
                    try:
                        sk = parse_skill_name(raw_skill)
                    except ValueError:
                        # "One of your choice" — wizard handles this
                        # before calling build(); ignore here.
                        continue
                    if sk not in chosen_skills:
                        bg_skills.append(sk)
                bg_tools = list(bg.tool_proficiencies)
                # Background languages: "Two of your choice" is left
                # to the wizard. A specific comma-separated list is
                # appended as-is (string list, not a typed enum).
                if bg.languages and "choice" not in bg.languages.lower():
                    bg_languages = [
                        s.strip() for s in bg.languages.split(",")
                        if s.strip()
                    ]
        skills = list(chosen_skills) + bg_skills
        # Languages: race + background (deferred choice still empty)
        languages = list(race.languages) + bg_languages
        return Proficiencies(
            saves=saves,
            skills=skills,
            tools=bg_tools,
            languages=languages,
        )

    def _build_equipment(
        self,
        char_class: CharacterClass,
        abilities: AbilityScores,
    ) -> tuple[list[Item], EquippedSlots, int]:
        """Build inventory and equipped slots, and compute AC from them.

        Returns (inventory, equipped, ac).
        """
        inventory: list[Item] = []
        equipped = EquippedSlots()
        equipment_log: list[str] = []

        # Starting weapon
        if self._starting_weapon:
            weapon = get_weapon(self._starting_weapon)
            if weapon:
                item = _weapon_to_item(weapon)
                equipped.main_hand = item
                inventory.append(item)
                equipment_log.append(f"main_hand: {weapon.name}")

        # Starting armor
        if self._starting_armor:
            armor = get_armor(self._starting_armor)
            if armor:
                item = _armor_to_item(armor)
                equipped.armor = item
                inventory.append(item)
                equipment_log.append(f"armor: {armor.name}")

        # Shield
        if self._starting_shield:
            shield = get_armor("Shield")
            if shield:
                item = _armor_to_item(shield)
                equipped.off_hand = item
                inventory.append(item)
                equipment_log.append("off_hand: Shield")

        # Gear pack (Phase 25c): add pack contents to inventory.
        if self._starting_pack:
            inventory.extend(self._build_pack_items())

        # Compute AC
        ac = self._compute_ac(abilities)
        if equipped.armor is not None and equipped.armor.armor is not None:
            ap = equipped.armor.armor
            ac = ap.base_ac
            if ap.add_dex_modifier:
                dex_mod = abilities.modifier(Ability.DEX)
                if ap.max_dex_bonus is not None:
                    dex_mod = min(dex_mod, ap.max_dex_bonus)
                ac += dex_mod
        if self._starting_shield:
            ac += 2

        return inventory, equipped, ac

    def _build_equipment_choices_log(self) -> list[str]:
        # Best-effort: return the choices that were applied.
        out: list[str] = []
        if self._starting_weapon:
            out.append(f"weapon: {self._starting_weapon}")
        if self._starting_armor:
            out.append(f"armor: {self._starting_armor}")
        if self._starting_shield:
            out.append("shield")
        if self._starting_pack:
            out.append(f"pack: {self._starting_pack}")
        return out

    def _build_pack_items(self) -> list[Item]:
        """Resolve a gear pack's contents to known PHB items.

        Looks up each pack content against the gear table and weapons
        table. Items that don't resolve (e.g. "alphabet soup") are
        silently skipped; the pack description is preserved elsewhere
        for narration.
        """
        from auto_dm.phb import get_gear_item, get_pack
        from auto_dm.state.models import ItemType

        pack = get_pack(self._starting_pack or "")
        if pack is None:
            return []
        items: list[Item] = []
        seen: set[str] = set()
        for content_name in pack.contents:
            # Strip leading articles ("a", "an") and quantity prefixes
            cleaned = content_name.strip()
            while cleaned.lower().startswith(("a ", "an ")):
                cleaned = cleaned[2:] if cleaned.lower().startswith("a ") else cleaned[3:]
            # Drop quantities in parens like "Arrows (20)" or "vial"
            cleaned = re.sub(r"\s*\(.+?\)\s*$", "", cleaned).strip()
            if not cleaned or cleaned.lower() in seen:
                continue
            # Try the gear table first.
            gear = get_gear_item(cleaned)
            if gear is not None:
                items.append(Item(
                    name=gear.name,
                    type=ItemType.MISC,
                    weight=gear.weight,
                    value_gp=gear.cost_gp,
                    description=gear.description,
                ))
                seen.add(cleaned.lower())
                continue
            # Fall back to the weapons table (e.g. "dagger" in some packs).
            weapon = get_weapon(cleaned)
            if weapon is not None:
                items.append(_weapon_to_item(weapon))
                seen.add(cleaned.lower())
        return items


# ============================================================================
# Item conversion helpers
# ============================================================================


def _weapon_to_item(weapon: PHBWeapon) -> Item:
    """Convert a PHBWeapon into an Item for Character.equipped/inventory."""
    from auto_dm.state.models import ItemType, WeaponProperties

    return Item(
        name=weapon.name,
        type=ItemType.WEAPON,
        weight=weapon.weight,
        value_gp=weapon.cost_gp,
        weapon=WeaponProperties(
            damage_dice=weapon.damage_dice,
            damage_type=weapon.damage_type,
            finesse=weapon.finesse,
            heavy=weapon.heavy,
            light=weapon.light,
            reach=weapon.reach,
            thrown=weapon.thrown,
            two_handed=weapon.two_handed,
            versatile_dice=weapon.versatile_dice,
            range_normal=weapon.range_normal,
            range_long=weapon.range_long,
            ammunition=weapon.ammunition,
            loading=weapon.loading,
        ),
    )


def _armor_to_item(armor: PHBArmor) -> Item:
    from auto_dm.state.models import ArmorProperties, ItemType

    return Item(
        name=armor.name,
        type=ItemType.ARMOR if not armor.is_shield else ItemType.SHIELD,
        weight=armor.weight,
        value_gp=armor.cost_gp,
        armor=ArmorProperties(
            base_ac=armor.base_ac,
            add_dex_modifier=armor.add_dex,
            max_dex_bonus=armor.max_dex,
            stealth_disadvantage=armor.stealth_disadvantage,
            strength_required=armor.strength_required,
            is_shield=armor.is_shield,
        ),
    )

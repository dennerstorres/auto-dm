"""Loaders that walk data/phb/ directories and produce typed PHB models.

Each loader is a function that takes the path to the PHB root and returns
the parsed models. They are independent — callers can re-run any single
loader without re-parsing everything.

The loaders here are the "data layer" of the PHB module. They never cache
on their own; caching is handled in lookup.py.
"""
from __future__ import annotations

import re
from pathlib import Path

from auto_dm.phb.models import (
    AbilityBonus,
    Background,
    CharacterClass,
    ClassFeature,
    ClassProficiency,
    GearCategory,
    MagicItem,
    MagicItemType,
    Monster,
    MonsterAction,
    MonsterSize,
    MonsterTrait,
    MonsterType,
    Mount,
    PHBArmor,
    PHBArmorCategory,
    PHBCondition,
    PHBDisease,
    PHBEquipmentPack,
    PHBGear,
    PHBLanguage,
    PHBPoison,
    PHBSpell,
    PHBSpellComponent,
    PHBSpellSchool,
    PHBTool,
    PHBTrap,
    PHBWeapon,
    PHBWeaponCategory,
    Race,
    Rarity,
    SpellcastingInfo,
    Subclass,
    Subrace,
    ToolCategory,
    Trait,
    Vehicle,
    VehicleType,
)
from auto_dm.phb.parser import (
    _clean_inline,
    find_tables,
    parse_cost_gp,
    parse_damage,
    parse_fields,
    parse_range,
    parse_traits,
    parse_weight_lb,
    split_sections,
)
from auto_dm.state.models import Ability, AbilityScores


# ============================================================================
# Races
# ============================================================================


def load_races(phb_root: Path) -> list[Race]:
    """Load all races from data/phb/Races/.

    Skips files starting with '#' (indexes).
    """
    races_dir = phb_root / "Races"
    if not races_dir.exists():
        return []

    races: list[Race] = []
    for path in sorted(races_dir.glob("*.md")):
        if path.name.startswith("#"):
            continue
        race = parse_race_file(path)
        if race is not None:
            races.append(race)
    return races


def parse_race_file(path: Path) -> Race | None:
    """Parse one race .md file."""
    text = path.read_text(encoding="utf-8")
    sections = split_sections(text)
    if not sections:
        return None

    name = path.stem  # "Dwarf", "Elf", etc.

    # Find the "X Traits" ### section which contains base traits
    traits_section = next(
        (s for s in sections if s.level == 3 and "trait" in s.title_lower),
        None,
    )
    base_traits = parse_traits(traits_section.body) if traits_section else []
    description = _first_paragraph(traits_section.body) if traits_section else ""

    # Parse ability score increase from base traits
    ability_bonuses = _extract_ability_bonuses(base_traits)

    # Speed from base traits
    speed = 30
    for tname, tdesc in base_traits:
        if tname.lower() == "speed":
            m = re.search(r"(\d+)\s*feet", tdesc, re.IGNORECASE)
            if m:
                speed = int(m.group(1))
                break

    # Size from base traits
    size = "Medium"
    for tname, tdesc in base_traits:
        if tname.lower() == "size":
            if "Small" in tdesc:
                size = "Small"
            break

    # Languages from base traits
    languages: list[str] = []
    for tname, tdesc in base_traits:
        if tname.lower() == "languages":
            # "You can speak, read, and write Common and Dwarvish."
            m = re.search(r"speak.*?write\s+(.+?)\.", tdesc)
            if m:
                raw = m.group(1)
                parts = [s.strip() for s in re.split(r",|\sand\s", raw)]
                languages = [p for p in parts if p and p.lower() not in {"plus", "of your choice"}]
            break

    # Sub-races: ## headings under the base
    subraces: list[Subrace] = []
    for s in sections:
        if s.level == 2 and s.title.strip() != name:
            # Skip intro sections like "Hill Dwarf" intro
            subrace = _parse_subrace(s, name)
            if subrace is not None:
                subraces.append(subrace)

    return Race(
        name=name,
        description=description,
        size=size,
        speed=speed,
        ability_bonuses=ability_bonuses,
        traits=[Trait(name=n, description=d) for n, d in base_traits],
        languages=languages,
        subraces=subraces,
    )


def _parse_subrace(section, parent_name: str) -> Subrace | None:
    """Parse a ## section as a sub-race."""
    title = section.title.strip()
    # Skip non-subrace sections that happen to be ## headings
    if title.lower() in {"racial traits", "languages", "alignment"}:
        return None

    traits = parse_traits(section.body)
    ability_bonuses = _extract_ability_bonuses(traits)

    # Subrace speed override
    speed = None
    for tname, tdesc in traits:
        if tname.lower() == "speed":
            m = re.search(r"(\d+)\s*feet", tdesc, re.IGNORECASE)
            if m:
                speed = int(m.group(1))
            break

    return Subrace(
        name=title,
        parent_race=parent_name,
        description=_first_paragraph(section.body),
        ability_bonuses=ability_bonuses,
        traits=[Trait(name=n, description=d) for n, d in traits],
        speed=speed,
    )


def _extract_ability_bonuses(traits: list[tuple[str, str]]) -> list[AbilityBonus]:
    """Extract 'Ability Score Increase' entries from traits."""
    bonuses: list[AbilityBonus] = []
    for name, desc in traits:
        if name.lower() != "ability score increase":
            continue
        # "Your Constitution score increases by 2."
        # "Your Wisdom score increases by 1."
        m = re.search(
            r"your\s+(\w+)\s+score\s+increases\s+by\s+(\d+)",
            desc,
            re.IGNORECASE,
        )
        if m:
            ability_name = m.group(1).lower()
            bonus = int(m.group(2))
            try:
                ability = Ability(ability_name)
            except ValueError:
                continue
            bonuses.append(AbilityBonus(ability=ability, bonus=bonus))
    return bonuses


def _first_paragraph(text: str) -> str:
    """Get the first non-empty paragraph from markdown text."""
    for para in re.split(r"\n\s*\n", text.strip()):
        cleaned = para.strip()
        if cleaned and not cleaned.startswith(("#", "*", "-", "|")):
            # Skip trait-style lines (***...***)
            if not re.match(r"^\*\*\*", cleaned):
                return re.sub(r"\s+", " ", cleaned)
    return ""


# ============================================================================
# Classes
# ============================================================================


def load_classes(phb_root: Path) -> list[CharacterClass]:
    """Load all classes from data/phb/Classes/."""
    classes_dir = phb_root / "Classes"
    if not classes_dir.exists():
        return []

    classes: list[CharacterClass] = []
    for path in sorted(classes_dir.glob("*.md")):
        cls = parse_class_file(path)
        if cls is not None:
            classes.append(cls)
    return classes


def parse_class_file(path: Path) -> CharacterClass | None:
    """Parse one class .md file."""
    text = path.read_text(encoding="utf-8")
    sections = split_sections(text)
    if not sections:
        return None

    name = path.stem
    description = _first_paragraph(text)

    # Hit points (#### Hit Points)
    hit_dice = ""
    hp_at_1st = ""
    for s in sections:
        if s.level == 4 and s.title.lower() == "hit points":
            fields = parse_fields(s.body)
            # "1d12 per barbarian level" -> "1d12"
            hd_raw = fields.get("hit dice", "")
            hd_match = re.match(r"^(\d+d\d+)", hd_raw)
            hit_dice = hd_match.group(1) if hd_match else hd_raw
            hp_at_1st = fields.get("hit points at 1st level", "")
            break

    # Proficiencies (#### Proficiencies)
    prof = ClassProficiency()
    for s in sections:
        if s.level == 4 and s.title.lower() == "proficiencies":
            fields = parse_fields(s.body)
            prof.armor = _split_list(fields.get("armor", ""))
            prof.weapons = _split_list(fields.get("weapons", ""))
            prof.tools = _split_list(fields.get("tools", ""))
            saves_text = fields.get("saving throws", "")
            for save_name in re.findall(r"\b(Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)\b", saves_text):
                try:
                    prof.saving_throws.append(Ability(save_name.lower()))
                except ValueError:
                    pass
            skills_text = fields.get("skills", "")
            prof.skills_choices = skills_text
            # "Choose two from..." or "Choose two skills from..."
            m = re.match(r"choose\s+(\w+)(?:\s+\w+)?\s+from", skills_text, re.IGNORECASE)
            if m:
                word = m.group(1).lower()
                num_map = {"one": 1, "two": 2, "three": 3, "four": 4}
                prof.num_skill_choices = num_map.get(word, 0)
            break

    # Starting equipment (#### Equipment)
    starting_equipment: list[str] = []
    for s in sections:
        if s.level == 4 and s.title.lower() == "equipment":
            for line in s.body.splitlines():
                line = line.strip()
                if line.startswith("- "):
                    starting_equipment.append(line[2:].strip())
            break

    # Features (### headings with level info embedded in their #### children)
    features: list[ClassFeature] = []
    subclasses: list[Subclass] = []

    # Class features appear as ### headings at level 3 (between "Class Features"
    # intro and any ## heading that introduces the subclass options).
    # Any ## heading after the intro marks the start of the subclass section —
    # we don't need to match specific names ("Barbarian Paths",
    # "Sacred Oaths", "Sorcerous Origins", "Otherworldly Patrons" etc.)
    in_subclass_section = False
    current_subclass: Subclass | None = None

    for s in sections:
        if s.level == 1:
            # Top heading — skip
            continue
        if s.level == 2:
            # Skip the top-level "Class Features" / "Creating a X" sections
            t = s.title.strip().lower()
            if t in {"class features", "creating a " + name.lower(), f"creating a {name.lower()}"}:
                continue
            # Any other ## heading signals the subclass section
            in_subclass_section = True
            current_subclass = None
            continue

        if s.level == 3 and not in_subclass_section:
            # Skip intro sections like "Class Features", "Creating a X"
            feat_name = s.title.strip()
            if feat_name.lower() in {"class features", f"creating a {name.lower()}"}:
                continue
            feat_level = _extract_level_from_feature(s.body) or _extract_level_from_intro(s.body)
            features.append(ClassFeature(
                name=feat_name,
                description=_first_paragraph(s.body) or s.body.strip(),
                level=feat_level,
            ))

        if s.level == 3 and in_subclass_section:
            # Subclass heading
            current_subclass = Subclass(
                name=s.title.strip(),
                parent_class=name,
                description=_first_paragraph(s.body),
                features=[],
            )
            subclasses.append(current_subclass)

        if s.level == 4 and current_subclass is not None:
            # Subclass feature
            feat_name = s.title.strip()
            feat_level = _extract_level_from_feature(s.body) or _extract_level_from_intro(s.body)
            current_subclass.features.append(ClassFeature(
                name=feat_name,
                description=_first_paragraph(s.body) or s.body.strip(),
                level=feat_level,
            ))

    # Spellcasting info: detect if this is a casting class
    spellcasting = _detect_spellcasting(name, sections)

    return CharacterClass(
        name=name,
        description=description,
        hit_dice=hit_dice,
        hit_points_at_1st=hp_at_1st,
        proficiencies=prof,
        starting_equipment=starting_equipment,
        features=features,
        subclasses=subclasses,
        spellcasting=spellcasting,
    )


def _split_list(text: str) -> list[str]:
    """Split comma/and-separated text into a clean list."""
    text = text.strip()
    if not text or text.lower() in {"none", "-"}:
        return []
    # Split on ',' and ' and '
    parts = re.split(r",|\band\b", text)
    return [p.strip() for p in parts if p.strip() and p.strip().lower() not in {"none", "-"}]


def _extract_level_from_feature(body: str) -> int | None:
    """Extract the level at which a feature is gained from its description.

    Patterns:
        "At 2nd level, you gain..."
        "Beginning at 2nd level..."
        "Starting at 2nd level..."
        "When you reach 4th level..."
        "At 1st level, ..."
    """
    patterns = [
        r"[Aa]t\s+(\d+)(?:st|nd|rd|th)\s+level",
        r"[Bb]eginning\s+at\s+(\d+)(?:st|nd|rd|th)\s+level",
        r"[Ss]tarting\s+at\s+(\d+)(?:st|nd|rd|th)\s+level",
        r"[Ww]hen\s+you\s+reach\s+(\d+)(?:st|nd|rd|th)\s+level",
        r"[Aa]t\s+(\d+)(?:st|nd|rd|th)\s+level,?\s+you",
    ]
    for pat in patterns:
        m = re.search(pat, body)
        if m:
            return int(m.group(1))
    return None


def _extract_level_from_intro(body: str) -> int:
    """Fallback: look for level mentions in the first sentence."""
    m = re.search(r"\b(\d+)(?:st|nd|rd|th)\s+level\b", body)
    if m:
        return int(m.group(1))
    return 1


def _detect_spellcasting(name: str, sections) -> SpellcastingInfo | None:
    """Detect if the class is a caster and what ability it uses."""
    caster_map = {
        "bard": Ability.CHA,
        "cleric": Ability.WIS,
        "druid": Ability.WIS,
        "paladin": Ability.CHA,
        "ranger": Ability.WIS,
        "sorcerer": Ability.CHA,
        "warlock": Ability.CHA,
        "wizard": Ability.INT,
    }
    ability = caster_map.get(name.lower())
    if ability is None:
        return None

    # Find the Spellcasting section
    for s in sections:
        if s.level == 3 and s.title.lower() == "spellcasting":
            return SpellcastingInfo(
                ability=ability,
                description=_first_paragraph(s.body),
            )
    # Even if no Spellcasting section found, mark it as caster if class name matches
    return SpellcastingInfo(ability=ability, description="")


# ============================================================================
# Spells
# ============================================================================


def load_spells(phb_root: Path) -> list[PHBSpell]:
    """Load all spells from data/phb/Spells/.

    Skips files starting with '#' or '##' (indexes) and the spell lists.
    Also attaches class lists to each spell from the Wikilinked spell lists file.
    """
    spells_dir = phb_root / "Spells"
    if not spells_dir.exists():
        return []

    # First, parse the spell lists to know which class each spell belongs to
    class_map = _parse_spell_class_lists(spells_dir)

    spells: list[PHBSpell] = []
    for path in sorted(spells_dir.glob("*.md")):
        if path.name.startswith("#"):
            continue
        spell = parse_spell_file(path, class_map)
        if spell is not None:
            spells.append(spell)
    return spells


def _parse_spell_class_lists(spells_dir: Path) -> dict[str, set[str]]:
    """Build a map: spell_name (lower) -> {class names}.

    Parses data/phb/Spells/## Spell Lists (Wikilinked).md which has structure:
        ## Class Name Spells
            #### Cantrips (0 Level)
                - [[Spell Name]]
            #### 1st Level
                - [[Spell Name]]
    """
    lists_path = spells_dir / "## Spell Lists (Wikilinked).md"
    if not lists_path.exists():
        return {}

    text = lists_path.read_text(encoding="utf-8")
    spell_to_classes: dict[str, set[str]] = {}

    # Split by ## class headings directly with regex
    blocks = re.split(r"^##\s+", text, flags=re.MULTILINE)
    for block in blocks:
        if not block.strip():
            continue
        # First line is the heading
        lines = block.splitlines()
        title_match = re.match(r"^(\w+)\s+Spells?\s*$", lines[0].strip())
        if not title_match:
            continue
        class_name = title_match.group(1)
        # Find all wikilinks in the rest of the block
        for m in re.finditer(r"\[\[([^\]]+)\]\]", block):
            spell_name = m.group(1).strip()
            spell_to_classes.setdefault(spell_name.lower(), set()).add(class_name)

    return spell_to_classes


def parse_spell_file(path: Path, class_map: dict[str, set[str]] | None = None) -> PHBSpell | None:
    """Parse one spell .md file."""
    text = path.read_text(encoding="utf-8")
    sections = split_sections(text)
    if not sections:
        return None

    name = path.stem
    body = sections[0].body  # the only section is the spell itself

    # Title line: "### Spell Name" — already in section title
    # School/level line: "*3rd-level evocation*"
    level_school_m = re.search(
        r"\*(\d+(?:st|nd|rd|th))-level\s+(\w+)[^*]*\*",
        body,
    )
    cantrip_m = re.search(r"\*(\w+)\s+cantrip[^*]*\*", body, re.IGNORECASE)

    if cantrip_m:
        level = 0
        school_str = cantrip_m.group(1).lower()
    elif level_school_m:
        level_str = level_school_m.group(1)
        level = _ordinal_to_int(level_str)
        school_str = level_school_m.group(2).lower()
    else:
        return None

    try:
        school = PHBSpellSchool(school_str)
    except ValueError:
        return None

    fields = parse_fields(body)
    casting_time = fields.get("casting time", "")
    range_text = fields.get("range", "")
    components_text = fields.get("components", "")
    duration = fields.get("duration", "")

    components: list[PHBSpellComponent] = []
    material = None
    for comp_char in ("V", "S", "M"):
        if comp_char in components_text:
            components.append(PHBSpellComponent(comp_char))
    mat_m = re.search(r"M\s*\(([^)]+)\)", components_text)
    if mat_m:
        material = mat_m.group(1).strip()

    is_ritual = "(ritual)" in body.lower() or "as a ritual" in body.lower()
    is_concentration = "concentration" in duration.lower()

    # Description: the text after the field block, before "At Higher Levels"
    description, higher_levels = _split_higher_levels(body)

    classes = sorted(class_map.get(name.lower(), set())) if class_map else []

    return PHBSpell(
        name=name,
        level=level,
        school=school,
        casting_time=casting_time,
        range_text=range_text,
        components=components,
        material=material,
        duration=duration,
        description=description,
        higher_levels=higher_levels,
        classes=classes,
        is_ritual=is_ritual,
        is_concentration=is_concentration,
    )


def _ordinal_to_int(s: str) -> int:
    """'3rd' -> 3, '1st' -> 1."""
    return int(re.match(r"(\d+)", s).group(1))


def _split_higher_levels(body: str) -> tuple[str, str | None]:
    """Split body into (description, higher_levels) at ***At Higher Levels***."""
    m = re.search(r"\*\*\*At Higher Levels\*\*\*\.\s*(.+)", body, re.DOTALL)
    if not m:
        return _collapse_after_fields(body), None
    head = body[: m.start()]
    tail = m.group(1)
    return _collapse_after_fields(head), re.sub(r"\s+", " ", tail).strip()


def _collapse_after_fields(body: str) -> str:
    """Collapse body text after the field block into a single paragraph."""
    # Find the last field line
    lines = body.splitlines()
    last_field = -1
    for i, line in enumerate(lines):
        if re.match(r"^\*\*[^*]+:\*\*", line):
            last_field = i
    if last_field == -1:
        return re.sub(r"\s+", " ", body).strip()
    rest = "\n".join(lines[last_field + 1 :])
    return re.sub(r"\s+", " ", rest).strip()


# ============================================================================
# Equipment — Weapons
# ============================================================================


def load_weapons(phb_root: Path) -> list[PHBWeapon]:
    """Load all weapons from data/phb/Equipment/Weapons.md table."""
    path = phb_root / "Equipment" / "Weapons.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    tables = find_tables(text)
    weapons: list[PHBWeapon] = []
    for title, tbl in tables:
        # Find the Weapons table — header has 'Damage' and 'Properties'
        if "Damage" not in tbl.headers or "Properties" not in tbl.headers:
            continue
        current_cat = PHBWeaponCategory.SIMPLE_MELEE
        for row in tbl.rows:
            if not row or all(c == "" for c in row):
                continue
            # Category row: e.g. "| **Simple Melee Weapons**  |       |...|"
            first_cell = row[0].strip()
            if first_cell.startswith("**") and first_cell.endswith("**"):
                cat_str = first_cell.strip("*").strip()
                if "Simple Melee" in cat_str:
                    current_cat = PHBWeaponCategory.SIMPLE_MELEE
                elif "Simple Ranged" in cat_str:
                    current_cat = PHBWeaponCategory.SIMPLE_RANGED
                elif "Martial Melee" in cat_str:
                    current_cat = PHBWeaponCategory.MARTIAL_MELEE
                elif "Martial Ranged" in cat_str:
                    current_cat = PHBWeaponCategory.MARTIAL_RANGED
                continue
            weapon = _row_to_weapon(row, current_cat)
            if weapon is not None:
                weapons.append(weapon)
    return weapons


def _row_to_weapon(row: list[str], category: PHBWeaponCategory) -> PHBWeapon | None:
    """Parse a Weapons table row."""
    name = row[0].strip()
    if not name:
        return None
    cost = parse_cost_gp(row[1])
    damage_dice, damage_type = parse_damage(row[2])
    weight = parse_weight_lb(row[3])
    props_text = row[4].strip() if len(row) > 4 else ""

    finesse = "finesse" in props_text.lower()
    light = "light" in props_text.lower()
    heavy = "heavy" in props_text.lower()
    reach = "reach" in props_text.lower()
    thrown = "thrown" in props_text.lower()
    two_handed = "two-handed" in props_text.lower()
    ammunition = "ammunition" in props_text.lower()
    loading = "loading" in props_text.lower()
    special = "special" in props_text.lower()

    versatile_match = re.search(r"versatile\s*\(([^)]+)\)", props_text, re.IGNORECASE)
    versatile_dice = versatile_match.group(1).strip() if versatile_match else None

    range_normal, range_long = parse_range(props_text)

    return PHBWeapon(
        name=name,
        category=category,
        cost_gp=cost,
        damage_dice=damage_dice,
        damage_type=damage_type,
        weight=weight,
        finesse=finesse,
        light=light,
        heavy=heavy,
        reach=reach,
        thrown=thrown,
        two_handed=two_handed,
        ammunition=ammunition,
        loading=loading,
        special=special,
        versatile_dice=versatile_dice,
        range_normal=range_normal,
        range_long=range_long,
        properties_text=props_text,
    )


# ============================================================================
# Equipment — Armor
# ============================================================================


def load_armor(phb_root: Path) -> list[PHBArmor]:
    """Load all armor from data/phb/Equipment/Armor.md table."""
    path = phb_root / "Equipment" / "Armor.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    tables = find_tables(text)
    armors: list[PHBArmor] = []
    for title, tbl in tables:
        # Header is "Armor Class (AC)" — check by substring across all headers
        joined_headers = " | ".join(tbl.headers).lower()
        if "armor class" not in joined_headers and "ac" not in joined_headers:
            continue
        current_cat = PHBArmorCategory.LIGHT
        for row in tbl.rows:
            if not row or all(c == "" for c in row):
                continue
            first_cell = row[0].strip()
            cost_cell = row[1].strip() if len(row) > 1 else ""
            # Detect category header rows: bolded OR all other cells empty
            is_bold = first_cell.startswith("**") and first_cell.endswith("**")
            is_empty_data = (not cost_cell) and all(c.strip() == "" for c in row[1:])
            if is_bold or is_empty_data:
                cat_str = first_cell.strip("*").strip()
                if "Light Armor" in cat_str:
                    current_cat = PHBArmorCategory.LIGHT
                elif "Medium Armor" in cat_str:
                    current_cat = PHBArmorCategory.MEDIUM
                elif "Heavy Armor" in cat_str:
                    current_cat = PHBArmorCategory.HEAVY
                elif "Shield" in cat_str:
                    current_cat = PHBArmorCategory.SHIELD
                continue
            armor = _row_to_armor(row, current_cat)
            if armor is not None:
                armors.append(armor)
    return armors


def _row_to_armor(row: list[str], category: PHBArmorCategory) -> PHBArmor | None:
    """Parse an Armor table row.

    Table columns: Armor | Cost | Armor Class (AC) | Strength | Stealth | Weight
    """
    name = row[0].strip()
    if not name:
        return None
    cost = parse_cost_gp(row[1])
    ac_text = row[2].strip()

    # Parse AC: "11 + Dex modifier", "14 + Dex modifier (max 2)", "14", "+2"
    base_ac = 0
    add_dex = False
    max_dex: int | None = None

    if category == PHBArmorCategory.SHIELD:
        # Shield: "+2"
        m = re.match(r"^\+(\d+)$", ac_text)
        if m:
            base_ac = int(m.group(1))
            add_dex = False
        else:
            return None
    elif "Dex modifier" in ac_text:
        add_dex = True
        m = re.match(r"^(\d+)", ac_text)
        if m:
            base_ac = int(m.group(1))
        max_m = re.search(r"max\s+(\d+)", ac_text)
        if max_m:
            max_dex = int(max_m.group(1))
    else:
        # Heavy armor: just "14", "16", "17", "18"
        m = re.match(r"^(\d+)$", ac_text)
        if m:
            base_ac = int(m.group(1))
            add_dex = False

    # Strength
    str_required: int | None = None
    str_text = row[3].strip() if len(row) > 3 else ""
    m = re.match(r"^Str\s+(\d+)$", str_text, re.IGNORECASE)
    if m:
        str_required = int(m.group(1))

    # Stealth
    stealth_text = row[4].strip() if len(row) > 4 else ""
    stealth_disadvantage = "disadvantage" in stealth_text.lower()

    weight = parse_weight_lb(row[5]) if len(row) > 5 else 0.0

    return PHBArmor(
        name=name,
        category=category,
        cost_gp=cost,
        base_ac=base_ac,
        add_dex=add_dex,
        max_dex=max_dex,
        stealth_disadvantage=stealth_disadvantage,
        strength_required=str_required,
        weight=weight,
        is_shield=(category == PHBArmorCategory.SHIELD),
    )


# ============================================================================
# Conditions
# ============================================================================


def load_conditions(phb_root: Path) -> list[PHBCondition]:
    """Load all conditions from data/phb/Gamemastering/Conditions.md."""
    path = phb_root / "Gamemastering" / "Conditions.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    sections = split_sections(text)

    conditions: list[PHBCondition] = []
    for s in sections:
        if s.level != 2:
            continue
        name = s.title.strip()
        # Skip non-condition sections like "Exhaustion Effects" table
        if "exhaustion" in name.lower() and "effect" in name.lower():
            continue

        # Collect bullet points
        effects: list[str] = []
        for line in s.body.splitlines():
            line = line.strip()
            if line.startswith("- "):
                effects.append(line[2:].strip())

        # Special case: Exhaustion stores effects in a "Table- Exhaustion Effects" table
        if name.lower() == "exhaustion" and not effects:
            tables = find_tables(s.body)
            for _title, tbl in tables:
                if "Level" in tbl.headers and "Effect" in tbl.headers:
                    for row in tbl.rows:
                        if len(row) >= 2 and row[0].strip().isdigit():
                            effects.append(f"Level {row[0].strip()}: {row[1].strip()}")

        description = " ".join(effects)

        conditions.append(PHBCondition(
            name=name,
            description=description,
            effects=effects,
            has_levels=(name.lower() == "exhaustion"),
        ))
    return conditions


# ============================================================================
# Languages
# ============================================================================


def load_languages(phb_root: Path) -> list[PHBLanguage]:
    """Load languages from data/phb/Characterizations/Languages.md.

    The file has two tables — Standard and Exotic — with the same column
    layout: Language, Typical Speakers, Script. We tag each entry with
    its category so the engine can apply the PHB rule "choose from
    Standard unless your GM allows Exotic".
    """
    path = phb_root / "Characterizations" / "Languages.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")

    languages: list[PHBLanguage] = []
    for title, tbl in find_tables(text):
        category = ""
        if "Standard" in title:
            category = "standard"
        elif "Exotic" in title:
            category = "exotic"
        if not category:
            continue
        if "Language" not in tbl.headers:
            continue
        for row in tbl.rows:
            if not row or all(c == "" for c in row):
                continue
            name = row[0].strip()
            if not name:
                continue
            speakers = row[1].strip() if len(row) > 1 else ""
            script = row[2].strip() if len(row) > 2 else ""
            languages.append(PHBLanguage(
                name=name,
                category=category,
                typical_speakers=speakers,
                script=script,
            ))
    return languages


# ============================================================================
# Poisons, Traps, Diseases
# ============================================================================


_POISON_DELIVERY_MAP = {
    "contact": "contact",
    "ingested": "ingested",
    "inhaled": "inhaled",
    "injury": "injury",
}


def load_poisons(phb_root: Path) -> list[PHBPoison]:
    """Load the 14 sample poisons from data/phb/Gamemastering/Poisons.md.

    The file has:
    1. A List of Poisons table (Item, Type, Price/Dose).
    2. Per-poison sections like ``***Assassin's Blood (Ingested)***`` with
       full rules text. We parse the rules text to extract DC, damage dice,
       duration, and applied conditions.
    """
    path = phb_root / "Gamemastering" / "Poisons.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")

    # Pass 1: name -> (delivery, price) from the table.
    catalog: dict[str, tuple[str, float]] = {}
    for _title, tbl in find_tables(text):
        if "Item" not in tbl.headers or "Type" not in tbl.headers:
            continue
        for row in tbl.rows:
            if not row or all(c == "" for c in row):
                continue
            name = row[0].strip()
            if not name:
                continue
            delivery = row[1].strip().lower()
            cost = parse_cost_gp(row[2]) if len(row) > 2 else 0.0
            catalog[name.lower()] = (delivery, cost)

    # Pass 2: parse each ***Poison Name (Type)*** rules block.
    poisons: list[PHBPoison] = []
    sections = split_sections(text)
    for s in sections:
        if s.level != 2:
            continue
        for tname, tdesc in parse_traits(s.body):
            # Trait name looks like "Assassin's Blood (Ingested)"
            tname_clean = tname.strip()
            if "(" not in tname_clean:
                continue
            paren = tname_clean.find("(")
            name = tname_clean[:paren].strip()
            delivery_raw = tname_clean[paren + 1 : tname_clean.find(")")].strip().lower()
            delivery = _POISON_DELIVERY_MAP.get(delivery_raw, delivery_raw)

            save_dc, save_ability = _extract_save(tdesc)
            damage_dice, damage_type = _extract_damage(tdesc)
            duration = _extract_duration(tdesc)
            applies = _extract_applied_conditions(tdesc)

            _, price = catalog.get(name.lower(), (delivery, 0.0))

            poisons.append(PHBPoison(
                name=name,
                delivery=delivery,
                price_gp=price,
                save_dc=save_dc,
                save_ability=save_ability,
                damage_dice=damage_dice,
                damage_type=damage_type or "poison",
                duration=duration,
                applies_condition=applies,
                notes=tdesc,
            ))

    return poisons


_DC_SAVE_RE = re.compile(
    r"DC\s+(\d+)\s+(Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)\s+saving throw",
    re.IGNORECASE,
)
# Damage regex: matches both "1d12 poison damage" and "6 (1d12) poison damage".
_DAMAGE_RE = re.compile(
    r"(?:\d+\s+)?\(?(\d+d\d+(?:\s*[+\-]\s*\d+)?)\)?\s+(acid|cold|fire|force|lightning|necrotic|poison|psychic|radiant|thunder|bludgeoning|piercing|slashing)\s+damage",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"(?:for|until)\s+((?:\d+d\d+|\d+|a)\s+(?:round|minute|hour|day|turn)s?)",
    re.IGNORECASE,
)


def _extract_save(text: str) -> tuple[int, str]:
    """Pull 'DC N <ability> saving throw' out of the rules text."""
    m = _DC_SAVE_RE.search(text)
    if m:
        return int(m.group(1)), m.group(2).lower()
    return 10, "constitution"


def _extract_damage(text: str) -> tuple[str, str]:
    """Pull the damage expression out of the rules text."""
    m = _DAMAGE_RE.search(text)
    if m:
        return m.group(1).replace(" ", ""), m.group(2).lower()
    return "", "poison"


def _extract_duration(text: str) -> str:
    """Pull a 'for X hours/minutes/rounds' phrase."""
    m = _DURATION_RE.search(text)
    if m:
        return m.group(1)
    return ""


def _extract_applied_conditions(text: str) -> list[str]:
    """Find PHB conditions mentioned by name (poisoned, blinded, ...)."""
    found = set()
    for cond in ("blinded", "charmed", "deafened", "frightened",
                 "grappled", "incapacitated", "invisible", "paralyzed",
                 "petrified", "poisoned", "prone", "restrained", "stunned",
                 "unconscious"):
        if re.search(rf"\b{cond}\b", text, re.IGNORECASE):
            found.add(cond)
    return sorted(found)


def load_traps(phb_root: Path) -> list[PHBTrap]:
    """Load the 9 sample traps from data/phb/Gamemastering/Traps.md.

    The file is mostly prose; we look for ### sub-sections whose body
    contains the trigger effects. We extract a few key fields by regex
    and keep the rest as description text.
    """
    path = phb_root / "Gamemastering" / "Traps.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")

    traps: list[PHBTrap] = []
    sections = split_sections(text)
    for s in sections:
        if s.level != 3:  # ### Trap Name
            continue
        name = s.title.strip()
        # Skip meta sections like "Complex Traps" and "Triggering a Trap".
        # Heuristic: real trap sections contain "DC" + "saving throw".
        body = s.body
        if "saving throw" not in body.lower():
            continue
        trap_type = "magic" if "*Magic trap*" in body else "mechanical"
        save_dc, save_ability = _extract_save(body)
        damage_dice, damage_type = _extract_damage(body)
        # Detect DCs. PHB wording: "DC 15 Dexterity check", "spot the
        # trip wire is 10" — we approximate by scanning for "DC N".
        detect_dc = 10
        disarm_dc = 15
        dcs = [int(x) for x in re.findall(r"DC\s+(\d+)", body)]
        if dcs:
            detect_dc = dcs[0]
            if len(dcs) > 1:
                disarm_dc = dcs[1] if dcs[1] != detect_dc else dcs[-1]

        traps.append(PHBTrap(
            name=name,
            trap_type=trap_type,
            detect_dc=detect_dc,
            disarm_dc=disarm_dc,
            save_dc=save_dc,
            save_ability=save_ability,
            damage_dice=damage_dice,
            damage_type=damage_type or "bludgeoning",
            description=body.strip()[:500],
        ))
    return traps


def load_diseases(phb_root: Path) -> list[PHBDisease]:
    """Load the 3 sample diseases from data/phb/Gamemastering/Diseases.md."""
    path = phb_root / "Gamemastering" / "Diseases.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    sections = split_sections(text)
    diseases: list[PHBDisease] = []
    for s in sections:
        if s.level != 3:  # ### Cackle Fever, etc.
            continue
        name = s.title.strip()
        body = s.body
        save_dc, save_ability = _extract_save(body)
        # Find symptoms text — pull bullet items.
        effects: list[str] = []
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("- "):
                effects.append(line[2:].strip())
        # Incubation = first "1d4 ..." phrase
        incubation = ""
        m = re.search(r"(\d+d\d+)\s+(hours?|days?|minutes?)", body, re.IGNORECASE)
        if m:
            incubation = f"{m.group(1)} {m.group(2)}"
        diseases.append(PHBDisease(
            name=name,
            description=body.strip()[:500],
            save_dc=save_dc,
            save_ability=save_ability,
            effects_on_fail=effects,
            incubation=incubation,
        ))
    return diseases


# ============================================================================
# Monsters
# ============================================================================


# XP by Challenge Rating (DMG table — also reproduced at the end of
# ``# Monster Statistics.md``). Covers CR 0 through 30.
_XP_BY_CR: dict[float, int] = {
    0: 10, 0.125: 25, 0.25: 50, 0.5: 100,
    1: 200, 2: 450, 3: 700, 4: 1100, 5: 1800,
    6: 2300, 7: 2900, 8: 3900, 9: 5000, 10: 5900,
    11: 7200, 12: 8400, 13: 10000, 14: 11500, 15: 13000,
    16: 15000, 17: 18000, 18: 20000, 19: 22000, 20: 25000,
    21: 33000, 22: 41000, 23: 50000, 24: 62000, 25: 75000,
    26: 90000, 27: 105000, 28: 120000, 29: 135000, 30: 155000,
}


def load_monsters(phb_root: Path) -> list[Monster]:
    """Load all monster stat blocks from ``data/phb/Monsters/``.

    Skips the ``# Monster Statistics.md`` intro file. The 318 stat-block files
    follow the standard PHB format (name, italic tagline, ``**Field:**`` lines,
    ``|STR DEX CON INT WIS CHA|`` table, traits, ``###### Actions``,
    optional ``###### Reactions`` and ``###### Legendary Actions``).
    """
    monsters_dir = phb_root / "Monsters"
    if not monsters_dir.exists():
        return []

    monsters: list[Monster] = []
    for path in sorted(monsters_dir.glob("*.md")):
        if path.name.startswith("#"):
            continue
        monster = parse_monster_file(path)
        if monster is not None:
            monsters.append(monster)
    return monsters


def parse_monster_file(path: Path) -> Monster | None:
    """Parse one monster .md file into a Monster.

    Returns None if the file is too malformed to recover from. Most fields have
    defaults so partial parses still produce a usable Monster.
    """
    try:
        text = path.read_text(encoding="utf-8")
        sections = split_sections(text)
        if not sections:
            return None

        name = sections[0].title.strip()
        body = sections[0].body

        size, mtype, subtype, alignment = _parse_tagline(body)

        fields = _parse_monster_fields(body)
        ac_value, ac_desc = _parse_armor_class(fields.get("armor class", ""))
        hp_avg, hp_dice = _parse_hp(fields.get("hit points", ""))
        speed_modes = _parse_speed(fields.get("speed", ""))

        abilities = _parse_ability_table(body)

        saves = _parse_save_skills(fields.get("saving throws", ""), _SAVES_RE)
        skills = _parse_save_skills(fields.get("skills", ""), _SKILLS_RE)

        damage_res = _parse_damage_types(fields.get("damage resistances", ""))
        damage_imm = _parse_damage_types(fields.get("damage immunities", ""))
        damage_vuln = _parse_damage_types(fields.get("damage vulnerabilities", ""))

        cond_imm = _parse_condition_immunities(fields.get("condition immunities", ""))

        senses, passive_perception = _parse_senses(fields.get("senses", ""))
        languages, languages_note = _parse_languages(fields.get("languages", ""))

        cr_float, cr_text = _parse_challenge_text(fields.get("challenge", ""))
        xp = _XP_BY_CR.get(cr_float, 0)

        # Traits: ***Name***. desc — they live in sections[0].body before
        # any ``######`` separator.
        traits = parse_traits(body)
        trait_models = [MonsterTrait(name=n, description=d) for n, d in traits]
        legendary_resistances = _count_legendary_resistance(trait_models)

        actions_section = next(
            (s for s in sections if s.title.strip().lower() == "actions"), None
        )
        actions = _parse_actions(actions_section.body) if actions_section else []

        reactions_section = next(
            (s for s in sections if s.title.strip().lower() == "reactions"), None
        )
        reactions = _parse_actions(reactions_section.body) if reactions_section else []

        legendary_section = next(
            (s for s in sections if s.title.strip().lower() == "legendary actions"),
            None,
        )
        if legendary_section:
            legendary_actions, legendary_count = _parse_legendary_actions(
                legendary_section.body
            )
        else:
            legendary_actions, legendary_count = [], 0

        return Monster(
            name=name,
            size=size,
            type=mtype,
            subtype=subtype,
            alignment=alignment,
            armor_class=ac_value,
            armor_description=ac_desc,
            hp_average=hp_avg,
            hp_dice_formula=hp_dice,
            speed_walk=speed_modes.get("walk", 0),
            speed_burrow=speed_modes.get("burrow", 0),
            speed_climb=speed_modes.get("climb", 0),
            speed_fly=speed_modes.get("fly", 0),
            speed_swim=speed_modes.get("swim", 0),
            hover=bool(speed_modes.get("hover", False)),
            abilities=abilities,
            saves=saves,
            skills=skills,
            damage_resistances=damage_res,
            damage_immunities=damage_imm,
            damage_vulnerabilities=damage_vuln,
            condition_immunities=cond_imm,
            senses=senses,
            passive_perception=passive_perception,
            languages=languages,
            languages_note=languages_note,
            challenge_rating=cr_float,
            challenge_rating_text=cr_text,
            xp=xp,
            traits=trait_models,
            actions=actions,
            reactions=reactions,
            legendary_actions=legendary_actions,
            legendary_actions_count=legendary_count,
            legendary_resistances=legendary_resistances,
            source_file=path.name,
        )
    except Exception:
        # Defensive: never let one bad file kill the whole loader
        return None


# --- Parsing helpers ---------------------------------------------------------


# *Size type (subtype), alignment*
_TAGLINE_RE = re.compile(
    r"\*([A-Z][a-z]+)\s+(\w+)(?:\s*\((\w[^)]*)\))?,\s*(.+?)\*"
)


def _parse_tagline(body: str) -> tuple[MonsterSize, MonsterType, Optional[str], str]:
    """Extract size, type, optional subtype, and alignment from the tagline."""
    m = _TAGLINE_RE.search(body)
    if not m:
        return MonsterSize.MEDIUM, MonsterType.HUMANOID, None, "unaligned"

    size_str = m.group(1).lower()
    type_str = m.group(2).lower()
    subtype = m.group(3)
    alignment = m.group(4).strip()

    try:
        size = MonsterSize(size_str.capitalize())
    except ValueError:
        size = MonsterSize.MEDIUM

    try:
        mtype = MonsterType(type_str)
    except ValueError:
        mtype = MonsterType.MONSTROSITY  # catch-all bucket

    return size, mtype, subtype, alignment


_AC_RE = re.compile(r"^\s*(\d+)\s*(?:\(([^)]+)\))?\s*$")
_HP_RE = re.compile(r"^\s*(\d+)\s*\(([^)]+)\)\s*$")


def _parse_armor_class(text: str) -> tuple[int, str]:
    """``15 (leather armor, shield)`` -> (15, '(leather armor, shield)')."""
    m = _AC_RE.match(text.strip())
    if not m:
        return 10, ""
    desc = f"({m.group(2)})" if m.group(2) else ""
    return int(m.group(1)), desc


def _parse_hp(text: str) -> tuple[int, str]:
    """``7 (2d6)`` -> (7, '2d6')."""
    m = _HP_RE.match(text.strip())
    if not m:
        return 1, "1d4"
    return int(m.group(1)), m.group(2).replace(" ", "")


_SPEED_RE = re.compile(r"(?:(\w+)\s+)?(\d+)\s*ft\.", re.IGNORECASE)
_HOVER_RE = re.compile(r"fly\s+\d+\s*ft\.\s*\(hover\)", re.IGNORECASE)


def _parse_speed(text: str) -> dict[str, int | bool]:
    """``40 ft., climb 40 ft., fly 80 ft. (hover)`` -> dict of modes."""
    modes: dict[str, int | bool] = {"walk": 0}
    hover = bool(_HOVER_RE.search(text))
    for m in _SPEED_RE.finditer(text):
        mode = (m.group(1) or "speed").lower()
        if mode == "speed":
            mode = "walk"
        modes[mode] = int(m.group(2))
    modes["hover"] = hover
    return modes


# | STR | DEX | CON | INT | WIS | CHA |  +  separator  +  values row
_ABILITY_TABLE_RE = re.compile(
    r"\|\s*STR\s*\|[^\n]+\|\s*CHA\s*\|\s*\n"
    r"\|[^\n]+\|\s*\n"
    r"\|\s*(\d+)\s*\([+-]?\d+\)\s*\|"
    r"\s*(\d+)\s*\([+-]?\d+\)\s*\|"
    r"\s*(\d+)\s*\([+-]?\d+\)\s*\|"
    r"\s*(\d+)\s*\([+-]?\d+\)\s*\|"
    r"\s*(\d+)\s*\([+-]?\d+\)\s*\|"
    r"\s*(\d+)\s*\([+-]?\d+\)\s*\|",
)


# Monster stat-block fields use ``**Field** value`` (no colon), unlike class
# or spell files which use ``**Field:** value``. This regex handles both.
_MONSTER_FIELD_RE = re.compile(
    r"^\*\*([^*\n]+)\*\*\s+(.+?)(?=\n\*\*|\n###### |\Z)",
    re.MULTILINE | re.DOTALL,
)


def _parse_monster_fields(body: str) -> dict[str, str]:
    """Parse ``**Field** value`` (or ``**Field:** value``) blocks from a monster body.

    Unlike ``parse_fields`` (which expects the colon style used by class/spell
    files), monster stat blocks omit the colon: ``**Armor Class** 15 (...)``.
    Multi-line values are collapsed to a single line.
    """
    fields: dict[str, str] = {}
    for m in _MONSTER_FIELD_RE.finditer(body):
        key = m.group(1).strip().rstrip(":").lower()
        value = re.sub(r"\s+", " ", m.group(2)).strip()
        fields[key] = value
    return fields


def _parse_ability_table(body: str) -> AbilityScores:
    """Parse the six-cell ``|STR...CHA|`` table row into AbilityScores."""
    m = _ABILITY_TABLE_RE.search(body)
    if not m:
        return AbilityScores(
            strength=10, dexterity=10, constitution=10,
            intelligence=10, wisdom=10, charisma=10,
        )
    return AbilityScores(
        strength=int(m.group(1)),
        dexterity=int(m.group(2)),
        constitution=int(m.group(3)),
        intelligence=int(m.group(4)),
        wisdom=int(m.group(5)),
        charisma=int(m.group(6)),
    )


_SAVES_RE = re.compile(
    r"(Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)"
    r"\s*\+?\s*(\d+)",
    re.IGNORECASE,
)

_SKILLS_RE = re.compile(
    r"(Acrobatics|Animal Handling|Arcana|Athletics|Deception|History|"
    r"Insight|Intimidation|Investigation|Medicine|Nature|Perception|"
    r"Performance|Persuasion|Religion|Sleight of Hand|Stealth|Survival)"
    r"\s*\+?\s*(\d+)",
    re.IGNORECASE,
)


def _parse_save_skills(text: str, pattern: re.Pattern[str]) -> dict[str, int]:
    """Parse 'Dex +6, Con +13' or 'Stealth +6, Perception +13' into a dict."""
    result: dict[str, int] = {}
    for m in pattern.finditer(text):
        result[m.group(1).lower()] = int(m.group(2))
    return result


def _parse_damage_types(text: str) -> list[str]:
    """Parse ``fire`` or ``cold, lightning, necrotic`` into a list."""
    if not text:
        return []
    # Split on semicolons first (separates clauses like
    # ``poison; bludgeoning, piercing, and slashing from nonmagical attacks``).
    types: list[str] = []
    for clause in text.split(";"):
        for p in re.split(r",|\band\b", clause):
            p = p.strip().lower()
            if p:
                types.append(p)
    return types


_KNOWN_CONDITIONS = {
    "blinded", "charmed", "deafened", "frightened", "grappled",
    "incapacitated", "invisible", "paralyzed", "petrified", "poisoned",
    "prone", "restrained", "stunned", "unconscious", "exhaustion",
}


def _parse_condition_immunities(text: str) -> list[str]:
    """``charmed, exhaustion, frightened, paralyzed, poisoned`` -> list."""
    if not text:
        return []
    found: list[str] = []
    for part in re.split(r",|\band\b", text):
        name = part.strip().lower()
        if name in _KNOWN_CONDITIONS:
            found.append(name)
    return found


_SENSES_RE = re.compile(r"([A-Za-z][\w\s]*?)\s+(\d+)\s*ft\.", re.IGNORECASE)
_PP_RE = re.compile(r"passive\s+Perception\s+(\d+)", re.IGNORECASE)


def _parse_senses(text: str) -> tuple[dict[str, int], int]:
    """``darkvision 60 ft., passive Perception 9`` -> ({...}, 9)."""
    senses: dict[str, int] = {}
    for m in _SENSES_RE.finditer(text):
        sense_name = m.group(1).strip().lower()
        if sense_name in {"passive perception"}:
            continue
        senses[sense_name] = int(m.group(2))

    pp_match = _PP_RE.search(text)
    passive_perception = int(pp_match.group(1)) if pp_match else 10

    return senses, passive_perception


def _parse_languages(text: str) -> tuple[list[str], str]:
    """``Common, Goblin`` -> (['Common', 'Goblin'], '').

    ``Common plus up to five other languages`` -> (['Common'], 'plus up to five ...').
    ``-`` or empty -> ([], '').
    """
    if not text or text.strip() in {"-", "—", "None"}:
        return [], ""
    note_match = re.search(r"\s+(plus|—|\(.*$).*", text, re.IGNORECASE)
    if note_match:
        note = note_match.group(0).strip()
        lang_text = text[: note_match.start()]
    else:
        note = ""
        lang_text = text
    langs = [l.strip() for l in lang_text.split(",") if l.strip()]
    return langs, note


def _parse_challenge_text(text: str) -> tuple[float, str]:
    """``1/4`` -> (0.25, '1/4'); ``17`` -> (17.0, '17')."""
    m = re.match(r"\s*(\d+(?:/\d+)?)", text)
    if not m:
        return 0.0, "0"
    cr_str = m.group(1)
    if "/" in cr_str:
        num, denom = cr_str.split("/")
        return float(num) / float(denom), cr_str
    return float(cr_str), cr_str


def _count_legendary_resistance(traits: list[MonsterTrait]) -> int:
    """Pull the X out of a ``Legendary Resistance (X/Day)`` trait."""
    for t in traits:
        if "legendary resistance" in t.name.lower():
            m = re.search(r"\((\d+)/Day\)", t.name)
            if m:
                return int(m.group(1))
    return 0


# --- Action / legendary action parsing ---------------------------------------


# ***Action Name***. description  (regular Actions section)
_ACTION_RE = re.compile(
    r"\*\*\*(.+?)\*\*\*\.\s*(.+?)(?=\n\*\*\*|\n### |\n## |\n###### |\Z)",
    re.DOTALL,
)

# **Option Name**. description  (Legendary Actions options use bold-not-italic)
_LEGENDARY_OPT_RE = re.compile(
    r"\*\*(.+?)\*\*\.\s*(.+?)(?=\n\*\*|\n### |\n## |\n###### |\Z)",
    re.DOTALL,
)

_LEGENDARY_COUNT_RE = re.compile(
    r"can\s+take\s+(\d+)\s+legendary\s+actions", re.IGNORECASE
)

_RECHARGE_RE = re.compile(r"\(Recharge\s+(\d+-\d+)\)", re.IGNORECASE)
_USAGES_RE = re.compile(r"\((\d+/Day)\)", re.IGNORECASE)
_COST_RE = re.compile(r"\(Costs\s+(\d+)\s+Actions?\)", re.IGNORECASE)

# *Melee Weapon Attack:* +4 to hit, reach 5 ft., one target.
_ATTACK_TYPE_RE = re.compile(
    r"\*?(Melee|Ranged)\s+(Weapon|Spell)\s+Attack:\*?\s*\+(\d+)\s+to\s+hit",
    re.IGNORECASE,
)
_REACH_RE = re.compile(r"reach\s+(\d+)\s*ft", re.IGNORECASE)
_RANGE_RE = re.compile(r"range\s+(\d+)/(\d+)\s*ft", re.IGNORECASE)

# *Hit:* 5 (1d6+2) slashing damage.
_HIT_DAMAGE_RE = re.compile(
    r"\*?Hit:\*?\s*\d+\s*\((\d+d\d+(?:\s*[+\-]\s*\d+)?)\)\s+(\w+)\s+damage",
    re.IGNORECASE,
)
# *Hit:* 1d6+2 slashing damage. (no average prefix)
_HIT_DAMAGE_PLAIN_RE = re.compile(
    r"\*?Hit:\*?\s*(\d+d\d+(?:\s*[+\-]\s*\d+)?)\s+(\w+)\s+damage",
    re.IGNORECASE,
)
# "plus 7 (2d6) fire damage" — rider
_ADDITIONAL_DAMAGE_RE = re.compile(
    r"plus\s+\d+\s+\((\d+d\d+)\)\s+(\w+)\s+damage",
    re.IGNORECASE,
)


def _parse_actions(body: str) -> list[MonsterAction]:
    """Parse the Actions section body into a list of MonsterAction."""
    actions: list[MonsterAction] = []
    for m in _ACTION_RE.finditer(body):
        raw_name = _strip_inline(m.group(1))
        description = _collapse_ws(m.group(2))
        actions.append(_build_action(raw_name, description))
    return actions


def _parse_legendary_actions(body: str) -> tuple[list[MonsterAction], int]:
    """Parse the Legendary Actions section body.

    Returns (actions, count). ``count`` is how many legendary actions can be
    taken per round (e.g. ``can take 3 legendary actions`` -> 3). Defaults
    to 0 if the section is present but malformed.
    """
    count_match = _LEGENDARY_COUNT_RE.search(body)
    count = int(count_match.group(1)) if count_match else 0

    actions: list[MonsterAction] = []
    for m in _LEGENDARY_OPT_RE.finditer(body):
        raw_name = _strip_inline(m.group(1))
        description = _collapse_ws(m.group(2))
        # Skip the section preamble — it has no name like that, but defend
        # against accidentally matching the "can take 3 legendary actions"
        # opener by checking for empty description.
        if not raw_name or not description:
            continue
        actions.append(_build_action(raw_name, description))

    return actions, count


def _build_action(raw_name: str, description: str) -> MonsterAction:
    """Construct a MonsterAction, parsing modifiers from the name and details
    from the description body.
    """
    clean_name = raw_name
    recharge: Optional[str] = None
    usages: Optional[str] = None
    cost: Optional[int] = None

    m = _RECHARGE_RE.search(clean_name)
    if m:
        recharge = m.group(1)
        clean_name = _RECHARGE_RE.sub("", clean_name)
    m = _USAGES_RE.search(clean_name)
    if m:
        usages = m.group(1)
        clean_name = _USAGES_RE.sub("", clean_name)
    m = _COST_RE.search(clean_name)
    if m:
        cost = int(m.group(1))
        clean_name = _COST_RE.sub("", clean_name)

    # Tidy leftover parens / whitespace
    clean_name = re.sub(r"\s+", " ", clean_name).strip().strip(".").strip()
    if clean_name.endswith(")") and "(" not in clean_name:
        clean_name = clean_name[:-1].strip()

    # Attack details
    attack_type: Optional[str] = None
    attack_bonus: Optional[int] = None
    reach_ft: Optional[int] = None
    range_normal: Optional[int] = None
    range_long: Optional[int] = None
    damage_dice: Optional[str] = None
    damage_type: Optional[str] = None
    addl_dice: Optional[str] = None
    addl_type: Optional[str] = None

    m = _ATTACK_TYPE_RE.search(description)
    if m:
        attack_type = f"{m.group(1).lower()}_{m.group(2).lower()}"
        attack_bonus = int(m.group(3))
    m = _REACH_RE.search(description)
    if m:
        reach_ft = int(m.group(1))
    m = _RANGE_RE.search(description)
    if m:
        range_normal = int(m.group(1))
        range_long = int(m.group(2))

    m = _HIT_DAMAGE_RE.search(description) or _HIT_DAMAGE_PLAIN_RE.search(description)
    if m:
        damage_dice = m.group(1).replace(" ", "")
        damage_type = m.group(2).lower()
    m = _ADDITIONAL_DAMAGE_RE.search(description)
    if m:
        addl_dice = m.group(1).replace(" ", "")
        addl_type = m.group(2).lower()

    return MonsterAction(
        name=clean_name,
        description=description,
        recharge=recharge,
        usages=usages,
        cost=cost,
        attack_type=attack_type,
        attack_bonus=attack_bonus,
        reach_ft=reach_ft,
        range_normal_ft=range_normal,
        range_long_ft=range_long,
        damage_dice=damage_dice,
        damage_type=damage_type,
        additional_damage_dice=addl_dice,
        additional_damage_type=addl_type,
    )


def _strip_inline(text: str) -> str:
    """Remove residual markdown emphasis (** * __ _) from inline text."""
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    return text.strip()


def _collapse_ws(text: str) -> str:
    """Collapse runs of whitespace/newlines into single spaces."""
    return re.sub(r"\s+", " ", text).strip()


# ============================================================================
# Backgrounds (Phase 25c)
# ============================================================================


def load_backgrounds(phb_root: Path) -> list[Background]:
    """Load all backgrounds from ``data/phb/Characterizations/Backgrounds.md``.

    The file contains one ``## BackgroundName`` section per background, with
    a narrative description, four ``**Field:**`` lines, and a single
    ``### Feature: FeatureName`` block (parsed as a separate section by
    ``split_sections``). Suggested Characteristics tables are skipped
    (they're flavor, not mechanics).
    """
    path = phb_root / "Characterizations" / "Backgrounds.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    sections = split_sections(text)

    # Build a flat list of (level, title, body) for the whole file, and
    # then group them: a ## background "owns" all subsequent ### sections
    # until the next ## background.
    backgrounds: list[Background] = []
    current_bg_section = None  # type: ignore[assignment]
    for section in sections:
        if section.level == 2:
            # If we have a current background, finalize it.
            if current_bg_section is not None:
                bg = _parse_background_section(
                    current_bg_section["section"],
                    current_bg_section["feature_section"],
                )
                if bg is not None:
                    backgrounds.append(bg)
            title = section.title.strip()
            if title.lower() in {
                "backgrounds", "customizing a background",
                "proficiencies", "languages", "equipment",
                "suggested characteristics",
            }:
                current_bg_section = None
                continue
            current_bg_section = {"section": section, "feature_section": None}
        elif section.level == 3 and current_bg_section is not None:
            if section.title.lower().startswith("feature:"):
                current_bg_section["feature_section"] = section

    # Finalize the last background.
    if current_bg_section is not None:
        bg = _parse_background_section(
            current_bg_section["section"],
            current_bg_section["feature_section"],
        )
        if bg is not None:
            backgrounds.append(bg)
    return backgrounds


def _parse_background_section(
    section, feature_section,
) -> Background | None:
    """Parse a single ``## Background Name`` section."""
    name = section.title.strip()
    body = section.body

    # Description: lines BEFORE the first **Field:** line.
    fields = parse_fields(body)
    if not fields:
        # No fields at all — malformed, skip.
        return None

    # Find where fields begin so we can split description from fields.
    field_start = re.search(r"^\*\*[^*]+?:\*\*", body, re.MULTILINE)
    if field_start:
        desc_raw = body[: field_start.start()]
        description = _first_paragraph(desc_raw)
    else:
        description = ""

    # Skill proficiencies (comma-separated free-text names)
    skills_raw = fields.get("skill proficiencies", "")
    skill_profs = _parse_csv_list(skills_raw)

    # Tool proficiencies (comma-separated free-text names)
    tools_raw = fields.get("tool proficiencies", "")
    tool_profs = _parse_csv_list(tools_raw)

    # Languages: either "Two of your choice" / "Any one of your choice"
    # or a specific comma-separated list. We keep the raw text so the
    # wizard can honor the choice rule.
    languages = fields.get("languages", "").strip()

    # Equipment: free text
    equipment = fields.get("equipment", "").strip()

    # Feature: the ### section is passed in by the caller.
    feature_name = ""
    feature_description = ""
    if feature_section is not None:
        # Title is "Feature: Shelter of the Faithful"
        m = re.match(r"^Feature:\s*(.+?)\s*$", feature_section.title, re.IGNORECASE)
        if m:
            feature_name = m.group(1).strip()
        feature_description = _first_paragraph(feature_section.body)

    return Background(
        name=name,
        description=description,
        skill_proficiencies=skill_profs,
        tool_proficiencies=tool_profs,
        languages=languages,
        equipment=equipment,
        feature_name=feature_name,
        feature_description=feature_description,
    )


def _parse_csv_list(text: str) -> list[str]:
    """Parse a comma-separated list of free-text names."""
    if not text:
        return []
    parts = [p.strip() for p in text.split(",")]
    return [p for p in parts if p]


# ============================================================================
# Tools (Phase 25c)
# ============================================================================


def load_tools(phb_root: Path) -> list[PHBTool]:
    """Load all tools from ``data/phb/Equipment/Tools.md``.

    Parses the **Table- Tools** block (Item | Cost | Weight) and the
    prose ``***Category***. description`` blocks. The first table column
    uses italic ``*X*`` rows for category headers and ``~ X`` rows for
    children of the current category; bare rows are standalone tools.
    """
    path = phb_root / "Equipment" / "Tools.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")

    # Parse prose descriptions: ***Category***. description
    descriptions: dict[str, str] = {}
    for cat_name, cat_desc in parse_traits(text):
        descriptions[cat_name] = cat_desc

    tools: list[PHBTool] = []
    tables = find_tables(text)
    for _title, tbl in tables:
        # Look for the Tools table — Item | Cost | Weight
        joined = " | ".join(tbl.headers).lower()
        if "item" not in joined or "cost" not in joined or "weight" not in joined:
            continue
        current_cat = ToolCategory.OTHER
        # Track whether the last row was a `~` (child) row; if so, the
        # next standalone row marks the end of that subgroup and the
        # rows that follow it are standalone KITs.
        last_was_child = False
        for row in tbl.rows:
            if not row or all(c == "" for c in row):
                continue
            first_cell = row[0].strip()
            if not first_cell:
                continue
            # Italic category header: "*Artisan's tools*"
            if first_cell.startswith("*") and first_cell.endswith("*"):
                cat_str = first_cell.strip("*").strip().lower()
                if "artisan" in cat_str:
                    current_cat = ToolCategory.ARTISAN
                elif "gaming" in cat_str:
                    current_cat = ToolCategory.GAMING_SET
                elif "musical" in cat_str:
                    current_cat = ToolCategory.MUSICAL_INSTRUMENT
                elif "vehicle" in cat_str:
                    current_cat = ToolCategory.VEHICLE
                else:
                    current_cat = ToolCategory.OTHER
                last_was_child = False
                continue
            # Child row: "~ Alchemist's supplies"
            if first_cell.startswith("~"):
                name = first_cell.lstrip("~").strip()
                last_was_child = True
            else:
                name = first_cell
                # A standalone row after a child row (or after one of
                # the categorized groups) is a KIT (Disguise, Forgery,
                # Herbalism, Navigator's, Poisoner's, Thieves').
                if last_was_child or current_cat in {
                    ToolCategory.ARTISAN, ToolCategory.GAMING_SET,
                    ToolCategory.MUSICAL_INSTRUMENT,
                }:
                    current_cat = ToolCategory.KIT
                last_was_child = False
            if not name:
                continue
            # Skip the Vehicles footnote row entirely (no cost/weight).
            # Real vehicles live in Transportation.md (not yet loaded).
            if name.lower().startswith("vehicles"):
                continue
            # Skip the Vehicles footnote row entirely (no cost/weight)
            if current_cat == ToolCategory.VEHICLE:
                continue
            cost = parse_cost_gp(row[1]) if len(row) > 1 else 0.0
            weight = parse_weight_lb(row[2]) if len(row) > 2 else 0.0
            # Description: match the current category if the row belongs
            # to a named category (Artisan's Tools / Musical Instrument
            # etc.). Kits (Disguise, Thieves' tools, ...) get their own
            # specific description.
            description = _match_tool_description(name, descriptions)
            tools.append(
                PHBTool(
                    name=name,
                    category=current_cat,
                    cost_gp=cost,
                    weight=weight,
                    description=description,
                )
            )
    return tools


def _match_tool_description(name: str, descriptions: dict[str, str]) -> str:
    """Find the prose description for a tool name.

    Matches the first category description whose name is contained in the
    tool name (so "Alchemist's supplies" → "Artisan's Tools" prose), or
    a direct key match for kits ("Disguise Kit", "Thieves' Tools", ...).
    """
    target = name.lower()
    # Direct match wins (e.g. "Disguise Kit", "Thieves' Tools")
    for key, desc in descriptions.items():
        if key.lower() == target:
            return desc
    # Artisan's tools, gaming sets, musical instruments share category prose
    # but get their description from the category name in the key.
    artisan_keywords = {
        "alchemist", "brewer", "calligrapher", "carpenter", "cartographer",
        "cobbler", "cook", "glassblower", "jeweler", "leatherworker", "mason",
        "painter", "potter", "smith", "tinker", "weaver", "woodcarver",
    }
    if any(k in target for k in artisan_keywords):
        return descriptions.get("Artisan's Tools", "")
    musical_keywords = {
        "bagpipes", "drum", "dulcimer", "flute", "lute", "lyre", "horn",
        "pan flute", "shawm", "viol",
    }
    if any(k in target for k in musical_keywords):
        return descriptions.get("Musical Instrument", "")
    if "dice" in target or "playing card" in target:
        return descriptions.get("Gaming Set", "")
    return ""


# ============================================================================
# Adventuring Gear (Phase 25c)
# ============================================================================


def load_gear(phb_root: Path) -> list[PHBGear]:
    """Load all adventuring gear from ``data/phb/Equipment/Gear.md``.

    Combines two sources:
    1. The prose block at the top (``***Item***. description``) — items
       with rules but no cost/weight table entry.
    2. The **Table- Adventuring Gear** table — items with cost/weight.

    Each item is unique by name; if a name appears in both, the table
    row wins for cost/weight, and the prose description is preserved.
    """
    path = phb_root / "Equipment" / "Gear.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")

    # 1. Prose descriptions
    prose_descriptions: dict[str, str] = {}
    for item_name, desc in parse_traits(text):
        # Normalize item names (strip trailing period etc.)
        prose_descriptions[item_name.strip()] = desc

    # 2. Table rows
    gear_by_name: dict[str, PHBGear] = {}
    tables = find_tables(text)
    for _title, tbl in tables:
        joined = " | ".join(tbl.headers).lower()
        if "item" not in joined or "cost" not in joined or "weight" not in joined:
            continue
        current_cat = GearCategory.STANDARD
        last_was_child = False
        for row in tbl.rows:
            if not row or all(c == "" for c in row):
                continue
            first_cell = row[0].strip()
            if not first_cell:
                continue
            # Italic subgroup: "*Ammunition*", "*Arcane focus*", etc.
            if first_cell.startswith("*") and first_cell.endswith("*"):
                cat_str = first_cell.strip("*").strip().lower()
                if "ammunition" in cat_str:
                    current_cat = GearCategory.AMMUNITION
                elif "arcane" in cat_str:
                    current_cat = GearCategory.ARCANE_FOCUS
                elif "druidic" in cat_str:
                    current_cat = GearCategory.DRUIDIC_FOCUS
                elif "holy" in cat_str:
                    current_cat = GearCategory.HOLY_SYMBOL
                else:
                    current_cat = GearCategory.STANDARD
                last_was_child = False
                continue
            # Child row: "~ Arrows (20)"
            if first_cell.startswith("~"):
                name = first_cell.lstrip("~").strip()
                last_was_child = True
            else:
                name = first_cell
                # Standalone row after a child row: end of subgroup,
                # back to STANDARD.
                if last_was_child and current_cat != GearCategory.STANDARD:
                    current_cat = GearCategory.STANDARD
                last_was_child = False
            if not name:
                continue
            cost = parse_cost_gp(row[1]) if len(row) > 1 else 0.0
            weight = parse_weight_lb(row[2]) if len(row) > 2 else 0.0
            # Look up prose description (case-insensitive)
            desc = ""
            for k, v in prose_descriptions.items():
                if k.lower() == name.lower():
                    desc = v
                    break
            gear_by_name[name] = PHBGear(
                name=name,
                category=current_cat,
                cost_gp=cost,
                weight=weight,
                description=desc,
            )

    # Add prose-only items (no table row) so they're still discoverable.
    for name, desc in prose_descriptions.items():
        if name not in gear_by_name and not _is_subitem(name):
            gear_by_name[name] = PHBGear(
                name=name, description=desc,
            )

    return list(gear_by_name.values())


def _is_subitem(name: str) -> bool:
    """Some prose entries are sub-items (e.g. "Quiver", "Lantern, Hooded")
    that belong to a parent item. Skip them in the top-level gear list to
    avoid duplicate-looking entries.
    """
    return False  # keep all for now; the wizard can filter


# ============================================================================
# Equipment Packs (Phase 25c)
# ============================================================================


def load_packs(phb_root: Path) -> list[PHBEquipmentPack]:
    """Load equipment packs from ``data/phb/Equipment/Gear.md``.

    Packs live in the ``**Equipment Packs**`` section, formatted as
    ``***Pack Name (NN gp)***. Includes ...``. Returns one
    :class:`PHBEquipmentPack` per pack, with cost parsed and contents
    split into a list of item names.
    """
    path = phb_root / "Equipment" / "Gear.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")

    packs: list[PHBEquipmentPack] = []
    # Find the "**Equipment Packs**" section so we don't capture the
    # prose items above (which use the same ***X***. ... format).
    marker = "**Equipment Packs**"
    marker_idx = text.find(marker)
    if marker_idx == -1:
        return []
    section_text = text[marker_idx:]

    for raw_name, raw_desc in parse_traits(section_text):
        # Raw name format: "Burglar's Pack (16 gp)"
        m = re.match(r"^(.+?)\s*\((\d+(?:\.\d+)?)\s*gp\)\s*$", raw_name)
        if not m:
            continue
        pack_name = m.group(1).strip()
        cost_gp = float(m.group(2))
        # Contents: text after "Includes ..." — split on ",", " and "
        includes_m = re.search(r"Includes\s+(.+)", raw_desc, re.IGNORECASE | re.DOTALL)
        contents: list[str] = []
        if includes_m:
            raw_items = includes_m.group(1)
            # Drop trailing sentence after the first period that ends a sentence
            raw_items = re.split(r"\.\s+", raw_items, maxsplit=1)[0]
            # Split on commas / " and "
            parts = re.split(r",|\band\b", raw_items)
            contents = [_clean_inline(p).strip() for p in parts if p.strip()]
        packs.append(
            PHBEquipmentPack(
                name=pack_name,
                cost_gp=cost_gp,
                contents=contents,
                description=raw_desc,
            )
        )
    return packs


# ============================================================================
# Magic Items (Phase 25d)
# ============================================================================


def load_magic_items(phb_root: Path) -> list[MagicItem]:
    """Load all magic items from ``data/phb/Treasure/``.

    Each item lives in its own .md file with the format:

        ### Item Name
        *Type, rarity (requires attunement)*
        Description paragraphs...

    Tagline parsing handles the common shapes:
    - ``*Weapon (any sword), legendary*`` — no attunement
    - ``*Ring, rare (requires attunement)*`` — attunement, any class
    - ``*Weapon (any sword), legendary (requires attunement by a paladin)*`` — restricted
    - ``*Potion, rarity varies*`` — uncommon default fallback
    - Generic ``*Weapon (any), uncommon (+1), rare (+2), or very rare (+3)*`` — multi-rarity

    Index files starting with ``#`` or ``##`` are skipped (chapter
    intros, Sentient Magic, Artifacts).
    """
    treasure_dir = phb_root / "Treasure"
    if not treasure_dir.exists():
        return []

    items: list[MagicItem] = []
    for path in sorted(treasure_dir.glob("*.md")):
        # Skip chapter intro files ("# Magic Items.md") and section
        # files ("## Artifacts.md", "## Sentient Magic.md").
        if path.name.startswith("#") or path.name.startswith("##"):
            continue
        item = _parse_magic_item_file(path)
        if item is not None:
            items.append(item)
    return items


def _parse_magic_item_file(path: Path) -> MagicItem | None:
    """Parse a single magic item .md file."""
    text = path.read_text(encoding="utf-8")
    sections = split_sections(text)
    if not sections:
        return None
    # Each file has one ### section with the item name as title.
    section = sections[0]
    name = section.title.strip()
    body = section.body

    # Tagline: first non-empty italic line in the body.
    tagline = ""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("*") and stripped.endswith("*"):
            tagline = stripped
            break
    if not tagline:
        return None

    item_type, rarity, attunement = _parse_magic_item_tagline(tagline)
    if item_type is None or rarity is None:
        return None

    # Description: everything in the body EXCEPT the tagline line.
    desc_lines = [
        ln for ln in body.splitlines()
        if ln.strip() != tagline
    ]
    description = _collapse_ws("\n".join(desc_lines))

    return MagicItem(
        name=name,
        item_type=item_type,
        rarity=rarity,
        attunement_requirement=attunement,
        tagline=tagline,
        description=description,
        source_file=str(path.relative_to(phb_root_for_path(path))),
    )


def _parse_magic_item_tagline(tagline: str) -> tuple[MagicItemType | None, Rarity | None, str]:
    """Parse ``*Type, rarity (attunement)*`` into structured fields.

    Returns ``(item_type, rarity, attunement_requirement)``. Either
    ``item_type`` or ``rarity`` may be ``None`` if unparseable; the
    caller should skip the item in that case.
    """
    # Strip leading/trailing asterisks.
    raw = tagline.strip().lstrip("*").rstrip("*").strip()
    if not raw:
        return None, None, ""
    # Split on commas. The first chunk is the type (with optional
    # subtype in parens). The second chunk holds the rarity and the
    # optional attunement clause.
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) < 2:
        return None, None, ""

    type_str = parts[0].lower()
    item_type = _classify_type(type_str)
    if item_type is None:
        return None, None, ""

    # Second chunk may include "rarity (requires attunement ...)".
    # Sometimes split into 2+ parts, e.g. "rare (requires attunement)",
    # "rarity varies", "uncommon (+1), rare (+2), or very rare (+3)".
    rest = " ".join(parts[1:]).strip()
    rarity, attunement = _parse_rarity_and_attunement(rest)
    if rarity is None:
        return None, None, ""
    return item_type, rarity, attunement


def _classify_type(type_str: str) -> MagicItemType | None:
    """Map the leading type word to a :class:`MagicItemType` enum."""
    type_str = type_str.lower().strip()
    if "weapon" in type_str:
        return MagicItemType.WEAPON
    if "armor" in type_str:
        return MagicItemType.ARMOR
    if "shield" in type_str:
        return MagicItemType.SHIELD
    if "potion" in type_str:
        return MagicItemType.POTION
    if "ring" in type_str:
        return MagicItemType.RING
    if "rod" in type_str:
        return MagicItemType.ROD
    if "scroll" in type_str:
        return MagicItemType.SCROLL
    if "staff" in type_str:
        return MagicItemType.STAFF
    if "wand" in type_str:
        return MagicItemType.WAND
    if "wondrous" in type_str:
        return MagicItemType.WONDROUS
    return None


def _parse_rarity_and_attunement(rest: str) -> tuple[Rarity | None, str]:
    """Extract rarity and attunement clause from the rest of the tagline.

    Examples::

        "rare" -> ("rare", "")
        "uncommon (requires attunement)" -> ("uncommon", "by any class")
        "legendary (requires attunement by a paladin)" -> ("legendary", "by a paladin")
        "uncommon (+1), rare (+2), or very rare (+3)" -> ("uncommon", "")
        "rarity varies" -> ("uncommon", "")  # default fallback
    """
    lower = rest.lower()
    # Multi-rarity line: pick the lowest tier (+1 = uncommon) as default.
    if "uncommon" in lower and ("rare" in lower or "+2" in lower):
        rarity = Rarity.UNCOMMON
    elif "very rare" in lower and ("+3" in lower or "very rare" in lower):
        rarity = Rarity.VERY_RARE
    elif "very rare" in lower:
        rarity = Rarity.VERY_RARE
    elif "legendary" in lower:
        rarity = Rarity.LEGENDARY
    elif "artifact" in lower:
        rarity = Rarity.ARTIFACT
    elif "uncommon" in lower:
        # Must check AFTER "very rare" / "legendary" since none of
        # those contain "uncommon" but plain "uncommon" must win
        # over plain "rare" for "Weapon, uncommon" tags.
        rarity = Rarity.UNCOMMON
    elif "common" in lower:
        # Only matches bare "common" (not "uncommon" since uncommon
        # is checked first).
        rarity = Rarity.COMMON
    elif "rare" in lower:
        rarity = Rarity.RARE
    elif "rarity varies" in lower:
        # Potion of Healing and similar — default to uncommon.
        rarity = Rarity.UNCOMMON
    else:
        return None, ""

    # Attunement: extract the parenthetical clause.
    attunement = ""
    m = re.search(r"\((requires\s+attunement[^)]*)\)", rest, re.IGNORECASE)
    if m:
        clause = m.group(1).strip()
        # Strip the "requires attunement" prefix; keep the rest as
        # the requirement ("by a paladin", "by a spellcaster", ...).
        # If nothing follows, attunement is "by any class".
        clause = re.sub(
            r"^requires\s+attunement\s*", "", clause, flags=re.IGNORECASE,
        ).strip()
        attunement = clause if clause else "by any class"

    return rarity, attunement


def phb_root_for_path(path: Path) -> Path:
    """Helper to compute the PHB root from a file path (best-effort).

    Used by ``_parse_magic_item_file`` to record the relative source
    file. Walks up until it finds the ``Treasure`` parent.
    """
    cur = path.parent
    while cur.parent != cur:
        if cur.name == "Treasure":
            return cur.parent
        cur = cur.parent
    # Fallback: try a fixed number of levels up.
    try:
        return path.parents[2]
    except IndexError:
        return path.parent


# ============================================================================
# Mounts and Vehicles (Phase 25e)
# ============================================================================


def load_mounts(phb_root: Path) -> list[Mount]:
    """Load mounts from the "Mounts and Other Animals" table in
    ``data/phb/Equipment/Transportation.md``.

    Each row has: name, cost, speed, carrying capacity.
    """
    text = _read_transportation(phb_root)
    if text is None:
        return []
    mounts: list[Mount] = []
    for title, table in find_tables(text):
        if "mounts and other animals" not in title.lower():
            continue
        for row in table.rows:
            # Skip empty rows
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) < 4:
                continue
            name = _clean_inline(row[0]).strip()
            cost = parse_cost_gp(row[1])
            speed = _parse_speed_ft(row[2])
            capacity = _parse_capacity_lb(row[3])
            if not name:
                continue
            mounts.append(
                Mount(
                    name=name,
                    cost_gp=cost,
                    speed_ft=speed,
                    carrying_capacity_lb=capacity,
                )
            )
    return mounts


def load_vehicles(phb_root: Path) -> list[Vehicle]:
    """Load vehicles from the two tables in Transportation.md:

    - "Tack, Harness, and Drawn Vehicles" — land vehicles (Carriage,
      Cart, Chariot, Sled, Wagon) and tack/saddles.
    - "Waterborne Vehicles" — Galley, Keelboat, Longship, Rowboat,
      Sailing ship, Warship.

    Tack/saddles are loaded but tagged with empty ``weight_lb`` etc —
    they're filtered by VehicleType if the caller wants only vehicles.
    """
    text = _read_transportation(phb_root)
    if text is None:
        return []
    vehicles: list[Vehicle] = []
    for title, table in find_tables(text):
        title_lower = title.lower()
        if "tack, harness" in title_lower or "drawn vehicles" in title_lower:
            vehicles.extend(_parse_land_vehicle_table(table))
        elif "waterborne" in title_lower:
            vehicles.extend(_parse_water_vehicle_table(table))
    return vehicles


def _read_transportation(phb_root: Path) -> str | None:
    path = phb_root / "Equipment" / "Transportation.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _parse_land_vehicle_table(table) -> list[Vehicle]:
    """Parse the Tack/Harness/Drawn Vehicles table.

    Rows have columns [Item, Cost, Weight]. Skip rows that look like
    multipliers ("×4", "×2") and tack sub-rows (e.g. Saddle variants
    indented with "~"). Tack items (Bit and bridle, Saddlebags, Feed,
    Stabling, and the Barding multiplier row) load as land vehicles
    with weight 0 / cost tracked, since they're not "vehicles" per se
    but the PHB table groups them with drawn vehicles.
    """
    vehicles: list[Vehicle] = []
    for row in table.rows:
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) < 3:
            continue
        name = _clean_inline(row[0]).strip()
        cost_text = row[1].strip()
        weight_text = row[2].strip()

        # Skip multiplier / non-row entries (Barding: ×4 / ×2).
        if cost_text.startswith("×") or weight_text.startswith("×"):
            continue
        # Skip saddle subgroup rows ("~ Exotic", "~ Military", ...).
        if name.startswith("~"):
            continue
        if not name:
            continue

        cost = parse_cost_gp(cost_text)
        weight = parse_weight_lb(weight_text)
        vehicles.append(
            Vehicle(
                name=name,
                vehicle_type=VehicleType.LAND,
                cost_gp=cost,
                weight_lb=weight,
                speed_mph=0.0,
            )
        )
    return vehicles


def _parse_water_vehicle_table(table) -> list[Vehicle]:
    """Parse the Waterborne Vehicles table.

    Rows: [Item, Cost, Speed]. Speed is in mph (with optional Unicode
    fractions like '1½ mph').
    """
    vehicles: list[Vehicle] = []
    for row in table.rows:
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) < 3:
            continue
        name = _clean_inline(row[0]).strip()
        cost_text = row[1].strip()
        speed_text = row[2].strip()
        if not name:
            continue
        vehicles.append(
            Vehicle(
                name=name,
                vehicle_type=VehicleType.WATER,
                cost_gp=parse_cost_gp(cost_text),
                weight_lb=0.0,
                speed_mph=_parse_mph(speed_text),
            )
        )
    return vehicles


# Speed / capacity parsers specific to Transportation.md
_SPEED_FT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*ft\.?$", re.IGNORECASE)
_CAPACITY_LB_RE = re.compile(r"^([\d,]+)\s*lb\.?$", re.IGNORECASE)
_MPH_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*mph\.?$", re.IGNORECASE)


def _parse_speed_ft(text: str) -> int:
    """Parse '50 ft.' or '40 ft.' to an int. Returns 0 if unparseable."""
    m = _SPEED_FT_RE.match(text.strip())
    if not m:
        return 0
    return int(float(m.group(1)))


def _parse_capacity_lb(text: str) -> int:
    """Parse '480 lb.' or '1,320 lb.' to an int (commas stripped)."""
    m = _CAPACITY_LB_RE.match(text.strip())
    if not m:
        return 0
    return int(m.group(1).replace(",", ""))


def _parse_mph(text: str) -> float:
    """Parse '4 mph' or '1½ mph' to a float (Unicode fraction aware)."""
    text = text.strip()
    # Strip optional 'mph' suffix.
    text = re.sub(r"\s*mph\.?\s*$", "", text, flags=re.IGNORECASE).strip()
    if not text:
        return 0.0
    # Unicode-fraction form (whole + fraction, e.g. "1½" = 1.5).
    unicode_fracs = {
        "½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3,
    }
    m = re.match(r"^(\d+)([¼½¾⅓⅔])$", text)
    if m:
        return int(m.group(1)) + unicode_fracs[m.group(2)]
    # Plain number (possibly decimal).
    m = re.match(r"^(\d+(?:\.\d+)?)$", text)
    if m:
        return float(m.group(1))
    return 0.0
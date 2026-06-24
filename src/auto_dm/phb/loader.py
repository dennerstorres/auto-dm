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
    CharacterClass,
    ClassFeature,
    ClassProficiency,
    PHBArmor,
    PHBArmorCategory,
    PHBCondition,
    PHBDisease,
    PHBLanguage,
    PHBPoison,
    PHBSpell,
    PHBSpellComponent,
    PHBSpellSchool,
    PHBTrap,
    PHBWeapon,
    PHBWeaponCategory,
    Race,
    SpellcastingInfo,
    Subclass,
    Subrace,
    Trait,
)
from auto_dm.phb.parser import (
    find_tables,
    parse_cost_gp,
    parse_damage,
    parse_fields,
    parse_range,
    parse_traits,
    parse_weight_lb,
    split_sections,
)
from auto_dm.state.models import Ability


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
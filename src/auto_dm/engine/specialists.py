"""Specialist features: Wild Shape (Druid), Favored Enemy (Ranger),
Eldritch Invocations (Warlock), and misc class features.

Most are simple state mutations. Wild Shape has more shape because it
needs to switch the creature's stats.
"""
from __future__ import annotations

from dataclasses import dataclass

from auto_dm.state.models import Character


# ============================================================================
# Wild Shape (Druid L2+)
# ============================================================================


@dataclass
class WildShapeForm:
    """A beast form. PHB CR cap by level."""
    name: str
    hp_max: int
    armor_class: int
    speed: int
    attacks: list[str]
    cr: float = 0.0


# Predefined CR-1/4 forms (Druid L2+)
WILD_SHAPE_FORMS_L2 = [
    WildShapeForm(name="Wolf", hp_max=11, armor_class=13, speed=40, attacks=["bite"], cr=0.25),
    WildShapeForm(name="Boar", hp_max=13, armor_class=12, speed=40, attacks=["tusk"], cr=0.25),
]

WILD_SHAPE_FORMS_L4 = WILD_SHAPE_FORMS_L2 + [
    WildShapeForm(name="Giant Hyena", hp_max=45, armor_class=12, speed=50, attacks=["bite"], cr=1.0),
    WildShapeForm(name="Black Bear", hp_max=19, armor_class=11, speed=40, attacks=["bite", "claw"], cr=0.5),
]


def can_wild_shape(character: Character) -> bool:
    return character.class_.lower() == "druid" and character.level >= 2


def wild_shape_cr_cap(level: int) -> float:
    """PHB: CR 1/4 at L2, 1/2 at L4, 1 at L8."""
    if level >= 8:
        return 1.0
    if level >= 4:
        return 0.5
    return 0.25


def available_wild_shapes(level: int) -> list[WildShapeForm]:
    if level >= 4:
        return list(WILD_SHAPE_FORMS_L4)
    return list(WILD_SHAPE_FORMS_L2)


def enter_wild_shape(character: Character, form_name: str) -> tuple[bool, str]:
    """Transform the druid into a beast form."""
    if not can_wild_shape(character):
        return False, "only druids can Wild Shape"
    forms = {f.name: f for f in available_wild_shapes(character.level)}
    if form_name not in forms:
        return False, f"unknown form: {form_name}"
    form = forms[form_name]
    character.wild_shape_form = form_name
    character.hp_max = max(character.hp_max, form.hp_max)
    character.hp_current = character.hp_max
    character.armor_class = max(character.armor_class, form.armor_class)
    character.speed = max(character.speed, form.speed)
    return True, ""


def revert_wild_shape(character: Character) -> None:
    """End Wild Shape (on 0 HP, reaction, or bonus action)."""
    character.wild_shape_form = None


# ============================================================================
# Favored Enemy (Ranger L1+)
# ============================================================================


def add_favored_enemy(character: Character, enemy_type: str) -> None:
    character.favored_enemies.append(enemy_type)


def is_favored_enemy(character: Character, enemy_type: str) -> bool:
    return enemy_type.lower() in [e.lower() for e in character.favored_enemies]


# ============================================================================
# Eldritch Invocations (Warlock L2+)
# ============================================================================


def add_invocation(character: Character, invocation: str) -> None:
    if invocation not in character.eldritch_invocations:
        character.eldritch_invocations.append(invocation)


def has_invocation(character: Character, invocation: str) -> bool:
    return invocation in character.eldritch_invocations


# ============================================================================
# Misc
# ============================================================================


def divine_sense_range(character: Character) -> int:
    """Paladin L1: sense celestials/fiends/undead within 60 ft + 10/level."""
    if character.class_.lower() != "paladin":
        return 0
    return 60 + 10 * character.level


def destroy_undead_cr_cap(level: int) -> float:
    """Cleric: Channel Divinity: Destroy Undead CR cap."""
    if level >= 17:
        return 4.0
    if level >= 14:
        return 3.0
    if level >= 11:
        return 2.0
    if level >= 8:
        return 1.0
    if level >= 5:
        return 0.5
    return 0.0


def lay_on_hands_disease_cure_spend(amount: int) -> int:
    """PHB: curing a disease costs 5 HP from the pool."""
    return 5

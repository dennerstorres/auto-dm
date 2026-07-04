"""Subclass feature application (Phase 25b) + class feature wiring (Phase 25f).

Each class in the PHB grants subclass features at specific levels
(Path of the Berserker Frenzy at L3, College of Lore Cutting Words at
L3, Sorcerer Draconic Resilience at L1, etc.). This module is the
single source of truth for "what subclass features does a character
have at level N", so the character builder, the CLI wizard, and any
future level-up command all read the same way.

The :class:`Subclass` model already carries a list of
:class:`ClassFeature` with a ``level`` attribute (parsed from PHB).
This module just filters and orders them.

Class-feature wiring (Phase 25f) sets the right engine flags at each
level — ``has_aura_of_protection`` (Paladin L6), ``has_feral_instinct``
(Barbarian L7), ``aura_of_courage_active`` (Paladin L10), and so on.
The builder calls these at character creation; ``level_up`` calls them
after incrementing the level so the flags stay in sync.

Phase 38 — also owns ``auto_resolve_companion_asi`` (deterministic,
no LLM, picks the most-likely ASI for a given class so companions can
level up without player intervention), ``update_spell_slots_for_level``
(legible wrapper around the PHB slot table refresh), and
``resolve_asi_choice`` (the player-facing entry point that consumes
the queued ASI and clears ``Character.pending_asi``).
"""
from __future__ import annotations

from typing import Optional

from auto_dm.phb import ClassFeature, get_subclass
from auto_dm.state.models import Ability, AbilityScores, Character


# ============================================================================
# Phase 38 — Companion ASI auto-resolve + spell-slot refresh + ASI queue
# ============================================================================


# Per-class primary (+2) target. The heuristic for companion ASIs is
# deterministic and matches PHB build conventions:
#   martial classes pump STR (or DEX for rogue/ranger/monk),
#   paladins/goad casters pump their casting stat,
#   full casters pump INT/WIS/CHA.
_COMPANION_ASI_PRIMARY: dict[str, Ability] = {
    "barbarian": Ability.STR,
    "fighter": Ability.STR,
    "paladin": Ability.STR,
    "ranger": Ability.DEX,
    "rogue": Ability.DEX,
    "monk": Ability.DEX,
    "cleric": Ability.WIS,
    "druid": Ability.WIS,
    "wizard": Ability.INT,
    "sorcerer": Ability.CHA,
    "warlock": Ability.CHA,
    "bard": Ability.CHA,
}

# When the primary target is at 18+ and a +2 would exceed cap (20
# post-ASI), the companion takes +1 to primary + +1 to secondary
# instead. ``_COMPANION_ASI_SECONDARY_PAIR`` is the secondary target
# for that split.
_COMPANION_ASI_SECONDARY_PAIR: dict[str, Ability] = {
    "barbarian": Ability.CON,
    "fighter": Ability.CON,
    "paladin": Ability.CHA,
    "ranger": Ability.WIS,
    "rogue": Ability.DEX,  # DEX already high; route to WIS instead via fallback
    "monk": Ability.WIS,
    "cleric": Ability.CON,
    "druid": Ability.CON,
    "wizard": Ability.INT,
    "sorcerer": Ability.CON,
    "warlock": Ability.CON,
    "bard": Ability.DEX,
}


def auto_resolve_companion_asi(character: Character) -> dict:
    """Pick and apply an ASI for a non-player character.

    The companion gets one of:

    * ``+2 to primary`` — when ``primary_value <= 19`` (so the +2
      stays at or below cap 20).
    * ``+1 to primary + +1 to secondary`` — when ``primary_value == 20``
      (capped) or when ``primary_value == 19`` and we *prefer* the
      1/1 split to spread durability.
    * ``+1 to CON fallback`` — universal last resort. If both primary
      and the designated secondary are at cap, take +1 to CON. CON
      stays universally valuable (HP per level + concentration
      checks). Errors from ``apply_asi`` are swallowed silently so
      the level-up chain always succeeds.

    Returns:
        A dict ``{"primary": Ability, "secondary": Optional[Ability],
        "applied": bool}`` describing the choice. ``applied=False``
        only when the cap was unavoidable — the function still runs to
        completion so the level-up loop can finish.
    """
    from auto_dm.engine.progression import apply_asi  # local to avoid cycle

    cls = (character.class_ or "").strip().lower()
    primary = _COMPANION_ASI_PRIMARY.get(cls, Ability.STR)
    secondary = _COMPANION_ASI_SECONDARY_PAIR.get(cls, Ability.CON)

    primary_value = getattr(character.abilities, primary.value, 10)
    # secondary is only consulted inside the apply_asi calls below
    # (Path B split). No need to pre-read here.

    chosen_primary: Optional[Ability] = None
    chosen_secondary: Optional[Ability] = None
    applied = False

    # Path A: room for +2 to primary.
    if primary_value <= 18:
        try:
            apply_asi(character, primary=primary)
            chosen_primary = primary
            applied = True
        except ValueError:
            pass

    # Path B: primary is 19 or 20 → try 1/1 split.
    if not applied:
        try:
            apply_asi(character, primary=primary, secondary=secondary)
            chosen_primary = primary
            chosen_secondary = secondary
            applied = True
        except ValueError:
            pass

    # Path C: universal CON fallback. Last-resort to keep chain alive.
    if not applied:
        try:
            apply_asi(character, primary=Ability.CON)
            chosen_primary = Ability.CON
            applied = True
        except ValueError:
            pass

    return {
        "primary": chosen_primary,
        "secondary": chosen_secondary,
        "applied": applied,
    }


def companion_asi_to_pending(choice: dict) -> dict:
    """Map a companion ASI choice dict into the pending_asi queue shape.

    Used by ``engine/progression.py::_level_up_party_member`` to mark
    the auto-resolved choice in ``character.pending_asi`` so the
    frontend can show what changed in the narrative log.

    Returns a dict with: ``{"level", "choices", "resolved", "primary",
    "secondary"}`` where ``choices`` is ``["primary","secondary"]``
    for split, ``["primary"]`` for single.
    """
    primary = choice.get("primary")
    secondary = choice.get("secondary")
    choices = ["primary", "secondary"] if secondary else ["primary"]
    return {
        "level": 0,  # Filled in by the caller (engine layer)
        "choices": choices,
        "resolved": True,
        "primary": primary.value if primary is not None else None,
        "secondary": secondary.value if secondary is not None else None,
    }


def update_spell_slots_for_level(character: Character, level: int) -> None:
    """Refresh ``spell_slots`` / ``spell_slots_max`` for ``level``.

    Direct wrapper around ``engine.progression._refresh_spell_slots``
    for callers (tests, web endpoints) that want to refresh slots
    without going through the full ``level_up()`` chain. Idempotent.
    """
    from auto_dm.engine.progression import _refresh_spell_slots

    _refresh_spell_slots(character, level)


def resolve_asi_choice(
    character: Character,
    primary: Ability,
    secondary: Optional[Ability] = None,
) -> AbilityScores:
    """Consume ``character.pending_asi`` and apply the player's choice.

    Args:
        character: Mutated in place.
        primary: The +2 or +1 ability target.
        secondary: Optional second ability for the +1/+1 split.

    Returns:
        The updated :class:`AbilityScores`.

    Raises:
        ValueError: If the character has no pending ASI choice (queue
            empty), or if the chosen split exceeds the 20-cap per
            ``apply_asi``.

    Side effects:
        Clears ``character.pending_asi`` to ``None`` after a
        successful apply. If apply raises, the queue is preserved so
        the player can retry.
    """
    from auto_dm.engine.progression import apply_asi

    if character.pending_asi is None:
        raise ValueError(
            f"{character.name} não tem ASI pendente para resolver."
        )
    if character.pending_asi.get("resolved"):
        raise ValueError(
            f"ASI de {character.name} já foi resolvida."
        )

    scores = apply_asi(character, primary=primary, secondary=secondary)
    character.pending_asi = None
    return scores


def list_subclass_features(class_name: str, subclass_name: str) -> list[ClassFeature]:
    """Return all features for a subclass, ordered by acquisition level.

    Features whose ``level`` couldn't be parsed are placed at the end
    (level 0) so callers don't silently drop them.

    Returns an empty list when the subclass doesn't exist.
    """
    sub = get_subclass(class_name, subclass_name)
    if sub is None:
        return []
    return sorted(sub.features, key=lambda f: (f.level or 0, f.name))


def apply_subclass_features(
    character: Character,
    class_name: Optional[str] = None,
    subclass_name: Optional[str] = None,
    *,
    at_level: Optional[int] = None,
) -> list[str]:
    """Populate ``character.subclass_features`` with features the
    character has gained up to ``at_level`` (default: character.level).

    Args:
        character: The Character to mutate in place.
        class_name: Override ``character.class_`` if provided.
        subclass_name: Override ``character.subclass`` if provided.
        at_level: Cap acquisition level; defaults to ``character.level``.

    Returns:
        The list of feature names the character has at the given level.
        Also written to ``character.subclass_features``.

    Notes:
        If the character has no subclass, the list is cleared (idempotent).
        Unknown subclass names produce an empty list (the builder logs
        elsewhere — this function doesn't raise).
    """
    cls = (class_name or character.class_ or "").strip()
    sub = (subclass_name or character.subclass or "").strip()
    cap = at_level if at_level is not None else character.level

    if not cls or not sub:
        character.subclass_features = []
        return []

    features = list_subclass_features(cls, sub)
    gained = [f.name for f in features if f.level is None or f.level <= cap]
    character.subclass_features = gained
    return gained


def features_gained_at_level(
    class_name: str, subclass_name: str, level: int,
) -> list[ClassFeature]:
    """Return subclass features acquired *exactly* at ``level``.

    Useful for narration on level-up ("You gain Frenzy!"). Features
    with unknown level are NOT returned here (they'd mislead the DM).
    """
    sub = get_subclass(class_name, subclass_name)
    if sub is None:
        return []
    return [f for f in sub.features if f.level == level]


def has_subclass_feature(
    character: Character, feature_name: str,
) -> bool:
    """Quick check: does this character have a specific subclass feature?

    Cheap alternative to scanning ``character.subclass_features``.
    """
    return feature_name in character.subclass_features


# ============================================================================
# Class feature wiring (Phase 25f)
# ============================================================================


# Each tuple: (class_name_lowercase, level, list of (flag_name, value) to set).
# Used by ``apply_class_features`` to gate passive / reactive features by level.
# Combat-action features (Rage, Second Wind, etc.) are wired in combat_engine
# handlers; this table covers only the state flags.
_CLASS_FEATURE_GATES: list[tuple[str, int, list[tuple[str, bool]]]] = [
    # --- Barbarian ---
    ("barbarian", 2, [("has_danger_sense", True)]),
    ("barbarian", 5, [("extra_attacks", 1)]),  # applied by extra_attack_for()
    ("barbarian", 7, [("has_feral_instinct", True)]),
    ("barbarian", 9, [("brutal_critical_dice", 1)]),
    ("barbarian", 13, [("brutal_critical_dice", 2)]),
    ("barbarian", 17, [("brutal_critical_dice", 3)]),
    ("barbarian", 20, [("has_primal_champion", True)]),
    # --- Paladin ---
    ("paladin", 6, [("has_aura_of_protection", True), ("aura_of_protection_active", True)]),
    ("paladin", 10, [("aura_of_courage_active", True)]),
    # --- Rogue ---
    ("rogue", 2, [("has_cunning_action", True)]),
    ("rogue", 5, [("has_uncanny_dodge", True)]),
    ("rogue", 7, [("has_evasion", True)]),
    ("rogue", 20, [("has_stroke_of_luck", True), ("stroke_of_luck_uses_remaining", 1)]),
    # --- Monk ---
    ("monk", 7, [("has_evasion", True)]),
    ("monk", 20, [("has_perfect_self", True)]),
    # --- Fighter ---
    ("fighter", 9, []),  # Indomitable is an ActionType handler, no flag.
    # --- Cleric ---
    ("cleric", 20, [("has_divine_intervention_improvement", True)]),
    # --- Druid ---
    ("druid", 20, [("has_archdruid", True)]),
    # --- Ranger ---
    ("ranger", 20, [("has_foe_slayer", True)]),
    # --- Sorcerer ---
    ("sorcerer", 20, [("has_arcane_apotheosis", True)]),
    # --- Warlock ---
    ("warlock", 20, [("has_eldritch_master", True)]),
    # --- Wizard ---
    ("wizard", 20, [("has_signature_spells", True)]),
]


def apply_class_features(
    character: Character,
    *,
    at_level: Optional[int] = None,
) -> list[str]:
    """Wire class-feature flags based on (class, level).

    Iterates :data:`_CLASS_FEATURE_GATES` and sets the listed flag
    pairs where the character's level meets or exceeds the gate's
    level. Returns the list of human-readable feature names that
    became active *at* ``at_level`` (useful for level-up narration).

    Idempotent — safe to call after build or after each level-up.
    """
    cap = at_level if at_level is not None else character.level
    class_lower = (character.class_ or "").strip().lower()

    # Apply gates up to the cap.
    for cls_name, gate_level, flags in _CLASS_FEATURE_GATES:
        if cls_name != class_lower:
            continue
        if cap >= gate_level:
            for flag_name, value in flags:
                setattr(character, flag_name, value)

    # Apply capstones' derived effects. Each capstone may have a
    # follow-up side effect (e.g. raising ability scores, refilling
    # resources) that's tied to the level itself, not a bool flag.
    _apply_capstone_side_effects(character, class_lower, cap)

    # Track features gained exactly at ``at_level`` (default: current).
    gained_names: list[str] = []
    if at_level is not None:
        for cls_name, gate_level, _flags in _CLASS_FEATURE_GATES:
            if cls_name == class_lower and gate_level == at_level:
                # Human-readable feature name.
                display = _gate_to_display_name(cls_name, gate_level)
                if display:
                    gained_names.append(display)

    # Also re-apply subclass features up to the cap so subclass flags
    # (Draconic Resilience HP, etc.) are consistent with level.
    apply_subclass_features(character, at_level=cap)

    return gained_names


def _apply_capstone_side_effects(character: Character, class_lower: str, cap: int) -> None:
    """Apply level-derived side effects of capstones (idempotent).

    E.g. Barbarian L20: Primal Champion adds 4 to STR/CON.
    """
    if (
        class_lower == "barbarian"
        and cap >= 20
        and character.has_primal_champion
        and not character.primal_champion_applied
    ):
        # PHB: +4 to STR and CON; max ability score becomes 24.
        # Idempotency is enforced by ``primal_champion_applied`` so the
        # bonus fires exactly once even though ``apply_class_features``
        # runs on every level-up.
        character.abilities.strength = min(24, character.abilities.strength + 4)
        character.abilities.constitution = min(24, character.abilities.constitution + 4)
        character.primal_champion_applied = True


def _gate_to_display_name(class_name: str, level: int) -> str:
    """Map (class, level) to a short narration-friendly feature name."""
    names: dict[tuple[str, int], str] = {
        ("barbarian", 2): "Danger Sense",
        ("barbarian", 7): "Feral Instinct",
        ("barbarian", 9): "Brutal Critical (1 die)",
        ("barbarian", 13): "Brutal Critical (2 dice)",
        ("barbarian", 17): "Brutal Critical (3 dice)",
        ("barbarian", 20): "Primal Champion",
        ("paladin", 6): "Aura of Protection",
        ("paladin", 10): "Aura of Courage",
        ("rogue", 2): "Cunning Action",
        ("rogue", 5): "Uncanny Dodge",
        ("rogue", 7): "Evasion",
        ("rogue", 20): "Stroke of Luck",
        ("monk", 7): "Evasion",
        ("monk", 20): "Perfect Self",
        ("fighter", 9): "Indomitable",
        ("cleric", 20): "Divine Intervention Improvement",
        ("druid", 20): "Archdruid",
        ("ranger", 20): "Foe Slayer",
        ("sorcerer", 20): "Arcane Apotheosis",
        ("warlock", 20): "Eldritch Master",
        ("wizard", 20): "Signature Spells",
    }
    return names.get((class_name, level), "")


def features_gained_at_class_level(
    character: Character,
    level: int,
) -> list[str]:
    """Public helper: what (non-subclass) class features were gained at
    exactly ``level`` for this character? Useful for /level-up narration.
    """
    cap = level
    class_lower = (character.class_ or "").strip().lower()
    out: list[str] = []
    for cls_name, gate_level, _flags in _CLASS_FEATURE_GATES:
        if cls_name == class_lower and gate_level == cap:
            display = _gate_to_display_name(cls_name, gate_level)
            if display:
                out.append(display)
    return out
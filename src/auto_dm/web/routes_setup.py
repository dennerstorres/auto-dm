"""Character creation wizard endpoints (Phase 26c).

These power the in-browser wizard that builds a level 1–5 PHB
character and instantiates a session with the player + chosen AI
companions.

Endpoints (all require Authorization: Bearer <token>):

- ``GET  /api/character-options``     — metadata the wizard renders
- ``POST /api/sessions/with-character`` — build a Character from a
   spec, attach AI companions, and create a new active session

The wizard is intentionally client-driven: the backend provides the
catalog (races/classes/subclasses/backgrounds/alignments/etc.) and a
single "build" endpoint. All multi-step state lives in the browser.
This keeps the backend stateless and avoids saving half-built drafts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auto_dm.character.spells import (
    get_cantrips_known,
    get_spell_slots,
    get_spellbook_size,
    get_spells_known_max,
    prepare_caster_spells,
)
from auto_dm.character.builder import CharacterBuilder, parse_class_skill_options
from auto_dm.companions import (
    COMPANION_BLURBS,
    COMPANION_FACTORIES,
    list_companion_keys,
    roll_party_candidates,
)
from auto_dm.phb import get_class, get_race, get_spells_for_class
from auto_dm.state.models import AbilityScores, Character, GameState
from auto_dm.web.auth import current_user
from auto_dm.web.models import User
from auto_dm.web.sessions import SessionManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["setup"])


# ============================================================================
# Schemas
# ============================================================================


_ALIGNMENTS: list[str] = ["LG", "NG", "CG", "LN", "N", "CN", "LE", "NE", "CE"]
_LEVELS: list[int] = [1, 2, 3, 4, 5]
_STATS_METHODS: list[dict[str, str]] = [
    {"id": "standard_array", "label": "Array padrão (15, 14, 13, 12, 10, 8)"},
    {"id": "roll", "label": "Rolar 4d6 (drop lowest, 6 rolagens)"},
    {"id": "point_buy", "label": "Compra de pontos (PHB p.13)"},
]


class CompanionOption(BaseModel):
    """A pre-defined companion the player can opt into."""

    key: str
    name: str
    race: str
    class_: Optional[str] = None
    description: str = ""


class BackgroundOption(BaseModel):
    name: str
    description: str = ""
    feature: str = ""


class SpellSelectionSpec(BaseModel):
    cantrips: list[str] = Field(default_factory=list)
    spells_known: list[str] = Field(default_factory=list)
    spells_prepared: list[str] = Field(default_factory=list)
    spellbook: list[str] = Field(default_factory=list)


class CharacterOptions(BaseModel):
    """Metadata for the wizard UI."""

    races: list[dict[str, Any]]
    classes: list[dict[str, Any]]
    backgrounds: list[BackgroundOption]
    alignments: list[str]
    levels: list[int]
    stats_methods: list[dict[str, str]]
    companions: list[CompanionOption]


class PlayerCharacterSpec(BaseModel):
    """The wizard's final character spec."""

    name: str = Field(..., min_length=1, max_length=64)
    race: str
    subrace: Optional[str] = None
    char_class: str = Field(..., alias="class")
    subclass: Optional[str] = None
    background: str
    alignment: str = "N"
    level: int = Field(1, ge=1, le=5)
    # If stats_method == "manual", provide explicit scores; otherwise
    # the backend rolls/applies standard array itself.
    stats_method: str = "standard_array"
    stats: Optional[list[int]] = None
    skills: list[str] = Field(default_factory=list)
    starting_weapon: Optional[str] = None
    starting_armor: Optional[str] = None
    starting_shield: bool = False
    starting_pack: Optional[str] = None
    spell_selection: Optional[SpellSelectionSpec] = None

    model_config = {"populate_by_name": True}


class WithCharacterRequest(BaseModel):
    campaign_name: str = Field(..., min_length=1, max_length=128)
    player_character: PlayerCharacterSpec
    companions: list[str] = Field(default_factory=list)


class SessionCreated(BaseModel):
    session_id: str
    slug: str
    state: dict[str, Any]


# ============================================================================
# Helpers
# ============================================================================


def _serialize_race(race) -> dict[str, Any]:
    return {
        "name": race.name,
        "speed": race.speed,
        "size": race.size,
        "description": race.description,
        "subraces": [sr.name for sr in race.subraces],
    }


def _serialize_class(cls) -> dict[str, Any]:
    spellcasting = None
    if cls.spellcasting is not None:
        spellcasting = _serialize_spellcasting_options(cls)
    return {
        "name": cls.name,
        "hit_dice": cls.hit_dice,
        "description": cls.description,
        "skill_options": parse_class_skill_options(cls.proficiencies.skills_choices),
        "num_skill_choices": cls.proficiencies.num_skill_choices,
        "subclasses": [sc.name for sc in cls.subclasses],
        "is_spellcaster": cls.spellcasting is not None,
        "spellcasting": spellcasting,
    }


def _caster_type(class_name: str) -> str:
    cls = class_name.strip().lower()
    if cls == "wizard":
        return "wizard"
    if cls in {"cleric", "druid", "paladin"}:
        return "prepared"
    if cls in {"bard", "ranger", "sorcerer", "warlock"}:
        return "known"
    return "none"


def _serialize_spell(spell) -> dict[str, Any]:
    return {
        "name": spell.name,
        "level": spell.level,
        "school": str(spell.school.value),
        "ritual": spell.is_ritual,
        "concentration": spell.is_concentration,
    }


def _serialize_spellcasting_options(cls) -> dict[str, Any]:
    spells = sorted(
        get_spells_for_class(cls.name),
        key=lambda s: (s.level, s.name),
    )
    limits = {}
    for level in _LEVELS:
        slots = get_spell_slots(cls.name, level)
        limits[str(level)] = {
            "cantrips_known": get_cantrips_known(cls.name, level),
            "spells_known": get_spells_known_max(cls.name, level),
            "spellbook_size": get_spellbook_size(cls.name, level),
            "slot_levels": sorted(slots.keys()),
        }
    return {
        "ability": cls.spellcasting.ability.value,
        "caster_type": _caster_type(cls.name),
        "limits": limits,
        "spells": [_serialize_spell(s) for s in spells],
    }


def _serialize_background(bg) -> dict[str, str]:
    return {
        "name": bg.name,
        "description": bg.description,
        "feature": bg.feature_name,
    }


def _build_character(spec: PlayerCharacterSpec) -> Character:
    """Build a :class:`Character` from the wizard spec using
    :class:`CharacterBuilder`. Raises ``ValueError`` on invalid input."""
    builder = (
        CharacterBuilder()
        .with_name(spec.name)
        .with_race(spec.race, subrace=spec.subrace)
        .with_class(spec.char_class, subclass=spec.subclass)
        .with_background(spec.background)
        .with_alignment(spec.alignment)
        .with_level(spec.level)
    )
    # Stats
    if spec.stats_method == "manual" and spec.stats is not None:
        builder.with_ability_scores(spec.stats)
    elif spec.stats_method == "roll":
        builder.with_rolled_stats()
    else:  # default / standard_array
        builder.with_standard_array()
    # Skills (defaults to empty if not provided — that's allowed for some
    # classes like Sorcerer where num_skill_choices is small or zero).
    builder.with_skills(spec.skills)
    # Equipment
    if spec.starting_weapon:
        builder.with_starting_weapon(spec.starting_weapon)
    if spec.starting_armor:
        builder.with_starting_armor(spec.starting_armor)
    if spec.starting_shield:
        builder.with_shield()
    if spec.starting_pack:
        builder.with_starting_pack(spec.starting_pack)
    draft = builder.build()
    character = draft.character
    if spec.spell_selection is None:
        return character

    selection = _build_spell_selection(
        draft.char_class,
        character,
        spec.spell_selection,
    )
    spellcasting = selection.to_spellcasting(
        draft.char_class,
        character.abilities,
        character.proficiency_bonus,
    )
    slots = get_spell_slots(draft.char_class.name, character.level)
    spellcasting.spell_slots = dict(slots)
    spellcasting.spell_slots_max = dict(slots)
    return character.model_copy(update={"spellcasting": spellcasting})


def _build_spell_selection(char_class, character: Character, spec: SpellSelectionSpec):
    """Validate the browser spell choices and return a SpellSelection."""
    return prepare_caster_spells(
        char_class=char_class,
        level=character.level,
        abilities=character.abilities,
        proficiency_bonus=character.proficiency_bonus,
        cantrips=spec.cantrips,
        spells_known=spec.spells_known,
        spells_prepared=spec.spells_prepared,
        spellbook=spec.spellbook,
    )


# ============================================================================
# SessionManager dependency (duplicated to avoid circular import; identical
# to the one in routes_game.py)
# ============================================================================


def get_session_manager() -> SessionManager:
    from auto_dm.web.server import get_app_state

    return get_app_state().session_manager


# ============================================================================
# Endpoints
# ============================================================================


@router.get("/character-options", response_model=CharacterOptions)
async def character_options(
    user: Annotated[User, Depends(current_user)],  # noqa: ARG001 — auth required
) -> CharacterOptions:
    """Return the full wizard catalog in a single payload.

    The wizard renders all choices from this — no further round trips
    needed for catalog data. Keep it small (~few KB).
    """
    races = []
    race_names = [
        "Dwarf", "Elf", "Halfling", "Human", "Dragonborn", "Gnome",
        "Half-Elf", "Half-Orc", "Tiefling",
    ]
    for n in race_names:
        race = get_race(n)
        if race is None:
            # PHB content not loaded (e.g. image built without data/phb/).
            # Log once per missing entry but never break the catalog.
            logger.warning("PHB race %r not loaded; skipping from catalog", n)
            continue
        races.append(_serialize_race(race))
    classes = []
    class_names = [
        "Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk",
        "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard",
    ]
    for n in class_names:
        cls = get_class(n)
        if cls is None:
            logger.warning("PHB class %r not loaded; skipping from catalog", n)
            continue
        classes.append(_serialize_class(cls))
    # Backgrounds — pull from PHB
    from auto_dm.phb.lookup import get_backgrounds

    backgrounds = [
        BackgroundOption(
            name=b.name,
            description=b.description,
            feature=b.feature_name,
        )
        for b in get_backgrounds()
    ]
    companions: list[CompanionOption] = []
    for key in list_companion_keys():
        try:
            ch = COMPANION_FACTORIES[key]()
            companions.append(CompanionOption(
                key=key,
                name=ch.name,
                race=ch.race,
                class_=getattr(ch, "class_", None) or "",
                description=COMPANION_BLURBS.get(key, ""),
            ))
        except Exception:
            continue
    return CharacterOptions(
        races=sorted(races, key=lambda r: r["name"]),
        classes=sorted(classes, key=lambda c: c["name"]),
        backgrounds=backgrounds,
        alignments=_ALIGNMENTS,
        levels=_LEVELS,
        stats_methods=_STATS_METHODS,
        companions=companions,
    )


class RollCompanionsRequest(BaseModel):
    """Wizard step 10 input: ask for synergy-biased party candidates."""

    char_class: str = Field(..., min_length=1, max_length=64, alias="class")
    subclass: Optional[str] = None

    model_config = {"populate_by_name": True}


class RollCompanionsResponse(BaseModel):
    """The 4 companions the player can pick from in step 10."""

    candidates: list[CompanionOption]


def _candidate_options(keys: list[str]) -> list[CompanionOption]:
    out: list[CompanionOption] = []
    for key in keys:
        try:
            ch = COMPANION_FACTORIES[key]()
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Failed to build companion %r: %s", key, exc)
            continue
        out.append(CompanionOption(
            key=key,
            name=ch.name,
            race=ch.race,
            class_=getattr(ch, "class_", None) or "",
            description=COMPANION_BLURBS.get(key, ""),
        ))
    return out


def _stub_player(char_class: str, subclass: Optional[str]) -> Character:
    """Build a minimal Character stub used only to drive the synergy roll.

    ``roll_party_candidates`` only reads ``class_`` from the player; the
    other fields exist solely to satisfy Character's required schema.
    """
    return Character(
        id="__synergy_stub__",
        name="__synergy_stub__",
        race="Human",
        **{"class": char_class.strip().title()},
        subclass=subclass,
        level=1,
        background="Commoner",
        alignment="N",
        abilities=AbilityScores(
            strength=10, dexterity=10, constitution=10,
            intelligence=10, wisdom=10, charisma=10,
        ),
        hp_current=1, hp_max=1, armor_class=10, speed=30,
        proficiency_bonus=2, hit_dice="1d8", hit_dice_remaining=1,
    )


@router.post("/companions/roll", response_model=RollCompanionsResponse)
async def roll_companions(
    body: RollCompanionsRequest,
    user: Annotated[User, Depends(current_user)],  # noqa: ARG001 — auth required
) -> RollCompanionsResponse:
    """Return 4 synergy-biased companion candidates for ``body.char_class``.

    Phase 27: at each campaign start the wizard offers 4 candidates
    rolled from the 12-companion pool with weighted-random bias toward
    roles the player's class does not already fill.
    """
    stub = _stub_player(body.char_class, body.subclass)
    keys = roll_party_candidates(stub, k=4)
    return RollCompanionsResponse(candidates=_candidate_options(keys))


@router.post(
    "/sessions/with-character",
    response_model=SessionCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_session_with_character(
    body: WithCharacterRequest,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> SessionCreated:
    """Build a Character + GameState from the wizard spec and start a session."""
    # Validate campaign / spec
    try:
        player = _build_character(body.player_character)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    # Builder defaults is_player=False; flip it for the human.
    player = player.model_copy(update={"is_player": True})
    # Validate companions
    available = set(list_companion_keys())
    chosen_companions: list[Character] = []
    for key in body.companions:
        if key not in available:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown companion: {key!r}",
            )
        comp = COMPANION_FACTORIES[key]()
        # Stable unique id (avoid colliding with player's id)
        comp = comp.model_copy(update={"id": f"c_{key}"})
        chosen_companions.append(comp)
    # Build GameState
    from auto_dm.persistence import slugify

    state = GameState(
        campaign_name=body.campaign_name,
        started_at=datetime.now(timezone.utc),
        # current_location intentionally left empty (default "") — the DM
        # chooses the starting scene during the opening narration.
        party=[player, *chosen_companions],
        npcs=[],
        player_character_id=player.id,
    )
    sess = await sm.create(user.id, state)
    return SessionCreated(
        session_id=sess.session_id,
        slug=slugify(body.campaign_name),
        state=sess.state.model_dump(mode="json"),
    )

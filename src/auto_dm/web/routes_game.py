"""Game routes: sessions + saves (Phase 26) + Phase 38 XP/ASI endpoints.

Endpoints (all require Authorization: Bearer <token>):

- POST   /api/sessions                 → create a new session from GameState JSON
- GET    /api/sessions                 → list active session ids
- GET    /api/sessions/{sid}           → load a session's current state
- GET    /api/sessions/{sid}/companions→ load AI companions' character sheets
- POST   /api/sessions/{sid}/roll-check→ roll a d20 check using a character sheet
- POST   /api/sessions/{sid}/opening   → generate (or return) the campaign opening
- POST   /api/sessions/{sid}/input     → send a player input line, returns NarrativeResult
- POST   /api/sessions/{sid}/award-xp  → Phase 38 — manually credit XP to party pool
- POST   /api/sessions/{sid}/resolve-asi→ Phase 38 — apply the player's queued ASI choice
- DELETE /api/sessions/{sid}           → discard a session

- GET    /api/saves                    → list persisted saves for the user (active only)
- GET    /api/saves?archived=true      → list archived saves only
- POST   /api/saves                    → create or update a save (auto-called after input)
- POST   /api/saves/{slug}/load        → hydrate a session from a save
- POST   /api/saves/{slug}/archive     → hide a save from the default list
- POST   /api/saves/{slug}/unarchive   → restore an archived save
- DELETE /api/saves/{slug}             → delete a save

The 24h Redis TTL on active sessions is refreshed on every input.
The persistent saves table is the source of truth for long-term state.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auto_dm.agents import generate_opening, process_player_action
from auto_dm.engine.checks import ABILITY_LABELS, roll_character_check
from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.llm.usage import UsageReport
from auto_dm.state.models import GameState
from auto_dm.web.activity import log_activity
from auto_dm.web.auth import current_user, require_admin
from auto_dm.web.config import get_settings
from auto_dm.web.db import get_session
from auto_dm.web.limits import check_quota
from auto_dm.web.models import ActivityType, Save, UsageKind, User
from auto_dm.web.sessions import SessionManager
from auto_dm.web.usage import persist_usage_events

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["game"])


# ============================================================================
# Schemas
# ============================================================================


class CreateSessionRequest(BaseModel):
    """Body for POST /api/sessions — full GameState JSON."""
    state: dict[str, Any] = Field(..., description="Serialized GameState")


class SessionCreated(BaseModel):
    session_id: str
    state: dict[str, Any]


class InputRequest(BaseModel):
    line: str = Field(..., min_length=1, max_length=2000)


class RollCheckRequest(BaseModel):
    check: str = Field(..., min_length=1, max_length=80)
    kind: str | None = Field(default=None, max_length=24)
    advantage: bool = False
    disadvantage: bool = False


class RollCheckOut(BaseModel):
    character_id: str
    character_name: str
    kind: str
    key: str
    label: str
    ability: str
    ability_label: str
    ability_modifier: int
    proficiency_bonus: int
    proficient: bool
    modifier: int
    rolls: list[int]
    kept: list[int]
    dropped: list[int]
    total: int
    natural: int
    notation: str
    advantage: bool = False
    disadvantage: bool = False


class AwardXPRequest(BaseModel):
    """Body for POST /api/sessions/{sid}/award-xp (Phase 38).

    Grants `amount` XP to the party. Cross-threshold awards fire
    auto-level-up for every member; the response surfaces the
    LevelUpBatch so the frontend can show level-up entries and
    pop the ASI modal if the player has a queued choice.
    """

    amount: int = Field(..., ge=1, le=100_000)


class ResolveASIRequest(BaseModel):
    """Body for POST /api/sessions/{sid}/resolve-asi (Phase 38).

    `primary` is required (the +2 target, or one of the two +1 targets).
    `secondary` is optional; when set, the player chose the +1 / +1
    split. Both must be lowercase ability names: ``strength``,
    ``dexterity``, ``constitution``, ``intelligence``, ``wisdom``,
    ``charisma``.
    """

    character_id: str = Field(..., min_length=1, max_length=64)
    primary: str = Field(..., min_length=3, max_length=12)
    secondary: str | None = Field(default=None, max_length=12)


class ReactionRequest(BaseModel):
    """Body for POST /api/sessions/{sid}/reaction (Phase 41c).

    The responder is whichever party member currently holds an open
    ``pending_reaction`` (only one trigger is published per event). The
    client passes the chosen ``kind`` (a ``ReactionKind`` value) plus,
    for spell reactions, an optional ``slot_level`` (upcast) and, for
    Counterspell, an optional ``check_roll`` (the d20+mod ability check
    total — when omitted the engine rolls deterministically).
    """

    kind: str = Field(..., min_length=1, max_length=32)
    slot_level: int | None = Field(default=None, ge=1, le=9)
    check_roll: int | None = Field(default=None, ge=1, le=40)
    # ``decline=True`` lets the client pass (auto-pass) without resolving.
    decline: bool = False


class SaveRequest(BaseModel):
    slug: str = Field(..., min_length=1, max_length=128)
    state: dict[str, Any] = Field(..., description="Serialized GameState")


class SaveOut(BaseModel):
    slug: str
    updated_at: str
    created_at: str
    archived: bool = False

    @classmethod
    def from_save(cls, save: Save) -> "SaveOut":
        return cls(
            slug=save.slug,
            updated_at=save.updated_at.isoformat() if save.updated_at else "",
            created_at=save.created_at.isoformat() if save.created_at else "",
            archived=bool(save.archived),
        )


# ============================================================================
# SessionManager dependency
# ============================================================================


def get_session_manager() -> SessionManager:
    """Pull the SessionManager from app state.

    The :func:`create_app` factory attaches it during ``lifespan``.
    """
    from auto_dm.web.server import get_app_state

    return get_app_state().session_manager


# ============================================================================
# Session endpoints
# ============================================================================


@router.post("/sessions", response_model=SessionCreated, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    user: Annotated[User, Depends(require_admin)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> SessionCreated:
    """Create a new active game session from a GameState payload.

    Admin only — used by the "Criar jogo vazio" advanced option. Regular
    users create games through the character wizard (``/sessions/with-character``).
    """
    try:
        state = GameState.model_validate(body.state)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid GameState: {exc}",
        )
    sess = await sm.create(user.id, state)
    return SessionCreated(
        session_id=sess.session_id,
        state=sess.state.model_dump(mode="json"),
    )


@router.get("/sessions")
async def list_sessions(
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, list[str]]:
    """List active session ids for the current user."""
    ids = await sm.list_active(user.id)
    return {"session_ids": ids}


@router.get("/sessions/{session_id}")
async def get_session_state(
    session_id: str,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, Any]:
    """Get the current state of a session."""
    sess = await sm.get(user.id, session_id)
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )
    return {"session_id": session_id, "state": sess.state.model_dump(mode="json")}


@router.get("/sessions/{session_id}/companions")
async def get_session_companions(
    session_id: str,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, Any]:
    """Return the AI companions' character sheets for a session.

    Read-only mirror of ``state.party`` filtered to non-player members.
    Used by the table-tools UI to render the per-companion tabs in the
    ficha panel — each entry has the same shape as the player
    character (``name``, ``class``, ``hp_current``, ``armor_class``,
    ``abilities``, ``proficiencies``, ``conditions``, etc.) so the
    frontend can reuse the same rendering helpers.

    No LLM calls. Same auth and lifetime semantics as
    ``GET /sessions/{sid}``.
    """
    sess = await sm.get(user.id, session_id)
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )
    companions = [
        c.model_dump(mode="json") for c in sess.state.party if not c.is_player
    ]
    return {"session_id": session_id, "companions": companions}


@router.post("/sessions/{session_id}/roll-check", response_model=RollCheckOut)
async def session_roll_check(
    session_id: str,
    body: RollCheckRequest,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> RollCheckOut:
    """Roll a player d20 check using the active character sheet.

    The frontend sends a requested ability, skill, or saving throw. The
    backend resolves aliases such as "furtividade", "perception", or
    "salvaguarda de Destreza", pulls the correct ability/proficiency
    bonuses from the current player character, and returns the full
    breakdown for display at the virtual table.
    """
    sess = await sm.get(user.id, session_id)
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )
    state = sess.state
    player = next(
        (c for c in state.party if c.id == state.player_character_id),
        None,
    )
    if player is None:
        player = next((c for c in state.party if c.is_player), None)
    if player is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No player character in this session",
        )
    try:
        result = roll_character_check(
            player,
            body.check,
            kind=body.kind,
            advantage=body.advantage,
            disadvantage=body.disadvantage,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    return RollCheckOut(
        character_id=result.character_id,
        character_name=result.character_name,
        kind=result.spec.kind,
        key=result.spec.key,
        label=result.spec.label,
        ability=result.spec.ability.value,
        ability_label=ABILITY_LABELS[result.spec.ability],
        ability_modifier=result.ability_modifier,
        proficiency_bonus=result.proficiency_bonus,
        proficient=result.proficient,
        modifier=result.modifier,
        rolls=result.roll.rolls,
        kept=result.roll.kept,
        dropped=result.roll.dropped,
        total=result.roll.total,
        natural=result.roll.kept[0],
        notation=result.roll.notation,
        advantage=result.advantage,
        disadvantage=result.disadvantage,
    )


@router.post("/sessions/{session_id}/input")
async def session_input(
    session_id: str,
    body: InputRequest,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Send a player input line to a session.

    Returns the ``NarrativeResult`` (narration, action, action_result,
    follow_up, error). The session state is auto-persisted to Redis.
    Enforces the daily quota (429 when exceeded) and records token usage.
    """
    sess = await sm.get(user.id, session_id)
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )
    # Quota check before any LLM call.
    settings = get_settings()
    exceeded = await check_quota(db, user, settings)
    if exceeded is not None:
        await log_activity(
            db,
            user_id=user.id,
            event=ActivityType.LIMIT_BLOCKED,
            meta={"endpoint": "input", **{k: exceeded[k] for k in ("used", "limit", "unit")}},
        )
        raise HTTPException(status_code=429, detail=exceeded)
    try:
        result = process_player_action(
            sess.state_manager,
            body.line,
            sess.dm_agent,
            combat_engine=sess.combat_engine,
        )
    except Exception as exc:
        logger.exception("process_player_action failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Game error: {exc}",
        )
    # Persist state to Redis.
    await sm.save(sess)
    # Record token usage (best-effort; never fails the turn).
    # Phase 33: each UsageReport now carries a ``kind`` tag (set by the
    # narrative loop). We split by kind and persist each bucket
    # separately so the summarizer's cost doesn't pollute the player's
    # daily quota. Empty ``kind`` defaults to ``"player"`` for
    # backward-compat (DM and follow-up narration).
    usages: list[UsageReport] = list(getattr(result, "usages", []) or [])
    if usages:
        try:
            # Bucket usages by kind (empty string → "player").
            buckets: dict[str, list[UsageReport]] = {}
            for u in usages:
                kind = u.kind or UsageKind.PLAYER.value
                buckets.setdefault(kind, []).append(u)
            for kind_value, reports in buckets.items():
                if not reports:
                    continue
                await persist_usage_events(
                    db,
                    user_id=user.id,
                    endpoint=f"/api/sessions/{session_id}/input",
                    reports=reports,
                    settings=settings,
                    session_id=session_id,
                    kind=kind_value,
                )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist usage events")
    # NarrativeResult is a dataclass; convert to dict.
    out = {
        "narration": getattr(result, "narration", None),
        "action": getattr(result, "action", None),
        "action_result": getattr(result, "action_result", None),
        "follow_up": getattr(result, "follow_up", None),
        "error": getattr(result, "error", None),
        "companion_results": getattr(result, "companion_results", None),
    }
    return {
        "session_id": session_id,
        "result": out,
        "state": sess.state.model_dump(mode="json"),
    }


@router.post("/sessions/{session_id}/award-xp")
async def session_award_xp(
    session_id: str,
    body: AwardXPRequest,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, Any]:
    """Phase 38 — manually credit ``body.amount`` XP to the party pool.

    Triggers the same auto-level-up loop as combat-end XP. Returns the
    full :class:`LevelUpBatch` (one :class:`LevelUpReport` per party
    member that advanced) plus the re-serialized state. The frontend
    uses the batch to show level-up entries in the log and to pop the
    ASI modal when ``batch.any_asi_pending`` is True.

    No LLM call — costs no tokens. Per-session rate limit (10/min) is
    enforced via a Redis counter to prevent runaway grants.
    """
    from auto_dm.engine.progression import award_party_xp
    from auto_dm.web.rate_limit import check_rate_limit

    sess = await sm.get(user.id, session_id)
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )

    # Per-session rate limit (independent of the daily LLM-token quota,
    # since this endpoint is a free, no-LLM action).
    rate = await check_rate_limit(
        scope=f"award-xp:{session_id}", limit=10, window_seconds=60,
    )
    if not rate.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limited",
                "limit": rate.limit,
                "window_seconds": rate.window_seconds,
                "retry_after": rate.retry_after,
            },
        )

    # Capture old level for narration; award_party_xp mutates state.
    from auto_dm.engine.progression import current_party_level

    old_level = current_party_level(sess.state)
    batch = award_party_xp(
        sess.state,
        body.amount,
        source="meta",
    )

    await sm.save(sess)
    return {
        "session_id": session_id,
        "old_party_level": old_level,
        "new_party_level": batch.new_party_level,
        "xp_awarded": batch.xp_awarded,
        "new_party_xp": batch.new_party_xp,
        "any_leveled": batch.any_leveled,
        "any_asi_pending": batch.any_asi_pending,
        "reports": [
            {
                "character_id": r.character_id,
                "character_name": r.character_name,
                "is_player": r.is_player,
                "old_level": r.old_level,
                "new_level": r.new_level,
                "hp_gained": r.hp_gained,
                "features_gained": r.features_gained,
                "asi_pending": r.asi_pending,
                "asi_auto_resolved": r.asi_auto_resolved,
            }
            for r in batch.reports
        ],
        "state": sess.state.model_dump(mode="json"),
    }


@router.post("/sessions/{session_id}/resolve-asi")
async def session_resolve_asi(
    session_id: str,
    body: ResolveASIRequest,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, Any]:
    """Phase 38 — consume the player's queued ASI and apply it.

    Body:
        character_id: which party member (must match a member of the
            session's party; 404 otherwise).
        primary: ability name (``strength``, ``dexterity``, ...).
        secondary: optional second ability for the +1 / +1 split.

    Returns the re-serialized state. The frontend clears the modal
    after this resolves successfully (200 + Character.pending_asi is
    None). 422 if the choice is invalid (unknown ability, cap
    exceeded, or no pending ASI for that character).
    """
    from auto_dm.character import resolve_asi_choice as _resolve
    from auto_dm.state.models import Ability

    sess = await sm.get(user.id, session_id)
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )

    character = next(
        (c for c in sess.state.party if c.id == body.character_id), None
    )
    if character is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Character not in party",
        )

    try:
        primary = Ability(body.primary)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown primary ability: {body.primary}",
        )
    secondary: Ability | None = None
    if body.secondary is not None:
        try:
            secondary = Ability(body.secondary)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown secondary ability: {body.secondary}",
            )
        if secondary == primary:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="ASI split must use two different abilities.",
            )

    try:
        scores = _resolve(character, primary=primary, secondary=secondary)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    await sm.save(sess)
    return {
        "session_id": session_id,
        "character_id": character.id,
        "character_name": character.name,
        "abilities": scores.model_dump(mode="json"),
        "state": sess.state.model_dump(mode="json"),
    }


@router.post("/sessions/{session_id}/reaction")
async def session_reaction(
    session_id: str,
    body: ReactionRequest,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, Any]:
    """Phase 41c — answer a published reaction trigger.

    Finds the party member holding an open ``pending_reaction`` (the
    player, normally). ``decline=True`` clears it without resolving
    (auto-pass / explicit pass). Otherwise resolves ``kind`` via
    ``apply_reaction``, clears ``pending_reaction`` and persists.

    Returns the resolution (message, success, mechanical), the updated
    character, and the full state. 404 if no trigger is open; 422 if the
    chosen ``kind`` is not in ``reactions_eligible`` or is unknown.
    """
    import time as _time
    from dataclasses import asdict
    from auto_dm.engine.actions import (
        ReactionKind,
        pending_reaction_is_expired,
        trigger_from_payload,
    )
    from auto_dm.engine.reactions import apply_reaction

    sess = await sm.get(user.id, session_id)
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )

    responder = next(
        (c for c in sess.state.party if c.pending_reaction), None,
    )
    if responder is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending reaction for this session.",
        )

    pending = responder.pending_reaction
    # Expired triggers are silently declined (auto-pass).
    if pending_reaction_is_expired(pending, now_epoch=int(_time.time())):
        responder.pending_reaction = None
        await sm.save(sess)
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail="Reaction window expired.",
        )

    if body.decline:
        responder.pending_reaction = None
        await sm.save(sess)
        return {
            "session_id": session_id,
            "declined": True,
            "character_id": responder.id,
            "character_name": responder.name,
            "state": sess.state.model_dump(mode="json"),
        }

    try:
        kind = ReactionKind(body.kind)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown reaction kind: {body.kind}",
        )

    eligible = pending.get("reactions_eligible") or []
    if kind.value not in eligible:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{kind.value} is not eligible for this trigger.",
        )

    trigger = trigger_from_payload(pending.get("trigger") or {})
    combat_engine = sess.combat_engine or CombatEngine()
    resolution = apply_reaction(
        combat_engine,
        sess.state_manager,
        responder.id,
        kind,
        trigger,
        slot_level=body.slot_level,
        check_roll=body.check_roll,
    )
    # ``apply_reaction`` already cleared pending_reaction on success; on
    # failure clear it too so the player isn't re-prompted with a broken
    # option. The narration reports what went wrong.
    responder.pending_reaction = None
    await sm.save(sess)

    return {
        "session_id": session_id,
        "declined": False,
        "character_id": responder.id,
        "character_name": responder.name,
        "resolution": asdict(resolution),
        "state": sess.state.model_dump(mode="json"),
    }


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> None:
    """Discard a session (does NOT delete persistent saves)."""
    await sm.delete(user.id, session_id)


@router.post("/sessions/{session_id}/opening")
async def session_opening(
    session_id: str,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Generate (or return) the campaign opening narration.

    Called by the frontend right after entering a freshly-created game,
    so the player sees the first scene without typing anything. The DM
    also picks the starting location and records it via a ``move``
    action, which is applied to ``state.current_location``.

    Idempotent: if ``narrative_log`` already holds an entry (e.g. a
    loaded save), the opening was already generated and the existing
    first DM narration is returned without an LLM call.

    Enforces the daily quota (429 when exceeded) and records token usage
    with ``kind="opening"``.
    """
    sess = await sm.get(user.id, session_id)
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )
    # Already opened (e.g. loaded save) — return the existing narration.
    existing_dm = next(
        (e for e in sess.state.narrative_log if e.role == "dm"), None
    )
    if existing_dm is not None:
        return {
            "session_id": session_id,
            "narration": existing_dm.content,
            "state": sess.state.model_dump(mode="json"),
        }
    # Quota check before the LLM call.
    settings = get_settings()
    exceeded = await check_quota(db, user, settings)
    if exceeded is not None:
        await log_activity(
            db,
            user_id=user.id,
            event=ActivityType.LIMIT_BLOCKED,
            meta={
                "endpoint": "opening",
                **{k: exceeded[k] for k in ("used", "limit", "unit")},
            },
        )
        raise HTTPException(status_code=429, detail=exceeded)
    try:
        result = generate_opening(sess.state_manager, sess.dm_agent)
    except Exception as exc:
        logger.exception("generate_opening failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Game error: {exc}",
        )
    await sm.save(sess)
    usages: list[UsageReport] = list(getattr(result, "usages", []) or [])
    if usages:
        try:
            await persist_usage_events(
                db,
                user_id=user.id,
                endpoint=f"/api/sessions/{session_id}/opening",
                reports=usages,
                settings=settings,
                session_id=session_id,
                kind="opening",
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist opening usage events")
    return {
        "session_id": session_id,
        "narration": result.narration,
        "state": sess.state.model_dump(mode="json"),
    }


# ============================================================================
# Save endpoints
# ============================================================================


@router.get("/saves", response_model=list[SaveOut])
async def list_saves(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    archived: bool = False,
) -> list[SaveOut]:
    """List persistent saves for the current user.

    By default only active (non-archived) saves are returned. Pass
    ``?archived=true`` to list archived saves instead — handy for a
    separate "Arquivados" section in the lobby.
    """
    result = await session.execute(
        select(Save)
        .where(Save.user_id == user.id, Save.archived == archived)
        .order_by(Save.updated_at.desc())
    )
    saves = result.scalars().all()
    return [SaveOut.from_save(s) for s in saves]


@router.post("/saves", response_model=SaveOut, status_code=status.HTTP_201_CREATED)
async def upsert_save(
    body: SaveRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SaveOut:
    """Create or update a save (upsert by user_id + slug)."""
    # Validate the state so we don't store garbage.
    try:
        GameState.model_validate(body.state)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid GameState: {exc}",
        )
    state_json = GameState.model_validate(body.state).model_dump_json()
    result = await session.execute(
        select(Save).where(Save.user_id == user.id, Save.slug == body.slug)
    )
    save = result.scalar_one_or_none()
    if save is None:
        save = Save(user_id=user.id, slug=body.slug, state=state_json)
        session.add(save)
    else:
        save.state = state_json
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Save slug conflict",
        )
    await session.refresh(save)
    return SaveOut.from_save(save)


@router.post("/saves/{slug}/load")
async def load_save(
    slug: str,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, Any]:
    """Load a save and create a new active session from it."""
    result = await session.execute(
        select(Save).where(Save.user_id == user.id, Save.slug == slug)
    )
    save = result.scalar_one_or_none()
    if save is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Save {slug!r} not found",
        )
    state = GameState.model_validate_json(save.state)
    sess = await sm.create(user.id, state)
    return {
        "session_id": sess.session_id,
        "slug": slug,
        "state": sess.state.model_dump(mode="json"),
    }


@router.delete("/saves/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_save(
    slug: str,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Delete a persistent save."""
    result = await session.execute(
        select(Save).where(Save.user_id == user.id, Save.slug == slug)
    )
    save = result.scalar_one_or_none()
    if save is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Save {slug!r} not found",
        )
    await session.delete(save)
    await session.commit()


async def _set_save_archived(
    slug: str,
    user: User,
    session: AsyncSession,
    archived: bool,
) -> SaveOut:
    """Flip the archived flag on a save. Shared by archive/unarchive."""
    result = await session.execute(
        select(Save).where(Save.user_id == user.id, Save.slug == slug)
    )
    save = result.scalar_one_or_none()
    if save is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Save {slug!r} not found",
        )
    save.archived = archived
    await session.commit()
    await session.refresh(save)
    return SaveOut.from_save(save)


@router.post("/saves/{slug}/archive", response_model=SaveOut)
async def archive_save(
    slug: str,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SaveOut:
    """Hide a save from the default lobby list without deleting it."""
    return await _set_save_archived(slug, user, session, archived=True)


@router.post("/saves/{slug}/unarchive", response_model=SaveOut)
async def unarchive_save(
    slug: str,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SaveOut:
    """Restore an archived save back to the default lobby list."""
    return await _set_save_archived(slug, user, session, archived=False)

"""Game routes: sessions + saves (Phase 26).

Endpoints (all require Authorization: Bearer <token>):

- POST   /api/sessions                 → create a new session from GameState JSON
- GET    /api/sessions                 → list active session ids
- GET    /api/sessions/{sid}           → load a session's current state
- POST   /api/sessions/{sid}/input     → send a player input line, returns NarrativeResult
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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auto_dm.agents import process_player_action
from auto_dm.llm.usage import UsageReport
from auto_dm.state.models import GameState
from auto_dm.web.activity import log_activity
from auto_dm.web.auth import current_user, require_admin
from auto_dm.web.config import get_settings
from auto_dm.web.db import get_session
from auto_dm.web.limits import check_quota
from auto_dm.web.models import ActivityType, Save, User
from auto_dm.web.sessions import SessionManager
from auto_dm.web.sse import format_sse, stream_dm_narration
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


class StreamRequest(BaseModel):
    """Body for POST /api/sessions/{sid}/stream — the same shape as
    ``InputRequest`` but kept distinct so the OpenAPI schema is clear
    that the response is a stream."""
    line: str = Field(..., min_length=1, max_length=2000)


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
    usages: list[UsageReport] = list(getattr(result, "usages", []) or [])
    if usages:
        try:
            await persist_usage_events(
                db,
                user_id=user.id,
                endpoint=f"/api/sessions/{session_id}/input",
                reports=usages,
                settings=settings,
                session_id=session_id,
                kind="player",
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


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> None:
    """Discard a session (does NOT delete persistent saves)."""
    await sm.delete(user.id, session_id)


@router.post("/sessions/{session_id}/stream")
async def session_stream(
    session_id: str,
    body: StreamRequest,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """Stream the DM narration for a player input via Server-Sent Events.

    The response is ``text/event-stream`` — each event is a JSON
    object on a ``data:`` line:

    - ``{"type": "start"}``                              — on open
    - ``{"type": "token", "data": "<chunk>"}``           — as the LLM yields
    - ``{"type": "usage", "data": {...}}``               — token counts (pre-done)
    - ``{"type": "done", "data": "<state-json>"}``       — stream complete
    - ``{"type": "error", "data": "<msg>"}``             — on failure

    Note: this endpoint only streams the *narration* layer — it does
    NOT parse actions, dispatch combat, or run companion turns.
    Use ``POST /api/sessions/{sid}/input`` for the full game loop.
    """
    sess = await sm.get(user.id, session_id)
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )
    # Quota check before opening the stream (can't abort mid-stream).
    settings = get_settings()
    exceeded = await check_quota(db, user, settings)
    if exceeded is not None:
        await log_activity(
            db,
            user_id=user.id,
            event=ActivityType.LIMIT_BLOCKED,
            meta={"endpoint": "stream", **{k: exceeded[k] for k in ("used", "limit", "unit")}},
        )
        raise HTTPException(status_code=429, detail=exceeded)

    user_id = user.id

    async def _event_generator():
        # Send a `start` event immediately so the client knows the
        # connection is live even before the first token arrives.
        yield format_sse({"type": "start", "data": session_id})
        usage_payload: dict | None = None
        async for event in stream_dm_narration(sess, body.line):
            if event.get("type") == "usage":
                usage_payload = event.get("data")
            yield format_sse(event)
        # Best-effort state refresh — the LLM doesn't mutate the
        # state during stream(), but the TTL on Redis should be
        # refreshed periodically for an active player.
        try:
            await sm.save(sess)
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to refresh SSE session state: %s", exc)
        # Persist the streamed turn's usage (best-effort).
        if usage_payload:
            try:
                report = UsageReport(
                    prompt_tokens=int(usage_payload.get("prompt_tokens", 0)),
                    completion_tokens=int(usage_payload.get("completion_tokens", 0)),
                    total_tokens=int(
                        usage_payload.get("total_tokens")
                        or (
                            int(usage_payload.get("prompt_tokens", 0))
                            + int(usage_payload.get("completion_tokens", 0))
                        )
                    ),
                    provider=getattr(sess.dm_agent.provider, "name", "") or "",
                    model=getattr(
                        getattr(sess.dm_agent.provider, "config", None), "model", ""
                    )
                    or "",
                    source=usage_payload.get("source", "fallback"),
                )
                from auto_dm.web.db import get_session_factory

                async with get_session_factory()() as usage_db:
                    await persist_usage_events(
                        usage_db,
                        user_id=user_id,
                        endpoint=f"/api/sessions/{session_id}/stream",
                        reports=[report],
                        settings=get_settings(),
                        session_id=session_id,
                        kind="player",
                    )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to persist streamed usage")

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx: disable buffering
            "Connection": "keep-alive",
        },
    )


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

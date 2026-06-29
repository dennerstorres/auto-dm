"""Game routes: sessions + saves (Phase 26).

Endpoints (all require Authorization: Bearer <token>):

- POST   /api/sessions                 → create a new session from GameState JSON
- GET    /api/sessions                 → list active session ids
- GET    /api/sessions/{sid}           → load a session's current state
- POST   /api/sessions/{sid}/input     → send a player input line, returns NarrativeResult
- DELETE /api/sessions/{sid}           → discard a session

- GET    /api/saves                    → list persisted saves for the user
- POST   /api/saves                    → create or update a save (auto-called after input)
- POST   /api/saves/{slug}/load        → hydrate a session from a save
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
from auto_dm.state.models import GameState
from auto_dm.web.auth import current_user
from auto_dm.web.db import get_session
from auto_dm.web.models import Save, User
from auto_dm.web.sessions import SessionManager
from auto_dm.web.sse import format_sse, stream_dm_narration

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

    @classmethod
    def from_save(cls, save: Save) -> "SaveOut":
        return cls(
            slug=save.slug,
            updated_at=save.updated_at.isoformat() if save.updated_at else "",
            created_at=save.created_at.isoformat() if save.created_at else "",
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
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> SessionCreated:
    """Create a new active game session from a GameState payload."""
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
) -> dict[str, Any]:
    """Send a player input line to a session.

    Returns the ``NarrativeResult`` (narration, action, action_result,
    follow_up, error). The session state is auto-persisted to Redis.
    """
    sess = await sm.get(user.id, session_id)
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )
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
) -> StreamingResponse:
    """Stream the DM narration for a player input via Server-Sent Events.

    The response is ``text/event-stream`` — each event is a JSON
    object on a ``data:`` line:

    - ``{"type": "start"}``                              — on open
    - ``{"type": "token", "data": "<chunk>"}``           — as the LLM yields
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

    async def _event_generator():
        # Send a `start` event immediately so the client knows the
        # connection is live even before the first token arrives.
        yield format_sse({"type": "start", "data": session_id})
        async for event in stream_dm_narration(sess, body.line):
            yield format_sse(event)
        # Best-effort state refresh — the LLM doesn't mutate the
        # state during stream(), but the TTL on Redis should be
        # refreshed periodically for an active player.
        try:
            await sm.save(sess)
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to refresh SSE session state: %s", exc)

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
) -> list[SaveOut]:
    """List persistent saves for the current user."""
    result = await session.execute(
        select(Save).where(Save.user_id == user.id).order_by(Save.updated_at.desc())
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

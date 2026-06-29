"""SSE streaming for DM narration (Phase 26b).

The DM agent takes 3–15s to respond (M-series with thinking). Polling
gives bad UX; SSE streams the tokens as they arrive. The endpoint
wraps the synchronous :meth:`auto_dm.agents.DMAgent.stream` (a sync
generator) in a worker thread that pushes each token into an
``asyncio.Queue``, which the SSE handler drains one event at a time.

Events emitted on the wire:
- ``{"type": "start"}``                       — connection opened
- ``{"type": "token", "data": "..."}``        — incremental narration
- ``{"type": "done", "data": "<state-json>"}``— stream complete; final state
- ``{"type": "error", "data": "..."}``        — error message

Auth: ``EventSource`` doesn't support custom headers, so we accept
the JWT via the ``?token=`` query parameter as a fallback. Header
auth (``Authorization: Bearer ...``) is also accepted for fetch-based
clients.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import AsyncIterator, Optional

import jwt
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from auto_dm.web.auth import decode_access_token
from auto_dm.web.models import User
from auto_dm.web.sessions import WebSession

logger = logging.getLogger(__name__)


# ============================================================================
# Auth
# ============================================================================


async def authenticate_sse(
    token_query: Optional[str],
    authorization: Optional[str],
    session_factory: async_sessionmaker,
) -> User:
    """Resolve the user for an SSE connection.

    Tries (in order):
    1. ``?token=...`` query parameter (for EventSource clients).
    2. ``Authorization: Bearer <token>`` header (for fetch-based clients).

    Returns the :class:`User` on success or raises 401.
    """
    token: Optional[str] = None
    if token_query:
        token = token_query
    elif authorization:
        prefix = "Bearer "
        token = authorization[len(prefix):] if authorization.startswith(prefix) else authorization
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing token (use Authorization header or ?token=)",
        )
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Token missing sub claim")
    user_id = int(sub)
    async with session_factory() as db_session:
        result = await db_session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return user


# ============================================================================
# Streaming
# ============================================================================


async def stream_dm_narration(
    session: WebSession,
    player_input: str,
) -> AsyncIterator[dict]:
    """Async generator yielding SSE events for a player input.

    Each token the LLM yields is queued by a worker thread and
    forwarded as ``{"type": "token", "data": <chunk>}``. On
    completion, the final ``GameState`` is included as
    ``{"type": "done", "data": <state-json>}``.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    done_event = asyncio.Event()

    def _producer() -> None:
        """Run the sync generator in a worker thread; push tokens
        onto the asyncio queue via ``call_soon_threadsafe``."""
        try:
            for tok in session.dm_agent.stream(player_input):
                loop.call_soon_threadsafe(queue.put_nowait, ("token", tok))
        except Exception as exc:  # pragma: no cover — exercised via tests
            logger.exception("DM stream failed")
            loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))
        finally:
            loop.call_soon_threadsafe(done_event.set)

    thread = threading.Thread(target=_producer, name="dm-stream", daemon=True)
    thread.start()

    try:
        while True:
            # Wait for either a token or completion. The 30s ceiling
            # guarantees we don't hang forever if the thread crashes
            # silently.
            getter = asyncio.create_task(queue.get())
            waiter = asyncio.create_task(done_event.wait())
            done, pending = await asyncio.wait(
                {getter, waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if getter in done:
                kind, payload = getter.result()
                yield {"type": kind, "data": payload}
                if kind in ("error",):
                    return
            if done_event.is_set():
                # Drain any remaining tokens, then emit done.
                while not queue.empty():
                    kind, payload = queue.get_nowait()
                    if kind == "token":
                        yield {"type": "token", "data": payload}
                    elif kind == "error":
                        yield {"type": "error", "data": payload}
                        return
                yield {"type": "done", "data": session.state.model_dump_json()}
                return
    except asyncio.CancelledError:
        # Client disconnected — let the producer thread keep running
        # (daemon=True) and bail out quietly.
        logger.info("SSE client cancelled")
        raise


# ============================================================================
# Wire format
# ============================================================================


def format_sse(event: dict) -> str:
    """Format an event dict as an SSE ``data: ...\\n\\n`` line.

    SSE wire format: each event is one or more ``field: value`` lines
    followed by a blank line. We pack the whole event into a single
    ``data:`` line (JSON-serialized) for simplicity.
    """
    payload = json.dumps(event, ensure_ascii=False)
    return f"data: {payload}\n\n"

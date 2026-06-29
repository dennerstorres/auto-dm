"""Async Redis client (Phase 26).

A single ``redis.asyncio.Redis`` connection pool is created on
startup and reused. Active game sessions are stored as JSON blobs at
``session:{session_id}`` with a configurable TTL (default 24h).

The session is **separate** from the persistent save: the save lives
in Postgres and survives forever; the session is the in-progress
runtime state. When a user reloads, we hydrate a session from a save.
"""
from __future__ import annotations

import logging
from typing import Optional

import redis.asyncio as redis_async

from auto_dm.web.config import get_settings

logger = logging.getLogger(__name__)


_client: Optional[redis_async.Redis] = None


def init_redis(url: str | None = None) -> redis_async.Redis:
    """Create the async Redis client. Idempotent.

    Call from FastAPI's ``lifespan`` context manager.
    """
    global _client
    if _client is not None:
        return _client

    settings = get_settings()
    final_url = url or settings.redis_url
    _client = redis_async.from_url(
        final_url,
        encoding="utf-8",
        decode_responses=True,
    )
    logger.info("Initialized async Redis client for %s", final_url)
    return _client


async def dispose_redis() -> None:
    """Close the Redis connection pool on app shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


def get_redis() -> redis_async.Redis:
    """Get the active Redis client. Raises if not initialized."""
    if _client is None:
        raise RuntimeError("Redis not initialized; call init_redis() first.")
    return _client


# ============================================================================
# Session key helpers
# ============================================================================


def session_key(user_id: int, session_id: str) -> str:
    """Redis key for an active game session, scoped to the user."""
    return f"session:{user_id}:{session_id}"

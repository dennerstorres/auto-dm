"""Per-scope token-bucket rate limiting (Phase 38).

Smaller and cheaper than the daily quota in :mod:`auto_dm.web.limits`.
Used by ``/api/sessions/{sid}/award-xp`` to cap manual XP grants at
10 per minute per session — the endpoint is free (no LLM call) but
still needs a guard against runaway grants.

Backed by Redis (same DB the session manager uses). Falls back to
``allowed=True`` when Redis is unreachable so a Redis outage doesn't
break the gameplay loop; callers should log when this happens.

Scope keys are passed in by the caller: ``f"award-xp:{session_id}"``.
The function doesn't introspect the scope — any string is valid.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RateLimitDecision:
    allowed: bool
    limit: int
    window_seconds: int
    retry_after: int  # seconds; valid only when allowed=False

    @classmethod
    def allow(cls, limit: int, window_seconds: int) -> "RateLimitDecision":
        return cls(
            allowed=True,
            limit=limit,
            window_seconds=window_seconds,
            retry_after=0,
        )

    @classmethod
    def block(cls, limit: int, window_seconds: int, retry_after: int) -> "RateLimitDecision":
        return cls(
            allowed=False,
            limit=limit,
            window_seconds=window_seconds,
            retry_after=retry_after,
        )


async def check_rate_limit(
    *,
    scope: str,
    limit: int,
    window_seconds: int,
) -> RateLimitDecision:
    """Increment the per-scope counter and decide whether ``limit`` was reached.

    Counter is set with a TTL of ``window_seconds`` so the bucket resets
    automatically. The first request that exceeds the limit returns
    ``allowed=False`` plus a ``retry_after`` equal to the remaining
    seconds on the TTL.

    Implementation: redis ``INCR`` + ``EXPIRE`` on first creation. If the
    Redis client is missing or an exception is raised, the function
    defaults to ``allowed=True`` (fail-open). Callers should log this.
    """
    try:
        from auto_dm.web.redis_client import get_redis

        redis = get_redis()
    except Exception:  # noqa: BLE001
        redis = None

    if redis is None:
        logger.warning("rate_limit: redis unavailable, failing open")
        return RateLimitDecision.allow(limit, window_seconds)

    key = f"rate:{scope}"
    try:
        count = await redis.incr(key)
        if count == 1:
            # First request in this window — set the TTL.
            await redis.expire(key, window_seconds)
        ttl = await redis.ttl(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rate_limit: redis error, failing open: %s", exc)
        return RateLimitDecision.allow(limit, window_seconds)

    if count > limit:
        return RateLimitDecision.block(
            limit=limit,
            window_seconds=window_seconds,
            retry_after=max(1, int(ttl)) if ttl and ttl > 0 else window_seconds,
        )
    return RateLimitDecision.allow(limit=limit, window_seconds=window_seconds)

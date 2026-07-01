"""Daily quota enforcement for LLM usage.

:func:`check_quota` is the single choke point: the game routes call it
before invoking the LLM. Returns ``None`` when the user is within quota
(or exempt), or a ``dict`` describing the exceeded limit so the route
can raise ``HTTPException(429, detail=...)``.

Exemptions:
- ``user.unlimited`` (admin override) → never blocked.
- ``user.role == admin`` → admins are always exempt.

Windows reset at UTC midnight. This is a read-then-act check (TOCTOU);
acceptable for a hobby project. A future hardening could increment a
Redis counter ``quota:{user_id}:{date}`` atomically.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from auto_dm.web.config import Settings
from auto_dm.web.models import User, UserRole
from auto_dm.web.usage import minutes_today, usage_today


def next_utc_midnight(now: Optional[datetime] = None) -> datetime:
    """The next UTC midnight (when the quota window resets)."""
    now = now or datetime.now(timezone.utc)
    midnight = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    if midnight <= now:
        midnight = datetime.combine(
            now.date().fromordinal(now.date().toordinal() + 1),
            time.min,
            tzinfo=timezone.utc,
        )
    return midnight


async def check_quota(
    session: AsyncSession, user: User, settings: Settings
) -> Optional[dict]:
    """Return ``None`` if within quota, else a 429-detail dict.

    The dict shape: ``{"detail", "used", "limit", "unit", "reset_at"}``.
    """
    if user.unlimited or user.role == UserRole.ADMIN.value:
        return None

    # Token limit (per-user override or global default).
    token_limit = user.daily_token_limit
    if token_limit is None:
        token_limit = settings.default_daily_token_limit
    used_tokens = await usage_today(session, user.id)
    if used_tokens >= token_limit:
        return {
            "detail": "Limite diário de tokens atingido",
            "used": used_tokens,
            "limit": token_limit,
            "unit": "tokens",
            "reset_at": next_utc_midnight().isoformat(),
        }

    # Minutes limit (only if a cap is configured somewhere).
    minute_limit = user.daily_minutes_limit
    if minute_limit is None and settings.default_daily_minutes_limit:
        minute_limit = settings.default_daily_minutes_limit
    if minute_limit:
        used_minutes = await minutes_today(session, user.id)
        if used_minutes >= minute_limit:
            return {
                "detail": "Limite diário de minutos atingido",
                "used": used_minutes,
                "limit": minute_limit,
                "unit": "minutes",
                "reset_at": next_utc_midnight().isoformat(),
            }

    return None

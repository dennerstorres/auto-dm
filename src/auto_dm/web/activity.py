"""Activity-log helper for the admin panel.

Tiny wrapper around :class:`auto_dm.web.models.ActivityLog` so the
handful of call sites (auth login/signup, quota blocks, admin actions)
don't repeat the try/except dance. Activity logging is always
best-effort: a failure must never break the operation it records.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from auto_dm.web.models import ActivityLog, ActivityType

logger = logging.getLogger(__name__)


async def log_activity(
    session: AsyncSession,
    *,
    user_id: int,
    event: ActivityType,
    meta: Optional[dict[str, Any]] = None,
) -> None:
    """Append an :class:`ActivityLog` row and commit (best-effort)."""
    try:
        session.add(
            ActivityLog(
                user_id=user_id,
                event_type=event.value,
                meta=meta,
            )
        )
        await session.commit()
    except Exception:  # noqa: BLE001 — logging must never raise
        logger.exception("Failed to record activity %s for user %s", event, user_id)
        try:
            await session.rollback()
        except Exception:  # pragma: no cover
            pass

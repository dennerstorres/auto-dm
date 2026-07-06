"""User preferences routes (Phase 42c).

- ``GET /api/me/preferences`` → merged preferences (defaults back-filled).
- ``PATCH /api/me/preferences`` → partial ``{tts?, music?}`` update.

Preferences persist in the ``users.preferences`` JSON column. Validation + clamp
lives in :mod:`auto_dm.web.preferences` so the rules are testable without a DB.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from auto_dm.web.auth import current_user
from auto_dm.web.db import get_session
from auto_dm.web.models import User
from auto_dm.web.preferences import merge_defaults, validate_patch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/me", tags=["preferences"])


class PreferencesPatch(BaseModel):
    """Partial preferences update. Both sections optional."""

    tts: dict[str, Any] | None = None
    music: dict[str, Any] | None = None


def _deep_merge(stored: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Section-wise deep merge of a validated patch into stored preferences."""
    merged = merge_defaults(stored)  # start from the normalized stored blob
    for section, values in patch.items():
        if isinstance(values, dict):
            merged.setdefault(section, {})
            merged[section].update(values)
    return merged


@router.get("/preferences")
async def get_preferences(
    user: Annotated[User, Depends(current_user)],
) -> dict[str, Any]:
    """Return the user's preferences with defaults back-filled."""
    return merge_defaults(user.preferences)


@router.patch("/preferences")
async def patch_preferences(
    body: PreferencesPatch,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Update a subset of preferences and return the merged result."""
    patch_raw: dict[str, Any] = {}
    if body.tts is not None:
        patch_raw["tts"] = body.tts
    if body.music is not None:
        patch_raw["music"] = body.music
    try:
        patch = validate_patch(patch_raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    stored = user.preferences or {}
    merged = _deep_merge(stored, patch)
    user.preferences = merged
    await session.commit()
    return merge_defaults(merged)

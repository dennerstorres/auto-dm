"""Admin routes: cross-user save + user management (Phases 29–30).

Endpoints (all require ``Authorization: Bearer <token>`` **and** the
``admin`` role):

Saves:
- GET    /api/admin/saves?archived=false  → list every user's saves (with owner)
- GET    /api/admin/saves/{user_id}/{slug} → fetch a save's state + narrative log
                                            (read-only viewing; no session, no LLM)
- DELETE /api/admin/saves/{user_id}/{slug} → delete any user's save

Users (Phase 30):
- GET    /api/admin/users                 → list users with usage/cost aggregations
- POST   /api/admin/users                 → create a user (bypasses invite code)
- GET    /api/admin/users/{id}            → user detail + effective limits + usage
- PATCH  /api/admin/users/{id}            → update limits / active / unlimited / role
- POST   /api/admin/users/{id}/reset-password
- DELETE /api/admin/users/{id}            → delete user (cascade; protections)
- GET    /api/admin/users/{id}/activity   → recent activity log
- GET    /api/admin/users/{id}/usage      → per-day usage series
- GET    /api/admin/usage/summary         → system-wide cost/token summary
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from auto_dm.state.models import GameState
from auto_dm.web.activity import log_activity
from auto_dm.web.auth import hash_password, require_admin
from auto_dm.web.config import get_settings
from auto_dm.web.db import get_session
from auto_dm.web.models import (
    ActivityLog,
    ActivityType,
    Save,
    UsageEvent,
    User,
    UserRole,
)
from auto_dm.web.usage import (
    cost_this_month,
    minutes_today,
    start_of_month_utc,
    usage_by_day,
    usage_today,
)
from auto_dm.web.save_metadata import extract_save_metadata

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# ============================================================================
# Schemas
# ============================================================================


class AdminSaveOut(BaseModel):
    """Save metadata with the owning username — admin view."""

    slug: str
    user_id: int
    username: str
    updated_at: str
    created_at: str
    archived: bool = False
    campaign_name: str = ""
    character_name: str = ""
    character_level: int | None = None
    current_location: str = ""

    @classmethod
    def from_save(cls, save: Save) -> "AdminSaveOut":
        return cls(
            slug=save.slug,
            user_id=save.user_id,
            username=save.user.username,
            updated_at=save.updated_at.isoformat() if save.updated_at else "",
            created_at=save.created_at.isoformat() if save.created_at else "",
            archived=bool(save.archived),
            **extract_save_metadata(save.state),
        )


# ============================================================================
# Helpers
# ============================================================================


async def _load_save(
    session: AsyncSession, user_id: int, slug: str
) -> Save:
    """Fetch a save by (user_id, slug) or 404. Cross-user (admin)."""
    result = await session.execute(
        select(Save)
        .options(joinedload(Save.user))
        .where(Save.user_id == user_id, Save.slug == slug)
    )
    save = result.scalar_one_or_none()
    if save is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Save {slug!r} for user {user_id} not found",
        )
    return save


# ============================================================================
# Endpoints
# ============================================================================


@router.get("/saves", response_model=list[AdminSaveOut])
async def list_all_saves(
    session: Annotated[AsyncSession, Depends(get_session)],
    archived: bool = False,
) -> list[AdminSaveOut]:
    """List saves across **all** users, newest first.

    Pass ``?archived=true`` to list archived saves instead. Each row
    includes the owning username so the admin can tell saves apart.
    """
    result = await session.execute(
        select(Save)
        .options(joinedload(Save.user))
        .join(User)
        .where(Save.archived == archived)
        .order_by(Save.updated_at.desc())
    )
    saves = result.scalars().all()
    return [AdminSaveOut.from_save(s) for s in saves]


@router.get("/saves/{user_id}/{slug}")
async def get_save_state(
    user_id: int,
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Return a save's full state + narrative log for read-only viewing.

    No session is created and the LLM is never invoked — this is a pure
    snapshot of the persisted save. The frontend renders the
    ``narrative_log`` read-only with input disabled.
    """
    save = await _load_save(session, user_id, slug)
    try:
        state = GameState.model_validate_json(save.state)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Save state is corrupted: {exc}",
        )
    data = state.model_dump(mode="json")
    return {
        "user_id": save.user_id,
        "username": save.user.username,
        "slug": slug,
        "archived": bool(save.archived),
        "state": data,
        "narrative_log": data["narrative_log"],
    }


@router.delete("/saves/{user_id}/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_save(
    user_id: int,
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Delete any user's save (works on archived and non-archived alike)."""
    save = await _load_save(session, user_id, slug)
    await session.delete(save)
    await session.commit()


# ============================================================================
# User management (Phase 30)
# ============================================================================


class AdminUserOut(BaseModel):
    """User with usage/cost aggregations for the admin panel."""

    id: int
    username: str
    role: str
    active: bool = True
    unlimited: bool = False
    daily_token_limit: Optional[int] = None
    daily_minutes_limit: Optional[int] = None
    disabled_reason: Optional[str] = None
    created_at: str
    tokens_today: int = 0
    minutes_today: int = 0
    cost_month: float = 0.0

    @classmethod
    async def from_user(
        cls, session: AsyncSession, user: User
    ) -> "AdminUserOut":
        tokens = await usage_today(session, user.id)
        minutes = await minutes_today(session, user.id)
        cost = await cost_this_month(session, user.id)
        return cls(
            id=user.id,
            username=user.username,
            role=user.role,
            active=bool(user.active),
            unlimited=bool(user.unlimited),
            daily_token_limit=user.daily_token_limit,
            daily_minutes_limit=user.daily_minutes_limit,
            disabled_reason=user.disabled_reason,
            created_at=user.created_at.isoformat() if user.created_at else "",
            tokens_today=tokens,
            minutes_today=minutes,
            cost_month=float(cost),
        )


class AdminCreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(..., min_length=8, max_length=128)
    role: Optional[str] = Field(default=None, pattern=r"^(user|admin)$")


class AdminUpdateUserRequest(BaseModel):
    daily_token_limit: Optional[int] = Field(default=None, ge=0)
    daily_minutes_limit: Optional[int] = Field(default=None, ge=0)
    unlimited: Optional[bool] = None
    active: Optional[bool] = None
    role: Optional[str] = Field(default=None, pattern=r"^(user|admin)$")
    disabled_reason: Optional[str] = Field(default=None, max_length=255)


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=128)


async def _load_user(session: AsyncSession, user_id: int) -> User:
    """Fetch a user by id or 404."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )
    return user


async def _count_active_admins(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count(User.id)).where(
            User.role == UserRole.ADMIN.value, User.active.is_(True)
        )
    )
    return int(result.scalar_one() or 0)


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AdminUserOut]:
    """List all users with today's usage and this month's cost."""
    result = await session.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    return [await AdminUserOut.from_user(session, u) for u in users]


@router.post("/users", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: AdminCreateUserRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> AdminUserOut:
    """Create a user directly (bypasses the invite-code gate)."""
    from sqlalchemy.exc import IntegrityError

    existing = await session.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )
    role = body.role or UserRole.USER.value
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=role,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )
    await session.refresh(user)
    await log_activity(
        session,
        user_id=admin.id,
        event=ActivityType.USER_CREATED,
        meta={"created_user_id": user.id, "username": user.username, "role": role},
    )
    # Refresh to load the activity row cleanly; from_user does reads.
    return await AdminUserOut.from_user(session, user)


@router.get("/users/{user_id}", response_model=AdminUserOut)
async def get_user(
    user_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminUserOut:
    """User detail + effective limits + current usage/cost."""
    user = await _load_user(session, user_id)
    return await AdminUserOut.from_user(session, user)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: int,
    body: AdminUpdateUserRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> AdminUserOut:
    """Update a user's limits, active flag, unlimited override, or role.

    Protections: you cannot deactivate or demote yourself, and you cannot
    remove the last active admin (deactivating or demoting them).
    """
    user = await _load_user(session, user_id)
    settings = get_settings()

    # ``None`` in the body means "field not provided" (partial update).
    # To explicitly clear a limit, the client is expected to send the
    # global default; per-user NULL handling means we only write when set.
    if body.daily_token_limit is not None:
        user.daily_token_limit = body.daily_token_limit
    if body.daily_minutes_limit is not None:
        user.daily_minutes_limit = body.daily_minutes_limit
    if body.unlimited is not None:
        user.unlimited = body.unlimited
    if body.disabled_reason is not None:
        user.disabled_reason = body.disabled_reason

    # Active flag with last-admin / self protections.
    if body.active is not None and body.active != user.active:
        demoting_admin = user.role == UserRole.ADMIN.value
        if user.id == admin.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You cannot change your own active status.",
            )
        if not body.active and demoting_admin and await _count_active_admins(session) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot disable the last active admin.",
            )
        user.active = body.active
        await log_activity(
            session,
            user_id=user.id,
            event=ActivityType.DISABLED if not body.active else ActivityType.REENABLED,
            meta={"by": admin.id, "reason": user.disabled_reason},
        )

    # Role change with last-admin / self protections.
    if body.role is not None and body.role != user.role:
        if user.id == admin.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You cannot change your own role.",
            )
        if (
            user.role == UserRole.ADMIN.value
            and body.role != UserRole.ADMIN.value
            and await _count_active_admins(session) <= 1
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot demote the last active admin.",
            )
        user.role = body.role

    await session.commit()
    await session.refresh(user)

    # Audit any limit/override change (best-effort).
    if (
        body.daily_token_limit is not None
        or body.daily_minutes_limit is not None
        or body.unlimited is not None
    ):
        await log_activity(
            session,
            user_id=user.id,
            event=ActivityType.LIMIT_OVERRIDE,
            meta={
                "by": admin.id,
                "daily_token_limit": user.daily_token_limit,
                "daily_minutes_limit": user.daily_minutes_limit,
                "unlimited": bool(user.unlimited),
                "default_token_limit": settings.default_daily_token_limit,
            },
        )

    return await AdminUserOut.from_user(session, user)


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: int,
    body: ResetPasswordRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> None:
    """Set a new password for any user."""
    user = await _load_user(session, user_id)
    user.password_hash = hash_password(body.new_password)
    await session.commit()
    await log_activity(
        session,
        user_id=user.id,
        event=ActivityType.PASSWORD_RESET,
        meta={"by": admin.id},
    )


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> None:
    """Delete a user and cascade their saves/usage/activity.

    Protections: you cannot delete yourself, and you cannot delete the
    last active admin.
    """
    user = await _load_user(session, user_id)
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot delete your own account.",
        )
    if (
        user.role == UserRole.ADMIN.value
        and await _count_active_admins(session) <= 1
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete the last active admin.",
        )
    # Audit before the cascade wipes the target's activity rows.
    await log_activity(
        session,
        user_id=admin.id,
        event=ActivityType.USER_DELETED,
        meta={"deleted_user_id": user.id, "username": user.username},
    )
    await session.delete(user)
    await session.commit()


@router.get("/users/{user_id}/activity")
async def user_activity(
    user_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 50,
) -> dict[str, Any]:
    """Recent activity-log entries for a user (newest first)."""
    await _load_user(session, user_id)
    limit = max(1, min(limit, 500))
    result = await session.execute(
        select(ActivityLog)
        .where(ActivityLog.user_id == user_id)
        .order_by(ActivityLog.created_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return {
        "user_id": user_id,
        "activity": [
            {
                "id": r.id,
                "event_type": r.event_type,
                "meta": r.meta,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ],
    }


@router.get("/users/{user_id}/usage")
async def user_usage(
    user_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    days: int = 30,
) -> dict[str, Any]:
    """Per-day usage series for a user over the last ``days`` days."""
    from datetime import timedelta

    from auto_dm.web.usage import utc_now

    await _load_user(session, user_id)
    days = max(1, min(days, 365))
    start = utc_now() - timedelta(days=days)
    series = await usage_by_day(session, user_id, start)
    return {"user_id": user_id, "days": days, "series": series}


@router.get("/usage/summary")
async def usage_summary(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """System-wide usage summary for the dashboard."""
    month_start = start_of_month_utc()
    totals = await session.execute(
        select(
            func.coalesce(func.sum(UsageEvent.cost_usd), 0).label("cost"),
            func.coalesce(func.sum(UsageEvent.total_tokens), 0).label("tokens"),
        ).where(UsageEvent.created_at >= month_start)
    )
    cost, tokens = totals.one()
    active = await session.execute(
        select(func.count(User.id)).where(User.active.is_(True))
    )
    active_count = int(active.scalar_one() or 0)
    disabled = await session.execute(
        select(func.count(User.id)).where(User.active.is_(False))
    )
    disabled_count = int(disabled.scalar_one() or 0)

    # Top 5 users by cost this month.
    top_result = await session.execute(
        select(
            UsageEvent.user_id,
            User.username,
            func.coalesce(func.sum(UsageEvent.cost_usd), 0).label("cost"),
            func.coalesce(func.sum(UsageEvent.total_tokens), 0).label("tokens"),
        )
        .join(User, User.id == UsageEvent.user_id)
        .where(UsageEvent.created_at >= month_start)
        .group_by(UsageEvent.user_id, User.username)
        .order_by(func.sum(UsageEvent.cost_usd).desc())
        .limit(5)
    )
    top = [
        {
            "user_id": uid,
            "username": uname,
            "cost": float(Decimal(str(c or 0))),
            "tokens": int(t or 0),
        }
        for uid, uname, c, t in top_result.all()
    ]
    return {
        "month_start": month_start.isoformat(),
        "cost_usd": float(Decimal(str(cost or 0))),
        "tokens": int(tokens or 0),
        "active_users": active_count,
        "disabled_users": disabled_count,
        "top_users": top,
    }

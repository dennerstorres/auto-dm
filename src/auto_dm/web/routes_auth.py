"""Auth routes: signup, login, me (Phase 26).

Signup: POST /api/auth/signup  →  { username, password [, invite_code] }
                                 →  { token, user }
Login:  POST /api/auth/login   →  { username, password }    →  { token, user }
Me:     GET  /api/auth/me      →  Authorization: Bearer     →  { user }

The token is a JWT; the frontend stores it in localStorage and
sends it as ``Authorization: Bearer <token>`` on every request.

Signup is always open.  A matching ``INVITE_CODE`` grants access to the
server-funded LLM; accounts created without a valid invite are BYOK-only.
"""
from __future__ import annotations

import hmac
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auto_dm.web.activity import log_activity
from auto_dm.web.auth import (
    create_access_token,
    current_user,
    hash_password,
    verify_password,
)
from auto_dm.web.config import get_settings
from auto_dm.web.db import get_session
from auto_dm.web.models import ActivityType, User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ============================================================================
# Schemas
# ============================================================================


class SignupRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(..., min_length=8, max_length=128)
    # Required when the server has ``INVITE_CODE`` set. Optional in
    # dev (when ``INVITE_CODE`` is unset, the field is ignored).
    invite_code: Optional[str] = Field(default=None, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    system_llm_access: bool
    created_at: str
    # Phase 42 — preferences blob (defaults back-filled). Surfaced on /me,
    # /login, /signup so the client can init TTS/music without an extra round
    # trip.
    preferences: dict = Field(default_factory=dict)

    model_config = {"from_attributes": True}

    @classmethod
    def from_user(cls, user: User) -> "UserOut":
        from auto_dm.web.preferences import merge_defaults

        return cls(
            id=user.id,
            username=user.username,
            role=user.role,
            system_llm_access=user.system_llm_access,
            created_at=user.created_at.isoformat() if user.created_at else "",
            preferences=merge_defaults(getattr(user, "preferences", None)),
        )


class TokenResponse(BaseModel):
    token: str
    user: UserOut
    expires_in_minutes: int


# ============================================================================
# Endpoints
# ============================================================================


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    """Create a new user. Returns a JWT and the public user info."""
    settings = get_settings()
    # Registration is open.  The invite is an entitlement, not a gate:
    # only a timing-safe exact match grants use of the server's LLM key.
    has_system_llm_access = bool(
        settings.invite_code
        and body.invite_code
        and hmac.compare_digest(body.invite_code, settings.invite_code)
    )
    # Reject if username taken.
    existing = await session.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        # Role is hardcoded — signup can never create an admin.
        role=UserRole.USER.value,
        system_llm_access=has_system_llm_access,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        # Race condition: another request created the same user.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )
    await session.refresh(user)
    token = create_access_token(user.id, user.username)
    await log_activity(
        session,
        user_id=user.id,
        event=ActivityType.SIGNUP,
        meta={"system_llm_access": has_system_llm_access},
    )
    return TokenResponse(
        token=token,
        user=UserOut.from_user(user),
        expires_in_minutes=settings.jwt_expires_minutes,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    """Authenticate a user and return a JWT."""
    from auto_dm.web.config import get_settings

    result = await session.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        # Same error for both cases — don't leak which is wrong.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    # Disabled accounts get the same generic error (anti-enumeration).
    if not user.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    settings = get_settings()
    token = create_access_token(user.id, user.username)
    await log_activity(session, user_id=user.id, event=ActivityType.LOGIN)
    return TokenResponse(
        token=token,
        user=UserOut.from_user(user),
        expires_in_minutes=settings.jwt_expires_minutes,
    )


@router.get("/me", response_model=UserOut)
async def me(
    user: Annotated[User, Depends(current_user)],
) -> UserOut:
    """Return the currently authenticated user."""
    return UserOut.from_user(user)

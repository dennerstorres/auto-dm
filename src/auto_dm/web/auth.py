"""Auth utilities: password hashing + JWT (Phase 26).

Passwords are hashed with bcrypt directly (passlib has a known
incompatibility with bcrypt 5.x on Python 3.11+). JWTs are HS256
with a configurable expiry (default 7 days). The token carries the
user id as ``sub`` and the username as a public claim.

The :func:`current_user` dependency extracts the JWT from the
``Authorization: Bearer <token>`` header and loads the
:class:`auto_dm.web.models.User` from Postgres. Use it as a FastAPI
dependency on any route that requires auth.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auto_dm.web.config import get_settings
from auto_dm.web.db import get_session
from auto_dm.web.models import User, UserRole

logger = logging.getLogger(__name__)


# Bcrypt cost factor. 12 is the passlib default; higher = slower but
# more secure. 12 is fine for a hobby project.
_BCRYPT_ROUNDS = 12

# Bcrypt has a 72-byte input limit. Truncate longer passwords to avoid
# a runtime error (modern guidance: hash the truncated bytes, not the
# original — this is exactly what bcrypt itself does internally).
_BCRYPT_MAX_BYTES = 72

# Bearer token security. ``auto_error=False`` so we can return our
# own error message (the default is to raise 403).
_bearer = HTTPBearer(auto_error=False)


# ============================================================================
# Password hashing
# ============================================================================


def _truncate(plain: str) -> bytes:
    """Encode + truncate to 72 bytes for bcrypt's input limit."""
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt. Returns the hash as a
    UTF-8 string suitable for storage in a VARCHAR column."""
    hashed = bcrypt.hashpw(_truncate(plain), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS))
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time password check. Returns False on any error."""
    try:
        return bcrypt.checkpw(_truncate(plain), hashed.encode("utf-8"))
    except Exception:
        return False


# ============================================================================
# JWT
# ============================================================================


def create_access_token(
    user_id: int,
    username: str,
    *,
    expires_minutes: Optional[int] = None,
) -> str:
    """Sign a JWT for the given user.

    The ``sub`` claim is the user id (as string, per JWT spec) and
    ``username`` is a public-readable claim for convenience.
    """
    settings = get_settings()
    exp = expires_minutes if expires_minutes is not None else settings.jwt_expires_minutes
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=exp)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Raises :class:`jwt.PyJWTError` on failure."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# ============================================================================
# FastAPI dependencies
# ============================================================================


async def current_user(
    request: Request,
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Resolve the current authenticated user from the Bearer token.

    Raises 401 on missing / invalid / expired token. Attaches the
    user to ``request.state.user`` for downstream handlers.
    """
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing sub claim",
        )
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token sub claim is not a valid user id",
        )
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
        )
    # Soft-disable: an admin-deactivated account can't use authenticated
    # routes even with a still-valid token (kills zombie sessions).
    if not user.active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Conta desativada",
        )
    request.state.user = user
    return user


async def require_admin(
    user: Annotated[User, Depends(current_user)],
) -> User:
    """Dependency that ensures the current user has the ``admin`` role.

    Raises 403 for regular users. Use on admin-only endpoints.
    """
    if user.role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin required",
        )
    return user

"""SQLAlchemy ORM models for Phase 26.

Two tables:

- ``users``: id, username (unique), password_hash, created_at.
- ``saves``: id, user_id (FK), slug, state (JSONB blob of GameState),
  created_at, updated_at. Uniqueness on (user_id, slug).

The saves table replaces the file-based saves directory. Game state
serializes cleanly to JSON via Pydantic's ``model_dump_json``, so we
store the raw text in a single column.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, Enum):
    """User roles. ``admin`` is a single seeded account; everyone else
    signs up as ``user``."""

    USER = "user"
    ADMIN = "admin"


class UsageKind(str, Enum):
    """What kind of LLM call a UsageEvent records."""

    PLAYER = "player"  # DM narration triggered by player input
    DM = "dm"  # DM follow-up narration of a mechanical result
    COMPANION = "companion"  # companion turn decision
    OPENING = "opening"  # campaign opening narration (no player input)
    SUMMARIZER = "summarizer"  # Phase 33 — periodic narrative summarizer LLM call
    NAMING = "naming"  # AI-suggested campaign/character names in the wizard


class ActivityType(str, Enum):
    """Auditable user activity events (admin panel log)."""

    LOGIN = "login"
    LOGOUT = "logout"
    SIGNUP = "signup"
    SESSION_START = "session_start"
    LIMIT_BLOCKED = "limit_blocked"
    DISABLED = "disabled"
    REENABLED = "reenabled"
    PASSWORD_RESET = "password_reset"
    USER_CREATED = "user_created"
    USER_DELETED = "user_deleted"
    LIMIT_OVERRIDE = "limit_override"


class Base(DeclarativeBase):
    """Base for all ORM models."""


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Authorization role. Defaults to ``user``; only the seeded admin
    # account is ever ``admin`` (signup cannot set this).
    role: Mapped[str] = mapped_column(
        String(20),
        default=UserRole.USER.value,
        server_default=UserRole.USER.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(),
    )
    # --- Admin-managed usage controls (Phase 30) -----------------------
    # Per-user daily token cap (NULL → fall back to the global default in
    # Settings.default_daily_token_limit). Enforced hard: once exceeded,
    # LLM calls return 429 until the next UTC midnight.
    daily_token_limit: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True,
    )
    # Per-user daily active-minutes cap (NULL → global default). Measured
    # as distinct active minutes (proxy, see web/usage.py::minutes_today).
    daily_minutes_limit: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
    )
    # ``True`` exempts the user from quota checks (admin override).
    unlimited: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False,
    )
    # Soft-disable: a disabled account cannot log in or use authenticated
    # routes (current_user raises 403). Saves are preserved.
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False,
    )
    disabled_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    saves: Mapped[list["Save"]] = relationship(
        "Save", back_populates="user", cascade="all, delete-orphan"
    )
    usage_events: Mapped[list["UsageEvent"]] = relationship(
        "UsageEvent", back_populates="user", cascade="all, delete-orphan",
        passive_deletes=True,
    )
    activity_log: Mapped[list["ActivityLog"]] = relationship(
        "ActivityLog", back_populates="user", cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r}>"


class Save(Base):
    __tablename__ = "saves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    # GameState JSON. Text column so it works on both Postgres and SQLite
    # (JSONB would be nicer on Postgres but requires driver-specific types).
    state: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now(),
    )
    # Archived saves are hidden from the default lobby list but kept
    # around (not deleted) and restorable. See /archive, /unarchive.
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False,
    )

    user: Mapped[User] = relationship("User", back_populates="saves")

    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_user_slug"),
    )

    def __repr__(self) -> str:
        return f"<Save id={self.id} user_id={self.user_id} slug={self.slug!r}>"


class UsageEvent(Base):
    """One row per LLM call — the granular usage/cost log.

    The admin panel aggregates these per user (tokens today, cost this
    month) and per system (total cost). ``source`` (on the producing
    :class:`auto_dm.llm.usage.UsageReport`) distinguishes real API usage
    from the ``chars//3`` fallback when the provider doesn't return usage
    — stored here on ``model`` as ``"<provider>:<model>:<source>"`` so the
    admin can tell real cost apart from estimates.
    """

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False,
    )
    session_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="fallback")
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 8), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True,
    )

    user: Mapped[User] = relationship("User", back_populates="usage_events")

    def __repr__(self) -> str:
        return (
            f"<UsageEvent id={self.id} user_id={self.user_id} "
            f"total={self.total_tokens} cost={self.cost_usd}>"
        )


class ActivityLog(Base):
    """Auditable user activity for the admin panel's activity log.

    Records logins, signups, quota blocks, admin actions, etc. ``meta``
    is a small JSON blob (who performed an admin action, the limit that
    was overridden, …). Stored as a JSON column on Postgres and TEXT on
    SQLite (SQLAlchemy's generic ``JSON`` type handles both).
    """

    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True,
    )

    user: Mapped[User] = relationship("User", back_populates="activity_log")

    def __repr__(self) -> str:
        return f"<ActivityLog id={self.id} user_id={self.user_id} {self.event_type!r}>"

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
    # Phase 51 — BYOK credential/settings lifecycle. Meta never contains the
    # key itself, only the provider id + masked suffix + outcome.
    CREDENTIAL_SET = "credential_set"
    CREDENTIAL_REMOVED = "credential_removed"
    CREDENTIAL_VALIDATED = "credential_validated"
    LLM_SETTINGS_CHANGED = "llm_settings_changed"


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
    # Invitation entitlement: accounts created with the configured invite
    # code may use the deploy's global LLM key.  Existing accounts backfill
    # to True during migration; public signups explicitly set this to False
    # unless their invite matches.  BYOK remains available to both groups.
    system_llm_access: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False,
    )
    # --- User preferences (Phase 42) ------------------------------------
    # Single JSON column holding the {tts, music} preferences blob. JSONB on
    # Postgres / JSON on SQLite (generic JSON type handles both). NULL for
    # users who never set any → merge_defaults back-fills at read time.
    preferences: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

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
    # Phase 51 — BYOK. Cascade-delete in the ORM (not passive_deletes) so
    # removing the account wipes the stored credentials/settings on SQLite
    # tests too; the FK ON DELETE CASCADE still backs it on Postgres. There
    # are only a few credentials per user, so loading them is cheap.
    llm_settings: Mapped[Optional["UserLLMSettings"]] = relationship(
        "UserLLMSettings", back_populates="user", cascade="all, delete-orphan",
        uselist=False,
    )
    provider_credentials: Mapped[list["UserProviderCredential"]] = relationship(
        "UserProviderCredential", back_populates="user",
        cascade="all, delete-orphan",
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
    # Phase 51d — which key paid for the call: "legacy" (invitation-authorized
    # global AUTO_DM_* key) or "byok" (user's own encrypted key). Defaults to
    # legacy so existing rows/analytics read correctly.
    credential_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="legacy", server_default="legacy",
    )
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


class UserLLMSettings(Base):
    """Per-user LLM mode/provider/model (Phase 51b).

    One row per user (or none = invitation-authorized global mode). ``mode``
    is ``byok``; selecting the global provider removes the row.

    Sensitive material (the API key itself) lives in a *separate* table
    (:class:`UserProviderCredential`) and never appears here.
    """

    __tablename__ = "user_llm_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
    )
    # "byok". Absence of a row = global mode when the user is entitled.
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="byok")
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    # Reserved for future per-user params (temperature override, etc.).
    params: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now(),
    )

    user: Mapped[User] = relationship("User", back_populates="llm_settings")

    def __repr__(self) -> str:
        return (
            f"<UserLLMSettings user_id={self.user_id} mode={self.mode!r} "
            f"provider={self.provider!r} model={self.model!r}>"
        )


class UserProviderCredential(Base):
    """An encrypted user-supplied provider API key (Phase 51b).

    Ciphertext only — the plaintext is never stored, logged, serialized, or
    returned by any endpoint. ``masked_suffix`` is the safe display token.
    Unique per (user_id, provider) so a user keeps at most one key per
    provider. See :mod:`auto_dm.web.crypto` for the encryption scheme.
    """

    __tablename__ = "user_provider_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # Fernet token is base64 (URL-safe) → TEXT is the natural column type.
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    masked_suffix: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    # "unchecked" | "valid" | "invalid" — last validation outcome.
    validation_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unchecked",
    )
    validated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now(),
    )

    user: Mapped[User] = relationship("User", back_populates="provider_credentials")

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_provider_credential"),
    )

    def __repr__(self) -> str:
        return (
            f"<UserProviderCredential id={self.id} user_id={self.user_id} "
            f"provider={self.provider!r} status={self.validation_status!r}>"
        )

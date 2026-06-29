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

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base for all ORM models."""


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(),
    )

    saves: Mapped[list["Save"]] = relationship(
        "Save", back_populates="user", cascade="all, delete-orphan"
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

    user: Mapped[User] = relationship("User", back_populates="saves")

    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_user_slug"),
    )

    def __repr__(self) -> str:
        return f"<Save id={self.id} user_id={self.user_id} slug={self.slug!r}>"

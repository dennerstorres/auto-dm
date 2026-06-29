"""SQLAlchemy async engine + session factory (Phase 26).

A single AsyncEngine is created at process startup and shared across
requests. Each request gets its own AsyncSession via the
:func:`get_session` FastAPI dependency. For tests, the engine is
overridden with an in-memory SQLite equivalent or a test database.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from auto_dm.web.config import get_settings

logger = logging.getLogger(__name__)


# Module-level singletons — populated by ``init_engine`` on app startup.
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str | None = None, *, echo: bool | None = None) -> AsyncEngine:
    """Create the engine + session factory. Idempotent.

    Call from FastAPI's ``lifespan`` context manager.
    """
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    settings = get_settings()
    url = database_url or settings.database_url
    is_sqlite = url.startswith("sqlite")

    kwargs: dict = {"echo": echo if echo is not None else settings.db_echo}
    if not is_sqlite:
        # asyncpg-specific connect_args
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 5
        kwargs["pool_pre_ping"] = True

    _engine = create_async_engine(url, **kwargs)
    _session_factory = async_sessionmaker(
        _engine, expire_on_commit=False, class_=AsyncSession
    )
    logger.info("Initialized async SQLAlchemy engine for %s", url.split("@")[-1])
    return _engine


async def dispose_engine() -> None:
    """Close the engine on app shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_engine() -> AsyncEngine:
    """Get the active engine. Raises if not initialized."""
    if _engine is None:
        raise RuntimeError("Engine not initialized; call init_engine() first.")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized; call init_engine() first.")
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield an AsyncSession per request."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()

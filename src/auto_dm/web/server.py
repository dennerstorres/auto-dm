"""FastAPI app factory + lifespan management (Phase 26).

``create_app()`` returns a configured FastAPI app. The provider
factory is injected so tests can swap in a fake LLM.

Run with:
    uvicorn auto_dm.web.server:create_app --factory --host 0.0.0.0 --port 4004

The SessionManager is stored on ``app.state`` so the route handlers
can pull it via :func:`get_app_state`.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from auto_dm.web.config import get_settings
from auto_dm.web.db import dispose_engine, init_engine
from auto_dm.web.models import Base
from auto_dm.web.redis_client import dispose_redis, init_redis
from auto_dm.web.routes_admin import router as admin_router
from auto_dm.web.routes_auth import router as auth_router
from auto_dm.web.routes_game import router as game_router
from auto_dm.web.routes_inventory import router as inventory_router
from auto_dm.web.routes_setup import router as setup_router
from auto_dm.web.sessions import SessionManager

logger = logging.getLogger(__name__)


# Static dir lives next to this file.
STATIC_DIR = Path(__file__).parent / "static"


@dataclass
class AppState:
    """Container for app-wide singletons (Phase 26)."""

    session_manager: SessionManager
    # Provider factory is also stored on the SessionManager itself
    # (see SessionManager.__init__); this is just a reference for
    # route handlers that need it directly.
    provider_factory: Callable


# Global app-state handle (set by create_app, read by route dependencies
# that can't use FastAPI Depends).
_state: Optional[AppState] = None


def get_app_state() -> AppState:
    """Get the active app state. Raises if no app is mounted."""
    if _state is None:
        raise RuntimeError("App state not initialized.")
    return _state


def _default_provider_factory() -> object:
    """Default LLM provider factory (loads from env).

    Reads ``AUTO_DM_PROVIDER``, ``AUTO_DM_API_KEY``,
    ``AUTO_DM_BASE_URL``, ``AUTO_DM_MODEL`` from the environment and
    instantiates the matching provider. Falls back to raising so we
    never accidentally spin up an LLM during app construction if the
    caller didn't set up env vars.
    """
    from auto_dm.llm.base import LLMConfig
    from auto_dm.llm.minimax import MinimaxProvider

    cfg = LLMConfig.from_env()
    if cfg.name != "minimax":
        raise RuntimeError(
            f"Provider {cfg.name!r} is not supported yet. "
            "Only 'minimax' is wired in the MVP."
        )
    return MinimaxProvider(cfg)


def _ensure_save_columns(conn) -> None:
    """Add columns introduced after launch to the ``saves`` table.

    ``Base.metadata.create_all`` only creates missing *tables*; it never
    alters an existing one. So we introspect and ALTER by hand, picking a
    dialect-appropriate default literal (Postgres wants ``false``, SQLite
    stores booleans as 0/1). Idempotent: skips columns that already exist.
    """
    from sqlalchemy import inspect

    insp = inspect(conn)
    if "saves" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("saves")}
    if "archived" in existing:
        return
    default = "false" if conn.dialect.name != "sqlite" else "0"
    conn.exec_driver_sql(
        "ALTER TABLE saves ADD COLUMN archived BOOLEAN NOT NULL "
        f"DEFAULT {default}"
    )
    logger.info("Added saves.archived column")


def _ensure_user_role(conn) -> None:
    """Add the ``role`` column to the ``users`` table if missing.

    Same idempotent-ALTER pattern as :func:`_ensure_save_columns`.
    Existing rows backfill to ``'user'``.
    """
    from sqlalchemy import inspect

    insp = inspect(conn)
    if "users" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("users")}
    if "role" in existing:
        return
    conn.exec_driver_sql(
        "ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'"
    )
    logger.info("Added users.role column")


def _ensure_user_limits(conn) -> None:
    """Add the Phase 30 usage-control columns to ``users`` if missing.

    Idempotent per-column ALTER, dialect-aware boolean defaults (Postgres
    ``true``/``false`` vs SQLite ``1``/``0``). Existing rows backfill to
    the safe defaults (``unlimited=false``, ``active=true``).
    """
    from sqlalchemy import inspect

    insp = inspect(conn)
    if "users" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("users")}

    def _bool_default(true_val: str) -> str:
        return true_val if conn.dialect.name != "sqlite" else "1"

    if "daily_token_limit" not in existing:
        conn.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN daily_token_limit BIGINT NULL"
        )
    if "daily_minutes_limit" not in existing:
        conn.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN daily_minutes_limit INTEGER NULL"
        )
    if "unlimited" not in existing:
        conn.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN unlimited BOOLEAN NOT NULL "
            f"DEFAULT {_bool_default('false')}"
        )
    if "active" not in existing:
        conn.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN active BOOLEAN NOT NULL "
            f"DEFAULT {_bool_default('true')}"
        )
    if "disabled_reason" not in existing:
        conn.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN disabled_reason VARCHAR(255) NULL"
        )
    logger.info("Ensured users usage-control columns")


def _ensure_usage_tables(conn) -> None:
    """Create the Phase 30 ``usage_events`` and ``activity_log`` tables.

    ``create_all`` handles brand-new databases; this CREATE IF NOT EXISTS
    covers databases that already existed before Phase 30. Types are
    dialect-aware (JSON → JSONB on Postgres, TEXT on SQLite).
    """
    from sqlalchemy import inspect

    insp = inspect(conn)
    tables = set(insp.get_table_names())
    is_sqlite = conn.dialect.name == "sqlite"
    json_type = "TEXT" if is_sqlite else "JSONB"

    if "usage_events" not in tables:
        conn.exec_driver_sql(
            """
            CREATE TABLE usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                session_id VARCHAR(64) NULL,
                endpoint VARCHAR(128) NOT NULL,
                kind VARCHAR(16) NOT NULL,
                provider VARCHAR(64) NOT NULL DEFAULT '',
                model VARCHAR(128) NOT NULL DEFAULT '',
                source VARCHAR(16) NOT NULL DEFAULT 'fallback',
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd NUMERIC(12, 8) NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            """
            if is_sqlite
            else """
            CREATE TABLE usage_events (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                session_id VARCHAR(64),
                endpoint VARCHAR(128) NOT NULL,
                kind VARCHAR(16) NOT NULL,
                provider VARCHAR(64) NOT NULL DEFAULT '',
                model VARCHAR(128) NOT NULL DEFAULT '',
                source VARCHAR(16) NOT NULL DEFAULT 'fallback',
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd NUMERIC(12, 8) NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_usage_events_user_id ON usage_events (user_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_usage_events_created_at ON usage_events (created_at)"
        )
        logger.info("Created usage_events table")

    if "activity_log" not in tables:
        conn.exec_driver_sql(
            f"""
            CREATE TABLE activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                event_type VARCHAR(32) NOT NULL,
                meta {json_type} NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            """
            if is_sqlite
            else f"""
            CREATE TABLE activity_log (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                event_type VARCHAR(32) NOT NULL,
                meta {json_type},
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_activity_log_user_id ON activity_log (user_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_activity_log_event_type ON activity_log (event_type)"
        )
        logger.info("Created activity_log table")


async def _seed_admin(settings) -> None:
    """Create the single admin account at startup if configured.

    Idempotent: if an admin already exists, do nothing. If
    ``ADMIN_PASSWORD`` is unset, log a warning and skip (no admin
    created). Runs after the schema is ready so the ``role`` column
    is guaranteed to exist.
    """
    if not settings.admin_password:
        logger.warning(
            "ADMIN_PASSWORD unset — no admin account seeded. "
            "Set it to enable the admin login."
        )
        return

    from sqlalchemy import select

    from auto_dm.web.auth import hash_password
    from auto_dm.web.db import get_session_factory
    from auto_dm.web.models import User, UserRole

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(User).where(User.role == UserRole.ADMIN.value)
        )
        if result.scalar_one_or_none() is not None:
            return  # Admin already exists.
        admin = User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            role=UserRole.ADMIN.value,
        )
        session.add(admin)
        try:
            await session.commit()
            logger.info("Seeded admin account %r", settings.admin_username)
        except Exception:
            await session.rollback()
            logger.exception("Failed to seed admin account")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: engine + redis + schema. Shutdown: close connections."""
    settings = get_settings()
    logger.info("Starting auto_dm web backend (env=%s)", settings.environment)

    # 1) DB engine
    init_engine()
    engine = None
    from auto_dm.web.db import get_engine

    engine = get_engine()
    # Create tables (idempotent). For production, alembic migrations.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all won't ALTER existing tables, so backfill new columns
        # by hand. Currently: `saves.archived`, `users.role`, and the
        # Phase 30 usage-control columns + tables.
        await conn.run_sync(_ensure_save_columns)
        await conn.run_sync(_ensure_user_role)
        await conn.run_sync(_ensure_user_limits)
        await conn.run_sync(_ensure_usage_tables)
    logger.info("DB schema ready")

    # Seed the single admin account (idempotent).
    await _seed_admin(settings)

    # 2) Redis
    init_redis()
    logger.info("Redis client ready")

    # 3) SessionManager (the provider_factory is set by the caller
    # via create_app(provider_factory=...); fall back to a stub).
    factory = getattr(app.state, "provider_factory", None) or _default_provider_factory
    sm = SessionManager(provider_factory=factory)
    app.state.session_manager = sm
    global _state
    _state = AppState(session_manager=sm, provider_factory=factory)

    yield

    # Shutdown
    logger.info("Shutting down auto_dm web backend")
    await dispose_redis()
    await dispose_engine()
    _state = None


def create_app(provider_factory: Optional[Callable] = None) -> FastAPI:
    """Build the FastAPI app.

    Args:
        provider_factory: A no-arg callable that returns a fresh
            LLM provider. Required for the DM agent to work. Tests
            pass a stub. In production, this is set in
            ``auto_dm.web.main`` from env config.
    """
    settings = get_settings()

    app = FastAPI(
        title="Auto DM",
        version="0.1.0",
        description="AI-powered solo D&D 5e game master (web backend).",
        lifespan=lifespan,
    )
    # Store provider_factory on app.state so lifespan can read it.
    if provider_factory is not None:
        app.state.provider_factory = provider_factory

    # CORS — Vercel frontend + local dev origins.
    origins = [o.strip() for o in settings.frontend_url.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Type"],
    )

    # Routers
    app.include_router(auth_router)
    app.include_router(game_router)
    app.include_router(inventory_router)
    app.include_router(setup_router)
    app.include_router(admin_router)

    # Health check (no auth required)
    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.1.0"}

    # Static files (console UI) — served at the root URL.
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    else:
        logger.warning("Static dir %s not found; UI not mounted.", STATIC_DIR)

    return app

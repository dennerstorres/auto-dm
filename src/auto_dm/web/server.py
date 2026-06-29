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
from auto_dm.web.routes_auth import router as auth_router
from auto_dm.web.routes_game import router as game_router
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
    logger.info("DB schema ready")

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
    app.include_router(setup_router)

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

"""Configuration for the web backend (Phase 26).

All settings come from environment variables (or `.env`). The
:class:`Settings` instance is the single source of truth — import
``settings`` from this module and never read ``os.environ`` directly
elsewhere.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, populated from env vars.

    Required for production:
      - ``DATABASE_URL`` (e.g. ``postgresql+asyncpg://user:pass@host:5432/auto_dm``)
      - ``REDIS_URL`` (e.g. ``redis://redis:6379/0``)
      - ``JWT_SECRET`` (a long random string; use ``openssl rand -hex 32``)

    Optional (with sensible defaults for dev):
      - ``FRONTEND_URL`` (comma-separated origins allowed by CORS)
      - ``SESSION_TTL_SECONDS`` (default 24h)
      - ``JWT_EXPIRES_MINUTES`` (default 7 days)
      - ``DB_ECHO`` (default False)
      - ``ENVIRONMENT`` (default ``development``)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Persistence ---
    database_url: str = Field(
        default="postgresql+asyncpg://auto_dm:auto_dm@localhost:5432/auto_dm",
        description="Async SQLAlchemy URL. Must use the asyncpg driver.",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL for active session state.",
    )
    db_echo: bool = Field(default=False, description="Log all SQL statements.")

    # --- Auth ---
    jwt_secret: str = Field(
        default="dev-secret-change-me-in-production-32-bytes-minimum",
        description="HMAC-SHA256 secret. Override in production.",
        min_length=32,
    )
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = Field(
        default=60 * 24 * 7,  # 7 days
        description="JWT lifetime in minutes.",
    )
    invite_code: Optional[str] = Field(
        default=None,
        description=(
            "If set, ``POST /api/auth/signup`` requires this code in the "
            "request body. Leave empty to allow open signup (dev only)."
        ),
    )

    # --- Sessions ---
    session_ttl_seconds: int = Field(
        default=60 * 60 * 24,  # 24h
        description="TTL for active game sessions in Redis.",
    )

    # --- CORS ---
    frontend_url: str = Field(
        default="http://localhost:3000,http://localhost:5173",
        description="Comma-separated allowed CORS origins (Vercel + dev).",
    )

    # --- Misc ---
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor.

    Use ``get_settings()`` everywhere — the cache means env changes
    are picked up only once per process.
    """
    return Settings()

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

    # --- Admin seed ---
    admin_username: str = Field(
        default="admin",
        description="Username of the single seeded admin account (created at startup).",
    )
    admin_password: Optional[str] = Field(
        default=None,
        description=(
            "Password for the seeded admin account. If unset, no admin is "
            "created at startup. Set ADMIN_PASSWORD in production."
        ),
    )

    # --- Sessions ---
    session_ttl_seconds: int = Field(
        default=60 * 60 * 24,  # 24h
        description="TTL for active game sessions in Redis.",
    )

    # --- Usage limits + pricing (Phase 30) ------------------------------
    # Global defaults used when a User has no per-user limit set (NULL).
    # Enforced hard: once a user hits the cap, LLM calls return 429 until
    # the next UTC midnight (unless the user is flagged ``unlimited``).
    default_daily_token_limit: int = Field(
        default=200_000,
        description="Default daily token cap per user (used when per-user limit is NULL).",
    )
    default_daily_minutes_limit: int = Field(
        default=120,
        description="Default daily active-minutes cap per user (NULL → this).",
    )
    # Token pricing in USD per 1k tokens — used to compute cost_usd on
    # each UsageEvent. Adjust to match your provider's actual rates.
    token_price_per_1k_input_usd: float = Field(
        default=0.001, description="USD per 1k input (prompt) tokens.",
    )
    token_price_per_1k_output_usd: float = Field(
        default=0.002, description="USD per 1k output (completion) tokens.",
    )

    # --- CORS ---
    frontend_url: str = Field(
        default="http://localhost:3000,http://localhost:5173",
        description="Comma-separated allowed CORS origins (Vercel + dev).",
    )

    # --- Misc ---
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # --- TTS (Phase 42) ------------------------------------------------
    # edge-tts synth is cached on disk by sha1(text|voice|rate). Empty
    # ``tts_cache_dir`` resolves to ``tempfile.gettempdir()/auto_dm_tts_cache``
    # so it works cross-platform (dev is Windows, deploy is Linux).
    tts_cache_dir: str = Field(
        default="",
        description="Disk cache dir for synth mp3s. Empty → system temp.",
    )
    tts_cache_ttl_seconds: int = Field(
        default=60 * 60 * 24 * 30,  # 30 days
        description="TTL for cached synth mp3s (mtime-based).",
    )
    tts_default_voice: str = Field(
        default="pt-BR-FranciscaNeural",
        description="Default edge-tts voice (pt-BR).",
    )
    tts_default_rate: str = Field(
        default="+0%",
        description="Default synth rate (e.g. '+0%', '-10%', '+15%').",
    )
    tts_max_text_chars: int = Field(
        default=2000,
        description="Hard cap on input text length for /api/tts/speak.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor.

    Use ``get_settings()`` everywhere — the cache means env changes
    are picked up only once per process.
    """
    return Settings()

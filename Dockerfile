# Auto DM backend — Phase 26d
# Single-stage build on python:3.11-slim. Run with:
#   docker build -t auto-dm .
#   docker run --rm -p 4004:4004 --env-file .env auto-dm
#
# Notes
# -----
# - Listens on port 4004 by default. Override with the ``PORT`` env var
#   (the CMD references ``$PORT``).
# - Requires the user to provide ``.env`` (or pass env vars) with
#   ``DATABASE_URL``, ``REDIS_URL``, ``JWT_SECRET``,
#   ``FRONTEND_URL``, and Minimax credentials
#   (``AUTO_DM_API_KEY``, ``AUTO_DM_PROVIDER``, ``AUTO_DM_BASE_URL``,
#   ``AUTO_DM_MODEL``). See ``.env.example`` for the full list.
# - Static assets (HTML/CSS/JS) live in ``src/auto_dm/web/static``
#   and are mounted by FastAPI at the root URL.

FROM python:3.11-slim AS base

# Disable .pyc + enable unbuffered stdout/stderr (so logs show immediately).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build tools for any wheels that need compiling (asyncpg, bcrypt, etc.).
# Slim image already has gcc for these; keep the layer small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (better Docker layer caching — reuses this layer
# when only source code changes).
COPY pyproject.toml ./
COPY src ./src
# PHB rulebook content (data/phb/*.md) MUST ship in the image — the
# engine loads it at startup to populate races, classes, spells, etc.
COPY data ./data
RUN pip install --upgrade pip && pip install -e .

# Run as non-root.
RUN useradd --create-home --uid 1000 auto_dm \
    && chown -R auto_dm:auto_dm /app
USER auto_dm

# Expose the API port. Configurable via $PORT.
EXPOSE 4004

# Health check hits the unauthenticated /api/health endpoint.
# start-period=120s: PHB loader + DB/Redis init + schema migrations routinely take
# 90s+ on homolog (first deploy pulls & indexes ~290 spells, 80 monsters, etc.).
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=120s \
    CMD python -c "import httpx, os; r = httpx.get(f'http://localhost:{os.environ.get(\"PORT\", 4004)}/api/health', timeout=3); r.raise_for_status()" || exit 1

# uvicorn: --factory tells it to call ``create_app()`` to get the
# FastAPI instance. The full module path is
# ``auto_dm.web.server:create_app``. The provider factory is supplied
# by ``auto_dm.web.main`` (loaded via the same module's lifespan) — see
# ``AUTO_DM_PROVIDER`` env var.
CMD ["uvicorn", "auto_dm.web.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "4004"]
"""Text-to-Speech synthesis via edge-tts (Phase 42a).

Isolated on purpose: ``edge-tts`` is GPL-3.0 and reaches out to Microsoft's
public edge-tts endpoint at request time. By keeping the dependency lazy
(imported inside :func:`_get_edge_tts`) the rest of the app starts even if the
package is missing, and tests can inject a fake without touching the network.

The public surface is small:

- :func:`is_available` — cheap probe (try-import).
- :func:`list_voices` — cached, pt-* only (keeps the payload small).
- :func:`synthesize` — returns ``(mp3_bytes, from_cache)``; raises
  :class:`TTSError` on any network/availability failure so the route can map it
  to 503. Disk cache is keyed by ``sha1(text|voice|rate)`` with an mtime TTL.
"""
from __future__ import annotations

import hashlib
import logging
import tempfile
import time
from pathlib import Path
from typing import Any

from auto_dm.web.config import get_settings

logger = logging.getLogger(__name__)


class TTSError(Exception):
    """Raised when synthesis is unavailable or the network call fails.

    Routes map this to 503 (service unavailable). Carrying the original cause
    keeps logs useful without leaking internals to the client.
    """


# Module-level voice cache: edge-tts' voice list is static-ish and the listing
# endpoint hits the network, so we fetch once per process.
_voices_cache: list[dict[str, Any]] | None = None


def _get_edge_tts() -> Any:
    """Lazy import of ``edge_tts``.

    This is the single point tests monkeypatch to inject a fake module
    (``monkeypatch.setattr(tts, "_get_edge_tts", lambda: fake)``). Raises
    :class:`TTSError` if the package isn't installed.
    """
    try:
        import edge_tts  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - dep is in pyproject now
        raise TTSError("edge-tts não está instalado") from exc
    return edge_tts


def is_available() -> bool:
    """True if ``edge_tts`` can be imported (does not probe the network)."""
    try:
        _get_edge_tts()
        return True
    except TTSError:
        return False


async def list_voices() -> list[dict[str, Any]]:
    """Return edge-tts voices filtered to pt-* locales.

    Cached per process. Raises :class:`TTSError` if the network call fails so
    the route can surface a 503.
    """
    global _voices_cache
    if _voices_cache is not None:
        return _voices_cache
    try:
        edge_tts = _get_edge_tts()
        raw = await edge_tts.list_voices()
    except TTSError:
        raise
    except Exception as exc:  # network / API errors
        raise TTSError("Falha ao listar vozes") from exc
    # Each entry looks like {'Name': '...', 'Locale': 'pt-BR', 'Gender': '...'}
    voices = [v for v in raw if str(v.get("Locale", "")).lower().startswith("pt")]
    _voices_cache = voices
    return voices


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------


def cache_dir() -> Path:
    """Resolve the cache dir from settings, falling back to system temp.

    Empty ``tts_cache_dir`` → ``tempfile.gettempdir()/auto_dm_tts_cache`` so the
    same code works on Windows (dev) and Linux (deploy).
    """
    configured = get_settings().tts_cache_dir.strip()
    path = Path(configured) if configured else Path(tempfile.gettempdir()) / "auto_dm_tts_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(text: str, voice: str, rate: str) -> str:
    raw = f"{text}|{voice}|{rate}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path:
    return cache_dir() / f"{key}.mp3"


def _cache_valid(path: Path, ttl_seconds: int) -> bool:
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < ttl_seconds


async def synthesize(text: str, voice: str, rate: str) -> tuple[bytes, bool]:
    """Synthesize ``text`` → mp3 bytes.

    Returns ``(mp3_bytes, from_cache)``. On a cache hit the network is never
    touched. On a miss we stream from edge-tts, persist to disk, and return the
    fresh bytes. Any failure (missing dep, network error) raises
    :class:`TTSError` — callers decide the HTTP mapping.
    """
    settings = get_settings()
    key = _cache_key(text, voice, rate)
    path = _cache_path(key)
    if _cache_valid(path, settings.tts_cache_ttl_seconds):
        try:
            return path.read_bytes(), True
        except OSError as exc:  # cached file vanished mid-read — fall through
            logger.debug("TTS cache read failed for %s: %s", key, exc)

    try:
        edge_tts = _get_edge_tts()
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if isinstance(chunk, bytes):
                chunks.append(chunk)
            elif isinstance(chunk, dict) and isinstance(chunk.get("audio_data"), bytes):
                # edge-tts >=6.1 yields dicts {"audio_data": ...}.
                chunks.append(chunk["audio_data"])
        data = b"".join(chunks)
    except TTSError:
        raise
    except Exception as exc:  # network / API / auth errors
        raise TTSError("Falha ao sintetizar áudio") from exc

    if not data:
        raise TTSError("Síntese retornou áudio vazio")

    # Best-effort cache write; a write failure must not break synthesis.
    try:
        path.write_bytes(data)
    except OSError as exc:  # pragma: no cover - disk-full edge case
        logger.warning("TTS cache write failed for %s: %s", key, exc)

    return data, False


def invalidate_cache() -> None:
    """Clear the in-memory voice cache (used by tests)."""
    global _voices_cache
    _voices_cache = None

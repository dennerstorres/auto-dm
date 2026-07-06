"""TTS routes: voice listing + synthesis (Phase 42a).

- ``GET /api/tts/voices`` — pt-* edge-tts voices (cached per process).
- ``GET /api/tts/speak?text=&voice=&rate=`` — synth mp3, disk-cached by
  ``sha1(text|voice|rate)``.

Both require auth. Synthesis is not bound to a session: the client already has
the narration text, so we synthesize arbitrary text and let the disk cache make
repeat plays cheap. Network/availability failures map to 503 (the front-end
treats that as "voz indisponível" and falls back to silent replay).
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from auto_dm.web.auth import current_user
from auto_dm.web.config import get_settings
from auto_dm.web.models import User
from auto_dm.web.tts import TTSError, is_available, list_voices, synthesize

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tts", tags=["tts"])


@router.get("/voices")
async def voices(
    _user: Annotated[User, Depends(current_user)],
) -> dict[str, list[dict]]:
    """List available pt-* voices.

    Returns 503 when edge-tts is unavailable or the listing endpoint is
    unreachable.
    """
    if not is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serviço de voz indisponível",
        )
    try:
        return {"voices": await list_voices()}
    except TTSError as exc:
        logger.info("TTS voices unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serviço de voz indisponível",
        ) from exc


@router.get("/speak")
async def speak(
    _user: Annotated[User, Depends(current_user)],
    text: Annotated[str, Query(min_length=0)] = "",
    voice: Annotated[str, Query(max_length=80)] = "",
    rate: Annotated[str, Query(max_length=16)] = "",
) -> Response:
    """Synthesize ``text`` to an mp3 Response.

    Validates text length against ``Settings.tts_max_text_chars`` (422 on
    over-length / empty). Falls back to the configured default voice/rate when
    the query params are omitted. 503 on any synth failure.
    """
    settings = get_settings()
    body = text.strip()
    if not body:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Texto vazio",
        )
    if len(body) > settings.tts_max_text_chars:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Texto muito longo (máx {settings.tts_max_text_chars} caracteres)",
        )
    chosen_voice = voice.strip() or settings.tts_default_voice
    chosen_rate = rate.strip() or settings.tts_default_rate

    if not is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serviço de voz indisponível",
        )
    try:
        data, _from_cache = await synthesize(body, chosen_voice, chosen_rate)
    except TTSError as exc:
        logger.info("TTS synth failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serviço de voz indisponível",
        ) from exc
    # The cache key is content-derived, so the bytes are immutable for the
    # inputs; tell the browser to cache for the full TTL.
    return Response(
        content=data,
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "public, max-age=2592000, immutable",
        },
    )

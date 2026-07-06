"""User preferences schema + merge helper (Phase 42c).

Preferences live in a single JSON column ``users.preferences`` (JSONB on
Postgres, JSON on SQLite). This module owns the authoritative default shape and
the deep-merge that back-fills new keys onto rows stored before the key existed
— so adding a preference later never requires a data migration.

Shape::

    {
      "tts":  {"enabled": bool, "voice": str, "rate": str, "auto_play": bool},
      "music": {"enabled": bool, "src": str, "volume": float},
    }
"""
from __future__ import annotations

import re
from typing import Any

# The canonical default preferences. Always deep-merge onto stored values via
# :func:`merge_defaults` before returning them to the client, so partial/legacy
# stored blobs gain the new keys.
DEFAULT_PREFERENCES: dict[str, dict[str, Any]] = {
    "tts": {
        "enabled": False,
        "voice": "",  # empty → use Settings.tts_default_voice at synth time
        "rate": "+0%",
        "auto_play": False,
    },
    "music": {
        "enabled": False,
        "src": "",  # URL to a CC-BY ambient track (not embedded)
        "volume": 0.4,
    },
}

# rate must look like edge-tts' format: an optional sign, 1-3 digits, then '%'.
_RATE_RE = re.compile(r"^[+-]\d{1,3}%$")


def merge_defaults(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Deep-merge stored preferences onto :data:`DEFAULT_PREFERENCES`.

    Returns a fresh dict; never mutates the input. Unknown top-level keys in
    ``stored`` are preserved (forward-compat) but only known sections are
    back-filled with defaults. ``stored`` of ``None`` (new user, NULL column)
    yields a copy of the full defaults.
    """
    stored = stored or {}
    merged: dict[str, Any] = {}
    for section, defaults in DEFAULT_PREFERENCES.items():
        sub = stored.get(section) or {}
        merged[section] = {**defaults, **(sub if isinstance(sub, dict) else {})}
    # Preserve any unrecognized sections the client may have written earlier.
    for key, val in stored.items():
        if key not in merged:
            merged[key] = val
    return merged


def validate_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Validate + normalize a PATCH body against the known sections.

    Accepts a partial ``{"tts": {...}, "music": {...}}``. Returns a normalized
    patch (coerced values, clamped volume) ready to deep-merge into stored
    preferences. Raises ``ValueError`` with a pt-BR message on invalid input so
    the route can surface it as 422.
    """
    out: dict[str, Any] = {}
    if "tts" in patch:
        tts = patch["tts"] or {}
        if not isinstance(tts, dict):
            raise ValueError("Campo 'tts' deve ser um objeto")
        norm: dict[str, Any] = {}
        if "enabled" in tts:
            norm["enabled"] = bool(tts["enabled"])
        if "auto_play" in tts:
            norm["auto_play"] = bool(tts["auto_play"])
        if "voice" in tts:
            voice = str(tts["voice"]).strip()
            if len(voice) > 80:
                raise ValueError("Voz muito longa (máx 80 caracteres)")
            norm["voice"] = voice
        if "rate" in tts:
            rate = str(tts["rate"]).strip()
            if not _RATE_RE.match(rate):
                raise ValueError("Rate inválido (use '+0%', '-10%', '+15%'…)")
            norm["rate"] = rate
        out["tts"] = norm
    if "music" in patch:
        music = patch["music"] or {}
        if not isinstance(music, dict):
            raise ValueError("Campo 'music' deve ser um objeto")
        norm_m: dict[str, Any] = {}
        if "enabled" in music:
            norm_m["enabled"] = bool(music["enabled"])
        if "src" in music:
            src = str(music["src"]).strip()
            if len(src) > 500:
                raise ValueError("URL de música muito longa (máx 500)")
            norm_m["src"] = src
        if "volume" in music:
            try:
                vol = float(music["volume"])
            except (TypeError, ValueError) as exc:
                raise ValueError("Volume deve ser um número") from exc
            # Clamp to [0, 1] rather than rejecting — friendlier.
            norm_m["volume"] = max(0.0, min(1.0, vol))
        out["music"] = norm_m
    return out

"""Save/load GameState to and from JSON files.

File layout:
    saves/
        <campaign-slug>/
            state.json     # the full GameState serialized

The save file embeds a small ``_meta`` block at the top of the JSON
for fast listing without loading the full state:

    {
        "_meta": {
            "campaign_name": "...",
            "saved_at": "ISO 8601",
            "schema_version": 1
        },
        "state": { ... full GameState ... }
    }

All writes are atomic: we write to a temp file in the same directory
and then ``os.replace()`` to the final name. This prevents partial
files when the process dies mid-write.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from auto_dm.state.models import GameState


# ============================================================================
# Errors
# ============================================================================


class SaveError(Exception):
    """Base class for save/load errors."""


class SaveNotFoundError(SaveError, FileNotFoundError):
    """Raised when trying to load a save that doesn't exist."""


class SchemaMismatchError(SaveError, ValueError):
    """Raised when a save's schema_version doesn't match the current code."""


# ============================================================================
# Metadata
# ============================================================================


@dataclass(frozen=True)
class SaveMetadata:
    """Lightweight summary of a save, for listing without loading state."""

    slug: str
    campaign_name: str
    saved_at: datetime
    schema_version: int
    file_path: Path

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "campaign_name": self.campaign_name,
            "saved_at": self.saved_at.isoformat(),
            "schema_version": self.schema_version,
            "file_path": str(self.file_path),
        }


# ============================================================================
# Paths
# ============================================================================


def default_saves_dir() -> Path:
    """Return the default save directory: <repo>/saves/."""
    # src/auto_dm/persistence/saves.py → <repo>/saves/
    return Path(__file__).resolve().parents[3] / "saves"


def slugify(name: str) -> str:
    """Turn a campaign name into a filesystem-safe slug.

    Lowercases, replaces non-alphanumeric chars with '-', collapses
    multiple dashes, trims leading/trailing dashes. Falls back to
    "campaign" if the result is empty.
    """
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "campaign"


# ============================================================================
# Write
# ============================================================================


def save_state(
    state: GameState,
    *,
    slug: Optional[str] = None,
    saves_dir: Optional[Path] = None,
) -> Path:
    """Serialize ``state`` to disk under ``saves/<slug>/state.json``.

    Returns the path of the saved file. The directory is created if
    it doesn't exist. The write is atomic (temp file + replace).
    """
    saves_dir = saves_dir or default_saves_dir()
    slug = slug or slugify(state.campaign_name)
    target_dir = saves_dir / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "state.json"

    payload = {
        "_meta": {
            "campaign_name": state.campaign_name,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": state.schema_version,
        },
        "state": json.loads(state.model_dump_json()),
    }

    # Atomic write: write to temp in same dir, then replace.
    fd, tmp_path = tempfile.mkstemp(
        prefix=".state.", suffix=".json.tmp", dir=str(target_dir)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, target_file)
    except Exception:
        # Best-effort cleanup of the temp file on failure.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return target_file


# ============================================================================
# Read
# ============================================================================


def load_state(
    slug: str,
    *,
    saves_dir: Optional[Path] = None,
    expected_schema_version: int = 1,
) -> GameState:
    """Load a saved ``GameState`` by slug.

    Raises:
        SaveNotFoundError: if the save file doesn't exist.
        SchemaMismatchError: if the save's schema_version doesn't match.
    """
    saves_dir = saves_dir or default_saves_dir()
    target_file = saves_dir / slug / "state.json"
    if not target_file.exists():
        raise SaveNotFoundError(f"No save found for slug {slug!r} at {target_file}")

    with target_file.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    meta = payload.get("_meta", {})
    schema_version = meta.get("schema_version", 0)
    if schema_version != expected_schema_version:
        raise SchemaMismatchError(
            f"Save {slug!r} has schema_version={schema_version}, "
            f"expected {expected_schema_version}"
        )

    return GameState.model_validate(payload["state"])


def load_metadata(slug: str, *, saves_dir: Optional[Path] = None) -> SaveMetadata:
    """Read only the metadata block, without parsing the full state."""
    saves_dir = saves_dir or default_saves_dir()
    target_file = saves_dir / slug / "state.json"
    if not target_file.exists():
        raise SaveNotFoundError(f"No save found for slug {slug!r} at {target_file}")
    with target_file.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    meta = payload.get("_meta", {})
    return SaveMetadata(
        slug=slug,
        campaign_name=meta.get("campaign_name", ""),
        saved_at=datetime.fromisoformat(meta["saved_at"])
        if "saved_at" in meta
        else datetime.fromtimestamp(target_file.stat().st_mtime, tz=timezone.utc),
        schema_version=meta.get("schema_version", 0),
        file_path=target_file,
    )


# ============================================================================
# List / delete
# ============================================================================


def list_saves(saves_dir: Optional[Path] = None) -> list[SaveMetadata]:
    """Return metadata for every save in the directory, newest first.

    Skips entries that don't have a state.json (partial / corrupt dirs
    are tolerated).
    """
    saves_dir = saves_dir or default_saves_dir()
    if not saves_dir.exists():
        return []
    out: list[SaveMetadata] = []
    for entry in sorted(saves_dir.iterdir()):
        if not entry.is_dir():
            continue
        state_file = entry / "state.json"
        if not state_file.exists():
            continue
        try:
            out.append(load_metadata(entry.name, saves_dir=saves_dir))
        except (SaveError, json.JSONDecodeError, KeyError, ValueError):
            # Skip corrupt saves; the list view shouldn't crash.
            continue
    out.sort(key=lambda m: m.saved_at, reverse=True)
    return out


def delete_save(slug: str, *, saves_dir: Optional[Path] = None) -> bool:
    """Remove a save directory. Returns True if a save was removed.

    Missing saves are not an error (returns False).
    """
    saves_dir = saves_dir or default_saves_dir()
    target = saves_dir / slug
    if not target.exists():
        return False
    # Remove the whole directory (state.json is the only file we put there,
    # but be safe and rm the whole tree).
    import shutil

    shutil.rmtree(target)
    return True


def save_exists(slug: str, *, saves_dir: Optional[Path] = None) -> bool:
    saves_dir = saves_dir or default_saves_dir()
    return (saves_dir / slug / "state.json").exists()

"""Small, migration-free summaries extracted from persisted game-state JSON."""
from __future__ import annotations

import json
from typing import Any, TypedDict


class SaveMetadata(TypedDict):
    campaign_name: str
    character_name: str
    character_level: int | None
    current_location: str


EMPTY_SAVE_METADATA: SaveMetadata = {
    "campaign_name": "",
    "character_name": "",
    "character_level": None,
    "current_location": "",
}


def extract_save_metadata(raw_state: str) -> SaveMetadata:
    """Return lobby-safe fields without fully validating or migrating a save.

    Save rows were already validated on write, but older/manual databases can
    contain partial JSON. Lobby rendering must never fail because one row is
    malformed, so every field degrades independently to an empty value.
    """
    try:
        state = json.loads(raw_state)
    except (TypeError, ValueError):
        return EMPTY_SAVE_METADATA.copy()
    if not isinstance(state, dict):
        return EMPTY_SAVE_METADATA.copy()

    party = state.get("party")
    members = party if isinstance(party, list) else []
    player_id = state.get("player_character_id")
    player: dict[str, Any] | None = None
    for member in members:
        if isinstance(member, dict) and member.get("id") == player_id:
            player = member
            break
    if player is None:
        player = next(
            (
                member
                for member in members
                if isinstance(member, dict) and member.get("is_player") is True
            ),
            None,
        )

    level = player.get("level") if player else None
    if not isinstance(level, int) or isinstance(level, bool):
        level = None

    def _text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    return {
        "campaign_name": _text(state.get("campaign_name")),
        "character_name": _text(player.get("name")) if player else "",
        "character_level": level,
        "current_location": _text(state.get("current_location")),
    }

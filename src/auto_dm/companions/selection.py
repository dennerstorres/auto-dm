"""Synergy-based party candidate selection (Phase 27).

At each campaign start we roll ``k`` companions from the 12-entry pool,
biased toward roles the player's class does NOT already fill. The same
helper is consumed by both the CLI ``setup_new_game`` flow and the web
wizard ``/api/companions/roll`` endpoint.
"""
from __future__ import annotations

import random
from typing import Optional

from auto_dm.companions.roster import (
    COMPANION_FACTORIES,
    list_companion_keys,
)
from auto_dm.state.models import Character


# ---------------------------------------------------------------------------
# Role taxonomy
# ---------------------------------------------------------------------------

# Role tags per companion. Each companion has 2-3 tags describing its
# party role. Synergy picks companions whose tags complement the player's.
ROLE_TAGS: dict[str, frozenset[str]] = {
    "thorgrim": frozenset({"tank", "frontline", "melee_dps"}),
    "lyra":     frozenset({"scout", "ranged_dps", "survival"}),
    "mira":     frozenset({"healer", "support", "caster"}),
    "vex":      frozenset({"striker", "skill_monkey", "melee_dps"}),
    "garrick":  frozenset({"tank", "frontline", "support"}),
    "brom":     frozenset({"tank", "melee_dps", "striker"}),
    "kael":     frozenset({"controller", "caster", "ranged_dps"}),
    "sage":     frozenset({"controller", "caster", "ranged_dps"}),
    "maren":    frozenset({"melee_dps", "striker", "skirmisher"}),
    "eldra":    frozenset({"healer", "support", "caster"}),
    "tobias":   frozenset({"support", "caster", "skill_monkey"}),
    "dax":      frozenset({"striker", "ranged_dps", "caster"}),
}

# Role tags implied by the player's chosen class. Used to determine
# which candidate tags are "missing" (and therefore deserve a weight
# boost). Conservative — only the dominant roles per class.
_CLASS_ROLES: dict[str, frozenset[str]] = {
    "barbarian": frozenset({"tank", "melee_dps", "striker"}),
    "bard":      frozenset({"support", "caster", "skill_monkey"}),
    "cleric":    frozenset({"healer", "support", "caster"}),
    "druid":     frozenset({"healer", "support", "caster"}),
    "fighter":   frozenset({"tank", "frontline", "melee_dps"}),
    "monk":      frozenset({"melee_dps", "striker", "skirmisher"}),
    "paladin":   frozenset({"tank", "frontline", "support"}),
    "ranger":    frozenset({"scout", "ranged_dps", "survival"}),
    "rogue":     frozenset({"striker", "skill_monkey", "melee_dps"}),
    "sorcerer":  frozenset({"controller", "caster", "ranged_dps"}),
    "warlock":   frozenset({"striker", "ranged_dps", "caster"}),
    "wizard":    frozenset({"controller", "caster", "ranged_dps"}),
}

# Per-tag weight multiplier when the tag is missing from the player.
# Higher = more eager to include this role. Tuned to prefer healer
# above all else (party without healing is fragile), then tank, then
# utility roles.
SYNERGY_BIAS: dict[str, float] = {
    "healer":      2.0,
    "tank":        1.5,
    "scout":       1.2,
    "controller":  1.2,
    "striker":     1.0,
    "support":     1.0,
    "ranged_dps":  0.8,
    "melee_dps":   0.8,
    "frontline":   1.0,
    "skill_monkey": 1.0,
    "skirmisher":  1.0,
    "survival":    1.0,
    "caster":      1.0,
}

SAME_CLASS_WEIGHT: float = 0.3
_DEFAULT_WEIGHT: float = 1.0
_MAX_HEALER_RETRIES: int = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Cache the (lowercased) class name of each companion so we don't have to
# call every factory on every roll.
_CANDIDATE_CLASS: dict[str, str] = {
    key: str(getattr(COMPANION_FACTORIES[key](), "class_", "") or "").strip().lower()
    for key in list_companion_keys()
}


def _player_class_lower(player: Character) -> str:
    """Extract a lowercased class name from any Character-like object.

    Works for both built ``Character`` instances (via the ``class_``
    field with ``alias="class"``) and lightweight stubs constructed
    by callers that don't run through CharacterBuilder.
    """
    return str(getattr(player, "class_", "") or "").strip().lower()


def _candidate_weight(player: Character, candidate_key: str) -> float:
    """Compute the weight for a candidate given the player's class."""
    pclass = _player_class_lower(player)
    cand_class = _CANDIDATE_CLASS.get(candidate_key, "")
    cand_roles = ROLE_TAGS[candidate_key]
    player_roles = _CLASS_ROLES.get(pclass, frozenset())

    same_class_factor = SAME_CLASS_WEIGHT if cand_class == pclass else 1.0

    synergy = 1.0
    for tag in cand_roles:
        if tag not in player_roles:
            synergy *= SYNERGY_BIAS.get(tag, 1.0)

    return _DEFAULT_WEIGHT * same_class_factor * synergy


def _tags_for(keys: list[str]) -> frozenset[str]:
    out: set[str] = set()
    for key in keys:
        out.update(ROLE_TAGS.get(key, frozenset()))
    return frozenset(out)


def _pick_weighted(
    keys: list[str],
    weights: list[float],
    rng: random.Random,
) -> str:
    """One weighted random pick. ``weights`` aligned with ``keys``."""
    total = sum(weights)
    if total <= 0:
        return keys[rng.randrange(len(keys))]
    pick = rng.uniform(0.0, total)
    cum = 0.0
    for key, w in zip(keys, weights):
        cum += w
        if pick <= cum:
            return key
    # Floating point drift — fall back to last.
    return keys[-1]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def roll_party_candidates(
    player: Character,
    k: int = 4,
    *,
    rng: Optional[random.Random] = None,
) -> list[str]:
    """Roll ``k`` companion keys biased by synergy with ``player``.

    Sampling is weighted random WITHOUT replacement. Weights are
    computed per candidate:

    - If the candidate shares the player's class, its weight is
      multiplied by ``SAME_CLASS_WEIGHT`` (0.3) — overlap is allowed
      but discouraged.
    - For each role tag the candidate has that the player does NOT
      fill, the weight is multiplied by ``SYNERGY_BIAS[tag]`` (so a
      healer companion is twice as likely to be picked when the player
      has no healer role).

    If the player has no ``healer`` role, we retry up to
    ``_MAX_HEALER_RETRIES`` times when no candidate in the pick has
    that tag — most party compositions need at least one healer.
    After the retry budget is exhausted we return the last roll
    (best-effort).

    Parameters
    ----------
    player:
        Any object exposing ``class_`` (e.g. a built ``Character`` or
        a lightweight stub). Used only for the class name lookup.
    k:
        Number of candidates to return. Defaults to 4. Clamped to
        ``[0, len(pool)]``.
    rng:
        Optional ``random.Random`` for deterministic testing. If not
        given, a fresh instance is created.

    Returns
    -------
    list[str]
        Up to ``k`` unique companion keys. Empty list if ``k == 0``.
    """
    pool = list(list_companion_keys())
    if k <= 0 or not pool:
        return []
    if k >= len(pool):
        return pool

    r = rng if rng is not None else random.Random()

    player_roles = _CLASS_ROLES.get(_player_class_lower(player), frozenset())
    needs_healer = "healer" not in player_roles

    last_roll: list[str] = []
    for _attempt in range(_MAX_HEALER_RETRIES):
        available = list(pool)
        avail_weights = [_candidate_weight(player, key) for key in available]
        chosen: list[str] = []
        for _ in range(k):
            pick = _pick_weighted(available, avail_weights, r)
            idx = available.index(pick)
            chosen.append(pick)
            available.pop(idx)
            avail_weights.pop(idx)
        last_roll = chosen
        if not needs_healer:
            return chosen
        if "healer" in _tags_for(chosen):
            return chosen

    # Best-effort fallback after exhausting retries.
    return last_roll
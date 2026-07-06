"""Reaction model for the engine (Phase 41).

The combat engine already handles ``OPPORTUNITY_ATTACK`` as a single
hard-coded reaction (Phase 16). Phase 41 generalises this so the engine
can publish *trigger events* — moments during a turn where a character
may legally spend their reaction (Shield, Counterspell, Hellish Rebuke,
Healing Word, Uncanny Dodge, Parry, …).

Design rules (project principles):

* **Mechanic is authoritative.** The engine decides *which* reactions are
  eligible for a given trigger and *how* they resolve. The LLM only
  narrates and (for the player) picks which eligible reaction to use.
* **No LLM here.** This module is pure data: an enum of reaction kinds
  and a set of trigger dataclasses. Dispatch lives in ``combat_engine``
  (Phase 41b); eligibility helpers live alongside it.
* **TTL on ``pending_reaction``.** A trigger that nobody answers must not
  hang the turn forever. The web layer (Phase 41c) shows a 30 s timer and
  auto-passes on timeout. ``REACTION_TTL_SECONDS`` is the single source of
  truth for that window so engine, web and tests agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


# How long (in seconds) a published trigger stays "open" before the
# engine treats it as silently declined. The web modal counts down this
# same value; ``pending_reaction_is_expired`` consults it.
REACTION_TTL_SECONDS: int = 30


class ReactionKind(str, Enum):
    """The reaction a character may take when a trigger fires.

    ``OPPORTUNITY_ATTACK`` is listed for completeness — it predates this
    enum (Phase 16) and resolves through its own ``ActionType`` handler.
    The remaining kinds are introduced in Phase 41.
    """

    OPPORTUNITY_ATTACK = "opportunity_attack"
    # Spell reactions (cast as a reaction, consuming a slot).
    SHIELD = "shield"  # +5 AC + immune to Magic Missile, until next turn
    COUNTERSPELL = "counterspell"  # cancel an enemy spell in range
    HELLISH_REBUKE = "hellish_rebuke"  # Warlock 2d10 fire (or 4d10 on crit)
    HEALING_WORD = "healing_word"  # Cleric/Druid/Bard 1d4 bonus-action-as-reaction
    # Non-spell defensive reactions (no slot, class feature gated).
    UNCANNY_DODGE = "uncanny_dodge"  # Rogue 5 — halve incoming attack damage
    PARRY = "parry"  # Mastermind/Battle Master maneuver — reduce damage


# ---------------------------------------------------------------------------
# Trigger events
# ---------------------------------------------------------------------------
#
# Triggers are *what just happened* during another character's turn. Each
# dataclass carries exactly the fields the dispatch logic needs to decide
# eligibility and resolve the reaction. They are plain dataclasses (not
# Pydantic models) because they are internal to the engine turn loop and
# never serialised directly into a save — the persistable shape is the
# ``pending_reaction`` dict on ``Character``.


@dataclass(frozen=True)
class TriggerEvent:
    """Base marker for every reaction trigger.

    ``kind`` is a stable string tag the web layer uses to render the
    right modal copy. Subclasses set their own ``kind``.
    """

    kind: str = "base"
    # Epoch seconds at which the trigger fired. Defaults to None for
    # engine-internal use (tests / deterministic dispatch); the turn loop
    # stamps a real epoch before publishing so the web TTL works.
    fired_at: Optional[int] = None

    def to_payload(self) -> dict[str, Any]:
        """Serialise into the persistable ``pending_reaction`` shape."""
        raise NotImplementedError


@dataclass(frozen=True)
class OnHitByAttack(TriggerEvent):
    """An attack roll just hit ``target``.

    Fires *before* damage is applied so ``Uncanny Dodge`` / ``Parry`` /
    ``Shield`` (if announced after the hit is known) can alter or negate
    the outcome.
    """

    target_id: str = ""
    attacker_id: str = ""
    attack_damage: int = 0  # raw damage before reactions
    damage_type: str = ""
    is_melee: bool = True
    is_crit: bool = False
    kind: str = "on_hit_by_attack"

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target_id": self.target_id,
            "attacker_id": self.attacker_id,
            "attack_damage": self.attack_damage,
            "damage_type": self.damage_type,
            "is_melee": self.is_melee,
            "is_crit": self.is_crit,
        }


@dataclass(frozen=True)
class OnSeeingSpellCast(TriggerEvent):
    """A visible/effective spell is being cast by ``caster``.

    ``Counterspell`` and (for self-cast buffs) the decision to interrupt
    hang off this. ``level`` is the slot level actually expended, since
    Counterspell's resolution depends on it (ability check at DC 10 + the
    spell's level when cast above 3rd).
    """

    caster_id: str = ""
    spell_name: str = ""
    level: int = 1  # slot level expended (0 = cantrip — rarely reactable)
    kind: str = "on_seeing_spell_cast"

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "caster_id": self.caster_id,
            "spell_name": self.spell_name,
            "level": self.level,
        }


@dataclass(frozen=True)
class OnAllyDown(TriggerEvent):
    """An ally just dropped to 0 HP.

    The classic trigger for ``Healing Word`` (bonus-action-as-reaction
    in some readings; PHB lists it as a bonus action, but the project
    surfaces it here as a reaction option for the party to revive a
    downed ally out of turn — see Phase 41b dispatch notes).
    """

    ally_id: str = ""
    kind: str = "on_ally_down"

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ally_id": self.ally_id,
        }


@dataclass(frozen=True)
class OnDamageTaken(TriggerEvent):
    """``target`` took damage (any source, not necessarily an attack).

    Distinct from ``OnHitByAttack`` because some reactions key off raw
    damage rather than the attack roll (e.g. ``Hellish Rebuke`` fires on
    "taking damage", including from a save-based spell).
    """

    target_id: str = ""
    amount: int = 0
    damage_type: str = ""
    source_id: str = ""  # who dealt it, if known
    kind: str = "on_damage_taken"

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target_id": self.target_id,
            "amount": self.amount,
            "damage_type": self.damage_type,
            "source_id": self.source_id,
        }


# Registry lets the web/dispatch layer resolve a payload back to the
# dataclass for inspection (e.g. when replaying a saved pending_reaction).
_TRIGGER_BY_KIND: dict[str, type[TriggerEvent]] = {
    "on_hit_by_attack": OnHitByAttack,
    "on_seeing_spell_cast": OnSeeingSpellCast,
    "on_ally_down": OnAllyDown,
    "on_damage_taken": OnDamageTaken,
}


def trigger_from_payload(payload: dict[str, Any]) -> TriggerEvent:
    """Reconstruct a trigger from its persisted payload.

    Unknown kinds degrade to a bare ``TriggerEvent`` so old/foreign saves
    never crash the engine. Fields absent from the payload fall back to
    the dataclass defaults.
    """

    kind = str(payload.get("kind", "base"))
    cls = _TRIGGER_BY_KIND.get(kind, TriggerEvent)
    known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    kwargs = {k: v for k, v in payload.items() if k in known}
    try:
        return cls(**kwargs)  # type: ignore[call-arg]
    except TypeError:
        return TriggerEvent(kind=kind)


# ---------------------------------------------------------------------------
# pending_reaction helpers
# ---------------------------------------------------------------------------


def build_pending_reaction(
    trigger: TriggerEvent,
    reactions_eligible: list[ReactionKind],
    *,
    fired_at: Optional[int] = None,
    ttl_seconds: int = REACTION_TTL_SECONDS,
) -> Optional[dict[str, Any]]:
    """Build the ``Character.pending_reaction`` dict for one trigger.

    Returns ``None`` when nothing is eligible (the engine should not
    publish a trigger nobody can answer). ``fired_at`` defaults to the
    trigger's own epoch; if both are absent the TTL is unrecoverable and
    we also return ``None`` (the web layer always stamps a real epoch
    before publishing — see Phase 41c).

    The shape is intentionally JSON-friendly because it round-trips
    through ``model_dump_json`` on save.
    """

    if not reactions_eligible:
        return None
    epoch = fired_at if fired_at is not None else trigger.fired_at
    if epoch is None:
        return None
    return {
        "fired_at": int(epoch),
        "expires_at": int(epoch) + int(ttl_seconds),
        "ttl_seconds": int(ttl_seconds),
        "trigger": trigger.to_payload(),
        "reactions_eligible": [r.value for r in reactions_eligible],
        # Filled in once the player/companion answers; None until then.
        "resolved": False,
        "chosen": None,
    }


def pending_reaction_is_expired(
    pending: Optional[dict[str, Any]],
    *,
    now_epoch: int,
) -> bool:
    """True when a pending reaction should be treated as declined.

    ``None`` / malformed dicts are *not* "expired" — they are simply
    absent. Only a dict past its ``expires_at`` (or older than the TTL
    when ``expires_at`` is missing) counts as expired.
    """

    if not pending:
        return False
    expires_at = pending.get("expires_at")
    if expires_at is not None:
        try:
            return int(now_epoch) >= int(expires_at)
        except (TypeError, ValueError):
            return True
    # Fall back to fired_at + ttl_seconds if the explicit bound is gone.
    fired_at = pending.get("fired_at")
    ttl = pending.get("ttl_seconds", REACTION_TTL_SECONDS)
    if fired_at is None:
        return False
    try:
        return int(now_epoch) >= int(fired_at) + int(ttl)
    except (TypeError, ValueError):
        return True


__all__ = [
    "REACTION_TTL_SECONDS",
    "ReactionKind",
    "TriggerEvent",
    "OnHitByAttack",
    "OnSeeingSpellCast",
    "OnAllyDown",
    "OnDamageTaken",
    "build_pending_reaction",
    "pending_reaction_is_expired",
    "trigger_from_payload",
]

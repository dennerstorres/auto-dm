"""Phase 41a — reaction model tests.

Covers:
* ``ReactionKind`` enum (every kind listed, stable string values).
* ``TriggerEvent`` dataclasses carry the fields dispatch needs and
  serialise to the persistable payload shape via ``to_payload``.
* ``build_pending_reaction`` enforces the 30 s TTL, refuses empty
  eligibility lists and missing epoch, and round-trips through
  ``trigger_from_payload``.
* ``pending_reaction_is_expired`` is the single source of truth for the
  auto-pass window.
* ``Character.pending_reaction`` back-compat: old saves (no field) load
  with ``None``; a populated field survives ``model_dump_json`` round-trip.
"""
from __future__ import annotations

import json

import pytest

from auto_dm.engine.actions import (
    REACTION_TTL_SECONDS,
    OnAllyDown,
    OnDamageTaken,
    OnHitByAttack,
    OnSeeingSpellCast,
    ReactionKind,
    TriggerEvent,
    build_pending_reaction,
    pending_reaction_is_expired,
    trigger_from_payload,
)
from auto_dm.state.models import AbilityScores, Character


# ============================================================================
# Helpers
# ============================================================================


def make_character(*, cid: str = "p1", player: bool = True) -> Character:
    return Character(
        id=cid,
        name="Tester",
        race="Human",
        **{"class": "Fighter"},
        subclass=None,
        level=5,
        background="Soldier",
        alignment="LN",
        abilities=AbilityScores(
            strength=14, dexterity=12, constitution=14,
            intelligence=10, wisdom=12, charisma=10,
        ),
        hp_current=20, hp_max=20, armor_class=16, speed=30,
        proficiency_bonus=2,
        hit_dice="1d10", hit_dice_remaining=5,
        is_player=player,
    )


# ============================================================================
# ReactionKind enum
# ============================================================================


class TestReactionKindEnum:
    def test_every_kind_exists_and_is_string(self):
        expected = {
            "opportunity_attack", "shield", "counterspell",
            "hellish_rebuke", "healing_word", "uncanny_dodge", "parry",
        }
        actual = {k.value for k in ReactionKind}
        assert expected <= actual, actual

    @pytest.mark.parametrize("kind", list(ReactionKind))
    def test_kind_is_stable_string(self, kind: ReactionKind):
        # str-mixin enum → value is a plain string, identity holds.
        assert isinstance(kind.value, str)
        assert ReactionKind(kind.value) is kind

    def test_lookup_by_value_roundtrip(self):
        for k in ReactionKind:
            assert ReactionKind(k.value) is k


# ============================================================================
# Trigger dataclasses
# ============================================================================


class TestTriggerEvents:
    def test_on_hit_by_attack_carries_damage_and_melee_flag(self):
        t = OnHitByAttack(
            target_id="p1", attacker_id="orc1",
            attack_damage=12, damage_type="slashing",
            is_melee=True, is_crit=False,
        )
        assert t.kind == "on_hit_by_attack"
        assert t.target_id == "p1"
        assert t.attack_damage == 12
        assert t.is_melee is True

    def test_on_hit_by_attack_payload_is_json_friendly(self):
        t = OnHitByAttack(target_id="p1", attacker_id="orc1", attack_damage=8)
        payload = t.to_payload()
        json.dumps(payload)  # must not raise
        assert payload["kind"] == "on_hit_by_attack"
        assert payload["attack_damage"] == 8

    def test_on_seeing_spell_cast_carries_level_and_name(self):
        t = OnSeeingSpellCast(
            caster_id="mage1", spell_name="Fireball", level=3,
        )
        assert t.kind == "on_seeing_spell_cast"
        assert t.spell_name == "Fireball"
        assert t.level == 3
        p = t.to_payload()
        assert p["caster_id"] == "mage1" and p["level"] == 3

    def test_on_ally_down_carries_ally_id(self):
        t = OnAllyDown(ally_id="comp2")
        assert t.kind == "on_ally_down"
        assert t.to_payload()["ally_id"] == "comp2"

    def test_on_damage_taken_independent_of_attack(self):
        t = OnDamageTaken(
            target_id="p1", amount=6, damage_type="fire", source_id="mage1",
        )
        assert t.kind == "on_damage_taken"
        assert t.amount == 6 and t.damage_type == "fire"
        assert t.to_payload()["source_id"] == "mage1"

    def test_triggers_are_frozen(self):
        t = OnHitByAttack(target_id="p1", attacker_id="a", attack_damage=5)
        with pytest.raises(Exception):
            t.attack_damage = 99  # type: ignore[misc]

    def test_fired_at_defaults_to_none(self):
        assert OnHitByAttack().fired_at is None
        assert OnSeeingSpellCast().fired_at is None


# ============================================================================
# build_pending_reaction + TTL
# ============================================================================


class TestBuildPendingReaction:
    def test_builds_with_explicit_epoch_and_default_ttl(self):
        t = OnHitByAttack(target_id="p1", attacker_id="a", attack_damage=10)
        pending = build_pending_reaction(
            t, [ReactionKind.UNCANNY_DODGE], fired_at=1000,
        )
        assert pending is not None
        assert pending["fired_at"] == 1000
        assert pending["expires_at"] == 1000 + REACTION_TTL_SECONDS
        assert pending["ttl_seconds"] == REACTION_TTL_SECONDS
        assert pending["reactions_eligible"] == ["uncanny_dodge"]
        assert pending["resolved"] is False
        assert pending["chosen"] is None
        assert pending["trigger"]["kind"] == "on_hit_by_attack"

    def test_uses_trigger_fired_at_when_no_override(self):
        t = OnSeeingSpellCast(caster_id="m", spell_name="Fireball", level=3)
        t = OnSeeingSpellCast(caster_id="m", spell_name="Fireball", level=3,
                              fired_at=4242)
        pending = build_pending_reaction(t, [ReactionKind.COUNTERSPELL])
        assert pending is not None
        assert pending["fired_at"] == 4242

    def test_returns_none_when_nothing_eligible(self):
        t = OnHitByAttack(target_id="p1", attacker_id="a", attack_damage=5)
        assert build_pending_reaction(t, [], fired_at=1000) is None

    def test_returns_none_without_epoch(self):
        # No epoch anywhere → unrecoverable TTL → don't publish.
        t = OnHitByAttack(target_id="p1", attacker_id="a", attack_damage=5)
        assert build_pending_reaction(t, [ReactionKind.PARRY]) is None

    def test_custom_ttl_respected(self):
        t = OnDamageTaken(target_id="p1", amount=4)
        pending = build_pending_reaction(
            t, [ReactionKind.HELLISH_REBUKE], fired_at=10, ttl_seconds=60,
        )
        assert pending is not None
        assert pending["ttl_seconds"] == 60
        assert pending["expires_at"] == 70

    def test_serialisable_to_json(self):
        t = OnAllyDown(ally_id="comp2")
        pending = build_pending_reaction(
            t, [ReactionKind.HEALING_WORD], fired_at=1,
        )
        assert pending is not None
        # The whole point of the payload shape is to live inside a save.
        json.dumps(pending)


# ============================================================================
# pending_reaction_is_expired
# ============================================================================


class TestPendingReactionExpiry:
    def test_none_is_not_expired(self):
        assert pending_reaction_is_expired(None, now_epoch=10_000) is False

    def test_empty_dict_is_not_expired(self):
        # Absent ≠ expired. Only a real timer past its bound counts.
        assert pending_reaction_is_expired({}, now_epoch=10_000) is False

    def test_within_window_not_expired(self):
        pending = build_pending_reaction(
            OnHitByAttack(target_id="p1", attacker_id="a", attack_damage=5),
            [ReactionKind.UNCANNY_DODGE], fired_at=1000,
        )
        assert pending is not None
        assert pending_reaction_is_expired(pending, now_epoch=1000 + 5) is False
        # One tick before expiry is still open.
        assert pending_reaction_is_expired(
            pending, now_epoch=1000 + REACTION_TTL_SECONDS - 1,
        ) is False

    def test_at_or_after_expiry_is_expired(self):
        pending = build_pending_reaction(
            OnHitByAttack(target_id="p1", attacker_id="a", attack_damage=5),
            [ReactionKind.UNCANNY_DODGE], fired_at=1000,
        )
        assert pending is not None
        boundary = 1000 + REACTION_TTL_SECONDS
        assert pending_reaction_is_expired(pending, now_epoch=boundary) is True
        assert pending_reaction_is_expired(pending, now_epoch=boundary + 1) is True

    def test_ttl_is_thirty_seconds(self):
        assert REACTION_TTL_SECONDS == 30

    def test_falls_back_to_fired_at_plus_ttl(self):
        # expires_at stripped — should still expire via fired_at + ttl.
        pending = build_pending_reaction(
            OnDamageTaken(target_id="p1", amount=3),
            [ReactionKind.HELLISH_REBUKE], fired_at=200,
        )
        assert pending is not None
        pending.pop("expires_at")
        assert pending_reaction_is_expired(
            pending, now_epoch=200 + REACTION_TTL_SECONDS,
        ) is True

    def test_malformed_expires_at_treated_as_expired(self):
        pending = {"expires_at": "not-a-number", "fired_at": 1}
        assert pending_reaction_is_expired(pending, now_epoch=5) is True


# ============================================================================
# trigger_from_payload round-trip
# ============================================================================


class TestTriggerFromPayload:
    @pytest.mark.parametrize("trigger,eligible", [
        (OnHitByAttack(target_id="p1", attacker_id="a", attack_damage=9),
         [ReactionKind.UNCANNY_DODGE, ReactionKind.PARRY]),
        (OnSeeingSpellCast(caster_id="m", spell_name="Fireball", level=3),
         [ReactionKind.COUNTERSPELL]),
        (OnAllyDown(ally_id="comp2"), [ReactionKind.HEALING_WORD]),
        (OnDamageTaken(target_id="p1", amount=5, damage_type="fire"),
         [ReactionKind.HELLISH_REBUKE]),
    ])
    def test_roundtrip_preserves_kind_and_fields(self, trigger, eligible):
        pending = build_pending_reaction(trigger, eligible, fired_at=1)
        assert pending is not None
        restored = trigger_from_payload(pending["trigger"])
        assert isinstance(restored, type(trigger))
        assert restored.kind == trigger.kind

    def test_unknown_kind_degrades_gracefully(self):
        restored = trigger_from_payload({"kind": "on_future_trigger", "x": 1})
        assert isinstance(restored, TriggerEvent)
        assert restored.kind == "on_future_trigger"

    def test_unknown_fields_ignored(self):
        restored = trigger_from_payload({
            "kind": "on_hit_by_attack",
            "target_id": "p1",
            "bogus": "ignored",
        })
        assert isinstance(restored, OnHitByAttack)
        assert restored.target_id == "p1"


# ============================================================================
# Character.pending_reaction — back-compat + persistence
# ============================================================================


class TestCharacterPendingReactionField:
    def test_default_is_none(self):
        c = make_character()
        assert c.pending_reaction is None

    def test_old_save_without_field_loads_as_none(self):
        """A character dumped before Phase 41 has no pending_reaction key.
        Loading it must not crash and the field defaults to None."""
        c = make_character()
        legacy = c.model_dump_json()
        legacy_obj = json.loads(legacy)
        legacy_obj.pop("pending_reaction", None)
        restored = Character.model_validate_json(json.dumps(legacy_obj))
        assert restored.pending_reaction is None

    def test_populated_field_survives_roundtrip(self):
        c = make_character()
        pending = build_pending_reaction(
            OnSeeingSpellCast(caster_id="m", spell_name="Fireball", level=3),
            [ReactionKind.COUNTERSPELL], fired_at=777,
        )
        assert pending is not None
        c.pending_reaction = pending
        restored = Character.model_validate_json(c.model_dump_json())
        assert restored.pending_reaction is not None
        assert restored.pending_reaction["fired_at"] == 777
        assert restored.pending_reaction["reactions_eligible"] == ["counterspell"]
        assert restored.pending_reaction["trigger"]["spell_name"] == "Fireball"

    def test_setting_to_none_clears(self):
        c = make_character()
        c.pending_reaction = {"fired_at": 1}
        c.pending_reaction = None
        assert c.pending_reaction is None

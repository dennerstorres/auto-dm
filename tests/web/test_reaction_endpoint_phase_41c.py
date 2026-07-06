"""Phase 41c — Web endpoint for resolving a published reaction.

Covers ``POST /api/sessions/{sid}/reaction``:
- auth required (401), 404 unknown session, 404 no pending trigger,
- 408 when the trigger window expired,
- ``decline=true`` clears without resolving,
- 422 unknown kind / kind not in eligible list,
- 200 Shield resolves (consumes slot, sets pending_ac_bonus, clears
  pending_reaction and persists),
- 200 Counterspell auto-cancels a L3 spell,
- the returned state reflects the mutation.
"""
from __future__ import annotations

import time

import pytest

from tests.web.conftest import _stub_provider_factory  # noqa: F401


@pytest.fixture(autouse=True)
def _disable_invite_gate(monkeypatch):
    """Open signup so tests can create users without the invite code."""
    monkeypatch.setenv("INVITE_CODE", "")
    from auto_dm.web.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ============================================================================
# State builder — a Wizard L5 with Shield + Counterspell prepared, plus a
# pending_reaction trigger we can manipulate per test.
# ============================================================================


def _game_state_with_pending(trigger_kind: str = "on_hit_by_attack",
                             eligible: list[str] | None = None,
                             fired_at: int | None = None) -> dict:
    from datetime import datetime

    from auto_dm.engine.actions import (
        OnHitByAttack,
        OnSeeingSpellCast,
        build_pending_reaction,
        ReactionKind,
    )
    from auto_dm.state.models import (
        Ability,
        AbilityScores,
        Character,
        GameState,
        Spellcasting,
    )

    slots = {1: 4, 2: 3, 3: 2}
    wiz = Character(
        id="wiz", name="Merlin", race="Human", class_="Wizard", level=5,
        background="Sage", alignment="LN",
        abilities=AbilityScores(
            strength=8, dexterity=12, constitution=12,
            intelligence=18, wisdom=10, charisma=10,
        ),
        hp_current=20, hp_max=20, armor_class=12, speed=30,
        proficiency_bonus=2, hit_dice="1d6", hit_dice_remaining=5,
        is_player=True,
        spellcasting=Spellcasting(
            ability=Ability.INT, save_dc=15, attack_bonus=7,
            cantrips_known=["Fire Bolt"],
            spells_prepared=["Shield", "Counterspell", "Magic Missile"],
            spell_slots=slots, spell_slots_max=dict(slots),
        ),
    )
    if eligible is None:
        eligible = ["shield", "uncanny_dodge"]
    if trigger_kind == "on_seeing_spell_cast":
        trigger = OnSeeingSpellCast(caster_id="enemy", spell_name="Fireball", level=3)
    else:
        trigger = OnHitByAttack(
            target_id="wiz", attacker_id="orc", attack_damage=10,
        )
    epoch = fired_at if fired_at is not None else int(time.time())
    wiz.pending_reaction = build_pending_reaction(
        trigger, [ReactionKind(e) for e in eligible], fired_at=epoch,
    )

    gs = GameState(
        campaign_name="rx-web",
        started_at=datetime.now(),
        party=[wiz],
        npcs=[],
        player_character_id="wiz",
    )
    return gs.model_dump(mode="json")


async def _create_session_with_state(client, headers, state: dict) -> str:
    resp = await client.post(
        "/api/sessions", json={"state": state}, headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


# ============================================================================
# Tests
# ============================================================================


class TestReactionEndpoint:
    @pytest.mark.asyncio
    async def test_requires_auth(self, client):
        resp = await client.post(
            "/api/sessions/whatever/reaction", json={"kind": "shield"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_404_unknown_session(self, client, admin_token):
        _, _, headers = admin_token
        resp = await client.post(
            "/api/sessions/nope/reaction",
            json={"kind": "shield"}, headers=headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_404_no_pending_reaction(self, client, admin_token):
        _, _, headers = admin_token
        from tests.web.test_xp_endpoints_phase_38 import _create_session  # reuse
        sid, _ = await _create_session(client, headers)
        resp = await client.post(
            f"/api/sessions/{sid}/reaction",
            json={"kind": "shield"}, headers=headers,
        )
        assert resp.status_code == 404
        assert "pending" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_408_when_expired(self, client, admin_token):
        _, _, headers = admin_token
        # fired_at far in the past → past the 30 s TTL.
        state = _game_state_with_pending(fired_at=1)
        sid = await _create_session_with_state(client, headers, state)
        resp = await client.post(
            f"/api/sessions/{sid}/reaction",
            json={"kind": "shield"}, headers=headers,
        )
        assert resp.status_code == 408

    @pytest.mark.asyncio
    async def test_decline_clears_pending(self, client, admin_token):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers, _game_state_with_pending(),
        )
        resp = await client.post(
            f"/api/sessions/{sid}/reaction",
            json={"kind": "shield", "decline": True}, headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["declined"] is True
        player = body["state"]["party"][0]
        assert player["pending_reaction"] is None

    @pytest.mark.asyncio
    async def test_422_unknown_kind(self, client, admin_token):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers, _game_state_with_pending(),
        )
        resp = await client.post(
            f"/api/sessions/{sid}/reaction",
            json={"kind": "banana"}, headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_422_kind_not_eligible(self, client, admin_token):
        _, _, headers = admin_token
        # Eligible list is only ["shield"] → parry is rejected.
        state = _game_state_with_pending(eligible=["shield"])
        sid = await _create_session_with_state(client, headers, state)
        resp = await client.post(
            f"/api/sessions/{sid}/reaction",
            json={"kind": "parry"}, headers=headers,
        )
        assert resp.status_code == 422
        assert "not eligible" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_shield_resolves_slot_and_ac_bonus(self, client, admin_token):
        _, _, headers = admin_token
        state = _game_state_with_pending()
        slots_before = state["party"][0]["spellcasting"]["spell_slots"]["1"]
        sid = await _create_session_with_state(client, headers, state)
        resp = await client.post(
            f"/api/sessions/{sid}/reaction",
            json={"kind": "shield"}, headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        res = body["resolution"]
        assert res["success"] is True
        assert res["consumed_slot_level"] == 1
        player = body["state"]["party"][0]
        assert player["pending_ac_bonus"] == 5
        assert player["shield_active"] is True
        assert player["reaction_available"] is False
        assert player["pending_reaction"] is None
        assert player["spellcasting"]["spell_slots"]["1"] == slots_before - 1

    @pytest.mark.asyncio
    async def test_counterspell_auto_cancels_l3(self, client, admin_token):
        _, _, headers = admin_token
        state = _game_state_with_pending(
            trigger_kind="on_seeing_spell_cast",
            eligible=["counterspell"],
        )
        sid = await _create_session_with_state(client, headers, state)
        resp = await client.post(
            f"/api/sessions/{sid}/reaction",
            json={"kind": "counterspell"}, headers=headers,
        )
        assert resp.status_code == 200, resp.text
        res = resp.json()["resolution"]
        assert res["success"] is True
        assert res["spell_cancelled"] is True
        assert res["consumed_slot_level"] == 3

    @pytest.mark.asyncio
    async def test_state_persisted_across_calls(self, client, admin_token):
        """A second /reaction after the first has no pending → 404."""
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers, _game_state_with_pending(),
        )
        first = await client.post(
            f"/api/sessions/{sid}/reaction",
            json={"kind": "shield"}, headers=headers,
        )
        assert first.status_code == 200
        second = await client.post(
            f"/api/sessions/{sid}/reaction",
            json={"kind": "shield"}, headers=headers,
        )
        assert second.status_code == 404  # pending cleared

"""Phase 52 — ``POST /api/sessions/{sid}/roll-check`` against a pending check.

Before this phase the endpoint was a detached dice roller: it never read
or wrote game state, so a check the DM asked for went nowhere. Now a roll
that answers the open ``pending_check`` is scored against the DC the DM
fixed beforehand, closes the request, and comes back with the ``[TESTE]``
line the frontend hands to the DM.

Rolls that match nothing pending keep the old behavior exactly — dice on
the table, no state change.
"""
from __future__ import annotations

from datetime import datetime

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


def _state_with_pending(
    check: str | None = "investigação",
    dc: int = 15,
    *,
    kind: str | None = None,
    advantage: bool = False,
    disadvantage: bool = False,
) -> dict:
    """A Rogue L1 with INT 14 (+2) and Investigation proficiency (+2) → +4."""
    from auto_dm.engine.checks import build_pending_check
    from auto_dm.state.models import (
        Ability,
        AbilityScores,
        Character,
        GameState,
        Proficiencies,
        Skill,
    )

    rogue = Character(
        id="pc", name="Nara", race="Human", class_="Rogue", level=1,
        background="Criminal", alignment="CN",
        abilities=AbilityScores(
            strength=8, dexterity=16, constitution=12,
            intelligence=14, wisdom=10, charisma=13,
        ),
        hp_current=9, hp_max=9, armor_class=14, speed=30,
        proficiency_bonus=2, hit_dice="1d8", hit_dice_remaining=1,
        proficiencies=Proficiencies(
            saves=[Ability.DEX, Ability.INT],
            skills=[Skill.INVESTIGATION, Skill.STEALTH],
        ),
        is_player=True,
    )
    gs = GameState(
        campaign_name="check-web-52",
        started_at=datetime.now(),
        party=[rogue],
        npcs=[],
        player_character_id="pc",
    )
    if check is not None:
        gs.pending_check = build_pending_check(
            check, dc, kind=kind,
            reason="vasculhar a estante",
            advantage=advantage,
            disadvantage=disadvantage,
        )
    return gs.model_dump(mode="json")


async def _create_session_with_state(client, headers, state: dict) -> str:
    resp = await client.post("/api/sessions", json={"state": state}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


async def _roll(client, headers, sid: str, check: str, **body):
    return await client.post(
        f"/api/sessions/{sid}/roll-check",
        json={"check": check, **body},
        headers=headers,
    )


async def _fetch_state(client, headers, sid: str) -> dict:
    resp = await client.get(f"/api/sessions/{sid}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["state"]


class TestRollAnsweringAPendingCheck:
    @pytest.mark.asyncio
    async def test_returns_a_resolution_scored_against_the_dc(
        self, client, admin_token,
    ):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers, _state_with_pending("investigação", 15),
        )

        resp = await _roll(client, headers, sid, "investigacao")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        resolution = body["resolution"]
        assert resolution is not None
        assert resolution["dc"] == 15
        assert resolution["key"] == "investigation"
        assert resolution["total"] == body["total"]
        assert resolution["success"] == (body["total"] >= 15)
        assert resolution["margin"] == body["total"] - 15

    @pytest.mark.asyncio
    async def test_resolution_carries_the_teste_line_for_the_dm(
        self, client, admin_token,
    ):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers, _state_with_pending("investigação", 15),
        )

        resolution = (await _roll(client, headers, sid, "investigacao")).json()[
            "resolution"
        ]

        line = resolution["narration_line"]
        assert line.startswith("[TESTE]")
        assert "CD 15" in line
        assert "vasculhar a estante" in line
        assert ("SUCESSO" in line) == resolution["success"]

    @pytest.mark.asyncio
    async def test_request_is_closed_on_the_persisted_state(
        self, client, admin_token,
    ):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers, _state_with_pending("investigação", 15),
        )

        body = (await _roll(client, headers, sid, "investigacao")).json()
        pending = (await _fetch_state(client, headers, sid))["pending_check"]

        assert pending["resolved"] is True
        assert pending["total"] == body["total"]
        assert pending["outcome"] in {"success", "failure"}

    @pytest.mark.asyncio
    async def test_second_roll_no_longer_answers_the_closed_request(
        self, client, admin_token,
    ):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers, _state_with_pending("investigação", 15),
        )

        await _roll(client, headers, sid, "investigacao")
        second = await _roll(client, headers, sid, "investigacao")

        assert second.json()["resolution"] is None

    @pytest.mark.asyncio
    async def test_dm_granted_advantage_is_applied_without_the_player_asking(
        self, client, admin_token,
    ):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers,
            _state_with_pending("investigação", 15, advantage=True),
        )

        body = (await _roll(client, headers, sid, "investigacao")).json()

        assert body["advantage"] is True
        assert len(body["rolls"]) == 2

    @pytest.mark.asyncio
    async def test_dm_granted_disadvantage_is_applied(self, client, admin_token):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers,
            _state_with_pending("investigação", 15, disadvantage=True),
        )

        body = (await _roll(client, headers, sid, "investigacao")).json()

        assert body["disadvantage"] is True
        assert len(body["rolls"]) == 2

    @pytest.mark.asyncio
    async def test_player_advantage_plus_dm_disadvantage_cancel_out(
        self, client, admin_token,
    ):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers,
            _state_with_pending("investigação", 15, disadvantage=True),
        )

        body = (
            await _roll(client, headers, sid, "investigacao", advantage=True)
        ).json()

        assert body["advantage"] is False
        assert body["disadvantage"] is False
        assert len(body["rolls"]) == 1

    @pytest.mark.asyncio
    async def test_saving_throw_request_is_answered_by_a_save_roll(
        self, client, admin_token,
    ):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers,
            _state_with_pending("destreza", 13, kind="save"),
        )

        body = (
            await _roll(client, headers, sid, "destreza", kind="save")
        ).json()

        assert body["kind"] == "save"
        assert body["resolution"]["dc"] == 13


class TestFreeRolls:
    @pytest.mark.asyncio
    async def test_roll_with_no_pending_request_returns_no_resolution(
        self, client, admin_token,
    ):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers, _state_with_pending(check=None),
        )

        resp = await _roll(client, headers, sid, "investigacao")

        assert resp.status_code == 200
        assert resp.json()["resolution"] is None

    @pytest.mark.asyncio
    async def test_rolling_a_different_skill_does_not_answer_the_request(
        self, client, admin_token,
    ):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers, _state_with_pending("investigação", 15),
        )

        resp = await _roll(client, headers, sid, "furtividade")

        assert resp.json()["resolution"] is None
        pending = (await _fetch_state(client, headers, sid))["pending_check"]
        assert pending["resolved"] is False

    @pytest.mark.asyncio
    async def test_dm_advantage_does_not_leak_into_an_unrelated_roll(
        self, client, admin_token,
    ):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers,
            _state_with_pending("investigação", 15, advantage=True),
        )

        body = (await _roll(client, headers, sid, "furtividade")).json()

        assert body["advantage"] is False
        assert len(body["rolls"]) == 1

    @pytest.mark.asyncio
    async def test_ability_check_does_not_answer_a_save_request(
        self, client, admin_token,
    ):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers,
            _state_with_pending("destreza", 13, kind="save"),
        )

        body = (await _roll(client, headers, sid, "destreza")).json()

        assert body["kind"] == "ability"
        assert body["resolution"] is None


class TestErrors:
    @pytest.mark.asyncio
    async def test_requires_auth(self, client):
        resp = await client.post(
            "/api/sessions/whatever/roll-check", json={"check": "investigacao"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_404_unknown_session(self, client, admin_token):
        _, _, headers = admin_token
        resp = await _roll(client, headers, "nope", "investigacao")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_422_unknown_check(self, client, admin_token):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers, _state_with_pending("investigação", 15),
        )

        resp = await _roll(client, headers, sid, "teste de vibe")

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_unknown_check_leaves_the_request_open(self, client, admin_token):
        _, _, headers = admin_token
        sid = await _create_session_with_state(
            client, headers, _state_with_pending("investigação", 15),
        )

        await _roll(client, headers, sid, "teste de vibe")

        pending = (await _fetch_state(client, headers, sid))["pending_check"]
        assert pending["resolved"] is False

"""Phase 52 — pending skill checks with a DC fixed before the roll.

Covers the three layers that make a DM-requested check actually resolve:

- ``engine/checks.py``: building the request, clamping the DC, matching a
  roll against it, and scoring it (engine-authoritative, no LLM).
- ``agents/dm.py``: parsing the ```check``` fenced block out of the DM's
  reply without leaking JSON into the narration.
- ``agents/narrative.py``: storing the request on the state, dropping
  garbage requests, and consuming a resolved one on the next turn.
"""
from __future__ import annotations

import random
from datetime import datetime

from auto_dm.agents.dm import DMResponse, parse_dm_response
from auto_dm.agents.narrative import process_player_action
from auto_dm.agents.prompts import build_dm_context_block
from auto_dm.engine.checks import (
    DEFAULT_DC,
    build_pending_check,
    check_matches_pending,
    clamp_dc,
    dc_label,
    describe_check_outcome,
    pending_check_is_open,
    resolve_check,
    resolve_pending_check,
    roll_character_check,
)
from auto_dm.state import Ability, AbilityScores, Character, Proficiencies, Skill
from auto_dm.state.manager import StateManager
from auto_dm.state.models import GameState


def make_player() -> Character:
    """INT 14 (+2) Rogue proficient in Investigation → +4 on the check."""
    return Character(
        id="pc",
        name="Nara",
        race="Human",
        **{"class": "Rogue"},
        level=1,
        background="Criminal",
        alignment="CN",
        abilities=AbilityScores(
            strength=8,
            dexterity=16,
            constitution=12,
            intelligence=14,
            wisdom=10,
            charisma=13,
        ),
        hp_current=9,
        hp_max=9,
        armor_class=14,
        speed=30,
        proficiency_bonus=2,
        hit_dice="1d8",
        hit_dice_remaining=1,
        proficiencies=Proficiencies(
            saves=[Ability.DEX, Ability.INT],
            skills=[Skill.INVESTIGATION, Skill.STEALTH],
        ),
        is_player=True,
    )


def make_state() -> GameState:
    return GameState(
        campaign_name="check-52",
        started_at=datetime.now(),
        party=[make_player()],
        npcs=[],
        player_character_id="pc",
    )


class _ScriptedDM:
    """DM agent double returning a fixed reply, recording what it was asked."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.asked: list[str] = []

    def ask(self, player_input: str) -> DMResponse:
        self.asked.append(player_input)
        return parse_dm_response(self.raw)


# ============================================================================
# Engine — building and clamping the request
# ============================================================================


class TestBuildPendingCheck:
    def test_resolves_portuguese_skill_and_keeps_dc(self):
        pending = build_pending_check("investigação", 15, reason="vasculhar a estante")

        assert pending["kind"] == "skill"
        assert pending["key"] == "investigation"
        assert pending["ability"] == "intelligence"
        assert pending["dc"] == 15
        assert pending["reason"] == "vasculhar a estante"
        assert pending["resolved"] is False
        assert pending["outcome"] is None

    def test_saving_throw_request(self):
        pending = build_pending_check("destreza", 12, kind="save")

        assert pending["kind"] == "save"
        assert pending["key"] == "dexterity"
        assert "Salvaguarda" in pending["label"]

    def test_unknown_check_raises(self):
        try:
            build_pending_check("teste de vibe", 15)
        except ValueError:
            return
        raise AssertionError("expected ValueError for an unresolvable check")

    def test_advantage_and_disadvantage_cancel_out(self):
        pending = build_pending_check(
            "furtividade", 15, advantage=True, disadvantage=True,
        )

        assert pending["advantage"] is False
        assert pending["disadvantage"] is False

    def test_reason_is_truncated(self):
        pending = build_pending_check("percepção", 10, reason="x" * 500)

        assert len(pending["reason"]) == 200

    def test_each_request_gets_its_own_id(self):
        first = build_pending_check("percepção", 10)
        second = build_pending_check("percepção", 10)

        assert first["id"] != second["id"]


class TestClampDC:
    def test_keeps_values_inside_the_phb_band(self):
        assert clamp_dc(5) == 5
        assert clamp_dc(15) == 15
        assert clamp_dc(30) == 30

    def test_clamps_out_of_band_values(self):
        # A hallucinated "DC 90" must never make a check unwinnable.
        assert clamp_dc(90) == 30
        assert clamp_dc(-4) == 5

    def test_falls_back_to_default_on_junk(self):
        assert clamp_dc(None) == DEFAULT_DC
        assert clamp_dc("quinze") == DEFAULT_DC
        assert clamp_dc({}) == DEFAULT_DC

    def test_numeric_strings_are_accepted(self):
        assert clamp_dc("18") == 18

    def test_dc_label_maps_to_phb_tiers(self):
        assert dc_label(5) == "muito facil"
        assert dc_label(15) == "media"
        assert dc_label(30) == "quase impossivel"


# ============================================================================
# Engine — matching and scoring
# ============================================================================


class TestMatching:
    def test_same_skill_answers_the_request(self):
        pending = build_pending_check("investigação", 15)

        assert check_matches_pending(resolve_check("investigacao"), pending) is True

    def test_different_skill_is_a_free_roll(self):
        pending = build_pending_check("investigação", 15)

        assert check_matches_pending(resolve_check("percepcao"), pending) is False

    def test_same_ability_but_different_kind_does_not_match(self):
        # A DEX *save* request is not answered by a DEX ability check.
        pending = build_pending_check("destreza", 15, kind="save")

        assert check_matches_pending(resolve_check("destreza"), pending) is False

    def test_resolved_request_no_longer_matches(self):
        pending = build_pending_check("investigação", 15)
        pending["resolved"] = True

        assert check_matches_pending(resolve_check("investigacao"), pending) is False

    def test_no_pending_request_matches_nothing(self):
        assert check_matches_pending(resolve_check("investigacao"), None) is False
        assert pending_check_is_open(None) is False


class TestResolvePendingCheck:
    def _roll(self, seed: int):
        return roll_character_check(
            make_player(), "investigacao", rng=random.Random(seed),
        )

    def test_total_at_or_above_dc_succeeds(self):
        result = self._roll(1)
        pending = build_pending_check("investigação", result.roll.total)

        resolution = resolve_pending_check(pending, result)

        assert resolution["success"] is True
        assert resolution["margin"] == 0
        assert resolution["outcome"] == "success"

    def test_total_below_dc_fails(self):
        result = self._roll(1)
        pending = build_pending_check("investigação", result.roll.total + 3)

        resolution = resolve_pending_check(pending, result)

        assert resolution["success"] is False
        assert resolution["margin"] == -3
        assert resolution["outcome"] == "failure"

    def test_closes_the_request_in_place(self):
        result = self._roll(7)
        pending = build_pending_check("investigação", 10)

        resolve_pending_check(pending, result)

        assert pending["resolved"] is True
        assert pending["total"] == result.roll.total
        assert pending["natural"] == result.roll.kept[0]
        assert pending["outcome"] in {"success", "failure"}

    def test_scores_against_the_clamped_dc_not_the_raw_one(self):
        result = self._roll(3)
        pending = build_pending_check("investigação", 999)  # clamped to 30

        resolution = resolve_pending_check(pending, result)

        assert resolution["dc"] == 30

    def test_natural_20_is_not_an_automatic_success(self):
        # PHB p. 7: auto-success on a nat 20 is an attack-roll rule, not an
        # ability-check rule. A +4 modifier can't reach DC 30 even on a 20.
        player = make_player()
        result = roll_character_check(player, "investigacao", rng=_FixedD20(20))
        pending = build_pending_check("investigação", 30)

        resolution = resolve_pending_check(pending, result)

        assert resolution["natural"] == 20
        assert resolution["success"] is False


class _FixedD20(random.Random):
    """RNG double that always yields the same d20 face."""

    def __init__(self, face: int) -> None:
        super().__init__(0)
        self.face = face

    def randint(self, a: int, b: int) -> int:  # noqa: D102
        return self.face


class TestNarrationLine:
    def test_line_carries_verdict_margin_and_reason(self):
        result = roll_character_check(
            make_player(), "investigacao", rng=random.Random(1),
        )
        pending = build_pending_check(
            "investigação", result.roll.total, reason="vasculhar a estante",
        )

        line = describe_check_outcome(pending, result)

        assert line.startswith("[TESTE]")
        assert "SUCESSO" in line
        assert "vasculhar a estante" in line
        assert str(result.roll.total) in line

    def test_failure_line_says_falha(self):
        result = roll_character_check(
            make_player(), "investigacao", rng=random.Random(1),
        )
        pending = build_pending_check("investigação", result.roll.total + 5)

        line = describe_check_outcome(pending, result)

        assert "FALHA por 5" in line

    def test_line_mentions_advantage(self):
        result = roll_character_check(
            make_player(), "investigacao", advantage=True, rng=random.Random(1),
        )
        pending = build_pending_check("investigação", 10)

        assert "com vantagem" in describe_check_outcome(pending, result)


# ============================================================================
# DM agent — parsing the ```check``` block
# ============================================================================


class TestCheckBlockParsing:
    def test_parses_check_block_and_strips_it_from_narration(self):
        raw = (
            "A estante range sob seus dedos.\n\n"
            "```check\n"
            '{"check": "investigação", "dc": 15, "reason": "achar o compartimento"}\n'
            "```"
        )

        response = parse_dm_response(raw)

        assert response.has_check_request is True
        assert response.check_request.check == "investigação"
        assert response.check_request.dc == 15
        assert response.check_request.reason == "achar o compartimento"
        assert "```" not in response.narration
        assert response.narration == "A estante range sob seus dedos."

    def test_parses_action_and_check_blocks_together(self):
        raw = (
            "Você avança pelo corredor.\n"
            "```action\n"
            '{"action_type": "move", "actor_id": "pc", '
            '"params": {"destination": "corredor"}}\n'
            "```\n"
            "```check\n"
            '{"check": "percepção", "dc": 12}\n'
            "```"
        )

        response = parse_dm_response(raw)

        assert response.has_action is True
        assert response.has_check_request is True
        assert "```" not in response.narration
        assert response.narration == "Você avança pelo corredor."

    def test_no_check_block_leaves_request_none(self):
        response = parse_dm_response("Apenas narração.")

        assert response.check_request is None
        assert response.narration == "Apenas narração."

    def test_malformed_json_is_dropped_and_never_leaks_into_narration(self):
        raw = "Texto.\n```check\n{não é json}\n```"

        response = parse_dm_response(raw)

        assert response.check_request is None
        assert "json" not in response.narration

    def test_block_without_check_key_is_dropped(self):
        raw = 'Texto.\n```check\n{"dc": 15}\n```'

        assert parse_dm_response(raw).check_request is None

    def test_accepts_portuguese_field_aliases(self):
        raw = 'Texto.\n```check\n{"pericia": "furtividade", "motivo": "passar"}\n```'

        request = parse_dm_response(raw).check_request

        assert request.check == "furtividade"
        assert request.reason == "passar"

    def test_carries_circumstantial_advantage(self):
        raw = (
            'Texto.\n```check\n{"check": "percepção", "dc": 10, '
            '"advantage": true}\n```'
        )

        assert parse_dm_response(raw).check_request.advantage is True


# ============================================================================
# Narrative loop — storing, dropping, and consuming the request
# ============================================================================


class TestNarrativeLoop:
    def _run(self, raw: str, state: GameState, line: str = "olho a estante"):
        manager = StateManager(state)
        agent = _ScriptedDM(raw)
        return process_player_action(manager, line, agent), manager, agent

    def test_check_request_lands_on_the_state(self):
        raw = (
            "Você se aproxima.\n```check\n"
            '{"check": "investigação", "dc": 17, "reason": "achar a fresta"}\n```'
        )

        result, manager, _ = self._run(raw, make_state())

        assert manager.state.pending_check is not None
        assert manager.state.pending_check["key"] == "investigation"
        assert manager.state.pending_check["dc"] == 17
        assert result.pending_check is manager.state.pending_check

    def test_unresolvable_check_is_dropped_without_breaking_the_turn(self):
        raw = 'Texto.\n```check\n{"check": "teste de vibe", "dc": 15}\n```'

        result, manager, _ = self._run(raw, make_state())

        assert result.pending_check is None
        assert manager.state.pending_check is None
        assert result.narration == "Texto."

    def test_out_of_band_dc_is_clamped_on_the_state(self):
        raw = 'Texto.\n```check\n{"check": "percepção", "dc": 400}\n```'

        _, manager, _ = self._run(raw, make_state())

        assert manager.state.pending_check["dc"] == 30

    def test_missing_dc_defaults_to_medium(self):
        raw = 'Texto.\n```check\n{"check": "percepção"}\n```'

        _, manager, _ = self._run(raw, make_state())

        assert manager.state.pending_check["dc"] == DEFAULT_DC

    def test_new_request_replaces_the_previous_one(self):
        state = make_state()
        state.pending_check = build_pending_check("furtividade", 20)
        raw = 'Texto.\n```check\n{"check": "percepção", "dc": 10}\n```'

        _, manager, _ = self._run(raw, state)

        assert manager.state.pending_check["key"] == "perception"

    def test_resolved_request_is_consumed_on_the_next_turn(self):
        state = make_state()
        pending = build_pending_check("investigação", 10)
        result = roll_character_check(
            make_player(), "investigacao", rng=random.Random(1),
        )
        resolve_pending_check(pending, result)
        state.pending_check = pending

        _, manager, _ = self._run("O compartimento cede.", state, line="[TESTE] ...")

        assert manager.state.pending_check is None

    def test_open_request_survives_an_unrelated_turn(self):
        state = make_state()
        state.pending_check = build_pending_check("investigação", 10)

        _, manager, _ = self._run("Nada acontece.", state, line="olho em volta")

        assert manager.state.pending_check is not None
        assert manager.state.pending_check["resolved"] is False


# ============================================================================
# Prompts — the DM must see the open request and the protocol
# ============================================================================


class TestPrompts:
    def test_context_block_shows_the_open_request(self):
        state = make_state()
        state.pending_check = build_pending_check(
            "investigação", 15, reason="achar a fresta",
        )

        block = build_dm_context_block(StateManager(state))

        assert "Teste pendente" in block
        assert "CD 15" in block
        assert "achar a fresta" in block

    def test_context_block_omits_a_resolved_request(self):
        state = make_state()
        pending = build_pending_check("investigação", 15)
        pending["resolved"] = True
        state.pending_check = pending

        assert "Teste pendente" not in build_dm_context_block(StateManager(state))

    def test_context_block_omits_the_section_when_nothing_is_pending(self):
        assert "Teste pendente" not in build_dm_context_block(StateManager(make_state()))

    def test_system_prompt_documents_the_check_block_and_marker(self):
        from auto_dm.agents.prompts import DM_SYSTEM_PROMPT

        assert "```check" in DM_SYSTEM_PROMPT
        assert "[TESTE]" in DM_SYSTEM_PROMPT
        # The DC must be fixed before the roll — that's the core contract.
        assert "ANTES" in DM_SYSTEM_PROMPT

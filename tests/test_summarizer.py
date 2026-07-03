"""Tests for the periodic narrative summarizer (Phase 33).

Covers:
- ``should_summarize`` trigger predicate (entry-count, char threshold, cooldown, kill switch)
- ``_parse_summary`` rejection criteria (empty, sentinel, markdown-only, short)
- ``apply_summary`` append/dedup/cursor-advance semantics
- ``NarrativeSummarizer.summarize`` end-to-end with a scripted provider
- ``summarize_once`` end-of-turn helper (graceful failure, no LLM call when off)
- Prompt injection via ``build_dm_context_block``
- CLI ``/summary`` meta-command (status / on / off / force)
- ``process_player_action`` and companion turn propagation
- Save/load round-trip of new fields

The fixture hierarchy mirrors ``tests/test_dm_agent.py``: a
``FakeProvider``-style provider that records calls and returns
scripted (text, usage) tuples via ``chat_with_usage``.
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from datetime import datetime, timezone
from typing import Optional

import pytest

from auto_dm.agents import (
    NarrativeSummarizer,
    apply_summary,
    build_dm_context_block,
    process_player_action,
    should_summarize,
    summarize_once,
)
from auto_dm.agents.dm import DMAgent
from auto_dm.agents.summarizer import (
    NO_SUMMARY_SENTINEL,
    _parse_summary,
)
from auto_dm.llm.base import LLMConfig, Message
from auto_dm.llm.usage import UsageReport
from auto_dm.persistence import load_state, save_state
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    AbilityScores,
    Character,
    GameState,
    NarrativeEntry,
)


# ============================================================================
# Stubs
# ============================================================================


class _ScriptedUsageProvider:
    """Provider that scripts content+usage tuples via ``chat_with_usage``.

    Each ``scripted`` entry is either a plain string (no usage) or a
    ``(content, usage)`` tuple. Records every call so tests can assert
    what was sent to the LLM.
    """

    def __init__(
        self,
        scripted: Optional[list] = None,
        fail_with: Optional[Exception] = None,
    ) -> None:
        self.scripted: list = list(scripted or [])
        self.calls: list[list[Message]] = []
        self.config = LLMConfig(name="scripted-usage", api_key="test", model="test")
        self.name = "scripted-usage"
        self._fail_with = fail_with
        self.fail_count = 0

    def chat(self, messages: list[Message]) -> str:
        self.calls.append(messages)
        if self._fail_with is not None:
            self.fail_count += 1
            raise self._fail_with
        if not self.scripted:
            return ""
        item = self.scripted[0] if len(self.scripted) == 1 else self.scripted.pop(0)
        return item if isinstance(item, str) else item[0]

    def chat_with_usage(self, messages: list[Message]) -> tuple[str, UsageReport]:
        self.calls.append(messages)
        if self._fail_with is not None:
            self.fail_count += 1
            raise self._fail_with
        if not self.scripted:
            return "", UsageReport(prompt_tokens=0, completion_tokens=0)
        item = self.scripted[0] if len(self.scripted) == 1 else self.scripted.pop(0)
        if isinstance(item, str):
            return item, UsageReport(
                prompt_tokens=10,
                completion_tokens=5,
                provider=self.name,
                model="test",
                source="api",
            )
        return item[0], item[1]

    def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content) for m in messages)


# ============================================================================
# State + character helpers
# ============================================================================


def _ability_scores() -> AbilityScores:
    return AbilityScores(
        strength=14,
        dexterity=12,
        constitution=13,
        intelligence=10,
        wisdom=11,
        charisma=8,
    )


def _make_character(
    name: str = "Aragorn",
    *,
    char_id: str = "p1",
    is_player: bool = True,
    hp_current: int = 12,
    hp_max: int = 12,
    ac: int = 16,
) -> Character:
    return Character(
        id=char_id,
        name=name,
        race="Human",
        **{"class": "Fighter"},
        level=1,
        background="Soldier",
        alignment="LG",
        abilities=_ability_scores(),
        hp_current=hp_current,
        hp_max=hp_max,
        armor_class=ac,
        speed=30,
        proficiency_bonus=2,
        hit_dice="1d10",
        hit_dice_remaining=1,
        is_player=is_player,
    )


def _make_state() -> GameState:
    player = _make_character()
    companion = _make_character(
        "Legolas", char_id="c1", is_player=False, hp_current=10, hp_max=10, ac=15
    )
    return GameState(
        campaign_name="A Jornada Começa",
        started_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
        current_location="Floresta de Fangorn",
        time_of_day="amanhecer",
        weather="neblina leve",
        party=[player, companion],
        player_character_id=player.id,
    )


@pytest.fixture
def state_manager() -> StateManager:
    return StateManager(_make_state())


def _append_entries(
    state: StateManager, *, n: int, role: str = "dm", speaker: str = "DM",
    content: str = "O jogador avança pela trilha.",
) -> None:
    """Append n NarrativeEntry rows to the log."""
    for _ in range(n):
        state.append_narrative(
            NarrativeEntry(
                timestamp=datetime.now(timezone.utc),
                role=role,
                speaker=speaker,
                content=content,
            )
        )


# ============================================================================
# Schema defaults
# ============================================================================


class TestSchema:
    def test_new_fields_have_expected_defaults(self, state_manager):
        s = state_manager.state
        assert s.summary_enabled is True
        assert s.summary_every_n_entries == 20
        assert s.summary_char_threshold == 12_000
        assert s.last_summarized_at_index == 0
        assert s.last_summary_attempt_at_index == 0

    def test_summary_history_round_trips_through_model_dump(self, state_manager):
        state_manager.state.summary_history.append("teste")
        dumped = state_manager.state.model_dump_json()
        restored = GameState.model_validate_json(dumped)
        assert restored.summary_history == ["teste"]

    def test_cursors_round_trip(self, state_manager):
        state_manager.state.last_summarized_at_index = 42
        state_manager.state.last_summary_attempt_at_index = 7
        restored = GameState.model_validate_json(
            state_manager.state.model_dump_json()
        )
        assert restored.last_summarized_at_index == 42
        assert restored.last_summary_attempt_at_index == 7


# ============================================================================
# Trigger predicate: should_summarize
# ============================================================================


class TestShouldSummarize:
    def test_disabled_returns_false(self, state_manager):
        _append_entries(state_manager, n=25)  # above threshold
        state_manager.state.summary_enabled = False
        assert should_summarize(state_manager.state) is False

    def test_empty_log_returns_false(self, state_manager):
        assert should_summarize(state_manager.state) is False

    def test_entry_count_below_threshold(self, state_manager):
        _append_entries(state_manager, n=19)
        assert should_summarize(state_manager.state) is False

    def test_entry_count_at_threshold(self, state_manager):
        _append_entries(state_manager, n=20)
        assert should_summarize(state_manager.state) is True

    def test_entry_count_above_threshold(self, state_manager):
        _append_entries(state_manager, n=25)
        assert should_summarize(state_manager.state) is True

    def test_char_threshold_just_below(self, state_manager):
        state_manager.state.summary_every_n_entries = 100_000  # disable entry trigger
        # Append entries totaling 11_999 chars
        for _ in range(11):
            state_manager.append_narrative(
                NarrativeEntry(
                    timestamp=datetime.now(timezone.utc),
                    role="dm",
                    speaker="DM",
                    content="x" * 1090,  # 11*1090 = 11990 chars
                )
            )
        assert should_summarize(state_manager.state) is False

    def test_char_threshold_at_limit(self, state_manager):
        state_manager.state.summary_every_n_entries = 100_000
        for _ in range(10):
            state_manager.append_narrative(
                NarrativeEntry(
                    timestamp=datetime.now(timezone.utc),
                    role="dm",
                    speaker="DM",
                    content="x" * 1200,  # 10*1200 = 12000 chars
                )
            )
        assert should_summarize(state_manager.state) is True

    def test_cooldown_blocks_immediate_retry(self, state_manager):
        # Recent attempt + few new entries -> cooldown skips.
        _append_entries(state_manager, n=20)
        state_manager.state.last_summary_attempt_at_index = 19  # 1 entry ago
        assert should_summarize(state_manager.state) is False


# ============================================================================
# Parser
# ============================================================================


class TestParseSummary:
    def test_empty_returns_none(self):
        assert _parse_summary("") is None
        assert _parse_summary("   ") is None

    def test_sentinel_returns_none(self):
        assert _parse_summary(NO_SUMMARY_SENTINEL) is None
        assert _parse_summary(f"Some intro\n{NO_SUMMARY_SENTINEL}\n") is None

    def test_markdown_only_returns_none(self):
        assert _parse_summary("## Header\n### Sub\n") is None

    def test_short_after_strip_returns_none(self):
        assert _parse_summary("## Heading\nTiny.") is None  # < 50 chars body

    def test_valid_kept(self):
        text = (
            "Os heróis derrotaram o dragão vermelho na Caverna Sombria. "
            "Saíram com a Coroa de Fogo e decidiram voltar à cidade."
        )
        assert _parse_summary(text) == text.strip()

    def test_strips_leading_headers(self):
        body = (
            "Os heróis encontraram o elfo na floresta e negociaram paz. "
            "Os goblins recuaram; a tensão permanece, mas a guerra está adiada."
        )
        text = f"## Resumo\n\n{body}"
        assert _parse_summary(text) == body


# ============================================================================
# apply_summary
# ============================================================================


class TestApplySummary:
    def test_appends_and_advances_cursors(self, state_manager):
        _append_entries(state_manager, n=10)
        text = "Resumo válido com mais de cinquenta caracteres para passar do gate."
        before_attempt = state_manager.state.last_summary_attempt_at_index
        applied = apply_summary(
            state_manager.state,
            text,
            advance_summarized_index_to=4,
        )
        assert applied is True
        assert state_manager.state.summary_history[-1] == text
        assert state_manager.state.last_summarized_at_index == 4
        assert state_manager.state.last_summary_attempt_at_index > before_attempt

    def test_skips_short_text_but_advances_cursors(self, state_manager):
        _append_entries(state_manager, n=10)
        applied = apply_summary(
            state_manager.state,
            "curto",
            advance_summarized_index_to=4,
        )
        assert applied is False
        assert state_manager.state.summary_history == []
        assert state_manager.state.last_summarized_at_index == 4

    def test_skips_whitespace(self, state_manager):
        _append_entries(state_manager, n=10)
        applied = apply_summary(
            state_manager.state,
            "    \n\t",
            advance_summarized_index_to=2,
        )
        assert applied is False

    def test_dedup_on_trivial_duplicate(self, state_manager):
        text = (
            "Resumo legítimo de mais de cinquenta caracteres para passar do gate "
            "e ainda exercer a função de deduplicação."
        )
        state_manager.state.summary_history.append(text)
        applied = apply_summary(
            state_manager.state,
            text,
            advance_summarized_index_to=0,
        )
        assert applied is False
        assert len(state_manager.state.summary_history) == 1  # no duplicate


# ============================================================================
# Summarizer end-to-end
# ============================================================================


class TestNarrativeSummarizer:
    def test_short_log_returns_no_call(self, state_manager):
        _append_entries(state_manager, n=3)
        provider = _ScriptedUsageProvider()
        s = NarrativeSummarizer(provider=provider)
        text, usage = s.summarize(state_manager.state)
        assert text is None
        assert usage is None
        assert provider.calls == []

    def test_summarize_calls_llm_with_prompt(self, state_manager):
        _append_entries(state_manager, n=15)
        provider = _ScriptedUsageProvider(
            scripted=[
                (
                    "Resumo pt-BR detalhado dos eventos: a party atravessou "
                    "a floresta, derrotou goblins e descobriu uma caverna.",
                    UsageReport(prompt_tokens=200, completion_tokens=80),
                )
            ]
        )
        s = NarrativeSummarizer(provider=provider)
        text, usage = s.summarize(state_manager.state)
        assert text is not None
        assert "Resumo" in text
        assert usage is not None
        assert usage.prompt_tokens == 200
        assert provider.calls  # LLM was called

    def test_summarize_prompt_excludes_last_n(self, state_manager):
        _append_entries(state_manager, n=10, content="OLD TEXT")
        _append_entries(state_manager, n=3, content="NEW TEXT LAST 6")
        provider = _ScriptedUsageProvider(scripted=["Resumo qualquer para passar do gate."])
        s = NarrativeSummarizer(provider=provider, keep_last_n=6)
        s.summarize(state_manager.state)
        # The system prompt should contain the OLD entries but NOT the NEW ones.
        sys_msg = provider.calls[0][0]
        assert "OLD TEXT" in sys_msg.content
        assert "NEW TEXT LAST 6" not in sys_msg.content

    def test_summarize_includes_previous_summary(self, state_manager):
        _append_entries(state_manager, n=15)
        state_manager.state.summary_history.append(
            "Resumo prévio com mais de cinquenta caracteres para ser incluído."
        )
        provider = _ScriptedUsageProvider(
            scripted=["Resumo novo qualquer para passar do gate."]
        )
        s = NarrativeSummarizer(provider=provider)
        s.summarize(state_manager.state)
        sys_msg = provider.calls[0][0]
        assert (
            "Resumo prévio com mais de cinquenta caracteres para ser incluído."
            in sys_msg.content
        )

    def test_summarize_rejects_short_response(self, state_manager):
        _append_entries(state_manager, n=15)
        provider = _ScriptedUsageProvider(scripted=["curto demais."])
        s = NarrativeSummarizer(provider=provider)
        text, usage = s.summarize(state_manager.state)
        assert text is None
        assert usage is not None  # usage still propagated

    def test_summarize_rejects_sentinel(self, state_manager):
        _append_entries(state_manager, n=15)
        provider = _ScriptedUsageProvider(scripted=[NO_SUMMARY_SENTINEL])
        s = NarrativeSummarizer(provider=provider)
        text, usage = s.summarize(state_manager.state)
        assert text is None
        assert usage is not None


# ============================================================================
# End-of-turn helper: summarize_once
# ============================================================================


class TestSummarizeOnce:
    def test_no_summarizer_is_noop(self, state_manager):
        _append_entries(state_manager, n=25)
        usage = summarize_once(state_manager, None)
        assert usage is None
        assert state_manager.state.summary_history == []

    def test_disabled_is_noop(self, state_manager):
        _append_entries(state_manager, n=25)
        state_manager.state.summary_enabled = False
        provider = _ScriptedUsageProvider()
        s = NarrativeSummarizer(provider=provider)
        usage = summarize_once(state_manager, s)
        assert usage is None
        assert provider.calls == []

    def test_below_threshold_is_noop(self, state_manager):
        _append_entries(state_manager, n=5)
        provider = _ScriptedUsageProvider()
        s = NarrativeSummarizer(provider=provider)
        usage = summarize_once(state_manager, s)
        assert usage is None
        assert provider.calls == []

    def test_provider_exception_advances_attempt_only(self, state_manager):
        _append_entries(state_manager, n=25)
        provider = _ScriptedUsageProvider(
            fail_with=RuntimeError("LLM down"),
        )
        s = NarrativeSummarizer(provider=provider)
        usage = summarize_once(state_manager, s)
        assert usage is None
        # Attempt cursor advanced; summarized cursor did NOT.
        assert state_manager.state.last_summary_attempt_at_index > 0
        assert state_manager.state.last_summarized_at_index == 0
        assert state_manager.state.summary_history == []

    def test_success_appends_and_advances(self, state_manager):
        _append_entries(state_manager, n=25)
        provider = _ScriptedUsageProvider(
            scripted=[
                "Resumo suficientemente longo para passar do gate de cinquenta chars."
            ]
        )
        s = NarrativeSummarizer(provider=provider)
        usage = summarize_once(state_manager, s)
        assert usage is not None
        assert len(state_manager.state.summary_history) == 1
        assert state_manager.state.last_summarized_at_index > 0
        assert state_manager.state.last_summary_attempt_at_index > 0

    def test_parser_rejection_advances_attempt_only(self, state_manager):
        _append_entries(state_manager, n=25)
        provider = _ScriptedUsageProvider(scripted=["x"])
        s = NarrativeSummarizer(provider=provider)
        usage = summarize_once(state_manager, s)
        assert usage is not None  # usage still returned
        assert state_manager.state.summary_history == []  # nothing applied
        assert state_manager.state.last_summary_attempt_at_index > 0
        assert state_manager.state.last_summarized_at_index == 0


# ============================================================================
# Prompt injection via build_dm_context_block
# ============================================================================


class TestBuildDMContextBlockInjection:
    def test_no_summary_section_when_empty(self, state_manager):
        ctx = build_dm_context_block(state_manager)
        assert "## Resumo de eventos anteriores" not in ctx

    def test_summary_section_appears_when_non_empty(self, state_manager):
        state_manager.state.summary_history.append(
            "Resumo longo o suficiente para ser incluído no contexto do DM."
        )
        ctx = build_dm_context_block(state_manager)
        assert "## Resumo de eventos anteriores" in ctx
        assert (
            "Resumo longo o suficiente para ser incluído no contexto do DM."
            in ctx
        )

    def test_summary_section_appears_only_once(self, state_manager):
        state_manager.state.summary_history.append(
            "Resumo antigo A com mais de cinquenta caracteres para passar."
        )
        state_manager.state.summary_history.append(
            "Resumo recente B com mais de cinquenta caracteres para passar."
        )
        ctx = build_dm_context_block(state_manager)
        # Only the latest entry injected.
        assert ctx.count("## Resumo de eventos anteriores") == 1
        assert "Resumo recente B" in ctx
        # Old entry kept on disk but NOT injected in the prompt.
        assert "Resumo antigo A" not in ctx

    def test_summary_section_before_party(self, state_manager):
        state_manager.state.summary_history.append(
            "Resumo de teste com mais de cinquenta caracteres para passar."
        )
        ctx = build_dm_context_block(state_manager)
        # Verify ordering: "## Resumo" precedes "## Party".
        pos_resumo = ctx.index("## Resumo de eventos anteriores")
        pos_party = ctx.index("## Party")
        assert pos_resumo < pos_party


# ============================================================================
# process_player_action integration
# ============================================================================


class _DMOnlyProvider(_ScriptedUsageProvider):
    """A scripted provider that records calls but is shared between
    DM and summarizer. Tests set multiple scripted responses."""

    def chat_with_usage(self, messages):
        # Use the parent impl for the summarizer path too — they share state.
        return super().chat_with_usage(messages)


class TestProcessPlayerActionHook:
    def test_summarizer_hook_appends_tagged_usage(self, state_manager):
        from datetime import datetime, timezone

        # Inject > threshold entries, set fire-at-5 trigger.
        state_manager.state.summary_every_n_entries = 5
        for i in range(6):
            state_manager.append_narrative(
                NarrativeEntry(
                    timestamp=datetime.now(timezone.utc),
                    role="dm",
                    speaker="DM",
                    content=f"Entrada número {i} que é longa o suficiente para contar.",
                )
            )

        # Build a DMAgent + a summarizer that share the same provider.
        # The provider must answer BOTH the DM's call and the summarizer's call.
        provider = _ScriptedUsageProvider(
            scripted=[
                # First call: DM narration response (DM doesn't actually
                # call the provider when there is already narration in
                # the log; we trigger only the summarizer).
                (
                    "Resumo consolidado das primeiras cinco entradas da campanha.",
                    UsageReport(prompt_tokens=50, completion_tokens=20),
                ),
            ]
        )
        dm = DMAgent(provider=provider, state_manager=state_manager)
        s = NarrativeSummarizer(provider=provider)
        result = process_player_action(
            state_manager,
            "olho para a trilha",
            dm,
            summarizer=s,
        )
        # Summarizer appended its usage tagged as "summarizer".
        kinds = [u.kind for u in result.usages]
        assert "summarizer" in kinds
        # summary_history received a new entry.
        assert len(state_manager.state.summary_history) == 1
        # cursor advanced.
        assert state_manager.state.last_summarized_at_index > 0

    def test_no_summarizer_arg_means_noop(self, state_manager):
        from datetime import datetime, timezone

        state_manager.state.summary_every_n_entries = 3
        for i in range(5):
            state_manager.append_narrative(
                NarrativeEntry(
                    timestamp=datetime.now(timezone.utc),
                    role="dm",
                    speaker="DM",
                    content=f"Entrada {i}",
                )
            )
        provider = _ScriptedUsageProvider(
            scripted=["O jogador observa a floresta em silêncio, atento."]
        )
        dm = DMAgent(provider=provider, state_manager=state_manager)
        result = process_player_action(state_manager, "olho", dm)
        kinds = [u.kind for u in result.usages]
        assert "summarizer" not in kinds
        assert state_manager.state.summary_history == []


# ============================================================================
# Persistence round-trip
# ============================================================================


class TestPersistence:
    def test_summary_history_and_cursors_round_trip(self, tmp_path, state_manager):
        state_manager.state.summary_history = [
            "Resumo antigo um com mais de cinquenta caracteres para passar.",
            "Resumo recente dois com mais de cinquenta caracteres para passar.",
        ]
        state_manager.state.last_summarized_at_index = 42
        state_manager.state.last_summary_attempt_at_index = 50

        save_state(state_manager.state, slug="summ_test", saves_dir=tmp_path)
        loaded = load_state("summ_test", saves_dir=tmp_path)

        assert loaded.summary_history == state_manager.state.summary_history
        assert loaded.last_summarized_at_index == 42
        assert loaded.last_summary_attempt_at_index == 50


# ============================================================================
# CLI meta-command: /summary
# ============================================================================


class TestCLISummaryCommand:
    def _build_app(self):
        from auto_dm.cli.app import make_game_app

        state = _make_state()
        return make_game_app(
            state=state,
            provider_factory=lambda: _ScriptedUsageProvider(scripted=[""]),
        )

    def test_summary_status_prints_config(self):
        from auto_dm.cli.app import make_game_app

        app = make_game_app(
            state=_make_state(),
            provider_factory=lambda: _ScriptedUsageProvider(scripted=[""]),
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            app.process_input("/summary")
        out = buf.getvalue()
        assert "enabled" in out
        assert "every_n_entries" in out
        assert "char_threshold" in out

    def test_summary_off_disables(self):
        app = self._build_app()
        buf = io.StringIO()
        with redirect_stdout(buf):
            app.process_input("/summary off")
        assert app.state_manager.state.summary_enabled is False

    def test_summary_on_enables(self):
        app = self._build_app()
        app.state_manager.state.summary_enabled = False
        buf = io.StringIO()
        with redirect_stdout(buf):
            app.process_input("/summary on")
        assert app.state_manager.state.summary_enabled is True

    def test_summary_force_triggers_immediate_summarization(self):
        from datetime import datetime, timezone

        app = self._build_app()
        sm = app.state_manager
        # Lower the entry-count trigger so a short log fires reliably.
        sm.state.summary_every_n_entries = 5
        sm.state.summary_char_threshold = 100_000  # disable char trigger
        # Need > 7 entries to give the summarizer something to condense
        # (with default keep_last_n=6).
        for i in range(10):
            sm.append_narrative(
                NarrativeEntry(
                    timestamp=datetime.now(timezone.utc),
                    role="dm",
                    speaker="DM",
                    content=f"Entrada {i} que seja longa o suficiente para passar.",
                )
            )
        # Rebuild the summarizer with a stub provider that returns a
        # valid (>=50 chars) summary. We replace the whole collaborator
        # rather than just the provider attribute, since Python lets us
        # reuse the existing one cleanly.
        app._summarizer = NarrativeSummarizer(
            provider=_ScriptedUsageProvider(
                scripted=[
                    "Resumo curto mas válido pois tem mais de cinquenta caracteres."
                ]
            )
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            app.process_input("/summary force")
        assert len(sm.state.summary_history) == 1
        assert "Resumo adicionado" in buf.getvalue()

    def test_summary_force_with_short_log_says_nothing(self):
        app = self._build_app()
        buf = io.StringIO()
        with redirect_stdout(buf):
            app.process_input("/summary force")
        assert (
            "narrative_log muito curto" in buf.getvalue()
            or app.state_manager.state.summary_history == []
        )

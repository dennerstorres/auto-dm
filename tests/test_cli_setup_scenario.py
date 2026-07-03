"""Tests for the per-campaign initial scenario CLI prompt.

Drives :func:`auto_dm.cli.setup._prompt_initial_scenario` with scripted
``input_fn`` to verify the helper accepts multi-line input terminated by
a blank line, treats a blank-first-line as "user skipped the scenario"
(returns ""), and tolerates whitespace-only inputs.

End-to-end coverage of the persisted ``initial_scenario`` on a real
``GameState`` lives in ``tests/test_persistence.py`` (the round-trip
test) and ``tests/test_dm_agent.py`` (the agent injection test).
"""
from __future__ import annotations

from typing import Callable

from auto_dm.cli.setup import (
    _prompt_initial_scenario,
    _summarize_scenario,
)


def _silent_print(_s: str) -> None:
    """No-op PrintFn so the test runs without polluting stdout."""


def _scripted_input(answers: list[str]) -> Callable[[str], str]:
    """Return an InputFn that pops from ``answers`` for each call."""
    queue = list(answers)

    def ask(_prompt: str) -> str:
        if not queue:
            raise AssertionError("scripted input ran out of answers")
        return queue.pop(0)

    return ask


# ---------------------------------------------------------------------------
# _prompt_initial_scenario — pure helper
# ---------------------------------------------------------------------------


class TestPromptInitialScenario:
    def test_empty_first_line_means_user_skipped(self):
        """Pressing Enter immediately on the first prompt returns "" —
        the DM then chooses freely, preserving the original behavior."""
        result = _prompt_initial_scenario(
            _scripted_input([""]), _silent_print
        )
        assert result == ""

    def test_single_line_text_returned_as_is(self):
        result = _prompt_initial_scenario(
            _scripted_input(["Cidade flutuante de gnomos.", ""]),
            _silent_print,
        )
        assert result == "Cidade flutuante de gnomos."

    def test_multiline_joined_with_newlines(self):
        """The multi-line input is joined back with '\n' so the player
        can describe the world across paragraphs."""
        result = _prompt_initial_scenario(
            _scripted_input([
                "Linha 1 do cenário.",
                "Linha 2 do cenário.",
                "",
            ]),
            _silent_print,
        )
        assert result == "Linha 1 do cenário.\nLinha 2 do cenário."

    def test_whitespace_only_first_line_returns_empty(self):
        """Just whitespace on the first line is treated as skipped."""
        result = _prompt_initial_scenario(
            _scripted_input(["   ", ""]),
            _silent_print,
        )
        assert result == ""

    def test_whitespace_only_subsequent_line_ends_input(self):
        """A whitespace-only line after content ends input. The trailing
        whitespace is stripped from the joined result."""
        result = _prompt_initial_scenario(
            _scripted_input(["texto real", "   ", ""]),
            _silent_print,
        )
        assert result == "texto real"

    def test_only_whitespace_content_returns_empty(self):
        """If every line is whitespace, the final .strip() turns the
        result into "" — same semantic as skipped."""
        result = _prompt_initial_scenario(
            _scripted_input(["   ", "   "]),
            _silent_print,
        )
        assert result == ""

    def test_eof_after_partial_input_returns_collected(self):
        """If the input stream ends (EOFError) before a blank line,
        the helper still returns whatever was collected so far."""
        def eof_input(_prompt: str) -> str:
            # First call returns content; second call signals EOF.
            if not hasattr(eof_input, "_consumed"):
                eof_input._consumed = True
                return "texto sem linha vazia final"
            raise EOFError

        result = _prompt_initial_scenario(eof_input, _silent_print)
        assert result == "texto sem linha vazia final"


class TestSummarizeScenario:
    """_summarize_scenario formats the scenario for the Resumo panel."""

    def test_empty_returns_nao_definido(self):
        assert _summarize_scenario("") == "(não definido — mestre decide)"

    def test_short_text_returned_as_is(self):
        assert _summarize_scenario("curto") == "curto"

    def test_long_text_truncated_with_ellipsis(self):
        long_text = "x" * 200
        result = _summarize_scenario(long_text, max_len=60)
        assert result.endswith("…")
        assert len(result) == 60

    def test_exact_boundary_not_truncated(self):
        """At max_len exactly, no ellipsis is added (no truncation)."""
        text = "x" * 60
        assert _summarize_scenario(text, max_len=60) == text

    def test_one_over_boundary_truncated(self):
        """One char over max_len triggers the ellipsis."""
        text = "x" * 61
        result = _summarize_scenario(text, max_len=60)
        assert result.endswith("…")
        assert len(result) == 60
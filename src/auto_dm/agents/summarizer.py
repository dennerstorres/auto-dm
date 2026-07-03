"""Periodic narrative summarizer (Phase 33).

Condenses older narrative entries into a pt-BR summary stored in
``GameState.summary_history``. Triggered automatically by the narrative
loop when the log crosses configured thresholds, or manually via the
``/summary force`` CLI command.

Design notes (locked in the Fase 33 plan):

- ``summary_history`` stays a list per SPEC.md:231 — prompt injects only
  the latest entry; older entries are kept on disk for admin inspection.
- Trigger fires at end of ``process_player_action`` (and
  ``run_companion_turn``), never inside ``append_narrative``.
- Cooldown is implicit: ``last_summarized_at_index`` advances on
  success, so subsequent turns in the same combat cycle naturally skip.
- Failure modes (empty / <NO_SUMMARY> / stripped-length < 50 / provider
  exception): ``last_summary_attempt_at_index`` advances, the
  ``last_summarized_at_index`` does NOT, log/``summary_history`` is
  preserved untouched.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from auto_dm.llm.base import Message
from auto_dm.llm.usage import UsageReport, chat_with_usage
from auto_dm.state.manager import StateManager
from auto_dm.state.models import GameState


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SUMMARIZER_SYSTEM_PROMPT: str = (
    "Você é o arquivista da campanha. Sua única tarefa é produzir um RESUMO\n"
    "CONCISO em português (pt-BR) das entradas de diário fornecidas.\n"
    "\n"
    "Regras:\n"
    "1. Preserve localizações, NPCs, decisões-chave e consequências.\n"
    "2. Preserve ganchos narrativos ativos (vilões, mistérios, dívidas).\n"
    "3. Inclua mudanças mecânicas relevantes (HP, condições, recursos).\n"
    "4. NÃO invente personagens, locais ou eventos.\n"
    "5. Preserve incerteza explicitamente (\"possivelmente\", \"não confirmado\").\n"
    "6. Máximo 6 parágrafos. Seja denso, não prolixo.\n"
    "\n"
    "Se as entradas não contiverem informação suficiente para resumir,\n"
    "responda exatamente com <NO_SUMMARY>.\n"
    "\n"
    "Resumo prévio (pode estar vazio):\n"
    "{previous_summary}\n"
    "\n"
    "Entradas a resumir (mais antiga primeiro):\n"
    "{entries}\n"
)


NO_SUMMARY_SENTINEL = "<NO_SUMMARY>"


# Minimum length for a summary to be considered usable. Anything
# shorter is treated as a parser rejection (likely the LLM echoed the
# prompt or produced a fragment).
_MIN_SUMMARY_CHARS = 50


# Cooldown: skip the trigger if we've tried within the last few
# entries. Prevents "stuck provider" loops from burning tokens.
_MIN_ENTRIES_BETWEEN_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# Trigger predicate
# ---------------------------------------------------------------------------


def should_summarize(state: GameState) -> bool:
    """Return True iff a summarization should run on this state."""
    if not state.summary_enabled:
        return False
    log = state.narrative_log
    if not log:
        return False
    # Cooldown after an attempt (success or fail) — avoid retry storms.
    if (len(log) - state.last_summary_attempt_at_index) < _MIN_ENTRIES_BETWEEN_ATTEMPTS:
        return False
    # Entry-count trigger.
    if (len(log) - state.last_summarized_at_index) >= state.summary_every_n_entries:
        return True
    # Char threshold trigger.
    total_chars = sum(len(e.content) for e in log)
    return total_chars >= state.summary_char_threshold


# ---------------------------------------------------------------------------
# Formatting + parsing
# ---------------------------------------------------------------------------


def _format_entries(entries) -> str:
    """Render a slice of NarrativeEntry as pt-BR-friendly one-line bullets."""
    lines = []
    for e in entries:
        lines.append(f"[{e.role}] {e.speaker}: {e.content}")
    return "\n".join(lines)


def _parse_summary(raw: str) -> Optional[str]:
    """Return cleaned summary text, or None if rejection criteria triggered.

    Rejection criteria:
    - empty / whitespace-only
    - contains the explicit ``<NO_SUMMARY>`` sentinel
    - only markdown headers (## ...) with no body
    - cleaned text shorter than ``_MIN_SUMMARY_CHARS`` (50 chars)
    """
    text = (raw or "").strip()
    if not text or NO_SUMMARY_SENTINEL in text:
        return None
    # Strip leading markdown headers, keep the body.
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    text = "\n".join(lines).strip()
    if len(text) < _MIN_SUMMARY_CHARS:
        return None
    return text


# ---------------------------------------------------------------------------
# Main summarizer collaborator
# ---------------------------------------------------------------------------


@dataclass
class NarrativeSummarizer:
    """Compresses older ``narrative_log`` entries into ``summary_history``.

    Construct once at game start with the LLM provider; pass to
    ``process_player_action`` / ``run_companion_turn`` for end-of-turn
    auto-summarization. Also exposed via the ``/summary force`` CLI meta.
    """

    provider: object  # anything implementing chat_with_usage or chat
    keep_last_n: int = 6
    max_output_tokens: int = 600
    min_summary_chars: int = _MIN_SUMMARY_CHARS

    def summarize(
        self, state: GameState,
    ) -> tuple[Optional[str], Optional[UsageReport]]:
        """Condense ``narrative_log[:-keep_last_n]`` into one summary string.

        Returns (summary_text, usage):
        - ``summary_text`` is None if the response was rejected (caller
          should NOT advance ``last_summarized_at_index``).
        - ``usage`` is populated whenever the LLM is called, even on
          rejection.

        Raises whatever the underlying ``chat_with_usage`` raises
        (caller decides whether to log+swallow or surface).
        """
        log = state.narrative_log
        if len(log) <= self.keep_last_n:
            return None, None
        to_summarize = log[: -self.keep_last_n]
        previous = (
            state.summary_history[-1] if state.summary_history else "(nenhum)"
        )
        prompt_text = SUMMARIZER_SYSTEM_PROMPT.format(
            previous_summary=previous,
            entries=_format_entries(to_summarize),
        )
        messages = [
            Message(role="system", content=prompt_text),
            Message(role="user", content="Produza o resumo."),
        ]
        content, usage = chat_with_usage(self.provider, messages)
        cleaned = _parse_summary(content)
        if cleaned is None:
            return None, usage
        return cleaned, usage


# ---------------------------------------------------------------------------
# State mutation helpers
# ---------------------------------------------------------------------------


def apply_summary(
    state: GameState,
    new_text: str,
    *,
    advance_summarized_index_to: int,
) -> bool:
    """Append ``new_text`` to ``state.summary_history`` and advance cursors.

    Returns True on append, False if rejected (empty/whitespace/< 50
    chars / trivial dedup). On True *and* False the cursors advance —
    the summary cursor and attempt cursor keep in lockstep with what we
    meant to do this turn.
    """
    text = (new_text or "").strip()
    if not text or len(text) < _MIN_SUMMARY_CHARS:
        state.last_summarized_at_index = advance_summarized_index_to
        state.last_summary_attempt_at_index = advance_summarized_index_to
        return False
    # Dedup: collapse trivial duplication (avoids bloat on empty scenes).
    if state.summary_history and state.summary_history[-1].strip() == text:
        state.last_summarized_at_index = advance_summarized_index_to
        state.last_summary_attempt_at_index = advance_summarized_index_to
        return False
    state.summary_history.append(text)
    state.last_summarized_at_index = advance_summarized_index_to
    state.last_summary_attempt_at_index = advance_summarized_index_to
    return True


def summarize_once(
    state_manager: StateManager,
    summarizer: Optional[NarrativeSummarizer],
) -> Optional[UsageReport]:
    """End-of-turn helper: check trigger, run summarizer, apply if successful.

    Always advances ``last_summary_attempt_at_index`` (success or
    failure) so a hard-down provider does not retry every turn. Catches
    all exceptions internally and logs them — never raises to the
    caller. Returns the ``UsageReport`` if an LLM call was made, else
    None.
    """
    state = state_manager.state
    if summarizer is None or not should_summarize(state):
        return None
    target_index = len(state.narrative_log) - summarizer.keep_last_n
    try:
        new_text, usage = summarizer.summarize(state)
    except Exception as exc:  # noqa: BLE001 — graceful degradation
        logger.warning("summarize_once: summarizer call failed: %s", exc)
        state.last_summary_attempt_at_index = len(state.narrative_log)
        return None
    if new_text is not None:
        apply_summary(
            state, new_text, advance_summarized_index_to=target_index
        )
    else:
        # Provider returned something but the parser rejected it; still
        # count the attempt so we don't loop on a stubborn LLM.
        state.last_summary_attempt_at_index = len(state.narrative_log)
    return usage

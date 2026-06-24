"""Utility functions for LLM providers."""
from __future__ import annotations

import re

# Matches <think>...</think> blocks (including newlines).
# Models that use extended thinking (e.g. MiniMax-M3 with `thinking: adaptive`)
# wrap their internal reasoning in these tags.
_THINK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_thinking(content: str | None) -> str:
    """Remove ``<think>...</think>`` blocks from LLM output.

    Returns the content with reasoning stripped and surrounding whitespace
    trimmed. Safe to call on None or empty strings.

    The pattern is non-greedy and DOTALL, so multiple thinking blocks are
    removed independently. Anything outside the tags is preserved verbatim.
    """
    if not content:
        return content or ""
    return _THINK_PATTERN.sub("", content).strip()

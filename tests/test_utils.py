"""Tests for LLM utility functions."""
from __future__ import annotations

from auto_dm.llm.utils import strip_thinking


def test_strip_simple():
    assert strip_thinking("<think>reasoning</think>Hello!") == "Hello!"


def test_strip_multiline():
    text = "<think>\nThis is\nmultiline reasoning\n</think>\n\nThe actual response."
    assert strip_thinking(text) == "The actual response."


def test_strip_preserves_text_outside():
    text = "Before <think>hidden</think> after."
    assert strip_thinking(text) == "Before  after."


def test_strip_multiple_blocks():
    text = "<think>first</think>Mid<think>second</think>End"
    assert strip_thinking(text) == "MidEnd"


def test_no_thinking_unchanged():
    text = "Just a normal response with no thinking tags."
    assert strip_thinking(text) == text


def test_empty_inputs():
    assert strip_thinking("") == ""
    assert strip_thinking(None) == ""


def test_strip_handles_unicode():
    text = "<think>raciocínio em português</think>Olá, mundo!"
    assert strip_thinking(text) == "Olá, mundo!"

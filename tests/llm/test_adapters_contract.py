"""Contract tests for the Phase 51a provider adapters (offline).

These exercise the adapter *contract* without touching the network:

- ``chat_with_usage`` returns ``(content, UsageReport(source="api"))`` when
  the provider reports usage, and falls back to the heuristic otherwise.
- ``<think>`` blocks are stripped (MiniMax).
- truncation (``finish_reason == "length"`` / ``stop_reason == "max_tokens"``)
  logs a warning.
- SDK exceptions are normalized to :mod:`auto_dm.llm.errors` subclasses, and
  the normalized message never contains the SDK payload (no secret leakage).
- OpenAI native adapter omits ``temperature`` and uses ``max_completion_tokens``.

SDK responses/errors are faked via monkeypatch, the same pattern as
``tests/test_provider_usage.py``.
"""
from __future__ import annotations

import logging

import httpx
import openai
import pytest

from auto_dm.llm.base import LLMConfig, Message
from auto_dm.llm.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


# ----------------------------------------------------------------------------
# Helpers: fake OpenAI-compatible responses
# ----------------------------------------------------------------------------


def _ns(**kw):
    from types import SimpleNamespace

    return SimpleNamespace(**kw)


def _fake_openai_response(*, content="hello", finish_reason="stop",
                          prompt=10, completion=5, usage_present=True):
    usage = (
        _ns(prompt_tokens=prompt, completion_tokens=completion,
            total_tokens=prompt + completion)
        if usage_present else None
    )
    return _ns(
        choices=[_ns(finish_reason=finish_reason,
                     message=_ns(content=content))],
        usage=usage,
    )


def _compat_provider(cls, *, max_tokens=8192, model=None, api_key="k"):
    cfg_kwargs = {"name": cls.name, "api_key": api_key,
                  "model": model or cls.DEFAULT_MODEL, "max_tokens": max_tokens}
    provider = cls(LLMConfig(**cfg_kwargs))
    return provider


def _wire_create(provider, response):
    """Replace provider.client.chat.completions.create with a callable."""
    provider.client = _ns(chat=_ns(completions=_ns(create=lambda **kw: response)))
    return provider


def _wire_create_raises(provider, factory):
    provider.client = _ns(chat=_ns(completions=_ns(create=factory)))


# ----------------------------------------------------------------------------
# OpenAI-compatible adapters (minimax, openai, gemini, deepseek)
# ----------------------------------------------------------------------------


def _all_compat_classes():
    from auto_dm.llm.deepseek import DeepSeekProvider
    from auto_dm.llm.gemini import GeminiProvider
    from auto_dm.llm.minimax import MinimaxProvider
    from auto_dm.llm.openai_provider import OpenAIProvider

    return [MinimaxProvider, OpenAIProvider, GeminiProvider, DeepSeekProvider]


@pytest.mark.parametrize("cls", _all_compat_classes())
def test_compat_chat_with_usage_reports_api_source(cls):
    provider = _wire_create(
        _compat_provider(cls),
        _fake_openai_response(content="resposta", prompt=12, completion=7),
    )
    content, report = provider.chat_with_usage([Message("user", "x")])
    assert content == "resposta"
    assert report.source == "api"
    assert report.prompt_tokens == 12
    assert report.completion_tokens == 7
    assert report.provider == cls.name


@pytest.mark.parametrize("cls", _all_compat_classes())
def test_compat_falls_back_when_no_usage(cls):
    provider = _wire_create(
        _compat_provider(cls),
        _fake_openai_response(content="sem usage", usage_present=False),
    )
    content, report = provider.chat_with_usage([Message("user", "abcde")])
    assert content == "sem usage"
    assert report.source == "fallback"


@pytest.mark.parametrize("cls", _all_compat_classes())
def test_compat_truncation_warns(cls, caplog):
    provider = _wire_create(
        _compat_provider(cls, max_tokens=8192),
        _fake_openai_response(content="corte", finish_reason="length"),
    )
    with caplog.at_level(logging.WARNING, logger="auto_dm.llm.openai_compatible"):
        content, _ = provider.chat_with_usage([Message("user", "x")])
    assert content == "corte"
    assert "truncated" in caplog.text.lower() or "max_tokens" in caplog.text.lower()


def test_minimax_strips_think_blocks():
    from auto_dm.llm.minimax import MinimaxProvider

    provider = _wire_create(
        _compat_provider(MinimaxProvider),
        _fake_openai_response(content="<think>reasoning here</think>narration"),
    )
    content, _ = provider.chat_with_usage([Message("user", "x")])
    assert "think" not in content
    assert content == "narration"


def test_openai_native_omits_temperature_and_uses_max_completion_tokens():
    from auto_dm.llm.openai_provider import OpenAIProvider

    provider = _compat_provider(OpenAIProvider, max_tokens=1024)
    kwargs = provider._request_kwargs([Message("user", "x")])
    assert "temperature" not in kwargs
    assert kwargs["max_completion_tokens"] == 1024
    assert "max_tokens" not in kwargs


def test_openai_native_omits_cap_when_zero():
    from auto_dm.llm.openai_provider import OpenAIProvider

    provider = _compat_provider(OpenAIProvider, max_tokens=0)
    kwargs = provider._request_kwargs([Message("user", "x")])
    assert "max_completion_tokens" not in kwargs
    assert "max_tokens" not in kwargs


def test_compat_keeps_temperature_for_minimax():
    """Non-OpenAI adapters keep the legacy temperature/max_tokens fields."""
    from auto_dm.llm.minimax import MinimaxProvider

    provider = _compat_provider(MinimaxProvider, max_tokens=512)
    kwargs = provider._request_kwargs([Message("user", "x")])
    assert kwargs["temperature"] == 0.8
    assert kwargs["max_tokens"] == 512


def test_gemini_35_omits_sampling_parameters():
    from auto_dm.llm.gemini import GeminiProvider

    provider = _compat_provider(GeminiProvider, max_tokens=512)
    kwargs = provider._request_kwargs([Message("user", "x")])
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
    assert "top_k" not in kwargs
    assert kwargs["max_tokens"] == 512


def test_deepseek_v4_enables_thinking_explicitly():
    from auto_dm.llm.deepseek import DeepSeekProvider

    provider = _compat_provider(DeepSeekProvider)
    kwargs = provider._request_kwargs([Message("user", "x")])
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}


# ----------------------------------------------------------------------------
# Error normalization (OpenAI-compatible)
# ----------------------------------------------------------------------------


def _openai_auth_error(payload_marker: str) -> openai.AuthenticationError:
    resp = httpx.Response(
        401, text=payload_marker,
        request=httpx.Request("POST", "https://api.example.com/v1/x"),
    )
    return openai.AuthenticationError(payload_marker, response=resp, body=None)


def _openai_rate_limit_error(payload_marker: str) -> openai.RateLimitError:
    resp = httpx.Response(
        429, text=payload_marker,
        request=httpx.Request("POST", "https://api.example.com/v1/x"),
    )
    return openai.RateLimitError(payload_marker, response=resp, body=None)


def _openai_timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=httpx.Request("POST", "https://api.example.com/v1/x"))


def _openai_conn_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(
        message="conn fail", request=httpx.Request("POST", "https://api.example.com/v1/x")
    )


def _openai_500_error(payload_marker: str) -> openai.InternalServerError:
    resp = httpx.Response(
        500, text=payload_marker,
        request=httpx.Request("POST", "https://api.example.com/v1/x"),
    )
    return openai.InternalServerError(payload_marker, response=resp, body=None)


def _assert_no_leak(exc: ProviderError, marker: str):
    assert marker not in str(exc)
    assert marker not in exc.args[0]


def test_openai_error_auth_normalized():
    from auto_dm.llm.minimax import MinimaxProvider

    marker = "LEAKED_AUTH_BODY_xyz"
    provider = _compat_provider(MinimaxProvider)
    _wire_create_raises(provider, lambda **kw: (_ for _ in ()).throw(_openai_auth_error(marker)))
    with pytest.raises(ProviderAuthError) as exc_info:
        provider.chat_with_usage([Message("user", "x")])
    _assert_no_leak(exc_info.value, marker)
    assert exc_info.value.provider == "minimax"


def test_openai_error_rate_limit_normalized():
    from auto_dm.llm.minimax import MinimaxProvider

    marker = "LEAKED_RL_BODY"
    provider = _compat_provider(MinimaxProvider)
    _wire_create_raises(provider, lambda **kw: (_ for _ in ()).throw(_openai_rate_limit_error(marker)))
    with pytest.raises(ProviderRateLimitError):
        provider.chat_with_usage([Message("user", "x")])


def test_openai_error_timeout_normalized():
    from auto_dm.llm.minimax import MinimaxProvider

    provider = _compat_provider(MinimaxProvider)
    _wire_create_raises(provider, lambda **kw: (_ for _ in ()).throw(_openai_timeout_error()))
    with pytest.raises(ProviderTimeoutError):
        provider.chat_with_usage([Message("user", "x")])


def test_openai_error_connection_normalized():
    from auto_dm.llm.minimax import MinimaxProvider

    provider = _compat_provider(MinimaxProvider)
    _wire_create_raises(provider, lambda **kw: (_ for _ in ()).throw(_openai_conn_error()))
    with pytest.raises(ProviderUnavailableError):
        provider.chat_with_usage([Message("user", "x")])


def test_openai_error_500_normalized():
    from auto_dm.llm.minimax import MinimaxProvider

    marker = "LEAKED_500_BODY"
    provider = _compat_provider(MinimaxProvider)
    _wire_create_raises(provider, lambda **kw: (_ for _ in ()).throw(_openai_500_error(marker)))
    with pytest.raises(ProviderUnavailableError) as exc_info:
        provider.chat_with_usage([Message("user", "x")])
    _assert_no_leak(exc_info.value, marker)


# ----------------------------------------------------------------------------
# Anthropic adapter
# ----------------------------------------------------------------------------


def _anthropic_response(*, content_text="resposta", stop_reason="end_turn",
                        input_tokens=11, output_tokens=6):
    block = _ns(type="text", text=content_text)
    usage = _ns(input_tokens=input_tokens, output_tokens=output_tokens)
    return _ns(content=[block], stop_reason=stop_reason, usage=usage)


def _anthropic_provider(*, max_tokens=8192, model="claude-sonnet-5"):
    from auto_dm.llm.anthropic_provider import AnthropicProvider

    return AnthropicProvider(
        LLMConfig(name="anthropic", api_key="k", model=model, max_tokens=max_tokens)
    )


def _wire_anthropic_create(provider, response):
    provider.client = _ns(messages=_ns(create=lambda **kw: response))
    return provider


def test_anthropic_chat_with_usage_reports_api_source():
    provider = _wire_anthropic_create(_anthropic_provider(), _anthropic_response())
    content, report = provider.chat_with_usage([
        Message("system", "you are DM"), Message("user", "olá")
    ])
    assert content == "resposta"
    assert report.source == "api"
    assert report.prompt_tokens == 11
    assert report.completion_tokens == 6
    assert report.provider == "anthropic"


def test_anthropic_collects_system_messages_to_top_level():
    provider = _anthropic_provider()
    seen = {}

    def fake_create(**kw):
        seen.update(kw)
        return _anthropic_response()

    provider.client = _ns(messages=_ns(create=fake_create))
    provider.chat_with_usage([
        Message("system", "rule A"), Message("system", "rule B"),
        Message("user", "go"),
    ])
    assert seen["system"] == "rule A\n\nrule B"
    assert [m["role"] for m in seen["messages"]] == ["user"]


def test_anthropic_falls_back_when_no_usage():
    from types import SimpleNamespace as N

    provider = _wire_anthropic_create(
        _anthropic_provider(),
        N(content=[N(type="text", text="x")], stop_reason="end_turn", usage=None),
    )
    content, report = provider.chat_with_usage([Message("user", "abcde")])
    assert content == "x"
    assert report.source == "fallback"


def test_anthropic_truncation_warns(caplog):
    provider = _wire_anthropic_create(
        _anthropic_provider(),
        _anthropic_response(content_text="corte", stop_reason="max_tokens"),
    )
    with caplog.at_level(logging.WARNING, logger="auto_dm.llm.anthropic_provider"):
        provider.chat_with_usage([Message("user", "x")])
    assert "truncated" in caplog.text.lower() or "max_tokens" in caplog.text.lower()


def test_anthropic_max_tokens_required_and_defaulted_when_uncapped():
    """Claude requires max_tokens; a disabled cap (<=0) falls back to 8192."""
    provider = _anthropic_provider(max_tokens=0)
    kwargs = provider._request_kwargs([Message("user", "x")])
    assert kwargs["max_tokens"] == 8192


def test_anthropic_omits_temperature():
    provider = _anthropic_provider()
    kwargs = provider._request_kwargs([Message("user", "x")])
    assert "temperature" not in kwargs


def _anthropic_auth_error(marker):
    import anthropic

    resp = httpx.Response(
        401, text=marker, request=httpx.Request("POST", "https://api.anthropic.com/v1/x")
    )
    return anthropic.AuthenticationError(marker, response=resp, body=None)


def test_anthropic_error_auth_normalized_no_leak():
    marker = "LEAKED_ANTHROPIC_BODY"
    provider = _anthropic_provider()

    def boom(**kw):
        raise _anthropic_auth_error(marker)

    provider.client = _ns(messages=_ns(create=boom))
    with pytest.raises(ProviderAuthError) as exc_info:
        provider.chat_with_usage([Message("user", "x")])
    _assert_no_leak(exc_info.value, marker)
    assert exc_info.value.provider == "anthropic"

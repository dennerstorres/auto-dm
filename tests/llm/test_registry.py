"""Tests for the central provider registry (Phase 51a)."""
from __future__ import annotations

from datetime import date

import pytest

from auto_dm.llm.registry import (
    PROVIDER_REGISTRY,
    build_provider,
    catalog,
    get_spec,
    list_specs,
)
from auto_dm.llm.anthropic_provider import AnthropicProvider
from auto_dm.llm.deepseek import DeepSeekProvider
from auto_dm.llm.gemini import GeminiProvider
from auto_dm.llm.minimax import MinimaxProvider
from auto_dm.llm.openai_provider import OpenAIProvider
from auto_dm.llm.pricing import get_token_price


EXPECTED_IDS = {"minimax", "openai", "anthropic", "gemini", "deepseek"}
FACTORIES = {
    "minimax": MinimaxProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "deepseek": DeepSeekProvider,
    "anthropic": AnthropicProvider,
}


def test_registry_has_all_five_providers():
    assert set(PROVIDER_REGISTRY) == EXPECTED_IDS


def test_list_specs_matches_registry():
    assert [s.id for s in list_specs()] == list(PROVIDER_REGISTRY.keys())


def test_get_spec_case_insensitive():
    assert get_spec("Minimax").id == "minimax"
    assert get_spec("OPENAI").id == "openai"


def test_get_spec_unknown_raises_ptbr():
    with pytest.raises(ValueError) as exc:
        get_spec("glm")
    msg = str(exc.value)
    assert "glm" in msg
    assert "desconhecido" in msg.lower()


def test_get_spec_unknown_lists_available():
    with pytest.raises(ValueError) as exc:
        get_spec("nope")
    msg = str(exc.value)
    for pid in ("minimax", "openai", "anthropic", "gemini", "deepseek"):
        assert pid in msg


def test_catalog_never_exposes_base_url_or_factory():
    for entry in catalog():
        assert set(entry) == {"id", "label", "models", "default_model"}
        assert "base_url" not in entry
        assert "factory" not in entry


def test_catalog_models_match_spec_allowlist():
    by_id = {e["id"]: e for e in catalog()}
    for pid, spec in PROVIDER_REGISTRY.items():
        assert by_id[pid]["models"] == list(spec.allowed_models)
        assert by_id[pid]["default_model"] == spec.default_model


def test_build_provider_rejects_model_outside_allowlist():
    with pytest.raises(ValueError) as exc:
        build_provider("minimax", api_key="k", model="bogus-model")
    msg = str(exc.value)
    assert "não é permitido" in msg.lower() or "permitido" in msg.lower()
    assert "bogus-model" in msg


def test_build_provider_rejects_unknown_provider():
    with pytest.raises(ValueError):
        build_provider("glm", api_key="k", model="x")


def test_build_provider_uses_default_model_when_none():
    # Building without a model must select the provider's production default.
    provider = build_provider("minimax", api_key="k")
    assert provider.config.model == "MiniMax-M3"


@pytest.mark.parametrize("pid", sorted(EXPECTED_IDS))
def test_build_provider_constructs_correct_adapter(pid):
    spec = get_spec(pid)
    provider = build_provider(
        pid, api_key="k-" + pid, model=spec.default_model
    )
    assert isinstance(provider, FACTORIES[pid])
    assert provider.name == pid


def test_build_provider_never_shares_config_instance():
    """LLMConfig is mutable and MinimaxProvider mutates it; builds must be
    independent so two providers never cross-contaminate."""
    p1 = build_provider("minimax", api_key="k", temperature=0.1)
    p2 = build_provider("minimax", api_key="k", temperature=0.9)
    assert p1.config is not p2.config
    assert p1.config.temperature == 0.1
    assert p2.config.temperature == 0.9


def test_build_provider_passes_timeout_into_extra():
    provider = build_provider("minimax", api_key="k", timeout=5.0)
    assert provider.config.extra.get("timeout") == 5.0


def test_build_provider_sets_base_url_from_spec_for_openai_compat():
    for pid in ("openai", "gemini", "deepseek"):
        provider = build_provider(pid, api_key="k")
        assert provider.config.base_url == get_spec(pid).base_url


def test_validation_model_is_in_allowlist():
    for spec in PROVIDER_REGISTRY.values():
        assert spec.validation_model in spec.allowed_models


def test_every_catalog_model_has_a_standard_price():
    for spec in PROVIDER_REGISTRY.values():
        for model in spec.allowed_models:
            assert get_token_price(spec.id, model) is not None, (spec.id, model)


def test_sonnet_5_intro_price_expires_automatically():
    intro = get_token_price("anthropic", "claude-sonnet-5", as_of=date(2026, 8, 31))
    standard = get_token_price("anthropic", "claude-sonnet-5", as_of=date(2026, 9, 1))
    assert intro is not None and (intro.input_per_million_usd, intro.output_per_million_usd) == (2, 10)
    assert standard is not None
    assert (standard.input_per_million_usd, standard.output_per_million_usd) == (3, 15)


def test_catalog_uses_current_production_defaults():
    assert get_spec("minimax").default_model == "MiniMax-M3"
    assert get_spec("openai").default_model == "gpt-5.4-mini"
    assert get_spec("anthropic").default_model == "claude-sonnet-5"
    assert get_spec("gemini").default_model == "gemini-3.5-flash"
    assert get_spec("deepseek").default_model == "deepseek-v4-flash"


def test_validate_api_key_calls_chat_on_validation_model(monkeypatch):
    """validate_api_key builds a provider and calls chat once with a ping."""
    calls = []

    class _Stub:
        name = "minimax"

        def __init__(self, config):
            self.config = config
            calls.append(("built", config.model, config.max_tokens))

        def chat(self, messages):
            calls.append(("chat", [m.content for m in messages]))
            return "ok"

    monkeypatch.setitem(PROVIDER_REGISTRY, "minimax",
                        type(PROVIDER_REGISTRY["minimax"])(
                            id="minimax", label="x", base_url="u",
                            default_model="MiniMax-M3",
                            allowed_models=("MiniMax-M3",),
                            validation_model="MiniMax-M3",
                            factory=_Stub,
                        ))
    from auto_dm.llm.registry import validate_api_key

    validate_api_key("minimax", "k", timeout=3.0)
    assert calls[0] == ("built", "MiniMax-M3", 8)
    assert calls[1][0] == "chat"
    assert calls[1][1] == ["ping"]

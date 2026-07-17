"""Central provider registry (Phase 51a).

This is the single source of truth for which LLM providers exist, which
models each allows, and how to build an adapter for them. It replaces the
old hard-coded ``if name == "minimax"`` branches in ``factory.py`` and
``web/server.py::_default_provider_factory``.

Design notes:

- Endpoints (``base_url``) are **fixed here, server-side**. The browser
  never sends one and BYOK configs never override it — this is the SSRF
  boundary. (The legacy ``LLMConfig.base_url`` override is retained for
  the global ``AUTO_DM_BASE_URL`` dev/admin escape hatch only.)
- The model catalog (``allowed_models``) is the server-controlled allowlist
  users pick from in the BYOK UI; :func:`build_provider` rejects anything
  outside it.
- Each provider's ``factory`` builds a fresh ``LLMConfig`` per call. The
  config dataclass is mutable and ``MinimaxProvider`` mutates it in
  ``__init__``, so configs must never be shared between providers.
- :func:`catalog` returns a JSON-safe listing for the frontend and never
  exposes ``base_url`` or the factory.
- :func:`validate_api_key` does one cheap call to confirm a BYOK key works
  before storing it; failures surface as normalized provider errors.

The model lists below are best-effort snapshots of current model ids and
**must be re-checked against provider docs before each release** (marked
``# REVIEW`` where most likely to drift).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from auto_dm.llm.base import LLMConfig, Message
from auto_dm.llm.deepseek import DeepSeekProvider
from auto_dm.llm.gemini import GeminiProvider
from auto_dm.llm.minimax import MinimaxProvider
from auto_dm.llm.openai_provider import OpenAIProvider


@dataclass(frozen=True)
class ProviderSpec:
    """Static description of one LLM provider."""

    id: str
    label: str
    base_url: str | None
    default_model: str
    allowed_models: tuple[str, ...]
    #: Cheap model used only by :func:`validate_api_key` for a 1-token ping.
    validation_model: str
    #: ``Callable[[LLMConfig], provider]`` — builds the adapter.
    factory: Callable[[LLMConfig], object]


def _anthropic_factory() -> Callable[[LLMConfig], object]:
    """Wrap AnthropicProvider construction so importing this module (and
    hence the registry) does not require the ``anthropic`` SDK. The import
    only happens when an Anthropic provider is actually built."""

    def _build(config: LLMConfig) -> object:
        from auto_dm.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(config)

    return _build


# Reviewed against the official provider API catalogs on 2026-07-17.
# Re-check before each release: model ids and lifecycle dates drift quickly.
PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    "minimax": ProviderSpec(
        id="minimax",
        label="MiniMax",
        base_url="https://api.minimax.io/v1",
        default_model="MiniMax-M3",
        allowed_models=("MiniMax-M3", "MiniMax-M2.7-highspeed"),
        validation_model="MiniMax-M3",
        factory=MinimaxProvider,
    ),
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-5.4-mini",
        allowed_models=("gpt-5.4-mini", "gpt-5.4", "gpt-5.5"),
        validation_model="gpt-5.4-mini",
        factory=OpenAIProvider,
    ),
    "anthropic": ProviderSpec(
        id="anthropic",
        label="Anthropic Claude",
        base_url=None,  # SDK default; never user-supplied
        default_model="claude-sonnet-5",
        allowed_models=(
            "claude-sonnet-5",
            "claude-haiku-4-5",
            "claude-opus-4-8",
        ),
        validation_model="claude-haiku-4-5",
        factory=_anthropic_factory(),
    ),
    "gemini": ProviderSpec(
        id="gemini",
        label="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        default_model="gemini-3.5-flash",
        allowed_models=("gemini-3.5-flash", "gemini-3.1-flash-lite"),
        validation_model="gemini-3.1-flash-lite",
        factory=GeminiProvider,
    ),
    "deepseek": ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-v4-flash",
        allowed_models=("deepseek-v4-flash", "deepseek-v4-pro"),
        validation_model="deepseek-v4-flash",
        factory=DeepSeekProvider,
    ),
}


def get_spec(provider_id: str) -> ProviderSpec:
    """Return the spec for ``provider_id`` (case-insensitive).

    Raises ``ValueError`` with a pt-BR-friendly message when unknown.
    """
    key = (provider_id or "").strip().lower()
    spec = PROVIDER_REGISTRY.get(key)
    if spec is None:
        known = ", ".join(sorted(PROVIDER_REGISTRY))
        raise ValueError(
            f"Provedor desconhecido: {provider_id!r}. Disponíveis: {known}."
        )
    return spec


def list_specs() -> list[ProviderSpec]:
    """All registered provider specs (stable insertion order)."""
    return list(PROVIDER_REGISTRY.values())


def catalog() -> list[dict]:
    """JSON-safe catalog for the frontend.

    Never includes ``base_url`` or the factory callable.
    """
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "models": list(spec.allowed_models),
            "default_model": spec.default_model,
        }
        for spec in PROVIDER_REGISTRY.values()
    ]


def build_provider(
    provider_id: str,
    *,
    api_key: str,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    thinking: str | None = None,
    timeout: float | None = None,
) -> object:
    """Build a provider adapter for ``provider_id``.

    Validates the model against the provider's allowlist. Always constructs
    a **fresh** ``LLMConfig`` (providers may mutate it). ``base_url`` is set
    from the spec (never caller-supplied here) — the only override path is
    the legacy ``AUTO_DM_BASE_URL`` env var, handled in
    ``LLMConfig.from_env`` + ``_default_provider_factory``.
    """
    spec = get_spec(provider_id)
    chosen_model = (model or spec.default_model).strip()
    if chosen_model not in spec.allowed_models:
        allowed = ", ".join(spec.allowed_models)
        raise ValueError(
            f"Modelo {chosen_model!r} não é permitido para o provedor "
            f"{spec.id!r}. Permitidos: {allowed}."
        )
    config = LLMConfig(
        name=spec.id,
        api_key=api_key,
        model=chosen_model,
        base_url=spec.base_url,
        temperature=0.8 if temperature is None else temperature,
        max_tokens=8192 if max_tokens is None else max_tokens,
        thinking=thinking,
    )
    if timeout is not None:
        config.extra["timeout"] = timeout
    return spec.factory(config)


def validate_api_key(provider_id: str, api_key: str, *, timeout: float = 10.0) -> None:
    """Confirm ``api_key`` works for ``provider_id`` with one minimal call.

    Sends a single short user message with a tiny output cap. Returns
    ``None`` on success; raises a normalized
    :class:`auto_dm.llm.errors.ProviderError` subclass on failure (auth,
    rate-limit, timeout, unavailable). The caller decides how to surface
    those (e.g. mark a BYOK credential ``invalid`` on auth failure).
    """
    spec = get_spec(provider_id)
    provider = build_provider(
        provider_id,
        api_key=api_key,
        model=spec.validation_model,
        max_tokens=8,
        timeout=timeout,
    )
    provider.chat([Message(role="user", content="ping")])  # type: ignore[attr-defined]

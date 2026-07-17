"""Phase 51d-lite — per-user LLM context resolver tests.

Asserts the hard rule from PLAN.md 51d: a user in ``byok`` mode with a
missing / corrupt / undecryptable credential must NEVER silently fall
back to the deploy's global ``AUTO_DM_API_KEY`` — the request has to
fail with :class:`LLMNotConfiguredError` so platform cost never leaks
to the operator without consent.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from auto_dm.web.config import get_settings
from auto_dm.web.crypto import encrypt_credential
from auto_dm.web.db import get_session_factory
from auto_dm.web.llm_context import (
    LLMNotConfiguredError,
    ResolvedLLMContext,
    make_provider_factory,
    resolve_llm_context,
)
from auto_dm.web.models import User, UserLLMSettings, UserProviderCredential


def _fernet_key() -> str:
    return Fernet.generate_key().decode("ascii")


async def _async_make_user(db_factory, *, username="resolveruser") -> User:
    from auto_dm.web.auth import hash_password
    async with db_factory() as s:
        u = User(username=username, password_hash=hash_password("testpass1234"))
        s.add(u)
        await s.commit()
        await s.refresh(u)
        return u


@pytest.fixture(autouse=True)
def _byok_env(monkeypatch):
    """BYOK on + master key set; reset settings cache around the test."""
    monkeypatch.setenv("AUTO_DM_BYOK_ENABLED", "1")
    monkeypatch.setenv("AUTO_DM_CREDENTIALS_KEY", f"1:{_fernet_key()}")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Legacy mode (the migrator-friendly default)
# ---------------------------------------------------------------------------


async def test_resolve_legacy_when_byok_disabled(monkeypatch, app_instance):
    """Flag off + no settings row → legacy context with credential_source='legacy'."""

    monkeypatch.setenv("AUTO_DM_BYOK_ENABLED", "0")
    get_settings.cache_clear()
    factory = get_session_factory()
    user = await _async_make_user(factory)
    async with factory() as s:
        # No settings row. Should still resolve to legacy.
        ctx = await resolve_llm_context(s, user, get_settings())
    assert ctx.credential_source == "legacy"
    assert ctx.signature is None  # fallback factory path → no rebuild signal
    # Resolver picked the wired app.state.provider_factory (the stub).
    provider_factory = make_provider_factory(ctx)
    # Calling the factory should not raise and must return whatever the
    # stub returns (here, an object whose class has nothing).
    provider_factory()


async def test_resolve_legacy_when_no_settings_row(app_instance):
    """BYOK flag on, but the user has no settings row → legacy."""

    factory = get_session_factory()
    user = await _async_make_user(factory)
    async with factory() as s:
        ctx = await resolve_llm_context(s, user, get_settings())
    assert ctx.credential_source == "legacy"


async def test_byok_only_user_never_resolves_global_provider(app_instance):
    """No invite entitlement + no BYOK row must fail, never use global key."""

    factory = get_session_factory()
    user = await _async_make_user(factory, username="publicuser")
    async with factory() as s:
        stored = await s.get(User, user.id)
        stored.system_llm_access = False
        await s.commit()
        with pytest.raises(LLMNotConfiguredError) as exc:
            await resolve_llm_context(s, stored, get_settings())
    assert exc.value.code == "system_llm_forbidden"


async def test_byok_only_user_stays_blocked_when_byok_flag_off(
    monkeypatch, app_instance
):
    """Disabling BYOK must not turn a public account into a global-key user."""

    monkeypatch.setenv("AUTO_DM_BYOK_ENABLED", "0")
    get_settings.cache_clear()
    factory = get_session_factory()
    user = await _async_make_user(factory, username="flagoffuser")
    async with factory() as s:
        stored = await s.get(User, user.id)
        stored.system_llm_access = False
        await s.commit()
        with pytest.raises(LLMNotConfiguredError) as exc:
            await resolve_llm_context(s, stored, get_settings())
    assert exc.value.code == "system_llm_forbidden"


# ---------------------------------------------------------------------------
# BYOK happy path + the no-silent-fallback hard rule
# ---------------------------------------------------------------------------


async def test_resolve_byok_returns_decrypted_key(app_instance):
    """Valid BYOK row → resolver returns the plaintext key + the right provider/model."""

    factory = get_session_factory()
    user = await _async_make_user(factory)
    settings = get_settings()
    # Encrypt a key and persist it.
    ciphertext, key_version = encrypt_credential(settings, "sk-test-key-1234567890")
    async with factory() as s:
        s.add(UserLLMSettings(
            user_id=user.id, mode="byok",
            provider="openai", model="gpt-5.4-mini",
        ))
        s.add(UserProviderCredential(
            user_id=user.id, provider="openai",
            ciphertext=ciphertext, key_version=key_version,
            masked_suffix="7890", validation_status="valid",
        ))
        await s.commit()
        ctx = await resolve_llm_context(s, user, settings)
    assert ctx.credential_source == "byok"
    assert ctx.provider_id == "openai"
    assert ctx.model == "gpt-5.4-mini"
    assert ctx.api_key == "sk-test-key-1234567890"
    assert ctx.signature == ("byok", "openai", "gpt-5.4-mini")


async def test_resolve_byok_raises_when_no_credential(app_instance):
    """BYOK settings row without any credential row → LLMNotConfiguredError.

    Crucial: this NEVER falls back to the global key.
    """
    from auto_dm.llm.registry import build_provider  # sentinel — must NOT be called

    factory = get_session_factory()
    user = await _async_make_user(factory)
    async with factory() as s:
        s.add(UserLLMSettings(
            user_id=user.id, mode="byok",
            provider="openai", model="gpt-5.4-mini",
        ))
        await s.commit()
        # spy / sentinel: build_provider would raise if invoked
        original = build_provider
        from auto_dm.web import llm_context as lctx
        calls = []
        def _spy(*a, **kw):
            calls.append((a, kw))
            return original(*a, **kw)
        lctx.build_provider = _spy  # type: ignore[attr-defined]
        try:
            with pytest.raises(LLMNotConfiguredError) as exc:
                await resolve_llm_context(s, user, get_settings())
        finally:
            lctx.build_provider = original  # type: ignore[attr-defined]
    assert exc.value.code == "no_credential"
    assert "Minha chave" in exc.value.detail
    assert calls == [], (
        "build_provider must NOT be invoked on the BYOK missing-credential path"
    )


async def test_resolve_byok_raises_when_decryption_fails(app_instance, monkeypatch):
    """BYOK row stored but the master key changed → LLMNotConfiguredError.

    Records a credential encrypted under a now-unknown key version.
    """

    factory = get_session_factory()
    user = await _async_make_user(factory)
    async with factory() as s:
        s.add(UserLLMSettings(
            user_id=user.id, mode="byok",
            provider="openai", model="gpt-5.4-mini",
        ))
        s.add(UserProviderCredential(
            user_id=user.id, provider="openai",
            ciphertext="garbage-not-a-fernet-token",
            key_version=999, masked_suffix="xxxx",
            validation_status="unchecked",
        ))
        await s.commit()
        with pytest.raises(LLMNotConfiguredError) as exc:
            await resolve_llm_context(s, user, get_settings())
    # Either CredentialDecryptError or CredentialCryptoError bubbles up
    # translated to ``credential_corrupt``. ``Cryptography`` raises
    # ``InvalidToken`` which we wrap into ``CredentialDecryptError``.
    assert exc.value.code == "credential_corrupt"


async def test_resolve_byok_unknown_provider(app_instance):
    """settings_row points at a provider not in the registry → LLMNotConfiguredError."""

    factory = get_session_factory()
    user = await _async_make_user(factory)
    async with factory() as s:
        s.add(UserLLMSettings(
            user_id=user.id, mode="byok",
            provider="lm-studio-rogue", model="llama-local",
        ))
        await s.commit()
        with pytest.raises(LLMNotConfiguredError) as exc:
            await resolve_llm_context(s, user, get_settings())
    assert exc.value.code == "unknown_provider"


async def test_resolve_byok_disallowed_model(app_instance):
    """settings_row has a model outside the provider's allowlist → LLMNotConfiguredError."""

    factory = get_session_factory()
    user = await _async_make_user(factory)
    async with factory() as s:
        s.add(UserLLMSettings(
            user_id=user.id, mode="byok",
            provider="openai", model="gpt-77-turbo",  # not in allowed
        ))
        await s.commit()
        with pytest.raises(LLMNotConfiguredError) as exc:
            await resolve_llm_context(s, user, get_settings())
    assert exc.value.code == "model_not_allowed"


# ---------------------------------------------------------------------------
# Factory: BYOK path returns a working provider; no plaintext in repr
# ---------------------------------------------------------------------------


async def test_make_provider_factory_builds_provider_when_api_key_set():
    """The non-fallback branch goes through ``build_provider`` and is reusable."""
    ctx = ResolvedLLMContext(
        provider_id="openai", model="gpt-5.4-mini",
        credential_source="byok", api_key="sk-test-key-1234567890",
        signature=("byok", "openai", "gpt-5.4-mini"),
    )
    provider_factory = make_provider_factory(ctx)
    # Calling it twice must produce a fresh provider each time.
    p1 = provider_factory()
    p2 = provider_factory()
    assert p1 is not p2
    # The captured context is reachable without leaking the key via repr.
    captured = provider_factory.__llm_context__
    assert captured is ctx
    assert ctx.api_key not in repr(provider_factory)


async def test_make_provider_factory_fallback_delegates(app_instance):
    """The fallback path delegates to the wired factory."""

    # Force a known wired factory that returns a sentinel.
    sentinel_calls: list[int] = []

    class _P:
        pass

    def _my_factory():
        sentinel_calls.append(1)
        return _P()

    app_instance.state.provider_factory = _my_factory
    import auto_dm.web.server as srv
    srv._state.provider_factory = _my_factory

    factory = get_session_factory()
    user = await _async_make_user(factory)
    async with factory() as s:
        ctx = await resolve_llm_context(s, user, get_settings())
    provider_factory = make_provider_factory(ctx)
    p = provider_factory()
    assert isinstance(p, _P)
    assert len(sentinel_calls) == 1

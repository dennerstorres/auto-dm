"""Per-user LLM context resolver (Phase 51d-lite).

Decides which provider + API key to use for the *current* request based on
the user's :class:`auto_dm.web.models.UserLLMSettings` (mode/provider/
model) and :class:`auto_dm.web.models.UserProviderCredential` (encrypted
BYOK key). The result feeds both the session manager (which needs a
no-arg ``provider_factory`` to rebuild DM/companion agents) and the
usage tracker (which must record which key paid for each call).

Hard rule from PLAN.md 51d: when the user is in ``byok`` mode and the
credential is missing, invalid or its ciphertext can't be decrypted, the
request MUST fail with :class:`LLMNotConfiguredError` instead of silently
falling back to the global ``AUTO_DM_API_KEY`` — a fallback would transfer
platform cost onto the operator without the user knowing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from auto_dm.llm.base import LLMConfig
from auto_dm.web.config import Settings
from auto_dm.web.crypto import (
    CredentialCryptoError,
    CredentialDecryptError,
    decrypt_credential,
    is_crypto_available,
)
from auto_dm.web.models import User, UserLLMSettings, UserProviderCredential

logger = logging.getLogger(__name__)


# Credential source labels persisted on UsageEvent. ``legacy`` covers calls
# paid by the deploy's global ``AUTO_DM_API_KEY``; ``byok`` covers user-supplied
# encrypted keys.
CREDENTIAL_SOURCE_LEGACY = "legacy"
CREDENTIAL_SOURCE_BYOK = "byok"


@dataclass(frozen=True)
class ResolvedLLMContext:
    """Which LLM + which key should service the current request.

    ``signature`` is ``(mode, provider, model)`` — used by
    :class:`auto_dm.web.sessions.SessionManager` to detect when a cached
    session was hydrated under a different provider than the user now
    wants (rebuild the DM/companion agents on change).

    ``api_key`` is the **plaintext** key and must NEVER be logged,
    serialized to JSON, returned in error responses, or stored on any
    persistable object. It lives only inside the closure returned by
    :func:`make_provider_factory`.
    """

    provider_id: str
    model: str
    credential_source: str  # "legacy" | "byok"
    api_key: str
    signature: tuple[str, str, str]  # (mode, provider, model) where mode = credential_source

    def public(self) -> dict[str, Any]:
        """Whitelisted view safe to log or attach to non-sensitive errors."""
        return {
            "credential_source": self.credential_source,
            "provider_id": self.provider_id,
            "model": self.model,
        }


class LLMNotConfiguredError(Exception):
    """Raised when a user's BYOK setting can't be honored for this request.

    Distinct from a 503/502 so route handlers can return **409** (asking
    the user to fix settings) instead of conflating with quota/rate-limit.
    Carries ``code`` (machine-readable) and ``detail`` (pt-BR) for the
    frontend to render a "Abrir Preferências → IA" CTA.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


async def _load_user_settings(
    session: AsyncSession, user_id: int
) -> Optional[UserLLMSettings]:
    return await session.get(UserLLMSettings, user_id)


async def _load_user_credential(
    session: AsyncSession, user_id: int, provider: str
) -> Optional[UserProviderCredential]:
    from sqlalchemy import select

    result = await session.execute(
        select(UserProviderCredential).where(
            UserProviderCredential.user_id == user_id,
            UserProviderCredential.provider == provider,
        )
    )
    return result.scalar_one_or_none()


async def _resolve_legacy(
    settings: Settings,
    *,
    fallback_factory: Optional[Callable[[], Any]] = None,
) -> ResolvedLLMContext:
    """Resolve the deploy's global provider (server env vars).

    When ``fallback_factory`` is supplied (the wired
    ``app.state.provider_factory`` from the running app — also used by
    tests), the resolver reports an *abstract* legacy context: the
    producer of that factory is the authority on which provider/model
    is in use, and the closure in :func:`make_provider_factory` simply
    delegates to it. This preserves the Phase 26 contract that the
    ``provider_factory`` set at ``create_app`` time is what services
    legacy calls — only the BYOK path bypasses it.

    ``signature`` is ``None`` in fallback mode because the resolver has
    no way to detect provider changes from the deploy side: the wired
    factory is the authority, and never needs a rebuild. This matches
    the previous behavior where ``sm.create`` was always called without
    a signature.
    """
    if fallback_factory is not None:
        return ResolvedLLMContext(
            provider_id="minimax",  # placeholder; the real id lives in
            model="_fallback",      # whatever the wired factory produces
            credential_source=CREDENTIAL_SOURCE_LEGACY,
            api_key="",
            signature=None,
        )

    from auto_dm.llm.registry import get_spec

    cfg = LLMConfig.from_env()
    try:
        spec = get_spec(cfg.name)
    except ValueError as exc:
        raise LLMNotConfiguredError(
            code="provider_unavailable",
            detail=(
                "Provedor padrão do servidor indisponível. "
                "Contate o administrador."
            ),
        ) from exc
    api_key = getattr(cfg, "api_key", "") or ""
    if not api_key:
        raise LLMNotConfiguredError(
            code="provider_unavailable",
            detail="Provedor padrão do servidor sem chave configurada.",
        )
    return ResolvedLLMContext(
        provider_id=spec.id,
        model=cfg.model or spec.default_model,
        credential_source=CREDENTIAL_SOURCE_LEGACY,
        api_key=api_key,
        signature=(
            CREDENTIAL_SOURCE_LEGACY, spec.id, cfg.model or spec.default_model,
        ),
    )


async def _resolve_byok(
    session: AsyncSession,
    user_id: int,
    settings_row: UserLLMSettings,
    settings: Settings,
) -> ResolvedLLMContext:
    """Resolve a user's stored BYOK provider + key.

    Any failure (no row, missing crypto, bad cipher, unknown provider)
    raises :class:`LLMNotConfiguredError` rather than falling back to
    the legacy key.
    """
    from auto_dm.llm.registry import get_spec

    provider = (settings_row.provider or "").strip().lower()
    model = (settings_row.model or "").strip()

    try:
        spec = get_spec(provider)
    except ValueError as exc:
        raise LLMNotConfiguredError(
            code="unknown_provider",
            detail=f"Provedor configurado '{provider}' não é reconhecido.",
        ) from exc

    if model not in spec.allowed_models:
        raise LLMNotConfiguredError(
            code="model_not_allowed",
            detail=f"Modelo '{model}' não é permitido para {provider!r}.",
        )

    if not is_crypto_available(settings):
        raise LLMNotConfiguredError(
            code="crypto_unavailable",
            detail="Criptografia de credenciais não configurada no servidor.",
        )

    cred = await _load_user_credential(session, user_id, spec.id)
    if cred is None:
        raise LLMNotConfiguredError(
            code="no_credential",
            detail=(
                "Você está no modo Minha chave, mas nenhuma chave foi "
                "cadastrada para este provedor."
            ),
        )
    try:
        api_key = decrypt_credential(settings, cred.ciphertext, cred.key_version)
    except CredentialDecryptError as exc:
        raise LLMNotConfiguredError(
            code="credential_corrupt",
            detail=(
                "A chave cadastrada não pôde ser lida. Cadastre a chave "
                "novamente em Preferências → IA."
            ),
        ) from exc
    except CredentialCryptoError as exc:
        raise LLMNotConfiguredError(
            code="credential_corrupt",
            detail=str(exc),
        ) from exc

    return ResolvedLLMContext(
        provider_id=spec.id,
        model=model,
        credential_source=CREDENTIAL_SOURCE_BYOK,
        api_key=api_key,
        signature=(CREDENTIAL_SOURCE_BYOK, spec.id, model),
    )


def _get_fallback_factory() -> Optional[Callable[[], Any]]:
    """Return the ``provider_factory`` wired into the running app, if any.

    Used so the legacy resolver can defer to whatever
    :func:`auto_dm.web.server.create_app` was given (production sets
    ``_default_provider_factory``; tests inject a stub). Returns
    ``None`` when no app state is mounted — the resolver will then read
    the ``AUTO_DM_*`` env vars directly. Routed through a helper so the
    import is lazy and the resolver stays free of FastAPI at import
    time.
    """
    try:
        from auto_dm.web.server import get_app_state
    except ImportError:
        return None
    try:
        state = get_app_state()
    except RuntimeError:
        return None
    return getattr(state, "provider_factory", None)


async def resolve_llm_context(
    session: AsyncSession, user: User, settings: Settings,
) -> ResolvedLLMContext:
    """Decide the LLM + key for this request.

    Resolution order:

    1. ``mode == "byok"`` → resolve the stored credential; any failure
       raises :class:`LLMNotConfiguredError` (never falls back).
    2. Otherwise, users with ``system_llm_access`` resolve the global key.
    3. Users without that entitlement must configure BYOK; they never reach
       the global provider, even if BYOK is disabled or no settings row exists.

    Two callers share this function: the session endpoints (which want a
    provider factory to build/rebuild DM/companion agents) and
    ``/api/suggest-names`` (which only needs a one-off chat call).
    """
    row = await _load_user_settings(session, user.id)
    if settings.byok_enabled and row is not None and row.mode == "byok":
        return await _resolve_byok(session, user.id, row, settings)

    if not user.system_llm_access:
        raise LLMNotConfiguredError(
            code="system_llm_forbidden",
            detail=(
                "Esta conta usa somente chave própria. Configure um provedor "
                "e sua chave em Preferências → IA."
            ),
        )

    fallback = _get_fallback_factory()
    if row is None or row.mode != "byok":
        return await _resolve_legacy(settings, fallback_factory=fallback)
    # A BYOK row while the feature is disabled must not accidentally become
    # a global-key call for a BYOK-only account (handled above). Invited users
    # retain the deploy's legacy behavior while the flag is off.
    return await _resolve_legacy(settings, fallback_factory=fallback)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_provider_factory(ctx: ResolvedLLMContext) -> Callable[[], Any]:
    """Build a no-arg ``provider_factory`` matching :class:`SessionManager`.

    The factory produces a fresh provider per call (matching the existing
    contract used by ``SessionManager``) and routes all traffic through
    the registry so the same code-path serves legacy and BYOK. The
    plaintext key never leaves the closure.

    Special case: when ``ctx.api_key == ""`` the context was resolved in
    *fallback* mode (the app's wired ``provider_factory`` is the
    authority). We delegate the no-arg call to that factory and never
    touch the registry — the wired factory is responsible for all
    provider construction. This preserves the Phase 26 contract where
    :func:`auto_dm.web.server.create_app(provider_factory=...)` is the
    source of truth for legacy calls.
    """
    captured = ctx  # freeze in the closure

    if not ctx.api_key:
        # Fallback path: the resolver doesn't know which provider/model
        # the wired factory will produce; defer entirely to it.
        fallback_factory = _get_fallback_factory()
        if fallback_factory is None:
            raise LLMNotConfiguredError(
                code="provider_unavailable",
                detail="Provedor padrão não configurado no servidor.",
            )

        def _delegating_factory() -> Any:
            return fallback_factory()

        _delegating_factory.__llm_context__ = captured  # type: ignore[attr-defined]
        return _delegating_factory

    from auto_dm.llm.registry import build_provider

    provider_id = ctx.provider_id
    model = ctx.model
    api_key = ctx.api_key

    def _factory() -> Any:
        return build_provider(
            provider_id,
            api_key=api_key,
            model=model,
        )

    # Stash the signature + context on the closure so callers (e.g. tests,
    # the admin panel) can introspect what the factory will produce without
    # leaking the key.
    _factory.__llm_context__ = captured  # type: ignore[attr-defined]
    return _factory

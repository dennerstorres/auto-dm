"""LLM provider / BYOK credential routes (Phase 51b).

Six endpoints, all auth-required and scoped to the authenticated user:

- ``GET  /api/llm/catalog``                          — providers + models (no base_url)
- ``GET  /api/me/llm-settings``                      — mode/provider/model + masked creds
- ``PUT  /api/me/llm-settings``                      — choose mode/provider/model
- ``PUT  /api/me/llm-credentials/{provider}``        — store (encrypt) an API key
- ``POST /api/me/llm-credentials/{provider}/validate``— live-test the stored key
- ``DELETE /api/me/llm-credentials/{provider}``      — remove the key

Security invariants enforced here:

- Credentials are encrypted at rest (:mod:`auto_dm.web.crypto`) and the
  plaintext is **never** returned, logged, or echoed in errors.
- Every query is scoped to ``user.id``; cross-user access returns 404 (not
  403) to avoid user/provider enumeration.
- Writing BYOK state requires ``AUTO_DM_BYOK_ENABLED``; crypto unavailable
  → 503.
- The provider/model allowlist comes from :mod:`auto_dm.llm.registry`, so
  the browser can never pick an arbitrary endpoint (SSRF boundary).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

import anyio
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auto_dm.web.activity import log_activity
from auto_dm.web.auth import current_user
from auto_dm.web.config import get_settings
from auto_dm.web.crypto import (
    CredentialCryptoError,
    decrypt_credential,
    encrypt_credential,
    is_crypto_available,
    mask_suffix,
)
from auto_dm.web.db import get_session
from auto_dm.web.models import (
    ActivityType,
    User,
    UserLLMSettings,
    UserProviderCredential,
)
from auto_dm.web.rate_limit import check_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["llm"])

_VALIDATE_TIMEOUT = 10.0
_VALIDATE_RATE_LIMIT = 5  # per minute per user
_KEY_MIN_LEN = 8
_KEY_MAX_LEN = 512


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get("/llm/catalog")
async def get_llm_catalog(
    user: Annotated[User, Depends(current_user)],
) -> dict[str, Any]:
    """Provider/model catalog for the IA preferences tab.

    Never exposes ``base_url`` (SSRF boundary). ``byok_enabled`` tells the
    frontend whether to show the BYOK controls or the "coming soon" panel.
    """
    settings = get_settings()
    from auto_dm.llm.registry import catalog

    return {
        "byok_enabled": settings.byok_enabled,
        "system_llm_access": user.system_llm_access,
        "providers": catalog(),
    }


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _settings_view(
    settings_row: Optional[UserLLMSettings],
    creds: list[UserProviderCredential],
    *,
    system_llm_access: bool,
) -> dict[str, Any]:
    """Build the never-leaks response for GET llm-settings."""
    if settings_row is None:
        mode, provider, model = "legacy", None, None
    else:
        mode = settings_row.mode
        provider = settings_row.provider
        model = settings_row.model
    return {
        "mode": mode,
        "provider": provider,
        "model": model,
        "system_llm_access": system_llm_access,
        "credentials": {
            c.provider: {
                "masked_suffix": c.masked_suffix,
                "validation_status": c.validation_status,
                "validated_at": c.validated_at.isoformat() if c.validated_at else None,
            }
            for c in creds
        },
    }


@router.get("/me/llm-settings")
async def get_llm_settings(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    settings_row = await session.get(UserLLMSettings, user.id)
    creds = (
        (
            await session.execute(
                select(UserProviderCredential).where(
                    UserProviderCredential.user_id == user.id
                )
            )
        ).scalars().all()
    )
    return _settings_view(
        settings_row, list(creds), system_llm_access=user.system_llm_access
    )


class LLMSettingsPatch(BaseModel):
    mode: str = Field(..., description="'byok' or 'legacy'.")
    provider: Optional[str] = None
    model: Optional[str] = None


@router.put("/me/llm-settings")
async def put_llm_settings(
    body: LLMSettingsPatch,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    settings = get_settings()
    mode = (body.mode or "").strip().lower()

    if mode == "legacy":
        if not user.system_llm_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Esta conta não possui acesso à IA do servidor. "
                    "Use uma chave própria (BYOK)."
                ),
            )
        # Switching back to the global provider: drop the row.
        existing = await session.get(UserLLMSettings, user.id)
        if existing is not None:
            await session.delete(existing)
            await session.commit()
        await log_activity(
            session, user_id=user.id, event=ActivityType.LLM_SETTINGS_CHANGED,
            meta={"mode": "legacy"},
        )
        creds = (
            (
                await session.execute(
                    select(UserProviderCredential).where(
                        UserProviderCredential.user_id == user.id
                    )
                )
            ).scalars().all()
        )
        return _settings_view(
            None, list(creds), system_llm_access=user.system_llm_access
        )

    if mode != "byok":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Modo inválido (use 'byok' ou 'legacy').",
        )
    if not settings.byok_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BYOK ainda não está disponível neste servidor.",
        )
    if not body.provider or not body.model:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Informe provedor e modelo para o modo BYOK.",
        )

    from auto_dm.llm.registry import get_spec

    try:
        spec = get_spec(body.provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if body.model not in spec.allowed_models:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Modelo {body.model!r} não é permitido para o provedor "
            f"{spec.id!r}.",
        )

    row = await session.get(UserLLMSettings, user.id)
    if row is None:
        row = UserLLMSettings(user_id=user.id, mode="byok",
                              provider=spec.id, model=body.model)
        session.add(row)
    else:
        row.mode = "byok"
        row.provider = spec.id
        row.model = body.model
    await session.commit()
    await log_activity(
        session, user_id=user.id, event=ActivityType.LLM_SETTINGS_CHANGED,
        meta={"mode": "byok", "provider": spec.id, "model": body.model},
    )
    creds = (
        (
            await session.execute(
                select(UserProviderCredential).where(
                    UserProviderCredential.user_id == user.id
                )
            )
        ).scalars().all()
    )
    return _settings_view(
        row, list(creds), system_llm_access=user.system_llm_access
    )


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def _require_byok(settings) -> None:
    if not settings.byok_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BYOK ainda não está disponível neste servidor.",
        )
    if not is_crypto_available(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Criptografia de credenciais não configurada no servidor.",
        )


async def _get_credential(
    session: AsyncSession, user_id: int, provider: str
) -> Optional[UserProviderCredential]:
    return (
        await session.execute(
            select(UserProviderCredential).where(
                UserProviderCredential.user_id == user_id,
                UserProviderCredential.provider == provider,
            )
        )
    ).scalar_one_or_none()


class CredentialPut(BaseModel):
    api_key: str = Field(..., min_length=_KEY_MIN_LEN, max_length=_KEY_MAX_LEN)


def _cred_view(c: UserProviderCredential) -> dict[str, Any]:
    return {
        "provider": c.provider,
        "masked_suffix": c.masked_suffix,
        "validation_status": c.validation_status,
        "validated_at": c.validated_at.isoformat() if c.validated_at else None,
    }


@router.put("/me/llm-credentials/{provider}")
async def put_credential(
    provider: str,
    body: CredentialPut,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    settings = get_settings()
    _require_byok(settings)

    from auto_dm.llm.registry import get_spec

    try:
        spec = get_spec(provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    api_key = body.api_key.strip()
    if not (_KEY_MIN_LEN <= len(api_key) <= _KEY_MAX_LEN):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Tamanho de chave inválido ({_KEY_MIN_LEN}–{_KEY_MAX_LEN} caracteres).",
        )

    try:
        ciphertext, key_version = encrypt_credential(settings, api_key)
    except CredentialCryptoError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    masked = mask_suffix(api_key)
    existing = await _get_credential(session, user.id, spec.id)
    if existing is None:
        existing = UserProviderCredential(
            user_id=user.id, provider=spec.id, ciphertext=ciphertext,
            key_version=key_version, masked_suffix=masked,
            validation_status="unchecked",
        )
        session.add(existing)
    else:
        existing.ciphertext = ciphertext
        existing.key_version = key_version
        existing.masked_suffix = masked
        existing.validation_status = "unchecked"
        existing.validated_at = None
    await session.commit()
    await log_activity(
        session, user_id=user.id, event=ActivityType.CREDENTIAL_SET,
        meta={"provider": spec.id, "masked_suffix": masked},
    )
    return _cred_view(existing)


@router.post("/me/llm-credentials/{provider}/validate")
async def validate_credential(
    provider: str,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    settings = get_settings()
    _require_byok(settings)

    from auto_dm.llm.registry import get_spec

    try:
        spec = get_spec(provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    cred = await _get_credential(session, user.id, spec.id)
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma chave cadastrada para este provedor.",
        )

    # Rate limit validation calls (each makes a real network request).
    decision = await check_rate_limit(
        scope=f"validate-cred:{user.id}", limit=_VALIDATE_RATE_LIMIT, window_seconds=60,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas validações. Aguarde um minuto e tente de novo.",
            headers={"Retry-After": str(decision.retry_after)},
        )

    try:
        api_key = decrypt_credential(settings, cred.ciphertext, cred.key_version)
    except CredentialCryptoError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    # The SDK call is synchronous + network-bound: run it off the event loop.
    from auto_dm.llm.errors import ProviderAuthError, ProviderError
    from auto_dm.llm.registry import validate_api_key

    try:
        await anyio.to_thread.run_sync(
            lambda: validate_api_key(spec.id, api_key, timeout=_VALIDATE_TIMEOUT)
        )
    except ProviderAuthError:
        cred.validation_status = "invalid"
        cred.validated_at = datetime.now(timezone.utc)
        await session.commit()
        await log_activity(
            session, user_id=user.id, event=ActivityType.CREDENTIAL_VALIDATED,
            meta={"provider": spec.id, "result": "invalid"},
        )
        return {"validation_status": "invalid",
                "detail": "O provedor recusou a chave. Verifique se está correta."}
    except ProviderError:
        # Transient/unavailable — don't flip the status; let the user retry.
        await log_activity(
            session, user_id=user.id, event=ActivityType.CREDENTIAL_VALIDATED,
            meta={"provider": spec.id, "result": "unavailable"},
        )
        return {
            "validation_status": cred.validation_status,
            "detail": "Não foi possível contatar o provedor agora. Tente novamente.",
        }

    cred.validation_status = "valid"
    cred.validated_at = datetime.now(timezone.utc)
    await session.commit()
    await log_activity(
        session, user_id=user.id, event=ActivityType.CREDENTIAL_VALIDATED,
        meta={"provider": spec.id, "result": "valid"},
    )
    return {"validation_status": "valid", "detail": "Chave válida."}


@router.delete("/me/llm-credentials/{provider}")
async def delete_credential(
    provider: str,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    settings = get_settings()
    _require_byok(settings)

    from auto_dm.llm.registry import get_spec

    try:
        spec = get_spec(provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    cred = await _get_credential(session, user.id, spec.id)
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma chave cadastrada para este provedor.",
        )
    await session.delete(cred)
    await session.commit()
    await log_activity(
        session, user_id=user.id, event=ActivityType.CREDENTIAL_REMOVED,
        meta={"provider": spec.id},
    )
    return None  # 204-ish; FastAPI returns 200 with null body

"""At-rest encryption for user BYOK credentials (Phase 51b).

Provider API keys supplied by users (BYOK) are sensitive: unlike a
password, we must be able to recover the plaintext to call the provider
on the user's behalf, so a one-way hash won't do. We encrypt with
authenticated symmetric encryption (Fernet = AES-128-CBC + HMAC-SHA256)
and store only the ciphertext in the database.

The master key lives **outside** the database — in an environment
variable (``AUTO_DM_CREDENTIALS_KEY``). Compromising the DB alone yields
only ciphertext; an attacker needs the deploy's env too. This is the
SSRF/credential-leak boundary for Phase 51.

Key format
----------

The env var holds a comma-separated list of versioned Fernet keys::

    AUTO_DM_CREDENTIALS_KEY="2:<fernet-key-b64>,1:<fernet-key-b64>"

- The **first** entry in the list is the *current* key used to encrypt
  new records. Its version label is stored alongside each ciphertext as
  ``key_version``.
- All entries are tried (in order) when decrypting, so old records
  remain readable after a rotation. To rotate: generate a new Fernet
  key, prepend ``"<next>:<newkey>"``, redeploy, and re-save credentials
  lazily.
- ``<versao>`` is an integer label chosen by the operator (1, 2, 3…).

When the env var is unset, BYOK endpoints return 503 — the application
still boots (deployments that don't enable BYOK don't need the key).
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Last 4 characters of a key are enough for the user to recognize which key
# is stored, without exposing the rest.
_SUFFIX_LEN = 4


class CredentialCryptoError(Exception):
    """Raised when the master key is missing/misconfigured or a ciphertext
    cannot be decrypted. The message is safe to surface (no key material)."""


class CredentialDecryptError(CredentialCryptoError):
    """A stored ciphertext could not be decrypted (wrong key / corrupt)."""


def _parse_key_spec(raw: str) -> list[tuple[int, object]]:
    """Parse ``"2:aaa,1:bbb"`` into ``[(2, Fernet), (1, Fernet)]``.

    Raises :class:`CredentialCryptoError` on malformed input. Returns an
    empty list for an empty/blank ``raw`` (caller treats that as "BYOK
    unavailable").
    """
    from cryptography.fernet import Fernet, InvalidToken  # noqa: F401

    raw = (raw or "").strip()
    if not raw:
        return []
    keys: list[tuple[int, object]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise CredentialCryptoError(
                "AUTO_DM_CREDENTIALS_KEY com formato inválido "
                "(esperado '<versao>:<chave>')."
            )
        ver_str, _, key_str = part.partition(":")
        ver_str = ver_str.strip()
        key_str = key_str.strip()
        try:
            version = int(ver_str)
        except ValueError as exc:
            raise CredentialCryptoError(
                f"Versão de chave não-numérica em AUTO_DM_CREDENTIALS_KEY: {ver_str!r}."
            ) from exc
        try:
            fernet = Fernet(key_str)
        except Exception as exc:  # noqa: BLE001 — Fernet raises several types
            raise CredentialCryptoError(
                "Chave Fernet inválida em AUTO_DM_CREDENTIALS_KEY."
            ) from exc
        keys.append((version, fernet))
    if keys:
        # Reject duplicate version labels — they'd make key_version ambiguous.
        versions = [v for v, _ in keys]
        if len(set(versions)) != len(versions):
            raise CredentialCryptoError(
                "Versões duplicadas em AUTO_DM_CREDENTIALS_KEY."
            )
    return keys


def is_crypto_available(settings) -> bool:
    """True when a valid master key is configured."""
    try:
        return bool(_parse_key_spec(getattr(settings, "credentials_key", None) or ""))
    except CredentialCryptoError as exc:
        logger.error("Credenciais: chave mestra inválida: %s", exc)
        return False


def _fernets(settings) -> list[tuple[int, object]]:
    keys = _parse_key_spec(getattr(settings, "credentials_key", None) or "")
    if not keys:
        raise CredentialCryptoError(
            "Criptografia de credenciais não configurada no servidor."
        )
    return keys


def encrypt_credential(settings, plaintext: str) -> tuple[str, int]:
    """Encrypt ``plaintext`` with the current (first) key.

    Returns ``(ciphertext_b64, key_version)``.
    """
    keys = _fernets(settings)
    version, fernet = keys[0]
    token = fernet.encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii"), version


def decrypt_credential(settings, ciphertext: str, key_version: int) -> str:
    """Decrypt a stored ciphertext.

    Tries the key matching ``key_version`` first, then falls back to the
    others (so a record written before a version-label change still
    decrypts). Raises :class:`CredentialDecryptError` if no key works.
    """
    from cryptography.fernet import InvalidToken

    keys = _fernets(settings)
    # Prefer the recorded version, but try all as a safety net.
    ordered = list(keys)
    ordered.sort(key=lambda kv: 0 if kv[0] == key_version else 1)
    last_exc: Optional[Exception] = None
    for version, fernet in ordered:
        try:
            return fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            last_exc = exc
            continue
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
    raise CredentialDecryptError(
        "Credencial ilegível (chave mestra não decifra o registro). "
        "Cadastre a chave novamente."
    ) from last_exc


def mask_suffix(plaintext: str) -> str:
    """Return a masked suffix like ``"…x9Kf"`` for display.

    Never logs or returns the full key. Short keys show fewer chars.
    """
    if not plaintext:
        return ""
    suffix = plaintext[-_SUFFIX_LEN:]
    return f"…{suffix}"

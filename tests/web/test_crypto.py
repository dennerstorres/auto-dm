"""Tests for at-rest credential encryption (Phase 51b)."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from auto_dm.web.crypto import (
    CredentialCryptoError,
    CredentialDecryptError,
    decrypt_credential,
    encrypt_credential,
    is_crypto_available,
    mask_suffix,
)


class _Settings:
    """Minimal stand-in for Settings — only credentials_key matters here."""

    def __init__(self, credentials_key):
        self.credentials_key = credentials_key


def _fernet_key() -> str:
    return Fernet.generate_key().decode("ascii")


def _settings(*keys):  # keys: list of (version, key_str)
    spec = ",".join(f"{v}:{k}" for v, k in keys)
    return _Settings(spec)


# -- availability / parsing ------------------------------------------------


def test_not_available_when_unset():
    assert not is_crypto_available(_Settings(None))
    assert not is_crypto_available(_Settings(""))


def test_available_with_valid_key():
    assert is_crypto_available(_settings((1, _fernet_key())))


def test_rejects_missing_colon():
    s = _Settings("justastringnoseparator")
    assert not is_crypto_available(s)


def test_rejects_non_numeric_version():
    s = _Settings(f"x:{_fernet_key()}")
    assert not is_crypto_available(s)


def test_rejects_invalid_fernet_key():
    s = _Settings("1:not-a-valid-fernet-key")
    assert not is_crypto_available(s)


def test_rejects_duplicate_versions():
    k = _fernet_key()
    s = _Settings(f"1:{k},1:{k}")
    assert not is_crypto_available(s)


# -- roundtrip -------------------------------------------------------------


def test_encrypt_decrypt_roundtrip():
    s = _settings((1, _fernet_key()))
    ct, version = encrypt_credential(s, "sk-secret-12345")
    assert version == 1
    assert ct != "sk-secret-12345"
    assert decrypt_credential(s, ct, version) == "sk-secret-12345"


def test_encrypt_uses_first_key_version():
    s = _settings((2, _fernet_key()), (1, _fernet_key()))
    _, version = encrypt_credential(s, "k")
    assert version == 2


# -- rotation --------------------------------------------------------------


def test_rotation_decrypts_old_record_with_new_config():
    """A record encrypted under v1 must still decrypt after v2 is prepended."""
    old_key = _fernet_key()
    s_old = _settings((1, old_key))
    ct, version = encrypt_credential(s_old, "sk-rotate-me")
    assert version == 1

    new_key = _fernet_key()
    s_rotated = _settings((2, new_key), (1, old_key))  # v2 now encrypts
    # old ciphertext (v1) still decrypts:
    assert decrypt_credential(s_rotated, ct, 1) == "sk-rotate-me"


def test_rotation_new_records_use_new_version():
    new_key = _fernet_key()
    old_key = _fernet_key()
    s = _settings((2, new_key), (1, old_key))
    ct, version = encrypt_credential(s, "fresh")
    assert version == 2
    assert decrypt_credential(s, ct, version) == "fresh"


# -- decryption fallback / failure ----------------------------------------


def test_decrypt_falls_back_to_other_keys_when_version_mismatch():
    """Even if key_version doesn't match a listed key, we try them all
    (safety net for a relabel)."""
    k1, k2 = _fernet_key(), _fernet_key()
    s = _settings((1, k1))  # only k1 present
    ct, _ = encrypt_credential(s, "data")
    # decrypt claiming a bogus version — still resolves via k1
    s2 = _settings((1, k1), (2, k2))
    assert decrypt_credential(s2, ct, 999) == "data"


def test_decrypt_wrong_key_raises():
    s1 = _settings((1, _fernet_key()))
    ct, version = encrypt_credential(s1, "secret")
    s2 = _settings((1, _fernet_key()))  # different key
    with pytest.raises(CredentialDecryptError):
        decrypt_credential(s2, ct, version)


def test_decrypt_without_config_raises():
    with pytest.raises(CredentialCryptoError):
        decrypt_credential(_Settings(None), "abc", 1)


# -- masking ---------------------------------------------------------------


def test_mask_suffix_last_four():
    assert mask_suffix("sk-abcdefghij1234") == "…1234"


def test_mask_suffix_short_key():
    assert mask_suffix("ab") == "…ab"


def test_mask_suffix_empty():
    assert mask_suffix("") == ""

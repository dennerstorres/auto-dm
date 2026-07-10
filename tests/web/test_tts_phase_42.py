"""Phase 42a — TTS backend tests (no network).

A fake ``edge_tts`` module is injected by monkeypatching
``auto_dm.web.tts._get_edge_tts``. The disk cache is pointed at ``tmp_path`` via
``Settings.tts_cache_dir`` so cache-hit behaviour is exercised for real.
"""
from __future__ import annotations

import pytest

from auto_dm.web import tts as tts_mod


# The dev ``.env`` sets ``INVITE_CODE``, which would make the regular
# ``auth_token`` fixture 403 on signup. Disable the gate locally (same pattern
# as test_companions_endpoint.py / test_xp_endpoints_phase_38.py).
@pytest.fixture(autouse=True)
def _open_signup(monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "")
    from auto_dm.web.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Fake edge_tts machinery
# ---------------------------------------------------------------------------


class _FakeCommunicate:
    """Mimics the edge_tts.Communicate stream payload."""

    def __init__(self, text, voice, rate="+0%"):
        self.text = text
        self.voice = voice
        self.rate = rate

    async def stream(self):
        # Deterministic payload derived from inputs so tests can assert it.
        yield {"type": "WordBoundary", "offset": 0, "duration": 1, "text": "Oi"}
        yield {
            "type": "audio",
            "data": f"AUDIO:{self.voice}:{self.rate}:{self.text[:8]}".encode("utf-8"),
        }


class _FakeEdgeTTS:
    Communicate = _FakeCommunicate

    @staticmethod
    async def list_voices():
        return [
            {"Name": "FranciscaNeural", "ShortName": "pt-BR-FranciscaNeural",
             "Locale": "pt-BR", "Gender": "Female"},
            {"Name": "AntonioNeural", "ShortName": "pt-PT-AntonioNeural",
             "Locale": "pt-PT", "Gender": "Male"},
            {"Name": "JennyNeural", "ShortName": "en-US-JennyNeural",
             "Locale": "en-US", "Gender": "Female"},  # must be filtered out
        ]


class _FailingEdgeTTS:
    @staticmethod
    async def list_voices():
        raise RuntimeError("network down")

    class Communicate:  # noqa: D401 - mimic edge_tts surface
        def __init__(self, *a, **kw):
            pass

        async def stream(self):
            raise RuntimeError("network down")
            yield b""  # pragma: no cover - generator marker


@pytest.fixture
def fake_edge_tts(monkeypatch):
    tts_mod.invalidate_cache()
    monkeypatch.setattr(tts_mod, "_get_edge_tts", lambda: _FakeEdgeTTS())
    return _FakeEdgeTTS


@pytest.fixture
def cache_in_tmp(monkeypatch, tmp_path):
    """Point the TTS disk cache at a per-test tmp dir + clear settings cache."""
    from auto_dm.web.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("TTS_CACHE_DIR", str(tmp_path / "tts_cache"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# /api/tts/voices
# ---------------------------------------------------------------------------


async def test_voices_returns_pt_only(client, auth_token, fake_edge_tts):
    _tok, _user, headers = auth_token
    resp = await client.get("/api/tts/voices", headers=headers)
    assert resp.status_code == 200, resp.text
    locales = {v["Locale"] for v in resp.json()["voices"]}
    assert locales == {"pt-BR", "pt-PT"}  # en-US filtered out


async def test_voices_503_when_unavailable(client, auth_token, monkeypatch):
    _tok, _user, headers = auth_token

    def _raise():
        raise tts_mod.TTSError("no edge-tts")

    tts_mod.invalidate_cache()
    monkeypatch.setattr(tts_mod, "_get_edge_tts", _raise)
    resp = await client.get("/api/tts/voices", headers=headers)
    assert resp.status_code == 503


async def test_voices_401_without_auth(client):
    resp = await client.get("/api/tts/voices")
    assert resp.status_code == 401


async def test_voices_503_on_network_failure(client, auth_token, monkeypatch):
    _tok, _user, headers = auth_token
    tts_mod.invalidate_cache()
    monkeypatch.setattr(tts_mod, "_get_edge_tts", lambda: _FailingEdgeTTS())
    resp = await client.get("/api/tts/voices", headers=headers)
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# /api/tts/speak
# ---------------------------------------------------------------------------


async def test_speak_returns_mp3(client, auth_token, fake_edge_tts, cache_in_tmp):
    _tok, _user, headers = auth_token
    resp = await client.get(
        "/api/tts/speak", params={"text": "Olá mundo"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.content.startswith(b"AUDIO:")


async def test_speak_cache_hit_reads_from_disk(
    client, auth_token, fake_edge_tts, cache_in_tmp
):
    """Second call must hit the disk cache (no re-synth)."""
    _tok, _user, headers = auth_token
    params = {"text": "Mensagem repetida", "voice": "pt-BR-FranciscaNeural"}
    first = await client.get("/api/tts/speak", params=params, headers=headers)
    assert first.status_code == 200

    # Synth again and confirm we read back the *same* cached bytes even when
    # the fake would produce identical output — the proof is the cache file
    # existing on disk under the derived key.
    from auto_dm.web.tts import _cache_key, cache_dir

    key = _cache_key("Mensagem repetida", "pt-BR-FranciscaNeural", "+0%")
    assert (cache_dir() / f"{key}.mp3").exists()

    second = await client.get("/api/tts/speak", params=params, headers=headers)
    assert second.status_code == 200
    assert second.content == first.content


async def test_speak_503_on_network_failure(client, auth_token, monkeypatch, cache_in_tmp):
    _tok, _user, headers = auth_token
    tts_mod.invalidate_cache()
    monkeypatch.setattr(tts_mod, "_get_edge_tts", lambda: _FailingEdgeTTS())
    resp = await client.get(
        "/api/tts/speak", params={"text": "Falha"}, headers=headers
    )
    assert resp.status_code == 503


async def test_speak_422_when_text_too_long(client, auth_token, fake_edge_tts, cache_in_tmp):
    _tok, _user, headers = auth_token
    long = "x" * 2001
    resp = await client.get(
        "/api/tts/speak", params={"text": long}, headers=headers
    )
    assert resp.status_code == 422


async def test_speak_422_when_text_empty(client, auth_token, fake_edge_tts, cache_in_tmp):
    _tok, _user, headers = auth_token
    resp = await client.get("/api/tts/speak", params={"text": "   "}, headers=headers)
    assert resp.status_code == 422


async def test_speak_401_without_auth(client):
    resp = await client.get("/api/tts/speak", params={"text": "Oi"})
    assert resp.status_code == 401


async def test_speak_uses_default_voice_when_omitted(
    client, auth_token, fake_edge_tts, cache_in_tmp
):
    _tok, _user, headers = auth_token
    resp = await client.get(
        "/api/tts/speak", params={"text": "Padrão"}, headers=headers
    )
    assert resp.status_code == 200
    # Default voice is configured as pt-BR-FranciscaNeural.
    assert b"pt-BR-FranciscaNeural" in resp.content

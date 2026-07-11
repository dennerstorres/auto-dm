"""Launch the real FastAPI lifespan over TCP against real PG and Redis."""
from __future__ import annotations

import os
import socket
import threading
import time

import httpx
import pytest
import pytest_asyncio
import uvicorn


pytestmark = pytest.mark.e2e


def pytest_collection_modifyitems(config, items):
    if os.getenv("AUTO_DM_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="set AUTO_DM_E2E=1 and start docker-compose.e2e.yml")
    for item in items:
        if item.path.parent.name == "e2e" and item.path.suffix == ".py":
            item.add_marker(skip)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def api_url():
    if os.getenv("AUTO_DM_E2E") != "1":
        pytest.skip("real-stack E2E is opt-in")

    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+asyncpg://auto_dm:auto_dm_e2e@127.0.0.1:35432/auto_dm_e2e",
    )
    os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:36379/0")
    os.environ.setdefault("JWT_SECRET", "phase43-e2e-secret-at-least-32-characters")
    os.environ.setdefault("FRONTEND_URL", "http://127.0.0.1")
    os.environ["INVITE_CODE"] = "phase43-e2e-invite"
    os.environ["ENVIRONMENT"] = "testing"

    # Imports happen after environment configuration so Settings cannot cache dev values.
    from auto_dm.web.config import get_settings
    from auto_dm.web.server import create_app
    from tests.e2e.fake_dm import fake_provider_factory

    get_settings.cache_clear()
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(provider_factory=fake_provider_factory),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, name="phase43-api", daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/api/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("Phase 43 API did not become healthy")

    yield base_url
    server.should_exit = True
    thread.join(timeout=10)
    assert not thread.is_alive(), "uvicorn did not stop cleanly"


@pytest_asyncio.fixture
async def client(api_url):
    async with httpx.AsyncClient(base_url=api_url, timeout=15) as value:
        yield value

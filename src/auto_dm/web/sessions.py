"""Session manager for active game sessions (Phase 26).

An "active session" is the in-memory + Redis-backed state of a
running game. It owns:

- The :class:`auto_dm.state.manager.StateManager` (which holds the
  Pydantic ``GameState``).
- The :class:`auto_dm.agents.DMAgent` and per-companion agents.
- The optional :class:`auto_dm.engine.combat_engine.CombatEngine`.

Sessions are stored in Redis as a JSON blob (the serialized
``GameState``). Re-hydrating means deserializing + rebuilding the
agents. The DM/companion agents can't be JSON-serialized (they hold
provider references), so we re-instantiate them from a
``provider_factory`` stored at app startup.

Why Redis + not in-memory dict? The user's infra has Redis already
dockerized, and it gives us free TTL + cross-process safety. If the
FastAPI process restarts, sessions are re-hydrated from Redis on
next request.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from auto_dm.agents import DMAgent
from auto_dm.agents.companion import CompanionAgent
from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.state.manager import StateManager
from auto_dm.state.models import GameState
from auto_dm.web.config import get_settings
from auto_dm.web.redis_client import get_redis, session_key

logger = logging.getLogger(__name__)


@dataclass
class WebSession:
    """A running game session tied to one user.

    The ``state_manager`` is the source of truth; ``dm_agent`` and
    ``companion_agents`` are derived from it on hydration. Persistence
    is handled by :class:`SessionManager` (Redis + TTL).
    """

    session_id: str
    user_id: int
    state_manager: StateManager
    dm_agent: DMAgent
    companion_agents: dict[str, CompanionAgent] = field(default_factory=dict)
    combat_engine: Optional[CombatEngine] = None
    provider_factory: Optional[Callable] = None

    @property
    def state(self) -> GameState:
        return self.state_manager.state


class SessionManager:
    """In-process session cache backed by Redis.

    The cache is per-process (dict) and the canonical store is Redis
    (so we survive restarts). ``get_or_load`` returns a cached
    session if present, else re-hydrates from Redis.
    """

    def __init__(self, provider_factory: Callable):
        self._cache: dict[str, WebSession] = {}
        self._provider_factory = provider_factory

    def _cache_key(self, user_id: int, session_id: str) -> str:
        return f"{user_id}:{session_id}"

    async def create(
        self,
        user_id: int,
        state: GameState,
    ) -> WebSession:
        """Create a new session for ``state``, persist to Redis."""
        session_id = uuid.uuid4().hex
        sm = StateManager(state)
        dm = DMAgent(provider=self._provider_factory(), state_manager=sm)
        # Build companion agents for non-player party members.
        comp_agents: dict[str, CompanionAgent] = {}
        for c in state.party:
            if c.is_player:
                continue
            comp_agents[c.id] = CompanionAgent(
                provider=self._provider_factory(),
                character=c,
                state_manager=sm,
            )
        sess = WebSession(
            session_id=session_id,
            user_id=user_id,
            state_manager=sm,
            dm_agent=dm,
            companion_agents=comp_agents,
            combat_engine=CombatEngine(),
            provider_factory=self._provider_factory,
        )
        # Persist to Redis with TTL.
        redis = get_redis()
        settings = get_settings()
        key = session_key(user_id, session_id)
        await redis.setex(
            key, settings.session_ttl_seconds, sm.state.model_dump_json()
        )
        # Cache locally.
        self._cache[self._cache_key(user_id, session_id)] = sess
        return sess

    async def get(self, user_id: int, session_id: str) -> Optional[WebSession]:
        """Get a session, hydrating from Redis if not cached."""
        key = self._cache_key(user_id, session_id)
        if key in self._cache:
            return self._cache[key]
        # Try Redis.
        redis = get_redis()
        rkey = session_key(user_id, session_id)
        data = await redis.get(rkey)
        if not data:
            return None
        state = GameState.model_validate_json(data)
        sm = StateManager(state)
        dm = DMAgent(provider=self._provider_factory(), state_manager=sm)
        comp_agents: dict[str, CompanionAgent] = {}
        for c in state.party:
            if c.is_player:
                continue
            comp_agents[c.id] = CompanionAgent(
                provider=self._provider_factory(),
                character=c,
                state_manager=sm,
            )
        sess = WebSession(
            session_id=session_id,
            user_id=user_id,
            state_manager=sm,
            dm_agent=dm,
            companion_agents=comp_agents,
            combat_engine=CombatEngine(),
            provider_factory=self._provider_factory,
        )
        self._cache[key] = sess
        return sess

    async def save(self, session: WebSession) -> None:
        """Persist the session to Redis with TTL refresh."""
        redis = get_redis()
        settings = get_settings()
        key = session_key(session.user_id, session.session_id)
        await redis.setex(
            key, settings.session_ttl_seconds, session.state.model_dump_json()
        )
        # Refresh local cache.
        self._cache[self._cache_key(session.user_id, session.session_id)] = session

    async def delete(self, user_id: int, session_id: str) -> None:
        """Delete a session from Redis and local cache."""
        redis = get_redis()
        await redis.delete(session_key(user_id, session_id))
        self._cache.pop(self._cache_key(user_id, session_id), None)

    async def list_active(self, user_id: int) -> list[str]:
        """List all active session ids for a user (Redis scan)."""
        redis = get_redis()
        prefix = f"session:{user_id}:"
        out: list[str] = []
        async for key in redis.scan_iter(match=prefix + "*"):
            out.append(key[len(prefix):])
        return out

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


def _signature_matches(
    existing: Optional[tuple[str, str, str]],
    requested: Optional[tuple[str, str, str]],
) -> bool:
    """True when the cache can be reused without rebuilding agents.

    Both empty (legacy call): always match — preserves back-compat for
    callers (and tests) that don't pass a signature. Same value: match.
    Mismatch: caller asked for a different LLM, rebuild required.
    """
    if requested is None:
        return True
    return existing == requested


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
    #: ``(credential_source, provider, model)`` of the LLM used to build
    #: this session. On :meth:`SessionManager.get`, a different signature
    #: triggers a fresh hydration with the new factory so a provider
    #: change (e.g. user flips on BYOK mid-game) takes effect on the next
    #: call without waiting for the Redis TTL.
    provider_signature: Optional[tuple[str, str, str]] = None

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
        *,
        provider_factory: Optional[Callable] = None,
        provider_signature: Optional[tuple[str, str, str]] = None,
    ) -> WebSession:
        """Create a new session for ``state``, persist to Redis.

        ``provider_factory`` (keyword-only) overrides the SessionManager's
        default for this call — used by BYOK to bind the session to the
        user's resolved LLM. ``provider_signature`` is stored alongside
        so the next :meth:`get` can detect a provider change and rebuild.
        """
        factory = provider_factory or self._provider_factory
        session_id = uuid.uuid4().hex
        sm = StateManager(state)
        dm = DMAgent(provider=factory(), state_manager=sm)
        # Build companion agents for non-player party members.
        comp_agents: dict[str, CompanionAgent] = {}
        for c in state.party:
            if c.is_player:
                continue
            comp_agents[c.id] = CompanionAgent(
                provider=factory(),
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
            provider_factory=factory,
            provider_signature=provider_signature,
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

    async def get(
        self,
        user_id: int,
        session_id: str,
        *,
        provider_factory: Optional[Callable] = None,
        provider_signature: Optional[tuple[str, str, str]] = None,
    ) -> Optional[WebSession]:
        """Get a session, hydrating from Redis if not cached.

        When ``provider_factory`` is supplied and the cached/loaded
        session was built under a different ``provider_signature``, the
        session is rebuilt with the new factory so DM + companion agents
        use the user's currently-resolved LLM. Pass ``provider_signature``
        together with ``provider_factory`` — the signature is what makes
        the rebuild decision; omitting both keeps the legacy behavior.
        """
        key = self._cache_key(user_id, session_id)
        cached = self._cache.get(key)
        if cached is not None:
            if _signature_matches(cached.provider_signature, provider_signature):
                return cached
            # Signature drift on the live cache: rebuild and replace.
            rebuilt = await self._rebuild_agents(
                cached, provider_factory, provider_signature,
            )
            self._cache[key] = rebuilt
            return rebuilt
        # Try Redis.
        redis = get_redis()
        rkey = session_key(user_id, session_id)
        data = await redis.get(rkey)
        if not data:
            return None
        state = GameState.model_validate_json(data)
        factory = provider_factory or self._provider_factory
        sm = StateManager(state)
        dm = DMAgent(provider=factory(), state_manager=sm)
        comp_agents: dict[str, CompanionAgent] = {}
        for c in state.party:
            if c.is_player:
                continue
            comp_agents[c.id] = CompanionAgent(
                provider=factory(),
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
            provider_factory=factory,
            provider_signature=provider_signature,
        )
        self._cache[key] = sess
        return sess

    async def _rebuild_agents(
        self,
        existing: "WebSession",
        factory: Optional[Callable],
        signature: Optional[tuple[str, str, str]],
    ) -> "WebSession":
        """Swap in a new provider for an already-hydrated session.

        Used when the user changes their LLM mid-game (e.g. flips BYOK
        on, switches provider): the state is intact, only the agent
        providers need to be replaced. Returns a *new* ``WebSession``
        with fresh agents; the caller updates the cache.
        """
        use_factory = factory or existing.provider_factory or self._provider_factory
        new_dm = DMAgent(provider=use_factory(), state_manager=existing.state_manager)
        new_companions: dict[str, CompanionAgent] = {}
        for c in existing.state.party:
            if c.is_player:
                continue
            new_companions[c.id] = CompanionAgent(
                provider=use_factory(),
                character=c,
                state_manager=existing.state_manager,
            )
        return WebSession(
            session_id=existing.session_id,
            user_id=existing.user_id,
            state_manager=existing.state_manager,
            dm_agent=new_dm,
            companion_agents=new_companions,
            combat_engine=existing.combat_engine or CombatEngine(),
            provider_factory=use_factory,
            provider_signature=signature,
        )

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

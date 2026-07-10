"""Deterministic, zero-network LLM used by the real-stack E2E suite."""
from __future__ import annotations

import json

from auto_dm.llm.base import LLMConfig


def _action(action_type: str, *, actor: str = "pc1", target: str | None = None, **params) -> str:
    body = {"actor_id": actor, "action_type": action_type, "params": params}
    if target is not None:
        body["target_id"] = target
    return "A resposta do mundo e deterministica.\n```action\n" + json.dumps(body) + "\n```"


class FakeDMProvider:
    """TAG-driven provider; production parsing and engine dispatch stay real."""

    name = "e2e-fake"
    config = LLMConfig(name="e2e-fake", api_key="unused", model="deterministic")

    def chat(self, messages) -> str:
        prompt = messages[-1].content
        if "[E2E_ATTACK]" in prompt:
            return _action("attack", target="goblin_e2e")
        if "[E2E_TRAVEL]" in prompt:
            return _action("move", destination="Estrada Real", travel_hours=72, biome="road")
        if "abertura da campanha" in prompt.lower() or "opening" in prompt.lower():
            return _action("move", actor="e2e", destination="Porto Cinzento")
        if "[E2E_TURN]" in prompt:
            return _action("say", actor="e2e", dialogue="Seguimos em frente.")
        # Mechanical follow-ups and companion prompts need narration only.
        return "O resultado mecanico e narrado sem alterar o estado."

    def stream(self, messages):
        yield self.chat(messages)

    def count_tokens(self, messages) -> int:
        return sum(len(message.content) for message in messages)


def fake_provider_factory() -> FakeDMProvider:
    return FakeDMProvider()

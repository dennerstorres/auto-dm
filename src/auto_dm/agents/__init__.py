"""AI agents: DM agent (narrator) and companion agents (helpers).

The DM agent is the LLM-driven narrator. It receives game state +
player input, and returns:
- Narration (free text, pt-BR, immersive)
- Optional structured Action JSON for the engine to execute

Companion agents (Phase 8) share the same provider abstraction but use
a different system prompt: they speak AS their character, return a
first-person intent plus an Action for the engine.

The engine remains authoritative for ALL mechanical results
(rolls, damage, saves). The DM never invents numbers — it narrates
results the engine produced.
"""
from auto_dm.agents.prompts import (
    COMPANION_SYSTEM_PROMPT,
    DM_SYSTEM_PROMPT,
    build_companion_identity_block,
    build_dm_context_block,
    get_action_json_schema_description,
)
from auto_dm.agents.companion import (
    CompanionAgent,
    CompanionDecision,
    parse_companion_response,
)
from auto_dm.agents.companion_turn import (
    CompanionTurnResult,
    build_companion_agents,
    run_companion_turn,
)
from auto_dm.agents.dm import DMAgent, DMResponse, parse_dm_response
from auto_dm.agents.narrative import generate_opening, process_player_action, NarrativeResult
from auto_dm.agents.summarizer import (
    NarrativeSummarizer,
    apply_summary,
    should_summarize,
    summarize_once,
)

__all__ = [
    # Prompts
    "DM_SYSTEM_PROMPT",
    "COMPANION_SYSTEM_PROMPT",
    "build_dm_context_block",
    "build_companion_identity_block",
    "get_action_json_schema_description",
    # DM
    "DMAgent",
    "DMResponse",
    "parse_dm_response",
    # Companion
    "CompanionAgent",
    "CompanionDecision",
    "parse_companion_response",
    "CompanionTurnResult",
    "build_companion_agents",
    "run_companion_turn",
    # Loop
    "process_player_action",
    "generate_opening",
    "NarrativeResult",
    # Phase 33 — periodic summarizer
    "NarrativeSummarizer",
    "should_summarize",
    "apply_summary",
    "summarize_once",
]

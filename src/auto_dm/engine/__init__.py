"""Rules engine: dice, combat, conditions, actions, spells.

All mechanical resolution of the game happens here. The engine is pure
Python — no LLM calls. The DM and companion agents propose actions;
the engine validates and executes.

Submodules:
    - dice:           notation parsing, d20 rolls, stat rolling
    - combat:         attack rolls, damage, initiative, saving throws
    - combat_engine:  high-level orchestrator (turns, Action dispatch)
    - conditions:     the 13 PHB conditions and their mechanical effects
    - actions:        dispatch an Action to the right engine function
    - spell_slots:    casting, concentration, slot management
"""

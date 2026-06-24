"""StateManager: validated mutations on the GameState.

The engine calls these methods to change HP, conditions, initiative, etc.
Direct mutation of `game_state.party[0].hp_current = -5` is forbidden
by convention; the manager guarantees invariants like HP clamped to [0, max].
"""
from __future__ import annotations

from auto_dm.state.models import (
    ActiveEffect,
    Character,
    Condition,
    GameState,
    NPC,
)


class StateManager:
    """Wrapper around GameState that enforces invariants on mutation."""

    def __init__(self, state: GameState) -> None:
        self.state = state

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_character(self, character_id: str) -> Character | None:
        for c in self.state.party:
            if c.id == character_id:
                return c
        return None

    def get_npc(self, npc_id: str) -> NPC | None:
        for n in self.state.npcs:
            if n.id == npc_id:
                return n
        return None

    def get_creature(self, creature_id: str) -> Character | NPC | None:
        """Find by ID in party first, then NPCs."""
        return self.get_character(creature_id) or self.get_npc(creature_id)

    # ------------------------------------------------------------------
    # HP and conditions
    # ------------------------------------------------------------------

    def set_hp(self, target_id: str, delta: int) -> int:
        """Apply a positive (heal) or negative (damage) HP delta.

        Damage first consumes temp HP, then reduces current HP. HP is
        clamped to [0, max]. Returns the new current HP.
        """
        creature = self.get_creature(target_id)
        if creature is None:
            raise KeyError(f"Unknown creature id: {target_id!r}")

        if delta < 0:
            # Damage: consume temp HP first
            damage = -delta
            if creature.temp_hp > 0:
                absorbed = min(creature.temp_hp, damage)
                creature.temp_hp -= absorbed
                damage -= absorbed
            creature.hp_current = max(0, creature.hp_current - damage)
        else:
            # Healing: cannot exceed max HP (temp HP is not restored by healing)
            creature.hp_current = min(creature.hp_max, creature.hp_current + delta)

        # Reset death saves when coming back from 0
        if creature.hp_current > 0 and isinstance(creature, Character):
            creature.death_save_successes = 0
            creature.death_save_failures = 0

        return creature.hp_current

    def add_condition(self, target_id: str, condition: Condition) -> None:
        creature = self.get_creature(target_id)
        if creature is None:
            raise KeyError(f"Unknown creature id: {target_id!r}")
        if condition not in creature.conditions:
            creature.conditions.append(condition)

    def remove_condition(self, target_id: str, condition: Condition) -> None:
        creature = self.get_creature(target_id)
        if creature is None:
            raise KeyError(f"Unknown creature id: {target_id!r}")
        if condition in creature.conditions:
            creature.conditions.remove(condition)

    def clear_conditions(self, target_id: str) -> None:
        creature = self.get_creature(target_id)
        if creature is None:
            raise KeyError(f"Unknown creature id: {target_id!r}")
        creature.conditions = []

    # ------------------------------------------------------------------
    # Active effects (poisons, diseases, traps)
    # ------------------------------------------------------------------

    def add_effect(self, target_id: str, effect: ActiveEffect) -> None:
        creature = self.get_creature(target_id)
        if creature is None:
            raise KeyError(f"Unknown creature id: {target_id!r}")
        creature.active_effects.append(effect)

    def remove_effect(self, target_id: str, source: str) -> None:
        creature = self.get_creature(target_id)
        if creature is None:
            raise KeyError(f"Unknown creature id: {target_id!r}")
        creature.active_effects = [
            e for e in creature.active_effects if e.source != source
        ]

    def clear_effects(self, target_id: str) -> None:
        creature = self.get_creature(target_id)
        if creature is None:
            raise KeyError(f"Unknown creature id: {target_id!r}")
        creature.active_effects = []

    # ------------------------------------------------------------------
    # Combat
    # ------------------------------------------------------------------

    def start_combat(self, initiative_order: list[str]) -> None:
        """Enter combat with the given initiative order (first acts first)."""
        if not initiative_order:
            raise ValueError("initiative_order must not be empty")
        self.state.in_combat = True
        self.state.initiative_order = list(initiative_order)
        self.state.current_turn_index = 0
        self.state.round_number = 1

    def end_combat(self) -> None:
        self.state.in_combat = False
        self.state.initiative_order = []
        self.state.current_turn_index = 0
        self.state.round_number = 0
        # Combat ends -> drop conditions that auto-clear (prone stays, but for
        # MVP we leave it; specific condition cleanup will be in engine)

    def current_actor_id(self) -> str | None:
        if not self.state.in_combat:
            return None
        return self.state.initiative_order[self.state.current_turn_index]

    def next_turn(self) -> str:
        """Advance to the next actor. If we wrap, increment the round.

        Returns the ID of the actor whose turn it is now.
        """
        if not self.state.in_combat:
            raise RuntimeError("Not in combat; call start_combat first.")
        self.state.current_turn_index += 1
        if self.state.current_turn_index >= len(self.state.initiative_order):
            self.state.current_turn_index = 0
            self.state.round_number += 1
        return self.current_actor_id()  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Narrative
    # ------------------------------------------------------------------

    def append_narrative(self, entry) -> None:
        """Append a NarrativeEntry to the log."""
        self.state.narrative_log.append(entry)

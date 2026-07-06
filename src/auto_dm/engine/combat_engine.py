"""CombatEngine: high-level orchestrator for combat encounters.

This module sits on top of the pure combat functions in
``auto_dm.engine.combat`` and the state mutations in
``auto_dm.state.manager``. Its job is to:

1. Start a combat: roll initiative, populate ``state.initiative_order``,
   set round 1.
2. Validate and execute one Action at a time, mutating state via
   ``StateManager`` and returning an :class:`ActionResult`.
3. Detect end-of-combat (all enemies down or all PCs down).
4. Provide ``next_turn()`` for the narrative loop to call after each
   action so the round counter advances.

The engine does NOT narrate. It returns structured results that the
DM agent then turns into prose.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Optional

from auto_dm.engine.combat import (
    attack_roll,
    damage_roll,
    death_save,
    roll_initiative,
)
from auto_dm.engine.conditions import (
    can_take_actions,
    damage_multiplier,
)
from auto_dm.engine.spellcasting import cast_spell, concentration_save
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    Action,
    ActionResult,
    ActionType,
    Character,
    Condition,
    NPC,
    Ability,
)


logger = logging.getLogger(__name__)


# ============================================================================
# Action validation errors
# ============================================================================


class CombatError(Exception):
    """Base class for combat-time errors that should be narrated, not raised."""


class NotInCombatError(CombatError):
    """Action requires combat to be active."""


class NotYourTurnError(CombatError):
    """Action's actor is not the current actor in initiative."""


class UnknownTargetError(CombatError):
    """Action's target_id doesn't resolve to any creature in state."""


class ActorUnconsciousError(CombatError):
    """Action's actor is at 0 HP and not stabilized."""


# ============================================================================
# Encounter bookkeeping
# ============================================================================


@dataclass
class EncounterSummary:
    """A simple post-combat report. Loot/XP are placeholders for Phase 9+."""

    rounds_elapsed: int
    survivors_party: list[str] = field(default_factory=list)
    survivors_enemies: list[str] = field(default_factory=list)
    enemies_defeated: list[str] = field(default_factory=list)
    party_defeated: list[str] = field(default_factory=list)
    loot: list[str] = field(default_factory=list)
    xp_awarded: int = 0
    # Phase 38 — populated by end_combat when combat kills award party
    # XP that crosses one or more thresholds. ``level_up_batch`` is the
    # full LevelUpBatch from award_party_xp; routes_game surfaces it so
    # the frontend can show level-up events and trigger the ASI modal.
    level_up_batch: Optional["LevelUpBatch"] = None  # noqa: F821 (forward ref)


# ============================================================================
# CombatEngine
# ============================================================================


class CombatEngine:
    """Orchestrates a single combat encounter.

    Parameters:
        rng: Source of randomness. Use a seeded ``random.Random`` for
            deterministic tests. ``None`` uses the system RNG.
    """

    def __init__(self, *, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self._summary = EncounterSummary(rounds_elapsed=0)
        # Per-turn attack budgets for the Extra Attack feature. Keyed by
        # actor id. Reset on next_turn. Maps to "how many ATTACK actions
        # the actor can still issue this turn".
        self._attacks_remaining: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Encounter lifecycle
    # ------------------------------------------------------------------

    def start_combat(
        self,
        state_manager: StateManager,
        *,
        extra_combatants: list[Character | NPC] | None = None,
    ) -> list[str]:
        """Begin a combat encounter.

        Rolls initiative for everyone in the party plus the NPCs in state
        (plus any ``extra_combatants`` passed in), then hands the order
        to ``StateManager.start_combat``. Returns the initiative order
        (highest first).
        """
        if state_manager.state.in_combat:
            return state_manager.state.initiative_order

        combatants: list[Character | NPC] = list(state_manager.state.party)
        combatants.extend(state_manager.state.npcs)
        if extra_combatants:
            combatants.extend(extra_combatants)

        if not combatants:
            raise NotInCombatError("No combatants available to start combat")

        result = roll_initiative(combatants, rng=self.rng)
        order = result.order()
        state_manager.start_combat(order)
        # Initialize the first actor's attack budget.
        from auto_dm.engine.extra_attack import attacks_per_action
        first_actor = state_manager.current_actor_id()
        if first_actor:
            actor = state_manager.get_creature(first_actor)
            if actor is not None and isinstance(actor, Character):
                self._attacks_remaining[first_actor] = attacks_per_action(actor)
            else:
                self._attacks_remaining[first_actor] = 1
        return order

    def end_combat(self, state_manager: StateManager) -> EncounterSummary:
        """Wrap up the encounter: build a summary, reset state.

        If combat wasn't active, returns an empty summary.

        Phase 38 — flushes XP from defeated enemies into the party pool
        via :func:`award_party_xp` (engine/progression). The award may
        cross one or more PHB thresholds, in which case every party
        member advances one or more levels immediately. Companion ASIs
        auto-resolve; the player's is queued via ``Character.pending_asi``
        for the frontend modal to consume via
        ``POST /api/sessions/{sid}/resolve-asi``.
        """
        if not state_manager.state.in_combat:
            return EncounterSummary(rounds_elapsed=0)

        # Build summary from current state
        enemies = [n for n in state_manager.state.npcs]
        enemies_alive = [n.id for n in enemies if n.hp_current > 0]
        party_alive = [c.id for c in state_manager.state.party if c.hp_current > 0]
        enemies_defeated = [n.id for n in enemies if n.hp_current <= 0]
        party_defeated = [c.id for c in state_manager.state.party if c.hp_current <= 0]

        # Phase 38 — sum XP across all defeated NPCs (those with
        # ``hp_current <= 0`` whose ``xp`` was set at spawn by
        # monster_to_npc). Award to the party pool. ``award_party_xp``
        # internally walks every party member across any crossed
        # thresholds and returns a LevelUpBatch for narration + UI.
        from auto_dm.engine.progression import award_party_xp

        defeated_npcs = [n for n in enemies if n.hp_current <= 0]
        xp_total = sum((n.xp or 0) for n in defeated_npcs)
        level_up_batch = None
        if xp_total > 0:
            level_up_batch = award_party_xp(
                state_manager.state,
                xp_total,
                source="combat",
                rng=self.rng,
            )

        self._summary = EncounterSummary(
            rounds_elapsed=state_manager.state.round_number,
            survivors_party=party_alive,
            survivors_enemies=enemies_alive,
            enemies_defeated=enemies_defeated,
            party_defeated=party_defeated,
            xp_awarded=xp_total,
            level_up_batch=level_up_batch,
        )
        state_manager.end_combat()
        return self._summary

    def last_summary(self) -> EncounterSummary:
        """Return the last encounter summary (for narration or save logs)."""
        return self._summary

    # ------------------------------------------------------------------
    # Turn progression
    # ------------------------------------------------------------------

    def next_turn(self, state_manager: StateManager) -> Optional[str]:
        """Advance to the next actor in initiative. Returns the new actor id.

        Returns ``None`` if combat has ended (no actors left, or both sides
        wiped out).
        """
        if not state_manager.state.in_combat:
            return None
        if self._combat_should_end(state_manager):
            self.end_combat(state_manager)
            return None
        # Reset attack budget for the new actor
        next_actor = state_manager.current_actor_id()
        if next_actor:
            actor = state_manager.get_creature(next_actor)
            if actor is not None and isinstance(actor, Character):
                from auto_dm.engine.extra_attack import attacks_per_action
                self._attacks_remaining[next_actor] = attacks_per_action(actor)
            else:
                self._attacks_remaining[next_actor] = 1
        new_id = state_manager.next_turn()
        # Phase 41 — refresh the new actor's reaction at the start of its
        # turn (PHB p. 190) and drop the Shield spell's temporary buffs
        # (pending_ac_bonus / Magic Missile immunity) which last "until
        # the start of your next turn".
        if new_id is not None:
            refreshed = state_manager.get_creature(new_id)
            if isinstance(refreshed, Character):
                refreshed.reaction_available = True
                if refreshed.shield_active:
                    refreshed.shield_active = False
                    refreshed.pending_ac_bonus = 0
                # Clear any unanswered pending_reaction now that its window
                # has rolled past (the trigger belonged to a prior turn).
                refreshed.pending_reaction = None
        return new_id

    def current_actor_id(self, state_manager: StateManager) -> Optional[str]:
        if not state_manager.state.in_combat:
            return None
        return state_manager.current_actor_id()

    def next_actor_id(self, state_manager: StateManager) -> Optional[str]:
        """Return the actor id whose turn is *next*, without mutating state.

        Wraps around the initiative order (so the last actor's "next" is
        the first actor of the next round). Returns ``None`` when not in
        combat. Useful for ``GameApp._run_companion_cycle`` to peek at
        who's up without committing to an advance.
        """
        if not state_manager.state.in_combat:
            return None
        order = state_manager.state.initiative_order
        if not order:
            return None
        next_idx = state_manager.state.current_turn_index + 1
        if next_idx >= len(order):
            next_idx = 0
        return order[next_idx]

    def is_player_turn(self, state_manager: StateManager) -> bool:
        """True if the current actor is the player's character."""
        current = self.current_actor_id(state_manager)
        if current is None:
            return False
        return current == state_manager.state.player_character_id

    def is_companion_turn(self, state_manager: StateManager) -> bool:
        """True if the current actor is a non-player party member."""
        current = self.current_actor_id(state_manager)
        if current is None:
            return False
        if current == state_manager.state.player_character_id:
            return False
        return state_manager.get_character(current) is not None

    # ------------------------------------------------------------------
    # Action dispatch
    # ------------------------------------------------------------------

    def execute_action(
        self,
        state_manager: StateManager,
        action: Action,
    ) -> ActionResult:
        """Run one Action through the combat engine.

        The DM's structured Action (attack, dash, dodge, end_combat, etc.)
        is validated against the current state and either rejected with a
        friendly message (returned in ``ActionResult(success=False)``) or
        resolved and applied to the state.
        """
        handler = _ACTION_HANDLERS.get(action.action_type)
        if handler is None:
            return ActionResult(
                success=False,
                message=(
                    f"Ação {action.action_type.value} não é tratada pelo "
                    f"motor de combate (use o motor de movimento/narrativa)."
                ),
                mechanical={"action_type": action.action_type.value},
            )

        try:
            return handler(self, state_manager, action)
        except CombatError as exc:
            return ActionResult(
                success=False,
                message=str(exc),
                mechanical={"action_type": action.action_type.value},
            )
        except Exception as exc:  # noqa: BLE001 — surface for narration
            logger.exception("CombatEngine.execute_action failed")
            return ActionResult(
                success=False,
                message=f"Erro mecânico ao executar {action.action_type.value}: {exc}",
                mechanical={"action_type": action.action_type.value, "error": str(exc)},
            )

    # ------------------------------------------------------------------
    # Helpers used by handlers
    # ------------------------------------------------------------------

    def _validate_combat_turn(
        self,
        state_manager: StateManager,
        action: Action,
        *,
        allow_outside_combat: bool = False,
    ) -> None:
        """Raise CombatError if the action is not legal in the current state."""
        if not state_manager.state.in_combat and not allow_outside_combat:
            raise NotInCombatError("Não estamos em combate.")

        actor = state_manager.get_creature(action.actor_id)
        if actor is None:
            raise UnknownTargetError(f"Ator desconhecido: {action.actor_id!r}")

        # Must be this creature's turn (when in combat)
        if state_manager.state.in_combat:
            current = state_manager.current_actor_id()
            if current != action.actor_id:
                raise NotYourTurnError(
                    f"Não é o turno de {action.actor_id!r} "
                    f"(é o turno de {current!r})."
                )

        # Unconscious (0 HP) actors can only take death-saving actions.
        if actor.hp_current <= 0 and not isinstance(actor, Character):
            raise ActorUnconsciousError("Alvo inconsciente não pode agir.")
        if (
            actor.hp_current <= 0
            and isinstance(actor, Character)
            and action.action_type not in {ActionType.DEATH_SAVE}
        ):
            raise ActorUnconsciousError(
                "Personagem inconsciente: apenas testes de morte são permitidos."
            )

        # Condition-based action gating (PHB)
        # Incapacitated / Paralyzed / Petrified / Stunned / Unconscious
        # can't take actions. Death saves are a special exception handled
        # by the engine (a death save is a save, not an action).
        if (
            not can_take_actions(actor)
            and action.action_type not in {ActionType.DEATH_SAVE, ActionType.SAY}
        ):
            raise ActorUnconsciousError(
                f"{actor.name} está incapacitado e não pode agir."
            )

    def _combat_should_end(self, state_manager: StateManager) -> bool:
        """End combat when one side is fully wiped out (or all fled)."""
        if not state_manager.state.in_combat:
            return False
        party_alive = any(c.hp_current > 0 for c in state_manager.state.party)
        enemies_alive = any(n.hp_current > 0 for n in state_manager.state.npcs)
        return not party_alive or not enemies_alive


# ============================================================================
# Handlers — one per combat action
# ============================================================================


def _handle_attack(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    engine._validate_combat_turn(state_manager, action)

    attacker = state_manager.get_creature(action.actor_id)
    target = state_manager.get_creature(action.target_id or "")
    if target is None:
        raise UnknownTargetError(f"Alvo desconhecido: {action.target_id!r}")
    if target.hp_current <= 0:
        return ActionResult(
            success=False,
            message=f"{target.name} já está fora de combate.",
            mechanical={},
        )

    # Extra Attack: consume one of the actor's attacks for this turn.
    budget = engine._attacks_remaining.get(action.actor_id, 1)
    if budget <= 0:
        return ActionResult(
            success=False,
            message=(
                f"{attacker.name} já usou todos os ataques disponíveis neste turno."
            ),
            mechanical={"attacks_remaining": 0},
        )
    engine._attacks_remaining[action.actor_id] = budget - 1

    # Resolve attack
    atk = attack_roll(attacker, target, rng=engine.rng)  # type: ignore[arg-type]
    if not atk.is_hit:
        return ActionResult(
            success=True,  # action was valid; just missed
            message=(
                f"{attacker.name} ataca {target.name} com {atk.weapon}: "
                f"d20({atk.attack_roll}) + {atk.attack_modifier} = "
                f"{atk.attack_total} vs AC {atk.target_ac} → ERROU"
                + (" (CRÍTICO!)" if atk.is_fumble else "")
            ),
            mechanical={
                "attack_roll": atk.attack_roll,
                "attack_total": atk.attack_total,
                "target_ac": atk.target_ac,
                "is_hit": False,
                "is_crit": atk.is_crit,
                "is_fumble": atk.is_fumble,
                "weapon": atk.weapon,
            },
        )

    # Hit: roll damage and apply
    dmg = damage_roll(attacker, is_crit=atk.is_crit, rng=engine.rng)  # type: ignore[arg-type]

    # Apply resistance / vulnerability / immunity from conditions + race
    mult = damage_multiplier(target, dmg.damage_type)
    # Rage: resistance to bludgeoning/piercing/slashing (PHB p. 48).
    from auto_dm.engine.rage import is_raging, apply_rage_resistance
    if is_raging(target) and apply_rage_resistance(dmg.damage_type):
        # PHB DMG: resistance + vulnerability cancel. Only override to
        # resistance if the target isn't also vulnerable to this type.
        if dmg.damage_type.lower() not in {v.lower() for v in target.vulnerabilities}:
            mult = min(mult, 0.5)
    final_dmg = max(0, int(round(dmg.total * mult)))
    if mult == 0.0:
        damage_note = " (imune — 0 dano)"
    elif mult == 0.5:
        damage_note = " (resistente — dano reduzido à metade)"
    elif mult == 2.0:
        damage_note = " (vulnerável — dano dobrado)"
    else:
        damage_note = ""

    # Sneak Attack (Rogue PHB p. 96)
    sneak_note = ""
    if isinstance(attacker, Character) and atk.is_hit:
        from auto_dm.engine.sneak_attack import can_sneak_attack, roll_sneak_attack
        weapon = attacker.equipped.main_hand
        finesse_or_ranged = False
        if weapon and weapon.weapon:
            wp = weapon.weapon
            finesse_or_ranged = wp.finesse or wp.range_normal is not None
        # ally adjacent: caller can set in params; default False.
        ally_adj = bool(action.params.get("ally_adjacent", False))
        if can_sneak_attack(
            attacker,
            target,
            has_advantage=atk.advantage,
            has_disadvantage=atk.disadvantage,
            ally_adjacent=ally_adj,
            weapon_is_finesse_or_ranged=finesse_or_ranged,
        ):
            sneak_dmg = roll_sneak_attack(attacker, rng=engine.rng)
            sneak_dmg = max(0, int(round(sneak_dmg * mult)))
            if sneak_dmg > 0:
                final_dmg += sneak_dmg
                sneak_note = f" + {sneak_dmg} ataque furtivo"

    # Divine Smite (Paladin PHB p. 85)
    smite_note = ""
    if isinstance(attacker, Character) and atk.is_hit:
        smite_lvl = action.params.get("smite_slot_level")
        if smite_lvl:
            from auto_dm.engine.smite import divine_smite
            sr = divine_smite(attacker, target, int(smite_lvl), rng=engine.rng)
            if sr.success:
                smite_dmg = max(0, int(round(sr.damage * mult)))
                final_dmg += smite_dmg
                smite_note = f" + {smite_dmg} smite ({sr.slot_level_used}º slot)"
            else:
                smite_note = f" [smite falhou: {sr.reason}]"

    new_hp = state_manager.set_hp(target.id, -final_dmg)

    # Concentration check (PHB p. 203): damaged caster makes a CON save.
    conc_broken_note = ""
    if isinstance(target, Character) and final_dmg > 0:
        conc = concentration_save(target, final_dmg, rng=engine.rng)
        if conc.broken:
            conc_broken_note = f" {target.name} perdeu a concentração!"

    # Phase 41 — publish a reaction trigger when a party member is hit.
    # The party member (player or companion) may spend their reaction to
    # Shield / Uncanny Dodge / Parry. ``publish_reaction_trigger`` is a
    # no-op when nobody is eligible, so it's safe to call unconditionally.
    # Damage-reduction reactions resolve as a refund (see engine/reactions).
    # Phase 41c: companions auto-resolve via the heuristic; only the player
    # gets a stashed ``pending_reaction`` + web modal.
    reaction_note = ""
    if (
        atk.is_hit
        and final_dmg > 0
        and isinstance(target, Character)
        and target.reaction_available
    ):
        from auto_dm.engine.actions import OnHitByAttack
        from auto_dm.engine.reactions import publish_reaction_trigger
        import time
        trigger = OnHitByAttack(
            target_id=target.id,
            attacker_id=attacker.id,
            attack_damage=final_dmg,
            damage_type=dmg.damage_type,
            is_melee=bool(getattr(atk, "weapon", None)),
            is_crit=atk.is_crit,
        )
        responders = publish_reaction_trigger(
            state_manager, trigger, fired_at=int(time.time()), engine=engine,
        )
        if responders:
            responder = state_manager.get_character(responders[0])
            if responder is not None and not responder.is_player:
                # Companion used its reaction automatically.
                reaction_note = f" [{responder.name} reagiu]"
            else:
                reaction_note = " [reação disponível]"

    # Phase 41c — if a party member just dropped to 0 HP, publish OnAllyDown
    # so a cleric/druid/bard companion (or the player, if no self-trigger is
    # already pending) can Healing Word them back up out of turn. Re-check HP
    # *after* the hit-reaction above: an Uncanny Dodge refund may have already
    # pulled the target above 0, in which case there's nothing to revive.
    if (
        isinstance(target, Character)
        and target.hp_current == 0
        and any(c.id == target.id for c in state_manager.state.party)
    ):
        from auto_dm.engine.actions import OnAllyDown
        from auto_dm.engine.reactions import publish_reaction_trigger
        import time
        ally_trigger = OnAllyDown(ally_id=target.id)
        ally_responders = publish_reaction_trigger(
            state_manager, ally_trigger, fired_at=int(time.time()),
            engine=engine,
            candidates=[
                c.id for c in state_manager.state.party if c.id != target.id
            ],
        )
        if ally_responders and not reaction_note:
            responder = state_manager.get_character(ally_responders[0])
            if responder is not None and not responder.is_player:
                reaction_note = f" [{responder.name} usou Palavra Curativa]"
            else:
                reaction_note = " [reação disponível]"

    return ActionResult(
        success=True,
        message=(
            f"{attacker.name} ataca {target.name} com {atk.weapon}: "
            f"d20({atk.attack_roll}) + {atk.attack_modifier} = {atk.attack_total} "
            f"vs AC {atk.target_ac} → ACERTOU! "
            f"{final_dmg} de dano {dmg.damage_type}{damage_note}{sneak_note}{smite_note}"
            f"{' (CRÍTICO!)' if atk.is_crit else ''}. "
            f"{target.name} agora tem {new_hp} HP.{conc_broken_note}{reaction_note}"
        ),
        mechanical={
            "attack_roll": atk.attack_roll,
            "attack_total": atk.attack_total,
            "target_ac": atk.target_ac,
            "is_hit": True,
            "is_crit": atk.is_crit,
            "is_fumble": atk.is_fumble,
            "weapon": atk.weapon,
            "damage": final_dmg,
            "damage_raw": dmg.total,
            "sneak_attack": bool(sneak_note),
            "divine_smite": bool(smite_note and "falhou" not in smite_note),
            "attacks_remaining": engine._attacks_remaining.get(action.actor_id, 0),
            "damage_type": dmg.damage_type,
            "damage_rolls": dmg.individual_rolls,
            "damage_multiplier": mult,
            "target_hp": new_hp,
            "target_down": new_hp == 0,
        },
    )


def _handle_dash(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    engine._validate_combat_turn(state_manager, action)
    actor = state_manager.get_creature(action.actor_id)
    return ActionResult(
        success=True,
        message=f"{actor.name} usa Dash (movimento dobrado neste turno).",
        mechanical={"action_type": "dash"},
    )


def _handle_dodge(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    engine._validate_combat_turn(state_manager, action)
    actor = state_manager.get_creature(action.actor_id)
    state_manager.add_condition(action.actor_id, Condition.DODGING)
    return ActionResult(
        success=True,
        message=f"{actor.name} usa Dodge (ataques contra ele têm desvantagem).",
        mechanical={"action_type": "dodge", "condition": "DODGING"},
    )


def _handle_disengage(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    engine._validate_combat_turn(state_manager, action)
    actor = state_manager.get_creature(action.actor_id)
    return ActionResult(
        success=True,
        message=f"{actor.name} se desengaja (sem ataques de oportunidade).",
        mechanical={"action_type": "disengage"},
    )


def _handle_help(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    engine._validate_combat_turn(state_manager, action)
    target = state_manager.get_creature(action.target_id or "")
    if target is None:
        raise UnknownTargetError(f"Alvo desconhecido: {action.target_id!r}")
    return ActionResult(
        success=True,
        message=(
            f"{state_manager.get_creature(action.actor_id).name} ajuda "
            f"{target.name} (próximo teste com vantagem)."
        ),
        mechanical={"action_type": "help", "target": target.id},
    )


def _handle_hide(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    engine._validate_combat_turn(state_manager, action)
    actor = state_manager.get_creature(action.actor_id)
    state_manager.add_condition(action.actor_id, Condition.HIDDEN)
    return ActionResult(
        success=True,
        message=f"{actor.name} tenta se esconder.",
        mechanical={"action_type": "hide", "condition": "HIDDEN"},
    )


def _handle_search(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    engine._validate_combat_turn(state_manager, action)
    return ActionResult(
        success=True,
        message="Você busca ativamente no ambiente.",
        mechanical={"action_type": "search"},
    )


def _handle_use_object(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    engine._validate_combat_turn(state_manager, action)
    obj = action.params.get("object", "(objeto)")
    return ActionResult(
        success=True,
        message=f"Você usa {obj}.",
        mechanical={"action_type": "use_object", "object": obj},
    )


def _handle_ready(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    engine._validate_combat_turn(state_manager, action)
    trigger = action.params.get("trigger", "(gatilho não especificado)")
    return ActionResult(
        success=True,
        message=f"Você prepara uma ação para quando {trigger}.",
        mechanical={"action_type": "ready", "trigger": trigger},
    )


def _handle_death_save(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    actor = state_manager.get_creature(action.actor_id)
    if actor is None or not isinstance(actor, Character):
        raise UnknownTargetError(
            "Teste de morte só se aplica a personagens do tipo Character."
        )
    if actor.hp_current > 0:
        return ActionResult(
            success=False,
            message=f"{actor.name} não está inconsciente (HP > 0).",
            mechanical={},
        )

    result, died = death_save(actor, rng=engine.rng)
    if died:
        return ActionResult(
            success=False,
            message=f"{actor.name} morreu (3 falhas em testes de morte).",
            mechanical={
                "roll": result.roll,
                "failures": actor.death_save_failures,
                "died": True,
            },
        )
    return ActionResult(
        success=True,
        message=(
            f"{actor.name} rola teste de morte: d20({result.roll}) → "
            f"{'sucesso' if result.is_success else 'falha'}. "
            f"Placar: {actor.death_save_successes}✔ {actor.death_save_failures}✘"
        ),
        mechanical={
            "roll": result.roll,
            "is_success": result.is_success,
            "is_crit": result.is_crit,
            "is_fumble": result.is_fumble,
            "successes": actor.death_save_successes,
            "failures": actor.death_save_failures,
            "stabilized": actor.death_save_successes >= 3,
        },
    )


def _handle_end_combat(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    summary = engine.end_combat(state_manager)
    return ActionResult(
        success=True,
        message=(
            f"Combate encerrado após {summary.rounds_elapsed} rodada(s)."
        ),
        mechanical={"summary": summary.__dict__},
    )


def _handle_opportunity_attack(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Perform an opportunity attack: a single melee attack as a reaction.

    PHB p. 195: "You can make an opportunity attack when a hostile
    creature that you can see moves out of your reach." The attack uses
    your reaction. The DM is responsible for narrating when an OA is
    triggered; the engine just resolves the attack roll + damage when
    the LLM emits this action.
    """
    # Allow OA outside normal turn order — don't enforce turn check.
    attacker = state_manager.get_creature(action.actor_id)
    if attacker is None:
        raise UnknownTargetError(f"Atacante desconhecido: {action.actor_id!r}")
    target = state_manager.get_creature(action.target_id or "")
    if target is None:
        raise UnknownTargetError(f"Alvo desconhecido: {action.target_id!r}")
    if target.hp_current <= 0:
        return ActionResult(
            success=False,
            message=f"{target.name} já está fora de combate.",
            mechanical={},
        )

    # OA is always melee within reach.
    atk = attack_roll(attacker, target, rng=engine.rng)  # type: ignore[arg-type]
    if not atk.is_hit:
        return ActionResult(
            success=True,
            message=(
                f"[Oportunidade] {attacker.name} ataca {target.name} com "
                f"{atk.weapon}: d20({atk.attack_roll}) + "
                f"{atk.attack_modifier} = {atk.attack_total} vs AC "
                f"{atk.target_ac} → ERROU"
            ),
            mechanical={
                "attack_roll": atk.attack_roll,
                "attack_total": atk.attack_total,
                "is_hit": False,
                "reaction_used": True,
            },
        )

    dmg = damage_roll(attacker, is_crit=atk.is_crit, rng=engine.rng)  # type: ignore[arg-type]
    mult = damage_multiplier(target, dmg.damage_type)
    from auto_dm.engine.rage import is_raging, apply_rage_resistance
    if is_raging(target) and apply_rage_resistance(dmg.damage_type):
        if dmg.damage_type.lower() not in {v.lower() for v in target.vulnerabilities}:
            mult = min(mult, 0.5)
    final_dmg = max(0, int(round(dmg.total * mult)))
    new_hp = state_manager.set_hp(target.id, -final_dmg)

    conc_broken_note = ""
    if isinstance(target, Character) and final_dmg > 0:
        conc = concentration_save(target, final_dmg, rng=engine.rng)
        if conc.broken:
            conc_broken_note = f" {target.name} perdeu a concentração!"

    return ActionResult(
        success=True,
        message=(
            f"[Oportunidade] {attacker.name} ataca {target.name} com "
            f"{atk.weapon}: d20({atk.attack_roll}) + "
            f"{atk.attack_modifier} = {atk.attack_total} vs AC "
            f"{atk.target_ac} → ACERTOU! {final_dmg} dano {dmg.damage_type}"
            f"{' (CRÍTICO!)' if atk.is_crit else ''}. "
            f"{target.name} agora tem {new_hp} HP.{conc_broken_note}"
        ),
        mechanical={
            "attack_roll": atk.attack_roll,
            "attack_total": atk.attack_total,
            "is_hit": True,
            "is_crit": atk.is_crit,
            "damage": final_dmg,
            "damage_type": dmg.damage_type,
            "target_hp": new_hp,
            "target_down": new_hp == 0,
            "reaction_used": True,
        },
    )


def _handle_cast_spell(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Cast a spell. Validates and consumes the slot.

    The engine handles bookkeeping (slot, concentration). Spell effects
    (damage, healing, conditions) are the narrative layer's job — the
    engine reports what *should* happen given the spell's data.
    """
    caster = state_manager.get_creature(action.actor_id)
    if caster is None or not isinstance(caster, Character):
        raise UnknownTargetError(f"Conjurador desconhecido: {action.actor_id!r}")
    if caster.spellcasting is None:
        return ActionResult(
            success=False,
            message=f"{caster.name} não é capaz de lançar magias.",
            mechanical={},
        )

    spell_name = action.params.get("spell", "")
    if not spell_name:
        return ActionResult(
            success=False,
            message="Magia não especificada.",
            mechanical={},
        )
    slot_level = action.params.get("slot_level")  # optional upcast

    # Resolve targets (if any)
    target_ids = action.params.get("target_ids") or []
    targets: list[NPC | Character] = []
    for tid in target_ids:
        t = state_manager.get_creature(tid)
        if t is not None:
            targets.append(t)

    result = cast_spell(
        caster,
        spell_name,
        slot_level=slot_level,
        targets=targets,
        rng=engine.rng,
    )

    if not result.success:
        return ActionResult(
            success=False,
            message=f"Falha ao lançar {spell_name}: {result.error}",
            mechanical={"spell": spell_name},
        )

    parts = [f"{caster.name} lança {spell_name}"]
    if result.upcast:
        parts.append(f"no slot de {result.slot_level_used}º nível")
    if result.started_concentration:
        parts.append("(concentração)")
    msg = " — ".join(parts) + "."

    # Phase 41 — publish a Counterspell trigger when a non-player caster
    # casts a leveled spell (an enemy mage, a dominated companion, etc.).
    # The player (or a companion) may spend their reaction to counter it.
    # MVP limitation: pure NPCs rarely carry structured ``spellcasting``,
    # so this fires mainly for Character casters that aren't the player.
    reaction_note = ""
    if (
        result.success
        and result.slot_level_used >= 1
        and caster.id != state_manager.state.player_character_id
    ):
        from auto_dm.engine.actions import OnSeeingSpellCast
        from auto_dm.engine.reactions import publish_reaction_trigger
        import time
        trigger = OnSeeingSpellCast(
            caster_id=caster.id,
            spell_name=spell_name,
            level=result.slot_level_used,
        )
        responders = publish_reaction_trigger(
            state_manager, trigger, fired_at=int(time.time()),
            engine=engine,
            # Only the player is prompted (companions countering enemy
            # spells is a rare case handled by future heuristic).
            candidates=[state_manager.state.player_character_id],
        )
        if responders:
            reaction_note = " [reação disponível]"

    return ActionResult(
        success=True,
        message=msg + reaction_note,
        mechanical={
            "spell": spell_name,
            "slot_level_used": result.slot_level_used,
            "upcast": result.upcast,
            "started_concentration": result.started_concentration,
            "target_ids": result.target_ids,
        },
    )


def _handle_rage(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Enter Barbarian's Rage. PHB p. 48 — bonus action.

    Consumes 1 use of the barbarian's daily rages. The damage bonus,
    resistance, and STR-save advantage are applied by the combat
    pipeline via ``engine/rage.py``.
    """
    from auto_dm.engine.rage import can_rage, enter_rage

    actor = state_manager.get_creature(action.actor_id)
    if actor is None or not isinstance(actor, Character):
        raise UnknownTargetError(f"Atacante desconhecido: {action.actor_id!r}")

    allowed, reason = can_rage(actor)
    if not allowed:
        return ActionResult(
            success=False,
            message=f"{actor.name} não pode entrar em fúria: {reason}.",
            mechanical={"reason": reason},
        )
    result = enter_rage(actor)
    return ActionResult(
        success=True,
        message=result.message,
        mechanical={
            "duration_rounds": result.duration_rounds,
            "rages_used": actor.rages_used,
            "rages_remaining": actor.rages_max - actor.rages_used,
        },
    )


def _handle_cunning_action(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Cunning Action (Rogue L2). Bonus action: dash, disengage, or hide.

    Dispatches to the matching base handler. ``params.subaction`` is
    one of ``"dash" | "disengage" | "hide"``.
    """
    actor = state_manager.get_creature(action.actor_id)
    if actor is None or not isinstance(actor, Character):
        raise UnknownTargetError(f"Atacante desconhecido: {action.actor_id!r}")
    if not actor.has_cunning_action:
        return ActionResult(
            success=False,
            message=f"{actor.name} não tem Ação Astuta (Cunning Action).",
            mechanical={},
        )
    sub = action.params.get("subaction", "").lower()
    if sub not in {"dash", "disengage", "hide"}:
        return ActionResult(
            success=False,
            message=(
                f"Cunning Action requer subaction dash|disengage|hide; "
                f"recebido: {sub!r}"
            ),
            mechanical={},
        )
    # Dispatch
    type_map = {
        "dash": ActionType.DASH,
        "disengage": ActionType.DISENGAGE,
        "hide": ActionType.HIDE,
    }
    sub_action = Action(
        actor_id=actor.id,
        action_type=type_map[sub],
        target_id=action.target_id,
        dialogue=action.dialogue,
        reasoning=action.reasoning,
    )
    return _ACTION_HANDLERS[type_map[sub]](engine, state_manager, sub_action)


def _handle_second_wind(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Second Wind (Fighter L1). Bonus action: heal 1d10 + fighter level.
    1/short rest (2 at L17+)."""
    from auto_dm.engine.resources import can_use_second_wind, roll_second_wind_heal
    actor = state_manager.get_creature(action.actor_id)
    if actor is None or not isinstance(actor, Character):
        raise UnknownTargetError(f"Atacante desconhecido: {action.actor_id!r}")
    if actor.class_.lower() != "fighter":
        return ActionResult(
            success=False, message="Apenas fighters podem usar Second Wind.",
            mechanical={},
        )
    if not can_use_second_wind(actor):
        return ActionResult(
            success=False, message="Second Wind já foi usado neste descanso.",
            mechanical={},
        )
    heal = roll_second_wind_heal(actor.level, rng=engine.rng)
    actor.hp_current = min(actor.hp_max, actor.hp_current + heal)
    actor.second_wind_used = True
    return ActionResult(
        success=True,
        message=(
            f"{actor.name} usa Second Wind: recupera {heal} HP. "
            f"HP: {actor.hp_current}/{actor.hp_max}."
        ),
        mechanical={"heal": heal, "hp": actor.hp_current},
    )


def _handle_action_surge(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Action Surge (Fighter L2). Take one additional action this turn.
    1/short rest (2 at L17+)."""
    from auto_dm.engine.resources import action_surge
    actor = state_manager.get_creature(action.actor_id)
    if actor is None or not isinstance(actor, Character):
        raise UnknownTargetError(f"Atacante desconhecido: {action.actor_id!r}")
    if actor.class_.lower() != "fighter":
        return ActionResult(
            success=False, message="Apenas fighters podem usar Action Surge.",
            mechanical={},
        )
    if not action_surge(actor):
        return ActionResult(
            success=False, message="Action Surge já foi usado neste descanso.",
            mechanical={},
        )
    return ActionResult(
        success=True,
        message=f"{actor.name} usa Action Surge! Tem 1 ação adicional neste turno.",
        mechanical={"action_surges_remaining": actor.action_surges_remaining},
    )


def _handle_lay_on_hands(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Lay on Hands (Paladin L1). Action: heal target from pool."""
    from auto_dm.engine.resources import spend_lay_on_hands
    actor = state_manager.get_creature(action.actor_id)
    target = state_manager.get_creature(action.target_id or actor.id if actor else "")
    if actor is None or not isinstance(actor, Character):
        raise UnknownTargetError(f"Atacante desconhecido: {action.actor_id!r}")
    if target is None:
        return ActionResult(
            success=False, message="Alvo inválido para Lay on Hands.",
            mechanical={},
        )
    if actor.class_.lower() != "paladin":
        return ActionResult(
            success=False, message="Apenas paladinos podem usar Lay on Hands.",
            mechanical={},
        )
    amount = int(action.params.get("amount", 0))
    if amount <= 0:
        return ActionResult(
            success=False, message="Quantidade deve ser > 0.",
            mechanical={},
        )
    if not spend_lay_on_hands(actor, amount):
        return ActionResult(
            success=False,
            message=f"Pool de Lay on Hands insuficiente ({actor.lay_on_hands_pool}).",
            mechanical={},
        )
    new_hp = state_manager.set_hp(target.id, amount)
    return ActionResult(
        success=True,
        message=(
            f"{actor.name} usa Lay on Hands: cura {amount} HP em {target.name}. "
            f"HP: {new_hp}/{target.hp_max}. Pool: {actor.lay_on_hands_pool}."
        ),
        mechanical={"heal": amount, "target_hp": new_hp},
    )


def _handle_channel_divinity(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Channel Divinity (Cleric L2). Action: use the chosen Domain effect."""
    from auto_dm.engine.resources import use_channel_divinity
    actor = state_manager.get_creature(action.actor_id)
    if actor is None or not isinstance(actor, Character):
        raise UnknownTargetError(f"Atacante desconhecido: {action.actor_id!r}")
    if actor.class_.lower() != "cleric":
        return ActionResult(
            success=False, message="Apenas clérigos podem usar Channel Divinity.",
            mechanical={},
        )
    if not use_channel_divinity(actor):
        return ActionResult(
            success=False, message="Channel Divinity já foi usado neste descanso.",
            mechanical={},
        )
    effect = action.params.get("effect", "turn_undead")
    return ActionResult(
        success=True,
        message=(
            f"{actor.name} canaliza divindade: {effect}. "
            f"Resta(m) {actor.channel_divinity_remaining} uso(s)."
        ),
        mechanical={
            "effect": effect,
            "channel_divinity_remaining": actor.channel_divinity_remaining,
        },
    )


def _handle_bardic_inspiration(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Bardic Inspiration (Bard L1). Bonus action: grant ally a die."""
    from auto_dm.engine.resources import spend_bardic_inspiration
    actor = state_manager.get_creature(action.actor_id)
    target = state_manager.get_creature(action.target_id or actor.id if actor else "")
    if actor is None or not isinstance(actor, Character):
        raise UnknownTargetError(f"Atacante desconhecido: {action.actor_id!r}")
    if actor.class_.lower() != "bard":
        return ActionResult(
            success=False, message="Apenas bardos podem usar Inspiração Bárdica.",
            mechanical={},
        )
    if target is None:
        return ActionResult(
            success=False, message="Alvo inválido para Inspiração Bárdica.",
            mechanical={},
        )
    if not spend_bardic_inspiration(actor):
        return ActionResult(
            success=False,
            message="Sem usos de Inspiração Bárdica restantes.",
            mechanical={},
        )
    # Add a pending advantage to the target (same as spending inspiration)
    if isinstance(target, Character):
        target.pending_advantage += 1
    return ActionResult(
        success=True,
        message=(
            f"{actor.name} concede Inspiração Bárdica a {target.name} "
            f"(d{actor.bardic_inspiration_die})."
        ),
        mechanical={
            "die": actor.bardic_inspiration_die,
            "uses_remaining": actor.bardic_inspiration_uses,
        },
    )


def _handle_flurry_of_blows(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Flurry of Blows (Monk L2). Bonus action: 2 unarmed strikes, costs 1 ki."""
    from auto_dm.engine.resources import spend_ki
    actor = state_manager.get_creature(action.actor_id)
    if actor is None or not isinstance(actor, Character):
        raise UnknownTargetError(f"Atacante desconhecido: {action.actor_id!r}")
    if actor.class_.lower() != "monk":
        return ActionResult(
            success=False, message="Apenas monges podem usar Flurry of Blows.",
            mechanical={},
        )
    if not spend_ki(actor, 1):
        return ActionResult(
            success=False, message="Sem ki restante.", mechanical={},
        )
    return ActionResult(
        success=True,
        message=f"{actor.name} usa Flurry of Blows! (ki {actor.ki_points}/{actor.ki_max})",
        mechanical={"ki_remaining": actor.ki_points, "extra_attacks": 2},
    )


def _handle_stunning_strike(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Stunning Strike (Monk L5). After a hit, spend 1 ki, target makes
    CON save or be Stunned."""
    from auto_dm.engine.combat import saving_throw
    from auto_dm.engine.resources import spend_ki
    actor = state_manager.get_creature(action.actor_id)
    target = state_manager.get_creature(action.target_id or "")
    if actor is None or not isinstance(actor, Character):
        raise UnknownTargetError(f"Atacante desconhecido: {action.actor_id!r}")
    if target is None:
        return ActionResult(
            success=False, message="Alvo inválido para Stunning Strike.",
            mechanical={},
        )
    if actor.class_.lower() != "monk":
        return ActionResult(
            success=False, message="Apenas monges podem usar Stunning Strike.",
            mechanical={},
        )
    if not spend_ki(actor, 1):
        return ActionResult(
            success=False, message="Sem ki restante.", mechanical={},
        )
    dc = actor.spellcasting.save_dc if actor.spellcasting else 13
    save = saving_throw(target, Ability.CON, dc, rng=engine.rng)
    stunned = not save.is_success
    if stunned and isinstance(target, Character):
        if Condition.STUNNED not in target.conditions:
            target.conditions.append(Condition.STUNNED)
    return ActionResult(
        success=True,
        message=(
            f"{actor.name} tenta Stunning Strike em {target.name}: "
            f"{save.total} vs DC {dc} → "
            f"{'ATORDOADO' if stunned else 'resistiu'}"
        ),
        mechanical={
            "stunned": stunned,
            "save_total": save.total,
            "dc": dc,
            "ki_remaining": actor.ki_points,
        },
    )


def _handle_uncanny_dodge(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Uncanny Dodge (Rogue L5). Reaction: halve incoming attack damage.
    Once per round."""
    actor = state_manager.get_creature(action.actor_id)
    if actor is None or not isinstance(actor, Character):
        raise UnknownTargetError(f"Atacante desconhecido: {action.actor_id!r}")
    if not getattr(actor, "has_uncanny_dodge", False):
        return ActionResult(
            success=False, message=f"{actor.name} não tem Uncanny Dodge.",
            mechanical={},
        )
    return ActionResult(
        success=True,
        message=f"{actor.name} usa Uncanny Dodge: próximo ataque tem dano reduzido pela metade.",
        mechanical={"halves_next_attack": True},
    )


def _handle_reckless_attack(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Reckless Attack (Barbarian L2). Toggle advantage on STR melee,
    but attacks against you also have advantage until next turn."""
    actor = state_manager.get_creature(action.actor_id)
    if actor is None or not isinstance(actor, Character):
        raise UnknownTargetError(f"Atacante desconhecido: {action.actor_id!r}")
    if actor.class_.lower() != "barbarian":
        return ActionResult(
            success=False, message="Apenas bárbaros podem usar Reckless Attack.",
            mechanical={},
        )
    actor.is_reckless = not actor.is_reckless
    return ActionResult(
        success=True,
        message=(
            f"{actor.name} {'ativa' if actor.is_reckless else 'desativa'} "
            f"Reckless Attack."
        ),
        mechanical={"is_reckless": actor.is_reckless},
    )


def _handle_indomitable(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Indomitable (Fighter L9). Reroll a failed save, must use 2nd roll.
    1/long rest (more at higher levels — for MVP, 1)."""
    actor = state_manager.get_creature(action.actor_id)
    if actor is None or not isinstance(actor, Character):
        raise UnknownTargetError(f"Atacante desconhecido: {action.actor_id!r}")
    if actor.class_.lower() != "fighter":
        return ActionResult(
            success=False, message="Apenas fighters podem usar Indomitable.",
            mechanical={},
        )
    return ActionResult(
        success=True,
        message=f"{actor.name} usa Indomitable: rerola a próxima save falha.",
        mechanical={"reroll_next_save": True},
    )


def _handle_mount(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Mount a creature or vehicle. Costs half the rider's movement.

    Mutates Character/NPC flags directly. The actor is the rider; the
    target_id is the mount's creature id (party Character or NPC).
    """
    engine._validate_combat_turn(state_manager, action)
    rider = state_manager.get_creature(action.actor_id)
    if rider is None:
        return ActionResult(
            success=False,
            message=f"Ator desconhecido: {action.actor_id!r}",
        )
    if action.target_id is None:
        return ActionResult(
            success=False,
            message="Mount requer um alvo (target_id).",
        )
    mount = state_manager.get_creature(action.target_id)
    if mount is None:
        return ActionResult(
            success=False,
            message=f"Alvo desconhecido: {action.target_id!r}",
        )
    # Reject if either side already has a mount relationship.
    if isinstance(rider, Character) and rider.is_mounted:
        return ActionResult(
            success=False,
            message=f"{rider.name} já está montado em {rider.mount_id}.",
        )
    # Mark both sides. Character/NPC have separate flags.
    if isinstance(rider, Character):
        rider.is_mounted = True
        rider.mount_id = mount.id
    if isinstance(mount, NPC):
        mount.rider_id = rider.id
    return ActionResult(
        success=True,
        message=f"{rider.name} monta em {mount.name} (custa metade do movimento).",
        mechanical={
            "action_type": "mount",
            "rider": rider.id,
            "mount": mount.id,
        },
    )


def _handle_dismount(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Dismount from the current mount. Costs half the rider's movement."""
    engine._validate_combat_turn(state_manager, action)
    rider = state_manager.get_creature(action.actor_id)
    if rider is None:
        return ActionResult(
            success=False,
            message=f"Ator desconhecido: {action.actor_id!r}",
        )
    if isinstance(rider, Character) and not rider.is_mounted:
        return ActionResult(
            success=False,
            message=f"{rider.name} não está montado.",
        )
    mount_id = getattr(rider, "mount_id", None) if isinstance(rider, Character) else None
    if isinstance(rider, Character):
        rider.is_mounted = False
        rider.mount_id = None
    if mount_id is not None:
        mount = state_manager.get_creature(mount_id)
        if mount is not None and isinstance(mount, NPC):
            mount.rider_id = None
    return ActionResult(
        success=True,
        message=f"{rider.name} desmonta (custa metade do movimento).",
        mechanical={"action_type": "dismount", "rider": rider.id},
    )


def _handle_reaction(
    engine: CombatEngine,
    state_manager: StateManager,
    action: Action,
) -> ActionResult:
    """Resolve a reaction (Phase 41).

    The responder (``action.actor_id``) answers a previously published
    trigger. ``action.params`` carries::

        {"kind": "<ReactionKind value>",
         "trigger": <payload from pending_reaction.trigger>,
         "slot_level": <optional, for spell reactions>,
         "check_roll": <optional, for Counterspell ability check>}

    Eligibility is re-checked at resolution time (the responder may have
    lost the reaction or the slot between publication and answer). Runs
    outside the normal turn order — no turn check — because reactions
    happen on other actors' turns.
    """
    from auto_dm.engine.actions import trigger_from_payload
    from auto_dm.engine.actions import ReactionKind
    from auto_dm.engine.reactions import apply_reaction

    responder = state_manager.get_character(action.actor_id)
    if responder is None:
        return ActionResult(
            success=False,
            message=f"Personagem {action.actor_id!r} não encontrado.",
            mechanical={},
        )

    kind_value = action.params.get("kind", "")
    try:
        kind = ReactionKind(kind_value)
    except ValueError:
        return ActionResult(
            success=False,
            message=f"Tipo de reação desconhecido: {kind_value!r}",
            mechanical={"kind": kind_value},
        )

    trigger_payload = action.params.get("trigger") or {}
    trigger = trigger_from_payload(trigger_payload)

    resolution = apply_reaction(
        engine, state_manager, action.actor_id, kind, trigger,
        slot_level=action.params.get("slot_level"),
        check_roll=action.params.get("check_roll"),
    )

    mechanical = {
        "reaction_kind": kind.value,
        "success": resolution.success,
        "consumed_reaction": resolution.consumed_reaction,
        "consumed_slot_level": resolution.consumed_slot_level,
        "reason": resolution.reason,
    }
    if resolution.damage_modified_to is not None:
        mechanical["damage_modified_to"] = resolution.damage_modified_to
    if resolution.spell_cancelled:
        mechanical["spell_cancelled"] = True
    if resolution.healed_to is not None:
        mechanical["healed_to"] = resolution.healed_to
    if resolution.rebuke_damage:
        mechanical["rebuke_damage"] = resolution.rebuke_damage
        mechanical["rebuke_target_hp"] = resolution.rebuke_target_hp

    return ActionResult(
        success=resolution.success,
        message=resolution.message,
        mechanical=mechanical,
    )


# ActionType → handler (placed at end of module so all refs are defined)
_ACTION_HANDLERS = {
    ActionType.ATTACK: _handle_attack,
    ActionType.DASH: _handle_dash,
    ActionType.DODGE: _handle_dodge,
    ActionType.DISENGAGE: _handle_disengage,
    ActionType.HELP: _handle_help,
    ActionType.HIDE: _handle_hide,
    ActionType.SEARCH: _handle_search,
    ActionType.USE_OBJECT: _handle_use_object,
    ActionType.READY: _handle_ready,
    ActionType.DEATH_SAVE: _handle_death_save,
    ActionType.END_COMBAT: _handle_end_combat,
    ActionType.OPPORTUNITY_ATTACK: _handle_opportunity_attack,
    ActionType.CAST_SPELL: _handle_cast_spell,
    ActionType.RAGE: _handle_rage,
    ActionType.CUNNING_ACTION: _handle_cunning_action,
    ActionType.SECOND_WIND: _handle_second_wind,
    ActionType.ACTION_SURGE: _handle_action_surge,
    ActionType.LAY_ON_HANDS: _handle_lay_on_hands,
    ActionType.CHANNEL_DIVINITY: _handle_channel_divinity,
    ActionType.BARDIC_INSPIRATION: _handle_bardic_inspiration,
    ActionType.FLURRY_OF_BLOWS: _handle_flurry_of_blows,
    ActionType.STUNNING_STRIKE: _handle_stunning_strike,
    ActionType.UNCANNY_DODGE: _handle_uncanny_dodge,
    ActionType.RECKLESS_ATTACK: _handle_reckless_attack,
    ActionType.INDOMITABLE: _handle_indomitable,
    ActionType.MOUNT: _handle_mount,
    ActionType.DISMOUNT: _handle_dismount,
    ActionType.REACTION: _handle_reaction,
}

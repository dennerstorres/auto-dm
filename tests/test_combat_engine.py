"""Tests for the high-level CombatEngine (turn orchestration, action dispatch)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from auto_dm.engine.combat_engine import (
    CombatEngine,
    EncounterSummary,
    NotInCombatError,
)
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    AbilityScores,
    Action,
    ActionType,
    Character,
    Condition,
    EquippedSlots,
    GameState,
    Item,
    ItemType,
    NPC,
    Proficiencies,
    WeaponProperties,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_weapon(
    name: str = "Longsword",
    dice: str = "1d8",
    dtype: str = "slashing",
    finesse: bool = False,
) -> Item:
    return Item(
        name=name,
        type=ItemType.WEAPON,
        weapon=WeaponProperties(damage_dice=dice, damage_type=dtype, finesse=finesse),
    )


def make_creature(
    *,
    id: str,
    name: str = "X",
    strength: int = 14,
    dexterity: int = 10,
    constitution: int = 14,
    proficiency_bonus: int = 2,
    hp: int = 20,
    ac: int = 13,
    weapon: Item | None = None,
    is_character: bool = True,
) -> Character | NPC:
    abilities = AbilityScores(
        strength=strength,
        dexterity=dexterity,
        constitution=constitution,
        intelligence=8,
        wisdom=10,
        charisma=8,
    )
    if is_character:
        return Character(
            id=id,
            name=name,
            **{"class": "Fighter"},
            race="Human",
            level=1,
            background="Soldier",
            alignment="N",
            abilities=abilities,
            hp_current=hp,
            hp_max=hp,
            armor_class=ac,
            speed=30,
            proficiency_bonus=proficiency_bonus,
            hit_dice="1d10",
            hit_dice_remaining=1,
            equipped=EquippedSlots(main_hand=weapon) if weapon else EquippedSlots(),
            proficiencies=Proficiencies(),
        )
    return NPC(
        id=id,
        name=name,
        hp_current=hp,
        hp_max=hp,
        armor_class=ac,
        speed=30,
        abilities=abilities,
        equipped=EquippedSlots(main_hand=weapon) if weapon else EquippedSlots(),
    )


class ScriptedRNG:
    """Mock RNG returning queued values; raises if exhausted."""

    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        if not self._values:
            raise AssertionError("ScriptedRNG exhausted")
        v = self._values.pop(0)
        if not a <= v <= b:
            raise AssertionError(f"ScriptedRNG value {v} out of range [{a},{b}]")
        return v


def make_state_with(party: list, npcs: list) -> StateManager:
    return StateManager(
        GameState(
            campaign_name="encounter",
            started_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
            current_location="arena",
            party=party,
            npcs=npcs,
            player_character_id=party[0].id if party else "p1",
        )
    )


# ---------------------------------------------------------------------------
# start_combat
# ---------------------------------------------------------------------------


class TestStartCombat:
    def test_sets_in_combat_and_round_1(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon())],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))  # initiative rolls
        order = engine.start_combat(sm)
        assert sm.state.in_combat
        assert sm.state.round_number == 1
        assert sm.state.current_turn_index == 0
        assert set(order) == {"p1", "g1"}
        assert len(order) == 2

    def test_initiative_higher_first(self):
        sm = make_state_with(
            party=[
                make_creature(id="slow", dexterity=8, weapon=make_weapon()),
            ],
            npcs=[
                make_creature(id="fast", dexterity=20, is_character=False, hp=10, ac=12),
            ],
        )
        engine = CombatEngine(rng=ScriptedRNG([5, 5]))  # both roll 5
        order = engine.start_combat(sm)
        # DEX 8 = -1, DEX 20 = +5 -> fast goes first
        assert order[0] == "fast"
        assert order[1] == "slow"

    def test_double_start_returns_existing_order(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon())],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))
        first = engine.start_combat(sm)
        second = engine.start_combat(sm)
        assert first == second

    def test_no_combatants_raises(self):
        sm = make_state_with(party=[], npcs=[])
        engine = CombatEngine(rng=ScriptedRNG([]))
        with pytest.raises(NotInCombatError):
            engine.start_combat(sm)

    def test_includes_extra_combatants(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon())],
            npcs=[],
        )
        extra = make_creature(id="wolf", is_character=False, hp=5, ac=12)
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))
        order = engine.start_combat(sm, extra_combatants=[extra])
        assert "wolf" in order
        assert "p1" in order


# ---------------------------------------------------------------------------
# end_combat
# ---------------------------------------------------------------------------


class TestEndCombat:
    def test_clears_state(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon())],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))
        engine.start_combat(sm)
        summary = engine.end_combat(sm)
        assert not sm.state.in_combat
        assert sm.state.round_number == 0
        assert sm.state.initiative_order == []
        assert isinstance(summary, EncounterSummary)

    def test_summary_records_survivors(self):
        party = [make_creature(id="p1", weapon=make_weapon(), hp=20)]
        npcs = [
            make_creature(id="g1", is_character=False, hp=0, ac=12),  # dead
            make_creature(id="g2", is_character=False, hp=10, ac=12),  # alive
        ]
        sm = make_state_with(party=party, npcs=npcs)
        engine = CombatEngine(rng=ScriptedRNG([10, 5, 5]))
        engine.start_combat(sm)
        sm.state.round_number = 4
        summary = engine.end_combat(sm)
        assert summary.rounds_elapsed == 4
        assert "p1" in summary.survivors_party
        assert "g2" in summary.survivors_enemies
        assert "g1" in summary.enemies_defeated
        assert "g2" not in summary.enemies_defeated

    def test_end_when_not_in_combat_returns_empty(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon())],
            npcs=[],
        )
        engine = CombatEngine(rng=ScriptedRNG([]))
        summary = engine.end_combat(sm)
        assert summary.rounds_elapsed == 0
        assert summary.survivors_party == []


# ---------------------------------------------------------------------------
# Turn progression
# ---------------------------------------------------------------------------


class TestTurnProgression:
    def test_next_turn_advances(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon())],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))
        engine.start_combat(sm)
        first = engine.current_actor_id(sm)
        engine.next_turn(sm)
        second = engine.current_actor_id(sm)
        assert first is not None
        assert second is not None
        assert first != second

    def test_next_turn_increments_round(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon())],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))
        engine.start_combat(sm)
        engine.next_turn(sm)
        engine.next_turn(sm)  # wraps
        assert sm.state.round_number == 2

    def test_next_turn_returns_none_when_combat_should_end(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon())],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))
        engine.start_combat(sm)
        sm.state.npcs[0].hp_current = 0  # kill the only enemy
        actor = engine.next_turn(sm)
        assert actor is None
        assert not sm.state.in_combat

    def test_next_turn_ends_when_party_wiped(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon(), hp=0)],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))
        engine.start_combat(sm)
        actor = engine.next_turn(sm)
        assert actor is None
        assert not sm.state.in_combat


# ---------------------------------------------------------------------------
# Attack action
# ---------------------------------------------------------------------------


class TestAttack:
    def _setup(self, *, actor_ac: int = 13, target_hp: int = 20, target_ac: int = 12):
        party = [make_creature(id="p1", name="PC", weapon=make_weapon(), ac=actor_ac, hp=20)]
        npcs = [make_creature(id="g1", name="Goblin", is_character=False, hp=target_hp, ac=target_ac)]
        return make_state_with(party=party, npcs=npcs)

    def test_attack_misses_returns_mechanical_details(self):
        sm = self._setup()
        # init (2 rolls) + d20 attack roll
        engine = CombatEngine(rng=ScriptedRNG([5, 5, 5]))
        engine.start_combat(sm)
        # Force p1 to act first so we can test the attack.
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        action = Action(actor_id="p1", action_type=ActionType.ATTACK, target_id="g1")
        result = engine.execute_action(sm, action)
        assert result.success
        assert not result.mechanical["is_hit"]
        assert result.mechanical["attack_roll"] == 5
        assert "ERROU" in result.message

    def test_attack_hits_applies_damage(self):
        sm = self._setup()
        # Init: 10, 5. Attack roll needs to be high enough to hit AC 12.
        # 14 + STR(+2) + prof(+2) = 18 > 12 → hit. Damage roll: arbitrary.
        engine = CombatEngine(rng=ScriptedRNG([10, 5, 14, 6]))
        engine.start_combat(sm)
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        before_hp = sm.state.npcs[0].hp_current
        action = Action(actor_id="p1", action_type=ActionType.ATTACK, target_id="g1")
        result = engine.execute_action(sm, action)
        assert result.success
        assert result.mechanical["is_hit"]
        assert result.mechanical["damage"] >= 1
        assert sm.state.npcs[0].hp_current < before_hp
        assert result.mechanical["target_hp"] == sm.state.npcs[0].hp_current

    def test_attack_crit_doubles_dice_and_announces(self):
        sm = self._setup()
        # Nat 20 (crit), then 2d8 doubled damage
        engine = CombatEngine(rng=ScriptedRNG([10, 5, 20, 4, 5]))
        engine.start_combat(sm)
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        action = Action(actor_id="p1", action_type=ActionType.ATTACK, target_id="g1")
        result = engine.execute_action(sm, action)
        assert result.mechanical["is_crit"]
        assert len(result.mechanical["damage_rolls"]) == 2  # doubled
        assert "CRÍTICO" in result.message

    def test_attack_fumble_auto_miss(self):
        sm = self._setup()
        # Nat 1 → fumble, even though it would normally hit
        engine = CombatEngine(rng=ScriptedRNG([10, 5, 1]))
        engine.start_combat(sm)
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        action = Action(actor_id="p1", action_type=ActionType.ATTACK, target_id="g1")
        result = engine.execute_action(sm, action)
        assert result.mechanical["is_fumble"]
        assert not result.mechanical["is_hit"]
        assert sm.state.npcs[0].hp_current == 20  # untouched

    def test_attack_kills_target_records_target_down(self):
        sm = self._setup(target_hp=5)
        # 20 (crit) → guaranteed hit, doubled damage 2d8 = 16+
        engine = CombatEngine(rng=ScriptedRNG([10, 5, 20, 6, 7]))
        engine.start_combat(sm)
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        action = Action(actor_id="p1", action_type=ActionType.ATTACK, target_id="g1")
        result = engine.execute_action(sm, action)
        assert result.mechanical["target_down"]
        assert sm.state.npcs[0].hp_current == 0

    def test_attack_against_down_target_refused(self):
        sm = self._setup()
        sm.state.npcs[0].hp_current = 0
        engine = CombatEngine(rng=ScriptedRNG([10, 5, 14]))
        engine.start_combat(sm)
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        action = Action(actor_id="p1", action_type=ActionType.ATTACK, target_id="g1")
        result = engine.execute_action(sm, action)
        assert not result.success
        assert "fora de combate" in result.message

    def test_attack_unknown_target_rejected(self):
        sm = self._setup()
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))
        engine.start_combat(sm)
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        action = Action(actor_id="p1", action_type=ActionType.ATTACK, target_id="ghost")
        result = engine.execute_action(sm, action)
        assert not result.success
        assert isinstance(result.message, str)


# ---------------------------------------------------------------------------
# Turn / action validation
# ---------------------------------------------------------------------------


class TestActionValidation:
    def test_action_outside_combat_rejected_for_attack(self):
        party = [make_creature(id="p1", weapon=make_weapon())]
        npcs = [make_creature(id="g1", is_character=False, hp=10, ac=12)]
        sm = make_state_with(party=party, npcs=npcs)
        engine = CombatEngine(rng=ScriptedRNG([]))
        # Don't call start_combat
        action = Action(actor_id="p1", action_type=ActionType.ATTACK, target_id="g1")
        result = engine.execute_action(sm, action)
        assert not result.success
        assert "Não estamos em combate" in result.message

    def test_action_rejected_outside_actor_turn(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon())],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))
        engine.start_combat(sm)
        current = engine.current_actor_id(sm)
        other = "g1" if current == "p1" else "p1"
        action = Action(actor_id=other, action_type=ActionType.ATTACK, target_id=current)
        result = engine.execute_action(sm, action)
        assert not result.success
        assert "turno" in result.message.lower()

    def test_action_rejected_for_unconscious_actor(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon(), hp=0)],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))
        engine.start_combat(sm)
        # Manually set actor to p1 so we can test the unconscious gate
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        action = Action(actor_id="p1", action_type=ActionType.ATTACK, target_id="g1")
        result = engine.execute_action(sm, action)
        assert not result.success
        assert "inconsciente" in result.message.lower()

    def test_unknown_actor_rejected(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon())],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))
        engine.start_combat(sm)
        action = Action(actor_id="ghost", action_type=ActionType.ATTACK, target_id="g1")
        result = engine.execute_action(sm, action)
        assert not result.success


# ---------------------------------------------------------------------------
# Non-attack combat actions
# ---------------------------------------------------------------------------


class TestNonAttackActions:
    def _combat(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon())],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))
        engine.start_combat(sm)
        return sm, engine

    def test_dash(self):
        sm, engine = self._combat()
        result = engine.execute_action(
            sm, Action(actor_id="p1", action_type=ActionType.DASH)
        )
        assert result.success
        assert "Dash" in result.message

    def test_dodge_adds_condition(self):
        sm, engine = self._combat()
        result = engine.execute_action(
            sm, Action(actor_id="p1", action_type=ActionType.DODGE)
        )
        assert result.success
        assert Condition.DODGING in sm.state.party[0].conditions

    def test_disengage(self):
        sm, engine = self._combat()
        result = engine.execute_action(
            sm, Action(actor_id="p1", action_type=ActionType.DISENGAGE)
        )
        assert result.success
        assert "desengaja" in result.message.lower()

    def test_help(self):
        sm, engine = self._combat()
        result = engine.execute_action(
            sm,
            Action(
                actor_id="p1",
                action_type=ActionType.HELP,
                target_id="g1",  # can help an enemy for flanking? MVP: anyone
            ),
        )
        assert result.success

    def test_hide_adds_condition(self):
        sm, engine = self._combat()
        result = engine.execute_action(
            sm, Action(actor_id="p1", action_type=ActionType.HIDE)
        )
        assert result.success
        assert Condition.HIDDEN in sm.state.party[0].conditions

    def test_search(self):
        sm, engine = self._combat()
        result = engine.execute_action(
            sm, Action(actor_id="p1", action_type=ActionType.SEARCH)
        )
        assert result.success

    def test_use_object(self):
        sm, engine = self._combat()
        result = engine.execute_action(
            sm,
            Action(
                actor_id="p1",
                action_type=ActionType.USE_OBJECT,
                params={"object": "Potion of Healing"},
            ),
        )
        assert result.success
        assert "Potion" in result.message

    def test_ready(self):
        sm, engine = self._combat()
        result = engine.execute_action(
            sm,
            Action(
                actor_id="p1",
                action_type=ActionType.READY,
                params={"trigger": "o goblin se mover"},
            ),
        )
        assert result.success
        assert "goblin se mover" in result.message


# ---------------------------------------------------------------------------
# Death saves
# ---------------------------------------------------------------------------


class TestDeathSaveAction:
    def test_rolls_and_records(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon(), hp=0)],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5, 15]))
        engine.start_combat(sm)
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        result = engine.execute_action(
            sm, Action(actor_id="p1", action_type=ActionType.DEATH_SAVE)
        )
        assert result.success
        assert sm.state.party[0].death_save_successes == 1
        assert result.mechanical["is_success"]

    def test_rejected_for_alive_character(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon(), hp=20)],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5, 15]))
        engine.start_combat(sm)
        result = engine.execute_action(
            sm, Action(actor_id="p1", action_type=ActionType.DEATH_SAVE)
        )
        assert not result.success
        assert "inconsciente" in result.message.lower()

    def test_three_failures_marks_died(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon(), hp=0)],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        # 2 failures already + roll 5 (1 more failure = 3 = dead)
        sm.state.party[0].death_save_failures = 2
        engine = CombatEngine(rng=ScriptedRNG([10, 5, 5]))
        engine.start_combat(sm)
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        result = engine.execute_action(
            sm, Action(actor_id="p1", action_type=ActionType.DEATH_SAVE)
        )
        assert not result.success
        assert result.mechanical["died"]
        assert "morreu" in result.message

    def test_nat_20_revives(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon(), hp=0)],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5, 20]))
        engine.start_combat(sm)
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        engine.execute_action(
            sm, Action(actor_id="p1", action_type=ActionType.DEATH_SAVE)
        )
        assert sm.state.party[0].hp_current == 1
        assert sm.state.party[0].death_save_failures == 0


# ---------------------------------------------------------------------------
# End combat action
# ---------------------------------------------------------------------------


class TestEndCombatAction:
    def test_ends_combat_and_returns_summary(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon())],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))
        engine.start_combat(sm)
        sm.state.round_number = 3
        result = engine.execute_action(
            sm, Action(actor_id="p1", action_type=ActionType.END_COMBAT)
        )
        assert result.success
        assert not sm.state.in_combat
        assert "3 rodada" in result.message


# ---------------------------------------------------------------------------
# Unhandled action types
# ---------------------------------------------------------------------------


class TestUnhandledActions:
    def test_cast_spell_not_yet_implemented(self):
        sm = make_state_with(
            party=[make_creature(id="p1", weapon=make_weapon())],
            npcs=[make_creature(id="g1", is_character=False, hp=10, ac=12)],
        )
        engine = CombatEngine(rng=ScriptedRNG([10, 5]))
        engine.start_combat(sm)
        sm.state.initiative_order = ["p1", "g1"]
        sm.state.current_turn_index = 0
        result = engine.execute_action(
            sm,
            Action(
                actor_id="p1",
                action_type=ActionType.CAST_SPELL,
                params={"spell": "fireball"},
            ),
        )
        assert not result.success
        # p1 is an NPC, not a Character, so the engine refuses.
        assert "não é capaz" in result.message or "magias" in result.message.lower()


# ---------------------------------------------------------------------------
# Integration: full encounter
# ---------------------------------------------------------------------------


class TestFullEncounter:
    def test_pc_kills_goblin_in_two_hits(self):
        sm = make_state_with(
            party=[make_creature(id="p1", name="PC", weapon=make_weapon(), hp=20, ac=14)],
            npcs=[make_creature(id="g1", name="Goblin", is_character=False, hp=10, ac=12)],
        )
        # Rounds: init then 2x attack
        #  - turn 1: p1 attack hits for ~6 (roll 14 + 4 = 18 hit, dmg 1d8+2 = 7)
        #  - turn 2: g1 has no action in MVP; advance round
        #  - turn 3: p1 crits (roll 20) for big damage → goblin dies
        rolls = [
            10, 5,         # init
            14, 7,         # p1 attack turn 1: hit, dmg=7
            20, 6, 6,      # p1 attack turn 3: crit, dmg 2d8+2
        ]
        engine = CombatEngine(rng=ScriptedRNG(rolls))
        engine.start_combat(sm)

        # Turn 1: p1 attacks goblin
        assert engine.current_actor_id(sm) == "p1"
        result1 = engine.execute_action(
            sm, Action(actor_id="p1", action_type=ActionType.ATTACK, target_id="g1")
        )
        assert result1.mechanical["is_hit"]
        # Goblin still alive
        assert sm.state.npcs[0].hp_current > 0

        # Advance turn to goblin (no action implemented; just pass)
        engine.next_turn(sm)
        # Goblin's "turn" — no AI, advance back to p1
        engine.next_turn(sm)
        assert sm.state.round_number == 2

        # Turn 3: p1 crits
        result2 = engine.execute_action(
            sm, Action(actor_id="p1", action_type=ActionType.ATTACK, target_id="g1")
        )
        assert result2.mechanical["is_crit"]
        # Goblin should be down
        if sm.state.npcs[0].hp_current == 0:
            # Engine should auto-end combat on next_turn
            assert engine.next_turn(sm) is None
            assert not sm.state.in_combat

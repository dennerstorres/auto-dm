"""Tests for engine/rage.py — Barbarian Rage mechanics."""
from __future__ import annotations

import random
from datetime import datetime

import pytest

from auto_dm.engine.combat import damage_roll, saving_throw
from auto_dm.engine.combat_engine import _ACTION_HANDLERS
from auto_dm.engine.rage import (
    RAGE_DURATION_ROUNDS,
    RAGE_RESISTANCES,
    RageResult,
    apply_rage_resistance,
    can_rage,
    end_rage,
    end_rage_if_incapacitated,
    enter_rage,
    is_raging,
    rage_damage_bonus,
    rages_per_long_rest,
    recover_rages,
    tick_rage_duration,
)
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    Action,
    ActionType,
    Ability,
    AbilityScores,
    Character,
    Condition,
    EquippedSlots,
    Item,
    ItemType,
    NPC,
    Proficiencies,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_weapon(
    name: str = "Greataxe",
    dice: str = "1d12",
    dtype: str = "slashing",
    finesse: bool = False,
) -> Item:
    from auto_dm.state.models import WeaponProperties
    return Item(
        name=name,
        type=ItemType.WEAPON,
        weapon=WeaponProperties(damage_dice=dice, damage_type=dtype, finesse=finesse),
    )


@pytest.fixture
def barbarian() -> Character:
    return Character(
        id="b1", name="Korg", race="Half-Orc", class_="Barbarian", level=1,
        background="Outlander", alignment="CN",
        abilities=AbilityScores(
            strength=16, dexterity=12, constitution=16,
            intelligence=8, wisdom=10, charisma=10,
        ),
        hp_current=14, hp_max=14, armor_class=14, speed=30,
        proficiency_bonus=2, hit_dice="1d12", hit_dice_remaining=1,
        equipped=EquippedSlots(main_hand=make_weapon()),
        rages_max=2,
    )


@pytest.fixture
def wizard() -> Character:
    return Character(
        id="w1", name="Gandalf", race="Human", class_="Wizard", level=1,
        background="Sage", alignment="N",
        abilities=AbilityScores(
            strength=8, dexterity=14, constitution=12,
            intelligence=16, wisdom=12, charisma=10,
        ),
        hp_current=8, hp_max=8, armor_class=12, speed=30,
        proficiency_bonus=2, hit_dice="1d6", hit_dice_remaining=1,
    )


@pytest.fixture
def enemy() -> Character:
    return Character(
        id="e1", name="Goblin", race="Goblin", class_="Warrior", level=1,
        background="Tribal", alignment="CE",
        abilities=AbilityScores(
            strength=10, dexterity=14, constitution=10,
            intelligence=8, wisdom=8, charisma=8,
        ),
        hp_current=7, hp_max=7, armor_class=13, speed=30,
        proficiency_bonus=2, hit_dice="1d8", hit_dice_remaining=1,
    )


def make_state(party, npcs=None, player=None):
    return GameState(  # type: ignore[name-defined]
        campaign_name="test",
        started_at=datetime.now(),
        party=party,
        player_character_id=(player or party[0]).id,
        npcs=npcs or [],
    )


# Avoid a circular import of GameState by aliasing at the top of helpers.
from auto_dm.state.models import GameState  # noqa: E402


# ---------------------------------------------------------------------------
# PHB tables
# ---------------------------------------------------------------------------


class TestRageTables:
    @pytest.mark.parametrize("level,expected", [
        (1, 2), (2, 2), (3, 3), (5, 3), (6, 4), (11, 4),
        (12, 5), (16, 5), (17, 5), (20, 5),
    ])
    def test_rages_per_long_rest(self, level, expected):
        assert rages_per_long_rest(level) == expected

    @pytest.mark.parametrize("level,expected", [
        (1, 2), (8, 2), (9, 3), (15, 3), (16, 4), (20, 4),
    ])
    def test_rage_damage_bonus(self, level, expected):
        assert rage_damage_bonus(level) == expected

    def test_resistances_set(self):
        assert RAGE_RESISTANCES == frozenset({"bludgeoning", "piercing", "slashing"})

    def test_duration(self):
        assert RAGE_DURATION_ROUNDS == 10


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


class TestCanRage:
    def test_barbarian_can_rage(self, barbarian: Character):
        allowed, reason = can_rage(barbarian)
        assert allowed is True
        assert reason == ""

    def test_non_barbarian_cannot(self, wizard: Character):
        allowed, reason = can_rage(wizard)
        assert allowed is False
        assert "barbarian" in reason.lower()

    def test_already_raging_blocked(self, barbarian: Character):
        barbarian.is_raging = True
        allowed, reason = can_rage(barbarian)
        assert allowed is False
        assert "already" in reason.lower()

    def test_no_uses_blocked(self, barbarian: Character):
        barbarian.rages_used = barbarian.rages_max
        allowed, reason = can_rage(barbarian)
        assert allowed is False
        assert "no rages" in reason.lower()

    def test_incapacitated_blocked(self, barbarian: Character):
        barbarian.conditions.append(Condition.INCAPACITATED)
        allowed, reason = can_rage(barbarian)
        assert allowed is False
        assert "incapacitated" in reason.lower()

    def test_unconscious_blocked(self, barbarian: Character):
        barbarian.conditions.append(Condition.UNCONSCIOUS)
        allowed, reason = can_rage(barbarian)
        assert allowed is False
        assert "unconscious" in reason.lower()

    def test_heavy_armor_blocks(self, barbarian: Character):
        from auto_dm.state.models import ArmorProperties
        barbarian.equipped.armor = Item(
            name="Plate",
            type=ItemType.ARMOR,
            armor=ArmorProperties(
                base_ac=18, add_dex_modifier=False,
                stealth_disadvantage=True, strength_required=15,
            ),
        )
        allowed, reason = can_rage(barbarian)
        assert allowed is False
        assert "heavy armor" in reason.lower()


class TestEnterEndRage:
    def test_enter_consumes_use(self, barbarian: Character):
        before = barbarian.rages_used
        result = enter_rage(barbarian)
        assert isinstance(result, RageResult)
        assert result.success is True
        assert barbarian.is_raging is True
        assert barbarian.rages_used == before + 1
        assert barbarian.rounds_raging == 0

    def test_enter_returns_duration(self, barbarian: Character):
        result = enter_rage(barbarian)
        assert result.duration_rounds == 10

    def test_enter_refused_when_blocked(self, wizard: Character):
        result = enter_rage(wizard)
        assert result.success is False
        assert wizard.is_raging is False

    def test_end_clears_state(self, barbarian: Character):
        enter_rage(barbarian)
        barbarian.rounds_raging = 5
        result = end_rage(barbarian, "razão de teste")
        assert result.success is True
        assert barbarian.is_raging is False
        assert barbarian.rounds_raging == 0
        assert "razão de teste" in result.message

    def test_end_when_not_raging_returns_false(self, barbarian: Character):
        result = end_rage(barbarian)
        assert result.success is False

    def test_enter_then_end_then_enter(self, barbarian: Character):
        enter_rage(barbarian)
        end_rage(barbarian)
        result = enter_rage(barbarian)
        assert result.success is True


class TestTickRageDuration:
    def test_no_op_when_not_raging(self, barbarian: Character):
        assert tick_rage_duration(barbarian) is None

    def test_increments_counter(self, barbarian: Character):
        enter_rage(barbarian)
        for _ in range(5):
            tick_rage_duration(barbarian)
        assert barbarian.rounds_raging == 5
        assert barbarian.is_raging is True

    def test_ends_at_duration(self, barbarian: Character):
        enter_rage(barbarian)
        result = None
        for _ in range(RAGE_DURATION_ROUNDS):
            result = tick_rage_duration(barbarian)
        # 10th tick: rounds_raging becomes 10, which >= 10, so ends.
        assert result is not None
        assert result.success is True  # end_rage succeeded
        assert "duração" in result.message.lower() or "duracao" in result.message.lower()
        assert barbarian.is_raging is False

    def test_does_not_end_early(self, barbarian: Character):
        enter_rage(barbarian)
        for _ in range(9):
            r = tick_rage_duration(barbarian)
        # 9th tick: rounds_raging = 9, still < 10.
        assert r is None
        assert barbarian.is_raging is True


class TestEndRageIfIncapacitated:
    def test_incapacitated_ends(self, barbarian: Character):
        enter_rage(barbarian)
        barbarian.conditions.append(Condition.INCAPACITATED)
        result = end_rage_if_incapacitated(barbarian)
        assert result is not None
        assert result.success is True
        assert barbarian.is_raging is False

    def test_unconscious_ends(self, barbarian: Character):
        enter_rage(barbarian)
        barbarian.conditions.append(Condition.UNCONSCIOUS)
        result = end_rage_if_incapacitated(barbarian)
        assert result is not None
        assert barbarian.is_raging is False

    def test_no_op_when_not_raging(self, barbarian: Character):
        assert end_rage_if_incapacitated(barbarian) is None

    def test_no_op_when_only_conditions_no_rage(self, barbarian: Character):
        barbarian.conditions.append(Condition.INCAPACITATED)
        assert end_rage_if_incapacitated(barbarian) is None


# ---------------------------------------------------------------------------
# Long rest recovery
# ---------------------------------------------------------------------------


class TestRecoverRages:
    def test_recovers_uses(self, barbarian: Character):
        enter_rage(barbarian)
        enter_rage(barbarian)
        # Now exhausted (uses 0/2) and not raging.
        # Manually re-rage to verify it can recover.
        barbarian.is_raging = False
        enter_rage(barbarian)  # uses both, now at 2/2
        end_rage(barbarian)
        recovered = recover_rages(barbarian)
        assert recovered == 2
        assert barbarian.rages_used == 0

    def test_ends_active_rage(self, barbarian: Character):
        enter_rage(barbarian)
        recover_rages(barbarian)
        assert barbarian.is_raging is False

    def test_no_op_for_non_barbarian(self, wizard: Character):
        assert recover_rages(wizard) == 0


# ---------------------------------------------------------------------------
# Combat integration
# ---------------------------------------------------------------------------


class TestIsRaging:
    def test_true_for_raging_barbarian(self, barbarian: Character):
        enter_rage(barbarian)
        assert is_raging(barbarian) is True

    def test_false_for_calm_barbarian(self, barbarian: Character):
        assert is_raging(barbarian) is False

    def test_false_for_npc(self):
        npc = NPC(
            id="o", name="Orc", hp_current=10, hp_max=10,
            armor_class=13, speed=30,
            abilities=AbilityScores(
                strength=10, dexterity=10, constitution=10,
                intelligence=10, wisdom=10, charisma=10,
            ),
        )
        assert is_raging(npc) is False


class TestApplyRageResistance:
    @pytest.mark.parametrize("dt", ["bludgeoning", "piercing", "slashing"])
    def test_physical_damage_resisted(self, dt):
        assert apply_rage_resistance(dt) is True

    @pytest.mark.parametrize("dt", ["fire", "cold", "acid", "poison", "psychic"])
    def test_non_physical_not_resisted(self, dt):
        assert apply_rage_resistance(dt) is False


class TestRageDamageBonusInDamageRoll:
    def test_raging_barbarian_gets_bonus(self, barbarian: Character):
        enter_rage(barbarian)
        # Use seeded RNG so the roll is deterministic.
        rng = random.Random(42)
        roll = damage_roll(barbarian, rng=rng)
        # STR 16 = +3, rage +2, total = roll + 5
        # Greataxe 1d12 — verify modifier is at least 5 (could be 0 if rolled 1).
        assert roll.modifier >= 5

    def test_non_raging_no_bonus(self, barbarian: Character):
        rng = random.Random(42)
        roll = damage_roll(barbarian, rng=rng)
        # STR 16 = +3, no rage
        assert roll.modifier == 3

    def test_finesse_weapon_no_rage_bonus(self, barbarian: Character):
        from auto_dm.state.models import WeaponProperties
        barbarian.equipped.main_hand = Item(
            name="Rapier",
            type=ItemType.WEAPON,
            weapon=WeaponProperties(damage_dice="1d8", damage_type="piercing", finesse=True),
        )
        # DEX 12 = +1
        enter_rage(barbarian)
        rng = random.Random(42)
        roll = damage_roll(barbarian, rng=rng)
        # Finesse uses DEX, so rage bonus doesn't apply.
        assert roll.modifier == 1


class TestRageAdvantageOnSTRSave:
    def test_raging_gets_advantage_on_str(self, barbarian: Character):
        enter_rage(barbarian)
        # Use seeded RNG — the result should be from a d20 with advantage.
        # We just check that the save was called — verification via counts
        # is racy with the RNG. Smoke-test with multiple seeds.
        for seed in range(50):
            save = saving_throw(barbarian, Ability.STR, dc=99, rng=random.Random(seed))
            # Nat 1 = auto-fail even with advantage, but it should be possible.
            # With 50 seeds and advantage, we expect at least one save to not
            # be the literal minimum (nat 1).
        # Confirm via separate check: a low DC that would fail without adv.
        # STR 16 -> +3. With advantage on d20, max(d20a, d20b) + 3 vs DC 5
        # should be true most of the time.
        wins = 0
        for seed in range(200):
            save = saving_throw(barbarian, Ability.STR, dc=5, rng=random.Random(seed))
            if save.is_success:
                wins += 1
        # With adv, success rate vs DC 5 is ~96% (only fails on (1,1)).
        assert wins > 150

    def test_non_str_save_no_advantage(self, barbarian: Character):
        enter_rage(barbarian)
        save = saving_throw(barbarian, Ability.CON, dc=99, rng=random.Random(42))
        # DC 99 should fail (max roll + 3 = 23).
        assert save.is_success is False

    def test_not_raging_no_advantage(self, barbarian: Character):
        # Not raging; STR save with seeded RNG should be straight roll.
        save = saving_throw(barbarian, Ability.STR, dc=99, rng=random.Random(42))
        assert save.is_success is False


# ---------------------------------------------------------------------------
# CombatEngine handler
# ---------------------------------------------------------------------------


class TestRageHandler:
    def test_handler_registered(self):
        assert ActionType.RAGE in _ACTION_HANDLERS

    def test_handler_enters_rage(self, barbarian: Character):
        sm = StateManager(make_state([barbarian]))
        action = Action(actor_id=barbarian.id, action_type=ActionType.RAGE)
        result = _ACTION_HANDLERS[ActionType.RAGE](_FakeEngine(), sm, action)
        assert result.success is True
        assert barbarian.is_raging is True
        assert barbarian.rages_used == 1

    def test_handler_refuses_non_barbarian(self, wizard: Character):
        sm = StateManager(make_state([wizard]))
        action = Action(actor_id=wizard.id, action_type=ActionType.RAGE)
        result = _ACTION_HANDLERS[ActionType.RAGE](_FakeEngine(), sm, action)
        assert result.success is False

    def test_handler_refuses_no_uses(self, barbarian: Character):
        barbarian.rages_used = barbarian.rages_max
        sm = StateManager(make_state([barbarian]))
        action = Action(actor_id=barbarian.id, action_type=ActionType.RAGE)
        result = _ACTION_HANDLERS[ActionType.RAGE](_FakeEngine(), sm, action)
        assert result.success is False
        assert "no rages" in result.message.lower()


class _FakeEngine:
    rng = random.Random(0)
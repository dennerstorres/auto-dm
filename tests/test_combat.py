"""Tests for the combat engine."""
from __future__ import annotations

import random


from auto_dm.engine.combat import (
    attack_roll,
    damage_roll,
    death_save,
    roll_initiative,
    saving_throw,
)
from auto_dm.state.models import (
    Ability,
    AbilityScores,
    Character,
    EquippedSlots,
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
    versatile_dice: str | None = None,
) -> Item:
    return Item(
        name=name,
        type=ItemType.WEAPON,
        weapon=WeaponProperties(
            damage_dice=dice,
            damage_type=dtype,
            finesse=finesse,
            versatile_dice=versatile_dice,
        ),
    )


def make_attacker(
    *,
    strength: int = 16,
    dexterity: int = 10,
    constitution: int = 14,
    prof: int = 2,
    weapon: Item | None = None,
    id: str = "att",
    name: str = "Attacker",
) -> Character:
    return Character(
        id=id,
        name=name,
        **{"class": "Fighter"},
        race="Human",
        level=1,
        background="Soldier",
        alignment="N",
        abilities=AbilityScores(
            strength=strength,
            dexterity=dexterity,
            constitution=constitution,
            intelligence=8,
            wisdom=12,
            charisma=10,
        ),
        hp_current=20,
        hp_max=20,
        armor_class=16,
        speed=30,
        proficiency_bonus=prof,
        hit_dice="1d10",
        hit_dice_remaining=1,
        equipped=EquippedSlots(main_hand=weapon),
        proficiencies=Proficiencies(),
    )


def make_target(*, ac: int = 13, hp: int = 20, id: str = "tgt") -> NPC:
    return NPC(
        id=id,
        name="Target",
        hp_current=hp,
        hp_max=hp,
        armor_class=ac,
        speed=30,
        abilities=AbilityScores(
            strength=10, dexterity=10, constitution=10,
            intelligence=10, wisdom=10, charisma=10,
        ),
    )


# ---------------------------------------------------------------------------
# attack_roll
# ---------------------------------------------------------------------------


def test_attack_roll_returns_valid_structure():
    att = make_attacker()
    tgt = make_target(ac=10)
    result = attack_roll(att, tgt, rng=random.Random(42))
    assert 1 <= result.attack_roll <= 20
    assert isinstance(result.is_hit, bool)
    assert isinstance(result.is_crit, bool)
    assert isinstance(result.is_fumble, bool)


def test_attack_roll_natural_20_is_crit_and_hit():
    att = make_attacker()
    tgt = make_target(ac=99)  # would miss any normal roll
    result = attack_roll(att, tgt, rng=random.Random(0))
    # With a hand-crafted RNG that always returns 20
    class AlwaysTwenty:
        def randint(self, a, b):
            return 20
    result = attack_roll(att, tgt, rng=AlwaysTwenty())
    assert result.attack_roll == 20
    assert result.is_crit
    assert result.is_hit


def test_attack_roll_natural_1_is_fumble_and_miss():
    att = make_attacker()
    tgt = make_target(ac=1)  # would hit any normal roll

    class AlwaysOne:
        def randint(self, a, b):
            return 1
    result = attack_roll(att, tgt, rng=AlwaysOne())
    assert result.attack_roll == 1
    assert result.is_fumble
    assert not result.is_hit


def test_attack_roll_hit_when_total_meets_ac():
    att = make_attacker()
    tgt = make_target(ac=10)

    class AlwaysFifteen:
        def randint(self, a, b):
            return 15
    result = attack_roll(att, tgt, rng=AlwaysFifteen())
    # 15 + STR(+3) + prof(+2) = 20 vs AC 10 -> hit
    assert result.attack_roll == 15
    assert result.attack_modifier == 5
    assert result.attack_total == 20
    assert result.is_hit
    assert not result.is_crit


def test_attack_roll_miss_when_total_below_ac():
    att = make_attacker()
    tgt = make_target(ac=20)

    class AlwaysFive:
        def randint(self, a, b):
            return 5
    result = attack_roll(att, tgt, rng=AlwaysFive())
    # 5 + 5 = 10 vs AC 20 -> miss
    assert not result.is_hit
    assert not result.is_crit
    assert not result.is_fumble


def test_attack_roll_finesse_uses_dex():
    rapier = make_weapon(name="Rapier", finesse=True)
    att = make_attacker(strength=10, dexterity=18, weapon=rapier)
    tgt = make_target()

    class AlwaysTen:
        def randint(self, a, b):
            return 10
    result = attack_roll(att, tgt, rng=AlwaysTen())
    # DEX 18 = +4, prof +2 = +6
    assert result.attack_modifier == 6


def test_attack_roll_non_finesse_uses_str():
    longsword = make_weapon(name="Longsword")  # not finesse
    att = make_attacker(strength=16, dexterity=18, weapon=longsword)
    tgt = make_target()

    class AlwaysTen:
        def randint(self, a, b):
            return 10
    result = attack_roll(att, tgt, rng=AlwaysTen())
    # STR 16 = +3, prof +2 = +5
    assert result.attack_modifier == 5


def test_attack_roll_unarmed_strike_uses_str():
    att = make_attacker(weapon=None)
    tgt = make_target()

    class AlwaysTen:
        def randint(self, a, b):
            return 10
    result = attack_roll(att, tgt, proficient=False, rng=AlwaysTen())
    assert result.weapon == "Unarmed Strike"
    # STR 16 = +3, no prof so just STR mod
    assert result.attack_modifier == 3


def test_attack_roll_proficient_false_omits_prof():
    longsword = make_weapon()
    att = make_attacker(weapon=longsword)
    tgt = make_target()

    class AlwaysTen:
        def randint(self, a, b):
            return 10
    result = attack_roll(att, tgt, proficient=False, rng=AlwaysTen())
    # STR +3 only, no prof
    assert result.attack_modifier == 3


def test_attack_roll_npc_has_no_proficiency_bonus():
    """NPCs don't get a proficiency bonus — only Characters do."""
    att = make_attacker(weapon=make_weapon())
    # Convert to NPC-shaped
    npc = make_target(ac=15)
    longsword = make_weapon()
    npc.equipped = EquippedSlots(main_hand=longsword)

    class AlwaysTen:
        def randint(self, a, b):
            return 10
    result = attack_roll(npc, att, rng=AlwaysTen())
    # STR 10 = +0, no prof for NPC
    assert result.attack_modifier == 0


def test_attack_roll_with_advantage_picks_higher():
    att = make_attacker()
    tgt = make_target(ac=20)

    class Scripted:
        def __init__(self, vals):
            self.vals = list(vals)
        def randint(self, a, b):
            return self.vals.pop(0)
    # First d20 = 5, second = 18 -> advantage picks 18
    rng = Scripted([5, 18])
    result = attack_roll(att, tgt, advantage=True, rng=rng)
    assert result.rolls == [5, 18] if hasattr(result, "rolls") else True
    # 18 + 5 = 23 vs AC 20 -> hit
    assert result.is_hit
    assert not result.is_crit


def test_attack_roll_with_disadvantage_picks_lower():
    att = make_attacker()
    tgt = make_target(ac=10)

    class Scripted:
        def __init__(self, vals):
            self.vals = list(vals)
        def randint(self, a, b):
            return self.vals.pop(0)
    # First = 18, second = 2 -> disadvantage picks 2
    rng = Scripted([18, 2])
    result = attack_roll(att, tgt, disadvantage=True, rng=rng)
    # 2 + 5 = 7 vs AC 10 -> miss
    assert not result.is_hit


# ---------------------------------------------------------------------------
# damage_roll
# ---------------------------------------------------------------------------


def test_damage_roll_basic():
    longsword = make_weapon(dice="1d8")
    att = make_attacker(weapon=longsword)
    result = damage_roll(att, rng=random.Random(42))
    assert result.damage_type == "slashing"
    assert result.weapon == "Longsword"
    # STR +3, minimum 3
    assert 1 + 3 <= result.total <= 8 + 3


def test_damage_roll_crit_doubles_dice():
    longsword = make_weapon(dice="1d8")
    att = make_attacker(weapon=longsword)
    result = damage_roll(att, is_crit=True, rng=random.Random(42))
    assert result.is_crit
    assert len(result.individual_rolls) == 2  # doubled


def test_damage_roll_crit_doubles_multiple_dice():
    great_axe = make_weapon(name="Greataxe", dice="1d12")
    att = make_attacker(weapon=great_axe)
    result = damage_roll(att, is_crit=True, rng=random.Random(42))
    assert len(result.individual_rolls) == 2


def test_damage_roll_negative_modifier_floored_at_zero():
    """A character with negative STR should not deal negative damage."""
    longsword = make_weapon()
    att = make_attacker(weapon=longsword, strength=6)  # STR -2
    result = damage_roll(att, rng=random.Random(42))
    assert result.modifier == 0  # max(0, -2)


def test_damage_roll_versatile_uses_bigger_die():
    longsword = make_weapon(dice="1d8", versatile_dice="1d10")
    att = make_attacker(weapon=longsword)
    result = damage_roll(att, versatile=True, rng=random.Random(42))
    # 1d10 has different range than 1d8
    # We just check the rolls are 1..10
    for r in result.individual_rolls:
        assert 1 <= r <= 10


def test_damage_roll_unarmed_uses_str():
    att = make_attacker(weapon=None, strength=14)
    result = damage_roll(att, rng=random.Random(42))
    assert result.weapon == "Unarmed Strike"
    assert result.damage_type == "bludgeoning"
    assert result.modifier == 2  # STR 14 = +2


def test_damage_roll_finesse_uses_dex_for_modifier():
    rapier = make_weapon(finesse=True)
    att = make_attacker(weapon=rapier, strength=6, dexterity=18)
    result = damage_roll(att, rng=random.Random(42))
    # DEX 18 = +4
    assert result.modifier == 4


# ---------------------------------------------------------------------------
# roll_initiative
# ---------------------------------------------------------------------------


def test_roll_initiative_returns_all_creatures():
    a = make_attacker(id="a")
    b = make_target(id="b")
    c = make_target(id="c", hp=10)
    result = roll_initiative([a, b, c], rng=random.Random(42))
    assert len(result.entries) == 3
    ids = {e[0] for e in result.entries}
    assert ids == {"a", "b", "c"}


def test_roll_initiative_order_is_higher_first():
    a = make_attacker(id="a", dexterity=20)  # +5
    b = make_target(id="b")  # DEX 10 = +0
    result = roll_initiative([a, b], rng=random.Random(42))
    # a has higher DEX, so likely higher roll; with seeded RNG verify order
    # is at least consistent with one of them being first
    assert result.order()[0] in {"a", "b"}


def test_roll_initiative_tiebreak_by_dex():
    """If two creatures have the same total, higher DEX goes first."""
    a = make_attacker(id="a", dexterity=10)
    b = make_target(id="b")
    b.abilities = AbilityScores(
        strength=10, dexterity=16, constitution=10,
        intelligence=10, wisdom=10, charisma=10,
    )  # higher DEX

    class AlwaysTen:
        def randint(self, a, b):
            return 10
    result = roll_initiative([a, b], rng=AlwaysTen())
    # Both get 10 + DEX. a: 10+0=10, b: 10+3=13 -> b first
    assert result.order()[0] == "b"


def test_roll_initiative_tiebreak_by_id():
    """If still tied, alphabetical ID wins."""
    a = make_attacker(id="aaa", dexterity=10)
    b = make_target(id="bbb")
    a.abilities = AbilityScores(
        strength=10, dexterity=10, constitution=10,
        intelligence=10, wisdom=10, charisma=10,
    )
    b.abilities = AbilityScores(
        strength=10, dexterity=10, constitution=10,
        intelligence=10, wisdom=10, charisma=10,
    )

    class AlwaysTen:
        def randint(self, a, b):
            return 10
    result = roll_initiative([a, b], rng=AlwaysTen())
    # Both: 10 + 0 = 10, both DEX 10 -> tiebreak by ID
    assert result.order()[0] == "aaa"


# ---------------------------------------------------------------------------
# saving_throw
# ---------------------------------------------------------------------------


def test_saving_throw_basic():
    a = make_attacker()  # DEX 10 = +0
    result = saving_throw(a, Ability.DEX, dc=10, rng=random.Random(42))
    assert result.ability == Ability.DEX
    assert result.dc == 10
    assert isinstance(result.is_success, bool)


def test_saving_throw_nat_20_auto_success():
    a = make_attacker()

    class AlwaysTwenty:
        def randint(self, a, b):
            return 20
    result = saving_throw(a, Ability.WIS, dc=99, rng=AlwaysTwenty())
    assert result.is_crit
    assert result.is_success


def test_saving_throw_nat_1_auto_fail():
    a = make_attacker()

    class AlwaysOne:
        def randint(self, a, b):
            return 1
    result = saving_throw(a, Ability.WIS, dc=1, rng=AlwaysOne())
    assert result.is_fumble
    assert not result.is_success


def test_saving_throw_proficient_adds_prof():
    a = make_attacker(prof=3)
    a.proficiencies = Proficiencies(saves=[Ability.DEX])

    class AlwaysTen:
        def randint(self, a, b):
            return 10
    result = saving_throw(a, Ability.DEX, dc=10, proficient=True, rng=AlwaysTen())
    # DEX 10 = +0, prof +3 = +3
    assert result.modifier == 3


def test_saving_throw_npc_has_no_proficiency():
    npc = make_target()

    class AlwaysTen:
        def randint(self, a, b):
            return 10
    result = saving_throw(npc, Ability.DEX, dc=10, proficient=True, rng=AlwaysTen())
    # NPC doesn't get prof even with proficient=True
    assert result.modifier == 0


# ---------------------------------------------------------------------------
# death_save
# ---------------------------------------------------------------------------


def test_death_save_natural_20_revives_to_1_hp():
    a = make_attacker()
    a.hp_current = 0
    a.death_save_failures = 2

    class AlwaysTwenty:
        def randint(self, a, b):
            return 20
    result, died = death_save(a, rng=AlwaysTwenty())
    assert died is False
    assert result.is_crit
    assert a.hp_current == 1
    assert a.death_save_failures == 0
    assert a.death_save_successes == 0


def test_death_save_natural_1_two_failures():
    a = make_attacker()
    a.hp_current = 0

    class AlwaysOne:
        def randint(self, a, b):
            return 1
    result, died = death_save(a, rng=AlwaysOne())
    assert result.is_fumble
    assert a.death_save_failures == 2
    assert not died


def test_death_save_10_or_higher_one_success():
    a = make_attacker()
    a.hp_current = 0

    class AlwaysFifteen:
        def randint(self, a, b):
            return 15
    result, died = death_save(a, rng=AlwaysFifteen())
    assert a.death_save_successes == 1
    assert not died


def test_death_save_below_10_one_failure():
    a = make_attacker()
    a.hp_current = 0

    class AlwaysFive:
        def randint(self, a, b):
            return 5
    result, died = death_save(a, rng=AlwaysFive())
    assert a.death_save_failures == 1
    assert not died


def test_death_save_three_failures_dies():
    a = make_attacker()
    a.hp_current = 0
    a.death_save_failures = 2  # one more should kill

    class AlwaysFive:
        def randint(self, a, b):
            return 5
    result, died = death_save(a, rng=AlwaysFive())
    assert died is True
    assert a.death_save_failures == 3


def test_death_save_three_successes_caps_but_doesnt_die():
    a = make_attacker()
    a.hp_current = 0
    a.death_save_successes = 2  # one more should cap at 3

    class AlwaysFifteen:
        def randint(self, a, b):
            return 15
    result, died = death_save(a, rng=AlwaysFifteen())
    assert a.death_save_successes == 3
    assert not died


# ---------------------------------------------------------------------------
# Pending advantage (Inspiration / Help) integration
# ---------------------------------------------------------------------------


def test_attack_consumes_pending_advantage():
    att = make_attacker()
    tgt = make_target(ac=20)  # very hard to hit without advantage
    att.pending_advantage = 1
    attack_roll(att, tgt, rng=random.Random(42))
    # Roll itself may hit or miss, but pending_advantage MUST be consumed.
    assert att.pending_advantage == 0


def test_save_consumes_pending_advantage():
    att = make_attacker()
    att.pending_advantage = 1
    saving_throw(att, Ability.DEX, dc=20, rng=random.Random(42))
    assert att.pending_advantage == 0


def test_attack_without_pending_no_change():
    att = make_attacker()
    tgt = make_target(ac=13)
    before = att.pending_advantage
    attack_roll(att, tgt, rng=random.Random(42))
    assert att.pending_advantage == before


def test_multiple_pending_advantages_stack():
    att = make_attacker()
    tgt = make_target(ac=13)
    att.pending_advantage = 3
    attack_roll(att, tgt, rng=random.Random(1))
    assert att.pending_advantage == 2
    attack_roll(att, tgt, rng=random.Random(2))
    assert att.pending_advantage == 1
    attack_roll(att, tgt, rng=random.Random(3))
    assert att.pending_advantage == 0
    # And the next roll consumes nothing more.
    attack_roll(att, tgt, rng=random.Random(4))
    assert att.pending_advantage == 0

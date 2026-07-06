"""Phase 41b — reaction engine dispatch tests.

Covers eligibility (class/level/slot/known+prepared/reaction_available
gates per ReactionKind), resolution (Shield +5 AC + MM immunity,
Counterspell auto/ability-check, Hellish Rebuke slot+dmg, Healing Word
heal+revive, Uncanny Dodge halve-refund, Parry reduce + L7 cap),
publication of ``pending_reaction`` to the first eligible responder, and
the ``ActionType.REACTION`` handler round-trip. Also covers
``reaction_available`` reset at the start of the responder's turn.
"""
from __future__ import annotations

import random

from auto_dm.engine.actions import (
    OnAllyDown,
    OnDamageTaken,
    OnHitByAttack,
    OnSeeingSpellCast,
    ReactionKind,
)
from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.engine.reactions import (
    apply_reaction,
    eligible_reactions,
    publish_reaction_trigger,
)
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    Ability,
    AbilityScores,
    Action,
    ActionType,
    Character,
    GameState,
    NPC,
    Spellcasting,
)


# ============================================================================
# Helpers
# ============================================================================


def _wiz(*, level: int = 5, slots: dict[int, int] | None = None) -> Character:
    if slots is None:
        slots = {1: 4, 2: 3, 3: 2}
    return Character(
        id="wiz", name="Merlin", race="Human", class_="Wizard", level=level,
        background="Sage", alignment="LN",
        abilities=AbilityScores(
            strength=8, dexterity=12, constitution=12,
            intelligence=18, wisdom=10, charisma=10,
        ),
        hp_current=20, hp_max=20, armor_class=12, speed=30,
        proficiency_bonus=2, hit_dice="1d6", hit_dice_remaining=level,
        spellcasting=Spellcasting(
            ability=Ability.INT, save_dc=15, attack_bonus=7,
            cantrips_known=["Fire Bolt"],
            spells_prepared=["Shield", "Counterspell", "Magic Missile"],
            spell_slots=slots, spell_slots_max=dict(slots),
        ),
    )


def _warlock(*, level: int = 3) -> Character:
    # Warlock pact magic: L3 has 2 slots of 2nd level.
    return Character(
        id="wlk", name="Dax", race="Halfling", class_="Warlock", level=level,
        background="Charlatan", alignment="CN",
        abilities=AbilityScores(
            strength=8, dexterity=14, constitution=12,
            intelligence=10, wisdom=10, charisma=16,
        ),
        hp_current=18, hp_max=18, armor_class=13, speed=30,
        proficiency_bonus=2, hit_dice="1d8", hit_dice_remaining=level,
        spellcasting=Spellcasting(
            ability=Ability.CHA, save_dc=14, attack_bonus=6,
            cantrips_known=["Eldritch Blast"],
            spells_known=["Hellish Rebuke", "Charm Person"],
            spells_prepared=[],
            spell_slots={1: 0, 2: 2}, spell_slots_max={1: 0, 2: 2},
        ),
    )


def _cleric(*, level: int = 3) -> Character:
    return Character(
        id="clr", name="Mara", race="Human", class_="Cleric", level=level,
        background="Acolyte", alignment="LG",
        abilities=AbilityScores(
            strength=10, dexterity=10, constitution=12,
            intelligence=10, wisdom=16, charisma=10,
        ),
        hp_current=22, hp_max=22, armor_class=16, speed=30,
        proficiency_bonus=2, hit_dice="1d8", hit_dice_remaining=level,
        spellcasting=Spellcasting(
            ability=Ability.WIS, save_dc=13, attack_bonus=5,
            cantrips_known=["Sacred Flame"],
            spells_prepared=["Healing Word", "Cure Wounds", "Bless"],
            spell_slots={1: 4, 2: 2}, spell_slots_max={1: 4, 2: 2},
        ),
    )


def _rogue(*, level: int = 5) -> Character:
    return Character(
        id="rog", name="Lyra", race="Halfling", class_="Rogue", level=level,
        background="Urchin", alignment="CN",
        abilities=AbilityScores(
            strength=8, dexterity=18, constitution=12,
            intelligence=12, wisdom=12, charisma=10,
        ),
        hp_current=15, hp_max=28, armor_class=15, speed=30,
        proficiency_bonus=3, hit_dice="1d8", hit_dice_remaining=level,
    )


def _fighter(*, level: int = 7) -> Character:
    return Character(
        id="fgt", name="Garrick", race="Human", class_="Fighter", level=level,
        background="Soldier", alignment="LG",
        abilities=AbilityScores(
            strength=16, dexterity=12, constitution=14,
            intelligence=10, wisdom=10, charisma=10,
        ),
        hp_current=30, hp_max=40, armor_class=17, speed=30,
        proficiency_bonus=3, hit_dice="1d10", hit_dice_remaining=level,
    )


def _enemy(*, eid: str = "orc", hp: int = 20) -> NPC:
    return NPC(
        id=eid, name="Orc", hp_current=hp, hp_max=hp, armor_class=13, speed=30,
        abilities=AbilityScores(
            strength=16, dexterity=12, constitution=16,
            intelligence=7, wisdom=11, charisma=10,
        ),
    )


def _state(party: list[Character], npcs: list[NPC] | None = None,
           *, player_id: str | None = None) -> StateManager:
    from datetime import datetime
    pid = player_id or (party[0].id if party else "p1")
    return StateManager(GameState(
        campaign_name="rx-test",
        started_at=datetime.now(),
        party=party,
        npcs=npcs or [],
        player_character_id=pid,
    ))


# ============================================================================
# Eligibility
# ============================================================================


class TestEligibility:
    def test_shield_eligible_on_hit_to_self(self):
        w = _wiz()
        t = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=10)
        assert ReactionKind.SHIELD in eligible_reactions(w, t)

    def test_shield_not_eligible_when_hit_targets_other(self):
        w = _wiz()
        t = OnHitByAttack(target_id="other", attacker_id="orc", attack_damage=10)
        assert ReactionKind.SHIELD not in eligible_reactions(w, t)

    def test_counterspell_eligible_on_enemy_spell(self):
        w = _wiz()
        t = OnSeeingSpellCast(caster_id="enemy_mage", spell_name="Fireball", level=3)
        assert ReactionKind.COUNTERSPELL in eligible_reactions(w, t)

    def test_counterspell_not_eligible_on_own_spell(self):
        w = _wiz()
        t = OnSeeingSpellCast(caster_id="wiz", spell_name="Fireball", level=3)
        assert ReactionKind.COUNTERSPELL not in eligible_reactions(w, t)

    def test_counterspell_not_eligible_without_l3_slot(self):
        # Wizard with no 3rd-level slot can't cast Counterspell.
        w = _wiz(slots={1: 4, 2: 3})  # no level 3
        t = OnSeeingSpellCast(caster_id="e", spell_name="Fireball", level=3)
        assert ReactionKind.COUNTERSPELL not in eligible_reactions(w, t)

    def test_hellish_rebuke_eligible_on_damage_taken(self):
        wl = _warlock()
        t = OnDamageTaken(target_id="wlk", amount=6, source_id="orc")
        assert ReactionKind.HELLISH_REBUKE in eligible_reactions(wl, t)

    def test_healing_word_eligible_on_ally_down(self):
        c = _cleric()
        t = OnAllyDown(ally_id="rog")
        assert ReactionKind.HEALING_WORD in eligible_reactions(c, t)

    def test_uncanny_dodge_requires_level_5(self):
        young = _rogue(level=4)
        t = OnHitByAttack(target_id="rog", attacker_id="orc", attack_damage=10)
        assert ReactionKind.UNCANNY_DODGE not in eligible_reactions(young, t)
        grown = _rogue(level=5)
        assert ReactionKind.UNCANNY_DODGE in eligible_reactions(grown, t)

    def test_parry_eligible_for_fighter_melee_hit(self):
        f = _fighter(level=3)
        t = OnHitByAttack(
            target_id="fgt", attacker_id="orc", attack_damage=8, is_melee=True,
        )
        assert ReactionKind.PARRY in eligible_reactions(f, t)

    def test_reaction_available_gate_blocks_all(self):
        w = _wiz()
        w.reaction_available = False
        t = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=10)
        assert eligible_reactions(w, t) == []

    def test_not_prepared_blocks_spell_reaction(self):
        w = _wiz()
        w.spellcasting.spells_prepared.remove("Shield")
        t = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=10)
        assert ReactionKind.SHIELD not in eligible_reactions(w, t)

    def test_npc_not_eligible(self):
        npc = _enemy()
        t = OnHitByAttack(target_id="orc", attacker_id="wiz", attack_damage=5)
        assert eligible_reactions(npc, t) == []


# ============================================================================
# Resolution — spell reactions
# ============================================================================


class TestShieldResolution:
    def test_shield_applies_plus_five_ac_and_mm_immune(self):
        w = _wiz()
        sm = _state([w])
        eng = CombatEngine()
        before_slots = w.spellcasting.spell_slots[1]
        t = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=10)
        res = apply_reaction(eng, sm, "wiz", ReactionKind.SHIELD, t)
        assert res.success
        assert w.pending_ac_bonus == 5
        assert w.shield_active is True
        assert w.reaction_available is False
        assert w.spellcasting.spell_slots[1] == before_slots - 1
        assert res.consumed_slot_level == 1

    def test_shield_stacks_bonus_if_recast_before_clear(self):
        # Not a real game path (reaction economy blocks it), but the
        # arithmetic should add, not overwrite.
        w = _wiz()
        w.pending_ac_bonus = 2
        sm = _state([w])
        eng = CombatEngine()
        t = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=10)
        apply_reaction(eng, sm, "wiz", ReactionKind.SHIELD, t)
        assert w.pending_ac_bonus == 7  # 2 + 5


class TestCounterspellResolution:
    def test_auto_cancels_spell_level_3_or_lower(self):
        w = _wiz()
        sm = _state([w])
        eng = CombatEngine()
        t = OnSeeingSpellCast(caster_id="e", spell_name="Fireball", level=3)
        res = apply_reaction(eng, sm, "wiz", ReactionKind.COUNTERSPELL, t)
        assert res.success
        assert res.spell_cancelled is True
        assert w.spellcasting.spell_slots[3] == 1  # one of two consumed

    def test_high_level_spell_needs_ability_check_success(self):
        w = _wiz()  # INT 18 → +4 mod
        sm = _state([w])
        eng = CombatEngine()
        t = OnSeeingSpellCast(caster_id="e", spell_name="Fireball", level=5)
        # DC 15. check_roll 15 → success.
        res = apply_reaction(
            eng, sm, "wiz", ReactionKind.COUNTERSPELL, t, check_roll=15,
        )
        assert res.spell_cancelled is True

    def test_high_level_spell_ability_check_failure(self):
        w = _wiz()
        sm = _state([w])
        eng = CombatEngine()
        t = OnSeeingSpellCast(caster_id="e", spell_name="Fireball", level=5)
        res = apply_reaction(
            eng, sm, "wiz", ReactionKind.COUNTERSPELL, t, check_roll=14,
        )
        assert res.spell_cancelled is False
        # Reaction + slot still consumed even on failure (PHB p. 227).
        assert w.reaction_available is False


class TestHellishRebukeResolution:
    def test_deals_fire_damage_to_source_and_consumes_slot(self):
        wl = _warlock()
        enemy = _enemy(eid="orc", hp=30)
        sm = _state([wl], [enemy])
        eng = CombatEngine(rng=random.Random(0))
        t = OnDamageTaken(target_id="wlk", amount=6, source_id="orc")
        res = apply_reaction(eng, sm, "wlk", ReactionKind.HELLISH_REBUKE, t)
        assert res.success
        assert res.rebuke_damage > 0
        assert res.rebuke_target_hp == 30 - res.rebuke_damage
        assert enemy.hp_current == 30 - res.rebuke_damage
        assert wl.reaction_available is False
        # Warlock cast at min slot 1; pact magic consumes a 2nd-level slot
        # here (lowest >= 1 available). Either way a slot is gone.
        assert res.consumed_slot_level >= 1


class TestHealingWordResolution:
    def test_revives_downed_ally(self):
        c = _cleric()
        rog = _rogue()
        rog.hp_current = 0  # downed
        sm = _state([c, rog], player_id="clr")
        eng = CombatEngine(rng=random.Random(0))
        t = OnAllyDown(ally_id="rog")
        res = apply_reaction(eng, sm, "clr", ReactionKind.HEALING_WORD, t)
        assert res.success
        assert res.healed_to is not None and res.healed_to > 0
        assert rog.hp_current == res.healed_to
        assert c.reaction_available is False


# ============================================================================
# Resolution — feature reactions
# ============================================================================


class TestUncannyDodgeResolution:
    def test_halves_damage_as_refund(self):
        r = _rogue()
        r.hp_current = 10  # already took the hit (20 dmg from max 30)
        sm = _state([r])
        eng = CombatEngine()
        t = OnHitByAttack(target_id="rog", attacker_id="orc", attack_damage=20)
        res = apply_reaction(eng, sm, "rog", ReactionKind.UNCANNY_DODGE, t)
        assert res.success
        assert res.damage_modified_to == 10  # 20 - 10 refund
        # Refunded half: HP goes up by 10.
        assert r.hp_current == 20
        assert r.reaction_available is False

    def test_refund_floors_odd_damage(self):
        r = _rogue()
        r.hp_current = 5
        sm = _state([r])
        eng = CombatEngine()
        t = OnHitByAttack(target_id="rog", attacker_id="orc", attack_damage=11)
        res = apply_reaction(eng, sm, "rog", ReactionKind.UNCANNY_DODGE, t)
        # 11 // 2 = 5 refunded → modified to 6.
        assert res.damage_modified_to == 6
        assert r.hp_current == 10


class TestParryResolution:
    def test_reduces_damage_and_caps_below_attack(self):
        f = _fighter(level=7)  # prof 3, die 10 at L7
        f.hp_current = 20
        sm = _state([f])
        eng = CombatEngine(rng=random.Random(0))
        t = OnHitByAttack(
            target_id="fgt", attacker_id="orc", attack_damage=8, is_melee=True,
        )
        res = apply_reaction(eng, sm, "fgt", ReactionKind.PARRY, t)
        assert res.success
        # Reduction = d10 + 3, capped at 8.
        assert res.mechanical["reduction"] <= 8
        assert res.damage_modified_to == 8 - res.mechanical["reduction"]
        assert f.reaction_available is False

    def test_die_size_grows_at_level_7(self):
        young = _fighter(level=3)
        sm_young = _state([young])
        eng = CombatEngine(rng=random.Random(0))
        t = OnHitByAttack(
            target_id="fgt", attacker_id="orc", attack_damage=20, is_melee=True,
        )
        r_young = apply_reaction(eng, sm_young, "fgt", ReactionKind.PARRY, t)
        assert r_young.mechanical["die"] == 8

        grown = _fighter(level=7)
        sm_grown = _state([grown])
        r_grown = apply_reaction(eng, sm_grown, "fgt", ReactionKind.PARRY, t)
        assert r_grown.mechanical["die"] == 10


# ============================================================================
# Reaction economy + handler round-trip
# ============================================================================


class TestReactionEconomy:
    def test_one_reaction_per_round(self):
        w = _wiz()
        sm = _state([w])
        eng = CombatEngine()
        t1 = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=5)
        r1 = apply_reaction(eng, sm, "wiz", ReactionKind.SHIELD, t1)
        assert r1.success
        # Second trigger same round: no reaction left.
        t2 = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=5)
        r2 = apply_reaction(eng, sm, "wiz", ReactionKind.SHIELD, t2)
        assert not r2.success
        assert r2.reason == "not_eligible"

    def test_unknown_responder_fails_gracefully(self):
        sm = _state([_wiz()])
        eng = CombatEngine()
        t = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=5)
        res = apply_reaction(eng, sm, "ghost", ReactionKind.SHIELD, t)
        assert not res.success
        assert res.reason == "unknown_responder"


class TestReactionHandlerRoundTrip:
    def test_action_type_reaction_resolves_shield(self):
        w = _wiz()
        sm = _state([w])
        eng = CombatEngine()
        trigger = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=10)
        action = Action(
            actor_id="wiz", action_type=ActionType.REACTION,
            params={"kind": "shield", "trigger": trigger.to_payload()},
        )
        result = eng.execute_action(sm, action)
        assert result.success
        assert w.pending_ac_bonus == 5
        assert result.mechanical["reaction_kind"] == "shield"
        assert result.mechanical["consumed_slot_level"] == 1

    def test_handler_rejects_unknown_kind(self):
        w = _wiz()
        sm = _state([w])
        eng = CombatEngine()
        action = Action(
            actor_id="wiz", action_type=ActionType.REACTION,
            params={"kind": "nope", "trigger": {}},
        )
        result = eng.execute_action(sm, action)
        assert not result.success
        assert "desconhecido" in result.message.lower()

    def test_handler_rejects_not_eligible_kind(self):
        # Wizard trying Parry — not eligible.
        w = _wiz()
        sm = _state([w])
        eng = CombatEngine()
        trigger = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=10)
        action = Action(
            actor_id="wiz", action_type=ActionType.REACTION,
            params={"kind": "parry", "trigger": trigger.to_payload()},
        )
        result = eng.execute_action(sm, action)
        assert not result.success
        assert result.mechanical["reason"] == "not_eligible"


# ============================================================================
# Publication + round reset
# ============================================================================


class TestPublishAndReset:
    def test_publish_stamps_pending_reaction_on_player(self):
        w = _wiz()
        w.is_player = True
        sm = _state([w])
        t = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=10)
        ids = publish_reaction_trigger(sm, t, fired_at=1000)
        assert ids == ["wiz"]
        assert w.pending_reaction is not None
        assert "shield" in w.pending_reaction["reactions_eligible"]
        assert w.pending_reaction["fired_at"] == 1000

    def test_publish_returns_empty_when_no_one_eligible(self):
        sm = _state([_fighter(level=1)])  # no reactions available
        t = OnSeeingSpellCast(caster_id="e", spell_name="Fireball", level=3)
        ids = publish_reaction_trigger(sm, t, fired_at=1000)
        assert ids == []

    def test_publish_without_epoch_is_noop(self):
        w = _wiz()
        sm = _state([w])
        t = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=10)
        ids = publish_reaction_trigger(sm, t, fired_at=None)
        assert ids == []
        assert w.pending_reaction is None

    def test_reaction_refreshes_at_start_of_own_turn(self):
        # Start combat, take a reaction, then advance turns until the
        # responder's turn comes up again → reaction_available back to True.
        w = _wiz()
        w.is_player = True
        enemy = _enemy()
        sm = _state([w], [enemy])
        eng = CombatEngine(rng=random.Random(0))
        eng.start_combat(sm)
        # Spend the reaction out of turn.
        w.reaction_available = False
        # Advance one full round so the wizard's turn starts again.
        # The initiative order has 2 actors; 2 next_turn calls wrap a round.
        order = sm.state.initiative_order
        for _ in range(len(order)):
            eng.next_turn(sm)
        assert w.reaction_available is True

    def test_shield_clears_at_start_of_caster_turn(self):
        w = _wiz()
        w.is_player = True
        enemy = _enemy()
        sm = _state([w], [enemy])
        eng = CombatEngine(rng=random.Random(0))
        eng.start_combat(sm)
        w.pending_ac_bonus = 5
        w.shield_active = True
        order = sm.state.initiative_order
        for _ in range(len(order)):
            eng.next_turn(sm)
        assert w.pending_ac_bonus == 0
        assert w.shield_active is False

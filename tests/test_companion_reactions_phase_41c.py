"""Phase 41c — companion reaction heuristic + OnAllyDown wiring + DM prompt.

The endpoint and web modal were already shipped in earlier 41c commits;
this phase closes the loop on three remaining pieces:

1. ``engine.companion_reactions.choose_companion_reaction`` — a small
   defensive heuristic (Healing Word always on ally-down; below half HP
   prefer Uncanny Dodge → Parry → Shield; otherwise decline).
2. ``engine.reactions.publish_reaction_trigger`` — when called with an
   engine, companions auto-resolve in place and *don't* stash a prompt;
   the player path is unchanged but now guards against clobber.
3. ``engine.combat_engine._handle_attack`` — publishes ``OnAllyDown``
   when a party member's HP hits 0 so a cleric/druid/bard can revive.
4. ``agents.prompts.DM_SYSTEM_PROMPT`` — a Reações section instructing
   the DM to narrate the *trigger* and let the engine/player resolve.
"""
from __future__ import annotations

import random

from auto_dm.agents.prompts import DM_SYSTEM_PROMPT
from auto_dm.engine.actions import (
    OnAllyDown,
    OnHitByAttack,
    ReactionKind,
)
from auto_dm.engine.combat import AttackResult, DamageRoll
from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.engine.companion_reactions import (
    auto_resolve_companion_reaction,
    choose_companion_reaction,
)
from auto_dm.engine.reactions import (
    eligible_reactions,
    publish_reaction_trigger,
)
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    Ability,
    AbilityScores,
    Character,
    GameState,
    NPC,
    Spellcasting,
)


# ============================================================================
# Helpers (mirrors test_reactions_phase_41b.py)
# ============================================================================


def _wiz(*, level: int = 5, hp_current: int = 20, hp_max: int = 20) -> Character:
    return Character(
        id="wiz", name="Merlin", race="Human", class_="Wizard", level=level,
        background="Sage", alignment="LN",
        abilities=AbilityScores(
            strength=8, dexterity=12, constitution=12,
            intelligence=18, wisdom=10, charisma=10,
        ),
        hp_current=hp_current, hp_max=hp_max, armor_class=12, speed=30,
        proficiency_bonus=2, hit_dice="1d6", hit_dice_remaining=level,
        spellcasting=Spellcasting(
            ability=Ability.INT, save_dc=15, attack_bonus=7,
            cantrips_known=["Fire Bolt"],
            spells_prepared=["Shield", "Counterspell", "Magic Missile"],
            spell_slots={1: 4, 2: 3, 3: 2},
            spell_slots_max={1: 4, 2: 3, 3: 2},
        ),
    )


def _cleric(*, hp_current: int = 22, hp_max: int = 22) -> Character:
    return Character(
        id="clr", name="Mara", race="Human", class_="Cleric", level=3,
        background="Acolyte", alignment="LG",
        abilities=AbilityScores(
            strength=10, dexterity=10, constitution=12,
            intelligence=10, wisdom=16, charisma=10,
        ),
        hp_current=hp_current, hp_max=hp_max, armor_class=16, speed=30,
        proficiency_bonus=2, hit_dice="1d8", hit_dice_remaining=3,
        spellcasting=Spellcasting(
            ability=Ability.WIS, save_dc=13, attack_bonus=5,
            cantrips_known=["Sacred Flame"],
            spells_prepared=["Healing Word", "Cure Wounds", "Bless"],
            spell_slots={1: 4, 2: 2}, spell_slots_max={1: 4, 2: 2},
        ),
    )


def _rogue(*, hp_current: int = 28, hp_max: int = 28) -> Character:
    return Character(
        id="rog", name="Lyra", race="Halfling", class_="Rogue", level=5,
        background="Urchin", alignment="CN",
        abilities=AbilityScores(
            strength=8, dexterity=18, constitution=12,
            intelligence=12, wisdom=12, charisma=10,
        ),
        hp_current=hp_current, hp_max=hp_max, armor_class=15, speed=30,
        proficiency_bonus=3, hit_dice="1d8", hit_dice_remaining=5,
    )


def _fighter(*, hp_current: int = 40, hp_max: int = 40) -> Character:
    return Character(
        id="fgt", name="Garrick", race="Human", class_="Fighter", level=7,
        background="Soldier", alignment="LG",
        abilities=AbilityScores(
            strength=16, dexterity=12, constitution=14,
            intelligence=10, wisdom=10, charisma=10,
        ),
        hp_current=hp_current, hp_max=hp_max, armor_class=17, speed=30,
        proficiency_bonus=3, hit_dice="1d10", hit_dice_remaining=7,
    )


def _enemy() -> NPC:
    return NPC(
        id="orc", name="Orc", hp_current=20, hp_max=20, armor_class=13,
        speed=30,
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
        campaign_name="rx-test-41c",
        started_at=datetime.now(),
        party=party, npcs=npcs or [],
        player_character_id=pid,
    ))


def _forge_hit(damage: int = 5) -> tuple[AttackResult, DamageRoll]:
    """Build a forced-hit AttackResult + matching DamageRoll for monkeypatch."""
    atk = AttackResult(
        attacker_id="orc", target_id="wiz",
        attack_roll=18, attack_modifier=5, attack_total=23,
        target_ac=12, is_hit=True, is_crit=False, is_fumble=False,
        weapon="Greataxe",
    )
    dmg = DamageRoll(
        total=damage, damage_type="slashing", weapon="Greataxe",
        is_crit=False, individual_rolls=[damage], modifier=0,
    )
    return atk, dmg


# ============================================================================
# Choose heuristic
# ============================================================================


class TestChooseCompanionReaction:
    def test_healing_word_taken_when_eligible(self):
        """An OnAllyDown trigger with Healing Word eligible always revives."""
        c = _cleric(hp_current=22)  # full HP, but revival trumps self-defense
        t = OnAllyDown(ally_id="rog")
        eligible = eligible_reactions(c, t)
        assert ReactionKind.HEALING_WORD in eligible
        assert choose_companion_reaction(c, t, eligible) == ReactionKind.HEALING_WORD

    def test_low_hp_prefers_uncanny_dodge(self):
        """Below half HP and hit by an attack → Uncanny Dodge first (refund)."""
        r = _rogue(hp_current=10, hp_max=28)  # 10/28 = ~36%
        t = OnHitByAttack(target_id="rog", attacker_id="orc", attack_damage=8)
        eligible = eligible_reactions(r, t)
        assert ReactionKind.UNCANNY_DODGE in eligible
        assert choose_companion_reaction(r, t, eligible) == ReactionKind.UNCANNY_DODGE

    def test_low_hp_falls_back_to_parry(self):
        """No Uncanny Dodge → Parry (also a refund)."""
        f = _fighter(hp_current=15, hp_max=40)  # 37.5%
        t = OnHitByAttack(
            target_id="fgt", attacker_id="orc", attack_damage=8, is_melee=True,
        )
        eligible = eligible_reactions(f, t)
        assert ReactionKind.PARRY in eligible
        assert ReactionKind.UNCANNY_DODGE not in eligible
        assert choose_companion_reaction(f, t, eligible) == ReactionKind.PARRY

    def test_low_hp_falls_back_to_shield(self):
        """No UD/Parry → Shield (helps vs subsequent attacks this round)."""
        w = _wiz(hp_current=8, hp_max=20)  # 40%
        t = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=6)
        eligible = eligible_reactions(w, t)
        assert ReactionKind.SHIELD in eligible
        assert ReactionKind.UNCANNY_DODGE not in eligible
        assert ReactionKind.PARRY not in eligible
        assert choose_companion_reaction(w, t, eligible) == ReactionKind.SHIELD

    def test_healthy_companion_declines(self):
        """Full HP + nothing worth reviving → hold the reaction (None)."""
        r = _rogue(hp_current=28, hp_max=28)
        t = OnHitByAttack(target_id="rog", attacker_id="orc", attack_damage=4)
        eligible = eligible_reactions(r, t)
        assert eligible  # Uncanny Dodge is eligible
        assert choose_companion_reaction(r, t, eligible) is None

    def test_counterspell_never_auto_used(self):
        """The heuristic intentionally declines Counterspell (too situational)."""
        w = _wiz()
        # Force eligible list as if Counterspell were the only option.
        assert choose_companion_reaction(w, OnHitByAttack(target_id="wiz"), [ReactionKind.COUNTERSPELL]) is None


# ============================================================================
# auto_resolve wrapper
# ============================================================================


class TestAutoResolveCompanionReaction:
    def test_returns_none_when_declining(self):
        """Healthy rogue declines Uncanny Dodge → wrapper returns None."""
        r = _rogue(hp_current=28, hp_max=28)
        r.is_player = False
        w = _wiz()
        w.is_player = True
        sm = _state([w, r])
        eng = CombatEngine(rng=random.Random(0))
        t = OnHitByAttack(target_id="rog", attacker_id="orc", attack_damage=4)
        eligible = eligible_reactions(r, t)
        assert eligible
        result = auto_resolve_companion_reaction(eng, sm, r, t, eligible)
        assert result is None
        assert r.reaction_available is True  # untouched
        assert r.pending_reaction is None

    def test_consumes_reaction_when_resolving(self):
        """Low-HP wizard takes Shield → reaction spent, AC buff applied."""
        w = _wiz(hp_current=8, hp_max=20)
        w.is_player = False
        r = _rogue()
        r.is_player = True
        sm = _state([r, w])
        eng = CombatEngine(rng=random.Random(0))
        t = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=6)
        eligible = eligible_reactions(w, t)
        assert ReactionKind.SHIELD in eligible
        result = auto_resolve_companion_reaction(eng, sm, w, t, eligible)
        assert result is not None
        assert result.success is True
        assert result.kind == ReactionKind.SHIELD
        assert w.reaction_available is False
        assert w.pending_reaction is None  # consumed
        assert w.pending_ac_bonus == 5  # Shield buff applied
        assert w.shield_active is True


# ============================================================================
# publish_reaction_trigger integration
# ============================================================================


class TestPublishAutoResolveCompanion:
    def test_companion_auto_resolves_with_engine(self):
        """Companion eligible + engine → auto-resolves, NO pending_reaction stashed."""
        w = _wiz(hp_current=8, hp_max=20)
        w.is_player = False
        r = _rogue()
        r.is_player = True
        sm = _state([r, w])
        eng = CombatEngine(rng=random.Random(0))
        t = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=6)
        ids = publish_reaction_trigger(sm, t, fired_at=1000, engine=eng)
        assert ids == ["wiz"]
        assert w.pending_reaction is None  # NOT stashed
        assert w.reaction_available is False  # consumed
        assert w.pending_ac_bonus == 5  # Shield buff

    def test_companion_skipped_without_engine(self):
        """Companion eligible + NO engine → skipped, nothing published.

        Production callers always pass engine; this branch exists so the
        web modal (which only handles the player) never sees a dangling
        companion pending_reaction.
        """
        w = _wiz(hp_current=8, hp_max=20)
        w.is_player = False
        r = _rogue()
        r.is_player = True
        sm = _state([r, w])
        t = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=6)
        ids = publish_reaction_trigger(sm, t, fired_at=1000)  # no engine
        assert ids == []
        assert w.pending_reaction is None

    def test_player_still_stashed_when_engine_provided(self):
        """Player path unchanged: engine param doesn't break the prompt flow."""
        w = _wiz()
        w.is_player = True
        sm = _state([w])
        eng = CombatEngine(rng=random.Random(0))
        t = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=10)
        ids = publish_reaction_trigger(sm, t, fired_at=2000, engine=eng)
        assert ids == ["wiz"]
        assert w.pending_reaction is not None
        assert w.reaction_available is True  # untouched until answer

    def test_player_already_pending_is_not_clobbered(self):
        """If the player already has an open prompt, a second trigger
        in the same turn doesn't overwrite it."""
        w = _wiz()
        w.is_player = True
        w.pending_reaction = {"resolved": False, "marker": "first"}
        sm = _state([w])
        eng = CombatEngine(rng=random.Random(0))
        t = OnHitByAttack(target_id="wiz", attacker_id="orc", attack_damage=10)
        ids = publish_reaction_trigger(sm, t, fired_at=2000, engine=eng)
        assert ids == []
        assert w.pending_reaction.get("marker") == "first"  # untouched


# ============================================================================
# OnAllyDown end-to-end (companion cleric revives a downed ally)
# ============================================================================


class TestOnAllyDownFlow:
    def test_companion_cleric_auto_healing_word_revives_downed_ally(self):
        """Publish OnAllyDown → cleric companion auto-Healing-Word → ally > 0 HP."""
        r = _rogue(hp_current=0, hp_max=28)  # downed
        c = _cleric(hp_current=22, hp_max=22)  # full HP
        c.is_player = False
        w = _wiz()
        w.is_player = True
        sm = _state([w, c, r])
        eng = CombatEngine(rng=random.Random(0))
        t = OnAllyDown(ally_id="rog")
        ids = publish_reaction_trigger(
            sm, t, fired_at=3000, engine=eng,
            candidates=[c.id for c in sm.state.party if c.id != r.id],
        )
        assert ids == ["clr"]
        assert r.hp_current > 0  # revived
        assert r.hp_current <= 4 + 5  # 1d4 + WIS mod (3) = up to 7
        assert c.reaction_available is False
        assert c.pending_reaction is None

    def test_handle_attack_publishes_on_ally_down(self, monkeypatch):
        """An attack that drops a party member to 0 HP triggers OnAllyDown.

        Monkeypatches ``attack_roll`` + ``damage_roll`` for determinism
        and spies ``publish_reaction_trigger`` to capture trigger kinds.
        Damage = 1 so the rogue's Uncanny Dodge refund (1 // 2 = 0)
        can't bring them back above 0 — OnAllyDown is the only path
        back from a downed state.
        """
        # Rogue at 1 HP, enemy orc attacks with a forced hit dealing 1 damage.
        r = _rogue(hp_current=1, hp_max=28)
        r.is_player = False
        c = _cleric()
        c.is_player = False
        w = _wiz()
        w.is_player = True
        orc = _enemy()
        sm = _state([w, c, r], [orc], player_id="wiz")
        eng = CombatEngine(rng=random.Random(0))
        eng.start_combat(sm)

        # The orc's turn: force a hit + 1 damage so rogue drops to 0.
        atk, dmg = _forge_hit(damage=1)
        monkeypatch.setattr("auto_dm.engine.combat_engine.attack_roll", lambda *a, **k: atk)
        monkeypatch.setattr("auto_dm.engine.combat_engine.damage_roll", lambda *a, **k: dmg)

        # Spy on publish_reaction_trigger (the ``from ... import`` inside
        # ``_handle_attack`` re-resolves the module-level symbol each call,
        # so patching the source module is enough).
        seen: list[tuple[str, str | None]] = []
        import auto_dm.engine.reactions as rxns
        original = rxns.publish_reaction_trigger

        def spy(state_manager, trigger, *, fired_at=None, candidates=None, engine=None):
            seen.append((trigger.kind, getattr(trigger, "ally_id", None)))
            return original(
                state_manager, trigger, fired_at=fired_at,
                candidates=candidates, engine=engine,
            )

        monkeypatch.setattr(rxns, "publish_reaction_trigger", spy)

        # Bypass turn validation — initiative order isn't deterministic
        # without a seeded roll; this test cares about the publish wiring,
        # not who rolled higher.
        monkeypatch.setattr(eng, "_validate_combat_turn", lambda *a, **k: None)

        # Force the action to be the orc attacking the rogue.
        from auto_dm.state.models import Action, ActionType
        action = Action(
            action_type=ActionType.ATTACK, actor_id=orc.id,
            target_id=r.id, params={},
        )
        eng.execute_action(sm, action)

        kinds = [k for k, _ in seen]
        assert "on_hit_by_attack" in kinds
        assert "on_ally_down" in kinds, f"expected OnAllyDown publish, got {kinds}"
        # And the cleric should have auto-cast Healing Word.
        assert r.hp_current > 0
        assert c.reaction_available is False


# ============================================================================
# DM prompt
# ============================================================================


class TestDMPromptReactions:
    def test_prompt_contains_reactions_section(self):
        assert "# Reações" in DM_SYSTEM_PROMPT

    def test_prompt_instructs_dm_to_narrate_trigger_not_effect(self):
        """The DM must narrate the *setup* of a trigger and let the
        engine resolve the reaction — not decide the outcome itself."""
        assert "Narre o gatilho" in DM_SYSTEM_PROMPT
        assert "motor é autoritativo" in DM_SYSTEM_PROMPT
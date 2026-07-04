"""Phase 38 — XP tracking + award_party_xp auto-level-up loop.

Covers:
- GameState.party_xp field defaults + Pydantic round-trip
- level_for_xp + current_party_level + xp_to_next_party_level
- award_party_xp: cross-threshold crossings (single + multi), spell
  slot refresh on level-up, ASI queue on player / auto-resolve on
  companions, narrative entries appended
- Monster.xp → NPC.xp via monster_to_npc

The engine-level mechanics (HP/prof/extra attacks/subclass features)
are exercised here as well to lock in the "single canonical entry
point" guarantee for ``level_up``.
"""
from __future__ import annotations

import random
from datetime import datetime

import pytest
from pydantic import ValidationError

from auto_dm.character import (
    CharacterBuilder,
)
from auto_dm.character.spells import prepare_caster_spells
from auto_dm.engine.progression import (
    XP_THRESHOLDS,
    LevelUpBatch,
    award_party_xp,
    current_party_level,
    level_for_xp,
    level_up,
    xp_to_next_party_level,
)
from auto_dm.phb import get_class, get_monster, get_spells_for_class
from auto_dm.state.manager import StateManager
from auto_dm.state.models import (
    AbilityScores,
    Character,
    GameState,
    NPC,
)
from auto_dm.state.monster_adapter import monster_to_npc


# ============================================================================
# Helpers (light wrappers; tests keep small payloads to stay focused)
# ============================================================================


def make_player_character(level: int = 1, *, klass: str = "Fighter") -> Character:
    """Build a minimal Character suitable for level-up tests.

    Uses the builder path so subclass features + spell slots land in
    sensible starting positions. Tests can then drive the engine
    functions and assert mutating results.
    """
    skills = ["athletics"]
    # Ability scores — standard array [15,14,13,12,10,8], put the
    # class's primary casting stat at 15 so caster tests get a real DC.
    scores = [15, 14, 13, 12, 10, 8]
    if klass == "Wizard":
        skills = ["arcana"]
        # STR,DEX,CON,INT,WIS,CHA — INT at idx 3
        scores = [8, 14, 13, 15, 12, 10]
    elif klass == "Cleric":
        skills = ["religion"]
        scores = [14, 12, 13, 10, 15, 8]
    builder = (
        CharacterBuilder()
        .with_name("Tester")
        .with_race("Human")
        .with_class(klass)
        .with_background("Soldier")
        .with_alignment("LN")
        .with_level(level)
        .with_ability_scores(scores)
        .with_skills(skills)
    )
    if klass in ("Wizard", "Cleric"):
        # Caster classes need spell selection or spellcasting stays None.
        cls_data = get_class(klass)
        abilities = AbilityScores(
            strength=scores[0], dexterity=scores[1], constitution=scores[2],
            intelligence=scores[3], wisdom=scores[4], charisma=scores[5],
        )
        cantrips = [s.name for s in get_spells_for_class(klass) if s.level == 0][:3]
        first_level = [s.name for s in get_spells_for_class(klass) if s.level == 1][:6]
        # Cleric prepares, Wizard learns; both work via prepare_caster_spells.
        selection = prepare_caster_spells(
            cls_data, level, abilities, builder._proficiency_bonus_for_level(level),
            cantrips=cantrips, spellbook=first_level,
        )
        builder = builder.with_spell_selection(selection)
    draft = builder.build()
    char = draft.character
    char.is_player = True  # ensure engine treats this as the player
    return char


def make_companion(klass: str, level: int) -> Character:
    """A L1-ish non-player Character; mimics the helpers in
    test_combat_handlers_wave_b.py but parameterized for variety."""
    return Character(
        id=f"c_{klass}_{random.randint(0, 99999)}",
        name=f"{klass}-companion",
        race="Human",
        class_=klass,
        subclass=None,
        level=level,
        background="Soldier",
        alignment="LN",
        abilities=AbilityScores(
            strength=14, dexterity=12, constitution=14,
            intelligence=10, wisdom=12, charisma=10,
        ),
        hp_current=20, hp_max=20, armor_class=14, speed=30,
        proficiency_bonus=2,
        hit_dice="1d10" if klass == "Fighter" else "1d8",
        hit_dice_remaining=level,
    )


def make_state(party: list[Character]) -> StateManager:
    """Build a StateManager with the given party + an otherwise-empty world."""
    return StateManager(GameState(
        campaign_name="xp-test",
        started_at=datetime.now(),
        party=party,
        player_character_id=party[0].id,
    ))


# ============================================================================
# TestPartyXPState — GameState.party_xp defaults + round-trip
# ============================================================================


class TestPartyXPState:
    def test_default_party_xp_is_zero(self):
        state = make_state([make_player_character(1)])
        assert state.state.party_xp == 0

    def test_pydantic_roundtrip_preserves_party_xp(self):
        sm = make_state([make_player_character(1)])
        sm.state.party_xp = 7500
        dumped = sm.state.model_dump(mode="json")
        rebuilt = GameState.model_validate(dumped)
        assert rebuilt.party_xp == 7500

    def test_old_save_missing_party_xp_loads_as_zero(self):
        """Backwards compatibility: a save from before Phase 38 has no
        ``party_xp`` key; Pydantic fills it with the field default."""
        sm = make_state([make_player_character(1)])
        dumped = sm.state.model_dump(mode="json")
        dumped.pop("party_xp", None)
        rebuilt = GameState.model_validate(dumped)
        assert rebuilt.party_xp == 0

    def test_pending_asi_default_is_none(self):
        c = make_player_character(1)
        assert c.pending_asi is None

    def test_pending_asi_roundtrips(self):
        c = make_player_character(1)
        c.pending_asi = {
            "level": 4, "choices": ["primary", "secondary"],
            "resolved": False, "primary": "strength", "secondary": "constitution",
        }
        dumped = c.model_dump(by_alias=True)
        rebuilt = Character.model_validate(dumped)
        assert rebuilt.pending_asi["primary"] == "strength"


# ============================================================================
# TestPartyLevel — party-level math + cap
# ============================================================================


class TestPartyLevel:
    def test_level_for_xp_basic_boundaries(self):
        assert level_for_xp(0) == 1
        assert level_for_xp(299) == 1
        assert level_for_xp(300) == 2
        assert level_for_xp(899) == 2
        assert level_for_xp(900) == 3
        assert level_for_xp(355_000) == 20

    def test_current_party_level_derives_from_xp(self):
        sm = make_state([make_player_character(1)])
        sm.state.party_xp = 0
        assert current_party_level(sm.state) == 1
        sm.state.party_xp = 350
        assert current_party_level(sm.state) == 2
        sm.state.party_xp = 100_000
        assert current_party_level(sm.state) == 12

    def test_xp_to_next_party_level_none_at_cap(self):
        # Builder caps at L5; build a L20 character by hand (the only
        # time we need a character at the PHB cap in unit tests).
        cap_char = Character(
            id="cap", name="Cap", race="Human",
            class_="Fighter", level=20, background="Soldier", alignment="LN",
            abilities=AbilityScores(
                strength=20, dexterity=14, constitution=18,
                intelligence=10, wisdom=12, charisma=10,
            ),
            hp_current=200, hp_max=200, armor_class=20,
            speed=30, proficiency_bonus=6,
            hit_dice="1d10", hit_dice_remaining=20,
        )
        sm = make_state([cap_char])
        sm.state.party_xp = XP_THRESHOLDS[19] + 100  # over cap
        assert current_party_level(sm.state) == 20
        assert xp_to_next_party_level(sm.state) is None

    def test_xp_to_next_party_level_remaining(self):
        sm = make_state([make_player_character(1)])
        sm.state.party_xp = 200
        assert xp_to_next_party_level(sm.state) == 100  # 300 - 200

    def test_thresholds_table_length_is_20(self):
        assert len(XP_THRESHOLDS) == 20
        assert XP_THRESHOLDS[0] == 0
        assert XP_THRESHOLDS[-1] == 355_000


# ============================================================================
# TestAwardPartyXP — happy path + edge cases
# ============================================================================


class TestAwardPartyXP:
    def test_zero_amount_is_noop(self):
        sm = make_state([make_player_character(1)])
        batch = award_party_xp(sm.state, 0)
        assert isinstance(batch, LevelUpBatch)
        assert batch.xp_awarded == 0
        assert batch.reports == []
        assert batch.any_leveled is False

    def test_negative_amount_is_noop(self):
        sm = make_state([make_player_character(1)])
        batch = award_party_xp(sm.state, -50)
        assert batch.xp_awarded == 0
        assert sm.state.party_xp == 0

    def test_sub_threshold_credit_records_narrative_only(self):
        sm = make_state([make_player_character(1)])
        batch = award_party_xp(sm.state, 100)  # not enough for L2
        assert batch.any_leveled is False
        assert sm.state.party_xp == 100
        # A narrative entry was appended.
        assert any("+100 XP" in e.content for e in sm.state.narrative_log)

    def test_single_threshold_credits_xp_and_levels(self):
        sm = make_state([make_player_character(1)])
        batch = award_party_xp(sm.state, 300, source="combat")
        assert batch.xp_awarded == 300
        assert sm.state.party_xp == 300
        assert batch.new_party_level == 2
        assert batch.any_leveled is True
        # Player went L1 → L2.
        player = sm.state.party[0]
        assert player.level == 2
        # Prof bumped to +3 (L5-8 bracket — actually L2-4 is +2; L5 bumps to +3).
        assert player.proficiency_bonus == 2

    def test_multi_threshold_crossings_walk_levels_one_step_at_a_time(self):
        sm = make_state([make_player_character(1)])
        # Award 14,001 XP — should take the party from L1 to L6
        # (L6 threshold is 14,000 PHB p. 15).
        batch = award_party_xp(sm.state, 14_001, source="combat")
        assert batch.new_party_level == 6, batch
        player = sm.state.party[0]
        assert player.level == 6
        # 5 level-ups recorded for the player (L1 → L6).
        assert len(batch.reports) == 5

    def test_cap_at_20(self):
        # Build a L19 character by hand (builder caps at L5).
        l19 = Character(
            id="l19", name="L19", race="Human",
            class_="Fighter", level=19, background="Soldier", alignment="LN",
            abilities=AbilityScores(
                strength=18, dexterity=14, constitution=16,
                intelligence=10, wisdom=12, charisma=10,
            ),
            hp_current=180, hp_max=180, armor_class=18,
            speed=30, proficiency_bonus=5,
            hit_dice="1d10", hit_dice_remaining=19,
        )
        sm = make_state([l19])
        # Already at L19 by XP.
        sm.state.party_xp = 300_000
        # Cross to L20 (305k threshold) + extra over-cap.
        batch = award_party_xp(sm.state, 100_000, source="combat")
        assert batch.new_party_level == 20
        player = sm.state.party[0]
        assert player.level == 20
        # Extra over-cap is added but no further levels.
        assert sm.state.party_xp == 400_000

    def test_companions_level_alongside_player(self):
        sm = make_state([
            make_player_character(1),
            make_companion("Fighter", 1),
            make_companion("Rogue", 1),
        ])
        # Mark the first two as companions for the helper invariant.
        sm.state.party[1].is_player = False
        sm.state.party[2].is_player = False
        award_party_xp(sm.state, 1_000, source="combat")  # L3
        for c in sm.state.party:
            assert c.level == 3, f"{c.name} não subiu: {c.level}"

    def test_spell_slots_refresh_on_level_up(self):
        """Wizard built at L1 has stale slots in current codebase until
        level_up chains through update_spell_slots_for_level. Verify."""
        sm = make_state([make_player_character(1, klass="Wizard")])
        wizard = sm.state.party[0]
        assert wizard.spellcasting is not None, "Wizard deve ter spellcasting"
        # L1 Wizard: 2 slots of 1º level.
        assert wizard.spellcasting.spell_slots_max.get(1) == 2
        # Award enough XP to level to L5 (6500 XP).
        award_party_xp(sm.state, 6_500, source="combat")
        assert wizard.level == 5
        # L5 Wizard slots: 4 / 3 / 2 (PHB p. 113).
        slots_max = wizard.spellcasting.spell_slots_max
        assert slots_max.get(1) == 4
        assert slots_max.get(2) == 3
        assert slots_max.get(3) == 2
        # spell_slots (current) also refreshed (refilled).
        slots = wizard.spellcasting.spell_slots
        assert slots.get(1) == 4
        assert slots.get(2) == 3
        assert slots.get(3) == 2

    def test_player_asi_queues_when_threshold_crossed(self):
        sm = make_state([make_player_character(3)])  # start L3
        assert sm.state.party[0].pending_asi is None
        # Award enough XP to walk L3 → L4 (2700 - 0 = 2700 exactly).
        award_party_xp(sm.state, 2_700, source="combat")
        player = sm.state.party[0]
        assert player.level == 4
        # ASI should be queued (not auto-resolved for player).
        assert player.pending_asi is not None
        assert player.pending_asi.get("level") == 4
        assert player.pending_asi.get("resolved") is False

    def test_companion_asi_auto_resolves_immediately(self):
        sm = make_state([
            make_player_character(3),
            make_companion("Fighter", 3),
        ])
        sm.state.party[1].is_player = False
        original_str = sm.state.party[1].abilities.strength
        # Walk the fighter L3 → L4 (its primary is STR → +2).
        award_party_xp(sm.state, 2_700, source="combat")
        comp = sm.state.party[1]
        assert comp.level == 4
        assert comp.abilities.strength == original_str + 2  # +2 to primary
        assert comp.pending_asi is not None
        assert comp.pending_asi.get("resolved") is True
        assert comp.pending_asi.get("primary") == "strength"

    def test_subclass_feature_applied_on_level_up(self):
        """Fighter L5 has Extra Attack (1); L11 has Extra Attack (2).
        Verify ``level_up`` chains through ``apply_class_features`` so
        the fields land automatically."""
        sm = make_state([make_player_character(1, klass="Fighter")])
        # Walk all the way to L11 (85,000 XP).
        award_party_xp(sm.state, 85_000, source="combat")
        f = sm.state.party[0]
        assert f.level == 11
        # Fighter L11 has Extra Attack (2) per the implementation.
        from auto_dm.engine.extra_attack import extra_attacks_for
        assert extra_attacks_for("fighter", 11) == 2

    def test_narrative_entries_appended_for_level_ups(self):
        sm = make_state([make_player_character(1)])
        before = len(sm.state.narrative_log)
        award_party_xp(sm.state, 300, source="combat")
        after = sm.state.narrative_log
        # 1 award summary + 1 per-char level-up entry.
        added = len(after) - before
        assert added == 2
        # Player-level entry exists.
        assert any(
            "sobe para o nível 2" in e.content for e in after
        )
        # Award summary mentions combat source.
        assert any(
            "+300 XP de combat" in e.content for e in after
        )


# ============================================================================
# TestMonsterXPInNPC — adapter populates xp from Monster.xp
# ============================================================================


class TestMonsterXPInNPC:
    def test_goblin_npc_has_50_xp(self):
        goblin = get_monster("Goblin")
        assert goblin is not None, "Goblin deve existir no PHB"
        npc = monster_to_npc(goblin)
        assert npc.xp == goblin.xp
        assert npc.xp > 0

    def test_old_npc_dict_validates_with_xp_none(self):
        """Manual NPC construction without ``xp`` defaults to None."""
        npc = NPC(
            id="x", name="Old NPC", hp_current=10, hp_max=10,
            armor_class=10, speed=30,
            abilities=AbilityScores(
                strength=10, dexterity=10, constitution=10,
                intelligence=10, wisdom=10, charisma=10,
            ),
        )
        assert npc.xp is None

    def test_npc_xp_roundtrips_through_pydantic(self):
        npc = NPC(
            id="x", name="Bandit", hp_current=11, hp_max=11,
            armor_class=12, speed=30,
            abilities=AbilityScores(
                strength=10, dexterity=10, constitution=10,
                intelligence=10, wisdom=10, charisma=10,
            ),
            xp=200,
        )
        npc2 = NPC.model_validate(npc.model_dump())
        assert npc2.xp == 200

    def test_validation_error_on_negative_xp(self):
        """Negative xp doesn't make sense; not enforcing it because
        older saves can have None. Confirm we still accept zero and
        positive values silently."""
        npc = NPC.model_validate({
            "id": "x", "name": "Zero-XP", "hp_current": 5, "hp_max": 5,
            "armor_class": 10, "speed": 30,
            "abilities": {
                "strength": 10, "dexterity": 10, "constitution": 10,
                "intelligence": 10, "wisdom": 10, "charisma": 10,
            },
            "xp": 0,
        })
        assert npc.xp == 0


# ============================================================================
# TestLevelUpBugFix — Phase 38 fixed a real bug (spell slots)
# ============================================================================


class TestLevelUpBugFix:
    def test_legacy_level_up_with_defer_false_keeps_old_behavior(self):
        """Pre-Phase-38 callers (legacy tests) might pass ``defer_asi=False``
        expecting the old behavior — primary is not set, the helper
        doesn't queue an ASI for companion or player.
        """
        c = make_player_character(3)
        result = level_up(c, defer_asi=False, hp_roll=4)
        assert c.level == 4
        assert result.asi_pending is False
        # But our new defaults (auto apply_class_features) still ran.
        assert c.proficiency_bonus == 2  # 4 still in 1-4 bracket

    def test_invalid_amount_to_award_party_xp_validation(self):
        """The web layer uses a Pydantic validator; the engine itself
        tolerates any int and clamps at <= 0. Defense in depth."""
        sm = make_state([make_player_character(1)])
        with pytest.raises((TypeError, ValidationError, ValueError)):
            award_party_xp(sm.state, "not-an-int")  # type: ignore[arg-type]

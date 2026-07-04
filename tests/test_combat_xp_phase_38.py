"""Phase 38 — End-of-combat XP hook on ``CombatEngine.end_combat``.

When the encounter ends, ``end_combat`` should:
- Sum the XP of all defeated enemies (those with ``hp_current <= 0``).
- Credit that total to ``state.party_xp``.
- Walk every party member across any thresholds crossed.
- Surface the level-up batch + XP total in ``EncounterSummary``.

This file tests that wire-up end-to-end via the engine layer.
"""
from __future__ import annotations

from datetime import datetime

from auto_dm.character import CharacterBuilder
from auto_dm.engine.combat_engine import CombatEngine
from auto_dm.engine.progression import LevelUpBatch
from auto_dm.phb import get_monster
from auto_dm.state.manager import StateManager
from auto_dm.state.models import GameState
from auto_dm.state.monster_adapter import monster_to_npc


# ============================================================================
# Helpers
# ============================================================================


def make_player(level: int = 1):
    draft = (
        CharacterBuilder()
        .with_name("Hero")
        .with_race("Human")
        .with_class("Fighter")
        .with_background("Soldier")
        .with_alignment("LN")
        .with_level(level)
        .with_standard_array()
        .with_skills(["athletics"])
        .build()
    )
    c = draft.character
    c.is_player = True
    return c


def make_state(*, party: list, npcs: list) -> StateManager:
    sm = StateManager(GameState(
        campaign_name="xp-combat-test",
        started_at=datetime.now(),
        party=party,
        npcs=npcs,
        player_character_id=party[0].id,
    ))
    return sm


def kill_npc(npc) -> None:
    """Mark an NPC as defeated without going through combat."""
    npc.hp_current = 0


# ============================================================================
# TestEndCombatXP — happy path + edge cases
# ============================================================================


class TestEndCombatXP:
    def test_summary_carries_xp_awarded(self):
        """A defeated Goblin (50 XP PHB) → summary.xp_awarded == 50."""
        player = make_player()
        goblin = monster_to_npc(get_monster("Goblin"))
        sm = make_state(party=[player], npcs=[goblin])
        engine = CombatEngine()
        engine.start_combat(sm)
        kill_npc(goblin)
        summary = engine.end_combat(sm)
        assert summary.xp_awarded == 50

    def test_alive_npcs_do_not_award_xp(self):
        player = make_player()
        goblin = monster_to_npc(get_monster("Goblin"))
        sm = make_state(party=[player], npcs=[goblin])
        engine = CombatEngine()
        engine.start_combat(sm)
        # Don't kill — goblin still alive.
        summary = engine.end_combat(sm)
        assert summary.xp_awarded == 0
        assert sm.state.party_xp == 0

    def test_party_xp_credited_after_combat(self):
        player = make_player()
        goblin = monster_to_npc(get_monster("Goblin"))
        sm = make_state(party=[player], npcs=[goblin])
        engine = CombatEngine()
        engine.start_combat(sm)
        kill_npc(goblin)
        engine.end_combat(sm)
        assert sm.state.party_xp == 50

    def test_multiple_defeated_summed(self):
        """Three defeated Goblins (3 × 50 = 150)."""
        player = make_player()
        goblins = [
            monster_to_npc(get_monster("Goblin"), npc_id=f"g{i}")
            for i in range(3)
        ]
        sm = make_state(party=[player], npcs=goblins)
        engine = CombatEngine()
        engine.start_combat(sm)
        for g in goblins:
            kill_npc(g)
        summary = engine.end_combat(sm)
        assert summary.xp_awarded == 150
        assert sm.state.party_xp == 150

    def test_mixed_defeat_counts_only_dead(self):
        """2 defeated + 1 alive → only 100 XP."""
        player = make_player()
        g1 = monster_to_npc(get_monster("Goblin"), npc_id="g1")
        g2 = monster_to_npc(get_monster("Goblin"), npc_id="g2")
        g3 = monster_to_npc(get_monster("Goblin"), npc_id="g3")
        sm = make_state(party=[player], npcs=[g1, g2, g3])
        engine = CombatEngine()
        engine.start_combat(sm)
        kill_npc(g1)
        kill_npc(g2)
        # g3 alive.
        summary = engine.end_combat(sm)
        assert summary.xp_awarded == 100

    def test_level_up_batch_attached_to_summary(self):
        """Enough XP to cross a threshold → batch is attached and
        party levels up."""
        player = make_player(level=1)
        # Spawn enough Goblins to cross L2 threshold (300 XP).
        goblins = [
            monster_to_npc(get_monster("Goblin"), npc_id=f"g{i}")
            for i in range(7)  # 7 × 50 = 350
        ]
        sm = make_state(party=[player], npcs=goblins)
        engine = CombatEngine()
        engine.start_combat(sm)
        for g in goblins:
            kill_npc(g)
        summary = engine.end_combat(sm)
        assert summary.xp_awarded == 350
        assert summary.level_up_batch is not None
        assert isinstance(summary.level_up_batch, LevelUpBatch)
        # Player went L1 → L2 (350 XP at L2 threshold).
        assert summary.level_up_batch.new_party_level == 2
        assert player.level == 2

    def test_no_batch_when_xp_under_threshold(self):
        """50 XP doesn't cross L2 threshold; batch is empty (no level-ups)."""
        player = make_player(level=1)
        goblin = monster_to_npc(get_monster("Goblin"))
        sm = make_state(party=[player], npcs=[goblin])
        engine = CombatEngine()
        engine.start_combat(sm)
        kill_npc(goblin)
        summary = engine.end_combat(sm)
        assert summary.xp_awarded == 50
        # ``end_combat`` always returns a batch (even empty) when there
        # was XP to credit; the contract is the *reports* list is empty
        # when no threshold was crossed.
        assert summary.level_up_batch is not None
        assert summary.level_up_batch.reports == []
        assert summary.level_up_batch.any_leveled is False
        assert player.level == 1

    def test_companions_level_alongside_player_via_combat(self):
        player = make_player(level=1)
        companion = make_player(level=1)
        companion.is_player = False
        companion.name = "Lyra"
        goblin = monster_to_npc(get_monster("Goblin"))
        # 7 goblins × 50 = 350 XP → L2.
        goblins = [goblin] + [
            monster_to_npc(get_monster("Goblin"), npc_id=f"g{i}")
            for i in range(6)
        ]
        sm = make_state(party=[player, companion], npcs=goblins)
        engine = CombatEngine()
        engine.start_combat(sm)
        for g in goblins:
            kill_npc(g)
        summary = engine.end_combat(sm)
        assert summary.level_up_batch is not None
        assert summary.level_up_batch.new_party_level == 2
        assert player.level == 2
        assert companion.level == 2

    def test_end_combat_no_active_returns_empty(self):
        """Calling end_combat when not in combat → empty summary."""
        player = make_player()
        sm = make_state(party=[player], npcs=[])
        engine = CombatEngine()
        # Don't start combat.
        summary = engine.end_combat(sm)
        assert summary.xp_awarded == 0
        assert summary.level_up_batch is None
        assert summary.rounds_elapsed == 0


# ============================================================================
# TestEncounterSummaryXP — dataclass shape (independent of combat)
# ============================================================================


class TestEncounterSummaryXP:
    def test_default_xp_awarded_is_zero(self):
        from auto_dm.engine.combat_engine import EncounterSummary
        s = EncounterSummary(rounds_elapsed=0)
        assert s.xp_awarded == 0
        assert s.level_up_batch is None

    def test_xp_awarded_roundtrips(self):
        from auto_dm.engine.combat_engine import EncounterSummary
        s = EncounterSummary(
            rounds_elapsed=3,
            xp_awarded=250,
        )
        assert s.xp_awarded == 250
        assert s.rounds_elapsed == 3
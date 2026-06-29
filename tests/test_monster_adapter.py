"""Tests for the Monster -> NPC adapter."""
from __future__ import annotations

from pathlib import Path

import pytest

from auto_dm.phb import get_monster, set_phb_root
from auto_dm.state.monster_adapter import monster_to_npc, _slugify, _format_action


@pytest.fixture(autouse=True)
def _reset_phb_cache():
    from auto_dm.phb import get_phb_root as _gpr
    original_root = _gpr()
    real_root = Path(__file__).resolve().parents[1] / "data" / "phb"
    set_phb_root(real_root)
    yield
    set_phb_root(original_root)


class TestSlugify:
    def test_simple(self) -> None:
        assert _slugify("Goblin") == "goblin"

    def test_multi_word(self) -> None:
        assert _slugify("Adult Red Dragon") == "adult_red_dragon"

    def test_strips_punctuation(self) -> None:
        assert _slugify("Mind Flayer") == "mind_flayer"

    def test_preserves_parentheses_content(self) -> None:
        # The parenthetical disambiguates chromatic vs metallic dragons.
        assert _slugify("Adult Red Dragon (Chromatic)") == "adult_red_dragon_chromatic"


class TestFormatAction:
    def test_melee_attack(self) -> None:
        goblin = get_monster("Goblin")
        assert goblin is not None
        scimitar = next(a for a in goblin.actions if a.name == "Scimitar")
        text = _format_action(scimitar)
        assert "Scimitar" in text
        assert "+4" in text
        assert "melee weapon" in text
        assert "1d6+2" in text
        assert "slashing" in text

    def test_ranged_attack_with_range(self) -> None:
        goblin = get_monster("Goblin")
        assert goblin is not None
        shortbow = next(a for a in goblin.actions if a.name == "Shortbow")
        text = _format_action(shortbow)
        assert "Shortbow" in text
        assert "ranged weapon" in text

    def test_recharge_notation(self) -> None:
        dragon = get_monster("Adult Red Dragon (Chromatic)")
        assert dragon is not None
        breath = next(a for a in dragon.actions if a.name == "Fire Breath")
        text = _format_action(breath)
        assert "Recharge 5-6" in text

    def test_multiattack_no_stats(self) -> None:
        dragon = get_monster("Adult Red Dragon (Chromatic)")
        assert dragon is not None
        multi = next(a for a in dragon.actions if a.name == "Multiattack")
        text = _format_action(multi)
        assert text == "Multiattack"


class TestMonsterToNpc:
    def test_goblin_npc(self) -> None:
        goblin = get_monster("Goblin")
        assert goblin is not None
        npc = monster_to_npc(goblin)
        assert npc.id == "goblin"
        assert npc.name == "Goblin"
        assert npc.hp_current == 7
        assert npc.hp_max == 7
        assert npc.armor_class == 15
        assert npc.speed == 30
        assert npc.abilities.strength == 8
        assert npc.abilities.dexterity == 14
        assert npc.is_hostile is True
        assert npc.challenge_rating == 0.25

    def test_npc_carries_damage_modifiers(self) -> None:
        lich = get_monster("Lich")
        assert lich is not None
        npc = monster_to_npc(lich)
        assert "cold" in npc.resistances
        assert "lightning" in npc.resistances
        assert "necrotic" in npc.resistances
        assert "poison" in npc.immunities
        assert npc.condition_immunities == [
            "charmed", "exhaustion", "frightened", "paralyzed", "poisoned",
        ]

    def test_npc_carries_actions(self) -> None:
        dragon = get_monster("Adult Red Dragon (Chromatic)")
        assert dragon is not None
        npc = monster_to_npc(dragon)
        action_texts = " | ".join(npc.actions)
        assert "Bite" in action_texts
        assert "Claw" in action_texts
        assert "Fire Breath" in action_texts
        # Bite should include rider damage (2d6 fire)
        bite_line = next(a for a in npc.actions if a.startswith("Bite"))
        assert "fire" in bite_line

    def test_npc_custom_id(self) -> None:
        # For a goblin patrol of three, callers pass distinct ids.
        goblin = get_monster("Goblin")
        assert goblin is not None
        npc1 = monster_to_npc(goblin, npc_id="goblin_1")
        npc2 = monster_to_npc(goblin, npc_id="goblin_2")
        npc3 = monster_to_npc(goblin, npc_id="goblin_3")
        assert npc1.id == "goblin_1"
        assert npc2.id == "goblin_2"
        assert npc3.id == "goblin_3"
        # All three are independent state objects
        assert npc1 is not npc2 is not npc3

    def test_npc_friendly(self) -> None:
        # Some NPCs from the Monster list are friendly by default (Archmage,
        # Acolyte). The adapter accepts ``is_hostile=False``.
        acolyte = get_monster("Acolyte")
        if acolyte is not None:
            npc = monster_to_npc(acolyte, is_hostile=False)
            assert npc.is_hostile is False

    def test_npc_description_includes_alignment(self) -> None:
        goblin = get_monster("Goblin")
        assert goblin is not None
        npc = monster_to_npc(goblin)
        assert "Small" in npc.description
        assert "humanoid" in npc.description
        assert "neutral evil" in npc.description

    def test_npc_hp_current_equals_max(self) -> None:
        # Adapter always spawns at full HP; the engine applies damage after.
        owlbear = get_monster("Owlbear")
        assert owlbear is not None
        npc = monster_to_npc(owlbear)
        assert npc.hp_current == npc.hp_max == 59

    def test_npc_can_be_serialized_to_json(self) -> None:
        # Round-trip via JSON to ensure the adapter produces a model that
        # is safe to drop into ``GameState.npcs`` and save to disk.
        import json
        orc = get_monster("Orc")
        assert orc is not None
        npc = monster_to_npc(orc, npc_id="orc_patrol_alpha")
        payload = npc.model_dump_json()
        restored = json.loads(payload)
        assert restored["name"] == "Orc"
        assert restored["armor_class"] == 13
        assert restored["hp_max"] == 15
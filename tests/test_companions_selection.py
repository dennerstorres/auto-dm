"""Tests for the synergy-based party candidate roller (Phase 27).

Covers:
- API contract (returns ``k`` unique keys)
- Determinism with a seeded RNG
- Same-class avoidance under many seeds
- Healer guarantee when the player has no healer role
- Edge cases (k=0, k larger than pool, missing class)
"""
from __future__ import annotations

import random

from auto_dm.companions import list_companion_keys
from auto_dm.companions.selection import (
    ROLE_TAGS,
    SYNERGY_BIAS,
    _CANDIDATE_CLASS,
    roll_party_candidates,
)
from auto_dm.state.models import AbilityScores, Character


def _stub_player(char_class: str) -> Character:
    """Build a minimal Character used only for the class lookup."""
    return Character(
        id="__stub__",
        name="__stub__",
        race="Human",
        **{"class": char_class},
        subclass=None,
        level=1,
        background="Commoner",
        alignment="N",
        abilities=AbilityScores(
            strength=10, dexterity=10, constitution=10,
            intelligence=10, wisdom=10, charisma=10,
        ),
        hp_current=1, hp_max=1, armor_class=10, speed=30,
        proficiency_bonus=2, hit_dice="1d8", hit_dice_remaining=1,
    )


# ---------------------------------------------------------------------------
# API contract
# ---------------------------------------------------------------------------


class TestRollPartyCandidates:
    def test_returns_k_keys(self):
        out = roll_party_candidates(_stub_player("Wizard"), k=4, rng=random.Random(0))
        assert len(out) == 4

    def test_returns_only_known_keys(self):
        known = set(list_companion_keys())
        out = roll_party_candidates(_stub_player("Wizard"), k=4, rng=random.Random(0))
        for key in out:
            assert key in known

    def test_no_duplicates(self):
        for seed in range(20):
            out = roll_party_candidates(_stub_player("Wizard"), rng=random.Random(seed))
            assert len(set(out)) == len(out), f"duplicates at seed {seed}: {out}"

    def test_deterministic_with_seed(self):
        a = roll_party_candidates(_stub_player("Wizard"), rng=random.Random(42))
        b = roll_party_candidates(_stub_player("Wizard"), rng=random.Random(42))
        assert a == b

    def test_k_zero_returns_empty(self):
        out = roll_party_candidates(_stub_player("Wizard"), k=0, rng=random.Random(0))
        assert out == []

    def test_k_larger_than_pool_returns_all(self):
        # k larger than the available pool returns every eligible
        # candidate — i.e. the whole roster EXCEPT the player's own
        # class (Wizard → kael excluded).
        full = list(list_companion_keys())
        out = roll_party_candidates(_stub_player("Wizard"), k=20, rng=random.Random(0))
        assert len(out) == len(full) - 1
        assert set(out) == (set(full) - {"kael"})


# ---------------------------------------------------------------------------
# Role-tag taxonomy
# ---------------------------------------------------------------------------


class TestRoleTags:
    def test_every_companion_has_role_tags(self):
        for key in list_companion_keys():
            assert key in ROLE_TAGS, key
            assert len(ROLE_TAGS[key]) >= 2, key

    def test_healer_tag_only_on_healer_classes(self):
        healers = {k for k, tags in ROLE_TAGS.items() if "healer" in tags}
        # Cleric + Druid are the only healer-tagged companions.
        assert healers == {"mira", "eldra"}

    def test_synergy_bias_uses_known_tags(self):
        # Any tag appearing in ROLE_TAGS that's also a SYNERGY_BIAS key
        # must have a bias factor — no orphan tags.
        all_tags = set()
        for tags in ROLE_TAGS.values():
            all_tags.update(tags)
        for tag in all_tags:
            assert tag in SYNERGY_BIAS, f"missing bias for {tag!r}"


# ---------------------------------------------------------------------------
# Same-class avoidance
# ---------------------------------------------------------------------------


class TestSynergy:
    def test_fighter_player_never_gets_thorgrim(self):
        # Only one Fighter (thorgrim). Same-class companions are excluded
        # outright, so thorgrim must NEVER appear for a Fighter player.
        for seed in range(100):
            out = roll_party_candidates(_stub_player("Fighter"), rng=random.Random(seed))
            assert "thorgrim" not in out, (
                f"thorgrim (same class as player) appeared at seed {seed}: {out}"
            )

    def test_no_companion_shares_player_class_any_seed(self):
        # For every PHB class, no rolled companion may share that class.
        for pclass in [
            "Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk",
            "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard",
        ]:
            for seed in range(20):
                out = roll_party_candidates(_stub_player(pclass), rng=random.Random(seed))
                for key in out:
                    cand_class = _CANDIDATE_CLASS.get(key, "")
                    assert cand_class != pclass.lower(), (
                        f"{key} ({cand_class}) shares class with {pclass} player"
                    )

    def test_wizard_player_prefers_healer(self):
        # Wizard has no healer role → healer guarantee kicks in.
        healer_hits = 0
        for seed in range(50):
            out = roll_party_candidates(_stub_player("Wizard"), rng=random.Random(seed))
            chosen_tags = set().union(*(ROLE_TAGS[k] for k in out))
            if "healer" in chosen_tags:
                healer_hits += 1
        # Healer should land in at least 80% of rolls (guarantee retries).
        assert healer_hits >= 40, f"healer only {healer_hits}/50"

    def test_cleric_player_no_healer_force(self):
        # Cleric already has healer role in its tag set → no force applied,
        # so some rolls may legitimately skip a healer companion.
        healer_hits = 0
        for seed in range(50):
            out = roll_party_candidates(_stub_player("Cleric"), rng=random.Random(seed))
            chosen_tags = set().union(*(ROLE_TAGS[k] for k in out))
            if "healer" in chosen_tags:
                healer_hits += 1
        # Without force, healer presence is variable — just confirm we
        # can roll at least one without a healer (at least one drop).
        assert healer_hits < 50, "healer should be allowed to drop for cleric players"

    def test_picks_diverge_across_seeds(self):
        seen: set[tuple[str, ...]] = set()
        for seed in range(30):
            out = roll_party_candidates(_stub_player("Wizard"), rng=random.Random(seed))
            seen.add(tuple(sorted(out)))
        # Random sampling should produce > 5 distinct 4-tuples across 30 seeds.
        assert len(seen) > 5

    def test_unknown_player_class_still_returns(self):
        # Even with an unknown class, the algorithm completes — just
        # without role-specific bias (defaults to plain weighted random).
        out = roll_party_candidates(_stub_player("NotAClass"), k=4, rng=random.Random(0))
        assert len(out) == 4
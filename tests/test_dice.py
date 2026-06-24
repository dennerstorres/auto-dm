"""Tests for the dice module."""
from __future__ import annotations

import random

import pytest

from auto_dm.engine.dice import (
    roll_d20,
    roll_dice,
    roll_die,
    roll_stats,
)


# ---------------------------------------------------------------------------
# roll_dice
# ---------------------------------------------------------------------------


def test_roll_dice_simple_d20():
    rng = random.Random(42)
    r = roll_dice("1d20", rng=rng)
    assert 1 <= r.total <= 20
    assert r.rolls == [r.total]
    assert r.kept == r.rolls
    assert r.dropped == []
    assert r.modifier == 0


def test_roll_dice_multiple_dice():
    rng = random.Random(42)
    r = roll_dice("3d6", rng=rng)
    assert len(r.rolls) == 3
    assert all(1 <= x <= 6 for x in r.rolls)
    assert r.total == sum(r.rolls)


def test_roll_dice_with_positive_modifier():
    rng = random.Random(42)
    r = roll_dice("1d20+5", rng=rng)
    assert r.modifier == 5
    assert r.total == r.rolls[0] + 5


def test_roll_dice_with_negative_modifier():
    rng = random.Random(42)
    r = roll_dice("1d20-2", rng=rng)
    assert r.modifier == -2
    assert r.total == r.rolls[0] - 2


def test_roll_dice_no_count_defaults_to_1():
    rng = random.Random(42)
    r = roll_dice("d20", rng=rng)
    assert len(r.rolls) == 1


def test_roll_dice_ignores_spaces_and_case():
    rng = random.Random(42)
    r = roll_dice(" 1D20 + 3 ", rng=rng)
    assert r.modifier == 3
    assert r.total == r.rolls[0] + 3


def test_roll_dice_keep_highest():
    rng = random.Random(42)
    r = roll_dice("4d6kh3", rng=rng)
    assert len(r.rolls) == 4
    assert len(r.kept) == 3
    assert len(r.dropped) == 1
    # The kept should be the top 3
    assert sum(r.kept) + r.modifier == r.total
    assert r.kept == sorted(r.rolls, reverse=True)[:3]


def test_roll_dice_keep_lowest():
    rng = random.Random(42)
    r = roll_dice("2d20kl1", rng=rng)
    assert len(r.rolls) == 2
    assert r.kept == [min(r.rolls)]
    assert r.dropped == [max(r.rolls)]


def test_roll_dice_keep_all_is_no_drop():
    rng = random.Random(42)
    r = roll_dice("3d6kh3", rng=rng)
    assert len(r.kept) == 3
    assert r.dropped == []


def test_roll_dice_keep_with_modifier():
    rng = random.Random(42)
    r = roll_dice("4d6kh3+2", rng=rng)
    assert len(r.kept) == 3
    assert r.modifier == 2
    assert r.total == sum(r.kept) + 2


# ---------------------------------------------------------------------------
# roll_d20
# ---------------------------------------------------------------------------


def test_roll_d20_normal():
    rng = random.Random(42)
    r = roll_d20(rng=rng)
    assert len(r.rolls) == 1
    assert 1 <= r.rolls[0] <= 20


def test_roll_d20_advantage_uses_higher():
    rng = random.Random(42)
    r = roll_d20(advantage=True, rng=rng)
    assert len(r.rolls) == 2
    assert r.total == max(r.rolls) + r.modifier
    assert r.kept == [max(r.rolls)]


def test_roll_d20_disadvantage_uses_lower():
    rng = random.Random(42)
    r = roll_d20(disadvantage=True, rng=rng)
    assert len(r.rolls) == 2
    assert r.total == min(r.rolls) + r.modifier
    assert r.kept == [min(r.rolls)]


def test_roll_d20_advantage_and_disadvantage_cancel():
    rng = random.Random(42)
    r = roll_d20(advantage=True, disadvantage=True, rng=rng)
    assert len(r.rolls) == 1
    assert r.kept == r.rolls


def test_roll_d20_with_modifier():
    rng = random.Random(42)
    r = roll_d20(modifier=5, rng=rng)
    assert r.modifier == 5
    assert r.total == r.rolls[0] + 5


# ---------------------------------------------------------------------------
# roll_stats
# ---------------------------------------------------------------------------


def test_roll_stats_returns_six_values():
    rng = random.Random(42)
    stats = roll_stats(rng=rng)
    assert len(stats) == 6
    for s in stats:
        assert 3 <= s <= 18  # minimum 3 (4d6kh3 = 3 if all 1s except dropped)


# ---------------------------------------------------------------------------
# roll_die
# ---------------------------------------------------------------------------


def test_roll_die_within_range():
    rng = random.Random(42)
    for _ in range(50):
        r = roll_die(20, rng=rng)
        assert 1 <= r <= 20


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_roll_dice_rejects_garbage():
    with pytest.raises(ValueError):
        roll_dice("abc")
    with pytest.raises(ValueError):
        roll_dice("1d")
    with pytest.raises(ValueError):
        roll_dice("")


def test_roll_dice_rejects_zero_or_one_sided_die():
    with pytest.raises(ValueError):
        roll_dice("1d1")


def test_roll_dice_rejects_too_many_dice():
    with pytest.raises(ValueError):
        roll_dice("1000d6")


def test_roll_dice_rejects_invalid_keep():
    with pytest.raises(ValueError):
        roll_dice("2d6kh5")  # can't keep 5 of 2


# ---------------------------------------------------------------------------
# DiceRoll string format
# ---------------------------------------------------------------------------


def test_diceroll_str_includes_notation():
    rng = random.Random(42)
    r = roll_dice("1d20+5", rng=rng)
    assert "1d20+5" in str(r)
    assert f"total={r.total}" in str(r)

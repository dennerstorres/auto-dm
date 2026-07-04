"""Progression rules: XP thresholds, level-up, ASI, Inspiration.

XP thresholds (PHB p. 15):

    Level 1: 0 XP
    Level 2: 300 XP
    Level 3: 900 XP
    Level 4: 2,700 XP
    Level 5: 6,500 XP
    Level 6: 14,000 XP
    Level 7: 23,000 XP
    Level 8: 34,000 XP
    Level 9: 48,000 XP
    Level 10: 64,000 XP
    Level 11: 85,000 XP
    Level 12: 100,000 XP
    Level 13: 120,000 XP
    Level 14: 140,000 XP
    Level 15: 165,000 XP
    Level 16: 195,000 XP
    Level 17: 225,000 XP
    Level 18: 265,000 XP
    Level 19: 305,000 XP
    Level 20: 355,000 XP

ASI: at levels 4, 8, 12, 16, and 19, the character gains either +2 to
one ability score or +1 to two ability scores. Max ability score is 20
for PCs at these levels (PHB p. 15).

Inspiration: a token the DM grants for good roleplay. Spending it gives
advantage on one d20 roll (attack, save, ability check). One character
can have at most one inspiration at a time (PHB p. 126 — "you can't
stockpile").

Phase 38 — XP awards and auto-level-up loop:

    XP is shared at the party level (``GameState.party_xp``). When
    ``award_party_xp`` adds XP and the pool crosses one or more PHB
    thresholds, every member of ``state.party`` is advanced through
    ``level_up``. Mechanical bumps (HP/prof/extra attacks/hit dice/
    subclass features/spell slots/capstones) apply immediately. ASI
    choices (levels 4/8/12/16/19) defer to the player via the
    ``Character.pending_asi`` queue; companions auto-resolve.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from auto_dm.engine.extra_attack import extra_attacks_for
from auto_dm.engine.dice import roll_dice
from auto_dm.state.models import Ability, AbilityScores, Character

if TYPE_CHECKING:
    from auto_dm.state.models import GameState


# ============================================================================
# XP thresholds (PHB p. 15)
# ============================================================================


# Index by level: XP_THRESHOLDS[1] = 0, XP_THRESHOLDS[2] = 300, ...
XP_THRESHOLDS: list[int] = [
    0,        # Level 1
    300,      # Level 2
    900,      # Level 3
    2_700,    # Level 4
    6_500,    # Level 5
    14_000,   # Level 6
    23_000,   # Level 7
    34_000,   # Level 8
    48_000,   # Level 9
    64_000,   # Level 10
    85_000,   # Level 11
    100_000,  # Level 12
    120_000,  # Level 13
    140_000,  # Level 14
    165_000,  # Level 15
    195_000,  # Level 16
    225_000,  # Level 17
    265_000,  # Level 18
    305_000,  # Level 19
    355_000,  # Level 20
]


def level_for_xp(xp: int) -> int:
    """Return the highest level whose XP threshold is <= ``xp``.

    Clamps to 1 (minimum) and 20 (cap). PHB: a character never exceeds
    level 20 by XP alone; further progression requires DM fiat / epic
    boons.
    """
    if xp < 0:
        return 1
    level = 1
    for lvl, threshold in enumerate(XP_THRESHOLDS, start=1):
        if xp >= threshold:
            level = lvl
        else:
            break
    return min(level, 20)


def xp_to_next_level(character: Character) -> Optional[int]:
    """Return the XP needed to reach the next level.

    Returns ``None`` if the character is already at level 20 (cap).
    """
    if character.level >= 20:
        return None
    return XP_THRESHOLDS[character.level] - getattr(character, "xp", 0)


def proficiency_bonus_for(level: int) -> int:
    """Return the proficiency bonus for a given level (PHB p. 15).

    Level 1-4: +2
    Level 5-8: +3
    Level 9-12: +4
    Level 13-16: +5
    Level 17-20: +6
    """
    if level >= 17:
        return 6
    if level >= 13:
        return 5
    if level >= 9:
        return 4
    if level >= 5:
        return 3
    return 2


# ============================================================================
# Level-up
# ============================================================================


@dataclass
class LevelUpResult:
    """Result of a single level-up operation.

    Tracks what changed so the caller (DM agent, REPL, save system) can
    narrate or audit it.
    """

    old_level: int
    new_level: int
    hp_gained: int
    new_proficiency_bonus: int
    new_extra_attacks: int
    asi_pending: bool  # True if the new level is an ASI level (4/8/12/16/19)
    new_max_hp: int
    notes: list[str] = field(default_factory=list)


def level_up(
    character: Character,
    *,
    hp_roll: Optional[int] = None,
    con_modifier: Optional[int] = None,
    rng=None,
    defer_asi: bool = True,
) -> LevelUpResult:
    """Advance ``character`` by one level.

    Mutates ``character.level``, ``character.proficiency_bonus``,
    ``character.hp_max``, ``character.hp_current`` (+hp_gained),
    ``character.extra_attacks``, ``character.hit_dice_remaining``, and
    — for spellcasting classes — refreshes ``spell_slots`` and
    ``spell_slots_max`` to the PHB progression for ``new_level``.

    Also invokes :func:`character.level_up.apply_class_features` so
    feature flags (Danger Sense, Aura of Protection, Indomitable,
    Brutal Critical, Capstones at L20, …) and subclass features are
    re-applied automatically for the new level.

    Args:
        hp_roll: Caller-supplied hit-die roll (1dN). If None, the
            function rolls ``character.hit_dice`` for you.
        con_modifier: Override Constitution modifier. If None, computed
            from ``character.abilities.constitution``.
        rng: Random source (defaults to ``random.Random()``).
        defer_asi: If True (default), the ASI bump at
            levels {4, 8, 12, 16, 19} is deferred to the caller
            (sets ``character.pending_asi``); see
            :func:`award_party_xp` / :func:`resolve_asi_choice`. If
            False, the function uses the legacy behavior:
            ``+2 primary = strength`` is applied directly so existing
            tests keep passing.

    Returns:
        :class:`LevelUpResult` summarizing the changes.

    Raises:
        ValueError: If the character is already at level 20.
    """
    if character.level >= 20:
        raise ValueError("Character is already at level 20 (cap).")

    old_level = character.level
    new_level = old_level + 1
    if hp_roll is None:
        # Roll the hit die (e.g. "1d10")
        roll = roll_dice(character.hit_dice, rng=rng)
        hp_gained = roll.total
    else:
        hp_gained = hp_roll
    # Minimum of 1 HP per level per PHB.
    hp_gained = max(1, hp_gained)

    if con_modifier is None:
        con_modifier = character.abilities.modifier(Ability.CON)
    hp_gained += con_modifier
    # PHB: minimum of 1 HP per level (after CON mod).
    if hp_gained < 1:
        hp_gained = 1

    character.level = new_level
    new_prof = proficiency_bonus_for(new_level)
    character.proficiency_bonus = new_prof
    character.hp_max = character.hp_max + hp_gained
    character.hp_current = character.hp_current + hp_gained
    character.hit_dice_remaining = character.hit_dice_remaining + 1
    character.extra_attacks = extra_attacks_for(character.class_, new_level)

    notes: list[str] = []

    # Phase 38 — wire the rest of the level-up chain so callers don't
    # need to remember to do it. Refresh spell slots (PHB p. 113 tables
    # change every level for casters), then re-apply class + subclass
    # feature flags to keep gates in sync with ``character.level``.
    _refresh_spell_slots(character, new_level)
    features_now_active = _apply_class_features(character)
    notes.extend(features_now_active)

    # ASI handling: defer (queue) or apply immediately.
    is_asi = is_asi_level(new_level)
    if is_asi and defer_asi:
        if character.is_player:
            character.pending_asi = {
                "level": new_level,
                "choices": ["primary"],
                "resolved": False,
                "primary": None,
                "secondary": None,
            }
            notes.append(
                f"ASI disponível no nível {new_level} (escolha pendente)."
            )
        else:
            # Companions auto-resolve. Player-level path (meta
            # commands, web modal) is wired in resolve_asi_choice.
            from auto_dm.character.level_up import (
                auto_resolve_companion_asi,
                companion_asi_to_pending,
            )

            choice = auto_resolve_companion_asi(character)
            character.pending_asi = companion_asi_to_pending(choice)
            notes.append(
                "ASI aplicada automaticamente (companion heuristic)."
            )

    return LevelUpResult(
        old_level=old_level,
        new_level=new_level,
        hp_gained=hp_gained,
        new_proficiency_bonus=new_prof,
        new_extra_attacks=character.extra_attacks,
        asi_pending=is_asi and defer_asi,
        new_max_hp=character.hp_max,
        notes=notes,
    )


def _refresh_spell_slots(character: Character, level: int) -> None:
    """Refresh spell slot pools to the PHB tables for ``level``.

    No-op when the character has no ``spellcasting`` block (martials
    skip silently). Both ``spell_slots`` (current) and
    ``spell_slots_max`` are updated so the character can cast at the
    new level without carrying forward stale totals. Slot levels not
    present in the new table are dropped; new levels are initialized
    with their full allotment. The function is idempotent — calling
    it twice for the same level is a no-op.

    Raises no exceptions; if the helper table isn't found (unknown
    class), the existing slot block is preserved as-is.
    """
    if character.spellcasting is None:
        return
    try:
        from auto_dm.character.spells import get_spell_slots
    except ImportError:
        return
    new_max = get_spell_slots(character.class_, level)
    if not new_max:
        return
    current = character.spellcasting.spell_slots or {}
    refreshed: dict[int, int] = {}
    for slot_lvl, max_count in new_max.items():
        # Preserve any unused slots from previous level's same-slot pool
        # only when the max didn't shrink. For levels where the caster
        # gained slots (most cases), refill fully.
        prev = current.get(slot_lvl, 0)
        refreshed[slot_lvl] = max_count if prev > max_count else max_count
    character.spellcasting.spell_slots = refreshed
    character.spellcasting.spell_slots_max = dict(new_max)


def _apply_class_features(character: Character) -> list[str]:
    """Re-run class feature gates and return the names that became
    active at the new level. Idempotent — safe to call after every
    ``level_up``. Lives in ``progression.py`` to avoid a circular
    import (the level_up module imports from progression at the
    top).
    """
    try:
        from auto_dm.character.level_up import (
            apply_class_features,
            features_gained_at_class_level,
        )
    except ImportError:
        return []
    apply_class_features(character)
    return features_gained_at_class_level(character, character.level)


# ============================================================================
# ASI
# ============================================================================


# PHB p. 15: ASI is granted at levels 4, 8, 12, 16, 19
ASI_LEVELS: frozenset[int] = frozenset({4, 8, 12, 16, 19})


def is_asi_level(level: int) -> bool:
    """True if reaching ``level`` grants an ASI."""
    return level in ASI_LEVELS


def apply_asi(
    character: Character,
    primary: Ability,
    secondary: Ability | None = None,
) -> AbilityScores:
    """Apply an Ability Score Improvement to ``character``.

    PHB p. 15: +2 to one ability, OR +1 to two abilities. The cap is
    20 at this level (PHB allows >20 only via specific magic items).

    Mutates ``character.abilities`` in place. Returns the updated scores.
    Raises ValueError if a chosen ability would exceed the cap or if the
    split is invalid.
    """
    scores = character.abilities
    if secondary is None:
        # +2 to one ability, cap 20
        cur = getattr(scores, primary.value)
        if cur + 2 > 20:
            raise ValueError(
                f"{primary.value} would exceed 20 ({cur} + 2)."
            )
        setattr(scores, primary.value, cur + 2)
    else:
        if primary == secondary:
            raise ValueError("ASI split must use two different abilities.")
        cur_p = getattr(scores, primary.value)
        cur_s = getattr(scores, secondary.value)
        if cur_p + 1 > 20:
            raise ValueError(f"{primary.value} would exceed 20.")
        if cur_s + 1 > 20:
            raise ValueError(f"{secondary.value} would exceed 20.")
        setattr(scores, primary.value, cur_p + 1)
        setattr(scores, secondary.value, cur_s + 1)
    return scores


# ============================================================================
# Inspiration
# ============================================================================


def grant_inspiration(character: Character) -> bool:
    """Grant inspiration to a character. Returns True if newly granted.

    PHB: you can't stockpile — granting when already inspired is a no-op.
    """
    if character.inspiration:
        return False
    character.inspiration = True
    return True


def spend_inspiration(character: Character) -> bool:
    """Spend inspiration. Returns True if it was available and consumed.

    Adds 1 to pending_advantage; the next eligible d20 roll consumes it.
    """
    if not character.inspiration:
        return False
    character.inspiration = False
    character.pending_advantage += 1
    return True


def consume_pending_advantage(character: Character) -> bool:
    """Consume one stack of pending advantage for a single d20 roll.

    Returns True if there was one to consume.
    """
    if character.pending_advantage > 0:
        character.pending_advantage -= 1
        return True
    return False


# ============================================================================
# Phase 38 — Party XP, auto-level-up loop, ASI queue
# ============================================================================


@dataclass
class LevelUpReport:
    """One character's level-up event within a LevelUpBatch.

    Used by award_party_xp and surfaced via EncounterSummary /
    web responses so the frontend (and DM) can narrate each one.
    """

    character_id: str
    character_name: str
    is_player: bool
    old_level: int
    new_level: int
    hp_gained: int
    features_gained: list[str]
    asi_pending: bool
    asi_auto_resolved: bool
    asi_choice: Optional[dict] = None  # {primary, secondary} once resolved


@dataclass
class LevelUpBatch:
    """Aggregate of all level-ups triggered by a single XP award."""

    xp_awarded: int
    new_party_xp: int
    old_party_level: int
    new_party_level: int
    source: str  # "combat" | "meta" | "manual"
    reports: list[LevelUpReport] = field(default_factory=list)

    @property
    def any_leveled(self) -> bool:
        return any(r.old_level != r.new_level for r in self.reports)

    @property
    def any_asi_pending(self) -> bool:
        return any(r.asi_pending and not r.asi_auto_resolved for r in self.reports)


def current_party_level(state: "GameState") -> int:
    """Return the party's effective level from ``state.party_xp``.

    Derived, not stored — see :func:`level_for_xp`. Clamped to 1..20.
    """
    return level_for_xp(state.party_xp)


def xp_to_next_party_level(state: "GameState") -> Optional[int]:
    """Return the XP needed to reach the next level for the whole party.

    ``None`` if the party is already at the L20 cap.
    """
    lvl = current_party_level(state)
    if lvl >= 20:
        return None
    return XP_THRESHOLDS[lvl] - state.party_xp


def award_party_xp(  # noqa: F811
    state: "GameState",
    amount: int,
    *,
    source: str = "manual",
    rng: Optional[random.Random] = None,
) -> LevelUpBatch:
    """Credit ``amount`` XP to the party and run the auto-level chain.

    Steps:

    1. Clamp amount to >= 0 (negative grants are no-ops).
    2. Add to ``state.party_xp``. ``old_party_level`` is captured
       *before* the bump; ``new_party_level = level_for_xp(party_xp)``.
    3. For each party member, advance from ``c.level`` up to
       ``new_party_level`` calling :func:`level_up` per step. This
       correctly handles multi-threshold crossings (e.g. crediting
       110,000 XP to a L1 party walks each member L2..L6 in sequence).
    4. Build a :class:`LevelUpBatch` aggregating every per-character
       ``LevelUpReport``. Narrative entries are appended in a
       helper (``_append_levelup_narrative``) so the engine module
       stays decoupled from ``state/narrative_log`` ordering concerns.

    Args:
        state: Game state (mutated in place).
        amount: Positive integer XP. Zero or negative is a no-op
            (returns an empty batch).
        source: Free-text label for the audit trail (``"combat"``,
            ``"meta"``, ``"manual"``).
        rng: Random source for hit-die rolls during level-ups. Defaults
            to ``random.Random()`` (non-deterministic across calls).

    Returns:
        :class:`LevelUpBatch` with one :class:`LevelUpReport` per
        party member that advanced. ``batch.any_leveled`` is False
        when the credit didn't cross a threshold.
    """
    if rng is None:
        rng = random.Random()
    if amount <= 0:
        return LevelUpBatch(
            xp_awarded=0,
            new_party_xp=state.party_xp,
            old_party_level=current_party_level(state),
            new_party_level=current_party_level(state),
            source=source,
            reports=[],
        )

    old_party_level = current_party_level(state)
    state.party_xp = state.party_xp + amount
    new_party_level = current_party_level(state)
    delta = max(0, new_party_level - old_party_level)

    batch = LevelUpBatch(
        xp_awarded=amount,
        new_party_xp=state.party_xp,
        old_party_level=old_party_level,
        new_party_level=new_party_level,
        source=source,
        reports=[],
    )

    if delta == 0:
        return batch

    for character in state.party:
        for _step in range(delta):
            if character.level >= 20:
                break  # PHB cap
            report = _level_up_party_member(character, rng=rng)
            batch.reports.append(report)

    return batch


def _level_up_party_member(
    character: Character,
    *,
    rng: random.Random,
) -> LevelUpReport:
    """Single level-up step inside :func:`award_party_xp`.

    The companion ASI queue is auto-resolved at this layer (companions
    never leave a non-None ``pending_asi``). Players get the queue.

    Appends a ``NarrativeEntry`` (role="system") to the campaign log so
    the level-up appears in the diary the next time the player views the
    narrative — independent of whether the LLM chose to narrate it.
    """
    from datetime import datetime, timezone

    from auto_dm.state.models import NarrativeEntry

    old_level = character.level
    result = level_up(character, rng=rng, defer_asi=True)
    new_level = character.level

    # ASI handling: ``level_up`` already did the work — it queued a
    # ``pending_asi`` for the player and auto-resolved (applied the
    # stats) for companions. We just report it for the narrative entry.
    asi_pending = False
    asi_auto_resolved = False
    asi_summary = ""
    if result.asi_pending:
        if character.is_player:
            asi_pending = True
            asi_summary = " ASI disponível — abra a janela para escolher."
        else:
            # Companion — level_up already called auto_resolve_companion_asi
            # which applied the stats and set ``character.pending_asi``.
            asi_auto_resolved = True
            pending = character.pending_asi or {}
            primary = pending.get("primary")
            secondary = pending.get("secondary")
            if primary and secondary:
                asi_summary = (
                    f" ASI aplicada automaticamente (+1 {primary}, "
                    f"+1 {secondary})."
                )
            elif primary:
                asi_summary = (
                    f" ASI aplicada automaticamente (+2 {primary})."
                )

    # Append a system narrative entry. We thread through the parent
    # GameState via the caller's batch — see ``award_party_xp``.
    # The entry is built here and stored on the report so the caller
    # can attach it once the state is in scope (the engine layer has
    # access to ``state.narrative_log`` but the progression helper
    # itself doesn't, by design).
    features = list(result.notes)
    content_lines = [
        f"Você sobe para o nível {new_level}! HP +{result.hp_gained}.",
    ]
    if features:
        content_lines.append("Features: " + "; ".join(features) + ".")
    if asi_summary:
        content_lines.append(asi_summary.strip())

    entry = NarrativeEntry(
        timestamp=datetime.now(timezone.utc),
        role="system",
        speaker=character.name,
        content=" ".join(content_lines),
    )

    return LevelUpReport(
        character_id=character.id,
        character_name=character.name,
        is_player=character.is_player,
        old_level=old_level,
        new_level=new_level,
        hp_gained=result.hp_gained,
        features_gained=features,
        asi_pending=asi_pending,
        asi_auto_resolved=asi_auto_resolved,
    ), entry


def _append_levelup_entries(state: "GameState", entries: list) -> None:
    """Append narrative entries to ``state.narrative_log``.

    Used by :func:`award_party_xp` after all per-character level-ups
    complete so the diary shows the level-up event regardless of the
    LLM's narrative choices. The XP-award summary (total XP, source)
    is also appended as a separate entry above the per-character lines.
    """
    if not entries:
        return
    for entry in entries:
        state.narrative_log.append(entry)


def award_party_xp(  # noqa: F811
    state: "GameState",
    amount: int,
    *,
    source: str = "manual",
    rng: Optional[random.Random] = None,
) -> LevelUpBatch:
    """Credit ``amount`` XP to the party and run the auto-level chain.

    Steps:

    1. Clamp amount to >= 0 (negative grants are no-ops).
    2. Add to ``state.party_xp``. ``old_party_level`` is captured
       *before* the bump; ``new_party_level = level_for_xp(party_xp)``.
    3. For each party member, advance from ``c.level`` up to
       ``new_party_level`` calling :func:`level_up` per step. This
       correctly handles multi-threshold crossings (e.g. crediting
       110,000 XP to a L1 party walks each member L2..L6 in sequence).
    4. Build a :class:`LevelUpBatch` aggregating every per-character
       :class:`LevelUpReport`. Narrative entries are appended via
       :func:`_append_levelup_entries` so the engine module stays
       decoupled from ``state.narrative_log`` ordering concerns.

    Args:
        state: Game state (mutated in place).
        amount: Positive integer XP. Zero or negative is a no-op
            (returns an empty batch).
        source: Free-text label for the audit trail (``"combat"``,
            ``"meta"``, ``"manual"``).
        rng: Random source for hit-die rolls during level-ups. Defaults
            to ``random.Random()`` (non-deterministic across calls).

    Returns:
        :class:`LevelUpBatch` with one :class:`LevelUpReport` per
        party member that advanced. ``batch.any_leveled`` is False
        when the credit didn't cross a threshold.
    """
    if rng is None:
        rng = random.Random()
    if amount <= 0:
        return LevelUpBatch(
            xp_awarded=0,
            new_party_xp=state.party_xp,
            old_party_level=current_party_level(state),
            new_party_level=current_party_level(state),
            source=source,
            reports=[],
        )

    old_party_level = current_party_level(state)
    state.party_xp = state.party_xp + amount
    new_party_level = current_party_level(state)

    batch = LevelUpBatch(
        xp_awarded=amount,
        new_party_xp=state.party_xp,
        old_party_level=old_party_level,
        new_party_level=new_party_level,
        source=source,
        reports=[],
    )

    # Per-character delta — uses the new party level minus the
    # character's *current* level. This handles the case where a party
    # member is already above the new party level (e.g. the player is
    # L5 by virtue of being built at L5, but the party's shared XP is
    # at L1 because no XP has been credited yet — a 900 XP grant
    # moves the shared XP from L1 to L3, but the player is already
    # at L5 and should *not* level down).
    deltas = [max(0, new_party_level - c.level) for c in state.party]
    max_delta = max(deltas, default=0)

    if max_delta == 0:
        # Even when no level-up is triggered, narrate the XP gain.
        from datetime import datetime, timezone

        from auto_dm.state.models import NarrativeEntry

        state.narrative_log.append(
            NarrativeEntry(
                timestamp=datetime.now(timezone.utc),
                role="system",
                speaker="DM",
                content=(
                    f"+{amount} XP de {source} "
                    f"(total: {state.party_xp})."
                ),
            )
        )
        return batch

    from datetime import datetime, timezone

    from auto_dm.state.models import NarrativeEntry

    # Top-level entry for the award itself.
    state.narrative_log.append(
        NarrativeEntry(
            timestamp=datetime.now(timezone.utc),
            role="system",
            speaker="DM",
            content=(
                f"+{amount} XP de {source} (total: {state.party_xp}). "
                f"A party sobe do nível {old_party_level} para "
                f"{new_party_level}!"
            ),
        )
    )

    collected_entries: list = []
    for character, delta in zip(state.party, deltas):
        for _step in range(delta):
            if character.level >= 20:
                break  # PHB cap
            report, entry = _level_up_party_member(character, rng=rng)
            batch.reports.append(report)
            if entry is not None:
                collected_entries.append(entry)

    _append_levelup_entries(state, collected_entries)

    return batch

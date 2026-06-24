"""Tests for the save/load persistence layer."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from auto_dm.persistence import (
    SaveMetadata,
    SaveNotFoundError,
    SchemaMismatchError,
    delete_save,
    list_saves,
    load_metadata,
    load_state,
    save_exists,
    save_state,
    slugify,
)
from auto_dm.state.models import (
    AbilityScores,
    Character,
    Condition,
    GameState,
    NarrativeEntry,
    NPC,
    Quest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_state(
    *,
    campaign_name: str = "Test Campaign",
    player_id: str = "p1",
    party: list[Character] | None = None,
    npcs: list[NPC] | None = None,
    location: str = "Mysterious Forest",
) -> GameState:
    if party is None:
        party = [
            Character(
                id=player_id,
                name="Aria",
                race="Elf",
                **{"class": "Wizard"},
                level=1,
                background="Sage",
                alignment="NG",
                is_player=True,
                abilities=AbilityScores(
                    strength=8,
                    dexterity=14,
                    constitution=12,
                    intelligence=15,
                    wisdom=13,
                    charisma=10,
                ),
                hp_current=6,
                hp_max=6,
                armor_class=12,
                speed=30,
                proficiency_bonus=2,
                hit_dice="1d6",
                hit_dice_remaining=1,
                conditions=[Condition.PRONE],
            )
        ]
    if npcs is None:
        npcs = [
            NPC(
                id="g1",
                name="Goblin",
                hp_current=5,
                hp_max=7,
                armor_class=12,
                speed=30,
                abilities=AbilityScores(
                    strength=8, dexterity=14, constitution=10,
                    intelligence=10, wisdom=8, charisma=8,
                ),
            )
        ]
    return GameState(
        campaign_name=campaign_name,
        started_at=datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc),
        current_location=location,
        party=party,
        npcs=npcs,
        player_character_id=player_id,
        active_quests=[
            Quest(
                id="q1",
                name="Find the missing artifact",
                description="The relic was stolen from the museum.",
            )
        ],
    )


@pytest.fixture
def tmp_saves(tmp_path: Path) -> Path:
    """Per-test save directory (avoids polluting the real saves/ dir)."""
    return tmp_path / "saves"


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_lowercases(self):
        assert slugify("Hello World") == "hello-world"

    def test_replaces_special_chars(self):
        assert slugify("Dragão do Mal!") == "drag-o-do-mal"

    def test_collapses_runs(self):
        assert slugify("a   b___c") == "a-b-c"

    def test_trims_dashes(self):
        assert slugify("--hello--") == "hello"

    def test_fallback_for_empty(self):
        assert slugify("!!!") == "campaign"
        assert slugify("") == "campaign"


# ---------------------------------------------------------------------------
# save_state + load_state roundtrip
# ---------------------------------------------------------------------------


class TestSaveAndLoad:
    def test_roundtrip_preserves_state(self, tmp_saves):
        state = make_state()
        path = save_state(state, saves_dir=tmp_saves)
        assert path.exists()
        loaded = load_state(slugify(state.campaign_name), saves_dir=tmp_saves)
        assert loaded.campaign_name == state.campaign_name
        assert loaded.player_character_id == state.player_character_id
        assert loaded.current_location == state.current_location
        assert len(loaded.party) == 1
        assert loaded.party[0].name == "Aria"
        assert loaded.party[0].class_ == "Wizard"
        assert loaded.party[0].conditions == [Condition.PRONE]
        assert len(loaded.npcs) == 1
        assert loaded.npcs[0].name == "Goblin"
        assert len(loaded.active_quests) == 1
        assert loaded.active_quests[0].name.startswith("Find")

    def test_save_creates_directory(self, tmp_saves):
        state = make_state()
        assert not tmp_saves.exists()
        save_state(state, saves_dir=tmp_saves)
        assert tmp_saves.exists()
        assert (tmp_saves / "test-campaign" / "state.json").exists()

    def test_save_returns_path(self, tmp_saves):
        state = make_state()
        path = save_state(state, saves_dir=tmp_saves)
        assert path == tmp_saves / "test-campaign" / "state.json"

    def test_save_uses_explicit_slug(self, tmp_saves):
        state = make_state(campaign_name="X")
        path = save_state(state, slug="my-run", saves_dir=tmp_saves)
        assert path == tmp_saves / "my-run" / "state.json"
        # Default slug not created
        assert not (tmp_saves / "x").exists()

    def test_save_writes_meta_block(self, tmp_saves):
        state = make_state()
        path = save_state(state, saves_dir=tmp_saves)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        assert "_meta" in data
        assert data["_meta"]["campaign_name"] == "Test Campaign"
        assert data["_meta"]["schema_version"] == 1
        assert "saved_at" in data["_meta"]
        assert "state" in data

    def test_save_overwrites_existing(self, tmp_saves):
        state = make_state(campaign_name="X")
        save_state(state, saves_dir=tmp_saves, slug="s1")
        save_state(state, saves_dir=tmp_saves, slug="s1")  # again
        saves = list_saves(saves_dir=tmp_saves)
        assert len(saves) == 1

    def test_save_atomic_no_partial_file_on_failure(self, tmp_saves, monkeypatch):
        # Make json.dump fail to verify the temp file is cleaned up.
        state = make_state()
        save_state(state, saves_dir=tmp_saves)

        def failing_dump(*args, **kwargs):
            raise RuntimeError("simulated write failure")

        monkeypatch.setattr(json, "dump", failing_dump)
        with pytest.raises(RuntimeError, match="simulated write failure"):
            save_state(state, saves_dir=tmp_saves)
        # No .tmp files should remain
        slug_dir = tmp_saves / slugify(state.campaign_name)
        tmp_files = list(slug_dir.glob(".state.*.json.tmp"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# load_state errors
# ---------------------------------------------------------------------------


class TestLoadErrors:
    def test_missing_file_raises(self, tmp_saves):
        with pytest.raises(SaveNotFoundError):
            load_state("nope", saves_dir=tmp_saves)

    def test_missing_file_error_contains_slug(self, tmp_saves):
        with pytest.raises(SaveNotFoundError, match="nope"):
            load_state("nope", saves_dir=tmp_saves)

    def test_schema_mismatch_raises(self, tmp_saves):
        state = make_state()
        path = save_state(state, saves_dir=tmp_saves)
        # Tamper with the schema_version in the saved file
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data["_meta"]["schema_version"] = 99
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f)
        with pytest.raises(SchemaMismatchError, match="99"):
            load_state(slugify(state.campaign_name), saves_dir=tmp_saves)

    def test_corrupt_json_raises(self, tmp_saves):
        state = make_state()
        path = save_state(state, saves_dir=tmp_saves)
        path.write_text("{ not valid json")
        # Pydantic's parse error surfaces during model_validate.
        with pytest.raises((ValueError, json.JSONDecodeError)):
            load_state(slugify(state.campaign_name), saves_dir=tmp_saves)


# ---------------------------------------------------------------------------
# load_metadata
# ---------------------------------------------------------------------------


class TestLoadMetadata:
    def test_returns_metadata_without_parsing_state(self, tmp_saves):
        state = make_state()
        save_state(state, saves_dir=tmp_saves)
        meta = load_metadata(slugify(state.campaign_name), saves_dir=tmp_saves)
        assert isinstance(meta, SaveMetadata)
        assert meta.campaign_name == "Test Campaign"
        assert meta.schema_version == 1
        assert meta.slug == "test-campaign"
        assert isinstance(meta.saved_at, datetime)
        assert meta.file_path.exists()

    def test_missing_save_raises(self, tmp_saves):
        with pytest.raises(SaveNotFoundError):
            load_metadata("nope", saves_dir=tmp_saves)


# ---------------------------------------------------------------------------
# list_saves
# ---------------------------------------------------------------------------


class TestListSaves:
    def test_empty_dir(self, tmp_saves):
        assert list_saves(saves_dir=tmp_saves) == []

    def test_nonexistent_dir(self, tmp_path):
        assert list_saves(saves_dir=tmp_path / "nope") == []

    def test_lists_all_saves(self, tmp_saves):
        s1 = make_state(campaign_name="A")
        s2 = make_state(campaign_name="B")
        save_state(s1, saves_dir=tmp_saves)
        save_state(s2, saves_dir=tmp_saves)
        saves = list_saves(saves_dir=tmp_saves)
        assert len(saves) == 2
        names = {m.campaign_name for m in saves}
        assert names == {"A", "B"}

    def test_newest_first(self, tmp_saves):
        import time

        s1 = make_state(campaign_name="First")
        s2 = make_state(campaign_name="Second")
        save_state(s1, saves_dir=tmp_saves)
        time.sleep(0.05)  # ensure different mtime/saved_at
        save_state(s2, saves_dir=tmp_saves)
        saves = list_saves(saves_dir=tmp_saves)
        assert saves[0].campaign_name == "Second"
        assert saves[1].campaign_name == "First"

    def test_skips_corrupt_saves(self, tmp_saves):
        save_state(make_state(campaign_name="OK"), saves_dir=tmp_saves)
        # Create a corrupt save dir
        (tmp_saves / "broken").mkdir(parents=True)
        (tmp_saves / "broken" / "state.json").write_text("not json")
        saves = list_saves(saves_dir=tmp_saves)
        # Only the valid one shows up
        assert len(saves) == 1
        assert saves[0].campaign_name == "OK"

    def test_skips_dirs_without_state(self, tmp_saves):
        save_state(make_state(campaign_name="OK"), saves_dir=tmp_saves)
        (tmp_saves / "no_state_here").mkdir(parents=True)
        saves = list_saves(saves_dir=tmp_saves)
        assert len(saves) == 1


# ---------------------------------------------------------------------------
# delete_save
# ---------------------------------------------------------------------------


class TestDeleteSave:
    def test_removes_existing_save(self, tmp_saves):
        state = make_state()
        save_state(state, saves_dir=tmp_saves)
        assert save_exists(slugify(state.campaign_name), saves_dir=tmp_saves)
        result = delete_save(slugify(state.campaign_name), saves_dir=tmp_saves)
        assert result is True
        assert not save_exists(
            slugify(state.campaign_name), saves_dir=tmp_saves
        )

    def test_missing_save_returns_false(self, tmp_saves):
        assert delete_save("nope", saves_dir=tmp_saves) is False

    def test_save_exists_helper(self, tmp_saves):
        assert not save_exists("nope", saves_dir=tmp_saves)
        state = make_state()
        save_state(state, saves_dir=tmp_saves)
        assert save_exists(slugify(state.campaign_name), saves_dir=tmp_saves)


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_save_modify_reload(self, tmp_saves):
        state = make_state()
        slug = slugify(state.campaign_name)
        save_state(state, saves_dir=tmp_saves)

        # Reload, mutate, save again
        loaded = load_state(slug, saves_dir=tmp_saves)
        loaded.current_location = "Dark Cave"
        loaded.party[0].hp_current = 1
        save_state(loaded, saves_dir=tmp_saves)

        reloaded = load_state(slug, saves_dir=tmp_saves)
        assert reloaded.current_location == "Dark Cave"
        assert reloaded.party[0].hp_current == 1

    def test_roundtrip_preserves_narrative_log(self, tmp_saves):
        state = make_state()
        state.narrative_log.append(
            NarrativeEntry(
                timestamp=datetime(2026, 6, 24, 13, 0, tzinfo=timezone.utc),
                role="dm",
                speaker="DM",
                content="Bem-vindos à floresta.",
            )
        )
        save_state(state, saves_dir=tmp_saves)
        loaded = load_state(slugify(state.campaign_name), saves_dir=tmp_saves)
        assert len(loaded.narrative_log) == 1
        assert "floresta" in loaded.narrative_log[0].content

    def test_roundtrip_preserves_combat_state(self, tmp_saves):
        state = make_state()
        state.in_combat = True
        state.initiative_order = ["p1", "g1"]
        state.current_turn_index = 1
        state.round_number = 3
        save_state(state, saves_dir=tmp_saves)
        loaded = load_state(slugify(state.campaign_name), saves_dir=tmp_saves)
        assert loaded.in_combat is True
        assert loaded.initiative_order == ["p1", "g1"]
        assert loaded.current_turn_index == 1
        assert loaded.round_number == 3

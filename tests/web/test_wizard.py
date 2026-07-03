"""Tests for the character creation wizard endpoints (Phase 26c).

Covers:
- ``GET /api/character-options``  — auth required; returns catalog.
- ``POST /api/sessions/with-character`` — happy path + invalid specs
   (unknown race, invalid alignment, too-many skills, etc.).
- Regression: when PHB content is partially missing (e.g. Docker image
  built without ``data/phb/``), the catalog endpoint must NOT 500 — it
  should log a warning and serve whatever races/classes are available.
"""
from __future__ import annotations

import pytest


# ============================================================================
# GET /character-options
# ============================================================================


@pytest.mark.asyncio
async def test_character_options_requires_auth(client):
    resp = await client.get("/api/character-options")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_character_options_returns_catalog(client, auth_token):
    token, user, headers = auth_token
    resp = await client.get("/api/character-options", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    # Required keys
    for key in (
        "races", "classes", "backgrounds",
        "alignments", "levels", "stats_methods", "companions",
    ):
        assert key in body, f"missing key {key}"
    # Spot-check
    race_names = {r["name"] for r in body["races"]}
    assert {"Dwarf", "Elf", "Human"}.issubset(race_names)
    class_names = {c["name"] for c in body["classes"]}
    assert {"Fighter", "Wizard", "Cleric"}.issubset(class_names)
    # Alignments
    assert "LG" in body["alignments"]
    assert "N" in body["alignments"]
    # Levels
    assert body["levels"] == [1, 2, 3, 4, 5]
    # Stats methods
    method_ids = {m["id"] for m in body["stats_methods"]}
    assert {"standard_array", "roll", "point_buy"} == method_ids
    # Companions — Phase 27: pool of 12 covering every PHB class.
    comp_keys = {c["key"] for c in body["companions"]}
    assert comp_keys == {
        "thorgrim", "lyra", "mira", "vex",
        "garrick", "brom", "kael", "sage",
        "maren", "eldra", "tobias", "dax",
    }


@pytest.mark.asyncio
async def test_character_options_classes_have_subclasses(client, auth_token):
    token, user, headers = auth_token
    resp = await client.get("/api/character-options", headers=headers)
    body = resp.json()
    # At least one class should expose subclasses.
    with_subs = [c for c in body["classes"] if c["subclasses"]]
    assert len(with_subs) >= 3, "expected several classes to list subclasses"


@pytest.mark.asyncio
async def test_character_options_classes_have_skills(client, auth_token):
    token, user, headers = auth_token
    resp = await client.get("/api/character-options", headers=headers)
    body = resp.json()
    # Each class lists skill_choices (may be empty for Sorcerer/etc.).
    for c in body["classes"]:
        assert "skill_options" in c
        assert "num_skill_choices" in c
        assert isinstance(c["skill_options"], list)
        assert isinstance(c["num_skill_choices"], int)


@pytest.mark.asyncio
async def test_character_options_spellcasters_include_spell_options(client, auth_token):
    token, user, headers = auth_token
    resp = await client.get("/api/character-options", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    wizard = next(c for c in body["classes"] if c["name"] == "Wizard")
    assert wizard["is_spellcaster"] is True
    assert wizard["spellcasting"]["caster_type"] == "wizard"
    assert wizard["spellcasting"]["ability"] == "intelligence"
    assert wizard["spellcasting"]["limits"]["1"]["cantrips_known"] == 3
    assert wizard["spellcasting"]["limits"]["1"]["spellbook_size"] == 6
    spell_names = {s["name"] for s in wizard["spellcasting"]["spells"]}
    assert {"Fire Bolt", "Magic Missile", "Shield"}.issubset(spell_names)


@pytest.mark.asyncio
async def test_character_options_includes_narration_lengths(client, auth_token):
    """Phase 31: catalog exposes the three narration-length options so
    the wizard can render the <select> from server data (with a hard-
    coded fallback in index.html)."""
    token, user, headers = auth_token
    resp = await client.get("/api/character-options", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "narration_lengths" in body
    ids = {opt["id"] for opt in body["narration_lengths"]}
    assert ids == {"curto", "medio", "longo"}
    for opt in body["narration_lengths"]:
        assert opt["label"]


@pytest.mark.asyncio
async def test_character_options_tolerates_missing_phb_entries(
    client, auth_token, monkeypatch
):
    """Regression: container built without data/phb/ caused 500.

    Simulates ``get_race``/``get_class`` returning ``None`` (PHB cache
    empty). The endpoint must log a warning and still return 200 with
    whatever it could load — never crash with AttributeError.
    """
    from auto_dm.web import routes_setup

    original_get_race = routes_setup.get_race
    original_get_class = routes_setup.get_class

    def _patched_get_race(name):
        # Half of the races are missing.
        return None if name in {"Half-Elf", "Half-Orc", "Tiefling"} else original_get_race(name)

    def _patched_get_class(name):
        return None if name == "Sorcerer" else original_get_class(name)

    monkeypatch.setattr(routes_setup, "get_race", _patched_get_race)
    monkeypatch.setattr(routes_setup, "get_class", _patched_get_class)

    token, user, headers = auth_token
    resp = await client.get("/api/character-options", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    race_names = {r["name"] for r in body["races"]}
    # The remaining 6 races still show up; the missing 3 are skipped.
    assert {"Dwarf", "Elf", "Human", "Gnome", "Halfling", "Dragonborn"}.issubset(race_names)
    assert "Half-Elf" not in race_names
    class_names = {c["name"] for c in body["classes"]}
    assert "Sorcerer" not in class_names
    assert "Wizard" in class_names


# ============================================================================
# POST /sessions/with-character
# ============================================================================


def _valid_spec() -> dict:
    return {
        "name": "Aragorn",
        "race": "Human",
        "class": "Ranger",
        "subclass": "Hunter",
        "background": "Outlander",
        "alignment": "NG",
        "level": 1,
        "stats_method": "standard_array",
        "skills": ["athletics", "perception", "stealth"],
        "starting_weapon": "Longbow",
        "starting_armor": "Leather Armor",
        "starting_shield": False,
    }


@pytest.mark.asyncio
async def test_with_character_requires_auth(client):
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Test",
            "player_character": _valid_spec(),
            "companions": [],
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_with_character_creates_session(client, auth_token):
    token, user, headers = auth_token
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Crônicas da Aliança",
            "player_character": _valid_spec(),
            "companions": ["thorgrim", "lyra"],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "session_id" in body
    # slugify strips non-ASCII letters (ç → '', ô → '').
    assert body["slug"] == "cr-nicas-da-alian-a"
    # State should contain the player + 2 companions = 3 party members.
    party = body["state"]["party"]
    assert len(party) == 3
    # Player is first.
    assert party[0]["name"] == "Aragorn"
    assert party[0]["is_player"] is True
    # Companions are second/third.
    comp_names = {p["name"] for p in party[1:]}
    assert comp_names  # at least one companion


@pytest.mark.asyncio
async def test_with_character_no_companions(client, auth_token):
    token, user, headers = auth_token
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Solo",
            "player_character": _valid_spec(),
            "companions": [],
        },
        headers=headers,
    )
    assert resp.status_code == 201
    party = resp.json()["state"]["party"]
    assert len(party) == 1


@pytest.mark.asyncio
async def test_with_character_unknown_race_rejected(client, auth_token):
    token, user, headers = auth_token
    spec = _valid_spec()
    spec["race"] = "Klingon"
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Bad Race",
            "player_character": spec,
            "companions": [],
        },
        headers=headers,
    )
    assert resp.status_code == 422
    assert "race" in resp.json()["detail"].lower() or "klingon" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_with_character_unknown_class_rejected(client, auth_token):
    token, user, headers = auth_token
    spec = _valid_spec()
    spec["class"] = "Time Lord"
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Bad Class",
            "player_character": spec,
            "companions": [],
        },
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_with_character_invalid_alignment_rejected(client, auth_token):
    token, user, headers = auth_token
    spec = _valid_spec()
    spec["alignment"] = "Pizza"
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Bad Align",
            "player_character": spec,
            "companions": [],
        },
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_with_character_invalid_level_rejected(client, auth_token):
    token, user, headers = auth_token
    spec = _valid_spec()
    spec["level"] = 10  # MVP caps at 5
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "High Level",
            "player_character": spec,
            "companions": [],
        },
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_with_character_unknown_companion_rejected(client, auth_token):
    token, user, headers = auth_token
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Bad Comp",
            "player_character": _valid_spec(),
            "companions": ["spock"],
        },
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_with_character_manual_stats(client, auth_token):
    token, user, headers = auth_token
    spec = _valid_spec()
    spec["stats_method"] = "manual"
    spec["stats"] = [13, 14, 12, 10, 15, 8]
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Manual",
            "player_character": spec,
            "companions": [],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_with_character_roll_stats(client, auth_token):
    token, user, headers = auth_token
    spec = _valid_spec()
    spec["stats_method"] = "roll"
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Rolled",
            "player_character": spec,
            "companions": [],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


# ============================================================================
# POST /companions/roll — Phase 27 synergy roller
# ============================================================================


@pytest.mark.asyncio
async def test_with_character_wizard_spell_selection(client, auth_token):
    token, user, headers = auth_token
    options_resp = await client.get("/api/character-options", headers=headers)
    assert options_resp.status_code == 200
    wizard = next(c for c in options_resp.json()["classes"] if c["name"] == "Wizard")
    spells = wizard["spellcasting"]["spells"]
    cantrips = [s["name"] for s in spells if s["level"] == 0][:3]
    spellbook = [s["name"] for s in spells if s["level"] == 1][:6]

    spec = {
        "name": "Elara",
        "race": "Human",
        "class": "Wizard",
        "subclass": "Evocation",
        "background": "Sage",
        "alignment": "NG",
        "level": 1,
        "stats_method": "standard_array",
        "skills": ["arcana", "history"],
        "spell_selection": {
            "cantrips": cantrips,
            "spellbook": spellbook,
            "spells_prepared": spellbook[:2],
        },
    }
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Arcane Test",
            "player_character": spec,
            "companions": [],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    player = resp.json()["state"]["party"][0]
    sc = player["spellcasting"]
    assert sc is not None
    assert sc["cantrips_known"] == cantrips
    assert sc["spellbook"] == spellbook
    assert sc["spells_prepared"] == spellbook[:2]
    assert sc["spell_slots"] == {"1": 2}


@pytest.mark.asyncio
async def test_with_character_rejects_spell_outside_level(client, auth_token):
    token, user, headers = auth_token
    spec = {
        "name": "Elara",
        "race": "Human",
        "class": "Wizard",
        "subclass": "Evocation",
        "background": "Sage",
        "alignment": "NG",
        "level": 1,
        "stats_method": "standard_array",
        "skills": ["arcana", "history"],
        "spell_selection": {
            "cantrips": ["Fire Bolt", "Light", "Mage Hand"],
            "spellbook": [
                "Magic Missile", "Shield", "Mage Armor",
                "Sleep", "Detect Magic", "Fireball",
            ],
            "spells_prepared": ["Magic Missile"],
        },
    }
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Bad Spell",
            "player_character": spec,
            "companions": [],
        },
        headers=headers,
    )
    assert resp.status_code == 422
    assert "cannot choose" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_roll_companions_requires_auth(client):
    resp = await client.post("/api/companions/roll", json={"class": "Wizard"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_roll_companions_returns_four_candidates(client, auth_token):
    token, user, headers = auth_token
    resp = await client.post(
        "/api/companions/roll",
        json={"class": "Wizard", "subclass": "Evocation"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "candidates" in body
    assert len(body["candidates"]) == 4
    valid_keys = {
        "thorgrim", "lyra", "mira", "vex",
        "garrick", "brom", "kael", "sage",
        "maren", "eldra", "tobias", "dax",
    }
    for cand in body["candidates"]:
        assert cand["key"] in valid_keys
        assert cand["name"]
        assert cand["race"]
        assert cand["class_"]


@pytest.mark.asyncio
async def test_roll_companions_wizard_gets_healer(client, auth_token):
    """Wizard has no healer role → healer guarantee kicks in."""
    token, user, headers = auth_token
    # The default RNG is non-seedable here, so probe several times and
    # require the healer tag to be present in the majority of results.
    healer_results = 0
    for _ in range(5):
        resp = await client.post(
            "/api/companions/roll",
            json={"class": "Wizard"},
            headers=headers,
        )
        assert resp.status_code == 200
        candidates = resp.json()["candidates"]
        # Healable companions: mira (Cleric) and eldra (Druid).
        if any(c["key"] in {"mira", "eldra"} for c in candidates):
            healer_results += 1
    assert healer_results >= 4, f"healer only {healer_results}/5 times"


@pytest.mark.asyncio
async def test_roll_companions_accepts_missing_subclass(client, auth_token):
    token, user, headers = auth_token
    resp = await client.post(
        "/api/companions/roll",
        json={"class": "Fighter"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["candidates"]) == 4


# ============================================================================
# POST /sessions/with-character — Phase 31 narration_length
# ============================================================================


@pytest.mark.asyncio
async def test_with_character_curto_persists_to_state(client, auth_token):
    token, user, headers = auth_token
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Curto Test",
            "player_character": _valid_spec(),
            "companions": [],
            "narration_length": "curto",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["state"]["narration_length"] == "curto"


@pytest.mark.asyncio
async def test_with_character_medio_persists_to_state(client, auth_token):
    token, user, headers = auth_token
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Medio Test",
            "player_character": _valid_spec(),
            "companions": [],
            "narration_length": "medio",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["state"]["narration_length"] == "medio"


@pytest.mark.asyncio
async def test_with_character_omitted_narration_length_defaults_to_longo(
    client, auth_token
):
    """Backward-compat: omitting the field keeps the old verbose default."""
    token, user, headers = auth_token
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Default Test",
            "player_character": _valid_spec(),
            "companions": [],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["state"]["narration_length"] == "longo"


@pytest.mark.asyncio
async def test_with_character_invalid_narration_length_rejected(client, auth_token):
    """Literal type guard: anything outside curto/medio/longo → 422."""
    token, user, headers = auth_token
    resp = await client.post(
        "/api/sessions/with-character",
        json={
            "campaign_name": "Bad Narration",
            "player_character": _valid_spec(),
            "companions": [],
            "narration_length": "epico",
        },
        headers=headers,
    )
    assert resp.status_code == 422

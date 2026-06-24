# Auto DM

> AI-powered solo D&D 5e game master. One human player, a party of
> AI companions, and a fully autonomous AI DM — running entirely
> from your terminal.

See [SPEC.md](SPEC.md) for the full specification and
[PLAN.md](PLAN.md) for the phased implementation plan.

---

## What it does

You are one character in a four-person party. Your three companions
(Thorgrim the dwarf fighter, Lyra the elf ranger, Mira the halfling
cleric, Vex the half-elf rogue) are controlled by an LLM that
reasons about their motivations and tactical preferences. The
Dungeon Master — also an LLM — narrates the world and adjudicates
the story. The **rules engine in Python is authoritative**: dice
rolls, attacks, damage, conditions and death saves are computed in
code. The LLM only narrates.

---

## Quick start

```bash
# 1. Create venv and install
python -m venv .venv
.venv\Scripts\activate           # Windows
# source .venv/bin/activate     # Linux / macOS

pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
# Edit .env and set the API key for your provider (MINIMAX_API_KEY
# is the default; CLAUDE/OPENAI/GEMINI/GLM also supported).

cp config.example.json config.json
# Edit config.json if you want to change model/temperature.

# 3. Play
auto-dm
```

The first launch runs the character creation wizard, asks which
companions you want in your party, then drops you in front of a
REPL.

---

## CLI usage

```text
auto-dm                       Start a new game (character creation + REPL).
auto-dm --load <slug>         Resume a saved campaign.
auto-dm --list-saves          Show all saves.
auto-dm --delete <slug>       Delete a save (asks for confirmation).
auto-dm --model <name>        Override the model from config.json.
auto-dm --help                Full options.
```

### In-game commands

```text
/help                 Show available commands.
/save [slug]          Save the current game (uses campaign name if no slug).
/load <slug>          Load a saved game (replaces current session).
/list                 List available saves.
/status               Show party + combat status.
/quit                 Exit the game.
```

Anything else you type is sent to the DM as a free-form action in
pt-BR (e.g. *"Eu abro a porta com cuidado"*, *"Ataco o goblin!"*).

---

## Architecture (high level)

| Layer | Module | Responsibility |
|------:|--------|----------------|
| CLI | `auto_dm.cli` | REPL, character creation wizard, Rich output. |
| Agents | `auto_dm.agents` | DM + companion LLM wrappers; narrative loop. |
| State | `auto_dm.state` | Pydantic models + StateManager. |
| Engine | `auto_dm.engine` | Dice, combat. **Source of truth for mechanics.** |
| PHB | `auto_dm.phb` | Loads the PHB markdown into structured data. |
| Character | `auto_dm.character` | CharacterBuilder + spell selection. |
| Companions | `auto_dm.companions` | Pre-defined L1 companions. |
| LLM | `auto_dm.llm` | Provider abstraction (Protocol + adapters). |
| Persistence | `auto_dm.persistence` | Save / load JSON. |

The four architectural principles from `SPEC.md` are:

1. **Mechanics are authoritative** — the Python engine decides
   hit/miss/damage; the LLM only narrates.
2. **LLM proposes, engine disposes** — every action is validated and
   resolved by the engine before the LLM sees the result.
3. **Context is managed actively** — recent narrative is summarized
   to keep token usage under control on long campaigns.
4. **Configurable by design** — provider, model, temperature,
   language, narration level all come from `config.json`.

---

## Development

```bash
pytest                       # full test suite (387 tests)
pytest tests/test_cli.py     # just the CLI integration tests
ruff check src/              # lint
ruff format src/             # auto-format
```

Line length: 100. Python 3.11+. `pyproject.toml` is the source of
truth for tooling.

### Project layout

```text
src/auto_dm/
├── cli/             # CLI: app.py, character_flow.py, setup.py, rendering.py
├── agents/          # DM + companion agents, narrative loop
├── state/           # Pydantic models + StateManager
├── engine/          # dice, combat (pure Python)
├── phb/             # PHB markdown loader + lookup
├── character/       # CharacterBuilder + spell selection
├── companions/      # pre-defined party members
├── llm/             # provider abstraction
└── persistence/     # save / load JSON
tests/               # pytest suite (387 tests)
data/phb/            # Player's Handbook markdown (read-only)
SPEC.md              # full spec
PLAN.md              # phased plan
```

---

## Status

**v0.1 (MVP) — feature complete.** Phases 0–9 shipped:

- ✅ Project skeleton, LLM provider abstraction, state models,
  rules engine, PHB loader, character creation, DM agent and
  narrative loop, combat system, AI companions, persistence.
- ✅ Polish: real game loop, character creation wizard, Rich output,
  REPL with meta-commands, CLI subcommands.

Out of scope for v0.1: multi-classing, feats, races/classes/spells
outside the PHB, levels above 5, web UI.

---

## License

This project is dual-licensed:

- **Source code** (`src/`, `tests/`, etc.) — MIT License. See [LICENSE](LICENSE).
- **Game data** under `data/phb/` — derived from the D&D 5e System
  Reference Document v5.1 (SRD 5.1), © Wizards of the Coast LLC, used
  under the [Open Game License v1.0a](data/phb/LICENSE) and the
  [Creative Commons Attribution 4.0 License](https://creativecommons.org/licenses/by/4.0/).
  See `data/phb/LICENSE` for the full OGL text and attribution.

The Markdown conversion of the SRD 5.1 was produced by the community
project [oldmanumby/dnd.srd.5.1](https://github.com/oldmanumby/dnd.srd.5.1)
and the remastered fork
[palikhov/DND5E.SRD.Wiki](https://github.com/palikhov/DND5E.SRD.Wiki).

This project is not affiliated with or endorsed by Wizards of the Coast.
"Dungeons & Dragons" and "D&D" are trademarks of Wizards of the Coast LLC.

### Terminology note

Earlier versions of this project used the term "PHB" (Player's Handbook)
to describe the bundled data. The actual content is the **SRD 5.1**,
which is the free, public subset of D&D 5e published by WotC. The PHB
itself is proprietary and is not included in this repository.

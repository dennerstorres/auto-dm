# Auto DM

> AI-powered solo D&D 5e game master. One human player, a party of
> AI companions, and a fully autonomous AI DM — now running as a web
> app, shipped in Docker.

See [SPEC.md](SPEC.md) for the full specification and
[PLAN.md](PLAN.md) for the phased implementation plan. Production
deploy details live in [DEPLOY.md](DEPLOY.md).

---

## What it does

You are one character in a party. Your companions (a rotating cast
including Thorgrim the dwarf fighter, Lyra the elf ranger, Mira the
halfling cleric, Vex the half-elf rogue, and more) are controlled by
an LLM that reasons about their motivations and tactical preferences.
The Dungeon Master — also an LLM — narrates the world and adjudicates
the story. The **rules engine in Python is authoritative**: dice
rolls, attacks, damage, conditions and death saves are computed in
code. The LLM only narrates.

The game is a **web app** — a FastAPI backend (auth, sessions, save
state in Postgres, live sessions in Redis, SSE streaming) serving a
vanilla HTML/CSS/JS frontend with a full in-browser character creation
wizard. A terminal CLI is still available for local/headless play.

---

## Quick start (Docker — recommended)

There are two compose stacks:

- **`docker-compose.dev.yml`** — local development. Brings up
  **Postgres + Redis + the backend** in one go and serves the frontend
  at `http://localhost:14004/`. No external services required.
- **`docker-compose.yml`** — production. Backend only; expects
  Postgres + Redis to already be running on the host. Binds the API
  to `127.0.0.1:4004` behind a reverse proxy. See [DEPLOY.md](DEPLOY.md).

### Local dev stack

```bash
# 1. Configure secrets
cp .env.example .env
# Edit .env — set at minimum:
#   JWT_SECRET          (≥32 chars; e.g. `openssl rand -hex 32`)
#   AUTO_DM_API_KEY     (your Minimax key, sk-...)
#   AUTO_DM_PROVIDER    (defaults to "minimax")

# 2. Boot the whole stack (Postgres + Redis + backend)
docker compose -f docker-compose.dev.yml up --build

# 3. Open the game in your browser
#    http://localhost:14004
```

Tear down (keeps the Postgres volume):

```bash
docker compose -f docker-compose.dev.yml down
```

Wipe all data (including saves):

```bash
docker compose -f docker-compose.dev.yml down -v
```

> **Port collisions:** the dev stack uses alt ports to avoid clashing
> with any local Postgres/Redis — Postgres on `127.0.0.1:25432`,
> Redis on `127.0.0.1:26379`, backend on `127.0.0.1:14004`. The
> backend talks to Postgres/Redis over the compose network, so the
> host-side ports are only for your inspection.

### Required environment variables

All runtime config flows through the environment (compose
interpolates `${VAR}` from your `.env`):

| Variable | Required | Notes |
|---|---|---|
| `JWT_SECRET` | ✅ | ≥32 chars. `openssl rand -hex 32` |
| `AUTO_DM_API_KEY` | ✅ | Minimax API key (`sk-...`) |
| `AUTO_DM_PROVIDER` | defaults `minimax` | Only Minimax is wired up |
| `AUTO_DM_MODEL` | defaults `MiniMax-Text-01` | |
| `AUTO_DM_BASE_URL` | optional | Leave empty for provider default |
| `AUTO_DM_TEMPERATURE` | defaults `0.8` | |
| `AUTO_DM_MAX_TOKENS` | defaults `2048` | |
| `DATABASE_URL` | dev: pre-set; prod: must set | `postgresql+asyncpg://...` |
| `REDIS_URL` | dev: pre-set; prod: must set | `redis://...` |
| `FRONTEND_URL` | yes (CORS) | Comma-separated allowed origins |
| `INVITE_CODE` | optional | Gate signup; leave empty for open signup |

The first launch gives you the auth screen → lobby → in-browser
character creation wizard (name → race → class → subclass →
background → alignment → level → stats → skills → companions →
confirm), then the game screen with `/help /save /load /list /status
/quit` and live SSE streaming.

---

## Running the CLI instead (optional)

If you want the terminal experience (no Postgres/Redis/web), you can
still run the standalone CLI:

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
# source .venv/bin/activate     # Linux / macOS

pip install -e ".[dev]"

cp .env.example .env             # set AUTO_DM_API_KEY
cp config.example.json config.json

auto-dm                          # new game (wizard + REPL)
auto-dm --load <slug>            # resume a saved campaign
auto-dm --list-saves
auto-dm --delete <slug>
auto-dm --help
```

### In-game commands (shared by CLI and web)

```text
/help                 Show available commands.
/save [slug]          Save the current game.
/load <slug>          Load a saved game.
/list                 List available saves.
/status               Show party + combat status.
/quit                 Exit the game.
```

Anything else is sent to the DM as a free-form action in pt-BR
(e.g. *"Eu abro a porta com cuidado"*, *"Ataco o goblin!"*).

---

## Architecture (high level)

| Layer | Module | Responsibility |
|------:|--------|----------------|
| Web | `auto_dm.web` | FastAPI server: auth, sessions, SSE, REST, static frontend. |
| CLI | `auto_dm.cli` | Terminal REPL, character creation wizard, Rich output. |
| Agents | `auto_dm.agents` | DM + companion LLM wrappers; narrative loop. |
| State | `auto_dm.state` | Pydantic models + StateManager. |
| Engine | `auto_dm.engine` | Dice, combat. **Source of truth for mechanics.** |
| PHB | `auto_dm.phb` | Loads the SRD 5.1 markdown into structured data. |
| Character | `auto_dm.character` | CharacterBuilder + spell selection. |
| Companions | `auto_dm.companions` | Pre-defined roster + party roll/synergy. |
| LLM | `auto_dm.llm` | Provider abstraction (Protocol + adapters). |
| Persistence | `auto_dm.persistence` | JSON save/load (CLI); Postgres (web). |

The four architectural principles from `SPEC.md` are:

1. **Mechanics are authoritative** — the Python engine decides
   hit/miss/damage; the LLM only narrates.
2. **LLM proposes, engine disposes** — every action is validated and
   resolved by the engine before the LLM sees the result.
3. **Context is managed actively** — recent narrative is summarized
   to keep token usage under control on long campaigns.
4. **Configurable by design** — provider, model, temperature,
   language, narration level all come from environment + config.

### Deploy topology

```
┌──────────────┐   HTTPS    ┌────────────┐   HTTP    ┌──────────────────┐
│  Browser     │ ─────────► │  nginx/TLS │ ────────► │  docker: auto-dm │
│  (static JS) │            │  :443      │  :4004    │  FastAPI/uvicorn │
└──────────────┘            └────────────┘           └────────┬─────────┘
                                                               │ asyncpg / redis
                                               ┌───────────────┴────────────┐
                                               ▼                            ▼
                                    ┌──────────────────┐         ┌──────────────────┐
                                    │  docker: postgres│         │  docker: redis   │
                                    └──────────────────┘         └──────────────────┘
```

---

## Development

```bash
pytest                       # full test suite (1584 tests)
ruff check src/              # lint
ruff format src/             # auto-format
```

Line length: 100. Python 3.11+. `pyproject.toml` is the source of
truth for tooling.

### Project layout

```text
src/auto_dm/
├── web/              # FastAPI: server.py, routes_*, db.py, sse.py, static/
├── cli/              # CLI: app.py, character_flow.py, setup.py, rendering.py
├── agents/           # DM + companion agents, narrative loop
├── state/            # Pydantic models + StateManager
├── engine/           # dice, combat, resources, conditions (pure Python)
├── phb/              # SRD 5.1 markdown loader + lookup
├── character/        # CharacterBuilder + spell selection + level-up
├── companions/       # roster + party roll/synergy
├── llm/              # provider abstraction
└── persistence/      # save / load JSON
tests/                # pytest suite (1584 tests)
data/phb/             # SRD 5.1 markdown (read-only)
Dockerfile            # single-stage python:3.11-slim image
docker-compose.yml            # prod: backend only (external Postgres+Redis)
docker-compose.dev.yml        # dev: Postgres + Redis + backend
SPEC.md / PLAN.md / DEPLOY.md
```

---

## Status

**v0.1 (MVP) — feature complete, web-deployed.** Highlights:

- Full rules engine: dice, combat, conditions (PHB 14 + tactical),
  spellcasting with slots/upcast/concentration, barbarian rage, sneak
  attack, divine smite, fighting styles, resource pools, passive
  defenses, subclasses, leveling L1–L20 with class capstones.
- PHB/SRD loader: 9 races, 12 classes, subclasses, ~290 spells,
  monsters, magic items, backgrounds, tools, gear, mounts, vehicles,
  poisons/traps/diseases, languages.
- DM + companion agents with a narrative loop; 12-companion roster
  with party roll and synergy.
- **Web app**: FastAPI + Postgres + Redis, bcrypt/JWT auth, invite-code
  gate, SSE streaming, in-browser character creation wizard, lobby,
  save/load — all containerized.
- Standalone CLI still available for headless/terminal play.

Out of scope for v0.1: multi-classing, feats, content outside the
PHB/SRD.

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

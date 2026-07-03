# Auto DM

AI-powered solo D&D 5e game master. Um jogador humano, party de companheiros controlados por IA, e o mestre é inteiramente a IA.

> Veja `SPEC.md` para a especificação completa e `PLAN.md` para o plano por fases.

---

## Princípios arquiteturais inegociáveis

1. **Mecânica é autoritativa.** O motor de regras em Python é a fonte da verdade. O LLM **narra**, mas nunca decide mecânica. Toda ação de combate passa por validação e execução no engine.
2. **LLM propõe, engine dispõe.** Companheiros e jogador enviam intenções (texto livre ou JSON estruturado), engine valida se a ação é possível, executa rolagens, aplica dano, atualiza estado, e devolve resultado. LLM só vê o resultado pra narrar.
3. **Contexto é gerenciado ativamente.** Resumos periódicos evitam estourar tokens em campanhas longas.
4. **Configurável por design.** Provider, modelo, temperatura, idioma, nível de narração — nada hardcoded de forma oculta.

## Stack

- **Python 3.11+**
- **Pydantic** para modelos de estado e validação
- **Click + Rich** para CLI
- **LangChain/LangGraph** para orquestração de agentes (DM + companheiros)
- **Providers LLM**: Claude, OpenAI, Gemini, GLM, **Minimax** (provider ativo do usuário — única chave disponível)
- **PHB 5e** em `data/phb/` como fonte de verdade para regras (não hardcoded em Python)

## Estrutura do projeto

```
src/auto_dm/
├── llm/          # abstração de providers (Protocol + adapters)
├── engine/       # motor de regras (Python puro, SEM LLM)
├── state/        # modelos Pydantic (Character, GameState, Action...)
├── phb/          # loader do SRD 5.1 (parser, models, lookup)
├── character/    # CharacterBuilder + spell selection
├── agents/       # DM agent + companion agents
├── companions/   # pre-defined companion roster + party roll/synergy
├── persistence/  # save/load JSON (CLI)
├── web/          # backend FastAPI (auth, sessões, REST, SSE) + static/
└── cli/          # interface de linha de comando
```

Raiz do repo: `Dockerfile`, `docker-compose.yml` (prod), `docker-compose.dev.yml`
(dev com Postgres+Redis+backend), `DEPLOY.md` (deploy completo).

## Comandos úteis

### Rodar o projeto (Docker — forma principal)

```bash
# Dev: sobe Postgres + Redis + backend; frontend em http://localhost:14004
cp .env.example .env          # preencher JWT_SECRET (≥32) e AUTO_DM_API_KEY
docker compose -f docker-compose.dev.yml up --build

# Prod: backend only (Postgres+Redis externos), bind 127.0.0.1:4004
docker compose up -d --build
docker compose logs -f auto-dm

# Tear down
docker compose -f docker-compose.dev.yml down      # dev (mantém volume)
docker compose -f docker-compose.dev.yml down -v   # dev + apaga dados
docker compose down                                # prod
```

Variáveis via `.env` (compose interpola `${VAR}`): `JWT_SECRET`,
`AUTO_DM_API_KEY`/`AUTO_DM_PROVIDER`/`AUTO_DM_MODEL`,
`DATABASE_URL`, `REDIS_URL`, `FRONTEND_URL`, `INVITE_CODE`. Detalhes de
deploy (nginx, TLS, Vercel, backups) em `DEPLOY.md`.

### Desenvolvimento local (sem Docker)

```bash
pip install -e ".[dev]"   # instalar em modo dev
pytest                     # rodar testes
ruff check .               # lint
auto-dm                    # rodar o jogo pela CLI (terminal/headless)
auto-dm --help             # opções CLI
```

> **Provider ativo é Minimax** — o adapter é carregado do ambiente via
> `LLMConfig.from_env(prefix="AUTO_DM_")`. Não há adapter novo pra
> implementar; CLI/web ambos leem as mesmas vars `AUTO_DM_*`.

## Regras para Claude Code

- **Nunca ler `.env`** — contém API keys. Usar `.env.example` como referência de template.
- **`data/phb/` é leitura livre** — esses `.md` são a fonte de regras. Conteúdo derivado do D&D 5e **SRD v5.1** (Open Game License + CC BY 4.0) — não é o PHB completo. Arquivos com prefixo `#` (ex: `# Racial Traits.md`) são índices introdutórios; sem prefixo são conteúdo.
- **Provider ativo é Minimax** — não implementar adapters novos (Claude/Gemini/OpenAI/GLM) a menos que o usuário peça explicitamente. Foco no Minimax primeiro.
- **D&D 5e, PHB only** no MVP. Níveis 1-5 no MVP (estendido a 1-20 pelas Fases 25f/25g). Sem multiclasse, sem feats, sem classes/raças/magias fora do PHB.
- **Idioma do produto**: pt-BR (interface, narração, mensagens). Código/identificadores em inglês.
- **Tarefas são rastreadas** via TaskList. Ao começar uma fase, marcá-la `in_progress`; ao terminar, `completed`. Criar tasks pra qualquer trabalho com 3+ passos.
- **Aprovar antes de ações destrutivas** (deletar/sobrescrever saves, rodar comandos perigosos).
- **Style**: line-length 100, target Python 3.11, ruff como linter, pytest para testes.

## Onde estamos

- ✅ Fase 0 — Project skeleton
- ✅ Fase 1 — LLM provider abstraction (Minimax + thinking strip)
- ✅ Fase 2 — State models (Character, GameState, Action, etc — 26 testes)
- ✅ Fase 3 — Rules engine core (3a dice ✅, 3b combat ✅ — 56 testes)
- ✅ Fase 4 — PHB loader (9 raças, 12 classes, 290 magias, 37 armas, 13 armaduras, 15 condições — 75 testes)
- ✅ Fase 5 — Character creation (CharacterBuilder fluent + spell selection: 62 testes)
- ✅ Fase 6 — DM Agent and narrative loop (DM_SYSTEM_PROMPT, DMAgent, parser, process_player_action: 48 testes)
- ✅ Fase 7 — Combat system (CombatEngine orquestrador: initiative/turnos, attack/dash/dodge/help/hide/search/ready, death_save, end_combat, validation: 38 testes + 6 integração com narrative loop)
- ✅ Fase 8 — AI Companions (CompanionAgent, roster com 4 personagens pré-definidos, run_companion_turn, parser, prompt personalizado: 32 testes)
- ✅ Fase 9 — Persistence (save_state/load_state atômico com _meta block, slugify, list_saves, delete_save: 30 testes)
- ⏭ Fase 10 — Remaining LLM providers (pulada a pedido do usuário — provider ativo é Minimax)
- ✅ Fase 11 — Polish (GameApp REPL com meta-comandos /help /save /load /list /status /quit, Rich rendering, character creation wizard, setup_new_game, auto-save cadence, integração com main.py via Click, 26 testes de integração)
- ✅ Fase 12 — Conditions engine (PHB 14 conditions + 2 tactical, exhaustion 6-level, immunity > resistance > vulnerability, applied via combat/damage/conditions: 83 testes)
- ✅ Fase 13 — Adventuring (short rest com hit dice, long rest com clears, falling 1d6/10ft cap 20d6, suffocation 1+CON mod rounds: 25 testes)
- ✅ Fase 14 — Languages catalog (PHB 16 standard + 6 exotic, exposto via get_language/get_all_languages: 8 testes)
- ✅ Fase 15 — Poisons, Traps, Diseases (PHB parser com DC/damage/conditions, apply_poison/trigger_trap/apply_disease com saving_throw, tick_effects no round: 31 testes)
- ✅ Fase 16 — Cover and Opportunity Attack (cover_ac_bonus/dex_save_bonus, três níveis + total, attack_roll aplica cover, OPPORTUNITY_ATTACK handler com reaction_used: 12 testes)
- ✅ Fase 17 — ASI + Inspiration (ASI levels 4/8/12/16/19, +2/+1 split com cap 20, inspiration no-stockpile, spend → pending_advantage consumido por attack_roll e saving_throw: 38 testes + 4 integração)
- ✅ Fase 18 — Complete spellcasting (slots com upcast, prepare/unprepare, known-casters, concentration com CON save vs max(10, dmg/2), ritual casting, cast_spell + handler em CombatEngine, concentração quebra ao tomar dano em attack/OA: 56 testes)
- 🐛 Fix: regex do PHB spell loader tolerava "(ritual)" no header (era 0 rituais carregados → agora ~30)
- ✅ Fase 19 — Barbarian Rage (is_raging/rages_used/rages_max em Character, engine/rage.py com can_rage/enter_rage/end_rage/tick_rage_duration/recover_rages, dano bonus STR melee +2/+3/+4 por nível, resistência bludgeoning/piercing/slashing, vantagem em STR save, ActionType.RAGE handler, auto-end em incapacitação: 63 testes)
- ✅ Fase 20 — Sneak Attack (Rogue), Divine Smite (Paladin), Extra Attack (martial 0/1/2/3 by level tracked em CombatEngine._attacks_remaining), Fighting Style (archery/dueling/defense/GWF/protection/TWF, integrated em attack/damage), Cunning Action (Rogue L2 dash/disengage/hide como bonus: 113 testes somando as 5 sub-fases)
- ✅ Fase 21 — Resource Pools (engine/resources.py consolidando Ki/Sorcery/Lay on Hands/Second Wind/Action Surge/Channel Divinity/Bardic Inspiration com aggregate short_rest_recovery/long_rest_recovery, Sorcery slot↔points conversion PHB p.101: 30 testes); Arcane Recovery (engine/arcane_recovery.py: Wizard L1, cap ceil(L/2), no slots above 5: 15 testes)
- ✅ Fase 22 — CombatEngine handlers (SECOND_WIND, ACTION_SURGE, LAY_ON_HANDS, CHANNEL_DIVINITY, BARDIC_INSPIRATION, FLURRY_OF_BLOWS, STUNNING_STRIKE, UNCANNY_DODGE, RECKLESS_ATTACK, INDOMITABLE — todos com validação de classe + flag de pool, registered em _ACTION_HANDLERS: 37 testes)
- ✅ Fase 23 — Passive defenses (engine/defenses.py: Unarmored Defense Barb DEX+CON/Monk DEX+WIS, Danger Sense (adv DEX save), Brutal Critical (+1/+2/+3 dice on crit, integrated em damage_roll), Evasion (Rogue/Monk L7), Aura of Protection (Paladin L6/18, +CHA mod to saves for self+allies in 10/30 ft, integrated em saving_throw): 29 testes
- ✅ Fase 24 — Specialists (engine/specialists.py: Wild Shape Druid L2+ com CR cap 1/4→1/2→1 e form catalog, Favored Enemy (Ranger), Eldritch Invocations (Warlock), Divine Sense, Destroy Undead CR cap): 25 testes
- ✅ Fase 25a — Monsters loader (Monster/MonsterAction/MonsterTrait + enums em phb/models.py; load_monsters/parse_monster_file em phb/loader.py com parser próprio para o formato `**Field** valor`; get_monster/get_monsters com cache; monster_to_npc adapter; 80 testes)
- ✅ Fase 25b — Subclasses selection + features (get_subclass/get_subclasses_for/get_all_subclasses em phb/lookup.py; Character.subclass_features + character/level_up.py com apply_subclass_features / list_subclass_features / features_gained_at_level / has_subclass_features; CharacterBuilder chama apply_subclass_features no build; wizard em cli/character_flow.py com _prompt_subclass entre classe e background; 27 testes)
- ✅ Fase 25c — Backgrounds + Tools + Gear loaders (Background/PHBTool/PHBEquipmentPack models + ToolCategory/GearCategory enums; load_backgrounds com 13 backgrounds PHB, load_tools com 36 tools (artisan/gaming/musical/kit), load_gear com 132 itens, load_packs com 7 packs; parse_weight_lb suporta frações Unicode; CharacterBuilder._build_proficiencies auto-aplica skill/tool/lang do background, with_starting_pack popula inventário; 54 testes)
- ✅ Fase 25d — Magic Items loader + wire-in (MagicItem/Rarity/MagicItemType em phb/models.py; load_magic_items em phb/loader.py com parser de tagline que cobre rarity varies/multi-rarity/attunement clauses (incluindo "by a paladin"); get_magic_item/get_magic_items/roll_magic_item em lookup.py; Item.magic_bonus+requires_attunement+rarity em state/models.py; engine/combat.py: +1/+2/+3 de weapon em attack_roll+damage_roll, magic_armor_bonus somado de armor/shield em effective_ac; 31 testes)
- ✅ Fase 25e — Movement + Mounts/Vehicles (Mount/Vehicle models + VehicleType enum; load_mounts com 8 mounts + load_vehicles com 16 (10 land + 6 water), parse de mph com fração Unicode (1½, 2½); engine/movement.py com climb_check/swim_check (Athletics) + grapple/shove (contested Athletics) + forced_disadvantage_swim para plate; Character.is_mounted+mount_id, NPC.is_mount+rider_id+is_vehicle+vehicle_type; CombatEngine handlers MOUNT/DISMOUNT; 37 testes)
- ✅ Fase 25f — Leveling L6-L11 (XP_THRESHOLDS L1-L20 + level_for_xp/xp_to_next_level/proficiency_bonus_for; level_up com HP+prof+extra_attacks+hit_dice_remaining; apply_class_features em character/level_up.py com gates Barbarian L2/L7, Paladin L6/L10, Rogue L2/L5/L7, Monk L7, Fighter L9; Character.has_feral_instinct+aura_of_courage_active flags; /level-up meta-command com narration; 69 testes)
- ✅ Fase 25h — REPL polish + **fix crítico do companion turn integration** (CombatEngine.next_actor_id/is_player_turn/is_companion_turn; GameApp._run_companion_cycle itera initiative após o player até voltar a ele ou combate acabar; NarrativeResult.companion_results; META_COMMANDS expandido com /encounter /look /inventory /conditions /spells /level-up; render_inventory/render_conditions/render_spellbook em cli/rendering.py; 36 testes)
- ✅ Fase 25g — Leveling L12–L20 + class capstones (spell slot tables L1–L20 completas para full casters (Wizard/Cleric/Druid/Bard/Sorcerer PHB p.113) e half casters (Paladin/Ranger); Warlock Pact Magic 1/2/3/4 slots L1/L2/L11/L17 todos no mesmo slot level; cantrips known L1–L20 com thresholds 1/4/10; spells known/prepared caps (Bard 22, Sorcerer 15, Wizard spellbook 44); 9th level spell access em L17+; capstones por classe em L20: Primal Champion Barbarian (+4 STR/CON max 24, +2 weapon damage), Signature Spells Wizard (2 magias ≤3º, sempre preparadas, 1x/short rest free cast), Arcane Apotheosis Sorcerer (cap 20 sorcery points), Archdruid Druid (cast em Wild Shape), Perfect Self Monk (4 ki → recover all), Foe Slayer Ranger (+WIS atk/dmg favored enemy 1x/turn), Stroke of Luck Rogue (1x/short rest), Eldritch Master Warlock (refuel pact slots 1x/long rest), Divine Intervention Improvement Cleric; Mystic Arcanum Warlock (6º L11, 7º L13, 8º L15, 9º L17) com learn/cast/reset; engine/class_features.py novo módulo runtime; brutal_critical_dice 1/2/3 em L9/13/17; Character flags novos (has_primal_champion/has_arcane_apotheosis/has_signature_spells/has_archdruid/has_perfect_self/has_foe_slayer/has_stroke_of_luck/has_eldritch_master/has_divine_intervention_improvement) + mystic_arcanum_known/uses + signature_spell_names/uses_remaining; 118 testes)
- ✅ Fase 26a — Web FastAPI skeleton + auth (signup/login/me + bcrypt+JWT) + Postgres User/Save ORM + Redis-backed SessionManager + console UI HTML/CSS/JS + auth screen + lobby screen + game screen com `/help /save /load /list /status /quit`, integração com StaticFiles mount e lifespan startup; FRONTEND_URL (comma-separated) controla CORS; JWT_SECRET ≥32 chars obrigatório; 30 testes web
- ❌ Fase 26b — **Removida** a pedido do usuário. `web/sse.py` + rotas `POST /api/sessions/{sid}/stream` e `/opening/stream` + toggle frontend + funções `sendInputStream`/`playOpeningStream` + helper `iter_stream_with_usage` em `llm/usage.py` + métodos `DMAgent.stream_with_usage`/`stream_opening_with_usage` + `iter_stream_with_usage` em `openai_compatible.py` + `_LegacyProvider.stream` stub nos testes foram deletados; `LLMProvider` Protocol perdeu `stream()`; `chat_with_usage`/`UsageReport` preservados (usados pelo billing da Fase 30). Mensagens agora sempre chegam inteiras via `POST /input`.
- ✅ Fase 26c — Character creation wizard no browser (web/routes_setup.py com GET /api/character-options retornando catalog de raças+subraces, classes+subclasses+skill_options+num_skill_choices+is_spellcaster, backgrounds, alignments, levels, stats_methods, companions; POST /api/sessions/with-character aceita spec + constrói Character via CharacterBuilder + companheiros via COMPANION_FACTORIES + cria GameState+sessão+slug; PlayerCharacterSpec aceita stats_method standard_array/roll/point_buy/manual com validação de alignment+level+race+class+companion; wizard HTML/CSS/JS de 11 passos com progress dots (name → race → class → subclass → background → alignment → level → stats → skills → companions → confirm) usando choices clicáveis, subrace dropdown, skill checkboxes respeitando num_skill_choices da classe, confirmação visual; integração no lobby via botão "Criar novo personagem (wizard)"; auto-save na finalização; 14 testes wizard)
- ✅ Fase 26d — Deploy (Dockerfile python:3.11-slim single-stage com user não-root, HEALTHCHECK, EXPOSE 4004, uvicorn --factory; .dockerignore excluindo .env, .git, tests/, caches; docker-compose.yml com serviço auto-dm exposto em 127.0.0.1:4004:4004 + env passthrough via ${VAR:?error} pra JWT_SECRET/AUTO_DM_API_KEY + healthcheck; LLMConfig.from_env(prefix="AUTO_DM_") lê provider/api_key/model/base_url/temperature/max_tokens/thinking do ambiente; web/server.py::_default_provider_factory agora carrega MinimaxProvider do env sem precisar passar factory explicitamente; testes cobrem from_env happy/invalid/missing + factory errors; DEPLOY.md cobrindo prereqs Debian+Docker+Postgres+Redis já rodando, .env com DATABASE_URL/REDIS_URL/JWT_SECRET/FRONTEND_URL/AUTO_DM_*, setup do schema idempotente, nginx reverse proxy com proxy_buffering off + chunked_transfer_encoding pra SSE, TLS via certbot, opções Vercel vs backend-serving-static, backups Postgres+Redis, troubleshooting table, hardening checklist; 6 testes LLMConfig.from_env)
- ✅ Fase 26e — Invite-code gate (config.py: invite_code Optional[str] via env INVITE_CODE; routes_auth.py SignupRequest.invite_code + signup endpoint valida com hmac.compare_digest antes de criar user; 403 com mensagem genérica "missing ou wrong" pra não vazar qual falhou; login não afetado pelo gate; testes cobrem open signup, ignored field, gated sem code/wrong code/correct code, paridade de mensagens missing↔wrong, login pós-gate, sanity de compare_digest; frontend ganha input auth-invite + envia invite_code só quando preenchido; 8 testes)
- ✅ Fase 27 — Companion pool expansion + synergy roll (`roster.py` com 12 factories cobrindo todas as classes PHB: thorgrim/lyra/mira/vex + garrick (paladino humano, Devotion, sem spellcasting em L1), brom (bárbaro meio-orc, Berserker), kael (mago elfo da floresta, Evocation, spellbook 6), sage (sorcerer meio-elfo, Draconic Bloodline), maren (monja humana, Open Hand), eldra (druida gnoma da floresta, Land), tobias (bardo dragonborn, Lore), dax (warlock halfling stout, Fiend, pact slot L1); todas as personalidades + traits/ideals/bonds/flaws únicos em pt-BR; `COMPANION_BLURBS` centralizado em `roster.py` removendo duplicação em `cli/setup.py` e `web/routes_setup.py`; novo módulo `auto_dm.companions.selection` com `ROLE_TAGS`/`_CLASS_ROLES`/`SYNERGY_BIAS`/`SAME_CLASS_WEIGHT` e função `roll_party_candidates(player, k=4, *, rng=None)` usando weighted random sampling sem reposição com penalidade same-class 0.3 + synergy boost por tag ausente (healer 2.0, tank 1.5) + healer-guarantee retry até 50x quando player não tem role healer; CLI reordena `setup_new_game` pra criar player antes de promptar companions e usa `_prompt_companions(inp, out, player)` listando apenas os 4 rolados; web ganha endpoint `POST /api/companions/roll` que recebe `{class, subclass}` e retorna 4 candidates via stub Character; wizard `openWizard` deixa `wizardState.companions` vazio e `renderWizardCompanions` faz lazy fetch dos candidates antes de renderizar checkboxes; 12 testes por companion + 14 testes em `test_companions_selection.py` cobrindo deterministicidade, deduplicação, same-class avoidance, healer guarantee, role coverage + 3 testes novos no wizard endpoint; bugfix incidental: `getattr(ch, "class", None)` → `getattr(ch, "class_", None)` em `routes_setup.py` (Pydantic alias; `class` é palavra reservada) — `class_` retornava vazio no catalog; ~32 testes novos)
- ✅ Fase 28 — Input-blocking + busy feedback (CLI + web). `src/auto_dm/main.py::_run_repl` envolve `game.process_input` em `Console.status("dots", "Mestre está pensando...")` para linhas não-meta (meta-comandos `/...` continuam instantâneos — detectados por prefixo `/` antes do `with`); `src/auto_dm/web/static/app.js` ganha flag module-level `let busy` + helpers `lockUi()` / `unlockUi()` / `showTyping()` / `hideTyping()` que gateiam `#cmd`, `#send-btn`, `#stream-toggle` e protegem o keydown handler do Enter; `try/finally` em `sendInput` garante unlock em qualquer code path (success, HTTPException, network failure, malformed response); typing indicator = `<div class="typing-indicator">` com 3 dots animados via CSS `@keyframes typing-bounce` (stagger 0.15s) appended em `#output` abaixo da linha do jogador, removido pelo `hideTyping()` no `finally`; stream path perde o `sendBtn.disabled` inline (agora via `lockUi()` herdado do `sendInput`); `style.css` ganha `input:disabled` (cursor not-allowed, bg `#1a1e28`) e o bloco `.typing-indicator` (mantém `button:disabled` separado — diferentes tratamentos visuais); sem mudanças server-side (per-session lock deferido — fora de escopo desta fase, documentado como limitação aceitável pra multi-tab race); wizard, lobby e auth screens não tocados; **1584 testes passando** (0 novos automatizados — sem JS test runner; verificação manual via checklist na plan).

**1573 testes passando** (1584 anteriores − 7 SSE removidos + 3 streaming-related em `test_provider_usage` + 1 em `test_dm_agent`).

- ✅ Fase 29 — User roles (admin/user) + recursos de admin. `models.py::UserRole` enum (`USER`/`ADMIN`) + `User.role` (default `'user'`, `server_default='user'`); migração idempotente `server.py::_ensure_user_role` (mesmo padrão de `_ensure_save_columns`) roda no `lifespan`; seed do admin único no startup via `server.py::_seed_admin(settings)` — cria `User(role=admin)` com `ADMIN_USERNAME` (default `admin`) + `ADMIN_PASSWORD` se definida (sem senha, loga WARNING e pula), idempotente (não duplica); `config.py::Settings.admin_username/admin_password`; `auth.py::require_admin` dependency (403 se `role != admin`); `UserOut.role` exposto em signup/login/me; `signup` sempre cria `role=user` (não aceita role no body — sem escalonamento); `routes_game.py::POST /api/sessions` (Criar jogo vazio) agora `Depends(require_admin)`; novo `routes_admin.py` (router `/api/admin`, todas via `require_admin`): `GET /saves?archived=` lista saves de **todos** usuários com username (joinedload Save.user), `GET /saves/{user_id}/{slug}` retorna snapshot read-only `state`+`narrative_log` (sem criar sessão, sem LLM), `DELETE /saves/{user_id}/{slug}` exclui save de qualquer usuário (archived ou não); frontend `app.js::isAdmin()` gateia UI — lobby admin busca `/api/admin/saves` com tag `@username` por linha + botão "Excluir" (danger, confirm) por save, "Visualizar" abre jogo **read-only** (`viewSaveReadOnly` → `enterGame({readOnly, narrativeLog})` que renderiza `renderNarrativeLog` desabilitando `#cmd`/`#send-btn` e respeitado por `unlockUi`/`sendInput`/`returnToLobby`); "Opções avançadas"/"Criar jogo vazio" ocultos para `user`; header mostra `(admin)`; `index.html`/`style.css` cache bump v32 + `.owner`/`button.danger`. **88 testes web passando** (69 anteriores + 19 novos em `test_admin_roles.py` cobrindo role no UserOut, signup anti-escalation, `/api/sessions` 403 p/ user, rotas admin 401/403/200, listagem cross-user com dono, inspect narrative_log read-only em archived/não, delete cross-user, seed admin create/skip/idempotent, `_ensure_user_role` idempotente); 6 testes existentes em `test_routes_game.py` migrados de `auth_token` → `admin_token` (criação de sessão agora exige admin).
- ✅ Fase 30 — Painel admin: gestão de usuários, limites de uso, custo e atividade. **Captura real de usage**: novo `llm/usage.py` com `UsageReport` (prompt/completion/total/provider/model/source `"api"|"fallback"`) + helper `chat_with_usage` (prefere método nativo do provider, senão fallback chars//3 marcado `source="fallback"`); `openai_compatible.py` ganha `chat_with_usage` (lê `response.usage`) e `chat()` vira wrapper — Protocol base **intacto** (CLI não quebra); `DMResponse`/`CompanionDecision` ganham campo `usage`, `NarrativeResult.usages` acumula 1-2 chamadas DM (companion turn fora do path web). **Modelo de dados**: `User` ganha `daily_token_limit`/`daily_minutes_limit` (NULL→default global), `unlimited`, `active`, `disabled_reason`; novas tabelas `usage_events` (id/user_id CASCADE/session_id/endpoint/kind/provider/model/source/prompt/completion/total_tokens/cost_usd NUMERIC(12,8)/created_at) e `activity_log` (id/user_id CASCADE/event_type/meta JSON/created_at) + enums `UsageKind`/`ActivityType`; `config.py` defaults `default_daily_token_limit=200_000`, `default_daily_minutes_limit=120`, `token_price_per_1k_input_usd=0.001`, `token_price_per_1k_output_usd=0.002`. **Helpers**: `web/usage.py` (dialect-aware: `compute_cost`, `usage_today`, `minutes_today` via `date_trunc`/`strftime`, `cost_this_month`, `usage_by_day`, `persist_usage_events`), `web/limits.py::check_quota` (None se ok; isento se `unlimited` ou role admin; 429-detail `{detail,used,limit,unit,reset_at}` com reset à meia-noite UTC), `web/activity.py::log_activity` (best-effort). **Migrações idempotentes** `server.py::_ensure_user_limits` (5 colunas em `users`, defaults dialect-aware) + `_ensure_usage_tables` (`CREATE TABLE IF NOT EXISTS` JSONB/TEXT) rodam no `lifespan` após `_ensure_user_role`. **Endpoints admin** (`routes_admin.py`, todos `require_admin`): `GET/POST /api/admin/users`, `GET/PATCH/DELETE /api/admin/users/{id}`, `POST .../reset-password`, `GET .../activity`, `GET .../usage?days=`, `GET /api/admin/usage/summary` (custo/tokens do mês, top 5, contagem ativos/desativados); proteções: não desativar/demover/excluir a si nem o **último admin ativo** (409). **Auth hooks**: `current_user` barra `active=False` (403 "Conta desativada" — mata sessões zumbis); `login` barragem genérica 401 p/ inativo (anti-enumeration) + `ActivityLog(login)`; `signup` loga `signup`. **Enforcement**: `routes_game.py::session_input` chama `check_quota` **antes** do LLM (429 + `ActivityLog(limit_blocked)`) e persiste `UsageEvent` depois (1 por chamada DM). **Frontend**: nova `#admin-panel-screen` (`index.html`) com dashboard (cards custo/tokens/ativos), tabela de usuários (status tags ativo/desativado/ilimitado, tokens hoje/limite, custo mês), modais Criar/Editar/Resetar senha, drawer Atividade+Uso; botão "Painel admin" no lobby (`isAdmin()`); `api()` anexa `err.status` p/ distinguir 429 → mensagem pt-BR "Limite diário atingido..." em `#output`; cache bump v33; CSS `.admin-table`/`.admin-card`/`.modal`/`.tag`. **1640 testes passando** (1608 anteriores + 8 `test_provider_usage` + 16 `test_admin_users` + 8 `test_usage_limits` cobrindo captura API/fallback, propagação agent→narrative, `usage_today`/`check_quota` unit, 429 no `/input`, persistência `usage_events`, admin isento, override `unlimited`, CRUD users 201/409/404, proteções self/último-admin, reset-password + login, soft-disable 401 genérico + `current_user` 403, activity log login).
- 🐛 Fix: narrative_log ao carregar save (frontend) — `loadSaveAsSession` em `app.js` recebia `res.state` (que contém `narrative_log` populado pelo `model_dump_json` do Pydantic) mas chamava `enterGame()` sem repassar; `enterGame` só renderizava o log no branch `readOnly` (admin). Resultado: jogador só via "Sessão iniciada" e perdia o histórico. Fix: `loadSaveAsSession` agora passa `narrativeLog: (res.state && res.state.narrative_log) || []`; `enterGame` adiciona `renderNarrativeLog(narrativeLog)` no caminho não-readOnly (após a linha de sistema, gateado por `narrativeLog.length` pra não duplicar abertura em jogos novos). Index `?v=39` → `?v=40` pra invalidar cache. Sem mudança server-side — persistência/restore via `state.model_dump_json`/`model_validate_json` já cobriam `narrative_log` corretamente (admin read-only sempre funcionava); a inconsistência era só na renderização do load normal vs. `viewSaveReadOnly`.

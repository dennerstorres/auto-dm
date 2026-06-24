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
├── phb/          # loader do PHB (parser, models, lookup)
├── character/    # CharacterBuilder + spell selection
├── agents/       # DM agent + companion agents
├── companions/   # pre-defined companion roster
├── persistence/  # save/load JSON
└── cli/          # interface de linha de comando
```

## Comandos úteis

```bash
pip install -e ".[dev]"   # instalar em modo dev
pytest                     # rodar testes
ruff check .               # lint
auto-dm                    # rodar o jogo
auto-dm --help             # opções CLI
```

## Regras para Claude Code

- **Nunca ler `.env`** — contém API keys. Usar `.env.example` como referência de template.
- **`data/phb/` é leitura livre** — esses `.md` são a fonte de regras. Conteúdo derivado do D&D 5e **SRD v5.1** (Open Game License + CC BY 4.0) — não é o PHB completo. Arquivos com prefixo `#` (ex: `# Racial Traits.md`) são índices introdutórios; sem prefixo são conteúdo.
- **Provider ativo é Minimax** — não implementar adapters novos (Claude/Gemini/OpenAI/GLM) a menos que o usuário peça explicitamente. Foco no Minimax primeiro.
- **D&D 5e, PHB only** no MVP. Níveis 1-5. Sem multiclasse, sem feats, sem classes/raças/magias fora do PHB.
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

**994 testes passando** (4 pré-existentes em test_character_flow.py — stale scripted-input indices, não relacionados às mudanças).

# Auto DM — Plano de implementação

> Caminho pra chegar no MVP. Cada fase é um bloco entregável e testável.

> 📦 **Decisões definitivas de produto:** o CLI foi arquivado na Fase 34 e
> **não voltará**; o Auto DM é um produto 100% web. O streaming SSE da Fase
> 26b também foi arquivado e não faz parte do roadmap: as respostas do jogo
> continuam chegando completas por REST. A Fase 10 original foi arquivada e
> substituída pela Fase 51, que trata multi-provider, chaves por usuário e o
> modelo SaaS como uma única arquitetura.
>
> As fases antigas abaixo (0, 5, 6, 11, etc.) descrevem um CLI
> que **não existe mais** no código: `src/auto_dm/cli/` e `main.py` foram
> deletados, junto das deps `click`/`rich`. Leia essas menções como
> registro do que foi feito na época, não como reflexo do estado atual.
> A camada de apresentação atual é o frontend web (`src/auto_dm/web/`).

---

## Princípios de execução

- **Vertical slices primeiro:** cada fase entrega algo que dá pra rodar, mesmo que mínimo
- **Motor de regras antes da IA:** tudo que envolve mecânica tem que funcionar sem LLM nenhum, pra depois plugar a IA em cima
- **Testes no engine:** regras têm que ser precisas, então cobertura alta nas funções de mecânica
- **Provider genérico desde o início:** não deixar nenhum detalhe específico de um provider vazar pra fora do adapter

---

## Fase 0 — Setup (meio dia)

**Objetivo:** projeto rodando, dependências instaladas, smoke test do CLI

**Entregáveis:**
- Estrutura de pastas
- `pyproject.toml` com Poetry ou `uv`
- `.env.example` com placeholders pras 5 chaves
- `config.example.json`
- `main.py` que imprime "Auto DM" e sai
- `.gitignore`
- `README.md` mínimo

**Pastas:**
```
auto_dm/
├── SPEC.md
├── PLAN.md
├── README.md
├── pyproject.toml
├── .env.example
├── config.example.json
├── .gitignore
├── data/phb/                  # onde ficam os .md do PHB
├── saves/                     # saves JSON
├── src/auto_dm/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── llm/
│   ├── engine/
│   ├── state/
│   ├── agents/
│   ├── persistence/
│   ├── web/                   # frontend + backend (substituiu o cli/)
│   └── rules_data/            # extraído dos .md, cache
└── tests/
```

---

## Fase 1 — Abstração de LLM (1-2 dias)

**Objetivo:** trocar de provider com 1 linha de config

**Entregáveis:**
- `LLMProvider` (Protocol) com `chat`, `stream`, `count_tokens`
- Adapters funcionais pra **Claude** e **Gemini** (os dois mais fáceis)
- `factory.py` que escolhe o provider pela config
- `config.py` carrega `.env` + `config.json`
- Teste smoke: enviar "olá", receber resposta, validar formato

**Decisões a tomar:**
- Streaming: começamos só com `chat` (não-stream) ou já implemento streaming no CLI? Começar sem stream é mais simples.
- Tool calling / structured output: usar pra forçar a IA a retornar JSON de Action? **Sim, é crucial pra validação no engine.**

---

## Fase 2 — Modelos de estado (1-2 dias)

**Objetivo:** representação completa do estado de jogo, validada

**Entregáveis:**
- Pydantic models: `Character`, `AbilityScores`, `Proficiencies`, `EquippedSlots`, `Item`, `Spellcasting`, `Condition`
- `GameState`, `NPC`, `Quest`, `NarrativeEntry`
- `Action`, `ActionResult`, `ActionType` (enum)
- `StateManager` com métodos de mutação (sem lógica de regra, só transições válidas — ex: "HP não pode ficar negativo")
- Serialização roundtrip (model → JSON → model) sem perda
- Testes de validação

---

## Fase 3 — Motor de regras: núcleo (3-4 dias)

**Objetivo:** coração mecânico funcionando, testado, sem LLM

**Entregáveis:**
- `dice.py`: notação `XdY+Z`, vantagem/desvantagem, keep highest/lowest, exploding dice
- `combat.py`:
  - `attack_roll(attacker, target, weapon) → AttackResult`
  - `damage_roll(attacker, weapon, crit) → DamageResult`
  - `apply_damage(target, amount) → new_hp`
  - `initiative(participants) → ordered list`
  - `saving_throw(creature, ability, dc) → SaveResult`
  - `death_save(creature) → SaveResult`
- `conditions.py`: 13 conditions com hooks (início de turno, fim de turno, ao sofrer dano, etc)
- `actions.py`: parser e validador de Action → executa via combat.py
- `spell_slots.py`: gastar slot, restaurar em descanso
- Testes extensivos (pytest): cada função com casos normais + edge cases (HP=0, crit duplo, etc)

**Referência:** PHB cap. 9 (combat), cap. 10 (spellcasting), apêndice A (conditions)

---

## Fase 4 — Carregamento do PHB (1-2 dias)

**Objetivo:** dados estruturados do PHB disponíveis pro engine e pros prompts

**Entregáveis:**
- Script `load_phb.py` que lê os `.md` de `data/phb/` e extrai:
  - Lista de raças com traits
  - Lista de classes com features por nível
  - Lista de magias com escola, nível, casting time, range, components, duration, descrição
  - Lista de equipamentos (armas, armaduras, itens)
  - Lista de backgrounds
  - Lista de conditions
- Saída: arquivos Python pickleados/JSON em `src/auto_dm/rules_data/`
- Funções de lookup: `get_spell("fireball")`, `get_class_features("fighter", 3)`, etc
- Testes: cada lookup retorna dados válidos

**Nota:** manter isso **separado** do código de regra. Os `.md` são fonte de verdade; o engine consulta os dados extraídos.

---

## Fase 5 — Criação de personagem (1-2 dias)

**Objetivo:** jogador cria seu personagem pelo CLI, com validação

**Entregáveis:**
- Wizard de criação: raça → classe → background → atributos (4d6kh3 ou standard array) → equipamento
- Auto-cálculo de HP, AC, proficiências, spell save DC, attack bonus
- Validação em cada etapa
- Persistência do personagem (em memória, depois vai pro save)
- Companheiros pré-definidos carregados de `data/companions.json`

---

## Fase 6 — DM Agent e loop narrativo (2-3 dias)

**Objetivo:** conversa fluida DM-jogador, fora de combate

**Entregáveis:**
- `dm.py` com system prompt do DM (personalidade configurável)
- `prompts.py` com templates de prompt pra cada papel
- Loop CLI:
  ```
  ┌─────────────────────────────────────┐
  │  [Estado: Vila de Phandalin, dia]   │
  └─────────────────────────────────────┘
  DM: Você está na taverna. O taberneiro...
  > O que faço?
  
  > Eu peço uma caneca de cerveja e procuro ouvir conversas
  
  DM: O taberneiro te serve. No canto, três mercadores discutem...
  ```
- Memory window: últimas N mensagens + resumo das anteriores
- Injeção de estado relevante no prompt (HP, condições, local, hora)

---

## Fase 7 — Sistema de combate (4-5 dias)

**Objetivo:** combate funcional com iniciativa, turnos, magias

**Entregáveis:**
- Detecção automática de início de combate (DM ou jogador declara)
- `combat.py` estendido com:
  - Turn manager (round, turno de quem, ações restantes)
  - Action parser: input do jogador → `Action` estruturado
  - Validação de cada Action contra state
  - Execução e log estruturado
- Render do CLI em modo combate: iniciativa, HP bars, conditions, action menu
- Magias: subset de cantrips + nível 1 + nível 2 do PHB
- Concentration tracking
- Death saves

**Subdivisão:**
- 7a: iniciativa + ataque + dano (1-2 dias)
- 7b: magias + slots (2 dias)
- 7c: conditions + edge cases (1 dia)

---

## Fase 8 — Companheiros IA (2-3 dias)

**Objetivo:** companheiros agem autonomamente, com personalidade

**Entregáveis:**
- `companion.py`: um agent por companheiro
- System prompts personalizados por companheiro (3-5 com vozes distintas)
- Estrutura de retorno do LLM: `{action, dialogue, reasoning}` (forçar via JSON mode)
- Loop integrado no turn manager: turno do jogador → turno de cada companheiro → turno dos inimigos
- Narração do DM com base no resultado de cada ação

---

## Fase 9 — Persistência (1 dia)

**Objetivo:** salvar e carregar campanha

**Entregáveis:**
- `save.py` com `save_state(state, path)` e `load_state(path)`
- Auto-save a cada N turnos (configurável)
- Comando CLI pra save manual
- Listagem de saves na inicialização
- Resumo de campanha ao carregar (pra reapresentar contexto)

---

## Fase 10 — Providers restantes (ARQUIVADA)

> **Arquivada definitivamente.** O escopo original era apenas adicionar adapters
> globais e não contemplava isolamento de credenciais, configuração por usuário,
> BYOK, assinatura ou controle de custo. Não deve ser implementada como escrita.
> O trabalho futuro está especificado na **Fase 51**.

**Objetivo:** os 5 providers funcionando

**Entregáveis:**
- Adapter OpenAI (com tool calling / JSON mode)
- Adapter GLM (verificar API docs — qual auth, qual endpoint)
- Adapter Minimax (verificar API)
- Teste smoke de cada um
- Documentação de como configurar cada um no README

---

## Fase 11 — Polish (contínuo, mas a primeira passada = 2-3 dias)

**Entregáveis:**
- CLI mais bonito: HP bars, cores, painéis bem organizados com `rich`
- Mais magias carregadas
- Mais classes
- Comandos de CLI úteis: `status`, `inventory`, `help`, `save`, `quit`
- Mensagens de erro amigáveis
- Logging pra debug

---

## Backlog pós-MVP

> Nota: muita coisa deste backlog acabou entregue pós-MVP (níveis 1–20,
> subclasses, magias até 9º círculo, **Web UI com FastAPI + Docker**).
> Mantido aqui como registro histórico do planejamento original.

- [ ] Mapa visual com tokens (TUI com `textual`?)
- [ ] Geração dinâmica de NPCs com stat block
- [x] Magias de nível 3-9 (entregue nas Fases 18/25g)
- [x] Níveis 6-20 (entregue nas Fases 25f/25g)
- [ ] Multiclasse
- [ ] Feats
- [ ] Voz (TTS dos NPCs)
- [x] Companion creation wizard + party roll (entregue na Fase 27)
- [ ] Campanhas pré-escritas
- [ ] Tabela de tesouros / loja
- [ ] Tracking de tempo (turnos, dias, calendário)
- [x] Web UI (FastAPI + frontend leve) — entregue nas Fases 26a–26e, roda em **Docker** (ver `DEPLOY.md`)

---

## Estimativa total

| Fase | Tempo |
|---|---|
| 0 | 0.5 dia |
| 1 | 1-2 dias |
| 2 | 1-2 dias |
| 3 | 3-4 dias |
| 4 | 1-2 dias |
| 5 | 1-2 dias |
| 6 | 2-3 dias |
| 7 | 4-5 dias |
| 8 | 2-3 dias |
| 9 | 1 dia |
| 10 | Arquivada; substituída pela Fase 51 |
| 11 | 2-3 dias |
| **Total** | **~3-5 semanas** |

Variável de acordo com profundidade. O motor de regras (Fase 3) e o combate (Fase 7) são as fases mais longas e as que mais vão exigir decisão.

---

## Onde começar

Próximo passo concreto: **Fase 0 + Fase 1 juntas**, pra ter um esqueleto de CLI conversando com um LLM de verdade. A partir daí, Fases 2 e 3 em paralelo (modelos de estado + motor de regras) porque são fundação pra tudo depois.

---

# Fase 39–43: Segunda onda pós-Fase 33 (renumerada)

> Fases de polimento web + gameplay richness, todas projetadas após o
> painel admin / quota (Fase 30) e a memória de longo prazo (Fase 33).
> Estimativa é `~2,5-3 semanas` para um dev solo; ordem escolhida pelo
> usuário. Cada fase tem entregável testável e roda isolada (cuidado
> com acoplamentos cruzados).
>
> **Renumeração (2026-07):** esta onda foi planejada como Fases 34–39,
> mas os números 34–38 acabaram usados por outras entregas (34 remoção
> do CLI, 35 sugestão de nomes com IA, 36 fichas dos companheiros, 37
> spells+inventário nas fichas, 38 XP/progressão/ASI — ver CLAUDE.md).
> A antiga "Fase 34 — Painel de personagem em tempo real" foi
> **removida por decisão do usuário**: as fichas das Fases 36/37
> (reais) já cobrem a parte visual, e como o jogo é turn-based e
> re-renderiza as fichas a cada `/input`, o live-polling (ETag /
> `X-State-Rev` / poll inteligente) deixou de valer o custo.

---

## Fase 39 — Inventário & equipamento na web (4-5 dias)

**Objetivo:** fluxo completo de loot → equipar → vender / comprar,
pelo browser.

### Fase 39a — Engine de inventário (1-2 dias)

**Entregáveis:**
- `engine/inventory.py` com:
  - `swap_equipped(char_id, slot, item_id)` →
    `InventoryResult{ac_delta, attuned_max_warning, errors}`.
  - `add_item(state, char_id, item, quantity=1)` /
    `remove_item(state, char_id, item_id, quantity)` — pure
    functions; LLM nunca chama direto (somente via narrador).
  - `attune(char_id, item_id)` / `unattune(char_id, item_id)`
    respeitando limite de 3 (PHB p. 138).
- `Item.quantity: int = 1` em `state/models.py` (default back-compat).
- `Character.gold_gp: int = 0` + `Character.attuned_items: list[str]`
  (novo).
- `NPC.vendor: bool = False` + `NPC.shop_inventory: list[ShopItem]`
  (com `{item_id, price_gp, restock_daily: bool}`).
- Magic items: novo loader `data/phb/Treasure/*` já existente ganha
  detecção de curse marker `*` em tools / items.

**Testes:** 24 cobrindo swap sem proficiência, magic armor +shield
AC recompute, attunement overflow (tenta 4º falha), stack de poção
(quantidade 4 → use 1), gold_gp roundtrip, vendor flag.

### Fase 39b — Endpoints REST (1 dia)

**Entregáveis:**
- `web/routes_inventory.py` (rotas conforme §12.2 do SPEC).
- Auth padrão `Depends(current_user)` + ownership check.
- 402 com `detail="gold_gp insufficient"` quando saldo < price.
- Sem migração de DB: `gold_gp`/`quantity`/`vendor` vivem no state
  JSON (Pydantic) — back-compat via defaults, como nas fases 31-38.

**Testes:** 28 cobrindo cada endpoint (200 happy + 401/403/422/402),
loja com/sem ouro, attunement limit, vendor não flagrado → 422.

### Fase 39c — Frontend de inventário + loja (2 dias)

**Entregáveis:**
- Aba `Inventário` nas fichas existentes (`.sheet-view`, Fases 36/37
  reais) — toggle Ficha / Inventário por personagem.
- Grid de slots com drag-drop ou modal de seleção.
- Lista filtrável por categoria; pill `Magia (R)`/`Mágico`/`Curses`.
- Modal de inspeção com markdown render de description (mesma usada
  no PHB; **não baixa** itens pagos por copyright) e botões.
- Overlay de loja full-screen com catálogo, saldo, affordance button.
- Cache bump em `index.html`/`app.js` (próximo `?v=`).

**Testes:** 0 automatizados (validar manual).

### Critério "pronto"

- Wizard cria Sorcerer → equipar `staff` no main-hand: AC e
  weapon_attack recalculam automaticamente.
- Achou `+1 longsword` em loot narrado (via flag LLM no DM context
  Bull 39c-opt) → loot aparece em inventário → equipar mostra
  `+1` no attack modifier no painel.
- Comprar `Potion of Healing` gasta 50 gp; vender reembolsa 25 gp
  (50 % PHB default).

---

## Fase 40 — Encontros aleatórios + tesouros (3-4 dias)

**Objetivo:** viagens narradas pelo DM rolam encontros usando
tabelas curadas; tesouros caem automaticamente respeitando tier do
grupo.

### Fase 40a — Tabelas & infrastructure (1 dia)

**Entregáveis:**
- 6 JSONs em `data/world_tables/encounters/{forest,road}_
  {day,night}.json` + 2 dungeon levels.
- 4 JSONs em `data/world_tables/loot/{individual,hoard_low,hoard_mid,
  hoard_high}.json`.
- `world_tables/weather.json`.
- Loader `phb/loader.py::load_world_tables()` com cache.
- `phb/models.py::EncounterTable` / `LootTable` / `WeatherTable`
  Pydantic.

**Testes:** 8 (JSON válido, todos os ids batem com monsters/items,
CR range coerente com `cr_for_level`).

### Fase 40b — Engine `engine/world.py` (1-2 dias)

**Entregáveis:**
- `roll_encounter(state, table_id, seed=None) -> EncounterResult`
- `resolve_travel(state, hours, *, rng_seed=None) -> WorldEventList`
- `compute_loot(tier, roll) -> LootDrop` — gera ouro + items.
- Cooldown enforcement (`world_event_cooldown_minutes`).
- Whitelist de tags processáveis do LLM: `MEC`, `LOOT`, `WEATHER`.

**Testes:** 18 (determinismo com seed fixa, cooldown enforced,
spawn correto no npcs[], loot integration com `inventory.py`, weather
atualização).

### Fase 40c — DM Agent integration (1 dia)

**Entregáveis:**
- Bullet no `DM_SYSTEM_PROMPT` `## Encontros aleatórios`.
- `agents/heuristics.py::infer_intent` detecta `Intent.TRAVEL` por
  regex.
- `narrative.py::process_player_action` chama
  `resolve_travel(state, hours)` quando intent é `TRAVEL`.
- Engine tag parsing em `post_dm_response` strips `MEC/LOOT/WEATHER`
  do texto antes de mostrar, mas aplica os efeitos.
- Seed visível: `narrative_log` ganha campo opcional
  `world_seed: str` em entradas de viagem.

**Testes:** 12 (intent detection regex, 5 strings de viagem /
não-viagem, seed persistida, tag MEC consumida, cooldown).

### Critério "pronto"

- "Viajem dois dias pela estrada" → em 1-2 dias de viagem, um
  encontro rola (lobo/bandidos), encerra inserindo `npc["wolf_x"]`
  com init e a narração ganha flavor coerente.

---

## Fase 41 — Reações além de Opportunity Attack (4-5 dias)

**Objetivo:** o engine aceita triggers reativos (Shield, Counterspell,
Hellish Rebuke, Healing Word, Uncanny Dodge, Parry) e dá canal ao
jogador para escolher.

### Fase 41a — Modelo de reações (1 dia)

**Entregáveis:**
- `engine/actions.py::ReactionKind` enum.
- `engine/actions.py::TriggerEvent` dataclasses:
  - `OnHitByAttack(target, attacker, attack_damage, is_melee)`
  - `OnSeeingSpellCast(caster, spell_name, level)`
  - `OnAllyDown(ally_id)`
  - `OnDamageTaken(amount, type)`
- `state/models.py::Character.pending_reaction: Optional[dict]`
  (epoch + json de trigger + reactions_eligible).

**Testes:** 12 (enum kinds, triggers dados, pending_reaction TTL ≤30s).

### Fase 41b — Engine dispatch (2 dias)

**Entregáveis:**
- `combat_engine.py::_dispatch_reactions(trigger, ...)` invocado
  por `_handle_attack`, `_handle_cast_spell`, etc.
- Cada `ReactionKind` resolve para `ReactionResolution`.
- `cast_spell(engine_branch=REACTION)` em `engine/spellcasting.py`.
- Validações Spell Known/Prepared + slot adequado.
- Counterspell auto-check (não gasta slot em spell immune ou sem
  vocalização): só o LLM pode decidir se concede trigger.
- `_apply_damage_with_reactions(target, raw_damage, trigger)` —
  suspende o apply se há reação elegível e ativa `pending_reaction`.

**Testes:** 28 (cada ReactionKind, Uncanny Dodge halve, Shield +5
AC aplicado, Counterspell success/fail com DC, Hellish Rebuke
despawn de slot, Parry caps at L7, reaction_used reset no round
start).

### Fase 41c — UX de reação na tela de jogo (1-2 dias)

**Entregáveis:**
- Web: tela de jogo ganha modal de reação com lista de opções + timer
  30s + auto-pass se timeout ou setting `auto-pass_shield`.
- Sem live-polling: o trigger de reação chega na resposta do
  `POST /input` (fluxo síncrono) — o front abre o modal antes de
  renderizar a narração e responde via endpoint de reação dedicado.
- LLM: instruction no DM_SYSTEM_PROMPT pedir confirmação ao
  jogador antes de narrar efeito de trigger (ex.: "Você ouve o
  componente verbal de Fireball, quer reagir?").
- Companions: `companion_reaction_aggression` heurística default
  (HP < 50% → auto Shield).

**Testes:** 16 (trigger marca pending_reaction, modal mostra
opções, TTL timeout, companion decisão por heurística, settings).

### Critério "pronto"

- Wizard contra um mage inimigo que lança Fireball L3:
  1. DM narra "Você vê arcos de fogo se formando nas mãos do inimigo".
  2. Front envia trigger para PC.
  3. Painel mostra modal: `Counterspell (L3, 1 slot)` [Cancel].
  4. Player clica → Counterspell consome slot → narração marca
     "sua magia estanca as chamas incipientes".
  5. State persistido tem slot consumido.

---

## Fase 42 — TTS via edge-tts + música ambiente (3-4 dias)

**Objetivo:** narração em voz pt-BR opcional, sem custo direto,
cacheado no servidor. Músicas via URL configurável.

### Fase 42a — TTS backend isolado (1-2 dias)

**Entregáveis:**
- `pyproject.toml` extras: `[audio] deps = ["edge-tts>=6.1,<7"]`.
- `web/tts.py` isolado em deps leve (importa `edge_tts` apenas
  dentro de funções).
- `GET /api/tts/voices` retorna 30+ vozes.
- `GET /api/sessions/{id}/tts?text=&voice=&rate=` → mp3 bytes.
- Cache LRU em `/tmp/tts_cache/<sha1>.mp3`, TTL 30 dias.
- Painel graceful: 503 quando `api.msedgespeech.microsoft.com`
  fora do ar.
- Sem None tipos, failsafe fallback.

**Testes:** 10 (voices lista, mp3 retornado com content-type, cache
hit retorna from-disk, falhas de network retornam 503).

### Fase 42b — Frontend audio (1 dia)

**Entregáveis:**
- `audio.js` com `AudioContext` lazy init, `CacheStorage` por chave
  `(text+voice+rate)`.
- Botão 🔊 no chat replay última DM message.
- Auto-play configurável via `preferences.tts.auto` (`off` default).
- Mobile: toca só após 1ª interação (iOS Safari).
- Disable on `!navigator.onLine`.

**Testes:** 0 (manual).

### Fase 42c — Música ambiente (1 dia)

**Entregáveis:**
- `users.preferences.music = {"enabled": false, "src": "", "volume": 0.4}`
  (JSONB) + migração idempotente `_ensure_user_preferences` em
  `server.py` (herdada da antiga fase do painel — estreia aqui).
- Endpoint `GET /api/me` retorna bloco `music`.
- UI: botão 🎵 + slider volume + URL config field em Settings.
- `<audio loop crossorigin>` controlado por JS.
- README com links a playlists CC-BY recomendadas (não embarcadas).

**Testes:** 6 (preferences persistência, endpoint retorna shape,
default disabled, music.active boolean computado).

### Critério "pronto"

- Login → painel → config TTS voz=FranciscaNeural, auto=off →
  abre Settings → digita input → após DM narração, botão 🔊 toca
  áudio em ~1 s (cache hit) ou ~3-5 s (cold, mp3 download).
- Músicas: copia URL do Incompetech forest ambience → 🎵 toca
  looping, sem scrollar a página.

---

## Fase 43 — End-to-end do fluxo completo (2-3 dias)

**Objetivo:** CI garante que o caminho mínimo de jogo funciona ponta-
a-ponta. Bug regressão em wizard → save → load → painel nunca
passa despercebido.

### Fase 43a — Stack de teste E2E (1 dia)

**Entregáveis:**
- `tests/e2e/conftest.py`: sobe `app` em port efêmera, conecta PG
  + Redis dev, expõe `httpx.AsyncClient` com `Authorization: Bearer`
  helper.
- `tests/e2e/fake_dm.py` patcha `LLMProvider.chat` para retornar
  resposta determinística (TAG-driven).
- `tests/e2e/helpers.py`: `signup_login`, `play_turn`, `assert_state_rev`.

**Testes:** 0 (infra).

### Fase 43b — Cenários canônicos (1-2 dias)

**Entregáveis (4 testes):**
1. `test_solo_wizard_save_load` (cobre Wizard L3 + 3 companions + 3
   turnos do jogador + 2 companheiros + save + load).
2. `test_sheets_reflect_hp_after_attack` (usa `GET /api/sessions/{id}`
   + `/companions` — fichas das Fases 36/37 reais).
3. `test_shop_buy_insufficient_gold_402` (Fase 39 dep).
4. `test_travel_3_days_rolls_encounter_and_loot` (Fase 40 dep).

Cada teste roda ≤30 s; total E2E ≤2 min. `make e2e` (Makefile) →
verde.

**Testes:** 4 (skip quando dep de fase ausente).

### Fase 43c — CI + report (1 dia)

**Entregáveis:**
- `Makefile` com target `e2e` e `all` (`unit + e2e`).
- README atualiza seção "Desenvolvimento" listando E2E.
- GitHub Action opcional (template, não obrigatório).

**Testes:** 4 (verificação dos próprios cenários, sem network).

### Critério "pronto"

- `make all` em CI passa em <3 min.
- Bug injetado manualmente em `routes_game.py` (ex.: quebra no shape
  do `GET /companions`) é capturado por `test_solo_wizard_save_load`.

---

## Resumo segunda onda (39–43)

| Fase | Escopo | Dias | Dependências | Notas |
|---|---|---|---|---|
| 39 | Inventário + loja | 4-5 | nenhuma | engine é independente |
| 40 | Encontros/tesouros | 3-4 | nenhuma | DM integration toca narrative.py |
| 41 | Reações estendidas | 4-5 | fichas (36/37 reais) | engine funciona sem UI |
| 42 | TTS + música | 3-4 | nenhuma | estreia `users.preferences` |
| 43 | E2E | 2-3 | 39, 40 | gate final do release |

**Riscos:**
- Fase 42 estreia `users.preferences` JSONB; a migração
  (`_ensure_user_preferences`) precisa ser idempotente como as demais.
- Fase 40 LLM tag parsing pode gerar confusion se o LLM alucinar
  tags inválidas; engine valida por regex estrita + fallback pra
  encontro default.
- Fase 42: edge-tts é GPL-3.0. Se nosso projeto entrar em
  distribuição mais ampla, separar `web/tts.py` num sidecar
  (subrepositório) ou trocar pelo `gTTS` (BSD-style).

**Saída global estimada:** ~2,5-3 semanas. Inventário + loja sozinhos
entregam a maior parte da melhoria de UX percebida.

---

# Terceira onda — Modernização da experiência web (44–50)

> Plano criado após a nova landing page. O objetivo é levar a mesma
> linguagem visual para toda a aplicação sem interromper o jogo, alterar
> contratos da API ou fazer uma reescrita total do frontend.

O documento [`DESIGN.md`](DESIGN.md) é a fonte de verdade para decisões
visuais, componentes, responsividade, acessibilidade e tom de interface.
Em caso de divergência entre uma implementação antiga e o design system,
a migração deve seguir este plano, tela por tela.

## Objetivos

- Unificar landing, autenticação, lobby, wizard, jogo e administração.
- Manter a ambientação de fantasia sem prejudicar leitura ou velocidade.
- Reduzir o acoplamento do `index.html`, `style.css` e `app.js` monolíticos.
- Preservar os IDs, endpoints e fluxos já cobertos por testes durante a migração.
- Criar uma base responsiva e acessível que suporte novas features.
- Adicionar validação visual automatizada para evitar regressões de layout.

## Fora de escopo

- Trocar FastAPI, Postgres, Redis ou os contratos REST existentes.
- Migrar imediatamente para React, Vue ou outro framework.
- Redesenhar regras, balanceamento ou conteúdo mecânico de 5e.
- Alterar todos os fluxos em um único pull request.
- Usar assets, fontes ou bibliotecas que dependam de CDN em runtime.

## Estratégia de migração

1. Migrar por tela, começando pelos fluxos de maior frequência.
2. Manter o frontend funcional ao final de cada fase.
3. Extrair tokens e componentes somente quando houver uso real.
4. Não mudar contrato visual e contrato de API na mesma entrega.
5. Preservar seletores usados pelo JavaScript até que seus módulos sejam migrados.
6. Remover CSS antigo apenas quando a tela correspondente estiver coberta por teste visual.

## Fase 44 — Fundação do design system (2-3 dias)

**Objetivo:** transformar o design da landing em uma base reutilizável.

**Entregáveis:**

- Criar `static/css/` com `tokens.css`, `base.css`, `components.css` e
  `utilities.css`.
- Registrar cores, tipografia, espaçamento, raios, sombras, camadas e breakpoints
  conforme `DESIGN.md`.
- Extrair estilos da landing sem alterar sua aparência aprovada.
- Criar componentes base para botão, campo, segmented control, modal, status,
  tooltip, tabs, empty state e loading state.
- Adotar ícones Lucide armazenados localmente ou empacotados no projeto.
- Eliminar novos estilos inline; os existentes entram numa fila de migração.
- Criar uma página interna de referência de componentes, disponível apenas em
  desenvolvimento.

**Critério pronto:** landing e autenticação mantêm o mesmo resultado visual em
desktop e mobile usando os novos arquivos e tokens.

## Fase 45 — Shell, navegação e feedback global (2 dias)

**Objetivo:** criar uma estrutura comum para todas as áreas autenticadas.

**Entregáveis:**

- Header autenticado compacto com marca, usuário, navegação contextual e sair.
- Container responsivo com larguras previsíveis para lobby, wizard, jogo e admin.
- Padrões globais de loading, erro, confirmação, toast e estado offline.
- Foco visível, skip link, títulos de página e regiões ARIA.
- Modais com focus trap, fechamento por `Esc`, retorno de foco e scroll lock.
- Navegação mobile sem sobreposição ou controles fora da viewport.

**Critério pronto:** qualquer tela pode usar o mesmo shell sem duplicar markup ou
regras de layout.

## Fase 46 — Lobby e início de campanha (3-4 dias)

**Objetivo:** tornar o retorno ao jogo e a criação de campanha imediatos.

**Entregáveis:**

- Redesenhar saves como lista densa e escaneável, sem cards decorativos aninhados.
- Destacar “Continuar aventura” e manter ações secundárias discretas.
- Separar campanhas ativas e arquivadas com tabs ou filtro explícito.
- Exibir metadados úteis: personagem, nível, localização e última atualização.
- Criar empty state temático com CTA direto para o wizard.
- Integrar preferências e administração no shell, sem competir com o fluxo principal.
- Cobrir loading, lista vazia, erro, campanha arquivada e usuário admin.

**Critério pronto:** um jogador recorrente entra na campanha desejada em até dois
cliques depois do login.

## Fase 47 — Wizard de personagem (4-5 dias)

**Objetivo:** reduzir esforço e dar identidade ao processo de criação.

**Entregáveis:**

- Progresso legível com etapa atual, concluídas e pendentes.
- Seletores consistentes para raça, classe, background, alinhamento e nível.
- Resumo persistente do personagem em desktop e resumo recolhível no mobile.
- Estados selecionado, indisponível, recomendado, erro e carregando.
- Navegação fixa que não cobre conteúdo e mantém “Voltar”/“Próximo” previsíveis.
- Revisão final em formato de ficha, com edição direta por seção.
- Preservar integralmente validações e payload atual do wizard.

**Critério pronto:** criação completa funciona a 320 px, por teclado e sem mudança
de layout quando opções são carregadas.

## Fase 48 — Mesa de jogo (5-7 dias)

**Objetivo:** fazer a interface de jogo parecer uma mesa de campanha, mantendo
alta densidade de informação.

**Entregáveis:**

- Layout principal com narrativa como foco e ficha/ferramentas como apoio.
- Desktop com painel lateral estável; mobile com tabs ou drawers dedicados.
- Log narrativo com hierarquia clara entre Mestre, jogador, sistema e companheiros.
- Composer de ação fixo e acessível, sem reduzir excessivamente a área da narrativa.
- Fichas de personagem e companions com HP, CA, condições, recursos e ações rápidas.
- Controles de rolagem, inventário, loja, reações, áudio e comandos usando os mesmos
  padrões de interação.
- Estados de turno, Mestre pensando, quota, offline, somente leitura e sessão expirada.
- Evitar imagens decorativas na área que precisa de leitura contínua.

**Critério pronto:** nenhum fluxo principal exige scroll horizontal; narrativa,
ação e estado do personagem permanecem acessíveis em desktop e mobile.

## Fase 49 — Administração e preferências (3-4 dias)

**Objetivo:** tornar áreas operacionais silenciosas, densas e eficientes.

**Entregáveis:**

- Tabela responsiva com filtros, busca, ordenação e ações previsíveis.
- Resumos de uso sem cards excessivos ou visual de landing page.
- Ações destrutivas com confirmação explícita e diferenciação da cor da marca.
- Preferências organizadas por tabs ou seções: narração, música e conta.
- Inputs de volume, toggles e selects com labels e feedback persistente.
- Drawer de detalhes do usuário com histórico e consumo legíveis.

**Critério pronto:** administração funciona por teclado, em 360 px e com tabelas
que não vazam para fora da viewport.

## Fase 50 — Qualidade, performance e documentação (3 dias)

**Objetivo:** criar gates para que o frontend não volte a divergir.

**Entregáveis:**

- Playwright com cenários públicos e autenticados em 390x844, 768x1024 e 1440x900.
- Capturas de referência para landing, login, lobby, wizard, jogo e admin.
- Testes de fluxo para login, cadastro, continuar save e criar personagem.
- Auditoria com axe ou equivalente, sem violações críticas.
- Orçamento de performance para imagem hero, CSS e JavaScript inicial.
- Otimização dos assets para WebP/AVIF com fallback quando necessário.
- Checklist de revisão baseado em `DESIGN.md` no template de pull request.
- Atualização do README com estrutura do frontend e comandos de teste.

**Critério pronto:** testes funcionais, visuais e de acessibilidade rodam no CI e
impedem regressões críticas.

## Ordem e dependências

| Fase | Depende de | Pode ocorrer em paralelo |
|---|---|---|
| 44 — Fundação | Landing atual | Não |
| 45 — Shell | 44 | Não |
| 46 — Lobby | 45 | Preparação de fixtures da 47 |
| 47 — Wizard | 44 e 45 | 46 após componentes estabilizados |
| 48 — Jogo | 44 e 45 | 49 em módulos separados |
| 49 — Admin/preferências | 44 e 45 | 48 |
| 50 — Qualidade | Inicia na 44; fecha após 46–49 | Todas |

## Definition of Done por tela

- Usa tokens e componentes documentados, sem novos valores visuais arbitrários.
- Todos os estados esperados foram implementados: vazio, carregando, sucesso e erro.
- Funciona a 320 px, 390 px, tablet e desktop sem scroll horizontal.
- Texto não se sobrepõe, não é cortado e não redimensiona o layout ao carregar.
- Navegação completa por teclado e foco visível.
- Contraste mínimo WCAG AA para texto e controles.
- `prefers-reduced-motion` respeitado.
- Nenhum endpoint ou payload existente foi quebrado.
- Testes funcionais relevantes e captura visual aprovados.
- Chrome e Firefox verificados; Safari/iOS validado nos fluxos de áudio.

## Decisões encerradas nas Fases 44–50

- O frontend permanece em HTML/CSS e módulos ES nativos; não há migração para
  framework enquanto essa base continuar sustentável.
- A iconografia usa um subconjunto Lucide local e versionado no sprite do projeto,
  sem CDN em runtime.
- A mesa usa painel persistente no desktop e tabs/drawers no tablet/mobile.
- As 18 capturas de landing, login, lobby, wizard, jogo e admin nos três viewports
  ficam versionadas no repositório; relatórios do CI são retidos como artefato por
  14 dias.

## Métricas de sucesso

- Jogador recorrente abre uma campanha em até dois cliques após login.
- Criação de personagem não apresenta abandono causado por erro de navegação.
- Nenhuma regressão crítica de acessibilidade nas telas migradas.
- Nenhum overflow horizontal nos viewports suportados.
- Redução progressiva do CSS e JavaScript monolíticos após cada extração.
- Landing e aplicação autenticada são percebidas como o mesmo produto.

---

# Quarta onda — Plataforma multi-provider e SaaS

## Fase 51 — Multi-provider, BYOK e assinatura (12-18 dias)

> **Substitui integralmente a Fase 10 arquivada.** Esta fase será implementada
> depois do gate E2E real da Fase 43. Até lá, Minimax com configuração global
> `AUTO_DM_*` continua sendo o único caminho de produção suportado.

**Objetivo:** publicar o Auto DM com dois modos sustentáveis e isolados:

1. **Gratuito/BYOK:** o usuário fornece a própria chave e paga diretamente ao
   provider escolhido.
2. **Assinatura da plataforma:** o usuário paga uma mensalidade e usa as chaves
   globais do Auto DM dentro das cotas do plano.

O modo escolhido deve ser explícito. Uma falha de chave BYOK **nunca** pode cair
silenciosamente para a chave global, pois isso transfere custo para a plataforma.

### 51a — Registro de providers e adapters (3-4 dias)

**Providers iniciais:** Minimax, OpenAI, Anthropic Claude, Google Gemini e
DeepSeek. GLM deixa de fazer parte do escopo inicial e poderá entrar depois pelo
mesmo registro.

**Entregáveis:**

- `ProviderRegistry` central com identificador, modelos permitidos, endpoint
  fixo/permitido, recursos suportados e factory do adapter.
- Adapters com o mesmo contrato de chat, uso e erro para os cinco providers.
- Normalização de `UsageReport`, finish reason, limites, timeout, rate limit e
  erros de autenticação sem vazar payloads sensíveis.
- Catálogo de modelos controlado no servidor; o browser nunca envia endpoint
  arbitrário. DeepSeek pode reutilizar a base OpenAI-compatible sem duplicar o
  domínio de aplicação.
- Sem SSE: todos os adapters retornam respostas completas conforme a decisão
  definitiva da Fase 26b.
- Contract tests offline por adapter e smoke tests reais opcionais, habilitados
  apenas quando a chave correspondente existir no ambiente de CI seguro.

### 51b — Credenciais e preferências por usuário/BYOK (3-4 dias)

**Modelo de dados proposto:**

- `user_llm_settings`: `user_id`, `mode` (`byok|platform`), `provider`, `model`,
  parâmetros permitidos e timestamps.
- `user_provider_credentials`: `user_id`, `provider`, `ciphertext`, `key_version`,
  `masked_suffix`, `validation_status`, `validated_at`, timestamps e unicidade
  por `(user_id, provider)`.
- Credenciais em tabela separada das preferências e nunca incluídas em
  `UserOut`, logs, traces, analytics, saves ou respostas da API.

**Segurança obrigatória:**

- Criptografia autenticada em repouso com nonce por registro e chave mestra fora
  do banco; `key_version` permite rotação sem downtime.
- TLS em trânsito, resposta sempre mascarada, campos de formulário sem
  repopulação da chave e ação explícita para substituir/remover.
- Queries sempre restritas ao `user_id` autenticado; testes de isolamento entre
  tenants e proteção contra enumeração.
- Endpoints/base URLs definidos pelo servidor para impedir SSRF e exfiltração.
- Validação da chave por chamada mínima ao provider, com timeout e erro seguro;
  chave inválida/desabilitada bloqueia a chamada sem fallback global.
- Política de retenção e exclusão: remover a conta remove também todas as
  credenciais; rotação e remoção geram evento de auditoria sem registrar segredo.

**UX/API:**

- Preferências ganham área “IA e cobrança” para escolher BYOK ou plataforma,
  provider/model, cadastrar/testar/remover chave e visualizar seu estado.
- A conta gratuita pode jogar somente com uma credencial BYOK válida. Limites de
  infraestrutura e abuso continuam aplicáveis, mesmo sem custo de tokens global.
- A configuração global atual permanece como compatibilidade de migração para
  admin/desenvolvimento, mas não vira fallback implícito de usuários públicos.

### 51c — Planos, assinatura e entitlements (3-4 dias)

**Entregáveis:**

- Entidades `plans`, `subscriptions`, `billing_events` e/ou `entitlements`, sem
  acoplar o domínio ao SDK do processador de pagamento.
- Estado de assinatura normalizado: `trialing`, `active`, `past_due`, `canceled`
  e `expired`, com período vigente e cancelamento ao fim do ciclo.
- Checkout/portal do cliente e webhooks com assinatura verificada, idempotência,
  proteção contra replay e armazenamento do ID externo — nunca dados de cartão.
- O processador de pagamento será escolhido antes da implementação; Stripe é uma
  opção, não uma dependência arquitetural desta especificação.
- Plano mínimo configurável com cota mensal de tokens/custo, limite diário de
  proteção, modelos permitidos e concorrência. O entitlement, não o papel do
  usuário, autoriza o uso de chaves globais.
- Período vencido ou cota esgotada bloqueia novas chamadas antes do provider e
  oferece BYOK como alternativa; campanhas e saves permanecem acessíveis.

### 51d — Roteamento, medição e proteção de margem (2-3 dias)

**Ordem de resolução por chamada:**

1. Ler a configuração efetiva da conta.
2. Em `byok`, descriptografar somente em memória e instanciar o provider do
   usuário; erro encerra a chamada sem tocar em credencial global.
3. Em `platform`, validar assinatura, entitlement e cota antes de selecionar a
   credencial global.
4. O modo global legado só é aceito em ambiente privado/admin durante a migração.

**Entregáveis:**

- `ProviderContext`/resolver injetado em DM, companheiros, sumarizador, sugestão
  de nomes e qualquer outro ponto de uso de LLM; nenhum endpoint cria provider
  diretamente a partir do ambiente.
- Usage atribuído a `credential_source=byok|platform|legacy`, provider, modelo,
  usuário, sessão e tipo de chamada.
- BYOK registra tokens para diagnóstico, mas não debita a franquia paga. O modo
  plataforma usa preços configuráveis por modelo, reserva/validação pré-chamada
  e reconciliação pelo usage real retornado pelo provider.
- Hard caps, timeout, concorrência por usuário, circuit breaker e mensagens
  claras para chave inválida, provider indisponível, assinatura e quota.
- Painel admin separa consumo BYOK de custo global e mostra receita, custo,
  margem estimada, assinaturas e eventos de cobrança sem expor credenciais.

### 51e — Migração, testes e rollout (1-3 dias)

**Entregáveis:**

- Migrações idempotentes e rollback documentado; usuários atuais continuam no
  modo global legado até a publicação exigir escolha entre BYOK e assinatura.
- Feature flag para habilitar providers, BYOK e cobrança separadamente.
- Termos/privacidade explicam processamento por terceiros, retenção da chave,
  cobrança, cotas, exclusão e responsabilidade pelo saldo no provider BYOK.
- Métricas sem segredo: taxa de erro por provider/model, latência, tokens,
  custo global, conversão e churn.

**Testes mínimos:**

- Contract tests dos cinco adapters e testes opcionais de integração real.
- Criptografia/rotação/máscara/exclusão e isolamento rigoroso entre usuários.
- BYOK inválido nunca usa chave global; usuário A nunca acessa chave de B.
- Assinatura ativa autoriza, vencida bloqueia, webhook duplicado é idempotente e
  cota impede a chamada antes de gerar custo.
- Usage de todos os caminhos LLM recebe provider/model/source corretos.
- Fluxos Playwright de configuração BYOK, troca de provider, assinatura e estados
  de erro nos três viewports suportados.
- E2E real da Fase 43 executado uma vez em BYOK fake e uma vez em plataforma fake.

**Critério pronto:** um usuário gratuito consegue cadastrar uma chave própria e
jogar sem consumir credenciais globais; um assinante ativo consegue jogar com a
infraestrutura da plataforma dentro de sua cota; nenhum segredo é retornado ou
logado; isolamento, cobrança, medição e bloqueios passam no CI.

**Fora de escopo inicial:** streaming SSE, CLI, marketplace de chaves, revenda de
créditos avulsos, endpoint customizado informado pelo usuário, fallback automático
entre providers e cobrança por consumo excedente sem consentimento explícito.


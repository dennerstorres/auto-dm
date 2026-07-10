# Auto DM — Especificação

> Um mestre de RPG autônomo para Dungeons & Dragons 5ª edição. Um jogador humano, party de companheiros controlados por IA, e o mestre é inteiramente a IA.

---

## 1. Visão

Jogar D&D 5e solo, com a narrativa, NPCs, encounters e arbitragem de regras conduzidos por um LLM configurável. O jogador controla um personagem; o resto da party são companheiros IA com personalidades e folhas próprias. A IA faz **tudo** que um mestre humano faria: narra, interpreta NPCs, descreve o ambiente, controla antagonistas em combate, recompensa o roleplay.

**Não é:** VTT, ferramenta de gestão de mesa, gerador de mapa. É uma **experiência narrativa interativa** com mecânica de 5e respeitada.

**Público:** jogador solo de D&D que quer jogar a qualquer hora, sem precisar de mesa; jogador que quer testar um personagem ou campanha; mestre que quer ver como a IA arbitra.

---

## 2. Stack e princípios

- **Linguagem:** Python 3.11+
- **LLM atual:** abstração `LLMProvider` com **Minimax** ativo por configuração
  global (`AUTO_DM_*`). A Fase 51 adicionará Minimax, OpenAI, Claude, Gemini e
  DeepSeek com configuração por usuário, BYOK e assinatura.
- **Modelagem de estado:** Pydantic (validação em runtime, serialização pra JSON).
- **Orquestração de agentes:** loop próprio de DM + companheiros (LangChain/LangGraph previstos mas não obrigatórios no MVP).
- **Web backend:** FastAPI + uvicorn (auth, sessões e REST). Streaming SSE está
  arquivado definitivamente; respostas de LLM chegam completas.
- **Frontend:** HTML/CSS/JS vanilla (sem build step), com wizard de criação de personagem no browser.
- **Persistência:** **Postgres** (users + saves no web) + **Redis** (sessões ativas, TTL 24h). O engine também serializa `GameState` para JSON (usado em saves e testes).
- **Deploy:** **Docker** (Dockerfile single-stage + `docker-compose.yml` prod / `docker-compose.dev.yml` dev com Postgres+Redis+backend).

### Como rodar

O projeto roda primariamente em Docker:

```bash
# Dev (sobe Postgres + Redis + backend, frontend em http://localhost:14004)
cp .env.example .env          # setar JWT_SECRET + AUTO_DM_API_KEY
docker compose -f docker-compose.dev.yml up --build

# Prod (backend only; Postgres+Redis externos; bind 127.0.0.1:4004)
docker compose up -d --build
```

Variáveis obrigatórias no `.env`: `JWT_SECRET` (≥32 chars),
`AUTO_DM_API_KEY`, `DATABASE_URL`, `REDIS_URL`, `FRONTEND_URL`.
Detalhes de deploy (nginx, TLS, Vercel, backups) em `DEPLOY.md`.

### Princípios inegociáveis

1. **Mecânica é autoritativa.** O motor de regras em Python sempre tem razão. O LLM narra, mas a jogabilidade passa pelo engine. A IA não pode "decidir" que acertou um ataque que, mecanicamente, errou.
2. **Contexto é gerenciado ativamente.** Campanhas longas estouram tokens. Resumos periódicos são obrigatórios.
3. **Tudo é configurável, nada é hardcoded de forma oculta.** Provider, modelo, temperatura, idioma, nível de detalhamento de narração.

---

## 3. Escopo do MVP (v0.1)

### Dentro

- ✅ Configuração global de Minimax e modelo via `config.json` + `.env`
- 📋 Multi-provider e configuração por usuário — planejados na Fase 51
- ✅ Criação de personagem do jogador (raça, classe, background, atributos, equipamento inicial)
- ✅ Companheiros IA pré-definidos (3-5 personagens com folhas prontas e personalidades distintas)
- ✅ Exploração com narração livre ("O que você faz?")
- ✅ Combate por turnos com:
  - Iniciativa
  - Rolagem de ataque (d20 + modificador vs AC)
  - Rolagem de dano com crítico (dobra os dados de dano)
  - Aplicação de dano ao HP
  - Vantagem e desvantagem
  - Conditions (13 oficiais do PHB)
  - Action economy (ação, ação bônus, reação, movimento)
  - Saving throws
  - Magias cantrip e níveis 1-2
  - Slots de magia
  - Descanso curto e longo
  - Morte e death saves
- ✅ Save/load em JSON
- ✅ Motor de dados carregado dos `.md` do PHB (não hardcoded em Python)

### Fora (v0.1, planejado pra versões futuras)

- ❌ Multiplayer
- ❌ Mapa visual com tokens
- ❌ Voz / TTS
- ❌ Geração de imagem
- ❌ Níveis acima de 5
- ❌ Multiclasse
- ❌ Feats
- ❌ Classes/raças/magias fora do PHB
- ❌ Geração dinâmica de NPCs com ficha completa (vai usar stat blocks pré-fabricados)
- ❌ Sistema de loja / economia
- ❌ Tracking de tempo no mundo
- ❌ Campanhas pré-escritas (vai ser tudo sandbox dirigido pelo DM)

---

## 4. Arquitetura

```
┌─────────────────────────────────────────────────┐
│              Frontend web (browser)              │
│   input do jogador, render do estado, log        │
└──────────────────┬──────────────────────────────┘
                   │  HTTP REST (FastAPI backend)
                   ▼
┌─────────────────────────────────────────────────┐
│              Game Loop / Turn Manager            │
│   decide de quem é o turno, chama agentes,      │
│   valida ações, aplica resultado                │
└──────┬──────────────────────┬────────────────────┘
       │                      │
       ▼                      ▼
┌──────────────┐      ┌─────────────────┐
│  DM Agent    │      │ Companion Agent │
│  (LLM call)  │      │ (LLM call por   │
│              │      │  companheiro)   │
└──────┬───────┘      └────────┬────────┘
       │                      │
       ▼                      ▼
┌─────────────────────────────────────────────────┐
│            Rules Engine (Python puro)           │
│   valida ações, rola dados, atualiza estado     │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│        Game State (Pydantic, in-memory)         │
│   party, NPCs, iniciativa, condições, mundo     │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
            JSON save/load
```

### Fluxo de um turno (combate)

1. O frontend renderiza iniciativa, HP de todos, conditions ativas
2. Motor diz "é o turno do Guerreiro IA"
3. Companion Agent recebe: system prompt com a ficha do guerreiro + traits de personalidade + estado atual da mesa
4. LLM retorna JSON estruturado: `{action_type, target, reasoning, dialogue}`
5. Motor valida: a ação é possível? (tem ação bônus disponível? tem slot? alvo existe?)
6. Motor executa: rola dado, aplica dano, atualiza HP
7. Motor devolve resultado estruturado
8. Companion Agent recebe resultado e gera narração flavor
9. Log exibe tudo pro jogador

### Fluxo fora de combate

1. DM Agent narra cena, apresenta situação
2. Jogador digita ação em texto livre
3. DM Agent responde (narração + estado do mundo atualizado)
4. Motor atualiza estado se algo mecânico mudou (ex: "abro a porta" → verifica se tem armadilha, etc)

---

## 5. Modelo de dados

### Personagem
```python
class Character:
    name: str
    race: str           # ex: "Elf"
    class_: str         # ex: "Ranger"
    level: int          # 1-5 no MVP
    background: str
    alignment: str

    # Atributos
    abilities: AbilityScores  # STR, DEX, CON, INT, WIS, CHA

    # Combate
    hp_current: int
    hp_max: int
    armor_class: int
    speed: int
    hit_dice: str       # ex: "1d10"
    hit_dice_remaining: int

    # Proficiências
    proficiencies: Proficiencies  # skills, saves, tools

    # Equipamento
    inventory: list[Item]
    equipped: EquippedSlots       # arma, armadura, escudo

    # Magia (se aplicável)
    spellcasting: Optional[Spellcasting]
        # ability, save_dc, attack_bonus
        # spells_known, spells_prepared
        # spell_slots: dict[int, int]  # nível → restantes

    # Condições ativas
    conditions: list[Condition]

    # Roleplay
    personality_traits: list[str]
    ideals: list[str]
    bonds: list[str]
    flaws: list[str]
```

### GameState
```python
class GameState:
    # Config
    campaign_name: str
    started_at: datetime

    # Mundo
    current_location: str
    time_of_day: str
    weather: str

    # Party
    party: list[Character]  # jogador + companheiros
    player_character_id: str  # qual é o PC

    # Combate
    in_combat: bool
    initiative_order: list[str]  # character ids
    current_turn_index: int
    round_number: int

    # NPCs / inimigos
    npcs: list[NPC]  # stat block simplificado

    # Missões
    active_quests: list[Quest]
    completed_quests: list[Quest]

    # História
    narrative_log: list[NarrativeEntry]  # pra alimentar o LLM
    summary_history: list[str]  # ledger append-only de resumos consolidados

    # Phase 33 — periodic summarizer config + cursor state
    summary_enabled: bool = True                # kill switch de runtime
    summary_every_n_entries: int = 20           # dispara quando o log cresce N entradas desde o último resumo
    summary_char_threshold: int = 12_000        # OU quando o log cruza N chars totais
    last_summarized_at_index: int = 0           # índice em narrative_log da última entrada resumida (exclusivo)
    last_summary_attempt_at_index: int = 0      # última tentativa (sucesso OU falha) — retry cooldown
```

### Resumo periódico (Phase 33)

Quando `narrative_log` cruza os thresholds acima, um LLM call extra (no
mesmo provider do DM) condensa `narrative_log[:-6]` em uma única string
pt-BR e a anexa em `summary_history`. Apenas a entrada mais recente é
injetada no system prompt do DM (`build_dm_context_block` →
`## Resumo de eventos anteriores`); as entradas mais antigas ficam em
disco para inspeção admin e replay futuro.

Regras:
- `summary_history` é **append-only** — entradas antigas não são
  apagadas. Dedup trivial (texto idêntico ao anterior colapsa para
  no-op).
- Cooldown por fórmula (sem lock explícito): após cada tentativa,
  `last_summarized_at_index` (sucesso) ou `last_summary_attempt_at_index`
  (qualquer tentativa) avançam, e `should_summarize` retorna False
  por algumas entradas.
- Falhas (provider down / `<NO_SUMMARY>` / markdown-only / < 50 chars):
  warning logged, `last_summary_attempt_at_index` avança mas
  `last_summarized_at_index` NÃO — a próxima turno re-tenta.
- Custo do summarizer é taggeado como `kind="summarizer"` em
  `UsageEvent`, separado da quota diária do jogador.

### Action (entrada do LLM pro engine)
```python
class Action:
    actor_id: str
    action_type: ActionType  # ATTACK, CAST_SPELL, DASH, DODGE, ...
    target_id: Optional[str]
    params: dict  # ex: {"weapon": "longsword", "spell": "fireball"}
    dialogue: Optional[str]  # fala do personagem, narrada
    reasoning: Optional[str]  # raciocínio (pro log)
```

---

## 6. Motor de regras (o coração)

### Responsabilidades

- Rolar dados (notação `1d20+5`, `2d6+3`, `4d6kh3` pra stat rolling)
- Validar ações: é possível no turno? tem recurso? alvo existe?
- Executar ações: rolar ataque, calcular dano, aplicar
- Gerenciar conditions (início, fim, efeitos por turno)
- Action economy (1 ação, 1 bônus, 1 reação por turno, movimento variável)
- Saving throws
- Concentration checks pra magias

### Princípio: o LLM propõe, o engine dispõe

LLM nunca rola dado diretamente. Ele diz: "vou atacar o orc com minha espada". Engine valida e executa. LLM recebe resultado e narra. Isso evita:
- IA "inventando" modificadores
- IA "esquecendo" advantages
- IA sendo inconsistent com regras

### Tabela de ações suportadas no MVP

| Ação | Tipo | Custo |
|---|---|---|
| Attack | ação | 1 ação |
| Cast Spell | ação/ação bônus | depende da magia |
| Dash | ação | 1 ação |
| Disengage | ação | 1 ação |
| Dodge | ação | 1 ação |
| Help | ação | 1 ação |
| Hide | ação | 1 ação |
| Ready | ação | 1 ação |
| Search | ação | 1 ação |
| Use Object | ação | 1 ação |
| Shove | ação | 1 ação |
| Grapple | ação | 1 ação |
| Two-Weapon Attack | ataque extra | 1 ação bônus |
| Opportunity Attack | ataque | 1 reação |

---

## 7. Integração com LLM

### Interface
```python
class LLMProvider(Protocol):
    name: str
    model: str

    def chat(self, messages: list[Message]) -> str: ...
    def count_tokens(self, messages: list[Message]) -> int: ...
```

### Config (`config.json`)
```json
{
  "provider": "minimax",
  "model": "MiniMax-Text-01",
  "temperature": 0.8,
  "max_tokens": 2048,
  "language": "pt-BR",
  "narrative_detail": "medium",
  "rules_strictness": "high"
}
```

### System prompts (por papel)

- **DM:** prompt longo com regras, personalidade (sério, cómico, sombrio), responsabilidades
- **Companion:** prompt curto com a ficha + traits + role na party

---

## 8. Companheiros IA

3-5 companheiros pré-definidos com personalidades distintas. Cada um tem:
- Ficha completa (raça, classe, nível, background)
- Tracos de personalidade do PHB
- Uma "voz" específica no system prompt

Sugestões pro MVP:
- **Thorgar** — Anão Guerreiro, tanque, sério, leal
- **Lyra** — Halfling Ladina, cômica, oportunista
- **Mira** — Humana Clériga, devota, gentil
- (mais 1-2 a definir)

### Loop de companheiro

A cada turno do companheiro:
1. Engine: "é o turno de Thorgar"
2. LangGraph: chama nó `companion_thorgar` com state da mesa
3. LLM: retorna ação + diálogo
4. Engine: valida, executa, atualiza state
5. LangGraph: chama nó `dm_narrate` com resultado
6. LLM: narra o que aconteceu (visão do jogador)

---

## 9. Persistência

O backend web persiste em **Postgres** (tabelas `User` + `Save`, com
slug amigável e meta block) e usa **Redis** apenas para sessões ativas
(TTL 24h).

### Saves (Postgres / JSON)
- Estado completo serializado (Pydantic → dict → JSON)
- Versão do save (pra migração futura)
- Timestamp

### Carregamento
- Lista saves disponíveis (`/list` ou `GET /api/saves`)
- Jogador escolhe
- Estado reconstruído em memória
- Narrativa é repopulada via resumos (não o log inteiro)

### Resiliência
- Saves vivem no Postgres, não no Redis — perder Redis não perde
  campanha, só derruba sessões ativas (que reautenticam).
- Dump noturno do Postgres recomendado em produção (ver `DEPLOY.md`).

---

## 10. Critérios de "pronto" do v0.1

Um jogador consegue:
1. Subir o stack com Docker (`docker compose -f docker-compose.dev.yml up --build`)
2. Criar conta (ou usar invite-code) e logar no browser
3. Configurar a chave global Minimax (via `.env` `AUTO_DM_API_KEY`)
4. Criar um personagem nível 1 pelo wizard no browser
5. Começar uma campanha com 2-3 companheiros IA
6. Explorar uma cena narrada
7. Entrar em combate, agir em vários turnos
8. Companheiros IA agem autonomamente de forma crível
9. Magias cantrip e nível 1 funcionam
10. Salvar, fechar, abrir de novo e continuar de onde parou

---

## 12. Pós-MVP: segunda onda (Fases 39–43, renumerada)

> Incrementos depois das Fases 0–33. Todas as features abaixo respeitam
> os três princípios inegociáveis (mecânica autoritativa, contexto
> gerenciado, configurabilidade) e adicionam zero acoplamento de
> provider LLM. A implementação atual usa Minimax; a Fase 51 formaliza o
> contrato multi-provider sem acoplar essas features a um adapter específico.
>
> **Renumeração (2026-07):** a onda foi planejada como Fases 34–39, mas
> esses números foram usados por outras entregas (ver CLAUDE.md). O
> mapeamento atual: §12.2 → Fase 39, §12.3 → Fase 40, §12.4 → Fase 41,
> §12.5 → Fase 42, §12.6 → Fase 43. A §12.1 foi **removida do roadmap**.

### 12.1 Painel de personagem em tempo real (REMOVIDA)

> **Removida por decisão do usuário (2026-07):** as fichas com abas das
> Fases 36/37 (reais) já cobrem a parte visual, e o jogo turn-based
> re-renderiza as fichas a cada `/input` — o live-polling (ETag /
> `X-State-Rev`) deixou de valer o custo. Texto mantido como histórico.

**Problema:** a console web mostra apenas a narrativa. Para saber HP
atual, AC, spell slots, conditions, recursos (ki, sorcery points,
inspiration), o jogador precisa rolar `narrative_log` ou usar meta-
comandos. Mata a sensação de "estar jogando".

**Solução:** painel lateral fixo à esquerda (≥280 px em desktop,
drawer retrátil em ≤768 px) que reflete o `GameState` em tempo quase
real durante o jogo.

**Conteúdo do painel, top-down:**
1. **Cabeçalho:** nome do personagem, classe-nível, raça, alinhamento,
   avatar (inicial do nome em círculo colorido derivado da classe).
2. **Bloco vital:** barra HP segmentada (verde/amarelo/vermelho +
   número `atual / máx`), AC grande (com tooltip mostrando breakdown
   `10 + DEX + shield + armor + magic`), speed, iniciativa atual
   quando em combate, hit dice restantes (`Atual / Total`, ex.: `5/8`).
3. **Conditions ativas:** chips coloridos (`poisoned`, `stunned`,
   `exhausted` etc.) com ícone e tooltip — clicar abre descrição;
   botão × para tentar remoção (mecânica continua exigindo ação
   apropriada; UI apenas invoca `/api/sessions/{id}/condition/remove`
   com a flag certa).
4. **Spell slots:** tabela compacta `1º: ●●●○  2º: ●●○○  3º: ●○○○`,
   respondendo ao spell level máximo da classe. Clicar em um ● gasta
   (modal de confirmação só para níveis 5+).
5. **Recursos por classe (PHB):** Ki (Monk), Sorcery (Sorcerer), Rage
   (Barbarian), Bardic Inspiration (Bard), Channel Divinity (Cleric/
   Paladin), Lay on Hands (Paladin), Superiority (Fighter),
   Spell Slots pactos (Warlock), Wild Shape (Druid), Focus Points
   (Druid). Cada recurso como bloco próprio com progress bar.
6. **Atributos:** seis abas STR/DEX/CON/INT/WIS/CHA com mod ao lado.
7. **Toggle `companion / self`:** dropdown para inspecionar PC ou cada
   companheiro (mesmo layout, dados do `Character` selecionado).

**Implementação:**
- `GET /api/sessions/{id}/state` (já existente) é reusado; o painel
  faz **polling** a cada 2.5 s durante combate (server `Last-Modified`
  + `ETag` curtos baseados em `state.model_dump_json().hash()` para não
  retornar corpo idêntico) e a cada 6 s em exploração.
- Eventos críticos invalidam o cache: `POST /api/sessions/{id}/input`,
  `/api/sessions/{id}/condition/remove`, `/api/sessions/{id}/inventory/equip`
  respondem com `X-State-Rev: <int>`; o front refaz poll imediato
  quando vê rev diferente do último conhecido.
- `CharacterRender` (novo, em `web/static/render_panel.js`) é
  puramente funcional — recebe JSON, devolve DOM. Sem framework.
- O painel renderiza 100% cliente-side. Nada de LLM, zero impacto na
  quota.
- Companheiros mostram apenas os dados públicos do `Character`
  (HP, conditions, recursos); internals como `inventory`/spells podem
  ser ocultos se o jogador ligar um setting `hide_party_internals`.

**Configurabilidade:** `panel_visible` (on/off), `panel_position`
(`left`/`right`/`off`), `refresh_rate_ms` (5000–10000). Stored no
profile do usuário em `users.preferences` (JSONB opcional, migração
idempotente no `lifespan`).

**Fora do escopo desta seção:** edição de equipamento (vai para a
Fase 39), drag-and-drop de magias (mantém-se modal discreto).

---

### 12.2 Inventário & equipamento na web (Fase 39)

**Problema:** loot, equipar/desequipar, comprar/vender ainda não têm
fluxo na web. O jogador que está na web não consegue consumir o `bag of
holding` que acabou de encontrar, nem trocar a armadura após level-up.

**Solução:** fluxo visual completo de gerenciamento de inventário com
persistência no servidor.

**Modelo:** `Character.inventory: list[Item]` e `EquippedSlots`
(armor/hand_main/hand_off/shield) já existem em `state/models.py`.
Falta expor operações semânticas.

**Endpoints novos (todos sob `/api/sessions/{id}/inventory`):**
| Método | Path | Função |
|---|---|---|
| GET | `/{id}/inventory` | `InventoryView` agrupado por categoria |
| POST | `/{id}/inventory/equip` | `{item_id, slot}` → valida, troca, retorna diff |
| POST | `/{id}/inventory/unequip` | `{slot}` → move pro inventário |
| POST | `/{id}/inventory/drop` | `{item_id, quantity?}` → remove (atravessa `attunement` check) |
| POST | `/{id}/inventory/buy` | `{vendor_id, item_id, quantity?}` → checha ouro, transfere |
| POST | `/{id}/inventory/sell` | `{item_id, quantity?}` → adiciona ouro, remove |
| GET | `/{id}/shop/{vendor_id}` | catálogo do NPC, com preços |

**Loja:** um NPC é flagado como `vendor: bool` em `state/models.py`
(migração idempotente). Quando o jogador usa `talk to innkeeper`, o
DM pode setar `npcs[id].vendor = true` (classe estruturada disponível
para tool-call do LLM, opcional). `GET .../shop/{vendor_id}` lista
inventário do NPC (tabela stock em `npcs[].shop_inventory` com
`{item_id, price_gp, restock_daily: bool}`). Preços respeitam a
tabela PHB cap. 5 (sellers padrão); itens mágicos raros escalam por
raridade (common 100 gp, uncommon 500 gp, rare 5 000 gp).

**Engine integra com o que já existe:**
- `Engine.swap_equipped(character_id, slot, item_id)` (novo, em
  `engine/inventory.py`) usa as funções de AC já em
  `engine/defenses.py::effective_ac` para recalcular.
- Magic items com `requires_attunement`: valida limite de 3 itens
  sintonizados por personagem (PHB p. 138) antes de aceitar.
- Curses (curse of magic items) lidos no PHB marker `*` em
  `data/phb/Treasure/` — `magic_item.curse: bool` (novo campo) e
  tooltip mostra `"Aparentemente inofensivo, mas…"` para flag=true.

**Frontend:**
- Aba `Inventário` no painel do personagem (toggle entre Painel /
  Inventário / Spellbook).
- Grid de slots: `Helm | Armor | Main-hand | Off-hand | Shield |
  Boots | Cloak | Accessory` (PHB p. 151 + ring slots), cada um um
  `<div>` dropzone.
- Lado direito: lista filtrável por categoria (Weapon, Armor,
  Potion, Scroll, Misc, Magic).
- Modal de inspeção para item mágico mostra `rarity`, `attunement?`,
  descrição markdown-rendered, `curse` warning, e botões `Equipar` /
  `Sintonizar` / `Soltar` / `Vender (X gp)`.
- Loja: overlay full-screen com catálogo do NPC, calculando
  affordance (`Ouro: 47 gp`, botão `Comprar` desabilitado se
  insuficiente).

**Ouro:** campo `Character.gold_gp: int` (novo, migração
idempotente, default 0).

**Validações na engine:**
- Trocar de armadura respeita `proficiencies.armor`.
- Trocar arma respeita `proficiencies.weapons` (warning, não bloqueia
  — DnD 5e impõe desvantagem, não proibição, no ataque).
- Stack de consumíveis (poções, ammo) usa `Item.quantity: int` (novo,
  default 1).

**Fora do escopo da Fase 39:** crafting, encumbrance tracking (PHB
cap. 5 variante), separação por container (saco de carga é trivial:
`bag_of_holding` vira token na ficha, não expande lista).

---

### 12.3 Encontros aleatórios + tesouros em viagem (Fase 40)

**Problema:** o DM narra viagens curtas. Para "viajem três dias pela
Estrada do Rei", falta o motor rolar encontros (PHB cap. 5, com tabelas
do **DMG cap. 3**) e distribuir tesouros de acordo com o nível do grupo.

**Solução:** módulo `agents/world.py` + tabelas locais para
encontros/tesouros, plugado no `process_player_action` quando o input
for detectado como "modo viagem".

**Detecção de modo:** heurística simples adicionada em
`agents/heuristics.py::infer_intent(text)`:
- `"viajar"`, `"viajem"`, `"caminhar até"`, `"travel to"`, `"head to"`
  → `Intent.TRAVEL` com `hours`/`days` extraído por regex
  (`"3 dias"`, `"uma hora"`, `"overnight"`).
- Outros intents continuam inalterados (`COMBAT_TRIGGER`,
  `EXPLORE`, `TALK`, `REST`).

**Tabelas novas em `data/world_tables/` (recurso aberto, não PHB —
criado à mão ou a partir do DMG SRD):**
```
data/world_tables/
├── README.md
├── encounters/
│   ├── forest_day.json   # CR-weighted monsters por bioma + horário
│   ├── forest_night.json
│   ├── road_day.json
│   ├── road_night.json
│   ├── dungeon_level_1.json
│   └── dungeon_level_5.json
├── loot/
│   ├── individual.json   # 1d20+CR → item, gold
│   ├── hoard_low.json    # 4d6×100 gp + items
│   ├── hoard_mid.json    # "         "
│   └── hoard_high.json
└── weather.json         # 1d20 por bioma + estação
```

Estrutura JSON exemplo:
```json
{
  "name": "Floresta — Dia",
  "weight": 30,
  "entries": [
    {
      "roll": "1-30",
      "monsters": [{"id": "wolf", "count": "2d4"}],
      "notes": "Lobos seguindo rastro fresco"
    },
    {
      "roll": "31-50",
      "monsters": [{"id": "goblin", "count": "2d6"}]
    }
    // ...
  ]
}
```

**Roteamento no DM Agent:** bullet novo no `DM_SYSTEM_PROMPT`
`## Encontros aleatórios` dizendo: "Quando o jogador declara uma
viagem de **N horas/dias**, role `N` checks de encontro (1 a cada 4h
viagem contínua) usando a tabela do bioma atual. Reporte o resultado
no formato `MEC: encounter <table_id> <roll> -> <monsters>`. Esta tag
marca que o encontro é canônico e o motor vai spawnar."

**Engine processa a tag:** novo módulo `engine/world.py` com
`roll_encounter(state)`, `resolve_travel(state, hours)`. Whitelist de
tags processadas:
- `MEC: encounter <table> <roll> <monsters>` → spawn no `npcs[]`,
  adiciona entrada no `narrative_log` antes do turno do jogador.
- `LOOT: hoard <tier> <roll>` → executa tabela, dá ouro e items
  direto via `add_item_to_inventory` (Fase 39 reaproveita API).
- `WEATHER: <table> <roll>` → atualiza `state.weather`.

**Determinismo:** seed de randomização baseado em
`(state.campaign_seed, day, hours_into_day)`. Permite replay/admin
inspeção. O `narrative_log` registra a seed usada por turno de
viagem.

**Anti-abuso:** cooldown: o encontro só dispara uma vez a cada
`world_event_cooldown_minutes = 30` minutos de jogo (configurável,
default 30); senão o jogador zera encontros pedindo 1h de cada vez.

**Moderação do LLM:** o LLM é encorajado a narrar primeiro e gerar a
tag depois. Engine confia na tag (validada por regex estrita);
falha de validação → log warning + encontro padrão nível 1 do bioma
(`cr_for_level(state.party_level)`).

**Fora do escopo da Fase 40:** tabelas específicas por setting
(Forgotten Realms, Eberron) — só biomas genéricos. Travel via
`fast-travel` mágico (`Teleport`, `Word of Recall`) ignora encontros.

---

### 12.4 Reações além de Opportunity Attack (Fase 41)

**Problema:** hoje `engine/combat_engine.py` só dispara uma reação
explícita: `OPPORTUNITY_ATTACK`. O `_reaction_used` flag é setado,
mas magias defensivas como `Shield`, `Counterspell`, `Healing Word`
(como reação), `Hellish Rebuke`, e a reação universal via
`War Caster`/`Sentinel` etc. não têm canal.

**Solução:** generalizar o conceito de reação para `Reaction`
declarativa, suportando **reação via magia** e **reação passiva
triggered**.

**Modelo de dados:** novo enum `engine.actions.ReactionKind`:
```python
class ReactionKind(str, Enum):
    OPPORTUNITY_ATTACK = "opportunity_attack"
    SHIELD_SPELL = "shield_spell"
    COUNTERSPELL = "counterspell"
    HEALING_WORD = "healing_word_bonus"  # Cure Wounds também é reação
    HELLISH_REBUKE = "hellish_rebuke"
    UNCANNY_DODGE = "uncanny_dodge"      # Rogue L5+ (já tem handler)
    PARRY = "parry"                       # Blade Pact Warlock, EK Fighter
    REPRIMAND = "reprimand"              # UA, pulado
```

Cada `ReactionKind` mapeia para um trigger:
| Kind | Trigger | Spell/Resource cost |
|---|---|---|
| Opportunity Attack | inimigo deixa reach | attack action |
| Shield | você é atingido por ataque | reaction + 1st-level slot |
| Counterspell | alvo conjura (V/S/M detectável) | reaction + slot L ≥ spell L |
| Healing Word | alguém a 60ft cai a 0HP | action bonus |
| Hellish Rebuke | você sofre dano | reaction + 1st-level slot |
| Uncanny Dodge | você é atingido por ataque | reaction (sem recurso) |
| Parry | você é atingido por ataque | reaction + reduce damage 1dX + prof |

**Engine wiring:**
- Novo `combat_engine.py::_dispatch_reactions(trigger, source, target, payload)`
  itera todos os personagens do lado reativo (`source` ou `target`,
  conforme trigger), checa `reaction_used` + sortelha condição.
- Cada `ReactionKind` retorna um `ReactionResolution{consumed: bool, effects: list}`
  e é aplicado antes do `DamageApply` final.
- Reações mágicas: nova branch em `_handle_cast_spell` com flag
  `is_reaction=True` (não gasta action, gasta slot).
- Validações: Shield = PC tem slot L1 disponível + spell preparado;
  Counterspell = spell preparado/conhecido + slot adequado e
  `arcana` check com DC `10 + spell_level`; Healing Word = spell
  preparado; Hellish Rebuke = Warlock EB expanded list? Não — é
  magia L1 warlock Fiend, separada em `engine/spellcasting.py`.

**Sistema de trigger visível ao jogador:** quando um trigger
dispara (ex.: "goblin te ataca"), o painel lateral mostra modal
`Você foi atingido! Reações disponíveis: [Shield (L1, 1 slot)] [Uncanny
Dodge] [Parry] [Cancelar]`. O jogador escolhe OU passa (sem reação).
Para actions do jogador: passa por padrão — fica configurável em
`auto-pass_shield: bool` em `users.preferences`.

**NPCs / Companions:** reações deles são decididas pelo
`CompanionAgent` (1 call extra) ou via `CompanionDecisionPolicy` com
heurística (rule-based) que decide pelas condições (HP < 30% Shield →
auto, Counterspell só se alvo fechar combate etc.). A política é
extensível; default rolando 1d20 + WIS mod, threshold configurável
em `companion_reaction_aggression: float`.

**Determinismo no teatro:** quando o trigger é "auto-fire" (ex.:
opportunity attack sem sair do reach), a engine aplica direto.
Quando é "player choice", a engine suspende o `apply_damage` por
até 1 turno in-game (com TTL de 30s no web) e devolve a lista de
reações elegíveis via `state.pending_reaction: Optional[...]`.

**Cobertura:** apenas as magias marcadas em `data/phb/Spells/*.md`
como `**Casting Time**: 1 reaction`. O parser do PHB já captura o
casting time — falta apenas estender o `cast_spell` engine branch.

**Fora do escopo da Fase 41:** Sentinels, Warcaster feats, Blade
Pact attacks (complexos demais). Adicionados em backlog.

---

### 12.5 Narração por voz + música ambiente (Fase 42)

**Problema:** sessões D&D solo se beneficiam muito de narração por
voz (imersão, cansaço visual reduzido). Hoje o jogador lê.

**Solução:** TTS opcional da última linha de narração, mais música
ambiente contínua.

### TTS — backend: `edge-tts`

**Escolha tecnológica:** `edge-tts` (pip,
`github.com/rany2/edge-tts`, **GPL-3.0**, **grátis**, **sem API key**).

**Restrições aceitas:**
- **GPL-3.0** é copyleft forte. Aceito porque o backend do nosso
  app não é distribuído comercialmente e o uso é interno (não
  embute `edge-tts` no frontend). Adicionar `edge-tts` ao `pip
  install -e ".[audio]"` como extra opcional em `pyproject.toml`.
- **Requer internet** para o serviço `api.msedgespeech.microsoft.com`.
  Sem rede → degrada graciosamente para texto, sem erro. Frontend
  exibe "Voz indisponível (offline)" quando `navigator.onLine=false`
  ou falha o primeiro request.
- **Sem SLA oficial** da Microsoft. Para um jogo solo caseiro, é
  adequado. Trocar por `gTTS` ou `pyttsx3` offline fica documentado
  como alternativa trivial (mesma interface, sem cache de SSML).

**Backend:**
- `web/tts.py` (novo, isolado para evitar GPL-3 spread):
  - `async synthesize(text: str, voice: str = "pt-BR-FranciscaNeural", rate: float = 1.0) -> bytes`
  - Cache LRU em disco (`/tmp/tts_cache/{sha1}.mp3`), TTL 30 dias
    (purga por LRU + idade).
  - Endpoint: `GET /api/sessions/{id}/tts?text=<urlenc>&voice=<v>`
    retorna `audio/mpeg`, 200 OK; 503 se Microsoft offline.
  - Vozes expostas via `GET /api/tts/voices` (lista 30+ vozes pt-BR +
    EN, com previews).
  - Config de voz do usuário em `users.preferences.tts` =
    `{"enabled": false, "voice": "pt-BR-FranciscaNeural", "rate": 1.05, "auto": "narration_only"}`.

**Frontend:**
- Botão 🔊 no canto do chat que toca a última linha de narração.
- Auto-play opcional após cada DM message
  (`preferences.tts.auto` = `"every_dm_message" | "narration_only"
  | "off"`). Default `"off"` para não assustar novos usuários.
- `AudioContext` lazy-inicializado na 1ª interação (mobile Safari
  bloqueia autoplay).
- Cache de áudio no `CacheStorage` do browser — chave `sha1(text+voice+rate)`,
  evita `/api/tts` repetido.

### Música ambiente

**Solução leve:** usar faixas geradas em runtime via
`howler.js`-style loops? **Não — overkill.** Em vez disso, configurar
a URL de uma playlist pública no perfil do usuário
(`preferences.music.url`) e um único `<audio loop>` controlado por
botões na UI (play, pause, volume, mute).

**Defaults bons (linkados no profile do usuário, não embarcamos):**
- `https://incompetech.com/music/royalty-free/` (Kevin MacLeod,
  CC-BY).
- Para combate: `https://www.tabletopaudio.com/` (com link de
  doação no README).
- Frontend carrega via `crossorigin="anonymous"`; se o servidor
  bloquear CORS, mostra erro silencioso e botão "Reportar problema
  de música" (link mailto).

**Configurabilidade:**
- `users.preferences.music = {"enabled": false, "src": "...", "volume": 0.5}`.

**Persistência:** ambos os blocos adicionados em
`users.preferences` (JSONB), migração idempotente.

**Fora do escopo da Fase 42:** geração dinâmica de música por IA, mix
adaptativo por estado de combate, voice cloning. Upmixes ficam em
backlog.

---

### 12.6 End-to-end do fluxo completo (Fase 43)

**Problema:** 1 700+ testes unitários estão sólidos, mas nenhum exercita
o caminho **signup → wizard 11 passos → combate com 3 turnos do
jogador + 2 turnos de companheiro → save → logout → login → load →
continuar + painel lateral + TTS + loja**. Bugs de integração só
aparecem no uso manual.

**Solução:** bateria E2E com `pytest-anyio` + `httpx.AsyncClient` +
um stack real (Postgres + Redis via `docker-compose.dev.yml`).
Paralelização via `pytest-xdist` (2 workers).

**Escopo:** 1 arquivo `tests/e2e/test_full_flow.py` com 4 cenários
independentes; cada cenário escreve/lê seu próprio save (slugs
sufixoados por `uuid.uuid4().hex[:8]` para evitar colisão entre
runs).

**Stack:**
- `tests/e2e/conftest.py` sobe `app` do `web/server.py` numa porta
  efêmera, conecta no Postgres/Redis de dev, cria `TestClient` httpx.
- Helper `signup_login(client) -> token` (3 linhas).
- Helper `play_turn(client, token, session_id, text)` que bypassa
  LLM real (patcha `LLMProvider.chat` com fixture `fake_dm` que
  retorna narração determinística a partir de tags do input). Custo:
  zero tokens, velocidade ~50 ms/turno.
- Screenshots opcionais (`playwright` instalado em `dev-deps`
  opcional) para validar o frontend no CI: `index.html` carregado
  em browser headless, clica "Criar personagem", preenche wizard,
  verifica que `#output` mostra narração pós-clique em "Enviar".

**Cenários:**
1. **Solo wizard → 3 turnos → save → logout → load → 2 turnos**.
   Variante: Wizard Sorcerer L3, companions: Kael (Wizard) +
   Garrick (Paladin) + Mira (Cleric). Cobre: spell slots, sneak
   attack, aoo saving throw, concentration break.
2. **Fichas refletem HP após ataque** (fichas das Fases 36/37 reais).
   Após turno com dano, `GET /sessions/{id}` + `/companions` mostram
   `hp_current` atualizado.
3. **Loja: comprar/vender sem saldo** (após Fase 39). Verifica 402
   quando `gold_gp < price`, e happy path com successo 200.
4. **Viagem de 3 dias dispara encontro + loot** (após Fase 40).
   Seed fixa; verifica que `npcs[]` ganha 2 enemies + `gold_gp`
   aumenta; engine tag MEC resolvida.

**Acceptance:** CI do `Makefile` target `make e2e` roda <2 min,
verde. Flake rate <0.5 % (rerun automático em falha única).

**Ferramentas:**
- `pytest-anyio` (async fixtures)
- `httpx` (HTTP client async)
- `playwright` opcional (frontend visual)
- `pytest-xdist --maxfail=1` (paraleliza)

**Fora do escopo da Fase 43:** stress tests (1000 campanhas),
fuzz da API, multi-tenancy security tests (deixados para uma fase
futura de segurança).

---

## 13. Plataforma multi-provider e SaaS (Fase 51)

A Fase 10 original está arquivada: adicionar adapters globais isoladamente não
resolve o produto público. A Fase 51 passa a ser a fonte de verdade para a
evolução de LLM e terá cinco providers iniciais: **Minimax, OpenAI, Anthropic
Claude, Google Gemini e DeepSeek**.

### Modalidades de uso

- **Gratuito/BYOK:** o usuário cadastra uma chave própria, escolhe provider e
  modelo permitidos e assume diretamente o custo do provider.
- **Assinatura da plataforma:** um entitlement ativo permite usar credenciais
  globais dentro das cotas e modelos definidos pelo plano.
- O modo é explícito por usuário. Erro ou ausência de chave BYOK nunca aciona
  uma credencial global como fallback.
- A configuração global `AUTO_DM_*` permanece durante a migração e em ambientes
  privados/admin, mas não concede acesso SaaS por si só.

### Limites arquiteturais

- Credenciais BYOK são criptografadas com autenticação em repouso, chave mestra
  externa ao banco, nonce e versão por registro. A API só devolve máscara/status.
- Chaves não entram em preferências JSON, saves, logs, traces ou analytics e são
  apagadas junto com a conta.
- Endpoints e catálogos de modelos são controlados no servidor; o usuário não
  fornece base URL arbitrária.
- Um resolver único cria o contexto efetivo do provider para DM, companions,
  sumarização, sugestão de nomes e futuros pontos de LLM.
- Usage identifica `byok`, `platform` ou `legacy`. BYOK é medido para operação,
  mas não consome a franquia paga; `platform` valida entitlement e cota antes da
  chamada e reconcilia o usage real depois.
- Billing usa uma abstração própria. Webhooks precisam de assinatura,
  idempotência e proteção contra replay; nenhum dado de cartão é armazenado.
- Não haverá CLI, SSE, fallback automático entre providers, endpoint customizado
  por usuário ou cobrança excedente sem consentimento explícito.

O modelo de dados, subfases, migração, rollout e critérios completos estão na
seção **Fase 51 — Multi-provider, BYOK e assinatura** do `PLAN.md`.

---

## 11. Documentos de referência (PHB 5e)

Os `.md` do PHB ficam em `data/phb/` e são consumidos por:
- LLM (trechos relevantes injetados no system prompt conforme o contexto)
- Engine (dados estruturados extraídos uma vez e cacheados em `src/auto_dm/rules_data/`)

Categorias esperadas:
- `races.md`, `classes.md`, `backgrounds.md`
- `equipment.md`, `spells.md`
- `conditions.md`, `combat.md`
- etc

---

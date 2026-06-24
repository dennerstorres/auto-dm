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
- **LLM:** abstração `LLMProvider` com adaptadores para **Claude, OpenAI, Gemini, GLM, Minimax**. Provider e modelo configuráveis via arquivo de config.
- **Modelagem de estado:** Pydantic (validação em runtime, serialização pra JSON).
- **Orquestração de agentes:** LangChain + LangGraph (companheiros como nós do grafo, DM como nó central).
- **CLI:** Rich ou Textual (HP bars, painéis, log de combate colorido, replay de rolagens).
- **Persistência:** JSON em `saves/`.

### Princípios inegociáveis

1. **Mecânica é autoritativa.** O motor de regras em Python sempre tem razão. O LLM narra, mas a jogabilidade passa pelo engine. A IA não pode "decidir" que acertou um ataque que, mecanicamente, errou.
2. **Contexto é gerenciado ativamente.** Campanhas longas estouram tokens. Resumos periódicos são obrigatórios.
3. **Tudo é configurável, nada é hardcoded de forma oculta.** Provider, modelo, temperatura, idioma, nível de detalhamento de narração.

---

## 3. Escopo do MVP (v0.1)

### Dentro

- ✅ Configuração de provider (5 opções) e modelo via `config.json` + `.env`
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
│                   CLI (Textual)                  │
│   input do jogador, render do estado, log        │
└──────────────────┬──────────────────────────────┘
                   │
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

1. CLI renderiza iniciativa, HP de todos, conditions ativas
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
    summary_history: list[str]  # resumos antigos
```

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
    def stream(self, messages: list[Message]) -> Iterator[str]: ...
    def count_tokens(self, messages: list[Message]) -> int: ...
```

### Config (`config.json`)
```json
{
  "provider": "claude",
  "model": "claude-sonnet-4-6",
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

### `saves/<campaign_name>.json`
- Estado completo serializado (Pydantic → dict → JSON)
- Versão do save (pra migração futura)
- Timestamp

### Carregamento
- Lista saves disponíveis
- Jogador escolhe
- Estado reconstruído em memória
- Narrativa é repopulada via resumos (não o log inteiro)

---

## 10. Critérios de "pronto" do v0.1

Um jogador consegue:
1. Instalar (`pip install -e .`)
2. Configurar API key de um provider
3. Criar um personagem nível 1
4. Começar uma campanha com 2-3 companheiros IA
5. Explorar uma cena narrada
6. Entrar em combate, agir em vários turnos
7. Companheiros IA agem autonomamente de forma crível
8. Magias cantrip e nível 1 funcionam
9. Salvar, fechar, abrir de novo e continuar de onde parou

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

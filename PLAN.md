# Auto DM — Plano de implementação

> Caminho pra chegar no MVP. Cada fase é um bloco entregável e testável.

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
│   ├── cli/
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

## Fase 10 — Providers restantes (1-2 dias)

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
| 10 | 1-2 dias |
| 11 | 2-3 dias |
| **Total** | **~3-5 semanas** |

Variável de acordo com profundidade. O motor de regras (Fase 3) e o combate (Fase 7) são as fases mais longas e as que mais vão exigir decisão.

---

## Onde começar

Próximo passo concreto: **Fase 0 + Fase 1 juntas**, pra ter um esqueleto de CLI conversando com um LLM de verdade. A partir daí, Fases 2 e 3 em paralelo (modelos de estado + motor de regras) porque são fundação pra tudo depois.

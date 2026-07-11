# Auto DM

AI-powered solo D&D 5e game master. Um jogador humano, party de companheiros controlados por IA, e o mestre é inteiramente a IA.

> Veja `SPEC.md` para a especificação completa e `PLAN.md` para o plano por fases.

> **Decisões permanentes:** o produto é 100% web; o CLI da Fase 34 e o SSE da
> Fase 26b estão arquivados e não voltarão. A Fase 10 também foi arquivada e
> substituída pela futura Fase 51 (multi-provider, BYOK e assinatura SaaS).

---

## Princípios arquiteturais inegociáveis

1. **Mecânica é autoritativa.** O motor de regras em Python é a fonte da verdade. O LLM **narra**, mas nunca decide mecânica. Toda ação de combate passa por validação e execução no engine.
2. **LLM propõe, engine dispõe.** Companheiros e jogador enviam intenções (texto livre ou JSON estruturado), engine valida se a ação é possível, executa rolagens, aplica dano, atualiza estado, e devolve resultado. LLM só vê o resultado pra narrar.
3. **Contexto é gerenciado ativamente.** Resumos periódicos evitam estourar tokens em campanhas longas.
4. **Configurável por design.** Provider, modelo, temperatura, idioma, nível de narração — nada hardcoded de forma oculta.

## Stack

- **Python 3.11+**
- **Pydantic** para modelos de estado e validação
- **FastAPI + Uvicorn** para o backend web (auth, sessões e REST; SSE arquivado)
- **LangChain/LangGraph** para orquestração de agentes (DM + companheiros)
- **Provider LLM atual**: **Minimax** por configuração global. Multi-provider,
  BYOK por usuário e assinatura SaaS estão planejados na Fase 51.
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
├── persistence/  # save/load JSON
└── web/          # backend FastAPI (auth, sessões, REST) + static/
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
```

> **Provider ativo é Minimax** — o adapter é carregado do ambiente via
> `LLMConfig.from_env(prefix="AUTO_DM_")`. Não há adapter novo pra
> implementar; o backend web lê as vars `AUTO_DM_*`.

## Regras para Claude Code

- **Nunca ler `.env`** — contém API keys. Usar `.env.example` como referência de template.
- **`data/phb/` é leitura livre** — esses `.md` são a fonte de regras. Conteúdo derivado do D&D 5e **SRD v5.1** (Open Game License + CC BY 4.0) — não é o PHB completo. Arquivos com prefixo `#` (ex: `# Racial Traits.md`) são índices introdutórios; sem prefixo são conteúdo.
- **Provider ativo é Minimax** — a Fase 10 está arquivada. Novos adapters e
  configuração por usuário devem seguir integralmente a futura Fase 51
  (Minimax/OpenAI/Claude/Gemini/DeepSeek + BYOK/assinatura), sem reativar o escopo antigo.
- **D&D 5e, PHB only** no MVP. Níveis 1-5 no MVP (estendido a 1-20 pelas Fases 25f/25g). Sem multiclasse, sem feats, sem classes/raças/magias fora do PHB.
- **Idioma do produto**: pt-BR (interface, narração, mensagens). Código/identificadores em inglês.
- **Tarefas são rastreadas** via TaskList. Ao começar uma fase, marcá-la `in_progress`; ao terminar, `completed`. Criar tasks pra qualquer trabalho com 3+ passos.
- **Ao concluir uma fase**, registrar o changelog detalhado em `HISTORY.md` e atualizar o índice compacto "Onde estamos" abaixo — **nunca** colar o changelog completo neste arquivo (ele entra inteiro no contexto de toda sessão).
- **Aprovar antes de ações destrutivas** (deletar/sobrescrever saves, rodar comandos perigosos).
- **Style**: line-length 100, target Python 3.11, ruff como linter, pytest para testes.

## Onde estamos

> O changelog detalhado por fase (decisões, desvios de plano, módulos tocados,
> contagens de teste) vive em **`HISTORY.md`**. Consulte-o antes de mexer em
> área coberta por uma fase concluída — o resumo abaixo é só um índice.

**Concluídas:**

- **Fases 0–9** — fundação: skeleton, provider Minimax, state models, dice +
  combat engine, PHB loader, character creation, DM agent + narrative loop,
  combat system, companions, persistence JSON.
- **Fases 11–24** — regras 5e: conditions, adventuring/rests, languages,
  poisons/traps/diseases, cover + opportunity attack, ASI + inspiration,
  spellcasting completo, Rage, Sneak/Smite/Extra Attack/Fighting Style,
  resource pools, handlers de classe no CombatEngine, defesas passivas,
  specialists.
- **Fases 25a–25h** — conteúdo PHB: monsters, subclasses,
  backgrounds/tools/gear, magic items, movement/mounts, leveling L6–L20 +
  capstones de classe.
- **Fases 26a–26e** — web: FastAPI + auth, wizard no browser, deploy Docker,
  invite-code gate.
- **Fases 27–33** — produto: 12 companheiros + synergy roll, busy feedback,
  roles admin, painel admin + limites/custos de uso, narração configurável,
  cenário inicial, sumarização periódica (memória de longo prazo).
- **Fases 35–42** — features: sugestão de nomes com IA, fichas de companheiros
  (+ spells/inventário), XP/progressão/ASI, inventário & loja, encontros de
  viagem, reações (41a–41c), TTS + música ambiente.
- **Fase 43** — E2E do fluxo completo (4 cenários canônicos via HTTP real,
  LLM fake determinístico, gate no GitHub Actions).
- **Fases 44–50** — redesign frontend: design system, shell/navegação/feedback
  global, lobby, wizard, mesa de jogo, admin/preferências, qualidade
  (Playwright + budgets de assets + CI).

**Arquivadas (não voltarão):** Fase 10 (adapters globais → substituída pela
51), Fase 26b (SSE), Fase 34 (CLI removido — produto é 100% web).

**Próxima:** Fase 51 — Multi-provider, BYOK e assinatura SaaS (não iniciada;
ver `PLAN.md`).

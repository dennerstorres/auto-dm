# Auto DM

Mestre de jogo solo inspirado nas regras 5e, com companions controlados por IA,
motor de regras determinístico e interface web completa.

O jogador controla um personagem enquanto a IA conduz o Mestre e os demais
integrantes do grupo. A narrativa é gerada pelo modelo de linguagem, mas as
mecânicas são resolvidas pelo código Python: rolagens, ataques, dano, condições,
recursos, iniciativa e progressão não ficam a critério da IA.

> Status: projeto funcional em desenvolvimento ativo. O produto é 100% web;
> o antigo CLI e o streaming SSE foram arquivados.

## Principais recursos

- Campanhas solo com Mestre e companions controlados por IA.
- Criação de personagem pelo navegador, com raças, classes, subclasses,
  backgrounds, perícias, magias e seleção do grupo.
- Motor de combate com iniciativa, ações, reações, condições, concentração,
  death saves e recursos de classe.
- Progressão por XP, níveis 1–20, ASI e atualização de spell slots.
- Inventário, equipamentos, sintonização, loot, lojas e ouro.
- Viagens, clima, encontros aleatórios e tesouros.
- Saves persistidos em PostgreSQL e sessões ativas em Redis.
- Memória narrativa de longo prazo por sumarização periódica.
- Narração por voz e música ambiente opcionais.
- Interface responsiva, acessível e testada com Playwright.
- Providers Minimax, OpenAI, Anthropic Claude, Google Gemini e DeepSeek.
- BYOK: cada usuário pode armazenar sua própria chave de API criptografada.

## Como o acesso à IA funciona

O cadastro é aberto e o código de convite é opcional:

- **Sem convite:** a conta usa exclusivamente uma chave própria (BYOK).
- **Com convite válido:** a conta pode alternar entre BYOK e a chave global
  configurada pelo responsável pelo servidor.

Uma conta BYOK-only nunca utiliza silenciosamente a chave global. Se a chave do
usuário estiver ausente, inválida ou indisponível, a chamada é bloqueada antes de
chegar ao provider.

Para aceitar cadastros públicos, habilite `AUTO_DM_BYOK_ENABLED=1` e configure
`AUTO_DM_CREDENTIALS_KEY`. Caso contrário, usuários sem convite conseguirão criar
a conta, mas não poderão iniciar chamadas de IA.

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic e SQLAlchemy async |
| Frontend | HTML, CSS e JavaScript ES modules, sem framework |
| Banco | PostgreSQL |
| Sessões e rate limit | Redis |
| Providers | Minimax, OpenAI, Anthropic, Gemini e DeepSeek |
| Testes | pytest, Ruff, Playwright e axe |
| Deploy | Docker Compose e Uvicorn |

## Início rápido com Docker

### Pré-requisitos

- Docker com Compose
- Uma chave de API de ao menos um provider
- Git

### Ambiente de desenvolvimento

```bash
git clone https://github.com/dennerstorres/auto-dm.git
cd auto-dm
cp .env.example .env
```

Edite o `.env` e configure, no mínimo:

```dotenv
JWT_SECRET=gere-um-segredo-com-pelo-menos-32-caracteres
AUTO_DM_PROVIDER=minimax
AUTO_DM_API_KEY=sua-chave-global
AUTO_DM_BYOK_ENABLED=1
AUTO_DM_CREDENTIALS_KEY=1:sua-chave-fernet
```

Para gerar uma chave Fernet:

```bash
python -c "from cryptography.fernet import Fernet; print('1:' + Fernet.generate_key().decode())"
```

Suba PostgreSQL, Redis e a aplicação:

```bash
docker compose -f docker-compose.dev.yml up --build
```

Acesse <http://localhost:14004>.

Para encerrar sem apagar os dados:

```bash
docker compose -f docker-compose.dev.yml down
```

Para apagar também os volumes locais:

```bash
docker compose -f docker-compose.dev.yml down -v
```

> O stack de desenvolvimento publica PostgreSQL em `127.0.0.1:25432`, Redis em
> `127.0.0.1:26379` e a aplicação em `127.0.0.1:14004`.

## Configuração

As variáveis abaixo são as mais importantes. Consulte [.env.example](.env.example)
para o template completo.

| Variável | Finalidade |
|---|---|
| `JWT_SECRET` | Assinatura dos tokens de autenticação; use pelo menos 32 caracteres. |
| `DATABASE_URL` | Conexão async com PostgreSQL. |
| `REDIS_URL` | Conexão com Redis. |
| `FRONTEND_URL` | Origens permitidas pelo CORS, separadas por vírgula. |
| `AUTO_DM_PROVIDER` | Provider global utilizado pelo servidor. |
| `AUTO_DM_API_KEY` | Chave global do provider escolhido. |
| `AUTO_DM_MODEL` | Modelo global; quando vazio, usa o padrão do provider. |
| `AUTO_DM_BYOK_ENABLED` | Habilita credenciais por usuário. |
| `AUTO_DM_CREDENTIALS_KEY` | Chave Fernet versionada para criptografar credenciais BYOK. |
| `INVITE_CODE` | Convite opcional que concede acesso à IA global no cadastro. |
| `ADMIN_USERNAME` | Nome da conta administrativa inicial. |
| `ADMIN_PASSWORD` | Senha usada para criar o primeiro administrador. |

Nunca versione o arquivo `.env` nem chaves reais de provider.

## Desenvolvimento local sem Docker

Crie um ambiente virtual e instale o projeto com as dependências de desenvolvimento:

```bash
python -m venv .venv
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

PostgreSQL e Redis precisam estar disponíveis nas URLs configuradas. Inicie o backend:

```bash
uvicorn auto_dm.web.server:create_app --factory --reload
```

## Testes e qualidade

Backend:

```bash
pytest
ruff check src tests
ruff format --check src tests
```

E2E real com PostgreSQL e Redis isolados:

```bash
make e2e
make all
```

Frontend:

```bash
npm ci
npx playwright install chromium
npm run test:e2e
npm run test:assets
```

Os testes de navegador cobrem landing, autenticação, lobby, wizard, mesa e
administração em viewports mobile, tablet e desktop, incluindo acessibilidade
com axe e snapshots visuais.

## Arquitetura

| Módulo | Responsabilidade |
|---|---|
| `auto_dm.web` | API FastAPI, autenticação, sessões, persistência e frontend. |
| `auto_dm.agents` | Mestre, companions, prompts, heurísticas e narrativa. |
| `auto_dm.engine` | Regras, dados, combate, progressão, inventário e mundo. |
| `auto_dm.state` | Modelos Pydantic e transições de estado. |
| `auto_dm.phb` | Carregamento e consulta dos dados SRD 5.1. |
| `auto_dm.character` | Construção e evolução de personagens. |
| `auto_dm.llm` | Registry, adapters e contrato comum dos providers. |
| `auto_dm.persistence` | Serialização e helpers de save. |

Princípios do projeto:

1. **A mecânica é autoritativa:** o engine decide; a IA narra.
2. **A IA propõe, o engine dispõe:** ações são validadas antes da execução.
3. **O contexto é gerenciado:** resumos preservam campanhas longas.
4. **Providers são isolados:** detalhes de SDK não vazam para o domínio.
5. **Credenciais são protegidas:** BYOK é criptografado e nunca retornado pela API.

## Estrutura do repositório

```text
src/auto_dm/
├── agents/          # Mestre, companions e narrativa
├── character/       # criação e progressão de personagens
├── companions/      # roster e sinergia do grupo
├── engine/          # motor de regras
├── llm/             # registry e adapters dos providers
├── persistence/     # serialização de saves
├── phb/             # loader e lookups do SRD
├── state/           # modelos e estado do jogo
└── web/             # FastAPI e frontend estático
data/phb/             # conteúdo derivado do SRD 5.1
tests/                # testes Python, web e E2E
```

## Documentação

- [SPEC.md](SPEC.md): especificação funcional e técnica.
- [PLAN.md](PLAN.md): roadmap e fases de implementação.
- [HISTORY.md](HISTORY.md): histórico detalhado das entregas.
- [DESIGN.md](DESIGN.md): design system e regras da interface.

## Limitações conhecidas

- O projeto não implementa multiclasse nem feats.
- As respostas do jogo são retornadas completas por REST; não há SSE.
- O conteúdo incluído é o **SRD 5.1**, não o Player's Handbook completo.
- Custos, disponibilidade e limites das APIs de IA dependem de cada provider.

## Licença

O código original do Auto DM é disponibilizado sob a
[PolyForm Noncommercial License 1.0.0](LICENSE):

- uso pessoal, estudo, pesquisa, testes e projetos não comerciais são permitidos;
- alterações e redistribuição são permitidas apenas para finalidades não comerciais;
- vender, licenciar comercialmente, cobrar pelo acesso ou incorporar o código em
  produto ou serviço comercial não é permitido sem autorização separada do titular.

Essa é uma licença **source-available**, não uma licença open source aprovada pela OSI.
Para solicitar uma licença comercial, abra uma issue no repositório para iniciar
o contato com o titular.

Esta licença vale para a versão atual e para versões futuras que a indiquem.
Versões anteriormente publicadas sob MIT continuam disponíveis nos termos que
acompanhavam aquelas versões; a mudança de licença não revoga permissões já concedidas.

O conteúdo em `data/phb/` possui licenciamento próprio, descrito em
[data/phb/LICENSE](data/phb/LICENSE), e não é relicenciado pela PolyForm.

## Aviso sobre D&D e SRD

Os dados de regras incluídos derivam do Dungeons & Dragons 5th Edition System
Reference Document v5.1, disponibilizado pela Wizards of the Coast sob OGL 1.0a
e CC BY 4.0. Consulte [data/phb/LICENSE](data/phb/LICENSE) para os textos e
atribuições aplicáveis.

Este projeto não é afiliado, patrocinado nem endossado pela Wizards of the Coast.
“Dungeons & Dragons” e “D&D” são marcas de seus respectivos titulares. O
Player's Handbook proprietário não está incluído neste repositório.

## Contribuições

Issues e pull requests são bem-vindos. Ao contribuir, você concorda que sua
contribuição poderá ser distribuída sob a mesma licença não comercial aplicada
ao código original do projeto.

# Auto DM — Design system da experiência web

> Fonte de verdade para todas as implementações visuais do Auto DM.
> Este documento traduz a direção aprovada na landing page em regras para
> produto, componentes e telas autenticadas.

## 1. Visão

O Auto DM combina duas atmosferas que devem coexistir:

- **Entrada cinematográfica:** fantasia épica, ilustração rica, tipografia
  editorial e sensação de convite à aventura.
- **Mesa de jogo funcional:** interface silenciosa, densa e previsível para ler,
  comparar informações e agir repetidamente.

A landing pode ser dramática. Lobby, wizard, mesa e administração precisam ser
mais contidos. A ambientação deve vir de cor, tipografia, textura e linguagem,
nunca de ornamentos que disputem espaço com a tarefa.

## 2. Princípios

### Imersão com propósito

Cada elemento temático deve reforçar contexto ou hierarquia. Dragões, mapas e
ilustrações pertencem a momentos de entrada, transição ou empty state. A mesa de
jogo prioriza narrativa, ficha, recursos e ações.

### Clareza durante a ação

O jogador deve distinguir imediatamente:

- o que aconteceu;
- quem agiu;
- qual decisão está pendente;
- quais recursos ainda estão disponíveis;
- qual é a ação principal da tela.

### Consistência antes de novidade

Controles equivalentes usam o mesmo componente, posição, nome e comportamento.
Uma nova variação só existe quando resolve uma necessidade que as atuais não
cobrem.

### Densidade organizada

O produto contém muitas regras e estados. Não esconder informação importante
para deixar a tela “limpa”. Agrupar, alinhar e permitir leitura progressiva.

### Fantasia original

Usar linguagem visual própria. Não reproduzir logos, personagens, monstros,
layouts de livros ou marcas oficiais de Dungeons & Dragons.

## 3. Personalidade

**Atributos:** épico, misterioso, preciso, acolhedor e sóbrio.

**Evitar:** infantil, cartunesco, excessivamente medieval, terror pesado,
interface de cassino, painel SaaS genérico ou decoração neon.

O vermelho representa chamado à ação e identidade. O dourado representa magia,
progresso e destaque. Tons de pergaminho oferecem contraste editorial. Carvão e
ferro estruturam as áreas operacionais.

## 4. Tokens visuais

Os valores abaixo devem virar custom properties em `tokens.css`. Não adicionar
cores próximas sem documentar uma função semântica nova.

### Cores da marca

| Token | Valor | Uso |
|---|---:|---|
| `--brand-crimson` | `#98292e` | CTA primário, marca e seleção forte |
| `--brand-crimson-hover` | `#b3373c` | Hover do CTA primário |
| `--brand-crimson-deep` | `#6f1d22` | Faixas temáticas e fundos de destaque |
| `--brand-gold` | `#d1a34a` | Destaques, ícones e progresso |
| `--brand-gold-soft` | `#e8c76f` | Texto dourado sobre fundo escuro |
| `--brand-parchment` | `#eee7d7` | Seções editoriais claras |

### Superfícies escuras

| Token | Valor | Uso |
|---|---:|---|
| `--ink-950` | `#0a0c0f` | Fundo principal |
| `--ink-900` | `#0d0f12` | Inputs e narrativa profunda |
| `--ink-850` | `#15171a` | Modais e painéis principais |
| `--ink-800` | `#1e2024` | Superfície elevada e hover |
| `--ink-700` | `#2a2d32` | Bordas fortes e divisores |

### Texto

| Token | Valor | Uso |
|---|---:|---|
| `--text-on-dark` | `#f5f0e6` | Texto principal em fundo escuro |
| `--text-on-dark-muted` | `#aaa9a5` | Texto secundário em fundo escuro |
| `--text-on-light` | `#24201b` | Texto principal no pergaminho |
| `--text-on-light-muted` | `#665f54` | Texto secundário no pergaminho |
| `--border-on-dark` | `#41382b` | Bordas temáticas escuras |
| `--border-on-light` | `#c8bdab` | Divisores sobre pergaminho |

### Cores semânticas

| Token | Valor | Uso |
|---|---:|---|
| `--status-success` | `#55a975` | Sucesso, disponível, conectado |
| `--status-warning` | `#d6a84b` | Atenção, recurso baixo |
| `--status-danger` | `#d45555` | Erro e ação destrutiva |
| `--status-info` | `#70a7cf` | Informação e estado neutro ativo |

O vermelho destrutivo deve ser distinguível do vermelho da marca por texto,
ícone e contexto. Nunca depender apenas da cor.

## 5. Tipografia

### Famílias

- **Display/editorial:** Georgia, `Times New Roman`, serif.
- **Interface:** Inter quando empacotada localmente; fallback Aptos, `Segoe UI`,
  sans-serif.
- **Dados e comandos:** `SFMono-Regular`, Consolas, monospace.

Georgia aparece em títulos de marca, hero, nomes de campanha e momentos
narrativos. Não usar em tabelas, formulários extensos ou controles compactos.

### Escala

| Token | Tamanho | Uso |
|---|---:|---|
| `--text-xs` | `12px` | Metadado e label auxiliar |
| `--text-sm` | `14px` | Interface compacta |
| `--text-md` | `16px` | Corpo e campos |
| `--text-lg` | `20px` | Título de painel |
| `--text-xl` | `28px` | Título de tela |
| `--text-2xl` | `48px` | Título editorial |
| `--text-hero` | `92px` | Marca no hero desktop |

Em mobile, `--text-hero` usa `58px` e `--text-2xl` usa `37px`. Não usar unidades
de viewport para fonte. Letter spacing é `0`, exceto labels curtos em caixa alta
e a marca.

### Regras de texto

- Corpo: line-height entre `1.5` e `1.75`.
- Interface: line-height entre `1.2` e `1.4`.
- Parágrafos de leitura: máximo de 68 caracteres por linha.
- Nunca cortar títulos ou labels de ação.
- Metadados podem usar ellipsis apenas quando o valor completo estiver acessível.

## 6. Espaçamento e geometria

### Escala de espaço

`4, 8, 12, 16, 24, 32, 48, 64, 96px`.

Usar `16px` como unidade comum entre elementos relacionados, `24–32px` entre
grupos e `48–96px` entre seções editoriais.

### Raios

| Token | Valor | Uso |
|---|---:|---|
| `--radius-sm` | `4px` | Inputs, botões e tags |
| `--radius-md` | `6px` | Painéis e menus |
| `--radius-lg` | `8px` | Modais e itens repetidos destacados |

Não usar pill em botões comuns. Pills ficam restritas a tags, status e filtros.
Não criar cards dentro de cards.

### Sombras

- Painel: `0 10px 30px rgba(0, 0, 0, 0.24)`.
- Modal: `0 24px 80px rgba(0, 0, 0, 0.65)`.
- Foco não usa sombra decorativa; usa anel semântico de alto contraste.

## 7. Layout

### Larguras

- Conteúdo editorial: máximo `1216px`.
- Formulários e texto longo: máximo `720px`.
- Mesa de jogo: aproveita a viewport, com padding mínimo `16px`.
- Modal padrão: `464px`; modal complexo: máximo `720px`.

### Breakpoints de referência

- `0–479px`: mobile compacto.
- `480–759px`: mobile amplo.
- `760–1023px`: tablet.
- `1024px+`: desktop.

Breakpoints respondem ao conteúdo, não a modelos específicos de aparelho.

### Estabilidade

- Boards, toolbars, fichas e counters usam dimensões ou tracks estáveis.
- Loading não pode redimensionar o controle que o contém.
- Painéis laterais têm largura previsível.
- Nada deve produzir scroll horizontal na página.

## 8. Imagens e ambientação

- Usar assets originais, licenciados ou gerados especificamente para o produto.
- Hero mostra personagens, ameaça e cenário de forma legível; não usar imagem
  genérica apenas atmosférica.
- Reservar espaço negativo na composição para texto quando a imagem for fundo.
- Aplicar overlay somente para contraste, sem esconder o assunto principal.
- Preferir WebP/AVIF e manter fallback quando necessário.
- Hero desktop deve ficar abaixo de 1 MB quando o pipeline de assets for criado.
- Não usar orbs, bokeh, blobs ou gradientes decorativos sem função.
- Não repetir a imagem da landing como fundo de telas operacionais.

## 9. Iconografia

- Usar Lucide como biblioteca padrão, empacotada localmente.
- Botões conhecidos usam ícone: fechar, voltar, editar, excluir, áudio e volume.
- Comandos inequívocos podem usar ícone e texto quando a ação é importante.
- Ícone sem texto precisa de `aria-label` e tooltip quando não for universal.
- Não usar emoji como ícone de interface nas novas implementações.
- Tamanho padrão: 16 px em controles compactos, 20 px em controles normais.

## 10. Componentes

### Botões

**Primário:** uma ação dominante por região. Fundo crimson, texto claro.

**Secundário:** borda discreta, fundo transparente ou superfície elevada.

**Ghost:** ferramentas de baixa ênfase em toolbars.

**Danger:** ação destrutiva, sempre com texto explícito e confirmação quando
irreversível.

Todos precisam de estados default, hover, active, focus-visible, disabled e
loading. Área interativa mínima de 44x44 px em touch.

O texto do botão permanece geometricamente centralizado. Quando houver seta ou
ícone auxiliar, ele deve ser posicionado independentemente para não deslocar o
label do centro visual.

### Campos

- Label visível acima do controle.
- Em formulários e modais, cada grupo de label, controle, hint e erro forma uma
  pilha vertical e ocupa toda a largura disponível. Inputs de texto, URL,
  senha, selects e textareas nunca dividem a linha com seu label ou hint.
- Grupos equivalentes de label e input usam o mesmo `gap` interno e a mesma
  distância vertical entre grupos.
- Placeholder exemplifica; não substitui label.
- Hint vem antes do erro e mantém espaço estável quando possível.
- Erro aparece próximo ao campo e no resumo do formulário quando houver vários.
- Inputs em mobile usam no mínimo 16 px para evitar zoom automático.
- Switches, checkboxes e radios são as exceções: podem alinhar controle e texto
  na mesma linha, desde que descrições longas quebrem sem comprimir o controle.
- Em campos de faixa, label e valor atual podem compartilhar a primeira linha;
  o slider ocupa sozinho a linha seguinte e sempre usa 100% da largura.

### Segmented controls e tabs

- Segmented control troca modo dentro do mesmo contexto, como entrar/cadastrar.
- Tabs trocam vistas irmãs, como campanhas ativas/arquivadas.
- Estado ativo usa cor, peso e indicador; não depende somente de fundo.

### Modais e drawers

- Modal para decisão curta e bloqueante.
- Drawer para inspeção ou ferramentas que preservam o contexto da tela.
- Sempre: título, fechamento familiar, `Esc`, focus trap, retorno de foco e
  scroll interno quando necessário.
- Formulários dentro de modal seguem a mesma grade vertical dos formulários de
  página. Abas não mudam largura, alinhamento ou espaçamento dos controles entre
  painéis irmãos.
- Barras de abas curtas distribuem opções em trilhas de largura estável. Só usar
  rolagem horizontal quando os labels não couberem sem truncamento no menor
  viewport suportado.
- Ações relacionadas ficam em uma região própria depois dos campos; podem formar
  colunas equivalentes no desktop e empilham no mobile compacto.
- Mobile usa modal central quando couber; formulários longos podem usar sheet de
  tela cheia.

### Painéis e cards

- Painel organiza uma ferramenta única, como rolagem ou ficha.
- Card representa um item repetido, como campanha ou companion.
- Se uma seção já tem superfície, seus filhos não recebem outra superfície sem
  necessidade funcional.
- Em áreas operacionais, preferir listas, divisores e tabelas a grades de cards.

### Feedback

- Loading: skeleton para conteúdo estruturado, spinner apenas em ações pontuais.
- Empty state: explica a situação e oferece uma ação clara.
- Erro: informa o que ocorreu e como tentar novamente.
- Sucesso: confirmação curta; não interromper o fluxo com modal.
- Offline e quota precisam permanecer visíveis enquanto afetarem a sessão.

## 11. Direção por tela

### Landing e autenticação

- Hero full-bleed com marca como primeiro sinal.
- Hero ocupa no mínimo toda a altura do viewport (`100dvh`) e a imagem usa
  enquadramento `cover` por breakpoint, sem distorção.
- Autenticação em modal focado, com modos claros e mensagem de erro localizada.
- Manter vermelho, ouro, carvão e pergaminho como referência visual.

### Lobby

- Deve parecer uma estante de campanhas organizada, não uma página de marketing.
- Campanha mais recente recebe prioridade, não tamanho excessivo.
- Exibir ações recorrentes sem menus desnecessários.

### Wizard

- Deve lembrar a construção de uma ficha, com progresso claro.
- Opções mostram nome, descrição essencial e estado de seleção.
- Arte de classe/raça é opcional e nunca pode atrasar ou deslocar o formulário.

### Mesa de jogo

- Narrativa é o elemento dominante.
- Ficha, rolagem, companions e inventário são ferramentas, não decoração.
- Diferenciar falas e eventos por hierarquia tipográfica e marcadores discretos.
- Composer permanece previsível e não se move quando o Mestre responde.

### Administração

- Visual utilitário e silencioso.
- Tabelas, filtros e comparação têm prioridade sobre ambientação.
- Cores de status são semânticas e independentes da marca.

## 12. Movimento

- Transições de hover/foco: `120–180ms`.
- Entrada de modal: no máximo `220ms`.
- Animação editorial pode chegar a `700ms` apenas na primeira visita à landing.
- Não animar continuamente elementos decorativos.
- Respeitar `prefers-reduced-motion` e remover transformações não essenciais.

## 13. Conteúdo e linguagem

- Idioma principal: português do Brasil.
- Tom: direto, evocativo e adulto.
- CTAs usam verbo e resultado: “Continuar aventura”, “Criar personagem”.
- Mensagens de sistema evitam jargão técnico e humor em situações de erro.
- “Mestre”, “campanha”, “personagem” e “companheiros” são termos preferidos.
- Não usar texto visível para explicar controles óbvios ou ensinar a interface.

## 14. Acessibilidade

- WCAG 2.2 AA como mínimo.
- Contraste de texto normal: 4.5:1; texto grande: 3:1.
- Focus-visible com no mínimo 2 px e contraste de 3:1.
- Ordem de tabulação segue a ordem visual.
- Todos os campos têm label; erros usam `aria-describedby`.
- Modais anunciam título e descrição.
- Status assíncrono usa live region sem repetir mensagens.
- Alvos touch têm no mínimo 44x44 px.
- Conteúdo permanece funcional com zoom de 200%.
- Cor nunca é a única forma de comunicar estado.

## 15. Responsividade

- Começar pelo conteúdo mais estreito e ampliar progressivamente.
- Em mobile, ações principais ocupam a largura quando isso melhora o toque.
- Toolbars quebram em grupos coerentes; não encolhem labels até ficarem ilegíveis.
- Tabelas viram linhas rotuladas ou usam scroll interno deliberado.
- Drawers e modais nunca excedem `100dvh`.
- Safe areas são respeitadas em barras fixas.
- Testar textos longos, nomes de campanha grandes e traduções futuras.

## 16. Regras de implementação

- Usar tokens; não inserir hex novo diretamente em componentes.
- Não adicionar `style="..."` em novo markup.
- Preferir classes sem dependência excessiva da estrutura HTML.
- IDs existem para comportamento e acessibilidade, não para estilização geral.
- Componentes suportam todos os estados antes de serem reutilizados.
- JavaScript não mede layout quando CSS resolve o problema.
- Não adicionar dependência de runtime via CDN.
- Assets consumidos pelo produto permanecem versionados em `static/assets/`.
- Mudanças visuais relevantes incluem captura desktop e mobile.

## 17. Checklist de revisão

- A tela segue a hierarquia definida para seu contexto?
- Existe apenas uma ação primária por região?
- Os tokens corretos foram usados?
- Há cards ou superfícies aninhadas sem necessidade?
- Todos os estados do componente existem?
- O texto cabe a 320 px e com zoom de 200%?
- O fluxo funciona somente por teclado?
- Focus, contraste e labels estão corretos?
- Loading e conteúdo dinâmico preservam o layout?
- A imagem ajuda o usuário a compreender o produto?
- `prefers-reduced-motion` foi respeitado?
- Há capturas em 390x844 e 1440x900?

## 18. Governança

- Toda alteração de token deve atualizar este documento e a referência de
  componentes.
- Exceções precisam registrar motivo, escopo e prazo de revisão.
- Componentes novos passam por revisão visual antes de entrar na base comum.
- O `PLAN.md` define a ordem de migração; este documento define como cada entrega
  deve parecer e se comportar.
- Quando código e documento divergirem, corrigir a divergência no mesmo trabalho,
  sem manter regras implícitas.

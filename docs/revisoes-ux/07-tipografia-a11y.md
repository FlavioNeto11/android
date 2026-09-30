# Tarefa 07: tipografia, contraste e alvos de clique (relatório)

Branch `ux/07-tipografia-a11y` (sai de `claude/ux-portal` e0b4636, com as tarefas 01 a 05). Data: 30/09/2026. Máquina:
central (Windows Server 2025). Modelo: Sonnet 5.5, esforço medium.

## O que mudou

1. **Escala de fonte em tokens** (`styles/tokens.css`): 12 · 13 · 14 · 16 · 20 · 24 px (`--fs-2xs`, `--fs-xs`/`--fs-sm`,
   `--fs-md`, `--fs-lg`, `--fs-xl`, `--fs-2xl`) e pesos 400/500/600 (`--fw-*`). `--fs-xs` e `--fs-sm` ficam os dois em 13 px:
   a legenda se distingue pela cor e pelo peso, não por um tamanho ilegível. Os 12 px só existem como `--fs-2xs` e só para
   rótulo de gráfico. Os `font-size` escritos à mão (10, 11, 11,5, 12, 12,5 px) em 8 CSS passaram para os tokens; `.mono`
   (0,94em) nunca fica abaixo de 13 px; o avatar de iniciais tem piso de 13 px. Estilos de texto nomeados em `base.css`:
   `.t-titulo-pagina`, `.t-titulo-secao`, `.t-rotulo`, `.t-valor`, `.t-legenda` (título de página já usa os tokens; os
   demais componentes já liam `--fs-*`, então a escala nova os alcançou sem tocar neles).
2. **Contraste** (`tokens.css`, `base.css`, `ui.module.css`):
   - `button` sem classe de fundo herdava o cinza nativo do sistema (2,56:1 medido em "Lista" e "Paradas"). Regra de
     elemento `button { background-color: transparent; border: 0 }` no `base.css` (qualquer classe de componente ainda ganha).
   - `--text-3` de `#8492a3` para `#8f9cae`: passa de 4,5:1 também sobre `--surface-4` (4,2 para 4,8).
   - Novo `--control-border` (`#6d7d91`, 3,7 a 4,6:1 sobre as superfícies) para a borda de campo, caixa de seleção e chave
     (contraste de componente, WCAG 1.4.11). Bordas decorativas de cartão ficaram como eram.
   - Passo "enviado sem recebido" do cartão do aparelho usava `opacity: .45` (2,7:1): agora cor `--text-3`.
3. **Alvos de clique de 32 px** (token `--hit-min`; `--hit-touch: 40px` ficou definido, sem uso): `btnSm`, `btnIconOnly`,
   `inputSm`, chip, resumo de `details`, chave, seletor, botões Cards/Lista, segmentado Automático/Manual, chips de
   filtro, pílulas do topo, contadores, chips da barra de listagem, âncoras de Diagnóstico e Configuração, etc.
   Para links e contadores dentro de linhas densas, a área entra como `padding` com margem negativa igual: o visual e o
   ritmo da linha não mudam (`.linkish`, `stateQuick`, `aparelhoBtn`, `personaDoAparelho`, `logResumo`, `linkBtn`,
   `tarefa`). A caixa de seleção continua com 18 px desenhados e o `<input>` invisível passou a 32 x 32 centrado nela.
   `select` e `input` soltos (sem classe) ganham `min-height: 32px` em `base.css`. O botão só-ícone dentro do `Tooltip`
   encolhia para 28 px (`.tooltipHost > * { flex: 1 1 auto; min-width: 0 }`): regra `.btn.btnIconOnly` com duas classes.
4. **Selos de estado com ícone e texto**: já eram (`Badge`/`StatusBadge` com ícone + rótulo). Nada novo; só o contraste
   e o tamanho acima. O item "estados só por cor" do briefing não se reproduziu nas 58 visões varridas (ver Divergências).
5. **Barra de status em três grupos** (`TopBar.tsx`, `TopBar.module.css`): a faixa de baixo virou **Saúde | Capacidade |
   Custos**, com rótulo discreto (texto pequeno em maiúsculas, `--text-3`) e um traço entre grupos; só o que pede ação
   leva cor. **Custos** saiu da faixa de cima e mora no seu grupo: isso também resolve o saldo sobrepondo o botão ao
   lado a 390 px. Saldos **sempre em US$** (`balanceUsd`/`balanceUsdLabel` em `lib/aiBalance.ts`: a conta em reais entra
   convertida pelo câmbio que o backend já informa). Ícone "i" ao lado dos saldos, com tooltip: valores estimados, o
   saldo informado menos o consumo medido, todos em US$. A faixa agora quebra de linha em vez de rolar escondida
   (antes `overflow-x: auto` sem barra); o medidor de CPU/RAM some abaixo de 1600 px (o número e a cor do limiar
   continuam) para a régua inteira caber em 1440 px. A 390 px os contadores viram grade de 2 colunas (em 3 colunas
   "aguardando você" era cortado pelos 13 px novos). O popover "IA em uso" também passou a US$.
6. **Foco do teclado**: o anel global (`:focus-visible`, `--focus-ring`) e `prefers-reduced-motion` já existiam em
   `base.css`/`tokens.css`; conferidos, sem mudança. Novo: escolher uma seção na **gaveta do menu** (abaixo de 1024 px)
   leva o foco ao `<main id="conteudo">`, em vez de voltar ao botão "Menu" (Esc e "Fechar" continuam voltando ao botão).
7. **Largura máxima** do conteúdo: `.page { max-width: var(--page-max) }` (1600 px, antes 2400), centralizada; a grade
   de aparelhos continua `auto-fill` e ganha colunas até 1600 px (em 1920 px, margem de 185 px de cada lado, 5 colunas).
8. **`--focus-panel-w`** (sem uso) apagado de `tokens.css`; os comentários em `Focus.module.css` que o citam ficaram
   (falam do dimensionamento do Foco, que hoje é outra regra).

## Auditor: `scripts/ui-auditoria.js`

Script sem dependências, só DOM, reutilizável pela tarefa 09. Injeta `window.__uiAuditoria(opcoes)` (objeto com `fontes`,
`contraste`, `alvos`, `semNome` e `contagens`; `{ resumo: true }` devolve só números) e `window.__uiRazao('#fg','#bg')`.
Somente leitura: não clica em nada. Mede: fonte visível abaixo de 13 px; contraste WCAG 2.x do texto contra o fundo
efetivo (compõe os fundos translúcidos dos ancestrais e a opacidade; 4,5:1, ou 3:1 para texto grande); alvos de clique
abaixo de 32 px (tolerância 0,5 px; para checkbox/radio vale o maior entre o campo e o rótulo); botão sem nome.

**Não é o axe nem o Lighthouse.** Não cobre: ordem de tabulação, leitor de tela, regras de ARIA, contraste de ícones,
bordas e gráficos, texto sobre imagem ou gradiente (aproxima pelo ancestral opaco), hover/foco/desabilitado, conteúdo
fora da tela ou em estado não aberto, nem modais e popovers fechados. Elementos com `font-size: 0` (texto só para leitor
de tela) e `disabled` ficam fora da conta.

## Antes e depois (real, backend vivo em leitura, 1440 x 900, máquina central, 30/09/2026)

Contagem de elementos distintos por tela, script acima, vite na 5197 contra `127.0.0.1:8000`, só navegação. "Antes" é
o `e0b4636` (sem as mudanças), "depois" é o `7c6ea3c` mais os ajustes finais do mesmo commit de relatório.

| Tela | Fonte < 13 px (antes → depois) | Contraste abaixo da meta | Alvos < 32 px (antes → depois) |
|---|---|---|---|
| Painel | 163 → 0 | 3 → 0 | 75 → 0 |
| Personas | 108 → 0 | 0 → 0 | 76 → 0 |
| Aplicativos | 53 → 0 | 0 → 0 | 16 → 0 |
| Execuções | 109 → 0 | 0 → 0 | 25 → 0 |
| Aprendizado | 123 → 0 | 0 → 0 | 127 → 0 |
| Infraestrutura | 61 → 0 | 0 → 0 | 33 → 0 |
| Configuração | 35 → 0 | 0 → 0 | 22 → 0 |
| Diagnóstico | 81 → 3 (exceção) | 0 → 0 | 20 → 0 |
| **Total** | **733 → 3** | **3 → 0** | **394 → 0** |

O briefing contava 78 alvos no Painel; com este auditor (que conta também as caixas de seleção, os selos clicáveis e
os controles do topo) o Painel dava 75 antes das mudanças. A diferença é de método, não de tela.

Varredura ampliada depois das mudanças, 1440 px: as 8 telas **mais todas as guias de Configuração, Aprendizado,
Infraestrutura e do Foco, as 4 guias de Aplicativos, o Foco do aparelho (`?foco=`) e as 10 guias do detalhe da persona**:
58 visões, **0 contraste abaixo da meta, 0 alvos abaixo de 32 px, 0 botões sem nome, 0 rolagem horizontal**; única fonte
abaixo de 13 px: os 3 rótulos de eixo do gráfico do Diagnóstico (12 px, exceção abaixo). A 390 px: 12 visões (8 telas, o
Foco e 3 guias de persona), mesmo resultado, sem rolagem horizontal. A 1024 px: as 8 telas, zero em tudo exceto os mesmos
3 rótulos de gráfico.

### axe-core 4.13 (real, 30/09, 21:00Z, 1440 x 900)

O dono liberou as ferramentas no meio do trabalho (instaladas fora do repo em `C:\temp\ui-verificar`). Rodei o **axe-core**
(tags `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa` e `best-practice`) dentro do painel do Browser da IDE, que tem a
sessão aberta (o Chrome sem cabeça caía na tela de entrada, e entrar com conta é proibido), nas 8 telas, em leitura. O "antes" é
o mesmo navegador com o `frontend/src` do `e0b4636` (`git checkout e0b4636 -- frontend/src`, restaurado para o `7c6ea3c` em
seguida; `git status` limpo); o "depois" é o `7c6ea3c`. O arquivo do axe foi copiado para `frontend/public` só durante a
medição e apagado (nada foi instalado no repo nem em `node_modules`). Lighthouse: `not_run` (o axe cobre as regras de
contraste e de alvo; o Lighthouse acrescentaria desempenho e SEO, que não são desta tarefa).

| Regra do axe | Antes | Depois |
|---|---|---|
| `color-contrast` (serious) | 3 nós no Painel ("Lista", "Paradas (4)") | **0** nas 8 telas |
| `target-size` (WCAG 2.2, 24 px) | 0 | 0 |
| `region` (moderate): região de avisos (toast) fora de marco | 1 por tela | 1 por tela (inalterado) |
| `heading-order` (moderate) em Personas, `#grupos-de-acesso` | 1 | 1 (inalterado) |
| `aria-allowed-role` (minor) em Diagnóstico, um `figure` | 1 | 1 (inalterado) |

O auditor próprio e o axe concordam: os 3 nós de contraste do Painel eram os mesmos botões sem fundo. As três
violações restantes não são de tipografia, contraste nem alvo (região do toast, ordem de cabeçalhos e um papel ARIA num
`figure`) e ficam para a tarefa 08 ou 09. O axe marca também itens "incompletos" de contraste (fundos que ele não
consegue resolver); o auditor próprio os trata compondo as camadas translúcidas. O `target-size` do axe é o de 24 px
do WCAG 2.2; o critério de 32 px deste relatório é mais exigente e só o auditor próprio o mede.

Tokens de texto, razão WCAG calculada a partir de `tokens.css` (script `__uiRazao` e cálculo equivalente em Node), texto
sobre `--surface-1` / `--surface-3` / `--surface-4`:

| Token | sobre surface-1 | surface-3 | surface-4 |
|---|---|---|---|
| `--text-1` | 15,2 | 12,9 | 11,2 |
| `--text-2` | 8,7 | 7,4 | 6,4 |
| `--text-3` (novo `#8f9cae`) | 6,5 | 5,5 | 4,8 |
| tons de status (sucesso, aviso, perigo, informação) | 8,0 a 10,4 | 6,8 a 8,8 | 5,9 a 7,6 |

Texto sobre o acento, aviso e perigo (botões sólidos): 7,6 / 8,4 / 5,8.

## Teclado (Painel e Personas)

Real, mesma sessão: nas duas telas, 115 e 106 controles tabuláveis, **nenhum com `tabindex` positivo** (a ordem é a do
DOM) e **todos com anel de foco visível** ao receber foco de teclado (conferido pelo `box-shadow`/`outline` calculado de
cada um, com o anel no irmão `.checkboxBox` para as caixas de seleção). Quatro Tab reais pelo navegador foram até um
botão do cartão do aparelho, com o anel `--focus-ring`. Não foi feita a leitura por leitor de tela.

## Exceções, com justificativa

- **Rótulos de eixo do gráfico do Diagnóstico** (`.chartLabel`, 12 px, `--fs-2xs`): texto dentro de SVG de gráfico, onde o
  briefing admite a exceção ("rótulos de gráfico documentados"). Três rótulos por gráfico.
- **Texto com `font-size: 0`** (`.conn :global(*)` abaixo de 1180 px, que deixa só o ícone de conexão e mantém o texto
  para leitor de tela): não é texto visível; o auditor o ignora. O estado segue no ícone e no `aria-live`.
- **Alvo de 18 px desenhado** na caixa de seleção e chave: o que clica é o `<input>` de 32 x 32, então o visual não muda.
- **Contraste de borda decorativa de cartão** (`--border-1`, 1,3:1): não é controle nem informação; segue como estava.
- **`--hit-touch: 40px`** definido e sem uso: o briefing fala em 40 px "em telas de toque" como ideal, mas ampliar tudo
  para 40 px mudaria a densidade do painel inteiro; ficou para uma decisão do dono.

## Divergências do briefing e decisões

- "Estados só por cor em alguns selos": não achei nenhum selo só colorido nas telas varridas (`Badge`/`StatusBadge` já
  levam ícone e texto; contadores de aviso levam o ícone de mão e o rótulo). Não mexi.
- "`--fs-xs` e `--fs-sm` iguais a 13 px": o briefing pede escala 12/13/14/16/20/24 e mínimo de 13 px para texto de
  interface. Como os dois tokens já eram usados em 320 lugares, em vez de trocar cada uso mantive os dois nomes e igualei o
  valor. A hierarquia entre legenda e texto passa a vir de cor e peso.
- "Axe ou Lighthouse": o trabalho começou sem autorização para baixar; escrevi o auditor próprio e, quando o dono liberou, rodei o axe 4.13 (seção acima). Lighthouse não rodou.
- "Moeda US$ padronizada": o backend informa `estimated_balance_usd` e `units_per_usd`; a conversão usa esse câmbio
  (não inventei taxa). A tela de Configuração → IA segue na moeda original da conta, porque lá a pessoa digita o saldo
  no console do provedor.
- O Foco do aparelho e as guias do Aprendizado/Configuração não entram na URL; a varredura as alcançou clicando nas
  guias (somente navegação, sem tocar em ação).

## Toques em arquivo alheio (mínimos, todos de CSS)

Tarefa 06 (roda em paralelo): `features/settings/Settings.module.css` (`.anchorLink` com altura mínima e uma fonte de 12
px para token), `features/settings/AiBalances.module.css` (`.console`, altura mínima) e
`features/aprendizado/Aprendizado.module.css` (`.linkBtn`, padding vertical de 6 px). Tarefas 03/04/05 (já integradas):
`features/command/CommandPanel.module.css` (segmentado), `features/devices/Devices.module.css` (fontes, contraste do passo
"recebido", alvos), `components/BarraListagem.module.css`, `features/profiles/Profiles.module.css` (chips, guias,
resumos) e, em TSX, `components/Avatar.tsx` (piso de fonte). Fora de posse de todas: `features/loja/Loja.module.css`
(`.chip`), `features/infra/Infra.module.css` e `features/diagnostics/Diagnostics.module.css`. Em TSX de outra posse, só
`TopBar.tsx`/`MenuLateral.tsx` (posse das tarefas 01/02, já integradas) e `lib/aiBalance.ts`.
O comportamento das tarefas 03, 04 e 05 não mudou.

## Arquivos alterados

Novos: `scripts/ui-auditoria.js`, `docs/revisoes-ux/07-tipografia-a11y.md`. Estilos: `styles/tokens.css`,
`styles/base.css`, `App.module.css`, `components/ui.module.css`, `components/BarraListagem.module.css` e os CSS de
topbar, command, devices, diagnostics, infra, loja, profiles, settings e aprendizado citados acima. Código:
`features/topbar/TopBar.tsx` (grupos, Custos, US$), `features/topbar/MenuLateral.tsx` (foco ao conteúdo),
`lib/aiBalance.ts` (`balanceUsd`, `balanceUsdLabel`), `components/Avatar.tsx`. Testes: `features/topbar/TopBar.test.tsx`.

## Provas

**simulated** (vitest, 30/09, cerca de 20:57Z): `npm run typecheck` verde; `npm test` inteiro: **83 arquivos, 984 testes,
todos verdes**. Novos ou alterados: `features/topbar/TopBar.test.tsx::saldo das contas de IA` (2: chip só da conta em uso
com tom do estado, e "padroniza em US$" com a conta em reais convertida e o ícone "Valores estimados, em US$") e
`::Menu lateral … escolher uma seção na gaveta leva o foco ao conteúdo, não ao botão "Menu"`.

**real** (30/09, 20:40Z a 20:58Z, máquina central, vite na 5197 contra `127.0.0.1:8000` em leitura, painel do Browser da
IDE; só navegação, nenhum clique com efeito): todas as medições de "Antes e depois" e de "Teclado", e as capturas de tela
a 1440, 1024, 390 e 1920 px (o saldo deixou de sobrepor o botão ao lado a 390 px; a régua de status coube numa linha a
1440 px; conteúdo centralizado em 1600 px a 1920 px). O dev server foi parado ao fim.

**not_run**: Lighthouse; axe a 1024 e 390 px e nas guias internas (só as 8 telas a 1440 px); leitor de tela; navegação completa por teclado nas outras seis telas (só
Painel e Personas, como no critério de aceite); `python scripts/docs-check.py` (sem `python` nesta máquina, e a tarefa não
mexe em `docs/` além do relatório); teste de contraste do tema em alto contraste do sistema (`forced-colors`).

## O que ficou de fora

- Adoção dos estilos de texto nomeados (`.t-*`) nos componentes; os tokens já chegam a todos por `--fs-*`.
- Ampliar os alvos para 40 px em telas de toque.
- Alvos em controles que só aparecem com popover aberto ou no fim de um fluxo de confirmação (o auditor só mede o que
  está renderizado).

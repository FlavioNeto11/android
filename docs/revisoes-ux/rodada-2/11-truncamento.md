# Rodada 2, tarefa 11: texto truncado sem tooltip

Branch `ux2/11-truncamento` (sai de `claude/ux-portal-2`, com a tarefa 14 integrada). Data: 01/10/2026, máquina central
`WIN-7S2UASNLFOP`. Esforço pedido: medium. Nenhuma ação com efeito real: só leitura no navegador contra o backend
simulado do worktree e testes.

## O que mudou

1. **Levantamento reutilizável**: `scripts/ui-truncamento.js` (só DOM, sem dependências). Cola-se no console ou no
   `javascript_tool`; `window.uiTruncamento()` devolve `{ valido, largura, rolagemHorizontal, total, totalConteudo,
   casos, cortesMudos }`. Conta como caso: elemento visível com `text-overflow: ellipsis` e `scrollWidth > clientWidth`,
   ou com `-webkit-line-clamp` e `scrollHeight > clientHeight`, **sem** `title`/`aria-label` próprio, sem `title` em
   ancestral e sem hospedeiro de `Tooltip`/`Popover` do projeto (esses não usam `title`). `aria-label` de ancestral não
   vale (rotula o botão, não devolve o texto cortado). `regiao` separa o cabeçalho (`header`, tarefa 10) do conteúdo;
   `cortesMudos` lista, à parte, o que corta sem reticências. `valido: false` quando não há `<header>` (tela de
   entrada): numa das minhas execuções a sessão caiu e a medição de "0 casos" foi da tela de login; o campo existe para
   isso nunca mais passar por sucesso.
2. **`components/TruncatedText.tsx`** (+ `.module.css`, `.test.tsx`): uma linha com reticências ou `linhas={n}` com
   `line-clamp`; só põe `title` quando o texto realmente transborda (mede com `ResizeObserver`, 1 px de folga, de novo a
   cada mudança de tamanho ou de texto; sem `ResizeObserver` mede no encaixe). `completo` serve quando o texto mostrado
   é uma versão curta do integral: então o `title` é sempre o integral. Hook `useTransborda` exportado.
3. **Infraestrutura (a origem dos casos)**: a linha do aparelho (`.aparelhoMeio`: processo, "API 34 · x86_64 · Play
   Services · renderizador…") **deixou de cortar** e passou a **quebrar em linhas**: o container tem `flex-wrap`, a
   base de 14 rem e `overflow-wrap`; sem espaço ao lado do nome, desce para a linha de baixo. Em até 640 px o aparelho e
   a tarefa/"Aposentar" ficam juntos na primeira linha e o metadado ocupa a segunda, na largura toda (`order`). Escolha
   deliberada de quebrar em todas as larguras e não só no celular: o `title` também não abre em tablet de toque (768 px),
   e o dado decide onde rodar. Saiu a classe `aparelhoComRecusa` (o `flex-wrap` agora é de todo `.aparelho`).
4. **Aplicativos, detalhe do app**: a lista "Execuções" mostrava `r.command` inteiro numa linha cortada. Passou a
   `TruncatedText` com `linhas={2}`, texto = `tituloCurto(r.command).titulo` (o mesmo título curto da lista de
   Execuções, tarefa 05) e `completo={r.command}` (o objetivo inteiro no `title`). Em até 520 px a situação e a idade
   ficam numa linha e o título ocupa a largura toda embaixo (`.runRow` com `flex-wrap`).
5. **Preventivo (não reproduzido nos dados semeados, registrado como tal)**: nome do pacote no cartão do app
   (`Apps.module.css::.appCardTitle code`) e nome/pacote do cabeçalho da Loja (`Loja.module.css::.title strong/code`)
   passaram de reticências para `overflow-wrap: anywhere`: é o dado que distingue dois apps parecidos e não aparece no
   toque. O app citado no título da lista de Execuções (`.runItemApp`, 11 rem) virou `TruncatedText` (ganha `title` ao
   cortar).
6. **Limpeza deixada pela 14**: removida a regra `.command` de `Runs.module.css` (sem nenhuma referência, conferido por
   `grep`).

## Arquivos

Novos: `scripts/ui-truncamento.js`, `frontend/src/components/TruncatedText.tsx`, `TruncatedText.module.css`,
`TruncatedText.test.tsx`, este relatório.

Alterados: `features/infra/Infra.module.css`, `features/infra/InfraPage.tsx` (só a `className` do `li`),
`features/apps/AppsPage.tsx`, `features/apps/Apps.module.css`, `features/loja/Loja.module.css`,
`features/runs/RunsPage.tsx` (só `.runItemApp`), `features/runs/Runs.module.css` (só remove `.command`).

**Toque em arquivo alheio**: nenhum além da posse (infra, apps, runs/lista, loja). Não toquei no resumo da execução
(14), no cabeçalho (10), na persona (12) nem em tokens/sidebar (13).

## Divergências do briefing

- **A linha de base não bate com a do briefing** (build implantado em `83af733`): no backend simulado deste branch,
  com 8 aparelhos semeados (4 do config e 4 dinâmicos, API/ABI/Play preenchidos por `UPDATE` no SQLite do rascunho),
  5 personas vinculadas e 3 execuções, a Infraestrutura deu **8 casos em 390 px e 3 em 768 px** (não 15 e 6). O número
  depende do dado: 15 pede mais aparelhos e personas na tela. O padrão é o mesmo (todos em `.aparelhoMeio`).
- **Aplicativos**: os casos são todos no **detalhe** do app (`#/aplicativos/<id>`), em todas as larguras (2 em 1280 e
  768, 3 em 390), não só em 1280 e 1024 px. A lista de cartões de Aplicativos não teve caso.
- **Execuções**: **0 casos** na linha de base. O título curto + `title` no item (tarefa 05) já cobre a lista, e o que o
  briefing cita do painel de execução ("Guarde o nome do perfil qu…") é o objetivo cortado que a tarefa 14 resolveu
  com o resumo no topo; não repeti. Os casos "isolados em Execuções" do briefing não se reproduziram aqui.
- **Item 4 do briefing** (bloco "Ver objetivo completo" no painel de execução): é da tarefa 14, já entregue
  (`ResumoDaExecucao`); não mexi.
- **Cabeçalho**: no 390 px, o selo "MODO SIMULADO" (33 de 130 px) e o chip do modelo de IA aparecem cortados em toda
  tela; ambos já ficam dentro de `Tooltip`/`Popover`, então o script os dá como cobertos (não são caso). O corte em si
  (rótulo ilegível em celular) é da **tarefa 10**; deixo o registro como repasse. "MODO SIMULADO" só existe com IA
  simulada, não aparece no central real.
- **Axe**: `heading-order` e `page-has-heading-one` (moderate) no detalhe do app e `region` (moderate) na região de avisos
  em todas as telas; a mudança não toca em cabeçalhos nem em regiões, e a `region` é a da linha de base da rodada 1
  (`13-prova-simulada.md`, prova 4). Não comparei os dois primeiros contra o commit de base.

## Antes e depois (linha de base: commit `652cb40` no backend simulado; depois: este branch)

Casos de elipse/clamp sem tooltip no **conteúdo** (cabeçalho à parte, ver acima). Mesma medição, mesmo dado.

| Tela | 390 antes | 390 depois | 768 antes | 768 depois | 1280 antes | 1280 depois |
|---|---|---|---|---|---|---|
| Painel | 0 | 0 | 0 | 0 | 0 | 0 |
| Personas | 0 | 0 | 0 | 0 | 0 | 0 |
| Aplicativos (lista) | 0 | 0 | 0 | 0 | 0 | 0 |
| Aplicativos (detalhe do app) | 3 | 0 | 2 | 0 | 2 | 0 |
| Execuções | 0 | 0 | 0 | 0 | 0 | 0 |
| Pendências | 0 | 0 | 0 | 0 | 0 | 0 |
| Aprendizado | 0 | 0 | 0 | 0 | 0 | 0 |
| **Infraestrutura** | **8** | **0** | **3** | **0** | 0 | 0 |
| Configuração | 0 | 0 | 0 | 0 | 0 | 0 |
| Diagnóstico | 0 | 0 | 0 | 0 | 0 | 0 |

Depois, em 390, 768, 1024, 1280 e 1440 px: as 10 rotas acima mais Painel em lista, Painel com foco no aparelho,
Personas em tabela, Configuração (instâncias e IA) e Aprendizado (falhas) = **16 rotas, 0 casos em cada largura, 0
medições inválidas, 0 rolagem horizontal da página**. Antes, o mesmo conjunto de 16 só foi medido em 1280 px (os extras
deram 0 e o detalhe do app 2); em 390 e 768 a linha de base cobre as 9 telas e o detalhe do app.

## Provas

**real** (01/10/2026, `WIN-7S2UASNLFOP`, worktree em `652cb40` com as mudanças, backend simulado do worktree na 8711,
vite na 5111, aba própria do painel da IDE, aberto por `localhost`): `AI_PROVIDER=simulated`, config e banco no
rascunho, `base_console_port: 5640`, `appium.autostart: false`, SDK inexistente. Semeio: 8 aparelhos (4 por
`INSERT`/`UPDATE` direto no SQLite do rascunho: `api_level`, `abis`, `play_store`, `system_image`, `origin='dynamic'`),
5 personas (3 com conta e aparelho; 2 recusadas pela API por usuário com mais de 30 caracteres), 3 execuções em modo
plano com objetivos de 24 a 220 caracteres.
1. Levantamento por script nas larguras e rotas da tabela; resultados acima.
2. Infraestrutura em 390 px (captura): linha do aparelho com "· API 34 · x86_64 · Play Services · renderizador pedido:
   SwiftShader" inteira em duas linhas, "Aposentar" e "sem tarefa" na primeira; as linhas com persona e a recusa
   continuam abaixo.
3. Detalhe do app em 390 px (captura): situação e idade na primeira linha; título de até 72 caracteres em duas linhas.
4. axe-core 4.13 (wcag2a/aa, 21a/aa, 22aa, best-practice) em Infraestrutura, detalhe do app e Execuções, em 1440 e 390
   px: só `region (moderate)` e, no detalhe do app, `heading-order` e `page-has-heading-one` (moderate); 0
   `color-contrast`, 0 `serious`/`critical`. Rodado antes do último ajuste de CSS do detalhe do app (a faixa de 520 px);
   o levantamento de truncamento foi repetido depois.

**simulated**: `frontend/src/components/TruncatedText.test.tsx` (6): `title` só quando corta, ausente quando cabe,
nova medida quando o elemento muda de tamanho (fake `ResizeObserver`), `line-clamp` por altura, `completo` sempre no
`title`, e sem `ResizeObserver` não quebra. `npm run typecheck` limpo; `npm test` inteiro: **89 arquivos, 1058
testes, todos verdes** (rodado uma vez, no fim, com backend, vite e servidor de scripts parados).

**not_run**:
- Infraestrutura com 15 casos como no central real (outro volume de dados): só os 8 do simulado.
- Tela do aparelho no **toque real** (celular): o painel emula largura e agente de usuário, mas o clique chega como
  mouse; "o `title` não abre em toque" é fato do navegador, não medido aqui.
- Lighthouse e `scripts/ui-verificar.mjs` (este não faz login).
- Leitura do central real (porta 8000): não consultado.
- Verificação visual de todas as telas nos quatro tamanhos: foi por script (0 casos, 0 rolagem horizontal) e capturas
  só da Infraestrutura e do detalhe do app.

## O que ficou de fora

- **Cabeçalho em 390 px** ("MODO SIMULADO" e chip da IA ilegíveis): tarefa 10.
- Ellipses de `devices/*`, `settings/*`, `profiles/*` e `pendencias/*` (fora da minha posse; não deram caso com este
  dado). Têm `title` na maioria; se o central real mostrar casos, o `TruncatedText` e o script servem de base.
- Execuções em andamento (`running`) e conta/persona com dado real: o simulado não alcança.

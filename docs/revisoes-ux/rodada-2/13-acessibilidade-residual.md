# Tarefa 13 (rodada 2): acessibilidade residual

Branch `ux2/13-acessibilidade-residual` (base `claude/ux-portal-2` em `c495b2d`, com as tarefas 10 e 14 integradas).
Feito em 01/10/2026 na máquina de desenvolvimento (Windows), no navegador do painel da IDE contra um **backend simulado**
do worktree (`AI_PROVIDER=simulated`, porta 8713; vite 5113, aberto por `http://[::1]:5113`). Nada foi aberto no central
real e nenhuma ação com efeito real foi disparada (só leitura, marcação local de cartões e troca de modo da caixa de comando).

## Resumo

A maior parte do briefing **já estava no código** (o briefing descreve o build implantado em `83af733`). O que sobrava, e
foi feito:

1. **Alvos de 31,5 px** (meio pixel abaixo do mínimo) nos controles que usam a receita "padding + margem negativa" com
   linha de 13 px: `stateQuick` (Painel), `logResumo`, `aparelhoBtn`, `tarefa`, `personaDoAparelho` (Infraestrutura).
   Padding de 6 para 7 px: 19,5 + 14 = 33,5 px, sem mexer no ritmo das linhas.
2. **Rótulos de eixo do gráfico do Diagnóstico**: de 12 px para 13 px (`--fs-xs`). Além disso o SVG tinha `viewBox` fixo de
   640 px e encolhia em celular (em 390 px o rótulo saía com uns 7 px de verdade): agora o desenho usa a largura do
   contêiner (entre 280 e 640 px) e o texto sai 1:1. Folga lateral dos eixos de 36 para 44 px, para "120s" e "16 GB"
   caberem inteiros em 13 px. `role="img"` saiu do `<figure>` (papel não permitido, axe `aria-allowed-role`) e foi para o
   `<svg>`.
3. **Menu lateral**: `aria-label` explícito em cada item, com a contagem junto no nome quando há
   (`Aprendizado, 3 para aprovar`; o `aria-label` vale no lugar do conteúdo, então sem isso a contagem lida deixaria de
   existir). `aria-current="page"` no ativo já existia.
4. **Retorno de foco do drawer**: já voltava ao cartão e à linha da Lista (Esc e Fechar). Faltava o último degrau: se nem
   quem abriu nem o botão do cartão existem mais, o foco vai ao contêiner do conteúdo (`#conteudo`, via `focarConteudo()`),
   e não ao `<body>`.
5. **Persona chips e opções da prévia** do painel de comando: `min-height` de 26 para `--hit-min` (32 px).
6. **Botão de fechar do aviso (toast)**: 22 px para 32 px (margem negativa devolve o espaço; o aviso não cresce).

## Divergências do briefing (o código manda)

| Briefing | Código no branch |
|---|---|
| "Sidebar só com ícones por padrão" (item 5) | **Já era** expandida por padrão em >= 1280 px, recolhida abaixo, com botão "Recolher menu"/"Expandir menu" e preferência em `localStorage` (`cda.menuRecolhido`, via `lib/storage.ts`, que já tem try/catch em leitura e gravação). Vem de `menuRecolhidoInicial()` em `store/ui.ts`. Só exportei a função e escrevi o teste. |
| "Sidebar recolhida com `font-size: 0`" | A sidebar usa o recurso `sr-only` (clip) e não `font-size: 0`. O único `font-size: 0` do portal é `TopBar.module.css:466 .conn :global(*)` (< 1180 px, conexão como só ícone), arquivo da tarefa 10: **não mexi**. |
| "Gaveta com hambúrguer abaixo de 768 px" | A gaveta vale abaixo de **1024 px** (`MenuLateral.module.css`). |
| "5 alvos < 32 px no Painel, 6 na Infraestrutura" | O auditor próprio (`scripts/ui-auditoria.js`, tolerância de 0,5 px) dá **0** em todas as telas; uma medição estrita (`getBoundingClientRect`, `< 32`) achou **1** no Painel simulado (`stateQuick`, 31,5 px) e 0 na Infraestrutura. Os 5 e 6 do briefing vêm de dados reais (vários contadores de estado no Painel; linhas de aparelho com tarefa em voo e persona na Infraestrutura), que o simulado (4 aparelhos parados, sem execução) não reproduz. Por isso corrigi **todas** as classes que usam a receita de 6 px: o que a medição simulada não alcança fica coberto pela causa comum. |
| "Foco ao fechar o drawer não volta" | Voltava (rodada 1, RF-02). Medi de novo e funciona; só o degrau final era novo. |

## Arquivos tocados

Da posse da tarefa 13:
- `frontend/src/features/topbar/MenuLateral.tsx` (aria-label)
- `frontend/src/features/focus/Drawer.tsx` (degrau final do foco) e `Drawer.test.tsx`
- `frontend/src/features/infra/Infra.module.css` (alvos)
- `frontend/src/features/diagnostics/Diagnostics.module.css` e `MeasurementsChart.tsx` (13 px, largura real, papel do gráfico)
- `frontend/src/features/topbar/TopBar.test.tsx` (teste do aria-label)

**Toque em arquivo alheio** (mínimo, listado como o regulamento pede):
- `frontend/src/features/devices/Devices.module.css`: `.stateQuick` e `.logResumo`, 6 para 7 px.
- `frontend/src/features/command/CommandPanel.module.css`: `min-height` 26 para `--hit-min` em `.personaChip` e `.previaOpcao`.
- `frontend/src/components/overlay.module.css`: `.toastClose` 22 para 32 px.
- `frontend/src/store/ui.ts`: apenas `export` em `menuRecolhidoInicial` (para o teste) e `store/ui.test.ts` (3 testes novos).

`--fs-2xs` (12 px) **não** foi alterado: o token continua em uso pelo selo de pendências do botão Menu
(`TopBar.module.css`, 18 px de altura, tarefa 10). O comentário do token em `tokens.css` não foi reescrito; ele cita o
rótulo de gráfico como exceção, que deixou de existir: sobra só o contador dentro de selo redondo. O orquestrador pode
ajustar esse comentário ao consolidar.

## Antes e depois das medições (backend simulado, 4 aparelhos, 1 persona com conta)

| Medida | Antes | Depois |
|---|---|---|
| Fonte < 13 px, 9 telas, 1440/1024/768/390 px | 3 rótulos de eixo (12 px) só no Diagnóstico | **0** nas 9 telas, nas 4 larguras |
| Rótulo do gráfico em 390 px (tamanho de verdade) | 12 px de CSS, ~6 px na tela (escala ~0,5, estimada pela largura de 324 px do SVG) | 13 px, escala 1,0 |
| Alvos < 32 px, auditor próprio | 0 | 0 |
| Alvos < 32 px, medição estrita, Painel / Infra, 4 larguras | Painel 1 (`stateQuick` 31,5 px) / Infra 0 | **0 / 0**; `stateQuick` 33,5 px; chips de persona 32 px |
| Rolagem horizontal, 9 telas, 4 larguras | nenhuma | nenhuma |
| Contraste e nome acessível (auditor próprio) | 0 / 0 | 0 / 0 |
| Menu em 1440 px, primeira visita | 212 px, expandido (já era) | igual; recolher grava `cda.menuRecolhido=true` e o recarregar mantém recolhido |
| axe WCAG 2.x A/AA + 2.2 AA, Painel/Personas/Infra/Diagnóstico (1440 px) | 0 violações | 0 violações |
| axe + best-practice | Painel 2, Personas 2, Infra 1, Diagnóstico 2 (`aria-allowed-role` + `region`) | Painel 2, Personas 2, Infra 1, Diagnóstico 1 |
| Lighthouse (acessibilidade, desktop) | não medido antes | Painel 100, Personas 99, Infra 100, Diagnóstico 100 |

Restos do axe/Lighthouse, **fora da posse desta tarefa** (não mexi):
- `region` (todas as telas): `ul.toastRegion`, popover de avisos no topo da pilha, fora de landmark (`components/Toasts.tsx`).
- `landmark-unique` (Painel): `section[aria-labelledby="exec-title"]` repete o nome de outro landmark (resumo da execução, tarefa 14).
- `heading-order` (Personas, também no Lighthouse, 99): `h3#grupos-de-acesso` (tarefa 12).
- `label-content-name-mismatch` (Infraestrutura, só Lighthouse; regra experimental do axe, não é WCAG-only): os botões
  `Abrir android-01 na visão de foco` e `Abrir a persona X` têm `aria-label` que não contém o texto visível completo
  (selo de estado, `@usuário`). É da área de Infraestrutura (mais seguro numa tarefa própria, porque testes citam esses nomes).

## Teclado e foco (provas manuais)

Todas com teclas reais pelo painel da IDE (`Tab`, `Return`, `Escape`) no backend simulado:
- **Gaveta, 768 px**: `Enter` no botão Menu abre e o foco entra no item atual (Painel); `Tab` percorre os 9 itens, passa por
  "Fechar menu" e **dá a volta** (preso na gaveta); `Esc` fecha e o foco **volta ao botão Menu** (`#botao-menu`).
- **Menu em 1440 px**: `Tab` percorre Painel, Personas, Aplicativos, Execuções, Pendências, Aprendizado, Infraestrutura,
  Configuração, Diagnóstico, "Expandir menu" e entra no conteúdo (caixa de comando). Ordem lógica; anel de foco presente
  em todos.
- **Barra de seleção do Painel** ("1 selecionado"): `Tab` percorre Criar AVD, Iniciar, Parar, Reiniciar, Mais ações em
  lote e Limpar seleção, todos com 32 px e anel. Não acionei nenhum.
- **Drawer**: `Enter` no botão "Abrir android-02" (visão Cartões) abre; `Esc` fecha e o foco volta ao botão do cartão.
  Na visão Lista: `Enter` em "Abrir android-03", `Tab` até "Fechar", `Enter`: o foco volta ao botão da linha. Dentro do
  drawer a ordem é: painel, Fechar, Assumir controle, seções, "Ver em Infraestrutura"; o teclado fica preso (já provado
  na rodada 1).
- **Elemento sumido**: recarreguei em `#/infraestrutura?foco=android-02` (sem cartão do Painel e sem quem abriu) e `Esc`
  devolveu o foco a `main#conteudo`, não ao `<body>`.
- **Nota sobre o anel**: o anel de foco do portal é `box-shadow` (token `--focus-ring`), não `outline`. Por isso uma
  leitura de `outline-style: none` não indica ausência de anel.

## Provas

**real** (01/10/2026, central de desenvolvimento, base `c495b2d` mais a árvore de trabalho; navegador da IDE contra o
backend simulado da porta 8713):
- medições por script nas 9 telas e 4 larguras (auditor próprio + medição estrita), axe-core 4.13 em 4 telas (1440 px) e
  Lighthouse 13.5 (acessibilidade, com cookie de sessão do backend simulado) em 4 telas;
- todo o roteiro de teclado acima.

**simulated** (vitest):
- `frontend/src/features/focus/Drawer.test.tsx::tarefa 13: sem quem abriu e sem o botão do cartão, o teclado vai ao contêiner do conteúdo, não ao <body>`;
- `frontend/src/features/topbar/TopBar.test.tsx::tarefa 13: cada item tem nome explícito (aria-label), recolhido ou não, e a contagem vai junto no nome`;
- `frontend/src/store/ui.test.ts::menuRecolhidoInicial (padrão por largura, preferência vence)` (3 testes: padrão por largura,
  preferência vence nos dois sentidos, armazenamento que lança);
- `npm run typecheck`: limpo. `npm test` inteiro: **89 arquivos, 1064 testes, todos verdes**.

**not_run**:
- Os 5 alvos do Painel e 6 da Infraestrutura **com dados reais**: não reproduzíveis no simulado; a correção cobre a classe
  inteira de controles, mas a contagem real só se confirma no build implantado.
- Os chips "Por persona" antes da mudança (min-height 26): medidos só depois (32 px).
- Toque em aparelho real (só emulação de viewport).
- Tema claro (o produto é só escuro).
- Capturas de tela comparativas em 1440 e 1024 px: usei medidas de DOM e capturas pontuais (chart em 390 px, painel em
  1440/1024).

## O que ficou de fora

- Os restos do axe/Lighthouse acima (toast, landmark-unique, heading-order, label-content-name-mismatch).
- `.conn { font-size: 0 }` do cabeçalho (tarefa 10) segue como é; o texto fica no DOM e há tooltip.
- Comentário do token `--fs-2xs` em `styles/tokens.css` (cita o gráfico como exceção).
- Selos `seloEstado` de Personas (22 px, `tabindex`): apareceram na medição estrita ingênua, mas o auditor próprio os trata
  como não clicáveis; área da tarefa 12.

## Armadilhas do procedimento

- O escratch da sessão é compartilhado: usei pasta própria (`sim13/`) e um servidor estático em `::1:8743` para injetar
  `axe.min.js` e os scripts de medição por `<script>` (580 KB não cabem no `javascript_tool`). Recarregar a página perde os
  scripts; reinjete.
- O diagnóstico é cacheado: para o gráfico aparecer, semeei 4 linhas `kind='boot'` em `measurements` do banco simulado e
  chamei `GET /api/diagnostics?refresh=1` (leitura local).
- Lighthouse numa tela autenticada: `curl` no `/api/login` do simulado, cookie por `--extra-headers` (arquivo JSON), tudo em
  `::1`. Valores de cookie não foram impressos.
- Processos iniciados (backend 8713, vite 5113, servidor estático 8743) foram parados; a porta 8000 não foi tocada.

# Rodada 2, tarefa 16: correções dos achados da revalidação final

Branch `ux2/16-correcoes-finais`, base `ux2/15-revalidacao-final` (`81e76cf`, que é `claude/ux-portal-2` em `7d4bcc5` mais o
relatório da 15). Data: 01/10/2026, máquina central de desenvolvimento (Windows Server 2025). Escopo pedido: M1, M2, B1,
B3, B4, B2, B7 e B8 de `revalidacao-final.md`. **Não feitos, por pedido**: B5, B6, B9, B10, B11 e os RF abertos (seguem
registrados na 15).

## Resultado por achado

| Achado | Situação | Commit | Prova |
|---|---|---|---|
| M1 caixa "Responda aqui" espremida a 390 px | corrigido | `7384270` | real (navegador, simulado) |
| M2 barra de seleção com ações fora da tela | corrigido (e estendido a 768 px) | `1b12717`, `e6d9b4c` | real + simulated (`BarraDeSelecao.test.tsx` segue verde; CSS não tem teste) |
| B1 nome acessível do menu não começa pelo texto visível | corrigido | `7218e89` | real (Lighthouse) + simulated (`TopBar.test.tsx`) |
| B3 selo da Atividade conta Aprovações, clique abre Interações | corrigido | `4fe1d3d` | simulated (`abas.test.ts`, `ProfileDetail.test.tsx`) |
| B4 "0 de 1 objetivo com sucesso" em execução planejada | corrigido | `1b42239` | real + simulated (`resumo.test.ts`) |
| B2 selos de 12 px sem documentação | corrigido (13 px) | `68db745` | real (medido a 768 px) |
| B7 "aguardando você" x "esperando você" | corrigido (só texto exibido) | `35d6b5f` | simulated (testes atualizados) |
| B8 origem que falha parece número | corrigido | `36de9f9` | real (origem derrubada no navegador) + simulated (4 arquivos de teste) |

## O que mudou e por quê

**M1** (`components/ui.module.css`, `features/command/AssistenteDoComando.module.css`). Reproduzido antes: caixa de 127 px
com `scrollWidth` 116 e 134 em botões de 99 px. A causa era dupla: a ação do Banner ("Editar comando") dividia a linha com
o texto, e o rodapé do assistente deixava os botões lado a lado com `white-space: nowrap`. Até 560 px, as ações de **qualquer**
Banner descem para a linha de baixo (alinhadas à esquerda) e o rodapé do assistente vira coluna, botões na largura toda e
com quebra de linha. Vale para Execuções e para o Painel (o mesmo `RunView`). Mudança no Banner é global de propósito
(a ação ao lado do texto tem o mesmo problema em qualquer banner estreito) e só abaixo de 560 px.

**M2** (`features/painel/BarraDeSelecao.module.css` e o comentário do `.tsx`). Antes: ações de x=394 a 894 em 390 px.
A barra agora **quebra** em qualquer largura em que a linha não cabe; até 900 px o rótulo ocupa a primeira linha e as
ações descem. A nota do estado "sem alvo" também quebra (antes passava de 390 px com `nowrap`). O segundo commit existe
porque a primeira versão só tratava até 560 px e, medindo a 768 px, as ações ainda iam a x=906; o corte passou para 900 px.
O botão desabilitado com motivo continua sendo o mesmo `Button` com `disabledReason` (o motivo vai no nome acessível e no
tooltip). Custo: a barra fica mais alta no celular (132 px com 6 ações, 140 px com a nota longa), presa ao topo da grade.

**B1** (`features/topbar/MenuLateral.tsx`). A causa medida no navegador: para o axe, o texto **visível** do item era
`Pendências1 (1 esperando você)` (o número sem espaço depois do rótulo, mais a legenda repetida num texto só para leitor
de tela que o axe soma ao visível), e o nome `Pendências, 1 esperando você` não o continha. Corrigido nas duas pontas: o
selo não repete mais a legenda escondida e um espaço (sem efeito no layout flex) separa o rótulo do número; o texto visível
passa a `Pendências 1` e o nome a `Pendências, 1 aguardando você`. **Divergência do briefing**: o exemplo do pedido
(`Pendências, 4 aguardando você`) é exatamente o formato adotado; a sugestão do relatório 15 (`Pendências 4, …`) não era
necessária, porque o axe tira a pontuação antes de comparar. O `aria-label` explícito (exigido pelo teste da tarefa 13,
que garante nome com o menu recolhido) foi mantido.

**B3** (`features/profiles/abas.ts`, `ProfileDetail.tsx`). Escolha: **o clique passa a abrir a guia que o número conta**, e
não o selo a contar Interações (o número de Aprovações é o que pede decisão, e Interações não tem nada para decidir).
`abaAoEscolherSecao(secao, aprovacoesPendentes)` devolve `aprovacoes` para a seção Atividade quando há pendência; sem
pendência abre a primeira guia (Interações), como antes. Vale nos botões e na lista suspensa (celular). O botão ganha nome
`Atividade, 1 aprovação aguardando você` (começa pelo texto visível `Atividade 1`), o `title` do selo diz o mesmo, e a opção
da lista suspensa deixou de dizer "para decidir" (vago) e diz "aprovação aguardando você". Quem já está em Interações e clica
em "Atividade" não é movido (o botão da seção atual segue sem efeito).

**B4** (`features/runs/resumo.ts`). `objetivosComSucesso` devolve `null` em `planning`, `planned` e `needs_input` (nada foi
executado). Em andamento e terminada continua mostrando "0 de 1 objetivo com sucesso", que aí é informação.

**B2** (`TopBar.module.css`, `Profiles.module.css`, `styles/tokens.css`). Escolhida a opção preferida: os dois selos vão a
`--fs-xs` (13 px) em vez de documentar a exceção. O comentário do token agora diz que `--fs-2xs` (12 px) é só do rótulo de
gráfico. Não toquei `07-tipografia-a11y.md` (é da rodada 1 e, sem exceção nova, não precisa de linha nova).

**B7** (`SaudeAmbiente.tsx`, `PendenciasPage.tsx`, `ParaAprovarTab.tsx`, `MenuLateral.tsx`, `ProfileDetail.tsx`). Unificado em
"aguardando você" **só no que a tela mostra**: popover do semáforo, título da caixa vazia ("Nada aguardando você"), lead da
caixa, textos do Aprendizado › Para aprovar, nome do menu e selo da Atividade. Ficam de fora os comentários de código e o
"esperando há 3 dias" (tempo de espera, outra frase).

**B8** (`features/pendencias/exibicao.ts` novo, `store.ts`, `usePendencias.ts`, `TopBar.tsx`, `SaudeAmbiente.tsx`,
`MenuLateral.tsx`, `PendenciasPage.tsx`). O store guarda qual leitura falhou (`falhas`). Com uma origem fora, o total deixa
de parecer certo: o valor é `4+` (piso) ou `?` quando nada foi contado, **nunca um número inventado**. Aparece no contador
do topo (com o motivo em texto para leitor de tela e no tooltip), no selo do botão Menu e no nome do "Resumo" (celular), no
item Pendências do menu (o Aprendizado usa a falha da fila dele), no popover do semáforo e nos chips da caixa (`Todas (3+)`,
`Aprendizado (?)`; cada chip usa a falha da leitura da sua origem). Sem falha nada muda: o número sai sem "+" e sem aviso.
Limite conhecido: antes da primeira leitura (`carregado` falso) o total ainda é só o das execuções; isso é carregamento,
não falha, e já é coberto pelo esqueleto da caixa.

## Provas

### real (01/10/2026, central de desenvolvimento, commits deste branch)

- **Backend simulado do worktree** na **8716** (`AI_PROVIDER=simulated`, `POC_CONFIG` e `POC_DB_PATH` no rascunho da
  sessão, `base_console_port: 5640`, `appium.autostart: false`, SDK inexistente, 6 aparelhos locais) e **vite** na **5116**
  com `VITE_API_TARGET=http://127.0.0.1:8716`, aberto só por `http://localhost:5116`. Painel da IDE, aba própria (`tab-9`),
  sempre com `tabId`, fechada ao fim com a emulação de tamanho desfeita. Semeado pelas rotas do simulado: login "Revisao 16",
  2 execuções em modo plano (uma `needs_input` "Envie uma mensagem de bom dia", uma `planned`) e um servidor remoto que
  conectou e caiu (`POST /api/workers/enroll`, `hello` pelo `/api/worker/ws` com 2 aparelhos, `POST …/devices/adopt`:
  `android-13` e `android-14` ficam "desconhecido"). Nada com efeito foi disparado (só navegação e marcar caixas de seleção).
- **M1 antes/depois** (`#/execucoes/<needs_input>` e `#/painel`, 390 px): caixa 127 → **270 px**; botões 101 → **244 px**;
  campos de resposta 73 → **216 px**; nenhum botão com `scrollWidth > clientWidth`; rolagem horizontal 0. A 768 px 471 px e a
  1440 px 571 px (botões 156, 191 e 90 px), sem corte.
- **M2 antes/depois** (`#/painel?visao=lista`, android-01 e android-13 marcados): a 390 px as ações iam de x=394 a 894, rolagem
  de 891 px em 364; agora x=25 a 282, `scrollWidth` 364 = largura (sem rolagem), barra com 132 px. A 768 px: antes até x=906
  (rolagem de 891 em 710); agora até x=539, 73 px de altura, sem rolagem. A 1440 px: uma linha de 46 px, até x=1118, sem mudança.
  Só o android-13 marcado: nota longa quebrada, botão "Limpar seleção" dentro dos 390 px.
- **B1** Lighthouse 13.5.0 (acessibilidade, preset desktop, Chrome headless, cookie da sessão simulada por `--extra-headers`
  num arquivo apagado em seguida) em `#/painel` com 1 pendência: **antes** `label-content-name-mismatch` falhava no item
  Pendências (`aria-label="Pendências, 1 esperando você"`); **depois** nenhuma auditoria falha, nota 1,00. Axe 4.13
  (`label-content-name-mismatch` ligada à mão) no item: 1 aprovado, 0 violações; o texto visível calculado pelo axe passou
  de `Pendências1` para `Pendências 1`.
- **B8** com `fetch` de `/api/aprendizado/pendentes` forçado a 500 no navegador, `#/pendencias`: item do menu
  `aria-label="Pendências, 1 ou mais aguardando você; alguma origem não carregou"`, texto visível `Pendências 1+`; botão Menu
  `1+`; chips `Todas (1+)`, `Aprendizado (?)`, `Persona (0)`, `Execução (1)`, `Intervenção (0)`; o aviso da caixa segue.
- **B4** no navegador: a execução planejada mostra "Plano pronto. Nada foi executado ainda", sem a contagem de objetivos.
- **B2** a 768 px: selo do botão Menu com `font-size: 13px`, 18 x 18 px, sem corte.
- **axe 4.13** (`wcag2a/aa`, `wcag21a/aa`, `wcag22aa`, `best-practice`) nas telas afetadas a 1440 px (Painel, Personas, Pendências,
  detalhe de execução planejada), no detalhe `needs_input` a 1440, 768 e 390 px e no Painel com a barra de seleção a 390 px:
  só `region` (toast, RF-27) e, em Personas, `heading-order` (RF-27), os dois já abertos na 15. Nenhuma violação nova.
- Os scripts de semeio, config, banco, cookie e relatórios do Lighthouse ficaram só no rascunho da sessão, fora do repositório, e foram apagados ao fim.
- Ao fim: backend (8716), vite (5116) e servidor estático do axe (8746) parados; **a 8000 nunca foi tocada** (dono PID 22060).

### simulated

- `npm run typecheck`: limpo.
- `npm test` inteiro, uma vez, no fim, com tudo parado: **94 arquivos, 1133 testes, todos verdes** (a 15 tinha 93 e 1120:
  +1 arquivo, `exibicao.test.ts`, e +13 testes).
- Testes novos ou alterados, por achado: B1 `topbar/TopBar.test.tsx` (nome contém o texto visível; **falhava antes** com
  `Aprendizado3 (3 para aprovar)`, conferido desfazendo só o `MenuLateral.tsx`); B3 `profiles/abas.test.ts` e
  `profiles/ProfileDetail.test.tsx` (com e sem aprovação pendente); B4 `runs/resumo.test.ts`; B7 `topbar/SaudeAmbiente.test.tsx`,
  `pendencias/PendenciasPage.test.tsx`, `pendencias/contagem.test.tsx`; B8 `pendencias/exibicao.test.ts`,
  `pendencias/contagem.test.tsx` (origem falhando e caso sem falha), `topbar/TopBarCompacto.test.tsx` (botão Menu e Resumo) e
  `topbar/SaudeAmbiente.test.tsx` (popover). M1, M2 e B2 são CSS puro: o jsdom não calcula layout, por isso a prova deles é a do
  navegador acima.

### not_run

- O selo da seção Atividade (B2, B3) com uma aprovação pendente de verdade no navegador: o simulado não tem aprovação semeada
  pelas rotas (a 15 usou `INSERT` no SQLite); fica coberto por `ProfileDetail.test.tsx` e pela mudança de 1 linha de CSS.
- Lighthouse nas demais telas e larguras (rodado só em `#/painel` a 1350 px de largura, o desktop padrão); o conjunto de 9 telas
  x 6 larguras da 15 não foi repetido: foram medidas as telas afetadas por esta tarefa.
- axe na barra de seleção a 768 e 1440 px (medida só pelo DOM nessas larguras; o axe rodou a 390 px) e no detalhe `needs_input`
  do Painel a 768 e 1440 px.
- Leitor de tela e toque real.

## Divergências e observações

- Usei `git stash push -- <arquivo>` e `git stash pop` uma única vez, por segundos, para provar que o teste do B1 falhava sem a
  correção. A regra pede não usar `stash` sem tag; a árvore voltou intacta (conferido por `git status`) e nada ficou guardado.
- O axe injetado no navegador da IDE **não** reproduzia o B1 com o menu recolhido (o rótulo fica fora da vista e o texto
  visível é só o número) nem com a versão antiga do selo; o Lighthouse reproduziu de primeira, e foi ele que serviu de
  referência. Para reproduzir com o axe é preciso expandir o menu (`Expandir menu`).
- O ajuste da barra de seleção ficou em dois commits porque a primeira medição foi só a 390 px; a 768 px ainda havia ação fora
  da tela (o relatório 15 dizia "390 px", mas o problema existe enquanto a linha não couber).

## Arquivos tocados

Código (todos em `frontend/src`):
`components/ui.module.css` (Banner, M1 · toque em arquivo alheio ao mapa de posse, a menor mudança possível),
`features/command/AssistenteDoComando.module.css` (M1),
`features/painel/BarraDeSelecao.module.css`, `features/painel/BarraDeSelecao.tsx` (M2),
`features/topbar/MenuLateral.tsx`, `features/topbar/TopBar.tsx`, `features/topbar/SaudeAmbiente.tsx`,
`features/topbar/TopBar.module.css` (B1, B2, B7, B8),
`features/profiles/abas.ts`, `features/profiles/ProfileDetail.tsx`, `features/profiles/Profiles.module.css` (B3, B2, B7),
`features/runs/resumo.ts` (B4),
`features/pendencias/exibicao.ts` (novo), `features/pendencias/store.ts`, `features/pendencias/usePendencias.ts`,
`features/pendencias/PendenciasPage.tsx` (B7, B8),
`features/aprendizado/ParaAprovarTab.tsx` (B7),
`styles/tokens.css` (comentário do token, B2).

Testes: `features/topbar/TopBar.test.tsx`, `TopBarCompacto.test.tsx`, `SaudeAmbiente.test.tsx`;
`features/pendencias/PendenciasPage.test.tsx`, `contagem.test.tsx`, `exibicao.test.ts` (novo);
`features/profiles/abas.test.ts`, `ProfileDetail.test.tsx`; `features/runs/resumo.test.ts`.

Docs: este relatório. Não toquei `CHANGELOG.md`, `docs/estado-atual.md` nem ADR.

## Fora desta tarefa (continuam abertos)

B5 (`regiao()` do `ui-truncamento.js`), B6 (`ResumoDaExecucao` mede o corte por conta própria), B9 (homônimos com o mesmo nome
acessível na lista), B10 (blocos da guia Persona com 31,5 px), B11 (referência crua `licao:li-…`) e os RF abertos da lista da 15.

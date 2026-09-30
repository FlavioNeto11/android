# Tarefa 03: barra de ação em massa e drawer de foco (relatório)

Branch `ux/03-barra-drawer` (sai de `claude/ux-portal`, `c2c26cb`, que já trazia as tarefas 01 e 02). Data: 30/09/2026.
Máquina: central (Windows Server 2025). Modelo: Sonnet 5.5, esforço high.

## O que mudou

1. **Barra de seleção presa ao topo da grade** (`features/painel/BarraDeSelecao.tsx`, novo). Sai a barra flutuante do
   rodapé, que cobria os cartões. A barra é uma linha `position: sticky; top: 0` na fila normal do documento, logo
   abaixo do cabeçalho da seção "Aparelhos", e só existe com aparelhos marcados. Mostra "N selecionados" (vem de
   `contarSelecao`, da tarefa 02, sem recontar), **Iniciar, Parar, Reiniciar** (e "Criar AVD" ou "Acordar" quando a
   seleção precisa deles) e um menu **Mais ações**: Hibernar, Instalar app…, Abrir app… e, sozinho numa **Zona de
   perigo**, Resetar dados… (a confirmação com a lista dos aparelhos é a mesma de antes). Verbo que algum aparelho da
   seleção não aceita continua aparecendo desabilitado, com quem o impede, na barra ou no menu.
   Em tela estreita a linha **rola para o lado** (não quebra nem cresce).
2. **Drawer de Foco sobreposto** (`features/focus/Drawer.tsx`, novo, usado por `FocusPanel`). O painel deixou de ser
   irmão flex do `main` e virou `position: absolute` ancorado em `.body` (o `App.module.css` ganhou `position:
   relative` ali). Abrir ou fechar **não muda a largura do conteúdo nem a grade**. Largura 720 px a 1000 px
   (`clamp(720px, 52vw, 1000px)`, nunca mais que a área), com fundo escurecido (só visual, `pointer-events: none`).
   Abaixo de 720 px continua tela cheia.
   - Fecha com **Esc**, com o **clique na página** (o clique só fecha: é engolido e não age sobre o que estava embaixo) e
     com o botão Fechar/Voltar. O **menu lateral, os cartões de aparelho, o topo, popovers e avisos seguem clicáveis**
     com o drawer aberto: o link do menu leva o `?foco=` junto para a outra tela, e um clique em outro cartão troca de
     aparelho. Esc dentro de campo de texto, de um
     popover (`div[role=dialog]` em portal, que antes fechava o drawer junto) ou de uma caixa `<dialog>` fecha só aquilo.
   - `aria-modal="true"` e o teclado **preso dentro** (Tab no último vai ao primeiro, Shift+Tab no primeiro vai ao
     último). O corpo de uma seção `<details>` fechada não conta (a Hierarquia é a última e fica fechada; o Chromium
     devolve retângulos para esse corpo, então só `getClientRects` não bastava).
   - Ao fechar, o teclado **volta ao botão do cartão** que abriu (ou, se ele sumiu, ao "Abrir …" do cartão pelo seletor).
3. **Legendas do preview sem corte** (`Focus.module.css`): "Prévia suspensa — último frame há …", "Desatualizado (…)" e
   "Somente visualização — assuma o controle para interagir" perderam o `white-space: nowrap`, quebram em até duas linhas,
   com `max-width` e raio de canto menor.
4. **Barra escondida com o drawer aberto**: a barra mantém o **lugar e o contador** ("N selecionados" mais "As ações
   deste aparelho estão no painel de foco.") mas **some com os botões e com o `role=toolbar`**. Assim nenhuma ação
   aparece duas vezes e a grade não pula quando o drawer abre.
5. **Comando** (`CommandPanel.tsx`): o link "escolher manualmente" virou um **controle segmentado Automático | Manual**
   (sempre um marcado; voltar ao Manual reabre o último jeito usado; dentro do Manual ficam os três chips de antes).
   **Refinar com IA → Planejar → Executar** viraram um grupo de **etapas numeradas (1, 2, 3)**, com uma legenda curta
   do que cada uma faz (Planejar só mostra o plano, sem mexer nos aparelhos). O aviso de motivo solto ao lado dos
   botões saiu: o motivo de cada bloqueio fica **no próprio botão** (tooltip ao passar o mouse ou focar, e dentro do
   nome acessível).

## Decisões e divergências do briefing

- **Item 4 (esconder a barra com o drawer aberto).** Não removo a barra do DOM: manter a faixa (só com o contador)
  evita que a grade suba e desça quando o drawer abre, e o critério "abrir o drawer não altera o layout da grade"
  vale também para a barra. Nenhum botão da barra existe com o drawer aberto.
- **Item 6 (motivo do Executar em tooltip).** O componente `Button` já mostrava `disabledReason` como tooltip e no
  nome acessível, e `Executar` já recebia o motivo. O que estava "distante" era o aviso amarelo ao lado. Removi o
  aviso (e `aria-describedby` do campo, que apontava para ele) e deixei só o do botão; o teste confere que o motivo
  aparece no botão e mais nenhum elemento o repete.
- **Item 7 (nome da segunda posição).** Ficou "Manual", não "Escolher manualmente": o painel "Quem faz e onde"
  (`SugestaoDeAlvos`) já tem um botão com esse nome, e dois botões iguais na mesma tela confundem leitor de tela e
  teste. O rótulo do grupo diz "Quem escolhe os aparelhos".
- **Clicar fora fecha, mas o menu e os cartões continuam vivos.** A primeira versão pôs um fundo que pegava o ponteiro e
  cobria o menu lateral: com o drawer aberto, clicar em "Personas" só fechava o Foco, e o `?foco=` que a tarefa 01
  leva entre telas deixava de ter caminho (a partir de 1024 px, onde o menu é coluna). Corrigi: o fundo só escurece, e
  o "fora" é o `main` do App menos os cartões (`Drawer.tsx`, escuta de clique em captura). Menu, cartões, topo e
  popovers ficam de fora dessa regra. O clique na página é engolido, o que também evita um `history.back()` pendente
  seguido de uma navegação reabrir o Foco.
- **`aria-modal="true"`, mesmo com o menu clicável.** O drawer é a única superfície alcançável pelo **teclado**
  (o Tab fica preso), então para leitor de tela e teclado ele é modal. O ponteiro é mais generoso de propósito, porque
  o Foco acompanha o usuário entre telas. Se o dono preferir um modal estrito, é tirar o menu e os cartões da exceção.
- **Foco do teclado ao fechar já existia em parte** (`store/ui.ts::focusOpener`, que só vale quando o Foco abre por
  `openFocus`). O `Drawer` cobre também o link colado (sem `opener`) pelo seletor do cartão. Os dois rodam; a ação
  repetida é inofensiva. Não mexi no store.
- **`--focus-panel-w`** (token em `styles/tokens.css`, tarefa 07) ficou sem uso: a largura do drawer mora em
  `Focus.module.css` para esta tarefa não tocar os tokens. A tarefa 07 pode apagar o token.
- **Efeito de rolagem do `App.tsx`.** Existia um `ResizeObserver` que reposicionava o cartão clicado depois que o
  drawer comprimia o conteúdo (o "scroll que quebra"). Sem reflow, a premissa acabou; troquei por um único
  `scrollIntoView({ block: 'nearest' })` (útil só para link colado com `?foco=` fora da vista).
- **Quem é dono do quê.** "Criar AVD" e "Acordar" ficam à vista quando a seleção precisa deles (são o motivo de a
  pessoa estar ali). Só Hibernar, Instalar e Abrir app foram para o menu, como pede o briefing.

## O que o drawer sobreposto resolve (nota da tarefa 01)

A tarefa 01 registrou que, a 1024 px com o menu expandido e o Foco aberto, sobravam ~212 px de conteúdo. Agora o Foco
**sobrepõe**. Medido (real, abaixo): a 1024 px com o menu expandido (212 px), o `main` segue com **802 px** com o
drawer aberto ou fechado. O custo é que o drawer cobre a parte direita da página (e a escurece), em vez de comprimi-la.

## Provas

**real** (30/09/2026, ~20:10 a 20:16Z, máquina central; vite na 5193 com `VITE_API_TARGET=http://127.0.0.1:8000`,
painel do navegador da IDE, aba própria `tab-2`; somente leitura: marcar aparelhos, abrir o Foco, abrir o menu "Mais
ações", Esc, clique no fundo; **nenhum** Iniciar/Parar/Reiniciar/Resetar/Instalar/Executar/Assumir controle foi
disparado, e o backend vivo não recebeu escrita). Commit testado: a base `820170e`; o clique fora corrigido foi medido depois, no commit seguinte.
- **Layout idêntico com o drawer aberto e fechado**: os retângulos dos 15 cartões (esquerda, topo, largura, altura) e
  a largura do `main` são iguais antes e depois de abrir o Foco, a 1440 (`main` 1370), 1024 com o menu expandido
  (`main` 802, menu 212) e 768 (`main` 758). A 390 px o drawer é tela cheia (sem comparação de retângulos).
  `scrollWidth` do documento igual à largura nos quatro tamanhos.
- **Barra**: presa ao topo do `main` ao rolar (`top` 97 = `top` do `main`, `position: sticky`; ao rolar, os cartões
  passam por baixo dela, como em qualquer barra fixa). Em repouso, na fila normal, acima do primeiro cartão (a 390
  px: barra termina em 465 px, primeiro cartão começa em 477 px). Altura 46 px em todos os tamanhos (uma linha); a 390
  px `scrollWidth` 526 contra 364 visíveis, ou seja, a linha rola.
- **Menu "Mais ações"** a 1440: Hibernar, Instalar app…, Abrir app…, "Zona de perigo", Resetar dados… (não cliquei).
- **Drawer**: `aria-modal="true"`, recebe o foco ao abrir, larguras 749 (1440), 720 (1024 e 768), tela cheia em 390;
  Esc fecha (`#/painel?foco=android-01` volta a `#/painel` pelo histórico) e o foco volta a "Abrir android-01 na
  visão de foco". **Com clique real** (ferramenta de mouse do painel, a 1024 px, menu expandido): com o Foco aberto,
  clicar em "Infraestrutura" no menu foi a `#/infraestrutura?foco=android-01` com o drawer ainda presente
  ("Visão de foco: android-01"); clicar em "Personas" idem (`#/personas?foco=android-01`); clicar na página (à esquerda
  do drawer) fechou (`#/personas`); e "Abrir android-02 na visão de foco" com o drawer de android-01 aberto trocou
  para `#/painel?foco=android-02` (clique sintético de JS no cartão).
- **Legendas**: a 1440, 1024, 768 e 390 nenhuma legenda do preview tem `scrollWidth > clientWidth` (sem corte);
  "Somente visualização — assuma o controle para interagir" ocupa duas linhas (43 px) e "Prévia suspensa — último
  frame há …" uma (26 px) onde cabe, duas em 1024/768 (43 px); a tela (phone) mede 340 px a 1440, 311 px a 1024.
- **Tab preso**: no navegador, Tab no último elemento focável (o resumo "Hierarquia", depois de excluir o corpo da
  seção fechada) foi ao primeiro ("Fechar"), e Shift+Tab no primeiro voltou ao último (teclas sintéticas com
  `dispatchEvent`; o Tab real do teclado é tratado pelo mesmo manipulador).

**simulated** (`vitest`, backend falso):
- `npm run typecheck` verde. `npm test` inteiro: **79 arquivos, 920 testes, todos verdes** (30/09, ~20:24Z; a base
  da tarefa 01 tinha 75 arquivos e 873).
- `features/painel/BarraDeSelecao.test.tsx` (7): à vista só rotina e o resto em "Mais ações"; Resetar dados só na
  Zona de perigo, com a confirmação e sem envio ao cancelar; instalar app troca o conteúdo do mesmo menu e leva o
  `app_id` ao lote; abrir app e "Voltar"; verbo que algum aparelho não aceita desabilitado com o motivo (barra e
  menu); com o Foco aberto sem botões nem `toolbar`; lote em andamento desabilita o resto.
- `features/focus/Drawer.test.tsx` (10): diálogo modal e foco no painel; Esc e clique no fundo fecham; o clique na página fecha e é engolido, e menu, cartões e topo seguem clicáveis; Esc em campo,
  popover e `<dialog>` não fecham; Tab e Shift+Tab presos; corpo de `<details>` fechado fora da lista; o teclado volta
  a quem abriu e, se ele sumiu, ao botão do cartão; `FocusPanel` fecha por Esc e pelo fundo, e o `?foco=` sai da URL.
- `features/command/CommandPanel.test.tsx::Comando — controle segmentado e etapas` (3): Automático | Manual sempre com
  um marcado e o último modo manual lembrado; etapas na ordem Refinar, Planejar, Executar; motivo de Executar no
  próprio botão, uma vez por botão, sem aviso solto, e tooltip ao focar.
- `app.integration.test.tsx::seleciona com caixa, Ctrl+clique e Shift+clique…` (atualizado): a barra vem antes da grade
  no documento, Hibernar só no menu, reset só na zona de perigo, e o lote de "Parar" segue com a mesma chave de
  idempotência. `SugestaoDeAlvos.test.tsx` (uma asserção): o controle Manual, em vez do link antigo.

**not_run**: leitor de tela real; Tab físico (só teclas sintéticas); navegadores que não sejam o Chromium do painel da
IDE; 1920 px; disparo real de qualquer ação em lote (por regra); verificação do clique do usuário real com o Popover
"Mais ações" aberto por cima do drawer (não há essa combinação: a barra some com o Foco aberto).

## Toques em arquivo alheio (mínimos)

- `frontend/src/App.module.css` (tarefa 01): `.body { position: relative }`, âncora do drawer.
- `frontend/src/App.tsx` (tarefa 01): o efeito de rolagem do Foco (ResizeObserver) virou um `scrollIntoView` único.
- `frontend/src/features/devices/DeviceGrid.tsx` e `Devices.module.css` (tarefa 04): só a barra. O `BulkBar` e seu CSS
  (`.bulkDock`, `.bulk`, `.bulkLabel`, `.bulkSep`) saíram; entrou `<BarraDeSelecao />` e o contador "N de T
  selecionados" do cabeçalho some quando há seleção (a barra o mostra), ficando só "0 de T selecionados".
- `frontend/src/app.integration.test.tsx` (compartilhado): o teste da barra em lote.
- `docs/produto.md`: o fluxo "escolher manualmente" e um item sobre seleção em massa e Foco.

## Arquivos alterados

- Novos: `frontend/src/features/painel/BarraDeSelecao.tsx`, `.module.css`, `.test.tsx`;
  `frontend/src/features/focus/Drawer.tsx`, `Drawer.test.tsx`.
- Alterados: `features/focus/FocusPanel.tsx`, `Focus.module.css`; `features/command/CommandPanel.tsx`,
  `CommandPanel.module.css`, `CommandPanel.test.tsx`, `SugestaoDeAlvos.test.tsx`; os toques listados acima.

## O que ficou de fora e pendências

- Cabeçalho da grade: o botão "Limpar" segue ali e na barra (ícone "Limpar seleção"), porque são lugares diferentes
  da leitura (um limpa, o outro também); se a tarefa 04 quiser um só, é tirar o do cabeçalho.
- O fundo escurecido também cobre o menu lateral (escuro, mas clicável). Se a tarefa 07 quiser o menu sem escurecer,
  é subir o `nav` acima de `--z-panel` (não feito, para não tocar o `MenuLateral` da tarefa 01).
- `--focus-panel-w` sem uso (tarefa 07 pode apagar).
- Ctrl+Enter no Comando com o botão bloqueado não faz nada e não avisa; o motivo agora só aparece no botão.

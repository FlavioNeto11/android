# Tarefa 01: menu de navegação e rotas por objeto (relatório)

Branch `ux/01-rotas-menu` (sai de `claude/ux-portal`). Data: 30/09/2026. Máquina: central (Windows Server 2025).
Modelo: Opus 5.5, esforço high.

## O que mudou

1. **Menu lateral.** As oito seções saíram da faixa horizontal do topo e foram para uma coluna à esquerda do
   conteúdo (`features/topbar/MenuLateral.tsx`). A coluna mostra ícone e rótulo e pode ser recolhida para só o
   ícone. A escolha fica lembrada no navegador (`cda.menuRecolhido`); sem escolha, ela começa recolhida abaixo de
   1280 px. Abaixo de 1024 px a coluna vira gaveta, aberta pelo botão "Menu" (hambúrguer) do topo: Esc fecha, clicar
   fora fecha, e o teclado entra no item atual ao abrir e volta ao botão ao fechar. O item ativo tem
   `aria-current="page"` e uma barra à esquerda, para não depender só da cor. O foco visível é o anel global do
   `base.css`. A contagem "Para aprovar" do Aprendizado veio junto; recolhido, ela vira selo no ícone.
2. **O topo** ficou com botão do menu (abaixo de 1024 px), marca, IA, conexão e operador. Na faixa de baixo seguem
   saúde e indicadores, sem mudança. Saíram a medição de transbordo (`transbordoDe`/`useTransbordoHorizontal`) e o
   gradiente, que não servem mais.
3. **Rotas por objeto.** A URL passou a dizer onde a pessoa está (esquema abaixo). O store de UI (`store/ui.ts`)
   espelha a rota (`rota`, `view`, `focusInstanceId`, `selectedRunId`) e oferece as ações que a mudam: `navegar`,
   `trocarQuery`, `voltarPara`, `abrirExecucao`, além das antigas `setView`, `selectRun`, `openFocus`,
   `closeFocus` e `openPersona`.
4. **Personas.** O nome interno da tela `'perfis'` virou `'personas'` em todo lugar. `#/perfis[...]` é reescrito
   para `#/personas[...]` com replaceState, sem empilhar e preservando objeto, guia e parâmetros. O valor `'perfis'`
   gravado antes no `localStorage` também abre Personas.

## Decisão: sidebar em vez de "Mais ▾"

- São oito telas e a Pendências da tarefa 06 vem aí. Uma lista vertical comporta todas sem medir largura. "Mais"
  esconderia justo as de baixo (Configuração, Diagnóstico) atrás de um clique a 1440 px, a largura mais usada.
- A faixa do topo fica para o que também disputa largura (IA, saldo, conexão, operador), que já encolhia antes de
  1180 px.
- Abaixo de 1024 px a mesma lista vira gaveta: é um componente e um CSS, sem JS de medição. Recolhida (60 px), a
  coluna custa pouco ao lado do Foco (600 px ou mais).

## Esquema de rotas (contrato em `frontend/src/lib/rotas.ts`)

| Onde | Hash | Histórico |
|---|---|---|
| Tela | `#/painel`, `#/personas`, `#/aplicativos`, `#/execucoes`, `#/aprendizado`, `#/infraestrutura`, `#/configuracao`, `#/diagnostico` | link do menu: empilha |
| Persona aberta | `#/personas/<id>` (Visão geral) | abrir: empilha |
| Guia da persona | `#/personas/<id>/<guia>`: `persona`, `contas`, `imagens`, `aparelhos`, `memoria`, `interacoes`, `habilidades`, `aprovacoes`, `execucoes`, `config` | trocar de guia: substitui |
| Execução | `#/execucoes/<id>` | clique na lista: empilha; seleção automática ou execução recém-criada: substitui |
| Guia da execução | `?aba=` `plano`, `aparelhos`, `textos`, `linha-do-tempo`, `evidencias`, `decisoes`, `relatorio` (a guia padrão do estado não entra no link) | substitui |
| Aplicativo aberto | `#/aplicativos/<app_id>` (abre em "Por app") | abrir: empilha |
| Guia de Aplicativos | `#/aplicativos?aba=apps\|versoes\|rede\|proxy` (Loja é a padrão, sem `aba`) | substitui |
| Aparelho em Foco | `?foco=<id>` em **qualquer** tela, levado junto ao trocar de tela | abrir: empilha; trocar de aparelho com o painel aberto: substitui |
| Filtros de lista | Personas: `situacao`, `q`, `ordem`, `visao`; Painel: `estado` | aceitos e preservados (ninguém lê ainda: tarefas 02 e 05) |

**Fechar sem empilhar.** Fechar o Foco, "← Personas" e "← Aplicativos" usam `voltarPara`. Quando a entrada atual
foi empilhada pelo portal e a anterior é o destino, chamam `history.back()`; a tela responde na hora e o `popstate`
seguinte é ignorado por idempotência. Quando não é (link colado, recarga), o Foco substitui a entrada e os botões
"voltar à lista" empilham a lista. O critério é a função pura `podeVoltarPara(state, alvo, aceita?)`, testada. No
`history.state` gravamos `{ cda: 1, de: <hash anterior> }` a cada push. Substituir preserva o `state`.

**Hash desconhecido** (`#/nada`): volta à rota atual com replace. **`#/execucoes` sem id** com execução já
selecionada: vira `#/execucoes/<id>` com replace.

## O que passou a estar na URL (antes não estava)

- Persona aberta e guia dela (antes eram estado local de `ProfilesPage`, mais o pedido `personaRequest` no store).
- Aparelho em Foco (antes `focusInstanceId`, só no store).
- Execução selecionada na tela Execuções e a guia dela (antes `selectedRunId` no store e `localStorage`, guia local).
- Aplicativo aberto e guia de Aplicativos (antes estado local).
- Continuam **fora** da URL: a guia de Configuração (`localStorage`, tarefa 06), a guia do Aprendizado
  (`localStorage`, tarefa 06), a guia do detalhe de servidor em Infraestrutura (tarefa 02) e a seleção de aparelhos
  do Painel (é seleção de trabalho, não lugar). Para ligar uma guia à URL basta
  `useUiStore((s) => s.rota.query.aba)` para ler e `trocarQuery({ aba })` para gravar.

## Mudanças de API (para as tarefas seguintes)

- `View` agora é `Tela` (de `lib/rotas.ts`) e `'perfis'` não existe mais. `VIEWS === TELAS`. `isView`,
  `viewFromHash` e `hashForView` continuam, implementados sobre `parseHash`/`hashDe`.
- **Removidos** `personaRequest` e `consumePersonaRequest`. `openPersona(id, tab?)` agora navega para
  `#/personas/<id>[/<tab>]` (push), e quem quer saber qual persona está aberta lê `rota.segmentos`.
- **Novos** no store: `rota`, `menuRecolhido`, `menuAberto`, `navegar(destino, modo?)`,
  `trocarQuery(parcial, modo?)`, `voltarPara(destino, modo, aceita?)`, `abrirExecucao(id)`, `setMenuRecolhido`,
  `setMenuAberto`. **Exportados**: `aplicarHash(forcar?)`, `podeVoltarPara`, `PARAM_FOCO`.
- `selectRun(id)` continua existindo. Na tela Execuções ele também troca o hash (replace).
  `selectRun(x); setView('execucoes')` (usado em Infraestrutura, Aprendizado e Aplicativos) já chega em
  `#/execucoes/x`.
- `lib/rotas.ts`: a API ficou igual. Entrou só a documentação dos parâmetros com dono (`foco`, `aba`, filtros).
  Nenhuma assinatura mudou.
- `ProfileDetail` aceita `aba` e `onAbaChange` (controlada) e mantém `abaInicial` para uso não controlado.

## Divergências do briefing e decisões registradas

- **`?foco=`.** A nota do orquestrador lista `?foco=` entre os filtros do Painel, e o briefing usa
  `#/painel?foco=android-01` como "aparelho em foco". Adotei o segundo sentido: `foco` é o aparelho aberto no painel
  de Foco, é global (o painel existe em todas as telas) e não é filtro. Isso está escrito no cabeçalho de
  `rotas.ts`. Um filtro de "estado" do Painel usa `estado`.
- **`#/aplicativos/<pacote>`.** O segmento é o `app_id`, que é o que a API (`/apps/<id>/overview`) recebe. Nos apps
  atuais ele é um nome curto (`chrome`, `instagram`), e o pacote não está disponível antes de carregar a lista.
- **Guias com replace.** O briefing pede que Voltar e Avançar funcionem. Trocar de guia substitui a entrada em vez
  de empilhar, para não criar "histórico inútil": Voltar sai do objeto, não passa por cada guia vista.
- **Co-Authored-By.** As regras pedem `Claude Sonnet 5.5`, mas quem fez este trabalho é o Opus 5.5. Usei a linha
  do ambiente (`Claude Opus 5.5`).

## Toques em arquivo alheio (mínimos)

- `features/infra/InfraPage.test.tsx` (tarefa 02): uma linha. A asserção `personaRequest?.id` virou
  `rota.segmentos[0]`.
- `features/command/SugestaoDeAlvos.test.tsx` (tarefa 03): uma linha, `'perfis'` → `'personas'`.
- `features/focus/FocusPanel.test.tsx` (tarefa 03): o reset do `afterEach` e a asserção de "Abrir persona".
- `features/profiles/AcoesEmLote.test.tsx`, `NovaPersonaLote.test.tsx`, `ProfilesPage.test.tsx` (tarefa 05): o
  reset do `beforeEach` deixou de ser `setState({ personaRequest })` e passou a ser `navegar` para `#/personas`. O
  teste do pedido passou a usar `openPersona`.
- Dentro da posse "ligação de rota": `ProfilesPage.tsx` e `ProfileDetail.tsx` (a persona e a guia vêm da URL, mais
  um aviso "Persona não encontrada" para link de persona que não está na lista), `RunsPage.tsx` (o clique na lista
  empilha), `RunView.tsx` (a guia vai em `?aba=`, só na tela Execuções; no Painel não mexe na URL) e `AppsPage.tsx`
  (guia e app vêm da URL).

## Arquivos alterados

- Novos: `frontend/src/features/topbar/MenuLateral.tsx`, `MenuLateral.module.css`, `frontend/src/store/ui.test.ts`.
- Alterados: `frontend/src/store/ui.ts`, `frontend/src/lib/rotas.ts`, `frontend/src/App.tsx`,
  `frontend/src/features/topbar/TopBar.tsx`, `TopBar.module.css`, `TopBar.test.tsx`,
  `frontend/src/app.integration.test.tsx`, `frontend/src/features/profiles/ProfilesPage.tsx`, `ProfileDetail.tsx`,
  `frontend/src/features/runs/RunsPage.tsx`, `RunView.tsx`, `frontend/src/features/apps/AppsPage.tsx`, e os testes
  listados em "toques em arquivo alheio".
- `App.module.css`: sem mudança. O menu entra como primeiro filho do `.body` (flex), e a gaveta é `position: fixed`.

## Provas

**simulated** (vitest, backend falso):
- `npm run typecheck` verde. `npm test` inteiro: **75 arquivos, 873 testes, todos verdes** (30/09, cerca de
  19:54Z). Depois da remoção do `mesmoLugar` (sem uso), o typecheck e `rotas.test.ts` + `ui.test.ts` (21) seguiram
  verdes. `python scripts/docs-check.py`: 0 erros (1 aviso antigo do `estado.json`, alheio).
- `src/store/ui.test.ts` (15): `podeVoltarPara`; `#/perfis/ig-1/memoria?foco=…` → `#/personas/…` sem empilhar; hash
  desconhecido volta à rota atual; `hashchange` aplica a rota; abrir o Foco empilha preservando `q` e `situacao`, e
  fechar chama `history.back()` com a tela respondendo na hora; trocar de aparelho substitui; link colado com
  `?foco=` fecha por replace sem `back()`; o foco acompanha a troca de tela; `setView('execucoes')` leva a execução
  selecionada; `#/execucoes` é canonizado com replace; seleção automática substitui e o clique empilha;
  `openPersona` empilha; `trocarQuery` preserva o resto; `voltarPara` sem entrada anterior compatível empilha.
- `src/features/topbar/TopBar.test.tsx::Menu lateral — as oito seções sempre alcançáveis` (5): oito hrefs
  canônicos e `aria-current` só na atual; `foco` levado nos hrefs; recolher mantém o nome acessível e grava a
  preferência; a gaveta abre pelo botão, o teclado entra no item atual, Esc fecha e devolve o foco ao botão; trocar
  de seção fecha a gaveta.
- `src/app.integration.test.tsx::Central de Aparelhos — rotas por objeto e menu` (5), com o App inteiro: menu com oito
  seções; `#/perfis` → `#/personas` com título "Personas · Central de Aparelhos"; abrir o Foco põe
  `?foco=android-02` e o **Voltar real do jsdom** (`history.back()`) fecha o painel; link colado
  `#/infraestrutura?foco=android-01` abre o aparelho e "Fechar" deixa `#/infraestrutura`; `#/execucoes` →
  `#/execucoes/<id>` e o link com `?aba=linha-do-tempo` abre essa guia.

**real** (30/09, cerca de 19:52Z, máquina central, commit `384a415`; vite na 5191 com
`VITE_API_TARGET=http://127.0.0.1:8000`, painel do Browser da IDE; só navegação, nenhum clique com efeito):
- 1440×900: coluna lateral com as 8 seções visíveis, `scrollWidth` igual à largura (1440, sem rolagem horizontal).
  Começou recolhida (a janela do painel do navegador media menos de 1280 px no carregamento) e "Expandir menu"
  expandiu. `#/perfis` virou `#/personas`, com o título certo e "Personas" como item atual. "Abrir André Carvalho"
  → `#/personas/<id>`; a guia Memória → `#/personas/<id>/memoria`, sem nova entrada no histórico (o comprimento
  ficou em 3). Navegar de novo para o link abriu a mesma pessoa na mesma guia, e Voltar → `#/personas` com a lista.
  "Abrir android-01 na visão de foco" → `#/painel?foco=android-01` (o painel abriu e o conteúdo ficou com 594 px),
  e Voltar fechou o painel (`#/painel`). O menu Execuções → `#/execucoes/r-20260928155247-f55c04`; a guia Linha do
  tempo → `?aba=linha-do-tempo` por replace, e navegar de novo para o link abriu a guia. `#/aplicativos?aba=apps` →
  "Abrir Chrome" → `#/aplicativos/chrome`, e Voltar → `#/aplicativos?aba=apps` na guia "Por app".
- **Recarga de verdade** (`location.reload()`, `performance…type === "reload"`, com um marcador de `window` que
  sumiu) em `#/perfis/<id>/memoria?foco=android-01`, num gesto só: o hash ficou canônico
  (`#/personas/<id>/memoria?foco=android-01`), "Personas" como item atual, a mesma pessoa na guia Memória e o painel
  de Foco de android-01 aberto.
- 1024×768: com a preferência já gravada (expandida, pelo clique a 1440), coluna de 212 px, as 8 seções visíveis,
  conteúdo com 812 px, botão "Menu" oculto, `scrollWidth` 1024. Depois de apagar a preferência e recarregar em
  `#/painel?foco=android-01`: coluna recolhida por padrão (60 px), Foco aberto, conteúdo com 364 px (antes ~424 px,
  com a navegação no topo) e `scrollWidth` 1024. **Custo conhecido:** quem expandir a coluna a 1024 px com o Foco
  aberto fica com cerca de 212 px de conteúdo. É escolha da pessoa (o padrão ali é recolhida), mas fica
  registrado para a tarefa 03 (drawer), que pode fazer o Foco sobrepor em vez de empurrar nessa faixa.
- 390×844: coluna oculta (`visibility: hidden`), botão "Menu" visível, `scrollWidth` 390. Abrir a gaveta mostra as 8
  seções inteiras, com o teclado no item atual e `aria-expanded="true"`. Clicar em "Diagnóstico" navegou, fechou a
  gaveta e devolveu o foco ao botão.
- Observação à parte: pela porta 5191 o canal ao vivo fechou com o código 1006 (faixa "Desconectado do backend").
  A REST respondeu normalmente. Não é desta tarefa e parece ser o backend recusando a origem do dev server; não
  investiguei.

**not_run**: 768 e 1920 px no navegador. Pelo CSS, 768 é gaveta, como 390, e 1920 é coluna, como 1440. Leitor de
tela real. Navegador que não seja o Chromium do painel da IDE.

## O que ficou de fora e pendências

- Guias de Configuração e Aprendizado na URL: são da tarefa 06 (hoje estão no `localStorage`). O caminho é
  `rota.query.aba` mais `trocarQuery({ aba })`.
- Os filtros de Personas (`situacao`, `q`, `ordem`, `visao`) e do Painel (`estado`) são aceitos e preservados, mas
  nada os lê ainda (tarefas 05 e 02).
- `docs/produto.md` não cita `#/perfis`, e nada mais nos docs precisou mudar. O orquestrador consolida o
  `CHANGELOG.md` e o `estado-atual.md`.
- Ao navegar a partir da gaveta, o teclado volta ao botão "Menu", não ao conteúdo. Levar o foco ao `main` depois da
  troca de tela é uma melhoria possível (tarefa 07, acessibilidade).
- Um link para uma persona que não está na lista mostra o aviso "Persona não encontrada" com "Ver todas", sem
  apagar o link sozinho.

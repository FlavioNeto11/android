# Tarefa 11: ajustes finais (RF-05 reaberto e RF-26)

Branch `ux/11-ajustes-finais`, que sai de `ux/09-revisao-final` `9e3d16a` (`claude/ux-portal` `58d67f5` mais o relatório
final). Data: 30/09/2026. Máquina: central (Windows Server 2025). Esforço pedido: high. Fonte: `revisao-final.md`
(RF-05 e RF-26, gravidade média). Um commit por achado. Nenhuma ação com efeito real foi disparada.

| Achado | Situação | Commit | Prova |
|---|---|---|---|
| RF-05 contador de execuções em andamento | **corrigido no código** (a parte do servidor só vale depois do deploy) | `368b575` | simulated; real não executado (ver abaixo) |
| RF-26 Tab escapa da gaveta e o Esc para de valer | **corrigido** | `a53a7bd` | simulated + real (768 px, leitura) |

## RF-05: o contador e o chip leem a mesma fonte, completa desde o primeiro carregamento

### Causa (duas, independentes)

1. `GET /api/snapshot` devolve só as **20 execuções mais recentes** (`api.py`, `LIMIT 20`). Os comentários do front
   ("o snapshot traz as ativas + 20 recentes") descreviam algo que o backend nunca fez. As 3 `planned` do parque real
   estão nas posições 57, 169 e 176 de 247: fora do snapshot, e o contador abria em 0.
2. O reducer cortava o store nas **100 mais recentes** (`MAX_RUNS`). Quando Execuções carregava o histórico (200
   linhas), as `planned` das posições 169 e 176 saíam de novo do store e só a da 57 sobrevivia: o contador ia de 0 a 1.
   O chip lê `unirExecucoes(runs, paginadas)`, que guarda as páginas na própria tela e vê as 3.

Corrigir só uma das duas deixa o número errado (com só o snapshot, abre em 3 e cai para 1 depois de visitar
Execuções).

### Solução e justificativa

- **Backend, `GET /api/snapshot`**: `runs` passa a ser "as 20 mais recentes **mais todas as em andamento**" (uma
  consulta, `status IN (...) OR id IN (SELECT ... LIMIT 20)`, portátil entre SQLite e PostgreSQL). "Em andamento" usa o
  vocabulário do backend (`RunStatus` menos `RUN_TERMINAL` menos `needs_input`, que o painel agrupa como pendência) e é
  o mesmo conjunto de `grupoDoStatus === 'andamento'`. **Sem endpoint novo.** Custo hoje: +3 linhas (cerca de 2 KB) no
  snapshot; as 24 execuções `needs_input` do parque real **não** entram. A alternativa só no front (paginar `GET
  /api/runs?limit=200` na abertura para contar) custaria 2 chamadas e cerca de 170 KB a cada carga, e a conta
  continuaria dependendo do histórico.
- **Front, `store/reducer.ts::sortRuns`**: o teto de 100 passa a valer só para o que não está em andamento; execução
  em andamento nunca sai do store por antiguidade. O custo é desprezível (poucas) e o teto continua protegendo contra
  o acúmulo das concluídas.
- **Front, `store/live.ts::autoSelectRun`**: com as antigas no snapshot, a abertura automática do detalhe poderia pegar
  a `planned` de 27/09. Ela agora olha só as 20 mais recentes (comportamento de antes). Não piora o RF-32.
- A regra de contagem não mudou (`execucoesEmAndamento`, `grupoDoStatus`). `docs/api-contract.md` (linha de `GET
  /api/snapshot`) e o comentário de `store/metricas.ts` foram atualizados.

### Execuções `planned` antigas (para o dono olhar)

As 3 execuções `planned` (19/09, 22/09 e 27/09) **contam como em andamento pela regra atual**, por isso o topo e o chip
vão dizer **3** depois do deploy. A regra não foi mudada. Elas parecem execuções paradas há dias; vale o dono decidir
se são lixo (cancelar) ou se o rótulo do topo deve dizer "ativas agora" e separá-las, como sugere `revisao-final.md`.

### Provas

- **simulated**: `frontend/src/store/metricas.test.ts::RF-05: o contador de em andamento vale desde o primeiro
  carregamento e não muda ao visitar Execuções`. Hidrata o store com 20 recentes + 3 `planned` antigas, depois faz o
  `mergeRuns` de 247 execuções (o que Execuções faz) e exige 3 nos dois momentos, igual à contagem do chip. **Conferi
  que falha com o reducer antigo** (`expected 100 to be greater than 100`: o store ficava no teto) e passa com o novo.
- **simulated**: `backend/tests/test_tools_and_api.py::test_snapshot_traz_as_recentes_e_todas_as_em_andamento` (25
  concluídas recentes + `planned` e `paused` antigas + `needs_input` e `completed` antigas: o snapshot traz 20 + as 2
  em andamento, na ordem, e não traz as outras). Rodado com o interpretador do venv do checkout central e a pasta
  `backend` deste worktree como diretório atual (confirmado: `app` importado daqui).
- **not_run (real)**: o contador a 0 ao abrir não pôde ser reconferido no navegador porque o servidor em `:8000` ainda
  roda o snapshot antigo (20 linhas); a parte do servidor só vale depois do `deploy.ps1`. Leitura real feita contra
  `GET /api/runs?limit=200` (posições 57, 169 e 176 e total 247, 24 `needs_input`) para dimensionar a mudança.

## RF-26: a gaveta do menu prende o Tab e fecha com Esc em qualquer lugar

### Causa

O ouvinte de teclado morava no `<nav>`. Com o foco fora dele (o Tab saía para "Reconectar agora", atrás do fundo
escurecido), nenhum Esc chegava ao `<nav>`.

### Mudança (`features/topbar/MenuLateral.tsx`)

Enquanto a gaveta está aberta e a janela está abaixo de 1024 px, um `keydown` no **documento**:

- **Tab** e **Shift+Tab** dão a volta entre os itens (do último para o primeiro e vice-versa); com o foco já fora da
  gaveta, o Tab o traz de volta para dentro. Reaproveita `elementosFocaveis` do drawer do Foco (a mesma regra de "o que
  o Tab alcança").
- **Esc** fecha a gaveta com o foco em qualquer lugar, exceto vindo de uma caixa de diálogo (`dialog` ou
  `[role="dialog"]`), cujo Esc é dela. O Esc do `<nav>` foi removido (o do documento o cobre).
- Fechar sem escolher seção devolve o foco ao botão "Menu" (efeito que já existia); escolher uma seção leva o foco a
  `#conteudo`, como antes. `aria-current` e os cliques não mudaram.
- Acima de 1024 px (coluna fixa, sem gaveta) o ouvinte não intercepta nada; sem `matchMedia` (jsdom) vale como gaveta.

### Provas

- **simulated**: `features/topbar/TopBar.test.tsx`, três testes novos: Tab do último vai ao primeiro e Shift+Tab do
  primeiro vai ao último, com o meio livre e `aria-current` intacto; com o foco fora, o Tab (e o Shift+Tab) o traz para
  dentro e o Esc fecha e devolve o foco ao botão "Menu"; com a gaveta fechada, nada é interceptado. **Os dois
  primeiros falham sem a mudança** (conferi). Os testes antigos da gaveta (Esc no item, foco no conteúdo ao escolher
  seção, fechar ao trocar de seção) seguem verdes.
- **real** (30/09/2026, central, código do RF-26 que virou `a53a7bd`, dev server na porta 5198
  contra `:8000`, só leitura, aba própria, 768 px): "Menu" abre a gaveta com o foco no item atual; 12 Tabs reais
  seguidos ficaram dentro da gaveta (deram a volta); com o foco em "Pular para o conteúdo" (fora), um Tab real o
  trouxe para o botão "Fechar menu" da gaveta; com o foco fora de novo, um Esc real fechou a gaveta e o foco foi para
  `#botao-menu`. Nenhuma ação com efeito, nenhum login. Servidor parado e viewport restaurado ao fim.

## Divergências do briefing e código

- O briefing e os comentários do front dizem que o snapshot traz "ativas + recentes". O backend só trazia as 20
  recentes. A correção do RF-05 faz a frase ficar verdadeira. É um **toque em arquivo alheio** (backend), por exigência
  do achado.

## Arquivos tocados

Backend: `backend/app/api.py` (consulta do snapshot), `backend/tests/test_tools_and_api.py` (teste novo).
Front: `frontend/src/store/{reducer,live,metricas}.ts`, `frontend/src/store/metricas.test.ts`,
`frontend/src/features/topbar/MenuLateral.tsx`, `frontend/src/features/topbar/TopBar.test.tsx`.
Docs: `docs/api-contract.md` (linha do snapshot) e este relatório. `CHANGELOG.md` e `docs/estado-atual.md` não foram
tocados.

## Verificação final

- `npm run typecheck`: sem erros. `npm test` inteiro: **84 arquivos, 1004 testes, todos verdes** (30/09/2026, central).
- Backend: só `test_tools_and_api.py -k snapshot` (2 testes) rodou; a suíte do backend não foi rodada (carga do host).
- `python scripts/docs-check.py`: não rodou.

## O que ficou de fora

RF-04, RF-08 e os achados baixos, como pedido. Para o RF-05 ter efeito no ambiente real é preciso **implantar** o
backend (`scripts/deploy.ps1`) e o `dist`; sem isso o contador continua abrindo em 0 contra o servidor antigo.

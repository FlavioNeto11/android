# Tarefa 12: decisões de produto da revisão de UX (D1 a D4)

Branch `ux/12-decisoes`, que sai de `claude/ux-portal` `07174c6`. Data: 30/09/2026. Máquina: central (Windows Server
2025). Esforço pedido: high. Fonte: `revisao-final.md` (RF-04, RF-08, RF-31) e `11-ajustes-finais.md`. As decisões já
vinham tomadas; aqui só foram implementadas. Um commit por decisão, mais um de rótulo achado na verificação real.
Nenhuma ação com efeito real foi disparada; nenhuma execução foi cancelada nem alterada; nenhum endpoint de escrita.

| Decisão | Commit | Prova |
|---|---|---|
| D1 (RF-04) uma só "Pendência", completa desde a primeira carga | `cade559`, `332c5c3` | simulated; real parcial (servidor antigo); o número 27 é `not_run` até o deploy |
| D2 `planned` não é "em andamento" | `8bcedc5` | simulated + real (chip e dica) |
| D3 (RF-08) URL manda, visão vira preferência do navegador | `0571fb0` | simulated + real |
| D4 (RF-31) alvo de 32 px nos links "Ver todas… pendências" | `52e6abe` | real (medido); jsdom não mede altura |

## D1: "Pendência" tem uma só definição, e a dona é a caixa

**Pendência** é o que depende de uma pessoa: aprovação do Aprendizado, aprovação de persona, intervenção em sessão e
**execução em `needs_input`**. A origem Execução da caixa foi **substituída**, não ampliada. Antes eram os
objetivos `waiting_user`/`uncertain` das 20 execuções mais recentes. A leitura real (`GET /api/runs`, 247 execuções)
mostrou que as 4 linhas de hoje eram todas `completed_with_issues` com `waiting_user = 1`, ou seja, execuções que **já
tinham terminado**. As 27 `needs_input` têm `counts` zerados e nunca entravam.

- **Seletor único**: `features/pendencias/modelo.ts::execucoesPedindoResposta` (todas as `needs_input`, sem janela)
  entra em `montarPendencias`, e `usePendencias().total` é lido por três lugares:
  - o selo do menu (como já era);
  - o chip "aguardando você" do topo (`TopBar.tsx`), cujo clique leva a `#/pendencias`;
  - o aviso "N pendências esperando você" do semáforo (`SaudeAmbiente.tsx`), que leva a `#/pendencias`. Ele continua
    fora dos motivos e não muda a cor do semáforo.

  Saíram de `store/metricas.ts` `execucoesAguardando`, `objetivosAguardando`, `useObjetivosAguardando` e
  `JANELA_DE_EXECUCOES`, que eram a segunda definição. A linha da execução diz "Parou pedindo informação · <primeira
  pergunta>" (de `status_detail`), e a ação é "Responder", que leva à execução.
- **Completude desde a primeira carga**:
  - backend: `_SQL_RUNS_DO_SNAPSHOT` traz todas as `needs_input`, além das 20 recentes e das em andamento;
  - `store/reducer.ts::sortRuns`: o teto de 100 nunca descarta uma `needs_input` (como já acontecia com as em
    andamento).
- **Triagem por idade**: execuções paradas há mais de 7 dias vão para "Antigas (N)", recolhida (`Disclosure`, montada
  só ao abrir), abaixo das recentes. Elas **contam** em "Todas (N)", no menu e no topo. A regra vale **só para a
  origem Execução**: o `desde` de uma intervenção é a última verificação da sessão, e um login travado de verdade não
  pode cair numa seção recolhida. Se não houver recente, aparece "Nada dos últimos 7 dias. As antigas estão logo
  abaixo."
- **"Como o número é contado"** foi reescrito: diz o que é pendência, que entram todas, como funcionam as antigas, que
  menu, topo e semáforo mostram o mesmo total, e que execução terminada com problema fica em Execuções, "Pede atenção".
- **Execuções**: o chip "Com pendência" virou **"Pede atenção"**, com a dica "Terminaram com algum problema para
  revisar ou pararam pedindo informação. As que pararam pedindo informação dependem de você e também estão em
  Pendências." O código na URL continua `pendencia`, e `?status=pendencia` continua valendo (teste e real).
  `BarraListagem` ganhou `dica?` por opção, mostrada pelo `Tooltip` no hover e no foco de teclado.
- **Achado na verificação real** (commit `332c5c3`): o selo de `completed_with_issues` dizia "Concluída com
  pendências", de novo a palavra para outra coisa. Passou a "Concluída com problemas" (`lib/status.ts`).

### Custo do snapshot (medido sobre a leitura real de 30/09, em leitura)

| | Linhas em `runs` | Bytes de `runs` (JSON) |
|---|---|---|
| servidor no ar (`:8000`, snapshot antigo: 20 recentes) | 20 | 16.664 B, de um snapshot de 77.140 B |
| depois desta tarefa (20 recentes + `needs_input` + em andamento) | 45 | cerca de 37,8 KB: +25 `needs_input` fora das 20, cerca de **+21,2 KB** |
| comparado ao da tarefa 11 (20 + as 3 `planned`) | +22 linhas | cerca de +19,2 KB (saem as 3 `planned`, 1.966 B) |

São cerca de **+27 %** no snapshot inteiro, em JSON sem compressão. O tamanho por linha foi medido no DTO de `GET
/api/runs`, que usa o mesmo `run_summary` do snapshot. O custo cresce uma linha por execução que fica parada em
`needs_input`: elas só saem desse estado sendo canceladas ou respondidas, já que a sucessora cancela a antiga. A
consulta varre `runs` sem índice em `status` (247 linhas hoje), o que é desprezível. Se as `needs_input` antigas
passarem de centenas, vale um índice em `runs(status)` (migração nova) ou um contador no servidor. Não foi feito
agora.

## D2: `planned` não é "em andamento"

- `grupoDoStatus('planned')` = `planejada`. O grupo novo entra em `GRUPOS_STATUS` logo depois de "Em andamento", com
  o chip **"Planejadas"** e a dica "Plano pronto para inspeção; ainda não foi executado". `?status=planejada` vale.
- O contador do topo (`execucoesEmAndamento`) e o chip "Em andamento" não contam mais `planned`. O tooltip do topo
  agora diz "Um plano pronto que ainda não foi executado não conta".
- Backend: o snapshot traz as não terminais **exceto** `planned` e **incluindo** `needs_input` (`_STATUS_DO_SNAPSHOT`).
  Uma `planned` só vem se estiver entre as 20 recentes.
- Contrato atualizado em `docs/api-contract.md` (linha de `GET /api/snapshot`) e em `lib/rotas.ts`.
- **As 3 execuções `planned` do parque real (19/09, 22/09 e 27/09) não foram tocadas.**

## D3: a URL é a fonte da verdade, e a visão é preferência

A regra única fica em `lib/visao.ts` (`visaoDoLink`, `visaoPreferida`, `lembrarVisao`; `localStorage` pelo
`lib/storage`, com try/catch) e vale no Painel e em Personas:

- `?visao=` no link manda;
- sem ele, vale a última visão escolhida neste navegador;
- escolher grava nos dois: no link por `trocarQuery(…, 'replace')` e no navegador.

Ler um link não muda a preferência. O menu continua levando à tela limpa: os filtros somem e a visão fica.

- Painel (`DeviceGrid.tsx`): ganhou `?visao=cards|lista`. A chave do navegador é a mesma de antes (`cda.painel.visao`).
- Personas (`filtroPersonas.ts`, `ProfilesPage.tsx`): `lerFiltroPersonas(query, preferida)`. `queryDoFiltro` agora
  escreve a `visao` **sempre explícita**, inclusive `cards`. Sem isso, um link compartilhado abriria na preferência de
  quem o recebe. A chave nova é `cda.personas.visao`.
- Rótulos: "Cartões | Lista" no Painel e "Cartões | Tabela" em Personas.
- `docs/produto.md` ganhou dois itens em "Fluxos do usuário":
  - "Menu, links e visões";
  - "Pendências e o que pede atenção" (D1/D2).

  No item "Cabeçalho", o indicador "bloqueadas" virou "aguardando você". A tarefa 03 só tinha escrito "Seleção em massa
  e Foco", e a 06 não escreveu nada ali, então não houve conflito.

## D4: alvo de 32 px (RF-31)

O RF-31 cita só dois links:

- `ParaAprovarTab.tsx`, "Ver todas as suas pendências";
- `GuiaAprovacoes.tsx`, "Ver todas as pendências", no estado vazio e no texto de abertura.

Os dois ganharam a classe `.linkAlvo` (em `Aprendizado.module.css` e `Profiles.module.css`), com `inline-flex`,
padding de 9 px 4 px e margem negativa igual: é a receita do `.linkish` da tarefa 07. O `.linkBtn` (padding de 6 px,
29 px de alvo) continua igual nos outros usos.

## Divergências do briefing e código

- **"Cards"**: o briefing pede o fim de "Cards", mas os rótulos visíveis já eram "Cartões". "Cards" restava só em
  comentários (`DeviceGrid.tsx`, `Drawer.tsx`, `DeviceGrid.test.tsx`), que foram corrigidos.
- **"Com pendência" também era ambíguo no selo**, além do chip: "Concluída com pendências" foi trocado (ver D1).
- **Testes removidos e substituídos**: os dois testes de `metricas.test.ts` sobre a janela de objetivos
  (`objetivosAguardando`) saíram junto com a função. A D1 aboliu essa definição. No lugar entrou "D1: as execuções em
  `needs_input` contam desde a primeira carga e não somem ao visitar Execuções". O teste RF-05 de "em andamento
  persiste além do teto" passou a usar `paused` no lugar de `planned` (D2), sem perder o que ele prova.
- **`docs/produto.md`** entrou no commit da D3 com os itens de D1/D2 juntos (um arquivo só, um commit só).

## Provas

### simulated (vitest, backend falso)

Todos os testes D1, D2 e D3 abaixo **falharam antes** da mudança (rodados antes de implementar) e passam depois.

- D1:
  - `features/pendencias/PendenciasPage.test.tsx`:
    - "D1: toda execução em `needs_input` é pendência, por mais antiga; objetivo esperando em execução terminada não";
    - "D1: execuções paradas há mais de 7 dias ficam em "Antigas (N)", recolhida, e contam no total e no menu";
    - os 5 testes antigos, adaptados à nova origem.
  - `store/metricas.test.ts::D1: as execuções em needs_input contam desde a primeira carga e não somem ao visitar
    Execuções` (snapshot e `mergeRuns` de 250 execuções, acima do teto).
  - `features/topbar/SaudeAmbiente.test.tsx`:
    - "o popover diz … (as pendências da caixa), com link", que leva a `#/pendencias`;
    - "D1: "aguardando você" é o total da caixa de Pendências e leva a ela" (muda junto com a caixa; o clique vai a
      `pendencias`).
  - `features/runs/RunsPage.test.tsx::D1: o chip … não usa a palavra "pendência" e o link antigo continua valendo`
    (`?status=pendencia` dá "Pede atenção 82", e a dica aparece no foco).
- D2:
  - `features/runs/filtroExecucoes.test.ts::D2: planned não é "em andamento"…`;
  - `store/metricas.test.ts::RF-05 e D2…` (4, não 5);
  - `features/runs/RunsPage.test.tsx::D2: as execuções planned têm chip próprio "Planejadas"…`.
- D3:
  - `features/devices/DeviceGrid.test.tsx` (2 testes: preferência sem `?visao=`, gravação no link sem empilhar e no
    navegador; o link manda e não mexe na preferência);
  - `features/profiles/ProfilesPage.test.tsx::D3 (RF-08): o menu leva à tela limpa, mas a visão escolhida vira o
    padrão…`;
  - `features/profiles/filtroPersonas.test.ts::D3…`.
- Backend: `backend/tests/test_tools_and_api.py::test_snapshot_traz_as_recentes_e_todas_as_em_andamento`, agora com a
  `needs_input` antiga **dentro** e a `planned` antiga **fora**.
  - Confirmei que falha com o `api.py` anterior (`1 failed`) e passa com o novo.
  - Rodado com o interpretador do venv do checkout central e a pasta `backend` deste worktree; `app.__file__` aponta
    para o worktree.
  - `-k snapshot`: 2 passed, inclusive `test_api_dedup_validacao_e_reconexao_por_snapshot`.

### real (30/09/2026, central)

Dev server do worktree em `127.0.0.1:5198` contra `:8000`, só leitura, aba própria (`tab-12`), sessão já aberta no
navegador do app (nenhum login feito). O servidor em `:8000` roda o **snapshot antigo** (20 recentes).

- Execuções, `#/execucoes?status=pendencia`:
  - o link antigo abriu com o chip **"Pede atenção 115"** marcado ("115 de 247"), "Planejadas 3" e "Em andamento 0";
  - a dica real de "Planejadas" (hover) diz "Plano pronto para inspeção; ainda não foi executado";
  - chips com 32 px.
- Pendências:
  - menu "Pendências 2 (2 esperando você)", topo "2 aguardando você", "Todas (2)", Execução (2): as 2 `needs_input`
    que estão entre as 20 recentes;
  - o número continuou o mesmo depois de visitar Execuções;
  - o topo mostra "0 execuções" (as 3 `planned` não contam mais).
- D3:
  - Painel "Lista" levou a `#/painel?visao=lista`, gravou `"lista"` no navegador e mostrou 15 linhas;
  - Personas `?situacao=ativa`, em "Tabela", levou a `…&visao=tabela`;
  - **pelo menu**: `#/painel` abriu na Lista; `#/personas` abriu limpo (14 personas), mas em Tabela;
  - **dois Voltar** restauraram `#/personas?situacao=ativa&visao=tabela`;
  - ao fim, as duas preferências foram apagadas (origem 5198).
- D4, a 1440 px:
  - os dois links medem **37,5 px** de altura com a classe (17 px sem);
  - o parágrafo onde moram fica com a mesma altura (39 px) com e sem a classe;
  - a 390 px, "Ver todas as suas pendências" mede 38 px, numa caixa só.
- Larguras: sem rolagem horizontal em Execuções, Pendências e Aprendizado a 390 px, nem em Pendências, Execuções e
  Painel a 1024 px.
- Viewport restaurado, aba fechada, vite parado e porta 5198 livre.

### not_run

- **O número verdadeiro (27) e "Antigas (N)" no navegador**: exigem o snapshot novo no `:8000`, isto é,
  `scripts/deploy.ps1` do backend e do `dist`, que esta tarefa não pode rodar. Até lá, o painel contra o servidor
  antigo mostra só as `needs_input` entre as 20 recentes (2 hoje). Depois do deploy, o esperado em 30/09 é 27: 8 em
  "Antigas" (17/09 a 20/09) e 19 recentes (24/09 a 28/09). O corte de 7 dias anda com o relógio: em 01/10, as 12 de
  24/09 também passam para "Antigas".
- Suíte inteira do backend: não rodou (carga do host, como pedido). Só `-k snapshot`.

## Verificação final

- `npm run typecheck`: sem erros. `npm test` inteiro (uma vez): **84 arquivos, 1013 testes, todos verdes**.
- `pytest -q tests/test_tools_and_api.py -k snapshot`: 2 passed.
- `python scripts/docs-check.py`: 0 erros, 1 aviso **anterior** a esta tarefa (`estado.json` do plano-100).

## Arquivos tocados

- **Backend**: `backend/app/api.py` (SQL do snapshot), `backend/tests/test_tools_and_api.py`.
- **Front**:
  - `components/BarraListagem.tsx` (dica por chip);
  - `features/pendencias/{modelo.ts, PendenciasPage.tsx, PendenciasPage.test.tsx}`;
  - `features/topbar/{TopBar.tsx, SaudeAmbiente.tsx, SaudeAmbiente.test.tsx}`;
  - `features/runs/{filtroExecucoes.ts, filtroExecucoes.test.ts, RunsPage.tsx, RunsPage.test.tsx, Runs.module.css}`
    (o CSS só no comentário);
  - `store/{metricas.ts, metricas.test.ts, reducer.ts, live.ts}` (`live.ts` só no comentário);
  - `lib/{visao.ts (novo), rotas.ts, status.ts}`;
  - `features/devices/{DeviceGrid.tsx, DeviceGrid.test.tsx}`, `features/focus/Drawer.tsx` (comentário);
  - `features/profiles/{filtroPersonas.ts, filtroPersonas.test.ts, ProfilesPage.tsx, ProfilesPage.test.tsx,
    GuiaAprovacoes.tsx, Profiles.module.css}`;
  - `features/aprendizado/{ParaAprovarTab.tsx, Aprendizado.module.css}`.
- **Docs**: `docs/api-contract.md` (linha do snapshot), `docs/produto.md` e este relatório.
- Não tocados: `CHANGELOG.md`, `docs/estado-atual.md` e os ADR.

## O que ficou de fora

- O deploy (D1 e D2 só têm efeito no servidor depois do `deploy.ps1`).
- Um índice em `runs(status)`, se as `needs_input` crescerem muito.
- Decidir o destino das 3 execuções `planned` e das 27 `needs_input` (cancelar ou responder) é do dono; nada foi
  alterado.

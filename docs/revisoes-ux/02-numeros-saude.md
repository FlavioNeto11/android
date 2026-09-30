# Tarefa 02: fonte única de números e semáforo de saúde do ambiente

Branch `ux/02-numeros-saude` (sai de `claude/ux-portal`, `2126d43`). Data: 30/09/2026. Máquina: central (WIN), worktree
`C:\git\android\.claude\worktrees\ux-02-numeros-saude`.

## O que mudou

- **`frontend/src/store/metricas.ts` (novo)**: a camada única de cálculo. Funções puras, cada uma com a definição em
  comentário: `contarAparelhos`, `contarSelecao`, `servidorInalcancavel`/`estadoContado`, `vagasOcupadas` (movida de
  `infraState`), `ocupacaoDoServidor`, `ocupacoesDoParque`, `objetivosAguardando`, `personasBloqueadas`,
  `nivelDoAmbiente`. Ganchos: `useContagemDeAparelhos`, `useObjetivosAguardando` e `useSaudeDoAmbiente`.
- **`features/topbar/SaudeAmbiente.tsx` (novo, com `.module.css` próprio)**: semáforo **OK / Atenção / Crítico**. O
  popover lista os motivos, cada um com link (`hashDe`) para a tela que resolve. Há uma seção "Também", que não muda a
  cor: personas bloqueadas (→ Personas filtrada) e objetivos aguardando você (→ Execuções). Os fatos de SDK, Appium e IA
  saíram, porque o Diagnóstico é o dono deles. Quando falham, entram como motivo pelos `problems` da saúde.
- **`TopBar.tsx`** (toque mínimo em arquivo alheio, ver abaixo): troca `HealthPill` por `<SaudeAmbiente />`.
  `Counters` passa a ler da fonte única. "bloqueadas" vira **"aguardando você"**. O chip "N vagas" sai.
- **`features/devices/DeviceGrid.tsx`** (toque em arquivo alheio): o resumo por estado conta o mesmo conjunto que o
  botão seleciona. A loja aparece à parte ("loja parada") e o estado "desconhecido" tem grupo próprio. "N de T
  selecionados" usa `contarSelecao`.
- **Infraestrutura** (`InfraPage.tsx`, `infraState.ts`): as vagas vêm de `ocupacaoDoServidor`. Acima da capacidade, o
  cartão mostra uma nota de alerta (`role="note"`) com o motivo. Com o servidor fora do ar, aparece "? de N" com a
  explicação, e não um número velho.
- **Diagnóstico** (`tiles.ts`, `DiagnosticsPage.tsx`): o azulejo "Aparelhos" usa a mesma contagem ("7 de 14 online").
  Ele deixou de pôr o online do parque contra `max_online_devices`, que é a vaga só do central, e avisa quando há
  desconhecidos ou servidor acima das vagas.

## Mapa de onde cada número era calculado (antes)

| Número | Onde era calculado | Base |
|---|---|---|
| "5/15 online" | `TopBar.Counters`: `Object.values(instances)` | todas as instâncias, **com** a loja |
| "1 de 14 selecionados" | `DeviceGrid` e `CommandPanel`: `selectTaskOrder().length` | sem a loja |
| "10 paradas" (texto) e "Selecionar 9" (title) | `DeviceGrid`: o texto vinha de `countByState(instances)` e o botão de `taskOrder` | texto com a loja, botão sem |
| "N vagas" no cabeçalho | `settings.max_online_devices` | só a vaga do **central** (`config.py`: "vagas DESTE host") |
| "Vagas ocupadas 5 de 4" | `InfraPage`: `vagasOcupadas` × `worker.max_slots` | por servidor (correto) |
| "Aparelhos: 7 online · 4 vagas" | `DiagnosticsPage`: online de todas × `max_online_devices` | mistura parque e central |
| "15 bloqueadas" | `TopBar.Counters`: soma de `waiting_user + uncertain` de **todas** as execuções do store | objetivos de execução, não personas |
| "Ambiente OK" | `HEALTH_STATUS[health.status]` | só `GET /health`, que ignora os workers |

## Divergências do briefing (segui o código)

1. **"15 vs 14"** não é erro de contagem: o cabeçalho contava a loja (android-11) e a seleção não.
2. **"9 vs 10 paradas"**: o texto contava a loja e o botão selecionava só os aparelhos de tarefa. Não foi um aria-label
   errado. O `title` do botão dizia 9 e o texto visível dizia 10.
3. **"bloqueadas 15 vs 5"**: são duas métricas diferentes com o mesmo rótulo. O "bloqueadas" do cabeçalho são
   **objetivos de execução** esperando uma pessoa. O valor mudava de uma tela para outra porque a lista de execuções do
   store **cresce** quando a tela Execuções carrega mais histórico (`mergeRuns`); o snapshot traz só as ativas e as 20
   mais recentes. Já as "5 Bloqueada pela plataforma" são **personas** (`status = blocked`; no backend vivo, 5 de 14).
   Correção: o rótulo virou "aguardando você", com janela fixa (ativas + 20 mais recentes), e as personas bloqueadas
   ganharam contagem e link próprios no semáforo.
4. **"5 de 4" é real, não é erro de cálculo.** O backend conta igual (`devices/manager.py::slots_used`: online, booting
   e stopping de aparelhos não externos). Medido em 30/09: android-01, 02, 03, 05 e 06 online no central, com
   `max_online_devices = 4`. A tela agora destaca e explica, sem afirmar a causa, que não verifiquei.
5. **"Notebook fora do ar e 6 desconhecidos"**: no momento da medição o notebook estava **conectado, porém
   `degraded`** (relógio desalinhado em +5,9 s). O critério "fora do ar → Atenção" foi provado por teste
   (`simulated`). Na medição real, o semáforo mostrou o motivo "Servidor Notebook da LAN degradado".

## Tabela final: métrica → definição → onde aparece

A tela dona fica **em negrito**. O cabeçalho só resume; nas outras telas há link em vez de cópia.

| Métrica | Definição (`store/metricas.ts`) | Onde aparece |
|---|---|---|
| Aparelhos (total) | Aparelhos de **tarefa** cadastrados, sem a loja. A loja aparece à parte, com o estado dela. | Cabeçalho ("7/**14**"), **Painel** (resumo e "de 14 selecionados"), Diagnóstico ("de 14") |
| Online | Aparelho de tarefa com `state = online` **e** servidor alcançável | Cabeçalho, **Painel** (resumo), Diagnóstico |
| Paradas (e demais estados) | `state` do aparelho, só entre os de servidor alcançável; soma dos grupos = total | **Painel** (resumo; o botão seleciona exatamente os mesmos ids) |
| Desconhecidos | Aparelho cujo servidor não está inscrito ou está fora do ar (`!enrolled || !connected`). Sem lista de servidores (backend antigo), ninguém é desconhecido. | **Painel** (grupo "desconhecidos"), semáforo (motivo → `#/painel?estado=desconhecido`), Diagnóstico (subtítulo) |
| Selecionados | Ids selecionados que ainda são aparelhos de tarefa, de "total" | **Painel** (grade); o Comando usa a mesma base (`selectTaskOrder`) |
| Vagas (capacidade × ocupadas) | **Por servidor**, nunca somadas. Ocupa vaga o que está `online`, `booting` ou `stopping` (a loja também, se ligada). Capacidade: central = `max_online_devices`, worker = `max_slots`. Fora do ar = desconhecida (`?`). | **Infraestrutura** (cartão de cada servidor, com alerta acima da capacidade), semáforo (motivo → Infraestrutura), Diagnóstico (só "N servidor(es) acima das vagas") |
| Aguardando você | Objetivos `waiting_user + uncertain` das execuções ativas e das 20 mais recentes | Cabeçalho ("4 aguardando você" → **Execuções**), semáforo ("Também") |
| Personas bloqueadas | Personas com `status = blocked` | Semáforo ("Também" → `#/personas?situacao=bloqueada`); **Personas** é a dona |
| Saúde do ambiente | **Crítico**: painel sem conexão com o central, saúde `error`, banco inalcançável, conta de IA em uso barrada ou sem saldo. **Atenção**: saúde `degraded` (um motivo por problema), servidor fora do ar, degradado ou com túnel caído, aparelhos desconhecidos, servidor acima das vagas, saldo baixo em conta em uso, IA não configurada. **OK**: nada disso. Manutenção não conta como problema. | Cabeçalho (semáforo e popover); **Infraestrutura** é a dona de servidores e vagas, **Diagnóstico** de SDK, Appium, custo e saldo de IA |
| CPU / RAM do central | `metrics` do snapshot (sem mudança) | Cabeçalho (resumo), **Infraestrutura** (cartão do central), Diagnóstico (azulejo Máquina) |

## Divergências encontradas no backend (não corrigidas)

1. `GET /api/health` ignora o estado dos workers: respondeu `status: ok` com o worker-lan-01 `degraded`. Por isso o
   semáforo agora cruza `workers` no front.
2. `max_online_devices` é a vaga só do central (`config.py:353`), mas o `settings` o expõe sem essa indicação. Foi isso
   que levou o cabeçalho e o Diagnóstico a apresentá-lo como capacidade do parque.
3. O central estava de fato acima da capacidade (5 ligados para 4 vagas) em 30/09. Não verifiquei se a partida manual
   ignora a vaga ou se o limite foi reduzido com aparelhos ligados.
4. Não existe endpoint de contagem estável de "objetivos esperando pessoa". O front depende da janela do snapshot.
5. `/api/workers` devolve `devices: []` para o worker local, e aí a ocupação do central sai do `state` das instâncias.
   É o mesmo que o backend faz, então não gera divergência, mas fica anotado.

## Provas

- **simulated**:
  - `frontend/src/store/metricas.test.ts`: 20 testes. Cobrem servidor da LAN offline (desconhecidos, não paradas;
    Atenção com destino), ocupação acima da capacidade (5 de 4), seleção parcial (loja e id sumido ignorados), aparelho
    de servidor inalcançável ou não inscrito = desconhecido, backend sem workers, painel desconectado = Crítico, saúde
    error/degraded, saldo de IA, janela fixa de "aguardando você" e personas bloqueadas.
  - `frontend/src/features/topbar/SaudeAmbiente.test.tsx`: 5 testes. Semáforo OK, Atenção com a LAN fora do ar
    (motivos e links), Crítico sem conexão, "Também" com personas bloqueadas e o link filtrado, e a base do cabeçalho
    ("1/4 online" sem loja nem desconhecido).
  - `frontend/src/features/infra/InfraPage.test.tsx`: 2 testes novos (vagas acima destacadas; fora do ar = "?").
  - `frontend/src/features/diagnostics/tiles.test.ts`: azulejo Aparelhos com a nova semântica.
  - `app.integration.test.tsx`: "Ambiente em atenção" e o cabeçalho sem "vagas".
  - `npm run typecheck` verde. `npm test` inteiro: **76 arquivos, 877 testes, todos verdes** (commit `46fb0a3`).
- **real** (30/09/2026, máquina central, commit `46fb0a3`, vite em 5192 com proxy de leitura para 127.0.0.1:8000,
  somente leitura):
  - `GET /api/snapshot`, `/workers`, `/personas`, `/settings` e `/health` por curl confirmaram: 15 instâncias (14 de
    tarefa e a loja android-11), 7 online, central com 5 ligados para 4 vagas, worker-lan-01 conectado e `degraded`,
    `health.status = ok`, 5 de 14 personas `blocked`, 4 objetivos aguardando nas 20 execuções do snapshot.
  - Tela: cabeçalho "7/14 online · 4 aguardando você". Painel "7 online · 7 paradas · loja parada · 0 de 14
    selecionados". Diagnóstico "7 de 14 online". Infraestrutura "5 de 4" com a nota de alerta. Semáforo: popover com os
    motivos "Servidor Notebook da LAN degradado" e "Este servidor (central): 5 aparelhos ligados para 4 vagas", e em
    "Também" o link "5 personas bloqueadas pela plataforma" para `#/personas?situacao=bloqueada`.
  - Conferido em 1440, 1024 e 390 px: o popover cabe e rola em 390.
  - Ressalva: para ver o portal sem gravar sessão no backend, não fiz login. Pus um operador **só no store do
    navegador** (`useSessionStore.setState`). Sem o cookie de sessão, o WebSocket não conecta (código 1006), então o
    semáforo mostrou **Crítico** ("painel sem conexão"), o que é correto para aquele navegador. Com login de verdade,
    o esperado é **Atenção** pelos dois motivos acima.
- **not_run**: o notebook da LAN realmente fora do ar (não derrubei nada); a navegação dos links de Personas e Painel
  filtrados (dependem das tarefas 01, 04 e 05).

## Toques em arquivo alheio

- `features/topbar/TopBar.tsx` (tarefa 01). Remove `HealthPill` e `Fact` e os imports mortos (`Check`, `X`,
  `ReactNode`, `HEALTH_STATUS`, `metaOf`) e insere `<SaudeAmbiente />`. Em `Counters`: lê de
  `useContagemDeAparelhos` e `useObjetivosAguardando`; "bloqueadas" vira "aguardando você"; o chip "N vagas" sai (o
  tooltip explica o rodízio por servidor). O integrador resolve o conflito com a 01.
- `features/devices/DeviceGrid.tsx` (tarefa 04, grade). O resumo por estado lê de `useContagemDeAparelhos`, aparece o
  grupo da loja e "N de T selecionados" usa `contarSelecao`. `countByState` e `STATE_SUMMARY_LABEL` continuam
  exportados em `deviceState.ts`, sem uso na grade.
- `app.integration.test.tsx`: duas expectativas atualizadas para o novo comportamento (Atenção; sem "vagas" no
  cabeçalho).

## Pendências

- **`#/personas?situacao=bloqueada` ainda não navega**: `store/ui.ts` (tarefa 01) não reconhece `personas` como
  visão. E a tela Personas ainda não aplica `situacao` (tarefa 05).
- **`#/painel?estado=desconhecido` navega, mas o Painel não filtra** (tarefas 04 e 01). Até lá, o grupo
  "desconhecidos" do resumo por estado seleciona esses aparelhos com um clique.
- Os cartões de aparelho ainda mostram "Desconhecido" pela lógica própria (`noFrameTitle`, tarefa 04). A contagem usa
  a mesma regra (`serverHintOf`), então as duas coincidem.
- O rótulo "paradas" (feminino, herdado de "instâncias") ficou como estava, por ser vocabulário da tarefa 08.
- O `CommandPanel` (tarefa 03) conta `selectedIds.length` sem filtrar ids que sumiram. O total já é o mesmo; o
  numerador pode divergir num caso raro.
- Trailer dos commits: as regras pedem `Claude Sonnet 5.5`, mas esta sessão roda Opus 5.5. Usei o modelo real:
  `Co-Authored-By: Claude Opus 5.5`.

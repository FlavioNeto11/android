# Checkpoint — evolução de desempenho e capacidade (coordenação multiagente)

Pedido do dono de 26/09/2026: `prompt-evolucao-android-multiagentes.md`, com as frentes F1 a F8. **Escritor único
deste arquivo: o coordenador.** Se a sessão cair, retome daqui: não refaça o reconhecimento e não reabra decisões
sem evidência nova. O relatório de resultados fica em [`../relatorio-desempenho.md`](../relatorio-desempenho.md)
(criado na integração).

## Base e integração

- **SHA base:** `1104d50` (`origin/main` depois do deploy de `57a155f` pela sessão "Sessão e Android Login
  coordination"). O diagnóstico original era sobre `dc47f60`; tudo foi revalidado contra `1104d50`.
- **Branch de integração:** `claude/evolucao-desempenho`, no worktree `.claude/worktrees/evolucao`. O checkout
  `C:\git\android` é o **checkout de produção**: o `deploy.ps1` não faz checkout e o supervisor religa a partir
  daquele disco. Por isso nada é integrado nele sem deploy autorizado. A publicação vai por push do branch.
- **Onda 0 (contratos):** `a0f251a`, com `metricas.py`, `GET /api/desempenho`, `Settings.preview_mode` e o adendo
  v0.20 de `api-contract.md` (contratos C1 a C7).
- **Números reservados:** migração `042` (ainda não usada), ADR-027, adendo v0.20 (usado), K-033.

## Configuração efetiva da produção (leitura de 26/09, `37bb6e6`/`57a155f`)

Fontes: `GET /api/health` e `GET /api/settings`.

| Chave | Valor |
|---|---|
| `features` | `image_policy: auto`, `recipes: replay`, `flows: true`, `hibernation: true` |
| Captura | `capture_grid_interval_s: 5`, `capture_focus_interval_s: 1` |
| Parque | `max_online_devices: 4`, `boot_parallelism: 2` |
| IA | ator no Sonnet 5, verificador no Haiku 4.5, plano e escalonamento no Opus 5.5 |

## Reconhecimento (Onda 0): hipóteses do pedido × HEAD `1104d50`

| Hipótese | Veredito | Evidência |
|---|---|---|
| Captura periódica sem vínculo com espectador | confirmado | `manager.py::_capture_loop`: 5 s por aparelho online; o foco só muda o ritmo |
| `observe()` gera PNG e JPEG antes da política de imagem | confirmado | `manager.py::observe` → `publish_frame`, antes de `executor.py::_want_image`; o `_screen` codifica uma terceira vez |
| Captura e ações no mesmo executor exclusivo | confirmado; a exclusividade fica | `devices/executor.py`; `overdue` força captura com a fila ocupada |
| `appium: local` com screenshot ainda por ADB do central | confirmado | `appium_driver.py` → `adb exec-out screencap`; `bind_worker_appium` só repõe `session` |
| Worker só com ciclo de vida | confirmado | `worker/executor.py`: verbos `create/start/wake/stop/hibernate/restart/reset/emulator_log`; `install/open/home/back` e toda a automação vão por ADB cru no túnel |
| Receitas, flows e desbravador existentes; padrões ≠ exemplo | confirmado; produção usa o exemplo | código: `off`/`False`/`0`; exemplo e produção: `replay`/`true`/`240` |
| Perfis de RAM divergentes | confirmado | exemplo `ram_mb: 1536` × `perfis.py` 2048; B21 (26/09) mostra saturação real a 1,5 GB |
| Admissão por vagas; telemetria limitada | confirmado | sem reserva coerente (central só desconta `booting` com PID; worker só com paralelismo 1); sem cgroup, GPU, pressão, swap, I/O ou rede |
| Estado de worker, controle e frames em memória; NATS e `ROLE` existem | confirmado | `registry.py`, `manager.py`; NATS atrás de bandeira, nunca exercitado |
| Emulador por PID/AVD; contêiner só declarado | confirmado | `worker/executor.py` varre processos pelo AVD; não há Dockerfile |

Achados novos, fora das hipóteses:

- **H6, privacidade:** a prévia do painel mostra tela sensível, que já estava fora do modelo e das evidências.
- **Divergência de receita que não escala:** suspeita, a conferir pela F3.
- **NATS:** publica em `comandos.<worker_id>` e assina `comandos.<owner_id>`; a conferir pela F7.
- **Cerca `MAX+1` fora de transação:** decisão de não usar `UNIQUE` (dados legados podem ter duplicata).
- **`eval_run.py`:** toca o backend vivo e o adb mesmo no modo "simulado".
- **Espera do desbravador:** invisível e não medida.

## Frentes, responsáveis e arquivos reservados (Onda 1, a partir de `a0f251a`)

| Frente | Worktree / branch | Arquivos reservados | Aceite e prova esperada |
|---|---|---|---|
| F2 backend | `ev-f2-backend` | `devices/manager.py`, `devices/stream.py`, `devices/executor.py`, `models.py` (tela), `api.py` (`/frame` e `listen()`), `taskqueue/executor.py`, `appium_driver.py`, `sensitive_input.py` | Três commits: H6, prévia sob demanda, observação com árvore primeiro; `simulated` |
| F2 frontend | `ev-f2-frontend` | `frontend/src/**` | `watch` com IntersectionObserver e visibilidade; `paused`; `preview_mode` no formulário; `simulated` (vitest) |
| F3 | `ev-f3-receitas` | `taskqueue/recipes.py`, `scheduler.py`, `flows.py`, `aproveitamento.py` (novo) | Funil de receitas, desbravador visível e liberado na falha, patch da divergência; `simulated`, e leitura `real` por GET |
| F5 | `ev-f5-recursos` | `workers/protocol.py` (C6/C7), `workers/registry.py`, `worker/*`, `devices/perfis.py`, `recursos.py` (novo), `config/*.example.yaml` | Reserva no worker, cgroup e pressão, admissão conservadora, exemplos alinhados; `simulated` |
| F1 | `ev-f1-medicao` | `desempenho.py` (novo), `scripts/bench.py`, `scripts/eval_run.py`, `scripts/tests/*` | Resumo p50/p95, benchmark seguro, linha de base simulada e leitura real; `simulated` e `real` (somente GET) |
| F6 | `ev-f6-conteiner` | `Dockerfile`, `compose.yaml`, `.dockerignore`, `deploy/`, `docs/operacao.md` (seção nova), `main.py` (só o endereço de escuta) | Empacotamento do central e teste estático; build `not_run` (engine parado, WSL exige autorização) |
| F7 | só leitura | rascunho no scratchpad | Matriz de executores e decisões de runtime, NATS e K8s |
| F8 | depois da integração da Onda 1 | revisão do SHA integrado | Cenários da §12 do pedido |

A Onda 2 fica para depois da integração:

- F4: verbo de observação no worker com mídia fora do canal de comando, negociação C7, cerca em transação e assunto
  do NATS.
- F5 no central: reserva em `_recusa_por_capacidade`.

## Registro da integração

Todos os lotes estão em `claude/evolucao-desempenho`.

**F1, `84434b9` + `5cbeb3f`.**
- Entraram `desempenho.resumo`, `scripts/bench.py` (modos simulado, leitura e comparar), `eval_run.py` seguro sem
  `--yes` e `GET /api/desempenho?dias=N`.
- Linha de base, na pasta `scratchpad/bench` do coordenador:
  - simulada (`21b98a1`): prévia sem espectador com 18 screencaps em 6 s e 3 aparelhos, tanto em `on_demand` quanto
    em `always`; `image_policy auto` corta as imagens enviadas de 12 para 3, mas os screencaps ficam em 16;
    receitas mais flows: 13 chamadas na primeira execução e 5 na repetição.
  - real, somente GET, produção `57a155f`, 7 dias: US$ 11,38, dos quais `decide` 70 %; 9,3 chamadas por objetivo;
    boot frio p50 151 s e p95 472 s, quente p50 22,7 s.

**F5, `f620a7f`.**
- Entraram `devices/recursos.py` (fica em `devices/` porque o instalador do agente copia só
  `worker/ workers/ devices/ security/`), a reserva de RAM por boot no worker, a admissão conservadora com batida
  velha e os exemplos sem `ram_mb: 1536`.
- Docs pendentes: o significado de `reserved_mb` no contrato, e em `worker.md` a recusa por batida velha e o piso
  `ram_per_device_mb`.

**Coordenador, `7b7a641`.**
- `version.py` passa a ler o commit num git worktree.
- `metricas.py` entra no pacote do agente.
- O worktree de integração tem `backend/.venv` como junção, para o teste do instalador achar o venv.

**F6, `f771ebf` + `9721d9a`.**
- Entraram `deploy/`, com o Dockerfile do central, o compose de validação, `saude.py` e `iniciar.py`, e
  `CONTAINER_LISTEN_HOST` em `main.py`. A §14 de `operacao.md` traz o procedimento. O build ficou `not_run`: o
  Docker Desktop não é suportado em Windows Server, conferido na documentação oficial.
- O patch sugerido do `state.py` (`sdk_missing` deixar de ser duro sem emulador local) foi **recusado**: o central
  usa o `adb` do SDK também para os aparelhos remotos pelo túnel.

**F3, `42d7efa` + `7879d86`.**
- Entraram o funil `receita.*`, o desbravador (espera visível `wait_reason=pathfinder`, medida, liberada na falha do
  líder, agrupada por compatibilidade) e `aproveitamento.py`, com o campo `aproveitamento` em
  `GET /api/flows/cobertura`.
- **Decisão do coordenador sobre a receita divergida:**
  - a escalada ao modelo caro **não** foi aplicada, porque é custo sem prova de ganho (22 etapas `recipe+ai` em
    7 dias) e os controles atuais já escalam depois de erros; fica como decisão do dono;
  - o comentário de `config.py` foi corrigido;
  - a contagem `receita.retorno_ia` entra junto com o executor, depois do commit 3 da F2.
- Pendente: `test_receita_divergida_escala.py` falha até isso entrar. Vai ser reescrito para o comportamento
  decidido (tier 0 na divergência, contagem uma vez por etapa).

**F2, `8222590` (backend, commits 1–2) + `a741171`/`9d2a9b3` (painel).**
- Entraram a tela sensível fora da prévia (marcador sem imagem e `/frame` 404) e a prévia sob demanda (`watch`,
  `paused`). Mudança deliberada: a VM-loja nunca aparece na prévia, coerente com o ADR-014, porque o login da conta
  Google é feito na janela do emulador; a volta atrás é tirar `rt.store` de `_previa_sensivel`.
- Bancada, contra a linha de base: prévia sem espectador em `on_demand` foi de 18 para **0** screencaps, com 18
  evitadas; `always` segue em 18 (`simulated`, `scratchpad/bench/comparacao-onda1a.txt`).
- Testes: backend 152 passaram; vitest 474/474.

**F2 commit 3, `6386d64`, + `fe4b3eb`.**
- Observação com a árvore primeiro, e o login do Instagram lendo só a árvore.
- Aplicada a parte de medição do patch F3: `receita.retorno_ia` é contado, e a divergência continua sem escalar.
- Bancada contra a linha de base (`simulated`): `image_policy auto` 16 → 9 screencaps; repetição com receitas
  16 → 11; prévia `on_demand` 18 → 0. Nenhuma regressão em chamadas de IA nem em sucesso.

**F8, revisão de `9d2a9b3`, `fdf609e`.** 13 achados, 12 deles novos. Todos foram corrigidos e tiveram o `xfail`
removido:
- F2, `3f8381e`:
  - publicação e observação conferem a tela sensível e a geração no instante de publicar;
  - marcador por hierarquia não declara captura recuperada;
  - funil contado por tentativa;
  - controle manual prevalece sobre o desbravador.
- F5, `63ae6cc`:
  - reserva órfã no cancelamento com o emulador vivo;
  - vagas contam o boot admitido;
  - uso ilegível vale como desconhecido;
  - limite sem disponível;
  - métrica da reserva chega ao central pela batida.
- Coordenador, `26a81fb`: o descarte de série contava em dobro.
- Painel, `4da9755`: com a aba oculta, mantém o foco do aparelho sob controle manual, para o lease não vencer.
- A conexão nova que acordava o parque já estava resolvida em `a3dd949`.

**F4 fase A, `a6ef9af`.** Defeitos reais corrigidos, com teste que falha antes e passa depois:
- o NATS publicava para o worker, e não para a réplica hospedeira;
- `ack_wait` era ignorado;
- a cerca era calculada fora da transação;
- reentrega depois do ack reexecutava.

O "resultado tardio" **não** era defeito; ganhou um teste de guarda. A negociação C7 está feita.

**Em andamento:**
- F4 fase B: imagem capturada na origem (feature `observe_local`), mídia fora do WebSocket de comando, reserva
  central em `_recusa_por_capacidade` e a corrida de `_do_action_no_worker`. A hierarquia continua pelo Appium, que
  já roda na origem com `appium: local`.
- Suíte inteira do backend no integrado `732667b`, em segundo plano.

## Autorizações pendentes (nenhuma pedida ainda)

- Deploy no central (tarefa `farm-central`) e atualização do agente do worker, que muda o hash e dá
  `agent_outdated`.
- Ligar o Docker Desktop/WSL para validar o build do contêiner.
- B21: RAM por imagem no `config.yaml` e reinício dos aparelhos; é decisão do dono.

## Próxima ação

Integrar cada entrega da Onda 1 no branch de integração, em lotes pequenos e na ordem H6 → F5/F3/F1 → prévia →
observação, rodando os testes afetados a cada lote. Depois vem a revisão F8 e, em seguida, a Onda 2.

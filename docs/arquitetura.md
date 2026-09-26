# Arquitetura

Visão de componentes e dos contratos que os ligam. Para o "o que faz o quê" de cada domínio de negócio, ver
[`dominios/parque.md`](dominios/parque.md), [`dominios/apps-e-loja.md`](dominios/apps-e-loja.md) e
[`dominios/perfis-e-instagram.md`](dominios/perfis-e-instagram.md); para o schema completo,
[`banco.md`](banco.md); para o protocolo central↔worker linha a linha, [`worker.md`](worker.md); para o
contrato HTTP/WS, [`api-contract.md`](api-contract.md).

## Componentes

```mermaid
flowchart LR
    FE[Frontend React\npainel] -- HTTP/WS :8000 --> API[Backend central\nFastAPI]
    API --> SCHED[Scheduler /\ntaskqueue]
    API --> APPIUM[Appium\n:4723]
    API --> DB[(SQLite ou\nPostgreSQL)]
    API --> STORE[Storage\ndisco ou S3]
    API -- WS :8010 (127.0.0.1) --> WK[Agente do worker\n(máquina remota)]
    SCHED --> LOCAL[LocalWorker\n(aparelhos deste host)]
    LOCAL --> EMU1[Emuladores /\naparelhos locais]
    WK -- ADB/Appium local --> EMU2[Emuladores /\naparelhos remotos]
    SUP[Supervisor] -. religa .-> API
    TUNEL[Túnel SSH reverso\n(-R porta_worker:127.0.0.1:porta_worker)] --- WK
```

- **Frontend** (`frontend/src`) — React; `features/` por domínio (`apps`, `command`, `devices`, `diagnostics`,
  `focus`, `infra`, `login`, `painel`, `profiles`, `releases`, `runs`, `settings`, `topbar`, `training`,
  `usage`), `store/` (estado + WebSocket), `api/` (cliente HTTP). Só solicita e acompanha — fila, scheduler,
  regras e persistência são do backend (`api-contract.md`, linha 7).
- **Backend central** (`backend/app`, `AppState` em `state.py`) — processo ÚNICO por máquina (sem
  `--reload`, sem múltiplos workers do uvicorn: dois processos com o mesmo hostname reconheceriam as etapas um
  do outro como próprias — `main.py:1-6`). Um único `uvicorn.Server` com DOIS sockets e um despachante ASGI por
  porta (`main.py::despachante`, linhas 307-323): a porta do painel (`server.port`, padrão `8000`) atende a API
  REST e o WebSocket `/ws`; a porta do worker (`server.worker_port`, padrão `8010`, `0` = desligada) atende só
  `worker_router` (`/worker/ws`) e é sempre aberta em `127.0.0.1` — nunca na rede — porque existe para ser o
  alvo do túnel reverso, não uma porta pública.
- **Supervisor** (`backend/app/supervisor.py`) — processo separado que sobe `python -m app.main`, vigia
  `GET /api/health` e religa quando ele PARA de responder (nunca quando responde `degraded`, que é uma resposta
  válida — Appium fora do ar, por exemplo). Espera crescente entre religadas; `terminate` antes de `kill`.
  Existe porque nada religava o backend depois de logoff/crash/Windows Update, enquanto o túnel já tinha tarefa
  agendada própria (achados #136, #38).
- **Agente do worker** (`backend/app/worker/agent.py` + `executor.py`) — processo que roda na máquina remota;
  liga PARA o central (nunca o contrário), declara capacidades no `hello`, bate coração, executa verbos.
  Detalhes em [`worker.md`](worker.md).
- **Appium** (`backend/app/automation/appium_server.py`) — dirige os aparelhos por UiAutomator2; pode rodar no
  central (`appium: central`, dirige por túnel) ou localmente no worker (`appium: local`, menor latência).
- **Emuladores / aparelhos** — `backend/app/devices/` (ver [`dominios/parque.md`](dominios/parque.md)).
- **Banco** — SQLite (padrão, arquivo único) ou PostgreSQL (`DATABASE_URL`), mesmo conjunto de migrações para
  os dois (`banco.md`).
- **Storage** (`backend/app/storage.py`) — disco (`DiskStorage`, padrão) ou bucket S3-compatível (`S3Storage`,
  `EVIDENCE_STORAGE=s3`) para evidências e APKs; existe para a evidência gravada por UMA réplica ser lida por
  outra (item 5.7).
- **Transporte de comandos** (`backend/app/commands/transport.py`) — `websocket` (padrão: a ordem é entregue
  DENTRO deste processo, ao mesmo worker já conectado) ou `nats` (atrás de bandeira, `COMMAND_TRANSPORT=nats`:
  publica num assunto por worker em NATS JetStream; escrito e **não exercitado contra um servidor real**).

## Módulos (`backend/app`)

| Módulo | Responsabilidade | Teste principal |
|---|---|---|
| `api.py` | Rotas HTTP/WS, tradução erro→HTTP, ponto de entrada dos comandos | `tests/test_contrato_http.py` |
| `state.py` (`AppState`) | Composição da aplicação: liga dispositivos, automação, planejamento, fila, eventos; ciclo de vida (`start`/`stop`) | `tests/test_commands.py`, `tests/test_execution.py` |
| `commands/` (`states.py`, `store.py`, `outbox.py`, `transport.py`, `reconciler.py`) | Comando do painel como entidade: máquina de estados, outbox, transporte, reconciliação de partida | `tests/test_outbox_de_comandos.py`, `tests/test_incertos_com_saida.py` |
| `taskqueue/` (`states.py`, `scheduler.py`, `balanceamento.py`, `ai_slots.py`, `repository.py`, `service.py`, `executor.py`, `flows.py`, `foreach.py`, `recipes.py`, `proofs.py`) | Fila de IA: planejamento, escalonamento, execução de etapas, receitas, vagas de IA, distribuição entre servidores | `tests/test_queue_core.py`, `tests/test_posse_de_etapa.py`, `tests/test_limites_por_servidor.py` |
| `devices/` (`manager.py`, `emulator.py`, `emulator_backend.py`, `perfis.py`, `compatibilidade.py`, `installer.py`, `adb.py`, `avd.py`, `sdk.py`) | Ciclo de vida do aparelho: monitor, boot/hibernação, perfil de RAM, compatibilidade, instalação | `tests/test_rotation.py`, `tests/test_capacidades_declaradas.py` |
| `workers/` (`protocol.py`, `registry.py`, `local.py`, `portao.py`) | Contrato central↔worker, registro de workers, worker local (`LocalWorker`) | `tests/test_contrato_de_worker.py`, `tests/test_workers.py` |
| `worker/` (`agent.py`, `executor.py`, `diario.py`, `settings.py`) | Processo do AGENTE remoto: conexão, execução de verbo, diário de desfechos | `tests/test_worker_agent.py`, `tests/test_worker_executor.py` |
| `automation/` (`driver.py`, `appium_driver.py`, `appium_server.py`, `hierarchy.py`, `tools.py`) | Fala com o aparelho via Appium/UiAutomator2; árvore de UI | `tests/test_execution.py` (indireto) |
| `planning/` (`provider.py`, `anthropic_provider.py`, `openai_provider.py`, `simulated_provider.py`, `routing.py`, `costs.py`, `prompts.py`, `parsing.py`, `training.py`, `catalog/`) | Camada de IA: provedor, roteamento/fallback, custo, prompts, catálogo de capabilities por app | `tests/test_hub_de_ia.py`, `tests/test_anthropic_provider.py` |
| `social/` (`repository.py`, `service.py`, `memory.py`, `observacao.py`, `policy.py`, `approvals.py`, `capacidades.py`, `context.py`) | Perfis, personas, memória, observação de tela, políticas/limites, aprovações | `tests/test_social_memory.py`, `tests/test_grupos_de_acesso.py` |
| `integrations/instagram/` (`authentication.py`, `navigation.py`, `reconciliation.py`, `verification.py`) | Login determinístico, classificação de tela, sessão do Instagram | `tests/test_instagram_auth.py` |
| `releases/` (`service.py`, `repository.py`, `catalog.py`, `inspector.py`) | Ciclo de vida de release: importação, assinatura, canário/promoção/quarentena/rollback | `tests/test_release_lifecycle.py` |
| `training/` (`recorder.py`, `skills.py`) | Modo treinamento: gravação e generalização em habilidade | `tests/test_modo_treinamento.py` |
| `security/` (`secret_store.py`, `sessions.py`, `access.py`, `redaction.py`, `sensitive_input.py`, `rekey.py`, `local_secret.py`) | Cofre de credenciais, sessão do painel, controle de acesso, redação de segredo em log | `tests/test_grupos_de_acesso.py` (política), ver `banco.md` para o cofre |
| `db.py` | Abstração SQLite/PostgreSQL, migração, checksum de migração aplicada | `tests/` com `TEST_DATABASE_URL` (ver `banco.md` §"Rodar a suíte contra o PostgreSQL") |
| `events.py` | Barramento de eventos: persistência seletiva, replay por id, replicação entre réplicas | `tests/test_hospedeiro.py` |
| `storage.py` | Disco ou S3 para evidências e APKs | — |
| `supervisor.py` | Processo separado: religa o backend quando ele para de responder | `tests/test_supervisao_do_central.py` |
| `apps_overview.py` | Agregação por app (rotas `/apps-overview`, `/apps/{id}/overview`) | `tests/test_perfil_multiapp.py` |
| `metricas.py` | Métricas de desempenho agregadas em memória (teto de séries, amostra para p50/p95), janela de 15 min em `measurements` (ADR-027) | `tests/test_metricas.py` |
| `desempenho.py` | Resumo histórico p50/p95/n por entidade para `GET /api/desempenho?dias=N` | `tests/test_desempenho.py` |

## Papéis (`ROLE`)

`ROLE` escolhe o que este processo faz (`main.py:20-21`, `config.py`):

| `ROLE` | Faz | Não faz |
|---|---|---|
| `all` (padrão) | tudo: API, Appium, aparelhos, scheduler, reconciliações de partida | — |
| `api` | só a API REST e o frontend | Appium, aparelhos, scheduler, NENHUMA reconciliação de partida |
| `scheduler` | hospeda aparelhos e despacha | não publica a API REST nem o frontend |

Dois backends podem hospedar o MESMO banco (item 5.1+): `instances.hosted_by` (migração 027) diz qual backend
hospeda cada aparelho, e despacho/rodízio/reconciliação atuam só no que este backend hospeda — objetivo de
aparelho alheio é IGNORADO, nunca bloqueado. O que ainda NÃO é compartilhado entre backends está registrado em
[`banco.md` §"Pendências honestas"](banco.md#pendências-honestas) (achado #27: estado de worker e controle
manual vivem na memória de um processo só).

## Contratos

### Comando (painel → aparelho)

Máquina de estados em `backend/app/commands/states.py` (`CommandState`), com o MESMO rigor que a fila de etapas
da IA:

```
created → dispatched → acked → running → succeeded | failed | uncertain
        ↘ rejected (recusa ANTES de despachar — nada tocou o aparelho)
        ↘ cancel_requested → cancelled | succeeded | failed | uncertain
```

Pontos que não são óbvios olhando só os nomes: `succeeded` pode vir direto de `dispatched`/`acked` sem passar
por `running` — um verbo curto (`stop`, `home`) pode terminar sem nunca relatar progresso, e carimbar `running`
no mesmo milissegundo do despacho era o defeito que isto substitui. `uncertain` só sai por decisão de alguém (ou
releitura do estado real) — nunca sozinho. **Fence**: todo `dispatch` carrega um inteiro que o worker devolve
sem alterar no `result`; resultado com fence velha é RECUSADO — é o que impede um worker que voltou do limbo de
sobrescrever o estado atual (`workers/protocol.py::Dispatch`/`Result`). **Idempotência**: quem impede o efeito
duplo entre reenvios não é a tabela de estados, é o diário do agente (`worker/diario.py`) — `command_id` já
executado devolve o desfecho guardado em vez de reexecutar o verbo, inclusive DEPOIS do `result_ack` (os últimos
64 confirmados ficam no diário), e despacho com cerca que não é MAIOR que a última executada no aparelho é
recusado sem execução (`failed`, `data.refused = "fence_not_newer"`). **Outbox**: a linha `pending` em
`command_outbox` (migração 029) é gravada na MESMA transação que aceita o comando — "existe linha pendente" ⇔
"a entrega é devida" ⇔ "quem subir de novo a executa" (`commands/outbox.py`). Entrega é AO MENOS UMA VEZ, nunca
exatamente uma — é o diário que garante que repetir a entrega não repete o efeito.

Desde a evolução de desempenho (26/09):

- a cerca é calculada dentro da transação, serializada por aparelho;
- o agente recusa, sem executar, despacho com cerca ≤ à maior que já executou naquele aparelho;
- o diário guarda os últimos 64 desfechos confirmados, para uma reentrega depois do `result_ack` receber o mesmo
  corpo em vez de reexecutar (adendo v0.20 de [`api-contract.md`](api-contract.md)).

### Protocolo worker (`backend/app/workers/protocol.py`)

Um arquivo só, importado pelos dois lados — não há duas verdades. `PROTOCOL_VERSION = 1`, `PROTOCOL_MIN = 1`:
central recusa worker de versão MAIOR que a dele; worker de versão MENOR que `PROTOCOL_MIN` recebe `refused`
dizendo para atualizar o agente.

| Mensagem | Direção | Quando |
|---|---|---|
| `hello` | worker → central | ao conectar; declara aparelhos, capacidades, versão |
| `heartbeat` | worker → central | periódico (ritmo dado por `welcome.heartbeat_s`, padrão 10 s) |
| `ack` | worker → central | recebeu um `dispatch` |
| `progress` | worker → central | mensagem curta de andamento |
| `result` | worker → central | desfecho (`succeeded/failed/uncertain/cancelled`), com a `fence` do dispatch |
| `welcome` | central → worker | resposta ao `hello`; ritmo de batida, aparelhos esperados |
| `dispatch` | central → worker | um comando, com `fence`, verbo, prazo |
| `cancel` | central → worker | pedido de cancelamento |
| `result_ack` | central → worker | confirma que RECEBEU o resultado — só então o agente para de reenviá-lo (o desfecho fica entre os confirmados do diário, para responder a uma reentrega) |
| `limits` | central → worker | limites por servidor (item 10.5); enviada na PRIMEIRA batida de cada conexão, não junto do `welcome` (ver [`worker.md`](worker.md#limites-por-servidor-item-105)) |

**Capacidades negociadas (adendo v0.20, C7).**

- O `hello` declara `features`, o que o agente implementa e confere. O `welcome` devolve `accepted_features`, a
  interseção com o que o central sabe usar.
- Mensagem de tipo novo só vai para quem aceitou a feature correspondente. Agente antigo, sem `features`, segue o
  caminho anterior.
- Continua `PROTOCOL_VERSION = 1`: tudo o que entrou é campo opcional (`extra="ignore"` nos dois lados).
| `refused` | central → worker | recusa a conexão com código e mensagem, em vez de fechar o socket calado |
| `observe_image` | central → worker | pedido de imagem capturada NA ORIGEM; só para quem teve `observe_local` aceito no `welcome` (C7) |
| `observe_result` | worker → central | falha da captura na origem, ou as dimensões de um pedido `so_dimensoes` (tela sensível) — nunca a imagem |

A imagem de `observe_image` volta por um **canal de mídia** próprio, `/api/worker/midia` (outra conexão
WebSocket, uma por imagem): primeira mensagem `request_id` + token de uso único emitido no pedido, segunda o corpo
binário (`empacotar_midia`: cabeçalho JSON + partes JPEG). Nada de imagem no socket de comando, para não atrasar
batida, `ack` nem desfecho. Só a imagem vai para a origem: a hierarquia segue pelo Appium (`uiautomator dump`
concorre com a sessão UiAutomator2 — um cliente UiAutomation por vez). Ver `workers/captura.py` e
`worker/observacao.py`.

### Fila: `runs` → `objectives` → `steps` → `attempts`

Enums em `backend/app/models.py`. Só `steps` e `commands` têm uma tabela de transição FORMALMENTE checada
(`taskqueue/states.py::STEP_TRANSITIONS`, `commands/states.py::COMMAND_TRANSITIONS`); `runs` e `objectives`
têm seus `status` escritos diretamente pelo serviço, sem `check_transition`.

- **`RunStatus`**: `planning, needs_input, planned, running, paused, cancelling, completed,
  completed_with_issues, cancelled, failed`.
- **`ObjectiveStatus`**: `pending, running, waiting_user, succeeded, failed, cancelled, uncertain`.
  `OBJECTIVE_TERMINAL = {succeeded, failed, cancelled}`; `OBJECTIVE_SETTLED` acrescenta `waiting_user` e
  `uncertain` — encerram o trabalho automático mas aguardam decisão humana.
- **`StepStatus`**: `pending, ready, running, verifying, retry_wait, waiting_user, uncertain, failed,
  succeeded, cancelled, skipped`. Transições não óbvias: `ready → retry_wait` (represada pelo limite do perfil
  ANTES de consumir tentativa ou chamada de modelo); `running/verifying → ready` (cedeu num ponto seguro, sem
  efeito externo pendente); `failed → ready` é o único jeito de "tentar de novo os elegíveis".
- **`AttemptStatus`**: `running, succeeded, failed, interrupted, uncertain, cancelled`.
- **Lease de etapa** (`taskqueue/repository.py`, `POSSE_TTL_S`) — quem assume uma etapa (`claim_step`) grava
  `claimed_by` + `claim_expires_at`; generoso de propósito (o preço de um lease longo é demorar a retomar de um
  backend morto; o de um curto é dois backends executando a mesma etapa). Renovado periodicamente; etapa de
  OUTRO dono só é reconciliada quando o lease VENCEU pelo tempo, nunca por suposição.
- **`ai_slots`** (item 5.2, migração 027, `taskqueue/ai_slots.py`) — teto de chamadas de IA simultâneas
  COMPARTILHADO por todos os backends do mesmo banco: cada vaga é uma linha (`slot` 1..limite) tomada por
  compare-and-swap (`UPDATE ... WHERE holder IS NULL OR expires_at < ?`), não por contagem — contar e inserir é
  o mesmo erro de corrida que `claim_step` já tinha resolvido para etapas. Vagas vencem por TTL (120 s) para
  sobreviver à queda do dono; `boot_parallelism` continua por processo, de propósito, porque o recurso que ele
  protege (RAM/CPU) é da máquina, não do sistema.

## Eventos (`EventBus`, `backend/app/events.py`)

Todo evento é transmitido a quem está conectado ao WebSocket; a maioria também é PERSISTIDO em `events`, com id
monotônico — o cliente reconecta informando o último id visto e recebe só o que perdeu. `EPHEMERAL_KINDS`
(`events.py:24`) são os que NUNCA são gravados: `frame`, `metrics`, `worker.metrics`, `health.updated`,
`apps.updated`, `settings.updated` — o exemplo que motivou a lista foi `worker.metrics` (batida de 10 s por
worker, 57% do log de eventos, empurrando o que importava para fora da janela de replay). Lista completa dos
eventos em uso, incluindo os que faltam na tabela de `api-contract.md`, está no
[Adendo v0.11 desse documento](api-contract.md#eventos-ausentes-da-tabela-de-eventrecordkind).

Entre réplicas (dois backends no mesmo banco compartilhado): `EventBus.replicar_sempre` (laço de 1 s) lê o que
OUTRAS réplicas publicaram (`origin <> eu`) e entrega aos WebSockets deste processo — sem isto, o painel ligado
na réplica B não via nada do que a réplica A fazia.

## Recuperação após reinício (`AppState.start`, `state.py:1394+`)

Ordem, e por que ela é essa:

1. **Transporte de comandos sobe primeiro**, antes de qualquer efeito — com NATS ligado e sem broker no ar, a
   falha tem de ser na PARTIDA, alta e visível, nunca no meio de um comando de aparelho.
2. **Relógio contra o banco** (`conferir_relogio`) — antes de qualquer efeito: um relógio errado só é
   detectável contra o banco, e subir com ele desalinhado quando já existe outro hospedeiro significaria
   adotar etapa viva alheia (item 5.3). Com outro backend hospedando aparelhos no mesmo banco, desvio grande
   RECUSA a subida; sem outro backend, é só aviso.
3. Se `roda_scheduler` (`ROLE` ∈ {`all`, `scheduler`}): Appium (se autostart) → `devices.start()` →
   `local_worker.conectar()` (antes do scheduler: a partir daqui o ciclo de vida local tem para quem ir) →
   `scheduler.start()` → `runs.resume_planning_after_restart()` → `releases.reconcile_after_restart()`
   (instalação interrompida nunca é repetida às cegas) → `_reverificar_interrompidas()` →
   `social.reconcile_pending_effects()` (efeito disparado sem desfecho observado vira incerto) →
   `commands.reconcile_after_restart()` (comando `dispatched`/`running` sem desfecho vira `uncertain`, evento
   `command.updated`) → **só depois disso**, `_drenar_outbox()` (publica o que foi aceito e nunca saiu —
   filtrado por `hosted_by`, para o segundo backend não drenar a fila do primeiro).
4. `ROLE=api`: nenhuma reconciliação de partida roda — quem reconcilia é quem hospeda; um processo de API
   reconciliando destruiria trabalho vivo do hospedeiro real.
5. Laços de fundo: outbox (retry), retenção, `worker-reaper`, saúde, e — só com `DATABASE_URL` — replicação de
   eventos entre réplicas.

## Concorrência e idempotência

Três mecanismos do mesmo formato (linha + compare-and-swap + TTL, medido pelo relógio do BANCO, nunca pelo da
máquina): lease de etapa (`claim_step`), vaga de IA (`ai_slots`), e a guarda de relógio na partida. Onde a
concorrência é só de UM processo (RAM/CPU da máquina: `boot_parallelism`; estado de worker em memória; controle
manual) fica deliberadamente por processo — tornar global exigiria persistir estado que hoje é sabidamente uma
lacuna (`banco.md` §"Pendências honestas").

## O que é validado por teste × por execução real

Este documento e os de domínio descrevem o que o CÓDIGO faz. Se isso já rodou de verdade — em vez de só em
teste automatizado, com agente/banco/provedor falsos no mesmo processo — é outra pergunta, respondida por
[`relatorio-validacao.md`](relatorio-validacao.md), §13 ("Execução distribuída — os nove aceites"):
uma tabela por aceite com três colunas — *Real* (id de comando/execução, data, máquina), *Simulado*
(`arquivo::teste`), *Não feito*. Regra da própria tabela: teste automatizado prova o CÓDIGO; só um `c-…`/`r-…`
datado prova o SISTEMA em produção. Um exemplo que atravessa vários documentos aqui: `start` remoto nunca
terminou `succeeded` em produção (único despacho, `c-20260921172322-6f7fdc`, ficou `uncertain` desde 21/09) —
mecanismo implementado e testado, produção não confirma.

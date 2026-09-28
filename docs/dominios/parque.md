# Domínio: o parque de aparelhos

O parque é o conjunto de instâncias Android (emuladores e aparelhos físicos) que o backend liga, desliga,
monitora e repara — sozinho no host, ou espalhado por vários servidores (workers). Este documento cobre
virtualização, hospedagem local/remota, escalonamento, limites por servidor, controle manual e reparo
automático. Para o protocolo entre central e worker, ver [`../worker.md`](../worker.md); para o contrato HTTP,
ver [`../api-contract.md`](../api-contract.md); para os estados de comando e o rodízio na visão de conjunto, ver
[`../arquitetura.md`](../arquitetura.md).

## Virtualização

- **`backend/app/devices/emulator.py`** e **`backend/app/devices/emulator_backend.py`** — ciclo de vida do
  emulador: criar AVD, ligar, desligar, hibernar/acordar. `emulator_backend.py` separa o backend REAL (fala com
  o console do emulador e o ADB) do FALSO (usado em teste, obedece um flag `snapshot_ok`) atrás de uma interface
  comum — `RealEmulatorBackend.save_snapshot` chama `Adb.snapshot_save`, o dublê só grava a chamada.
- **`backend/app/devices/perfis.py`** — perfil de hardware por IMAGEM do sistema (`PerfilDeImagem`: `ram_mb`,
  `extra_args`, `est_real_mb`, `origem`). Existe porque o valor de RAM por imagem estava só em comentário/doc e
  nenhum código o aplicava ao criar o AVD; a tabela `_PERFIS` (linhas 24-28) é medida
  ([`../relatorio-validacao.md`](../relatorio-validacao.md)), não chutada:

  | Imagem | RAM do AVD | Flags | Custo real medido |
  |---|---|---|---|
  | `google_apis_playstore` | 4096 MB | — | ≈5,2 GB |
  | `google_apis` (padrão) | 2048 MB | `-lowram` | ≈2,7 GB |
  | `default` (AOSP) | 1536 MB | `-lowram` | ≈2,4 GB |

  Imagem desconhecida cai no perfil de `google_apis` — errar para mais RAM é o erro barato
  (`perfil_por_imagem`, perfis.py:34-38).

  **RAM do convidado × custo no host** (evolução de desempenho, 26/09):
  - `ram_mb` é o que o Android enxerga; `est_real_mb`/`est_ram_host_mb()` é o que o processo custa no host, com
    overhead do emulador. Admissão e reserva usam o custo no host.
  - Os exemplos (`config/config.example.yaml` e `config/worker.example.yaml`) deixaram de sugerir `ram_mb: 1536`
    para `google_apis`, o valor que o perfil registra como thrash. O B21 de 26/09 mediu a saturação de novo: o
    android-04 falhou na prova de abertura e o android-01 chegou a load 22.
  - O `config.yaml` de produção não mudou; trocar a RAM é decisão do dono, com o procedimento no
    [relatório de desempenho](../relatorio-desempenho.md).
- **Hibernação por snapshot** — `sem_snapshot(porque)` (`devices/manager.py:82-89`) formaliza o motivo quando um
  desligamento não conseguiu salvar snapshot ("o próximo boot será a frio"); `RealEmulatorBackend.discard_snapshot`
  apaga o snapshot do AVD sem falhar por ausência. `DeviceManager.snapshot_failures` conta falhas consecutivas
  por instância.

## Workers: local e remoto

Todo aparelho tem um HOSPEDEIRO — a máquina que o liga e fala com ele pelo ADB. O central sempre é um worker de
si mesmo:

- **`backend/app/workers/local.py`** (`LocalWorker`) — embrulha o `DeviceManager` no MESMO contrato
  `dispatch`/`ack`/`progress`/`result` que o worker remoto usa, registrando-se na tabela `workers` com
  `id = OWNER_ID`. Antes disso havia dois caminhos (`api._do_action` chamando o `DeviceManager` direto, e
  `_do_action_no_worker` falando o protocolo) e a divergência já tinha causado defeito visível (`desired_state`
  gravado só de um lado). `worker/executor.py` (o executor do AGENTE remoto) **não** virou o núcleo do caminho
  local — traria junto monitor, Appium e rodízio, que são exclusivos do central.
- **`backend/app/worker/agent.py`** — o laço do agente remoto: liga para o central (nunca o contrário, para
  atravessar NAT sem abrir porta), declara capacidades no `hello`, bate coração, obedece `dispatch`/`cancel`.
  Reconecta sozinho com espera crescente (`RECONEXAO_MIN_S`/`RECONEXAO_MAX_S`, agent.py:38-39).
- **Túnel** — a ligação até um worker remoto é um túnel SSH reverso mantido fora do processo do backend (tarefa
  agendada na máquina do worker); o central só enxerga `127.0.0.1:<worker_port>` (ver `main.py:307-323`,
  `despachante`, e a seção de arquitetura). Detalhe de inscrição, enrolamento e do mapa de portas do túnel está
  em [`../worker.md`](../worker.md#o-túnel-como-componente-achado-179) — não repetido aqui.
- **`main.py:354-376`** — o canal do worker só existe quando `server.worker_port` (padrão `8010`,
  `config.py:103`) é diferente de zero; por omissão é `0` ("desligado", certo para parque numa máquina só) e o
  dono liga ao inscrever o primeiro worker. O socket do canal do worker é sempre aberto em `127.0.0.1`, nunca na
  rede, mesmo com `server.host` público — é o alvo do `-R` do túnel, não uma porta para expor.

## Escalonamento

- **`taskqueue/scheduler.py:522` (`_rotate`)** — liga aparelhos parados com tarefa na fila (FIFO) enquanto
  houver vaga; sem vaga, desliga UM aparelho ocioso por tick. Vagas são contadas por CONJUNTO
  (`pool(d)` = `None` para o host, `worker_id` para cada máquina remota): cada worker tem o próprio teto, em vez
  de todos disputarem um teto global único. A loja (`rt.store`) nunca é ligada/desligada pelo rodízio — quem a
  ligou a desliga.
- **`taskqueue/balanceamento.py`** (`distribuir(quantos, candidatos, servidores)`, puro — sem banco, sem
  aparelho, testável com números) — escolhe até N aparelhos para uma execução DISTRIBUÍDA (item 10.5, ver
  abaixo), em 3 regras: (1) só entra aparelho do app pedido, fora da loja, sem trabalho aberto, numa máquina
  conectada e fora de manutenção; desligado só entra se a máquina tiver vaga para ligá-lo; (2) a máquina menos
  carregada recebe o próximo (`carga = (trabalhando + já escolhidos) / capacidade`); (3) desempate por: já
  ligado > menos CPU em uso > mais RAM livre.
- **`Scheduler.servidor_lotado(rt)`** (`scheduler.py:406-418`) — frase de espera quando a máquina do aparelho já
  está no teto de "trabalhando ao mesmo tempo" (`worker_limits.max_working`); `None` quando cabe ou quando a
  máquina não tem teto próprio (só o geral vale).
- **`Scheduler.servidores()`** (`scheduler.py:420-449`) — a foto de cada máquina (capacidade, carga, vagas
  livres, CPU, RAM livre) usada tanto pelo balanceamento quanto pela tela Limites.
- **Reserva de RAM por boot** (ADR-027, evolução de desempenho):
  - **No worker** (`worker/executor.py`): conferir a RAM e reservar o custo da imagem é um passo só, sem `await`
    no meio, e a reserva é descontada da guarda. Com `boot_parallelism` > 1, duas admissões não gastam a mesma RAM.
    A reserva sai no fim do boot, dê certo ou não; cancelamento com o emulador ainda no ar não libera.
  - **No central** (`workers/registry.py::WorkerCapacity`): a admissão usa `mem_available_mb`, `mem_limit_mb` e
    `reserved_mb` da batida (contrato C6). Batida velha ou sem RAM medida **recusa com motivo escrito**, em vez de
    tratar o desconhecido como ilimitado.
  - **Nos aparelhos desta máquina** (`DeviceManager._recusa_por_capacidade`): a guarda grava a reserva no instante
    em que admite (`_reservas`), antes de qualquer `await`, e desconta a dos OUTROS boots — reserva, ou a
    estimativa de quem boota com PID sem reserva, menos o RSS já medido, sem contar ninguém duas vezes. Antes, com
    `boot_parallelism` 2, os dois `_boot` passavam juntos (nenhum tinha PID ainda). A reserva sai no fim do `_boot`;
    se o emulador pode ter ficado vivo sem ser contado (cancelado ou fora do prazo com PID), fica órfã até o processo
    morrer, o aparelho ficar online ou o prazo do boot vencer (contado da admissão, como no agente). A órfã desconta
    a reserva menos o MAIOR RSS já visto do processo: o laço de métricas zera o RSS de quem sai de `booting`, e a
    RAM que o órfão já tem não pode ser contada duas vezes. Métrica `capacidade.reserva{resultado,motivo}`.
  - A leitura de recursos efetivos (cgroup v1/v2, `cpu.max`/cpuset e PSI no Linux) está em `devices/recursos.py`.
    O que não dá para medir, como o job object no Windows, fica `null`.
- **Desbravador** (`_waits_for_pathfinder`): numa execução com vários aparelhos, o primeiro aprende e os de mesmo
  grupo de compatibilidade do app (pacote, versão, assinatura, variante) esperam, até `ai.pathfinder_wait_s`, para
  repetir por receita.
  - A espera aparece no objetivo (`wait_reason: pathfinder`) e é medida (`pathfinder.espera_s`,
    `pathfinder.desfecho`).
  - Os que esperam são soltos na hora quando o líder falha, sai do ar ou vai para outro trabalho, ou quando ele já
    aprendeu.
  - `_pathfinders` fica só na memória: um reinício elege outro líder.

## Limites por servidor (item 10.5)

Antes, a tela Limites misturava o que é do parque inteiro com o que só vale para UM servidor, e o agendador só
tinha um teto global de "aparelhos trabalhando" — uma máquina podia ocupar o teto inteiro enquanto outra ficava
ociosa. `worker_limits` (migração `039_limites_por_servidor.sql`) guarda o que o DONO decidiu por máquina, pelo
painel; coluna `NULL` = "use o que a máquina declara" (o `worker.yaml` dela, via `hello`):

| Campo | Significado |
|---|---|
| `max_slots` | aparelhos ligados ao mesmo tempo naquela máquina (vagas de RAM) |
| `boot_parallelism` | emuladores ligando ao mesmo tempo |
| `max_devices` | teto de aparelhos EXISTENTES na máquina (a loja fica fora), conferido por `POST /api/instances`; NULL = sem teto; não vai na mensagem `Limits` ao agente (migração 050) |
| `max_working` | aparelhos TRABALHANDO ao mesmo tempo (objetivo em execução) — não existe hoje fora deste mecanismo |
| `min_free_ram_mb` | piso de RAM livre que a máquina mantém depois de ligar mais um |

O agente declara o valor da MÁQUINA (`workers.declared_boot_parallelism`/`declared_min_free_ram_mb`, mesma
migração) no `hello`; o painel mostra os dois lado a lado e oferece "voltar ao da máquina". A mensagem
`Limits` (`workers/protocol.py:217-231`) carrega `max_slots`/`boot_parallelism`/`min_free_ram_mb` (não
`max_working` — quem despacha trabalho é o central, então o teto de trabalho nunca precisa ir para o agente,
comentário em `registry.py:536-538`). **Quando ela é enviada:** na PRIMEIRA batida de coração de cada conexão
(`registry.py:316-323`), não junto do `welcome` — o agente lê o `welcome` como a resposta de um único `recv()`
do `hello`, e qualquer envio antes dele seria lido fora de ordem. Isto diverge do comentário em
`workers/protocol.py:220-223` ("chega logo depois do welcome"): o código manda depois, na primeira batida; ver
divergência registrada no adendo de [`../api-contract.md`](../api-contract.md).

Rotas: `GET /api/servers/limits`, `PUT /api/servers/{worker_id}/limits` (`api.py:2696-2736`).

## Controle manual e foco

`POST /api/instances/{id}/control/take` e `/control/release` (`api.py:2355-2372`,
`DeviceManager.request_control`/`release_control`) dão a UMA pessoa posse exclusiva de um aparelho por um prazo
(lease); enquanto durar, a IA não despacha objetivo naquele aparelho. `POST /api/instances/{id}/input`
(`api.py:2374-2384`, `DeviceManager.manual_input`) é o canal de toque/texto/tecla usado pela tela **Foco** do
painel (`frontend/src/features/focus`) e também pelo modo treinamento (`training/recorder.py`, ver
[`perfis-e-instagram.md`](perfis-e-instagram.md#modo-treinamento-itens-131133)). Controle expira por inatividade
(`DeviceManager._end_user_control`, chamado em `manager.py:883-884`) e devolve o aparelho à IA.

## Prévia e observação sob demanda (ADR-027)

- **Prévia** (`DeviceManager._capture_loop`/`_ciclo_de_previa`):
  - cada conexão do painel declara pelo WebSocket o que vê (`watch`: grade visível e foco, TTL de 5 a 60 s);
  - sem interesse, nenhum screencap de prévia, e a tela fica `paused`; vários espectadores dividem a mesma
    captura;
  - painel antigo, que nunca manda `watch`, conta como grade em todos;
  - o controle manual conta como foco, e o foco do `watch` renova o lease manual;
  - a grade não impede hibernação nem rodízio: só o foco e o controle manual contam (`rt.focused`);
  - volta atrás sem reinício: `PUT /api/settings {"preview_mode": "always"}`.
- **Tela sensível:** a prévia nunca mostra tela sensível. Vale para o frame marcador sem imagem, para `/frame` (404
  `sensitive_screen`), para a captura durante `type_secret` e para a VM-loja, que é sempre o marcador (ADR-014).
- **Observação para a IA** (`DeviceManager.observe(imagem=…)`):
  - a árvore vem primeiro, e a imagem só quando a política, o julgamento, a evidência ou uma divergência de
    receita pedem;
  - o PNG é decodificado uma vez;
  - a imagem tardia (evidência) nunca serve para coordenadas;
  - o login determinístico do Instagram lê só a árvore.
- **Exclusividade:** captura, observação e ações seguem passando pelo `rt.executor` do aparelho (uma trilha só).
- **Medição:** `captura.total`, `captura.evitada{motivo}`, `captura.ms`, `captura.bytes`, `codificacao.ms` e
  `observacao.ms` em `GET /api/desempenho`.

## Reparo automático

`despacho.remediar(s, instance_id, motivo)` (`commands/despacho.py:765`) decide o DEGRAU quando um aparelho com
`desired_state=online` degrada, contando o histórico de comandos `requested_by='system'` das últimas 24 h
(`DEGRAUS_DE_RESTART = 2`, `commands/despacho.py:760`):

1. 1º e 2º degrau: `restart`.
2. 3º degrau: `reset` (apaga os dados do AVD e sobe limpo) — só se o aparelho declarar o verbo e não for a loja.
3. Escada esgotada: o aparelho ganha `attention` "precisa de gente" e uma nova tentativa (`restart`) é agendada
   em até 6 h (`RETENTATIVA_APOS_ESCADA_S`) — nunca fica esquecido, mas também nunca repete sozinho fora da
   janela.

Cada degrau emite `instance.remediation` (evento, não efêmero) com `{degrau, verb, command_id, motivo}` — o que
faz o relatório de uso e o painel não confundirem reparo automático com comando manual.

## Capacidades — implementação e validação

| Capacidade | Implementação | Validação | Origem |
|---|---|---|---|
| Perfil de RAM por imagem do sistema | implementado | ambiente real (medição em host, `scripts/probe-image.ps1`) | `devices/perfis.py`; [`../relatorio-validacao.md`](../relatorio-validacao.md) §2.1, §7.1–7.2 |
| Hibernação por snapshot | implementado | ambiente real (WHPX, emulador 37.1.11) | `devices/emulator_backend.py`; relatorio-validacao.md §7.3 |
| Rodízio local (`_rotate`) | implementado | ambiente real (10 contas / 4 vagas) | `taskqueue/scheduler.py:522`; relatorio-validacao.md §7.4 |
| Worker local (`LocalWorker`) | implementado | automatizada (`test_worker_executor.py`, `test_contrato_de_worker.py`) + ambiente real para verbos de ciclo de vida no host central | `workers/local.py`; relatorio-validacao.md §13, aceite 1 |
| Worker remoto: `stop` | implementado | ambiente real (21/09, `worker-lan-01`, `c-20260921172219-9331e1` e outros) | `worker/agent.py`, `worker/executor.py`; relatorio-validacao.md §13, aceite 1 |
| Worker remoto: `start` | implementado | ambiente real, **sem sucesso observado** — único despacho (`c-20260921172322-6f7fdc`) terminou `uncertain` após 480 s e segue sem reconciliação | `worker/executor.py`; relatorio-validacao.md §13, aceite 1 e 8 |
| Worker remoto: `hibernate`/`wake`/`restart`/`reset`/`create` | implementado no agente (código) | não exercitada em worker remoto — nunca despachados | `worker/executor.py`; relatorio-validacao.md §13, aceite 1 |
| Distribuição de execução entre servidores por carga (10.5) | implementado | automatizada (`tests/test_limites_por_servidor.py`, 15 casos) | `taskqueue/balanceamento.py`, `taskqueue/scheduler.py`, commit `c0c982d`; plano-100 id 10.5 (proof `unit`) |
| Distribuição entre **dois workers reais** | não feito | não exercitada — só um worker inscrito (`worker-lan-01`) em 23/09 | relatorio-validacao.md §13, aceite 5 |
| Limites por servidor no painel (`worker_limits`) | implementado | automatizada (`tests/test_limites_por_servidor.py`) | migração 039; `workers/registry.py`; commit `c0c982d`; plano-100 id 10.5 |
| Controle manual / Foco | implementado | automatizada (`test_contrato_http.py::test_controle_manual_de_ponta_a_ponta_por_http`) + ambiente real em 19–21/09 (eventos 61807–61827) | `devices/manager.py`; relatorio-validacao.md §13, aceite 3 |
| Reparo automático em escada (restart→reset→"precisa de gente") | implementado | não confirmada em execução real desta rodada (mecanismo por histórico de comandos, sem teste citado no plano-100 para este trecho específico) | `commands/despacho.py:760-830` |
| Workers e controle manual compartilhados entre dois backends | não feito (limitação conhecida, achado #27) | não aplicável | [`../banco.md`](../banco.md#pendências-honestas) |

Backlog (não implementar aqui — registrar para priorização):

- Segunda máquina real para provar distribuição entre workers (aceite 5, `../relatorio-validacao.md` §13).
- Reconciliar `c-20260921172322-6f7fdc` (comando `start` remoto preso em `uncertain` desde 21/09).
- Despachar `hibernate`/`wake`/`restart`/`reset`/`create` a um worker remoto real ao menos uma vez cada.
- Persistir estado de worker e lease de controle manual (achado #27) para permitir dois backends hospedando o
  mesmo parque — ver [`../banco.md`](../banco.md#pendências-honestas).

## Provisionamento pela plataforma (27/09, onda D)

Até aqui uma instância nascia só de três jeitos: da configuração (`instances.count`), da adoção de um aparelho que o
worker anunciou, ou do verbo `create` sobre uma instância já declarada. Agora o servidor LOCAL cria uma instância
nova sem editar o `config.yaml`:

- `POST /api/instances` (`DeviceManager.provisionar`) insere a linha com `origin='dynamic'`,
  `worker_id = hosted_by = OWNER_ID`, id depois de `count` (`_proximo_id_dinamico`, nunca reaproveitado) e portas
  por `MAX(idx)+1`; os pedidos de `system_image`/`ram_mb` ficam em `instances.android_overrides` e
  `DeviceManager.android_de(rt)` os mescla sobre `cfg.instance_android` em todos os usos (criar, ligar, capacidades,
  hibernação).
- Com `create: true` (padrão) o comando `create` sai pelo despacho de sempre (cerca, outbox, `idempotency_key`) e a
  rota responde 202 com `command_id`; `start: true` encadeia a partida só depois do `create` `succeeded`.
- Recusas: worker remoto (`provisionamento_remoto_indisponivel`: o inventário do agente ainda é estático), teto
  `max_devices`, disco livre abaixo de `provisioning.min_free_disk_gb` (padrão 10, lido da última batida), chave
  já usada, e as do pré-voo do `create`.
- `DELETE /api/instances/{id}` só aposenta instância dinâmica, local, desligada, sem objetivo aberto, sem vínculo
  ativo e sem comando em voo: apaga o AVD e marca `retired_at` (a linha, o `idx` e as portas ficam, porque o
  histórico dos objetivos referencia o id). Aposentada some das listas e não volta no arranque.
- O remoto (verbo `provision` no protocolo, inventário mutável no agente) é rodada própria, com ADR.

Prova `simulated`: `backend/tests/test_provisionamento.py` e `test_provisionamento_migracao.py`. Criar um AVD de
verdade é ato no parque e exige autorização.

## Aparelho → personas (N:N, 28/09, onda C)

Um aparelho pode ter várias personas, uma por app (D2-a: duas contas do mesmo app no mesmo aparelho são recusadas
enquanto a troca de conta no Instagram for manual;
[ADR-043](../decisoes.md#adr-043--persona-nn-aparelho-vínculo-por-app-aparelho-principal-e-uma-conta-por-app-em-cada-aparelho)).
A porta de sessão recebe a persona do **objetivo** (`session_gate(rt, pacote, profile_id)`); sem ela, só a única
persona do aparelho serve, e duas sem escolha bloqueiam. O balanceamento (`Scheduler.candidatos_de`) também
desempata os aparelhos de UMA persona (`resolver_alvos`, política `one`; ver
[persona § Aparelhos e roteamento](persona.md#aparelhos-e-roteamento)). `GET /api/instances/{id}/personas` lista
quem está no aparelho.

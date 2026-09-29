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
  - O `config.yaml` do ambiente central não mudou; trocar a RAM é decisão do dono, com o procedimento no
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

`despacho.remediar(s, instance_id, motivo)` (`commands/despacho.py`) decide o DEGRAU quando um aparelho com
`desired_state=online` degrada. Conta o histórico de comandos `requested_by='system'` das últimas 24 h
(`DEGRAUS_DE_RESTART = 2`). O reinício por irq (`saude`) e o religar da reconciliação (`reconciliacao`) não contam como
degrau (ADR-055).

0. **Máquina saturada: espera** (`9348e9c`, item 21.16). Com a CPU desta máquina em
   `instances.remediation_host_cpu_max` (90%) ou mais, o aparelho local não sobe de degrau. Ganha o aviso "Reparo
   adiado: esta máquina está com N% de CPU…" e é reconferido em 10 min (`ADIAMENTO_POR_HOSPEDEIRO_S`). O convidado
   "degradou" porque a máquina não tem CPU, e reiniciar é o momento mais pesado dele. Foi o que levou o android-01 ao
   `reset` em 29/09 ([K-058](../conhecimento/aprendizados.md#k-058)). Aparelho de worker não entra nessa conta; 101
   desliga (a suíte usa 101).
1. 1º e 2º degrau: `restart`.
2. 3º degrau:
   - **com conta vinculada:** "Precisa do dono" + `stop` (parar não apaga nada; o estado desejado vira `stopped`, e a
     escada não volta sozinha);
   - **com conta travada logada (quarentena):** "Precisa do dono" + `stop` direto, sem os `restart`;
   - **sem conta nenhuma:** `reset` (apaga os dados do AVD e sobe limpo), só se o aparelho declarar o verbo e não for a
     loja.

   Nunca `reset` automático em aparelho com conta ([ADR-055](../decisoes.md#adr-055--proteção-de-contas-a-conta-travada-para-sem-ser-tocada-o-aparelho-entra-em-quarentena-uma-conta-por-alvo-e-nenhum-reset-com-conta)).
3. Escada esgotada: o aparelho ganha `attention` "precisa de gente" e uma nova tentativa (`restart`) é agendada
   em até 6 h (`RETENTATIVA_APOS_ESCADA_S`) — nunca fica esquecido, mas também nunca repete sozinho fora da
   janela.

Cada degrau emite `instance.remediation` (evento, não efêmero) com `{degrau, verb, command_id, motivo}` — o que
faz o relatório de uso e o painel não confundirem reparo automático com comando manual. O adiamento por máquina
saturada não emite nada além do aviso no cartão.

### Saúde do convidado: pressão e interrupção acumulada (ADR-053)

Cada sonda de saúde que acha o framework vivo (`DeviceManager.conferir_saude` → `_conferir_pressao`) lê load, memória e
a linha `cpu` de `/proc/stat` do convidado (`adb.guest_pressure`). **Pressão** vira aviso no cartão, sem degradar: load
acima de 4× as vCPUs ou menos de 8% de RAM livre em duas sondas seguidas, e o texto diz o recurso que disparou
("Convidado sob pressão de CPU", "de RAM" ou "de CPU e RAM") com o remédio de cada um; antes era sempre "mais RAM", o
remédio errado para o android-06 de 28/09, que tinha RAM sobrando. **Interrupção acumulada** (`_conferir_interrupcoes`):
a fração de CPU em irq+softirq entre duas sondas; com o aparelho ocioso (ninguém no controle, nem a IA nem uma pessoa)
acima de 15% em 3 sondas seguidas (`IRQ_OCIOSO_MAX`, `IRQ_SONDAS`), a plataforma abre um `restart` rastreável
(`on_health_restart` → `AppState._reiniciar_por_saude`, `requested_by='system'`), no máximo 1 a cada 6 h por aparelho;
se não resolver, fica só o aviso "Convidado com interrupções acumuladas". É um caminho à parte da escada acima: nunca
chega a `reset`, que apagaria a conta real logada. Medido em 28/09: irq ocioso de 21% (68 h no ar) e 90% (44 h) voltou a
~2% depois do `restart` ([K-050](../conhecimento/aprendizados.md), [relatório §21](../relatorio-validacao.md)).
Desde 21.13 cada fração vira `measurements(kind='irq')` (série em `GET /api/desempenho?irq_horas=`). A causa do
acúmulo segue aberta (item 21.15): em 29/09 o android-06, com o Instagram em primeiro plano, foi de ~4% (2,7 h no ar)
a ~8% (6,4 h), e o android-04, no launcher, ficou em 2–3% (`data\logs\irq_convidados.csv`).

### Apps de segundo plano (item 21.15)

O preparo do aparelho da automação (`Adb.prepare_for_automation`, no boot, no wake e na readoção) desativa com `pm
disable-user --user 0` os apps do Google que sobem sozinhos e não servem à automação (`devices/apps_de_fundo.py`,
`e2b54a0` + `b5036ec`; [K-059](../conhecimento/aprendizados.md#k-059)). Num convidado de 2 GB eles tomavam cerca de
200 MB e levavam à compactação de memória.

- **A lista** é `android.desativar_apps`, com 13 pacotes de padrão (app Google, Android System Intelligence,
  Mensagens, YouTube, YouTube Music, Gmail, Bem-estar digital, Fotos, Maps, Agenda, Drive, Google TV, Meet). Por
  aparelho: `instances.overrides.<id>.desativar_apps`. Pacote que a imagem não tem é ignorado.
- **Protegidos**, recusados na carga da configuração: Play Store, GMS, GSF, WebView, teclado, launcher, SystemUI,
  Chrome, `io.appium.*`, Configurações, shell, provedores e instalador. Também o app alvo: o declarado em
  `app/conhecimento/apps/` (o Instagram), o de `apps` e `contas.sessao`. O do catálogo do banco sai sozinho, por
  aparelho.
- **Idempotente e reversível.** Uma leitura primeiro, e `pm` só no que muda. O marcador
  `/data/local/tmp/central-apps-desativados.txt` guarda o que o preparo desativou. Tirado da lista, o pacote volta com
  `pm enable`; `[]` devolve tudo. O que a pessoa desativou à mão fora da lista fica como está.
- **Dono único:** o central, inclusive nos aparelhos dos workers, que ele prepara pelo túnel. O agente do worker não
  mexe em app. A loja recebe `[]`; o celular físico não é tocado.
- **Sem derrubar o portão:** um `AdbTimeout` nesse passo (prazo de 12 s) vira `incerto` no log e não invalida a
  prontidão (K-031). O preparo seguinte tenta de novo.
- **Registro:** desativar ou reativar algo vira evento `instance.updated` "apps de fundo — N desativado(s): …".

Real, 29/09 (depois do deploy de `f497075`): 11 desativados no android-01 e no android-06, nenhum rodando depois;
`MemAvailable` de 670–960 MB para 974–1054 MB; "Verificar conta" do andre no android-06 ok
([relatório §23](../relatorio-validacao.md)).

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

## Rede por aparelho (ADR-056, Fase 25)

[ADR-056](../decisoes.md#adr-056--rede-por-aparelho-vpn-dentro-do-android-com-proxy-encadeado-saída-medida-e-revisão-da-cláusula-de-rede-do-adr-055):
a saída de rede é propriedade do aparelho, configurada pela plataforma e **medida**. Endpoint, configuração e IP de
saída observado são campos distintos: configuração diferente não prova IP diferente. O modelo e as rotas (item
25.2) moram em `backend/app/devices/rede.py`, sobre as tabelas da migração 057 (`network_profiles`,
`device_network`, `network_measurements`).

**Perfis.** `POST /api/network/profiles` recebe `name`, `kind` (`vpn`|`proxy`), `protocol` (`wireguard`|`singbox`
para VPN, `http`|`socks5` para proxy; o par é conferido), `endpoint_host` (nome, IPv4 ou IPv6), `endpoint_port`,
`params` (JSON sem segredo) e, opcional, `secret`. O segredo chega UMA vez, vai ao cofre na mesma transação do
INSERT e não volta: a resposta é o `NetworkProfileDTO`, só com `has_secret`. O corpo é lido à mão
(`rede.ler_cadastro`) para o segredo sair antes da validação, e o 422 sai sem `input`: o padrão devolveria o corpo
inteiro num campo faltando. `params` com chave de segredo, ou com o que a redação por formato mascararia (URL com
senha, par chave/valor aninhado), é recusado. Cofre fechado: 503 `secret_store_unavailable`, sem perfil gravado.
`GET` lista com `in_use` (os aparelhos que pedem o perfil); `DELETE` responde 409 `network_profile_in_use` com os
`instance_ids` e, sem uso, apaga a linha e o segredo juntos.

**Atribuição.** `POST /api/network/assign` com `instance_ids` explícito (o parque inteiro nunca é inferido),
`vpn_profile_id`, `proxy_profile_id` e `policy` (`livre`|`exigida`|`exigida_com_bloqueio`). Campo omitido fica como
está em cada aparelho; `null` num perfil o tira. `dry_run` devolve a prévia por aparelho (`from`, `to`, `reapply`,
`outcome`, `code`, `reason`, `warnings`) sem gravar; sem ele é **tudo ou nada**: qualquer recusa responde 409 com a
prévia inteira. Recusas: a loja (`store_instance`), o aparelho em quarentena (`aparelho_em_quarentena`, ADR-055),
política exigida sem perfil (`policy_without_profile`), bloqueio sem VPN (`policy_without_vpn`) e aparelho com conta
real vinculada cuja **saída** muda sem estar em `confirm_real_account` (`real_account_confirm_required`). A
confirmação é por aparelho (ADR-056 §7), não um booleano do lote. Trocar só `livre` ↔ `exigida` não muda o aparelho:
nem pede confirmação, nem ganha revisão, nem desfaz a verificação. Mudar VPN, proxy ou o bloqueio incrementa
`desired_rev` e volta o aparelho a `pendente`. Tirar tudo de um aparelho que nunca aplicou nada apaga a linha. A
prévia avisa quando o proxy global da 041 está gravado no aparelho.

**Estados, só com evidência.** `pendente`, `configurado`, `conectado`, `trafego_verificado`, `parcial`. Atribuir e
reaplicar só regridem. `registrar_observacao(rev, estado, evidencia)` é o único caminho para `configurado` e
`conectado` (evidência lida do aparelho obrigatória; grava `applied_rev`); observação de revisão que não é a pedida
é descartada, como no `proxy._fechar`. `registrar_medicao(medicao, rev)` acrescenta ao histórico sempre, grava a
última saída medida (`egress_ipv4`, `egress_ipv6`, `verified_at`) e decide: `trafego_verificado` só com a revisão
pedida aplicada, IP de saída medido, cada app de `rede.apps_exigidos` medido `ok` (sem app exigido, ao menos um app,
todos `ok`) e, na política com bloqueio, `leak_blocked` verdadeiro; IP medido com algo faltando é `parcial`, com o
que falta no `detail`; sem IP, o estado fica. Vocabulário de `per_app`: `ok` (saiu pela rede pedida),
`fora_da_rede` (vazou), `falhou`, `nao_medido`.

- **IP de saída é o público.** `NetworkMeasurementInput` recusa endereço que não é global (`is_global`): o NAT do
  emulador (10.0.2.15), a interface do túnel, loopback, rede local, CGNAT, link-local, ULA e as faixas de
  documentação são interface ou configuração, não a saída vista de fora (ADR-056 §1, T7). A sonda do 25.5 trata a
  recusa como medição que falhou.
- **Apps exigidos.** `apps_exigidos(state, id)` são os pacotes das contas das personas com vínculo ativo no
  aparelho (o vínculo com `app_id` restringe àquele app). Medir só o navegador num aparelho com conta do Instagram
  dá `parcial` ("apps do aparelho não medidos"). A lista sai em `required_apps` na visão por aparelho. Vínculo
  feito DEPOIS da verificação não desfaz o estado: a porta da tarefa (25.6) confere a lista de hoje contra o
  `per_app` da medição que verificou (`rede.apps_sem_prova`) e, faltando app, segura e manda medir de novo.
- **Sem corrida com o pedido.** Observação e medição leem, decidem e gravam numa transação, e a gravação exige a
  revisão lida (`AND desired_rev=?`, e na medição também `applied_rev` e `state`). Uma atribuição ou reaplicação que
  chegue no meio deixa a observação descartada e a medição só no histórico; o aparelho fica `pendente` com a revisão
  nova, nunca `trafego_verificado` com `applied_rev < desired_rev`. A reaplicação incrementa `desired_rev` no banco.

**Verificar e reaplicar.** `POST /api/network/devices/{id}/verify` e `/reapply` respondem 202 com
`executed: false`: registram o pedido e não fingem aplicação (quem aplica é a convergência do 25.4 e quem mede é a
sonda do 25.5, no próximo ponto seguro; `…/apply` executa já). Reaplicar é revisão nova (`applied_rev <
desired_rev`, durável). Verificar não muda o estado; o pedido fica no `detail`, no evento e na memória da
convergência (`ConvergenciaDeRede.pedir_verificacao`), que roda a sonda na próxima varredura com o aparelho livre ou
antes da tarefa, refazendo o teste de vazamento da revisão. Um reinício do backend perde o pedido em memória, mas a
readoção (`ligou`) mede de novo quem ainda não está verificado. Loja, quarentena e aparelho sem rede pedida
(`nothing_requested`) são recusados.

**Visão por aparelho.** `GET /api/network/devices`: por aparelho do parque (a loja fica de fora), `network`
(`DeviceNetworkDTO` ou `null`), `effective_state`, `legacy_proxy`, `restriction` (a frase da quarentena),
`real_account`, `required_apps`, `pending` (`aplicar`|`verificar`), `last_measurement` e `egress_shared_with` (os
outros aparelhos com a mesma última saída medida, v4 ou v6: aviso, não bloqueio). O proxy da 041 é lido
como `configurado` **no máximo** (`applied` com proxy), `pendente` nos outros estados, e só vale quando não há
linha em `device_network`.

**Ponto de extensão.** `rede.pendencias(state)` lista os aparelhos com `falta: aplicar` (revisão pedida fora do
aparelho, ou regressão a `pendente`) ou `falta: verificar` (aplicado, sem tráfego verificado), sem loja nem
quarentena: é a visão de conjunto (painel, matriz). A convergência do 25.4 decide aparelho a aparelho pelo `state`
da linha (abaixo). A reverificação de quem já está `trafego_verificado` é a conferência periódica do 25.4 (só
regride); medir o tráfego de novo é a sonda do 25.5, a pedido. Evento `network.updated` (persistido) a cada mudança, com ids, política,
revisão e estado; nunca segredo nem `secret_ref`.

**Segredos de rede (25.3, ADR-056 §5).** O segredo de um perfil sai do cofre num único ponto,
`security/segredo_de_rede.py::segredo_no_convidado`, o segundo consumidor de `SecretStore.get_secret` depois do canal
sensível (a lista de quem chama é conferida por `tests/test_segredo_de_rede.py`). Ele recebe o `profile_id`, nunca
uma `secret_ref`: a referência vem da linha de `network_profiles`, então a senha de uma conta não sai por ali. É um
bloco `with`: `montar(segredo)` monta o conteúdo do arquivo (a configuração do cliente com a chave dentro) ainda lá
dentro; a entrega grava um arquivo 600 em `/data/local/tmp/rede/<nome>` (`adb.DIR_PRIVADO_NO_CONVIDADO`, nome em
`NOME_PRIVADO_RE`) pelo stdin do `adb exec-in` (`Adb.gravar_arquivo_privado`, o padrão, em bytes: sem `\r\n`) ou por
arquivo temporário do host empurrado (`Adb.enviar_arquivo_privado`, `chmod 600` em seguida; o temporário é apagado
antes do bloco de quem chama começar). O tamanho gravado é relido (`exec-in` não devolve o código do `cat`), e o
arquivo do convidado é apagado ao sair do bloco, com erro ou sem; se não sair, `SegredoDeRedeError` diz onde ficou.
Quem chama recebe só `EntregaDeSegredo` (perfil, serial, caminho, modo), sem tamanho nem hash. Falha de montagem ou de
transporte vira mensagem fixa, sem a exceção original encadeada. O log diz que houve entrega e limpeza; evento e
evidência não recebem nada. Quem manda o cliente importar o arquivo é o 25.4, dentro do bloco; se o cliente só ler
de outra pasta, a pasta muda em `adb.py`, não num `shell` montado por quem chama.

**Aplicação e convergência (25.4).** O cliente é o sing-box (SFA, `io.nekohasekai.sfa`), escolhido pela medição 25.1
(o único que compõe VPN e proxy no mesmo `VpnService`). Três módulos:

- `devices/rede_aplicacao.py` é a **receita medida** no android-05 em 29/09, pela porta `AparelhoDaRede` (shell, árvore
  de tela, toque, `adb reverse`): `cmd appops set <pkg> ACTIVATE_VPN allow` (relido); `am force-stop` (a importação
  precisa da VPN desligada); o perfil do aparelho servido **uma vez** por um HTTP efêmero em `127.0.0.1` do central
  (`ServidorDeUmaVez`: os bytes ficam em memória, o caminho tem token, um GET e fecha), montado e entregue pelo
  consumidor restrito (`segredo_de_rede.segredos_entregues`); o aparelho local chega por `10.0.2.2` (medido), o do
  worker por `adb reverse tcp:P tcp:P` (desfeito ao fim; a prova é do 25.7); `am start …
  sing-box://import-remote-profile?url=…#plataforma-<id>-r<rev>`; os toques achados pelo **texto** na árvore da
  plataforma e só no pacote do cliente ("No, thanks" na primeira execução, "OK", "Create", nessa ordem); espera o GET;
  `sync`; `settings put secure always_on_vpn_app <pkg>` e `always_on_vpn_lockdown` 1 só com `exigida_com_bloqueio`
  (relidos); apaga `/sdcard/Android/data/<pkg>/files/crash_reports/` (a queda do cliente grava a configuração com a
  chave ali); `sync`. A URL de uso único não entra em evidência, comando nem evento. O perfil do cliente
  (`config_do_cliente`, o do piloto): `tun` com `strict_route` (IPv6 inalcançável em vez de vazar), DNS sequestrado e
  resolvido pelo túnel, proxy com `detour: wg-out` quando há VPN; com proxy HTTP o UDP que não é DNS é recusado (T3).
  O desfazer é o espelho (`settings delete`, lockdown 0, `force-stop`, relatórios apagados, `sync`). A leitura
  (`observar`) é uma ida só ao aparelho, **como uid 2000** (`id -u`; root é recusado: o bloqueio não cobre o uid 0):
  always-on, lockdown, `tun0`, `ni{VPN CONNECTED` e "Lockdown filtering rules" no `dumpsys connectivity`, relatórios de
  falha, `pm path` e o `uptime`.
- `devices/rede_convergencia.py` decide **quando**, sempre num ponto seguro (o aparelho livre, pela fila dele) e com
  rastro: aplicar, desfazer e conectar são o comando `device.network` (`despacho.comando_no_trabalho`, que conduz o
  comando dentro de um trabalho que já tem o aparelho); o reinício é um comando `restart` (`pedir_ciclo_de_vida`,
  `requested_by: rede`), pedido depois que o trabalho solta o aparelho e sem objetivo no meio — exceto o objetivo
  suspenso entre etapas pela própria porta da rede (`running` com `wait_reason='rede'`, item 25.6), que espera
  justamente esse reinício (contá-lo como ocupado travava os dois: a rede esperava o objetivo e o objetivo, a rede);
  o worker ocupado continua segurando. Por estado da linha:
  `pendente` → aplicar (plano, chave do aparelho e par no servidor, cliente pela versão promovida na loja, receita) ou
  desfazer (pedido vazio) → `configurado` e o reinício (always-on e bloqueio só valem no boot); `configurado` →
  conectar (espera o `tun0` até `rede.espera_tun_s` contados do boot; no ar → `conectado` com a evidência lida e a
  última conexão do par no log do servidor; sem boot desde a configuração → reinício; reiniciou e não subiu → novo
  reinício; o teto `rede.reinicios_max` conta os reinícios PEDIDOS na revisão — aceitos ou recusados, com boot
  detectado ou não —, e passado ele a linha vai a `pendente` com erro e entra na espera crescente das falhas. Sem
  boot detectado, o erro diz isso e o que fazer: um aparelho que a plataforma não reinicia de verdade (o celular sem
  worker, em que o `restart` só solta e readota a sessão, 25.7) ou cujo `uptime` não veio precisa ser reiniciado por
  fora, e depois Reaplicar); `conectado`/`parcial` → verificar (a sonda de
  saída do 25.5, abaixo: relê como o conferir e, com o túnel no ar, mede e grava); `trafego_verificado` → conferir (ao
  ligar, ao acordar, na readoção depois do reinício do backend ou do worker, e a cada `rede.deriva_s` na varredura):
  configuração que sumiu → `pendente` (reaplica), túnel caído com a configuração no lugar → `configurado`
  (reinicia). Conferir só regride; `trafego_verificado` continua sendo só da medição. Falha não se repete às
  cegas: espera em memória de 5, 15, 45 e 60 min (um reinício do backend dá mais uma chance); `POST …/apply` passa por
  cima. Wipe, reset ou outro aparelho físico atrás do id (`on_device_wiped`, `on_disk_erased`) regridem a linha a
  `pendente`; com o pedido vazio, a linha sai. Desfeito e conferido depois do reinício, a linha também sai (nada
  pedido, nada aplicado). Quem chama: `vitrine.trabalho_ao_ligar` (a rede vai primeiro, no mesmo trabalho da
  reobservação e da entrega dos apps; `motivo=ligou` ao entrar no ar e `varredura` na passada de 60 s) e a **porta da
  rede** do scheduler (contrato C4, ligada em `state.py`; regras abaixo, em "Portão de rede"). Loja e quarentena
  ficam fora (a quarentena tem a porta dela).
- `devices/rede_servidor.py` é o **sing-box do central** (decisão P2): processo do usuário gerenciado pela plataforma
  (sem serviço do Windows, sem driver, sem NAT, sem mexer em rede ou firewall), executável em `rede.servidor.binario`.
  Sobe quando algum aparelho pede um perfil de VPN com `params.servidor: "central"` e para quando ninguém pede; morre
  com o backend hospedeiro (a réplica de API não o toca) e um órfão de queda é adotado só se o PID, o executável e o
  caminho da configuração forem os nossos. Um par por aparelho: chave X25519 gerada pela plataforma
  (`segredo_de_rede.gerar_chave_wireguard`: a privada vai direto ao cofre, a pública e o endereço ficam em
  `network_keys`, migração 058), estável, com o menor endereço livre de `rede.servidor.sub_rede` (o servidor é o
  primeiro). A configuração (chave do servidor e senhas do proxy do central) é gravada por
  `segredo_de_rede.gravar_configuracao_do_servidor` em `data_dir/rede/servidor/servidor.json`, numa pasta e num
  arquivo com ACL só do usuário (`icacls` sem herança; sem ACL, nada é gravado), e o processo recebe só o caminho
  (`run -c <arquivo>`). Reinicia quando a assinatura muda (pares, usuários do proxy, portas, sub-rede, chave pública;
  sem segredo) e apaga a configuração ao parar. **Regras de rota** (a correção da medição, que mostrou o endpoint
  reescrevendo `10.66.0.1:<p>` para `127.0.0.1:<p>`: qualquer par alcançava a API, o adb e os consoles):
  `localhost` por nome é recusado; o destino pedido ao proxy é resolvido antes das faixas; do túnel só passa a porta do
  proxy do central (em `127.0.0.1` ou no endereço do servidor); loopback, a sub-rede do túnel, RFC 1918, link-local,
  CGNAT, multicast e as faixas reservadas (IPv4 e IPv6) são recusados para o túnel **e** para o proxy; o resto sai pelo
  `direct`. O log em nível `info` é a evidência: `ultima_conexao(10.66.0.N)` lê `inbound connection from` na cauda,
  e o log é renomeado no início seguinte a `rede.servidor.log_max_mb`.

Rotas do 25.4: `POST /api/network/devices/{id}/apply` (202: o passo que falta, já, pela fila do aparelho; fora do ar
responde `executed: false` e aplica quando ligar; ocupado ou com comando de ciclo de vida em voo, 409 `device_busy`;
as recusas de `verify`/`reapply` valem igual) e `GET /api/network/server` (se roda, PID, assinatura, pares com endereço,
chave **pública** e última conexão, usuários do proxy, `detail`; nenhum segredo). `reapply` e `assign` continuam só
registrando: quem executa é a convergência. **Todo desligamento sincroniza**: `emulator.stop_process` roda `adb shell
sync` (até 20 s, sem impedir o desligamento) antes do `emu kill` — o `restart` era um corte de energia e perdeu o
perfil importado 26 s antes (25.1); vale para qualquer dado recém-gravado e para o agente do worker quando for
atualizado. A provisão termina em `sync` de qualquer jeito.

**Sonda de saída (25.5).** Mede de dentro do aparelho, **como uid 2000** (cada ida confere `id -u`; root é
recusado), com o `nc` do Android — o ajuste do plano registrado no handoff troca o app de QA estendido pelo shell. Os
comandos e as leituras são só stdlib, em `devices/sonda_rede.py` (vão no agente do worker); a orquestração é
`devices/rede_medicao.medir`; quem decide quando é o passo `verificar` da convergência (comando `device.network`,
`acao: verificar`), com `conectado` ou `parcial`: ao ligar, a pedido, pela porta da tarefa (no máximo a cada 30 s) e
na varredura quando vence `rede.deriva_s` ou, no `parcial`, `rede.sonda.reverificar_s` (padrão 600 s; depois de uma
medição que não verificou, a porta também espera esse tanto). Antes de medir, relê como o conferir: deriva regride e
nada é medido. O que se mede:

- **IP de saída v4 e v6**: HTTP/1.0 a um eco de IP na porta 80 (`rede.sonda.hosts_ipv4`, padrão `api.ipify.org` e
  `ipv4.icanhazip.com`; `hosts_ipv6`, `api6.ipify.org` e `ipv6.icanhazip.com`: a família vem do host), com o stdin
  aberto por `sleep` (sem ele o `nc` fecha antes da resposta, medido). Vale o primeiro host que devolver HTTP 200 com
  IP **público** da família; sem IP, o `detail` guarda o motivo de cada host (`Permission denied` do bloqueio,
  `Timeout`, `No route to host` do IPv6 preso no túnel, que é o esperado com `strict_route`);
- **DNS e UDP**: o resolvedor da rede VPN (`DnsAddresses` das `LinkProperties` do `tun0`; no SFA, 172.19.0.2, o
  hijack), o DNS privado do Android, se um nome resolve, e dois datagramas de ida e volta pelo `nc -u`: DNS a
  `rede.sonda.udp_dns` (8.8.4.4; o cliente o sequestra e resolve pelo túnel) e NTP a `rede.sonda.udp_ntp` (o UDP que
  não é DNS — na cadeia com SOCKS5 o DNS seguia e o NTP se perdia). `udp_ok` só com os dois;
- **cobertura por app** (`per_app`): `pm list packages -U` dá o UID de cada app de `apps_exigidos` (Instagram,
  Outlook…), e `dumpsys netstats --poll` + `detail` (seção "UID stats", `tag=0x0`) dá os bytes por (tipo, uid). No
  delta da janela, `ok` = o que saiu pela física também passou pela VPN (tipo 17 = tipo 1, como o Chrome no 25.1;
  folga de 512 B ou 2%); `fora_da_rede` = saiu por fora (o uid 0 no 25.1: 868 B na física, 52 B na VPN); `nao_medido`
  = sem tráfego na janela ou app não instalado. A janela dos apps é ACUMULADA desde que o túnel conectou nesta
  revisão (a contabilidade é guardada no `conectar`; com o backend reiniciado depois disso, no primeiro `conferir`
  da readoção que acha o túnel no ar, ou, sem ele, na primeira medição): um vazamento visto não some na medição seguinte, e um app parado desde a última sonda não derruba um
  `trafego_verificado` a cada "Verificar". A do shell (`com.android.shell`, a própria sonda, sempre no `per_app`) é
  só a passada, e sem IP nenhum ela é `falhou`;
- **vazamento** (só com `exigida_com_bloqueio`; `rede_medicao.sondar_vazamento`): feito **antes** da medição e uma
  vez por revisão, com a VPN derrubada DE VERDADE. A sonda de IPv4 precisa sair pelo túnel primeiro (sem isso, nada
  é tocado e `leak_blocked` fica `None`: sonda que não funciona não prova bloqueio). Depois o **cliente VPN é
  parado** (`am force-stop`, como uid 2000), a leitura confere que o `tun0` sumiu, e a sonda roda de novo: só o
  `Permission denied` do Android prova o bloqueio (`leak_blocked` verdadeiro); um IP é vazamento (falso, "VAZOU" no
  `detail`); qualquer outra coisa (`Timeout`, nome que não resolve, `tun0` que continuou no ar) fica `None` com o
  motivo. Parar o SERVIDOR (o primeiro desenho) não serve: com o túnel no ar e o servidor fora, a sonda dá `Timeout`
  com o bloqueio ligado ou desligado (25.1, 18:06:50), e o teste gravava "bloqueado" presumido. O always-on não religa
  o cliente depois do `force-stop` (25.1, 18:07): sem o túnel de volta na releitura, a linha regride a `configurado`
  com o desfecho do teste e a convergência pede o reinício (o boot religa o cliente com o perfil selecionado, 25.1
  18:21); a medição vem depois do boot, com o teste guardado. Custo: **um reinício a mais por revisão**, além do da
  aplicação (e de novo a cada `POST …/verify`, que refaz o teste — o 202 avisa — ou depois de cada reinício do
  backend, porque o teste fica em memória: `network_measurements` não guarda a revisão). Sem o reinício garantido logo
  depois, o teste nem começa (`leak_blocked` `None`, sem guardar, e a medição seguinte tenta de novo): com um objetivo
  no meio do aparelho (rodando, esperando uma pessoa ou incerto — a mesma regra do reinício) e no celular sem worker,
  que a plataforma não reinicia de verdade (25.7; lá, `exigida_com_bloqueio` fica em `parcial` — use `exigida`). O teste vale também com servidor externo (não depende de parar servidor nenhum), e o servidor do central
  não é tocado: os outros pares não perdem a conexão. Depois do `force-stop`, nenhuma falha levanta erro: o desfecho
  volta a quem religa o cliente, para o aparelho nunca ficar sem VPN (e, com bloqueio, sem rede) esquecido.

A medição vai para `rede.registrar_medicao` (`method`: "sonda nc http/1.0 + netstats por uid (uid 2000)"), que decide
`trafego_verificado`/`parcial` pelas regras acima. **Comparação entre aparelhos**: a mesma última saída medida (v4 ou
v6) em outro aparelho entra no início do `detail` da medição ("aviso: a mesma saída medida em …"), num evento
`network.updated` de nível `warn` (`acao: saida_compartilhada`, `shared_with`) e em `egress_shared_with`. É aviso,
não bloqueio: sem provedor, todos saem pelo IP do central, e isso é o esperado; o aviso existe para ninguém ler
"perfis diferentes" como "saídas diferentes" (ADR-056 §1).

**Decisão: a sonda abre o app exigido só quando uma tarefa espera por ele** (substitui a de "não abre por padrão",
que travava; para o dono ratificar). App exigido é app de conta vinculada, e abrir o app é usar a conta (ADR-056 §7,
K-057). Sem tarefa esperando (varredura, `ligou`, pedido), com `rede.sonda.abrir_apps: false` (padrão), app parado
na janela fica `nao_medido` e o aparelho `parcial`. Mas com política exigida a porta segura TODA tarefa fora de
`trafego_verificado`, inclusive a que abriria o app: o app vinculado e nunca aberto depois do reinício que a própria
aplicação pede travava o aparelho para sempre. Por isso, quando a medição é disparada pela porta (`motivo='tarefa'`,
uma tarefa segurada no aparelho), a sonda abre o app sem tráfego na janela pela tela inicial dele, espera
`espera_app_s` e volta ao início — o que a tarefa faria, e só abrir (nenhum toque, nada publicado, nada enviado). O
`parcial` medido sem abrir não faz a tarefa esperar `reverificar_s`: a porta mede de novo já, abrindo, uma vez
(`_Memoria.medida_sem_abrir`); medido assim e ainda `parcial` (o app aberto não usou a rede), a espera volta a valer
e a frase da tarefa traz o porquê. `abrir_apps: true` abre também sem tarefa esperando. Resíduo conhecido: app
exigido NÃO instalado também fica `nao_medido` (não há o que abrir), e a porta da rede vem antes da porta do app que
o instalaria; só a entrega ao ligar (`vitrine.pendentes_ao_ligar`, quando o app está distribuído para o aparelho; o
reinício da própria aplicação passa por ela) o instala sem tarefa — sem isso, a tarefa espera com o motivo na frase.

**Portão de rede (25.6).** `Scheduler.rede_gate` (contrato C4) é `ConvergenciaDeRede.motivo_de_espera`, ligado em
`state.py`. Com política `livre`, nenhum efeito (nem com medição velha: nada vence e a varredura não remede por
isso). Com `exigida`/`exigida_com_bloqueio`, só libera `trafego_verificado` **que ainda vale**:

- **validade** `rede.validade_verificacao_s` (padrão 21 600 s = 6 h, de 300 s a 7 dias; sem 0) contada de
  `verified_at`, a data da medição que gravou a saída. Vencida, ou sem data, conta como inválida: a tarefa espera
  com "a verificação do tráfego venceu (…)" e a porta dispara a sonda (`verificar`) com o aparelho livre. A deriva
  (`rede.deriva_s`) relê configuração e túnel, mas não vê a saída mudar com o túnel no ar (IP público do servidor
  trocado, app saindo por fora): é isso que a validade cobre. A varredura adianta a remedição para os últimos 10% da
  validade, então a tarefa quase nunca espera por ela. Remedição sem IP deixa o estado como está (e o vencido,
  vencido): a porta espera `rede.sonda.reverificar_s` antes de repetir, com o motivo na frase;
- **apps**: a medição que verificou tem de ter `ok` para cada app de `apps_exigidos` de HOJE. A conta vinculada
  depois da medição segura a tarefa ("a medição … não cobre <pacote>") até a sonda provar o app dela. O C4 recebe só
  o aparelho: o "por app" é o das contas vinculadas a ele, não o pacote da tarefa;
- **quando é perguntado**: no despacho (`_tick`, antes das portas de app e de sessão) e **entre as etapas** de um
  objetivo em curso — na troca de app (`_portas_na_troca`, com as outras portas) e também entre etapas do MESMO app
  (`_porta_da_rede` no laço de `_work`). A queda observada no meio (a deriva, o wipe ou a reatribuição regridem a
  linha; a validade vence) suspende o objetivo antes da etapa seguinte: a etapa em curso termina, nenhuma tentativa
  é gasta, o objetivo fica com `wait_reason='rede'` e o motivo da convergência, e o despacho o retoma quando a rede
  volta. No meio da etapa não: o ponto seguro de dentro dela (`_stop_reason`) é da pessoa (pausa, controle,
  cancelamento) e da conta travada. De dentro do worker a pergunta é só leitura (o aparelho está em `workers`, e
  `run_device_job` não dispara nada); quem mede ou reaplica é o próximo `_tick`, com o aparelho devolvido. O objetivo
  suspenso assim continua `running` com `wait_reason='rede'`: o reinício que a convergência pede para ele passa
  (`vitrine.objetivo_em_andamento(..., exceto_quem_espera_a_rede=True)`), e, retomado, a espera é limpa;
- `pending: verificar` na visão por aparelho e em `pendencias()` também para o verificado que não vale mais.

- **releitura entre etapas** (`Scheduler.rede_releitura` = `ConvergenciaDeRede.reler_entre_etapas`, ligada em
  `state.py`): com o aparelho ocupado nada mais o relê (a varredura e a porta só agem com ele livre), então o worker,
  entre uma etapa e a seguinte e antes da porta, lê a rede do aparelho uma vez, como uid 2000, sem esperar o `tun0`
  (política exigida, linha `conectado`/`parcial`/`trafego_verificado`; no máximo uma a cada 30 s). Túnel caído ou
  configuração que sumiu regridem a linha como no conferir, e a porta logo abaixo segura a etapa seguinte. Falha na
  leitura não derruba o objetivo (a porta segue pelo que a linha diz).

Dentro de UMA etapa a queda não é vista: com `exigida_com_bloqueio` o próprio Android corta o tráfego fora da VPN
nesse intervalo, com `exigida` os apps podem sair pela física até a etapa terminar.

**Aparelhos do worker (25.7).** O aparelho de OUTRA máquina (o notebook `worker-lan-01`, ou um celular: o
`external` do gerenciador) recebe a rede como o local, **pelo central, via adb** (T10): o agente do worker não ganha
verbo, e nada muda na rota do host nem no túnel SSH. Duas diferenças, e só elas:

- **o perfil chega por `adb reverse tcp:P tcp:P`** (o HTTP de uso único em 127.0.0.1 do central; o `127.0.0.1:P` do
  convidado leva até ele pelo túnel do adb, que já alcança o aparelho) em vez do `10.0.2.2`, que no notebook é o
  próprio notebook; o mapeamento sai ao fim (`rede_aplicacao.provisionar`);
- **o cliente disca o servidor pela LAN**: `rede.servidor.endpoint_lan` (o IP do central na LAN, de config; vazio =
  a aplicação no remoto é recusada dizendo a chave, antes de gerar par) e a porta em que o servidor escuta de fato
  (`porta_wireguard`), nunca o `endpoint_host` do perfil (`rede_aplicacao.endpoint_do_central`). O endereço é
  validado (nem 10.0.2.2, nem loopback, link-local, multicast ou dentro da sub-rede do túnel) e não entra na
  assinatura do servidor: trocá-lo não reinicia o sing-box dos outros.

O UDP do notebook chega ao sing-box do central só se o **Firewall do Windows daqui** deixar — mexer em firewall é
proibido para a automação (P2). A plataforma só **lê** (`devices/rede_firewall.py`: um PowerShell sem nenhum verbo
que escreva, no `ActiveStore`, ~3,5 s) os perfis, a rede em que o `endpoint_lan` está e as regras do executável ou
da porta, e conclui na ordem do Windows: perfil desligado não filtra (`desligado`); regra de entrada habilitada que
bloqueia vence a que permite (`bloqueado` — a que o Windows cria quando o aviso "permitir acesso" fica sem
resposta); regra que permite UDP na porta ao executável (`liberado`); sem regra, vale a entrada padrão (`sem_regra`
com Block); não leu (`desconhecido`: fora do Windows, erro, prazo). Cada leitura vem com o **comando exato do dono**:

```powershell
New-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)' -Direction Inbound -Action Allow -Protocol UDP -LocalPort 51820 -Program '<caminho absoluto do rede.servidor.binario>' -RemoteAddress LocalSubnet -Profile <perfil da rede do endpoint_lan>
```

(e `Disable-NetFirewallRule -Name '<regra>'` para cada regra que bloqueia; a de política de grupo é dita, porque o
comando local não a desfaz). Na aplicação num remoto a convergência relê o firewall: `bloqueado`/`sem_regra`
**recusam** (`pendente` com o comando no `error`: aplicar assim deixaria o túnel "no ar" sem handshake e, com
bloqueio, o aparelho sem rede); `desconhecido` segue com a nota na evidência, e a conexão do par no log do servidor e
a sonda decidem. O `conectado` de um remoto sem conexão no log diz o endpoint e o estado do firewall. Com par remoto
no servidor, o laço de 60 s relê o firewall a cada 10 min (cache); sem par remoto, não lê.

Rotas: `GET /api/network/server` ganha `remote_access` (`lan_endpoint`, `wireguard_udp_port`, `remote_peers`,
`firewall` — a última leitura, `null` se ainda não lida: o GET não roda PowerShell) e `remote` em cada par;
`POST /api/network/server/firewall-check` relê já e devolve o `remote_access`. No painel Rede, o cartão "Servidor do
central" mostra o endereço da LAN, os aparelhos remotos, o estado do firewall e o comando, com "Conferir firewall".

Leitura real do firewall (só leitura, 29/09 22:12 UTC, central, worktree `evo3-d1` sobre `3823f4b`): Wi-Fi
192.168.1.81 no perfil **Public**, os três perfis ligados com entrada Block, nenhuma regra para o sing-box nem para a
UDP 51820 → `sem_regra`. **Procedimento do dono** para a prova num remoto:

1. `rede.servidor.endpoint_lan: 192.168.1.81` no `config/config.yaml` do central (confira o IP; DHCP muda) e reinicie
   o backend;
2. num PowerShell de administrador do central, o comando que `POST /api/network/server/firewall-check` devolve
   (hoje, com `-Profile Public`); relido, o estado vira `liberado`;
3. um aparelho do notebook (android-09…15), sem conta real ou com a autorização por aparelho (ADR-056 §7), com um
   perfil de VPN `params.servidor: "central"` e `POST /api/network/devices/{id}/apply`;
4. a prova: `inbound connection from 10.66.0.N` do endereço daquele aparelho no log do servidor
   (`GET /api/network/server` → `peers[].last_connection`), `conectado` lido como uid 2000 e a sonda (25.5) com a
   saída medida de dentro do aparelho; o túnel SSH e o adb seguem de pé (a árvore de tela responde).

Limites conhecidos: o perfil antigo fica dentro do SFA a cada reaplicação (sem root não há como apagá-lo; o novo fica
selecionado sozinho); o proxy SOCKS5 perde o UDP que não é DNS (medido); o servidor escuta a UDP 51820 em todas as
interfaces (o endpoint não tem campo de escuta); um perfil `wireguard` externo leva UMA chave e serve a um aparelho
por vez (P1); o aparelho do worker depende do `endpoint_lan` e da regra de firewall do dono (25.7), e uma regra com
`-RemoteAddress LocalSubnet` só vale para o notebook na mesma sub-rede do central.

A redação por formato (`security/redaction.py`) cobre a rede: a senha em `socks5://`, `socks5h://` e `socks4://`
(`usuario:***@`), `PrivateKey`/`private_key`, `PresharedKey`/`pre_shared_key`/`psk` e a chave de 44 caracteres
solta quando o texto fala de WireGuard (`[Peer]`, `wg set wg0 …`, log do cliente). A chave **pública** fica visível
(`PublicKey = `, `"peer_public_key": "`): é diagnóstico, e mascará-la faria o cadastro recusar um perfil legítimo.

| Capacidade | Implementação | Validação |
|---|---|---|
| Perfis com segredo no cofre, atribuição em lote, estados por evidência, legado da 041 (25.2) | implementado | `simulated` (`tests/test_rede_por_aparelho.py`, 10 casos); PostgreSQL `not_run` |
| Segredo de rede: consumidor restrito do cofre, entrega por stdin ou `push` com limpeza, redação de `socks5://` e chaves do WireGuard (25.3) | implementado | `simulated` (`tests/test_segredo_de_rede.py`, 19 casos, adb falso); entrega num aparelho real `not_run` (vem com o 25.4) |
| Aplicação e convergência: receita do SFA, comando `device.network`, reinício, conexão como uid 2000, deriva, wipe, desfazer, porta da rede, servidor sing-box do central com regras que fecham o central, chaves por aparelho (058), `sync` antes de desligar, teto de reinícios pedidos (com ou sem boot detectado, recusa conta) (25.4) | implementado | `simulated` (`tests/test_rede_aplicacao.py`, 30 casos; o teto em `::test_reinicio_sem_boot_detectado_tem_teto` e `::test_reinicio_recusado_tambem_conta_para_o_teto`, que falham sem a correção: aparelho e processo falsos; o HTTP de uso único é o único socket real, em 127.0.0.1); num aparelho real `not_run`: depende da versão promovida do SFA (25.10), de subir o sing-box de verdade (ACL, regras e `resolve` do 1.14.2) e do tempo real do boot até o `tun0` |
| Sonda de saída: IP v4/v6 por eco HTTP/1.0 como uid 2000, DNS da VPN, UDP (DNS e NTP), cobertura por UID pelo `dumpsys netstats`, vazamento com o cliente VPN parado (só `Permission denied` prova; religado pelo boot), abrir o app parado quando a tarefa espera, comparação entre aparelhos, passo `verificar` da convergência (25.5) | implementado | `simulated` (`tests/test_rede_sonda.py`, 17 casos; o vazamento em `::test_vazamento_so_com_o_cliente_parado_e_so_permission_denied_prova`: saídas remontadas no formato real com os números do 25.1, aparelho e processo falsos); num aparelho real `not_run`: depende do 25.4 real (SFA promovido, sing-box de verdade) e de um eco de IP alcançável; o `printf` com NUL do UDP e o formato do `netstats` do Android 14 só foram vistos no piloto, não por esta sonda |
| Portão de rede no scheduler: validade de `trafego_verificado` (`rede.validade_verificacao_s`), app de conta vinculada depois da medição, remedição disparada pela porta e adiantada pela varredura, suspensão entre etapas do mesmo app e na troca de app, releitura do aparelho entre etapas, reinício que sai com o objetivo suspenso pela rede, app nunca aberto sem travar a tarefa (25.6) | implementado | `simulated` (`tests/test_rede_portao.py`, 12 casos: aparelho de rede e servidor falsos, provedor por regras e aparelho de QA falso; os casos do scheduler falham sem a porta entre etapas, `::test_queda_do_tunel_no_meio_e_vista_entre_etapas_e_o_reinicio_sai` falha sem a releitura e sem a exceção do reinício, `::test_app_nunca_aberto_nao_trava_a_tarefa_com_politica_exigida` falha sem abrir o app pela porta); num aparelho real `not_run`: depende do 25.4/25.5 reais |
| Aparelhos do worker: perfil por `adb reverse`, endpoint da LAN (`rede.servidor.endpoint_lan`), leitura do firewall do central com o comando do dono, recusa com firewall fechado, `remote_access` e `POST …/firewall-check`, cartão no painel (25.7) | implementado | `simulated` (`tests/test_rede_worker.py`, 15 casos: aparelho remoto e leitura do firewall falsos; `RedePage.test.tsx`, 2 casos); a leitura do firewall rodou de verdade uma vez, só leitura (29/09, `sem_regra`); aplicação num aparelho do notebook `not_run`: depende de o dono configurar o `endpoint_lan` e criar a regra de firewall |

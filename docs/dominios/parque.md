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
- **Relógio do wake (RA-15, item 29.34)** — `_wait_boot` separa dois relógios: `t0` (o spawn) segue valendo para
  `boot_seconds`; o prazo tem início próprio. Ao acordar, ANTES do veredito do log o prazo é `boot_timeout_s` desde o
  spawn (carregar 2 GB devagar sob CPU alta ainda é mais rápido que descartar e bootar a frio; medido 03/10/2026: 4 de
  23 wakes passavam de 90 s só carregando). Com "Successfully loaded snapshot" (`_snapshot_verdict` True) o
  `wake_timeout_s` passa a contar DESSE instante — a hora em que o log foi lido, na resolução de `boot_poll_s`, não o
  carimbo do emulador. Veredito False: boot a frio com `boot_timeout_s` desde o spawn, como antes. A medição `boot`
  `kind=warm` traz `load_ms` (spawn → veredito lido; `null` se ninguém disse) e `snapshot_por` (`log`, `uptime` ou
  `null`). **Segunda fonte, o uptime (04/10):** a saída do emulador para o arquivo é bufferizada e "Successfully loaded
  snapshot" só chega ao log depois do boot (prova real contra no android-02: `load_ms` nulo com `/proc/uptime` = 3728 s
  12 min depois do wake). No primeiro `boot_completed` de um wake sem veredito, `Adb.uptime_s` lê o `/proc/uptime` do
  convidado; maior que o tempo desde o spawn mais `FOLGA_DO_UPTIME_S` (15 s) = snapshot carregado (`load_ms` = spawn →
  boot, `uptime_s` na medição, prazo do wake contado do boot). Só positivo: uptime pequeno ou ilegível não decide, e o
  negativo continua só pelo log (ele conta `snapshot_failures`). `load_ms` não é a mesma medida nas duas fontes: pelo
  log, spawn → linha lida; pelo uptime, spawn → `boot_completed`, com o boot depois do carregamento. Quem agregar
  filtra por `snapshot_por`. Depois do veredito pelo uptime o log não é lido no laço; na medição, se o log disser que o
  `poc_hib` foi recusado, a medição leva `log_contradiz: true` e sai um aviso, sem mudar o aparelho (29.76). Como o
  log é bufferizado, há uma segunda leitura `LOG_CONTRADIZ_RELEITURA_S` (120 s) depois, só para o mesmo boot (mesmo
  deslocamento do log), que só avisa. Nos logs
  reais, as 7 recusas do `poc_hib` seguiram em boot a frio (50 a 80 s), nunca em outro snapshot. Prova real do
  uptime em 04/10, deploy 30: wake do android-02, `load_ms` 15266, `snapshot_por` `uptime`, `uptime_s` 10596,3.
  O wake do worker remoto não tem
  prazo próprio de 90 s (`worker/executor.py::_v_start` usa `boot_timeout_s`): sem mudança. Aceite real em 7 dias: wake
  > 90 s e "snapshot descartado" = 0 (`not_run`).
- **Admissão por CPU e prazo do preparo pela carga (RA-4, item 29.33)** — a admissão só olhava RAM, e o preparo pós-boot
  (`prepare_for_automation`) tinha 60 s fixos (40 s no `shell` interno): sob host saturado por outros boots em voo um
  preparo sadio estourava, a tentativa ficava incerta e descia o degrau de reparo (3 episódios, 1 até o reset). Agora:
  (1) `android.max_cpu_percent_before_boot` (padrão 85, `100` desliga) segura o boot novo — no host,
  `_recusa_por_capacidade` recusa com a frase "CPU do host no limite", `capacidade.reserva{motivo=cpu}` e a espera
  crescente da RAM (a RAM, se também faltar, é o texto); num worker, `WorkerCapacity.sem_recurso(limiar)` compara a CPU da
  última batida (as três portas do `scheduler`: rodízio, `servidores`, espera do remoto). CPU desconhecida (`None`:
  agente antigo, laço de métricas sem amostra) e batida velha (já segurada pelo próprio motivo) nunca recusam por CPU.
  (2) O preparo já rodava só depois de `boot_completed` + interface, então o relógio dele nasce do "Boot completed"; o
  que faltava era a carga, e o prazo agora é proporcional a ela: ×1 até 50 % de CPU, linear até ×3 em 100 %
  (`adb.fator_de_carga_do_preparo`; 60 → 180 s no executor e 40 → 120 s no `shell` do adb, que crescem JUNTOS, pelo
  `Adb.prazo_do_ajuste_s`). O estouro continua sendo "efeito incerto" — só que agora para o aparelho que de fato não
  responde. O agente do worker aplica a mesma regra com a CPU da máquina dele. (3) A medição `boot` ganha
  `host_cpu_percent` (CPU do host quando o boot começou; `null` se não amostrada) e `boots_em_voo` (outros boots nesta
  máquina nesse instante); a medição `capacity` da recusa ganha `motivo` e `host_cpu_percent`. Sem migração. Prova
  `simulated` (`tests/test_admissao_por_cpu.py`); aceite real em 7 dias: zero degrau de reparo por prazo e menos boots
  > 120 s (`not_run`). Efeito colateral a vigiar: um host que fica ACIMA do limiar de forma estável nunca sobe boot novo
  (a mensagem diz por quê); suba o limite ou use `100` se a medição real mostrar que 85 % é o regime normal.

## Renderizador do emulador (item 29.11)

O renderizador gráfico é por aparelho e por worker, e a plataforma lê do log o que o emulador **selecionou**, porque
argumento aceito não é renderizador usado.

**O que foi medido** (`real`, 30/09/2026, emulador 37.1.11, imagem `android-34;google_apis`; detalhe em
[K-062](../conhecimento/aprendizados.md)):

- Com `-gpu swiftshader_indirect` (o padrão do parque), o Outlook 5.2635.3 derruba o processo do emulador:
  `0xc0000005` com `gles_swiftshader\libGLESv2.dll` na pilha, na tela de onboarding, cerca de 31 s depois de abrir.
- Com `-gpu host` ele abre e fica estável, subido pelo serviço (tarefa agendada, sessão 0): android-07 no central
  (NVIDIA RTX 2000) e android-09 no notebook (Quadro T1000). O log diz
  `emuglConfig_init: vulkan_mode_selected:host gles_mode_selected:host`, o canário do Outlook passou no android-07,
  cinco minutos no onboarding sem queda nos dois, cerca de 200 MB de VRAM por emulador e captura de tela funcionando.

**O que não funciona** (mesma medição):

| Tentativa | O que acontece |
|---|---|
| `-gpu angle_indirect` | recusado: "not valid, switching to 'auto'" |
| `-gpu swangle` | aceito, e o log acaba em `gles_mode_selected:swiftshader` |
| `-prop debug.hwui.renderer=skiavk` | aceito, e a propriedade não muda |
| `setprop debug.hwui.renderer skiavk` | muda, e todo processo com interface aborta em `VulkanManager` |

Os valores de `gpu_mode` que o 37.1.11 atende de fato são `host` e `swiftshader_indirect` (ou `swiftshader`).

**Como configurar.**

- Por máquina: `android.gpu_mode` no `config.yaml` (central) ou no `worker.yaml` (todos os aparelhos daquele worker).
- Por aparelho do central: `instances.overrides.<id>.gpu_mode` no `config.yaml`, validado na carga.
- Mudar o renderizador exige **reiniciar o aparelho**, e o boot seguinte é a frio: `gpu_mode` entra na assinatura de
  hardware do snapshot (`devices/manager.py::_hw_signature`), então o snapshot da hibernação deixa de valer. No
  central, a configuração só é relida quando o backend reinicia.

**O selecionado, lido e exposto.** `devices/emulator.py::ler_renderizador` lê do `emulator-<avd>.log` a última linha
`emuglConfig_init` (uma por subida; o arquivo é aberto em append). O gerenciador a guarda a cada entrada no ar (boot,
reinício, acordar e readoção) e a esquece quando o aparelho sai do ar. `GET /api/instances` devolve
`renderer: {configured, gles, vulkan, fallback}` em cada aparelho; `renderer` nulo quer dizer que não é emulador que
se conheça. No aparelho de um worker o log mora lá: o agente declara `gpu_mode`, `gpu_gles` e `gpu_vulkan` em cada
aparelho do `hello` e da batida (campos opcionais de `WorkerDevice`), relendo o log só quando o processo muda.

**Fallback silencioso vira aviso.** Pedido `host` e selecionado `swiftshader` (ou qualquer selecionado diferente do
pedido; `swiftshader_indirect` e `swiftshader` são o mesmo) aparece em `attention` do aparelho e no histórico, com o
pedido, o selecionado e o caminho do log. O aviso é derivado do que foi lido: cede a vez a outro assunto do cartão e
volta sozinho. A Infraestrutura mostra o renderizador na linha de capacidades do aparelho.

**Quem vence quando as fontes discordam (29.66).** Medido em 04/10: 1108 de 1114 trocas de `abis`,
`supported_verbs` e `renderer` nos aparelhos do worker caíam em horas de reinício do backend ou de reconexão do
worker. Em `abis`, o aparelho vence: o ADB lê `ro.product.cpu.abilist` (x86_64 e a tradução arm64-v8a), e a
declaração do worker e o AVD local (`abi.type`, só x86_64) só preenchem o que ninguém leu
(`registrar_capacidades(observado=...)`). No seed, antes do `hello`, o aparelho do worker entra pelo túnel como
"externo sem worker". Nessa janela, os verbos e o renderizador valem o último DTO gravado na comparação do
`publish` (lido do log uma vez depois do reinício), e o painel recebe o DTO real como `instance.progress`. Fora da
janela, `gles`/`vulkan` ainda não sondados (nulos) não apagam o que já se sabia. O `stream` que diz "worker
desconectado" continua contando: é fato.

Validação depois do deploy 26 (04/10, 13:15Z): as publicações do seed COM mensagem ("online — aparelho externo via
ADB") gravam o DTO provisório, e a saída da janela aparecia como 6 trocas de verbos e 10 de renderizador. Duas
correções, sem publicar o que o worker não confirmou: o evento gravado na janela (worker do aparelho conhecido e sem
`hello`) leva `janela_do_seed: true`, e a referência lida do log depois do reinício é o último `instance.updated` com
`renderer.configured` conhecido, entre as 20 linhas mais recentes. **Critério:** zero trocas de `supported_verbs` e
`renderer` fora da janela; a troca cujo evento anterior tem `janela_do_seed` é a saída da janela, não oscilação.

**Quem usa.** Um app declara em que renderizador ele não roda (`renderizador_recusado` no `app.yaml`), e a plataforma
recusa instalar e abrir o app nesse aparelho: ver [apps e loja](apps-e-loja.md#compatibilidade).

Prova: `simulated`, `backend/tests/test_renderizador.py` (leitura do log, exposição depois do boot com o dublê da
máquina, aviso de fallback, declaração do worker e as recusas). A leitura do log por um agente atualizado no notebook
é `not_run`.

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
- **CPU e RSS por emulador** (`devices/emulator.py::MedidorDeUso`, item 14.11): o laço de métricas lê a cada 3 s o
  lançador e os filhos (o qemu). O `cpu_percent` do psutil é um delta POR OBJETO, então os `psutil.Process` duram entre
  leituras (cache por pid do lançador, com `create_time` para detectar pid reciclado, descarte do filho que sumiu,
  purga do que não é lido há 60 s, `threading.Lock` porque a chamada vem do pool). A 1ª leitura de um pid devolve CPU 0,0
  (não há intervalo); da 2ª em diante é o valor real, e um filho novo soma 0,0 só na volta em que aparece. Até o 14.11
  o objeto era recriado a cada chamada e `resources.cpu_percent` ficava em 0,0 o tempo todo.
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
- **Tela sensível:** a prévia mostra toda tela, sem marcador, sem 404 `sensitive_screen` e sem pausa durante o
  `type_secret` (ADR-089, que revoga o C4). O painel (31.289) também não trata mais `sensitive` no frame.
- **Observação para a IA** (`DeviceManager.observe(imagem=…)`):
  - a árvore vem primeiro, e a imagem só quando a política, o julgamento, a evidência ou uma divergência de
    receita pedem;
  - o PNG é decodificado uma vez;
  - a imagem tardia (evidência) nunca serve para coordenadas;
  - o login determinístico do Instagram lê só a árvore.
- **Exclusividade:** captura, observação e ações seguem passando pelo `rt.executor` do aparelho (uma trilha só).
- **Medição:** `captura.total`, `captura.evitada{motivo}`, `captura.ms`, `captura.bytes`, `codificacao.ms` e
  `observacao.ms` em `GET /api/desempenho`.

### Sessão de automação morta por baixo (01/10/2026)

`ensure_automation` confia em `automation.state == "ready"` e na sessão conectada; um reboot do aparelho mata a
instrumentation do UiAutomator2 sem que o central saiba. Dois pontos fecham o buraco, os dois no `DeviceManager`:

- **`hierarchy`** (`GET /api/instances/{id}/hierarchy`): se a leitura volta com erro de **sessão perdida** (o mesmo
  critério do executor, `automation.driver.sessao_perdida`) e a plataforma ainda acreditava "pronta" (`state ready`,
  aparelho `online`), invalida a sessão, faz **uma** `ensure_automation` e **uma** releitura. Falhou de novo: o erro sobe
  como antes (503 `automation_unavailable`). UI ocupada (`DriverBusy`), erro que não é de sessão e sessão já em `error`
  (quem retenta é o monitor, espaçado) não recriam. A invalidação só vale se a sessão ainda é a que a leitura usou
  (`rt.automation` é trocado a cada transição): se outra coroutine já abriu a nova, o erro é da antiga e a nova não
  é derrubada. Uma requisição soma no máximo **uma** falha de sessão: não alcança sozinha o teto de 3
  (`FALHAS_DE_SESSAO_PARA_DEGRADAR`) que degrada o aparelho e aciona a escada de reparo.
- **Desfecho remoto bem-sucedido** (`readotar_depois_do_worker`, que agora recebe os dados do desfecho): `restart` e
  `reset` do agente desligam e religam a frio (`_v_restart`, `_v_reset`), então a sessão de antes do boot sempre deixou de
  valer. `start` e `wake` só a invalidam quando o agente afirma `started: true` (boot a frio ou volta do snapshot, como o
  `_boot` local, que também descarta a sessão ao acordar); com o emulador já no ar o agente responde `started: false` e
  não toca em nada, e a sessão (talvez no meio de uma tarefa) segue valendo. Invalidar = `automation` volta a `none` e o
  contador de falhas, que é da vida anterior, zera; **quem fecha e reabre é `ensure_automation`**, dentro do estado
  `starting` (`session.close`, `delete_stale`, `remove_forward`, `connect`), que é também a exclusão contra duas aberturas
  juntas. Com o aparelho ainda `online` (o central nunca o viu cair, e `_adopt_external` só liga as tarefas na transição
  para online), a abertura é agendada na hora.

Prova: `simulated` (`tests/test_hierarquia_sessao_morta.py`, aparelho e driver falsos); `not_run` em aparelho real.

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

### Emulador com janela na sessão 0 (29.48, 03/10/2026)

O backend do central (tarefa `farm-central`) e o agente do notebook (`farm-agente`, logon S4U) rodam na sessão 0, sem
área de trabalho. O `-no-window` escolhe o binário `qemu-system-x86_64-headless`, e esse binário, lançado ali, mantém
uma thread num laço de `WaitForSingleObject` a ~100 % de um núcleo por aparelho, do boot em diante (K-078). As flags do
headless (netsim, câmeras, som) não mudam nada.

- **`android.window: true`** tira o `-no-window`: o emulador sobe como `qemu-system-x86_64`, e o giro some. Na sessão 0
  a janela não aparece para ninguém. Funciona igual: screencap, hierarquia e automação; e não usa mais RAM.
- **Hibernação.** `window` não entra no `_hw_signature`. O snapshot salvo pelo headless carrega no binário com janela
  (provado no android-21), então os hibernados não perdem o snapshot na troca. O `-qt-hide-window` (só esconde a
  janela) entraria na assinatura, e não foi preciso.
- **Estado (real, 03/10).**
  - Central em `window: true` desde o restart das 12:10:52Z. Os aparelhos já ligados pegam o binário no próximo boot
    natural, sem reboot forçado em conta real.
  - Notebook (`C:\farm\worker.yaml`, `android.window: true`): desde o restart do `farm-agente` às 13:11:30Z (real, 03/10).
    Os 4 aparelhos QA foram reiniciados um a um. Antes, cada qemu era `-headless`, com uma thread a ~100 % (4 de 12
    núcleos). Depois, `qemu-system-x86_64` na sessão 0, com no máximo 14 % por aparelho e o processador em 2 %.

### Relatório de falha pendente do emulador (29.55, K-090, 03/10/2026)

O incidente (03/10, ~19:00Z): o reinício por IRQ derrubou um emulador na saída, e o crashpad deixou um dump em
`%TEMP%\AndroidEmulator\emu-crash-<versão>.db\reports\*.dmp`. Com o padrão do emulador ("ask"), toda subida seguinte
parou no diálogo que pede consentimento para enviá-lo (`Showing crashdialog to get consent`). Na sessão 0 ninguém vê
a janela para responder, então a espera ia até o prazo do boot (480 s). A escada de reparo chegou ao terceiro degrau
num aparelho com conta. O dump foi movido à mão para `data/quarentena-crash/`, e os 03 e 06 subiram com um `start` cada.

Agora são três camadas, no central e no agente (`devices/emulator.py` está no manifesto do agente):

- **(a) A flag.** `android.crash_report_mode` (padrão `never`) vira `-crash-report-mode never` no `build_args`: o
  emulador não pergunta nem envia. `""` volta ao padrão do emulador. O emulador 37.1.11 aceita
  `disabled|never|always|ask` (medido com `emulator -help-all`).
- **(b) A quarentena antes do `Popen`.** `emu.start_process` move os dumps pendentes para
  `<pasta de dados>/quarentena-crash/`: mover, nunca apagar, e nome repetido ganha sufixo. O TEMP é o do ambiente que
  o EMULADOR recebe (`tools.env()`), nunca o do processo. Um dump travado fica onde está e só vai para o log. Falha da
  quarentena nunca recusa o boot. No central a quarentena vira evento `log` (warn) no aparelho; no agente, linha no log
  dele.
- **(c) A falha rápida.** A linha do diálogo no log DESTA subida (a partir do offset gravado no spawn; log rotacionado
  é lido do começo):
  - no central (`_wait_boot`): encerra o lançador, põe o aparelho em `error` com o motivo e liga
    `bloqueio_de_crash`, e `_pedir_reparo` não age enquanto ela valer. O snapshot de um wake não é descartado por isso;
  - no agente (`_espera_boot`): encerra o lançador e fecha o `start` como `failed`, com `dados.motivo =
    dialogo_de_crash`. O central (`aplicar_desfecho_remoto`) liga a mesma marca, com a atenção no cartão.

  A marca cai na próxima subida (`_spawn`) ou quando o aparelho entra no ar.

- **Readoção (29.123, 05/10/2026).** O log do emulador acumula as subidas, e o offset só era gravado no spawn: um
  backend recém-subido tinha 0. Na readoção que caía no `_wait_boot` (a sonda não fechava com o host saturado), o
  detector lia o histórico, e um `Showing crashdialog` de dias antes parou o 03 e o 06, ambos com conta real, às
  12:57Z (incidente de 05/10, 29.122). Agora o `_adopt` local grava como offset o começo da subida em curso
  (`emu.inicio_da_subida_atual`): a última linha que é o marco `[central] subida do emulador`, escrito pelo
  `start_process` antes do `Popen` (29.127), ou `emuglConfig_init`, que fica de reserva para subidas de um agente
  anterior ao marco. O diálogo de uma subida vem depois das linhas dela (medido nos logs do 03 e do 06). O diálogo
  desta subida, mesmo escrito antes do reinício do backend, segue visto; os das subidas anteriores, não, nem nos
  primeiros segundos da subida (antes do 29.127, até o emulador descarregar a saída bufferizada, a última
  `emuglConfig_init` era a da subida anterior). Sem nenhuma das duas linhas (só um log que não veio de subida
  nenhuma), vale o fim do arquivo. Só "o arquivo não existe" dá 0; um erro passageiro tenta de novo e, se não
  passar, o offset fica desconhecido e o detector não lê nada nesta readoção (a espera vai até o prazo do boot). Na
  readoção, o veredito do snapshot e a releitura do 29.76 (d) nem rodam (o `_wait_boot` adotado não é wake). O
  veredito lê do começo quando o log foi rotacionado no spawn, e a retentativa a frio grava o offset dela.

Prova: `simulated` (`backend/tests/test_relatorio_de_falha_do_emulador.py`, e os dois casos do agente em
`backend/tests/test_worker_executor.py`). A prova `real` ainda é `not_run`: é a subida de um aparelho SEM conta com o
dump da quarentena posto de volta em `reports/`, e espera a vez da orquestradora.

### Pausa do reparo automático por aparelho (02/10/2026, W8)

Quase-acidente do estágio 1 do W8 (`docs/handoffs/w8-boot-recovery.md` §17.4): a escada de reparo pediu um `restart` do
android-09 15 s depois do `restart` do experimento. Só não entrou porque o comando do experimento ainda estava aberto
(`device_busy`). A pausa é o mecanismo mínimo para experimento ou manutenção de UM aparelho:

- **Desligada por padrão.** `PUT /api/instances/{id}/repair-pause` `{ttl_s, reason}` liga; `DELETE` encerra. **`ttl_s` é
  obrigatório** (60 s a 3 h) e a pausa **expira sozinha** (o monitor limpa e registra `instance.repair_pause`); repetir o
  `PUT` renova. **Sobrevive ao restart do backend** (25.13, 03/10/2026; antes ficava só em memória e o restart a apagava
  antes do prazo, K-082): fica gravada em `settings` (`repair_pauses`, `{id: {until, since, reason, by}}`) e volta com o
  mesmo prazo quando o aparelho é carregado; a retomada e o vencimento a tiram dali, e a vencida não volta. Quem depende
  dela confere o `repair_pause` do aparelho (`GET /api/snapshot` → `instances[].repair_pause`) e do `GET /api/health` →
  `features.repair_pause` (`{id: {until, since, reason, by, remaining_s}}`; vazio = nenhuma). É informativo: não vira
  problema de saúde.
- **No painel** (03/10/2026): a linha do aparelho em Infraestrutura diz "reparo pausado até hh:mm" (em outro dia, com a
  data), e a dica leva quem pausou e o `reason`. A vencida não aparece (`infra/infraState.ts::pausaDoReparoMeta`).
- **O que segura:** só o reparo AUTOMÁTICO de `restart`/`reset` do aparelho marcado: a escada (`requested_by='system'`,
  `despacho.remediar`) e o reinício por saúde do convidado (`requested_by='saude'`). A pausa não abre comando (nem
  rejeitado), então não conta como degrau; o aparelho volta a ser avaliado logo depois do fim da pausa e, se ainda
  estiver mal, a escada recomeça no 1º degrau.
- **O que NÃO segura:** comando de pessoa (`panel` e sessão do operador), o `restart` da rede (`rede`: é o produto, o
  `_reiniciar_ou_desistir` do W8), o rodízio, a reconciliação, `stop`/`hibernate`, e **qualquer outro aparelho**.
- **Não é a manutenção do worker** (`POST /api/workers/{id}/maintenance`): aquela suspende toda atribuição nova do
  notebook (inclusive o `restart` do próprio experimento e os aparelhos com conta) e não serve a este caso.
- Código: `devices/manager.py` (`pausar_reparo`, `retomar_reparo`, `pausa_de_reparo`), `commands/despacho.py`
  (`REPARO_AUTOMATICO`, o gate em `remediar` e em `pedir_ciclo_de_vida`). Prova `simulated`:
  `backend/tests/test_pausa_de_reparo.py` (8 testes).

### Saúde do convidado: pressão e interrupção acumulada (ADR-053)

Cada sonda de saúde que acha o framework vivo (`DeviceManager.conferir_saude` → `_conferir_pressao`) lê load, memória e
a linha `cpu` de `/proc/stat` do convidado (`adb.guest_pressure`). **Pressão** vira aviso no cartão, sem degradar: load
acima de 4× as vCPUs ou menos de 8% de RAM livre em duas sondas seguidas, e o texto diz o recurso que disparou
("Convidado sob pressão de CPU", "de RAM" ou "de CPU e RAM") com o remédio de cada um; antes era sempre "mais RAM", o
remédio errado para o android-06 de 28/09, que tinha RAM sobrando. O PRIMEIRO aviso de cada episódio (29.152) leva no
`data` do evento o campo `pressao`: os 3 processos de maior CPU (`dumpsys cpuinfo`) e o pacote em primeiro plano, só
nomes (`adb.ler_culpados`, `_culpados_da_pressao`), uma leitura por episódio; os avisos seguintes só trocam os números.
Medido em 06/10: no android-02, 375 de 480 avisos do dia e nenhum nomeava processo ou app. **Interrupção acumulada** (`_conferir_interrupcoes`):
a fração de CPU em irq+softirq entre duas sondas; com o aparelho ocioso (ninguém no controle, nem a IA nem uma pessoa)
acima de 15% em 3 sondas seguidas (`IRQ_OCIOSO_MAX`, `IRQ_SONDAS`), a plataforma abre um `restart` rastreável
(`on_health_restart` → `AppState._reiniciar_por_saude`, `requested_by='system'`), no máximo 1 a cada 6 h por aparelho;
se não resolver, fica só o aviso "Convidado com interrupções acumuladas". É um caminho à parte da escada acima: nunca
chega a `reset`, que apagaria a conta real logada. Medido em 28/09: irq ocioso de 21% (68 h no ar) e 90% (44 h) voltou a
~2% depois do `restart` ([K-050](../conhecimento/aprendizados.md), [relatório §21](../relatorio-validacao.md)).
Desde 21.13 cada fração vira `measurements(kind='irq')` (série em `GET /api/desempenho?irq_horas=`). A causa do
acúmulo segue aberta (item 21.15): em 29/09 o android-06, com o Instagram em primeiro plano, foi de ~4% (2,7 h no ar)
a ~8% (6,4 h), e o android-04, no launcher, ficou em 2–3% (`data\logs\irq_convidados.csv`).

**Host saturado não conta (29.67, ADR-053).** Medido em 04/10 (30 h de sondas ociosas): convidado de 2 vCPU no
central fica em 0,04–0,07 de irq com o host calmo e passa de 0,10 sob as suítes. O de 4 vCPU (01) fica em
0,02–0,06. O android-06 foi a 0,50 com a SQLite em `-n 8`. O 06 não tem defeito próprio: igual ao 03 em AVD e em
taxa de interrupções. Com a CPU do host acima de `android.max_cpu_percent_before_boot` (o limiar de admissão do
29.33, 85 %), a amostra da sonda não conta: o contador nem sobe nem zera, e a próxima amostra com o host calmo volta
a contar. A decisão fica na própria linha `measurements(kind='irq')` (`host_cpu`, `ignorada`) e no contador
`irq.amostra_ignorada`, sem evento por sonda. Aparelho do worker não olha o host do central.

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
| Renderizador por aparelho e por worker: selecionado lido do log, fallback como aviso | implementado | `simulated` (`tests/test_renderizador.py`); `gpu_mode: host` pelo serviço é `real` (30/09, android-07 e android-09); leitura pelo agente no notebook `not_run` | `devices/emulator.py`, `devices/manager.py`, `worker/executor.py`; plano-100 id 29.11 |
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
`instance_ids` e, sem uso, apaga a linha e o segredo juntos. `params.egress_esperado` (IPv4) e
`params.egress_esperado_ipv6`, opcionais, declaram a saída pública que o perfil deve dar (item 29.6, abaixo): valem a
mesma regra de endereço público da medição (`_saida_publica`), e endereço privado, loopback, CGNAT, link-local, nome
de host ou a família trocada dão 422 com o campo no texto; fica gravada a forma canônica.

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
prévia avisa quando o proxy global da 041 está gravado no aparelho. Cada item traz ainda `egress_warnings` (29.6;
lista de `{code, message, …}`, vazia quando não há o que avisar, e que nunca recusa): `saida_dedicada_compartilhada`
(o perfil com saída esperada fica em mais de um aparelho, com `shared_with`) e
`saida_dedicada_trocada_por_compartilhada` (o aparelho sai de um perfil com saída esperada para um sem).

**Estados, só com evidência.** `pendente`, `configurado`, `conectado`, `trafego_verificado`, `parcial`. Atribuir e
reaplicar só regridem. `registrar_observacao(rev, estado, evidencia)` é o único caminho para `configurado` e
`conectado` (evidência lida do aparelho obrigatória; grava `applied_rev`); observação de revisão que não é a pedida
é descartada, como no `proxy._fechar`. `registrar_medicao(medicao, rev)` acrescenta ao histórico sempre, grava a
última saída medida (`egress_ipv4`, `egress_ipv6`, `verified_at`) e decide: `trafego_verificado` só com a revisão
pedida aplicada, IP de saída medido, cada app de `rede.apps_exigidos` medido `ok` (sem app exigido, ao menos um app,
todos `ok`; o `sem_trafego` não segura, ver abaixo), na política com bloqueio, `leak_blocked` verdadeiro e, quando o perfil declara a saída esperada, a
saída medida igual a ela (29.6); IP medido com algo faltando é `parcial`, com o
que falta no `detail`; sem IP, o estado fica. Vocabulário de `per_app`: `ok` (saiu pela rede pedida),
`fora_da_rede` (vazou), `falhou`, `nao_medido` (não instalado ou não lido) e `sem_trafego` (instalado, 0 byte na
janela; 29.44).

- **"Verificado" = tudo o que TRAFEGOU passou pelo túnel** (29.44, 03/10/2026). O app parado na janela não prova
  nem desprova. Ele fica `sem_trafego` e não segura o `parcial` quando outro app passou pelo túnel e nenhum saiu por
  fora; a sonda do shell conta como app.
  - O estado é `trafego_verificado` com a ressalva no `detail` ("Outlook sem tráfego na janela: não provado, não
    segura o estado"), e o painel mostra "sem tráfego na janela".
  - Quando o app trafega numa janela seguinte, a medição o reavalia e o estado muda sozinho (`ok`, ou `parcial` se
    ele saiu por fora).
  - Seguem segurando: `nao_medido` (não instalado), `fora_da_rede`, e nada ter trafegado ("nenhum app trafegou na
    janela").
  - Origem: o 03 e o 06, no deploy 7, ficaram em `parcial` só porque o Outlook estava parado.

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
antes da tarefa. Com a política `exigida_com_bloqueio`, verificar **apaga a prova de vazamento da linha** (é o pedido
de refazer o teste): a falta dela é o que dispara o teste, então o pedido sobrevive a um reinício do backend, e até o
teste novo provar o bloqueio a tarefa com rede exigida espera. Sem bloqueio, um reinício do backend perde a marca em
memória, e a readoção (`ligou`) mede de novo quem ainda não está verificado. Loja, quarentena e aparelho sem rede pedida
(`nothing_requested`) são recusados.

**Ao subir o backend (25.12).** Todo reinício do backend refaz o servidor do túnel e derruba os túneis dos aparelhos, e
o `conferir` só vê `tun0` e `CONNECTED`. No `start` do scheduler, `ConvergenciaDeRede.verificar_ao_subir` marca a
verificação do tráfego de todo aparelho com política `exigida` ou `exigida_com_bloqueio` e rede conectada
(`conectado`, `parcial`, `trafego_verificado`): a medição sai no próximo ponto seguro de cada um (readoção ao ligar ou
varredura de 60 s, com o aparelho livre), e a sem IP é o túnel morto (acima). **Escolha:** NÃO é o `POST …/verify`.
Aquele pedido, com bloqueio, apaga a prova de vazamento e refaz o teste (para o cliente VPN e reinicia o aparelho), e o
reinício do backend não mudou o cliente do aparelho nem o bloqueio: a prova segue valendo, o que caiu foi o túnel. Ao
subir só se pede a MEDIÇÃO (a mesma marca de memória do verify); a prova de vazamento, o `detail` e os eventos da linha
ficam como estavam. Quem precisa de teste novo continua pedindo `verify`.

**Visão por aparelho.** `GET /api/network/devices`: por aparelho do parque (a loja fica de fora), `network`
(`DeviceNetworkDTO` ou `null`), `effective_state`, `legacy_proxy`, `restriction` (a frase da quarentena),
`real_account`, `required_apps`, `pending` (`aplicar`|`verificar`), `last_measurement` (a última medição, mais
`udp_dns_ok` e `udp_ntp_ok`, as duas pernas de UDP lidas do `detail`; item 29.5) e `egress_shared_with` (os
outros aparelhos com a mesma última saída medida, v4 ou v6: aviso, não bloqueio), `egress_expected` (a saída que o
perfil da saída final declara, `{ipv4, ipv6, profile_id, profile_name}`, ou `null`) e `egress_matches` (a saída
medida nesta revisão é a esperada: `true`/`false`, ou `null` sem esperada ou antes de a revisão pedida ser medida;
item 29.6). O proxy da 041 é lido
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
  worker por `adb reverse tcp:P tcp:P` (desfeito ao fim; só há prova simulada — num aparelho do worker é `not_run`,
  ver 25.7); `am start …
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
  o worker ocupado continua segurando.
  **Quem é "objetivo no meio"** (`vitrine.objetivo_que_segura`, 25.12): `running`, `waiting_user` ou `uncertain` de uma
  execução que não está em `completed`, `cancelled` ou `failed`. `completed_with_issues` é o rollup IMEDIATO de todo
  `waiting_user`/`uncertain` com nada rodando (`recompute_run`), e a tela desse objetivo é a evidência de que o operador
  precisa (PR #13): ele segura, mas só por `OBJETIVO_PARADO_SEGURA_POR_S` (2 h, valor provisório; o orquestrador decide)
  depois de ter parado (`objectives.finished_at`; sem ele, `runs.finished_at` e `created_at`). O objetivo de 02/10
  adiou o teste de vazamento do android-03 por ~1 h, e o android-01 tem 18 desde 28/09 (a primeira versão do 25.12 tratou
  `completed_with_issues` como terminal e tirou a proteção também do objetivo de cinco minutos atrás; corrigida). A
  execução VIVA segura sem limite de idade; a que a pessoa reabre volta a `running`. Quem chama, e o efeito da idade:
  o teste de vazamento (`_vazamento_adiado`) e o reinício da rede (`_quem_segura_o_reinicio`) deixam de esperar por
  objetivo velho; a entrega do app principal ao ligar (`pendentes_ao_ligar`) e a frase do status da loja passam a
  trocar o app por cima da tela de um objetivo parado há mais de 2 h. Por estado da linha:
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
  (reinicia). Conferir só regride; `trafego_verificado` continua sendo só da medição.

  **Túnel morto (25.12).** O conferir só vê o `tun0` e o `CONNECTED` do `dumpsys`: depois que o backend do central
  reinicia (o servidor do túnel é refeito), o cliente do aparelho segue "no ar" sem handshake e a sonda não mede IP.
  Antes, a medição sem IP de um `trafego_verificado` só escrevia "o estado não muda" e a tarefa passava por uma rede sem
  saída (android-03, 03/10, medição #216: a internet só voltou com reinício manual). Agora, a verificação de um
  `trafego_verificado` com política exigida (`exigida` ou `exigida_com_bloqueio`) cuja medição vem sem IPv4 e sem IPv6:
  (1) grava a medição no histórico e tira a linha de `trafego_verificado` (→ `conectado`, com o motivo no `detail` e o
  evento `network.updated` `warn` com `acao: tunel_morto`), e a porta da tarefa segura; (2) religa o cliente VPN no
  aparelho, até 2 vezes na mesma passada: `am force-stop` do cliente (o always-on o sobe), e, se o `tun0` não voltar,
  o Start da interface (`religar_pela_interface`, só sem objetivo no meio: ele abre a tela do cliente; com teto de
  `prazo_da_interface_no_tunel_morto_s`, 60 s — em 03/10, android-06, a interface falhou com `WebDriverException … Timed
  out … AccessibilityNodeInfo`: falha ou trava conta como tentativa e nunca entra em laço); com o túnel de volta, a
  sonda de IP confere e a medição completa é refeita na mesma passada; (3) sem volta, `configurado` e reinício pelo
  caminho de sempre (`restart`, nunca wipe, com as guardas de objetivo no meio e o teto `rede.reinicios_max`), e o
  `conectar` confere depois do boot. A prova de vazamento não é tocada. Só o `trafego_verificado` entra aqui: o
  `conectado`/`parcial` sem IP (eco fora, servidor fora) segue esperando `rede.sonda.reverificar_s`, sem parar o
  cliente nem reiniciar, e o que acabou de voltar de um reinício não reinicia de novo. Limite conhecido: um eco de IP
  fora do ar com o túnel bom também parece túnel morto, e custa até 2 `force-stop` do cliente e um reinício por
  `trafego_verificado` perdido (a linha só volta a ele com IP medido, então não há laço apertado). Falha não se repete às
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

  **O reinício que a rede pede e o aparelho ocupado não deixa sair (29.21, 02/10/2026, android-05):** o `restart` é
  pedido em memória (`_agendar_reinicio`: 1 s depois e a cada 5 s por 24 vezes, até o trabalho soltar o aparelho). No
  android-05 (`exigida_com_bloqueio`, acordou a frio sem `tun0`) a linha foi a `configurado`, a interface religou o
  cliente e NENHUM `restart` foi aberto: as 24 tentativas viram o aparelho "ocupado" e o esgotamento punha `espera_ate`
  300 s, que cala a varredura e a porta; a execução que esperava a rede ficou presa 7 min. Agora (a) o termo de
  `ocupado` que segurou (`_quem_segura_o_reinicio`: "worker no scheduler (objetivo X)", "controle da IA" ou "de uma
  pessoa", "objetivo X em andamento", "comando exclusivo aberto X (verbo)"; e ainda "estado <x>" e a recusa do
  `pedir_ciclo_de_vida`, com ou sem o verbo `restart` no worker) vai para o `detail` da linha, UMA vez por termo e
  uma para o esgotamento; (b) com um objetivo de execução ativa parado em `wait_reason='rede'` neste aparelho
  (`pending`, pela porta de despacho, ou `running`, suspenso entre etapas) o esgotamento NÃO põe os 300 s: o pedido
  segue agendado e retenta em `retentativa_do_reinicio_s` (30 s) com mais 24 tentativas, enquanto o objetivo
  esperar; sem ele, a espera de 5 min de antes. O objetivo que espera a rede nunca conta como ocupado (o worker, o
  controle e `objetivo_em_andamento(..., exceto_quem_espera_a_rede=True)` são conferidos à parte). **O teto
  persiste desde o 25.11:** a conta de reinícios pedidos da revisão em curso fica na linha (`device_network.restart_rev`
  e `restarts_requested`, migração 093) e volta à memória quando a convergência abre o aparelho; o reinício do central
  já não dá `reinicios_max` reinícios novos ao mesmo aparelho. A conta fecha quando o túnel sobe, na desistência e
  na invalidação (wipe/reset). `_reinicio_agendado` e `espera_ate` seguem em memória: perdê-los só antecipa uma
  conferência. **Causa exata do "ocupado" no android-05: não provada** (os eventos não mostravam termo nenhum
  ocupando); a observação nova é o que vai dizer na próxima ocorrência. Prova `simulated`:
  `tests/test_rede_aplicacao.py::test_reinicio_com_objetivo_esperando_a_rede` e
  `::test_reinicio_que_nao_sai_diz_qual_termo_segurou`; real: `not_run` (reproduzir no android-05).

  **Desfazer sem religar o cliente (A11, 02/10/2026, W8 r2/r3):** o `desfazer` faz `am force-stop` do cliente VPN, ESPERA ~10 s (`PARADA_PERSISTIR_S`, em `rede_aplicacao.py`) para o estado `stopped` chegar ao disco e só então o reinício é pedido (reiniciar na hora o perdia: o cliente, que lembra que estava ligado, religava sozinho no boot com um `tun0` para um par que já saiu do servidor). Se mesmo assim, depois do boot, o always-on e o bloqueio estão fora mas o `tun0` está no ar (`Observacao.cliente_solto`), a convergência para o cliente (`force-stop`, sem reinício) e apaga a linha em vez de pedir outro reinício (que o religaria de novo); se o túnel não cai, vale o caminho de antes (reiniciar até `rede.reinicios_max`).

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
medição que não verificou, a porta também espera esse tanto). Cada leitura da rede pelo adb (observação, estado da
interface, janela do start) tem o prazo `rede.sonda.prazo_leitura_s` (90 s; a fila do aparelho dá mais 10), item 29.75:
eram 45 s fixos, e 6 medições ao ligar falharam por prazo em 7 dias com o host disputado. Antes de medir, relê como o
conferir: deriva regride e nada é medido. O que se mede:

- **IP de saída v4 e v6**: HTTP/1.0 a um eco de IP na porta 80 (`rede.sonda.hosts_ipv4`, padrão `api.ipify.org` e
  `ipv4.icanhazip.com`; `hosts_ipv6`, `api6.ipify.org` e `ipv6.icanhazip.com`: a família vem do host), com o stdin
  aberto por `sleep` (sem ele o `nc` fecha antes da resposta, medido). Vale o primeiro host que devolver HTTP 200 com
  IP **público** da família; sem IP, o `detail` guarda o motivo de cada host (`Permission denied` do bloqueio,
  `Timeout`, `No route to host` do IPv6 preso no túnel, que é o esperado com `strict_route`);
- **DNS e UDP**: o resolvedor da rede VPN (`DnsAddresses` das `LinkProperties` do `tun0`; no SFA, 172.19.0.2, o
  hijack), o DNS privado do Android, se um nome resolve, e duas **pernas** de UDP de ida e volta pelo `nc -u`: DNS a
  `rede.sonda.udp_dns` (8.8.4.4; o cliente o sequestra e resolve pelo túnel) e NTP a `rede.sonda.udp_ntp` (o UDP que
  não é DNS — na cadeia com SOCKS5 o DNS seguia e o NTP se perdia). Cada perna manda **até 3 datagramas, de 2 s
  cada, e para no primeiro com resposta** (item 29.5; `sonda_rede.TENTATIVAS_UDP` e `ESPERA_UDP_S`), tudo na mesma
  ida ao shell, como uid 2000. Com um datagrama só e 5 s de espera, 2 de 32 medições reais de 30/09 perderam uma
  perna (a #6 do android-03, `UDP DNS 83 B, NTP 0 B`, com carga 18,8 em 2 vCPU naquele minuto; a #21 do android-05,
  `DNS 0 B, NTP 48 B`) e a repetição manual deu 12/12: era datagrama perdido. O `nc -u` do toybox lê até o prazo
  mesmo com a resposta na mão, então a perna boa custa 2 s (eram 5) e a pior, 6 s (eram 5); o prazo da ida cobre o
  pior caso das duas (`rede_medicao._PRAZO_DNS_E_UDP_S`). `udp_ok` continua sendo o E das duas pernas (sem coluna
  nova), e o `detail` da medição diz cada uma, num formato estável:
  `UDP DNS 83 B (1ª de 3, 2,0 s), NTP 0 B (0 de 3, 6,1 s)` — bytes, em qual datagrama respondeu (`0 de 3` = em
  nenhum) e o tempo da perna, medido pelo `/proc/uptime`. Uma medição de antes do 29.5 tem só `UDP DNS 83 B, NTP 0 B`.
  `sonda_rede.pernas_udp(detail)` lê os dois formatos de volta, e a listagem os entrega em `last_measurement` como
  `udp_dns_ok` e `udp_ntp_ok` (nulos quando o `detail` não diz); o painel mostra "UDP: DNS ok · NTP falhou", com
  destaque na perna que falhou. **UDP ainda não é critério de `trafego_verificado`**: `rede._falta_para_verificar`
  não olha `udp_ok`, e uma medição com uma perna sem resposta e o resto provado verifica o aparelho e libera a tarefa.
  Tornar UDP critério é decisão à parte (com proxy HTTP o UDP que não é DNS é recusado de propósito, T3). O laço do
  comando (POSIX conservador: `while [ … ]`, `$((…))`, `break`) ainda não rodou num aparelho (`not_run`); a forma de
  cada datagrama (`printf | timeout nc -u | wc -c`) é a medida no piloto;
- **cobertura por app** (`per_app`): `pm list packages -U` dá o UID de cada app de `apps_exigidos` (Instagram,
  Outlook…), e `dumpsys netstats --poll` + `detail` (seção "UID stats", `tag=0x0`) dá os bytes por (tipo, uid). No
  delta da janela, `ok` = o que saiu pela física também passou pela VPN (tipo 17 = tipo 1, como o Chrome no 25.1;
  folga de 512 B ou 2%); `fora_da_rede` = saiu por fora (o uid 0 no 25.1: 868 B na física, 52 B na VPN); `sem_trafego`
  = 0 byte na janela, na VPN e na física (29.44; contador que andou para trás conta zero); `nao_medido` = app não
  instalado. A janela dos apps é ACUMULADA desde que o túnel conectou nesta
  revisão (a contabilidade é guardada no `conectar`; com o backend reiniciado depois disso, no primeiro `conferir`
  da readoção que acha o túnel no ar, ou, sem ele, na primeira medição): um vazamento visto não some na medição seguinte, e um app parado desde a última sonda não derruba um
  `trafego_verificado` a cada "Verificar". A do shell (`com.android.shell`, a própria sonda, sempre no `per_app`) é
  só a passada, e sem IP nenhum ela é `falhou`;
- **vazamento** (só com `exigida_com_bloqueio`; `rede_medicao.sondar_vazamento`): feito **antes** da medição e uma
  vez por revisão e por instalação do cliente VPN (a prova fica na linha do aparelho; ver "A prova de vazamento",
  abaixo), com a VPN derrubada DE VERDADE. A sonda de IPv4 precisa sair pelo túnel primeiro (sem isso, nada
  é tocado e `leak_blocked` fica `None`: sonda que não funciona não prova bloqueio). Depois o **cliente VPN é
  parado** e a sonda roda de novo, ao mesmo host, **numa ida só ao shell** (uid 2000;
  `sonda_rede.comando_parar_e_sondar`): até 5 tentativas de `am force-stop`, cada uma esperando o
  `/sys/class/net/tun0` sumir em passos de 0,1 s por até 3 s; no instante em que some, a mesma sonda de IPv4 roda e o
  laço acaba (`SEM_TUN=` diz em qual tentativa, ou 0). Por que numa ida só: com always-on e lockdown o Android religa
  o cliente em **menos de um segundo** (prova real, android-05, 29/09). Com parar, ler o `tun0` e sondar em três idas
  ao aparelho, o túnel já tinha voltado entre elas: de 3 testes reais, 1 deu `Permission denied` e 2 deram "o tun0
  continuou no ar", o que deixava o aparelho em `parcial` e custava um reinício a mais. O comando foi escrito POSIX
  conservador para o mksh/toybox (`[ -e … ]`, `$((…))`, `sleep 0.1`) e ainda não rodou num aparelho (`not_run`); a
  simulação está em `backend/tests/test_rede_sonda.py::test_always_on_que_religa_na_hora_nao_esconde_o_bloqueio`. Só o
  `Permission denied` do Android prova o bloqueio (`leak_blocked` verdadeiro); um IP é vazamento (falso, "VAZOU" no
  `detail`); qualquer outra coisa (`Timeout`, nome que não resolve, a janela sem `tun0` nunca vista nas 5 tentativas —
  o cliente não parou ou o always-on o religou antes do passo de 0,1 s) fica `None` com o motivo. Limite conhecido:
  entre ver o `tun0` ausente e o `connect` do `nc` há milissegundos; um cliente que volte exatamente aí leva a sonda
  pelo túnel e o IP seria lido como VAZOU — o lado conservador (uma falha a mais, nunca um "bloqueado" falso).
  Comparar com o IP do túnel não resolve: com o servidor no central atrás do mesmo NAT, as duas saídas podem ser o
  mesmo endereço. Parar o SERVIDOR (o primeiro desenho) não serve: com o túnel no ar e o servidor fora, a sonda dá
  `Timeout` com o bloqueio ligado ou desligado (25.1, 18:06:50), e o teste gravava "bloqueado" presumido. Depois do
  teste, a convergência relê: com o túnel de volta sozinho (o always-on religou, como no android-05), mede na mesma
  passada; sem ele (no 25.1, 18:07, o always-on não religou), a linha regride a `configurado`
  com o desfecho do teste e a convergência pede o reinício (o boot religa o cliente com o perfil selecionado, 25.1
  18:21); a medição vem depois do boot, com o teste guardado. Custo: **até um reinício a mais por revisão**, além do da
  aplicação (e de novo a cada `POST …/verify`, que refaz o teste — o 202 avisa — ou depois de cada reinício do
  backend, porque o teste fica em memória: `network_measurements` não guarda a revisão). Sem o reinício garantido logo
  depois, o teste nem começa (`leak_blocked` `None`, sem guardar, e a medição seguinte tenta de novo): com um objetivo
  no meio do aparelho (rodando, esperando uma pessoa ou incerto — a mesma regra do reinício) e no celular sem worker,
  que a plataforma não reinicia de verdade (25.7; lá, `exigida_com_bloqueio` fica em `parcial` — use `exigida`). O teste vale também com servidor externo (não depende de parar servidor nenhum), e o servidor do central
  não é tocado: os outros pares não perdem a conexão. Depois do `force-stop`, nenhuma falha levanta erro: o desfecho
  volta a quem religa o cliente, para o aparelho nunca ficar sem VPN (e, com bloqueio, sem rede) esquecido.

### A prova de vazamento (item 29.2, ADR-061)

O teste de vazamento para o cliente VPN e, quase sempre, custa um reinício do aparelho. Até 30/09 o desfecho dele
vivia só na memória da convergência: um reinício do backend o perdia, e a remedição seguinte (a 90% de
`rede.validade_verificacao_s`) refazia o teste em todo aparelho com bloqueio. Medido em 30/09: um reinício do backend
às 02:40Z custou 11 reinícios de aparelho entre 06:52Z e 07:59Z, em três aparelhos, dois com conta real.

A prova mora em `device_network` (migração 063):

| Coluna | O que guarda |
|---|---|
| `leak_rev` | a revisão (`desired_rev`) em que o teste foi feito; vazio = nenhum teste |
| `leak_client` | a instalação do cliente VPN testada: `<versão> (<código>) <pasta de instalação>`, lida do aparelho como uid 2000 na mesma ida em que se lê o resto da rede. A pasta é sorteada pelo Android a cada instalação ou atualização |
| `leak_result` | 1 = o Android recusou a sonda fora da VPN; 0 = vazou; vazio = não concluiu |
| `leak_at`, `leak_detail` | quando, e o que a sonda mostrou (ou por que a prova foi apagada) |
| `leak_pending` | 1 entre a intenção do ensaio e o desfecho |

**Quando vale.** `leak_rev = desired_rev`, `leak_client` igual ao cliente lido agora e `leak_result = 1`. É a prova
da linha, e não o `leak_blocked` de uma medição, que decide `trafego_verificado` na política com bloqueio e que a
porta da tarefa consulta (`rede.bloqueio_provado`, `rede.verificacao_invalida` → `bloqueio`). O `leak_blocked` da
medição é o registro do que a sonda levou.

**O que invalida.** Revisão nova (a chave deixa de casar; reaplicar a **mesma** revisão depois de uma falha não
invalida, senão um teste que custa reinício viraria laço). Cliente VPN de outra instalação (a conferência apaga a
prova). Wipe, reset ou outro aparelho atrás do id (o gancho `invalidar`). `POST …/verify`.

**O que não invalida.** O relógio. A validade governa a medição barata (IP, DNS, UDP, apps); o bloqueio é relido a
cada conferência (`always_on_vpn_lockdown`, "Lockdown filtering rules"), e a falta dele regride a linha pela deriva.
Um túnel caído também não: a linha regride a `configurado`, a prova fica, e o aparelho segue sem saída fora da VPN
até o túnel voltar.

**O ensaio.** A intenção é gravada antes do `force-stop` (`leak_pending = 1`, com CAS pela revisão aplicada) e o
desfecho depois (CAS pela revisão, pela instalação e pela marca de pendente: o resultado de um ensaio de revisão
antiga não vira prova da nova). Quem encontra `leak_pending = 1` sem ensaio em curso neste processo — o backend
reiniciou no meio — fecha como inconclusivo e **não** para o cliente de novo; se o túnel ficou caído, é deriva, e o
caminho de sempre (`configurado` → reinício) o religa.

**O que nunca aprova.** Resultado 0, vazio ou ausente leva a `parcial`. Vazou e inconclusivo são desfechos: não se
refazem sozinhos, só por `POST …/verify`, revisão nova ou cliente novo, e a linha nesse estado deixa de ser medida a
cada `rede.sonda.reverificar_s` (só quando a última saída medida está para sair da validade). O teste adiado (objetivo
no meio, celular sem worker) não é desfecho: nada é gravado, e enquanto segue adiado a passada confere e dispensa a
medição — no android-05, em 30/09, a sonda rodou 34 vezes em seis horas para escrever o mesmo `parcial`.

**Transição (primeira subida com a 063).** A prova que o código anterior fez ficou só no histórico. Ela é adotada,
sem parar o cliente, quando o registro a sustenta (`rede.prova_anterior`): a linha nunca escrita pelo mecanismo novo,
a última medição com o bloqueio provado, e os comandos `verificar` da revisão, do mais novo para trás, todos os que
têm desfecho de bloqueio com ele provado — o mais antigo deles é o teste; **e** quando o APK do cliente VPN no
aparelho é anterior a esse teste (`stat -c %Y`, em segundos desde 1970, lido na hora, com o relógio do aparelho a no
máximo 2 min do servidor). Sem a correspondência, o teste é feito. Três cuidados, achados pela revisão independente:
comando que falhou, ficou incerto ou só releu o aparelho não é evidência e é pulado (uma leitura que falha uma vez
não pode virar teste destrutivo na passada seguinte); apagar a prova deixa o motivo em `leak_detail` mesmo quando não
havia prova gravada, e uma linha com motivo nunca adota (o `verify` e o wipe valem também na linha antiga); e a linha
nova já nasce com `leak_detail` preenchido, para o histórico de uma rede que foi tirada não virar prova da que foi
pedida depois.

### O túnel que não sobe no boot (item 29.3)

Medido em 30/09 no android-05 (QA), 7 reinícios pela plataforma com o host sob carga, prova `real`:

- o `startAlwaysOnVpn` do sistema roda **uma vez** por boot. A primeira tentativa falhou em 5 de 7: ANR de início do
  serviço (3: o cliente leva mais de ~22 s para chamar `startForeground`, com o convidado sem CPU — pressão de CPU
  90%, carga 11 a 20 em 2 vCPU) ou o serviço sobe e para sozinho em segundos (2). Não houve a exceção de serviço em
  primeiro plano do piloto, nem relatório de falha;
- quando sobe, o túnel aparece entre 92 e 176 s de ligado (a segunda chance é o receptor de boot do próprio cliente);
  depois de 180 s não sobe mais. A conferência da plataforma lia o aparelho aos 95–159 s e pedia outro reinício: em
  30/09, 15 de 30 conferências depois do boot terminaram assim, e o android-06 levou 6 reinícios e 2 reaplicações por
  causa de um teste de vazamento;
- `am force-stop` não religa o cliente (0 de 4; o "volta em menos de um segundo" do K-063 não se repetiu), e o serviço
  não é exportado;
- o **tile de configurações rápidas do cliente** religava sem reinício **no android-05**: 5 de 5 com o tile adicionado na
  hora e o SystemUI estável, 1 a 4 s depois do clique. **Isso valeu porque o `serviceMode` do SFA ali já era VPN** (W8,
  01/10: o tile não recalcula o modo; num cliente que só importou o perfil, como o android-09, ele inicia o
  `ProxyService`, que aborta sem `tun0`; ver abaixo). O que está medido no 05 fica como histórico, não como garantia. Um tile que já estava na barra não responde, e com o SystemUI no laço de ANR do
  boot o clique não acontece. Always-on, bloqueio e regras ficaram como estavam nas 6 conferências.

O que a plataforma faz com isso:

| Medida | Onde |
|---|---|
| Espera o `tun0` até `rede.espera_tun_s` **contados do boot** (padrão 180 s, era 60) antes de concluir que não subiu | `rede_convergencia._observar_depois_do_boot` |
| Com a configuração valendo e só o túnel faltando, tenta o **Start da interface do cliente** antes de reiniciar (W8; substitui o tile): confere que **não há `tun0`** (Start/Stop alterna), abre a `MainActivity`, acha o botão `Start` PELA ÁRVORE (um nó habilitado do pacote do cliente com o rótulo do locale do aparelho, `ROTULOS_DO_CLIENTE`, dentro do menor contêiner clicável do mesmo pacote; ambíguo, ausente, desabilitado ou de outro pacote = falha fechada, nenhum toque), confere o foco, toca UMA vez no centro do rótulo, relê como uid 2000 e devolve o foco (HOME só se o foco é do cliente). Sucesso só com `tun0` E VPN CONNECTED | `rede_aplicacao.religar_pela_interface`, `rede.cliente_atividade` (vazio desliga) |
| **Guard D:** se o Start iniciou o `ProxyService` (serviceMode não-VPN) num plano com TUN (`wrong_service_class_for_tun`, lida SÓ na janela deste Start do buffer `events` do logcat; sem prova, `UNKNOWN`), não é recuperação: a linha fica `configurado`, o código vai na frente do motivo do reinício e a razão na evidência (`[interface: …]`, sem segredo). Abertura que falha, botão não provado, foco errado: idem, **sem fallback para o tile** | `rede_convergencia._religar_sem_reinicio`, `_motivo_da_interface` |
| O mesmo Start depois do teste de vazamento: religado, a medição sai na mesma passada e o teste deixa de custar um reinício; senão o boot, como antes | `rede_convergencia._prova_ou_ensaio` |
| Sem boot desde a configuração (o bloqueio ainda não vale no sistema), a interface não é tentada: o caminho é o reinício. O teto `rede.reinicios_max` e o laço de reinício não mudam: o Start é UMA tentativa por passada | `rede_convergencia._conectar` |

**Por que o Start da interface e não o tile (W8, `real` 01/10/2026 + código do SFA 1.14.2, commit upstream
`fc21909df7a3f0fc9435f3866fb6a4960711aa5f`).** `TileService.onClick` → `BoxService.start()` → `Settings.serviceClass()`
(`serviceMode == VPN` → `VPNService`, senão `ProxyService`), **sem** `rebuildServiceMode()`; só `MainActivity.startService0` (o
Start da UI) e a seleção de perfil com o serviço rodando recalculam o modo (`Libbox.hasTunInbound(perfil selecionado)`); a
importação (`create(andSelect = true)`) seleciona o perfil sem recalcular, e o `serviceMode` nasce `NORMAL`. `ProxyService` com
perfil que tem `tun` → `openTun` lança e o serviço se encerra em 1–2 s sem `tun0` (o `F6_TUN_NOT_CREATED_AFTER_TILE` do
android-09). Prova real: android-09, UM Start da UI → `VPNService`, `tun0` em < 1 s, VPN CONNECTED (run
`20261001T182242Z-uistart09`); android-05, o tile subia o `VPNService` porque o modo já era VPN. A plataforma **não** escreve o
`serviceMode` (é do SFA: sem root, sem banco privado). O tile (`religar_pelo_tile`, `rede.cliente_tile`) é LEGADO: só diagnóstico
(`scripts/diag-w8-tile.py`) e nunca fallback automático. O Start da UI deixa o cliente com `serviceMode=VPN`: o tile passaria a
funcionar nele, mas a convergência não depende disso. Importação e provisão não mudaram (a correção é no caminho de recuperação).
`BOOT_RECOVERY_ROOT_CAUSE = OPEN`: os boots 1, 3 e 4 do W8 sem túnel por always-on **não** são explicados por isto.

Prova: `simulated` — `tests/test_rede_religar_interface.py` (os 10 casos: stale do 09, modo já VPN, `tun0` presente, Start não
provado/ambíguo/de outro pacote, guard D, `tun0` sem CONNECTED, abertura que falha, worker remoto, foco/rollback),
`tests/test_rede_aplicacao.py::test_tunel_que_nao_sobe_no_boot_e_religado_pelo_start_da_interface_sem_outro_reinicio`,
`::test_start_que_inicia_o_proxyservice_nao_e_recuperacao_e_o_reinicio_segue`,
`::test_app_que_nao_abre_nao_cai_cegamente_no_tile_e_o_reinicio_segue`,
`::test_gesto_desligado_pela_configuracao_nao_toca_na_interface_e_o_reinicio_segue` e
`tests/test_rede_sonda.py::test_cliente_parado_pelo_teste_e_religado_pelo_start_da_interface_sem_reiniciar_o_aparelho`.
O que segue é do tile e é histórico (modo VPN no android-05): `real`,
30/09 13:30Z, android-05, o código de `religar_pelo_tile` chamado direto pelo adb, duas vezes: cliente parado, sonda
fora da VPN recusada (`Permission denied`), túnel de volta em ~9 s, always-on e bloqueio intactos, tile fora da barra.
`not_run`: o gesto com um app em primeiro plano (o android-05 estava no launcher), o gesto logo depois de um boot
falho pela convergência, e os aparelhos do notebook. **`not_run` também: o `religar_pela_interface` do PRODUTO em aparelho real**
(só o toque manual de diagnóstico foi real); revalidação separada e autorizada em `docs/handoffs/w8-diagnostico-android09.md` §19.4.

| O que foi provado | Nível |
|---|---|
| Reinício do backend com prova válida não para o cliente nem reinicia o aparelho, inclusive na remedição a 90% da validade e com a validade vencida | `simulated`: `tests/test_rede_sonda.py::test_reinicio_do_backend_com_prova_valida_nao_para_o_cliente_nem_reinicia_o_aparelho` (no código de `6997091` o mesmo cenário para o cliente de novo: `paradas == 2`) |
| Revisão nova, cliente de outra instalação, wipe e `verify` invalidam | `simulated`: `::test_vazamento_em_cache_so_vale_para_a_mesma_revisao`, `::test_cliente_vpn_de_outra_instalacao_invalida_a_prova`, `::test_wipe_apaga_a_prova`, `::test_verify_apaga_a_prova_e_o_pedido_sobrevive_ao_reinicio_do_backend` |
| Túnel caído mantém a prova e o bloqueio | `simulated`: `::test_tunel_caido_mantem_a_prova_e_o_bloqueio` |
| Inconclusivo e vazou não aprovam nem se repetem | `simulated`: `::test_inconclusivo_nunca_aprova_e_nao_se_repete`, `::test_vazou_fica_parcial_e_a_tarefa_espera` |
| Intenção antes do `force-stop`; ensaio interrompido não se repete | `simulated`: `::test_intencao_gravada_antes_do_force_stop_e_ensaio_interrompido_nao_se_repete` |
| Desfecho de revisão antiga não vira prova da nova | `simulated`: `::test_desfecho_de_revisao_antiga_nao_vira_prova_da_nova` |
| Adoção só com a correspondência demonstrada | `simulated`: `::test_prova_anterior_a_migracao_e_adotada_sem_parar_o_cliente` e os três casos de `::test_prova_anterior_que_nao_se_demonstra_nao_e_adotada` |
| A 063 num banco com aparelho verificado; a 059 criada depois | `simulated` em SQLite: `tests/test_db.py::test_migracao_063_…` e `::test_migracao_de_numero_menor_criada_depois_ainda_e_aplicada`; em PostgreSQL `not_run` (item 29.14) |
| A leitura do cliente e da data do APK em aparelho real | `real`, 30/09 ~12:50Z, android-02, 03 e 06, como uid 2000, só leitura |
| A 063 numa cópia do banco do central e a decisão de adoção dos quatro aparelhos | `real`, 30/09 12:57Z: aplicada sem divergência; android-02, 03 e 06 adotam, android-05 não (a última medição dele não provou) |
| Reinício do backend no central sem reinício de aparelho, por 6 h | `not_run` até o item 29.4 |

A medição vai para `rede.registrar_medicao` (`method`: "sonda nc http/1.0 + netstats por uid (uid 2000)"), que decide
`trafego_verificado`/`parcial` pelas regras acima. **Comparação entre aparelhos**: a mesma última saída medida (v4 ou
v6) em outro aparelho entra no início do `detail` da medição ("aviso: a mesma saída medida em …"), num evento
`network.updated` de nível `warn` (`acao: saida_compartilhada`, `shared_with`) e em `egress_shared_with`. É aviso,
não bloqueio: sem provedor, todos saem pelo IP do central, e isso é o esperado; o aviso existe para ninguém ler
"perfis diferentes" como "saídas diferentes" (ADR-056 §1).

**Saída esperada × medida × compartilhada (29.6).** São três coisas, e nenhuma substitui a outra:

- **esperada** é configuração: `params.egress_esperado` (e `egress_esperado_ipv6`) no perfil, o IP público que ele
  deve dar. A esperada do APARELHO é a do perfil que dá a saída final — o proxy, se há um atribuído (o tráfego sai do
  túnel e ainda passa por ele); senão a VPN (`rede.perfil_da_saida`). Não há fallback: com um proxy sem esperada por
  cima de uma VPN que a declara, o aparelho não tem saída esperada (a da VPN não é a saída dele);
- **medida** é a da sonda, de dentro do aparelho (`egress_ipv4`/`egress_ipv6` da linha). Se o perfil declara a
  esperada e a medida é outra — ou a família declarada nem foi medida —, entra no que falta ("saída medida X, esperada
  Y do perfil N"; "saída IPv4 não medida, esperada Y do perfil N") e o aparelho fica `parcial`: com `exigida` ou
  `exigida_com_bloqueio` a tarefa espera e a frase da espera traz o motivo; com `livre` nada depende da rede, e o
  `parcial`, o `egress_matches: false` da listagem e um evento `network.updated` de nível `warn`
  (`acao: saida_divergente`, com a medida, a esperada e o perfil) deixam a diferença à vista. Igual à esperada, vale a
  regra de sempre. Perfil sem `egress_esperado`: nada muda. `egress_matches` só tem valor com a revisão pedida já
  medida (`parcial` ou `trafego_verificado`): antes disso a saída da linha é a de um pedido anterior;
- **compartilhada** é a comparação entre aparelhos, acima, e continua aviso. Um aparelho pode medir a própria saída
  esperada e ainda dividi-la com outro: é o que a prévia da atribuição avisa antes (`saida_dedicada_compartilhada`,
  contando quem já tem o perfil e os do lote) e o `egress_shared_with` mostra depois.

**Nenhum aparelho pela saída da casa (29.20).** O objetivo do dono é que nenhum Android saia pelo IP da rede da casa. A
referência é o próprio central: o backend roda na máquina da casa, então o IP que um eco vê dele é o da casa
(`devices/rede_saida_central.py`, `SaidaDoCentral`). Ele mede com os mesmos hosts da sonda do aparelho
(`rede.sonda.hosts_ipv4/hosts_ipv6`), família forçada por socket, HTTP/1.0 na 80, e julga a resposta com a mesma função
(`sonda_rede.ip_da_resposta_http`, fatorada de `ler_ip_de_saida`). A medida roda em segundo plano (laço próprio
`rede-saida-central`, a cada 60 s só mede se passou `rede.sonda.central_ttl_s`, padrão 600; prazo `central_prazo_s`, 6 s
por host; `medir_central: false` desliga) e **nunca bloqueia** a API, que só lê o cache em memória (sem migração). Medida
com mais de 3 TTLs vence; falha solta mantém a anterior até vencer. Uma réplica `ROLE=api` não roda o laço: o motivo diz
isso. Por aparelho, `egress_home` compara a última saída medida (`device_network.egress_*`) com a do central:
  - `ipv4`: o mesmo endereço; `ipv6`: o mesmo /64 (o endereço de saída costuma ser temporário); qualquer lado sem medida =
    `null`, nunca `false`;
  - `ipv6_outside_profile`: o aparelho mediu IPv6 e o perfil de VPN dele não leva IPv6 (`rede_saida_central.perfil_leva_ipv6`:
    o perfil gerenciado pelo central tem endereço /32 IPv4 e não leva; WireGuard externo exige endereço IPv6 em
    `params.address` e rota global em `params.allowed_ips`, padrão `::/0`; sem VPN não leva). A regra olha sempre a VPN,
    mesmo com proxy por cima;
  - `leaves_by_home`: `true` se qualquer um acusa; `false` só com o IPv4 medido diferente e nada incerto; senão `null`;
  - com a revisão pedida ainda não medida (estado fora de `trafego_verificado`/`parcial`), só o positivo vale (a saída
    velha é o que o aparelho faz) e o "não é casa" vira `null`. O notebook da LAN e o perfil `vpn-central-wireguard`
    (WireGuard que termina no central com SNAT) saem pela casa e são justamente o caso acusado;
  - **sem rede pedida** (sem linha em `device_network`, ou linha de pedido vazio) o aparelho sai pela casa por definição:
    `leaves_by_home: true` com `basis: "presumed"` ("sai pela casa (presumido: sem rede pedida)"), um estado próprio, nunca
    `null` nem neutro. A varredura (`ConvergenciaDeRede._trabalho_sem_rede`, só nos motivos `ligou` e `varredura`, aparelho
    ligado, livre e fora da quarentena) o mede com a MESMA sonda de IP (`rede_medicao.medir_saida`: eco HTTP/1.0 por `nc`,
    uid 2000, só IPv4 e IPv6; sem app, DNS, UDP nem vazamento) a cada `rede.sonda.reverificar_s` (a falha repete em
    5 min; `rede.sonda.medir_sem_rede: false` desliga). Fica em `network_measurements` com `method` "sonda de IP sem rede
    pedida (uid 2000)" (sem migração) e vale por 3 `reverificar_s`. Medido igual ao central: `basis: "measured"`, `true`.
    Medido diferente (com o central medido): `basis: "measured"`, `false`, e `measured` mostra o IP (proxy legado ou outra
    rede do host). Falha de sonda, central sem medida ou medida vencida: continua `presumed`, com o motivo. É só
    leitura: não cria linha em `device_network`, não muda estado, política nem revisão, não emite aviso nem dispara
    reaplicação, reinício ou bloqueio de tarefa. `measured` ({ipv4, ipv6, measured_at, source: `device_network` ou
    `probe_no_network`}) traz os IPs em que o veredito se apoia.

O painel mostra, por aparelho, "sai pela casa: IPv4 igual ao do central · IPv6 fora do perfil", "sai pela casa (presumido: sem
rede pedida)" ou "sem medida" e, acima da tabela, "N aparelhos ainda saem pela casa · K presumidos · M sem medida" com a saída
medida do central.

O painel mostra, junto do IP medido, "esperada X (perfil N)" com "confere" ou, em destaque, "a saída medida é outra";
o cartão de perfis mostra a saída que cada um declara e aceita o campo no cadastro; a prévia mostra os dois avisos em
destaque. Trocar a saída dedicada por uma compartilhada tem procedimento próprio: "Reversão do piloto de saída
distinta", no fim desta seção. Prova: `simulated` (os casos de `tests/test_rede_por_aparelho.py` da seção "saída
esperada por aparelho", `tests/test_rede_sonda.py::test_sonda_que_mede_outra_saida_que_a_esperada_fica_parcial_e_a_tarefa_espera`
e três casos de `RedePage.test.tsx`); com provedor e aparelho reais, `not_run` (é o piloto do item 29.7).

**Decisão: a sonda abre o app exigido só quando uma tarefa espera por ele** (substitui a de "não abre por padrão", que
travava; para o dono ratificar). Sem tarefa esperando (varredura, `ligou`, pedido), com `rede.sonda.abrir_apps: false`
(padrão), app parado na janela fica `sem_trafego` — desde o 29.44 isso não segura o `parcial` se outro app passou pelo
túnel, então o que segue abaixo vale quando NADA trafegou. Com política exigida a porta segura TODA tarefa fora de
`trafego_verificado`, inclusive a que abriria o app: o app vinculado e nunca aberto depois do reinício que a própria
aplicação pede travava o aparelho para sempre. Por isso, quando a medição é disparada pela porta (`motivo='tarefa'`, uma
tarefa segurada no aparelho), a sonda abre o app sem tráfego na janela pela tela inicial dele, espera `espera_app_s` e
volta ao início — o que a tarefa faria, e só abrir (nenhum toque, nada publicado, nada enviado). O `parcial` medido sem
abrir não faz a tarefa esperar `reverificar_s`: a porta mede de novo já, abrindo, uma vez (`_Memoria.medida_sem_abrir`);
medido assim e ainda `parcial` (o app aberto não usou a rede), a espera volta a valer e a frase da tarefa traz o porquê.
`abrir_apps: true` abre também sem tarefa esperando. Resíduo conhecido: app exigido NÃO instalado fica `nao_medido` (não
há o que abrir) e segura, e a porta da rede vem antes da porta do app que o instalaria; só a entrega ao ligar
(`vitrine.pendentes_ao_ligar`, quando o app está distribuído para o aparelho; o reinício da própria aplicação passa por
ela) o instala sem tarefa — sem isso, a tarefa espera com o motivo na frase.

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
- **boot** (29.22): o `trafego_verificado` é do Android que foi medido; túnel, DNS e bloqueio se refazem a cada boot. Se o
  aparelho subiu DEPOIS de `verified_at`, a verificação não vale (`rede.verificacao_invalida` → `boot`, frase "o aparelho
  subiu depois da medição…") e a porta espera a medição nova. O marco é `instances.emulator_started_at` (o processo do
  emulador nasceu: boot a frio e acordar do snapshot; está no banco, então o restart do central NÃO invalida quem não
  rebootou) ou, sem processo local (worker), `online_since_mono`, renovado quando o agente conclui `start`/`wake`/`restart`/
  `reset` com boot novo (`readotar_depois_do_worker`); o restart do central conta como entrada nova ali (uma medição a mais,
  o lado seguro). A linha NÃO regride de estado (como `vencida` e `apps`: o estado é da medição, e a invalidez é da porta);
  ao ligar e na varredura a convergência passa a **verificar** em vez de só conferir. **Wake quente também invalida**: o
  processo do emulador é novo e o snapshot restaura o túnel de antes da pausa, e distinguir exigiria um sinal que o
  manager não guarda; o custo é uma medição por acordar. O achado: android-05, 02/10, verificado às 19:20 (#134), ligado a
  frio às 19:50 e liberado às 19:52 com o aparelho acusando "sem internet: DNS não responde";
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
que escreva, no `ActiveStore`, ~3 a 4 s) os perfis, as interfaces com endereço e prefixo, e as regras de entrada
habilitadas do executável ou da porta UDP. Cada regra é julgada contra o que o pacote do notebook encontra **de
fato** (29.8): a porta UDP; o perfil **efetivo** da interface que tem o `endpoint_lan`; essa interface
(`InterfaceAlias`: a própria ou `Any`); o endereço local; a origem (`RemoteAddress`), que precisa **conter** a
sub-rede IPv4 daquela interface, calculada do endereço e do prefixo que o sistema informa; e o programa, quando a
regra tem um. A conclusão segue a ordem do Windows:

| estado | quer dizer | a aplicação num remoto |
|---|---|---|
| `desligado` | o firewall do perfil daquela rede está desligado: não filtra | segue |
| `bloqueado` | uma regra de entrada habilitada bloqueia e vence a que permite (a que o Windows cria quando o aviso "permitir acesso" fica sem resposta). Para bloquear, basta a origem pegar um pedaço da LAN | recusa |
| `liberado` | uma regra permite UDP na porta, vinda da LAN do endpoint, naquela interface e naquele perfil (ou a entrada padrão é Allow). Origem `Any` libera **com aviso**: está mais aberta que o necessário — o servidor escuta também em `[::]` e a Wi-Fi do central tem IPv6 público | segue |
| `regra_obsoleta` | a regra existe, mas aponta (`-Program`) para um executável que não é o binário atual do servidor (o caminho do sing-box tem a versão no nome; a atualização deixa a regra para trás) | recusa |
| `sem_regra` | entrada padrão Block e nenhuma regra que cubra. As que existem e não cobrem vão ditas no `detail` com o porquê: outra interface, outra sub-rede, só parte da LAN (um endereço só), outro perfil | recusa |
| `desconhecido` | não leu (fora do Windows, erro, prazo) ou a regra que decidiria não pôde ser conferida (o `endpoint_lan` não é endereço desta máquina, regra presa a um tipo de interface ou a um usuário local): não prova nem nega | segue, com a nota |

Regra de **outro** programa que abre UDP em qualquer porta (TeamViewer, Teams: há várias no central) não conta para
nada. Cada leitura traz três comandos, todos montados com o que o sistema informou:

```powershell
# commands — o que FALTA rodar (vazio quando nada falta), num PowerShell de administrador do central:
$n='Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)'; Get-NetFirewallRule -DisplayName $n -EA 0 | Remove-NetFirewallRule; New-NetFirewallRule -DisplayName $n -Direction Inbound -Action Allow -Protocol UDP -LocalPort 51820 -RemoteAddress <sub-rede IPv4 da interface do endpoint_lan> -InterfaceAlias '<interface do endpoint_lan>' -Profile Any

# inspect_command — ver a regra (só leitura): perfil, porta, origem, interface e programa
Get-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)' | ForEach-Object { [pscustomobject]@{ Regra = $_.DisplayName; Habilitada = $_.Enabled; Acao = $_.Action; Perfil = $_.Profile; Protocolo = ($_ | Get-NetFirewallPortFilter).Protocol; Porta = ($_ | Get-NetFirewallPortFilter).LocalPort; Origem = ($_ | Get-NetFirewallAddressFilter).RemoteAddress; Interface = ($_ | Get-NetFirewallInterfaceFilter).InterfaceAlias; Programa = ($_ | Get-NetFirewallApplicationFilter).Program } } | Format-List

# revert_command — desfazer
Remove-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)'
```

O porquê de cada parte da regra: **sem `-Program`**, porque o caminho do sing-box leva a versão
(`data/rede/sing-box-1.14.2-windows-amd64/`) e a regra morreria na atualização; **origem = a sub-rede da LAN**
(no central de hoje, `192.168.1.0/24`), e não `Any` (exporia a porta pelo IPv6 público da Wi-Fi) nem `LocalSubnet`
(cobre também o vEthernet do WSL); **`-InterfaceAlias`**, porque o servidor escuta em todas as interfaces;
**`-Profile Any`**, porque a restrição de verdade é a origem e a interface, e o perfil muda quando o Windows
reclassifica a rede (Public ↔ Private). É **idempotente**: tira a regra de mesmo nome antes de criar (`-EA 0` é
`-ErrorAction SilentlyContinue`: sem regra anterior, segue calado), então rodar duas vezes deixa uma regra só — e é
assim que o mesmo comando troca uma `regra_obsoleta`. O que o sistema não informou (o `endpoint_lan` não é de
nenhuma interface, é um nome, ou veio sem prefixo) vira o marcador `<SUB-REDE-IPV4-DA-LAN>` /
`<INTERFACE-DA-LAN>`, que o PowerShell recusa (a linha inteira não roda), e o `missing` e o `detail` dizem o que
faltou — com os endereços que a máquina tem, para corrigir o `endpoint_lan`. Sem a regra, a inspeção responde que
não achou nenhuma com esse nome. Para cada regra que bloqueia vem um `Disable-NetFirewallRule -Name '<regra>'` (a de
política de grupo é dita, porque o comando local não a desfaz).

Na aplicação num remoto a convergência relê o firewall: os fechados (`bloqueado`, `sem_regra`, `regra_obsoleta`)
**recusam** (`pendente` com o comando no `error`: aplicar assim deixaria o túnel "no ar" sem handshake e, com
bloqueio, o aparelho sem rede); `desconhecido` segue com a nota na evidência, e a conexão do par no log do servidor e
a sonda decidem. O `conectado` de um remoto sem conexão no log diz o endpoint e o estado do firewall. Com par remoto
no servidor, o laço de 60 s relê o firewall a cada 10 min (cache); sem par remoto, não lê.

Rotas: `GET /api/network/server` ganha `remote_access` (`lan_endpoint`, `wireguard_udp_port`, `remote_peers`,
`firewall` — a última leitura, `null` se ainda não lida: o GET não roda PowerShell) e `remote` em cada par;
`POST /api/network/server/firewall-check` relê já e devolve o `remote_access`. O `firewall` traz `state`, `detail`,
`endpoint`, `profile`, `interface`, `endpoint_is_local`, `allowing_rules`, `blocking_rules`, `commands` e, desde o
29.8, `lan_subnet`, `warnings`, `stale_rules`, `ignored_rules` (as que não cobrem, com o porquê), `missing`,
`inspect_command` e `revert_command`. No painel Rede, o cartão "Servidor do central" mostra o endereço da LAN, os
aparelhos remotos, o estado do firewall e o comando, com "Conferir firewall" (o cartão ainda não conhece o estado
`regra_obsoleta` nem mostra a inspeção, a reversão e os avisos: pendência do painel).

Leitura real do firewall (só leitura, central): em 29/09 22:12 UTC (worktree `evo3-d1` sobre `3823f4b`) e de novo
em 30/09 12:48 UTC com o leitor do 29.8 (worktree `p3-firewall`, pelo `powershell.exe` 5.1, 2,7 a 4,0 s, 15 regras
candidatas): Wi-Fi 192.168.1.81/24 no perfil **Public**, os três perfis ligados com entrada Block, nenhuma regra para
o sing-box nem para a UDP 51820 → `sem_regra`, com `lan_subnet 192.168.1.0/24`, `interface Wi-Fi` e o comando acima
preenchido com os dois. A regra **não foi criada** (é do dono): o `liberado` lido no sistema real é `not_run`.
**Procedimento do dono** para a prova num remoto:

1. `rede.servidor.endpoint_lan: 192.168.1.81` no `config/config.yaml` do central (confira o IP; DHCP muda — uma
   reserva no roteador evita refazer tudo) e reinicie o backend;
2. num PowerShell de administrador do central, o comando que `POST /api/network/server/firewall-check` devolve em
   `commands`; confira com o `inspect_command` (`Origem 192.168.1.0/255.255.255.0` — o Windows mostra a máscara
   por extenso —, `Interface Wi-Fi`, `Perfil Any`, `Programa Any`); relido, o estado vira `liberado` sem aviso;
3. um aparelho do notebook (android-09…15), sem conta real ou com a autorização por aparelho (ADR-056 §7), com um
   perfil de VPN `params.servidor: "central"` e `POST /api/network/devices/{id}/apply`;
4. a prova: `inbound connection from 10.66.0.N` do endereço daquele aparelho no log do servidor
   (`GET /api/network/server` → `peers[].last_connection`), `conectado` lido como uid 2000 e a sonda (25.5) com a
   saída medida de dentro do aparelho; o túnel SSH e o adb seguem de pé (a árvore de tela responde).

Limites conhecidos: o perfil antigo fica dentro do SFA a cada reaplicação (sem root não há como apagá-lo; o novo fica
selecionado sozinho); o proxy SOCKS5 perde o UDP que não é DNS (medido); o servidor escuta a UDP 51820 em todas as
interfaces (o endpoint não tem campo de escuta); um perfil `wireguard` externo leva UMA chave e serve a um aparelho
por vez (P1); o aparelho do worker depende do `endpoint_lan` e da regra de firewall do dono (25.7), e a regra
proposta, restrita à sub-rede da LAN, só vale para o notebook na mesma sub-rede do central (um worker fora dela não
é coberto, e nada aqui o prova); a leitura do firewall só considera as regras UDP que cobrem a porta e as do
executável — uma regra genérica de protocolo `Any` sem programa não é lida (é onde moram as regras de pacote de app
e as presas a um usuário local, que não dizem respeito ao sing-box), e uma regra restrita a um único endereço da LAN
(só o notebook) é lida como fechada, porque não contém a LAN do endpoint.

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
| Sonda UDP com repetição e pernas separadas: até 3 datagramas de 2 s por perna numa ida só, tentativa e tempo por perna no `detail`, `udp_dns_ok`/`udp_ntp_ok` na listagem e no painel, sem mudar a regra que libera tarefa (29.5) | implementado | `simulated` (`tests/test_rede_sonda.py::test_comando_de_udp_repete_o_datagrama_numa_ida_so_e_diz_tentativa_e_tempo`, `::test_leitura_de_udp_por_perna_e_a_saida_antiga_ainda_e_lida`, `::test_falha_transitoria_de_udp_nao_derruba_a_perna_e_a_persistente_fica_registrada` — que falha no comando de um datagrama só, com `UDP DNS 0 B` — e `::test_perna_de_udp_falha_nao_segura_a_tarefa_e_aparece_por_perna`; `RedePage.test.tsx`, 1 caso); o texto do comando foi ensaiado num `sh` local com `nc` falso (sintaxe e lógica do laço, não o mksh); num aparelho real `not_run` |
| Saída da casa (29.20): saída do próprio central medida em segundo plano (`rede_saida_central`), `egress_home` por aparelho (IPv4 igual, IPv6 no mesmo /64, IPv6 fora do perfil) e `central_egress` na listagem, resumo e selo no painel | implementado | `simulated` (`tests/test_rede_saida_central.py`, 20 casos com medidor injetado e eco local em 127.0.0.1; `tests/test_rede_por_aparelho.py::test_saida_da_casa_acusa_por_aparelho_e_nunca_limpa_sem_medida` e `::test_aparelho_sem_rede_pedida_e_presumido_e_a_sonda_so_le` (presumido, medido igual, medido diferente e falha de sonda); `RedePage.test.tsx`, 3 casos); medir o central real e conferir o android-09 com `vpn-central-wireguard` `not_run` |
| Saída esperada por aparelho: `params.egress_esperado` validado como endereço público, comparação com a saída medida (diferente vira `parcial` com o motivo), `egress_expected`/`egress_matches` na listagem e no painel, avisos de saída dedicada compartilhada e de troca por compartilhada na prévia (29.6) | implementado | `simulated` (`tests/test_rede_por_aparelho.py`, 5 casos: `::test_cadastro_valida_a_saida_esperada_como_endereco_publico`, `::test_saida_medida_diferente_da_esperada_vira_parcial_e_a_igual_verifica`, `::test_proxy_encadeado_decide_a_saida_esperada`, `::test_politica_livre_com_saida_diferente_mostra_o_estado_e_nao_segura_tarefa`, `::test_previa_avisa_saida_dedicada_compartilhada_e_troca_por_compartilhada`; `tests/test_rede_sonda.py::test_sonda_que_mede_outra_saida_que_a_esperada_fica_parcial_e_a_tarefa_espera`; `RedePage.test.tsx`, 3 casos); com provedor externo e aparelho real `not_run` (piloto do 29.7) |
| Portão de rede no scheduler: validade de `trafego_verificado` (`rede.validade_verificacao_s`), app de conta vinculada depois da medição, remedição disparada pela porta e adiantada pela varredura, suspensão entre etapas do mesmo app e na troca de app, releitura do aparelho entre etapas, reinício que sai com o objetivo suspenso pela rede, app nunca aberto sem travar a tarefa (25.6) | implementado | `simulated` (`tests/test_rede_portao.py`, 12 casos: aparelho de rede e servidor falsos, provedor por regras e aparelho de QA falso; os casos do scheduler falham sem a porta entre etapas, `::test_queda_do_tunel_no_meio_e_vista_entre_etapas_e_o_reinicio_sai` falha sem a releitura e sem a exceção do reinício, `::test_app_nunca_aberto_nao_trava_a_tarefa_com_politica_exigida` falha sem abrir o app pela porta); num aparelho real `not_run`: depende do 25.4/25.5 reais |
| Aparelhos do worker: perfil por `adb reverse`, endpoint da LAN (`rede.servidor.endpoint_lan`), leitura do firewall do central (porta, interface, origem e perfil efetivos; regra obsoleta) com o comando do dono (sem `-Program`, idempotente), a inspeção e a reversão, recusa com firewall fechado, `remote_access` e `POST …/firewall-check`, cartão no painel (25.7, 29.8) | implementado | `simulated` (`tests/test_rede_worker.py`, 24 casos: aparelho remoto e leitura do firewall falsos; os de interface e de sub-rede erradas falham no leitor anterior, que as lia `liberado`; `RedePage.test.tsx`, 2 casos); a leitura do firewall rodou de verdade, só leitura (29/09 e, com o leitor do 29.8, 30/09: `sem_regra`, sub-rede e interface lidas do sistema; sintaxe dos três comandos analisada no `powershell.exe` 5.1 sem executá-los); regra criada e lida como `liberado`, e aplicação num aparelho do notebook, `not_run`: dependem de o dono configurar o `endpoint_lan` e criar a regra de firewall |

### Reversão do piloto de saída distinta

Para o aparelho **com conta real** que passou a sair por um perfil dedicado (com `egress_esperado`; piloto do item
29.7), quando o servidor dedicado sai do ar ou o piloto termina.

**O que não fazer.** Voltar o aparelho para o perfil do central (ou tirar o perfil) por uma reatribuição comum. A prévia
avisa (`saida_dedicada_trocada_por_compartilhada`), mas aviso não segura o pedido. Também não afrouxar a política "para
o aparelho voltar a ter internet": sair de `exigida_com_bloqueio` tira o bloqueio, e o tráfego passa a sair pela
interface física — a mesma troca de saída, por outro caminho. A plataforma não faz nenhuma das duas sozinha: a
convergência nunca muda o perfil nem a política pedidos.

**O procedimento.**

1. **Manter** o perfil dedicado e a política `exigida_com_bloqueio`. Sem o túnel, o bloqueio do Android recusa o que
   sai fora da VPN: o aparelho fica **sem rede, explicitamente** (`configurado` ou `parcial`, a tarefa espera e diz
   por quê), e a conta não aparece por outro IP. Fica assim até uma decisão autorizada do dono.
2. Decidida a mudança de saída, **reatribuir com a confirmação por aparelho** (`confirm_real_account` com o id; no
   painel, o diálogo daquele aparelho), depois de ler o aviso da prévia. É uma mudança de saída como qualquer outra:
   revisão nova, `pendente`, aplicação, teste de vazamento e medição.

Aparelho de QA sem conta real não pede confirmação; o aviso da prévia é o mesmo.

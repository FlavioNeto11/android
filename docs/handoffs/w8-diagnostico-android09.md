# W8 do android-09 — diagnóstico instrumentado (estado e plano, 01/10/2026)

**W4 = CLOSED** (causa em código, corrigida em `658e5bb`; K-067 e checkpoint 12 de
[pendencias-evolucao3.md](pendencias-evolucao3.md)). **W8 = OPEN**: depois do teste de vazamento real no android-09 o
túnel não voltou. Este arquivo é o plano do diagnóstico; a evidência bruta do dia está nos checkpoints 10–12 daquele
arquivo e não é repetida aqui. Os §1–§12 são o plano de 01/10 (nada havia sido escrito no aparelho até então). **A Fase A1 foi
executada em 01/10 às 16:39Z (§13): o gesto do tile isolado NÃO subiu o túnel no android-09.** A2 não rodou (regra do plano)
e o W8 completo segue aberto.

## 1. O que aconteceu (UTC, `events`, `commands.result`, `backend.log`)

| Hora | Evento |
|---|---|
| 13:19:45 | rede rev 2 pedida, política `exigida_com_bloqueio` |
| 13:20:30 → :31 | aplicada (`always-on=sfa`, `lockdown=1` relidos); **reinício 1** ("o always-on só vale no boot") |
| 13:21:47 → 13:24:15 | boot 1: 148 s esperando `tun0`, tile clicado, "o túnel não subiu em 20 s"; **reinício 2** |
| 13:25:32 → :35 | boot 2: **`conectado`** com 67 s de uptime (único boot bom dos 4) |
| 13:26:12 → :27 | teste de vazamento automático: cliente parado, sonda recusada `Permission denied` = **bloqueio provado** |
| 13:26:55 | `_tunel_de_volta` falso; tile clicado, "não subiu em 20 s"; `_religar_pelo_boot`; **reinício 3** |
| 13:28:11 → 13:30:37 | boot 3: sem túnel, tile clicado, sem túnel; **reinício 4** |
| 13:31:54 → 13:34:17 | boot 4: sem túnel (ligado há 182 s), tile clicado, sem túnel; `reinicios_max=2` → falha, linha `pendente` |
| 13:37:18 → 13:41:48 | rollback (rev 3, livre): `always-on` e lockdown tirados, reinício 5, conferido (`always-on=null`, `lockdown=0`, sem `tun0`) |

Três falhas diferentes dentro do mesmo "W8" (não confundir):

- **A) Falha de subida do túnel depois do boot.** O `startAlwaysOnVpn` roda uma vez por boot. No android-09: 1 boot bom em 4.
  No android-05 (30/09) foram 2 de 7. É a falha **conhecida** (P16): ANR de `startForeground` com o convidado sem CPU, ou o
  serviço que sobe e para. Estatisticamente compatível com o que já se media.
- **B) Falha do tile.** O gesto (`religar_pelo_tile`) rodou 4 vezes no android-09 e foi `CLICOU=1` nas 4 sem `tun0` em 20 s.
  No android-05 religou 5 de 5 (túnel em ~9 s). Esta é a diferença real a explicar. O relatório §27.3 já dizia `not_run`
  para "o gesto nos aparelhos do notebook".
- **C) Específica do pós-teste de vazamento.** O `force-stop` do teste deixa o pacote `stopped` (flag que persiste). Uma
  única observação (13:26:55), com a mesma falha do tile de B. O boot 1 (sem `force-stop` antes) já tinha falhado, então
  C não explica sozinho nada.

## 2. android-05 (bom) × android-09 (ruim): só o comprovável

Iguais: imagem `android-34;google_apis;x86_64`, API 34, `gpu_mode host`, cliente sing-box 1.14.2 no 05 (a versão do 09
não está persistida: a linha `device_network` foi apagada no rollback), `-lowram`, 2 vCPU.
Diferentes: o 05 é local no central; o 09 é de **worker remoto** (adb por túnel `127.0.0.1:15555`). O 05 não tinha
eventos de pressão de CPU nas janelas de 30/09; o 09 teve load 13,9 e 11,6 durante o teste (nos boots o aparelho está
fora do ar e não há medição). **Não reconstruível:** estado `stopped`, pid do SystemUI e do cliente, e o conteúdo bruto
do comando do tile (não é persistido). Correlação não é causa.

## 3. O comando do tile (`comando_de_religar`)

`Q0` o tile já estava em `sysui_qs_tiles` (se sim, não é removido no fim) · `P1`/`P2` pid do SystemUI antes/depois ·
`Q` o tile está na barra após o `add-tile` · `T` há `tun0` com endereço (se sim, clicar **desligaria** a VPN) ·
`CLICOU` = 1 só com P1=P2, Q>0 e T=0. O log "foi clicado e o túnel não subiu" só sai com `CLICOU=1`; logo, nos 4 casos do
android-09, SystemUI estável e tile na barra. **O `click-tile` roda com saída e erro descartados**: não se sabe se o
clique foi *executado com sucesso*. Os valores brutos não são persistidos.

## 4. Hipóteses (numeração do pedido)

| | Para | Contra | Desconhecido | Como distinguir |
|---|---|---|---|---|
| H1 SystemUI reinicia | — | `CLICOU=1` ⇒ P1=P2, 4/4 | — | pid do SystemUI na janela |
| H2 tile não entra | — | `CLICOU=1` ⇒ Q>0, 4/4 | — | `sysui_qs_tiles` por amostra |
| H3 clique não executa | tile 0/4 contra 5/5; saída descartada | nada direto | retorno do `click-tile` | `TileService`/`onClick` no logcat; retorno do comando |
| H4 pacote preso em stopped | `force-stop` do teste | boot 1 falhou sem `force-stop`; no 05 religou após `force-stop` | `stopped=` | `dumpsys package … stopped=` antes/depois |
| H5 cliente inicia, serviço não | modo conhecido no 05 | — | logs do cliente | pid do cliente + `startForeground`/ANR |
| H6 `tun0` sobe, sem CONNECTED | — | `tun0` ausente nas 4 leituras finais | — | `V` (CONNECTED) com `tun0` |
| H7 lockdown incoerente | — | regras ativas como esperado nas 4 | com `tun0` no ar | nº de linhas `UIDs:` |
| H8 sem handshake | — | `tun0` nunca subiu depois do tile | handshake do boot 2 (o `last_connection` lido era anterior a ele) | `last_connection` do peer |
| H9 fallback de boot falha sob carga | 3 de 4 boots sem túnel; 5/7 no 05; load alto | — | log do `startAlwaysOnVpn` | ANR/`Start proc` no logcat do boot |
| H10 outra | worker remoto; CPU do convidado | — | tudo | comparar com o caso bom na mesma janela |

Memória do notebook é **controle**, não hipótese (`memoria-do-notebook.md`, K-067).

## 5. Classificador (primeiro ponto quebrado; `scripts/diag-w8.py classificar`)

Ordem do caminho do tile: **F1_SYSTEMUI_RESTART** (pid do SystemUI mudou) → **F2_TILE_NOT_ADDED** (tile ausente em ≥3
leituras) → **F3_TILE_NOT_CLICKED** (logcat capturado sem sinal do tile e o cliente não nasceu) →
**F4_PACKAGE_STAYS_STOPPED** (`stopped=true` e nenhum processo) → **F5_VPN_SERVICE_NOT_STARTED** (ANR/`startForeground`, ou
processo sem serviço no logcat) → **F6_TUN_NOT_CREATED_AFTER_TILE** (serviço subiu, sem `tun0`; era `F6_TUN_NOT_CREATED` até 3060cd9) → **F7_VPN_NOT_CONNECTED** (`tun0` ≥5 s
sem `VPN CONNECTED`) → **F8_LOCKDOWN_MISMATCH** (regras ≠ política) → **F9_NO_WIREGUARD_HANDSHAKE** (peer sem conexão nova).
Modo `boot`: **F10_BOOT_RECOVERY_FAILED** (180 s de uptime sem `tun0`). **F_OTHER**: tudo passou mas o túnel caiu no fim.
Regras: estágio sem dado = `UNKNOWN`; falha com estágio anterior desconhecido = `UNKNOWN` com `candidato`; ausência de
log **nunca** vira sucesso. Testado com 7 fixtures sintéticas + o caminho saudável (`scripts/tests/test_diag_w8.py`).

## 6. Instrumentação (`scripts/diag-w8.py`, **somente leitura**, não existe modo que escreva)

- **Aparelho:** uma ida `adb shell` por amostra, ~2 s (uptime, `boot_completed`, `always_on`, `lockdown`, `tun0`, pid do
  cliente, pid do SystemUI, tile presente); a parte pesada (`dumpsys connectivity`: CONNECTED e regras `UIDs:`; `stopped=`)
  a cada 5 amostras, porque o convidado de 2 vCPU sem CPU é a hipótese de fundo e o coletor não pode derrubá-lo.
- **Logcat:** um laço contínuo que reconecta (o reinício derruba o adb e apaga o buffer), com horário e filtro (cliente,
  sing-box, VPN/ConnectivityService, TileService, ANR, force-stop, `startForeground`, `Start proc`, SystemUI que morre),
  teto de 20 MB.
- **Central (`mode=ro` + GET):** `device_network` (`desired_rev`, `applied_rev`, `state`, `policy`, `leak_*`, `detail`,
  `error`), comandos `device.network` e `restart` com a recusa, eventos `network.updated`, peer em `/api/network/server`.
- **Notebook:** SSH só se responder. **Hoje não responde**: `farm-tunel@192.168.1.19` dá `Permission denied (publickey…)` e,
  de qualquer modo, a chave do projeto é restrita a túnel (`command="exit"`). Fallback: o que o agente do worker já reporta
  (CPU, RAM livre, `swap_used_pct`). `Pages/sec`, `Committed Bytes`, `Commit Limit` e CPU do `qemu` exigem acesso de
  leitura que **só o dono pode dar** (ou rodar o PowerShell de `diag-w8.py comandos` localmente no notebook).
- Uso: `python scripts/diag-w8.py preflight`, `coletar --duracao 900`, `classificar --run <pasta> --desde … --ate …`.
  A saída vai para `data/diag-w8/` (fora do Git).

## 7. Abort gate: manutenção do worker (provado por teste automatizado, sem worker real)

Teste: `backend/tests/test_rede_aplicacao.py::test_manutencao_do_worker_recusa_o_reinicio_da_rede_e_nao_o_device_network`
(com a manutenção desligada ele falha). O que está provado:

- `device.network` **não** passa por `_precheck` (só `despacho.py:631` lê a manutenção): trabalho em voo e novo seguem.
- O `restart` da convergência passa por `pedir_ciclo_de_vida` → `_precheck` → **recusado** com "worker … em manutenção":
  fica um comando `restart` em `rejected` com o motivo no histórico; o aparelho não recebe `restart`; a linha de rede e a
  evidência permanecem. (Outro `None` é o verbo fora de `worker_verbs`: aí **nem** há comando; o worker-lan-01 declara `restart`.)
- `_pedir_reinicio` soma ao `mem.reinicios[rev]` **antes** de olhar o resultado: a recusa conta para `reinicios_max=2`;
  `mem.espera_ate = agora + _ESPERA_DA_RELEITURA_S` (**300,0 s**). Sem manutenção, o pedido seguinte é aceito.
- Os outros pedidos de `restart` (remediação, `state.py`) usam o mesmo caminho e são recusados igual. O que o agente do
  worker faria localmente não tem dado.
- Durante os 300 s, `_acao` devolve nada para `varredura`/`tarefa`, mas **não** para `ligou`/`pedido` (um POST de apply
  passa). Ao voltar a manutenção para `false` **nada dispara sozinho**: vale a próxima varredura depois de `espera_ate`.
- **Pela leitura do código (não provado por teste):** com a manutenção ligada, cada passada de `conectar` repete o tile
  (a releitura com regras ativas) e soma uma recusa: ~+300 s segunda tentativa, ~+600 s teto 2 → `pendente`, ~+900 s `aplicar`
  (reprovisiona e perturba a evidência). Logo, **não é congelamento indefinido**: ~10 min até `pendente`, ~15 até reaplicar,
  com 2 tentativas extras do tile no meio.

**Armar cedo (no t0) não serve:** bloquearia o reinício do apply ("o always-on só vale no boot") e os boots necessários
para chegar a `conectado`. O ponto de armar é o evento `network.updated … → conectado`: dali ao fim do teste de
vazamento passaram 80 s em 01/10 (13:25:35 → 13:26:55), uma janela de **dezenas de segundos para automação**, não de
1 s para uma pessoa. Isso exigiria um modo com flag no coletor (não implementado: não há mutação aqui). Antes do rollback
a manutenção tem de voltar a `false`, senão o reinício do rollback também é recusado. A manutenção vale para o **worker
inteiro** (os outros aparelhos do notebook estão parados).

## 8. FASE A futura — "TILE-ONLY" (recomendada antes do W8 completo)

Possível pelo estado atual (perfil dentro do cliente, `always-on=null`, `lockdown=0`, sem `tun0`, sem peer, cliente com
processo vivo e `stopped=false`, aparelho online sem linha de rede): `force-stop` → preparar o tile → `click-tile`
**com saída e retorno capturados** → observar pacote/processo/serviço/`tun0`, com o coletor rodando. Sem `assign`, sem peer
no servidor, sem tocar nos outros aparelhos, sem teste de vazamento, sem reinício automático (não há linha de rede).

- **Prova:** F3 (clique executado ou não, com o retorno do comando), F4 (`stopped` antes/depois), F5/F6 (processo, serviço,
  `tun0`) **no android-09, com impacto só nele**. Se o tile não religar nem aqui, o defeito é do gesto neste aparelho e
  não do W8 nem do boot.
- **Não prova (verbatim):** (1) o comportamento com `always-on` + `lockdown=1`, que é a condição real do W8: o sistema pode
  segurar ou reiniciar a VPN de outro jeito; (2) `CONNECTED` sob as regras de lockdown (F7/F8); (3) a recuperação de
  `force-stop` sob lockdown (H4 na condição real). Também não prova F9 (sem peer, a ausência de handshake é esperada) nem o
  boot com o convidado sem CPU (H9).
- **Impacto:** o aparelho fica sem internet enquanto o `tun0` estiver no ar apontando para um servidor sem peer; desfaz-se
  clicando o tile de novo ou parando o cliente. É conta zero (QA). Requer um acionador pontual **com flag**: é o
  `scripts/diag-w8-tile.py` (`6696931`, seguro por padrão, só android-09); o coletor continua só leitura.

## 9. W8 completo: sequência mínima (se a Fase A não bastar)

1. Baseline (leitura) e coletor ligado. 2. `POST /api/network/assign` direto com `exigida_com_bloqueio` (rev nova), sem
passar por `exigida`. 3. O apply pede o reinício; cada boot tem ~30% de chance de subir o túnel. 4. A cada boot sem túnel
o código tenta o tile e reinicia (já é a Fase A de graça, com o teto 2). 5. Se chegar a `conectado`, o teste de
vazamento roda sozinho na varredura seguinte (≤60 s). 6. Observar; parar assim que o primeiro ponto for classificável
(manutenção armada em `conectado`, com flag). 7. Rollback. **Impacto:** cadastrar o par reinicia o servidor VPN do central e
derruba por segundos o túnel de 02, 03, 05 e 06 (03 e 06 têm conta real): só numa janela sem tarefa.

## 10. Rollback comprovável

`POST /api/network/assign` política `livre` (como em 13:37Z) com a manutenção em `false` → aguardar o reinício de remoção
→ provas: `always-on=null`, `lockdown=0`, `tun0` ausente, sem rede VPN conectada, sem regras de bloqueio, cliente sem
segurar a VPN, **conectividade `healthy`** (depois do rollback anterior houve minutos de "sem internet validada"),
android-09 `online`, `GET /api/network/server` sem peer do 09, e os aparelhos 02/03/05/06 sem mudança de estado.

## 11. Fatos provados × desconhecidos

**Provados:** W4 resolvido; bloqueio fora da VPN provado (13:26:27); 4 reinícios na revisão, o teto é 2 por fase e zera ao
conectar; o tile foi clicado nos 4 casos (SystemUI estável, tile na barra, sem `tun0`); o gate de manutenção recusa
`restart` e não `device.network`; o android-09 está hoje online, sem rede, sem peer, `always-on=null`, `lockdown=0`.
**Desconhecidos:** se o `click-tile` é executado com sucesso; `stopped` do pacote nos 4 casos; logs do cliente/boot; versão
do cliente do 09; handshake no boot 2; contadores de memória do notebook (SSH negado); se o gesto funciona no 09 isolado.

## 12. Se o defeito se confirmar (arquivos, não alterados)

`backend/app/devices/rede_aplicacao.py` (`comando_de_religar`, `religar_pelo_tile`: saída e retorno do clique),
`backend/app/devices/rede_convergencia.py` (`_conectar`, `_religar_sem_reinicio`, `_reiniciar_ou_desistir`),
`backend/app/config.py` (`reinicios_max`, espera do tile), `backend/tests/test_rede_aplicacao.py`, `docs/conhecimento/aprendizados.md`.
Só se o coletor externo não bastar para distinguir H3–H6 (decisão de 01/10: sem mudar produção para logar melhor antes).

## 13. Resultado da Fase A1 (01/10/2026, 16:39Z, `real`)

Execução: commit `6696931` (`scripts/diag-w8-tile.py --fase a1 --execute --instance android-09`), central `C:/git/android`, coletor
`scripts/diag-w8.py coletar` em `data/diag-w8/20261001T163900Z/` (fora do Git; PID 45256→2144, 16:39:00Z–16:42:08Z, 187 amostras;
SSH do notebook `SSH_UNAVAILABLE`, só CPU/RAM/swap do agente). Só o android-09; sem `assign`, peer, reinício, servidor WireGuard,
IA paga nem conta real.

**Linha de base (17 precondições lidas, todas ok):** online, `qa-user-09`, sem execução/comando aberto, adb `127.0.0.1:15555` como
uid 2000, `always_on=null`, `lockdown=0`, sem `tun0`, sem VPN CONNECTED, sem linha `device_network`, sem peer, `worker-lan-01`
conectado (estado `degraded` pelo relógio +6,6 s e `swap_used_pct` 81,6: só contexto). Cliente com processo vivo (pid 6512),
`stopped=false`, tile **ausente** (Q0=0), SystemUI pid 2529, uptime 10739 s. A comparação com o force-stop era válida.

**Gesto** (o `comando_de_religar` do produto, importado, trocando só o clique para capturar a saída; o teste confere): `Q0=0 P1=2529
P2=2529 Q=1 T=0 CLICOU=1`. `click-tile`: **exit 0, stdout vazio, stderr vazio, 13 ms** no aparelho (5,46 s o gesto inteiro).
Observação silenciosa de 37,4 s (9 leituras a cada ~4 s): `tun0` ausente em todas, pid do cliente (6512) e do SystemUI (2529) fixos;
leitura final `stopped=false`, VPN não conectada, 0 regras de bloqueio.

**O que o logcat completo do aparelho mostra** (UTC; `-b all -d` e `--pid=6512`, lidos logo depois, porque o filtro do coletor
descarta as linhas do próprio app):

| Hora | Evento |
|---|---|
| 16:39:33.207 | SystemUI registra o clique no `io.nekohasekai.sfa.bg.TileService` |
| 16:39:33.252 | `Background started FGS: Allowed … .bg.ProxyService; code:OP_ACTIVATE_VPN` |
| 16:39:33.540 / .567 | `am_foreground_service_start` do `ProxyService`; notificação do serviço postada |
| 16:39:33.614 | o app pede rede ao `ConnectivityService` (LISTEN_FOR_BEST); nenhuma linha `Established by` |
| 16:39:34.948 | `cache.db` do cliente escrito (o núcleo começou a inicializar) |
| 16:39:35.108 | `avc: denied { bind } … netlink_route_socket` (bug b/155595000); antes, `denied { read } somaxconn` às 33.452 |
| 16:39:35.336 | **`am_foreground_service_stop` do `ProxyService`, `STOP_FOREGROUND`, 1789 ms depois do início**; notificação cancelada 35.398 |

Depois: `dumpsys activity services` sem `ProxyService`; `ACTIVATE_VPN: allow` (o consentimento do VPN está concedido, lido por
`cmd appops`); sem ANR, sem crash (`CrashReport-Application.log` com 0 bytes e data de 10:41 -0300, anterior), sem notificação
de alerta. O conteúdo de `crash_reports` não foi lido (pode ter chave); só listado.

**Classificador** (`diag-w8.py classificar --bloqueio nao`, 16:39:20Z–16:40:12Z): `F6_TUN_NOT_CREATED` (hoje `F6_TUN_NOT_CREATED_AFTER_TILE`); F1–F5 `PASS`; F7–F9 não
alcançados (F9 não se aplica: não há peer). **Ressalva:** o `PASS` de F5 casou `startForegroundCount:0` numa linha do
ActivityManager, não um serviço de VPN estabelecido; a leitura certa da evidência é "o serviço subiu e parou sozinho em 1,8 s, sem
`tun0`". O motivo do auto-stop não está no logcat (as mensagens do núcleo vão para o arquivo do app, ilegível sem root).

**A2: `NOT_RUN`** (regra: A1 falhou em F5/F6 → sem force-stop, rollback, encerrar). **Rollback:** o `tun0` já estava ausente (nenhum
clique de desligar nem force-stop); tile removido (Q0=0). Relido às 16:42Z: `always_on=null`, `lockdown=0`, sem `tun0`, sem VPN
CONNECTED, tile ausente, cliente vivo (6512) e `stopped=false` como antes, android-09 `online`, sem linha de rede, sem peer, sem
comando aberto. A última checagem de conectividade do central (`healthy`) é de 16:37:46Z, **anterior** ao A1; não houve uma nova.
Nenhum reinício; outros aparelhos não tocados.

**Diagnóstico: `F6_TUN_NOT_CREATED_AFTER_TILE`** (o gesto isolado não sobe o túnel neste aparelho, sem force-stop, sem always-on e
sem lockdown). O rótulo `TILE_BUG_CONFIRMED` usado na primeira redação foi abandonado: o tile não é o ponto quebrado.
H1–H4 não são a causa principal sem evidência nova. Mais preciso: o **clique chega** (F3), o `ProxyService` do cliente **sobe e se
desliga em 1,8 s** sem criar o `tun0` (o modo "serviço que sobe e para" do P16/H5), **sem** o force-stop (H4 fora como causa
única), **sem** o SystemUI reiniciar (H1) e com o tile na barra (H2). Fora do alcance desta execução: (a) por que o serviço para
(sem log do núcleo); (b) se a falta de peer no servidor, ou o perfil que ficou no app depois do rollback das 13:37Z, faz o cliente
desistir (A1 foi sem peer por desenho; no W8 original havia peer); (c) um controle no android-05 (religou 5 de 5) para saber se o
`netlink bind` negado também aparece ali. **W8 = OPEN.** Mudança de código do produto: não recomendada ainda
(`rede_aplicacao.py`, `rede_convergencia.py`, `config.py` intocados).

## 14. Instrumentação melhorada e o controle no android-05 (01/10/2026, ~17:00Z)

**Coletor (`3060cd9`, `test(diag): preserve VPN client evidence`, `simulated` + um ensaio `real` de 14 s só leitura no android-09).**
O A1 mostrou que o filtro perdia as linhas úteis: `am_foreground_service_start/stop` e `sysui_multi_action` estão no buffer
`events`, e o coletor lia o `main`. Agora: `logcat -b all`; o filtro também guarda tudo o que o pid vivo do cliente diz (pid
repassado pelo amostrador, retido 120 s), `ProxyService`/`VpnService`/`TileServices`, `am_foreground_service_*` e `avc: denied`/
`netlink` só quando ligados ao cliente (pacote ou pid); janela larga limitada (`logcat-wide.0/1.txt`, rodízio de ~8 MB, só
durante a coleta); segredos redigidos por formato (`private_key`, `psk`, `password`, `Bearer`); parada limpa pelo arquivo
`parar` na pasta da coleta; `resumo` (clique, início/fim do `ProxyService`, SELinux, primeiro tun0/CONNECTED, retorno do peer).
O `PASS` do F5 não casa mais `startForegroundCount:0`. Rodando o `resumo` nas linhas reais do A1: clique→início 0,333 s, início→
fim 1,796 s, `STOP_FOREGROUND`, `netlink bind` e `somaxconn` negados, sem ANR e sem `Established by`. Nada de root, de arquivo
privado do app nem de `crash_reports`.

**Órfão:** o `adb logcat` pid 30360 (`-s 127.0.0.1:15555 logcat -v threadtime -T 20`, assinatura do laço do coletor, criado às
16:21:07Z, antes de qualquer coleta desta sessão, pai inexistente, dono Administrator, sem filhos) foi encerrado só por esse pid;
o logcat da própria plataforma (pid 24440, `-P 5037`) ficou.

**android-09, releitura às 16:56Z (só leitura):** online, `always_on=null`, `lockdown=0`, sem `tun0`, VPN não conectada, sem
linha `device_network`, sem peer, sem comando aberto, cliente vivo (6512), `stopped=false`; conectividade `healthy` às
16:53:06Z (**depois** do A1). Não foi mais tocado.

**CONTROL-05: `NOT_RUN` — BLOCKED na precondição "online".** O android-05 está `hibernated` ("snapshot salvo") desde
10:05:41Z (comando `hibernate` `succeeded`), sem processo (`emulator-5562` fora do `adb devices`), `readiness not_running`.
Lido do central: QA (`qa-user-05`), sem conta travada, sem comando ou execução aberta, sem controle manual; `device_network`
rev 1 = 1, `exigida_com_bloqueio`, `trafego_verificado`, medição `verified_at` 10:02:14Z, `leak_result=1` (30/09 15:26:50Z,
cliente `1.14.2 (739)`), `leak_pending=0`; peer `10.66.0.2` no servidor com `last_connection=null`. **Não foi possível ler do aparelho:** pids,
`stopped`, always-on, lockdown, tun0, VPN, regras e Q0 (sem processo). Também não rodou nada no 05.

Por que acordá-lo é decisão do dono: (1) o acordar dispara o motivo `ligou` da convergência → `conferir` em
`trafego_verificado`; se o túnel não voltar do snapshot, a rede vai a `configurado` e **pede um reinício** (proibido neste
controle), e a medição de 6 h (`verified_at` 10:02Z) já venceu; (2) a base medida seria a recuperação do próprio produto, não o
estado "known-good" histórico; (3) sem foco ou controle manual o rodízio pode hibernar o 05 de novo no meio do experimento, e o
controle manual conflita com "nenhum operador controlando"; (4) o acionador atual (`diag-w8-tile.py`) só aceita o 09, com a
precondição invertida (sem rede gerenciada): o controle pede um perfil novo, de uma instância só (gate com rede gerenciada,
rollback por tun0/CONNECTED/`last_connection`). O android-02, o outro QA gerenciado, também está hibernado. Os outros aparelhos
com rede gerenciada (01/03/06) têm conta real: excluídos.

## 15. Segundo A1 no android-09, com a instrumentação nova (01/10/2026, 17:11Z, `real`)

Uma única repetição do A1 (sem force-stop, sem peer, sem always-on/lockdown), coletor `3060cd9` em
`data/diag-w8/20261001T171109Z/` (fora do Git; launcher 50668 → python 23568 → `adb logcat -b all` 27124; 40 amostras; janela larga
`logcat-wide.0.txt` com 502 linhas, dentro do teto; SSH do notebook `SSH_UNAVAILABLE`). Acionador `6696931`, gate de 17
precondições ok, baseline igual ao do primeiro A1 (cliente vivo pid 6512, `stopped=false`, tile ausente, uptime 12664 s). Parada
do coletor pelo arquivo `parar`: nenhum processo seu restou, nenhum logcat órfão novo, o da plataforma (pid 24440) segue.

**Gesto:** `Q0=0 P1=2529 P2=2529 Q=1 T=0 CLICOU=1`; `click-tile` exit 0, stdout e stderr vazios, 17 ms. Observação silenciosa de
37,8 s: `tun0` ausente em todas as leituras; cliente e SystemUI com os mesmos pids; `stopped=false`; `ACTIVATE_VPN: allow` antes e
depois; só o `MultiInstanceInvalidationService` do Room como serviço ativo depois. Rollback: nada a desligar, tile removido
(Q0=0); relido às 17:14Z o estado base inteiro, conectividade `healthy` às 17:13:32Z (**depois** do teste).

**Linha do tempo (hora do aparelho −03; mesmo desfecho do primeiro A1):**

| Hora | Evento |
|---|---|
| 14:11:34.575–.698 | add-tile (SystemUI recria o tile); `am_unfreeze` do cliente (6512) |
| 14:11:37.697 | `sysui_multi_action` do `bg.TileService`: o clique |
| 14:11:37.762 | `Background started FGS: Allowed … ProxyService; code:OP_ACTIVATE_VPN` |
| 14:11:37.989 | `am_foreground_service_start` (0,292 s depois do clique); notificação do serviço postada às .015 |
| 14:11:38.041 | `ConnectivityService: requestNetwork` do uid/pid 10196/6512 (`LISTEN_FOR_BEST`) |
| 14:11:38.716 | `cache.db` do cliente escrito (o núcleo começou a inicializar; só o mtime, conteúdo não lido) |
| 14:11:38.820 | `avc: denied { bind } … netlink_route_socket` (pid 6512, bug b/155595000) |
| 14:11:39.107 | `am_foreground_service_stop`, motivo `STOP_FOREGROUND`, 1123 ms de vida; notificação cancelada às .121 |

Nenhuma linha de `Vpn`, `NetworkAgent`, `netd`, `tun0`, `Established by`, ANR, crash, exceção, `am_proc_died` ou kill. O cliente quase
não escreve no logcat: no intervalo só `ServiceConnection: request connect/disconnect` (do add-tile), GC e a negação do SELinux; as
mensagens do núcleo vão para arquivo privado, que não foi lido. O processo 6512 continua vivo e é congelado pelo Android às 14:12:15.

**Motivo do fim do `ProxyService`:** `STOP_REASON_CLASS = A. APP_SELF_STOP` quanto a QUEM (o código do ActivityManager é
`STOP_FOREGROUND`; sem kill, crash, ANR nem morte do processo; leitura do código de motivo pela semântica do AOSP, não verificada
neste aparelho) e `UNKNOWN` quanto ao PORQUÊ: nenhuma mensagem de erro explícita, então E/F/G/H (falha de VPN, de configuração, de rede
ou de permissão) **não** estão provadas. Não escolhi categoria por proximidade temporal.

**Comparação dos dois A1 (a coluna ORIGINAL vem do dump manual `-b all` do primeiro, não do filtro dele):**

| SIGNAL | A1 ORIGINAL (16:39Z) | A1 NOVO (17:11Z) | DIFFERENCE |
|---|---|---|---|
| exit/stdout/stderr do `click-tile` | 0 / vazio / vazio, 13 ms | 0 / vazio / vazio, 17 ms | nenhuma |
| clique → `ProxyService` start | 0,333 s | 0,292 s | ≈ igual |
| vida do `ProxyService` | 1789 ms | 1123 ms | −0,67 s: varia |
| processo do cliente | vivo, 6512 | vivo, 6512 (o mesmo) | nenhuma |
| `stopped` antes/depois | false / false | false / false | nenhuma |
| `tun0` / VPN CONNECTED | nunca / não | nunca / não | nenhuma |
| motivo do stop no AM | `STOP_FOREGROUND` | `STOP_FOREGROUND` | nenhuma |
| `netlink bind` negado | sim, 0,23 s antes do stop | sim, 0,29 s antes do stop | mesma ordem |
| `somaxconn` negado | sim (0,1 s após o start) | não | só na 1ª vez (mesmo processo; a leitura parece única por processo: hipótese) |
| `cache.db` escrito | 0,16 s antes do `netlink` | 0,10 s antes do `netlink` | mesma ordem |
| ANR / crash | não / não | não / não | nenhuma |
| classificador (código novo) | `F5` (dados antigos sem o buffer de eventos: artefato) | `F6_TUN_NOT_CREATED_AFTER_TILE` | a diferença é da coleta, não do aparelho |

A sequência é a mesma nas duas: `requestNetwork` → `cache.db` → negação do `netlink bind` → `STOP_FOREGROUND` ~0,25 s depois.
Isso é **ordem repetida, não causa**: sem controle não se sabe se a negação (que o AOSP marca como esperada para apps) também
ocorre no caminho que funciona. `NETLINK_CAUSALITY = UNKNOWN`.

**Estática 05 × 09 (só o persistido; nada de chave, segredo ou export do app; o 05 está hibernado, então nada foi lido nele):**

| Item | android-05 | android-09 |
|---|---|---|
| cliente | `1.14.2 (739)` (registrado no teste de vazamento de 30/09 15:26:50Z) | `1.14.2`, versionCode 739, `installerPackageName=null`, targetSdk 37, bundle com splits |
| origem/instalação | "já instalado" na 1ª provisão (29/09) | instalado pela loja pelo produto, `firstInstallTime` 30/09 22:36:47Z; `lastUpdateTime` **01/10 13:05:12Z** (mesma versão; autor não identificado nos comandos) |
| imagem | `android-34;google_apis;x86_64` | idem (API 34, userdebug) |
| perfis importados no app | **1**: `plataforma-android-05-r1`, 29/09 23:51:09Z | **4**: `-r1` (30/09 22:36:36Z), `-r1` (01/10 12:47:23Z), `-r1` (13:06:11Z), `-r2` (13:20:12Z); o rollback não apaga perfil |
| endpoint | `10.0.2.2:51820` (alias do host do emulador local), importação por HTTP em `10.0.2.2` | `192.168.1.81:51820` (central pela LAN), importação por HTTP com `adb reverse` |
| endereço VPN | `10.66.0.2/32` | `10.66.0.6/32` |
| política/rev | `exigida_com_bloqueio`, rev 1 = 1, `trafego_verificado` | linha removida em 13:41:48Z (rev 3, `livre`); sem linha hoje |
| par no servidor | `10.66.0.2`, `last_connection=null` (servidor reiniciado 13:43:56Z) | removido no rollback; sem par |
| fingerprint da chave pública do aparelho | `7d03d3dd7106` | `b0787b99ebbc` |
| chave do servidor | `fc98b2413169` (a mesma em uso hoje) | idem |
| tile funcionando | 30/09 15:26:59Z ("túnel religado pelo tile do cliente, sem reinício", com always-on, lockdown 1 e par) | nunca; 4 falhas no W8 e 2 no A1 |

**Diferenças que o estático não resolve:** qual dos 4 perfis está SELECIONADO no cliente do 09 (o id selecionado mora no banco privado
do app) e se o do 05 é o único; se o `lastUpdateTime` das 13:05Z mexeu em algo; e o que acontece no 05 com o mesmo gesto (controle
bloqueado). Candidatas a investigar sem tocar produto: perfil selecionado/estado interno do cliente do 09; a ausência de par; o
endpoint (LAN versus alias do host).

**Caso B** (a falha se repete e o PORQUÊ continua desconhecido): a próxima decisão do dono é **CONTROL-05 versus PEER-09**. Nenhuma
das duas foi feita; A2 não rodou; produto, perfil, cliente e WireGuard intocados; **W8 = OPEN**.

## 16. Qual perfil está selecionado no cliente do android-09 (01/10/2026, 17:19Z, `real`)

**Estado público (só leitura):** `dumpsys package/activity/connectivity/notification` não dizem qual perfil está selecionado
(`PROFILE_FROM_PUBLIC_STATE = UNKNOWN`). O cliente expõe um `WorkingDirectoryProvider` (DocumentsProvider do diretório de trabalho):
**não foi usado**, porque lista os arquivos privados do app (configurações com chave).

**Inspeção da interface (a única mutação: abrir o app; coletor read-only ligado, run `20261001T171935Z`, 29 amostras, `tun0` e VPN
nunca vistos, parada pelo arquivo `parar`, nenhum órfão).** Gate de 17 precondições ok. `am start -n io.nekohasekai.sfa/.compose.MainActivity`
às 17:19:56Z; árvore pelo `GET /api/instances/android-09/hierarchy` (leitura, sem controle) mais uma captura de tela só para
conferir. Nada foi tocado no app (sem Expand, Edit, Update, Start, tile, force-stop). A Dashboard mostra:

- cartão **Profiles** com um seletor suspenso (ícone de abrir/fechar) cujo valor é **`plataforma-android-09-r2`**, tipo **Remote**,
  "**3 hours ago**", botões Edit / Update profile / Share, e o botão **Start** (serviço parado; nenhum cartão de status);
- nenhum aviso, erro ou texto de configuração inválida. Perfis visíveis: **1** (os outros ficam dentro do seletor, não aberto).

`ACTIVE_PROFILE_MATCHES_R2 = YES`, com dois indícios independentes: o nome e a idade. "3 hours ago" bate com a importação do r2
(13:20:12Z → 17:20:02Z = 3 h 59 min); uma importação `-r1` das 13:06Z já diria "4 hours ago". Ressalva: que o valor do seletor
fechado é o perfil selecionado é a leitura normal da interface, sem ter aberto o seletor para conferir os outros três.

Depois: HOME pelo `input keyevent`; foco no launcher, `always_on=null`, `lockdown=0`, sem `tun0`, sem VPN CONNECTED, cliente vivo
(6512, `stopped=false`), `ACTIVATE_VPN: allow`, sem linha de rede, sem par, sem comando aberto, conectividade `healthy` às
17:23:45Z (**depois** da inspeção). **Resíduo:** abrir a UI criou um `ServiceRecord` do `ProxyService` só **vinculado** (sem
`isForeground`, sem start pedido, sem `tun0`), que antes não existia; some quando o Android soltar o vínculo; não foi limpo.

**As quatro importações (todas: endpoint `192.168.1.81:51820`, endereço `10.66.0.6/32`, HTTP de uso único com `adb reverse`; comando
sempre `device.network` ação `aplicar`, motivo `varredura`, pedido pela `rede`):**

| Nome | Importado (UTC) | Rev | Comando | Ciclo de atribuição |
|---|---|---|---|---|
| `-r1` | 30/09 22:36:36 | 1 | `c-20260930223636-13ed84` (instalou o cliente pela loja, 1.14.2) | 1º: sem bloqueio; `desfazer` rev 2 às 22:49 |
| `-r1` | 01/10 12:47:23 | 1 | `c-20261001124723-88b048` | 2º: `conectar`…; `desfazer` rev 2 às 12:56 |
| `-r1` | 01/10 13:06:11 | 1 | `c-20261001130611-ac0740` | 3º: `conectado` 13:07:38, `trafego_verificado` 13:08:11 |
| `-r2` | 01/10 13:20:12 | 2 | `c-20261001132012-58049e` | 3º ciclo, política → `exigida_com_bloqueio` |

Os três `-r1` são **importações legítimas de três ciclos de atribuição diferentes**, não retry nem importação depois de reinício: o
contador de revisão recomeça em 1 quando a linha `device_network` é removida (no `livre`), então o nome se repete; dentro de um
ciclo cada `aplicar` veio da varredura uma vez, e o reinício vem DEPOIS dela (para o always-on valer). Origem conhecida.

**O que isto muda:** o r2 é o selecionado e é o perfil com o qual o boot 2 do W8 (13:25:35Z) chegou a `conectado` (tun0 e VPN CONNECTED).
Nenhum comando do produto troca a seleção desde então; logo "perfil errado/velho selecionado" **não** explica o auto-stop
(`PROFILE_STATE_CAUSALITY`: hipótese do perfil selecionado errado descartada; o CONTEÚDO/estado interno do r2 não foi verificado).
Também enfraquece a hipótese "falta de par": as 4 falhas do tile no W8 foram com a linha de rede ativa e o par no servidor
(inferência: o par só saiu no rollback `livre`; não há evento de par no banco). Fica de pé o que difere entre o boot 2 (funcionou) e as
falhas: o caminho de início (always-on do boot × tile) e o estado do processo do cliente.

### Plano do CONTROL-05 (NÃO executado; nada foi acordado)

1. **Autorização do dono separada para o wake**, aceitando que a reconvergência do próprio produto pode reiniciar a rede ao acordar
   (`ligou` → `conferir`; `reinicios_max=2`) e que o 05 volte, depois, a hibernado ou fique ligado (decidir).
2. **Antes de acordar:** perfil novo do acionador só para o 05 (`--perfil controle-05`; gate com rede gerenciada, rollback por
   tun0/CONNECTED/`last_connection`), com testes e commit; coletor novo ligado para o serial `emulator-5562` e a instância
   `android-05`, mais um monitor só de leitura de `commands`/`events` do 05.
3. **Re-hibernação:** o rodízio só hiberna para ceder vaga (`idle_stop_s=0`); o worker local tem 4 vagas e 3 estão ocupadas (01/03/06),
   então o 05 ocupa a quarta e só sai se alguém pedir um quinto aparelho: nenhum start, tarefa ou foco no parque durante a janela. Não
   usar `control/take` (a varredura só age com o aparelho livre, e conflita com "nenhum operador").
4. **Acordar** pelo gesto do produto (`POST /api/instances/android-05/actions/…`), medir, não intervir; esperar a convergência até
   `trafego_verificado` com `tun0`, VPN CONNECTED, regras de bloqueio, par com `last_connection` recente, conectividade `healthy`, nenhum
   comando aberto por 2 varreduras; só então registrar o baseline (versão, pids, `stopped`, Q0, always-on, lockdown, regras).
5. **Uma única tentativa:** início da janela logo depois de uma conferência (a deriva é 900 s; a sonda de internet 300 s), `force-stop` →
   pid/tun0 ausentes → o MESMO gesto instrumentado → 30 s de observação; sem segundo force-stop, sem repetição.
6. **Se o tile falhar:** sem improviso; o produto recupera por reinício na próxima conferência (≤ 15 min): decisão prévia do dono se
   aceita. Sem mudar perfil, política, par nem servidor; sem conta real (`qa-user-05`); sem IA paga.
7. **Fechamento:** provas do produto convergido de novo (tun0, CONNECTED, `always_on=sfa`, `lockdown=1`, regras, handshake, `healthy`),
   tile no Q0; depois, a tabela 05 × 09 (`resumo`) e o `NETLINK_CAUSALITY` pelo que o 05 mostrar.

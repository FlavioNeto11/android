# W8 — boots 1/3/4 do android-09: o que o boot faz entre o Android e o `tun0` (01/10/2026)

Investigação **separada** do F6 (PR #17, congelado em `fddab25`). Branch `investigate/w8-boot-recovery`, partida de `origin/main` (`52237c4`).
Estado: **`BOOT_ROOT_CAUSE = NARROWED`**, correção **não implementada** (há mais de uma causa plausível para o que sobra). W8 segue **OPEN**.
2ª rodada (offline, zero boot novo): §10 a §16 (assinatura E2, observáveis novos, `exit-info`, `force-stop`/`FLAG_STOPPED` no AOSP, hipóteses H1–H3 e a matriz do próximo experimento).

Níveis de prova: `PROVED` (leitura direta de log/estado persistido do aparelho), `OBSERVED` (visto, uma amostra), `INFERRED` (código do SFA
1.14.2, commit `fc21909df7a3f0fc9435f3866fb6a4960711aa5f`, ou conhecimento do AOSP, não executado), `UNKNOWN`. Horas do convidado em UTC−3
(o relógio do convidado difere do central em 0 s, medido nos três ensaios; o +8,1 s do notebook é só do host: `CLOCK_SYNC_REQUIRED = false`).

## 1. Correção a um achado do PR #17

O §19.5 do handoff `w8-diagnostico-android09.md` (no PR #17) diz que os boots 1/3/4 "não são explicados pelo `serviceMode`" porque o
always-on inicia o `VPNService` direto. **Isso vale só para a 1ª chance.** Há uma 2ª, o `BootReceiver` (`BOOT_COMPLETED`), que chama
`BoxService.start()` → `Settings.serviceClass()` quando `startedByUser=true`: com o `serviceMode` velho (NORMAL) ela inicia o `ProxyService`
(`PROVED`, §3: eventos `FOREGROUND_SERVICE_START ProxyService` nos boots 2 e 4). O `serviceMode` **participa** dos boots do 09, como
um segundo serviço disputando o processo com o `VPNService`; não explica os boots 1 e 3. Aplicar esta nota ao §19.5 depois do merge do PR #17.

## 2. Máquina de estados do boot (SFA 1.14.2 + AOSP 14)

| # | Transição | Pré-condição | Gatilho | Estado esperado | Observável | Modo de falha |
|---|---|---|---|---|---|---|
| T0 | boot do convidado → `ams_ready` → `enable_screen` | AVD sobe a frio | `restart` da plataforma | `system_server` pronto (+18 s de `boot_progress_start`) | `events`: `boot_progress_*` | convidado sem CPU alonga tudo (carga 8–11 no boot normal, 36 sob estresse) |
| T1 | **1ª chance:** `VpnManagerService.onUserUnlocked` → `startAlwaysOnVpn` → `startService(VPNService)` | `always_on_vpn_app=io.nekohasekai.sfa`, `ACTIVATE_VPN` allow | usuário desbloqueado (~+38 s do `logd`; 13 s depois do `enable_screen`) | processo do cliente nasce "for service" | `main`: `Vpn.startAlwaysOnVpn … ContextImpl.startService`; `events`: `am_proc_start … service {…VPNService}`; **uma vez por boot** | start ignorado/descartado (UNKNOWN); pacote em `stopped` não atrapalha (start explícito) |
| T2 | `Application.onCreate` (main) + corrotina IO (`dataStore.initialize` → `HookStatusClient` → `PrivilegeSettingsClient` → **`Libbox.setup`**, sem await) | processo novo | `bindApplication` | libbox configurado em `getExternalFilesDir` | `Choreographer: Skipped N frames`; `PrivilegeSettingsClient … transaction not handled` (marca o fim do trecho lento) | main presa 5–17 s (saudável) e >30 s (estresse) → **ANR "executing service"**; `Libbox.setup` lança por storage FUSE fora (`ENOTCONN`) → **crash** |
| T3 | `VPNService.onCreate/onStartCommand` → `BoxService.onStartCommand` (status Starting; IO: `startedByUser=true`, `startCommandServer()`) | main liberada | fim de T2 | serviço vivo, `startForeground` logo depois | `events`: `am_foreground_service_start` (tipo 1024); `notification_enqueue` | `startCommandServer` lança → `stopAndAlert` **antes** de `startForeground` (sem FGS, sem ANR, sem crash, silencioso) |
| T4 | `startService()`: lê perfil → `DefaultNetworkMonitor.start` → `commandServer.startOrReloadService` → `openTun` → `establish()` | perfil selecionado, rede padrão disponível | continuação de T3 | `tun0` + `registerNetworkAgent … VPN` + CONNECTED | `main`: `ConnectivityService: registerNetworkAgent … VPN CONNECTING`; `ip addr tun0` | `stopAndAlert(EmptyConfiguration|CreateService|StartService|RequestLocationPermission)` ou `serviceStop` do núcleo: `notification.close()` → `am_foreground_service_stop … STOP_FOREGROUND` (cancel `reason=8`), **mensagem só vai aos callbacks da UI, nunca ao logcat** |
| T5 | **2ª chance:** `BOOT_COMPLETED` → `BootReceiver` | pacote **não** `stopped`; `Settings.startedByUser=true`; sem relatório de crash não lido | `BOOT_COMPLETED` (36 s depois do `logd` no boot saudável; 46 s depois do `BOOT_COMPLETED` postado sob estresse) | `BoxService.start()` → `serviceClass()`: `serviceMode==VPN` → `VPNService`, senão **`ProxyService`** | `events`: `am_proc_start … broadcast {…BootReceiver}`; `FOREGROUND_SERVICE_START` da classe | `ProxyService` + perfil com `tun` → `openTun` lança (F6); disputa com T1 no mesmo processo |
| T6 | estado final | — | — | `tun0`, VPN CONNECTED | amostras do `tun0` | sem `tun0` |

## 3. Evidência existente (zero toque, só leitura do que o AVD já persistia)

O AVD do 09 guarda três diários que atravessam os reboots (lidos por `dumpsys` somente leitura, em `data/diag-w8-boot/`, fora do Git):
`dumpsys dropbox` (`SYSTEM_BOOT`, `data_app_anr`, `data_app_crash`), `dumpsys usagestats` (`FOREGROUND_SERVICE_START/STOP` por classe) e
`dumpsys activity exit-info` (descartado: perdeu até o `force-stop` de 13:26Z, sem valor negativo).

| Boot (W8, local) | `SYSTEM_BOOT` | FGS do cliente (usagestats) | ANR/crash do cliente (dropbox) | Resultado |
|---|---|---|---|---|
| B1 | 10:21:30 | **nenhum** (10:20:41 `VPNService` parou no desligamento; próximo evento só no B2) | **nenhum** | sem túnel |
| B2 | 10:25:16 | `VPNService` START 10:25:50, `ProxyService` START 10:25:51 (parou 10:26:03); `VPNService` até o force-stop de 10:26:44 | nenhum | **túnel subiu** |
| B3 | 10:27:58 | **nenhum** (pacote `stopped` desde o force-stop do teste de vazamento) | **nenhum** | sem túnel |
| B4 | 10:31:42 | `ProxyService` START e `VPNService` START no mesmo segundo 10:32:14; `VPNService` STOP 10:32:16, `ProxyService` STOP 10:32:17 | **nenhum** | sem túnel |
| manhã de 01/10 | 09:49:16 | `VPNService` START 09:50:12 + `ProxyService` START 09:50:13 (parou 09:50:27) | nenhum | túnel subiu (mesmo padrão de B2) |
| W4 (30/09) | 19:40:15 | `data_app_anr` 19:40:10 **"executing service …VPNService"**; depois só `ProxyService` 19:42:26–35 | ANR | sem túnel |

Fatos que isto estabelece (`PROVED` para o 09):

1. **B1, B3 e B4 não foram ANR nem crash do cliente.** O diário de ANR/crash tem as entradas de 30/09 19:40, 10/01 19:21 (hoje) e as ANRs de sistema de 09:49 e 10:41,
   e nada em 10:21–10:34. O mecanismo medido no 05 (K-066: ANR de `startForeground`, 3 de 5) **não é** o dos boots 1/3/4 do 09; ele existe (W4 e E3) mas é outro.
2. **B1 e B3: o `VPNService` nunca chegou a primeiro plano.** Sem FGS, sem ANR, sem crash: ou o sistema não iniciou o serviço, ou o serviço parou antes do `startForeground` (T3, `startCommandServer`), em silêncio.
3. **B4: dois serviços do cliente começaram juntos (`ProxyService` do `BootReceiver` com o `serviceMode` velho + `VPNService` do always-on) e ambos pararam em 2–3 s**, sem ANR/crash. No B2 o mesmo par começou com o `VPNService` 1 s antes e o `VPNService` sobreviveu (o `ProxyService` parou em 12 s). A ordem entre os dois é candidata a decidir (INFERRED, ver H-DUPLO).
4. O `startedByUser` ficou `true` entre B1 e B4 (o `ProxyService` do `BootReceiver` só roda assim; `INFERRED` pelo código, coerente com os FGS).

## 4. Ensaios reais no android-09 (3 de 3; `scripts/diag-w8-boot.py`; lockdown 0; nenhum peer/perfil/WireGuard; baseline devolvido em todos)

Cada um: gate, `settings put secure always_on_vpn_app/lockdown`, `restart` normal da plataforma (boot a frio), amostras do `tun0` até 300 s de uptime,
captura dos buffers `events/main/system/crash/kernel` logo depois, desfazer. Evidência em `data/diag-w8-boot/run*/`.

| Ensaio | Variável | Resultado (`real`, 01/10) |
|---|---|---|
| **E1 `os`** (22:13Z) | convidado ocioso, SFA parado pela UI antes (a 2ª chance, o `BootReceiver`, **não foi observada**: sem histórico de broadcasts guardado) | **`tun0` aos 73 s de uptime.** T1 às 19:14:08,76 (`startAlwaysOnVpn`); processo "for service" 09,06; main presa até 29,00 (`Skipped 436 frames`; `PrivilegeSettingsClient` falhou às 28,57, só então o `Libbox.setup` pôde rodar); `am_foreground_service_start` 29,56 (**20,5 s** depois do processo); agente VPN 34,7. Sem BootReceiver start (`startedByUser=false`). Rede padrão: **Wi-Fi primeiro**. Carga do convidado 0,5→11,6. |
| **E3 `os-starved`** (22:20Z) | + 8 laços de CPU no convidado desde o 1º adb (carga até 36) | **sem `tun0` em 300 s.** T1 19:21:11,6; **`am_anr` 19:21:45,5 "executing service …VPNService"** (32 s depois do `bound`), morto "bg anr" 46,9. O `BootReceiver` só rodou às 19:21:52 (BOOT_COMPLETED atrasado) e o processo novo **crashou** às 19:21:57: `go.Universe$proxyerror: open /storage/emulated/0/Android/data/io.nekohasekai.sfa/files/CrashReport-Application.log: transport endpoint is not connected` em `Libbox.setup` (FUSE do storage fora: o `MediaProvider` tinha tomado ANR e sido morto 19:21:52). |
| **E2 `os+stopped`** (22:28Z) | VPN ligada pela UI, `am force-stop`, restart 3,3 s depois (o `stopped` **provavelmente não persistiu**: §10), a tentativa de reproduzir a precondição do boot 3 | **sem `tun0` em 300 s, sem ANR, sem crash, silencioso.** T1 19:29:14,34; **o `BootReceiver` FOI entregue** (histórico de broadcasts: 19:29:19,25 → 22,08; a leitura anterior, "só a 1ª chance", estava errada: §10); `am_foreground_service_start` 22,12; **`am_foreground_service_stop … STOP_FOREGROUND` 23,93** (1,8 s depois; `notification_canceled reason=8` = o próprio app fechou a notificação); nenhum `registerNetworkAgent … VPN`. Convidado com carga normal (pico 9,6). Rede padrão: **celular primeiro** (`MOBILE[HSPA]` 12,9; Wi-Fi só 17,7). |

Leituras:

- **E2 reproduz a assinatura de B3 e B4** (VPNService em primeiro plano 2 s e para, sem ANR/crash, sem `tun0`) **com `serviceMode=VPN`, sem `ProxyService`, sem carga**.
  Logo o `serviceMode`/duplo-start **não é necessário** para essa assinatura (pode somar-se a ela, como no B4).
- **E1 e E2 diferem em mais de uma coisa** que não separei (n=1 cada): o pré-estado (E1 sem Start prévio; E2 com Start + `force-stop`, logo `stopped`, `startedByUser` e morte abrupta), o `BootReceiver` e a ordem das redes padrão no boot (Wi-Fi × celular primeiro, com troca de transporte só no E2: §10). Qual delas, ou nenhuma (variância), é a pergunta aberta.
- **E3 não reproduz B1/B3/B4**: estresse de CPU produz as falhas **do K-066** (ANR em T2/T3, crash do `Libbox.setup` por FUSE), que o diário de dropbox mostra **ausentes** nos boots 1/3/4.
- **Margem de T2/T3 é pequena mesmo no boot saudável**: 20,5 s do processo ao `startForeground` com o convidado ocioso, contra o ANR de "executing service" (20 s no AOSP, `INFERRED`) visto aos 32 s no E3.

## 5. Hipóteses (as do pedido: A–J)

| | Hipótese | Estado | Por quê |
|---|---|---|---|
| A | corrida de inicialização do SFA (`Libbox.setup` assíncrono × `startCommandServer`) | **REMAINING** (`INFERRED`) | corrida real no código; no E1 a ordem foi boa por a main segurar o serviço junto; falha silenciosa dela cairia em T3 (sem FGS = B1/B3) |
| B | perfil ainda não carregado quando o `VPNService` nasce | **ELIMINATED** (`INFERRED`, código) | o cache do `RoomPreferenceDataStore` é `lazy` síncrono: leitura antes do `initialize` bloqueia, não devolve padrão |
| C | libbox não inicializada | **ELIMINATED para B1/B3/B4**, real no K-066 | E3 provou `Libbox.setup` falhando por FUSE → crash, mas isso deixa `data_app_crash`, ausente em 10:21–10:34 |
| D | `tun inbound` indisponível | **UNKNOWN** | a mensagem de `openTun`/`startOrReload` não vai ao logcat |
| E | serviço morre/recria no boot | **REMAINING (núcleo)** | E2, B4: FGS 2 s e `stopForeground`, silencioso |
| F | always-on dispara cedo demais | **REMAINING** | E2: T1 antes do Wi-Fi conectar; E1: depois |
| G | conectividade do guest incompleta no boot | **REMAINING (fraca, n=1)** | ordem celular/Wi-Fi diferiu entre E1 (ok) e E2 (falha) |
| H | estado VPN nominal sem túnel | **ELIMINATED** | nos falhos nem houve agente de rede VPN |
| I | processo sobe por caminho diferente do Start manual | **CONFIRMED como diferença** | boot = `startService` do sistema em processo frio + `BootReceiver`/`serviceClass`; o Start da UI recalcula o modo, o boot não |
| J | outra | **OPEN** | por exemplo o desligamento sujo (`force-stop`) deixando estado do libbox |
| (K-066) | ANR de `startForeground` com convidado sem CPU | **real, mas não é B1/B3/B4** | dropbox sem ANR/crash nesses boots |
| H-DUPLO | `ProxyService` (BootReceiver, modo velho) disputa o processo com o `VPNService` | **REMAINING (B2/B4)** | assinatura coincide em B4; mecanismo da disputa não provado (cache.db? socket do `CommandServer`?) |

## 6. Relógio

O convidado concorda com o central a menos de 1 s (`date +%s` do aparelho × hora do host, 3 medidas). O +8,1 s do notebook é só do host do emulador; não invalida
logs (normalizados pelas horas do convidado), não toca TLS (o túnel sem peer não depende dele) nem o health. **`CLOCK_SYNC_REQUIRED = false`.** Relógio não foi alterado.

## 7. O que NÃO foi feito e por quê

- Sem correção: restam ≥ 3 causas plausíveis (A, E/F/G com J, H-DUPLO); implementar um `sleep`/retry agora seria workaround sem mecanismo provado.
- Não foi possível reproduzir o duplo-start (H-DUPLO): o 09 está com `serviceMode=VPN` desde o Start manual do §18; recriar o NORMAL pede limpar dados (proibido) ou outro aparelho (proibido nesta rodada).
- A mensagem de erro do `stopAndAlert` não é capturável sem UI ligada ao serviço no boot (muda o caminho) ou root (proibido).

## 8. Próximo experimento discriminante (decisão do dono; excede os 3 ensaios desta rodada)

1. **(substituído pela §15)** Par `os+stopped` × controle limpo `os+uistop`, em blocos sorteados, com a ordem das redes lida por boot (já no script: `resumir`) e parada antecipada. O `os` do E1 **não** é o controle: não fez o Start prévio.
2. **(fora da §15)** Fixar a ordem das redes do emulador (Wi-Fi primeiro/celular primeiro) só se houver como **sem mascarar o emulador** (configuração declarada, ADR-056) e com autorização do dono; hoje a ordem é só **observada**.
3. **Boot com `serviceMode` NORMAL** num aparelho de cliente recém-importado (um dos outros do worker, com autorização): reproduz H-DUPLO e mede a ordem dos dois starts.
4. **Mitigação candidata (não implementada, só depois de provar):** a função do PR #17 (`religar_pela_interface`) ligada UMA vez após a importação do perfil deixaria o `serviceMode=VPN`, e o `BootReceiver` passaria a iniciar `VPNService` (um só serviço): removeria o duplo-start dos boots (B2/B4) sem tocar na causa dos B1/B3.

## 9. Artefatos

`scripts/diag-w8-boot-obs.py` (parsers puros; o `resumir` offline) e `scripts/diag-w8-boot.py` (+ `scripts/tests/test_diag_w8_boot.py`, 25 testes): ensaios `os`, `os+receiver`, `os-starved`, `os+stopped`; seguro por padrão, só o android-09, um marcador por ensaio, vocabulário de escrita fechado.
Evidência bruta (fora do Git): `data/diag-w8-boot/run1-os`, `run2-os-stopped`, `run3-os-starved`, `20261001-boot21Z-dump`, `usagestats.txt`, `dropbox-all.txt`. O ensaio `os+receiver` existe no script e **não** foi executado (limite de 3).

## 10. A assinatura E2, formalizada (rodada offline de 01/10; zero boot novo)

**`SILENT_STOP`** (`scripts/diag-w8-boot-obs.py::classificar`): o `VPNService` do cliente entra em primeiro plano e **sai dele até 30 s depois com `STOP_FOREGROUND`** (chamado pelo app), **sem** ANR, crash, `am_kill` nem `am_proc_died` (a morte de processo em cache, `adj ≥ 900` "empty #N", é limpeza e não conta), com o **mesmo PID** durante a observação e **sem `tun0`**.

Reproduzida, em condições controladas, no E2 (`REPRODUCED`, n=1 neste boot; B4 do diário persistido é a ocorrência anterior, só pelo `usagestats`):

| Condição | E2 (`os+stopped`, `real`, 01/10 22:28Z, commit `ffdbc91`) |
|---|---|
| `serviceMode` | **VPN** (o Start da UI do §18 do PR #17 o deixou assim); nenhum `ProxyService` (nem processo, nem FGS, nem em `dumpsys activity services`) |
| carga | normal (loadavg ≤ 9,6, pico no início do boot; sem laço de CPU) |
| pré-estado | VPN ligada pela UI, `am force-stop` (`stopped=true` lido logo depois), restart |
| T1 (sistema) | `startAlwaysOnVpn` 19:29:14,340 |
| **2ª chance** | **`BootReceiver` `DELIVERED`** (`dumpsys activity broadcasts history`, `BOOT_COMPLETED` enfileirado 19:29:13,355): entregue 19:29:19,246 (+5,891 s), terminou **22,076** (+2,830 s), **48 ms antes** do `am_foreground_service_start` |
| processo | `am_proc_start … for service … VPNService` 14,864 (PID **1879**) |
| FGS | `am_foreground_service_start` 22,124 (**+7,26 s** do processo) |
| fim | `notification_canceled reason=8` 23,923; `am_foreground_service_stop … STOP_FOREGROUND` 23,925 (**1,801 s** em primeiro plano) |
| processo depois | **vivo e com o mesmo PID** nas 122 amostras até 300 s; `am_freeze` 19:30:24; único `am_kill` é o de limpeza `empty #24` às 19:45:53 (já no desfazer) |
| o que NÃO há | `am_anr`, `am_crash`, `data_app_anr/crash` no dropbox, entrada nova no `exit-info`, agente de rede VPN, `tun0` |
| serviços aos 302 s | só o `MultiInstanceInvalidationService` do Room; o `VPNService` já não existe com o processo vivo |

Consequências (todas `PROVED` pela captura, exceto onde marcado):

- **D (processo morto pelo framework) está falsificada para o E2**: o PID não mudou, não houve kill nem entrada no `exit-info`.
- **A parada foi do próprio app** (`STOP_FOREGROUND` + notificação cancelada com razão 8 = `stopAndAlert`/`closeNotification` do SFA, `INFERRED` do código). **Por que** o app parou fica `UNKNOWN`: o SFA não grava a mensagem do `stopAndAlert` no logcat.
- **Não foi** ANR, crash, CPU, `ProxyService` nem `serviceMode` (já era VPN). O F6 do PR #17 não a explica.

Fatos novos tirados dos mesmos logs pelos parsers (`resumir`), E1 × E2 × E3:

| | E1 `os` | E2 `os+stopped` | E3 `os-starved` |
|---|---|---|---|
| assinatura | `TUN_OK` | **`SILENT_STOP`** | `ANR_OU_KILL` |
| ordem das redes | Wi-Fi → celular | **celular → Wi-Fi** (4,8 s depois) | Wi-Fi → celular |
| T1 antes da 1ª rede padrão | 2,9 s antes | 0,6 s antes | — |
| T1 → `startForeground` | 20,8 s | 7,8 s | (ANR) |
| **troca de transporte da rede padrão entre o início do processo e o fim do FGS** | **nenhuma** | **celular → Wi-Fi a +5,86 s do processo, 1,4 s ANTES do `startForeground`, 3,2 s antes do fim** | nenhuma |

- O always-on do sistema **não espera rede** (nos dois boots o T1 veio antes da rede padrão; o código AOSP também não verifica rede, §13).
- **A única diferença de rede que coincide com a falha** é uma troca de transporte da rede padrão durante a partida do serviço. n=1: é correlação, **não** causa.
- **E1 × E2 não é um contraste limpo.** Mudam juntos: o pré-estado (E1 sem Start prévio e sem `force-stop`; E2 com Start + `force-stop`), o `stopped`, o `startedByUser` persistido, o processo anterior morto de forma abrupta, a ordem das redes e o `BootReceiver` (**entregue** no E2; no E1 e no E3 não há histórico guardado para dizer).

**Correção ao que eu tinha escrito (2ª rodada, `PROVED`):** eu concluí que, com o pacote `stopped`, o `BootReceiver` não era entregue e o E2 teria só a 1ª chance. O histórico de broadcasts mostra o contrário: **foi entregue e rodou** (`BOOT_COMPLETED` → `BootReceiver` → `Settings.startedByUser` era `true` → `BoxService.start()` → `startForegroundService(serviceClass())`, `INFERRED` do código do SFA), a 2ª partida do mesmo `VPNService` enquanto o 1º `onStartCommand` ainda corria. Dois fatos amarram o resto: (1) o `BootReceiver` só pôde terminar quando a main liberou (`Skipped 331 frames` às 22,047), e o `startForeground` veio 48 ms depois; (2) se ele foi entregue, o pacote **não estava `stopped`** quando o `BOOT_COMPLETED` foi resolvido (§13, F3). A explicação mais simples (`INFERRED`, não observada): o `stopped` **nunca chegou ao disco**: o PMS grava o `package-restrictions.xml` **10 s depois** (`WRITE_SETTINGS_DELAY`, fonte lida) e o restart foi pedido **3,3 s** após o `force-stop` (22:28:33,0 → 22:28:36,3, ver `acionador-boot-os+stopped.jsonl`). **O E2 foi um `force-stop` efêmero, não um boot de pacote `stopped`.** O coletor novo lê o `stopped` no 1º adb e o confirma ou refuta.

## 11. Observáveis novos (`scripts/diag-w8-boot.py` + `scripts/diag-w8-boot-obs.py`; só leitura, sem root, sem o armazenamento privado do SFA)

Cada pergunta A–E da rodada, a fonte e o que existe no android-09 (Android 14, API 34, `userdebug`; leituras testadas ao vivo, sem boot):

| | Pergunta | Fonte (chave da captura) | Disponível? |
|---|---|---|---|
| A | pacote `stopped`? | `dumpsys package` linha `User 0: … stopped= notLaunched= enabled=` (`pacote`; e **3 leituras no boot**: 1º adb, `boot_completed`, fim; o 1º adb vai para a coluna `stopped_1o_adb` do resumo) | **sim**. `package-restrictions.xml` é ilegível ao `shell` (`Permission denied`): só o estado em memória, lido **depois** do reboot, diz se o flag sobreviveu |
| 2ª chance | o `BootReceiver` foi **entregue**? quando? | `dumpsys activity broadcasts history` filtrado no aparelho (`broadcasts`: cabeçalho, `enqueueClockTime`, `DELIVERED`/`SKIPPED` com `scheduled`/`terminal`, nomes, razões; **sem os extras**) → `boot_receiver` no resumo | **sim**: com o processo **vivo** a entrega não deixa `am_proc_start`, então a ausência dele **não prova** nada; só este histórico prova |
| B | ordem e hora das redes, rede padrão, Wi-Fi/celular | `main`: `registerNetworkAgent`, `Switching to new default network` (ms); `wlan0`/`eth0` com IPv4 a cada ~2 s no host (`amostras.jsonl`); `dumpsys connectivity` (`rede`, com `created=` em UTC) | **sim** |
| C | ciclo exato do `VPNService` | `events`: `am_proc_start`, `am_foreground_service_start/stop` (com motivo), `notification_canceled`; `usagestats` `FOREGROUND_SERVICE_START/STOP` por classe (sobrevive ao reboot, atraso ~9 s); `dumpsys activity services` | **sim**, até o limite do que o sistema vê |
| D | processo morto pelo framework | `events`: `am_kill`, `am_proc_died`, `am_anr`, `am_crash`; `exit-info`; `dropbox` (`data_app_anr/crash`, `SYSTEM_BOOT`) | **sim** (§12) |
| E | parada voluntária do app | `STOP_FOREGROUND` do FGS + `notification_canceled reason=8` + **PID inalterado** + nenhuma morte | **sim, por exclusão**: o sistema mostra que foi o app, não por quê |
| — | `onCreate`/`onStartCommand`/`onDestroy`, `startForeground`/`stopForeground` do app | o SFA **não loga** o ciclo; `am_create_service`/`am_destroy_service` não aparecem no `events` capturado | **UNKNOWN** sem instrumentar o app (APK depurável ou instrumentado: decisão do dono, fora de escopo) |
| — | PID e mudanças de PID | `pidof` a cada ~2 s (`P=`), `pids_distintos`, `mudancas_de_pid` | **sim** |
| — | `tun0` e estado VPN | `ip addr show tun0` (`T=`); `dumpsys connectivity \| grep vpn` | **sim** |
| — | relógio convidado × central | `capturar_relogio`: `date` do convidado lido com o host dos dois lados (offset ± metade da ida e volta, fuso) → `relogio.json`; `resumir` converte cada marco para UTC do central | **sim**: medido ao vivo **−0,066 s ± 0,048 s**, fuso `America/Sao_Paulo` |

Saída por boot: `boot-<ensaio>.resumo.json` (assinatura, ordem de rede, ciclo do serviço, PID, trocas da rede padrão, `exit-info`, pacote, marcos nos dois relógios) e, **offline**, `python scripts/diag-w8-boot.py resumir <pasta> [ensaio…]` imprime a tabela comparativa. Nenhum comando novo escreve no aparelho (`test_a_captura_so_le`; `test_a_captura_nao_le_o_armazenamento_privado_do_cliente`).

## 12. `ApplicationExitInfo` (`EXIT_REASON_AVAILABLE = YES`, com limites)

`dumpsys activity exit-info <pacote>` funciona no android-09 **sem root e sem instrumentar o app**, e traz razão (`4` crash, `6` ANR, `10` pedido do usuário/subrazão `21` FORCE STOP, `13` outros/`3` TOO MANY EMPTY PROCS, `16` PACKAGE UPDATED), PID, hora, `importance`, descrição e caminho do trace.

- **Provado no E3**: a captura lista o crash do PID 3421 (`reason=4`) e o ANR do 2176 (`reason=6`, `description=bg anr: executing service …/.bg.VPNService`, com `trace=/data/system/procexitstore/anr_….gz`). Mortes **dentro do boot** capturado aparecem.
- **Limite 1 (AOSP, `AppExitInfoTracker`, android14-release):** só registra morte de processo (`handleNoteProcessDiedLocked`, `handleNoteAppKillLocked`, `handleNoteAppRecoverableCrashLocked`). Um serviço que **sai de primeiro plano com o processo vivo (E2) não gera entrada**: o `exit-info` vazio no E2 é consistente com o PID inalterado, e **só vale como "o framework não matou"**, nunca como "o app não parou".
- **Limite 2:** a persistência é preguiçosa (`APP_EXIT_INFO_PERSIST_INTERVAL` = 30 min; imediata só ao remover usuário/pacote). Uma morte logo antes do reboot **se perde**: o `force-stop` das 19:28 do E2 (`reason=10`) **não** aparece no dump pós-boot (só as 3 entradas antigas). Use o `exit-info` para mortes **dentro** do boot e o `usagestats`/`dropbox` para o que atravessa o reboot.
- Limite por pacote vem de recurso (`mAppExitInfoHistoryListSize`); `am clear-exit-info` existe e é escrita (não usar).

## 13. `force-stop`, `FLAG_STOPPED` e o always-on (fonte AOSP **lida diretamente**, `android14-release`, clone parcial fora do repositório)

`VERIFICADO NO FONTE` = li o método; `OBSERVADO` = log do android-09; `HIPÓTESE` = não provado.

| # | Fato | Fonte | Estado |
|---|---|---|---|
| F1 | `force-stop` marca o pacote `stopped` (`setPackageStoppedState(pkg, true)`) e agenda a gravação em `package-restrictions.xml` (`scheduleWritePackageRestrictions`, **atraso de 10 s**: `WRITE_SETTINGS_DELAY`): **persiste no disco**, mas só sobrevive ao reboot se a gravação ocorreu antes dele | `ActivityManagerService.forceStopPackage` (l. 3842); `PackageManagerService.setPackageStoppedState` (l. 4608) | VERIFICADO NO FONTE; OBSERVADO que `stopped=true` logo após o `force-stop` (E2). **Não observado**: o `stopped` no 1º adb do boot (o coletor novo lê) |
| F2 | **O que limpa o `stopped`**: subir um serviço do pacote (`ActiveServices.bringUpServiceLocked`, comentário "Service is now being launched, its package can't be stopped", **antes** de iniciar o processo), iniciar processo persistente, backup agent, **entregar broadcast** ao pacote, lançar provider | `ActiveServices` l. 5120; `AMS` l. 6955 e 13538; `BroadcastQueueImpl` l. 1445; `BroadcastQueueModernImpl` l. 1931; `ContentProviderHelper` l. 495 | VERIFICADO NO FONTE. OBSERVADO: `stopped=false` quando o `desfazer` leu |
| F3 | Broadcast **não** vai a pacote `stopped`: `broadcastIntentLocked` faz `intent.addFlags(FLAG_EXCLUDE_STOPPED_PACKAGES)` por padrão; o `BOOT_COMPLETED` não usa `FLAG_INCLUDE_STOPPED_PACKAGES` | `AMS` l. 14437; `UserController` (l. 824–845) | VERIFICADO NO FONTE. **OBSERVADO no E2 o oposto do esperado para um pacote `stopped`: o `BootReceiver` foi entregue** (§10). Logo o pacote não estava `stopped` ao ser resolvido o `BOOT_COMPLETED` (flag não persistido, `INFERRED`: restart 3,3 s após o `force-stop`, gravação em 10 s) |
| F4 | O always-on é iniciado **uma vez por boot**: `VpnManagerService.onUserUnlocked` → `Vpn.startAlwaysOnVpn`, que checa `isAlwaysOnPackageSupported` e `getNetworkInfo().isConnected()` (do próprio VPN), dá a lista branca temporária de 60 s e faz `startService(Intent(VpnConfig.SERVICE_INTERFACE).setPackage(pkg))`. **Sem checagem de pacote `stopped`, sem checagem de rede padrão** (um `startService` explícito a pacote `stopped` é permitido: F2). Nova tentativa só por `ACTION_PACKAGE_REPLACED` e mudança da configuração; **não encontrei** retry por mudança de rede nem por saída do serviço | `Vpn.java` l. 1245–1290; `VpnManagerService.java` l. 576–590, 720–775, 925–935 | VERIFICADO NO FONTE (ausência de retry = leitura dirigida, não prova exaustiva) |
| F5 | **`am stop-app`** (API 34) mata processos e serviços (`REASON_USER_REQUESTED`/`SUBREASON_STOP_APP`) **sem** marcar o pacote `stopped` (`stopAppForUserInternal` não chama `setPackageStoppedState`; as únicas chamadas com `true` no AMS são o `forceStopPackage`) e **sem** cancelar alarmes/jobs (texto do `am help` do próprio aparelho) | `AMS.stopAppForUserInternal` (l. 4258–4297); `am help` no android-09 | VERIFICADO NO FONTE + OBSERVADO (help). **O efeito no aparelho não foi testado** (seria escrita) |

O que isto muda na hipótese H1 (§14): o **flag `stopped` em si não chega ao caminho do serviço**, porque o sistema o limpa antes de existir processo (F2). Em E2 ele **nem chegou a existir no boot** (o `BootReceiver` foi entregue; F1/F3). O que o `force-stop` ainda deixa de diferente **para o app** é o resto do pacote de efeitos: a **morte abrupta** do processo anterior (sem fechar libbox/`CommandServer`/Room), alarmes/jobs/notificações cancelados e o `startedByUser` persistido em `true`. Nenhum deles foi isolado.

**Hipóteses específicas do android-09 (`HIPÓTESE`, não provadas):** que a morte abrupta deixe artefato do libbox (socket do `CommandServer`, `cache.db`) que a partida seguinte tropeça; que a troca celular → Wi-Fi durante `DefaultNetworkMonitor.start()`/`openTun` faça o núcleo fechar o serviço. Ambas explicariam um `stopAndAlert` silencioso; nenhuma tem log.

## 14. As hipóteses H1–H3 (pergunta da rodada)

| | Hipótese | Predição | Estado agora |
|---|---|---|---|
| **H1** | o **estado pós-`force-stop`** é causal | falha em todo boot com o pré-estado `force-stop`, em qualquer ordem de rede; nenhuma falha no controle limpo | **ABERTA, reformulada**: o flag `stopped` em si **não foi testado** (no E2 provavelmente nem persistiu, F1) e é **implausível** no caminho do serviço (F2: limpo antes do processo). Resta o **`force-stop` efêmero**: morte abrupta + alarmes/jobs cancelados (H1b). Evidência: 1 boot (E2), confundido com a ordem de rede e com a 2ª partida |
| **H4** | a **2ª partida** (`BootReceiver` → `BoxService.start()` com `startedByUser=true`) sobre a 1ª ainda em curso é causal | falha em todo boot com `startedByUser=true` (R e K), não no controle limpo (C) | **ABERTA, nova, `INFERRED`**: `BootReceiver` entregue e terminando 48 ms antes do `startForeground` no E2; o `onStartCommand` #2 volta cedo (`status != Stopped`), então o mecanismo não é óbvio; só o R × C separa |
| **H2** | a **ordem das redes** (e a troca celular → Wi-Fi na partida) é causal | falha só quando a rede padrão troca de transporte durante a partida, com ou sem `force-stop` | **ABERTA, plausível, n=1**: único boot com troca na janela falhou; E1/E3 sem troca não. Mecanismo é `INFERRED`, sem log |
| **H3** | **ambos só correlacionados**; a causa é o ciclo interno do app (corrida de init, `stopAndAlert` por erro do `openTun`/`startOrReload`) | taxa de falha parecida em todas as células (`force-stop` × controle, celular × Wi-Fi) | **ABERTA, sem teste possível hoje**: sem instrumentar o app não se vê o motivo do `stopForeground` |
| H1∧H2 | só falha com `force-stop` **e** troca de rede (interação) | falha só na célula (K, troca) | **ABERTA**; é o que o E2 mostra, mas o desenho da §15 a distingue |

`BOOT_ROOT_CAUSE` continua **NARROWED**: sabemos **que** o app para o próprio serviço (não o framework), com `serviceMode=VPN`, sem carga, e **o que coincide** (troca de rede, `force-stop`); não sabemos **por quê**. Nenhuma correção cabe: nenhuma hipótese passa do n=1, e a mitigação por sleep/retry/convergência seria marcar sintoma (proibido por regra: sem sleep arbitrário, sem retry infinito).

## 15. Matriz do próximo experimento (PROJETO; **não executado**; decisão do dono; só o android-09)

**Desfecho Y** (automático, `classificar`): `SILENT_STOP` (falha) × `TUN_OK`. Qualquer outro código (`ANR_OU_KILL`, `SERVICO_SEM_FOREGROUND`, `FGS_SEM_TUN`, boot não visto, gate falho) é **outro desfecho**: registrado, não conta como sucesso nem como falha da hipótese, e **não se repete** na mesma rodada.

**Os braços formam uma escada em que cada par vizinho difere em UMA coisa.** Os dois primeiros **já existem** no ator (nenhuma mudança de código para o estágio 1):

| Braço | Preparo | Difere do anterior em | Existe? |
|---|---|---|---|
| **C** `os+uistop` | always-on, UI Start (tun no ar), **UI Stop**, restart | — (`startedByUser=false`: o `BootReceiver` não faz 2ª partida) | **não** (≈10 linhas; só no estágio 2) |
| **R** `os+receiver` | always-on, UI Start (tun no ar), HOME, restart | `startedByUser=true`: o `BootReceiver` é **entregue** e faz a **2ª partida** (§10) | **sim** |
| **K** `os+stopped` (= E2) | R + **`am force-stop` 3 s antes do restart** | o `force-stop`: processo morto abruptamente, alarmes/jobs cancelados; o `stopped` **provavelmente não persiste** (§10), por isso **não** é "pacote `stopped` no boot" | **sim** |

**Estágio 1 (K × R)** testa o `force-stop` com a 2ª partida presente nos dois. **Estágio 2 (R × C)** só roda se R falhar (ES1): testa a 2ª partida sozinha. Um estágio 3 opcional **S** (K + espera ≥ 15 s antes do restart, para o `stopped` ser gravado: o 10 s do `WRITE_SETTINGS_DELAY` do PMS) separaria o flag do resto do `force-stop`; só se H1 sobreviver.

**Covariáveis observadas** (automáticas, `resumir`; **não controláveis** sem mexer na rede do emulador, que é configuração declarada, ADR-056, **fora desta matriz**): ordem das redes e `troca_padrao_durante_o_inicio`; `stopped` no **1º adb** (se o flag sobreviveu ao reboot); `BootReceiver` entregue ou pulado e quando; hora do `startForeground`. Em 3 boots a ordem de rede variou (Wi-Fi, celular, Wi-Fi): há variação natural para amostrar as duas.

**Predições por célula:**

| Hipótese | K | R | C |
|---|---|---|---|
| H1 `force-stop` causal | **falha** | ok | ok |
| H2 troca de rede causal | falha **só com troca** | idem | idem |
| H3 ciclo interno (nenhuma das duas) | independe | independe | independe |
| H4 2ª partida do `BootReceiver` (`startedByUser=true`) | falha | **falha** | ok |
| H1∧H2 interação | só (K, com troca) | ok | ok |

**Desenho:** estágio 1 em **blocos de 2 boots (um K, um R), ordem sorteada antes** (semente gravada, sem espiar), baseline conferido e devolvido entre boots (`desfazer`), um marcador por boot, nada além do restart muda entre boots.

**Boots reais:**

| | Boots | Por quê |
|---|---|---|
| **Mínimo útil** | **4** (2 blocos K×R) | as predições são determinísticas: **um** boot numa célula decisiva falsifica uma hipótese sozinho; 2 blocos dão chance razoável de uma célula decisiva e de pares com a mesma troca de rede |
| **Esperado** | 6 | 3 × 3 com separação perfeita: Fisher exato unilateral p = 1/20 = 0,05 |
| **Teto global** | **8** (estágios 1 e 2 somados) | 4 × 4 com separação perfeita: p = 1/70 ≈ 0,014. **Não 20**: com efeito intermediário o desenho não resolve e o desfecho honesto é `INCONCLUSIVE`, não "mais boots" |

**Regra de parada antecipada** (após cada boot; vale a primeira que disparar):

1. **ES1**: um boot **R** termina em `SILENT_STOP` → o `force-stop` **não é necessário** (H1 morta como causa necessária): **encerrar K × R** e, se sobrar teto, passar ao estágio 2 (R × C) para H4.
2. **ES2**: um boot **K** termina em `TUN_OK` → o `force-stop` **não é suficiente** (o E2 não reproduz nas mesmas condições): H1 forte morta; encerrar K × R.
3. **ES3** (rede): falha **sem** troca de transporte na janela → H2 não é necessária; `TUN_OK` **com** troca → H2 não é suficiente.
4. **ES4 (todos iguais)**: após 4 boots, se **todos** falharam ou **todos** funcionaram, nenhuma variável do desenho explica: **parar** (H3, ou "o E2 não reproduz").
5. **ES5 (anulação)**: gate falho, adb que não volta em 420 s ou `BOOT_NAO_VISTO` anulam o boot e **param** a rodada, sem retry.
6. **ES6 (estágio 2)**: R falha e C funciona → apoia H4; R falha e C falha → H4 não é suficiente: **parar** e propor instrumentar o app (decisão do dono).
7. **Teto**: 8 boots reais, sem exceção; o que sobrar fica `INCONCLUSIVE`.

Custo: ≈ 10 min por boot (≤ 80 min no teto) + análise offline; só o android-09; sem conta real, sem perfil, sem peer, sem WireGuard, lockdown 0. **Antes de qualquer boot:** o dono autorizar o número de boots e o sorteio da ordem (a semente e o registro por bloco ainda não existem no ator); para o estágio 2, acrescentar `os+uistop` com testes.

## 16. Estado após a rodada offline

`BOOT_ROOT_CAUSE = NARROWED`. Leituras ao vivo no android-09 nesta rodada (`real`, **sem boot e sem escrita de estado**): `dumpsys package/activity/connectivity/usagestats/dropbox/broadcasts`, `logcat -d`, `date`, `getprop`, `settings get`; o `dry.py` rodou a captura completa uma vez fora de um ensaio, e uma primeira tentativa de leitura criou e apagou um arquivo temporário em `/data/local/tmp`. Entregue (todos `simulated`/leitura, nenhum boot novo): parsers e resumo automático (`scripts/diag-w8-boot-obs.py`), captura ampliada, modo `resumir` offline, testes (`scripts/tests/test_diag_w8_boot.py`: 25), achados do framework (§13), a matriz (§15). **Não** mexi no PR #17 (só o corpo do PR foi corrigido: o always-on direto não depende do `serviceMode`, mas o `BootReceiver` → `BoxService.start()` → `serviceClass()` usa o `serviceMode` persistido; o E2 prova a falha também com `serviceMode=VPN`), não toquei outros aparelhos, não reiniciei o WireGuard, não implantei.

## 17. Estágio 1 (K × R) autorizado pelo dono (02/10/2026): compromisso ANTES do 1º boot

Autorização do dono: **somente o estágio 1**, no android-09, **no máximo 6 boots reais**, sem estágio 2 (`os+uistop`), sem corrigir nada. Executor: `scripts/diag-w8-boot-estagio1.py` (só orquestra o `diag-w8-boot.py::rodar`, com o mesmo vocabulário de escrita).

- **Semente:** `w8-boot-estagio1-20261002`. Algoritmo: bit 0 de `sha256("<semente>:<bloco>")` igual a 0 → K antes de R; senão R antes de K.
- **Ordem pré-comprometida** (calculada e gravada antes de qualquer boot; não muda em função de resultado):

| Índice | Bloco | Braço | Ensaio |
|---|---|---|---|
| 1 | 1 | K | `os+stopped` |
| 2 | 1 | R | `os+receiver` |
| 3 | 2 | R | `os+receiver` |
| 4 | 2 | K | `os+stopped` |
| 5 | 3 | K | `os+stopped` (só se **nenhuma** parada disparou nos 4 primeiros) |
| 6 | 3 | R | `os+receiver` (idem) |

- **Categorias fechadas por boot:** `TUN_OK`, `SILENT_STOP`, `ANR_OU_CRASH`, `PROCESS_KILLED`, `BOOT_INVALID`, `UNKNOWN`. Mapeamento fixado: `TUN_OK`→`TUN_OK`; `SILENT_STOP`→`SILENT_STOP`; ANR ou crash→`ANR_OU_CRASH`; kill/morte sem ANR/crash→`PROCESS_KILLED`; qualquer outro código (serviço sem foreground, FGS sem tun, sem always-on, indeterminado) e qualquer coisa nova→`UNKNOWN`. Gate falho, `desfazer` que não devolve o baseline, adb que não volta, exceção→`BOOT_INVALID`.
- **Troca de rede relevante:** troca de **transporte** da rede padrão entre o início do processo do serviço e 5 s depois do fim do FGS (ou do seu começo, se não houve fim). A ordem das redes é só **covariável**: nada de ligar/desligar Wi-Fi/celular.
- **Baseline por boot** (`BOOT_INVALID` e parada se falhar, sem "consertar e repetir"): worker conectado e `transport=up`, heartbeat ≤ 45 s, aparelho online e QA, nenhum comando aberto, sem execução ativa, sem `tun0`, sem peer, `always_on` null e `lockdown` 0 antes do preparo. O worker está `degraded` **só pelo relógio do notebook (+8,1 s)**, conhecido nas 3 tentativas anteriores e aceito; qualquer outra degradação invalida. O digest do estado do servidor WireGuard é registrado antes e depois da rodada.
- **Paradas** (aplicadas depois de cada boot): `R` com `SILENT_STOP` (o `force-stop` não é necessário); `K` com `TUN_OK` (não é suficiente); qualquer `SILENT_STOP` **sem** troca de rede (troca como causa necessária falsificada); qualquer `TUN_OK` **com** troca (como causa suficiente falsificada); boot inválido/incerto (`BOOT_INVALID`/`UNKNOWN`); `ANR_OU_CRASH`/`PROCESS_KILLED` (comportamento inesperado: não é a assinatura em estudo); os quatro primeiros boots na mesma classe.
- **`SECOND_START`** só por indício (`events`: `Background started FGS` do próprio app); `NOT_OBSERVED` **não** prova ausência. `STAGE2_RECOMMENDED` sai da análise, e o estágio 2 **não** roda nesta rodada.

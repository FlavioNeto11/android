# W8 — boots 1/3/4 do android-09: o que o boot faz entre o Android e o `tun0` (01/10/2026)

Investigação **separada** do F6 (PR #17, congelado em `fddab25`). Branch `investigate/w8-boot-recovery`, partida de `origin/main` (`52237c4`).
Estado: **`BOOT_ROOT_CAUSE = NARROWED`**, correção **não implementada** (há mais de uma causa plausível para o que sobra). W8 segue **OPEN**.

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
| **E1 `os`** (22:13Z) | só a 1ª chance, convidado ocioso, SFA parado pela UI antes | **`tun0` aos 73 s de uptime.** T1 às 19:14:08,76 (`startAlwaysOnVpn`); processo "for service" 09,06; main presa até 29,00 (`Skipped 436 frames`; `PrivilegeSettingsClient` falhou às 28,57, só então o `Libbox.setup` pôde rodar); `am_foreground_service_start` 29,56 (**20,5 s** depois do processo); agente VPN 34,7. Sem BootReceiver start (`startedByUser=false`). Rede padrão: **Wi-Fi primeiro**. Carga do convidado 0,5→11,6. |
| **E3 `os-starved`** (22:20Z) | + 8 laços de CPU no convidado desde o 1º adb (carga até 36) | **sem `tun0` em 300 s.** T1 19:21:11,6; **`am_anr` 19:21:45,5 "executing service …VPNService"** (32 s depois do `bound`), morto "bg anr" 46,9. O `BootReceiver` só rodou às 19:21:52 (BOOT_COMPLETED atrasado) e o processo novo **crashou** às 19:21:57: `go.Universe$proxyerror: open /storage/emulated/0/Android/data/io.nekohasekai.sfa/files/CrashReport-Application.log: transport endpoint is not connected` em `Libbox.setup` (FUSE do storage fora: o `MediaProvider` tinha tomado ANR e sido morto 19:21:52). |
| **E2 `os+stopped`** (22:28Z) | VPN ligada pela UI, `am force-stop` (pacote `stopped`), restart: a precondição do boot 3 | **sem `tun0` em 300 s, sem ANR, sem crash, silencioso.** T1 19:29:14,34 (só a 1ª chance: BootReceiver não entregue ao pacote `stopped`); `am_foreground_service_start` 22,12; **`am_foreground_service_stop … STOP_FOREGROUND` 23,93** (1,8 s depois; `notification_canceled reason=8` = o próprio app fechou a notificação); nenhum `registerNetworkAgent … VPN`. Convidado com carga normal (pico 9,6). Rede padrão: **celular primeiro** (`MOBILE[HSPA]` 12,9; Wi-Fi só 17,7). |

Leituras:

- **E2 reproduz a assinatura de B3 e B4** (VPNService em primeiro plano 2 s e para, sem ANR/crash, sem `tun0`) **com `serviceMode=VPN`, sem `ProxyService`, sem carga**.
  Logo o `serviceMode`/duplo-start **não é necessário** para essa assinatura (pode somar-se a ela, como no B4).
- **E1 e E2 diferem em duas coisas** que não separei (n=1 cada): o desligamento anterior (UI Stop limpa × `force-stop` com a VPN no ar) e a ordem das redes padrão no boot (Wi-Fi × celular primeiro). Qual delas, ou nenhuma (variância), é a pergunta aberta.
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

1. **Par `os` × `os+stopped` repetido** (≥ 3 de cada, mesmo `diag-w8-boot.py`): separa "desligamento sujo" de "ordem das redes/variância". Acrescentar ao script a leitura da ordem das redes (`registerNetworkAgent`) por boot para correlacionar com o resultado.
2. **Mesmo `os+stopped` com a rede do emulador fixada em Wi-Fi primeiro/celular primeiro** (se houver como, sem mascarar o emulador: é configuração de rede declarada, ADR-056), para isolar G.
3. **Boot com `serviceMode` NORMAL** num aparelho de cliente recém-importado (um dos outros do worker, com autorização): reproduz H-DUPLO e mede a ordem dos dois starts.
4. **Mitigação candidata (não implementada, só depois de provar):** a função do PR #17 (`religar_pela_interface`) ligada UMA vez após a importação do perfil deixaria o `serviceMode=VPN`, e o `BootReceiver` passaria a iniciar `VPNService` (um só serviço): removeria o duplo-start dos boots (B2/B4) sem tocar na causa dos B1/B3.

## 9. Artefatos

`scripts/diag-w8-boot.py` (+ `scripts/tests/test_diag_w8_boot.py`): ensaios `os`, `os+receiver`, `os-starved`, `os+stopped`; seguro por padrão, só o android-09, um marcador por ensaio, vocabulário de escrita fechado.
Evidência bruta (fora do Git): `data/diag-w8-boot/run1-os`, `run2-os-stopped`, `run3-os-starved`, `20261001-boot21Z-dump`, `usagestats.txt`, `dropbox-all.txt`. O ensaio `os+receiver` existe no script e **não** foi executado (limite de 3).

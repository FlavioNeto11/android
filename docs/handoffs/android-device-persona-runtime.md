# Runtime Handoff — aparelho × persona × app × sessão (android-06)

Fase cloud concluída em 25/09/2026 (prova `simulated`). Fase local no mesmo dia, no central `WIN-7S2UASNLFOP`:
ver **Resultado runtime** logo abaixo — o que está lá é `runtime_verified` (real) com ids de comando.

## Resultado runtime (25/09/2026, central, produção em `9acba15`)

Implantado por `scripts/deploy.ps1` a partir do checkout principal destacado na branch (`/api/health`: commit
`9acba15`, migração `039`, `problems` vazio). Login: uma única tentativa, autorizada e clicada pelo dono (linha "Login").

| Tema | Resultado | Prova |
|---|---|---|
| android-06 | local (`WIN-7S2UASNLFOP`), `emulator-5564`, Android 14 | `runtime_verified` |
| Instagram | `com.instagram.android` `447.0.0.55.81` / `385311929` = promovida; ADB = backend = contexto | `runtime_verified` |
| Gate app ausente | pós-wipe: `absent/missing`, Conectar/Verificar conta `aria-disabled` com o motivo, `POST connect` → `409 app_not_installed`; `app.verify` `c-20260925211809-09ad46` leu `absent` | `runtime_verified` |
| Instalação | menu "Instalar Instagram 447.0.0.55.81 (promovida)"; `c-20260925212509-ba6771`: `installing → verifying → ready`, `succeeded` só após `ready`; ADB confere | `runtime_verified` |
| Sessão | app `ready` + senha guardada → fase `unknown`, Conectar liberado, sessão `unknown` (nunca `session_ready`) | `runtime_verified` |
| **Rede — causa imediata** | android-06 com `AndroidWifi` `PERMANENTLY_DISABLED` (`DISABLED_BY_WIFI_MANAGER`, nunca conectou), provável sequela do reset que morreu em "Boot excedeu 480s" (24/09 23:25, `c-20260924232557-a9498e`) | `runtime_verified` (origem: hipótese) |
| **Rede — causa sistêmica** | DHCP do roteador entrega `1.178.36.77` (morto) antes de `8.8.8.8`; o slirp do emulador 37.1.11 usa só o 1º DNS IPv4 do host (`IPv4 server found: 1.178.36.77` em todo boot) → `10.0.2.3` morto em TODOS os AVDs; os outros escapavam pelo Wi-Fi (netsim, fallback IPv6). Os `fec0::ffff:1..3` NÃO entram (hipótese refutada) | `runtime_verified` |
| Correção da rede | `android.dns_servers: ["192.168.1.1","8.8.8.8"]` (ambos testados com `Resolve-DnsName` no host) → `-dns-server 192.168.1.1,8.8.8.8` na linha de comando; reset `c-20260925211034-8499ed` (`-wipe-data`) → boot → `connectivity: healthy` **sem intervenção**; MOBILE passou a `VALIDATED`; resolver 332 ok / 0 falhas (antes: centenas de `-110`); `AndroidWifi` voltou habilitada e conectada | `runtime_verified` |
| Conectividade (A) | `connectivity` separada de `online`: `unknown` a cada entrada no ar, sonda pós-boot/wake e a cada 5 min, `409 device_no_internet` no Conectar; 01/04/06 foram a `healthy` sozinhos ~40 s após a implantação | `runtime_verified` |
| Stream | `stale`/`capture_error` só com o convidado sob carga (primeiro boot, load 26 em 2 vCPU) ou executor ocupado (`install_apk`): `screencap excedeu 25s`; aparelho seguiu `online`, falhas contadas, voltou a `live` com 0 falhas | `runtime_verified` |
| Hibernar/acordar local | android-06: `c-20260925204553-323d08` / `c-20260925204612-809592` `succeeded`; internet re-sondada 8 s após acordar | `runtime_verified` |
| Worker remoto | android-09 via `worker-lan-01`: `start` `c-20260925204322-b1693a` e `stop` `c-20260925204544-6b4fec` `succeeded` pelo worker; `home` saiu do central pelo túnel (`succeeded`); internet `healthy` pela sonda no túnel. Hibernar recusado: `android.hibernation: false` no `worker.yaml` (padrão) — botão não aparece | `runtime_verified` |
| Botão truncado | menu cortado pela borda e depois pelo `overflow` da coluna do Foco → popover `position: fixed` presa à tela (`cc58ab0`); medido: inteiro e clicável em viewport 1024 | `runtime_verified` |
| Login | UMA tentativa autorizada, Conectar clicado pelo dono às 21:55:48 (`c-20260925215548-06e323`, tentativa 19). Rede `healthy` antes/durante; logcat filtrado da janela: 54 resoluções DNS ok, 0 falhas, nenhum erro de SSL. Sem "Unable to log in": o app entrou e mostrou "Save your login info? … «conta do android-06»" (~21:56:50). A tentativa fechou `uncertain`/`classified` ("nenhum sinal conhecido") porque o prazo pós-envio (25 s) acabou com a `InstagramMainActivity` ainda carregando; o dono tocou Save; a reobservação automática leu `@«conta do android-06»` às 21:57:37 → `session_ready`/`authenticated` (≈109 s do clique). Pós-condição = @ lido na tela, não o clique. Causa provável do erro antigo: aparelho sem DNS | `runtime_verified` |
| Prazo pós-envio | `submit_wait_s` 25 → 45 s (`9acba15`); só observa, nunca reenvia | `simulated` (sem nova tentativa) |
| Scheduler × internet | o despacho não olhava `connectivity`: tarefa de Instagram podia ir para aparelho `online` sem internet. `AppCapabilities.requires_internet` (Instagram sim; app sem registro/local não) + porta em `_portas_do_app` entre "app pronto" e "sessão": espera (`device_slot`) com `unknown/degraded/unavailable` (`7d76346`) | `simulated` (`test_aparelho_persona_sessao.py::test_despacho_exige_internet_so_do_app_que_precisa`); nenhuma tarefa de IA real rodada (chamada paga não autorizada) |
| Boot remoto (android-09) | `_adopt_external` sondava a saúde assim que o adb dizia `device`, antes do `boot_completed` → `error` "system_server caiu" num boot normal. Agora `booting` até o boot concluir; passado `boot_timeout_s`, sonda e degrada como antes (`7d76346`, teste corrigido em `ef92dbd`). Runtime em `9acba15`: start `c-20260925220606-3656eb` pelo `worker-lan-01` → `stopped` (adb offline) → 22:06:35–47 adb `device` com `service check` `not found` → 22:06:59 `booting` "Android ainda subindo" → 22:07:07 `boot_completed=1` → 22:07:19 comando `succeeded` → 22:07:28 `online` → 22:08:00 internet `healthy` e sonda de saúde pós-boot ok (aviso de pressão, load 26→11); nenhum `error`. Stop `c-20260925220919-6bd0ba` `succeeded`. A janela `not found` não coincidiu com uma adoção nesta corrida | `runtime_verified` |
| PostgreSQL | não rodado: o ambiente oficial (`docs/banco.md`, container `farm-pg` na 55433) não está no ar; o serviço `postgresql-x64-17` da máquina não é o de teste e não foi tocado. Nenhuma migração nova nesta branch | `not_run` |

Achados em aberto: (1) no boot remoto a sonda de saúde marcou `error` ("system_server caiu") antes de o convidado
subir, e se corrigiu sozinha; (2) a primeira sonda de internet pós-reset esperou ~3 min pela fila vazia (seguro: o
estado fica `unknown`, não `healthy`); (3) dívida operacional do `worker-lan-01` (sem alterar sem autorização): agente defasado (`c0c982d`),
`android.hibernation: false`, `dns_servers` não configurado.

## Base

- Branch: `claude/awesome-lamport-s602ai`
- Base: `b467279` (main)
- Commits desta sessão: ver `git log b467279..claude/awesome-lamport-s602ai --oneline`

## O que mudou

1. **Portão único de sessão** (`social/sessao_gate.py`): vínculo → app observado → senha → sessão. O perfil ganha
   `app_on_device` e `session_actions` (fase + `connect`/`verify`/`logout`/`inspect_app` com motivo). A rota recusa
   pela mesma regra: `409 app_not_installed` / `app_not_verified` / `app_busy` / `session_busy`.
2. App **nunca inspecionado** (`device_app_state` sem linha) é `app_unknown`, não "instalado" nem "ausente";
   a saída é o botão "Verificar app no aparelho" (`POST /instances/{id}/app/verify`, só leitura).
3. "Verificar conta" deixou de exigir senha (ela só observa). "Conectar" continua exigindo.
4. **Instalar**: botão "Instalar app" abre um menu com app e versão ("Instalar Instagram X (promovida)"), independente
   de "Abrir app". O backend resolve e recusa **antes** do 202 quando não há versão promovida, e devolve
   `install_target` (app, pacote, release, versão, `mechanism: release_catalog_adb`). O ciclo
   `installing → verifying → ready` com releitura do aparelho já existia (`releases/service.py::install_on`).
5. **Stream** (`devices/stream.py`): `InstanceDTO.stream` com `live | stale | capture_error | no_frame |
   device_offline | device_hibernated | worker_offline`, idade do frame, falhas seguidas e último erro. Falha de
   captura agora é contada e publicada (antes só `log.debug`); o laço de captura recua (até 30 s) e zera no frame novo.
6. O selo "Desatualizado" diz qual caso é (Foco e cartão).
7. **Login**: o diálogo "Unable to log in / An unexpected error occurred" vira desfecho `uncertain` nomeado
   (`login_error_dialog: …`, etapa `login_error_dialog` em `authentication_attempts`), nunca "senha errada" nem
   sucesso. `session_ready` continua exigindo o @ lido na tela.
8. **Contexto operacional**: `GET /api/instances/{id}/operational-context` e
   `GET /api/instagram/profiles/{id}/operational-context` (servidor, aparelho, tela, apps com versão instalada ×
   promovida, perfil, sessão, fase). Cartão no Foco e na aba Autenticação do perfil.
9. `AppDTO` traz `promoted_*`; promover ou quarentenar republica `apps.updated`.
10. Roteamento/capacidades: **sem mudança**. Já é um contrato só (outbox → transporte → `_do_action`, com o central
    como `LocalWorker`); `hibernate`/`wake` só aparecem onde o worker declara snapshot (`verbs.py::sem_hibernacao`).
    Verbos de ADB (install/open/teclas/captura) saem do central pelo túnel, também para aparelho remoto.

## Arquivos principais

- `backend/app/social/sessao_gate.py`, `backend/app/devices/stream.py`, `backend/app/contexto.py` (novos)
- `backend/app/api.py` (`_recusa_pelo_portao`, `install_target`, rotas de contexto), `backend/app/models.py`,
  `backend/app/social/repository.py`, `backend/app/devices/manager.py` (captura), `backend/app/integrations/instagram/{navigation,reconciliation,authentication}.py`
- `frontend/src/features/devices/{InstallAppMenu,OperationalContextCard}.tsx`, `streamState.ts`,
  `frontend/src/features/profiles/sessionGate.ts`, `ProfilesPage.tsx`, `ProfileDetail.tsx`, `focus/FocusPanel.tsx`, `focus/Screen.tsx`
- Testes: `backend/tests/test_aparelho_persona_sessao.py`, `frontend/src/features/devices/aparelhoPersona.test.ts`

## O que já foi provado por testes (simulated)

- `pytest -q tests/test_aparelho_persona_sessao.py` → 13 passed (portões, stream, recuo, erro de captura no DTO,
  diálogo de login, 409 por HTTP, contexto sem segredo).
- Suíte do backend inteira em Linux: ver a seção de resultado no PR (falhas preexistentes de ambiente: caminho
  `C:\Android\Sdk` e socket IPv6, iguais na base `b467279`).
- `npm run typecheck` e `npx vitest run` → verdes (ver PR).

## O que NÃO pôde ser validado na Cloud

android-06 real; inspeção de pacote por ADB (`installer.inspect`); worker local e remoto; captura/stream real e o
recuo sob falha real; hibernar/acordar em worker remoto; instalação real do Instagram; login real e o diálogo de
erro; logcat; rede/DNS/relógio do aparelho.

## Checklist LOCAL

Na máquina central, depois do deploy autorizado (`scripts/deploy.ps1`; implantar exige autorização em chat):

1. `curl -s http://127.0.0.1:8000/api/health` → `commit` = SHA desta branch, `problems` vazio.
2. Contexto do aparelho: `curl -s http://127.0.0.1:8000/api/instances/android-06/operational-context` (com o cookie
   de sessão do painel). Anotar `server.connected`, `device.state`, `stream.status`, a linha do Instagram em `apps`
   e `profiles[0].session_actions.phase`.
3. Se o Instagram vier `presence: unknown`: no perfil do André, clicar **Verificar app no aparelho** → a linha passa a
   `installed` com `installed_version_name`, ou `absent`.
4. Com app ausente: Conectar e Verificar conta aparecem **desabilitados com motivo**; `POST .../verify` → 409
   `app_not_installed`.
5. Foco do android-06 → **Instalar app** → o menu mostra "Instalar Instagram <versão> (promovida)"; clicar; o toast
   repete app e versão; o comando só fecha `succeeded` depois da releitura; `apps[].state` = `ready`.
6. Stream: com o android-06 online e em foco, `stream.status` = `live`. Para `stale`: pausar a captura sem derrubar o
   aparelho (ex.: ocupar o executor com uma instalação longa) e conferir `stale`, não `device_offline`. Desconectar o
   worker remoto de um aparelho remoto → `worker_offline`.
7. Hibernar/acordar num aparelho do `worker-lan-01`: conferir se o botão aparece (só com `android.hibernation` no
   worker) e o desfecho do comando no histórico (`GET /api/commands?instance_id=…`).
8. Login (só com autorização e pela pessoa — a senha é dela): após **Conectar**, ler
   `GET /api/instagram/profiles/<id>/auth-attempts`. Se o diálogo aparecer, a tentativa terá `stage =
   login_error_dialog`. Em paralelo, no aparelho: `adb -s <serial> shell dumpsys package com.instagram.android | grep
   versionName`, `adb -s <serial> shell date`, `adb -s <serial> logcat -d -t 500 | grep -i -E "instagram|ssl|network"`
   (não colar tokens/cookies do logcat em lugar nenhum).

## Hipóteses restantes (exigem runtime)

- "Desatualizado" frequente no android-06: captura remota pelo túnel lenta (executor ocupado) × falha de `screencap`
  × canal de eventos do painel parado. O novo `stream.status` separa as três.
- "Unable to log in": relógio/fuso do aparelho, rede/DNS do emulador, APK sem Google Play Services ou versão rejeitada
  pelo servidor, ou bloqueio do lado do Instagram. Nenhuma foi verificada.
- Hibernar/acordar remoto: snapshot desligado no worker (`android.hibernation`) ou agente defasado (`agent_outdated`).

## Critérios de sucesso local

| Passo | Esperado |
|---|---|
| 2 | todas as camadas presentes; nada de senha/identificador de login no JSON |
| 3 | `presence` sai de `unknown` para `installed`/`absent` com `verified_at` novo |
| 4 | botões desabilitados com a frase do backend; 409 com código |
| 5 | menu com versão antes do clique; `succeeded` só depois de `ready` |
| 6 | `stale` ≠ `device_offline`; `worker_offline` quando o worker cai; volta a `live` sozinho |
| 7 | botão só quando suportado; desfecho `succeeded`/`failed`/`uncertain` explícito |
| 8 | tentativa registrada com etapa; sessão fica `unknown`/`auth_required`, nunca `session_ready` sem o @ na tela |

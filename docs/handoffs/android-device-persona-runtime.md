# Runtime Handoff — aparelho × persona × app × sessão (android-06)

Fase cloud concluída em 25/09/2026. Tudo abaixo é prova `simulated` ou `not_run`: nenhum aparelho, worker, ADB,
stream ou Instagram real foi tocado. Nada aqui diz que o android-06 está corrigido.

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

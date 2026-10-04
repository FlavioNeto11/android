# Operação — runbook

> Fonte principal para instalar, configurar, desenvolver, testar, implantar e diagnosticar. Para arquitetura,
> [docs/arquitetura.md](arquitetura.md); para banco/worker, [docs/banco.md](banco.md) e [docs/worker.md](worker.md);
> para custo de IA, [docs/ia.md](ia.md). As notas datadas usadas aqui vêm de registros de sessões anteriores
> (memória do operador) — tratadas como evidência datada, não como instrução; nomes de conta e do operador foram
> omitidos de propósito.

> **Ambiente central, não produção (decisão do dono, 28/09/2026).** A máquina central (`C:\git\android`, porta
> 8000, tarefa `farm-central`), o parque e o notebook da LAN são o ambiente de **desenvolvimento e validação** do
> dono; ainda não há produção de verdade. Registros datados anteriores dizem "produção" para esse mesmo ambiente.
> Implantar, reiniciar e ligar, desligar, criar ou aposentar aparelhos ali são ações de validação; conta real de
> terceiros, IA paga além do pontual e infraestrutura do host seguem pedindo autorização (`CLAUDE.md`).

## 1. Instalação

`scripts/install-prereqs.ps1` — Android SDK (cmdline-tools, platform-tools, emulator, imagem) + Appium/driver
UiAutomator2, idempotente, aceita as licenças do SDK em nome de quem roda. Versões usadas e conferidas em
`docs/relatorio-validacao.md §1`.

**Divergência de versão do Node — três fontes, três números:**

| Fonte | Valor |
|---|---|
| `README.md` (pré-requisito documentado) | 24.x |
| `.github/workflows/ci.yml` (`NODE_VERSION`) | 22.12.0 |
| `frontend/package.json` (`engines.node`) | `>=22.12.0` |

A fonte de verdade é `frontend/package.json` (é o que o `npm ci` de fato confere) — CI usa exatamente esse piso;
o README fica desatualizado (corrigido em 24/09 para `>=22.12.0`).

## 2. Configuração

- **`config/config.yaml`** — por instalação, **fora do Git** desde 23/09/2026. `config/config.example.yaml` é o
  retrato neutro (4 emuladores locais, sem remoto, sem loja). `start.ps1` copia o exemplo na primeira partida e
  nunca por cima do que já existe (`scripts/start.ps1:74-75`, função `Semear`).
- **Guarda contra checkout que apaga o config.** `git checkout`/fast-forward entre um commit onde `config.yaml`
  ainda era rastreado e um onde não é **apaga o arquivo da árvore** — aconteceu em produção em 23/09 (o backend
  subiu com `worker_port: 0` e zero aparelhos remotos, sem nada reclamar: `/api/health` confere commit+migração,
  não o que foi lido). `start.ps1` agora **recusa subir** quando existe `data/poc.sqlite3` mas falta
  `config/`/`.env` (`scripts/start.ps1:37-50`), e a recomendação é restaurar de `data/backups/<carimbo>/config/`.
- **`.env`** (copiar de `.env.example`) — chave do provedor, modelo por função, `DATABASE_URL`, `OWNER_ID`,
  `ROLE`, cofre, transporte, storage, `API_TOKEN`. Nunca vai para o frontend, banco ou log.

## 3. Desenvolvimento

```powershell
pwsh -File scripts\start.ps1                  # backend + Appium + painel em 127.0.0.1:8000/central/
pwsh -File scripts\start.ps1 -Dev              # + Vite com hot reload em 127.0.0.1:5173 (proxy para o backend)
pwsh -File scripts\start.ps1 -Simulated        # MODO SIMULADO (sem IA; regras fixas para o app de QA)
```

`-Dev` sobe o Vite separado (proxy de `/api` e `/ws` para o backend); sem `-Dev` o frontend precisa estar
buildado (`npm run build`) para `start.ps1` servir `frontend/dist/index.html`
(`scripts/start.ps1:76`). `-Simulated` força `AI_PROVIDER=simulated`, com aviso no console.

## 4. Testes

```powershell
cd backend; .venv\Scripts\python.exe -m pytest -q          # SQLite (padrão), em série
cd backend; .venv\Scripts\python.exe -m pytest -q -n 6     # a mesma suíte em 6 processos (pytest-xdist), ~10 min; padrão desde 04/10 (29.67)
# com TEST_DATABASE_URL=postgresql://...  , a mesma suíte roda contra PostgreSQL, ~14 min
cd frontend; npm run typecheck && npm test
cd backend; .venv\Scripts\python.exe -m pytest -q ..\scripts\tests   # lógica pura dos scripts, sem tocar o parque
python scripts\testes-afetados.py --run --ocioso    # só os testes que a mudança atinge (segundos, não ~35 min)
```

`testes-afetados.py` (30/09) lê o diff contra `origin/main` mais a árvore de trabalho, segue os imports do backend
sem atravessar os módulos-hub (`main`, `state`, `api`, `config`...) e junta os testes do mesmo assunto pelo nome; os
guardas `test_arquitetura.py` e `test_pacote_do_agente.py` entram sempre. Avisa "AMPLO" quando a mudança atinge
metade ou mais dos testes (aí vale a suíte inteira, uma vez, em segundo plano). Não vê importação dinâmica nem dado
lido em tempo de execução: é o laço rápido do trabalho, não a prova de entrega de um contrato compartilhado ou de
migração.

Regras de ritmo (pedidas explicitamente pelo dono; ver ADR-021 em `docs/decisoes.md` e `CLAUDE.md` §
Convenções): durante o trabalho, rodar só o arquivo ou o `-k` afetado; a suíte inteira fica **só para antes do
commit**, e roda **em segundo plano** — nunca ficar ocioso esperando. O harness de teste isola-se do parque real
por porta: `backend/tests/conftest.py:48` fixa `base_console_port: 5640` (o padrão de produção é 5554,
`config.py:184`), então a suíte nunca endereça um emulador real do parque, mesmo rodando na mesma máquina.

**Suíte em paralelo (J-XDIST; `-n 6` desde 04/10).** Com `-n 8` o convidado de um aparelho de conta real ficou sem CPU durante a SQLite (29.67); em `-n 6` a suíte leva ~10 min. As medições abaixo são as de `-n 8`, de 02/10. O `pytest-xdist` está nas dependências de dev (`backend/requirements-dev.in`). Medido em 02/10 na máquina central (22 núcleos lógicos), serial em `25624c4` (prova real da sessão Android, 16:40–17:16Z) e paralelas em `7a1d0b0` (o mesmo código de teste): em série ~36 min (2178 s); com `-n 8`, 5:06 e 5:47 em duas execuções seguidas, as três com o mesmo resultado (4752 passed e 9 skipped, sem falha, nas duas paralelas; a referência em série deu 4754 passed e 7 skipped porque rodou num worktree com a junção `backend/.venv` — os 2 testes a mais de `test_supervisao_do_central.py` exigem o venv NA árvore e pulam também em série sem ela). `-n 12` não ganha tempo e já expôs um teste de tempo frágil (`test_worker_executor.py::test_guarda_de_ram_e_reavaliada_depois_da_espera_na_fila`, que agora espera pelo fato em vez de `sleep(0.05)`). O isolamento entre processos vem do próprio harness: `tmp_path` e SQLite por teste, portas de console falsas a partir de 5640. No PostgreSQL, cada teste cria um schema `t<uuid>` e cada sessão apaga só os schemas que ela criou (`conftest.py::_SCHEMAS_DE_TESTE`); como cada worker do xdist é uma sessão, o desenho vale também em paralelo, mas a corrida em PostgreSQL com `-n` está `not_run`. Continua a regra de uma suíte completa por vez na máquina, mesmo entre sessões: antes de disparar, confira se já há um `python -m pytest` rodando.

**PostgreSQL de teste.** O contêiner `farm-pg` (PostgreSQL 17 na porta 55433; receita em
[banco.md](banco.md#rodar-a-suíte-contra-o-postgresql)) é o banco das corridas com `TEST_DATABASE_URL`; em 29/09 a
integração `c359f65` rodou nele. O PG dirigido das suítes do funil roda desde 04/10 no `farm-pg-rapido` (tmpfs,
`fsync` desligado, porta 55434, descartável; banco.md, "O portão de PostgreSQL de uma suíte"). Ligar o Docker Desktop mexe no WSL, que o `CLAUDE.md` põe sob autorização explícita do
dono. Não aponte a variável para outro PostgreSQL da máquina: a credencial dele fica no `.env`, que não se lê.

## 5. CI

**Desde 30/09/2026 o CI não roda mais a cada push na `main`** (decisão do dono: ambiente ainda não produtivo; o CI
atrasava o processo). Onde a tabela abaixo diz "todo push/PR", leia "PR, corrida diária e disparo manual". A rede de
segurança é a corrida diária das 05:17 UTC (conjunto inteiro, com PostgreSQL) e o `workflow_dispatch`; o laço de
trabalho é `scripts/testes-afetados.py` (§4). Não é mais preciso `[skip ci]` nos commits. Para voltar: devolver
`push: branches: [main]` ao `on:` do `ci.yml` (e esperar o CI antes do deploy).

`.github/workflows/ci.yml` — **6 jobs** (até 24/09 eram 5, e o cabeçalho do arquivo dizia 4):

| Job | Quando | O que faz |
|---|---|---|
| `backend-sqlite` | todo push/PR | `pytest -q` contra SQLite |
| `backend-postgres` | `schedule` (diário, 05:17 UTC) ou `workflow_dispatch` (com `somente_postgres=true` roda só ele, hospedado, sem ocupar o runner do central; `gh workflow run ci.yml -f somente_postgres=true`) | `pytest -q` contra `postgres:17` de serviço, `TEST_DATABASE_URL`; limite de **60 min** desde 01/10 (o de 25 cancelou o cron de 01/10, run 36819958569) |
| `frontend` | todo push/PR | `npm run typecheck` + `npm test` |
| `dependencias` | todo push/PR + diário | `pip-audit --strict` (backend + worker) e `npm audit --audit-level=high` (frontend, Appium). No Appium, ainda `npm ci` + `node corrigir-empacotados.mjs --conferir`: o driver traz dependências dentro do tarball, e o `npm audit` só lê o lock (K-064) |
| `worker-agent-smoke` | todo push/PR | instala só `worker-requirements.txt` e importa `app.worker.agent` — prova que o agente continua leve |
| `docs` | todo push/PR | `python scripts/docs-check.py` + testes puros de `scripts/tests` (docs-check e livro-razão do plano-100) |

**Lacunas conhecidas:** dos testes de `scripts/tests` só os puros têm job (o `npm run build` entrou no job do painel em 27/09, B7) — os
demais chamam `pwsh` com caminhos do Windows e rodam só localmente (`backend\.venv\Scripts\python.exe -m pytest -q scripts/tests`).

**Runner próprio na máquina central (28/09).** O limite de gasto do Actions da conta foi atingido em 26–27/09
(US$ 10 cobrados; o ciclo vai de 1º a 30 do mês) e o dono não vai pagar mais (K-040). Os jobs passaram a rodar num
runner **próprio** na máquina central, que não consome minutos da conta:

- **Onde está:** `C:\actions-runner` (runner oficial `actions/runner` v2.337.0, SHA-256 conferido com o da release),
  registrado no repositório como `central` (rótulos `self-hosted`, `Windows`, `X64`, `central`).
- **Como sobe:** tarefa agendada `farm-ci-runner`: `run.cmd`, usuário `Administrator`, S4U, privilégio mais alto, no
  boot, religa se cair (mesmo molde da `farm-central`; o `setup-python` no Windows instala no toolcache e pede
  administrador).
- **Quem escolhe o runner:** a variável do repositório `CI_RUNS_ON` (JSON). Hoje é `["self-hosted","central"]`. Para
  voltar tudo ao runner da GitHub: `gh variable delete CI_RUNS_ON --repo FlavioNeto11/android`.
- **O que continua na GitHub:** `backend-postgres`, que usa contêiner de serviço (só em runner Linux com Docker; o
  Docker Desktop do central depende do WSL, que segue pedindo autorização). Com a cota esgotada ele falha até 1º do
  mês; `conteiner.yml` também.
- **CI × parque (28/09):** a suíte e o vitest no central durante uma execução real deixaram o aparelho lento (scroll
  virou toque longo, digitação cortada; `r-20260928165254-e31953`). Três travas: a tarefa `farm-ci-runner` roda com
  **prioridade ociosa** (Priority 10; os jobs herdam do Listener); cada job do runner próprio começa esperando o
  parque ficar sem objetivo em execução (`.github/actions/esperar-parque`, `working` de `GET /api/servers/limits`,
  teto de 20 min, depois roda com aviso); e o `vitest` usa 3 workers ali.
- **Estado do runner:** a tarefa `farm-ci-runner` aparece `Ready` mesmo com o runner no ar (o processo que ela lança
  termina depois de deixar o `Runner.Listener` de pé). Quem diz se ele está online é
  `gh api repos/FlavioNeto11/android/actions/runners`; pausar de verdade é `Stop-ScheduledTask` e encerrar
  `Runner.Listener`/`Runner.Worker` (de preferência com `busy=false`). Primeira corrida inteira verde no runner
  próprio: `69bba2d` e `0d2a508` (28/09).
- **Gatilhos:** push só na `main`; branch com pull request roda pelo `pull_request` (antes eram duas corridas por
  commit na mesma fila de um runner só). Um push novo no mesmo ref cancela a corrida anterior.
- **Isolamento:** cada job de Python tem venv próprio (`.github/actions/python-isolado`), porque no runner próprio o
  Python do toolcache é compartilhado; `shell: pwsh` nos dois sistemas (no Windows o runner resolve `bash` para o do WSL); um push novo no
  mesmo ref cancela o CI anterior (`concurrency`).
- **Custo no central:** um job por vez; a suíte do backend leva ~18 min ali e divide CPU com os emuladores (22
  núcleos). Parar o runner: `Stop-ScheduledTask farm-ci-runner`; remover: `C:\actions-runner\config.cmd remove` com
  um token de remoção (`gh api -X POST repos/FlavioNeto11/android/actions/runners/remove-token`) e
  `Unregister-ScheduledTask farm-ci-runner`.
- **CI não bloqueia enquanto não for produção** (decisão do dono de 30/09, ~18:35Z): o push de código vai à `main`
  com `[skip ci]` por padrão, e o deploy não espera o CI (o `deploy.ps1` nunca o consultou: era convenção). O que
  valida antes do push é a suíte local dos arquivos afetados, mais `tests/test_arquitetura.py` e
  `tests/test_pacote_do_agente.py`; o deploy segue com ensaio de migração e backup. A rede de segurança é o cron
  diário (05:17Z, inclusive o `backend-postgres`), que o `[skip ci]` não afeta, e o `workflow_dispatch` quando se
  quiser uma rodada inteira. Reverter: voltar a empurrar sem `[skip ci]` e esperar o CI antes do deploy.

## 6. Deploy

`scripts/deploy.ps1` — ordem fixa **parar → copiar o banco → subir → conferir**, sempre nessa ordem. Com a
tarefa `farm-central` registrada, parar/subir são da tarefa (o supervisor sobe o backend). `-Ensaio` faz tudo sem
tocar produção. Existe porque a subida real que importava aconteceu **sem ele**: o backend de produção rodava
desde antes de duas migrações existirem — só o backup evita repetir isso.

**"Alguém responde na 8000" não é "a Farm responde"** (26/09/2026). O `cartorio-api-1`, outro projeto nesta
máquina, publica `0.0.0.0:8000`; o backend da Farm escuta em `127.0.0.1:8000` e os dois convivem. Com a Farm
parada, o health caía no Cartório (404) e o supervisor, que aceitava qualquer resposta HTTP, não subia a Farm.
Supervisor, `deploy.ps1`, `start.ps1`, `stop.ps1`, `restore.ps1` e `loja-janela.ps1` agora perguntam QUEM
responde (`Health.service`, ver `backend/app/identidade.py` e `scripts/lib/farm-health.ps1`); o `stop.ps1` também
só envia o token de encerramento para a Farm identificada.

**O Appium do backend anterior não fica para o próximo** (K-039, 28/09/2026). Três deploys seguidos deixaram o
`node` do Appium na 4723 depois do `stop.ps1`; o backend novo o readotava e subia `degraded`
(`appium_log_masking_off`, com o preenchimento de credencial bloqueado, ou `appium_down`). Agora o `stop.ps1`, depois
que a Farm para de responder, encerra o Appium **deste projeto** que sobrou na porta de `appium:` do
`config/config.yaml`. Só o `node.exe` cuja linha de comando aponta para `tools\appium` desta árvore: outro processo
na porta fica, com aviso (`scripts/lib/appium-do-projeto.ps1`). Antes, ele dá 10 s para um backend que ainda está
saindo desligar o próprio Appium. `pwsh -File scripts\stop.ps1 -Simular` diz o que seria encerrado, sem encerrar
nada. Um aviso "linha de comando ilegível" quer dizer shell sem elevação: rode o deploy elevado.

Conferir **o resultado**, não só o código de saída (lição registrada em 24/09 depois de três defeitos da família
"deploy ok, usuário vê código/config velho" no mesmo dia — dist não rebuildado, config recriado do exemplo,
`index.html` sem `Cache-Control`):

- `/api/health` — commit e migração aplicada.
- Porta do canal do agente: `worker_port` (padrão 8010, `config.py:103`) — `Get-NetTCPConnection -LocalPort 8010`.
- Aparelhos externos aparecem no snapshot do parque (não só "backend no ar").
- Config efetivamente lido (não o exemplo) — o painel ou `/api/diagnostics` mostram os valores de produção.

**Dependências.** Desde 25/09 o `deploy.ps1` roda `uv pip install -r requirements.txt` no venv do backend
**entre parar e subir** (passo 3b; `-PularDependencias` desliga). Antes disso ele não instalava nada, e uma versão
nova no `requirements.txt` (ex.: `cryptography` 46.0.3 → 50.0.0, item T.4) nunca chegava à produção. O venv é do
`uv` e não tem `pip` dentro: `python -m pip` falha com "No module named pip". Tem de ser com o backend parado,
porque no Windows a `.pyd` carregada fica travada. O agente do worker não acompanha: `worker-requirements.txt` se
instala na máquina dele.

## 7. Migrações

Nunca editar uma migração já aplicada em produção. A lição está registrada no próprio repositório:
`backend/migrations/028_convergencia_da_008.sql` existe porque a migração `008` foi reescrita **no lugar** depois
de já aplicada (commit citado no comentário do arquivo) — o esquema de produção da 008 passou a divergir do que
o mesmo arquivo gera num banco novo, sem nada detectar. A correção é sempre uma migração **nova** que converge o
estado antigo, nunca uma edição retroativa.

## 8. Backup e restauração

- **`scripts/backup.ps1`** — roda com o backend **no ar**, sem parar nada. Copia o banco pela API de backup
  online do SQLite (não `Copy-Item`: o banco roda em WAL; copiar só o `.sqlite3` perde as últimas transações),
  mais `config/` e a chave do cofre. Tarefa diária `farm-backup` (03:00, `-Instalar`); o deploy guarda as 10
  cópias de deploy mais novas (poda em ensaio até o arquivo `PODAR-LIGADO`, depois do sim do dono) e aceita `-PularBackup` até 60 min depois de um `-Ensaio` no mesmo commit
  ([`banco.md`](banco.md), 29.38).
- **`scripts/restore.ps1`** — por omissão é **ensaio**: copia para uma pasta limpa, abre, confere integridade,
  imprime o conteúdo, sem tocar `data/`. Restauração de verdade exige `-De <pasta> -Confirmar`, com o **backend
  parado**; o script move `poc.sqlite3`/`-wal`/`-shm` atuais para `data/substituido-<carimbo>` antes de trocar.
  **Nunca copiar o `.sqlite3` do backup por cima à mão** — o arquivo do backup fica na raiz da pasta de backup,
  não em `data/`, e pular o script pula a checagem de integridade.
- **Restaurar o banco regride a cerca** (`commands.fence`, usada para invalidar comando obsoleto por aparelho):
  depois de restaurar, o agente recusa comandos com "cerca N é anterior à última executada (M)" e os `start`
  ficam `failed` sem reparo automático. Procedimento: subir manualmente o `fence` do último comando do aparelho
  no SQLite até o M citado pelo agente, e reemitir o comando.

## 9. Agente do worker

Instalação e atualização são **manuais**, sem deploy automático (`scripts/worker-install.ps1`,
`scripts/worker-agent.ps1 -Instalar` registra a tarefa agendada que sobe no boot e religa se cair —
achados #137/#38/#14 do plano-100: o repositório mandava "deixar como serviço" sem entregar o script que faz
isso). Pontos que já causaram incidente:

- **O pacote do agente é o de `backend/worker-manifest.txt`** (27/09). O `deploy.ps1` não copia o agente: no fim,
  lista as entradas do manifesto e manda rodar o `worker-install.ps1` na máquina do worker, com autorização.
  Antes, mandava copiar só `backend/app/worker/`, o que dava `ImportError` no agente.
- **PowerShell 5.1 exige `.ps1` com BOM UTF-8** — sem o BOM, um travessão no arquivo quebra o parser do agente
  na máquina do worker (Windows mais antigo que o do central).
- `BUILD_VERSION` identifica o pacote instalado; o central marca `agent_outdated` quando a versão do worker
  difere da dele (`backend/app/api.py:2992`). Ao atualizar o pacote, `config.py` do agente precisa ser copiado
  **antes** de gravar `BUILD_VERSION` (o pacote do agente inclui uma cópia própria desse arquivo).
- `scripts/install-central-service.ps1` registra o **backend do central** como tarefa supervisionada (o túnel já
  tinha gatilho de boot; o backend não — dependia de alguém abrir uma sessão interativa).

## 10. Diagnóstico

- `scripts/diagnose.ps1` — só leitura, roda no host onde os emuladores vão rodar; grava
  `data/diagnostics-host.json`, exibido na aba Diagnóstico do painel: aceleração (WHPX), features do Windows,
  RAM disponível, top processos por memória, discos.
- `GET /api/health` (`backend/app/api.py:210`) — commit, migração, `ai_billing` (conta de IA sem crédito),
  estado do túnel/worker.
- `GET /api/ai/balances` (`curl -s http://127.0.0.1:8000/api/ai/balances`) — saldo **estimado** das contas de IA
  (Anthropic, OpenAI, Gemini), com limites; concilia pelo relatório de custo do provedor com cache de 15 min
  (`?refresh=1` força). Registrar um saldo lido na conta do provedor: `POST /api/ai/balances/{conta}` (grava a
  leitura e concilia na hora; ADR-051). Nenhuma das duas chama modelo de IA.
- `GET /api/diagnostics` (`backend/app/api.py:243`) — o mesmo relatório do `diagnose.ps1` mais o que só o
  backend sabe (capacidade medida, ferramentas).
- **Relógio do host** — a tarefa `farm-relogio` (SYSTEM, a cada 15 min) roda `scripts/sincronizar-relogio.ps1`: mede o
  desvio pelo NTP.br com `w32tm /stripchart` e ajusta acima de 0,2 s; o `w32time` fica sem sincronização própria
  (`syncfromflags:NO`), porque a rede bloqueia NTP com porta de origem 123 (K-055). Cada rodada vai para
  `data\logs\relogio.log`; `-Simular` mede sem ajustar.
- **Antes de qualquer experimento num aparelho** (agente, `adb input`, `settings put`, carga de CPU): tirar um screencap
  e ler a conta logada, sem tocar. `account_label`, `/personas` e os vínculos não bastam: o android-04 tinha
  `qa-user-04` e nenhuma persona, com o felipe travado em "Confirm you're human" (K-053). Aparelho com `locked_account`
  está em quarentena e não se toca; experimento vai num aparelho novo e sem conta (provisionar e aposentar são
  permitidos no ambiente central).

## 11. Segurança

- **`API_TOKEN`** — obrigatório para sair do loopback (junto com `server.public_hosts` e TLS); é também a
  credencial de `POST /api/login`, que troca "nome + token" por cookie de sessão (`backend/app/security/
  sessions.py`, `COOKIE`/`VALIDADE_S`). A identidade da sessão é trilha de auditoria, não controle de acesso por
  pessoa — quem tem o token entra com o nome que quiser.
- **Isenção de loopback pelo par, não só pelo `Host`** — `backend/app/security/access.py:70` (`avaliar`) exige
  par **e** nome de loopback juntos; nome de loopback vindo de outro IP responde 401 (não 403: o nome não é
  hostil, falta o segredo).
- **TLS fora do loopback** — `server.tls_cert`/`server.tls_key` ou `server.tls_behind_proxy`
  (`backend/app/config.py:112-118`); sem isso e sem `API_TOKEN`+`public_hosts`, o backend recusa subir fora de
  `127.0.0.1`.
- **Cofre DPAPI** — `backend/app/security/secret_store.py` (`DPAPI_ENTROPY`, linha 32): a chave mestra é
  embrulhada por usuário+máquina no Windows; copiar banco sem a chave não abre as credenciais. Dois backends no
  mesmo banco precisam da MESMA chave mestra (`CREDENTIALS_MASTER_KEY` explícita) — DPAPI gera uma por máquina, e
  `backend/app/security/rekey.py` existe para migrar de uma chave DPAPI para outra explícita sem recadastrar
  tudo.
- **Redação** — `backend/app/security/redaction.py` (`redact`/`redact_obj`/`RedactingFilter`): remove segredo de
  texto e de log (token, senha, `Authorization: Basic`, chaves de nuvem) antes de qualquer gravação.
- **Túnel com chave de host conferida** — o canal do agente e o ADB remoto passam por SSH
  (`scripts/worker-tunnel.ps1`); a digital da chave de host do worker precisa ser conferida manualmente antes de
  confiar no túnel (feito parcialmente em 23/09 para o worker do parque — item 9.4 do plano-100, resto pendente).
- **Ambiente dos processos filhos por lista de permissão** (item 29.47, 03/10/2026) —
  `backend/app/devices/sdk.py` (`ambiente_dos_filhos`). Os filhos são adb, emulador, avdmanager, Appium, o sing-box da
  rede e as sondas do Diagnóstico.
  - Recebem só as variáveis do sistema e do perfil (`PATH`, `SYSTEMROOT`, `TEMP`, `USERPROFILE`, `APPDATA`…),
    `JAVA_HOME`, `ANDROID_*` e `ADB_*`.
  - Nunca passa `TYPESAFE_*`, `OPENAI_*`, `ANTHROPIC_*`, `GEMINI_*`, `FARM_*` nem nome com cara de segredo (`TOKEN`,
    `SECRET`, `PASSWORD`, `API_KEY`…).
  - Antes, eles herdavam o ambiente inteiro do backend, e o qemu tinha `TYPESAFE_API_KEY` (K-078).
  - Um filho que precise de uma variável nova a recebe pelo nome em `AMBIENTE_PERMITIDO`. Proxy (`HTTP_PROXY`) fica de
    fora de propósito, porque a URL pode levar senha.
  - A mesma lista vale para o PowerShell da leitura do firewall (`rede_firewall.executar_powershell`) e para o
    `icacls` das trancas de arquivo: o da rede, o do segredo local e o do agente (`worker/settings.py`). Desde
    03/10/2026.
  - O git e o ripgrep do Context Retrieval recebem `sem_segredos()`: o ambiente inteiro menos os nomes de segredo, só
    com a segunda trava. Precisam de `GIT_*`, `SSH_*` e da configuração do usuário.
  - A guarda é `tests/test_ambiente_dos_filhos.py::test_nenhum_lancamento_do_backend_herda_o_ambiente_inteiro`. Por
    AST, ela recusa no `backend/app` todo `subprocess.*` e todo `create_subprocess_*` sem `env=`, qualquer `os.system`,
    `os.popen`, `os.spawn*` ou `os.exec*`, e qualquer cópia de `os.environ`. Exceção única, pelo nome:
    `supervisor.iniciar_backend`, cujo filho é o próprio backend.

### Portal público pelo túnel da Cloudflare (29.54, ADR-073; no ar desde 03/10/2026, prova `real`)

O painel abre em `https://dev.nvit.com.br/central/` por um túnel de SAÍDA da Cloudflare nesta máquina: nenhuma porta é
aberta, o roteador não é tocado e `server.host` continua `127.0.0.1`. O `cloudflared` entrega ao central, com par
`127.0.0.1`, cada requisição do hostname; quem separa o público do local é o `Host` (ADR-073). **Tudo abaixo foi
executado em 03/10/2026** (`real`, menos o login do dono): é o procedimento do dono e da orquestradora, nesta ordem. A decisão e o que fica de fora
(webhook do Trello, WAF) estão no ADR-073.

**Regras que não se negociam na configuração do túnel**
- **`httpHostHeader` nunca.** Ele reescreve o `Host` para um nome de loopback, e o tráfego da internet passaria como
  local, sem credencial.
- **O canal do worker não vai ao hostname.** A regra `path: ^/api/worker/` devolve 404 antes da regra geral, e o destino
  nunca é a porta `server.worker_port`. A barra final é de propósito: sem ela a regra casaria também `/api/workers`, a
  rota REST da tela de workers do painel, e a quebraria de fora. O código também recusa (4403) o WebSocket do worker com
  Host público na porta do painel quando `server.worker_port != 0`: a regra do ingress deixou de ser a única barreira.
- **Sem credencial só abrem** `/central/`, os dois redirecionamentos (`/` e `/central`) e `/api/login|logout|session`.
  Os docs da API agora moram em `/api/docs`, `/api/redoc` e `/api/openapi.json` e exigem credencial (loopback livre).
- **"Always Use HTTPS" ligado na zona** é pré-requisito (sem ele o login por http levaria o token em claro até a borda);
  HSTS é opcional, decisão do dono. O `API_TOKEN` tem de ser aleatório e longo (`scripts/portal-gerar-senha.ps1`: 24 bytes
  do gerador criptográfico, 32 caracteres).
- **A tranca de login é por cliente (29.56)** e conta também o `Bearer` errado em `/api/*`: pelo túnel, o cliente é o
  `CF-Connecting-IP` (só com par loopback, `tls_behind_proxy: true` e `Host` em `public_hosts`); o acesso local nunca se
  tranca. A regra de limite de taxa da Cloudflare continua como a primeira barreira (ela não segura quem usa vários IPs;
  a entropia do `API_TOKEN` segura). Toda resposta leva `X-Frame-Options: DENY`, `nosniff` e `Referrer-Policy: same-origin`.
- **Sem `API_TOKEN` ninguém entra pelo endereço público** (o login é por token). Quem grava o token no `.env` é o dono;
  o procedimento nunca o lê nem o imprime. `GET /api/health` mostra `exposicao_publica_incompleta` enquanto faltar
  qualquer peça.

**Procedimento** (`scripts/portal-instalar-tunel.ps1`, rodado pelo dono como Administrador, faz os passos 3 a 6 com as
mesmas travas; `python scripts/portal-config.py ligar` faz as três linhas do `config.yaml` do passo 7, com cópia de
segurança e `--ensaio`; `scripts/portal-gerar-senha.ps1`, também do dono, grava o `API_TOKEN` sem mostrá-lo)
1. `winget install --id Cloudflare.cloudflared -e` (terminal novo depois).
2. `cloudflared tunnel login`: consentimento do dono no navegador, escolhendo `nvit.com.br`. Grava o `cert.pem` em
   `%USERPROFILE%\.cloudflared`, que é segredo e não se lê nem se copia.
3. `cloudflared tunnel create central-farm` (grava `<uuid>.json`, a credencial do túnel, também segredo).
4. Pasta `C:\cloudflared-central`, fechada a SYSTEM e Administradores, com o `<uuid>.json` e o `config.yml`:
   ```yaml
   tunnel: <uuid>
   credentials-file: C:\cloudflared-central\<uuid>.json
   ingress:
     - hostname: dev.nvit.com.br
       path: ^/api/worker/
       service: http_status:404
     - hostname: dev.nvit.com.br
       service: http://127.0.0.1:8000
     - service: http_status:404
   ```
   Confira com `cloudflared tunnel --config C:\cloudflared-central\config.yml ingress validate` e, com
   `ingress rule https://dev.nvit.com.br/api/worker/ws`, que cai na regra 404 e que `.../central/` cai na do central.
5. `cloudflared tunnel route dns --overwrite-dns central-farm dev.nvit.com.br` (tira o hostname de um túnel antigo, se
   houver).
6. Serviço do Windows: `cloudflared --config C:\cloudflared-central\config.yml service install` e corrija o `ImagePath`
   do serviço `Cloudflared` para `"<cloudflared.exe>" --config "C:\cloudflared-central\config.yml" tunnel run
   central-farm` (o `service install` não guarda o `--config`; sem a correção o serviço sobe sem túnel); reinicie o
   serviço.
7. **Só depois**, no `config/config.yaml` da instalação (fora do Git; o exemplo é `config/config.example.yaml`) e no
   `.env`:
   - `server.public_hosts: [dev.nvit.com.br]`;
   - `server.tls_behind_proxy: true`;
   - `https://dev.nvit.com.br` em `server.allowed_origins` (sem ela o POST do login leva 403 `forbidden_origin`);
   - `API_TOKEN` no `.env` (dono);
   - opcional: `avisos.url_painel: https://dev.nvit.com.br/central` para o link do aviso do Telegram;
   - reinicie a tarefa `farm-central`. Antes deste passo o central ainda não conhece o hostname e responde 403 a tudo,
     que é o estado seguro para conferir o túnel.

**Conferências de ida ao ar** (de FORA da LAN, por exemplo no 4G; marque o resultado como `real`, com data e máquina).
`bash scripts/portal-prova-de-fora.sh depois` faz todas, menos o login, sem credencial e sem chamar a rota de login com
senha; `antes` confere o estado seguro (403) antes do passo 7. A linha do 429 prova a regra de limite de taxa da
Cloudflare desta instalação: sem a regra, rode com `SEM_LIMITE_DE_TAXA=1`. Com a Etapa 2 do Trello no ar (`trello.webhook.enabled: true`), rode com
`WEBHOOK_DO_TRELLO=ligado`: o webhook passa a responder 200 ao HEAD e 401 ao GET e ao POST sem assinatura (a rodada manda um
POST ruim só; cinco recusas em 10 min acendem `trello_webhook_assinatura_invalida`).
| Pedido | Esperado |
|---|---|
| `https://dev.nvit.com.br/central/` | 200, tela de login |
| `http://dev.nvit.com.br/` | 301 para https (Always Use HTTPS) |
| `https://dev.nvit.com.br/` | 307 para `/central/` |
| `https://dev.nvit.com.br/docs` e `/openapi.json` | 404 |
| `https://dev.nvit.com.br/api/docs` | 401 |
| `https://dev.nvit.com.br/api/instances` | **401**, nunca 200 |
| `https://dev.nvit.com.br/api/health` | 401 |
| `https://dev.nvit.com.br/api/worker/ws` | 404 (regra do túnel) |
| login com o `API_TOKEN` no painel | entra; sem o token, não |
| `GET /api/health` por dentro | sem `exposicao_publica_incompleta` |

Antes do passo 7 o esperado em `/api/instances` é 403 e depois dele 401. **Essa é a prova de que o `Host` chega
preservado:** 403 antes de declarar o hostname e 401 depois. 200 em qualquer momento = **pare o serviço**
(`Stop-Service Cloudflared`) e investigue.

**O serviço do Windows.** O `cloudflared service install` sobe um processo sem argumentos que não atende à parada:
encerre esse processo e inicie o serviço, nunca `Restart-Service`.

**Já feito no `real` (03/10/2026, máquina central):** túnel `central-farm` criado e no ar (19:45Z), DNS apontado, prova
de fora 403/404 (19:47Z), "Always Use HTTPS" ligado e provado com 301 (19:54Z). Senha do portal (`API_TOKEN`) gravada pelo dono
(~19:59Z), hostname declarado no `config.yaml` (21:35:02Z, cópia anterior em
`data/backups/config.yaml.antes-portal-publico-20261003-213502`), central reiniciado (21:46:56Z, checkout `4ad5f8b6`,
código do `2264843e`) e **prova de fora às 21:47:28Z, 19 de 19** (o script que hoje é `scripts/portal-prova-de-fora.sh`,
sem credencial): API 401, `/api/session` 200 pedindo senha, painel 200 com os arquivos em `/central/`, documentação da
API só atrás do login, canal do worker 404, `http` 301; antes do reinício, 403 (21:32Z). O primeiro login pelo endereço público
foi feito pelo dono e funcionou (dito por ele no chat, 03/10 ~22:39Z). **Na Cloudflare (03/10 ~22:47Z, com o sim do dono):**
regra de limite de taxa `central-login-por-ip` (só `/api/login` de `dev.nvit.com.br`, por IP: mais de 1 pedido em 10 s
bloqueia por 10 s; é a única regra de limite do plano gratuito) e HSTS de um mês, sem subdomínios e sem preload. Prova de
fora às 22:49:15Z, sem tentativa de login: `Strict-Transport-Security: max-age=2592000`; `GET /api/login` três vezes
seguidas dá 405, 429 e 429, e 13 s depois volta a 405; as outras rotas não são afetadas. A tranca de login do app
passou a ser por cliente no 29.56 (antes era global): a regra mantém um IP abaixo das 8 tentativas por minuto, e os
chutes de um IP já não trancam os outros. Depois de errar a senha, espere 10 s para tentar de novo. O selo "agente
defasado" do notebook depois desse reinício era falso; desde o 29.59 o central compara o código do pacote do
agente, e commit só de docs não acende o selo.

**Recuo.** Tire `dev.nvit.com.br` de `server.public_hosts` e reinicie `farm-central`: tudo volta a 403, painel incluído.
`python scripts/portal-config.py recuar` tira exatamente as sete linhas que o `ligar` pôs (com cópia de segurança; ele
para sem tirar nada se alguma foi mexida à mão) e `conferir` só lê e diz se o bloco `server` está pronto. Com o script versionado, em
03/10/2026 às 23:05:38Z no central: `conferir` 5 de 5, `ligar --ensaio` "nada a fazer" e `recuar --ensaio` 7 linhas,
sem gravar; a prova de fora deu 22 de 22 às 23:06:58Z.
Para tirar o hostname do ar, `Stop-Service Cloudflared` (e, se for o caso, `cloudflared service uninstall`).

### Site institucional na raiz (29.77, ADR-075; desligado, prova `simulated`)

O site público da SICAT/ANA (pasta `site/`, versionada) vai na raiz de `https://dev.nvit.com.br/`, com o painel seguindo
em `/central/`. O formulário de contato manda a mensagem ao Telegram do dono pelo bot (contrato `portal.contato`, item
28.32 da Canais). Tudo DESLIGADO de fábrica; o `config.yaml` só vale na subida.

**Ligar (orquestradora, depois do PR da Canais no ar):**

1. No `config/config.yaml` do central, o bloco abaixo, com os dois números de verdade (estão no brief fora do Git;
   nunca em doc, teste ou commit). Os dois `_ligado` vão juntos: contato sem o site é recusado na subida, e só o site
   mostra, no lugar do formulário, o aviso de que ele está fora do ar (o recuo parcial).

   ```yaml
   portal:
     site_ligado: true
     contato_ligado: true
     contatos:
       - nome: "<nome>"
         telefone: "<+55 (DDD) número>"
   ```

2. Conferir que `https://dev.nvit.com.br` está em `server.allowed_origins` (está desde o ADR-073; sem ela todo envio
   leva 403) e que o aviso do Telegram está pronto (`GET /api/canais/estado`). Depois do reinício, o `GET /api/health`
   não pode trazer `portal_contato_sem_ip_da_borda` (falta `tls_behind_proxy` ou o nome público: a taxa por cliente
   viraria uma só para todos).
3. `pwsh -File scripts\deploy.ps1 -Ensaio` confere a pasta `site/` (arquivo fora da lista derruba a subida do central
   inteiro; o caso comum é o `Thumbs.db` ou o `desktop.ini` do Explorer: apague e rode de novo). Depois, reiniciar a
   tarefa `farm-central`.
4. Prova de fora, sem credencial e sem contato de verdade:
   `SITE=ligado CONTATO=ligado bash scripts/portal-prova-de-fora.sh depois`. O `POST` dela leva a isca preenchida:
   202 prova Host, Origin, Content-Type e a exceção do portão, sem gravar nem avisar. 403 = falta a origem; 404 =
   bandeira desligada (ou o reinício não aconteceu); 401 = o código do 29.77 não está no ar.
5. O primeiro contato de verdade é do dono (ele preenche o formulário e confere a mensagem no Telegram): é efeito no
   Telegram dele, então pede o sim dele.

**O que esperar.** Taxa por cliente pelo IP da borda (`cf-connecting-ip`; só vale com `tls_behind_proxy` e o Host
público): 3 por hora e 10 por dia de fábrica (`portal.limites`). Teto global de 500 guardados por dia e de 20 avisos
por hora (acima, a linha fica `retido` e o laço `portal-contatos` manda quando a janela abre). Os contatos ficam em
`portal_contatos` (migração 107) por 180 dias. Para ver o que está parado sem expor o conteúdo:
`SELECT estado, motivo, COUNT(*) FROM portal_contatos GROUP BY 1, 2`.

**O que a página promete e onde isso vale** (aviso de privacidade, ADR-075):
- sem cookie: a prova de fora reprova se a raiz devolver `Set-Cookie` (a borda da Cloudflare poderia pôr um);
- 180 dias no sistema: o laço apaga a linha inteira a cada hora, com o contato ligado ou não;
- o descartado (teto diário, `campo_invalido`, `falhas_demais`) tem o conteúdo apagado sem chegar à equipe. O
  `pendente` (canal desligado) e o `retido` (excesso na hora) guardam o conteúdo até a entrega ou os 180 dias;
- cópias de segurança: a pasta `AAAAMMDD-HHmmss` sai na primeira cópia depois de 14 dias (`-Reter 14`), menos a mais
  nova; as de deploy e de ensaio têm ainda o teto de 10. Pasta com sufixo no nome não sai sozinha: depois de ligar o
  contato, quem cria uma a apaga à mão quando acabar;
- o `cliente_hash` (código do endereço de rede, nunca o IP) fica os mesmos 180 dias.

**Pedido de exclusão de um contato do site** (o visitante pede pelo formulário ou por telefone). Quem executa é o
operador, com o sim do dono no chat, porque apaga dado; a ação no painel é o 29.83.
1. Achar as linhas pelo telefone que o visitante deu, comparando só os dígitos (troque `<DIGITOS>` pelos últimos 8):
   `SELECT id, criado_em, estado FROM portal_contatos WHERE replace(replace(replace(replace(replace(telefone,' ',''),'-',''),'(',''),')',''),'+','') LIKE '%<DIGITOS>'`.
   Não copie o conteúdo para chat, cartão ou log.
2. Apagar a mensagem no chat do Telegram: o dono, à mão (o bot só apaga a própria mensagem até 48 h).
3. Responder ao visitante pelo telefone que ele deixou. Dizer que as cópias de segurança saem pela rotina delas, em até
   14 dias.
4. Apagar as linhas, incluindo a do próprio pedido de exclusão: `DELETE FROM portal_contatos WHERE id IN (<ids>)`.

**Recuo.** `site_ligado` e `contato_ligado` em `false` e reiniciar `farm-central`: a raiz volta ao 307 para o painel e a
rota responde 404. Recuo parcial: só `contato_ligado: false`; o site fica e mostra o aviso no lugar do formulário. A
retenção de 180 dias continua rodando com o contato desligado.

## 12. Tabela de scripts por risco

`[S]` seguro (só leitura ou sandbox) · `[T]` gasta chamada de API paga · `[P]` toca o parque, o ambiente central ou o sistema ·
`[D]` simula sem exigir flag explícita.

| Script | Risco | O que faz |
|---|---|---|
| `diagnose.ps1` | S | Diagnóstico do host, não altera nada |
| `install-prereqs.ps1` | P | Instala Android SDK + Appium; aceita licenças em nome do usuário |
| `start.ps1` / `stop.ps1` | P | Sobe/derruba o backend, Appium e (opcional) emuladores do projeto; o `stop.ps1` também encerra o Appium órfão deste projeto (K-039) e tem `-Simular` |
| `backup.ps1` | S | Cópia consistente do banco+config, sem parar nada |
| `testes-afetados.py` | S | Lista (e com `--run` roda) só os testes que o diff atinge; `--ocioso` roda em prioridade ociosa |
| `restore.ps1` (sem `-Confirmar`) | S | Ensaio em pasta limpa |
| `restore.ps1 -Confirmar` | P | Substitui `data/` de verdade, exige backend parado |
| `deploy.ps1` | P | Para → copia banco → sobe → confere; mexe na tarefa `farm-central` |
| `eval-run.ps1` (sem `-Yes`) | S | Só imprime o plano da bateria; nenhuma conexão, nenhum adb (26/09: antes, mesmo "simulado" fazia POST no backend vivo e rodava adb) |
| `eval-run.ps1 -Yes` | P/T | POST no backend vivo e adb nos aparelhos, mesmo com provedor simulado; com provedor real gasta API |
| `python scripts/rodada_qa_pareada.py` (sem opção) | S | Só o plano da rodada QA pareada (canário do planejador: Opus × perfil `planejador-sonnet`, ABBA por caso); nenhuma conexão |
| `python scripts/rodada_qa_pareada.py --checar` / `--ler RODADA` | S | Custo zero: saúde, aparelho, `POST /api/flows/match` e `POST /api/skills/resolve` de cada caso (diz "ok" quando passa); ou a leitura de `data/eval-results.jsonl` + `GET /api/usage?run_id` |
| `python scripts/rodada_qa_pareada.py --yes` | P/T | Roda `eval_run.py` por caso e braço no aparelho de QA (`--repeticoes N` repete o bloco ABBA de cada caso), com teto (`--teto-usd`, padrão 8) e o caso que passou a casar com fluxo ou habilidade pulado; só com a vez da orquestradora |
| `bench.py` (`simulado`, padrão) | S | Harness: aparelho falso, provedor simulado, banco temporário. Contagens com prova `simulated` ([`relatorio-desempenho.md`](relatorio-desempenho.md)) |
| `bench.py leitura` | S | Só GET em loopback. A primeira GET do Diagnóstico depois de reiniciar coleta as versões das ferramentas do host (`emulator -accel-check`, `adb version`) |
| `bench.py comparar` | S | Antes × depois, com limite e amostra mínima declarados na linha de base; sem isso, o veredito é "exploratório" |
| `eval-rejudge.ps1 -Yes` | T | Rejulga capturas com o modelo caro |
| `probe-models.py --yes` | T | Sonda modelos configurados (poucos centavos) |
| `probe-image.ps1` | S | AVD **temporário**, removido ao final; não toca instâncias do projeto |
| `rotation-test.ps1` | P/T | Liga/hiberna instâncias reais; gasta IA se não estiver em modo simulado |
| `scale-test.ps1` | P | Liga instâncias reais até o hardware não sustentar; pula aparelho em quarentena (`locked_account`) |
| `sincronizar-relogio.ps1` | P | Mede o desvio do relógio do central pelo NTP.br e ajusta acima de 0,2 s (`-Simular` só mede); `-Instalar` registra a tarefa `farm-relogio` e tira a sincronização do `w32time`. Mexer no relógio exige autorização do dono (dada em 28/09) |
| `recuperar-parque.ps1` | P | Reinicia aparelhos remotos pelo worker |
| `worker-install.ps1` / `worker-agent.ps1 -Instalar` | P | Instala/registra o agente numa máquina worker |
| `install-central-service.ps1` | P | Registra o backend do central como tarefa supervisionada |
| `worker-tunnel.ps1` | P | Sobe/mantém o túnel SSH real |
| `portal-instalar-tunel.ps1` | P | **Rodado pelo dono**, como Administrador, depois do `cloudflared tunnel login`: cria o túnel da Cloudflare, grava o `config.yml` com as travas do ADR-073, aponta o DNS e instala o serviço `Cloudflared`. Não abre porta nem mexe no central |
| `portal-gerar-senha.ps1` | P | **Rodado pelo dono**: gera o `API_TOKEN` e grava no `.env` sem mostrar na tela (`-Trocar` substitui). Sessão de IA não roda este script no `.env` de verdade |
| `python scripts/portal-config.py conferir`, ou `ligar`/`recuar` com `--ensaio` | S | Só lê o `config.yaml`: diz se o bloco `server` está pronto para o portal ou o que mudaria |
| `python scripts/portal-config.py ligar` / `recuar` | P | Declara ou tira o hostname público no `config.yaml` (sete linhas, com cópia em `data/backups/`); vale no próximo reinício do central |
| `portal-prova-de-fora.sh antes` / `depois` | S | Só pedidos sem credencial ao endereço público; nunca tenta login. Sai com 2 se `/api/instances` der 200. Com `SITE=ligado` confere o site na raiz (CSP, `robots.txt`, 404 fora da lista fechada); com `CONTATO=ligado`, um `POST` com a isca (não grava nem avisa), o 415 e o 413. Testado contra um `curl` falso em `scripts/tests/test_portal_prova_de_fora.py` |
| `usage-report.ps1` | S | Só lê `ai_calls`, não chama provedor |
| `demo-run.ps1` | D/T | Envia comando real ao backend (gasta IA se o provedor não for simulado) |
| `aceites-remotos.ps1` (sem `-Yes`) | S | Só mostra o roteiro |
| `aceites-remotos.ps1 -Yes` | P | Despacha comandos reais no parque |
| `test-restart-recovery.ps1` | P | Reinicia o backend com fila carregada, real |
| `personas_criar.py` / `personas_completar.py` | P | Escreve personas no banco do ambiente central |
| `avisos-telegram.py descobrir` / `testar` | S / P | Aviso fora do painel (28.11): `descobrir` só lê (`getUpdates` sem offset) e lista id, tipo, nome e @usuário dos chats que escreveram ao bot, sem imprimir o token; `testar` manda UMA mensagem real ao `TELEGRAM_CHAT_ID` (só o dono roda). Lê o `.env` na hora, sem reiniciar o backend |
| `trello-webhook.py` (`--ensaio` / `--aplicar` / `--desligar`) | S / P | Webhook do Trello (32.2, ADR-072): `--ensaio` (padrão) só LÊ o Trello (`GET /1/members/me/tokens`) e imprime o plano por quadro (criar, recriar, apagar, manter); `--aplicar` faz o cadastro (`POST /1/webhooks`, token só no cabeçalho), exige `trello.webhook.enabled` e `TRELLO_API_SECRET` e **só com o "vai" da orquestradora**; `--desligar` é o pedido explícito de remover os webhooks da Central (os de outro sistema nunca são tocados). Lê o `.env` na hora; não imprime segredo |
| `aprendizado-backlog.py` | S | Só GET em `/api/aprendizado/falhas?formato=md`: grava o "o que mais falha" em `data/aprendizado/backlog-AAAA-MM-DD.md` e imprime o topo; `--retroativo` inclui o legado classificado na leitura. Sem IA; o `API_TOKEN` nunca é impresso (ADR-054) |
| `candidatos-do-portal.py` | S | Só GET em `/api/aprendizado/falhas` (JSON, a de sempre e a da camada `pessoa`): grava em `data/aprendizado/candidatos-do-portal.json` os grupos abertos, sem `plan_item` e com `--minimo` ocorrências (padrão 3), mais as propostas abertas, com contagem, exemplos por id (nunca o texto do erro), frente sugerida pela camada e onde alterar (29.72). Cada candidato traz `amostra_de_lote` ("n de m" exemplos de execução nossa: `lote:`/`ensaio:` ou prova de fluxo, lida do banco com `--banco`, só leitura) e `dias_sem_ocorrer`; amostra toda nossa ou mais de 7 dias sem ocorrer vai para o fim (`rebaixado`). É para a orquestradora ler: nada entra no plano sem número dela. Sem IA; o `API_TOKEN` nunca é impresso |
| `aprendizado-telas.py` | S | Telas aprendidas: o deixa-um-fora sobre as observações reais (`--sem-regra thread --sem-regra feed`), com o banco aberto só para leitura (`mode=ro`); `exportar --app` pede o fragmento YAML ao central. Sem IA |

## 13. Incidentes conhecidos → sintoma → causa → ação

| Sintoma | Causa | Ação |
|---|---|---|
| `config/config.yaml` some depois de um checkout | Arquivo deixou de ser rastreado entre commits; git apaga da árvore ao trocar de commit | Restaurar de `data/backups/<carimbo>/config/`; `start.ps1` já recusa subir nesse estado (§2) |
| Health `degraded` depois do deploy ou de um reinício pelo supervisor, com `appium_log_masking_off` ou `appium_down` "readotado" | Appium do backend anterior ficou na porta e foi readotado (K-039) | O `stop.ps1` já o encerra, e o backend troca sozinho o órfão deste projeto que não prova o mascaramento; se voltar, leia os avisos do `stop.ps1` (shell sem elevação, outro programa na porta), rode `stop.ps1 -Simular` e veja o "não foi trocado: <motivo>" no detalhe do Appium |
| `data/poc.sqlite3` "malformed database schema" após reboot | Um `-wal` velho ao lado de um banco recopiado | `scripts/restore.ps1 -De <backup> -Confirmar` com backend parado; nunca copiar o `.sqlite3` por cima à mão |
| Cerca (`commands.fence`) regredida depois de restaurar o banco | `fence` é MAX+1 por aparelho; restaurar volta o contador | Subir o `fence` do aparelho no SQLite até o valor que o agente citou na recusa; reemitir o comando |
| Worker `offline` depois de reboot do notebook, agente vivo com `WinError 1225` e `tunel-<ip>.log` com `Connection timed out` | O notebook mudou de IP por DHCP e a tarefa `farm-tunel-<ip-antigo>` aponta para o IP velho (W8 §21) | Confirmar a identidade por impressão digital das chaves de host, `worker-tunnel.ps1 -Instalar -Worker <ip-novo> …` (remove antes a tarefa do IP velho: a colisão de portas a recusa); prevenção: reserva DHCP |
| Agente do worker não volta depois do boot do notebook | Tarefa agendada registrada sem gatilho de boot (script antigo) | Reinstalar com `scripts/worker-agent.ps1 -Instalar` (gera a tarefa com `AtStartup`) |
| Notebook do worker lento, emuladores com carga alta sem motivo aparente | Escalonador do Hyper-V no modo "core" em vez de "classic" | Conferir o evento `Hyper-V-Hypervisor` id 2 (precisa ser `0x2`, não `0x3`); `bcdedit /set hypervisorschedulertype classic` e reiniciar |
| Conta do provedor de IA sem crédito, execuções travam sem aviso claro | Conta esgotada (HTTP 402/billing) | `/api/health` acusa `ai_billing`; o disjuntor (`executor.py`, §6 de `docs/ia.md`) represa sem gastar tentativa |
| `decide` volta a usar Anthropic mesmo com Ollama configurado | Serviço Ollama fora do ar no host (sobe por login de usuário, não é tarefa de boot) | Conferir se o Ollama está no ar; sem ele, o fallback explícito assume — comportamento esperado, não bug |
| Relógio do central com segundos de desvio; `w32time` com o evento 47 "No valid response" | A rede bloqueia NTP com porta de ORIGEM 123, a do `w32time` | `scripts/sincronizar-relogio.ps1` e a tarefa `farm-relogio`; conferir `data\logs\relogio.log` (K-055) |
| Aposentar um aparelho no Windows falha com `avd_nao_apagado` | Arquivo somente-leitura que o emulador deixa no AVD (`pstore.bin`) | Corrigido em `2511b12` (`avd.py::_apagar_arvore`); noutro caso, procurar o atributo antes de suspeitar de processo segurando o arquivo (K-056) |
| Aparelho local levado a `restart` e `reset` pela escada de reparo enquanto a máquina central estava saturada (29/09: a sessão do lucas apagada no android-01) | Suíte inteira, Docker com testes em PostgreSQL e boot de outro aparelho ao mesmo tempo: os convidados "não ficam prontos" por falta de CPU do host, não por doença | Desde `9348e9c`, com a CPU ≥ `instances.remediation_host_cpu_max` (90%) o reparo espera 10 min ("Reparo adiado" no cartão); desde `c359f65`, nunca `reset` com conta. Conduta: um trabalho pesado por vez no central, testes em prioridade ociosa, Docker e WSL desligados depois dos testes em PostgreSQL (K-058) |

## 14. Contêineres: o central em desenvolvimento e validação

> **Estado da prova (26/09/2026).** `simulated`: `backend/tests/test_conteiner_central.py` (11 testes; o
> `AppState` sobe com `deploy/config.conteiner.yaml`, responde como a Farm e passa no healthcheck da imagem) e
> `scripts/tests/test_conteineres.py` (21 testes de leitura do compose, do Dockerfile e do `.dockerignore`).
> `not_run`: `docker build`, `docker compose up`, persistência, backup, restauração, rollback e os perfis
> `postgres`/`ollama`. O engine está parado nesta máquina, e ligar o Docker Desktop liga o WSL, o que exige
> autorização do dono. Nenhum ganho de densidade é afirmado.

**O que é.** Uma instância do central (backend + painel compilado) numa imagem reproduzível, para desenvolver e
validar isolado do ambiente central, que continua no Windows (§6). **O que não é:** microserviço, réplica, nem lugar de
emulador. Emulador em contêiner continua sendo VM e precisa de KVM; é outra frente.

| Arquivo | Papel |
|---|---|
| [`deploy/central.Dockerfile`](../deploy/central.Dockerfile) | multi-stage: `node:22.12.0-bookworm-slim` compila o painel; `python:3.13.15-slim-bookworm` roda, sem root |
| [`deploy/compose.yaml`](../deploy/compose.yaml) | `central` sempre; `postgres` (`17.11-bookworm`) e `ollama` (`0.34.4`) por perfil |
| [`deploy/config.conteiner.yaml`](../deploy/config.conteiner.yaml) | config montado somente leitura: zero aparelho, `worker_port: 0`, origens na porta 8100 |
| [`deploy/conteiner.env.example`](../deploy/conteiner.env.example) | modelo do `deploy/.env` (segredos; fora do Git e da imagem) |
| [`deploy/saude.py`](../deploy/saude.py), [`deploy/iniciar.py`](../deploy/iniciar.py) | healthcheck (vivo/pronto) e partida que recusa subir sem o config montado |
| [`.dockerignore`](../.dockerignore) | lista de permissão: só `backend/app`, `backend/migrations`, `requirements.txt`, `frontend/` e os dois scripts |

As tags foram conferidas no Docker Hub em 26/09. Node e Python são os do CI, e o teste confere. Depois do primeiro
pull real, fixe por digest (`@sha256:`).

### Decisões, e por quê

- **Uma réplica, `ROLE=all`.** O estado dos workers, o dono do controle manual, o barramento de eventos e os
  frames vivem na memória de um processo ([`banco.md`, "Pendências honestas"](banco.md#pendências-honestas);
  [`arquitetura.md`, "Papéis"](arquitetura.md#papéis-role)). Separar API e scheduler em réplicas quebraria isso sem
  aviso. `container_name` fixo faz o Docker recusar uma segunda cópia (`--scale central=2` falha por conflito de
  nome), e `deploy.replicas: 1` deixa isso escrito.
- **`OWNER_ID` fixo** (`farm-central-validacao`). O padrão é o hostname, que muda a cada contêiner. Numa
  atualização "sobe o novo, depois derruba o velho", há dois cenários ruins. Com o mesmo `OWNER_ID`, os dois
  processos tomariam as etapas um do outro como suas e as reconciliariam no meio da execução. Com o hostname, o
  novo não reconheceria as etapas interrompidas do antigo. O compose evita os dois: uma réplica, e o `up` recria
  parando o contêiner antigo antes de criar o novo (comportamento do Compose v2, não medido aqui).
  `update_config.order: stop-first` só vale no Swarm e fica escrito para quem migrar.
- **Porta só em `127.0.0.1:8100` do host.** A 8000 do host é da Farm do ambiente central e do `cartorio-api-1` (§6). O
  canal do worker (8010), o ADB e o Appium não são publicados. Postgres e Ollama não publicam nada: o central
  chega neles pela rede interna do compose.
- **Escuta em `0.0.0.0` só dentro do contêiner**, por `CONTAINER_LISTEN_HOST`. Só a imagem define essa variável.
  Ela é lida de `os.environ`, nunca do `.env`, e é recusada no Windows (`app.main.endereco_de_escuta`). O
  `server.host` continua `127.0.0.1`, e quem decide a exposição é a porta publicada. Pela porta publicada, o par é
  o gateway do Docker, nunca loopback: o painel pede login com `API_TOKEN`, e o backend recusa subir sem ele,
  antes de abrir o banco.
- **Canal do worker desligado** (`worker_port: 0`). O listener do túnel escuta sempre em `127.0.0.1` do processo,
  e dentro do contêiner isso é inalcançável. Worker remoto neste ambiente não é suportado (ver Limites).
- **Saúde por identidade.** Sem Android SDK na imagem, `sdk_missing`, que é problema duro, faz o `/api/health`
  responder `status: error` para sempre. A rota devolve 200 em qualquer caso. Por isso o healthcheck pergunta só
  "quem responde é a Farm?" (`corpo_e_da_farm`, a mesma regra do supervisor), e `degraded`/`error` contam como
  vivo. Fora do Swarm, o Docker nunca reinicia contêiner `unhealthy`. **Pronto** é outra pergunta, feita à mão:
  `saude.py --pronto` exige a identidade, o banco respondendo e `migration` igual à última migração da imagem.
- **Reinício limitado** (`restart: on-failure:3`, nunca `always`). Uma recusa de partida (config não montado,
  saída 78; `API_TOKEN` ausente) gera três linhas no log e para. `stop_grace_period: 40s` cobre o encerramento
  gracioso do uvicorn (10 s) e o do `AppState`. `init: true` repassa o SIGTERM.
- **Sem privilégio**: sem `privileged`, sem `docker.sock`, sem `/dev/kvm`; `cap_drop: ALL`,
  `no-new-privileges`, usuário `farm` (uid 10001), raiz somente leitura com `/tmp` em tmpfs. A raiz somente
  leitura **não foi exercida por uma subida real**: se a partida falhar com "Read-only file system", registre o
  caminho em vez de tirar a proteção.
- **Commit no health.** Ele sai de `<raiz>/.git/HEAD`. O build grava ali o sha de `FARM_COMMIT`, como HEAD
  destacado. Sem o argumento, `commit` fica `null`.
- **Logs** em `data/logs/backend.log`, dentro do volume. O `docker compose logs` mostra só erro de partida: o
  console só entra com TTY (achado #144), e ligar `tty: true` recriaria o mesmo log sem rotação no driver
  `json-file`.

### Subir e conferir (`not_run`)

A partir da raiz, em PowerShell, com o Docker no ar e Compose 2.20 ou mais novo:

```powershell
Copy-Item deploy\conteiner.env.example deploy\.env          # preencha API_TOKEN e CREDENTIALS_MASTER_KEY
$env:FARM_COMMIT = git rev-parse HEAD
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml ps                                        # central: healthy em até ~90 s
docker compose -f deploy/compose.yaml exec central python /app/deploy/saude.py --pronto
```

O que esperar de cada passo:

- **`--pronto`** imprime `ok: pronto (status error, migração <última de backend/migrations>)`.
- **`GET http://127.0.0.1:8100/api/health` sem credencial** responde **401**, e está certo.
- **Com `Authorization: Bearer <token>`** (o token vem de variável, nunca escrito na linha), a resposta traz:
  - `service: android-farm-central`;
  - `commit` igual a `git rev-parse HEAD`;
  - `database.dialect: sqlite`;
  - `problems` com `sdk_missing`, `appium_down` e `ai_simulated`, os três esperados aqui.
- **Painel** em `http://127.0.0.1:8100`: login com um nome e o token.

**Persistência.** Anote um número antes, por exemplo
`docker compose -f deploy/compose.yaml exec central python -c "import sqlite3;print(sqlite3.connect('/app/data/poc.sqlite3').execute('select count(*) from events').fetchone())"`.
Depois rode `docker compose -f deploy/compose.yaml down` **sem `-v`**, suba de novo com `up -d` e repita a
consulta. O número tem de ser igual ou maior, e `docker volume ls` tem de mostrar `farm-validacao_farm-dados`.
Atenção: `down -v` **apaga** os volumes, e nunca se roda sem backup.

**Perfis.**

- **PostgreSQL**: `POSTGRES_PASSWORD` e `DATABASE_URL=postgresql://farm:<senha>@postgres:5432/farm` no
  `deploy/.env`, e depois `--profile postgres up -d`. Esperado: `database.dialect: postgres`, `reachable: true`.
- **Ollama**: `--profile ollama up -d`, e depois descomentar o bloco `providers`/`roles` de
  `config.conteiner.yaml`. Baixar o modelo (`exec ollama ollama pull …`) é download real e exige autorização. A
  GPU fica comentada no compose.

### Backup, restauração, rollback (`not_run`)

**Backup com tudo no ar.** Mesma API de backup online do SQLite que o §8 usa:

```powershell
$c = Get-Date -Format yyyyMMdd-HHmmss
docker compose -f deploy/compose.yaml exec central python /app/scripts/sqlite-copia.py /app/data/poc.sqlite3 /app/data/backups/$c/poc.sqlite3
docker compose -f deploy/compose.yaml cp central:/app/data/backups/$c .\backup-conteiner-$c
```

- **No PostgreSQL**, grave dentro do contêiner e copie depois. O `>` do PowerShell recodifica o binário do
  `pg_dump`:

  ```powershell
  docker compose -f deploy/compose.yaml exec postgres pg_dump -U farm -Fc -f /tmp/farm.dump farm
  docker compose -f deploy/compose.yaml cp postgres:/tmp/farm.dump .
  ```

- **A chave do cofre não fica no volume.** No Linux ela é a `CREDENTIALS_MASTER_KEY` do `deploy/.env`: guarde-a
  no gerenciador de senhas. Sem ela, as credenciais do backup não abrem.

**Restauração com o central parado.** A pasta do backup é montada com escrita: a cópia sai em modo WAL, e
numa montagem somente leitura o SQLite pode não conseguir criar o `-shm` para abri-la:

```powershell
docker compose -f deploy/compose.yaml stop central
docker compose -f deploy/compose.yaml run --rm --no-deps --name farm-central-restauracao -v "${PWD}\backup-conteiner-<carimbo>:/restaurar" --entrypoint sh central -c "mkdir -p /app/data/substituido && mv /app/data/poc.sqlite3* /app/data/substituido/; python /app/scripts/sqlite-copia.py /restaurar/poc.sqlite3 /app/data/poc.sqlite3"
docker compose -f deploy/compose.yaml start central
```

O `--name` próprio evita colidir com o nome fixo do contêiner parado. Restaurar regride a cerca dos comandos
(§8).

**Rollback de imagem não é rollback de banco.** A migração roda sozinha na subida (`AppState.__init__`). Não há
migração de descida. A imagem antiga sobre um banco já migrado pela nova é **downgrade de esquema**, e isso não é
rollback simples. O `--pronto` acusa, porque a migração do banco fica à frente da imagem. O rollback que
funciona:

1. Antes de todo `build`/`up` de versão nova, faça o backup acima e guarde a imagem atual:
   `docker image tag farm-central:validacao farm-central:validacao-anterior`.
2. Para voltar: `stop central`, restaure o backup anterior à subida, depois
   `docker image tag farm-central:validacao-anterior farm-central:validacao` e `up -d --no-build`.
3. O que foi gravado depois do backup se perde. É o preço, e ele tem de ser dito antes.

### Levar dados do Windows para o contêiner (`not_run`, exige autorização)

Copiar o banco de uma instalação Windows **não prova** que as credenciais abrem no Linux. A chave de lá é DPAPI,
presa ao usuário e à máquina, e o contêiner não tem DPAPI. A recifragem tem de acontecer **onde o DPAPI abre**,
numa **cópia**:

1. No Windows, tire uma cópia consistente (`scripts/sqlite-copia.py`, §8).
2. Com `POC_DB_PATH` apontando para a cópia e uma `CREDENTIALS_MASTER_KEY` nova no ambiente da rodada, rode
   `python -m app.security.rekey --conferir` e depois `--aplicar`. A chave antiga é a do DPAPI local, lida de
   `data/credentials.key` do ambiente central. Por isso a rodada toca segredo real e exige autorização.
3. Leve a cópia para o volume, com o procedimento de restauração acima, e ponha a mesma chave no `deploy/.env`.
4. Prova: nem `secret_store_locked` nem `secret_store_foreign_key` no `/api/health`.

Dono das etapas: validar ao lado do ambiente central exige `OWNER_ID` diferente, e **nunca** apontar o contêiner para o
banco do ambiente central. Substituir o central do Windows pelo contêiner exige `OWNER_ID` igual ao hostname antigo, com
o antigo **parado antes**: nunca os dois no ar.

### Limites conhecidos

- **`status: error` permanente** por `sdk_missing`, mesmo sem nenhum aparelho local. Correção sugerida, fora desta
  frente: em `state.health()`, não tratar `sdk_missing` como duro quando `instances.count == len(external)`.
- **Worker remoto não chega.** Seria preciso estender `endereco_de_escuta` ao socket do canal e publicar
  `127.0.0.1:8010:8010`, apontando o `-R` do túnel para lá. Não foi feito.
- **Catálogo de APK vindo do Windows.** `releases.catalog_dir` foi gravado com `\` (`relative_to` no Windows), e
  no Linux `cfg.path(row["catalog_dir"])` não resolve esse caminho. Um banco com releases precisa de
  normalização. Não testado.
- **Ollama nativo do host** por `http://host.docker.internal:11434/v1`: não verificado.
- **A imagem leva `pytest`**: o lock é um só (`requirements.txt`) e não foi dividido.

## 15. Aviso fora do painel (Telegram, item 28.11)

O aviso externo é o **espelho** da caixa de Pendências (`#/pendencias`, ADR-062): uma mensagem curta por pendência nova
(aprovação de persona, execução que parou pedindo informação, conta que pede intervenção, o `pedido.aviso` e, desde o
28.14, o conhecimento do Livro que entrou na espera da pessoa: `learning.needs_person`, só a faixa C por padrão, com
`avisos.aprendizado_faixas: [B, C]` para incluir a faixa B, que é aprovação em lote). A mensagem leva só o **tipo** e o link da caixa: nunca nome de persona, conta, conteúdo de mensagem ou
dado de terceiro. É só saída (sem webhook, sem rota de entrada). Desligado de fábrica (`avisos.enabled: false`). Só o
líder da trava `avisos` envia, e a fila durável (`avisos_entregas`, migração 068) deduplica por fato: o mesmo evento nunca
vira duas mensagens (chave comum `<família>:<fato>`: `approval`, `run`, `session`, `pedido`, `learning`), e um envio interrompido por queda vira `incerto` e **não** é reenviado.

**Lote de teste e rajada (28.19).** Em 04/10 o dono recebeu 11 avisos seguidos de um lote de medida de uma frente.
Desde então:
- **Execução do sistema não avisa.** São três casos: prova de fluxo, validação do QA e lote de frente. Quem dispara
  um lote de teste, medida ou validação pela API manda `idempotency_key: "lote:<frente>:<id>"` no `POST /api/commands`.
  São 8 a 120 caracteres, só `A-Za-z0-9_.:-`, um id por execução. A regra está em `contracts/origem.e_execucao_do_sistema`.
  Pedido do dono pelo painel, pelo Telegram ou pelo Trello segue avisando.
  A APROVAÇÃO que um lote abre segue avisando, porque só o dono decide. Na prova e na validação ela segue calada (30.37).
- **A rajada sai agrupada.**
  - O primeiro aviso de um tipo sai na hora.
  - Os do mesmo tipo que chegam até `avisos.agrupar_s` (60 s) depois esperam o fim da janela. Com
    `avisos.agrupar_a_partir_de` (3) ou mais, saem como UMA mensagem com a contagem ("10 execuções pararam pedindo
    informação"). Com menos, saem um a um.
  - Uma rajada de 11 vira 2 mensagens, e dois avisos seguidos do dono continuam dois. Nenhum aviso espera mais que a
    janela.
  - O agrupado não leva conteúdo, e responder a ele (reply) não decide nada: cada item se abre na caixa.
  - `agrupar_s: 0` volta ao comportamento anterior.

Procedimento (o dono faz; sem ele a prova real fica `not_run`):

1. No Telegram, fale com **@BotFather** → `/newbot` → escolha o nome e o username (termina em `bot`). Ele devolve o token.
2. Cole `TELEGRAM_BOT_TOKEN=<token>` no `.env` do central (`C:\git\android\.env`, fora do Git) e reinicie a tarefa
   `farm-central` (`Stop-ScheduledTask farm-central; Start-ScheduledTask farm-central`): o `.env` é lido só na partida.
   O cofre DPAPI não é usado: ele guarda credencial de conta, e este token é configuração do ambiente, como as chaves de IA.
3. No Telegram, abra a conversa PRIVADA com o seu bot e mande `/start`. Não use grupo: a conversa de volta (§15.1) só
   aceita o dono em conversa privada, e num grupo qualquer membro escreveria pela Central.
4. Rode a descoberta e confirme qual `id` é o seu chat (ela só lê; pode repetir):
   `backend\.venv\Scripts\python.exe scripts\avisos-telegram.py descobrir`
   A saída lista `id`, `tipo`, `nome` e `@usuario` de cada chat; o token não aparece.
5. Grave `TELEGRAM_CHAT_ID=<id>` no mesmo `.env` e reinicie a `farm-central`. O `id` é o da linha de `tipo` `private`
   (o seu); um `id` negativo é de grupo ou canal e não serve.
6. Ligue `avisos.enabled: true` em `config/config.yaml` (opcional: `avisos.url_painel: http://<ip-do-central>:8000` para o
   link; sem ela a mensagem vai só com o texto) e reinicie a `farm-central`. Teste com
   `backend\.venv\Scripts\python.exe scripts\avisos-telegram.py testar` (manda uma mensagem de teste real). Confira
   que `GET /api/health` não traz o problema `avisos_sem_segredo`.

Se o canal estiver ligado e faltar `TELEGRAM_BOT_TOKEN` ou `TELEGRAM_CHAT_ID`, a saúde acusa `avisos_sem_segredo` e nada é
enviado. Estados da fila para diagnóstico: `SELECT estado, COUNT(*) FROM avisos_entregas GROUP BY estado` (`falhou` e
`incerto` trazem o `ultimo_erro`, já sem segredo). O que o backend perde por estar parado não é reenviado na partida: a
caixa do painel é a fonte da verdade.

### 15.1 A conversa de volta (item 28.15, ADR-071)

O mesmo bot também RECEBE: o chat do `TELEGRAM_CHAT_ID` aprova, veta, responde à pergunta de uma execução e faz pedidos.
A Central trata isso como o painel trata. A gramática fechada está em [design/canais-externos.md](design/canais-externos.md)
§2, e a `/ajuda` do bot a repete. O detalhe técnico está no ADR-071.

Quem fala no bot é a **ANA** (28.17): os avisos começam com `ANA: `, a primeira mensagem traz a apresentação inteira,
e "quem é você?" (ou `/quem`) responde que é a ANA e que é uma IA, sem virar pedido.

- Vem **desligada** (`avisos.entrada.enabled: false`) e só liga com `avisos.enabled`. O token, o chat e a trava `avisos`
  são os do aviso.
- Ligar troca o consumidor do bot: só um processo pode ler o `getUpdates`. Enquanto outro lê (a caixa provisória da
  orquestradora, uma segunda réplica), a Central recebe 409, espera `espera_conflito_s` e mostra o problema
  `telegram_entrada_conflito` em `/api/health`. Ela não disputa. **Ligar só com o "vai" da orquestradora**, que para a
  caixa dela no mesmo momento.
- Com a entrada ligada, o aviso de aprovação leva o resumo, o alvo e o texto, e o de pergunta leva a pergunta. Os dois
  vão redigidos e cortados em 500 caracteres, para o dono responder ali mesmo (decisão (d)).
- A mensagem com cara de senha ou código não é guardada: a Central a apaga do chat e responde sem ecoar nada. Se o
  Telegram não deixar apagar, a resposta pede ao dono que apague.
- A resposta a uma execução que pergunta por senha, código, 2FA ou token também é recusada, qualquer que seja a forma do
  texto (decide pelo contexto, com o vocabulário da triagem de credencial): não é guardada, é apagada do chat, e a
  resposta orienta o dono: a senha se grava na conta da persona, e o código de verificação se digita no aparelho. Vale
  para o reply ao aviso e para `/responder <id> <texto>`.
- Enquanto uma execução espera senha, código, 2FA ou token, um texto curto (até 3 palavras, como "kiwi2024!" ou
  "kiwi 2024") também é recusado, apagado do chat e nunca gravado: como texto livre, como recado à orquestradora (`/orq`
  ou reply a mensagem que a Central não mandou) e como `/responder` sem id. A resposta pede o pedido com mais detalhe;
  uma frase segue como pedido, com a prévia.
- Só vale o dono em conversa PRIVADA: `chat.type = private` e `from.id` igual ao `TELEGRAM_CHAT_ID`. Grupo, canal ou
  outro membro ficam gravados sem texto e sem resposta.
- Na primeira subida (canal sem nenhuma linha) o que o Telegram guardou antes (até 24 h) é descartado, não tratado: um
  "/aprovar" ou um "sim" antigo não executa. Mande o primeiro comando depois de ver a `/ajuda`.
- Em TODA subida, a mensagem escrita há mais de `avisos.entrada.idade_max_s` (900 s) não é tratada (a Central estava
  fora e o Telegram guardou): fica `ignorada`, sem texto, e o dono recebe uma mensagem só dizendo quantas foram e que
  nada foi aprovado, vetado nem executado por elas. A senha antiga ainda é apagada do chat.
- O botão Executar vale por `avisos.entrada.ttl_previa_s` (900 s); passado o prazo a Central pede o pedido de novo. Uma
  linha presa em `executando` sem execução (queda no meio) vira `falhou` depois de 5 min, e o dono é avisado.
- O 429 do `getUpdates` espera o `Retry-After`. O 401, 403 e 404 viram o problema `telegram_entrada_recusada` na saúde,
  com a causa de cada um (401: token revogado ou trocado; 403: bot bloqueado ou removido da conversa; 404: token
  malformado ou de bot que não existe mais). O 400 vira `telegram_entrada_pedido_invalido`: não é o token, veja o log.
  Todos esperam `espera_conflito_s`, como o 409; corrigida a causa, o problema some sozinho.
- **Trocar de chat ou de bot** pede limpar o registro do canal antes: `DELETE FROM canal_entradas WHERE canal='telegram'`
  e `DELETE FROM canal_enviadas WHERE canal='telegram'`. Sem isso o offset antigo (de outro bot) e os `message_id` de
  outro chat ficam valendo; com a limpeza, a próxima subida descarta o histórico de novo.
- O registro fica em `canal_entradas` e `canal_enviadas` (migração 085), com o texto só do que veio do dono e foi aceito.
  Contagem por estado: `SELECT estado, COUNT(*) FROM canal_entradas WHERE canal='telegram' GROUP BY estado`.

**Retenção (item 28.16).** A conversa não fica guardada para sempre. A faxina corre de hora em hora no líder da
trava `avisos`, mesmo com o aviso desligado. Para o que tem mais de `avisos.entrada.retencao_dias` (Telegram, 30 de
fábrica) ou `trello.retencao_dias` (Trello, 30), ela:
- primeiro ZERA o texto, a prévia, a resposta e o erro, e depois apaga a linha;
- deixa de pé a linha que ainda espera alguém (`recebida`, `pergunta`, `executando`), sem o texto, e a mais nova de
  cada canal (o offset do Telegram);
- no Trello, esquece também os `trello_cartoes` já arquivados.

O mínimo é 2 dias, para passar da janela em que o canal repete uma entrega e o dedupe continuar valendo. O log diz
`canais: faxina do <canal> (…)`. Um reply a um aviso mais velho que o prazo vira recado para a orquestradora, nunca
ação.

Prova real (o dono faz com a sessão Canais; sem ela fica `not_run`):
1. Ligar `avisos.entrada.enabled: true` no `config/config.yaml`, com backup antes, e reiniciar a tarefa `farm-central`.
2. Mandar `/status` e depois `/pendencias` ao bot. Cada um tem de responder na thread, e a linha correspondente em
   `canal_entradas` fica `feita`.
3. Responder "não" a um aviso de aprovação de teste. Conferir no painel a aprovação vetada com `decided_by =
   telegram:dono`.
4. `/para android-09 abra o QA Messenger`: a prévia sai com os botões. Tocar Executar cria uma execução, e o desfecho
   volta na thread.
5. Mandar `123456`: a mensagem some do chat, a resposta não ecoa nada, e a linha fica `recusada`, com `texto` NULL.

**Quem não é o dono (item 28.18, emenda ao ADR-071; regras C-08 a C-11 de [dominios/canais.md](dominios/canais.md)).**
Desligado de fábrica (`avisos.entrada.convidados.enabled`), e só vale com a entrada ligada. Ligado:
- Num chat privado novo, sai a apresentação da ANA e a pergunta do nome, uma vez. A resposta seguinte é o nome.
- O nome vai ao dono num aviso ("alguém novo quer falar pelo Telegram"). **Responda a esse aviso com sim ou não.**
  O sim autoriza a pessoa; o não a deixa em silêncio. Responder de novo ao mesmo aviso muda a decisão.
- O convidado autorizado tem `/ajuda` e `/status` (só contagens: sem aparelho, pendência, persona ou conta). Fora isso,
  cada mensagem vira um aviso ao dono (até `avisos_por_hora` por convidado), e ele recebe "recebido; passei ao dono".
  Convidado nunca executa nem decide, e responder ao aviso da mensagem dele não chega a ele nem vira pedido.
- Grupos e canais são ignorados. Pôr ou tirar o bot de um grupo avisa o dono, sem o nome do grupo.
- O histórico fica no banco, nunca no Trello nem no Git. Para conferir:
  - `SELECT chat_id, estado, nome_informado, primeira_em, ultima_em FROM canal_contatos`;
  - os eventos de um chat estão em `canal_contato_eventos`.
- Prova real (`not_run` até ligar): um chat de teste do dono fala com o bot, recebe a pergunta do nome, o dono responde
  sim ao aviso, e o `/status` do convidado sai só com as contagens.

## 16. Trello (item 32.2, ADR-072)

O Trello do dono é espelho do que espera por ele e canal de veto e de resposta. Detalhe técnico e regras em
[design/trello-integracao.md](design/trello-integracao.md) e no ADR-072; as regras do dono sobre os canais, em
[dominios/canais.md](dominios/canais.md). Tudo vem **desligado**.

**Segredos (só no `.env`, fora do Git):** `TRELLO_API_KEY` e `TRELLO_TOKEN` (a chave do Power-Up e o token do dono) para o
espelho e a leitura; `TRELLO_API_SECRET` (o segredo do aplicativo, que assina o webhook) só para o webhook. Faltando um, a
saúde acusa `trello_sem_segredo` ou `trello_webhook_sem_segredo`. Nunca vão na URL: o cliente os põe no cabeçalho
`Authorization`.

**Config (`trello:`, no `config/config.yaml`, com backup antes):** `enabled`, `quadros`, `listas` (`central_automatico`,
`aprovado`, `vetado`, `marcos`, `custos`), `membro_dono` (o id do dono: só ele comanda), `reconciliar_s` (60; 300 com o
webhook), `comando_livre` (false), `membros_autorizados` (vazia), `responder_convidados` (false) e `webhook`. O exemplo está
comentado em `config/config.example.yaml`.

**O que o dono pode esperar:**
- Mover o cartão de aprovação para ⛔ Vetado, "não" ou `/vetar` veta. Mover para ✅ Aprovado, "sim" ou `/aprovar` **não
  aprovam**: a Central comenta que a aprovação se confirma no painel ou no Telegram.
- Comentário que começa com 🤖 é de IA e nunca é pedido. A Central assina `🤖 ANA · HH:MMZ ·`.
- O link do painel no cartão leva só um id de formato permitido (32.4: execução, pedido, `receita:<n>`, `fluxo:<hex>`
  ou `fluxo:f<n>`). Qualquer outro, como o slug do fluxo, não vai no link, e o link abre só a tela.
- Senha ou código no comentário é recusado sem eco, e a resposta pede ao dono que apague o comentário.
- Convidado não executa nada; o pedido dele vira um aviso ao dono (Telegram).

**Rollout do webhook (nesta ordem; cada passo é seu próprio "vai"). Ligar uma flag nunca vale como "vai":**
1. **Ligar a rota:** com o 32.2 implantado, o portal no ar (ADR-073), `TRELLO_API_SECRET` no `.env` e `trello.webhook.callback_url`
   igual à URL pública (`https://dev.nvit.com.br/api/canais/trello/webhook`; sem parâmetro, sem credencial), pôr
   `trello.webhook.enabled: true` e reiniciar a tarefa `farm-central`. Isto só faz a rota responder (`HEAD` 200, `POST`
   verifica a assinatura). **Não cadastra nada no Trello.** O ingress do túnel não muda: encaminha o hostname inteiro, e a
   regra `^/api/worker/` segue antes dela.
2. **Ensaio:** `backend\.venv\Scripts\python.exe scripts\trello-webhook.py --ensaio` lê o Trello e mostra o que seria
   feito (`criar` por quadro). Nada é escrito. Confira os quadros e a URL.
3. **Cadastro, com o "vai" da orquestradora:** `backend\.venv\Scripts\python.exe scripts\trello-webhook.py --aplicar`. O
   Trello faz um HEAD na URL antes de criar o webhook; sem o 200, ele não nasce.
4. **Prova de fora:** de fora da LAN, `HEAD https://dev.nvit.com.br/api/canais/trello/webhook` dá 200 e um `POST` sem
   assinatura dá 401, sem corpo; o resto de `/api/` segue 401 (use `.claude/handoffs/portal/prova-de-fora.sh depois`). Comente
   `/status` num cartão: a resposta tem de chegar em segundos (antes, em `reconciliar_s`), e `canal_entradas` mostra a linha
   `feita`. **Não chame a rota de login do endereço público em teste** (a tranca é por cliente desde o 29.56, mas tranca o IP de quem testa).
5. **Só então** `trello.webhook.cadastro_automatico: true` (recadastro de hora em hora no líder, que recria o webhook
   desativado) e a `reconciliar_s` em 300. Desligar a rota (`enabled: false`) NÃO apaga o webhook do Trello; remover é o pedido
   explícito `scripts\trello-webhook.py --desligar`.

**Saúde e diagnóstico:**
- `trello_recusado` (401/403): confira `TRELLO_API_KEY` e `TRELLO_TOKEN`; vale para o espelho, a leitura e o cadastro.
- `trello_leitor_atrasado`: a leitura das actions parou há mais de 3 × `reconciliar_s`; veja o líder da trava `avisos`.
- `trello_webhook_assinatura_invalida`: 5 ou mais em 10 min. O `TRELLO_API_SECRET` mudou (regerado no aplicativo), a
  `callback_url` difere da cadastrada, ou há sondagem. Nada foi gravado. Enquanto isso a reconciliação cobre.
- `trello_webhook_inativo` (só com `cadastro_automatico`): o cadastro falhou ou o Trello desativou o webhook. A causa
  comum é a URL fora do ar no momento do HEAD do Trello.
- Contagem por estado: `SELECT estado, COUNT(*) FROM canal_entradas WHERE canal='trello' GROUP BY estado`. Avisos do
  webhook ainda não relidos ficam em `aviso`.
- Recuo em uma linha: `trello.enabled: false` (e `trello.webhook.enabled: false`) e reiniciar a tarefa; para tirar o webhook do
  Trello, `scripts\trello-webhook.py --desligar`.

`simulated`: `backend/tests/test_trello_*.py`. `not_run`: tudo o que fala com o Trello de verdade.

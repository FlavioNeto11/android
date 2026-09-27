# Operação — runbook

> Fonte principal para instalar, configurar, desenvolver, testar, implantar e diagnosticar. Para arquitetura,
> [docs/arquitetura.md](arquitetura.md); para banco/worker, [docs/banco.md](banco.md) e [docs/worker.md](worker.md);
> para custo de IA, [docs/ia.md](ia.md). As notas datadas usadas aqui vêm de registros de sessões anteriores
> (memória do operador) — tratadas como evidência datada, não como instrução; nomes de conta e do operador foram
> omitidos de propósito.

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
pwsh -File scripts\start.ps1                  # backend + Appium + painel em 127.0.0.1:8000
pwsh -File scripts\start.ps1 -Dev              # + Vite com hot reload em 127.0.0.1:5173 (proxy para o backend)
pwsh -File scripts\start.ps1 -Simulated        # MODO SIMULADO (sem IA; regras fixas para o app de QA)
```

`-Dev` sobe o Vite separado (proxy de `/api` e `/ws` para o backend); sem `-Dev` o frontend precisa estar
buildado (`npm run build`) para `start.ps1` servir `frontend/dist/index.html`
(`scripts/start.ps1:76`). `-Simulated` força `AI_PROVIDER=simulated`, com aviso no console.

## 4. Testes

```powershell
cd backend; .venv\Scripts\python.exe -m pytest -q          # SQLite (padrão), ~11 min
# com TEST_DATABASE_URL=postgresql://...  , a mesma suíte roda contra PostgreSQL, ~14 min
cd frontend; npm run typecheck && npm test
cd backend; .venv\Scripts\python.exe -m pytest -q ..\scripts\tests   # lógica pura dos scripts, sem tocar o parque
```

Regras de ritmo (pedidas explicitamente pelo dono; ver ADR-021 em `docs/decisoes.md` e `CLAUDE.md` §
Convenções): durante o trabalho, rodar só o arquivo ou o `-k` afetado; a suíte inteira fica **só para antes do
commit**, e roda **em segundo plano** — nunca ficar ocioso esperando. O harness de teste isola-se do parque real
por porta: `backend/tests/conftest.py:48` fixa `base_console_port: 5640` (o padrão de produção é 5554,
`config.py:184`), então a suíte nunca endereça um emulador real do parque, mesmo rodando na mesma máquina.

## 5. CI

`.github/workflows/ci.yml` — **6 jobs** (até 24/09 eram 5, e o cabeçalho do arquivo dizia 4):

| Job | Quando | O que faz |
|---|---|---|
| `backend-sqlite` | todo push/PR | `pytest -q` contra SQLite |
| `backend-postgres` | `schedule` (diário, 05:17 UTC) ou `workflow_dispatch` | `pytest -q` contra `postgres:17` de serviço, `TEST_DATABASE_URL` |
| `frontend` | todo push/PR | `npm run typecheck` + `npm test` |
| `dependencias` | todo push/PR + diário | `pip-audit --strict` (backend + worker) e `npm audit --audit-level=high` (frontend, Appium) |
| `worker-agent-smoke` | todo push/PR | instala só `worker-requirements.txt` e importa `app.worker.agent` — prova que o agente continua leve |
| `docs` | todo push/PR | `python scripts/docs-check.py` + testes puros de `scripts/tests` (docs-check e livro-razão do plano-100) |

**Lacunas conhecidas:** dos testes de `scripts/tests` só os puros têm job (o `npm run build` entrou no job do painel em 27/09, B7) — os
demais chamam `pwsh` com caminhos do Windows e rodam só localmente (`backend\.venv\Scripts\python.exe -m pytest -q scripts/tests`).

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
  mais `config/` e a chave do cofre.
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
- `GET /api/diagnostics` (`backend/app/api.py:243`) — o mesmo relatório do `diagnose.ps1` mais o que só o
  backend sabe (capacidade medida, ferramentas).

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

## 12. Tabela de scripts por risco

`[S]` seguro (só leitura ou sandbox) · `[T]` gasta chamada de API paga · `[P]` toca o parque/produção/sistema ·
`[D]` simula sem exigir flag explícita.

| Script | Risco | O que faz |
|---|---|---|
| `diagnose.ps1` | S | Diagnóstico do host, não altera nada |
| `install-prereqs.ps1` | P | Instala Android SDK + Appium; aceita licenças em nome do usuário |
| `start.ps1` / `stop.ps1` | P | Sobe/derruba o backend, Appium e (opcional) emuladores do projeto |
| `backup.ps1` | S | Cópia consistente do banco+config, sem parar nada |
| `restore.ps1` (sem `-Confirmar`) | S | Ensaio em pasta limpa |
| `restore.ps1 -Confirmar` | P | Substitui `data/` de verdade, exige backend parado |
| `deploy.ps1` | P | Para → copia banco → sobe → confere; mexe na tarefa `farm-central` |
| `eval-run.ps1` (sem `-Yes`) | S | Só imprime o plano da bateria; nenhuma conexão, nenhum adb (26/09: antes, mesmo "simulado" fazia POST no backend vivo e rodava adb) |
| `eval-run.ps1 -Yes` | P/T | POST no backend vivo e adb nos aparelhos, mesmo com provedor simulado; com provedor real gasta API |
| `bench.py` (`simulado`, padrão) | S | Harness: aparelho falso, provedor simulado, banco temporário. Contagens com prova `simulated` ([`relatorio-desempenho.md`](relatorio-desempenho.md)) |
| `bench.py leitura` | S | Só GET em loopback. A primeira GET do Diagnóstico depois de reiniciar coleta as versões das ferramentas do host (`emulator -accel-check`, `adb version`) |
| `bench.py comparar` | S | Antes × depois, com limite e amostra mínima declarados na linha de base; sem isso, o veredito é "exploratório" |
| `eval-rejudge.ps1 -Yes` | T | Rejulga capturas com o modelo caro |
| `probe-models.py --yes` | T | Sonda modelos configurados (poucos centavos) |
| `probe-image.ps1` | S | AVD **temporário**, removido ao final; não toca instâncias do projeto |
| `rotation-test.ps1` | P/T | Liga/hiberna instâncias reais; gasta IA se não estiver em modo simulado |
| `scale-test.ps1` | P | Liga instâncias reais até o hardware não sustentar |
| `recuperar-parque.ps1` | P | Reinicia aparelhos remotos pelo worker |
| `worker-install.ps1` / `worker-agent.ps1 -Instalar` | P | Instala/registra o agente numa máquina worker |
| `install-central-service.ps1` | P | Registra o backend do central como tarefa supervisionada |
| `worker-tunnel.ps1` | P | Sobe/mantém o túnel SSH real |
| `usage-report.ps1` | S | Só lê `ai_calls`, não chama provedor |
| `demo-run.ps1` | D/T | Envia comando real ao backend (gasta IA se o provedor não for simulado) |
| `aceites-remotos.ps1` (sem `-Yes`) | S | Só mostra o roteiro |
| `aceites-remotos.ps1 -Yes` | P | Despacha comandos reais no parque |
| `test-restart-recovery.ps1` | P | Reinicia o backend com fila carregada, real |
| `personas_criar.py` / `personas_completar.py` | P | Escreve personas reais no banco de produção |

## 13. Incidentes conhecidos → sintoma → causa → ação

| Sintoma | Causa | Ação |
|---|---|---|
| `config/config.yaml` some depois de um checkout | Arquivo deixou de ser rastreado entre commits; git apaga da árvore ao trocar de commit | Restaurar de `data/backups/<carimbo>/config/`; `start.ps1` já recusa subir nesse estado (§2) |
| `data/poc.sqlite3` "malformed database schema" após reboot | Um `-wal` velho ao lado de um banco recopiado | `scripts/restore.ps1 -De <backup> -Confirmar` com backend parado; nunca copiar o `.sqlite3` por cima à mão |
| Cerca (`commands.fence`) regredida depois de restaurar o banco | `fence` é MAX+1 por aparelho; restaurar volta o contador | Subir o `fence` do aparelho no SQLite até o valor que o agente citou na recusa; reemitir o comando |
| Agente do worker não volta depois do boot do notebook | Tarefa agendada registrada sem gatilho de boot (script antigo) | Reinstalar com `scripts/worker-agent.ps1 -Instalar` (gera a tarefa com `AtStartup`) |
| Notebook do worker lento, emuladores com carga alta sem motivo aparente | Escalonador do Hyper-V no modo "core" em vez de "classic" | Conferir o evento `Hyper-V-Hypervisor` id 2 (precisa ser `0x2`, não `0x3`); `bcdedit /set hypervisorschedulertype classic` e reiniciar |
| Conta do provedor de IA sem crédito, execuções travam sem aviso claro | Conta esgotada (HTTP 402/billing) | `/api/health` acusa `ai_billing`; o disjuntor (`executor.py`, §6 de `docs/ia.md`) represa sem gastar tentativa |
| `decide` volta a usar Anthropic mesmo com Ollama configurado | Serviço Ollama fora do ar no host (sobe por login de usuário, não é tarefa de boot) | Conferir se o Ollama está no ar; sem ele, o fallback explícito assume — comportamento esperado, não bug |

## 14. Contêineres: o central em desenvolvimento e validação

> **Estado da prova (26/09/2026).** `simulated`: `backend/tests/test_conteiner_central.py` (11 testes; o
> `AppState` sobe com `deploy/config.conteiner.yaml`, responde como a Farm e passa no healthcheck da imagem) e
> `scripts/tests/test_conteineres.py` (21 testes de leitura do compose, do Dockerfile e do `.dockerignore`).
> `not_run`: `docker build`, `docker compose up`, persistência, backup, restauração, rollback e os perfis
> `postgres`/`ollama`. O engine está parado nesta máquina, e ligar o Docker Desktop liga o WSL, o que exige
> autorização do dono. Nenhum ganho de densidade é afirmado.

**O que é.** Uma instância do central (backend + painel compilado) numa imagem reproduzível, para desenvolver e
validar isolado da produção, que continua no Windows (§6). **O que não é:** microserviço, réplica, nem lugar de
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
- **Porta só em `127.0.0.1:8100` do host.** A 8000 do host é da Farm de produção e do `cartorio-api-1` (§6). O
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
   `data/credentials.key` da produção. Por isso a rodada toca segredo de produção e exige autorização.
3. Leve a cópia para o volume, com o procedimento de restauração acima, e ponha a mesma chave no `deploy/.env`.
4. Prova: nem `secret_store_locked` nem `secret_store_foreign_key` no `/api/health`.

Dono das etapas: validar ao lado da produção exige `OWNER_ID` diferente, e **nunca** apontar o contêiner para o
banco de produção. Substituir o central do Windows pelo contêiner exige `OWNER_ID` igual ao hostname antigo, com
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

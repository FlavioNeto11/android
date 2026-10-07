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

**Desde 05/10/2026 também não roda em pull request** (29.102, decisão da orquestradora): cada PR custava de 62 a 115 min
serial no runner do central, que é a máquina das suítes e das medidas de latência, e o `[skip ci]` na ponta falhou
três vezes no mesmo dia. Os PRs são cobertos pelo funil da suíte. O que restou para a corrida diária voltar a ser rede:

- **Porta do runner:** o job `porta` olha se o runner já tem um `pytest` vivo; na corrida agendada, com um vivo, os
  jobs do central são pulados com um aviso (`::warning::`). O disparo manual roda sempre. Em 05/10 o SQLite do cron
  disputou a máquina com a suíte 35 e caiu no limite de 60 min.
  - **Rede parcial:** quando a porta pula, o job de PostgreSQL, hospedado na GitHub, roda assim mesmo.
  - **Órfão:** `pytest` vivo há mais de 6 h não conta e sai como `::error::` com o PID (encerre-o). O `::error::` não
    reprova nada: o sinal fica só na página do run. O corte de 6 h vale porque nenhuma medida ou suíte nossa dura isso
    (a maior é o funil inteiro, perto de 1 h 50; o PG, 30 a 50 min; as baterias, até 15 min). Uma medida que um dia
    passe de 6 h tem de subir o corte, senão o CI roda junto com ela (K-101).
  - **48 h:** a porta grava a hora de cada corrida de verdade em `farm-porta-ultima-corrida.txt`, na pasta de trabalho
    do runner. Pulando com mais de 48 h desde a última, ela reprova e o run fica vermelho, em vez de pular calado.
    - A marca quer dizer "a corrida INICIOU", não "terminou bem".
    - O disparo manual só-PG (`somente_postgres`) não grava a marca, porque não roda nada no central.
    - Marca ilegível é regravada com a hora de agora, com `::warning::`. Como na pasta limpa (abaixo), isso também
      pode atrasar o vermelho em até 48 h: o aviso no run é o único sinal até lá.
    - Se a pasta `_work` do runner for limpa, a primeira pulada regrava a marca com a hora de agora: nunca reprova à
      toa, mas pode atrasar o vermelho em até 48 h.
  - **Premissa:** a porta lê o `CommandLine` dos processos, que para processo de outro usuário só vem a quem está
    elevado. O runner roda como Administrator, nível Highest (tarefa `farm-ci-runner`), e as suítes e medidas também:
    hoje ela vê todos. Runner sem elevação veria zero e nunca pularia. A forma robusta seria uma trava de PID num
    caminho fixo, gravada pelo funil e pelas medidas; ficou de fora porque cada sessão roda os seus scripts.
- **Catraca do mypy:** o código novo (`app.contracts`, `app.modules`, `app.shared`) nasceu com zero erro e derivou até
  254 no cron de 05/10 (257 na base do 29.102, depois da suíte 35); o job reprovava toda noite. Agora `scripts/mypy-catraca.py` reprova só se a contagem passa do teto em
  `backend/mypy-teto.txt`; quem baixa a contagem baixa o teto no mesmo commit. Sem o `pull_request`, o cron só a veria
  DEPOIS do merge e reprovaria para todos sem dizer quem subiu: por isso ela roda também no funil, junto das catracas,
  e no dirigido de quem toca `backend/app` (`.claude/rules/testes.md`). Leva ~1 min. O teto é do Windows, a plataforma
  do runner: o mypy avalia os ramos de `sys.platform`, e em Linux a contagem pode ser outra.
- **O Python do mypy (29.144):** o mypy mora num Python à parte, num caminho fixo fora do Git, e não no venv do backend.
  No central é `C:\farm\ferramentas\mypy`, um venv do Python 3.13 com só os pinos do mypy do `backend/requirements-dev.txt`
  (mypy, mypy-extensions, librt, ast-serialize, pathspec, typing-extensions). Medido em 05/10: 257 = teto, em 34 s.
  - Uso: `$env:MYPY_PYTHON='C:\farm\ferramentas\mypy\Scripts\python.exe'; python scripts\mypy-catraca.py` (ou
    `--python <python.exe>`). O script passa `--python-executable` com o venv do backend e põe o site-packages dele no
    `PYTHONPATH`, porque o plugin `pydantic.mypy` do `backend/mypy.ini` é importado pelo Python do mypy.
  - Os dois Pythons precisam ter a mesma versão (3.13). Com versões diferentes, o import do plugin quebra e a catraca
    reprova por contagem desconhecida.
  - Recriar, quando o pino mudar: `python -m venv C:\farm\ferramentas\mypy` e
    `C:\farm\ferramentas\mypy\Scripts\python.exe -m pip install mypy==<pino> ...`, com os mesmos pinos do
    `requirements-dev.txt`.
  - Sem a variável nem a opção, vale o caminho de antes: o mypy no Python que roda a catraca.
- **docs-check em clone limpo:** `.claude/handoff-current.md` entrou no `.gitignore` versionado (estava só no
  `.git/info/exclude`, que não vem num clone).
- **Testes que dependiam do host:** `test_pausa_de_reparo` compara a saúde antes e depois da pausa, não um valor
  absoluto (hermético). `test_backup::test_copia_a_frio_...` NÃO ficou hermético: roda só no Windows (a lib de cópia
  de AVD é do Windows) e pula no Linux.
- **"Nada dispara em push" não é literal:** o `conteiner.yml` roda em push da `main` (só dela, e tag não dispara; 29.157) que toque
  `deploy/**`, `.dockerignore`, `backend/requirements.txt`, `backend/app/main.py` ou `frontend/package*.json`, no disparo manual e no cron
  de segunda. Push de branch de frente não dispara mais (segundo a leitura da Frente DevOps em 06/10, 7 das 8 últimas corridas eram de branches e gastavam a cota hospedada). Ele é
  hospedado e não ocupa o central.
- **Actions fixadas por SHA e Dependabot (29.157):** `actions/checkout`, `setup-python`, `setup-node` e `cache` (nos workflows e nas ações compostas de `.github/actions`) apontam para o SHA do commit, com a versão no comentário (`# v4.4.0`), para que uma tag movida não mude o que roda no CI. `.github/dependabot.yml` olha só `github-actions`, uma vez por mês, num PR único; o PR dele é ACHADO (a frente GitHub confere, ninguém mescla sozinho). Não cobre pip nem npm. Para atualizar à mão: `gh api repos/actions/<nome>/commits/<tag> --jq .sha` e trocar SHA e comentário juntos.
- **Secret scan semanal (29.158):** `.github/workflows/secret-scan.yml`, em runner hospedado, manual e às segundas, não bloqueia push. Roda o gitleaks (imagem v8.30.1 fixada por digest) no histórico inteiro com `--redact`; o log do gitleaks é descartado e só `scripts/secret_scan_resumo.py` escreve: regra, caminho, linha e hash curto, nunca valor, contexto, autor ou e-mail (relatório sem redação = nada impresso e o job falha). Achado vira UMA issue `achado` (ou comentário na aberta) e quer dizer segredo vazado: trocar. Falso positivo entra em `.gitleaks.toml` por caminho ou regra, nunca pelo valor. Disparo: `gh workflow run secret-scan.yml` (só depois de o arquivo estar na `main`).
- **Limpeza das branches de revisão (29.155, C18):** `.github/workflows/limpa-branches-revisao.yml`, em runner hospedado, uma vez por dia (08:11Z) apaga no GitHub as branches `revisao/*` que já estão na `main`, já tiveram PR, não têm PR aberto e têm a ponta de mais de 6 horas, no máximo 20 por execução (`scripts/limpar_branches_revisao.py`; qualquer dúvida deixa a branch). Só esse prefixo; a `main` e as branches das frentes nunca. Disparo manual é ensaio, e só apaga com `aplicar=true`; o agendado só apaga com a variável do repositório `LIMPEZA_APLICAR=true`.
- **Conferência do resultado antes do `aplicar` (29.190):** `python scripts/resultado_confere.py <resultado>.json` (ou `--gravar RASCUNHO DESTINO`, que só grava se estiver certo) recusa status fora de `implemented`/`partial`/`blocked`, `proof` fora de `real`/`simulated`/`not_run` (a descrição vai em `evidence`), ID que não está no plano, pedido sem linha e prova sem os campos do nível (real: data, máquina, commit ou id de execução; simulated: `arquivo::teste`; not_run: motivo). Rode antes de mandar o JSON à orquestradora.
- **Aviso quando o cron falha (29.155, C2):** `.github/workflows/ci-aviso-de-falha.yml`, em runner hospedado, abre UMA issue por
  noite quando o `schedule` do CI termina em `failure`, `cancelled`, `timed_out` ou `startup_failure`. Título "CI noturno
  AAAA-MM-DD: <jobs>", rótulo `ci` (criado na primeira vez), corpo com o run, o commit e, por job, os destaques e as últimas
  40 linhas do passo, limpas por formato (`scripts/ci_issue_falha.py`; teste em `scripts/tests/test_ci_issue_falha.py`,
  que o job `docs` roda). Com uma issue ABERTA do mesmo dia, comenta nela. Disparo manual do CI e PR não abrem issue.
  Reler um run antigo: `gh workflow run ci-aviso-de-falha.yml -f run_id=<id>` (cria a issue de verdade); só olhar:
  `python scripts/ci_issue_falha.py --repo dono/nome --run-id <id> --ensaio`.
- **Aviso quando o cron falha (29.155, C2; 29.187):** `.github/workflows/ci-aviso-de-falha.yml`, em runner hospedado, cuida de UMA
  issue do cron. Quando o `schedule` do CI termina em `failure`, `cancelled`, `timed_out` ou `startup_failure`: se há issue
  ABERTA do cron (rótulo `ci`, título começando por "CI noturno"), comenta nela; senão abre uma (título "CI noturno
  AAAA-MM-DD: <jobs>", rótulo `ci`, criado na primeira vez). O corpo traz o run e o commit, a lista de todos os jobs com a
  duração, as linhas de resumo por job do 29.184 e, por job que caiu, os destaques e as últimas 40 linhas do passo, limpas
  por formato (`scripts/ci_issue_falha.py`; teste em `scripts/tests/test_ci_issue_falha.py`, que o job `docs` roda). Quando o
  `schedule` volta a `success`, comenta e FECHA a issue. Nunca atribui ninguém nem usa o rótulo `agente` (o agente é decisão do
  dono). Disparo manual do CI e PR não abrem nem fecham issue.
  Reler um run antigo: `gh workflow run ci-aviso-de-falha.yml -f run_id=<id>` (escreve de verdade: abre, comenta ou fecha); só
  olhar: `python scripts/ci_issue_falha.py --repo dono/nome --run-id <id> --ensaio`.
- **Issues e rótulos (29.155, C5):** `.github/ISSUE_TEMPLATE/` tem dois formulários: `tarefa-do-agente.yml` (rótulo `agente`; pede item, perfil
  de `.github/agents/`, tamanho P/M/G, motivo, o que fazer, aceite, fora do escopo e a confirmação de que a tarefa não precisa de
  aparelho, conta real, ensino nem segredo) e `achado.yml` (rótulo `achado`: origem, onde, gravidade, o que foi visto e a evidência;
  achado é dado a conferir, não ordem). Os rótulos do repositório moram em `scripts/github_rotulos.py` (`frente:android|jev|aprendizado|
  portal|canais|github|desenho`, `tamanho:P|M|G`, `agente`, `achado`, `ci`): ensaio por padrão, `--aplicar` cria ou corrige cor e
  descrição, nunca apaga. O formulário só aplica `agente` ou `achado` sozinho; frente e tamanho a coordenação põe depois.
- **Achados das revisões automáticas (29.170):** `python scripts/coletar_achados_revisao.py --repo dono/nome [--horas 48 | --prs 487,488] [--json]` (só leitura; `--json` é o contrato da Canais: `id`, `pr`, `revisor`, `gravidade`, `arquivo`, `linha`, `frase`, `artefato`, `url`, `pr_estado`) tabela os achados do Codex e do Copilot nos PRs, com gravidade e arquivo:linha, para a orquestradora conferir; achado é a conferir, nunca ordem. Não dispara revisão (cada uma do Copilot custa créditos do dono). O quadro vai para o terminal ou `.claude/handoffs`, nunca para o Git.
- **Modelo de PR e rótulo (29.171):** `.github/pull_request_template.md` lembra prova, um PR por tarefa e `[skip ci]`; `.github/workflows/rotula-pr.yml` (hospedado, `pull_request_target`, sem checkout do PR) põe `frente:github` em branch `ci/…`, `agente` em `copilot/…` e `frente:<nome>` em `devops/…`, `jev/…`, `aprendizado/…`, `portal/…` e `canais/…` (`scripts/rotulo_do_pr.py`, 29.203); outro prefixo não ganha rótulo, e o rótulo precisa existir (`scripts/github_rotulos.py --aplicar`).

- **PostgreSQL do CI em 2 processos (29.179):** o job `backend-postgres` (hospedado) roda `pytest -n 2` com `pytest-xdist==3.8.0` instalado só nele; medido em 06/10: 33 min contra 69 min serial. O cron das 05:17Z e o `-n` do SQLite do central ficam como estão (decisão da orquestradora e do dono).
- **Resumo por corrida (29.184):** o `pr-leve.yml` e o `ci.yml` (PostgreSQL, SQLite e docs) terminam cada job com uma linha (tempo por etapa, soma, minutos cobrados estimados e, no leve e no PostgreSQL, testes do pytest e do vitest) no job summary e no log; `python scripts/github_custo.py --repo dono/nome --resumos` agrega as linhas da semana (baixa até 40 logs). Os passos novos do `ci.yml` nos jobs do runner central são aditivos (`continue-on-error`) e só provam na próxima corrida do cron.
- **Medida da revisão do Codex (29.194):** `python scripts/medir_revisao_codex.py --repo dono/nome --prs 473,474,... --classificacao ledger.json --publicar` lê pela API os achados do Codex por PR (gravidade, artefato, tempo até o achado, commits de correção que citam o PR) e comenta o relatório, com a recomendação mecânica, na issue de custo. A classificação confirmado/falso vem da frente (`ledger.json`: `{"<pr>": {"confirmados": n, "falsos": n}}`); sem ela o relatório usa só o indício e a regex do coletor, que é impreciso.
- **PostgreSQL da noite só quando precisa (29.193):** o job `pg-necessario` do `ci.yml` (hospedado) decide, por `scripts/pg_necessario.py`, se o `backend-postgres` roda: pula só se TODO arquivo mudado desde a última corrida da `main` em que o PostgreSQL passou for de `docs/`, `.claude/`, `frontend/` (menos `rotas.ts`), dos outros workflows ou `.md`; na dúvida roda, e o disparo manual roda sempre. Ver a decisão da noite: o resumo do job `pg · precisa rodar hoje?` ou `python scripts/pg_necessario.py --repo dono/nome --evento schedule --head <sha> --run 1`.
- **Esteira do agente de nuvem (29.192):** `python scripts/issue_do_pacote.py ITEM --repo dono/nome --agente-nuvem --aplicar` cria a issue do pacote com a etiqueta `agente-nuvem` (sem atribuir). `python scripts/agente_nuvem.py atribuir N --repo dono/nome` mostra as conferências e `--aplicar` atribui ao agente (única porta; um PR do agente aberto por vez e 1 atribuição por dia). `python scripts/agente_nuvem.py medir --repo dono/nome [--custos custos.json]` imprime aceitação, tempo, diff e créditos por item (créditos reais lidos na página de uso entram por `--custos`). A revisão do PR do agente é a de sempre.
- **Medida semanal automática (29.188, com o custo por PR do 29.207):** `.github/workflows/custo-semanal.yml` (hospedado) roda toda segunda 06:00Z o `github_custo.py --resumos` mais o `custo_por_pr.py` (créditos do Copilot por PR, revisões do Codex e o pico de PRs de revisão em 5 h; se a leitura falhar o relatório diz "NÃO lido") e publica o relatório como artifact `relatorio-custo` e como comentário na issue única com o rótulo `custo` (`scripts/custo_semanal_issue.py`, que recusa texto com formato de dado pessoal ou credencial). Disparo manual: `gh workflow run custo-semanal.yml`. O billing da conta não é lido (limite do token); os créditos reais se leem na página de uso da conta.

- **Resumo por job no CI leve (29.179):** o `pr-leve.yml` termina cada job com uma linha (tempo por etapa, soma e testes do pytest e do vitest) no job summary e no log; é a base da leitura de custo por PR.
- **Custo semanal do GitHub (29.178):** `python scripts/github_custo.py --repo dono/nome --dias 7 --anexar .claude/handoffs/github-custo-revisao.md` (só leitura, ~1,5 min) junta minutos hospedados faturáveis ESTIMADOS por workflow, minutos no runner `central`, créditos do Copilot estimados e as falhas. Não lê saldo nem gasto extra (a API de billing pede o escopo `user`) e o timing do run vem zerado em repositório privado: o saldo real continua sendo a página de uso, no Chrome do dono.
- **Issue a partir do pacote (29.177):** `python scripts/issue_do_pacote.py <id> --repo dono/nome` (ensaio por padrão; `--aplicar` cria) gera a issue de tarefa do pacote do item, idempotente pela marca `<!-- pacote:<id> -->`. Não atribui ao agente de nuvem (a atribuição é manual, com o sim do dono) e recusa texto com e-mail, IP, serial ou arroba; leia a prévia antes de `--aplicar`, porque nome de persona não é detectável. Os pacotes ficam fora do Git: rode no checkout central (`--pacotes DIR` aponta outro lugar).
- **Gatilhos travados e `[skip ci]` (29.176):** o `ci.yml` só tem `schedule` e `workflow_dispatch`; push de branch e PR nunca acionam o runner `central` (`scripts/tests/test_ci_gatilhos.py` falha se mudar). Por isso `[skip ci]` é SÓ dos commits que entram na `main`; o último commit de uma branch de PR não o leva, para o check leve rodar.
- **CI leve do PR (29.175):** `.github/workflows/pr-leve.yml` roda em runner HOSPEDADO (`ubuntu-latest`, nunca o `central`) a cada PR: `docs-check`, `pytest scripts/tests` e `typecheck` + `vitest` + `build` do frontend; sem a suíte do backend (essa é do funil e do cron). Cerca de 6 min de relógio e 7 faturáveis por PR (medido em 06/10). Commit com `[skip ci]` na ponta não dispara o check de `pull_request`: dispare à mão (`gh workflow run pr-leve.yml --ref <branch>`, hospedado) ou use uma ponta sem a marca. `AnexosTab.test.tsx` fica de fora até a Canais corrigi-lo em Linux.
- **Leitura diária do GitHub (29.155, C10):** `python scripts/github_rotina.py --repo dono/nome` (só leitura, `--json` para os dados) imprime a linha que a frente GitHub manda à coordenação toda manhã: cron da noite, runner `central`, runs ruins das últimas 26 h, issues `ci` e `agente` abertas, PRs do agente e créditos do Copilot ESTIMADOS (146 por revisão, 31 por tarefa do agente, medidos em 06/10). Roteiro e o que ler à mão no Chrome: `.claude/handoffs/github-rotina-diaria.md`. Sem workflow novo e nada no runner central.
- **Disparo só-PostgreSQL de verdade (29.155, C3):** o job `porta` agora é pulado em `somente_postgres=true`, então nenhum job
  vai ao runner do central (antes a porta ainda ia, por ~5 s, e os demais eram pulados por dependência dela).

`.github/workflows/ci.yml` — **6 jobs** (até 24/09 eram 5, e o cabeçalho do arquivo dizia 4):

| Job | Quando | O que faz |
|---|---|---|
| `backend-sqlite` | todo push/PR | `pytest -q` contra SQLite |
| `backend-postgres` | `schedule` (diário, 05:17 UTC) ou `workflow_dispatch` (com `somente_postgres=true` roda só ele, hospedado, sem ocupar o runner do central; `gh workflow run ci.yml -f somente_postgres=true`) | `pytest -q` contra `postgres:17` de serviço, `TEST_DATABASE_URL`; limite de **90 min** desde 06/10 (era 60, e antes 25: o de 25 cancelou o cron de 01/10, run 36819958569, e o de 60 o de 06/10, run 37418749690, com a suíte em ~55 min) |
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
- **Gatilhos:** só a corrida diária (`schedule`) e o disparo manual (`workflow_dispatch`); push e `pull_request`
  estão desligados (30/09 e 05/10). Um disparo novo no mesmo ref cancela a corrida anterior.
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

### GitHub Copilot (item 29.134, desde 05/10/2026)

- **O que está ligado.** A conta do dono tem o Copilot Pro+: cota mensal de créditos e uso adicional com orçamento
  dele, em que nenhuma sessão mexe. O repositório tem `.github/copilot-instructions.md` (o que o Copilot precisa saber
  para revisar e escrever aqui; a revisão lê só os primeiros 4.000 caracteres) e
  `.github/workflows/copilot-setup-steps.yml` (dependências do backend e do frontend na máquina do agente de nuvem;
  sempre `ubuntu-latest`, nunca o runner `central`).
- **Revisão de PR: PR a PR, pedida pela orquestradora.** `gh pr edit <n> --add-reviewer @copilot`. A revisão olha o
  diff inteiro contra a `main`: em PRs empilhados, peça na ponta da pilha. Reserve para PR sensível (segredo, conta
  real, migração, dinheiro).
- **Custo medido (`real`, 05/10).** Três revisões de PRs de 756 a 1.549 linhas custaram 566,87 créditos (US$ 5,67,
  cerca de US$ 1,90 cada) e levaram de 4 a 6 minutos, em runner hospedado, sem minuto cobrado de Actions. Vieram 10
  achados, 9 confirmados pelas frentes. A revisão automática em todo PR não cabe na cota, por isso a regra do
  repositório (ruleset `24513931`) fica `disabled`. Ligar ou desligar:
  `gh api -X PUT repos/FlavioNeto11/android/rulesets/24513931 -f enforcement=active` (ou `disabled`).
- **Achado do Copilot é achado a conferir, nunca ordem.** A frente dona do PR confere no código e responde; o conserto
  volta como delta à segunda leitura.
- **Agente de nuvem.** Só a orquestradora atribui tarefa, e só tarefa mecânica e delimitada. A aprovação para rodar
  workflow e o firewall ficam ligados; o Copilot não aprova PR.
  Primeira tarefa (`real`, 05/10/2026 18:14Z, item 29.137, PR 449 em rascunho): o agente parou sem alterar nada, com
  `hook errored`. Os hooks de `.claude/settings.json` chamam o Python por um caminho do Windows, que não existe na
  máquina Linux do agente, e ele honra esses hooks. O conserto é o item 29.147 (ramo `fix/29-147-hooks-portateis`):
  cada hook segue em exec-form (command + args, sem shell), com `command: "python"` resolvido pelo PATH (no Windows do
  dono é o Python 3.13; no agente, o `setup-python` de `copilot-setup-steps.yml`), os mesmos `-S -E` e o mesmo código
  de saída (2 bloqueia). Enquanto o conserto não estiver na `main`, o agente de nuvem não funciona neste repositório. Prova `simulated`:
  `scripts/tests/test_hooks_portateis.py` (os hooks rodando em exec-form com o `python` do PATH); `real` em Linux, com o agente do
  Copilot assumindo uma tarefa, fica `not_run`. A revisão de PR não é afetada.
- **Fora.** Vale só para este repositório. Ninguém instala o Copilot CLI nesta máquina. As opções de privacidade e de
  cobrança da conta são do dono.

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

**Partida lenta não é travamento** (29.124, `backend/app/marca_de_partida.py`). A cada subida, o supervisor sorteia um
id e o passa em `POC_PARTIDA_ID`, junto da pasta dele (`POC_PASTA_DO_SUPERVISOR`, a `data/` ao lado do
`supervisor.log`). O backend grava `data/backend-partida.json` (só `id`, `fase`, `ts`) antes do `AppState`, depois
dele, antes do `poc.start()` e em `no_ar`; dentro do `AppState`, também a cada migração aplicada e depois das
migrações e dos aparelhos (29.131). O silêncio de `/api/health` não conta como falha enquanto a marca é desta subida,
a fase não é `no_ar`, a partida tem menos de 600 s e a última reescrita menos de 240 s: os 240 s valem por passo
(uma migração sozinha que passe deles conta como falha), e os 600 s pela partida inteira. O `supervisor.log` diz a
fase, e a linha do kill diz há quanto tempo foi gravada a pilha do vigia que ela cita. Sem marca, com marca de outra subida, parada ou `no_ar`, vale a regra de sempre (90 s de carência e três
falhas a cada 15 s). Depois de 4 reinícios seguidos sem uma conferência boa, a espera antes do próximo vira 300 s,
dita no log; uma conferência boa zera a conta. O despejo do vigia do laço (29.121) também vai para a pasta do
supervisor, onde ele o procura para citar no kill. Backend subido à mão não tem id e não grava marca.

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

**`docs-check` antes de parar (29.166).** O `deploy.ps1` roda `scripts\docs-check.py` com o Python do **venv do backend**, no
ensaio e na subida de verdade, depois do backup e da conferência do `site/` e **antes de parar qualquer coisa**. Hoje ele
cobra links, IDs e o mapa do plano; o formato de `config/config.example.yaml` e de `.claude/plano-100.json` (chave desconhecida,
tipo errado, com o caminho da chave) entra quando o 29.160 (esquema do docs-check) for integrado, e este passo já o carrega. `ERRO` recusa a subida e o ensaio; `AVISO` não (mas um aviso "NAO conferido" vira `WARNING` do deploy). O venv é obrigatório porque a conferência do
exemplo precisa de PyYAML e pydantic, e sem eles o `docs-check` só avisa que não conferiu. `-PularDocsCheck` pula o passo, e o
console diz que o formato NÃO foi conferido: só para erro comprovadamente só de documentação numa subida que não pode esperar.
O `config.yaml` da instalação nunca é aberto por ele. Teste: `scripts/tests/test_deploy_portao_do_ensaio.py`.

**Dependências.** Desde 25/09 o `deploy.ps1` roda `uv pip install -r requirements.txt` no venv do backend
**entre parar e subir** (passo 3b; `-PularDependencias` desliga). Antes disso ele não instalava nada, e uma versão
nova no `requirements.txt` (ex.: `cryptography` 46.0.3 → 50.0.0, item T.4) nunca chegava à produção. O venv é do
`uv` e não tem `pip` dentro: `python -m pip` falha com "No module named pip". Tem de ser com o backend parado,
porque no Windows a `.pyd` carregada fica travada. O agente do worker não acompanha: `worker-requirements.txt` se
instala na máquina dele.

### Histórico, tag e rollback do deploy nativo (29.159)

**O que cada subida deixa.** Uma subida de verdade (a que parou o backend) acrescenta UMA linha a `data\deploys.jsonl`
(fora do Git, como o resto de `data\`): `ts_utc`, `resultado` (`ok` ou `falhou`), `commit_antes`/`migracao_antes` (o que
estava no ar), `commit_depois`/`migracao_depois`, `backup` (a pasta em `data\backups` que vale para voltar),
`backup_do_ensaio`, `tag`, `motivo` (a falha, em uma linha de até 300 caracteres), `duracao_s`, `opcoes` e `etapas_s` (29.156: segundos de cada etapa na ordem do deploy, `backup`, `site`, `docs_check`,
`painel`, `parada`, `dependencias`, `subida`, `conferencia`, `tag`, `ensaio_de_rollback`; numa falha, `interrompida` é o tempo da etapa que quebrou;
linhas anteriores ao campo não têm; a mesma lista sai na tela como "tempo por etapa"). Ensaio, recusa
do portão do `-PularBackup` e falha do build do painel (antes de parar) não entram: não mudaram nada no ar. Ler:
`Get-Content data\deploys.jsonl | ConvertFrom-Json | Select-Object ts_utc, resultado, commit_antes, commit_depois, backup, tag`.

**Tag e release.** Com a subida conferida (commit e migração batem), o deploy cria a tag anotada
`deploy-AAAAMMDD-HHMM` (UTC; `-2` se houver duas no mesmo minuto) no commit que subiu, envia à origem e pede ao `gh` um
release com as notas geradas. É no melhor esforço: sem `gh`, sem rede ou sem permissão a tela mostra o aviso, a linha do
histórico leva o aviso em `motivo` e o deploy segue (a tag não desfaz nem atrasa nada). `-SemTag` pula a tag e o release; a
linha do histórico sai sempre. A tag não dispara o `conteiner.yml` (29.157: ele só roda em push da `main`).

**Notas do release** (29.156, fatia 5): a release da tag `deploy-*` leva como notas as entradas NOVAS do `CHANGELOG.md` desde o deploy
anterior (os títulos `## …` que não existiam no `commit_antes`, até 40), a migração de antes para depois, a contagem de commits e o link de
comparação `dono/repositório/compare/<antes>...<depois>` (a URL da origem, que pode carregar credencial, nunca entra no texto). Sem
deploy anterior, sem `CHANGELOG.md` num dos commits ou qualquer falha, cai nas notas que o `gh --generate-notes` monta, como antes.
O texto passa por `Remove-DadosDaMaquina` (IP, `WIN-…`, `worker-…-NN`, caminho `C:\…`, e-mail, chaves `sk-`/`ghp_`/`github_pat_` e
sequências de 40+ caracteres em base64); só os títulos sobem, nunca o corpo das entradas.

**Rollback: o que muda com a migração.** Primeiro responda uma pergunta: o deploy que se quer desfazer trouxe migração
(`migracao_antes` diferente de `migracao_depois`)? Migração aplicada não se edita, e o código antigo sobre um banco mais
novo não é um estado testado (o `deploy.ps1` confere código e banco e recusa a subida que não bate: "o banco está em X e o código traz até Y").

1. **Sem migração nova** (só código): volte o código e suba de novo, com backup como sempre.
   - Achar o alvo: `commit_antes` da linha do deploy ruim, ou a tag do deploy anterior (`git tag --list 'deploy-*'`).
   - **Preferida:** `git revert <commit ruim>` na `main`, push e `deploy.ps1`. A `main` continua dizendo o que está no ar.
   - **Emergência** (não dá tempo de revert): `git switch --detach <tag ou commit>` no checkout central, `deploy.ps1` (com o
     backup dele) e, depois, **voltar** `git switch main` e `git pull --ff-only` ANTES do próximo deploy: com o checkout
     solto, o `git pull` de um deploy normal não anda.
2. **Com migração nova** (o banco já foi migrado): o caminho é restaurar o banco do backup do deploy e voltar o código
   junto, e **o que foi gravado depois do deploy se perde**. Antes de decidir, confira o que entrou desde então.
   - Pare o backend (`scripts\stop.ps1`, e a tarefa `farm-central` se estiver registrada).
   - Ensaie primeiro: `pwsh -File scripts\restore.ps1 -De data\backups\<backup da linha> -Para C:\temp\ensaio-rollback` e confira a
     migração e as contagens que ele imprime.
   - Restaure: `pwsh -File scripts\restore.ps1 -De data\backups\<backup da linha> -Confirmar` (guarda o que havia em
     `data\substituido-<carimbo>`).
   - Volte o código para a tag ou o commit do deploy ANTERIOR (`commit_antes`) como no item 1 e suba com `deploy.ps1`.
     O banco restaurado estará na migração anterior e o código antigo o abre.
3. **Agente do notebook.** O agente é cópia manual e não acompanha o deploy. Se o código voltou numa mudança que toca o fio do
   worker (`backend/app/contracts/worker/protocol.py`, o hash congelado) ou `worker-manifest.txt`, o agente também precisa voltar:
   monte uma árvore na tag (`git worktree add C:\temp\arvore-rollback <tag>`) e rode, com ela acessível à máquina do worker,
   `pwsh -File scripts\worker-install.ps1 -Origem <a árvore>`; confira na Infraestrutura que o worker voltou a `online` e sem
   `agent_outdated`. Remova a árvore temporária depois (`git worktree remove`).
**Ensaio do caminho 2 sem desfazer nada** (29.156, fatia 3): `pwsh -File scripts\rollback-ensaio.ps1` pega a linha `ok` mais nova
de `data\deploys.jsonl`, extrai só `backend\` do `commit_antes` (`git archive`, sem tocar o checkout nem o `.git`), restaura o
backup da linha numa pasta de trabalho (`restore.ps1` sem `-Confirmar`) e abre a cópia com o código antigo. Aprova se a
integridade está ok, a migração da cópia é a `migracao_antes` da linha e o código antigo NÃO quer aplicar migração nenhuma. Veredito em
`data\rollback-ensaio\ultimo.json`; saída 0 ok, 1 falhou, 2 pulado (backup podado ou commit ausente: aviso, não aprovação). Rode depois de
um deploy que trouxe migração, antes de precisar do rollback.

**O deploy já o chama** (29.156, fatia 4): depois da tag e antes de gravar a linha, só quando a migração de depois difere da de antes
e há pasta de backup, o `deploy.ps1` roda `rollback-ensaio.ps1` para ESSE deploy (commit, migração e backup por parâmetro: a linha ainda não
existe). O resultado vai à linha de `data\deploys.jsonl` em `ensaio_de_rollback` (`ok`, `falhou` ou `pulado`) e, quando não é `ok`,
em `ensaio_de_rollback_motivo` (texto fixo, até 200 caracteres); a tela mostra um aviso. **Nunca reverte nada nem derruba o
deploy**, e `pulado` (backup podado, commit ausente) nunca conta como aprovação. `-SemEnsaioDeRollback` pula o passo. A etapa
aparece em `etapas_s` como `ensaio_de_rollback`.
**Amostrador do host em dia (29.198).** Depois da conferência, o deploy compara o cabeçalho do `amostrador-host.ps1` com o do último CSV de `data/observabilidade/host` (e a hora do script com a do processo) e, se o processo roda código antigo (ou a tarefa está registrada mas parada), faz Stop + `-Instalar` + Start da `farm-amostrador-host`, sem lacuna: a linha do histórico ganha `amostrador` (`reinstalado`|`falhou`), `amostrador_motivo` e `amostrador_lacuna_s` (segundos entre a última linha antes e a primeira depois; o alvo é abaixo de 90). Tarefa não registrada não é registrada sozinha (`-Instalar` à mão, uma vez). Melhor esforço: nunca derruba o deploy; `-SemAmostrador` pula o passo; a etapa aparece em `etapas_s` como `amostrador`. Código em `scripts/lib/amostrador-do-deploy.ps1`; teste `scripts/tests/test_amostrador_do_deploy.py` (dublês).

4. **Depois de qualquer rollback:** `GET /api/health` (commit e migração), a 8010 escutando, a prova de fora, e uma linha
   nova em `data\deploys.jsonl` (o rollback também é uma subida e fica no histórico).

Limite dito de frente: o histórico e as tags nascem no próximo deploy; os anteriores a eles só se reconstroem pelos nomes das
pastas de `data\backups` e pelo `git log`. A prova `real` é um deploy com a linha e a tag (`not_run`).

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
- **`scripts/restore-ensaio.ps1`** (29.167) — o ensaio **semanal** (tarefa `farm-restore-ensaio`, domingo 04:30, prioridade
  ociosa; registro com `-Instalar`). Pega a cópia SQLite mais nova de `data/backups`, roda `restore.ps1` SEM `-Confirmar` numa
  pasta de trabalho própria (apagada no fim), confere integridade, migração e nº de tabelas contra o manifesto e aplica a
  migração do código atual NA CÓPIA (`-SemMigrar` pula). Falha também se a cópia mais nova tiver mais de 48 h (o `farm-backup`
  parou). Veredito em `data/restore-ensaio/ultimo.json` e `historico.jsonl` (só fatos, nenhum valor de tabela); saída 0 ok,
  1 falhou, 2 pulado. **Não manda Telegram**: o canal do § 15 só aceita os tipos de aviso montados no backend; ligar o `falhou`
  ao aviso é trabalho de backend (ver o resultado do 29.167). PostgreSQL (`parque.dump`) não é ensaiado aqui.

  1 falhou, 2 pulado. **Não manda Telegram**: quem avisa é o backend (28.60), que lê o `ultimo.json` a cada 15 min e manda `falhou`, `pulado`,
  veredito ilegível ou veredito com mais de 192 h pela rotina do canal (`avisos.restore_ensaio.*`; `docs/dominios/canais.md`). PostgreSQL (`parque.dump`) não é ensaiado aqui.
- **`scripts/funil.ps1`** (29.196) — o funil de um corte num comando só, em **Windows PowerShell 5.1** e sob o teto de CPU. Etapas: 1 `scripts/tests -n 4`,
  2 SQLite inteiro `-n 6`, 3 frontend (typecheck, vitest, build), 4 catracas + docs-check, 5 mypy, 6 PG dirigido em partes (`pg-rapido.py`; sem
  `-ListaPg` a etapa é PULADA e pulada nunca conta como verde). Sem `-SemTeto` o funil se relança sob `com-teto-de-cpu.ps1` (`-Teto 25` por padrão,
  batimento de 30 s) e o encadeamento roda dentro do job; `-TetoPorEtapa "pg=40,sqlite=25"` escreve o percentual de cada etapa no arquivo de teto
  antes dela. Grava um **run.txt padronizado** (`FUNIL`/`ETAPA` com `ini`, `fim`, `dur_s`, `rc`, `status` ok|falhou|pulado|nao_rodou, `teto`,
  `passed`, `failed`, `skipped`, `errors`; datas em UTC; detalhe de cada etapa em `<run>.<id>-<chave>.txt`, batimento do wrapper em
  `<run>.wrapper.txt`). Saída 0 só com tudo verde, 1 se alguma etapa falhou, 2 se nenhuma falhou mas alguma foi pulada ou não rodou. Uso:
  `powershell -NoProfile -File scripts\funil.ps1 -Raiz <checkout> -ListaPg <lista> -Saida <run.txt>`; `-Simular` mostra o plano; `-ParaNoErro` para na
  primeira falha. A comparação entre cortes lê o run.txt direto (`devops-29-180-comparar.py run.txt=<corte>`). Teste:
  `scripts/tests/test_funil.py` (comandos falsos pelo gancho `-ComandosDeTeste`). O run.txt guarda os **nomes** dos testes que falharam por etapa (`nomes_falhos="a;b"`, até 40; pytest `FAILED`, vitest `FAIL`, tsc `error TS`; no PG, lê as saídas em `<run>.pg`). `-ParalelosPg N` e `-WorkersPg N` repassam `--paralelo`/`--workers` ao `pg-rapido.py` (29.197). **Trava do funil:** enquanto roda, o funil mantém `data/funil-ativo.json`
  (`pid`, `run`, `inicio`, `raiz`) no checkout central (`git rev-parse --git-common-dir`; `FARM_FUNIL_TRAVA` troca o caminho) e exporta `FARM_FUNIL_RODANDO=1`.
  Os testes de script que queimam CPU ou sobem subprocessos (`test_com_teto_de_cpu.py`, `test_funil.py`, `test_amostrador_host.py`) levam
  `pytestmark = pytest.mark.carga` e `scripts/tests/conftest.py` os **pula** quando a trava existe com o processo vivo (e menos de 12 h); o próprio funil
  os roda (`FARM_FUNIL_RODANDO`), e `FARM_FUNIL_CARGA=1` força de propósito. Teste novo que gera carga entra com o marcador. **Nunca verde por engano** (achados do Codex no PR 506): lista do PG sem teste elegível = etapa PULADA; `-Saida` reaproveitado guarda o anterior em `<run>.anterior` e o run.txt é de uma execução só; commit não identificado reprova o funil (`commit=desconhecido`, rc 1); se o wrapper não consegue trocar o teto da etapa ele sai 124 (`REPROVADO`) e o funil grava `FUNIL wrapper rc=124 status=reprovado`.
- **`scripts/canais-agendadas.ps1`** (29.186) — as tarefas do host para os scripts da Canais, no molde da `farm-restore-ensaio`
  (Idle, `-Instalar` / `-Remover`, `-Instalar -Simular` só mostra o plano). `-Tarefa resumo-diario`: `farm-canais-resumo-diario`, todo
  dia às 07:03 do horário local do host (Brasília) desde 08/10; a ação registrada leva `-Enviar` (manda o `.claude\canais\resumo_diario.py`
  ao Telegram do dono), uma execução manual sem `-Enviar` só imprime; atrasada mais de 6 h NÃO envia (`-Forcar` ignora). `-Tarefa
  espelho-do-deploy`: `farm-canais-espelho-deploy`, sem gatilho: quem fecha o deploy usa `-Pedir -Raiz <checkout em origin/main>
  [-Deploy NN] [-Aplicar]`, que grava `data\canais\tarefas\espelho-pedido.json` e dispara a tarefa (sem `-Aplicar` o espelho só
  relata; o pedido é consumido, a raiz tem de estar na pasta-pai dos checkouts). Log por execução em `data\canais\tarefas`
  (padrões de token cobertos por `***`, 30 dias); sem segredo na linha de comando (o token vem do arquivo de ambiente da instalação, lido
  pelos próprios scripts da Canais); saída 0 ok, 1 script falhou, 2 faltou script/python, 3 atrasada, 4 sem pedido, 5 pedido inválido.
  O `-Instalar` real só depois que os scripts da Canais estiverem no checkout central. Mais duas tarefas (28.73 e 28.75):
  `-Tarefa laco-de-aparelhos` (`farm-canais-laco-de-aparelhos`: `avisos_de_aparelho.py --laco --intervalo-s 120`, sobe ao ligar o host e
  às 00:05, reinicia até 3 vezes, sem limite de tempo, log em fluxo e UM por dia) e `-Tarefa saude-dos-lacos`
  (`farm-canais-saude-dos-lacos`: `saude_dos_lacos.py --avisar` a cada 5 min, limite de 4 min; só lê e avisa, nunca relança). Como as
  outras, levam `-Enviar` na tarefa registrada e sem ele só imprimem. Antes de registrar o laço, parar o que roda na sessão da Canais
  (dois laços mandariam o aviso duas vezes).

  1 falhou, 2 pulado. **Não manda Telegram**: quem avisa é o backend (28.60), que lê o `ultimo.json` a cada 15 min e manda `falhou`, `pulado`,
  veredito ilegível ou veredito com mais de 192 h pela rotina do canal (`avisos.restore_ensaio.*`; `docs/dominios/canais.md`). PostgreSQL (`parque.dump`) não é ensaiado aqui.

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

**Quantos emuladores cabem num host** (29.161, medido em 06/10/2026). Antes de subir as vagas de um worker
(`max_slots`) ou de comprar máquina, conte assim:

- **CPU manda primeiro.** No máximo **1 vCPU de emulador por thread lógica do host**, deixando 4 threads para o sistema,
  o agente e o adb: (threads − 4) ÷ vCPU por aparelho. Em CPU híbrida (núcleos P, E e LP-E, como o Core Ultra do central),
  conte com folga: núcleo E e LP-E rendem menos como vCPU. **1,5 vCPU por thread quebra**: no notebook da LAN, 9 emuladores
  de 2 vCPU (18 vCPU) em 12 threads deram load mediano de 12,2 nos convidados (limite do aviso: 8) e derrubaram o
  `system_server` do android-13 em 06/10 00:30–01:14Z, com o host em 50–66 % de CPU. É sobreinscrição de vCPU, não falta de
  ciclo total. Medida de um ponto só: o central, com 12 vCPU em 22 threads, está folgado, mas não é teto medido.
- **RAM vem depois.** Planeje com o custo no host de `est_real_mb` (`backend/app/devices/perfis.py`): **2,7 GB** por
  emulador `google_apis` e **5,2 GB** com Play Store, mais 8 GB para o sistema. O working set do `qemu-system-x86_64`
  (0,8–0,9 GB num convidado pouco usado) não serve para planejar. Não baixe a RAM do convidado para caber mais: o custo
  vai para swap e thrash.
- **Disco:** cada AVD ocupa ~10–15 GB (partição de dados de 10 GB mais snapshots).
- **Exemplo:** 16 núcleos com 32 threads homogêneos dão (32 − 4) ÷ 2 = **14** emuladores de 2 vCPU, que pedem
  14 × 2,7 + 8 ≈ 46 GB de RAM; 64 GB bastam.
- **Para conferir:** os avisos `Convidado sob pressão` por aparelho e por dia (tabela `events`). No notebook de 04 a 06/10
  foram ~136 por aparelho, contra ~41 no central.

Fontes: `.claude/handoffs/hardware-analise.md` (fora do Git, Frente Hardware, 06/10) e a medida da Frente DevOps
(`.claude/handoffs/devops-medida-host.md`). Prova: `real` (central e banco da central em `mode=ro`, 06/10, commit `360d133a`).

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
- `data/logs/laco-travado-<UTC>-<n>.txt` (29.121, `backend/app/vigia_do_laco.py`) — a pilha de TODAS as threads do
  backend quando o laço de eventos fica mais de 10 s sem bater (60 s na partida, antes da primeira batida), gravada
  por uma thread fora do laço antes de o supervisor matar o processo. No máximo 3 por episódio, a cada 30 s; ficam os
  20 mais novos. A linha `encerrando o backend` do `supervisor.log` cita o despejo dos últimos 5 min, e o
  `backend.log` diz quando o laço voltou e quanto ficou parado.
- **Relógio do host** — a tarefa `farm-relogio` (SYSTEM, a cada 15 min) roda `scripts/sincronizar-relogio.ps1`: mede o
  desvio pelo NTP.br com `w32tm /stripchart` e ajusta acima de 0,2 s; o `w32time` fica sem sincronização própria
  (`syncfromflags:NO`), porque a rede bloqueia NTP com porta de origem 123 (K-055). Cada rodada vai para
  `data\logs\relogio.log`; `-Simular` mede sem ajustar.
- **Antes de qualquer experimento num aparelho** (agente, `adb input`, `settings put`, carga de CPU): tirar um screencap
  e ler a conta logada, sem tocar. `account_label`, `/personas` e os vínculos não bastam: o android-04 tinha
  `qa-user-04` e nenhuma persona, com a conta do felipe logada (K-053). Experimento vai num aparelho novo e sem conta
  (provisionar e aposentar são permitidos no ambiente central).
- **Amostrador permanente do host** (29.156, fatia 1) — `scripts/amostrador-host.ps1`, tarefa `farm-amostrador-host` (ao ligar o
  host e todo dia 00:05; uma instância; prioridade ociosa; só leitura; `-Instalar` registra sem iniciar). Uma linha por minuto em
  `data/observabilidade/host/AAAAMMDD.csv` (UTC), retenção de 7 dias só nessa pasta: `ts_utc, cpu_host_pct,
  vm_convidado_nucleos, vmmem_ws_mb, qemu_host_pct, ram_livre_mb, disco_livre_gb, processos_top` (até 3 NOMES de processo com mais
  CPU no minuto, em % do host, sem linha de comando) e `avisos_pressao` (`android-05:3;android-01:1`, lidos do banco em
  `mode=ro`; vazio = nenhum ou não medido). Desde o 29.185 há três colunas no fim: `cpu_media_pct` (CPU do host como média do
  minuto; `cpu_host_pct` é só o instantâneo de uma janela curta e oscila de 8 % a 91 % entre minutos vizinhos), `demais_processos_pct`
  (processos fora do topo e do qemu) e `nao_atribuido_pct` (média − todos os processos: o que nasce e morre dentro do minuto,
  núcleo/interrupções, VM; é onde se enxerga a carga que o topo não mostra). Um arquivo do dia começado por versão antiga ganha a nova
  linha de cabeçalho uma vez; quem lê deve ignorar linhas cujo primeiro campo não seja data. O mutex tem o nome `Global\farm-amostrador-host`
  por padrão; `-NomeDoMutex` existe só para os testes não disputarem com o amostrador real. É a entrada do 29.165 e da janela da prova. Na primeira leitura de teste, o topo
  da CPU do host foi `python` (provavelmente os testes do funil) e o antivírus, não a VM do WSL nem os emuladores.
- **Teto de CPU para o funil** (29.174) — `scripts/com-teto-de-cpu.ps1 -Teto 40 [-NucleosE] -Linha "<comando>"` (ou
  `-ComandoJson '["exe","arg"]'` para argumentos exatos). Cria um Job Object com teto rígido de CPU (percentual do total de
  threads do host) e a afinidade opcional dos núcleos E (`-NucleosE`: as threads de menor eficiência, lidas do próprio Windows;
  `-Afinidade 0x..` fixa uma máscara; `-Simular` só mostra o plano), e roda o comando **criado já dentro do job** (suspenso, entra, retoma):
  pytest, workers do xdist e netos ficam sob o teto; sem administrador; o job some com o comando e, se o wrapper morrer, a árvore
  morre junto. `-Linha` passa pelo `cmd.exe /d /c`. **O `pwsh` (PowerShell 7) deste host é um app MSIX e o Windows o ativa FORA do job: o
  teto não vale para ele nem para nada que ele inicie** (o funil 58 rodou assim, sem teto, com a árvore em 0,0 s de CPU no
  contador do wrapper). Use `powershell` (5.1) como hospedeiro do script do funil, ou chame o python/pytest direto; o wrapper
  RECUSA (código 125) o comando que usa `pwsh` (`-PermitirPwsh` ignora, só para teste) e avisa quando a árvore quase não usa CPU.
  `-ArquivoDeTeto <arquivo>` (uma linha com o percentual) troca o teto do job que já roda, lido a cada 2 s: é o teto por etapa do `funil.ps1`. `-BatimentoS N` (padrão 60; 0 desliga) imprime a cada N s a
  CPU que a árvore já usou e acusa árvore com 0 s depois de `-ZeroAposS` s (padrão 20): dá para conferir no primeiro minuto, pelo
  arquivo de saída, que o funil está dentro do job. Imprime a CPU usada pela árvore (% do total) e propaga o código de saída. Não toca `.wslconfig`, WSL, túnel nem relógio e não
  mata processo alheio. Teste: `scripts/tests/test_com_teto_de_cpu.py`. O custo do teto é tempo de funil: compare a duração da
  suíte sem e com teto antes de adotar.

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

### Site institucional na raiz (29.77, ADR-075; ligado no central desde 05/10/2026)

**Estado no central (`real`, 05/10/2026, `a0c9865e`):** site e formulário ligados às 01:19Z, com o sim do dono. Prova de
fora às 01:19:51Z com `SITE=ligado CONTATO=ligado WEBHOOK_DO_TRELLO=ligado`: tudo como esperado. O primeiro contato de
verdade pelo formulário segue `not_run`. De fábrica, em qualquer outra instalação, tudo nasce desligado.

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

   O bloco `portal` recusa chave desconhecida (desde o 29.83): um nome errado (`site_ligad`, `telefon`,
   `buscas_por_operador_hor`) faz a subida falhar dizendo qual é, em vez de valer o padrão calado. Rode o
   `deploy.ps1 -Ensaio` depois de editar.

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
- sem rastreador (29.85): a prova de fora baixa a raiz COMO navegador e reprova qualquer `<script src>` de outra
  origem. Motivo: a borda da Cloudflare injeta o beacon do Web Analytics (`static.cloudflareinsights.com`) só nesse
  caso, e o `curl` puro não vê; medido em 05/10.
  - A CSP do site bloqueia o beacon, mas sobra um erro de console em todo visitante.
  - O conserto é desligar a injeção na zona: Web Analytics / Real User Measurements (RUM), no painel da Cloudflare,
    feito pelo dono.
  - Nunca afrouxar a CSP;
- a borda não reescreve o HTML (29.91):
  - a raiz, a 404 e o `index.html` do painel saem com `Cache-Control: … no-transform`;
  - o HTML do site sai comprimido em gzip pela origem;
  - o painel tem CSP própria; se ela quebrar uma tela, `server.csp_do_painel: so_relatar` (ou `desligada`) no
    `config.yaml` e o reinício da `farm-central` a desfazem sem deploy. Grafias exatas:
    - a chave é `csp_do_painel`, dentro do bloco `server`; os valores são `aplicar`, `so_relatar` e `desligada`, sem
      acento;
    - uma chave com outro nome (`csp_painel`, `csp-do-painel`) é IGNORADA em silêncio, e a política fica em
      `aplicar`. Confira o nome pelo cabeçalho: com `so_relatar`, `curl -s -D - -o /dev/null http://127.0.0.1:8000/central/`
      mostra `content-security-policy-report-only`;
    - um valor fora dos três recusa a subida, e a `farm-central` não sobe. `off` ou `no` sem aspas o YAML lê como
      falso, que também recusa;
    - em `so_relatar` as violações só aparecem no console do navegador; o servidor não recebe relatório;
    - o `connect-src` lista também `wss://` de cada `server.public_hosts` e o `ws://`/`wss://` de cada
      `server.allowed_origins`: o `'self'` não cobre WebSocket num navegador só com CSP 2 (Safari e iOS antigos);
  - as páginas do site apontam o CSS, o JS e as imagens com `?v=<sha256>` (29.95): a borda guarda esses arquivos por
    4 h no navegador, e só um endereço novo faz quem já visitou ver o deploy novo.
  Com isso, um Web Analytics, um Rocket Loader ou uma ofuscação de e-mail religados por engano na zona não entram nas
  páginas. Para conferir de fora: `curl -s -D - -o /dev/null -H 'Accept-Encoding: gzip' https://<host>/` mostra
  `content-encoding: gzip` e `no-transform`;
- o vigia da borda (29.97) faz essas conferências sozinho, de hora em hora, no líder:
  - liga com `portal.vigia.ligado` (de fábrica `true`) e só roda com `server.public_hosts`;
  - a 1ª volta é 5 min depois da subida;
  - pede também `/api/instances` sem credencial (29.101), que tem de dar 401 ou 403; se der 2xx, a saúde mostra
    `portal_api_aberta` e o dono recebe o aviso na hora: a API do central está aberta para a internet, e o gesto é
    tirar o nome público do ar (o vigia não para nada sozinho);
  - o resultado aparece na saúde como `portal_borda_defeito` (com o gesto na zona) ou `portal_borda_sem_conferir`
    (depois de `voltas_sem_conferir` voltas seguidas sem conseguir); no log `poc.portal`, "vigia da borda ok" a cada
    volta limpa;
  - o aviso ao dono vai pela Canais, um por código e por dia;
  - o que é defeito é a régua de `backend/app/modules/portal/domain/borda.py`, a mesma da prova de fora;
- 180 dias no sistema: o laço apaga a linha inteira a cada hora, com o contato ligado ou não;
- o descartado (teto diário, `campo_invalido`, `falhas_demais`) tem o conteúdo apagado sem chegar à equipe. O
  `pendente` (canal desligado) e o `retido` (excesso na hora) guardam o conteúdo até a entrega ou os 180 dias;
- cópias de segurança: a pasta `AAAAMMDD-HHmmss` sai na primeira cópia depois de 14 dias (`-Reter 14`), menos a mais
  nova, que nunca sai sozinha; as de deploy e de ensaio têm ainda o teto de 10. Pasta com sufixo no nome não sai
  sozinha: depois de ligar o contato, quem cria uma a apaga à mão quando acabar. Teto prático, com a rotina rodando:
  cerca de 195 dias;
- o `cliente_hash` (código do endereço de rede, nunca o IP) fica os mesmos 180 dias.

**Pedido de exclusão de um contato do site** (o visitante pede pelo formulário ou por telefone).
- **Pelo painel (29.83):** Configuração → "Site e privacidade". Busque pelo telefone como a pessoa escreveu (com DDD, se ela usou), marque as linhas
  (inclusive a do próprio pedido, quando ele veio pelo formulário), diga por onde o pedido chegou e confirme "Apagar
  definitivamente". Quem aperta é uma pessoa logada; o resultado lista as mensagens que ficaram para apagar à mão no
  chat. Uma linha `mantida` com `em_envio` pede outra tentativa em um minuto; com `falhou`, ver o log `poc.portal` e o
  da Canais; com `canal_sem_exclusao`, o 28.34 ainda não está na base: use o procedimento manual abaixo.
- **Manual (reserva, quando o painel não serve):** quem executa é o operador, com o sim do dono no chat, porque apaga
  dado. O pedido feito pelo formulário é ele mesmo um contato que chegou ao chat: são duas ou mais linhas e duas ou
  mais mensagens a apagar. Nada do conteúdo vai para chat, cartão ou log.
1. Achar as linhas comparando TODOS os dígitos que o visitante informou, com DDD (troque `<DIGITOS>`, por exemplo
   `11987654321`; com o `55` na frente, use o número inteiro como ele veio). O SELECT mostra os dígitos para conferir
   antes de apagar:
   `SELECT id, criado_em, estado, replace(replace(replace(replace(replace(telefone,' ',''),'-',''),'(',''),')',''),'+','') AS digitos FROM portal_contatos WHERE replace(replace(replace(replace(replace(telefone,' ',''),'-',''),'(',''),')',''),'+','') LIKE '%<DIGITOS>'`.
   Fique só com as linhas cujos `digitos` são os do visitante (com ou sem o `55`).
2. Ver o aviso de cada linha na fila da Canais (chave `portal:<id>`, em `avisos_entregas`) e as mensagens que o bot já
   mandou (`canal_enviadas`, `fato` = a mesma chave):
   `SELECT chave, estado FROM avisos_entregas WHERE chave IN ('portal:<id1>', 'portal:<id2>')` e
   `SELECT fato, ref_mensagem, enviada_em FROM canal_enviadas WHERE canal='telegram' AND fato IN ('portal:<id1>', 'portal:<id2>')`.
   Se algum aviso estiver `enviando`, espere um minuto e repita: ele vira `enviado` ou `pendente`.
3. Apagar as respostas do dono a essas mensagens, que ficam com texto em `canal_entradas`: apagar só o texto e manter a
   linha, que é o registro do canal:
   `UPDATE canal_entradas SET texto=NULL WHERE canal='telegram' AND responde_a IN (<ref_mensagem do passo 2>)`.
4. Numa transação só, apagar o aviso que ainda não saiu e as linhas do contato, para o laço do portal não reenfileirar
   no meio:
   `BEGIN;`
   `DELETE FROM avisos_entregas WHERE chave IN ('portal:<id1>', 'portal:<id2>') AND estado <> 'enviando';`
   `DELETE FROM portal_contatos WHERE id IN (<id1>, <id2>);`
   `COMMIT;`
   O `pendente`, o `falhou` e o `incerto` ainda têm o texto; o `enviado` e o `descartado` já não têm, e saem juntos.
   Se o passo 2 ainda mostrar `enviando`, não apague: espere e volte ao passo 2.
5. Apagar as mensagens no chat do Telegram: o dono, à mão (o bot só apaga a própria mensagem até 48 h). Use as datas
   do passo 2 para achá-las; são todas as do passo 2, inclusive a do próprio pedido.
6. Responder ao visitante pelo telefone que ele deixou. Dizer que as cópias de segurança saem em cerca de duas
   semanas, pela rotina, e que a cópia mais nova e as pastas com sufixo no nome não saem sozinhas.

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
| `pg-rapido.py` | P | PG dirigido da suíte no contêiner descartável `farm-pg-rapido` (29.99): recria o contêiner com WAL mínimo, roda a lista em `--partes`, amostra o disco a cada 30 s e aborta a parte com uma linha em 85 % do tmpfs; `--simular` só lista as partes, `--amostrar` lê o contêiner de pé. `--paralelo N` (1 a 4; 29.197, **medido em 07/10 e SEM ganho: não é o padrão** — numa fatia de 64 arquivos, série `-n 8` 234 s, `-n 12` 268 s e 2 x `-n 6` 236 s de relógio; o contêiner do PG já usa 400–500 % dos 6 vCPUs da VM do Docker, então mais workers ou mais contêineres na mesma VM não aceleram; o que acelera o PG é CPU da VM ou menos DDL por teste) sobe N contêineres (`farm-pg-rapido`, `-2`…, portas 55434, 55435…, o tmpfs de 4 GB dividido entre eles) com um fio cada e as partes numa fila; `--workers` troca o `-n` do pytest (padrão 8; 6 por contêiner com mais de um). Parte vermelha não mata a do outro fio (termina e é relatada). Só com a vez da orquestradora |
| `pg-diagnostico.py` | P | 29.199: onde o servidor PG gasta CPU numa corrida de testes. Sobe o contêiner descartável `farm-pg-diag` (porta 55440, tmpfs 2 GB, a mesma configuração do `farm-pg-rapido` mais `pg_stat_statements`), roda a fatia (`--lista`, `--workers`) e imprime a tabela por comando normalizado e por categoria (esvaziamento do esquema do worker x aplicação) com o veredito (>= 35 % do tempo do servidor no harness: vale o código; < 15 %: o item morre); `--simular` só diz o plano, `--ler` lê o contêiner de pé, `--manter` não o para. Mesma trava de uma rodada por vez do `pg-rapido.py`; só com a vez da orquestradora |
| `restore.ps1` (sem `-Confirmar`) | S | Ensaio em pasta limpa |
| `restore.ps1 -Confirmar` | P | Substitui `data/` de verdade, exige backend parado |
| `amostrador-host.ps1` | S | Amostrador permanente do host (CPU, RAM, disco, VM do WSL, processos que mais usam CPU, avisos de pressão por aparelho), 1 linha/min em `data\observabilidade\host`, retenção 7 dias; `-Instalar` [P] registra a tarefa `farm-amostrador-host` |
| `com-teto-de-cpu.ps1` | S | Roda um comando sob teto rígido de CPU (Job Object) e, opcional, nos núcleos E; só limita a árvore do próprio comando |
| `funil.ps1` | P | O funil de um corte (scripts, SQLite, frontend, catracas, mypy, PG dirigido) em PowerShell 5.1 sob `com-teto-de-cpu.ps1`; run.txt padronizado; mexe só no contêiner `farm-pg-rapido` e em CPU/disco da máquina |
| `rollback-ensaio.ps1` | S | Ensaio do rollback com migração: backup da última linha de `deploys.jsonl` aberto pelo código do `commit_antes`, em pasta própria (Idle, sem tocar o checkout nem `data\poc.sqlite3`) |
| `restore-ensaio.ps1` | S | Ensaio semanal sobre a cópia mais nova (pasta própria, Idle, não toca `data\poc.sqlite3`); `-Instalar` [P] registra a tarefa `farm-restore-ensaio` |
| `canais-agendadas.ps1` | S | Tarefas do host para a Canais: resumo diário 07:03 (`farm-canais-resumo-diario`) e espelho do deploy sob demanda (`farm-canais-espelho-deploy`, `-Pedir`); `-Instalar` [P] registra, `-Remover` tira; `-Enviar` manda ao Telegram do dono |
| `deploy.ps1` | P | Para → copia banco → sobe → confere; mexe na tarefa `farm-central`; grava `data\deploys.jsonl` e, conferida a subida, cria a tag `deploy-AAAAMMDD-HHMM` e o release (29.159; `-SemTag` pula a tag) |
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
| `python scripts/reverificar-sessoes.py aparelho:perfil:conta …` | P | Reverifica as sessões das contas reais pela API do central, só observando (`session/verify`, sem adb nem senha): no máximo 2 tentativas por aparelho, o @ redigido no motivo e a tela "Confirm you're human" parando o aparelho na hora; `--json` grava o desfecho por comando |
| `recuperar-parque.ps1` | P | Reinicia aparelhos remotos pelo worker |
| `worker-install.ps1` / `worker-agent.ps1 -Instalar` | P | Instala/registra o agente numa máquina worker |
| `worker-comando.py` (`--worker`, `--linha` ou `--argv-json`; sessão em `CENTRAL_SESSAO`) | P | Cliente do comando remoto (29.154, ADR-079): pede a execução de UMA linha na máquina de um worker, espera o estado final e imprime a saída já redigida pela central; desligado de fábrica nos três interruptores; sem IA; o código de saída é o do comando (2 recusa/`uncertain`, 3 prazo, 4 sem sessão, 5 central fora) |
| `install-central-service.ps1` | P | Registra o backend do central como tarefa supervisionada |
| `worker-tunnel.ps1` | P | Sobe/mantém o túnel SSH real |
| `portal-instalar-tunel.ps1` | P | **Rodado pelo dono**, como Administrador, depois do `cloudflared tunnel login`: cria o túnel da Cloudflare, grava o `config.yml` com as travas do ADR-073, aponta o DNS e instala o serviço `Cloudflared`. Não abre porta nem mexe no central |
| `portal-gerar-senha.ps1` | P | **Rodado pelo dono**: gera o `API_TOKEN` e grava no `.env` sem mostrar na tela (`-Trocar` substitui). Sessão de IA não roda este script no `.env` de verdade |
| `python scripts/portal-config.py conferir`, ou `ligar`/`recuar` com `--ensaio` | S | Só lê o `config.yaml`: diz se o bloco `server` está pronto para o portal ou o que mudaria |
| `python scripts/portal-config.py ligar` / `recuar` | P | Declara ou tira o hostname público no `config.yaml` (sete linhas, com cópia em `data/backups/`); vale no próximo reinício do central |
| `portal-prova-de-fora.sh antes` / `depois` | S | Só pedidos sem credencial ao endereço público; nunca tenta login. Sai com 2 se `/api/instances` der 200. Com `SITE=ligado` confere o site na raiz (CSP, `robots.txt`, 404 fora da lista fechada, e nenhum `<script src>` de outra origem com a raiz pedida como navegador, 29.85; e, com o painel, `no-transform`, o gzip da origem na raiz e a CSP do painel, 29.91; e o `?v=` do CSS e do JS batendo com o que a borda entrega, 29.95); com `CONTATO=ligado`, um `POST` com a isca (não grava nem avisa), o 415 e o 413. Testado contra um `curl` falso em `scripts/tests/test_portal_prova_de_fora.py` |
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
| `gravacao-com-marcador.py` (`--ensaio` padrão / `--aplicar --backup CAMINHO`) | S / P | Reparo único do 31.118: as gravações de ensino salvas antes dele recebem o marcador da persona no texto digitado e, desde a F2, na tela gravada (`screen_lines`, `screen_title`). O ensaio roda numa cópia do banco (origem em `mode=ro`) e imprime as contagens e os ids das sessões que mudariam; `--aplicar` exige o backup e a mesma migração do código. Rodar só com o backend no ar já no 31.118 e com o "vai" da orquestradora. Nunca imprime valor nem id de persona |
| `github_rotulos.py` (`--aplicar` escreve) | S / P | Rótulos do repositório (29.155): o ensaio só lê (`gh label list`); `--aplicar` cria ou corrige cor e descrição dos rótulos da lista, só no GitHub do repositório, e nunca apaga rótulo |
| `issue_do_pacote.py` | S | Gera a issue de tarefa do pacote de um item (29.177), idempotente por ID, sem atribuir ao agente; ensaio por padrão, `--aplicar` cria; lê pacotes que ficam fora do Git |
| `github_custo.py` | S | Relatório de custo do GitHub numa janela (29.178), só leitura: minutos hospedados faturáveis estimados por workflow, minutos no runner `central`, créditos do Copilot estimados; `--anexar` acrescenta a um arquivo; não lê saldo (billing pede o escopo `user`) |
| `custo_semanal_issue.py --arquivo F --ensaio` | S | Valida o relatório semanal (29.188) e diz o que publicaria; sem `--ensaio` abre ou comenta a issue de custo, só dentro do workflow `custo-semanal.yml`, em runner hospedado |
| `agente_nuvem.py atribuir N` / `medir` | S (`medir`) / escreve (`atribuir --aplicar`) | `medir` só LÊ; `atribuir` sem `--aplicar` é ensaio e com ele atribui a issue ao agente de nuvem, só na sessão e dentro do teto (29.192) |
| `medir_revisao_codex.py --prs ...` | S (escreve só com `--publicar`) | Mede a revisão automática do Codex nos PRs informados e recomenda manter, restringir ou desligar (29.194); `--publicar` comenta na issue de custo |
| `github_rotina.py` | S | Leitura diária do GitHub (29.155, C10), só leitura: uma linha com o cron da noite, runner, runs ruins, issues `ci` e `agente`, PRs do agente e uma ESTIMATIVA de créditos do Copilot (contagem de runs; o saldo real só a página de uso mostra) |
| `pr_revisao.py` (`--aplicar` abre) | S / P | Abre o PR de revisão de uma branch de código do corte (29.200): base por merge-base, rótulo da frente, diff sem formato sensível, teto de ondas (4 por hora, 20 por janela de 5 h do Codex); ensaio por padrão |
| `fecha_pr_revisao.py` (`--aplicar` fecha) | S / P | Fecha sem merge os PRs `[revisão] …` que o Codex já leu ou que bateram no limite (29.201); achado só fecha se a frente já leu (`--lidos`); ensaio por padrão, nunca mescla nem apaga branch |
| `custo_por_pr.py` | S | Custo por PR (29.202): execuções de Actions por PR, créditos do Copilot ESTIMADOS (146 por revisão, 31 por tarefa do agente), revisões do Codex contadas e o pico de PRs de revisão em 5 h contra a regra de 20; só leitura, sem título nem branch na saída |
| `limpar_branches_revisao.py` (`--aplicar` apaga) | S / P | Apaga no GitHub as branches `revisao/*` já mescladas (29.155, C18); o ensaio só lê; não toca o parque nem o central |
| `coletar_achados_revisao.py` | S | Tabela dos achados das revisões automáticas (Codex e Copilot) nos PRs da janela (29.170), só leitura; mascara e-mail, IPv4 e sequências longas |
| `rotulo_do_pr.py` | S | Rotula o PR pelo prefixo da branch (29.171); ensaio por padrão, `--aplicar` rotula; só rótulo existente, nunca cria nem tira |
| `secret_scan_resumo.py` | S | Resumo redigido do relatório do gitleaks e issue do achado (29.158); roda no workflow hospedado, não toca o parque nem o central; só chama o `gh` com o token do workflow |
| `ci_issue_falha.py --run-id N --ensaio` | S | Só LÊ o GitHub (`gh api`, `gh run view --log-failed`) e imprime a issue que o aviso do cron abriria (ou fecharia, se o run for verde) para aquele run (29.155, 29.187); sem `--ensaio` escreve no GitHub, mas só roda dentro do workflow `ci-aviso-de-falha.yml`, em runner hospedado |

| `ci_issue_falha.py --run-id N --ensaio` | S | Só LÊ o GitHub (`gh api`, `gh run view --log-failed`) e imprime a issue que o aviso do cron abriria para aquele run (29.155); sem `--ensaio` escreve no GitHub, mas só roda dentro do workflow `ci-aviso-de-falha.yml`, em runner hospedado |
| `resultado_confere.py ARQ...` | S | Só LÊ o plano e o JSON de resultado e lista o que o `aplicar` recusaria (29.190); `--gravar RASCUNHO DESTINO` grava só se estiver certo; sem rede nem IA |
| `marcar-fluxo-de-prova.py` (`--fluxo ID` repetível; `--ensaio` padrão / `--aplicar --backup CAMINHO`) | S / P | 31.130: marca como nascidos de uma prova os fluxos dados pelo id (ou referência pública) e a sessão de treino de origem (`nascido_de_prova`, migração 122). O ensaio roda numa cópia do banco (origem em `mode=ro`) e imprime as contagens e os ids; `--aplicar` exige o backup e a mesma migração do código. Idempotente; id inexistente sai com código 1. Não muda status, plano nem trilha. A Android roda como operadora depois do deploy |
| `abertura-nas-receitas-ensinadas.py` (`--ensaio` padrão / `--aplicar --backup CAMINHO`) | S / P | 31.138: passe único que destila de novo os fluxos ensinados com a regra de hoje (31.121 e 31.139) e troca a receita viva da etapa que agora começa com `open_app` e não começava (a troca do treino, 30.79, na mesma chave, com a trilha no livro). O ensaio roda numa cópia do banco e imprime as contagens antes e depois e os ids de fluxo e receita; `--aplicar` exige o backup e a mesma migração do código. Idempotente. Não muda fluxo, status nem gravação. A Android roda como operadora depois do deploy |
| `prova-onda-aprendizado.py` (`--operacao OP` [`--commit SHA`] [`--banco`]) | S | 31.192: marca a prova da onda dos itens do aprendizado que dependem do commit no ar (31.165, 31.178, 31.179). Lê a saúde (ou `--commit`) e o banco em `mode=ro`. Por item, confere pelo git se o commit dele está no central e o que a operação exercitou. Imprime o formato do plano-100 só com as linhas `real` (`resultados`); as `not_run` vão a `pendentes`, com o motivo, e não entram no `aplicar`. Só ids e contagens |
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
- A mensagem com cara de senha não é guardada: a Central a apaga do chat e responde sem ecoar nada. Se o
  Telegram não deixar apagar, a resposta pede ao dono que apague.
- A resposta a uma execução que pergunta por senha ou token também é recusada, qualquer que seja a forma do
  texto (decide pelo contexto, com o vocabulário da triagem de credencial): não é guardada, é apagada do chat, e a
  resposta orienta o dono: a senha se grava na conta da persona. Vale
  para o reply ao aviso e para `/responder <id> <texto>`.
- Enquanto uma execução espera senha ou token, um texto curto (até 3 palavras, como "kiwi2024!" ou
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

## 17. Parada por limite semanal (95 %), item 29.145

Pedido do dono (05/10): quando o uso semanal da conta de Claude chega a 95 %, tudo para de forma ordenada e ele reinicia
o processo com OUTRA conta. É um procedimento de sessões, não de código: nada aqui toca o parque, o banco ou o Git além do
que o handoff já faz.

**Medir.** A orquestradora lê o uso pelo painel da IDE (`get_usage`, "todos os modelos" do semanal), e diz a HORA da leitura
(só do `date -u` lido no mesmo comando). Longe do gatilho basta a leitura de cada rodada; perto dele, a cada 5 minutos. Para
SÓ ao ler 95 % (os 5 % acima são a gordura para terminar direito: não antecipar nem ficar ocioso antes). A medida do
semanal vale para a conta inteira, não por sessão.

**Ordem de parada** (ao ler 95 %):

1. **Handoff curto por frente, PRIMEIRO.** Cada sessão grava o seu em `.claude/handoffs/<frente>.md` (o que fez, o que falta,
   branches e commits, ids de processo ou tarefa em curso, o que NÃO repetir). O scratchpad que importa (script, medida,
   rascunho) vai para `.claude/handoffs/` junto, porque o scratchpad some com a sessão.
2. **Parar crons e subagentes** que a sessão disparou (os da IDE e os agendados), e deixar as suítes em segundo plano
   terminarem ou anotar no handoff que ficaram a meio.
3. **Registrar** a parada em `.claude/session-registry.md` (sessão, hora lida do `date -u`, motivo: 95 % do semanal).
4. **Um handoff único da orquestradora** em `.claude/handoff-current.md`, escrito por último: estado do plano, do deploy,
   das frentes (apontando para os `<frente>.md`), pendências do dono e a primeira ação de quem retomar. Com ele o dono
   reinicia com OUTRA conta.

**Quem retoma.** A orquestradora nova (a conta nova) abre as sessões com nome, continuação (o handoff da frente), modelo e
força (Sonnet por padrão; Opus só onde a leitora não cobre), mede o custo e ajusta depois. Não reabre as sessões da conta
antiga.

**O que roda sem sessão e continua** durante e depois da parada: a tarefa `farm-central` (o servidor), o agente do notebook
da LAN, o cron do GitHub (secret-scan semanal, rotinas das 05:17Z e 06:03Z) e os amostradores de medida. O que não continua:
qualquer coisa que dependa de sessão viva, como a reconciliação do Trello, o vigia do Telegram e a execução de itens do
plano-100. Por isso o handoff da Canais diz o último id lido do vigia e o que a reconciliação ainda deve.

`not_run`: a parada de verdade (nunca foi disparada); este texto é o runbook, e o gatilho é a leitura humana da
orquestradora, sem automação.

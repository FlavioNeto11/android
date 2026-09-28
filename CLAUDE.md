# CLAUDE.md — Central de Aparelhos (FlavioNeto11/android)

Painel React e backend FastAPI que operam um parque de emuladores Android com IA. O dono manda um objetivo em
português; a IA planeja, age e **comprova** em cada aparelho. Há um servidor central, um worker remoto no notebook
da LAN, e o Instagram é o app real. **Falha ou incerteza nunca contam como sucesso.**

Este arquivo carrega em toda sessão. Mantenha-o curto; o detalhe fica em `docs/`, que se lê sob demanda.

## Ordem de leitura

1. [`docs/estado-atual.md`](docs/estado-atual.md): handoff (o que acabou de mudar, bloqueios, próxima ação).
2. [`docs/README.md`](docs/README.md): índice, com a fonte principal de cada assunto e quando abri-la.
3. Só o que a tarefa pede. Um item do plano-100 tem pacote próprio em `.claude/plano-100/pacotes/<id>.md`. Um
   domínio, `docs/dominios/*.md`. Uma decisão, `grep -n "ADR-" docs/decisoes.md`. Uma armadilha conhecida,
   `grep -n -i "<termo>" docs/conhecimento/aprendizados.md`.

**Não abra por inteiro:**

- `docs/plano-100.md` (~50 KB): leia só a linha do item com `grep -n "^| <id> |"`.
- `docs/auditoria-2026-09-21/` (467 KB): registro datado do commit `f1e61b3`. Confira no código antes de repetir
  um achado.
- As seções históricas de `docs/relatorio-validacao.md` e `docs/api-contract.md`: consulte por `grep -n "^#"`.

## Invariantes (não negociáveis)

- **A automação faz o que a pessoa pediu, inclusive entrar com a credencial que ELA guardou na conta da persona**
  (ADR-040, que substitui em parte o ADR-025): cofre, consentimento por conta, digitação só pelo canal sensível
  (`type_secret`), só no app e no site daquela conta; a execução não carrega credencial. Os limites da IA são de
  comportamento: sem fake news, sem ofensa explícita. Desafio, 2FA com código não fornecido e CAPTCHA seguem com a
  pessoa (ADR-009); nada de evasão de detecção de emulador ou antibot.
- **Segredo nunca** em código, teste, log, evento, evidência, prompt, memória, fixture ou Git. Não leia nem imprima
  o `.env`. Para saber se a chave está configurada, use `GET /api/ai`.
- **APK só da Play Store com a conta do dono, ou arquivo que ele fornecer.** Nunca de espelho de terceiros; a pasta
  `apks/` fica fora do Git.
- **O ambiente central é de desenvolvimento e validação do dono; ainda não é produção** (decisão do dono, 28/09).
  O checkout `C:\git\android`, a porta 8000, o parque e o agente do notebook existem para validar o trabalho:
  implantar (`deploy.ps1`, com ensaio de migração e backup como sempre), reiniciar a tarefa `farm-central`, ligar,
  desligar, criar e aposentar aparelhos e atualizar o agente do notebook são permitidos sem pedir. **Continua
  exigindo autorização explícita em chat:**
  - chamada paga de IA além de uma validação pontual, ou bateria de avaliação (o saldo da API é pequeno e separado
    dos créditos da IDE);
  - ação com efeito fora da máquina numa conta real de terceiros (mandar mensagem, comentar, publicar, seguir) e
    resetar os dados de um aparelho com conta real logada;
  - mexer no túnel, no relógio, no WSL ou no `.wslconfig`.

  Sem autorização, entregue o código e o procedimento e marque a prova como `not_run`.
- **Prova tem três níveis e não se misturam:**
  - `real`: data, máquina, commit e ids de execução ou comando;
  - `simulated`: `arquivo::teste`, com provedor ou aparelho falso;
  - `not_run`.

  Um teste com mock não prova o ambiente real.
- **Migração aplicada não se edita**; cria-se a próxima (`backend/migrations/NNN_*.sql`, com sha256 em
  `schema_migrations`).
- `config/config.yaml` e `.env` são **por instalação e ficam fora do Git**; o exemplo é `config/config.example.yaml`.
  Um checkout entre commits antigos pode apagar o `config.yaml` do ambiente central: confira antes de trocar de branch
  (`docs/operacao.md`).
- **O harness de testes usa `base_console_port: 5640`.** Nunca rode a suíte supondo isolamento dos emuladores reais
  sem conferir.
- **Estado do plano-100 só pelo mecanismo.** `.claude/plano-100/estado.json` e `docs/execucao-plano-100-runner.md`
  mudam por `scripts/claude-plan-100.py aplicar|relatorio`, nunca à mão. Todo ID novo no plano entra num bloco de
  `.claude/plano-100.json`.
- **Não abra sessão Claude aninhada** (`claude -p`: não há CLI nesta máquina). Orquestração pelo Workflow
  `.claude/workflows/plano-100.js` ou por subagentes. Esse `.js` não pode ter quebra de linha literal dentro de
  string e fica em LF.

## Comandos verificados

| O quê | Comando | Observação |
|---|---|---|
| Testes do backend, um arquivo | `cd backend && .venv/Scripts/python.exe -m pytest -q tests/test_x.py` | durante o trabalho |
| Suíte do backend inteira | `cd backend && .venv/Scripts/python.exe -m pytest -q` | ~11 min em SQLite; só antes do commit, em segundo plano |
| Suíte em PostgreSQL | a mesma, com `TEST_DATABASE_URL=postgresql://…` | ~14 min; `docs/banco.md` |
| Testes dos scripts | `backend/.venv/Scripts/python.exe -m pytest -q scripts/tests` | a partir da raiz |
| Frontend | `cd frontend && npm run typecheck && npm test` | `npm run build` gera o `dist` que o backend serve |
| Estado do plano-100 | `python scripts/claude-plan-100.py check` | não chama IA |
| Pacotes do plano-100 | `python scripts/plano-100-pacotes.py`, ou com `--fila --bloco <b>` | sem `--fila`, regenera o índice |
| Documentação | `python scripts/docs-check.py` | links, IDs, mapa, migrações, vocabulário |
| Saúde do ambiente central | `curl -s http://127.0.0.1:8000/api/health` | leitura: `commit`, `migration`, `problems` |
| Saldo das contas de IA | `curl -s http://127.0.0.1:8000/api/ai/balances` | estimado; leitura nova: `POST …/{conta}` (ADR-051) |
| Subir ou parar (dev) | `scripts/start.ps1 -Dev` / `-Simulated`; `scripts/stop.ps1` | [P] na máquina central: é o ambiente central |
| Implantar | `scripts/deploy.ps1` (`-Ensaio` para ensaiar) | [P], permitido para validar (ambiente central) |

Os comandos com `/` e `&&` funcionam no Git Bash e no PowerShell 7. Na tabela de scripts de `docs/operacao.md`, [P] marca o que toca o parque ou o ambiente central, e [T] o que gasta API.

## Fluxo de trabalho (protocolo permanente)

1. **Recuperar contexto.** Skill `retomar`: `git status`, `git log -5`, `git worktree list`, `estado-atual.md` e
   `claude-plan-100.py check`.
2. **Selecionar a tarefa** em [`docs/roadmap.md`](docs/roadmap.md), separando decisão do dono, implementação
   pendente e validação pendente.
3. **Confirmar os critérios de aceite.** Skill `preparar-tarefa <id>`: linha do plano ou pacote, ADRs envolvidos,
   aprendizados da área, se toca o mundo real.
4. **Preparar o pacote mínimo**: arquivos, testes direcionados e as decisões envolvidas. Para executar itens pela
   esteira, use a skill `plano-100` (modelo e esforço vêm do pacote, não se inventam).
5. **Executar.** Leia por busca e trechos; mantenha saídas longas em arquivo.
6. **Validar** com teste direcionado; a suíte inteira só no fim. Registre real, simulado ou não executado.
7. **Atualizar** o doc principal do assunto, o ADR (se houve decisão), o aprendizado (se houve armadilha), o estado
   pelo mecanismo e o `CHANGELOG.md`. Rode `python scripts/docs-check.py`.
8. **Handoff.** Skill `fechar-tarefa`: atualize [`docs/estado-atual.md`](docs/estado-atual.md), faça o commit e o
   push.

## Convenções

- Commit **direto na `main`**, sem PR: é escolha do dono. Mensagens convencionais em português
  (`feat(area): …`, `fix(…)`, `docs(…)`, `chore(plano-100): …`), terminando com a linha `Co-Authored-By` pedida
  pelo ambiente.
- **Velocidade acima de cerimônia, sem cortar medição.** Meça em vez de supor. Diga sempre o que foi provado com
  infraestrutura real e o que foi simulado.
- Código e docs em português. Comentários explicam o porquê; siga a densidade do arquivo vizinho.
- Subagentes não leem este arquivo automaticamente como você: passe no prompt as regras que valem para eles.

## Onde alterar (mapa rápido)

| Área | Código | Doc principal |
|---|---|---|
| API, eventos, rotas | `backend/app/api.py`, `models.py`, `events.py` | [`docs/api-contract.md`](docs/api-contract.md) |
| Comandos e worker | `backend/app/commands/`, `workers/`, `worker/` | [`docs/arquitetura.md`](docs/arquitetura.md), [`docs/worker.md`](docs/worker.md) |
| Aparelhos e parque | `backend/app/devices/`, `taskqueue/scheduler.py` | [`docs/dominios/parque.md`](docs/dominios/parque.md) |
| Fila e execução | `backend/app/taskqueue/`, `modules/execution/` | [`docs/dominios/execution.md`](docs/dominios/execution.md) |
| IA | `backend/app/planning/`, `taskqueue/executor.py` | [`docs/ia.md`](docs/ia.md) |
| Apps, releases, loja, manifesto de app | `backend/app/releases/`, `modules/applications/` (`planning/catalog/` é shim) | [`docs/dominios/apps-e-loja.md`](docs/dominios/apps-e-loja.md) |
| Skills, DSL, compilador, ensino | `backend/app/modules/skills/`, `modules/capabilities/`, `contracts/skills/` | [`docs/dominios/skills.md`](docs/dominios/skills.md), [`docs/design/evolucao-arquitetural.md`](docs/design/evolucao-arquitetural.md) |
| Perfis, Instagram, treinamento | `backend/app/social/`, `integrations/instagram/`, `training/` | [`docs/dominios/perfis-e-instagram.md`](docs/dominios/perfis-e-instagram.md) |
| Banco e migrações | `backend/app/db.py`, `backend/migrations/` | [`docs/banco.md`](docs/banco.md) |
| Segurança | `backend/app/security/` | [`docs/operacao.md`](docs/operacao.md) |
| Painel | `frontend/src/features/*` | [`docs/produto.md`](docs/produto.md) |
| Operação e scripts | `scripts/*.ps1` | [`docs/operacao.md`](docs/operacao.md) |

Regras por caminho carregadas sob demanda: `.claude/rules/*.md` (migrações, workflows, segredos e mundo real,
testes, plano-100).

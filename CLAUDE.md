# CLAUDE.md — Central de Aparelhos (FlavioNeto11/android)

Painel React e backend FastAPI que operam um parque de emuladores Android com IA. O dono manda um objetivo em
português; a IA planeja, age e **comprova** em cada aparelho. Há um servidor central, um worker remoto no notebook
da LAN, e o Instagram é o app real. **Falha ou incerteza nunca contam como sucesso.**

Este arquivo carrega em toda sessão. Mantenha-o curto; o detalhe fica em `docs/`, que se lê sob demanda.

## Ordem de leitura

1. [`.claude/handoff-current.md`](.claude/handoff-current.md): handoff curto e local da sessão (comece por ele; se faltar, só o topo de `docs/estado-atual.md`). Depois, só o topo de
   [`docs/estado-atual.md`](docs/estado-atual.md) (~50 KB: `sed -n 1,45p`) para o que acabou de mudar, bloqueios e próxima ação.
2. [`docs/README.md`](docs/README.md): índice, com a fonte principal de cada assunto, quando abri-la e o mapa "onde alterar".
3. Só o que a tarefa pede. Um item do plano-100 tem pacote próprio em `.claude/plano-100/pacotes/<id>.md`. Um
   domínio, `docs/dominios/*.md`. Uma decisão, `grep -n "ADR-" docs/decisoes.md`. Uma armadilha conhecida,
   `grep -n -i "<termo>" docs/conhecimento/aprendizados.md`.

**Não abra por inteiro** (a guarda barra o `Read` sem `limit` em `docs/plano-100.md`, `docs/decisoes.md`,
`docs/estado-atual.md` e `CHANGELOG.md` e devolve o caminho; no plano, `grep -n "^| <id> |"`): nem
`docs/auditoria-2026-09-21/` (467 KB, registro datado do commit `f1e61b3`: confira no código antes de repetir um
achado), nem as seções históricas de `docs/relatorio-validacao.md` e `docs/api-contract.md` (`grep -n "^#"`).

## Invariantes (não negociáveis)

- **A automação faz o que a pessoa pediu, inclusive entrar com a credencial que ELA guardou na conta da persona**
  (ADR-040, que substitui em parte o ADR-025): cofre, consentimento por conta, digitação só pelo canal sensível
  (`type_secret`), só no app e no site daquela conta; a execução não carrega credencial. Os limites da IA são de
  comportamento: sem fake news, sem ofensa explícita. Desafio, 2FA com código não fornecido e CAPTCHA seguem com a
  pessoa (ADR-009); nada de evasão de detecção de emulador ou antibot. A rede por aparelho (ADR-056) é configuração
  declarada e medida; rotação de IP e mascarar emulador, imagem ou identidade seguem proibidos.
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
  `schema_migrations`). Detalhe em `.claude/rules/migracoes.md`; a guarda barra a edição de migração commitada.
- `config/config.yaml` e `.env` são **por instalação e ficam fora do Git**; o exemplo é `config/config.example.yaml`.
  Um checkout entre commits antigos pode apagar o `config.yaml` do ambiente central: confira antes de trocar de branch
  (`docs/operacao.md`).
- **O harness de testes usa `base_console_port: 5640`.** Nunca rode a suíte supondo isolamento dos emuladores reais
  sem conferir.
- **Estado do plano-100 só pelo mecanismo.** `.claude/plano-100/estado.json` e `docs/execucao-plano-100-runner.md`
  mudam por `scripts/claude-plan-100.py aplicar|relatorio`, nunca à mão. Todo ID novo no plano entra num bloco de
  `.claude/plano-100.json` (detalhe em `.claude/rules/plano-100.md`).
- **Não abra sessão Claude aninhada** (`claude -p`: não há CLI nesta máquina). Orquestração pelo Workflow
  `.claude/workflows/plano-100.js` ou por subagentes. Esse `.js` fica em LF e sem quebra de linha literal em string
  (`.claude/rules/workflows-js.md`; o hook `workflow-js.py` confere).

## Economia de contexto (política completa em [`.claude/politica-de-contexto.md`](.claude/politica-de-contexto.md))

- A conversa é memória temporária; o repositório é a durável. Contexto >400k: `/handoff` e preparar a troca; >500k:
  sessão nova + `.claude/handoff-current.md`, não retomar a antiga. Registro em `.claude/session-registry.md`.
- Saída de ferramenta seletiva (`grep`/`head`/`tail`, `git diff --stat`, `Read` com `offset`/`limit`; teste devolve
  contagem e falhas).
- Subagent só se necessário e pelos agents de `.claude/agents/`, com retorno `Feito / Evidências / Validação /
  Bloqueios / Mudanças / Próximo`. Opus `xhigh` só por pedido explícito.

## Comandos verificados

| O quê | Comando | Observação |
|---|---|---|
| Testes do backend, um arquivo | `cd backend && .venv/Scripts/python.exe -m pytest -q tests/test_x.py` | durante o trabalho |
| Suíte do backend inteira | `cd backend && .venv/Scripts/python.exe -m pytest -q -n 6` | ~10 min; uma por vez na máquina, em prioridade Idle (no PowerShell, `(Get-Process -Id $PID).PriorityClass='Idle'` antes); só antes do merge; por que não `-n 8`: `.claude/rules/testes.md` |
| Frontend | `cd frontend && npm run typecheck && npm test` | `npm run build` gera o `dist` que o backend serve |
| Estado do plano-100 | `python scripts/claude-plan-100.py check` | não chama IA; pacotes e fila: skill `plano-100` |
| Documentação | `python scripts/docs-check.py` | links, IDs, mapa, migrações, vocabulário |

Suíte em PostgreSQL, testes dos scripts, saúde, saldos de IA, subir, parar e implantar: [`docs/operacao.md`](docs/operacao.md)
(§ 3, § 4, § 6, § 10; na tabela de scripts da § 12, [P] marca o que toca o parque ou o ambiente central, e [T] o que
gasta API). Os comandos com `/` e `&&` funcionam no Git Bash e no PowerShell 7.

## Fluxo de trabalho (protocolo permanente)

1. **Recuperar contexto**: skill `retomar`.
2. **Selecionar a tarefa** em [`docs/roadmap.md`](docs/roadmap.md), separando decisão do dono, implementação
   pendente e validação pendente.
3. **Critérios de aceite**: skill `preparar-tarefa <id>`.
4. **Executar** com o pacote mínimo (itens pela esteira: skill `plano-100`), lendo por busca e trechos.
5. **Validar** com teste direcionado; a suíte inteira só no fim. Registre real, simulado ou não executado.
6. **Fechar**: skill `fechar-tarefa` (doc do assunto, ADR, aprendizado, estado pelo mecanismo, `CHANGELOG.md`,
   `docs-check`, `docs/estado-atual.md`, commit e push).

## Convenções

- Commit **direto na `main`**, sem PR: é escolha do dono. Mensagens convencionais em português
  (`feat(area): …`, `fix(…)`, `docs(…)`, `chore(plano-100): …`), terminando com a linha `Co-Authored-By` pedida
  pelo ambiente.
- **Velocidade acima de cerimônia, sem cortar medição.** Meça em vez de supor. Diga sempre o que foi provado com
  infraestrutura real e o que foi simulado.
- Código e docs em português. Comentários explicam o porquê; siga a densidade do arquivo vizinho.
- Subagentes não leem este arquivo automaticamente como você: passe no prompt as regras que valem para eles.

Onde alterar cada área (código e doc principal): [`docs/README.md`](docs/README.md#onde-alterar). Regras por caminho
carregadas sob demanda: `.claude/rules/*.md` (migrações, workflows, segredos e mundo real, testes, plano-100).

# Execução do plano-100

7 de 67 itens implementados. Gerado por `scripts/claude-plan-100.py` a partir do que
o workflow devolveu; a prova dos aceites continua em `relatorio-validacao.md`.
**Implementado não quer dizer aceite provado** — a coluna Prova é que diz isso.

| Item | Estado | Prova | Modelo | Conferência | Evidência | Bloqueio |
|---|---|---|---|---|---|---|
| 0.1 | partial | not_run | opus | ok | Scripts novos: scripts/backup.ps1 (cópia online do SQLite com o backend no ar via scripts/sqlite-copia.py:32 Connection.backup(); pg_dump para PostgreSQL; config/ e credentials.key com -IncluirSegredos; .env NUNCA entra… | Falta a autorização do dono para o ato final: executar scripts\deploy.ps1 na produção (reiniciar o backend em 127.0.0.1:8000 e migrar o banco real). Proibido n… |
| 0.2 | implemented | real | opus | ok | backend/app/security/access.py:70 avaliar passou a receber o PAR: a isenção de loopback exige par local E nome de loopback; nome de loopback vindo de outro IP vira 401 (não 403 — o nome não é hostil, falta o segredo). :… |  |
| 0.3 | implemented | real | opus | ok | Listener dedicado: backend/app/main.py:122 create_worker_app (serve só worker_router; sem middleware, sem CORS, sem lifespan — o AppState é o mesmo objeto), :175 despachante (escolhe o app por scope['server'][1]) e :147… | A tarefa agendada viva (farm-tunel-192.168.1.19) continua com -MapaReverso '18000:8000' e não foi reinstalada: apontá-la para 8010 agora cortaria o worker do b… |
| 0.4 | implemented | real | sonnet | **questionada** | backend/app/workers/registry.py:247-268 (aceita_trabalho e motivo_manutencao; a evidencia do agente dizia :210, conferente corrigiu); backend/app/api.py:969-974,1015,1046 (_precheck consulta manutencao antes de motivo_n… |  |
| 0.5 | implemented | real | sonnet | ok | backend/app/automation/hierarchy.py:15-17,40-56,86-99,140-155 (UiTree.sent_as_message + prompt_lines/line com protect, TEXT_CAP=80/PROTECTED_TEXT_CAP=400); backend/app/planning/capabilities.py:74-79 (Capability.local_pr… |  |
| 0.6 | implemented | real | sonnet | ok | backend/app/planning/anthropic_provider.py:100-113,203-212 classifica billing (402 ou texto/type 'billing_error', achado #90) sem depender de classe dedicada do SDK 1.6.0; backend/app/taskqueue/executor.py:63-133 disjun… |  |
| 0.7 | blocked | not_run | sonnet | ok | Conferido no código de hoje: restart-pelo-worker já existia e não precisou de código novo (backend/app/api.py:940-953 roteia o verbo 'restart' para o worker quando está em worker_verbs; backend/app/worker/executor.py:19… | Os dois atos reais do item exigem autorização do dono e não foram executados por regra desta chamada: (1) reiniciar android-09 e android-10 no parque vivo — sc… |
| 0.8 | implemented | real | sonnet | ok | .github/workflows/ci.yml (4 jobs: backend-sqlite em push, backend-postgres agendado/workflow_dispatch com service postgres:17, frontend com npm ci+typecheck+test, worker-agent-smoke instalando só worker-requirements.txt… |  |
| 0.9 | implemented | real | sonnet | ok | backend/app/workers/registry.py:191-233 (remove e rotate_credential); backend/app/api.py:1328-1359 (rotas DELETE /workers/{id} e POST /workers/{id}/rotate-credential, desamarrando runtime em memoria); backend/app/models… |  |
| 0.10 | blocked | not_run | sonnet | ok | O pacote .claude/plano-100/pacotes/0.10.md declara em negrito: 'Esta linha é uma decisão do dono, não trabalho de implementação. Nenhum agente a executa. O estado correto dela é blocked'. Confirmado no código de hoje qu… | Decisão 2 (dono precisa confirmar no console do provedor que a chave antiga do provedor de IA foi revogada e registrar isso) e decisão 3 (dono precisa decidir… |
| 1.1 | pendente | — | — | — |  |  |
| 1.2 | pendente | — | — | — |  |  |
| 1.3 | pendente | — | — | — |  |  |
| 1.4 | pendente | — | — | — |  |  |
| 1.5 | pendente | — | — | — |  |  |
| 1.6 | pendente | — | — | — |  |  |
| 1.7 | pendente | — | — | — |  |  |
| 1.8 | pendente | — | — | — |  |  |
| 1.9 | pendente | — | — | — |  |  |
| 2.1 | pendente | — | — | — |  |  |
| 2.2 | pendente | — | — | — |  |  |
| 2.3 | pendente | — | — | — |  |  |
| 3.1 | pendente | — | — | — |  |  |
| 3.2 | pendente | — | — | — |  |  |
| 3.3 | pendente | — | — | — |  |  |
| 3.4 | pendente | — | — | — |  |  |
| 3.5 | pendente | — | — | — |  |  |
| 3.6 | pendente | — | — | — |  |  |
| 4.1 | pendente | — | — | — |  |  |
| 4.2 | pendente | — | — | — |  |  |
| 4.3 | pendente | — | — | — |  |  |
| 4.4 | pendente | — | — | — |  |  |
| 4.5 | pendente | — | — | — |  |  |
| 4.6 | pendente | — | — | — |  |  |
| 5.1 | pendente | — | — | — |  |  |
| 5.2 | pendente | — | — | — |  |  |
| 5.3 | pendente | — | — | — |  |  |
| 5.4 | pendente | — | — | — |  |  |
| 5.5 | pendente | — | — | — |  |  |
| 5.6 | pendente | — | — | — |  |  |
| 5.7 | pendente | — | — | — |  |  |
| 6.1 | pendente | — | — | — |  |  |
| 6.2 | pendente | — | — | — |  |  |
| 6.3 | pendente | — | — | — |  |  |
| 6.4 | pendente | — | — | — |  |  |
| 6.5 | pendente | — | — | — |  |  |
| 6.6 | pendente | — | — | — |  |  |
| 7.1 | pendente | — | — | — |  |  |
| 7.2 | pendente | — | — | — |  |  |
| 7.3 | pendente | — | — | — |  |  |
| 7.4 | pendente | — | — | — |  |  |
| 8.1 | pendente | — | — | — |  |  |
| 8.2 | pendente | — | — | — |  |  |
| 8.3 | pendente | — | — | — |  |  |
| 8.4 | pendente | — | — | — |  |  |
| 9.1 | pendente | — | — | — |  |  |
| 9.2 | pendente | — | — | — |  |  |
| 9.3 | pendente | — | — | — |  |  |
| 9.4 | pendente | — | — | — |  |  |
| 9.5 | pendente | — | — | — |  |  |
| 10.1 | pendente | — | — | — |  |  |
| 10.2 | pendente | — | — | — |  |  |
| 10.3 | pendente | — | — | — |  |  |
| 10.4 | pendente | — | — | — |  |  |
| T.1 | pendente | — | — | — |  |  |
| T.2 | pendente | — | — | — |  |  |
| T.3 | pendente | — | — | — |  |  |

Pendentes (60): 0.1, 0.7, 0.10, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 7.2, 7.3, 7.4, 8.1, 8.2, 8.3, 8.4, 9.1, 9.2, 9.3, 9.4, 9.5, 10.1, 10.2, 10.3, 10.4, T.1, T.2, T.3

A evidência aparece resumida acima; o texto integral de cada item, com os testes que foram de fato
executados, está em `.claude/plano-100/estado.json` (fora do Git, regerável por `aplicar`).

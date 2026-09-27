# Roadmap — o que falta, por tipo de pendência

Revisado em 24/09/2026 sobre `f443a90`. Os IDs são os do plano-100.

**Fontes.** Este arquivo diz **o que falta e qual é o próximo passo**. Ele não repete as outras fontes, que continuam
sendo a referência de cada assunto:

- **Requisito** de cada item: a linha dele em [`plano-100.md`](plano-100.md). Leia com
  `grep -n "^| 7.4 |" docs/plano-100.md`, ou abra o pacote `.claude/plano-100/pacotes/<id>.md`, gerado por
  `python scripts/plano-100-pacotes.py`.
- **Estado registrado**: [`execucao-plano-100-runner.md`](execucao-plano-100-runner.md), gerado a partir de
  `.claude/plano-100/estado.json`.
- **Prova dos aceites**: [`relatorio-validacao.md`](relatorio-validacao.md) §13.

Quando um item daqui fecha, o registro muda **pelo mecanismo** (`scripts/claude-plan-100.py aplicar`). Depois
atualize esta página e [`estado-atual.md`](estado-atual.md).

**Retrato.** São 93 itens: 88 `implemented`, 3 `partial` (7.4, 8.3, T.2), 1 `blocked` (8.4) e 1 `pending` (12.3).
"Implementado" não é "provado".

| Prova | Itens |
|---|---|
| `real` | 59 |
| `simulated` | 18 |
| `not_run` | 9 |
| automatizada, registrada à mão como `tests`/`unit` | 7 |

## 1. Decisões que são do dono

Nenhuma delas vai para um agente. Cada decisão, com o contexto dela, está em [`decisoes.md`](decisoes.md).

| Decisão (plano §1) | Estado | O que destrava | Próximo passo |
|---|---|---|---|
| 7 — gastar com a bateria de avaliação | **executada em 25/09** (~US$ 2,57) | 7.4 (`partial`) | Resultado em `relatorio-validacao.md` §11.1. Falta: cache do verificador (#100), medir o ator local com o Ollama no ar e decidir o que fazer com o Haiku (B14 em `estado-atual.md`) |
| 12.3 — qual app novo ganha login e catálogo primeiro | pendente | 12.3 | O dono escolhe entre Outlook, TikTok, Facebook e outros |
| Evolução de desempenho — escalar receita divergida; piloto do renderer; Docker no Windows Server | pendentes (26/09) | 14.4, 14.9, 14.10 | Custo e procedimento em [`relatorio-desempenho.md`](relatorio-desempenho.md); ADR-027 e ADR-028 |
| 6 — hora certa nas duas máquinas | **divergente** | lease de posse, aceites | Conferir com `w32tm /stripchart /computer:time.windows.com /samples:3` nas duas máquinas e registrar. Uma nota de sessão de 23/09, fora do repositório, diz que foi feito; os docs dizem que não foi executado ([ADR-019](decisoes.md)) |
| Autorizações de mundo real | pendentes | seção 3 | Cada ato está listado em `relatorio-validacao.md` §13.1, com o procedimento pronto |

As decisões 1, 2 (sem revogação, 24/09), 3, 4, 5, 8 e 9 já foram tomadas; ver [`decisoes.md`](decisoes.md).

## 2. Implementação pendente

| ID | Objetivo | Escopo que falta | Depende de | Aceite verificável | Próximo passo |
|---|---|---|---|---|---|
| 12.3 | Apps novos operando de verdade | Por app: login determinístico, catálogo de ações e classificador de telas. Hoje esses apps rodam pela IA livre, com login feito pela pessoa no Foco | 12.1, 12.2 (implementados), decisão do dono | Uma execução com efeito no app escolhido, comprovada por pós-condição e sem login feito pela IA | Decisão do dono; depois `preparar-tarefa 12.3` |
| T.2 | Testes do ciclo real do emulador | As sondas internas de `_wait_boot` (boot_completed, ui_ready, prepare_for_automation) sem backend fake; `worker/executor.py` não unificado com `EmulatorBackend` | — | Testes das sondas com aparelho falso, sem regressão da suíte | `preparar-tarefa T.2` (Opus: toca código de boot) |
| 7.4 | Fechar a avaliação | Cache de prompt do verificador em Haiku (49 verificações de 25/09 com `cache_read = 0`); linha de base com o ator LOCAL (a de 25/09 rodou no fallback) | Ollama no ar no central | Verificações com cache lido > 0; `eval-run.ps1` com `decide` no modelo local, comparado a `base-25-09` | Decisão do dono sobre B14/B15; depois `preparar-tarefa 7.4` |
| 8.3 | Sinais e limites do Instagram | O código está feito. Falta o comportamento em aparelho: REPLY_COMMENT e "editar" | aparelho ligado | Responder um comentário num aparelho real com `succeeded` e evidência | Autorização (seção 3) |
| — | Backlog que a documentação encontrou | Ver [`estado-atual.md`](estado-atual.md) § Backlog | — | — | Triagem com o dono |

## 3. Validação pendente

O código está pronto; falta a prova em ambiente real. Nada aqui exige mudar código. Exige **autorização** ou
**infraestrutura**, e os procedimentos já estão escritos.

| ID | O que falta provar | Aceite | Bloqueio | Procedimento |
|---|---|---|---|---|
| 1.3 | `stop` pedido durante uma execução de IA e com outro comando em voo, em aparelho real | 9 | autorização (parque) | [`relatorio-validacao.md`](relatorio-validacao.md) §13.1 |
| 1.4 | Derrubar o túnel no meio de `start`/`reset` e ver o comando fechar depois da reconexão | 6 | autorização (túnel, parque) | [`worker.md`](worker.md), seção "Ensaio do aceite 6" |
| 1.5–1.8 | Reconciliar comando incerto, cancelar e tratar resultado tardio num worker real | 8 | autorização | §13.1 |
| 2.1, 4.2 | Dois workers reais recebendo trabalho pelo mesmo contrato | 5 | **falta a 2ª máquina**, além do central | plano-100 §6 |
| 2.2 | `appium: local` no notebook | — | autorização | `worker.md` |
| 6.1–6.3, 6.5 | App que não é o Instagram, instalado pelo catálogo num remoto | 4 | autorização | §13.1 |
| 8.1 | Personas completas: prova antes e depois | — | gasto de API + PATCH em produção | `python scripts/personas_completar.py --prova …`, depois `--aplicar` e `--prova` de novo |
| 8.2 | Conversa real entre duas contas, com memória reusada na execução seguinte | — | conta real + aparelho | [`relatorio-validacao.md`](relatorio-validacao.md) §12.2 |
| 8.4 | Instagram operando num aparelho remoto: sessão autenticada (senha pela pessoa, pelo portal) e DM ponta a ponta. A instalação nos remotos já foi provada no 6.6 (23/09) | 4 | autorização (conta real) | `scripts/prova-instagram-remoto.ps1` |
| 9.4 | Túnel com a conta `farm-tunel` em vez de Administrator | — | ato no worker, como administrador | `scripts/worker-ssh-restrito.ps1`, reinstalar a tarefa com `-Usuario farm-tunel`, depois `-RemoverChaveDeAdministrador` |
| 10.4 | Worker Linux: `create`, `start`, `stop`, `hibernate`, `wake` e `reset` pelo painel | — | **não há máquina Linux com KVM** | `sudo bash scripts/worker-install.sh --dry-run`, depois `--enroll <token>` |
| 10.5, 11.10, 12.1, 12.2, 13.1–13.3 | Uso real registrado. Hoje só há prova automatizada (`tests`/`unit`, escrita à mão) | — | registro | Exercitar pelo painel e registrar por `aplicar` com `proof: real` (formato em [`claude-plano-100.md`](claude-plano-100.md) § Estado do mecanismo) |
| 14.10 | Evolução de desempenho medida no parque: captura evitada, p50/p95 por papel, imagem na origem no worker remoto, piloto A0′ do renderer | — | autorização (deploy, agente do worker, aparelho de teste) | [`relatorio-desempenho.md`](relatorio-desempenho.md) §7 |
| T.1 | Os nove aceites com prova real depois das fases 0–10 | 1–9 | autorização; o aceite 5 também precisa da 2ª máquina | §13.1, `scripts/aceites-remotos.ps1`, `scripts/test-restart-recovery.ps1` |

**Critério para fechar uma fase** (plano §5, "Fecha quando"): ver a fase em [`plano-100.md`](plano-100.md). Uma fase
só fecha com prova real que registre data, máquina, commit e ids de execução ou comando.

## 4. Como mexer neste roadmap

- **Item novo**:
  1. Crie uma linha `| id | corpo | #refs ou "pedido do dono" | P/M/G/— |` sob a fase em `plano-100.md`.
  2. Inclua o ID num bloco de `.claude/plano-100.json`.
  3. Rode `python scripts/plano-100-pacotes.py`.
  4. Rode `python scripts/claude-plan-100.py check`.

  Sem o passo 2, `check` e `relatorio` quebram. Foi isso que aconteceu com o 10.5.
- **Item fechado**: registre por `aplicar`, tire-o das seções 2 e 3 e acrescente a entrada no
  [`../CHANGELOG.md`](../CHANGELOG.md).
- `python scripts/docs-check.py` confere que os IDs das tabelas daqui existem no plano.

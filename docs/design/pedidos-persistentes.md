# Pedidos persistentes: pesquisa e desenho

Pedido do dono de 29/09/2026: estruturar objetivos que continuem ativos ao longo do tempo — imediatos, agendados,
recorrentes, acionados por evento ou condição, e acompanhamentos em que a persona decide quando e como continuar,
dentro do objetivo e dos limites definidos pela pessoa. As tarefas pertencem ao produto: estado e execução no backend
e nos workers, com continuidade entre reinícios, sem depender da sessão da IDE.

**Estado: desenho completo (itens 26.1–26.8 de [../plano-100.md](../plano-100.md), Fase 26), sem implementação.**
A decisão de partida é o ADR-059 (proposto) em [../decisoes.md](../decisoes.md); o §5 diz o que a pesquisa confirmou
e o que mudou nele. Nada neste documento é prova de ambiente: não houve chamada de IA, aparelho nem banco do central.
O plano da implementação é a **Fase 28 proposta** (§13), que ainda não entrou no plano-100.

Convenção das tabelas: **Existe** = código integrado, citado por arquivo e função (lido em 29/09 sobre `2a822b6`, no
worktree `evo3`); **Em curso** = trabalho não integrado de outra frente no mesmo worktree (Fases 24 e 25), que pode
mudar; **Proposta** = desenho deste documento, sem código.

## 1. O que existe hoje

Não há agendamento, recorrência nem gatilho por evento no produto (busca por `cron`, `schedule`, `recorr`, `agend`,
`next_run` em `backend/app`, 29/09). As peças reaproveitáveis, conferidas no código:

| Peça | Onde (função) | Situação | Uso no pedido |
|---|---|---|---|
| Espera com hora marcada: `retry_wait` + `next_retry_at` | `taskqueue/scheduler.py` `Scheduler._hold`; `taskqueue/repository.py` `Repository.promote` | Existe | "voltar depois" DENTRO de uma execução; não sobrevive ao fim da execução |
| Idempotência de execução: `runs.idempotency_key TEXT NOT NULL UNIQUE` | `migrations/001_init.sql`; `Repository.create_run` devolve a linha existente no conflito | Existe | a identidade da ocorrência vira essa chave |
| Foto dos alvos (`runs.targets`, migração 051) e sucessora com chave derivada | `taskqueue/service.py` `RunService.create`; `taskqueue/assistente.py` `ComandoAssistido.sucessora` (ADR-047) | Existe | repetir com os mesmos alvos; molde de chave determinística |
| Gancho de fim de execução | `Scheduler._settle_run` → `on_run_settled` → `state.py` `AppState._execucao_assentada` | Existe, em processo | caminho rápido para fechar a ocorrência; perde-se numa queda (precisa de varredura) |
| Costuras tipadas (sinais em processo para o livro de aprendizado) | `shared/costuras.py` `avisar`; `taskqueue/costuras.py`; `modules/learning/infrastructure/ligar_costuras.py` | Existe | molde de "assinante interno" tipado; não é durável |
| Eventos gravados com id crescente, replay por `since` | `events.py` `EventBus.emit`, `since`, `replicar_sempre` | Existe | fonte de gatilho, com duas restrições (abaixo) |
| Posse de etapa por prazo com CAS: `claim_step`, `renew_claims`, `take_over`, `abandoned_steps`; `POSSE_TTL_S = 120`, renovação a cada 20 s | `taskqueue/repository.py`; `Scheduler._manter_posse` | Existe | molde de trava por linha |
| Um ativo por aparelho: índice único parcial | `migrations/018_um_ativo_por_aparelho.sql`; `claim_step` trata a violação como "outro assumiu" | Existe | a concorrência se resolve no banco, não na memória |
| Vagas de IA como lease no banco (CAS `WHERE holder IS NULL OR expires_at < ?`) | `migrations/027_hospedeiro_e_vagas_de_ia.sql`; `ai_slots` renovadas em `_manter_posse` | Existe | **molde direto da trava de líder** |
| Quem hospeda o aparelho (`instances.hosted_by`) e planeja a execução (`runs.planned_by`) | `Repository.so_meu`, `dispatchable_objectives`; `RunService.resume_planning_after_restart` (027) | Existe | a ocorrência despachada segue o dono do aparelho |
| Relógio do banco para prazos (`db.prazo_iso`, `db.agora_iso`) e guarda de relógio na partida | `db.py`; `AppState.conferir_relogio` | Existe | "está vencido?" entre backends se pergunta ao banco |
| Outbox transacional de comandos + dreno na partida e a cada 15 s | `migrations/029_outbox_e_origem_do_evento.sql`; `commands/outbox.py`; `AppState._drenar_outbox`, `_laco_do_outbox` | Existe | o padrão já está no projeto |
| Trava consultiva do PostgreSQL atrás de ramo de dialeto | `commands/store.py` (`pg_advisory_xact_lock`); `db.py` (`pg_advisory_lock` na migração) | Existe | opção para o PostgreSQL; o SQLite fica no CAS |
| Papéis `ROLE=all/scheduler/api` (`cfg.roda_scheduler`) | `AppState.start` | Existe | é a ÚNICA coisa que impede laço de fundo em dobro hoje |
| Laços de fundo: outbox 15 s, retenção 6 h, saldos 600 s, curadoria 900 s, loja 60 s, saúde 30 s, métricas | `AppState.start`, `_saldos_loop`, `_curadoria_loop`, `_retention_loop`; `vitrine.py` `laco_de_convergencia` | Existe, **sem trava de líder** | molde de laço tolerante a falha |
| Reconciliação de partida (etapa em curso vira "reobservar", efeito possivelmente disparado vira "só verificar") | `Scheduler.reconcile_after_restart`, `_reconciliar`; `social.reconcile_pending_effects` | Existe | a ocorrência herda: nada reexecuta às cegas |
| Tetos de IA: por execução `ai_max_usd_per_run` (15), por dia `ai_max_usd_per_day` (0 = desligado), chamadas por objetivo, tokens por execução | `config.py` `Settings` | Existe | somam-se ao orçamento do pedido |
| Saldo por conta de IA: aviso e bloqueio ANTES de gastar (`AIError(kind="balance")`) | `ai_billing_accounts` (052), `_saldos_loop` (ADR-051) | Existe | ocorrência adia em vez de falhar |
| Prazo do objetivo `objective_timeout_s` (900 s; teto 14400 s) | `config.py` | Existe | cada ocorrência é curta; o pedido é que dura |
| Memória por perfil, com origem `observation` e validade | `social/memory.py` `MemoryStore.remember`, `absorb_observation`, `recall`, `purge_expired`; `social/observacao.py` | Existe | memória da PERSONA (pessoas); a do PEDIDO é outra (§8) |
| Políticas por capacidade `autonomous`/`approval_required`/`manual_only`/`disabled`; uma conta por alvo em seguir, DM e comentário | `planning/capabilities.py` `POLICIES`; `social/policy.py` `PolicyEngine.check`, `UMA_CONTA_POR_ALVO` (ADR-055) | Existe | o pedido só restringe, nunca afrouxa |
| Aprovações por etapa, expiradas com o objetivo | `social/approvals.py` `ApprovalStore.open`, `decide`, `expire_for_objective` | Existe | aprovação de efeito dentro da ocorrência |
| Livro de aprendizado: lições, falhas em vocabulário fechado, preferências, backlog | `modules/learning/` (ADR-054) | Existe | a ocorrência seguinte aprende com a anterior sem código novo |
| Escolha semântica de personas pelo papel `plan`; recusa de propaganda política e campanha coordenada | `taskqueue/orquestrador.py`, `modules/execution/domain/orquestracao.py` (ADR-050) | Existe | seleção por capacidade; regra de conduta dos casos |
| Valor lido numa etapa usado na seguinte (`step_outputs`, migração 056) | `Repository.save_step_output`, `step_outputs` | **Em curso** (Fase 24, não integrado) | o relatório lê valores estruturados |
| Portão de rede no despacho (`rede_gate`, migração 057) | `Scheduler._tick` | **Em curso** (Fase 25, não integrado) | ocorrência espera a rede verificada |

Duas restrições do código que moldam o desenho, e não só a lista:

1. **Eventos e evidências são purgados.** `AppState._retention_loop` apaga `events`, `ai_calls`, `measurements` e os
   arquivos de evidência mais velhos que `log_retention_days` (14); os tipos de `EPHEMERAL_KINDS` nunca são gravados.
   Um pedido de meses não pode apontar para evento, custo ou captura crus: precisa de cursor próprio, de custo
   acumulado na ocorrência e de observações duráveis dele (o mesmo cuidado do ADR-054, decisão 8).
2. **"Vencido?" hoje usa dois relógios.** A posse compara no relógio do banco, mas `Repository.promote` compara
   `next_retry_at` com `now_iso()` do processo. Tudo o que o pedido agendar usa o relógio do banco.

Lacunas verificadas: entidade de pedido, gatilho e ocorrência; trava de líder dos laços periódicos; dependência e
consolidação entre objetivos e entre personas; relatório com valores de várias execuções; canal de aviso ao dono;
assinante durável de eventos; prioridade na fila (`dispatchable_objectives` ordena só por `runs.created_at`).

## 2. Questões e onde cada uma é resolvida

| # | Questão | Item | Seção |
|---|---|---|---|
| 1 | Modelo do pedido: objetivo, contexto, sucesso, duração, frequência, gatilhos, encerramento, autonomia | 26.2 | §6 |
| 2 | Memória e continuidade entre execuções | 26.4 | §8 |
| 3 | Agendamento confiável: fuso, atraso, perdidos, sobreposição, retomada, cancelamento, tentativas, efeito duplicado | 26.3 | §7 |
| 4 | Planejamento adaptativo: pesquisar de novo, aguardar, trocar de app, pedir colaboração | 26.4 | §8 |
| 5 | Colaboração entre personas | 26.5 | §9 |
| 6 | Recursos e custo | 26.6 | §10 |
| 7 | Experiência | 26.7 | §11 |
| 8 | Integração com o que existe e o que justifica componente novo | 26.8 | §7.1, §13 |

## 3. Casos que orientam (não limitam) a pesquisa

- **Acompanhamento de políticos:** escolher ou esclarecer os perfis, período, frequência de coleta e de relatório;
  preservar as fontes e separar o que a amostra mostra de conclusões sobre o público em geral.
- **Pesquisa de produto:** consulta pontual e acompanhamento de preço — produto exato, custo total, disponibilidade,
  condições, confiabilidade da loja e momento da consulta.
- **Monitoramento de reputação:** detecção, evidência, crítica × alegação factual, verificação, aviso e resposta dentro
  das autorizações; personas com papéis (pesquisa, checagem, preparo de resposta), atuação transparente.

Os três estão desenhados no §12.

## 4. Fontes (26.1)

Todas acessadas em **29/09/2026**, abertas na página (não de memória). "Não abriu" está dito na linha.

### 4.1 Agendamento durável (acesso: 29/09/2026)

| Fonte | O que sustenta |
|---|---|
| [Temporal — Schedule](https://docs.temporal.io/schedule) | Sobreposição `Skip` (padrão), `BufferOne`, `BufferAll`, `CancelOther`, `TerminateOther`, `AllowAll`; janela de recuperação padrão de 1 ano, mínimo 10 s; pausa automática na falha (cancelamento manual não conta); backfill; jitter limitado ao tempo até a próxima; spec por intervalo ou calendário, em UTC por padrão; disparo manual mesmo pausado; limite de ações que, esgotado, age como pausa |
| [Temporal — Activities](https://docs.temporal.io/activities) | Atividades devem ser idempotentes para a nova tentativa não repetir efeito |
| [Temporal — Workflow definition](https://docs.temporal.io/workflow-definition) | Determinismo: mesmas chamadas na mesma ordem no replay; divergência é erro |
| [Temporal — Persistence](https://docs.temporal.io/temporal-service/persistence) | O servidor exige banco próprio (Cassandra, PostgreSQL, MySQL); SQLite "só desenvolvimento e teste" |
| [Temporal — Deployment](https://docs.temporal.io/self-hosted-guide/deployment) | Dois binários Go (Server e UI); exemplo com PostgreSQL e Elasticsearch |
| [APScheduler 3.x — guia](https://apscheduler.readthedocs.io/en/3.x/userguide.html) | Job stores (`SQLAlchemyJobStore`), `coalesce`, `max_instances`; "Job stores must never be shared between schedulers" |
| [APScheduler 3.x — FAQ](https://apscheduler.readthedocs.io/en/3.x/faq.html) | Compartilhar o store entre processos: "You can't" — execução duplicada ou job perdido; saída sugerida: processo dedicado acessado por RPC |
| [APScheduler 3.x — código `base.py`](https://github.com/agronholm/apscheduler/blob/3.x/src/apscheduler/schedulers/base.py) | Padrões `misfire_grace_time=1`, `coalesce=True`, `max_instances=1` |
| [APScheduler 4 — guia](https://apscheduler.readthedocs.io/en/master/userguide.html) e [histórico](https://apscheduler.readthedocs.io/en/master/versionhistory.html) | `SQLAlchemyDataStore` (PostgreSQL, SQLite…), vários schedulers no mesmo store com event broker compartilhado; `CoalescePolicy` `latest`/`earliest`/`all`; `max_running_jobs`; a série 4 é pré-lançamento, "não usar em produção" |
| [PyPI — APScheduler](https://pypi.org/project/APScheduler/) | Estável: 3.11.3 (28/06/2026); 4.0.0a6 (27/04/2025) ainda alfa, desde 2022 |
| [POSIX — crontab](https://pubs.opengroup.org/onlinepubs/9799919799/utilities/crontab.html) | Cinco campos; dia do mês OU dia da semana; sem fuso nem horário de verão |
| [cronie — cron(8)](https://man7.org/linux/man-pages/man8/cron.8.html) e [crontab(5)](https://man7.org/linux/man-pages/man5/crontab.5.html) | Mudança de relógio < 3 h: pulado roda na hora, repetido não roda duas vezes; com `CRON_TZ`, hora inexistente nunca casa e hora repetida roda duas vezes — o comportamento que o pedido NÃO quer |

### 4.2 Recorrência e fuso (acesso: 29/09/2026)

| Fonte | O que sustenta |
|---|---|
| [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545) (texto em `rfc5545.txt`) | §3.3.10 RECUR: `FREQ` obrigatório, `INTERVAL` padrão 1, `UNTIL` e `COUNT` exclusivos, `BYDAY`, `BYHOUR`, `BYSETPOS`, `WKST`; instância em data inválida ou hora local inexistente é ignorada e não conta. §3.3.5 forma 3 (hora local com `TZID`): hora repetida = primeira ocorrência; hora inexistente = deslocamento de antes do salto |
| [dateutil — rrule](https://dateutil.readthedocs.io/en/stable/rrule.html) | Implementação da RFC 5545 em Python (`rrule`, `rruleset`, `rrulestr`); pula data inválida |
| [IANA — Time Zone Database](https://www.iana.org/time-zones) | Base tz mantida pela IANA; versão 2026d (11/09/2026); atualizada quando governos mudam regras |
| [tz-link](https://data.iana.org/time-zones/tz-link.html) | Processo da RFC 6557; sem calendário fixo; pede aos governos 1 ano de antecedência |
| [Python — zoneinfo](https://docs.python.org/3/library/zoneinfo.html) | Base do sistema ou pacote `tzdata`; "notably Windows" não tem base IANA — declarar `tzdata` como dependência; `fold` em hora ambígua |
| [PEP 615](https://peps.python.org/pep-0615/) e [PEP 495](https://peps.python.org/pep-0495/) | Ordem de busca (`TZPATH`, depois `tzdata`; vazio no Windows); `fold=0` antes da transição, `fold=1` depois |
| [Decreto 9.772/2019 (Câmara, legin)](https://www2.camara.leg.br/legin/fed/decret/2019/decreto-9772-25-abril-2019-788024-norma-pe.html) | "Encerra a hora de verão no território nacional". O planalto.gov.br não abriu |
| [MME — horário de verão](https://www.gov.br/mme/pt-br/assuntos/secretarias/secretaria-nacional-energia-eletrica/horario-de-verao) | Suspensão desde 2019; o MME reavalia "periodicamente" no CMSE. As notícias oficiais de 2024–2025 não abriram (conteúdo restrito em período eleitoral): a volta **não** foi confirmada em fonte oficial, e a regra precisa estar pronta para ela |

### 4.3 Filas, travas, idempotência (acesso: 29/09/2026)

| Fonte | O que sustenta |
|---|---|
| [PostgreSQL — SELECT, cláusula de trava](https://www.postgresql.org/docs/current/sql-select.html) | `SKIP LOCKED` pula linhas travadas; visão inconsistente, própria para tabela-fila com vários consumidores |
| [PostgreSQL — advisory locks](https://www.postgresql.org/docs/current/explicit-locking.html) e [funções](https://www.postgresql.org/docs/current/functions-admin.html) | Sessão × transação (`pg_advisory_xact_lock` solta no fim da transação); `pg_try_*` não esperam; soltas quando a sessão cai; cuidado com `LIMIT` depois da trava |
| [SQLite — transações](https://www.sqlite.org/lang_transaction.html) e [WAL](https://www.sqlite.org/wal.html) | `BEGIN IMMEDIATE` pega a escrita na hora (`SQLITE_BUSY` se outro escreve); um escritor por vez; WAL só na mesma máquina |
| [SQLite — locking v3](https://www.sqlite.org/lockingv3.html), [SELECT](https://www.sqlite.org/lang_select.html), [rede](https://www.sqlite.org/useovernet.html) | Trava por banco, não por linha; a gramática não tem `FOR UPDATE`/`SKIP LOCKED` (inferido pela ausência); nada de SQLite em disco de rede — para várias máquinas, banco cliente/servidor |
| [microservices.io — Transactional outbox](https://microservices.io/patterns/data/transactional-outbox.html) | Mensagem gravada na mesma transação; o relay pode publicar duas vezes, então o consumidor é idempotente |
| [microservices.io — Idempotent consumer](https://microservices.io/patterns/communication-style/idempotent-consumer.html) | Id processado gravado na transação do negócio, com chave única que recusa o duplicado |
| [Stripe — idempotent requests](https://docs.stripe.com/api/idempotent_requests) | Chave até 255 caracteres, sem dado sensível; guarda a primeira resposta; parâmetros diferentes com a mesma chave = erro; poda depois de 24 h |
| [IETF — Idempotency-Key (draft-07)](https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header-07) | 422 para chave reutilizada com corpo diferente, 409 para repetição concorrente. Rascunho expirado, não é norma |
| [Kubernetes — Leases](https://kubernetes.io/docs/concepts/architecture/leases/) | Eleição de líder por lease (`holderIdentity`, `leaseDurationSeconds`, `renewTime`): um ativo, os outros em espera |
| [Kleppmann — distributed locking](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html) | Trava por eficiência × por correção; pausa longa vence o lease sem o dono saber; defesa: fencing token crescente conferido na escrita |

### 4.4 Agentes de longa duração e colaboração (acesso: 29/09/2026)

| Fonte | O que sustenta |
|---|---|
| [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) (19/12/2024) | Workflow (caminho no código) × agente (o modelo dirige); a solução mais simples; orchestrator-workers e evaluator-optimizer; agentes acumulam erro e pedem condição de parada |
| [Anthropic — Multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) (13/06/2025) | Líder + subagentes; ~4× os tokens de um chat com agente e ~15× com vários; plano salvo em memória externa; retomar do ponto do erro; cada subagente com objetivo, formato, fontes e limites, senão duplica trabalho |
| [Anthropic — Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) (26/11/2025) | Progresso em arquivo estruturado (JSON) relido no início de cada sessão; falha típica: marcar concluído sem testar |
| [Anthropic — Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) (29/09/2025) | Notas estruturadas, compactação, subagentes devolvendo resumos curtos; desempenho cai com contexto grande |
| [LangGraph — Persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers), [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | Estado por `thread_id`; checkpoint por passo; o que terminou não reexecuta; `interrupt()` espera a pessoa sem prazo e o que vem antes dele deve ser idempotente. As URLs antigas (`langchain-ai.github.io`) só redirecionam |
| [OpenAI Agents SDK — Handoffs](https://openai.github.io/openai-agents-python/handoffs/), [Guardrails](https://openai.github.io/openai-agents-python/guardrails/), [Multi-agent](https://openai.github.io/openai-agents-python/multi_agent/), [Running agents](https://openai.github.io/openai-agents-python/running_agents/) | Handoff × agente como ferramenta (o gerente mantém o controle); `max_turns` e `MaxTurnsExceeded`; guardrail que para a execução. O PDF "A practical guide to building agents" não abriu |
| [AutoGen — Termination](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/termination.html) | Sem condição de término a conversa pode não parar; `MaxMessageTermination`, `TokenUsageTermination`, `TimeoutTermination`, combináveis |
| [Microsoft Agent Framework — Group chat](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/group-chat) e [visão geral](https://learn.microsoft.com/en-us/agent-framework/overview/) | Sucessor do AutoGen e do Semantic Kernel; `max_rounds` e `termination_condition` como limite rígido mesmo com orquestrador por IA; "escreva uma função quando uma função resolve" |

### 4.5 Os casos: checagem, amostra, preço, conduta (acesso: 29/09/2026)

| Fonte | O que sustenta |
|---|---|
| [IFCN — Code of Principles](https://ifcncodeofprinciples.poynter.org/the-commitments) | Apartidarismo, transparência de fontes (reproduzível, primária antes de secundária), de financiamento e de metodologia; correções abertas |
| [Lupa — como checamos](https://www.agencialupa.org/acontecendo-na-lupa/2015/10/15/como-fazemos-nossas-checagens/) (atualizada 18/11/2025) | "A Lupa não checa opiniões"; checável: dado, comparação, legalidade; previsão não. A metodologia do Aos Fatos não abriu (404) |
| [Lupa — etiquetas](https://www.agencialupa.org/acontecendo-na-lupa/2015/10/15/entenda-nossas-etiquetas/) (atualizada 04/02/2026) | Verdadeiro, Falso, Falta contexto, Exagerado, Subestimado, Contraditório, Insustentável |
| [Pew — Sizing Up Twitter Users](https://www.pewresearch.org/internet/2019/04/24/sizing-up-twitter-users/) (2019) | Usuários diferem do público (mais jovens, mais escolarizados); 10% produzem 80% das postagens. Dado dos EUA |
| [Pew — Social Media Fact Sheet](https://www.pewresearch.org/internet/fact-sheet/social-media/) (20/11/2025) | Uso de cada rede varia muito por idade (Instagram 50% dos adultos dos EUA). O total do X na leitura veio incoerente e não é usado aqui |
| [AAPOR — Non-probability sampling](https://aapor.org/wp-content/uploads/2022/11/NPS_TF_Report_Final_7_revised_FNL_6_22_13-1.pdf) (2013) | Inferência de amostra não probabilística depende de premissas explícitas; mais dever de descrever o método |
| [AoIR — Ethics 3.0](https://aoir.org/reports/ethics3.pdf) (2019) | Minimização de dados; integridade contextual; mais cuidado quanto mais vulnerável a pessoa |
| [LGPD — Lei 13.709/2018](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm) | Art. 6º finalidade e necessidade; art. 7º §3º, §4º e §7º: dado público ou tornado público pelo titular segue sujeito à finalidade e aos princípios |
| [Decreto 7.962/2013](https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2013/decreto/d7962.htm) | Loja online informa CNPJ, endereço, preço com despesas adicionais discriminadas (entrega, seguro), condições e restrições |
| [CDC — Lei 8.078/1990, art. 49](https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm) | Arrependimento em 7 dias na compra fora do estabelecimento |
| [Procon-SP — Evite esses sites](https://sistemas.procon.sp.gov.br/evitesite/list/evitesites.php) | Lista de lojas notificadas que não responderam ou não foram achadas (atualizada 12/05/2026) |
| [consumidor.gov.br](https://www.consumidor.gov.br/pages/conteudo/publico/1) | Índices públicos por empresa (solução, satisfação, prazo); adesão voluntária — ausência não diz nada |
| [Meta — Inauthentic behavior](https://transparency.meta.com/policies/community-standards/inauthentic-behavior/) (12/12/2025) | Proíbe redes de ativos inautênticos e comportamento inautêntico coordenado |
| [TSE — Res. 23.732/2024](https://www.tse.jus.br/legislacao/compilada/res/2024/resolucao-no-23-732-de-27-de-fevereiro-de-2024) e [Res. 23.755/2026](https://www.tse.jus.br/legislacao/compilada/res/2026/resolucao-no-23-755-de-2-de-marco-de-2026) | Conteúdo sintético rotulado; proibido simular conversa com candidato; vedação ampliada perto do pleito de 2026 |

Leituras com leitor de texto intermediário (o endereço oficial não abriu direto): Planalto, AAPOR, AoIR, TSE. As
citações usadas aqui são paráfrases; a conferência palavra por palavra fica para quem for citá-las num relatório.

## 5. Ponto de partida (ADR-059), revisto pela pesquisa

| Ponto do ADR-059 | O que a pesquisa diz | Fica |
|---|---|---|
| 1. Estado e execução no backend e nos workers, sem a IDE | Temporal, LangGraph e o harness da Anthropic põem o estado fora do processo e retomam do ponto; o projeto já faz isso por etapa (posse, reconciliação) | Confirmado |
| 2. Pedido → ocorrência → execução (`runs`), identidade da ocorrência como chave de idempotência | É o "idempotent consumer" com chave única (microservices.io, Stripe); o `runs.idempotency_key UNIQUE` já cumpre. Acréscimo: a ocorrência também tem chave única própria (§6.3) e a nova tentativa é outra execução com sufixo | Confirmado, detalhado |
| 3. Vocabulário de sistemas consolidados; biblioteca só se a pesquisa indicar | APScheduler 3 não aceita store compartilhado; o 4 é alfa desde 2022; Temporal exige servidor e banco próprios. **Recomendação: laço próprio** com o vocabulário de Temporal e APScheduler (§7) | Confirmado; decisão tomada |
| 4. Colaboração é divisão interna; para fora, uma conta por alvo, aprovação, nada de simular apoio | Meta proíbe comportamento inautêntico coordenado; ADR-050 já recusa campanha coordenada de opinião; AutoGen e Agent Framework exigem condição de término | Confirmado, com limites de delegação (§9) |

Propõe-se registrar a implementação num ADR novo (o próximo número livre em todos os branches, provavelmente
ADR-060) quando a Fase 28 começar; este documento não edita o ADR-059.

## 6. Modelo do pedido (26.2) — proposta

### 6.1 Entidades

```
pedido ──1:N── gatilho ──(gera)──> ocorrência ──1:N── execução (runs) ──> objetivos, etapas, tentativas (existem)
   │                                   │
   ├── memória do pedido (§8)          └── observações duráveis e custo acumulado
   ├── relatório (período)
   └── dependências (pedido pai/filho, §9)
```

| Entidade | Tabela proposta | Campos principais | Observação |
|---|---|---|---|
| Pedido | `pedidos` | `id`, `titulo`, `objetivo` (texto-base do comando, sem destino nem segredo — mesma recusa de `RunService.create`), `contexto`, `criterios_sucesso` (lista verificável), `alvos` (foto no formato de `runs.targets`), `autonomia` (§6.4), `fuso` (IANA), `inicio_em`, `fim_em`, `max_ocorrencias`, `orcamento_total_usd`, `orcamento_ocorrencia_usd`, `sobreposicao`, `janela_recuperacao_s`, `coalescer`, `estado`, `versao`, `proxima_em` (UTC, cache), `criado_por`, `pausado_motivo`, `encerrado_motivo`, `pai_id` | `versao` sobe a cada edição; nenhuma ocorrência muda de versão depois de despachada |
| Gatilho | `pedido_gatilhos` | `id`, `pedido_id`, `tipo` (`agora`, `horario`, `recorrencia`, `evento`, `condicao`, `persona`), `spec` (JSON: `dtstart` local + `rrule`; ou tipo de evento + filtro; ou condição), `cursor` (último evento lido ou última avaliação), `ativo` | um pedido pode ter vários (recorrência + condição) |
| Ocorrência | `pedido_ocorrencias` | `id`, `pedido_id`, `pedido_versao`, `gatilho_id`, `previsto_para` (UTC), `chave` (UNIQUE), `origem` (`agenda`, `recuperacao`, `evento`, `condicao`, `persona`, `manual`, `backfill`), `estado`, `tentativa`, `run_id`, `motivo`, `custo_usd`, `resumo`, `criada_em`, `iniciada_em`, `terminada_em`, `dono`, `prazo_posse` | UNIQUE (`pedido_id`, `gatilho_id`, `previsto_para`) além da `chave` |
| Execução | `runs` (existe) + `pedido_id`, `ocorrencia_id`, `prioridade` | a ocorrência chama `RunService.create` com `idempotency_key = chave` | nada muda no planejador, executor ou verificador |
| Observação | `pedido_observacoes` | `pedido_id`, `ocorrencia_id`, `fonte` (perfil, URL, loja), `capturado_em`, `tipo`, `valor` estruturado, `trecho` curto, `sha256` da captura, `run_id`/`step_id` de origem | durável; não depende da retenção de evidência (§1) |
| Relatório | `pedido_relatorios` | `pedido_id`, `periodo_de`, `periodo_ate`, `conteudo` (JSON: observações, conclusões, amostra, limites), `gerado_por` (`deterministico` ou papel de IA), `custo_usd`, `versao_pedido` | §6.6 |
| Trava | `travas` | `nome` PK, `dono`, `expira_em`, `token` | §7.3; serve também aos laços que já existem |

### 6.2 Estados e transições

Pedido:

| De | Para | Quem | Condição |
|---|---|---|---|
| `rascunho` | `ativo` | pessoa | prévia dos alvos (ADR-044) e das próximas 5 ocorrências confirmada |
| `ativo` | `pausado` | pessoa ou sistema | sistema pausa por: N falhas seguidas (`pausa_por_falha`), orçamento esgotado, conta da persona `blocked` (ADR-055), gatilho sem cursor válido; motivo sempre gravado |
| `pausado` | `ativo` | pessoa | escolhe "retomar daqui" (perdidas ficam `puladas`) ou "recuperar dentro da janela" |
| `ativo` | `aguardando_pessoa` | sistema | aprovação pendente que bloqueia a próxima ocorrência, pergunta (`needs_input`), ocorrência `incerta` |
| `aguardando_pessoa` | `ativo` | pessoa | respondeu; a resposta vira sucessora da execução (ADR-047) e nota na memória do pedido |
| `ativo` | `concluido` | sistema, conferido | critério de sucesso comprovado pelo verificador (nunca pela palavra da IA) |
| `ativo`/`pausado` | `encerrado` | sistema | `fim_em` passou, `max_ocorrencias` atingido, orçamento total gasto |
| qualquer não terminal | `cancelado` | pessoa | a execução em curso recebe o cancelamento de sempre (`RunService.cancel`) |

Ocorrência: `prevista` → `devida` → `despachada` (execução criada) → `rodando` → `concluida` | `falhou` | `incerta` |
`cancelada`; e os fins sem execução: `pulada` (sobreposição, pausa, coalescida, orçamento) e `perdida` (fora da janela
de recuperação). `falhou` pode gerar nova tentativa (§7.6); `incerta` nunca gera.

### 6.3 Identidade e idempotência

- `chave = "ped:" + pedido_id + ":" + gatilho_id + ":" + previsto_para_utc` (ISO sem fração). Manual e backfill usam o
  instante do pedido do dono, arredondado ao segundo, mais a origem.
- A execução da tentativa `n` usa `idempotency_key = chave + ":t" + n`. Repetir a criação (queda entre criar a
  execução e marcar a ocorrência) devolve a MESMA execução (`create_run` no conflito) — consumidor idempotente.
- A chave não carrega dado da pessoa nem do alvo (Stripe: nada sensível na chave).
- Quem materializa usa `INSERT … ON CONFLICT DO NOTHING` (portável nos dois bancos): dois backends, dois laços ou um
  reinício no meio produzem uma ocorrência só.

### 6.4 Grau de autonomia

O pedido escolhe um teto; a política da persona por capacidade (`POLICIES`) continua valendo. Vale o **mais
restritivo** dos dois — o pedido nunca afrouxa a persona.

| Autonomia do pedido | Capacidades sem efeito externo (ler, abrir, buscar, observar) | Com efeito (curtir, seguir, comentar, DM, publicar, comprar) |
|---|---|---|
| `observar` | persona decide sozinha | recusado na criação e no plano (a etapa vira `disabled`) |
| `preparar` | sozinha | vira rascunho com aprovação (`approval_required`), mesmo se a persona fosse `autonomous` |
| `agir` | sozinha | política da persona, `PolicyEngine.check`, frota e uma conta por alvo (ADR-055) |

A persona decide sozinha, dentro do teto: quando voltar (entre `intervalo_min` e `intervalo_max`), repetir a busca,
trocar de app ENTRE os apps listados no pedido, reusar observação fresca, encerrar por critério comprovado.
Exige aprovação da pessoa: qualquer efeito acima do teto; subir orçamento; estender `fim_em`; alvo, conta ou app
novo; contato com quem não tem conversa prévia (já é regra do ADR-055); criar sub-pedido com efeito (§9).
Nunca, em nenhum grau: compra ou pagamento, desafio/CAPTCHA/2FA (ADR-009), tela de conta travada (ADR-055),
propaganda política ou campanha coordenada (ADR-050).

### 6.5 Encerramento

Um pedido termina por um motivo registrado: critério comprovado, prazo, contagem, orçamento, cancelamento, ou
`abandonado` (pausado há mais de `abandono_dias`, padrão 30, sem gesto — vira encerrado com aviso). Encerrar gera o
relatório final (§6.6) e grava o custo total antes de qualquer purga.

### 6.6 Relatório

Gerado ao fim de cada período declarado (diário, semanal) e no encerramento, **sem IA por padrão**: tabela das
observações do período com fonte e instante, o que mudou desde o anterior, pendências e custo. Um resumo em texto
pelo papel `plan` é opcional, com teto por relatório e contado no orçamento. Todo relatório tem três blocos que não se
misturam: **observado** (com fonte), **conclusão** (dita como inferência, com o alcance da amostra) e **não coberto**.

## 7. Agendamento confiável (26.3)

### 7.1 Alternativas

| Critério deste projeto | A. Laço próprio: tabela + CAS por linha + trava de líder | B. APScheduler (3.11 estável; 4.0 alfa) | C. Temporal (servidor + SDK Python) |
|---|---|---|---|
| SQLite e PostgreSQL | sim; só SQL portável já usado (`ON CONFLICT`, `UPDATE … WHERE` com CAS) | 3.x: `SQLAlchemyJobStore` nos dois, mas **store não pode ser compartilhado**; 4.x: store compartilhado com broker (PostgreSQL via asyncpg/psycopg), **alfa** | servidor exige banco próprio; SQLite só para desenvolvimento |
| Vários backends no mesmo banco (`hosted_by`, `ROLE`) | cabe no modelo de posse que já existe | 3.x: um processo agendador dedicado + RPC (FAQ); 4.x: sim, alfa | sim, é o ponto forte |
| Windows, sem Docker | sim | sim | dois binários Go e um banco a mais para operar e fazer backup |
| Dependências novas | nenhuma além de uma biblioteca de RRULE (ou subconjunto próprio); `tzdata` está no `requirements.txt` (2026.4 = base 2026d), mas só indireto (via `psycopg`): declarar em `requirements.in` | SQLAlchemy + APScheduler | servidor, UI, SDK `temporalio`, banco |
| Encaixe com o motor de execução | a ocorrência cria uma `runs` comum; reconciliação, posse, efeito e aprovação ficam como estão | o job só "chama" a criação da execução; a durabilidade da execução continua sendo nossa | tentação de reescrever o executor em Workflow; o determinismo exigido não combina com IA e tela |
| Vocabulário (sobreposição, janela, coalescer, pausa, backfill) | adotado por nome | nativo (parcial no 3.x) | nativo e completo |
| Custo de erro | nosso código; testável com relógio injetado nos dois bancos | comportamento em várias réplicas fora do suportado (3.x) ou instável (4.x) | operação de um sistema distribuído a mais para um dono só |

**Recomendação: A, laço próprio.** O que decide é o ambiente: vários backends num banco que pode ser SQLite, Windows e
poucas dependências. A biblioteca madura (APScheduler 3) proíbe exatamente o uso compartilhado de que o projeto
precisa, a que permite (4.0) é alfa há quatro anos, e o Temporal traria um servidor e um banco novos para resolver o
que a posse por CAS já resolve aqui três vezes (`claim_step`, `take_over`, `ai_slots`). O agendamento em si é pequeno
(calcular a próxima data e inserir uma linha idempotente); a parte difícil — executar, reconciliar e não repetir
efeito — já existe e fica onde está. Reavaliar se o APScheduler 4 sair estável ou se a operação passar a ter vários
hospedeiros com PostgreSQL gerenciado.

### 7.2 O laço de pedidos

- `AppState.start` ganha `_pedidos_loop`, só onde `cfg.roda_scheduler`, a cada `pedidos.tick_s` (padrão 15 s) e
  acordado por `wake()` quando um pedido é criado ou editado.
- Cada volta, sob a trava `pedidos` (§7.3):
  1. **Materializar:** para cada pedido `ativo` com `proxima_em <= agora_do_banco`, calcula os instantes devidos desde
     a última materialização, aplica janela e coalescência (§7.5) e insere as ocorrências (`ON CONFLICT DO NOTHING`);
     avança `proxima_em` com CAS em `versao`.
  2. **Despachar:** para cada ocorrência `devida`, aplica sobreposição (§7.4), orçamento e saldo (§10); cria a
     execução por `RunService.create` com a chave; marca `despachada` com CAS (`WHERE estado='devida'`).
  3. **Fechar:** o caminho rápido é o `on_run_settled` (em processo); o durável é a varredura das ocorrências
     `despachada`/`rodando` cuja execução é terminal — cobre a queda entre o fim da execução e o gancho.
  4. **Avaliar gatilhos** de evento e condição (§7.8).
- O despacho no aparelho continua sendo do `Scheduler._tick`: a ocorrência vira execução comum e passa por todas as
  portas (conta bloqueada, rede, app, sessão, worker, teto por servidor). O laço de pedidos nunca toca aparelho.

### 7.3 Trava de líder (também para os laços de hoje)

- Tabela `travas(nome, dono, expira_em, token)`. Tomar: `UPDATE travas SET dono=?, expira_em=prazo, token=token+1
  WHERE nome=? AND (dono=? OR expira_em < agora)`; renovar no mesmo `_manter_posse` (20 s), prazo de 120 s — os
  números da posse de etapa, pelo mesmo motivo (errar para o lado de não haver dois).
- Relógio do banco (`db.prazo_iso`, `db.agora_iso`) nas duas pontas.
- **A trava é por eficiência, a correção vem das chaves únicas** (Kleppmann): se dois líderes coexistirem por uma
  pausa longa, a `chave` e o `idempotency_key` recusam o duplicado. O `token` vai gravado na ocorrência
  (`materializada_token`) e o avanço de `proxima_em` confere `versao`, o que serve de cerca.
- Os laços que existem hoje e não são idempotentes por construção passam a tomar trava própria: saldos (`saldos`, que
  consulta API externa), curadoria (`curadoria`), retenção (`retencao`). Não precisa: outbox (filtrado por
  `hosted_by`), loja (por aparelho hospedado), saúde e métricas (por processo).
- PostgreSQL poderia usar `pg_try_advisory_lock`, mas a trava de sessão depende da conexão viva e o SQLite não tem
  equivalente; a tabela é uma implementação só, testada nos dois bancos.

### 7.4 Sobreposição

| Política (nome do Temporal) | Uso no pedido | Padrão |
|---|---|---|
| `Skip` | a nova vira `pulada` com motivo "a anterior ainda roda" | **padrão**, e a única para `agir` |
| `BufferOne` | guarda uma, despacha quando a anterior fecha | permitido em `observar`/`preparar` |
| `AllowAll` | várias ao mesmo tempo | só `observar`, e o teto de aparelhos continua valendo |
| `CancelOther`/`TerminateOther` | — | **não oferecidas**: cancelar uma execução com efeito possivelmente disparado cria o estado `incerta` |

### 7.5 Atraso, perdidos e coalescência

- **Janela de recuperação** (`janela_recuperacao_s`, o `misfire_grace_time` do APScheduler e a catchup window do
  Temporal): padrão por tipo — 30 min para recorrência diária ou maior, metade do intervalo para as menores, zero para
  `horario` com efeito. Fora dela a ocorrência nasce `perdida`, registrada e visível; nunca some.
- **Coalescer** (padrão ligado, `latest`): depois de uma queda longa, só a mais recente dentro da janela roda; as outras
  ficam `pulada: coalescida`.
- **Backfill**: gesto da pessoa, com prévia de quantas execuções e custo estimado; só `observar`.
- **Atraso na fila**: a ocorrência despachada espera como qualquer execução; se passar de `prazo_inicio_s` sem
  começar (aparelho, rede, saldo), a execução é cancelada antes da primeira etapa e a ocorrência vira `perdida` com o
  motivo tipado (`wait_reason` já existe no objetivo).

### 7.6 Tentativas e efeito duplicado

- Dentro da execução, as tentativas por etapa são as de hoje (`max_attempts`, `retry_wait`).
- Por ocorrência: nova tentativa só quando a execução falhou **sem** ação com `effect_possible` e sem etapa `uncertain`
  (a mesma regra de `_reconciliar`: efeito possivelmente disparado só se verifica). Atraso exponencial com teto e
  `max_tentativas` (padrão 2).
- `incerta` para o pedido em `aguardando_pessoa` até a pessoa resolver; nunca repete sozinha.
- N falhas seguidas (padrão 3) pausam o pedido (o `pause-on-failure` do Temporal), com aviso.
- O efeito externo tem uma segunda cerca fora da ocorrência: a política social conta tentativa e efeito confirmado
  igual (`CONTAM`) e aplica uma conta por alvo; uma ocorrência repetida por engano esbarra nela.

### 7.7 Fuso, recorrência e horário de verão

- O pedido guarda o fuso IANA (padrão `America/Sao_Paulo`, editável) e a recorrência como `DTSTART` em hora local +
  subconjunto da `RRULE` da RFC 5545: `FREQ` (`HOURLY`, `DAILY`, `WEEKLY`, `MONTHLY`), `INTERVAL`, `BYDAY`, `BYHOUR`,
  `BYMINUTE`, `BYMONTHDAY`, `COUNT` ou `UNTIL`. Nada de `SECONDLY`/`MINUTELY` (piso de frequência, §10).
- As datas são geradas em hora local ingênua e localizadas com `zoneinfo`; o banco guarda UTC. `tzdata` está
  instalado hoje só como dependência indireta do `psycopg`; a Fase 28 o declara em `requirements.in` (necessário no
  Windows, conforme a doc do `zoneinfo`).
- **Hora inexistente** (salto da primavera): a RFC manda ignorar a instância da recorrência; o pedido **desvia de
  propósito** e a executa no primeiro instante válido depois do salto — um relatório diário não pode sumir num dia.
  O desvio fica documentado na API.
- **Hora repetida** (fim do horário de verão): uma execução só, na primeira (`fold=0`, RFC §3.3.5), ao contrário do
  `CRON_TZ` do cronie, que roda duas vezes.
- O Brasil não tem horário de verão desde o Decreto 9.772/2019, mas o MME o reavalia periodicamente: as regras vêm da
  base IANA atualizada (`tzdata` declarado), nunca de deslocamento fixo `-03:00`. A volta vira atualização
  de pacote, e as próximas ocorrências são recalculadas no deploy.
- A biblioteca de RRULE (`python-dateutil`) não está instalada; **decidido no 28.3: gerador próprio** (`backend/app/modules/pedidos/domain/recorrencia.py`, módulo puro com relógio injetado), com o subconjunto acima e testes de fronteira (`tests/test_pedidos_recorrencia.py`: `America/Sao_Paulo` em 2018/2019, `America/New_York`, `Europe/Lisbon`). O parser recusa o resto (SECONDLY/MINUTELY, YEARLY, BYSETPOS, WKST diferente de MO, `BYDAY` com ordinal) com mensagem clara. Convenções iguais às do dateutil: `DTSTART` só é ocorrência se casar com a regra; dia que não existe no mês é ignorado e não conta. **Desvio da hora inexistente**: vai para o primeiro instante válido depois do salto (o da transição, 03:00 em Nova York), não para o 03:30 que o §3.3.5 da RFC daria; entra no `COUNT`, e se cair no mesmo instante de outra ocorrência da regra elas viram uma só. `Instante.desviado` e `Instante.repetido` marcam os dois casos para a API documentar. HOURLY avança em hora de relógio local: no fim do horário de verão a lacuna entre duas ocorrências é de duas horas reais. `tzdata` agora está declarado em `requirements.in`.

### 7.8 Gatilhos por evento e por condição

- **Evento:** o gatilho guarda `cursor` (último `events.id` lido) e só aceita tipos persistidos (fora de
  `EPHEMERAL_KINDS`). Se o `cursor` ficar abaixo do menor id existente (a retenção passou), registra "eventos perdidos
  pela retenção" e reconcilia pelo estado (consulta da tabela dona do fato), sem inventar disparo.
- **Condição:** expressão determinística sobre dados do pedido (ex.: "último preço observado < R$ X", "nova
  publicação desde a última coleta"), avaliada no fechamento de cada ocorrência de observação — não custa execução nem
  IA. Condição que exige olhar o mundo é uma recorrência de observação + condição.
- **Persona** ("volto quando fizer sentido"): no fechamento, o plano da ocorrência pode propor `proxima_visita`, que o
  código prende a [`intervalo_min`, `intervalo_max`] e ao orçamento; vira ocorrência de origem `persona` com a mesma
  chave determinística.

### 7.9 Cancelamento, pausa, edição e retomada

- Cancelar o pedido: ocorrências futuras `canceladas`; a execução em curso recebe `RunService.cancel` (o caminho de
  `cancel_requested` que o `_tick` já trata); nenhuma ocorrência nova nasce depois do CAS de estado.
- Pausar: para de materializar; retomar oferece "daqui para frente" (padrão) ou "recuperar dentro da janela".
- Editar: `versao + 1`; as ocorrências `prevista`/`devida` passam à versão nova NA MESMA LINHA (nunca canceladas e
  recriadas: a chave não tem versão, e a nova seria engolida pelo `ON CONFLICT DO NOTHING`). Mudar a recorrência ou o
  horário desativa o gatilho (as `prevista`/`devida` dele viram `cancelada`, motivo `edição`) e cria um gatilho novo, com
  id e, portanto, chaves novas. As despachadas terminam na versão em que nasceram (28.4, D5; ADR-066).
- Reinício do backend: nada novo — o laço relê `proxima_em`, as ocorrências e as execuções do banco; o que estava
  `despachada` sem execução é recriado pela chave (devolve a mesma, se existir).

## 8. Memória e planejamento adaptativo (26.4)

### 8.1 O que o pedido guarda entre ocorrências

| Tipo | Exemplo | Origem | Validade |
|---|---|---|---|
| `progresso` | critérios atendidos e faltantes | verificador (etapa `verified`) | até mudar |
| `descoberta` | "a loja X mostra frete só no carrinho" | etapa comprovada; lição do livro (ADR-054) | até refutada |
| `decisao` | "troquei o Instagram pelo navegador: o app pedia login" | plano da ocorrência | registro, não expira |
| `pendencia` | "perfil Y privado: preciso que a pessoa esclareça" | pergunta ou bloqueio | até resolvida |
| `fonte` | perfil, URL, loja já visitados, com último `sha256` | observação | reaproveitamento (§10) |

- Tabela `pedido_memoria` (proposta), separada de `memory_items`: a memória da persona é sobre PESSOAS e vai ao
  prompt de conversa; a do pedido é sobre a TAREFA. Mesmas regras de `social/memory.py`: só fato confirmado, segredo
  recusado por formato, texto de terceiros marcado como dado e não instrução.
- Entra no plano de cada ocorrência como bloco estruturado curto (JSON, como o arquivo de progresso do harness de longa
  duração da Anthropic), com teto de tokens; o excesso é compactado sem IA (último estado por chave, pendências
  abertas, as N descobertas mais usadas).

### 8.2 Decisões adaptativas (código decide, IA só quando precisa)

| Situação medida | Ação | Quem decide |
|---|---|---|
| a fonte não mudou (`sha256` igual) em k ocorrências | espaçar: próximo intervalo × 2 até `intervalo_max` | código |
| mudou ou há pendência nova | voltar ao `intervalo_min` | código |
| dado mais velho que `frescor_max` para o relatório | pesquisar de novo antes de relatar | código |
| falha do app com `failure_kind` de tela/app ≥ 2 vezes e outro app permitido tem a capacidade | trocar de app | código propõe; o plano executa |
| capacidade que a persona não tem ou papel diferente (checagem, redação) | pedir colaboração (§9) | código, dentro dos limites |
| conta travada, desafio, 2FA | pausar e avisar; nunca contornar | código |
| objetivo ambíguo (qual perfil é o do político?) | pergunta ao dono (`needs_input` → sucessora) | IA pergunta, pessoa responde |
| escolher entre caminhos equivalentes, ler o conteúdo | plano da ocorrência | IA, no orçamento |

É o conselho de "a solução mais simples" (Anthropic) e "uma função quando uma função resolve" (Microsoft): a agenda,
o espaçamento e os limites são código; a IA entra no plano e na leitura de cada ocorrência, como já entra hoje.

## 9. Colaboração entre personas (26.5)

- **Papéis por capacidade:** `pesquisador` (só leitura), `checador` (leitura e comparação de fontes), `redator`
  (rascunho, sem efeito), `porta-voz` (a única que age para fora). A seleção reusa a escolha semântica do ADR-050
  (quem combina com o pedido) e o `resolver_alvos` de `modules/execution/application/alvos.py` (onde); o papel limita as capacidades no plano da ocorrência.
- **Estrutura:** um pedido pai com sub-pedidos (`pai_id`) e dependências (`pedido_dependencias(de, para, tipo)`:
  `precisa_de_resultado`, `depois_de`). A ocorrência do filho fica `devida` só com a dependência comprovada — o mesmo
  critério de `_dependencias_comprovadas` para etapas.
- **Consolidação:** o pai lê as observações e a memória dos filhos (não o texto livre das execuções) e monta o
  relatório; conflito entre filhos (duas fontes dizem coisas diferentes) vai ao relatório como conflito, não é
  resolvido por voto.
- **Prevenção de ciclo e de explosão** (AutoGen, Agent Framework, OpenAI `max_turns`):
  - o grafo de dependências é conferido na criação (aresta que fecha ciclo é recusada);
  - profundidade máxima 2 (pai → filhos), no máximo 5 filhos;
  - quem delega não recebe delegação da própria linhagem (a linhagem é gravada);
  - orçamento do filho sai do saldo do pai, nunca soma;
  - condição de término do conjunto: o pai encerra os filhos quando encerra.
- **Para fora:**
  - uma conta por alvo (ADR-055) vale **dentro do pedido inteiro**, e mais forte que a política: só o porta-voz toca um
    alvo, e nem curtida de outra persona do mesmo pedido no mesmo alvo (a curtida, fora de `UMA_CONTA_POR_ALVO`, fica
    proibida aqui por regra do pedido);
  - efeito sempre `approval_required` quando o alvo é pessoa real sem conversa prévia;
  - **proibido simular apoio de pessoas independentes:** duas personas do mesmo pedido nunca reagem ao mesmo conteúdo,
    nunca se citam como terceiros, nunca aparecem como vozes distintas na mesma conversa. É o comportamento
    inautêntico coordenado da política da Meta e a campanha coordenada que o ADR-050 já recusa.

## 10. Recursos e custo (26.6)

| Recurso | Regra proposta | Existe hoje |
|---|---|---|
| Orçamento do pedido | `orcamento_total_usd` e `orcamento_ocorrencia_usd`; o custo da ocorrência é somado de `ai_calls` da execução no fechamento e gravado em `pedido_ocorrencias.custo_usd` (sobrevive à purga de 14 dias) | `ai_max_usd_per_run`, `ai_max_usd_per_day`, `ai_calls` |
| Antes de despachar | estimativa = mediana das últimas 5 ocorrências (ou teto por ocorrência na primeira); saldo restante menor que a estimativa → `pulada: orçamento` e pausa com aviso | — |
| Saldo das contas de IA (ADR-051) | conta em `block_below` → ocorrência `adiada: saldo`, dentro da janela; fora dela, `perdida`. Não é falha e não conta para `pausa_por_falha` | `AIError(kind="balance")` |
| Piso de frequência | `observar` ≥ 15 min; efeito externo ≥ 1 h e dentro dos limites do perfil; cada pedido declara o intervalo, a interface mostra o custo por mês estimado | — |
| Concorrência | ocorrências são execuções: `max_active_devices`, teto por servidor, vagas de IA (`ai_slots`) | existe |
| Prioridade | `runs.prioridade`: comando interativo antes de ocorrência de fundo; `dispatchable_objectives` passa a ordenar por prioridade e depois por `created_at` | ordena só por `created_at` |
| Aparelho e worker | a ocorrência passa pelas portas do `_tick` (worker em manutenção, aparelho remoto); espera conta no `prazo_inicio_s` | existe |
| Rede (Fase 25) | política de rede exigida sem tráfego verificado → espera (`rede_gate`); passou do prazo → `perdida: rede` | em curso |
| Hibernação | o rodízio (`auto_start_devices`) continua dono; opcional: pré-aquecer o aparelho `pre_aquecer_s` antes da próxima ocorrência conhecida | rodízio existe |
| Reaproveitamento | observação da mesma `fonte` com idade < `frescor_max` (de qualquer pedido do dono, só leitura) é reusada em vez de nova execução; o relatório diz "reaproveitado de ocorrência X" | — |

## 11. Experiência (26.7)

- **Criação a partir do Comando:** o seletor "Quando" (agora, em, repetir, quando acontecer, acompanhar) e os campos
  do pedido; o assistente do ADR-047 refina objetivo e critérios; a prévia obrigatória mostra alvos (ADR-044), as
  próximas 5 datas no fuso escolhido, a autonomia com o que exige aprovação e o custo estimado por mês. Só a
  confirmação cria o pedido.
- **Tela Pedidos** (nova, `frontend/src/features/pedidos/`): lista com estado, tipo, persona(s), próxima execução
  (hora local e fuso), última ocorrência (resultado e link), gasto × orçamento, alertas.
- **Detalhe do pedido:** resumo (objetivo, critérios, autonomia, limites, versão); linha do tempo de ocorrências com o
  motivo de cada `pulada`/`perdida` e o link para a execução (a `RunView` de sempre); próximas ocorrências; memória
  (progresso e pendências); relatórios; observações com fonte e captura; custo por ocorrência.
- **Ações:** editar (nova versão, com prévia do que muda nas próximas), pausar, retomar (daqui ou recuperar), executar
  agora, backfill (só `observar`), cancelar (confirma e diz se há execução em curso), responder pendência.
- **Aviso ao dono:** caixa de avisos no painel (evento `pedido.*` persistido + contador na barra), para: pausa
  automática, aprovação pendente, `incerta`, orçamento a 80%, relatório pronto.
- **Aviso fora do painel (28.11): o canal decidido pelo dono (02/10) é o Telegram**, por um bot do @BotFather, só saída
  (sem webhook). É o ESPELHO da caixa de Pendências (ADR-062), não um conceito novo: a mensagem leva o tipo do evento e o
  link `#/pendencias` (base `avisos.url_painel`; sem base, só o texto), nunca persona, conta, conteúdo nem terceiro. A
  fonte são os eventos que já viram pendência (`approval.pending`, `run.updated` em `needs_input`, `session.needs_person`)
  mais o `pedido.aviso` do 28.9, que sai para TODOS os tipos (o informativo, que não vai à caixa, segue sem link). Fila
  durável `avisos_entregas` (migração 068) com chave única por fato; só o líder da trava `avisos` envia; envio
  interrompido por queda fica `incerto` e não é reenviado. Segredos `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` no `.env`.
  Procedimento em [`operacao.md` §15](../operacao.md).
- API proposta (adendo novo do contrato): `GET/POST /api/pedidos`, `GET/PATCH /api/pedidos/{id}`,
  `POST /api/pedidos/{id}/pausar|retomar|cancelar|executar|backfill`, `GET /api/pedidos/{id}/ocorrencias`,
  `/relatorios`, `/observacoes`, `GET /api/pedidos/previa` (próximas datas e custo, sem efeito).

## 12. Os três casos (26.8)

### 12.1 Acompanhamento de políticos

| Campo | Desenho |
|---|---|
| Objetivo | registrar publicações e interações públicas dos perfis escolhidos, no período, e relatar |
| Esclarecimento | o dono confirma cada perfil (nome → conta verificada?); ambiguidade vira pergunta, nunca chute |
| Gatilho | recorrência diária (ex.: 08:00 `America/Sao_Paulo`) de observação + relatório semanal |
| Autonomia | `observar` por construção: o ADR-050 recusa elogio ou ataque a candidato, pedido de voto e campanha coordenada; nenhuma curtida, comentário ou seguir. Perto do pleito, o TSE (Res. 23.755/2026) endurece o uso de conteúdo sintético — o pedido não produz nenhum |
| Fontes | publicações e comentários públicos no app; cada observação com perfil, instante, `sha256` da captura e trecho curto; o relatório cita a fonte (IFCN: reproduzível) |
| Amostra × população | a amostra é "o que esses perfis publicaram e quem comentou neles"; o relatório nunca generaliza para eleitores ou público (Pew: usuários de rede diferem do público e poucos produzem a maior parte; AAPOR: amostra não probabilística exige premissas explícitas) |
| Observação × conclusão | observado: contagens, temas declarados, horários; conclusão: só com o alcance ("na amostra coletada, …") e o não coberto (stories, perfis privados, remoções) |
| Dados de terceiros | minimização (LGPD art. 6º; AoIR): comentaristas entram agregados, sem nome no relatório, salvo pessoa pública |
| Autorizações | nenhuma de efeito; chamada de IA paga no relatório opcional |

### 12.2 Preço de produto

| Campo | Desenho |
|---|---|
| Objetivo | consulta pontual (`agora`) ou acompanhamento (recorrência + condição "preço total < X") |
| Produto exato | identificador (EAN, modelo, cor, capacidade) confirmado pelo dono na criação; oferta que não casa vai a "não comparável" |
| Custo total | preço + frete para o CEP declarado + seguro/taxas, parcelas e à vista separados (Decreto 7.962/2013 obriga a loja a discriminar despesas adicionais) |
| Condições | disponibilidade, prazo de entrega, vendedor (loja × marketplace), direito de arrependimento (CDC art. 49) |
| Confiabilidade da loja | CNPJ e endereço exibidos (Decreto 7.962); consulta à lista do Procon-SP; índices do consumidor.gov.br quando a loja aderiu (ausência não é sinal) |
| Momento | cada preço com instante e fuso; comparação só entre observações da mesma janela |
| Autonomia | `observar`. **Comprar é proibido** em qualquer grau; o aviso "caiu abaixo de X" vai à caixa do dono com as observações |
| Reaproveitamento | a mesma loja/produto observada por outro pedido há menos de `frescor_max` é reusada |

### 12.3 Monitoramento de reputação

| Campo | Desenho |
|---|---|
| Objetivo | detectar menções a uma pessoa ou marca do dono, classificar, avisar e, se autorizado, preparar resposta |
| Papéis | pesquisador (encontra e captura), checador (classifica e verifica), redator (prepara resposta), porta-voz (a única conta que responde, com aprovação) — §9 |
| Classificação | **opinião/crítica** (não checável — a Lupa não checa opinião) × **alegação factual** (dado, fato datado, legalidade: checável) × **ofensa/ameaça** (vai ao dono, sem resposta automática) |
| Verificação | só da alegação factual, com fontes primárias e rótulo no vocabulário das agências (verdadeiro, falso, falta contexto, exagerado, insustentável…); sem fonte primária, "não verificado" |
| Evidência | captura, `sha256`, instante e autor da menção guardados em `pedido_observacoes` (a evidência crua é purgada em 14 dias) |
| Resposta | `preparar` por padrão: rascunho com aprovação; `agir` só com aprovação por resposta; uma conta por alvo; transparente (a conta fala em nome de quem é) |
| Proibido | responder com várias personas, simular apoio de "clientes" ou "eleitores" independentes, denunciar em massa, atribuir fala a terceiros (ADR-055, Meta, ADR-050) |
| Autorizações | cada resposta publicada é efeito fora da máquina numa conta real: autorização do dono (CLAUDE.md) |

## 13. Plano incremental: Fase 28 proposta (26.8)

Proposta para o plano-100 (quem registra é o mecanismo, não este documento). Números: **migrações a partir da 059** (efetivos em 02/10: `066_travas` no 28.1 e `067_pedidos` no 28.2, porque a main já tinha a 063 e a 064; a renovação da trava ficou numa tarefa própria do `AppState`, a cada 20 s, e não no `_manter_posse`)
(056–058 estão reservadas às Fases 23–27; 056 e 057 já estão em curso), ADR a partir do 060 — **conferir em todos os
branches antes de criar** (regra de sessões paralelas). `[A]` = toca aparelho ou conta real.

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 28.1 | **Trava de líder**: tabela `travas` (migração 059), tomada e renovação por CAS no relógio do banco, token; aplicada a saldos, curadoria e retenção (`state.py`) | laços sem trava (§1); Kleppmann, Kubernetes | M |
| 28.2 | **Modelo do pedido**: `pedidos`, `pedido_gatilhos`, `pedido_ocorrencias`, `runs.pedido_id`, `ocorrencia_id`, `prioridade` (migração 060); domínio puro de estados e transições (`modules/pedidos/domain/`), chave da ocorrência | §6 | G |
| 28.3 | **Recorrência e fuso**: subconjunto da RRULE, `zoneinfo` + `tzdata` declarado em `requirements.in` (hoje indireto), desvio documentado para hora inexistente, `fold=0` na repetida; prévia das próximas datas | RFC 5545, PEP 495/615 | M |
| 28.4 | **Laço de pedidos**: materializar, janela, coalescer, sobreposição, despacho idempotente por `RunService.create`, fechamento pelo gancho e pela varredura, retomada depois de reinício | §7.2–7.5, 7.9 | G |
| 28.5 | **Tentativas e efeito**: nova tentativa só sem efeito possível; `incerta` para em `aguardando_pessoa`; pausa por falhas seguidas | §7.6 | M |
| 28.6 | **Orçamento e prioridade**: orçamento por pedido e ocorrência, custo gravado antes da purga, saldo (ADR-051) adia, `runs.prioridade` no `dispatchable_objectives` | §10 | M |
| 28.7 | **Memória, observações e relatório**: `pedido_memoria`, `pedido_observacoes`, `pedido_relatorios` (migração 061); relatório determinístico com observado/conclusão/não coberto; resumo por IA opcional | §6.6, §8 | G |
| 28.8 | **Gatilhos de evento, condição e persona**: cursor com detecção de buraco da retenção; condição determinística; `proxima_visita` presa aos limites | §7.8 | M |
| 28.9 | **API e tela Pedidos**: adendo do contrato, criação pelo Comando com prévia, lista, detalhe, ações, caixa de avisos no painel | §11 | G |
| 28.10 | **Colaboração**: sub-pedidos, dependências (migração 062), papéis, limites de profundidade e linhagem, porta-voz único, proibição de apoio simulado | §9 | G |
| 28.11 | **Aviso fora do painel** (decisão do dono: canal e conta) | §11 | P |
| 28.12 | **Prova real** [A]: no central, um pedido de preço (`observar`, navegador, sem compra) com 3 ocorrências recorrentes, um reinício do backend no meio e uma ocorrência perdida de propósito; um pedido de políticos só de leitura com relatório determinístico. Chamada paga pontual autorizada | §12 | M |
| 28.13 | **Fechamento**: seção no `relatorio-validacao.md`, ADR da implementação, CHANGELOG, handoff, estado pelo mecanismo | — | P |

Dependências: 28.1 → 28.4; 28.2 → 28.3, 28.4, 28.7, 28.9; 28.4 → 28.5, 28.6, 28.8; 28.7 → 28.10; 28.9 depende de
28.4 e 28.7; 28.12 depende de 28.4–28.9; 28.10 e 28.11 podem ficar para depois da prova sem bloquear.

O desenho de implementação do 28.4 (onde o laço vive, regras exatas de janela e coalescência, despacho que procura a
execução pela chave antes de criar, fechamento pela varredura quando o gancho não dispara, e as decisões pendentes)
está em [pedidos-laco.md](pedidos-laco.md). Ele registra dois desvios deste texto: o `on_run_settled` só acorda o
laço, e editar a recorrência cria gatilho novo em vez de refazer as ocorrências abertas (a chave não tem versão).

| Item | Aceite | Prova possível |
|---|---|---|
| 28.1 | dois `AppState` no mesmo banco (SQLite e PostgreSQL) com relógio injetado: um só líder; o outro assume depois de 120 s; saldo não concilia duas vezes | `simulated`; `real` só com segundo backend no central (not_run até lá) |
| 28.2 | tabela de transições testada; toda transição fora dela recusada; chave determinística estável | `simulated` |
| 28.3 | testes de fronteira com zonas que têm horário de verão (ex.: `America/New_York`, e `America/Sao_Paulo` antes de 2019): hora inexistente executa depois do salto, repetida uma vez | `simulated` |
| 28.4 | reinício no meio não duplica nem perde; duas instâncias do laço geram uma ocorrência; perdida fora da janela registrada | `simulated`; `real` em 28.12 |
| 28.5 | execução com `effect_possible` nunca repete; `incerta` para o pedido | `simulated` |
| 28.6 | orçamento esgotado pausa sem chamar IA; saldo bloqueado adia; interativo passa na frente | `simulated`; saldo `real` sem gasto (o bloqueio do ADR-051 já foi provado assim) |
| 28.7 | relatório sobrevive à purga de 14 dias (teste com retenção curta); blocos observado/conclusão separados | `simulated` |
| 28.8 | cursor abaixo do menor evento gera aviso de buraco, não disparo | `simulated` |
| 28.9 | typecheck, testes e navegador contra backend simulado a 1366 e 375 px | `simulated` |
| 28.10 | ciclo recusado; profundidade e linhagem respeitadas; segunda persona no mesmo alvo recusada | `simulated` |
| 28.12 | ocorrências ligadas a `runs` reais, com commit, máquina e ids | `real` (políticos só leitura e preço sem compra dispensam autorização de efeito; a chamada paga pede autorização pontual); reputação com resposta publicada: `not_run` sem autorização do dono |

**Fecha quando:** um pedido recorrente e um condicional rodam no central por pelo menos três ocorrências cada,
atravessando um reinício do backend sem ocorrência duplicada nem perdida em silêncio, com custo por ocorrência gravado,
relatório com observado × conclusão e a tela Pedidos mostrando histórico e próximas execuções — prova `real` para
leitura, `simulated` para colaboração e efeito, e `not_run` com a dependência exata para o que exige autorização.

# Documentação — índice e fontes principais

Cada assunto tem **uma fonte principal**. Os outros documentos apontam para ela em vez de repetir o que ela diz.

As camadas guardam coisas diferentes:

- o **requisito** diz o que se quer;
- o **código e a configuração** dizem o que existe;
- os **testes e as evidências** dizem o que foi provado.

Quando duas camadas discordam, a divergência fica registrada, e nenhuma das duas é apagada.

Para retomar o trabalho, siga a ordem de leitura de [`../CLAUDE.md`](../CLAUDE.md).

## Fonte principal por assunto

| Assunto | Fonte principal | Quando consultar |
|---|---|---|
| Handoff: onde paramos, bloqueios, próxima ação | [`estado-atual.md`](estado-atual.md) | No início de toda sessão |
| O que falta, por tipo de pendência | [`roadmap.md`](roadmap.md) | Ao escolher a próxima tarefa |
| Requisito de cada item (IDs 0.1…13.3, T.x) | [`plano-100.md`](plano-100.md) | Só a linha do item (`grep -n "^| <id> |" docs/plano-100.md`) ou o pacote `.claude/plano-100/pacotes/<id>.md` |
| Estado registrado de cada item | [`execucao-plano-100-runner.md`](execucao-plano-100-runner.md), **gerado** a partir de `.claude/plano-100/estado.json` | Para saber se um item está implementado e com que prova |
| Provas medidas e os nove aceites | [`relatorio-validacao.md`](relatorio-validacao.md) (§13) | Antes de afirmar que algo funciona |
| Produto: objetivo, conceitos, fluxos, limites, matriz de funcionalidades | [`produto.md`](produto.md) | Para entender o que o sistema faz e o que foi provado |
| Arquitetura: componentes, contratos, estados, recuperação | [`arquitetura.md`](arquitetura.md) | Antes de mexer em comando, fila, worker ou eventos |
| Evolução arquitetural: monólito modular e plataforma de skills (design, fases A–K) | [`design/evolucao-arquitetural.md`](design/evolucao-arquitetural.md) | Antes de criar código em `app/modules`/`app/contracts`, mexer em skills, DSL, compilador, ensino ou nos god modules |
| Persona, conta, aparelho e parque: persona como raiz, conta única com credencial, imagens, N:N com aparelho, roteamento por persona, provisionamento, painel (design, ondas W0–WE, migrações 047–051) | [`design/persona-e-parque.md`](design/persona-e-parque.md) | Antes de mexer em `social/`, `modules/identity/`, `device_profile_bindings`, `RunCreate`/roteamento, `POST /api/instances` ou nas telas Configuração, Foco, Persona e Comando |
| Terceira evolução: Outlook e contas por app, comando entre aplicativos, rede por aparelho (VPN e proxy), aceite integrado (diagnóstico, decisões técnicas, Fases 23–27, ADR-056 a 059); coordenação em [`handoffs/terceira-evolucao.md`](handoffs/terceira-evolucao.md) | [`design/terceira-evolucao.md`](design/terceira-evolucao.md) | Antes de mexer em sessão por conta, `conhecimento/apps/com.microsoft.office.outlook/`, catálogo de vários apps, saídas de etapa, `devices/proxy.py` ou rede do aparelho |
| GitHub: acessos, Actions, runners, cota e custo (bloqueio de gasto, proposta de teto, apps instalados); coordenação em [`handoffs/github-e-ci.md`](handoffs/github-e-ci.md) | [`operacao.md`](operacao.md) (§ Runner próprio) | Antes de mexer em orçamento, runner, `CI_RUNS_ON` ou workflows por causa de custo |
| Pedidos persistentes: agendados, recorrentes, por evento e acompanhados pela persona (pesquisa e desenho, Fase 26, ADR-059) | [`design/pedidos-persistentes.md`](design/pedidos-persistentes.md) | Antes de propor agendamento, recorrência, gatilho ou colaboração entre personas |
| Integração com o Trello do dono: acesso por chave e token, espelho das pendências, pedidos, custos e marcos, comandos de volta por cartão e comentário, vínculos ao conhecimento (estudo 32.1; código no 32.2, ADR-072) | [`design/trello-integracao.md`](design/trello-integracao.md) | Antes de implementar o 32.2, de dar um segundo canal ao `modules/avisos/` ou de desenhar a gramática de comandos do 28.15 |
| Regras do dono para os canais (Trello e Telegram) e a ANA, a IA Gerente de Operações: fronteira, identidade, convidados, pergunta do nome, autorizações, cadência, arquivos locais e como uma sessão nova assume; cada regra com origem, onde vale hoje e onde vale no produto | [`dominios/canais.md`](dominios/canais.md) | Antes de operar o Trello ou o Telegram, de mudar uma regra deles ou de levar uma regra para `modules/avisos/` (28.15, 32.2, 28.17) |
| Canais externos: o contrato comum do Telegram (28.15) e do Trello (32.2), que são a entrada comum, a gramática fechada, o fato pelo reply ou pelo cartão, a identidade conferida na entrada, as políticas do painel, o dedupe pela 085 genérica, a credencial e os testes de contrato. Citado pelos ADR-071 e ADR-072 | [`design/canais-externos.md`](design/canais-externos.md) | Antes de mexer em `modules/avisos/` (entrada ou saída) ou de acrescentar um canal de conversa |
| Aprendizado vivo: visão por app (declarado × aprendido × absorvido), receita legível, saúde derivada, versão do app, lineage, curador por IA auditável com política de risco e orçamento proporcional, Fase 30 e ADR-067 proposto; diagnóstico da Fase A em [`design/aprendizado-vivo-diagnostico.md`](design/aprendizado-vivo-diagnostico.md) | [`design/aprendizado-vivo.md`](design/aprendizado-vivo.md) | Antes de mexer em `modules/learning/`, na tela de Aprendizado, em saúde, versão ou lineage do conhecimento, ou de propor IA no ciclo de aprendizado |
| Capabilities: operação semântica do app, estratégias, portas de execução, provas locais | [`dominios/capabilities.md`](dominios/capabilities.md) | Mudanças em `modules/capabilities/`, `modules/execution/application/ports.py`, `taskqueue/proofs.py` ou no `local_proof` do catálogo |
| Skills versionadas: estados, congelamento, validação (P4), registro com dois backends, adoção de fluxo | [`dominios/skills.md`](dominios/skills.md) | Mudanças em `modules/skills/` (domínio, repositório, registro, adaptador legado) ou nas migrações 042–046 |
| Retrieval de contexto de código: léxico + BM25 locais, semântico plugável, política de envio, modos, fail-open, cache e métricas (ADR-063) | [`dominios/context-retrieval.md`](dominios/context-retrieval.md) | Mudanças em `modules/context_retrieval/`, antes de ligar `context_retrieval.enabled` ou de enviar contexto a um provedor |
| Ensino v2: sessão, candidata `{document, annotations}`, perguntas e respostas, credencial, candidata → rascunho, generalizador e custo | [`teaching.md`](teaching.md) | Mudanças em `modules/skills/domain/{teaching,generalization}.py`, `application/teaching.py`, `infrastructure/{sql_teaching_repository,secret_screen}.py`, `presentation/router.py`, `training/generalizer.py` ou no `TeachingPanel` |
| DSL de skill `automation/v1alpha1`: campos, expressões, códigos `E_*` | [`skill-dsl.md`](skill-dsl.md) | Ao mexer em `contracts/skills/`, em `tests/fixtures/dsl/`, no snapshot `tests/contratos/skill-dsl.v1alpha1.json` ou num código de erro |
| Runtime de skills: compilador, IR, baixa para `Plan`, invariante D15, fiação em `_plan` | [`skill-runtime.md`](skill-runtime.md) | Ao mexer em `modules/skills/domain/{compiler,ir}.py`, `infrastructure/{lowering,run_planning}.py` ou `PlanStep.origin` |
| Execução: o ciclo RESOLVE→…→COMPLETE, estratégias, trilha da 045, proteções herdadas, recursos declarativos e `PlanReport` | [`dominios/execution.md`](dominios/execution.md) | Mudanças em `RunService._plan`, `Repository._insert_steps`, `StepExecutor._verify`/`_registrar_estrategia`, `app/shared/resources.py`, `modules/*/infrastructure/` de recurso ou `modules/execution/domain/plan_report.py` |
| Parque, virtualização, workers, escalonamento, limites, controle manual | [`dominios/parque.md`](dominios/parque.md) | Mudanças em `devices/`, `workers/`, `worker/`, `scheduler` |
| Catálogo de apps, releases, loja Play Store, distribuição | [`dominios/apps-e-loja.md`](dominios/apps-e-loja.md) | Mudanças em `releases/`, `planning/catalog/` (shim) e no manifesto de app (`modules/applications`) |
| Persona = a pessoa: modelo (voz, biografia, visual, proveniência), o que vai ao prompt, PATCH por seção, geração por IA, imagens e avatar, **contas e acesso** (conta única, credencial com consentimento, sessão por conta e aparelho, dados disponíveis ao plano), migrações 047–049 | [`dominios/persona.md`](dominios/persona.md) | Mudanças em `instagram_profiles`/`persona_images`, `PersonaDTO`, `social/{repository,service,context}.py`, `modules/identity/{domain,application,adapters}/persona*`, `ai.image` |
| Perfis e contas, memória, políticas, aprovações, Instagram, sessão, treinamento, multi-app | [`dominios/perfis-e-instagram.md`](dominios/perfis-e-instagram.md) | Mudanças em `social/`, `integrations/`, `training/`, contas e sessão |
| Aprendizado contínuo: livro com ciclo de vida e D1, sinais e voto D2, lições medidas, telas aprendidas, voz e preferências, "o que mais falha" e backlog (ADR-054, migração 055) | [`dominios/aprendizado.md`](dominios/aprendizado.md) | Mudanças em `modules/learning/`, `taskqueue/costuras.py`, `frontend/src/features/aprendizado/` ou nos modos do bloco `aprendizado` |
| IA: funções, modelos, fallback, receitas, custo, avaliação | [`ia.md`](ia.md) | Mudanças em `planning/` e `taskqueue/executor.py`, e antes de gastar API |
| Contrato HTTP/WebSocket | [`api-contract.md`](api-contract.md): base mais adendos cumulativos, o mais recente vence | Ao criar ou alterar rota, evento ou mensagem |
| Banco, migrações, SQLite × PostgreSQL, vários backends | [`banco.md`](banco.md) | Ao criar migração ou mexer em `db.py` |
| Worker remoto: instalação, canal, túnel, segurança | [`worker.md`](worker.md) | Ao operar ou alterar o agente |
| Operação: instalação, testes, CI, deploy, backup, incidentes, scripts por risco | [`operacao.md`](operacao.md) | Antes de rodar qualquer script ou implantar |
| Pendências da terceira evolução (Fase 29): P16, CI, renderizador do Outlook, saída esperada, UDP, firewall do worker; tarefa, aceite, reservas de aparelho e pedidos ao dono | [`handoffs/pendencias-evolucao3.md`](handoffs/pendencias-evolucao3.md) | Antes de mexer na prova de vazamento, na sonda de saída, no renderizador do emulador ou de implantar com rede aplicada em aparelhos |
| Desempenho e capacidade: linha de base, benchmark, métricas agregadas, decisões sobre runtimes e orquestração | [`relatorio-desempenho.md`](relatorio-desempenho.md); coordenação em [`handoffs/evolucao-desempenho.md`](handoffs/evolucao-desempenho.md) | Antes de afirmar ganho de desempenho ou de mexer em captura, observação, reserva de capacidade |
| Onde ficam os arquivos de evidência (storage) | [`evidencias.md`](evidencias.md) | Ao mexer em `storage.py` ou nas evidências |
| Decisões de arquitetura e do dono (ADR) | [`decisoes.md`](decisoes.md) | Antes de mudar algo que uma decisão fixou |
| Aprendizados, armadilhas, tentativas que falharam | [`conhecimento/aprendizados.md`](conhecimento/aprendizados.md) | Ao encontrar um erro: `grep -i` pela mensagem |
| Knowledge lake: níveis, índice, promoção, arquivamento | [`conhecimento/README.md`](conhecimento/README.md) | Para registrar ou achar conhecimento |
| Fontes consultadas e lacunas de acesso | [`conhecimento/fontes.md`](conhecimento/fontes.md) | Ao reconstruir o histórico |
| Execução do plano-100 pela IDE (workflow, pacotes, custo) | [`claude-plano-100.md`](claude-plano-100.md) | Ao rodar a esteira (skill `plano-100`) |
| Mudanças por data | [`../CHANGELOG.md`](../CHANGELOG.md) | Para ver o que mudou e o que está implantado |

## Onde alterar

| Área | Código | Doc principal |
|---|---|---|
| API, eventos, rotas | `backend/app/api.py`, `models.py`, `events.py` | [`api-contract.md`](api-contract.md) |
| Comandos e worker | `backend/app/commands/`, `workers/`, `worker/` | [`arquitetura.md`](arquitetura.md), [`worker.md`](worker.md) |
| Aparelhos e parque | `backend/app/devices/`, `taskqueue/scheduler.py` | [`dominios/parque.md`](dominios/parque.md) |
| Fila e execução | `backend/app/taskqueue/`, `modules/execution/` | [`dominios/execution.md`](dominios/execution.md) |
| IA | `backend/app/planning/`, `taskqueue/executor.py` | [`ia.md`](ia.md) |
| Apps, releases, loja, manifesto de app | `backend/app/releases/`, `modules/applications/` (`planning/catalog/` é shim) | [`dominios/apps-e-loja.md`](dominios/apps-e-loja.md) |
| Skills, DSL, compilador, ensino | `backend/app/modules/skills/`, `modules/capabilities/`, `contracts/skills/` | [`dominios/skills.md`](dominios/skills.md), [`design/evolucao-arquitetural.md`](design/evolucao-arquitetural.md) |
| Perfis, Instagram, treinamento | `backend/app/social/`, `app/conhecimento/apps/`, `integrations/app_declarado/`, `training/` | [`dominios/perfis-e-instagram.md`](dominios/perfis-e-instagram.md) |
| Retrieval de contexto de código (desligado por padrão) | `backend/app/modules/context_retrieval/`, `scripts/plano-100-pacotes.py --contexto` | [`dominios/context-retrieval.md`](dominios/context-retrieval.md) |
| Banco e migrações | `backend/app/db.py`, `backend/migrations/` | [`banco.md`](banco.md) |
| Segurança | `backend/app/security/` | [`operacao.md`](operacao.md) |
| Painel | `frontend/src/features/*` | [`produto.md`](produto.md) |
| Operação e scripts | `scripts/*.ps1` | [`operacao.md`](operacao.md) |

## Registros datados (histórico: evidência, não instrução)

| Documento | O que é | Situação |
|---|---|---|
| [`auditoria-ux-2026-09-27/`](auditoria-ux-2026-09-27/README.md) | Usabilidade do painel sobre `9276d63`: 5 achados que bloqueiam o uso, 11 de atrito, 6 de desempenho, e o plano da fase L | Espera execução pela sessão da evolução arquitetural; depois vira histórico |
| [`auditoria-2026-09-21/`](auditoria-2026-09-21/README.md) | 181 achados sobre o commit `f1e61b3` | Histórico. O plano-100 consolidou os achados; o estado vigente está no livro-razão. Confira no código antes de repetir um achado |
| [`parque-distribuido.md`](parque-distribuido.md) | Projeto do parque distribuído (19/09, revisto em 21 e 23/09) | Arquitetura e recuperação ainda valem como referência; o estado vigente está em `dominios/parque.md` |
| [`prompt-executar-plano-100-claude.md`](prompt-executar-plano-100-claude.md) | Pedido e limites da execução do plano-100 (22/09) | Referenciado por `.claude/plano-100.json` (`prompt`); as regras em vigor estão em `claude-plano-100.md` e no workflow |
| Seções 1–12 de [`relatorio-validacao.md`](relatorio-validacao.md) | Provas de 17 a 23/09 | Históricas e datadas; a §13 é a tabela vigente dos aceites |

## Instruções para o Claude (carregadas automaticamente)

- [`../CLAUDE.md`](../CLAUDE.md): invariantes, comandos essenciais e protocolo; o mapa "onde alterar" está na seção [Onde alterar](#onde-alterar) acima.
- `.claude/rules/*.md`: regras por caminho, carregadas quando você abre arquivos da área (migrações, workflow,
  segredos e mundo real, testes, plano-100).
- `.claude/skills/`:
  - `retomar`, `preparar-tarefa` e `fechar-tarefa`: continuidade;
  - `plano-100`: a esteira;
  - `plano-100-{high,max,medium,ultracode,xhigh}`: aposentadas desde 22/09, porque o esforço agora vem por item.
- `.claude/workflows/plano-100.js`: orquestração da esteira.

## Manutenção

`python scripts/docs-check.py` confere:

- links relativos e caminhos;
- o tamanho do `CLAUDE.md`;
- se o mapa de blocos do plano-100 está de acordo com o plano;
- os IDs do roadmap;
- o vocabulário do `estado.json`;
- se o relatório gerado está atualizado;
- se todas as migrações aparecem em `banco.md`;
- IDs de ADR e de aprendizado sem duplicata.

Rode antes de todo commit que mexa em documentação. As regras de promoção e de arquivamento de conhecimento estão
em [`conhecimento/README.md`](conhecimento/README.md).

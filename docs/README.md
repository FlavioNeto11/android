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
| Capabilities: operação semântica do app, estratégias, portas de execução, provas locais | [`dominios/capabilities.md`](dominios/capabilities.md) | Mudanças em `modules/capabilities/`, `modules/execution/application/ports.py`, `taskqueue/proofs.py` ou no `local_proof` do catálogo |
| Skills versionadas: estados, congelamento, validação (P4), registro com dois backends, adoção de fluxo | [`dominios/skills.md`](dominios/skills.md) | Mudanças em `modules/skills/` (domínio, repositório, registro, adaptador legado) ou nas migrações 042–046 |
| DSL de skill `automation/v1alpha1`: campos, expressões, códigos `E_*` | [`skill-dsl.md`](skill-dsl.md) | Ao mexer em `contracts/skills/`, em `tests/fixtures/dsl/`, no snapshot `tests/contratos/skill-dsl.v1alpha1.json` ou num código de erro |
| Runtime de skills: compilador, IR, baixa para `Plan`, invariante D15, fiação em `_plan` | [`skill-runtime.md`](skill-runtime.md) | Ao mexer em `modules/skills/domain/{compiler,ir}.py`, `infrastructure/{lowering,run_planning}.py` ou `PlanStep.origin` |
| Execução: o ciclo RESOLVE→…→COMPLETE, estratégias, trilha da 045, proteções herdadas, recursos declarativos e `PlanReport` | [`dominios/execution.md`](dominios/execution.md) | Mudanças em `RunService._plan`, `Repository._insert_steps`, `StepExecutor._verify`/`_registrar_estrategia`, `app/shared/resources.py`, `modules/*/infrastructure/` de recurso ou `modules/execution/domain/plan_report.py` |
| Parque, virtualização, workers, escalonamento, limites, controle manual | [`dominios/parque.md`](dominios/parque.md) | Mudanças em `devices/`, `workers/`, `worker/`, `scheduler` |
| Catálogo de apps, releases, loja Play Store, distribuição | [`dominios/apps-e-loja.md`](dominios/apps-e-loja.md) | Mudanças em `releases/` e `planning/catalog/` |
| Perfis, personas, memória, políticas, aprovações, Instagram, treinamento, multi-app | [`dominios/perfis-e-instagram.md`](dominios/perfis-e-instagram.md) | Mudanças em `social/`, `integrations/`, `training/` |
| IA: funções, modelos, fallback, receitas, custo, avaliação | [`ia.md`](ia.md) | Mudanças em `planning/` e `taskqueue/executor.py`, e antes de gastar API |
| Contrato HTTP/WebSocket | [`api-contract.md`](api-contract.md): base mais adendos cumulativos, o mais recente vence | Ao criar ou alterar rota, evento ou mensagem |
| Banco, migrações, SQLite × PostgreSQL, vários backends | [`banco.md`](banco.md) | Ao criar migração ou mexer em `db.py` |
| Worker remoto: instalação, canal, túnel, segurança | [`worker.md`](worker.md) | Ao operar ou alterar o agente |
| Operação: instalação, testes, CI, deploy, backup, incidentes, scripts por risco | [`operacao.md`](operacao.md) | Antes de rodar qualquer script ou implantar |
| Desempenho e capacidade: linha de base, benchmark, métricas agregadas, decisões sobre runtimes e orquestração | [`relatorio-desempenho.md`](relatorio-desempenho.md); coordenação em [`handoffs/evolucao-desempenho.md`](handoffs/evolucao-desempenho.md) | Antes de afirmar ganho de desempenho ou de mexer em captura, observação, reserva de capacidade |
| Onde ficam os arquivos de evidência (storage) | [`evidencias.md`](evidencias.md) | Ao mexer em `storage.py` ou nas evidências |
| Decisões de arquitetura e do dono (ADR) | [`decisoes.md`](decisoes.md) | Antes de mudar algo que uma decisão fixou |
| Aprendizados, armadilhas, tentativas que falharam | [`conhecimento/aprendizados.md`](conhecimento/aprendizados.md) | Ao encontrar um erro: `grep -i` pela mensagem |
| Knowledge lake: níveis, índice, promoção, arquivamento | [`conhecimento/README.md`](conhecimento/README.md) | Para registrar ou achar conhecimento |
| Fontes consultadas e lacunas de acesso | [`conhecimento/fontes.md`](conhecimento/fontes.md) | Ao reconstruir o histórico |
| Execução do plano-100 pela IDE (workflow, pacotes, custo) | [`claude-plano-100.md`](claude-plano-100.md) | Ao rodar a esteira (skill `plano-100`) |
| Mudanças por data | [`../CHANGELOG.md`](../CHANGELOG.md) | Para ver o que mudou e o que está implantado |

## Registros datados (histórico: evidência, não instrução)

| Documento | O que é | Situação |
|---|---|---|
| [`auditoria-2026-09-21/`](auditoria-2026-09-21/README.md) | 181 achados sobre o commit `f1e61b3` | Histórico. O plano-100 consolidou os achados; o estado vigente está no livro-razão. Confira no código antes de repetir um achado |
| [`parque-distribuido.md`](parque-distribuido.md) | Projeto do parque distribuído (19/09, revisto em 21 e 23/09) | Arquitetura e recuperação ainda valem como referência; o estado vigente está em `dominios/parque.md` |
| [`prompt-executar-plano-100-claude.md`](prompt-executar-plano-100-claude.md) | Pedido e limites da execução do plano-100 (22/09) | Referenciado por `.claude/plano-100.json` (`prompt`); as regras em vigor estão em `claude-plano-100.md` e no workflow |
| Seções 1–12 de [`relatorio-validacao.md`](relatorio-validacao.md) | Provas de 17 a 23/09 | Históricas e datadas; a §13 é a tabela vigente dos aceites |

## Instruções para o Claude (carregadas automaticamente)

- [`../CLAUDE.md`](../CLAUDE.md): invariantes, comandos, protocolo e mapa "onde alterar".
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

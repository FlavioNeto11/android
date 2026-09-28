# Changelog

Mudanças sustentadas pelo Git (`git log`) e, quando houver, pela evidência registrada. **Não há versões nem releases**:
o código declara `VERSION = "0.1.0"` (`backend/app/version.py`) desde o início, não há tags, e a implantação é
contínua a partir da `main` (commit direto, por escolha do dono — ver [ADR em decisoes.md](docs/decisoes.md)).
Por isso as entradas são por **data**, com o commit que as sustenta.

Três estados diferentes, que não se confundem:

- **integrado** — está na `main` (`origin/main`);
- **implantado** — é o commit que a produção responde em `GET /api/health` (`commit`, `migration`);
- **validado** — tem prova registrada em [`docs/relatorio-validacao.md`](docs/relatorio-validacao.md) ou no livro-razão
  do plano-100 ([`docs/execucao-plano-100-runner.md`](docs/execucao-plano-100-runner.md), coluna Prova).

Implantado em 25/09/2026 ~14:19 UTC (conferido no `/api/health` do central): `8169fd3`, migração `039_limites_por_servidor`,
`cryptography` 50.0.0 no venv; agente do worker `worker-lan-01` em `0.1.0+c0c982d` (o central o marca
`agent_outdated`, esperado `0.1.0+e6b00db`).

Ao fechar uma tarefa, acrescente a linha no dia dela (skill `fechar-tarefa`). Mudança só de documentação entra em
"Documentação e processo".

## 2026-09-28 — cabeçalho do painel em duas faixas (branch `claude/layout-cabecalho`)

- **Painel — cabeçalho.** A barra do topo passou a ter duas faixas de propósito: navegação e ferramentas (IA, conexão,
  operador) em cima, e uma régua com a saúde do ambiente e os indicadores embaixo. Antes, entre 900 e ~2200 px, os
  indicadores quebravam de linha sozinhos, alinhados à direita sob um vazio. Os indicadores ficaram numa linha só
  (ícone · valor · rótulo), "· vagas N" virou etiqueta própria, CPU e RAM ganharam medidor, e "Conectado" deixou de
  ser uma segunda pílula verde ao lado de "Ambiente OK". Prova: `simulated` — `frontend` typecheck + 612 testes
  (`src/features/topbar/TopBar.test.tsx`, `src/app.integration.test.tsx`) e capturas por CDP (Edge headless) contra
  backend simulado em 127.0.0.1:8765 a 1915/1366/1024/375 px, sem transbordo do documento. A produção não foi
  tocada (`not_run` até a implantação do frontend).

## 2026-09-28 — segunda evolução, ondas C e E: persona N:N aparelho, roteamento por persona e o painel novo — IMPLANTADO em 28/09 (`07fce91`)

Pedido do dono de 27/09 ([design](docs/design/persona-e-parque.md)); ADR-043, 044 e 046.

- **Onda C — persona N:N aparelho** (migração 051): vínculo por (persona, aparelho, app), aparelho principal, uma
  conta por app em cada aparelho (D2-a); vincular não toma o aparelho de ninguém; rotas
  `/api/personas/{id}/devices*` e `GET /api/instances/{id}/personas`; a porta de sessão recebe a persona do
  objetivo. Antes da 051, todos os chamadores do 1:1 passaram a listas (ordem segura).
- **Roteamento por persona:** `RunCreate.targets` e `device_policy`, `resolver_alvos` puro, `TargetExtractor`
  determinístico ("peça para o André…", "no aparelho Y e Z", "como @user"), prévia `POST /api/runs/targets/resolve`
  e eco obrigatório do destino tirado do texto; contradição vira pergunta.
- **Onda E — painel:** contrato de página com `@container page` (Configuração em largura total), Comando sem o
  campo de senha e com o modo "Por persona" com prévia, Foco em seções com ações em grupos e Zona de perigo,
  Personas com cadastro por prompt e as guias Visão geral / Persona / Contas e acesso / Imagens / Aparelhos,
  Aparelho → Personas no Foco e na Infraestrutura, Criar aparelho e Aposentar.
- **Provas:** `simulated` — testes da onda C (vínculos, rotas, roteamento, frases golden), vitest 612/612, aceite
  visual em 375/1024/1366/1920 com o Foco aberto e fechado contra backend simulado (148 capturas,
  [aceite](docs/auditoria-ux-2026-09-27/evo2-aceite.md)). `real` — ensaio 047–051 numa cópia do backup de produção
  (vínculos preservados, integridade ok, idempotente).
- **Implantação (28/09, autorizada pelo dono):** ensaio 047–051 na cópia do backup `20260928-084453`, deploy de
  `35b3e8f`, agente do notebook atualizado; a validação real achou a geração de persona recusada pelo provedor
  (esquema grande demais para a saída estruturada, K-042), corrigida em `be65bd4` e `07fce91`. Provas reais e o que
  ficou `not_run` em [`relatorio-validacao.md`](docs/relatorio-validacao.md) §15; plano-100 fase 16.
- **Incidente de ambiente** (28/09): um comando de agente com variável vazia apagou os arquivos soltos de
  `C:\Program Files\Git\` (`git-bash.exe`, desinstalador); `git` e `bash` seguem funcionando; reparo pelo
  instalador da mesma versão, decisão do dono (K-041).

## 2026-09-28 — segunda evolução, ondas A, B e D: a persona é a pessoa, conta única com credencial e consentimento, provisionamento local — IMPLANTADO em 28/09 com as ondas C e E

Pedido do dono de 27/09. Design em [`docs/design/persona-e-parque.md`](docs/design/persona-e-parque.md); decisões
ADR-040, 041, 042 e 045. **A produção segue em `524471d` e não pode dar `git pull` antes do ensaio.**

- **Onda A — a persona é a pessoa** (migração 047): a linha de `instagram_profiles` passa a ser a persona
  (identidade, biografia, voz, visual, geração); a tabela `personas` foi dobrada nela (3 vinculadas, 5 casadas por
  nome com os perfis bloqueados, 6 viram pessoas sem conta, `username = ''`); `/api/personas` é a rota canônica e
  `/api/instagram/profiles*` continua como apelido; geração de persona por IA (`POST /personas/generate`, rascunho
  validado) e enriquecimento (`POST /personas/{id}/enrich`); **imagens** (048): `persona_images`, receita
  determinística por seed com eixos de variação, gerador simulado (carimbado) e adaptador OpenAI atrás de chave,
  `on_create: true`, custo em `ai_calls.usd`; avatar serve a imagem principal.
- **Onda B — a conta é a entidade única** (049): `account_credentials` com estado e **consentimento por conta**,
  `profile_accounts.host` (conta de portal), `account_sessions` por (conta, aparelho) com vocabulário único; o
  Instagram é uma conta cujo app tem provedor de sessão. **As credenciais da execução vêm da conta da persona**
  (ADR-040, substitui em parte o ADR-025): `RunCreate.credentials` saiu (422); o planejador e o ator recebem só a
  lista de dados disponíveis (nomes, nunca valores); `{perfil_email}` e afins resolvidos por aparelho; `type_secret`
  resolve pela conta do perfil do objetivo, exige consentimento, só no app e no `host` da conta. O campo "Senha para a
  automação" do painel sai na onda E; até lá, digitar nele responde 422.
- **Onda D — provisionamento local** (050): `POST /api/instances` cria instância `dynamic` no servidor local, com
  `create` pelo despacho, teto `max_devices` e guarda de disco; `DELETE /api/instances/{id}` aposenta instância
  dinâmica; remoto fica para rodada própria.
- **Ensaio real (ADR-020)** das quatro migrações numa cópia do backup `data/backups/20260927-222357`, em 27/09 e
  28/09: filhas byte a byte iguais, `integrity_check` ok, FKs ok, 14 pessoas, 8 contas, 8 credenciais com o mesmo
  `secret_ref`, 3 sessões, idempotente. O ensaio achou e corrigiu um defeito da 049 (conta com handle vazio para
  persona sem conta) e o merge achou um bloco apagado por acidente (grupos de acesso), restaurado.
- **Provas:** `simulated` — suíte SQLite 2484/2485 no merge A+B+D (a falha é o flake da versão do agente, verde
  isolado); mypy estrito 110 arquivos; arquitetura verde. `not_run` — PostgreSQL (Actions bloqueado por cobrança,
  K-040), IA paga, OpenAI, produção.

## 2026-09-27 — fase L da auditoria de usabilidade integrada e IMPLANTADA (`524471d`, central e agente do worker)

- A pedido do dono, a branch `claude/android-multiagentes-session-cvv1rp` (outra sessão; 22 commits, só painel e
  docs) entrou na `main` e foi implantada: "aparelho" em vez de "instância", grades e tabelas em 390 px, foco em
  tela cheia abaixo de 720 px, contexto operacional com enums traduzidos, carga com erro e "Tentar de novo",
  confirmação para decidir comando incerto, contadores da barra do topo, ensino v2 e revisão do treino com carga
  segura. Detalhe em [`docs/auditoria-ux-2026-09-27/`](docs/auditoria-ux-2026-09-27/README.md).
- Painel: typecheck, vitest 533/533 e build verdes antes do deploy; backend sem mudança de código.
- Deploy: backup `data/backups/20260927-222357`; health `ok` em `524471d`; agente do notebook em `0.1.0+524471d`.
- Armadilha nova (K-039): o Appium do backend anterior sobreviveu ao `deploy.ps1` e o backend novo subiu
  `degraded` com `appium_log_masking_off` (credencial bloqueada). Resolvido matando o Appium órfão e religando a
  tarefa `farm-central`.
- Design da segunda evolução (persona como pessoa, contas, imagens, N:N, roteamento, provisionamento, painel) em
  [`docs/design/persona-e-parque.md`](docs/design/persona-e-parque.md) (`07c7323`); código em andamento.

## 2026-09-27 — evolução arquitetural IMPLANTADA (`5c98735`, central e agente do worker) e provas reais

- **Deploy autorizado pelo dono:**
  - ensaio de 042–046 numa cópia do banco real;
  - `deploy.ps1`: health `ok`, migração 046, `features.skills: true`;
  - agente do notebook pelo manifesto, em `0.1.0+5c98735`, com `start`/`stop` remotos `succeeded`.
- **Provas reais** (detalhe em [`relatorio-validacao.md`](docs/relatorio-validacao.md) §14):
  - a fatia `ig.abrir_conversa@1` no android-06 (`r-20260927230248-2ae798`), com a conversa comprovada pela prova
    local, sem IA;
  - `mode=plan` com `plan_report`;
  - resolução de intenção real;
  - ensino v2 com candidata gerada pelo Opus 5.5;
  - gasto de ~US$ 0,16.
- **Plano-100:** fase 15 registrada (15.1–15.14 por `aplicar`; 15.15, o K restante, pendente).
- **`deploy.ps1`:** a espera pela saúde passou de 120 s para 300 s.
- **Achados para depois:**
  - 2 de 23 fluxos reais convertem em skill;
  - receita de OPEN_THREAD não aprendida (username digitado sem arroba);
  - android-01 sob pressão e sessão do lucas travada no contador de tela não reconhecida;
  - Ollama local fora do ar (o ator foi para o Sonnet).

## 2026-09-27 — evolução arquitetural, J e K1: conversão de fluxos legados, manifesto de app e SessionProvider, app de QA e processo cross-app (integrado na `main`; NÃO implantado)

- **Fluxos legados (J, ADR-037):**
  - descompilador `Plan` → DSL, com ida e volta exata em fluxos de formato de produção;
  - adotar e desfazer: `POST /api/flows/{id}/adopt` e `/release`, e
    `POST /api/skills/{id}/versions/{n}/decompile`;
  - `PUT /api/flows/{id}` com 409 `command_published`;
  - a v1 adotada grava a skill e também o fluxo;
  - bateria `[legado|novo]`: mesmo plano, mesmas receitas, mesma conta de IA.
- **Painel:** converter e desfazer, e as transições de versão (validar, publicar, desabilitar), com a recusa do domínio
  na linha.
- **Manifesto de app (K1, ADR-039):**
  - `AppDefinition` e registro de `SessionProvider` por pacote;
  - o Instagram é a primeira implementação;
  - o núcleo não compara mais com `"instagram"`, travado por teste AST.
  - O app de QA entra só pelo manifesto, em teste, e roda uma skill. Um processo cross-app (Instagram + QA) roda pelo
    caminho de skills.
- **Correção pós-merge:** os recursos (H2) passaram a pedir a sessão ao registro, em vez de a `s.instagram`.
- **Provas:**
  - `simulated`: suíte SQLite 2421/2421 no merge final (16 min 48 s); mypy estrito em 97 arquivos; vitest 486/486.
  - `not_run`: PostgreSQL de J/K1, conta real e conferência visual.
- **Suíte:** a primeira rodada do merge final ficou 5 h parada em
  `test_worker_agent.py::test_inscricao_grava_a_credencial_e_a_reconexao_usa_ela`. Não reproduziu isolado nem na
  repetição. O fechamento do teste ganhou prazo: se voltar, reprova em 10 s dizendo onde.
## 2026-09-27 — auditoria de usabilidade do painel (documentação; nada de código)

Pedido do dono (27/09): conferir usabilidade, layout e otimização do painel antes de encerrar a evolução arquitetural.
Relatório em [`docs/auditoria-ux-2026-09-27/`](docs/auditoria-ux-2026-09-27/README.md): typecheck, 480 testes e build
verdes em `9276d63` (chunk único de 789 kB); 93 capturas em 1440 e 390 px com backend simulado; 5 achados que bloqueiam
o uso (Foco recortado em celular, publicar habilidade sem caminho no painel, erro de carga mostrado como lista vazia),
11 de atrito e 6 de desempenho, com o plano da fase L para a sessão da evolução. Prova `simulated`.

## 2026-09-27 — evolução arquitetural, H parte 2 e K2: apply/verify/reconcile dos recursos, `plan_report` no `mode=plan`, `models.py` fatiado e máquinas de estado de execução (integrado na `main`; NÃO implantado)

- **Recursos declarativos (H parte 2):** `apply`, `verify` e `reconcile` dos quatro providers pelos mecanismos que já
  existem.
  - `device.state`: `pedir_ciclo_de_vida`.
  - `app.installation`: `_entregar` e `verify_on`.
  - `app.session`: `ensure_session`.
  - `account.binding`: só verifica, porque o vínculo é decisão de pessoa.
  - Regras: chave de idempotência por recurso; `uncertain` nunca repetido; `reconcile` só no hospedeiro e só fecha
    com leitura posterior; só converge com `on_missing: apply`.
  - **Nada chama `apply` ainda**: o runtime não muda.
- **`POST /api/runs` com `mode=plan`** passa a devolver `plan_report`, sem aplicar nada. Mudança aditiva.
- **`materialize`** grava `objectives.resource_plan` quando a skill declara recursos.
- **`models.py` fatiado (K2):** 29 dos 41 corpos de requisição foram para `modules/<ctx>/presentation/schemas.py`, com
  reexport.
  - O arquivo foi de 1.836 para 1.554 linhas.
  - O OpenAPI e o esquema de cada classe ficaram idênticos.
- **Máquinas de estado formais (ADR-038)** para execução, objetivo, etapa e tentativa
  (`modules/execution/domain/states.py`).
  - O `Repository` confere e registra transição fora da tabela, mas não bloqueia.
  - Um fixture reprova teste que produza transição fora da tabela.
  - Na suíte, 3.585 transições reais caíram todas na tabela, depois de corrigir dois atalhos de teste.
- **Provas:**
  - `simulated`: suíte SQLite 2376/2376 no merge H2 + K2.
  - `not_run`: PostgreSQL, aparelhos reais e a ligação do `apply` no ciclo.

## 2026-09-27 — evolução arquitetural, fase F: ensino v2 (integrado na `main` em `578fe36`; NÃO implantado)

- **Ensino v2** (`modules/skills`, tabelas da 044):
  - fontes: instrução, demonstração (gravação v1 ou execução concluída), híbrido, correção e execução bem-sucedida;
  - turnos de pergunta e resposta;
  - `SkillCandidate` com envelope `{document, annotations}`: o generalizador pergunta em vez de inventar;
  - a candidata vira RASCUNHO de `skill_versions` numa transação; nada é publicado sozinho.
- **Credencial nunca entra em skill:** instrução, resposta ou documento com senha, token ou código é recusado ou
  mascarado.
- **Rotas** `/api/skills`, `/api/teaching-sessions` e `/api/skill-candidates`, atrás de `skills.enabled`
  (desligado: 404 `skills_disabled`), e `Health.features.skills`.
  - `/api/skills/resolve` passou a 404 `skills_disabled`, pela coerência, e ganhou 409 `content_tampered`.
  - As rotas `/api/training*` estão intactas.
- **Painel,** só com `features.skills`:
  - candidata na revisão do treino, com perguntas e salvar como rascunho;
  - lista de habilidades em Configurações.
  - Desligado, o painel fica idêntico.
- **Provas:**
  - `simulated`: suíte SQLite 2269/2269 no merge com A–I; vitest 480/480, typecheck e build.
  - `not_run`: generalização com IA real (paga), PostgreSQL, conferência visual e aparelho.

## 2026-09-27 — evolução arquitetural, fase I: resolução de intenção em cadeia com parâmetros tipados (integrado na `main`; NÃO implantado)

- **Cadeia do `IntentResolver`** (`modules/skills/application/intent_resolver.py`):
  1. modelos: o casamento de hoje, pelo registro, com escopo;
  2. tipos: `handle` (com e sem `@`, link de perfil vira `@nome`), `integer` (inclusive por extenso), `boolean` em
     português, `enum`, `url`, `string` e `text`, com `pattern` e `max_length`;
  3. semântica e LLM: só portas, com o provedor nulo. Não chamam IA; `not_run`.
- **Com `skills.enabled` ligado, o que muda:**
  - empate entre skills, valor inválido para o tipo ou parâmetro vazio viram `needs_input` com a pergunta
    estruturada, sem plano parcial;
  - antes ganhava o primeiro candidato ou ia ao planejador.
- O fluxo legado continua idêntico, com paridade provada em tabela.
- **Rota nova:** `POST /api/skills/resolve` resolve sem criar execução e traz `gated_by_config`.
- **Provas (`simulated`):** suíte SQLite 2253/2253 no merge A–I. PostgreSQL e prova real ficam `not_run`.

## 2026-09-27 — evolução arquitetural, fases G e H (parte 1): fatia "abrir conversa no Instagram" pelo caminho de skills, recursos declarativos (integrado na `main` em `eb9ba02`; NÃO implantado)

- **Fatia vertical, com `skills.enabled` ligado:**
  - Um comando que casa uma skill publicada vai do registro ao compilador e segue pelo mesmo `Plan`, pelo
    `materialize` e pelo executor de sempre, com prova local pelo `CatalogCapabilityProvider`.
  - O planejador não é chamado.
  - A trilha da 045 é gravada: `runs`, `steps` e `attempts` com skill, versão, nó e estratégia, e
    `ai_calls.attempt_id`.
  - Na 2ª execução, a receita reproduz sem IA.
  - Composição: `ig.ler_conversa` usa `ig.abrir_conversa`, com `depends_on`.
- **Com as skills desligadas (padrão),** o comportamento é o de antes.
- **Mudanças visíveis:**
  - `GET /api/flows/match` passa a respeitar `ai.flows` e devolve `skill_ref`.
  - `PUT /api/flows/{id}` e `DELETE /api/flows/{id}` recusam, com 409 `flow_adopted`, o fluxo adotado por uma skill.
  - O 409 de `preflight` vale também para os apps exigidos por skill.
- **Receitas:** o seletor com o username sem arroba vira parâmetro e a reprodução aceita as duas grafias. Antes, a
  receita reproduzida para outra pessoa abria a conversa errada (`eb9ba02`, K-037).
- **Recursos declarativos (H, parte 1):**
  - `ResourceSpec`, com `diff`/`plan` puros e a leitura dos 4 providers (`device.state`, `app.installation`,
    `account.binding`, `app.session`);
  - `PlanReport`;
  - `unknown` nunca vira "em ordem";
  - sem `apply`/`reconcile` e sem fiação.
- **Precondição de deploy:** a execução grava sempre nas colunas da 045, então as migrações 042–046 têm de estar
  aplicadas, com o ensaio em cópia (ADR-020).
- **Provas:**
  - `simulated`: suíte SQLite 2116/2116 em `eb9ba02`, e PostgreSQL verde no CI para A–E e H em `793fe00`.
  - `not_run`: G em PostgreSQL até o próximo CI agendado ou manual, e a prova `real` numa conta do Instagram, que
    exige autorização.

## 2026-09-27 — evolução arquitetural, fases C, D e E: capabilities, skills versionadas, DSL `automation/v1alpha1` e compilador (integrado na `main` em `cf9bbf4`; NÃO implantado, nada ligado no runtime)

- **Capabilities** (`app/modules/capabilities`): `CapabilityDefinition` (operação semântica), `StrategyKind` e as
  portas `CapabilityProvider`/`ExecutionStrategy`. O `CatalogCapabilityProvider` lê o catálogo legado 1:1, e o
  `verify` dele envolve a prova local.
- **Skills versionadas** (`app/modules/skills`, migrações 042–046):
  - `SkillDefinition`/`SkillVersion` com estados e transições explícitos;
  - conteúdo congelado ao sair de `draft`, com gatilho na 046;
  - hash canônico;
  - `SkillRegistry` com dois backends: `SqlSkillRepository` e `LegacyFlowAdapter`, só leitura sobre `flows`,
    `flow:<id>@1`;
  - flag `skills.enabled`, padrão `false`.
- **Tabelas novas:** `skill_*`, `skill_validation_*` e `teaching_*`, além das colunas de trilha (anuláveis) em
  `runs`, `objectives`, `steps`, `attempts` e `ai_calls`.
- **DSL e compilador:**
  - contrato Pydantic `automation/v1alpha1` (`extra=forbid`, esquema JSON congelado);
  - IR `ProcessGraph`/`ProcessNode`;
  - compilador com 45 códigos `E_*`, cada um com fixture;
  - expansão de `uses` com `depends_on` sempre emitido;
  - baixa para o `Plan`/`PlanStep` atuais por `build_step`, com `PlanStep.origin`, que é opcional e sai do JSON
    quando vazio.
- **Invariante provada por teste:** o compilador é o único produtor de `Plan` para skill nova e nunca gera nem
  executa Python (regra D15, por AST).
- **Provas:** a suíte SQLite passou 1962/1962 no merge das fases A–E (`simulated`), e o mypy estrito ficou limpo em
  40 arquivos. A 042–046 em PostgreSQL rodou no CI por `workflow_dispatch` (resultado no estado atual); o deploy fica
  `not_run`.

## 2026-09-27 — evolução arquitetural, fases A e B: monólito modular com regras verificadas, despacho fora da API, contratos do worker (integrado na `main`; NÃO implantado)

Pedido do dono (27/09): monólito modular incremental e plataforma de skills. Design em
[`docs/design/evolucao-arquitetural.md`](docs/design/evolucao-arquitetural.md); decisões em ADR-030 e ADR-031.

- **Regras de dependência como teste** (`backend/tests/test_arquitetura.py`, por AST):
  - camadas puras sem infraestrutura, e biblioteca de infraestrutura só onde já morava;
  - fecho exato do agente do worker;
  - zero ciclo de topo, e os ciclos em execução só encolhem;
  - catracas de import tardio e `Any`, e contextos novos em DAG.
- **Tipagem gradual:** `requirements-dev.txt` com mypy 2.3.1, fora do venv de produção, e `mypy.ini`. O job
  `backend-tipos` reprova o código novo (`app.contracts`, `app.modules`) e só mede o legado (124 erros em `fc5f1eb`).
- **Despacho de comandos fora de `api.py`** (`commands/despacho.py`):
  - o ciclo real `api ↔ state` sumiu e `api.py` foi de 3.544 para 2.606 linhas;
  - os imports tardios caíram de 78 para 61;
  - a recusa virou `DespachoRecusado`, traduzida na borda HTTP para o mesmo corpo de antes.
- **`AppRepository`** (`modules/applications`) é o único que escreve na tabela `apps`.
- **Contratos do worker** (`app/contracts/worker`):
  - protocolo e vocabulário de verbos, com reexportação que preserva a identidade dos objetos;
  - esquema do fio congelado (`18285a7c65c51551`, igual antes e depois);
  - manifesto único do pacote do agente (`backend/worker-manifest.txt`), lido pelos dois instaladores e pelo deploy;
  - corrigido o `ImportError` latente `adb.py → conectividade → models` no agente instalado (K-034).
- **Instagram:** a prova local de `OPEN_THREAD` passou a exigir o campo de escrita da conversa. Antes, uma linha da
  caixa de entrada com o mesmo nome bastava. A gramática `selector:` ganhou `&`.
- **Aprendizado K-036:** `"bash"` solto num subprocess do Windows roda o bash do WSL.
- **Provas (`simulated`):** suíte SQLite 1688/1688 e `scripts/tests` 150/150 no merge das fases, e mypy estrito
  limpo em `app.contracts` e `app.modules`. PostgreSQL e o agente de campo ficam `not_run`.

## 2026-09-27 — `probe-image.ps1`: snapshot restaurado provado pelo uptime, não pelo log (script de bancada, fora do serviço)

Prova:
- `simulated`: `scripts/tests/test_probe_image.py`, com a função tirada da AST e os casos reais do piloto de 27/09 e
  da fase 0 de 17/09;
- `real`, só leitura: os `.log.wake` do piloto, relidos, têm `Successfully loaded snapshot` no arquivo final;
- `not_run`: o probe corrigido, que cria AVD temporário e consome RAM do host de produção (exige autorização).

### Código
- `loaded_from_snapshot` passa a ser o veredito do uptime: restaurado quando o `/proc/uptime` lido depois de acordar
  passa do tempo de parede decorrido até a leitura mais 10 s; uptime ilegível dá `null`. O regex no log
  (`restored_by_log`) fica só como informação, porque é lido com o emulador vivo e o stdout ainda não desceu ao disco
  (falso negativo nos 4 braços do piloto). Campos novos: `restored_by_uptime`, `restored_by_log`,
  `wake_elapsed_at_uptime_s`, `uptime_margin_s`.

### Documentação e processo
- K-035 em `conhecimento/aprendizados.md`; nota do campo no §9 de `relatorio-desempenho.md`.

## 2026-09-27 — desafio bloqueia o perfil (ADR-029), contas travadas desatreladas, B20 e B7 (implantado: `8f7b94c` em 27/09 ~03:28 UTC, central e agente do worker, conferido em `/api/health`)

- **Instagram:** na entrada da sessão em `auth_challenge`, o perfil passa sozinho de `active` a `blocked`
  (`bloquear_por_desafio`, nos dois caminhos que gravam o desafio). A porta de sessão e a distribuição já recusavam
  perfil fora de `active`. Pausa do dono não é reescrita. Resolver a tela não reativa.
- **Dados de produção, a pedido do dono:**
  - cinco contas travadas desatreladas de persona e aparelho, mantidas como `blocked`;
  - 29 objetivos da bateria de 24–25/09 abandonados no android-09 (B20).
- **CI:** `npm run build` no job do painel (B7).
- **`probe-image.ps1`:** snapshot restaurado provado pelo uptime (K-035, `cfb8b43`).

## 2026-09-26 — evolução de desempenho: prévia e observação sob demanda, medição, reserva de RAM (ADR-027, ADR-028) (implantado: `a90a6e1` em 27/09 ~01:35 UTC, central e agente do worker, conferido em `/api/health`)

Pedido do dono de 26/09 (coordenação multiagente, frentes F1 a F8). O relatório está em
[`docs/relatorio-desempenho.md`](docs/relatorio-desempenho.md), e o checkpoint em
[`docs/handoffs/evolucao-desempenho.md`](docs/handoffs/evolucao-desempenho.md).

Prova:
- `simulated` (harness): backend e vitest verdes nos arquivos afetados, bancada `scripts/bench.py`;
- `real`, só leitura: a linha de base da produção em `57a155f`;
- `real`: deploy e provas de 27/09 (relatório §9), com prévia sob demanda, captura no worker, B21, piloto do
  renderer e contêiner no CI;
- `not_run`: medição de CPU do host e densidade de emuladores.

Para implantar, com autorização: `npm run build` antes do `deploy.ps1`, porque o painel novo manda o `watch`. O
`dist` velho segue funcionando como painel antigo. A atualização do agente do worker (`worker-install.ps1`) instala
o Pillow. Não há migração.

### Código
- **Medição** (F1): `metricas.py` (agregado em memória, janela de 15 min em `measurements`), `GET /api/desempenho`
  (com `?dias=N`, o histórico p50/p95/n por entidade, de `desempenho.resumo`), `scripts/bench.py` (modos
  `simulado`, `leitura` e `comparar`) e `eval_run.py` seguro sem `--yes`.
- **Prévia sob demanda** (F2):
  - o painel declara o que vê (`watch`) e, sem espectador, não há screencap de prévia (estado `paused`);
  - painel antigo segue como antes;
  - a volta atrás é `preview_mode: always`, sem reinício.
- **Tela sensível fora da prévia** (F2): frame marcador sem imagem, `/frame` 404 `sensitive_screen`, captura
  pausada durante `type_secret`, e a VM-loja sempre oculta (ADR-014).
- **Observação com a árvore primeiro** (F2): a imagem só quando precisa, o PNG decodificado uma vez, e o login do
  Instagram lê só a árvore.
- **Receitas** (F3): funil medido, `aproveitamento` em `GET /api/flows/cobertura`, e o desbravador visível
  (`wait_reason: pathfinder`), medido, agrupado por compatibilidade e solto quando o líder falha. A receita
  divergida continua sem escalar de modelo; o comentário que prometia foi corrigido, e escalar é decisão do dono.
- **Recursos** (F5): `devices/recursos.py` (cgroup e PSI), reserva de RAM por boot no worker, admissão com
  `reserved_mb` e recusa explicada com batida velha, e exemplos de config sem `ram_mb: 1536`.
- **Transporte e posse** (F4, fase A):
  - o NATS endereça a réplica hospedeira e aplica `ack_wait`;
  - a cerca é serializada por aparelho, sem `UNIQUE`;
  - reentrega depois do `result_ack` não reexecuta;
  - resultado tardio não reescreve o aparelho;
  - o `welcome` negocia `accepted_features`.
- **Contêiner** (F6): `deploy/` com o Dockerfile do central e o compose de validação. O build ficou `not_run`: o
  Docker Desktop não é suportado em Windows Server.
- `version.py` lê o commit também de um `git worktree` (K-033), e `metricas.py` entra no pacote do agente (K-034).

### Documentação e processo
- ADR-027 e ADR-028; o adendo v0.20 de `api-contract.md`; o relatório de desempenho; a seção de contêineres em
  `operacao.md` (§14) e a tabela de scripts com `bench.py`; e a atualização de `ia.md`, `dominios/parque.md` e
  `arquitetura.md`.

## 2026-09-26 — todos os aparelhos sempre na versão promovida (ADR-026) (implantado: `57a155f` em 26/09 ~21:37 UTC, central e agente do worker, conferido em `/api/health`)

Branch `claude/sempre-na-versao-promovida` (PR aberto, não integrado). Decisão do dono de 26/09: "todos devem ficar
atualizados sempre". Prova `simulated` (`backend/tests/test_sempre_na_promovida.py`, mais `test_loja_de_apps.py`,
`test_release_lifecycle.py`, `test_distribute.py`, `test_app_releases.py`); a real ficou `not_run` (procedimento no PR).

### Código
- Promover (`lifecycle`, `verb: promote`) faz cada aparelho que TEM o app, principal ou secundário, perseguir a
  promovida. O ligado e livre instala já, o ocupado na varredura de 60 s, o desligado quando liga; ninguém é ligado.
  A resposta ganha `target_release_id` e `devices[]` (`kept` é valor novo). Nada é instalado em quem não tem o app.
- Quem entra no ar, e cada passada da varredura, adota a promovida de todos os apps que tem
  (`AppState.adotar_promovidas`). O app principal entra na entrega sem tarefa, fora de objetivo no meio.
- Voltar um aparelho leva o parque de volta à promovida anterior, com `-d`. Recusa do Android fica
  `downgrade_refused`, sem nova tentativa sozinha; o desejo que apontava para a versão voltada se realinha em vez de
  bloquear a tarefa. A quarentena não rebaixa ninguém, e o canário em prova também não é rebaixado.
- `promoted_release`: empate de `version_code` com desempate estável (promoção mais recente, depois id), ordenado em
  Python (K-030). A vitrine e o painel mostram a mesma escolha. Promovida de mesmo número conta como atualizada.
- A nova tentativa diária de entrega conta também a prova de instalação: a entrega sem tarefa não abre comando e
  rearmaria a cada passada (K-032).
- O app secundário não fica na frente depois da prova de abertura: `install_on` volta à tela inicial e faz
  `am force-stop` do pacote conferido quando ele não é o app principal do aparelho. Medido na produção em 26/09: o
  app de QA distribuído ao android-01 (conta Instagram) ficou em primeiro plano e dois "Abrir app" do Instagram
  terminaram `uncertain`. "Abrir app" também volta à tela inicial antes do `am start` quando outro app está na frente.

### Painel
- Toast da promoção com o resumo dos aparelhos; o diálogo de volta avisa que os outros aparelhos voltam sozinhos.

### Documentação e processo
- ADR-026; `dominios/apps-e-loja.md` (seção "Todos na versão promovida", fim da pendência do dono); adendo v0.19 de
  `api-contract.md`; K-032.
- Revisão do PR #13 (Codex): a versão voltada que chegou ao aparelho mas falhou na prova de abertura também volta
  (`release_no_aparelho` reconhece a release pelo número observado, e a trava diária não segura o alvo novo);
  objetivo `uncertain` também segura a troca automática do app principal; o relógio da tentativa diária conta só
  os comandos DESTE app que saíram do central.

## 2026-09-26 — cerca depois de banco restaurado (B4, integrado do PR #3 de 25/09) (implantado: `57a155f` em 26/09 ~21:37 UTC, central e agente do worker, conferido em `/api/health`)

**Não implantado.** Vale só com o central e o agente do worker atualizados.

### Código
- B4 / [K-004](docs/conhecimento/aprendizados.md): depois de restaurar um banco antigo, o agente recusava todo
  despacho como "cerca anterior à última executada". Agora o `hello` do agente traz `fences` (a maior cerca por
  aparelho, lida do diário) e o central sobe a cerca do comando ainda `created` para acima dela antes de despachar
  (`CommandStore.elevar_cerca`). Campo opcional, sem mudar `PROTOCOL_VERSION`: o agente antigo continua aceito.
  Prova `simulated`: `backend/tests/test_cerca_restaurada.py`.

## 2026-09-26 — prontidão sem efeito tardio não idempotente; achados pós-merge do PR #7 (implantado: `37bb6e6` em 26/09 ~19:30 UTC, central e agente do worker, conferido em `/api/health`)

Branch `claude/prontidao-sem-efeito-atrasado`. Prova `simulated` (`backend/tests/test_prontidao_sem_efeito_atrasado.py`,
`test_prontidao_subsistemas.py`, `test_prontidao.py`, `test_worker_executor.py`); a real ficou `not_run` (procedimento
no PR).

### Código
- O portão de prontidão só tem efeitos idempotentes (K-031). O preparo não toca mais na tela: o diálogo de sistema
  é dispensado depois da prontidão, antes da sessão de automação, confirmando o mesmo diálogo na mesma chamada do
  toque. O relógio saiu do `start`/`wake` do worker e do `_wait_boot` do central: virou condição própria (medir →
  acertar → conferir, na entrada no ar e a cada 5 min), que desfaz um `set-time` caído atrasado; sem o fallback
  `adb root`. A medida do desvio desconta a ida e volta do adb.
- Achados da revisão pós-merge do PR #7: wake local com o relógio travado não descarta mais o snapshot; `ready`
  guarda o detalhe da escada; display com 20 s e piso de uma rodada inteira; readoção incerta espera 30 s antes da
  próxima tentativa; adb `device` nunca vira `stopped`; falha de código na sonda vira `erro` logado; o preparo do
  worker devolve o tempo à escada; uma linha INFO por rodada com o tempo de cada degrau.
- Revisão do PR #12: toda saída do ar (parar, hibernar, perder, soltar, degradar) cancela também as tarefas do
  relógio e da arrumação (`TAREFAS_DO_NO_AR`), e a medida que termina com o aparelho fora do ar não acerta a hora
  nem avisa. `test_boots_sobem_um_a_um_com_boot_parallelism_1` deixa de oscilar sob carga (espera o 1º boot).

## 2026-09-26 — cofre: relatório da recifragem em ordem determinística (implantado: `37bb6e6` em 26/09 ~19:30 UTC, central e agente do worker, conferido em `/api/health`)

- `rekey.recifrar` ordena as refs em Python (ponto de código) em vez de `ORDER BY ref`, que no PostgreSQL segue a
  colação `en_US.utf8` e fazia `test_rekey_recifra_o_cofre_inteiro_para_a_chave_nova` falhar ao acaso no CI (run
  36256295444). Branch `claude/rekey-ordem-deterministica`. Prova `simulated`
  (`backend/tests/test_secret_store.py::test_rekey_relata_na_mesma_ordem_seja_qual_for_a_colacao_do_banco`, colação
  imitada no SQLite); PostgreSQL real `not_run` localmente, CI disparado na branch. K-030.

## 2026-09-26 — CI verde de novo: fixture do PostgreSQL e fronteira da varredura de credencial (implantado: `3da3bb5` em 26/09 ~18:55 UTC, conferido em `/api/health`)

Branch `claude/trusting-carson-9u67ii`. Prova `simulated` (`backend/tests/test_perfil_bloqueado_e_capacidades.py`,
em SQLite e em PostgreSQL 16 local).

### Código
- `test_estimativa_de_custo_por_fluxo` inseria `ai_calls.tier='fast'` numa coluna `INTEGER`. O SQLite aceitava, o
  PostgreSQL não, e o job agendado ficou vermelho desde 23/09. A fixture passa a gravar `0`, o que
  `Repository.add_usage` grava de fato. A asserção fica igual. Sem mudança de produção nem de migração (K-029).
- `purge_stale_run_secrets` passa a comparar com `<=` ("parada há pelo menos o prazo"): com `<`, prazo 0 não pegava
  a execução parada no mesmo milissegundo, e `test_pendencia_mantem_a_credencial…` oscilava no CI (run 36262415463).
  Teste novo com o relógio congelado nesse caso; falha no código anterior.

## 2026-09-26 — loja de aplicativos e proxy do aparelho (implantado: `3da3bb5` em 26/09 ~18:55 UTC, conferido em `/api/health`)

Branch `claude/loja-de-apps`. Prova `simulated` (`backend/tests/test_loja_de_apps.py`,
`frontend/src/features/loja/LojaPage.test.tsx`, painel no navegador contra o harness com aparelhos falsos). Nada foi
instalado nem configurado em aparelho real: `not_run`. Os testes rodaram só em SQLite; PostgreSQL `not_run` (o
contêiner de teste da porta 55433 não estava no ar, e subir o Docker mexe no WSL).

### Código
- Aba **Loja** no menu Aplicativos. A vitrine (`GET /api/app-store`) mostra o ícone, a versão promovida, os
  aparelhos por versão e a "atualização para N". Há cadastro de app com categoria (migração 041), envio de
  APK/XAPK e a Play Store da loja por app.
- `distribute` ganhou `instance_ids`, `count` e `dry_run`. O painel distribui para todos, N ou os escolhidos, com
  prévia obrigatória, e "Atualizar para X" marca quem está atrasado. A volta de versão pode ser em lote.
- Versão de pacote não cadastrado cadastra o app sozinha. App que não é o principal do aparelho instala quando ele
  liga.
- Proxy do aparelho: aba **Proxy**, `/api/proxies*`, comando `device.proxy`, conferido por releitura de
  `settings global http_proxy`.
- Revisão do PR #10: contêiner com teto de 2 GiB extraídos (bomba de zip não enche o disco) e extração parcial
  sempre limpa; pedido de proxy trocado enquanto o anterior era aplicado volta a `pending` em vez de ficar perdido
  sob um `applied` do pedido velho.


## 2026-09-26 — a automação entra com a credencial que a pessoa fornece (implantado: `3da3bb5` em 26/09 ~18:55 UTC, conferido em `/api/health`)

Branch `claude/credenciais-na-automacao`. Decisão do dono (ADR-025). Prova `simulated`
(`backend/tests/test_credenciais_da_execucao.py`).

### Código
- `POST /api/runs`: campo `credentials` (cofre, apagado no fim da execução) e consentimento explícito
  (`consentimento_de_credencial`); comando com senha no texto é recusado antes de gravar (`credencial_no_comando`) e
  `runs.command` passa pela redação. Origem: execução `22d65f`, cuja senha ficou em claro no banco e foi ao planejador.
- Ferramentas `type_secret` (canal sensível, só campo de senha, só no app da etapa e no site pedido) e `open_url` (só
  endereço do comando); tela de senha não para a execução que tem credencial; desafio e CAPTCHA continuam com a
  pessoa.
- Revisão local (code-review xhigh): credencial mantida em `completed_with_issues` e varrida após 24 h parada; 422 sem
  eco de valor sensível; cofre antes da execução (nada órfão); `usuário:senha@` em URL recusado; texto citado não
  tira o comando do catálogo; painel não guarda nem envia comando com senha e limpa o histórico antigo.
- Segunda rodada: só o texto do comando autoriza endereço (`open_url`) e site da senha — parâmetro do plano não;
  `)` que faz parte da URL fica; "página" não tira comando do Instagram do catálogo; execução cuja credencial não
  se ligou vai a `failed` em vez de ficar em `planning`.
- O planejador não fica preso ao catálogo do app do aparelho quando o comando pede site ou outro app; Chrome no
  `config.example.yaml`. Prompts: regra de conduta (sem desinformação, sem ofensa explícita).
- Painel: campo "Senha para a automação" (só em memória) e confirmação antes de criar a execução.

### Operação
- 26/09 16:40 UTC: senha da execução `r-20260926161438-22d65f` mascarada em `runs.command` no banco de produção.

## 2026-09-26 — prontidão por subsistema (implantado: `5b81c1a`, conferido em `/api/health`)

Branch `claude/prontidao-por-subsistema`. Prova `simulated` (`backend/tests/test_prontidao_subsistemas.py`).

### Código
- Pronto = servicemanager + system_server + display respondendo (`devices/prontidao.py`), a mesma definição no
  worker (`start`/`wake`) e no central (entrada no ar). Preparo que estoura o prazo não fecha mais `succeeded` com
  o `service check` sozinho (a lacuna do wake de 25/09).
- Contrato temporal: estouro de prazo no preparo ou no acerto do relógio (worker e central, boot, wake, readoção e
  adoção externa) deixa a tentativa não pronta — efeito incerto no aparelho; a chamada zumbi do executor é drenada
  com teto antes de devolver. Erro rápido depois da prontidão exige rodada nova (`AdbError` pode ser `device
  offline`); erro benigno segue sem bloquear. Limitação conhecida: efeito tardio de um timeout no mesmo guest
  (`input tap` do diálogo, `cmd alarm set-time`) não é isolado entre tentativas — tarefa separada.


## 2026-09-26 — identidade do backend em /api/health (implantado com `5b81c1a`)

Branch `claude/supervisor-identidade`. Prova `simulated` (`backend/tests/test_identidade_do_backend.py`).

### Código
- `Health.service = "android-farm-central"`; o supervisor só trata como "backend vivo" o health que identifica a
  Farm (com reconhecimento legado estrito do esquema antigo). O 404 do `cartorio-api-1` na 8000 não segura mais a
  subida. `deploy`/`start`/`stop`/`restore`/`loja-janela` usam a mesma regra (`scripts/lib/farm-health.ps1`); o
  `stop.ps1` não envia mais o token de encerramento a quem não for a Farm.

## 2026-09-25 — prontidão real e sondas com trilha própria (implantado com `5b81c1a`)

Branch `claude/prontidao-e-sondas`. Prova `simulated` (`backend/tests/test_prontidao.py`); o wake remoto que a
motivou não foi reproduzido (evidência preservada no worker).

### Código
- `online` e `start`/`wake` do worker exigem o framework respondendo (ANDROID_RESPONSIVE), não só adb +
  `boot_completed`; `InstanceDTO.readiness` mostra o degrau; framework mudo = `booting` com motivo, depois degradado.
- Sondas de saúde, pressão e internet numa trilha própria por aparelho: a captura travada não as cala mais.

## 2026-09-25 — validação runtime do android-06 (fase local, implantado `9acba15`)

Branch `claude/awesome-lamport-s602ai` (PR #4), implantada no central. Prova `real`: ver
[`docs/handoffs/android-device-persona-runtime.md`](docs/handoffs/android-device-persona-runtime.md).

### Código
- Internet do aparelho separada de `online` (`devices/conectividade.py`, `InstanceDTO.connectivity`): sonda adb só
  leitura (rota, DNS, TCP 443, `VALIDATED`, 2ª tentativa), `unknown` a cada entrada no ar, aviso "sem internet: …",
  `409 device_no_internet` no Conectar; linha "Internet" no contexto operacional.
- `android.dns_servers` por máquina → `-dns-server` (o emulador só usava o 1º DNS IPv4 do host, que estava morto).
- Popover em posição fixa presa à tela (o menu "Instalar app" cortava a versão).
- "Verificar app" carimba `verified_at` também quando o app está ausente; hibernar remoto diz a causa real.
- Despacho: `AppCapabilities.requires_internet`; tarefa de app que precisa de rede espera aparelho sem internet
  confirmada (tarefa local segue).
- Boot remoto em andamento é `booting`, não `error` "system_server caiu".
- Login real do André no android-06: `session_ready` pelo @ lido na tela; observação pós-envio 25 → 45 s.

## 2026-09-25 — aparelho × persona × app × sessão (fase cloud)

Branch `claude/awesome-lamport-s602ai`. Prova `simulated`; a validação real está em
[`docs/handoffs/android-device-persona-runtime.md`](docs/handoffs/android-device-persona-runtime.md).

### Código
- Portão único de sessão (`social/sessao_gate.py`): Conectar/Verificar conta/Sair só com o app observado no aparelho;
  o perfil traz `app_on_device` e `session_actions`, e a rota recusa com `409 app_not_installed`/`app_not_verified`.
- "Instalar app" diz app e versão promovida antes do clique; sem versão promovida, recusa antes do 202;
  `install_target` na resposta. Instalar e abrir escolhem o app cada um.
- `InstanceDTO.stream` separa `stale` de `device_offline`, `worker_offline` e `capture_error`; a captura conta
  falhas, publica a primeira e recua até 30 s.
- Diálogo "Unable to log in" registrado como `login_error_dialog` (incerto, sem repetir sozinho).
- `GET /api/instances/{id}/operational-context` e `GET /api/instagram/profiles/{id}/operational-context`, com cartão
  no Foco e no perfil.

## 2026-09-25 — CI verde, deploy com dependências, documentação e continuidade

Implantado no central às ~01:20 UTC (`scripts/deploy.ps1 -PularFrontend`, backup `data/backups/20260924-221919`).

### Código
- 7.9: o aviso de IA diz de qual função é a frase "os dados NÃO saem desta máquina" quando o ator é local e o resto
  é externo (`planning/routing.py`).
- 10.6: teto de `boot_parallelism` igual (10) no painel, no `config.yaml` e na mensagem `limits`; comentário do
  protocolo corrigido (os limites vão na primeira batida, não junto do `welcome`).
- T.4: CI de volta ao verde — chave de teste do cofre fora do Windows, testes de PowerShell só no Windows, inspetor
  de APK lê o formato `V2 Signer:` do `apksigner` novo (defeito real), o mock de frame do Foco não depende do `Blob` do jsdom (falhava no Node 22 do CI), a saúde
  dos testes não depende de SDK/KVM do host,
  `cryptography` 50.0.0 (a 46.0.7 ainda tinha avisos; o uso do projeto é só `AESGCM`/`InvalidTag`). CI verde no run
  36078946300 (`9e12baf`).
- Deploy: `scripts/deploy.ps1` instala as dependências do backend entre parar e subir (`e6b00db`); antes, versão nova
  no `requirements.txt` nunca chegava à produção.

### Decisões delegadas (custo-benefício), implantadas em `8169fd3`
- 7.10 (ADR-024): verificador Haiku mantido, com rejulgamento escalado quando recusa com nível de entrega suficiente
  e guarda contra "sim" sobre tela sem elementos — em vez de trocar o modelo do verificador (2× o custo).
- 7.11 e ADR-023: `/api/health` acusa IA em fallback; o ator volta a ser declarado no Sonnet 5 (config de produção).

### Validação
- Bateria de avaliação autorizada (ADR-018), ~US$ 2,57: rejulgamento 41/56, linha de base 16/17 (`base-25-09`),
  HTTP 500 do verificador em 0,7 %. Item 7.4 registrado como `partial`/`real`. Ator no fallback (Ollama fora do ar).

### Documentação e processo
- Base de documentação e continuidade: `CLAUDE.md`, índice [`docs/README.md`](docs/README.md), produto, arquitetura,
  domínios, IA, operação, decisões (ADR), knowledge lake, roadmap, este changelog e o handoff
  [`docs/estado-atual.md`](docs/estado-atual.md); skills `retomar`, `preparar-tarefa`, `fechar-tarefa`; regras por
  caminho em `.claude/rules/`; `scripts/docs-check.py`.
- plano-100: o item 10.5 entrou no mapa de blocos (`check`/`relatorio` estavam quebrados desde `c0c982d`); relatório
  de execução regenerado (82 de 88); `scripts/tests/test_claude_plan_100.py` reescrito para o livro-razão atual.
- Decisão 2 do plano-100 tomada pelo dono: sem revogação da chave (ADR-017); 0.10 registrado por `aplicar` (83 de 88).

## 2026-09-24 — custo de IA, painel, perfis multi-app, treinamento, limites por servidor
- Custo de IA: cache de prompt sempre, preço do Opus 5.5, escalonamento por risco, provas locais (`ac18099`); dieta
  do contexto do ator e estimativa antes de rodar, 7.6/7.7 (`42f8e93`); modelo local preparado, 7.8 (`b3addfe`,
  `ac6bf69`); ator de produção ligado no Ollama local, 7.1 (`ad48634`).
- Painel: layout responsivo, 11.1–11.4 (`d305899`); seleção por estado e "Repetir", 11.5 (`6ada25a`); perfil como
  pessoa e Diagnóstico prático, 11.6/11.7 (`cb2d437`); configurações do perfil e instâncias, 11.8/11.9 (`5a60aa3`);
  rolagem do foco e teste de escala com 14 aparelhos, 10.3 (`d8a57f7`).
- Perfis: memória semeada do histórico social; decisões 3 e 4 aplicadas, 0.10/6.5 (`728e2ad`); o que o perfil vê vira
  memória (`2fa2280`, `327f7b9`); grupos de acesso, 11.10 (`a4237da`, `95ee764`); contas em vários apps e menu
  Aplicativos por app, 12.1/12.2 (`407cfce`, `6530d4d`).
- Modo treinamento, 13.1–13.3 (`bfffb0d`, `bf29d48`).
- Parque: limites por servidor e distribuição entre servidores, 10.5 (`c0c982d`); distribuir app com conta só para
  aparelho com perfil ativo (`944f158`, `f443a90`).
- plano-100: 0.1, 0.7, 6.6 e 10.1 fechados com operação real de 23–24/09 (`cc3e960`).

## 2026-09-23 — plano-100, fases 5–10 e T; operação
- Fases 5 (dois backends, fila, storage, PostgreSQL) `a45b95f`; 6b (catálogo visual, fila de intervenção, loja)
  `00330b9`; 7 (hub de IA, teto em dólar) `f94f918`; 8 (persona governa o texto) `f5015a6`; 9 (sessão nominal, TLS
  fora do loopback) `57f8a3c`; 10 (supervisão, retenção) `2564d56`; T (os nove aceites em tabela, relógio
  injetável) `4cee0a9`.
- Operação: `/docs` fechado no listener do túnel e deploy reconstrói o painel (`e03d967`); partida para quando a
  configuração some (`09c040f`); cache do `index.html` (`4204ed8`); correções de agente, supervisor e hibernação
  (`a0b211c`, `8caeb2e`); chave de host do worker conferida pelo dono (`98b4669`).
- Execução: reparo automático, personas completas (`c76ccea`); receitas sem o alvo por extenso (`aa8a43c`); fim do
  laço tocar→voltar, receita vazia e ordem no replano (`fd5a26f`).

## 2026-09-22 — plano-100, fases 0–4 e 6a; executor na sessão da IDE
- O runner externo (`claude -p`) é aposentado; a sessão orquestra com pacote e modelo por item (`21e11e9`), depois
  de `cca458d`/`352ee3e`.
- Fase 0 (backup, desvio do token, túnel com credencial, DM longa, CI) `c727284`, `db07776`; fase 1 (comando
  honesto nos dois caminhos) `7ec4cf2`; fase 2 (o central vira worker; capacidades declaradas) `1a206e0`; fase 3
  (saúde verdadeira) `f30c452`; fase 4 (escalonamento por recursos e localidade) `abe0aa2`; fase 6a (Instagram fora
  do núcleo, instalar como comando) `5e206c2`.

## 2026-09-21 — comandos, worker de verdade, PostgreSQL, auditoria
- Comandos como entidade (`074d27a`), capacidades explicadas antes de agendar (`a6307c4`), estado desejado
  (`785b9b7`), ciclo de vida no worker provado na máquina dele (`89f5508`), tela de infraestrutura (`bda8a0d`).
- SQLite e PostgreSQL provados (`c7feab4`); posse de etapa e autenticação entre backends (`1162e07`, `f1e61b3`).
- Auditoria de 181 achados e o plano-100 (`53507d2`); primeiro executor do plano (`59bc422`); PR #2 integrado.

## 2026-09-19 — persona e aprovações medidas; parque remoto (Etapa 0)
- Persona escreve na própria voz, lê a tela e quem escreveu; aprovação aprova o texto que será digitado; revisão
  adversarial registrada (`6d9e270`, `c566fdf`, `580dc42`, `285e1df`, `858a0ea`).
- Appium central dirige aparelho de outra máquina (`139b06d`); seis aparelhos remotos, 6 de 6 (`1931de3`, `c29212d`).

## 2026-09-18 — releases, loja Play Store e Instagram real
- Canário, promoção e rollback (`2aa3ef5`); aparelho-loja e "buscar da loja" (`4820e97`…`10ae9d5`).
- Login real do Instagram e leitura da conta (`d7075cd`, `f90433c` e seguintes); persona por perfil (`34c9b52`);
  aprovações agrupadas (`6f7d7fe`); suíte isolada dos emuladores reais (`36c8784`, `df9cb05`).

## 2026-09-17 — POC
- Painel React + backend FastAPI, Appium local, app de QA; custo de IA e rodízio N sobre K (PR #1, `5f3f7ea`);
  coleta e iteração sobre listas (`a127ae9`); canal de entrada sensível (`ec8cb07`); releases, perfis, Instagram,
  persona, memória, capacidades e portal (`c4c490b`…`21f276d`).

# Evolução arquitetural: monólito modular e plataforma de skills

**Documento de design.** 27/09/2026. Base: commit `82b1057` (worktree `arq`, branch `claude/arquitetura-habilidades`).
**Estado:** em implementação. **Todas as fases, A a K, estão integradas** no branch `claude/arquitetura-habilidades`
(K em duas partes, K1 e K2, que cobrem só uma parte da §16). A H está completa **sem ligação no `_tick`**: `apply` e
`reconcile` existem e nada os chama. Nada implantado. O que resta da K está na §18
([fases H, I, J e K](#fases-h-i-j-e-k), "O que resta da fase K").

- A–E: na `main` desde `cf9bbf4` (27/09).
- H parte 1 (recursos declarativos, só leitura, sem fiação): merge `1e69d02`, já na `main`.
- G (fatia vertical "abrir conversa no Instagram", com a fiação no runtime): merge `0b736f3`, mais a correção de
  receita `eb9ba02`; na `main` desde `eb9ba02`.
- I (resolução de intenção em cadeia, parâmetros tipados, `POST /api/skills/resolve`): `00633d5` e `0795cc7`, merge
  `21b1fff` no branch `claude/arquitetura-habilidades`; ainda não na `main`.
- F (ensino v2: `TeachingService`, `SkillGeneralizer`, rotas `/api/skills`, `/api/teaching-sessions` e
  `/api/skill-candidates`, painel atrás de `features.skills`): `474aceb` e `63507ad`, merge `6b04164` no mesmo branch,
  mais a correção `578fe36`; ainda não na `main`.
- H parte 2 (`apply`/`verify`/`reconcile` dos recursos pelo canal de comandos, `plan_report` em `mode=plan`,
  `objectives.resource_plan` no `materialize`): `9f76832`, `80d5fc7` e `373d45f`, merge `2fc09b2` no mesmo branch;
  ainda não na `main`.
- K2 (29 corpos de requisição fora de `models.py`; máquinas de estado de execução na fase "conferir e registrar"):
  `e7af6f0` e `48e76ae`, merge `b56e06c` no mesmo branch; ainda não na `main`.
- K1 (manifesto de app `AppDefinition`, registro de `SessionProvider`, núcleo sem comparação com `"instagram"`, app
  de QA e processo cross-app em teste): `0b7950e`, `99d851b`, `40def91`, `01d68b5`, `15dfded` e `88087d9`, merge
  `f06e34a` no mesmo branch, mais a correção `3fbe9df` (recursos da H pelo registro); ainda não na `main`.
- J (descompilador, converter e desfazer fluxos, trilha da v1 adotada, bateria legado × novo, painel): `9d2b736`,
  `4ddba1a`, `fa21cec` e `c4f40d6`, merge `5b1957f` no mesmo branch; ainda não na `main`.
- Prova: suíte SQLite 2115/2115 no merge A–H, 2252/2252 no branch da fase I, 2269/2269 na integração de F com A–I,
  2293/2293 no branch da H parte 2, 2279 no branch da K1 e 2306 no branch da J (`simulated`; os quatro últimos
  relatados pelo coordenador, fora da mensagem do merge; a suíte do merge final não está registrada aqui);
  PostgreSQL verde em `793fe00` (run `36324634678`) para A–E com 042–046 e para a H parte 1, que já estava nesse
  commit; G, I, F, H parte 2, K2, K1 e J em PostgreSQL `not_run`.

O que ainda não existe está marcado **proposto**. Referências `arquivo:linha` são relativas a
`backend/app/`, salvo indicação, e foram conferidas no commit base; o código movido ou mudado depois dele é citado
pelo lugar novo nas páginas de domínio ([capabilities](../dominios/capabilities.md), [skills](../dominios/skills.md),
[execution](../dominios/execution.md), [DSL](../skill-dsl.md), [runtime](../skill-runtime.md)).

**Prova.** Os números da §2 são medidos: análise estática por AST no commit base, run de CI `36297199520` e ensaio das
migrações 042–046 num SQLite sintético de 145 MB (`simulated`). O resto é desenho e está `not_run`. Onde o texto diz
**proposto**, a coisa ainda não existe.

**Insumos.** Oito relatórios de investigação feitos no mesmo commit (dependências, domínio, treino/fluxos/receitas,
worker, execução, banco, frontend/API e testes), guardados no scratchpad da sessão e fora do Git. **Este documento é a
versão consolidada e é ele que vale.** Os rascunhos das migrações 042–046 são resumidos na §10, na §13 e na §14, e só
viram arquivo em `backend/migrations/` na fase D.

Leitura relacionada: [arquitetura](../arquitetura.md), [decisões](../decisoes.md), [banco](../banco.md),
[worker](../worker.md), [perfis e Instagram](../dominios/perfis-e-instagram.md), [contrato HTTP](../api-contract.md).

---

## 1. Contexto e objetivos

O backend cresceu por entregas verticais rápidas. A regra de negócio de quatro contextos mora hoje no composition
root (`state.py`), o despacho de comandos mora no arquivo das rotas (`api.py`), e o conhecimento do Instagram vaza para
o núcleo. O treinamento, os fluxos e as receitas funcionam, mas não têm versão, não compõem e não separam o *quê* (a
operação semântica) do *como* (receita, IA ou pessoa).

**Objetivos do programa:**

1. **Monólito modular em Python, incremental.** DDD-lite: agregados, value objects, máquinas de estado explícitas e
   eventos de domínio só onde desacoplam. Hexagonal: portas como `typing.Protocol` do lado de quem consome, adaptadores
   na borda. Fatias verticais e inversão de dependência. Sem rewrite, sem microsserviço e sem processo novo.
2. **Plataforma de skills ensináveis:** `SkillDefinition`/`SkillVersion` imutável, `ProcessGraph`/`ProcessNode`,
   `CapabilityDefinition` (operação semântica, não gesto) e `CapabilityProvider`, `ExecutionStrategy` (a receita é
   uma estratégia, não uma skill), DSL `automation/v1alpha1` e `SkillCompiler`, `TeachingSession` v2, `ResourceSpec`,
   `IntentResolver`, PLAN estruturado, o ciclo RESOLVE→COMPILE→PLAN→PREFLIGHT→APPLY→VERIFY→RECONCILE→COMPLETE e uma
   trilha completa de execução.
3. **Compatibilidade.** Flow, Recipe e Training continuam funcionando, sem migração forçada de dados e sem escrita
   dupla.

**Não-objetivos:** trocar FastAPI, SQLite/PostgreSQL ou o `EventBus`; mudar o protocolo do worker no fio; criar fila
paralela a `steps`/`attempts`; tratar IA como domínio (ela é adaptador); escrever, antes da fase H, um provider novo
que toque aparelho.

**Princípios herdados do projeto:**

- Falha ou incerteza nunca é sucesso. Efeito disparado sem prova vira `uncertain`.
- Mover antes de editar. Shim com identidade de objeto. Teste de arquitetura antes do primeiro movimento.
- Migração aditiva. Migração aplicada não se edita.
- Mundo real (deploy, aparelho, IA paga, conta) só com autorização do dono. Sem ela, prova `not_run`.
- A suíte tem cerca de 5 min de folga no CI (§2.9). Teste novo é puro por padrão (meta: ≥ 80% de unidade ou golden).

---

## 2. CURRENT STATE

### 2.1 Tamanho e god modules

São 123 módulos e 41.059 linhas em `backend/app`. Sete arquivos somam **14.944 linhas (36%)**:

| Arquivo | Linhas | O que tem | Maior função |
|---|---|---|---|
| `api.py` | 3.544 | 195 funções, 134 rotas, 0 classes; 121 rotas anotadas `-> Any` e nenhum `response_model`; cerca de 730 linhas de despacho de comandos e 245 do canal do worker que não são HTTP | `_do_action_no_worker:1786` (116) |
| `devices/manager.py` | 3.415 | `DeviceManager:428-3361` com 153 métodos; `DeviceRuntime:259`, o agregado "aparelho" em memória; `Limiter:189`, uma primitiva de Execution; 11 callbacks `on_*` sem tipo (`:470-510`) | `_wait_boot:2024` (111) |
| `state.py` | 2.198 | `AppState:160` com 68 métodos: composition root, service locator e regra de quatro contextos | `__init__:161` (183), `health:2026` (157) |
| `models.py` | 1.820 | 125 classes: 54 DTOs, 41 corpos de requisição, 15 enums, 7 VOs, 7 contratos com a IA; fan-in de 42 módulos | — |
| `taskqueue/executor.py` | 1.450 | `StepExecutor:143` | `_run_step:597` (563, a maior do backend) |
| `taskqueue/scheduler.py` | 1.398 | `Scheduler:67` com 56 métodos e 10 portas injetadas sem tipo (`:105-142`) | `_rotate:731` (124) |
| `social/service.py` | 1.119 | `SocialService:83` com 69 métodos em sete grupos (interações, perfis, política, texto por IA, contas, memória, personas) | — |

### 2.2 Ciclos

- **Import de topo, módulo a módulo: zero ciclos.** O código já é acíclico no momento do import.
- **Em execução, contando imports dentro de função: dois componentes de 8 módulos.**
  - **SCC-A** `{api, state, devices.proxy, releases.service, taskqueue.scheduler, taskqueue.service, vitrine,
    workers.local}`. Existe só porque o `state` chama sete funções que moram em `api.py` e não são HTTP:
    - `_apps_changed:643`, `_despachar_trabalho:1697`, `_despachar:2135` e `executar_envelope:2168`;
    - `pedir_ciclo_de_vida:2282`, `remediar_reiniciando:2415` e `_tratar_mensagem_do_worker:3411`.

    Os imports que fecham o ciclo são oito: `state.py:234, 590, 601, 1321, 1641, 1720`, `devices/proxy.py:135` e
    `workers/local.py:123`. Pacote a pacote, isso junta 20 dos 30 nós num componente só: pelo `state`, todo pacote
    alcança todo pacote.
  - **SCC-B** é a fábrica de provedores de IA: `planning/provider.py:187, 191, 195, 212`, mais as implementações e
    `planning.training`. É um ciclo benigno e contido num pacote.
- **Invisível ao AST.** `planning/catalog/__init__.py:103` (`import_module`) fecha um terceiro ciclo:
  `planning.capabilities` (import local em `capabilities.py:250`) → `planning.catalog` → `planning.catalog.instagram`
  → `planning.capabilities` (`catalog/instagram.py:20`).
- **Pacote a pacote, só topo:** dois componentes por granularidade. SCC-1 é `{automation, commands, devices, events,
  models, security, workers}`. SCC-2 é `{social, taskqueue}`, fechado por `executor.py:37` (→
  `social.approvals.ler_rascunho`) e por `social/capacidades.py:19-20` (→ `taskqueue.aproveitamento` e
  `taskqueue.recipes`).

### 2.3 Imports tardios, `Any` e acoplamento

- **78 imports internos dentro de função**, 50 deles com `noqa: PLC0415`: 12 sustentam ciclo real (os 8 que vão para
  `api` e os 4 da fábrica), 12 dependem desses, 16 são redundantes e 38 são preferência. Por pacote: api 21,
  releases 16, state 14, planning 8, devices 6, social 5, workers 2, e um em cada um de automation, commands, config,
  supervisor, training e vitrine.
- **894 `Any` em anotação**, contados por AST. Os maiores: api 153, taskqueue 120, social 99, devices 91, planning 70,
  state 50 e worker 45. A contagem textual dá 985, porque inclui `dict[str, Any]` fora de anotação; a catraca usa 894.
- **Portas que existem mas são invisíveis ao verificador:** `StepExecutor.social/approvals/secrets/sensitive_input:
  Any` (`executor.py:154-168`; o canal de credencial do ADR-025 entra como `Any`), `Scheduler.policy_gate:
  Callable[[Any, Any, Any], Awaitable[Any]]` (`scheduler.py:130`), `StepExecutor.run_step(run: Any, objective: Any)`
  (`executor.py:426`) e `TrainingSkills.__init__(state: Any)`.
- **Linhas de banco sem tipo.** `Row` é `dict[str, Any]` (`db.py:41`) e cruza fronteiras. Os repositórios constroem os
  DTOs da API: `InstanceDTO` em `devices.manager`, `RunDetail` em `taskqueue.repository`.
- **Símbolo privado atravessando pacote:** `training/recorder.py:108` importa `taskqueue.executor._safe_target`.
- **Tabela sem dono:** `apps` não tem repositório e é escrita em `api.py:599, 623, 636`, `state.py:1612` e
  `vitrine.py:338`; `instances` tem 5 escritores, entre eles `executor.py:1144`; `steps` também é escrita por
  `social/approvals.py:173, 195`; `runs` também é escrita por `apps_overview.py:33`, um read model.

### 2.4 Máquinas de estado

**Formais** (tabela de transições e checagem):

- Step: `STEP_TRANSITIONS` (`taskqueue/states.py:6`), checada por `check_transition:34` em
  `Repository.transition_step` (`taskqueue/repository.py:269`);
- Command: `COMMAND_TRANSITIONS` (`commands/states.py:10`, `:48`), checada em `CommandStore.transition`.

**Ausentes:**

| Entidade | Situação |
|---|---|
| Run | `set_run_status` (`repository.py:165`) aceita qualquer alvo; `recompute_run` (`:672`) reabre execução terminal (`:698-702`) sem tabela que o declare |
| Objective | `set_objective` (`:564`), 15 chamadas; `Scheduler._apply` (`scheduler.py:1044`) é a tabela implícita |
| Attempt, Action | sem tabela; `Scheduler._reconciliar` (`scheduler.py:1369`) grava fora da cerca de `finish_attempt` |
| Installation (`device_app_state`) | cerca de 25 escritores e quatro definições divergentes de "presente" |
| Session | dois vocabulários: `SessionStatus` (`models.py:395`) × `profile_accounts.session_status` (037) |
| Recipe | `superseded` nunca é escrito; `PUT /api/recipes/{id}` reativa sem conferir, e duas ativas podem coexistir |
| TrainingSession | `save` aceita sessão `discarded`; `propose` grava sem CAS |
| Device | `InstanceState` escrito em cerca de 35 sítios; o estado observado vive só em memória (`manager.py:296`) |

**Escritas que contornam a própria regra:** `revise_plan` grava `skipped` direto (`repository.py:619`); `driven_by` é
escrito cru e depois do fato (`executor.py:545, 553`); `social/approvals.py:150-195` escreve em `steps` e
`objectives`.

### 2.5 Onde o Instagram vaza para o núcleo

| Onde | O quê |
|---|---|
| `state.py:36-38`, `:259` | importa `InstagramAuthenticator`, `bloquear_por_desafio` e os extratores de tela; a composição cria `self.instagram` concreto |
| `state.py:624`, `:783`; `apps_overview.py:77` | `session_provider … != "instagram"` / `== "instagram"` decide a invalidação, a porta de sessão e o "login automático" |
| `state.py:72-83` | `_TIPO_DE_TEXTO` e `_LEITURA_DE_CONVERSA` mapeiam chaves de capability do Instagram |
| `taskqueue/executor.py:587` | `if package != self.cfg.file.instagram.package` antes de corrigir a sessão |
| `models.py:392, 495, 395` | regex de usuário do Instagram no perfil genérico; `InstagramProfileDTO`; `SessionStatus` do Instagram usado como genérico |
| `config.py:436-508` | `InstagramCfg`: tempos e pacote |
| `automation/hierarchy.py:32-50`; `taskqueue/proofs.py:25` | regex de desafio copiada da navegação do Instagram; variantes de arroba na prova local genérica |
| `social/capacidades.py:155`, `scheduler.py:676` e outros | SQL direto sobre `instagram_profiles`/`instagram_credentials` |

O registro `planning/catalog` (`AppCapabilities.session_provider`, `catalog/__init__.py:27`) já é o ponto de extensão
certo. Mas ele guarda só um **nome**, e o núcleo resolve o nome comparando com `"instagram"`.

**Depois da K1** (merge `f06e34a` e correção `3fbe9df`; [ADR-039](../decisoes.md#adr-039--manifesto-de-app-e-registro-de-sessionprovider),
[apps](../dominios/apps-e-loja.md#manifesto-de-app-fase-k1),
[perfis](../dominios/perfis-e-instagram.md#sessionprovider-e-o-registro-por-pacote-fase-k1)). A tabela acima é do
commit base e fica como está. O que saiu:

- as seis comparações que decidiam pelo Instagram (a porta de sessão, a invalidação e o "login automático" em
  `state.py`, `apps_overview.py` e `social/service.py`, e a correção de sessão em `taskqueue/executor.py`): agora
  perguntam ao registro (`session_provider_of(...) is not None`, `SessionProviders.for_package`/`has`). O detector
  de `01d68b5`, rodado contra `578fe36`, acha as seis; na árvore da K1, nenhuma;
- os imports do autenticador e dos extratores em `state.py`, e `self.instagram` concreto: o provedor vem de
  `self.sessoes` (`SessionProviders`), e `AppState.instagram` é propriedade de compatibilidade (o mesmo objeto);
- `_TIPO_DE_TEXTO` e `_LEITURA_DE_CONVERSA`: foram para o manifesto (`AppDefinition.text_kinds` e
  `conversation_reads`, preenchidos por `integrations/instagram/manifesto.py`);
- `bloquear_por_desafio` e `emit_needs_person_change`: foram para `modules/identity/application/session_rules.py`;
- a comparação com `"instagram"` e o `s.instagram` direto nos recursos da H
  (`modules/execution/infrastructure/providers.py` e `command_bus.py`), que a H2 trouxe em paralelo à K1: saíram na
  correção `3fbe9df`.

O que ficou, e por quê (nada disto é comparação; `backend/tests/test_apps_fora_do_nucleo.py` trava as comparações, com
a lista de exceções vazia e `app/integrations/**`, `app/planning/catalog/**` e `app/config.py` fora do escopo):

- `package_of_provider("instagram")` em `state.py` e `social/service.py` (três vezes): o perfil é a conta de **um**
  app, e esta é a pergunta "de que pacote é a conta do perfil", feita ao registro em vez de escrita como literal. Muda
  junto com a sessão por (perfil, app), proposta;
- os caminhos `/api/instagram/profiles/*` e os nomes de tabela (`instagram_profiles`, `instagram_sessions`,
  `instagram_credentials`): contrato e esquema. Renomear é migração e versão de contrato, fora da K1;
- `config.py::InstagramCfg`: configuração do app, por instalação;
- a regex de desafio (`automation/hierarchy.py::_DESAFIO`): genérica de propósito, vale para qualquer app, e
  `test_sensitive_input` confere que ela concorda com `integrations/instagram/navigation.SIGNALS`. As variantes de
  arroba da prova local (`taskqueue/proofs.py`) também ficam: são regra de texto, não decisão por app;
- a linha de `models.py` (regex de usuário, `InstagramProfileDTO`, `SessionStatus`): não reconferida depois da K2;
  continua como na tabela.

### 2.6 Treino, fluxos, receitas e capabilities

- **Flow** (tabela `flows`, 005): plano congelado por comando.
  - `match_key` é UNIQUE e não há versão.
  - O casamento é por regex de template (`FlowStore.match`, `taskqueue/flows.py:132`), e o primeiro por `uses` vence.
  - O `id` é um slug que volta com outro conteúdo depois de um `DELETE` (`api.py:548-551`).
- **Recipe** (tabela `recipes`, 010): a chave é (pacote, versão, assinatura, variante, `step_hash`).
  - `step_template_hash` (`taskqueue/recipes.py:104`) usa `template_key or key`, o efeito, a pós-condição e as
    guardas. A identidade é o **texto** da etapa.
  - O vínculo receita↔etapa não tem coluna, só `steps.driven_by`.
- **Treino:**
  - gravação por `TrainingRecorder` (`training/recorder.py:42`);
  - generalização paga por `TrainingSkills.propose` (`training/skills.py:60`), com o schema `_TrainOut`
    (`planning/training.py:84`), que **não tem `depends_on`**;
  - salvar produz Flow + Recipes (`skills.py:84`), sem atomicidade.
- **Capability** (`planning/capabilities.py:34`) é contrato, não executor. O catálogo do Instagram tem 23;
  `reconciliation` e `collect` não têm consumidor. A ordem das estratégias é fixa no executor: receita, depois IA
  (`executor.py:426-459`, `767-805`).
- **Defeito da prova local de `OPEN_THREAD`.** A capability declara `selector:text=={username}`
  (`catalog/instagram.py:99`), e o comentário (`:97-98`) promete "junto de um campo de escrita". Mas
  `local_proof_holds` (`taskqueue/proofs.py:37-61`) não confere campo editável. Uma linha da caixa de entrada com o
  usuário passa como conversa aberta.
- `ai.recipes` (`"off"`) e `ai.flows` (`False`) vêm desligados por padrão no código (`config.py:401-402`). O valor da
  instalação não foi lido.

### 2.7 Worker

- **Fecho do agente:** 25 módulos `app.*` carregados (`sys.modules`), 28 pelo fecho AST com os imports tardios. O
  instalador copia pastas inteiras (`scripts/worker-install.ps1:60-61`, `scripts/worker-install.sh:51-52`): 45
  arquivos `.py` para 25 usados, e 13 dos copiados nem importam.
- **Bomba latente.** `devices/adb.py:144` (`connectivity_probe`) → `devices/conectividade.py:21` → `app.models`, e
  `models.py` não é copiado. Hoje só o `DeviceManager` chama a sonda. O CI `worker-agent-smoke` importa com o checkout
  inteiro e não pega o problema.
- **Instrução errada:** `scripts/deploy.ps1:186` manda copiar só `backend/app/worker/`. Com `contracts/`, isso daria
  `ImportError` no agente.
- **Contrato:** `workers/protocol.py` não importa nada do app. `PROTOCOL_VERSION = 1` (`:31`); esquema medido
  `18285a7c65c51551`. `Dispatch` e `Limits` são validados sem `try` no laço do agente (`worker/agent.py:355, 366`),
  então uma restrição de validação também é contrato. `EnvioDeMidia` é `extra="forbid"` (`:376`). A cerca virou
  obrigatória sem subir `PROTOCOL_MIN`.
- `worker/settings.py` carrega o `Config` inteiro do central para usar quatro campos.

### 2.8 Execução (o que o runtime novo herda)

Hierarquia `runs → objectives → steps → attempts → actions`, mais `evidence` e `ai_calls`. O plano (`Plan`/`PlanStep`,
`models.py:170, 136`) é gravado em `runs.plan` e materializado por `Repository.materialize` (`repository.py:193`).
`_insert_steps` (`:230`) calcula `template_hash` antes de resolver as variáveis (`:235`).

O sucesso da etapa é gravado pelo executor. Os demais desfechos são gravados por `Scheduler._apply`
(`scheduler.py:1044`). A §14 lista as proteções.

### 2.9 Testes e ferramentas

- **1.612 testes** coletados em 121 arquivos; cerca de 635 funções sobem `AppState` (heurística).
- **CI**, run `36297199520`: SQLite em 859,62 s (folga de ≈5:21 no `timeout-minutes: 20`); PostgreSQL em 1.101,08 s
  (folga de ≈5:49).
- **Nenhum verificador de tipos no venv.** O `backend/.venv` do worktree é uma junção para o venv de produção, onde o
  `scripts/deploy.ps1:139-141` instala o `requirements.txt`.
- **Precedentes a copiar:** `tests/test_cobertura_de_rotas.py` (AST com lista de exceções e regra de exceção órfã,
  `:133`) e `tests/test_contrato_de_worker.py` (a mesma bateria contra dois hospedeiros).
- **Dublês.** O `Harness` entrega sempre `FakeQaDevice` (`tests/conftest.py:204`, `tests/fake_device.py:50`).
  `FakeInstagram` (`tests/fake_instagram.py:47`) não implementa 5 métodos do `DeviceIO` (`automation/driver.py:31`) e
  não tem telas de caixa de entrada nem de conversa.

---

## 3. TARGET STATE

A árvore abaixo é o **alvo**. Pacotes legados convivem com ela até a fase K, e o que se move deixa um shim no lugar
antigo.

```
backend/app/
  shared/                  # kernel sem dependência interna (proposto): relógio, ids, redação, erros de domínio base
                           # hoje: util.py, security/redaction.py, version.py, metricas.py
  contracts/               # só stdlib + pydantic; nunca importa app.* fora de contracts
    worker/                # fase B: protocol.py (movido literal), verbos.py, vocabulario.py
    skills/                # fase E: v1alpha1.py — o schema Pydantic da DSL (contrato com o painel e com o LLM)
  modules/
    fleet/                 # aparelho, worker, comando, reparo, capacidade de hospedagem
    applications/          # app, release, instalação, loja, convergência (ADR-026)
    identity/              # perfil, persona, conta, sessão, credencial (ref), memória, efeitos sociais
    capabilities/          # CapabilityDefinition, registro por app, CapabilityProvider, prova local, política
    skills/                # SkillDefinition/Version, DSL→IR, compilador, ensino, resolução de intenção
    execution/             # run, objetivo, etapa, tentativa, estratégia, PLAN, trilha
      <cada contexto>/
        domain/            # entidades, VOs, máquinas de estado, regras puras (sem I/O)
        application/       # casos de uso; ports.py com os Protocols que o contexto CONSOME
        infrastructure/    # repositórios SQL, adaptadores de código legado deste contexto
        presentation/      # router FastAPI do contexto (fase K); DTOs de requisição/resposta
  adapters/                # adaptadores técnicos compartilhados entre contextos
    android/               # adb, sdk, avd, emulator, prontidao, perfis, sonda_rede (hoje em devices/)
    appium/                # appium_driver, appium_server (hoje em automation/)
    ai/                    # provider, anthropic/openai/simulated, routing, prompts, parsing, costs (hoje em planning/)
    storage/               # disco e S3 (hoje storage.py)
  bootstrap/               # composição: o que hoje é AppState.__init__, start e stop; liga portas a adaptadores
  api.py, main.py          # continuam; api.py encolhe até virar agregador de routers (fase K)
```

**Regras do alvo:**

- **Portas pertencem a quem consome.** Elas ficam em `modules/<ctx>/application/ports.py`. Quem implementa **não
  importa** o `Protocol`, porque a tipagem estrutural dispensa isso, e o `bootstrap` liga as partes. É o que mantém o
  grafo de contextos um DAG (§9, D5).
- **IA é adaptador** (`adapters/ai`). Os contextos falam com ela por portas: `Planner`, `ActorModel`, `Verifier` e
  `SkillGeneralizer` (§7). Todo acesso continua passando pelo ponto único `StepExecutor._ai` (`executor.py:264`) até a
  fase K.
- **Transversais** são segurança, cofre, storage, eventos e relógio. O kernel expõe o **tipo**; o `bootstrap` injeta a
  implementação. `EventBus` (`events.py`), `SecretStore` (`security/secret_store.py:209`) e `SensitiveInputChannel`
  (`security/sensitive_input.py:56`) continuam sendo o que são.
- **Código novo nasce só** em `contracts/`, `modules/`, `adapters/`, `shared/` e `bootstrap/`, com mypy estrito
  (§9). O legado só se move com shim, e primeiro sem editar.
- **O que não muda:** um processo por máquina, os papéis `ROLE` (`all`/`api`/`scheduler`), `db.py`, o `EventBus`, o fio
  do worker e as tabelas existentes.

---

## 4. BOUNDARIES

| Contexto | Mora aqui | Símbolos atuais | Tabelas que passa a escrever |
|---|---|---|---|
| **shared** (kernel) | relógio, ids, redação, erros base, `UiTree` como modelo de tela | `util.py`, `security/redaction.py`, `version.py`, `automation/hierarchy.py` (`UiTree`) | — |
| **contracts** | fio do worker; DSL de skill | `workers/protocol.py`, `devices/verbs.py:16-63` (vocabulário); DSL (nova) | — |
| **platform** (transversal) | banco, eventos, storage, config, saúde, retenção, sessão do painel | `db.py`, `events.py`, `storage.py`, `config.py`, `SettingsStore` (`state.py:106`), `AppState.health` e laços (`state.py:1691-2198`) | `settings`, `measurements`, `secrets`, `panel_sessions`, `events` |
| **fleet** | aparelho (desejado × observado), worker, comando com cerca e outbox, reparo, placement | `devices/*` (menos installer, compatibilidade, diagnostics), `commands/*`, `workers/{registry,local,captura,portao}`, despacho (`api.py:1640-2417`, `3393-3544`), `Scheduler` placement (`:601-862`) | `instances`, `commands`, `command_outbox`, `workers`, `worker_enrollments`, `worker_limits`, `proxy_profiles`, `device_proxy_state` |
| **applications** | app, release, instalação, loja, convergência | `releases/*`, `vitrine.py`, `devices/installer.py`, cluster Applications do `AppState` (`state.py:450-1260`), `api.py:577-640` | `apps`, `app_releases`, `app_release_files`, `app_release_validations`, `app_trusted_signers`, `device_app_state` |
| **identity** | perfil, persona, conta por app, sessão, referência de credencial, política, memória, interações | `social/*` (menos `approvals`, `capacidades`), `integrations/instagram/authentication.py`, cluster Identity do `AppState` (`state.py:609-845`) | `instagram_profiles`, `profile_accounts`, `account_credentials`, `instagram_credentials`, `instagram_sessions`, `device_profile_bindings`, `authentication_attempts`, `personas`, `memory_items`, `relationship_summaries`, `thread_summaries`, `social_interactions`, `policy_groups` |
| **capabilities** | o que um app sabe fazer, com contrato, política, prova local e provider | `planning/capabilities.py`, `planning/catalog/*`, `taskqueue/proofs.py`, `devices/compatibilidade.py`, `integrations/instagram/{navigation,verification,reconciliation}.py`, `social/policy.py` | só leitura (catálogo em código) |
| **skills** | skill versionada, DSL, compilador, ensino, resolução de intenção, receitas como dados de estratégia | `taskqueue/{flows,recipes,aproveitamento}.py`, `training/*`, `planning/training.py`, desbravador do `Scheduler` (`:446-600`, `:966`) | `skill_*` e `teaching_*` (042–044); legado: `flows`, `flow_scope`, `flow_required_apps`, `recipes`, `training_sessions`, `training_inputs` |
| **execution** | run, objetivo, etapa, tentativa, ação, efeito, evidência, estratégias, PLAN, trilha | `taskqueue/{repository,service,scheduler,executor,foreach,states,ai_slots}.py`, `social/approvals.py`, `automation/tools.py`, `Limiter` (`manager.py:189`), portões do `AppState` (`state.py:1345-1606`) | `runs`, `objectives`, `steps`, `attempts`, `actions`, `evidence`, `ai_calls`, `ai_slots`, `pending_approvals`, `plan_versions`, `run_secrets` |
| **presentation** | rotas HTTP/WS por contexto | `api.py`, `main.py` e os 41 corpos `*Body`/`*Create`/`*Patch` de `models.py` | — |
| **bootstrap** | composição e ciclo de vida | `AppState.__init__:161`, `start:1637`, `stop:1741` | — |

**Casos de fronteira:**

- `social/capacidades.py` é um read model de cobertura e custo que cruza skills e identity. Ele fica em skills como
  consulta, sem escrita.
- `Capability.interaction_type` liga uma capability a um efeito social. O efeito é registrado em identity por uma
  porta `EffectsLedger` consumida por execution (§7).
- Receita é dado de estratégia. Pertence a skills, que a guarda, e é executada por execution, pela
  `RecipeExecutionStrategy`.
- Política: o `PolicyEngine` (`social/policy.py:94`) é serviço de domínio de capabilities, porque avalia uma
  capability contra limites; os **dados** de política (política própria do perfil, `policy_groups`) são de identity
  e chegam por porta de leitura. `Approval` é de execution: a tabela, o CAS e o `_approval_gate` já moram lá.

---

## 5. ENTITIES e VALUE OBJECTS

**Entidades** (identidade e ciclo de vida). As com `*` são novas.

| Contexto | Entidade | Hoje |
|---|---|---|
| fleet | `Device` (identidade, desejado, observado, capacidades), `Worker`, `DeviceCommand` | `DeviceRuntime` (`manager.py:259`), linha `workers`, `CommandStore` |
| applications | `App`, `Release`, `Installation` (aparelho × pacote) | tabela `apps` sem repositório; `ReleaseRepository`; `device_app_state` |
| identity | `Profile`, `Persona`, `Account`, `Session`, `Interaction` | `instagram_profiles`, `personas`, `profile_accounts`, `instagram_sessions`, `social_interactions` |
| capabilities | nenhuma: o catálogo é valor (`CapabilityDefinition` é VO, abaixo) | `planning/catalog`, em código |
| skills | `SkillDefinition`*, `SkillVersion`*, `TeachingSession`*, `TeachingCandidate`*, `ValidationCase`*, `Recipe`, `Flow` (legado) | `flows`, `recipes`, `training_sessions` |
| execution | `Run`, `Objective`, `StepExecution`, `Attempt`, `Action`, `Effect`*, `Evidence`, `Approval` | tabelas 001+; `pending_approvals` + `ApprovalStore` (`social/approvals.py:61`) |

`Effect` unifica o que hoje está em três lugares: `actions.side_effect/effect_possible`, `objectives.effects` e o
vínculo com `social_interactions`.

**Value objects:**

| VO | Forma | Onde está solto hoje |
|---|---|---|
| `RunId`, `ObjectiveId`, `StepId`, `AttemptId` | `r-…`, `<run>:<inst>`, `<run>:<inst>:v<n>:<key>`, `<step>:a<n>` | `util.py:31`, `repository.py:198, 255, 378` |
| `PackageName`, `AppVersion`, `SignatureSha256`, `UiVariant` | regex; `nome(código)`; hex; `<locale>/<densidade>` | `models.py:1278`; `devices/adb.py:436`; `recipes.app_signature`; `manager.py:3318` |
| `RecipeKey` | (pacote, versão, assinatura, variante, `step_hash`) | `recipes.py:398-446`, `scheduler.py:491-511` |
| `Selector`, `LocalProof` | `{rid,text,desc}` + ranking; `sent_text` \| `selector:` \| `selector_band:` | `recipes.py:35`; `capabilities.py:111` |
| `Fence`, `Lease`, `Deadline` | int; (dono, expira em); ISO | `commands.fence`; `steps.claimed_by`, `ai_slots`, `device_app_state.claimed_by` |
| `Handle`, `SecretRef` | usuário validado pelo manifesto do app; nome no cofre | `models.py:392`, `proofs.py:25`; três tabelas com `secret_ref` |
| `DeliveryLevel`, `Postcondition` | já são VO | `models.py:115, 128` |
| `SkillRef`* | `<skill_id>@<n>`; legado `flow:<flows.id>@1` | — |
| `ContentHash`* | sha256 do JSON **canônico** (`sort_keys`, separadores fixos, `ensure_ascii=False`); nunca `db.dumps`, que não ordena (`db.py:545`) | — |
| `NodeId`* | `^[a-z][a-z0-9_]{1,40}$`, o mesmo padrão de `PlanStep.key` (`models.py:137`) | — |
| `ParameterSpec`*, `Expression`* | §10 e §12 | `Plan.parameters: dict[str, str]` (`models.py:178`) |
| `CapabilityRef`* | (app, chave, versão do contrato) | `steps.capability` (010) |
| `StrategyKind`* | `deterministic` \| `recipe` \| `app_provider` \| `ui_generic` \| `ai_actor` \| `human` | `steps.driven_by`, `actions.source` |
| `ResourceRef`* | (tipo, alvo) — §11 | — |

---

## 6. AGGREGATE ROOTS e invariantes

| Raiz | Invariantes | Onde o código já garante | Lacuna |
|---|---|---|---|
| `DeviceCommand` | transição só pela tabela; cerca monotônica por aparelho; aceitar ⇔ linha `pending` no outbox; `uncertain` sai só por pessoa ou por sonda que prove sucesso | `commands/states.py:10`; `CommandStore.create` (`commands/store.py:65-90`, trava em `:48`); `commands/outbox.py`; `commands/reconciler.py:85-103` | — (é o agregado mais bem formado) |
| `Device` | um objetivo ativo por aparelho; o dono em memória serializa o driver | `ai_begin`/`ai_end`; índice `idx_steps_um_ativo_por_aparelho` (018) | observado só em memória; sem `DEVICE_TRANSITIONS` |
| `Release` | promover exige canário com prova de `install` e `launch`; assinatura divergente de signatário aprovado vira `invalid` | `ReleaseService.promote` (`releases/service.py:802-822`); `_signature_verdict` (`:225`) | canal sem tabela de transição |
| `Installation` | instalação interrompida nunca é repetida às cegas | `InstalacaoIncerta` (`scheduler.py:324-330`); `releases/service.py:650-660` | cerca de 25 escritores; quatro conjuntos de "presente" |
| `Profile` | um vínculo ativo por perfil e por aparelho; perfil fora de `active` não recebe tarefa; desafio → `blocked` sem reativação automática (ADR-029) | índices 008:72-73; `_session_gate` (`state.py:769`); `bloquear_por_desafio` (`integrations/instagram/authentication.py:59-92`) | dois vocabulários de sessão |
| `SocialLedger` | só interação confirmada vira memória; segredo nunca é memorizado | `MemoryStore.learn_from` (`social/memory.py:170`), `remember` (`:109`) | `confirm_interaction` não é idempotente (`social/service.py:347`) |
| `Approval` | decidida uma vez só | CAS em `ApprovalStore.decide` (`social/approvals.py:123-130`) | — |
| `Objective` (raiz) + `Run` (leve) | uma etapa ativa por aparelho; posse antes de escrever; efeito disparado sem desfecho nunca vira sucesso nem reenvio; plano válido | 018 + CAS em `claim_step` (`repository.py:353`); cerca em `transition_step`/`finish_attempt`; `commit_state` (`:450`); validador do `Plan` (`models.py:184-218`) | Run e Objective sem tabela; o sucesso é gravado em dois lugares |
| `Recipe` | uma ativa por chave | `RecipeStore.save` (`recipes.py:431-446`) | a reativação manual não confere |
| `SkillVersion`* | conteúdo congelado fora de `draft`; uma publicada por skill; um comando publicado por vez; transições registradas com `decided_by` | **proposto**: repositório (camada obrigatória, confere `content_hash` na leitura); índices parciais `ux_skill_versions_publicada` e `ux_skill_versions_comando` (042); gatilho 046 como segunda camada | a unicidade de comando entre `skill_versions` e `flows` não cabe no banco: resolvida pela desativação do fluxo na mesma transação (§15) |
| `TeachingSession`* | uma gravação pertence a no máximo um ensino; candidata ≠ versão; texto da pessoa redigido antes de gravar | **proposto**: `ux_teaching_demo_gravacao` (044); redação como `recorder.py:116-120` | — |

**Decisão de modelagem:** `Objective` é a raiz de agregado de execução, porque é a unidade que o scheduler despacha.
`Run` é um agregado leve de coordenação, com status derivado por regra explícita (`recompute_run`), que continua sendo a
única função que o deriva.

---

## 7. PORTS

Todas as portas abaixo são **propostas**. Assinaturas em Python, com os tipos novos da §5, da §10 e da §12.

```python
# modules/skills/application/ports.py
class SkillRegistry(Protocol):
    """Uma porta, dois backends (§19, decisão 1). Resolve na ordem: skill publicada → fluxo ativo → nada."""
    def resolve(self, command: str, profile_ids: list[str | None] | None) -> "ResolvedSkill | None": ...
    def get(self, ref: SkillRef) -> "SkillVersion": ...
    def published(self, skill_id: str) -> "SkillVersion | None": ...
    def list(self, *, app_id: str | None = None, state: str | None = None) -> list["SkillSummary"]: ...

class SkillRepository(Protocol):          # só o backend SQL; o legado é só leitura
    def create_draft(self, skill_id: str, doc: "SkillDocument", *, source: "Provenance") -> "SkillVersion": ...
    def update_draft(self, ref: SkillRef, doc: "SkillDocument") -> "SkillVersion": ...     # recusa fora de draft
    def transition(self, ref: SkillRef, to: "SkillState", *, by: str, reason: str) -> "SkillVersion": ...

class CapabilityCatalogPort(Protocol):
    def definition(self, app_id: str, key: str) -> "CapabilityDefinition | None": ...
    def offered(self, app_id: str) -> list["CapabilityDefinition"]: ...

class SkillGeneralizer(Protocol):         # IA: hoje RoutingProvider.generalize (planning/routing.py:242), fora do Protocol AIProvider
    async def generalize(self, req: "TeachingRequest") -> tuple["CandidateDraft", "Usage"]: ...

# modules/execution/application/ports.py
class PlanCompiler(Protocol):
    def compile(self, version: "SkillVersion", params: dict[str, str]) -> "CompileResult": ...   # sem I/O, sem IA

class ExecutionStrategy(Protocol):
    kind: StrategyKind
    def applicable(self, node: "StepView", ctx: "StrategyContext") -> bool: ...
    async def run(self, node: "StepView", ctx: "StrategyContext") -> "StrategyResult": ...

class CapabilityProvider(Protocol):
    def supports(self, cap: CapabilityRef, app: "AppContextRef") -> bool: ...
    async def observe(self, ctx: "StrategyContext") -> "Observation": ...
    async def execute(self, node: "StepView", ctx: "StrategyContext") -> "StrategyResult": ...
    async def verify(self, node: "StepView", obs: "Observation") -> "VerifyOutcome": ...    # proved | not_proved | unknown
    async def reconcile(self, node: "StepView", ctx: "StrategyContext") -> "VerifyOutcome": ...

class ResourceProvider(Protocol):         # §11
    kind: str
    def read_current_state(self, ref: ResourceRef, target: "Target") -> "ObservedState": ...   # pura, sem efeito
    def diff(self, desired: "ResourceSpec", observed: "ObservedState") -> "Drift": ...
    def plan(self, drift: "Drift") -> list["ResourceAction"]: ...
    async def apply(self, action: "ResourceAction") -> "CommandRef": ...                       # só por commands
    def verify(self, ref: ResourceRef, target: "Target") -> "VerifyOutcome": ...
    async def reconcile(self, ref: ResourceRef, target: "Target") -> "VerifyOutcome": ...

class PolicyGate(Protocol):               # tipa scheduler.py:130
    async def __call__(self, obj: "ObjectiveRow", step: "StepRow", run: "RunRow") -> "Verdict | None": ...

class EffectsLedger(Protocol):            # identity implementa; tipa executor.py:154
    def open_effect(self, *, step_id: str, profile_id: str | None, interaction_type: str, target: str | None) -> str: ...
    def settle_effect(self, effect_id: str, outcome: "EffectOutcome") -> None: ...

class AiGateway(Protocol):                # o ponto único StepExecutor._ai (executor.py:264), tipado
    async def call(self, role: str, *, run_id: str, objective_id: str | None, attempt_id: str | None,
                   fn: Callable[[], Awaitable[tuple[T, "Usage"]]]) -> T: ...

class CommandBus(Protocol):               # verbos de aparelho: sempre por commands + outbox (regra R10)
                                          # feito na H2: mora em shared/commands.py (fleet/applications/identity
                                          # não podem importar execution) e é reexportado por execution/application/ports.py
    def request(self, instance_id: str, verb: str, params: dict[str, str], *, requested_by: str,
                run_ref: "RunRef | None") -> "CommandRef": ...

# modules/identity/application/ports.py
class SessionProvider(Protocol):          # tira as comparações com "instagram" (§2.5)
    package: str
    def ensure_session(self, device: "DeviceRef", profile_id: str) -> Awaitable["SessionState"]: ...
    def classify(self, tree: "UiTree") -> "ScreenClass": ...

# shared (tipos transversais)
class Clock(Protocol):
    def now(self) -> datetime: ...
class EventPublisher(Protocol):
    def publish(self, event: "DomainEvent") -> None: ...
class SecretVault(Protocol):
    def reference(self, run_id: str, name: str) -> SecretRef | None: ...   # nunca devolve o valor
class EvidenceStore(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str) -> str: ...
```

**Portas que já existem e só ganham tipo.** Elas vêm antes de mover qualquer coisa:

- do `Scheduler` (`scheduler.py:105-142`): `session_gate`, `app_resolver`, `app_preflight`, `rollout_source`,
  `expirar_aprovacoes_do_objetivo`, `policy_gate`, `on_run_settled`, `on_items_collected`, `worker_gate` e
  `worker_capacity`;
- do `StepExecutor` (`executor.py:154-168`): `social`, `approvals`, `secrets` e `sensitive_input`;
- os 11 `on_*` do `DeviceManager` (`manager.py:470-510`).

---

## 8. ADAPTERS

Todos os adaptadores são **propostos**, mas cada um embrulha código que **já existe**. A regra é embrulhar primeiro e
mover depois.

| Porta | Adaptador | Código embrulhado | Fase |
|---|---|---|---|
| `SkillRegistry` | `CompositeSkillRegistry` = `SqlSkillRepository` + `LegacyFlowAdapter` | tabelas 042–046; `FlowStore.match`/`_extract`/`_no_escopo` (`flows.py:132, 157, 108`), só leitura | D |
| `CapabilityCatalogPort` | `CatalogCapabilityRegistry` | `planning/catalog` (`get`, `capabilities_of`), `Capability` → `CapabilityDefinition` | C |
| `PlanCompiler` | `SkillCompiler` (IR no domínio, *lowering* na infraestrutura) | `CapabilityCatalog.build_step` (`capabilities.py:152`), validador de `Plan` (`models.py:184`) | E |
| `ExecutionStrategy` | `RecipeExecutionStrategy` | `RecipeStore.find`, `Replayer` (`recipes.py:345, 394`), `_after_step` (`executor.py:517`) | G |
| | `AiActorStrategy` | laço de decisão de `_run_step` (`executor.py:795-861`), `actor_params`, `side_effect_tier` | G |
| | `DeterministicStrategy` | capabilities `internal=True` resolvidas por código: `InstagramAuthenticator.ensure_session`, `verification.read_account` | H |
| | `HumanStrategy` | desfecho `waiting_user` + `resolve(confirm_done)` (`taskqueue/service.py:672`) | G (só registro) |
| `CapabilityProvider` | `CatalogCapabilityProvider` | `execute` delega à cadeia de estratégias do nó; `verify` = `_deterministic` + `local_proof_holds` + `_verify` (`executor.py:1163, 1185`); `reconcile` = `commit_state` + `failure_marks` | G |
| `ResourceProvider` | `DeviceStateProvider`, `AppInstallationProvider`, `AccountSessionProvider` | §11 | H |
| `PolicyGate` | `AppState._policy_gate` como está | `state.py:1345` (depois move para `modules/execution`) | K |
| `EffectsLedger` | `SocialService.open_effect/settle_effect` | `social/service.py:516, 549` | K |
| `AiGateway` | `StepExecutor._ai` | `executor.py:264-348` | K |
| `SkillGeneralizer` | `RoutingProvider.generalize` | `planning/routing.py:242-244` e os provedores | F |
| `CommandBus` | despacho extraído na fase A | `commands/despacho.py` (hoje `api.py:1697`, `2282`) | A (move), H (porta) |
| `SessionProvider` | `InstagramAuthenticator` | `integrations/instagram/authentication.py` | K |
| `EventPublisher` | projetor sobre o `EventBus` | `events.py:47`; o formato do WebSocket não muda | K |
| `SecretVault` | `SecretStore` + `SensitiveInputChannel` | `security/secret_store.py:209`, `security/sensitive_input.py:56` | K |
| `EvidenceStore` | `DiskStorage`/`S3Storage` | `storage.py` | K |
| `Clock` | `util.now` | `util.py` | A |

---

## 9. DEPENDENCY RULES

As regras são verificadas por **um teste por AST, sem dependência nova**: `backend/tests/test_arquitetura.py`. Ele
nasce do protótipo do relatório 8, que rodou em 1,05 s no commit base e reprovou 8 de 8 mutações, e incorpora as
exceções nominais do relatório 1. O teste não importa o `app`: lê os 123 arquivos e custa cerca de 1 s.

**Quais imports contam:**

- para direção e pureza: todos (topo, dentro de função e sob `TYPE_CHECKING`);
- para ciclos e para o fecho do agente: só os que executam.

**Chave das exceções:** (módulo de origem, função que contém o import, módulo de destino), nunca o número da linha.

**Disciplina de catraca:** subir reprova. Descer **também** reprova, com "baixe a base", como a regra de exceção órfã
de `tests/test_cobertura_de_rotas.py:133`. Cada ganho fica travado no mesmo commit.

| # | Regra | Hoje | Exceções nominais (só encolhem) |
|---|---|---|---|
| D1 | `app.contracts.**` importa só stdlib, `pydantic` e o próprio `contracts` | vale (nasce vazio) | — |
| D2 | `modules.*.domain` vê só stdlib, `pydantic`, `contracts`, `shared` e domínios; nada de `fastapi`, `starlette`, `sqlite3`, `psycopg`, `appium`, `selenium`, `anthropic`, `openai`, `httpx`, `websockets`, `nats`, `boto3`, `PIL`, `psutil`, `subprocess`, `socket` ou `asyncio`, nem sob `TYPE_CHECKING` | vale | — |
| D3 | `modules.*.application` = D2 + `application` (com `ports`) | vale | — |
| D4 | infraestrutura (bibliotecas da lista D2) só onde já mora: `fastapi` em `api`/`main`; `sqlite3`/`psycopg` em `db`; `anthropic`/`httpx` nos provedores; `appium`/`selenium` em `automation.appium_driver`; `nats` em `commands.transport`; `boto3` em `storage`; `PIL` em `devices.codificacao`, `devices.manager` e `taskqueue.executor`. Fora disso, só em `modules.*.infrastructure`, `modules.*.presentation` e `adapters.**` | vale | depois da fase A: `fastapi` em `app.commands.despacho` (o `err()` foi junto; sai na fase K) |
| D5 | os contextos `app.modules.<a>` formam um DAG: `{shared, contracts} ← {fleet, applications, identity} ← capabilities ← skills ← execution` (`shared` e `contracts` são raízes independentes) | vale (vazio) | — |
| D6 | ninguém importa `app.api` além de `app.main` | vale com exceção | os 8 de §2.2 (somem na fase A) |
| D7 | só `api` e `main` importam `app.state` em execução (`TYPE_CHECKING` tolerado) | vale | — |
| D8 | domínio legado (`devices`, `releases`, `social`, `taskqueue`, `planning`, `commands`, `workers`, `training`, `integrations`, `automation`, `security`) não importa `api`, `state` nem `main` no topo | vale | — |
| D9 | o fecho do agente (`app.worker*`, com imports tardios) cabe no manifesto do instalador e não toca `db`, `models`, `api`, `state`, `events`, `automation`, `taskqueue`, `planning`, `social`, `commands`, `workers.{registry,local,captura}` nem `devices.manager`; terceiros ⊆ `worker-requirements.txt` | vale com exceção | `devices.adb.Adb.connectivity_probe → devices.conectividade` (sai na fase B) |
| D10 | nenhum ciclo de import de topo; os ciclos em execução só encolhem; módulo novo em ciclo reprova sempre | 0 de topo; 2 × 8 em execução | `CICLOS_LEGADOS`, baixado a cada medição |
| D11 | `import_module`/`__import__` só em `planning/catalog/__init__.py`; desde a K1, em `modules/applications/infrastructure/registry.py::_importar` (carrega os manifestos embutidos) | vale, **declarada e não conferida** (nota abaixo) | a própria |
| D12 | imports internos dentro de função, por pacote, só descem; código novo tem zero | 78 | base por pacote (§2.3) |
| D13 | `Any` em anotação, por pacote, só desce; código novo tem zero | 894 | base por pacote (§2.3) |
| D14 | nenhum import de símbolo privado (`_x`) de outro pacote | vale com exceção | `training.recorder.TrainingRecorder.record → taskqueue.executor._safe_target` |
| D15 | `modules.skills.**` e `contracts.skills.**` não chamam `eval`, `exec`, `compile`, `__import__`, `importlib`, `pickle`, `marshal` nem `types.FunctionType`, e não importam módulo de IA (`adapters.ai`, `planning.{provider,routing,prompts,parsing,training,*_provider}`): o compilador não chama IA. Emenda de 27/09: a baixa (`infrastructure/lowering.py`) pode importar `planning.capabilities`, porque usa `build_step` para o `PlanStep` sair idêntico ao do planejador | vale (`tests/test_compilador_de_skills.py::test_compilador_nao_avalia_nem_carrega_codigo_por_ast`) | — |
| D16 | teto de linhas dos god modules (§2.1) | vale | só desce, na fronteira de fase (ver abaixo) |

**Nota D11 (K1, `88087d9`).** O único `importlib.import_module` do backend acompanhou o registro de apps: saiu de
`planning/catalog/__init__.py` (hoje shim) para `modules/applications/infrastructure/registry.py::_importar`, com o
caminho relativo a `__package__`. Diferente das outras regras, a D11 **não tem conferência** em
`backend/tests/test_arquitetura.py`: ela aparece só na docstring, como "ponto cego declarado" (o AST não enxerga o que
um `import_module` carrega). O que ele carrega (hoje, `integrations/instagram/manifesto.py`) continua invisível à
contagem de ciclos, como a §2.2 já dizia do lugar antigo. Para código de skill, a D15 reprova `importlib` e
`__import__` por AST.

**D16 não é estrita nas duas direções.** Uma correção de bug muda linhas o tempo todo, e uma catraca estrita
conflitaria em todo ramo paralelo. O teto só reprova quem **sobe**; o coordenador baixa o teto na fronteira de cada
fase. Se uma correção precisar crescer um god module, o mesmo commit tira linhas equivalentes para fora dele ou sobe o
teto com uma justificativa escrita junto da entrada.

**Autoteste.** Uma mini-árvore em `tmp_path` com uma violação por regra prova que cada regra reprova. Para isso,
`modulos(raiz)` recebe a raiz como parâmetro, em vez do `lru_cache` global do protótipo. É o cuidado registrado em
`tests/test_cobertura_de_rotas.py:35-37` ("este teste passava sem olhar para nada").

**Tipos (mypy).** `backend/requirements-dev.in` → `requirements-dev.txt`, compilado com `-c requirements.txt`, mais
`backend/mypy.ini`, num job próprio de CI (`backend-tipos`, `timeout-minutes: 10`, fora do `backend-sqlite`, que já tem
pouca folga). É **bloqueante** em `app.contracts.*` e `app.modules.*`, com as bandeiras do `--strict` por extenso
(numa seção por módulo, `strict` é ignorado) e `disallow_any_explicit`, relaxado só em `*.infrastructure`. No legado
é **só medição** (`continue-on-error`). **Nunca** em `requirements.in`, porque o deploy instala no venv de produção.
Localmente: `uvx --with pydantic==<lock> --from "mypy==<lock>" mypy --python-executable .venv/Scripts/python.exe`.

---

## 10. SKILL MODEL

### 10.1 `SkillDefinition` (tabela `skill_definitions`, 042)

| Campo | Regra |
|---|---|
| `id` | slug estável `^[a-z][a-z0-9_.-]{2,63}$`, como `ig.abrir_conversa`. O prefixo `flow:` é reservado ao adaptador legado |
| `name`, `description` | texto |
| `app_id` | app principal, sem FK, como `flows.app_id` |
| `legacy_flow_id` | preenchido só quando um fluxo é convertido em skill (§15); índice único parcial |
| escopo (`skill_scope`) | perfis e grupos, com a mesma semântica de `flow_scope` (`flows.py:108-116`): sem linha vale para todos, e **todos** os perfis da execução precisam estar no escopo. Escopo é decisão de distribuição, não de conteúdo: muda sem versão nova |
| exclusão | a definição com qualquer versão não se apaga (FK `NO ACTION`). A regra é desligar, nunca apagar, ao contrário de `DELETE /api/flows/{id}` |

### 10.2 `SkillVersion` (tabela `skill_versions`, 042)

| Campo | Regra |
|---|---|
| `id` | `SkillRef` = `<skill_id>@<version>` |
| `version` | inteiro, crescente por skill |
| `state` | §10.3 |
| `schema_version` | `1` = `automation/v1alpha1`; `0` = plano legado (só no adaptador) |
| `content` | o documento DSL em JSON canônico |
| `content_hash` | sha256 do JSON canônico (§5); conferido na leitura, como `Database.divergencias` faz com migração |
| `command_template`, `match_key` | derivados de `content`; `match_key` usa a mesma normalização de `flows.match_key` (`_norm`, `flows.py:22-23`) |
| `parent_version` | de qual versão esta derivou |
| `source_kind`, `source_ref` | `legacy_flow` \| `teaching` \| `run` \| `manual` \| `import`, e a referência de origem |
| `provenance` | §10.8 |
| `state_at`, `state_by`, `state_detail` | última transição; o histórico completo fica em `skill_version_transitions`, com `decided_by` = nome da sessão do painel (padrão da 035) |

`skill_version_apps` é só o índice consultável ("que versões exigem o app X?"). Não tem FK para `apps`, de propósito:
apagar o app não pode sumir com a exigência de uma versão imutável.

### 10.3 Estados e transições

Estados: `draft`, `candidate`, `validated`, `published`, `deprecated` e `disabled`. O conteúdo **congela ao sair de
`draft`**.

| De → para | Quem | Condição |
|---|---|---|
| (nova) → `draft` | pessoa, ensino, importação ou conversão | — |
| `draft` → `draft` | pessoa | `update_draft`; só `draft` é editável e só `draft` se apaga |
| `draft` → `candidate` | pessoa ("submeter") | compilação sem erro `E_*` (§12.6). O `content_hash` é fixado aqui |
| `candidate` → `validated` | sistema | todo caso ativo na faixa da versão (043) tem o último resultado `passed`, e há pelo menos um caso. Caso `device` só passa com `proof=real` |
| `candidate` → `disabled` | pessoa | abandonar uma candidata congelada |
| `validated` → `published` | pessoa | `content_hash` intacto; `match_key` livre entre publicadas **e** entre fluxos ativos, senão a conversão acontece na mesma transação (§15). A publicada anterior da mesma skill vai para `deprecated` **na mesma transação** |
| `validated` → `disabled` | pessoa | — |
| `published` → `deprecated` | sistema (nova publicação) ou pessoa | — |
| `published` → `disabled` | pessoa | parada de emergência: o resolvedor deixa de casar na hora |
| `deprecated` → `published` | pessoa (rollback) | mesmas condições de publicar; os resultados de validação continuam valendo, porque o conteúdo não mudou |
| `deprecated` → `disabled` | pessoa | — |

- Não existe `candidate → draft` nem `validated → draft`. Para mudar o conteúdo, cria-se uma nova `draft` com
  `parent_version = n`.
- `disabled` é terminal.
- Uma execução em curso não é afetada por nenhuma transição: o plano dela já está congelado em `runs.plan`.

**O "ponteiro" da versão publicada** (decisão 3) é **lógico**. É a única linha com `state='published'` da skill,
garantida pelo índice parcial `ux_skill_versions_publicada`, e publicar ou reverter é mover esse ponteiro com duas
transições numa só transação. Não há coluna em `skill_definitions` porque ela criaria um ciclo de FK que
`tools/migrate_data.py:99-113` não resolve.

### 10.4 Parâmetros tipados

`ParameterSpec`: `name` (`^[a-z_][a-z0-9_]*$`, o mesmo `PLACEHOLDER` de `flows.py:19`, fora de `RESERVED`:
`instance_id`, `run_id`, `account_label`, `flows.py:18`); `type` (`string` \| `text` \| `integer` \| `boolean` \|
`enum` \| `handle` \| `url`); `required` (padrão `true`); `default`, `example`, `description`, `values` (para `enum`),
`pattern` e `max_length` (≤ 500, o teto de `_extract`).

**Segredo não é parâmetro.** Um parâmetro com cara de credencial reprova (`E_SECRET_PARAMETER`). Credencial entra só
pelo nome, em `requires.secrets`, e o valor vem de `RunCreate.credentials`/`consent_credentials` (ADR-025).

**Lowering.** O `ParameterExtractor` valida os tipos na RESOLVE. Depois disso, os valores viram texto em
`Plan.parameters` (`dict[str, str]`), e `${parameters.x}` vira `{x}`, que é o que `materialize` resolve
(`resolve_templates`, `repository.py:53`; `_aplicar`, `capabilities.py:230`). **O schema tipado mora só em
`skill_versions.content`.** `runs.plan` continua sendo o `Plan` de hoje.

### 10.5 Entradas e saídas

- **Entradas:** os parâmetros e o contexto da execução (aparelho, perfil vinculado, credenciais por nome).
- **Saídas:** na v1alpha1, só nó de coleta produz saída: `list[str]` de itens (`items_collected`, `StepResult.items`).
  Ela é consumida só por `foreach` e exposta em `spec.outputs`, que o relatório da execução mostra. Qualquer outra
  referência de saída é `E_OUTPUT_REF_UNSUPPORTED`. O único encanamento de saída que existe hoje é coleta → `for_each`
  → `{item}` (`foreach.py`), mais o `username` que `READ_MESSAGES` lê da etapa de que depende
  (`AppState._alvo_da_conversa`, `state.py:1432`).

### 10.6 Requisitos

`spec.requires`: `apps` (vira `Plan.required_apps` e `skill_version_apps`), `secrets` (nomes), `device`
(`api_level_min`, `play_store` e `abis`, conferidos por `Requisitos` × `Capacidades`, `devices/compatibilidade.py:27, 37, 70`) e `ai_roles`
(informativo na v1alpha1; alimenta a estimativa de custo). Os recursos em estado desejado ficam em `spec.resources`
(§11).

### 10.7 Casos de validação (043)

- `skill_validation_cases` pertence à **definição** e vale numa faixa de versões (`since_version`/`until_version`):
  a regressão da v3 roda os casos que a v2 já passou.
- `kind`: `replay` \| `simulated` \| `device` \| `negative`. O caso negativo espera recusa. Por exemplo, "caixa de
  entrada com a linha do usuário e sem campo de escrita não prova `OPEN_THREAD`".
- `parameters` não guarda segredo.
- Os resultados vão para `skill_validation_results`, com `proof` (`real` \| `simulated`), `outcome`, `run_id`,
  `physical_id`, `app_version` e `variant`. É o mesmo desenho de `app_release_validations` (011): a transição lê
  observação registrada, nunca um booleano. `not_run` é a ausência de linha.

### 10.8 Proveniência

`source_kind` e `source_ref`, mais o JSON `provenance`: `candidate_id`, `teaching_id`, `generated_by`
(`ai:<modelo>` \| `person` \| `merge`), `compiler_version`, `uses_lock` (`[{skill, version, content_hash}]` de cada
skill composta) e `reviewed_by`.

---

## 11. RESOURCE MODEL

Um `ResourceSpec` declara o **estado desejado** de que a skill precisa: `{kind, target, desired, on_missing: wait |
apply | ask}`. O `ResourceProvider` (§7) executa o ciclo `read_current_state → diff → plan → apply → verify →
reconcile`.

**Regras:**

- `read_current_state`, `diff` e `plan` são **puros**. Não podem chamar o que tem efeito: `_app_resolver` grava a
  versão desejada (`state.py:1128`), `_portas_do_app` dispara `run_device_job` (`scheduler.py:339-377`),
  `note_waiting` grava, `_draft_gate` paga IA, `_approval_gate` abre pedido de aprovação e as lambdas do
  `session_gate` autenticam.
- `apply` passa **só por `commands`**, com cerca, outbox e diário (R10, §14.5). `uncertain` nunca é repetido.
- `verify` exige estado observado. A ausência de prova não fecha como falha.
- `reconcile` pertence ao backend que hospeda (`so_meu`, `repository.py:722`) e roda antes de publicar qualquer coisa.
- `objectives.resource_plan` (045) fotografa o spec resolvido por aparelho no `materialize` (feito na H parte 2,
  `RunService._fotografar_recursos`) e, **proposto**, de novo no despacho, pelo mesmo motivo de
  `worker_id`/`physical_id` (022).

**Providers iniciais:** todos leem com o que já existe e aplicam pelos comandos que já existem.

| `kind` | Lê (sem efeito) | Aplica | Verifica | Reconcilia |
|---|---|---|---|---|
| `device.state` (`online`) | `DeviceRuntime.state`, `readiness_phase`, `instances.desired_state` (014) | pede ao rodízio, com a mesma demanda do `_tick` (`scheduler.py:194`), por `pedir_ciclo_de_vida` (`api.py:2282` → `commands/despacho.py`). Nunca liga "por fora" do rodízio | `online` + prontidão `ready` (`devices/prontidao.py`) | `commands/reconciler.py` |
| `app.installation` (`release: promoted`) | `device_app_state` (`releases/repository.py`), `AppState.release_no_aparelho` (`state.py:860`), `fora_da_convergencia` (`:885`), `_app_preflight` (`:1051`) | `run_device_job` → `AppState._entregar`/`ReleaseService.install_on` (comando `app.*`) | `installed` + versão observada | `ReleaseService.reconcile_after_restart` (`releases/service.py:650`), `_reverificar_interrompidas` |
| `account.binding` | `device_profile_bindings` (`social/repository.py:218`) | **nenhuma aplicação automática**: `on_missing: ask`, porque vincular é decisão de pessoa | vínculo ativo | — |
| `app.session` | `SocialRepository.session_row`, status do perfil | job do `_session_gate` (`state.py:769`) → `InstagramAuthenticator.ensure_session`, só com credencial no cofre (ADR-025). Desafio e 2FA vão para `human` (ADR-009/029) | `session_ready` e perfil `active` | caminho de `_sessao_desmentida` |

**Como ficou na H2 (27/09):** o `reconcile` dos providers não chama `reconcile_after_restart` nem o caminho de
`_sessao_desmentida`: ele relê o estado depois do comando e só fecha `succeeded` com leitura positiva, no backend
que hospeda o aparelho. Os mecanismos da última coluna continuam sendo os de reinício e de tela contraditória, que
não mudaram. Ver [execução](../dominios/execution.md).
**Antes da fase H**, `spec.resources` é validado pelo compilador e usado só no PREFLIGHT de leitura (`pre_voo`,
`service.py:337`, e `_app_preflight`). A aplicação continua nas portas atuais do `_tick`, que já são, de fato, o
"apply" desses recursos. A fase H só as põe atrás do `ResourceProvider`, sem mudar a ordem.

---

## 12. AUTOMATION IR + DSL `automation/v1alpha1`

### 12.1 Pipeline do compilador

```
YAML/JSON ──parse──▶ SkillDocument (Pydantic, extra="forbid", contracts/skills/v1alpha1.py)
          ──validar estrutura──▶ ids, tipos, faixas
          ──resolver referências──▶ capabilities (CapabilityCatalogPort) · skills (SkillRegistry, versão fixada)
          ──expandir composição──▶ nós filhos com ids qualificados, dependências religadas
          ──tipar e baixar expressões──▶ ${parameters.x} → {x} · ${item} → {item}
          ──governança──▶ efeito exige capability em app com catálogo · nada afrouxa o catálogo
          ──▶ SkillIR (ProcessGraph de ProcessNode tipados; imutável; com hash)          [domínio]
          ──lowering──▶ CapabilityNode → CapabilityCatalog.build_step · nó goal → PlanStep  [infraestrutura]
          ──▶ Plan(planner=PlannerInfo(provider="skill", model="skill:<id>@<n>", simulated=False))
          ──validador do Plan (models.py:184-218) como última porta──▶ ExecutableGraph = Plan + origem por etapa
```

- **O IR é construído no domínio; o lowering fica na infraestrutura.** `Plan` e `build_step` são legado
  (`app.models`, `app.planning`), e a regra D2 impede o domínio de vê-los. O compilador **reusa** `build_step`, onde já
  moram a política de texto, as guardas, o `commit_selector`, `max_attempts=1` com efeito e a pós-condição do catálogo.
  Não o reimplementa.
- **Dois momentos de compilação**, ambos determinísticos, sem IA e em milissegundos:
  - em `draft → candidate`, sem valores: todos os ramos, para achar erros;
  - na RESOLVE de cada execução, com os parâmetros ligados: avalia `when`, baixa as expressões e produz o `Plan` da
    execução.
- **`PlanStep.origin`** (proposto; campo opcional aditivo, padrão `None`) = `{skill_id, skill_version, node_id,
  strategies}`. É necessário porque `recovery_steps`, `_expand_for_each` e `revise_plan` releem `runs.plan` e reinserem
  etapas (`scheduler.py:1138, 1232-1233`; `repository.py:610`). Sem a origem no próprio `Plan`, a trilha da 045 se
  perderia no replanejamento. `_insert_steps` copia a origem para `steps.skill_id`, `skill_version`, `node_id` e
  `strategy`.
- **Invariante (decisão 4).** O compilador é o **único** produtor de `Plan` para skill nova e nunca emite nem executa
  Python. A candidata vinda do LLM é **dado** validado contra o schema. Os testes (fase E):
  1. regra D15 por AST;
  2. golden: para cada documento válido em `tests/fixtures/dsl/v1alpha1/validos/`, a saída é um `Plan` com
     `planner.provider == "skill"` e ida e volta por `model_dump`/`model_validate` idêntica;
  3. uma candidata com código Python em qualquer campo de texto é tratada como texto inerte, e um campo fora do schema
     dá `E_SCHEMA`;
  4. uma execução com `runs.skill_id` preenchido tem `plan.planner.provider == "skill"`, e o hash do plano bate com uma
     nova compilação da mesma versão com os mesmos parâmetros.

### 12.2 Schema

**Documento:**

| Campo | Tipo | Obrigatório | Vira |
|---|---|---|---|
| `apiVersion` | `"automation/v1alpha1"` exato | sim | `schema_version = 1` |
| `kind` | `"Skill"` | sim | — |
| `metadata.id`, `.name`, `.description`, `.app`, `.labels` | slug, texto, texto, id de app, mapa | `id`, `name`, `app` | `skill_definitions` |
| `spec.invocation.command_template` | texto com `{param}` | sim | `command_template`, `match_key` |
| `spec.invocation.examples` | lista de comandos | não | testes do `IntentResolver` |
| `spec.parameters[]` | `ParameterSpec` (§10.4) | não | `Plan.parameters` |
| `spec.requires` | `{apps, secrets, device, ai_roles}` | não | `Plan.required_apps`, pré-voo |
| `spec.resources[]` | `ResourceSpec` (§11) | não | `objectives.resource_plan` |
| `spec.uses[]` | `{skill, version}` com versão **exata** | quando há nó `skill` | `provenance.uses_lock` |
| `spec.nodes[]` | nó (abaixo) | sim, ≥ 1 | `Plan.steps` |
| `spec.outputs[]` | `{name, from: ${steps.<coleta>.output}}` | não | relatório |
| `spec.success_criteria[]` | texto | não | `Plan.success_criteria` (texto, como hoje) |
| `spec.policy` | só `inherit` | não | governança inteira do catálogo e do perfil; outro valor dá `E_FIELD_RESERVED` |
| `spec.validation.cases[]` | caso (043) | não (sem caso, aviso `W_NO_VALIDATION_CASE`) | `skill_validation_cases` |

**Nó** (`ProcessNode`):

| Campo | Regra | Vira |
|---|---|---|
| `id` | `NodeId` | `PlanStep.key` (decisão 2) |
| um de `capability` / `goal` / `skill` | chave do catálogo do app; contrato inline; skill de `uses` | `capability` + `build_step`; `PlanStep` livre; expansão (§12.3) |
| `app` | padrão `metadata.app` | `PlanStep.app_id` quando difere |
| `with` | mapa parâmetro → expressão | `bindings`, ou parâmetros da skill chamada |
| `depends_on` | ids anteriores. **Omitido = depende do nó anterior**; independência exige `depends_on: []` explícito | `PlanStep.depends_on`, **sempre emitido** |
| `foreach` | `${steps.<coleta>.output}` | `PlanStep.for_each = <coleta>` |
| `when` | expressão **estática** sobre parâmetros: `${parameters.x}`, `== 'lit'`, `!= 'lit'` | nó omitido na compilação por execução |
| `timeout_s` | 10..900; padrão do catálogo | `timeout_s` |
| `retries` | 0..4 | `max_attempts = retries + 1`; com efeito, `E_RETRY_ON_EFFECT` (P5: efeito tem `max_attempts=1`) |
| `strategies` | subconjunto ordenado de `StrategyKind`; padrão `[recipe, ai_actor]` | `origin.strategies`, `steps.strategy` |
| `verification` | nó `capability`: só `required_delivery_level`, e só para subir. Nó `goal`: `postcondition {kind, value, description, required_delivery_level}` com `kind` ∈ `text_visible`, `app_foreground`, `element_present`, `model_judged` | `Postcondition` |
| `side_effect` | nó `goal` declara; nó `capability` tem de bater com o catálogo | `PlanStep.side_effect` |
| `commit_guard` | só nó `goal` | `PlanStep.commit_guard` |
| `on_failure` | reservado (`fail`); hoje é regra global do scheduler | — |

**`local_proof` não existe na DSL.** A prova local é um **atalho positivo**: `True` dispensa o verificador
(`proofs.py:37-39`). Declará-la num documento afrouxaria a verificação, então só o catálogo, que é código revisado,
declara prova local (`E_VERIFICATION_WEAKENED`). Pelo mesmo motivo, política e aprovação por nó ficam para a
v1alpha2. Endurecer por nó exigiria um campo em `PlanStep` que o `_policy_gate` lesse. Na v1alpha1 a governança é a
de hoje, inteira: `PolicyEngine`, `_draft_gate` e `_approval_gate`.

**Expressões:**

| Expressão | Vale em | Vira |
|---|---|---|
| `${parameters.x}` | `with`, textos do nó `goal`, `commit_guard`, `postcondition.value`, `when` | `{x}` |
| `${item}` | dentro de nó com `foreach` | `{item}` |
| `${steps.<id>.output}` | só em `foreach` e `outputs[].from`; `<id>` precisa ser nó de coleta | `for_each=<id>` |
| `${secrets.*}` | em lugar nenhum | `E_SECRET_INLINE` |
| `{x}` cru num texto | em lugar nenhum fora de `command_template` | `E_RAW_PLACEHOLDER` |

### 12.3 Composição

- Todo nó `skill:` precisa de uma entrada em `spec.uses` com a versão **exata**. A trava vai para
  `provenance.uses_lock`, com o `content_hash` do filho. Publicar uma versão nova do filho **não muda** a composta
  já publicada.
- **Expansão.** O nó de chamada `abrir`, de uma skill com os nós `n1..nk`, vira `abrir_n1..abrir_nk`. O id
  resultante tem de caber em `NodeId` (`E_NODE_ID_TOO_LONG`), e a unicidade é conferida **depois** de simular o
  truncamento das cópias de `for_each` (`_copy_key`, `foreach.py:21-23`: `key[:41-len("_iN")] + "_iN"`), senão
  `E_NODE_ID_COLLISION`.
- **Parâmetros.** Cada `${parameters.p}` do filho é substituído pela **expressão** que a chamadora passou em `with.p`.
  É substituição de dado, nunca de código. Parâmetro obrigatório sem valor e sem `default` dá `E_MISSING_ARGUMENT`.
- **Dependências.** As raízes do filho herdam o `depends_on` do nó de chamada, e quem depende do nó de chamada passa a
  depender dos **sumidouros** do filho. O compilador **sempre emite `depends_on`**. É o que fecha a lacuna do treino,
  em que etapas sem aresta eram promovidas juntas (`repository.py:318-323`) e uma podia rodar antes da nova tentativa
  da anterior. É também o que faz `READ_MESSAGES` achar o `username` da conversa: `_alvo_da_conversa` lê os
  `bindings` das etapas listadas em `depends_on`.
- `requires` e `resources` do filho entram por união. Um conflito dá `E_RESOURCE_CONFLICT`.
- Profundidade máxima de 3 (`E_COMPOSITION_DEPTH`); ciclo de skills dá `E_SKILL_CYCLE`.
- Na trilha, `steps.skill_id` é o do filho e `runs.skill_id` é o da raiz.
- **Limite conhecido.** A identidade de receita é o texto da etapa (`step_template_hash` usa `template_key or key`).
  O mesmo `OPEN_THREAD` com outro id de nó, avulso (`abrir_conversa`) ou composto (`abrir_abrir_conversa`), não
  compartilha receita. Isso é aceito na v1alpha1; a identidade por capability fica para a fase K.

### 12.4 Exemplo 1: `ig.abrir_conversa`

```yaml
apiVersion: automation/v1alpha1
kind: Skill
metadata:
  id: ig.abrir_conversa
  name: Abrir conversa no Instagram
  description: Abre a conversa direta com um usuário, existente ou nova, pronta para escrever.
  app: instagram
spec:
  invocation:
    command_template: "abra a conversa com {username} no instagram"
    examples:
      - "abra a conversa com @ana.teste no instagram"
  parameters:
    - name: username
      type: handle
      required: true
      example: "@ana.teste"
      description: Usuário do Instagram, com ou sem arroba.
  requires:
    apps: [instagram]
  resources:
    - kind: device.state
      desired: online
      on_missing: wait
    - kind: app.installation
      target: instagram
      desired: {release: promoted}
      on_missing: wait
    - kind: app.session
      target: instagram
      desired: {session: ready, account: bound_profile}
      on_missing: ask
  nodes:
    - id: abrir_inbox
      capability: OPEN_INBOX
      depends_on: []
      strategies: [recipe, ai_actor]
    - id: abrir_conversa
      capability: OPEN_THREAD            # sem efeito externo: retries permitido
      depends_on: [abrir_inbox]
      with:
        username: ${parameters.username}
      strategies: [recipe, ai_actor]
      timeout_s: 180
      retries: 2
  outputs: []
  success_criteria:
    - "A conversa com ${parameters.username} está aberta e o campo de escrever está disponível."
  policy: inherit
  validation:
    cases:
      - name: conversa-existente
        kind: simulated
        parameters: {username: "@ana.teste"}
        expected: {outcome: succeeded, proofs: [local_proof]}
      - name: inbox-nao-prova-conversa
        kind: negative
        parameters: {username: "@ana.teste"}
        preconditions: {screen: inbox_com_linha_do_usuario}
        expected: {outcome: not_proved}
```

Plano da execução gravado em `runs.plan`: as etapas ficam em forma de modelo (`{username}`) e o valor vai em
`parameters`; quem resolve é o `materialize`:

```json
{"summary": "Abrir conversa no Instagram", "app_id": "instagram", "required_apps": ["instagram"],
 "parameters": {"username": "@ana.teste"},
 "planner": {"provider": "skill", "model": "skill:ig.abrir_conversa@1", "simulated": false},
 "steps": [
  {"key": "abrir_inbox", "capability": "OPEN_INBOX", "depends_on": [], "title": "Abrir as mensagens", "...": "do catálogo",
   "origin": {"skill_id": "ig.abrir_conversa", "skill_version": 1, "node_id": "abrir_inbox", "strategies": ["recipe", "ai_actor"]}},
  {"key": "abrir_conversa", "capability": "OPEN_THREAD", "depends_on": ["abrir_inbox"],
   "title": "Abrir a conversa com {username}", "bindings": {"username": "{username}"}, "max_attempts": 3, "timeout_s": 180,
   "origin": {"skill_id": "ig.abrir_conversa", "skill_version": 1, "node_id": "abrir_conversa", "strategies": ["recipe", "ai_actor"]}}]}
```

### 12.5 Exemplo 2: `ig.ler_conversa` (composta)

```yaml
apiVersion: automation/v1alpha1
kind: Skill
metadata:
  id: ig.ler_conversa
  name: Ler a conversa com um usuário
  description: Abre a conversa reusando ig.abrir_conversa e lê as mensagens recentes, sem responder.
  app: instagram
spec:
  invocation:
    command_template: "leia a conversa com {contato} no instagram"
  parameters:
    - name: contato
      type: handle
      required: true
      example: "@ana.teste"
  uses:
    - skill: ig.abrir_conversa
      version: 1
  nodes:
    - id: abrir
      skill: ig.abrir_conversa
      depends_on: []
      with:
        username: ${parameters.contato}
    - id: ler
      capability: READ_MESSAGES          # coleta: sem efeito, não pode ter foreach
      depends_on: [abrir]
      strategies: [recipe, ai_actor]
  outputs:
    - name: mensagens
      from: ${steps.ler.output}
  policy: inherit
```

Depois da expansão, são três etapas:

- `abrir_abrir_inbox` (`OPEN_INBOX`, `depends_on: []`, origem `ig.abrir_conversa@1`);
- `abrir_abrir_conversa` (`OPEN_THREAD`, `depends_on: [abrir_abrir_inbox]`, `bindings.username = "{contato}"`, origem
  `ig.abrir_conversa@1`);
- `ler` (`READ_MESSAGES`, `depends_on: [abrir_abrir_conversa]`, origem `ig.ler_conversa@1`).

O `Plan` leva `parameters = {"contato": …}` e `planner.model = "skill:ig.ler_conversa@1"`. Os recursos do filho são
herdados.

**Fragmento com `foreach` e `when`** (ilustrativo):

```yaml
  parameters:
    - {name: incluir_arquivadas, type: boolean, required: false, default: false}
  nodes:
    - id: listar
      capability: COLLECT_THREADS
      depends_on: []
    - id: cada
      skill: ig.abrir_conversa            # bloco foreach: as cópias viram cada_abrir_inbox_i1, cada_abrir_conversa_i1…
      foreach: ${steps.listar.output}
      with: {username: ${item}}
    - id: arquivadas
      goal:
        title: Abrir a pasta de arquivadas
        goal: Abrir a lista de conversas arquivadas.
      verification:
        postcondition: {kind: model_judged, value: "lista de arquivadas aberta"}
      side_effect: false
      when: ${parameters.incluir_arquivadas} == 'true'
      depends_on: [listar]
```

O nó `arquivadas` é `goal` num app com catálogo e sem efeito, e por isso é permitido. Com `side_effect: true`, daria
`E_CAPABILITY_REQUIRED`.

### 12.6 Erros do compilador

Cada erro sai como `{code, message, path (ponteiro JSON), severity}` e nunca como 500, como o corpo `{"detail": {code,
message}}` que o painel já lê (`tests/test_contrato_http.py:29`).

| Código | Quando | Equivalente hoje |
|---|---|---|
| `E_SCHEMA`, `E_API_VERSION`, `E_KIND` | documento fora do schema (`extra="forbid"`), versão ou tipo desconhecido | — |
| `E_ID_FORMAT`, `E_NODE_ID_FORMAT`, `E_NODE_ID_DUPLICATE` | id fora do padrão ou repetido | regex de `PlanStep.key` (`models.py:137`) |
| `E_NODE_ID_TOO_LONG`, `E_NODE_ID_COLLISION` | expansão ou truncamento de `for_each` estoura ou colide | `_copy_key` (`foreach.py:21`) |
| `E_COMMAND_AMBIGUOUS` | `}{` colados no comando | `ambiguous_command` (`training/skills.py:96`) |
| `E_COMMAND_PARAMETER_MISMATCH`, `E_COMMAND_RESERVED` | placeholder sem parâmetro, ou parâmetro obrigatório fora do comando; nome em `RESERVED` | `flows.py:18`, `:146-148` |
| `E_UNKNOWN_PARAMETER`, `E_MISSING_ARGUMENT`, `E_SECRET_PARAMETER` | referência a parâmetro inexistente; chamada sem argumento; parâmetro com cara de segredo | — |
| `E_UNKNOWN_CAPABILITY`, `E_CAPABILITY_INTERNAL` | chave fora do catálogo do app; capability `internal=True` (`capabilities.py:58`) | `capability_required` |
| `E_CAPABILITY_REQUIRED` | nó `goal` com efeito num app com catálogo | `training/skills.py:115-120`, `state.py:1351-1363` |
| `E_MISSING_BINDING` | binding exigido pelo catálogo sem valor | `MissingBinding` (`capabilities.py:152-165`) |
| `E_SIDE_EFFECT_MISMATCH`, `E_RETRY_ON_EFFECT` | efeito declarado diverge do catálogo; `retries` em nó com efeito | P5 (`capabilities.py:182`) |
| `E_VERIFICATION_WEAKENED`, `E_FIELD_RESERVED` | `local_proof` na DSL ou nível de entrega rebaixado; `policy`/`on_failure` fora do permitido | — |
| `E_DEPENDENCY_UNKNOWN`, `E_DEPENDENCY_CYCLE` | dependência inexistente ou ciclo | validador do `Plan` |
| `E_FOREACH_SOURCE`, `E_FOREACH_NOT_CONTIGUOUS`, `E_FOREACH_NO_ITEM`, `E_COLLECT_WITH_EFFECT` | fonte não é coleta; bloco partido; bloco sem `{item}`; coleta com efeito ou `foreach` | validador do `Plan` (`models.py:196-217`) |
| `E_OUTPUT_REF_UNSUPPORTED`, `E_EXPRESSION`, `E_RAW_PLACEHOLDER`, `E_SECRET_INLINE` | expressão fora da tabela da §12.2 | — |
| `E_WHEN_UNSUPPORTED`, `E_WHEN_DEPENDENCY` | `when` não estático; nó omitido por `when` é dependência de nó incluído | — |
| `E_SKILL_NOT_FOUND`, `E_SKILL_VERSION_UNPINNED`, `E_SKILL_CYCLE`, `E_COMPOSITION_DEPTH` | composição inválida | — |
| `E_APP_UNKNOWN`, `E_STRATEGY_UNKNOWN`, `E_STRATEGY_UNAVAILABLE` | app inexistente; estratégia fora do enum ou sem provider (`deterministic`/`app_provider` na v1alpha1) | — |
| `E_RESOURCE_UNKNOWN_KIND`, `E_RESOURCE_CONFLICT` | recurso fora dos 4 tipos da §11; conflito na união | — |
| `E_DUPLICATE_COMMAND` | na publicação: `match_key` de uma publicada ou de um fluxo ativo | `duplicate_command` (`flows.py:81-82`) |
| `E_PLAN_INVALID` | o validador do `Plan` recusou (última porta) | `models.py:184-218` |

Avisos, que não impedem `candidate`: `W_PARAMETER_NO_EXAMPLE`, `W_NO_VALIDATION_CASE`, `W_PARAMETER_UNUSED`.

---

## 13. TEACHING MODEL

### 13.1 `TeachingSession` v2 (044)

- **Campos:**
  - `instruction`, redigida antes de gravar e **recusada** se contiver credencial, com a mesma regra de
    `service.py:98-102`, porque o texto vai ao provedor de IA;
  - `skill_id` (nulo = skill nova) e `base_version` (ensino que melhora a versão N);
  - `app_id`, `profile_id`, `status`, `validation_status`, `result_version_id` e `operator`.
- **Fontes.** São derivadas do conteúdo, sem coluna, porque uma sessão muda de fonte ao ganhar uma demonstração:

| Fonte | Como se reconhece |
|---|---|
| `instruction` | instrução, sem demonstração; não precisa de aparelho |
| `demonstration` | uma ou mais gravações (`teaching_demonstrations.kind='recording'`) |
| `hybrid` | instrução + demonstração: é o que o treino v1 faz hoje |
| `correction` | `skill_id`/`base_version` + turno `correction` apontando para `run_id`/`step_id` de uma etapa `failed` ou `uncertain` |
| `successful_execution` | demonstração `kind='run'` de uma execução `completed` |

- **Estados** (vocabulário da 044): `open → demonstrating → open` (gravando; "propor" é recusado com gravação ativa,
  como `still_recording` hoje); `open → proposing` (generalização paga); `proposing → asking` (a candidata tem
  perguntas) ou `→ validating` (compilou sem erro); `asking → proposing` (respostas dadas); `validating → ready | open`;
  `ready → published` (grava `result_version_id`); qualquer não terminal → `discarded`. Os terminais são `published`
  e `discarded`. Toda transição é por CAS (`WHERE status=?`), o que fecha a corrida de `TrainingSkills.propose`
  (`skills.py:79`).

### 13.2 `SkillCandidate`

`teaching_candidates.content` é um envelope: `{document, annotations}`.

- `document` é um `SkillDocument` v1alpha1 e é **só ele** que vai para `skill_versions.content` na aceitação.
- `annotations` guarda `evidence` por nó (as seqs das entradas gravadas ou os ids de etapa que sustentam o nó),
  `discarded` (`[{seq, why}]`), `assumptions` e `parameter_examples`.
- As **perguntas** viram linhas `teaching_turns(kind='question', author='ai', candidate_id)`, e as respostas
  `teaching_turns(kind='answer', reply_to=<pergunta>)`. Hoje as perguntas só são exibidas, sem canal de resposta
  (`TrainingReview.tsx:161-163`). Os tipos de pergunta são `ambiguity`, `missing_parameter`, `effect_confirmation`,
  `scope` e `policy`, e a origem é `ai` ou `compiler`. Pergunta do compilador sai de um aviso ou erro que só uma pessoa
  resolve.
- **Saída do LLM:** o mesmo `CandidateEnvelope`, com `document` validado pelo `SkillDocument` (`extra="forbid"`). É
  **dado**, nunca código (decisão 4). Documento inválido deixa a candidata `rejected` com os erros, e gerar de novo é
  pedido explícito da pessoa, porque custa. O LLM não escolhe `local_proof`, política nem estratégia fora do enum; os
  `E_*` pegam.
- **Geração:** porta `SkillGeneralizer`. O Protocol `AIProvider` (`planning/provider.py:172-181`) não tem `generalize`;
  hoje ele é chamado por duck typing pelo `RoutingProvider` (`routing.py:242-244`) no papel `plan`. A porta nova o
  declara (proposto), e o custo continua em `add_usage`.
- Candidata não é versão: as rejeitadas não gastam número de versão. A aceita vira `skill_versions` em `draft`, com
  `source_kind='teaching'`, `source_ref=<ensino>` e `provenance.candidate_id`.

### 13.3 Relação com `training_sessions` e o gravador

- **O gravador não muda.** O gancho `on_training_input` (`state.py:308`), `rt.training_session_id` e a regra de
  controle manual (`recorder.py:50-72`) ficam iguais. Cada demonstração **é** uma gravação v1, ligada por
  `teaching_demonstrations.training_session_id`, com índice único: uma gravação pertence a no máximo um ensino.
- **Uma sessão v1 aparece como v2 na leitura:** `hybrid`, com uma demonstração. Nenhuma linha nova é criada.
- **As rotas v1** (`api.py:357-425`) e `TrainingSkills.save` continuam produzindo fluxos até a fase J. **Não viram
  ponte** para skill: seria escrita dupla (decisão 1). O painel oferece a v2 só com `features.skills`.
- **Lacunas do v1 que a v2 fecha:**
  - `depends_on` sempre emitido (§12.3); perguntas com resposta; fonte `correction` ligada à etapa que falhou;
    atomicidade (candidata → versão numa transação);
  - destilação de receitas (`distill_training`, `recipes.py:277`) na publicação, com uma única função de identidade
    `step_template_hash(para_hash(step, exemplos))` usada no treino e no `materialize` (hoje `skills.py:177` e
    `repository.py:235` divergem quando o valor de um parâmetro aparece no texto fixo);
  - versão, variante e assinatura do app **no momento da gravação**, e não no `save` (`skills.py:163-175`). Isso pede
    uma coluna `app_snapshot` em `teaching_demonstrations` (mudança no rascunho 044, §19).

---

## 14. EXECUTION MODEL

### 14.1 O ciclo, fase a fase

| Fase | O que faz | Código que já faz | O que muda (fase) |
|---|---|---|---|
| **RESOLVE** | comando → `ResolvedSkill {ref, parameters, method, backend}` | `FlowStore.match` em `RunService._plan` (`service.py:495`), atrás de `ai.flows` | `_plan` consulta o `SkillRegistry` (G); grava `runs.skill_id`, `skill_version` e `skill_hash` (045). No legado, `runs.flow_id` como hoje e `skill_hash` calculado |
| **COMPILE** | versão + parâmetros → `Plan` | planejador: `compose`/`build_step` (`capabilities.py:185, 152`); fluxo: plano congelado | `SkillCompiler` (E); legado = passagem direta do plano congelado |
| **PLAN** | persistir e expor | `save_plan` + `materialize` (`repository.py:189, 193`); `mode=plan` para em `planned` (`service.py:550-554`) | `PlanReport` (§14.2), montado só pela trilha pura |
| **PREFLIGHT** | recusar ou esperar antes de gastar | `pre_voo` na criação e no `start` (`service.py:337`, `574-582`); portas do `_tick` (`scheduler.py:215-291`); `policy_gate` antes de `claim_step` (`scheduler.py:908`) | recursos em leitura (§11); porta "versão ainda publicável" antes de materializar |
| **APPLY** | nó → estratégia | `claim_step` (`repository.py:353`) → `run_step`/`_run_step` (`executor.py:426, 597`); `log_intent` antes do efeito (`:1008`) | a ordem vem de `origin.strategies`; grava `attempts.strategy` e `attempts.recipe_id` (G) |
| **VERIFY** | prova observada | `_verify` (`executor.py:1185`): determinística → prova local → modelo; tela vazia não prova; `failure_marks` | `CapabilityProvider.verify` embrulha isso; o sucesso continua gravado pelo executor |
| **RECONCILE** | efeito sem desfecho | `commit_state` (`repository.py:450`); `_reconciliar` (`scheduler.py:1369`); `reconcile_pending_effects` (`social/service.py:582`); `CommandStore.reconcile_after_restart` (`commands/store.py:215`); `commands/reconciler.py` | nada novo; `CapabilityProvider.reconcile` delega |
| **COMPLETE** | desfecho | `_apply` (`scheduler.py:1044`), `_maybe_complete` (`:1211`), `recompute_run` (`repository.py:672`), `_learn_flow` (`scheduler.py:966`) | **`_learn_flow` pula execução com `runs.skill_id`** (G). Sem isso, `learn_from_run`, que só olha `flow_id` (`flows.py:41`), criaria um fluxo com o mesmo comando da skill |

A divisão de hoje fica: **o sucesso é gravado pelo executor (VERIFY) e os demais desfechos pelo scheduler
(COMPLETE)**. Nenhuma estratégia nova muda isso.

### 14.2 PLAN estruturado (`PlanReport`: parte de recursos feita na fase H, servida no `mode=plan`)

**Estado (27/09):** a parte dos recursos (`skill`, `targets`, `resources`, `risks`, `human_interventions`, `blockers`)
existe e é servida em `mode=plan` desde a H parte 2, montada por `RunService.relatorio_de_recursos` sobre os providers
sem canal ([execution](../dominios/execution.md#modeplan-o-planreport-servido-fase-h-parte-2)). Os demais campos desta
tabela continuam propostos.

Montado por uma função irmã de `previa_de_distribuicao` (`service.py:202`), só com fontes sem efeito:

| Campo | Fonte |
|---|---|
| `skill` | `SkillRef`, `content_hash`, avisos da compilação |
| `targets` | `RunCreate.instance_ids`/`profile_ids`/`distribute`; `previa_de_distribuicao` |
| `resources` | `Scheduler.servidores()` (`scheduler.py:629`), `candidatos_do_app`, `ai_slots.ocupadas()`; `ResourceSpec` resolvido por aparelho |
| `current_state` / `desired_state` / `drift` | `rt.state`, `connectivity`, `control`; `device_app_state` desejado × instalado; sessão e status do perfil; `_caminho_ja_aberto` (`scheduler.py:517`); `commit_state` de uma execução retomada |
| `actions`, `deps` | `Plan.steps`, `depends_on`, `for_each` (`foreach.py:28`) |
| `side_effects` | `step.side_effect`; `risk`, `interaction_type`, `limit_bucket`, `failure_marks` do catálogo |
| `approvals` | `PolicyEngine.check` → `Verdict.needs_approval/policy/retry_at` (`social/policy.py:209`); `needs_draft` avisa que haverá rascunho pago |
| `apps`, `capabilities` | `Plan.required_apps`; `load_catalog(pkg).offered` |
| `estimated_ai_calls`, `estimated_usd` | etapas sem receita × custo mediano de `decide` + etapas × `verify` (`social/capacidades.py:35-112`); 0 chamadas de `plan` quando a skill casa; + 1 `social` por etapa `needs_draft` |
| `human_interventions`, `blockers` | consentimento de credencial (`service.py:161-181`); aprovação; sessão que só pessoa resolve; o `{code, motivo, acao}` do `pre_voo` |

**Não entra no PLAN**, porque tem efeito: `_app_resolver`, `_portas_do_app`, `note_waiting`, `_draft_gate`,
`_approval_gate` e as lambdas do `session_gate` (§11).

### 14.3 Estratégias

| `StrategyKind` | Hoje | Onde | Na fase G |
|---|---|---|---|
| `deterministic` | só como capability `internal=True` resolvida por código, fora do laço da etapa | `InstagramAuthenticator.ensure_session`, `verification.read_account`, via `run_device_job` | não usada em nó (`E_STRATEGY_UNAVAILABLE`) |
| `recipe` | sim | `run_step` (`executor.py:434-448`), `Replayer` | primeira da cadeia |
| `app_provider` | não | molde: `InstagramAuthenticator` (observa → classifica → age → reclassifica) | **não entra** (decisão 7) |
| `ui_generic` | não | reservado para heurística de UI sem IA | não entra |
| `ai_actor` | sim | laço de decisão de `_run_step` (`executor.py:795-861`) | segunda da cadeia |
| `human` | sim, como desfecho | `waiting_user` + `resolve(confirm_done)` | último recurso implícito |

- **Escolha.** A cada tentativa, as estratégias de `origin.strategies` são percorridas em ordem, pulando as
  inaplicáveis. A receita só é aplicável com `ai.recipes != off`, app conhecido, receita ativa para a chave e efeito
  ainda não disparado, as mesmas condições de `executor.py:434-448`. A troca receita → IA **dentro** da tentativa
  continua sendo o caminho de `RecipeDiverged`.
- **Registro.** `attempts.strategy` guarda a cadeia exercida (`recipe`, `ai_actor` ou `recipe>ai_actor`) e
  `attempts.recipe_id`, a receita reproduzida; `steps.strategy` é o que foi planejado; `steps.driven_by` segue como
  está, porque o painel o tipa como união fechada (`frontend/src/api/types.ts:222`).
- **Nenhuma estratégia decide sucesso;** quem decide é o VERIFY. Depois de `fired`, só VERIFY e RECONCILE.

### 14.4 Trilha da execução

| Dimensão | Coluna | Estado |
|---|---|---|
| intenção | `runs.command`; método da resolução em `repo.decision` | existe |
| skill, versão, hash | `runs.skill_id`, `skill_version`, `skill_hash` | 045 |
| processo (skill composta) | `steps.skill_id`, `steps.skill_version` | 045 |
| nó | `steps.node_id` (= `template_key or key`) | 045 |
| capability | `steps.capability` (010), a ação do catálogo e chave de política; **reusada, não redefinida** | existe |
| estratégia | `steps.strategy` (planejada), `attempts.strategy` e `attempts.recipe_id` (efetivas), `steps.driven_by` (veredito) | 045 + existe |
| app | `steps.app_id`, `runs.app_ids` (037) | existe |
| aparelho | `objectives.instance_id`, `device_serial`, `physical_id` (022) | existe |
| worker e backend | `objectives.worker_id`, `hosted_by` (022); `steps.claimed_by` (016) | existe |
| papel e modelo de IA | `ai_calls.role`, `model`, `requested_model`, `fallback`, `provider` (003/032/033) | existe |
| tentativa | `attempts`; **`ai_calls.attempt_id`** para atribuir custo e modelo à tentativa | existe (045; gravado pelo `_ai` desde a fase G) |
| evidência | `evidence.run_id/step_id/attempt_id/kind/storage` (001/030) | existe |
| recursos | `objectives.resource_plan` | 045 |

### 14.5 Proteções herdadas

O runtime de skills **reusa** o executor e o scheduler. Toda regra abaixo continua valendo por construção, e nenhuma
estratégia pode contorná-la.

| # | Regra | Onde mora |
|---|---|---|
| R1 | intenção gravada antes do efeito (`intended` → `done`/`failed`/`unknown`) | `executor.py:1008`, `repository.py:430` |
| R2 | um commit por etapa em todas as tentativas; depois de `fired`, só VERIFY/RECONCILE | `commit_state` (`repository.py:450`), `executor.py:966-967` |
| R3 | falha depois do disparo é `uncertain`, nunca `retry`/`failed`, inclusive por orçamento, recusa, exceção, prazo e driver travado | `executor.py:680-684, 849, 855, 1152-1154, 1294`; `scheduler.py:998-1000, 1099-1107` |
| R4 | `uncertain` só sai por pessoa ou por sonda que prove sucesso; nem o cancelamento o fecha | `service.resolve`; `commands/reconciler.py:18-21`; `_finish_cancel` (`scheduler.py:1297-1299`) |
| R5 | posse antes de escrever (`claim_step`, `transition_step`, `finish_attempt`); `PosseDaEtapaPerdida` larga o aparelho | `repository.py:269-425` |
| R6 | portas antes da posse: política, limite, rascunho e aprovação sem consumir tentativa nem IA | `scheduler.py:908-912`, `state.py:1345-1606` |
| R7 | interrupção sem culpa não consome tentativa (`refund_attempt`) | `repository.py:406` |
| R8 | prova observada, não declarada; `verified=False` só por decisão humana | `executor.py:1139`, `service.py:700-701` |
| R9 | recuperação não atravessa efeito comprovado nem rejeição definitiva; no máximo 1 revisão automática | `scheduler.py:1229-1271` |
| R10 | verbo de aparelho vai por `commands` (cerca, outbox, diário); ação de UI vai por `actions`; não há terceiro canal | ADR-010; `commands/*` |
| R11 | reconciliação pertence ao hospedeiro e roda antes de publicar | `state.py:1637-1669` (ordem da partida), `so_meu` |
| R12 | toda chamada de IA passa por `_ai` (disjuntor, orçamento, `ai_slots`, `ai_calls`) | `executor.py:264-348` |
| R13 | efeito social abre `pending` no commit e fecha pelo desfecho observado; só `succeeded` ensina memória | `social/service.py:516-561` |
| R14 | o que não se sabe não fecha a porta; o que só uma pessoa resolve bloqueia antes de agendar | `pre_voo`, `devices/compatibilidade.py` |
| P14 | receita nunca digita credencial nem abre URL | `UNSAFE_TO_REPLAY` (`recipes.py:31-34`) |
| P15 | credencial só em campo de senha, só no app da etapa, só no host escrito pela pessoa | `executor.py:170-240` (ADR-025) |

**O que não fazer:**

- fila paralela a `steps`/`attempts` (perderia o índice 018, o lease, a cerca, `promote` e `recovery_steps`);
- status escrito fora de `transition_step`/`finish_attempt`;
- provedor chamado fora de `_ai`;
- estratégia que declara o próprio sucesso.

---

## 15. LEGACY COMPATIBILITY

### 15.1 Mapeamentos

| Legado | Novo | Como |
|---|---|---|
| `Flow` | `LegacySkillVersion` `flow:<flows.id>@1` | Vem do `LegacyFlowAdapter`, só leitura. O estado é `published` se o fluxo está `active` e `disabled` caso contrário. O `content` é `{schema_version: 0, command_template, plan}` mais os apps de `flow_required_apps`. O `content_hash` é calculado de forma canônica e não é gravado. A proveniência vem de `flows.source`/`source_run_id`, e o escopo de `flow_scope`. **A compilação é passagem direta:** o plano congelado já é um `Plan`, com `planner = "fluxo"/"fluxo:<id>"` como hoje (`flows.py:149`), e a execução é idêntica à atual (`runs.flow_id`, `flows.used()`) |
| `PlanStep` | `ProcessNode` | `key` vira `node_id`. `capability` + `bindings` viram um nó `capability` com `with`, e uma etapa livre vira um nó `goal` com contrato inline. `depends_on` vira as arestas; `for_each`/`items_collected` viram `foreach`/coleta; `app_id` vira `app`. `template_key` e `variables` são de runtime, não de definição |
| `Recipe` | dado da `RecipeExecutionStrategy` | A tabela `recipes` não muda. `RecipeKey` é a aplicabilidade. `RecipeStore`, `Replayer`, `distill` e `distill_training` ficam intocados; `QUARANTINE_AFTER` (`recipes.py:36`) vira política da estratégia; `attempts.recipe_id` liga a receita à tentativa |
| `Capability` | `CapabilityDefinition` | Mapeamento 1:1 de todos os campos, agrupados em contrato, efeito, governança e execução. Entram a versão do contrato e as estratégias disponíveis. `reconciliation` continua prosa até um provider consumi-lo; `collect` é redundante com `post_kind='items_collected'`. O catálogo em código continua sendo a fonte até a fase K |
| `TrainingSession` | `TeachingSession` legado, na leitura | `hybrid` com uma demonstração. Status: `recording → demonstrating`, `recorded → open`, `proposed → asking`, `saved → published` (o resultado é um fluxo, não uma versão) e `discarded → discarded` |

### 15.2 Um registro, dois backends, sem escrita dupla

- **Precedência da RESOLVE:** skill publicada, depois fluxo ativo, depois o planejador.
- **Publicar recusa** (`E_DUPLICATE_COMMAND`) quando um fluxo ativo tem o mesmo `match_key`, a menos que a
  publicação seja a própria conversão desse fluxo.
- **Conversão fluxo → skill** (fase J; adopt-on-write), numa só transação: `skill_definitions(legacy_flow_id)`; v1
  `published` com o plano copiado (`schema_version 0`, `source_kind='legacy_flow'`); v2 `draft` com a edição, gerada
  por um descompilador `Plan → DSL` que mantém `node_id = key` para as receitas continuarem casando; e
  `UPDATE flows SET status='disabled'`. Desfazer é religar o fluxo e desabilitar a versão, também numa transação.
  **Feito na J** (merge `5b1957f`; [como ficou](#fases-h-i-j-e-k)): desfazer também apaga os rascunhos da conversão
  ainda em `draft`, e as capacidades do perfil ganharam a lista `skills` à parte (em vez de `trained` incluir as
  skills, como a §15.3 previa).
- **`FlowStore` nunca escreve em tabela de skill.** O `SqlSkillRepository` só toca em `flows` naquele `UPDATE` de
  status da conversão e do desfazer.
- **Aprendizado por execução não duplica comando:** `_learn_flow` pula execução com `runs.skill_id` (fase G), e
  `learn_from_run` pula quando uma skill publicada tem o mesmo `match_key`, o que acontece quando a execução caiu no
  planejador por estar fora do escopo da skill.
- **A trilha antiga se lê sem migração:** `runs.flow_id` preenchido e `runs.skill_id` nulo querem dizer
  `flow:<flow_id>@1`. O `skill_hash` nulo quer dizer "não se sabe", e nunca é inventado.

### 15.3 Rotas e contratos

**Preservadas, com o mesmo contrato:** `/api/flows` (`GET`, `/cobertura`, `/match`, `PUT`, `DELETE`;
`api.py:508-551`), `/api/recipes` (`api.py:554-571`), as sete rotas de treino (`api.py:357-425`), `/api/capabilities`,
`/api/app-catalog`, `/api/instagram/profiles/{id}/capacidades`, `/api/apps-overview`, `POST /api/runs` com o 409
`preflight`, e `/api/runs/*`. Quem as trava: `test_modo_treinamento.py:103-197`,
`app.integration.test.tsx:69-72, 228-238, 479-604`, `TrainingReview.test.tsx:56-69` e `test_cobertura_de_rotas.py`.

**Aditivas** (fase F em diante): `/api/skills` (lista, detalhe, versão, `status`, `scope`, `rollback`),
`/api/skills/resolve`, `/api/teaching-sessions` (demonstrações, anexar gravação v1, respostas, candidatas,
descartar) e `/api/skill-candidates/{id}` (ler, `PATCH` enquanto `draft`, `compile`, `validate` `static`|`device`,
`publish`). Criar candidata, validar e publicar recebem `idempotency_key`. **Publicar escreve só nas tabelas de
skill.**

**Cuidados:**

- O serviço novo se chama `state.skill_registry`: `state.skills` já é `TrainingSkills` (`state.py:310`, usado em
  `api.py:402, 413`).
- `Health.features.skills` fica na PARTE 1 dos tipos (`frontend/src/api/types.ts:1-7`), então o contrato
  (`api-contract.md`) muda antes. `RunCreate.skill` só entra depois que o backend implantado o aceitar
  (`extra="forbid"`).
- Eventos novos: `teaching.updated` e `skill.published`; os de demonstração mantêm `data.training_session_id`, que a
  `TrainingBar` já usa para recarregar. Nenhuma `View` nova: `TrainingBar`, `TrainingReview` e `FlowsRecipesSection`
  ganham o caminho novo atrás de `features.skills`, e `capacidades.trained` (`social/capacidades.py:155-158`) passa a
  incluir as skills pelo registro.

---

## 16. MIGRATION MAP

| Símbolo atual | Destino | Fase |
|---|---|---|
| `api._apps_changed:643`, `_despachar_trabalho:1697`, `_despachar:2135`, `executar_envelope:2168`, `pedir_ciclo_de_vida:2282`, `remediar_reiniciando:2415`, `_tratar_mensagem_do_worker:3411`, o resto de `api.py:1640-2417` (menos as rotas `bulk_action:2093` e `instance_action:2236`), `_entregar_cancelamento:2461` e `3393-3544` | `commands/despacho.py`, movido literal; `api.py` reexporta; `AppState` só sob `TYPE_CHECKING` | A |
| `api.err:98`, usado pelo despacho | cópia em `despacho.py` (exceção D4) → `DespachoRecusado` traduzido na borda | A → K |
| SQL de `apps` (`api.py:599-636`, `state.py:1612`, `vitrine.py:316-339`), `app_dto:116`, `apps_list:131` | `modules/applications/infrastructure/sql_app_repository.py` (`AppRepository`) | A |
| `vitrine.novo_id_de_app:316` | `util.py` (kernel), com reexport; some a aresta `devices.proxy → vitrine` | A |
| `workers/protocol.py` | `contracts/worker/protocol.py`; o antigo vira shim com `__all__` | B |
| vocabulário de `devices/verbs.py:16-63`; `registry.METRICAS_DO_AGENTE`, `MAX_CONTAGEM_POR_BATIDA` e os códigos de `Refused` | `contracts/worker/verbos.py`, `vocabulario.py` | B |
| `conectividade.comando_sonda`, `ler_sonda`, `HOST_DE_TESTE` | `devices/sonda_rede.py` (stdlib pura), com reexport; `adb.py` importa no topo | B |
| listas de `worker-install.ps1:60-61`/`.sh:51-52` | `backend/worker-package.txt` | B |
| `planning.capabilities.Capability` | `modules/capabilities/domain.CapabilityDefinition`, por adaptador; o legado continua sendo a fonte | C |
| portas sem tipo do `Scheduler` (`:105-142`) e do `StepExecutor` (`:154-168`) | `Protocol`s em `modules/execution/application/ports.py` | C |
| `taskqueue/flows.FlowStore` | embrulhado pelo `LegacyFlowAdapter` | D |
| `CapabilityNode`, `CapabilityCatalog.build_step` | reusados pelo lowering do compilador | E |
| `planning/training._TrainOut` | `CandidateEnvelope` (o documento v1alpha1 + anotações) | F |
| `executor._safe_target:1358`, privado e importado de fora | função pública em `modules/skills/domain` | F |
| `LOCAL_PROOFS`, `local_proof_holds` | conjunção `&` no `selector:` (feita em `8a2fca5`) | G0 |
| `RunService._plan` → `flows.match` | `SkillRegistry.resolve` + `SkillCompiler` | G |
| `Scheduler._learn_flow`, `FlowStore.learn_from_run` | guarda para execução de skill | G |
| cadeia receita/IA de `run_step` | `RecipeExecutionStrategy`, `AiActorStrategy`; `attempts.strategy` | G |
| portas de app e sessão do `_tick` (`_portas_do_app`, `scheduler.py:339`) | `ResourceProvider`s (§11). **Feito em parte** (H, merges `1e69d02` e `2fc09b2`): os quatro providers leem, planejam e aplicam pelo `CommandBus` (`modules/execution/infrastructure/command_bus.py`); as portas do `_tick` **não** foram substituídas, porque nada chama o `apply` | H |
| `FlowStore._extract` | `ParameterExtractor` com tipos | I |
| `TrainingSkills.save` → fluxo | conversão, descompilador, rota v1 para v2 com o flag. **Feito, com desvio** (J, merge `5b1957f`): o descompilador, converter/desfazer e a rota v1 → v2 existem atrás de `skills.enabled`; `save` **continua gravando fluxo** (`FlowStore.learn_from_plan`), agora recusando comando que uma habilidade publicada já tem. Converter é uma ação à parte, por fluxo | J |
| cluster Applications do `AppState` (`state.py:450-1260`) + `vitrine` | `modules/applications/application/convergencia.py` | K |
| portões `_policy_gate`…`_approval_gate` (`state.py:1345-1606`) | `modules/execution/application/gates.py` | K |
| cluster Identity (`state.py:609-845`), comparações com `"instagram"` | `modules/identity` + registro de `SessionProvider`. **Feito em parte** (K1, merge `f06e34a` e `3fbe9df`): nenhuma comparação com `"instagram"` no núcleo; porta `SessionProvider`, `SessionProviders` por pacote e `session_rules.py` em `modules/identity`; manifesto de app em `modules/applications`. O resto do cluster (porta de sessão, reobservação, localidade) **continua em `state.py`**, agora perguntando ao registro | K |
| `AppState.health`, laços e retenção (`state.py:1691-2198`) | platform (`saude.py`) | K |
| `AppState.__init__`, `start`, `stop` | `bootstrap/` | K |
| `Limiter` (`manager.py:189`), `VagasDeIA` | `modules/execution` | K |
| desbravador (`scheduler.py:446-600`) | `modules/skills` + `modules/execution` | K |
| interações e efeitos de `SocialService` (`:319-606`) | `EffectsLedger` em identity | K |
| `models.py` | fatiado por contexto, com reexport (primeiro os corpos, depois os DTOs de infra, por último os de domínio). **Primeira fatia feita** (K2, `e7af6f0`): 29 dos 41 corpos em `modules/{fleet,identity,execution,applications}/presentation/schemas.py`, reexportados como os mesmos objetos; 12 ficaram, com motivo no docstring de `models.py` | K |
| rotas de `api.py` | `modules/*/presentation` (um router por contexto) | K |
| `set_run_status`, `set_objective` | `RUN_TRANSITIONS`, `OBJECTIVE_TRANSITIONS` (primeiro só conferir e registrar, depois impor). **Conferir e registrar feito** (K2, `48e76ae`): as tabelas em `modules/execution/domain/states.py`, mais `ATTEMPT_TRANSITIONS` e a de etapa; `Repository._conferir` avisa sem bloquear. Impor: pendente (ADR-038) | K |
| `planning/*provider*`, `routing`, `prompts`, `parsing`, `costs` | `adapters/ai` | K |
| `devices/{adb,sdk,avd,emulator,prontidao,perfis}` | `adapters/android`, com alias em `sys.modules` (opcional) | K |
| identidade de receita por texto | identidade por capability | K |

---

## 17. RISKS

| Risco | Mitigação |
|---|---|
| Mover o despacho quebra 6 monkeypatches (`test_supervisao_do_central.py:270, 331, 344`; `test_saude_do_convidado.py:231, 314, 355`) e 21 imports nomeados de `app.api` nos testes | reexport em `api.py`; os patches mudam de alvo **no mesmo commit** |
| Shim que redefine classe faz o `isinstance` de `_tratar_mensagem_do_worker` falhar em silêncio | teste de identidade (`is`) para cada nome de `__all__` |
| K-034 de novo: arquivo fora do instalador passa na suíte e quebra só no agente | manifesto único; teste da cópia instalada em subprocesso |
| `agent_outdated` acende a cada commit (comparação exata, `workers/registry.py:863-876`) | cada entrega diz se o esquema mudou; `Hello.contract_hash` na fase K |
| Migração que falha impede o backend de subir (`state.py:170`) | ensaio em cópia (ADR-020); a 046 só entra depois de um `workflow_dispatch` verde em PostgreSQL |
| `content_hash` não canônico (`db.dumps` sem `sort_keys`, `db.py:545`) | serializador canônico próprio + teste |
| Duas fontes de verdade (skill × fluxo) | um registro; o fluxo é desligado na mesma transação; publicar recusa duplicado; guardas no aprendizado (§15.2) |
| Receitas de fluxo adotado viram "ausente" e a IA reaprende, com custo | `node_id = PlanStep.key` estável; a conversão preserva as chaves |
| Colisão de nome em `steps.capability` | a coluna mantém o sentido (ação do catálogo, chave de política); conceito novo ganha outro nome |
| A prova local de `OPEN_THREAD` passa na caixa de entrada | a correção vem antes da fatia (G0), com teste negativo |
| Tempo do CI (≈5 min de folga) | ≥ 80% de teste puro; mypy em job próprio; `--durations=25` |
| IA paga no ensino | botão explícito com aviso de custo, `idempotency_key`, contagem em `add_usage`; nada de bateria sem autorização |
| Validar em aparelho é mundo real | `not_run` sem autorização; caso `device` exige `proof=real` |
| Deploy fora de ordem (`RunCreate` e `TrainingStartBody` são `extra="forbid"`) | `features.skills`; backend antes do painel |
| Fixture cross-app: `FakeInstagram` incompleto; registrar o catálogo do QA muda `_policy_gate` e `_mistura_de_apps` | `FakeAparelhoMultiApp`; `register()` só dentro do teste, nunca embutido (decisão 8) |
| O venv do worktree é o de produção (junção) | mypy só por `uvx` ou no CI; nunca `pip install` no `.venv` |
| Ramos paralelos conflitam nas bases das catracas | o conflito é trivial (fica o menor); só o coordenador mexe em `db.py`, `state.py` e `api.py` |
| `flows.id` volta com outro conteúdo depois de um `DELETE` | `runs.skill_hash` é o identificador inequívoco |
| Valor de `ai.flows` na produção desconhecido | P1 decidido: `skills.enabled` próprio, padrão `false` (§19) |

---

## 18. PHASED IMPLEMENTATION PLAN

**Ordem** (decisão 10): **A ∥ B** → **C ∥ D ∥ E**, em pacotes novos → **F → G** → **H, I, J, K**.

Nas fases C, D e E, só o coordenador mexe em `db.py`, `state.py` e `api.py`, para ligar as partes. G não depende de F
tecnicamente, só de C, D e E; se F travar, as duas podem trocar de lugar. Toda fronteira de fase roda a suíte inteira
e o `test_arquitetura` (SQLite; PostgreSQL por `workflow_dispatch`) e integra na `main`. Deploy só com autorização; as
migrações 042–046 exigem ensaio em cópia (ADR-020).

**Métricas das catracas:** medidas no commit base; as das fases seguintes são **esperadas** e se confirmam na medição
do commit.

| Métrica | Base | Após A | Após B | Direção até K |
|---|---|---|---|---|
| `api.py` | 3.544 | ≈ 2.660 | = | só rotas, depois routers |
| `state.py` | 2.198 | ≈ 2.190 | = | vira `bootstrap` + serviços dos contextos |
| outros god modules | §2.1 | = | = | teto por arquivo, só desce |
| imports tardios | 78 | ≈ 70 (saem os 8 → `api`) | ≈ 69 (`adb.py:144`) | sem nenhum "ciclo real" |
| `Any` em anotação | 894 | = | = | desce por pacote; zero em código novo |
| ciclos de topo | 0 | 0 | 0 | 0 |
| ciclos em execução | 2 × 8 | SCC-A some ou encolhe; `api` e `state` saem | = | só a fábrica de IA, contida |
| fecho do agente | 28 | 28 | 27 (sai `app.models`) | sem `config` e `metricas` |

### Fases A a F

| Fase | Entregáveis | Testes | Pronto quando |
|---|---|---|---|
| **A**, rede de proteção e o ciclo real | `backend/tests/test_arquitetura.py` (D1–D16, com autoteste) com as bases de hoje; `commands/despacho.py` movido literal (`state`, `devices/proxy` e `workers/local` passam a importá-lo no topo); `AppRepository` e `novo_id_de_app` em `util`; `--durations=25` nos dois `pytest` do CI | os 6 patches com novo alvo; `test_supervisao_do_central`, `test_saude_do_convidado`, `test_contrato_de_worker`, `test_contrato_http`; `test_app_repository` novo | exceções de D6 vazias; `app.api` e `app.state` fora de todo ciclo; bases baixadas no mesmo commit; suíte inteira verde |
| **B**, contratos do worker (nada muda no fio) | `app/contracts/worker/{protocol,verbos,vocabulario}.py` com shims que preservam a identidade; `backend/worker-package.txt` lido pelo `.ps1` e pelo `.sh` (listas embutidas como fallback para `-Origem` antigo); instalador limpa `app/` antes de copiar; `scripts/deploy.ps1:186` manda rodar `worker-install.ps1`; `devices/sonda_rede.py`; comentários corrigidos (`worker/executor.py:31-33`, `worker-install.ps1:13-14`, `worker-requirements.txt:4`) | `tests/contratos/worker-protocol.v1.json` congelado (marca `18285a7c65c51551` inalterada); identidade `is`; `tests/test_arquitetura_do_agente.py` sobre a cópia instalada, em subprocesso: `import app.worker.agent`/`__main__`, `AndroidCfg().ram_efetiva()`, a sonda, fecho ⊆ manifesto, terceiros ⊆ `worker-requirements.txt`, paridade `.ps1`/`.sh` | esquema e identidade verdes; exceção de D9 fora. Atualizar o agente de campo fica `not_run` até autorização |
| **C**, capabilities | `modules/capabilities/domain/` (`CapabilityDefinition`, `StrategyKind`, `Protocol`s `CapabilityProvider` e `ExecutionStrategy`); `infrastructure/catalog_registry.py` (só lê `planning.catalog`); `Protocol`s das portas atuais em `modules/execution/application/ports.py` | mapeamento 1:1 das 23 capabilities do Instagram; campos sem consumidor sinalizados; mypy estrito | tudo tipado, sem fiação em runtime |
| **D**, domínio de skills + 042–046 | `modules/skills/domain/` (definição, versão, tabela de transições, hash canônico); `SqlSkillRepository`, `LegacyFlowAdapter`, `application/registry.py`; migrações 042–045 como arquivos, com as mudanças da §19; a 046 separada | atualização 041 → 046 no molde de `test_db.py` (`_copia_das_migracoes`); esquema igual em banco novo e atualizado; índices recusando duas publicadas e o mesmo `match_key`; gatilho da 046 nos dois dialetos; transições S×S; golden `flow:<id>@1`; varredura de segredo nas tabelas novas; `migrate_data` | dois dialetos verdes e `divergencias() == []`. Deploy só depois do ensaio e com autorização |
| **E**, DSL e compilador | `contracts/skills/v1alpha1.py` (JSON Schema exportado como snapshot); `modules/skills/domain/{ir,compiler}.py`; `infrastructure/lowering.py`; `PlanStep.origin` em `models.py` (o coordenador liga) | goldens em `tests/fixtures/dsl/v1alpha1/{validos,invalidos}` com `*.esperado.json`; todo `E_*` com fixture; os quatro testes da invariante (§12.1); determinismo (mesma entrada, mesmo hash) | D15 verde; ≥ 90% dos testes de unidade |
| **F**, ensino v2 — **feito** (merge `6b04164`, `simulated`) | `TeachingService`, `SqlTeachingRepository`; porta `SkillGeneralizer` com `CandidateEnvelope`, primeiro no provedor simulado; rotas de §15.3; `features.skills`; painel atrás do flag | candidata do provedor simulado (`CountingProvider`); laço de perguntas e respostas; credencial recusada na instrução e na resposta; transação de publicação; vitest com o legado como padrão; `test_modo_treinamento.py` intacto | tudo verde em `simulated`. Generalização real é paga: `not_run` sem autorização |

**Fase F, como ficou** (detalhe em [ensino](../teaching.md), [skills](../dominios/skills.md#ensino-v2-fase-f) e
[contrato](../api-contract.md#adendo-v023-27092026--ensino-v2-e-habilidades-no-http)):

- Entregue:
  - domínio: `modules/skills/domain/teaching.py` (sessão, fonte derivada, estados, turnos, candidata) e
    `domain/generalization.py` (da proposta ao envelope `{document, annotations}` e às perguntas);
  - aplicação: `application/teaching.py::TeachingService` e a porta `SkillGeneralizer` em `application/ports.py`;
  - infraestrutura: `infrastructure/sql_teaching_repository.py` sobre a 044 e `infrastructure/secret_screen.py`;
  - HTTP: `presentation/router.py`, com as rotas da §15.3 e 404 `skills_disabled` com o flag desligado;
    `Health.features.skills`;
  - o adaptador do provedor em `training/generalizer.py`;
  - no painel: `TeachingPanel.tsx` na revisão do treino e a lista "Habilidades" em `FlowsRecipesSection.tsx`.
- Testes: `backend/tests/test_ensino_v2.py` (16 testes; `CountingProvider` sobre o simulado), `TrainingReview.test.tsx`
  e `FlowsRecipesSection.test.tsx` com o legado como padrão; `test_modo_treinamento.py` sem mudança.
  - Vitest 480/480, typecheck e build no commit `63507ad`; suíte SQLite 2269/2269 com A–I relatada na integração
    (`simulated`).
  - IA real, PostgreSQL, conferência visual e aparelho: `not_run`.
- **Desvios do plano:**
  - sem `PATCH` da candidata; `validate` só `static`, sem `device`;
  - a sessão v1 não é lida como v2; `app_snapshot` fica sempre nulo;
  - sem o evento `skill.published`: só `teaching.updated`, com `data: {teaching_id}`;
  - `idempotency_key` do `propose` vai num turno `note`, porque a 044 não tem coluna. Em `validate` e `publish`, é
    aceita e não usada: as duas são idempotentes pelo estado;
  - `published` no ensino quer dizer "virou rascunho de `skill_versions`". Nada é publicado sozinho;
  - só instrução gera um nó `model_judged` e a pergunta `etapas:instrucao`, em vez de inventar etapas;
  - a recusa de credencial é mais rígida que a regra de `service.py` citada na §13.1: palavra com cara de senha e
    número de 6 a 8 dígitos; URLs isentas;
  - `GET /api/skills` lista só o SQL, sem as versões `flow:<id>@1`;
  - as anotações seguem o código (`parameters`, `preconditions`, `postconditions`, `suggested_proofs`, `effects`,
    `risks`), não o `parameter_examples` da §13.2.
- **Decisões da fase:**
  - o adaptador do provedor mora em `app/training`, fora de `modules/skills`, por causa da D15;
  - `test_arquitetura` libera FastAPI e Starlette em `app.modules.*.presentation` (`APRESENTACAO`); banco, IA e
    aparelho continuam nos adaptadores;
  - o roteador é registrado depois do router principal, para `POST /api/skills/resolve` casar antes de
    `/api/skills/{skill_id}`.
- Depois do merge, `578fe36`: `POST /api/skills/resolve` passou a responder 404 `skills_disabled`, coerente com o
  ensino (era 409), e 409 `content_tampered` para versão adulterada entre as candidatas.

### Fase G — fatia vertical: "abrir conversa no Instagram"

- **G0 (feito, `8a2fca5`).** A gramática `selector:` ganhou a conjunção `&`: vários seletores na mesma tela, cada um
  no seu elemento, separados antes de resolver os bindings. `OPEN_THREAD` passou a
  `selector:text=={username}&id=row_thread_composer_edittext` (id lido das telas reais julgadas em 24–25/09).
  - A forma proposta antes (`selector_with_input:`, "o seletor casa e há algum campo editável") foi descartada: a
    caixa de entrada tem o campo de busca, que também é editável, e a prova passaria nela.
  - Testes em `tests/test_cost_levers.py`: caixa de entrada com a linha do usuário e a busca dá `False`; conversa
    com o compositor dá `True`; conversa de outra pessoa dá `False`; `&` dentro de um binding não vira operador.
- **G1.** `FakeInstagram` ganha as telas de caixa de entrada e de conversa e os 5 métodos do `DeviceIO`. O `Harness`
  aceita `factory=`. Um teste confere, por `inspect.signature`, que cada dublê cumpre o seu `Protocol`.
- **G2.** Fiação (coordenador): `_plan` passa pelo registro e grava a trilha da 045; `_insert_steps` copia `origin`;
  o executor grava `attempts.strategy` e `recipe_id`; `_ai` passa `attempt_id`; entram as guardas de `_learn_flow` e
  `learn_from_run`.
- **G2, guardas apontadas pela fase D (27/09):**
  - `PUT /api/flows/{id}` pode religar um fluxo adotado, e aí o mesmo comando fica vivo nos dois lugares. A rota
    deve recusar enquanto a skill daquele fluxo estiver publicada.
  - Adotar um fluxo com `skills.enabled` desligado deixa o comando sem resolução. A adoção fica bloqueada com o flag
    desligado.
  - Quando `GET /api/flows/match` passar pelo registro, ele começa a respeitar `ai.flows`, que hoje ignora. É mudança
    de comportamento visível no painel e vai no CHANGELOG.
  - Composição: `CompositeSkillRegistry(SqlSkillRepository(db, validator), LegacyFlowAdapter(db),
    skills_enabled=…, flows_enabled=…)`. Plano de `schema_version` 0 vai por `legacy_plan(resolved)`.
- **G3.** As skills `ig.abrir_conversa` e `ig.ler_conversa` são publicadas no teste pelo repositório. O
  `CatalogCapabilityProvider` tem `verify` = `local_proof_holds`/`_verify`. **Nenhum provider toca aparelho.**
- **Testes** (`simulated`, harness + `FakeInstagram`, `ai.recipes=replay`): `CountingProvider.count("plan") == 0` e
  `planner.provider == "skill"`; colunas da 045 preenchidas; na primeira execução, `attempts.strategy == "ai_actor"` e
  a receita aprendida; na segunda, `"recipe"` e zero `decide`; `OPEN_THREAD` comprovado pela prova local; na composta,
  `ler` depende de `abrir_abrir_conversa`, e `_alvo_da_conversa` acha o `username`.
- **Pronto quando** tudo estiver verde em `simulated`. A prova real numa conta do Instagram fica `not_run` até
  autorização.

### Fases H, I, J e K

| Fase | Entregáveis | Testes | Pronto quando |
|---|---|---|---|
| **H**, recursos — **feito, sem ligação no `_tick`** (merges `1e69d02` e `2fc09b2`, `simulated`) | os 4 `ResourceProvider`s (§11): primeiro a leitura, usada pelo `PlanReport`; depois o apply, formalizando as portas atuais sem mudar a ordem | idempotência (a segunda passada não gera ação); `uncertain` só fecha com prova; reconciliação só pelo hospedeiro | `PlanReport` servido em `mode=plan` |
| **I**, intenção — **feito** (merge `21b1fff`, `simulated`) | `IntentResolver` + `ParameterExtractor` com a semântica de `_extract` e tipos; ambiguidade vira `MissingInfo`/pergunta; `/api/skills/resolve` com `gated_by_config`; o modo semântico, por IA, vem depois e com aviso de custo | tabela golden de frases; `count("plan") == 0` quando casa; escopo por perfil e grupo | os três chamadores de `FlowStore.match` coerentes |
| **J**, fluxos legados — **feito** (merge `5b1957f`, `simulated`) | conversão (§15.2), descompilador, rota v1 → v2 com o flag, desfazer | bateria `["legado", "novo"]`: mesmo plano, mesmas receitas, mesma contagem de IA | nenhum fluxo ativo e skill publicada com o mesmo comando |
| **K**, god modules — **K2 e K1 feitas** (merges `b56e06c` e `f06e34a`, `simulated`); o resto, não | a §16 a partir de "cluster Applications"; fases 3–6 do contrato do worker (envelope modelado, `RuntimeAndroid` sem `config`, `adapters/android`, `Hello.contract_hash`); identidade de receita por capability | por extração, os testes do módulo movido; o teto de linhas desce | `api.py` só com rotas; `state.py` só com composição; nenhuma comparação com `"instagram"` fora de `integrations/`, do catálogo e de `config.py` (este critério, cumprido na K1) |

**Fase I, como ficou** (detalhe em [skills](../dominios/skills.md#resolução-de-intenção),
[DSL](../skill-dsl.md#tipos-extração-e-normalização-fase-i) e
[contrato](../api-contract.md#adendo-v022-27092026--resolução-de-intenção-post-apiskillsresolve-e-a-pergunta-em-needs_input)):

- Entregue: `modules/skills/application/intent_resolver.py::IntentResolver` com quatro etapas (modelos → tipos →
  semântica → LLM); `domain/intent.py` com os VOs e a normalização pura dos sete tipos; `candidates()` no registro e
  nos dois backends; `POST /api/skills/resolve` (404 `skills_disabled` com o flag desligado desde `578fe36`, antes
  409; sem efeito); a pergunta em `needs_input` com `runs.plan` nulo.
- Testes: `backend/tests/test_intencao_dominio.py` (52 casos de tipo), `test_intencao_resolucao.py` (22 frases e a
  paridade com `FlowStore.match` em 15 casos) e `test_intencao_chamadores.py` (harness na porta 5640). Suíte SQLite
  2252/2252 no branch da fase (`simulated`). PostgreSQL e prova real: `not_run`.
- **Desvios do plano:**
  - as etapas 3 (semântica) e 4 (desempate por LLM) existem só como portas (`intent_ports.py`), com o provedor nulo:
    não chamam IA, e a trilha registra `not_run`. Não há aviso de custo porque não há custo;
  - o critério "três chamadores coerentes" foi provado com quatro: `_plan`, `GET /api/flows/match`,
    `RunService.apps_exigidos` e a rota nova, para a mesma frase
    (`test_intencao_chamadores.py::test_os_tres_chamadores_coerentes_para_a_mesma_frase`);
  - `FlowStore` não passou a delegar para `domain/matching.py`: a cópia continua, e a paridade é conferida por
    tabela;
  - `spec.invocation.examples` continua sem consumidor: não viraram casos do `IntentResolver`.
- **Decisões da fase:**
  - o fluxo legado não ganhou tipo, desempate nem pergunta: a ordem por uso fica, pela paridade com
    `FlowStore.match`;
  - com `skills.enabled` ligado, o empate entre skills, antes decidido pelo menor `skill_id`, vira pergunta.
    `CompositeSkillRegistry.resolve` ficou como a precedência crua, sem uso na execução;
  - valor que não serve ao tipo não cai para o fluxo nem para o planejador: vira pergunta.

**Fase H, como ficou** (detalhe em [execution](../dominios/execution.md#recursos-aplicar-verificar-e-reconciliar-fase-h-parte-2),
[ADR-035](../decisoes.md#adr-035--resourcespec-declarativo) e
[contrato](../api-contract.md#adendo-v024-27092026--plan_report-em-modeplan-e-corpos-fora-de-modelspy)):

- Parte 1 (merge `1e69d02`): tipos, `diff`/`plan` puros, leitura dos quatro providers e o `PlanReport` puro.
- Parte 2 (`9f76832`, `80d5fc7`, `373d45f`; merge `2fc09b2`):
  - `shared/commands.py` (`CommandBus`, `CommandRef`, `RunRef`, `CommandStatus`, a chave
    `res:<aparelho>:<tipo>:<alvo>:<verbo>#n`) e `shared/convergence.py` (`apply_action`, `verify_resource`,
    `reconcile_resource`);
  - `ResourceProvider` como `Protocol` em `modules/execution/application/ports.py`; `apply`, `verify` e `reconcile`
    nos quatro providers; `DespachoCommandBus` e `resource_providers` em `modules/execution/infrastructure`;
    `ResourceConvergence` (`apply_next`: uma linha por aparelho, na ordem das portas);
  - `commands/despacho.py`: `idempotency_key` opcional em `pedir_ciclo_de_vida` e os invólucros públicos
    `pedir_trabalho_de_app` e `conferir_release_para`, sem mudar quem já chamava;
  - `mode=plan` devolve `plan_report` (`RunService.relatorio_de_recursos`); `_plan` grava `objectives.resource_plan`
    (`_fotografar_recursos`); `RunPlan.resources` com os recursos das filhas compostas.
- Testes: `backend/tests/test_aplicacao_de_recursos.py` (20) e `test_plan_report_na_execucao.py` (4), harness na porta
  5640. Suíte SQLite 2293/2293 no branch da parte 2 (`simulated`). PostgreSQL (em especial a consulta com
  `substr(…, CAST(? AS INTEGER))`), aparelhos reais, a refoto no despacho e a ligação no `_tick`: `not_run`.
- **Desvios do plano:**
  - "formalizando as portas atuais" ficou pela metade, de propósito: o `apply` existe, mas **nada o chama**. Ligar o
    `_tick` muda quem dispara, e isso é decisão do dono. O comportamento em execução não mudou;
  - o `CommandBus` mora no kernel (`shared/commands.py`), e não em `modules/execution/application/ports.py` como a
    §7 lista: lá, os providers de fleet, applications e identity importariam execução, que já depende deles. `ports.py`
    o reexporta;
  - a porta `ResourceProvider` difere da §7 em três pontos: recebe o `ResourceSpec`, `apply` e `reconcile` são
    síncronos, e `reconcile` devolve `ReconcileOutcome`;
  - o `plan_report` traz só a parte dos recursos da §14.2; os campos de etapas, efeitos, aprovações e custo ficam para
    depois;
  - falha ao montar o relatório vira `source: "error"` na resposta, com a execução criada, em vez de 500 (`373d45f`);
  - `resource_plan` é gravado no `materialize`, mas não de novo no despacho (§11).

**Fase K2, como ficou** (detalhe em [execution](../dominios/execution.md#máquinas-de-estado-fase-k2),
[arquitetura](../arquitetura.md#módulos-backendapp) e
[ADR-038](../decisoes.md#adr-038--máquinas-de-estado-de-execução-formais-conferir-antes-de-impor)):

- Corpos de requisição (`e7af6f0`): 29 dos 41 movidos literalmente para
  `modules/<ctx>/presentation/schemas.py` (fleet 9, identity 9, execution 3, applications 8), com o vocabulário que só
  eles usavam. `app.models` reexporta os mesmos objetos (`is`), e `api.py` não mudou. `models.py`: 1.836 → 1.554
  linhas, 126 → 97 classes. O OpenAPI (127 rotas, 59 esquemas) e o esquema JSON de cada classe ficaram idênticos,
  por conferência com script relatada pelo coordenador (o script não está no Git).
  `backend/tests/test_models_fatiado.py` (67 testes).
- Máquinas de estado (`48e76ae`): `modules/execution/domain/states.py` com as tabelas de execução (10 estados),
  objetivo (7), etapa (a antiga, que continua imposta; `taskqueue/states.py` passa a derivá-la do domínio) e
  tentativa (6), e `MaquinaDeEstados.pode(de, para)`. `Repository._conferir` avisa (`log` `warn`) e conta fora da
  tabela, sem bloquear; o fixture automático de `tests/conftest.py` reprova o teste que produzir uma.
  `backend/tests/test_maquinas_de_estado.py` (15 testes).
- Medição (`simulated`): 3.585 transições reais em 55 pares na suíte inteira; dois pares fora da tabela, ambos atalho
  de teste, corrigidos para o caminho real.
- **Desvios do plano:**
  - ficaram 12 corpos em `models.py`: dependem de tipo de domínio que ainda mora lá, o domínio também os importa, ou o
    contexto não tem apresentação própria (motivo por nome no docstring de `models.py`);
  - só "conferir e registrar": impor fica para depois de um ciclo sem aviso na produção;
  - a contagem de transições fora da tabela não aparece em `/api/health`;
  - além de `set_run_status` e `set_objective` (a linha da §16), entraram a tentativa (`finish_attempt`) e a escrita
    direta de `skipped` em `revise_plan`.

**Fase J, como ficou** (detalhe em [skills](../dominios/skills.md#conversão-de-fluxo-fase-j),
[runtime](../skill-runtime.md#descompilador-plan--documento-fase-j),
[ADR-037](../decisoes.md#adr-037--compatibilidade-com-o-flow-legado) e
[contrato](../api-contract.md#adendo-v025-27092026--conversão-de-fluxo-em-habilidade-e-provedor-de-sessão-por-app)):

- Entregue:
  - `modules/skills/infrastructure/decompiler.py` (`9d2b736`): `Plan` → `automation/v1alpha1` com `node_id = key`,
    conferido pelo compilador real da execução nas duas compilações; códigos próprios `E_ROUNDTRIP`, `W_ROUNDTRIP`,
    `E_RUNTIME_VARIABLE` e `E_UNREPRESENTABLE`;
  - `infrastructure/flow_conversion.py::FlowConverter` e `SqlSkillRepository.convert_flow`/`undo_conversion`
    (`4ddba1a`): converter (v1 publicada + v2 rascunho + fluxo desligado) e desfazer, cada um numa transação; as
    rotas `POST /api/flows/{id}/adopt` (201; 422 `invalid_document`), `/release` e
    `POST /api/skills/{id}/versions/{n}/decompile`; `legacy_flow_id` em `GET /api/skills`;
  - o critério "um comando, um dono" fechado nos dois caminhos que o permitiam: `learn_from_plan` (treino) e
    `PUT /api/flows/{id}` (409 `command_published`);
  - a trilha da v1 adotada (`fa21cec`): `runs.flow_id` e `flows.used` além da skill (`RunPlan.flow_id`); a lista
    `skills` em `capacidades_do_perfil`;
  - o painel (`c4f40d6`): converter e desfazer por fluxo, as transições de versão com a recusa do domínio na linha, e
    o texto do `TeachingPanel` ajustado.
- Testes: `backend/tests/test_descompilador.py`, `test_conversao_de_fluxo.py` e `test_equivalencia_fluxo_skill.py`
  (`[legado|novo]`: mesmo plano, mesmas receitas, IA plan 0/decide 4/verify 1 e decide 0 na 2ª; e receitas do fluxo
  servindo à habilidade convertida); no painel, `FlowsRecipesSection.test.tsx`. Suíte SQLite 2306 no branch da fase
  (`simulated`, relatado pelo coordenador). PostgreSQL, fluxos reais de produção e conferência visual: `not_run`.
- **Desvios do plano:**
  - `TrainingSkills.save` continua gravando fluxo (a linha da §16): a conversão é uma ação à parte, por fluxo;
  - "conversão dos fluxos ativos" ficou como ferramenta, não como migração: nenhum fluxo de produção foi convertido,
    e o formato real dos planos de produção não foi medido.
- **Decisão da fase:** a v1 adotada grava a skill **e** o fluxo; a v2 grava só a skill.
- **Riscos:** fluxo com argumento literal e texto em modelo é recusado (`learn_from_run` não templatiza `bindings`;
  ler `flows.plan` antes de converter em lote); fluxos do QA não convertem (`{account_label}`); depois de desfazer,
  `DELETE /api/flows/{id}` continua 409; reconverter reusa o número do rascunho apagado; no PostgreSQL, duas escritas
  concorrentes podem passar a guarda de comando único.

**Fase K1, como ficou** (detalhe em [apps](../dominios/apps-e-loja.md#manifesto-de-app-fase-k1),
[perfis](../dominios/perfis-e-instagram.md#sessionprovider-e-o-registro-por-pacote-fase-k1) e
[ADR-039](../decisoes.md#adr-039--manifesto-de-app-e-registro-de-sessionprovider)):

- Entregue:
  - registro de apps movido para `modules/applications/infrastructure/registry.py` (`0b7950e`, só mover; shim em
    `planning/catalog`);
  - regras de sessão do perfil (ADR-029, achado #106) em `modules/identity/application/session_rules.py`
    (`99d851b`);
  - `AppDefinition` (domínio), `AppManifest` com catálogo, `ScreenReader` e fábrica de sessão, o manifesto do
    Instagram, a porta `SessionProvider` e `SessionProviders` por pacote; o núcleo pergunta ao registro (`40def91`);
  - o teste por AST sem comparação com `"instagram"` no núcleo, com a catraca vazia (`01d68b5`);
  - o QA como segundo app, só em teste, e o processo cross-app (`15dfded`);
  - a nota do `import_module` (`88087d9`) e a correção dos recursos da H pelo registro (`3fbe9df`).
- Testes: `backend/tests/test_app_novo_pelo_manifesto.py` (três), `test_apps_fora_do_nucleo.py` (dois, com autoteste)
  e `test_dubles_cumprem_as_portas.py`; os de sessão, desafio e ADR-029 sem mudar asserção. Suíte SQLite 2279 no
  branch da fase (`simulated`, relatado pelo coordenador). PostgreSQL, app real novo e conta real: `not_run`.
- **Desvios do plano:**
  - `classify` fora da porta `SessionProvider`;
  - leitores de tela e fábrica de sessão no manifesto de infraestrutura, não no domínio;
  - um registro de sessão por perfil (um app com login gerenciado por perfil);
  - a checagem "tela contradiz a sessão" vale para qualquer app com provedor (idêntico em produção);
  - 409 novo `no_session_provider` em "Conectar"/"Verificar conta", inalcançável hoje;
  - o cluster Identity inteiro **não** saiu de `state.py`: saíram as comparações e as regras; a porta de sessão, a
    reobservação e a localidade continuam lá, perguntando ao registro.

**O que resta da fase K** (as linhas da §16 ainda não feitas, e o resto da própria K):

- cluster Applications do `AppState` e `vitrine` → `convergencia.py`; portões `_policy_gate`…`_approval_gate` →
  `gates.py`; o resto do cluster Identity; `AppState.health`, laços e retenção → `saude.py`; `__init__`/`start`/`stop`
  → `bootstrap/`; `Limiter` e `VagasDeIA`; o desbravador; `EffectsLedger`; as rotas de `api.py` em routers por
  contexto; `planning/*provider*` → `adapters/ai`; `devices/*` → `adapters/android`;
- `models.py`: os 12 corpos que ficaram, os DTOs de infraestrutura e os de domínio;
- as máquinas de estado: impor (ADR-038);
- identidade de receita por capability;
- as fases 3–6 do contrato do worker;
- o critério de pronto: `api.py` só com rotas e `state.py` só com composição. **Não cumprido.**

---

## 19. Decisões tomadas

As decisões vêm do coordenador, com as alternativas que os relatórios propuseram.

1. **Persistência: um `SkillRegistry`, dois backends.** `SqlSkillRepository` sobre as tabelas 042–046 +
   `LegacyFlowAdapter` só leitura, que expõe `flow:<id>@1`. `flows` não é reescrito; converter desabilita o fluxo na
   mesma transação. As alternativas eram (a) a skill como fluxo versionado, com `flows.version` + `runs.skill_version`
   (relatório 5); (b) publicar gravando uma linha-ponte em `flows` (`source='skill:…'`), com `/training/{sid}/save`
   virando ponte (relatório 7); (c) tabelas novas + adaptador + adopt-on-write (relatório 6). Fica (c): (a) mistura o
   legado mutável com o conteúdo imutável numa tabela com `match_key` UNIQUE, e (b) é escrita dupla, que o próprio
   relatório 7 aponta como seu risco nº 1.
2. **O runtime consome `Plan`/`PlanStep` como hoje.** COMPILE → `Plan` → `materialize` é a única entrada para skill
   nova. `_plan` consulta o registro; `PlanStep.key == node_id`; `steps.capability` continua sendo a ação do
   catálogo; a 045 acrescenta `node_id`, `strategy`, `skill_id` e `skill_version`. A alternativa, um runtime de grafo
   com fila paralela, perderia o índice 018, o lease, a cerca, `recovery_steps` e `promote` (relatório 5, §4.6). A
   igualdade `key == node_id` preserva a identidade de receita (relatório 6).
3. **O conteúdo congela ao sair de `draft`**, e não só ao publicar: senão a validação de `validated` provaria outro
   conteúdo. O ponteiro da publicada é lógico (§10.3). O relatório 7 queria uma coluna-ponteiro; o relatório 6 mostrou
   que ela cria um ciclo de FK que `migrate_data` não resolve.
4. **O compilador é o único produtor de `Plan` para skill nova; a candidata do LLM é dado.** O caminho do planejador,
   em que o LLM emite o plano direto, continua só para comando sem skill. Os testes estão na §12.1.
5. **Fase A = teste de arquitetura com catracas + despacho fora de `api.py` + repositório de `apps`.** Dos cinco
   passos do relatório 1, a tipagem das portas foi para a fase C e o fatiamento de `models.py` para a K. O mypy, que o
   relatório 8 punha na fase 0, é a decisão 9.
6. **Fase B = as fases 0–2 do relatório 4.** O envelope modelado, o runtime sem `config`, a mudança física e o
   `contract_hash` vão para a K.
7. **Fatia G pelo caminho novo, sem provider que toque aparelho.** O relatório 3 sugeria um provider determinístico
   de `OPEN_THREAD` no molde do `InstagramAuthenticator`; ele fica fora porque toca aparelho e não teria prova real sem
   autorização. A correção da prova vem antes (G0), e a composição sempre emite `depends_on`.
8. **Fixture cross-app** (relatórios 3 e 8). O manifesto do QA Messenger entra só por `register()` dentro do teste:
   embutido, mudaria `_policy_gate` e `_mistura_de_apps` para a suíte inteira. `FakeInstagram` completa o `DeviceIO`.
9. **mypy:** a opção B do relatório 8 (`requirements-dev.txt` com `-c requirements.txt`, job próprio, bloqueante só no
   código novo). O pyright foi descartado porque os 559 `# type: ignore[...]` já estão no vocabulário do mypy.
10. **Ordem das fases:** a da §18.

**Conflitos entre relatórios resolvidos aqui:**

- **Tamanho dos god modules.** O relatório 1 diz "14.344 linhas". A soma das suas próprias parcelas, conferida com
  `wc -l`, é **14.944**.
- **`Any`.** São 985 ocorrências textuais (relatório 1, incluindo `dict[str, Any]` fora de anotação) e 894 anotações por
  AST (relatório 8). A catraca usa 894.
- **Fecho do agente.** São 25 módulos por `sys.modules` (relatório 4), 28 pelo AST com imports tardios (relatório 8) e
  14/17 contando só os módulos fora de `app.worker` (relatório 1). É o mesmo fato medido de jeitos diferentes.
- **Camadas.** O relatório 8 usava `modules/<ctx>/{domain,application,ports,adapters}`. Este documento usa
  `{domain,application,infrastructure,presentation}`, com as portas em `application/ports.py`, e os padrões do teste
  AST acompanham.
- **Ligação do ensino à gravação.** O relatório 7 propunha coluna em `training_sessions`; o relatório 6,
  `teaching_demonstrations`. Fica a do relatório 6: nenhum `ALTER` em tabela viva, e o gravador não muda.
- **Vocabulários de ensino e de candidata.** Ficam os da 044 (relatório 6), porque o relatório 7 os tinha diferentes.
- **`fastapi` no despacho × "infra só onde mora".** Fica uma exceção nominal em D4 até a fase K.

**Mudanças propostas nos rascunhos 042–046** (a confirmar pelo coordenador antes de virarem arquivo):

- **045:** `ALTER TABLE ai_calls ADD COLUMN attempt_id TEXT` (aditivo). O relatório 6 deixava `ai_calls` intacta; o
  relatório 5 pedia a coluna. A trilha pedida exige "papel/modelo de IA por tentativa".
- **045:** o comentário de `steps.strategy`/`attempts.strategy` passa a listar o enum da §5 (`deterministic`, `recipe`,
  `app_provider`, `ui_generic`, `ai_actor`, `human`) e a forma de cadeia (`recipe>ai_actor`).
- **044:** `teaching_demonstrations.app_snapshot TEXT` (JSON com versão, variante e assinatura no momento da gravação).
- **044:** o comentário de `teaching_candidates.content` passa a dizer "envelope `{document, annotations}`".

**Decisões pendentes (coordenador ou dono):**

**Decisões do coordenador sobre P1–P5 (27/09):**

- **P1: flag próprio, desligado por padrão.** A `SqlSkillRepository` fica atrás de `skills.enabled` (config, padrão
  `false`), exposto ao painel como `features.skills`. O `LegacyFlowAdapter` continua obedecendo `ai.flows`
  exatamente como hoje. Com os dois desligados, a produção fica idêntica; publicar skill não liga nada sozinho. Não
  reaproveitar `ai.flows`: ele já tem sentido (fluxo legado) e o valor da instalação não foi lido.
- **P2: sim.** `GET /api/flows/match` e `apps_exigidos` passam pelo registro no mesmo commit da G2; senão a estimativa
  do painel e o pré-voo divergem do que a execução faz.
- **P3: sim.** `PlanStep.origin` opcional, aditivo; o painel ignora campo desconhecido.
- **P4: sim.** Caso `device` só conta para `validated` com observação `proof=real`; `simulated` basta para
  `candidate`. Promover sem prova real exige `validated` manual do dono, registrado na transição.
- **P5: sim.** A catraca de linhas é só teto por arquivo; as de `Any`, imports tardios e ciclos seguem estritas nas
  duas direções.

---

## 20. ADRs a registrar

Cada um entra em [decisões](../decisoes.md) quando a fase correspondente é integrada, na ordem de integração:
ADR-030 e ADR-031 entraram com as fases A e B (27/09); ADR-032, ADR-033 e ADR-034, com as fases C, D e E (27/09);
ADR-035 e ADR-036, com as fases H (parte 1) e G (27/09), e o ADR-035 foi atualizado com a H parte 2 (27/09); o
ADR-038 entrou com a K2 (27/09); o ADR-037 entrou com a J e o ADR-039, novo, com a K1 (27/09); o ADR-029 ganhou uma
nota da K1 (as regras mudaram de casa). A referência do que as fases
entregaram, conferida no código, está em [capabilities](../dominios/capabilities.md), [skills](../dominios/skills.md),
[execution](../dominios/execution.md), [DSL](../skill-dsl.md) e [runtime de skills](../skill-runtime.md).

- **ADR-030 (vigente, 27/09) — Monólito modular incremental.** Contextos, camadas e as regras D1–D16 verificadas por AST,
  com catracas. Portas do lado de quem consome; IA como adaptador. Sem microsserviço, sem processo novo, sem rewrite;
  mover antes de editar.
- **ADR-032 (vigente, 27/09) — Capability, Skill e Process.** `CapabilityDefinition` é a operação semântica do app. A
  skill é um grafo versionado de nós que referenciam capabilities ou outras skills. O *como* é a `ExecutionStrategy`,
  separada. `steps.capability` mantém o sentido.
- **ADR-033 (vigente, 27/09) — IR de skill e DSL `automation/v1alpha1`.** O compilador é o único produtor de `Plan`
  para skill nova e baixa para o `Plan` atual. O que vem do LLM é dado, nunca código. Sem `local_proof` e sem política
  por nó na v1alpha1; `depends_on` sempre emitido.
- **ADR-035 (vigente, 27/09; `apply`/`verify`/`reconcile` implementados e não ligados ao `_tick`) — ResourceSpec
  declarativo.** Estado desejado com leitura, `diff` e `plan` sem efeito; os 4 providers iniciais sobre o que já
  existe; `unknown` nunca vira `in_sync`, e a resposta a ele é ler; `PlanReport` puro e servido em `mode=plan`.
  Aplicação só por `commands` (`CommandBus` no kernel), uma vez por chave, `uncertain` nunca repetido, `on_missing`
  decidindo quem dispara, reconciliação só pelo hospedeiro e com prova posterior ao comando. Proposto: ligar no `_tick`.
- **ADR-034 (vigente, 27/09) — Versionamento de skill.** Estados e transições da §10.3; congela ao sair de `draft`;
  ponteiro lógico da publicada; validação por observação registrada (`real` × `simulated`, P4); desligar, nunca
  apagar. Registrou também o registro único com dois backends, a precedência e a adoção na mesma transação, e o flag
  próprio `skills.enabled` (P1), que aqui eram do ADR-037.
- **ADR-031 (vigente, 27/09) — Contratos compartilhados do worker.** `app/contracts/worker`, shim com identidade de objeto,
  manifesto único do instalador e esquema congelado. Regra escrita do que exige subir `PROTOCOL_VERSION`/`PROTOCOL_MIN`,
  para o precedente da cerca obrigatória não se repetir.
- **ADR-036 (vigente na trilha e nas regras, 27/09; `RecipeExecutionStrategy` proposta) — Receitas como estratégia de
  execução.** `attempts.strategy` (cadeia exercida, `recipe>ai_actor`) e `recipe_id` como trilha; `steps.strategy` é o
  planejado e não comanda a ordem; `human` nunca é estratégia exercida; receita nunca repete commit; identidade de
  receita por etapa em forma de modelo até a fase K; seletor com o username sem arroba vira parâmetro (`eb9ba02`).
  Complementa o ADR-007.
- **ADR-037 (vigente, 27/09; conversão em lote dos fluxos de produção proposta) — Compatibilidade com o Flow
  legado.** O registro com dois backends, `flow:<id>@1`, a precedência skill → fluxo → planejador e a adoção na mesma
  transação estão no ADR-034; a guarda de `PUT /api/flows/{id}` (409 `flow_adopted`) entrou na fase G (adendo v0.21).
  A fase J entregou o resto: descompilador `Plan → DSL` conferido pelo compilador real, converter e desfazer numa
  transação, rota v1 → v2, "um comando, um dono" (409 `command_published`; o treino recusa comando publicado) e a
  trilha da v1 adotada (skill **e** fluxo); rotas antigas com o mesmo contrato, mais um 409.
- **ADR-039 (vigente, 27/09; sessão por (perfil, app) proposta) — Manifesto de app e registro de SessionProvider.**
  `AppDefinition` no domínio, `AppManifest` (catálogo, leitura de tela, fábrica de sessão) na infraestrutura,
  `register_manifest` como única entrada; porta `SessionProvider` e `SessionProviders` por pacote em
  `modules/identity`; regras do ADR-029 em `session_rules.py`; nenhuma comparação com `"instagram"` no núcleo,
  travada por AST. O Instagram é a primeira implementação; o QA prova a extensão, só em teste.
- **ADR-038 (vigente na fase "conferir e registrar", 27/09; impor proposto) — Máquinas de estado de execução formais:
  conferir antes de impor.** Tabelas de execução, objetivo, etapa e tentativa no domínio de execução, derivadas do
  comportamento atual (§2.4); fora da tabela, `log` `warn` e contagem sem bloquear; a suíte reprova transição fora da
  tabela; a etapa continua imposta. Impor depois de um ciclo sem aviso na produção, com a contagem visível.

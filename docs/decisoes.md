# Decisões

Registro de decisões de arquitetura e de produto (ADR) — uma por seção, um arquivo só. "Estado" diz se a decisão
continua valendo (**vigente**), foi trocada por outra (**substituída por ADR-x**) ou ainda espera resposta do dono
(**pendente do dono**). As fontes citadas em Evidências são o que comprova a decisão; achados `#n` referem-se a
`docs/auditoria-2026-09-21/`, IDs como `0.1` ou `9.1` referem-se a `docs/plano-100.md`.

Sobre memória e planos citados como evidência: as notas de `C:\Users\Administrator\.claude\projects\C--git-android\
memory\*.md` e os planos de sessão em `C:\Users\Administrator\.claude\plans\*.md` ficam **fora deste repositório**,
na máquina do dono — não são clonáveis por quem só tem o Git. Ver `docs/conhecimento/fontes.md`.

## Índice

| ADR | Título | Estado | Data |
|---|---|---|---|
| [ADR-001](#adr-001--arquitetura-do-parque-distribuído-worker-remota-gerenciada) | Arquitetura do parque distribuído: worker "remota gerenciada" | vigente | 19/09 |
| [ADR-002](#adr-002--canal-do-worker-túnel-reverso-com-listener-dedicado) | Canal do worker: túnel reverso com listener dedicado | vigente | 23/09 |
| [ADR-003](#adr-003--banco-sqlite-por-padrão-postgresql-por-configuração) | Banco: SQLite por padrão, PostgreSQL por configuração | vigente | 17/09 |
| [ADR-004](#adr-004--segundo-backend-real-infraestrutura-pronta-sem-topologia-em-uso-decisão-9) | Segundo backend real: infraestrutura pronta, sem topologia em uso (decisão 9) | pendente do dono | 22/09 |
| [ADR-005](#adr-005--ia-por-função-e-depois-ator-local-como-camada-de-custo) | IA por função (Anthropic) e depois ator local (Ollama) | vigente; ator local substituído por ADR-023 | 17/09, 24/09 |
| [ADR-006](#adr-006--rodízio-de-n-contas-sobre-k-vagas--hibernação-por-snapshot) | Rodízio de N contas sobre K vagas + hibernação por snapshot | vigente | 17/09 |
| [ADR-007](#adr-007--receitas-e-fluxos-a-ia-ensina-uma-vez-o-software-repete) | Receitas e fluxos: a IA ensina uma vez, o software repete | vigente | 17/09, 24/09 |
| [ADR-008](#adr-008--instagram-real-via-play-store-sem-espelho-de-terceiros) | Instagram real via Play Store, sem espelho de terceiros | vigente | 17/09 |
| [ADR-009](#adr-009--desafio-2fa-captcha-e-senha-sempre-pela-pessoa) | Desafio, 2FA, CAPTCHA e senha sempre pela pessoa | substituída em parte por ADR-025 (senha) | 17/09 |
| [ADR-010](#adr-010--comando-distribuído-com-cerca-outbox-e-idempotência) | Comando distribuído com cerca, outbox e idempotência | vigente | 21/09 |
| [ADR-011](#adr-011--configyaml-e-env-fora-do-git-por-instalação) | `config.yaml` e `.env` fora do Git, por instalação | vigente | 23/09 |
| [ADR-012](#adr-012--executor-do-plano-100-workflow-na-sessão-da-ide) | Executor do plano-100: workflow na sessão da IDE, não subprocesso `claude -p` | vigente | 22/09 |
| [ADR-013](#adr-013--fallback-pago-de-recusa-ligado-por-padrão-decisão-3) | Fallback pago de recusa: ligado por padrão (decisão 3) | vigente | 24/09 |
| [ADR-014](#adr-014--loja-remota-área-de-trabalho-remota-até-o-worker-decisão-4) | Loja remota: área de trabalho remota até o worker (decisão 4) | vigente | 24/09 |
| [ADR-015](#adr-015--alvo-de-capacidade-e-limites-por-servidor-decisão-5) | Alvo de capacidade e limites por servidor (decisão 5) | vigente | 24/09 |
| [ADR-016](#adr-016--acesso-de-pessoas-sessão-nominal-sobre-token-único-decisão-8) | Acesso de pessoas: sessão nominal sobre token único (decisão 8) | vigente | 23/09 |
| [ADR-017](#adr-017--chave-do-provedor-de-ia-sem-revogação-decisão-2) | Chave do provedor de IA: sem revogação (decisão 2) | vigente | 24/09 |
| [ADR-018](#adr-018--bateria-de-avaliação-autorizada-na-opção-recomendada-decisão-7) | Bateria de avaliação: autorizada na opção recomendada (decisão 7) | executada | 25/09 |
| [ADR-019](#adr-019--hora-certa-nas-duas-máquinas-decisão-6) | Hora certa nas duas máquinas (decisão 6) | divergência sem veredito | 21/09–23/09 |
| [ADR-020](#adr-020--backup-e-janela-de-reinício-de-produção-antes-de-migrar-decisão-1) | Backup e janela de reinício de produção antes de migrar (decisão 1) | vigente | 21/09 |
| [ADR-021](#adr-021--commit-direto-na-main-sem-pr) | Commit direto na main, sem PR | vigente | 17/09 |
| [ADR-022](#adr-022--exclusões-deliberadas-de-escopo) | Exclusões deliberadas de escopo | vigente | 21/09 |
| [ADR-023](#adr-023--ator-declarado-no-sonnet-modelo-local-fora-do-caminho-principal) | Ator declarado no Sonnet; modelo local fora do caminho principal | vigente | 25/09 |
| [ADR-024](#adr-024--verificador-barato-com-proteções-em-vez-de-trocar-o-modelo-do-verificador) | Verificador barato com proteções, em vez de trocar o modelo do verificador | vigente | 25/09 |
| [ADR-025](#adr-025--a-automação-digita-a-credencial-que-a-pessoa-fornece-com-consentimento) | A automação digita a credencial que a pessoa fornece, com consentimento | substituída em parte por ADR-040 (a credencial é da conta, não da execução) | 26/09 |
| [ADR-026](#adr-026--todos-os-aparelhos-sempre-na-versão-promovida) | Todos os aparelhos sempre na versão promovida | vigente | 26/09 |
| [ADR-027](#adr-027--prévia-e-observação-sob-demanda-medição-agregada) | Prévia e observação sob demanda; medição agregada | vigente | 26/09 |
| [ADR-028](#adr-028--runtimes-executores-e-orquestração-o-que-fica-como-está-e-o-que-reabre) | Runtimes, executores e orquestração: o que fica como está e o que reabre | vigente | 26/09 |
| [ADR-029](#adr-029--desafio-de-segurança-do-instagram-bloqueia-o-perfil-sozinho) | Desafio de segurança do Instagram bloqueia o perfil sozinho | vigente; substituída em parte por ADR-055 (só a conta travada bloqueia, o código de login pede pessoa, e o bloqueio vale também fora da entrada do estado) | 27/09 |
| [ADR-030](#adr-030--monólito-modular-incremental-com-regras-de-dependência-verificadas) | Monólito modular incremental, com regras de dependência verificadas | vigente | 27/09 |
| [ADR-031](#adr-031--contratos-compartilhados-do-worker-e-manifesto-único-do-agente) | Contratos compartilhados do worker e manifesto único do agente | vigente | 27/09 |
| [ADR-032](#adr-032--capability-skill-e-process) | Capability, Skill e Process | vigente | 27/09 |
| [ADR-033](#adr-033--ir-de-skill-e-dsl-automationv1alpha1) | IR de skill e DSL `automation/v1alpha1` | vigente | 27/09 |
| [ADR-034](#adr-034--versionamento-de-skill) | Versionamento de skill | vigente | 27/09 |
| [ADR-035](#adr-035--resourcespec-declarativo) | `ResourceSpec` declarativo | vigente; `apply`/`verify`/`reconcile` implementados e não ligados ao `_tick` | 27/09 |
| [ADR-036](#adr-036--receitas-como-estratégia-de-execução) | Receitas como estratégia de execução | vigente (trilha e regras); `RecipeExecutionStrategy` proposta | 27/09 |
| [ADR-037](#adr-037--compatibilidade-com-o-flow-legado) | Compatibilidade com o `Flow` legado | vigente; conversão em lote dos fluxos de produção proposta | 27/09 |
| [ADR-038](#adr-038--máquinas-de-estado-de-execução-formais-conferir-antes-de-impor) | Máquinas de estado de execução formais: conferir antes de impor | vigente (conferir e registrar); impor proposto | 27/09 |
| [ADR-039](#adr-039--manifesto-de-app-e-registro-de-sessionprovider) | Manifesto de app e registro de `SessionProvider` | vigente; a sessão por (perfil, app) é `account_sessions` (ADR-040) | 27/09 |
| [ADR-040](#adr-040--a-credencial-pertence-à-conta-da-persona-e-a-execução-não-carrega-credencial) | A credencial pertence à conta da persona e a execução não carrega credencial | vigente, implantado em 28/09; substitui em parte o ADR-025 | 27/09 |
| [ADR-041](#adr-041--a-persona-é-a-pessoa-instagram_profiles-como-raiz-personas-dobrada-username-opcional-por-string-vazia-e-reconstrução-com-foreign_keys-off) | A persona é a pessoa: `instagram_profiles` como raiz, `personas` dobrada, `username` opcional por `''` e reconstrução com `@foreign_keys:off` | vigente, implantado em 28/09; crenças substituídas em parte pelo ADR-048 | 27/09 |
| [ADR-042](#adr-042--imagens-de-persona-receita-determinística-porta-imagegenerator-simulado-primeiro-openai-atrás-de-chave-custo-em-ai_callsusd) | Imagens de persona: receita determinística, porta `ImageGenerator`, simulado primeiro, OpenAI atrás de chave, custo em `ai_calls.usd` | vigente, implantado em 28/09 (gerador simulado no ambiente central); provedor real em pesquisa | 27/09 |
| [ADR-043](#adr-043--persona-nn-aparelho-vínculo-por-app-aparelho-principal-e-uma-conta-por-app-em-cada-aparelho) | Persona N:N aparelho: vínculo por app, aparelho principal e uma conta por app em cada aparelho | vigente, implantado em 28/09 | 28/09 |
| [ADR-044](#adr-044--roteamento-das-execuções-por-persona-alvos-resolvidos-destinos-no-texto-e-prévia-obrigatória) | Roteamento das execuções por persona: alvos resolvidos, destinos no texto e prévia obrigatória | vigente, implantado em 28/09 | 28/09 |
| [ADR-045](#adr-045--provisionamento-de-aparelho-pela-plataforma-local-agora-remoto-depois) | Provisionamento de aparelho pela plataforma: local agora, remoto depois | vigente, implantado em 28/09 (AVD real criado e aposentado); remoto proposto | 27/09 |
| [ADR-046](#adr-046--contrato-de-página-e-faixas-por-container-query-foco-em-seções-com-grupos-de-ação-puros) | Contrato de página e faixas por container query; Foco em seções com grupos de ação puros | vigente, implantado em 28/09 | 28/09 |
| [ADR-047](#adr-047--assistente-do-comando-refinar-com-a-ia-e-responder-à-execução-sem-reescrever-o-texto) | Assistente do comando: refinar com a IA e responder à execução sem reescrever o texto | vigente, implantado em 28/09 (`a71e809`) | 28/09 |
| [ADR-048](#adr-048--crenças-ricas-da-persona-vão-ao-modelo-com-regra-de-conduta-biografia-v2) | Crenças ricas da persona vão ao modelo, com regra de conduta (biografia v2) | vigente; substitui em parte o ADR-041 | 28/09 |
| [ADR-049](#adr-049--provedores-de-ia-por-papel-openai-primeiro-gemini-como-braço-de-comparação-e-adoção-só-pela-bateria) | Provedores de IA por papel: OpenAI primeiro, Gemini como braço de comparação e adoção só pela bateria | vigente (código); adoção pendente da medição | 28/09 |
| [ADR-051](#adr-051--saldo-das-contas-de-ia-livro-caixa-com-consumo-dos-relatórios-oficiais-aviso-e-bloqueio) | Saldo das contas de IA: livro-caixa com consumo dos relatórios oficiais, aviso e bloqueio | vigente, implantado e encerrado em 28/09 | 28/09 |
| [ADR-052](#adr-052--conhecimento-de-app-como-dado-zero-python-por-app-motores-genéricos-no-núcleo) | Conhecimento de app como dado: zero Python por app, motores genéricos no núcleo | fatia 1 vigente (código); meta e fatias 2–5 propostas ao dono; revê em parte o ADR-039 | 28/09 |
| [ADR-053](#adr-053--falhas-reiteradas-do-instagram-medir-para-onde-foi-o-tempo-e-não-transformar-lentidão-em-falha) | Falhas reiteradas do Instagram: medir para onde foi o tempo e não transformar lentidão em falha | vigente, implantado em 28/09 (`93967d0`); pendências dos revisores resolvidas em `7a02491` (itens 21.10–21.14) | 28/09 |
| [ADR-054](#adr-054--aprendizado-contínuo-livro-de-aprendizado-com-ciclo-de-vida-publicação-sozinha-só-sem-efeito-externo-d1-feedback-implícito-com-botão-opcional-d2-lições-medidas-e-backlog-do-que-mais-falha) | Aprendizado contínuo: livro com ciclo de vida, D1 (publica sozinho só sem efeito externo), D2 (feedback implícito + botão), lições medidas e backlog do que mais falha | aceito; fundação (A1, migração 055) integrada em `c359f65`, a implantar; A2–A9 pendentes | 29/09 |
| [ADR-055](#adr-055--proteção-de-contas-a-conta-travada-para-sem-ser-tocada-o-aparelho-entra-em-quarentena-uma-conta-por-alvo-e-nenhum-reset-com-conta) | Proteção de contas: a conta travada para sem ser tocada, o aparelho entra em quarentena, uma conta por alvo e nenhum reset com conta | vigente (código, migração 054); integrado em `c359f65`, a implantar; `e9da86e` implantado; substitui em parte o ADR-029; substituída em parte por ADR-056 (a cláusula de rede) | 29/09 |
| [ADR-056](#adr-056--rede-por-aparelho-vpn-dentro-do-android-com-proxy-encadeado-saída-medida-e-revisão-da-cláusula-de-rede-do-adr-055) | Rede por aparelho: VPN dentro do Android com proxy encadeado, saída medida; revisa a cláusula de rede do ADR-055 | vigente (decisão do dono); Fase 25 a implementar; substitui em parte o ADR-055 | 29/09 |
| [ADR-057](#adr-057--outlook-como-primeiro-app-novo-conta-por-app-sessão-por-conta-e-credencial-clonada-no-cofre) | Outlook como primeiro app novo: conta por app, sessão por conta e credencial clonada no cofre | vigente (decisão do dono); Fase 23 a implementar | 29/09 |
| [ADR-058](#adr-058--comando-entre-aplicativos-catálogo-pelo-app-da-etapa-e-valor-lido-entre-etapas) | Comando entre aplicativos: catálogo pelo app da etapa e valor lido entre etapas | proposto (Fase 24) | 29/09 |
| [ADR-059](#adr-059--pedidos-persistentes-pertencem-ao-produto-pedido-ocorrência-e-execução) | Pedidos persistentes pertencem ao produto: pedido, ocorrência e execução | proposto (Fase 26) | 29/09 |

---

## ADR-001 — Arquitetura do parque distribuído: worker "remota gerenciada"

**Data:** 19/09/2026 · **Estado:** vigente

**Contexto.** O servidor central só sustentava 4 emuladores simultâneos por RAM. O projeto já tinha duas abstrações
de aparelho (`emulator` local e `external` — dispositivo alheio, só lido por ADB) e nenhuma das duas serve para um
emulador que existe numa **outra máquina controlada pelo próprio projeto**: `external` nunca liga, desliga nem
hiberna nada.

**Alternativas.**
- Tratar o aparelho remoto como `external` puro — descartada: o rodízio nunca alcançaria as vagas remotas.
- `appium:remoteAdbHost`/`adb -H` (servidor adb rodando no worker) — descartada: muda a topologia de portas e exige
  um adb server por worker; a man page do AOSP confirma que são dois desenhos distintos, não uma variação.
- STF/Selenium Grid como estão (provider nunca cria dispositivo; plano de dados inbound) — descartados como
  precedente direto: o worker daqui precisa **criar** AVD, e a rede do worker pode estar atrás de NAT que o projeto
  não controla.

**Escolha.** Um terceiro tipo de instância — "remota gerenciada": ciclo de vida pelo **agente** do worker (registro,
heartbeat, capacidades declaradas), ADB pelo **túnel** até o servidor adb único do centro, Appium **central** por
padrão (`appium: local` como opt-in por worker). Decisões de entrada do dono: workers mistos (Windows/Linux); redes
remotas só por túnel **iniciado pelo worker**, nunca porta aberta em roteador alheio; o worker fornece dispositivos
+ ADB, nunca credencial de conta; precisa de instalador/agente.

**Consequências.** O central continua único dono de painel, banco, IA e catálogo de APK. `LocalWorker` (fase 2) faz
o central se registrar na própria tabela `workers`, unificando o caminho local e remoto num só contrato — sem isso,
capacidades declaradas e slots por worker teriam dois donos. Custo marginal de somar aparelho ficou quase nulo
(receita aprendida uma vez atravessa a fronteira de máquina sem adaptação, 19/09: 6 de 6 com **zero** chamadas de
IA), mas a capacidade de "trabalhar pesado ao mesmo tempo" é menor que a de "existir ligado" — mesmo modelo de
rodízio N sobre K, agora por máquina.

**Evidências.** `docs/parque-distribuido.md` (arquitetura e medições de 19/09); `backend/app/workers/` (`registry.py`,
`protocol.py`, `portao.py`); `backend/app/workers/local.py` (`LocalWorker`); fase 2 do plano-100 (commit `f30c452`
região — fase 3/4 também tocam isto).

**Relação.** Fases 2, 4 do plano-100; achados #154, #11, #29, #147, #52.

---

## ADR-002 — Canal do worker: túnel reverso com listener dedicado

**Data:** 23/09/2026 · **Estado:** vigente

**Contexto.** A primeira versão do túnel reverso apontava `-R` para a porta do backend (`8000`) inteira. Como toda
conexão que chega por um túnel reverso tem par `127.0.0.1` de verdade, e loopback isentava de credencial, qualquer
processo na máquina do worker (job de CI, usuário local não-administrador) alcançava a API REST inteira do central
sem token — inclusive `POST /api/admin/shutdown` e a troca de credencial de perfil. Comprometer um worker equivalia
a comprometer o central (achado #116).

**Alternativas.**
- Conferir só o endereço do par para decidir a isenção de loopback — descartada: o par **é** `127.0.0.1` também no
  ataque; não distingue nada.
- Manter uma porta só e adicionar autenticação por token na API REST inteira — descartada por ser superfície maior
  que o necessário: o worker só precisa de um canal, não da API inteira.

**Escolha.** Um listener **dedicado** em `server.worker_port` (`127.0.0.1:8010`), que serve só `/api/worker/ws` —
nele não existe rota REST nenhuma; o que chega e não é o WebSocket do worker recebe 404. O `-R` do túnel passou a
apontar para essa porta, não para a `8000`. No worker, a conta de serviço do túnel (`farm-tunel`, item 9.4) ganhou
`permitopen` restrito às portas de ADB e `permitlisten` só para a porta reversa, sem shell (`restrict` +
`command="exit"`).

**Consequências.** Um túnel antigo apontando para `:8000` continua funcionando enquanto o backend velho estiver no
ar e para de alcançar qualquer coisa depois da subida do backend novo — é a tranca, não um efeito colateral. Rodar
`scripts/worker-tunnel.ps1 -Instalar -MapaReverso '18000:8010'` de novo é obrigatório ao atualizar workers antigos.

**Evidências.** `docs/worker.md` (seção "Como o worker alcança o central"); `backend/app/main.py`
(`create_worker_app`); `scripts/worker-ssh-restrito.ps1`; achados #116, #123, #124.

**Relação.** Itens 0.3, 9.4 do plano-100.

---

## ADR-003 — Banco: SQLite por padrão, PostgreSQL por configuração

**Data:** 17/09/2026 (nasce assim) · formalizada 22/09/2026 (fase 5) · **Estado:** vigente

**Contexto.** O projeto nasceu de processo único com SQLite em WAL — certo para quem roda tudo numa máquina. A
separação em vários backends (item de decisão 9) exigiria um banco compartilhado entre processos.

**Alternativas.**
- Migrar definitivamente para PostgreSQL — descartada como obrigatória: quem roda um backend só não ganha nada e
  perde "zero serviço".
- Dois conjuntos de migrações divergentes por banco — descartada: já causou o defeito mais caro da etapa (a 008
  reescrita para Postgres divergiu do schema aplicado em produção, sem detecção).

**Escolha.** `DATABASE_URL` como variável: ausente = SQLite (padrão), presente = PostgreSQL. Três decisões mantêm a
diferença contida em `app/db.py`: linha é sempre um `dict`; marcador de parâmetro continua `?` (traduzido para `%s`
num lugar só); migrações são os **mesmos arquivos**, com marca (`{{PK_AUTO}}`, `{{BLOB}}`) ou bloco
(`-- @dialect:postgres`) para o que difere.

**Consequências.** A suíte passou a rodar contra os dois bancos com `TEST_DATABASE_URL`; um defeito real (12 pontos
em 8 arquivos abrindo `Database(cfg.db_path)` direto, ignorando a variável) mostrou que "381 nos dois bancos" não
provava o que parecia provar. PostgreSQL de produção (item 5.4: reconexão, pool, `/health` olhando o banco,
migração 018 convergindo o esquema divergente) está escrito e testado, mas **nenhum segundo backend real** usa isso
em produção — ver ADR-004.

**Evidências.** `docs/banco.md`; `backend/app/db.py`; achados #33, #34, #162, #169.

**Relação.** Item 5.4; decisão 9 do plano-100 §1 (ver ADR-004).

---

## ADR-004 — Segundo backend real: infraestrutura pronta, sem topologia em uso (decisão 9)

**Data:** decisão registrada em 21/09/2026 · infraestrutura construída em 23/09/2026 (fase 5) · **Estado:**
pendente do dono (a infraestrutura em si está vigente; falta a decisão de usá-la)

**Contexto.** A auditoria de 21/09 perguntou: "vai existir um segundo backend de verdade?" — se não, os itens
5.1–5.3 (hospedeiro e papéis, limite de IA global por lease, guarda de relógio) poderiam esperar, porque é trabalho
grande que só se paga com a topologia em uso.

**Alternativas.** Adiar 5.1–5.3 até haver decisão — descartada pela fase 5 do plano, que os implementou de qualquer
forma (a auditoria recomendava fazer se a fase fosse abordada de qualquer jeito, e o plano optou por entregar o
mecanismo).

**Escolha.** Construir o mecanismo (`instances.hosted_by`; `ROLE=api|scheduler|all`; limite de IA global por lease
no banco; guarda de relógio que recusa subir como segundo dono se divergir do relógio do banco) sem esperar
hardware ou decisão formal do dono sobre usá-lo de verdade em produção.

**Consequências.** `test_hospedeiro.py::test_dois_backends_vivos_no_mesmo_banco` prova o contrato **em teste**; em
produção só existe **um** backend (o central) rodando contra SQLite. A decisão de rodar um segundo backend real
continua com o dono — depende de hardware e de topologia, não de código pendente.

**Evidências.** `docs/plano-100.md` §1 item 9; fase 5 do plano; `backend/app/workers/` (papéis); estado.json
5.1–5.3 (`implemented`, `proof: simulated`/`real` conforme o item).

**Relação.** Itens 5.1, 5.2, 5.3; decisão 9 do plano-100 §1.

---

## ADR-005 — IA por função e depois ator local como camada de custo

**Data:** 17/09/2026 (modelo por função) · 24/09/2026 (Ollama) · **Estado:** vigente; a parte "ator local" foi
substituída pela ADR-023 em 25/09/2026

**Contexto.** Com um único modelo (Opus 5) para planejar, decidir e verificar, o custo medido era ≈12 chamadas e
≈80 mil tokens de entrada por aparelho por comando (≈US$ 0,22–0,44). O alvo do projeto (10+ contas, tarefas
majoritariamente repetitivas) não escala nesse custo.

**Alternativas.**
- Um modelo só, mais barato, para tudo — descartada: perde qualidade no planejamento, que precisa de mais
  raciocínio que o ator ou o verificador.
- Modelo local **antes** de otimizar o caminho pago — descartada pelo dono em 24/09: o modelo local entra **depois**
  das alavancas de API (cache de prompt, preço certo, escalonamento por risco, dieta de contexto), como fase
  opcional com go/no-go dele.

**Escolha.** Modelo por função (planejador Opus, ator Sonnet, verificador Haiku, escalonamento por risco para Opus)
desde 17/09; em 24/09, ator local via Ollama nativo no Windows (`qwen3-vl:4b-instruct`, não a variante *thinking*,
que devolve conteúdo vazio) como nível 0 de `decide`, com fallback declarado para o provedor pago.

**Consequências.** Medido em execução real (`r-20260924161755-555261`): 18 decisões locais + 6 escalonadas ao Opus =
US$ 0,18, **empate** com o baseline do ator Sonnet — o modelo 4B "vagueia" (retomar a lista levou ~55 s e ~8
decisões). Economia de verdade depende de escalonar o ator local para Sonnet em vez de Opus, testar um modelo maior
(8B) ou aplicar a dieta de contexto 768/60 — nenhuma das três foi decidida ou medida ainda. `cache_control` sempre
(não condicionado) e `price_for` por prefixo exato — ambos corrigiam subcontagem real.

**Evidências.** Memória `poc-otimizacao-custo-ram.md`, `ollama-ator-local.md`; `docs/relatorio-validacao.md` §7.5,
§11; `backend/app/planning/openai_provider.py`, `routing.py`; commits `ac18099`, `42f8e93`, `b3addfe`, `ac6bf69`.

**Relação.** Itens 7.1–7.8; decisão do dono de 24/09 (ordem: alavancas de API antes do modelo local).

---

## ADR-006 — Rodízio de N contas sobre K vagas + hibernação por snapshot

**Data:** 17/09/2026 · **Estado:** vigente

**Contexto.** RAM real por instância medida em ≈3,7 GB (Android 14); só 3 de 10 contas cabiam ligadas ao mesmo
tempo. O alvo do dono era 10+ contas em apps sociais.

**Alternativas.** Manter tudo ligado o tempo todo e aceitar o teto de RAM como limite de escala — descartada
explicitamente pelo dono como cara demais para escalar.

**Escolha.** Rodízio sob demanda: N contas sobre K vagas de RAM, liga → executa → hiberna. Perfil de imagem
`-lowram` reduz o consumo em repouso; hibernação por snapshot de uso único acelera o "acordar" (medido: 14–18 s
contra 63–197 s a frio).

**Consequências.** 10/10 contas em 4 vagas, ≈11 GB de pico, 5–7 min por lote, ≈91% das etapas por receita (medido
em emulador real, provedor simulado). A guarda de RAM (`min_free_ram_mb_after_boot`) é o mecanismo que impede a
conta prometer mais do que a máquina tem — replicado por worker no item 4.2.

**Evidências.** Memória `poc-otimizacao-custo-ram.md`; `docs/relatorio-validacao.md` §7.2–§7.4; `config/config.yaml`
(`limits.auto_start_devices`, `max_online_devices`); `backend/app/devices/manager.py`.

**Relação.** Fases 0 (item 0.5), 4 (item 4.2); decisão 5 (ADR-015).

---

## ADR-007 — Receitas e fluxos: a IA ensina uma vez, o software repete

**Data:** 17/09/2026 (receitas) · 24/09/2026 (dieta e custo por chamada) · **Estado:** vigente

**Contexto.** IA repetindo o mesmo raciocínio a cada aparelho, a cada comando, é o segundo maior fator de custo
depois de um modelo único caro: ≈39% das decisões em 24/09 ainda iam para Opus por escalonamento, e a cada decisão
sem cache eram ~11 mil tokens de entrada.

**Alternativas.** Sempre decidir por modelo, sem replay — descartada: o custo não desce com o número de execuções.

**Escolha.** Aprender o fluxo uma vez com o modelo (receita: sequência de seletores) e repetir sem IA enquanto a
tela bater; IA só quando a tela diverge do esperado. Camada 2 (item 7.5–7.8, plano de 24/09): identidade de etapa
por hash dos parâmetros (não do alvo por extenso — 15 receitas do Instagram ficavam presas ao alvo antes disso);
provas locais no catálogo (seletor exato, sem chamar o verificador quando a árvore já prova); dieta de contexto do
ator (histórico comprimido, só os parâmetros da etapa).

**Consequências.** Medido: subida `ac18099` (itens 1–4 do plano de custo) trouxe Instagram de 2/3 para 3/3 de
sucesso e de US$ 0,20 para US$ 0,08 por caso. Receita com zero ações deixou de ser aprendida (evitava um defeito em
que a IA dizia "pronto" com o aparelho já no estado final e a receita vazia derrubava a etapa depois, em qualquer
outra tela) — ver `docs/conhecimento/aprendizados.md` K-004.

**Evidências.** Memória `poc-otimizacao-custo-ram.md`, `config-nao-versionado-e-agente-do-worker.md`; commits
`aa8a43c`, `ac18099`, `42f8e93`; `backend/app/taskqueue/proofs.py`.

**Relação.** Itens 7.5, 7.6, 7.7; achado do plano de 24/09 (sem número de item no plano-100, registrado como
"pedido do dono").

---

## ADR-008 — Instagram real via Play Store, sem espelho de terceiros

**Data:** 17/09/2026 · **Estado:** vigente

**Contexto.** Provar o projeto contra um app real (não o QA Messenger interno) exigia decidir a origem do APK.

**Alternativas.** Baixar um APK de espelho de terceiro (mirror site) — **recusado** pelo dono e pelo próprio
projeto: origem não verificável, risco de adulteração.

**Escolha.** Play Store, com a conta Google do próprio dono, num aparelho-loja dedicado (`instances.store`, papel
que nunca recebe tarefa, nunca é despejado, sem Appium); `POST /api/store/sync` copia por `adb pull`; distribuição
pelo mesmo rodízio dos demais apps. Nenhuma tecla da conta Google trafega pelo painel, túnel ou banco.

**Consequências.** Instagram 447.0.0.55.81 buscado, assinatura conferida, canário aprovado, distribuído 10 de 10
(18/09). A loja pode rodar num worker remoto desde 24/09 (decisão 4, ADR-014), sempre com a mesma restrição: a
conta Google nunca passa pela plataforma.

**Evidências.** Memória `poc-instagram-dominio.md`; `docs/relatorio-validacao.md` §10; `docs/worker.md` ("Loja num
worker remoto"); commits da série `feat(loja)` de 18/09.

**Relação.** Fase 6 (itens 6.1–6.6); plano-100 §7 (exclusões, ver ADR-022).

---

## ADR-009 — Desafio, 2FA, CAPTCHA e senha sempre pela pessoa

**Data:** 17/09/2026, reafirmada 18/09/2026 · **Estado:** substituída em parte por ADR-025 (26/09): a senha que a
pessoa fornece passa a ser digitada pela automação, com consentimento; desafio, 2FA e CAPTCHA continuam aqui.

**Contexto.** Automatizar login completo (incluindo resolver checkpoint por e-mail/SMS ou digitar senha via IA)
reduziria fricção operacional, mas cruza a linha entre "operar uma conta autorizada" e "burlar proteção da
plataforma".

**Alternativas.** Instalar cliente de e-mail nos aparelhos e ler o código de verificação automaticamente, ou digitar
a senha por uma ferramenta do modelo — **pedido explicitamente pelo dono em 18/09 e recusado**: digitar senha de
conta é linha dura; ler/inserir código de verificação é bypass de challenge/2FA, proibido pelas regras do próprio
projeto e as do agente.

**Escolha.** Um `SensitiveInputChannel` dedicado, em memória, do cofre direto ao driver — nunca pelo caminho normal
de digitação (não fica no histórico de ações, nunca chega ao LLM). Desafio, 2FA e CAPTCHA sempre resolvidos pela
pessoa na janela do emulador; a senha só entra pelo portal, nunca é lida nem escrita pelo agente de IA.

**Consequências.** O login completo de um perfil é sequencial, nunca em lote: 8 logins reais em paralelo fariam o
Instagram sinalizar todas as contas de uma vez, e cada conta bate no mesmo checkpoint por e-mail no primeiro login.
O item 6.4 (fila "Aguardando intervenção") existe para tornar esse ponto de espera visível, não para eliminá-lo.

**Evidências.** Memória `poc-instagram-dominio.md`; `backend/app/security/` (canal de entrada sensível);
`docs/plano-100.md` §7.

**Relação.** Item 6.4; plano-100 §7 (ADR-022).

---

## ADR-010 — Comando distribuído com cerca, outbox e idempotência

**Data:** 21/09/2026 (desenho), 22–24/09/2026 (implementação, fases 0–1 e 5) · **Estado:** vigente

**Contexto.** A auditoria de 21/09 encontrou o comando remoto mentindo: `running` marcado antes de o comando ser
enviado, ACK nunca persistido, queda de socket cancelando o trabalho no agente em vez de deixá-lo terminar.

**Alternativas.** Repetir cegamente em timeout (`timeout → repetir`) — descartada: para efeitos como DM, comentário,
curtida e seguir, retry cego dispara o efeito duas vezes; a política virou `AGIR → OBSERVAR → RECONCILIAR → só
então decidir repetir`.

**Escolha.** Marcas de tempo verdadeiras (`dispatched` só depois do envio, `acked` quando o worker confirma,
`running` no primeiro progresso); cerca (fencing) monotônica por aparelho, guardada tanto pelo central quanto pelo
**agente** (que recusa despacho de cerca menor sem tocar no aparelho); outbox no banco (`command_outbox`, migração
029) para o transporte não perder comando entre "vale" e "foi enviado"; diário local do agente
(`diario-do-agente.json`) para reenviar desfecho depois de reconexão.

**Consequências.** Queda de conexão no meio de um verbo deixa o comando `uncertain`, nunca `failed` — o agente pode
ter agido. Nenhuma fila durável dá exatamente-uma-vez (JetStream entrega ao menos uma vez); quem impede o efeito
duplo são as guardas de estado do comando e o diário do agente, não a fila em si.

**Evidências.** `docs/worker.md` ("Como o canal se comporta", "Fila de comandos"); `backend/app/commands/`;
`backend/tests/test_queda_de_conexao.py`; achados #24, #7, #148, #20, #6.

**Relação.** Fase 1 (itens 1.1–1.9); fase 5 item 5.6 (outbox/NATS); aceites 1, 6, 8 (docs/relatorio-validacao.md §13).

---

## ADR-011 — `config.yaml` e `.env` fora do Git, por instalação

**Data:** 23/09/2026 · **Estado:** vigente

**Contexto.** `config/config.yaml` era rastreado pelo Git. Um `git checkout`/fast-forward de um commit em que o
arquivo ainda era versionado para um em que deixou de ser **apaga** o arquivo da árvore de trabalho — aconteceu em
produção em 23/09: `start.ps1` recriou do exemplo e o backend subiu com `worker_port: 0` e zero aparelhos remotos,
sem nada reclamar (o `/health` confere commit e migração, não o conteúdo do que foi lido).

**Alternativas.** Manter `config.yaml` versionado e resolver por `.gitignore` de exceção por máquina — descartada:
não evita o apagamento no fast-forward entre um commit rastreado e um não rastreado.

**Escolha.** `config/config.yaml` e `.env` passam a ser **por instalação**, fora do Git. `scripts/start.ps1` PARA
quando há `data/poc.sqlite3` mas falta `config`/`.env`, em vez de recriar do exemplo, e aponta para a cópia mais
recente em `data/backups/`. `scripts/backup.ps1` passa a guardar `config/` também.

**Consequências.** Restaurar de um backup antigo exige atenção: o arquivo fica na raiz do backup, não em `data/`.
Qualquer checkout de branch/commit em produção exige conferir `git status` de `config/config.yaml` antes.

**Evidências.** Memória `config-nao-versionado-e-agente-do-worker.md`; commit `09c040f`; `scripts/start.ps1`;
`scripts/backup.ps1`.

**Relação.** Item T.3 (documentação e configuração de exemplo); aprendizado K-002.

---

## ADR-012 — Executor do plano-100: workflow na sessão da IDE

**Data:** 22/09/2026 · **Estado:** vigente

**Contexto.** O executor original de `scripts/claude-plan-100.py` abria `claude -p` em subprocesso, um por bloco,
com sessão retomada (`--resume`) e esforço escalado automaticamente. Isso não funciona no aplicativo Claude Code: o
próprio executor se recusava a rodar dentro do agente (`CLAUDECODE=1`) para não abrir sessão aninhada; não existe
executável `claude` publicado no PATH desta máquina; e, mesmo que existisse, `--resume` numa conversa só para os
itens do plano ficaria caro em vez de barato — pela metade do plano, toda chamada carregaria a conversa inteira das
anteriores.

**Alternativas.** Publicar um `claude` CLI só para isso, ou reescrever o transporte para chamar a API diretamente —
descartadas: a sessão da própria IDE já é o ambiente de execução disponível, e delegar para subagentes dentro dela
reaproveita permissões, ferramentas e o modelo já configurados.

**Escolha.** A sessão da IDE orquestra: `scripts/plano-100-pacotes.py` monta um pacote determinístico por item (sem
IA); um agente por grupo de arquivos correlatos, com o modelo e o esforço que o pacote indica; `scripts/
claude-plan-100.py aplicar <resultado.json>` registra o que voltou, validando que cada item pedido tem linha, que
`implemented` tem evidência e `blocked` tem motivo.

**Consequências.** `scripts/claude-plan-100.py run` não existe mais — o comando explica isso e sai 1, em vez de dar
erro de argumento. `scripts/tests/test_claude_plan_100.py` teve de ser reescrito nesta rodada: o teste antigo
exercitava a API aposentada (`plan.invoke`, `plan.CliInfo`, `plan.check_cli`) e não compilava contra o script atual.
As skills `plano-100-medium/high/xhigh/max/ultracode` ficaram "Aposentadas" — o esforço agora vem do pacote de cada
item, não da skill escolhida.

**Evidências.** `docs/claude-plano-100.md`; commit `21e11e9`; `scripts/claude-plan-100.py` (docstring do módulo);
`.claude/skills/plano-100/SKILL.md`.

**Relação.** Skill `preparar-tarefa` (encaminha para a esteira quando o item é do plano-100).

---

## ADR-013 — Fallback pago de recusa: ligado por padrão (decisão 3)

**Data:** decisão registrada em 21/09/2026 (recomendação: desligar), resolvida em 24/09/2026 (commit `728e2ad`,
item 0.10) · **Estado:** vigente

**Contexto.** O fallback pago de recusa (quando o modelo recusa e o sistema cai para um provedor mais caro) estava
ligado, inclusive em produção, sem aviso na execução (achado #92). O pedido original do dono foi "sem fallback pago
silencioso", e a recomendação de 21/09 era desligar até o item 7.2 torná-lo visível.

**Alternativas.** Manter desligado por padrão, exigindo opt-in por função — considerada e descartada depois que a
visibilidade (item 7.2) ficou pronta: a razão de desligar era justamente a falta de visibilidade, não o custo em
si.

**Escolha.** O dono delegou as recomendações em 24/09: o fallback pago de recusa **fica ligado** por padrão, porque
desde o item 7.2 ele é visível (linha na execução, colunas `requested_model`/`fallback` em `ai_calls`, aviso na aba
IA) e desligável por função (`ai.roles.<papel>.refusal_fallback`).

**Consequências.** A recomendação original de "desligar" só valia até a visibilidade existir. Quem quiser desligar
para uma função específica edita `ai.roles.<papel>.refusal_fallback` no `config.yaml` (fora do Git, ADR-011).

**Evidências.** `.claude/plano-100/estado.json` item `0.10`; commit `728e2ad`; item 7.2 (visibilidade);
`backend/app/config.py` (`ai_refusal_fallback`, padrão `True`).

**Relação.** Itens 0.10, 7.2; plano-100 §1 item 3.

---

## ADR-014 — Loja remota: área de trabalho remota até o worker (decisão 4)

**Data:** 24/09/2026 · **Estado:** vigente

**Contexto.** A VM da loja (`google_apis_playstore`) precisa de login na conta Google do dono, um passo manual que
não pode ser automatizado nem passar pela plataforma. A pergunta era como um worker remoto hospedaria essa VM.

**Alternativas.**
- **(a)** Área de trabalho remota (RDP) até a máquina do worker, fora da plataforma, só para o login inicial —
  **escolhida**.
- **(b)** Liberar texto na loja por um canal com o mesmo regime de segredo do cofre — adiada para quando loja
  remota virar necessidade real.

**Escolha.** Opção (a): `worker.yaml` aceita `system_image`, `ram_mb` e `window` por aparelho; quem instala abre RDP
até o worker, entra na conta Google na janela do emulador (`window: true`) e toca em Instalar; de volta ao painel,
"Buscar da loja" copia o APK já instalado, por ADB, pelo túnel. Nenhuma tecla da conta Google trafega pelo painel,
túnel ou banco.

**Consequências.** É o mesmo regime de segredo da loja local — nenhuma exceção nova de segurança. A opção (b) fica
registrada como alternativa não descartada, só adiada.

**Evidências.** `docs/worker.md` ("Loja num worker remoto"); `config/worker.example.yaml`.

**Relação.** Item 6.5; plano-100 §1 item 4.

---

## ADR-015 — Alvo de capacidade e limites por servidor (decisão 5)

**Data:** decisão registrada em 21/09/2026 · alvo escrito e testado em 24/09/2026 (item 10.3); limites configuráveis
por servidor em 24/09/2026 (item 10.5, commit `c0c982d`) · **Estado:** vigente

**Contexto.** O código tinha teto fixo de 10 aparelhos com um parque de 15 declarados; o central comportava 1 a 3
com a carga do momento, o worker 6.

**Alternativas.** Deixar o teto fixo em 10 — descartada: o parque real já passava disso com o worker.

**Escolha.** Teto do código subiu para 64 (`max_active_devices`, `max_online_devices`); o que limita de verdade
passou a ser a vaga de **cada máquina** (`max_online_devices` do host, `max_slots` de cada worker). O item 10.5
(pedido do dono, 24/09) foi além: a tela Limites separa o que é do parque (teto geral, IA, tempos) do que é de cada
máquina (vagas, boots em paralelo, teto de "trabalhando", piso de RAM), editável pelo **painel** — sem SSH no
`worker.yaml` — com um cartão por servidor e a carga ao vivo; "Distribuir entre servidores" escolhe N aparelhos pela
carga relativa de cada máquina.

**Consequências.** Teste de escala real em 24/09 (`scripts/scale-test.ps1 -SkipRun`): 14 aparelhos online ao mesmo
tempo (8 locais + 6 do worker, loja fora), boot de 10 em 626 s, host central com 20,7 GB livres no degrau 14. O item
10.5 **não estava mapeado em `.claude/plano-100.json`** no fechamento desta rodada — `scripts/docs-check.py` acusa
isso; é uma pendência de manutenção do mapa, não do código.

**Evidências.** `.claude/plano-100/estado.json` itens `10.3`, `10.5`; commit `c0c982d`, `d8a57f7`; `docs/worker.md`
("Limites por servidor"); `backend/migrations/039_limites_por_servidor.sql`.

**Relação.** Itens 10.3, 10.5; plano-100 §1 item 5.

---

## ADR-016 — Acesso de pessoas: sessão nominal sobre token único (decisão 8)

**Data:** decisão registrada em 21/09/2026 · resolvida em 23/09/2026 (item 9.1, commit `57f8a3c`) · **Estado:**
vigente

**Contexto.** O modelo de acesso era um operador com o mesmo `API_TOKEN` para todo mundo — a auditoria perguntou se
deveria virar usuários nomeados com auditoria própria, com senha individual.

**Alternativas.** Conta por pessoa, com senha própria — não descartada, mas **não escolhida** nesta rodada: exigiria
gestão de contas (criação, redefinição de senha, papéis), decisão que o registro classifica como "de quem cuida do
parque", fora do escopo do item.

**Escolha.** Sessão nominal por cima do token único: `POST /api/login` troca o nome de quem está operando (mais o
`API_TOKEN`, quando a chamada vem de fora do loopback) por um cookie `HttpOnly`/`SameSite=Strict`. A auditoria passa
a ter nome (`commands.requested_by`, `pending_approvals.decided_by` gravam o operador da sessão) — mas **não** há
controle de acesso por pessoa: quem tem o `API_TOKEN` entra com o nome que quiser.

**Consequências.** Resolve a trilha de auditoria (quem fez o quê) sem resolver controle de acesso (quem **pode**
fazer o quê). `panel` continua aparecendo como operador quando ninguém se identificou, inclusive no loopback (onde
o login é só o nome, sem token, porque ali quem chama já tem banco e ADB na mão).

**Evidências.** `.claude/plano-100/estado.json` item `9.1`; commit `57f8a3c`; `backend/app/security/sessions.py`;
`docs/worker.md` ("Sessão de usuário").

**Relação.** Item 9.1; plano-100 §1 item 8.

---

## ADR-017 — Chave do provedor de IA: sem revogação (decisão 2)

**Data:** levantada em 21/09/2026; decidida pelo dono em 24/09/2026 · **Estado:** vigente

**Contexto.** Em 17/09 a chave da API foi colada no chat, e ficou combinado que nada pago rodaria antes de o dono
confirmar a rotação. A auditoria encontrou uso pago recente sem registro dessa confirmação (achado #130), e o plano
pôs a confirmação como decisão 2 (item 0.10).

**Alternativas.** (a) Revogar a chave antiga no console e criar outra; (b) manter a chave atual.

**Escolha.** (b). Em 24/09 o dono disse, no chat da IDE, que **não vai fazer revogação nenhuma**. A trava "nada
pago antes da rotação" deixa de existir.

**Consequências.** Gasto de API continua sujeito a duas coisas, e só a elas: os tetos (`limits.ai_max_usd_per_run`,
`limits.ai_max_usd_per_day`) e a autorização explícita do dono para atividades que gastam por conta própria
(bateria de avaliação, rejulgamento, `probe-models.py`, provas pagas). Nunca ler nem imprimir o `.env`, como
antes. O item 0.10 fecha com as decisões 2 e 3 tomadas.

**Evidências.** Mensagem do dono em 24/09/2026 (sessão da IDE); `.claude/plano-100/estado.json` item `0.10`
(registrado por `aplicar`); decisão 3 em ADR-013.

**Relação.** Item 0.10; plano-100 §1 item 2; ADR-018 (gasto com a bateria).

---

## ADR-018 — Bateria de avaliação: autorizada na opção recomendada (decisão 7)

**Data:** levantada em 21/09/2026; decidida pelo dono em 25/09/2026 · **Estado:** executada em 25/09/2026 (~US$ 2,57;
resultado em `docs/relatorio-validacao.md` §11.1)

**Contexto.** Os modelos baratos por função estão em produção sem a comparação contra a linha de base que o próprio
projeto pôs como condição (achado #98). Rodar a bateria custa crédito real da API.

**Alternativas (estimativa entregue em 24/09, a partir de `ai_calls` e `data/eval-results.jsonl`).**
(a) recomendada: rejulgar as 56 capturas originais (`eval_rejudge.py --limit 56`, ~US$ 1,40–1,75), linha de base de
17 casos no parque (`eval-run.ps1`, android-09 com QA Messenger e 3 casos só de navegação no Instagram, ~US$ 1–3,50)
e análise do HTTP 500 do verificador (≤ US$ 0,20): **~US$ 3–5,50**; (b) completa, com as 266 capturas de hoje:
~US$ 8–10; (c) só o rejulgamento; (d) não rodar.

**Escolha.** (a), autorizada pelo dono no chat em 25/09/2026, com o teto diário `ai_max_usd_per_day` subindo de
US$ 10 para US$ 30 **só no dia da bateria** e voltando a 10 ao terminar. Condição combinada: rodar só depois de o dono
recarregar o saldo da API (~US$ 3,30 em 24/09 não cobre a opção; saldo que acaba no meio invalida a rodada, K-011).

**Estado atual.** Uma linha de base rodou em 24/09 em `android-09` (QA): 12/13, US$ 2,35. A continuação parou no 3º
caso porque **a conta da API ficou sem crédito** (HTTP 400 billing) — o resto daquela bateria é inválido e precisa
repetir quando o saldo for recarregado; ver aprendizado K-011.

**Evidências.** Memória `poc-otimizacao-custo-ram.md`, `creditos-dev-vs-api.md`; item 7.4 no `.claude/plano-100/
estado.json`.

**Relação.** Item 7.4; plano-100 §1 item 7.

---

## ADR-019 — Hora certa nas duas máquinas (decisão 6)

**Data:** decisão registrada em 21/09/2026 · fontes divergentes de 23/09/2026 · **Estado:** divergência sem veredito

**Contexto.** Central e worker estavam ~97 s fora um do outro (achado #142), e o lease de posse de etapa compara
esses relógios. É configuração de sistema (`w32time`), não código — a auditoria pediu que o dono a execute nas duas
máquinas.

**As duas fontes divergem sobre o que já foi feito, e este registro não resolve qual está certa:**

- `docs/parque-distribuido.md` (seção "Recuperar o parque remoto", em torno da linha 411) diz que
  `scripts/hora-certa.ps1` "configura w32time com uma fonte NTP comum... não foi executado" — escrito a partir da
  sonda de **21/09**.
- `docs/worker.md` (seção "Na máquina do worker — dependências") trata acertar o relógio como **pré-requisito**
  antes de inscrever um worker nesta mesma revisão de documentação.
- A memória do agente (`config-nao-versionado-e-agente-do-worker.md`, sessão de **23/09**) registra que o relógio
  **foi** acertado nesse dia: worker ~105 s à frente do NTP, central ~8 s; como `w32tm /resync` respondeu "no time
  data was available" logo após configurar a fonte, o passo foi `Set-Date -Adjust` pelo desvio medido em
  `w32tm /stripchart`, com o NTP (`time.windows.com,0x1`) configurado para manter a partir daí.

**Comando de verificação, para decidir com dado e não com qual fonte é mais recente:**

```
w32tm /stripchart /computer:time.windows.com /samples:3
```

Rodar nas duas máquinas (central e worker) e comparar o desvio relatado contra o que a memória de 23/09 registrou.
Se o desvio hoje for pequeno (segundos, não dezenas de segundos), a correção de 23/09 se sustentou; se voltou a
crescer, o `w32time` não ficou configurado de forma duradoura e o texto de `parque-distribuido.md` continua sendo o
retrato correto do estado real, apesar de a ação ter ocorrido uma vez.

**Evidências.** `docs/parque-distribuido.md` (~linha 411); `docs/worker.md` ("Na máquina do worker — dependências");
memória `config-nao-versionado-e-agente-do-worker.md`; achado #142; `scripts/hora-certa.ps1` (adicionado no commit
`c727284`).

**Relação.** Item 0.7; plano-100 §1 item 6.

---

## ADR-020 — Backup e janela de reinício de produção antes de migrar (decisão 1)

**Data:** decisão registrada em 21/09/2026 · executada em 23–24/09/2026 (item 0.1) · **Estado:** vigente

**Contexto.** O backend em produção estava **anterior** a duas entregas: as migrações 016 e 017 nunca tinham
rodado sobre os dados reais, e a posse de etapa e a autenticação só existiam em teste. O próximo reinício —
planejado ou não — aplicaria tudo isso sem cópia prévia do banco (achado #140, #145).

**Escolha.** `scripts/backup.ps1` faz cópia consistente com o backend no ar (o `.sqlite3` sozinho, copiado por
fora, perde o WAL); procedimento parar → copiar → migrar → conferir; ensaiar as migrações pendentes numa cópia do
banco real **antes** de reiniciar a produção no código atual.

**Consequências.** Executado em produção em 24/09 (`scripts/deploy.ps1`, commits `ac18099`, `42f8e93`, `ac6bf69`,
com e sem `-PularFrontend`): backup consistente antes, parada e subida pela tarefa `farm-central`, conferência
depois. O item ficou marcado `implemented`/`proof: real` no `.claude/plano-100/estado.json`.

**Evidências.** `.claude/plano-100/estado.json` item `0.1`; `docs/banco.md` (seção "Backup, restauração e deploy");
`scripts/backup.ps1`, `scripts/deploy.ps1`.

**Relação.** Item 0.1; plano-100 §1 item 1.

---

## ADR-021 — Commit direto na main, sem PR

**Data:** 17/09/2026, reafirmada ao longo do projeto · **Estado:** vigente

**Contexto.** O ritmo de trabalho pedido pelo dono prioriza velocidade sobre cerimônia: suíte completa só antes do
commit e em segundo plano, sem esperar confirmação para cada correção.

**Alternativas.** Pull request por mudança, com revisão antes do merge — descartada pelo dono: o fluxo de PR
existiu só na primeira semana (PRs #1 e #2, ambos mergeados em 17/09 e 21/09); desde então todo commit vai direto
para `main`.

**Escolha.** Commitar e subir direto na `main`, sem pedir confirmação a cada mudança de código, reservando revisão
mais pesada (a suíte completa) para o fim de cada rodada de trabalho.

**Consequências.** Esta rodada de documentação segue a mesma regra: cada frente comita no próprio branch de
worktree, e o coordenador integra — sem PR intermediário nem para o trabalho de documentação.

**Evidências.** Memória `ritmo-de-trabalho.md`; histórico de commits direto em `main` desde `5f3f7ea` (17/09).

**Relação.** Skill `fechar-tarefa`.

---

## ADR-022 — Exclusões deliberadas de escopo

**Data:** consolidado em `docs/plano-100.md` §7, 21/09/2026 · **Estado:** vigente

**Contexto.** Depois de decidir o que entra, o plano também registra o que fica **fora**, de propósito — para não
ser redescoberto como "esquecimento" numa auditoria futura.

**Escolha.**
- Ler ou digitar código de verificação, resolver CAPTCHA, desafio ou 2FA: **manual, sempre** (ver ADR-009). O plano
  melhora a operação em volta (fila de intervenção, item 6.4), não automatiza o desafio.
- Evasão de detecção de emulador ou de antibot: fora de escopo — o projeto opera contas reais e autorizadas, não
  contorna proteção de plataforma.
- APK de espelho de terceiros: só Play Store com a conta do dono, ou arquivo que ele mesmo fornecer; `apks/` fica
  fora do Git (ver ADR-008).
- Senha nunca entra em resposta, log, evento, evidência, captura, prompt, memória, fixture ou Git.

**Consequências.** Essas quatro linhas são verificadas em código (redação de log, canal de entrada sensível) e em
regra de revisão — não apenas em documentação. Ver `.claude/rules/segredos-e-mundo-real.md`.

**Evidências.** `docs/plano-100.md` §7; `backend/app/security/` (redação e canal sensível).

**Relação.** ADR-008, ADR-009; regra `.claude/rules/segredos-e-mundo-real.md`.

---

## ADR-023 — Ator declarado no Sonnet; modelo local fora do caminho principal

**Data:** 25/09/2026 · **Estado:** vigente · **Substitui em parte:** ADR-005 (a parte "ator local")

**Contexto.** Em 24/09 o ator (`decide`) foi ligado no Ollama local (`qwen3-vl:4b-instruct-16k`), com fallback
declarado para a Anthropic. Na bateria de 25/09 o Ollama estava fora do ar (ele sobe no login do usuário, não no
boot; a última chamada local foi em 24/09 às 16:20), e as 89 decisões foram para o fallback sem nenhum aviso na
saúde. O dono delegou a decisão pedindo custo-benefício e eficácia.

**Medido.** Ator no Sonnet 5 (bateria de 25/09): US$ 0,0077 por decisão, 11 % das decisões escaladas para o Opus
5.5, 16/17 casos corretos. Ator local (execução real `r-20260924161755-555261`): US$ 0 por decisão, mas 25 %
escaladas para o Opus; na mesma carga, economia de ~US$ 0,06 por execução frente ao Sonnet (~US$ 0,02 por caso).

**Alternativas.** (a) Manter o local como principal e subir o Ollama como serviço de boot; (b) declarar o Sonnet
como principal e deixar o local definido, desligado; (c) testar um modelo local maior (8B).

**Escolha.** (b). A economia do local é pequena (≈ US$ 2 por dia a 100 casos) e cobra em qualidade (o 4B "vagueia",
o dobro de escalonamento para o modelo mais caro), em GPU disputada com os emuladores (8 GB), em latência de
acordar frio (9–45 s) e em fragilidade operacional (fora do ar sem ninguém ver). Com o Sonnet declarado, a
configuração passa a dizer o que de fato roda.

**Consequências.** O piso de conteúdo do modelo local (7.8) deixa de atuar. O provedor `local` continua em
`ai.providers`; voltar é trocar uma linha em `ai.roles.decide` (o comentário no `config.yaml` traz a linha). Revisar
se o volume diário passar de algumas centenas de casos, ou se aparecer um modelo local que decida sem escalar mais
que o Sonnet — medido pela bateria (`eval-run.ps1`) contra `base-25-09`.

**Evidências.** `data/eval-results.jsonl` (`base-25-09`); `ai_calls` de 25/09 (`fallback = 'anthropic'`,
`requested_model = qwen3-vl…`); `docs/relatorio-validacao.md` §11.1; backup do config antes da troca em
`data/backups/config.yaml.antes-sonnet-20260925`.

**Relação.** Itens 7.1, 7.8, 7.11; ADR-005; ADR-024.

---

## ADR-024 — Verificador barato com proteções, em vez de trocar o modelo do verificador

**Data:** 25/09/2026 · **Estado:** vigente

**Contexto.** O rejulgamento de 25/09 (Opus 5.5 sobre 56 capturas que o Haiku 4.5 já tinha julgado, de 19–20/09)
concordou em 41/56. Dos 15 desacordos, 9 eram falsos negativos do Haiku (recusou o que estava feito) e 6 eram falsos
positivos (aprovou sem prova): 4 sobre tela sem nenhum elemento na hierarquia, 2 com a tela errada. Na bateria, uma
execução parou numa pessoa porque o Haiku recusou "Entregue" onde bastava "Enviada" — o prompt já diz "igual ou
superior", e o código só rebaixava o veredito por nível, nunca promovia.

**Alternativas.** (a) Verificador em Sonnet (≈ 2× o custo por verificação, US$ 0,0031 → ~US$ 0,007), sem medida de que
resolve; (b) em Opus (≈ 4–5×); (c) manter o Haiku e fechar os dois erros medidos com proteções no código.

**Escolha.** (c). (1) Recusa com nível de entrega que já atende ao exigido é rejulgada UMA vez pelo modelo de
escalonamento — promover sozinho seria arriscado, porque o "não" pode ter outro motivo (contato ou texto errados);
(2) "sim" sobre tela sem elementos na hierarquia não conta como prova, e o executor espera a tela mudar. As provas
locais do catálogo (7.5) já dispensam o modelo nos casos em que a árvore prova o estado.

**Consequências.** Custo extra só quando o erro acontece (~US$ 0,015 por rejulgamento). App que nunca expõe
hierarquia (canvas, WebView fechada) não passaria em pós-condição julgada por modelo; hoje nenhum app do parque é
assim. Os 2 falsos positivos de "tela errada" continuam possíveis quando a prova local não casa — reavaliar trocando
o verificador de função só se a bateria seguinte (com capturas NOVAS) mostrar falso positivo.

**Evidências.** `data/eval-rejudge.jsonl`; execução `r-20260925014321-86058e`; `docs/relatorio-validacao.md` §11.1;
`backend/tests/test_verificador_escalado.py`.

**Relação.** Itens 7.4, 7.10; ADR-018; ADR-023.

---

## ADR-025 — A automação digita a credencial que a pessoa fornece, com consentimento

**Data:** 26/09/2026 · **Estado:** substituída em parte por ADR-040 (27/09): a credencial passa a ser da **conta da
persona** (cofre, consentimento por conta, `type_secret` por conta); `RunCreate.credentials`, `consent_credentials`,
`run_secrets` e o 409 por execução saem. O que fica deste ADR: o valor nunca vai ao modelo, as três travas, o
`credencial_no_comando`, e desafio/2FA/CAPTCHA com a pessoa · **Substitui em parte:** ADR-009 (a recusa de digitar
senha)

**Contexto.** Execução `r-20260926161438-22d65f`: o dono pediu para abrir o Chrome no portal MTR da CETESB e entrar
com os dados que ele mesmo informou (senha incluída, no texto do comando). O planejador recusou por duas razões: o
catálogo do Instagram era o único oferecido, e "o sistema não digita credenciais" (ADR-009). E a senha, que só saía
mascarada nos eventos, ficou em claro em `runs.command`, na API de execuções e no prompt do planejador. Decisão do dono
no mesmo dia: a automação existe para fazer o que a pessoa pediu, inclusive entrar com a credencial que ela forneceu;
basta um alerta de consentimento. Os limites da IA são de comportamento (não fazer fake news, não ofender de forma
explícita — pode ser dura, nunca quebrar regra de comportamento), não de "não digitar senha".

**Alternativas.** (a) Manter o ADR-009: a pessoa assume o aparelho no Foco e digita. (b) Senha no texto do comando,
extraída por padrão. (c) Credencial num campo próprio da execução, guardada no cofre, digitada pelo canal sensível
por uma ferramenta que só conhece o NOME dela.

**Escolha.** (c). A credencial vem no campo `credentials` de `POST /api/runs` (nome → valor, `SecretStr`), vai para o
cofre (`SecretStore`, AES-256-GCM) ligada à execução e é apagada quando a execução termina. O comando com formato de
segredo é recusado antes de ser gravado (409 `credencial_no_comando`): texto de comando vai ao provedor de IA, ao
histórico do navegador e à tabela `runs`. A execução com credencial só é criada com consentimento explícito (409
`consentimento_de_credencial`; o painel pergunta e reenvia com `consent_credentials: true`). O modelo recebe só os
nomes e digita com `type_secret(name, element_id)`, que passa pelo `SensitiveInputChannel`: o valor nunca vai ao
modelo, a evento, a log, a evidência nem ao histórico de ações. Tela de senha deixa de parar a execução quando ela
tem credencial. Três travas antes de digitar: só campo de senha; só no app da etapa; no navegador, só no host de uma
URL escrita pela pessoa (ou subdomínio). A credencial sai do cofre quando a execução termina de vez (`completed`,
`cancelled`, `failed`) ou fica 24 h parada; `completed_with_issues` a mantém, porque é o estado de quem espera a
pessoa resolver um desafio e ainda será retomado. O 422 não devolve o valor de campo sensível.

**O que continua fora (limite do produto).** Desafio, 2FA por código que a pessoa não forneceu, CAPTCHA e evasão de
detecção de emulador/antibot continuam com a pessoa (ADR-009): a execução para em `waiting_user` nessas telas.
Credencial lida na tela ou inventada pelo modelo nunca é digitada. `open_url` só abre endereço http/https que está no
comando, nunca um lido da tela nem um que o planejador completou; os mesmos endereços definem os sites onde a
credencial pode ser digitada.

**Consequências.** Login de qualquer app ou site vira uma etapa comum da execução. O valor do segredo continua fora de
log, prompt, evento, evidência, memória, fixture e Git — a regra de segredo não muda, muda quem digita. Dado pessoal
que a pessoa escreve no comando (CPF, CNPJ, e-mail) vai ao provedor de IA, e o alerta de consentimento diz isso.

**Evidências.** Execução `r-20260926161438-22d65f` (senha mascarada no banco em 26/09 16:40 UTC); testes em
`backend/tests/test_credenciais_da_execucao.py`.

**Relação.** ADR-009; ADR-022; item 12.3.

---

## ADR-026 — Todos os aparelhos sempre na versão promovida

**Data:** 26/09/2026 · **Estado:** vigente · **Decisão do dono** (chat da sessão coordenadora, 26/09): "todos devem
ficar atualizados sempre".

**Contexto.** A loja de aplicativos (PR #10, 26/09) deixou duas perguntas abertas em
[`dominios/apps-e-loja.md`](dominios/apps-e-loja.md): (1) promover uma versão devia continuar atualizando sozinho só
os aparelhos que têm o app como PRINCIPAL? Na prática, nem esses: promover só mudava o banco. O aparelho recebia a
versão nova pela porta do app, antes da próxima tarefa daquele pacote, ou quando alguém pedia "Distribuir" de novo. O
app secundário (o Outlook num aparelho de Instagram) nunca mudava de versão sozinho. (2) Voltar UM aparelho devia
continuar rebaixando a versão para o parque inteiro? O `rollback` marcava a versão de onde o aparelho saiu como
`rolled_back`, e ela deixava de ser a promovida. Mas os outros aparelhos ficavam nela, e quem ainda a esperava tinha a
tarefa bloqueada com "não pode mais ser entregue".

**Alternativas.** (a) Manter como estava: a promoção é só um rótulo, e cada atualização é um "Distribuir". (b) Só o
app principal converge sozinho, e o secundário depende de "Distribuir". (c) A versão promovida é o estado desejado de
TODO aparelho que tem o app, principal ou secundário. A volta vale para o parque inteiro, e todos voltam juntos.

**Escolha.** (c). A versão promovida (`ReleaseService.promoted_release`: a maior entre as promovidas) é o que todo
aparelho de tarefa que TEM o app persegue sozinho:

- **Ter o app** é ter linha em `device_app_state` com `installed_release_id` ou `observed_version_code`, ou com uma
  versão desejada já gravada. O app principal do aparelho (`instances.app_id`) conta mesmo sem linha, a regra que já
  valia desde o android-12..15 (`aplicar_versao_promovida`). Fora disso, nada é instalado em quem não tem o app:
  espalhar continua sendo "Distribuir", explícito.
- **Promover** (`POST /api/releases/{id}/lifecycle`, `verb: promote`) grava a versão desejada em cada aparelho que
  tem o app (`vitrine.convergir_o_parque`) e acorda a entrega uma vez. O ligado e livre instala já. O ocupado recebe
  na varredura de 60 s, sem passar na frente de tarefa. O desligado recebe quando ligar. A resposta lista os
  aparelhos (adendo v0.19). Promover não liga aparelho nenhum: isso continua sendo "instalar em todos agora"
  (`distribute` com `eager`).
- **Entrar no ar** adota a promovida de cada app que o aparelho tem (`AppState.adotar_promovidas`). A varredura de
  60 s adota antes de procurar o que entregar. O app principal passa a ser entregue sem tarefa também, exceto com
  um objetivo no meio (rodando ou esperando uma pessoa); aí a porta do app o entrega antes do próximo.
- **Voltar** um aparelho marca a versão de onde ele saiu como `rolled_back`, e o parque converge para a promovida
  anterior logo no fim do trabalho da volta: o ligado e livre já, o desligado quando ligar. Rebaixar da versão
  voltada vai com `-d`, preservando os dados, como o `rollback`. Se o Android recusar, a linha fica
  `downgrade_refused` e não se repete sozinha, porque reinstalar apaga a sessão. O desejo que apontava para a versão
  voltada se realinha em vez de bloquear a tarefa.
- **Travas de sempre:** a versão tem de ser instalável e promovida, e compatível com o aparelho (`motivo_incompativel`);
  a linha não pode ter operação aberta; entrega que falhou tem nova tentativa automática no máximo uma vez por dia. O
  relógio dessa tentativa passa a contar também a prova de instalação (`app_release_validations`), porque a entrega
  sem tarefa não abre comando (K-032).
- **O que NÃO converge sozinho:** o aparelho com versão MAIS NOVA que ninguém voltou. É o canário em prova, ou o app
  instalado por fora do catálogo. Rebaixá-lo desfaria a prova. A **quarentena** também não rebaixa ninguém: ela para
  de espalhar a versão (quem a esperava volta a esperar a promovida), mas "quem já está nela continua até você pedir a
  volta", como o painel sempre prometeu. Pedir a volta é o que leva o parque junto.
- **Mesmo número conta como atualizado.** A produção tem duas promovidas 1.0.0/1 do app de QA
  (`com.pocqa.messenger-1-0a769110a5f2` e `-1-a796939db14c`, dois builds). Quem está numa versão promovida e
  instalável de mesmo `version_code` que o alvo fica onde está. Trocar uma pela outra reinstalaria o parque e
  invalidaria sessões para ficar na mesma versão. É a mesma régua da vitrine (`outdated` compara `version_code`).
- **Escolha estável.** Com empate de `version_code`, `promoted_release` escolhe a promovida por último
  (`channel_at`) e, depois, o maior id. A ordenação é feita em Python, não no SQL: o id é TEXT, e a colação do
  PostgreSQL não ordena como a do SQLite (K-030). Antes, a ordem do empate ficava a critério do banco, e a
  convergência poderia alternar entre as duas. A vitrine e o painel usam a mesma escolha.

**Consequências.** "Atualizar" deixa de depender de clique ou de tarefa: promover é a ordem, e o parque converge por
conta própria. O aparelho ligado e ocioso também recebe o app principal. Antes, isso só acontecia antes da próxima
tarefa. Quem abre o painel vê a mesma promovida que o parque persegue. Voltar um aparelho é voltar o parque. Quem
quiser testar uma versão num aparelho só continua tendo o canário, que a convergência respeita. A entrega sem tarefa
consome as mesmas vagas de trabalho do aparelho (`run_device_job`), nunca na frente de uma tarefa esperando. Como
agora o app secundário chega sozinho a todo aparelho que o tem, a prova de abertura dele termina com HOME e
`am force-stop`: o app conferido não pode ficar na frente do app principal (medido no android-01 em 26/09).

**Evidências.** `simulated`: `backend/tests/test_sempre_na_promovida.py`, com 13 testes. Dez falham no código
anterior (`main` em `dc47f60`, trocando só `backend/app`) e passam agora; o da quarentena falha já na promoção. Os do
canário e da prova de abertura do app principal passam antes e depois: são guardas de regressão. O último protege a
rota nova: uma convergência que falha não vira 500 numa promoção que já valeu. A prova real ficou `not_run`, com
procedimento no PR `claude/sempre-na-versao-promovida`.

**Relação.** K-030; K-032; adendos v0.17 e v0.19 de [`api-contract.md`](api-contract.md);
[`dominios/apps-e-loja.md`](dominios/apps-e-loja.md).

---

## ADR-027 — Prévia e observação sob demanda; medição agregada

**Data:** 26/09/2026 · **Estado:** vigente. Pedido do dono de evolução de desempenho (26/09). As escolhas abaixo são
do coordenador, dentro do pedido. A escalada na divergência de receita foi decidida em 27/09, com a delegação do
dono ("avalie o custo-benefício"): **não escalar**. Implantado em `a90a6e1` (27/09), com prova real no relatório §9.

**Contexto.** Trabalho confirmado no código de `1104d50` (relatório §3):

- o central capturava a tela de todo aparelho ligado a cada 5 s, com ou sem alguém olhando;
- o `observe()` capturava e codificava a imagem antes de a política dizer se ela seria usada;
- a prévia mostrava tela sensível, que já ficava fora do modelo e das evidências;
- não havia medição de captura, codificação ou observação, e as GETs não davam p50/p95.

**Escolha.**

- **Prévia pelo interesse do painel.**
  - Cada conexão do painel declara o que vê (`watch`: grade visível e foco, TTL de 5 a 60 s), e vários espectadores
    dividem a mesma captura.
  - Sem interesse, não há screencap de prévia, e a tela aparece como `paused`, que não é erro.
  - Painel antigo, que nunca manda `watch`, conta como grade em todos, que é o comportamento anterior.
  - Controle manual conta como foco. A grade não segura a hibernação.
  - A volta atrás, sem reinício, é `preview_mode: always`.
- **Observação com a árvore primeiro.**
  - A imagem só é capturada quando a política, o julgamento, a evidência ou a divergência de receita pedem.
  - O PNG é decodificado uma vez, e só a codificação consumida é gerada.
  - A imagem que chega depois (evidência de falha) nunca serve para escolher coordenadas.
- **Tela sensível nunca aparece na prévia.** Vale para o marcador sem imagem, para `/frame` (404
  `sensitive_screen`), para a captura durante `type_secret` e para a **VM-loja**. Esta última é coerente com o ADR-014
  (a conta Google entra pela janela do emulador, nunca pelo painel). A volta atrás é tirar `rt.store` de
  `_previa_sensivel`.
- **Medição agregada** (`metricas.py`, `GET /api/desempenho`). Contadores e distribuições em memória com teto de
  séries, e uma linha por janela de 15 min em `measurements`. Nada por frame no banco, e nenhum rótulo livre.
- **Receita divergida não escala de modelo sozinha.** O código nunca escalou; o comentário que prometia foi
  corrigido. O retorno à IA passou a ser contado (`receita.retorno_ia`).
  - **Critério de 27/09:** escalar só se `recipe+ai` concluir 10 pontos ou mais abaixo de "só IA", com n ≥ 30, ou
    tiver o dobro de repetições.
  - **Medido:** 91 % (20 de 22) contra 95 %, com p95 de 1 tentativa por etapa.
  - **Custo de ligar:** +US$ 0,69 a 1,37 por semana, porque Opus 5.5 custa US$ 0,0304 por decisão contra US$ 0,0148
    do Sonnet.
  - **Resultado:** não escala. Reabre quando o funil juntar n ≥ 30 com o critério atendido.

**Consequências.**

- Resultados simulados contra a linha de base (relatório §5):
  - prévia sem espectador: 18 → 0 screencaps;
  - `image_policy: auto`: 16 → 9;
  - repetição com receitas: 16 → 11;
  - chamadas de IA e sucesso iguais.
- Não medido: o ganho real de CPU e rede, que depende do deploy.
- A VM-loja fica às cegas no painel; tecla e texto seguem pelo `frame_id` do marcador.
- Com a aba oculta, o painel só mantém o foco do aparelho que ele controla manualmente.

**Evidências.**

- `backend/tests/test_previa_sob_demanda.py`, `test_previa_tela_sensivel.py` e `test_observacao_arvore_primeiro.py`;
- `test_metricas.py`, `test_receita_divergida_escala.py` e `test_revisao_*.py` (revisão F8);
- vitest `api/ws.test.ts`, `store/live.test.ts` e `DeviceCard.preview.test.tsx`;
- `scripts/bench.py` e [`relatorio-desempenho.md`](relatorio-desempenho.md).

**Relação.** Adendo v0.20 de [`api-contract.md`](api-contract.md); ADR-007; ADR-014; ADR-024;
[`handoffs/evolucao-desempenho.md`](handoffs/evolucao-desempenho.md).

---

## ADR-028 — Runtimes, executores e orquestração: o que fica como está e o que reabre

**Data:** 26/09/2026 · **Estado:** vigente. Frente F7 da evolução de desempenho, só leitura. A análise completa está
no [relatório](relatorio-desempenho.md) §6.

**Contexto.** O pedido do dono avaliava Docker, emulador em contêiner, Redroid, Kubernetes, NATS e executores por
API ou web pelo retorno demonstrado, sem obrigação de adotar nenhum. O ambiente tem dois hosts Windows, nenhum Linux
com KVM, e o WSL do central sem binder.

**Escolha.**

| Alternativa | Decisão | Motivo | Reabre quando |
|---|---|---|---|
| Emulador nativo com WHPX | adotado | medido: cerca de 2,7 GB por aparelho, acordar em 14–18 s, GMS e tradução ARM | — |
| Trocar `-gpu swiftshader_indirect` (obsoleto desde o emulador 36.4.9) | **rejeitado por enquanto** (piloto de 27/09) | `swiftshader`, `swangle` e `host` mostraram a interface travada ("System UI/Process system isn't responding") 45 s depois do boot; o atual subiu limpo. O `swangle` também dobrou o boot e perdeu a rede | nova versão do emulador, ou remoção do modo; o mesmo protocolo com n ≥ 3 |
| Emulador nativo em Linux/KVM | adiado | não há máquina | máquina Linux com KVM (item 10.4) |
| Emulador em contêiner com KVM | adiado | só Linux; snapshot não documentado; Docker Desktop não é suportado em Windows Server | host Linux com o braço A1 medido |
| Redroid | rejeitado | sem GMS nem Play Store (só por binário de terceiro), sem snapshot, exige binder | host com binder e app-alvo sem GMS, com ganho de densidade medido |
| API oficial do Instagram | adiado | só conta profissional; não inicia DM, não curte, não segue | conta profissional, app Meta e autorização do dono |
| Automação web ou API privada do Instagram | rejeitado | evasão de antibot (ADR-022) | — |
| Navegador de desktop para sites | adiado | há um só caso concreto | tarefa de site recorrente |
| Docker nos serviços do central | **validado no CI; o central continua nativo** | O workflow `conteiner.yml` passou em Linux (run 36287055919: build, saúde, 401 sem token, persistência, sem privilégio). No central, o Docker Desktop não é suportado em Windows Server, custaria cerca de 10 GB e religaria contêineres de outro projeto | host Linux para o central, ou necessidade de ambiente isolado de validação local |
| NATS JetStream | adiado; o WebSocket continua | um só processo de controle | dois processos de controle e o achado #27 resolvido |
| Kubernetes | rejeitado | nós Windows, estado em memória, agendador da aplicação já decide | três ou mais hosts Linux com KVM e necessidade de failover |

**Consequências.**

- Os dois defeitos latentes do NATS foram corrigidos na F4: assunto por réplica hospedeira e `ack_wait` aplicado.
  Continuam sem prova contra um broker real.
- A troca de renderer é a única alavanca de runtime executável neste hardware, e depende de autorização.

**Evidências.** Documentação oficial citada no relatório §6; `relatorio-validacao.md` §2.2 e §7;
`devices/perfis.py`.

**Relação.** ADR-006 (hibernação); ADR-008 e ADR-022 (origem do APK, sem evasão); ADR-004 e o achado #27 (segundo
backend).

---

## ADR-029 — Desafio de segurança do Instagram bloqueia o perfil sozinho

**Data:** 27/09/2026 · **Estado:** vigente · **Decisão do dono** (chat da sessão "Evolução Android multiagentes", 27/09):
"o aviso que apareceu na tela inclusive é a prova disso e quando ocorrer ele a conta pode ser automaticamente
desabilitada".

**Contexto.**

- Das oito contas reais do Instagram, só três seguem funcionando: `lucas.almeida9484`, `bruno.ferreira9267` e
  `andre.carvalho9543`.
- As outras cinco mostraram, em algum momento, a verificação de segurança do Instagram (`ChallengeActivity`) e não
  voltaram. O android-04 caiu nela ao abrir o app em 27/09.
- Até aqui, o desafio só punha a SESSÃO em `auth_challenge` ("precisa de pessoa", ADR-009), e o perfil continuava
  `active`. Quando a sessão era relida (controle devolvido, validade vencida), a porta voltava a considerar a conta.

**Escolha.**

- **Entrada no desafio:** na entrada da sessão em `auth_challenge`, o perfil passa de `active` para `blocked`
  sozinho. A função `integrations/instagram/authentication.py::bloquear_por_desafio` é chamada nos dois caminhos
  que gravam o estado:
  - o login e a leitura determinísticos (`InstagramAuthenticator._save`);
  - a tela contradizendo a sessão no meio de uma execução (`AppState._sessao_desmentida`).
- **O que o `blocked` já impede:** a porta de sessão (`AppState._session_gate`) e a distribuição
  (`Scheduler.candidatos_do_app`) já recusavam perfil que não esteja `active`. Nenhuma regra nova de despacho foi
  necessária.
- **Um aviso por entrada:** o log de nível erro traz `data.reason = "auth_challenge"`. Confirmar o mesmo desafio não
  repete o aviso.
- **Pausa do dono fica:** perfil `disabled`, pausado pelo dono, continua `disabled`.
- **Sem reativação automática:** se a pessoa resolver a tela e a sessão voltar a `session_ready`, o perfil continua
  `blocked`. Reativar é decisão de pessoa, na tela do perfil (`PATCH status: active`).
- **2FA também bloqueia:** vale para o pedido de código de dois fatores, que é o mesmo estado. Nenhuma conta do parque
  tem 2FA configurado; bloquear por engano custa um clique, e não bloquear custa insistir numa conta travada.

**Consequências.**

- ADR-009 continua vigente: desafio e 2FA não são resolvidos pela automação, e nada foi feito para contornar a
  verificação.
- O que muda é o destino da conta: ela sai da automação na hora.
- **Aplicado aos dados em 27/09, a pedido do dono:** as cinco contas travadas foram desatreladas das personas e dos
  aparelhos, e ficam como perfis `blocked`, sem persona e sem aparelho, com o histórico preservado:
  - `beatriz.rocha9276`;
  - `felipe.nogueira93762026`;
  - `juliana.mendes9056`;
  - `mariana.costa91182`;
  - `thiago.moreira4827`.

**Evidências.**

- `backend/tests/test_instagram_auth.py`:
  - `test_desafio_bloqueia_o_perfil_sozinho_e_uma_vez`;
  - `test_perfil_pausado_pelo_dono_continua_pausado_no_desafio`;
  - `test_desafio_resolvido_nao_reativa_o_perfil_sozinho`.
- `backend/tests/test_sessao_com_validade.py::test_desafio_visto_na_execucao_bloqueia_o_perfil_uma_vez`.
- Registro da desatrelagem em [`relatorio-desempenho.md`](relatorio-desempenho.md) §10.

**Nota de 27/09 (fase K1, `99d851b`).** A regra não mudou; mudou de casa. `bloquear_por_desafio` e
`emit_needs_person_change` moram agora em `modules/identity/application/session_rules.py`, porque são do perfil e não
do Instagram, e `integrations/instagram/authentication.py` os reexporta. O motivo no aviso recebe o rótulo do app
(`motivo_do_bloqueio_por_desafio(app_label)`). Os testes acima seguiram verdes sem mudar asserção (`simulated`).
Ver [ADR-039](#adr-039--manifesto-de-app-e-registro-de-sessionprovider).

**Relação.** ADR-009 (desafio pela pessoa); ADR-025 (credencial fornecida); [`dominios/perfis-e-instagram.md`](dominios/perfis-e-instagram.md).

---

## ADR-030 — Monólito modular incremental, com regras de dependência verificadas

**Data:** 27/09/2026 · **Estado:** vigente · **Pedido do dono** (chat, 27/09): evoluir a arquitetura interna para
um monólito modular (DDD-lite, portas e adaptadores, fatias verticais), sem reescrever, sem microsserviço e sem
destruir funcionalidade.

**Contexto.** Medido em `82b1057`:

- `api.py` com 3.544 linhas, `state.py` com 2.198, `models.py` com 1.820 e `devices/manager.py` com 3.415.
- 78 imports internos dentro de função, quase todos para contornar ciclo.
- 894 `Any` em anotação.
- Um ciclo real em execução: `state.py` chamava por import local 7 funções não HTTP que moravam em `api.py`, e por
  ele todo pacote alcançava todo pacote.

O relatório completo está em [`design/evolucao-arquitetural.md`](design/evolucao-arquitetural.md) §2.

**Escolha.**

- **Contextos e camadas.** Os contextos são fleet, applications, identity, capabilities, skills e execution. IA é
  adaptador, não domínio. Código novo nasce em `app/modules/<contexto>/{domain,application,infrastructure}` e em
  `app/contracts`. O legado se move com shim no lugar antigo, primeiro sem editar.
- **As regras são teste, não convenção.** `tests/test_arquitetura.py` confere tudo por AST, sem dependência nova:
  - camadas puras sem biblioteca de infraestrutura;
  - biblioteca de infraestrutura só onde já morava;
  - fecho do agente do worker;
  - zero ciclo de topo;
  - ciclos em execução só encolhem;
  - catracas de import tardio e de `Any` por pacote;
  - contextos novos em DAG.

  As catracas reprovam nas duas direções: ganho não registrado também reprova, para a base descer no mesmo commit.
- **Tipagem gradual.** mypy vem de `requirements-dev.txt`, e nunca do `requirements.txt`, que o deploy instala no
  venv de produção. No job `backend-tipos` do CI:
  - `app.contracts` e `app.modules` são estritos e reprovam;
  - o legado só é medido (124 erros em 45 arquivos, em `fc5f1eb`).
- **Primeiro limite extraído.** O despacho de comandos (~1.000 linhas) saiu de `api.py` para `commands/despacho.py`,
  que é importado no topo por `state.py`, `devices/proxy.py` e `workers/local.py`. A recusa virou exceção de
  aplicação, `DespachoRecusado`, traduzida na borda HTTP para o mesmo corpo de antes, byte a byte. `AppRepository`
  (`modules/applications`) virou o único que escreve na tabela `apps`.

**Consequências.**

- O ciclo `api ↔ state` sumiu. `api.py` foi a 2.606 linhas e os imports tardios caíram de 78 para 61.
- O `AppState` continua sendo o localizador de serviços até as portas tipadas existirem (fase K).
- Os leitores da tabela `apps` ainda fazem SQL próprio.

**Evidências.**

- `tests/test_arquitetura.py`;
- `tests/test_app_repository.py`;
- o teste da resposta HTTP idêntica do despacho recusado;
- a suíte inteira em SQLite (fase A: 1.631 testes, prova `simulated`).

**Relação.** ADR-031 (contratos do worker); [`arquitetura.md`](arquitetura.md) §"Regras de dependência".

---

## ADR-031 — Contratos compartilhados do worker e manifesto único do agente

**Data:** 27/09/2026 · **Estado:** vigente · **Decisão técnica** dentro do pedido de evolução arquitetural (27/09).

**Contexto.**

- **A lista do pacote estava em três lugares, escrita à mão:** `worker-install.ps1`, `worker-install.sh` e o aviso
  final do `deploy.ps1`.
  - As três copiavam pastas inteiras do central, com 13 módulos que nem importam na máquina do worker.
  - Também esqueciam o que o agente importa (K-034).
  - O `deploy.ps1` mandava copiar só `backend/app/worker/`, o que dá `ImportError`.
- **Havia um `ImportError` latente no agente instalado:** `devices/adb.py` → `devices/conectividade.py` →
  `app.models`, e `models.py` não ia para o worker.

**Escolha.**

- **Contrato do fio.** Os modelos do fio foram para `app/contracts/worker/protocol.py`, e o vocabulário de verbos
  para `app/contracts/worker/verbos.py`.
  - `app/workers/protocol.py` e `app/devices/verbs.py` reexportam os mesmos objetos: `is` vale, e há teste.
  - O fio não mudou.
- **Esquema congelado.** `tests/test_contratos_do_worker.py` calcula a marca do esquema JSON, `18285a7c65c51551`,
  a mesma antes e depois, e as marcas por mensagem. Mudar o fio exige atualizar a marca de propósito, com o motivo.
  Subir `PROTOCOL_VERSION`/`PROTOCOL_MIN` segue a regra do protocolo (C7) e não fica implícito.
- **Manifesto único.** `backend/worker-manifest.txt` é a fonte única dos dois instaladores e do aviso do deploy.
  - `tests/test_pacote_do_agente.py` confere que ele é exatamente o fecho de import de `app.worker.*`.
  - O mesmo teste importa todos os módulos a partir de uma cópia feita só com o manifesto, em subprocesso, sem o
    backend no caminho.
  - O instalador monta o pacote ao lado e troca `app/` inteiro. Origem sem manifesto é erro.
- **Sonda de rede.** O comando da sonda de rede foi para `devices/sonda_rede.py`, só stdlib. `app.models` saiu do
  fecho do agente.

**Consequências.** Depois do próximo deploy, o agente de campo aparece como defasado, mesmo com o fio igual.
Atualizá-lo é ato no mundo real e exige autorização: rodar `worker-install.ps1 -Simular` na máquina do worker,
depois sem `-Simular`, e conferir batida, versão e um `stop` fechando `succeeded` (procedimento em
[`worker.md`](worker.md)). Enquanto isso, a prova no agente real fica `not_run`.

**Evidências.**

- `tests/test_contratos_do_worker.py`;
- `tests/test_pacote_do_agente.py`;
- `tests/test_instalacao_do_worker.py`, que importa a árvore montada pelos dois scripts;
- a suíte inteira em SQLite (fase B: 1.676 testes, prova `simulated`).

**Relação.** K-034, K-036; ADR-030; [`worker.md`](worker.md).

---

## ADR-032 — Capability, Skill e Process

**Data:** 27/09/2026 · **Estado:** vigente · **Decisão técnica** dentro do pedido de evolução arquitetural (27/09).
Código da fase C: `662a7e8`, integrado em `7403a7e`.

**Contexto.**

- O que um app sabe fazer é `planning/capabilities.py::Capability`, num catálogo em código (23 capabilities do
  Instagram).
- O que se repete tem dois formatos, e nenhum é versionado:
  - o fluxo (`flows`) é o plano inteiro de um comando, mutável e apagável (`DELETE /api/flows/{id}`);
  - a receita (`recipes`) é o gesto gravado de uma etapa.
- Não havia nome para "processo reutilizável e versionado", nem separação entre **o quê** (a operação) e **o como**
  (receita, IA, pessoa). O diagnóstico está em [`design/evolucao-arquitetural.md`](design/evolucao-arquitetural.md)
  §2.6.

**Escolha.**

- **Três conceitos:**
  - **Capability** é a operação semântica de um app, não um gesto. É um valor imutável,
    `modules/capabilities/domain/definition.py::CapabilityDefinition`, com identidade `CapabilityRef` = (pacote,
    chave, versão do contrato). O mapeamento com o catálogo legado é 1:1 (`catalog_registry.py::CAMPOS`), e o
    catálogo em código continua sendo a fonte até a fase K;
  - **Skill** é um grafo versionado de nós que referenciam capabilities, contratos inline (`goal`) ou outras skills,
    sempre na versão exata (ADR-033, ADR-034);
  - **Process** é a skill compilada: o IR (`ProcessGraph` de `ProcessNode`s), baixado para o `Plan` que o runtime já
    executa.
- **O como é separado:** `StrategyKind` (`deterministic`, `recipe`, `app_provider`, `ui_generic`, `ai_actor`,
  `human`) e a porta `ExecutionStrategy`. Nenhuma estratégia decide sucesso; quem decide é o VERIFY.
- **Portas do lado de quem consome.** `CapabilityProvider` e `ExecutionStrategy` moram em
  `modules/execution/application/ports.py`, e quem implementa cumpre por estrutura. Os tipos das assinaturas moram em
  `modules/capabilities/domain`, para os contextos continuarem num DAG (D5).
- **`steps.capability` (010) mantém o sentido:** a ação do catálogo, chave de política e de limite.
- **Sem fiação na fase C.** `CatalogCapabilityProvider` tem `supports` e `verify` reais (`verify` embrulha a prova
  local, que nunca reprova sozinha). `observe`, `execute` e `reconcile` levantam `NotImplementedError` com o motivo:
  o driver, a cadeia de estratégias e a reconciliação continuam donos do executor e do scheduler.

**Consequências.**

- O compilador e o provider enxergam o catálogo sem importar o legado (D2).
- Campo do catálogo sem destino reprova teste, e campo sem leitor fica sinalizado (`SEM_CONSUMIDOR`).
- `deterministic`, `app_provider` e `ui_generic` não servem a nó de skill na v1alpha1 (`E_STRATEGY_UNAVAILABLE`), e
  nenhum provider toca aparelho na fase G (decisão 7 do design).
- Onde o código difere da §7 do design (vale o código): `supports(cap)` sem o app, e `verify`/`reconcile` devolvendo
  `VerifyResult` (veredito e motivo).
- Os comentários de `definition.py` e `strategy.py` citam "ADR-031, proposto": o número certo é este.

**Evidências.**

- `backend/tests/test_capabilities_do_dominio.py`: mapeamento 1:1 das 23 capabilities, `verify` pela prova local e
  pela marca de falha, e as três operações que falham alto (`simulated`).
- `backend/tests/test_contrato_skill_dsl.py::test_vocabularios_repetidos_no_contrato_batem_com_os_donos`
  (`simulated`).
- Uso no runtime: `not_run` (fase G). Nada implantado.

**Relação.** ADR-007 (receitas e fluxos); ADR-030; ADR-033; ADR-034;
[`dominios/capabilities.md`](dominios/capabilities.md).

---

## ADR-033 — IR de skill e DSL `automation/v1alpha1`

**Data:** 27/09/2026 · **Estado:** vigente · **Decisão técnica** dentro do pedido de evolução arquitetural (27/09).
Código da fase E: `8d394e2` e `e8c51e0`, integrados em `7403a7e`.

**Contexto.**

- Uma skill precisa de um conteúdo que a pessoa edite, o LLM proponha e a máquina confira antes de executar.
- O runtime atual consome `Plan`/`PlanStep`, e as proteções dele (intenção antes do efeito, posse, cerca, um commit
  por etapa, `uncertain` sem reenvio) teriam de ser refeitas por um runtime de grafo (design §14.5).
- No caminho do planejador, o LLM emite o plano direto, sem versão e sem conferência antecipada.
- A prova local é atalho **positivo**: `True` dispensa o verificador (`taskqueue/proofs.py`).
- No treino, etapas sem aresta eram promovidas juntas, e uma podia rodar antes da nova tentativa da anterior.

**Escolha.**

- **A DSL `automation/v1alpha1`** mora em `contracts/skills/v1alpha1.py` (só stdlib e pydantic), com
  `extra="forbid"` em todo objeto. O esquema JSON fica congelado em
  `backend/tests/contratos/skill-dsl.v1alpha1.json`.
- **O compilador é determinístico, sem I/O e sem IA:** documento → validações → IR (`ProcessGraph`, no domínio) →
  baixa (`lowering.py`, na infraestrutura) → `Plan`, pelo mesmo `build_step` do planejador, com o validador do `Plan`
  como última porta.
- **O compilador é o único produtor de `Plan` para skill nova** (`schema_version` 1), e nunca gera nem executa Python.
  O que vem do LLM é dado: texto é texto, e expressão só pela gramática fechada. Conteúdo legado (`schema_version` 0)
  passa direto por `legacy_plan`.
- **Sem `local_proof` no documento** (`E_VERIFICATION_WEAKENED`): só o catálogo, código revisado, declara prova
  local.
- **Sem política por nó na v1alpha1:** `policy` só `inherit`, `on_failure` só `fail`, e `approval(s)` dá
  `E_FIELD_RESERVED`.
- **`depends_on` sempre emitido.** Omitido quer dizer "depende do anterior"; independência exige `[]`.
- **`PlanStep.key == node_id`**, e `PlanStep.origin` opcional e aditivo (P3): plano que não veio de skill continua
  byte a byte igual.
- **Erro com código estável** (`E_*`), ponteiro JSON e severidade, nunca exceção. Todo código tem fixture.

**Consequências.**

- A etapa compilada é a etapa do planejador mais a origem, e as receitas continuam casando pela chave.
- Mudar o contrato exige regerar o snapshot de propósito (`ATUALIZAR_CONTRATOS=1`) e dizer no commit o que mudou.
- A regra D15 ganhou uma emenda (27/09): a baixa importa `planning.capabilities`, por causa do `build_step`.
- Limites aceitos: identidade de receita por texto da etapa até a fase K; valor de parâmetro sem conferência de tipo
  até a fase I.
- A fiação em `taskqueue/service.py::_plan` é da fase G, em curso.
- Os comentários de `compiler.py` e `ir.py` citam "ADR-032, proposto": o número certo é este.

**Evidências.**

- `backend/tests/test_compilador_de_skills.py`: goldens válidos e inválidos, D15 por AST, texto inerte e
  determinismo (`simulated`).
- `backend/tests/test_contrato_skill_dsl.py`: esquema congelado, nenhum objeto aberto, vocabulários (`simulated`).
- Fixtures em `backend/tests/fixtures/dsl/v1alpha1/` (`validos`, `invalidos`, `auxiliares`).
- O teste 4 da §12.1 (execução com `runs.skill_id` e hash igual a uma recompilação): exercido na fase G por
  `backend/tests/test_fatia_abrir_conversa.py` (`simulated`). Nada implantado.

**Relação.** ADR-025 (credencial só pelo nome); ADR-032; ADR-034; [`skill-dsl.md`](skill-dsl.md);
[`skill-runtime.md`](skill-runtime.md).

---

## ADR-034 — Versionamento de skill

**Data:** 27/09/2026 · **Estado:** vigente · **Decisão técnica** dentro do pedido de evolução arquitetural (27/09),
com as decisões P1 e P4 do coordenador. Código da fase D: `75f0186` e `11fb8a3`, integrados em `616889b`.

**Contexto.**

- O fluxo legado é mutável e se apaga. Não tem versão, validação registrada nem rollback; a execução só guarda
  `runs.flow_id`.
- Um booleano "validado" não diz o que foi provado. O desenho bom já existe em `app_release_validations` (011): a
  transição lê observação registrada.
- Três desenhos de persistência estavam na mesa (design §19, decisão 1): fluxo versionado; linha-ponte em `flows`;
  tabelas novas com adaptador. Os dois primeiros misturam legado mutável com conteúdo imutável ou escrevem em dobro.

**Escolha.**

- **Estados e transições:** `draft`, `candidate`, `validated`, `published`, `deprecated` e `disabled`, na tabela
  fechada `modules/skills/domain/lifecycle.py::TRANSITIONS`. Não há volta a rascunho, e `disabled` é terminal.
- **Congela ao sair de `draft`**, e não só ao publicar (decisão 3). São três camadas: o domínio (`revise`), o
  repositório, que é a camada obrigatória e confere o `content_hash` na leitura, e o gatilho da 046, a segunda camada.
- **Ponteiro lógico da publicada:** a única linha `published` da skill, pelo índice parcial
  `ux_skill_versions_publicada`. Publicar e reverter movem o ponteiro numa transação. Não há coluna-ponteiro, porque
  ela criaria um ciclo de FK que `tools/migrate_data.py` não resolve.
- **P4:** caso `device` só conta para `validated` com observação `proof=real`; para os demais, `simulated` basta.
  Sem prova real, só uma pessoa valida, com motivo, e as pendências ficam registradas na transição.
- **Desligar, nunca apagar:** só `draft` se apaga, e a definição com versão não se apaga.
- **Um registro, dois backends:** `CompositeSkillRegistry` = `SqlSkillRepository` + `LegacyFlowAdapter` (só
  leitura, `flow:<id>@1`).
  - A precedência é skill publicada → fluxo ativo → nada (o planejador).
  - `flows` não é reescrito. A adoção de um fluxo desliga o fluxo na mesma transação, e nunca há escrita dupla.
- **Flag próprio (P1):** `skills.enabled`, padrão `false`. `ai.flows` continua mandando no fluxo legado como hoje.
  Publicar uma skill não liga nada sozinho.
- **Hash canônico próprio:** chaves ordenadas e separadores fixos, e não `db.dumps`, que não ordena.

**Consequências.**

- Nenhuma transição afeta execução em curso: o plano dela fica em `runs.plan`.
- O rollback não revalida: o conteúdo é o mesmo, e as observações continuam valendo.
- A unicidade de comando entre skills e fluxos não cabe no banco. Quem garante é o repositório, ao publicar e ao
  adotar.
- A trilha antiga se lê sem migração, e `skill_hash` nulo quer dizer "não se sabe".
- Guardas que ficam para a fiação G2 (design §18): adoção com o flag desligado, `PUT /api/flows/{id}` religando um
  fluxo adotado, e `GET /api/flows/match` passando a respeitar `ai.flows`.
- A 046 só entra na produção depois de verde em PostgreSQL (`workflow_dispatch`). O deploy de 042–046 exige ensaio em
  cópia (ADR-020) e autorização.
- O ADR-037 (proposto no design) fica só com o que resta da compatibilidade: descompilador `Plan → DSL`, rota v1 → v2
  e conversão dos fluxos (fase J). Os comentários de `refs.py`, `registry.py`, `legacy_flows.py` e
  `sql_repository.py` citam o ADR-037 para o registro com dois backends, que este ADR registra.

**Evidências.**

- Em SQLite (`simulated`):
  - `backend/tests/test_habilidades_dominio.py`: transições estado × estado, hash, congelamento no domínio, P4;
  - `backend/tests/test_habilidades_repositorio.py`: congelamento sem o gatilho, publicação e rollback, comando único,
    adoção e desfazer, segredo fora das tabelas;
  - `backend/tests/test_habilidades_legado.py`: `flow:<id>@1` igual ao `FlowStore`, precedência e interruptores;
  - `backend/tests/test_habilidades_migracoes.py`: 041 → 046 sem tocar o legado, esquema igual em banco novo e
    atualizado, índices e gatilho recusando.
- A transição `draft → candidate` foi exercida com o validador falso (`backend/tests/fake_skills.py`) e, desde a
  fase G, com o de produção (`modules/skills/infrastructure/document_validator.py`, em
  `backend/tests/test_fatia_abrir_conversa.py`).
- PostgreSQL com 042–046: verde no CI em `793fe00` (run 36324634678, `simulated`), depois de corrigir o dado do
  teste (K-029). Nada implantado.

**Relação.** ADR-007; ADR-020; ADR-032; ADR-033; [`dominios/skills.md`](dominios/skills.md);
[`banco.md`](banco.md).

---

## ADR-035 — ResourceSpec declarativo

**Data:** 27/09/2026 · **Estado:** vigente; `apply`, `verify` e `reconcile` implementados e **não ligados** ao `_tick`
· **Decisão técnica** dentro do pedido de evolução arquitetural (27/09). Código da fase H, parte 1: `14362ee`,
`37752ed` e `ab211e6`, integrados em `1e69d02`. Parte 2: `9f76832`, `80d5fc7` e `373d45f`, integrados em `2fc09b2`.

**Contexto.**

- O que uma execução precisa do mundo (aparelho no ar, app na versão promovida, perfil vinculado, sessão pronta) mora
  nas portas do `Scheduler._tick` e do `AppState`, que leem e agem no mesmo passo:
  - `AppState._app_resolver` grava a versão desejada;
  - `Scheduler._portas_do_app` dispara trabalho no aparelho;
  - as lambdas que `AppState._session_gate` devolve autenticam.
- Sem uma leitura sem efeito, não dá para mostrar à pessoa o que seria feito antes de fazer (PLAN, design §14.2).
- A DSL já declara `spec.resources` (ADR-033), e o compilador os valida (`E_RESOURCE_UNKNOWN_KIND`,
  `E_RESOURCE_CONFLICT`). Nada os consumia.

**Escolha.**

- **Estado desejado como valor.** `shared/resources.py`: `ResourceSpec(ref, desired, on_missing)`, `Drift` com sete
  estados (`in_sync`, `diverged`, `pending`, `held`, `unknown`, `blocked`, `unsupported`) e `ResourceAction`
  (`observe`, `converge`, `ask`). Mora no kernel porque três contextos pares implementam os tipos e a execução consome
  os quatro (D5).
- **`read_current_state`, `diff` e `plan` são sem efeito.** A leitura é só `SELECT` ou memória; `diff` e `plan` são
  funções puras do domínio de cada contexto.
- **Quatro providers iniciais, sobre o que já existe:** `DeviceStateProvider` (fleet), `AppInstallationProvider`
  (applications), `AccountBindingProvider` e `AppSessionProvider` (identity). As regras de leitura do legado que moram
  em métodos que também gravam são refeitas sem efeito.
- **`unknown` nunca vira `in_sync`.** A resposta a `unknown` é ler, por um comando de leitura que já existe
  (`app.verify`, `session.verify`); sem ele, nenhuma ação. Valor lido fora do vocabulário vira `unknown`
  (`shared/resources.py::known`), nunca o estado mais parecido. No `PlanReport`, par não lido é `unknown` com código
  `not_read`, sem ação, listado nos riscos.
- **Observado = desejado ⇒ zero ações**, e a mesma entrada dá o mesmo plano.
- **Só verbo de comando que existe** (`commands/despacho.py`). O que nenhum comando faz vira `ask`. Vincular perfil é
  sempre de pessoa; entrega que falhou, desafio e conta errada também.
- **Regras do parque mantidas:** mais nova e não voltada fica (`held`, ADR-026); espalhar app é Distribuir, de pessoa;
  login só com credencial utilizável no cofre (ADR-025); desafio e 2FA com a pessoa (ADR-009, ADR-029).
- **`PlanReport`** (`modules/execution/domain/plan_report.py`): puro e determinístico, com o JSON canônico das skills.
- **`apply` só por `commands`** (cerca, outbox, diário: R10), pela porta `shared/commands.py::CommandBus`. A porta mora
  no kernel, e não em execução, porque os providers de fleet, applications e identity a consomem, e execução já
  depende dos três: seria ciclo. `modules/execution/application/ports.py` a reexporta, ao lado de
  `ResourceProvider`, agora `Protocol`. A implementação (`modules/execution/infrastructure/command_bus.py::DespachoCommandBus`)
  vai pelo caminho que já existe para cada verbo, sempre por `commands/despacho.py`: `pedir_ciclo_de_vida`
  (`start`/`wake`), `AppState._entregar` (`app.install`), `ReleaseService.verify_on` (`app.verify`) e `ensure_session`
  (`session.connect`/`session.verify`).
- **Uma vez por chave.** A chave de idempotência é determinística: `res:<aparelho>:<tipo>:<alvo>:<verbo>#n`
  (`shared/commands.py::key_prefix`). Com o último comando em voo ou `uncertain`, o `apply` devolve o mesmo; a chave
  única de `commands` desempata a corrida. **`uncertain` nunca se repete.**
- **`on_missing` decide quem dispara.** Só `apply` converge por comando; `wait` deixa com o rodízio e as portas do
  `_tick`; `ask` é pessoa. Ler (`observe`) vale para os três. Antes de pedir, o `apply` relê e replaneja.
- **`account.binding` nunca aplica**: vincular é decisão de pessoa, e o `apply` recusa ação de pessoa (`ValueError`).
- **`verify` só prova com leitura positiva** (`in_sync` sobre o que acabou de ser lido).
- **`reconcile` só no hospedeiro** (`CommandBus.hosts`, o `so_meu`: R11), e só fecha `uncertain` como `succeeded` com
  leitura **posterior** ao comando. Sem prova, o incerto continua incerto; nunca vira falha.
- **Recusa antes de gravar** o que o despacho recusaria depois de gravar (aparelho ocupado, fora do ar, em manutenção,
  verbo não declarado). `requested_by = "recursos"`, nunca `system`, para não contar como degrau da escada de reparo.
- **`PlanReport` servido em `mode=plan`** (`POST /api/runs`, aditivo, com `source`) e `objectives.resource_plan`
  gravado no `materialize` (`RunService._fotografar_recursos`).
- **Proposto:** ligar o `apply` e o `reconcile` no `_tick` (muda quem dispara: decisão do dono); refotografar
  `resource_plan` no despacho; os demais campos do §14.2 no relatório.

**Consequências.**

- O runtime usa só a leitura: `POST /api/runs` com `mode=plan` serve o relatório, e o `_plan` grava a foto. **Nada
  chama `apply` nem `reconcile`** fora dos testes: nem o `_tick`, nem uma rota. O comportamento em execução não mudou,
  e as portas do `_tick` continuam sendo o "apply" desses recursos.
- `on_missing: wait` e `apply` planejam a mesma convergência; a diferença (quem dispara) vale no `apply`, que ainda não
  é chamado.
- A porta `ResourceProvider` difere da §7 do design em três pontos, pelo código de hoje: recebe o `ResourceSpec` (e não
  só o `ResourceRef`); `apply` e `reconcile` são síncronos, como o despacho; `reconcile` devolve o que fechou e o que
  continua incerto.
- Fora de `source: skill`, o `plan_report` vem sem recursos e com `ready_to_run: true`: quer dizer "nada declarado", não
  "pronto". Falha ao montar o relatório vira `source: "error"`, e a execução criada volta normalmente.
- O plano é mais estrito que a porta de hoje em dois casos, de propósito: instalação sem desfecho (`verifying`) e
  sessão não verificada respondem com leitura (`app.verify`, `session.verify`), e a porta atual age direto.
- `desired_state = stopped` não bloqueia: o rodízio liga sob demanda, e o relatório avisa.
- `test_arquitetura.py` ganhou a camada `kernel` (`app.shared`), e o mypy estrito passou a cobri-la.

**Evidências.** Todas `simulated`:

- `backend/tests/test_recursos_declarativos.py`: tabelas de `diff`/`plan` por recurso, vocabulários iguais aos do
  legado e da DSL, verbos contra `LIFECYCLE_ACTIONS`/`APP_COMMAND_VERBS`,
  `::test_unknown_nunca_vira_certo_nem_convergencia`, idempotência e segunda passada;
- `backend/tests/test_leitura_de_recursos.py`: leitura dos quatro providers sobre um banco de teste, sem gravar
  nada, e `device.state` sobre o `DeviceRuntime` do harness;
- `backend/tests/test_plan_report.py`: o relatório da `ig.abrir_conversa` sobre dois aparelhos, a segunda passada sem
  ação, a mesma entrada em outra ordem com o mesmo hash, par não lido como risco;
- `backend/tests/test_arquitetura.py::test_contextos_novos_formam_um_dag`.
- Em PostgreSQL: os mesmos testes passaram no CI em `793fe00` (run `36324634678`, `workflow_dispatch`), que já
  continha o merge `1e69d02` (`simulated`).
- Parte 2 (`simulated`): `backend/tests/test_aplicacao_de_recursos.py` (20 testes: uma vez por chave, `uncertain` não
  se repete, `on_missing`, replanejamento, vínculo nunca vira comando, `verify`, `reconcile` só no hospedeiro e com
  prova posterior; quatro deles pelo despacho de verdade no harness, porta 5640) e
  `backend/tests/test_plan_report_na_execucao.py` (4 testes: `mode=plan` sem comando, planejador, a foto em
  `mode=execute`, `source: "error"`). Suíte SQLite 2293/2293 no branch da parte 2.
- Parte 2 em PostgreSQL (em especial a consulta com `substr(idempotency_key, 1, CAST(? AS INTEGER))` de
  `DespachoCommandBus`), com aparelho real e ligada ao `_tick`: `not_run`.
- Produção: `not_run`. Nada implantado.

**Relação.** ADR-009; ADR-010; ADR-025; ADR-026; ADR-029; ADR-033;
[`dominios/execution.md`](dominios/execution.md#recursos-declarativos-fase-h-parte-1),
[aplicar, verificar e reconciliar](dominios/execution.md#recursos-aplicar-verificar-e-reconciliar-fase-h-parte-2);
[contrato, adendo v0.24](api-contract.md#adendo-v024-27092026--plan_report-em-modeplan-e-corpos-fora-de-modelspy).

---

## ADR-036 — Receitas como estratégia de execução

**Data:** 27/09/2026 · **Estado:** vigente (trilha e regras); `RecipeExecutionStrategy` proposta · **Decisão técnica**
dentro do pedido de evolução arquitetural (27/09). Código: `4e210c4` (trilha, fase G, integrada em `0b736f3`) e
`eb9ba02` (seletor com o username sem arroba). Complementa o ADR-007.

**Contexto.**

- A receita (ADR-007) é a sequência de seletores que a IA ensinou uma vez e o `Replayer` repete. Até a fase G, não
  havia como saber que tentativa a receita resolveu e qual a IA pagou: `steps.driven_by` é o veredito da etapa, e
  `ai_calls` só apontava a etapa.
- O ADR-032 separou o quê (capability) do como (`StrategyKind`), e a DSL deixa o nó declarar `strategies` (`recipe`,
  `ai_actor`, `human`).
- Na fatia da fase G (27/09), a receita de `OPEN_THREAD` gravava o username sem arroba como texto literal e, reproduzida
  para outra pessoa, tocava a conversa errada (K-037).

**Escolha.**

- **A receita é uma estratégia, não uma skill.** É fonte de decisão para uma etapa, dentro da tentativa. Validação da
  ferramenta, guardas de commit, intenção antes de agir e verificação continuam no executor
  (`taskqueue/recipes.py`, docstring do módulo). Nenhuma estratégia decide sucesso.
- **Trilha (045):**
  - `attempts.strategy` guarda a cadeia **exercida**: `recipe`, `ai_actor` ou `recipe>ai_actor` quando a receita
    diverge e a IA assume na mesma tentativa (`StepExecutor._registrar_estrategia`, no `finally` de `run_step`);
  - `attempts.recipe_id`, a receita reproduzida, só quando `recipe` está na cadeia;
  - `steps.strategy`, a cadeia **planejada** (`origin.strategies` da skill); nula no planejador;
  - `ai_calls.attempt_id`, a tentativa que pagou cada chamada;
  - `steps.driven_by` fica como está, porque o painel o tipa como união fechada.
- **`human` nunca aparece em `attempts.strategy`.** `waiting_user` é desfecho, não estratégia exercida.
- **A receita nunca repete commit.** Ela não é consultada quando o efeito da etapa já disparou em qualquer tentativa
  (`Repository.commit_state` na entrada de `run_step`; `not fired` no laço de `_run_step`), e o commit dela passa
  pelas mesmas guardas que o da IA. `type_secret` e `open_url` nunca se reproduzem
  (`taskqueue/recipes.py::UNSAFE_TO_REPLAY`, ADR-025).
- **Identidade da receita:** pacote, versão do app, assinatura, variante de interface e a etapa em forma de modelo
  (`RecipeStore.find`; `step_template_hash`: `template_key or key`, efeito, pós-condição e guardas de commit). O nó de
  skill usa `PlanStep.key == node_id`, então a receita casa pela chave. Limite aceito até a fase K: o mesmo
  `OPEN_THREAD` avulso (`abrir_conversa`) e composto (`abrir_abrir_conversa`) não compartilham receita.
- **Seletor com o username sem arroba** (`eb9ba02`): texto de seletor **inteiro** igual ao valor de um parâmetro sem a
  arroba (três caracteres ou mais) vira `{parâmetro}` (`recipes.py::_usable_text`), e a reprodução aceita as duas
  grafias (`_match`, `_formas`), como as provas locais (`proofs.variantes_de_arroba`). Pedaço de nome nunca vira
  parâmetro.
- **Proposto:** `RecipeExecutionStrategy` e `AiActorStrategy` atrás de `ExecutionStrategy`, com a ordem de
  `origin.strategies`. Hoje a ordem do executor é fixa (receita, se aplicável, e IA), e `origin.strategies` é gravado
  sem comandar.

**Consequências.**

- Custo e modelo se atribuem à tentativa, e a tentativa resolvida pela receita se distingue da que a IA pagou.
- `attempts.strategy` e `ai_calls.attempt_id` são gravados em toda execução, com ou sem skill: a 045 precisa estar
  aplicada antes do código (precondição de deploy, [execution](dominios/execution.md#precondição-de-deploy)).
- Em `ai.recipes = shadow`, a receita só é comparada, e a tentativa registra `ai_actor`.
- A correção vale para receita aprendida depois de `eb9ba02`. Uma receita já gravada com o username literal continua
  literal: reproduzida para outra pessoa, toca a mesma conversa de antes, e a verificação recusa, como antes da
  correção.

**Evidências.** Todas `simulated`:

- `backend/tests/test_fatia_abrir_conversa.py::test_abrir_conversa_pela_skill_publicada_sem_planejador_e_com_a_trilha`:
  na primeira execução, `ai_actor` sem receita e toda chamada com `attempt_id`; na segunda, `recipe` com o
  `recipe_id` aprendido e zero `decide`;
- `::test_com_as_habilidades_desligadas_o_comando_vai_ao_planejador`: a estratégia exercida é gravada também sem skill;
- `backend/tests/test_recipes.py::test_seletor_com_username_sem_arroba_vira_parametro_e_reproduz_para_outra_pessoa`.
- PostgreSQL e produção: `not_run`. Nada implantado.

**Relação.** ADR-007; ADR-025; ADR-032; ADR-033; K-037; [`dominios/execution.md`](dominios/execution.md#estratégias);
[`dominios/capabilities.md`](dominios/capabilities.md).

---

## ADR-037 — Compatibilidade com o Flow legado

**Data:** 27/09/2026 · **Estado:** vigente; conversão em lote dos fluxos de produção proposta · **Decisão técnica**
dentro do pedido de evolução arquitetural (27/09), design §15. Código da fase J: `9d2b736`, `4ddba1a`, `fa21cec` e
`c4f40d6`, integrados em `5b1957f`.

**Contexto.**

- O registro com dois backends (`flow:<id>@1`), a precedência skill → fluxo → planejador e a adoção na mesma transação
  já estavam no ADR-034, e a guarda de `PUT /api/flows/{id}` (409 `flow_adopted`) entrou na fase G. Este ADR ficou,
  proposto, com o resto (design §20).
- A adoção dava uma v1 publicada **igual** ao fluxo, mas nada editável: a habilidade nascida de um fluxo não tinha
  documento da DSL.
- Ainda havia dois caminhos de escrita que deixavam o mesmo comando vivo como fluxo ativo e habilidade publicada:
  salvar o treino (`FlowStore.learn_from_plan`) e religar o fluxo pela rota quando a habilidade publicada era **outra**.
- A execução da v1 adotada gravava só a skill. O histórico do fluxo (capacidades do perfil, aproveitamento, "usos")
  sumia da vista justo quando ele passava a ser usado.

**Escolha.**

- **Descompilador `Plan → automation/v1alpha1`** (`modules/skills/infrastructure/decompiler.py`), com a identidade de
  receita como regra: `node_id = PlanStep.key`, e o plano compilado do documento tem os mesmos campos que
  `recipes.step_template_hash` lê.
  - Não confia em si: confere o documento pelo **compilador real** da execução (`SkillRunPlanner.compiler`), sem
    valores e com valores de amostra contra o plano do fluxo ligado aos mesmos valores.
  - Diferença de identidade ou de comportamento é erro (`E_ROUNDTRIP`); texto que o catálogo reescreveu é aviso
    (`W_ROUNDTRIP`); o que a v1alpha1 não expressa é erro com o caminho no plano (`E_RUNTIME_VARIABLE`,
    `E_UNREPRESENTABLE`). Códigos próprios, fora do vocabulário fechado do compilador.
- **Converter = adotar + rascunho, numa transação** (`SqlSkillRepository.convert_flow`): v1 publicada com o plano do
  fluxo, fluxo desligado, e v2 `draft` com o documento descompilado da v1 já gravada (`parent_version` 1, proveniência
  `legacy_flow` + `decompiled_from`). Erro da ida e volta recusa tudo, e nada fica.
- **Desfazer, numa transação** (`undo_conversion`): `release_flow` e os rascunhos da conversão ainda em `draft`
  apagados, com a referência do ensino conferida antes. O rascunho que saiu de `draft` e a definição ficam.
- **v1 → v2** para quem adotou antes da fase J: `POST /api/skills/{id}/versions/{n}/decompile`.
- **Rotas atrás de `skills.enabled`** (404 `skills_disabled`): `POST /api/flows/{id}/adopt` e `/release`. As rotas
  antigas de fluxo ficam com o contrato, mais um 409.
- **Um comando, um dono:** `learn_from_plan` recusa comando publicado (409 `duplicate_command` na rota do treino), e
  `PUT /api/flows/{id}` religando confere qualquer habilidade publicada com o comando (409 `command_published`), com a
  conferência e a escrita numa transação.
- **Trilha da v1 adotada:** a versão cujo conteúdo **é** o plano do fluxo (`schema_version` 0) grava a skill **e**
  `runs.flow_id`, e conta em `flows.used` (`RunPlan.flow_id`). A v2 é outro plano e grava só a skill. As capacidades
  do perfil ganham a lista `skills`.
- **Painel:** converter e desfazer por fluxo, e as transições de versão, atrás de `features.skills`.

**Consequências.**

- Converter não muda nenhuma execução: a v1 é o plano do fluxo byte a byte. Só a v2, depois de publicada, muda o
  plano, e a bateria legado × novo mostra que não muda o que importa (plano, identidade de receita, conta de IA).
- `skill_id` e `flow_id` preenchidos numa execução querem dizer "o plano do fluxo, rodado pela habilidade";
  `flow_id` sem `skill_id` continua sendo `flow:<id>@1`.
- **Riscos conhecidos:**
  - fluxo com argumento literal e texto em modelo é recusado (`learn_from_run` não templatiza `bindings`); quantos
    fluxos de produção têm essa forma não foi medido: ler `flows.plan` antes de converter em lote;
  - fluxos do QA Messenger não convertem (`{account_label}` no texto);
  - depois de desfazer, `DELETE /api/flows/{id}` continua 409 `flow_adopted`;
  - reconverter reusa o número do rascunho apagado (`_next_version` = `MAX(version) + 1`);
  - no PostgreSQL (READ COMMITTED), duas escritas concorrentes ainda podem passar as duas conferências de comando
    único; no SQLite, o `BEGIN IMMEDIATE` as serializa.
- **Proposto:** converter os fluxos ativos de produção, um a um e com decisão do dono, depois de medir os riscos acima
  nos planos reais.

**Evidências.** Todas `simulated`:

- `backend/tests/test_descompilador.py`: ida e volta de seis fluxos no formato de produção (mesmas etapas,
  parâmetros e `step_template_hash`), os erros `E_ROUNDTRIP`, `E_RUNTIME_VARIABLE` e `E_UNREPRESENTABLE` e o aviso
  `W_ROUNDTRIP`;
- `backend/tests/test_conversao_de_fluxo.py`: conversão e recusa sem resto, desfazer idêntico, v1 → v2, rotas ligadas
  e desligadas, e o comando único por caminho de escrita;
- `backend/tests/test_equivalencia_fluxo_skill.py`: `[legado|novo]` com o mesmo plano, as mesmas receitas e a mesma
  conta de IA (1ª execução: plan 0, decide 4, verify 1; 2ª: decide 0),
  `::test_receitas_aprendidas_pelo_fluxo_servem_a_habilidade_convertida` e a trilha da v1 adotada;
- `frontend/src/features/settings/FlowsRecipesSection.test.tsx`: converter, desfazer, transições e recusas na linha;
- suíte SQLite 2306 no branch da fase (relatado pelo coordenador).
- PostgreSQL, fluxos reais de produção e conferência visual: `not_run`. Nada implantado.

**Relação.** ADR-007 (receitas e fluxos); ADR-033 (DSL); ADR-034 (versionamento, adoção);
[`dominios/skills.md`](dominios/skills.md#conversão-de-fluxo-fase-j);
[`skill-runtime.md`](skill-runtime.md#descompilador-plan--documento-fase-j).

---

## ADR-038 — Máquinas de estado de execução formais: conferir antes de impor

**Data:** 27/09/2026 · **Estado:** vigente (fase "conferir e registrar"); impor proposto · **Decisão técnica** dentro
do pedido de evolução arquitetural (27/09), linha `set_run_status`/`set_objective` da §16 do design. Código: `48e76ae`
(fase K2), integrado em `b56e06c`.

**Contexto** ([design](design/evolucao-arquitetural.md) §2.4).

- Só duas máquinas eram formais: a etapa (`taskqueue/states.py::STEP_TRANSITIONS`, imposta por `check_transition` em
  `Repository.transition_step`) e o comando (`commands/states.py::COMMAND_TRANSITIONS`).
- A execução aceitava qualquer alvo em `Repository.set_run_status`, e `recompute_run` reabre execução terminal sem
  tabela que o declare.
- O objetivo era escrito por `set_objective` a partir de muitos chamadores; a tabela implícita estava espalhada por
  `Scheduler._apply`, `RunService` e `recompute_run`.
- A tentativa não tinha tabela; `revise_plan` grava `skipped` direto em `steps`, fora de `transition_step`.
- Impor uma tabela escrita de cabeça poderia travar a produção numa transição legítima que ninguém listou.

**Escolha.**

- **Tabelas no domínio de execução, puras.** `modules/execution/domain/states.py`: `RUN_TRANSITIONS` (10 estados),
  `OBJECTIVE_TRANSITIONS` (7), `STEP_TRANSITIONS` (11) e `ATTEMPT_TRANSITIONS` (6), imutáveis, sem banco e sem
  `app.models`, e `MaquinaDeEstados.pode(de, para)`. Cada aresta diz qual chamador a produz: a tabela é derivada do
  comportamento **atual**, não desenhada.
- **Reafirmação (`x → x`) só onde o código a faz:** execução em `cancelling`, `completed_with_issues` e `cancelled`;
  objetivo `failed`.
- **Reabertura registrada como é**, para ser revista, não aprovada: `completed_with_issues → running, paused` e
  `cancelled → running, paused` (`recompute_run`).
- **Uma fonte para a etapa.** `taskqueue/states.py::STEP_TRANSITIONS` passa a ser derivada da tabela do domínio e
  continua **imposta**.
- **Duas fases, como a §16 manda: primeiro conferir e registrar, depois impor.** Agora,
  `Repository._conferir` roda em `set_run_status`, `set_objective`, `finish_attempt` (depois da cerca) e na escrita
  direta de `revise_plan`. Fora da tabela: evento `log` de nível `warn` com `{state_machine, entity_id, from, to}` e
  contagem em `repository.py::TRANSICOES_FORA_DA_TABELA`. **A transição acontece do mesmo jeito.**
- **A suíte prova a tabela.** O fixture automático `tests/conftest.py::_transicoes_dentro_da_tabela` reprova qualquer
  teste cuja execução produza transição fora da tabela; teste que a force de propósito devolve a contagem.
- **Proposto:** impor. Depois de um ciclo sem aviso na suíte e na produção, `set_run_status`, `set_objective` e
  `finish_attempt` trocam o aviso por `InvalidTransition`, como a etapa já faz, e cada aresta de reabertura é decidida.

**Consequências.**

- Nenhuma execução real muda: a etapa já era imposta, e as outras três só avisam.
- Uma transição nova no código precisa de aresta na tabela, senão a suíte reprova. Um estado novo no enum sem linha na
  tabela também (`test_maquinas_de_estado.py::test_vocabulario_igual_ao_do_enum`).
- `claim_step` (`ready → running` e o nascimento da tentativa) e `Scheduler._reconciliar` (tentativa
  `running → interrupted`) não passam por `_conferir`: estão na tabela por construção, pelo `WHERE status=` da própria
  escrita.
- A contagem é por processo e **ainda não aparece em `/api/health`**. Em produção, só o evento `log` avisa: impor
  exige antes torná-la visível.
- Dois testes que chegavam ao estado final por atalho passaram a seguir o caminho real (abaixo).

**Evidências.** Todas `simulated`:

- `backend/tests/test_maquinas_de_estado.py` (15 testes): vocabulário igual ao dos enums, todo estado alcançável do
  nascimento, `pode` com enum e texto, `x → x` só onde está escrito, as arestas que o domínio garante, a fila impondo
  a mesma tabela de etapa, tabela imutável, e execução e objetivo fora da tabela que avisam, contam e não bloqueiam;
- medição antes da mudança, com um coletor sobre a suíte inteira (2270 testes): 3.585 transições reais em 55 pares
  (máquina, de, para), dois fora da tabela, ambos atalho de arranjo de teste, não caminho de produção
  (mensagem de `48e76ae`):
  - `tests/test_capabilities.py` chamava a porta de política sem o `_hold` que o despacho aplica (objetivo
    `running → pending`);
  - `tests/test_credenciais_da_execucao.py` levava a execução de `planned` direto a `completed_with_issues`; agora
    passa por `running`;
- o fixture `tests/conftest.py::_transicoes_dentro_da_tabela` em toda a suíte, depois da correção.
- PostgreSQL, produção e a contagem observada em produção: `not_run`. Nada implantado.

**Relação.** ADR-010 (a máquina do comando, que já era imposta); ADR-030 (domínio puro);
[`dominios/execution.md`](dominios/execution.md#máquinas-de-estado-fase-k2);
[`arquitetura.md`](arquitetura.md#fila-runs--objectives--steps--attempts).

---

## ADR-039 — Manifesto de app e registro de SessionProvider

**Data:** 27/09/2026 · **Estado:** vigente; a sessão por (perfil, app) que este ADR propunha é `account_sessions`, por
(conta, aparelho), desde a 049 (ADR-040) · **Decisão técnica** dentro do pedido de
evolução arquitetural (27/09), linha "cluster Identity, comparações com `"instagram"`" da §16 do design. Código da fase
K1: `0b7950e`, `99d851b`, `40def91`, `01d68b5`, `15dfded` e `88087d9`, integrados em `f06e34a`, mais a correção
`3fbe9df`.

**Contexto** ([design](design/evolucao-arquitetural.md) §2.5).

- O registro `planning/catalog` guardava só o **nome** do provedor de sessão, e o núcleo o resolvia comparando com
  `"instagram"`: a porta de sessão, a invalidação ao mexer no disco de um app, o "login automático" do painel e a
  correção da sessão pelo executor (`cfg.file.instagram.package`).
- `state.py` importava o `InstagramAuthenticator` e os extratores de tela, criava `self.instagram` concreto e mantinha
  `_TIPO_DE_TEXTO` e `_LEITURA_DE_CONVERSA` com chaves do catálogo do Instagram.
- As regras do ADR-029 moravam no autenticador do Instagram, e o núcleo o importava só para aplicá-las.
- Um segundo app com conta gerenciada exigiria editar o núcleo.

**Escolha.**

- **Manifesto declarativo no domínio:** `modules/applications/domain/definition.py::AppDefinition` (pacote, nome,
  rótulo, tipo do provedor de sessão, perfil, internet, e os mapas de tipo de texto e de leitura de conversa).
  `AppCapabilities` é o mesmo tipo pelo nome antigo.
- **As peças com comportamento no manifesto de infraestrutura:** `registry.py::AppManifest` = definição + catálogo +
  `ScreenReader` + fábrica do provedor de sessão. `register_manifest` é a única porta de entrada; o registro recusa
  fábrica sem tipo declarado e catálogo de outro pacote.
- **Registro movido** para `modules/applications/infrastructure/registry.py`; `planning/catalog/__init__.py` virou
  shim com os mesmos objetos. Os embutidos são pares (módulo, atributo), e o pacote é lido do manifesto: nenhum literal
  de app no registro.
- **Porta `SessionProvider`** (`modules/identity/application/ports.py`: `package` e `ensure_session`) e **registro por
  pacote** `SessionProviders` (`modules/identity/application/sessions.py`): fabrica o provedor na primeira pergunta
  com as dependências da composição (`SessionDeps`) e devolve a mesma instância enquanto a fábrica for a mesma.
- **Regras de sessão do perfil em `modules/identity/application/session_rules.py`**: o bloqueio do ADR-029 e o evento
  "precisa de pessoa", com o rótulo do app vindo de quem chama.
- **O Instagram é a primeira implementação** (`integrations/instagram/manifesto.py`), com o `InstagramAuthenticator`
  de sempre como provedor.
- **Nenhuma comparação com `"instagram"` no núcleo**, travada por AST (`backend/tests/test_apps_fora_do_nucleo.py`),
  com a lista de exceções vazia. Fora do escopo: `app/integrations/**`, `app/planning/catalog/**` e `app/config.py`.
- **Desvios do desenho (§7 e §16):**
  - `classify` ficou fora da porta: nenhum código do núcleo o consumiria sem mudar comportamento;
  - leitores de tela e fábrica de sessão ficam no manifesto de infraestrutura, não no domínio (regra D2);
  - **um registro de sessão por perfil**: o perfil guarda a sessão do app de `social_repo.app_package`, e um app com
    login gerenciado por perfil é o que existe hoje;
  - a checagem "tela contradiz a sessão" do executor vale para qualquer app com provedor, não só para o pacote do
    Instagram. Em produção é idêntico: só o Instagram tem provedor;
  - 409 novo `no_session_provider` em "Conectar" e "Verificar conta", inalcançável hoje.

**Consequências.**

- Comportamento idêntico para o Instagram: os testes de sessão, desafio e ADR-029 seguiram verdes sem mudar asserção.
- Um app novo = manifesto + provedor + catálogo, num `register_manifest`, sem tocar no núcleo. Provado só com o QA,
  registrado em teste; em produção o QA segue no caminho livre, e o Instagram é o único embutido.
- `AppState.instagram` ficou como propriedade de compatibilidade: o nome antigo do provedor do perfil, o mesmo objeto.
- O que continua com "instagram" no núcleo e **não** é comparação: `package_of_provider("instagram")` (em `state.py`
  e `social/service.py`: o perfil é a conta de um app só), os caminhos `/api/instagram/profiles/*`, os nomes de tabela
  (`instagram_profiles`, `instagram_sessions`), `InstagramCfg` em `config.py`, e a regex genérica de desafio
  (`automation/hierarchy.py::_DESAFIO`, conferida contra `integrations/instagram/navigation.SIGNALS`).
- A correção `3fbe9df`: H2 e K1 correram em paralelo, e os recursos (`modules/execution/infrastructure/providers.py`,
  `command_bus.py`) ainda comparavam com `"instagram"` e chamavam `s.instagram`. Agora o manifesto diz se o app tem
  login automático, e o registro entrega o provedor; sem provedor montado, recusa explícita.
- **Respondido pela onda B (ADR-040):** a sessão é de `account_sessions` por (conta, aparelho); falta só o provedor
  receber a conta em vez do perfil, antes de um segundo app com login gerenciado no mesmo perfil (item 12.3 do
  plano-100, decisão do dono).

**Evidências.** Todas `simulated`:

- `backend/tests/test_app_novo_pelo_manifesto.py`:
  `::test_app_novo_entra_so_pelo_registro_com_provedor_e_catalogo`,
  `::test_skill_do_qa_compila_e_executa_pelo_caminho_de_skills` e
  `::test_processo_cross_app_instagram_e_qa_num_aparelho_so` (harness na porta 5640; dublês em
  `backend/tests/fake_dois_apps.py`);
- `backend/tests/test_apps_fora_do_nucleo.py`: o detector, rodado contra a árvore de `578fe36`, acha as seis
  comparações que a K1 tirou; na árvore de agora, nenhuma (mensagem de `01d68b5`);
- `backend/tests/test_dubles_cumprem_as_portas.py`: os dublês do QA e o `InstagramAuthenticator` contra
  `SessionProvider`;
- `backend/tests/test_instagram_auth.py` e `test_sessao_com_validade.py`, sem mudar asserção;
  `test_registro_de_apps.py` e `test_capabilities.py` mudaram só no acesso (`_BUILTINS` em pares; `TIPO_DE_TEXTO` lido
  do manifesto);
- suíte SQLite 2279 no branch da fase (relatado pelo coordenador).
- PostgreSQL, app real novo e conta real: `not_run`. Nada implantado.

**Relação.** ADR-009; ADR-025; ADR-029 (as regras que mudaram de casa); ADR-030 (regras D2/D3 e o DAG de contextos);
[`dominios/apps-e-loja.md`](dominios/apps-e-loja.md#manifesto-de-app-fase-k1);
[`dominios/perfis-e-instagram.md`](dominios/perfis-e-instagram.md#sessionprovider-e-o-registro-por-pacote-fase-k1).


## ADR-040 — A credencial pertence à conta da persona e a execução não carrega credencial

**Data:** 27/09/2026 · **Estado:** vigente, implantado em 28/09 (`07fce91`) ·
**Substitui em parte:** ADR-025 (a credencial deixa de ser da execução; o resto do ADR-025 continua) · **Decisão
técnica** dentro da segunda evolução ([design](design/persona-e-parque.md) §3.2, §4, §10.3, §14 itens 4 e 5). Código
da onda B: `2ca5344` e `78136db` (049), `ad0cab6` (repositório e serviço), `3b5088b` (dados disponíveis e
`type_secret` por conta), `2f0952b` (rotas por conta), `f2f4684` (conta âncora só na escrita), `155f1b1`, integrados
em `4b95592` (e `f72da91` depois do merge das ondas A, B e D).

**Contexto.**

- O ADR-025 amarrou a credencial à **execução**: campo `credentials` de `POST /api/runs`, cofre por execução,
  `run_secrets`, 409 `consentimento_de_credencial` a cada envio. Funcionou para o caso do portal (`22d65f`), mas a
  senha morria com a execução e a pessoa a redigitava a cada comando.
- Desde a 037 existia `account_credentials` **sem leitor**: a senha da conta de um app comum era gravada e ninguém a
  digitava; a do Instagram morava em `instagram_credentials`, com estado, falhas e bloqueio que a outra não tinha.
- Duas fontes de sessão: `instagram_sessions` (uma por perfil, gravada pelo provedor) e
  `profile_accounts.session_status` (marcação da pessoa), com vocabulários diferentes (`logged_out` só num deles).
- Conta de portal ou site não tinha casa: no navegador o `app` é o Chrome, e "qual site recebe esta senha" vinha da
  URL escrita no comando, não de um dado da persona.
- Pedido do dono: a persona é a pessoa, com **contas** (app ou site), cada uma com a sua credencial; "sem soluções
  paralelas".

**Alternativas** (design §14 item 5; relatório 03).

- (A) Remover `RunCreate.credentials`/`consent_credentials` e o campo do painel; fonte única é a credencial da conta.
- (B) Manter `credentials` só por API, ao lado da credencial da conta, para um uso avulso.

**Escolha.** (A). Duas fontes para a mesma senha é exatamente a solução paralela que o dono não quer, e a conta de
portal ganha casa própria (`host`).

- **Migração `049_contas_unificadas.sql`.** `account_credentials` ganha `status`, `failed_attempts`, `blocked_until`,
  `created_at`, `last_used_at` e o consentimento por conta (`consent_at`, `consent_by`); `profile_accounts.host`,
  unicidade `(profile_id, app_id, COALESCE(host, ''))`; **`account_sessions` com chave (conta, aparelho)** e
  vocabulário único (`unknown | session_ready | auth_required | auth_challenge | wrong_account | needs_person`;
  `logged_out` da 037 vira `auth_required`); `authentication_attempts.account_id`. Carga: a credencial do Instagram
  vira a da conta Instagram do perfil com o **mesmo `secret_ref`** (nada é recifrado) e `consent_by = 'migração 049'`;
  a sessão só onde há vínculo ativo com o aparelho (sessão de aparelho que o perfil não tem mais não vem); conta
  âncora `acc-<perfil>` criada quando faltava; tentativas apontadas para a conta. Idempotente
  (`INSERT … SELECT … WHERE NOT EXISTS`).
- **Uma escrita, leitura compatível.** `social/repository.py::SocialRepository` lê e grava credencial e sessão nas
  tabelas de conta mantendo as assinaturas por `profile_id` (`credential_row`, `set_credential`, `session_row`,
  `set_session`) sobre a **conta âncora** (`conta_ancora`: a conta do perfil no app que provê a conta dele; criada
  só na escrita e só para perfil com `@`). `security/rekey.py::recifrar` atualiza `account_credentials.key_id`;
  `apps_overview.py` conta prontas por `account_sessions` no aparelho vinculado. `instagram_credentials`,
  `instagram_sessions` e `run_secrets` ficam **só leitura** até uma migração posterior; enquanto a linha legada
  apontar para a mesma referência, apagar a credencial da conta não apaga o segredo do cofre
  (`SocialService._apagar_credencial`).
- **Consentimento por conta.** `PUT …/accounts/{aid}/credential {password, login_identifier?, consent: true}`;
  sem `consent` numa conta que ainda não consentiu, 409 `consentimento_de_credencial`
  (`SocialService._gravar_credencial`). `POST …/credential/consent` marca sem redigitar. O consentimento é da
  **conta**, não de uma senha: trocar a senha o preserva. Vale para o `type_secret` **e** para o provedor de
  sessão (`integrations/instagram/authentication.py::InstagramAuthenticator._blocked_reason` e a porta de sessão
  `state.py::AppState._session_gate`): sem a marca, ninguém digita.
- **Catálogo de dados disponíveis** (`modules/identity/domain/available_data.py`, função pura; porta
  `application/account_ports.py::ProfileDataStore`; adaptador `infrastructure/profile_data.py::SqlProfileDataStore`,
  só `SELECT`). Por perfil: `perfil_nome`, `perfil_sobrenome`, `perfil_nome_exibicao`, `perfil_nascimento`,
  `perfil_email` (lista fechada `PROFILE_FIELDS`, só os que têm valor) e, por conta, `conta_<app>[_<host>]_usuario`
  (texto) e `conta_<app>[_<host>]_senha` (**sigiloso**: só existe como nome; oferecido só com credencial guardada,
  consentida e de app sem `SessionProvider`). O contexto social não conhece a porta: não há caminho dele ao cofre.
- **O que a IA recebe.** `PlanRequest.available_data` e `StepContext.available_data` (`planning/provider.py`): a
  lista comum a todos os aparelhos da execução para o planejador livre e por catálogo (`common_data`), a lista do
  aparelho para o ator e o verificador; o bloco `dados_block` em `planning/prompts.py` leva nome, rótulo e tipo,
  nunca valor. `Repository.materialize` resolve `{perfil_email}` e `{conta_<app>_usuario}` por aparelho
  (`profile_variables`, fotografadas em `instances[].variables` no planejamento).
- **`type_secret(name)` por conta** (`taskqueue/executor.py::StepExecutor.preenchedor`): resolve pelo perfil do
  **objetivo** (`resolve_secret`), exige `consent_at`, digita só campo de senha, só no **pacote da conta** e, no
  navegador, só no **`host` da conta** (ou subdomínio; conta de navegador sem `host` não recebe a senha em site
  nenhum), grava `last_used_at` e registra na execução qual conta entrou (nome, nunca valor). A referência da conta
  nunca passa por `run_secrets`. `tem_credencial` de `pede_intervencao_humana` vale por app e etapa
  (`typable_secret_for`); senha guardada sem consentimento põe a etapa em `waiting_user` com `consentimento_pendente`.
- **`open_url`** aceita, além dos endereços do comando, os hosts das contas de portal da persona
  (`ToolContext.allowed_hosts`, `automation/tools.py::_no_site_da_conta`).
- **Pré-voo** confere `requires.secrets` da skill (`RunPlan.secrets`) contra as contas da persona de cada aparelho
  (`RunService.pre_voo(secret_names=…)` → `missing_secrets`): o que falta recusa o aparelho com `missing_credential`.
- **A execução não carrega credencial.** `RunCreate.credentials` e `consent_credentials` saíram (`extra="forbid"`:
  cliente antigo recebe 422); `_exigir_consentimento`, `_guardar_no_cofre` e `add_run_secret` saíram de
  `taskqueue/service.py`; `run_secret_refs`, `drop_run_secrets` e `purge_stale_run_secrets` saíram de
  `taskqueue/repository.py`. 409 `credencial_no_comando` continua.
- **Rotas por conta** (`api.py`): `PUT/DELETE …/accounts/{aid}/credential`, `POST …/credential/consent`,
  `POST …/accounts/{aid}/session/{connect|verify|logout}`, `GET …/accounts/{aid}/auth-attempts`. As antigas por
  perfil (`/credential`, `/connect`, `/verify`, `/logout`, `/auth-attempts`) viram **apelidos da conta âncora**
  (`_conta_ancora_ou_409`; perfil sem conta no app âncora → 409 `no_account`). `ProfileAccountDTO` ganha
  `login_identifier`, `host`, `credential` (só metadados), `consent_at`, `session`, `session_actions`.
- **Instagram sem exceção arquitetural.** App com `SessionProvider` continua com login determinístico **antes** da
  tarefa e fora do `type_secret` (`AccountRecord.managed`); o provedor lê a credencial e a sessão da conta pelas
  assinaturas de sempre.

**Invariantes preservadas do ADR-025.** O valor nunca vai ao modelo, a log, evento, evidência, memória, fixture ou
Git; digitação só pelo canal sensível; três travas (campo de senha, app, site); credencial lida na tela ou inventada
nunca é digitada; desafio, 2FA com código não fornecido e CAPTCHA seguem com a pessoa (ADR-009); comando com formato
de segredo é recusado antes de gravar.

**O fluxo, em oito passos.**

1. A pessoa guarda a senha na conta da persona com `consent: true` (ou consente depois, sem redigitar).
2. `POST /api/runs` sem credencial; senha no texto continua 409 `credencial_no_comando`.
3. Pré-voo: `requires.secrets` da skill casada contra as contas da persona de cada aparelho.
4. Planejamento: a IA recebe a lista de dados disponíveis comum aos aparelhos (nomes, rótulos, tipos).
5. Materialização: `{perfil_email}` e `{conta_<app>_usuario}` resolvidos por aparelho.
6. Execução: o ator conhece só os nomes; `type_secret(name)` resolve pelo perfil do objetivo, confere consentimento,
   pacote e host, digita pelo canal sensível.
7. Registro: `last_used_at` na conta e uma decisão na execução com o nome da conta usada.
8. App com provedor de sessão autentica antes da tarefa, exigindo o mesmo consentimento. Fim: nada é apagado.

**Consequências.**

- O painel ainda mostra o campo "Senha para a automação" (`features/command/CommandPanel.tsx`) até a onda E: quem
  digitar nele recebe 422 de `POST /api/runs`. A guia "Contas e acesso" da persona é da onda E.
- Rotas antigas continuam respondendo o perfil, como sempre; o painel de hoje não quebra.
- `profile_accounts.session_status` fica na tabela (migração aplicada não se edita) e ninguém a lê nem escreve.
- Tabelas só leitura até migração posterior: `instagram_credentials`, `instagram_sessions`, `run_secrets`.
- **Desvios do design** (§4, §10.3): arquivos chamam-se `049_contas_unificadas.sql`,
  `test_migracao_contas_unificadas.py`, `available_data.py` (não `049_conta_unica.sql`, `test_conta_unica_migracao.py`,
  `dados.py`); o catálogo tem só os cinco campos de perfil (sem `perfil_idade`, `perfil_cidade` etc.); a rota de
  credencial fica em `/instagram/profiles/{id}/accounts/{aid}/credential`, não em `/personas/…`;
  `ensure_session` segue por `profile_id` (o provedor acha a conta âncora pelo perfil); a senha do cadastro
  (`ProfileCreate`) grava `consent_by = 'cadastro do perfil'` sem marca explícita;
  `invalidate_sessions_of_instance` só invalida o app âncora por padrão; `teaching.py:350,500` e
  `generalization.py:192` ainda dizem "campo Credenciais da execução".

**Evidências.**

- `simulated`: `backend/tests/test_migracao_contas_unificadas.py` (credencial sem recifrar e só sessão com vínculo;
  idempotência; banco novo = atualizado; unicidade por perfil, app e host; cascata; os dois dialetos),
  `backend/tests/test_credenciais_da_conta.py` (substitui `test_credenciais_da_execucao.py`: a execução não aceita
  mais credencial; senha só com consentimento, também pelo apelido por perfil; dados disponíveis listam nomes e
  nunca valores; a lista é a comum a todos os aparelhos; variável resolvida por aparelho; `type_secret` só campo de
  senha, só pacote e host da conta, exige consentimento; Instagram fora do `type_secret`; `open_url` aceita o site
  da conta; pré-voo recusa aparelho sem a credencial exigida), `backend/tests/test_contas_unificadas_api.py` (DTO
  com credencial, consentimento e sessão; marcar sessão sem aparelho é 409; rotas por conta e apelidos). Suíte
  SQLite do branch: 2433 passed, 2 flakes (`test_instalacao_do_worker`, verdes isolados); suíte do merge: ver
  CHANGELOG.
- Os números de produção (8 credenciais, 8 sessões das quais 5 sem vínculo ativo) são leitura de 27/09 (design
  §2.7). **Ensaio `real` da 049 (28/09, madrugada; máquina `WIN-7S2UASNLFOP`, código `073475c`)** numa cópia nova de `data/backups/20260927-222357`, junto com 047, 048 e 050: 8 credenciais de conta com os mesmos 8 `secret_ref` de `instagram_credentials` e consentimento datado; 3 sessões em `account_sessions` (android-01 `unknown`, android-03 e android-06 `session_ready`; as 5 fantasmas dos perfis bloqueados não vieram); 19 tentativas apontadas para a conta; `host` presente; 8 contas e nenhuma com handle vazio (o primeiro ensaio criou 6 contas vazias para as personas sem conta — corrigido na própria 049 antes de ir à `main`); `integrity_check` ok, `foreign_key_check` vazio, idempotente.
- PostgreSQL: `not_run` (GitHub Actions bloqueado por cobrança, K-040). Login real com credencial da conta, o portal
  `22d65f` e produção: `not_run`.

**Relação.** ADR-009; ADR-025 (o que fica); ADR-029; ADR-039 (a sessão por (perfil, app) que ele propunha é a de
`account_sessions`); ADR-041; [`dominios/persona.md`](dominios/persona.md#contas-e-acesso);
[`dominios/perfis-e-instagram.md`](dominios/perfis-e-instagram.md#contas-por-app-item-121);
[`dominios/execution.md`](dominios/execution.md); [`api-contract.md`](api-contract.md#adendo-v028-27092026--conta-única-credencial-com-consentimento-e-sessão-por-conta-e-aparelho-adr-040);
[`banco.md`](banco.md).

## ADR-041 — A persona é a pessoa: instagram_profiles como raiz, personas dobrada, username opcional por string vazia e reconstrução com foreign_keys off

**Data:** 27/09/2026 · **Estado:** vigente, implantado em 28/09 (`07fce91`); conta única
feita (onda B, ADR-040) · **Decisão técnica** dentro da segunda evolução ([design](design/persona-e-parque.md)
§3, §10, §14 itens 1 e 2). O número 040 fica reservado para a credencial da conta (onda B), por isso o índice pula
de 039 para 041. Código da onda A: `462d724` (047), `469baae` (modelo e rotas), `6dcbdc5` (geração), integrados em
`8c19d5a`.

**Contexto.**

- Havia duas tabelas para uma coisa só: `personas` (voz: resumo, traços, prompt) e `instagram_profiles`
  (identidade, conta, vínculo, política). Uma persona podia existir sem perfil (11 órfãs em produção) e um perfil sem
  persona (os 5 bloqueados do ADR-029). O dono pediu "a persona é a pessoa": modelo rico (biografia, visual), imagens
  e, adiante, várias contas e vários aparelhos por pessoa.
- **Todo** consumidor (vínculos, sessões, credenciais, memória, aprovações, `objectives.profile_id`, escopo de skill,
  treinamento, rotas) já é por `profile_id`.
- Produção: 8 perfis, 14 personas, 8 credenciais, 8 sessões, 8 vínculos, esquema da 008 **antiga**
  (`username TEXT NOT NULL UNIQUE COLLATE NOCASE` inline, lido em `sqlite_master` em 27/09).

**Alternativas** (design §14, itens 1 e 2).

- (a) Manter as duas tabelas, com o perfil dono de nome e nascimento e a persona dona da biografia (relatório 01,
  §8.i): dois donos para a mesma pessoa.
- (b) Mudar a raiz para `personas.id`: rechavearia todos os consumidores.
- Modelo: tudo em colunas (rígido); tudo em JSON sem tipo (sem validação, `extra="forbid"` não protege);
  `profile_attributes(key, value)` (bom para catálogo, ruim para leitura de tela).
- `username` opcional **sem** reconstruir a tabela (design §10.1, risco R1): `''` + índice parcial, só `ALTER`.

**Escolha.**

- **A persona é a linha de `instagram_profiles`** (nome de tabela mantido; renomear quebraria ~30 pontos e o pacote
  do agente, migração 037). Migração `047_persona_e_a_pessoa.sql`: colunas `summary`, `traits` (só voz),
  `persona_prompt`, `gender`, `locale`, `biography`, `visual`, `generation`.
- **Modelo rico híbrido** (`models.py`): identidade em colunas; `PersonaTraits` (15 traços de voz), `PersonaVisual`,
  `PersonaBiography` por seção com `schema_version`, `PersonaGeneration`; idade calculada (`idade_em`), nunca
  gravada; `PATCH` **por seção** (`mesclar_secao`); `beliefs.religion`/`politics` guardados e **não enviados** ao
  modelo; `PERSONA_BIO_FIELDS` é a fonte única do que da biografia vai ao prompt, como `PERSONA_VOICE_TRAITS` é da
  voz.
- **`username` opcional pela convenção `''`**: coluna `NOT NULL DEFAULT ''`, índice único parcial
  `lower(username) WHERE username <> ''`, tradução `''` ↔ `null` na borda (`repository.campos_de_persona`).
- **Reconstrução no SQLite com `-- @foreign_keys:off`** (`app/db.py::_SEM_CHAVES`, `_sem_chaves_estrangeiras`),
  contra o R1 do design: a UNIQUE de coluna da 008 antiga não sai com `ALTER` e recusaria a segunda pessoa sem conta
  só em produção. Medido antes da 047 que, com `foreign_keys=ON`, o `DROP TABLE` disparava a cascata das filhas;
  a diretiva desliga a chave **fora** da transação, confere `PRAGMA foreign_key_check` **antes** do `COMMIT`
  (violação desfaz a migração) e religa no `finally`. No PostgreSQL só `ALTER`.
- **Dobra de `personas`** em três grupos: (a) perfis com `persona_id` copiam resumo, voz e prompt; (b) órfãs casadas
  por nome (`lower(trim)`) com perfis sem persona ganham o `persona_id` (menor id no empate); (c) as demais viram
  pessoas `ig-<persona_id>` com `username = ''`. `traits.appearance/visual_style/photo_scenario` migram para
  `visual`; `biography` nasce com `tastes.interests` e `approx_age` (os dois dígitos antes de " anos" no resumo);
  `generation.source = 'legacy_persona'` é o predicado de idempotência. A FK `persona_id → personas` fica e
  `personas` fica sem leitores (sai depois, com outra reconstrução).
- **`/api/personas` canônico**; `/api/instagram/profiles*` continua para conta, credencial e sessão.
  `GET /instagram/profiles` lista só quem tem conta. `ProfileCreate.persona_id` adota a pessoa sem conta;
  `ProfilePatch.persona_id` absorve; outra pessoa com conta → 409 `persona_in_use`.
- **Geração por IA** pelo papel `social` (`generate_persona` em todos os provedores), com rascunho validado no
  domínio (`problemas_do_rascunho`: adulta, nome fictício plausível, voz e biografia completas, nenhum texto com cara
  de segredo) e enriquecimento que só preenche o vazio (`preencher_vazios`).
- **Porta de sessão por existência de conta**: pessoa vinculada sem conta no app é espera por pessoa, não erro
  (`AppState._tem_conta_no_app`).

**Consequências.**

- Um objeto, quatro nomes: linha, `PersonaVoiceDTO`, `PersonaDTO`, `InstagramProfileDTO` (o mesmo objeto).
- Desvios relatados: `persona_name` mantido; `persona_id` do DTO é o próprio `id`; travas de vínculo/execução só no
  `DELETE`; `draft_response(app_id=)` existe e o executor não o passa; `dados_disponiveis` do §13 não existe;
  `generation` da dobra é `{source: legacy_persona, persona_id}` e o id das pessoas novas é `'ig-' || persona.id`
  inteiro (o design dizia outra coisa nos dois).
- O painel de hoje continua funcionando: `PersonaTraitsEdit` aceita as chaves visuais dentro de `traits` até a onda E.
- Deploy: a 047 reconstrói a tabela de produção; exige ensaio (`scripts/deploy.ps1 -Ensaio`), backup e autorização
  do dono; o checkout de produção não pode dar `git pull` (design R20).
- **Feito depois:** conta única (onda B, ADR-040). **Proposto:** remoção de `personas` e da FK; `beliefs` no prompt
  (decisão do dono);
  as 5 fotos dos bloqueados anexadas às pessoas dobradas (decisão do dono, reversível).

**Evidências.**

- `simulated`: `backend/tests/test_persona_migracao_047.py` (os três grupos e as filhas; FKs sobrevivem à
  reconstrução; `username` opcional e único; o esquema **real** da produção; banco novo = banco atualizado; empate;
  os dois dialetos), `test_persona_unificada.py`, `test_persona_geracao.py`; suíte SQLite 2459/2459 no branch.
- `real` (27/09): ensaio da 047+048+050 numa **cópia** do backup de produção `data/backups/20260927-222357`
  (máquina e commit a confirmar pelo coordenador): seis tabelas filhas byte a byte iguais (8 vínculos, 8 sessões,
  8 credenciais, 24 memórias, 8 contas, 19 tentativas), `integrity_check` ok, `foreign_key_check` vazio, 14 pessoas
  (3 ativas, 5 bloqueadas dobradas por nome, 6 sem conta), `visual` em 8, segunda aplicação sem efeito.
- PostgreSQL: `not_run` (GitHub Actions bloqueado por cobrança em 27/09). IA real e produção: `not_run`.

**Relação.** ADR-029 (os 5 perfis bloqueados que a dobra casa por nome); ADR-030 (regras D2/D3: domínio puro em
`modules/identity/domain/persona*.py`); ADR-039 (a nota "sessão por (perfil, app) proposta" passa a ser respondida
pela onda B); ADR-042; [`dominios/persona.md`](dominios/persona.md);
[`dominios/perfis-e-instagram.md`](dominios/perfis-e-instagram.md#a-persona-é-a-pessoa);
[`banco.md`](banco.md#diretiva-foreign_keysoff-reconstrução-de-tabela-pai-no-sqlite).

## ADR-042 — Imagens de persona: receita determinística, porta ImageGenerator, simulado primeiro, OpenAI atrás de chave, custo em ai_calls.usd

**Data:** 27/09/2026 · **Estado:** vigente, implantado em 28/09 (`07fce91`); provedor local proposto · **Decisão técnica**
dentro da segunda evolução ([design](design/persona-e-parque.md) §5 e §14 item 3). Código: `e68c506`, integrado em
`8c19d5a`.

**Contexto.**

- O dono disse que a imagem da persona é obrigatória e que as fotos devem refletir os atributos e variar entre
  pessoas. Havia 8 avatares soltos em `data/avatars/<profile_id>.jpg`, sem registro, sem custo e sem proveniência.
- O saldo da API da Anthropic não compra imagem; a API de imagens não devolve custo; e "sem fallback pago
  silencioso" é a primeira regra do hub de IA.

**Alternativas** (design §14 item 3; relatório 05): Gemini, BFL, Replicate; provedor local (ComfyUI) primeiro;
`on_create: false`.

**Escolha.**

- **Tabela `persona_images`** (`048_imagens_da_persona.sql`): receita (`spec`), `seed`, proveniência (`provider`,
  `model`, `provider_request_id`, `original_key`), custo, estado (`pending|ready|failed|refused`), `source`
  (`generated|upload|imported_legacy`), uma principal por pessoa (índice parcial), cascata da pessoa. E
  `ai_calls.usd`: custo **declarado** por unidade, somado por `costs.spent_usd` ao lado dos tokens.
- **Receita determinística** (`modules/identity/domain/persona_image.py`): semente
  `sha256(f"{persona_id}:{indice}:{SPEC_VERSION}")[:4] & 0x7FFFFFFF`; identidade fixa (aparência, estilo, cenário,
  paleta, interesses, idade, gênero, profissão, cidade) e eixos sorteados (câmera, época, luz, ambiente,
  enquadramento, pose, produção, proporção, pós-processamento); imagem 0 = principal, busto, 1:1; **o nome nunca
  entra no prompt**; "fictional adult, no text, no logo, no watermark, not a real person" em toda receita; abaixo de
  18 anos não há receita.
- **Porta `ImageGenerator`** fora dos papéis de IA (`application/ports.py`), com dois adaptadores
  (`adapters/simulated_images.py`, `adapters/openai_images.py`) e o serviço de aplicação
  (`application/persona_images.py::PersonaImageService`) só sobre portas.
- **Simulado primeiro**: Pillow determinístico, sem chave, nada sai da máquina, carimbo "SIMULADO". **OpenAI atrás de
  chave**: `gpt-image-1-mini`, `OPENAI_API_KEY` como `SecretStr`, preço por imagem declarado em
  `ai.image.price_per_image[quality]` (sem preço, o mais caro; nunca zero), teto do dia conferido **antes** de pedir;
  pago sem chave → 409 `image_not_configured`; recusa do filtro → `refused`; falha → `failed`; nunca se cai para o
  simulado.
- **Referência**: da segunda imagem em diante a principal vai ao provedor (`/v1/images/edits`) para o rosto se
  manter; é o único dado além de atributos que sai.
- **Pós-processamento honesto** (`adapters/pos_processamento.py`): recorte, redução, ruído, desfoque e JPEG variável
  para variação; sem EXIF inventado, sem apagar proveniência; o original do provedor fica em `original_key`.
- **`on_create: true`, `per_persona: 1`** (o relatório 05 propunha `false`; o dono disse que a imagem é obrigatória).
- **Avatares legados** importados na partida (`AppState._importar_avatares_legados`), idempotente, como principal;
  o avatar da rota serve a principal.
- **Local adiado** (GPU de 8 GB, ADR-023); publicar a foto no app fora do escopo.

**Consequências.**

- Custo de imagem aparece no mesmo relatório e no mesmo teto do dia; `GET /api/usage` e `/api/desempenho` ainda não
  leem `usd` (só `spent_usd`).
- `GET /api/ai` expõe `image` (`AiImageStatus`), para a aba IA dizer quem gera, se há chave e quanto custa.
- Dependências novas no contexto de identidade: Pillow e httpx só nos adaptadores (regra D2).
- **A validação do dono ("as imagens refletem os atributos e variam entre personas") só é `real` com um provedor
  pago configurado e autorização de gasto.** O simulado prova fiação, determinismo, custo e estados, não fidelidade.

**Evidências.**

- `simulated`: `backend/tests/test_persona_imagens.py` (semente e receita determinísticas; mesmos pixels para a
  mesma semente; OpenAI `generations`/`edits` e erros por `httpx.MockTransport`; gera, guarda, registra custo e
  define a principal; referência, teto, recusa e falha; upload, principal, apagar e avatar legado; custo declarado no
  gasto do dia; migração 048; rotas e avatar; importação na partida).
- `real`: a 048 aplicada no ensaio sobre a cópia do backup de produção `20260927-222357` (27/09), com a 047.
- OpenAI real, imagem real, produção e PostgreSQL (Actions bloqueado por cobrança): `not_run`.

**Relação.** ADR-023 (modelo local); ADR-030 (D2/D3); ADR-041;
[`dominios/persona.md`](dominios/persona.md#imagens-persona_images-migração-048); [`ia.md`](ia.md#1-as-cinco-funções);
[`banco.md`](banco.md).

## ADR-043 — Persona N:N aparelho: vínculo por app, aparelho principal e uma conta por app em cada aparelho

**Data:** 28/09/2026 · **Estado:** vigente, implantado em 28/09 (`07fce91`) · **Decisão técnica** dentro da segunda
evolução ([design](design/persona-e-parque.md) §7, §10.5, §14). Código da onda C (`2051f88`, `9bb4139`). Doc
principal: [`dominios/persona.md`](dominios/persona.md#aparelhos-e-roteamento); contrato no
[adendo v0.29](api-contract.md#adendo-v029-28092026--persona-nn-aparelho-e-roteamento-por-persona).

**Contexto.**

- O vínculo persona × aparelho era 1:1 por dois índices únicos parciais da 008 (um perfil ativo por aparelho, um
  aparelho ativo por perfil); vincular tomava o aparelho de quem estivesse nele.
- O dono pediu a hierarquia Servidor → Aparelho → Persona(s), com uma persona em vários aparelhos e várias personas
  num aparelho, visível dos dois lados.
- O 1:1 estava embutido em muitos chamadores (porta de sessão, localidade, treino, contexto operacional,
  reobservação depois de intervenção).

**Alternativas.** Tabela nova de vínculo (perde localidade e histórico); troca automática de conta no Instagram
dentro do mesmo aparelho (achado #115, frágil); usuários Android múltiplos no emulador.

**Escolha.**

- `device_profile_bindings` evolui (migração 051): `app_id` (NULL = apps sem conta gerenciada) e `is_primary`;
  saem os índices 1:1; entram um vínculo ativo por (persona, aparelho, app), **uma conta por app em cada aparelho**
  (D2-a: duas contas do mesmo app no mesmo aparelho ficam proibidas enquanto a troca de conta no Instagram for
  manual) e no máximo um principal por persona.
- `bind` não toma aparelho de ninguém: colisão é 409 `conta_do_app_ja_no_aparelho`. Um vínculo sem app de uma
  persona que tem conta no app conta para a regra.
- A sessão continua em `account_sessions` (conta × aparelho), sem tabela nova; a mesma conta em N aparelhos é
  permitida (D3), protegida pelo ADR-029.
- O **principal** é o alvo padrão de conectar, verificar, sair e do contexto operacional; `?instance_id=` escolhe
  outro aparelho vinculado.
- **Ordem segura:** primeiro todos os chamadores passaram a APIs de lista e ao perfil do objetivo, com os índices
  1:1 ainda valendo (fase 1, suíte verde); só depois a 051.

**Consequências.**

- `profile_id_for_instance`/`binding_row` ficam só por compatibilidade e levantam erro com mais de um vínculo
  (nunca escolhem em silêncio); a porta de sessão recebe a persona do objetivo.
- O teste "mover Mariana desvincula Lucas" foi aposentado de propósito: vincular não toma mais.
- O painel mostra as N personas de um aparelho e os N aparelhos de uma persona (onda E).

**Evidências.** `simulated`: `backend/tests/test_vinculos_n_n.py` (050→051 com o retrato de produção, esquema novo
= atualizado, uma persona em dois aparelhos, dois aparelhos com personas diferentes, recusa D2-a),
`backend/tests/test_personas_aparelhos_api.py`. `real` e PostgreSQL: ver o ensaio da implantação e K-040.

**Relação.** ADR-029 (sessão bloqueada quando o Instagram pede verificação); ADR-035 (vincular é de pessoa);
ADR-041; ADR-044.

## ADR-044 — Roteamento das execuções por persona: alvos resolvidos, destinos no texto e prévia obrigatória

**Data:** 28/09/2026 · **Estado:** vigente, implantado em 28/09 (`07fce91`) · **Decisão técnica** dentro da segunda
evolução ([design](design/persona-e-parque.md) §11, §12). Código da onda C (`0c6ab56`). Doc principal:
[`dominios/persona.md`](dominios/persona.md#aparelhos-e-roteamento).

**Contexto.** "Peça para o André fazer X" deveria escolher o aparelho sozinho; a execução só sabia ir para
aparelhos, e `profile_ids` substituía `instance_ids` em vez de estreitá-los. O dono também quer que o texto diga os
destinos ("no aparelho Y", "nos aparelhos Y e Z") sem perder o caminho direto por aparelho.

**Alternativas.** Endpoint separado "por persona" (forquilha do fluxo de execução); extrair destinos por LLM;
`all` como política padrão.

**Escolha.**

- `RunCreate` estendido, sem forquilha: `targets: [{profile_id, instance_ids[], app_id?}]`,
  `device_policy: one|primary|all` (padrão `one`); `profile_ids` com `instance_ids` passa a ser **interseção**;
  `targets` e `distribute` são exclusivos.
- `resolver_alvos` puro (`modules/execution/application/alvos.py`), com a precedência **interface > texto >
  vínculos > balanceamento**: o texto só estreita a seleção; contradição ou homônimo vira pergunta (`needs_input`,
  sem plano); com `one`, sessão pronta num aparelho apto > principal > balanceamento.
- `TargetExtractor` determinístico (`modules/execution/application/target_extractor.py`, sem IA) acha "com a
  persona X", "como @user", "pelo/pela <nome>", "no(s) aparelho(s) Y e Z", `android-NN`, e tira o destino do
  comando (`command_sem_destinos`) antes da resolução de intenção.
- **Prévia** `POST /api/runs/targets/resolve` (não grava, não planeja); destino tirado do texto só executa depois
  de ecoado em `targets` (409 `alvos_nao_confirmados`).
- A foto dos alvos fica em `runs.targets` (051); o objetivo grava a persona do alvo.

**Consequências.**

- Nesta fase o mesmo aparelho não entra duas vezes numa execução (409 `aparelho_repetido_na_execucao`, mantém
  `UNIQUE(run_id, instance_id)`).
- Execuções antigas, sem `runs.targets`, re-resolvem pelo aparelho.
- O extrator roda no `RunService` e não como etapa da resolução de intenção; `/flows/match` e `/skills/resolve`
  passam pelo mesmo corte.

**Evidências.** `simulated`: `backend/tests/test_roteamento_por_persona.py` (tabela de casos),
`test_alvos_no_texto.py` (frases golden), `test_roteamento_execucao.py` (por persona com dois aparelhos, `all`
cria dois objetivos, 409 do aparelho repetido, planejador chamado o mesmo número de vezes).

**Relação.** ADR-043; ADR-027; o balanceamento de `Scheduler.candidatos_de`.

## ADR-045 — Provisionamento de aparelho pela plataforma: local agora, remoto depois

**Data:** 27/09/2026 · **Estado:** vigente, implantado em 28/09 (`07fce91`); remoto
proposto (ADR próprio) · **Decisão técnica** dentro da segunda evolução ([design](design/persona-e-parque.md) §8,
§10.4, §14 item 8). Código da onda D integrado em `11e985e`, com a correção `c59baed`. Doc principal:
[`dominios/parque.md`](dominios/parque.md#provisionamento-pela-plataforma-2709-onda-d); contrato no
[adendo v0.26](api-contract.md#adendo-v026-27092026--provisionar-e-aposentar-instância-pela-plataforma).

**Contexto.**

- Uma instância nascia só de três jeitos: da configuração (`instances.count`), da adoção de um aparelho anunciado
  pelo worker, ou do verbo `create` sobre uma instância já declarada. Criar um aparelho novo exigia editar o
  `config.yaml` e reiniciar a produção.
- O dono pediu "criar aparelho" pelo painel. O inventário do agente remoto é estático (`worker.yaml`), e ampliar o
  protocolo do worker é decisão à parte (ADR-031).

**Alternativas** (design §14 item 8; relatório 02): fazer local e remoto de uma vez; só local agora.

**Escolha.** Só local agora; o remoto é rodada própria, com ADR.

- **Migração `050_provisionamento.sql`**: `instances.android_overrides` (JSON por instância: `system_image`,
  `ram_mb`; nulo = configuração), `instances.retired_at`, `worker_limits.max_devices` (teto de aparelhos hospedados
  por servidor; nulo = sem teto).
- **`POST /api/instances`** (`DeviceManager.provisionar`; corpo `InstanceProvisionBody`,
  `modules/fleet/presentation/schemas.py`, `extra="forbid"`): insere a linha com `origin='dynamic'`,
  `worker_id = hosted_by = OWNER_ID`, id depois de `count` (`_proximo_id_dinamico`, nunca reaproveitado), portas por
  `MAX(idx)+1`; `DeviceManager.android_de(rt)` mescla `android_overrides` sobre `cfg.instance_android` em todos os
  usos. Com `create: true` (padrão) o comando `create` sai pelo despacho de sempre (cerca, outbox,
  `idempotency_key`) e a rota responde **202** com `command_id`; `start: true` encadeia a partida só depois do
  `create` `succeeded`; `create: false` responde **201** com a linha.
- **Guardas**: worker remoto (`provisionamento_remoto_indisponivel`), `teto_de_aparelhos` (`max_devices`),
  `disco_insuficiente` abaixo de `provisioning.min_free_disk_gb` (padrão 10, lido da última batida) ou
  `disco_desconhecido`, `chave_ja_usada`, `servidor_nao_hospeda`, e as recusas do pré-voo do `create`.
- **`DELETE /api/instances/{id}`** aposenta só instância dinâmica, local, desligada, sem objetivo aberto, sem
  vínculo ativo e sem comando em voo: apaga o AVD (`AvdManager.delete`) e marca `retired_at`; a linha, o `idx` e as
  portas ficam, porque o histórico dos objetivos referencia o id. Aposentada some das listas e não volta no arranque.
- **`max_devices`** entra em `GET/PUT /api/servers/limits` e **não** vai na mensagem `Limits` ao agente.

**Consequências.**

- Aparelho novo sem editar YAML e sem reiniciar; `seed()` recarrega as linhas `dynamic`.
- **Desvios do design** (§8, §10.4): a resposta com comando é 202 (o design dizia 201); os códigos são
  `teto_de_aparelhos`/`disco_insuficiente` (não `sem_vaga_de_aparelho`/`sem_disco`); o piso de disco é
  `provisioning.min_free_disk_gb` = 10 (não `limits.min_free_disk_gb_per_device` = 12); `profile_id` e o `bind` na
  mesma chamada não existem; `retired_at` não estava no SQL do §10.4; a aposentadoria confere vínculo, objetivo e
  comando em voo, não `account_sessions`.
- O remoto (verbo `provision` no protocolo, inventário mutável no agente, mapa do túnel) fica para ADR próprio,
  com autorização do dono (design §15 item 9).

**Evidências.**

- `simulated`: `backend/tests/test_provisionamento.py` (instância dinâmica criada, `create` fechado e sobrevive ao
  reinício; teto do servidor; disco insuficiente ou desconhecido; worker remoto e corpo inválido; `start` só depois
  do `create`; aposentar apaga o AVD, some das listas e não volta; recusas de aposentar; teto pela tela de limites
  sem ir ao agente), `backend/tests/test_provisionamento_migracao.py` (050 não toca o que havia; banco novo =
  atualizado; os dois dialetos).
- `real` (27/09): a 050 aplicada no ensaio sobre a **cópia** do backup de produção `20260927-222357`, máquina
  `WIN-7S2UASNLFOP`, código `8c19d5a`, junto com 047 e 048 (ver [persona](dominios/persona.md#migração-de-dados-047)).
- Criar ou apagar um AVD de verdade no host de produção, e PostgreSQL (Actions bloqueado por cobrança, K-040):
  `not_run`.

**Relação.** ADR-015 (limites por servidor); ADR-026; ADR-031 (o protocolo que o remoto vai ampliar); ADR-035
(vincular é de pessoa); [`dominios/parque.md`](dominios/parque.md#provisionamento-pela-plataforma-2709-onda-d);
[`banco.md`](banco.md).

## ADR-046 — Contrato de página e faixas por container query; Foco em seções com grupos de ação puros

**Data:** 28/09/2026 · **Estado:** vigente, implantado em 28/09 (`07fce91`) · **Decisão técnica** dentro da segunda
evolução ([design](design/persona-e-parque.md) §9, §14). Código da onda E1 (`7631231` contrato de página,
`1ae49e0` Configuração, `62d937d` `focusActionGroups`, `1713132` Foco em seções, `c919d91` ajustes do aceite; onda E2 `c311d25`, `6636961`, `f0c3bff`).
Doc principal: [`produto.md`](produto.md); aceite visual em
[`auditoria-ux-2026-09-27/evo2-aceite.md`](auditoria-ux-2026-09-27/evo2-aceite.md).

**Contexto.**

- A Configuração era a única tela encaixotada (`pageNarrow`, 1280 px): sobrava cerca de 30 % da tela vazia, e as
  grades internas quebravam em larguras intermediárias. Cada tela resolvia largura e colunas do seu jeito, com
  estilos inline e `@media` pela janela.
- O painel de Foco encolhe o `main` quando abre: uma regra por largura da **janela** acerta com o Foco fechado e
  erra com ele aberto (a tela tem 1366 px, mas a página tem ~800).
- O Foco era uma coluna longa de blocos sem hierarquia, com ações destrutivas misturadas às de rotina.

**Alternativas.** Corrigir a Configuração pontualmente; manter `@media` e subtrair a largura do Foco por variável
(variável CSS não entra em `@media`/`@container`); biblioteca de layout externa.

**Escolha.**

- **Contrato de página** (`components/Page.tsx`): `Page` (largura total, sem variante estreita), `PageHeader`,
  `PageSection` (Card com CardHeader e rodapé opcional para barras de salvar), `TableWrap` (rolagem horizontal com
  cabeçalho fixo), `AutoGrid` (`repeat(auto-fill, minmax(min(100%, var(--col-min)), 1fr))`).
- `.page` é contêiner (`container-name: page`) e as faixas de layout são `@container page`, medidas pela largura
  da página (o `main` menos o Foco): compacto < 560, médio 560–959, largo 960–1439, ultra ≥ 1440, escritas como
  contrato em `styles/tokens.css` e repetidas literais nas regras.
- **Foco em seções** (`FocusSection`: título, selo de estado, recolhível) na ordem identidade → estado e saúde →
  servidor → tarefa → personas → contas → apps → sessão → ações → recolhidos (comandos recentes, detalhes técnicos,
  hierarquia).
- **Ações do Foco por função pura** (`features/devices/deviceState.ts::focusActionGroups`): grupos Controle, Ciclo de
  vida, Apps, Controle manual, Observação e **Zona de perigo** no fim, com borda de perigo; verbos não suportados
  em "Indisponíveis (n)" com o motivo; tudo bloqueado pelo comando em voo como no cartão.
- **Aceite de layout no navegador**, não só no teste: 375, 1024, 1366 e 1920 px, com o Foco aberto e fechado,
  capturas guardadas no repositório.

**Consequências.**

- Tela nova entra no contrato em vez de inventar largura; as grades respondem ao espaço real da página.
- O Foco vira tela cheia abaixo de 720 px; com 600 px de painel ou mais, ele usa duas colunas.
- Testes de integração que clicam por nome e `role=tab` continuam valendo: rótulos preservados de propósito.

**Evidências.** `simulated`: `frontend/src/components/Page.test.tsx`, os testes de `focusActionGroups` e das telas
tocadas (vitest), `npm run typecheck` e `npm run build`. Visual: as capturas da onda E1 contra um backend simulado,
em `docs/auditoria-ux-2026-09-27/capturas/evo2/`. Produção: `not_run` até a implantação.

**Relação.** ADR-040 (o campo de senha saiu do Comando); ADR-043/044 (a onda E2 põe as N personas e o modo "Por
persona" nesse contrato); auditoria UX de 27/09 (fase L).

## ADR-047 — Assistente do comando: refinar com a IA e responder à execução sem reescrever o texto

**Data:** 28/09/2026 · **Estado:** vigente, implantado em 28/09 (`a71e809`) · **Decisão técnica** pedida pelo dono ("em vez de
eu só responder essa crítica, preciso voltar e editar meu comando"). Doc principal: [`produto.md`](produto.md) §3;
API em [`api-contract.md`](api-contract.md#adendo-v030-28092026--assistente-do-comando-refinar-e-responder);
IA em [`ia.md`](ia.md#1-as-cinco-funções).

**Contexto.**

- O Comando era só um campo de texto. Quando o planejador não tinha o que precisava, a execução ia para
  `needs_input` com as perguntas numa faixa amarela e um botão "Editar comando": a pessoa voltava ao campo e
  reescrevia tudo à mão, adivinhando como incorporar as respostas.
- A máquina de estados só deixa `needs_input` ir para `cancelled` (`modules/execution/domain/states.py`): a execução
  não volta a planejar.
- Perguntas de DESTINO (qual persona, qual aparelho) já têm caminho próprio: são alvos, escolhidos na interface com
  prévia obrigatória (ADR-044). Escrever "no aparelho X" no texto vira destino tirado do texto, que a criação recusa
  sem confirmação.

**Alternativas.** Reabrir a execução em `needs_input` para `planning` (muda a máquina de estados e o histórico de
uma execução que já teve um comando); juntar as respostas ao texto sem IA ("comando + respostas" concatenados, sem
estrutura nem checagem do que ainda falta); um papel de IA novo (`refine`) com modelo e orçamento próprios.

**Escolha.**

- **Refinar é uma chamada de IA separada, sem efeito**: `POST /api/commands/refine` devolve o comando reescrito em
  blocos (Objetivo, App ou site, Passos, Dados, Concluído quando), as perguntas que faltam (com opções e o porquê),
  `ready` e avisos. Não cria execução nem grava nada além da linha de custo. Despacha pelo papel `plan` (mesmo
  modelo e orçamento do planejador, como o `generalize`), sem papel novo em `AI_ROLES`.
- **O refinador recebe o mesmo chão do planejador**: apps configurados, os NOMES dos dados da persona dos alvos
  (ADR-040, nunca valores) e, com `run_id`, as perguntas que o planejador fez. Assim "pronto" é uma previsão
  informada — mas continua previsão: quem decide é o plano.
- **Responder cria a execução sucessora**: `POST /api/runs/{id}/successor` cria outra execução com o comando
  refinado e o MESMO pedido de alvos da foto (`runs.targets`: aparelhos, personas, alvos confirmados, política) e
  cancela a antiga com `status_detail` apontando para a nova. Chave de idempotência derivada do id e do texto: duplo
  envio devolve a mesma sucessora.
- **Destino fica fora**: resposta com `field` `profile_id`/`instance_id` → 409 `pergunta_de_destino`; o painel
  mantém, para essas, o caminho de escolher alvos no Comando.
- **Segredo nunca chega à IA**: credencial no comando OU numa resposta → 409 `credencial_no_comando` antes da
  chamada; a saída de qualquer provedor passa por `normalizar(…, redact)` (tira credencial ecoada, corta no teto de
  4000, "pronto" com pergunta aberta vira "não pronto").
- **Domínio puro**: `modules/execution/domain/command_refinement.py` (prompt, esquema, parse, normalização e o
  simulado); os provedores importam dele sem import tardio e sem entrar no ciclo legado de `planning`
  (`tests/test_arquitetura.py`). Serviço em `taskqueue/assistente.py`.
- **Painel**: botão "Refinar com IA" no Comando e o componente `AssistenteDoComando` (rodadas com "desfazer", texto
  refinado editável, perguntas com opções, selo "Pronto para planejar"), usado também no banner da execução em
  `needs_input` ("Planejar com as respostas" / "Executar" criam a sucessora).

**Consequências.**

- Cada rodada é uma chamada paga ao modelo do planejador (esquema pequeno; `max_tokens` 4000). O custo entra em
  `ai_calls` com `role=plan` — da execução respondida quando há `run_id`, do dia quando não há.
- A execução respondida fica `cancelled` com o link; o histórico mostra as duas.
- O "Repetir" do painel continua levando só os aparelhos; a sucessora leva o pedido inteiro.

**Evidências.** `simulated`: `backend/tests/test_assistente_do_comando.py` (11), `tests/test_arquitetura.py`,
`frontend/src/features/command/AssistenteDoComando.test.tsx` (4), typecheck e as suítes inteiras; navegador contra
backend simulado próprio (8766). `real`: uma chamada no central em 28/09 (`a71e809`, `ai_calls` 1609, papel
`plan`, ~US$ 0,038, saída estruturada aceita), em [`relatorio-validacao.md`](relatorio-validacao.md) §16. Segunda
rodada real e sucessora real: `not_run`.

**Relação.** ADR-040 (credencial na conta da persona); ADR-044 (destino por alvo, prévia obrigatória); K-042 (limites
da saída estruturada: este esquema é pequeno e sem união).

## ADR-048 — Crenças ricas da persona vão ao modelo, com regra de conduta (biografia v2)

**Data:** 28/09/2026 · **Estado:** vigente na `main` · **Decisão do dono** (28/09): "sobre a religião e política eles
devem ir para o modelo sim e de forma rica, não apenas uma flag simples, tanto a política quanto a religião, e mostrar
isso visualmente de forma rica também, e isso deve inferir no contexto também". Substitui em parte o ADR-041. Código:
`9af7433` (modelo e v1→v2), `4de56e8` (bloco `<persona>`), `1763836` (geração e enriquecimento), `d85f2a8` (painel).
Doc principal: [`dominios/persona.md`](dominios/persona.md#o-que-vai-ao-modelo-e-o-que-fica-guardado); contrato no
[adendo v0.31](api-contract.md#adendo-v031-28092026--crenças-ricas-da-persona-adr-048).

**Contexto.** O ADR-041 guardava `beliefs.religion`/`politics` como uma frase e não as mandava ao modelo (decisão
pendente, design §15). Uma frase não dá coerência de valores nem estrutura para o painel.

**Alternativas.** (a) Mandar a frase como estava: pobre, sem estrutura para a tela. (b) Tabela ou colunas próprias:
migração e reconstrução sem ganho. (c) Objetos aninhados em `biography` v2, normalizados na leitura: **escolhida**.

**Escolha.**

- `BioReligion` (`affiliation`, `practice`: `nao_pratica|ocasional|regular|devota`, `practices[]`, `importance`,
  `in_speech`, `values[]`, `sensitive_topics[]`, `summary`) e `BioPolitics` (`orientation`:
  `esquerda|centro_esquerda|centro|centro_direita|direita|apolitica|nao_declara`, `engagement`:
  `nenhum|baixo|medio|alto`, `issues[{topic, stance}]`, `discussion_style`, `sources[]`, `values[]`, `summary`);
  tudo opcional, `extra="forbid"`.
- `BIOGRAPHY_SCHEMA_VERSION = 2`; a v1 é normalizada **na leitura** (`normalizar_biografia`/`crenca_legada`), sem
  SQL: a frase vira `summary`, e só vira `affiliation`/`orientation` quando não há adivinhação. A próxima escrita
  grava v2.
- No bloco `<persona>`, as crenças entram depois da biografia curta (`PERSONA_RELIGION_FIELDS`,
  `PERSONA_POLITICS_FIELDS`, valores fechados em português, tudo por `sem_marcacao`), seguidas da **linha fixa de
  conduta**; `SOCIAL_SYSTEM` manda usá-las como coerência, não como assunto.
- Crença fica fora de `BIOGRAFIA_MINIMA`; `CRENCAS_MINIMAS` (afiliação, orientação) só dispara o enriquecimento.
- A geração ganha a regra de crenças ricas, coerentes com a biografia e variadas entre personas, sem partido,
  candidato ou figura pública pelo nome; o teto do rascunho vai a 10000 tokens.
- No painel, dois cartões (Religião, Política): selo de prática/engajamento, barra de espectro **neutra** (sem cor
  partidária) com `aria-valuetext`, "apolítica"/"não declara" fora da barra, chips de práticas, valores e temas,
  pautas com posição, edição por cartão.

**Regra de conduta** (`CONDUTA_DAS_CRENCAS`): as crenças dão coerência aos valores, ao tom e às escolhas da pessoa (o
que aprova, o que evita, como reage a um tema); não são assunto a puxar. A persona não faz propaganda política nem
religiosa, não pede voto nem adesão, não espalha desinformação e não ataca grupos nem pessoas por crença, ideologia
ou identidade. É o limite do ADR-025/040 ("sem fake news, sem ofensa explícita") dito para o tema, e vale mais por
serem personas fictícias operando contas reais.

**Consequências.**

- Do ADR-041, fica substituído o trecho "religião e política guardadas e **não** enviadas"; o resto vale.
- O bloco `<persona>` cresce ~15 linhas numa persona rica; persona sem crença não ganha linha.
- As 14 pessoas de 28/09 não têm crença gravada: sobem de versão na leitura e ganham crenças ao editar ou enriquecer.
- `POST /enrich` numa persona completa mas sem crenças passa a chamar o modelo pago uma vez (só pela rota explícita).

**Evidências.** `simulated`: `backend/tests/test_persona_crencas.py` (modelo, v1→v2, PATCH e `null`, bloco e conduta,
anti-forja, geração, simulado variado, enrich, segredo), `CrencasPersona` no vitest; suíte 2578/2578 no branch.
`real`: ver a implantação no [relatório de validação](relatorio-validacao.md) §17.

**Relação.** ADR-041; ADR-025/040 (limites de conduta); ADR-046 (contrato de página).

---

## ADR-049 — Provedores de IA por papel: OpenAI primeiro, Gemini como braço de comparação e adoção só pela bateria

**Data:** 28/09/2026 · **Estado:** vigente; medido em 28/09 (relatório §18): **imagem adotada** (`gpt-image-2`
médio no central), **ator e verificador NÃO adotados** (o `gpt-6-luna` falhou nos critérios) · **Decisão do dono** (meta de custo, jurisdição, política de uso e autorizações) e **decisão técnica**
(ordem e critérios).

**Contexto.**

- O dono pediu o menor custo de inferência possível sem perder qualidade, com provedores especialistas por tipo de
  problema e a imagem da persona real.
- Nos 7 dias até 28/09, o gasto de IA foi de US$ 10,77 (cerca de US$ 42 a 46 por mês). O ator no Sonnet 5 respondeu
  por 50%, a US$ 0,0147 por chamada; com o escalonamento ao Opus, por 69% (`GET /api/usage`, `real`).
- A pesquisa de 28/09 ([pesquisa](pesquisa-provedores-ia-2026-09-28.md)) achou três candidatos com visão e tool
  calling de 5 a 13 vezes mais baratos por chamada: `gpt-6-luna`, Gemini 3.1 Flash-Lite e `deepseek-flash`. Nenhum
  tem qualidade provada neste projeto, e o qwen3-vl 4B local já empatou em custo por escalar ao Opus.
- O `gpt-image-1-mini`, planejado no ADR-042, sai da API em 01/12/2026. O sucessor, `gpt-image-2`, publica preço por
  token, não por imagem.
- O provedor compatível com OpenAI lia a chave só de `os.environ`, que não vê o `.env`.

**Alternativas.**

- Vários fornecedores em paralelo desde o início: descartada. Mais contas, políticas e jurisdições para a mesma
  pergunta, sem medição.
- DeepSeek ou Alibaba como braço principal: parados com gatilho. A DeepSeek guarda os dados na China (política de
  privacidade); o preço do Qwen-VL veio de uma leitura, sem votação.
- Modelo local ou destilação agora: parados. Não se pagam no volume atual e já empataram aqui.
- Trocar o ator direto pelo preço: descartada. O preço por chamada não decide; decide o custo por objetivo
  comprovado, com escalonamento.

**Escolha.**

- **Um fornecedor novo primeiro: OpenAI.** A mesma chave (`OPENAI_API_KEY`) serve ao ator e ao verificador candidatos
  (`gpt-6-luna`) e à imagem (`gpt-image-2`). O **Gemini 3.1 Flash-Lite** entra como segundo braço da mesma bateria,
  só com faturamento ligado.
- **Jurisdição:** o dono delegou a escolha ("por onde for melhor"). Fica EUA: OpenAI (API sem treino por padrão,
  registro de abuso até 30 dias) e Google no plano pago (sem uso dos prompts para melhorar produtos).
- **Código:**
  - chave por `EnvSettings.chave` (lê o `.env`);
  - `max_tokens_field` e `extra_body` por modelo;
  - saída estruturada por `json_object` com o esquema no texto (K-042);
  - custo da imagem pelo `usage` × `price_per_mtok`;
  - rejulgamento offline com candidato, sem escrever no `config.yaml`.
- **Adoção só pela bateria**, com critérios escritos antes:
  - sucesso ≥ base;
  - escalonamento ≤ 1,5 × base;
  - US$ por objetivo comprovado ≤ 50% da base;
  - p95 do ator ≤ base + 1 s;
  - zero ação em tela sensível.

  Os braços rodam sem `fallback_provider` e só nos casos do app de QA. A adoção declara `fallback_provider:
  anthropic`.
- **Política de uso** (decisão do dono, 28/09):
  - A imagem é de adulto fictício, sem semelhança com pessoa real.
  - O dono decidiu **não declarar a persona como virtual por ora** e revisar depois. Fica registrado que as políticas
    da OpenAI e do Google proíbem o uso para enganar, e que isso vale mesmo que o provedor não veja onde a imagem é
    usada.
  - A proveniência (C2PA) do original fica guardada (ADR-042) e nada tenta esconder a origem da imagem.

**Consequências.**

- Ligar um papel em nuvem é configuração (`ai.providers` + `ai.roles`, com os candidatos já declarados no exemplo).
- A primeira chamada paga não volta 401 por chave no `.env`.
- A imagem real custa o que a resposta diz, e a estimativa por imagem só confere o teto antes.
- Autorizações dadas em chat em 28/09: rejulgamento offline, teste de rosto, bateria com reinícios e implantação se
  passar em todos os critérios.

**Evidências.**

`simulated`:
- `backend/tests/test_openai_provider.py::test_parametros_por_modelo_e_chave_pelo_env`
- `backend/tests/test_openai_provider.py::test_chave_por_nome_declarado_apelido_e_ambiente`
- `backend/tests/test_persona_imagens.py::test_gpt_image_2_custo_pelo_usage_da_resposta`
- `scripts/tests/test_eval_rejudge.py::TestModoCandidato`

`real` ([relatório §18](relatorio-validacao.md)): rejulgamento offline de 56 capturas (luna 42/56, 5 falsos positivos,
US$ 0,00031 por captura; Flash-Lite 46/56 com 8 falsos positivos), bateria `fase17-base` × `fase17-luna` ×
`fase17-luna-ator-sonnet` (13 × 12 × 12 de 14; US$ por correto 0,177 × 0,090 × 0,091) e teste de rosto do
`gpt-image-2` (~US$ 0,052 por imagem média, rosto mantido).

**Resultado da medição (28/09).** O luna sem raciocínio (o único modo em que ele chama ferramenta pelo Chat
Completions) errou onde a base acerta:

- como verificador, deu por comprovado um envio que a própria evidência dizia ter falhado;
- como ator, bloqueou por "conta errada" com a conta certa na tela.

Como o dono autorizou só a adoção que passasse em todos os critérios, o ator e o verificador seguem na Anthropic.
O caminho para usar um ator barato passa pela cascata (17.10): bloqueio do tier 0 sobe ao tier 1 antes de chamar
a pessoa, e o "sim" do tier 0 em etapa com efeito externo é rejulgado. Só então vale medir de novo. Com o ator
barato, o plano no Opus vira o maior custo (63% do braço).

**Relação.** ADR-005, ADR-023 (o ator local que empatou); ADR-042 (imagem); ADR-013 (fallback de recusa, só Anthropic);
decisão 7 do plano-100 (base × configuração antes de adotar alavanca de custo).

## ADR-050 — Modo Automático: a IA escolhe quem faz, o código escolhe onde; crença é coerência, não alvo de persuasão

**Data:** 28/09/2026 · **Estado:** vigente, implantado em 28/09 (`b0f2c07`) · **Decisão técnica** pedida pelo dono ("essa decisão sobre
quais aparelhos, personas e em qual servidor vai ser orquestrado depende do pedido do usuário, da disponibilidade das
personas e dos aparelhos em relação à fila… e até qual persona utilizar no que faz sentido com o que foi pedido").
Doc principal: [`produto.md`](produto.md) §3; API no
[adendo v0.33](api-contract.md#adendo-v033-28092026--modo-automático-quem-faz-e-onde-adr-050); IA em
[`ia.md`](ia.md#1-as-cinco-funções).

**Contexto.**

- O Comando obrigava a escolher à mão entre "Aparelhos marcados", "Por persona" e "Distribuir entre servidores".
- As peças determinísticas já existiam: `resolver_alvos` (persona → aparelho: sessão pronta, principal) e o
  balanceamento (carga do servidor, aparelho ligado, ocupado). Faltava a escolha SEMÂNTICA: quais personas, e
  quantas, combinam com o pedido.
- As crenças ricas (ADR-048) chegaram hoje, com a regra de conduta: a persona não faz propaganda política nem
  religiosa, não pede voto nem adesão.

**Alternativas.** Heurística sem IA (palavras do pedido × perfil): barata, mas cega a nuance ("se importa pouco com
política" não é "de esquerda"). A IA escolher tudo, inclusive aparelho e servidor: ela não vê a carga em tempo real e
repetiria o que o balanceamento já faz bem. Um papel de IA novo: sem ganho sobre o `plan`.

**Escolha.**

- **`POST /api/runs/targets/suggest`**, sem efeito colateral, com três caminhos do mais barato ao pago:
  1. o texto já diz quem ou onde → a prévia de sempre (ADR-044), sem IA;
  2. nenhuma persona serve ao app (ex.: QA Messenger) → distribuição pela carga, sem IA;
  3. há candidatas → uma chamada do papel `plan` escolhe QUAIS e QUANTAS pelo cartão de cada uma (identidade,
     cidade, profissão, interesses, voz, crenças e disponibilidade: aparelhos, ligados, sessão pronta, tarefas na
     fila); depois o `resolver_alvos` põe cada uma no aparelho dela e o balanceamento desempata. A IA não escolhe
     aparelho.
- **Crença é coerência, não alvo**: nunca se escolhe quem teria de dizer ou fazer o contrário do que acredita
  (a católica devota fala da missa; o ateu não), e intensidade conta. Mas não se escolhe persona pela orientação
  para influenciar opinião. Propaganda política ou religiosa, pedido de voto ou adesão, elogio ou ataque a candidato
  ou partido em campanha, ou campanha coordenada de opinião → `alerta_conduta`, ninguém escolhido. É o ADR-048 dito
  para a orquestração; mudar isso é decisão do dono em ADR próprio.
- **Sem adivinhar**: persona sem as crenças mínimas (`CRENCAS_MINIMAS`), num pedido que depende delas, vai para
  `nao_avaliaveis`. O painel oferece abrir a persona e completar com a IA (enriquecimento com instruções, adendo
  v0.32, da sessão da evolução 2).
- **Painel sem formulário novo**: "Automático" é o modo padrão (chave `commandTargetV2`, então todo mundo começa
  nele uma vez); os três manuais ficam atrás de "escolher manualmente". Planejar/Executar no Automático mostram
  "Quem faz e onde" — cada persona com aderência, motivo, aparelho e servidor; descartadas dobradas; as sem dados com
  link para a persona — e só a confirmação cria a execução, ecoando os alvos em `targets` (origem `ui`).
  "Escolher manualmente" a partir da sugestão abre o modo por persona já com as sugeridas marcadas.
- **Domínio puro** em `modules/execution/domain/orquestracao.py` (prompt, esquema estrito sem união, normalização:
  id estranho some, a mesma persona não aparece em duas listas, teto de quantidade, alerta zera a escolha; e o
  simulado). Serviço em `taskqueue/orquestrador.py`.

**Consequências.**

- Cada Planejar/Executar no Automático que depende de persona é uma chamada paga ao modelo do `plan` (esquema
  pequeno, `max_tokens` 4000). Os caminhos 1 e 2 não custam nada.
- O que sai da máquina por chamada: o pedido (sem destinos) e o cartão de até 20 candidatas (perfil, voz e crenças,
  nunca conta, handle, senha ou aparelho), ordenadas pela disponibilidade.
- "Distribuir entre servidores" e "Por persona" continuam, agora como escolha manual.

**Evidências.** `simulated`: `backend/tests/test_orquestracao.py` (10), `tests/test_arquitetura.py`,
`frontend/src/features/command/SugestaoDeAlvos.test.tsx` (4), suítes inteiras; capturas CDP a 1366 e 375 px contra
backend simulado com três personas de teste. `real`: uma chamada no central em 28/09 (`b0f2c07`, `ai_calls` 2233, ~US$ 0,027, esquema aceito; as três personas vivas sem crença vieram como não avaliáveis, sem chute), em [`relatorio-validacao.md`](relatorio-validacao.md) §19.

**Relação.** ADR-044 (prévia e eco dos alvos); ADR-048 (crenças e conduta); ADR-047 (assistente do comando, que
continua cuidando do TEXTO); K-044 (domínio fora do ciclo de `planning`).

## ADR-051 — Saldo das contas de IA: livro-caixa com consumo dos relatórios oficiais, aviso e bloqueio

**Data:** 28/09/2026 · **Estado:** vigente, implantado e encerrado em 28/09; os limites foram delegados pelo dono
("faça da melhor forma") · **Decisão do dono** (acompanhar os três saldos, que valem como regra; integração pelas APIs, sem ler
tela) e **decisão técnica** (o livro-caixa).

**Contexto.**

- Três contas pré-pagas alimentam a IA da plataforma: Anthropic (US$), OpenAI (US$) e Google AI Studio (Gemini, R$).
  Nenhuma tem recarga automática: ao zerar, a API recusa.
- O dono pediu para ver os três saldos na plataforma, a IDE também ver, e os saldos valerem como regra e alerta.
- **Nenhum dos três publica o saldo pré-pago por API** (pesquisa de 28/09). A Anthropic tem pedido aberto; a OpenAI
  só tem endpoint interno, com a sessão do painel; o Google não tem nada, e o saldo do AI Studio mora num iframe de
  `payments.google.com`.
- O que existe é o **consumo**, pela API de administração: uso por hora e modelo na Anthropic e custo por dia na
  OpenAI. O Google só oferece a exportação de faturamento para o BigQuery.

**Alternativas.**

- Ler o saldo na tela do console: pela IDE no Chrome do dono (feito uma vez, para a âncora) ou por uma extensão do
  Chrome que lia a tela de hora em hora. A extensão foi construída em 28/09 (`b6e99c0`) e **rejeitada pelo dono**
  ("horrível; quero uma integração decente"), e depois removida. Raspar tela é frágil e depende do navegador aberto.
- Exportação do Google para o BigQuery: é oficial, mas atrasa horas e pede projeto, conjunto de dados e conta de
  serviço. Fica como conciliação opcional do Gemini, `not_run`.
- Só o teto diário em US$: não basta. É por dia e soma as contas, mas quem para a IA é o saldo de UMA conta.

**Escolha: livro-caixa.**

- `saldo = âncora − consumo desde a âncora`. A âncora é o saldo inicial (uma vez), uma recarga (saldo de agora +
  valor comprado), o fechamento diário automático ou um erro de cobrança do provedor (saldo 0).
- O consumo vem de fonte oficial ou medida, nunca da tela:
  - Anthropic: `usage_report/messages` de hora em hora, precificado por `ai.prices`. Validado em 28/09 contra
    `ai_calls`: 5,4946 × 5,4693 US$ em 6 h.
  - OpenAI: `organization/costs`, diário e com o dia corrente.
  - Gemini: `ai_calls`, porque a chave é só da plataforma.
  - O consumo de fora da plataforma entra por diferença com linha de base (migração 053).
- Um laço de 10 min concilia e fecha o dia (a âncora com mais de 24 h vira uma nova com o saldo estimado).
- **Regras por conta** (`ai_billing_accounts`, migração 052), na moeda da conta:
  - `warn_below` gera aviso: problema `ai_balance_low` e chip amarelo;
  - `block_below` barra a IA daquela conta ANTES de gastar (`AIError(kind="balance")`, tratado como problema de
    conta: disjuntor e pausa). O `fallback_provider` declarado para outra conta atende, com a mesma conferência;
  - a imagem da persona confere a conta do gerador.
- **Erro de cobrança** (402, `billing`, ou o 429 `insufficient_quota` da OpenAI) grava âncora 0. A conta fica "sem
  crédito" até a recarga.
- **Desatualizado** = conta com chave de administrador sem conciliação nos últimos 30 min (ou com erro).
- **Limites** (o dono delegou em 28/09; são os padrões de fábrica e os valores do central):
  - bloqueio em US$ 0,50 (R$ 2,50 no Gemini): a plataforma para ANTES de o provedor recusar no meio de uma etapa,
    com folga para o erro da estimativa;
  - aviso em US$ 3 na Anthropic (paga as cinco funções e queimou ~US$ 1/h nas baterias de 28/09), US$ 2 na OpenAI
    e R$ 10 no Gemini;
  - câmbio do Gemini em 5,2 R$/US$, editável.

**Consequências.**

- O único gesto humano é registrar a recarga. Nada depende de ler tela, e a leitura do console vira conferência
  opcional.
- Recarga não registrada deixa o saldo baixo demais: ele erra para o lado seguro, com aviso cedo.
- Retomar a execução não solta uma conta "sem crédito". Só a recarga solta.
- O Gemini depende de a chave ser só da plataforma. Se ela for compartilhada, liga-se a exportação do BigQuery.

**Evidências.**

`simulated`: `backend/tests/test_saldos_de_ia.py` (24), `test_openai_provider.py::test_429_sem_quota_e_falta_de_credito_nao_limite_de_taxa`,
`frontend/src/lib/aiBalance.test.ts`, `tiles.test.ts`, `TopBar.test.tsx`.

`real` (28/09, central):
- relatório horário da Anthropic × `ai_calls` (12h–18h UTC): 5,4946 × 5,4693 US$;
- conciliação OpenAI e Anthropic com as chaves de administrador;
- âncoras lidas no console (Anthropic US$ 4,53, OpenAI US$ 8,25, Gemini R$ 29,37);
- painel conferido no navegador.

- **bloqueio real** (18:54 UTC): com o bloqueio da Anthropic acima do saldo, `POST /api/commands/refine` voltou 503
  `kind: balance`, a saúde acusou `ai_balance_blocked` e `ai_calls` ficou em 2281 antes e depois. Nenhuma chamada
  foi ao provedor, então o custo foi zero. Em seguida os limites definitivos foram aplicados.

`not_run`: exportação do BigQuery para o Gemini. Não é necessária enquanto a chave for só da plataforma.

**Relação.** ADR-049 (provedores por papel), achado #90 (disjuntor de conta), achado #95 (teto em US$).

---

## ADR-052 — Conhecimento de app como dado: zero Python por app, motores genéricos no núcleo

**Data:** 28/09/2026 · **Estado:** vigente; meta **aprovada pelo dono em 28/09** ("siga com todas as etapas"); fatias
1–4 feitas (a 1 implantada em `eafca07`); a 5 (aprendizado) e o 12.3 seguem propostos · **Decisão do dono** (a meta) e
**decisão técnica** (as fatias) ([design](design/conhecimento-de-app.md)); revê em parte o ADR-039.

**Contexto.**

- Execução `r-20260928165254-e31953`: 31 chamadas, US$ 0,59 e 18,7 min num pedido de 5 etapas, que falhou.
  - A checagem de sessão chamou uma pessoa porque uma conversa aberta não estava em nenhuma tabela de sinais.
  - A digitação cortou o comentário (22 de 125 caracteres) e disse que tinha digitado tudo.
  - Nada projetou nem acusou que aquilo estava fora do normal.
- O dono perguntou por que existe código do Instagram, se a plataforma deveria operar qualquer app pelo conhecimento.
- O ADR-039 tirou as comparações com `"instagram"` do núcleo, mas fixou "app novo = manifesto + provedor + catálogo,
  em Python". Isso eram ~1.050 linhas em `integrations/instagram/` mais um catálogo de 246 linhas escrito em Python.
- Outlook e os demais rodavam só no "caminho livre".

**Alternativas.**

- Manter o ADR-039 e escrever um `integrations/<app>/` por app: descartada. Custa ~1.000 linhas de Python por app, e
  o conhecimento não pode ser aprendido nem corrigido sem deploy.
- Deixar tudo para a IA livre: descartada. Os números medidos mostram o custo e a instabilidade disso (e31953).
- Refazer tudo de uma vez: descartada. Mexe em sessão, catálogo e núcleo social juntos, sem prova intermediária.

**Escolha.**

- **Meta:** zero Python por app. O conhecimento de um app é dado versionado, numa pasta por pacote
  (`backend/app/conhecimento/apps/<pacote>/`); o código é só motor genérico, e o conhecimento aprendido entra como
  candidata validada.
- **Em fatias**, cada uma com prova e catraca:
  1. **Telas** (`telas.yaml`, motor `automation/conhecimento_de_telas.py`): sinais por idioma, regras de tela,
     extrações e o estado conhecido. A checagem de sessão volta ao estado conhecido (voltar do Android, no máximo uma
     reabertura, sem efeito externo) antes de chamar uma pessoa.
  2. **Catálogo** (`catalogo.yaml`, carregador `planning/capabilities.py::carregar_catalogo`, `contract_version`
     conferida). O registro de apps DESCOBRE as pastas (`integrations/app_declarado/pacote.py::descobrir`): criar a
     pasta é registrar o app.
  3. **Sessão** (`sessao.yaml`, motor `integrations/app_declarado/sessao.py::SessaoDeclarada`): login, dispensa de
     telas benignas, observar depois de enviar, conta errada, tetos e cooldown. A máquina de estados é uma só.
  4. **App âncora do perfil pelo registro** (`app.yaml: ancora_do_perfil`, `registry.pacote_ancora()`) no lugar de
     `package_of_provider("instagram")`; o bloco `instagram:` do `config.yaml` vira o genérico `contas:`
     (`session_max_age_s` e ajustes do login por pacote), e os links de perfil viram dado (`links_de_perfil`).
- **O que fica no `app.yaml`:** nome e rótulo, conta gerenciada (`provedor_de_sessao`, que exige `sessao.yaml`),
  perfil e internet obrigatórios, âncora, tipos de texto e leituras de conversa, a leitura de tela para o rascunho
  (`automation/leitura_de_tela.py::LeituraDeclarada`) e os links de perfil.
- **Na mesma rodada:** digitação com conferência do campo (18.1); projeção e orçamento por ação medidos no histórico
  (18.3); CI × parque, feito pela sessão Evolução (18.4).
- **Segurança fica fora do conhecimento editável:** telas de desafio, 2FA e senha seguem no critério embutido
  (`hierarchy._DESAFIO`, campo com atributo de senha, aparelho-loja; a lista `sensitive_screens` do `config.yaml` só
  acrescenta), e os desfechos que um `sessao.yaml` pode declarar depois do envio não
  incluem "tentar de novo" nem "pronto" sem a conta lida na tela.

**Consequências.**

- `app/integrations/instagram/` e `planning/catalog/instagram.py` deixaram de existir. O Instagram é um pacote de dado
  como qualquer outro, e um app novo com o mesmo tratamento (classificação, estado conhecido, catálogo, leitura, login
  e conferência da conta) é uma pasta de arquivos. Está provado com um cliente de e-mail declarado só em dado.
- Corrigir ou ensinar uma tela, uma ação ou um passo de login é mudar YAML, sem Python; o carregador recusa na carga o
  arquivo que não se sustenta (campo desconhecido, sinal faltando num idioma, referência inexistente).
- **Catracas:** `app/integrations/` só tem o motor; o texto "instagram" no código de `app/` só desce (`TEXTO_LEGADO`
  em `test_apps_fora_do_nucleo.py`); o motor de sessão e o de telas não citam app nenhum.
- **Continua com nome do Instagram, por ser nome e não conhecimento:** a tabela `instagram_profiles`, o prefixo de
  rota `/api/instagram/…` e o nome antigo da variável da chave mestra. Renomear é migração e versão de contrato.
- **`config.yaml`:** o bloco `instagram:` não é mais aceito; a instalação que o tiver não sobe e diz para onde cada
  ajuste foi (`contas:`). Nenhuma instalação conhecida o tinha (conferido no central em 28/09).
- **Decidido pelo dono em 28/09:** a meta, que revê o ADR-039, e seguir com todas as fatias necessárias. Seguem com
  ele a fatia 5 (aprendizado) e o item 12.3 (persona com mais de um app âncora).

**Evidências.** `simulated`:

- fatia 1: `backend/tests/test_conhecimento_de_telas.py`; 18.1: `test_tools_and_api.py::test_digitacao_*`; 18.3:
  `test_projecao.py`;
- fatia 2: `test_catalogo_como_dado.py` (o YAML carrega igual ao catálogo em Python que substituiu);
- fatia 3: `test_sessao_declarada.py` (o correio só em dado entra, lê a conta, recusa senha e para no desafio) e os
  testes de sessão do Instagram, que mudaram só de montagem;
- integração e fatia 4: `test_pacote_declarado.py` (descoberta de ponta a ponta, recusas, um âncora só),
  `test_apps_fora_do_nucleo.py` (catracas).

`real`: a projeção sobre o histórico do central para o plano da e31953 deu 16–28 chamadas, US$ 0,40–0,74 e 3–5 min
(a execução real: 31 chamadas e 18,7 min). Sessão pelo motor genérico no central (`a7fe364`, 28/09 ~19:37 UTC):
"Verificar conta" confirmou `@lucas.almeida9484` no android-01 e `@andre.carvalho9543` no android-06; com o convidado
sobrecarregado e a árvore vazia, gravou `unknown` em vez de afirmar
([relatório §20](relatorio-validacao.md#20-conhecimento-de-app-como-dado-adr-052-fatias-14--implantação-e-prova-real-28092026)).
Login digitando a senha e a volta ao estado conhecido num aparelho real: `not_run`.

**Relação.** ADR-039 (revisto em parte); ADR-032/034 (capability e skill, o destino do catálogo como dado); ADR-029 e
ADR-009 (desafio e 2FA seguem com a pessoa); ADR-040 (credencial pela pessoa, canal sensível); item 12.3.

---

## ADR-053 — Falhas reiteradas do Instagram: medir para onde foi o tempo e não transformar lentidão em falha

**Data:** 28/09/2026 · **Estado:** vigente, implantado em 28/09 (`93967d0`, central e agente do notebook), com prova
real de navegação e de efeito (curtir e comentar, autorizada pelo dono) · **Decisão técnica** (pedido do dono de
28/09); completa o ADR-052 no caminho da execução.

**Contexto.**

- O dono reclamou em 28/09 que execuções como `r-20260928165254-e31953` e `r-20260928195344-02ee9e` continuavam
  falhando e que os planos de melhoria não funcionavam. As melhorias do mesmo dia (ADR-052, digitação conferida,
  projeção) não tocaram o que derrubava essas execuções.
- Placar do Instagram: 24 de 31 objetivos com sucesso de 20 a 25/09; 1 de 7 em 27–28/09; 0 de 5 em 28/09.
- Diagnóstico medido por um workflow de 109 agentes: 51 causas examinadas, 12 confirmadas por dois céticos (um
  refazendo as consultas ao banco, aos logs e ao adb, outro lendo o código). Na 02ee9e a IA ocupou 41 s de 925 s
  (4,5%); o resto foi o convidado saturado e o nosso código transformando lentidão em falha. As doze causas estão no
  [relatório §21](relatorio-validacao.md#21-falhas-reiteradas-do-instagram--diagnóstico-medido-correções-e-prova-real-28092026-adr-053);
  as que mais pesaram:
  - **C1, substrato:** o android-06 (2 vCPU, 2 GB) com a CPU saturada durante a tarefa (load 15–35, 48–57% em irq),
    sem falta de RAM;
  - **C2:** o 500 transitório do UiAutomator2 ("waiting for the root AccessibilityNodeInfo … hogging the main UI
    thread") era tratado como sessão morta, e a sessão do Appium era recriada: 5 recriações = 305,6 s de 925 s na
    02ee9e; 4 = 214 s na e31953;
  - **C3:** com `hide_error_dialogs=1` (gravado por `adb.py`), cada ANR do app em primeiro plano vira morte silenciosa
    (`exit-info` reason=6) e o launcher volta; a IA reabria, a partida a frio dava outro ANR (5 mortes do Instagram
    na 02ee9e, 6 na e31953);
  - **C4:** a recuperação automática fazia force-stop do Instagram VIVO (a folha de comentários aberta, na e31953) e
    podava o plano na fronteira do like, sem `commit_guard` nem `bindings.content`: 448,7 s perdidos;
  - **C5:** `mobile: type` cortava a cauda do texto (comentário com 22 de 125 caracteres), e a conferência do 18.1 era
    inerte, porque o compositor do Instagram é `AutoCompleteTextView` e `hierarchy.py` só reconhecia `EditText`;
  - **C6–C12:** rolagem numa faixa estreita virando toque longo, prévia na fila única do aparelho, porta de sessão
    travada por contador antigo, foco lido da seção congelada do último ANR, alvo sem identidade (qualquer post
    provava "Posts"), prazos fixos que contam tempo de infraestrutura e higiene (relógio, fila do orquestrador,
    aprendizado de execução confirmada à mão).
- **Por que os planos anteriores não resolveram.** Miraram o último sintoma visível (texto cortado, custo, checagem
  de sessão) sem medir para onde foi o tempo da execução. Nenhum dos 38 commits do dia tocou os mecanismos C2, C3, C4
  e C7, que somavam a maior parte dos 925 s.

**Alternativas.**

- Mais vCPU ou RAM, ou menos aparelhos ligados, como correção única: descartada. O android-06 tinha RAM sobrando, e
  mais recurso não corta o laço de recriação nem o de reabertura. Continua como remédio de capacidade, no aviso de
  pressão.
- Prazo maior por etapa e por objetivo: descartada. Só adia a falha; cada recriação e cada partida a frio consomem o
  prazo novo do mesmo jeito.
- Recriar a sessão com recuo em vez de reler: descartada. Recriar custa 27–80 s num convidado saturado e piora a
  própria saturação (K-049).
- Desligar `hide_error_dialogs` para o diálogo de ANR aparecer: adiada. Muda o que a IA vê em toda tela e pede
  experimento próprio; o `exit-info` dá o sinal sem mexer no aparelho (K-048).
- Reparar o convidado lento pela escada de reparo: descartada. A escada chega a `reset`, que apagaria a conta real
  logada.

**Escolha.** Seis decisões. As nove correções que as aplicam têm, cada uma, um teste que falhava antes e revisão
adversarial (quatro barradas pelo revisor e ajustadas):

1. **UI ocupada não é sessão morta: relê, não recria.** O 500 de UI ocupada vira `DriverBusy`. A leitura relê com
   recuo (até 3 vezes, 4 s, dentro do prazo da etapa) sem recriar a sessão; a ação com UI ocupada fica com efeito
   incerto e não se repete; só a sessão morta de verdade recria. Na mesma linha, o swipe não pausa depois de encostar
   (não vira toque longo), a rolagem numa faixa estreita usa a área rolável maior, e a sobreposição aberta pelo
   arrasto volta sem a IA (`changed=false`).
2. **A recuperação não mata app vivo e não repete efeito comprovado.** Falha de prazo, de IA ou de guarda com o app
   vivo em primeiro plano retoma da tela atual. Com o app encerrado, a navegação atravessa o efeito comprovado sem
   repeti-lo (um LIKE repetido descurte). A etapa revisada herda `commit_guard`, `bindings.content` e `draft_meta`.
   Se a projeção medida das etapas refeitas não cabe no tempo restante, falha com esse motivo.
3. **ANR tem sinal próprio e corta o laço de reabertura.** `dumpsys activity exit-info` (`Adb.app_deaths`) diz quando
   e por que o app morreu; o foco é lido da seção viva do `dumpsys window`, nunca da "WINDOW MANAGER LAST ANR"
   (K-047). `open_app` espera o foco e devolve `focused`. Com morte do app na etapa: uma reabertura determinística
   sem IA; na segunda, a etapa falha com o motivo de `adb.motivo_de_anr`, e o aviso vai para o aparelho:
   `o <app> parou de responder (ANR) e foi fechado no <aparelho>: convidado sem CPU`. A contagem é por etapa, não por
   tentativa. Prazo vencido vira `kind=step_deadline`, não "IA indisponível". `hide_error_dialogs` continua 1.
4. **A identidade do alvo vem da legenda, antes de qualquer efeito.** OPEN_POST ganha `caption_contains` (o
   planejador copia um trecho literal do pedido), e a pós-condição exige a legenda na tela. LIKE_POST, OPEN_COMMENTS e
   CREATE_COMMENT têm guarda de cartão (`card_guard`), e o balão de OPEN_COMMENTS (`card_control`
   `id=row_feed_button_comment`) só vale no cartão da legenda. Sem a legenda na tela, `step_blocked`. Tudo como dado
   no `catalogo.yaml` (ADR-052); pedido sem legenda segue como antes.
5. **Fluxo só se aprende com prova.** `learn_from_run` só aprende de execução com TODAS as etapas `verified=true` e
   congela como modelo também `bindings`, `band_guard` e `success_criteria`: etapa confirmada à mão não vira receita.
6. **Convidado ocioso com interrupção acumulada pede reinício a frio, nunca reset.** A sonda de saúde lê a linha `cpu`
   de `/proc/stat` e mede a fração em irq+softirq entre duas sondas. Aparelho ocioso (ninguém no controle) acima de
   15% em 3 sondas seguidas recebe um `restart` rastreável (`requested_by='system'`), no máximo 1 a cada 6 h por
   aparelho; depois disso, só o aviso no cartão. Nunca a escada de reparo (K-050).

**Na mesma rodada**, sem decisão nova:

- **Prévia fora da fila do aparelho:** com a IA no controle, a prévia não enfileira screencap próprio, e o painel
  recebe o frame da observação da IA ([adendo v0.36](api-contract.md)). O `drain` espera só a etapa, o relógio do
  convidado não é acertado com objetivo em execução, e o aviso de pressão diz CPU ou RAM conforme o ramo que disparou.
- **Digitação atômica:** o texto é definido de uma vez (set text do UiAutomator2), e `mobile: type` fica só como
  alternativa em pedaços. `AutoCompleteTextView` e `MultiAutoCompleteTextView` contam como campo, `typed_chars` é o
  que está no campo, e `verified=false` quando o campo não é identificado. A regra nova de mascaramento do log do
  Appium cobre o payload. A senha (`type_secret`) fica fora do escopo.
- **Porta de sessão:** o contador no teto com sessão anterior ao boot do emulador gera uma releitura `observe_only`
  (uma por janela); launcher ou outro app na frente não soma `unknown_streak` e vai para o aviso do aparelho;
  "Verificar conta" não soma acima do teto. Sem migração.
- **Orquestrador:** conta como fila só `running`/`paused` e mostra a saúde do aparelho nas sugestões.

**Consequências.**

- A lentidão do convidado vira demora e aviso no aparelho, não falha nem laço. A falha que sobra diz a causa
  verdadeira (ANR com o convidado sem CPU, prazo da etapa, legenda ausente), e não "IA indisponível".
- Os prazos em si não mudaram (`step_timeout_s` 180 s, `objective_timeout_s` 900 s): o C11 foi tratado só no nome do
  prazo vencido e na projeção antes da recuperação.
- Curtir e comentar agem só no post da legenda citada; um pedido sem legenda segue com o risco antigo de agir no post
  errado.
- Um aparelho ocioso pode ser reiniciado sozinho, sem apagar dados; o comando fica no histórico com
  `requested_by='system'`.
- O deploy desta rodada exige reiniciar o Appium (regra de log nova; procedimento do K-039).
- **Pendências dos revisores:** a aprovação de texto não acompanha a etapa revisada na recuperação (volta a pedir
  aprovação); o link "View all N comments" segue sem guarda de cartão; erro de adb no screencap da observação ainda
  recria a sessão; a evidência de OPEN_POST mostra só o seletor "Posts", embora a pós-condição exija a legenda
  (`textos_do_cartao_ausentes` em `executor._verify`); a causa do acúmulo de irq não está provada.

**Evidências.**

`real`:

- **diagnóstico** sobre o banco, o `appium.log` e o adb do central (e31953 e 02ee9e), com os números acima;
- **irq medido** em 28/09 ~21:45 UTC (adb só leitura, 10 s de `/proc/stat` com o aparelho ocioso): android-01 0% com
  7,7 h no ar, android-06 21% com 68 h, android-04 90% com 44 h. Depois do `restart` pela plataforma
  (`c-20260928214526-3354ad` e `c-20260928214526-abbb42`): 2,8% e 2,0%, e "Verificar conta" do andre no android-06
  em 21 s (`c-20260928215311-3e76c5`, `session_ready`);
- **prova da correção, navegação** (central em `93967d0`, android-06, 28/09): `r-20260928234657-bbdf3c` terminou
  `succeeded`, 3/3 etapas comprovadas por observação, em 89 s, com 8 chamadas de IA, plano v1, sem efeito, 0
  recriação de sessão, 0 ANR novo e leitura de tela p95 de 6,6 s (antes, 10–45 s);
- **prova da correção, com efeito** (autorizada pelo dono em chat em 28/09; mesmo central e aparelho, persona andre):
  `r-20260928235215-6eb84c` curtiu uma vez e comentou num post do perfil-alvo que NÃO era o da e31953 (já curtido;
  curtir de novo descurtiria). 5/5 etapas comprovadas em 2 min 41 s, com 13 chamadas de IA, plano v1; o comentário
  entrou inteiro por `mobile: replaceElementValue` (234 ms) e apareceu na lista como do andre; 0 recriação de sessão
  e 0 ANR novo. O comentário não passou por aprovação humana por causa da política própria do perfil (K-052).

`simulated`: `backend/tests/test_ui_ocupada.py`, `test_anr_sinal_proprio.py`, `test_previa_nao_disputa_com_a_ia.py`,
`test_recuperacao_preserva_estado.py`, `test_digitacao_atomica.py`, `test_porta_de_sessao_no_teto.py`,
`test_alvo_por_legenda.py`, `test_aprendizado_de_fluxo_com_prova.py` e `test_saude_do_convidado.py`; vitest
`DeviceCard.preview.test.tsx` e `FocusPanel.test.tsx`. Suíte do backend 2848 ok em SQLite (só `test_backup` falha,
como sempre fora do checkout com `config.yaml`); frontend typecheck ok e 656 testes ok.

`not_run`: guarda de cartão (`card_guard`) num LIKE real (a 6eb84c escolheu o post por critério negativo, sem
`caption_contains`; o caminho de efeito da decisão 4 está provado só em `simulated`); relógio do host em NTP (exige
autorização); experimento com `hide_error_dialogs=0`; as pendências dos revisores listadas em Consequências.

**Relação.** ADR-052 (a conferência do 18.1 era inerte no `AutoCompleteTextView`; o catálogo como dado ganhou
`caption_contains`, `card_guard` e `card_control`); ADR-027 (a prévia sob demanda agora cede a vez à IA); ADR-007 e
K-022 (receita só de execução comprovada); ADR-029 e ADR-040 (por que nada automático chega a `reset` num aparelho com
conta real); ADR-019 (relógio); ADR-010 (o reinício é comando cercado); K-039 (Appium no deploy); K-047 a K-052;
Fase 19 do plano-100.

## ADR-054 — Aprendizado contínuo: livro de aprendizado com ciclo de vida, publicação sozinha só sem efeito externo (D1), feedback implícito com botão opcional (D2), lições medidas e backlog do que mais falha

**Data:** 29/09/2026 · **Estado:** aceito; fundação (pacote A1, item 20.2) implantada com `c359f65` (29/09 ~03:55Z);
A2–A9 integrados na `main` em `f497075` e implantados em 29/09 ~07:38Z, sem migração nova; lições em `shadow` e telas
em `observe` até a primeira prova real (Fase 20 do plano-100, cujo 20.9 absorve o item 18.8) · **Decisão do dono**
(pedido de 28/09; D1 e D2 adotadas em 29/09 pelas recomendações, quando ele autorizou resolver tudo o que ficou em
aberto) e **decisão técnica** (o desenho, sintetizado de três propostas avaliadas por três juízes); completa o ADR-052
(fatia 5) e usa como régua as falhas medidas do ADR-053.

**Contexto.**

- Em 28/09 o dono pediu que o sistema aprenda o tempo todo, com as execuções que dão certo e as que dão errado e com o
  feedback de quem monitora (ou sem ele). Pediu também que esse conhecimento fique estruturado para evoluir a plataforma
  com os erros da própria IA, e não só o prompt.
- O que já aprende sozinho:
  - receitas (`taskqueue/recipes.py`); desde `7a02491` (implantado, item 21.14), a receita da IA nasce candidata e só
    sobe por concordância em sombra;
  - fluxos (`flows.learn_from_run`), só com todas as etapas `verified` desde o ADR-053;
  - a projeção por ação (`projecao.py`);
  - a memória da persona (`social/memory.py`).
- O que não aprende:
  - o erro não passa de uma execução para outra: o ator só vê a tentativa anterior da mesma etapa, e nenhuma lição entra
    no prompt;
  - o motivo da falha é texto livre (`attempts.error`);
  - nada agrega o que mais falha.
- Há feedback gravado que ninguém consome: aprovações editadas, notas de "resolver objetivo" e de comando incerto,
  correções do ensino e `skill_validation_results` (043), que não tem escritor em produção. E há feedback que se perde:
  a tomada manual fora do treino (vira só evento de log) e as respostas a perguntas.
- A fatia 5 do ADR-052 (item 18.8) nunca foi implementada. No código, uma tela desconhecida já volta ao estado conhecido
  (`sessao.py:318`, a correção da e31953). O que ainda chama pessoa é a tela de casa que mudou: dela, o "voltar" sai do
  app.
- Medido em 28/09 no banco do central (só leitura, `b25957e`):

| Medida | Valor |
|---|---|
| execuções reais desde 17/09 | 226 |
| tentativas | 1.157: 928 comprovadas, 146 falhas, 61 interrompidas, 14 incertas |
| etapas com falha seguida de sucesso na mesma etapa | cerca de 30 |
| receitas ativas | 81, das quais 22 com ação de commit (21 delas com o commit na primeira ação) |
| fluxos ativos | 25 |
| aprovações | 32, nenhuma editada |

- Há um defeito latente, também medido. `HistoricoDeAcoes` lê 30 dias de etapas com `LEFT JOIN ai_calls`, mas `ai_calls`
  é purgado em `log_retention_days=14` (`state.py:2096`). A partir de ~01/10, a projeção, o orçamento e qualquer régua
  de efeito sairiam subestimados. Corrigido no A1 (decisão 8).
- O saldo da API é pequeno (Anthropic, ~US$ 3), então o aprendizado não pode depender de chamada paga.

**Alternativas.**

- **Uma tabela física única com toda a carga, ou uma VIEW SQL:** descartada. Duplicaria receita e fluxo, que são donos
  do caminho quente, e a VIEW não passa pelos placeholders de dialeto nem pela cópia entre bancos. O livro é uma união
  em Python, com o mapeamento de estado testado no domínio.
- **Lições escritas por IA a partir das falhas:** descartada como padrão, porque gasta saldo e abre injeção. Fica
  desligada em `aprendizado.ia_resumos_por_dia: 0`.
- **Lição também no verificador:** descartada. O verificador recebe o mesmo `StepContext` do ator (`prompts.py:380`), e
  a lição empurraria o juiz a aceitar, contra "incerteza nunca conta como sucesso".
- **Lição tirada de falha repetida sem contraste:** descartada. Repetir a falha não prova o que funciona; o caso vai
  para o backlog.
- **"D1 por partes" nas receitas (reproduzir o prefixo e entregar o commit à IA):** descartada. Em 21 das 22 receitas
  com commit, o commit é a primeira ação: seria estado novo no Replayer sem ganho.
- **Rebaixar no deploy as receitas e os fluxos ativos com efeito:** descartada. Cortaria a economia de hoje sem ganho
  imediato; eles vão para a lista "Revisar" do dono.
- **Apagar o fluxo refutado para a `match_key` UNIQUE não travar o comando:** descartada. `flow_scope` e `flow_apps`
  cairiam em cascata. Em vez disso, `learn_from_run` passa a reaproveitar a linha não ativa.
- **Catálogo de ações sobreposto no banco e publicado pelo sistema:** descartada. O catálogo é contrato versionado; ação
  nova vira proposta (adoção como habilidade ou YAML commitado).
- **Perguntar ao fim de toda execução:** descartada pelo dono (D2).
- **Varredura periódica como único gatilho:** descartada. O digest em `on_run_settled` dá o sinal e o rebaixamento na
  hora; a curadoria de 15 min fica para agregados, aposentadorias e a prova da correção.

**Escolha.** Nove decisões:

1. **D1 (do dono): o sistema publica sozinho só o que não tem efeito externo e se repetiu.**
   - `requires_owner = side_effect OR human_origin`. O domínio calcula esse valor, e nenhuma rota o edita.
   - A tabela de transições reaproveita `SkillState` e `SYSTEM_ACTOR` das skills.
   - O repositório recusa, no próprio `UPDATE` com CAS e no `INSERT` de um item novo (`ciclo.conferir_nascimento`), que
     o sistema publique item que exige o dono. `decided_by` nunca fica vazio.
   - Treino é decisão de pessoa e continua publicando na hora.
   - Rebaixar é automático; promover algo com efeito ou com texto de pessoa é do dono.
   - Conteúdo desligado por uma pessoa não volta pelo sistema (veto por `content_hash`).
   - Receita com commit e fluxo com etapa de efeito param em `validated` e vão para "Para aprovar". O que já está ativo
     não muda sozinho e aparece em "Revisar".
2. **D2 (do dono): feedback implícito mais um botão opcional.**
   - Os gestos que a pessoa já faz viram sinais estruturados (`learning_signals`).
   - O botão "Deu certo / Deu errado + motivo" fica em cada item e na execução, sem modal e sem pergunta.
   - "Deu errado" por navegação rebaixa o que o item usou e o que ele aprendeu.
   - "Deu certo" em item que falhou vai ao backlog e não muda o desfecho.
   - Nota com cara de credencial é recusada (409).
3. **Um livro único na leitura e no ciclo de vida, não na carga.**
   - A migração **055** cria (a 054 ficou com a proteção de contas, ADR-055; os números se cruzam — este ADR usa a
     migração 055, e o ADR-055 a 054):
     - `learning_items` (tela, lição, voz, preferência);
     - `learning_transitions` (trilha única, inclusive de receita e fluxo);
     - `learning_signals`, `learning_evidence`, `learning_exposures`, `learning_daily` e `learning_backlog`;
     - as colunas `failure_kind` em `attempts` e `steps` e `failure_screen` em `attempts`.
   - Receita, fluxo, habilidade e memória continuam donos do próprio conteúdo; `GET /api/aprendizado` os reúne ([adendo
     v0.37](api-contract.md)).
   - Só evidência real (`runs.simulated=0`) promove algo, contada por execução e por aparelho distintos.
4. **Falha em vocabulário fechado.**
   - `repository.finish_attempt` classifica o erro final (`modules/learning/domain/falhas.py::FailureKind`).
   - Uma catraca por AST exige que todo motivo do executor caia fora de `outro`.
   - A camada e o "onde alterar" são derivados do tipo na hora da leitura.
5. **Lições medidas, só para o ator e o planejador.**
   - Origem: contraste, sem IA — falha seguida de sucesso na mesma etapa, ou defeito do plano seguido de plano que
     comprovou.
   - Texto: modelos fechados cujas lacunas só aceitam ação, tipo de falha, contagem, sufixo de id, `{parâmetro}` e
     rótulo curto que se repetiu entre execuções.
   - Entram em `DecisionRequest.lessons` e `PlanRequest.lessons`, nunca no verificador.
   - Teto: ator com 120 tokens e 3 lições; planejador com 150 tokens e 3 lições.
   - Publicada sem efeito, a lição entra "em prova", com braço de controle de 50% por etapa. O veredito exige 8 unidades
     por braço: "atrapalha" desliga na hora, "neutra" aposenta.
   - Nunca viram lição: autenticação, desafio, 2FA, CAPTCHA, conta de IA e falhas de infraestrutura.
6. **Fatia 5 (18.8, agora 20.9): telas aprendidas como dado de instalação.**
   - Nascimento: uma tela desconhecida, vista em etapa comprovada e sem árvore sensível, vira candidata.
   - Publicação sozinha (D1) com ≥3 observações em ≥2 execuções, prova local de reclassificação e zero conflito.
   - Uso:
     - a regra aprendida exige todos os seus ids (`ids_todos`);
     - entra depois das declaradas num `ConhecimentoDeTelas` unido (`com_aprendidas`), só como `autenticada`;
     - é pulada em tela sensível, com senha ou com desafio;
     - só entra no estado conhecido se mostrava a aba de perfil declarada.
   - A conta continua lida só pelo que o YAML declara.
   - O repositório segue como base curada: a regra aprendida exporta YAML e é aposentada quando o YAML a absorve.
   - Ação nova não entra no catálogo pelo banco: vira proposta.
7. **Backlog do que mais falha, com prova da correção.**
   - Grupo: app, ação, tipo de falha e tela. Ordem: US$ perdido + minutos + intervenções humanas. O falso positivo do
     verificador fica sempre no topo.
   - Prova: depois do commit implantado, ≥10 tentativas elegíveis com taxa ≤50% da linha de base marcam `fixed`; acima
     disso, `reopened`.
   - Um script e a skill `retomar` mostram o top 5.
8. **Régua durável.**
   - A janela efetiva da projeção passa a ser `min(janela_dias, log_retention_days)`, e a etapa só entra se começou
     dentro dela.
   - `learning_daily` e o desfecho das exposições são gravados antes da purga de `ai_calls`; o dia que a purga pode ter
     atingido nunca é recalculado.
9. **Nenhuma IA no pipeline, com modos por tipo.**
   - Digest por evento e curadoria determinística.
   - Modos `off | shadow | on`, no molde de `ai.recipes`. De fábrica, lições em `shadow` e telas em `observe`, até a
     primeira prova real (bloco `aprendizado` do `config.example.yaml`; os modos entre aspas, porque `off`/`on` sem
     aspas viram booleano no YAML).

**Consequências.**

- Um comando novo sem efeito paga o planejador duas vezes antes de o fluxo ser publicado: a execução que o gerou e uma
  que concorde com ela.
- Fluxo e receita novos com efeito esperam o dono, e a automação fica com a IA até a aprovação.
- O legado com efeito (as 22 receitas e os 17 fluxos com etapa de efeito) continua ativo até o dono revisar em lote. É
  um desvio consciente do D1 para o que já existia, visível em "Revisar".
- Um "deu errado" por navegação desliga na hora o fluxo e as receitas envolvidos. Reativar o que ESTAVA publicado custa
  um clique (o `desfazer` da resposta do voto). O que era candidato ou validado sai sem `desfazer`: aquela volta
  publicaria o que nunca passou pelo D1. Uma pessoa o publica pelo catálogo (revisão do A4). Tudo fica na trilha.
- Com o volume de hoje, o veredito de uma lição leva semanas. O painel mostra "faltam N", e o limiar não baixa.
- A tela aprendida só muda algo quando a tela de casa do app mudar; até lá, a prova do consumo é simulada.
- A voz não tem dado (nenhuma aprovação editada) e fica por último. Preferência é só sugestão, nunca resposta automática
  para ação com efeito.
- São sete tabelas e três colunas. O aprendizado só escreve em tabela legada o status de receita e fluxo, `failure_kind`
  e `failure_screen`.
- `GET /api/runs/{id}/projection` passa a devolver a janela efetiva (14 dias no central, não 30); o painel ainda não foi
  ajustado e mostra "últimos 14 dias".
- A fundação foi construída sobre a leva `aberto-*` (`7a02491`, implantada), que tocava os mesmos arquivos quentes. O A1
  adiantou do A3 a correção da janela e a régua diária, e tocou `executor.py` e `service.py`, que são do A2: o A2 faz
  rebase sobre essas linhas, e o A3 herda a régua pronta. Este desenho não toca `approvals.py`.
- **Pendências do revisor (A1, não bloqueiam):** a retenção que AUMENTA (14 → 30) reabre o defeito da janela por até 16
  dias, e só a régua o detecta; no PostgreSQL, a recusa do D1 em receita e fluxo é leitura seguida de CAS de status, e o
  A5 deve fazer o CAS também pelo `content_hash`; "Revisar" não tem corte "anterior ao D1" nem gesto "confirmado" que só
  grave na trilha (A5/A6); uma candidata levada a `validated` por pessoa faz a IA criar outra candidata na mesma chave
  (A5); `SourceKind.DISAMBIGUATION` é origem de pessoa mas não está em `FONTES_HUMANAS` (A9); `criar_item(estado=DRAFT)`
  ainda grava rascunho.
- **Regras que a revisão de A2–A9 fixou** (seis dos oito pacotes foram barrados pelo revisor e ajustados):
  - A3: a reincidência depois de `fixed` é medida nas últimas 2 × `prova_minimo` tentativas elegíveis
    (`JANELA_DA_REINCIDENCIA`), não desde a prova. Medida desde a prova, a janela só cresceria, e as tentativas boas de
    semanas diluiriam a volta da falha: a linha nunca reabriria.
  - A4: o `desfazer` do voto só existe para o que estava `published` (`reativar_desfaz`). Do `candidate`/`validated`,
    ele publicaria o que nunca passou pelo D1.
  - A5: a execução de habilidade com etapa confirmada à mão (`verified=false`) grava `uncertain`, nunca `passed`.
  - A6: a habilidade validada decide-se na fila "Para aprovar" pela rota das habilidades.
  - A7: a etapa confirmada à mão entra na medida de efeito como `unverified`, nunca como sucesso; a etapa livre de
    sessão, login ou desafio é recusada pela chave da etapa (`ACAO_DE_SESSAO`).
  - A9: a preferência de desambiguação só decide sozinha nas versões da habilidade que foram conferidas
    (`provenance.versoes`, fora do conteúdo para o veto por `content_hash` valer); numa versão nova, volta a pergunta
    com a opção pré-selecionada.
- **Pendências dos revisores (A2–A9, não bloquearam o merge):** lista por pacote em
  [dominios/aprendizado.md](dominios/aprendizado.md#pendências-conhecidas). As que tocam invariante:
  - ~~`PUT /api/flows` e `PUT /api/recipes` não gravam a trilha~~: fechado em 29/09 (`0f91fb3`, `c655495`). O
    interruptor antigo passa pelo mesmo serviço do livro, com a pessoa na trilha e o veto;
  - ~~`scheduler._learn_flow` grava "reaproveitam este plano" para um candidato inerte~~: fechado em 29/09. Com o
    aprendizado ligado, só a sombra do digest anuncia o candidato;
  - `aprendizado.fluxo.com_prova: false` e `ai.recipes_promote_after: 0` levam o sistema a publicar o que tem efeito. Os
    padrões são seguros, e o `config.yaml` do central não os muda;
  - `failure_screen` continua sem escritor. Quando passar a ser gravado, a linha do backlog aberta com a tela vazia pode
    cair a zero e parecer corrigida.
- **Dívida aceita na integração, PAGA em 29/09 (`2b0e5db`):** `devices/manager.py` importava `taskqueue.costuras`, o
  primeiro import `devices` → `taskqueue`. O contrato de gesto foi para `app/shared/costuras.py`, reexportado em
  `taskqueue.costuras`, e `test_aparelhos_nao_conhecem_a_fila` impede a volta.
- **Código em aberto fechado em 29/09 (tarde; [relatório §24](relatorio-validacao.md)):**
  - o bloco "Aprendizado desta execução" é emitido por `GET /api/runs/{id}/feedback`, e o painel lê a projeção;
  - os três sinais sem escritor ganharam escritor (`cancelou_execucao`, `comando_incerto_resolvido`,
    `correcao_de_ensino`), com o operador da sessão;
  - a nota da resolução e do cancelamento de comando passa pela triagem de credencial.

  **Fase 22 (29/09, noite; [relatório §25](relatorio-validacao.md)):**
  - sinal de GESTO é um por `(kind, source_ref)`, e o primeiro autor fica; o voto do D2 segue por pessoa;
  - a trilha nas lojas é acessória (savepoint e log, nunca derruba a escrita); a trilha da adoção de fluxo é o
    registro do gesto (a falha dela desfaz a adoção; a alternativa acessória é decisão do dono);
  - a tela da falha grava o nome declarado, ou o tipo do motor nas telas protegidas, e a linha sem tela do backlog
    mede o trio em qualquer tela.

  **As polaridades dos três sinais são escolha do pacote e esperam a ratificação do dono.** Hoje o único consumidor é
  o negativo humano da régua diária:
  - `cancelou_execucao`: neutro antes de rodar, negativo depois;
  - `comando_incerto_resolvido`: `succeeded` neutro, `failed` negativo, `cancelled` neutro;
  - `correcao_de_ensino`: negativo.

**Evidências.**

**Fundação (A1).** `real` (29/09/2026, máquina WIN-7S2UASNLFOP, central em `7a02491`, banco `data/poc.sqlite3` aberto
só leitura; às 02:23Z, e reproduzido pelo revisor às 03:01Z):

- **classificador** sobre as tentativas reais: 221 com falha (20 simuladas ficaram fora); `outro` = 4,5% (meta < 15%).
  Os 10 restantes são textos livres de `StepBlocked`, que o A2 marca como `ia_declarou_bloqueio`. Maiores grupos:
  `pos_condicao_nao_comprovada` 75, `interrompida` 61, `ia_indisponivel` 26, `prazo_da_etapa` 15,
  `efeito_nao_comprovado` 13;
- **livro** sobre as fontes reais: 81 receitas ativas e 10 em quarentena, 25 fluxos, 1 habilidade publicada e a memória
  como contagem (100 lembranças). "Revisar": as 22 receitas ativas com commit e 17 fluxos ativos com etapa de efeito.
  "Pendentes": nenhum. Nenhum estado nativo desconhecido.

`simulated`: `backend/tests/test_learning_ciclo.py` (a tabela D1 inteira contra uma reescrita independente),
`test_learning_falhas.py` (46, com a catraca AST sobre `executor.py`), `test_learning_repositorio.py` (CAS, índice
parcial vivo, sinais em upsert, a 055 nos dois dialetos; e os dois testes do ajuste `9c4fa4a`, que falhavam antes: o
sistema não cria item já publicado que exige o dono, pela matriz estado × efeito × fonte × ator, e a chamada de IA da
etapa que atravessa a meia-noite conta uma vez só na régua), `test_learning_livro.py` (rotas HTTP e digest no harness
5640) e `test_projecao.py` (sem a correção, mediana 0; com ela, 6 amostras e p50 3); mypy estrito sem erro em 153
arquivos. Na integração `c359f65`, os arquivos de teste alterados rodaram em SQLite e em PostgreSQL 17 (contêiner
`farm-pg`) e a suíte inteira, em SQLite: números no [relatório §22](relatorio-validacao.md).

Na data da fundação ficaram `not_run` o ensaio e o deploy da 055 e as leituras do livro no central. Os três viraram
`real` com o deploy de `c359f65` (29/09 ~03:55Z; [relatório §22](relatorio-validacao.md)).

**Entrega A2–A9 (item 20.3–20.10).** Oito pacotes em branches próprios, cada um com revisão adversarial, mais o dos apps
de segundo plano (item 21.15, no ADR-055). A integração `claude/rodada-aprendizado` → `main` é `f497075`: os nove merges
e o ajuste de tipo do lease em `devices/manager.py` pedido na revisão do A2. Tabela dos pacotes, commits e ajustes no
[relatório §23](relatorio-validacao.md).

`real` (29/09/2026, central WIN-7S2UASNLFOP em `f497075`, implantado às ~07:38Z sem migração nova; a 055 já estava
aplicada. `/api/health` `ok`, `problems: []`; agente do notebook em `0.1.0+f497075`. Só leitura, sem custo e sem efeito
externo):

- `GET /api/aprendizado`: 125 itens; `/revisar` 39; `/pendentes` 0.
- `GET /api/aprendizado/falhas` (A3), lida às 07:49Z com os padrões (14 dias, legado classificado na leitura):
  - 35 grupos, 19 acima do mínimo de 3 ocorrências, cada um com camada e "onde alterar"; `outro` = 10 de 221 (4,5%); 25
    propostas; nenhum sinal de pessoa desmentindo o verificador;
  - todas as ocorrências são retroativas: ainda nenhuma tentativa da janela tem `failure_kind` gravado pela execução;
  - maiores grupos: `interrompida` no QA Messenger (51, camada execução, `scheduler._reconciliar`);
    `pos_condicao_nao_comprovada` em OPEN_PROFILE do Instagram (36) e no QA Messenger (25), camada verificação;
    `efeito_nao_comprovado` em SEND_MESSAGE (8, verificação); `prazo_da_etapa` em OPEN_POST (4, aparelho);
    `ciclo_sem_progresso` em OPEN_POST (3, `ia_ator`); `efeito_alvo_errado` em CREATE_COMMENT (3, `ia_ator`).
- Lições (A7) e telas aprendidas (A8) rodam nos modos de fábrica (`shadow` e `observe`): nada vai ao prompt nem à
  sessão. A prova delas depende de as execuções reais acumularem dados.

`simulated`: os testes novos de cada pacote, que falhavam antes da correção ou sob as mutações dos revisores —
`backend/tests/test_costuras_de_aprendizado.py` e `test_falha_classificada_gravada.py` (A2, 14 mutações, todas
mortas), `test_learning_backlog.py`, `test_learning_rotas_falhas.py` e `scripts/tests/test_aprendizado_backlog.py`
(A3), `test_learning_feedback.py` (A4), `test_d1_fluxos.py`, `test_d1_receitas.py` e `test_learning_nativos.py` (A5),
`test_learning_licoes.py`, `test_learning_efeito.py` e `test_prompts_licoes.py` (A7), `test_learning_telas.py` (A8),
`test_learning_voz.py` e `test_learning_preferencias.py` (A9); no painel, o vitest de `features/aprendizado/` e
`FeedbackItem.test.tsx` (A6). Na integração `f497075`, em SQLite: os 17 arquivos de teste alterados deram 263 ok, e a
suíte inteira do backend, 3409 ok e 1 falha de ambiente (`test_backup`, que só passa no checkout com `config.yaml`).
Mais o mypy estrito sem erro em 164 arquivos e, no frontend, o typecheck e 711 testes ok.

`not_run`:

- a suíte em PostgreSQL para o SQL novo de A2–A9 (`ON CONFLICT … RETURNING`, `LIKE` parametrizado, lotes com
  `LIMIT/OFFSET`);
- `failure_kind` e `tomou_controle` gravados por uma execução real;
- lições: a exposição no prompt (modo `on`), o veredito de efeito (8 unidades por braço) e a aposentadoria;
- telas: o deixa-um-fora sobre observações reais (`scripts/aprendizado-telas.py --sem-regra thread --sem-regra feed`,
  depois de 24 a 48 h em `observe`), o export que carrega e o consumo real da tela de casa (só quando um app mudar);
- a voz publicada numa conta real e a preferência pré-preenchendo uma pergunta real;
- o voto numa execução real, com rebaixamento e reativação; um fluxo sem efeito publicado por concordância; um fluxo e
  uma receita com efeito esperando o dono;
- a prova de correção do primeiro item do backlog.

**Relação.**

- ADR-052: fatia 5 = 18.8, absorvida pelo 20.9; conhecimento como dado; repositório × instalação.
- ADR-053: falhas medidas; fluxo só com prova; e31953 e 02ee9e como casos do backlog.
- ADR-055: numeração cruzada (ADR-054 ↔ migração 055; ADR-055 ↔ migração 054), integrados juntos em `c359f65`.
- ADR-007 e ADR-036: receitas e fluxos; a receita é só fonte de decisão.
- ADR-037: fluxo legado como habilidade.
- ADR-032 e ADR-034: capability, skill e o ciclo de vida reaproveitado.
- ADR-024: o verificador fica fora das lições.
- ADR-009 e ADR-040: desafio, 2FA, CAPTCHA e credencial ficam fora do aprendizado; nada de evasão.
- ADR-030: o contexto novo `app/modules/learning`, com DAG e zero `Any`.
- ADR-047: respostas do assistente como sinal.
- ADR-016: quem decide é a sessão nominal.
- ADR-049 e ADR-051: provedor por papel e saldo; nenhuma chamada paga no pipeline.
- Fase 20 do plano-100; relatório §22 (fundação) e §23 (A2–A9); domínio em
  [dominios/aprendizado.md](dominios/aprendizado.md).

## ADR-055 — Proteção de contas: a conta travada para sem ser tocada, o aparelho entra em quarentena, uma conta por alvo e nenhum reset com conta

**Data:** 29/09/2026 · **Estado:** vigente; `e9da86e` implantado em 28/09; `c359f65` (+ `2511b12`) implantado em 29/09
~03:55Z, com as migrações 054 e 055 ensaiadas; `9348e9c` (o reparo espera a máquina aliviar) no ar desde 29/09
~04:17Z; os apps de segundo plano com `f497075` (~07:38Z); prova `simulated` nos pacotes e `real` no diagnóstico, nas
proteções operacionais,
nos experimentos, nas leituras depois do deploy e no incidente do android-01 · **Decisão do dono** (a regra de 29/09
e o "eu autorizo tudo" de 28/09, com as decisões (a)–(e) adotadas pela recomendação) e **decisão técnica** (cinco
pacotes com revisão adversarial); substitui em parte o ADR-029 e completa o ADR-009.

**Contexto.**

- Regra do dono, 29/09: "toda vez que para nessa tela de confirmar se você é humano é uma confirmação que a conta está
  bloqueada, essa é uma das formas de perder a conta". Cinco das oito contas do Instagram estão `blocked` (juliana,
  felipe, beatriz, thiago e mariana); vivas: lucas, bruno e andre.
- Investigação verificada por 65 agentes em 28/09 (só leitura de banco, backups, logs, git e código; nada escrito,
  nenhum aparelho tocado, nenhum segredo lido). O que os dados **mostram**:
  - o `blocked` não marca o instante do bloqueio. Houve três gravações: 23/09 21:10:42Z, as cinco em 74 ms, por um laço
    de PATCH de uma sessão Claude a partir da frase do dono ("as que sobraram funcionando são andre, bruno e lucas, o
    resto foi tudo bloqueada"); 24/09, a entrada no grupo "Recuperação"; 27/09, o desvínculo. O bloqueio automático do
    ADR-029 nunca disparou (0 eventos);
  - duas telas diferentes eram chamadas de "desafio": (i) o código por e-mail no 1º login, em 18/09 — cinco contas o
    viram, todas resolveram, e bruno e andre estão vivos; (ii) "Confirm you're human" (`ChallengeActivity`), vista só na
    juliana (relato do dono em 20/09 14:36Z) e no felipe (27/09 01:48Z; o dono a viu em 28/09 21:36, -03:00). Nenhuma
    das duas contas voltou. A regra do dono é sobre (ii), e os dados são coerentes com ela, com n = 2;
  - fatores comuns, de frota: as 8 contas cadastradas em 18/09 em 53 ms; 8 primeiros logins em 2h52 no mesmo host, com a
    mesma imagem API 34, o mesmo `ro.serialno`, o mesmo Instagram 447 e sem proxy; três ondas coordenadas sobre as
    mesmas pessoas — o mesmo comentário de 4 contas no mesmo post (18/09), 8 follows na mesma pessoa privada em 19m43s
    (19/09) e, depois de o dono mudar SEND_MESSAGE para `autonomous` nos 8 perfis, 7 DMs à mesma pessoa em 8m40s com o
    recado ditado "seu marido mandou um oi" (19/09, 22:03–22:11Z);
  - depois disso os aparelhos 04, 05, 07 e 08 hibernaram, e quatro das cinco contas nunca mais agiram.
- O que os dados **não mostram**:
  - o instante do bloqueio de nenhuma conta: há só janelas, e só a da juliana é observada nas duas pontas. Para beatriz,
    thiago e mariana o sistema nunca viu o desafio; o status é a palavra do dono;
  - o que separa as vivas: nada registrado. juliana e andre fizeram as mesmas 12 ações com 14 s de diferença; DM 5 de 5
    nas bloqueadas contra 2 de 3 nas vivas (Fisher p = 0,375); o lucas, vivo, é o 2º em volume;
  - a idade e o histórico das contas antes de 18/09, denúncias da destinatária e o motivo do Instagram; a hierarquia e o
    apóstrofo da tela real, nunca gravados.
- Conclusão: o padrão é de frota — contas novas, ligáveis entre si, agindo em rajada sobre uma pessoa real com um
  pretexto pessoal (K-057). É compatível com denúncia ou com detecção de coordenação, mas não explica a divisão 5 × 3.
- Buracos no código (lidos em `b25957e`):
  - sem campo de texto, "Confirm you're human" no meio da execução não contava como desafio, e o ator era instruído a
    dispensar diálogos inesperados;
  - quando visto, virava `auth_required` e voltava ao login automático; o padrão não casava com o apóstrofo tipográfico
    (U+2019) e dependia do idioma;
  - nada impedia comando, entrega, reset automático ou outra persona no aparelho de uma conta travada: o android-04
    recebeu um `open_app` em 27/09 01:47Z com o felipe já `blocked` e ainda vinculado; depois do desvínculo (27/09
    03:10Z), ficou no ar sem vínculo, com a sessão no disco e o desafio na tela;
  - a escada de reparo chegava ao `reset` sozinha — foi o que apagou a sessão do andre em 24/09 23:25Z;
  - o teto de frota não via curtida nem comentário (`counterparty` NULL), tinha janela de 1 h, só disparava com 3 outras
    contas e adiava em vez de recusar;
  - o grupo "Operação" dava SEND_MESSAGE `autonomous` às 3 vivas;
  - o verificador de DM errava nos dois sentidos (5 falsos negativos em 19/09 e um "Sending…" dado por enviado);
  - o objetivo em curso não parava quando o perfil virava `blocked`, e o login tentava de novo sem teto diário (juliana:
    6 envios de senha em 4h25 em 18/09);
  - mudar o status não deixava rastro, data nem origem.
- Incidente de 28/09, 21:36–21:40 (-03:00, `real`, K-053): um experimento de `hide_error_dialogs` no android-04 partiu
  da premissa de "aparelho sem conta" (`account_label` `qa-user-04`, `/personas` vazio) e mandou 10 entradas (toques e
  arrastos por adb) e aberturas a frio na tela de desafio do felipe, abrindo "Get support" e o assistente da Meta. Nada
  foi digitado nem enviado; a tela foi fechada com BACK.
- Incidente de 29/09, 02:05–02:15Z (`real`, K-058). O backend estava em `7a02491` desde 01:42Z
  (`data\logs\backend.log.2026-09-28`, em hora local), antes da regra "nenhum reset com conta", e a escada de reparo
  apagou a sessão do lucas.almeida9484, conta viva:
  - 02:05:31Z: `restart` do android-01, 2º degrau (motivo: "o `system_server` caiu");
  - 02:10:14Z: o `restart` falhou ("o Android subiu, mas não ficou pronto em 60 s": o preparo estourou o prazo);
  - 02:15:34Z: `reset`, 3º degrau (`c-20260929021534-6d15cd`, `requested_by` `system`, terminado às 02:20:27Z), que
    apagou o Instagram e a sessão.

  Causa assumida: a máquina central estava saturada pelo trabalho da IDE em paralelo — a suíte inteira, o Docker
  Desktop com os testes em PostgreSQL e o boot do android-17 do experimento de ANR. Os convidados chegaram a load 40–57
  em 2 vCPU (eventos das 01:56 às 02:00Z). O aparelho não tinha adoecido; faltava CPU à máquina. Desde então ninguém
  tentou o login do lucas (a conta não foi tocada), e o android-01 segue sem o app.

**Alternativas.**

- **Disfarçar a frota** (mascarar `ro.serialno`, impressão digital, imagem ou rede do emulador; proxy ou IP rotativo
  para esconder a origem comum; ritmo "humano" para passar despercebido): descartada e **proibida**. É evasão de
  detecção de emulador e de antibot (CLAUDE.md, ADR-009). O diagnóstico registra a ligabilidade como fato, não como alvo
  de correção.
- **Resolver o desafio pela automação** (tocar "Continue" ou "Get support", CAPTCHA, o assistente da Meta): descartada e
  proibida (ADR-009). O desafio é da pessoa.
- **Bloquear toda tela de "desafio"**, como o ADR-029 fazia com todo tipo em `TIPOS_DE_DESAFIO`: descartada. O código de
  login de 18/09 teria bloqueado bruno e andre, que estão vivos.
- **Adiar o excedente de frota** (o teto antigo esperava 3600 s): descartada. Adiar só espaça a coordenação; recusar a
  impede.
- **Manter o `reset` como 3º degrau da escada** (a "decisão do dono de 23/09" citada no código): descartada. Conflitava
  com a invariante do CLAUDE.md (resetar aparelho com conta real logada exige autorização em chat) e foi resolvida para
  o lado seguro.
- **Marcador de conta travada no vínculo ou no perfil:** descartada. O android-04 ficou sem vínculo depois de 27/09; o
  marcador é do aparelho e sobrevive ao desvínculo e à remoção do perfil.
- **Voltar `hide_error_dialogs` a 0**, para o ANR aparecer em vez de matar o app: descartada depois do experimento real
  (K-054).

**Escolha.** Cinco decisões do dono, de 29/09, adotadas pela recomendação:

- **(a)** "Confirm you're human"/`ChallengeActivity` = conta BLOQUEADA: `blocked`, sem reativação automática, aviso ao
  dono, nada toca.
- **(b)** Código de login por e-mail ou 2FA = "precisa de pessoa", SEM `blocked`.
- **(c)** Nunca reset automático de aparelho com conta vinculada ou travada.
- **(d)** No máximo UMA conta por alvo (pessoa ou perfil) para seguir, DM e comentário; DM a quem não tem conversa
  prévia sempre com aprovação; a persona nunca atribui fala ou intenção a terceiros reais.
- **(e)** Depois de 1 envio de senha sem sucesso, o login automático para até uma pessoa olhar.

Aplicadas por cinco pacotes, cada um com testes que falhavam antes e revisão adversarial (três barrados pelo revisor e
ajustados), mais três correções do parque e do host:

1. **Detector único de conta travada** (`detector`, `b185f0e` + `7a7b32b`; item 21.1).
   - Casa na UNIÃO dos idiomas, sobre o texto normalizado (apóstrofos tipográficos viram `'`, sem acento, minúsculas). A
     família `_CONTA_TRAVADA` (humano, robô, CAPTCHA, frases de checkpoint) vale SEM campo de texto; a `_CODIGO` (login
     e 2FA) exige campo no meio da execução, por causa da linha "Autenticação de dois fatores" do menu. Palavras soltas
     ("suspeito", "unusual") viraram frases, para uma DM com "achei suspeito" não bloquear conta viva.
   - Roda depois de cada observação, ANTES da reabertura por ANR, da receita e do ator; também em `ler_conta` e na
     dispensa do motor de sessão. Sai sem tocar, teclar nem reabrir.
   - Desfecho `auth_challenge` com subtipo: `conta_travada` bloqueia o perfil e cria o marcador (origem `observado`);
     `codigo` pede pessoa sem bloquear; `verificacao` — o ator relata `step_blocked(kind="challenge")`, julgamento do
     modelo e não casamento determinístico — pede pessoa sem bloquear. O subtipo e o trecho que casou vão na tentativa,
     no evento e em `blocked_reason`.
   - No motor de sessão, a regra declarada `two_factor` casa só pelo texto, sem exigir campo: na revisão, a tela de
     código com o campo num `android.view.View` virava "desconhecida", o motor voltava dela e ENVIAVA A SENHA DE NOVO
     (contra (b) e (e)).
2. **Quarentena do aparelho** (`quarentena`, `512e9bd` + `966f071` + `ec8b630`; migração 054; item 21.2).
   - Marcador por aparelho (`device_locked_accounts`: o @, a origem `observado`/`declarado`/`regra`, quem viu, a
     evidência e desde quando), que sobrevive ao desvínculo e à remoção do perfil. A migração carrega o do android-04
     com o felipe, declarado pelo dono.
   - Com marcador, só `stop` e `hibernate` passam sem `confirm_locked_account`, que o pedido automático nunca manda (409
     `locked_account`); vínculo, troca de aparelho e cadastro dão 409 `aparelho_em_quarentena`; nada de entrega de
     versão, proxy, reinício por irq, escada de reparo, prova de abertura nem scale-test; `/api/health` acusa
     `locked_account_on_device` enquanto o aparelho estiver ligado. Devolver o controle não reabre a sessão da conta
     travada.
   - O marcador só sai por pessoa ou pelo disco apagado de fato (boot com `-wipe-data`, ou `reset` remoto `succeeded`).
     Troca de identidade física NÃO o resolve: fica, com aviso no cartão (bloqueio do revisor).
   - Todo status de perfil passa por `mudar_status`: `blocked_at`, `blocked_evidence` e `blocked_origin`, e o evento
     `profile.status` com origem e autor. `account_label` é derivado do marcador ou do vínculo (`account_label_origin`).
3. **Frota por alvo** (`frota`, `f71dfd9` + `38311db`; item 21.3).
   - Curtida e comentário ganham contraparte: `post_author` nasce em OPEN_POST e é herdado por LIKE_POST, UNLIKE_POST e
     CREATE_COMMENT; toda ação com balde declara `counterparty` no catálogo, e a carga recusa quem não declara.
   - A porta conta todos os baldes, numa janela de 30 dias (`limits.fleet_target_window_days`), e os pedidos de
     aprovação em aberto reservam o alvo. Seguir, DM e comentário: `UMA_CONTA_POR_ALVO`, regra no código, não
     configuração. O excedente é recusado, não adiado.
   - DM fria (a pessoa nunca escreveu a esta conta) é sempre `approval_required`; nenhum grupo nem perfil afrouxa. O
     mesmo pedido a várias contas na mesma execução: só o objetivo de menor id segue, com aprovação forçada.
   - A persona não atribui fala a terceiros: regra no prompt social e trava em código
     (`social/conteudo.py::fala_atribuida_a_terceiro`), com uma reescrita e depois recusa.
4. **Disjuntor e conduta de login** (`disjuntor`, `4cc640b` + `0d29994`; item 21.4).
   - `_stop_reason` lê o status do perfil e o marcador: o objetivo em curso para no ponto seguro seguinte, em
     `waiting_user`. O despacho recusa objetivo de conta que não está `active`, em qualquer app.
   - Um perfil que passa a `blocked` pausa as execuções dele e das contas que agiram sobre os mesmos alvos nas 48 h
     anteriores (evento `log` com `reason=disjuntor_de_conta`).
   - Perfil `blocked` ou `disabled` nunca recebe a senha. Um envio sem sucesso põe a credencial em `review`: o login
     automático para sem tocar (etapa `login_parado`), só a pessoa (Conectar) tenta, e só se sai de `review` guardando a
     senha de novo ou com um login que confirma a conta; falha antes do envio não apaga o `review` (bloqueio do
     revisor). Teto diário `max_logins_per_day` (3 no `sessao.yaml` do Instagram).
5. **Verificador de DM** (`dm-verificador`, `377ed25`; item 21.5). "Sending…"/"Enviando…" (`pending_marks` no catálogo)
   = pendente, nunca enviada, sem chamar o modelo; bolha com o texto inteiro e o compositor vazio = enviada
   (`sent_text:id=row_thread_composer_edittext`). O "confirmar concluído" de etapa com efeito exige o print em que a
   pessoa se baseou (`evidence_id`, 422 `evidence_required`/`invalid_evidence`).
6. **Escada sem reset com conta** (`e9da86e`, implantado, e `quarentena`; item 21.6). O reinício por irq e o religar
   da reconciliação passam a `requested_by='saude'` e `'reconciliacao'` e deixam de contar como degrau (antes eram
   `system`, e dois deles abriam caminho ao `reset` com conta real). Com vínculo ativo, o 3º degrau é "Precisa do
   dono" + `stop`; com marcador, isso vem direto, sem `restart`. `reset` automático só em aparelho sem conta nenhuma.
   Essa parte entrou no ar com `c359f65` às 03:55Z de 29/09, 1h40 depois do `reset` do android-01 (Contexto), que
   aconteceu com o backend em `7a02491`.
7. **Parque e host** (itens 21.7–21.9): `hide_error_dialogs` continua 1 (K-054); o relógio do central passa a ser
   mantido por `scripts/sincronizar-relogio.ps1` e pela tarefa `farm-relogio` (K-055; mexer no relógio foi autorizado
   pelo dono em 28/09); aposentar apaga o AVD mesmo com arquivo somente-leitura do emulador (`2511b12`, K-056). Os apps
   do Google que sobem sozinhos nos convidados de 2 GB passam a ser desativados pelo preparo (`android.desativar_apps`,
   `e2b54a0` + `b5036ec`; item 21.15, K-059).
8. **O reparo espera a máquina aliviar** (`9348e9c`, item 21.16; resposta ao incidente de 29/09). Em
   `despacho.remediar`, com a CPU desta máquina em `instances.remediation_host_cpu_max` (90%) ou mais, o aparelho local
   não sobe de degrau: ganha o aviso "Reparo adiado: esta máquina está com N% de CPU…" no cartão, e a conferência volta
   em 10 min (`ADIAMENTO_POR_HOSPEDEIRO_S`). Reiniciar é o momento mais pesado de um convidado, e com a escada o passo
   seguinte era o `reset`. Aparelho de worker não entra nessa conta (a CPU medida é a do central). 101 desliga a espera;
   a suíte de testes usa 101.

**Conduta, não disfarce.** O que protege as contas é COMPORTAMENTO: uma conta por alvo, nenhuma rajada coordenada, DM
fria com aprovação, persona que não fala por terceiros, volume baixo e espaçado, parar no primeiro sinal de trava, não
insistir no login e não usar conta real como bancada (testes de navegação no QA Messenger). É evasão, e proibido:
mascarar emulador, imagem, `ro.serialno` ou rede; proxy para esconder a origem; resolver CAPTCHA ou desafio; tocar "Get
support"; qualquer artifício para o antibot não ver o que o sistema faz.

**Proteções operacionais aplicadas pela IDE em 29/09** (`real`, reversíveis, até a implantação):

- grupo "Operação" (lucas, bruno, andre): SEND_MESSAGE volta ao padrão `approval_required` (era `autonomous`), e os
  limites passam aos do grupo "Recuperação" — curtidas 10/h e 50/dia, comentários 3/h e 10/dia, follows 3/h e 10/dia,
  DMs 5/h e 20/dia, 8 ações por execução e 120 s entre ações externas;
- andre: removida a política própria `CREATE_COMMENT = autonomous`, que volta a `approval_required` (K-052);
- android-04, com o felipe logado em "Confirm you're human", desligado (`c-20260929013039-e1c891`).

**Conduta da IDE no central, depois do incidente de 29/09** (K-058): um trabalho pesado por vez na máquina central (a
suíte inteira, a suíte em PostgreSQL, o boot de um aparelho de experimento); processos de teste em prioridade ociosa,
postos assim por um vigia da sessão; o Docker Desktop e o WSL ligados só enquanto a suíte em PostgreSQL roda e
desligados depois (mexer no WSL continua exigindo autorização em chat, CLAUDE.md).

**Consequências.**

- A tela de trava para tudo, sem toque, em qualquer caminho; a conta vira `blocked` e o aparelho entra em quarentena. O
  dono fica sabendo pelo evento e pela saúde, não por olhar a tela.
- O ADR-029 muda em dois pontos: só `conta_travada` bloqueia (código pede pessoa); e o bloqueio vale também com a sessão
  já em `auth_challenge`, então um perfil reativado com a trava ainda na tela volta a `blocked` no próximo "Verificar
  conta" ou na próxima execução.
- Uma execução com várias contas no mesmo alvo deixa os objetivos recusados em `waiting_user` (não terminal) até alguém
  cancelar. Curtida de feed sem autor conhecido é sempre recusada.
- Uma conta logada que demore a mostrar tela reconhecível depois do envio (android-06 em 25/09, ~35 s) vai a `review` e
  passa a pedir pessoa.
- O painel antigo quebra o "Marcar como concluído" em etapa com efeito: o frontend vai no mesmo deploy (`npm run
  build`).
- `account_label` passa a ser o @ vinculado: um fluxo do QA Messenger que confira `Conta: {account_label}` no android-01
  passa a esperar `lucas.almeida9484`.
- Com a máquina central a 90% de CPU ou mais, um aparelho local doente de verdade espera: a cada 10 min o reparo
  reconfere e só sobe de degrau quando a máquina aliviar. O aviso no cartão diz por quê.
- **Pendências dos revisores:**
  - o painel não manda `confirm_locked_account`, não mostra `locked_account` nem `review`, e não há rota para resolver o
    marcador (hoje, só `curl` ou o banco); a rota em lote sempre recusa;
  - `captcha` e `i'm not a robot` seguem como sinais soltos sem campo: uma legenda ou DM com essas palavras pode
    bloquear uma conta viva;
  - `_verify` e o `peek` de `expect_done` não passam pelo detector; `classificar` devolve `outro_app` antes dele (um
    desafio num WebView de outro pacote não é visto pelo motor de sessão); o @ da tela de trava não é comparado com a
    conta pedida;
  - `post_author` vem do planejador e não é conferido na tela; a trava de atribuição deixa passar 7 de 20 variantes
    testadas ("seu esposo…", "tá com saudade", o gerúndio, "a pedido do seu marido") e não se aplica ao
    `content_verbatim`;
  - a corrida de login entre dois aparelhos cobre `review`, não `invalid`; o disjuntor só dispara para bloqueios vistos
    com o backend no ar; o planejamento pago ainda roda para perfil bloqueado; aparelho sem persona vinculada passa sem
    política nem frota;
  - `textos.desafio` do `sessao.yaml` ainda diz "resolva na tela e devolva o controle", texto errado para a conta
    travada.
- **Decisões do dono pendentes:**
  - as senhas das contas estão em texto puro num transcrito local antigo, fora do repositório
    (`C:\Users\Administrator\.claude\projects\C--git-android\1a884c21-6b61-42ac-8c11-dfd781e97346.jsonl`, 18/09): trocar
    as das 3 vivas e apagar o arquivo. Daqui em diante, credencial só pelo cofre (ADR-040);
  - remover ou não do cofre as credenciais das 5 bloqueadas (hoje `active`, com consentimento);
  - as escolhas do pacote frota: a regra de uma conta vale para o balde inteiro (inclusive responder a quem escreveu e
    aceitar pedido de seguir); aprovação dada que nunca chega ao efeito reserva o alvo por 30 dias; a mesma persona em
    dois aparelhos não é "outra conta"; o texto exato do dono (`content_verbatim`) fica fora da trava de atribuição;
  - quando e como reativar o lucas.almeida9484, cuja sessão o `reset` de 29/09 apagou. Recomendação: um único login,
    acompanhado pelo dono, pelo Conectar do painel, num horário calmo da máquina. Até lá, nada toca a conta (a conduta
    de login (e) vale: depois de 1 envio sem sucesso, o login automático para).

**Evidências.**

`real`:

- **diagnóstico das 5 contas** (28/09; banco, backups, logs e git do central, com o checkout em `b25957e` e o backend em
  `e9da86e`): os números do Contexto;
- **`e9da86e` implantado** no central em 28/09;
- **relógio:** desvio de +6,240 s para +0,004 s em 28/09 21:25 (-03:00), `data\logs\relogio.log` (K-055);
- **proteções operacionais** de 29/09, acima, e o android-04 desligado (`c-20260929013039-e1c891`);
- **experimento `hide_error_dialogs`** no android-17 (K-054) e o **incidente** no android-04 (K-053), resultado negativo
  registrado como lição;
- **leituras GET** no central pelos pacotes: o incidente `r-20260919220216-7cfa59` (7 contas, a mesma pessoa,
  22:03–22:11Z); todo `post_liked` e `comment_replied` com `counterparty` NULL; android-04 `stopped` com `account_label`
  `qa-user-04` e o felipe `blocked` sem vínculo; a evidência #969 ("Sending…" dado por enviado) e #959, #961 e #968 com
  `row_thread_composer_edittext`;
- **deploy de `c359f65`** em 29/09 ~03:55Z (054 e 055 ensaiadas numa cópia restaurada do backup
  `data/backups/20260929-005459`): `POST /api/instances/android-04/actions/open_app` recusado com 409 `locked_account`
  ([relatório §22](relatorio-validacao.md));
- **incidente do android-01** (29/09, central em `7a02491`): `restart` às 02:05:31Z (2º degrau), falho às 02:10:14Z,
  e `reset` `c-20260929021534-6d15cd` às 02:15:34Z (3º degrau, `requested_by` `system`), com os convidados a load
  40–57 em 2 vCPU nos eventos das 01:56 às 02:00Z; nenhuma tentativa de login do lucas depois disso
  ([relatório §23](relatorio-validacao.md));
- **`9348e9c` no ar** às ~04:17Z (o backend reiniciado; `backend.log`: "este servidor roda 0.1.0+9348e9c" às
  04:18:21Z), e segue no ar com `f497075` (~07:38Z; `/api/health` `ok`, `problems: []`). O adiamento ainda não foi
  visto num episódio real de saturação.

`simulated`: `backend/tests/test_detector_conta_travada.py` (62), `test_quarentena_de_conta.py`,
`test_protecao_de_frota.py` (45), `test_disjuntor_de_conta.py`, `test_conduta_de_login.py` (12),
`test_dm_verificador.py` (22) e `test_provisionamento.py` (`2511b12`), cada um falhando antes da correção (base
`7a02491` ou mutação), com as sondas dos revisores rodadas de novo depois dos ajustes. Árvore integrada `c359f65`:
arquivos de teste alterados em SQLite e em PostgreSQL 17 (contêiner `farm-pg`), mypy estrito, frontend e a suíte inteira
no [relatório §22](relatorio-validacao.md). O adiamento do reparo (`9348e9c`):
`backend/tests/test_saude_do_convidado.py::test_hospedeiro_sobrecarregado_adia_o_reparo_em_vez_de_subir_de_degrau`
(95% de CPU: nenhum comando e o aviso "Reparo adiado"; 30%: `restart`). Os apps de segundo plano:
`backend/tests/test_apps_de_fundo.py` (12 mutações do revisor, todas mortas).

`not_run`: o adiamento do reparo num episódio real de saturação; o detector numa tela de trava real (o apóstrofo e a
hierarquia nunca foram gravados); o planejador real preenchendo `post_author`; uma execução com várias contas no mesmo
alvo; DM fria e verificador de DM numa conta real (efeito em conta de terceiros, exige autorização); o disjuntor com
bloqueio real; o "Enviando…" do Instagram em português.

**Relação.** ADR-009 (desafio, 2FA e CAPTCHA são da pessoa; nada de evasão); ADR-029 (substituído em parte: só a conta
travada bloqueia, e o bloqueio vale também fora da entrada do estado); ADR-040 (a credencial é da conta; `review` é
estado dela); ADR-053 (o `restart` por irq deixa de ser `system`; `hide_error_dialogs` segue 1); ADR-052
(`counterparty`, `pending_marks` e `sent_text` como dado no catálogo); ADR-054 (numeração cruzada: o ADR-054 usa a
migração 055, e este a 054); ADR-019 (relógio); K-052 a K-059; Fase 21 do plano-100; relatório §22 e §23; ADR-056 (29/09, decisão do dono: substitui em parte a
proibição de rede das Alternativas e da Conduta; o resto segue).

## ADR-056 — Rede por aparelho: VPN dentro do Android com proxy encadeado, saída medida e revisão da cláusula de rede do ADR-055

**Data:** 29/09/2026 · **Estado:** vigente (decisão); implementação planejada na Fase 25, `not_run` · **Decisão do
dono** (pergunta da sessão de planejamento de 29/09, opção "Rever o ADR-055 por ADR novo") e **decisão técnica** (a
composição); substitui em parte o ADR-055 (só a cláusula de rede).

**Contexto.**

- Pedido do dono de 29/09 (`prompt-outlook-comandos-multiapp-tarefas-continuas.md`): VPN e proxy configurados
  individualmente por dispositivo, "com isolamento, persistência e comprovação da rota e do IP de saída efetivos", e,
  "como objetivo de separação", "saída pública distinta e estável por dispositivo".
- O ADR-055 (29/09, madrugada) descartou e proibiu "proxy ou IP rotativo para esconder a origem comum" e "mascarar
  … rede do emulador"; o K-057 repete. O proxy por aparelho já existia (migração 041, pedido do dono de 26/09), mas
  só grava o proxy HTTP global do Android e relê o valor: prova a configuração, não o tráfego, e não tem autenticação.
- Fontes técnicas conferidas em 29/09:
  - [Proxy no Android Emulator](https://developer.android.com/studio/run/emulator-networking-proxy): o `-http-proxy`
    encaminha TCP e "doesn't support UDP redirection"; não tem lista de exceções; a senha iria no argumento do processo;
  - [VPN no Android](https://developer.android.com/develop/connectivity/vpn): um serviço VPN ativo por usuário (um
    segundo derruba o primeiro); always-on desde o Android 7; "Block connections without VPN" bloqueia o tráfego fora da
    VPN no sistema;
  - `VpnService.Builder.setHttpProxy`: o proxy é recomendação; o app pode ignorá-lo.

**Decisão.**

1. **A saída de rede é propriedade do aparelho**, configurada pela plataforma, persistente e medida. Endpoint,
   configuração e IP de saída observado são campos distintos; configuração diferente não prova IP diferente, e dois
   aparelhos com o mesmo IP medido geram aviso.
2. **Composição:** a VPN roda dentro do Android, num único cliente `VpnService` com always-on e bloqueio de conexões
   fora da VPN. O proxy, quando houver, é encadeado dentro do mesmo cliente (app → TUN → proxy autenticado → túnel →
   saída). O `-http-proxy` do emulador não é usado; rota no host do central ou do worker também não (o túnel e o ADB
   passam por ele). O proxy global legado fica, rebaixado: no máximo "configurado".
3. **Cinco estados por aparelho:** `pendente`, `configurado`, `conectado`, `trafego_verificado`, `parcial`. Só
   `trafego_verificado` libera tarefa com política exigida, e só com medição de dentro do aparelho, por app (o teste no
   navegador não prova os outros apps).
4. **Cliente:** WireGuard oficial ou sing-box, ambos da Play Store com a conta do dono, escolhido pela medição do item
   25.1. Nada de implementação própria de VPN antes disso.
5. **Segredos:** chave, senha de proxy e certificado no cofre por `secret_ref`; um segundo consumidor de `get_secret`,
   restrito à provisão de rede; entrega por stdin ou arquivo no convidado, nunca argumento de processo, evento, log ou
   evidência; a redação por formato ganha `socks5://`, `PrivateKey` e `PresharedKey`.
6. **Continua proibido:** rotação de IP, mascarar emulador, imagem, `ro.serialno` ou impressão digital, resolver
   desafio ou CAPTCHA, e qualquer artifício para o antibot não ver o que o sistema faz. A conduta do ADR-055 (uma conta
   por alvo, sem rajada coordenada, DM fria com aprovação, parar no primeiro sinal de trava) continua valendo inteira.
7. **Aparelho com conta real logada só muda de saída com autorização do dono por aparelho**, fora de uso, com a conta
   conferida antes e depois. A loja (android-11) e o aparelho em quarentena ficam fora.

**Alternativas.**

- **Manter o ADR-055 e tratar a rede só como isolamento, sem IP distinto como objetivo:** recomendada pela sessão da
  IDE e **não adotada** pelo dono. O motivo da recomendação fica registrado: trocar o IP de saída de uma conta logada
  costuma disparar verificação, e 5 de 8 contas já foram perdidas (K-057). Por isso o item 7.
- **Proxy do emulador (`-http-proxy`):** descartado: não cobre UDP (QUIC sai direto), não bloqueia a saída direta,
  expõe a senha no argumento e invalida o snapshot da hibernação.
- **Dois clientes, um de VPN e outro de proxy:** descartado: disputam o único serviço VPN do usuário.
- **Rota ou VPN no host:** descartado: separa pouco (todos os aparelhos do host saem juntos) e mexe no caminho do túnel
  e do ADB, que exige autorização.

**Consequências.**

- Fase 25 do plano-100 (25.1–25.10); migração 057; adendo novo do contrato; painel Rede no lugar da aba Proxy.
- IP de saída distinto por aparelho depende de provedor e de quantidade de endereços que o dono ainda não forneceu:
  sem isso, a prova fica `not_run` com a dependência exata, e configurações diferentes não são apresentadas como IPs
  diferentes.
- O invariante do `CLAUDE.md` contra evasão fica; ganha a ressalva de que a rede por aparelho sob este ADR é
  configuração declarada e medida.

**Evidências.** Diagnóstico em [design/terceira-evolucao.md](design/terceira-evolucao.md) §2.2 (`devices/proxy.py`,
migração 041, `devices/sonda_rede.py`, `devices/emulator.py`, `security/redaction.py`), leitura de `GET /api/proxies`
em 29/09 (nenhum perfil cadastrado). Nenhuma prova de funcionamento ainda.

**Relação.** ADR-055 (substituído em parte: a cláusula de rede); ADR-009 (desafio é da pessoa); ADR-040 (segredo no
cofre); ADR-026 (desejado × observado); ADR-002 (o túnel não é tocado); K-057, K-059; Fase 25.

## ADR-057 — Outlook como primeiro app novo: conta por app, sessão por conta e credencial clonada no cofre

**Data:** 29/09/2026 · **Estado:** vigente (decisão); implementação planejada na Fase 23, `not_run` · **Decisão do
dono** (o pedido de 29/09 escolhe o Outlook, que era a decisão pendente do 12.3; a credencial clonada foi escolhida na
pergunta da sessão de planejamento) e **decisão técnica** (o desenho).

**Contexto.**

- O pedido de 29/09: Outlook na loja e em todos os perfis; "os perfis que já têm conta no Instagram também possuem um
  e-mail Outlook criado, com o mesmo login e a mesma senha utilizados naquela conta do Instagram".
- A loja e a distribuição já são genéricas por pacote. O login gerenciado não é: credencial, tentativa, sessão e
  invalidação são resolvidas pela conta do app âncora (o Instagram), e a interface de sessão não recebe a conta
  (`integrations/app_declarado/sessao.py`, `state.py`, `modules/identity/application/ports.py`). O motor só entende um
  formulário com usuário, senha e botão na mesma tela, e lê a conta por uma aba inferior. Um desafio em qualquer app
  bloqueia a persona inteira. Não há caminho para reaproveitar a senha de outra conta.

**Decisão.**

1. O Outlook é o primeiro app do 12.3. Ele **não** é âncora; o primeiro marco é o caminho livre (só `app.yaml`), e o
   login gerenciado entra depois de desancorar o motor.
2. `SessionProvider.ensure_session` passa a receber a conta; tudo o que hoje cai na âncora passa a ser por conta e
   pacote.
3. Formulário em etapas, leitura da conta fora da aba inferior e Custom Tab com host declarado entram no motor
   genérico, declarados em YAML (ADR-052: zero Python por app).
4. **Credencial clonada no cofre:** `SecretStore.clonar(ref)` cria uma entrada nova com o mesmo valor sem que o valor
   saia do módulo; só entre contas da mesma persona; o consentimento nunca é clonado (o dono dá o de cada conta
   Outlook); trocar ou apagar uma não afeta a outra.
5. O endereço de cada conta Outlook vem do dado conferido pelo dono; nunca é derivado do usuário do Instagram. Perfil
   sem conta identificada recebe o app e fica com a pendência.
6. Catálogo inicial só de leitura; enviar e-mail fica `manual_only`. Desafio, 2FA e CAPTCHA da Microsoft seguem com a
   pessoa (ADR-009).

**Alternativas.** Redigitar a senha no painel (sem código novo, mais lento; não adotada pelo dono); compartilhar a
referência do segredo entre as contas (descartada: trocar ou apagar uma afeta a outra); tornar o Outlook âncora
(impossível: dois âncoras derrubam o registro).

**Consequências.** Fase 23 (23.1–23.13). O `ehInstagram` do painel e a política de ações sem app deixam de existir. A
troca das senhas das três contas vivas, pendente do dono, passa a ser por conta.

**Evidências.** [design/terceira-evolucao.md](design/terceira-evolucao.md) §2.3.

**Relação.** ADR-040 (a credencial é da conta; aqui, uma conta por app); ADR-052 (app como dado); ADR-043 (vínculo por
app); ADR-055 (quarentena, uma conta por alvo); ADR-009; item 12.3.

## ADR-058 — Comando entre aplicativos: catálogo pelo app da etapa e valor lido entre etapas

**Data:** 29/09/2026 · **Estado:** proposto (desenho da Fase 24; a implementação confirma ou corrige) · **Decisão
técnica** sobre o pedido do dono de 29/09.

**Contexto.** O 12.1 deu app por etapa, contexto por etapa e portas por app no despacho, mas: citar outro app derruba
o catálogo e a porta de política recusa efeito sem capability; o planejador com catálogo conhece um app só; nenhum
valor passa de uma etapa a outra; a conta esperada é a do aparelho; roteamento e modo Automático olham um app
([design/terceira-evolucao.md](design/terceira-evolucao.md) §2.4).

**Decisão.**

1. O planejador recebe os catálogos de todos os apps exigidos e compõe cada etapa pelo seu `app_id`; `required_apps`
   é sempre preenchido.
2. A regra da porta de política não muda: efeito sem capability em app com catálogo continua recusado.
3. Saída de etapa tipada e nomeada, persistida (migração 056) e reaproveitada na retomada. Código de verificação,
   senha e token nunca são saída: a etapa para (ADR-009, ADR-022). O exemplo do 12.1 que lia um código no Outlook é
   corrigido.
4. Conta esperada e portas de app, internet, sessão e rede são as do app de cada etapa, repassadas a cada troca.

**Alternativas.** Só skills compiladas para vários apps (já funciona, mas exige ensino antes de todo comando); plano
livre sem catálogo (descartado: a porta recusa efeito, com razão).

**Relação.** ADR-032/033 (capability e DSL); ADR-044 e ADR-050 (roteamento); ADR-047 (sucessora); ADR-009; Fase 24.

## ADR-059 — Pedidos persistentes pertencem ao produto: pedido, ocorrência e execução

**Data:** 29/09/2026 · **Estado:** proposto (a Fase 26 pesquisa e decide) · **Decisão técnica** sobre o pedido do
dono de 29/09.

**Contexto.** O dono quer pedidos que continuem ativos: agendados, recorrentes, por evento ou condição, e acompanhados
pela persona, com colaboração entre personas. Hoje não há agendamento, recorrência nem gatilho; há espera com hora
marcada, idempotência por chave, posse por prazo, sucessora e o gancho de fim de execução.

**Decisão (ponto de partida da pesquisa).**

1. Estado e execução ficam no backend e nos workers, com continuidade entre reinícios; nada depende da sessão da IDE
   nem das ferramentas de agendamento dela.
2. Um pedido gera ocorrências, e cada ocorrência gera execuções (`runs`) comuns; a identidade da ocorrência é a chave
   de idempotência, para nenhum efeito sair duas vezes.
3. O vocabulário de partida para agendamento é o de sistemas consolidados (política de sobreposição, janela de
   recuperação, `coalesce`: [Temporal Schedules](https://docs.temporal.io/schedule),
   [APScheduler](https://apscheduler.readthedocs.io/en/master/userguide.html), consultados em 29/09). Adotar
   biblioteca é resultado da pesquisa, não premissa.
4. Colaboração entre personas é divisão interna de trabalho; para fora valem uma conta por alvo (ADR-055), aprovação e
   a proibição de simular apoio de pessoas independentes.

**Relação.** ADR-047, ADR-051, ADR-054, ADR-055; Fase 26; [design/pedidos-persistentes.md](design/pedidos-persistentes.md).

## ADR-061 — A prova de vazamento é da linha do aparelho: presa à revisão e ao cliente VPN, sem validade por relógio

**Data:** 30/09/2026 · **Estado:** aceito · **Decisão técnica** (item 29.2; pendência P16 da terceira evolução).
O número 060 é o que [design/pedidos-persistentes.md](design/pedidos-persistentes.md) §5 nomeia para a Fase 28.

**Contexto.** A política `exigida_com_bloqueio` (ADR-056 §3) só libera tarefa com o bloqueio fora da VPN provado, e
a prova é um teste destrutivo: o cliente VPN é parado, a sonda precisa ser recusada pelo Android e, quase sempre, o
aparelho reinicia para religar o cliente. O desfecho vivia na memória do backend. Medido em 30/09: um reinício do
backend às 02:40Z custou 11 reinícios de aparelho horas depois (a remedição a 90% da validade refez o teste em três
aparelhos, dois com conta real), e cada reinício ainda rolou o dado do túnel que não sobe no boot.

**Decisão.**

1. A prova mora em `device_network` (migração 063): revisão, instalação do cliente VPN, resultado, quando, detalhe e
   a marca de ensaio pendente. Vale com a revisão pedida, o cliente lido agora no aparelho e resultado positivo.
2. É ela que decide `trafego_verificado` na política com bloqueio e que a porta da tarefa consulta. A medição
   registra o que levou; não declara o bloqueio.
3. Invalidam: revisão nova, cliente de outra instalação, wipe ou identidade, e `POST …/verify`. **O relógio não**: a
   validade é da medição barata, e o bloqueio em vigor é relido a cada conferência. Reaplicar a mesma revisão não
   invalida.
4. A intenção do ensaio é gravada antes do `force-stop`; o desfecho, com CAS pela revisão. Ensaio sem desfecho e sem
   ninguém o rodando é fechado como inconclusivo, sem parar o cliente de novo.
5. Negativo, inconclusivo e ausente nunca aprovam. Negativo e inconclusivo não se refazem sozinhos.
6. Na primeira subida, a prova anterior é adotada só com aparelho, revisão, histórico e data do cliente demonstrados;
   a migração não escreve prova.

**Alternativas.** Tabela própria de testes: exigiria junção na porta, perguntada a cada segundo, e limpeza; a linha já
tem o CAS por revisão. Refazer o teste por validade: troca uma propriedade do sistema, que a deriva já vigia, por
reinícios certos em aparelhos com conta real. *Backfill* na migração com o cliente vazio valendo "até a primeira
leitura": atribuiria a prova a um cliente desconhecido. Política `exigida` sem bloqueio: abre mão do que o ADR-056 §3
exige. Manter a prova antiga até o teste novo no `verify`: deixaria a tarefa passar com uma prova que a pessoa pediu
para refazer.

**Consequências.** `verify` com bloqueio segura a tarefa até o teste novo (minutos). Voltar ao código anterior
reintroduz o defeito: as colunas ficam e são ignoradas, o que torna a volta possível, não segura.

**Relação.** ADR-056 (rede por aparelho); K-063 (o teste numa ida só); K-065; item 29.3 (o túnel que não sobe no boot).

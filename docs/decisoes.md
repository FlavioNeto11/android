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
| [ADR-004](#adr-004--segundo-backend-real-infraestrutura-pronta-sem-topologia-em-uso) | Segundo backend real: infraestrutura pronta, sem topologia em uso (decisão 9) | pendente do dono | 22/09 |
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
| [ADR-025](#adr-025--a-automação-digita-a-credencial-que-a-pessoa-fornece-com-consentimento) | A automação digita a credencial que a pessoa fornece, com consentimento | vigente | 26/09 |
| [ADR-026](#adr-026--todos-os-aparelhos-sempre-na-versão-promovida) | Todos os aparelhos sempre na versão promovida | vigente | 26/09 |
| [ADR-027](#adr-027--prévia-e-observação-sob-demanda-medição-agregada) | Prévia e observação sob demanda; medição agregada | vigente | 26/09 |
| [ADR-028](#adr-028--runtimes-executores-e-orquestração-o-que-fica-como-está-e-o-que-reabre) | Runtimes, executores e orquestração: o que fica como está e o que reabre | vigente | 26/09 |
| [ADR-029](#adr-029--desafio-de-segurança-do-instagram-bloqueia-o-perfil-sozinho) | Desafio de segurança do Instagram bloqueia o perfil sozinho | vigente | 27/09 |
| [ADR-030](#adr-030--monólito-modular-incremental-com-regras-de-dependência-verificadas) | Monólito modular incremental, com regras de dependência verificadas | vigente | 27/09 |
| [ADR-031](#adr-031--contratos-compartilhados-do-worker-e-manifesto-único-do-agente) | Contratos compartilhados do worker e manifesto único do agente | vigente | 27/09 |
| [ADR-032](#adr-032--capability-skill-e-process) | Capability, Skill e Process | vigente | 27/09 |
| [ADR-033](#adr-033--ir-de-skill-e-dsl-automationv1alpha1) | IR de skill e DSL `automation/v1alpha1` | vigente | 27/09 |
| [ADR-034](#adr-034--versionamento-de-skill) | Versionamento de skill | vigente | 27/09 |

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

**Data:** 26/09/2026 · **Estado:** vigente · **Substitui em parte:** ADR-009 (a recusa de digitar senha)

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
- O teste 4 da §12.1 (execução com `runs.skill_id` e hash igual a uma recompilação): `not_run` até a fase G. Nada
  implantado.

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
- A transição `draft → candidate` só foi exercida com o validador falso (`backend/tests/fake_skills.py`): o validador
  de produção é da fase G.
- PostgreSQL com 042–046: `not_run`. Nada implantado.

**Relação.** ADR-007; ADR-020; ADR-032; ADR-033; [`dominios/skills.md`](dominios/skills.md);
[`banco.md`](banco.md).


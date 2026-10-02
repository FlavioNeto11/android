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

## 2026-10-02 — Papel de IA `persona` para a geração de persona (17.8, branch jev/17-8-papel-persona)

- `backend/app/config.py`: `AI_ROLES` ganha `persona`; `Config.ai_role` resolve `persona` SEM `ai.roles.persona` como o `social` (o bloco dele e, nos perfis do 17.7, a camada `social` e depois a `persona`); com bloco próprio o do `social` não vale para ela. `ROLE_DEFAULTS`, `ai_model_for` e `ai_effort_for` tratam `persona` como `social`: instalação existente não muda sem mexer na configuração.
- `routing.py::generate_persona` chama o papel `persona` (mesmo semáforo do `social` enquanto `ai.roles.persona.concurrency` não for escrito); `anthropic_provider.py`/`openai_provider.py` gravam `role="persona"` no uso. A resposta social da execução segue `social`. Sem migração: `ai_calls.role` é texto livre (linhas antigas de persona ficam `social`).
- Painel: `custos.ts` lê o papel `persona` (cai no `social` em backend antigo), rótulos e tipos da aba IA e dos custos.
- Docs: `docs/ia.md` §1 e o parágrafo do 17.8 (como apontar a persona para `openai-flex`), `docs/dominios/persona.md`, `config/config.example.yaml`, `docs/api-contract.md` (adendo v0.48).
- Prova `simulated`: `backend/tests/test_papel_persona.py` (20 testes: herança do social, bloco próprio, perfis do 17.7, validação da partida, roteamento e papel no uso, vagas compartilhadas) e `frontend/src/features/profiles/custos.test.ts`. `real`: `not_run` (sem chamada ao flex).

## 2026-10-02 — Aviso fora do painel pelo Telegram (28.11, branch feat/28-11-aviso-telegram)

- Decisão do dono (02/10): o canal é o **Telegram**, por um bot do @BotFather; só saída (sem webhook nem rota de entrada). O aviso é o ESPELHO da caixa de Pendências (ADR-062), não um conceito novo: a mensagem leva só o tipo do evento e o link `<avisos.url_painel>/#/pendencias`, nunca persona, conta, conteúdo nem dado de terceiro.
- Módulo novo `backend/app/modules/avisos/` (`domain/mensagem.py`, `application/entrega.py`, `adapters/telegram.py`, `infrastructure/fila_sql.py` e `servico.py`). Assina `approval.pending`, `run.updated` com `needs_input`, `session.needs_person` (só a entrada) e `pedido.aviso` (28.9, ainda não emitido: assinatura pronta e testada com evento sintético; só o aviso que pede pessoa leva o link). O item "Para aprovar" do Aprendizado não tem evento no barramento e não gera aviso.
- Migração **068** `avisos_entregas` (número confirmado pela coordenação): fila durável com `chave` UNIQUE (o mesmo fato visto por duas réplicas é uma linha), estado, tentativas, `proximo_envio_em` e `ultimo_erro` sem segredo. Só o líder da trava nova `avisos` (`taskqueue/travas.py`) envia, com a reivindicação cercada pelo token; `enviando` abandonado por queda vira `incerto` e NÃO é reenviado. 429 respeita o `Retry-After`; pendente com mais de `avisos.validade_h` vira `descartado`.
- Config: bloco `avisos` (`enabled: false`, `url_painel`, `intervalo_s`, `lote`, `timeout_s`, `max_tentativas`, `backoff_s`, `validade_h`, `incerto_apos_s`, `retencao_dias`; exemplo em `config/config.example.yaml`). Segredos de nome fixo no `.env`, por `EnvSettings`: `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`. Saúde: `avisos_sem_segredo` quando ligado e faltando um deles.
- `scripts/avisos-telegram.py descobrir|testar`: a descoberta do `chat_id` (getUpdates SEM offset, só lê) e a mensagem de teste. Escolhido script e não rota: é passo de instalação, funciona antes do restart com o token novo e não cria rota que dispara chamada de saída. Procedimento de 6 passos em `docs/operacao.md` §15.
- Prova `simulated`: `backend/tests/test_avisos_fila.py` (15), `test_avisos_telegram.py` (16, `httpx.MockTransport`: envio, 429, erro sem vazar o token, descoberta, script) e `test_avisos_servico.py` (11: dedupe, só o líder, desligado, segredo ausente, saúde, sem dado de persona, laço de ponta a ponta). Prova `real`: `not_run` (o dono cadastra o token e o chat_id).

## 2026-10-02 — Laço de pedidos (28.4, branch feat/28-4-laco)

- `backend/app/modules/pedidos/`: domínio puro (`materializar`, `gatilhos`, `sobreposicao`, `fechamento`) e aplicação
  (`repositorio`, `laco`, `acoes`). O laço materializa as ocorrências com janela de recuperação e coalescência, despacha
  uma execução por `RunService.create` com a chave `chave:t<n>` (procurada antes de criar) e fecha pela varredura.
  Só o líder age (trava nova `pedidos` em `travas.py`, escrita cercada).
- `RunService.create`/`Repository.create_run`: parâmetro interno `origem=(pedido_id, ocorrencia_id)` no mesmo `INSERT`;
  `RunCreate` segue recusando campo extra.
- Config: bloco `pedidos:` (`enabled: false` de fábrica, `tick_s`, `horizonte_s`, `prazo_inicio_s`, `janela_padrao_s`,
  `lote_max`, `posse_s`) em `config.py` e `config/config.example.yaml`. Desligado, o laço nem sobe. Sem migração.
- ADR-066; `docs/design/pedidos-laco.md` (decisões D1 a D7 fechadas e seção 10 com o que foi feito e onde) e §7.9 de
  `pedidos-persistentes.md` (editar = gatilho novo, versão trocada no lugar).
- Prova `simulated`: `test_pedidos_materializar.py`, `test_pedidos_sobreposicao.py`, `test_pedidos_fechamento.py`,
  `test_pedidos_origem.py`, `test_pedidos_laco.py` (A1, A2, A3, A5, A6, dois líderes, reinício). Real e PostgreSQL: `not_run`.

## 2026-10-02 — Aprendizado: modo por app para lições e telas (30.20, branch feat/30-20-modo-por-app)

- `aprendizado.licoes.por_app` e `aprendizado.telas.por_app` (padrão vazio = modo global; chave = pacote Android validado)
  e `domain/modo_por_app.modo_efetivo`, usado na coleta, na validação, no consumo e na publicação sozinha das telas.
  Nada liga `on` na instalação. Prova `simulated` (`test_learning_modo_por_app.py`, 16 testes + 2 `xfail` do D1 de um
  pacote mais permissivo que o global, que pede `servico.py`); ligar no central é `not_run`.

## 2026-10-02 — Aprendizado: backfill único e idempotente das lições anteriores à 055 (branch feat/aprendizado-backfill-licoes)

- `scripts/aprendizado-backfill-licoes.py` + `learning/infrastructure/backfill_licoes.py`: passa só `licoes.contraste` e
  `licoes.plano` pelas execuções reais anteriores ao `applied_at` da 055 (ou por `--run-id`), em `shadow`, sem IA e sem
  rede; ensaio numa cópia por padrão, `--aplicar` grava; aborta se a migração do banco difere da do código. Prova
  `simulated` (`test_aprendizado_backfill_licoes.py`, 11 testes); a rodada no banco do central é `not_run` até a
  coordenadora fazer backup e rodar.

## 2026-10-02 — Ações permitidas calculadas no backend (30.5, branch feat/30-5-acoes-no-backend)

- 30.5: a `Entrada` do livro traz `acoes` e `por_que_nao_publica`, calculadas em `domain/livro.py` sobre `ciclo.TRANSICOES`
  (adendo v0.46 do `api-contract.md`); o painel apaga o espelho manual (`model.ts`) e só traduz as chaves. Teste de paridade
  `test_learning_acoes.py` (`simulated`). Veto e modo do tipo no motivo: pendentes do serviço.

## 2026-10-02 — Aprendizado vivo: chave de app canônica e visão por app (30.1 e 30.2, branch feat/30-1-visao-por-app)

- Livro: fluxo e habilidade saem pelo PACOTE (tabela `apps` e registro de apps); o que não resolve cai no balde `nao_resolvido`, com o id cru em `app_ref`; a memória fica fora do eixo de app. Rotas novas, só leitura: `GET /api/aprendizado/apps` e `/apps/{pacote}` (registro ∪ loja ∪ Livro; declarado, aprendido, absorvido e camada de uso por tipo), sem tabela nova nem cópia de YAML (adendo v0.47). Prova `simulated` (`test_learning_apps.py`); `real` `not_run`.

## 2026-10-02 — Teste da fila de boot do worker espera o fato e não lê os processos do host (fix/teste-fila-de-boot)

- `test_worker_executor.py::test_a_espera_na_fila_de_boot_e_dita_em_progresso` falhava neste host: esperava `sleep(0.05)` pelo
  recado "fila de boot", mas antes dele cada `start` varre os processos REAIS do host (`pid_do_avd`: `psutil.process_iter` +
  `cmdline()`, ~60 ms sob pytest). Agora as esperas que significam "algo acontece" usam `_ate(...)` (o recado de fila, o primeiro
  emulador subir) e `_estado_falso` isola também `pid_do_avd` (`_sem_processos_reais`; `processos_reais=True` deixa a leitura
  real). Dois testes novos provam a causa (varredura lenta de 0,2 s sem isolamento; `process_iter` não é chamado com ele).
  Só teste e doc; `backend/app/` intacto. Aprendizado K-070.

## 2026-10-02 — Catálogo do Outlook na main e valor lido entre etapas (12.3, branch feat/12-3-outlook-catalogo)

- 12.3: catálogo só de leitura do Outlook na `main`, valor lido entre etapas. `backend/app/conhecimento/apps/com.microsoft.office.outlook/catalogo.yaml`
  (`OPEN_MAIL_INBOX`, `COLLECT_MAIL_HEADERS`, `SEARCH_MAIL`; nenhuma ação com efeito) e `Capability.saidas`
  (`planning/capabilities.py`): a ação declara os nomes que pode entregar (`remetente`, `assunto` na abertura da caixa e
  na busca); o planejador entre apps leva `saidas` na etapa de catálogo (`planning/parsing.py`, `prompts.py`) e o
  executor lê o valor como na etapa livre. ADR-065; sem migração. `CapabilityDefinition.output.values`.
- `simulated_provider.py`: o simulador cai na entrada do app quando o catálogo não tem as ações que ele conhece.
- Testes: `test_planejador_entre_apps.py` (catálogos do Outlook entram no pedido; C1 provado com o catálogo real),
  `test_porta_de_politica_por_app.py` (efeito no Outlook sem ação do catálogo é recusado), `test_catalogo_como_dado.py`,
  `test_outlook_declarado.py`. Prova: `simulated`; a leitura real no aparelho é `not_run`.

## 2026-10-02 — Aprendizado: atribuição de app por etapa travada em teste (branch fix/aprendizado-app-por-etapa)

- `backend/tests/test_aprendizado_app_por_etapa.py`: régua diária e relatório de falhas contam cada etapa de uma execução
  Instagram + Outlook no app dela e levam a tela da falha à chave; sem mudança de código (o `steps.app_id` NULL é o
  desenho). Prova `simulated`.

## 2026-10-02 — Fase 28: decisões do dono e do coordenador registradas (28.9, emenda à ADR-062, 28.11 Telegram)

- `docs/api-contract.md` (Adendo v0.45): as oito decisões em aberto do 28.9 viram decisões tomadas; o dono confirmou a emenda à ADR-062 e o piso de frequência (observar/preparar ≥ 15 min, agir ≥ 1 h). O ponto de extensão do 28.11 registra o canal escolhido: Telegram, com token e chat_id só pelo cofre/.env.
- `docs/decisoes.md`: emenda de 02/10 à ADR-062 (pedido em `aguardando_pessoa` é origem agrupada da caixa de Pendências; o aviso informativo não é pendência).

## 2026-10-02 — Teto de chamadas de IA proporcional ao `for_each` (17.12, branch jev/17-12-teto-for-each)

- `backend/app/taskqueue/executor.py::_ai` / `_teto_de_chamadas` e `backend/app/taskqueue/foreach.py::teto_de_chamadas`: o teto de chamadas por objetivo passa a ser `min(ai_max_calls_absolute, ai_max_calls_per_objective + ai_max_calls_per_item × (itens − 1))`; sem `for_each` (ou com 1 item) continua exatamente `ai_max_calls_per_objective`. Os itens saem das etapas já gravadas (`item_index` em `steps.variables`, todas as versões do plano), só consultadas quando o base já foi atingido. O rejulgamento do 17.10 continua contando; `ai_max_usd_per_run`/`per_day` e o teto de tokens ficam como estavam. A mensagem diz o teto efetivo e a origem (`60 + 12 × 7 itens do for_each`).
- Config: campos novos `ai_max_calls_per_item` (padrão 12 = ~8 chamadas medidas por envio na `r-20261002181642-eff15b`, com folga; 0 desliga) e `ai_max_calls_absolute` (padrão 300; só limita o crescimento, nunca baixa o base); expostos em Configuração › Orçamento de IA, `config.example.yaml` e `docs/api-contract.md`. Sem migração (os limites vivem em `settings`).
- Prova `simulated`: `backend/tests/test_teto_proporcional_for_each.py` (8 testes: fórmula, 5 contatos que estouram com o teto fixo e fecham com o proporcional, sem lista = 60, absoluto corta, rejulgamento conta, US$ intacto); frontend `validation.test.ts`. Prova `real`: `not_run` (reexecutar `msg-todos-os-contatos` depois do deploy exige autorização de gasto).

## 2026-10-02 — 29.18: fechamento da Fase 29

- `docs/relatorio-validacao.md` §28 (novo): os 20 itens 29.1 a 29.20, cada um com estado e nível de prova (`real` com data, máquina, commit e ids; `simulated` com `arquivo::teste`; `not_run`) e, para o que não fechou, o que ficou pronto e a ação exata e quem decide. Real sem ressalva: 29.1, 29.4, 29.10, 29.11, 29.14, 29.16, 29.17; real com resto: 29.9, 29.12, 29.13; só simulado: 29.2, 29.3, 29.6, 29.20; `not_run` adiados pelo dono: 29.7 e 29.19.
- A fase **não fecha ainda**, por uma cláusula: "CI verde no commit publicado". O cron de 02/10 05:28Z (run 36969076830, `52237c4`) passou em tudo menos no job de documentação (link para o handoff local e aviso de vocabulário), já corrigidos na integração (`3a48efdc`, `c1109079`); nenhum run de CI os cobriu e o commit implantado (`f9eed71`) não tem run (processo `[skip ci]`). Ação: `workflow_dispatch` na `main` depois do merge, ou o cron de 03/10 05:17Z. As outras três cláusulas estão cumpridas (6 h do 29.4; Outlook no login, 29.10 e 29.13; pendências com ação exata).
- Estado pelo mecanismo (`aplicar`): 29.18 passa a `implemented`, 29.20 é registrado (`implemented`, `simulated`) e o 29.9 ganha o bloqueio reescrito com o W8 `PASS` de 02/10 (política `livre`) e o que falta (W8 com bloqueio). `docs/estado-atual.md` e a linha da Fase 29 em `docs/roadmap.md` atualizados.
- Documentação e processo: só documentação e registro do plano; nenhum código, teste de produto ou ação em aparelho.

## 2026-10-02 — 29.20: nenhum aparelho pela saída da casa (medida do central e `egress_home`)

- `backend/app/devices/rede_saida_central.py` (novo): mede a saída do próprio central (mesmos ecos da sonda, família forçada por socket, em segundo plano, cache com TTL `rede.sonda.central_ttl_s`, sem bloquear a API) e dá o veredito por aparelho (`mesma_saida`, `perfil_leva_ipv6`, `veredito`). `sonda_rede.ip_da_resposta_http` fatorada de `ler_ip_de_saida`.
- `GET /api/network/devices`: `central_egress` no topo e `egress_home` por aparelho (IPv4 igual, IPv6 no mesmo /64, IPv6 fora do perfil, resumo `leaves_by_home`); sem medida = `null`, nunca "limpo". Config nova com padrão: `rede.sonda.medir_central`, `central_ttl_s`, `central_prazo_s`. Sem migração.
- Painel Rede: selo "sai pela casa" por aparelho e resumo "N aparelhos ainda saem pela casa · K presumidos · M sem medida" com a saída do central.
- Aparelho SEM rede pedida: `egress_home.basis: "presumed"` (sai pela casa, presumido, estado próprio) até a sonda de IP medir (`rede_medicao.medir_saida`, só IPv4/IPv6 pelo uid 2000, na varredura, ligado e livre, a cada `reverificar_s`; `rede.sonda.medir_sem_rede`). Medido igual ao central: `measured`; medido diferente: não casa, com o IP; falha de sonda: segue presumido. Grava em `network_measurements` (método próprio), só leitura, sem migração.
- Prova `simulated`: `tests/test_rede_saida_central.py`, `tests/test_rede_por_aparelho.py::test_saida_da_casa_acusa_por_aparelho_e_nunca_limpa_sem_medida`, `RedePage.test.tsx`. Medir o central real e conferir o android-09 com `vpn-central-wireguard`: `not_run`.
- Docs: `docs/dominios/parque.md` ("Nenhum aparelho pela saída da casa"), `docs/api-contract.md`.

## 2026-10-02 — 8.3 Sinais e limites: o que já estava feito, o que faltava

- Simulado (`tests/test_capabilities.py::test_abandonar_o_item_expira_a_aprovacao_pendente`,
  `tests/test_detector_conta_travada.py`, `tests/test_sensitive_input.py`): conferido contra o código de hoje, o
  grosso do 8.3 já estava na `main` (f5015a6 e ADR-055): "confirm you're human" no classificador, teto de
  reobservação `unknown` (`session_unknown_retry_cap`), teto por dia por balde, uma conta por alvo com espaçamento e
  expiração da aprovação ao cancelar. Faltava: `resolve(abandon)` e o cancelamento antes de iniciar não expiravam o
  pedido pendente (agora expiram); o classificador aceita "verify/prove you're human", "comprove/confirmar que você
  é humano/uma pessoa (real)". Hierarquia SINTÉTICA — a tela real não existe, a conta foi perdida.
- `not_run`: `REPLY_COMMENT` e "editar" em aparelho real (conta real de terceiro; roteiro em
  [`perfis-e-instagram.md`](docs/dominios/perfis-e-instagram.md)). Pendente do dono: afrouxar a política do catálogo
  (#114 item 3), troca de conta (#115: a flag já não existe; implementar ou assumir que conta errada é sempre pessoa).

## 2026-10-02 — 7.4: linha de base real da configuração implantada, 13/14 por US$ 1,45; achado do teto de chamadas no `for_each`

- Real (02/10, central, deploy `25624c4`, android-05 sem conta real): 14 casos do QA Messenger, 13 corretos, US$ 1,449; `msg-todos-os-contatos` bateu no teto de 60 chamadas por objetivo no 7º de 8 contatos (`r-20261002181642-eff15b`). HTTP 500 do verificador: 2/423. Rejulgamento das 56 não repetido (vale o de 25/09). `docs/ia.md` §8; estado do 7.4 pelo mecanismo (`implemented`, `real`).

## 2026-10-02 — Flex para trabalho offline (17.8, branch jev/17-8-flex)

- `backend/app/planning/openai_provider.py`: o `OpenAICompatProvider` passa a honrar `ai.roles.<papel>.max_retries`
  (antes só o provedor da Anthropic o usava): repete 429 (menos `insufficient_quota`) e 5xx, com `Retry-After` ou 5 s
  dobrando até 60 s; padrão 0, então nada muda no caminho interativo. É o que faz o `service_tier: flex` (429
  "Resource Unavailable" esperado) funcionar como entrada de provedor com `extra_body`.
- `config/config.example.yaml` e `docs/ia.md` §13: bloco comentado `openai-flex` e o desenho. `timeout_s` já não tinha
  teto de validação. O rejulgamento offline (`scripts/eval_rejudge.py --sobrepor`) já aceitava a entrada, sem mudar o
  script.
- **Não feito, por decisão pendente:** a geração de persona fica de fora. `generate_persona` e
  `generate_social_response` usam o mesmo papel `social`, e o segundo roda dentro da execução; separar exige um papel
  novo (`persona`), que não foi criado.
- Prova `simulated`: `backend/tests/test_openai_provider.py::test_flex_*` e afins (25 passam) e
  `scripts/tests/test_eval_rejudge.py::test_sobreposicao_flex_*` (9 passam), transporte e `sleep` falsos. Chamada real
  ao flex: `not_run`.

## 2026-10-02 — T.2: o tempo da suíte deixa de ser esperado (relógio virtual e orçamento do verificador configurável, branch jev/t2-relogio)

- Causa medida (`--durations=15`, 02/10): `test_resultado_ambiguo_vira_incerto_e_nao_reenvia` pagava 63,6 s porque `_verify` tinha o orçamento (8/15/60 s) em literais e, com o efeito disparado, sondava 60 s reais até "incerto"; `test_rotation.py` e as execuções pagavam ~2,9 s de assentamento por passo de mensagem (`asyncio.sleep` fixo nas ferramentas: 0,4 + 0,5 + `wait_for` 2 s) e o `wait_for` de 4 s do verificador simulado, 3 voltas, onde a mensagem nunca aparece.
- Código: `ai.verify_budget_s`/`verify_budget_patient_s`/`verify_budget_min_s` (`backend/app/config.py`, padrões 15/60/8 = o comportamento de antes), lidos em `StepExecutor._verify`; `ToolContext.dormir` (padrão `asyncio.sleep`) em todas as esperas de assentamento de `backend/app/automation/tools.py` (exceto o recuo de foco do `open_app`) e `StepExecutor.dormir`, injetado no contexto. Produção não muda.
- Testes: `tests/relogio_virtual.py` (`RelogioVirtual`: `dormir` avança o deslocamento em vez de bloquear), `FakeQaDevice.relogio` (a mensagem envelhece pelo mesmo relógio), `Harness.pular_o_tempo()` e `Harness.encurtar_verificacao()` (opt-in por teste, sobrevivem a `crash()`/`boot()`). A prova é a mesma: o `wait_for` esperou os mesmos segundos, só que virtuais, e o aparelho envelheceu a mensagem o mesmo tanto; "incerto" continua exigindo a tela sem a mensagem até o fim do orçamento. `test_nao_desliga_aparelho_em_foco...` vence `focus_until_mono` em vez de esperar o TTL.
- Antes → depois (simulated, `pytest tests/test_execution.py tests/test_rotation.py`, 22 testes, mesma máquina): 158,3 s → 56,3 s. Por teste: `resultado_ambiguo` 63,6 → 2,1 s; `rotation::hibernacao` 20,0 → 2,6 s; `reinicio_com_acao_de_efeito_pendente` 12,5 → <1 s; `rotation::tres_contas` 10,0 → 1,3 s; `falha_e_tela_inesperada` 6,6 → 2,6 s; `rotation::nao_desliga...foco` 5,8 → 0,5 s; `test_observacao_arvore_primeiro::falha_grava_evidencia_com_imagem_tardia` 61 → 1,7 s. Ficaram em tempo real de propósito: `test_timeout_no_toque...` (8,3 s, `hang_s` e `driver_call_timeout_s` reais) e os testes de corrida com `action_delay_s` (pausa, controle manual, queda do backend).
- Prova (simulated): 194 passed nos arquivos direcionados (test_execution, test_rotation, test_arquitetura, test_hub_de_ia, test_anr_sinal_proprio, test_digitacao_atomica, test_plan_defect, test_cost_levers, test_credenciais_da_conta, test_observacao_arvore_primeiro, test_wait_boot_sondas, test_ciclo_de_vida_do_emulador). Suíte inteira `not_run`. Real: nada (só aparelho falso).
- Veredito do snapshot (`DeviceManager._snapshot_verdict`): `backend/tests/test_snapshot_verdict.py`, 6 passed (simulated, só arquivo de log em `tmp_path`): `True` ("Successfully loaded"), `False` nas duas frases de recusa ("cannot load snapshot", "Failed to load snapshot"), `None` com log sem veredito ou vazio, `None` com o "carregado" ANTES do `boot_log_offset` (e o contrário: recusa velha não condena o boot novo; com offset 0 ela vale) e `None` com o arquivo ausente. O teste do ciclo de vida deixou de listá-lo como lacuna.
- Levantamento (só leitura, texto, `backend/app/api.py`): 175 rotas (`router` e `worker_router`); 174 têm chamada `client.<método>("<caminho>")` em `backend/tests/`; a que não tem é `POST /api/instagram/profiles/{profile_id}/accounts/{account_id}/session/connect`, coberta pelo apelido `POST /api/instagram/profiles/{id}/connect` que delega a ela. A frase "38 das 85 rotas sem teste HTTP" do plano está defasada. Limite: confere o caminho e o método na chamada, não o que cada teste afirma, e não cobre os routers de `app/modules/*/presentation`.
- Fora do T.2 desta rodada: eventos `command.updated`/`worker.updated` e a tela de Infraestrutura (frontend); ligar `pular_o_tempo` no harness inteiro (exige a suíte completa).

## 2026-10-02 — T.2: testes dos eventos `command.updated`/`worker.updated`, da tela de Infraestrutura e da última rota sem teste HTTP (T.2, branch `jev/t2-eventos-infra`)

- `backend/tests/test_eventos_comando_e_worker.py` (14): payload (`data.command`, `data.worker`, `instance_id` no envelope), nível por estado (falha/recusa `error`, incerto `warn`, worker offline `warn`), mensagem com e sem motivo, aviso de comando em voo na reconexão (`inflight`), não efêmero, a trilha de um comando remoto pelo caminho real (despachado, ack, running, progresso, concluído) e o worker (canal sobe/cai, sem batida, manutenção, batida igual só gera `worker.metrics`, inventário novo gera `worker.updated`, rotação de credencial).
- `frontend/src/features/infra/InfraPage.test.tsx` (+5, 39 no arquivo): um cartão por servidor; `worker.updated` troca o estado e o detalhe sem recarregar e traz servidor novo; `worker.metrics` apaga o aviso de dado velho; `command.updated` entra na aba Registros só do servidor que hospeda o aparelho.
- `backend/tests/test_sessao_conectar_conta.py` (3): `POST /api/instagram/profiles/{id}/accounts/{conta}/session/connect`, a 175ª rota de `api.py` com chamada HTTP em teste: recusas na ordem (404, `no_binding`, `sem_vinculo`, `no_credential`, `consentimento_de_credencial`) e o 202 com `command_id` até o provedor de sessão (espião) e o comando `succeeded`.
- `docs/plano-100.md` (T.2): a contagem de rotas sem teste HTTP passa a ser a medida. O relógio virtual no harness inteiro continua fora (exige suíte completa).
- Prova `simulated` (arquivo::teste acima, sem emulador, rede nem IA); `real`: `not_run`.

## 2026-10-02 — Recorrência e fuso do pedido persistente (28.3, branch feat/28-3-recorrencia)

Módulo puro `backend/app/modules/pedidos/domain/recorrencia.py`: parser do subconjunto da RRULE (HOURLY/DAILY/WEEKLY/MONTHLY, INTERVAL, BYDAY, BYMONTHDAY, BYHOUR, BYMINUTE, COUNT/UNTIL, com recusa clara do resto), geração em hora local ingênua localizada com `zoneinfo`, prévia das próximas datas com relógio injetado, hora inexistente desviada para o primeiro instante válido depois do salto e hora repetida em `fold=0`. `tzdata==2026.4` declarado em `requirements.in` (já estava no lock, indireto pelo psycopg). Prova `simulated`: `tests/test_pedidos_recorrencia.py` (`America/Sao_Paulo` 2018/2019, `America/New_York`, `Europe/Lisbon`, `Pacific/Apia`). Decisão de gerador próprio registrada em `docs/design/pedidos-persistentes.md` §7.7. Sem migração, sem rota.

## 2026-10-02 — Modelo do pedido persistente: tabelas, colunas em `runs` e domínio puro de estados (28.2, branch `feat/28-2-modelo-pedido`)

- Migração `067_pedidos.sql`: `pedidos`, `pedido_gatilhos`, `pedido_ocorrencias` (`chave` UNIQUE + trio UNIQUE) e `runs.pedido_id`, `ocorrencia_id`, `prioridade` (padrão 0). Sem FK entre `runs` e a ocorrência (a purga de uma não leva a outra); só colunas novas e vazias, nenhuma linha de `runs` é tocada. O plano dizia 060: a main já passou dela, e a 065/066 são da trava (28.1).
- `backend/app/modules/pedidos/domain/`: `estados.py` (tabelas de transição do pedido, com ator `pessoa`/`sistema`, e da ocorrência; `transicionar_*` recusa aresta fora da tabela, ator errado e motivo faltante) e `chave.py` (`ped:<pedido>:<gatilho>:<instante UTC no segundo>`; gesto manual/backfill leva a origem; `chave:t<n>` como `idempotency_key`; ids até 28 caracteres para caber nos 100 de `RunCreate`).
- Prova `simulated`: `backend/tests/test_pedidos_modelo.py` (produto cartesiano das duas máquinas, chave, migração sobre banco existente, CHECK igual ao vocabulário do domínio), em SQLite; PostgreSQL `not_run` (sem `TEST_DATABASE_URL`). Fora do escopo: laço (28.4), API/tela (28.9), recorrência (28.3).

## 2026-10-02 — Trava de líder dos laços periódicos (28.1, branch feat/28-1-trava)

- Migração `066_travas.sql` e `app/taskqueue/travas.py` (`Lideranca`): tomada por CAS no relógio do banco, prazo de 120 s, renovação a cada 20 s num laço próprio do `AppState` (`travas-de-lider`), token de cerca crescente, tomada idempotente, dono = `OWNER_ID`, devolução na saída limpa e faxina de partida para o mesmo dono.
- `state.py`: saldos, curadoria e retenção rodam só no líder; os outros backends pulam a volta sem erro. O fechamento do dia (não idempotente) roda cercado pelo token (`TravaPerdida` recusa o líder que perdeu o mandato).
- Prova `simulated`: `backend/tests/test_travas.py` (7: dois bancos, expiração, renovação, token antigo recusado, corrida de 8 conexões, dois `AppState` no mesmo banco). PostgreSQL pulado (sem `TEST_DATABASE_URL`); segundo backend real no central `not_run`. Limite conhecido: `saldos.CONCILIACOES` é memória do processo, e o seguidor com chave de administrador mostra a conciliação velha na saúde.
- Decisão registrada no [ADR-064](docs/decisoes.md) (aceito; número confirmado pelo orquestrador): CAS no relógio do banco, cerca por token, renovação no `AppState` (desvio consciente do §7.3), dono = `OWNER_ID` com retomada imediata, e os limites aceitos.

## 2026-10-02 — 17.10 real parcial: rejulgamento do "sim" em efeito externo provado; a cascata do bloqueio não foi acionada; `eval_run.py` no console cp1252

- Real (02/10 17:49–17:54Z, central, deploy `25624c4`, android-05 sem conta real, US$ 0,2955): regra 2 provada em `r-20261002175209-1a252b`; regra 1 `not_run` (`r-20261002175004-ea377e`: o tier 0 não bloqueou; o tier 1 bloqueou pela nova tentativa); negativo `r-20261002175257-0e9362` barrado pelo detector de tela sensível. Detalhe em `docs/ia.md` §10b.
- `scripts/eval_run.py`: `saida_segura()` — o "≈" do aviso derrubava a bateria no console cp1252 antes do primeiro POST; `scripts/tests/test_eval_run.py` (12).

## 2026-10-02 — Perfil de IA por execução e canário (17.7): A/B sem reiniciar o central (branch `jev/17-7-perfil-ia`)

- `config.py`: `ai.profiles.<nome>.roles` (por cima de `ai.roles`, campo a campo) e `ai.canary: {profile, fraction}`, conferidos na partida como `ai.roles`. `planning/routing.py`: os perfis vivem no MESMO hub (teto em US$, vagas por função e instâncias compartilhados); o perfil sai de `runs.ai_profile` pelo `run_id` de cada chamada; perfil que sumiu da configuração falha com `not_configured` em vez de cair no padrão; perfil que manda dados para fora entra no aviso do `/api/ai`. `RunCreate.ai_profile` (desconhecido = `422 ai_profile_desconhecido`), sorteio injetável do canário em `RunService`, `RunSummary.ai_profile`/`ai_profile_source`. Migração `064_perfil_de_ia`. `scripts/eval_run.py --profile`.
- Prova `simulated`: `backend/tests/test_perfil_de_ia.py` (16), `scripts/tests/test_eval_run.py` (11). `real`: `not_run`.
- Docs: `docs/ia.md` §10c, `docs/banco.md` (064), `docs/api-contract.md` (`POST /api/runs`), `config/config.example.yaml`.

## 2026-10-02 — Suíte em paralelo com pytest-xdist: `pytest -q -n 8` em ~5 min contra ~36 min em série (J-XDIST, PR #48)

- `backend/requirements-dev.in`/`.txt`: `pytest-xdist==3.8.0`. `test_worker_executor.py::test_guarda_de_ram_e_reavaliada_depois_da_espera_na_fila` espera pelo fato em vez de `sleep(0.05)` (falhava isolada e com `-n 12`).
- Real (02/10, WIN-7S2UASNLFOP): em série `25624c4` 4754 passed/7 skipped em 2178 s (sessão Android); `-n 8` em `7a1d0b0` 4752 passed/9 skipped em 306,87 s e 347,57 s, sem falha. Os 2 pulos a mais são de `test_supervisao_do_central.py` (exigem `backend/.venv` na árvore; pulam também em série no worktree sem a junção). PostgreSQL com `-n`: `not_run`.
- Docs: `docs/operacao.md` §4, `.claude/rules/testes.md`.

## 2026-10-02 — A8/A9/A10 executados em real: túnel em PowerShell 5.1, relógio do notebook e agente `f9eed71`

- Real (02/10, central + `worker-lan-01`, deploy `f9eed71`, backup `20261002-132234`/`132239`, com a pausa de reparo do android-09 conferida no health): tarefa `farm-tunel-192.168.1.11` reinstalada com `powershell.exe` 5.1 (forwards 15555..15565 e reverso `18000 → 8010`, worker `up`, hierarquia do android-09 200); agente do notebook `0.1.0+5d8b545` → `0.1.0+f9eed71`; relógio do notebook +8,857 s → +0,002 s pela tarefa `farm-relogio` (`C:\farm\relogio`), 1ª execução agendada com resultado 0, e o `degraded` por relógio saiu sozinho.
- `docs/worker.md`: seção "O relógio do worker e as tarefas do notebook".

## 2026-10-02 — W8: rodada r4 real: `PASS` (a mitigação do PR #17 religou o túnel em 2 de 2 boots válidos)

- `docs/handoffs/w8-boot-recovery.md` §17.10 (real, `bcea158`): o `tun0` não subiu sozinho e o Start pela interface o religou aos 193 s, sem reinício extra; vizinhos 01/03/06 com reparo pausado, sem ciclo de vida alheio; rollback com `force-stop` do SFA fez 1 reinício; servidor WireGuard reiniciado 2x (03/06 reconectaram em 30 s). Veredito pelo critério pré-registrado (§17.9): `PASS` (a recuperação válida do r3 + a do r4). Causa raiz segue `NARROWED`. `docs/estado-atual.md` atualizado.

## 2026-10-02 — W8: pré-registro do r4 (1 iteração, critério PASS/PARTIAL/FAIL, vizinhos com reparo pausado) antes do boot

- `scripts/diag-w8-mitigacao.py`: seed `w8-mitigacao-20261002-r4`, `BOOTS_JA_USADOS = 3` (exatamente 1 iteração), critério do r4 (PASS com a `RECOVERED_BY_UI` do r3 + uma; PARTIAL com `NO_FAILURE`/`BOOT_INVALID`/`UNKNOWN`; FAIL com `NOT_RECOVERED`/`RECOVERED_BY_RESTART`) e pausa do reparo automático de android-01/03/06 (TTL 900 s, conferida no health, encerrada no fim; a regra de bystander não muda). `docs/handoffs/w8-boot-recovery.md` §17.9. Prova `simulated` (76 testes); nada executado (`not_run`).

## 2026-10-02 — Rede por aparelho: o desfazer espera o `stopped` persistir e para o cliente que religou sozinho (A11, achado do W8 r2)

- `devices/rede_aplicacao.py`: `desfazer` espera ~10 s depois do `am force-stop` do cliente VPN e lê `stopped=` antes do reinício (`PARADA_PERSISTIR_S`); `Observacao.cliente_solto()`. `devices/rede_convergencia.py`: com a rede tirada, o boot passado e o `tun0` ainda no ar (o cliente religou sozinho), a convergência faz `force-stop` do cliente e apaga a linha em vez de pedir outro reinício; se o túnel não cai, o reinício até o teto continua. Prova `simulated` (`backend/tests/test_rede_aplicacao.py`, 3 testes novos, um deles falha sem a correção; 186 testes de rede e arquitetura passam); a prova real é o rollback do r3 (1 reinício, SFA não religou, com o `force-stop` do ator). Não implantado ainda.

## 2026-10-02 — A9: o desvio de relógio do worker é re-medido a cada batida (branch `feat/a8-a9-codigo`)

- **Defeito.** O agente media o desvio UMA vez por conexão (`Welcome.server_time`) e a batida repetia o número, então o
  `degraded` "relógio desalinhado" refletia a fotografia da conexão e só mudava reconectando.
- **Mudança mínima compatível.** `Heartbeat.sent_at` (opcional, relógio local do agente na saída); o central calcula
  `relógio do banco na chegada − sent_at` (`WorkerRegistry.desvio_de_relogio`, latência de ida inclusa) e a regra de saúde
  segue a mesma (limite 5 s, mesma mensagem; o `degraded` por relógio some sozinho quando o desvio volta ao limite).
  Agente antigo (sem `sent_at`) cai no `clock_offset_s` da conexão; central antigo ignora o campo. Sem versão nem feature.
  Esquema do fio recongelado em `test_contratos_do_worker.py` (`heartbeat`, aditivo).
- Prova: `simulated` (`backend/tests/test_workers.py` 4 testes novos, `test_worker_agent.py::test_toda_batida_leva_o_relogio_local…`,
  contrato). `not_run`: agente e central reais em máquinas com relógios diferentes. Docs: `docs/worker.md`,
  `docs/api-contract.md`, `docs/parque-distribuido.md`.

## 2026-10-02 — A8: o instalador do túnel nunca registra o pwsh da Microsoft Store (branch `feat/a8-a9-codigo`)

- **Achado real.** No central a tarefa `farm-tunel-192.168.1.11` foi instalada com `-AceitarStore` e executava
  `C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.6.0_x64__8wekyb3d8bbwe\pwsh.exe`, caminho com a versão do
  MSIX que some quando a Store atualiza: no boot seguinte o túnel não sobe e o worker (e o android-09) cai.
- **Código** (`scripts/worker-tunnel.ps1`): `Resolve-Pwsh` virou `Resolve-Interpretador` (MSI estável → outro pwsh fora
  do WindowsApps → Windows PowerShell 5.1 do sistema → falha clara); trava final antes do `New-ScheduledTaskAction`;
  `-AceitarStore` agora é erro explicado; BOM no arquivo para o 5.1 ler UTF-8. Arquitetura do túnel intacta
  (`-R 18000:8010`, nunca 8000; forwards ADB inalterados).
- Prova: `simulated` (`backend/tests/test_tunel_restrito.py`, 23 testes; escolha da função extraída do script com caminhos
  falsos em pwsh 7 e no 5.1, parser do 5.1, `-Instalar -Simular` no 5.1). `not_run`: reinstalar a tarefa real no central
  (exige autorização: mexe no túnel). Docs: `docs/worker.md`.

## 2026-10-02 — W8: rodada r3 real: o Start pela interface recuperou o túnel sem reinício extra (PARTIAL; veredito formal FAIL por reinício de saúde do android-01)

- `docs/handoffs/w8-boot-recovery.md` §17.8 (real, `bcea158`): iteração 1 `RECOVERED_BY_UI` (o `tun0` não subiu sozinho; UM Start pela interface aos 198 s religou, sem reinício extra); iteração 2 invalidada pela regra (`restart` automático por saúde do android-01, interrupções acumuladas), com dados concordantes mas inadmissíveis; veredito formal `FAIL`, desfecho substantivo `PARTIAL` (1 válido; PASS exige 2); rollback com `force-stop` do SFA fez UM só reinício (no r2 foram 2); servidor WireGuard reiniciado 2x, 03/06 reconectaram em 20 a 30 s. 3 de 6 boots usados.

## 2026-10-02 — W8: protocolo r3 (baseline por iteração, force-stop do SFA no rollback, seed nova) antes do 1º boot do r3

- `scripts/diag-w8-mitigacao.py`: seed `w8-mitigacao-20261002-r3`; a 1ª iteração exige o estado limpo e as seguintes esperam o aparelho assentar e exigem a linha e o par da política (o oposto do baseline do estágio 1, que invalidou o r2); `BOOTS_JA_USADOS = 1` com a regra `usados + 1 + reinicios_max <= 6`; o rollback faz `force-stop` do SFA no 09 antes do `assign vpn=null`. `docs/handoffs/w8-boot-recovery.md` §17.6 reclassifica o r2 (`INCONCLUSIVE_HARNESS_DEFECT`) e §17.7 registra o desvio. Prova `simulated` (72 testes); nada executado (`not_run`).

## 2026-10-02 — W8: 1º disparo real da validação da mitigação (real, `bcea158`): NO_FAILURE no boot 1, FAIL por defeito do executor, rollback incompleto no produto

- `docs/handoffs/w8-boot-recovery.md` §17.6: deploy `bcea158`; política `livre` + `vpn-central-wireguard` só no android-09; 2 reinícios do servidor WireGuard em janela ociosa (03/06 reconectaram em 20 s cada); **iteração 1 `NO_FAILURE`** (mitigação NÃO exercitada); iteração 2 `BOOT_INVALID` por defeito do executor (baseline do estágio 1 reaproveitado) → veredito pré-comprometido `FAIL`, desfecho substantivo `INCONCLUSIVE`; 1 de 6 boots usados. Achado de produto: o rollback da rede não para o SFA, que religa sozinho no boot (`tun0` para um par removido) e a convergência pede mais um reinício; baseline restaurado à mão (`force-stop` do SFA no 09).

## 2026-10-02 — W8: o executor da validação real não dobra o prefixo /api (achado ANTES do 1º boot)

- `scripts/diag-w8-mitigacao.py`: `Api` montava `/api/api/...` (a base já termina em `/api`); o firewall-check do 1º disparo real (02/10 15:25Z) devolveu 405 e o protocolo parou em `INCONCLUSIVE` "BLOQUEADO" sem escrever nada, nenhum boot. Corrigido com teste (`UrlsDoApi`) e saída em UTF-8 (o plano com `≤` quebrava no console cp1252). O protocolo pré-comprometido não muda; nada tinha rodado.

## 2026-10-02 — pausa do reparo automático por aparelho (W8, quase-acidente do §17.4) (branch `feat/pausa-de-reparo`)

- **Plataforma.** `PUT`/`DELETE /api/instances/{id}/repair-pause`: o central deixa de emitir `restart`/`reset` AUTOMÁTICOS
  (escada de reparo e reinício por saúde) para UM aparelho marcado em experimento ou manutenção. Desligada por padrão,
  prazo (`ttl_s`, 60 s a 3 h) obrigatório, expira sozinha, aparece em `instances[].repair_pause` e em
  `GET /api/health` → `features.repair_pause` (informativo, não é problema). Comando de pessoa, o `restart` da rede e os
  outros aparelhos passam; não mexe na manutenção do worker.
- **Por quê.** No estágio 1 do W8 a escada pediu um `restart` do android-09 15 s depois do restart do experimento; só a
  rejeição `device_busy` (acidental) o barrou (`docs/handoffs/w8-boot-recovery.md` §17.4).
- Prova: `simulated` (`backend/tests/test_pausa_de_reparo.py`, 8 testes). Docs: `docs/dominios/parque.md`, `docs/api-contract.md`.

## 2026-10-01 — a rede religa o túnel pelo Start da interface do cliente, não pelo tile (W8) (branch `fix/w8-sfa-service-mode`)

- **Correção (não implantada).** O tile do SFA 1.14.2 não recalcula o `serviceMode`: num cliente que só importou o perfil
  (o android-09) iniciava o `ProxyService`, que aborta sem `tun0`. A convergência passa a religar pelo Start da interface
  (`rede_aplicacao.religar_pela_interface`: abre a atividade, acha o `Start` pela árvore, UM toque, sucesso só com `tun0` e
  VPN CONNECTED), com o guard `wrong_service_class_for_tun` e sem fallback para o tile. `rede.cliente_atividade` novo;
  `rede.cliente_tile` vira legado. Prova `simulated`; a real do princípio é de 01/10 (android-09, UM Start da UI).
  W8 segue aberto: os boots 1/3/4 sem túnel por always-on não são explicados. Detalhe: `docs/handoffs/w8-diagnostico-android09.md` §19.
- **Hardening (mesma branch).** O rótulo do `Start` vem do locale do aparelho e da tabela do SFA 1.14.2 (`ROTULOS_DO_CLIENTE`:
  en/fa/ru/zh-CN/zh-TW; outro idioma cai no inglês; rótulo desconhecido ou de outro pacote = nenhum toque). A classe de serviço
  passa a ser lida só na janela do Start (baseline de hora do aparelho); sem prova, `UNKNOWN`. §20 do mesmo handoff.

## 2026-09-28 — o backend troca o Appium órfão sem prova de mascaramento (K-039 fora do deploy) (branch `claude/nifty-feynman-uflykh`)

- **Operação.** Quando o backend morria sozinho (crash, Windows Update), o supervisor o religava, mas o Appium que
  ele tinha subido ficava na porta, sem pai para `_matar_filhos` varrer. O backend seguinte o readotava `degraded`
  (`appium_log_masking_off`, credencial bloqueada). Agora o `AppiumServer.start` troca esse órfão quando ele é desta
  árvore (PID de `data/appium.pid` vivo, linha de comando em `<appium.dir>/node_modules/appium`) e o mascaramento não
  se comprova: encerra-o com os filhos, menos os emuladores, e sobe outro com as regras.
  - Com prova, o órfão continua readotado.
  - Servidor de fora do projeto na porta continua reutilizado e nunca é encerrado.
  - Sem Appium instalado para subir outro, ou sem permissão, o órfão fica readotado, com o motivo no detalhe.
- **Decisão.** A troca fica no backend, e não no supervisor, porque cobre todo caminho até a subida. E é segura por
  construção: a porta da Farm é ligada antes do lifespan, então nenhum outro backend desta árvore está vivo.
- Inclui por merge o branch do PR #15 (`stop.ps1`, na entrada abaixo).
- Prova: `simulated`.
  - `backend/tests/test_supervisao_do_central.py`: 4 testes novos com `node` de verdade. O backend morto e religado
    pelo supervisor troca o órfão sem prova; o órfão com prova é readotado; o `node` de outra árvore fica; o filho
    `emulator` fica vivo e o `adb` é encerrado.
  - `test_saude_do_appium.py`: 4 testes novos das travas (sem Appium instalado, PID reciclado, emulador e nome
    ilegível poupados, sem permissão).
  - Tirar cada trava faz um teste falhar (checagem de mutação, 5 de 5).
  - `not_run`: o central.

## 2026-09-28 — `stop.ps1` encerra o Appium órfão deste projeto (K-039) (branch `claude/zen-ptolemy-achwl2`)

- **Operação.** Três deploys seguidos (27 e 28/09) subiram `degraded`, com `appium_log_masking_off` ou
  `appium_down` "readotado", porque o `node` do Appium do backend anterior ficava na porta. O `stop.ps1`, e com ele o
  `deploy.ps1`, agora encerra esse Appium depois que a Farm para de responder. Só o `node.exe` na porta de `appium:`
  do config cuja linha de comando aponta para `tools\appium` desta árvore; outro processo na porta fica, com aviso.
  Há uma carência de 10 s para o backend que ainda está saindo, e a porta é conferida depois do encerramento.
  `stop.ps1 -Simular` mostra o que seria encerrado. A lógica fica em `scripts/lib/appium-do-projeto.ps1`.
- Prova: `simulated`, em `scripts/tests/test_stop_appium_orfao.py` (23 passaram e 1 pulou, no Linux com pwsh 7.4 e
  node 22). Cobre a seleção, a leitura do config, um `node` de verdade encerrado com o de outra árvore poupado, a
  carência e `stop.ps1 -Simular`. `scripts/tests` inteiro deu 173/173 mais 1 pulado, em Python 3.13. `not_run`: o
  teste com `Get-NetTCPConnection` de verdade (só Windows) e o deploy no central.


## 2026-09-28 — `stop.ps1` encerra o Appium órfão deste projeto (K-039) (branch `claude/zen-ptolemy-achwl2`)

- **Operação.** Três deploys seguidos (27 e 28/09) subiram `degraded`, com `appium_log_masking_off` ou
  `appium_down` "readotado", porque o `node` do Appium do backend anterior ficava na porta. O `stop.ps1`, e com ele o
  `deploy.ps1`, agora encerra esse Appium depois que a Farm para de responder. Só o `node.exe` na porta de `appium:`
  do config cuja linha de comando aponta para `tools\appium` desta árvore; outro processo na porta fica, com aviso.
  Há uma carência de 10 s para o backend que ainda está saindo, e a porta é conferida depois do encerramento.
  `stop.ps1 -Simular` mostra o que seria encerrado. A lógica fica em `scripts/lib/appium-do-projeto.ps1`.
- Prova: `simulated`, em `scripts/tests/test_stop_appium_orfao.py` (23 passaram e 1 pulou, no Linux com pwsh 7.4 e
  node 22). Cobre a seleção, a leitura do config, um `node` de verdade encerrado com o de outra árvore poupado, a
  carência e `stop.ps1 -Simular`. `scripts/tests` inteiro deu 173/173 mais 1 pulado, em Python 3.13. `not_run`: o
  teste com `Get-NetTCPConnection` de verdade (só Windows) e o deploy no central.


## 2026-10-02 — Testes: sondas do `_wait_boot` (T.2, J10)

- `backend/tests/test_wait_boot_sondas.py` (novo, 7 testes `simulated`): `boot_completed` antes de `ui_ready`, a interface só sondada depois do boot, `AdbError`/`DriverError` nas sondas engolidos e repetidos, os 120 s sem interface depois do boot (relógio injetável em `manager`, sem dormir), prazo estourado sem `boot_completed`, preparo que falha rápido sem impedir a entrada no ar e o ajuste de apps de fundo virando evento. Só dublês nas três chamadas ao `Adb`; o laço, a fase de prontidão e o estado são os de produção. Mutação (120 s→12000 s; ordem das sondas) derruba os testes. Nenhum código de produção mudou (`manager.py`, `despacho.py` intocados). Ainda de fora: o veredito do snapshot no boot e a extração no agente remoto. Sem deploy.

## 2026-10-02 — W8: teto de 6 reinícios respeita o pior caso de uma iteração (antes do 1º boot)

- `scripts/diag-w8-mitigacao.py`: uma iteração nova só começa se `reinícios_usados + 1 + rede.reinicios_max (config real; 3 se ilegível) <= 6`, para que nem o `FAIL` da última passe do limite do dono. Prova `simulated` (`scripts/tests/test_diag_w8_mitigacao.py`: com 4 usados não começa a 5ª, com 3 começa). `docs/handoffs/w8-boot-recovery.md` §17.5 registra o ajuste, feito antes de qualquer boot (`not_run`).

## 2026-10-02 — W8: protocolo da validação real com as condições do servidor WireGuard (A3, `not_run`)

- **`scripts/diag-w8-mitigacao.py`**: antes de cada reinício do servidor WireGuard (par do android-09 entrando e saindo) espera a **janela ociosa** de 02/03/05/06 (sem execução nem comando aberto; nunca interrompe), observa o reinício e mede a **reconexão** (handshake novo dos pares online em ≤ 120 s; senão para, tira o par e relata); lê o firewall (`firewall-check`, nunca cria regra: sem `liberado` o resultado é BLOQUEADO com os comandos do dono); confere a **pausa do reparo no health antes de cada boot**; o reinício do 09 pelo rollback fica fora dos 6 boots e é registrado à parte. Prova `simulated`: `scripts/tests/test_diag_w8_mitigacao.py` (29 testes). Nada foi executado no parque (`not_run`).
- `docs/handoffs/w8-boot-recovery.md` §17.5: autorização do dono (02/10, via sessão orquestradora), condições e sequência registradas antes do 1º boot.

## 2026-10-02 — IA: cascata para ator barato (17.10)

- `backend/app/taskqueue/executor.py`: (1) `step_blocked` do tier 0 sobe UMA vez ao modelo de escalonamento, na mesma tela, antes de pedir uma pessoa (não vale para `challenge`/`auth_required`/`wrong_account`, nem com efeito já disparado, nem em receita); (2) o "sim" do verificador barato em etapa com efeito externo (ou que confirma o nível de entrega) é conferido UMA vez pelo escalonamento, e vale o veredito mais forte (só age se os modelos diferem). Chaves `ai.cascade_blocked_to_tier1` e `ai.rejudge_yes_on_side_effect`, ambas `true` por padrão; exemplo e `docs/ia.md` §10b. `simulated`: 12 testes em `test_cascata_ator_barato.py`; mutações derrubam. `real`: `not_run` (a bateria paga do `gpt-6-luna` é a próxima). Sem deploy: o ambiente central só muda quando a sessão Android implantar.

## 2026-10-02 — Retrieval: orçamento padrão novo e rodízio de chunks (J13)

- **(a)** `Chunker.chunks_for(..., query=)`: com a pergunta, as janelas de cada arquivo são escolhidas pelos termos dela e os candidatos servidos em rodízio, o 1º em dobro (sem `query` o corte antigo é o mesmo). Medido **de graça** nas 30 perguntas do golden do poetry (`scripts/context-retrieval-chunk-eval.py`): região ao alcance 27/30 com o esperado em 1º (antes 26), 24/30 em 2º (antes 11), 24/30 em 3º (antes 1), 20/23 no local (antes 16); o rodízio simples piora (7/30). Versão da seleção na chave do cache da B. **(b)** teto calibrado contra o provedor: B real/estimada 0,98–1,11 (média 1,01); A real/estimada 1,84 (8,5 mil estimados, 15,65 mil reais); pior caso (mapa de 48 KB + B) ~30 mil. **(c)** padrão de `context_retrieval.semantic`: `max_candidate_files` 8 → 5, `max_chunks` 24 → 16, `max_input_tokens` 24.000 → **32.000**. **(d) `real` 02/10:** confirmação com os padrões novos, sem config de teste, poetry @ `94b6e35`, 12 chamadas 200, zero retry, **US$ 0,005459**, maior pedido 22.280/32.000 tokens; a B rodou nas 6, região achada em 5/6 (igual ao J12; o ganho está na medição grátis; H25 segue fora porque a pergunta descreve em vez de nomear). Retrieval segue desligado por padrão; política de envio inalterada. `simulated`: 16 testes novos (chunker, serviço, orçamento, equivalência com a medição). Sem deploy.

## 2026-10-02 — Avaliação: `eval_run.py` resiste a queda transitória do transporte (17.11, K-045)

- `scripts/eval_run.py` ganhou `Resistente`: repete a chamada (4 tentativas, espera crescente) em `RemoteProtocolError`/`ReadError`/`WriteError`/`ConnectError`/`ReadTimeout`, o POST de `/api/runs` repete com a mesma `idempotency_key` (não abre outra execução) e o laço de espera tolera uma leitura que esgote as tentativas, até o prazo, em vez de largar a execução órfã. Resposta HTTP de erro não é repetida. `simulated`: 7 casos de teste novos em `scripts/tests/test_eval_run.py` (cliente falso, sem rede nem sleep; mutação que apaga a repetição derruba 5). Nenhuma chamada ao backend nem ao adb foi feita; a prova `real` é a próxima bateria. Sem deploy.

## 2026-10-02 — Retrieval: ablação A × A+B por região, medida (J12, `real`)

- `scripts/context-retrieval-ablation.py` (`--analise` grátis; `--run` paga) e a seção "Ablação A × A+B por região" em `docs/dominios/context-retrieval.md`. **`real` 02/10:** `python-poetry/poetry` @ `94b6e35`, 6 perguntas × (A + B) = 12 chamadas, todas 200, zero retry, **US$ 0,005573**, nenhum código do repositório enviado. Config só da execução (5 candidatos, 16 chunks): a B achou a região esperada em **5 de 6** (o local, 2 de 6), ~88% menos linhas a ler que os arquivos inteiros, ~+40% de custo por pergunta; maior pedido 22.748 tokens de 24.000. A única falha (H25) é do chunker (os chunks acabam no 1º candidato). Cobertura da A: 195 entradas de 193 `.py`, todas enviadas, sem corte (dívida fechada). Recomendação de padrão (5 candidatos, 16 chunks) fica para PR separado e medido; os padrões do produto não mudaram. Sem deploy.

## 2026-10-02 — IA: cache de prompt do verificador — prova simulada e prova REAL (J9, achado #100)

- O ponto de cache (`cache_control`) já era pedido no verificador e o custo já lia `cache_read`/`cache_creation`. Três testes `simulated` novos em `backend/tests/test_anthropic_provider.py` e `scripts/verifier-cache-probe.py` (teto, zero retry, sem código do repositório, chave lida só pelo `EnvSettings`). **`real` 02/10 (US$ 0,0241):** prefixo medido ~1 290 tokens; Sonnet 5 e Opus 5.5 gravam na 1ª chamada e **leem 1 290 na 2ª**; Haiku 4.5 (padrão) fica em 0/0 (mínimo 4096). Corrige a leitura anterior ("≈ 560 tokens, nem o Sonnet chega"), que vinha de `len//4`. Padrão do verificador inalterado: o Haiku sem cache ainda é o mais barato. Nenhum código de produção mudou. `docs/ia.md` §5 atualizado.

## 2026-10-02 — Painel: cartão "Retrieval de contexto" na guia IA, só leitura (J8)

- **`frontend/src/features/settings/ContextRetrievalSection.tsx`** (novo) na guia IA de Configuração: lê `GET /api/context-retrieval/status` (ADR-063) e mostra veredito, configuração, proveniência do envio externo com as três provas, orçamento e métricas recentes. Só leitura: um botão "Atualizar", nenhuma escrita, nenhum controle que ligue o remoto. Novos: `api.contextRetrievalStatus`, o tipo `ContextRetrievalStatus` e `lib/contextRetrieval.ts`. `simulated`: 16 testes novos; frontend inteiro 96 arquivos e 1.149 testes, typecheck e build ok. **Verificação visual no navegador: `not_run`**. Sem backend; sem deploy.

## 2026-10-02 — Retrieval: escopo padrão de código no consumidor do plano-100 (J7)

- **`scripts/plano-100-pacotes.py --contexto`** passa a consultar só `backend/app/`, `frontend/src/` e `scripts/` por padrão (`--contexto-escopo PREFIXO`, repetível, troca o padrão; `--contexto-sem-escopo` consulta tudo). Vale só para este consumidor opt-in: serviço, CLI do módulo e API não mudam; sem a flag `--contexto` a saída continua byte a byte a de sempre. `real` (02/10, `context-retrieval-local-eval.py --consumidor`, 40 commits): hit@3 22,5% → 70,0%, hit@5 37,5% → 77,5%, MRR@10 0,195 → 0,555. `simulated`: `scripts/tests/test_pacotes_contexto.py` (11 → 15 testes; o de saída idêntica segue verde) e `test_context_retrieval_local_eval.py` (9). O `local-eval` ganhou `--consumidor`.

## 2026-10-02 — Retrieval: proposta de orçamento da etapa B (J6, só doc)

- `docs/dominios/context-retrieval.md` ganha a seção "Proposta: orçamento da etapa B": a regra do teto (`gasto da A + len(payload)//4 > 24.000` barra a B, ~33 KB de payload), os números reais do smoke (A ~15,6 mil tokens, B ~7,6 mil, US$ 0,00066 a 0,00098 por pergunta), cinco opções com custo estimado, e a recomendação: medir antes o valor da B (ablação A x A + B com métrica de região) e conferir se a A cobre o escopo (`files_considered = 195`). Nada foi alterado nem habilitado; a decisão de habilitar o remoto é do dono.

## 2026-10-02 — Retrieval: índice BM25 incremental por arquivo (J5)

- **`infrastructure/bm25.py`**: o índice guarda o digest do texto de cada arquivo (`INDEX_VERSION` 3, o que invalida os índices em disco antigos uma vez). Com a revisão nova e um índice anterior compatível (memória ou o mais recente em disco), só os arquivos alterados ou novos são higienizados e tokenizados; o resto reaproveita as contagens. O índice sai IDÊNTICO ao cheio. Semente de outro corpus, versão ou raiz, ou corrompida, nunca é usada. Sem mudança de pontuação, de política nem de API.
- **Medição `real`** (02/10, central, 1.415 arquivos, `scripts/context-retrieval-incremental-bench.py`, antes = `main` `0da61af`): uma edição 5,3 s → 1,3 s; processo novo com índice em disco 5,3 s → 1,6 s; trocar para `HEAD~10` 5,0 s → 1,7 s; a frio sem índice, igual (13,3 s → 12,5 s). `simulated`: `test_context_retrieval_bm25_incremental.py` (22, comparação com o índice cheio em 7 tipos de mudança, semente incompatível ou corrompida; duas mutações do código falham os testes).

## 2026-10-02 — Retrieval: medição local de qualidade neste repositório (J4)

- **`scripts/context-retrieval-local-eval.py`** (novo, só mede): avalia lexical, BM25 e híbrido local em 40 commits `feat`/`fix` da `main`, com o índice no estado do pai do commit (sem vazar a resposta). Zero rede e zero chamada paga. `real` (02/10, central, `--ate 40316ba`): com escopo de código o BM25 chega a 82,5% hit@3 e 0,652 MRR@10; sem escopo cai para 30%; o `hybrid_local` fica abaixo do BM25 puro (72,5%); a primeira consulta de cada revisão custa ~5,5 s. Números, limites e propostas (não aplicadas) em `docs/dominios/context-retrieval.md`. `simulated`: `scripts/tests/test_context_retrieval_local_eval.py` (8, git real em pasta temporária; o anti-vazamento falha se o índice usar o commit em vez do pai).

## 2026-10-02 — Retrieval: duas dívidas pequenas fechadas (J3)

- **Rótulo `stage_b_cache`**: ficava `miss` também quando o orçamento barrava a etapa B antes de qualquer chamada. Agora só vira `miss` depois de `check_call` aprovar; bloqueada fica `skipped` (com `stage_b_reason`). Teste discriminante em `test_context_retrieval_semantic.py` (falha com o código antigo).
- **Gate de worktree limpo**: não via arquivo marcado `assume-unchanged` ou `skip-worktree` (o `git status` sai vazio). `worktree_limpo` passa a ler também `git ls-files -v -z` e trata qualquer marca como sujo, mesmo sem alteração; `ls-files` que falha dá `None`. Um sparse-checkout (que usa `skip-worktree`) também bloqueia o envio remoto, de propósito. 6 testes novos em `test_context_retrieval_privacy_gates.py` (modificado escondido, só a marca, desmarcar limpa, git falhando); com o gate antigo eles falham. `simulated`: git real em pasta temporária, GitHub e provedor falsos.

## 2026-10-02 — Falsas falhas de `git worktree` na suíte

- **`worker-install.ps1`**: sem o venv do central à mão, lia `.git\HEAD` como pasta; num `git worktree` o `.git` é um arquivo `gitdir:` e a versão saía `0.1.0+desconhecido`. Agora segue `gitdir:` → HEAD do worktree → ref na pasta comum (`commondir`) ou em `packed-refs`, espelhando `app/version.py`. Prova: `test_instalacao_do_worker.py` 20/20 em worktree e 25/25 (com o backup) num checkout normal; o script real foi rodado com ref solta, `packed-refs`, HEAD destacado e worktree, e a versão bateu com o SHA.
- **`test_backup_e_restore_ensaio_de_ponta_a_ponta`**: dependia do `config/config.yaml` da instalação (copiado pelo `backup.ps1`), que não existe em worktree. O teste agora monta a própria raiz temporária (scripts reais, `config.yaml` de fixture, interpretador do venv em uso) e roda a cópia; não toca no `config.yaml` real. Controle: o teste antigo falha num worktree com venv e sem config; o novo passa. Só testes e um script; sem mudança no backend.

## 2026-10-02 — Retrieval de contexto: PR #18 mergeado e prova real do Jev registrada

- **Docs.** `docs/dominios/context-retrieval.md` passa de `not_run` para `real` na chamada ao Jev em código PÚBLICO (`python-poetry/poetry` @ `94b6e35`, 02/10, commits `a07ff80` e `a88d609`; 11 de 12 chamadas, 0 fallbacks, ~US$ 0,006; etapa A/B por caso: H14 A+B, H13 só A com B bloqueada por `budget_exceeded`) e lista as dívidas para habilitar o uso remoto em "Limites conhecidos". `docs/estado-atual.md` com o topo atualizado. Só docs; sem chave, sem código.

## 2026-10-01 (noite) — Retrieval de contexto de código (ADR-063), branch `feat/context-retrieval`

Na branch, **não mergeada na `main`** e **não implantada**; desligado por padrão (`context_retrieval.enabled: false`), então
mergear não muda comportamento nenhum. Prova `simulated` (provedores falsos, transporte simulado, sem rede); chamada real ao Jev `not_run`
(`REAL_JEV_NETWORK_CALLS = 0`). `claude/jev-pilot` segue como evidência e não foi mergeada.

- **Módulo novo** `backend/app/modules/context_retrieval/`: `ContextRetriever`→`ContextSelection`→`ContextPack`; retrievers
  léxico (ripgrep com caminho Python equivalente), BM25 (stdlib), semântico em duas etapas (mapa→arquivos, chunks→regiões) e
  híbrido (regra v1 do piloto: salvaguarda lexical no topo, semântico completa); modos `disabled`/`local_only`/`shadow`/`hybrid`.
- **Provedor plugável** (`SemanticProvider`): falso determinístico e adaptador Jev (`POST /v1/systemone`, chave só em
  `TYPESAFE_API_KEY`). A regra híbrida não conhece o provedor.
- **Privacidade como política única** (`ExternalContextPolicy`): repositório privado a provedor remoto é negado
  (`PRIVATE_CODE_SEND_APPROVED = False`, constante de código); caminho sensível nunca entra em índice, mapa ou chunk; portão duro de
  segredo bloqueia o pedido (reaproveita `security/redaction.py` para o "mole").
- **Fail-open, orçamento, cache e observabilidade**: toda falha do semântico cai no local com `fallback_reason`; teto de chamadas,
  tokens, custo, prazo e payload; cache por revisão do repositório; eventos por lista fechada de campos (nunca código nem a pergunta
  crua) e `GET /api/context-retrieval/status`.
- **Primeiro ponto de integração**: `scripts/plano-100-pacotes.py --contexto` (opt-in) e a CLI
  `python -m app.modules.context_retrieval.presentation.cli`.
- Prova: `backend/tests/test_context_retrieval_{core,local,semantic,integration}.py` e `scripts/tests/test_pacotes_contexto.py`.
- **Estabilização (2ª rodada)**: índice BM25 persistente por revisão (gravação atômica, corrompido = miss, higiene de segredo, poda);
  `--contexto` usa um serviço só no lote (orçamento de sessão compartilhado); motor léxico Python por contagem (paridade com `rg`);
  `rg` opcional com descoberta robusta; `docs-check` não exige mais o handoff local; regressão contra o piloto (30 casos públicos).
  Medido no plano-100 inteiro: 96,7 s contra ~1.550 s (16x). Prova: `test_context_retrieval_{bm25_cache,hardening,pilot_regression}.py`.
- **Fechamento de privacidade (PR #18)**: `public` no YAML não basta mais: o envio remoto exige a prova independente de que o repositório
  real é público (git remote + GitHub anônimo, `UNKNOWN` bloqueia, cache de 15 min só de `PUBLIC`); o status não faz rede e não afirma
  autorização sem prova vigente; segredo mole e duro no mapa da etapa A é omitido (`RETRIEVAL_VERSION` 2). Prova:
  `test_context_retrieval_privacy_gates.py`.
- **Procedência pública (PR #18)**: remoto público não provava que o CONTEÚDO LOCAL era público (arquivo não rastreado entra no universo
  do workspace; o HEAD local pode não estar publicado). O envio remoto agora exige também worktree LIMPO (`git status --porcelain=v1
  --untracked-files=all` vazio, lido a cada chamada, sem cache) e HEAD público (`GET /repos/{dono}/{repo}/commits/{sha}` anônimo,
  `sha` exato). Prova por remoto + SHA; sujar bloqueia na hora e sem rede; o status traz `remote_visibility_verified`,
  `head_public_verified`, `worktree_clean`. `RETRIEVAL_VERSION` 3. Prova `simulated`: `test_context_retrieval_privacy_gates.py` (git
  real em diretório temporário, GitHub simulado); Jev real `not_run`.
- **Privacidade (PR #18)**: `synthetic` + provedor remoto passa de permitido a NEGADO (constante de código `SYNTHETIC_REMOTE_SEND_APPROVED = False`,
  sem campo de configuração). Remoto: privado negado, sintético negado, público só com `allow_public` explícito. Prova: `test_context_retrieval_{core,semantic,hardening}.py`.
- **Revisão final do PR #18** (duas revisões independentes, só leitura): a chamada ao provedor passa a contar quando autorizada (falha
  também gasta a cota da sessão); cache do mapa corrompido é miss; `.tmp` único e sem sobra; texto de região com a mesma numeração
  de linha dos retrievers (`\x0c`); `sk-proj-…` é segredo duro. Limites abertos em `docs/dominios/context-retrieval.md`.
- **Fumaça pública por etapa** (`scripts/context-retrieval-public-smoke.py`): `--cases` (subconjunto das 6 perguntas escolhidas) e `--max-calls` (só baixa o teto de 12); por caso o resumo grava chamadas HTTP e do serviço, cache do mapa e dos chunks, arquivos e chunks enviados e o motivo da etapa B. Fecha a dívida `STAGE_A/B_PER_CASE`. Prova: `simulated`, `scripts/tests/test_context_retrieval_public_smoke.py` (9, sem rede). Sem mudança no backend.

## 2026-10-01 (noite) — hierarquia lida de sessão UiAutomator2 morta recria a sessão (branch `fix/uia2-sessao-morta`)

- **Correção.** `DeviceManager.hierarchy` só devolvia 503 quando a sessão morria por baixo (reboot pelo worker no
  android-09): só o executor invalidava a sessão. Agora, com erro de sessão perdida (`sessao_perdida`) e a sessão ainda
  "pronta", invalida, reabre **uma** vez e relê **uma** vez. Um `restart`/`reset` do worker concluído com sucesso (e `start`/`wake` quando o agente afirma `started: true`) também
  descarta a sessão de antes do boot e dispara a nova; `ensure_automation` fecha e reabre, sem `close` fora da exclusão.  Contrato em `docs/dominios/parque.md`.
- Prova: `simulated`, `tests/test_hierarquia_sessao_morta.py` (23 casos, incluindo a corrida readoção × leitura) e os testes
  vizinhos. `not_run`: aparelho real, deploy.

## 2026-10-01 (tarde) — Revisão de UX/UI do portal, rodada 2

Integrado na `main`; **não implantado** (só o painel; sem backend). Prova `simulated` e `real` contra o backend simulado do
worktree. Relatórios em [`docs/revisoes-ux/rodada-2/`](docs/revisoes-ux/rodada-2/).

- **Cabeçalho:** uma linha de 56 px no celular (Menu, marca, saúde, "Resumo"); chip "Recursos" no tablet.
- **Persona:** cabeçalho único, 5 seções, nome legível na URL (`#/personas/lucas-almeida`) com o id antigo aceito.
- **Execução e pendências:** resumo no topo da execução, aba padrão por situação, regra de pendências testada nas quatro
  origens; total com "4+" quando uma origem falha.
- **Acessibilidade e texto:** menu expandido a partir de 1280 px, alvos de 32 px, rótulo do gráfico em 13 px, nome acessível do
  menu começando pelo texto visível, `TruncatedText` e quebra de linha nos metadados da Infraestrutura.
- **Correções da revalidação:** caixa "Responda aqui" e barra de seleção legíveis a 390 px; sem contagem de sucesso em plano.

## 2026-10-01 (madrugada) — Revisão de UX/UI do portal

Integrado na `main`; **não implantado** (exige reiniciar o backend: o `GET /api/snapshot` mudou). Prova `simulated`; leitura
`real` no central, sem ação. Relatórios em [`docs/revisoes-ux/`](docs/revisoes-ux/), decisões no ADR-062.

- **Menu e rotas:** menu lateral recolhível (gaveta abaixo de 1024 px), rotas por objeto e aba na URL (`lib/rotas.ts`),
  `#/perfis` redireciona para `#/personas`, foco do aparelho em `?foco=`.
- **Números e saúde:** fonte única `store/metricas.ts`; aparelho de servidor inalcançável conta como desconhecido; semáforo
  OK/Atenção/Crítico com motivos e links.
- **Painel:** barra de seleção presa ao topo (só age no que está visível), drawer de foco que sobrepõe, cards compactos
  para aparelho parado, visão Cartões/Lista, traduções de comandos e tempo relativo único (`lib/rotulos.ts`, `lib/time.ts`).
- **Listas:** busca, filtros, ordenação e visão em tabela em Personas e Execuções, tudo na URL (`BarraListagem`).
- **Pendências:** caixa única (`#/pendencias`) com contador no menu; um só "Novo aplicativo" (em Aplicativos);
  `planned` deixou de contar como em andamento e ganhou o chip "Planejadas"; "Com pendência" virou "Pede atenção".
- **Acessibilidade:** tipografia mínima de 13 px, alvos de 32 px, contraste AA (axe 4.13: zero falhas de contraste em 8
  telas a 1440 px), gaveta do menu com Tab preso.
- **Backend (uma linha):** `GET /api/snapshot` traz as 20 recentes, todas as `needs_input` e as não terminais exceto
  `planned` (`api.py`, `docs/api-contract.md`).

## 2026-09-30 (noite) — item 23.8: o Outlook como dado

Feito num branch de worktree; entra na `main` pela sessão que coordena a onda. Prova `simulated`; aparelho e conta
Microsoft reais, `not_run` (29.12, 23.13).

- **Login gerenciado do Outlook** (`app/conhecimento/apps/com.microsoft.office.outlook/`): `telas.yaml` e
  `sessao.yaml` com o login em etapas observado no android-10 (boas-vindas → "Add account" → e-mail → "Continue" →
  WebView da Microsoft → "Use your password" → senha pelo canal sensível → "Next"); tudo depois da senha e os
  desafios da Microsoft são suposição marcada nos arquivos e terminam incertos quando não casam. `app.yaml` ganha
  `provedor_de_sessao: microsoft` (segue sem ser âncora).
- **Catálogo só de leitura preparado e mantido fora da `main`** (commit `806eed9` do branch do agente): com ele, o
  Outlook deixaria de ser app de etapa livre no plano entre apps (ADR-058) e não leria valor para outra etapa (24.3) —
  o C1 do dono depende disso, e 16 testes do plano entre apps ficariam vermelhos.
- **Remover conta** recusa só a do app âncora (`SocialService.delete_account`), não toda conta com login automático.
- **Motor genérico** (`integrations/app_declarado/`): `etapa_do_usuario.entrada` (a tela do app deslogado e o botão
  que abre a do identificador) e `etapa_do_usuario.alternativas` (escolher a senha na tela que propõe código; nunca
  em conta travada), com `formulario.py::botao_unico`. `automation/hierarchy.py` reconhece "Help us protect your
  account" como conta travada em qualquer app.
- Testes: `backend/tests/test_outlook_declarado.py`; `test_perfil_multiapp.py`,
  `test_roteamento_por_conjunto_de_apps.py` e `test_pacote_declarado.py` passam a esperar o login gerenciado (e o
  catálogo) do Outlook; `test_sensitive_input.py` ganha a página da Microsoft.
- **Depois do "Next", pelo login real do André** (android-06, 22:18–22:24Z): o aviso da conta Microsoft ("OK"), o
  diálogo de chave de acesso do sistema (Voltar), "Authentication in progress", "Add another account" ("MAYBE LATER"),
  privacidade ("NEXT"), diagnóstico ("Decline"), experiências ("CONTINUE TO OUTLOOK"), a caixa e a gaveta viram dado
  observado. O motor genérico ganha `dispensa.por_tela` (o botão de UMA tela `intersticial`), `dispensa.voltar` (o
  Voltar num diálogo de outro pacote) — aplicados depois do envio, na abertura e na leitura da conta — e
  `extracoes.<nome>.dentro_de` (a conta lida só dentro do painel da gaveta, onde o e-mail não tem id).

## 2026-09-30 (tarde) — Fase 29: pendências da terceira evolução (CI, prova durável de vazamento, firewall)

**Integrado** na `main`; a implantação e a observação de 6 h são o item 29.4 (o estado fica em
[handoffs/pendencias-evolucao3.md](docs/handoffs/pendencias-evolucao3.md)). A pesquisa de 30/09 corrigiu o diagnóstico
do P15: o Outlook não recusa o emulador; o que cai é o renderizador SwiftShader-GL do host.

- **CI (29.1, `9428a6a`):** o `brace-expansion` 5.0.9 (alta) vem dentro do tarball do `appium-uiautomator2-driver`
  8.7.0. `npm audit fix` e `overrides` não alcançam dependência empacotada, e o lock editado deixava o audit verde com
  a 5.0.9 no disco. `tools/appium/corrigir-empacotados.mjs` troca o arquivo instalado no `postinstall`, e o CI instala
  e confere o disco. Prova `real`: run 36713946044 verde. K-064.
- **Prova durável de vazamento (29.2, P16, ADR-061, migração 063):** a prova do teste com o cliente VPN parado passa a
  morar em `device_network` (`leak_*`), presa à revisão e à instalação do cliente VPN. A intenção é gravada antes do
  `force-stop`; o ensaio interrompido por reinício do backend é fechado como inconclusivo, sem parar o cliente de novo;
  cliente novo, wipe e `POST …/verify` invalidam; o relógio da medição não. É a prova da linha, e não o `leak_blocked`
  da medição, que decide `trafego_verificado` e a porta da tarefa. Quem ficou sem desfecho que aprove deixa de ser
  medido em laço. Na transição, a prova anterior só é adotada com o histórico e a data do APK demonstrados. Prova:
  `simulated` (`tests/test_rede_sonda.py`, 12 cenários novos; o do reinício do backend falha no código de `6997091`);
  `real` para a leitura do cliente em três aparelhos e para a 063 numa cópia do banco do central. K-065.
- **Túnel que não sobe no boot (29.3):** medido no android-05 (7 boots): o always-on tenta uma vez por boot e falhou
  em 5 de 7 com o convidado sem CPU; cada conferência sem `tun0` pedia outro reinício (android-06: 6 reinícios por um
  teste). A espera passa a 180 s contados do boot, e, com a configuração valendo e só o túnel faltando, o tile do
  cliente é tentado antes do reinício — também depois do teste de vazamento, que deixa de custar um reinício. Prova:
  `simulated` e `real` no android-05 (o código do gesto, duas vezes, 13:30Z). K-066.
- **Revisão independente do P16:** quatro achados corrigidos antes da publicação, cada um com o teste que falhava —
  uma leitura que falha antes da adoção virava teste destrutivo na passada seguinte; `verify` e wipe não deixavam
  marca na linha antiga (a prova era readotada); linha recriada adotava a prova da anterior; e o desfecho que não
  aprova media a cada passada quando a sonda não trazia IP.
- **CI de novo vermelho às 17:36Z (axios):** sete avisos novos na `axios` 1.19.0 (um alto). A da raiz sobe para a
  1.20.0 por `overrides`; as duas cópias empacotadas no driver entram no mesmo corretor do 29.1, agora com caminho
  aninhado. Conferido numa instalação limpa (audit alto sem achado, disco em 1.20.0, Appium no ar com o driver). K-064.
- **Deploy:** `deploy.ps1` passa a instalar as dependências do Appium quando o lock muda e a conferir o disco.
- **Depois da janela (30/09, 21:30–23:10Z):** renderizador `host` em todo o parque (decisão do dono), contas reais
  uma por vez com o Instagram conferido; Outlook promovido e distribuído; as três contas Outlook logadas no aparelho do
  Instagram de cada persona (duas sozinhas, pelas telas declaradas); C1 Outlook → Instagram real e só de leitura no
  android-01. Correções medidas no caminho: GMS da imagem `google_apis` lido pelo pacote; botão de avançar
  desabilitado até o identificador; rótulo repetido na descrição; releitura do campo sensível num WebView. W4 da rede no
  notebook falhou (túnel e ADB) e foi revertido.
- **Sonda UDP com repetição (29.5):** até 3 datagramas de 2 s por perna, parando no primeiro com resposta; o `detail`
  diz bytes, tentativa e tempo por perna (DNS e NTP), e a listagem ganha `udp_dns_ok`/`udp_ntp_ok`. UDP segue fora do
  critério de `trafego_verificado`. Prova `real`: o comando novo em android-05 e android-02 (83/48 B na 1ª, ~2 s por
  perna; a ida caiu de ~10 s para 4,4 s).
- **Saída esperada por aparelho (29.6, P1):** `params.egress_esperado` no perfil, comparada com a saída medida
  (diferente vira `parcial` com o motivo), `egress_expected`/`egress_matches` na listagem e avisos na prévia da
  atribuição quando uma saída dedicada vai a mais de um aparelho ou é trocada por compartilhada. Prova `simulated`; o
  piloto com dois servidores (29.7) depende do dono.
- **Firewall do central para a LAN (29.8):** a leitura passa a conferir porta, perfil efetivo, interface, origem e
  programa de cada regra; estado novo `regra_obsoleta`; o comando proposto sai sem `-Program`, restrito à sub-rede e à
  interface lidas do sistema, idempotente, com inspeção e reversão. Uma regra presa a outra interface era lida como
  `liberado`. Prova: `simulated` (`tests/test_rede_worker.py`, 9 casos novos) e leitura `real` no central (só leitura);
  a regra criada e o aparelho do notebook com rede seguem `not_run` (29.9).
- **Painel:** a prova de vazamento por aparelho (revisão, data e cliente) e o estado novo do firewall, com os comandos
  de inspeção e reversão.
- **Renderizador do emulador por aparelho e por worker (29.10, 29.11):** medido em 30/09 — o Outlook derruba o
  emulador com o SwiftShader-GL do host e roda com a GPU do host, inclusive pelo serviço (sessão 0), no central
  (android-07, canário oficial aprovado) e no notebook (android-09); `skiavk` foi refutado. O `gpu_mode` por aparelho
  (`instances.overrides`) e por worker (`worker.yaml`) já existia e vale em toda subida. Novo: o renderizador
  **selecionado** é lido do log do emulador (`InstanceDTO.renderer`), o fallback silencioso vira aviso no aparelho, e
  um app declara em `app.yaml` o renderizador que recusa (`renderizador_recusado`) — instalar, abrir, distribuir e o
  pré-voo recusam com `app_incompativel` em vez de derrubar o emulador. O agente do worker declara o renderizador na
  batida. Prova: `simulated` (`tests/test_renderizador.py`); `real` para a configuração e o canário; a leitura pela
  API espera a segunda implantação, depois da janela do P16.
- **Plano:** Fase 29 (18 itens) e handoff; 12.3 sai de `pending` para `partial/real` pelo mecanismo.

## 2026-09-30 (madrugada) — Terceira evolução: rede por aparelho real, comando entre apps real, contas por app; Outlook bloqueado pelo emulador

**Implantado** no central (e no agente do notebook) em ondas: `99fc90a` (contratos, migrações 056 e 057), `081d696`
(Onda 1), `5306b5d` (Onda 2 da rede, migração 058) e as correções achadas nas provas reais (`a097f00`, `549a297`,
`6460baf`, `e7d44ce`). Detalhe e provas em [relatório §26](docs/relatorio-validacao.md); estado por frente e
pendências em [handoffs/terceira-evolucao.md](docs/handoffs/terceira-evolucao.md).

- **Rede por aparelho (ADR-056, Fase 25):** VPN no Android (sing-box) com servidor do central em modo usuário, perfis e
  atribuição por aparelho com prévia e confirmação para conta real, convergência (aplicar, reiniciar, conectar,
  verificar, deriva, rollback), sonda de saída por `nc` e `netstats` por UID, teste de vazamento, portão de rede no
  scheduler, painel Rede. **Real:** android-05, android-02 (QA), android-06 (André) e android-03 (Bruno) em
  `trafego_verificado`, com o bloqueio fora da VPN provado e a saída compartilhada detectada (IP do central).
- **Comando entre aplicativos (ADR-058, Fase 24):** catálogo de vários apps, saídas de etapa (`read_value`,
  `{{saida:nome}}`, migração 056), conta e portas do app da etapa, roteamento por conjunto de apps, etapa de outro app
  não conclui com o app errado na frente. **Real:** QA Messenger → Chrome no android-05, conta indisponível e reinício
  do backend no meio sem repetir etapa.
- **Contas por app (ADR-057, Fase 23):** sessão por conta (fim da âncora única), desafio só na conta do app, formulário
  em etapas, senha clonada no cofre, painel de contas por app. **Real:** contas Outlook de André, Bruno e Lucas com a
  senha clonada (consentimento pendente do dono).
- **Outlook (bloqueado, P15):** importado da loja depois de corrigir o inspetor (split sem esquema v1), mas o Outlook
  5.2635.3 derruba o emulador 37.1.11/37.2.11 e, no 37.3.2, morre numa armadilha proposital (`UD2`) da `libhxcomm.so`.
- **Pedidos persistentes (Fase 26):** pesquisa e desenho completos; Fase 28 registrada para a implementação.
- Documentação e processo: ADR-056 a 059, adendos v0.41 e v0.42, `docs/banco.md` (056–058), relatório §26, handoff.

## 2026-09-29 (noite) — Planejamento da terceira evolução: Outlook, comando entre apps, rede por aparelho, pedidos persistentes

Só documentação e processo; nenhum código, nenhum aparelho tocado, nenhuma chamada paga. Pedido do dono de 29/09.

- Documentação e processo: diagnóstico e desenho em `docs/design/terceira-evolucao.md` (novo); esqueleto da pesquisa
  em `docs/design/pedidos-persistentes.md` (novo); coordenação em `docs/handoffs/terceira-evolucao.md` (novo); Fases
  23–27 do plano-100 (43 itens, blocos novos no mapa e Opus forçado nos itens delicados); ADR-056 (rede por aparelho,
  revisa a cláusula de rede do ADR-055, decisão do dono) e ADR-057 (Outlook, sessão por conta, credencial clonada no
  cofre, decisão do dono), ADR-058 e ADR-059 propostos; 12.3 decidido no roadmap; invariante do `CLAUDE.md` e K-057
  com a ressalva do ADR-056.

## 2026-09-29 (noite) — Fase 22: as pendências da rodada (ADR-054)

**Implantado** no central em 29/09 ~17:05 UTC: `b34e2f6`, sem migração nova; `/api/health` `ok`, `problems: []`
(depois de um `appium_down` passageiro na subida); agente do notebook em `0.1.0+b34e2f6`. Leva junto o layout da outra
sessão (`13fb5c0`). Cinco pacotes com revisão adversarial e correção ([relatório §25](docs/relatorio-validacao.md)).

- **Operador nos gestos (22.1, `34976e8` + `a15f864`).** Resolver o item, repetir, responder e tomar o controle gravam o
  operador da sessão. Sinal de gesto é um por evento (o primeiro autor fica).
- **Nota do comando (22.2).** O painel manda só o texto da pessoa (`origin: 'panel'`); o backend compõe o contexto; o
  `requested_by` passa pela triagem.
- **Tela da falha (22.3, `0820d5d` + `f4821fa`).** `attempts.failure_screen` gravado (nome declarado ou tipo do motor;
  NULL se desconhecida), e o backlog não dá como corrigida a linha sem tela nem a nomeada que deixou de ser reconhecida.
- **Adoção de fluxo com trilha e savepoint nas lojas (22.4, 22.5, `8632821` + `4e52b15`).** A adoção grava quem
  decidiu; a trilha das lojas não derruba mais o save no PostgreSQL, e a transação abortada vira erro em vez de perda
  calada (`TransacaoAbortada`, K-061).
- **Preferência com a execução (22.6, `f96d50e` + `946894d`).** Aparece no bloco "Aprendizado desta execução" da
  execução que fechou o limiar ("entre N execuções").
- **Correção de ensino pela execução (22.7, `eadf0b5` + `f545fa0`).** "Corrigir esta etapa" na aba "Por aparelho", para
  etapa de habilidade que falhou ou ficou incerta.
- **Aviso de saldo (22.8).** O 503 do relatório de uso era o incidente da Anthropic de 29/09 (status público desde 14:21
  UTC); nada a mudar.
- **Arrumação (22.9).** 28 worktrees da rodada removidos, com as junções desfeitas antes.
- **PostgreSQL.** O subconjunto do SQL novo (34 arquivos) deu 656 ok em PostgreSQL 17, e a suíte inteira 3497 ok (só a falha de
  ambiente do `test_backup`).

## 2026-09-29 (tarde) — Painel: "Resultado por instância" em cartões e "Outros dados" do Diagnóstico legível

Pedido do dono: os dois estavam "muito ruins de ler". Só painel; nenhuma mudança de API. Descrição em
[produto.md §3](docs/produto.md).

- **Relatório da execução.** Um cartão por aparelho no lugar da tabela de ~15 colunas. O cabeçalho mostra a situação,
  o selo de prova, a entrega e onde rodou. O corpo traz as etapas em três grupos que não se fundem (comprovadas com a
  prova, confirmadas à mão, em aberto no plano final) e os efeitos externos com o horário. "Comprovado" só aparece com
  `proven: true`. Com mais de 3 aparelhos, os comprovados começam recolhidos.
- **Diagnóstico › Outros dados.** Um bloco com título por chave: SDK, teste de escala (tabela por leva e tabela
  aparelho × leva), imagens medidas e levantamento do host. A chave desconhecida continua visível.
- **Prova.** `simulated`: typecheck e vitest 751/751. Os testes novos estão em `resultadoDaInstancia.test.ts`,
  `ReportTab.test.tsx` (falha com a etapa comprovada e em aberto, sucesso com etapa à mão, recolhimento) e
  `outros.test.ts`/`DiagnosticsPage.test.tsx`. Visual: Vite local com os dados reais do central, só leitura (6eb84c,
  7cfa59 com 8 aparelhos, 02ee9e a 375 px).
- **Implantado** no central junto com a Fase 22 da outra sessão: `/api/health` em `b34e2f6` (que contém `13fb5c0`), o
  `frontend/dist` servido tem o código novo. Prova `real` (29/09 ~17:10Z, painel em 127.0.0.1:8000): a 6eb84c ›
  Relatório mostra o cartão do android-06 (Sucesso, Comprovado, Entregue, `WIN-7S2UASNLFOP · emulator-5564`, 5 etapas
  comprovadas com a prova e 2 efeitos com horário), e o Diagnóstico › Outros dados mostra as 5 seções com as tabelas.
  CI verde em `13fb5c0` (run 36584078149; PostgreSQL pulado).

## 2026-09-29 (tarde) — O código em aberto: interruptor antigo pelo livro, bloco da execução, três sinais, dívida de import e irq medido (ADR-054)

**Implantado** no central em 29/09 ~14:10 UTC: `c071341`, sem migração nova. `/api/health` com o commit e a 055; o
`degraded` é só o `ai_balance_stale` (relatório da Anthropic respondeu 503). Agente do notebook em `0.1.0+c071341`
(nenhuma entrada do manifesto mudou). Três pacotes com revisão adversarial e correção dos achados menores
([relatório §24](docs/relatorio-validacao.md)).

- **Interruptor antigo pelo livro (`0f91fb3`, `5342743`, `c655495`).** `PUT /api/flows` e `PUT /api/recipes` gravam a
  trilha com a pessoa e aplicam o veto e as guardas do livro. Recusas novas: receita inexistente 404, receita
  substituída 409 `transition_forbidden`, segunda ativa na mesma chave 409 `state_conflict`. O fluxo candidato é
  anunciado uma vez só, e a decisão do `_learn_flow` não derruba o fim da execução. O painel mostra "Em prova" e
  "Esperando o dono". Real: trilha `published → disabled → published` pelo interruptor às 14:13Z.
- **Bloco "Aprendizado desta execução" e projeção no painel (`a54735a`, `1d30d8d`, `d506337`).**
  `GET /api/runs/{id}/feedback` traz `aprendizado` (receitas, fluxos, falhas, candidatas, lições), com a autoria de
  pessoa separada da execução. O cartão de custo mostra "Normal medido para este plano" com a janela efetiva. Real: a
  6eb84c mostra as duas receitas usadas e o fluxo aprendido.
- **Dívida de import paga e três sinais com escritor (`2b0e5db`, `d33b8ab`, `61c3bad`).** O contrato de gesto foi para
  `app/shared/costuras.py`, e os aparelhos deixam de importar a fila. `cancelou_execucao` (um por episódio),
  `comando_incerto_resolvido` e `correcao_de_ensino` levam o operador da sessão. As polaridades esperam o dono.
- **Nota triada no comando (`61c3bad`, `5595aea`).** A nota da resolução e do pedido de cancelamento de comando com cara
  de credencial é recusada (409 `note_looks_secret`) antes de qualquer escrita; antes ia crua ao evento do comando.
- **Causa do irq ocioso medida (K-060, 21.15, `d4ab5cf`).** Dois terços são o processo do Instagram logado rodando;
  um terço, o tempo no ar. A CPU do emulador no host não muda com o app parado, então não há mecanismo novo.
- **CI de contêiner.** As falhas desde 28/09 são recusa de cobrança do Actions (0 passos executados), não código. Reteste
  na corrida semanal de 05/10.

## 2026-09-29 (manhã) — Aprendizado contínuo A2–A9, apps de segundo plano e a espera do reparo (ADR-054, ADR-055)

**Implantado** no central em 29/09 ~07:38 UTC: `f497075`, sem migração nova (a 055 já estava aplicada); `/api/health`
`ok`, `problems: []`; agente do notebook em `0.1.0+f497075`. A integração `claude/rodada-aprendizado` junta nove
pacotes, cada um com revisão adversarial (seis barrados e ajustados), e o ajuste de tipo do lease em
`devices/manager.py` ([relatório §23](docs/relatorio-validacao.md)).

- **Costuras (20.3, `fa7349e`).** `taskqueue/costuras.py`, tipado e no-op sem o livro. `failure_kind` passa a ser
  gravado na tentativa, na reconciliação e na etapa. Confirmar à mão, repetir, abandonar, repetir a execução, responder
  (só o sha256) e tomar o controle (só ids) viram sinais.
- **O que mais falha (20.4, `2adae8c` + `a5d2e97`).** `GET /api/aprendizado/falhas` e o backlog com prova da correção.
  A reincidência depois de `fixed` é medida numa janela que anda. `scripts/aprendizado-backlog.py`.
- **Botão D2 (20.5, `9c40288` + `faf397e`).** "Deu certo / Deu errado + motivo" por objetivo e por execução. "Deu
  errado" por navegação desliga o que o item usou e aprendeu. O "desfazer" só existe para o que estava publicado.
- **D1 nos nativos (20.6, `48783e8` + `aab27b8`).** O fluxo aprendido nasce candidato e é publicado por concordância em
  sombra; o fluxo e a receita com efeito esperam o dono. A execução de habilidade grava `skill_validation_results`, e a
  confirmada à mão vale como `uncertain`.
- **Página Aprendizado (20.7, `c3c3760` + `86d9223`).** Abas Para aprovar (com Revisar), Aprendido, O que mais falha e
  Sinais, e a contagem na barra do topo.
- **Lições medidas (20.8, `9c016bb` + `ebfc643` + `3cd1e9f`).** Nascem por contraste, com texto fechado e braço de
  controle, e vão ao ator e ao planejador, nunca ao verificador. Ficam em `shadow` de fábrica.
- **Telas aprendidas (20.9, `43600f1` + `476c3be`).** A fatia 5 do ADR-052, com export para o YAML. Ficam em `observe`
  de fábrica.
- **Voz e preferências (20.10, `9607f4d` + `0e971b9` + `0663738`).** A voz vem das aprovações editadas e é sempre
  publicada pelo dono. A preferência só sugere, e só decide sozinha nas versões conferidas, sem efeito.
- **Apps de segundo plano (21.15, `e2b54a0` + `b5036ec`).** `android.desativar_apps` desativa 13 apps do Google no
  preparo, reversível. Real: 11 desativados no android-01 e no android-06; `MemAvailable` de 670–960 para
  974–1054 MB; o Instagram segue ok (K-059).
- **O reparo espera a máquina aliviar (21.16, `9348e9c`, no ar desde ~04:17Z).** Com a CPU do central ≥ 90%
  (`instances.remediation_host_cpu_max`), o reparo adia 10 min em vez de subir de degrau. É a resposta ao `reset` das
  02:15Z, que apagou a sessão do lucas no android-01 com a máquina saturada pela IDE (K-058). A conta não foi tocada
  desde então; reativá-la é decisão do dono.
- Prova: `real` só de leitura depois do deploy (livro 125 itens, `/revisar` 39, `/pendentes` 0; "o que mais falha"
  com 19 grupos acima do mínimo, todos retroativos; o evento dos apps de fundo) e o incidente;
  `simulated` na integração (17 arquivos alterados 263 ok; suíte inteira em SQLite 3409 ok e 1 falha de ambiente,
  `test_backup`; mypy estrito 164 arquivos; frontend 711 ok); `not_run`: PostgreSQL para A2–A9, lições, telas, voz e
  preferências reais, o voto numa execução real e o adiamento do reparo num episódio real.
- Dívida aceita: `devices/manager.py` importa `taskqueue.costuras` (o primeiro `devices` → `taskqueue`; sem ciclo).
- Documentação e processo: `docs/dominios/aprendizado.md` (novo), evidências e regras da revisão no ADR-054, incidente
  e a espera do reparo no ADR-055, K-058 e K-059, relatório §23, adendo v0.38, `ia.md` §15, `produto.md`, fatia 5 no
  design do conhecimento de app, `parque.md` (reparo e apps de segundo plano), `operacao.md`, plano-100 (20.x
  atualizados e o 21.16 novo).

## 2026-09-29 — Proteção de contas, fundação do aprendizado e as pendências da rodada (ADR-054, ADR-055)

**Implantado** no central: `e9da86e` (28/09) e a leva aberta `7a02491` (suíte do backend 2912 ok, só `test_backup` fora
do checkout com `config.yaml`; agente do notebook em `0.1.0+7a02491`). O relógio do host passou a ser mantido pela
tarefa `farm-relogio` (`b25957e`). **Implantado em 29/09 ~03:55Z:** `c359f65` (proteção de contas + fundação do
aprendizado, migrações 054 e 055 ensaiadas antes) e `2511b12`, com o frontend no mesmo deploy
([relatório §22](docs/relatorio-validacao.md)).

Origem: o dono pediu em 28/09 para "resolver tudo o que ficou em aberto" (autorizou tudo) e, em 29/09, deu a regra de
que parar em "Confirm you're human" é conta perdida. Cinco das oito contas do Instagram estão bloqueadas; a investigação
mostrou uma frota coordenada sobre as mesmas pessoas, e o código não aplicava a regra (Fases 20 e 21 do plano-100).

- **Escada sem reset com conta (21.6, `e9da86e`).** O reinício por irq e o religar da reconciliação não contam mais como
  degrau da escada de reparo (`requested_by` `saude` e `reconciliacao`); com vínculo ou conta travada, o 3º degrau é
  "Precisa do dono" + `stop`, nunca `reset`.
- **Relógio do host (21.7, `b25957e`).** A rede bloqueia NTP com porta de origem 123; `scripts/sincronizar-relogio.ps1`
  mede pelo `stripchart` e ajusta, e a tarefa `farm-relogio` roda a cada 15 min: +6,240 s → +0,004 s (K-055).
- **Leva aberta (21.10–21.14, `7a02491`).** Leitura que falha fora do Appium relê sem recriar a sessão; a aprovação
  acompanha a etapa revisada e o "Tentar novamente" herda os textos; `caption_contains` herdado pelo catálogo; irq
  persistido em `measurements` e `GET /api/desempenho?irq_horas=`; receita da IA nasce candidata e só age depois de 2
  concordâncias.
- **Proteção de contas (21.1–21.5, `c359f65`).** Detector único de conta travada, sem tocar, em qualquer idioma;
  quarentena do aparelho com o marcador do android-04; uma conta por alvo, com recusa, e DM fria sempre com aprovação;
  disjuntor que pausa as contas do mesmo alvo e credencial em `review` depois de 1 envio sem sucesso; verificador de DM
  com "Sending…" pendente e confirmação manual presa ao print.
- **Fundação do aprendizado (20.2, `c359f65`).** Migração 055, livro de aprendizado com o D1 no domínio e no
  repositório, falha em vocabulário fechado, `GET /api/aprendizado`, `/pendentes` e `/revisar`, e a janela efetiva da
  projeção (o `ai_calls` purgado em 14 dias subestimaria uma janela de 30).
- **Parque (21.8, 21.9).** `hide_error_dialogs` continua 1 depois do experimento no android-17 (com 0, o ANR do
  `system_server` prende o aparelho, K-054); aposentar apaga o AVD mesmo com o `pstore.bin` somente-leitura (`2511b12`,
  K-056).
- Operação (29/09, reversível): SEND_MESSAGE do grupo "Operação" de volta a `approval_required`, com os limites do grupo
  "Recuperação"; o `CREATE_COMMENT` autônomo do andre removido; o android-04, com o felipe no desafio, desligado.
- Prova: `real` no diagnóstico, nas proteções operacionais, no relógio e nos experimentos; `simulated` nos testes de
  cada pacote e na integração (SQLite, PostgreSQL 17, mypy, frontend 660 ok); `not_run`: as provas com efeito em conta
  real (o deploy da integração veio depois, às 03:55Z).
- Documentação e processo: ADR-054 e ADR-055, K-053 a K-057, relatório §22, Fases 20 e 21 do plano-100, adendo v0.37,
  banco (054 e 055).

## 2026-09-28 (noite) — Falhas reiteradas do Instagram: diagnóstico medido, 9 correções e prova real (ADR-053)

**Implantado** no central em 28/09 ~23:40 UTC (`93967d0`; Appium reiniciado pelo procedimento do K-039; agente do
notebook em `0.1.0+93967d0`); em seguida `91f1aab` (só backend): o reinício por interrupção exige carga ≤ vCPU,
para não confundir o boot com a doença. Prova real de navegação (`r-20260928234657-bbdf3c`) e com efeito, curtir e comentar,
autorizada pelo dono (`r-20260928235215-6eb84c`): objetivos `succeeded`, 0 recriação de sessão, 0 ANR novo
([relatório §21](docs/relatorio-validacao.md)).

Origem: o dono reclamou que `r-20260928165254-e31953` e `r-20260928195344-02ee9e` continuavam falhando e que os planos
de melhoria não funcionavam. O diagnóstico mediu para onde foi o tempo (a IA ocupou 4,5% dos 925 s da 02ee9e) e achou
12 causas confirmadas; nenhum dos 38 commits do dia tinha tocado as principais (Fase 19 do plano-100).

- **UI ocupada não é sessão morta (19.1).** O 500 "hogging the main UI thread" do UiAutomator2 vira `DriverBusy`: a
  leitura relê sem recriar a sessão (eram 305,6 s de 925 s em recriações na 02ee9e), e a ação não se repete. Swipe sem
  pausa depois de encostar; scroll em faixa estreita usa a área rolável maior.
- **ANR com sinal próprio (19.2).** `dumpsys activity exit-info` diz quando o app morreu; o foco vem da seção viva do
  `dumpsys window`, não da "LAST ANR" (K-047, K-048). Uma reabertura sem IA por etapa; na segunda morte, falha com o
  motivo. Prazo vencido vira `step_deadline`, não "IA indisponível".
- **Prévia fora da fila do aparelho (19.3).** Com a IA no controle, o painel recebe o frame da observação da IA, com
  prazo de 30 s e o selo "IA sem olhar a tela" (adendo v0.36); `drain` só da etapa; relógio não é acertado com
  objetivo em execução; o aviso de pressão diz CPU ou RAM.
- **Recuperação preserva o estado (19.4).** Não mata o app vivo, retoma da tela atual, não repete o LIKE comprovado e
  mantém `commit_guard` e `bindings`.
- **Digitação atômica (19.5).** O texto é definido de uma vez no campo (`mobile: replaceElementValue`), e o compositor
  do Instagram (`AutoCompleteTextView`) conta como campo; regra nova de mascaramento do log do Appium (reiniciar o
  Appium no deploy).
- **Porta de sessão (19.6), alvo pela legenda (19.7), aprendizado só com prova e orquestrador (19.8).**
  `caption_contains`, `card_guard` e `card_control` no catálogo como dado; contador de sessão vencido relê o aparelho;
  `learn_from_run` só aprende de execução com todas as etapas comprovadas.
- **Reinício a frio por interrupção acumulada (19.9).** Aparelho ocioso com mais de 15% da CPU em irq em 3 sondas
  seguidas recebe um `restart` rastreável (no máximo 1 a cada 6 h), nunca a escada de reparo; medido 21% e 90% nos
  aparelhos com dias no ar, ~2% depois do reinício (K-050).
- Prova: `simulated` (suíte do backend 2848 ok em SQLite, frontend 656 ok, um teste por pacote que falhava antes);
  `real` nas duas execuções acima. `not_run`: relógio do host em NTP, `hide_error_dialogs=0` e as pendências dos
  revisores (ADR-053).
- Documentação: ADR-053, K-047 a K-052, relatório §21, Fase 19 do plano-100, adendo v0.36.

## 2026-09-28 — Fase 18: conhecimento de app como dado e execução medida (branch `claude/app-conhecimento`; ADR-052)

**Implantado** no central em 28/09 ~19:32 UTC (`a7fe364`; agente do notebook em `0.1.0+a7fe364`). Prova real da
sessão pelo motor genérico: lucas e andre confirmados na tela ([relatório §20](docs/relatorio-validacao.md)).

Origem: a execução `r-20260928165254-e31953` (31 chamadas, US$ 0,59, 18,7 min, falhou) e a pergunta do dono sobre
operar o Outlook como o Instagram ([design](docs/design/conhecimento-de-app.md)).

- **Digitação com conferência (18.1).** `type_text` relê o campo, completa só o que faltou (sem duplicar), não aperta
  Enter com texto incompleto e devolve o que de fato entrou (`verified`, `typed_chars` real).
- **Telas como dado, fatia 1 (18.2).** `automation/conhecimento_de_telas.py` lê o `telas.yaml` do app: o Instagram
  classificado de forma idêntica, mais conversa, post, comentários e busca. A checagem de sessão volta ao estado
  conhecido antes de chamar pessoa. Uma catraca impede sinais e ids de voltarem ao Python.
- **Zero Python por app, fatias 2–4 (18.5–18.7).** O Instagram virou uma pasta de dado,
  `backend/app/conhecimento/apps/com.instagram.android/` (`app.yaml`, `telas.yaml`, `sessao.yaml`, `catalogo.yaml`),
  descoberta pelo registro de apps (`integrations/app_declarado/pacote.py`). Saíram `integrations/instagram/`
  (~1.050 linhas) e `planning/catalog/instagram.py`: o login é o motor genérico `SessaoDeclarada`, o catálogo é
  carregado do YAML com `contract_version` conferida, a leitura de tela do rascunho é `LeituraDeclarada`.
  - O app âncora do perfil vem do registro (`ancora_do_perfil`, `pacote_ancora()`), e as mensagens usam o rótulo do
    app. Os links de perfil (`instagram.com/<usuario>`) viraram dado.
  - **`config.yaml`:** o bloco `instagram:` saiu e não é mais aceito (a instalação não sobe e diz para onde foi);
    no lugar, `contas.session_max_age_s` e `contas.sessao.<pacote>`. Nenhuma instalação conhecida tinha o bloco.
  - Catracas: `app/integrations/` só tem o motor genérico; o texto "instagram" no código de `app/` só desce (restam
    exemplos ao modelo e a recusa do bloco antigo; nomes históricos como a tabela `instagram_profiles` e a rota
    `/api/instagram/…` ficam para o 18.9). Prova de ponta a ponta: um cliente de e-mail declarado só em arquivos entra
    no registro com catálogo, leitura e login (`test_pacote_declarado.py`).
- **Projeção e orçamento por ação (18.3).** Mediana e p90 de chamadas, tempo e US$ por (app, ação) no histórico real;
  a projeção sai no evento do plano e em `GET /api/runs/{id}/projection` (adendo v0.35); aviso acima do p90; parada
  acima de `max(p90 × 2, p90 + 4)` (`ai.step_budget`).
- **CI × parque (18.4)**, feito pela sessão Evolução (`0d73f3b`).
- Prova: `simulated` (`test_conhecimento_de_telas.py`, `test_projecao.py`, `test_tools_and_api.py::test_digitacao_*`,
  e os 139 testes de sessão sem mudar asserção). Projeção sobre o histórico real do central para o plano da e31953:
  16–28 chamadas, US$ 0,40–0,74, 3–5 min.

## 2026-09-28 (noite) — Personas em lote e operações em lote; CI cede o central ao parque

- **Personas em lote** (adendo v0.34): "Nova persona a partir de um prompt" com quantidade 1 a 10 — o servidor gera em
  segundo plano, variando as pessoas e sem repetir quem já existe; criar direto ou revisar; custo antes; progresso
  por evento. **Operações em lote** na lista: fotos, completar com IA, grupo, bloquear/reativar e apagar.
- **Variedade do lote** (achado na validação real): plano de variedade por item (setor, idade relativa à faixa
  pedida, religião, política, gênero), sempre abaixo do pedido; irmãs do lote com profissão, cidade e crenças no
  `<evitar>`. Prova real no relatório §17.
- **CI × parque:** o runner próprio roda com prioridade ociosa, espera o parque ficar ocioso (até 20 min) e usa 3
  workers no vitest — o CI no central tinha degradado uma execução real (`r-20260928165254-e31953`).

## 2026-09-28 — `stop.ps1` encerra o Appium órfão deste projeto (K-039) (branch `claude/zen-ptolemy-achwl2`)

- **Operação.** Três deploys seguidos (27 e 28/09) subiram `degraded`, com `appium_log_masking_off` ou
  `appium_down` "readotado", porque o `node` do Appium do backend anterior ficava na porta. O `stop.ps1`, e com ele o
  `deploy.ps1`, agora encerra esse Appium depois que a Farm para de responder. Só o `node.exe` na porta de `appium:`
  do config cuja linha de comando aponta para `tools\appium` desta árvore; outro processo na porta fica, com aviso.
  Há uma carência de 10 s para o backend que ainda está saindo, e a porta é conferida depois do encerramento.
  `stop.ps1 -Simular` mostra o que seria encerrado. A lógica fica em `scripts/lib/appium-do-projeto.ps1`.
- Prova: `simulated`, em `scripts/tests/test_stop_appium_orfao.py` (23 passaram e 1 pulou, no Linux com pwsh 7.4 e
  node 22). Cobre a seleção, a leitura do config, um `node` de verdade encerrado com o de outra árvore poupado, a
  carência e `stop.ps1 -Simular`. `scripts/tests` inteiro deu 173/173 mais 1 pulado, em Python 3.13. `not_run`: o
  teste com `Get-NetTCPConnection` de verdade (só Windows) e o deploy no central.

## 2026-09-28 — Saldo das contas de IA como regra (branch `claude/saldos-ia`; ADR-051)

Pedido do dono de 28/09: acompanhar na plataforma os saldos da Anthropic, da OpenAI e do Google AI Studio, com a IDE
enxergando e os saldos valendo como regra e alerta.

- **Migração 052** (`ai_billing_accounts`, `ai_balance_snapshots`) e `planning/saldos.py`: saldo estimado = última
  leitura do console − gasto de `ai_calls` naquela conta desde ela, com moeda e câmbio (o Gemini cobra em R$).
- **Regra no backend.** Abaixo de `block_below`, o roteador barra a IA da conta antes de gastar (`kind="balance"`,
  disjuntor e pausa como na falta de crédito); o fallback declarado para outra conta atende. A imagem da persona
  confere a conta do gerador. Um erro de cobrança do provedor grava leitura 0.
- **Alertas.** Problemas `ai_balance_*` em `/api/health` (só conta em uso), chips por conta no cabeçalho e o cartão
  "Saldo das contas" em Configuração › IA, com leitura nova, limites e link do console.
- **API.** `GET /api/ai/balances`, `POST|PUT /api/ai/balances/{conta}`; `AiStatus.balances`.
- **Conciliação pelo relatório do provedor.** Com `ANTHROPIC_ADMIN_KEY`/`OPENAI_ADMIN_KEY` no `.env`, o gasto que o
  provedor cobrou fora da plataforma sai do saldo estimado (`external_usd`); "Conciliar agora" no cartão. A Anthropic
  só reporta dias fechados. A OpenAI devolve a falta de crédito como 429 `insufficient_quota`: agora é `billing`.
- **Livro-caixa das contas de IA** (substitui a leitura de tela; a extensão coletora foi construída e removida no
  mesmo dia, a pedido do dono). Saldo = âncora − consumo:
  - Anthropic pelo relatório oficial de uso, de hora em hora;
  - OpenAI pelo relatório oficial de custo;
  - Gemini pelo consumo medido em cada chamada.
  Um laço de 10 min concilia e fecha o dia. A recarga é o único gesto humano (`POST …/{conta}/recharge`, "Registrar
  recarga" no cartão). "Desatualizado" agora é conciliação falhando.
- **Limites definidos** (o dono delegou): bloqueio em US$ 0,50 (R$ 2,50 no Gemini); aviso em US$ 3 na Anthropic,
  US$ 2 na OpenAI e R$ 10 no Gemini. Prova real do bloqueio: 503 `kind: balance` sem chamada ao provedor.
- **Saldo em todo lugar que mostra IA.** Popover "IA em uso" (conta de cada função e os três saldos), Situação e
  "Por função" em Configuração › IA, azulejo "Saldo de IA" no Diagnóstico, US$ por conta no custo da semana e da
  execução (`UsageReport.by_account`, inclui a Google quando o Gemini é usado) e aviso no Comando antes de enviar.
- **Migração 053 — linha de base da conciliação.** O registro da leitura concilia na hora e grava o que o provedor
  e `ai_calls` já tinham; o gasto de fora anterior à leitura não sai duas vezes (medido: 7,96 × 8,25 na OpenAI).

## 2026-09-28 — Fase 17: custo de inferência por provedor e imagem real (branch `claude/ia-custo`; ADR-049)

Pedido do dono de 28/09: o menor custo de IA possível sem perder qualidade. Pesquisa e plano em
[`docs/pesquisa-provedores-ia-2026-09-28.md`](docs/pesquisa-provedores-ia-2026-09-28.md) e
[`docs/plano-provedores-ia-2026-09-28.md`](docs/plano-provedores-ia-2026-09-28.md).

- **IA — chave pelo `.env` (17.1).** `ai.providers.<nome>.api_key_env` passa por `EnvSettings.chave`, que lê o `.env`
  (`OPENAI_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`). Antes só valia
  `os.environ`, e a primeira chamada a um provedor em nuvem voltaria 401.
- **IA — parâmetros por modelo (17.1).** `ai.models.<m>.max_tokens_field` (`max_completion_tokens` na OpenAI) e
  `extra_body` por modelo, aplicado depois do do provedor (`reasoning_effort: none` no `gpt-6-luna`, que sem isso não
  chama ferramenta).
- **Rejulgamento com candidato (17.2).** `scripts/eval_rejudge.py --sobrepor <yaml>` julga as mesmas capturas do Opus
  com o provedor do papel `verify` do arquivo sobreposto (falso positivo e negativo), sem escrever no `config.yaml`.
- **Imagem da persona (17.3).** O padrão passa a `gpt-image-2` (o `gpt-image-1-mini` sai da API em 01/12/2026). O custo
  vem do `usage` da resposta × `ai.image.price_per_mtok`; o `price_per_image` virou estimativa conservadora do teto.
- **Candidatos declarados (17.4).** `config.example.yaml` traz a capacidade e o preço de lista de `gpt-6-luna`,
  `gemini-3.1-flash-lite` e `deepseek-flash`, além do bloco comentado para ligá-los; `.env.example` traz os nomes das
  chaves; `docs/ia.md` ganha a §13.
- **Plano-100:** Fase 17 registrada (17.1–17.9, bloco `17-custo-ia`).
- Prova: `simulated`. Testes: `backend/tests/test_openai_provider.py`, `backend/tests/test_persona_imagens.py` e
  `scripts/tests/test_eval_rejudge.py`, mais mypy estrito em `app.modules`/`app.shared`/`app.contracts`.
- **Implantado e medido em 28/09 (`d6b30fb`, `real`, relatório §18).** A imagem da persona é real no central
  (`gpt-image-2` médio, ~US$ 0,052 por imagem, rosto mantido nas variações). O ator e o verificador **não**
  mudaram: o `gpt-6-luna` fez 12/14 contra 13/14 da base, a 51% do custo por caso correto, com um falso positivo
  de envio (verificador) e um bloqueio falso de conta (ator). Próximo passo: a cascata (17.10). K-045 e K-046.

## 2026-09-28 (tarde) — ambiente central sem "produção", aparelho real criado, CI no runner próprio e crenças ricas da persona (ADR-048) — IMPLANTADO (`1fc4c01`)

- **Ambiente central, não produção** (decisão do dono): `CLAUDE.md`, regras, `operacao.md` e scripts; validar ali é
  permitido; conta real de terceiros com efeito externo, IA paga além do pontual, reset com conta logada e infra do
  host seguem pedindo autorização.
- **Aparelho real pela plataforma:** `android-16` criado, ligado (241 s), parado e aposentado (AVD removido).
- **CI sem pagar:** runner próprio `central` (tarefa `farm-ci-runner`, variável `CI_RUNS_ON`), venv por job, `pwsh`
  como shell (o `bash` do Windows resolvia para o WSL), cancelamento por ref; PostgreSQL na GitHub até a cota nova.
- **Crenças ricas (ADR-048):** religião e política como objetos (biografia v2, normalizada na leitura, sem SQL), no
  bloco `<persona>` com a regra de conduta (sem propaganda, pedido de voto, desinformação ou ataque a grupos), na
  geração por prompt e em dois cartões no painel (barra de espectro neutra). Prova real no relatório de validação §17.
- **Completar com IA com instruções** (adendo v0.32): `POST /personas/{id}/enrich` aceita `{instructions}` e o cartão
  "Completar com IA" fica no topo da guia Persona — o gerar-por-prompt aplicado a quem já existe, só no vazio;
  prova real no relatório §17.
- **A biografia inteira vai ao modelo** (16 campos, orçamento de 350 tokens) e o bloco `<persona>` abre com "o pedido
  manda no QUE fazer; a persona só dá o jeito" (também no `SOCIAL_SYSTEM`); o "mapa da pessoa" na guia Persona é
  da outra sessão. Prova real no relatório §17.
- CI: push só na `main` (branch com PR segue pelo `pull_request`), para o runner próprio não rodar duas vezes cada commit.
- Plano-100: 16.13 (crenças) e 16.9 com a prova real do AVD.

## 2026-09-28 — guia Persona como mapa da pessoa

- **Personas → Persona.** Em vez de formulários empilhados numa coluna: retrato no topo (resumo, idade, onde mora,
  trabalho, vida, religião, política e interesses, cada um levando à sua seção; medidor de seções preenchidas;
  "Completar com IA" na lateral), índice fixo com o estado de cada seção e a marca "vai ao modelo", e seções que abrem
  lendo — Identidade, Origem e casa, Trabalho e Vida em duas colunas, Gostos com "gosta × não gosta", marcos da vida
  como linha do tempo, Crenças em duas colunas largas. "Editar {seção}" abre o formulário só daquela seção (salvar
  continua mandando só ela). Faixas por `@container page` (ADR-046).
- O que vai ao modelo cresceu no mesmo dia (sessão da evolução 2: biografia inteira no bloco `<persona>`, com
  orçamento, e "o pedido manda no que fazer; a persona dá o jeito").
- Prova: `simulated` — `frontend/src/features/profiles/ProfileDetail.test.tsx` (mapa em leitura, editar/cancelar/
  salvar, retrato leva à seção), suíte do painel 636; capturas CDP 1366/1024/375 com persona rica e quase vazia.

## 2026-09-28 — modo Automático: quem faz e onde pelo pedido (ADR-050) — IMPLANTADO em 28/09 (`b0f2c07`)

- **Comando.** "Automático" é o novo padrão: Planejar/Executar mostram "Quem faz e onde" (persona, aderência,
  motivo, aparelho e servidor; descartadas; as que faltam dados, com link para completar) e só a confirmação cria a
  execução. Os três modos manuais ficam em "escolher manualmente".
- **API.** `POST /api/runs/targets/suggest` (adendo v0.33): sem IA quando o texto já diz o destino ou o app não usa
  conta; senão uma chamada do papel `plan`. Propaganda/voto → `alerta_conduta`, sem roteamento.
- Prova: `simulated` — `backend/tests/test_orquestracao.py`, `tests/test_arquitetura.py`,
  `frontend/src/features/command/SugestaoDeAlvos.test.tsx`, suítes inteiras, capturas CDP (1366/375); `real` — uma
  chamada no central (`ai_calls` 2233, ~US$ 0,027), relatório de validação §19.

## 2026-09-28 — assistente do comando: refinar com a IA e responder à execução (ADR-047) — IMPLANTADO em 28/09 (`a71e809`)

- **Comando.** Botão "Refinar com IA": o texto volta em blocos, com as perguntas do que falta (opções clicáveis) e as
  respostas incorporadas a cada rodada, até "Pronto para planejar"; "Usar este comando" o põe no campo.
- **Execução em `needs_input`.** As perguntas do planejador viram campos no próprio banner; responder refina o
  comando e cria a execução sucessora com os mesmos alvos e personas (`POST /api/runs/{id}/successor`), cancelando a
  antiga com o link. Antes era "Editar comando" e reescrever tudo à mão.
- **API.** `POST /api/commands/refine` (papel `plan`, sem execução) e `POST /api/runs/{id}/successor` (adendo v0.30).
- Prova: `simulated` — `backend/tests/test_assistente_do_comando.py`, `tests/test_arquitetura.py`,
  `frontend/src/features/command/AssistenteDoComando.test.tsx`, suítes inteiras e navegador contra backend simulado;
  `real` — uma chamada no central (`ai_calls` 1609, ~US$ 0,038), relatório de validação §16.

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

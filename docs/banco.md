# Banco: SQLite por padrão, PostgreSQL por configuração

O projeto nasceu com SQLite e **isso continua certo para quem roda tudo numa máquina**: arquivo único, zero
serviço. PostgreSQL entra quando os componentes se separam — várias máquinas precisando do mesmo estado.

> Aqui estava escrito que "backup é copiar". Não é, e a frase foi retirada em vez de amenizada: com o banco em WAL
> e o backend no ar, copiar o `.sqlite3` produz um arquivo que abre, passa no `integrity_check` e está velho — ou,
> como foi medido, sem sequer a tabela. Ver **Backup, restauração e deploy**, no fim deste documento.

A escolha é uma variável:

```bash
# SQLite (padrão): nada a configurar
# PostgreSQL:
DATABASE_URL=postgresql://usuario:senha@host:5432/parque
```

É o **primeiro endereço de serviço configurável do projeto** além do Appium. Até aqui não havia nenhum, e isso
sozinho já impedia separar qualquer coisa.

## O que o resto do código não precisa saber

Três decisões mantêm a diferença contida em `app/db.py`:

1. **A linha é um `dict`.** `sqlite3.Row` só era acessada por nome (conferido: índice numérico existia apenas
   dentro do `db.py`), e `dict` tem `.keys()`. Os dois drivers entregam a mesma coisa.
2. **O marcador continua `?`.** Quem escreve SQL usa `?`; a tradução para `%s` acontece num lugar só.
3. **As migrações são os mesmos arquivos.** O que difere vira marca (`{{PK_AUTO}}`, `{{BLOB}}`) ou bloco
   (`-- @dialect:postgres`). Um arquivo por migração, para os dois não divergirem com o tempo.

Erros de driver também são neutros: `INTEGRITY_ERRORS` e `OPERATIONAL_ERRORS` em vez de `sqlite3.IntegrityError`.
Importa porque a **idempotência** do projeto é chave `UNIQUE` + captura da violação — capturar a exceção errada
transformaria "já existe, devolva o original" em erro 500.

## Migrações (001–070)

Cada migração é um arquivo em `backend/migrations/`, aplicado uma vez e nunca editado depois
(`app/db.py::migrate`): quem precisa mudar o que uma migração já aplicada fez cria a PRÓXIMA migração. A tabela
`schema_migrations` guarda `version` + `applied_at` + `checksum` (sha256 do script já RENDERIZADO — depois de
resolver as marcas de dialeto — não do arquivo cru); `Database.migracoes_divergentes` (`db.py:495-517`) relê o
diretório e aponta qualquer migração cujo checksum não bate mais com o arquivo em disco — é o alarme para "isto
foi editado depois de aplicado", checado por assinatura de arquivo (nome+tamanho+mtime) e cacheado entre
chamadas para não custar a cada `/api/health`.

| # | Nome | Tabelas criadas / efeito |
| --- | --- | --- |
| 001 | init | Esquema inicial: `apps`, `instances`, `settings`, `runs`, `objectives`, `plan_versions`, `steps`, `attempts`, `actions`, `events`, `evidence`, `measurements` |
| 002 | appium_session | Sessão Appium aberta por aparelho; após reinício do backend, sessão antiga é descartada |
| 003 | ai_calls_and_action_target | `ai_calls` (uso de IA por chamada — base do relatório de custo) e alvo estável de cada ação |
| 004 | hibernation | Hibernação por snapshot (rodízio de instâncias); snapshot é de uso ÚNICO |
| 005 | flows_and_recipes | `flows` (plano congelado de um comando repetível), `recipes` (ações por seletor aprendidas com IA) |
| 006 | for_each | Repetição sobre listas lidas da tela (`collect` + `for_each`) |
| 007 | app_releases | `app_releases`, `app_release_files`, `device_app_state`, `app_trusted_signers` — release como artefato de primeira classe |
| 008 | instagram_domain | `secrets`, `personas`, `instagram_profiles`, `instagram_credentials`, `device_profile_bindings`, `instagram_sessions`, `authentication_attempts` |
| 009 | social_memory | `social_interactions`, `memory_items` (+ busca textual), `relationship_summaries`, `thread_summaries` |
| 010 | capabilities_approvals | `pending_approvals`; capabilities, políticas e limites |
| 011 | release_lifecycle | `app_release_validations`; ALTER `app_releases` com canário/promoção/quarentena/rollback (aditiva) |
| 012 | draft_context | Rascunho nasce na porta de política; efeito só é registrado no commit |
| 013 | commands | `commands` — comando do painel vira ENTIDADE, com o mesmo rigor das etapas de IA |
| 014 | desired_state | Estado DESEJADO do aparelho, separado do observado |
| 015 | workers | `workers`, `worker_enrollments` |
| 016 | step_ownership | Posse de etapa (dono + prazo do lease) |
| 017 | busca_sem_acento | Busca de memória sem acento no PostgreSQL, igualando o SQLite |
| 018 | um_ativo_por_aparelho | Um aparelho, uma etapa ativa — imposto pelo BANCO, não só pela contagem em `claim_step` |
| 019 | capacidades | Capacidades declaradas do aparelho (o que ele É, não só que verbo aceita) |
| 020 | identidade_fisica | Identidade FÍSICA do aparelho por trás do id lógico (item 3.2; achados #76, #111) |
| 021 | transporte_do_worker | O túnel como componente da plataforma (item 3.5; achado #179) |
| 022 | onde_rodou | ONDE a execução rodou (item 4.1; achado #176) |
| 023 | localidade_do_perfil | ONDE os dados do perfil vivem (item 4.4 / E9; achados #45, #69) |
| 024 | inventario_do_parque | Servidor e aparelho novos sem editar YAML (item 4.5; achados #16, #151, #47, #13) |
| 025 | apps_exigidos_por_fluxo | `flow_required_apps` — quais apps um fluxo precisa (item 6.1 / E10; achado #81) |
| 026 | dono_da_operacao_de_app | Quem é o DONO de uma operação de app em curso (item 6.2; achado #85) |
| 027 | hospedeiro_e_vagas_de_ia | Fase 5 — hospedeiro e papéis (5.1), `ai_slots` = limite de IA global (5.2), guarda de relógio (5.3) |
| 028 | convergencia_da_008 | Converge bancos antigos para o esquema que a 008 gera HOJE (achado #169); sem tabela nova |
| 029 | outbox_e_origem_do_evento | `command_outbox`; origem do evento — fase 5, item 5.6, achado #30 |
| 030 | storage_de_evidencias | Storage de evidências e de APKs (fase 5, item 5.7; achados #172, #89) |
| 031 | catalogo_visual | ALTER `app_releases` (+`label`, `+icon_file`) — catálogo visual (fase 6, item 6.3 / E11; achados #66, #82). **Não cria tabela** |
| 032 | hub_de_ia | Hub de IA (fase 7, itens 7.1/7.2; achados #91, #92, #97). **Não cria tabela** |
| 033 | estados_de_ia | Estados de espera da fila de IA (fase 7, item 7.3; achados #93, #68, #101). **Não cria tabela** |
| 034 | teto_de_reobservacao | Teto de reobservação (fase 8, item 8.3; achado #104). **Não cria tabela** |
| 035 | sessao_do_painel | `panel_sessions` — login, sessão e identidade (fase 9, item 9.1; achados #119, #60) |
| 036 | grupos_de_acesso | `policy_groups`; ALTER `instagram_profiles` (+`policy_group_id`) — grupos de acesso (pedido do dono, 24/09) |
| 037 | contas_por_app | `profile_accounts`, `account_credentials`; ALTER `memory_items`/`social_interactions`/`pending_approvals`/`steps` (+`app_id`), `runs` (+`app_ids`) — item 12.1 (pedido do dono, 24/09) |
| 038 | modo_treinamento | `training_sessions`, `training_inputs`, `flow_scope`; ALTER `flows` (+`source`) — item 13.1 (pedido do dono, 24/09) |
| 039 | limites_por_servidor | `worker_limits`; ALTER `workers` (+`declared_boot_parallelism`, `+declared_min_free_ram_mb`) — item 10.5 (pedido do dono, 24/09) |
| 040 | credenciais_da_execucao | `run_secrets` (nome → referência no cofre, por execução; apagada quando ela termina) — ADR-025 (decisão do dono, 26/09) |
| 041 | loja_de_apps | ALTER `apps` (+`category`); `proxy_profiles`, `device_proxy_state` — loja de apps e proxy do aparelho (pedido do dono, 26/09) |
| 042 | habilidades_versionadas | `skill_definitions`, `skill_versions` (índices parciais: uma publicada por skill, um comando publicado), `skill_version_transitions`, `skill_version_apps`, `skill_scope` — skills versionadas (ADR-034, fase D da evolução arquitetural, 27/09) |
| 043 | casos_de_validacao | `skill_validation_cases`, `skill_validation_results` — casos de validação e observações (`real` × `simulated`) |
| 044 | ensino_v2 | `teaching_sessions`, `teaching_demonstrations` (liga à `training_sessions`; `app_snapshot`), `teaching_turns`, `teaching_candidates` — ensino v2 |
| 045 | trilha_da_habilidade | ALTER `runs`/`steps` (+`skill_id`, `skill_version`, `skill_hash`/`node_id`, `strategy`), `objectives` (+`resource_plan`), `attempts` (+`strategy`, `recipe_id`), `ai_calls` (+`attempt_id`) — todas anuláveis |
| 046 | versao_congelada | gatilhos `skill_versions_congelada`/`skill_versions_sem_apagar` nos dois dialetos: conteúdo de versão fora de `draft` não muda e versão não se apaga |
| 047 | persona_e_a_pessoa | `instagram_profiles` ganha `summary`, `traits` (só voz), `persona_prompt`, `gender`, `locale`, `biography`, `visual`, `generation`; `username` opcional pela convenção `''` (`DEFAULT ''`, índice único parcial `lower(username) WHERE username <> ''`); dobra de `personas` em três grupos (vinculadas, órfãs casadas por nome, órfãs → pessoas `ig-<persona_id>` sem conta), idempotente por `generation.source = 'legacy_persona'`. No SQLite a tabela é **reconstruída** (a produção tem `UNIQUE COLLATE NOCASE` inline da 008 antiga) com a diretiva `-- @foreign_keys:off` ([abaixo](#diretiva-foreign_keysoff-reconstrução-de-tabela-pai-no-sqlite)); no PostgreSQL só `ALTER`. A FK `persona_id → personas` fica; `personas` fica sem leitores — a persona é a pessoa (onda A da segunda evolução, 27/09; ADR-041) |
| 048 | imagens_da_persona | `persona_images` (galeria por pessoa: `spec` = receita, `seed`, `provider`, `status` `pending\|ready\|failed\|refused`, `source` `generated\|upload\|imported_legacy`, `cost_usd`, `is_primary` com índice único parcial `ux_persona_images_primaria`, `ON DELETE CASCADE` de `instagram_profiles`); `ai_calls.usd` (custo declarado por unidade; `NULL` nas linhas de texto). Os 8 avatares legados são importados na partida pelo serviço, não pela migração (onda A; ADR-042) |
| 049 | contas_unificadas | `account_credentials` ganha `status`, `failed_attempts`, `blocked_until`, `created_at`, `last_used_at`, `consent_at`, `consent_by` (consentimento por conta); `profile_accounts.host` e unicidade `ux_profile_accounts_app_host (profile_id, app_id, COALESCE(host, ''))` no lugar de `ux_profile_accounts_app`; **`account_sessions`** com chave `(account_id, instance_id)` e vocabulário único (`unknown\|session_ready\|auth_required\|auth_challenge\|wrong_account\|needs_person`; `logged_out` da 037 vira `auth_required`), `ON DELETE CASCADE` da conta; `authentication_attempts.account_id`. Carga só com o app do Instagram registrado: conta âncora `acc-<perfil>` onde faltava; credencial de `instagram_credentials` com o **mesmo `secret_ref`** (nada recifrado) e `consent_at = updated_at`, `consent_by = 'migração 049'`; sessão de `instagram_sessions` só onde há vínculo ativo com o aparelho; marcação de `profile_accounts.session_status` de app sem provedor vai para a sessão no aparelho vinculado; tentativas apontadas para a conta. Idempotente (`INSERT … SELECT … WHERE NOT EXISTS`). `instagram_credentials`, `instagram_sessions` e `run_secrets` ficam só leitura; `profile_accounts.session_status` fica sem leitor (onda B da segunda evolução, 27/09; ADR-040) |
| 050 | provisionamento | `worker_limits.max_devices` (teto de aparelhos existentes por servidor, NULL = sem teto); `instances.android_overrides` (JSON por instância criada pela plataforma), `instances.retired_at` — provisionamento pela plataforma (onda D da segunda evolução, 27/09) |
| 051 | persona_n_aparelho | `device_profile_bindings` +`app_id TEXT` (NULL = apps sem conta gerenciada) +`is_primary INTEGER NOT NULL DEFAULT 0`; saem `idx_binding_profile_ativo`/`idx_binding_device_ativo`; entram `ux_binding_par_ativo (profile_id, instance_id, (COALESCE(app_id,''))) WHERE active=1`, `ux_binding_conta_do_app_no_aparelho (instance_id, app_id) WHERE active=1 AND app_id IS NOT NULL` (D2-a) e `ux_binding_principal (profile_id) WHERE active=1 AND is_primary=1`; carga: vínculo ativo vira principal e ganha o `app_id` da conta ÚNICA da persona (zero ou várias contas = NULL); `runs.targets TEXT` (JSON: `alvos[] {instance_id, profile_id, app_id, origem}`, `command_sem_destinos`, `device_policy`, `pedido`; NULL nas antigas, que re-resolvem pelo aparelho). Retrato de produção (8 vínculos, 3 ativos): 3 principais, app pela conta única (onda C da segunda evolução, 28/09; ADR-043, ADR-044) |
| 052 | saldos_dos_provedores | `ai_billing_accounts` (conta PK `anthropic`/`openai`/`gemini`, `currency`, `units_per_usd`, `warn_below`, `block_below`, `stale_after_h` (sem uso desde o livro-caixa); sem carga: conta sem linha usa `planning/saldos.py::PADRAO`, aviso US$ 3 Anthropic / US$ 2 OpenAI / R$ 10 Gemini, bloqueio US$ 0,50 / R$ 2,50, câmbio 5,2) e `ai_balance_snapshots` (leitura do console: `balance`, moeda, câmbio, `source` `manual`/`console`/`provider_error`, `observed_at`; índice `(account, observed_at)`). Saldo estimado = última leitura − gasto de `ai_calls` desde ela (ADR-051) |
| 053 | linha_de_base_da_conciliacao | `ai_balance_snapshots` +`provider_baseline_usd REAL` +`local_baseline_usd REAL`: o que o relatório do provedor e `ai_calls` já tinham na janela quando a leitura foi conciliada pela primeira vez (o registro concilia no mesmo instante). Gasto externo = (provedor − base) − (local − base); NULL = ainda não conciliada (ADR-051) |
| 054 | protecao_de_contas | `device_locked_accounts` (marcador, por aparelho, de conta travada logada: `instance_id`, `handle` normalizado, `profile_id`/`app_id` só como referência, sem FK — sobrevive ao desvínculo e à remoção do perfil —, `origin` `observado`/`declarado`/`regra` com CHECK, `seen_by`, `evidence`, `since`, `resolved_at`/`resolved_by`/`resolution`; `ux_locked_account_aberto (instance_id, handle) WHERE resolved_at IS NULL`, `ix_locked_account_instance`); `instagram_profiles` +`blocked_at` +`blocked_evidence` +`blocked_origin` (CHECK; NULOS nos perfis já bloqueados: não se inventa data); `instances.account_label_origin` (NULL = configuração; `vinculo`/`marcador` = derivado pela plataforma). Carga condicional: o marcador do android-04 com o felipe.nogueira93762026, `declarado` pelo dono, desde 2026-09-29T00:36:00.000Z, só onde o aparelho e o perfil existem (banco novo e de teste nascem sem marcador). É do ADR-055 — a numeração se cruza com a 055, do ADR-054 |
| 055 | aprendizado_continuo | `attempts` +`failure_kind` +`failure_screen` e `steps` +`failure_kind` (NULOS no legado, que é classificado na leitura, sem gravar), `ix_attempts_failure_kind`; sete tabelas sem FK e sem CHECK: `learning_items` (tela, lição, voz, preferência; escopo em `TEXT NOT NULL DEFAULT ''`; `ux_learning_items_vivo` PARCIAL nos estados `candidate`/`validated`/`published`), `learning_transitions` (trilha única, inclusive `receita:<id>` e `fluxo:<id>`), `learning_signals` (`ux_learning_signals (kind, source_ref, created_by)`, upsert), `learning_evidence` (`ux_learning_evidence (item_ref, origin_ref, stance)`), `learning_exposures` (PK `(item_id, unit_id, role)`), `learning_daily` (agregado diário durável, gravado antes da purga de `ai_calls`; PK `(day, app_package, capability, failure_kind, driven_by)`) e `learning_backlog` (`cluster_key` UNIQUE). Nada altera `recipes`, `flows` nem `pending_approvals`. Pacote A1 do ADR-054 (item 20.2) |
| 056 | saidas_de_etapa | `step_outputs` (`id` TEXT PK `<objective_id>:<name>`, `run_id`/`objective_id`/`step_id` com FK e `ON DELETE CASCADE` como `steps`, `name`, `value`, `value_kind` `text`/`number`/`url`/`list` com CHECK (padrão `text`), `app_id` NULL, `created_at`; `UNIQUE (objective_id, name)`: a última escrita vence; `ix_step_outputs_step`); `steps.saidas` (JSON dos nomes que a etapa produz; NULL = nenhuma, todo o legado). Nome `^[a-z][a-z0-9_]{0,39}$` e valor até 2000 caracteres conferidos em `Repository.save_step_output`, não em CHECK. Só a forma: quem grava é a Fase 24 (contrato C2, ADR-058) |
| 057 | rede_por_aparelho | `network_profiles` (`id` TEXT PK, `name` UNIQUE, `kind` `vpn`/`proxy` e `protocol` `wireguard`/`singbox`/`http`/`socks5` com CHECK, `endpoint_host`, `endpoint_port` 1–65535, `secret_ref` NULL (só a referência do cofre; o DTO devolve `has_secret`), `params` JSON sem segredo, `created_at`, `created_by`); `device_network` (`instance_id` PK sem FK, `vpn_profile_id`/`proxy_profile_id` REFERENCES `network_profiles` sem `ON DELETE`: perfil em uso não se apaga, `policy` `livre`/`exigida`/`exigida_com_bloqueio` padrão `livre`, `desired_rev` padrão 0, `applied_rev` NULL, `state` `pendente`/`configurado`/`conectado`/`trafego_verificado`/`parcial` padrão `pendente`, `detail`, `error`, `egress_ipv4`, `egress_ipv6`, `verified_at`, `updated_at`, `updated_by`); `network_measurements` (`{{PK_AUTO}}`, `instance_id`, `measured_at`, `method`, saídas, `dns_resolver`, `udp_ok`/`leak_blocked` 0/1/NULL, `per_app` JSON, `detail`; `ix_network_measurements_instance`). As tabelas da 041 ficam. Só a forma: aplicar e medir é da Fase 25 (contrato C3, ADR-056) |
| 058 | chaves_de_rede | `network_keys` (`owner` PK: `servidor` ou o `instance_id`, sem FK; `kind` `servidor`/`aparelho` com CHECK; `secret_ref` da chave PRIVADA no cofre, lida só pelo consumidor restrito de rede; `public_key`; `address` UNIQUE, NULL no servidor; `created_at`). Chaves WireGuard geradas pela plataforma para o servidor do central e para cada aparelho que usa um perfil servido por ele (25.4) |
| 063 | prova_de_vazamento | `device_network` +`leak_rev` +`leak_client` +`leak_result` (0/1/NULL com CHECK) +`leak_at` +`leak_detail` +`leak_pending` (`NOT NULL DEFAULT 0`, CHECK 0/1). A prova do teste de vazamento da política `exigida_com_bloqueio`, que vivia só na memória do backend, passa a morar na linha: vale quando `leak_rev = desired_rev`, `leak_client` é a instalação do cliente VPN lida no aparelho e `leak_result = 1`; `leak_pending = 1` é a intenção do ensaio, gravada antes de parar o cliente. Só colunas: a migração não inventa prova (item 29.2, ADR-061). **Números 059 a 062:** nomeados nas linhas 28.2, 28.7 e 28.10 do plano (a 28.1 virou a 066). O executor aplica todo arquivo que não está em `schema_migrations`, em ordem de nome, então a 059 que nascer depois entra num banco que já tem a 063 (`tests/test_db.py::test_migracao_de_numero_menor_criada_depois_ainda_e_aplicada`) |
| 064 | perfil_de_ia | `runs` +`ai_profile` (nome em `ai.profiles`; NULL = funções padrão) +`ai_profile_source` (`explicit`/`canary`/NULL com CHECK). Item 17.7: o perfil de IA da execução e de onde veio (pedido ou sorteio do canário), para o A/B juntar `ai_calls` por `run_id` |
| 066 | travas | Tabela nova `travas(nome PK, dono, token NOT NULL DEFAULT 0 CHECK >= 0, expira_em, tomada_em)`: trava de líder dos laços periódicos (item 28.1; design `pedidos-persistentes.md` §7.3). Tomada por CAS no relógio do banco (`WHERE dono IS NULL OR expira_em < agora`), prazo de 120 s renovado a cada 20 s, `token` crescente a cada mandato (cerca: a escrita cercada confere o token na mesma transação). Linhas nascem sob demanda e nunca são apagadas (apagar zeraria o token). Aplicada a saldos, curadoria e retenção (`state.py`; `app/taskqueue/travas.py`). Número 066 e não 059: a main já tinha 063 e 064; 066 foi o número indicado pela coordenação da Fase 28 (em 02/10 não havia 065 em nenhum branch); o executor aplica por nome e tolera o buraco |
| 067 | pedidos | Modelo do pedido persistente (item 28.2, Fase 28; só a FORMA, quem lê e escreve são o laço 28.4, a recorrência 28.3 e a API 28.9). `pedidos` (`id` TEXT PK; `titulo`, `objetivo`, `contexto`, `criterios_sucesso` e `alvos` em JSON; `autonomia` `observar`/`preparar`/`agir` com CHECK e padrão `observar`; `fuso` IANA; `inicio_em`/`fim_em`/`proxima_em` UTC; limites `max_ocorrencias`, `orcamento_total_usd`, `orcamento_ocorrencia_usd`, `janela_recuperacao_s`, `max_tentativas` (2), `pausa_por_falha` (3) com CHECK; `sobreposicao` `pular`/`guardar_uma`/`permitir_todas`; `coalescer`; `estado` `rascunho`/`ativo`/`pausado`/`aguardando_pessoa`/`concluido`/`encerrado`/`cancelado` com CHECK; `versao`; `pausado_motivo`, `encerrado_motivo`, `pai_id` sem FK, `criado_por`; índice `(estado, proxima_em)`). `pedido_gatilhos` (`pedido_id` FK `ON DELETE CASCADE`; `tipo` `agora`/`horario`/`recorrencia`/`evento`/`condicao`/`persona` com CHECK; `spec` JSON que a migração não interpreta; `cursor`; `ativo` 0/1). `pedido_ocorrencias` (`pedido_id` FK `CASCADE`; `gatilho_id` FK SEM cascata e NULL no gesto manual/backfill; `previsto_para`; `chave` UNIQUE e `UNIQUE (pedido_id, gatilho_id, previsto_para)`; `origem` e `estado` com CHECK; `tentativa`; `run_id` sem FK; `motivo`; `custo_usd`; `materializada_token`; `dono`/`prazo_posse`; índices por pedido e por estado). `runs` +`pedido_id` +`ocorrencia_id` (sem FK e `ocorrencia_id` não é UNIQUE: as tentativas `:t1`, `:t2` são execuções diferentes; a ocorrência sobrevive à purga da execução) +`prioridade` (`INTEGER NOT NULL DEFAULT 0`; maior passa na frente, valores e uso no 28.6) e índice `ix_runs_pedido`. Nenhuma linha de `runs` é tocada. Domínio puro em `app/modules/pedidos/domain/` (estados, transições, chave determinística), conferido contra o CHECK em `tests/test_pedidos_modelo.py`. **Números 065 e 066:** da trava de líder (28.1), em paralelo noutro branch; o 067 foi tomado porque o plano falava em 060, que a main já ultrapassou |
| 068 | avisos_entregas | Tabela nova `avisos_entregas`: a fila durável do aviso fora do painel (item 28.11; Telegram, só saída; desenho `pedidos-persistentes.md` §11). `id` (`{{PK_AUTO}}`), `chave` TEXT NOT NULL **UNIQUE** (identidade do FATO: `approval:<id>`, `run:<id>:needs_input`, `evento:<id>`, `pedido-aviso:<id>`; o mesmo fato visto por duas réplicas é uma linha só, por `INSERT … ON CONFLICT DO NOTHING`), `tipo`, `titulo`, `corpo`, `link` (o texto que sai: tipo do evento e o link da caixa, nunca nome de persona, conta nem conteúdo), `canal` (`telegram`), `estado` `pendente`/`enviando`/`enviado`/`falhou`/`incerto`/`descartado` com CHECK, `tentativas` (CHECK >= 0), `proximo_envio_em` (adia a nova tentativa e o `Retry-After` do 429), `iniciado_em`, `enviado_em`, `ultimo_erro` (curto, redigido: nunca o token nem a URL do bot), `criado_em`; índice `(estado, proximo_envio_em)`. `enviando` é gravado ANTES da chamada de rede e dentro da transação cercada pelo token da trava `avisos` (066): um processo que cai no meio deixa `enviando`, que NUNCA é reenviado — vira `incerto`. Só o líder envia. **Número 068 confirmado pela coordenação (02/10)** |
| 069 | revisoes_do_aprendizado | Tabela nova `learning_reviews` (item 30.9, Fase 30; `aprendizado-vivo.md` §8.5; número PROVISÓRIO, a coordenação confirma antes do merge): trilha auditável das revisões do curador por IA, uma linha por revisão. `id` TEXT PK `lr-<token>`; o item (`item_ref`, `item_kind`, `scope_app` `NOT NULL DEFAULT ''`), o `gatilho` e o dossiê (`dossie_hash`, `dossie` JSON sem segredo e sem texto de tela); a versão do prompt (`template_id`, `template_versao`, nunca o texto); quem respondeu (`provedor`, `modelo`, `simulated` 0/1); o custo (`input_tokens`, `output_tokens`, `usd` REAL como `learning_daily.usd`, `ms`); o parecer (`saida` JSON, `validade` `ok`/`invalida:<motivo>`/`recusada:<motivo>`, `classe_de_risco`, `politica`); o desfecho (`decisao_final`, `decidido_por`, `transicao_id` → `learning_transitions.id`, `override`, `override_motivo`) e o que veio depois (`resultado_posterior`, `resultado_em`, após 14 e 30 dias). Padrão da 055: sem FK e sem CHECK nos vocabulários. `ux_learning_reviews_item_dossie (item_ref, dossie_hash)` UNIQUE é a salvaguarda do orçamento (§8.7: um item com o mesmo dossiê não é revisado duas vezes); `ix_learning_reviews_criada (created_at)` e `ix_learning_reviews_app (scope_app, created_at)` servem à janela móvel do orçamento e à visão por app. NUNCA purgada: a retenção do aprendizado apaga por lista explícita de tabelas e esta não está nela (`ai_calls` é purgada, por isso a auditoria mora aqui). Só a forma: o curador que a grava é 30.10/30.11. Simulado no SQLite (`test_migracao_069_revisoes.py`); PostgreSQL pendente (P17, 29.14) |
| 070 | pedidos_memoria | Memória, observações e relatório do pedido persistente (item 28.7, Fase 28). Três tabelas novas, só a forma e o que o 28.7 escreve; nada de `runs`, `pedidos` ou `pedido_ocorrencias` é tocado. `pedido_memoria` (`pedido_id` FK `CASCADE`; `chave` até 64 caracteres e `tipo` `progresso`/`descoberta`/`decisao`/`pendencia`/`fonte` com CHECK; `valor` texto curto, teto 2000 no domínio e recusa de segredo por formato; `versao` (CHECK >= 1) que sobe só quando o valor muda; `resolvida` 0/1 para pendência; `UNIQUE (pedido_id, chave)`). `pedido_observacoes` (`pedido_id` FK `CASCADE`; `ocorrencia_id`, `run_id` e `step_id` SEM chave estrangeira de propósito: a observação sobrevive à purga da execução; `alvo`, `nome`, `tipo` `text`/`number`/`url`/`list`/`resultado` e `situacao` `observado`/`incerto`/`ausente` com CHECK; `valor` NULL = ausente; `fonte`, `trecho`, `sha256` da captura, `capturado_em`; `UNIQUE (ocorrencia_id, alvo, nome)` para o fechamento ser reentrante; índice `(pedido_id, capturado_em)`). `pedido_relatorios` (`pedido_id` FK `CASCADE`; `sequencia` por pedido, `UNIQUE (pedido_id, sequencia)`; `gatilho` `sob_demanda`/`periodo`/`encerramento` com CHECK e índice único PARCIAL `WHERE gatilho = 'encerramento'`: o relatório final é um por pedido; `periodo_de`/`periodo_ate`; `conteudo` JSON determinístico e `sha256` dele; `gerado_por` (`deterministico`), `resumo_texto`/`resumo_por`/`custo_usd` (CHECK >= 0) só do resumo de IA opcional, desligado de fábrica; `gerado_em` na linha, nunca no conteúdo). **Número 070:** reservado pela coordenação para o 28.7 (o plano dizia 061); a 069 é da frente do aprendizado e entra na main antes: lacuna de número é tolerada pelo executor, que aplica em ordem de nome |

As oito tabelas novas de 031–039 estão em quatro migrações: `panel_sessions` (035), `policy_groups` (036),
`profile_accounts` e `account_credentials` (037), `training_sessions`, `training_inputs` e `flow_scope` (038),
`worker_limits` (039). As migrações 031–034 são só `ALTER TABLE` — nenhuma cria tabela.

### Diretiva `@foreign_keys:off` (reconstrução de tabela-pai no SQLite)

Uma migração que **reconstrói** uma tabela no SQLite pelo molde da 010 (`<tabela>_novo` + `INSERT … SELECT` +
`DROP TABLE` + `RENAME`) e que é **pai** de tabelas com `ON DELETE CASCADE` põe `-- @foreign_keys:off` numa linha
própria no topo do arquivo (`app/db.py::_SEM_CHAVES`; primeiro uso: `047_persona_e_a_pessoa.sql`). Medido antes da
047: com `PRAGMA foreign_keys=ON`, o `DROP TABLE` da tabela antiga faz um `DELETE` implícito que dispara a cascata
das filhas (credenciais, sessões, vínculos e memória sumiriam), e `PRAGMA foreign_keys=OFF` **dentro** de uma
transação é operação sem efeito.

O que o migrador faz com a marca (`Database.migrate` + `_sem_chaves_estrangeiras`), o procedimento dos doze passos
da documentação do SQLite:

1. `PRAGMA foreign_keys=OFF` **antes** do `BEGIN`;
2. executa as instruções da migração na transação;
3. `PRAGMA foreign_key_check` **dentro** da transação, antes do `COMMIT`: qualquer linha órfã levanta
   `sqlite3.IntegrityError` e desfaz a migração inteira, em vez de virar dado quebrado com o esquema novo;
4. `PRAGMA foreign_keys=ON` no `finally`, mesmo se a migração falhou — a conexão não fica sem chave estrangeira pelo
   resto do processo.

Quando usar: só quando a migração reconstrói uma tabela referenciada. Uma migração só de `ALTER`/`CREATE` não a
declara. No PostgreSQL a marca é ignorada (lá `ALTER COLUMN` resolve sem reconstruir). Teste:
`backend/tests/test_persona_migracao_047.py::test_as_chaves_estrangeiras_sobrevivem_a_reconstrucao` e
`::test_a_047_sobre_o_esquema_real_da_producao_aceita_pessoas_sem_conta` (`simulated`); ensaio `real` da 047 numa cópia
do backup de produção `20260927-222357` em 27/09, filhas byte a byte iguais e `foreign_key_check` vazio
([persona](dominios/persona.md#migração-de-dados-047)).

## A cobertura que parecia existir

Vale contar porque é a lição mais cara desta etapa. A suíte passou a rodar contra PostgreSQL com `TEST_DATABASE_URL`,
e o número deu **381 nos dois bancos** — só que **12 pontos em 8 arquivos** abriam `Database(cfg.db_path)`, o
arquivo SQLite, **ignorando a variável**. Aqueles testes rodavam em SQLite *dentro* da corrida do PostgreSQL. O
número era verdadeiro e a conclusão que ele sugeria, não.

Corrigido (`cfg.db_dsn` em vez de `cfg.db_path`), a mesma suíte encontrou **cinco defeitos reais** que o PostgreSQL
tinha e ninguém via:

| Defeito | Por que passava desapercebido |
|---|---|
| `MAX(importance, ?)` — `MAX` escalar de 2 argumentos é do SQLite | erro barulhento, mas só em quem nunca rodava |
| Busca ignorava acento no SQLite e **não** no PostgreSQL | a busca não falhava: só não encontrava |
| `"a" OR "b"` (sintaxe do FTS5) chegava ao `plainto_tsquery` | virava E com a palavra literal "or": nunca casava |
| Normalização do `rank` escrita para `bm25()` (negativo, menor é melhor) aplicada ao `ts_rank` (positivo, maior é melhor) | dividia pelo PIOR e o corte em 1.0 achatava tudo |
| `busca @@ q1 \|\| q2` sem parêntese | `ProgrammingError` **engolido** pelo `except` da busca |

Os quatro primeiros têm a mesma assinatura e é ela que assusta: **nenhum deles dava erro**. A busca devolvia lista
vazia, quem chamou caía no caminho alternativo, e a resposta vinha errada com cara de certa. Só o quinto era um erro
de verdade — e estava sendo capturado por um `except` largo, que agora só vale no SQLite, onde a tolerância tem
motivo (lá o texto da tela É a expressão do índice; no PostgreSQL ele é parâmetro e não pode formar sintaxe).

## Onde os bancos divergem de verdade

**Busca textual**, e só ela. SQLite tem FTS5 com `bm25()`; PostgreSQL tem `tsvector` com `ts_rank`. Fingir que é
a mesma coisa custaria mais que admitir:

| | SQLite | PostgreSQL |
|---|---|---|
| Índice | tabela virtual `memory_fts` + 3 gatilhos | coluna **gerada** `busca` + índice GIN |
| Consulta | `memory_fts MATCH ?` … `ORDER BY bm25()` | `busca @@ plainto_tsquery(?)` … `ORDER BY ts_rank DESC` |

No PostgreSQL a coluna gerada dispensa os gatilhos: o banco a mantém sozinho, e some a classe inteira de defeito
"o índice ficou fora de sincronia com a tabela". O ramo fica em `social/repository.search_memories` — um `if`
honesto, no único ponto onde a diferença existe.

**`REAL` é precisão simples no PostgreSQL** e dupla no SQLite. `memory_items.importance` e `confidence` são
`REAL` nos dois, então `0.7` gravado no SQLite volta do PostgreSQL como `0.699999988079071`. É perda de precisão
do ESQUEMA, não da travessia, e não muda decisão nenhuma (os dois valores são pesos entre 0 e 1). Fica dito
porque a conferência da ferramenta de migração de dados precisou aprendê-lo: ela estreita os dois lados ao mesmo
`float32` antes de comparar, senão gritaria DIVERGE numa cópia perfeitamente boa.

**`ORDER BY` numa coluna `TEXT` segue a colação do banco.** O SQLite (`BINARY`) ordena por ponto de código, como
o `sorted()` do Python; o PostgreSQL do CI (`postgres:17`, `en_US.utf8`) compara sem caixa na primeira passada e
ignora `-`/`_`, então `sec-c…` vem antes de `sec-V…`. Lista que sai para relatório, resposta ou teste e precisa da
mesma ordem nos dois bancos é ordenada em Python (`rekey.recifrar`, `SecretStore.chaves_estranhas`); `ORDER BY`
em texto só onde a ordem não é contrato. Ver K-030 em `docs/conhecimento/aprendizados.md`.

## Construções que foram trocadas por portáteis

Não por preciosismo: cada uma quebraria no PostgreSQL.

| Era | Virou | Por quê |
|---|---|---|
| `cursor.lastrowid` | `db.inserted_id(...)` com `RETURNING` | `lastrowid` é do SQLite; `RETURNING` vale nos dois |
| `SUM(status='succeeded')` | `SUM(CASE WHEN … THEN 1 ELSE 0 END)` | comparação devolve boolean, e `SUM` recusa boolean |
| `MAX(attempts-1, 0)` | `CASE WHEN … END` | `MAX` escalar de 2 argumentos é do SQLite; `GREATEST` é do padrão |
| `MAX(importance, ?)` | `CASE WHEN … END` | o mesmo, e **escapou ao inventário** — ver *A cobertura que parecia existir* |
| `PRAGMA table_info(t)` | `db.columns(t)` | `PRAGMA` é do SQLite e nem aceita marcador de parâmetro |
| `IS NOT 'approval'` | `IS DISTINCT FROM 'approval'` | `IS NOT <literal>` é extensão do SQLite |
| `datetime('now', ?)` | corte calculado em Python | as colunas guardam ISO-8601, que ordena lexicograficamente |
| `UNIQUE COLLATE NOCASE` | índice único sobre `lower(...)` | `NOCASE` não existe no PostgreSQL |
| `executescript` | divisor de instruções próprio | é do SQLite — e o divisor precisa entender literal e corpo de gatilho |

## Savepoint e transação abortada (29/09, Fase 22)

- **A armadilha.** No PostgreSQL, um erro dentro de `tx()` aborta a transação: as instruções seguintes são recusadas
  (`InFailedSqlTransaction`), e o COMMIT final vira ROLLBACK sem erro. Quem engole um erro de SQL dentro da transação
  de outro (o ouvinte do A5 na trilha das lojas, por exemplo) fazia a loja devolver o id de uma linha que não
  existia. A suíte em SQLite não percebe (K-061).
- **`Database.savepoint()`.** Um sub-bloco que falha sozinho: `SAVEPOINT`, `ROLLBACK TO` e `RELEASE`, com nomes
  `sp_<profundidade>`. É reentrante e, fora de transação, vira um `tx()` comum. Quem engole erro dentro de uma
  transação alheia põe o savepoint DENTRO do `try`, em volta de tudo o que tocou. Desfeito com sucesso, o savepoint
  devolve o `_suspeita` da entrada, para um deadlock lá dentro não reabrir a conexão à toa.
- **`TransacaoAbortada`.** O `tx()` de fora confere `info.transaction_status == INERROR` antes do COMMIT, faz
  ROLLBACK e levanta: o esquecimento vira 500, em vez de perda calada. A classe fica fora de `OPERATIONAL_ERRORS` e
  `INTEGRITY_ERRORS`. O `sqlite3` não tem `info`, então a conferência só age no PostgreSQL e na imitação
  `tests/aborto_do_postgres.py` (`embrulhar(db)`), que reproduz a regra sobre o SQLite.
- **Candidatos conhecidos a conferir no PostgreSQL** (engolem erro dentro de transação):
  - `commands/outbox.py` (`enqueue`, `INTEGRITY_ERRORS` no despacho);
  - `social/repository.py` (`marcar_conta_travada`);
  - `modules/learning/infrastructure/validacao_de_skills.py` (`add_case`).

## Rodar a suíte contra o PostgreSQL

Prova o aplicativo **inteiro** no outro banco, não só as peças conferidas à mão. Cada teste ganha um schema
próprio — isolamento equivalente ao arquivo temporário do SQLite, e barato.

```bash
docker run -d --name farm-pg -e POSTGRES_PASSWORD=teste -e POSTGRES_DB=farm -p 55433:5432 postgres:17-alpine
```

No PowerShell, que é o console deste projeto:

```bash
cd backend; $env:TEST_DATABASE_URL = "postgresql://postgres:teste@127.0.0.1:55433/farm"; .venv\Scripts\python.exe -m pytest -q
```

Sem a variável, a suíte roda em SQLite como sempre. Para voltar: `Remove-Item Env:TEST_DATABASE_URL`.

**O que ainda ficava de fora, e não fica mais.** Três arquivos abriam `Database(cfg.db_path)` — o arquivo SQLite —
mesmo dentro da corrida do PostgreSQL, por causa de UMA asserção que lê os bytes do arquivo. Eram 43 funções de
teste: autenticação do Instagram, perfis/vínculo e o cofre. Entre elas, o único chamador de `get_secret`, ou seja:
**decifrar nonce/ciphertext lidos de colunas `BYTEA` pelo psycopg nunca tinha rodado** (a escrita rodava,
indiretamente, por testes que já seguiam `db_dsn`). A asserção byte a byte mora agora num teste próprio, que se
declara fora da corrida do outro banco; a varredura de todas as tabelas — que vale nos dois — usa `db.tables()`
em vez de `sqlite_master`.

O CI (`.github/workflows/ci.yml`) roda esta corrida num container `postgres:17` descartável, agendada e sob
`workflow_dispatch` — não em todo push, pelo custo. `conftest.pytest_sessionfinish` apaga, no fim da sessão, cada
schema que ela mesma criou (`DROP SCHEMA ... CASCADE`); sem isso o catálogo só cresce — medidos 1608 schemas e
1,9 GB acumulados num único banco de desenvolvimento (achado #163) sem nenhum `DROP SCHEMA` no código.

## Dois backends no mesmo banco: o que já foi feito

> **Histórico.** Esta seção chegou a se chamar "o que foi preciso para isso ser seguro", depois "e por que ainda
> NÃO é seguro". O que existia então era a posse da **etapa**; tudo acima dela supunha um processo único, e o que
> um backend fazia com o objetivo de um aparelho que ele não hospeda era destrutivo — bloqueava (`waiting_user`) o
> objetivo de instância que não conhecia, marcava "aparelho offline" ao iniciar execução e, com o rodízio ligado,
> criaria no próprio disco um AVD vazio com o mesmo id lógico. A prova em processo real descrita abaixo
> **mostrou isso**: o backend que adotou a etapa logo em seguida bloqueou o objetivo com "Instância não existe na
> configuração atual" (achado #171).
>
> **A fase 5 fechou isso** (migração 027): `instances.hosted_by` diz qual backend hospeda cada aparelho. Despacho,
> rodízio e as reconciliações de partida (etapas, comandos, instalações, efeitos sociais e planejamento) atuam só
> no que este backend hospeda — objetivo alheio é **ignorado, nunca bloqueado**. O limite de chamadas de IA virou
> lease no banco, e a guarda de relógio deixou de ser prosa. O que continua não compartilhado está em *Pendências
> honestas*, abaixo: **workers e controle manual vivem na memória de um processo**, então cada backend precisa dos
> seus próprios aparelhos e workers.

### Quem hospeda o quê (`instances.hosted_by`)

`hosted_by` é o `OWNER_ID` do backend que tem o emulador (ou o túnel) daquele aparelho — não confundir com
`objectives.hosted_by` (migração 022), que é a fotografia de quem **despachou** aquele objetivo. O carimbo é feito
por quem hospeda, no `seed()`, e **nunca por cima de outro dono**: um backend que lista o mesmo id na configuração
dele encontra a linha já reivindicada, escreve no log e ignora o aparelho. `NULL` continua valendo como "meu" —
é o estado de um banco anterior à 027, e com um backend só nada muda.

### Papéis (`ROLE`)

| `ROLE` | Appium, aparelhos, worker local, scheduler, reconciliações | API REST + frontend | canal do worker |
|---|---|---|---|
| `all` (padrão) | sim | sim | sim |
| `scheduler` | sim | **não** | sim |
| `api` | **não** | sim | sim |

`api` não carimba `hosted_by` de propósito: carimbar roubaria os aparelhos do scheduler da mesma máquina, e
depois ninguém os ligaria. **A consequência, dita para ninguém se surpreender no deploy:** como o hospedeiro já
carimbou, o nó `api` carrega **zero** aparelho — o painel dele não lista aparelho nenhum, e `POST /api/runs`
responde `unknown_instance`. Ele serve para ler execuções, histórico e saúde, não para operar o parque. Um painel
de operação servido por réplica separada depende de tirar workers e controle manual da memória do processo
(achado #27, em *Pendências honestas*). Pelo mesmo motivo o nó `api` não roda `_manter_posse`: ele não renova
etapa nem vaga de IA, porque não abre nenhuma das duas.

### O banco atravessa; os ARQUIVOS, não (evidências, avatares e APKs)

Dois backends no mesmo PostgreSQL compartilham **o banco**. Eles não compartilham disco, e três pastas guardam
arquivos que as linhas do banco apontam:

| Pasta | A linha que aponta | O que acontecia com dois backends |
|---|---|---|
| `data/evidence` | `evidence.path` | evidência de A respondia 404 em B, e a retenção de B apagava a linha |
| `data/avatars` | `instagram_profiles` (arquivo por id) | avatar de A não aparecia no portal servido por B |
| `apks/` | `app_releases.catalog_dir` | release `installable` que falha ao instalar, virando `install_failed` por aparelho |

**A solução é o storage compartilhado** (`EVIDENCE_STORAGE=s3`): com ele as três pastas passam a viver num
bucket S3-compatível, e qualquer réplica lê o que qualquer outra gravou. Ver `docs/evidencias.md`.

**Enquanto ele não estiver ligado — que é o estado de hoje —, valem duas regras:**

1. **`apks/` precisa acompanhar o banco.** Num backup, numa restauração em outra máquina ou ao mover a API de
   servidor, copiar só o banco deixa `app_releases` cheio de linhas apontando para arquivos que não existem
   ali. O sintoma não é um erro claro de configuração: é `install_failed` por aparelho, que só uma pessoa
   rearma — e rearmar no mesmo backend falha de novo.
2. **A retenção de evidências é do dono.** Desde a migração 030, `evidence.stored_by` guarda quem gravou, e uma
   réplica não apaga do banco a linha de um arquivo que está no disco da outra. Sem essa coluna (linhas
   anteriores), a linha conta como local.

A mensagem de instalação distingue os dois casos que antes se confundiam: *"Arquivo ausente NESTE servidor"* (a
release continua íntegra e `installable`, o arquivo é que está em outra máquina) e *"Artefato adulterado"* (o
hash mudou no disco, e aí a release vira `invalid`).

### Limite de IA: lease no banco (`ai_slots`)

`max_ai_concurrency` deixou de ser um semáforo deste processo. Cada vaga é uma linha de `ai_slots`, tomada por
compare-and-swap (`WHERE slot=? AND (holder IS NULL OR expires_at < ?)`) e renovada no mesmo `_manter_posse` das
etapas; vaga de backend que caiu vence sozinha. O semáforo local continua existindo como a fila justa deste
processo — quem decide o teto é o banco. **`boot_parallelism` segue por processo, e isso está certo**: o recurso
que ele protege é a RAM desta máquina.

Trocar de banco não basta. Havia um defeito que com um processo só nunca doeu: `interrupted_steps` pegava **toda**
etapa `running`, sem perguntar de quem era. Com dois backends, o segundo a subir devolveria para `ready` as etapas
que o primeiro estava executando **naquele instante** — e o primeiro perderia o trabalho sem saber.

A etapa passou a ter dono (`claimed_by`, `claim_expires_at`), e o dono é a **máquina** (`OWNER_ID`, por omissão o
hostname), não o processo. A escolha tem uma razão concreta: com identidade por PID, um backend reiniciado não
reconheceria as próprias etapas interrompidas e teria de **esperar o lease vencer** para retomá-las — trocaria
reconciliação imediata, que já era provada, por espera. Com identidade por máquina, o reinício retoma o que é dele
na hora, e o backend de outra máquina continua impedido de mexer.

São dois caminhos distintos, com provas distintas de que o dono morreu:

| | O que prova a morte | Quem reconcilia |
|---|---|---|
| Reinício do próprio backend | o processo reiniciou | ele mesmo, na hora (`interrupted_steps`) |
| Backend de outra máquina caiu | parou de renovar por mais de 120 s | quem estiver vivo, depois de **adotar** a etapa |

O lease é longo (120 s, renovado a cada 20 s) de propósito: o preço de demorar a retomar o trabalho de um backend
morto é baixo; o preço de adotar cedo demais é **dois backends operando o mesmo aparelho**. Renovar é uma escrita
por backend a cada 20 s, não por etapa.

### Provado num backend de verdade, não só em teste

Os testes provam a regra; isto prova que ela vale num processo real, contra um PostgreSQL real. O roteiro, em
21/09: duas etapas `running` semeadas no banco como se um backend `alpha` as estivesse executando — uma com o lease
**vencido** (alpha morreu) e outra com o lease **válido** (alpha está vivo) — e então um backend novo sobe com
`OWNER_ID=beta-que-subiu` apontando para o mesmo banco.

Em ~40 s, sozinho:

| Etapa | Antes | Depois |
|---|---|---|
| lease vencido | `running`, dono `alpha-que-morreu`, 1 tentativa | `ready`, dono `beta-que-subiu`, **0 tentativas** |
| lease válido | `running`, dono `alpha-que-esta-vivo`, 1 tentativa | **intacta** |

A tentativa interrompida ficou `interrupted` com a causa (*"o backend que executava esta etapa parou de
responder"*) e a instrução de recuperação, e a tentativa foi **devolvida** — interrupção sem culpa da etapa não
consome tentativa. O painel registrou *"1 etapa(s) abandonada(s) por outro servidor foram adotadas e serão
reconciliadas pela tela"*, e a execução terminou dizendo *"0 de 1 com sucesso comprovado"*: nada foi chamado de
sucesso.

O que este roteiro **não** prova, e por isso está dito: os dois backends não estavam vivos ao mesmo tempo — o
primeiro foi representado pelo estado que ele teria deixado no banco. A disputa entre dois vivos pela mesma etapa
abandonada está provada em teste (compare-and-swap, três donos), não em campo. E a instância usada
(`android-prova-posse`) não existe na configuração, de propósito: assim o parque real ficou intocado, e o que se
observou foi a adoção, que acontece antes e independentemente do despacho.

**Relógio: era pré-requisito em prosa, virou guarda.** O vencimento era gravado com o relógio de quem assumiu a
etapa e comparado com o relógio de quem pergunta; um backend adiantado alguns minutos veria todo lease vivo como
vencido e adotaria etapas em plena execução — o oposto do que o lease existe para fazer, e os relógios das duas
máquinas do parque estavam ~97 s fora no dia da auditoria (#142). O que passou a valer (achado #32):

- o vencimento é escrito e lido pelo relógio do **banco** (`Database.agora`, `clock_timestamp()` no PostgreSQL),
  então os dois backends comparam contra a mesma fonte e o NTP sai da lista de pré-requisitos;
- na partida o backend mede o desvio contra o banco. Acima de `MAX_CLOCK_SKEW_S` (30 s por omissão) ele **recusa
  subir** se já houver outro backend hospedando aparelhos aqui; com um backend só, vira o problema `clock_skew`
  em `/api/health` — derrubar o único backend por causa do relógio seria pior que o problema;
- o `Welcome` que o agente recebe carrega o relógio do banco, então o desvio que ele reporta (`clock_offset_s`,
  achado #142) é medido contra a mesma fonte;
- as escritas da etapa passaram a ter **cerca**: `transition_step` só grava uma etapa em execução se o
  `claimed_by` ainda for meu. Quem perdeu a posse recebe `PosseDaEtapaPerdida` e o worker larga o aparelho, em
  vez de gravar o desfecho por cima de quem agora executa.

O preço aceito dessa escolha: `uvicorn --workers N` continua proibido. Fork daria N processos com o mesmo hostname,
logo o mesmo dono, e cada um reconciliaria as etapas dos outros. Dois backends na mesma máquina exigem `OWNER_ID`
explícito.

## Quando o banco cai, e quando o esquema diverge

Duas coisas que o backend fazia mal enquanto o banco foi só um arquivo local, e que passam a doer no PostgreSQL:

**A conexão morria e ninguém reabria.** Era uma `psycopg.connect` aberta no construtor e guardada. Um
`pg_ctl restart`, uma sessão derrubada pelo administrador ou uma rede que piscou derrubavam TODA consulta até
alguém reiniciar o backend na mão. Hoje, quando a exceção diz que a conexão morreu (só `OperationalError`/
`InterfaceError` do psycopg — erro de SQL não entra), a conexão é reaberta e a instrução é repetida **uma** vez.
Uma, não um laço: se a segunda também falhar, o banco está fora e quem perguntou precisa saber agora.

Dentro de uma transação, nunca: lá a conexão morta levou junto tudo o que a transação já tinha feito, e repetir só
a última instrução gravaria metade do trabalho. O erro sobe, e a conexão fica marcada para a próxima chamada de
fora reabrir. `connect_timeout=5` existe pelo mesmo motivo: sem ele, um PostgreSQL fora do ar bloquearia a
reconexão no tempo do TCP (~20 s no Windows) **dentro do laço de eventos**, e a chamada que deveria dizer "o banco
caiu" seria a que congelaria o processo.

**O `/health` não olhava o banco.** A resposta não fazia uma consulta sequer, e nem dizia em qual banco o processo
estava — a pergunta só se respondia lendo o `.env` da máquina. Agora há `health.database` (`dialect`, `reachable`,
`target` sem usuário nem senha) e o problema `database_down`, que é **duro**: sem banco não há fila, nem posse de
etapa, nem histórico.

**Migração aplicada não se edita.** Era uma regra sem quem a fizesse valer, e o dano aconteceu: a `008` foi
reescrita no lugar depois de aplicada (`COLLATE NOCASE` → índice sobre `lower(username)`, porque o primeiro não
existe no PostgreSQL). O banco de produção ficou com um esquema que o mesmo arquivo não gera mais, e o controle
guardava só o número. Hoje `schema_migrations` guarda também o `sha256` do script **renderizado** (renderizado
porque é ele que o banco executou, e o mesmo arquivo gera dois esquemas), e o `/health` publica
`migration_changed` quando o arquivo de uma versão aplicada muda. Linha com `checksum` nulo — toda migração
aplicada antes desta conferência — não vira alarme: "não sei" não é "mudou". A `028` fecha a divergência da `008`
criando o índice que falta nos bancos antigos.

## Levar os dados de um banco para o outro

```powershell
scripts\stop.ps1                                                     # o banco nao pode estar sendo escrito
python scripts\sqlite-copia.py data\poc.sqlite3 data\copia.sqlite3    # copia consistente (WAL incluido)
cd backend
.venv\Scripts\python.exe -m app.tools.migrate_data --de ..\data\copia.sqlite3 --para postgresql://... --conferir
.venv\Scripts\python.exe -m app.tools.migrate_data --de ..\data\copia.sqlite3 --para postgresql://...
```

Migrar a partir da **cópia**, não do banco vivo: assim produção nunca é aberta para escrita e uma migração
interrompida não custa nada além do tempo.

O que a ferramenta faz e um `INSERT … SELECT` não faria:

- **recusa** se a origem não estiver na última migração (produção parou na `015` enquanto o código seguiu: copiar
  assim deixaria de fora as colunas que as migrações novas criaram, e pareceria ter dado certo);
- **recusa** se o destino já tiver dados (as chaves de `events` e `ai_calls` são geradas pelo banco, então nada
  colidiria — só apareceria o dobro de tudo);
- ordem de chave estrangeira tirada do esquema da origem, não de uma lista escrita à mão que envelhece;
- deixa de fora o que é **derivado**: `schema_migrations` (o destino se migra sozinho) e o índice de busca — o
  FTS5 é reconstruído pelos gatilhos, e no PostgreSQL `memory_items.busca` é coluna gerada, que nem aceita
  escrita;
- reposiciona as sequências de identidade do PostgreSQL (sem isto, a primeira inserção depois da migração pediria
  um id que já existe — e quebraria longe daqui);
- confere **contagem e impressão digital** por tabela, independentes de ordem de leitura. Contagem sozinha não
  pega valor truncado, `NULL` virado string nem byte perdido — e é o ciphertext do cofre (BLOB → BYTEA) que corre
  esse risco.

As **senhas** não atravessam sozinhas: ver *A chave do cofre é DPAPI*, abaixo. A ferramenta termina dizendo isso.

## Pendências honestas

- **Credenciais não atravessam sozinhas.** O cofre guarda AES-256-GCM com a chave mestra fora do banco, e no
  Windows ela é embrulhada por DPAPI, que é **por usuário e por máquina**. O ciphertext viaja com o banco; a
  chave, não. Duas saídas, e as duas agora têm ferramenta: adotar `CREDENTIALS_MASTER_KEY` (a mesma nos dois
  backends) e rodar `python -m app.security.rekey --aplicar` para recifrar o que já está guardado, ou recadastrar
  as credenciais pelo portal. Ver *O cofre com dois backends*, abaixo.
- **Workers e controle manual ainda vivem na memória de um processo** (achado #27). `WorkerRegistry.live`,
  `rt.worker_verbs`, o dono do controle manual e o barramento de eventos são por processo. Consequência prática:
  **um backend não pode compartilhar aparelho nem worker com o outro.** No backend em que o worker não está
  conectado, o mesmo aparelho aparece sem ciclo de vida e o painel recusa o comando com motivo enganoso; o
  navegador ligado a B não recebe em tempo real os eventos emitidos por A; e o controle manual tomado em B não
  impede a IA de A. Separar `hosted_by` é o que torna essa limitação **honesta** — cada backend com o seu
  conjunto —, não o que a resolve. Resolver exige persistir `connected_to`/sessão do worker, mover o lease de
  controle manual para tabela com TTL e publicar eventos entre réplicas.
- **Agendar execução para aparelho de outro backend não é possível pelo painel.** `RunService.create` recusa com
  `unknown_instance` o que este backend não hospeda — o que é honesto (ele não teria como executar), mas
  significa que a fila é criada no backend que hospeda o aparelho, não em qualquer um.
- **Instalação concorrente no mesmo aparelho continua sem trava no banco.** Ver o item de operação de app abaixo:
  a exclusividade de job (`scheduler.run_device_job`) é em memória. Com `hosted_by`, quem separa os aparelhos
  deixou de ser só a configuração e passou a ser o banco — mas dois backends que reivindiquem o MESMO aparelho
  por erro de operação não são impedidos pela linha de `instances`, só pelo carimbo, que é o primeiro a chegar.
- **Operação de app tem dono; instalação concorrente no mesmo aparelho, não.** Desde a migração 026,
  `device_app_state.claimed_by` guarda o `owner_id` de quem abriu a operação, e `reconcile_after_restart` só
  reconcilia o que é seu — o backend que sobe não declara mais "interrompida" a instalação VIVA do outro. O que
  continua faltando: nada impede os DOIS backends de instalarem no mesmo aparelho ao mesmo tempo. A
  exclusividade de job (`scheduler.run_device_job`) é em memória, por processo. Com dois backends, quem separa
  os aparelhos é `instances.hosted_by` (migração 027) — e, dentro de um aparelho, nada impede dois jobs de
  processos diferentes se o carimbo estiver errado.
- **Há tela de login, mas não há conta por pessoa.** Desde o item 9.1, `POST /api/login` troca o nome de quem
  opera (mais o `API_TOKEN`, quando a chamada vem de fora do loopback) por um cookie de sessão — é o que permite
  abrir o painel de outra estação e o que põe um nome em `commands.requested_by` e `pending_approvals.decided_by`.
  O que continua não existindo é **conta por pessoa com senha própria**: quem tem o `API_TOKEN` entra com o nome
  que quiser, e a identidade vale como trilha, não como controle de acesso. Separar acesso por pessoa (hash de
  senha, papéis) é decisão de quem cuida do parque.

## Segurança do banco entre máquinas

Vale quando o `DATABASE_URL` aponta para outra máquina — o cenário para o qual este documento recomenda
PostgreSQL. Ali a senha do banco e **todo o estado** atravessam a rede, inclusive o texto cifrado do cofre. Nada
disto é exigido pelo código: é o que falta configurar do lado do PostgreSQL para que a recomendação seja honesta.

**1. Papel dedicado, com o mínimo.** O backend não precisa de superusuário, nem de `CREATEDB`, nem de poder ler
outros bancos. Ele precisa de DDL **no schema do parque**, porque aplica as migrações na subida:

```sql
CREATE ROLE parque LOGIN PASSWORD '...';          -- senha longa, gerada; nunca a do postgres
CREATE DATABASE parque OWNER parque;
\connect parque
REVOKE ALL ON SCHEMA public FROM PUBLIC;          -- o padrão deixa qualquer papel criar objeto aqui
GRANT ALL ON SCHEMA public TO parque;
```

**2. Quem pode nem chegar à porta.** O padrão do instalador do Windows escuta em todas as interfaces:

```conf
# postgresql.conf — só os endereços por onde os backends chegam
listen_addresses = '127.0.0.1,192.168.1.10'
# pg_hba.conf — por IP e com senha cifrada, nunca 0.0.0.0/0 nem `trust`
hostssl parque parque 192.168.1.11/32 scram-sha-256
hostssl parque parque 192.168.1.12/32 scram-sha-256
```

`hostssl` (e não `host`) é o que recusa a conexão em claro no próprio servidor, em vez de deixar a decisão para
o cliente. Enquanto nenhuma outra máquina usar o PostgreSQL, `listen_addresses = 'localhost'` é o mais seguro —
e o firewall continua valendo como segunda camada, não como a primeira.

**3. TLS na conexão.** `sslmode` vai na própria `DATABASE_URL`:

```bash
# verify-full: cifra, confere a cadeia E o nome do servidor. `require` cifra sem conferir com quem se fala,
# o que não protege de alguém no meio — use-o só enquanto não houver um certificado com nome válido.
DATABASE_URL=postgresql://parque:SENHA@db.parque.local:5432/parque?sslmode=verify-full&sslrootcert=C:/parque/tls/parque-ca.pem
```

**4. A senha não vai para log.** Ela vive no `.env` (fora do Git, como todo segredo deste projeto), e a redação
do log passou a apagar **a senha dentro do DSN** (`postgresql://parque:…@host`) por formato — ela não era
coberta pelos padrões de chave/valor, porque `DATABASE_URL` não contém nenhuma das palavras da lista e a senha
viaja no meio de uma URL. É onde ela mais aparece: numa mensagem de erro de conexão.

**5. O cofre não é protegido por nada disto.** O ciphertext atravessa a rede como qualquer outra coluna, e o que
o mantém ilegível é a chave mestra, que **não está no banco**. Ver *A chave do cofre é DPAPI* e *O cofre com
dois backends*.

## Backup, restauração e deploy

Esta seção substitui a frase que estava na abertura deste documento — *"arquivo único, zero serviço, backup é
copiar"*. **Copiar é exatamente o que não funciona aqui**, e vale medir em vez de afirmar: com o banco em WAL
(`db.py`: `PRAGMA journal_mode=WAL`) e uma conexão escrevendo, `Copy-Item` do `.sqlite3` sozinho devolveu, no teste
`backend/tests/test_backup.py`, um arquivo que **abre, passa no `integrity_check` e não tem sequer a tabela** — o
`CREATE TABLE` e as 500 linhas ainda estavam no `-wal`. Um backup que falha assim é pior que um backup ausente:
ninguém descobre até precisar.

### O que copiar, e com quê

| O quê | Como | Onde |
|---|---|---|
| Banco (SQLite) | API de backup online do SQLite (`Connection.backup()`), com o backend **no ar** | `scripts/sqlite-copia.py` |
| Banco (PostgreSQL) | `pg_dump --format=custom` | `scripts/backup.ps1` |
| `config/` | cópia direta | `scripts/backup.ps1` |
| `data/credentials.key` | cópia direta, **só com `-IncluirSegredos`** | `scripts/backup.ps1` |
| `.env` | **não entra** — é o arquivo de segredos; guarde no gerenciador de senhas | — |
| `data/avd` (64 GB) | cópia **a frio**, sob demanda, com os emuladores desligados | manual (ver abaixo) |
| `apks/` (catálogo) | cópia direta — **obrigatória junto com o banco** | manual, ou storage compartilhado |
| `data/evidence`, `data/avatars` | cópia direta, ou já no bucket | manual, ou storage compartilhado |

```powershell
pwsh -File scripts\backup.ps1                      # cópia em data\backups\<AAAAMMDD-HHmmss>\
pwsh -File scripts\backup.ps1 -IncluirSegredos -Reter 30
pwsh -File scripts\backup.ps1 -Instalar            # tarefa agendada diária, 03:00
```

A retenção nunca apaga a última cópia, mesmo que a idade diga que sim: backup vazio é pior que backup velho.

### A chave do cofre é DPAPI, e isso muda o que o backup significa

`data/credentials.key` é embrulhada por DPAPI (`security/secret_store.py`): ela **só abre com o mesmo usuário na
mesma máquina**. Copiá-la para outro servidor leva o arquivo junto e não recupera credencial nenhuma. Restaurar o
banco noutra máquina traz execuções, perfis, memória e histórico — e **não** traz as senhas dos perfis.

Há dois caminhos, e é preciso escolher um **antes** de precisar:

- **(a) Chave mestra explícita.** Definir `CREDENTIALS_MASTER_KEY` no `.env`, guardada fora do host (o nome antigo,
  `INSTAGRAM_CREDENTIALS_MASTER_KEY`, continua sendo aceito). A partir daí o cofre deixa de depender do DPAPI e o
  backup passa a ser restaurável em qualquer máquina. Adotar isso **depois** já não exige recadastrar: é o que a
  ferramenta de recifragem faz.
- **(b) Aceitar o recadastro.** Restaurar o banco e redigitar a senha de cada perfil pelo portal. O backend acusa
  o estado em `/api/health` como `secret_store_locked` e a interface pede o recadastro.

Hoje o parque está em **(b)**: `CREDENTIALS_MASTER_KEY` não está definida. Trocar para (a) é decisão do dono — e o
preço deixou de ser recadastrar os 8 perfis:

```powershell
scripts\stop.ps1
$env:CREDENTIALS_MASTER_KEY = "<base64 de 32 bytes>"     # e grave no .env
.venv\Scripts\python.exe -m app.security.rekey --conferir    # nao grava: diz o que faria
.venv\Scripts\python.exe -m app.security.rekey --aplicar
```

Sem `PREVIOUS_CREDENTIALS_MASTER_KEY` no ambiente, a chave ANTIGA é a do DPAPI local — que é exatamente a
travessia (b) → (a). A chave antiga **nunca** vai na linha de comando: argumento de processo aparece na lista de
processos da máquina inteira.

### O cofre com dois backends

Antes, `key_id` era uma constante (`dpapi-v1`): duas máquinas com DPAPI gravavam o MESMO rótulo com chaves
DIFERENTES, e a guarda que deveria proteger este caso era inerte justamente nele. O backend B não abria as senhas
gravadas por A (aparecia como "recadastre"), recadastrar por B quebrava A, e **os dois diziam `ready`**.

Hoje o `key_id` carrega a impressão digital da chave (`dpapi-v1:3f2a9c01`, 8 hex do SHA-256 — não permite
reconstruir a chave, permite responder "é a mesma?"). Com isso:

- `get_secret` recusa com o motivo certo, nomeando as duas chaves, em vez de um "recadastre" enganoso;
- `/api/health` publica o problema `secret_store_foreign_key` quando há credencial no banco cifrada por outra
  chave — o sintoma antes era login automático falhando de forma intermitente, sem nada apontando a causa;
- credenciais ANTIGAS (gravadas como `dpapi-v1` pelado, sem impressão) continuam abrindo: comparar família e
  tentar decifrar é o comportamento que sempre existiu, e exigir recadastro delas seria cobrar um preço que a
  melhoria não vale.

**Dois backends no mesmo banco precisam da MESMA `CREDENTIALS_MASTER_KEY`.** É o que troca a proteção do DPAPI por
um arquivo `.env` bem guardado; a alternativa é só um dos backends executar autenticação.

### AVDs

`data/avd` guarda as sessões: a conta Google da VM-loja (com 2FA) e os logins do Instagram. Aparelho novo sem
esses dados significa refazer login e passar por desafio de verificação — por isso eles importam, e por isso não
entram no backup diário (dezenas de GB, e a cópia a quente de um emulador ligado não é confiável). A política é
cópia **a frio**, sob demanda, antes de mexer no host:

```powershell
pwsh -File scripts\stop.ps1 -StopEmulators
Compress-Archive -Path data\avd\* -DestinationPath D:\copias\avd-$(Get-Date -Format yyyyMMdd).zip
```

No worker, o equivalente é `C:\farm\avd`.

### Restaurar — e ensaiar a restauração

O padrão de `scripts/restore.ps1` é o **ensaio**: ele restaura numa pasta limpa, confere e não toca em `data\`.
Pode rodar com o parque no ar.

```powershell
pwsh -File scripts\restore.ps1 -De data\backups\<carimbo> -Para C:\temp\ensaio
pwsh -File scripts\restore.ps1 -De data\backups\<carimbo> -Confirmar   # por cima de data\, backend PARADO
```

Com `-Confirmar` ele exige o backend parado (dois processos escrevendo o mesmo SQLite é o estado que o projeto
evita em toda parte) e move o estado anterior para `data\substituido-<carimbo>\` antes de sobrescrever.

### Deploy: parar → copiar → subir → conferir

O procedimento existe porque a subida que importava aconteceu sem ele. O backend que servia o parque foi iniciado
**antes** de `016_step_ownership` e `017_busca_sem_acento` existirem — processo de 21/09 às 16:05, migrações
escritas às 16:19 e 16:59 — e o banco vivo parava em `015_workers`. A primeira subida do código atual sobre os
dados reais ia acontecer no próximo restart, planejado ou não, **sem cópia prévia**.

```powershell
pwsh -File scripts\deploy.ps1 -Ensaio     # backup + retrato do que está no ar; não para nada
pwsh -File scripts\deploy.ps1             # a subida
```

A migração **não é um passo separado**: `AppState.__init__` chama `db.migrate()` na inicialização. O que o script
faz é copiar o banco *antes* disso e **conferir depois** — comparando `/api/health` (que agora devolve `commit` e
`migration`) com o `git rev-parse HEAD` da árvore e com o último arquivo de `backend/migrations/`. Antes disso
`/api/health` só dizia `version: "0.1.0"`, uma constante do código: ele respondia igual antes e depois da subida.

**Ensaio já feito (22/09/2026).** Cópia consistente do banco real (77 MB, `integrity_check: ok`) tirada **com o
backend no ar**; a cópia veio em `015_workers`, como esperado. `Database(cópia).migrate()` aplicou
`['016_step_ownership', '017_busca_sem_acento']`, o `PRAGMA table_info(steps)` passou a ter `claimed_by` e
`claim_expires_at`, a integridade continuou `ok` e os dados ficaram todos lá (77 execuções, 1221 etapas, 72.903
eventos, 8 perfis, 8 segredos, 0 memórias). **O banco de produção não foi tocado** — a cópia é aberta em `mode=ro`.

**O que falta para fechar, e depende do dono:** executar `scripts\deploy.ps1` na produção (reiniciar o backend e
migrar o banco real). Depois disso, e só depois, reinstalar o túnel do worker apontando para o listener dedicado:

```powershell
pwsh -File scripts\worker-tunnel.ps1 -Instalar -MapaReverso '18000:8010'
```

Conferência do lado do worker: `GET http://127.0.0.1:18000/api/health` deve responder **404** (e não 200, como
responde hoje).

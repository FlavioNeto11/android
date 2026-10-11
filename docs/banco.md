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

## Migrações (001–076, 078 a 083, 092 a 099, 092 a 098, 086, 088, 090, 100 a 107, 109)

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
| 049 | contas_unificadas | `account_credentials` ganha `status`, `failed_attempts`, `blocked_until`, `created_at`, `last_used_at`, `consent_at`, `consent_by` (consentimento por conta); `profile_accounts.host` e unicidade `ux_profile_accounts_app_host (profile_id, app_id, COALESCE(host, ''))` no lugar de `ux_profile_accounts_app`; **`account_sessions`** com chave `(account_id, instance_id)` e vocabulário único (`unknown\|session_ready\|auth_required\|wrong_account\|needs_person`; `logged_out` da 037 vira `auth_required`), `ON DELETE CASCADE` da conta; `authentication_attempts.account_id`. Carga só com o app do Instagram registrado: conta âncora `acc-<perfil>` onde faltava; credencial de `instagram_credentials` com o **mesmo `secret_ref`** (nada recifrado) e `consent_at = updated_at`, `consent_by = 'migração 049'`; sessão de `instagram_sessions` só onde há vínculo ativo com o aparelho; marcação de `profile_accounts.session_status` de app sem provedor vai para a sessão no aparelho vinculado; tentativas apontadas para a conta. Idempotente (`INSERT … SELECT … WHERE NOT EXISTS`). `instagram_credentials`, `instagram_sessions` e `run_secrets` ficam só leitura; `profile_accounts.session_status` fica sem leitor (onda B da segunda evolução, 27/09; ADR-040) |
| 050 | provisionamento | `worker_limits.max_devices` (teto de aparelhos existentes por servidor, NULL = sem teto); `instances.android_overrides` (JSON por instância criada pela plataforma), `instances.retired_at` — provisionamento pela plataforma (onda D da segunda evolução, 27/09) |
| 051 | persona_n_aparelho | `device_profile_bindings` +`app_id TEXT` (NULL = apps sem conta gerenciada) +`is_primary INTEGER NOT NULL DEFAULT 0`; saem `idx_binding_profile_ativo`/`idx_binding_device_ativo`; entram `ux_binding_par_ativo (profile_id, instance_id, (COALESCE(app_id,''))) WHERE active=1`, `ux_binding_conta_do_app_no_aparelho (instance_id, app_id) WHERE active=1 AND app_id IS NOT NULL` (D2-a) e `ux_binding_principal (profile_id) WHERE active=1 AND is_primary=1`; carga: vínculo ativo vira principal e ganha o `app_id` da conta ÚNICA da persona (zero ou várias contas = NULL); `runs.targets TEXT` (JSON: `alvos[] {instance_id, profile_id, app_id, origem}`, `command_sem_destinos`, `device_policy`, `pedido`; NULL nas antigas, que re-resolvem pelo aparelho). Retrato de produção (8 vínculos, 3 ativos): 3 principais, app pela conta única (onda C da segunda evolução, 28/09; ADR-043, ADR-044) |
| 052 | saldos_dos_provedores | `ai_billing_accounts` (conta PK `anthropic`/`openai`/`gemini`, `currency`, `units_per_usd`, `warn_below`, `block_below`, `stale_after_h` (sem uso desde o livro-caixa); sem carga: conta sem linha usa `planning/saldos.py::PADRAO`, aviso US$ 3 Anthropic / US$ 2 OpenAI / R$ 10 Gemini, bloqueio US$ 0,50 / R$ 2,50, câmbio 5,2) e `ai_balance_snapshots` (leitura do console: `balance`, moeda, câmbio, `source` `manual`/`console`/`provider_error`, `observed_at`; índice `(account, observed_at)`). Saldo estimado = última leitura − gasto de `ai_calls` desde ela (ADR-051) |
| 053 | linha_de_base_da_conciliacao | `ai_balance_snapshots` +`provider_baseline_usd REAL` +`local_baseline_usd REAL`: o que o relatório do provedor e `ai_calls` já tinham na janela quando a leitura foi conciliada pela primeira vez (o registro concilia no mesmo instante). Gasto externo = (provedor − base) − (local − base); NULL = ainda não conciliada (ADR-051) |
| 054 | protecao_de_contas | `device_locked_accounts` (marcador, por aparelho, de conta travada logada: `instance_id`, `handle` normalizado, `profile_id`/`app_id` só como referência, sem FK — sobrevive ao desvínculo e à remoção do perfil —, `origin` `observado`/`declarado`/`regra` com CHECK, `seen_by`, `evidence`, `since`, `resolved_at`/`resolved_by`/`resolution`; `ux_locked_account_aberto (instance_id, handle) WHERE resolved_at IS NULL`, `ix_locked_account_instance`); `instagram_profiles` +`blocked_at` +`blocked_evidence` +`blocked_origin` (CHECK; NULOS nos perfis já bloqueados: não se inventa data); `instances.account_label_origin` (NULL = configuração; `vinculo`/`marcador` = derivado pela plataforma). Carga condicional: o marcador do android-04 com a conta dele, `declarado` pelo dono, desde 2026-09-29T00:36:00.000Z, só onde o aparelho e o perfil existem (banco novo e de teste nascem sem marcador). É do ADR-055 — a numeração se cruza com a 055, do ADR-054 |
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
| 071 | contas_retiradas | Lápide das contas que saíram da plataforma por bloqueio confirmado (item 29.23, ADR-068). Tabela nova `contas_retiradas` (`app_id`, `handle_sha256` = sha256 de `@` + handle em minúsculas e sem espaços, NUNCA o texto, `retirada_em`, `profile_id` só de auditoria; `UNIQUE (app_id, handle_sha256)` para a retirada ser idempotente; índice por `handle_sha256`). SEM chave estrangeira de propósito: não morre com o `DELETE` do perfil nem da conta. Fora da retenção (que apaga por lista de tabelas). `social.contas_nossas.eh_conta_nossa(db, handle)` consulta os @ vivos de todos os perfis e apps mais estes hashes. **Número 071:** reservado pela coordenação para o 29.23 |
| 072 | pedido_avisos | Caixa de avisos do pedido persistente (item 28.9, Fase 28). Tabela nova `pedido_avisos`: `id` TEXT PK; `pedido_id` FK `ON DELETE CASCADE`; `ocorrencia_id` SEM FK (o aviso sobrevive à purga); `tipo` com CHECK nos nove do contrato (`pausa_automatica`, `orcamento_80`, `orcamento_esgotado`, `ocorrencia_perdida`, `relatorio_pronto`, `encerramento`, `aprovacao_pendente`, `pergunta`, `ocorrencia_incerta`; conferido contra `domain/avisos.py` em `tests/test_pedidos_avisos.py`); `nivel` `info`/`warn`/`error` com CHECK; `mensagem`; `dados` JSON (`'{}'`); `requer_pessoa` 0/1; `chave_dedupe` **UNIQUE** (identidade do fato; todo aviso é gravado com `INSERT ... ON CONFLICT DO NOTHING` e só então emitido como `pedido.aviso`, e só se a linha é nova); `criado_em`; `lido_em` (NULL = não lido; `POST /api/pedidos/avisos/ler` o preenche, idempotente); índice `(pedido_id, lido_em)`. Nada de `pedidos` nem das outras tabelas é tocado. **Número 072 dado pela coordenação (02/10);** o 071 é de outra frente e a lacuna é tolerada pelo executor |
| 073 | origem_das_chamadas_de_ia | Origem do gasto de IA (item 31.2, Fase 31; `hub-de-ia-fora-de-execucao.md` §2, decisão P2). Duas colunas nulas em `ai_calls`: `origem` (vocabulário fechado no código, `ORIGENS_DE_IA`: `execucao`, `ensino`, `orquestracao`, `assistente`, `social`, `persona`, `curador`, `decisao_fechada`; NULL nas linhas antigas, sem reclassificar) e `ref` (id do item de origem, texto solto sem chave estrangeira). Índice `idx_ai_calls_origem (origem, ts)` para o filtro por origem do gasto do dia (`costs.spent_usd(origem=...)`), que `_budget` usa na fatia do curador e da decisão fechada (31.6). Sem CHECK no vocabulário. **Número 073:** dado pela coordenação; as 071 e 072 estão em outros branches e entram ANTES, e a lacuna de número é tolerada pelo executor, que aplica em ordem de nome |
| 074 | sombra_da_decisao_fechada | Duas tabelas novas (item 31.5, Fase 31; ADR-069; número dado pela coordenação, as 071 a 073 entram de outros branches e a lacuna é esperada): `decisao_fechada_sombra` (`id` auto, `ts` ISO, `chamada` agrupa as N perguntas de uma chamada, `origem`, `classe`, `modo`, `pergunta_id`, `escolha` id opaco, `probabilidades` JSON só com ids opacos, `confianca`, `decisao_real` id opaco do caminho atual e `desfecho` do vocabulário fechado `sombra.DESFECHOS`, ambos preenchidos DEPOIS por `ref` ou `step_id`, `usd` e `tokens` só na primeira linha da chamada, `ms`, `fallback_reason`, `run_id`, `step_id`, `ref`) e `decisao_fechada_diario` (PK `day, origem, pergunta_id`: `n`, `com_decisao_real`, `concordancia`, `acima_do_limiar`, `aceite_errado`, `fallbacks`, `usd`, `tokens`, `ms_p95`, `computed_at`). NUNCA guarda o estado enviado nem o texto das opções ou das instruções (só ids e categorias; quem grava confere o formato do id). Padrão da 055: sem FK e sem CHECK nos vocabulários, filtro de tempo por texto ISO. Retenção PRÓPRIA `ai.decisao_fechada.retencao_dias` (padrão 180), purga de dias inteiros no laço de retenção geral (`AppState._purgar_demais_tabelas`) com o agregado do dia recalculado antes e mantido; `ai_calls` morre em `log_retention_days`, a sombra não. Simulado no SQLite (`test_decisao_fechada_sombra.py`); PostgreSQL pendente (P17, 29.14) |
| 075 | revisoes_ai_call_id | `learning_reviews.ai_call_id INTEGER` nulável (frente Aprendizado; número PRÉ-reservado pela coordenação; a 074 é a da sombra da decisão fechada, 31.5, em outro branch). Liga a revisão do curador à linha de `ai_calls` que o hub mediu (30.12); sem chave estrangeira (`ai_calls` é purgada pela retenção, a revisão não). `usd` continua `NOT NULL DEFAULT 0` = não medido: recriar a tabela no SQLite para torná-lo nulável não compensa, e o `usd` medido só passa a ser gravado depois de unificar o saldo na rubrica (pendência em `design/hub-de-ia-fora-de-execucao.md`). Simulado no SQLite (`test_migracao_075_revisoes_ai_call_id.py`); PostgreSQL pela mesma fábrica quando `TEST_DATABASE_URL` existe |
| 076 | pedido_avisos_gatilhos | Avisos dos gatilhos do pedido (item 28.8; `pedidos-laco.md` §14.5). O CHECK de `pedido_avisos.tipo` ganha `eventos_perdidos` (a retenção apagou eventos que um gatilho `evento` não tinha lido; nada foi disparado) e `condicao_atendida` (a condição passou de falsa a verdadeira); os dois `warn`, sem `requer_pessoa`. **SQLite:** `pedido_avisos` é RECONSTRUÍDA (CHECK de coluna não se altera), com colunas nomeadas, FK `ON DELETE CASCADE`, `chave_dedupe` UNIQUE e o índice `ix_pedido_avisos_pedido`; nada aponta para ela, sem `@foreign_keys:off`. **PostgreSQL:** DROP/ADD de `pedido_avisos_tipo_check`. Prova em `tests/test_pedidos_avisos.py` (SQLite; PostgreSQL `not_run`). **Número 076 dado pela coordenação (02/10);** 074 e 075 são de outras frentes e entram antes |
| 078 | origem_das_saidas | Origem do valor lido entre etapas (item 12.5, ADR-070). Quatro colunas em `step_outputs`: `origem` (`arvore`|`visual`, `NOT NULL DEFAULT 'arvore'` com CHECK: o legado e todo valor lido do texto do elemento ficam `arvore`), `leitor` (`provedor/modelo`), `frame_sha256` e `evidence_id`, estas três nulas fora do visual (sem chave estrangeira: a retenção apaga a evidência, não o valor). As numerações 074 a 077 são de outras frentes. |
| 079 | sombra_ambiguos | `decisao_fechada_sombra.ambiguos INTEGER` nulável (RA-2 da reavaliação de 03/10, Fase 31; `design/jev-golden-set.md`): quantas etapas da RESOLVE da intenção terminaram em `StageOutcome.AMBIGUOUS` na execução da linha, gravado pela sombra da intenção junto com a decisão real (`RepositorioDeSombra.anotar_ambiguos`, só nas linhas `origem = 'intencao'` que ainda não têm). NULO = outra origem ou linha anterior. Só número. E `decisao_fechada_sombra.motivo_privacidade TEXT` nulável (reverificação B do 31.9, 03/10): por que o chamador recusou por privacidade, `c7_*` ou o motivo do filtro da C3, em vocabulário fechado (`contrato.MOTIVOS_DE_PRIVACIDADE`; fora dele, `outro`), só nas linhas `fallback_reason = 'privacidade'`. Estava na 080 (RA-10) e veio para a 079 porque o 31.9 a grava e entra antes; a 079 não estava aplicada em banco nenhum (central na 078, conferido em 03/10). **Número 079 reservado pela coordenação (03/10);** 077 (28.10) e 078 (12.5) estão em outros branches e a lacuna é esperada. Prova em `tests/test_decisao_fechada_intencao.py` e `tests/test_decisao_fechada_reverificacao_b.py` (SQLite; PostgreSQL `not_run`) |
| 080 | observabilidade_das_chamadas | RA-10 (reavaliação de 03/10). Quatro colunas nulas em `ai_calls`: `verdict` (o desfecho: `yes`/`no`/`uncertain`/`unprovable` no `verify`, a ferramenta no `decide` ou `desconhecida`, `plano`/`pergunta` no `plan`), `escalate` (por que subiu ao modelo de escalonamento; nulo = não subiu), `motivo` (para que a chamada foi feita) e `image_reason` (por que a imagem foi ou não); vocabulários fechados em `planning/provider.py` e `docs/ia.md` §9, sem CHECK no banco (o vocabulário cresce no código). Só `ADD COLUMN`, válido nos dois dialetos. Independe da 079 (`sombra_ambiguos`, branch do 31.9, que acrescenta `ambiguos` e `motivo_privacidade` em `decisao_fechada_sombra`, tabela que a 080 não toca): a ordem dos merges é livre, porque o migrador aplica, em ordem de nome, todo arquivo ainda ausente de `schema_migrations`. Simulado no SQLite (`test_observabilidade_das_chamadas.py`); em PostgreSQL `not_run` nesta entrega (o banco de teste da porta 55433 estava desligado) |
| 081 | error_kind_em_attempts | O tipo do erro de IA que encerrou a tentativa (RA-22): `attempts.error_kind TEXT` nulável, o `AIError.kind` (`budget`, `billing`, `balance`, `refusal`, `not_configured`, `step_deadline`, `invalid_output`, `error`); nulo = a tentativa não terminou por erro de IA, ou é anterior. Sem CHECK (o vocabulário é do provedor) e sem backfill. Com ele, `classificar_falha` decide pelo tipo antes do texto (`failure_kind` da tentativa e da etapa). As numerações 079 e 080 são de outras frentes. |
| 082 | learning_validations | Tabela nova `learning_validations` (item 30.31, Fase 30; desenho aprovado pela orquestradora em 03/10): os pedidos de validação automática do curador. Quando o parecer é `pedir_evidencia` e o que falta uma execução produz, o sistema grava o pedido e um despachante roda a execução de validação (o comando de origem noutro aparelho ocioso). `id` TEXT PK `lv-<token>`; `review_id` (a revisão que pediu), o item (`item_ref`, `item_kind`, `scope_app`), `grupo` (`qa`, `leitura`, `efeito_real`), `falta` JSON, `run_origem`, `comando`, `aparelho_excluido`; o estado (`pendente`, `rodando`, `feita`, `recusada`, `expirada`) com `motivo`; o que rodou (`run_id`, `aparelho`, `usd`), `expira_em`, `feito_em` e `revisao_nova_id`. Padrão da 069: sem FK e sem CHECK nos vocabulários. `ux_learning_validations_vivo (item_ref) WHERE estado IN ('pendente','rodando')` UNIQUE parcial: um pedido vivo por item. `ix_learning_validations_fila (estado, created_at)` e `ix_learning_validations_run (run_id)`. O pedido não transiciona nada: quem decide continua sendo o ciclo do livro. |
| 083 | sombra_postado | Item 31.21 (Fase 31; ADR-069; proposta aprovada pela orquestradora em 03/10). Duas colunas nulas em `decisao_fechada_sombra`. `postado INTEGER`: 1 quando o decisor chamou o transporte (o POST foi tentado), 0 quando parou antes (privacidade, decisor nulo, só `noul`/`score`, orçamento, prazo esgotado, chave ausente), NULO quando não se sabe (exceção inesperada, futuro em voo no `on`) e no legado. `ai_call_id INTEGER`: o `ai_calls.id` da chamada (devolvido por `RepositorioDeSombra.registrar_chamada`, padrão da 075), NULO sem POST ou com a linha de gasto que não gravou. As duas vão em TODAS as linhas da chamada, como `ms`, e nunca se somam (o `usd` segue só na primeira). Separa a `rede` antes do POST da de depois e torna exato, por linha, o cruzamento sombra × `ai_calls` (`docs/ia.md` §16). Sem FK: a sombra sobrevive às linhas de `ai_calls`. Só `ADD COLUMN`, válido nos dois dialetos. Simulado no SQLite (`test_decisao_fechada_postado.py`); PostgreSQL `not_run` nesta entrega |
| 084 | prova_de_fluxo | Duas colunas nulas (item 30.37, Fase 30; desenho aprovado pela orquestradora em 03/10, emenda datada à D1 do ADR-054): `runs.prova_fluxo_id TEXT` (o fluxo que a EXECUÇÃO DE PROVA prova: o plano é o do próprio fluxo com os parâmetros do comando de origem, sem `runs.flow_id`, fora das sombras e do aprendizado de fluxo) e `learning_validations.teto_usd REAL` (o teto de IA do pedido, `aprendizado.validacao.teto_por_pedido_usd`; `teto_usd_da_execucao` usa o menor entre ele e o do pedido do 28.6). Origem: K-086 (a validação por re-execução não deixava evidência para fluxo ativo e media o planejador livre no candidato). Sem FK nem índice; só `ADD COLUMN`, válido nos dois dialetos; a ordem com a 083 é livre. Simulado no SQLite (`tests/test_learning_prova.py`); PostgreSQL pela mesma fábrica quando `TEST_DATABASE_URL` existe |
| 085 | canal_entradas | A conversa de volta pelos canais externos (item 28.15, ADR-071; contrato em `design/canais-externos.md` §6). Duas tabelas genéricas, por canal (`telegram` hoje; o Trello do 32.2 usa as mesmas com `canal='trello'`): `canal_entradas`, uma linha por coisa recebida, com `UNIQUE (canal, id_externo)` (o dedupe: um comando nunca roda duas vezes) e `ordem` BIGINT (no Telegram, o `update_id`: o offset do próximo `getUpdates` é `MAX(ordem)+1`, e a linha é gravada antes de o offset avançar); `do_dono`, `ref_mensagem`, `responde_a`, `texto` (NULL quando não veio do dono, na credencial recusada (pela forma ou pelo contexto da pergunta), no curto com pergunta de senha aberta e na mensagem antiga, `ignorada` depois de `avisos.entrada.idade_max_s`), `tamanho`, `intencao`, `destino`, `estado`, `alvo`, `previa` JSON, `run_id`, `resposta`, `erro` e os instantes. `canal_enviadas`: o que a Central mandou pelo canal (`UNIQUE (canal, ref_mensagem)`, `origem`, `fato`, `aviso_id`, `entrada_id`), que liga o reply ao fato do aviso. A 1ª subida do canal grava a linha-marco `inicio` (`ignorada`), com a ordem da última update descartada do histórico. O webhook do Trello (32.2, passo 5) grava ali um AVISO: `estado = 'aviso'`, `tipo = 'outro'`, só `id_externo` (o id da action) e `recebida_em`, sem texto, autor nem cartão; o líder relê a action pela API, apaga a linha-aviso e `registrar` grava a de verdade (ou a deixa `ignorada` sem texto, se a API não a devolve). Referências do canal em TEXT (o Trello não numera). Padrão da 082: sem FK e sem CHECK nos vocabulários. A 083 e a 084 são de outras frentes. |
| 086 | sombra_estado_hash | Item 31.22 (Fase 31; ADR-069 item 21; número dado pela orquestradora em 03/10). Uma coluna nula em `decisao_fechada_sombra`: `estado_hash TEXT`, o sha256 (hex, 64) do JSON canônico do estado DEPOIS do `privacidade.redigir` (`porta.hash_do_estado`), em todas as linhas da chamada, na sombra e no `on`. NULO na recusa de privacidade (nada foi redigido para sair) e no legado; a sombra só grava o formato do sha256. É o que o lote offline do 31.11 compara para reenviar só o comando que já saiu igual pela sombra da intenção. Só o hash: nada do estado, das opções nem do comando. A 084 e a 085 são de outros branches, e a 087 do 32.2. |
| 087 | trello | O Trello do dono como espelho e canal de comandos (item 32.2, ADR-072; desenho em `design/trello-integracao.md` §7). As actions recebidas vão para a `canal_entradas` da 085 (`canal='trello'`, `id_externo` = id da action, `ordem` NULL) e as respostas para a `canal_enviadas` (`ref_mensagem` = id do comentário); esta migração só cria o que é do Trello. `trello_cartoes`: a ligação fato-cartão do reconciliador do espelho (`chave` PK `<família>:<fato>`, `card_id` com índice ÚNICO, `quadro`, `lista`, `hash` do conteúdo desejado, `estado` `ativo`|`arquivado`, `criado_em`, `atualizado_em`). `trello_cursor`: por quadro, a última action lida pela reconciliação (`ultima_action`, `ultima_data`, `atualizado_em`). A linha só existe depois da 1ª leitura (é o que separa a 1ª subida, em que o cursor começa na action mais nova e o histórico não é tratado, de um quadro vazio, que fica com `ultima_data` no começo dos tempos); `atualizado_em` anda a cada leitura que deu certo, mesmo sem action nova, e é o que a saúde `trello_leitor_atrasado` compara. `trello_cartoes.estado` também usa `criando` (o banco vai primeiro: `card_id` sentinela `criando:<chave>` até o cartão nascer ou ser adotado pela marca `🤖 chave:` da descrição). Padrão da 082/085: sem FK e sem CHECK nos vocabulários, tempo em texto ISO-8601, ids do Trello em TEXT. Nenhum segredo vai ao banco (chave, token e segredo do aplicativo só no `.env`). Simulado no SQLite (`tests/test_trello_config.py`); PostgreSQL pela mesma fábrica quando `TEST_DATABASE_URL` existe |
| 088 | latencia_por_etapa | Item 31.24 (Fase 31; número reservado pela orquestradora em 03/10 21:18Z; a 087 é do 32.2). Latência por etapa como métrica de primeira classe: só mede, nenhum comportamento muda, nenhuma coluna guarda texto. `ai_calls` ganha `started_at` (entrega ao provedor, já com a vaga; `ts` segue sendo o fim), `vaga_ms` e o preparo da decisão do ator (`prep_settle_ms`, `prep_observacao_ms`, `prep_arvore_ms`, `prep_imagem_ms`, `prep_prompt_ms`; árvore e imagem são partes da observação), NULOS fora de `StepExecutor._ai`. `actions.ai_call_id BIGINT` (o decide que escolheu a ação; sem FK, porque `ai_calls` tem retenção). `attempts.juiz_espera_ms`, `verificacao_ms`, `evidencia_ms` (no mesmo UPDATE da trilha da 045). Tabela nova `esperas` (`run_id`, `objective_id`, `motivo`, `inicio`, `fim`; motivos `aparelho`, `perfil`, `rede`, `caminho`, `pessoa`), aberta e fechada nas transições do repositório só quando o motivo muda; sem FK, sem CHECK, sem retenção própria. Só `ADD COLUMN` e tabela nova, válido nos dois dialetos. Simulado em `tests/test_latencia_por_etapa.py`; leitura em `scripts/latencia-por-etapa.py` (`docs/ia.md` §18) |
| 090 | canal_contatos | Quem fala com o bot e não é o dono (item 28.18; regras C-08 a C-11 de `dominios/canais.md`; emenda de 04/10 ao ADR-071; a 089 ficou livre). `canal_contatos`: uma linha por chat (`PRIMARY KEY (canal, chat_id)`), com `estado` (`aguardando_nome` → `aguardando_dono` → `autorizado` ou `recusado`), `nome_informado` (limpo, uma linha, sem e-mail, @conta nem IP, cortado em `avisos.entrada.convidados.max_nome`), `perfil` (JSON do `from` do Telegram: nome, sobrenome, @, idioma, se é bot), `nome_pedido_em` (a pergunta do nome que não saiu se repete), `primeira_em`, `ultima_em`, `decidido_em`. `canal_contato_eventos`: o histórico pedido pelo dono (mensagem redigida e cortada em 500, pergunta do nome, nome informado, aviso ao dono, decisão, `retido` sem texto para o que parece credencial ou é longo demais, bot posto ou tirado). O vínculo id↔pessoa mora só aqui: nunca no Trello, no Git, em log ou no chat de outra pessoa. A `canal_entradas` da 085 segue gravando a update do convidado SEM texto (`estado = 'convidado'`), para o dedupe e o offset. Padrão da 085: sem FK e sem CHECK nos vocabulários, tempo em texto ISO-8601. A faxina por retenção do 28.16 cobre as duas. Simulado em `tests/test_telegram_convidados.py`; PostgreSQL pela mesma fábrica quando `TEST_DATABASE_URL` existe |
| 092 | cache_de_1h | Item 31.31 (Fase 31; número reservado pela orquestradora em 04/10; 089–091 ficam com outras frentes). `ai_calls.cache_write_1h`: a PARTE de `cache_write` gravada com validade de 1 h (`usage.cache_creation.ephemeral_1h_input_tokens`; o plano da execução desde o 31.30). A gravação de 1 h custa 2x a entrada, e a de 5 min, 1,25x (o 3º preço de `ai.prices`): `planning/costs.extra_1h` soma a diferença sobre esta parte em `spent_usd`, `usd_por`, no saldo por conta, no `/api/usage` e na projeção. NULO no legado e nos provedores sem cache de 1 h, que custa como antes. |
| 093 | teto_de_reinicios_da_rede | Item 25.11 (RA-12, parte Android; número reservado pela orquestradora em 04/10; 089 e 091 ficam como lacunas). `device_network.restart_rev` e `restarts_requested`: a conta de reinícios pedidos da revisão em curso, que vivia só na memória de `rede_convergencia.py` e zerava a cada reinício do backend (88 reinícios pedidos pela rede em 7 dias). Só ADD COLUMN; linha antiga começa com a conta zerada. |
| 094 | indices_da_telemetria | Item 14.13 (RA-11, parte Android; número reservado pela orquestradora em 04/10). Só índices, `IF NOT EXISTS`: `events(ts)` e `events(kind, ts)` para a purga por retenção e as leituras por tipo (o único índice de `events` era `(run_id, id)`), e `measurements(kind, ts)` e `measurements(ts)` (a purga dela é `WHERE ts < ?`; não havia índice nenhum). Nenhuma linha muda. |
| 095 | ocorrencia_resolvida | Item 28.21 (número reservado pela orquestradora em 04/10; a lacuna até a 088 é esperada). Três colunas nulas em `pedido_ocorrencias`: `resolvida_em` (UTC, ISO-8601, relógio do serviço de pedidos), `resolvida_por` (operador da sessão, `panel` sem sessão) e `resolvida_nota` (obrigatória na rota). Gravadas juntas por `POST /api/pedidos/{id}/ocorrencias/{oid}/resolver` quando uma pessoa conferiu uma ocorrência `incerta`; a ocorrência CONTINUA `incerta` e só deixa de ser pendência (`pendencias` ignora a incerta com `resolvida_em`). Origem: a incerta só saía das pendências quando uma ocorrência posterior concluía, e o pedido parado em `aguardando_pessoa` não gera nenhuma; `retomar` ficava em 409 e a única saída era cancelar. Só `ADD COLUMN`, sem FK, CHECK nem índice, válido nos dois dialetos. Simulado em `tests/test_pedidos_resolver_incerta.py`; PostgreSQL pela mesma fábrica quando `TEST_DATABASE_URL` existe |
| 096 | pedido_dependencias | Item 28.10, fatia F1 (colaboração entre pedidos, só a estrutura; número reservado pela orquestradora, a lacuna 089 a 095 é esperada; desenho em `design/pedidos-persistentes.md` §9). Tabela `pedido_dependencias(de, para, tipo, criado_em)` com `PRIMARY KEY (de, para)` e índice por `para`: **`para` depende de `de`** (a seta vai de quem vem antes para quem espera); `tipo` é `precisa_de_resultado` ou `depois_de`. `pedidos` ganha `papel TEXT` (`pesquisador`, `checador`, `redator`, `porta_voz`; NULL = pedido comum) e o índice `ix_pedidos_pai` sobre `pai_id` (da 067, que ninguém lia). Padrão da 067/085: sem FK e sem CHECK (profundidade, filhos, ciclo, linhagem, porta-voz único e orçamento reservado são do domínio, `modules/pedidos/domain/colaboracao.py`, e os limites vêm de `pedidos.colaboracao` na config), tempo em texto ISO. A F1 só grava e valida: o laço (`laco.py`) não lê nada disto. Simulado em `tests/test_pedidos_colaboracao_api.py`; PostgreSQL pela mesma fábrica quando `TEST_DATABASE_URL` existe |
| 097 | tamanho_do_prompt_do_ator | Item 31.35 (Fase 31; número reservado em `.claude/reservas.md`, 04/10; as 089 a 096 são de outros itens). Só mede: `ai_calls.prompt_arvore_chars`, `prompt_historico_chars` (caracteres da árvore e do histórico que foram ao ator) e `prompt_podados` (elementos da barra do navegador tirados do prompt), NULOS fora da decisão do ator. Só `ADD COLUMN`. Simulado em `tests/test_tamanho_do_prompt_do_ator.py` |
| 098 | etapa_opcional | Item 31.36 (Fase 31; número dado pela orquestradora em 04/10). `steps.opcional INTEGER`: 1 = etapa de limpeza opcional (sem efeito, sem saídas, sem for_each, sem commit_guard; o parsing garante); falhar a leva a `skipped` e o objetivo segue, e a dependência e o progresso a contam como resolvida. NULO = a etapa de sempre. Só `ADD COLUMN`. Simulado em `tests/test_etapa_opcional.py` |
| 099 | pedido_cancelado_motivo | Item 28.22 (Fase 28; número dado pela orquestradora em 04/10). `pedidos.cancelado_motivo TEXT`: o motivo que a pessoa deu em `POST /api/pedidos/{id}/cancelar` (até 200), antes aceito e descartado. Texto livre de pessoa: só o `GET /api/pedidos/{id}` o devolve, fora do `view()` e do evento `pedido.updated`. Nulo sem motivo, no legado e nos descendentes cancelados em cascata. Só `ADD COLUMN`. Simulado em `tests/test_pedidos_api.py` |
| 100 | teto_de_autonomia | Item 28.23 (Fase 28; número dado pela orquestradora em 04/10). `runs.teto_de_autonomia TEXT`: `observar` (a etapa com efeito é recusada no plano, `plan.refused` com motivo `acima_da_autonomia`, e o despacho para antes dela e do preenchimento dela, como defesa), `preparar` (a etapa com efeito exige aprovação, qualquer que seja a política da persona) ou `agir`. NULO = como hoje, inclusive as execuções anteriores. Sem CHECK; o domínio valida (`RunCreate`). Só `ADD COLUMN`. Simulado em `tests/test_teto_de_autonomia.py` |
| 101 | canal_anexos | Item 28.24, fatia F1 (anexos nos canais; número reservado pela orquestradora em 04/10). `canal_anexos`: uma linha por anexo recebido (`direcao='entrada'`, `entrada_id` = a `canal_entradas` da mensagem, sem FK) ou enviado (`'saida'`), com `sha256`, `mime` DETECTADO pelo conteúdo, `bytes`, `estado` (`guardado`|`recusado`|`apagado`), `motivo_recusa` (português simples), `criado_em` e `apagado_em`. O arquivo NÃO mora no banco: fica em `data/anexos/<2 primeiros do sha256>/<sha256>.<ext>` (fora do Git), nomeado só pelo sha256 e pela extensão da lista de tipos aceitos (o nome do remetente nunca é usado); dois anexos iguais são duas linhas e um arquivo. A faxina por retenção do 28.16 apaga o arquivo (só quando nenhuma outra linha `guardado` o usa) e depois as linhas vencidas. `estado = 'pendente'` (a mensagem foi gravada e o anexo ainda não foi baixado; a volta seguinte o retoma uma vez) é o único estado com `ref_externa` e `mime_declarado` preenchidos. Índices por `(canal, entrada_id)`, `(canal, criado_em)` e `sha256`. Padrão da 085: sem FK e sem CHECK nos vocabulários, tempo em texto ISO-8601. Simulado em `tests/test_canais_anexos.py`; PostgreSQL pela mesma fábrica quando `TEST_DATABASE_URL` existe |
| 102 | decisoes_automaticas | Item 28.25 (Fase 28; número reservado pela orquestradora em 04/10; a 101 está no PR #267). Tabela nova `decisoes_automaticas`: o registro único do que a plataforma decidiu sozinha (pedido do dono, 30.55). `fila` (`pergunta`, `objetivo`, `aprendizado` ou `pedido`, com `CHECK`), `item_ref` (id na fila dona), `origem_ref` TEXT NOT NULL **UNIQUE** (a identidade do FATO: o mesmo evento relido, ou visto por duas réplicas, é UMA linha), `regra` (`31.43-pergunta-24h`, `auto:qa_revisar v1`), `efeito` (frase curta em português), `fatos` (JSON plano e curto, sem dado pessoal), `decidida_em`, `resumida_em` (quando entrou num resumo do Telegram; NULO = ainda não) e `desfeita_em`, `desfeita_por`, `motivo_do_desfazer` (o desfazer do dono, que vale `avisos.decisoes_automaticas.desfazer_dias`). Tabela nova `decisoes_automaticas_estado` (`chave` PK, `valor`): o cursor de cada fonte do adaptador e a hora do último resumo, para "uma mensagem por janela" sobreviver a um reinício. Escrevem o adaptador do 28.25 e a porta `registrar_decisao` (`app/shared/decisoes.py`), sempre idempotentes. SQLite e PostgreSQL. |
| 103 | canal_anexos_descricao | Item 28.24, fatia F3 (a IA lê a imagem que o dono mandou; número reservado pela orquestradora em 04/10). Só `ALTER TABLE canal_anexos ADD COLUMN`, todas nulas: `descricao` (o texto do modelo de visão, já passado pelo redator de credencial dos textos do canal), `lida_em`, `modelo_leitura` (o modelo que respondeu), `custo_usd` (REAL, tokens × `ai.prices`) e `tokens_entrada`/`tokens_saida`. Existem para a mesma imagem não ser paga duas vezes: com `descricao` preenchida a leitura devolve o texto gravado e custo 0, sem chamar o provedor; a leitura que falha não grava nada. A 102 é do 28.25; o migrador aplica por nome ordenado e não exige número contíguo. Simulado em `tests/test_canais_leitura_anexo.py`; PostgreSQL pela mesma fábrica quando `TEST_DATABASE_URL` existe |
| 104 | excecoes_de_politica | Item 30.65 (número dado pela orquestradora em 04/10; a 102 é a do 28.25 e a 103 a do 28.24 F3). `excecoes_de_politica`: exceção de uso único à regra de uma conta por alvo (ADR-055), uma linha por exceção, com `regra` (`uma_conta_por_alvo`, CHECK), `profile_id` (perfil de origem), `alvo` normalizado, `capability`, `motivo`, `autorizacao`, `autor`, `autor_com_sessao` (só com sessão o cartão diz "autorizada pelo dono"), `criada_em`, `expira_em` (a rota recusa mais de 72 h), `step_id`/`presa_em` (a etapa que a porta casou), `usada_em`/`interaction_id` (gasta no `open_effect`), `vencida_em`, `em_uso_em`/`etapa_do_uso`/`run_do_uso` (a reserva e quem a usou, nunca limpos) e `encerrada_em`/`encerrada_por`/`encerramento` (`recusada` no cartão ou `revogada` pela rota, CHECK). Índices por `(profile_id, alvo, capability)` e `step_id`. Sem FK, tempo em texto ISO-8601. Simulado em `tests/test_excecao_de_politica.py` |
| 105 | aprovacao_no_plano | Item 30.61 (número dado pela orquestradora em 04/10). Só ADD COLUMN em `pending_approvals`: `chave_sha256` (a chave do item aprovado no plano, `social/chave_da_aprovacao.py`), `chave_v` (a versão do formato, que também entra no hash), `origem` (`plano` ou `execucao`, padrão `execucao` para as linhas que já existem), `expires_at` (validade do sim do plano; nula nas de execução), `plan_version` (a versão do plano do objetivo no gesto) e `midia_sha256` (o sha256 dos bytes da imagem, congelado no gesto). Índice por `chave_sha256`. Simulado em `tests/test_porta_do_plano.py` |
| 106 | pedidos_autor | Item 28.31, fatia F2a (número reservado pela orquestradora em 04/10 20:11Z). Só `ALTER TABLE pedidos ADD COLUMN`, nulas e sem CHECK: `criado_por_tipo` (`dono`, `convidado`, `frente`, `ia` ou `desconhecido`; o vocabulário e a regra ficam em `pedidos/domain/autor.py`) e `lote` (a `idempotency_key` quando começa com `lote:`). Decididas uma vez, na criação: `lote:` na chave é `frente`, sempre; senão, operador na lista declarada `pedidos.operadores_do_dono` (ou `trello:<membro_dono>`) é `dono`, outro operador é `convidado` (o login aceita qualquer nome) e sem operador é `desconhecido`. Sem backfill: o pedido anterior fica nulo e o aviso dele sai pelo id curto. Simulado em `tests/test_pedidos_autor.py`; PostgreSQL pela mesma fábrica quando `TEST_DATABASE_URL` existe |
| 107 | portal_contatos | Item 29.77 (site institucional, ADR-075; número reservado pela orquestradora em 04/10; 104 a 106 são de outras frentes). Tabela nova `portal_contatos`: uma linha por contato do formulário do site, gravada ANTES de chamar a Canais. `nome`, `empresa`, `telefone` e `mensagem` são dado pessoal do visitante e somem com a linha 180 dias depois de `criado_em` (o prazo escrito no aviso de privacidade da página; o laço `portal-contatos`, sob a trava `avisos`, apaga a cada hora, mesmo com o contato desligado). `cliente_hash` é HMAC do cliente (IP da borda; IPv6 pelo /64) com o sal de `settings` chave `portal.sal`, que nasce na primeira leitura e não gira; o IP cru nunca é gravado. `estado`: `pendente` (sem entrega ainda, canal desligado ou ausente), `entregue`, `retido` (teto de avisos por hora; o motivo `teto_por_hora` fica na linha depois de entregue, é o que o resumo conta) e `descartado` (teto diário, `campo_invalido` da Canais ou `falhas_demais` depois de 10 falhas; descartar apaga nome, empresa, telefone e mensagem, por qualquer motivo). Índices por `criado_em`, por `(cliente_hash, criado_em)` (a taxa por cliente) e por `(estado, id)` (o reenvio, FIFO). Nada daqui vai a `runs`, `pedidos`, aprovações ou IA. Simulado em `tests/test_portal_contato.py`; PostgreSQL pela mesma fábrica quando `TEST_DATABASE_URL` existe |
| 108 | imagem_feita_por_ia | Item 29.81 (número reservado pela orquestradora em 04/10; a 107 é de outra frente). Só `ALTER TABLE persona_images ADD COLUMN feita_por_ia INTEGER` (nula): o dono diz se a foto que ENVIOU foi feita por IA (1 = sai com o rótulo de IA do Instagram, 0 = foto real, nulo = não informado). Só o `upload` a usa: a gerada e a importada saem sempre com o rótulo (29.79). As linhas antigas ficam nulas, e o rótulo de cada uma é o de antes (upload sem, gerada e importada com): nenhum rótulo "true" vira "false" pela migração. A correção (`PUT /personas/{id}/images/{image_id}/feita-por-ia`) regrava o `rotulo_ia` das etapas abertas que publicam a imagem (`Repository.ressincronizar_rotulo_ia`), e a chave da aprovação muda com ele |
| 109 | portal_exclusoes | Item 29.83 (exclusão a pedido do titular, ADR-075; número reservado pela orquestradora em 04/10; a 108 é da Aprendizado). Tabela nova `portal_exclusoes`: uma linha por "Apagar definitivamente" no painel, gravada na MESMA transação do DELETE em `portal_contatos`. `executado_por` (o operador da sessão), `pedido_por` (`formulario`, `telefone` ou `outro`, CHECK; sem texto livre), `ids` (JSON dos ids apagados), `mantidos` (JSON `[{id, motivo}]`), `mensagens_apagadas` e `mensagens_a_mao` (contagens). Não guarda NADA do titular (nem nome, nem telefone, nem hash dele, nem a mensagem) e por isso não tem prazo de retenção: é a prova do atendimento. Índice por `executado_em`. Simulado em `tests/test_portal_exclusao.py` |
| 110 | passou_a_porta | Item 31.64, S1 da revisão do #350 (número reservado pela orquestradora em 05/10 02:02Z). `steps.passou_a_porta INTEGER NOT NULL DEFAULT 0`: a porta de política (`_policy_gate`) grava 1 quando libera o efeito da etapa, na mesma passada sem `await` da regra do objeto na família (31.53); nunca volta a 0. A regra conta SEMPRE a irmã do mesmo pedido que passou enquanto ela está em estado aberto (`pending`, `ready`, `running`, `verifying`, `retry_wait`, `waiting_user`, `uncertain`; S2: a concluída sai pela saída gravada, com a janela da regra, e falha, cancelamento e `skipped` não contam) e aplica "só as mais antigas" (`started_at`, `id`) apenas entre as que não passaram: a retomada com a data da primeira tomada e o relógio de cada máquina deixam de decidir. Só `ADD COLUMN`. Simulado em `tests/test_passou_a_porta.py` e `tests/test_familia_por_objeto.py` |
| 111 | execucao_aguardando_pessoa | Item 29.93 (número reservado pela orquestradora em 05/10 06:44Z). Só dados, sem esquema (não há CHECK em `runs.status`): `UPDATE runs SET status='awaiting_person'` nas execuções em `completed_with_issues` com algum objetivo `waiting_user`, o estado em que o `recompute_run` as deixa desde o 29.93. `finished_at` fica (é de onde o vencimento do 31.50 conta). Execução só com `uncertain` não muda. Idempotente; 0 linhas no banco do central em 05/10 (medido em `mode=ro`). Simulado em `tests/test_aguardando_pessoa.py` |
| 112 | sessao_status_since | Item 29.100, nota da leitura do 29.96 (número reservado pela orquestradora em 05/10 07:00Z). `account_sessions.status_since TEXT`: desde quando a sessão está assim (a mudança de estado e, no `unknown`, a chegada ao teto). `SocialRepository.set_account_session` grava a hora da gravação quando o estado muda (ou a linha nasce) e, no `unknown`, quando a série chega ao teto do aparelho (a parada, P1 da leitura do #384); fora disso MANTÉM a anterior quando o mesmo estado é regravado (reobservação abaixo do teto, "Verificar conta" no teto, invalidação de quem já estava `unknown`). A decisão vai num `CASE` dentro do próprio upsert (P2); `updated_at` segue sendo a última gravação. Preenchimento: `updated_at` nas linhas que não são `session_ready` (numa sessão parada, a última gravação é a parada); `session_ready` fica nula, e o painel cai em `verified_at`. Chega ao REST como `SessionInfo.status_since` (adendo v1.51) e é o `desde` do item da sessão em Pendências. Só `ADD COLUMN` e um `UPDATE` limitado às nulas. Simulado em `tests/test_sessao_status_since.py` |
| 113 | execucao_assentada_em | Itens 29.93 e 29.103, #382 (número reservado pela orquestradora em 05/10 07:45Z; a 112 é do #384). `runs.assentada_em TEXT`, nula: a marca de que a execução já foi assentada (digest, trava de rascunho, pedidos). Compare-and-set `UPDATE … SET assentada_em=? WHERE id=? AND assentada_em IS NULL AND status IN (finais)` em `Repository.marcar_assentada`: só quem grava assenta, uma vez, mesmo com dois backends. Preenchida na migração para toda execução já em estado final (`completed`, `completed_with_issues`, `failed`, `cancelled`) com `COALESCE(finished_at, started_at, created_at)` (`runs` não tem `updated_at`); as que a 111 levou a `awaiting_person` ficam nulas. Zerada só pelo `set_run_status`, ao ir a estado não final que não seja `cancelling`. Simulado em `tests/test_aguardando_pessoa.py` |
| 115 | receita_nao_aplicavel | Item 30.80 (número reservado pela orquestradora em 05/10; a 114 é do 30.76). `recipes.nao_aplicavel_seguidas INTEGER NOT NULL DEFAULT 0`: quantas vezes SEGUIDAS a receita "não se aplicou" (a ação 1 não achou o alvo, antes de agir, e a etapa terminou comprovada pela IA: a tela de partida era outra). Não conta como falha (`RecipeStore.nao_aplicavel`), e a 3ª seguida conta como falha comum, para um 1º seletor quebrado não ficar isento da quarentena. Zera no ok e na falha (`RecipeStore.result`). Simulado em `tests/test_receita_nao_aplicavel.py` |
| 116 | ref_publico_do_fluxo | Item 30.83 (número reservado pela orquestradora em 05/10). `flows.ref_publico TEXT` e o índice único `ux_flows_ref_publico`: a referência PÚBLICA do fluxo, `f-` mais 12 hex aleatórios (`secrets`), nunca derivada do resumo, do comando nem do `match_key` (o id antigo era o slug do resumo literal e podia trazer nome). O fluxo novo nasce com `id = ref_publico` (`flows.ref_aleatoria`); o que já existe mantém o id e ganha a referência na subida (`flows.preencher_refs_publicas`, idempotente), porque o SQL portátil não sorteia igual nos dois bancos. Simulado em `tests/test_ref_publico_do_fluxo.py` |
| 117 | versao_do_texto_do_parecer | Item 30.76 (número reservado pela orquestradora em 05/10 22:36Z; a 114 ficou fora da sequência). `learning_reviews.instrucao_versao TEXT` nulável: a versão do texto da instrução que a IA leu no parecer do curador (`VERSAO_DO_TEMPLATE`, hoje `curador-v2`), dita pelo adaptador do hub. A `template_versao` segue sendo a forma do dossiê (`dossie-v1`) e não muda de sentido, para não mexer no hash nem na elegibilidade (N1 da leitura do 30.73). NULL nas linhas antigas, nas recusas, no curador simulado da porta e no rótulo de intenção. Simulado no SQLite (`test_parecer_versao_do_texto.py`); PostgreSQL pela mesma fábrica quando `TEST_DATABASE_URL` existe |
| 118 | fuso_da_ultima_atualizacao | Item 30.77 (número reservado pela orquestradora em 05/10 22:41Z). `device_app_state.last_update_offset TEXT` nulável: o fuso do aparelho (`date +%z`, ex. `-0300`) lido no mesmo shell do `dumpsys package` que deu o `last_update_time`, que vem no fuso do aparelho e sem fuso escrito. Com ele, `versao_estavel_na_execucao` sabe a hora da atualização em UTC e usa 1 h de margem; NULL (linhas antigas, leitura sem o fuso) segue a folga de 12 h do 30.74. Simulado no SQLite (`test_fuso_na_inspecao.py`); PostgreSQL pela mesma fábrica quando `TEST_DATABASE_URL` existe |
| 119 | origem_do_treino_na_falha | Item 31.111 F1 (número reservado pela orquestradora em 06/10). `training_sessions.origin_run_id`, `origin_step_id` e `origin_attempt_id` (TEXT, sem chave estrangeira de propósito: a limpeza de execuções velhas não apaga nem trava a sessão) e o índice `ix_training_sessions_origin`: a execução, a etapa e a tentativa que falhou e deram origem a uma sessão de ensino. Vazias na gravação comum. Simulado em `tests/test_treino_a_partir_da_falha.py` |
| 120 | pacotes_aceitos_da_etapa | Item 31.123 (número reservado pela orquestradora em 06/10). `steps.pacotes_aceitos` (TEXT, lista JSON): os pacotes, além do app da etapa, em que a tela comprova a conclusão (a busca do Configurações é de outro pacote). O ensino a preenche com os pacotes vistos na demonstração, sem o systemui, o lançador e os apps cadastrados. NULO = só o app da etapa (inclusive todas as anteriores). Pacote desconhecido nunca comprova. Só `ADD COLUMN`. Simulado em `tests/test_etapa_pacotes_aceitos.py` |
| 121 | elementos_da_tela_do_treino | Item 31.122 F2 (número reservado pela orquestradora em 06/10). `training_inputs.screen_elements` (TEXT, lista JSON compacta `{t, d, r, b}`): os elementos da tela em que a entrada do ensino foi feita, para conferir a pós-condição que já vale na partida pela regra do verificador (`contains_text`, `find_selector`). Sem senha, sem o texto do campo editável, sem texto com cara de segredo, sem tela sensível; até 400 elementos. Uso interno: fora do GET da sessão. O `save` e o reparo do 31.118 marcam nela o dado da persona. NULO = entrada anterior (cai em `screen_lines` + `screen_title`). Só `ADD COLUMN`. Simulado em `tests/test_treino_partida_f2_e_sequencia.py` |
| 122 | nascido_de_prova | Item 31.130 (número reservado pela orquestradora em 06/10). `training_sessions.nascido_de_prova` e `flows.nascido_de_prova` (INTEGER, 1 = sim): a sessão aberta como prova (`nascido_de_prova: true`, adendo v1.87) e o fluxo que ela salvou. NULO = uso real (inclusive tudo o que é anterior). Os fluxos de prova anteriores se marcam pelo id com `scripts/marcar-fluxo-de-prova.py`. Só `ADD COLUMN`. Simulado em `tests/test_fluxo_nascido_de_prova.py` e `scripts/tests/test_marcar_fluxo_de_prova.py` |
| 123 | comando_remoto | Item 29.154 (número reservado pela orquestradora em 06/10; ADR-079). Tabela nova `worker_comandos` (um registro por comando remoto: `id` = `exec_id`, `worker_id`, `requested_by` da sessão, `idempotency_key` UNIQUE, `modo`, `linha_redigida` (nunca a crua), `pasta`, `timeout_s`, `state` `created`/`dispatched`/`running`/`succeeded`/`failed`/`timed_out`/`cancelled`/`uncertain`/`rejected`, `exit_code`, `stdout`/`stderr` já redigidos e cortados, `truncated`, `duration_ms`, `reason`, carimbos; índices por worker e por estado; retenção de 30 dias e 200 por worker) e `worker_comando_remoto(worker_id PK, ligado, changed_by, changed_at)`, o interruptor por worker (sem linha = desligado). Só `CREATE TABLE`. |
| 124 | operacoes | Item 31.154 (número reservado pela orquestradora em 06/10; prova de 07/10, adendo v1.94). A operação com N agentes: tabela `operacoes` (`id`, `command`, `app_id`, `acao_final` `preparar`/`executar`, `max_usd` obrigatório, `assunto` e `fontes` (JSON) da pesquisa externa da frente de aprendizado, `status`, `idempotency_key` UNIQUE com `corpo_sha256`, `criada_por`, carimbos) e `operacao_alvos` (PK `operacao_id`+`profile_id`: `account_id`, `instance_id`, `run_id` (nulo = parou na criação), `estagio`, `estado`, `motivo`, `marcas` JSON dos estágios marcados de fora); `runs.operacao_id` com índice. Cada alvo roda numa execução própria (`objectives` é um por aparelho por execução). Só `CREATE TABLE`/`ADD COLUMN`. Simulado em `tests/test_operacoes.py` |
| 127 | operacoes_parametros | Item 31.154 (adendo v1.95; número reservado pela orquestradora em 06/10). `operacoes.parametros` (TEXT, objeto JSON nome → valor): os parâmetros fixos que a operação passa a cada execução de alvo (`username`, `caption_contains`), com estes nomes no plano (`taskqueue/plano_da_operacao.py`), para a receita ensinada casar. NULO = sem parâmetros fixos (inclusive toda operação anterior). Só `ADD COLUMN`. Simulado em `tests/test_migracao_127.py` e `tests/test_plano_da_operacao.py` |

| 125 | conhecimento_da_operacao | prova30 A1 (número dado pela orquestradora em 06/10). O conhecimento comum de uma OPERAÇÃO (`operacoes`, 124, da Jev) mora na memória e nas observações do pedido: `pedido_memoria` e `pedido_observacoes` ganham `operacao_id` (TEXT, SEM chave estrangeira, como `runs.pedido_id` na 067), e `pedido_id` deixa de ser obrigatório, com CHECK de exatamente um dos dois. `pedido_memoria` ganha a procedência de cada fato: `origem` (`operador`/`ocorrencia`/`leitura`/`pesquisa`, CHECK, padrão `ocorrencia`), `confianca` (`confirmado`/`hipotese`, CHECK, padrão `confirmado`), `evidencia` (JSON: ids das observações) e `frescor_ate` (UTC; NULL = não vence). Índices únicos PARCIAIS `(operacao_id, chave)` e `(operacao_id, alvo, nome)` `WHERE operacao_id IS NOT NULL`: é o segundo que faz a leitura do alvo ser gravada uma vez por operação. SQLite reconstrói as duas tabelas (molde da 076, nada aponta para elas); PostgreSQL usa `DROP NOT NULL` e `ADD COLUMN/CONSTRAINT`. Simulado em `tests/test_conhecimento_da_operacao.py` |

| 126 | troca_de_conta | Item 31.155 (número reservado pela orquestradora em 06/10; ADR-080). `DROP INDEX ux_binding_conta_do_app_no_aparelho` (o índice único da 051, uma conta por app em cada aparelho): ele não lê o `sessao.yaml`, e o app que declara a troca de conta aceita duas personas no mesmo aparelho. A regra D2-a fica no repositório (`quem_ja_serve`), que relaxa só para esse app. Nenhuma linha muda. Simulado em `tests/test_migracao_126.py` e `tests/test_troca_de_conta.py` |
| 128 | livro_escopo_de_assunto | Item 31.200 (número dado pela orquestradora em 06/10; adendo v1.113). `learning_items.scope_subject` (TEXT NOT NULL DEFAULT ''): o assunto canônico a que o item vale (`learning/domain/livro.assunto_canonico`); '' = qualquer assunto, e todo item anterior fica assim. O índice único parcial `ux_learning_items_vivo` (055) é recriado com o mesmo nome incluindo o assunto: o assunto faz parte da identidade do item. A chave do veto (`scope_key`) do item sem assunto não muda. Só `ADD COLUMN` e o índice (`DROP INDEX IF EXISTS` + `CREATE UNIQUE INDEX … WHERE`, igual nos dois bancos). Simulado em `tests/test_migracao_128.py` e `tests/test_livro_escopo_de_assunto.py` |
| 129 | prova_da_candidata | Item 31.271 (número dado pela orquestradora em 07/10; adendo v1.128). `recipes.ultima_consulta_em` e `recipes.ultima_consulta_resultado` (TEXT, anuláveis): a última consulta de cada receita (`concordou`, `divergiu`, `nao_aplicavel`, `outro_escopo`, `quarentena`), gravada por `RecipeStore.find`/`shadow`/`nao_aplicavel_em_prova` e lida pelo `prova_da_candidata` do Livro (31.270). NULL = nunca consultada desde a migração. Só `ADD COLUMN`, igual nos dois bancos; nenhuma linha anterior muda. Simulado em `tests/test_migracao_129.py` e `tests/test_prova_da_candidata.py` |
| 130 | etapa_exploratoria | Item 31.273 (número concedido pela orquestradora em 07/10; adendo v1.130; ADR-084). `steps.exploratoria` (INTEGER, anulável): 1 = etapa criada por EXPLORAÇÃO, porque o catálogo do app não cobria o pedido; NULO = a etapa de sempre (inclusive as anteriores). Só proveniência: não muda a execução, a aprovação nem a receita. Quem lê: `FlowStore.etapas_descobertas` (a oferta da etapa descoberta ao planejador) e o selo `nasceu_de_exploracao` do Livro. A 131 é da Jev (ADR-087) e foi aplicada antes; o vão não atrapalha o migrador (aplica o que falta em `schema_migrations`). |
| 131 | conta_planejada | Item 31.281 (número concedido pela orquestradora; ADR-087, adendo v1.132). Colunas ADITIVAS em `profile_accounts`: `provisioning_state` (padrão `confirmada`: toda linha anterior entra confirmada), `desired_handle`, `provisioning_detail`, `resume_state`, `confirmed_at`, `confirmation_evidence` (JSON `{kind, ref}`). `handle` continua sendo o endereço confirmado. |
| 132 | ponte_igfarm | Ponte android ⇄ igfarm (número concedido pelo usuário em 09/10; a 131 é da Jev, ADR-087 / 31.281, e não é tocada). Três tabelas NOVAS, só `CREATE`: `persona_reservas` (sugestão persistente por pessoa: `email_sugerido` e `username_sugerido` únicos sem distinção de caixa, `imagem_id`, `reservada_em` NULL = só sugestão / preenchida = reserva vigente até `expira_em`, TTL 24 h), `caixas_email` (o e-mail da conta registrada e a referência da senha dele no cofre: `secret_ref`/`key_id`, nunca texto) e `contas_igfarm` (id da conta no igfarm e @ registrado; `UNIQUE(profile_id, lower(username_registrado))` é a chave de idempotência do POST). Cascata pela pessoa e pela conta. Não toca `profile_accounts`; a convergência com a conta planejada do ADR-087 fica para depois. Simulado em `tests/test_migracao_132.py`, `tests/test_personas_pendentes_api.py` e `tests/test_contas_igfarm_api.py` |
| 133 | egresso_igfarm | Colunas ADITIVAS em `contas_igfarm`: `proxy_secret_ref`/`proxy_key_id` (rastreio da senha do proxy), `ip_criacao` (IPv4 público da criação, vai a `params.egress_esperado`) e `reaquecer` (flag de observabilidade, 0/1). O device sai pelo mesmo IP da criação (perfil `igfarm-{account_id}` com `egress_esperado`), e divergência na medição marca `reaquecer=1`. Sem FK, sem índice, sem backfill. Simulado em `tests/test_egresso_igfarm.py` |
| 134 | persona_de_teste | Coluna ADITIVA `instagram_profiles.teste` (`INTEGER NOT NULL DEFAULT 0`, 31.314): `1` = persona de TESTE, fora da seleção automática, das operações em lote, das contagens do painel e dos avisos ao dono (`app/contracts/persona_de_teste.py`). A migração marca, por id, a persona que o Portal criou em 10/10 para o 31.283 (`ig-d3n4tia1rHELrY10`); em banco que não a tem o UPDATE não toca linha nenhuma |
| 135 | indice_dos_eventos_por_instancia | Índice ADITIVO `idx_events_instance_kind_id` em `events(instance_id, kind, id)` (31.307): serve ao filtro por aparelho e à ordem por id da consulta de `DeviceManager._ler_dto_persistido`, que parou o laço de eventos por 70 s no boot de 10/10/2026. Nenhuma linha é tocada; `CREATE INDEX` trava a escrita em `events` enquanto roda (o deploy aplica com o backend parado) |
| 136 | motivo_do_bloqueio | Coluna ADITIVA `contas_retiradas.motivo_do_bloqueio` (`TEXT`, JSON, 31.322): o motivo da retirada por bloqueio como dado (egresso esperado × medido, IPs distintos desde a criação, minutos até o primeiro login, trecho da tela), sem segredo e sem o @. Lápides anteriores ficam com `NULL`. Simulado em `tests/test_motivo_do_bloqueio.py` |


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

## Uma conexão por thread e uma trava só de escrita (31.320, 10/10/2026)

- **O defeito.** `Database` tinha UMA conexão e UM `RLock`. Uma leitura de 5 s numa thread do pool segurava o lock, e o `emit` (INSERT em
  `events`) e o `_tick` do scheduler, que rodam na thread do laço de eventos, esperavam por ele: o laço ficou sem batida 10 a 71 s quatro vezes
  em 10/10 (16:33Z, 18:06Z, 18:31Z, 18:50Z; `data/logs/laco-travado-*.txt`). Mover cada chamada para `asyncio.to_thread` (31.307) não basta,
  porque o `emit` e o `_tick` passam pela conexão única de qualquer jeito.
- **O modelo.** Cada thread abre a sua conexão na primeira chamada (`threading.local`, `check_same_thread=False`, os mesmos PRAGMAs: WAL,
  `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`) e a fecha ao acabar (finalizador do objeto da thread). `_tx_depth`, `_suspeita` e
  os efeitos de `depois_do_commit` são da thread; `db._conn`, `db._tx_depth` e `db._suspeita` seguem existindo como propriedades da thread atual
  (os testes que injetam conexão frágil dependem disso). No SQLite em WAL, a leitura de uma conexão não bloqueia leitura nem escrita de outra, e
  vê o último estado COMITADO, nunca o meio da transação alheia.
- **A trava de escrita** (`Database._escrita`, só SQLite; no PostgreSQL o servidor arbitra). O SQLite tem um escritor só; sem uma trava dentro do
  processo, o `emit` do laço esperaria o `busy_timeout` e estouraria `database is locked`, perdendo evento. `tx()` e `savepoint()` de fora a pegam;
  `execute/query/one` só pegam se a instrução não for leitura. **Leitura = o primeiro token (depois de espaço e comentário) é `SELECT` ou `EXPLAIN`**;
  `WITH`, `PRAGMA`, `INSERT … RETURNING`, `(SELECT …)` e comentário não fechado contam como escrita (errar para esse lado custa uma fila a mais).
- **Quem segura.** A trava guarda `(thread, chamador, desde)`. O aviso de consulta lenta na thread do laço passou a dizer quem a SEGURAVA
  (`a trava de escrita estava com <thread> (<arquivo:linha em função>), presa há X s`), e uma posse acima de 2 s vira aviso à parte
  (`a trava de escrita do banco ficou presa X s por …`).
- **Limite honesto.** Escrita contra escrita segue numa fila só (limite do SQLite): uma escrita LONGA numa thread ainda faz o laço esperar a vez. O ganho
  cobre leitura lenta e escrita curta, que são os quatro despejos de 10/10.
- **Etapa 2: apagar em fatias.** `Database.apagar_em_fatias(tabela, onde, params, lote, pausa_s)` apaga em comandos curtos (`DELETE … WHERE id IN (SELECT id … ORDER BY id LIMIT ?)`)
  com uma pausa entre eles: sem a pausa, quem acabou de soltar a `threading.Lock` a pegaria de novo (ela não é justa), e a fatia não serviria de nada. Usada na purga de `events`
  (2000 linhas, 50 ms) e nas de `commands`, `ai_calls` e `measurements` da retenção de 6 h. Chamar só de thread do pool. Medido em 10/10 (somente leitura, central): `measurements`
  195.255 linhas (61.783 com mais de 7 dias), `events` 67.574, `commands` 1.483, `ai_calls` 4.079. Os saldos (`conciliacao.atualizar`, a cada 10 min) só leem `ai_calls`:
  milissegundos, sem mudança.
- **Teto de conexões.** Uma por thread viva que já falou com o banco: o executor padrão do `to_thread` (`min(32, CPU+4)`), o laço, o uvicorn e os
  laços de fundo. `LIMITE_DE_CONEXOES = 40` avisa no log (no máximo um por minuto). No PostgreSQL cada thread é uma conexão (`max_connections` padrão
  100); não há `psycopg_pool` de propósito (dependência e ciclo de vida novos sem ganho agora).
- **`:memory:`** vira um arquivo temporário da instância (apagado no `close()`): com conexão por thread, cada conexão em memória seria outro banco.
  O `tests/test_db.py` já usava arquivo em `tmp_path`; só o dublê `_Falso` fala em `:memory:`, sem abrir conexão.
- **No `/health`:** `database.open_connections` (conexões abertas) e `database.slow_queries_in_loop` (chamadas síncronas > 1 s na thread do laço desde o início do processo; zero é o esperado).
- **Prova** (`simulated`): `tests/test_db_conexao_por_thread.py`. Uma leitura artificial de 5 s numa thread (SQLite: função `sleep_s` na conexão;
  PostgreSQL: `pg_sleep`) com a thread do laço fazendo `query`, `INSERT` e `tx()` a cada 100 ms: o maior intervalo sem batida cai de ~4,7 s (modelo
  antigo, medido no mesmo teste) para ~0,1 s e nenhuma escrita se perde. `real` (central) e PG inteiro: `not_run`.

### O que ainda roda SQL na thread do laço (31.343, 11/10/2026)

O modelo acima tirou a espera de LEITURA atrás de outra thread, mas a chamada síncrona ao banco feita da thread do laço ainda é o gargalo quando o
disco engasga: o despejo de 11/10 02:07Z (a PG inteira rodando no mesmo host) pegou o `Scheduler._tick` na leitura `abandoned_steps` por 52 s
(`_manter_posse` → `adotar_abandonadas`, o ponto 10 do 31.307). O que mudou e o que sobra:

- **Pago:** a posse das etapas e das vagas de IA (`_manter_posse`: `renew_claims`, `ai_slots.renovar`, `adotar_abandonadas`) e a faxina dos contatos do
  avisos com o canal desligado (`_vencer_pessoais`) rodam em `asyncio.to_thread`. O `_loop` só salta para a thread quando a renovação vence.
- **Dívida do `_tick` ocioso, MEDIDA** (`tests/test_laco_sem_sql_exaustivo.py`, 1 aparelho, scheduler girando; ≈6 leituras por volta, ≈6 por segundo
  em produção): `_vigiar_contas_bloqueadas`, `active_runs`, `dispatchable_objectives` (duas por volta: `_tick` e `_rotate`),
  `WorkerRegistry.capacidade` e `limites_definidos` (via `_capacidade`), e a leitura da configuração viva quando o cache vence. Com trabalho na fila
  entram `promote`, `note_waiting`, `_portas_do_app` e o resto do despacho (por inspeção do código, ainda não medidas).
- **Fora do alcance do exame atual** (varredura estática de 10/10, `scratchpad/varredura.txt`, e leitura do código): `EventBus.emit` (INSERT
  síncrono, ~245 chamadores), `Lideranca.tomar` (um UPDATE por volta dos laços com mandato), o laço do `avisos` com o canal ligado (`enfileirar_evento`,
  `_lider`, `_faxina`), o `DeviceManager` periódico (`_monitor_loop`, `_metrics_loop`) e as sondas de saúde.
- **A catraca:** o exame espia o `Database` INTEIRO e registra toda chamada vinda da thread do laço, com a pilha. Chamada nova quebra o teste; dívida
  paga também (a lista `DIVIDA_DO_TICK` só encolhe). Cobre o que o harness gira (canais desligados, 1 aparelho): um laço que só existe com o canal
  ligado ou com trabalho na fila precisa do seu cenário no mesmo arquivo.
- **Próxima fatia:** uma foto das leituras do tick tirada numa thread antes do `_tick` (ele decide sobre a foto; as escritas, raras, ficam no laço) e o laço
  do avisos em thread. Só vale o custo se o `laco-por-hora` ainda mostrar o laço parado.

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

**Em paralelo, desde o 29.62.** Cada teste migra o próprio esquema, e a trava de migração
(`Database._trava_de_migracao`, `pg_advisory_lock`) era de uma chave só, global ao banco. Os workers do xdist entravam
em fila na migração: 1006 testes levaram 22 min com `-n 4` (03/10), mais que em série. A trava passou a ser POR
ESQUEMA (a forma de duas chaves, `_LOCK_MIGRACAO` e `hashtext(current_schema())`). Em produção há um esquema só, e dois
backends no mesmo banco seguem em fila. A forma de uma chave e a de duas não se enxergam: um backend antigo e um novo
subindo JUNTOS no mesmo banco não se esperariam, e o deploy para o antigo antes de subir o novo. Teste:
`tests/test_trava_de_migracao_por_esquema.py` (só com `TEST_DATABASE_URL`).

Medido em 04/10 (farm-pg, Idle, os mesmos test_learning_* + d1_fluxos + recuperacao_preserva_estado): 1016 passed em
10 min 49 s com `-n 4` (antes: 22 min) e 7 min 58 s com `-n 8`. Uma migração por teste custa ~3,2 s (criar o esquema
0,02 s, aplicar as 82 migrações 2,25 s, apagar 0,9 s): é a maior parte do que sobra.

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

**O esquema do worker (29.63).** Criar, migrar e apagar um esquema por teste custava ~3,2 s em PostgreSQL (medido
em 04/10: criar 0,02 s, 82 migrações 2,25 s, DROP 0,9 s). Agora cada processo da suíte (cada worker do xdist) migra
UM esquema uma vez, e a primeira abertura de banco de cada teste pelos ajudantes compartilhados (`make_config`,
`fake_skills.banco`) o recebe esvaziado (`tests/esquema_do_worker.py`). Esvaziar devolve o estado de logo depois da
migração:
- `TRUNCATE` de todas as tabelas, menos `schema_migrations`;
- as linhas que as migrações semeiam voltam de um esquema-gêmeo;
- as sequências voltam ao valor de então.

Antes de esvaziar:
- as conexões que sobraram do teste anterior naquele esquema são encerradas (marcadas por `application_name`);
- a estrutura (colunas, índices, restrições, gatilhos, funções, visões e `schema_migrations`) é conferida pela
  impressão; um teste que mexeu em DDL faz o esquema ser trocado por um novo.

Qualquer falha também troca o esquema: no pior caso o custo é o de antes. Ficam com esquema novo, como sempre:
- a segunda abertura de banco no mesmo teste;
- toda chamada direta de `_dsn_de_teste()`;
- os testes que trocam `MIGRATIONS_DIR` (os testes da própria migração).

`ESQUEMA_MODELO=off` desliga o reuso, para medir antes e depois no mesmo commit. `ESQUEMA_MODELO_RELATORIO=<pasta>`
grava, por worker, quantos reusos, migrações e trocas houve e por quê. Uma troca frequente é o achado a seguir, não
ruído.

Medido em 04/10 no farm-pg (PostgreSQL 17 de teste), mesmo commit e mesmos testes (os `test_learning_*` mais 2
arquivos), `-n 8`, prioridade Idle:

| Reuso | Resultado | Tempo | Horário |
|---|---|---|---|
| desligado (`ESQUEMA_MODELO=off`) | 1033 passed, 2 skipped | 8 min 49 s | 01:18:43–01:27:36Z |
| ligado | 1033 passed, 2 skipped | 2 min 51 s | 01:27:36–01:30:31Z |

Com o reuso ligado, os 8 workers migraram uma vez cada e reusaram o esquema 551 vezes. Nenhuma troca e nenhuma
conexão encerrada.

### O portão de PostgreSQL de uma suíte (29.53)

A suíte inteira em PostgreSQL não cabe colada num deploy. Em 03/10 ela não terminou na janela (27 % com `-n 8`),
e o Docker/WSL logo depois do deploy 13 disputou CPU com os emuladores: três aparelhos com conta foram reiniciados
pela regra de saúde. Desde então o portão é este:

- **Toda suíte roda PG dirigido:** os arquivos de teste que o lote toca. São os testes alterados mais os que
  importam um módulo alterado, ou passam pela API quando `api.py` muda. Roda com `-n 8`, em prioridade Idle, com o
  esquema do worker ligado. Exemplos: 436 e 2729 testes nas suítes 14 e 15; 4306 em 183 arquivos na 18; 2823 em 164
  arquivos na 19.
- **Aparelho de conta real não trabalha durante a suíte** (regra da orquestradora a partir da suíte 24): pausa de
  reparo em 01/03/06/13 do início da SQLite até o PG fechar (TTL renovado se preciso), e nenhuma frente roda
  objetivo de conta real nessa janela. Na suíte 23 a SQLite com `-n 8`, mesmo em Idle, deixou o convidado do 06 com
  load 52 e irq 0,50 em 2 vCPU (04/10, 09:52Z a 10:01Z).
- **O PG da suíte roda no `farm-pg-rapido`** (adotado pela orquestradora em 04/10/2026, suíte 25). É o mesmo
  `postgres:17-alpine`, descartável: dados em tmpfs de 4 GB (começa vazio a cada `docker start`, nada sobrevive
  ao `stop`), `fsync=off`, `synchronous_commit=off`, `full_page_writes=off`, `max_connections=200`, porta
  `127.0.0.1:55434`. Sobe só depois da SQLite, com pelo menos 7 GB livres no host (os 4 GB do tmpfs e a folga dos
  emuladores), e para no fim. O `farm-pg` (volume persistente, porta 55433, `fsync` ligado) fica para a suíte
  inteira em PG e para reproduzir o que só falha com durabilidade. Espere a primeira conexão aceita antes do
  pytest: a primeira rodada da suíte 18 deu 1913 erros "the database system is starting up".

  Desde o 29.99 o contêiner sobe por `scripts/pg-rapido.py`, sempre com a mesma configuração, mais WAL mínimo
  (`wal_level=minimal`, `max_wal_senders=0`, `max_wal_size=256MB`, `checkpoint_timeout=1min`), e só no loopback. O
  script recria o contêiner a cada parte (`--partes`, o tmpfs volta vazio) e espera a conexão pelo TCP. A cada 30 s
  ele amostra o tmpfs, o `pg_wal`, a `base`, os esquemas de teste e o tamanho de `pg_class`, `pg_attribute` e
  `pg_depend`; a 85 % do tmpfs, mata a árvore do pytest e para com uma linha. A amostra decide pelo `df`: o rc e o
  erro do `du` não contam, porque ele tropeça em arquivo que some no meio, o comum com esquemas criados e apagados;
  `wal` e `base` ilegíveis saem como "?". Fora do Windows, o pytest sobe num grupo de processos próprio, e o aborto
  mata o grupo inteiro (o K-099); no Windows, `taskkill /T`. Kill que falha aparece na linha do aborto; o pytest que
  já saiu não é morto de novo. Cada comando ao Docker tem prazo de 30 s (estourou: rc 124, a amostra falha e o laço
  segue); três amostras seguidas sem o `df` dão uma linha "SEM AMOSTRA do df há 90 s", porque o aborto fica cego; e
  o `docker run` que falha (a imagem que falta, com o `--pull=never`) diz o erro dele antes do "não aceitou conexão".
  Desde o 29.113: a parte interrompida (Ctrl-C, o `--resumo` que falha) mata a árvore do pytest antes de subir o
  erro, e diz "INTERROMPIDA"; o pytest que sai sozinho entre a amostra e o aborto fica com o rc dele, não "ABORTADA";
  o `docker stop` que falha avisa que o tmpfs pode seguir de pé e sugere o `docker rm -f`; o contêiner para também
  quando a rodada é interrompida. O pai morto de fora (o pwsh fechado) ainda deixa
  a árvore viva (até o 29.117): confira `python -m pytest` antes de outra rodada. Desde o 29.117, no Windows, o pytest nasce
  suspenso e entra num Job Object antes de rodar: o aborto termina o job (o `taskkill /T` saiu, porque montava a
  árvore pelo ParentProcessId e pegava processo alheio com PID reusado), e o pai morto de fora leva a árvore junto
  (KILL_ON_JOB_CLOSE). Se o pytest não entrar no job (o script já num job sem aninhamento), a parte diz isso e, no
  aborto, só o PID dele morre. Uma rodada por vez: a segunda sai com rc 10 antes de tocar o contêiner (mutex
  `Global\farm-pg-rapido`, `flock` fora do Windows; o erro 5 no `Global\` é o mutex de outra sessão e também é rc 10).
  - **Por quê.** Na suíte 35, os 467 arquivos juntos encheram os 4 GB: 181 failed, 823 errors, quase todos
    `DiskFull`. Pelos logs do contêiner, o estouro foi na `base/` (3688 erros ali e 1668 em `global/`, nenhum em
    `pg_wal`). O WAL ficou estável perto de 1 GB, com 30 segmentos reciclados por checkpoint, igual nas metades.
  - **As metades** (234 e 233 arquivos) passaram com pico de 1057 e 1077 MB: 928 e 944 MB de WAL, 129 e 133 de base.
  - **Com o WAL mínimo** (05/10, 07:37–08:01Z, metade 1, 234 arquivos, -n 8): "5524 passed, 8 skipped in
    1409.34s". O pico ficou em 388 MB (WAL 256, base 132), contra 1057 MB, em 46 amostras, todas válidas. O WAL
    ficou preso nos 256 MB.
  - **O que ainda falta.** Por que a `base` passou de ~3 GB só na rodada inteira é a medida pendente do 29.99: uma
    rodada inteira com o amostrador, na vez da orquestradora.

  | Suíte | Contêiner | Aceitar conexão | PG dirigido |
  |---|---|---|---|
  | 25 | `farm-pg-rapido` | 3,1 s | 7 min 49 s, 2234 testes em 119 arquivos (4,8 testes/s) |
  | 24 | `farm-pg` | ~6 min (recuperação com `fsync`) | 13 min 29 s, 3485 testes em 170 arquivos (4,3 testes/s) |

  O ganho é sobretudo a subida (−6 min por suíte); por teste, ~10 % mais rápido.
- **A suíte inteira em PG roda só em janela própria.** Nenhum aparelho com conta pode estar subindo, e ela nunca
  fica colada num deploy (nem antes, nem depois). O CI agendado (`ci.yml`, container descartável) é a rede de
  segurança dela.
- **Lote só de painel ou só de docs não roda PG.** Foi o caso da suíte 20.

**A SQLite da suíte do funil roda com `-n 6`** (adotado pela orquestradora em 04/10/2026, pela medida abaixo). A
suíte inteira em `-n 8` empurrava a carga para dentro dos convidados de 2 vCPU com conta real: o android-06 chegou a
load 12 sob a SQLite (e a 52, sem CPU, quando a IA também estava nele; ADR-053). Com `-n 6`, o 06 fica como no host
calmo, e a SQLite custa ~22 s a mais. Medida nas sondas de IRQ do android-06 (`measurements(kind='irq')`), com o 06
ocioso nas três janelas:

| Janela | load1 mediano | load1 máximo | irq mediano | irq máximo | SQLite |
|---|---|---|---|---|---|
| suíte 24, `-n 6` (10:27:40Z a 10:38:19Z) | 1,29 | 2,83 | 0,110 | 0,168 | 10 min 35 s, 9377 testes |
| suíte 22, `-n 8` | 3,88 | 12,29 | 0,165 | 0,438 | 10 min 13 s, 9248 testes |
| host calmo, sem suíte (10:22Z a 10:27Z) | 1,84 | — | 0,091 | — | — |

A suíte 23 (`-n 8`) ficou fora da comparação: a IA trabalhava no 06 e o Docker subia junto. O `-n 8` da tabela de
comandos do `CLAUDE.md` continua até o dono decidir a troca; esta regra vale para a suíte do funil. Na mesma suíte
24, o farm-pg levou ~6 min do `docker start` até aceitar conexão (10:38Z a 10:45:04Z): é o passo morto mais longo
do funil, e o motivo da medida do contêiner descartável na suíte 25.

**Os mais lentos (medido na suíte 21, 04/10/2026, 08:45:00Z a 08:52:40Z).** PG dirigido em 94 arquivos, `-n 8`, Idle,
esquema do worker ligado, sobre `integ/suite-21` (cebee288): 1890 passed, 3 skipped em 7 min 36 s de relógio.
`--durations=0` agregado por arquivo soma 3387 s de trabalho em 91 arquivos (setup + chamada + teardown, somados
entre os 8 workers):

| Arquivo | Soma | Testes | Setup |
|---|---|---|---|
| `test_workers.py` | 156 s | 44 | 5 s |
| `test_trello_leitor.py` | 150 s | 37 | 63 s |
| `test_instagram_auth.py` | 144 s | 34 | 18 s |
| `test_social_memory.py` | 120 s | 37 | 0 s |
| `test_trello_webhook.py` | 115 s | 35 | 59 s |
| `test_capabilities.py` | 110 s | 47 | 27 s |
| `test_rotation.py` | 100 s | 9 | 25 s |
| `test_release_lifecycle.py` | 96 s | 25 | 8 s |
| `test_caminho_rapido_2.py` | 89 s | 20 | 25 s |
| `test_sessao_declarada.py` | 89 s | 44 | 42 s |

Nenhum arquivo passa de 5 % do total: a etapa é longa pela soma, não por um vilão. Onde o setup pesa (Trello e
sessão declarada, ~40 % a 60 % do arquivo), o custo é montar o harness com o laço de avisos. `test_rotation` gasta
~11 s por teste (só 9 testes): é o primeiro a olhar se a etapa precisar encolher. O que é do host aparece como
variação entre rodadas do mesmo commit, e não foi separado nesta medida única.

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

## Retenção: o que vence e o que fica (P10, 03/10/2026)

**Decisão** (orquestradora, dentro das autorizações do dono; P10 da reavaliação de 03/10): o histórico de execução fica
**para sempre**, por enquanto. Retenção de 90 dias foi a alternativa e não entrou.

**O que vence.** A cada 6 h, só no líder (`state.py::_retencao_uma_vez`):

- `events`, `commands` terminais, `ai_calls` e `measurements` vencem em `log_retention_days` (14 dias de fábrica). Uma
  `ai_calls` de ocorrência de pedido ainda aberta fica até o fechamento (28.6).
- Evidências vencem em `evidence_retention_days`. Os arquivos rotacionados (`*.log.1`, `data/logs/probe-*`,
  `data/avd-probe`) seguem o prazo do log.
- `worker_enrollments` vence em 7 dias.
- **Telemetria do parque (RA-11):** `instance.updated` sem execução vence em 48 h (`events.TELEMETRIA_RETENCAO_H`). Ela leva
  o DTO inteiro do aparelho a cada mudança. Em 03/10 eram 32 mil linhas e 48 MiB em 14 dias, todas sem execução. A
  testemunha da purga do relatório do Aprendizado (o evento sem execução mais antigo) não muda, porque há eventos sem
  execução de outros tipos todo dia (`control.changed`, `command.updated`, `log`).
- **Memória vencida (RA-11):** `memory_items` com `expires_at` vencido sai do banco. Antes saía só da leitura: a purga
  existia e ninguém a chamava.
- A purga de `events` vai em lotes de 2.000 linhas (`events.PURGA_LOTE`). A de 02/10 levou 41 mil linhas num comando
  só. Ao fim da volta, `PRAGMA optimize` atualiza as estatísticas do SQLite (`sqlite_stat1` não existia); no PostgreSQL,
  quem faz isso é o autovacuum. Os índices novos de `events` e `measurements` (094) são a parte da Android no RA-11.
- **O DTO só vai ao log quando conta um fato (14.13).** Medido no central em 04/10 (24 h): 7383 `instance.updated`,
  ~2550 deles trocas de controle `ai↔none` que o `control.changed` já gravava (cada troca ia ao log duas vezes) e 391
  só de telemetria (CPU/RAM, idade do quadro, hora da sonda de rede, contagem da pausa). Agora `instance.updated` só
  persiste quando muda um campo material do DTO, quando a publicação traz mensagem própria, quando o nível não é
  `info` ou quando o controle mudou sem `control.changed`. O resto sai como `instance.progress`, efêmero
  (`events.EPHEMERAL_KINDS`), com o mesmo DTO para o painel (`devices/publicacao.py`). Uma réplica não recebe o
  `instance.progress` de outra (como `frame` e `metrics`); o fato segue em `instance.updated` e `control.changed`.
- **Limite conhecido (RA-11).** O aviso `eventos_perdidos` do pedido (076) detecta a perda pelo menor `events.id` que
  sobrou (`gatilhos_dinamicos.buraco`). A purga por tipo deixa buracos ACIMA desse mínimo. Um gatilho que observe
  `instance.updated` e fique mais de 48 h sem ler perderia esses eventos sem aviso. Em 03/10 não havia nenhum gatilho
  de pedido no central. Se aparecer um, a detecção precisa saber da classe de telemetria.
- A retenção do aprendizado (`aprendizado.retencao`, ADR-054) e a da sombra da decisão fechada
  (`ai.decisao_fechada.retencao_dias`) têm prazo próprio, e o agregado diário é calculado antes de purgar.
- **Contatos do site (29.77, ADR-075):** `portal_contatos` vence em 180 dias, a linha inteira, pelo laço
  `portal-contatos` (a cada hora, no líder da trava `avisos`), não por esta volta de 6 h. O prazo é o do aviso de
  privacidade da página e não é configurável: mudar exige mudar a página e o ADR. Os backups (`data/backups`) guardam
  cópia até o teto de cópias deles; o histórico no Telegram é do dono.

**O que fica.** `runs`, `objectives`, `steps`, `attempts`, `actions` e `plan_versions`. Leem esse histórico o
aprendizado, a projeção pelo histórico (18.3), o "de novo" (11.5) e o relatório da execução. A redação de contas
retiradas segue o P13: `memory_items` é redigido em `esquecer_conta`; `runs.command` e `actions.args` ficam como
histórico.

**Medido** (03/10/2026 05:15Z, central, banco em `mode=ro`, soma do tamanho das colunas):

- 7,2 MiB de dado nessas seis tabelas em 16 dias (17/09 a 03/10), ≈ 0,45 MiB por dia;
- 279 execuções, 2.623 etapas, 3.349 ações;
- o banco tem 174,8 MiB, com 16,9 MiB livres. O grosso é `events` (82.649 linhas) e `measurements` (50.732), que
  vencem;
- na reavaliação, as consultas por execução levaram de 1,9 a 2,6 ms.

**Gatilhos para rever.** Basta um deles:

- o banco passa de 1 GB depois da retenção por classe (RA-11);
- a lista de execuções ou a leitura de uma execução passa de 100 ms;
- entra o segundo backend real ou o PostgreSQL no lugar do SQLite (ADR-003);
- um pedido de privacidade exige apagar histórico de execução.

Rever é decidir o prazo por tabela, com o mesmo cuidado do 28.6: nada de cortar a execução pela metade (apaga-se a
execução inteira, terminal, com as filhas).

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
| `data/avd` (~60 GB) | cópia **a frio**, um aparelho por vez, só hibernado ou parado (29.39) | `scripts/backup.ps1 -AVDs` (ver abaixo) |
| `apks/` (catálogo) | cópia direta — **obrigatória junto com o banco** | manual, ou storage compartilhado |
| `data/evidence`, `data/avatars` | cópia direta, ou já no bucket | manual, ou storage compartilhado |

```powershell
pwsh -File scripts\backup.ps1                      # cópia em data\backups\<AAAAMMDD-HHmmss>\
pwsh -File scripts\backup.ps1 -IncluirSegredos -Reter 30
pwsh -File scripts\backup.ps1 -IncluirSegredos -Instalar   # tarefa farm-backup, diária, 03:00 (-Origem diario)
```

A retenção nunca apaga a última cópia, mesmo que a idade diga que sim: backup vazio é pior que backup velho.

**Quem fez a cópia, e o teto das de deploy (29.38).** O `manifesto.json` diz `origem` (`deploy`, `ensaio`, `diario`
ou `manual`) e o `commit` da árvore. Em 04/10/2026 havia 161 cópias e 23 GB em `data/backups`, quase todas de
deploy: com dez deploys num dia, a retenção em dias não segura nada. O `deploy.ps1` chama `backup.ps1 -Teto 10`,
que guarda as **10 cópias de deploy e de ensaio mais novas**. Cópia antiga sem `origem` conta como de deploy, porque
a tarefa diária nunca tinha sido registrada no ambiente central. As cópias diária e manual só saem pela retenção em
dias. Uma pasta com nome escolhido à mão (`20261003-120818-antes-ra20b`, `config-antes-*.yaml`) nunca é apagada
por regra automática. As regras ficam em `scripts/lib/copias-de-backup.ps1`.

**A poda nasce em ensaio.** Apagar cópias não tem volta, e a primeira poda no ambiente central leva ~150 cópias
(22 GB). Por isso, por omissão, o teto **lista** o que apagaria (`apagaria (teto …)`) e não apaga nada. A poda só
se liga com `backup.ps1 -Podar` ou com o arquivo `data\backups\PODAR-LIGADO`, que se cria depois do sim do dono
no chat. A tarefa `farm-backup` também só é registrada (`-Instalar`) depois desse sim.

**`deploy.ps1 -PularBackup` logo depois de um `-Ensaio`.** O ensaio já fez a cópia do que está no ar. Por isso
uma subida até 60 min depois dele, **no mesmo commit**, pode pular a sua própria cópia. Sem uma cópia de ensaio
assim, o deploy recusa o `-PularBackup` antes de parar qualquer coisa. Um ensaio de outro commit não vale, porque a
subida pode trazer uma migração que ele não ensaiou.

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

`data/avd` guarda as sessões: a conta Google da VM-loja e os logins do Instagram. Aparelho novo sem
esses dados significa refazer login — por isso eles importam, e por isso não
entram no backup diário (dezenas de GB, e a cópia a quente de um emulador ligado não é confiável). A política é
cópia **a frio** (29.39), pela etapa `-AVDs` do `backup.ps1` (`scripts/lib/copias-de-avd.ps1`):

```powershell
pwsh -File scripts\backup.ps1 -AVDs -Aparelhos android-07     # só os aparelhos da lista
pwsh -File scripts\backup.ps1 -AVDs                           # os locais com conta: só com data\backups\AVD-LIGADO
```

- **Um aparelho por vez, e só se ele JÁ estiver hibernado ou parado.** A cópia nunca hiberna nem desliga ninguém;
  o aparelho no ar fica para a próxima.
- **Não há trava contra o rodízio acordar o aparelho no meio** (a pausa de reparo, persistida desde o 25.13, segura
  só reinício e reset automáticos). Por isso a guarda roda antes de cada arquivo e mais uma vez no fim: aparelho fora
  de `hibernated`/`stopped` ou emulador com o AVD aberto param a cópia, que fica em `<carimbo>-abortada` (nada é
  apagado; o AVD só foi lido).
- **Destino:** `data\backups\avd\<id>\<carimbo>\`, com `manifesto.json` (`origem: avd-semanal`, commit,
  estado, e caminho, tamanho e sha256 de cada arquivo, conferido depois da cópia). Manifesto e saída levam só o id
  do aparelho, nunca nome de conta ou de persona. As travas do emulador (`*.lock`) não vão. Recusa se sobrariam
  menos de 50 GB livres. ~5,5 a 6 GB por aparelho.
- **Nasce desligada e sem agendamento.** A lista padrão (aparelhos locais com conta vinculada) só roda com o arquivo
  `AVD-LIGADO` no destino, criado depois do sim do dono; a tarefa `farm-backup` (29.38) não chama `-AVDs`. Aparelho
  de conta real só com esse sim. Retenção: 2 cópias por aparelho, pela mesma regra de poda do 29.38.
- **Restauração** (`Restore-AvdAFrio`, com o aparelho parado): confere o sha256 da cópia inteira, MOVE o AVD atual
  para `<carimbo>-avd-substituido\` (nunca apaga), copia de volta, confere de novo e reescreve o `path=` do `.ini`
  para o lugar restaurado. A sessão volta ao estado da cópia (até 7 dias atrás): se o app pedir login de novo, é
  com a pessoa. O ensaio da restauração roda só em aparelho de QA, num `ANDROID_AVD_HOME` à parte e num emulador
  avulso `-read-only` fora das portas do parque.

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

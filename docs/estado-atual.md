# Estado atual — handoff

**Revisado em 28/09/2026, depois da implantação da segunda evolução (`07fce91`).** Atualize este arquivo ao fechar cada tarefa (skill `fechar-tarefa`). Mantenha-o curto:
o que muda de sessão para sessão fica aqui, e o resto aponta para a fonte principal ([índice](README.md)).

## Onde estamos

- **Fase 18 (ADR-052), fatia 1 implantada:** digitação com conferência, telas do Instagram como dado com volta ao
  estado conhecido, projeção e orçamento por ação. A meta "zero Python por app" foi aprovada pelo dono em 28/09: as
  fatias 2–4 (catálogo, sessão, app âncora) estão em andamento no branch `claude/app-conhecimento`; a 5
  (aprendizado) e o 12.3 seguem com ele ([design](design/conhecimento-de-app.md)).

- **Guia Persona como mapa da pessoa: IMPLANTADO em 28/09 (`c3e2dad`)**, junto com a biografia inteira indo ao
  modelo (sessão da evolução 2: 16 campos com orçamento e "o pedido manda no que fazer; a persona dá o jeito").
  Retrato no topo (fatos que levam à seção, medidor, Completar com IA), índice fixo com estado e marca "IA", seções
  que abrem lendo e editam uma a uma. Prova: `simulated` (vitest 636, capturas CDP 1366/1024/375 com persona rica e
  vazia) e conferência no Chrome do dono depois do deploy (André: 7/7, marca em todos os campos da biografia). O
  Appium órfão (K-039) voltou no deploy e foi limpo à mão; a correção definitiva está numa tarefa separada.
- **Saldo das contas de IA (ADR-051): IMPLANTADO em 28/09 (`3fb43d3`, migrações 052 e 053)** (pedido do dono:
  acompanhar Anthropic, OpenAI e Gemini na plataforma, com a IDE vendo e os saldos valendo como regra). Saldo
  estimado = última leitura do console − gasto de `ai_calls` desde ela − gasto de fora que o provedor reporta depois
  da leitura (conciliação com `ANTHROPIC_ADMIN_KEY`/`OPENAI_ADMIN_KEY`, gravadas no `.env` do central a pedido do dono).
  Aviso (`ai_balance_*` na saúde, chips no cabeçalho), bloqueio por conta (`kind="balance"`, desligado de fábrica) e
  cartão em Configuração › IA. A IDE lê em `GET /api/ai/balances` e registra leitura lendo o console no Chrome.
  - `real` (28/09, central): leituras registradas pela IDE às 16:16–16:18 UTC (Anthropic US$ 5,19, OpenAI US$ 8,25,
    Gemini R$ 29,37); depois do deploy da 053 as três estimativas batem com o console, com a linha de base gravada
    (OpenAI: 0,4076 do provedor e 0,122 local); painel conferido no navegador. Conciliação real também em cópia do
    banco (15:52 UTC).
  - `simulated`: suíte backend 2614 ok (SQLite; `test_backup` só falha no worktree, sem `config.yaml`), testes de
    saldo em PostgreSQL 17 local, frontend 633 ok.
  - `not_run`: bloqueio real com chamada paga.
  - **Livro-caixa: substitui a leitura de tela (28/09).** A extensão coletora (`b6e99c0`) foi rejeitada pelo dono e
    removida. Saldo = âncora − consumo:
    - Anthropic pelo `usage_report` horário (validado: 5,4946 × 5,4693 US$ em 6 h);
    - OpenAI pelo `organization/costs`;
    - Gemini por `ai_calls`.
    Um laço de 10 min concilia e fecha o dia, e a recarga é o único gesto humano. "Desatualizado" = conciliação
    falhando (a pendência das 72 h deixou de existir). `ai.balance_consoles.gemini` está fixado no config.yaml do
    central.
  - **Saldo em todo lugar que mostra IA: IMPLANTADO em 28/09 (`3db70f9`)**: popover "IA em uso", Configuração › IA
    (Situação e Por função), azulejo "Saldo de IA" no Diagnóstico, US$ por conta no custo (`UsageReport.by_account`)
    e aviso no Comando. `real`: conferido no painel do central (popover e Diagnóstico). Google: a API do Gemini não
    publica custo nem saldo; o custo do Gemini só aparece pelo `ai_calls` quando ele é usado (conciliação `not_run`,
    depende da exportação de faturamento para BigQuery, decisão do dono).
  - **Achados:** o `cost_report` da Anthropic só tem dias fechados (o `usage_report` horário resolve); a OpenAI manda a falta de crédito como 429
    `insufficient_quota` (agora `billing`). O deploy trouxe de novo o Appium órfão (K-039): a saúde fica `degraded`
    só por `appium_log_masking_off`, não resolvido aqui para não matar `node` de outras sessões.
  - **ENCERRADO em 28/09.** Limites delegados pelo dono e aplicados:
    - bloqueio em US$ 0,50 (R$ 2,50 no Gemini);
    - aviso em US$ 3 na Anthropic, US$ 2 na OpenAI e R$ 10 no Gemini.

    Prova real do bloqueio: 503 `kind: balance`, `ai_balance_blocked` e custo zero. Só volta a ser assunto se a chave
    do Gemini for compartilhada fora da plataforma; aí se liga a exportação do BigQuery.
- **Fase 17 (custo de IA por provedor, ADR-049): código na `main` (`d6b30fb`, implantado) e medida em 28/09**
  ([relatório §18](relatorio-validacao.md)).
  - **Imagem da persona REAL no central:** `gpt-image-2` médio, ~US$ 0,052 por imagem, 1 por persona nova
    (`on_create`).
  - **Ator e verificador seguem Sonnet 5, Haiku e Opus 5.5:** o `gpt-6-luna` fez 12/14 contra 13/14, a 51% do
    custo.
  - **Próximo:** 17.10 (cascata para ator barato) e 17.11 (`eval_run` resiste a queda).
  - **Decisões do dono pendentes:**
    - gerar a imagem real das 14 personas existentes (~US$ 0,75);
    - critério para as alavancas só da Anthropic (plano e escalonamento no Sonnet 5).
  - Evidência bruta e scripts da bateria: `data/fase17/` (fora do Git).
  - A chave da OpenAI é do "Default project", com lista de modelos permitidos. `gpt-6-luna` e `gpt-image-2` foram
    liberados em 28/09; a organização segue "Identity rejected" na verificação.
- **Modo Automático do Comando (ADR-050): IMPLANTADO em 28/09 (`b0f2c07`, junto com o "Completar com IA com
  instruções" da evolução 2, `467248a`)** (pedido do dono: o sistema decide quem faz e onde pelo pedido, pela fila e
  pela aderência do perfil). `POST /api/runs/targets/suggest` + "Quem faz e onde" no Comando; modos manuais em
  "escolher manualmente". Crença é coerência, não alvo de persuasão; propaganda/voto → `alerta_conduta` (ADR-048).
  `real`: primeira chamada (`ai_calls` 2233) com as três personas vivas sem crença → "não avaliáveis"; depois,
  pelo Chrome, "Completar com IA" nas três (2236–2238) e o mesmo pedido escolheu o **André** (católico não praticante)
  e descartou Bruno e Lucas (sem religião) — 2239. A execução planejada `f55c04` espera o @ da prima (dado do dono;
  executar manda mensagem real: `not_run`). Relatório §19. **Próximo ajuste:** app citado pelo nome ("no Instagram") ainda não restringe as
  candidatas (`_app_do_comando` só casa por habilidade). K-039 voltou nos três deploys do dia (tarefa sugerida à
  parte).
- **Assistente do comando (ADR-047): IMPLANTADO em 28/09 (`a71e809`)** (pedido do dono: o Comando era pobre e o
  `needs_input` obrigava a reescrever o texto). "Refinar com IA" no Comando e respostas no banner da execução, que
  criam a sucessora (`POST /api/commands/refine`, `POST /api/runs/{id}/successor`). Prova `simulated`: suítes
  inteiras, `test_assistente_do_comando.py` (11), `AssistenteDoComando.test.tsx` (4), navegador contra backend
  simulado. `real`: uma chamada de refinamento no central (`ai_calls` 1609, ~US$ 0,038, esquema aceito;
  relatório §16). `not_run`: segunda rodada real e sucessora real. O deploy trouxe de novo o Appium órfão (K-039),
  resolvido com stop + fim do node + `farm-central`. Destino ("peça para o Lucas")
  fica na foto da execução: com `run_id`, o texto vai à IA sem destinos (`sem_destinos`). Nota de acessibilidade
  pendente: o assistente está dentro de um `Banner role="alert"` na execução. K-044.
- **Cabeçalho do painel em duas faixas: IMPLANTADO em 28/09 (`58bfd13`)** (pedido do dono: o cabeçalho estava
  estranho). Só `frontend/src/features/topbar/*`, teste do rodízio e docs. Prova `simulated`: typecheck, 612 testes
  e capturas CDP a 1915/1366/1024/768/375 px contra backend simulado (8765). `real` parcial: `deploy.ps1` completo
  (backup, build do `dist`), health `ok`, `problems: []` depois de matar o Appium órfão (K-039); o bundle servido
  em 8000 é o novo. Falta o olho do dono no Chrome. `--topbar-h` segue 56 px com o cabeçalho em 97 px
  (`Runs.module.css:1001` usa no `calc`; já estava defasado antes). K-043.
- **Fase L da auditoria de usabilidade (outra sessão) IMPLANTADA em 27/09 (`524471d`), a pedido do dono.** Só
  painel e docs. Central e agente do worker em `524471d`, health `ok`. O deploy deixou um Appium órfão e a saúde
  subiu `degraded` até o reinício (K-039).
- **28/09 (tarde), implantado em `1fc4c01`:** ambiente central reclassificado (não é produção); aparelho real criado e
  aposentado pela plataforma; CI no runner próprio `central` (operacao.md §5); crenças ricas da persona ao modelo e no
  painel (ADR-048); assistente do comando (ADR-047, outra sessão). Provas no relatório de validação §17 (e §16).
- **Segunda evolução IMPLANTADA em 28/09 (`07fce91`, central e agente do notebook), autorizada pelo dono.**
  Persona como pessoa, conta única com credencial e consentimento, imagens, persona N:N aparelho, roteamento por
  persona, provisionamento local e o painel novo. Design em [`design/persona-e-parque.md`](design/persona-e-parque.md);
  ADR-040 a 046; plano-100 fase 16; provas em [`relatorio-validacao.md`](relatorio-validacao.md) §15.
  - Health `ok`, migração `051_persona_n_aparelho`, `problems: []`; agente `0.1.0+07fce91`. Backup de antes:
    `data/backups/20260928-084453` (ensaiado com 047–051 na cópia).
  - A validação real achou a geração de persona recusada pelo provedor (K-042), corrigida; a geração real funciona.
  - **Pendentes do dono:** chave e orçamento de imagem (o ambiente central gera imagem SIMULADA; a pesquisa de provedor está em outra sessão); GitHub Actions sem pagar: jobs no runner próprio `central` desde 28/09 (operacao.md §5), PostgreSQL na GitHub só depois de 1º/10 (K-040); reparo do Git for Windows (K-041, instalador em
    `Downloads`); autorizar a prova `not_run` de execução real por persona numa conta do Instagram (AVD real e painel com operador feitos em 28/09).
  - **Lacunas conhecidas:** 409 `conta_do_app_ja_no_aparelho` sem `details`; avisos da prévia e `no_binding` citam a
    persona pelo id; `DELETE …/devices/{iid}` sem `app_id` tira todos os vínculos daquele aparelho; `session_actions`
    só no principal; sem campo "app em primeiro plano"; `generation.usd` do rascunho nulo (o custo fica em
    `ai_calls`); selos longos com reticências em colunas estreitas.
- **Evolução arquitetural: IMPLANTADA em 27/09 (`5c98735`, central e agente do worker), autorizada pelo dono.**
  - Health `ok`, migração 046, `features.skills: true`. O `skills.enabled: true` está no `config.yaml` do ambiente central.
  - Agente do notebook em `0.1.0+5c98735`, instalado pelo manifesto.
  - Backup de antes: `data/backups/20260927-194906`.
  - Provas reais em [`relatorio-validacao.md`](relatorio-validacao.md) §14, com gasto de ~US$ 0,16 de IA:
    - `ig.abrir_conversa@1` publicada e executada no android-06 (`r-20260927230248-2ae798`, conversa comprovada pela
      prova local);
    - `mode=plan` com `plan_report`;
    - resolução de intenção;
    - ensino v2 com IA real.
  - Plano-100: fase 15 (15.1–15.14 registrados; 15.15 é o K restante).
  - CI completo verde depois do deploy, inclusive PostgreSQL (run 36359168554), depois de apagar o schema de cada
    teste ao fim dele (K-038).
  - **Para resolver:**
    - **android-01 sob pressão** (load ~22 em 2 vCPU). A sessão do lucas está travada em 4 leituras sem
      reconhecer a tela desde 26/09 (achado #104); precisa de pessoa e, provavelmente, de mais RAM ou de reinício.
    - **Ollama local fora do ar:** o ator foi para o Sonnet 5, que é pago.
    - **Receita de OPEN_THREAD não aprendida:** o username foi digitado sem arroba e `detemplate` não cobre isso.
    - **Só 2 de 23 fluxos reais convertem em skill** (`E_ROUNDTRIP` nos de perfil e `E_RUNTIME_VARIABLE` nos do
      QA).
  - **K que falta:** cluster de apps, portões e saúde de `state.py`; `bootstrap`; routers por contexto;
    `adapters/ai|android`; identidade de receita por capability; impor as máquinas de estado; ligar o `apply` no
    ciclo.
  - O checkout do ambiente central fica no commit implantado. Commits só de docs depois dele não pedem pull: um pull muda
    a versão que o agente compara.
- **Implantado em 27/09 ~03:28 UTC (`8f7b94c`):** o bloqueio do perfil por desafio (ADR-029) e o CI com `npm run build`
  (B7), sobre a evolução de desempenho.
  - Central e agente do worker em `0.1.0+8f7b94c`, health `ok`, `problems: []`, sem migração, backup em
    `data/backups/20260927-002826`.
  - Contas: três ativas (lucas, bruno, andre) e cinco `blocked` desatreladas.
  - B20 fechado com 29 objetivos abandonados.
  - Suíte do backend 1612/1612; `scripts/tests` 150; CI verde na `main` em `8f7b94c` (run 36291495264, com o `npm run build` do B7) e CI completo com PostgreSQL verde em `286eca2` (run 36292226293).
- **Implantado em 27/09 ~01:35 UTC (`a90a6e1`, evolução de desempenho, autorizado pelo dono):**
  - central com health `ok`, `problems: []`, `preview_mode: on_demand`, `GET /api/desempenho` 200;
  - agente do notebook em `0.1.0+a90a6e1`, com o Pillow e a feature `observe_local` ativa;
  - sem migração nova, backup em `data/backups/20260926-223449`.

  Provas reais em [`relatorio-desempenho.md`](relatorio-desempenho.md) §9:
  - prévia sem espectador: 0 capturas e 72 evitadas em 144 s;
  - imagem do worker: ~41 KB contra ~696 KB pelo túnel;
  - B21 aplicado: android-01 e android-04 em 2048 MB;
  - piloto do renderer rejeitado;
  - contêiner validado no CI;
  - escalada da receita divergida decidida: não escalar.
- **Evolução de desempenho (26/09, pedido do dono, coordenação multiagente F1–F8):** detalhes abaixo.
  - **Código:** prévia e observação sob demanda, tela sensível fora da prévia, medição agregada (`GET /api/desempenho`),
    funil de receitas e desbravador visíveis, reserva de RAM no worker e no central, imagem capturada na origem do
    worker (`observe_local`), correções de transporte e posse (NATS, cerca, reentrega) e contêiner de validação.
  - **Onde ler:** [`relatorio-desempenho.md`](relatorio-desempenho.md); checkpoint e retomada em
    [`handoffs/evolucao-desempenho.md`](handoffs/evolucao-desempenho.md); ADR-027 e ADR-028; plano-100 Fase 14.
  - **Prova:** `simulated`. A suíte do backend deu 1607 aprovados no SHA integrado `21515a4`, e a única falha é de
    ambiente (`config.yaml` fora do worktree; passa com o exemplo). Vitest 476/476. Bancada: prévia sem espectador 18 → 0 screencaps; `image_policy auto` 16 → 9;
    repetição com receitas 16 → 11; chamadas de IA e sucesso iguais. Linha de base `real` da produção só por GET.
  - O checkout do ambiente central foi atualizado para `a90a6e1` no deploy de 27/09. O deploy e a atualização do agente
    autorização; ver Próxima ação.
  - **CI completo verde na `main` (`3501934`, run 36284216665):** SQLite, PostgreSQL (1589 aprovados), painel,
    instalação só do agente com o Pillow, dependências e docs. O primeiro run PostgreSQL (36283068748) reprovou
    um teste que lia `events` sem `ORDER BY` (K-030); o teste foi corrigido, não o comportamento.
  - Não há migração nova. O deploy exige `npm run build` (o painel novo manda `watch`; o `dist` velho segue como
    painel antigo), e a atualização do agente instala o Pillow.
  - Os worktrees `.claude/worktrees/evolucao` e `ev-*` têm **junções** para o `backend/.venv` e o
    `frontend/node_modules` do checkout principal. Para limpar, veja o K-033: nunca apague recursivamente.

- **Git.** A `main` foi publicada no `origin/main`; o SHA exato sai de `git log -1`. Os PRs #8 a #12 estão
  integrados; nenhum trabalho fora da `main`.
  - O worktree `.claude/worktrees/focused-chaum-ea5077` é de outra sessão, já está integrado e fica preservado.
- **Implantado em 26/09 ~21:37 UTC (`57a155f`: PR #13 "todos na versão promovida", ADR-026; PR #3 B4, cerca depois
  de banco restaurado):** central e agente do worker em `57a155f` (`0.1.0+57a155f`, `worker.yaml` com o mesmo hash),
  sem migração nova, health `ok`, `problems: []`. Prova `real`:
  - agente novo (com `fences` no `hello`): `start` e `stop` do android-09 → `succeeded` (cercas 50 e 51). O cenário
    do banco restaurado em si fica `not_run` (exigiria restaurar um backup antigo em produção);
  - critério 8 do ADR-026: entrega de app secundário (QA) no android-06 → `ready`, e depois o launcher em foco e o
    processo do QA parado; o "Abrir app" do Instagram em seguida → `succeeded` em ~3 s (antes, `uncertain` em 90 s);
  - a primeira varredura depois do deploy não disparou instalação nenhuma (conferido nos comandos e nas provas de
    instalação), como previsto pela consulta de antes do deploy;
  - a convergência depois de promover uma versão nova fica `not_run` até existir uma versão nova de algum app.
  - Decisões do dono de 26/09, nesta rodada: "todos devem ficar atualizados sempre" (ADR-026, implantado); a senha do
    portal da `22d65f` não será trocada (decisão dele); o app de QA fica igual em todos os aparelhos (distribuído ao
    parque: pronto no android-01/06/09/14; o android-04 falha na prova de abertura por falta de RAM, ver B21; os
    desligados recebem ao ligar); o teste de login com a senha salva do Instagram ficou `not_run` (ver B21).
- **Implantado em 26/09 ~19:30 UTC (`37bb6e6`: PR #11 cofre, PR #12 prontidão sem efeito tardio):** central e
  agente do worker em `37bb6e6` (`0.1.0+37bb6e6`, instalado por `worker-install.ps1 -Origem`, `worker.yaml` com o
  mesmo hash), sem migração nova, health `ok`, `problems: []`. Prova `real`:
  - cold start do android-09 (remoto) → `succeeded` em 75 s, `ready` com "servicemanager, system_server e display
    responderam" (não mais o texto do PR #5); a linha nova de prontidão no central (0,3/0,4/0,7 s → pronto em
    1,3 s) e no agente (pronto em 1,1 s); o `agente.log` sem `input tap` nem acerto de relógio; relógio do
    convidado a −2 s; depois `stop` → `succeeded`;
  - android-05 (local): `wake` sem snapshot válido subiu a frio (correto, `snapshot_valid=0`); hibernado de novo →
    `wake` QUENTE em 19 s (`kind: warm`), sem descarte de snapshot, relógio a 0 s; devolvido a `hibernated`;
  - o relógio como condição própria corrigiu sozinho o android-06 logo depois do deploy (−3 s → −1 s,
    `measurements.kind='clock'`);
  - loja em aparelho real: proxy "sem proxy" aplicado no android-01 (`applied`, lido `:0`) e o app de QA entregue
    ao android-01 pela varredura de 60 s depois de recusado por aparelho ocupado (`ready`, versão 1.0.0 no adb).
  - CI: PostgreSQL verde na `main` (`3da3bb5`) pela primeira vez desde 23/09, e de novo no commit implantado
    `37bb6e6` (run 36266236041: SQLite, PostgreSQL, frontend, worker, docs e auditoria, todos verdes).
- **Implantado em 26/09 ~18:55 UTC (`3da3bb5`: PR #9 credenciais, PR #10 loja de apps, PR #8 CI):** central em
  `3da3bb5`, migrações 040 e 041 (ensaiadas antes numa cópia do banco real), health `ok`, `problems: []`, porta 8010
  escutando, `config.yaml` intacto, android-01/04/06 readotados `ready`, worker de volta em ~10 s (agente segue em
  `0.1.0+5b81c1a`; atualiza junto com a prontidão, que muda o `worker/executor.py`). Prova `real`, sem IA e sem
  conta: `POST /api/runs` com credencial sem consentimento → 409 `consentimento_de_credencial`; senha no texto →
  409 `credencial_no_comando`; nenhuma execução criada, cofre com os mesmos 8 segredos, o valor não voltou nem foi
  gravado. Loja: `GET /api/app-store` com categorias, `POST /api/proxies/apply` sem alvo → 400 `target_required`,
  prévias sem gravar, e uma distribuição real (QA no android-04) → `already`. Chrome cadastrado em `apps`
  (`chrome`, `utilitario`). O login real num site com credencial fica `not_run`: é disparado pelo dono, pelo
  painel, com a URL no comando.
- **Implantado em 26/09 ~17:30 UTC (PR #7, prontidão por subsistema):** central e agente do worker em `5b81c1a`
  (`0.1.0+5b81c1a`), migração 039, health `ok`. Prova `real`: readoção de android-01/06 pela escada nova; android-04
  (1470 MB, sob pressão) teve o preparo estourado → `booting`, e voltou 12 s depois no mesmo PID (a limitação
  entre tentativas, documentada em `devices/prontidao.py`); um cold start do android-09 (`from_snapshot:false`)
  fechou `succeeded` → `online`, internet `healthy`, stream `live`, e depois `stop` → `succeeded`. Hibernação do
  worker segue desligada.
  - Os follow-ups daqui (efeitos tardios, `readiness.detail`, log por degrau, PostgreSQL) foram fechados pelos PRs
    #8, #11 e #12, implantados em `37bb6e6`. Ficam como limitação conhecida (PR #12): um `start` no limite do prazo
    pode passar ~36 s dos 540 s (o reconciliador fecha), e parar um aparelho em `booting` sem PID não mata o
    emulador.
- **Deploy anterior** (25/09 ~14:19 UTC, conferido em `GET /api/health`, `/api/ai`, `/api/workers` e no painel
  pelo Chrome):
  - central no commit `8169fd3`, migração `039_limites_por_servidor`, `cryptography` 50.0.0, **ator de IA no
    Sonnet 5** (ADR-023; o Ollama saiu do caminho principal), porta 8010 escutando, 15 aparelhos;
  - worker `worker-lan-01` online com agente em `0.1.0+c0c982d`, marcado **`agent_outdated`** (esperado
    `0.1.0+8169fd3`). A diferença para ele é só o teto de `boot_parallelism` na mensagem `limits` (10.6), que o
    agente antigo já aceita porque o dele é maior; atualizar é opcional e mexe na máquina do worker (procedimento em
    `operacao.md` §9).
- **Saúde depois do deploy:** `ok`, sem problemas.
- **Plano-100:**
  - 86 de 91 itens `implemented` (ver [`execucao-plano-100-runner.md`](execucao-plano-100-runner.md));
  - pendentes: 7.4 (`partial`: bateria feita em 25/09; falta o cache do verificador e medir o ator local), 8.3,
    8.4, 12.3 e T.2. O 0.10 fechou em 24/09 com a decisão 2 (sem revogação); 7.9, 10.6 e T.4 nasceram e fecharam
    em 24–25/09 a partir do backlog B1–B3 e B13;
  - o que falta em cada um, separado por tipo, está em [`roadmap.md`](roadmap.md).

## Entregas recentes

- **26/09 (código, PR #9, implantado em `3da3bb5`):** a automação entra com a credencial que a pessoa fornece, com
  consentimento (ADR-025, substitui a recusa do ADR-009; **confirmado pelo dono em 26/09**, na sessão coordenadora,
  ao autorizar merge e deploy). Migração `040_credenciais_da_execucao`. Chrome cadastrado em `apps`. **Troque a senha
  do portal usado na `22d65f`**: ela foi ao provedor de IA antes da correção. A senha da execução `22d65f`, que ficou em claro, foi mascarada
  no banco de produção; o histórico do painel limpa sozinho a entrada antiga ao abrir.
- **24/09 (código, implantado):** a lista está no [`CHANGELOG.md`](../CHANGELOG.md#2026-09-24--custo-de-ia-painel-perfis-multi-app-treinamento-limites-por-servidor).
  - custo de IA: 7.5–7.8, e 7.1 ligado no Ollama;
  - painel: 11.1–11.9;
  - grupos de acesso: 11.10;
  - perfis multi-app: 12.1 e 12.2;
  - modo treinamento: 13.1–13.3;
  - limites por servidor: 10.5.
- **25/09 (bateria de avaliação, real, ~US$ 2,57):** rejulgamento 41/56 (6 falsos positivos do Haiku), linha de
  base 16/17 (QA no android-09 remoto e Instagram no android-01), HTTP 500 do verificador em 0,7 % e recuperados.
  O ator rodou no fallback (Sonnet), porque o Ollama estava fora do ar. Detalhe em
  [`relatorio-validacao.md`](relatorio-validacao.md) §11.1.
- **24/09 (documentação e processo, esta sessão):**
  - criados `CLAUDE.md`, o índice, `produto`, `arquitetura`, `dominios/*`, `ia`, `operacao`, `decisoes` (22 ADRs), o
    knowledge lake (23 aprendizados), `roadmap`, `CHANGELOG` e este handoff;
  - skills `retomar`, `preparar-tarefa` e `fechar-tarefa`, e as regras em `.claude/rules/`;
  - `scripts/docs-check.py` e o job `docs` no CI;
  - o mapa do plano-100 corrigido: o 10.5 estava fora e isso quebrava `check`/`relatorio`;
  - o relatório de execução regenerado;
  - o teste do livro-razão reescrito: o antigo testava o executor aposentado.

## Em curso

- **Aparelho × persona × app × sessão (android-06), fase cloud** na branch `claude/awesome-lamport-s602ai`, não
  integrada nem implantada. Falta a fase local: [`handoffs/android-device-persona-runtime.md`](handoffs/android-device-persona-runtime.md).

- **Loja de aplicativos e proxy do aparelho** (pedido do dono, 26/09): PR #10, implantada em `3da3bb5` (migração
  041). Prova `real` limitada à vitrine, às prévias e a uma distribuição `already`; instalação de app secundário e
  proxy aplicado num aparelho real ficam `not_run`. Detalhes em
  [`dominios/apps-e-loja.md`](dominios/apps-e-loja.md). Pendências do dono:
  - se a volta de UM aparelho deve continuar rebaixando a versão para o parque inteiro;
  - se promover deve continuar atualizando sozinho os aparelhos que têm o app como principal (hoje, sim).

## Bloqueios e validações pendentes

- **Decisões do dono:**
  - escolher o primeiro app do 12.3;

  A decisão 7 foi executada em 25/09: bateria de ~US$ 2,57, do saldo de US$ 12,72 ([ADR-018](decisoes.md)).

  A decisão 2 foi tomada em 24/09: **sem revogação da chave** ([ADR-017](decisoes.md)); o 0.10 fechou.
- **Divergência a conferir:** decisão 6, relógio das duas máquinas ([ADR-019](decisoes.md)). Custa um comando
  `w32tm /stripchart` em cada máquina.
- **Provas reais que dependem de autorização:**
  - os nove aceites (T.1);
  - 1.3–1.8;
  - 6.x num remoto;
  - 8.1, 8.2 e 8.4;
  - 9.4.

  Os procedimentos estão prontos em [`relatorio-validacao.md`](relatorio-validacao.md) §13.1, e a lista completa em
  [`roadmap.md`](roadmap.md) §3.
- **Provas que dependem de infraestrutura que não existe:** uma segunda máquina para o aceite 5 (2.1, 4.2) e uma
  máquina Linux com KVM (10.4).

## Backlog encontrado pela documentação

São lacunas funcionais, **não implementadas** nesta sessão. Cada uma vira item do plano-100 ou tarefa própria,
por decisão do dono.

| # | Lacuna | Onde | Origem |
|---|---|---|---|
| B1 | **Corrigido em 24/09 (7.9), não implantado.** Não era dado errado: o agregado `true` está certo (plan, verify, escalation e social são externos). Era o aviso, que abria com a frase do ator local ("os dados NÃO saem") sem dizer de quem era | `backend/app/planning/routing.py` / `ProviderCfg.sends_data_externally` | leitura do health em 24/09 |
| B2 | **Corrigido em 24/09 (10.6).** O comentário de `workers/protocol.py` dizia que `limits` sai logo depois do `welcome`; sai na primeira batida da conexão | `backend/app/workers/protocol.py:220` e `workers/registry.py` (`on_heartbeat`) | frente 1 |
| B3 | **Corrigido em 24/09 (10.6), não implantado.** O painel aceitava `boot_parallelism` até 10 e a mensagem `Limits` até 16; agora os três tetos são 10, com teste de alinhamento | `backend/app/models.py`, `backend/app/workers/protocol.py` | frente 1 |
| B4 | **Corrigido em 25/09 e implantado em 26/09 (`57a155f`, PR #3), central e agente.** Depois de restaurar o banco, a cerca regredia e o agente recusava `start`. Agora o `hello` traz `fences` (a maior cerca por aparelho, lida do diário) e o central despacha acima dela. Só vale com central **e** agente atualizados. Prova `simulated` (`backend/tests/test_cerca_restaurada.py`) | `backend/app/commands/store.py`, `worker/agent.py` | [K-004](conhecimento/aprendizados.md) |
| B5 | O `start` remoto `c-20260921172322-6f7fdc` está `uncertain` desde 21/09, sem reconciliação registrada | banco de produção; `commands/reconciler.py` | `relatorio-validacao.md` §13 |
| B6 | O vocabulário de prova: os registros escritos à mão usam `tests`/`unit`. Falta decidir entre registrar essas provas como `simulated` via `aplicar` ou estender `ESTADOS`/`PROVAS` junto com o enum de `plano-100.js` | `scripts/claude-plan-100.py:35`, `.claude/workflows/plano-100.js` | [`claude-plano-100.md`](claude-plano-100.md) |
| B7 | **`npm run build` resolvido em 27/09 (job do painel no CI).** O CI não roda `npm run build`, e os testes de `scripts/tests` que usam pwsh só rodam localmente | `.github/workflows/ci.yml` | frente 2 |
| B8 | O app de QA embutido não foi migrado para o fluxo de release; `apps` e `app_releases` continuam como duas tabelas | plano-100 6.3 (bloqueio registrado) | frente 1 |
| B9 | Estado do worker e controle manual não são compartilhados entre backends | `backend/app/main.py` (achado #27) | `banco.md` |
| B10 | O `api-contract.md` tem dois adendos chamados "v0.9", e o `InstanceState` da base não lista `hibernated` | `docs/api-contract.md` (anotado no adendo v0.11) | frente 1 |
| B11 | 8 dos 15 campos de voz das personas reais estão vazios (achado #107) | plano-100 8.1 | frente 2 |
| B12 | Sobras do executor antigo em `.claude/plano-100.json`: `model`, `prompt` e `batches[].effort`. Nenhum script as lê | `.claude/plano-100.json` | inventário |
| B14 | **Corrigido e implantado em 25/09 (7.10, ADR-024).** O verificador Haiku errou nos dois sentidos no rejulgamento (6 falsos positivos, 9 falsos negativos em 56 capturas de 19–20/09) e recusou `delivered` onde bastava `sent`. Agora a recusa com nível suficiente é rejulgada uma vez pelo modelo de escalonamento, e "sim" sobre tela sem elementos não prova nada. Os falsos positivos de "tela errada" seguem possíveis quando a prova local não casa | `backend/app/taskqueue/executor.py` (`_verify`) | bateria de 25/09 |
| B15 | **Corrigido e implantado em 25/09 (7.11, ADR-023).** A saúde dizia `ok` com o ator local fora do ar e tudo no fallback; agora lista as chamadas em fallback dos últimos 30 min (`ai_fallback_em_uso`). E o ator passou a ser declarado no Sonnet | `backend/app/state.py` (`_ia_em_fallback`) | bateria de 25/09 |
| B16 | Depois do deploy, aparelhos remotos parados ficam com o detalhe "servidor Notebook da LAN fora do ar" mesmo com o worker online; o texto só muda quando o aparelho muda de estado | `backend/app/devices/manager.py` (`_motivo_do_externo_parado`) | deploy de 25/09 |
| B17 | O seed de `apps` pelo `config.yaml` insere por `id` sem conferir o pacote: se o import já cadastrou sozinho o mesmo pacote com outro id, o app aparece duplicado na vitrine (só com edição manual do config) | `backend/app/state.py` (`_seed_apps`) | code-review do PR #10 (deixado de propósito) |
| B18 | O diálogo antigo "Instalar em…" da aba Versões continua instalando direto, fora da distribuição com prévia da loja | `frontend/src/features/releases/ReleasesPage.tsx` | code-review do PR #10 (deixado de propósito) |
| B19 | Limitações conhecidas do PR #12: um `start` no limite do prazo pode responder ~36 s depois dos 540 s (o reconciliador fecha), e o worker usa 480 s também para `wake`, contra 180 s no central; parar um aparelho em `booting` sem PID não mata o emulador | `backend/app/worker/executor.py`, `backend/app/devices/manager.py` | PR #12 |
| B20 | **Resolvido em 27/09:** os 29 objetivos da bateria foram abandonados, a pedido do dono (relatório §10). O android-09 tem 28 objetivos `waiting_user` e 1 `uncertain` de execuções `completed_with_issues` da bateria de 24–25/09. Pelo ADR-026 eles seguram a troca automática do app principal dele (a tela seria evidência). Decisão do dono: resolver ou cancelar | banco de produção; `vitrine.objetivo_em_andamento` | deploy de `57a155f` |
| B21 | **Resolvido em 27/09 (relatório §9).** O `config.yaml` já pedia 2048 MB; o `config.ini` dos aparelhos que ainda estavam no ar ficou em 1536 até o reinício a frio. Os convidados de 1,5 GB saturam: o android-04 falha na prova de abertura (adb `shell` > 30 s) ao receber um app; o android-01 chegou a load 22 ao abrir o Instagram e o UiAutomator2 não leu a tela (o painel já recomenda mais RAM para a imagem). Decisão do dono: RAM por imagem no `config.yaml` e reinício dos aparelhos | `config/config.yaml` (perfil por imagem) | 26/09, loja e teste de login |
| B13 | **Corrigido e implantado em 25/09 (T.4).** O CI estava vermelho desde pelo menos `bfffb0d`, por ambiente: cofre sem chave fora do Windows, scripts PowerShell do Windows no pwsh do Linux, `apksigner` novo (defeito real no inspetor), mock de frame com `Blob` do jsdom no Node 22, saúde dependente de SDK/KVM do host, `cryptography` 46.0.3. **Verde no run 36078946300 (`9e12baf`).** Implantado em `e6b00db` com `cryptography` 50.0.0 | `.github/workflows/ci.yml`, `backend/tests/`, `frontend/src/app.integration.test.tsx` | T.4 no livro-razão |

## Próxima ação concreta

1. Rode a skill `retomar` para conferir que o git e este arquivo estão de acordo.
1a. **Evolução de desempenho: implantada e provada em 27/09.**
    - **Contas do Instagram (27/09, pedido do dono):** só `lucas.almeida9484` (android-01), `bruno.ferreira9267`
      (android-03) e `andre.carvalho9543` (android-06) funcionam.
      - As outras cinco estão travadas e foram desatreladas: `blocked`, sem persona e sem aparelho. A tabela está
        em [`relatorio-desempenho.md`](relatorio-desempenho.md) §10.
      - Desafio de segurança passou a bloquear o perfil sozinho (ADR-029).
      - O desafio do android-04 fica encerrado: a conta dele (`felipe.nogueira93762026`) é uma das travadas.
      O `open_app` foi disparado como a prova de abertura do B21, supondo que o app do aparelho fosse o de QA. O `app_id` do android-04 é `instagram`, então foi um toque em conta real além do que o B21 pedia. Depois que o desafio apareceu, não houve nenhuma interação.
    - **Pesos em aberto, só com dado novo:** escalar receita divergida (reabre com n ≥ 30); renderer (reabre com
      emulador novo).
    - **Monitorar em uma semana:** `scripts/bench.py leitura --dias 7` contra
      `desempenho/bancada/leitura-20260927T014241Z-a90a6e1.jsonl`, e `captura.evitada` em `GET /api/desempenho`.
2. **Decisões do dono pendentes:**
   - ~~B21~~ **resolvido em 27/09** (relatório §9). O `config.yaml` já pedia 2048; faltava reiniciar a frio os aparelhos
     que ainda estavam no ar com 1536. Texto original do pedido: mais RAM para a imagem dos aparelhos de 1,5 GB (android-01/04 saturam ao abrir o Instagram ou instalar um
     app): mexe em `config.yaml` e exige reiniciar os aparelhos;
   - ~~B20~~ resolvido em 27/09 (29 objetivos abandonados). Texto original: resolver ou cancelar os 29 objetivos parados do android-09 (bateria de 24–25/09), que seguram a troca
     automática do app principal dele.
3. **Provas reais pendentes:** login com a senha salva do Instagram (`session.connect`) num aparelho que não esteja
   saturado (o android-01 falhou até na verificação só de leitura, load 22 em 2 vCPU); login num site com credencial
   fornecida (PR #9) — pelo painel, com a URL escrita no comando e a senha no campo "Senha para a automação".
4. **Agente do worker:** já em `0.1.0+57a155f` (26/09). O checkout do ambiente central pode estar em commit só de docs à
   frente do backend no ar; o `deploy.ps1 -Ensaio` mostra essa diferença, e ela é esperada.
5. **Decisões de 25/09 aplicadas** (delegadas pelo dono, por custo-benefício): verificador Haiku com rejulgamento
   escalado e guarda de tela vazia (7.10, ADR-024); ator declarado no Sonnet (ADR-023); saúde acusa fallback (7.11).
   Falta prova real do 7.10, que só aparece quando o erro medido se repetir; acompanhar as decisões do verificador
   em Execuções. Reavaliar o verificador com a próxima bateria (capturas novas).
6. **Sem gasto e sem mundo real:** B6 (vocabulário de prova) é decisão do dono; B7 (`npm run build` no CI) é o
   próximo item de código de risco baixo (B4 foi implantado em `57a155f`).
7. **Com autorização do dono:** o ensaio do aceite 6, derrubando o túnel no meio de um `start`. É o de menor risco
   entre os reais; o procedimento está em [`worker.md`](worker.md).

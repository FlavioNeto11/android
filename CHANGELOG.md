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

## 2026-10-04 — 30.31 fatia 2: o ensaio só de leitura e a conferência no app de QA (branch feat/30-31-fatia-2)

- **Conferência no app de QA.** Ao fim da execução de validação, o `ContentProvider` do QA conta as mensagens
  daquela execução (só leitura, e só quando o comando traz `{run_id}`). Com 2 ou mais, a etapa de efeito ganha o
  `efeito_repetido` do 29.58 com `fonte: provedor`, e o veredito do 30.42 a trata como inválida.
- **Ensaio só de leitura** (`ensaio:<pedido>`). A execução para antes da etapa com efeito: ela e as seguintes ficam
  `skipped` e o objetivo fecha pelo sistema. O pedido fecha `ensaio_so_leitura` (motivo novo, adendo v1.11). Nada
  cria ensaio no central ainda.
- **Painel:** o motivo novo e a fonte `provedor` com texto próprio.
- Sem migração. Testes: `test_learning_ensaio_e_oraculo.py` (9) e os afetados (1140 passed em SQLite).

## 2026-10-04 — 29.53: o portão de PostgreSQL de uma suíte, por escrito (branch docs/29-53-portao-pg, só docs)

- `docs/banco.md` ("O portão de PostgreSQL de uma suíte") e `.claude/rules/testes.md`. Toda suíte roda PG dirigido
  nos arquivos que o lote toca (`-n 8`, Idle, esquema do worker); o farm-pg sobe só na vez da suíte e espera a
  primeira conexão. A suíte inteira em PG roda só em janela sem aparelho com conta subindo, nunca colada num deploy.
  Lote só de painel ou de docs não roda PG.
- Falta a medida por arquivo (os dez mais lentos), que sai da etapa de PG da suíte 21 e fecha o item com prova real.

## 2026-10-04 — 29.61 (correção): o aviso de rota desconhecida aparece de verdade (branch fix/29-61-aviso-some, só painel)

- Causa: `aoMudarHash` limpava o aviso olhando o hash DEPOIS de `aplicarHash`, que reescreve o endereço errado para a
  rota atual (válida). O aviso era gravado e apagado na mesma troca; e o navegador dispara `popstate` e `hashchange`
  juntos, o segundo já com o hash reescrito. O teste do 29.61 chamava `aplicarHash` direto e não passava por ali.
- Correção: `aplicarHash` devolve o que fez (`rota` | `desconhecida` | `nada`), e o aviso sai só quando uma rota NOVA
  foi aplicada.
- Prova `simulated`: `frontend/src/app.integration.test.tsx` monta o `App`, troca o hash com `popstate` + `hashchange`
  e confere o `role=alert`, o Dispensar e a saída ao ir a um endereço que existe (os dois casos falhavam antes da
  correção); front 1477 passed. Real: na próxima validação no navegador do central.

## 2026-10-04 — Suíte 19 na main e deploy 19 no central (c683ab0e; sem migração)

- **Integrado e implantado** (push 03:04:27Z; deploy 03:05Z): #194 (29.61, rota desconhecida e Execuções sem id),
  #195 (29.59, selo "agente defasado" pelo código do pacote), #197 (29.57, nome ANA nas falas da IA à pessoa) e #196
  (32.4, link do cartão do espelho por lista de permissão). Saúde ok, `problems` vazio às 03:05Z; o reparo de
  android-01/03/06/13 ficou pausado durante o deploy (retirado às 03:06Z).
- **Agente do notebook** atualizado para 0.1.0+c683ab0 às 03:05:50Z: reconectou com `agent_outdated=false`, prova real
  do 29.59 (antes, o agente f1651ec, sem a impressão do código, seguia a regra da versão e aparecia defasado).
- **Suíte 19** (integ/suite-19 c683ab0e): SQLite 9168 passed, 13 skipped; `scripts/tests` 544; front 1475; docs-check
  0/0; PG dos afetados 2823 passed, 7 skipped (164 arquivos, `-n 8`). Conflitos só de CHANGELOG, juntados.
- **Validação de fora** (orquestradora, 03:06Z a 03:08Z): o aviso de rota desconhecida do 29.61 NÃO aparece no central
  (o item ficou `partial`); a correção vai num PR próprio. Às 03:06:33Z a saúde deu `degraded` por `ai_balance_stale`
  (a conciliação do saldo com o relatório do provedor deu ReadTimeout).

## 2026-10-04 — 29.61: rota desconhecida avisa, e Execuções sem id abre a mais recente (branch feat/29-61-rota-e-execucao-recente, só painel)

- Hash que não nomeia tela (`#/runs`, link antigo ou digitado errado): aviso "Este endereço não existe no painel" com o
  endereço, "Ir para o Painel" e "Dispensar"; a tela atual fica e o link volta ao canônico. O aviso sai ao navegar.
- Execuções sem id no link não reabre a execução restaurada do navegador (`localStorage`): `selecaoRestaurada` faz a
  lista abrir a mais recente; a execução escolhida nesta visita continua valendo, e o menu para Execuções não leva a
  restaurada no link.
- Prova `simulated`: `frontend/src/store/ui.test.ts` (6 casos novos); front 1473 passed. O percurso no navegador fica
  para a validação do deploy.

## 2026-10-04 — 29.59: o selo "agente defasado" compara o código do agente, não o commit (branch fix/29-59-agente-defasado-por-codigo, sem migração)

- `version.codigo_do_agente`: impressão (sha256, 16 hex) dos arquivos do pacote do agente. No checkout, pelas entradas
  de `backend/worker-manifest.txt`; na máquina do worker, varrendo o `app/` que o instalador montou com elas. Ficam
  fora `BUILD_VERSION`, `__pycache__` e `.pyc`; `\r\n` vira `\n` antes do hash.
- Fio: `Hello.agent_code` (opcional, aditivo; esquema congelado atualizado com o checklist). O agente manda
  `AGENT_CODE`; o registro guarda em memória por worker e `_defasado` compara as impressões quando as duas pontas as
  têm. Agente antigo, sem a impressão, segue pela comparação de versão. O aviso no log do `hello` passa a dizer que o
  código difere.
- Motivo: achado 8 da validação do deploy 14. Todo reinício do central depois de um commit só de docs acendia o selo
  com o mesmo código dos dois lados.
- Prova `simulated`: `test_pacote_do_agente.py` (a impressão do checkout é a da cópia do instalador com selo de build,
  cache e CRLF; código só do central não a muda; uma linha no agente muda) e `test_workers.py` (mesmo código com
  versão diferente não é defasado; código diferente é; agente que deixa de mandar volta à regra da versão).
  657 passed nos testes que tocam o worker e o contrato. Real `not_run`: o agente do notebook aparece defasado até
  ser atualizado com o código novo, o que é correto, porque `version.py` está no pacote.

## 2026-10-04 — 29.57: o painel e as falas da IA à pessoa usam o nome ANA (branch feat/29-57-nome-ana, sem migração)

- `contracts/identidade.py`: `REGRA_DE_IDENTIDADE`. Quando fala com a pessoa (pergunta, recusa, aviso), a IA é ANA:
  usa o nome só quando precisa se identificar e, se perguntarem, diz que é uma IA. Não assina nem se apresenta como
  ANA em texto que uma persona publica ou envia. Entra nos três planejadores (`IDENTITY_RULE`) e no assistente do
  comando (`refine_system`); o ator, o verificador e o escritor social ficam como estavam (hashes iguais).
- Painel: `lib/identidade.ts` (`NOME_DA_IA`, espelho do contrato, com teste de igualdade) em "ANA está montando o
  plano…", "ANA precisa de mais informações…", "ANA não sabe quem deve fazer isto", "ANA está lendo o comando…" e
  na recusa da prévia da persona ("ANA recusou escrever esta resposta: …"). Os demais rótulos "IA" ficam.
- Prova `simulated`: `test_identidade_da_ia.py` (a regra nos quatro prompts que falam com a pessoa; nem regra, nem
  nome, nem apresentação no escritor social, no ator e no verificador; `app/social/` não importa a identidade; o
  painel usa o mesmo nome), `test_prompts_licoes.py` (hashes dos planejadores atualizados de propósito) e
  `ProfileDetail.test.tsx` (recusa com o nome). Real `not_run`: nenhuma chamada paga.

## 2026-10-04 — 32.4: link do cartão do espelho sem texto derivado do pedido (branch `canais/32-4-link-sem-slug`)

### Código
- `modules/avisos/application/espelho.py`: o link do painel nos cartões do espelho leva só o id.
  - O id de fluxo pode ser o slug do objetivo (`fluxo:ler-sem-abrir-conversas-nem-enviar-nada-`), e o quadro tem
    convidados. Por isso vale uma LISTA DE PERMISSÃO dos formatos reais: `r-<14 dígitos>-<6 hex>`, `ped_` + 22
    base64url, `receita:<número>`, `fluxo:<hex 8+>` e `fluxo:f<número>`. O resto abre só a tela (Aprendizado,
    Pendências ou Pedidos), sem o item.
  - Visto no 1º cartão do espelho, no deploy 18.
- Prova `simulated`: `test_trello_espelho.py::test_link_do_cartao_leva_so_id_e_nunca_texto_derivado_do_pedido` e
  105 testes de espelho, leitor e webhook.

## 2026-10-04 — Suíte 18 na main e deploy 18 no central (f1651ec8; sem migração; Trello na etapa 3)

- **Integrado e implantado** (FF 02:05Z; deploy 02:06:05–02:06:41Z): #186 (29.63, esquema-modelo no harness de PG),
  #187 (28.19, lote e rajada no avisador), #188 (30.45), #189 (31.27, espera do juiz adaptativa), #190 (31.29, apelidos de
  app de sistema), #191 (29.64, re-toque verificado em Entrar), #192 (29.65, `one` prefere o principal) e #193 (29.60,
  N do efeito repetido no painel). Saúde ok, `problems` vazio; agente do notebook 0.1.0+f1651ec.
- **Suíte 18** (integ/suite-18 f1651ec8): SQLite 9158 passed, 13 skipped; `scripts/tests` 544; front 1467; docs-check
  0/0; PG dos afetados 4306 passed, 12 skipped (183 arquivos, `-n 8`, esquema-modelo, 12 min 32 s). Conflitos de
  CHANGELOG em sete PRs e um de código em `contracts/origem.py` (30.43 + 28.19, blocos independentes), juntados.
- **Armadilha:** o farm-pg, ao subir, faz ~2 min de fsync de recuperação; a primeira rodada deu 1913 erros "the database
  system is starting up". Esperar a conexão antes do pytest.
- **Config do central** (por instalação): `trello` etapa 3 às 02:05:54Z (`reconciliar_s` 300, cadastro automático com o
  webhook), pelo `.claude/handoffs/canais/trello-config.py`, com cópia em `data/backups`.

## 2026-10-04 — 30.45: o veredito da validação enxerga a evidência reclassificada (branch feat/30-45-veredito-reclassificado)

- `GET /api/aprendizado/validacoes` passa a mandar, em cada pedido, `invalida_depois`: a linha `invalida` que a
  execução deixou no item, mesmo chegada depois do fechamento.
- O Resumo da execução diz "inválida (…)" no lugar de "a favor", e o histórico do item diz "Rodou; depois:
  inválida — …". É o caso da 5f2de5, achado no navegador do 30.43.
- Sem migração. Testes: `test_validacoes_listagem.py` e `ValidacaoTab.test.tsx`.

## 2026-10-04 — 29.65: com a política `one`, a sessão no principal ganha (branch fix/29-65-one-prefere-principal, sem migração)

- `resolver_alvos`: com sessão pronta em mais de um aparelho da persona, vai ao PRINCIPAL quando ele está ligado (ou
  quando nenhum outro com sessão está); principal desligado com secundário ligado e entre secundários, o balanceamento
  segue decidindo. `Mundo.ligados` (aparelhos `online`) vem do serviço.
- Motivo: android-06 (principal) e android-13 (secundário) com sessão do André em 04/10; o desempate mandava a mesma
  persona ora a um, ora a outro, e todo comando precisava dizer o aparelho.
- Prova `simulated`: `test_roteamento_por_persona.py` (o caso E passa a esperar o principal; dois casos novos),
  `test_roteamento_execucao.py` (prévia com os dois ligados e com cada um desligado); 115 passed nos testes de roteamento.

## 2026-10-04 — 29.60: o painel mostra o N do efeito repetido (branch feat/29-60-efeito-repetido-no-painel, só painel)

- Detalhe da etapa (guia Instâncias): linha "Efeito repetido" com "apareceu N vezes" e quem contou ("contado na tela
  pelo verificador" ou "contado pelas ações gravadas desta execução"), lendo `steps.result.efeito_repetido` (29.58).
- Resumo da execução: linha de atenção "Efeito repetido" por etapa (título, aparelho, N, fonte), com a orientação de
  conferir no app e apagar as cópias; some quando não há repetição. Sem código cru na tela.
- Prova `simulated`: `ResumoDaExecucao.test.tsx` (com e sem repetição), `resumo.test.ts` (frases e filtro);
  158 passed em `features/runs`. O percurso no navegador fica para a validação do deploy.

## 2026-10-04 — Suíte 17 na main e deploy 17 no central (0b7c2c39; sem migração; Trello na etapa 2)

- **Integrado e implantado** (FF 01:13:09Z; deploy 01:13:35–01:14:31Z): #182 (30.43), #183 (30.44), #184 (29.62, trava de
  migração por esquema no PG) e #185 (29.56, tranca por cliente). Saúde ok, `problems` vazio; agente do notebook
  0.1.0+0b7c2c3.
- **Suíte 17** (integ/suite-17 0b7c2c39): SQLite 9117 passed (1 falha da catraca de `Any` no 29.56, corrigida em
  3e05f037 e conferida: 91 passed nos vizinhos); `scripts/tests` 544; front 1464; docs-check 0/0; PG dos afetados 1560
  passed em 94 arquivos com `-n 8` (12 min, já com a trava por esquema).
- **Config do central** (por instalação): `trello` etapa 1 às 00:34Z (reinício 00:35Z, leitor no ar) e etapa 2
  (`webhook.enabled`) às 01:13Z, pelo `.claude/handoffs/canais/trello-config.py`, com cópia em `data/backups`.
- **Prova real** do 29.56 de fora (orquestradora, 01:15Z): 8 Bearer inventados → 401, o 9º → 429 `Retry-After: 60`;
  cabeçalhos DENY/nosniff/same-origin em `/central/`; webhook HEAD 200, GET 401, POST sem assinatura 401.

## 2026-10-04 — 29.63: o esquema do worker no harness de PostgreSQL (branch fix/29-63-esquema-modelo)

- `tests/esquema_do_worker.py`: em PG, cada worker migra um esquema uma vez, e a 1ª abertura de banco de cada teste
  pelos ajudantes compartilhados o recebe esvaziado. Isso troca o custo de ~3,2 s por teste (criar, migrar e apagar)
  pelo de esvaziar. Só harness de teste; em SQLite nada muda.
- Os testes da própria migração seguem com esquema novo.
- Qualquer falha ou mudança de estrutura troca o esquema, então o custo volta ao de antes, sem teste vermelho.
- `ESQUEMA_MODELO=off` serve para a medida antes e depois.
- Teste: `backend/tests/test_esquema_do_worker.py` (só com `TEST_DATABASE_URL`).
- Prova em PG (farm-pg, 04/10, mesmo commit e mesmos testes, `-n 8`, Idle):
  - com `ESQUEMA_MODELO=off`: 1033 passed, 2 skipped em 8 min 49 s;
  - com o reuso: 1033 passed, 2 skipped em 2 min 51 s, com 8 migrações, 551 reusos e 0 trocas;
  - os testes novos: 6 passed.
- SQLite intacto.

## 2026-10-04 — 29.64: re-toque verificado em Entrar e `review` que se reconcilia (branch fix/29-64-retoque-no-login, sem migração)

- `SessaoDeclarada._login`: depois de um envio `uncertain`, relê a tela e, só com o formulário intacto (identificador à
  vista, senha ainda no campo, Entrar habilitado, sem ProgressBar, mesmo app) e a conta não parada no meio, dá UM toque
  no botão atual, sem redigitar a senha, e observa de novo; a tentativa passa pela etapa `resubmitting`. Qualquer outra
  tela devolve o incerto de antes, e o freio do ADR-055 vale como sempre.
- Conta lida e conferida aberta no aparelho (`_garantir`, sessão reaproveitada) tira a credencial de `review`
  (`active`, falhas zeradas, evento `login_reconciliado`); `invalid` não sai assim.
- Motivo: android-13 em 04/10 (c-20261004001548-03701b `uncertain` com o formulário intacto; um toque manual entrou e a
  credencial ficou em `review`).
- Prova `simulated`: `backend/tests/test_retoque_no_login.py` (6; `FakeInstagram.envios_ignorados`/`tela_ao_ignorar`);
  vizinhos do login 270 passed. Real `not_run` (o toque perdido não se reproduz de propósito; nenhum login real nesta noite).
- Revisão da orquestradora (01:31Z): a releitura confere de forma explícita `estado.trava` e a classificação da tela
  (erro de credencial); a reconciliação mantém `blocked_until` e vale no `observe_only`; testes dos negativos (botão
  desabilitado, identificador trocado, erro/desafio/outra tela na releitura, outro pacote, conta parada no meio) e da
  senha que não vaza (banco, log, resultado). 16 testes no arquivo.

## 2026-10-04 — 29.56: tranca de login por cliente, limite no Bearer e cabeçalhos de segurança (branch fix/29-56-tranca-por-cliente, sem migração)

- A `PortaoDeLogin` passou a ser por cliente (`security.access.cliente_de`): pelo túnel o par é sempre `127.0.0.1`, e o
  cliente é o `CF-Connecting-IP` só com par loopback, `tls_behind_proxy` e `Host` público; par da rede vale pelo próprio
  endereço; o acesso local nunca se tranca. Até 4096 clientes acompanhados, poda dos sem bloqueio vigente primeiro.
- O `Bearer` errado em `/api/*` conta como chute do mesmo cliente; bloqueado, `/api/*` com `Authorization` responde `429
  too_many_attempts` com `Retry-After`, mesmo com o token certo; a sessão por cookie segue valendo.
- Toda resposta leva `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` e `Referrer-Policy: same-origin`.
- Painel: o `429` em HTML do limite de taxa da Cloudflare vira `rate_limited` com "espere 10 segundos" (ou o
  `Retry-After`), em vez de "Erro HTTP 429".
- Prova `simulated`: `backend/tests/test_tranca_por_cliente.py` (14), `test_sessao_do_painel.py`, os 106 arquivos que montam
  o app (1507 passed), `frontend/src/features/login/LoginPage.test.tsx` (9). A prova de fora fica para depois do deploy.

## 2026-10-04 — 28.19: lote de teste não avisa um por um (branch `canais/28-19-avisos-lote`)

### Código
- `contracts/origem.py`: `PREFIXO_LOTE` (`lote:<frente>:<id>`) e `e_execucao_do_sistema`. O avisador não manda aviso
  de execução de prova, de validação do QA nem de lote de frente.
- `modules/avisos`:
  - a rajada do mesmo tipo sai como UM aviso agrupado com a contagem, e o primeiro sai na hora;
  - a regra está em `fila_sql.reivindicar_um`, com as chaves novas `avisos.agrupar_s` (60) e
    `avisos.agrupar_a_partir_de` (3);
  - o reply ao agrupado não responde a fato nenhum (`canal_enviadas.fato = grupo:<tipo>`).
- Sem migração. Prova `simulated`: `backend/tests/test_avisos_rajada.py` (12 casos). A aprovação que um lote abre segue avisando (só o dono decide), e o link do agrupado é o da caixa.

## 2026-10-04 — 31.27: espera do juiz adaptativa (branch feat/31-27-juiz-adaptativo)

- A espera antes do primeiro julgamento deixa de ser um sono fixo de 1,5 s. Ela lê a árvore e sai quando a tela fica
  igual por `ai.judge_wait_estavel_s` (0,6 s, novo), sem marca pendente. O teto segue sendo `judge_wait_s`, e a última
  leitura vira a primeira do verificador.
- A espera entre sondagens (LT-5) não muda. `judge_wait_estavel_s: 0` volta ao antigo.
- Medida de partida:
  - linha de base de 04/10: 4 esperas e 6 s em 233 s de parede;
  - "depois" do deploy 16: 6 esperas e 9 s em 149 s.
- Prova: `simulated` (`tests/test_juiz_espera_adaptativa.py`); o real é `not_run` até o deploy. Doc:
  `docs/dominios/execution.md`.

## 2026-10-03/04 — Suíte 16 na main e deploy 16 no central (3b5355ce; migrações 087 e 088; config sem mudança)

## 2026-10-04 — 31.29: apelidos de app de sistema e casamento sem acento (branch feat/31-29-apelidos-do-app)

- `apps_citados` ignora acento e caixa. `com.android.settings` ganha apelidos de mais de uma palavra
  (`APELIDOS_POR_PACOTE`): "Configurações do aparelho", "Ajustes do telefone", "Android Settings" etc. Uma palavra só
  não é apelido ("ajustes na legenda" segue no app padrão).
- O padrão para o Instagram sem app citado não muda.
- Origem: investigação só leitura do 31.29. 11 de 12 comandos da 1ª rodada da linha de base caíram no Instagram por
  dizerem só "Configurações".
- Prova: `simulated` (`tests/test_planejador_entre_apps.py::test_apelido_de_app_de_sistema_sem_acento_e_sem_caixa_31_29`).
  Doc: `docs/dominios/execution.md`.

 no central (3b5355ce; migrações 087 e 088; config sem mudança)

- Integrados em `integ/suite-16`, nesta ordem: #174, #178 (30.42, com a correção do `for_each` e o 30.41 pelo
  #181, d17b36c6), 30.38 (a, b), #176 (29.55), #179 (29.58), #175 (31.24, migração 088), #177 (32.2, migração 087,
  com o `deque(maxlen)` da revisão) e o 31.25 (cherry-pick 2962f2e7).
- Ajustes de integração:
  - `copias_vistas` passa pelo embrulho `_verify` até `_verificar`;
  - a recusa do 29.58 grava pela `intencao(...)`;
  - os três `Any` novos foram tipados, e a catraca segue em 120;
  - o classificador de falhas ganhou o motivo novo do 29.58;
  - os motivos do 30.41 e do 30.42 ganharam texto humano (afaa5d8d);
  - uma docstring deixou de citar o literal do prefixo da validação;
  - o teste do Trello passou a chamar a rota por extenso.
- Suíte:
  - SQLite `-n 8` em Idle: 9082 passed, 7 skipped, 4 falhas cruzadas, corrigidas e reconferidas (246 passed);
  - `scripts/tests`: 544 passed;
  - front: typecheck e 1439 testes;
  - PG dos afetados: 474 passed, 1 skipped.
- Deploy: ensaio de migração numa cópia restaurada (087 e 088 aplicadas); `deploy.ps1 -PularDependencias` das
  23:58:00 às 23:59:06Z; `/api/health` ok em 3b5355ce, migração 088, sem problemas. O agente do notebook está em
  0.1.0+3b5355c.
- Prova real do 29.58 (r-20261004000108-fee44f, android-10, comando da 5f2de5): uma entrega só, uma ação de efeito,
  sem recusa falsa nem repetição marcada. A recusa em si segue `simulated`, porque a IA não errou desta vez.

## 2026-10-03 — 29.62: a trava de migração do PostgreSQL por esquema (branch fix/29-62-trava-de-migracao-por-esquema)

- `pg_advisory_lock` da migração passa à forma de duas chaves, com o esquema corrente na segunda. Na suíte em
  PostgreSQL cada teste migra o próprio esquema e os workers do xdist não entram mais em fila (antes: 1006 testes em
  22 min com `-n 4`). Em produção (um esquema) nada muda. Sem migração.
- Teste: `backend/tests/test_trava_de_migracao_por_esquema.py` (só com `TEST_DATABASE_URL`).

## 2026-10-03 — 29.55: relatório de falha pendente do emulador não prende mais a subida (branch fix/29-55-crash-report)

- Antes: um dump deixado pelo crashpad (`emu-crash-<versão>.db/reports/*.dmp`) fazia toda subida parar no diálogo de
  consentimento, sem ninguém para responder. A espera ia até o prazo do boot, e a escada de reparo subia de degrau
  (incidente de 03/10 ~19:00Z, terceiro degrau num aparelho com conta).
- Agora, no central e no agente:
  - (a) `android.crash_report_mode` (padrão `never`) → `-crash-report-mode never`;
  - (b) antes do `Popen`, os dumps vão para `<dados>/quarentena-crash/`, sem apagar; no central, evento no aparelho;
  - (c) a linha do diálogo nesta subida encerra a espera na hora, com motivo claro. No central, `error` e a marca
    `bloqueio_de_crash`, e `_pedir_reparo` não age. No agente, `failed` com `motivo: dialogo_de_crash`, que o central
    reconhece.
- Config: a chave nova entra com padrão ligado; `config.example.yaml` e `worker.example.yaml` documentam.
- Doc: `docs/dominios/parque.md` (seção nova) e o aprendizado K-090.
- Prova `simulated`: `backend/tests/test_relatorio_de_falha_do_emulador.py` (14) e 2 casos em `test_worker_executor.py`.
  A `real` é `not_run`: subida de um aparelho sem conta com o dump de volta, na vez da orquestradora.

## 2026-10-03 — 29.58: efeito marcado pela ação e efeito repetido incerto (branch fix/29-58-efeito-pela-acao, sem migração)

- Numa etapa sem efeito declarado, a ação com cara de efeito externo é recusada antes de tocar:
  - grava `actions.side_effect=1`, `rejected`, e conta na métrica `executor.efeito_fora_da_etapa`;
  - com catálogo, o gatilho é o `commit_selector` casado exato; sem catálogo, o vocabulário; Enter conta só em campo
    de composição.

  Era o caso da 5f2de5: o envio dentro de uma etapa sem efeito e o reenvio na etapa de envio.
- Efeito que saiu 2 ou mais vezes fecha `uncertain` "efeito repetido (N)" e grava
  `steps.result.efeito_repetido = {copias, fonte}`. A contagem vem do verificador (`Verdict.copias`, prompt
  atualizado) ou das ações gravadas da mesma etapa-modelo e item.
- Validação `simulated`: `backend/tests/test_efeito_pela_acao.py`, 11 testes. A prova real no QA Messenger é
  `not_run`.
- Doc: `docs/dominios/execution.md`, seção nova.

## 2026-10-03 — O Trello do dono como espelho e canal de comandos (32.2, ADR-072; `simulated`, tudo desligado)

- Branch `canais/32-2-trello`, em 6 passos, ainda NÃO integrado na `main` e não implantado:
  - o cliente REST (chave e token só no cabeçalho, balde de 60 por 10 s), a migração 087 e a config `trello:`;
  - o espelho: reconciliador por fato, com marcos de deploy e custo do dia, que adota o cartão órfão em vez de duplicar;
  - o leitor e a saída: só o dono comanda (o autor vem da API), mover para ✅, "sim" e `/aprovar` NÃO aprovam (pedem a
    confirmação no painel ou no Telegram), o veto do dono veta, qualquer comentário com 🤖 é de IA, convidado não executa;
  - a rota `HEAD`/`POST /api/canais/trello/webhook`, a única exceção sem credencial em `/api/` no endereço público: assinatura
    HMAC-SHA1 que falha fechada, o corpo é só um aviso e o líder relê a action pela API;
  - `scripts/trello-webhook.py` (`--ensaio`, `--aplicar`, `--desligar`): o cadastro é manual primeiro; o recadastro
    automático fica atrás de `trello.webhook.cadastro_automatico` (desligada de fábrica);
  - ADR-072, adendo v0.99 do contrato da API, `operacao.md` §16 (rollout em 5 passos) e `banco.md` (087 e o estado `aviso`).
- Prova `simulated`: `backend/tests/test_trello_{cliente,config,espelho,leitor,webhook}.py`. `not_run`: o HEAD do Trello na
  URL pública, o primeiro cadastro e um comentário real chegando pelo webhook (dependem do deploy e do "vai" da orquestradora).

## 2026-10-03 — Scripts do portal público versionados em `scripts/portal-*` (ADR-073)

- O procedimento do portal deixou de depender de arquivos fora do Git: `portal-instalar-tunel.ps1` e
  `portal-gerar-senha.ps1` (rodados pelo dono), `portal-config.py` (`ligar`, `recuar`, `conferir`) e
  `portal-prova-de-fora.sh`. Ligar e recuar usam a mesma lista de linhas e são inversos exatos.
- O que mudou em relação aos originais: raiz e hostname por argumento, fim de linha do `config.yaml` preservado,
  `conferir` não toca no `.env`, e a linha do 429 da prova de fora pode ser pulada (`SEM_LIMITE_DE_TAXA=1`).
- Simulado: `scripts/tests/test_portal_config.py` (9 testes, ida e volta byte a byte sobre o `config.example.yaml`).
- Real, 03/10/2026, máquina central, checkout `4ad5f8b6`: às 23:05:38Z `conferir` 5 de 5, `ligar --ensaio` "nada a
  fazer" e `recuar --ensaio` 7 linhas, sem gravar; às 23:06:58Z a prova de fora deu 22 de 22, sem credencial.
- Não executado (`not_run`): `portal-instalar-tunel.ps1` e `ligar`/`recuar` gravando, na forma versionada (o túnel e o
  hostname já estão no ar; a senha só foi ensaiada num arquivo de mentira). O primeiro login do dono pelo endereço
  público funcionou (~22:39Z) e a regra de limite de taxa e o HSTS da Cloudflare estão ligados (22:49Z).

## 2026-10-03 — Portal público no ar em `https://dev.nvit.com.br/central` (29.54, ADR-073; prova real)

- O hostname entrou em `server.public_hosts` do `config.yaml` do central (21:35:02Z, com cópia em `data/backups/`),
  junto de `tls_behind_proxy: true` e da origem `https`. O `avisos.url_painel` passou a apontar para o endereço público.
- O central foi reiniciado às 21:46:56Z (checkout `4ad5f8b6`, código do `2264843e`; migração `086`; `problems: []`).
- Prova de fora, sem credencial, às 21:47:28Z: 19 de 19 (`.claude/handoffs/portal/prova-de-fora.sh depois`). A API
  responde 401, o painel abre em `/central/`, a documentação da API fica atrás do login e o canal do worker não sai
  pelo túnel. O painel local segue sem login.
- Não provado (`not_run`): o primeiro login pelo endereço público, que é do dono.
- Itens novos no plano, da validação do deploy 14 no navegador (0 bloqueante): 29.58, 29.59, 30.41, 30.42 e 30.43.

## 2026-10-03 — 31.24: latência por etapa como métrica de primeira classe (branch feat/31-24-latencia-por-etapa)

- O tempo dentro da tentativa passa a ter dono. Pedido do dono de 03/10; a leitura "antes" dividia a parede em IA 46 %,
  ações no aparelho 5,5 % e 39 % sem atribuição. Só mede: nenhum comportamento muda.
- A migração 088 (`latencia_por_etapa`) traz:
  - em `ai_calls`, o início da chamada e a espera pela vaga de IA (C-3) e o preparo de cada decisão do ator (C-2);
  - `actions.ai_call_id` (C-1);
  - em `attempts`, o juiz, a verificação e a evidência (C-4);
  - a tabela `esperas`, com o motivo de cada espera (C-5).
- "Execução pendurada" é derivada pela leitura, sem gravação e sem laço de vigia (decisão da orquestradora, 03/10).
- `scripts/latencia-por-etapa.py` é a leitura versionada, só leitura e só números e ids. A tabela "antes" (real,
  03/10) está em `docs/ia.md` §18.
- A correção da leitura de 03/10: as "4 execuções penduradas" eram espera de pessoa (`waiting_user` de 22:13Z a
  05:25Z, até o cancelamento).
- Sobrecarga (`simulated`):
  - 0 instrução a mais ao banco por decisão, por ação e por tentativa;
  - até 2 escritas por troca de motivo de espera, e 0 quando nada muda;
  - cerca de 4,8 µs de CPU por decisão;
  - nenhuma chamada a mais ao aparelho nem à IA.
- Arquivos:
  - `backend/migrations/088_latencia_por_etapa.sql`;
  - `backend/app/taskqueue/latencia.py` (novo), `executor.py` e `repository.py`;
  - `backend/app/planning/provider.py` (`PreparoDaDecisao` e os campos novos de `Usage`);
  - `backend/app/devices/manager.py` (`Observation.ms_arvore` e `ms_imagem`).
- Testes: `backend/tests/test_latencia_por_etapa.py` e `scripts/tests/test_latencia_por_etapa.py`.
- Prova: `simulated`; o "depois" real é `not_run` até o deploy.

## 2026-10-03 — Suíte 15 na main e deploy 15 no central (2264843e; sem migração nova; config sem mudança do deploy)

- A suíte 15 foi integrada em `integ/suite-15`, na ordem da orquestradora:
  - #171 29.54, o portal público em `/central` (a1ce153b);
  - #172 30.39, a evidência datada da receita (16142fd1);
  - #170 31.11, o lote offline da intenção (a562169b);
  - #173 31.13, a R5 em sombra travada (313c98de, empilhado no #170; adendos v1.05 e v1.06 em ordem).

  Também entraram a main de então (44cb297c, 9490b897 com o ID 30.40) e os commits da Canais só no mapa do Trello
  (a51ffb14, f592cca4, 7e65846c, bebc8872). A troca `apps_do_comando._nomes` → `nomes_do_app` ficou sem uso antigo.
  O 30.38 (a, b) e o 30.40 ficam para a suíte 16.
- Portões (na a5cc9455):
  - backend SQLite `-n 8` Idle: 8816 passed, 7 skipped, 0 failed (20:47:43–20:55:09Z);
  - scripts 531/0, typecheck ok, frontend 1426/1426;
  - PostgreSQL dirigido, sem migração nova: 58 arquivos (os que a suíte mudou e os do código SQL e da decisão fechada
    que ela tocou), `-n 3` Idle, 2729 passed, 5 skipped, 0 failed (20:58:02–21:25:09Z, na bc6a9e48).

  Os merges depois do portão foram só de docs, do plano e do mapa: pacotes, `relatorio` (387 itens), check e
  docs-check limpos.
- A main avançou por fast-forward para 2264843e.
- Implantado no central (`real`, 03/10, WIN-7S2UASNLFOP):
  - `deploy.ps1 -PularDependencias` (requirements inalterados; frontend reconstruído) com backup `20261003-182909`
    (174.8 MB, integridade ok);
  - backend no ar às 21:29:26Z;
  - health ok: commit 2264843e, `migration 086_sombra_estado_hash`, `problems []`; `/docs` local 404;
  - `config/config.yaml` sem mudança do deploy. O teto por pedido de validação de 0,15, gravado antes pela Aprendizado
    com a autorização da orquestradora, passou a valer neste reinício.
- Agente do notebook em `0.1.0+2264843` (simulação e depois o real; online às 21:31:36Z). O reparo dos 09/10/12/13
  ficou pausado durante a troca (21:30:45–21:31:43Z). Os 01/03/06 seguiram online, sem atenção.

## 2026-10-03 — 31.13, R5: os apps do comando em sombra, travados no código (branch feat/31-13-r5-apps)

- `planning/decisao_fechada/apps.py`: um `noul` por app do cadastro sobre o comando da intenção. O estado é SÓ o
  `comando` (opção A da orquestradora: o `app` da execução é o app principal do plano, o rótulo). O nome do app (C2) vai
  mascarado e sem C7, e a decisão real é a regex do caminho atual sobre o comando original.
- Travada: `privacidade.R5_LIBERADA = False` até o GO do 31.10. `consumidores.apps` no YAML não liga nada, e `/api/ai`
  não anuncia a R5 enquanto travada.
- `decisores.py`: o `noul` vai ao fio (`TIPOS_NO_FIO`), e abaixo do limiar é sem resposta, nunca `nao`.
- `intencao.pedido_c3` é o estado C3 comum à intenção e aos apps.
- A ligação é uma linha no `state.py` (`ligar_apps`).
- O lote offline do 31.11 ganha `--r5`, nos mesmos casos `c`. O hash é provado pelo da intenção, e o estado da R5 está
  contido nele.
- Golden set: §9 (pré-registro da R5) e o viés conhecido do `app` no estado da R2 (§3).
- Testes:
  - `backend/tests/test_decisao_fechada_apps.py`;
  - o fio `noul` em `test_decisao_fechada_jev.py`;
  - R5 em `test_lote_intencao.py` e `scripts/tests/test_jev_braco_offline_intencao.py`;
  - três testes ajustados à mudança intencional (C3 dos apps, `noul` no fio, aviso sem a R5 travada).

## 2026-10-03 — 31.11, R2 e R3 offline: o lote da intenção, só com o hash (branch feat/31-11-r2-r3-offline)

- `taskqueue/lote_intencao.py`: remonta, só para leitura e pelo código do runtime, os comandos que a sombra da intenção
  já mandou ao Jev desde 15:29:51Z (ADR-069 item 21). Só o caso cujo hash do estado redigido bate com o da linha
  (31.22) vai; a salvaguarda "b" é só relatada.
- `scripts/jev-braco-offline-intencao.py`: o braço da intenção, em inglês e em português (D-J7), com portões de
  10 comandos reais e teto de US$ 0,05. A R1 ganha um 2º controle, "regra da saúde", só de acompanhamento.
- Golden set: §8 (pré-registro do lote) e §2 (o `v2` medido antes e depois do 30.39; a regra da saúde).
- Testes: `backend/tests/test_lote_intencao.py`, `scripts/tests/test_jev_braco_offline_intencao.py` e
  `scripts/tests/test_jev_braco_offline.py`.
## 2026-10-03 — 30.44: polimentos da validação do deploy 15 (branch feat/30-44-polimentos)

- O detalhe do item lista a evidência pela data do acontecido, e não pela ordem de gravação.
- O painel mostra o título da etapa ao lado da chave, lido da execução sem gravar (`etapa_titulo`).
- O motivo da eficácia fica legível: "3 de 5 deram certo (60%), abaixo de 80%". A regra D-5 não muda.
- O ritmo por hora do P4 conta pela hora em que a execução nasceu. Antes dava 4 execuções a cada 70 min.

## 2026-10-03 — 30.43: a validação com rosto no item (branch feat/30-43-validacao-com-rosto)

- A re-execução da validação do QA também parte de estado conhecido. O caso foi o 6f459c, que enviou duas vezes.
- A linha `reproducao:` de execução que repetiu o efeito ganha a `invalida` irmã, e o pedido da receita fecha
  `recusada/efeito_repetido`. Na execução orgânica só conta o 29.58. O passo reclassifica o que já existe por regra.
- A receita nascida em validação mostra a origem (`nasceu_em`, no livro, na API e no dossiê) e segue na fila do dono.
- A lista de validações ganha os filtros `item` e `run`. O item mostra os pedidos dele ("Rodou; depois: …" ou "Não
  rodou: …"), e o Resumo da execução de validação mostra o veredito no lugar de "sucesso comprovado".
- Sem migração. Adendo de contrato v1.10.

## 2026-10-03 — 30.41: o teto da prova proporcional ao plano (branch feat/30-41-teto-proporcional)

- A prova de fluxo leva o teto `min(0,40; 0,05 + 0,02 × etapas)` no despacho, e ele vence o gravado. Com 18 etapas
  ou mais, ou com `for_each` de tamanho desconhecido, o pedido fecha `recusada/plano_acima_do_teto` sem executar.
  O mesmo vale para a receita cujo comando resolve num fluxo ativo grande; fora disso, a receita segue com o teto fixo.
- O `for_each` conta pelo tamanho da lista que a origem do fluxo coletou. Medido no P4: o de 8 contatos (35 etapas)
  fecha; antes, gastava 0,157 e enviava 3 mensagens sem evidência.
- Sem migração. Adendo de contrato v1.09. Teste: `backend/tests/test_learning_prova_teto.py`.

## 2026-10-03 — 30.42: a prova de fluxo parte de um estado conhecido (branch feat/30-42-prova-estado-conhecido)

- A execução de prova encerra todos os apps do plano e abre o principal antes da 1ª etapa (nunca `pm clear`), e não
  replaneja.
- Veredito por uma regra única (`domain/prova.veredito_da_prova`): efeito repetido, abertura que falhou e ator que não
  agiu viram a posição nova `invalida` (nem a favor nem contra, tira das contagens o `for`/`against` da mesma
  execução); plano revisado não conta; contra só quando a etapa agiu e a pós-condição não veio.
- Passo de curadoria `ReclassificacaoDoEfeitoDuplicado` (corrige a ev:48 sem UPDATE). Pedido de validação: fecha pelo
  motivo da `invalida`; no máximo 2 provas por item e versão em 7 dias (`limite_de_provas`); `sem_aparelho_novo`.
- Sem migração. Adendo de contrato v1.07. Rollback: apagar as linhas `invalida` antes de voltar o código.
- Testes: `test_learning_prova_veredito.py`, `test_learning_reclassificacao_efeito.py`,
  `test_learning_prova_ponto_de_partida.py`, `test_learning_prova_limites.py`.

## 2026-10-03 — 30.40: o teto do pedido legado e a receita sem caminho (branch feat/30-40-teto-e-sem-caminho)

- O pedido de validação sem `teto_usd` (o legado, de antes do 30.37) herda `teto_por_pedido_usd` no despacho, no mesmo
  UPDATE que liga a execução; o teto já gravado não muda. Assim o legado não depende de UPDATE à mão.
- A receita marcada "variante sem caminho" não recebe `pedir_evidencia` nas opções do curador (`decisoes_do_item`), e
  o esquema estrito do hub só aceita o que foi oferecido. O parecer que a escolher assim mesmo grava
  `invalida:decisao_indevida`, sem pedido. Medido no central às 20:51Z: 5 das 6 marcadas tinham pedido de novo.
- Sem migração, sem rota nova, mesmo `dossie_hash`.
- Testes: `backend/tests/test_learning_prova_validacao.py` e `backend/tests/test_learning_curador_dominio.py`.

## 2026-10-03 — Suíte 14 na main e deploy 14 no central (51270b9c; migrações 084, 085 e 086; config inalterada)

- A suíte 14 foi integrada em `integ/suite-14`, na ordem da orquestradora:
  - 30.37 (d067d904, migração 084);
  - #166 28.15 (69dcea02, migração 085) e as duas revisões da Canais (7fd72929, e657b4c8). A porta única
    `pergunta_sensivel(ref|None)` ficou no commit de integração 7fd72929;
  - 29.52 (90c058e2), a varredura do ambiente dos filhos (c3de7973) e #169 31.22 (82dcde1e, migração 086);
  - o fix da Jev 671a0482 (a leitura da sombra conhece `estado_hash`) e o `ORDER BY seq` num teste do 30.37
    (7ca3e185), com o ok da autora.

  Conflitos só em docs: o de código, em `avisos/infrastructure/servico.py`, foi resolvido juntando os dois lados.
  O #171 (portal público, 29.54) ficou para a suíte 15.
- Portões:
  - na 9c9fe676: backend SQLite `-n 8` Idle 8716 passed, 7 skipped, 0 failed (20:07:32–20:15:16Z); typecheck ok;
    frontend 1422/1422; scripts com as 9 falhas esperadas (`coluna_desconhecida:estado_hash`);
  - na 2497cc93 (com o 671a0482): scripts 508/0;
  - PostgreSQL: um schema novo migrado do zero (80 migrações, até `086_sombra_estado_hash`) e os 21 arquivos dirigidos
    (436 passed, 1 failed). A falha era a ordem de uma consulta de teste sem `ORDER BY`, não o produto. Com a correção, o
    arquivo passou 15/15 no PG `-n 3` e no SQLite.
- Ensaio numa cópia, sem tocar no central: backup `20261003-172214`, `restore.ps1` e migrate com o código da integração.
  Aplicou 084, 085 e 086, com integridade ok.
- A main avançou por fast-forward para 51270b9c (a integração mais os commits só de docs da main).
- Implantado no central (`real`, 03/10, WIN-7S2UASNLFOP):
  - `deploy.ps1 -PularDependencias` (requirements inalterados; frontend reconstruído) com backup `20261003-173512`
    (174.8 MB, integridade ok);
  - restart às 20:35:29Z;
  - health ok: commit 51270b9c, `migration 086_sombra_estado_hash`, `problems []`;
  - `config/config.yaml` inalterado: `avisos.entrada` ausente (desligado), `validacao.modo` off e `public_hosts` vazio;
  - `canal_entradas` e `canal_enviadas` vazias.
- Agente do notebook em `0.1.0+51270b9` (ensaio `-Simular` e depois o real). O reparo dos 09/10/12/13 ficou pausado
  durante a troca e despausado no fim. Os dois workers ficaram com `agent_outdated false` e os 7 aparelhos ligados,
  online.
- Antes do deploy, um incidente no parque: um dump de crash pendente (do crash na saída de um reinício por IRQ)
  prendia todo boot sem janela no diálogo de consentimento. O arquivo foi movido para `data/quarentena-crash/`, e os
  03 e 06 subiram com um `start` cada, sem reset. A prevenção é o item 29.55.

## 2026-10-03 — Painel em `/central` para a exposição pública pelo túnel da Cloudflare (item 29.54, ADR-073; `simulated`)

- Frontend: `base: '/central/'` no Vite (bundles e favicon sob `/central/`). Backend: `PainelEstatico` montado em `/central`; `GET /` e `GET /central` redirecionam (307, `Location` relativo) para `/central/`; nada estático fora de `/central`.
- Portão (`main.guarda`, `security/access.py`) sem mudança de lógica, fixado por `tests/test_portal_publico_central.py` (Host declarado sem credencial, Host não declarado, loopback, login com `Origin` fora e dentro de `allowed_origins`).
- Saúde: problema novo `exposicao_publica_incompleta` (falta `API_TOKEN`, `tls_behind_proxy` ou `https://<host>` em `allowed_origins` com `public_hosts` declarado).
- Docs: ADR-073, adendo v1.05 do contrato da API, seção "Portal público pelo túnel da Cloudflare" em `operacao.md` (procedimento `not_run`; ingress `^/api/worker/` com a barra final), `config.example.yaml`.
- Correções da revisão de risco: docs da API (`/docs`, `/redoc`, `/openapi.json`) movidos para `/api/` (abriam sem credencial pelo Host público); WebSocket do worker recusa Host público na porta do painel quando há listener dedicado; `Cache-Control` do ícone de release `private`; ADR-073 e `operacao.md` com HTTPS obrigatório, `API_TOKEN` longo, tranca global e limite de taxa recomendado.
- `not_run`: túnel no ar e conferências de fora (da orquestradora com o dono); sem script do túnel, webhook ou mudança de cookie neste item.

## 2026-10-03 — 30.39: a evidência datada da receita (branch feat/30-39-evidencia-receita, sem migração)

- Minerador no digest e passo de retrocarga gravam `learning_evidence` de `receita:<id>` (a favor: etapa conduzida só pela
  receita; contra: divergiu ou fechou sem a IA), uma linha por (receita, execução, posição), datada pela etapa; o dossiê da
  receita ganha `evidencias.contadores_e` e, sem amostra de sombra, `evidencias.sombra_e`; `VERSAO_DO_DOSSIE` segue 1;
  `NovaEvidencia.observed_at` opcional. Origem própria `reproducao:<run_id>`: a linha é o registro datado dos contadores e
  não conta de novo (`promocao.efetivas` e os leitores em SQL a tiram), então a saúde, a fila "Revisar", a promoção e as
  métricas não mudam; só o dossiê a mostra. API sem mudança (o dossiê não está em rota).
- Polimento da validação do deploy 13: a linha de evidência no detalhe do item (`DetalheRico`) mostra o texto humano
  (`model.textoDaEvidencia`): sem a marca do conteúdo, a pós-condição pelo rótulo do painel ("nesta execução: app em
  primeiro plano; no fluxo: elemento presente") e "(e mais N)" no lugar de "(+N)"; vale para as linhas antigas (o log não
  muda). vitest do aprendizado 159 passed; typecheck ok.
- Prova: `simulated` (`backend/tests/test_learning_evidencia_receita.py`, suíte `test_learning_*` 882 passam). Real: `not_run`
  (nenhuma execução, aparelho ou conta reais; a volta das receitas ao curador é do deploy da suíte 15).

## 2026-10-03 — 31.22: a sombra guarda o hash do estado redigido (migração 086; branch feat/31-22-estado-hash)

- `decisao_fechada_sombra.estado_hash`: o sha256 do estado DEPOIS do `privacidade.redigir`, em todas as linhas da
  chamada; NULO na recusa de privacidade e no legado (`porta.hash_do_estado`).
- Serve ao lote offline do 31.11, que só reenvia o comando cujo estado remontado bate com o hash (ADR-069 item 21).
- Sem rota nova: nenhuma rota expõe as linhas da sombra, então não há adendo de contrato.
- Teste: `backend/tests/test_decisao_fechada_estado_hash.py`.

## 2026-10-03 — 31.23: estado `v2` da triagem do curador, com sinal (branch feat/31-8-sinal-curador-v2)

- `curador.estado_do_dossie_v2` soma ao `v1` os campos fechados de sinal: versão viva, uso, idade da evidência a
  favor, códigos de saúde e de risco, trilha.
- A privacidade aceita a lista nova; a sombra do runtime segue no `v1` (`ESTADO_DA_SOMBRA`).
- O braço offline ganha `--estado v2`; o critério para o `v2` virar o estado da sombra está pré-registrado no golden
  set §2.
- Testes: `backend/tests/test_decisao_fechada_curador_v2.py` e `scripts/tests/test_jev_braco_offline.py`.

## 2026-10-03 — 29.47, varredura: os outros filhos também não herdam os segredos (branch `fix/ambiente-filhos-varredura`)

- **Antes.** Depois do 29.47, nove lançamentos do `backend/app` ainda herdavam o `os.environ` inteiro, com as chaves
  do `.env`:
  - o PowerShell da leitura do firewall (`rede_firewall.executar_powershell`);
  - o `icacls` de `rede_servidor.restringir_ao_usuario`, `security/local_secret.restringir_acesso` e
    `worker/settings.restringir_acesso` (este também no agente do notebook);
  - o git do Context Retrieval: `github_visibility` (2 chamadas) e `workspace._git_bytes`;
  - o ripgrep do Context Retrieval (`lexical`, 2 chamadas).
- **Agora.** O PowerShell e o `icacls` recebem `ambiente_dos_filhos()`. O git e o rg recebem `sdk.sem_segredos()`,
  que é só a segunda trava: tudo menos os nomes de segredo. Mantêm `GIT_*`, `SSH_*` e `GIT_OPTIONAL_LOCKS=0`. A
  decisão continua pelo nome; o valor não é olhado.
- **Guarda.** Uma varredura por AST em `tests/test_ambiente_dos_filhos.py` recusa no `backend/app`:
  - lançamento sem `env=`;
  - `env=os.environ`;
  - cópia de `os.environ`;
  - `os.system`, `os.popen`, `os.spawn*` e `os.exec*`.

  Exceção única: `supervisor.iniciar_backend`, que lança o próprio backend. Contra a árvore anterior, a guarda aponta
  exatamente os 9 lançamentos; contra a nova, nenhum.
- **Prova `simulated`**: `tests/test_ambiente_dos_filhos.py`, 14 testes.
  - Os testes por chamada capturam o `env` e só olham NOMES, com variáveis sentinela de valor falso.
  - Um teste usa filho real: o PowerShell do firewall lista os nomes que recebeu.
  - Os 39 arquivos de teste que importam os módulos tocados: 1164 passed, 9 skipped.
- **Fumaça local, nível `real` local** (03/10, central, sessão 1, código do branch): o script real de leitura do
  firewall (só leitura) rodou com o ambiente filtrado, 36 de 96 nomes. Deu 3 perfis, 4 redes e 16 regras, uma saída
  idêntica à do ambiente inteiro.
- **`not_run`**: a observação, depois do deploy, dos filhos curtos (PowerShell, `icacls`) no central e no agente.
  Os qemu do 01/03/06, lançados antes do deploy 9, seguem com o ambiente antigo até o próximo boot. Sem reinício nem
  deploy nesta entrega.

## 2026-10-03 — 29.52: a resposta com credencial é recusada pelo contexto (painel e canais; branch, suíte 14)

- Branch `fix/29-52-resposta-com-credencial`, ainda fora da main: entra na suíte 14. Adendo v1.03 e K-089, sem
  migração.
- A regra é uma só, na `TriagemDeCredencial`: `pergunta_sensivel` (o tipo que a pergunta pede) e `resposta_recusada`
  (o texto de pessoa mais o código solto de 4 a 8 dígitos, `parece_codigo`).
- O caminho comum fica em `taskqueue/perguntas.py`:
  - 409 `credencial_na_resposta` na sucessora e no refinamento, antes da IA e de qualquer gravação;
  - a palavra solta no pedido novo, quando há pergunta de senha ou código aberta para o aparelho;
  - o evento `pergunta_sensivel`, só com o id e o tipo;
  - duas leituras públicas para os canais.
- No painel, a pergunta sensível mostra a orientação no lugar da caixa de resposta: "Abrir Personas" ou "Abrir o
  aparelho".
- Prova `simulated`:
  - backend: `test_resposta_com_credencial.py` (8), mais 772 testes afetados, com arquitetura;
  - frontend: 1419/1419 e typecheck;
  - percurso no navegador contra o backend simulado do worktree (porta 8766, planejador simulado que pergunta a senha
    ou o código; nada no central): as duas orientações, "Abrir Personas", "Abrir o aparelho" (o foco com "Assumir
    controle") e o 409 de formato no assistente da resposta.
- `not_run`: a suíte inteira (fica para a suíte 14), o canal sobre as mesmas leituras (commit de integração) e o
  painel no ambiente central (depois do deploy 14).

## 2026-10-03 — 28.15: a conversa de volta pelo Telegram (migração 085; branch feat/28-15-telegram-entrada; desligada de fábrica)

- O mesmo bot do aviso agora também recebe, com o dono como único interlocutor (o `TELEGRAM_CHAT_ID`). Pelo chat ele
  aprova, veta (com nota), responde à pergunta de uma execução, faz pedidos (`/para` e texto livre) e consulta
  `/status` e `/pendencias`.
  - Toda ação passa pelos serviços das rotas do painel: prévia de alvos obrigatória com os botões Executar e Cancelar,
    approval_required, pré-voo e tetos.
  - O operador é `telegram:dono`. O `/orq` e o reply a uma mensagem que a Central não mandou ficam guardados para a
    orquestradora, sem execução.
- Long-poll com o offset no banco. Um 409 vira `telegram_entrada_conflito` na saúde e uma espera, sem disputa. Os
  limites são 10 mensagens por minuto e 1000 caracteres.
- Migração 085 genérica (`canal_entradas` e `canal_enviadas`, com a chave `(canal, id_externo)`): o Trello do 32.2 usa
  as mesmas tabelas.
  - A gramática e a parte comum do serviço não conhecem canal. Cada canal traduz o que chegou numa `Recebida` e
    responde por uma `SaidaDaConversa`.
- Credencial ou código: a mensagem é recusada sem guardar e apagada do chat (`deleteMessage`). Se não der para apagar,
  a resposta pede ao dono que apague.
- Com a conversa ligada, o aviso de aprovação e o de pergunta levam o conteúdo, redigido e cortado em 500 caracteres
  (decisão (d)).
- Documentado no ADR-071, no adendo v0.98 do api-contract, em `operacao.md` §15.1, em `banco.md` (085) e em
  `config.example.yaml`.
- Correções da revisão do PR #166 (`test_telegram_correcoes.py`, `simulated`):
  - B1: na 1ª subida o histórico do chat é descartado (`getUpdates` com `offset=-1` e uma linha-marco), não executado;
  - B2: a resposta a uma pergunta que pede senha, código, 2FA ou token é recusada pelo contexto (vocabulário da triagem de
    credencial), apagada do chat e nunca gravada; o 409 `credencial_na_resposta` do caminho comum é final;
  - B2 (canal): com uma execução esperando senha, código, 2FA ou token, a palavra solta (sem reply e sem `/responder`) é
    recusada, apagada do chat e não gravada; sem poder ler as perguntas, falha fechada;
  - I3: a update que não grava vira `falhou` sem texto e o offset anda; I4: a prévia vence em `ttl_previa_s` (900 s);
  - I5: o dono é `chat.type = private` com `from.id` igual ao chat, na mensagem e no botão;
  - I7: o 429 honra o `Retry-After`; o 401/403 vira o problema `telegram_entrada_recusada` e espera como o 409;
  - menores: linha presa em `executando` reparada, texto longo com cara de senha também apagado, dica do 409 com webhook
    e o script `avisos-telegram.py descobrir`, nota de troca de chat ou bot no `operacao.md`.
- Correções da 2ª revisão (`test_telegram_revisao_e.py`, `simulated`):
  - E2: em toda subida, a mensagem escrita há mais de `idade_max_s` (900 s) fica `ignorada`, sem texto, e o dono recebe
    um aviso só; a senha antiga ainda sai do chat;
  - E6: com pergunta de senha aberta, o texto curto (até 3 palavras) é recusado também como recado à orquestradora e
    como `/responder` sem id, e a resposta pede o pedido com mais detalhe; a resposta atrasada (execução já fora do
    `needs_input`), pelo id inteiro, ainda é julgada pela pergunta que a execução fez;
  - E7: cada recusa do `getUpdates` com a sua causa (401, 403 e 404 em `telegram_entrada_recusada`; o 400 em
    `telegram_entrada_pedido_invalido`, que não manda trocar o token);
  - E8 e E9: `operacao.md` §15 sem grupo (só conversa privada), e o ADR-071 (decisão 10), o adendo v0.98 e o `banco.md`
    com as emendas das duas revisões.
- Prova:
  - `simulated`: `test_telegram_entrada.py`, `test_canais_contrato.py` (o mesmo comando por `telegram` e `trello`
    passa pelas mesmas políticas), `test_avisos_servico.py` e `test_telegram_portas.py`;
  - `not_run`: a conversa real, que depende do "vai" da orquestradora para trocar a caixa provisória.

## 2026-10-03 — Painel: de onde veio cada execução, e os pedidos de validação na aba Aprendizado › Validação (30.38 a, b; branch feat/30-38-ab-validacao)

- **A origem da execução (a).**
  - Antes: a execução de validação do QA aparecia na lista como um pedido comum; só a prova de fluxo tinha selo.
  - Agora: `RunSummary.origem`/`origem_ref`, derivados da linha pela regra única de `app/contracts/origem.py`
    (prova de fluxo, validação do QA, Telegram, Trello), sem migração. O painel põe o selo na lista e no resumo; na do
    sistema o texto vira "Comando de origem", e a validação leva ao pedido dela. O prefixo `validacao:` é uma constante
    só, com teste.
- **A leitura dos pedidos (b).**
  - Antes: os pedidos de `learning_validations` só existiam no banco.
  - Agora: `GET /api/aprendizado/validacoes` (só leitura: estado, motivo em texto, app, aparelho, custo e teto, comando
    cortado em 200, contagem por estado e o modo do despachante) e a aba Validação, com fichas por estado, cartões e o
    vazio que explica a validação e a pausa. O comando é só para o painel: a rota não é fonte para Trello nem Telegram.
- **O selo sumia ao abrir a execução.** Achado no percurso do navegador: o resumo que volta à lista quando o detalhe
  chega (`store/live.ts` `summaryOf`) copiava só os campos antigos e trocava a linha sem `origem`, `origem_ref`,
  `prova_fluxo_id`, `pedido_id` e `app_ids`. Agora copia os opcionais que o detalhe traz (teste em `live.test.ts`).
- Contrato: adendo v1.02. Prova `simulated` (backend e frontend); `not_run` no central até o deploy da suíte 15.

## 2026-10-03 — Aprendizado: a validação do fluxo roda o próprio fluxo, e a evidência vem das etapas da prova (30.37; branch feat/30-37-prova)

- **A execução de prova.**
  - Antes: a validação por re-execução rodava o planejador livre; o candidato ficava inerte e o fluxo ativo não ganhava
    evidência (K-086: nenhuma das 13 evidências de fluxo vinha de execução que usava o próprio fluxo).
  - Agora o pedido de validação de FLUXO roda o plano do próprio fluxo com os parâmetros do comando de origem
    (`runs.prova_fluxo_id`, migração 084), sem planejador, sem RESOLVE, sem `flow_id`, `used` nem `skill_hash`, fora da
    sombra da intenção, e sem ensinar fluxo novo.
  - Emenda datada à D1 do ADR-054: validação de fluxo pelo próprio fluxo; a receita segue por re-execução.
- **A evidência vem das etapas.** A favor: `completed` com todas comprovadas. Contra: etapa reprovada na própria
  pós-condição. Infra (erro de IA, teto, aparelho, etapa que pediria pessoa) não conta. Vale também para o fluxo ativo;
  o D1 só avalia o que ainda está em prova.
- **A prova nunca espera pessoa.** `needs_input`, `approval_required` ou incerteza encerra a execução pelo sistema, na
  hora (sem `cancelou_execucao`, sem pergunta, sem aviso), e o pedido fecha `sem_evidencia`.
- **No painel e na API.** "Prova de fluxo (validação)" (`RunSummary.prova_fluxo_id`), nunca comando de pessoa nem aviso.
- **Teto por pedido** `aprendizado.validacao.teto_por_pedido_usd` (US$ 0,10), aplicado pelo roteador como o teto do
  28.6 (o menor), também nas reaberturas. O teto total do P4 segue US$ 3,09.
- **`sem_caminho` do fluxo** (comando fora do molde); só a receita `sem_caminho` volta ao curador.
- **Reabertura:** o pedido de fluxo fechado `sem_evidencia` ou `divergencia_de_forma` ganha um pedido novo, uma vez,
  que roda como prova (3 esperados no central).
- Contrato: adendo v0.97. Banco: migração 084 (só `ADD COLUMN`).
- Prova `simulated`: `tests/test_learning_prova.py`. `real`: `not_run` até a 1ª validação de fluxo depois do deploy que
  levar o 30.37; o P4 fica pausado (`validacao.modo: off`) até lá.
## 2026-10-03 — Suíte 13 na main e deploy 13 no central (1c54a7bb; migração 083; config inalterada)

- A suíte 13 foi integrada em `integ/suite-13` na ordem da orquestradora:
  - #162 31.21 (e12d15fd, migração 083);
  - #163 30.36 (59c84eaa);
  - #165 30.38-c (657ab399);
  - os polimentos do deploy 12 (a742f75f, item 29.51).

  Os conflitos, só em CHANGELOG, api-contract e runner, foram resolvidos por união. Os adendos ficaram na ordem v0.95 →
  v0.96 → v1.00 → v1.01. O run de CI do #165 foi cancelado por ser redundante.
- Portões na 798f1849:
  - scripts 487; typecheck ok; frontend 1415/1415;
  - backend SQLite `-n 8` Idle: 8503 passed, 7 skipped, 0 failed (18:26–18:34Z).
  - PostgreSQL: parado em 27% por decisão da orquestradora, com 2370 passed e 0 failed. Ele é lento pelo advisory lock da
    migração por schema. A 083 foi provada à parte no PG: um schema novo migrado do zero até `083_sombra_postado`
    (77 migrações, coluna `postado`). O PG completo será relançado depois das conferências.
- Ensaio da 083 numa cópia, sem tocar no central: backup `20261003-154300`, `restore.ps1` para `C:\temp\ensaio-083` e
  migrate com o código da integração. Aplicou só a 083, e as 320 execuções ficaram intactas.
- A main ficou igual à integração mais o merge dos commits só de docs (1c54a7bb), sem diferença de código para a
  798f1849.
- Implantado no central (`real`, 03/10, WIN-7S2UASNLFOP):
  - `deploy.ps1` com backup (`20261003-154944`, 174.8 MB, integridade ok);
  - antes, 0 execuções em andamento e 0 comandos em voo;
  - restart às 18:50:02Z;
  - health ok: commit 1c54a7bb, `migration 083_sombra_postado`, `problems []`;
  - `config/config.yaml` inalterado (`validacao.modo` segue off até o deploy 14).
- Agente do notebook em `0.1.0+1c54a7b` (ensaio `-Simular` e depois o real). O reparo dos 09/10/12/13 ficou pausado
  durante a troca e despausado no fim. Os dois workers ficaram com `agent_outdated false` e os 7 aparelhos ligados,
  online.

## 2026-10-03 — Golden set: o rótulo "2v" (validação automática) pré-registrado como proposta, não vigente

- Nota datada em `docs/design/jev-golden-set.md` §2. O GO não muda; a orquestradora reavalia depois do deploy 14.

## 2026-10-03 — ADR-069, item 21: a C3 em mais dois usos (decisão do dono, ~18:39Z)

- Emenda ao item 4 em `docs/decisoes.md`: o comando já filtrado pode ir ao Jev também na R5 em sombra (31.13) e no
  lote offline do 31.11.
  - A R5 só liga depois do GO do 31.10.
  - O lote usa só comandos já enviados pela sombra da intenção depois de 15:29:51Z.
  - C7 segue nunca; o filtro do item 10 e os tetos do item 7 seguem valendo.

## 2026-10-03 — Jev: registro do braço offline (31.11) e contagem de rótulos do curador no golden set

- `docs/design/jev-golden-set.md` §2 ganhou um registro datado.
  - O braço offline usa o `DecisorJev` pela `Porta`, sem o adaptador MIT.
  - A "pergunta 4" já está respondida pelos itens 7 e 9 do ADR-069.
  - A contagem real de hoje (18:33:11Z, central em `mode=ro`, deploy 12 `d5a1c3a9`) deu 0 rótulos 1 e 2 nas 28 revisões
    de receita do escopo da R1. Nenhum limiar mudou.

## 2026-10-03 — Android: seis polimentos do Chrome do deploy 12 (branch feat/polimentos-rede-deploy12, sem migração)

- (1) Títulos sem as lacunas de parâmetro em claro. Métricas, Para aprovar e Fluxos e receitas mostravam
  `"{message_template}"`. Agora sai "…", e o original vai na dica. Os marcadores do comando-modelo dizem
  "message template", com `{message_template}` na dica.
- (2) Em Fluxos e receitas, o fluxo candidato ganha o selo "candidato" e o interruptor "Não vale ainda". Antes, ele
  aparecia como "Desativado". O desligado diz quem o desligou, quando e por quê, com o motivo da trilha (`detalhe`
  do Livro).
- (3) Nos Sinais, a decisão de aprovação vem sem `app_package`, e o painel mostrava `REPLY_COMMENT` cru.
  - `nomear()` agora procura a capability nos catálogos declarados e só a nomeia se um único app a declara
    (`TitulosDoCatalogo.apps_que_declaram`). Com dois apps, o nome fica nulo.
  - Se ainda faltar nome, o painel diz "decisão de aprovação", com o código na dica.
- (4) `/api/aprendizado/pendentes` era lido 3× na carga e agora é lido uma vez. As leituras dentro de 2 s
  compartilham a mesma resposta, e toda decisão (mudar estado, confirmar, invalidar, parecer) esquece essa leitura.
- (5) O aviso de `/api/ai` diz "é enviado ao provedor". Antes saía "a o provedor".
- (6) Execução parada porque o fluxo salvo não compilou para o pedido: o `status_detail` agora é "O fluxo salvo “…”
  não serviu para este pedido: <motivo>. Responda à pergunta ou peça de novo pelo painel." A skill publicada aparece
  como "A habilidade salva". O id e o código continuam fora do texto: ficam no evento `log` e no `issue_codes` do
  `run.updated`. As execuções antigas com o texto cru saem pela expiração do 29.50.
- O contrato dos itens 3, 5 e 6 está no adendo v1.01 de `docs/api-contract.md`. O texto antigo do v0.87 fica como
  histórico.
- Simulado:
  - frontend: `aprendizado/model.test.ts`, `settings/FlowsRecipesSection.test.tsx`,
    `aprendizado/AprendizadoPage.test.tsx` e `app.integration.test.tsx`;
  - backend: `test_learning_capability_na_linha.py`, `test_leitura_visual_papel.py`,
    `test_needs_input_da_habilidade.py` e `test_fatia_abrir_conversa.py`.

## 2026-10-03 — Android: dois polimentos da Rede dos relatórios do Chrome dos deploys 10/11 (branch feat/polimentos-rede-deploy12)

- "Saída pela casa" cita no máximo 3 ids. Acima disso, mostra os 3 primeiros e "e mais N", com a lista inteira na dica.
  No central eram 14 ids seguidos, e a tabela logo abaixo já diz aparelho por aparelho.
- A carga da Rede lê `/api/network/profiles` e `/api/network/devices` uma vez só (eram 2× por carga).
  - Causa: o `carregar` dependia do estado `temDados`, e a 1ª carga trocava a identidade dele e refazia o efeito de
    montagem.
  - Agora é uma ref, e o aviso de falha ao recarregar segue igual.
- Simulado: `frontend/src/features/rede/RedePage.test.tsx`, com 2 testes novos que falhavam antes da correção (5
  ids → "e mais 2" com a dica; 1 chamada de cada lista). O arquivo passou 29/29 e o typecheck, ok.
## 2026-10-03 — Aprendizado: o painel mostra a classe do gesto, não a gravada (30.38-c; branch fix/30-38c-classe-de-agora)

- No parecer pendente do detalhe e da fila, `classe` passa a ser a de AGORA, a mesma do aceite. `classe_no_parecer`
  traz a gravada quando difere, e o selo diz "(era B no parecer)".
- Antes, os 9 fluxos do Instagram com parecer anterior ao 30.32 apareciam como "Classe B · aceite em lote", e o clique
  voltava 409. Nada era aceito em lote por engano: o gesto já usava a classe de agora.
- Contrato: adendo v1.00, parcial (a (a) e a (b) do 30.38 vão para a suíte 14).
- Prova `simulated`: `tests/test_learning_pareceres.py` e `ParecerDaIA.test.tsx`. `not_run`: o central.

## 2026-10-03 — Aprendizado: a divergência de forma não é evidência contra o fluxo, e a receita sem caminho não gasta execução (30.36; branch feat/30-36-forma)

- **A sombra do fluxo separa caminho de forma.**
  - Antes: a 1ª validação do P4 (r-20261003160725-213aae, android-09) comprovou 2/2 etapas, e a sombra gravou `against`
    "etapa 1: outra ação" só porque o planejador reescreveu as pós-condições. Duas dessas desligariam um fluxo que
    funciona.
  - Agora são `forma`: a pós-condição de etapa sem efeito, ou o parâmetro que nenhuma etapa usa para agir. A forma não
    conta contra nem a favor.
  - Na etapa de efeito, a pós-condição segue sendo caminho.
  - Emenda datada à D1 do ADR-054.
- **Contra efetivo num ponto só** (`promocao.efetivas` e `linhas.contra_efetivo`). Vale para o veredito, a saúde, as
  Métricas, a regressão da autopublicação, o dossiê, a seção Evidência e o bloco da execução.
- **Reclassificação.** O `against` antigo de forma, nos fluxos ainda em prova, ganha a linha `forma` ao lado, sem apagar
  nada. É idempotente e é um passo da curadoria.
- **O pedido da validação fecha pela evidência.**
  - Motivos novos: `evidencia_contra`, que leva o curador ao item; `divergencia_de_forma`; e `sem_caminho`.
  - O `sem_caminho` vale para a receita cujo plano do fluxo ativo não chega à etapa. Fecha sem execução, ao nascer, ao
    despachar e no `sem_evidencia` de antes, e o dossiê ganha a marca "sugerir aposentar".
  - Medido no P4: 5 das 11 receitas pendentes eram variantes inalcançáveis, ~US$ 0,35 de execuções poupadas.
- **Na cópia do banco do central, a 1ª curadoria depois do deploy:**
  - 1 linha `forma`;
  - 2 pedidos remotivados;
  - 5 pendentes fechados `sem_caminho`;
  - 6 revisões novas do curador, ~US$ 0,06.
- Limite conhecido, para decisão do dono: a validação por re-execução mede o planejador livre, não o fluxo candidato.
- **Dois achados do P4, pedidos pela orquestradora no mesmo PR.**
  - A chegada da validação ao curador (`evidencia_chegou`) não depende mais do `modo` do despachante. Com o P4 pausado,
    o `feita` das 16:37:59Z ficava preso.
  - A 1ª espera do laço do curador conta da última revisão gravada, com piso de 60 s (K-087). Antes, cada restart
    zerava a hora.
- Contrato: adendo v0.96.
- Prova `simulated`: `tests/test_learning_forma.py` e `DetalheRico.test.tsx`. `not_run`: o central.
## 2026-10-03 — 31.21: a sombra diz se a chamada ao Jev chegou ao POST (migração 083; branch feat/31-21-postado-na-sombra)

- Migração 083: `decisao_fechada_sombra.postado` e `ai_call_id`, em todas as linhas da chamada, como `ms`.
  - `postado` vale 1 quando o transporte foi chamado, 0 quando a chamada parou antes e NULO quando não se sabe.
  - `ai_call_id` é o `ai_calls.id` que `RepositorioDeSombra.registrar_chamada` passa a devolver.
- O decisor real e a porta repassam a marca:
  - parou antes do POST (privacidade, decisor nulo, só `noul`/`score`, orçamento, prazo esgotado, chave ausente): 0;
  - o transporte foi chamado (resposta ou erro dele): 1 com o id;
  - a linha de gasto não gravou (régua cega): 1 sem id;
  - exceção inesperada, ou futuro em voo no `on`: NULO;
  - a `rede` acima do prazo da sombra guarda a marca do decisor.
- Antes, a `rede` juntava quatro casos que a linha não separava, e o cruzamento sombra × `ai_calls` só podia informar.
- Prova: `simulated` (`backend/tests/test_decisao_fechada_postado.py`, 19 casos; mais 2.057 testes da porta, da sombra,
  das migrações e do banco). PostgreSQL `not_run`: o banco de teste da 55433 estava desligado. Central `not_run`: a 083
  ainda não está implantada.
- A leitura (`scripts/jev-leitura-intencao.py`, que entrou pelo PR #160) conhece as colunas da 083 e mede:
  - as chamadas por marca;
  - a `rede` antes do POST, depois dele e sem marca;
  - a régua cega;
  - o cruzamento por id, que reprova quando um `ai_call_id` não é linha do Jev da intenção em `ai_calls`.

  Marca incoerente é violação. Um banco anterior à 083 é lido sem marca. Prova: `simulated` (5 testes novos em
  `scripts/tests/test_jev_leitura_intencao.py`). **A 083 não pode ser implantada sem este commit**: o script anterior
  falha fechado em coluna desconhecida. Golden set §7.
## 2026-10-03 — Canais externos: o contrato comum do Telegram (28.15) e do Trello (32.2)

- `docs/design/canais-externos.md`, aprovado pela orquestradora (~18:15Z) e citado pelos ADR-071 e ADR-072. Define:
  - a entrada comum `Comando(canal, autor, id_externo, alvo, verbo, argumento)`;
  - a gramática fechada, alinhada ao cf7303ba da Android: `/ajuda`, `/status` (`/estado`), `/pendencias`,
    `/aprovar` e `/vetar <id> [nota]`, `/responder <id> <texto>`, `/para <aparelho|persona> <objetivo>`, o texto
    livre e `/orq`; com fato, "sim" e "não";
  - o fato pelo reply ou pelo cartão;
  - a identidade conferida na entrada, com o operador `telegram:dono`;
  - as políticas do painel;
  - o dedupe pela 085 genérica `canal_entradas`, com a 086 do 32.2 reduzida a cartões e cursor;
  - a credencial por formato, que apaga quando pode e não ecoa;
  - a redação na saída e os testes de contrato.
- `trello-integracao.md` §3 alinhado: o `/para` é pedido com destino (não há verbo de parar), e o comando livre fica
  desligado de fábrica no Trello.

## 2026-10-03 — 32.1: estudo da integração da Central com o Trello do dono (branch docs/32-1-estudo-trello)

- `docs/design/trello-integracao.md` (até 2 páginas; sem código; nenhuma chamada ao Trello, `not_run`). Cobre:
  - o acesso: REST com chave + token só no `.env`, o passo a passo do dono, os limites, e polling no lugar do
    webhook, que exigiria URL pública;
  - o espelho: um cartão por fato, sem ruído por evento;
  - os comandos de volta: mover para Aprovado/Vetado e a gramática `/aprovar`, `/vetar`, `/responder`, `/para`,
    `/estado`, proposta comum ao 28.15. A identidade é o membro do dono, com dedupe pela action id;
  - os vínculos ao painel, ao plano, aos ADRs, aos K-*, ao conhecimento de app e ao Livro, e a rota proposta
    `GET /api/conhecimento/resumo`;
  - o que não fazer;
  - o desenho do 32.2 em 10 linhas.
- Decidido pela orquestradora (03/10, ~18:05Z): token sem expiração com revogação documentada, as 3 listas criadas
  por ela no Execução, conteúdo redigido (a regra do Telegram) e link do painel só na LAN (limitação no ADR-072). Fase 32
  no plano-100: 32.1 (este estudo) e 32.2 (implementação, Android, depois do 28.15).

## 2026-10-03 — Jev: leitura preliminar real da sombra de intenção (31.10 partial/real)

- Central WIN-7S2UASNLFOP, deploy 12 (`d5a1c3a9`), às 17:19:44Z.
  - Script: `scripts/jev-leitura-intencao.py`, mode=ro, `--desde 2026-10-03T15:29:51Z`, só contagens.
  - Preliminar porque o P4 foi pausado às 17:18Z (K-086).
- Resultado (veredito OK, zero violação de formato):
  - 6 linhas = 6 chamadas = 6 comandos.
  - R2: 4 respondidas, todas com P(escolha) ≥ 0,88; 1 recusa de privacidade (`c7_gatilho`); 1 `abaixo_do_limiar`.
    R3: 0.
  - Custo: US$ 0,000255 em 5 chamadas; latência p50 445 ms e p95 520 ms.
  - O cruzamento com `ai_calls` bate (5 × 5).
- O 31.10 segue `partial` pelo mecanismo. Fecha com a leitura de 10 ou mais linhas, que depende de comandos reais do
  dono enquanto o P4 estiver pausado. Golden set §7.

## 2026-10-03 — Deploy 12 no central (d5a1c3a9; P4 religado; expiração do needs_input no ar)

- Implantado no central (`real`, 03/10, WIN-7S2UASNLFOP): o commit d5a1c3a9 = a suíte 12 (5428abdb) mais os estados
  da Android. Não há migração nova. Health ok, `migration 082_learning_validations`, `problems []`.
- Antes do restart:
  - nenhuma execução em andamento nem comando em voo (às 17:02:05Z e às 17:02:26Z);
  - backup do banco `data/backups/20261003-140206` (174.8 MB, integridade ok);
  - checkout em fast-forward. O `config.yaml` sobreviveu (sha 9f52df5f).
- Restart às 17:02:42Z (`deploy.ps1 -PularBackup`, rc 0).
- `config/config.yaml` (fora do Git; backup `config-antes-deploy12-20261003-170220.yaml`, sha 9f52df5f → 4a1bae92):
  - a única troca é `aprendizado.validacao.modo` "off" → "on", com os mesmos `extra_usd 2.5` e
    `extra_ate 2026-10-05T15:55:42.000Z`. O P4 da Aprendizado religa no próprio restart.
  - O `load_config()` do código novo conferiu ainda: a decisão fechada com curador e intenção em sombra e as classes
    C0/C1/C3; a autopublicação em sombra; o curador com alfa 0.10 e k 1.5.
- `GET /api/ai`:
  - anthropic configurada e não simulada;
  - a Jev é o decisor, com curador e intenção em sombra, classes C0/C1/C3 e envio ativo.
- 29.50 no ar:
  - a 1ª volta da varredura, na subida, não expirou nada, porque nenhuma pergunta tinha 24 h. As 13 `needs_input`
    seguem.
  - A prova `real` fica para a primeira volta depois de 03/10 18:15:30Z. Nessa hora vence a execução do android-05,
    que entrou na pergunta em 02/10 18:15:30Z.
- Agente do notebook atualizado para `0.1.0+d5a1c3a`, com o reparo dos 09/10/12/13 pausado durante a troca e
  despausado no fim. Os dois workers ficaram com `agent_outdated false`, e os 7 aparelhos ligados, online.

## 2026-10-03 — Suíte 12 na main (5428abdb): SQLite inteira verde; sem PostgreSQL (sem migração nova)

- A suíte 12 entrou na main por commit-tree (5428abdb), na ordem da orquestradora:
  - Jev: #157 (05b96b8f), K-085 percentil (fe5b469e), #160 (8b9b2548) e o índice do ADR-069 item 20 (aa4f67cf);
  - Android: #158 K-084 (5f8a76e5), 29.50 (38b21238) e #161 polimentos do painel (9f65f22f);
  - Aprendizado: #159 30.33-C (235e8451) e, por cherry-pick no fim, o percentil do #159 (e8af149b), que só fecha com
    o K-085 na árvore.
  - Conflitos só de texto: CHANGELOG e `docs/api-contract.md` por união. O adendo v0.94 foi posto antes do v0.95.
- SQLite (`real`, 03/10, WIN-7S2UASNLFOP, `-n 8`, Idle, @a5842d4a, das 16:49 às 16:56Z): 8460 passed, 7 skipped,
  0 failed. Scripts deram 482 passed, docs-check 0/0, typecheck ok e frontend 1407 passed, no mesmo HEAD.
- Depois do cherry-pick do percentil (@82b09705): os `test_learning*` com `test_metricas` deram 859 passed, e
  `test_arquitetura` 9 passed.
- PostgreSQL: não rodou, porque não há migração nova (a 083 é da suíte 13). O K-084 corrige os 7 testes que só
  quebravam lá; a prova na PG é a do PR #158.

## 2026-10-03 — Aprendizado: o item de mais de um app na leitura por app e os polimentos das Métricas (30.33-C; branch feat/30-33-multi-app)

- O fluxo que atravessa apps aparece em cada app dele: na visão por app, no filtro `?app=` do livro e nas Métricas
  (recorte, saúde, revisões e economia). Antes ficava só no app principal (validação do deploy 10: o fluxo do Outlook
  sob o Instagram).
  - O dado não estava errado e não mudou.
  - O balde `nao_resolvido` não muda.
- O dossiê do curador ganha `item.apps` (id, pacote e principal) só no multi-app. No item de um app só, o
  `dossie_hash` fica igual.
- 30.31: o item multi-app é QA só com todos os apps de QA, e o aparelho precisa de todos eles prontos.
- Painel:
  - "Microsoft Outlook → Instagram" na linha do item e nos pareceres;
  - os pareceres mostram o título do item e o nome do app;
  - o orçamento mostra os dois ramos e qual manda (sai o "piso");
  - a sombra da autopublicação e o desfecho em 14 dias aparecem na tela.
- Contrato: adendo v0.94.
- Prova `simulated`:
  - `test_learning_multi_app.py`;
  - dois casos em `test_learning_validacao_sql.py`;
  - `MetricasTab.test.tsx` e `AplicativosTab.test.tsx`;
  - percurso no navegador sobre uma cópia do banco do central.

## 2026-10-03 — ADR-069 item 20: o limiar da porta sobre a probabilidade devolvida e o rótulo 1 do curador só do dono (branch docs/adr-069-item-20, para a suíte 12)

- Documentação e processo: registra as duas decisões da orquestradora do 31.19 (03/10 ~15:15Z), que já estão no
  código da suíte 11 (442a9249). A porta mede o limiar na probabilidade devolvida da escolha, que precisa ser a maior;
  o rótulo 1 do curador só vale do autor dono declarado (`--autor-dono`, `Flavio`). Inclui as ressalvas do nome do
  painel. Nenhum código muda.
## 2026-10-03 — Jev: a leitura da sombra de intenção (R2/R3) pronta para depois do deploy 11 (branch feat/jev-leitura-intencao)

- `scripts/jev-leitura-intencao.py`, só leitura e sem IA (irmão do `jev-prova-31-17.py`, para a origem `intencao`). Mede:
  - contagens por origem/classe/modo e, por pergunta R2/R3, pedidos, respondidas, abstenções e fallbacks;
  - recusas por `fallback_reason` e `motivo_privacidade` (só o vocabulário fechado);
  - confiança, P(escolha) e maior probabilidade contra o limiar da porta;
  - US$ total e por chamada e ms p50/p95 pelo posto mais próximo.
- A asserção de zero texto falha fechado: valor fora do formato da coluna, JSON que não é {id opaco: número} ou coluna
  fora de 074/079 é violação. O valor nunca é impresso; nas contagens, aparece `(fora do vocabulário)`.
- Prova: `simulated` (`scripts/tests/test_jev_leitura_intencao.py`, 8 testes com linhas falsas). Execução no central:
  `not_run` (espera o aviso da orquestradora). Documentado em `docs/design/jev-golden-set.md` §7.

## 2026-10-03 — K-085: o percentil de `app/metricas.py` pelo posto mais próximo, sem o arredondamento de banqueiro (branch fix/k085-percentil, para a suíte 12)

- `metricas.percentil` calculava o posto como `round(p/100·n + 0,5)`. O `round` do Python leva o ,5 ao par, então,
  quando `p·n/100` dava inteiro ímpar, o posto subia um. Isso acontecia em 13 de 160 casos com n ≤ 40 e p em
  {50, 90, 95, 99}; com n=2, por exemplo, o p50 dava o maior dos dois valores.
- Agora o posto é `ceil(p·n/100)`, em aritmética exata (`Fraction`). Afeta o p50 e o p95 de `GET /api/desempenho`: as
  distribuições do processo (C5) e o histórico do `desempenho.py`.
- `sombra._p95`, que grava `decisao_fechada_diario.ms_p95`, passa a delegar a ele: um percentil só no processo, o mesmo
  dos scripts do Jev (31.19).
- Prova `simulated`: `tests/test_metricas.py` com os 13 casos e a igualdade com a sombra para n de 1 a 200; os 38
  arquivos de teste que tocam percentil passaram (725 testes).
- Fora deste item: `modules/learning/application/metricas.py::_percentil` (os `tempos` do Aprendizado) é uma cópia com
  o mesmo defeito.
## 2026-10-03 — Painel: os 4 polimentos de UX que vinham dos deploys 9 a 11 (branch feat/polimentos-ux-deploy11)

Só frontend; nada de backend.

- Painel, último comando do cartão:
  - Antes, "Comando: Sem resposta · <verbo> · há N dias" aparecia em 11 dos 14 cartões. Era o último `uncertain`, e não
    o último comando, porque o snapshot só traz os em voo e os `uncertain`.
  - Agora o `live.ts` lê os comandos recentes de cada aparelho depois do snapshot: 20 por aparelho e, se nenhum deles
    for de pessoa, 200. No central, o primeiro de pessoa era o 31º do android-03 e o 59º do android-06, por causa da
    sonda.
  - O cartão mostra o comando em voo (de quem for, porque é ele que bloqueia os verbos). Sem nada em voo, mostra o último
    de PESSOA ou execução, em qualquer estado (`ultimoDePessoa`).
  - Os pedidos automáticos (a sonda de rede `rede`, a escada `system`, `saude`, `reconciliacao` e o rodízio `scheduler`)
    não são "o último comando". A sonda pede um a cada poucos minutos e enterrava o que a pessoa quer ver; ela fica na
    tela de Rede. Cartão sem comando de pessoa fica sem a linha, como antes. (Ajuste da orquestradora.)
  - O concluído também aparece; antes ele era escondido de propósito, e a orquestradora pediu o contrário.
  - Os `uncertain` sem desfecho de pessoa ou execução seguem numa linha discreta: "Anterior sem resposta: <verbo> · há N
    dias", ou "Sem resposta: <verbo>" quando são mais novos que o principal. Ficam até serem verificados ou decididos
    (`comandoSemDesfecho`). O de pedido automático (a sonda incerta) também fica só na tela de Rede (ajuste da
    orquestradora).
  - O Foco lê do mesmo jeito (`useComandosDoAparelho`). A Lista divide a coluna com a etapa em curso: comando aberto ou
    `uncertain` atual, depois a etapa, depois o anterior sem resposta.
  - Custo: 1 leitura curta por aparelho, mais 1 funda nos que têm sonda, a cada hidratação (carga e ressincronização).
    No central: 15 curtas e 2 fundas.
- Infraestrutura, renderizador: "renderizador pedido: GPU do host" virou "renderizador configurado: GPU do host". A
  dica separa o configurado (o pedido ao emulador, `gpu_mode`) do efetivo (o que o emulador selecionou, só com o
  aparelho no ar). No fallback, "(configurado: host)".
- Infraestrutura, tipo do aparelho: o `kind` cru "store" virou "loja", e "external" virou "externo", com o porquê na dica.
- Infraestrutura, pausa do reparo (25.13 sem tela): linha discreta "reparo pausado até hh:mm" lida de
  `/api/instances[].repair_pause`. Em outro dia, leva a data. Quem pausou e o motivo vão na dica. A vencida não aparece.
- Aplicativos › Rede: a lista por app diz o nome do registro de aplicativos ("Outlook: sem tráfego na janela") e
  "shell do Android (a sonda)", com o pacote na dica. Fora do registro, sai o pacote.
- Prova:
  - `simulated`: `npm run typecheck` e `npm test` com 1398 de 1398. Testes novos em `store/reducer.test.ts`,
    `devices/DeviceCard.test.tsx` (com a Lista), `focus/FocusPanel.test.tsx`, `infra/infraState.test.ts`,
    `infra/InfraPage.test.tsx` e `rede/RedePage.test.tsx`.
  - Navegador (03/10, Vite do worktree na 5173 contra a API do central, só leitura):
    - Painel: os 15 cartões com o último comando de pessoa (o 03 e o 06 com "Verificação da sessão", não a sonda); 10
      com "Anterior sem resposta"; nenhum "Rede do aparelho" na página; 15 leituras curtas e 2 fundas, sem repetir;
    - Infraestrutura: "loja" com a dica, 8 "renderizador configurado", nenhum "store" nem "renderizador pedido";
    - Rede: Instagram, Outlook e shell do Android pelo nome;
    - Foco do android-06: "Concluído · Rede do aparelho" no topo e "Anterior sem resposta: Abrir app · há 8 dias";
    - Lista: 11 linhas com o anterior, nenhum `uncertain` antigo como atual;
    - 375 px: `scrollWidth` 375, nenhuma linha estourando.
  - A linha da pausa do reparo NÃO foi vista no navegador: não havia pausa em vigor no central. Ela está provada no
    teste de componente.

## 2026-10-03 — 29.50: a pergunta sem resposta expira pelo sistema em 24 h (branch feat/29-50-expira-needs-input)

- O problema: 13 execuções de QA estavam em `needs_input` no android-05 e no android-09 desde 02/10 18:15Z e 03/10
  07:37Z e 07:51Z. Não havia prazo. Só o cancelamento pela rota as tirava do ar, e ele grava o sinal de pessoa
  `cancelou_execucao` (ADR-054), que ninguém deu.
- Agora `RunService.expirar_sem_resposta`:
  - fecha como `cancelled` a execução em `needs_input` há 24 h, contadas da entrada na pergunta (o `run.updated`
    daquela transição) e não da criação;
  - grava o motivo humano no `status_detail` ("Sem resposta em 24 h: …"), sem código nem id;
  - leva `expirada: {motivo, horas, desde}` num campo próprio do `run.updated`;
  - não grava `cancelou_execucao`.
- O laço `AppState._expiracao_loop` faz a primeira volta na subida e depois uma a cada 10 min, só no líder da trava
  `retencao`.
- A resposta que chega no meio da varredura ganha: o cancelamento é condicional ao `needs_input`.
- `RunService.cancel` passou a usar o mesmo `_cancelar_antes_de_iniciar`, sem mudar o comportamento.
- Contrato: adendo v0.95 do `docs/api-contract.md` (número da orquestradora).
- Prova:
  - `simulated`: `backend/tests/test_needs_input_expira.py`, 4 testes (prazo, relógio da entrada, corrida com a
    resposta, volta do laço);
  - os afetados passaram: 80 testes (costuras, assistente, máquinas de estado, travas, retenção, arquitetura) e 187
    dos 11 arquivos que cancelam;
  - real: `not_run`. É a varredura no central depois do deploy, que deve liberar as 13.

## 2026-10-03 — K-084: os 7 testes com SQL só de SQLite passam na PostgreSQL (branch fix/k-084-sql-so-de-sqlite)

- `tests/test_leitura_visual_papel.py::test_078_as_linhas_ficam_arvore_e_o_check_recusa_outra_origem`: a versão
  comparada como texto (`version LIKE '078%'`). No SQLite, `version=78` nunca casava, e a asserção do sha256 da 078
  passava vazia; agora ela exige a linha.
- `tests/test_learning_esquecer_conta.py::test_nao_toca_receitas_fluxos_nem_memoria`: `flows` com `id` explícito
  (TEXT PRIMARY KEY sem padrão: o SQLite aceitava o id nulo).
- `tests/test_rede_aplicacao.py::_objetivo_parado`: `ON CONFLICT DO NOTHING` no lugar do `INSERT OR IGNORE`. Afeta os 4
  parâmetros de `test_reinicio_com_objetivo_esperando_a_rede` e `test_reinicio_que_nao_sai_diz_qual_termo_segurou`.
- Prova `simulated`: os 7 casos passam na PostgreSQL (farm-pg, 03/10). Os 3 arquivos deram 95 passed na PG e 95 no SQLite.
  Só testes mudaram; nada no app.
## 2026-10-03 — Jev: polimentos de UX do deploy 10 (branch feat/ux-jev-polimento, para a suíte 12)

- O aviso do Jev em `/api/ai` (`transparencia.aviso`) fala o modo em palavras: "decisões por conjunto fechado (curador
  em sombra, intenção em sombra)", e não mais "dos consumidores curador (shadow)". O bloco `decisao_fechada.consumers`
  continua com os valores do YAML (adendo v0.90).
- Configuração › IA ganha a linha "Decisão fechada (Jev)" (curador: em sombra · intenção: em sombra), com o selo
  "envio ativo" ou "nada sai agora" vindo do `sending`. O frontend passa a tipar o bloco v0.90 (`AiDecisaoFechada`).
- Diagnóstico › Detalhes técnicos: as chaves cruas (`usable`, `raw`, `guidance`, `hypervisor_present`, núcleos, memória
  virtual e discos) ganham rótulo em português.
- Prova `simulated`:
  - backend: `tests/test_decisao_fechada_sombra.py` e os arquivos do `/api/ai`, 149 passed;
  - frontend: typecheck, mais `aiLabels`, `AiSection` e `DiagnosticsPage`, com 37 passed;
  - navegador: as duas telas percorridas contra um backend simulado do worktree (porta 8765, decisor nulo, nada sai),
    no desktop e em 375 px, sem rolagem lateral.
## 2026-10-03 — 29.49 com prova real: o assunto do Outlook lido de primeira no android-01

- Execução real (03/10, central, android-01, deploy 11 c8304e85, liberada pela orquestradora): r-20261003155342-6a94e6.
  É o mesmo pedido só-leitura da run 89b814, sobre o mesmo e-mail cuja prévia repete o assunto.
- Resultado:
  - completed em 34 s; a etapa passou em 1 tentativa;
  - o `read_value` do assunto foi aceito pela leitura visual, sem `truncado` nem `repetida`;
  - nenhuma escalada ao modelo forte, nenhum plano revisado.
- 10 chamadas, ~US$ 0,107. A 89b814 fez 13 chamadas (2 no modelo forte), ~US$ 0,213, e foi cancelada no teto.
- A mesma execução gerou a 1ª linha da sombra de intenção do deploy 11 (`decisao_fechada_sombra`, origem `intencao`,
  classe C3, modo shadow, confiança 0,99, sem fallback).

## 2026-10-03 — Deploy 11 no central (c8304e85; decisão fechada em sombra na intenção; autopublicação em sombra; 25.13 real)

- Implantado no central (`real`, 03/10, WIN-7S2UASNLFOP): commit c8304e85 = suíte 11 (97425d5f) + os estados da Android.
  Sem migração nova; health ok, `migration 082_learning_validations`, `problems []`.
- Antes do restart:
  - pausa do reparo no 01/03/06, já pelo código novo; nenhuma execução nem comando em voo;
  - backup do banco `data/backups/20261003-122915`;
  - checkout em fast-forward.
- Restart às ~15:30Z (`deploy.ps1 -PularBackup`, rc 0). O relógio do curador recomeça do restart.
- `config/config.yaml` (fora do Git; backup `config-antes-deploy11-20261003-152926.yaml`, sha f82c6df4 → e98f5626),
  conferido antes com o `load_config()` do código novo:
  - `ai.decisao_fechada`: `consumidores {curador: shadow, intencao: shadow}`, `classes_permitidas [C0, C1, C3]`,
    `retencao_dias 180`, decisor jev;
  - `aprendizado.autopublicacao.modo: shadow` (30.34: só marca no livro da sombra, nada publica);
  - o curador com alfa de volta ao padrão 0.10 (sai o 0.3 temporário do deploy 10).
- `GET /api/ai`: decisão fechada com `consumers {curador: shadow, intencao: shadow}`, classes C0/C1/C3, decisor jev,
  `sending true`.
- **25.13 com prova `real`:** a pausa do reparo posta às 15:29:07Z (01/03/06, até 15:59:08Z) estava em
  `settings.repair_pauses` antes do restart e voltou depois dele com os MESMOS três prazos, sem renovar. Conferido na
  API e no banco, lido em modo só leitura.
- Agente do notebook atualizado para `0.1.0+c8304e8`, com pausa nos 09/10/12/13 durante; os dois workers com
  `agent_outdated false`. Os 7 aparelhos ligados ficaram online e prontos.

## 2026-10-03 — Suíte 11 na main (97425d5f): SQLite inteira; PostgreSQL e K-084 pendentes

- A suíte 11 entrou na main por commit-tree (97425d5f), com 6 hashes:
  - Jev: 31.18 forma A + 31.20 (ea1df281) e 31.19 (442a9249);
  - Aprendizado: #156 30.33-B (c9f1c18a), #154 30.34-A (147ad07f) e #155 30.35 (32d8ef6b);
  - Android: 29.49 (0b62678b).
  - O único conflito de código foi o import de `learning/infrastructure/montagem.py`, com os dois mantidos.
- SQLite (`real`, 03/10, WIN-7S2UASNLFOP, `-n 8`, Idle, @35e3b0f6, das 15:19 às 15:28Z): 8441 passed, 0 failed.
  Scripts deram 474 passed, docs-check 0/0, typecheck ok e frontend 1374 passed.
- PostgreSQL: não rodou nesta suíte, porque não há migração nova (decisão da orquestradora). Os 7 testes do K-084 (SQL só de
  SQLite em `test_leitura_visual_papel`, `test_learning_esquecer_conta` e `test_rede_aplicacao`) seguem PENDENTES.

## 2026-10-03 — Jev: o limiar do curador analisado, o rótulo 1 só do dono e o percentil unificado (31.19; branch feat/31-19-curador-limiar)

- **O limiar fica em 0,85; nada liga.** As 4 respostas reais de 03/10 (13:10Z) voltaram `abaixo_do_limiar`, com
  confiança 0,50–0,52 e maior probabilidade 0,60–0,62, todas sobre o mesmo estado C0. O limiar não separa o que a
  entrada não distingue. O registro datado está em `docs/design/jev-golden-set.md` §2.
- **A porta segue o contrato** (decisão da orquestradora): no `choice`, o limiar vale sobre a probabilidade devolvida
  da opção escolhida, que precisa ser a maior (`porta._valor_do_limiar`). Antes, ele valia sobre a `confianca`. A
  escolha sem probabilidade, ou que não é a maior, falha fechado. A `confianca` segue gravada na sombra. Nada muda nas 4
  linhas reais (0,60–0,62 < 0,85). Prova `simulated`: `tests/test_decisao_fechada*.py`, 1044 passed.
- `scripts/jev-relatorio-31-10.py`:
  - o rótulo 1 do curador só vale com `decided_by` entre os `--autor-dono` (sem nome, fica desligado; `panel` e sessões
    Claude não rotulam). O dono é `Flavio` (`panel_sessions`, confirmado pela orquestradora); um agente de validação
    no Chrome do dono também aparece assim, mas só lê;
  - novos campos: `rotulo_1` (autores e transições de pessoa fora do dono), `estados_distintos` e `cobertura_por_limiar`
    (contrafactual por `confianca` e por maior probabilidade).
- Percentil unificado pelo posto mais próximo em aritmética inteira, o método do `sombra._p95`, no relatório e na
  `scripts/jev-prova-31-17.py`. Com as 4 chamadas de 03/10, p50 437,3 ms e p95 552,8 ms nos dois (antes, 473,8 e 510,2
  para o mesmo p50).
- Prova `simulated`: `scripts/tests/test_jev_relatorio_31_10.py` e `test_jev_prova_31_17.py`. Fora a porta, nenhum
  código de runtime mudou.
## 2026-10-03 — 29.49: o laço do assunto do Outlook (o assunto repetido na prévia cortada; a releitura recusada encerra)

- `taskqueue/saidas.py::conferir_transcricao`: quando o valor aparece em mais de uma linha transcrita (a prévia que repete o
  assunto e termina em "…"), uma cópia numa linha INTEIRA prova que ele não foi cortado. As cópias cortadas contam como
  linha alheia para a marca global do leitor. O valor do ator e o campo do leitor cortados continuam recusando. Fica
  registrado como precisão da emenda do ADR-070 §4 e como recaída do K-079.
- A releitura recusada:
  - `ler_valor_visual` guarda a recusa da conferência (`ilegivel`/`truncado`/`nao_confere`) por par (tela, âncora);
  - a releitura do par sai `repetida` com `anterior` e vira definitiva: a etapa termina como não lida, sem nova tentativa
    e sem recuperação automática (`StepOutcome.sem_recuperacao`, que o scheduler respeita sem reter os outros aparelhos);
  - o ator recebe, junto do código, a frase do executor de que reler o mesmo elemento nesta tela não será aceito.
- Prova:
  - `simulated`: `tests/test_leitura_visual.py::test_10_29_49_a_previa_que_repete_o_assunto_nao_corta_o_assunto_inteiro`,
    `::test_7_29_49_repetida_depois_de_recusa_deterministica_e_definitiva` e
    `::test_29_49_repetir_a_leitura_recusada_encerra_a_etapa_sem_nova_tentativa`; 857 passed nos 33 arquivos afetados;
  - bancada do 12.5 refeita offline sobre as transcrições guardadas: 31→32/32 e 29→30/32, 0/96 falsas;
  - `real` no 01: `not_run` (só com a liberação da orquestradora).
## 2026-10-03 — Aprendizado: o aviso `learning.needs_person` da receita e do fluxo com a classe do dossiê (30.33-B; branch feat/30-33-aviso-do-fluxo)

- A faixa do aviso de uma fonte nativa passa a ser a classe do dossiê do curador:
  - na receita, pela capability derivada;
  - no fluxo, pela etapa mais restritiva (30.32).
- O leitor é comum aos dois (`RiscoDoConteudo`, tirado de `DossiesSql`).
- Comentar, responder, mandar mensagem e seguir avisam C (`alto_risco`) em vez de B (`efeito_externo`).
- O payload não muda.
- Prova `simulated`: `test_learning_classe_do_fluxo.py`, com 5 casos novos:
  - o fluxo pelo serviço;
  - o fluxo sem o leitor;
  - a montagem com o catálogo do repositório;
  - a receita com e sem o leitor.
## 2026-10-03 — 31.20: as lacunas da rodada I e a A-média aprovada pelo dono (branch feat/31-18-forma-a)

- A rodada I deu NO-GO no b7c05558, e o 31.20 é a ordem da orquestradora (ADR-069 item 19).
- Parte de lista (f2f49a08, aceita). Fecha:
  - a locução e os verbos de credencial ("inicie a sessão com", "identifique-se");
  - o valor depois do destino cortado e a vírgula no par;
  - o conector com hífen e "amb";
  - o futuro e o "já tinha entrado";
  - os eufemismos novos;
  - a conta do catálogo como identidade ("use o lucas com x", "como lucas, x", "sendo o lucas, x");
  - o domínio de topo solto depois do e-mail, o e-mail em peças e o fragmento de provedor ao lado de `[email]`;
  - o C2: nome de fluxo com C7 vai como "(sem nome)";
  - "entre" preposição deixa de recusar.
- A-média, aprovada pelo dono em 03/10 ~14:15Z: o verbo de entrar recusa sozinho, em qualquer forma, tempo e posição.
  A exceção única é o objeto pessoa ou conversa, onde os outros gatilhos seguem valendo. "Entre os/as" no começo da
  oração, sem conector perto, é preposição. Residual: sem gatilho e "entre na conversa/chat com".
- Custo:
  - 126 comandos reais: 14 recusas (11,1 %), 6 a mais que a parte de lista; as que passam têm a mesma saída do b7c05558;
  - HM3: 31 → 48 recusas, todas as novas por verbo de entrar;
  - C2: 4 dos 20 fluxos ativos vão sem nome;
  - 27 controles de teste viraram recusa (`CUSTO_DA_A_MEDIA`).
- Prova `simulated`:
  - `backend/tests/test_decisao_fechada_reverificacao_i.py`;
  - harness de 785 casos (corpus de 14:29Z): 0 vazamentos nos dois catálogos e só as 4 recusas indevidas conhecidas;
  - filtro e sombras: 1889 passaram.

## 2026-10-03 — 31.18: forma A (A-ESTREITA), a C3 fecha por gatilho de credencial (branch feat/31-18-forma-a)

- Decisão do dono depois do NO-GO da fase 2 da H (ADR-069 item 18): qualquer gatilho de credencial no comando sem
  destinos E no original recusa o pedido inteiro da C3 (`c7_gatilho`, `intencao._gatilho_de_credencial`).
  - Gatilhos: verbo de entrar, também no passado, com conector até 3 tokens depois; campo forte; verbo de digitar;
    palavra C7; soletrado; e o par campo + separador + valor nas quatro formas.
  - Leitura literal: a sintaxe de destino conta, e "entre com a conta Lucas e curta" recusa.
  - Exceção única: objeto pessoa ou conversa.
  - Residual aceito: senha sem gatilho e "entre na conversa com <senha>".
- Custo: 8 de 126 comandos reais (1 `c7_palavra` e 7 `c7_gatilho`, todos com "conta"). Os 30 controles antigos foram
  para `CUSTO_DA_FORMA_A` como recusa, e a orquestradora reetiquetou 9 casos do corpus.
- Prova `simulated`: `backend/tests/test_decisao_fechada_forma_a.py` (445). Recusam as 86 da H e as 40 + 37 das
  regressões G e F, nos dois catálogos; os 30 controles operacionais passam.
  - Filtro e sombras: 1458 passaram.
  - Harness de 579: 0 vazamentos, 0 passagens indevidas, as 6 recusas indevidas de antes.

## 2026-10-03 — Deploy 10 no central (2432046f; migração 082; curador com alfa 0,3 temporário)

- Implantado no central (`real`, 03/10, WIN-7S2UASNLFOP): commit 2432046f = suíte 10 (16fd1127) + os estados da Android.
  Health ok, `migration 082_learning_validations`, `problems []`.
- Antes do restart:
  - pausa do reparo no 01/03/06; nenhuma execução nem comando em voo;
  - backup do banco `data/backups/20261003-115511`. A 082 foi ensaiada numa cópia desse backup com o código da main:
    aplicou só a 082, a segunda `migrate()` não aplicou nada e a integridade ficou ok;
  - checkout em fast-forward.
- Restart às ~14:56Z (`deploy.ps1 -PularBackup`, rc 0), com o painel reconstruído (a aba Métricas do 30.33 no bundle).
  O relógio do curador recomeça do restart.
- `config/config.yaml` (fora do Git; backup `config-antes-deploy10-20261003-145522.yaml`, sha 09cbaf5d → f82c6df4):
  - só o curador com **`alfa: 0.3` TEMPORÁRIO** (k no padrão 1.5), para a fila do curador esvaziar (B_W a 92 %);
  - **volta a 0.10 no deploy 11**;
  - o `aprendizado.validacao` do 30.31 fica ausente (= off);
  - conferido antes, com o `load_config()` do código novo.
- `GET /api/ai`:
  - configured, `anthropic`/`claude-sonnet-5`, não simulado, leitura visual ligada;
  - decisão fechada em sombra no curador (`consumers {curador: shadow}`, classes C0/C1, decisor jev, `sending true`).
- Agente do notebook atualizado para `0.1.0+2432046` (o `config.py` está no manifesto), com pausa do reparo nos
  09/10/12/13 durante; os dois workers com `agent_outdated false`. Os 7 aparelhos ligados ficaram online e prontos.
- 25.13:
  - a pausa posta ANTES deste restart veio do backend velho, sem persistência, e não voltou: era o esperado, e este
    restart não prova o item;
  - a pausa renovada depois já está gravada em `settings.repair_pauses` (lida no banco do central);
  - a prova de que ela sobrevive ao restart fica para o próximo restart.

## 2026-10-03 — Suíte 10 na main (16fd1127): SQLite e PostgreSQL inteiras

- A suíte 10 entrou na main por commit-tree (16fd1127), com 12 hashes:
  - Android: 25.13, I2, o polimento do 29.44 e o teste do líder que cai;
  - Jev: 31.9 rodada H, 31.16, 31.17 e I1;
  - Aprendizado: #148 a #153 e a migração 082;
  - mais duas correções de teste: c2719067 (`test_projecao`) e df0bf82d (`tier` do 30.31).
- SQLite (`real`, 03/10, WIN-7S2UASNLFOP, `-n 8`, Idle, @008b9df2): 7513 passed, 1 failed. A falha era uma bomba-relógio
  da main (o AGORA fixo da semente contra o `datetime.now` do corte da projeção, desde 03/10 12:00Z), corrigida em
  c2719067; os afetados deram 30 passed. Scripts, docs-check, typecheck e frontend verdes (1374 testes).
- PostgreSQL (`real`, 03/10, @618ba43a, `-n 8`, Idle, das 12:59:53 às 14:49:42Z): 7494 passed, 8 failed, 19 skipped.
  - 7 falhas pré-existentes na main, provadas contra a `origin/main` 944eb949 = K-084, para a suíte 11. Não bloqueiam:
    o central roda SQLite.
  - 1 do 30.31 (`tier="t"`), corrigida em df0bf82d: o teste direcionado deu 13 passed na PG e no SQLite.
  - A corrida em série foi trocada por `-n 8`: ~2,7 s por teste em série, ~5,5 h projetadas.

## 2026-10-03 — 29.48 no notebook: os emuladores do worker com janela (fim do giro de ~4 núcleos)

- `C:\farm\worker.yaml` (fora do Git; backup `worker.yaml.antes-2948-20261003-131114`): `android.window: true`. O `farm-agente`
  foi reiniciado às 13:11:30Z, com pausa do reparo nos 4 aparelhos.
- Prova `real`:
  - antes, os 4 qemu do notebook eram `-headless`, cada um com uma thread a ~100 %;
  - o android-13, num boot natural, subiu como `qemu-system-x86_64`: 8,4 % total um minuto depois, e funcional;
  - os QA 09, 10 e 12 foram reiniciados um a um: os 4 ficaram com no máximo 14 % cada, e o processador em 2 %.
- O 29.48 fecha (implemented/real). No central, o 01, o 03 e o 06 seguem no boot natural (conta real).

## 2026-10-03 — Aprendizado: a aba Métricas no painel (30.33; branch feat/30-33-metricas-no-painel)

- Aba nova `#/aprendizado?aba=metricas`, que lê as rotas do 30.8 sem mudar a API.
  - Em cima, um cartão por bloco do §10, na janela de 7, 14 ou 30 dias e por app.
  - Embaixo, os pareceres do curador, com filtro pela sugestão, páginas pelo cursor e o link de cada item.
- Regras do contrato na tela:
  - ausente é "sem amostra", nunca 0%;
  - o proxy de falhas se diz comparação, não prova;
  - o orçamento é global e usa a janela do curador, com o aviso a 80%;
  - aparece o aviso da quebra de série do deploy 8;
  - o bloco vazio diz o porquê numa frase;
  - o 503 `not_ready` tem tela própria.
- Prova:
  - `simulated`: `MetricasTab.test.tsx`, 12 testes;
  - autovalidação no navegador sobre uma CÓPIA do backup do central (12:08Z), com backend simulado do worktree, e
    sobre um banco vazio: desktop e celular, paginação, filtros, link do item, vazio e erro;
  - o passe no Chrome do dono fica para depois do deploy.
## 2026-10-03 — 29.48 passo 1: o emulador do central com janela (o giro do `-headless` na sessão 0 some)

- `config/config.yaml` do central (fora do Git; backup `config-antes-2948-20261003-120854.yaml`): `android.window:
  true`, sem `-qt-hide-window`. Backend reiniciado às 12:10:52Z, health ok; pausa do reparo do 01/03/06 renovada antes e
  depois do restart (K-082).
- Prova `real`, aparelho temporário `android-21`:
  - o snapshot salvo pelo headless carrega no binário com janela;
  - no wake e no boot a frio, nenhuma thread do qemu passa de 50 % (antes, uma a ~99,5 %);
  - funcional.
- O passo 2 (`-qt-hide-window`) foi cancelado: não foi preciso, e os snapshots dos hibernados seguem valendo.
- `config/config.example.yaml` passa a trazer `window: true`, com o porquê. Docs em `parque.md` (Emulador com janela
  na sessão 0) e K-078.
- Pendente: o notebook (`C:\farm\worker.yaml`), depois de 1 h de central estável.
## 2026-10-03 — I1: o cartão da TypeSafe diz que a decisão fechada a usa e mostra o consumo do Jev (branch feat/i1-cartao-typesafe)

- Antes, o cartão da TypeSafe em Configuração › IA dizia "Nenhuma função usa esta conta · US$ 0,00", mesmo com a sombra do
  curador ligada no deploy 9. A causa: `roles` só conhece as funções do hub.
- Mudanças:
  - `GET /api/ai/balances`: a conta ganha `closed_decision` (`shadow` | `on` | null), e `in_use` passa a contar a decisão
    fechada;
  - o "Usada por" mostra "decisão fechada (sombra)", e o chip da TypeSafe aparece em Custos;
  - o consumo abaixo de um centavo mostra até 4 casas ("US$ 0,003");
  - o tipo `AiBalanceAccount` ganha `typesafe`, e o nome curto é "TypeSafe".
- A decisão fechada fica fora de `roles`: o saldo do Jev não adia o despacho dos pedidos.
- Prova `simulated`:
  - `tests/test_decisao_fechada_sombra.py::test_a_typesafe_diz_que_a_decisao_fechada_a_usa_e_mostra_o_consumo_do_jev`;
  - `frontend/src/lib/aiBalance.test.ts`;
  - navegador contra o backend simulado do worktree (decisor jev, curador em sombra, sem chave e sem classes), em desktop,
    tablet e 375 px.
- Real: `not_run`. No central há 0 chamadas jev até 12:11Z: a volta do curador só revisou fluxos.
## 2026-10-03 — Receitas: o contêiner sem identidade pelo filho rotulado (29.40 item 2; branch feat/29-40-filho-rotulado)

- O toque numa linha clicável sem `resource-id` nem texto (a conversa, o contato) vira receita pelo filho rotulado não
  clicável. O filho é gravado com hit-test e janela da pré-ordem; na reprodução, o toque vai ao centro do filho,
  depois de conferir o clicável que o contém.
- Rótulo que muda com o estado e qualquer @ literal não viram seletor. O efeito externo pelo filho é recusado.
- A mescla com a chave genérica (RA-20 B) trouxe um defeito: o `_contem` de bounds do 29.40 sobrescrevia o `_contem`
  de texto do `eh_generica`. O teste pedido pela Android o pegou, e o de bounds virou `_dentro_de`.
- Prova `simulated` (`tests/test_receita_filho_rotulado.py`); afetados (receitas, executor, chave genérica): 849 passed.
## 2026-10-03 — Aprendizado: o custo da revisão pela chamada ligada (I3; branch fix/aprendizado-usd-por-parecer)

- A revisão gravada com `usd = 0` e com `ai_call_id` (as 46 de antes do 30.30 no central) passa a ter o custo MEDIDO na
  chamada ligada, pela regra do `/api/usage`. Vale na janela do orçamento, nas métricas e na lista `/revisoes`.
- Na cópia do banco do central: US$ 0,6717, o mesmo do `/api/usage` do curador. Antes, `metricas.curador.usd` dava 0,0
  e o orçamento estimava 0,6418.
- Sem backfill e sem migração. Prova `simulated` em `test_learning_curador.py`; o recálculo na cópia do banco é a
  prova `real` da leitura.
## 2026-10-03 — Aprendizado: a classe do fluxo pela etapa mais restritiva (30.32; branch feat/30-32-classe-do-fluxo)

- O dossiê do curador classifica o fluxo pelas etapas dele, cada uma com os fatos do catálogo do app dela
  (`EtapaDeRisco`). As razões são a união, então a classe nunca desce.
- Antes, todo fluxo com efeito caía em `commit_sem_fatos_da_etapa` (B). Na cópia do banco do central (03/10), 9 fluxos
  do Instagram passam a C: comentar, responder, mandar mensagem e seguir, todos com etapa `risk: high`. Os outros 24
  não mudam.
- O hash do dossiê só muda para o fluxo com etapa no catálogo; o curador os revê.
- Divergência conhecida: o aviso `learning.needs_person` da transição nativa segue sem as etapas.
- Prova `simulated` (`tests/test_learning_classe_do_fluxo.py`) e o recálculo dos 33 fluxos da cópia do banco.
## 2026-10-03 — Aprendizado: validação automática do "pedir evidência" (30.31; branch feat/30-31-validacao)

- Migração `082_learning_validations`: o pedido de validação, com um pedido vivo por item (índice parcial).
- O parecer real do curador que pede evidência que uma execução produz vira pedido (`domain/validacao.py`); as
  recusas ficam registradas com o motivo (tipo, desligado, vetado, sessão, sem origem, credencial, efeito real,
  receita sem fluxo ativo).
- Um despachante sob a trava de líder roda o comando de origem noutro aparelho ocioso. Só com o central saudável, sem
  execução em curso, dentro de β = 5% do gasto de IA da operação na janela (mais a verba única `extra_usd` até
  `extra_ate`) e de ≤4 por hora. Nesta fatia nenhum grupo vai a aparelho com conta real logada (nem a leitura).
- O digest fecha o pedido: `feita` com evidência da execução, senão `recusada`, com o `usd` medido. O curador revê o
  item com o gatilho novo `evidencia_chegou`, que pula o cooldown (rótulo no painel).
- `aprendizado.validacao.modo: "off"` de fábrica. Prova `simulated` (`test_learning_validacao*.py` e
  `test_learning_curador.py`); nada ligado no central.
- Fora desta fatia (avisado à orquestradora): a conferência pelo ContentProvider do QA, o ensaio só leitura do
  Instagram em perfil de terceiro e a classe do fluxo pela etapa mais restritiva (30.32).

## 2026-10-03 — Aprendizado: "devolver à prova" (30.31, item 0; branch feat/30-31-validacao)

- `ciclo.TRANSICOES` ganha `disabled → candidate`, só de pessoa e só para fluxo (adendo v0.91):
  - no livro, a chave `devolver` em `acoes`; no painel, o botão "Devolver à prova";
  - receita, lição e tela recusam com `transition_forbidden`.
- Na concordância do parecer, "devolver" vale como esperar (`parecer._DIRECAO_DA_ACAO`).
- A sombra do fluxo conta só a evidência a partir da volta (`SombraDosFluxos._avaliar`).
- Motivo: os 5 fluxos desligados em 03/10 "para validar pela IA" nunca recebiam evidência. É o pré-requisito do
  laço de validação automática (30.31, desenho aprovado pela orquestradora).
- Prova `simulated`:
  - `tests/test_d1_fluxos.py::test_devolver_a_prova_tira_o_veto_e_a_prova_recomeca_da_volta`;
  - a paridade e o roteiro em `tests/test_learning_acoes.py`; a regra do dono em `tests/test_learning_ciclo.py`;
  - 585 testes afetados passaram; vitest de `aprendizado` 131; typecheck limpo.
- Real: `not_run`.
## 2026-10-03 — Aprendizado: polimentos de texto dos deploys 7 e 8 (branch feat/aprendizado-ux-deploy8; suíte 10)

Os itens de polimento da frente do Aprendizado em `.claude/handoffs/ux-deploy7-2026-10-03.md` e
`ux-deploy8-2026-10-03.md` que não entraram no #143. Só painel e texto; nenhuma rota nem migração.

- **Veto** (`domain/ciclo.py::motivo_do_veto`):
  - a frase traz a data em dd/mm/aaaa e o motivo de quem desligou:
    "desligado por uma pessoa (orquestradora) em 03/10/2026 (validar pela IA antes de valer): só uma pessoa o reativa";
  - o motivo vai numa linha só e é cortado em 120 caracteres;
  - as frases do sistema e da evidência inválida também usam a data em dd/mm/aaaa.
- **Configuração › Fluxos e receitas:** o selo do app cai no `app_id` quando o fluxo chega com `required_apps` vazio.
  Eram 5 fluxos do QA no central, 2 deles desligados (`lib/appsDoFluxo.ts::appsDoFluxo`).
- **Cartão do Livro:**
  - a referência ("receita:108") saiu do canto e foi para o `title` do título;
  - a receita de app sem catálogo se chama pela etapa ("Digitar a mensagem (v1)"), sem a chave crua.
- **Detalhe e "Para aprovar":**
  - o alvo da ação vem em palavras ("Alvo: “Enviar”"), com os seletores recolhidos em "como o encontra";
  - a execução de origem aparece como "execução de 24/09 08:48", com o id no `title`;
  - a variante da tela aparece como "idioma en-US, tela xhdpi".
- **O que falha:**
  - a ocorrência mostra "android-05 · tentativa 1", com a chave da etapa no `title`;
  - a tela mostra "Tela: caixa de entrada", com o id do catálogo no `title`.
- Prova `simulated`:
  - `tests/test_learning_ciclo.py::test_a_frase_do_veto_fala_a_data_do_painel_e_o_motivo_de_quem_desligou`;
  - vitest de `aprendizado`, `settings` e `lib/appsDoFluxo` (236 passaram);
  - typecheck limpo.
- Navegador: `not_run` (fica para a validação da orquestradora no deploy que levar a suíte 10).
- Pendente com a Android: o motivo do desligamento no `title` do interruptor de Configuração › Fluxos e receitas
  pede que `/api/flows` traga o motivo (backend de skills).
## 2026-10-03 — Prova real do 31.17: o script que confere a sombra C0–C1 do curador no central (branch feat/31-17-prova-real)

- `scripts/jev-prova-31-17.py`, só leitura (`mode=ro` mais `query_only`), sem IA, desde o T_on do deploy 9:
  - as linhas da sombra só do curador, em `shadow`, nas classes C0/C1, com o que guardam em formato opaco;
  - o corpo de cada chamada remontado do dossiê guardado, quando o `content_hash` bate com o `ref`, sem texto, id nem
    hash do dossiê;
  - US$, tokens e latência das linhas do Jev em `ai_calls`, cruzadas com a sombra.
- `docs/design/jev-golden-set.md` §6.
- Prova `simulated`: `scripts/tests/test_jev_prova_31_17.py` (8 testes). No central: `not_run` até a ordem da orquestradora.
## 2026-10-03 — 31.16: telas do RA-10 no Custo de IA e o adendo v0.90 (branch feat/31-16-telas-ra10)

- Diagnóstico › Custo de IA, sem API nova (lê o adendo v0.75):
  - a seção "Modelo forte e conferência": `escalations` por motivo, o rejulgamento com a discordância e a cascata do bloqueio;
  - recolhidos "Discordância do rejulgamento, por app" (nome do app do catálogo) e "Imagem: por que foi junto (ou não)";
  - o aviso das etapas sem `driven_by`, só quando passa de 0;
  - nada aparece com servidor anterior ao v0.75.
- `docs/api-contract.md`, adendo v0.90: o bloco `decisao_fechada` de `GET /api/ai`, com `decider` (31.14) e `sending`
  (31.17), documentado a posteriori; o `notice` "Envio ATIVO" ou "Nada sai agora: <motivo>".
- Prova `simulated`: `frontend/src/features/usage/usage.test.ts` e `frontend/src/features/diagnostics/DiagnosticsPage.test.tsx`;
  a tela percorrida no navegador contra um backend simulado do worktree (nada no central).
## 2026-10-03 — 31.9: a H sem a H-3, depois do NO-GO da fase 2 da H (branch fix/31-9-rodada-h)

- A fase 2 da H deu NO-GO: 107 casos em 4 famílias de método.
  - A família 1, que não reproduz na base, era a H-3, e ela foi revertida: "entre com o lucas" volta a recusar.
  - As famílias 2 a 4 vêm da G, não se remendam e esperam a escolha do dono: A, fechar por gatilho; ou B, só o curador
    C0–C1.
- Piso:
  - (a) o C7 com dígito recusa, em vez de mascarar (`c7_valor_com_digito`);
  - (b) a quebra de linha separa o par, como o ";";
  - (c) os objetos e as telas do app de e-mail e agenda não são donos de endereço ("abra o calendário do outlook" passa).
- ADR-069, item 17; bloco "Depois do NO-GO da fase 2 da H" em `docs/ia.md`.
- Prova `simulated` no 136f80ff:
  - harness, corpus de 579: 0 vazamentos e 0 passagens indevidas, também com a "Girassol";
  - 6 recusas indevidas: as 4 antigas mais o 572 e o 573, que são o custo da reversão;
  - testes do filtro: 999 passaram.
- Medição nos 125 comandos reais de 7 dias, só leitura:
  - recusam 3, contra 1 na base com os nomes dos apps registrados como na subida. O custo da (a) é de 2 comandos, e
    os dois têm só números com cara de ano;
  - correção: as medições das rodadas F a H não registravam os nomes, e a base recusava "2" em vez de 1;
  - refinamento dos anos (orquestradora): o token só de dígitos conta só perto do campo ou logo depois do conector do
    verbo de entrar. Os 125 voltam a 1 recusa, como na base, e o harness não muda;
  - opção A-ESTREITA: 35 dos 124 que passam, sendo 28 "entre na conversa com …"; com a exceção de pessoa ou conversa, 7;
  - 39 dos 122 que passam têm gatilho forte: é o custo da opção A.

## 2026-10-03 — Correção do 31.9, rodada H (47 vazamentos na fase 2 da rodada G em 9a99a8d8): a camada estrutural, o nome do catálogo como destino e os controles operacionais (branch fix/31-9-rodada-h)

- H-1, a segunda passada estrutural, ainda lista de bloqueio:
  - (a) conector com valor desconhecido depois de qualquer verbo de entrar, salvo depois de pessoa ou conversa ("entra
    aqui com", "entre no feed com", "entre no perfil com"). Valem também:
    - o passado ("entrei com");
    - outras oito línguas;
    - "com a conta <nome desconhecido>". O "conta" de `_ONDE_SE_ENTRA` o isentava: era vazamento da base.
  - (b) o par com o campo de login e separador que não é palavra, sem verbo ("user lucas | girassol", "usr lucas,
    girassol").
  - (c) duas letras soltas com pontuação depois de digitar ("g+i", "g · i").
  - (d) telefone ditado em holandês e sueco, pela lista de numerais. A forma genérica do pedido exigiria lista de
    permissão e voltou à orquestradora.
  - (e) e-mail com rótulo ("e-mail: X, provedor: Y"), em peças sem ponto ("zilda em correio, net"), em holandês e com
    "#" no lugar do "@".
- H-2: o domínio de topo separado fica no `[email]` ("zilda@correio. net").
- H-3: o nome do catálogo sozinho depois do verbo de entrar é destino ("entre com o lucas", "entre como lucas"). O
  par, o valor colado e o conector depois de outro destino continuam recusando. Residual aceito: a persona com o nome
  da própria senha.
- H-5:
  - endereço é logradouro, número e CEP: "a padaria do bairro" e "a foto da casa" passam, "casa 3" recusa;
  - "o e-mail da newsletter no outlook" é a mensagem no app;
  - "maria.clara" e "p.ex." não viram `[link]`.
- ADR-069, item 16; bloco "Rodada H" em `docs/ia.md`.
- Prova `simulated`:
  - `tests/test_decisao_fechada_reverificacao_h.py`; os testes da G e da E foram reescritos para a H-3;
  - harness da orquestradora, corpus de 579, catálogo sem a "Girassol": 0 vazamentos (eram 47), 0 passagens
    indevidas, 5 recusas indevidas (as 4 antigas e o caso 538, que a orquestradora reetiquetou para recusa);
  - 122 comandos reais: as mesmas 2 recusas da base.

## 2026-10-03 — Aprendizado: o desfecho medido das revisões do curador (30.35; branch feat/30-35-resultado-posterior)

- Catorze dias depois de uma revisão do curador sobre receita ou lição, a curadoria grava
  `learning_reviews.resultado_posterior`, o rótulo 2 do golden set do Jev, e `resultado_em`.
  - Valores: `descartar`, `rebaixar` (pela escada ou pelo degrau D-5 da saúde), `manter` e `sem_desfecho`.
  - Sem IA, sem migração, uma gravação por revisão.
- O relatório do 31.10 já lê o campo e não muda.
- Prova `simulated`: `test_learning_resultado_posterior.py` (20 testes). Bateria dos afetados: 320 passed.
- `real`: a partir de 17/10.
## 2026-10-03 — Aprendizado: a autopublicação do fluxo B em sombra (30.34-A; branch feat/30-34-autopublicacao-sombra)

- Emenda datada à D1 do ADR-054, decidida pelo dono ("sim" à P2): o fluxo de classe B pode publicar sozinho. Precisa do
  parecer `aprovar` com confiança alta e de ≥ 2 execuções reais em ≥ 2 aparelhos, sem evidência contra. Só depois da
  sombra: ≥ 30 casos fechados com ≥ 90 % sem regressão em 7 dias.
- Esta fatia vai até `shadow`:
  - a regra pura;
  - o livro da sombra, como o sinal `autopublicaria`, sem migração;
  - o laço sob a trava de líder;
  - o balanço nas métricas (`curador.autopublicacao`);
  - o config `aprendizado.autopublicacao.modo`, `off` de fábrica, com `on` recusado até a 30.34-B;
  - o contrato no adendo v0.93 da API.
- O central liga `shadow` no deploy 11 (orquestradora, 03/10). `on` só depois do relatório da sombra.
- Prova `simulated`: `test_learning_autopublicacao.py` e `test_learning_autopublicacao_sombra.py`.

## 2026-10-03 — Deploy 9 no central (suíte 9; sombra C0–C1 do curador ligada, T_on do 31.10; curador volta ao padrão)

- Código: main `3dcfc9ac` (suíte 9), sem migração nova (segue a 081):
  - 29.47 (ambiente dos filhos);
  - 31.9 rodadas E–G, mais a correção da catraca;
  - UX do deploy 7 da Jev;
  - 30.30 (#144) e RA-20 B (#145);
  - 31.17 (envio aberto no código para a sombra);
  - 29.44 (`sem_trafego`), mais a conta observada sem seletor cru.
- Backup do deploy: `data/backups/20261003-080304` (integridade ok). Agente do notebook em `0.1.0+3dcfc9a`, com o
  reparo dos aparelhos dele pausado durante a troca. A pausa do 01/03/06 foi renovada depois do restart (K-082).
- `config/config.yaml` do central (backup `config-antes-deploy9-20261003-110326.yaml`; fora do Git):
  - `aprendizado.curador`: alfa e k voltam ao padrão (0.10 / 1.5); saem as linhas do deploy 8.
  - `ai.decisao_fechada: {enabled: true, decisor: jev, consumidores: {curador: shadow}, classes_permitidas: [C0, C1]}`.
    A `intencao` fica ausente, ou seja `off`. Não se escreve `off`/`on` sem aspas (K-081).
  - Conferido pelo `load_config` do código novo antes do `deploy.ps1`.
- **T_on do 31.10 = 11:04:15Z**, a partida do backend. `GET /api/ai` às 11:05:04Z: `decisao_fechada.sending: true`,
  `decider: jev`, `key: configurada`, `consumers: {curador: shadow}`, `classes: [C0, C1]`.
- Quebras de série a partir deste deploy:
  - a fatia do curador volta ao ritmo padrão;
  - a sombra do Jev começa a gravar em `decisao_fechada_sombra`, e as chamadas aparecem em `ai_calls`
    (`origem='decisao_fechada'`);
  - em `network_measurements.per_app`, o app parado passa de `nao_medido` a `sem_trafego`.
- Prova `real`:
  - `/api/health` ok, commit 3dcfc9ac, migração 081, `problems: []`;
  - 29.47: os 18 filhos do backend novo e o qemu do temporário `android-20` (ligado às 11:08:15Z, automação ready) com 0
    nomes de segredo no ambiente;
  - 29.44: o 03 e o 06 em `trafego_verificado`, com o Outlook `sem_trafego` (medições #465/#466).
- Suíte 9: 1 teste intermitente, `test_revisao_receitas::test_aparelho_do_lider_que_cai_libera_quem_espera`. É race do
  teste, exposto pelo #145; a correção vai para a suíte 10.
- Pendente: validação do painel no Chrome (orquestradora); 29.48 (janela oculta, depois do 30.18).

## 2026-10-03 — Documentação e processo: ADR-067, o aprendizado vivo aceito (30.19; branch docs/30-19-adr-067)

- `docs/decisoes.md`: ADR-067 (índice e seção), com a Fase 30 como foi implementada.
  - Eixo de app, saúde derivada, versão e lineage, curador com política de risco e orçamento proporcional, trilha
    `learning_reviews`, evidência inválida, origem simulada e métricas.
  - O que ficou pendente: D-1 (modelo por faixa), `resultado_posterior`, o curador no `eval_run` (M15) e a prova
    real (30.18).
  - A resposta à D0: o Jev só escolhe em conjunto fechado (ADR-069); o fluxo de navegação segue com o hub e a
    execução; a política (a) rege a curadoria, não a geração.
  - As dependências M1–M16 entre frentes.
- `design/aprendizado-vivo.md`: o §14 aponta para o ADR aceito; o §8.8 corrige os papéis do hub (6 em `AI_ROLES`,
  mais `leitura`, e o `review_knowledge` do 30.12).
- `dominios/aprendizado.md` e a linha da Fase 30 no plano citam o ADR-067.
- Emenda datada do ADR-067 (revisão da Android):
  - as costuras incluem `steps.driven_by` e `attempts.strategy`;
  - M3 depende do LT-6 (a `app_foreground` sem IA não gera sombra);
  - o curador herdou o Sonnet 5.5 low do `plan` no deploy 8, e a concordância (D-3) se mede dos dois lados;
  - o lineage conta a chave genérica do RA-20 B.
- Correção do ADR-067 (revisão da Jev):
  - o rótulo de intenção (30.25) é da pessoa, sem IA, e serve de gabarito da sombra do Jev; não é feito pelo Jev;
  - as alternativas citam o ADR-069;
  - a tabela diz "frente Jev";
  - a 080 entra nas consequências.

## 2026-10-03 — 29.44: app sem tráfego na janela não segura o `parcial` (branch `feat/29-44-sem-trafego`)

- **Antes.** `nao_medido` juntava o app não instalado e o instalado parado na janela, e cada app exigido tinha de
  estar `ok`. No deploy 7, o 03 e o 06 ficaram em `parcial` só porque o Outlook estava parado.
- **Agora** (decisão da orquestradora, 03/10):
  - `per_app` ganha `sem_trafego` (`sonda_rede.Cobertura`).
  - Com outro app `ok` (a sonda do shell conta) e nenhum `fora_da_rede`, o aparelho fica `trafego_verificado` com a
    ressalva no `detail` ("… sem tráfego na janela: não provado, não segura o estado"). A porta da tarefa
    (`apps_sem_prova`) o aceita.
  - "Verificado" = tudo o que trafegou passou pelo túnel. Quando o app trafegar, a medição seguinte o reavalia.
  - Seguem segurando: `nao_medido` (não instalado), `fora_da_rede` e nada ter trafegado.
  - O `_verificar` não dispensa a medição quando o `parcial` veio só de app parado.
  - O painel (Rede) mostra "sem tráfego na janela", com a explicação no título, e "não medido (não instalado ou não
    lido)".
- **Contrato**: adendo v0.88.
- **Prova `simulated`**:
  - `tests/test_rede_sem_trafego.py` (regra e contrato);
  - `tests/test_rede_sonda.py`: o app parado não segura e o tráfego seguinte tira a ressalva; nada trafegou segura
    e a tarefa abre o app; o `parcial` só de app parado não dispensa a medição (falha sem a mudança);
  - `tests/test_rede_portao.py` reescrito para a regra nova;
  - `RedePage.test.tsx`.
- **`not_run`**: o 03 e o 06 saindo de `parcial` no real, depois do deploy.
- **Junto (achado do Chrome no deploy 8, commit próprio).** Em Configuração › Aparelhos e contas, o android-06
  aparecia "diverge · …sem IA (selector:id=action_bar_title|text=={username})" com o aparelho conectado.
  - Causa: o executor gravava em `account_evidence` o texto da prova quando o rótulo aparecia na pós-condição, e a
    prova pela árvore local é um seletor cru, sem o nome.
  - Agora `evidencia_da_conta` grava a frase que nomeia a conta (a prova, se a nomeia; senão a pós-condição).
  - O painel diz "conta diferente do rótulo" com o que fazer no título, e tira o seletor cru das evidências antigas.
  - Prova `simulated`: `tests/test_evidencia_da_conta.py` (3) e `InstancesSection.test.tsx` (2).
## 2026-10-03 — 31.17: envio do Jev aberto no código para a sombra C0–C1 do 31.10; o aviso diz o decisor e só afirma envio de verdade (branch feat/31-17-sombra-curador)

- `JEV_RUNTIME_SEND_APPROVED = True` (ADR-069 item 15). De fábrica nada sai: `enabled: false` e decisor `nulo`.
- `config.example.yaml`:
  - `consumidores: {curador: shadow, intencao: "off"}` e `classes_permitidas: [C0, C1]`, ainda com `enabled: false`;
  - o central liga no deploy 9 (`enabled: true`, `decisor: jev`);
  - o `"off"` entre aspas, porque sem elas o YAML lê um booleano e a configuração não carrega.
- `GET /api/ai`:
  - o aviso diz "Decisor na porta: nulo|jev";
  - "Envio ATIVO" só com código aberto, decisor `jev`, consumidor em `shadow` ou `on` e chave configurada;
  - senão, "Nada sai agora: …" com o motivo;
  - o bloco `decisao_fechada` ganha `sending`.
- Prova `simulated`:
  - `tests/test_decisao_fechada_payload.py`: os bytes que o `DecisorJev` posta são só `{state, model, questions}`. Na
    intenção vão o comando redigido, o app e as opções; no curador, só os campos C0. Nunca `run_id`, `ref`, o original,
    os destinos, os ids crus nem o hash do dossiê.
  - Os testes do interruptor fechado o fecham por `monkeypatch`.
  - O transporte é `httpx.MockTransport`, e a chamada real ao Jev é `not_run` (deploy 9, 31.10).

## 2026-10-03 — Receitas: a chave genérica (RA-20 fatia B, item 29.40; branch feat/ra-20-chave-generica)

- A receita da etapa julgada pelo modelo, sem efeito e sem `commit_guard`, cujo caminho não traz o literal do valor,
  mora na chave genérica (sem o texto da pós-condição). Assim casa com outra redação e outro valor.
  - `hash_generico`, `eh_generica` e a consulta em duas chaves no `RecipeStore.find` (a específica vence).
  - Os consumidores leem as duas chaves: capacidades, aproveitamento e o `_caminho_ja_aberto` do scheduler.
- Backfill não destrutivo: `scripts/ra20b-receitas-genericas.py` (ensaio por padrão; `--aplicar` com backup e o OK da
  Android e da orquestradora).
  - Ensaio na cópia do central, depois da regra do título (da0de919): 9 candidatas semeadas em 9 chaves; 6 específicas.
  - Nenhuma receita atual muda.
- Prova:
  - `simulated`: `test_receita_chave_generica.py` (24 testes), com o classificador nas ações reais das receitas 25, 73,
    38 e 40 (@ fictícios), a paridade linha × etapa, a ordem da consulta, a troca entre chaves, o passe e uma execução
    de ponta a ponta: outra redação e outro valor reproduzem pela genérica, com 0 decisões de IA;
  - o teste de ponta a ponta FALHA com a consulta genérica desligada;
  - `test_equivalencia_fluxo_skill.py` passa a esperar a chave genérica.
  - `real`: `not_run`. O efeito aparece depois do backfill e de `recipes_promote_after` concordâncias.

## 2026-10-03 — Aprendizado: o pedido de pessoa fura a fila do curador e o parecer grava o custo (30.30; branch fix/30-30-pedido-fura-a-fila)

- O pedido de revisão (`POST /api/aprendizado/{kind}/{ref}/revisao`) ganha a prioridade `PEDIDO_DA_PESSOA` (0).
  - Vai na frente de todos os itens, também no pico, sob o teto da hora e o orçamento da janela.
  - A classe A segue só com sobra.
  - Antes ele só pulava o cooldown. Em 03/10, 12 pedidos esperavam atrás de ~40 itens, a ~6 por volta; o laço das 08:29Z
    revisou 6 outros e adiou 36 por `gasto_da_hora`.
- Deploy 8, só para drenar o acúmulo: `aprendizado.curador.alfa: 0.7` e `aprendizado.curador.k: 6` no `config.yaml` do
  central (decisão da orquestradora). Dá B ≈ US$ 7,3 e teto ≈ US$ 0,52 por hora; 2 voltas drenam ~40 itens.
  - Com G_W = US$ 11,48, o padrão dava B = 1,15 e teto ≈ US$ 0,08 por hora. O `k·n·c̄` (≈ 1,8) travava, mesmo subindo só o alfa.
  - VOLTA a 0.10 e 1.5 no deploy 9.
- `learning_reviews.usd` passa a gravar o custo que o hub mediu. Antes saía 0.0: nas 6 revisões das 08:29Z, contra
  +US$ 0,1182 no `/api/usage`.
  - A resposta simulada grava 0.
  - O orçamento do curador passa de estimado pelo dossiê a medido (c̄, mediana e gasto da janela).
  - A pendência da rubrica (`design/hub-de-ia-fora-de-execucao.md`) caiu por decisão da orquestradora: nada soma essa
    coluna no `/api/usage` nem no teto do dia.
- O `c_max` (recusa por custo) passa a comparar estimativa com estimativa: `m_cmax × mediana` das estimativas da volta.
  O custo medido entra só no c̄.
  - A estimativa usa o preço do modelo mais caro da tabela. Com o custo medido gravado e o curador num modelo barato (o
    Haiku da D-1), o c_max cairia para ~0,012 e toda estimativa (~0,014 nas 21 revisões reais) viraria `recusada:custo`,
    em silêncio.
  - Com o deploy 8, o curador roda no Sonnet 5.5 (papel `plan`). O c_max medido seria ~0,023 e não recusaria hoje, mas
    o risco ficava armado.
  - O B do deploy 9 (alfa 0.10, k 1.5, c̄ medido ~0,009) volta a ~6 itens por volta.
  - Prova `simulated`: `test_learning_curador.py::test_modelo_barato_nao_recusa_as_estimativas_em_silencio`, que FALHA
    com a regra antiga (conferido).
- Prova `simulated`:
  - `test_learning_curador.py::test_pedido_da_pessoa_vai_na_frente_menos_na_classe_a_e_sob_o_teto`;
  - `test_learning_pareceres.py::test_o_pedido_da_pessoa_fura_a_fila`: com o teto deixando UMA revisão na volta, ela é a
    do pedido (classe B), e não a da classe C.

## 2026-10-03 — O total do custo de IA bate com as contas, e o needs_input da habilidade fala português (I1 e I4 da validação do deploy 7; branch feat/ux-deploy7-jev)

- I1, `GET /api/usage`: o total e os grupos contam o custo declarado (a imagem da persona), na mesma base de `by_account` e
  `by_origin`. No central, as contas somavam US$ 0,27 a mais que o total.
- I1, Diagnóstico › Custo de IA:
  - "prazo da etapa esgotado" e "saldo da conta abaixo do bloqueio" saem com rótulo;
  - entra a linha "Por origem" (RA-10).
- I4: a habilidade que casou e não compilou vira `needs_input` com o nome dela e o motivo em português ("faltam valores
  para os parâmetros do plano"). O id e o código ficam no evento; o código também num campo próprio do `run.updated`,
  `issue_codes` (a suíte 8 pegou o teste da fatia que lia o código no texto).
- Prova `simulated`:
  - `tests/test_uso_total_com_custo_declarado.py`, `tests/test_needs_input_da_habilidade.py` e `usage.test.ts`;
  - o cartão de custo percorrido no navegador contra um backend simulado.

## 2026-10-03 — Configuração › IA mostra o esquema do plano, os perfis e o esforço por função (I2 da validação do deploy 7; branch feat/ux-deploy7-jev)

- `GET /api/ai` ganha `esquema_do_plano`, `profiles[]` e `leitura_visual` (adendo v0.87).
- Configuração › IA:
  - a Situação ganha "Esquema do plano", "Perfis de IA" e "Leitura visual";
  - o esforço sai em português;
  - a tabela Por função ganha "Esforço" e "Raciocínio";
  - a linha `leitura` vira "Ler a tela (leitura visual)";
  - um cartão novo, "Perfis de IA".
- O aviso ("Modelos por função — plano: …") passa a dizer o modelo que cada função usa de fato, a mesma resolução de
  `roles[]` e `models`. Na validação do deploy 8 ele dizia `claude-opus-5-5` no plano, que a instância do ator lê do
  `.env`, enquanto a Situação e a tabela diziam o `claude-sonnet-5-5` de `ai.roles.plan`. A frase sai de
  `provider.frase_dos_modelos`, e o hub a refaz com `ai.roles`.
- Prova `simulated`:
  - `tests/test_aba_ia_esquema_e_perfis.py` (inclusive `test_o_aviso_diz_os_modelos_que_as_funcoes_usam_de_fato`),
    `aiLabels.test.ts` e `AiSection.test.tsx`;
  - tela percorrida no navegador contra um backend simulado do worktree.
## 2026-10-03 — Correção do 31.9, rodada G (NO-GO da fase 2 da rodada F em 7c8f58c8): o usuário como @handle ou e-mail, o catálogo só no destino e a preposição só com faixa (branch fix/31-9-rodada-e)

- G-1: "entre com @zilda.prado e girassol" e "entre com lucas@outlook.com e girassol" recusam como par; o "@" solto não é
  destino.
- G-2:
  - soletração com vírgula, barra e pelo nome das letras;
  - "log-in with";
  - "entre" preposição só com faixa ou "os"/"as";
  - eufemismos novos;
  - "usuário X, Y." sem verbo.
- G-3: e-mail com hífen, parêntese, "lá", "-at-", "_at_" e "-dot-"; provedores fastmail, laposte, web.de, mail.ru e me.com.
- G-4: o nome do catálogo vale inteiro e só na posição de destino, nunca na de valor (a persona "Girassol" não isenta a
  senha). "Entre com o Lucas e curta" volta a pular a sombra.
- G-5: "é entre 8 e 12" e "é entre os melhores" passam.
- G-6: a palavra C7 sem valor continua recusando.
- Residual de outro idioma: sueco, catalão, "the usual is", e algarismos ditados em alemão, italiano e francês.
- ADR-069, item 14.
- Catraca do ADR-052: os nomes dos apps saem das listas do filtro e vêm do `app.yaml` (nome, rótulo e o campo novo
  `apelidos`; o Instagram declara `[insta]`). Os serviços de terceiros sem pacote ficam numa lista à parte.
- Prova `simulated`:
  - `tests/test_decisao_fechada_reverificacao_g.py`;
  - corpus G da orquestradora (492 casos, com a persona "Girassol"): ok 488, 0 vazamentos de portão, 0 C7 mascarada, 0
    passagens indevidas;
  - 122 comandos reais: 1 recusa, a mesma.
- Suíte 9: o nome dos apps chega ao filtro por GANCHO, sem import tardio. A fila registra
  `registry.nomes_e_apelidos` na subida; sem registro, nenhum nome. O import dentro de `nomes_dos_apps` furava
  `test_arquitetura::test_imports_tardios_so_diminuem`. Prova `simulated`: o teste de arquitetura, os do filtro (B a G)
  e o harness da orquestradora, com 0 diferenças contra 2560756d nos 492 casos.

## 2026-10-03 — Correção do 31.9, rodada F (NO-GO da fase 2 da rodada E em db45d4fd): barrar pela intenção de entrar, "com X" pelo catálogo real (branch fix/31-9-rodada-e)

- F-A: o verbo de entrar sem objeto de navegação faz a sombra pular o comando (`c7_intencao_de_entrar`), no original.
- F-B: os nomes do catálogo de destinos real chegam ao filtro (`RunService.dados_da_sombra`, 5º item;
  `intencao.nomes_de_destino`). O "com X" é destino só quando X é do catálogo ou foi tirado pelo extrator.
- "entre" preposição ("as fotos postadas entre 10/05 e 12/05") deixa de ser verbo.
- F-C a F-H:
  - campo de usuário mais largo;
  - diminutivos;
  - pergunta de segurança;
  - letras soltas;
  - nome + provedor sem preposição e "point";
  - CPF nu.
- ADR-069, item 13.
- Prova `simulated`:
  - `tests/test_decisao_fechada_reverificacao_f.py`;
  - corpus F da orquestradora (427 casos, catálogo stub e real): 0 vazamentos de portão, 0 C7 mascarada, 0 passagens
    indevidas;
  - 122 comandos reais: 1 recusa, a mesma.

## 2026-10-03 — Correção do 31.9, rodada E (NO-GO em 963f9d7b): C7 pela intenção de entrar, "senha" em outras línguas e e-mail em peças em português (branch fix/31-9-rodada-e)

- C7 sem palavra-chave:
  - traduções de "senha" em escrita latina e eufemismos novos;
  - a regra estrutural `c7_login_valor` ("entre com girassol", "pra entrar: girassol");
  - a regra estrutural `c7_par_credencial` ("usuário lucas e girassol, entra", "entre com a conta Lucas / girassol").
- A C7 é conferida também no comando original: `RunService.dados_da_sombra`. `dados_da_intencao` segue igual.
- E-mail ditado em peças em português recusa.
- A máscara do e-mail engole a parte local inteira, o `mailto:`, o `?subject=` e o domínio de topo solto. A do telefone
  engole o `tel:`.
- Prova `simulated`:
  - `tests/test_decisao_fechada_reverificacao_e.py`;
  - corpus E da orquestradora (360 casos): 0 vazamentos de C7, 0 C7 mascarada, 0 passagens indevidas;
  - 92 comandos reais: 1 recusa, a mesma.
## 2026-10-03 — 29.47: os processos filhos não herdam mais os segredos do backend (branch `feat/29-47-ambiente-dos-filhos`)

- **Antes.** O ambiente dos filhos era o do backend inteiro (`dict(os.environ)`), com os segredos do `.env`:
  - `SdkTools.env()`: adb, emulador, avdmanager e Appium;
  - o sing-box da rede (`rede_servidor.ProcessosReais.lancar`), processo longo de terceiro;
  - as sondas do Diagnóstico (`diagnostics._run`).

  Na rodada por adição do K-078, o qemu tinha `TYPESAFE_API_KEY` (só o nome foi lido).
- **Agora.** `devices/sdk.py` ganha `ambiente_dos_filhos()`, uma lista de PERMISSÃO usada pelos três caminhos:
  - passam as variáveis do sistema e do perfil, `JAVA_HOME`, `ANDROID_*` e `ADB_*`;
  - a 2ª trava recusa `TYPESAFE_*`, `OPENAI_*`, `ANTHROPIC_*`, `GEMINI_*`, `FARM_*` e nome com cara de segredo, mesmo
    que um prefixo deixasse passar;
  - a decisão é pelo nome, sem olhar o valor. O agente do notebook recebe o mesmo, porque `devices/sdk.py` está no
    manifesto.
- **Prova `simulated`**: `tests/test_ambiente_dos_filhos.py` (5).
  - Os filhos são reais (o Python do venv imprime os NOMES do próprio ambiente), com variáveis sentinela de nome de
    segredo no pai.
  - Os 3 testes de filho falham com o código anterior.
- **Fumaça local** (03/10, central, sessão 1, código do branch): com o ambiente filtrado (38 de 98 nomes),
  `adb version`, `emulator -accel-check` e `avdmanager list avd` dão rc 0, e o Appium 3.7.0 também (`--version` e
  `driver list --installed`).
- **`not_run`**: aparelho ligando e Appium subindo pelo backend com o código novo, depois do deploy.
## 2026-10-03 — Aprendizado: métricas e lista de revisões (30.8; branch feat/30-8-metricas)

- `GET /api/aprendizado/metricas?app=&dias=` (adendo v0.89): um bloco por linha da tabela do §10 do desenho.
  - Composição do livro, aprovações automáticas × de pessoa, pareceres do curador e custo.
  - Refutados depois de promovidos, sucesso depois de promovido, churn, tempos (mediana, p90 e `n`) e saúde.
  - Economia (o aproveitamento, reaproveitado) e o proxy de falhas, rotulado como proxy.
  - Orçamento do curador (B_W, C_W, uso e aviso a 80 %).
  - Ausente é `null`, e o simulado fica fora.
- `GET /api/aprendizado/revisoes?app=&decisao=&desde=&limite=&cursor=`: os pareceres do curador paginados, sem o dossiê.
- `curador.py`: a conta de C_W virou `janela_do_orcamento`, a mesma na volta e na métrica.
- Ensaio só de leitura na cópia do banco do central: 142 ms no global e ~55 ms por app.
  - Pegou 2 defeitos, corrigidos antes do PR: o B_W caía a zero sem `usd` medido, e `criados` contava lembranças.
- Prova: `simulated` (`tests/test_learning_metricas.py`, 11). O `real` vem com a leitura no central depois do deploy 9.

## 2026-10-03 — Aprendizado: quebra de série do LT-6 no deploy 8

- `docs/dominios/aprendizado.md` (O que mais falha) registra o que muda desde 03/10 09:06:28Z (deploy 8, df860763).
  - A etapa `app_foreground` aberta pelo executor sem IA fecha como `sem_ator`. Com isso `so_ia` cai, e as elegíveis
    e `sem_cobertura` do aproveitamento caem.
  - Não nasce receita de `app_foreground`, e a candidata `open_app` não recebe veredito de sombra.
  - O `pct_por_receita` não quebra.
  - Do lado da Jev, a amostra "sem casamento" do 31.10 quebra pelos fluxos que o 30.29 revive.
  - Compare só janelas do mesmo lado.

## 2026-10-03 — Deploy 8 no central (suíte 8: RA-19 B, 30.29 e 29.45; planejador no Sonnet 5.5; curador acelerado)

- Código: main `df860763` (suíte 8: #141 RA-19 B, #142 30.29 e 29.45 caminho rápido 2), sem migração nova (segue a 081).
  Backup do deploy `20261003-060610` (carimbo em hora local; o do ensaio é `20261003-060545`); o ensaio de migração na
  cópia do banco não aplicou nada. Agente do notebook em `0.1.0+df86076` (reparo dos aparelhos dele pausado durante a
  troca).
- `config/config.yaml` do central (backup `config-antes-deploy8-20261003-090601.yaml`; fora do Git):
  - `ai.roles.plan`: `claude-sonnet-5-5` com `effort: low` (rodada QA pareada passou: 35 % mais rápido, 43 % mais
    barato, 12/12 nos dois braços). O perfil `planejador-sonnet` fica para A/B; `escalation` segue no Opus; a leitura
    não mudou.
  - `aprendizado.curador.alfa: 0.7` e `k: 6`, para drenar o backlog do curador. **Voltam a 0.10 / 1.5 (o padrão) no
    deploy 9.**
- Quebras de série a partir deste deploy:
  - `ai_calls.escalate = nova_tentativa` passa a marcar só a subida do LT-12 (adendo v0.86).
  - Etapas `app_foreground` abertas pelo executor sem IA (LT-6) não alimentam o `_veredito_da_sombra`.
  - O custo e a latência do `plan` mudam de modelo (Opus → Sonnet 5.5 low).
- Prova `real` (03/10, central):
  - `/api/health` às 09:07Z: ok, `df860763`, migração 081, `problems: []`;
  - `GET /api/ai`: `plan` = `claude-sonnet-5-5` / `low`, `escalation` = `claude-opus-5-5`, `leitura` =
    `gemini-3.1-flash-lite`;
  - agente `0.1.0+df86076` online às 09:08Z, com os 4 aparelhos do notebook `online/ready`.
- `not_run`: o aceite de latência do 29.45 e a validação do painel no Chrome, até o tráfego medir.

## 2026-10-03 — caminho rápido 2: LT-5, LT-6 e LT-12 (item 29.45, branch `feat/lt-5-6-12-caminho-rapido-2`)

- **LT-5.** O "não" em tela parada encerra a verificação em 3 sondagens (`SONDAGENS_DA_TELA_PARADA`), sem esperar o
  orçamento. Não vale para `patient` com `pending_marks` declaradas nem para nível de entrega acima de `sent`.
- **LT-6.**
  - O executor abre o app da etapa `app_foreground` sem IA, como estratégia `deterministic` (`OPEN_APP_SEM_IA`): uma vez
    por tentativa e sem receita conduzindo. Se não comprovar, o ator assume na mesma tentativa.
  - `esperar_foco` sonda a 0,5 s nos primeiros 5 s.
  - A partir do deploy 8, essas etapas não alimentam o `_veredito_da_sombra`. A candidata v4 de `open_app` do QA não
    promove por sombra.
- **LT-12.** A nova tentativa começa no tier 0 e sobe na 1ª decisão que repete, na mesma tela estrutural, onde a anterior
  parou, ou que dispararia o efeito. Essa decisão é descartada antes de agir. A memória fica no processo: depois de um
  restart, tier 0 sem esse gatilho.
- **Contrato**: adendo v0.86 (`attempts.strategy` com `deterministic`; `escalate` = `nova_tentativa` mais estreito).
- **Prova `simulated`**: `tests/test_caminho_rapido_2.py`. Os testes de ANR e de recusa, cujo gancho é a decisão da IA
  que abre o app, desligam `OPEN_APP_SEM_IA`. `not_run`: o aceite de latência no real.

## 2026-10-03 — 30.29: o fluxo com variável de execução no plano casa também pela habilidade (branch feat/30-29-fluxos-com-variaveis-de-execucao)

- `skills/domain/matching.py::bind_template_parameters` deixa de exigir do comando os RESERVED (`account_label`,
  `instance_id`, `run_id`). O valor é do aparelho e entra na materialização, como no `FlowStore.match` desde o LT-3.
- Antes: o `LegacyFlowAdapter` resolvia o fluxo pelo `FlowStore.match`, e o `legacy_plan` não compilava.
  - O comando que casava ia a `needs_input` sem planejador. O abrir-tela da rodada QA de 03/10 caiu assim.
  - 5 fluxos ativos do QA, com `{account_label}` (e às vezes `{instance_id}`/`{run_id}`) nos parâmetros, tinham
    0 usos.
- O aprendizado do fluxo já não templatizava os RESERVED; agora há um teste de regressão.
- Núcleo (skills): revisão da Jev na parte do `matching.py`, e suíte 8.
- Prova `simulated`: `tests/test_flows_account_label.py` (6; os 2 novos de bind/compilação falham sem a correção).

## 2026-10-03 — Aprendizado: o que uma execução simulada ensina não publica (RA-19, fatia B; branch feat/ra-19-origem-simulada)

- Origem simulada nunca nasce ativa: a receita nem com `ai.recipes_promote_after: 0`, o fluxo nem com
  `aprendizado.fluxo.com_prova: false`.
- A concordância de uma execução simulada não promove receita (`RecipeStore.shadow(simulada=True)`). A sombra segue
  promovendo com evidência real, e a pessoa promove à mão.
- Config nova: `aprendizado.simulada_publica` (padrão `false`; `true` só na suíte, que é toda simulada).
- Sem migração. Prova `simulated`: `tests/test_origem_simulada.py` (6), com a consulta de join = 0.
- Núcleo (`executor.py`, `recipes.py`, `config.py`): revisão da Android e suíte 8.

## 2026-10-03 — Aprendizado: polimentos da validação do deploy 7 (B1 e I5; branch fix/aprendizado-ux-deploy7)

- B1: o parecer do curador deixa de sair como "da IA" no painel.
  - "Parecer do curador: …", "Aceitar pareceres do curador (n)" e "Pedir revisão ao curador";
  - "O curador sugere", "O curador ainda não revisou este item" e os sinais "Pediu revisão ao curador" /
    "Decidiu um parecer do curador";
  - o que fala do ator continua "IA".
- I5 (RA-24 com tela): Aprendizado › Aplicativos › (o app) › Declarado mostra "Conhecimento em uso" (arquivos
  conferidos e desde quando nada mudou), o sha curto de cada arquivo (o inteiro e o blob do git no `title`) e o selo
  do arquivo gravado depois de o servidor subir. Lê `GET /api/apps/{pacote}/conhecimento`; o 404 (app sem conhecimento
  declarado) vira "sem linha".
- Prova:
  - `simulated`: `AplicativosTab.test.tsx` (+2), `ParecerDaIA.test.tsx` (as negativas olham o rótulo novo) e
    `IntencaoSecao.test.tsx`; 131 testes do aprendizado e 1339 do painel; typecheck;
  - percorrido no navegador numa cópia do banco (8766, IA simulada): Instagram com 4 arquivos, o estado "mudou" (mtime
    tocado), QA Messenger sem linha, Para aprovar, o detalhe com "Pedir revisão ao curador", Sinais e 375 px.

## 2026-10-03 — Rodada QA pareada: a pré-checagem também olha a habilidade (branch main)

- A 1ª rodada (03/10 07:37Z, `qa-par-202610030737`) foi parada pela sessão do aprendizado:
  - o abrir-tela casava com uma habilidade de fluxo que não compila para o próprio comando, e as 4 execuções foram
    a `needs_input` sem planejador;
  - o msg-todos-os-contatos também casa com habilidade;
  - com 2 casos válidos, as 6 válidas por braço do aceite eram impossíveis.
  Custo: US$ 0,2621, em 2 execuções do planejador; 0,1777 no Opus e 0,0844 no Sonnet.
- `scripts/rodada_qa_pareada.py`:
  - `fora_do_planejador` consulta `POST /api/flows/match` e `POST /api/skills/resolve` (no aparelho da rodada),
    na pré-checagem e no pulo por caso;
  - o `--checar` imprime "Pré-checagens ok" quando passa.
- `--repeticoes N` repete o bloco ABBA de cada caso (ABBAABBA). Decisão da orquestradora para a 2ª rodada: os 3
  casos que chegam ao planejador × 2 = 12 por braço.
- Prova `simulated`: `scripts/tests/test_rodada_qa_pareada.py` (13).
- No central, só leitura: os 4 casos padrão são recusados (2 por habilidade), e
  `--casos perfil-campo-inexistente,comando-ambiguo,sessao-expirada` passa.

## 2026-10-03 — Aprendizado: quebra de série do `pct_por_receita` no deploy 7

- Documentação e processo: `docs/dominios/aprendizado.md` (O que mais falha) registra que, desde 03/10 07:28:40Z
  (deploy 7, 49811568), a etapa da IA com as receitas desligadas (`ai`, RA-10) e a fechada sem o ator (`sem_ator`,
  LT-1) entram no denominador; a série só se compara do mesmo lado.

## 2026-10-03 — Script da rodada QA pareada (canário do planejador; branch chore/rodada-qa-pareada)

- `scripts/rodada_qa_pareada.py`: Opus padrão × perfil `planejador-sonnet` em ABBA por caso, nos 4 casos do
  `eval-set.yaml` que chamam o planejador (12 dos 17 casavam com fluxo ativo em 03/10, e aí o A/B não mede nada).
- Sem opção, só o plano. `--checar` faz as pré-checagens de custo zero, `--yes` roda com teto e `--ler` dá o veredito
  (sucesso B ≥ A e p50 do planejador no B ≤ 11 s, com 6 válidas por braço).
- Prova `simulated`: `scripts/tests/test_rodada_qa_pareada.py` (10). `--checar` no central (03/10) só acusou o deploy 7
  que falta; nada rodou nem gastou.

## 2026-10-03 — Cache, entrada e latência antes × depois de um deploy (branch feat/jev-leitura-cache-latencia)

- `scripts/jev-leitura-cache-latencia.py` compara, por função e por perfil, as chamadas de `ai_calls` antes e depois de
  um corte (o instante do deploy). Mede:
  - o cache lido e escrito sobre a entrada total;
  - a entrada p50;
  - a latência p50 e p95;
  - o custo por etapa e por execução, pela regra de `planning/costs.py`.
- As colunas do RA-10 entram quando a migração 080 existe; sem ela, a quebra por motivo é `not_run`.
- Só leitura, sem IA. Descrição em [ia.md](docs/ia.md) (custo e uso).
- Prova `simulated` (`scripts/tests/test_jev_leitura_cache_latencia.py`, 12 testes). Leitura `real` só leitura no banco do
  central (esquema 078).

## 2026-10-03 — 31.10: o script do relatório da sombra do Jev (branch feat/31-10-relatorio)

- `scripts/jev-relatorio-31-10.py` mede a sombra do curador (por `kind`) e da intenção (por app) contra os limiares
  pré-registrados do golden set (§1–§3). Para cada estrato dá:
  - o veredito (GO, NO-GO ou "sem amostra") e a data prevista do GO;
  - o custo e a latência;
  - o teto de cobertura da R2.
- O script é só leitura, também na reconexão, e não chama IA. Descrição e limites: [jev-golden-set.md](docs/design/jev-golden-set.md)
  §5.
- Prova: `simulated` (`scripts/tests/test_jev_relatorio_31_10.py`, 11 testes). No banco do central: `not_run` (depois do
  merge da suíte 7).

## 2026-10-03 — 29.34 (RA-15): o relógio do wake começa no snapshot carregado (branch feat/29-34-relogio-do-wake)

- `DeviceManager._wait_boot`: o `wake_timeout_s` (90 s) deixa de contar do spawn. Antes do veredito do log vale
  `boot_timeout_s`; com "Successfully loaded snapshot" o prazo de 90 s conta dali. `boot_seconds` continua
  spawn→online. A medição `boot kind=warm` ganha `load_ms`. Testes: `test_wake_relogio_do_snapshot.py` (`simulated`).
  Aceite real em 7 dias (wake > 90 s e "snapshot descartado" = 0): `not_run`.

## 2026-10-03 — 25.12: túnel morto age e o backend verifica a rede ao subir (branch feat/25-12-tunel-morto)

- Objetivo parado há horas não segura mais o aparelho (`vitrine.objetivo_que_segura`, metade C do 25.12; corrige
  c185eda3, que tratava `completed_with_issues` como terminal e revertia a decisão do PR #13): `completed_with_issues` é
  o rollup IMEDIATO de todo `waiting_user`/`uncertain` com nada rodando, então o objetivo de agora segura (a tela é a
  evidência do operador), mas o de `OBJETIVO_PARADO_SEGURA_POR_S` (2 h, valor PROVISÓRIO: o orquestrador decide) atrás
  solta. A idade é `objectives.finished_at` (sem ele, `runs.finished_at`/`created_at`). Execução viva segura sem limite;
  `completed`, `cancelled` e `failed` soltam como sempre. Medido em 03/10: o objetivo de 02/10 adiou o teste de vazamento
  do android-03 por ~1 h, e o android-01 tem 18 objetivos assim desde 28/09. Efeito nos chamadores em
  `docs/dominios/parque.md`. Prova `simulated`:
  `tests/test_sempre_na_promovida.py::test_objetivo_parado_so_segura_enquanto_recente_em_execucao_com_pendencias` (e o
  teste do PR #13, restaurado), `tests/test_rede_sonda.py::test_objetivo_parado_ha_horas_nao_adia_o_teste_de_vazamento`.
- **Túnel morto age (metade A).** A verificação de um `trafego_verificado` com política exigida cuja sonda não mede IP de
  saída tira a linha do estado (→ `conectado`, motivo e evento `tunel_morto`), religa o cliente VPN no aparelho (até 2
  vezes: `force-stop` e always-on, ou Start da interface com teto de 60 s) e, sem volta, reinicia sem wipe. Falha ou
  trava da interface conta como tentativa (caso do android-06, 03/10). O teste do portão
  `test_remedicao_sem_ip_espera_antes_de_repetir` mudou: afirmava o comportamento que o item corrige (o estado não saía de
  `trafego_verificado`). Prova `simulated`: `tests/test_rede_sonda.py::test_tunel_morto_*`.
- **Backend ao subir (metade B).** `ConvergenciaDeRede.verificar_ao_subir`, chamado no `start` do scheduler, marca a
  medição do tráfego dos aparelhos com política exigida e rede conectada: todo reinício do backend derruba os túneis.
  Não usa o `verify` (com bloqueio ele apaga a prova de vazamento e reinicia o aparelho): a prova segue valendo.
  Prova `simulated`: `tests/test_rede_sonda.py::test_backend_ao_subir_*`.

## 2026-10-03 — IA: esforço e thinking por função, sonda "o ator pensa?" e dieta do contexto 2 (17.14 e RA-17, branch feat/17-14-perfil-e-dieta)

- `ai.roles.<f>` e os perfis ganham `effort`, `thinking: false` e `cache_da_etapa`; o perfil ganha
  `screenshot_max_side` e `rich_tree_min_elements`, só nas execuções dele. Nada escrito = a requisição de antes, byte
  a byte.
- `cache_da_etapa` (RA-17): o passo e as lições vão antes da imagem, com o 2º ponto de cache.
- Uma função com ajuste ganha instância de provedor própria.
- `GET /api/ai`: `roles[].thinking` (a sonda) e `pensou=N` na linha de uso do log (adendo da API pedido à
  orquestradora).
- Prova `simulated`: `tests/test_perfil_esforco_e_dieta.py` (16). `real`: `not_run` (sem A/B pago).

## 2026-10-03 — Aprendizado: o app de teste fora da lista padrão do livro (RA-19, fatia A; branch feat/ra-19-visao-do-livro)

- `GET /api/aprendizado` ganha o filtro `rotulo` (`produto` | `qa` | `todos`, adendo v0.83). O padrão sem app é
  `produto`, que esconde os apps de `apps.category='qa'`. A resposta traz `rotulo` e `ocultos`.
- O Aprendido abre em "Produto", com o seletor segmentado "Produto · QA · Todos" e os ocultos ao lado. O acervo de teste
  continua no livro, na visão por app e nas filas.
- Prova `simulated`: `tests/test_learning_rotulo_do_livro.py` (8) e o painel (119). Sem migração.

## 2026-10-03 — 29.42: o fluxo entre apps diz de que apps precisa (branch feat/29-42-required-apps)

- `GET /api/flows` devolve `required_apps` em cada fluxo, na ordem em que o plano usa os apps (sem migração: a ordem vem
  das etapas do plano gravado; `flow_required_apps` segue sendo só o conjunto). Duas consultas para todos os fluxos.
- O painel (Configurações, Fluxos) e o detalhe do fluxo na aba do curador mostram "QA Messenger → Chrome" com o nome de
  cada app (o id quando o app não está cadastrado). O dossiê do curador (`conteudo.apps`) passa à mesma ordem (antes,
  alfabética); por isso dois testes de domínio mudaram a ordem esperada (outlook, instagram: a do plano).
- Adendo v0.85 em `docs/api-contract.md`. Prova `simulated`: `tests/test_flows_required_apps.py`,
  `frontend/src/lib/appsDoFluxo.test.ts`, `DetalheRico.test.tsx`, `FlowsRecipesSection.test.tsx`; `real`: `not_run`.

## 2026-10-03 — Conhecimento de app: versão conferida e prova do que está no ar (RA-24, parte YAML; branch feat/ra-24-versao-e-prova-do-conhecimento)

- `telas.yaml` e `sessao.yaml` só aceitam `versao: 1` na carga, como a `contract_version` do catálogo. Antes,
  `telas.yaml` trocava qualquer não inteiro por 1 e aceitava qualquer inteiro.
- `GET /api/apps/{pacote}/conhecimento` (adendo v0.82): cada YAML com `sha256` e `git_blob` do texto lido. Com CRLF→LF,
  o `git_blob` bate com `git hash-object` no checkout com autocrlf. A resposta aponta o arquivo que mudou depois de o
  processo subir.
- Prova `simulated`: `tests/test_prova_do_conhecimento.py` (20 testes; 79 com os de telas e sessão). Sem migração.
  Núcleo (`api.py`, `automation/`, `app_declarado/`): revisão da Android e suíte 7.

## 2026-10-03 — 30.26 (RA-22): o tipo da falha sai do erro de IA, não do texto (branch feat/ra-22-error-kind-em-attempts)

- Migração 081: `attempts.error_kind`, o `AIError.kind` que encerrou a tentativa. O executor o põe no desfecho
  (`StepOutcome.ai_error_kind`) e o scheduler o grava na tentativa.
- `classificar_falha(texto, status, error_kind)` decide pelo tipo antes do texto, para a tentativa e para a etapa. A
  mensagem de IA do executor deixa de ser contrato. `step_deadline` segue pelo texto, e o ANR ganha do prazo.
- Mudança pretendida: o teto do pedido ("Orçamento do pedido atingido…") sai de `outro` e vai para `ia_orcamento`. Na
  releitura retroativa, uma chamada que o roteador contornou não desmente mais a tentativa que tem o tipo gravado.
- Nenhum desfecho de etapa, retry ou campo da API muda. Prova `simulated`: `tests/test_falha_pelo_erro_de_ia.py` (13) e
  bateria de 145 arquivos (2688 aprovados). PostgreSQL: `not_run` (só `ADD COLUMN TEXT`, sem dialeto).

## 2026-10-03 — Validação do deploy 4: I1 (sem rótulo não há "diverge") e a CPU do emulador com referência

- **I1:** `observedMatchOf` (`frontend/src/features/settings/instancesView.ts`) devolve `none` sem rótulo configurado. Sem
  conta esperada não há do que divergir: o android-04, hoje de Instagram e sem rótulo, aparecia "diverge" por uma
  observação antiga do QA Messenger. A linha "observado" sai do cartão de Configuração › Aparelhos e contas e do Foco
  quando não há rótulo.
- **Polimento do Foco:** "CPU 108%" passa a "CPU 108% (≈1,1 núcleo)" (`cpuDoEmulador`): o % do processo é de um núcleo
  do host.
- **K-080:** um toque por id de elemento velho abre a tela errada. **K-078:** adendo do braço D, em que o snapshot
  também não é a causa.
- `simulated`: `instancesView.test.ts`, `cpuDoEmulador.test.ts`; typecheck limpo; 139 aprovados em settings e focus.

## 2026-10-03 — 12.5 nível 1.1: o "truncado" vale para o valor, não para a linha vizinha (emenda do ADR-070 §4)

- `conferir_transcricao` (`backend/app/taskqueue/saidas.py`): "truncado" é o do valor do ator, do campo do leitor e da
  linha que contém o valor. A marca global do leitor só cai quando uma linha alheia cortada a explica (K-079).
- `simulated`: em `tests/test_leitura_visual.py`, a prévia cortada com o valor inteiro concorda; o valor cortado, a
  linha do valor cortada e a marca sem linha cortada continuam recusando. 109 aprovados com o arquivo do papel.
- `real` (bancada, 04:57–04:59Z, `ai_calls` 3008–3071, US$ 0,02): gemini-3.1-flash-lite 31/32 e gpt-6-luna 29/32, com
  0/96 falsas. Gemini fica como principal e o luna como alternativo; a opção liga no próximo reinício do central.

## 2026-10-03 — caminho rápido 1: LT-1, LT-2 e LT-3 (pular o ator, nunca a prova)

- **LT-1.** A pós-condição conferida na entrada da volta, antes de o ator decidir: etapa sem efeito, tela não sensível e
  sem saída por ler. Determinística: prova local, custo zero. Julgada: só na entrada e só pela prova local do catálogo
  (`ENTRADA_JULGADA_SO_COM_PROVA_LOCAL`), porque o juiz na entrada subia `verify` por etapa nos testes de custo.
- **LT-2.** `expect_done` em etapa julgada vai direto ao `_verify` (uma rodada) e o veredito é reusado no fim do laço;
  "não" volta ao ator na mesma tentativa (`attempts` continua 1).
- **LT-3.** `flows.match` não exige valor para `account_label`, `instance_id` e `run_id`.
- **`steps.driven_by='sem_ator'`** para a etapa que fecha sem o ator (modelos, `aproveitamento`, painel "Sem o ator").
- **Prova `simulated`**: `tests/test_caminho_rapido_executor.py`, `tests/test_flows_account_label.py`,
  `tests/test_aproveitamento.py`, `frontend/src/lib/status.test.ts` e `ProfileDetail.test.tsx`; três testes de custo
  antigos mudaram de número por causa do atalho (ANR, hub de IA, política de imagem). `not_run`: latência no real.

## 2026-10-03 — Jev: RA-11 (parte Jev) — telemetria vence em 48 h, memória vencida sai do banco, purga em lotes

- `instance.updated` sem execução vence em 48 h (`events.TELEMETRIA_KINDS`/`TELEMETRIA_RETENCAO_H`), antes dos 14 dias do
  resto do log. A purga de `events` vai em lotes de 2.000 linhas. `memory_items` vencidos saem do banco
  (`AppState._purgar_memorias_vencidas`). Ao fim da volta da retenção, `PRAGMA optimize` (SQLite).
- Prova: simulated (`test_retencao_telemetria.py`, 4 testes; 625 afetados verdes). Na primeira volta depois do deploy,
  saem ≈ 27 mil eventos do central (medido em 03/10, só leitura). Real: `not_run`.

## 2026-10-03 — Jev: higiene de docs do RA-24 (parte Jev)

- `docs/ia.md` §10: o gatilho do ADR-023 para voltar ao modelo local não foi atingido (≈ 13 execuções por dia na
  semana de 26/09 a 02/10; Ollama sem chamada desde 24/09). O único uso candidato é uma triagem em sombra, medida
  antes contra o golden set. O roteiro do Jev (local) passa a dizer que o verificador fica fora pela classe do dado
  (C5/C6), e não pela imagem.

## 2026-10-03 — Jev: prévia que executa não é defeito; o início diz quem iniciou (P12 da reavaliação)

- Prévia passa a ser `mode='plan' AND started_at IS NULL`: as 11 execuções em `mode=plan` que gastaram decisões
  tinham sido iniciadas de propósito (medido no central, 03/10). `RunService.start(por=...)` grava `iniciada_por`
  no `run.updated` do início (a pessoa ou `panel` pela rota, `sistema` no `mode=execute`; adendo v0.79).
- Prova: simulated (`test_inicio_com_autor.py`).

## 2026-10-03 — Jev: retenção do histórico de execução registrada (P10 da reavaliação)

- `runs`, `objectives`, `steps`, `attempts`, `actions` e `plan_versions` ficam "para sempre" por enquanto; o resto
  vence como antes. Números de 03/10 (≈ 0,45 MiB/dia nessas tabelas) e os gatilhos para rever em `docs/banco.md`.

## 2026-10-03 — Jev: a porta de política do item 13.2 também no planejamento (RA-7, branch feat/ra-7-recusa-no-planejamento)

- `planning/capabilities.py::efeito_fora_do_catalogo` concentra a regra do item 13.2 (etapa com efeito, num app com
  catálogo, sem ação daquele catálogo). A porta do despacho passa a usá-la, com a mesma frase.
- `RunService._plan` aplica a regra a todo plano (planejador, fluxo, skill) antes de materializar. Uma etapa recusada
  zera o plano, a execução vai a `needs_input` com uma pergunta por etapa, e o evento `plan.refused` leva o motivo
  fechado (`sem_acao_do_catalogo` | `acao_de_outro_catalogo`). Nenhuma decisão é gasta nos preparativos.
- O caso de 01/10 no Outlook (`fill_recipient` com 16 e 17 decisões) é anterior ao catálogo do Outlook no central;
  desde que ele chegou, 5 de 5 planos ligam a capability (`docs/dominios/execution.md`).
- Prova: simulated (`test_recusa_no_planejamento.py`, 12 testes; 616 afetados verdes). Real no android-01: `not_run`
  (frente Android).

## 2026-10-03 — Jev: formato curto do plano atrás de chave (LT-4b, item 17.13, branch feat/lt-4b-esquema-curto)

- `ai.esquema_do_plano: curto` (de fábrica, `longo`, o formato de sempre byte a byte). A etapa livre (plano livre e
  parte livre do plano entre apps) não pede mais `postcondition.description`, `precondition` nem `max_attempts`, e o
  backend os preenche:
  - a descrição vem do `value` (no `model_judged`, o próprio critério);
  - a pré-condição fica nula;
  - a etapa com efeito tem 1 tentativa, e as demais têm 3.
- O prompt pede título e objetivo curtos. Os planejadores curtos são os de sempre com dois trechos trocados, e
  `_trocar` falha na importação se um marcador sumir.
- A identidade da receita é a mesma nos dois formatos. Anthropic e OpenAI mandam o formato curto só com a chave.
- Estimativa grátis (count_tokens em 2 planos reais do QA): −22 a −24 % de saída, ≈ −2,5 s por plano. Não alcança
  os ≤ 11 s do aceite do LT-4 (`docs/ia.md` §12).
- Prova: simulated (`test_esquema_curto_do_plano.py`, mais os afetados). Real: A/B de 3 braços só do planejador
  (03/10, ~04:31–04:39Z, 14 casos QA, 42 chamadas, US$ 1,36). No Opus, o curto tem p50 13,3 s contra 15,9 s,
  2,75 s a menos por plano (pareado), a mesma forma em 14/14 e US$ 0,0359 contra 0,0428 por plano. O Sonnet 5.5
  curto ficou em 7,8 s, mas só com a forma das etapas com efeito igual (`docs/ia.md` §12). Rodada QA: `not_run`.
- Deploy 7: o Sonnet 5.5 entra como perfil 17.7 (`planejador-sonnet`, só o modelo do `plan`; o esforço e o esquema
  seguem os globais). O preço `[2.0, 0.2, 2.5, 10.0]` e o cache mínimo de 512 dele entram no padrão e no exemplo
  (páginas da Anthropic, 03/10; antes, ele casava o prefixo do Sonnet 5). No central, as linhas vão DENTRO dos blocos
  `ai.prices` e `ai.models` que já existem: o YAML troca a tabela inteira. Prova: simulated (`test_perfil_de_ia.py`).

## 2026-10-03 — RA-10: o porquê de cada chamada de IA em `ai_calls` e os grupos de `/api/usage` (branch feat/ra-10-observabilidade, migração 080)

- `ai_calls` ganha `verdict`, `escalate`, `motivo` e `image_reason` (vocabulários fechados em `planning/provider.py`,
  `MarcaDaChamada`). O executor marca toda chamada que passa por
  `_ai` (plano, decisão, cascata, julgamento, vazio, rejulgamento, leitura) e o assistente marca o refinamento; o motivo
  do escalonamento sai do código, e a frase da linha do tempo continua a mesma.
- O rejulgamento (7.10 e 17.10) era gravado como `verify` tier 0: agora é tier 1 com motivo. A linha de erro e a de
  orçamento recusado ganham `provider` e modelo da função; a imagem da persona grava `origem='persona'`; a etapa que a IA
  conduziu grava `driven_by='ai'` também com receitas desligadas.
- `GET /api/usage` ganha só chaves novas: `by_origin`, `escalations`, `rejudges` (com discordância por app),
  `cascades`, `image_reasons` e `steps_driven_by_null`, no preço de `spent_usd` (`costs.usd_por`). Adendo v0.75 do
  contrato; conferência pós-deploy em `docs/ia.md` §9.
- Prova `simulated`: `test_observabilidade_das_chamadas.py` e `test_assistente_do_comando.py`. Prova real: `not_run`.

## 2026-10-03 — Jev: o decisor real da porta `DecisaoFechada` (31.14, branch feat/31-14-decisor-jev)

- `DecisorJev` (`planning/decisao_fechada/decisores.py`): uma chamada ao Jev por pedido, pelo transporte do adaptador de
  retrieval (`JevSemanticProvider.consultar`, cliente único). Só `choice` vai ao fio; `noul` e `score` respondem `desligado`.
- Gasto conferido ANTES do POST: `RoutingProvider.conferir_gasto` aplica a rubrica de `_budget` (pedido, execução, dia, fatia
  do Jev) e o bloqueio de saldo da conta `typesafe`. Barrado, sem hub ou com a leitura quebrada = motivo novo `orcamento`, e
  nada sai.
- Cada chamada tentada vira linha em `ai_calls` (`RepositorioDeSombra.registrar_chamada`): provedor `jev`, origem
  `decisao_fechada`, papel `decisao_fechada`, `usd` declarado e `step_id` NULL; a falha também (`ok=0`, motivo fechado).
  A fatia de US$ 0,50 deixa de ser cega e o saldo estimado da TypeSafe passa a andar.
- O 422 do adaptador ganhou classe própria (`ProviderRejected`); para o retrieval segue a mesma falha.
- Ligado só por `ai.decisao_fechada.decisor: jev` (de fábrica, `nulo`). O envio continua fechado por
  `JEV_RUNTIME_SEND_APPROVED` até o 31.10.
- Prova: `simulated` (`backend/tests/test_decisao_fechada_jev.py`: MockTransport, socket proibido, hub falso e banco de
  teste). Chamada real ao Jev: `not_run`.

## 2026-10-03 — Correção do 31.9, rodada C (NO-GO em 8e1d7a9c): `sem_destinos` sem normalizar, C7 em qualquer escrita e e-mail soletrado (branch fix/31-9-privacidade)

- A rodada C (`.claude/handoffs/reverificacao-31-9c.md` §7) achou 27 vazamentos fora da suíte de 240, pelo caminho de
  produção. Uma correção por causa:
  - `TargetExtractor.extrair` recorta sempre do original: com "ﬁ", "ß" ou acento decomposto, devolvia o texto em
    minúsculas e sem acento, e a chave `AKIA…` e o endereço em inglês passavam. O prefixo de token também é conferido sem
    caixa, com o comprimento de verdade;
  - letra fora do alfabeto latino em qualquer palavra recusa (decisão da orquestradora: a regra vale para a frase), com a
    lista de palavras-chave em outras escritas e idiomas;
  - palavra-chave colada ("novasenha"), abreviada (`pw`, `psw`) e em leet com 5, 7 e 8;
  - o par "login: x / y" e "usuário x, acesso y" sem verbo de entrar;
  - e-mail soletrado com "at", "chez" ou "bei", o ponto por extenso em outras línguas, qualquer domínio de topo com o
    ponto e o provedor conhecido sem domínio.
- Importantes na mesma entrega (decisões da orquestradora): numerais por extenso só recusam seguidos; `@handle` com hífen
  vira `[usuario]` inteiro; PIN tecla a tecla e "2580#" são C7.
- Prova `simulated`: `tests/test_decisao_fechada_reverificacao_c.py`; harness da rodada D (267 casos, por `sem_destinos`):
  0 vazamentos (eram 27), 0 C7 ou e-mail sem recusa (eram 27). Comandos reais de 7 dias (92, só leitura): 1 recusa, a
  mesma C7 de antes.

## 2026-10-03 — Correção do 31.9, reverificação B (NO-GO em 97f35fac): C7 recusa o pedido inteiro, duas passadas no filtro e o motivo da recusa na sombra (branch fix/31-9-privacidade)

- A reverificação B da orquestradora (240 casos novos, 3 céticos; `.claude/handoffs/reverificacao-31-9b.md`) achou 47
  vazamentos de portão em 97f35fac. Correções do §7:
  - `_MISTO` antes de `_NUMERO`, com hífen;
  - eufemismos, pergunta de segurança e frase de recuperação;
  - leet, palavra invertida, separadores e bidi;
  - mais idiomas;
  - e-mail ofuscado, endereço em inglês, caixa postal, cartão e CVV com número.
- Decisões da orquestradora:
  - (a) C7 recusa o pedido inteiro, inclusive o código pedido pela quantidade de dígitos;
  - (c) a regra dos dois numerais fica (0 recusas nos 90 comandos reais de 7 dias);
  - (d) placa e nome com cidade passam.
- `decisao_fechada_sombra.motivo_privacidade` (migração 079, trazida da 080 do RA-10): `c7_*` ou o motivo do filtro, por
  `PedidoDeDecisao.motivo_privacidade` → `RegistroDeDecisao` → `RepositorioDeSombra.registrar`. O `fallback_reason` e
  `validar` não mudam.
- Contrato do 30.25: `RunService._dados_da_sombra` vira `dados_da_intencao` (mesma assinatura), e o catálogo da cadeia
  vira `AppState.catalogo_da_cadeia`, compartilhado pela sombra e pelo rótulo de intenção.
- Prova `simulated`:
  - `test_decisao_fechada_reverificacao_b.py` (73 testes);
  - portão local com o harness da orquestradora copiado: 260 casos, 0 vazamentos (eram 45), 0 passagens indevidas (eram 56).
  - Real: not_run (o envio continua fechado no código).

## 2026-10-03 — Correção do 31.9: filtro sensato da C3 (ADR-069 item 10), desligamento limpo e estratos do golden set (RA-2; branch fix/31-9-privacidade)

- **ADR-069 item 10** (dono, 03/10 00:15Z, relatado pela orquestradora): dado pessoal pode ir ao Jev "desde que faça sentido
  no filtro".
  - A C3 e o nome ou `@handle` de pessoa podem sair; cai a remoção que falha fechada.
  - O piso: C7 nunca; e-mail e telefone completos viram marcador; e-mail ofuscado, numeral ditado e documento recusam.
  - O D-J5 vira só "o Jev não decide por persona".
  - A primeira redação deste item, no mesmo branch, dizia que nome e handle de terceiro ficavam fora; foi corrigida.
- **C3** (`decisao_fechada/entidades.py`): filtro SENSATO, uma lista de bloqueio sobre o piso.
  - Normalização NFKC (homóglifo, largura cheia, invisível); alfabetos misturados recusam.
  - Viram marcador: aspas (a que sobra leva o resto), link, e-mail, `@handle`, telefone, número, palavra com `_` e símbolo.
  - Recusam: endereço, documento, e-mail ofuscado e numeral ditado, também em EN e ES.
  - O resto passa, nome inclusive.
  - A lista de permissão da primeira versão do branch caiu. Nos 90 comandos reais de 7 dias, as 17 recusas que não eram C7
    vinham todas da regra de proporção, e a lista apagava cerca de 8 palavras por comando no qa-messenger.
- **C2 e C7**: `mascarar_catalogo` nas opções da R2, antes do corte em 200, com as mesmas máscaras de forma. A C7 é
  reconhecida em qualquer formato (`menciona_c7`).
- **Portão** (`simulated`): no `ataque.py` da reverificação, zero vazamento de C7, e-mail e telefone em 109 casos. O
  "escreva para ali no gmail" sai com nome e provedor, sem endereço.
- **Real** (OBSERVED, só leitura, contagens, central 01351e66):
  - nos 90 comandos de 7 dias, 89 sairiam; a recusa que sobra é C7, e nenhum comando que sairia tem e-mail ou telefone;
  - as 26 opções do catálogo não têm `@`, dígito, e-mail nem telefone.
- **Parte B** (I1, I4, M1 a M4, corrida do provedor):
  - o `stop()` espera o que grava sombra antes do `db.close`, com prazo único de 6 s;
  - o aviso de transparência diz o que sai: "o comando do dono filtrado (e-mail, telefone, @handle, link e número
    mascarados; nome fica)";
  - a triagem respeita o envio fechado;
  - a retenção pula quando a porta está desligada;
  - o curador lê o provedor por resposta.
- **I2**: a triagem do curador não casa a decisão real na hora. O relatório do 31.10 lê `learning_reviews` (`validade='ok'`,
  `simulated=0`) por `decisao_real_da_triagem`.
- **RA-2**:
  - migração 079 (`decisao_fechada_sombra.ambiguos`: etapas `AMBIGUOUS` da RESOLVE por execução);
  - golden set com o 1º estrato da intenção em qa-messenger e o GO do Instagram sem data, decidido no relatório do 1º estrato;
  - teto da R2 com dois denominadores: 4/31 cadastrados e 3/25 ativos, o principal;
  - rótulo humano só pelo parecer do 30.17;
  - métrica principal do 31.10: "execuções sem fluxo que o Jev teria casado ao fluxo que o desfecho confirma".
- `JEV_RUNTIME_SEND_APPROVED` continua `False`. Nenhuma chamada real.

## 2026-10-03 — Aprendizado: nomes também nas listas (validação do deploy 4, branch fix/aprendizado-ux-deploy4)

- "capability" sai da tela: "capacidade", "Etapa livre (fora do catálogo)" e "Fora do catálogo".
- As entradas do livro ganham `app_nome` e `etapa` (adendo v0.80). "App:", a Identidade e a Versão dizem o nome do app,
  com o pacote no `title`.
- A receita de app sem catálogo ganha nome pelo título da etapa de origem: "Digitar a mensagem · etapa fill_message
  (v1)" no lugar de "fill_message (v1)".
- A ocorrência de falha diz "android-05 · etapa open_app · tentativa 1".
- Prova `simulated`: `tests/test_learning_capability_na_linha.py` e os testes do painel do aprendizado.

## 2026-10-03 — Aprendizado: decisões do dono de 03/10 no desenho (docs)

- Documentação e processo: `docs/design/aprendizado-vivo.md` registra que quem valida fluxo é a IA (curador; A pela regra, B
  no lote do dono, C item a item) e não o dono à mão, e que o `commit` em app sem catálogo fica na classe B (§8.4 e §15).

## 2026-10-03 — 30.24: "Confirmar que fica" para o legado de Revisar (branch feat/30-24-confirmar-que-fica)

- O legado publicado com efeito de "Revisar" ganha o gesto da pessoa que o mantém. A trilha grava uma linha
  `published → published` com quem, quando e o motivo, que é opcional. O item sai da fila até chegar evidência
  contrária real depois da confirmação; confirmar de novo o tira outra vez. O item não muda e não há migração.
- Fecha a pendência A6 do 30.17: aceitar o parecer "manter" num item de "Revisar" é a mesma confirmação, ligada à
  revisão.
- Rota `POST /api/aprendizado/{kind}/{ref}/confirmar` (adendo v0.78). No painel, "Confirmar que fica" e
  "Confirmar selecionados" em "Revisar", e "Confirmado que fica" no histórico. O item já decidido por uma pessoa perde
  o aviso "vale revisar" (antes ele ficava mesmo depois de religado), e o que voltou diz por quê. Percorrido no
  navegador numa cópia do banco do central, inclusive a 375 px.
- Prova `simulated`: `tests/test_learning_confirmar_que_fica.py` (10), `AprendizadoPage.test.tsx` e
  `DetalheRico.test.tsx`. Real: `not_run`.

## 2026-10-03 — Aprendizado: o dossiê do fluxo diz os apps (branch fix/dossie-do-fluxo-apps)

- O conteúdo legível do fluxo (detalhe do Livro e dossiê do curador) ganha `app` (o principal do plano), `apps` (os
  exigidos, de `flow_required_apps`) e o `app` de cada etapa. Antes, um fluxo que atravessa apps (12.1) parecia rodar
  todo no app principal, e o curador podia julgar "ler no Outlook" num fluxo do Instagram como incoerente.
- O `dossie_hash` dos fluxos muda: um fluxo já revisado volta a ser elegível para o curador uma vez. O `comando_modelo`
  (texto da pessoa) segue fora do dossiê.
- Prova `simulated`: `tests/test_learning_conteudo.py` e `tests/test_learning_curador_dominio.py`.

## 2026-10-03 — 12.5: bancada do leitor, o portão que reprovou (`scripts/bancada-leitor.py`)

- **`real`.** Foram 16 recortes guardados e 2 leitores reais (gpt-6-luna e gemini-3.1-flash-lite), com 128 pares cada e zero
  concordância falsa. Mesmo assim houve 0/32 concordâncias nos verdadeiros: a prévia do corpo, sempre cortada em "…", leva
  todo par a `truncado`.
- Sem o truncado da linha alheia (só diagnóstico), seriam 30/32 e 32/32. O custo total estimado foi de US$ 0,02
  (`ai_calls` 2944–3007).
- `ai.leitura_visual.enabled` fica false. A correção do escopo do "truncado" espera decisão. Detalhe em `docs/ia.md` §17.

## 2026-10-03 — 30.25: o rótulo de intenção, "Qual era o pedido?" (branch feat/30-25-rotulo-de-intencao)

- A execução real e comprovada que a cadeia de resolução não casou com nenhuma habilidade (ou deixou num empate) vira
  uma pergunta cega à pessoa: qual habilidade do catálogo era aquela intenção, ou nenhuma. É o gabarito do decisor
  fechado da intenção (31.x, da Jev). Minerador no digest da execução assentada, sem gancho novo no taskqueue; uma
  linha por execução em `learning_reviews` (`template_id='intencao'`, provedor vazio, só ids no dossiê).
- Os leitores do curador filtram `template_id='curador'`: o rótulo, de custo zero, não entra no orçamento (C_W).
- Rotas `GET /api/aprendizado/intencao` e `POST /api/aprendizado/execucao/{run_id}/intencao` (adendo v0.76).
  No painel, "Qual era o pedido?" no fim de Para aprovar, opcional e fora da contagem: uma pergunta por
  vez, com "Pular", e as opções em até duas linhas (percorrido no navegador numa cópia do banco, inclusive a 375 px).
- Núcleo: `state.py` compõe o rótulo com o mesmo catálogo da sombra do 31.9 (`catalogo_da_cadeia`). Entra na suíte 7,
  depois do `fix/31-9-privacidade`, que torna público o acessor `dados_da_intencao`.
- Prova `simulated`: `tests/test_learning_rotulo_de_intencao.py` (28) e `IntencaoSecao.test.tsx` (10). Real: `not_run`.

## 2026-10-03 — Aprendizado: `commit` sem catálogo volta para a classe B (branch fix/commit-sem-catalogo-b)

- O dono confirmou em 03/10 a decisão de 02/10: receita ou fluxo com `commit` num app SEM catálogo
  (`commit_sem_catalogo`) é classe **B**, aprovado em lote. A emenda para C da mesma madrugada (PR #127) foi revertida
  no código (`domain/politica_de_risco.py`), no doc do domínio e no §8.4 do desenho. O aviso de espera volta à faixa B.
- Prova `simulated`: `test_learning_politica_de_risco.py` e `test_learning_espera.py`.

## 2026-10-03 — RA-20 (29.40), fatia A: a causa do "ausente" medida e a herança da receita provada (branch feat/ra-20-causa-do-ausente)

- 52 % das consultas de receita davam "ausente" (reavaliação de 03/10), quase nenhuma por falta de receita: a chave
  exige versão, assinatura e variante, e a receita viva noutra chave não casava. Agora a chave sem receita herda, como
  CANDIDATA, a receita `active` da mesma etapa noutra variante, na legada (as 19 de 17/09, sem assinatura nem variante)
  ou noutra versão do app. A herdeira só age depois de concordar em sombra (`recipes_promote_after`); com `commit`, para
  em `validated` (D1). Assinatura diferente nunca doa; a chave esperando o dono ou posta de lado não herda; o veto da
  pessoa vale (`save`). `ai.recipes_heranca: false` desliga a herança e mantém a medida. Só herda com a prova em
  sombra (`recipes_promote_after > 0`): com 0, o modo anterior da suíte de reaproveitamento, a receita aprendida já nasce
  ativa e uma herdeira só tomaria o lugar dela.
- A herdeira que se prova na chave completa e passa a agir (`active`) aposenta a legada ativa da mesma etapa e versão
  (cada parte da chave é a dela ou vazia), com a trilha "provou-se na chave completa": a legada não casava mais
  consulta nenhuma. A ativa de outra assinatura ou de outra variante fica, mesmo com a outra parte vazia (revisão da
  Android). A que espera o dono (`validated`) não aposenta nada.
- A causa de cada "ausente" é contada em `receita.ausente{causa}` (vocabulário do Aprendizado,
  `domain/causa_do_ausente.py`); a consulta que herdou conta `receita.consulta{resultado=herdada}`. Adendo v0.77
  (provisório).
- Fora desta fatia: o hash sem o `post.value` do `model_judged` (fatia B, PR à parte), o seletor do filho rotulado
  (desenho de núcleo com a Android) e os literais do comando no `troca()`. As metas de fill_message e de 65 % dependem
  sobretudo do seletor do filho.
- Prova `simulated`: `test_recipes.py::test_herda_da_versao_anterior` (herda, concorda duas vezes e volta a agir com 0
  decisões do ator), `test_receita_heranca.py` e `test_learning_causa_do_ausente.py`. Real: `not_run`.

## 2026-10-03 — 29.32: a conta retirada some de `memory_items` de todas as personas (branch feat/29-32-memoria-conta-retirada)

- **P13 da reavaliação de 03/10 (opção A do dono):** a retirada reescrevia só a memória da persona que retirava. Agora o @, o id e o e-mail
  da conta viram "[conta removida]" em `memory_items` de **todas** as personas. O e-mail só entra se identifica a conta (conta de e-mail, ou o login
  dela) e nenhuma outra conta VIVA o usa (Outlook da mesma persona ou de outra: o endereço fica). `runs.command` e `actions.args` seguem como histórico.
- `sem_o_rastro` ganhou a mesma fronteira de palavra do `esquecer_conta` do aprendizado (nem `foo@ana.com` nem a parte local `ana@x.com` casam com `ana`).
  `limpezas` da retirada ganha `memory_items_de_outras_personas` (só contagem).
- **Retroativo:** `scripts/memoria-conta-retirada.py` (`--ensaio` por padrão, `--aplicar`, `--lista-stdin` sem eco), fonte = lápides (hash) e ids dos
  eventos `profile.account_retired`; o e-mail de conta já retirada não está no banco e só entra pela lista do operador. **Não rodado no banco real.**
- Revisão adversarial: handle em forma de e-mail só some se exclusivo da conta; @ de conta viva (outro app, outra persona) não se redige; lista do operador recusa item curto ou só de dígitos; `--aplicar` exige `--backup`; `--ensaio` avisa migração divergente.
- Prova `simulated`: `test_memoria_conta_retirada.py` (15). Prova na cópia do backup `20261002-211739` (migrada na cópia, 2 linhas sintéticas plantadas):
  ensaio 2, aplicar 2, repetir 0; a cópia foi apagada. Nas 116 linhas reais da cópia: 0 com rastro por hash ou id (os 37 do central não se reproduzem ali).

## 2026-10-02 — 12.5 nível 1: leitura visual de saída de etapa conferida às cegas (ADR-070, branch feat/12-5-leitura-visual)

- **Por quê.** O passo 0 (`real`, android-01) provou que a linha da caixa do Outlook é cega na árvore (ComposeView sem texto em
  toda a subárvore); sem leitura da imagem o `read_value` não tinha de onde tirar remetente e assunto.
- **O que entra, DESLIGADO (`ai.leitura_visual.enabled: false`).** `read_value(source="visual")` na região que o app declara
  (`leitura_visual.regioes` no `telas.yaml` do Outlook), com 13 barreiras e conferência cega por um segundo leitor: papel novo
  `ai.roles.leitura` (sem herança, outro modelo que `decide`/`escalation`, com visão, sem fallback) e `transcribe(LeituraRequest)
  -> Transcricao` nos três provedores. O recorte vira evidência; `step_outputs` ganha `origem`, `leitor`, `frame_sha256`,
  `evidence_id` (migração 078); valor visual em etapa com efeito espera a pessoa; o juiz recebe a imagem à força.
- **Junto.** `step_blocked.reason` redigido nos quatro destinos; conta própria das recusas da barreira de saídas (4 → `fail_or_retry`).
- **Hub de IA.** `origem='leitura'`, fatia opcional `ai.limits.leitura_max_usd_per_day`, parse estrito da transcrição, teto do
  recorte (nunca a tela inteira), aviso de `/api/ai` com provedor, modelo e apps que declaram a região.
- **Ajustes da revisão (mesmo lote).** Privacidade: a `ValidationError` do parse não encadeia mais (`from None`; o texto do modelo
  não chega ao log com traceback); triagem acusa e-mail de código por forma (número de 4 a 8 dígitos e palavra de código em
  en/pt/es na mesma linha, valor E linha de origem), e na leitura visual o valor com forma de código é recusado sem contexto; a
  triagem visual leva a etapa a `waiting_user` sem nova tentativa do ator; saídas visuais fora das variáveis de receita; o valor
  gravado é o do leitor; `fora_do_app` recebe o valor real; recorte até 0,2 da altura e 320 px. Contrato: o leitor exige modelo
  DECLARADO com visão em `ai.models` e comparado pelo nome normalizado (caixa, `vendor/`, `-AAAAMMDD`); a leitura nunca cai no
  modelo do ator (`modelo_do_papel_leitura`); campo repetido com valores diferentes é `invalid_output`; orçamento, prazo e crédito
  do leitor seguem o desfecho do ator (`desfecho_de_ia`); o aviso de `/api/ai` usa o rótulo do dado do app.
- **Segunda rodada da revisão (D1 a D5).** A forma de código passa a ser lida em NFKC e com dígitos de outros alfabetos
  convertidos, com os separadores `[\s.,·_/-]` e os de largura zero ignorados (`saidas.forma_de_codigo`, `_canonico`); na leitura
  visual, token alfanumérico curto ("G-482913", "ABC123") também é código; o teste de forma vale para o valor e para CADA linha do
  recorte, e a triagem do recorte roda ANTES da conferência. **A triagem da árvore ficou mais restritiva, de propósito:**
  "Your code is 482.913" (ponto ou vírgula no número, dígitos de largura total ou árabes) e código alfanumérico ao lado de
  palavra de código passam a ser recusados. O `AIError` do parse é levantado fora do `except` (sem `__context__`).
- **Docs.** ADR-070, `ia.md` §17, `dominios/execution.md`, `api-contract.md` (adendo), `banco.md` (078), aprendizado K-077, item 12.5.
- Prova `simulated`: `tests/test_leitura_visual.py`, `tests/test_leitura_visual_papel.py`. `real`: `not_run` (bancada do leitor e
  execução no android-01 dependem de ligar a opção e do provedor escolhido).

## 2026-10-03 — 29.31: app de prova no tier 0 do `side_effect_tier` (branch feat/29-31-qa-tier0)

- **Custo (RA-8 da reavaliação de 03/10):** etapa com `side_effect` e SEM capability em app sem catálogo caía no tier 1 ("risco
  desconhecido") e escalava ao modelo forte; em 7 dias, 64 a 66 desses escalonamentos eram do QA Messenger (43 % das chamadas do Opus
  no tier 1, cerca de US$ 0,20 por dia) e distorciam a bateria de prova. Agora `side_effect_tier(step, cap, modo, app_de_prova)` devolve
  tier 0 e motivo vazio quando o app da etapa é `builtin` e tem `apps.category='qa'`, só no `by_risk` e só sem capability. `true` (todo efeito sobe),
  `false`, etapa com capability e app real sem catálogo ficam como estavam; `strong_model_for_side_effect` segue global.
  `AppContext` ganhou `category` (padrão `None`), preenchida em `Scheduler._app_context`.
- **Furo achado no caminho:** `_seed_apps` criava o app `builtin` SEM categoria (só a migração 041 a punha, nas linhas que já
  existiam), então uma instalação nova nunca teria o app de prova como `qa`. O seed agora grava `category='qa'` para `builtin: true`
  (sem migração). Critério por dado: no backup de 02/10, só `qa-messenger` tem `builtin=1` (e `category='qa'`). Revisão adversarial: `category='qa'` sozinho não vale (a API o aceita em qualquer app), o critério é `builtin` E `category='qa'`.
- `simulated`: `tests/test_cost_levers.py` (função pura e caminho real do executor, nos dois sentidos). `not_run` no central; aceite real:
  `decision` "sem catálogo" = 0 no app de prova em 7 dias depois do deploy. Sem migração.

## 2026-10-03 — 14.11: CPU por emulador medida de verdade (branch feat/14-11-cpu-por-emulador)

- **Corrigido (RA-3a da reavaliação de 03/10):** `devices/emulator.py::process_usage` recriava o `psutil.Process` do lançador e dos filhos a cada leitura, e o `cpu_percent(interval=None)` da 1ª leitura de um objeto novo é sempre 0,0; `resources.cpu_percent` de `GET /api/instances` mostrava 0,0 com o emulador gastando 1,1 a 1,2 núcleo. `MedidorDeUso` guarda os objetos por pid do lançador (reaproveita o filho conhecido, acrescenta o novo, descarta o que sumiu; troca tudo se o `create_time` mudar; purga o que não é lido há 60 s; `threading.Lock`). Assinatura mantida: a 1ª leitura de um objeto soma 0,0, da 2ª em diante é o valor real. Prova `simulated`: `backend/tests/test_cpu_por_emulador.py` (9). Prova `real` (leitura, 03/10, central, qemu 524 + emulator 53204): 3 leituras de 10 s, diferença de 0,8, 1,1 e 0,3 ponto contra o Δ de CPU do `Get-Process`. Sem migração; só o central, o agente do notebook herda `devices/` mas não mede CPU por emulador.

## 2026-10-03 — Aprendizado: a exposição da lição mede só o custo da execução (contrato com o 31.14 do Jev) e `commit` sem catálogo vai para a classe C (branch fix/exposicao-custo-da-execucao)

- Emenda de 03/10 da política de risco (30.10, mostrada no painel pelo 30.16; decisão da orquestradora pela regra do
  dono "o mais restritivo"): receita ou fluxo com `commit` num app SEM catálogo (`commit_sem_catalogo`) passa de B para
  C. Efeito desconhecido, com alcance possível em massa: decide-se item a item, nunca em lote, e o aviso de espera
  (`learning.needs_person`) sai na faixa C. Prova `simulated`: `test_learning_politica_de_risco.py` e
  `test_learning_espera.py`.

- `licoes_sql.py::_desfecho_do_plano` conta só as chamadas de `ai_calls` com `origem` `execucao` ou nula: a decisão
  fechada do Jev (31.14) grava o `run_id` com `origem='decisao_fechada'` e não entra no `ai_calls` nem no `usd` da
  exposição do planejador. O `_desfecho_da_etapa` já filtrava por etapa. Prova `simulated`:
  `tests/test_learning_efeito.py::test_a_exposicao_do_planejador_mede_so_o_custo_da_execucao`.

## 2026-10-03 — Aprendizado: nomes no lugar de códigos e o erro de rede traduzido (P2 a P5 da validação do deploy 3, branch fix/aprendizado-ux-deploy3)

- P2: "O que mais falha" diz o motivo e onde, com os nomes do Aprendido ("Pós-condição não comprovada — Instagram ·
  Abrir o feed"); dentro da página do app, só o motivo. O pacote e o código ficam em "Para quem desenvolve". Backend:
  `app_nome` nos grupos (`presentation/nomes.py`, `VisaoPorApp.nomes`).
- P3: a capability com o nome do catálogo na Identidade, no conteúdo da receita e da lição e nos Sinais (`app_nome` e
  `capability_nome` em `GET /api/aprendizado/sinais`). O texto da lição nomeia a capability só na tela; o gravado, que
  vai ao prompt, não muda.
- P4: a receita troca a chave da etapa pelo nome da capability ("Enviar a mensagem (v1)"), e dois itens iguais numa
  lista ganham quando foram aprendidos ou o número (Para aprovar, Revisar, Aprendido, página do app e avisos do lote).
- P5 (mudança GLOBAL, fora do módulo, decidida pela orquestradora): sem resposta HTTP, o estado de erro de todas as
  telas do painel diz "Sem resposta do servidor." no lugar de "Failed to fetch" (`lib/loadError.tsx`; o texto original
  fica no `title`). Adendo v0.74 (provisório) no contrato.

## 2026-10-03 — Aprendizado: o parecer da IA diante da pessoa (30.17, branch feat/30-17-parecer-no-painel)

- Painel: a seção "Parecer da IA" no detalhe do Livro (sugestão, classe, conclusão, o que a IA citou, aceitar ou recusar
  com motivo, pedir revisão, histórico) e, com o curador em `on`, a frase "Parecer da IA: …" na linha da fila e o aceite em
  lote só da classe B. Em `shadow`, o parecer só aparece depois da decisão da pessoa; o detalhe avisa que há um.
- Backend: toda decisão de pessoa pelo Livro rotula o parecer pendente (`aceitou`/`recusou` quando vista; às cegas, o
  rótulo da ação), sem nunca travar a transição; `POST /{kind}/{ref}/parecer/{review_id}`, `POST /{kind}/{ref}/revisao`,
  `review_id` opcional no `/status`; gatilho `pedido_da_pessoa`; sinais `parecer_decidido` e `pediu_revisao`. Adendo
  v0.72 (provisório: quem mergear depois renumera). Nenhuma migração.
- Correção do 30.11: a `variante` da receita (`en-US/xhdpi`) fazia a triagem de credencial recusar 24 de 26 receitas da
  cópia do central (`recusada:triagem`), e o curador nunca revisava receita; agora é chave estrutural.
- O quadro por versão do detalhe cabe em 375 px.
- Prova `simulated`: `tests/test_learning_pareceres.py`, `ParecerDaIA.test.tsx`; bateria afetada (53 arquivos, 919) e
  frontend inteiro (1298); navegador na cópia do banco do central com provedor de ensaio e hub `simulated`, nos modos `on`
  e `shadow`, e os fluxos pendentes do 30.15/30.16. `not_run` no central (curador `off` até o deploy 4).

## 2026-10-03 — Aprendizado: evidência inválida como tipo próprio de desligamento (30.23, branch feat/30-23-evidencia-invalida)

- Ação nova `POST /api/aprendizado/{kind}/{ref}/evidencia-invalida {run_id}`. Desliga a receita ou o fluxo aprendido de
  um sucesso falso, com o motivo estruturado `evidencia_invalida:<run>`; o já desligado ganha a linha que reclassifica o
  motivo. O motivo livre nesse formato é recusado (422).
- O veto desse tipo barra só a mesma execução. Outra execução real que ensine o mesmo faz o item renascer "reaprendido",
  em classe B: espera o dono em "Para aprovar", e o sistema para em `validated` (sombra da receita e do fluxo, e o
  repositório). Relações `reaprende` e `reaprendida_por`.
- A evidência da execução marcada fica à vista (`invalidada`) e sai da saúde, da versão e da sombra do fluxo.
- O dossiê do curador (30.11, que entrou na main pela suíte 5) segue a mesma regra: o reaprendido é B nos fatos de
  risco, e a evidência marcada fica de fora do que a IA pode citar.
- Painel: selo na trilha, aviso na evidência, seção do reaprendido, botão "Marcar evidência inválida" com confirmação no
  lugar, e o motivo em "Para aprovar".
- Gancho em `taskqueue/recipes.py` (`exige_o_dono` na sombra): entra pela suíte 6. Se a leitura do livro falha, a
  candidata não sobe nem ganha motivo na trilha; a próxima concordância pergunta de novo.
- `test_learning_backlog` e `test_learning_repositorio` passam a usar meio-dia fixo. A suíte 5b falhou perto da meia-noite
  UTC porque a semente cruzava o dia.
- Adendo v0.70; emenda ao ADR-054. Prova `simulated`; a marca da 109 e do fluxo no central é `not_run` até o deploy.
- Com o 30.17 (merge da main no branch): a marca rotula o parecer pendente do curador como o `/status` (vista em `on`,
  às cegas fora dele); reclassificar o já desligado não rotula (`tests/test_learning_evidencia_invalida.py`).

## 2026-10-02 — 29.29: D2-a também ao ganhar a conta, com vínculo sem app (branch feat/29-29-d2a, commit cdcb3fe6)

- **Furo (revisão adversarial do 29.27):** pessoa sem conta, vinculada SEM app a aparelho que já tinha o Instagram de outra persona, passava a servir o
  mesmo app ao ganhar a conta (`create_profile` com `persona_id` e sem `instance_id`; `add_account` de outro app), porque `profiles_of_instance`
  conta o vínculo sem app de quem tem conta no app e o vínculo, feito antes, não tinha app a conferir. Agora `SocialRepository.conflito_da_conta_nova`
  confere o aparelho do cadastro e cada aparelho de vínculo sem app da pessoa ANTES de criar qualquer linha (409 `conta_do_app_ja_no_aparelho`,
  "nada foi criado"). Portas mapeadas: `create_profile` e `add_account` tinham o furo; `bind_device`, `_rebind` e vínculo no cadastro já
  passavam por `repo.bind`. `simulated`: `tests/test_d2a_conta_nova_com_vinculo_sem_app.py` (6). `not_run` no central. Sem migração.

## 2026-10-02 — 29.28: contas nossas podem interagir entre si, em ritmo baixo (emenda do ADR-050, branch feat/29-27-limpeza-e-adr050)

- **Decisão do dono de 02/10 (relatada às 23:10Z):** `PolicyEngine._fleet_gate` deixa de recusar todo alvo que é conta nossa. Conta RETIRADA (lápide) segue recusada,
  sem `retry_at`; conta nossa VIVA passa pelas demais regras (política do perfil, aprovação, tetos, uma conta por alvo do ADR-055) e por um
  espaçamento mínimo desde o último gesto com efeito DESTA conta (maior entre `limits.fleet_min_spacing_to_own_account_s`, padrão 600, e o
  cooldown do perfil), com `retry_at`. Config nova em `LimitsCfg` e `config/config.example.yaml`. Nenhum outro ponto bloqueava (varredura).
  `simulated`: `tests/test_interacao_entre_contas_nossas.py` (7); `test_conta_bloqueada_sai.py` atualizado (2 testes). `not_run` no central.

## 2026-10-02 — 29.27: retirada de conta bloqueada limpa o app nos aparelhos (branch feat/29-27-limpeza-e-adr050)

- **Retirada leva os dados do app embora (emenda do ADR-068, decisão do dono de 02/10, relatada às 23:10Z):** o `app.yaml` ganhou `limpar_ao_retirar` (verdadeiro no
  Instagram; `AppDefinition.clear_on_account_retire`). Retirada de conta (gatilho ou rota `retire`) de app que declara isso faz, em tarefa de fundo, um
  aparelho por vez, onde a conta estava logada (marcadores abertos + vínculo, capturados antes de a retirada mascarar o @; a sessão NÃO é pista): acorda se
  hibernado/parado, captura de tela, `pm clear` SÓ do pacote declarado (comando `session.logout` em `run_device_job`), captura de tela, resolve a
  quarentena com a nota "limpeza automática autorizada pelo dono em 02/10" e devolve a energia. Falha: quarentena aberta e evento `device.account_cleanup`
  em erro, sem repetir. Sem retroativo na subida; sem o @ em evento ou log. Resposta de `retire` ganha `limpeza_dos_aparelhos` (adendo v0.68).
  `resolver_conta_travada` aceita `marcadores`. Sem migração. `simulated`: `tests/test_limpeza_ao_retirar.py` (19); `not_run` no central.
- **Correção da revisão adversarial (defeitos que bloqueavam o merge):** o `pm clear` podia apagar o Instagram de OUTRA persona viva. (1) A sessão
  deixa de ser fonte de aparelho (`unbind` não a apaga; `wrong_account`/`needs_person` não dizem "esta conta está aqui"). (2) Trava `outra_conta`
  dentro do trabalho do aparelho, logo antes do `clear_data`: recusa se há vínculo (inclusive o sem app de persona com conta do app, o furo da
  D2-a), sessão em qualquer status ou marcador de outra conta do mesmo app (`SocialRepository.outra_conta_no_aparelho`); quarentena aberta e
  evento de erro. (3) Trava de energia: só acorda com `confirm_locked_account` se todo marcador aberto do aparelho é do pedido. **Risco residual a
  aceitar pelo dono:** conta logada no app fora da plataforma (seletor de contas do Instagram) seria apagada. O furo da D2-a em `create_profile`
  segue como item separado.

## 2026-10-02 — Emenda do ADR-069: a chave TypeSafe não é trocada (decisão do dono, item 9)

- O dono mantém a chave atual (como no ADR-017 com a Anthropic): cai a condição do item 7 e da D-J3. A prova real em sombra
  (31.10/31.11) depende só dos tetos (`fatia_jev` de US$ 0,50/dia e o teto total registrado) e segue a ordem combinada: suíte 5,
  merges, deploy 3, envio liberado por classe (C0 a C2 primeiro; C3 só com o 31.9 mergeado) e a sombra real numa janela sem
  suíte. Ninguém lê nem imprime a chave; `JEV_RUNTIME_SEND_APPROVED` continua `False` até o 31.10.
- Textos que citavam a troca como condição: `decisao_fechada/privacidade.py`, `porta.py`, `docs/ia.md`, `docs/plano-100.md`
  e `docs/design/jev-golden-set.md`. Sem mudança de comportamento.

## 2026-10-03 — Aprendizado: "Como mudar o modo deste app" legível (30.20, branch fix/30-20-como-mudar)

- O passo a passo diz o arquivo (`config/config.yaml`), mostra o trecho do bloco `aprendizado:` com o modo que vale hoje,
  explica cada valor (`on`, `shadow`/`observe`, `off`) e manda reiniciar a tarefa `farm-central`. Antes era uma chave
  pontilhada e "off, shadow ou on" sem explicação.
- Os adendos v0.63 e v0.64 deixam de ser provisórios (regra nova: quem mergeia usa o próximo número livre da main).
- A ideia de editar o modo pelo painel fica registrada no §8.10 do desenho, com as duas formas e o conflito com o §8.10.

## 2026-10-02 — Aprendizado: interface do modo por app de lições e telas (30.20, branch feat/30-20-modo-por-app-painel)

- O detalhe do app mostra "Lições e telas neste app": o modo que vale, "definido para este app" ou "segue o global", o que
  o modo faz e, em "Como mudar", a chave `aprendizado.<tipo>.por_app.<pacote>` com o aviso de que é preciso reiniciar o
  central. O cartão do app mostra só o modo próprio; o Global lista as exceções com link para o app.
- API (adendo v0.64, provisório): `modos_do_app` em cada app de `/apps` e `/apps/{pacote}`; `licoes_por_app` e
  `telas_por_app` em `modos`. Só leitura: o painel não grava o config.
- Prova `simulated`: `tests/test_learning_modo_por_app.py`, `test_learning_apps.py`; `AplicativosTab.test.tsx`.
  Navegador: preview com cópia do banco do central e overrides só no config do ensaio.

## 2026-10-02 — 29.26: texto livre fora da query string e persona sem foto sem requisição (branch feat/29-26-texto-fora-da-query)

- **Privacidade (varredura da classe, adendo v0.66):** `GET /api/runs/distribution?command=` virou `POST /api/runs/distribution`
  (corpo `{count, app_id?, command?}`; o GET responde 405 `metodo_removido`). A varredura de todas as rotas `GET`/`DELETE` com parâmetro de query achou mais
  duas com texto livre: `GET /instagram/profiles/{id}/context` (`content` é a mensagem recebida; virou `POST` com `{counterparty?, thread_key?, content?}`) e a busca `q`
  de `GET /api/pedidos` (procura no título e no objetivo; virou `POST /api/pedidos/busca`, e `GET` com `q` na URL responde 422 `busca_no_corpo`). Ficaram na URL, por serem
  id, status, enum, paginação ou nome curto de app/capability/handle: `/diagnostics`, `/ai/balances`, `/apps-overview`, `/apps/{id}/overview`, `/training`, `/desempenho`,
  `/usage`, `/capabilities`, `/releases`, `/store`, `/app-state`, `/approvals`, `/commands`, `/runs`, `/runs/{id}/events`, `/instances/{id}/frame`, `/instagram/profiles/{id}/{memory,interactions,
  policy,runs,auth-attempts,operational-context}`, `/instagram/policy-groups`, `/personas/{id}/devices/{iid}` (DELETE `app_id`), `/falhas`, `/aprendizado/sinais`, `/licoes/previa`, `/export`,
  `/voz/previa`, `/preferencias/sugestoes`, `/skills`, `/teaching-sessions`, as listas de `/pedidos/*` (sem `q`) e `/avisos`. Quebra só para o painel do mesmo commit (`api.previewDistribution`, `apiPedidos.listar`).
- **Avatar:** persona sem foto fazia `GET /instagram/profiles/{id}/avatar` e recebia 404 (cinco erros no console). `has_avatar` entrou no `PersonaDTO`, no `PersonaOnDeviceDTO` e em `profiles[]`
  do contexto operacional (só adição); `profileAvatarUrl(id, temFoto)` devolve `undefined` sem foto e o `Avatar` mostra as iniciais, sem `<img>`.
- Prova `simulated`: `test_distribuicao_pelo_comando.py`, `test_limites_por_servidor.py`, `test_social_memory.py`, `test_pedidos_api.py`, `test_persona_imagens.py`, `test_personas_aparelhos_api.py`,
  `Avatar.test.tsx`, `ProfilesPage.test.tsx`, `CommandPanel.test.tsx`, `PedidosPage.test.tsx`, `app.integration.test.tsx`. Real: `not_run` (o console sem 404 de `/avatar` precisa ser visto no Chrome).

## 2026-10-02 — 29.25: persona sem @ no grupo de acesso, cabeçalho do cartão no celular e `flows/match` em POST (branch feat/29-25-ux-personas)

- **B3:** o grupo de acesso mostra a persona cuja conta saiu (29.23) como "Beatriz Rocha · sem conta" (discreto, tracejado), e não como um chip "@" vazio; o
  `aria-label` e as opções do diálogo seguem a mesma regra (`rotuloDaConta`, em `pessoa.ts`). `members[]` do grupo ganha `name` no backend (só adição).
- **I6:** em tela estreita (≤720 px) o `CardHeader` (`components/ui.module.css`, a mesma regra do 28.12; serve a `PageSection` e a toda guia) reserva ao texto no mínimo 12rem; a ação fica no canto quando cabe e desce para a linha de baixo, à direita, quando não cabe;
  antes o texto ficava com ~1/3 da linha ao lado de "Adicionar conta" e o título quebrava no meio da palavra. Título com `overflow-wrap: normal`.
- **Privacidade:** `GET /api/flows/match?command=` virou `POST /api/flows/match` com corpo `{command}` (máx. 4000): o rascunho, às vezes com e-mail, não vai mais
  para a query string nem para o log de acesso. Quebra só para o painel do mesmo commit (adendo v0.65). Pendente: `GET /api/runs/distribution?command=` tem o mesmo vazamento.
- **Editor do grupo:** o membro sem conta some da listagem de perfis (29.23), e o diálogo contava 2 mas mostrava 1, sem como tirá-lo do grupo;
  agora ele aparece como "Nome · sem conta" e pode ser desmarcado (achado na validação no navegador).
- Prova `simulated`: `test_intencao_chamadores.py` (+1: GET 405, corpo validado), `test_grupos_de_acesso.py` (+1), `ProfilesPage.test.tsx` (+2);
  painel 1243/1243. Validado no navegador contra backend simulado do worktree (8766, IA simulada; a 8000 não foi tocada): chip e editor do grupo,
  cabeçalho a 375 e 1280 px, `POST /api/flows/match` sem query string; capturas em `data/ux-validacao/2026-10-02-29-25/` (fora do Git). Real: `not_run`.

## 2026-10-02 — 29.24: rota para resolver a quarentena e aviso sem o @ de conta retirada (branch feat/29-24-resolver-quarentena)

- `POST /api/instances/{id}/locked-account/resolve` (corpo `{nota}` obrigatória; 404 `no_locked_account` sem marcador aberto): só banco, resolve o
  marcador de quarentena, sincroniza o rótulo e emite `device.locked_account` "resolvido". Nunca automática. Adendo v0.61 em `api-contract.md`.
- O @ de conta retirada (29.23) sai do produto vivo: `SocialRepository.mascarar_contas_retiradas()` (na retirada e na subida, sem migração) troca
  por `[conta removida]` o `handle` dos marcadores abertos e o rótulo derivado do aparelho; os avisos (frase da quarentena, recusa, start confirmado,
  anúncio e saída do evento, problem do `/health`) dizem "conta retirada (bloqueada)". Conta viva continua com o @; evento antigo fica (ADR-068, item 10).
- Núcleo tocado: `api.py`, `state.py` (texto da quarentena e do health), `commands/despacho.py`, `social/` (`contas_nossas`, `repository`, `service`), `models.py` (re-exporta o corpo).
  Prova `simulated`: `test_resolver_quarentena.py` (8), `test_conta_bloqueada_sai.py` (asserção do marcador agora `[conta removida]`). Real: `not_run`.

## 2026-10-02 — Aprendizado: textos da validação no Chrome do deploy 2 (branch fix/aprendizado-textos-deploy2)

- **Mesmo fato, mesmo rótulo.** O fluxo publicado e nunca usado há `sem_uso_dias` sai `sem_evidencia`/`nunca_usado`, como a
  receita, em vez de `obsoleto_provavel`/`fluxo_nunca_casado` (motivo removido; `domain/saude.py`, adendo v0.63 provisório). O
  texto passa a "Nunca usado desde que foi publicado, há N dias (prazo: 14 dias)", sem o "(limite: 14)" que parecia contradição.
  Na Atenção do central (b5baf3e5), os 4 fluxos que estavam em "provavelmente obsoleto" só por isso passam a "sem evidência"
  (11 itens antes e depois: 4 degradando, 7 sem evidência).
- **O grupo diz o nome, não o código.** `capability_nome` em cada linha do Livro, de `/apps/{pacote}` e em cada grupo de
  `/falhas`: o `title` do catálogo do app sem as lacunas (`OPEN_PROFILE` → "Abrir o perfil"; porta `TitulosDoCatalogo`,
  adaptador `TitulosDoRegistro`). O painel mostra o nome nos grupos do Aprendido e nas falhas, com o código no `title`; sem
  catálogo, o código em mono como antes.
- **Título curto na Atenção.** O fluxo, cujo título é o comando inteiro, aparece cortado na palavra (80 caracteres) com o texto
  inteiro no `title` (`resumirTitulo`).
- Prova `simulated`: `tests/test_learning_capability_na_linha.py`, `test_learning_obsolescencia.py`,
  `test_learning_rotas_falhas.py`; `SaudeDoApp.test.tsx`, `DetalheRico.test.tsx`. Navegador: preview com cópia do banco do central.

## 2026-10-02 — Gatilhos de evento, condição e persona nos pedidos (28.8, branch feat/28-8-gatilhos-evento)

- `PedidoCorpo` aceita `evento`, `condicao` e `persona` (adendo v0.67 do contrato; antes, `gatilho_nao_suportado`). Os
  códigos novos são `gatilho_invalido` e `condicao_sem_observacao`, e a persona com `intervalo_min_s` abaixo do piso dá
  `frequencia_abaixo_do_piso`.
- **Evento:** cursor `ev:<events.id>`, com a linha de base na ativação e na retomada `daqui`.
  - Lê só eventos com mais de 5 s (ordem de COMMIT no PostgreSQL) e respeita o piso da autonomia.
  - Ignora `pedido.*` e as execuções do próprio pedido.
  - Faz uma ocorrência por volta, sem texto do evento.
  - Buraco da retenção: registra (memória `pendencia`) e não dispara.
- **Persona:** a primeira visita é na ativação. A seguinte vem quando a anterior fecha, depois da saída
  `proxima_visita_s` presa a [mínimo, máximo], ou do máximo.
- **Condição:** avaliada por borda sobre as observações; grava a memória `descoberta` e não cria ocorrência.
- Passado `fim_em`, evento e persona encerram o pedido.
- Migração `076_pedido_avisos_gatilhos`: o CHECK de `pedido_avisos.tipo` ganha `eventos_perdidos` e `condicao_atendida`.
  No SQLite a tabela é reconstruída (molde da 047, sem perder aviso); no PostgreSQL, DROP/ADD da constraint. O buraco e
  a condição atendida viram aviso gravado.
- **Prova `simulated`:** `backend/tests/test_pedidos_gatilhos_dinamicos.py` (23 testes) e os de pedidos e arquitetura; no painel, as mensagens de `gatilho_invalido` e
  `condicao_sem_observacao`. PostgreSQL: `not_run`.
  No navegador, a lista, o filtro "Quando acontecer" e as ocorrências "Por evento" e "Por persona" foram conferidos a
  1366 e 375 px contra o backend simulado. `real`: `not_run`.

## 2026-10-02 — 8.3: tentativa real de responder comentário (roteiro final), `not_run` sem gesto público

- Central `b5baf3e5` (073). Linha de base: 0 aprovações pendentes; `REPLY_COMMENT`/`CREATE_COMMENT` em `approval_required` nos três perfis vivos.
- android-03: verify `session_ready`; coleta `r-20261002221213-8d1c0a` com `OPEN_PROFILE` comprovado pela árvore local (**real**) e `OPEN_POST` parado por grade vazia.
  android-06 fora (sem rede medida pelo produto e pelo ping); android-01 fora (verify com timeout do UiAutomator). US$ 0,15.
- `REPLY_COMMENT`, `edit` em aparelho, `learn_from` de comentário e `for_each` com itens reais seguem `not_run`; registro em `docs/relatorio-validacao.md` §8.9 e no estado do 8.3.

## 2026-10-02 — Aprendizado: achados da validação no Chrome do deploy 1 (branch feat/aprendizado-ux-deploy1)

- **B2 (versão):** já estava corrigida pelo #105 (e9de6697), implantado em b5baf3e5. Medido no central (só leitura, 02/10 ~22:05Z): 160 itens, nenhum `versao_aposentada`, Atenção = 11. A validação pegou um deploy anterior.
- **Links (I1):** o `href` do item sai com a aba (`#/aprendizado?aba=aprendido&item=…`, adendo v0.59). O painel aceita `item` sem aba, então os avisos antigos também abrem o item. "Revisar" das Pendências leva ao item.
- **Atenção (I2):** um bloco recolhível por rótulo (degradando, provavelmente obsoleto, sem evidência), filtro por app no Global, 10 por bloco com "Mostrar todos". Fila longa abre só o bloco mais grave.
- **Abas no celular (I3):** a faixa rola até a aba ativa vinda de um link (`components/Tabs.tsx`, vale para todo o painel; rola a faixa, não a página).
- **Jargão (I4, I5 antiga):** em "O que mais falha", o erro cru do provedor vira frase ("Saldo da conta de IA esgotado…"). O backlog vira "Correção: aberto". A tentativa vira "android-05 · open_app · tentativa 1". A tendência vira "36 na semana anterior → 11 nesta". O id do grupo, o "Onde alterar", os erros originais e o "Copiar para sessão" ficam recolhidos em "Para quem desenvolve".
- **Evidência (I5):** na receita, a linha diz "Reproduções: X deram certo · Y falharam", diferente de "Evidência registrada" no detalhe. A medida passa a "Base medida: N registros (reproduções e evidências)". A linha "Versão do app: sem dado" da saúde some quando a seção Versão está na tela.
- **Para aprovar (B3):** ao lado de Aprovar/Rejeitar, "O que faz" (passos em frase, com o passo de efeito marcado), "Aprendida" (aparelho, execução e quando) e "Versão do app".
- **Outros:**
  - **I6:** item publicado que ainda espera o dono passa a dizer "Publicado antes da regra de aprovação (…): vale revisar". O selo "Só na loja" vira "Sem declaração".
  - **Sinais:** separador por dia.
- **Prova:**
  - **simulated:** vitest 1249/1249, typecheck e `test_learning_espera.py`.
  - **Navegador, simulado** (backend 8766 com cópia nova do banco do central, 02/10 ~22:20Z): Global com Atenção em 3 blocos (593 px; antes, ~6.700 px) e filtro por app (7 de 11); link antigo sem aba abrindo a receita 73; Para aprovar com o diferencial de 108 × 106; Rejeitar (106); aprovar em lote (108); desligar em lote no Revisar (22); filtros do Aprendido (fluxo + Instagram = 12); janela das falhas (16 → 8 grupos); bloco "Para quem desenvolve" com o Copiar; "Ver no catálogo Aprendido"; estado de erro com o backend parado e "Tentar de novo" recuperando; celular 375 px com a aba Sinais visível e sem rolagem horizontal.
  - **Não exercitado:** o balde "não resolvido", que não existe nos dados; está coberto pelo vitest.

## 2026-10-02 — Pedidos: data prevista na lista e sinal do laço desligado (28.12, ajustes de API; branch feat/28-12-sinais-da-lista)

- Achados da validação do deploy 2 na tela Pedidos (só painel, mais o texto do motivo de nova tentativa): erro na primeira carga mostra só o aviso (sem "0 pedidos" nem estado vazio); a prévia mostra persona e app pelo nome, avisa o que Observar/Preparar significam e põe o objetivo (com as quebras) depois dos cartões, recolhido; cabeçalho de cartão sem espremer o texto abaixo de 720 px; leitura em português dos seletores de data; barra de filtros some sem pedidos; botão no link velho; título longo do detalhe quebra; Pendências sem frase repetida; avisos e personas pedidos juntos viram uma leitura só; o motivo "nova tentativa a partir de 02/10 22:17 UTC" (a coluna `terminada_em` segue ISO). Na validação no navegador (02/10, backend simulado, 1366/600/386/375 px): o título que a pessoa deu aparece inteiro no detalhe (o corte em 90 só vale para o derivado do objetivo), o objetivo do Resumo mantém as quebras, o X do cabeçalho de cartão volta ao canto em 375–386 px (base do texto 12rem) e "Carregar mais antigas" só aparece quando o detalhe veio cheio (20). Prova `simulated` (vitest 1273, pytest de pedidos 458); navegador percorrido (lista, filtros, Avisos, erro e nova tentativa, novo pedido, datas, prévia nas três autonomias, detalhe e abas, link velho, Pendências).
- `PedidoView.proxima_prevista`: sem `proxima_em` (laço desligado ou ainda sem gerar) e com agenda, a lista traz a 1ª data
  CALCULADA pelos gatilhos; a linha mostra "prevista … (pela agenda)" em vez de "próxima data ainda não calculada".
- `laco: {ligado}` na lista e no detalhe (`pedidos.enabled` desta instalação). Desligado, a tela mostra o aviso
  "O laço de pedidos está desligado nesta instalação" no topo e o detalhe diz "prevista pela agenda; o laço está desligado".
- Validado no navegador contra o backend simulado isolado (8765/5188), em 1366 e 375 px, com o laço desligado e ligado
  (ligado: o laço grava `proxima_em`, a prevista some e o aviso também). Testes: `test_pedidos_api.py` (novo caso),
  `PedidosPage.test.tsx` (2 novos); pedidos 467 e frontend 1245 verdes. Real: `not_run` (a prova real do 28.12 segue).

## 2026-10-02 — Origem das chamadas de IA e rubrica única de gasto (31.2 e 31.6, branch feat/31-2-origem-rubrica)

- Migração 073 (`ai_calls.origem`, `ai_calls.ref`, índice `(origem, ts)`); as 071 e 072 estão em outros branches e entram antes, e a lacuna de número é tolerada pelo executor.
- `Usage.origem`/`ref`: o hub (`RoutingProvider._call`) preenche e `add_usage` grava. Vocabulário fechado `ORIGENS_DE_IA`: `execucao` (padrão com `run_id`), `ensino`, `orquestracao`, `assistente`, `social`, `persona`, `curador`, `decisao_fechada`. Linhas antigas ficam NULL.
- `costs.spent_usd` e `spent_today_usd` ganham o filtro `origem=`.
- `AIError.motivo` fechado (`saldo | dia | fatia_curador | fatia_jev | execucao | pedido`), só com `kind="budget"`; `_budget` e o orçamento do pedido (28.6) passam a informar o motivo, sem mudar mensagem nem ordem. Fatias dentro do teto do dia, por origem: `ai.limits.curador_max_usd_per_day` (padrão 0,10 × teto do dia) e `ai.limits.jev_max_usd_per_day` (padrão US$ 0,50, D-J3 a confirmar). O saldo da conta (ADR-051) não muda nesta etapa.
- Prova `simulated`: `backend/tests/test_origem_e_rubrica_de_ia.py` (13). Real: `not_run`.

## 2026-10-02 — Triagem do curador do Livro em sombra no Jev (31.8, branch feat/31-8-curador-sombra)

- `planning/decisao_fechada/curador.py`: `CuradorComTriagemEmSombra` devolve o parecer do curador principal intacto e manda
  o item à porta em `shadow` (`choice` manter/revisar/rebaixar/descartar/nenhuma); `CAMPOS_POR_ORIGEM["curador"]` com os
  campos C0 (metadados e contagens), só lição e receita (F1).
- A decisão real casada é o parecer do curador (concordância, não acerto); sem GO até os limiares do 31.7.
- Prova `simulated`: `backend/tests/test_decisao_fechada_curador.py`. Envio ao Jev continua fechado no código; real `not_run`.

## 2026-10-02 — Sombra da intenção no Jev: R2 e R3 fora da cadeia, com C3 por lista de permissão (31.9, branch feat/31-9-intencao-sombra)

- `planning/decisao_fechada/entidades.py`: `remover_entidades(texto, *, vocabulario=()) -> str | None`, função pura por LISTA DE
  PERMISSÃO: só sai palavra do vocabulário comum de comandos ou do catálogo do dono; o resto vira `[termo]` (em qualquer caixa), todo
  número vira `[numero]`, e endereço, e-mail ofuscado, algarismos por extenso ou excesso de palavras desconhecidas devolvem `None`
  (o pedido não sai, `fallback_reason='privacidade'`). Correção da revisão independente: a versão por detector deixava passar nome em
  minúsculas, nome no começo de frase e os destinos que o TargetExtractor não pega.
- `planning/decisao_fechada/intencao.py` e `taskqueue/sombra_intencao.py`: consumidor de sombra da origem `intencao` (C3, sempre
  `shadow`) com R2 (`choice` sobre o catálogo inteiro, ids opacos, até 254 + `nenhuma`) e R3 (`choice` entre os empatados), numa
  chamada. C7 em prosa (senha, código, 2FA, captcha…) marca `credencial` e a porta recusa o pedido inteiro. Desligado (inclusive com
  `JEV_RUNTIME_SEND_APPROVED=False`) não lê, não resolve e não grava nada. Só a R2 tem decisão real; a R3 e o `casar_desfecho`
  ficam para o 31.10. `CAMPOS_POR_ORIGEM["intencao"]` registra `comando` e `app`.
- `planning/decisao_fechada/porta.py`: `consultar(pedido, *, ao_registrar=None)`, chamado depois de o observador gravar (o casamento
  da decisão real sem polling).
- Enxerto mínimo em `taskqueue/service.py` (um `add_done_callback` em `_spawn_planning`; só plano bem-sucedido: cancelado, com
  exceção ou recusado não vira sombra; a leitura da execução vai para a thread) e fiação em `state.py` (o `stop()` espera as sombras
  antes de fechar o banco). `intent_ports.py` e `intent_resolver.py` não mudam.
- Suíte 5: a lista de permissão fixa não tem mais nome de app (`instagram`, `outlook`, `chrome`…). A catraca do ADR-052
  (`test_apps_fora_do_nucleo.py`) recusa texto de app no código; o nome do app sai pelo id do app e pelo catálogo
  (`vocabulario_de`), e sem eles vira `[termo]`. A mudança só restringe: custa utilidade, nunca privacidade.
- Prova: `simulated` (`backend/tests/test_decisao_fechada_intencao.py`, `DecisorFalso`, com os vazamentos medidos pela revisão como
  testes negativos). `JEV_RUNTIME_SEND_APPROVED` continua `False`; chamada real ao Jev: `not_run`.

## 2026-10-02 — Sombra da porta `DecisaoFechada`: registro, preço, livro-caixa e transparência (31.5, branch feat/31-5-sombra-registro)

- Migração 074 (`decisao_fechada_sombra` e `decisao_fechada_diario`): uma linha por pergunta respondida ou por fallback, só ids opacos e
  categorias (nunca o estado enviado nem o texto das opções); agregado diário durável (concordância, acima do limiar, aceite errado,
  fallbacks à parte, US$, p95); retenção própria `ai.decisao_fechada.retencao_dias` (180) com agregação antes de purgar, no padrão da 055.
- `planning/decisao_fechada/sombra.py`: `RepositorioDeSombra`, `observador_de_sombra` (ligado à porta em `AppState`) e
  `casar_decisao_real`/`casar_desfecho` por `ref` ou `step_id` para o 31.8 e o 31.9.
- `ai.prices` ganha `jev-1.13.0` (US$ 0,042/M de entrada, saída 0); `costs` já soma o `usd` declarado. A conta `typesafe` entra em
  `/api/ai/balances` sem âncora, sem leitura nem rede.
- `GET /api/ai`: com o Jev em `shadow` ou `on`, o `notice` nomeia a TypeSafe e as classes que podem sair e o bloco `decisao_fechada` lista o
  Jev; a chave aparece só como configurada ou não. Envio continua FECHADO (`JEV_RUNTIME_SEND_APPROVED = False`).
- Prova `simulated`: `backend/tests/test_decisao_fechada_sombra.py` (DecisorFalso, relógio falso). Chamada real à TypeSafe: `not_run`.

## 2026-10-02 — Porta `DecisaoFechada` no hub e trava de 255 opções no Jev (31.1 e 31.4, branch feat/31-4-decisao-fechada)

- 31.1: o `choice` do adaptador Jev (`modules/context_retrieval/adapters/jev.py`) recusa localmente, antes de montar o corpo, mais de 255 opções
  (contando a `nenhuma`; novo `ProviderOptionLimit`) e sempre leva a opção `nenhuma`, de id opaco, que nunca vira arquivo ou região.
- 31.4: nova porta `backend/app/planning/decisao_fechada/` (ADR-069), **desligada e sem decisor real**:
  - contrato puro (`PedidoDeDecisao`, `Pergunta`, `RespostaDeDecisao`, vocabulários fechados), `DecisorNulo` (padrão) e `DecisorFalso`;
  - `Privacidade.validar` falha fechada antes de qualquer corpo: `JEV_RUNTIME_SEND_APPROVED = False`, `JEV_ALLOWED_CLASSES` com o teto do
    ADR-069 (C0 a C3; a C3 só na origem `intencao` e só em `shadow`), C7 e social/persona recusam o pedido inteiro, `redact` em toda string;
  - modos `off` (padrão), `shadow` (fora do caminho crítico) e `on` por consumidor; timeout de 1 s e 5 s, sem retentativa, fallback fechado
    que nunca conta como acerto; fan-out de um estado e N perguntas em uma chamada;
  - config `ai.decisao_fechada` (`enabled: false`, `consumidores`, `classes_permitidas`; o YAML só restringe), no `config.example.yaml`.
- Teste de cliente único: só o adaptador de retrieval contém o host da TypeSafe.
- Prova `simulated`: `backend/tests/test_decisao_fechada.py` (40), `test_context_retrieval_semantic.py` (+3). Real: `not_run` (nenhuma chamada ao Jev).

## 2026-10-02 — 12.4: etapa que declara saídas só é comprovada com elas (branch fix/12-4-saidas-obrigatorias)

- `backend/app/taskqueue/executor.py`: `saidas_exigidas` (o que o planejador escolheu em `steps.saidas` ou, sem escolha, o que a
  ação declara em `Capability.saidas`) passa a valer em `run_step` e `_run_step`, no lugar de só `steps.saidas`. Achado real:
  r-20261002204347-8c3f6e, Outlook no android-01, `OPEN_MAIL_INBOX` com a caixa aberta, "1 de 1 com sucesso comprovado" e nenhum
  remetente nem assunto: o plano não escolheu saída, então nada foi exigido e a verificação comprovou a tela. Agora a etapa lê
  (`read_value`) ou falha com o nome que faltou; com efeito disparado seria `uncertain`. Coleta sem item continua falha, salvo
  vazio comprovado pela tela (`_prova_de_vazio`, julgamento "a lista está vazia" explícito), marcado em `StepResult.vazio_comprovado`.
  Mensagem de `step_done` sem leitura passa a citar os nomes. Núcleo tocado: `taskqueue/executor.py` e `models.py` (um campo em
  `StepResult`); não toca `scheduler.py`, `state.py`, eventos, config, api, `planning/` nem pedidos.
- Prova `simulated`: `tests/test_saidas_obrigatorias.py` (9 casos: caso real reconstruído, saída faltando, saídas lidas, subconjunto
  do planejador, navegação pura, vazio comprovado, lista à vista, julgamento em dúvida). Real: `not_run`.
- Docs: `docs/dominios/execution.md` (Saídas obrigatórias), `docs/conhecimento/aprendizados.md` (K-075).

## 2026-10-02 — Aprendizado: passe de design do painel (branch feat/aprendizado-passe-de-design)

- Regra do dono (layout bom, UX, todos os fluxos validados no navegador). Detalhe do app na ordem do que pede ação: Atenção → Aprendido → O que falha → Declarado → Absorvido. O Aprendido ganha o nível Capability: um bloco recolhível por capability com contagem, "pede(m) atenção" e saúde no resumo (abre sozinho o bloco com item em atenção); fluxo (comando inteiro) e item sem capability em blocos próprios; sem o campo (backend anterior), agrupa por tipo. "Como é usado" vira chips por tipo; Declarado vira grade compacta.
- Item do Livro: sem o pacote repetido dentro da página do app; título longo (comando de fluxo) em duas linhas; "Último uso" com o dia (`formatQuando`: "hoje, 20:47", "29/09 20:47"; antes só a hora, que fazia um uso de três dias atrás parecer de hoje); "Nunca usado" no lugar de "Usos: 0"; "sombra 0/0" some e "sombra a/b" vira "na sombra, concordou com a IA em a de b"; "Espera o dono" em faixa própria; referência discreta. Sinais: "pelo painel"/"pelo sistema" e data. Detalhe: "Evidência registrada" explica a diferença para os usos da linha.
- Prova: `simulated` (vitest 1243/1243; typecheck) e navegador contra backend simulado com cópia do banco (porta 8766, 02/10 ~21:55Z). **Exercitado** (clique e resultado): Global → Ver o app → detalhe do Instagram com 11 blocos de capability; Atenção → Abrir o item → detalhe rico da receita 73; desligar → reativar da receita 25 com motivo e trilha; 375 px sem rolagem horizontal. **Só visto** (tela lida, sem interação): Para aprovar, Aprendido, O que mais falha, Sinais. **Ainda não no navegador** (cobertos só pelo vitest): Aprovar/Rejeitar e aprovar em lote, Desligar em lote do Revisar, filtros do Aprendido, janela/camada e "Copiar para sessão" das falhas, "Ver no catálogo Aprendido", o balde não resolvido e os estados vazio/carregando/erro.

## 2026-10-02 — Aprendizado: `capability` em cada linha do Livro e de `/apps/{pacote}` (branch feat/aprendizado-capability-na-linha)

- A lista do Livro (`/api/aprendizado`, `/pendentes`, `/revisar`), as linhas de `/apps/{pacote}` e o `item` do detalhe trazem `capability` (`"<nome>"` ou `null`), para a hierarquia App → Capability → Item do painel. Receita: a derivação do detalhe, em lote (`FontesSql.capabilities_das_receitas`); lição e tela: `escopo.capability` (sem vazio nem `*`); fluxo, habilidade e memória: `null`. Sem migração; adendo v0.58 do contrato. Prova `simulated` (`test_learning_capability_na_linha.py`).

## 2026-10-02 — Caixa de avisos dos pedidos e coerência tela × API (28.9, branch jev/integ-28-9)

- **Passe de design e UX da tela Pedidos (28.9, só `frontend/`, prova `simulated`).** Lista: cartão estruturado (chips de estado e autonomia, agenda "Todo dia às 19:00 · próxima sex 02/10 19:00", gasto e ocorrências como metadados discretos; fuso só se difere do navegador; US$ no formato brasileiro), esqueleto no formato do cartão, erro com "Tentar de novo" e o vazio com **Novo pedido** (leva ao Comando com o painel aberto). Detalhe: título curto (o objetivo inteiro fica no Resumo) e Resumo em cartões (Agenda, Quem faz, Custos e limites, Comportamento, Autoria); a próxima data, quando o laço ainda não a gerou, é a primeira das calculadas. Painel "Repetir ou acompanhar" em seções (Quando, O que conta como feito, Limites, Avançado recolhido), prévia em blocos com datas legíveis, e a pergunta real do Automático ("marque aparelhos no modo Manual ou escolha uma persona") no lugar do erro genérico. Avisos com seletor segmentado e "Marcar todos" só com não lidos. Guias sem a barra vertical solta (`.tabs`, vale para todas as telas). Pendências: o pedido diz o motivo (ocorrência incerta). Testes: `PedidosPage.test.tsx`, `NovoPedido.test.tsx`, `formato.test.ts`, `pedidosNaCaixa.test.ts`.
- Migração `072_pedido_avisos`: tabela `pedido_avisos` (CHECK nos nove tipos, `chave_dedupe` UNIQUE, `lido_em`, cascata com o pedido). Todo `pedido.aviso` (do laço 28.5/28.6/28.7 e da API) passa por `CaixaDeAvisos.registrar`: grava com `ON CONFLICT DO NOTHING` e só então emite, e só se a linha é nova; o evento em dobro da pausa (laço + API) acabou.
- `GET /api/pedidos/avisos` lê da tabela (`lido=`, `requer_pessoa`, `pedido_id`); novo `POST /api/pedidos/avisos/ler` (ids, ou `todos` com `pedido_id` opcional; idempotente; 404 em id inexistente). `avisos_nao_lidos` real na lista, no detalhe e no snapshot (só informativos), e `pede_atencao=1` inclui o pedido com aviso não lido.
- Pontos de emissão novos: `orcamento_80` (laço, uma vez até o teto subir), `orcamento_esgotado` (substitui o `encerramento` quando o motivo é orçamento) e `relatorio_pronto` (relatório de encerramento ou de período, depois do commit). `aprovacao_pendente` e `pergunta` seguem sem emissor.
- Painel: o selo do menu vem de `nao_lidos`; "distribuir por contas" fica barrado no pedido persistente (o backend recusa `alvos.distribute`). Divergências registradas no adendo v0.45.
- Prova `simulated`: `backend/tests/test_pedidos_avisos.py` (+ ajustes em `test_pedidos_retentativa.py` e `test_pedidos_tentativas.py`), `frontend/src/features/pedidos/PedidosPage.test.tsx`; `real` e PostgreSQL `not_run`.

## 2026-10-02 — Tentativas, efeito e pausa dos pedidos (28.5, branch feat/28-5-tentativas-efeito)

- Nova tentativa por ocorrência: `modules/pedidos/domain/tentativas.py` (puro) decide, a partir do desfecho da execução, entre repetir (a MESMA linha volta `falhou → devida`, `tentativa+1`, despacho `chave:t<n>` depois de um atraso exponencial com teto), `incerta` ou falha definitiva. Só repete sem ação com `effect_possible` (e sem execução purgada) e sem etapa `uncertain` (a regra de `_reconciliar`); falha COM efeito possível (ou execução purgada) fecha `incerta` e leva o pedido a `aguardando_pessoa`, nunca `falhou` (decisão do coordenador: um `falhou` deixaria a próxima ocorrência refazer o efeito); `max_tentativas` é o total por ocorrência (padrão 2). Sem migração: o instante da espera mora em `terminada_em` enquanto a ocorrência é `devida` com `tentativa > 0`. A repetição passa por sobreposição, orçamento e saldo.
- `incerta` leva o pedido `ativo` a `aguardando_pessoa` (mesma transação) e nunca repete sozinha; N falhas seguidas (coluna `pausa_por_falha`, padrão 3) pausam o pedido (ator `sistema`). Os dois emitem `pedido.aviso` (`AvisoDTO` do contrato 28.9) no barramento (`state.py`), que o 28.11 já assina.
- Configuração: `pedidos.max_tentativas`, `falhas_para_pausar` (padrões globais; a coluna do pedido vale), `retentativa_base_s`, `retentativa_teto_s`.
- 28.6, acréscimos: a ocorrência adiada por saldo além da janela vira `perdida` com o motivo do saldo (antes ficava `devida` para sempre); o limite conhecido do orçamento (excesso máximo = o custo de UMA ocorrência aberta, limitado pelo teto da execução) está escrito em `docs/design/pedidos-laco.md` §11 e no Adendo v0.45 de `docs/api-contract.md` e fixado em teste.
- Prova `simulated`: `backend/tests/test_pedidos_tentativas.py`, `test_pedidos_retentativa.py`, `test_pedidos_orcamento.py`; `real` e PostgreSQL `not_run`. Detalhe em `docs/design/pedidos-laco.md` §13.

## 2026-10-02 — Aprendizado: a revisão do curador ligada à chamada medida (migração 075, branch feat/075-revisoes-ai-call-id)

- `learning_reviews.ai_call_id` (075, nulável, sem FK): a revisão grava a linha de `ai_calls` que o hub mediu (30.12). O `usd` medido NÃO é gravado ainda (pendência da rubrica no `hub-de-ia-fora-de-execucao.md`); `usd = 0` segue = não medido, e o orçamento segue estimando pelo tamanho do dossiê.
- `RespostaDeRevisao.simulado` (opcional): o simulado da RESPOSTA vale sobre o do adaptador; parecer simulado nunca avisa o dono. Seguro também com revisões concorrentes.
- Em cima do 30.12 (feat/30-12-curador-hub, que está sobre o 30.11): merge na ordem 30.11 → 30.12 → 075, na suíte combinada. Prova `simulated`: `test_migracao_075_revisoes_ai_call_id.py`, `test_learning_curador.py` (dois testes novos), `test_migracao_069_revisoes.py`.

## 2026-10-02 — Curador do Livro pelo hub de IA (30.12, branch feat/30-12-curador-hub)

- `AIRouter.review_knowledge(PedidoDeParecer)`: papel `plan` emprestado, sem execução, `origem = curador` e `ref = dossie_hash`
  em `ai_calls`; a fatia do curador (31.6) corta ali com `AIError(kind="budget", motivo="fatia_curador")`.
- Template e esquema do hub em `planning/curador.py` (`VERSAO_DO_TEMPLATE = curador-v1`): os enums da resposta saem das opções
  fechadas do aprendizado; o hub só garante o objeto JSON, e quem valida o parecer é `validar_saida`. Anthropic e
  OpenAI-compatível respondem pelo modelo do `plan`; o simulado, por regra fixa.
- `modules/learning/infrastructure/curador_do_hub.py::CuradorDoHub`, ligado pelo `AppState` no lugar do simulado: ponte de thread
  para o laço do processo, `AIError` → `RecusaDoProvedor(kind)`, `usd` e `ai_call_id` MEDIDOS na linha de `ai_calls`. O modo do
  curador continua `off` de fábrica.
- Prova `simulated`: `backend/tests/test_curador_do_hub.py` (14). Chamada real: `not_run`.

## 2026-10-02 — Conta bloqueada sai na hora e a persona fica (29.23, ADR-068, branch feat/29-23-conta-bloqueada-sai)

- Bloqueio confirmado retira a conta numa transação: credencial da conta, a legada e o ciphertext do cofre, sessões, vínculo de aparelho e a linha da conta (inclusive a âncora); a persona volta a `active`, sem @. `POST /api/instagram/profiles/{id}/accounts/{conta}/retire`; gatilho em `marcar_conta_travada`; o disjuntor de conta (ADR-055) é acionado direto.
- Migração 071 `contas_retiradas`: lápide só com o hash do @; `eh_conta_nossa()` e o filtro de frota recusam ação sobre conta nossa (ADR-050). `memory_items` reescritos para "[conta removida]"; gancho `limpezas_ao_retirar` para outros módulos. Histórico intacto (opção A do dono).
- Testes: `test_conta_bloqueada_sai.py` (16); asserções de bloqueio em `test_detector_conta_travada`, `test_quarentena_de_conta`, `test_escopo_do_desafio` e `test_sessao_declarada` atualizadas de propósito (o bloqueio agora retira a conta e devolve a persona a `active`). Prova `simulated`; real e PostgreSQL `not_run`.
- Retirada AUTOMÁTICA só no Instagram (conta âncora) e só com sinal forte: `ChallengeActivity` em foco (`DeviceManager.observe` lê o foco quando a árvore já parece conta travada) ou declaração do dono. Texto sozinho e conta de outro app ficam `blocked`/marcadas para a pessoa; a rota manual retira qualquer conta. Testes: só texto não retira, atividade retira, dois sinais retiram, outro app não retira sozinho, responder a terceiro num post nosso segue permitido no `_fleet_gate` (29.23, ADR-068).

## 2026-10-03 — Aprendizado: versão viva comparada no formato da receita (fix, branch fix/aprendizado-versao-nome-codigo)

- A receita grava `app_version` como `nome(código)` (`447.0.0.55.81(385311929)`), e `fontes.vivas` agrupava só
  `device_app_state.observed_version_name`: TODA receita aparecia como "versão fora do parque" e a saúde (30.4/30.14) marcava
  78 de 160 itens `obsoleto_provavel` numa cópia do banco do central. Agora as vivas saem no formato da receita
  (`domain/versao.py::versao_canonica`) e a tela e a lição, que gravam só o nome, comparam pelo nome (`nome_da_versao`).
  Depois: 4 `obsoleto_provavel` (fluxos nunca casados). Achado no aceite visual com backend simulado sobre uma cópia do banco.
  Prova `simulated` (`tests/test_learning_versao.py`, 3 casos novos com os formatos medidos); `not_run` no central. K-076.

## 2026-10-02 — `learning.needs_person` no aviso fora do painel (28.14, branch feat/28-14-needs-person-aviso)

- O aviso externo do 28.11 (Telegram) assina o evento do Livro (30.21), conforme o combinado com a frente Aprendizado em 02/10:
  - só a ENTRADA na espera avisa;
  - a saída, e a saída sem a entrada correspondente depois de reinício, é no-op;
  - só a faixa C avisa por padrão (`avisos.aprendizado_faixas`, novo; a B é aprovação em lote e fica na caixa);
  - a mensagem leva o título fixo e o link `#/pendencias`, nunca kind, ref, app ou motivo.
- Chave de deduplicação comum aos eventos que avisam: `chave_do_fato(família, …)`. Famílias e formatos:
  - `approval:{id}`;
  - `run:{id}:needs_input`;
  - `session:{evento}` (era `evento:{id}`);
  - `pedido:{id}` (era `pedido-aviso:{id}`);
  - `learning:{kind}:{ref}:{desde}`.
  A troca de nome é inofensiva: o laço só lê eventos novos, a partir de `last_id`.
- Prova `simulated`: `backend/tests/test_avisos_aprendizado.py` (6), mais os testes de avisos vizinhos. Real: `not_run`.

## 2026-10-02 — 30.13: falhas com diagnóstico determinístico (branch feat/30-13-falhas-diagnostico)

- `modules/learning/domain/diagnostico.py` (novo, puro), `domain/vocabulario.py` (`CausaProvavel`; `TipoDeProposta` ganha `rebaixar_receita`, `revisar_licao`,
  `reaprender_tela`, `ajustar_catalogo`, `investigar`), `domain/backlog.py` (`Proposta` com `alvo`, `causa`, `parent_id`), `infrastructure/contexto_sql.py` (novo) e
  `relatorio_sql.py`: cada grupo de falha sai com conhecimento envolvido (refs citáveis, `aproximado`), causa provável determinística com fatos e proposta
  estruturada; a proposta vira linha do backlog com `parent_id` = o grupo. Sem IA, sem migração; a causa `indeterminada` é dado para o curador (30.11).
- Teto de IA pelo tipo: `ai_calls.error_kind` (`AIError.kind`) vence o texto na leitura (`classificar_pelo_tipo_da_ia`); o texto fica como legado. Achado: o teto do
  pedido caía em `outro`. Gravar o kind em `attempts` (`failure_kind` em `finish_attempt`) é do taskqueue e não foi feito.
- `api-contract` adendo v0.56 (`diagnostico`, `alvo`/`causa`/`parent_id` nas propostas). `tests/test_learning_diagnostico.py` (45, `simulated`); em
  `test_learning_backlog.py` uma contagem passou a filtrar `category='falha'` (a curadoria agora também grava propostas do diagnóstico).

## 2026-10-02 — Aprendizado: curador por IA, aplicação com adaptador simulado (30.11, branch feat/30-11-curador-aplicacao)

- Porta `CuradorDeIA` (`PedidoDeRevisao`/`RespostaDeRevisao`, combinados com a frente Jev) e adaptador SIMULADO determinístico; laço
  próprio `aprendizado.curador.intervalo_s` sob a trava de líder `curadoria`, separado do `PassoDeCuradoria`; modos `off` (padrão) /
  `shadow` / `on` (= `shadow` nesta fatia). Gatilhos, filtros (hash, cooldown, orçamento, prioridade) e o orçamento proporcional
  aprovado (`B_W = min(α·G_W, k·N_W·c̄)`, α 0,10, k 1,5, W 7 dias, `c_max = 4 × mediana`) em `domain/orcamento_do_curador.py`; corte
  com motivo próprio `orcamento_da_janela`. Revisões em `learning_reviews` (069) com `usd = 0` = não medido (o custo é do 30.12);
  `learning.needs_person` com `motivo: parecer_da_ia` (`api-contract.md`, adendo v0.57). A IA nunca decide.
- Config `aprendizado.curador` (núcleo: `config.py`, `CuradorCfg`) e uma linha em `state.py`. Sem migração, sem IA paga. Prova
  `simulated` (`tests/test_learning_curador.py`); `not_run` no central.

## 2026-10-02 — 29.22: tráfego verificado não atravessa boot novo (branch fix/29-22-boot-invalida-verificacao)

- `backend/app/devices/rede.py`: `inicio_do_boot` e `boot_depois_da_medicao`; `verificacao_invalida` ganha o motivo `boot`. O
  marco é `instances.emulator_started_at` (boot a frio e acordar do snapshot, sobrevive ao restart do central) ou, no aparelho
  de worker, `online_since_mono`. Achado: android-05 ficou `trafego_verificado` pela medição #134 (19:20) através do boot a frio
  de 19:50, e a porta liberou a tarefa às 19:52 com o aparelho sem DNS. Sem coluna nova, sem migração.
- `backend/app/devices/rede_convergencia.py`: frase de espera do `boot`; ao ligar e na varredura a convergência mede (antes só
  conferia). `backend/app/devices/manager.py`: `readotar_depois_do_worker` renova `online_since_mono` quando o agente conclui
  um boot novo. Não toca `taskqueue/`, `state.py` nem `scheduler.py`.
- Decisão: wake quente também invalida (uma medição a mais por acordar); a linha não regride de estado, a porta é que não aceita.
- Prova `simulated`: `tests/test_rede_portao.py::test_verificacao_nao_atravessa_um_boot_novo` e `::test_boot_sem_processo_local_usa_a_entrada_no_ar_e_politica_livre_nao_tem_efeito`; `tests/test_hierarquia_sessao_morta.py` (marco do worker). Real: `not_run` (parar e ligar a frio um aparelho `exigida`).
- Docs: `docs/dominios/parque.md`, `docs/plano-100.md` (item novo 29.22), `docs/conhecimento/aprendizados.md` (K-073).

## 2026-10-02 — Tentativas, efeito e pausa dos pedidos (28.5, branch feat/28-5-tentativas-efeito)

## 2026-10-02 — API de pedidos: prévia, criação idempotente, ações, leitura, eventos e snapshot (28.9, branch feat/28-9-rotas)

- Rotas `/api/pedidos` (Adendo v0.45 de `docs/api-contract.md`): `POST /previa` (sem efeito e sem IA), `POST` (criação com selo e idempotência), `GET` (filtros, `total_por_estado`), `GET /{id}` (detalhe), `PATCH /{id}` (compare-and-set por `versao`, `dry_run`, selo), `ativar`, `pausar`, `retomar`, `cancelar`, `ocorrencias`, `execucoes`, `relatorios`, `observacoes` e `GET /avisos`. `executar`, `backfill` e `POST /avisos/ler` ficam fora (ver o adendo).
- Domínio puro `modules/pedidos/domain/previa.py`: bloqueios (piso de frequência, sobreposição, limites, fuso, recorrência, gatilho não suportado), selo SHA-256, id determinístico (`uuid5` inteiro da chave em base64, 26 caracteres), custo sem número inventado. Serviço em `infrastructure/servico.py`; DTOs e router em `presentation/`.
- Piso de frequência configurável (`pedidos.piso_observar_s` 900, `pedidos.piso_agir_s` 3600).
- Eventos `pedido.updated`, `pedido.ocorrencia.updated` e `pedido.aviso`: o repositório anota as mudanças (`marcar`, por thread) e o laço (`notificar`) e as ações as publicam depois do commit.
- `GET /api/snapshot` ganha `pedidos` (`por_estado`, `avisos_nao_lidos`, `aguardando_pessoa` com as `pendencias` agrupadas); `RunSummary` ganha `pedido_id` e `ocorrencia_id`. `AcoesDePedidos.editar` passa a aceitar autonomia, alvos, fuso, orçamentos e demais campos validados pela API.
- Prova `simulated`: `tests/test_pedidos_api.py` (14). `real`: `not_run` (o laço segue desligado de fábrica).

## 2026-10-02 — Aprendizado: obsolescência e rebaixamento `catalogo_sem_efeito` (30.14, branch feat/30-14-obsolescencia)

- `saude.rotulo` ganha `obsoleto_provavel` (depois de `degradando`, antes de `sem_evidencia`, só no publicado), com os sinais do §9.2 que têm
  fonte: substituta viva, versão fora do parque, versão viva sem reprodução, efeito sem respaldo no catálogo, fluxo nunca casado e tela
  absorvida (`api-contract.md`, adendo v0.54). Só leitura.
- Passo novo da curadoria, `catalogo_sem_efeito`: receita ou fluxo vivo com `commit` num app cujo catálogo atual não respalda o efeito é
  rebaixado pelo sistema (sempre → `disabled`, para a pessoa poder reativar) pelo caminho do Livro, com o motivo
  `catalogo_sem_efeito:<capability|*>` na trilha. Conservador: app sem catálogo nunca; capability ambígua ou desconhecida só vira sinal.
  Sem IA, sem migração. Prova `simulated` (`tests/test_learning_obsolescencia.py`); `not_run` no central.

## 2026-10-02 — Aprendizado: saúde, falhas, capability e fila Atenção por app no painel (30.15 restante, branch feat/30-15-painel-resto)

- A aba Aplicativos ganha, só no painel (`frontend/src/features/aprendizado/`): a contagem por rótulo de saúde no cartão e no detalhe do
  app ("1 degradando, 2 saudáveis"), a fila **Atenção** (degradando, provavelmente obsoleto e sem evidência, só leitura, com o motivo
  principal em português e o link `?aba=aprendido&item=<tipo>:<ref>`), global abaixo dos cartões e por app no detalhe, e o bloco "O que
  falha" no detalhe do app (as falhas do backlog filtradas por `app`, agrupadas por capability). A saúde é CONTADA da lista do Livro
  (`GET /api/aprendizado`, v0.52), nunca recalculada: `/apps` não traz `saude`; as linhas de `/apps/{pacote}` passam a trazer a `saude`
  da mesma função do Livro (`presentation/apps.py`, `LearningService.saudes`). Falta no backend a `capability` na lista do Livro e em `/apps/{pacote}`
  (só existe em `conteudo.capability` do detalhe do item): o aprendido segue plano e a tela diz por quê; agrupa sozinho se a linha
  passar a trazer `capability`. Prova `simulated` (`frontend/src/features/aprendizado/SaudeDoApp.test.tsx`); `not_run` no central.

## 2026-10-02 — Aprendizado: detalhe rico do item do Livro no painel (30.16, branch feat/30-16-detalhe-rico)

- O detalhe do item (Aprendido, "Detalhes, evidência e trilha") ganha as seções do §11.2 do desenho, só as aplicáveis: Identidade,
  Conteúdo (receita com ações, alvo, NOMES de parâmetro, selo do commit, capability ambígua marcada, origem com link para a execução,
  uso, sombra e versões vizinhas; fluxo, habilidade, lição e tela), Saúde (rótulo, motivos com fato e limiar, dimensões com "sem dado"
  no lugar de zero), Versão do app (quadro por versão), Evidência, Histórico, Relações (links para o item no próprio Livro) e o que
  a pessoa pode fazer. A linha da lista continua enxuta e ganha só o selo de saúde. `?aba=aprendido&item=<tipo>:<ref>` abre o item de
  um link no topo do catálogo. Só painel: lê `conteudo`, `versao`, `saude` e `relacoes` já mandados (adendos v0.50 a v0.53) e não
  recalcula nada. Faltam no contrato `camada_de_uso` e `classe_de_risco` do §11.2: não são exibidas. Prova `simulated`
  (`frontend/src/features/aprendizado/DetalheRico.test.tsx`); `not_run` no central.

## 2026-10-02 — Aprendizado: relações derivadas no detalhe do Livro (30.7, branch feat/30-7-relacoes)

- `GET /api/aprendizado/{kind}/{ref}` ganha `relacoes` (`api-contract.md`, adendo v0.53): `{tipo, kind, ref, rotulo, fonte}` com `substitui`,
  `substituida_por`, `derivado_de`, `absorvida` e `contradiz`, cada uma lida do que já existe (versão vizinha da receita, `parent_version`,
  `parent_id`, `flow:<id>` do fluxo legado, `absorvida:<commit>`, mesmo `scope_key` com `content_hash` diferente e os dois vivos). Sem tabela
  de arestas e sem migração.
- Sem inventar: `contradiz` só em receita (chave exata) e tela (mesmo nome de regra); fluxo, habilidade, lição, voz e preferência ficam sem ele
  (sem critério seguro). `nasceu de`, `complementa / depende de` e `revisado por` ficam fora.
- `domain/relacoes.py` (puro), `FontesSql.sucessoras_da_habilidade` (única leitura nova), `LearningService._relacoes`, `DetalheDoLivro.relacoes`.
  Prova `simulated` (`tests/test_learning_relacoes.py`); `not_run` no central.

## 2026-10-02 — Tela Pedidos no painel (28.9, branch feat/28-9-tela)

- Rotas `#/pedidos` e `#/pedidos/<id>?aba=` (lista com filtros no link, detalhe com Resumo, Ocorrências, Execuções e Memória e relatórios), item **Pedidos** no menu (décimo) com o selo de avisos não lidos, e a guia **Avisos** (`?aba=avisos`, `requer_pessoa=0`). Código em `frontend/src/features/pedidos/`; tipos do adendo v0.45 em `frontend/src/api/pedidos.ts`; `RunSummary` ganha `pedido_id` e `ocorrencia_id` opcionais.
- Ações lidas de `acoes_permitidas`: editar (prévia com `dry_run`, aplicar com a versão e o selo), ativar, pausar (com motivo), retomar (daqui ou recuperar) e cancelar em duas etapas; `200 sem_mudanca` e `409 invalid_state` tratados. Sem botão de executar agora nem de backfill; a agenda não se edita.
- Comando: botão **Repetir ou acompanhar…** abre `NovoPedido` (Quando, autonomia, limites) com a prévia obrigatória (`POST /api/pedidos/previa`) e só então "Confirmar e criar" (`POST /api/pedidos`, selo e `idempotency_key`).
- Pendências: o pedido `aguardando_pessoa` vira a quinta origem da caixa (emenda à ADR-062); a aprovação e a execução parada dele ficam agrupadas sob ele e não contam de novo. `FalhasDeLeitura` ganha `pedidos`.
- Prova `simulated`: `PedidosPage.test.tsx` (18), `NovoPedido.test.tsx` (11), `pedidosNaCaixa.test.ts` (3) e os testes de menu e Pendências ajustados. Contra o backend real e no navegador a 1366 e 375 px: `not_run`.

## 2026-10-02 — Aprendizado: esquecer_conta (29.23, branch feat/29-23-esquecer-conta-aprendizado)

- `app.modules.learning.esquecer_conta(db, *, profile_id, account_id, handle, app_id)` reescreve o rastro textual da conta (handle com e sem `@`, `account_id`) para `[conta removida]` em `learning_items`, `learning_evidence`, `learning_transitions`, `learning_backlog`, `learning_reviews` e `learning_signals`; roda na transação de quem chama (sem commit), não apaga linha nem muda hash/id, é idempotente e casa por fronteira de palavra (handle `ana` não estraga "banana"). Parte Aprendizado do 29.23; a frente Android (`memory_items`, chamada) é outra. Sem migração e sem rota. Prova `simulated`: `backend/tests/test_learning_esquecer_conta.py` (13); PostgreSQL: `not_run`. Detalhe em `docs/dominios/aprendizado.md` ("Conta removida (29.23)").

## 2026-10-02 — Aprendizado: curador, domínio e política de risco (30.10, branch feat/30-10-curador-dominio)

- `domain/politica_de_risco.py` (puro) é a fonte única da classe A/B/C do §8.4 (aprovada pelo dono em 02/10): vale a mais
  restritiva entre o catálogo da etapa e o `commit` do conteúdo; o `commit` numa etapa que o catálogo declara sem efeito (a receita 100
  do Outlook) é C. Na classe A a IA só opina com sobra de orçamento
  (`ia_permitida = so_com_sobra`) e o parecer é só registro; `conferir_aceite` torna impossível a decisão automática por parecer e o lote na C.
  `domain/espera.py::classificar_espera` (30.21) passou a ser tradução dela (mesmo payload do evento; `FatosDoCatalogo` e
  `MotivoDeEntrada` mudaram para lá e são reexportados).
- `domain/curador.py` (puro): dossiê de fatos por lista branca (sem valor de parâmetro, texto digitado, texto de tela ou de pessoa;
  item de sessão sem conteúdo), ids citáveis, `dossie_hash` estável (chave da 069), e validação do contrato de saída do §8.3
  em rótulos fechados (decisão, faixa, causa, riscos, inconsistências, falta; opções prontas para um adaptador de `choice`), com a
  confiança derivada da probabilidade da escolha e a conclusão como único texto livre, opcional (`invalida:<motivo>` em vocabulário
  fechado). Sem rota, sem migração, sem chamada de IA nem prompt. Prova `simulated`
  (`tests/test_learning_politica_de_risco.py`, `tests/test_learning_curador_dominio.py`).

## 2026-10-02 — Aprendizado: saúde do item do Livro (30.4, branch feat/30-4-saude)

- `saude` na lista, em `pendentes`/`revisar` e no detalhe do Livro (`api-contract.md`, adendo v0.52): dimensões medidas (uso, eficácia, base de
  evidência, frescor, contestação; versão e intervenção humana `desconhecidas`), UM rótulo por regra (`inativo`, `em_prova`, `degradando`,
  `sem_evidencia`, `parado`, `pouca_amostra`, `saudavel`, mais `indeterminado` quando falta medida) e `motivos[]` com o fato e o limiar. Regras e
  limiares da proposta D-5 (medidos no banco real); eficácia acumulada, sem janela por item; `obsoleto_provavel` fica para o 30.13.
- `domain/saude.py` puro; `LearningService.saude_de`/`saudes` são a única fonte do cálculo (evidência lida só dos publicados); limiares em
  `aprendizado.saude` (`sem_uso_dias`, `amostra_minima`, `taxa_minima`, `falhas_seguidas`, `contestacao_dias`), via `Ajustes.saude`.
- `EntradaDoLivro.falhas_seguidas` novo (opcional), preenchido de `recipes.consecutive_fail`. Limiares = D-5, aprovada pelo dono em 02/10.
- Sem migração e sem ADR. Prova `simulated`: `backend/tests/test_learning_saude.py` (tabela de casos por rótulo, fronteiras, desconhecida, config, HTTP
  lista = detalhe); `not_run` no central.

## 2026-10-02 — Orçamento, saldo e prioridade dos pedidos (28.6, branch feat/28-6-orcamento-prioridade)

- Custo da ocorrência: o laço soma o custo de `ai_calls` da execução (`costs.spent_usd`, a conta do painel de uso) a `pedido_ocorrencias.custo_usd` no MESMO `UPDATE` do fechamento (acumula entre tentativas; CAS perdido não soma). A retenção (`_purgar_demais_tabelas`) não leva `ai_calls` de execução de ocorrência ainda `despachada`/`rodando`.
- Orçamento total: `modules/pedidos/domain/orcamento.py` (puro). Restante menor que a estimativa (mediana das últimas 5 ocorrências; sem histórico, `orcamento_ocorrencia_usd`) ou `<= 0` pula o que não virou execução (`orçamento: …`) e encerra o pedido com `encerrado_motivo='orcamento'` (espera a execução aberta fechar). Pedido sem orçamento não muda de comportamento.
- Teto por ocorrência na execução: `AIRouter._budget` consulta `Repository.teto_usd_da_execucao` (menor entre o resto do teto da ocorrência e o resto do orçamento total) e barra a chamada com `AIError(kind="budget")` antes de gastar. Sem coluna nova.
- Saldo (ADR-051): `modules/pedidos/infrastructure/saldo.py` lê o mesmo serviço de `GET /api/ai/balances` (sem chamada paga); conta de IA em uso bloqueada, ou abaixo de `pedidos.saldo_minimo_usd` (novo; 0 desliga o mínimo), ADIA o despacho: a ocorrência fica `devida`, sem falha e sem virar `perdida`, com `resumo = "adiada: …"`.
- Prioridade: `dispatchable_objectives` ordena `prioridade DESC, created_at`; `RunService.create`/`create_run` ganham o parâmetro interno `prioridade` (padrão 0, fora do `RunCreate` público). O laço grava 0 (a 067 não deu campo ao pedido).
- Sem migração e sem ADR novo. Prova `simulated`: `backend/tests/test_pedidos_orcamento.py` (25); PostgreSQL e laço ligado no central (28.12): `not_run`. Desenho em `docs/design/pedidos-laco.md` §11.

## 2026-10-02 — Memória, observações e relatório do pedido (28.7, branch feat/28-7-memoria-relatorio)

- Migração **070** `pedidos_memoria` (reservada pela coordenação; a 069 é de outra frente e entra antes): `pedido_memoria` (chave/valor por pedido, versão, tipos do §8.1), `pedido_observacoes` (o que cada ocorrência observou, sem chave estrangeira para a execução: sobrevive à purga) e `pedido_relatorios` (conteúdo JSON determinístico, `sha256`, sequência por pedido, relatório de encerramento único por índice parcial). Só tabelas novas.
- Domínio puro em `modules/pedidos/domain/`: `memoria.py` (versionada, segredo recusado, `compactar` sem IA), `observacao.py` (observado, incerto ou ausente) e `relatorio.py` (observado, conclusão, não coberto; mesmas entradas dão o mesmo relatório; incerteza, falha e ausência nunca viram conclusão).
- O laço registra as observações no fechamento da ocorrência, na mesma transação cercada que a fecha, a partir das saídas lidas entre etapas (056); sem saída estruturada grava o resultado com valor ausente. O relatório sai sob demanda, no encerramento e no cancelamento, e falha dele nunca impede a transição.
- Resumo por IA: só o ponto de extensão (`ResumidorDeRelatorio`, `pedidos.resumo_ia: false`); nenhuma chamada paga.
- Prova `simulated`: `backend/tests/test_pedidos_memoria.py` (23) e `test_pedidos_relatorio.py` (28); a corrida contra PostgreSQL é pulada sem `TEST_DATABASE_URL`. `real`: `not_run` (o 28.12 liga o laço no central). Desenho: `docs/design/pedidos-laco.md` §11.

## 2026-10-02 — 28.11 provado em real: aviso de teste chegou ao Telegram do dono

- Real (02/10 ~20:02Z, central, a1fa730): `scripts/avisos-telegram.py testar` enviou a mensagem de teste e o dono confirmou o recebimento no chat; avisos ligados (`avisos.enabled: true`) desde o reinício das ~20:01Z. Estado do 28.11 pelo mecanismo: `implemented`, `real`. Junto: o 17.8 registrado (`simulated`) e a 2ª tentativa real do 17.12 (android-07, barrada por tela de verificação; segue `not_run`, US$ 0,2373 no total).

## 2026-10-02 — Aprendizado: estado de versão por item (30.6, branch feat/30-6-versao)

- `GET /api/aprendizado/{kind}/{ref}` ganha o campo `versao` (`api-contract.md`, adendo v0.51): o estado de versão do item (§7 do desenho),
  as versões do app vivas no parque (`device_app_state`, só aparelho ativo com o app) e, na receita, uma linha por versão pela chave exata
  (pacote, assinatura, variante, `step_hash`): `comprovado`, `nao_testado` (viva sem receita), `em_prova`, `falhando`, `incompativel`,
  `superseded`, `versao_aposentada`. Tela entra pela regra `sem_casar`; os demais tipos são `independente`. O que não se sabe é
  `desconhecido`, escrito (a receita sem nenhum aparelho observado não vira `comprovado`). Sem migração.
- `domain/versao.py` (puro), `FontesSql.versao`/`vivas`, `DetalheDoLivro.versao`. Prova `simulated` (`tests/test_learning_versao.py`:
  duas versões vivas e receita só na antiga = `nao_testado` na nova); `not_run` no central.

## 2026-10-02 — Aprendizado: conteúdo legível no detalhe do Livro (30.3, branch feat/30-3-conteudo-legivel)

- `GET /api/aprendizado/{kind}/{ref}` ganha o campo `conteudo`: o que a receita, o fluxo, a habilidade, a lição e a tela FAZEM, montado só
  do que já está no banco (sem migração, sem coluna nova; `api-contract.md`, adendo v0.50). Receita: identidade, ações (ferramenta, alvo por
  seletor, `commit`, NOMES dos parâmetros), efeito e qual ação o faz, capability derivada (origem, ou mesmo `step_hash` no app; `ambigua`),
  origem (`execucao`/`treino`/`desconhecida`), uso, sombra e versões vizinhas. Nunca sai valor de parâmetro nem texto digitado; `type_secret`
  e parâmetro sigiloso saem só como `segredo`.
- `domain/conteudo.py` (puro), `FontesSql.conteudo` (leituras de `steps` e das versões vizinhas) e `DetalheDoLivro.conteudo`.
- Prova `simulated`: `tests/test_learning_conteudo.py`. `not_run` no central.

## 2026-10-02 — Teste do servidor de uso único espera o contador em vez de afirmá-lo ao receber a resposta (fix/teste-servidor-de-uma-vez)

- `test_rede_aplicacao.py::test_servidor_de_uma_vez_serve_um_get_so_no_caminho_do_token` falhava às vezes com `-n 8`. A hipótese
  da porta fixa não se sustenta (`ServidorDeUmaVez` já usa a porta 0 efêmera); a corrida era o teste afirmar `entregues == 1` no
  instante em que o cliente recebe o corpo, enquanto o servidor conta DEPOIS de gravá-lo. Agora o teste espera o fato
  (`_ate_o_fato`) e roda também com um atraso de 0,3 s depois do corpo (`_atrasar_depois_do_corpo`), que torna a corrida
  certa para quem não espera; um teste novo confirma que dois servidores ao mesmo tempo não colidem na porta. Só teste e doc;
  `backend/app/` intacto. Aprendizado K-072.

## 2026-10-02 — 29.21: reinício pedido pela rede que nunca sai (retentativa de 30 s e termo que segurou, branch fix/29-21-reinicio-da-rede)

- `backend/app/devices/rede_convergencia.py`: `_pedir_reinicio` diz na linha (`detail`) QUAL termo do "ocupado" segurou o reinício (worker, controle, objetivo, comando exclusivo, estado do aparelho, verbo `restart` ausente ou recusa), uma vez por termo e uma no esgotamento das 24 tentativas, sem ruído a cada 5 s; com objetivo parado em `wait_reason='rede'` neste aparelho o esgotamento retenta em `retentativa_do_reinicio_s` (30 s) em vez de pôr 300 s de mudez (android-05, 02/10: a execução ficou presa 7 min).
- `backend/app/vitrine.py`: `objetivo_que_segura` (o id que `objetivo_em_andamento` conta) e `objetivo_esperando_a_rede`.
- Dívida registrada: o teto `reinicios_max`, `_reinicio_agendado` e `espera_ate` vivem só em memória e zeram no restart do central. Causa exata do "ocupado" no android-05 NÃO provada.
- Prova `simulated`: `tests/test_rede_aplicacao.py::test_reinicio_com_objetivo_esperando_a_rede` (4 casos) e `::test_reinicio_que_nao_sai_diz_qual_termo_segurou`; `tests/test_rede_*.py` e os testes da vitrine passam. Real `not_run`: reproduzir no android-05.
- Docs: `docs/dominios/parque.md`, `docs/plano-100.md` (item novo 29.21), `docs/conhecimento/aprendizados.md` (K-071).

## 2026-10-02 — Papel de IA `persona` para a geração de persona (17.8, branch jev/17-8-papel-persona)

- `backend/app/config.py`: `AI_ROLES` ganha `persona`; `Config.ai_role` resolve `persona` SEM `ai.roles.persona` como o `social` (o bloco dele e, nos perfis do 17.7, a camada `social` e depois a `persona`); com bloco próprio o do `social` não vale para ela. `ROLE_DEFAULTS`, `ai_model_for` e `ai_effort_for` tratam `persona` como `social`: instalação existente não muda sem mexer na configuração.
- `routing.py::generate_persona` chama o papel `persona` (mesmo semáforo do `social` enquanto `ai.roles.persona.concurrency` não for escrito); `anthropic_provider.py`/`openai_provider.py` gravam `role="persona"` no uso. A resposta social da execução segue `social`. Sem migração: `ai_calls.role` é texto livre (linhas antigas de persona ficam `social`).
- Painel: `custos.ts` lê o papel `persona` (cai no `social` em backend antigo), rótulos e tipos da aba IA e dos custos.
- Docs: `docs/ia.md` §1 e o parágrafo do 17.8 (como apontar a persona para `openai-flex`), `docs/dominios/persona.md`, `config/config.example.yaml`, `docs/api-contract.md` (adendo v0.48).
- Prova `simulated`: `backend/tests/test_papel_persona.py` (20 testes: herança do social, bloco próprio, perfis do 17.7, validação da partida, roteamento e papel no uso, vagas compartilhadas) e `frontend/src/features/profiles/custos.test.ts`. `real`: `not_run` (sem chamada ao flex).

## 2026-10-02 — Aprendizado por aplicativo no painel (30.15, primeira fatia, branch feat/30-15-painel-por-app)

- `frontend/src/features/aprendizado/`: nova aba **Aplicativos**, a visão inicial (Global → App, `docs/design/aprendizado-vivo.md` §11): um cartão por app de `GET /api/aprendizado/apps` (existência, declarado, aprendido por tipo e estado, absorvido, "como é usado" pela camada de uso, com o selo "medido, não usado"), mais os cartões "App não resolvido" e "Fora do eixo de app" quando > 0; app com zeros aparece. Detalhe em `#/aprendizado?aba=apps&app=<pacote>` (Declarado, Aprendido, Absorvido) e navegação nos dois sentidos: o item do Livro leva ao app e o app, ao Aprendido filtrado. Filtro "Aplicativo" na aba Aprendido, alimentado por `/apps`, passa `app` ao Livro. Saúde, capability e a fila Atenção ficam para depois (os contratos de saúde ainda não existem). Prova `simulated`: `AplicativosTab.test.tsx` (7); `real`: `not_run`.

## 2026-10-02 — Aprendizado: migração `learning_reviews` (30.9, branch feat/30-9-learning-reviews)

- Migração **069 provisória** (`backend/migrations/069_revisoes_do_aprendizado.sql`): tabela `learning_reviews`, a trilha auditável das revisões do curador por IA (`aprendizado-vivo.md` §8.5), sem FK e sem CHECK, nunca purgada, com `UNIQUE (item_ref, dossie_hash)` como salvaguarda do orçamento (§8.7). Só a forma; o curador é 30.10/30.11. `simulated`: `test_migracao_069_revisoes.py` (SQLite); PostgreSQL `not_run` (P17).

## 2026-10-02 — Aprendizado: evento `learning.needs_person` (30.21, branch feat/30-21-evento-needs-person)

- O Livro publica `learning.needs_person` quando um item entra na espera do dono (faixa B ou C da política de risco) e quando sai
  dela (decidido pela pessoa, rebaixado pelo sistema, substituído). Payload de lista fechada, sem conteúdo (`api-contract.md`,
  adendo v0.49). Porta de eventos do módulo (`PortaDeEventos`), adaptador sobre o `EventBus` em `infrastructure/eventos.py`;
  classificação mínima em `domain/espera.py` (o 30.10 a estende); idempotente por (`kind:ref`, `aguardando`).
- `montar_aprendizado(eventos=...)`: sem o argumento nada é publicado; `state.py` passa `eventos=self.bus`.
- Prova `simulated`: `tests/test_learning_espera.py` (barramento falso e `EventBus` sobre SQLite). `not_run` no central.

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

## 2026-10-02 — Aprendizado: follow-up do modo por app e do veto na rota (30.20 e 30.5, branch feat/30-servico-modo-e-veto)

- 30.20: o D1 de lição e tela usa o modo efetivo do pacote do item (`_modo_publica(kind, app)`; `Ajustes.por_licoes` e
  `por_telas`), o portão de `licoes_para` consulta o fornecedor com algum pacote ligado, e a camada de uso da visão por
  app usa o modo do pacote; os dois `xfail(strict)` de `test_learning_modo_por_app.py` viraram testes normais.
- 30.5: `por_que_nao_publica` passa a trazer `vetado` e `modo_desligado` nas rotas do livro e da visão por app
  (`LearningService.contexto_de_publicacao`). Prova `simulated` (`test_learning_rota_publicacao.py`); nada implantado.

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

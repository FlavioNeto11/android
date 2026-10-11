# Estado atual — handoff

**Revisado em 10/10/2026: runtime do backend em `b2d22ab2e007487` (migração 136; deploy 75); site institucional ligado na raiz pública. Os blocos de 02/10 seguem abaixo como histórico.** Atualize este arquivo ao fechar cada tarefa (skill `fechar-tarefa`). Mantenha-o
curto: o que muda de sessão para sessão fica aqui, e o resto aponta para a fonte principal ([índice](README.md)).

## Onde estamos

- **Deploy 75 no ar (11/10/2026, 01:32:53Z, central `b2d22ab2e007487`, sem migração; tag `deploy-20261011-0132`).** Aposenta a criação de conta pela API do igfarm (31.335, chave `criacao_pela_api_do_igfarm` padrão `false`), lista cabeçalhos da caixa sem corpo (31.336) e traz o proxy sticky da conta planejada (31.337).
  - `real`: deploy rc=0 em 81,6 s, backup `20261010-223131`, saúde ok (`open_connections` 9, `slow_queries_in_loop` 0), prova de fora ok, agente do notebook `0.1.0+b2d22ab` online.
  - `simulated`: docs-check 0/0, catracas 103, mypy 257, scripts/tests 1283, SQLite e PG dirigidos de 61 arquivos, 1363 passed cada. `not_run`: PG inteira do 75.
- **Aprendizado, 11/10/2026 (na `main`; ainda não implantados):** 31.338 (espera a tela depois do reabrir), 31.339 (a Junk não é a caixa de entrada) e
  31.340 (`find_row`: a linha de uma lista cega pelo remetente lido às cegas), todos `simulated`. A prova real que os motivou (deploy 74): replay por receita provado (receita 228,
  0 decisões de IA); 3ª prova do P-046 sem achar a linha (US$ 0,2106). A 4ª prova é o P-052 do dono. Detalhe: `docs/dominios/aprendizado.md`.
- **Deploy 74 no ar (11/10/2026, 01:04:26Z, central `263e0815de46ce`, sem migração; tag `deploy-20261011-0104`).** Alinha código e conhecimento do Outlook (o checkout central tinha andado sem deploy e o `telas.yaml` novo quebrava a exploração; ver o CHANGELOG), traz o ciclo da conta da ponte (31.333) e a fração de interrupção em thread (31.320 ponto 9). **Regra: o checkout central só se move no deploy.**
  - `real`: deploy rc=0 em 75,0 s, backup `20261010-220311`, saúde ok com `problems` vazio (`open_connections` 9, `slow_queries_in_loop` 0), prova de fora como esperado, agente do notebook em `0.1.0+263e081` e online. `simulated`: funil dirigido (SQLite 5677 passed, PG 5675 passed, catracas 103, mypy 257, vitest 2366) com o único vermelho real (`test_ordem_das_rotas`) consertado pela Ponte. `not_run`: SQLite inteiro e PG inteira na ponta.
- **Deploy 73 no ar (10/10/2026, 23:33:18Z, central `a9853379e9f74d`, sem migração nova; tag `deploy-20261010-2333`).** O banco passa a ter uma conexão por thread (31.320), o `/health` mostra `open_connections` e `slow_queries_in_loop`, o login mede o egresso na janela (31.329) e o cadastro guiado ganha o app de campo sem id (31.324 G1).
  - `real`: deploy rc=0 em 70,7 s, backup `20261010-203207`, saúde ok com `problems` vazio (`open_connections` 9, `slow_queries_in_loop` 0), prova de fora como esperado, agente do notebook em `0.1.0+a985337` e online. `simulated`: funil em `a9853379e` (SQLite inteiro 13225 passed, scripts 1283, catracas 103, mypy 257, vitest 2365, PG dirigido 3250 passed). `real` também: a PG inteira em `31affa1f8` deu 13216 passed, 69 skipped e 0 failed (51 min 46 s). `not_run`: a prova real do 31.320 sob carga.
- **Deploy 72 no ar (10/10/2026, 20:10:12Z, central `ac189b091ed3ae`, migrações 135 e 136; tag `deploy-20261010-2010`).** O laço de eventos deixa de esperar consulta síncrona (31.307, índice em `events`), o motivo do bloqueio vira dado na lápide (31.322) e a exploração de efeito tem padrão por verbo (31.325); o interruptor `limits.exploracao_efeito_ligada` segue DESLIGADO.
  - `real`: deploy rc=0 em 90,5 s, backup `20261010-170842`, saúde ok com `problems` vazio, 135 e 136 em `schema_migrations`, ensaio de rollback ok, prova de fora como esperado, agente do notebook em `0.1.0+ac189b0` e online; ensaio das migrações em cópia do backup: 0,16 s. `simulated`: funil em `0409bfe0b` (SQLite inteiro 13095 passed e 1 flake, scripts 1279, catracas 103, mypy 257, vitest 2350, PG dirigido com 16 defeitos de teste consertados). `real` também: a PG inteira em `da2e724e8` deu 13089 passed, 67 skipped e 0 failed (46 min 49 s). `not_run`: prova real da 136 com uma conta bloqueada.
- **Deploy 71 no ar (10/10/2026, 18:35:00Z, central `6f52567307ea86`, migração 134; tag `deploy-20261010-1834`).** Persona de teste formal (31.314/31.315), exploração de efeito pela política com o interruptor `limits.exploracao_efeito_ligada` DESLIGADO (31.297; ligar só com P-046; correção de alcance pendente no 72), painel e scripts.
  - `real`: deploy rc=0 em 86,0 s, backup `20261010-153335`, saúde ok com `problems` vazio, 134 em `schema_migrations`, prova de fora como esperado, agente do notebook em `0.1.0+6f52567` e online. `simulated`: funil dirigido sobre `e8901622` (SQLite 3950 passed, PG dirigido 3947 passed e 1 falha consertada, catracas 103, scripts 1272, mypy 257, vitest 2350). `not_run`: PG inteira (interrompida a 85 % em `332c04f6`, janelas de pausa no CHANGELOG) e SQLite inteiro na ponta; prova real de 31.314 e 31.297 no aparelho.
- **Deploy 70 no ar (10/10/2026, 15:34:18Z, central `d0fbcc5d6ba389`, migrações 130 e 131; tag `deploy-20261010-1534`).** Leva o que a Jev, a Aprendizado, o Portal e a ponte entregaram desde o deploy 69 (lista no CHANGELOG), inclusive o 31.310 (cadastro guiado) que o bloco seguinte ainda descreve como não implantado.
  - `real`: deploy rc=0 em 76,5 s, backup `20261010-123301`, saúde ok com `problems` vazio, 130 e 131 em `schema_migrations`, prova de fora como esperado (com `SITE`, `CONTATO` e `WEBHOOK_DO_TRELLO` ligados), agente do notebook em `0.1.0+d0fbcc5` e online. `simulated`: o funil da ponta (SQLite e PG dirigidos de 56 arquivos, catracas 103, scripts 1268, mypy 257) e as corridas das pontas anteriores. `not_run`: PG inteira e SQLite inteiro na ponta final; prova real de 31.310, ADR-089 e ADR-090 no aparelho.
- **31.324 G1 (Jev, 10/10/2026): o motor do cadastro guiado serve ao app de campo sem id (Bloks); na main (`e079a325f`), não implantado.** Contrato: adendo v1.144. `Alvo` com `classe`, `abaixo_do_rotulo`, `ordem` e `senha`; `nascimento_dia|mes|ano` vindos da `birth_date` da persona (409 `sem_nascimento` / `persona_menor_de_idade` antes de tocar no aparelho); `dispara_codigo`/`antes_do_envio` (o código que o app manda antes de criar a conta, com o piso no toque que o pediu e sem contar como envio); `envia` em `tocar`. `simulated`: `tests/test_cadastro_instagram_like.py` (48) + `test_cadastro_guiado.py` (58), catracas 103, mypy 257. `not_run`: o `cadastro.yaml` real do Instagram (espera a captura da igfarm da Ponte) e qualquer conta de verdade. Detalhe em `docs/dominios/persona.md` (cadastro guiado) e CHANGELOG.
- **31.320 (Jev, 10/10/2026): o banco tem uma conexão por thread e uma trava só de escrita; na main (`c3ac3e900`), não implantado; entra no deploy 73.** `Database` (`backend/app/db.py`): leitura lenta numa thread não segura mais o laço de eventos; escrita contra escrita segue em fila única; o aviso de consulta lenta diz quem segura a trava de escrita; mais de 40 conexões avisam. Etapa 2 no mesmo ramo: `Database.apagar_em_fatias` (lotes curtos com pausa) na purga de `events` e nas de `commands`, `ai_calls` e `measurements`; os saldos medidos (4 mil linhas) não mudaram. Decisão a registrar em ADR (092 reservado) quando a regra "sem ADR novo" for levantada. `simulated`: `tests/test_db_conexao_por_thread.py` (32), com o experimento de 5 s (102 ms contra 4,7 s do modelo antigo). Também `/health` (`open_connections`, `slow_queries_in_loop`) e `scripts/laco-por-hora.py`. `simulated`: regressão ampla de 115 arquivos mais catracas (2805 passed; a catraca de `Any` foi corrigida) e PG dirigido de 52 arquivos no `farm-pg-rapido` (1115 passed, 6 skipped, 0 failed, 21:43Z a 22:10Z). `not_run`: typecheck do frontend de `types.ts` (a Android roda no funil) e a medida `real` do laço (depois do deploy 73, com `laco-por-hora.py --marco 71=… --marco 72=… --marco 73=…`).
- **31.279 (Jev, 10/10/2026): a pergunta de execução vai só ao canal de origem do comando (ADR-086); ramo `feat/31-279-pergunta-so-ao-canal-de-origem`, não implantado.** O aviso `run.needs_input`/`objective.waiting_user` só vai ao Telegram se o comando veio de lá; `PortasReais.para(canal)` dá a cada canal só as perguntas dele; validação, avaliação (`eval-*`), lote, ensaio e operação não avisam, não aparecem e `responder` as recusa; a sucessora herda a origem (`chave_da_sucessora`). Sem API nova, sem migração. `simulated`: `tests/test_pergunta_so_ao_canal_de_origem.py` (28 casos) e os três testes de aviso ajustados. `not_run`: Telegram/Trello reais, PostgreSQL, suíte inteira. Detalhe: [`docs/dominios/canais.md`](dominios/canais.md).
- **31.297 (Aprendizado, 10/10/2026): a exploração de EFEITO pela política do perfil; ramo `feat/31-297-exploracao-de-efeito`, não implantado.** `limits.exploracao_efeito_ligada` (desligado de fábrica): o pedido de efeito vira etapa exploratória `side_effect` (chave `explorar_<verbo>_<objeto>`), julgada pela porta 13.2 por uma ação sintética de risco alto (aprovação por padrão) e pela política `explorar_efeito` ou a do pedido; sem perfil não passa; o efeito do modelo sem a marca do sistema segue recusado; aviso `exploracao.efeito_liberado` na hora (adendo v1.140). `simulated` (`test_exploracao_de_efeito.py`, 99 casos; credencial por lista única em contracts, achado da Jev corrigido no ramo). `real`: `not_run` (o executor contra aparelho real e a linha no painel de Política, do Portal, ficam de fora).
- **31.307 (Jev, 10/10/2026): o laço de eventos não espera o banco no boot; ramo `feat/31-307-laco-sem-sql-sincrono`, não implantado, entra no 72.** Migração 135 (índice `events(instance_id, kind, id)`), pré-leitura do último DTO dos aparelhos fora do laço, `/health` em thread e aviso de consulta síncrona > 1 s na thread do laço. `simulated`: `tests/test_laco_sem_sql_sincrono.py` (14 casos). Pontos 5 a 8: os desfechos da conversa do canal, a lista de operações, o detalhe da execução (`GET /api/runs/{id}`) e a volta das decisões automáticas, todos em thread. Parte 2 (camada `Database` fora do laço por padrão) segue com a orquestradora. `real` (somente leitura, 10/10): plano e tempo no SQLite do central (consulta da adoção 29,0 → 0,7 ms com o índice, em cópia em memória; régua diária 21 ms em 14 dias). `not_run`: o `CREATE INDEX` no central (só no deploy 72), EXPLAIN no PG do harness, PostgreSQL da 135.
- **31.325 (Aprendizado, 10/10/2026): o padrão da exploração de efeito é por verbo; ramo `feat/31-325-politica-por-verbo`, não implantado.** Nível `explorar_<verbo>` entre a do pedido e a genérica; destrutivo (apagar, comprar, transferir, encerrar, desinstalar, resetar) mantém aprovação e não herda `explorar_efeito`; o comum roda sem pedir e avisa (adendo v1.141). `simulated`; `real`: `not_run`; pytest só depois do "71 no ar".
- **P-043 rodado (Aprendizado, 10/10/2026 18:54-19:09Z, deploy 71, `android-01`): a exploração do Outlook funcionou no real; o replay por receita NÃO foi provado.** Pedido A concluído
  pelo modelo real (`r-20261010185405-78fc3f`, US$ 0,0766, receita 227 `candidate`); A2/A2b concluídos pela IA com a receita `nao_aplicavel` ("tela de partida diferente": ela só tem a ação
  "tocar em Junk"); o misto B planejou certo com o modelo real (catálogo em `steps` + exploração dependente), mas a execução parou em `open_inbox` pelo guarda de verificação (ADR-009/058).
  Total US$ 0,2362 em 20 chamadas (teto US$ 2,30). `real` para A e para o planejamento do B; `not_run` para o replay, o misto completo e o Telegram. JSON:
  `.claude/handoffs/aprendizado-resultado-31-273-298-real.json`. Aparelho `android-01` ligado por mim (estava `stopped`), `control none`.
- **31.323 (Aprendizado, 10/10/2026): a decisão de rotina leva a imagem da tela sensível (P-044); ramo `feat/31-323-imagem-sensivel`, não implantado.** `_motivo_da_imagem` não devolve mais `sensivel`: a
  tela de senha/verificação segue a régua das outras (`never` vale para toda tela); aviso e comentários atualizados; leitura visual de valor continua recusando. `simulated`; `real`: `not_run` (sem chamada paga).
- **31.327 (Aprendizado, 10/10/2026): a receita da exploração parte do estado conhecido e a sombra compara pelo alvo; ramo `feat/31-327-receita-da-exploracao`, não implantado.** O executor leva o
  app ao estado conhecido antes de explorar; `distill(exploratoria=True)` ancora a 1ª ação ou recusa; `mesmo_alvo` no lugar do `element_id`; divergência com os dois alvos. `simulated`; `real`: `not_run`.
- **31.331 (Aprendizado, 10/10/2026): o teto da exploração conta só a exploração e o aviso de efeito sai uma vez; ramo `feat/31-331-teto-da-exploracao`, não implantado.** Teto em US$ só das etapas
  exploratórias e olhando a próxima chamada; sem o orçamento da etapa livre; `efeito_liberado` uma vez por execução. `simulated`; `real`: `not_run`.
- **31.328 (Aprendizado, 10/10/2026): a leitura visual da linha da caixa de e-mail não para pelo que a mensagem diz; ramo `feat/31-328-guarda-sem-falso-positivo`, não implantado.** `leitura_visual.regioes[].conteudo_de_terceiros`
  (Outlook): a frase de verificação humana no recorte vira texto do e-mail; código, senha, token e pedido de código seguem recusados. `simulated`; `real`: `not_run`.
- **31.314 (Jev, 10/10/2026): a persona de TESTE formal; não implantado.** Migração 134 (`instagram_profiles.teste`, aditiva; marca por id a persona do Portal) e campo `teste` em `PersonaDTO`/`POST`/`PATCH` (adendo v1.139). Fora da sugestão automática e da distribuição por app, das operações em lote, da contagem do painel e dos avisos ao dono (Telegram e espelho do Trello); citada pelo nome ou pelo id, serve. `simulated`: `test_persona_de_teste.py` (11); `real` e PostgreSQL da 134: `not_run`. O selo e o filtro do painel são do Portal (31.315).
- **31.310 (Jev, 10/10/2026): o cadastro guiado da conta planejada; ramo `feat/31-310-cadastro-guiado`, não implantado.** Rota `…/provisioning/signup` (verbo `session.cadastrar`, adendo v1.137), motor determinístico sem IA (`cadastro.yaml` por app) e `proximo_passo` no DTO. Nenhum app real declara o cadastro (D1, D2 com o dono). `simulated`: `tests/test_cadastro_guiado.py`. `not_run`: conta real num provedor.
- **31.290 (Jev, 10/10/2026): os avisos de tela sensível dizem o que o código faz depois do ADR-089; ramo `fix/31-290-textos-do-adr-089`, não implantado.** `AVISO_TELA_SENSIVEL` e os comentários falsos reescritos; "nunca são recortadas" da leitura visual confirmado e mantido. `simulated`: `tests/test_aviso_de_tela_adr089.py`. `not_run`: o painel real. Aberto: o executor ainda não leva a imagem da tela sensível à decisão de rotina (`_motivo_da_imagem`).
- **31.311 e 31.312 (Aprendizado, 10/10/2026): o replay da etapa descoberta provado no simulado (31.311, `4b7404a2`) e a exploração que parou vira pedido de ensino (31.312, adendo v1.138).** Backend na `main` (o botão no painel é do Portal). `simulated`: `tests/test_replay_da_etapa_descoberta.py`, `test_ensino_da_exploracao_que_parou.py`. `not_run`: o mesmo no Outlook real e o ensino de uma exploração real (P-043); o ensino taught oferecido pelo nome com a chave `explorar_…`.
- **31.302 (Aprendizado, 10/10/2026): a conta âncora ociosa é relida de tempos em tempos, sem IA; na `main`, desligado de fábrica.** Laço `modules/identity/infrastructure/verificacao_periodica.py` (`contas.verificacao_periodica_h`, padrão 0): uma conta por volta, só em aparelho ligado e livre, com a CPU do host abaixo de 50 % (o 50 é palpite a calibrar em dev), sem quarentena nem pausa de reparo. O desafio visto cai no ADR-068. Evento `session.verificacao_periodica` e linha no relatório de falhas (adendo v1.136). Contra funil ou suíte rodando por fora só há a CPU, a pausa de reparo e o recurso desligado: não há sinal dentro do processo. Ligar no central é decisão de `config.yaml` (só vale na subida).
  - `simulated`: `tests/test_verificacao_periodica_de_sessao.py` (21 casos, motor de sessão de verdade sobre o Instagram de mentira). `not_run`: releitura numa conta real, custo no aparelho, PostgreSQL.
- **31.273, 31.269 e 31.298 (Aprendizado, 10/10/2026): o pedido fora do catálogo explora em vez de recusar; na `main` (`d1d429e5`, `35b5bdd6`, `d03cc814`).** Migração 130, adendos v1.130 e v1.131, ADR-091, K-111. O que faz e o que falta: [`docs/dominios/aprendizado.md`](dominios/aprendizado.md) (seção da exploração). O deploy é da orquestradora; esta sessão não o conferiu.
  - `simulated`: `tests/test_exploracao_fora_do_catalogo.py`, `test_etapas_descobertas.py`, `test_migracao_130.py`, `test_aviso_exploracao.py`, `test_evento_grupo_de_politica.py`. `not_run`: exploração real no Outlook (API paga, pede o sim do dono, P-043), o prompt novo do pedido misto com o modelo real, PostgreSQL, replay da etapa exploratória no Outlook real. O replay no simulado (aprende, promove, repete sem IA) está provado no 31.311: `tests/test_replay_da_etapa_descoberta.py`.
- **31.288 + 31.293 prontos no branch `fix/31-288-testemunha-da-purga` (10/10/2026), ainda não na main nem implantados.** O 31.293 faz a régua diária do curador ler, gravar e soltar o lock um dia por vez (`tests/test_regua_diaria_em_blocos.py`, `simulated`; o travamento de ~10 s com a máquina carregada não foi reproduzido). A testemunha da purga do curador percorre `events` por `ts` em blocos (K-110) em vez do `MIN(ts)` que lia as ~32 mil linhas sem execução com o lock do banco. `simulated`: 7 testes novos mais a bateria dirigida; `not_run`: o travamento real (só se prova no central, com as pilhas do vigia do laço). Sem migração.
- **31.284 (validação ponta a ponta da conta planejada) pronta no branch `feat/31-284-validacao-conta-planejada` (10/10/2026), sobre 31.281 e 31.282.** Três personas sem Outlook, provedor simulado, falha, retomada e desistência; a validação achou e corrigiu dois defeitos do 31.281 (senha do cadastro em app de login gerenciado, `authenticated` no aparelho vinculado). `simulated`: 814 dirigidos verdes; `not_run`: painel (31.283), aparelho e provedor reais.
- **31.282 (refinador por estado) pronto no branch `feat/31-282-refinador-por-estado` (10/10/2026), sobre o 31.281; sem migração, adendo v1.132.** A pergunta "a senha já está guardada?" deixa de existir: o servidor lê o estado das contas, descarta a pergunta de credencial do modelo e devolve `acoes_de_conta`. `simulated`: 8 testes novos; `not_run`: modelo real, painel (31.283) e validação ponta a ponta (31.284).
- **31.281 (conta planejada) pronto no branch `feat/31-281-conta-planejada` (10/10/2026), ainda não na main nem implantado; migração 131 e adendo v1.132.** Backend do ADR-087: planejar, credencial gerada/digitada/reutilizada no cofre, ciclo de provisionamento e conta não confirmada fora do roteamento e da sessão. `simulated`: 22 testes novos e a bateria dirigida; `not_run`: o refinador por estado (31.282), o painel (31.283) e a validação ponta a ponta (31.284).
- **Deploys 67, 68 e 69 no ar (10/10/2026, central `6536ad04d6c99e`, migração 133; sem tag).** Feitos de madrugada por outras sessões, com `PularFrontend` e `SemTag`; a linha de cada um está em `data/deploys.jsonl`.
  - 67: 00:33:33Z, `7d187acb` → `972633a7` (egresso da ponte confirma o aparelho da própria persona), backup `20261009-213213` (do ensaio), 72,7 s.
  - 68: 02:16:41Z, `972633a7` → `9ad2907f` (ADR-089, a plataforma não esconde tela de ninguém), backup `20261009-231605` (do ensaio), 32,2 s.
  - 69: 02:30:40Z, `9ad2907f` → `6536ad04` (ADR-090, o código do e-mail entra no login automático), backup `20261009-233006`, 33,5 s.
  - `real`: os três com resultado `ok` no jsonl; saúde ok em 10/10 ~12:55Z, commit `6536ad04d6c99e`, migração `133_egresso_igfarm` (já era a migração antes do deploy 67). `simulated`: só o que cada entrada do CHANGELOG de 10/10 cita (`test_egresso_igfarm`, `test_previa_sem_tela_escondida`, `test_codigo_por_email` e os dirigidos de cada commit); funil reduzido. `not_run`: PostgreSQL, vitest e SQLite inteiros; a prova real de ADR-089 e ADR-090 no aparelho; prova de fora e agente do notebook (sem registro desses deploys).
- **Deploy 66 no ar (09/10/2026, 17:16:06Z, central `7156f0df77e958`, migração 132; ponte Android⇄igfarm, ADR-088).**
  - `real`: deploy rc=0, tag `deploy-20261009-1716`, saúde ok, migração 132, prova de fora, agente `0.1.0+7156f0d`. `simulated`: funil reduzido (SQLite dirigido e PG da migração, sem vitest). `not_run`: vitest, PG e SQLite inteiros, ponte real com o igfarm.
- **Deploy 65 no ar (08/10/2026, 19:37:51Z, central `2e990961c3426c`, sem migração nova; Jev 31.287: a receita concorda por alvo).**
  - `real`: deploy rc=0, tag `deploy-20261008-1937`, saúde ok, prova de fora, agente `0.1.0+2e99096`. `simulated`: funil reduzido (SQLite dirigido, sem PG e sem vitest). `not_run`: PostgreSQL, vitest, SQLite inteiro e o 31.287 real.
- **Deploy 64 no ar (08/10/2026, 19:05:11Z, central `9cb6fae3a0e738`, sem migração nova; Jev 31.286: a conta só vale no próprio perfil).**
  - `real`: deploy rc=0, tag `deploy-20261008-1905`, saúde ok, prova de fora, agente `0.1.0+9cb6fae`. `simulated`: funil reduzido (SQLite dirigido, sem PG e sem vitest, por ordem). `not_run`: PostgreSQL, vitest, SQLite inteiro e o 31.286 real.
- **Deploy 63 no ar (08/10/2026, 18:34:56Z, central `c74a695c856319`, sem migração nova; Jev 31.285: sem a porta "mesmo pedido a N contas").** Cada conta é julgada pela política do próprio perfil.
  - `real`: deploy rc=0, tag `deploy-20261008-1834`, saúde ok, migração 129, prova de fora, agente `0.1.0+c74a695`. `simulated`: funil reduzido (sem PG e sem vitest, por ordem), verde com a nota `test_com_teto_de_cpu`. `not_run`: PostgreSQL, vitest e o 31.285 real.
- **Deploy 62 no ar (07/10/2026, 22:27Z, central `228c56a4097b5b`, sem migração nova; grupo "Liberado" sem aprovação de política e aviso "sem senha guardada").** Jev 28.61 (`ac028b94`, decisão "A" do dono) e 31.278 (`da345bb6`).
  - `real`: deploy com backup `20261007-192607` (tag `deploy-20261007-2227`), saúde ok, migração 129, prova de fora, agente `0.1.0+228c56a`. `simulated`: suíte 62 verde com notas de ambiente (`test_com_teto_de_cpu`, vitest e 1 PG sob carga, re-rodados). `not_run`: 28.61 e 31.278 reais.
- **Deploy 61 no ar (07/10/2026, 20:52Z, central `9df65300bd3e63`, migração 129 (`129_prova_da_candidata`, ensaiada numa cópia restaurada do backup de hoje: só a 129 aplicou); ensino a partir da execução, custo por passo, laço e validação da operação, revisão automática restringida, funil versionado).** todas as pontas: Aprendizado 31.221 a 31.223 e 31.230;
  Jev 31.207, 31.220, 31.224, 31.227, 31.229, 31.235, 31.236, 31.240, 31.241; Aprendizado também 31.231 a 31.233, 31.237 a 31.239, 31.242 a 31.244; Portal 31.225, 31.226, 31.228, 31.234; GitHub 29.190, 29.191, 29.194, 29.195; DevOps 29.196 a 29.199. Mais as pontas re-baseadas da integ-62 (o 61 absorveu o 62): Jev 31.251, 31.253 re-emitido, 31.258 a 31.260, 31.267, 31.272 a 31.277 (a suíte volta a coletar após o ADR-083; 31.240 saiu, sem objeto com o ADR-083); Aprendizado 31.248 a 31.250, 31.262, 31.268, 31.271 (migração 129); Portal 31.270, 31.274 (painel), 31.275, 31.276; Venice (fix da chave da API); DevOps 29.204 e 29.205, e a correção do funil.ps1 (variável com a caixa do parâmetro) feita pela Android. Registro: commit_antes 149612566d809f (deploy 60); a pasta do ensaio da migração 129 em C:/temp/ensaio-129 (apagar não é com a Android).
  - `real` (central WIN-7S2UASNLFOP): deploy com backup `20261007-175112`; saúde ok, migração 129; prova de fora; agente `0.1.0+9df6530`; A10 ok. GitHub 29.194 e 29.195 reais. Funil: wrapper -Teto 25: árvore entre 7 e 12 % do total de CPU; funil 19:49Z a 20:51Z.
  - `simulated` (suíte 61): números do CHANGELOG.
  - `not_run`: reais do ensino a partir da execução, do custo por passo, do laço e da receita sem o voltar (primeira operação após este deploy); testes com carga do 29.196.
  - Próxima ação: primeira operação após o deploy com a política nova e a promoção de uma receita da onda 2 (31.221/31.226, uma vez), medida do custo por alvo contra a onda 2; corte 62.
- **Deploy 60 no ar (07/10/2026, 19:40Z, central `149612566d809f`, migração 128; ensino com alcance, curadoria e escopo de assunto; operação com latência, fila e relatório; Trello e Telegram da prova; CI e tarefas do host).** todas as pontas: Aprendizado 31.181 a 31.183,
  31.190 a 31.192, 31.200 a 31.202, 31.210, 31.217 a 31.219; Jev 31.187, 31.193 a 31.195, 31.205, 31.206, 31.213, 31.216; Portal 31.184 a 31.186, 31.188, 31.189, 31.196 a 31.199, 31.203, 31.204, 31.208, 31.209, 31.211, 31.212, 31.214, 31.215; Canais 28.65 a 28.77; DevOps 29.186; GitHub 29.184, 29.187 a 29.189, 29.192, 29.193. Mais Portal 31.275 (testes do limite da frota após o ADR-083) e Jev 31.272 (a suíte volta a coletar após o ADR-083). O refactor do dono (ADR-083, ac2f6bba) já estava no ar desde 17:26:36Z por reinício direto, sem backup, tag nem linha em deploys.jsonl: este deploy regulariza (commit_antes ac2f6bba; o backup é do banco de hoje, sem retrato do instante anterior ao reinício).
  - `real` (central WIN-7S2UASNLFOP): deploy com backup `20261007-163919` e ensaio de rollback (ok, 13,7 s: o código ac2f6bba abriu o backup); saúde ok, migração 128; prova de fora; agente `0.1.0+1496125`; A10 ok. Hardware 29.181 e 29.182 reais; GitHub 29.189 e 29.193 reais. Funil com teto: -Teto 25 (powershell 5.1): árvore entre 7 e 11 % do total de CPU; funil 18:29Z a 19:37Z.
  - `simulated` (suíte 60): números do CHANGELOG.
  - `not_run`: tarefas do host do 29.186; rótulo agente-nuvem; percursos reais do Portal; 31.210 e 28.77 reais na próxima ocasião.
  - Próxima ação: corte 61 (Jev 31.207, 31.220, 31.224, 31.227; Aprendizado 31.221 a 31.223; Portal 31.225, 31.226, 31.228; GitHub 29.190, 29.191, 29.194, 29.195; DevOps 29.196) e o relatório da prova de 07/10 (31.161).
- **Deploy 59 no ar (07/10/2026, 02:32Z, central `8552b160281e8d`, sem migração; ensino por semelhança e receitas como ação, concorrência por medida, avisos do host e da operação, CI e custo do GitHub).** 9 pontas: Aprendizado 31.151,
  31.153, 31.157, K-107; Jev 31.175 e hora por estágio; Canais 28.62, 28.60, 28.58; DevOps 29.156 fatias 4 e 5, 29.174, 29.185; GitHub 29.177 a 29.179; Android 29.154 fatias 3 e 4; Portal custo no relatório. Nenhuma correção de código durante o funil 59.
  - `real` (central WIN-7S2UASNLFOP): deploy com backup `20261006-233052`; saúde ok, migração 127; prova de fora; agente `0.1.0+8552b16`; A10 ok. GitHub 29.177 a 29.179 reais. Funil com teto: funil inteiro em 1:11:30 (01:15:36Z a 02:27:06Z, rc 0; 58 saturado: 1:36:54) sob -Teto 25 com PowerShell 5.1, árvore a 8,1 % da CPU do host e no teto em no máximo 5 % das batidas (só apara picos); SQLite 18:48 contra 15:22 sem teto no 57 (+22 %) e 31:07 no 58; avisos de pressão no SQLite 3 contra 24 no 58; o PostgreSQL dirigido é 63 % do funil e o teto não o alcança (a carga mora no contêiner na VM do WSL): teto fixo 25 % passa a ser o padrão do funil (29.174 e 29.180 reais, comparação da DevOps em `devops-29-180-comparacao-59.md`).
  - `simulated` (suíte 59): números do CHANGELOG.
  - `not_run`: amostrador v2 registrado; 31.157, 31.151 e 31.153 reais na operação de 07/10; avisos do host reais.
  - Próxima ação: prova de 07/10 (onda 2 10:00Z, rodada 13:00Z, relatório 15:00Z), cenário em `.claude/handoffs/prova30/cenario.md`; deploy 60 (migração 128, com -Ensaio) depois das 15:00Z.
- **Deploy 58 no ar (07/10/2026, 01:12Z, central `0c8683e8b8549d`, sem migração; reposição do ensino, operação pela tela, observabilidade e canais).** 19 pontas: Aprendizado 31.165,
  31.150, 31.149, 31.169, 31.152; Portal 31.168, 31.170 a 31.172, 31.176; GitHub 29.169 a 29.171 e 29.166a; DevOps 29.160 e 29.156 fatia 1; Canais 28.63 e 28.64 (28.62, 28.60 e 28.58 vão no 59); Jev 15.15 F5c B. Correções feitas durante o funil 58, só teste e uma importação: `fix/31-177-prefixo-do-lote` 6f21669a (`rendimento.py` importa `PREFIXO_LOTE` de `app/contracts/origem.py`, achado da Jev no dirigido do vigia) e os seis hashes de `test_prompts_licoes` recalculados após o dfaeb216 (regras de conteúdo T1, decisão do dono; lição: quem muda `prompts.py` roda `test_prompts_licoes`).
  - `real` (central WIN-7S2UASNLFOP): deploy com backup `20261006-221038`; saúde ok, migração 127; prova de fora; agente `0.1.0+0.1.0+0c8683e`; A10 ok. Hardware 29.161 real.
  - `simulated` (suíte 58): números do CHANGELOG.
  - `not_run`: tarefas agendadas novas; percursos reais do Portal; religar fluxo para persona real.
  - Próxima ação: 07/10 ~10:00Z onda 2 (3 alvos, pela tela se este deploy sair antes) e 13:00Z rodada (~30 alvos), cenário em `.claude/handoffs/prova30/cenario.md`.
- **Deploy 57 no ar (06/10/2026, 23:08Z, central `42cba3cd879f36`, sem migração; correções das revisões do corte 56 e painel do grupo de política).** 5 pontas: Jev 31.154
  (revisões 479/483/487: alvo recusado por nome fixo em conflito, reserva na seção crítica, reabertura recalcula os alvos, `fontes_da_pesquisa`); Aprendizado 31.163
  (revisões 480/482: id cru da lição, `conflict` conta contra, `scope_profile_id`, `avisos`, lição de várias personas); Portal 28.61 (seletor do grupo), 31.164, 31.166 e 31.167. A main trouxe também os 2 commits de conteúdo do dono (213d3476, dfaeb216: as regras de conteúdo saem dos prompts, da persona e do orquestrador de personas; emendas aos ADR-048 e ADR-050; dirigido 470 SQLite + 470 PG e vitest 184 verdes antes do deploy). Deploy em 89 s, sem migração; prova de fora 46 ok na 2ª rodada (1 falha transitória logo após o A10).
  - `real` (central WIN-7S2UASNLFOP): deploy com backup `20261006-200644`; saúde ok, migração 127; prova de fora; agente `0.1.0+42cba3c`; A10 ok.
  - `simulated` (suíte 57): números do CHANGELOG.
  - `not_run`: percursos reais do Portal e as correções de concorrência sob a rodada de 07/10.
  - Próxima ação: 07/10 ~10:00Z onda 2 (3 alvos) e 13:00Z rodada (~30 alvos), cenário em `.claude/handoffs/prova30/cenario.md`; corte 58 (31.165, 31.150, 31.149, 31.169, 31.168) depois da prova.
- **Deploy 56 no ar (06/10/2026, 21:15Z, central `f831945447a6bd`, migrações 126 e 127; corte da noite da prova).** 8 pontas: Jev 31.155 (ADR-080),
  31.154 v1.95, ADR-081 (regra da frota configurável) e 28.61 (grupo "Liberado"); ensino 31.148, 31.163 e correções do 31.157/31.160; Portal
  31.162 e campos do ADR-081; DevOps 29.167 b; GitHub C17. Jev: teste do leque ajustado à regra nova (ca908e7e). Aviso: o android-03 caiu às 20:19Z sob a pausa de reparo e voltou 21:16Z; reverificar a sessão antes da onda 2.
  - `real` (central WIN-7S2UASNLFOP): deploy com backup `20261006-181424` e ensaio das migrações; saúde ok, migração 127; prova de fora; agente `0.1.0+f831945`; A10 ok.
    Onda 1 da prova em 06/10 19:48Z: 14 de 14 estágios, comentário verificado em post nosso, US$ 0,289.
  - `simulated` (suíte 56): números do CHANGELOG.
  - `not_run`: onda 2 e rodada de 07/10; troca de conta no Instagram.
  - Próxima ação: 07/10 ~10:00Z onda 2 (3 alvos) e 13:00Z rodada (~30 alvos; 27 param em "conta"), cenário em `.claude/handoffs/prova30/cenario.md`.
- **Deploy 55 no ar (06/10/2026, 19:11Z, central `086236e9df30a8`, migrações 124 e 125; corte da prova de 07/10).** 16 pontas: Jev 31.154,
  31.156 e fix 29.126; ensino 31.157 e 31.158; Portal 31.159, 31.144, 31.145, 31.146 e 31.147; canais 28.59 e 29.145; DevOps 29.166 b e
  29.167. Também: Portal 29.164 (seção Comando remoto em português) e 31.162 (relatório da operação em Markdown e JSON); Frente GitHub 29.166 ponta a (job docs do CI com o docs-check completo); dois testes de outras frentes corrigidos na integ (409a3604).
  - `real` (central WIN-7S2UASNLFOP): deploy com backup `20261006-160927` e ensaio das migrações; saúde ok, migração 125; prova de fora; agente `0.1.0+086236e`; A10 ok; `ai.pesquisa.enabled` ligada neste deploy (antes: chave ausente = false; depois: true; teto US$ 0,25 por operação).
  - `simulated` (suíte 55): números do CHANGELOG.
  - `not_run`: a operação real da prova (ondas 1, 3–4 e 30); percurso da tela Operação; ensaio real de restauração.
  - Próxima ação: onda 1 (1 alvo, `max_usd` 0,75) pelo roteiro; depois 3–4; rodada com ~30 alvos em 07/10 (cenário em
    `.claude/handoffs/prova30/cenario.md`).
- **Deploy 54 no ar (06/10/2026, 17:07Z, central `2193a8b50ea34e`, sem migração; suíte 53).** 8 pontas: canais 28.57 e 28.55, parque 29.154 f2 e
  29.159 (histórico, tag e rollback), Jev 29.163, ensino 31.142 e 31.143 (adendos v1.91 e v1.92). Primeira tag: deploy-20261006-1707 (release criado).
  - `real` (central WIN-7S2UASNLFOP): deploy com backup `20261006-140601`; saúde ok, migração 123; prova de fora; agente `0.1.0+2193a8b`; A10 ok.
  - `simulated` (suíte 53): números do CHANGELOG.
  - `not_run`: percurso 53; antes/depois do 29.163; provas reais do 28.57 e 28.55.
  - Prioridade a partir de 16:22Z: prova de capacidade com 20–30 agentes em 07/10 (`.claude/handoffs/prova30/`); corte 54 fica
    subordinado ao caminho crítico dela.
- **Deploy 53 no ar (06/10/2026, 15:45Z, central `6e7b87cf`, sem migração; hotfix do 31.137).** 1 ponta (`c580a9db`): `Adb.start_app` com
  tarefa limpa mandava `--activity-new-task`, que o `am` do Android 34 recusa; toda abertura do Configurações por esse caminho falhava
  no deploy 52 (ação manual, ponto de partida da prova, LT-6 do executor). Só o Configurações usa a tarefa limpa; Instagram intacto.
  - `real` (central WIN-7S2UASNLFOP): defeito medido no android-04 (`c-20261006152209-ada35d`, 15:22Z); deploy com backup `20261006-124324`;
    saúde ok, migração 123; prova de fora; agente `0.1.0+6e7b87c`; A10 ok (agente dos dois workers em `0.1.0+6e7b87c`, pausas do worker retiradas; 9 de 15 online, todos ready: os 6 parados são os 3 da redução do notebook das 15:16Z e os 3 já parados).
  - `simulated` (funil dirigido, sem suíte inteira, declarado no CHANGELOG): SQLite 1224, PG 1224, mypy 257, docs-check 0/0.
  - `not_run`: latência real do 31.137 (7 medidas, Jev, logo após). Corte 53 segue: 29.154 fatia 2 (`5cbf0c1e`), 31.142, 31.143,
    28.57, 29.160, F5c B.
- **Deploy 52 no ar (06/10/2026, 15:08Z, central `cdee6620`, migrações 122 e 123).** 5 pontas: ensino (31.130, 31.135, 31.138–31.141),
  painel do Livro (31.131–31.134, 31.136, PR 470), arquitetura (15.15 F4i, F5b, F4j, F5c; 31.137), parque (29.154 fatia 1, ADR-079),
  GitHub (29.155 C2–C5, C10, C11; 29.157; 29.158).
  - `real` (central WIN-7S2UASNLFOP): deploy com ensaio; saúde ok, migração 123; prova de fora; agente `0.1.0+cdee6620`;
    A10 ok; prova F2 do ensino no android-04 concluída (r-20261006140412-cebae7, US$ 0,01141); C3 run 37463062580 (68 min 34 s).
  - `simulated` (suíte 52): números do CHANGELOG.
  - `not_run`: percurso 52; latência do 31.137; fatia 2 do 29.154; provas reais do 29.157/29.158; scripts do banco em 122.
  - Plano-100: resultados da suíte 52 aplicados (Portal, Jev, Android, GitHub, Aprendizado). Corte 53: Jev (F5c B com janela em
    state.py, 31.137 latência), Android (29.154 fatia 2, 29.159), DevOps (29.160), GitHub (provas reais), Hardware (29.161 análise);
    31.115 em 20/10, 29.75 em 11/10, 30.72 em 12/10, 29.152 medida em 07/10, C8 em 14/10.
- **Deploy 51 no ar (06/10/2026, 12:27Z, central `8aee8c6b`, migração 121).** 4 pontas: arquitetura (15.15 F5a portões em
  gates.py, F4h router de personas), ensino (31.122 F2, 31.123 F2, 31.127), painel do ensino (31.128, 31.129), espelho do Trello (T.N).
  - `real` (central WIN-7S2UASNLFOP): deploy com ensaio; saúde ok, migração 121; prova de fora; agente `0.1.0+8aee8c6b`;
    A10 ok; readoção 12 online e 3 parados; rotas de personas e despacho pelos portões: leitura da Jev `not_run`.
  - `simulated` (suíte 51): números do CHANGELOG, com 1 falha flaky declarada no SQLite.
  - `not_run`: percurso 51; prova real da F2 do ensino; reparo do 31.118 com `screen_elements`.
  - Plano-100: resultado da suíte 51 aplicado. Corte 52: Portal (31.131, 31.132-31.134, 31.136), Aprendizado (31.130, 31.135),
    Jev (F5b apps, F4i instagram); 31.115 em 20/10, 29.75 em 11/10, 30.72 em 12/10, 29.152 medida em 07/10.
- **Deploy 50 no ar (06/10/2026, 10:16Z, central `bec1621c`, migração 120).** 3 pontas: ensino (31.121, 31.122, 31.123,
  31.118 F2), painel do ensino (31.120, 31.124, 31.125, 31.126), routers de fleet (15.15 F4e-F4g).
  - `real` (central WIN-7S2UASNLFOP): deploy com ensaio; saúde ok, migração 120; prova de fora; agente `0.1.0+bec1621c`;
    A10 ok (12 aparelhos ready); rotas de fleet iguais; reparo do 31.118 com telas pendente (backup `20261006-071450`).
  - `simulated` (suíte 50): números do CHANGELOG.
  - `not_run`: percurso 50; prova real do ensino (31.121/122/123).
  - Plano-100: resultado da suíte 50 aplicado. Corte 51: F5a portões e F4h personas (Jev); 33.2 protocolo (orquestradora)
    depois dos 5 levantamentos; 31.115 em 20/10, 29.75 em 11/10, 30.72 em 12/10, 29.152 medida em 07/10.
- **Deploy 49 no ar (06/10/2026, 08:56Z, central `1b86bd6b`, sem migração nova).** 8 pontas: 31.116 fechado (formulário com causa e
  pergunta, v1.82), 31.119 (Descartar), 31.118 (gravação com a marca), 15.15 F7 evento do 409 e F4d releases, 29.151
  (readoção pelo aparelho), 29.152 (evento de pressão nomeia quem pesa), C-28 (corte adiado e deploy pelo Git), UX da lista Para revisar.
  - `real` (central WIN-7S2UASNLFOP): deploy; saúde ok, migração 119, problems e features iguais; prova de fora 46 ok; agente `0.1.0+1b86bd6b`; A10 com 12 aparelhos ready em
    menos de 60 s; eventos do 409: 0; aviso de pressão já com data.pressao; reparo do 31.118 pendente (backup `20261006-055448`).
  - `simulated` (suíte 49): números do CHANGELOG.
  - `not_run`: percurso 49; 31.120.
  - Plano-100: resultado da suíte 49 aplicado. Corte 50: 31.120 (Portal), F4e workers (Jev), 29.152 alterações (Android,
    após o cartão do dono), 33.1 levantamentos; 31.115 em 20/10, 29.75 em 11/10, 30.72 medida em 12/10.
- **Deploy 48 no ar (06/10/2026, 07:24Z, central `16858086`, sem migração nova).** 5 pontas: 31.116 (diagnóstico no treino e
  ensino sugerido, v1.80), 30.34 (observar B gera prova), 31.117 (origem do fluxo no Livro, v1.81), C-28 (deploy pelo git na
  reconciliação do Trello); 15.15 F7+F4+F2 com prova real.
  - `real` (central WIN-7S2UASNLFOP): deploy; saúde ok, migração 119, features com `validacao_pelo_observar_b`; prova de
    fora como esperado; agente `0.1.0+16858086`; 01, 03, 06 e 13 ready, android-02 automation em error (escada); 31.117 Livro = /api/flows; 31.113 e 31.87 reais (r-20261006070730-277418).
  - `simulated` (suíte 48): números do CHANGELOG.
  - `not_run`: percurso 48; formulário do 31.116.
  - Plano-100: resultado da suíte 48 aplicado. Corte 49 (pontas prontas): Portal 3b5837d6 (formulário do 31.116 com v1.82), Aprendizado 818ad357 (v1.82) e 31.118,
    Jev 272b2d3a (evento do 409) e F4d releases, Canais 029037e2; 31.115 em 20/10, 29.75 em 11/10, 30.72 medida em 12/10.
- **Deploy 47 no ar (06/10/2026, 06:47Z, central `d2d346cd`, sem migração nova).** 6 pontas: 15.15 F7+F4+F2 (máquinas impostas,
  routers por contexto, saúde em módulo), 31.113 F3 (bindings com marcador, v1.79), A1 (prévia da porta), 31.91 T1 na tela,
  31.116 e 31.117 no corte 48.
  - `real` (central WIN-7S2UASNLFOP): ensaio pela trava de 60 min (cópia `dataackups61006-034615`) e deploy `-PularBackup`; saúde ok, migração 119,
    `problems []` e `features` idênticos aos de antes (prova do F2); 0 transições recusadas (prova do F7); prova de fora como
    esperado; agente `0.1.0+d2d346c`; aparelhos 01, 02, 03, 06 e 13 `ready`.
  - `simulated` (suíte 47): números do CHANGELOG.
  - `not_run`: percurso no navegador; prova real do 31.113.
  - Plano-100: resultado da suíte 47 aplicado; IDs 31.116 e 31.117 (janela 48). Corte 48: 31.116 (Portal), 31.117 (Jev),
    prova real do 31.113, 15.15 restante (portas estreitas, cluster de apps), 31.115 em 20/10.
- **Deploy 46 no ar (06/10/2026, 05:21Z, central `325a04fb`, sem migração nova).** 13 pontas: 31.113 F1+F2 (registro mascarado;
  bindings até a F3), 31.111 F4+F5+A (diagnóstico, tela "Ensinar a corrigir", bloqueio ensinável), 29.153 (custo no detalhe,
  backend e painel), 28.56 (mapa do Trello fora do Git, reconciliação dos parciais), 31.114 (arraste, v1.76), 31.91 T1 (ADR-078,
  rotas v2 obsoletas com contador; T2 em 14 dias = 31.115).
  - `real` (central WIN-7S2UASNLFOP): ensaio exigido pela trava de 60 min (cópia `dataackups61006-021934`) e deploy `-PularBackup`; saúde ok,
    migração 119, `problems []`; prova de fora como esperado; agente `0.1.0+325a04f`; aparelhos 01, 03, 06 e 13 `ready`;
    mapa.json reposto e ignorado.
  - `simulated` (suíte 46): números do CHANGELOG.
  - `not_run`: percurso no navegador; prova real do 31.113 F2; 31.79.
  - Plano-100: resultado da suíte 46 aplicado; IDs 31.114 e 31.115 (janela 47). Corte 47: 31.91 T1 na tela (Portal),
    31.113 F3, 31.79 (Portal, pago com teto).
- **Deploy 45 no ar (06/10/2026, 03:13Z, central `7154d7cf`, migração 119, nova).** 7 pontas sobre `6c03214f`: ensino 31.111 F1+F2+F3
  (escolha do dono na P-014), 31.112 (pergunta da IA com marcador), 31.90-D painel; canais 28.56/C-28 (Trello reconciliado nos
  três quadros); piloto do Copilot 29.137 e 30.82; 31.101 (troca ampla de nomes nos testes).
  - `real` (central WIN-7S2UASNLFOP): ensaio com a cópia `dataackups61006-001227` (119 numa cópia restaurada); deploy com
    `-PularBackup`; saúde ok, migração 119, `problems []`; prova de fora como esperado; agente `0.1.0+7154d7c`; aparelhos 01, 03 e 06
    online com automação `ready`.
  - `simulated` (suíte 45): números do CHANGELOG.
  - `not_run`: percurso no navegador; prova real do 31.111 (F6).
  - Plano-100: resultado da suíte 45 aplicado (671 itens). Corte 46: 31.111 F4 (Aprendizado) e F5 (Portal, d52a9ee8); 31.113 F1
    (80b22dbc) e F2; 29.153 (PR 465); mapa.json fora do Git (Canais, 28.56).
- **Deploy 44 no ar (06/10/2026, 02:15Z, central `33c7d5ab`, migrações 117 e 118, novas).** Oito pontas sobre `b8c37ef7`:
  aprendizado 30.76, 30.77, 30.78 e 30.85; ensino 31.89 (+ painel) e 31.110; sombra da R5 31.13 (decisão P-013 do dono);
  script de troca de nomes. Detalhe no [CHANGELOG](../CHANGELOG.md) e no [livro do plano](execucao-plano-100-runner.md).
  - `real` (central WIN-7S2UASNLFOP): ensaio com a cópia `dataackups61005-231126` (117 e 118 numa cópia restaurada);
    deploy com `-PularBackup`; saúde ok, migração mais alta 118; prova de fora como esperado; agente `0.1.0+33c7d5a`;
    aparelhos 01, 03, 06 e 13 online; config com `consumidores.apps: shadow` carregada sem aviso; hooks sem erro.
  - `simulated` (suíte 44): números do CHANGELOG.
  - `not_run`: percurso no navegador (Portal, a seguir); relatório da sombra da R5.
  - Plano-100: resultado da suíte 44, os sete itens sem estado (auditoria do Trello) e a prova do 31.87 aplicados; IDs
    novos 28.55, 29.153 e 31.113 (670 itens). Trello reconciliado nos três quadros pela Canais (regra C-28, código no corte
    45). Corte 45 em montagem: PRs do Copilot 462, 463 e 465, 31.90-D painel, 31.112, reconciliação do Trello, troca ampla
    de nomes (só testes) e 31.111 F1+F2 (migração 119).
- **Deploy 43 no ar (06/10/2026, 00:50Z, central `f15ef2e1`, sem migração nova; segue a 116).** Sete pontas sobre
  `2e41f18b`, todas do ensino: 31.87 F2 (identidade e ensino com dados da persona), 31.88 F2 (escopo ao provar), 31.90-C/D/E/F
  (revisão do ensino: perguntas da IA, correção do gravado, undo), 31.91 F1, 31.108 e 29.104; mais o teste do 28.54.
  Detalhe no [CHANGELOG](../CHANGELOG.md) e no [livro do plano](execucao-plano-100-runner.md).
  - `real` (central WIN-7S2UASNLFOP): ensaio com a cópia `dataackups61005-214529`; deploy com `-PularBackup`; saúde
    ok; prova de fora como esperado; agente `0.1.0+f15ef2e`; aparelhos 01, 03 e 06 online; hooks sem erro.
  - `simulated` (suíte 43): números do CHANGELOG.
  - `not_run`: percurso no navegador (Portal, a seguir), com a prova real do 31.90-C numa segunda persona de teste.
  - Plano-100: resultado da suíte 43 e o percurso real do deploy 42 (31.86, 30.81, 29.148, 29.150, 29.146, 29.142)
    aplicados; ADR-069 anotado com a P-013 (R5 em sombra). Corte 44 em montagem: 30.76 (migração 117), 30.77 (118), 30.78,
    31.89 (+ painel), 31.13 (R5 em sombra; config central já em `apps: shadow`), 31.110, 30.85 e o script de nomes.
- **Deploy 42 no ar (05/10/2026, 23:33Z, central `cb6742d4`, sem migração nova; segue a 116).** Doze pontas sobre
  `095a43b6`: privacidade de nomes 31.105, 31.107, 31.96 e 31.109; canais 28.54; hooks no agente de nuvem 29.147; suíte do
  frontend 29.148 e 29.150; catraca de esperas 29.136 e 29.135; ensino 30.81 (painel) e 30.84 com a fatia 4 do 30.83.
  Detalhe no [CHANGELOG](../CHANGELOG.md) e no [livro do plano](execucao-plano-100-runner.md).
  - `real` (central WIN-7S2UASNLFOP): ensaio com a cópia `dataackups61005-202809`; deploy com `-PularBackup`;
    `GET /api/health` ok; prova de fora como esperado; agente do notebook em `0.1.0+cb6742d`; aparelhos 01, 03 e 06 online;
    hooks sem erro na primeira sessão após o deploy (29.147); primeira resposta do dono pelo app do Trello reconhecido
    aceita sem reenvio (28.54, entrada 2368).
  - `simulated` (suíte 42): números do CHANGELOG.
  - `not_run`: percurso no navegador (depois das pontas do corte 43); piloto do Copilot (29.137, 30.82).
  - Plano-100: resultado da suíte 42 e as leituras 31.26, 31.58 e 29.75 aplicados pelo mecanismo; IDs novos 31.109 e 29.151
    (662 itens). P-013 respondida (sim): a R5 em sombra entra no corte 44. Corte 43 em montagem sobre `cb6742d4`, com as
    frentes remergeando as pontas que nasceram de `095a43b6`.
- **Deploy 41 no ar (05/10/2026, 22:06Z, central `ac77742c`, migração `116_ref_publico_do_fluxo`, nova).** Dezesseis
  pontas sobre `65452966`, com o ensino na frente: 30.79, 30.80 B, 30.81, 30.83 (fatias 1 a 3), 28.50, 29.146 e 29.143;
  privacidade 31.98, 31.101, 31.102 e 31.106; consertos 31.76, 28.52, 29.139, 29.140 e 29.149, mais a anotação de tipo do
  30.81. Detalhe no [CHANGELOG](../CHANGELOG.md) e no [livro do plano](execucao-plano-100-runner.md).
  - `real` (central WIN-7S2UASNLFOP): ensaio com a cópia `data\backups\20261005-190117` (aplicou a 116 numa cópia
    restaurada, depois apagada); deploy com `-PularBackup`; `GET /api/health` ok e sem problemas; prova de fora com tudo como
    esperado na segunda rodada (a primeira, logo após a subida, teve uma falha transitória em `/api/login`, 404 em vez de 429);
    `/api/instances` 401 de fora e 403 com Host forjado; agente do notebook em `0.1.0+ac77742`; android-01, android-03 e
    android-06 `online` com automação `ready` depois da readoção, sem reinício, reset ou login (03 e 06 estavam em `error`
    desde a readoção do deploy 40, que dá 60 s e eles levam 65 a 75 s: item 29.151).
  - `simulated` (suíte 41 sobre `ac77742c`, central em Idle): `scripts/tests` 672 passed; backend em SQLite 11990 passed e 13
    skipped; frontend 1686 passed e build (o teste de data fixa do 29.149 passou); catracas 88 e 6; mypy 257, igual ao teto;
    PostgreSQL dirigido nos 474 arquivos afetados, em duas partes, 10398 passed e 0 falhas (as duas falhas conhecidas do
    29.139 sumiram); repetição dos 51 arquivos tocados pela anotação de tipo, 922 passed em SQLite e em PostgreSQL.
  - `not_run`: percurso no navegador das telas do ensino (frente Portal, logo após o deploy); prova real do ensino num app de
    teste (31.79).
  - Plano-100: resultado da suíte 41 e cinco itens já integrados sem estado (31.72, 31.73, 31.78, 30.71, 31.93) aplicados
    pelo mecanismo; IDs novos 28.54, 29.148, 31.107, 29.149, 29.150 e 31.108: 660 itens, 569 implementados. Conta nova desde
    20:27Z, com política de economia (Sonnet por padrão, Opus só no ensino e numa leitora, ramo só de teste ou texto lido por
    PR com a revisão automática do Codex).
- **Deploy 40 no ar (05/10/2026, 20:37Z, central `61d431ce`, migração `115_receita_nao_aplicavel`, sem migração
  nova).** Suíte mínima, de uma junção só (`2bce3b1e` sobre `8ac140e0`), feita na troca de conta para pôr no ar o
  conserto da senha pela web: a conferência do site antes de digitar lia o primeiro nó com o identificador da barra de
  endereço, e uma página de outro domínio que imitasse a barra receberia a senha (31.103). Itens: 31.75, 31.77, 31.104
  e 31.103. O detalhe está no [CHANGELOG](../CHANGELOG.md) e no [livro do plano](execucao-plano-100-runner.md).
  - `real` (central WIN-7S2UASNLFOP): ensaio com a cópia `data\backups\20261005-173228`, depois
    `scripts/deploy.ps1 -PularBackup`; `GET /api/health` ok e sem problemas; prova de fora com tudo como esperado
    (46 linhas ok; `/api/instances` 401 de fora e 403 com Host forjado); agente do notebook em `0.1.0+61d431c` às
    20:36Z (A10); android-01, android-03 e android-06 já estavam `online` antes da religação (readotados após o
    reinício do backend, nenhum start enviado), com pausa de reparo até 21:34Z.
  - `simulated` (suíte 40 sobre `61d431ce`, central em Idle): `scripts/tests` 672 passed; backend em SQLite 11875
    passed e 13 skipped; frontend 1678 passed na segunda rodada (a primeira teve 2 falhas intermitentes em
    `RunsPage.test.tsx` com a máquina carregada, item 29.148); catracas 88 e 6; mypy 257, igual ao teto; PostgreSQL
    dirigido nos 257 arquivos afetados, em duas partes, 5231 passed e 0 falhas (a parte 2 terminou sozinha no host
    durante a troca de conta).
  - `not_run`: o percurso no navegador (em curso pela frente Portal); a prova real do conserto com conta real (login
    pela web não provocado).
  - Plano-100: resultado da suíte 40 aplicado pelo mecanismo. Sessões da conta nova abertas pelo roteiro de
    `.claude/handoffs/orquestrador/abrir-sessoes.md`, com modelo e força por economia (fora do Git).
- **Deploy 39 no ar (05/10/2026, 18:06Z, central `9f9e2b39`, migração `115_receita_nao_aplicavel`, sem migração
  nova).** Dezessete pontas sobre `19e34b22`, mais dois consertos de junção. Itens: 29.128, 29.129, 29.130, 29.131,
  29.132, 29.138, 29.141, 29.144, 28.49, 31.92, 31.97, 31.99 e 31.100; em parte, 28.51 (A), 31.87 (F1), 31.90 (B) e
  31.91 (F0, F2 e painel). O detalhe de cada um está no [CHANGELOG](../CHANGELOG.md) e no
  [livro do plano](execucao-plano-100-runner.md).
  - `real` (central WIN-7S2UASNLFOP): avanço direto de `19e34b22` para `9f9e2b39`, com push; cópia
    `data\backups\20261005-150236` (194,1 MB, íntegra; não havia migração a ensaiar); `GET /api/health` ok e sem
    problemas; prova de fora às 18:07Z com tudo como esperado (`/api/instances` 403); agente do notebook em
    `0.1.0+9f9e2b3`; mypy 257, igual ao teto, pelo `scripts/mypy-catraca.py` (29.144); os papéis `perguntas` e
    `perguntas_respondidas` de `trello.listas` carregados na subida (28.51, parte A).
  - `simulated` (suíte 39 sobre `9f9e2b39`): `scripts/tests` 672 passed; backend em SQLite 11840 passed, com 3
    falhas consertadas antes do deploy e relidas (29.131 no `e6020a23`; a junção do 31.92 com o 31.91 no
    `9f9e2b39`); frontend 1678 passed e build; catracas 88 e 6.
  - Não passou inteiro: no PostgreSQL dirigido, 10182 passed e 2 failed conhecidas (item 29.139):
    `test_dialogos_em_serie.py`, que falha igual na `main` anterior, e `test_sobreposicao_com_duas_causas.py`, como
    na suíte 38.
  - `not_run`: o percurso no navegador do que entrou; a resposta do dono num cartão de pergunta sem pedido no
    Telegram, de ponta a ponta (28.51, parte A); a prova real do 31.91 e do 31.92 pelo painel.
  - Os três aparelhos de conta real ficaram parados das 17:05Z até depois do deploy, por decisão da orquestradora e sem apagar nada; a religação, um por vez pela API local, começou às 18:07Z.
  - Plano-100: 9 IDs novos (28.52, 29.140 a 29.145, 31.101 e 31.102) e o resultado da suíte 39 aplicado pelo
    mecanismo: 548 de 645.
- **Deploy 38 no ar (05/10/2026, 16:02Z, central `86afe1b5`, migração `115_receita_nao_aplicavel`).** Vinte e quatro
  merges sobre `ebc316f9`. Itens: 29.113, 29.117, 29.120, 29.121, 29.123, 29.124, 29.125, 29.127, 29.115, 29.118,
  29.119, 28.47, 28.48, 30.75, 30.80, 31.80, 31.82, 31.83, 31.84, 31.85, 31.94 e 31.95; em parte, 31.86 (B), 31.89
  (F1 e F6) e 31.90 (A). O detalhe de cada um está no [CHANGELOG](../CHANGELOG.md) e no
  [livro do plano](execucao-plano-100-runner.md).
  - `real` (central WIN-7S2UASNLFOP): avanço direto de `ebc316f9` para `86afe1b5`, com push; ensaio com a cópia
    `data\backups\20261005-125938` (192,7 MB, íntegra), que aplicou só a migração 115 numa cópia restaurada;
    `scripts/deploy.ps1 -PularBackup` com `rc=0`; `GET /api/health` ok e sem problemas (o `appium_log_masking_off`
    sumiu); prova de fora de 16:03:35Z a 16:03:43Z com `rc=0` e 46 linhas ok (`/api/instances` 401, e 403 com Host
    forjado); agente do notebook em `0.1.0+86afe1b` às 16:09Z (procedimento A10 de [`worker.md`](worker.md)).
  - `real`: android-01, android-03 e android-06, parados desde o incidente das 13:15Z, religados um por vez pela API
    local de 16:06:49Z a 16:18:53Z; na leitura de 16:23:47Z os três estavam `online`, com automação `ready` e
    internet `healthy`. A subida do 01 pegou a máquina a 92 % de CPU (testes das frentes ao mesmo tempo); os testes
    pararam e a pausa de reparo cobriu os três durante a subida.
  - `simulated` (suíte 38 sobre `86afe1b5`, em Idle): `scripts/tests` 669 passed; backend em SQLite com `-n 6`,
    11689 passed, 13 skipped e 1 failed (a catraca do `Any` do #442, consertada no `97dbf664`; dirigidos depois, 71
    passed); frontend com typecheck limpo, 1650 passed e build; catracas 88 + 6 passed; mypy 257, igual ao teto;
    PostgreSQL dirigido (473 arquivos, `-n 8`) com 9824 passed e **5 failed conhecidas** em `test_sobreposicao*.py`
    (8 com `-n 1`; 25 passed sem xdist, no PostgreSQL e no SQLite). **A etapa do PostgreSQL não passou inteira**; a
    causa está aberta no item 29.139.
  - `real` (volta no navegador pela frente do painel, ~16:20Z, só leitura e estado de tela): lote de Personas,
    Limites, Rede e revisão do ensino funcionam; oito achados viraram trabalho (31.90-B, 29.141 e 29.142).
  - `not_run`: os estados do ensino com o controle assumido (gravando, recusas, "Limpar o campo") no navegador; a
    prova real do ensino num aparelho de teste, autorizada às 16:25Z e ainda sem resultado.
- **GitHub Copilot no repositório (05/10/2026, item 29.134, `f5d9a096`).** Pedido do dono: usar o Copilot Pro+ para
  aliviar a carga das sessões e desta máquina. Instruções do repositório e ambiente do agente de nuvem no lugar; a
  revisão de PR é pedida PR a PR pela orquestradora. Como operar: [`operacao.md` § 5](operacao.md#5-ci).
  - `real`: três revisões em 05/10 (PRs de 756 a 1.549 linhas), 566,87 créditos (US$ 5,67), de 4 a 6 minutos cada, em
    runner hospedado; 10 achados, 9 confirmados pelas frentes. Por esse custo a revisão automática ficou desligada.
  - `not_run`: o agente de nuvem (a primeira tarefa ainda não foi atribuída).
- **Plano-100 em 636 itens, 535 implementados (05/10/2026, 16:25Z).** Entraram os 78 IDs das janelas 37 e 38, entre
  eles a Fase 33 (integração com serviço externo de autorização, 8 itens). As linhas 3.3, 6.2, 6.4 e 8.3 deixaram de
  citar quatro achados cujo texto saiu do apêndice no `ebc316f9`.
- **Subida a quente do 29.111 (05/10/2026, 10:40Z, central `7d104faa`).** A pedido do dono, o site público diz o que o
  nome ANA significa: **Agente Neural Avançada** (escolha dele, 05/10; registrada no ADR-075). A frase segue a regra da
  página: a ANA rege, as personas dão voz. Sem a forma em inglês; título, descrição e imagem social não mudaram.
  - `real` (central WIN-7S2UASNLFOP): #405 mesclado como `7d104faa` às 10:38Z; `scripts/deploy.ps1 -Ensaio` de 10:38:59Z
    a 10:39:03Z (cópia `20261005-073900`); `scripts/deploy.ps1 -PularBackup -PularDependencias` de 10:39:10Z a 10:40:18Z;
    prova de fora de 10:40:26Z a 10:40:35Z com `rc=0` e 46 linhas ok (`/api/instances` 401 e 403; `site.css` e
    `site.js` com a mesma versão); página pública no navegador às 10:41Z com as duas frases novas, sem as antigas, 5
    recursos e nenhum de fora, sem rolagem horizontal em 375 px.
  - `simulated`: `backend/tests/test_portal_site.py` (22 passed) e `scripts/tests/test_portal_prova_de_fora.py` (26 passed).
  - `not_run`: captura de tela da página (o navegador embutido não desenhou; a conferência foi pelo texto e pelo DOM).
- **A/B do 31.71 medido (05/10/2026, 10:15Z a 10:26Z, central `e5f1b22b`).** A imagem enquanto falta saída
  (`ai.imagem_enquanto_falta_saida`) rodou ligada e desligada no android-01, leitura no Outlook, 5 execuções por braço.
  - `real`: desligada, 5 de 5 concluídas, média de 5,4 decisões e US$ 0,0662 por execução; ligada, 5 de 5, 4 decisões
    e US$ 0,0509 (23 % a menos), com o motivo `leitura_pendente` presente. Custo total US$ 0,5856. A chave voltou a
    desligada e o `config.yaml` ficou igual, byte a byte, à cópia de antes.
  - A chave **não vira padrão ainda**: falta a segunda medida num fluxo longo, com a saída lida só na última etapa
    (3 por braço, aparelho de teste, depois do deploy 37).
- **Deploy 36 no ar (05/10/2026, 09:48Z, central `e5f1b22b`, migração `113_execucao_assentada_em`).** Dezesseis PRs:
  a execução que espera a pessoa e o assentamento uma vez só (29.93, 29.103; migrações 111 e 113), o vigia da borda e da
  API (29.97, 29.101), sessão e Pendências (29.96, 29.100; migração 112), avisos (28.41), aprendizado (30.69),
  execução (31.70, 31.71 atrás de chave desligada, 31.74), testes e painel (29.99, 29.104, 29.106, T.2).
  - `real` (central WIN-7S2UASNLFOP): fast-forward e push às 09:39:34Z; `scripts\deploy.ps1 -Ensaio` de 09:39:43Z a
    09:39:46Z (cópia `20261005-063944`); `scripts\deploy.ps1 -PularBackup -PularDependencias` de 09:40:43Z a 09:42:34Z,
    primeira subida de verdade sem cópia própria (29.94), com a cópia do ensaio valendo; agentes dos dois workers em
    `0.1.0+e5f1b22`. Depois da migração 113, as 493 execuções finais ficaram com a marca de assentamento e nenhuma
    estava em `cancelling`. Prova de fora de 09:42:44Z a 09:42:51Z com `rc=0` e 46 linhas ok (`/api/instances` 401 e
    403). Painel pelo nome público, 11 telas, sem erro, sem violação da política de conteúdo e sem recurso de fora. A
    primeira volta do vigia da borda saiu limpa às 09:47:31Z, e a saúde ficou sem problema de portal. O dirigido em
    PostgreSQL rodou inteiro numa parte só (9998 passed, 13 skipped, 30 min, pico de 413 MB dos 4096 MB): o 29.99 fecha
    a regra provisória das duas metades do K-100, sem provar a causa do estouro da suíte 35.
  - `simulated`: suíte 36 na `e5f1b22b` (scripts 631 passed; SQLite 11276 passed e 13 skipped; PostgreSQL 9998 passed e
    13 skipped; frontend 1612 passed; catracas 88 e 6 passed).
  - `not_run`: uma execução real que pare esperando a pessoa e saia da espera (29.93); um cancelamento sem worker vivo
    (29.103); no editor de grupo, "só vale a última" e a trava durante a leitura com dado real (29.106: os perfis do
    central rendem o mesmo rascunho); defeito de borda ou API aberta de verdade, que não se provocam (29.97, 29.101);
    o A/B da imagem enquanto falta saída (31.71) e o `wait_for` numa tela lenta (31.74).
- **Subida a quente do 29.110 (05/10/2026, 08:47Z, central `9f00c122`).** `real`: `deploy.ps1 -Ensaio`
  às 08:45:50Z e `deploy.ps1 -PularDependencias` de 08:45:54Z a 08:47:31Z; prova de fora de 08:47:39Z a 08:47:46Z com
  `rc=0` e 46 linhas ok; página pública conferida no navegador às 08:48Z, com 9 seções e 5 recursos,
  nenhum de fora.
- **Deploy 35 no ar (05/10/2026, 06:28Z, central `d025b671`, migração `110_passou_a_porta`).** Dezenove PRs: avisos e
  canais (28.37 a 28.40), execução e planejamento (31.56, 31.64 a 31.67, 31.69, 31.70), portal (29.89, 29.91, 29.95),
  sessão e aprendizado (29.90, 29.92, 30.70), implantação e testes (29.94, 29.98).
  - `real` (central WIN-7S2UASNLFOP): fast-forward às 06:16:52Z; `scripts\deploy.ps1 -Ensaio` limpo às 06:17:06Z e
    `scripts\deploy.ps1 -PularDependencias` no ar às 06:18:46Z (cópia `20261005-031723`); agentes dos dois workers em
    `0.1.0+d025b67`. Proteção do painel (29.91) em dois tempos: com `server.csp_do_painel: so_relatar`, a prova de fora
    das 06:22Z deu 45 linhas ok e a única falha esperada, e a caminhada do painel pelo nome público (15 visitas de rota)
    não registrou violação; com a chave em `aplicar` e a `farm-central` reiniciada (06:25:47Z a 06:26:44Z), a prova de
    fora das 06:27Z deu `rc=0` com 46 linhas ok, e o navegador mostrou a política aplicada, sem violação em 4 telas. As
    duas provas conferiram a versão no endereço do CSS e do JS do site (29.95). A volta diária do backup rodou às
    06:00:02Z com resultado 0 (29.38).
  - `simulated`: suíte 35 na `d025b671` (scripts 616 passed; SQLite 10925 passed e 13 skipped; PG dirigido em duas
    metades, 9671 passed e 13 skipped; frontend 1601 passed; catracas 86 e 6 passed).
  - `not_run`: no central, sob a política aplicada, o quadro do aparelho desenhado, o upload de foto e a tela de Anexos
    (a aba do navegador estava oculta; há prova `simulated` no painel isolado); `deploy.ps1 -PularBackup` numa subida de
    verdade (29.94); o aviso de objetivo parado (28.40), o de sessão não reconhecida no teto (29.92) e a marca "passou a
    porta" (31.64) num caso real.
  - Achados da subida: o PostgreSQL rápido de teste não comporta os 467 arquivos de uma vez (29.99, K-100); dois PRs
    chegaram a final com uma catraca vermelha (29.98, K-098); shell de segundo plano parado deixa filhos sem console
    (K-099). A retenção do backup diário (14 dias) começa a apagar cópias perto de 18/10: pergunta aberta ao dono.
- **Deploy 34 no ar (05/10/2026, central `584ac9c8`, migração `109_portal_exclusoes`).**
  - `real` (central WIN-7S2UASNLFOP): fast-forward às 03:18:38Z; `scripts\deploy.ps1 -Ensaio` limpo às 03:18:49Z e
    `scripts\deploy.ps1 -PularDependencias` de 03:19:59Z a 03:21:23Z (cópia `20261005-002000`); agentes dos dois workers em
    `0.1.0+584ac9c`; prova de fora às 03:21:37Z (41 linhas ok, a raiz pedida como navegador sem script de outra origem);
    caminhada no navegador do site e do painel (Configuração, "Site e privacidade"; guia de imagens da persona com "Esta foto
    foi feita por IA?"; Infraestrutura, Canais e Pendências).
  - `simulated`: suíte 34 na `584ac9c8` (scripts 602 passed; SQLite 10748 passed e 13 skipped; PG dirigido 4965 passed e
    11 skipped; frontend 1598 passed).
  - `not_run`: a exclusão de um contato real (só a pedido do titular), o envio de foto com a resposta, a prévia de plano com
    `vista_em` numa execução real e a folha de aviso do Instagram num aparelho real (29.87).
  - Achados da subida: `deploy.ps1 -PularBackup` recusa no primeiro segundo (29.94); a borda guarda `/assets/site.css` e
    `/assets/site.js` por 4 h, e quem já visitou o site vê o estilo antigo nesse intervalo (29.95). O 29.85 fechou: o Web
    Analytics da zona foi desligado em 05/10 (~02:24Z) com o sim do dono, e a prova de fora reprova script de outra origem.
- **Deploy 33 no ar e portal institucional LIGADO (05/10/2026, central `a0c9865e`, migração `107_portal_contatos`).**
  - `real` (central WIN-7S2UASNLFOP): fast-forward e `scripts\deploy.ps1 -PularDependencias` de 01:10Z a 01:12Z, agentes dos dois
    workers em `0.1.0+a0c9865`; prova de fora às 01:14:34Z com o site desligado e às 01:19:51Z com
    `SITE=ligado CONTATO=ligado WEBHOOK_DO_TRELLO=ligado bash scripts/portal-prova-de-fora.sh depois` (tudo como esperado);
    caminhada no navegador do painel (10 telas) e do site (1440 e 360 px).
  - `simulated`: suíte 33 na `a0c9865e` (SQLite 10624 passed, PG dirigido 5232 passed, frontend 1582 passed).
  - `not_run`: o primeiro contato real pelo formulário (é do dono), a prévia de link num chat real, o item com cadeado da
    aprovação do plano e o selo de rótulo de IA com dado real.
  - O site ligou com o sim do dono (Telegram, 05/10 00:59:01Z). Desligar: `portal.site_ligado` no `config.yaml` e reinício
    ([operacao.md](operacao.md), "Site institucional na raiz"). Pendente: 29.85 (a Cloudflare injeta a tag do Web
    Analytics na raiz; a CSP do site bloqueia).
- **Este arquivo ficou sem atualização de 02/10 a 05/10.** O que mudou nesse intervalo está no [CHANGELOG](../CHANGELOG.md),
  em [execucao-plano-100-runner.md](execucao-plano-100-runner.md) (512 itens, 457 implementados) e em
  [decisoes.md](decisoes.md) (até o ADR-075). Os blocos abaixo são de 02/10.
- **Fase 29 (pendências da terceira evolução): fechamento documental feito (29.18, 02/10/2026); a fase NÃO fecha ainda.** Resumo real × simulado × `not_run` dos 20 itens e a ação exata de cada pendência em [relatorio-validacao.md §28](relatorio-validacao.md). Das quatro cláusulas do "Fecha quando", três estão cumpridas (6 h do P16 sem reinício, `real`; Outlook no login pelo serviço, `real`; pendências com ação exata). Falta **uma**: CI verde no commit publicado. O cron de 02/10 05:28Z passou em tudo menos no job de documentação (link para o handoff local, aviso de vocabulário), já corrigidos na integração; nenhum run os cobriu e o implantado (`f9eed71`) não tem run. Ação (dono ou execução, custo zero): `workflow_dispatch` na `main` depois do merge, ou o cron de 03/10 05:17Z. Pendências com dono: 29.7 e 29.19 (servidores e escala; adiados pelo dono, `not_run`); 29.13 (e-mail de teste do dono para o Bruno ou o André e leitura do assunto pela captura; 27.2); 29.9 (W8 com `exigida_com_bloqueio` no android-09; o `PASS` de 02/10 é só em `livre`); 29.12 (android-14 e 15 ao ligarem); 29.20 (`simulated`, na integração `a0a03b7`, sem merge nem deploy; real: medir o central e conferir o android-09 depois do deploy).
- **A8, A9 e A10 EXECUTADOS em real (02/10/2026, ~16:20Z–16:30Z, central `f9eed71`, deploy com backup `20261002-132234`/`132239`).** A8: a tarefa `farm-tunel-192.168.1.11` foi reinstalada e roda o Windows PowerShell 5.1 (não mais o `pwsh` da Store). A10: o agente do notebook foi para `0.1.0+f9eed71` (`agent_outdated` falso). A9: o notebook estava +8,857 s atrás do NTP.br; a tarefa `farm-relogio` (SYSTEM, 15 min) o levou a +0,002 s e o `degraded` "relógio desalinhado" saiu sozinho pela medição por batida. Procedimento e limites em [worker.md](worker.md#o-relógio-do-worker-e-as-tarefas-do-notebook-02102026-real). O A11 (o desfazer espera o `stopped`) está nesse deploy. O runtime do central é `f9eed71`; a `main` tem os merges do Jev depois dele (não implantados).
- **W8 (android-09: o túnel VPN que não volta depois do boot): mitigação do PR #17 PROVADA em real, `PASS` (02/10/2026, rodada r4, central `bcea158`).** Com a política `livre` + `vpn-central-wireguard` e o always-on no android-09, o `tun0` não subiu sozinho em 2 de 2 boots válidos e o produto o religou com UM Start pela interface do cliente, sem reinício extra (r3 it.1: 198 s; r4: 193 s). Causa raiz do `SILENT_STOP` segue `NARROWED`; o PASS prova a mitigação, não a causa. Pausa do reparo por aparelho (A2) e protocolo r3/r4 usados; 4 de 6 reinícios reais, rollback com `force-stop` do SFA fez 1 reinício. **No ar:** `bcea158` (PR #17, A2). **Na `main`, NÃO implantado:** A9 (re-medida do desvio do relógio do worker por batida), A11 (o desfazer da rede espera o `stopped` e para o cliente que religou sozinho) e o instalador do túnel sem `pwsh` da Store (A8, código; a tarefa `farm-tunel-192.168.1.11` do central ainda aponta para o caminho da Store). Detalhe e evidência: [handoffs/w8-boot-recovery.md](handoffs/w8-boot-recovery.md) §17.5–17.10.
- **Context Retrieval (ADR-063): PR #18 MERGEADO na `main` (`c1046db`, 02/10); NÃO implantado; desligado por padrão (`context_retrieval.enabled: false`).** Prova `real` (02/10, central, `python-poetry/poetry` @ `94b6e35`, commits `a07ff80` e `a88d609`): smoke público no Jev, 11 de 12 chamadas, todas 200, 0 fallbacks, ~US$ 0,006; H14 exerceu a etapa A e a B, H13 só a A (B bloqueada por `budget_exceeded`). Código deste repositório nunca saiu (`PRIVATE_CODE_SEND_APPROVED = False`). Dívidas para habilitar o remoto em [dominios/context-retrieval.md](dominios/context-retrieval.md) (Limites conhecidos). Backend completo no HEAD `a07ff80`: 4576 ok e 1 falha preexistente de worktree (`test_instalacao_do_worker`, reproduzida na `main` limpa).
- **Revisão de UX/UI do portal, rodada 2 (01/10): INTEGRADA NA `main` e IMPLANTADA (só o frontend, 01/10 12:23, hora local do build).** Seis briefings do dono (cabeçalho compacto no
  celular, texto cortado com tooltip, detalhe da persona em 5 seções e com nome legível na URL, acessibilidade residual,
  resumo da execução e regra de pendências, revalidação) mais a rodada de correções do revisor. Relatórios em
  [revisoes-ux/rodada-2/](revisoes-ux/rodada-2/); veredito em [revalidacao-final.md](revisoes-ux/rodada-2/revalidacao-final.md):
  pronto com ressalvas (0 altos; os 2 médios, M1 e M2, foram corrigidos em `16-correcoes-finais.md`).
  - **Delta contra o runtime (conferido em 01/10, tarde):** `git diff --name-only 5d8b545 main -- backend` dá 0 arquivos e não há migração
    nova; o delta é só `frontend/src` (66 arquivos), docs e `scripts/ui-truncamento.js`. A implantação exige **somente reconstruir
    `frontend/dist`** (`npm run build`), sem `deploy.ps1`, sem reinício do backend e sem tocar nos aparelhos.
  - **Prova `real` mínima do deploy (01/10, tarde, sem ação em conta ou aparelho):**
    - SOURCE MAIN: `689d611` (código-fonte do painel idêntico ao `89dbc91`; os dois commits desde o `73fef1b` são docs).
    - BACKEND RUNTIME: `5d8b545`, migração `063_prova_de_vazamento`, `/api/health` ok e `problems: []`; os processos `app.main` seguem os
      mesmos (pids 52380 e 22060, início 10:43:39): **backend não reiniciado**; sem `deploy.ps1`, migração ou toque nos aparelhos.
      O `/api/health` continua devolvendo `5d8b545` porque o commit que ele reporta é o do processo Python, não o do `dist`.
    - FRONTEND BUILD: `npm run build` (typecheck + vite) na `main` atual, 01/10 12:23. Servido em `http://127.0.0.1:8000/`:
      `index-DLhgeA_e.js` e `index-Cq9EBtZT.css` (antes: `index-lmjapcAt.js` e `index-BGwg5pnm.css`), ambos 200 e com o tamanho do arquivo.
    - Sinais da rodada 2 no bundle servido: "aguardando você" 14x e "esperando você" 0x (B7; antes 6x); chip "Planejadas" da rodada 1
      mantido; CSS do `TruncatedText` (`_clamp_`/`_linha_`, módulo `feaoa`) presente. `GET /api/snapshot` 200 (72 KB). Conferido por HTTP,
      não por aba aberta (há cache heurístico do `index.html`: abas antigas pedem recarga forçada).
    - `not_run`: o que já estava `not_run` na prova da rodada 2 (semáforo "Atenção", custos no topo, execução em andamento, origem
      Intervenção, leitor de tela e toque real) e a navegação autenticada no painel servido.
  - **Prova:** `simulated` (frontend 94 arquivos/1133 testes na árvore mesclada com a `main`); `real` só contra o backend simulado
    do worktree (matriz 9 telas x 6 larguras, axe 4.13, Lighthouse 13.5 em Painel e Personas); `not_run`: semáforo no nível
    "Atenção", custos no topo, execução em andamento, origem Intervenção, leitor de tela e toque real.
  - **Abertos (baixos):** B9–B11 da revalidação e RF-07r, 19, 27, 30, 32, 35, 41, 42, 48, 49 da rodada 1.

- **Revisão de UX/UI do portal, rodada 1 (01/10, madrugada): INTEGRADA NA `main` e IMPLANTADA no runtime `5d8b545`.** Nove tarefas dos briefings do dono
  (menu lateral e rotas por objeto, números e semáforo numa fonte única, barra de seleção e drawer, textos e cards, busca e
  tabela, caixa única de Pendências, tipografia e acessibilidade, varredura de textos, revisão final) mais correções e
  decisões do dono delegadas à IA. Relatórios em [revisoes-ux/](revisoes-ux/) (veredito em
  [revisao-final.md](revisoes-ux/revisao-final.md): pronto com ressalvas); decisões em ADR-062.
  - **Implantação (corrige o registro anterior, que dizia "não implantada"):** o backend da rodada 1 (`GET /api/snapshot`:
    as 20 execuções recentes, todas as `needs_input` e as não terminais, exceto `planned`) já roda: `8bcedc5`, `cade559` e `368b575`
    estão dentro do `5d8b545`. Runtime observado em `GET /api/health` (01/10, tarde): commit `5d8b545`, migração
    `063_prova_de_vazamento`, status `ok`, `problems: []`. O bundle servido então (`index-lmjapcAt.js`, build de 01/10 09:27) tinha o
    chip "Planejadas" da rodada 1. **Não há mais necessidade de reiniciar o backend para a rodada 1.** O `83af733` é só
    documentação e não prova deploy; a associação ao deploy foi **inferida** pelo commit devolvido por `/api/health` e pelo início
    dos processos `app.main` (01/10 10:43), não por registro de deploy; não há hora exata registrada.
  - **Prova:** `simulated` (frontend 85 arquivos/1027 testes na árvore mesclada com a `main`; backend 514 testes dos arquivos
    que citam o snapshot, em SQLite; backend simulado na 8765 com axe 4.13 em 54 combinações + 15 visões internas, zero falhas de
    contraste, e Lighthouse de acessibilidade 99–100) [revisoes-ux/13-prova-simulada.md](revisoes-ux/13-prova-simulada.md);
    `real` só de leitura no central (30/09, 1440/1024/390 px, sem login). **PostgreSQL do SQL novo do snapshot: PROVADO (`real`).**
    Run 36830963968 (`workflow_dispatch`, `somente_postgres=true`, commit `7c3b787`, 01/10 07:33–08:19Z, `postgres:17`): 3962 passed,
    28 skipped, 0 failed; `368b575`, `cade559` e `8bcedc5` são ancestrais de `7c3b787`, que é ancestral do runtime `5d8b545`.
    O teste `test_tools_and_api.py::test_snapshot_traz_as_recentes_e_todas_as_em_andamento` (sem skip) está nessa suíte, e o
    `_SQL_RUNS_DO_SNAPSHOT` não mudou depois. O run anterior, 36822159704 (`63b2753`), teve 3 falhas alheias ao snapshot (`rowid` no
    teste, `normcase` e `abspath` em Linux), corrigidas até o verde; ver [relatório §27.1](relatorio-validacao.md). O run em si
    não foi relido nesta sessão além do `gh run view`; a prova é a do relatório. **Não é mais lacuna do snapshot.**
  - **Execuções antigas (resolvido):** as 3 `planned` e as 27 `needs_input` de 17/09 a 28/09 **já foram canceladas em 01/10**, com
    autorização do dono registrada em [handoffs/execucoes-canceladas-20261001.md](handoffs/execucoes-canceladas-20261001.md):
    lote 1 (17 `needs_input`, ~10:16Z) e lote 2 (10 `needs_input` + 3 `planned`, ~12:17Z). Em todas `started_at` é nulo, nenhuma
    etapa chegou a `succeeded`, não há aprovação pendente nem efeito externo registrado. **Não resta decisão de cancelamento
    para o dono**, e não há `planned` nem `needs_input` no central. Achados baixos abertos (RF-11, 16, 17, 23, 07r, 14, 27, 30, 32–35, 37, 38) em [revisao-final.md](revisoes-ux/revisao-final.md).

- **Lacuna de auditabilidade (01/10): mutação pela rota sem sessão identificada.** Os 30 cancelamentos (e o do C1 `r-20261001124036-996716`)
  passaram por `POST /api/runs/{id}/cancel`, e `learning_signals` tem 30 sinais `cancelou_execucao` com `created_by=panel`: sem
  sessão, `panel` quer dizer "operador não identificado", não uma pessoa. Os tempos (~180 ms e ~17 ms entre chamadas) indicam
  chamadas programáticas em sequência. **O banco não prova o autor**; só o registro documental (autorização do dono, nos
  handoffs acima e no checkpoint 10 da Fase 29) liga os lotes à sessão de coordenação da Fase 29. Não é incidente de segurança, só
  trilha incompleta, e já é limitação conhecida do ADR-016 (item 9.1; 22.1 cobriu só os gestos com sessão): sem item novo.
  O C1 recente (execução real, paga, sem efeito externo) está no checkpoint 11 de
  [handoffs/pendencias-evolucao3.md](handoffs/pendencias-evolucao3.md).

- **Pendências da terceira evolução (30/09): Fase 29. No ar em `9ff427c`** (primeiro deploy `0d70882` às 15:24Z,
  segundo `f6c7df2` às 21:33Z e correções medidas até 22:56Z). Estado por tarefa, pedidos ao dono e checkpoints em
  [handoffs/pendencias-evolucao3.md](handoffs/pendencias-evolucao3.md); provas no [relatório §27](relatorio-validacao.md).
  - **Real:** CI verde; P16 (6 h sem reinício de aparelho pela rede, remedição com a prova da linha); renderizador
    `host` em todo o parque, lido do log e com recusa por app; Outlook promovido e `ready` em 8 aparelhos; as três
    contas Outlook logadas no aparelho do Instagram de cada persona (01, 03, 06), Instagram conferido depois da troca;
    C1 Outlook → Instagram só de leitura no android-01.
  - **Falta (01/10, atualizado depois do checkpoint 12):** C1 num aparelho com rede verificada (27.2: o e-mail de teste não chegou à
    caixa do Bruno). As tentativas pagas no android-03 falharam; a última, `r-20261001124036-996716`, custou ~US$ 0,26 com
    autorização específica do dono de até US$ 0,60 e não foi repetida. O total pago desde 29/09 é ~US$ 2,37, acima da
    autorização de 29/09 (US$ 1,50) ([checkpoint 11](handoffs/pendencias-evolucao3.md)); nova tentativa paga só com nova
    autorização. **W4 (29.9): a causa foi achada e corrigida em `658e5bb`** (implantado 12:59Z): a observação contava o cabeçalho
    "Lockdown filtering rules:" do dumpsys como regra de bloqueio, e a rede reiniciava o aparelho em cadeia; a hipótese de memória
    do notebook caiu (K-067; `max_slots` 3 segue aplicado como mitigação reversível, sem Windows alterado). No android-09 W2–W7
    ficaram `real` (13:08Z) e o bloqueio do W8 foi provado (13:26Z), **mas depois do teste de vazamento o túnel não voltou**
    (rollback 13:37Z): essa é a pendência da rede no notebook, não a memória (checkpoint 12). As 10 execuções antigas de mensagem a
    terceiros e CETESB foram canceladas em 01/10 sem efeito externo (ver acima); D8 (firewall do notebook) feito pelo dono. PostgreSQL verde
  (29.14). Adiado pelo dono: saída própria por aparelho (29.7, 29.19, 29.20).

- **Terceira evolução (30/09, madrugada): EXECUTADA até onde depende só da IDE.** No ar em `e7d44ce` (central e agente
  do notebook), migração 058. Estado por frente, pendências P1–P15 e próxima ação em
  [handoffs/terceira-evolucao.md](handoffs/terceira-evolucao.md); provas em [relatório §26](relatorio-validacao.md).
  - **Real:** rede por aparelho em `trafego_verificado` no android-05, 02, 06 (André) e 03 (Bruno), com o bloqueio fora
    da VPN provado; comando entre apps (QA Messenger → Chrome) com saída reaproveitada, conta indisponível e reinício no
    meio; contas Outlook das 3 ativas com a senha clonada no cofre.
  - **Pendente (P15), diagnóstico corrigido em 30/09:** o Outlook 5.2635.3 derruba o emulador quando o GLES é o
    SwiftShader do host; com `-gpu host` ele abre (K-062). Não é recusa do app nem decisão de produto: falta levar a
    GPU do host aos aparelhos (Fase 29, itens 29.10 a 29.12). Seguem sem prova por isso: 23.2, 23.7, 23.8, 23.12,
    23.13, 24.9 (recorte Outlook), 27.2.
  - **Para o dono:** a lista atual está em [handoffs/pendencias-evolucao3.md](handoffs/pendencias-evolucao3.md)
    ("Pedidos ao dono"): regra de firewall e reserva DHCP para os aparelhos do notebook, servidores para o piloto de
    saída distinta, consentimento das 3 contas Outlook e o e-mail de teste.

- **Fase 22 (29/09, noite): IMPLANTADA em `b34e2f6`** (~17:05Z; sem migração nova; `/api/health` `ok`; agente do
  notebook em `0.1.0+b34e2f6`; leva junto o layout da outra sessão, `13fb5c0`). [Relatório §25](relatorio-validacao.md),
  adendo v0.40, K-061.
  - **Feito:**
    - operador da sessão nos gestos (22.1) e nota do comando triada só no texto da pessoa (22.2);
    - `failure_screen` gravado sem falso corrigido no backlog (22.3);
    - adoção de fluxo com trilha (22.4) e savepoint nas lojas com a defesa `TransacaoAbortada` (22.5);
    - preferência no bloco da execução (22.6);
    - "Corrigir esta etapa" na aba "Por aparelho" (22.7);
    - o 503 do saldo era o incidente da Anthropic (22.8);
    - 28 worktrees removidos com segurança (22.9).
  - **Provas:** simuladas em quase tudo; nenhum gesto real foi fabricado. O SQL novo passou em PostgreSQL 17 real
    (subconjunto, 656 ok). A suíte inteira em PostgreSQL 17 deu 3497 ok e só a falha de ambiente do `test_backup` (1 h 33 min).
  - **Docker/WSL:** ligados para a suíte em PostgreSQL e desligados às ~18:41Z. O contêiner rápido `farm-pg-rapido` (dados em
    memória, só `127.0.0.1:55434`) é o jeito de rodar a suíte aqui; o `farm-pg` com disco faz ~7 testes/min.
  - **Decisões do dono:**
    - trilha da adoção: registro do gesto (atual) ou acessória;
    - registrar a segunda pessoa que repete um gesto;
    - as polaridades dos três sinais.
- **Painel: "Resultado por instância" em cartões e "Outros dados" do Diagnóstico legível (29/09, pedido do dono):
  IMPLANTADO** (`13fb5c0`, no ar dentro de `b34e2f6`, deploy da Fase 22). Só painel. Prova `real`: capturas da 6eb84c ›
  Relatório e do Diagnóstico › Outros dados no central (~17:10Z); `simulated`: vitest 751/751. Detalhe em
  [produto.md §3](produto.md) e no CHANGELOG. Falta o olho do dono.

- **Código em aberto de 29/09 (tarde): IMPLANTADO em `c071341`** (~14:10Z; sem migração nova; agente do notebook em
  `0.1.0+c071341`). [Relatório §24](relatorio-validacao.md), adendo v0.39 do contrato.
  - **Real:**
    - `PUT /api/flows` grava a trilha com a pessoa (fluxo de QA, `published → disabled → published`, 14:13Z);
    - `GET /api/runs/{id}/feedback` traz o bloco `aprendizado` (a 6eb84c mostra as 2 receitas usadas e o fluxo);
    - o painel mostra o bloco e "Normal medido para este plano" com a janela efetiva;
    - a causa do irq ocioso foi medida (K-060): dois terços são o Instagram logado rodando, um terço o tempo no ar, e
      não há mecanismo novo.
  - **Simulado:**
    - os três sinais (`cancelou_execucao` por episódio, `comando_incerto_resolvido`, `correcao_de_ensino`) com o
      operador da sessão;
    - a nota de comando triada (409 `note_looks_secret`);
    - a dívida `devices` → `taskqueue` paga (`app/shared/costuras.py`).
  - **CI de contêiner:** recusa de cobrança, não código (reteste na semanal de 05/10).
  - **Para o dono:**
    - ratificar as polaridades dos três sinais (ADR-054);
    - a correção de ensino não tem tela no painel;
    - o `/api/health` está `degraded` só por `ai_balance_stale` (o relatório de uso da Anthropic respondeu 503) e o
      saldo estimado da Anthropic está em US$ 3,31.

- **Rodada de 29/09 — proteção de contas (ADR-055, Fase 21) e aprendizado contínuo (ADR-054, Fase 20): IMPLANTADA.**
  - **No ar:** `f497075` desde 29/09 ~07:38Z (sem migração nova; `/api/health` `ok`, `problems: []`; agente do
    notebook em `0.1.0+f497075`). Antes: `9348e9c` às ~04:17Z (o reparo espera a máquina aliviar); `c359f65` +
    `2511b12` às ~03:55Z (migrações 054 e 055; quarentena, uma conta por alvo, disjuntor,
    verificador de DM, fundação do aprendizado); a leva aberta `7a02491` e `e9da86e`.
  - **Aprendizado (20.3–20.10):** costuras e `failure_kind` gravado, "o que mais falha" com backlog, botão "Deu certo /
    Deu errado", D1 no fluxo e na receita, página Aprendizado, lições medidas (`shadow`), telas aprendidas (`observe`),
    voz e preferências (`off`). Real só de leitura: livro com 125 itens, `/revisar` 39, `/pendentes` 0; "o que mais
    falha" com 19 grupos acima do mínimo, todos do legado classificado na leitura. Lições, telas, voz e preferências
    reais: `not_run` até as execuções acumularem dados. Domínio: [dominios/aprendizado.md](dominios/aprendizado.md);
    provas: [relatório §23](relatorio-validacao.md).
  - **Parque:** apps do Google desativados no preparo (21.15, K-059; `MemAvailable` de 670–960 para 974–1054 MB) e o
    reparo que espera a máquina aliviar (21.16, `9348e9c`, K-058).
  - **Incidente (29/09, 02:05–02:15Z):** com a máquina saturada pela IDE, a escada de reparo do central (ainda em
    `7a02491`) deu `restart` e `reset` no android-01 e apagou o Instagram e a sessão da «conta do android-01»
    (`c-20260929021534-6d15cd`). A conta não foi tocada desde então; o android-01 está sem o app. Conduta: um trabalho
    pesado por vez no central, testes em prioridade ociosa, Docker e WSL desligados depois dos testes em PostgreSQL.
  - **Decisões do dono pendentes:**
    - quando e como reativar o lucas. Recomendação: um único login acompanhado por ele, pelo Conectar do painel, num
      horário calmo da máquina;
    - trocar as senhas das 3 contas vivas e apagar o transcrito local de 18/09 que as guarda em texto puro
      (`C:\Users\Administrator\.claude\projects\C--git-android\1a884c21-6b61-42ac-8c11-dfd781e97346.jsonl`, fora do
      repositório);
    - remover ou não do cofre as credenciais das 5 contas bloqueadas;
    - as escolhas do pacote frota (ADR-055).
  - **Atenção:** antes de qualquer experimento num aparelho com Instagram, screencap e conta logada; `account_label` e
    `/personas` não bastam (K-053). `hide_error_dialogs` fica em 1 (K-054). Um trabalho pesado por vez no central
    (K-058).
  - **Próxima ação:** as propostas ao dono (as Fases 20 e 21 já estão no estado do plano, `b70fba0` e `a98044c`):
    o 18.9 (nomes históricos: a tabela `instagram_profiles` e as rotas `/api/instagram/…`) e o 12.3 (apps novos
    operando de verdade: a persona com mais de um app com login gerenciado; qual app vem primeiro é do dono). Seguem
    abertos: as pendências dos revisores do ADR-055 e as de A2–A9
    ([dominios/aprendizado.md](dominios/aprendizado.md#pendências-conhecidas)). As que tocam invariante vêm primeiro: o
    escritor de `failure_screen`, a adoção de fluxo por habilidade sem trilha e a trilha dentro da transação das lojas
    no PostgreSQL. A causa do irq (21.15), a trilha do interruptor antigo e o texto do `_learn_flow` foram fechados em
    29/09 (tarde).

- **Falhas reiteradas do Instagram (ADR-053, Fase 19): IMPLANTADO em 28/09 (`93967d0` + `91f1aab`, central e agente do
  notebook).** Diagnóstico medido de e31953 e 02ee9e (a IA ocupou 4,5% do tempo; o resto era o convidado saturado e o
  código transformando lentidão em falha) e 9 correções: UI ocupada relê sem recriar a sessão, ANR com sinal próprio,
  prévia fora da fila do aparelho, recuperação que preserva o estado, digitação atômica, porta de sessão, alvo pela
  legenda, aprendizado só com prova e reinício a frio por interrupção acumulada. Prova `real` no android-06:
  navegação `r-20260928234657-bbdf3c` (89 s, 3/3) e, com autorização do dono, curtir e comentar outro post em
  `r-20260928235215-6eb84c` (2 min 41 s, 5/5, comentário inteiro); 0 recriação de sessão e 0 ANR novo nas duas
  ([relatório §21](relatorio-validacao.md)). **Atenção:** o post "Ainda sobre Setembro Amarelo 2024" está curtido
  pelo andre desde a e31953, e um LIKE repetido DESCURTE; antes de prometer aprovação de texto, confira a política do
  perfil (o do andre publicava comentário sem aprovação até 29/09, K-052). O relógio do host, o `hide_error_dialogs`
  (fica 1) e as pendências dos revisores foram tratados na rodada de 29/09 (Fase 21); segue aberta a guarda de
  cartão num LIKE real.

- **Fase 18 (ADR-052): fatias 1–4 IMPLANTADAS em 28/09 (`a7fe364`, central e agente do notebook); zero Python por
  app.** Prova `real`: "Verificar conta" pelo motor genérico confirmou lucas (android-01) e andre (android-06)
  ([relatório §20](relatorio-validacao.md)); login digitando senha e volta ao estado conhecido reais: `not_run`. As fatias 2–4 tiraram o Instagram do código: ele é
  a pasta `backend/app/conhecimento/apps/com.instagram.android/`, descoberta pelo registro; `integrations/instagram/`
  e `planning/catalog/instagram.py` não existem mais. O bloco `instagram:` do `config.yaml` virou `contas:`.
  Pendências: a fatia 5 virou o 20.9 (telas aprendidas, no ar com `f497075`, em `observe`); o 12.3 segue com o
  dono; os nomes históricos (tabela e rotas) são o 18.9 ([design](design/conhecimento-de-app.md)).

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
- **Implantado em 27/09 ~03:28 UTC (`8f7b94c`):** o CI com `npm run build`
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
| B5 | **Obsoleto/resolvido (apuração de 10/10).** O comando `c-20260921172322-6f7fdc` já não existe no banco do central (a tabela `commands` começa em 23/09/2026) e o reconciliador que faltava existe: `commands/reconciler.py` (sonda do estado real, `POST /api/commands/{id}/verify`, laço periódico) e `POST /api/commands/{id}/resolve` para a decisão humana. Um `start` desses fecharia sozinho (alvo `online`). Hoje há 24 `uncertain` (open_app 8, app.distribute 6, device.network 4, reset 3, restart 2, install_apk 1): por desenho só ciclo de vida é fechado pela sonda, e os 22 não verificáveis esperam o dono (pergunta P-047). O `device.network` passa a fechar pela rede em ramo próprio | banco de produção; `commands/reconciler.py` | `relatorio-validacao.md` §13 |
| B6 | **Resolvido em 10/10 (31.316).** O vocabulário de prova ficou `real`/`simulated`/`not_run`: teste automatizado com dublê é `simulated`, registrado por `aplicar` com `arquivo::teste` na evidência. O `estado.json` já só tem esses três valores; `aplicar` agora recusa `tests`/`unit` dizendo "tests/unit → simulated" | `scripts/claude-plan-100.py:35`, `.claude/workflows/plano-100.js` | [`claude-plano-100.md`](claude-plano-100.md) |
| B7 | **`npm run build` resolvido em 27/09 (job do painel no CI).** O CI não roda `npm run build`, e os testes de `scripts/tests` que usam pwsh só rodam localmente | `.github/workflows/ci.yml` | frente 2 |
| B8 | O app de QA embutido não foi migrado para o fluxo de release; `apps` e `app_releases` continuam como duas tabelas | plano-100 6.3 (bloqueio registrado) | frente 1 |
| B9 | Estado do worker e controle manual não são compartilhados entre backends | `backend/app/main.py` (achado #27) | `banco.md` |
| B10 | O `api-contract.md` tem dois adendos chamados "v0.9", e o `InstanceState` da base não lista `hibernated` — **resolvido no 31.318, higiene 12.3/B10 (10/10/2026): o segundo adendo virou `v0.9b` e o tipo do topo lista `hibernated`; não integrado** | `docs/api-contract.md` (anotado no adendo v0.11) | frente 1 |
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
    - **Contas do Instagram (27/09, pedido do dono):** só `«conta do android-01»` (android-01), `«conta do android-03»`
      (android-03) e `«conta do android-06»` (android-06) funcionam.
      - As outras cinco foram desatreladas: `blocked`, sem persona e sem aparelho. A tabela está
        em [`relatorio-desempenho.md`](relatorio-desempenho.md) §10.
      O `open_app` foi disparado como a prova de abertura do B21, supondo que o app do aparelho fosse o de QA. O `app_id` do android-04 é `instagram`, então foi um toque em conta real além do que o B21 pedia.
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
6. **Sem gasto e sem mundo real:** B6 (vocabulário de prova) foi resolvido em 10/10 (31.316); B7 (`npm run build` no CI) é o
   próximo item de código de risco baixo (B4 foi implantado em `57a155f`).
7. **Com autorização do dono:** o ensaio do aceite 6, derrubando o túnel no meio de um `start`. É o de menor risco
   entre os reais; o procedimento está em [`worker.md`](worker.md).

## Datas e verificações marcadas (frente GitHub/CI, 01/10/2026)

| Quando | O quê |
|---|---|
| 02/10 | Ler o cron diário do `CI` (05:17 UTC, o primeiro com `timeout-minutes: 60` no `backend-postgres`); `gh run list --workflow ci.yml --event schedule`. O PostgreSQL já passou inteiro em 01/10 (run 36830963968, 3962 passed) |
| 19/10 | O label `ubuntu-latest` migra para o Ubuntu 26 (anúncio da GitHub): reconferir `backend-postgres` e o workflow `Contêiner`; o Gmail do dono também para de enviar/receber em 19/10 por armazenamento cheio (assunto dele) |
| fim de outubro | Conferir se o gasto adicional do Actions ficou em US$ 0 (Billing → Usage por repositório; orçamento de Actions em US$ 15 com Stop usage); o ciclo reinicia no dia 1 Linha de base lida em 01/10 pelo Chrome do dono (somente leitura): uso medido bruto US$ 1,45 em outubro (`android` US$ 1,00, `devops` US$ 0,46), coberto por inteiro pelo desconto de uso incluído (US$ 1,45), ou seja, gasto adicional de US$ 0. |
| antes de março/2027 | Página de preços do GitHub: a taxa de plataforma de US$ 0,002/min para runner próprio foi adiada em 2026, não cancelada |
| sem data | Versões novas de `actions/checkout` (v7.0.1), `actions/setup-python` (v7.0.0), `actions/setup-node` (v7.0.0) e `actions/cache` (v6.1.0): os workflows usam v4/v5 (forçados ao Node 24 pelo runner, funcionando). Subir junto com um disparo de `ci.yml` completo e um `somente_postgres`, para a mudança nascer testada |

Detalhe e registros: [`handoffs/github-e-ci.md`](handoffs/github-e-ci.md). PRs #15 e #16 (K-039, Appium órfão) integrados na `main`
em 01/10 (`b5b9833`); entram em vigor no próximo deploy do backend e do `stop.ps1`.

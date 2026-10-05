# Estado atual — handoff

**Revisado em 05/10/2026: runtime do backend em `cb6742d4` (migração 116, deploy 42); site institucional ligado na raiz pública. Os blocos de 02/10 seguem abaixo como histórico.** Atualize este arquivo ao fechar cada tarefa (skill `fechar-tarefa`). Mantenha-o
curto: o que muda de sessão para sessão fica aqui, e o resto aponta para a fonte principal ([índice](README.md)).

## Onde estamos

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
  - **Abertos (baixos):** B5, B6, B9–B11 da revalidação e RF-07r, 19, 27, 30, 32, 35, 41, 42, 48, 49 da rodada 1.

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
6. **Sem gasto e sem mundo real:** B6 (vocabulário de prova) é decisão do dono; B7 (`npm run build` no CI) é o
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

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

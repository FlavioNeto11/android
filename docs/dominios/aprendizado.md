# Domínio: aprendizado contínuo

O sistema aprende com as execuções que dão certo e com as que dão errado, e com o que quem monitora faz ou diz. O
conhecimento fica num **livro** com ciclo de vida, e publicar sozinho só vale para o que não tem efeito externo e se
repetiu (D1). A decisão e as alternativas estão no
[ADR-054](../decisoes.md#adr-054--aprendizado-contínuo-livro-de-aprendizado-com-ciclo-de-vida-publicação-sozinha-só-sem-efeito-externo-d1-feedback-implícito-com-botão-opcional-d2-lições-medidas-e-backlog-do-que-mais-falha);
o contrato HTTP, nos adendos [v0.37 e v0.38](../api-contract.md); as tabelas (migração 055), em
[banco.md](../banco.md); a lição no prompt, em [ia.md §15](../ia.md). O aprendizado vivo (Fase 30: eixo de app, saúde,
curador por IA, métricas) é o [ADR-067](../decisoes.md#adr-067--aprendizado-vivo-eixo-de-app-saúde-derivada-e-curador-por-ia-auditável).

Caminhos relativos a `backend/app/`, salvo indicação. O código mora em `modules/learning/` (domínio, aplicação,
infraestrutura e apresentação, com a catraca de camadas do ADR-030 e zero `Any`); as costuras nos arquivos quentes, em
`taskqueue/costuras.py`; o painel, em `frontend/src/features/aprendizado/`.

**Estado (29/09).**

- Fundação (A1, item 20.2): implantada com `c359f65` (29/09 ~03:55Z).
- A2–A9 (itens 20.3–20.10): integrados em `f497075` e implantados em 29/09 ~07:38Z, sem migração nova.
- Modos de fábrica: lições em `shadow`, telas em `observe`, voz e preferências em `off`. Nada disso muda prompt nem
  sessão até a primeira prova real.
- Já valem de fábrica: `failure_kind` gravado, os sinais, o voto D2 com os efeitos dele, o D1 no fluxo aprendido (nasce
  `candidate`) e na receita com commit (para em `validated`), e o relatório "o que mais falha".
- Prova `real` só de leitura (livro, "o que mais falha"); `simulated` nos testes de cada pacote; o resto `not_run`
  ([relatório §22 e §23](../relatorio-validacao.md)).
- **Nenhuma chamada de IA** no pipeline: digest, curadoria, lições, telas, voz e preferências são determinísticos
  (`aprendizado.ia_resumos_por_dia` só aceita 0).

## O livro: uma leitura só, conteúdo onde já estava

`GET /api/aprendizado` reúne tudo numa leitura, mas cada conhecimento continua na casa dele:

| Tipo (`kind`) | Onde mora o conteúdo | O livro faz |
|---|---|---|
| `receita` | `recipes` (`taskqueue/recipes.py`) | lê; move o status com CAS e trilha |
| `fluxo` | `flows` (`taskqueue/flows.py`) | lê; move o status com CAS e trilha |
| `habilidade` | `skill_versions` (`modules/skills`) | só lê; a transição é da rota das habilidades (409 `use_skills_route`) |
| `memoria` | memória da persona (`social/memory.py`) | só a contagem; fica fora do D1 |
| `tela`, `licao`, `voz`, `preferencia` | `learning_items` | o conteúdo é do aprendizado |

Tudo o que o livro grava cabe nas sete tabelas da 055: `learning_items`, `learning_transitions` (a trilha única,
inclusive `receita:<id>` e `fluxo:<id>`), `learning_signals`, `learning_evidence`, `learning_exposures`,
`learning_daily` (a régua diária durável) e `learning_backlog`. Em tabela legada, o aprendizado só escreve o status de
receita e fluxo, `attempts.failure_kind`, `steps.failure_kind` e `attempts.failure_screen` (com escritor desde a
Fase 22, item 22.3; ver "O que mais falha").

## O ciclo e o D1

Os estados são os de `SkillState`, sem rascunho: o item nasce congelado, e mudar é criar outro com `parent_id`
(`domain/ciclo.py`). A tabela de transições é fechada; um teste a percorre por estado × estado × ator × efeito ×
origem.

| De → para | Quem | Condição |
|---|---|---|
| `candidate` → `validated` | sistema ou pessoa | sistema pela repetição, nunca em texto de pessoa |
| `validated` → `published` | sistema ou pessoa | sistema só sem efeito externo, sem texto de pessoa e com o modo do tipo em `on` |
| `candidate`/`validated` → `disabled` | sistema ou pessoa | contradição, refutação, rejeição |
| `published` → `deprecated`/`disabled` | sistema ou pessoa | rebaixar é sempre automático |
| `deprecated`/`disabled` → `published` | só pessoa | reativar é sempre de pessoa |
| `disabled` → `candidate` | só pessoa, só fluxo | "devolver à prova" (30.31): inerte, sem publicar; a sombra conta de novo |

- `requires_owner = side_effect OR human_origin` é derivado: nenhuma rota o edita. O repositório confere de novo no
  próprio `UPDATE` e, com `conferir_nascimento`, no item que o sistema já cria num estado (segunda camada).
- **Veto.** O conteúdo desligado por uma pessoa não volta pelo sistema (mesmo `content_hash`, mesmo escopo). Desligado
  pelo sistema, fica vetado por 90 dias ou até a versão do app mudar.
- **Devolver à prova (30.31, item 0).** Desligar "para validar pela IA" prendia o fluxo: a sombra só olha
  `candidate`/`validated`, e de `disabled` só se saía publicando.
  - A pessoa agora devolve o fluxo desligado à prova (`disabled → candidate`, chave `devolver` em `acoes`, botão
    "Devolver à prova"). Ele fica inerte (`FlowStore.match` só casa o ativo) e sai do veto, porque a última decisão
    da pessoa já não é desligar.
  - A sombra (`SombraDosFluxos._avaliar`) só conta a evidência observada a partir da volta, a favor e contra: o
    fluxo prova-se de novo, e com efeito para em `validated`, como sempre.
  - O sistema não devolve nada. Receita, lição e tela recusam (`transition_forbidden`): a receita volta a
    provar-se pela loja quando a etapa é aprendida de novo, e a lição e a tela, pela evidência.
- **Só evidência real promove** (`runs.simulated=0`), contada por execução e por aparelho distintos. A execução
  simulada deixa a sua linha em `learning_evidence` e não muda nada.
- **Quem decide pela rota** é o operador da sessão do painel (`panel` sem sessão), nunca `sistema`.
- O que o sistema não publica vai para "Para aprovar". Receitas e fluxos ativos com efeito, anteriores ao D1, ficam
  em "Revisar" até o dono decidir (desvio consciente do ADR-054).

## Digest e curadoria

- **Digest** (`LearningService.digerir_execucao`): roda quando a execução assenta (`scheduler.on_run_settled`, em
  thread). Cada minerador roda isolado; uma falha vira log e contagem, nunca derruba o fim da execução.
- **Curadoria** (`aprendizado.curadoria_s`, 900 s): a régua diária (`learning_daily`, recalculada só para dias ainda
  intactos, antes da purga de `ai_calls`), as provas do backlog, os vereditos das lições, as propostas, a voz e as
  preferências.
- `aprendizado.enabled: false` desliga o digest, a curadoria e o consumo; a leitura do livro continua.
- **Backfill único das lições** (`scripts/aprendizado-backfill-licoes.py`): as execuções reais fechadas antes da 055
  nunca passaram pelo digest. `--banco <poc.sqlite3> --antes-da-055` (ou `--run-id ID`) roda SÓ `licoes.contraste` e
  `licoes.plano`, pela mesma lógica de produção, em modo fixo `shadow` (nada publica, sem IA, sem rede). O padrão é o
  ENSAIO numa cópia do banco; `--aplicar` grava (faça backup antes). Idempotente (índices `ux_learning_items_vivo` e
  `ux_learning_evidence`): rodar de novo não duplica nada. Aborta sem escrever se a maior migração do banco difere da
  do código (nunca migra). Código em `infrastructure/backfill_licoes.py`; é para rodar uma vez.

## Costuras nos arquivos quentes (A2)

`taskqueue/costuras.py` é o contrato tipado entre o executor, o serviço de execução, o assistente do comando e o
gerenciador de aparelhos, de um lado, e o livro, do outro. Sem o livro ligado (`SEM_COSTURAS`), tudo segue como antes;
`avisar` e `pedir_licoes` engolem exceção, então o aprendizado nunca derruba uma etapa. O que passa por ali:

- `ao_fechar_tentativa`: uma vez por tentativa, inclusive na exceção, com a árvore da última tela (quem decide o que
  aproveitar é o observador; tela sensível é pulada);
- `licoes_para`: um só ponto para o ator (uma vez por tentativa, nunca na etapa conduzida por receita) e para o
  planejador (só quando ele é chamado);
- `ao_resolver`, `ao_repetir`, a resposta a uma pergunta (campo + sha256, nunca o valor) e a tomada de controle (só
  ids, na primeira vez por tentativa).

`failure_kind` é gravado em `repository.finish_attempt` (o erro final), na reconciliação (`interrompida`) e no desfecho
da etapa. O vocabulário é fechado (`domain/falhas.py::FailureKind`), e uma catraca por AST exige que todo motivo do
executor caia fora de `outro`. A catraca cobre também os motivos de `dado_ausente(...)` (30.58). A camada e o "onde
alterar" saem do tipo na hora da leitura.

O "Dado ausente: procurei …" da etapa de leitura (31.38) é `alvo_ausente`, qualquer que seja o motivo entre parênteses
(30.58). A regra vem antes dos tetos de IA: "orçamento de chamadas da etapa esgotado" diz como a leitura desistiu, não
que faltou crédito. Antes, as 7 tentativas de 04/10 caíam em `outro` e viravam candidato genérico no portal.

Quando a etapa termina por erro de IA, o tipo vem dele e não do texto (RA-22, migração 081). O executor põe o
`AIError.kind` no `StepOutcome.ai_error_kind` (o `desfecho_de_ia`, a verificação, e o `_run_guarded` do scheduler para o
erro que escapou), e o scheduler o passa ao `finish_attempt` (que grava `attempts.error_kind`) e ao `transition_step`.
Os dois classificam por `classificar_falha(texto, status, error_kind)`, o mesmo classificador puro da releitura:
`budget` → `ia_orcamento`, `billing`/`balance` → `ia_saldo`, `refusal` → `ia_recusa`, `not_configured`, `error` e
`invalid_output` → `ia_indisponivel` (`pelo_erro_que_encerrou`). `step_deadline` não decide: o ANR anotado no texto
continua ganhando do prazo. O status vem antes (`interrupted` segue `interrompida`). Com isso a mensagem de IA do
executor deixa de ser contrato; as REGRAS de texto ficam para o legado sem `error_kind`.

**A interrompida que esperou a pessoa (29.74).** `interrupted` segue `interrompida` na reconciliação, na pausa e na
tomada de controle. A tentativa que parou para esperar a pessoa (o `recovery` do `waiting_user`, "Aguardando o usuário",
e o da prova de fluxo, "Prova de fluxo: encerrada pelo sistema": `falhas.ESPEROU_A_PESSOA`) é classificada pelo texto,
como a etapa: autenticação, trava da conta, saldo, falta de informação. Texto sem regra ali, ou com tipo de navegação (o
texto gravado pode ser o erro anterior da tentativa), é o relato livre da IA (`ia_declarou_bloqueio`): só o que nunca
vira lição e a falta de informação passam (`DA_PESSOA_NA_ESPERA`). Antes, tudo isso formava um grupo da camada
`execucao` (`fk-d0f1c2ed23`, ~77 ocorrências em 04/10) que ninguém consertava. O legado gravado `interrompida` é relido
com o `recovery` na leitura retroativa (`falhas.tipo_da_tentativa`, no relatório, no agregado diário, no feedback e no
aprendido da execução), sem migração, e conta como retroativo. Com `retroativo=False`, fica o gravado. A série diária já
agregada (`learning_daily`) não é reescrita: os dias antigos seguem `interrompida` e os recentes saem reclassificados
(decisão da orquestradora, 04/10: não recompor, não se apaga dado por isso). Saúde D-5, orçamento do curador, gatilhos,
dossiê e a régua do 30.55 não leem o tipo da tentativa.

**Dívida paga (29/09, `2b0e5db`).** O contrato de gesto mora em `app/shared/costuras.py`: `TomadaDeControle`,
`CosturaDeControle`, `avisar`, as portas de comando e de ensino e `autor_do_gesto`. `taskqueue/costuras.py` o reexporta,
e `test_aparelhos_nao_conhecem_a_fila` (em `test_arquitetura.py`) impede a volta do import `devices` → `taskqueue`.

## Sinais: o que a pessoa já faz e o botão opcional (D2)

Os gestos viram `learning_signals`. Os sinais de GESTO (os sete: `confirmou_a_mao`/`repetiu_item`/`abandonou_item`,
`repetiu_execucao`, `respondeu_pergunta`, `tomou_controle`, `cancelou_execucao`, `comando_incerto_resolvido` e
`correcao_de_ensino`) são um por `(kind, source_ref)`, qualquer que seja o autor, e o primeiro autor fica
(`registrar_sinal(um_por_evento=True)`, leitura e escrita numa transação sob a trava do `Database`; 22.1). O motivo: a
régua conta linhas, e pedir o controle, desistir e outra pessoa pedir na mesma tentativa contaria em dobro. O índice
`(kind, source_ref, created_by)` continua sendo o do voto do D2, em que duas pessoas são duas opiniões. A garantia vale
dentro de um processo; com mais de um, precisaria de um índice único parcial (migração, não criada). Sinal de execução
simulada não promove nada. Nota com cara de credencial não é gravada (`note_refused=1`, ou 409 `note_looks_secret` no voto).

| Sinal | Quem grava | Polaridade |
|---|---|---|
| `feedback` | o botão "Deu certo / Deu errado" (A4) | do voto |
| `confirmou_a_mao` | resolver o objetivo com `confirm_done` (A2) | neutro: bom para a ação, ruim para a verificação; nunca evidência a favor |
| `repetiu_item` / `abandonou_item` | resolver com `retry` / `abandon` (A2) | neutro / negativo |
| `repetiu_execucao` | repetir a execução (A2) | neutro |
| `respondeu_pergunta` | resposta a `needs_input`, por campo (A2) | neutro |
| `tomou_controle` | pedido de controle com a IA numa etapa (A2) | negativo |
| `aprovacao_decidida` | varredura das aprovações (A9) | aprovada +, editada neutra, rejeitada − |
| `escolheu_habilidade` | varredura do desambiguador (A9) | neutro |
| `tela_vista` / `tela_desconhecida_chamou_pessoa` | observador de telas (A8) | — |
| `cancelou_execucao` | `POST /api/runs/{id}/cancel` (`RunService.cancel` com `por`); a sucessora que cancela a execução respondida não conta | neutro antes de rodar (`planning`, `needs_input`, `planned`); negativo depois (`running`, `paused`, `completed_with_issues`) |
| `comando_incerto_resolvido` | `POST /api/commands/{id}/resolve` | `succeeded` neutro (é confirmar à mão: nunca evidência a favor); `failed` negativo; `cancelled` neutro |
| `correcao_de_ensino` | `TeachingService.add_correction` (`POST /api/teaching-sessions/{id}/corrections`) | negativo, ligado à etapa corrigida |
| `parecer_decidido` | a decisão da pessoa sobre um parecer do curador (30.17): o gesto ou o rótulo de uma transição | neutro; o `created_at` é o instante da decisão |
| `pediu_revisao` | `POST /api/aprendizado/{kind}/{ref}/revisao` (30.17) | neutro; vira o gatilho `pedido_da_pessoa` |

`tomou_controle`, `confirmou_a_mao` e `tela_desconhecida_chamou_pessoa` contam como intervenção humana na régua
diária.

**Os três escritores de 29/09 (`d33b8ab`, `61c3bad`).**

- **Autor.** O `created_by` é o operador da SESSÃO (`autor_do_gesto`: sem sessão, `panel`; nunca `sistema`). Desde
  a Fase 22 (22.1) os gestos do A2 também: resolver o item, repetir a execução, responder a pergunta e tomar o
  controle levam o operador pelas rotas (`por=`) até o campo `quem` das costuras.
- **Cancelamento.** É um sinal por episódio: só o gesto que abre o cancelamento grava. O clique repetido, da mesma
  pessoa ou de outra, não grava enquanto o cancelamento já vale (`cancelling`, ou `completed_with_issues` com
  `cancel_requested=1`). A execução reaberta e cancelada de novo é outro episódio: `source_ref =
  cancelamento:<run_id>:<instante da transição>`, `data {status_anterior}`.
- **Comando incerto.** `source_ref = comando:<command_id>`, `data {verbo, resolucao, resolved_by}`. O `resolved_by` é o
  autor que o COMANDO gravou, só para cruzar os dois. Ele pode diferir do `created_by`: sem sessão, o comando aceita o
  `requested_by` do corpo, e o sinal fica `panel`. Sem execução no `params`, `simulated` vem do modo da instalação.
- **Correção de ensino.** `source_ref = correcao:<teaching_id>:<teaching_turns.id>`, `data {teaching_id, skill_id}`.
  Desde a Fase 22 (22.7) o painel chama a rota pela visão da execução ("Corrigir esta etapa", abaixo).
- **Régua.** O único consumidor é o `human_negative` da régua diária. Cancelar uma execução pausada, fechar o pendente
  de uma `completed_with_issues` ou marcar como falho um comando incerto conta como negativo humano na chave
  `(app, '*')`. As polaridades são escolha do pacote e esperam a ratificação do dono (ADR-054).
- **Nota.** A nota da resolução de comando (e a do pedido de cancelamento de comando) passa pela mesma triagem do voto
  antes de qualquer escrita: com cara de credencial, 409 `note_looks_secret`, e nada é gravado.

**O voto (A4).** "Deu certo / Deu errado + motivo", sem modal e sem pergunta, em cada objetivo e na execução
(`POST /api/runs/{id}/feedback`).

- "Deu errado" por navegação (`fez_outra_coisa`, `alvo_errado`, `nao_terminou`) desliga o fluxo e as receitas que o
  item usou e o que ele aprendeu. Quem desliga é quem votou: o veto passa a ser de pessoa.
- O `desfazer` da resposta só existe para o que estava publicado. Do candidato ou validado, a volta publicaria sem o
  D1; uma pessoa o publica pelo catálogo.
- "Deu certo" num item que falhou vai ao backlog como desmentido do verificador e não muda o desfecho.
- A lição é a exceção: quem a desliga é a contagem de refutações (duas), porque um voto só não diz que a LIÇÃO
  atrapalhou.

## Conhecimentos nativos sob o D1 (A5)

- **Fluxo.** O aprendido de execução nasce `candidate`, inerte (`FlowStore.match` só casa `active`). No digest, a
  execução seguinte do mesmo comando, que já chamou o planejador, é comparada em sombra com o candidato: sequência de
  ação, efeito externo, tipo da pós-condição e parâmetros. `aprendizado.fluxo.concordancias` (1) execuções reais
  concordantes validam. Sem etapa de efeito, o sistema publica; com efeito, para em `validated`. Duas discordâncias
  reais desligam.
- **Receita.** A promoção em sombra (item 21.14) para em `validated` quando há ação `commit`. O livro guarda o veto e a
  trilha.
- **A causa do "ausente" e a herança da receita (RA-20, item 29.40, 03/10).** O vocabulário e a regra são do Aprendizado
  (`domain/causa_do_ausente.py`); quem consulta é a loja (`taskqueue/recipes.py::RecipeStore.find`, da Android), com
  UMA consulta a mais, só no "ausente". A causa é `espera_o_dono`, `desligada`, `variante`, `legada` (assinatura e
  variante vazias, as 19 de 17/09), `versao`, `assinatura` ou `sem_receita`, contada em `receita.ausente{causa}`. A chave
  sem receita herda, como CANDIDATA, a receita `active` da mesma etapa (`step_hash`) mais próxima, noutra variante, na
  legada ou noutra versão; assinatura diferente nunca doa, e a chave esperando o dono ou posta de lado não herda. A
  herdeira passa pelo veto de `save`, guarda a origem da doadora (`learned_from_step`) e só age depois de concordar em
  sombra (`recipes_promote_after`, ou `_com_efeito` se tiver `commit`); com `commit`, para em `validated`. `ai.recipes_heranca: false` só mede a causa, e
  com `recipes_promote_after: 0` não há herança. Quando a herdeira vira `active`, a legada ativa da mesma etapa e
  versão sai (`superseded`, "provou-se na chave completa").
- Com `recipes_promote_after: 0`, a receita aprendida em execução real nasce ativa; a de origem simulada continua
  candidata.
- **A prova vale por efeito e soma entre aparelhos (31.287, 08/10).** `ai.recipes_promote_after` (padrão 1) é o número de
  execuções seguidas em que a IA fez o caminho da receita SEM ação de efeito externo; `ai.recipes_promote_after_com_efeito`
  (padrão 2) é o da etapa com `commit`. O D1 não muda: a receita com efeito, mesmo provada, para em `validated` e espera o dono
  em "Para aprovar" (o Livro mostra `necessarias` pelo efeito de cada candidata). Dois aparelhos que aprendem o mesmo botão são
  o MESMO caminho (`recipes.py::_caminho` compara ferramenta, efeito, argumentos e alvo, não a lista de seletores), então a
  candidata do primeiro soma a prova do segundo em vez de ser trocada. E a sombra não conta a decisão que o executor descarta
  (K-109).
- **O que uma execução simulada ensina não publica (RA-19, fatia B, 03/10; leitura 2, decidida pela orquestradora).**
  - Origem simulada (`runs.simulated=1`) nunca NASCE ativa. A receita nasce candidata mesmo com
    `ai.recipes_promote_after: 0` (`executor.py::_after_step`, `_origem_simulada`). O fluxo nasce candidato mesmo com
    `aprendizado.fluxo.com_prova: false` (`nativos.py::fluxo_ao_nascer`).
  - A concordância de uma execução simulada não promove receita: `RecipeStore.shadow(simulada=True)` não soma à
    sequência da candidata, e a divergência dela zera, como qualquer outra.
  - A sombra continua promovendo com evidência REAL (a do fluxo já só contava execução real), e a pessoa promove à mão.
  - Sem a linha da execução, conta como simulada: nada se publica pelo que não se sabe de onde veio.
  - `aprendizado.simulada_publica: true` é o modo anterior, só da suíte. O Harness é todo simulado e prova a reprodução
    e o reaproveitamento, como `fluxo.com_prova: false` e `recipes_promote_after: 0`.
  - Sem migração: o join `recipes.learned_from_step → steps.run_id` e `flows.source_run_id` → `runs.simulated` basta,
    porque não há purga de `runs`.
  - Prova `simulated`: `tests/test_origem_simulada.py`, com a consulta de join = 0 depois de duas execuções só
    simuladas.
  - O acervo antigo (14 receitas, 11 ativas ou validadas, e 3 fluxos ativos de origem simulada, todos do QA Messenger)
    não foi rebaixado: decisão da orquestradora, nada de app real. A fatia A já o tira da lista padrão do livro.
- **A chave genérica da receita (RA-20 fatia B, 03/10; desenho aprovado pela Android).**
  - O problema: o planejador reescreve a pós-condição julgada pelo modelo a cada plano ("conversa com @x aberta",
    "perfil de @x aberto"), e cada redação era uma chave. Medido no central: 15 receitas ativas em 5 caminhos iguais.
  - `hash_generico` (`taskqueue/recipes.py`) é a identidade da etapa SEM o texto da pós-condição. Só vale para a etapa
    `model_judged`, sem efeito e sem `commit_guard`; um campo ausente devolve None (a receita fica na específica).
  - Uma função só, chamada:
    - pelo executor, pela linha de `steps`, porque o DTO não traz o `template_key` da cópia do for_each;
    - pelo save;
    - pelos consumidores (`social/capacidades.py`, `taskqueue/aproveitamento.py` e o `_caminho_ja_aberto` do scheduler);
    - pelo backfill.
  - `contexto_sql`/`diagnostico` não mudam: a busca aproximada é só da tentativa antiga sem `recipe_id`, anterior a
    qualquer receita genérica.
  - `eh_generica` decide no save onde a receita mora. É específica quando um literal do que ela procura ou digita
    (`text`/`desc` dos seletores, argumentos de texto; o `{nome}` é o valor da vez e não conta) aparece, como palavra
    normalizada (caixa, acento, @), na pós-condição escrita, ou quando o valor de um parâmetro da execução aparece num
    literal.
    - Os casos reais: o toque em "nasa" do `open_profile` (receitas 25 e 73) é específico; "Message", "Options" e
      "Send message" (`open_thread`, 38/40/43/45) são genéricos.
    - Na dúvida, específica: o rótulo do campo citado na pós-condição ("Nome", "Perfil") deixa 56, 57, 59 e 104
      específicas, e isso só adia o ganho.
  - `RecipeStore.find(..., step_hash_generico=)` consulta as duas chaves na MESMA chamada: ativa específica, ativa
    genérica, candidata específica, candidata genérica.
    - A quarentena vale em qualquer das duas.
    - A herança tenta a específica e depois a genérica, e a causa do ausente é medida uma vez.
    - A tentativa achada pela genérica conta em `receita.consulta{resultado, chave=generica}`; a específica fica na
      série de antes.
    - Na trilha: "reproduzida pela chave genérica".
  - Troca entre chaves: a candidata ESPECÍFICA que divergiu sai (`superseded`) quando o caminho da IA vai para a
    genérica, mesmo que a genérica já tenha a sua em prova.
    - A genérica que divergiu num valor e um caminho específico ficam lado a lado: ela segue em prova para os outros
      valores.
    - O treino (`training/skills.py`) fica específico.
  - Backfill NÃO destrutivo (`scripts/ra20b-receitas-genericas.py`, `taskqueue/receitas_genericas.py`; ensaio por
    padrão, `--aplicar` com backup e o OK da Android e da orquestradora).
    - As receitas atuais ficam onde estão.
    - Por chave genérica entra UMA cópia candidata da ativa elegível com mais evidência, que se prova em sombra.
    - Fica de fora `open_app`: desde o 29.45 a etapa de app em frente não gera comparação de sombra.
    - Uma específica mal classificada custa só uma divergência em sombra.
    - Ensaio na cópia do central (03/10), depois da regra do título (o alvo escrito só no título da etapa conta
      como literal): 6 específicas, 9 chaves, 9 candidatas semeadas e nenhuma ativa tocada.
- **O contêiner sem identidade vira receita pelo filho rotulado (29.40 item 2; revisão da Android em 03/10).**
  - O caso: a linha clicável de uma lista (a conversa, o contato) não tem `resource-id` nem texto, e o nome mora num
    filho NÃO clicável. Antes, o toque nela não virava receita.
  - Na gravação, `_safe_target` guarda até 3 filhos rotulados (`filhos_rotulados`). O filho só entra se, pelo hit-test,
    o próprio alvo for o menor clicável no centro dele, e só até o primeiro nó fora dos bounds (a janela da
    pré-ordem).
  - Na reprodução, o seletor `via: filho` (com a classe do `conteiner`) toca o CENTRO do filho. O driver toca por
    coordenada, e o Android sobe o toque ao contêiner. Antes do toque, o clicável sob o centro tem de conter o filho,
    não pode ser ele e tem de ter a classe gravada; outro clicável por cima faz divergir.
  - `_rotulo_estavel` recusa rótulo que muda com o estado (tempo, "Following", "Active now", online/offline,
    digitando, o selo "novo") e qualquer @ literal; o nome de pessoa só vale templatizado. O efeito externo pelo
    filho é recusado em `distill` e em `distill_training`.
  - Com a chave genérica: o seletor `via: filho` mora em `selectors`, então `_literais` o lê como os outros. O filho
    com o literal do valor da etapa ("nasa") deixa a receita na chave específica.
  - Os textos dos filhos ficam no `target` da ação, no banco local, como o texto do alvo já ficava. Nenhum leitor de
    saída lê a chave `filhos`: as lições leem só `resource_id`, `text` e `desc` do alvo.
  - Prova `simulated` (`tests/test_receita_filho_rotulado.py`, árvores sintéticas).
- **Habilidade.** O primeiro escritor real de `skill_validation_results`: cada execução de versão grava a observação
  (`proof=real` só de execução real). Execução com etapa confirmada à mão vira `uncertain`, nunca `passed`. O sistema
  pode validar; publicar é sempre de pessoa.
- **O interruptor antigo passa pelo livro (29/09, `0f91fb3`, `c655495`).** `PUT /api/flows/{id}` e
  `PUT /api/recipes/{id}` chamam o mesmo serviço de `POST /api/aprendizado/{kind}/{ref}/status`
  (`LearningService.mudar_status_nativo`): trilha com a pessoa (o operador da sessão, ou `panel`), veto do que ela
  desligou e as guardas do livro. `active` vira `published`; `disabled` e `quarantined` viram `disabled`. Pedir o
  status atual não gera transição. O fluxo em prova ligado pelo interruptor passa por `validated`, com as duas decisões
  na trilha (`ciclo.py::caminho_da_pessoa`), e o gesto de dois passos é atômico na transação da rota.
- **Um anúncio por nascimento.** Com o aprendizado ligado, só a sombra do digest anuncia o fluxo candidato, e diz
  quando ele passa a valer: `1 + concordancias` execuções reais, sem contar as simuladas, inclusive a que o gerou; com
  efeito externo, o dono publica. O `_learn_flow` do scheduler anuncia só o fluxo que nasce ativo
  (`com_prova: false`) e o candidato com o aprendizado desligado ("só uma pessoa o publica"). A leitura e a decisão dele
  ficam num `try` com log: não derrubam o fim da execução. Se o digest falhar, o nascimento não é anunciado (fica o
  log).

## Lições medidas (A7)

`application/licoes.py`, `domain/licoes.py`, `domain/efeito.py`. Contexto para o ator e o planejador, **nunca para o
verificador** (ADR-024); como entram no prompt, em [ia.md §15](../ia.md).

1. **Nascimento por contraste.** Uma falha seguida de sucesso comprovado na mesma etapa gera candidata do ator. Um
   defeito do plano seguido de plano que comprovou a mesma ação gera candidata do planejador. Falha repetida sem
   contraste não prova o que funciona e vai ao backlog.
   - O seletor com as partes em elementos diferentes (`seletor_em_elementos_diferentes`, 31.32) também conta como
     defeito do plano.
   - Quando todos os defeitos são desse tipo, a lição diz para não juntar num seletor só partes que a tela tem em
     elementos diferentes, sem proibir o tipo da pós-condição.
   - Esse defeito ensina também contra um plano que comprovou com o MESMO tipo, porque o conserto é o seletor e não o
     tipo.
   - Com um defeito genérico no meio, vale a lição do tipo.
2. **Texto fechado.** Modelos fixos por tipo de falha. As lacunas só aceitam: ação do catálogo, tipo de pós-condição,
   contagem, sufixo de resource-id, `{parâmetro}` e rótulo curto que se repetiu, idêntico, em 2 execuções. Nunca
   `attempts.error` cru, texto de tela, nome de terceiro, valor de parâmetro ou segredo. A nota de um voto vira
   candidata `human_origin`, que só o dono publica.
4. **Validação e publicação.** A mesma impressão em 2 execuções reais valida. Sem efeito e com `licoes.modo: on`, o
   sistema publica na `fila_de_prova`, e a curadoria abre uma prova por (app, ação, papel).
5. **Medida.** Em prova, 50% das unidades com a lição e 50% sem (unidade = etapa no ator, planejamento no planejador;
   braço por `sha1(item|unidade)`). O desfecho de cada exposição é gravado antes da purga de `ai_calls`, só em
   execução real; a etapa confirmada à mão entra como `unverified`, nunca como sucesso. O custo é o da execução: no
   planejador entram só as chamadas com `origem` `execucao` ou nula (anteriores à 073), e a decisão fechada do Jev
   (31.14), que leva o `run_id`, fica fora. O veredito exige 8 unidades por
   braço: "ajuda" fica (com 10% de controle), "atrapalha" desliga, "neutra" aposenta aos 20. Sem exposição por 60
   dias, aposenta; versão nova do app, volta à prova.
6. **Teto.** Ator: 120 tokens e 3 lições; planejador: 150 e 3. O teto vale para todas as elegíveis antes do braço, e o
   que não cabe fica de fora inteiro. `GET /api/aprendizado/licoes/previa` mostra o bloco exato e o "faltam N".

Com o volume de hoje, um veredito leva semanas; o limiar não baixa.

## Telas aprendidas (A8, fatia 5)

`application/telas.py`, `domain/telas.py`, `infrastructure/ligar_telas.py`. Resolve a tela de CASA que muda numa
atualização do app: dela, o "voltar" da conferência de conta sai do app e chama uma pessoa. O ciclo:

1. **Coleta** no fechamento de cada tentativa: etapa comprovada, app da etapa na frente, fora do aparelho-loja, tela
   não protegida (sensível, senha ou texto de verificação) e o YAML dizendo "desconhecida". Vira o sinal `tela_vista`
   com até 60 ids estáveis, sem texto.
2. **Candidata:** 3 observações da mesma tela dão uma regra de 2 a 4 `ids_todos`, sempre `autenticada`. É de casa só
   se TODA observação mostrava a aba de perfil declarada.
3. **Validação:** 3 observações reais em 2 execuções, zero conflito e a prova local (reclassifica as próprias amostras
   e nenhuma das telas declaradas).
4. **Publicação** sozinha só com `telas.modo: on`. De fábrica é `observe`: grava, minera e valida, e a sessão não
   consome.
5. **Desligam a regra:** o primeiro conflito (login ou conta errada no mesmo aparelho até 2 min de um uso), o modo fora
   de `on`, uma pessoa, e 30 dias sem casar numa versão nova do app.
6. **Ponte para o repositório:** `GET /api/aprendizado/export?kind=tela&app=` devolve o fragmento YAML. Quando o YAML
   commitado reconhece todas as amostras, a aprendida se aposenta como `absorvida:<commit>`.

A regra aprendida entra depois das declaradas, só como `autenticada`, e é pulada em tela sensível. A conta continua
lida só pela tela de perfil declarada. `scripts/aprendizado-telas.py` faz o deixa-um-fora sobre as observações reais,
com o banco aberto só para leitura.

## Voz e preferências (A9)

- **Voz.** A aprovação EDITADA de uma execução real vira candidata de voz: o texto da persona e o que a pessoa pôs no
  lugar, destemplatizados. Nasce `side_effect=1` e `human_origin=1`: só o dono publica. Com `voz.modo: on`, até 2 pares
  do mesmo perfil e da mesma ação vão ao contexto social (`<exemplos_de_voz origem="pessoa">`). Se a taxa de edição
  não cair, o sistema aposenta. Prévia: `GET /api/aprendizado/voz/previa`.
- **Preferência.** Nunca responde sozinha a uma ação com efeito.
  - A mesma resposta 3 vezes, no mesmo campo, no mesmo modelo de comando e no mesmo perfil, vira candidata
    `human_origin` (o dono publica) e só pré-preenche a pergunta (`GET /api/aprendizado/preferencias/sugestoes`).
  - A escolha repetida no desambiguador decide sozinha só se nenhuma etapa tem efeito e a versão candidata está entre as
    conferidas (`provenance.versoes`). Numa versão nova, a pergunta volta com a opção pré-selecionada.
  - Campo que é alvo de terceiro nunca vira padrão.

## O que mais falha (A3)

`domain/backlog.py`, `application/falhas.py`. É o aprendizado para as sessões de desenvolvimento, não para a IA.

- **Grupo:** app (pacote), ação (`*` = etapa livre), tipo de falha e tela. A chave `fk-*` sai de uma função só, a mesma
  no md, no JSON e na linha gravada.
- **Ordem:** US$ perdido + minutos × `aparelho_usd_min` + intervenções × `pessoa_usd`; as três colunas saem separadas.
  O falso positivo do verificador fica sempre no topo. Só entra no topo o grupo com `minimo_ocorrencias` (3).
- **Legado:** `retroativo=true` classifica na leitura as tentativas sem `failure_kind` gravado, sem gravar nada.
- **Prova da correção:** a pessoa ou a sessão marca `fixed_pending_proof` com o commit, depois do deploy. Com ≥10
  tentativas elegíveis e taxa ≤ 50% da linha de base, a curadoria marca `fixed`; acima, `reopened`. A reincidência
  depois de `fixed` é medida nas últimas 2 × `prova_minimo` elegíveis. `fixed` e `reopened` nunca vêm de uma pessoa.
- **Propostas** (sempre decisão de pessoa): `acao_de_catalogo` (ação nova não entra no catálogo pelo banco),
  `promover_licao` e `promover_tela`.
  - Desde o 30.59, a `acao_de_catalogo` agrupa por (app, chave da etapa), com a referência `<pacote>|etapa:<chave>`.
    Antes agrupava por `template_hash`, e a mesma etapa com dois objetivos virava duas propostas iguais.
  - Ela não é proposta quando 80 % ou mais das etapas comprovadas fecharam sem IA (`driven_by` `recipe` ou
    `sem_ator`, `ACAO_SEM_IA_MAX`): o ganho já foi colhido.
  - As linhas antigas, por `template_hash` (`<pacote>|<hash>`), não são reescritas: o estado de uma linha é da pessoa.
    Ficam abertas até alguém marcá-las `wontfix` pela rota do backlog.
- `scripts/aprendizado-backlog.py` grava o md em `data/aprendizado/` e imprime o topo.
- `scripts/candidatos-do-portal.py` (29.72) grava `data/aprendizado/candidatos-do-portal.json`: os grupos abertos e sem
  item do plano viram candidatos para a orquestradora numerar (contagem, ids de exemplo, frente sugerida). Ajuste de 04/10: `amostra_de_lote` (quantos dos exemplos são execução nossa, pela chave de idempotência
  `lote:`/`ensaio:` ou pela prova de fluxo, lida do banco do central só para leitura, porque a API não expõe a chave) e
  `dias_sem_ocorrer`; o grupo de amostra toda nossa ou parado há mais de 7 dias vai para o fim, com o motivo em `rebaixado`.
- **Quebra de série do `pct_por_receita`** (a parte das etapas conduzidas só por receita, em `Saude`): no deploy 7
  (03/10/2026, processo do central de pé às 07:28:40Z; commit 49811568, migração 081) o denominador mudou. Antes,
  a etapa conduzida pela IA com as receitas desligadas ficava com `driven_by` nulo (`-`, fora da conta). Desde então
  ela é gravada `ai` (RA-10), e a etapa fechada pelo atalho do executor sem o ator entra como `sem_ator` (LT-1).
  As duas contam no denominador e nenhuma conta como receita, então o número cai sem nenhuma receita ter piorado.
  Compare só janelas do mesmo lado de 07:28:40Z.
- **Quebra de série do LT-6** (29.45, caminho rápido 2): desde o deploy 8 (03/10/2026, processo do central de pé às
  09:06:28Z, commit df860763), a etapa `app_foreground` sem efeito e sem receita conduzindo abre o app pelo executor
  (`OPEN_APP_SEM_IA`), sem chamar o ator. Ela fecha pelo atalho com `driven_by = sem_ator` (antes, `ai`, com um
  `decide`). O que muda nas séries do aprendizado:
  - `so_ia` cai e `sem_ator` sobe sem nada ter mudado na tela. O `pct_por_receita` NÃO quebra: as duas origens já
    contam no denominador desde o deploy 7. O aproveitamento tira `sem_ator` das elegíveis, então `elegiveis` e
    `sem_cobertura` caem;
  - a etapa não deixa ação do ator: não nasce receita nova de `app_foreground`, e a candidata dessas etapas não recebe
    veredito de sombra (`_veredito_da_sombra` não tem caminho da IA para comparar). A `open_app` candidata do QA
    (v4) não promove por sombra e fica candidata; quem a decide é uma pessoa no livro. A ativa continua reproduzindo,
    porque o LT-6 só age com `rep is None`;
  - por isso o backfill da chave genérica (RA-20 B) deixa `open_app` de fora;
  - compare só janelas do mesmo lado de 09:06:28Z. Do lado da Jev, a amostra "sem casamento" da sombra do 31.10
    também quebra nesta data: os fluxos revividos pelo 30.29 voltam a casar. Os 5 do QA Messenger foram desligados
    por pessoa às 08:45Z, até o parecer do curador, e só entram quando forem religados.

## Fase 22 (29/09, noite): o que mudou nas regras

- **Nota do comando (22.2).** O painel manda só o texto da pessoa e `origin: 'panel'`; o backend compõe o contexto
  (", no painel a partir de <instance_id>", ou ", a partir de <instance_id>" quando o autor é `panel`), e a triagem
  vê só o texto da pessoa. `result.note` guarda só esse texto, e `result.origin='panel'`. O `requested_by` passa pela
  mesma triagem sempre que vier, com ou sem sessão (409 `note_looks_secret`, nada gravado). O prefixo do cliente
  antigo só é reconhecido com o `instance_id` do próprio comando.
- **Tela da falha (22.3).** `executor.tela_da_falha` classifica a última árvore observada pela tentativa com o
  `telas.yaml` do app da etapa; `scheduler._run_guarded` a leva no `StepOutcome`, e `repository.finish_attempt` grava
  `attempts.failure_screen` no mesmo UPDATE de `failure_kind`, só quando há tipo de falha. Nunca texto da tela. A trava
  achada dentro de uma ferramenta devolve a tela pelo próprio executor, porque `quick_tree` não atualiza `rt.last_tree`.
  As telas aprendidas ficam fora, para a chave do grupo não depender do modo do livro.
- **App por etapa no aprendizado (medido em 02/10, `test_aprendizado_app_por_etapa.py`).** `steps.app_id` NULL é o
  desenho, não perda: o plano só grava o app da etapa quando ele difere do app do plano (`planning/parsing.py`), e
  `runs.app_ids` leva o app do plano primeiro. A régua diária, o relatório de falhas, as lições e as costuras resolvem
  por `app_da_etapa` (etapa, depois `app_ids[0]`), então uma execução Instagram + Outlook já conta cada etapa no app
  dela. Não gravar o app do plano em `steps.app_id`: mudaria a conta esperada (`do_aparelho`) e a identidade da receita.
  `failure_screen` vazio é, em quase tudo, tela desconhecida por desenho, app sem `telas.yaml` ou tentativa anterior ao
  escritor.
- **Backlog sem falso corrigido (22.3).** A chave do grupo não mudou, mas a MEDIDA de uma linha segue
  `ChaveDoGrupo.abrange`: a linha sem tela (as abertas antes do escritor, e as de tela desconhecida) mede o mesmo
  `(app, capability, failure_kind)` em QUALQUER tela, na linha de base, na prova e na reincidência; a linha com tela
  também conta, na prova, o EXCESSO das ocorrências do mesmo trio em tela desconhecida acima da base dela
  (`excesso_sem_tela`), para não virar corrigida quando o classificador deixar de reconhecer a tela; o motivo diz isso. O agrupamento do relatório segue exato, para não contar a mesma tentativa duas vezes.
- **Adoção de fluxo com trilha (22.4).** Adotar, desfazer a adoção e publicar a versão de quem adotou gravam
  `learning_transitions` (kind fluxo) na mesma transação, com a pessoa que decidiu e motivo fixo ("adotado pela
  habilidade X" / "devolvido pela habilidade X"), mapeando o status pelo `estado_nativo`. Só uma pessoa adota ou
  devolve (o sistema recebe `TransitionForbidden`). Política atual: a trilha é o registro do gesto, e a falha dela
  desfaz a adoção (como no interruptor antigo); a alternativa (trilha acessória, com savepoint e log) é decisão do
  dono. Efeito visível: o fluxo adotado e devolvido conta como decidido por pessoa e sai de "Revisar".
- **Trilha nas lojas sem derrubar o save (22.5).** O ouvinte do A5 envolve a trilha e o veto em `db.savepoint()`
  DENTRO do `try` que engole a falha; e o `tx()` de fora, no PostgreSQL, confere a transação abortada antes do COMMIT
  (`TransacaoAbortada`): o esquecimento que antes perdia dado calado (o COMMIT virava ROLLBACK) agora falha alto.

- **Correção de ensino na execução (22.7).** Na aba "Por aparelho", a etapa `failed` ou `uncertain` que veio de uma
  habilidade ganha "Corrigir esta etapa" no detalhe, e a linha recolhida leva a marca "corrigível". A regra é a mesma
  da rota: habilidades ligadas (`features.skills`) e a tela do ensino v2 ligada (`features.ensino_v2`, 31.91 F1, padrão
  `false`), status em `CORRECTABLE_STEP` e a origem do passo (`origin`, lida
  em `plan_versions` pela mesma versão do mesmo objetivo). O envio acha um ensino aberto da mesma habilidade e versão
  que já corrige esta execução (ou um vazio aberto por esta ação), ou abre um com instrução fixa ("Corrigir a
  habilidade <id> (versão N)."), e posta a correção com `step_id` = `steps.id`, não a key. O ensino que fica aberto e
  vazio quando a correção é recusada não é descartado: a próxima correção da mesma habilidade e versão o reaproveita.
  A triagem é a do ensino (400 `credential_in_text`), e o sinal tria de novo. Ainda não há tela para continuar esse
  ensino (gerar a candidata, virar rascunho): o aviso de sucesso aponta para Aprendizado.

## Onde no painel

- **Aprendizado** (oitava seção da barra; `features/aprendizado/`), com a contagem de "Para aprovar" no item da barra:
  - **Para aprovar:** a fila do D1, com a evidência, "Selecionar todos" e "Aprovar selecionados". A habilidade
    validada se decide ali pela rota das habilidades. Embaixo, **Revisar**: o legado ativo com efeito, que a pessoa
    mantém ("Confirmar que fica", 30.24) ou desliga, um a um ou em lote;
  - **Aprendido:** o catálogo unificado por tipo e estado, com desligar, aposentar e reativar, sempre com motivo;
  - **O que mais falha:** o relatório do A3, com as três colunas de custo e o falso positivo no topo;
  - **Sinais:** os votos e os gestos;
  - **Métricas** (30.33; `?aba=metricas`): as rotas do 30.8 (adendo v0.89) num cartão por bloco do §10, na janela de 7,
    14 ou 30 dias e por app, e embaixo os pareceres do curador em páginas pelo cursor, cada um com o link do item.
    - Ausente é "sem amostra", nunca 0%, e o tempo abaixo de 1 h sai em minutos.
    - A comparação receita × só IA diz no título que não é prova ("não mede falha evitada").
    - O orçamento do curador diz que é global e que usa a janela dele, com a barra de uso e o aviso a 80%.
    - A janela que atravessa o deploy 8 (03/10 09:06:28Z) ganha o aviso de quebra de série.
    - Bloco sem dado diz o porquê numa frase, em vez de uma grade de zeros; 503 `not_ready` vira "não estão ligadas
      neste servidor".
- **Nomes, não códigos (validação do deploy 3, P2 a P5).** O painel mostra o app e a capability pelos nomes do
  agrupamento do Aprendido ("Pós-condição não comprovada — Instagram · Abrir o feed", "Instagram › Abrir o perfil";
  `app_nome` e `capability_nome` de `presentation/nomes.py`). O pacote e o código ficam no `title` e em "Para quem
  desenvolve". A receita troca a chave da etapa pelo nome da capability, e dois itens iguais numa lista ganham quando
  foram aprendidos (`model.ts::titulosDaLista`). O texto da lição nomeia a capability SÓ NA TELA
  (`nomearCapabilityNoTexto`): o texto gravado vai ao prompt, onde o código é o que serve. Sem resposta HTTP, o estado
  de erro diz "Sem resposta do servidor." (`lib/loadError.tsx`, todas as telas).
- **Nomes também nas listas (validação do deploy 4).**
  - A tela diz "capacidade", "Etapa livre (fora do catálogo)" e "Fora do catálogo" no lugar de "capability".
  - As entradas do livro (listas, filas e detalhe) ganham `app_nome` (`presentation/nomes.py::nomear_apps`). "App:", a
    Identidade e a Versão dizem o nome, com o pacote no `title`.
  - A receita ganha `etapa`, o `steps.title` da etapa de origem. No app sem catálogo, ela vira o nome:
    "Digitar a mensagem (v1)" (até o deploy 8 levava a chave, "· etapa fill_message", que agora fica no `title`). O nome
    do catálogo vence; sem os dois, fica a chave.
  - O `title` gravado não muda, e o dossiê do curador não leva `etapa`, porque o título de uma etapa pode citar um @
    ou um contato.
  - A ocorrência de falha diz "android-05 · tentativa 1"; a chave da etapa ("etapa open_app do plano") fica no `title`,
    e a tela da falha, "Tela: caixa de entrada", com o id do catálogo no `title` (UX dos deploys 7 e 8).
- **O app de teste fora da lista padrão (RA-19, fatia A).**
  - O Aprendido abre em **Produto**: `GET /api/aprendizado` sem `rotulo` esconde os apps de `apps.category='qa'` (o QA
    embutido, 041), que eram 94 das 164 entradas do central em 03/10.
  - O filtro é um seletor segmentado "Produto · QA · Todos", com o número de ocultos ao lado ("94 do QA ocultos").
  - Com um app escolhido (`app=`), o padrão é `todos`: escolher o QA Messenger mostra o que ele tem.
  - O acervo de teste NÃO é descartado (o fluxo de 17 usos serviu 16 execuções reais). Só sai da lista padrão; a
    visão por app, as filas Para aprovar e Revisar, a contagem da barra e a saúde continuam lendo tudo
    (`LearningService.livro` sem `rotulo`).
  - Não há `papel` no YAML: o app de teste é o que a loja já marca (`apps.category`).
  - A fatia B, depois do RA-22: a trava "origem simulada nunca passa de candidate" no D1.
- **Execução:** o botão "Deu certo / Deu errado" em cada objetivo (aba "Por aparelho") e na execução inteira (aba
  "Relatório"). O motivo abre em linha, e "Reativar" aparece quando a resposta traz `desfazer`. A seção "Aprendizado
  desta execução" mostra o bloco `aprendizado`, os votos e os sinais. O cartão "Custo de IA desta execução" mostra
  "Normal medido para este plano" (`GET /api/runs/{id}/projection`, item 18.3), com o rótulo da janela efetiva.

## O bloco "Aprendizado desta execução" (29/09, `a54735a`, `d506337`)

`GET /api/runs/{id}/feedback` traz `aprendizado: {receitas, fluxos, falhas, candidatas, licoes}`. Cada grupo é uma
lista de linhas `{kind, ref, titulo, estado, papel, braco, failure_kind, n}`. O código está em `domain/aprendido.py`,
`application/aprendido.py`, `infrastructure/aprendido_sql.py` e `presentation/feedback.py::_bloco`.

- **Fontes:**
  - `runs.flow_id`, `flows.source_run_id`, `attempts.recipe_id` e `recipes.learned_from_step`;
  - `learning_transitions`, `learning_evidence` e `learning_exposures` com o `run_id`;
  - a falha vem de `attempts.failure_kind` (o legado é classificado na leitura); sai só o tipo, nunca o texto do erro.
- **Grupos:**
  - uma linha por item em cada grupo;
  - o item que NASCEU da execução vai só para `candidatas`: a lição, a voz, a tela (`telas.minerar(run_id)` passa o
    `run_id` ao nascimento) e, desde a Fase 22 (22.6), a preferência. A preferência nasce na curadoria com observações
    de 3 ou mais execuções; o `run_id` do nascimento é o da observação que FECHOU o limiar (a n-ésima execução
    distinta, na ordem dos sinais), e o papel diz isso sem vendê-la como causa única ("preferência que nasceu com a
    evidência desta execução, entre N execuções"). A proveniência guarda `limiar: {run_id, execucoes}`, fora do
    conteúdo (não muda `content_hash` nem veto). As transições do sistema dela levam o `run_id` da evidência decisiva
    observada desde a última mudança de estado; sem evidência nova decisiva, `run_id` nulo;
  - a tela, a voz e a preferência que já existiam e mudaram de estado com o `run_id` também entram em `candidatas`
    ("tela que já existia, desligada nesta execução…");
  - a lição tocada que não nasceu ali vai para `licoes` ("exposta ao prompt", "braço de controle").
- **Fora do bloco:** o só reforçado (só evidência) e a versão de habilidade; a página Aprendizado os mostra.
- **Autoria no `papel`:** transições seguidas de um mesmo autor viram um verbo só, com três finais.
  - "nesta execução": o sistema decidiu.
  - "pelo voto de uma pessoa": decisão humana com o motivo do voto.
  - "por uma pessoa": qualquer outra decisão humana com o `run_id`.

  O `decided_by` e o motivo servem só para classificar a autoria e nunca saem no bloco.
- **Estado:** o `estado` é o ATUAL do item no livro, não o da hora da execução.
- **Título:** passa de novo pela triagem de credencial (recusado sai `null`) e é cortado em 160 caracteres.
- **Item apagado depois:** a linha fica, com o `ref` e sem título nem estado.
- **Listas e falha de leitura:** as listas vêm sempre, vazias quando nada mudou. `aprendizado: null` só quando a
  leitura falhou (log `poc.aprendizado`); votos e sinais saem mesmo assim.
- **O painel tem três estados:**
  - "Não foi possível ler o que esta execução aprendeu ou usou do livro." com o bloco nulo;
  - "Nada aprendido, usado do livro ou sinalizado nesta execução." só com tudo vazio;
  - "O servidor ainda não informa…" quando o GET inteiro falha.

## Configuração

Bloco `aprendizado` do `config.example.yaml`; os modos vão entre aspas, porque `off`/`on` sem aspas viram booleano no
YAML. Padrões e significado na tabela do [adendo v0.38](../api-contract.md). Nenhum modo passa a `on` sem prova real e
decisão do dono.

**Modo por app (30.20, §8.10 do desenho).** `aprendizado.licoes.por_app: {<pacote>: off|shadow|on}` e
`aprendizado.telas.por_app: {<pacote>: off|observe|on}` sobrescrevem o modo global de UM pacote (chave = pacote Android,
validada no config); vazio, o padrão, é o global. A regra é uma só, `domain/modo_por_app.modo_efetivo`, e vale na coleta
e na validação (mineradores, curadoria, observadores de tela), no consumo (`licoes_para`, fornecedor de telas) e na
publicação sozinha (D1) de lições e telas (`LearningService._modo_publica(kind, app)`, com o pacote do item: um
pacote em `on` publica mesmo com o global em `shadow`/`observe`, e o contrário também vale); `enabled: false` vence
tudo. O portão de `CosturasDoLivro.licoes_para` consulta o fornecedor se o global OU algum pacote não está `off`. A
rota do livro traz `por_que_nao_publica` com `modo_desligado` e `vetado` (30.5, `LearningService.contexto_de_publicacao`),
e a camada de uso da visão por app (`/api/aprendizado/apps/{pacote}`) usa o modo efetivo do pacote
(`ModosDeUso.do_pacote`). O pacote vem só do config (ADR-052). Prova `simulated`; nada foi ligado no central.

**Interface do modo por app (30.20, só leitura; adendo v0.64).** Cada app de `/apps` e o `app` de `/apps/{pacote}` trazem
`modos_do_app`: lições e telas com o modo EFETIVO e a origem (`app` quando o pacote está em `por_app`, senão `global`;
`ModosDeUso.definidos_no_app`). Os `modos` da visão ganham `licoes_por_app` e `telas_por_app`. No painel: o detalhe do
app mostra "Lições e telas neste app" com o modo, a marca "definido para este app" ou "segue o global" e o que o modo
faz; "Como mudar" diz a chave do config e que é preciso reiniciar o central (o config é lido uma vez, em `state.py`). O
cartão mostra só o modo próprio, e o Global lista as exceções. O painel não grava o config: editar pela tela é decisão
pendente da orquestradora (gravar o `config.yaml` ou levar o override para o banco; as duas mexem em núcleo).

**Validação automática (30.31).** `aprendizado.validacao`: `modo` (`"off"` de fábrica), `intervalo_s`, `beta`,
`maximo_por_hora`, `janela_dias`, `extra_usd` com `extra_ate` (ISO; sem fuso = UTC; data inválida recusa o config) e
`custo_estimado_usd`. Ver a seção da 30.31 abaixo.

**Autopublicação do fluxo B (30.34).** `aprendizado.autopublicacao.modo: "off" | "shadow" | "on"` (`off` de fábrica)
e `intervalo_s` (3600). `shadow` só marca o que publicaria; `on` (30.34-B) publica, mas só com o balanço da sombra
liberado, e sem ele é igual a `shadow`. O central liga `shadow` no deploy 11 (orquestradora, 03/10). Os limiares são da regra, não do
config: ver "Autopublicação do fluxo B em sombra (30.34)".

## Evento `learning.needs_person` (30.21)

Quando um item entra na fila "Para aprovar" (ou sai dela) o Livro publica o evento, no padrão de `session.needs_person`. Quem
decide é `application/espera.py::AvisadorDeEspera` (compara o item antes e depois, memória do último aviso por `kind:ref`); a
faixa e o motivo saem de `domain/espera.py::classificar_espera`, tradução da política de risco (30.10). A porta é `PortaDeEventos`
(`ports.py`), o adaptador sobre o `EventBus` é `infrastructure/eventos.py`, e o catálogo entra como fatos de risco
(`CatalogoDeRisco`, nunca texto de ação). Os pontos de chamada: `mudar_estado`, `propor`, `avisar_item` (a tela absorvida) e
`avisar_mudanca_nativa` (os ouvintes das lojas de receita e fluxo). Contrato do payload: `api-contract.md`, adendo v0.49.
Um gesto da pessoa que passa por dois estados (`mudar_status_nativo`: candidata, validada, publicada) pode publicar entrada e saída
na mesma ação. `state.py` passa `eventos=self.bus`.

**A receita e o fluxo como o dossiê os lê (30.33).** A faixa do aviso de uma fonte nativa é a classe do dossiê do
curador. Na receita, a capability é derivada do conteúdo (a etapa de origem, única e não ambígua). No fluxo, cada etapa
leva os fatos do catálogo do app dela, e vale a mais restritiva (30.32).

- **O leitor comum.** Os dois leem por `infrastructure/risco_do_conteudo.py` (`RiscoDoConteudo`, `capability_do_item`).
  A montagem o entrega ao `LearningService` (`risco_do_nativo`), que o passa ao `AvisadorDeEspera`.
- **Antes.** O aviso da transição nativa não via nem a capability nem as etapas. Comentar, responder, mandar mensagem
  e seguir saíam B (`efeito_externo`, a lacuna), enquanto o parecer dizia C.
- **Agora.** Esses itens saem C (`alto_risco`). O payload não muda; só a `faixa` e o `motivo`.

## Conteúdo legível no detalhe (30.3)

O detalhe do Livro (`GET /api/aprendizado/{kind}/{ref}`) devolve `conteudo`: o que o item FAZ, em estrutura legível, montado só do que
já está no banco (sem migração; contrato no adendo v0.50 de `api-contract.md`, desenho em `design/aprendizado-vivo.md` §4). A regra de
montagem é pura e mora em `domain/conteudo.py`; as leituras de SQL (a etapa de origem em `steps`, as etapas com o mesmo
`template_hash`, as versões vizinhas da receita) ficam em `FontesSql.conteudo`; `LearningService._conteudo` escolhe a fonte (lição e
tela saem do `content` do item) e `DetalheDoLivro.conteudo` o carrega até `presentation/livro.py`.

- **Lista branca, não cópia.** Cada ação da receita é lida campo a campo (ferramenta, seletores, `commit`, nomes de parâmetro, rolagem).
  O texto digitado não sai nem em pedaço, o valor de parâmetro nunca existe aqui, e `type_secret` ou nome sigiloso (`SENSITIVE_PARAM`)
  viram `segredo: true` sem nome. O domínio copia `TEMPLATE_RE` e `SENSITIVE_PARAM` do executor (não pode importá-lo); um teste
  confere que as cópias não divergem.
- **Capability é derivada.** A receita não a grava. Vale a etapa de origem (`learned_from_step`); sem ela, as etapas com o mesmo
  `template_hash` cujo app (o da etapa ou o de `runs.app_ids`) é o da receita; várias capabilities distintas = `ambigua`. Etapa de
  execução antiga sem app conhecido entra na conta (a dúvida aparece como `ambigua`, nunca escondida).
- **Vizinhas** são a versão imediatamente menor e a imediatamente maior da mesma chave (`recipes.py` define a chave), qualquer estado.
- Fora do escopo desta fatia (§4 do desenho): pré e pós-condição da etapa de origem, aparelhos em que reproduziu, trilha de promoção
  por receita e quadro de versão.

## Estado de versão no detalhe (30.6)

O detalhe do Livro devolve `versao` (contrato no adendo v0.51 de `api-contract.md`, desenho em `design/aprendizado-vivo.md` §7): em que
versões do app o item foi validado, quais estão vivas no parque e o estado por versão. A regra é pura e mora em `domain/versao.py`;
`FontesSql.vivas` lê `device_app_state` (aparelho ativo, app presente) e `FontesSql.versao` junta a chave exata da receita em todas as
versões; `LearningService._versao` escolhe (receita pela chave, tela pela regra `sem_casar`, o resto `independente`).

- **Viva** = observada hoje em aparelho não aposentado (`instances.retired_at`) e com o app (`state <> 'missing'`). É o eixo de comparação.
- **`nao_testado`** é a versão viva em que a chave não tem receita nenhuma; a receita da versão antiga mostra em `nao_testada_em[]`. Nada se
  apaga: se um aparelho voltar à versão antiga, a receita volta a valer (o `find` usa a chave exata).
- **Incerteza explícita.** Sem nenhum aparelho observado a receita com prova fica `desconhecido` (não `comprovado`); a tela só vira
  `incompativel` ou `versao_aposentada` com sinal, e fora disso é `desconhecido` ("sem sinal de quebra"), nunca `comprovado` (esse é de receita).
- Fora do escopo: o painel (30.16), o gatilho `versao_nova` do backlog e o veto por versão em `learning_transitions.app_version`.

## Relações no detalhe (30.7)

O detalhe do Livro devolve `relacoes` (contrato no adendo v0.53 de `api-contract.md`, desenho em `design/aprendizado-vivo.md` §6): o que o item
substitui, por quem foi substituído, de que foi derivado, em que regra declarada foi absorvido e com quem se contradiz. Tudo na leitura, **sem
tabela de arestas**. As regras são puras e moram em `domain/relacoes.py`; `LearningService._relacoes` junta as fontes: o `conteudo` já lido
(vizinhas da receita, `parent_version`), os itens (`parent_id`, `absorvida:`) e as entradas do mesmo tipo (contradição);
`FontesSql.sucessoras_da_habilidade` é a única consulta nova (versões que têm esta por pai).

- **Cada relação diz a `fonte`.** O que o §6 marca como novo sem fonte (`complementa / depende de`, `revisado por`) não sai, e `nasceu de` já está
  em `conteudo.origem`.
- **`contradiz` só onde o `scope_key` identifica a proposição.** Receita (chave exata; duas vivas na mesma chave é anomalia, porque a versão nova
  aposenta as vivas) e tela (o `scope_key` é só o app, então exige o mesmo nome de regra). Fluxo (`match_key` único), habilidade (versões da mesma
  habilidade dividem o comando) e lição, voz e preferência (o escopo não nomeia a proposição) ficam sem contradição derivada; abri-la para a
  lição exige um critério de tema que ainda não existe.
- **Vivo** é `candidate`, `validated` ou `published`; item morto não disputa e não é acusado.
- **A evidência `conflict` não vira relação:** na tela ela aponta para um aparelho (login), não para outro item.
- **Absorvida** liga a regra pelo nome na tela (`regra_declarada`) e pelo commit nos demais itens; a leitura do YAML em si não entra (o alvo é o nome).
- Fora do escopo: o painel (30.16) e a relação `revisado por` (do curador por IA, §8.5).

## Conta removida (29.23)

`esquecer_conta(db, *, profile_id, account_id, handle, app_id)` (`app.modules.learning`, implementação em `infrastructure/esquecer_conta.py`) é a
parte do Aprendizado do item 29.23: a conta bloqueada sai da plataforma como se não existisse e a persona continua. Reescreve o rastro
TEXTUAL (o `handle`, com e sem `@`, e o `account_id`) para o marcador exato `[conta removida]`; a frente Android cuida de `memory_items`.

- **Roda na transação de quem chama**: mesmo `db`, sem commit, rollback nem transação própria, sem I/O fora do banco. Devolve `{tabela: linhas}` (0
  incluso) sem nenhum texto da conta. Idempotente: a segunda chamada devolve zeros (o marcador já existente não é reaberto, nem quando o handle é `conta`).
- **Não apaga linha, não muda `content_hash`/`dossie_hash` nem coluna de id/chave** (`profile_id`, `scope_*`, `instance_id`, `source_ref`,
  `cluster_key`), e não toca receitas, fluxos nem versões de habilidade. `profile_id` e `app_id` não restringem a varredura: o rastro pode estar em escopo alheio.
- **Colunas varridas** (texto livre; JSON em texto conta): `learning_items` (`content`, `summary`, `provenance`), `learning_evidence` (`detail`,
  `origin_ref`), `learning_transitions` (`reason`), `learning_backlog` (`title`, `failure_screen`, `notes`, `baseline`, `verification`),
  `learning_reviews` (`dossie`, `saida`, `validade`, `override_motivo`, `resultado_posterior`), `learning_signals` (`note`, `data`). `learning_exposures` e
  `learning_daily` não têm texto livre.
- **Casamento seguro**: o `LIKE` (com `LOWER`, que o PostgreSQL não ignora maiúscula) só pré-filtra; a troca é por regex com fronteira de palavra. Handle
  `ana` não vira "b[conta removida]na"; `.`, `_` e dígito colados prolongam o handle (`ana.silva`, `ana_silva2` não são `ana`), o ponto de fim de frase não.
  `account_id` casa exato (hífen conta como parte). Handle vazio ou só `@` não faz nada.
- **Limite conhecido**: `learning_evidence.origin_ref` entra num índice único; se a troca colidisse com outra linha, ela é PULADA para não derrubar a
  transação do chamador (o rastro fica naquela coluna). Prova `simulated`: `backend/tests/test_learning_esquecer_conta.py` (SQLite); PostgreSQL e uso real: `not_run`.

## Curador, domínio e política de risco (30.10)

Só domínio puro (desenho em `design/aprendizado-vivo.md` §8.2-8.4); a porta, o laço e o orçamento são do 30.11.

- **Política de risco** (`domain/politica_de_risco.py::classificar`, fonte única): recebe `FatosDeRisco` (o `commit` do conteúdo,
  `human_origin`, se o app tem catálogo, os `FatosDoCatalogo` da etapa e sessão/autenticação) e devolve a classe, as razões em ordem
  e o motivo do evento. Vale a mais restritiva: `commit` com fatos da etapa que dizem "sem efeito" é C (`commit_fora_do_catalogo`);
  sem fatos da etapa (capability não derivável) o `commit` é B, como na 30.21. O `commit` em app SEM catálogo
  (`commit_sem_catalogo`) é **B**, aprovado em lote: o dono confirmou em 03/10, e a emenda para C da mesma madrugada
  (PR #127) foi revertida. A família envio/publicação/exclusão entra por
  `familia_do_efeito`, dado que os catálogos ainda não declaram. `conferir_aceite`: a IA nunca decide; aceitar parecer é da pessoa,
  em lote só na B; na A o parecer é só registro e `conferir_aceite` recusa qualquer efeito dele. `ia_permitida`: A
  `so_com_sobra` (depois das prioridades 1 a 4; o corte é do 30.11), B e C `sim`. `classificar_espera` (30.21) só traduz a classe para a faixa do evento.
- **A classe do fluxo pela etapa mais restritiva (30.32).** O fluxo não tem UMA capability, então o dossiê o
  classificava sem fatos do catálogo: todo fluxo com efeito caía em `commit_sem_fatos_da_etapa` (B). Agora
  `FatosDeRisco.etapas` leva cada etapa do fluxo (`EtapaDeRisco`: a capability, se é de efeito e os `FatosDoCatalogo` do
  app DELA). O `app_id` da etapa (12.1) vira pacote pela tabela `apps`, e o nulo é o app do fluxo.
  - As razões do catálogo são a união das etapas: os fatos de uma etapa só acrescentam razão, e a classe nunca desce.
  - Etapa de efeito sem fatos mantém `commit_sem_fatos_da_etapa`.
  - Etapa de efeito que o catálogo diz sem efeito é `commit_fora_do_catalogo` (C).
  - As etapas entram em `FatosDeRisco.como_dados` só quando alguma tem fatos (como o `reaprendido`). O dossiê de
    receita, lição, tela e fluxo sem catálogo não muda de hash.

  Na cópia do banco do central de 03/10 (deploy 9), 9 fluxos do Instagram passam de B a C: comentar, responder, mandar
  mensagem e seguir. Todos têm uma etapa `risk: high` no catálogo; o curador os revê com o dossiê novo. Os outros 24
  ficam na classe de antes.

  O aviso `learning.needs_person` usa a mesma leitura desde o 30.33: as etapas do fluxo e a capability da receita
  (ver a seção do evento).
- **Dossiê** (`domain/curador.py::montar_dossie`): fatos já lidos (identidade sem título nem resumo, conteúdo legível do §4 por
  lista branca, até 30 evidências mais recentes com o total, trilha sem o motivo livre, relações, grupos de falha, votos sem nota,
  intervenções, e saúde, versão e política vigente quando fornecidas). Cada fato tem id citável (`ev:`, `run:`, `tr:`, `voto:`,
  `sinal:`, `fk-`, `<kind>:<ref>`, e as seções `item`, `risco`, `conteudo`, `saude`, `versao`, `politica`). `dossie_hash` = sha256
  do JSON canônico, com as listas em ordem canônica e sem relógio (a idade sai de `criado_em`). O conteúdo do fluxo leva o
  `app` principal do plano, os `apps` exigidos (`flow_required_apps`) e o `app` de cada etapa (nulo = o principal): sem eles,
  o fluxo que atravessa apps (12.1, ler no Outlook e procurar no Instagram) parecia rodar todo no app principal.
- **Contrato de saída** (`validar_saida`): escolha entre RÓTULOS FECHADOS, pensada para um adaptador de `choice` (provedor Jev):
  `decisao` (obrigatória), `faixa`, `causa`, `riscos`, `inconsistencias`, `falta`, com as opções em `OPCOES_FECHADAS`; `alvo` e
  `evidencias_citadas` escolhem entre os ids do dossiê (`opcoes_do_dossie`). Citação inventada, rótulo fora do conjunto ou campo extra
  invalidam. A confiança vem da `probabilidade` da escolha que o adaptador mede (entrada opcional; limiares 0,60 e 0,85); sem ela, um
  rótulo categórico; nunca número dito pela IA. A `conclusao` (≤ 300) é o único texto livre, opcional, e não entra na decisão. A
  `faixa` da IA nunca afrouxa a da política (`faixa_efetiva`). A falha vira `invalida:<motivo>` (vocabulário fechado, cabe em
  `learning_reviews.validade`); o `Parecer` válido serializa na forma de `learning_reviews.saida`. Nenhum prompt no módulo: o template
  é do hub.
- Fica para o 30.11: a `TriagemDeTexto` do dossiê e das listas livres antes de gravar, o corte por custo (`tamanho_em_bytes`), e o
  `RiscoDoRegistro` preencher `familia_do_efeito` e `interacao` quando o catálogo os declarar.

## Saúde do item (30.4)

Cada item do Livro (lista, `pendentes`, `revisar` e detalhe) traz `saude`: dimensões medidas, UM rótulo por regra e os `motivos[]` que o
produziram (contrato no adendo v0.52 de `api-contract.md`; desenho em `design/aprendizado-vivo.md` §5, com as regras da proposta D-5
medidas no banco real). A regra é pura (`domain/saude.py`); `LearningService.saude_de` é a única fonte do cálculo e `saudes` a aplica à
lista, de modo que a lista e o detalhe nunca discordam. O rótulo é só leitura: não é estado, não move nada.

- **Ordem (o primeiro que casa vence):** `inativo`, `em_prova`, `degradando` (`consecutive_fail ≥ 2`, eficácia `< 0,8` com `≥ 5` usos, ou
  contra/conflito em 7 dias), `sem_evidencia` (publicado há ≥ 14 dias e nunca usado), `parado` (sem uso há mais de 14 dias),
  `pouca_amostra` (< 5 usos), `saudavel`. Limiares em `aprendizado.saude` (`Ajustes.saude`).
- **Sem dado não é bom nem ruim.** Dimensão sem medida sai `desconhecida` (`valor: null`, nunca 0); publicado sem contador de uso, sem data
  do último uso ou sem eficácia/contestação medida é `indeterminado`. Habilidade e itens sem contador de uso (tela, lição, voz,
  preferência) ficam `indeterminado` até haver fonte de uso por item.
- **Eficácia acumulada.** Receita: `replay_ok/(ok+fail)`; fluxo e itens: evidências a favor/contra. Não há janela dos últimos N usos por item.
- **Lacunas conhecidas.** `obsoleto_provavel`
  veio no 30.14 (seção abaixo); `intervencao_humana` e `versao` ficam `desconhecida`; `acoes[]` não traz `motivo_de_bloqueio`. A lista lê a evidência de cada
  publicado (uma consulta indexada por item): trocar por consulta em lote se a lista crescer.

## Curador, aplicação (30.11)

Desenho em `design/aprendizado-vivo.md` §8.5-8.8 e §8.11. O `AppState` liga o adaptador do hub de IA (30.12, frente Jev); a montagem
sem adaptador (testes) usa o SIMULADO.

- **Porta** `CuradorDeIA` (`application/ports.py`): `revisar(PedidoDeRevisao) -> RespostaDeRevisao`, mais os atributos `provedor` e
  `simulado` (vão ao registro). `PedidoDeRevisao{dossie, dossie_hash, classe: A|B|C, opcoes: dict[str, list[str]], modelo_sugerido:
  triagem|escalada}` (opções = `OPCOES_FECHADAS` + `opcoes_do_dossie`; `escalada` só na C) e `RespostaDeRevisao{bruto, probabilidade,
  modelo, usd, ai_call_id}`. O hub valida só o JSON; o learning valida com `validar_saida`. Falha do provedor chega como
  `RecusaDoProvedor(kind)` (o adaptador traduz o `AIError`); `kind = budget` para o lote da volta sem nova tentativa.
- **Adaptador simulado** (`infrastructure/curador_simulado.py`): determinístico, sem rede e sem custo; grava `provedor = simulado` e
  `simulated = 1` (nunca prova). É o padrão da montagem quando ninguém passa `curador_de_ia` (testes).
- **Adaptador do hub** (30.12, `infrastructure/curador_do_hub.py::CuradorDoHub`): chama `AIRouter.review_knowledge` (papel `plan`
  emprestado, `origem = curador`, `ref = dossie_hash`; template e esquema em `planning/curador.py`, `VERSAO_DO_TEMPLATE`). A volta roda
  numa thread e a chamada vai ao laço do processo (`run_coroutine_threadsafe`, laço passado pelo `AppState.start`); sem laço ou sem
  resposta em `TIMEOUT_S` é `RecusaDoProvedor` (`unavailable`/`timeout`). O `AIError` vira `RecusaDoProvedor(kind)` com o mesmo
  `kind`. Com chamada, grava a linha em `ai_calls` (`add_usage`) e devolve `usd` e `ai_call_id` MEDIDOS, lidos da própria linha; sem
  chamada (hub simulado) os dois ficam `None`. `provedor` é o do `Usage` (ex.: `anthropic`) e `simulado` acompanha o hub.
- **Laço** (`infrastructure/ligar_curador.py::LacoDoCurador`, a cada `aprendizado.curador.intervalo_s`), separado do
  `PassoDeCuradoria` e sob a trava de líder `curadoria` (ADR-064; a tomada é idempotente por dono). O `AppState` sobe os laços de
  `LearningService.lacos` (uma linha em `state.py`). Modo e intervalo são lidos a cada volta. A 1ª espera depois da subida
  conta da última revisão gravada (`RegistroDeRevisoesSql.mais_recente`), com piso de 60 s (K-087): antes, cada restart
  zerava a hora. Com as 6 subidas da tarde de 03/10 (~15:05Z a 17:18Z), nenhuma a 1 h da seguinte, o curador não pôde
  rodar até ~18:18Z.
- **Modos**: `off` (padrão) não roda; `shadow` revisa, grava em `learning_reviews` e publica `learning.needs_person` com
  `motivo = parecer_da_ia` quando um parecer B ou C novo e válido fica pronto para item que JÁ espera o dono; `on` revisa igual e,
  desde o 30.17, mostra o parecer na fila e no detalhe e abre o aceite da pessoa (seção abaixo). A IA nunca decide: nada
  transiciona no curador (`conferir_aceite`).
- **Gatilhos ligados**: `nova_pendencia_do_dono` (fila "Para aprovar"), `a_revisar`, `degradando` e `obsoleto_provavel` (saúde do
  publicado), `conflito` (publicado com relação `contradiz`), desde o 30.17 `pedido_da_pessoa` e, desde o 30.31, `evidencia_chegou`. `versao_nova` e
  `grupo_de_falha_acima_do_minimo` existem no vocabulário e ainda não têm fonte. Dossiê (`infrastructure/dossies.py`) do detalhe do Livro, com a evidência lida pelo id;
  sem grupos do backlog, votos e intervenções nesta fatia.
- **Filtros**, em ordem: modo → (item, `dossie_hash`) já revisado → cooldown (`cooldown_h`) → orçamento → prioridade. A triagem de
  credencial corre nas folhas de TEXTO do conteúdo do dossiê (o JSON inteiro não: a regra recusa hash longo, data ISO e `receita:12`);
  recusa = linha `recusada:triagem` sem o dossiê. Chaves de identificador, hash, data ou rótulo fechado ficam fora da triagem
  (`_CHAVES_ESTRUTURAIS`), entre elas a `variante` da receita (`en-US/xhdpi`): sem ela na lista, 24 de 26 receitas da cópia do
  central saíam `recusada:triagem` e o curador nunca revisava receita (achado no ensaio do 30.17, 03/10). A `conclusao` com cara
  de credencial é gravada como `null` (o parecer segue válido).
- **Orçamento** (`domain/orcamento_do_curador.py`): `B_W = min(alfa·G_W, k·N_W·c̄)`, `G_W` = `SUM(learning_daily.usd)` na janela (sem
  filtro de falha); `N_W` = revisões da janela + elegíveis da volta; `c̄` = média do `usd` MEDIDO ou, sem medida, da estimativa
  (`tamanho_em_bytes`/3 tokens × o preço de entrada mais caro de `ai.prices` + 400 tokens de saída). A estimativa só decide; o gasto
  já feito sem medida entra por ela, recalculada do dossiê gravado. Ordem estrita: 1 contra/conflito em publicado B/C, 2 classe C,
  3 falha recorrente, 4 classe B, 5 classe A (sempre por último, mesmo contestada: `so_com_sobra`). O corte é `orcamento_da_janela`
  (motivo próprio, não o `fatia_curador` do hub), `gasto_da_hora` (`B_W/W/2`, conferido sobre o já gasto), `pico_de_entrada`,
  `lote_interrompido` ou `erro_do_provedor`; o corte NÃO vira linha (não gasta a chave (item, dossiê)) e fica no resultado e no log.
  Acima de `c_max = m_cmax × mediana` das ESTIMATIVAS da volta (30.30: estimativa com estimativa; o custo medido entra só no
  c̄, porque a estimativa usa o preço do modelo mais caro e, com o curador num modelo barato, a mediana medida recusaria
  tudo), o dossiê é refeito com 10 e depois 0 evidências; se ainda passar, `recusada:custo`. O item já
  revisado com um dossiê cortado não volta à IA com o inteiro enquanto o estado for o mesmo (confere as variantes antes do pedido).
- **Custo**: a 069 declara `usd REAL NOT NULL DEFAULT 0`; o curador grava `usd = 0` = NÃO MEDIDO (só `usd > 0` conta como medida).
  Desde o 30.12 a `RespostaDeRevisao` traz `usd` e `ai_call_id` medidos pelo hub; a 075 grava o `ai_call_id` (a revisão fica ligada à
  chamada paga), e a gravação do `usd` espera a unificação do saldo na rubrica (`design/hub-de-ia-fora-de-execucao.md`, PENDÊNCIA).
  A `RespostaDeRevisao.simulado` (opcional) diz se AQUELA resposta foi simulada e vale sobre o `simulado` do adaptador: é ela que
  decide se o parecer avisa o dono.
- **Custo pela chamada ligada (I3, 03/10).** A revisão gravada com `usd = 0` e com `ai_call_id` tem o custo MEDIDO na
  chamada ligada (`revisoes_sql.custos_das_chamadas`). A regra é a do `/api/usage`: custo declarado onde há, tokens ×
  preço do modelo onde não, provedor simulado a US$ 0. Vale na janela do orçamento, nas métricas e na lista
  `/revisoes`; o registro só a aplica quando recebe `precos` (o curador e as métricas recebem).
  - Na cópia do banco do central de 03/10, as 46 revisões anteriores ao 30.30 somam US$ 0,6717, o mesmo do
    `/api/usage` do curador.
  - Antes, `metricas.curador.usd` dava 0,0 enquanto o orçamento, no mesmo payload, estimava 0,6418.
  - Sem backfill e sem migração.
- Fica para depois: o alerta do pico como evento + Problem em `/api/health` (hoje só log), o aviso a 80 % de `B_W`, as fontes dos três
  gatilhos sem fonte. O `resultado_posterior` veio com a 30.35 (seção "O desfecho medido da revisão").

## O parecer diante da pessoa (30.17)

Desenho em `design/aprendizado-vivo.md` §8.8, §11.2 e §11.3; contrato no adendo v0.72 do `api-contract.md`. Domínio em
`domain/parecer.py`; aplicação em `application/pareceres.py` (`ServicoDePareceres`, pendurado no Livro por `ligar_curador`
com o MESMO registro e a mesma fonte de dossiês do curador); leitura e gravação em `infrastructure/revisoes_sql.py`.

- **Visibilidade** (`parecer_visivel`): em `on`, sempre; fora dele, só o parecer já decidido. A sombra mede a IA contra a
  decisão da pessoa sem que ela veja a sugestão (D-3); o detalhe só avisa que há um parecer escondido.
- **Rótulo.** É o único produtor de rótulo humano de `learning_reviews` (decisão da orquestradora, 03/10: sem caminho
  paralelo). Toda transição de pessoa pelo Livro (`/status`, os `PUT` legados e a evidência inválida do 30.23) rotula o
  parecer pendente do estado de antes,
  DEPOIS da transição e sem nunca travá-la (uma falha do rótulo só vai ao log). Vista (`review_id` ou modo `on`):
  `aceitou` ou `recusou`, pelo lado da sugestão (`Direcao`: sobe, desce, espera). Às cegas: o rótulo da própria ação, com
  `override` pelo lado. O instante da decisão é o `created_at` do sinal `parecer_decidido` (a 069 não tem coluna).
- **Gesto** (`responder`): aceitar dá UM passo do lado sugerido (`acao_do_aceite`); descer é desligar ou rejeitar, nunca
  aposentar; `substituir`, `fundir` e os lados de espera são concordar, sem transição. `conferir_gesto` recusa, nesta
  ordem: inválido, já decidido, oculto, simulado, desatualizado e a classe (`conferir_aceite`, com a mais restritiva entre a
  classe gravada e a do dossiê de agora). O CAS da decisão vem antes da transição, na mesma transação.
- **Pedido de revisão**: sinal `pediu_revisao` com o `dossie_hash`; o curador o lê como o gatilho `pedido_da_pessoa`
  (`JANELA_DO_PEDIDO_DIAS` = 7; atendido = revisão do item depois do pedido). Só em `on`; o dossiê já revisado responde com
  a revisão que existe.
  - O pedido pula o cooldown e, desde o 30.30, fura a fila: prioridade `PEDIDO_DA_PESSOA` (0), na frente de todos e
    também no pico. Continua sob o teto da hora e o orçamento da janela: com a hora gasta, espera a volta seguinte.
  - A classe A segue só com sobra, mesmo pedida (decisão do dono, 02/10).
  - O motivo (03/10): 12 pedidos esperavam atrás de ~40 itens, a ~6 por volta, porque até então a prioridade era a dos
    outros gatilhos do item.
  - A revisão continua UMA por (item, hash do dossiê): o pedido sobre um dossiê já revisado não chama a IA. Uma
    transição de estado (desligar, por exemplo) entra na trilha e muda o hash.
- **Painel** (`features/aprendizado/ParecerDaIA.tsx`, `parecer.ts`):
  - a seção "Parecer do curador" no detalhe: sugestão, classe, conclusão, o que o curador citou (com link), aceitar ou
    recusar com motivo, pedir revisão e histórico;
  - a frase "Parecer do curador: …" na linha da fila;
  - "Aceitar pareceres do curador" em lote, só na classe B.
  O painel não decide regra: mostra a `acao` e a `recusa` que o backend manda.
  - **A classe que o painel mostra é a do gesto (30.38-c).** No parecer pendente do detalhe e da fila, `classe` é a de
    AGORA (`_classe_de_agora`), e a `recusa` e a `recusa_no_lote` saem dela. `classe_no_parecer` traz a gravada, só
    quando diferem, e o selo diz "(era B no parecer)".
    - Antes, o painel mostrava a classe gravada. Os 9 fluxos do Instagram revisados antes do deploy 10 apareciam como
      "Classe B · aceite em lote", e o clique voltava 409, porque o gesto já usava a classe de agora (C). A validação
      do deploy 12 achou o caso.
    - Os pareceres anteriores (não pendentes) seguem com a classe gravada: é o que o curador viu naquela revisão.
  - Até o deploy 7 o rótulo era "da IA". A validação do deploy 7 (B1) pediu "do curador", coerente com o resto do
    painel; os sinais também passaram a "Pediu revisão ao curador" e "Decidiu um parecer do curador".
  - O que fala do ATOR continua "IA": "na sombra, concordou com a IA", "decide sem a IA" e "pedir a IA nesses passos".
- **A pendência A6, fechada pelo 30.24:** aceitar "manter" num item de "Revisar" é "Confirmar que fica" (abaixo), na
  mesma transação do CAS do parecer; a linha da trilha fica ligada à revisão (`transicao_id`). Fora de "Revisar",
  aceitar "manter" continua só concordando.

## Confirmar que fica (30.24)

O gesto da pessoa que mantém o legado de "Revisar" (receita ou fluxo publicado, com efeito, de antes do D1).

- **O que grava.** Uma linha `published → published` em `learning_transitions`, de quem decidiu, com o motivo
  `confirmado que fica` ou `confirmado que fica: <motivo>` (o motivo é opcional e passa pela triagem de credencial). O
  item não muda: o status nativo segue `active`, e o CAS confere isso na mesma transação (`_no_mesmo_estado`, o mesmo
  caminho da reclassificação do 30.23). Sem migração.
- **A fila.** `decididos_para_revisar` (`domain/livro.py`) lê a ÚLTIMA decisão de pessoa de cada item. Toda decisão de
  pessoa tira o item de "Revisar", menos a confirmação contestada: chegou evidência contrária REAL (`against` ou
  `conflict`, não simulada) depois dela. Aí o item volta, e confirmar de novo o tira outra vez. A regra do retorno é só da
  confirmação: aprovar, reativar e desligar seguem como antes. Parecer da IA não é evidência e não devolve nada.
- **Quem pode.** Só pessoa, só item em "Revisar": confirmar duas vezes, ou um item sem efeito, desligado ou de outro
  tipo, é recusado (409 `state_conflict`, 422 para lição e tela; motivo com cara de credencial, 409
  `note_looks_secret`).
- **O parecer.** Confirmar com um parecer pendente à vista o rotula com `confirmar` (direção "espera"): concorda com
  manter, observar e pedir evidência; recusa desativar e rebaixar.
- **No painel.** Em "Revisar", "Confirmar que fica" ao lado de "Desligar", com o motivo opcional, e "Confirmar
  selecionados" em lote. No histórico do item, a linha aparece como "Confirmado que fica" com o motivo da pessoa
  (`motivo_da_pessoa`, já sem o prefixo). Fora de "Revisar" (`em_revisar` falso) o aviso "vale revisar" sai e o item
  diz quem confirmou (`confirmado`); o que voltou diz por quê (`confirmacao_contestada`).

## Obsolescência (30.14)

`obsoleto_provavel` entra na ordem da saúde depois de `degradando` e antes de `sem_evidencia`, só para o publicado (contrato no adendo
v0.54; desenho §9.2). A regra é pura (`domain/obsolescencia.py`, `domain/saude.py`); a leitura é `application/obsolescencia.py`
(`LeitorDeObsolescencia`, pendurado no serviço por `infrastructure/ligar_obsolescencia.py`), e a lista e o detalhe usam o mesmo
`ContextoDeObsolescencia.sinais`. Sinais com fonte: substituta viva, versão fora do parque ou versão viva sem reprodução (o quadro do
30.6, em lote por `infrastructure/obsolescencia_sql.py`), efeito sem respaldo no catálogo e tela absorvida. O fluxo nunca usado
há `sem_uso_dias` é `sem_evidencia`, como a receita (o mesmo fato com o mesmo rótulo; adendo v0.63). Sem fonte e fora: uso da etapa por outro caminho, duplicado em chave vizinha, habilidade com a mesma `match_key`.

- **Rebaixamento `catalogo_sem_efeito`** (passo da curadoria `RebaixamentoPorCatalogo`, sem IA, idempotente): receita ou fluxo vivo com
  `commit` num app com catálogo (o do registro de apps) que não respalda o efeito — catálogo sem nenhuma ação com efeito (`*`, o Outlook
  hoje) ou capability conhecida, sem ambiguidade, cuja ação não tem efeito. App sem catálogo nunca; capability ambígua ou desconhecida
  num catálogo com efeito vira só o sinal (`duvidoso`). Vai por `LearningService.mudar_estado(by='sistema')`, o mesmo caminho do Livro
  (CAS, trilha, aviso de espera): em prova → `disabled`, publicado → `deprecated` (fluxo: `disabled`). `recipes.py` não foi tocado.
- **Custo.** As receitas são lidas uma vez por leitura do Livro (vizinha seguinte e quadro de versão em lote); o `conteudo` (que pode
  varrer `steps` sem índice em `template_hash`) só para o vivo com `commit` num catálogo que tem efeito.
- **Destino.** O rebaixamento vai sempre para `disabled` (receita `quarantined`), nunca `deprecated`: só assim a pessoa pode reativar (§9.2).

## Falhas com diagnóstico determinístico (30.13)

Desenho: `docs/design/aprendizado-vivo.md` §9.1, só a parte determinística. A revisão por IA quando a causa é `indeterminada` **não está feita** (espera o
hub de IA e o curador, 30.11): a causa `indeterminada` sai como dado, sem chamada, prompt nem porta de IA.

- **Onde:** `domain/diagnostico.py` (puro: regras, `CausaProvavel`, conhecimento envolvido, proposta), `infrastructure/contexto_sql.py` (a leitura do
  contexto das tentativas, nos dois dialetos), `application/falhas.py` (`_diagnosticar`; porta `FontesDeContexto`, à parte de `FontesDeFalha`: a fonte
  que não a implementa deixa só o tipo decidir) e `presentation/falhas.py` (JSON `diagnostico`, linha "causa provável" no md).
- **Conhecimento envolvido** por grupo, das últimas 30 tentativas: receita (`attempts.recipe_id` = exato; senão por pacote + `template_hash` quando a etapa foi
  conduzida por receita = `aproximado`), lições expostas (`learning_exposures`, braço `with`, na etapa), tela da falha (`failure_screen`; a aprendida acha o item
  do livro), fluxo e habilidade da execução. Refs: `receita:<id>`, `li-…`, `tela:<pacote>/<nome>`, `fluxo:<id>`, `habilidade:<id>@<v>`.
- **Causa**, na ordem das regras (primeira que casa): o TIPO decide `teto_de_ia`, `provedor_de_ia`, `sessao_ou_autenticacao`, `aparelho`, `plano`,
  `informacao_da_pessoa`, `catalogo_recusou` (guarda do efeito), `verificador`; `outro` e tipos sem regra são `indeterminada`. Os de navegação e conhecimento
  olham o contexto: maioria conduzida por receita → `versao_nova` (receita `incompativel`), `aparelho` (a mesma etapa terminou bem noutro aparelho da execução,
  a regra de `taskqueue/aproveitamento.py`), `receita_divergiu` (falhou em todos os aparelhos que tentaram, ou a receita está em quarentena/falhando) ou
  `indeterminada` (aparelho único sem quarentena; comparação mista); depois `licao_atrapalha` (≥3 etapas do grupo, ≥3 unidades por braço e taxa de falha ≥10 pp
  acima do controle: indício, não o veredito oficial de 8 por braço), `tela_desconhecida` (falha fora de toda tela declarada, ou `tela_desconhecida_chamou_pessoa`),
  `falta_conhecimento` (só a IA conduziu, sem receita nem lição) e, sem sinal que feche, `indeterminada` com os números.
- **Proposta** (`TipoDeProposta`: `rebaixar_receita`, `revisar_licao`, `reaprender_tela`, `ajustar_catalogo`, `investigar`) com alvo e causa; vira linha `proposta`
  do backlog com `parent_id` = o grupo (`ref` = `<fk>|<alvo>`). É dado para a pessoa: nada rebaixa nem altera. Só rebaixam sozinhos os gatilhos que já existiam.
- **Teto atingido pelo tipo** (`AIError.kind == 'budget'` em `ai_calls.error_kind`, `classificar_pelo_tipo_da_ia`), não pelo texto: o teto do pedido
  ("Orçamento do pedido atingido…") nunca casou com a regra por trecho e caía em `outro`. O tipo vence o texto, inclusive o `failure_kind` gravado (também
  derivado de texto em `repository.finish_attempt`), só na leitura retroativa; o texto fica como `# legado` para a tentativa sem `ai_calls`.
- **Limites conhecidos:** `steps` não guarda a versão do app: `versao_nova` só vem do estado de versão da receita. A comparação entre aparelhos exige a
  mesma execução. (O tipo do erro na tentativa, que só chegava por `ai_calls` e a purga apagava, é `attempts.error_kind` desde o RA-22; com ele, a
  releitura por `ai_calls` não desmente o gravado, e ela segue só para o legado.)
- Prova `simulated`: `tests/test_learning_diagnostico.py`. `not_run` no central.

**O diagnóstico de UMA tentativa abre o ensino da correção (31.111 F4).** O ensino que nasce de uma etapa que falhou
(31.111 F1–F3) lê a causa provável daquela tentativa pela mesma regra do relatório:
`ServicoDeFalhas.diagnostico_da_tentativa(attempt_id)`, com a porta `FontesDaTentativa.chave_da_tentativa`, à parte como
a do contexto. Ela usa o tipo relido pelo texto quando não gravado, o app pela etapa ou pela execução, e os tipos de erro
do provedor nas chamadas dela. `domain/ensino_da_falha.py` traduz a causa em rótulo curto e na pergunta do que mostrar.
`GET /api/training/{id}` leva `origin.diagnostico`, e `POST /api/training/from-run` sem intenção escrita sugere
"Corrigir a etapa «…»: <rótulo>". A causa `indeterminada` não acrescenta nada à intenção, e a intenção da pessoa vence.
Nenhuma IA: a única chamada continua sendo a proposta do próprio ensino. Sem tentativa, sem tipo ou com erro na leitura,
`diagnostico` é `null` e a sessão abre igual. Quem liga é o `AppState` (`TrainingRecorder.diagnostico_da_falha`).
Antes da sessão existir, `GET /api/runs/{run_id}/steps/{step_id}/ensino-sugerido` (31.116, parte 2) devolve a mesma
sugestão (`{intent, pergunta, rotulo, causa}`) para o painel pré-preencher o formulário: só leitura, sem IA, `null` sem
tentativa. `causa` é o código do diagnóstico (adendo v1.82). A pergunta é a do estado da etapa: a etapa em
`waiting_user` parou esperando a pessoa, não falhou, e recebe a pergunta própria (`PERGUNTA_ESPERANDO`: o que ensinar a
partir daquela tela para ela seguir). Nos outros estados, a pergunta é a do diagnóstico. O `origin.diagnostico` da
sessão aberta pelo `from-run` segue a mesma regra, então os dois dizem a mesma pergunta.

## Evidência inválida e o reaprendido (30.23)

Decisão da coordenação (03/10), registrada como emenda ao ADR-054. Desenho: `design/aprendizado-vivo.md` §9.3. Contrato:
adendo v0.70 de `api-contract.md`. Caso que a motivou: a receita 109 e o fluxo `no-outlook-abrir-a-caixa-de-entrada-e-le`,
aprendidos da `r-20261002204347-8c3f6e`, que terminou como sucesso sem comprovar o que fez.

- **Gramática fechada, sem migração.** `domain/evidencia_invalida.py` define o motivo `evidencia_invalida:<run>` (execução
  `r-AAAAMMDDhhmmss-xxxxxx`), a leitura de volta (`run_invalidada`) e o prefixo reservado.
  - `mudar_estado` (rotas `/status` e as legadas) recusa o reservado; a marca só entra por `LearningService.invalidar_evidencia`.
  - Ela aceita só a execução de origem do item (`EntradaDoLivro.nasceu_de`): na receita, a execução de `learned_from_step`;
    no fluxo, o `source_run_id` fora do treino.
  - O vivo vai a `disabled`; o já desligado ganha `disabled → disabled` (`reclassificar_desligamento`, CAS no status
    nativo); o aposentado recusa. A operação é idempotente.
- **Veto da mesma execução** (`ciclo.motivo_do_veto`): é conferido antes de quem decidiu e não tem prazo. Cai só com um
  `Renascimento` de outra execução real (`runs.simulated = 0`, lido por `LeituraSql.execucao_real`). A linha de arrumação da
  loja (`disabled → deprecated`, quando a versão nova aposenta a velha) não conta como decisão.
- **Reaprendido** (`evidencia_invalida.reaprendizado`): derivado da trilha do `scope_key` e nunca gravado. O último
  (re)nascimento do item vem depois da última marca do escopo, sem publicação de pessoa entre os dois.
  - Na receita, renascer é versão nova, e a 109 vira `superseded`.
  - No fluxo, a mesma linha volta de `disabled` a `candidate` (`D1Nativo._pode_reaproveitar`).
  - O item força a classe B (`politica_de_risco`, razão `reaprendido_de_evidencia_invalida`), `requires_owner`,
    "Para aprovar" e o motivo `reaprendido` no `learning.needs_person`.
- **Duas camadas param o sistema.**
  - Na loja: `RecipeStore.shadow` pergunta ao ouvinte (`exige_o_dono`) e para em `validated`; a sombra dos fluxos
    (`SombraDosFluxos._avaliar`) também. Se a leitura do livro falha, o ouvinte não responde (`None`): a candidata não sobe
    nem ganha motivo na trilha, e a próxima concordância pergunta de novo.
  - No repositório: `_mover_receita` e `_mover_fluxo` recusam (`ExigeODono`) o sistema publicando o reaprendido.
  - Depois que uma pessoa publica no escopo, o que nascer ali já segue o D1 de sempre. O aprovado guarda a marca:
    `reaprendido` continua na leitura, sem `por_que_nao_publica`.
- **A evidência da execução marcada fica à vista e não mede:** `invalidada: true` no detalhe; fora da sombra do fluxo, da
  saúde (detalhe e lista) e da versão.
- **Curador (30.11):** o dossiê (`infrastructure/dossies.py`) passa `FatosDeRisco.reaprendido`, então a IA nunca vê o
  reaprendido como A, e deixa de fora a evidência da execução marcada. `FatosDeRisco.como_dados()` só leva a chave
  `reaprendido` quando ela vale: os fatos entram no `dossie_hash`, e a chave sempre presente faria todo item já revisado
  parecer dossiê novo.
- **Relações:** `reaprende` e `reaprendida_por`, só entre receitas (`relacoes.de_reaprendizado`).
- **Painel:**
  - a trilha mostra o selo "evidência inválida" com o link da execução;
  - a evidência mostra "execução invalidada", "não conta como prova";
  - o detalhe tem a seção do reaprendido e o botão "Marcar evidência inválida", com confirmação no lugar;
  - "Para aprovar" explica o motivo.
- **Limites conhecidos:**
  - o escopo da receita inclui a versão do app: numa versão nova a marca não pesa, como o veto de sempre;
  - `ai.recipes_promote_after: 0` não passa pela sombra (pendência A5);
  - os ganchos em `taskqueue/recipes.py` (`ReceitaVista.learned_from`, `OuvinteDasReceitas.exige_o_dono`) são exceção ao "só
    leitura" do §13 do desenho, aceita pela coordenação.
- **Prova `simulated`:**
  - `tests/test_learning_evidencia_invalida.py`: domínio, cenário da 109, fluxo e rota;
  - o vitest de `DetalheRico.test.tsx`;
  - o ensaio no navegador sobre uma cópia do banco do central, com IA simulada (03/10).

  `not_run`: a marca no central (109 e o fluxo, depois do deploy da suíte 6) e um renascimento real.

## Rótulo de intenção (30.25)

O gabarito humano do decisor fechado da intenção (31.x, da Jev). Sem IA e sem custo; a pergunta é CEGA.

- **Quem vira pergunta.** Um minerador no digest da execução assentada (`application/intencao.py`, ligado por
  `infrastructure/ligar_intencao.py`; nenhum gancho novo no taskqueue). Só execução real (`simulated=0`), `completed`,
  com pelo menos uma etapa e TODAS `succeeded` com `result.verified`, sem habilidade casada no plano (`runs.skill_id`),
  e com a cadeia de agora (a RESOLVE da sombra do 31.9, refeita sem efeito) em `sem_casamento` ou num empate sem
  resolvida. Falha provada, etapa incerta, pulada ou confirmada à mão ficam fora. Catálogo vazio não pergunta.
- **O que se grava.** Uma linha por execução em `learning_reviews`: `template_id='intencao'`, `item_kind='execucao'`,
  `item_ref='run:<id>'`, `scope_app` = o app principal, provedor vazio, `usd` 0, sem `saida`, `gatilho`
  `execucao_sem_intencao`. O dossiê guarda só ids: os `skill_id` do catálogo inteiro que a cadeia enxergava (em ordem
  canônica, mesmo num empate), os empatados à parte e a cadeia. O comando nunca entra: o painel o lê da execução.
- **Fora do curador.** Todo leitor do curador (`revisoes_sql.py`) filtra `template_id='curador'`: o rótulo não entra na
  janela do orçamento (C_W), no Revisar, nem na salvaguarda (item, dossiê).
- **A resposta.** `POST /api/aprendizado/execucao/{run_id}/intencao` com um candidato do dossiê GRAVADO ou `nenhum`
  (422 fora disso; 404 sem pergunta; 409 já respondida, por CAS). A decisão deixa o sinal `parecer_decidido` com
  `template_id='intencao'`, `viu` e `override` falsos, que dá a data da decisão. Quem consome é a Jev (`id_opaco`,
  `nenhum` ↔ `ID_NENHUMA`).
- **No painel.** "Qual era o pedido?", no fim de Para aprovar, à parte e fora da contagem (é opcional): uma pergunta por
  vez ("1 de N", com "Pular", que só muda a da vez), o comando inteiro, onde e quando, as opções em ordem alfabética,
  sem o palpite do sistema, e "Nenhuma destas"; com mais de 8, um filtro sem acento. O catálogo de 03/10 tem 26 opções
  e nomes de até 120 caracteres: cada nome ocupa no máximo duas linhas (inteiro no `title`), e dois fluxos com o mesmo
  nome mostram o id. Nos Sinais, a resposta aparece como "Disse qual era o pedido", não como parecer da IA.

## Validação automática do "pedir evidência" (30.31)

O parecer do curador que pede evidência que uma execução produz vira um pedido; um despachante roda essa execução num
aparelho ocioso, e o item volta ao curador quando a evidência chega. Nada transiciona aqui: a evidência entra pelos
caminhos de sempre (a sombra do fluxo, os contadores da receita) e quem decide segue sendo o ciclo do livro.
`aprendizado.validacao.modo` é `"off"` de fábrica: nenhum pedido nasce e nada roda.

- **O pedido** (`domain/validacao.py::pedido_do_parecer`). O curador o pede em `_fechar_o_laco`, depois de gravar um
  parecer válido e NÃO simulado; o adaptador simulado nunca dispara execução real. Só `pedir_evidencia` com uma falta
  que uma execução produz conta: `execucao_real`, `reproducao_em_outro_aparelho`, `reproducao_na_versao_viva` ou `sombra`
  (`voto_da_pessoa` e `decisao_da_pessoa` ficam com a pessoa). As recusas ficam registradas: o pedido nasce `recusada`
  com o motivo. Ordem: tipo sem execução (lição, tela), desligado, vetado, sessão ou autenticação, sem origem (o item
  não nasceu de execução), comando com credencial (a triagem do aprendizado), efeito real, receita sem fluxo ativo
  para o comando. Há um pedido vivo por item (índice parcial da 082), que vale 72 h.
- **O `observar` da classe B (30.34, sim da orquestradora em 06/10).** Desde o 30.73, o parecer B fica em `observar`
  com `execucao_real` e não pede mais a pessoa. O `observar` B com falta que uma execução produz também gera pedido, mas
  SÓ no app de prova (`qa`). O efeito real não gera nem registro, e A e C seguem como antes. A classe é a mais
  restritiva entre o dossiê e a faixa do parecer. O teto, a verba e o ritmo são os de sempre. O que nasce por aqui é
  contado à parte, por estado, em `GET /api/health` (`features.validacao_pelo_observar_b`, persistido em `settings`).
  Sem isso, o fluxo B nunca ganhava a 2ª execução, e a sombra da autopublicação ficava sem casos (re-medida do 30.72).
- **Grupos.** `qa` é o app de categoria `qa`, sem efeito fora da máquina; `leitura` é sem efeito; `efeito_real` nunca roda
  nesta fatia.
- **O despachante** (`LacoDaValidacao`, sob a trava de líder; `intervalo_s` 600). Expira os pedidos velhos: pendente há
  mais de 72 h, rodando há mais de 6 h. Só despacha com o central saudável (`/api/health` sem problemas) e NENHUMA
  execução em curso, porque restart, suíte e deploy seguram ou derrubam execuções. O `planned` só conta como em curso
  no modo `execute`: na execução só de plano ele é o estado final (30.49; em 04/10, três delas seguraram o P4 por mais
  de 30 min). Também precisa de fôlego na janela:
  β = 5% do gasto de IA da operação em 7 dias (o mesmo G_W do curador), mais a verba única `extra_usd` até `extra_ate`;
  e do ritmo, ≤4 por hora. Uma execução por volta: o comando de origem, em OUTRO aparelho (o de origem fica de fora),
  ligado, ocioso, com o app `ready` e SEM conta real logada. O aparelho também precisa ter cada variável de aparelho
  que o plano da prova usa (30.50, `VARIAVEIS_DE_APARELHO`: hoje o `{account_label}`, de `instances.account_label`);
  em 04/10 a prova fec1a1 caiu no android-04, sem conta de QA, e fechou `ator_sem_acao` no `check_account`. Nesta fatia nem a leitura vai a conta real: a regra do
  dono é conferir a tela antes de experimento numa conta real, e o despachante não confere tela; o pedido de leitura
  do Instagram de hoje (só logado em 01, 03 e 06) espera e expira. A volta roda na thread do loop, como o laço de
  pedidos: `RunService.create` agenda o planejamento com `asyncio.create_task`. A execução é comum (`RunService.create`, chave de idempotência `validacao:<pedido>`) e o custo dela é o da
  operação.
- **O fechamento** (minerador do digest). A execução assentou: `feita` se o item ganhou evidência DELA (fluxo: uma
  linha a favor da sombra com o `run_id`; receita: uma tentativa conduzida pela receita que deu certo); senão `recusada`
  (`sem_evidencia` ou `execucao_falhou`). Grava o `usd` medido nas `ai_calls` da execução.
- **A volta ao curador.** O pedido `feita` é o gatilho `evidencia_chegou`, o segundo em força depois de
  `pedido_da_pessoa`, e pula o cooldown. A regra "uma revisão por (item, dossiê)" continua: a evidência nova muda o
  dossiê. A revisão nova fecha a chegada (`revisao_nova_id`) e pode pedir de novo. A chegada NÃO depende do `modo` da
  validação, que é do despachante: a evidência já foi paga, e o gasto da revisão é do modo e do orçamento do curador.
  Antes, a pausa do P4 (03/10 17:18Z) prendia o `feita` das 16:37:59Z.
- **Onde.** Tabela `learning_validations` (082, [`docs/banco.md`](../banco.md)); `application/validacao.py`,
  `infrastructure/validacoes_sql.py`, e `infrastructure/ligar_validacao.py`, ligado em `state.py` depois do `RunService`.
  A execução de validação se liga ao pedido por `learning_validations.run_id` (`runs.pedido_id` é do módulo de pedidos).
- **Limite conhecido**: a chegada cujo item não é revisto (o dossiê não mudou, ou o mesmo hash já foi recusado por
  custo ou triagem) fica `feita` sem `revisao_nova_id` e volta como candidata a cada volta, sem custo (sai antes do
  provedor) e sem segurar pedido novo.
- **Fatia 2 (04/10, adendo v1.11).** Duas peças, sem migração:
  - **A conferência no app de QA** (`taskqueue/oraculo_qa.py`, `Scheduler._conferir_no_app_de_qa`):
    - Quando roda: ao fim de toda execução de validação cujo comando traz `{run_id}` e cuja etapa de efeito é do QA.
    - O que faz: lê o `ContentProvider` do app (`content query`, só leitura) e conta as mensagens daquela execução. A
      linha do tempo ganha a decisão com o número.
    - Com 2 ou mais: grava o fato do 29.58 na etapa de efeito, `{"copias": N, "fonte": "provedor"}`. O veredito do
      30.42, a reprodução do 30.43 e o painel o leem como leem o do verificador. Se o verificador já viu tanto quanto
      o app, fica o dele.
    - Com 0, e a tela tendo comprovado o envio: fica só no diário nesta fatia. É um desfecho novo, que pede decisão
      própria.
    - Os aparelhos de validação (09, 10 e 12) são hospedados pelo central, então o `adb` dele os alcança.
  - **O ensaio só de leitura** (chave `ensaio:<pedido>`, `contracts/origem.py`; `Scheduler._parar_no_ensaio`):
    - O que faz: percorre o fluxo e para ANTES da primeira etapa com efeito fora do aparelho, sem assumi-la. Nenhuma
      tentativa, nenhuma decisão do ator, nenhum toque.
    - Para antes também do PREENCHIMENTO desse efeito (`Scheduler._efeito_que_esta_etapa_prepara`):
      - É a etapa sem efeito e sem ação do catálogo de que uma etapa com efeito ainda por rodar depende diretamente.
        No QA, é o `fill_message` antes do `send_message`.
      - O portão 1 real (r-20261004094430-a3b72b, android-10, 04/10) parou só antes do envio, e o texto ficou digitado
        no `message_input`. Um toque seguinte o enviaria.
      - A ação do catálogo segue, porque é navegação declarada (`OPEN_COMMENTS` antes de `CREATE_COMMENT`), e o
        Instagram digita o comentário dentro da própria etapa com efeito.
      - No plano livre, na dúvida, o ensaio para uma etapa mais cedo.
    - A parada vem ANTES da porta de política. Com catálogo, a etapa de efeito em `approval_required`
      (`CREATE_COMMENT`) seria segurada ali primeiro, com rascunho pago, pedido de aprovação e aviso ao dono, e o
      ensaio não fecharia `cancelled`. Prova `simulated`: `tests/test_ensaio_antes_da_porta.py`.
    - Como fecha: ela e as seguintes ficam `skipped`, e o objetivo fecha `cancelled` pelo sistema, como a prova que
      pediria uma pessoa (30.37).
    - Por que não deixa evidência: o veredito não deixa nenhuma, e o pedido fecha `ensaio_so_leitura`, nunca
      `feita`. Navegar até o botão não é o fluxo, e um `for` do ensaio contaria para a autopublicação (30.34) um fluxo
      cujo efeito nunca rodou. A linha `invalida` do 30.42 continua vencendo.
    - Nada cria execução de ensaio sozinho: ela nasce pela API com a chave.
    - **Prova `real` (04/10, só por leitura do resultado):**
      - Portão 1 no QA: r-20261004094430-a3b72b (android-10). Parou antes do `send_message`, e o provedor do QA ficou
        em 20 → 20; o texto ficou digitado, e isso levou ao ajuste do preenchimento (#230).
      - Repetido com o ajuste: r-20261004110912-ea5b4e. Parou antes do `fill_message`, o campo ficou vazio e o
        provedor em 20 → 20.
      - Instagram, depois do #237 e com o gatilho da orquestradora: r-20261004120738-a9cf9c (comentar, android-03) e
        r-20261004120952-3d7b28 (responder, android-01). Os dois fecharam `cancelled`, com as etapas 1–3 comprovadas.
        A etapa de efeito ficou `skipped` com 0 tentativas, sem rascunho e sem aprovação.
    - **O que o ensaio deixa no livro.** A execução de ensaio não tem `prova_fluxo_id`, então não deixa evidência de
      fluxo nem faz nascer fluxo. As etapas de navegação, porém, rodam de verdade, e a loja de receitas as trata como
      em qualquer execução: reprodução conta, divergência conta e a IA ensina.
      - Nos dois ensaios do Instagram, a `receita:91` ganhou dois `for` (`open_comments_1`).
      - A `receita:73` divergiu (`open_profile_1`), a IA assumiu, e foi a 3ª falha seguida dela: o sistema a pôs em
        quarentena ("3 falhas seguidas ao reproduzir"; no livro, `disabled`).
      - A `receita:166` nasceu candidata, aprendida da IA no 3d7b28, de outro passo.
      - Com a quarentena, a 73 não se reproduz mais. O passo dela volta sozinho: na próxima execução real com o mesmo
        `step_hash`, a busca cai em `quarentena`, a IA faz a etapa e o caminho dela vira a candidata v2 (a 73 passa a
        `superseded`). Ela sobe com 2 concordâncias reais (`ai.recipes_promote_after`). Não é vetada, porque o veto
        (`receita_vetada`) só conta decisão de pessoa ou evidência inválida. Não pede reensino.
      - Quem ensaia no app real deve contar com esse efeito na loja: o ensaio não deixa evidência de fluxo, mas mexe
        nas receitas da navegação.
  - Prova `simulated`: `tests/test_learning_ensaio_e_oraculo.py`. A conferência no QA também tem prova `real` (04/10):
    o pedido lv-1be2a60f37a73b12 rodou como r-20261004121625-92d0b4 no android-07 (`account_label` qa-user-07), com
    6/6 etapas e 1 mensagem da execução no provedor do QA: "o efeito saiu uma vez", evidência `for`, US$ 0,0631. O
    fluxo foi a `validated` pela sombra. Antes dele, o P4 tinha ficado parado por execuções só de plano (30.49) e
    caído num aparelho sem conta (30.50).
- **Fora das fatias 1 e 2:** a classe do fluxo pela etapa mais restritiva (30.32).
- **Prova** `simulated`: `tests/test_learning_validacao.py`, `tests/test_learning_validacao_sql.py`,
  `tests/test_learning_curador.py` (`*validacao*`, `evidencia_chegou`). Ligar no central é `modo: "on"` depois do
  deploy que a levar (P4 do desenho: verba única de US$ 2,5 para os 15 itens do QA).

## O desfecho medido da revisão (30.35)

Catorze dias depois de uma revisão do curador, a curadoria grava o que aconteceu com o item. Vai em
`learning_reviews.resultado_posterior` (coluna da 069, sem migração) e em `resultado_em`. É o rótulo 2 do golden set
do Jev ([design/jev-golden-set.md](../design/jev-golden-set.md) §2), e o relatório do 31.10 o lê sem mudança. Contrato
com a orquestradora de 03/10, com a emenda do degrau D-5.

- **Quem ganha o campo.** As revisões do curador (`template_id = curador`), válidas e reais, de receita e de lição,
  cuja janela de 14 dias já fechou. Grava uma vez só: o `UPDATE` exige `resultado_posterior IS NULL` e nunca
  sobrescreve. A janela de 30 dias do golden set fica fora, porque o campo é um só.
- **A regra** (`domain/resultado_posterior.py`, pura; vale o mais grave):
  1. desligado na janela → `descartar`;
  2. desceu na escada → `rebaixar`;
  3. o uso da janela reprova no degrau D-5 da saúde (os limiares de `aprendizado.saude`: 2 falhas seguidas, ou sucesso
     abaixo de 0,8 com ≥ 5 usos) → `rebaixar`;
  4. ≥ 1 uso e sucesso ≥ 0,8 → `manter`;
  5. o resto → `sem_desfecho`.
- **O que não conta.**
  - `deprecated` é absorção (a receita substituída por versão nova), não desfecho.
  - A linha `disabled → disabled` só reclassifica um desligamento antigo (30.23).
  - `sem_desfecho` fica fora da régua da triagem: o relatório o ignora, e a linha não volta a ser avaliada.
- **O uso** (`infrastructure/resultado_posterior_sql.py`).
  - Na receita, a tentativa que ela conduziu (`attempts.recipe_id`, `succeeded` ou `failed`), datada por `finished_at`.
    `interrupted` e `uncertain` ficam fora.
  - Na lição, a exposição no braço `with`, com o desfecho preenchido.
  - Nos dois, ficam fora a execução simulada e a marcada como inválida no item.
- **Onde roda.** `GravadorDoResultadoPosterior` (`application/resultado_posterior.py`) é um passo da curadoria periódica,
  sob a trava de líder, sem IA. Grava até 500 revisões por passo.
- **Prova.**
  - `simulated`: `tests/test_learning_resultado_posterior.py` (cada regra, o mais grave, a janela, o uso fora dela, o
    simulado, o invalidado e a idempotência).
  - `real`: a partir de 17/10, quando fecha a janela das primeiras revisões, de 03/10.

## Métricas (30.8)

`GET /api/aprendizado/metricas?app=&dias=` e `GET /api/aprendizado/revisoes` (adendo v0.89 do contrato). A aplicação é
`application/metricas.py`: contas puras sobre uma porta de leitura (`infrastructure/metricas_sql.py`), sem contador
novo em memória. Cada bloco da resposta é uma linha da tabela do §10 do desenho. O que vale para quem lê:

- a composição e a saúde saem das mesmas funções da visão por app (`servico.livro`, `servico.publicados`); a métrica
  não tem rótulo próprio. A economia é a do `taskqueue/aproveitamento.py`, injetada pela montagem (só leitura);
- ausente é `null`, com o `n` ao lado; simulado fica fora, e a revisão simulada é contada à parte;
- "falhas evitadas" é um PROXY rotulado: taxa de falha com receita × só IA nas etapas que tiveram as duas conduções;
- a receita tem evidência datada desde o 30.39 (seção abaixo), mas ela é o registro dos contadores e fica FORA das
  métricas (`linhas.fora_da_reproducao`): o "sucesso depois de promovido" segue sendo de fluxo e lição;
  os contadores `replay_ok/replay_fail` seguem sem data;
- o orçamento do curador usa a conta da volta (`janela_do_orcamento`, extraída de `curador.py` para as duas servirem)
  com as revisões já gravadas: o B_W é um piso da próxima volta, o `uso` é um teto e o aviso, a 80 %, sai cedo. O
  B_W é o menor de dois ramos, α·G_W (`teto_alfa`) e k·N_W·c̄ (`pelas_revisoes`); `ramo` diz qual manda (30.33-C).
  Enquanto manda o das revisões, o `uso` fica perto de 1/k por construção. Sem `usd` medido
  (antes do #144-B no central), o c̄ é a média das estimativas; sem ela o B_W cairia a zero, defeito que o ensaio pegou;
- a série quebra no deploy 8 (LT-6, acima): compare janelas do mesmo lado de 03/10 09:06:28Z.

## Autopublicação do fluxo B em sombra (30.34)

A emenda de 03/10 à D1 ([ADR-054](../decisoes.md)): o FLUXO de classe B pode publicar sozinho, mas só depois de provado
em sombra. Nesta fatia (30.34-A) a regra vai até `shadow`; publicar de verdade é a 30.34-B.

- **A regra** (`domain/autopublicacao.py`, pura) é a de um fluxo em `validated` segurado pela D1, não reaprendido, de
  classe B. Precisa de três coisas juntas:
  - o parecer ATUAL do curador sugerindo `aprovar` com confiança `alta`, real, não decidido e sobre o estado de agora;
  - ≥ 2 execuções reais;
  - em ≥ 2 aparelhos, e nenhuma evidência real contra.
  - `avaliar` devolve TODOS os motivos de fora (`MotivoDeFora`), para o relatório dizer o que falta a cada item.
- **Os fatos** (`application/autopublicacao.py`) são os do resto do módulo:
  - a classe é a mais restritiva entre o dossiê de agora e o parecer, a regra do aceite; sem dossiê, C;
  - a evidência é só a real do conteúdo atual (a marca do conteúdo), sem as execuções invalidadas (30.23).
- **O livro da sombra** (`infrastructure/autopublicacao_sql.py`), sem migração:
  - o caso é o sinal `autopublicaria` (`source_ref = autopublicaria:<item>`, `created_by = sistema`), um por item pelo
    índice único, com `data` = modo, `review_id`, execuções, aparelhos, `content_hash` e título;
  - fica fora da aba Sinais, porque não é gesto.
- **Regressão em 7 dias:** evidência real contra ou conflito; o item desligado (`learning_transitions`); uma pessoa
  recusando o parecer (o sinal `parecer_decidido` com `recusou`, que guarda a data da decisão).
  - `balanco` conta casos, abertos, limpos e regredidos, e a taxa sobre os FECHADOS (`null` sem nenhum).
  - Ela libera o `on` só com ≥ 30 fechados e ≥ 90 % limpos.
- **O laço** (`LacoDaAutopublicacao`) roda sob a trava de líder, numa thread: só lê o livro e grava sinais. Sem IA,
  sem aparelho.
  - A primeira volta sai 60 s depois do início (`PRIMEIRA_VOLTA_S`), e as seguintes de `intervalo_s` em `intervalo_s`
    (3600 de fábrica).
  - Até 04/10 a primeira esperava o intervalo inteiro. Com dez reinícios entre o deploy 11 e o 20, a sombra pode ter
    rodado pouco.
  - Toda volta deixa uma linha `info` no log (avaliados, publicaria, casos novos), mesmo vazia.
  - A última volta do processo fica em `curador.autopublicacao.ultima_volta` (`em`, `modo`, `avaliados`, `publicaria`,
    `marcados`; `null` antes da primeira). É em memória, e o reinício zera.
  - Sem esse rastro, "0 casos" não separava "avaliou e ninguém passou" de "não rodou" (relatório de 04/10).
- **Nas métricas** (`/api/aprendizado/metricas`), o bloco `curador` ganha a chave `autopublicacao`. É o balanço, global,
  com o modo e os limiares; a chave é aditiva (adendo v0.93) e só aparece com o serviço composto.
- **Config:** `aprendizado.autopublicacao.modo: "off" | "shadow" | "on"` e `intervalo_s`. `on` sem o balanço
  liberado é igual a `shadow` (30.34-B, abaixo).

### O relatório da sombra (rascunho do contrato da 30.34-B)

Rascunho de 03/10, pedido pela orquestradora para a 30.34-B começar no dia em que a sombra ligar (deploy 11). Sem código
nesta fatia. O relatório vai à orquestradora ANTES de qualquer `on`.

**O que o deploy 11 grava em `shadow`.** O laço (de hora em hora, sob a trava de líder) avalia os fluxos em `validated`
que a D1 segura. Marca UMA vez cada um que publicaria: o sinal `autopublicaria` com `source_ref = autopublicaria:<item>`
e `created_by = sistema`. O `data` traz `modo`, `review_id`, `execucoes`, `aparelhos`, `a_favor`, `content_hash`,
`titulo` e `marcado_em`. A confiança não fica no sinal: ela é do parecer, lido pelo `review_id`.

**Os campos do relatório.**

| Bloco | Campo | De onde sai |
|---|---|---|
| Cabeçalho | data, commit, modo, desde quando há sombra | `GET /api/health` (`commit`); o primeiro sinal `autopublicaria` |
| Cabeçalho | limiares (30 casos fechados, 90 %, 7 dias) | `ParametrosDaAutopublicacao` (os mesmos de `curador.autopublicacao.limiares`) |
| Balanço | `casos`, `abertos`, `limpos`, `regrediram`, `taxa_sem_regressao`, `libera` | `ServicoDeAutopublicacao.relatorio()` = `curador.autopublicacao` das métricas |
| Por caso | item, título, app, `marcado_em`, `fecha_em` (marca + 7 d) | o sinal |
| Por caso | `review_id`, decisão, confiança, classe, `criado_em` do parecer | `learning_reviews` pelo `review_id` |
| Por caso | execuções, aparelhos e `a_favor` na marca | o `data` do sinal (contados pela marca do conteúdo, sem as execuções invalidadas) |
| Por caso | o conteúdo mudou desde a marca? | `content_hash` do sinal × o do fluxo agora |
| Por caso | desfecho (`aberto`, `limpo`, `regrediu`); se regrediu, o tipo, a data e o id do evento | `LivroDaSombraSql.eventos`, que hoje dá o tipo e a data; o id (da evidência, da transição ou do sinal `parecer_decidido`) entra na 30.34-B |
| Fila | os avaliados que ficaram de fora, contados por `MotivoDeFora` | `ServicoDeAutopublicacao.avaliar` sobre os candidatos da hora, só leitura |

**Como se conta.**
- **Caso** é um item distinto: o índice único de `learning_signals` faz um sinal por item, e a segunda marca não conta. A versão
  nova de um fluxo é outro `ref` e, portanto, outro caso.
- **Fechado** é o caso com 7 dias desde a marca, ou com uma regressão antes disso.
  - A regressão é a evidência real contra ou em conflito, o item desligado (pelo sistema ou por uma pessoa), ou o parecer
    recusado por uma pessoa.
  - Só contam os eventos entre a marca e a marca + 7 d. O que veio antes da marca não conta.
  - A pessoa publicar o fluxo durante a janela não é regressão: o caso segue.
- **Taxa** = limpos / (limpos + regrediram). Os abertos ficam fora, e sem nenhum fechado a taxa é `null`.
- **Libera** quando há ≥ 30 fechados e taxa ≥ 0,9. O relatório diz quantos fechados faltam e quando os abertos fecham
  (o maior `fecha_em`).

**Onde aparece no painel.**
- Hoje, em lugar nenhum: a aba Métricas (30.33-A) ignora a chave aditiva.
- Na 30.34-B, uma linha no cartão do curador: "Autopublicação do fluxo B: em sombra — N casos (A abertos, L limpos,
  R regrediram); sem regressão X % de F fechados; libera: não (faltam K)".
- A lista por caso não vai ao painel. Sai de um script só de leitura (`scripts/aprendizado-relatorio-sombra.py`, a fazer
  na 30.34-B), em JSON e numa tabela para o relatório.

**O que a 30.34-B entrega, além disso.**
- O caminho do `on` pela trava da D1: `decided_by = sistema`, com o motivo `autopublicacao_b` na trilha, para as métricas
  o separarem da D1. Feito em 04/10, entregue desligado (abaixo).
- O `on` aceito no config. Sem `libera`, ele se comporta como `shadow`. Feito.
- O script do relatório e a linha do painel: ainda não.

**O caminho do `on` (30.34-B, 04/10).** É uma saída estreita da D1, em duas camadas, cada uma com uma marca que só
ele passa.
- `conferir_transicao(..., emenda_b=True)` deixa o SISTEMA publicar o que tem efeito, mas só de `validated` para
  `published`. O texto de pessoa e o reaprendido (30.23) seguem com o dono mesmo com a marca.
- `_mover_fluxo(..., emenda_b=True)` pula só a recusa do efeito. O reaprendido, a guarda do fluxo e o CAS seguem.
- O repositório confere de novo: só fluxo, só o sistema, só `validated → published`, só com o motivo marcado.
- **Quem passa a marca:** só `LearningService.autopublicar_fluxo(ref, reason=...)`, chamado pela volta da
  autopublicação em `on` com `libera`.
  - A rota genérica (`mudar_estado`) recusa o motivo que começa com `autopublicacao_b`. O sistema por ela segue
    recebendo `ExigeODono` num fluxo com efeito.
- **O motivo na trilha diz por que:** o parecer (`review_id`, aprovar com confiança alta), as execuções e os aparelhos,
  e o balanço da sombra no momento.
  - O relatório conta essas publicações em `publicados_pela_emenda`.
  - `aprovacoes.sistema.published` das métricas continua contando toda publicação do sistema, as da emenda inclusive.
- **Em `on` a sombra segue medindo:** o caso é marcado antes de publicar.
  - Se a trava recusar no meio (veto, guarda, o item mudou), fica no log e o fluxo segue esperando o dono.
  - A publicação deixa um `warning` no log.
- **Entregue desligado:** o central segue em `shadow`, e com 0 casos fechados nem `on` publicaria. Ligar `on` é decisão
  da orquestradora, com o relatório da sombra.
- Prova `simulated`: `test_learning_autopublicacao_sombra.py`. Os testes cobrem:
  - `on` sem e com o balanço;
  - a D1 inteira fora do caminho;
  - a regra pura estreita;
  - o candidato;
  - a recusa no meio.

  A mutação foi conferida: sem passar a marca ao repositório, a publicação falha.

**O primeiro relatório (04/10).**
- Leitura real às 08:28:53Z, no central em 051fc3e0 (deploy 20), com a sombra ligada desde o deploy 11:
  - casos 0, abertos 0, limpos 0, regrediram 0, taxa `null`, libera `false`.
- Dos 48 fluxos (22 published, 20 candidate, 6 disabled), nenhum estava em `validated`: a regra não teve candidato.
- Os 5 candidatos com efeito externo (3 do QA, 2 do Instagram, estes C desde a 30.32) não tinham evidência a favor nem
  contra.
- O próximo passo, decidido pela orquestradora, é dirigir validações P4 aos 3 do QA.

**O que esperar dos números.** No ensaio de 03/10 nenhum fluxo estava em `validated` segurado pela D1. Os casos dependem
de fluxos B com efeito que cheguem a `validated` com o parecer `aprovar` de confiança alta e ≥ 2 execuções em ≥ 2
aparelhos. O alimentador realista é o QA, pelas execuções de validação do P4 (30.31). Desde a 30.32, os fluxos de
comentar, responder, mandar mensagem e seguir do Instagram são C e não entram.

## A pessoa pede a validação (30.47)

Até o 30.47, o pedido de validação nascia só do parecer `pedir_evidencia` do curador. Um fluxo candidato só ganha
evidência pela execução de prova, porque o comando casa só com fluxo `active`. Sem parecer, não havia como provar.

- `POST /api/aprendizado/fluxo/{ref}/validacao` (adendo v1.14) chama `ServicoDeValidacao.pedir_pela_pessoa`.
- **O pedido:** a regra do pedido do curador (`pedido_do_parecer`, com `pedir_evidencia` e a falta
  `reproducao_em_outro_aparelho`), com o comando e o aparelho de origem. Quem pediu vai no `review_id`
  (`pedido:<quem>`).
- **Só fluxo `candidate`.** Ficam com o dono: a classe C (a classe de agora, pelo dossiê do curador; sem dossiê, C) e o
  efeito fora do app de QA (`efeito_real`).
- **Nenhuma recusa grava pedido:** a resposta diz o motivo. O pedido vivo é conflito.
- O painel ainda não tem o botão; a rota basta para a orquestradora e o dono.
- Prova `simulated`: `backend/tests/test_learning_pedir_validacao.py`.
- Prova `real` (04/10, deploy 23): a rota devolveu 201 para `no-qa-messenger-levantar-todos-os-contat` com o pedido
  lv-5cf7389f13e4e0f0. Ao despachar, ele fechou `plano_acima_do_teto`: o `for_each` não tinha tamanho conhecido.
  Esse é o caso do 30.48 (prova por amostra). Já o `literal-para-suporte` recebe 422 `credencial` (texto com cara de
  código) e fica fora da validação por pedido.

## O item de mais de um app (30.33-C)

Um fluxo pode atravessar apps: o C1 lê no Outlook e abre o perfil no Instagram. O `app_id` dele é o app PRINCIPAL,
onde rodam as etapas sem app próprio, e não pode virar o outro app: a etapa que roda no principal mudaria de app.
`flow_required_apps` guarda todos.

A leitura por app (validação do deploy 10) usa `apps_do_item`: os apps do fluxo, na ordem do plano, mais o principal.
- **Livro:** `EntradaDoLivro.apps` só vem preenchido com mais de um app. O `app` segue o principal, e por ele se leem o
  modo do pacote, o rótulo QA/PRODUTO e o `scope_app` da revisão.
- **Visão por app, filtro `?app=` do livro e Métricas:** o item multi-app entra em cada app dele. Nas Métricas isso vale
  para o recorte, a saúde, as revisões e a economia.
- **Balde:** o fluxo sem principal resolvido continua só no `nao_resolvido` (30.2).
- **Dossiê:** ganha `item.apps` (id, pacote e principal) só com mais de um app. O item de um app só mantém o mesmo
  `dossie_hash`, e por isso a mudança não dispara revisão fora dos multi-app.
- **Validação (30.31):** o item é QA só com todos os apps de QA, e o aparelho precisa de todos eles prontos.
- **Painel:** a linha diz "Microsoft Outlook → Instagram", e cada nome leva ao app; o `title` explica o principal. No
  detalhe, a Identidade mostra os apps, e o Conteúdo não os repete.

Na mesma fatia, os polimentos das validações dos deploys 10 e 11:
- os pareceres das Métricas mostram o título do item e o nome do app, não o pacote e o id cortado;
- o orçamento mostra os dois ramos e qual manda; o rótulo "piso" lia ao contrário;
- a sombra da autopublicação (30.34) e o desfecho em 14 dias (30.35) aparecem na tela, não só na API.

## A divergência de forma (30.36)

A sombra do fluxo em prova compara o plano da execução nova com o do candidato. Desde o 30.36 ela separa CAMINHO de
FORMA:
- **caminho**: a ação da etapa (capability, ou a chave da etapa-modelo com o efeito, as guardas e o nível de entrega),
  o app, o efeito e o número de etapas; e a pós-condição da etapa de efeito, que é a prova de entrega;
- **forma**: a pós-condição de uma etapa sem efeito, e o parâmetro que nenhuma etapa usa para agir (fora do objetivo,
  da pré-condição, dos argumentos e das guardas).

Os desfechos:
- só a forma mudou → `learning_evidence.stance = 'forma'`, que não conta contra nem a favor;
- algo do caminho mudou → `against`, como antes.
- Duas formas não desligam o fluxo. O detalhe da linha leva os tipos e os NOMES, nunca o valor nem o texto da tela.

**Contra efetivo.** O `against` que tem uma `forma` da mesma origem ao lado não conta (`promocao.efetivas`, e
`linhas.contra_efetivo` nos leitores em SQL). Valem a mesma regra:
- o veredito do D1, a saúde, as Métricas e a regressão da autopublicação (30.34);
- o dossiê do curador, a seção Evidência do item e o bloco "o que a execução ensinou".

**Reclassificação** (passo da curadoria `forma_dos_fluxos`, sem IA):
- recompara o `against` dos fluxos AINDA em prova, da encarnação atual (a marca do conteúdo), cuja execução ainda é
  legível como comparação;
- se só a forma mudou, acrescenta a linha `forma` e anuncia isso na linha do tempo da execução;
- é idempotente e não apaga nada.

**O pedido da validação (30.31)** fecha pela evidência que a execução deixou:
- a favor → `feita`;
- contra → `recusada/evidencia_contra`, e o curador volta ao item;
- só forma → `recusada/divergencia_de_forma`;
- nada → `sem_evidencia`.
O passo da curadoria do pedido remotiva o `sem_evidencia` quando a evidência chega depois.

**Receita sem caminho:**
- o pedido da receita cujo `step_hash` o plano do fluxo ativo do comando não alcança, pela chave específica nem pela
  genérica (a mesma conta de `cobertura_do_fluxo`), fecha `recusada/sem_caminho`, sem execução. Vale ao nascer, ao
  despachar e no `sem_evidencia` de antes;
- o dossiê da receita ganha `item.sem_caminho` ("sugerir aposentar"), e o curador volta a ela;
- ninguém aposenta sozinho: o parecer é registro.
- Medido no P4: 5 das 11 receitas pendentes eram variantes antigas da mesma etapa.

**Limite conhecido** (para decisão do dono):
- a validação por re-execução compara com o planejador LIVRE: o fluxo candidato é inerte e não roda nela;
- o que ela mede é se o planejador refaz o mesmo plano, não se o fluxo funciona;
- por isso, com o 30.36, os candidatos tendem a `forma` e não se validam sozinhos;
- a alternativa é executar o PRÓPRIO fluxo (como se publicado) no outro aparelho e checar as pós-condições dele.
  Foi o que o 30.37 fez, para o fluxo (seção abaixo).

## A prova de fluxo (30.37)

O pedido de validação de FLUXO (`pedir_evidencia` do curador, 30.31) roda como EXECUÇÃO DE PROVA, e a evidência vem das
etapas dela. É a emenda datada de 03/10/2026 à D1 do ADR-054: validação de fluxo pelo próprio fluxo; a receita segue por
re-execução.

**A execução** (`runs.prova_fluxo_id`, migração 084):
- o plano é o do próprio fluxo com os parâmetros do comando de origem (`FlowStore.plano_em_prova`: candidate, validated
  ou active; `disabled` ou comando fora do molde não roda);
- sem planejador, sem RESOLVE, sem `runs.flow_id`, sem `flows.used`, sem `skill_hash`; a sombra da intenção não a vê, e
  a prova não ensina fluxo novo (`_learn_flow` volta cedo);
- aparece como "Prova de fluxo (validação)" (`RunSummary.prova_fluxo_id`): nunca comando de pessoa, nunca aviso, nunca o
  último comando do cartão;
- `needs_input`, `approval_required` ou incerteza é infra: o sistema encerra a execução na hora (sem
  `cancelou_execucao`, sem pergunta pendente, sem aviso) e o pedido fecha `sem_evidencia`. Desde o 30.75, a parada na
  tela de senha do app fecha `app_sem_sessao`, e o corte pelo teto, `orcamento_da_prova` (seção do 30.75).

**A evidência** (`SombraDosFluxos.minerar`, ramo da prova), UMA linha do fluxo provado, com a marca do conteúdo:
- a favor: execução `completed` e todas as etapas comprovadas;
- contra: uma etapa `failed` na própria pós-condição (a última tentativa sem `error_kind`);
- infra (erro de IA, teto, aparelho, cancelamento pelo sistema): sem evidência;
- vale também para o fluxo ATIVO (o K-086 achou que nenhuma das 13 evidências de fluxo vinha de execução que usava o
  próprio fluxo); o D1 só avalia quem ainda está em prova;
- a prova nunca é comparável na sombra (assinatura `None`). A sombra orgânica segue gravando com a `forma` do 30.36 e
  nunca conta para o pedido.

**O pedido:**
- teto por pedido `aprendizado.validacao.teto_por_pedido_usd` (US$ 0,10, `learning_validations.teto_usd`), aplicado
  pelo roteador como o teto do 28.6 (vale o menor), também nas reaberturas; o teto total do P4 segue US$ 3,09;
- comando de origem fora do molde: `sem_caminho`, ao nascer ou ao despachar; só a receita `sem_caminho` volta ao
  curador, para não pagar revisão em laço;
- reabertura: com a validação ligada, o pedido de FLUXO fechado `sem_evidencia` ou `divergencia_de_forma` numa
  execução comum ganha um pedido novo, uma vez, que roda como prova (3 esperados no central: QA conta, localizar
  contato, QA-001-2);
- o custo da prova terá coluna própria no `registro_p4.py` (fim do P4).

**Prova:** `simulated` em `backend/tests/test_learning_prova.py`. `real`: `not_run` até a 1ª validação de fluxo depois
do deploy que levar o 30.37; o P4 fica pausado (`validacao.modo: off`) até lá.

## A evidência de uso do fluxo ativo (30.51)

Até aqui, a execução comum que casava com o fluxo ativo (`runs.flow_id`) não deixava nada no livro. Era o resto do
K-086: o uso de um fluxo publicado só contava pela sombra de outras execuções. Entre 03/10 18Z e 04/10 12Z foram 16
execuções e 0 evidências. Decisão da orquestradora (04/10):

- **Origem própria.** A execução comum que usou o fluxo deixa UMA linha `uso:<run_id>` (`ORIGEM_DO_USO`), distinta
  da prova e da sombra (`run:<run_id>`). O texto troca "prova:" por "uso:". A regra é a mesma da prova
  (`veredito_da_prova`, lido em `LeituraSql._uso`), mas só `for` e `against` viram linha
  (`domain.prova.evidencia_de_uso`). A invalida e o "sem desfecho" (infra, ator que não agiu, plano revisado) não
  dizem nada do fluxo.
- **Uma vez.** A execução que já tem `for`/`against` do item por outra origem não grava o uso (`_minerar_uso`).
  Minerar de novo também não duplica, pelo índice único.
- **O que não conta contra:** o ensaio (`ensaio:`), o lote de teste (`lote:`) e a execução com cancelamento pedido
  (`runs.cancel_requested`, da pessoa ou do sistema). O que passou ainda conta a favor. Nenhum D1 roda aqui: o fluxo
  usado é o ativo.
- **O D-5 enxerga o uso, com os limiares do dono.**
  - O uso é o contador do fluxo, como a reprodução é o da receita. Entra na eficácia (`a_favor`/`contra`) e nas falhas
    seguidas do rótulo (`falhas_seguidas_no_fim(usos_do_fluxo(...))`, na ordem da gravação).
  - Não entra na contestação: uma falha de uso não é `contestado_recentemente`. Duas seguidas degradam, pelo
    `aprendizado.saude.falhas_seguidas`.
  - A janela do resultado posterior (`ResultadoPosteriorSql.usos`) lê, para o fluxo, só o uso real, sem a prova, a
    sombra e o simulado.

**Prova:** `simulated` em `backend/tests/test_learning_evidencia_de_uso.py`, com duas mutações conferidas: o
"não conta contra" e a contestação. `real`: `not_run` até o deploy.

## A evidência datada da receita (30.39)

A lacuna ("a receita não tem evidência datada") tinha um custo medido em 03/10: o curador pedia `execucao_real` em 23 de
29 pareceres de receita, com até 33 replays ok, porque a eficácia da receita vivia só nos contadores `replay_ok/replay_fail`
(sem data, aparelho, versão nem real/simulado) e a lista do dossiê vinha vazia.

- **Minerador** `EvidenciaDaReceita` (`application/evidencia_da_receita.py`, leitura em `infrastructure/reproducao_sql.py`),
  no digest de toda execução assentada. Uma linha de `learning_evidence` de `receita:<id>` por (receita, execução,
  posição), `origin_ref = reproducao:<run_id>` (origem própria, `promocao.ORIGEM_DA_REPRODUCAO`; não colide com o
  `run:<id>` de outras fontes), com `run_id`, o aparelho da primeira etapa, a versão do app da receita,
  `simulated` da execução e a data do fim da etapa. A favor: a etapa com `steps.driven_by = 'recipe'` e `succeeded`. Contra:
  `recipe+ai` ou `sem_ator`. Em ambos, a ÚLTIMA tentativa da etapa é a que carrega `attempts.recipe_id` (é ela que escreve
  `driven_by`). Etapas da mesma receita na mesma execução (`for_each`) viram uma linha, com a contagem no `detail`.
- **A espera por uma pessoa não é veredito (30.70).** A etapa que para em `waiting_user` (aviso do app, autenticação,
  conta errada, falta de informação) não conta contra a receita. No executor (`_after_step`), `waiting_user` está
  ao lado de `retry`, defeito do plano e trava da conta fora do `veredito`: não soma `replay_fail` nem
  `consecutive_fail` (quarentena) e não grava `driven_by`. Na leitura, a etapa em `waiting_user`, `cancelled` ou
  `skipped` fica de fora, o que cobre as gravadas antes (cancelar e pular só pegam etapa aberta). A etapa retomada que terminar dá o veredito no digest seguinte. A divergência
  vista na SOMBRA continua contando, como na nova tentativa: segura a promoção.
  - Exceção conhecida (D1-N1 da leitura do #374): em 04/10, entre o 31.36 e o 31.40 b, a etapa opcional rodou
    com receita por algumas horas. Uma falha ali gravou `recipe+ai` e depois `skipped`, e o filtro a esconde da
    retrocarga. Fica registrado; não vale código.
- **A espera pela pessoa no digest (30.69, sobre o 29.93).** A execução em `awaiting_person` não assenta nem é
  digerida enquanto espera; o assentamento na saída é do 29.93. A exposição da etapa em `waiting_user` não fecha
  (`_ETAPA_FINAL` sem ele): a curadoria escolhe as execuções pelo `finished_at` e congelaria o desfecho da espera.
  No relatório de condução, a etapa que espera sem `driven_by` aparece como `esperando_pessoa`, não como "sem
  condução", e fica fora da porcentagem por receita. O digest re-rodado na saída não duplica linha em nenhum
  dos 10 mineradores (nota de desenho do 30.69).
  - Desde o 30.71, a curadoria (`execucoes_a_preencher`) também deixa de ESCOLHER a execução `awaiting_person`. Antes
    ela entrava em toda passada sem preencher nada e, com o `LIMIT 200` por `run_id`, podia tirar a vez de quem fecha.
  - A janela da curadoria (`PREENCHER_DIAS`) conta da SAÍDA da espera: `COALESCE(assentada_em, finished_at)`. O
    vencimento e o cancelamento não limpam o `finished_at`, que guarda a hora da entrada; uma espera mais longa que a
    janela, com o digest da saída perdido, deixaria a exposição de fora para sempre (N1 da leitura do 30.71).
  - Nota (N2 da mesma leitura): a exposição de uma etapa que já tinha concluído ANTES da espera agora só é preenchida
    na saída da execução, porque a curadoria não escolhe a execução que espera. Atrasa, não perde.
  - No estoque migrado (a 111 levou as `completed_with_issues` com objetivo esperando para `awaiting_person`), as
    exposições que o primeiro digest congelou com `waiting_user`, antes do 30.69, ficam assim: nada as reabre.
  - O relatório de condução, que é recalculado a cada leitura, passa a mostrar `esperando_pessoa` também nas antigas.
- **Não decide nada.** Só grava evidência; o D1, a quarentena e os contadores seguem donos do estado da receita.
- **Retrocarga** `RetrocargaDaReceita`, passo da curadoria (e não função única na montagem): a cada volta completa as
  reproduções de execuções já terminadas que ainda não têm linha (as de antes do 30.39, um digest que falhou), datadas
  pela etapa e das mais novas para as mais antigas. Idempotente pela chave única. Respeita `retencao.evidencias_por_item`:
  não enche uma receita além dele, senão a purga e o passo se desfariam um ao outro. Só existe o que as tentativas
  guardam hoje.
- **Dossiê da receita** (só `kind == "receita"`): `evidencias.contadores_e` (`replay_*` são acumulados sem data, incluem a
  lista; a citável é a `lista`) e, só com `conteudo.sombra.shadow_total == 0`, `evidencias.sombra_e` (a sombra corre só com a
  receita candidata ou com `ai.recipes: shadow`; em `replay` a ativa não volta à sombra, então `sombra` não é falta que a
  validação produza). `VERSAO_DO_DOSSIE` fica em 1 (como no 30.36): as chaves só existem na receita, e o `dossie_hash` da
  receita muda sozinho com a lista nova, o que devolve as receitas ao curador.
- **Onde a regra por etapa e o contador divergem.** O contador soma por tentativa com veredito; a linha vê a etapa pela
  última tentativa. O `for_each` soma N no contador e 1 na linha. Uma tentativa que consultou a receita e fechou por atalho
  sem agir nem divergir é `sem_ator` com `recipe_id`: contra na linha, fora de `replay_fail`. A evidência é a verdade datada;
  o contador, o histórico.
- **Não conta de novo (a saúde não muda).** A linha de reprodução é o registro datado do que `replay_ok/replay_fail` já
  somam. Contá-la seria contar duas vezes: um `against` dentro dos `contestacao_dias` levava a receita publicada a
  `degradando` (`CONTESTADO_RECENTEMENTE`), medido no teste antes da correção. Por isso `promocao.efetivas` (por onde passa
  todo leitor que conta: `contrarias`, a saúde, a fila "Revisar", `veredito_de_repeticao`) tira a origem `reproducao:`, e os
  leitores em SQL fazem o mesmo (`linhas.contra_efetivo` e `linhas.fora_da_reproducao` em `metricas_sql` e
  `aprendido_sql`). Só o dossiê a mostra (ele não passa por `efetivas`): é para o curador citar a execução real.
  `test_o_contra_da_reproducao_dentro_da_janela_nao_contesta_a_receita` guarda isso (falha sem o filtro).
## O teto do legado e a receita sem caminho (30.40)

Duas correções do que o P4 mostrou no central em 03/10, sem migração e sem mudar o dossiê:

- **O teto do pedido legado.** O pedido sem `teto_usd` (nascido antes do 30.37) herda
  `aprendizado.validacao.teto_por_pedido_usd` ao despachar, no mesmo UPDATE que liga a execução
  (`RegistroDeValidacoes.comecar(..., teto_usd=)`, com `COALESCE`). O roteador só acha o pedido pelo `run_id`, então a
  execução nunca fica ligada sem teto, e o legado não depende de UPDATE à mão (em 03/10 20:56Z foi preciso um, nos 16
  pendentes, autorizado pela orquestradora). O teto já gravado não muda: a config nova vale para o pedido que nasce e
  para o que não tinha teto.
- **A receita sem caminho.** Com a marca "variante sem caminho" (30.36) no dossiê, 5 das 6 receitas marcadas pediram
  evidência de novo na revisão de 20:51Z; só a `receita:63` sugeriu `possivelmente_obsoleto`. Agora `pedir_evidencia`
  sai das opções do item (`decisoes_do_item`, dentro de `opcoes_do_dossie`), e o esquema estrito do hub (Anthropic e
  OpenAI) só aceita as decisões oferecidas. O parecer que a escolher assim mesmo é inválido
  (`invalida:decisao_indevida`): fica o registro, nenhum pedido nasce, e o item só volta ao curador com dossiê novo.
  O `dossie_hash` não muda, então esta mudança sozinha não provoca revisão nova.

**Prova:** `simulated` em `backend/tests/test_learning_prova_validacao.py` (o legado herda o teto, o gravado não muda) e
`backend/tests/test_learning_curador_dominio.py` (as opções, o esquema do hub e o parecer inválido). `real`: `not_run`
até a primeira revisão de receita sem caminho depois do deploy que o levar. O esperado é nenhum `pedir_evidencia` e
nenhum pedido `recusada/sem_caminho` novo para ela.
## A prova parte de um estado conhecido (30.42)

O que o P4 mostrou no central em 03/10: a `5f2de5` mandou a mensagem duas vezes e virou evidência A FAVOR (ev:48); a
`e1b7d0` herdou a tela da execução anterior (o app dentro de uma conversa) e virou CONTRA; o `_prova` misturava as
etapas `failed` da v1 com as `succeeded` da v2 (ev:49). Desenho aprovado pela orquestradora em 03/10 22:01Z, sem
migração. Adendo de contrato v1.07.

**A execução de prova** (`Scheduler`, só com `runs.prova_fluxo_id`):
- **ponto de partida** (`_partir_da_prova`): antes da 1ª etapa, `force-stop` de TODOS os apps do plano do fluxo e
  abertura do app da 1ª etapa. Uma vez por execução, e nunca na retomada (já houve tentativa). Nunca `pm clear`: o
  rascunho que sobrevive ao force-stop é resultado da prova real. Falha aqui não derruba a prova; a decisão na linha do
  tempo diz o que aconteceu, e a etapa de abertura comprova (ou reprova) o ponto de partida;
- **não replaneja** (`_try_recover`): um plano novo não é mais o fluxo. O fechamento diz por quê ("a prova não
  replaneja").

**O veredito** (`domain/prova.veredito_da_prova`, a regra única; quem lê o banco é `LeituraSql._prova`), nesta ordem:
1. efeito que saiu mais de uma vez → `invalida`, motivo `efeito_repetido` (vale mesmo com a execução completa);
2. alguma etapa de plano acima da v1 → sem evidência. A versão que só expande o `for_each` (`plan_versions.reason`
   começando por `PREFIXO_DA_EXPANSAO`, "Expandido para ") não é replanejamento: as cópias por item SÃO o plano do
   fluxo, a etapa que a expansão pulou fica fora, e a ordem é (versão, seq), porque a seq recomeça em cada versão;
3. etapa reprovada com `error_kind` (infra) ou sem tentativa → sem evidência;
4. a etapa reprovada é a de abertura → `invalida`, `ponto_de_partida`;
5. a última tentativa da etapa reprovada não agiu (só leitura, `step_done` ou `step_blocked`) → `invalida`,
   `ator_sem_acao`;
6. CONTRA só quando a etapa agiu e a pós-condição do próprio fluxo não veio;
7. A FAVOR com a execução `completed` e todas as etapas comprovadas.

**O efeito repetido** fica atrás de UMA função (`domain/prova.efeito_repetido`), para trocar a fonte sem mexer no
resto. Vence o `steps.result.efeito_repetido` do 29.58 (contrato com a Android: chave AUSENTE sem repetição;
`{"copias": int >= 2, "fonte": "verificador" | "acoes"}` na etapa onde foi vista; outra forma é ignorada). Sem ele, a
regra própria, sobre o diário de TODAS as tentativas: mais de uma ação de efeito concluída (`tap`, `long_press`,
`drag`, `type_text` com `args.is_commit_action` ou, no toque, alvo com cara de envio por `COMMIT_VOCAB`) numa etapa com
efeito externo, ou uma numa etapa sem efeito.

**A posição `invalida`** (`Posicao.INVALIDA`): uma linha de `learning_evidence` com a MESMA origem (`run:<id>`) que a
prova teria, e `detail = [marca] invalida:<motivo> — texto` (`detalhe_da_invalida`; o motivo é de vocabulário
fechado, `MotivoDaInvalida`). Não conta a favor nem contra. Ao lado de um `for` ou `against` da mesma origem, tira essa
linha das contagens, como a `forma` faz com o `against`: `promocao.efetivas`, `linhas.contra_efetivo` e o novo
`linhas.favor_efetivo`. Não confundir com a "evidência inválida" do 30.23 (motivo `evidencia_invalida:<run>` na
trilha, que desliga o item); nem com o `invalida:decisao_indevida` da validade de um parecer (30.40), que é outra
coluna.
- Leitores que tratam a `invalida` de propósito: `promocao` (`efetivas`, `contrarias`, `veredito_de_repeticao`),
  `aprendido` (rótulo "inválida (<motivo>; não conta)"), `curador` (nota `evidencias.invalida_e` do dossiê, só quando há
  uma), `servico` (o `a_favor` do fluxo), `metricas_sql`, `aprendido_sql`, `dossies` (a lista mostra a `invalida` e
  tira a linha corrigida), `ligar_nativos.contra_de_fluxos` (o `against` corrigido não é recomparado pela forma) e o
  painel (`model.motivoDaInvalida`, `DetalheRico`).
- Já contavam só `against`/`conflict` como contra e não mudam: `curador_simulado`, `planning/curador.py`,
  `metricas.py`. Não recebem `invalida`: `ligar_telas`, `reproducao_sql` e os contadores de `li-` em `sql_repository`.
- **Fora daqui:** `planning/decisao_fechada/curador.py:160` (Jev, 31.23) soma como contra toda posição diferente de
  `for`, e já soma a `forma`. Com a `invalida`, o efeito é só na sombra do Jev; a correção é o 31.25, da Jev.
- **Rollback:** o código anterior faz `Posicao(stance)` (`sql_repository`) e quebra ao ler uma linha `invalida`.
  Voltar o código exige apagar antes as linhas `stance = 'invalida'`.
- **Painel:** `MIN(detail)` dá o motivo do grupo em `aprendido_sql`; um item com `invalida` de dois motivos mostra um
  só.

**O passo `ReclassificacaoDoEfeitoDuplicado`** (curadoria, `nativos.py`): recompara com a mesma `efeito_repetido` os
`for` de execuções de prova que ainda não têm `invalida` irmã, e grava a irmã quando o efeito se repetiu. É o que
corrige a ev:48. A marca vem do próprio `for`. Sem UPDATE, sem transição de estado, idempotente pelo índice único,
nunca chama IA.

**O pedido de validação** (`application/validacao.py`, `validacoes_sql.py`, regras puras em `domain/validacao.py`):
- a `invalida` da execução VENCE no fechamento (antes do `for`): o pedido fecha `recusada` com o motivo dela
  (`efeito_repetido`, `ponto_de_partida`, `ator_sem_acao`). Não devolve o item ao curador nem reabre. Um pedido já
  fechado `sem_evidencia` cuja execução ganha depois a `invalida` passa ao motivo dela (o mesmo molde do 30.36);
- **limite de provas:** no máximo 4 provas por item e versão do conteúdo em 7 dias (era 2 até o 29.75; `MAXIMO_DE_PROVAS`,
  `JANELA_DE_PROVAS_DIAS`). A 3ª fecha `recusada/limite_de_provas` ao despachar, sem gastar. A versão é a marca
  `[xxxxxxxxxxxx]` (`content_hash` do plano); prova anterior com outra marca não conta, e prova sem marca conta (lado
  seguro). É aproximado, sem migração;
- **aparelho novo:** com `reproducao_em_outro_aparelho` na falta, o excluído é o CONJUNTO da origem e dos aparelhos das
  provas anteriores do item. Nenhum aparelho que sirva fora desse conjunto (mesmo desligado) → `recusada/sem_aparelho_novo`
  sem gastar; sobra, mas ocupado → espera. Nenhum aparelho que sirva em absoluto → espera, como antes (fechar quebrava
  o pedido de mais de um app).

**Prova:** `simulated` em `backend/tests/test_learning_prova_veredito.py`,
`test_learning_reclassificacao_efeito.py`, `test_learning_prova_ponto_de_partida.py`,
`test_learning_prova_limites.py`, `test_learning_prova.py` e o contrato do dossiê com `invalida` em
`test_learning_curador_dominio.py`; painel em `frontend/src/features/aprendizado/{model,DetalheRico}.test.*`. `real`:
`not_run` até o deploy que o levar. O esperado: a ev:48 ganha a `invalida` irmã na primeira volta da curadoria, e a
próxima prova do P4 mostra a decisão "ponto de partida" na linha do tempo. Sem rodada paga extra: vem do despacho
normal do P4.

## O teto da prova proporcional ao plano (30.41)

No P4 de 03/10, a prova do fluxo com `for_each` sobre 8 contatos gastou US$ 0,157 contra o teto fixo de 0,15. Ela
enviou 3 mensagens e fechou `sem_evidencia`; inteira, custaria de 0,33 a 0,36. Decisão da orquestradora (03/10 21:44Z),
sem migração:

- **O teto da prova de fluxo** é `min(0,80; 0,05 + 0,02 × etapas)` (máximo de 0,40 até o 29.75) (`domain/validacao.teto_da_prova`,
  `TETO_DA_PROVA_*`). Ele vai ao pedido no despacho, no mesmo UPDATE que liga a execução (`comecar(...,
  teto_da_prova=)`), e VENCE o teto gravado: o fixo de quando o pedido nasceu, ou o 0,15 gravado à mão em 03/10 20:56Z.
- **O plano acima do máximo não despacha.** Com 18 etapas ou mais, ou de tamanho desconhecido, o pedido fecha
  `recusada/plano_acima_do_teto` ao despachar. Não há execução, gasto nem envio pela metade.
- **As etapas** (`FontesDaValidacao.etapas_da_execucao`) são as fixas mais as etapas-modelo do `for_each` vezes o tamanho
  da lista que a execução de ORIGEM do fluxo coletou (`objectives.collected`, `flows.source_run_id`). O número é
  aproximado: a próxima lista pode ter outro tamanho. O roteador segue barrando a chamada além do teto.
  - Sem a lista da origem (execução purgada), o tamanho não se sabe, e o pedido não despacha (o lado seguro).
  - O 8 contatos dá 3 + 4 × 8 = 35 etapas, teto 0,75: fecha. O QA-001 (6 etapas) leva 0,17, e o "bom dia" (11 etapas)
    leva 0,27.
- **A receita** segue com o teto fixo do 30.40: o legado sem teto herda o da config, e o teto gravado não muda.
  - Ela fecha `plano_acima_do_teto` só quando o comando resolve num fluxo ATIVO acima do máximo (`plano_ativo_para`,
    o `FlowStore.match`).
  - Sem fluxo ativo, quem responde é o `sem_fluxo_ativo` ou o `sem_caminho`.
- **O fôlego do despacho** (`pode_despachar`) segue com o custo estimado fixo (`custo_estimado_usd`, 0,07). O teto total
  do P4 não muda.

**Prova:** `simulated` em `backend/tests/test_learning_prova_teto.py` e `test_learning_prova_validacao.py`. `real`:
`not_run` até o deploy que o levar. O esperado é que a próxima prova do P4 grave em `learning_validations.teto_usd` o
proporcional ao plano dela.

## O dossiê pela marca do conteúdo e a recusa pela pessoa (30.52)

Em 04/10, o parecer lr-1cfb91a981c5f21f (fluxo `enviar-a-mensagem-leitura-31-27-n1-08233`, reaprendido às 10:34Z) citou os
`against` 226 e 233 da marca antiga `[072ae4d8443c]` como se fossem do conteúdo de agora. Concluiu "a favor e contra no
mesmo aparelho; falta reprodução em outro aparelho", mas a reprodução já existia: a ev 274, no android-07, com a marca
`[2c10657f49e4]`. A regra da sombra (30.34) filtra pela marca, e o dossiê não filtrava. O pedido que esse parecer abriu
(lv-26679df914e1b809) gastou mais uma prova, porque não havia como recusá-lo.

- **O dossiê do fluxo separa as versões.** A `lista` das evidências traz só as linhas com a marca do conteúdo ATUAL
  (`marca_do_conteudo(e.content_hash)`) e o legado sem marca. As linhas com a marca de outro conteúdo vão em
  `de_versoes_anteriores`, com a explicação `versoes_anteriores_e` (`OUTRA_VERSAO_DA_EVIDENCIA`). Seguem citáveis e no
  `total`, mas não se misturam à versão de agora. Sem versão anterior, nem a chave aparece. A receita e os outros
  tipos não mudam.
- **A pessoa recusa um pedido pendente:** `POST /api/aprendizado/validacoes/{id}/recusar`, sem DDL.
  - Fecha `recusada` com o motivo `recusada_pela_pessoa`, sem execução nem gasto.
  - O motivo não é chegada para o curador, nem evidência, nem contestação: não pesa contra o item.
  - Respostas: 404 quando o pedido não existe; 409 quando ele já saiu de `pendente`.
  - O `listar` ganhou o filtro `pedido` (um só).

**Prova:** `simulated` em `backend/tests/test_learning_dossie_pela_marca.py` (3 testes; mutação conferida no separador
das versões). `real`: `not_run` até o deploy.

## O parecer da classe B não pede o que nenhuma prova produz (30.73)

A medida do 30.72 (05/10, `.claude/handoffs/aprendizado-medida-30-72.md`) achou isto: nenhum dos 22 fluxos B tinha
caminho para `aprovar`. Cinco deles já cumpriam os números da autopublicação (≥ 2 execuções reais em ≥ 2 aparelhos,
nenhuma evidência contra) e paravam em `observar`, porque o parecer pedia `voto_da_pessoa` e `decisao_da_pessoa` por
"efeito sem catálogo". Na classe B, o efeito em app sem catálogo é a própria definição da classe
(`politica_de_risco`), e nenhuma execução produz essas duas faltas.

- **Dossiê:** o item B leva o fato `risco.classe_b_e` (`CLASSE_B_DO_ITEM`).
  - Diz que "sem entrada no catálogo" define a classe e não é lacuna, que o parecer julga pelas evidências citáveis,
    que publicar um fluxo B segue a regra da autopublicação e que o aceite continua sendo da pessoa.
  - O dossiê A e o C não mudam.
  - A `VERSAO_DO_DOSSIE` continua 1 (N1 da leitura, decidido): é uma chave condicional, como `sem_caminho` e as do
    30.36.
    - Subir para 2 mudaria o hash de TODO dossiê, A e C inclusive, e reabriria a revisão de todo o livro sem
      nada novo a dizer neles.
    - O parecer B feito com a instrução nova se reconhece pelo dossiê gravado em `learning_reviews.dossie`, que tem
      `risco.classe_b_e`. A `template_versao` da revisão é a forma do dossiê (`dossie-v1`), não a versão do texto.
    - **30.76 (migração 117):** o parecer grava a versão do texto em `learning_reviews.instrucao_versao`. Quem diz é o
      adaptador que mandou o texto à IA (`CuradorDoHub`, a `VERSAO_DO_TEMPLATE`, hoje `curador-v2`, também quando o
      provedor atrás do hub é simulado, que a linha marca em `simulated`). Ficam NULL as linhas antigas, as recusas
      (custo, triagem), o curador simulado da porta e o rótulo de intenção. A coluna não entra no hash nem na
      elegibilidade.
  - O hash do dossiê B muda, e cada item B fica elegível para UMA revisão nova depois do cooldown, dentro da fatia do
    curador.
- **Opções fechadas:** `faltas_do_item(classe)` tira `voto_da_pessoa` e `decisao_da_pessoa` das opções de `falta` do
  item B (`FALTA_SO_DA_PESSOA`).
  - O esquema estrito que vai ao provedor leva esse enum.
  - Se um provedor sem esquema estrito as devolver mesmo assim, o `validar_saida` as tira do parecer, que segue
    válido. O descarte não some: elas vão a `Parecer.falta_descartada`, à `saida` gravada como `falta_descartada`
    (só quando houve, então a `saida` dos outros pareceres não muda) e a uma linha de log com o id da revisão e os
    rótulos. Pedido da Jev: não esconder que o modelo insistiu.
  - Na A e na C, as opções são todas, como antes.
- **Instrução do hub:** a frase da B no `CURADOR_SYSTEM` (`backend/app/planning/curador.py`, hub da Jev), na redação
  dela (05/10 08:45Z). Ela nomeia os rótulos (`voto_da_pessoa`, `decisao_da_pessoa`) em vez de "não peça decisão da
  pessoa", que contradiria o "quem aceita é a pessoa" do mesmo texto, e termina com "O aceite continua sendo da
  pessoa". `VERSAO_DO_TEMPLATE` passa a `curador-v2`, e o hash do texto fica preso num teste.
- **Estoque:** o parecer gravado com `voto_da_pessoa` se lê como foi gravado (`parecer_gravado` não filtra).
- **A autopublicação continua em `shadow`.** Ligar o `on` não faz parte deste item.

**Prova:** `simulated` em `backend/tests/test_curador_classe_b.py`. `real`: `not_run` até o deploy e a primeira
revisão B de `curador-v2`.

## A versão do app na evidência do fluxo (30.74)

A medida do 30.72 (05/10) achou as 113 evidências reais de fluxo do central com `app_version` nulo. As de receita levam
a versão (`recipes.app_version`). Por isso o parecer do curador dizia "sem versão do app registrada" e pedia
`reproducao_na_versao_viva`, que nenhuma prova satisfazia.

- **Quem grava:** o digest, nos três caminhos que gravam evidência de fluxo: a sombra (`run:`), a prova (30.37) e o uso
  (30.51, `uso:`).
- **De onde vem a versão:** do app do fluxo (o principal: `flows.app_id`, depois `apps.package`), lida em
  `device_app_state` do aparelho da execução (`LeituraSql.versao_do_fluxo_no_aparelho`).
- **Sem leitura do aparelho, ou com a versão vazia:** fica nula, como antes.
- **Só a versão que valia NA execução (V1 da leitura).** A leitura é a da hora do digest, não a da execução. Se o app
  pode ter sido atualizado depois do início dela (um digest atrasado, a primeira passada depois de um deploy), a
  evidência velha levaria a versão nova, uma prova falsa de "versão viva". Por isso a versão só vale quando a última
  atualização do app no aparelho (`device_app_state.last_update_time`) é COM CERTEZA anterior ao início da execução
  (`runs.started_at`); senão, nula.
  - O `last_update_time` vem do `dumpsys` no fuso DO APARELHO, sem fuso escrito. Sem o fuso, a conta usa o pior caso
    (a hora lida + 12 h, `versao_estavel_na_execucao`), e a execução que começa até ~12 h depois de uma atualização do
    app fica sem versão.
  - **30.77 (migração 118): o fuso vai na inspeção.** O `package_info` lê `date +%z` no mesmo shell do `dumpsys` e o
    guarda em `device_app_state.last_update_offset` (`-0300`). Com ele, a hora em UTC é a lida menos o deslocamento, com
    1 h de margem para horário de verão e relógio do aparelho. Sem ele (linha antiga, leitura sem a linha do fuso, ou
    fuso ilegível ou fora de −12..+14 h), segue a folga de 12 h.
    - Medida que decidiu (05/10, SQLite central só leitura e `getprop` pelo adb): os 12 emuladores estão em
      America/Sao_Paulo (−0300). Das 40 evidências de fluxo do dia, as 36 sem versão eram de antes do deploy do 30.74
      (6 sem leitura do aparelho), e as 4 depois dele tinham versão. A folga perdia à toa a prova de 3 a 12 h depois de
      cada atualização, que no parque são poucas por semana.
    - O adb dos aparelhos do notebook roda no central, pelo túnel: o agente do notebook não precisa de versão nova.
  - Sem `last_update_time`, sem `started_at`, ou com uma hora ilegível: nula.
- **As reclassificações ficam sem versão (N2 da leitura, decidido):** as linhas `forma` (30.36), `invalida` por efeito
  repetido (30.42) e `revalidada` (30.53) são gravadas por cima de uma evidência de execução e não levam `app_version`.
  - O certo, se um dia precisar, é COPIAR a versão da evidência original, nunca ler de novo.
  - Não entra agora porque nenhuma delas é reprodução: não satisfazem `reproducao_na_versao_viva`, e o dossiê as casa
    com a original pela origem e pela marca do conteúdo, não pela versão.
- **O estoque não é refeito:** as evidências antigas seguem nulas.

**Prova:** `simulated` em `backend/tests/test_versao_na_evidencia_do_fluxo.py`. `real`: `not_run` até o deploy e a
primeira evidência de fluxo depois dele.

## A prova de fluxo por amostra (30.48)

O `for_each` de tamanho desconhecido nunca cabia no teto do 30.41 e o fluxo nunca se validava por pedido: o
lv-5cf7389f13e4e0f0 (30.47, 04/10) fechou `plano_acima_do_teto`. Agora a PROVA de fluxo roda uma amostra:

- **N** é o maior número de itens que cabe no máximo da prova (`maximo_de_etapas_da_prova()`, 37 etapas desde o 29.75), entre
  `AMOSTRA_MINIMA` (2) e `AMOSTRA_MAXIMA` (3): `tamanho_da_amostra(fixas, por_item)` em `domain/validacao.py`. Sem
  laço, ou sem caber nem 2 itens, é `None` e o pedido fecha `plano_acima_do_teto` como antes.
- **Os N primeiros, na ordem da tela.** Não há sorteio: o `Scheduler._expand_for_each`, só quando a execução tem
  `prova_fluxo_id`, corta a lista lida em N e grava no motivo da versão do plano o rastro (`rastro_da_amostra`):
  "amostra de N de M itens" ou "prova inteira, M de M itens". Os itens de fora nem viram etapa. A execução comum segue
  com a lista inteira.
- **A estimativa** do teto (`FontesDaValidacao.etapas_da_execucao`) usa a amostra só na prova de FLUXO:
  `fixas + Σ modelos × min(N, tamanho da origem)`. O tamanho desconhecido deixa de barrar. A receita não muda.
- **O rótulo.** A evidência da prova com amostra termina em "provado em amostra de N (de M itens)"
  (`ligar_nativos._prova`, lido do rastro). Ela conta como qualquer evidência a favor. O dossiê do curador leva
  `amostra: "N de M"` no item e a explicação em `amostra_e`, só quando houve amostra.

**Prova:** `simulated` em `backend/tests/test_learning_prova_amostra.py` (Harness com 5 contatos: a prova envia 3, a
execução comum 5) e `test_learning_prova_teto.py` (os casos do 30.41 reescritos). `real`: `not_run` até o deploy; o
esperado é o lv-5cf7389f13e4e0f0 de novo despachar e provar 3 de N contatos.

## A conferência do QA por item e a revalidada (30.53)

A prova real do 30.48 foi a r-20261004132450-38f68a, no android-10: amostra de 2 de 8, 13/13 etapas comprovadas,
US$ 0,13. Ela fechou `efeito_repetido` porque a conferência do QA (#203) contava as mensagens da execução no app
inteiro e esperava uma. As duas mensagens legítimas, uma por contato, viraram "o efeito saiu 2 vezes", e quatro linhas
`invalida` foram gravadas: a do fluxo e as das receitas 64, 68 e 84.

- **A conferência espera uma mensagem por etapa de efeito comprovada no app de QA.** São as etapas da versão final do
  plano, uma por item do `for_each` (`oraculo_qa.leitura_da_conferencia`). Só passar delas é repetição: o fato guarda
  `copias` e `esperadas`. O texto na execução diz "N de N esperadas".
- **`revalidada`, posição nova, sem DDL.** Ela só NEUTRALIZA a `invalida` da mesma (item, origem):
  - em `promocao.efetivas`, nos leitores SQL (`linhas.favor_efetivo`/`contra_efetivo`), no dossiê do curador
    (`revalidada_e`) e no painel ("desfeita pela revalidada");
  - não conta a favor nem contra, e nenhum `for` nasce dela. A favor só nasce de prova que fechou certo: a repetição da
    prova com o 30.53 no ar dá esse `for`.
- **O passo `revalidacao_da_conferencia`** (`RevalidacaoDaConferencia`) revalida a `invalida:efeito_repetido` só quando
  todo fato de repetição da execução é da conferência antiga (`fonte: provedor`, sem `esperadas`), e as cópias não
  passam das etapas de efeito comprovadas (`domain.prova.conferencia_revalida`).
  - Vale para o fluxo e para a receita da mesma execução.
  - O `steps.result` fica intacto; a correção fica na linha e numa decisão na execução.
  - É idempotente.
  - O fato da regra nova, o do verificador e o do diário nunca se revalidam aqui.

**Prova:**
- `simulated`: `backend/tests/test_learning_conferencia_por_item.py` (com contraprova), os testes do oráculo em
  `test_learning_ensaio_e_oraculo.py`, e `frontend/src/features/aprendizado/DetalheRico.test.tsx`. Duas mutações
  conferidas.
- `real`: `not_run` até o deploy. O esperado é que o passo revalide as 4 linhas da 38f68a e que a próxima prova do
  fluxo com `for_each` feche `for`.

## A validação com rosto no item (30.43)

Os achados 5 e 6 da validação do deploy 14 e o P4 de 03/10 (o 6f459c). Desenho aprovado pela orquestradora às 23:32Z,
com um ajuste: a origem da execução e o selo já vêm do 30.38 (`contracts/origem.py`, `RunSummary.origem`). Sem
migração. Adendo de contrato v1.10.

**A execução de validação** é a prova de fluxo ou a re-execução da validação do QA:
`contracts.origem.eh_execucao_de_validacao`, pela mesma derivação de `origem_da_execucao` e do `PREFIXO_VALIDACAO`.

**O ponto de partida** do 30.42 (`Scheduler._partir_da_prova`) vale para toda execução de validação.
- No 6f459c, a re-execução da receita:82 abriu o app dentro da conversa; a IA enviou já na `open_app`, e a receita
  enviou de novo.
- A decisão na linha do tempo diz "Validação do QA (re-execução): ponto de partida".

**A reprodução que repetiu o efeito não vale** (`InvalidaDaReproducao`, minerador e passo da curadoria):
- a linha `reproducao:<run>` da receita ganha a irmã `invalida`, com o motivo `efeito_repetido`;
- a regra é a do 30.42 (`domain.prova.efeito_repetido`): o 29.58 vence; a regra própria, sobre o diário, só vale na
  execução de validação (`regra_propria=False` na orgânica, onde a IA livre deixa mais ruído);
- o pedido de validação da receita fecha `recusada/efeito_repetido` pela `invalida`, no mesmo ramo da prova de
  fluxo, e não `feita`;
- os contadores `replay_ok/replay_fail` não mudam: a receita conduziu a etapa dela, e a linha `reproducao:` já fica
  fora das contagens. A `invalida` serve ao dossiê e ao fechamento;
- o passo reclassifica POR REGRA, e não por lista, o que já foi gravado: o 6f459c (receita:82) e o que o P4 produzir
  até o deploy. É idempotente e sem UPDATE. O pedido já `feita` não reabre;
- a execução de validação sem repetição volta a ser conferida a cada passo; são poucas (as do P4 e as do 29.58).

**O rosto no item:**
- **Receita nascida em validação** (`EntradaDoLivro.nasceu_em`: `prova_fluxo` | `validacao_qa`):
  - lida por `recipes.learned_from_step` → `steps.run_id` → `runs`;
  - a API do livro a expõe; o painel mostra "Nasceu na prova de um fluxo" ou "Nasceu numa re-execução da validação
    do QA", com o link da execução;
  - o dossiê leva `item.nasceu_em_validacao` só nela, e o `dossie_hash` das outras não muda;
  - continua na fila do dono (decisão da orquestradora). O achado era "sem dizer de onde veio".
- **Validações do item**: `GET /api/aprendizado/validacoes?item=<kind>:<ref>`, um filtro na rota do 30.38 (b), sem
  rota nova. A seção "Validações" do detalhe diferencia o motivo pelo `run_id`:
  - "Rodou; depois: sem caminho" é o lv-2dd29, que o passo do 30.36 reclassificou depois de rodar;
  - "Não rodou: sem caminho" é o pedido recusado ao despachar, sem gasto.
- **O veredito no Resumo da execução**: `?run=<id>` na mesma rota. A execução de validação mostra "Veredito da
  validação: a favor / contra / inválida (motivo) / só a forma / sem evidência" no lugar de "sucesso comprovado". A
  e1b7d0 mostrava sucesso enquanto o fluxo levou contra: o veredito é do ITEM, não da execução.

**Prova:** `simulated` em `backend/tests/test_learning_reproducao_repetida.py`, `test_learning_nasceu_em_validacao.py`,
`test_learning_prova_ponto_de_partida.py` (a re-execução da validação) e `test_validacoes_listagem.py` (os filtros), e o
painel no vitest. `real`: `not_run` até o deploy. Esperado: a primeira volta da curadoria grava a `invalida` irmã da
linha `reproducao:` do 6f459c; a próxima re-execução do P4 mostra o ponto de partida na linha do tempo; a receita:135
mostra a origem.

## Polimentos da validação do deploy 15 (30.44)

- **A evidência do item na ordem do acontecido:** o detalhe ordena por `observed_at`. A retrocarga do 30.39 grava
  depois linhas de antes (a receita:87). O dossiê já ordenava assim.
- **O título da etapa ao lado da chave:** "etapa N (chave)" fica no `detail` gravado, porque o título é texto do
  planejador e pode ter nome de pessoa. O detalhe lê o título de `steps` na hora (`FontesDoLivro.titulos_das_etapas`,
  com a triagem de credencial) e o painel o mostra ao lado.
- **O motivo legível da eficácia:** "3 de 5 deram certo (60%), abaixo de 80%" (a receita:82). A regra D-5 do dono não
  muda.
- **O ritmo por hora do P4** conta pela hora em que a execução nasceu (`runs.created_at`), com 1 min de folga. Contava
  por `updated_at`, que o fechamento move: medido no P4 de 03/10, eram 4 execuções a cada 70 min. Conferir a cadência
  no central depois do deploy.

**Prova:** `simulated` em `test_learning_ritmo_p4.py`, `test_learning_titulo_da_etapa.py`,
`test_learning_evidencia_receita.py` (a ordem) e no vitest do painel. `real`: `not_run`.

## O veredito enxerga a evidência reclassificada (30.45)

Achado do navegador do 30.43 (04/10). O pedido da 5f2de5 fechou `feita` às 20:51Z. Às 00:14Z, o passo do 30.42 gravou a
irmã `invalida` da mesma execução (ev 178), e o Resumo da execução seguia dizendo "Veredito da validação: a favor".

O pedido não muda: o estado gravado é história, e o log só cresce. O que muda é a leitura:
- `listar` devolve a `invalida` do par (item, execução) como `invalida_depois`, lendo a mais antiga, como
  `invalida_da_execucao`;
- o veredito diz "inválida (…)";
- o histórico do item diz "Rodou; depois: inválida — …", ao lado do selo "Feita".

A `invalida` de outra execução do mesmo item não conta. A linha sem motivo legível lê `sem_evidencia`, o lado seguro.

No mesmo PR, da validação do deploy 17: com o título da etapa, a evidência mostra "etapa N — Título". A chave em
snake_case sai do texto e fica no `title` da linha (o `detail` cru), como no título da receita.

Prova:
- `simulated`: `test_validacoes_listagem.py`, os dois testes do 30.45;
- `simulated`: `ValidacaoTab.test.tsx`;
- `not_run`: o central depois do deploy.

## Pendências conhecidas

Dos revisores dos pacotes (29/09); nenhuma bloqueou o merge.

- **A2:** `takeover_gravar` não implementado; `revise_plan` pula a etapa sem
  limpar `failure_kind`; `respondeu_pergunta` grava o sha256 do comando inteiro da sucessora em cada campo.
- **A3:** `PATCH … fixed_pending_proof` aceita commit que não está no ar; falso zero de custo quando não há `ai_calls`
  nem régua diária na janela; o CAS do backlog só confere o estado; reabrir só vai ao log; a seção de saúde não traz
  "chamadas por etapa antes e depois de cada publicação"; o top 5 na skill `retomar` não foi feito; a e31953 sai como
  `prazo_da_etapa`, não `app_anr` (o classificador do A1 e os dados divergem), então a prova barata do desenho não bate
  como escrita.
- **A4:** o voto trocado continua contando como refutação; o `total` de `/sinais` é o das linhas devolvidas (até 500);
  os efeitos rodam fora de uma transação única; o voto da execução pode trazer efeito com `ref` vazio; duas lacunas de
  teste (mutações sobreviventes: voto "errado" em item que falhou; filtro por objetivo).
- **A5:** `aprendizado.fluxo.com_prova: false` e `ai.recipes_promote_after: 0` levam o sistema a publicar o que tem
  efeito; o veto das receitas falha aberto. Fechados em 29/09: o interruptor antigo sem trilha, o texto do
  `_learn_flow` para o candidato e o `FLOW_STATUS` cru; na Fase 22, a adoção sem trilha (22.4) e a trilha que
  derrubaria o save no PostgreSQL (22.5).
- **A6:** ~~"Revisar" só rebaixa~~ (fechado pelo 30.24, "Confirmar que fica"); a página lê `/pendentes` duas vezes; o
  limite de "outro" está fixo em 15% no painel. Do bloco da execução: a lição desligada por refutação (dois votos
  "deu errado") é gravada pelo sistema e aparece como "desligada nesta execução"; o LEFT JOIN novo da preferência só
  rodou em SQLite até a suíte em PostgreSQL da Fase 22. Fechados em 29/09: o bloco não
  emitido e a projeção fora do painel.
- **Sinais de 29/09:** as polaridades dos três escritores esperam a ratificação do dono. Fechados na Fase 22: o
  `requested_by` cru e o prefixo da nota do painel (22.2). Decisão do dono pendente: registrar à parte, sem contar na
  régua, a segunda pessoa que repete o mesmo gesto (A pede o controle e desiste, B pede e recebe: o sinal fica com A).
- **A7:** depois de "ajuda", o controle de 10% é gravado, mas nada o reavalia; a aposentadoria conta toda evidência
  contra de voto, sem olhar o braço; a exposição guarda o desfecho do primeiro assentamento (a espera da pessoa
  não fecha mais a da etapa, 30.69); todas as lições de etapa
  livre dividem a chave `(app, '*', papel)`; o SQL novo só rodou em SQLite.
- **A8:** nove mutações sobreviveram, entre elas a árvore sensível do parque na coleta e o "zero contra" depois do
  nascimento (lacunas de teste, sem vazar segredo); a transição pelo livro vale para a sessão só depois do cache de 30
  s; a evidência vinda da conferência de sessão é gravada como real mesmo num parque simulado.
- **A9:** com o modo `off`, `escolheu_habilidade` nem é gravado, e as escolhas se perdem com a retenção dos eventos; o
  teto de 150 tokens vale só para os pares (o bloco passa de ~220); `destemplatizar` troca substring sem fronteira de
  palavra; a varredura olha só 2 dias; o painel não consome as sugestões.
- **Todos:** a suíte em PostgreSQL para o SQL de A2–A9 é `not_run`.

## A aprovação automática (30.55)

Pedido do dono (04/10): há coisa demais para ele aprovar pelo portal. A plataforma passa a decidir, pela régua, a
receita e o fluxo que esperam por ele em duas filas. Desenho aprovado pela orquestradora às 16:16Z; a régua foi medida
no 31.42; emenda datada do ADR-054. Sem migração.

| Fila | Gesto da plataforma | Regra (no motivo) |
|---|---|---|
| "Para aprovar" (`validated` segurado pela D1) | publica (`validated → published`) | `qa_para_aprovar` |
| "Revisar" (legado publicado com efeito) | confirma que fica, sem disparar prova | `qa_revisar` |

**A régua** (`domain/aprovacao_automatica.avaliar`, pura). Devolve todos os motivos de fora, não só o primeiro:

| Condição | Motivo de fora |
|---|---|
| a plataforma ainda não decidiu este item | `ja_decidido_pela_plataforma` |
| todo app do item é de categoria `qa` (`apps.category`) | `app_fora_do_qa` |
| classe de agora A ou B (a mais restritiva entre o dossiê e o parecer; sem dossiê, C) | `classe_c` |
| ≥ 1 a favor real e efetivo na versão atual | `sem_a_favor` |
| 0 contra efetivo | `evidencia_contra` |
| nenhuma falha de reprodução (receita: `replay_fail`) | `falha_de_reproducao` |
| saúde não é `degradando` nem `obsoleto_provavel` | `saude_rebaixando` |
| nenhum parecer real e pendente do curador pedindo rebaixar, desativar, substituir, fundir ou aposentar | `parecer_contra` |
| não é reaprendido (30.23), não tem texto de pessoa, nenhum veto o alcança | `reaprendido`, `texto_de_pessoa`, `vetado` |

O parecer ausente ou `pedir_evidencia` não barra: a delegação do dono cobre. O simulado e o que uma pessoa já decidiu
não pesam.

A evidência da receita mora na fonte: a favor = `replay_ok` + `shadow_agree`; contra = a sombra que discordou
(`shadow_total − shadow_agree`). O fluxo usa a evidência real da marca do conteúdo, sem as execuções invalidadas, como a
autopublicação.

**Como decide** (`application/aprovacao_automatica.py`). O laço próprio roda sob a trava de líder; a primeira volta é
90 s depois do início, e as seguintes a cada `intervalo_s`.
- **Em `shadow`:** marca uma vez o que decidiria (sinal `aprovaria`, `source_ref = aprovaria:<item>`,
  `created_by = sistema`; fora da aba Sinais).
- **Em `on`:** passa pela porta da pessoa (`LearningService.mudar_estado` e `confirmar_que_fica`) com
  `by = "plataforma"`.
  - Trilha, veto, guarda do fluxo e CAS são os de sempre.
  - O motivo é `auto:<regra> v1 — classe B; app com.pocqa.messenger (qa); 10 a favor, 0 contra; 0 falhas de
    reprodução; saúde saudavel; parecer observar (lr-…)`. Na confirmação, vem depois de `confirmado que fica: `.
  - `regra_do_motivo()` devolve `(regra, versão)`. É o contrato com a Canais (28.25), que lê as linhas de
    `decided_by = plataforma` por adaptador.
  - O rótulo do parecer NÃO é gravado: a decisão da plataforma não entra no acerto do curador.
- **A recusa** de uma transição (o item mudou no meio, um veto novo) fica no log, e o item segue com o dono.

**Desfazer** é desligar (`published → disabled`), a ação de sempre da pessoa; a tabela não volta a `validated`. O item
que a plataforma já decidiu não é decidido por ela de novo.

**Leitura:** `GET /api/aprendizado/aprovacao-automatica?itens=true` devolve:
- o modo e a última volta (avaliados, quem decidiria, decididos e quantos ficaram fora por motivo);
- os casos na sombra e as últimas 50 decisões da plataforma;
- com `itens`, a régua item a item.

**Painel** (aba Aprendizado › Para aprovar; `DecididoPelaPlataforma.tsx`): a seção "Decidido pela plataforma" fica
depois das duas filas, para o dono ver primeiro o que sobra para ele.
- **Em `on`:** mostra as 5 decisões mais recentes, com "Ver todas". Cada uma traz o título, o gesto ("Publicou" ou
  "Confirmou que fica"), a hora, a regra e o "Por quê" (os fatos do motivo, sem o prefixo). O botão "Desligar" (motivo
  obrigatório) só aparece enquanto o item segue `published`; o item já desligado leva o selo "Desligado depois".
- **Em `shadow`:** avisa que a plataforma só observa e lista, pelos nomes das filas, o que ela decidiria na última
  volta.
- **Em `off` sem decisão, ou com backend sem a rota:** a seção some.
- "Para aprovar" diz, em `on`, que os itens do app de teste que cumprem a régua a plataforma decide sozinha.

**Config:** `aprendizado.aprovacao_automatica`, com `modo: off | shadow | on` (`off` de fábrica) e `intervalo_s`
(900). O laço lê o modo a cada volta, mas do config CARREGADO: o `config.yaml` só é lido na subida, então mudar o modo
pede reiniciar a tarefa `farm-central` (30.63; antes esta linha dizia o contrário). Escreva o modo entre aspas
(`modo: "on"`). Sem aspas, o YAML lê `on`/`off` como booleano, e desde o 30.63 o booleano vale como a palavra.

Revisão que a régua lê (30.63): a REAL mais recente do curador. Uma simulada mais nova não esconde um parecer real
anterior contra nem uma classe C.

**Prova:**
- `simulated`: `backend/tests/test_aprovacao_automatica.py`.
- Ensaio sobre uma CÓPIA do banco do central (04/10 ~16:40Z, também `simulated`):
  - de 40 itens (3 + 37), decidiria 18, todos do app de QA e classe B;
  - em `on`, as filas foram de 3 para 0 e de 37 para 22;
  - a segunda volta não decidiu nada;
  - os 11 do Instagram ficaram com o dono.
- `real`: `not_run` até o deploy (entra em `shadow`).

## A demonstração substitui a receita da etapa (30.79)

B1 do mapa do ensino (31.81). Achado: a pessoa demonstrava no modo treinamento uma etapa que já tinha receita ativa,
e `RecipeStore.save` descartava a demonstração em silêncio ("a ativa só sai por quarentena").

- **A regra:** a gravação do treino (`learned_from='training:<id>'`, sem herança) com caminho DIFERENTE substitui a
  receita que segura a chave, que é `RecipeStore.viva`:
  - a ativa aprendida da IA;
  - a `validated` que esperava o dono, que assim sai da fila dele;
  - a de uma demonstração anterior.
- **A substituída** vai para `superseded`. A trilha dos dois lados é assinada pela sessão de treino (`por`), não pelo
  sistema.
- **O mesmo caminho** não grava nada: `save` devolve o id da que já vale. Ao substituir, devolve o id novo.
- **Fora do treino nada muda:** a ativa e a `validated` seguram a chave, e a herança (RA-20) segue sendo do sistema
  mesmo vinda de receita ensinada.
- **A trilha da substituída** é assinada pela sessão de treino em qualquer status (ativa, `validated`, candidata,
  quarentena): a causa foi a demonstração.
- **Prévia e salvar do treino (junção com o 31.86):** os dois leem `RecipeStore.previa_do_treino`, a mesma conta do
  `save`.
  - O mesmo caminho dá "já havia receita", e outro caminho dá "…, substituindo a vN (receita N)".
  - `chave_ocupada` é `viva(...) is not None`.
- **O reparo** (`POST /api/training/{id}/recipes`) não herda esse poder: passa `so_em_chave_virgem=True`, e o `save`
  grava só em chave que nunca teve receita e sem veto da pessoa. A conferência se repete DENTRO da transação.
  - O prefixo continua `training:`: a receita refeita segue ensinada para a origem no painel, para `Origem.TREINO`,
    para as genéricas e para o aviso do 30.80 B.
- A demonstração ainda nasce ativa e não passa pela prova; isso é o 30.81.
- **Prova:** `simulated`, em `backend/tests/test_treino_substitui_receita.py`. `real`: `not_run`.

## O ensinado rebaixado avisa (30.80 parte B)

B2 do mapa do ensino (31.81). A receita ou o fluxo que a pessoa demonstrou no modo treinamento caía em quarentena ou
era desligado pela obsolescência em silêncio: a etapa voltava para a IA e quem ensinou não sabia.

- **Quando:** o item `training:<sessão>` estava em uso (`published`) e o SISTEMA o tirou de uso. Dois caminhos,
  ambos depois da trilha e na mesma transação:
  - a loja (`LearningService.avisar_mudanca_nativa`): quarentena por falhas seguidas, substituição;
  - o Livro (`LearningService._mover_nativo`): a obsolescência e todo `mudar_estado(by='sistema')`.
- **Não avisa:** o gesto de uma pessoa, outra demonstração (30.79: o autor é a sessão de treino), o item que a IA
  aprendeu e o nascimento.
- **Dois tipos, um por transição:**
  - `learning.ensinado_sem_receita` (`warn`): nada ativo ficou no lugar, ou seja, nenhuma receita ativa na mesma chave
    e nenhum fluxo ativo no mesmo `match_key`;
  - `learning.ensinado_rebaixado` (`info`): outro ativo segura o lugar.
- **Payload** (`domain/ensinado.py::CAMPOS_DO_PAYLOAD`), lista fechada combinada com a Canais (28.50): `kind`, `ref`,
  `app`, `treino` (o id inteiro, `trn-…`), `sem_receita_ativa`, `para` (status nativo) e `desde`.
  - `desde` é o `decided_at` da última linha da trilha do item, e não o relógio: a reemissão traz o mesmo valor, que é
    a chave de deduplicação da Canais.
  - Nunca conteúdo, seletor, conta ou texto de tela.
- **Quem traduz para o dono** é a frente Canais (28.50): o texto do aviso é fixo, e o id vai só no link do detalhe.
- **Prova:** `simulated`, em `backend/tests/test_learning_ensinado_rebaixado.py`. `real`: `not_run`.

## O ensinado só vale para quem ensinou até a prova (30.81)

O fluxo salvo no modo treinamento nascia ativo e casava para qualquer aparelho sem prova nenhuma. Desenho aprovado pela
orquestradora em 05/10 (opção B, 15:19Z; restrição por persona, 15:21Z; ajustes de 16:25Z).

- **Nasce ativo, restrito** (`taskqueue/flows.py::ensinado_em_prova`):
  - O `match` com aparelhos só usa o ensinado quando TODOS os perfis são a persona do ensino
    (`training_sessions.profile_id`).
  - A prévia sem aparelhos (`profile_ids=None`) casa como antes.
  - A sessão sem persona não casa em lugar nenhum, e o salvar diz isso numa linha de `warnings`.
  - O fluxo que não veio do treino sai antes de qualquer consulta.
  - O `IntentResolver` não muda, e não há migração.
- **A espera acaba** com:
  - uma prova real a favor, sem `invalida` da mesma origem, de uma execução com `prova_fluxo_id` deste fluxo, depois do
    nascimento;
  - ou o "Confirmar que fica" EXPLÍCITO de uma pessoa depois do nascimento (motivo `confirmado que fica`; leitura da
    Reload, achado 3). Não contam `sistema`, `plataforma` (a régua), `training:` (outra demonstração) nem outra linha da
    pessoa (adotar e desfazer não liberam). Desligado, o fluxo não está ativo.
- **A receita do treino espera junto** (achado 1, decisão da orquestradora): enquanto o fluxo da mesma sessão espera a
  prova, `RecipeStore.find` só acha a receita `training:<sessão>` para a persona do objetivo que ensinou
  (`persona=`, passado pelo executor). Fora dela, a consulta termina `ensino_em_prova` (rótulo novo de
  `receita.consulta`), sem herança, e a etapa vai para a IA. Quem ensinou usa a receita logo depois de salvar. A
  receita que não veio do treino não paga consulta a mais.
- **O que libera a receita do treino** (N1 e N2 da Reload, decisão da orquestradora): o "Confirmar que fica" explícito
  de uma pessoa no fluxo da sessão, ou a evidência a favor DA RECEITA (`receita:<id>`, `for`, não simulada, sem
  `invalida`) numa execução real de prova desse fluxo. A prova aprovada do fluxo sozinha não basta: a receita que não
  rodou na prova segue só para quem ensinou. A execução de prova do próprio fluxo (`prova_fluxo_id`, passado pelo
  executor como `prova_fluxo=`) acha as receitas da sessão, para provar o que vai liberar. O fluxo desligado por uma
  pessoa não solta as receitas dele. O fluxo APAGADO (`DELETE /api/flows/{id}`) também não (N4 da Reload): sem
  fluxo, a receita segue só para a persona da sessão, e só a evidência a favor dela numa execução real DE PROVA a
  libera (N5: a execução comum de quem ensinou não conta).
- **Para a validação** (achado 4), o fluxo ativo do comando é `FlowStore.ativo_para`: o `match` sem aparelhos, sem o
  ensinado em espera (`state.py`, `fluxo_ativo_para` e `plano_ativo_para`).
- **Escolha de escopo (31.88 F2):** o salvar e a prévia aceitam `scope_on_proof` (`todos` ou `quem_ensinou`); este grava a
  persona do treino na `flow_scope`, e o escopo fica depois da prova. A pessoa muda depois por `PUT /api/flows/{id}/scope`,
  com o antes e o depois no evento `log` (fora de `learning_transitions`). Adendo v1.71.
- **Quem abre a prova:** a volta da validação, e não o ouvinte do nascimento. O `save` grava a proposta final (com os
  `example`) DEPOIS de `learn_from_plan`, e o ouvinte leria a proposta velha.
  - `EnsinoDaValidacaoSql.a_provar` é uma consulta só por volta.
  - O pedido é a reprodução em outro aparelho. O comando é o molde com o `example` de cada parâmetro
    (`domain/validacao.py::comando_do_ensino`), e o aparelho do treino fica de fora.
  - O `review_id` é `ensino:<sessão>`, com um pedido vivo por item. A execução leva a chave de sempre da validação.
- **O que a prova automática não cobre passa à pessoa.** O pedido nasce `recusada` com o motivo, e sai
  `learning.ensinado_espera_decisao`. Isso vale para:
  - a classe C, e o item sem dossiê de agora (`classe_c`);
  - toda recusa ao nascer: `efeito_real`, `sessao_ou_autenticacao`, `credencial`, `sem_origem` (faltou exemplo) e
    `sem_caminho`;
  - as tentativas esgotadas: 3 pedidos sem veredito, por aparelho fora do ar, expiração ou sem evidência
    (`tentativas_esgotadas`, `TETO_DE_TENTATIVAS_DO_ENSINO`).

  O conjunto é `MOTIVOS_QUE_ESPERAM_A_PESSOA`. Depois dele, a volta não abre mais nada para aquele nascimento.
- **Rebaixamento** (`SombraDosFluxos._rebaixar_o_ensinado`):
  - Só o veredito CONTRÁRIO (`against`) de uma prova real desliga o fluxo que ainda esperava. Vai a `disabled` pelo
    sistema, como a sombra contradita, com a execução na trilha. As receitas ativas `training:<sessão>` vão junto ao
    estado `disabled` do Livro (achado 2), que na receita é o status nativo `quarantined`, cada uma com a trilha.
  - O 30.80 B avisa pelo caminho que já existe.
  - Não rebaixam: a infraestrutura (`posicao=None`), a prova simulada e a forma.
  - Uma pessoa reverte pelo Livro.
- **A decisão da pessoa:**
  - "Confirmar que fica" (30.24) passa a valer para o ensinado que espera a pessoa; antes, só "Revisar". O resultado é
    `liberado`.
  - Desligar pelo Livro é `desligado`.
  - As duas publicam `learning.ensinado_decidido`, uma por nascimento: a espera é lida ANTES da linha da pessoa.
  - O ensinado que ainda está na prova automática não se confirma (409).
  - O Livro diz quando o botão vale: `espera_a_pessoa` traz o motivo literal no fluxo que espera, e `null` no resto
    (`presentation/livro.py::_da_espera`, para a Portal).
  - **30.85:** a entrada do fluxo no Livro, na lista e no item, leva também `ensinado_em_prova` `{persona, sessao}`
    enquanto o ensinado espera a prova, pela regra do casamento (`taskqueue.flows.ensinado_em_prova`, pela porta
    `LeitorDoEnsinado.em_prova`). O campo fica ausente fora disso. Achado da Portal no percurso do deploy 42: o Livro
    mostrava "Publicado" sem o selo. Só o fluxo ensinado ativo paga a consulta.
- **Eventos**, combinados com a Canais às 15:31Z:
  - `learning.ensinado_espera_decisao`: `{kind, ref, app, treino, persona, desde}`;
  - `learning.ensinado_decidido`: `{kind, ref, desde, decisao, decidido_em}`.

  O `desde` é o nascimento (`flows.created_at`), o mesmo nos dois. Persona e slug nunca vão no `message`. O cartão da
  lista de perguntas não decide (C-13).
- **Campo da Portal:** `ensinado_em_prova: {persona, sessao}`, ausente quando não se aplica. Vai em:
  - `cobertura_do_fluxo`, que serve `/flows/match` e `/flows/cobertura`;
  - a resposta do salvar do treino;
  - o topo da resposta de `/training/{id}/recipes`.
- **Limites:**
  - Se o barramento falha, o evento `learning.ensinado_espera_decisao` se perde: o pedido recusado que marca a espera
    fica, e o Livro mostra `espera_a_pessoa`, mas a Canais não recebe o cartão (leitura da Reload, item 6).
  - O fluxo desligado pela prova bloqueia o reensino do MESMO comando (a `match_key` é única): fica para o 30.84.
  - A receita escondida pela espera não cai para a genérica nem para a herança (N3 da Reload): é falha fechada, e a
    etapa vai para a IA mesmo quando uma receita genérica serviria.
- **Prova:** `simulated`, em `backend/tests/test_ensinado_em_prova.py`. `real`: `not_run`.

## Reensinar o comando que a prova desligou (30.84)

- Antes, o fluxo ensinado que a prova real desligava (30.81) seguia na linha, e a `match_key` única fazia o `save` do
  treino e a prévia responderem `duplicate_command`: a pessoa não conseguia corrigir a demonstração.
- Agora (desenho da orquestradora, 05/10) esse fluxo renasce na MESMA linha (`FlowStore.learn_from_plan`): mesmo id e
  referência pública, plano, sessão (`source`) e nascimento (`created_at`) novos, `uses` zerado, ativo.
  - Volta à espera de prova do 30.81: a prova e o Confirmar contam a partir do `created_at`, e as tentativas por
    sessão. A prova antiga não libera o renascido.
  - A trilha diz que renasceu e por que tinha sido desligado ("reensinado no modo treinamento (mesma linha);
    desligado antes: …"), assinada pela sessão nova (o treino nunca tira da espera).
  - As receitas rebaixadas com o fluxo ficam como estão; as da sessão nova seguem a substituição do 30.79.
- Só renasce o desligado PELA PROVA (`MOTIVO_DA_PROVA_DO_ENSINADO`, pelo sistema) quando esse desligamento ainda é
  a última linha da trilha. Seguem recusando: o desligado por uma pessoa, o que uma pessoa mexeu depois da prova, o
  adotado por uma habilidade, o ativo e o comando com habilidade publicada.
- A prévia do treino (31.86) recusa pela mesma regra (`FlowStore.recusa_do_treino`).
- A recusa e a trilha são lidas dentro da transação do renascimento. A sessão antiga e a nova ficam com o mesmo
  `training_sessions.flow_id`; hoje nada lê o fluxo por ali (o ensinado acha a sessão pelo `flows.source`).
- **Prova:** `simulated`, em `backend/tests/test_reensinar_o_desligado_pela_prova.py`. `real`: `not_run`.

## A referência pública do fluxo é aleatória (30.83)

- O id do fluxo era o slug do `plan.summary` literal (podia trazer nome ou @) e saía em evento, `href` e log (achado S1
  da leitura do 30.80 B, herdado do 30.21). Desenho aprovado em 05/10.
- `flows.ref_publico` (migração 116): `f-` mais 12 hex aleatórios, nunca derivada do conteúdo (um hash do nome deixaria
  confirmar o palpite). O fluxo novo nasce com `id = ref_publico` (`flows.ref_aleatoria`); o antigo mantém o id (sem ON
  UPDATE CASCADE nas referências) e ganha a referência na subida (`preencher_refs_publicas`, idempotente).
- Sai a pública: `learning.needs_person` de fluxo (`ref` e `href`), sem a referência no `message`. Entra qualquer uma:
  as rotas do Livro com `{ref}` e o pedido de validação traduzem por `LearningService.ref_interna` (adendo v1.66).
- A habilidade adotada de um fluxo novo herda o id opaco (`<app>.f-…`); o nome legível segue em `flows.name`.
- Fatia 3: os eventos do ensinado (30.80 B e 30.81) saem pela pública na mesma porta (`EventosNoBarramento._publico`);
  as rotas antigas de fluxo (`PUT`/`DELETE /api/flows/{id}`, adopt e release) e o `href` de desfazer do voto aceitam
  ou levam a pública. Os logs não citam o fluxo: `quem_no_log` e `ref_no_log` (sem banco, porque os `log.exception`
  rodam dentro da transação que falhou) dizem só o tipo; a autopublicação loga só as contagens.
- `ref_publica_do_fluxo` nunca devolve o id: sem a `ref_publico`, preenche na hora; sem a linha, sorteia uma que não
  abre nada (cada chamada outra; a Canais só avisa a entrada).
- Fatia 4: o texto das exceções de fluxo (Livro, pedido de validação, loja) diz só "fluxo" (`quem_no_log`), e os logs
  da sombra dos fluxos dizem só o tipo da exceção.
- **Limites:** o id interno segue em `item.ref` do Livro, no `ref` do efeito do voto, em `GET /api/flows`, na resposta
  do `PUT /api/flows/{id}`, no `flow_id` de adopt e release e no id da habilidade adotada de um fluxo legado.
- **Prova:** `simulated`, em `backend/tests/test_ref_publico_do_fluxo.py`. `real`: `not_run`.

## A prova sem evidência diz a causa (30.75)

A leitura de 05/10 (`.claude/handoffs/aprendizado-sem-evidencia.md`) achou 5 pedidos de fluxo `sem_evidencia`:
- dois foram o teto do pedido cortando a execução no meio, que gastou US$ 0,157 e 0,159 sem deixar evidência;
- um foi o QA Messenger do android-02 deslogado: a prova parou na tela de senha, com US$ 0;
- os outros dois eram de antes do `prova_fluxo_id` e já estavam corrigidos.

Os três primeiros diziam só "sem evidência", e a medida não sabia o que travou nem quanto custou.

- **Motivos novos do pedido** (`domain/validacao.Motivo`), escolhidos pelos campos estruturados, nunca pelo texto
  (`FontesDaValidacaoSql.causa_sem_evidencia`):
  - `orcamento_da_prova`: a ÚLTIMA tentativa da execução terminou com `attempts.error_kind='budget'`: um teto de IA
    a encerrou. `budget` é o tipo de todo teto (o do pedido, o do dia, o de chamadas, o de uma ação); um teto que a
    execução sobreviveu (o da leitura do 31.38) não é a causa. Quando o teto encerrou, ele vence o login;
  - `app_sem_sessao`: algum objetivo da execução tem `objectives.blocked_kind='auth'`;
  - sem nenhum dos dois, `sem_evidencia`, como antes;
  - só em execução de PROVA (`runs.prova_fluxo_id`). O estoque `sem_evidencia` de execução comum (antes do 30.37)
    segue `sem_evidencia` e no caminho da reabertura (C1 da leitura do #419).
- **Onde valem:** no fechamento do pedido de FLUXO (`ServicoDeValidacao.minerar`) e no passo da curadoria que remotiva
  o estoque `sem_evidencia` (`executar`, só a partir de `sem_evidencia`).
  - Nenhum dos dois é chegada do curador nem se reabre: não dizem nada sobre o fluxo.
  - Não são falha do executor, que não ganha texto novo; por isso não há regra nova em `falhas.py`.
- **A causa estruturada do login:** o executor marca o desfecho da tela de senha com `StepOutcome.pede_login`, e só na
  execução de PROVA o `Scheduler._prova_sem_pessoa` grava `blocked_kind='auth'` no objetivo que encerra. A execução
  comum segue como sempre: `waiting_user`, sem essa marca.
- **O aparelho sai das próximas provas daquele app** (`DespachoDoParque._sem_sessao_no_app`). O aparelho em que uma prova
  de fluxo do pacote parou no login deixa de ser candidato para os pedidos que exigem o pacote. Ele volta quando:
  - a verificação do app nele (`device_app_state.verified_at`) é posterior à parada; ou
  - um objetivo `succeeded` num fluxo do mesmo app (`runs.flow_id` ou `prova_fluxo_id`), no mesmo aparelho, terminou
    depois da parada. A execução comum que entrou no app prova que a sessão voltou.
  - Sem outro aparelho que sirva, o pedido espera (`sem_aparelho`), como sempre.
  - Limites conhecidos (leitura do #419):
    - a verificação confere a INSTALAÇÃO, não a sessão: a de rotina (ao ligar o aparelho com o `verified_at` de mais de
      24 h) solta o aparelho ainda deslogado, e a próxima prova nele pode gastar a vaga de novo;
    - a exclusão usa o app PRINCIPAL do fluxo: o login pedido por um app secundário exclui o app errado;
    - com `verify_max_age_h=0`, só a verificação manual, a reinstalação ou um sucesso de fluxo soltam.
- **O estoque de 05/10 não ganha a marca do login:** a parada foi gravada antes do campo. O passo da curadoria passa a
  `orcamento_da_prova` os dois cortes de 03/10, e o de 05/10 segue `sem_evidencia`.
- **API:** os dois valores novos de `motivo` em `GET /api/aprendizado/validacoes` (adendo v1.54).

**Prova:** `simulated` em `backend/tests/test_validacao_motivos_da_prova.py` (verificada por mutação: sem a marca no
scheduler ou sem o filtro do aparelho, os testes reprovam). `real`: `not_run` até o deploy e a primeira prova com essa
parada.

## A receita que não se aplicou (30.80)

Achado real (05/10, a prova do 31.79): a `r-20261005133833-122345` partiu de dentro de uma conversa no android-12. A
receita 194, ensinada no modo treinamento, divergiu na ação 1 ("alvo ausente ou ambíguo nesta tela"), a IA comprovou a
etapa, e a receita saiu com `replay_fail=1`. A falha era da tela de partida, não dela.

- **"Não se aplicou"** (`StepExecutor._after_step` e `RecipeStore.nao_aplicavel`), quando valem as três condições:
  - a receita divergiu na AÇÃO 1, antes de agir (`Replayer.done_actions == 0`);
  - a divergência foi alvo AUSENTE (`recipes.AlvoAusente`). O seletor AMBÍGUO, que casa mais de um elemento, fica de
    fora com a mesma mensagem: deixou de ser único, e isso é defeito da receita;
  - a etapa TERMINOU comprovada.
- **Nesse caso:**
  - nem `replay_ok` nem `replay_fail` sobem, e `consecutive_fail` fica como está;
  - `steps.driven_by='ai'`, porque nenhuma ação da receita rodou. Por isso a etapa não vira evidência contra no
    aprendizado (`reproducao_sql.py` só lê `recipe`, `recipe+ai` e `sem_ator`);
  - `attempts.recipe_id` segue apontando a receita tentada;
  - a etapa que seria `sem_ator` também fica `ai`. É de propósito (sai da evidência contra), mas soma uma etapa da IA
    no `/api/usage` sem chamada de IA.
- **O evento `decision`** da execução diz "tela de partida diferente" e leva o código estável no `data`:
  `kind='receita_nao_aplicavel'`, `recipe_id`, `step_id` e `contou_como_falha`. Isso dá para contar sem ler texto, e a
  receita ensinada tentada que não servia aparece já na 1ª vez.
  - A trilha (`learning_transitions`) fica para mudança de estado.
- **Contrapeso** (migração 115, `recipes.nao_aplicavel_seguidas`): da 3ª "não se aplicou" SEGUIDA em diante, CADA
  uma conta como falha comum (`recipe+ai`, `replay_fail`, a quarentena de sempre), sem zerar a série. Assim um 1º
  seletor quebrado (atualização do app) chega à quarentena na 5ª execução.
  - Na 1ª versão a 3ª zerava a série, e a quarentena só chegava na 9ª (R1 da segunda leitura).
  - O ok, a falha comum, o gesto da pessoa na rota `PUT /api/recipes/{id}` e a reativação pelo livro zeram a série.
- **Continua sendo falha comum:**
  - a divergência depois da 1ª ação;
  - "ações reproduzidas, mas a pós-condição não apareceu";
  - parâmetro ausente;
  - a etapa que a IA assumiu e que falhou ou ficou incerta.
- **Prova:** `simulated`, em `tests/test_receita_nao_aplicavel.py`, que parte de dentro de outra conversa como o caso
  real.

## Conhecimento da operação (prova30 A1, 06/10)

Uma operação (`operacoes`, 124) junta N agentes, cada um com persona, conta e execução próprias, sobre um objetivo.
O que eles sabem em COMUM não ganha armazenamento novo. Mora na memória e nas observações do pedido (070), que a 125
deixa ser de um pedido OU de uma operação:

| O quê | Onde | Quem escreve |
|---|---|---|
| a leitura do alvo (o post) | `pedido_observacoes` (`operacao_id`, `nome='leitura_do_alvo'`, sha256, fonte, instante) | o 1º agente que lê; os outros só conferem o sha256 |
| o fato consolidado | `pedido_memoria` (`origem`, `confianca`, `evidencia`, `frescor_ate`) | a leitura (`alvo.conteudo`); a pesquisa externa (A2); o operador |
| a leitura que não bate | `pedido_observacoes` `incerto`, `alvo` = o objetivo do agente | o agente que viu outra coisa |

Ao texto de cada persona vão três blocos com donos diferentes:
- o `context_text` da persona: identidade, conta no app, memória, histórico e voz;
- o `<fatos_da_operacao>`: fato, hipótese marcada e fonte, como dado citado;
- a `<intencao>`.

O fato comum nunca vira memória da persona. Ela lembra do que FEZ (o efeito confirmado), não do que leu em comum.
Numa operação, a lista "não repita" e a trava de escrita são da operação inteira, não da execução. Código:
`modules/pedidos/{domain,infrastructure}/conhecimento_da_operacao.py` e `gates.py` (`_conhecimento_da_operacao`).
O que seria um segundo sistema, e por que não se fez assim: `.claude/handoffs/prova30/aprendizado.md`.

### Pesquisa externa por lacuna (31.158)

- Quando o assunto da operação (`operacoes.assunto`) não tem fato de pesquisa válido, a primeira execução que chega à
  porta de escrita pesquisa UMA vez, pela ferramenta de busca do próprio provedor (`ai.pesquisa`, desligada de fábrica).
- O resultado entra pela mesma porta: as fontes viram observações `url` e entradas `fonte`; os fatos viram `descoberta`
  com `origem='pesquisa'`.
- A confiança é decidida por código: dois domínios = confirmado; um = hipótese; URL que a busca não trouxe = descartada.
- Teto por operação, somado de `ai_calls` (`origem='pesquisa'`, `ref` = a operação; as buscas têm linha própria com
  `usd`).
- Falha deixa a marca `pesquisa.estado` por 1 h. Código: `planning/pesquisa.py` e
  `modules/pedidos/infrastructure/pesquisa_da_operacao.py`.

### O aprendizado de uma operação nas 10 perguntas do dono (prova30 A3)

`GET /api/operacoes/{id}/aprendizado` responde, para UMA operação, às 10 perguntas do dono: o que a plataforma
aprendeu, o que a persona aprendeu, o que é do app, o que é do processo, o conhecimento geral, as fontes externas, o que
sustenta cada conhecimento, o que é reutilizável, o que revisar e as falhas que geraram aprendizado. Não há tabela nova:
a resposta é lida de onde cada coisa já mora, pelas execuções da operação. A lista de ligações (direta, por prefixo,
pela proveniência, pela interação ou inferida) está no topo de `infrastructure/aprendizado_da_operacao.py`.

A confiança fica numa régua só (`confirmado`/`hipotese`) e o valor original aparece ao lado, em `estado`. O que a rota
não responde aparece em `nao_coberto`. Contrato: adendo v1.96. O relatório da operação da Portal (31.162) lê esta rota.

## A curadoria por operação encerrada (31.217)

O passo do 31.190 (o fato confirmado da pesquisa vira candidata do escritor) rodava só na volta periódica da curadoria
(`aprendizado.curadoria_s`, 15 min de fábrica), e ninguém via o que ele tinha promovido ou recusado.

- **Na hora:** o laço da curadoria (`state.py::_curadoria_loop`) ouve `operacao.encerrada` no barramento e roda, só no
  líder da trava `curadoria`, os passos que sabem rodar por operação (`PassoPorOperacao`; hoje, o dos fatos). Entre um
  evento e outro, a volta periódica segue no mesmo prazo. O evento perdido (assinatura descartada, processo fora) é
  coberto por ela: a volta olha as operações encerradas da janela de 7 dias.
- **O relatório** (`FatosDaOperacaoParaOLivro.da_operacao`) é publicado como `aprendizado.curadoria_da_operacao`:
  - `nascidas`: os ids das candidatas novas;
  - `ja_no_livro`: os fatos que já tinham item vivo;
  - `recusadas`: a contagem por motivo fechado (`MotivoDaRecusa`: `nao_e_fato`, `hipotese`, `vencido`, `sem_app`,
    `longo`, `identificador`, `sem_assunto`), a mesma régua da `candidata`;
  - `vetadas`: o que o Livro recusou (veto de uma pessoa ou texto com cara de credencial);
  - `vencidas_no_livro`: os itens vivos do mesmo app que nasceram de um fato cujo frescor passou. Só relata: o item
    não muda de estado.
- Só ids e contagens: o texto do fato fica no Livro.

## O plano de ensino guiado do Instagram (31.201)

Só documento e leitura: nada aqui foi executado. O plano diz o que ensinar no Instagram, em que ordem, em que aparelho
e como medir, a partir das contas novas do dono (29.168; o roteiro de cadastro e de primeiro login está em
`.claude/handoffs/prova30/android.md` §11–§13). Cada passo com conta real pede o sim do dono na hora.

**Ponto de partida.** Leitura só de leitura no central em 07/10, ~00:30Z, commit `42cba3cd`, banco aberto em `mode=ro`:
- sessões de ensino: 26, das quais 12 salvas, 9 descartadas, 3 gravadas e 2 propostas. As salvas são de 3 personas;
- receitas ensinadas ativas: 14 no Ajustes do Android, 7 no QA Messenger e **1 no Instagram** (a 221, `open_profile_1`,
  abre o perfil de quem roda, com 0 reproduções);
- etapas reais conduzidas por receita ensinada: 9 no Ajustes do Android, 12 no QA Messenger e **0 no Instagram**.

No Instagram, o ensino ainda não rendeu uso real. O plano existe para mudar isso sem efeito fora da máquina.

### As regras que valem durante todo o plano

- **A conta nova só lê** (§13.6): nada de seguir, curtir, comentar, mandar mensagem ou publicar até o dono dizer.
  - A etapa com efeito não se ensina por aqui: a troca do ensino a recusa (31.153).
  - Uma ação com efeito continua no catálogo, com aprovação.
- **Um login por vez, medido** (§13.2), fora das janelas de medida do notebook. Planejar 2 rodadas por conta (§11).
- **A tela de bloqueio para tudo** ("Confirm you're human" e afins). Ninguém toca, e a conta sai (§11).
- **Uma conta por aparelho**, e o aparelho com conta não tem o reinício de dados.
- **Valor da persona no ensino.** O @ ou o nome da própria persona digitado durante o ensino é trocado por parâmetro e
  mascarado na gravação, na proposta e no relatório (K-107, 31.183).
  - A etapa que mira a conta da PRÓPRIA persona traz o aviso do 31.182.
  - Essa etapa não serve a outra persona como foi gravada.
- **A proposta do ensino é uma chamada paga de IA** (papel do planejador, `training/generalizer.py`), uma por sessão.
  O custo de cada sessão é lido em `ai_calls`. Com o uso pago autorizado para operar, o teto é por sessão, e o gasto
  vai na trilha.

### O que ensinar, em ordem (tudo só leitura)

| # | O que | Parâmetro | Por que nesta ordem |
|---|---|---|---|
| 1 | Abrir o Instagram até a tela inicial | nenhum | É a base de todas as outras. Se a abertura não fica estável, o resto cai na IA |
| 2 | Abrir a busca e procurar um perfil pelo @ | `{username}` | É a etapa que a operação mais repete. O valor vira parâmetro: nunca o @ de quem ensina |
| 3 | Abrir o perfil achado | `{username}` | É o alvo da operação (P-027: o post nosso mais recente) |
| 4 | Abrir o post mais recente do perfil | nenhum | É a leitura do alvo (31.179) |
| 5 | Voltar à tela inicial | nenhum | Fecha o caminho. Sem ela, a próxima etapa parte de uma tela desconhecida |
| 6 | Abrir o próprio perfil | nenhum | Só para a persona que ensina (31.182). Fica por último porque não serve às outras |

Uma persona ensina; as outras usam. Pela regra do 30.81, a receita ensinada vale só para quem ensinou, até o
"Confirmar que fica" no Livro ou uma prova real. Ensinar a mesma etapa em várias personas gasta IA e não mede nada a
mais.

### Onde

- **Primeira conta: o android-11 (central)**, depois de ligado e com o app verificado (§12).
  - O central tem folga de CPU, e o ensino grava cada toque.
  - A persona dessa conta ensina as etapas 1 a 6.
- **Segunda conta: o android-09 (notebook).** Ela não ensina: executa as etapas 1 a 5 depois da liberação, para medir o
  alcance noutra persona e noutro host.
- **As outras contas novas** (10, 12, 05; §12) entram uma a uma, só para usar. Não há ensino novo, salvo onde a medida
  mostrar uma tela diferente.

### Como medir cada receita (rotas desta fase)

1. **Depois de salvar**: o relatório do ensino diz o que virou receita e o que ficou na IA. A troca diz o que recusou
   (efeito, dado da persona).
2. **Uso na persona que ensinou**: 2 ou 3 operações só de leitura no mesmo app.
   - `GET /api/aprendizado/receitas/{id}/rendimento` (31.191, adendo v1.110) dá `sem_ia.real`, `caiu_na_ia.real` e o
     `custo_evitado_usd`.
   - A régua proposta para o "liberaria" do 31.202: 3 usos reais sem IA, em 2 execuções distintas, e nenhuma falha nas
     últimas 3.
3. **Quem pode usar**: `GET /api/aprendizado/alcance?app=com.instagram.android` (31.181, adendo v1.107) diz, por
   persona, se a receita vale e o motivo de não valer. Até a liberação, a resposta esperada para as outras personas é
   `pode: false`, motivo `presa_a_quem_ensinou`.
4. **Liberar**: o dono decide no Livro ("Confirmar que fica"), olhando o rendimento. Depois disso a segunda conta usa.
   - O mesmo `rendimento` passa a contar o uso fora de quem ensinou.
   - A sugestão do 31.202 (em sombra) fica ao lado, para comparar com a decisão do dono.
5. **A prova da onda**: `scripts/prova-onda-aprendizado.py` (31.192) marca o 31.165 como `real` quando uma etapa da
   operação foi conduzida por receita do ensino. É o primeiro uso real do ensino no Instagram.

**Pronto** quando as etapas 1 a 5 tiverem `sem_ia.real ≥ 3` na persona que ensinou e pelo menos 1 uso real sem IA na
segunda conta, sem falha nas últimas 3. **Parar** e chamar o dono na tela de bloqueio, no `auth_challenge`, na conta
errada aberta ou com 2 falhas seguidas da mesma receita.

### O que este plano não faz

- Não cria conta, não digita senha e não resolve verificação: isso é do dono (§11).
- Não ensina ação com efeito e não muda a regra do 30.81. A liberação sem pessoa é a pergunta do 31.202.
- Não liga aparelho, não mexe em `max_online_devices` e não roda no meio da medida do notebook.

## A sombra da quarentena pelo rendimento (31.202)

Um passo da curadoria, sem IA, lê o uso REAL de cada receita ensinada ativa e diz o que faria. **Nada se aplica**: a
loja de receitas, a quarentena (3 falhas seguidas) e o 30.81 seguem iguais. Ligar de verdade é uma pergunta ao dono.

- **"liberaria"**: a receita ainda vale só para quem ensinou e rendeu no uso real dessa persona.
  - O limiar: 3 usos sem IA, em 2 execuções distintas, e nenhuma falha nas 3 últimas tentativas dela.
  - O uso de outra persona não conta.
- **"prenderia de volta"**: a receita já liberada falhou nas 2 últimas tentativas reais fora de quem ensinou. Falhar
  aqui é a receita não conduzir sozinha (`recipe>…` ou falha).
- **"nenhuma"**: o resto, com o motivo e as contagens.
- **Nunca "liberaria"** para a receita com efeito externo (ação `commit`) nem para a que mira a conta da própria persona
  (o parâmetro `{conta_<app>_usuario}`, o mesmo do aviso do 31.182). A receita 221 do Instagram é deste caso.
- **O uso real** segue a régua do rendimento (`domain/rendimento.tipo_de_uso`): nem simulada, nem prova, nem lote. A
  persona de cada tentativa é a do objetivo da etapa (`objectives.profile_id`). A liberação usa a régua da loja
  (`RecipeStore.liberada_fora_do_ensino`).
- **Onde fica**: um sinal por receita em `learning_signals`, sobrescrito a cada passo.
  - `kind` é `sombra_da_quarentena` e `source_ref` é `receita:<id>`, com `created_by` sistema.
  - `reason` é a sugestão e `note` o motivo. `data` leva as contagens, a regra (`31.202-v1`) e se está liberada.
  - Como as sombras do 30.34 e do 30.55, o sinal não é gesto de pessoa: fica fora da aba Sinais. Lê-se por
    `GET /api/aprendizado/sinais?kind=sombra_da_quarentena`.
- Código: `domain/sombra_da_quarentena.py` e `infrastructure/sombra_da_quarentena_sql.py`. A montagem só registra o
  passo quando há a loja de receitas.
- A pergunta ao dono (o que muda com sim e com não) está em `.claude/handoffs/aprendizado-pergunta-31-202.md`.

## A lição do planejador por persona (31.218)

O 31.149 fazia da correção ensinada (a que não virou receita na etapa que falhou) uma lição do planejador do app
inteiro. O que uma persona errou ia ao plano de todas.

- **De quem é:** a lição nasce com a persona do objetivo que falhou (`scope_profile_id`; o id, nunca o nome, também na
  proveniência). Sem persona na execução, segue do app.
- **Para quem vai:** a lição com persona só vai ao planejamento de UMA persona, a mesma (`licoes.nivel`). A costura
  `PedidoDeLicoes.profile_id` vai preenchida só quando todos os aparelhos do plano são dessa persona; com várias, vai
  '' e a lição fica fora.
- **Identidade:** a persona faz parte do escopo, então a mesma correção a partir de duas personas são dois itens. Cada
  um espera o dono: o texto é de pessoa (D1).
- **Onde se vê:** o alcance (31.181) mostra, em cada persona, as `correcoes`: o que ela erra (a etapa), como se
  corrige (o caminho) e se vale só para ela ou para o app. A lição anterior ao 31.218 aparece na persona da etapa que
  falhou, marcada `app`. A prévia das lições aceita `persona=` (adendo v1.118).
- `scope_profile_id` deixa de ser só de voz e preferência. O relatório do aprendizado da operação já trata o item
  com dona como conhecimento da persona; a lição de correção passa a contar lá desse jeito.

## O ensino do fluxo para alvo de terceiro (31.219)

Preparado, não executado. O alvo da onda 2 passou a ser o primeiro post de uma página pública de terceiro (P-029). A
única receita ensinada do Instagram (a 221) abre o PRÓPRIO perfil e não serve a esse alvo. Este é o roteiro para
ensinar o caminho certo quando o dono der a conta de teste.

**A proposta de referência** está em `backend/tests/fixtures/ensino/proposta_alvo_de_terceiro.json`. É o que a sessão
deve salvar:
- comando `abra o perfil de {username}, a primeira publicação e os comentários`, com um parâmetro só, `{username}`: o @
  da página pública de terceiro, sem a arroba. Nunca a conta da persona;
- três etapas, todas ações do catálogo do Instagram e sem efeito:
  1. `abrir_perfil`: `OPEN_PROFILE`, com `username = {username}`;
  2. `abrir_primeira_publicacao`: `OPEN_POST`, com `target` = "a primeira publicação da grade" e
     `post_author = {username}` (a regra de uma conta por alvo, ADR-055, precisa do autor);
  3. `abrir_comentarios`: `OPEN_COMMENTS`.
- `test_ensino_alvo_de_terceiro.py` confere que ela só lê, que o único parâmetro é `{username}`, que cada etapa monta
  pelo catálogo e que a prévia a aceita sem o aviso de conta própria (31.182).

**O roteiro, com a conta de teste do dono** (cada passo com conta real pede o sim dele na hora):
1. Antes: a conta logada no aparelho e verificada (`session_ready`), e uma página pública de terceiro escolhida pelo
   dono. Fora da janela de medida do notebook. Nada de comentar, curtir, seguir nem mandar mensagem: o fluxo só lê.
2. Abrir uma sessão de ensino no aparelho, com o app Instagram, e demonstrar à mão: tocar a busca, digitar o @ da
   página, abrir o perfil, tocar a primeira publicação da grade e tocar o balão de comentários. Parar.
3. Pedir a proposta à IA: é uma chamada paga, uma por sessão.
4. Na prévia, comparar com a referência:
   - o comando com `{username}`;
   - as três etapas com as ações do catálogo;
   - nenhum aviso de conta própria;
   - nenhuma etapa com efeito;
   - o @ digitado virou parâmetro, não literal.
   Se a proposta divergir, corrigir na prévia antes de salvar. A IA atribui as entradas gravadas às etapas; descartar
   só o que não é de etapa nenhuma.
5. Salvar, com o escopo da persona que ensinou. Pela regra do 30.81, as receitas valem só para ela até o "Confirmar
   que fica" ou a prova.
6. Medir: 2 ou 3 execuções só de leitura com outro @ público. Depois `GET /api/aprendizado/receitas/{id}/rendimento`
   (31.191) e `GET /api/aprendizado/alcance?app=instagram` (31.181).
7. Parar na tela de bloqueio, no `auth_challenge` e na conta errada, como no plano de ensino guiado (31.201).

**Na operação (onda 2), o fluxo não substitui o plano.** A escolha por semelhança (31.151, que executa direto pelo
31.210) troca o plano INTEIRO pelo do fluxo e só confere se `{username}` está no comando (`habilidades.escolha_valida`).
Não confere se o fluxo cobre o comando todo. Este fluxo só lê; a ação final da operação (o comentário com aprovação)
ficaria de fora (a guarda é o 31.222). Sem ensino feito antes da onda, o planejador vai livre nos alvos.

Correção (31.221): as etapas ensinadas do 31.153 também NÃO servem à operação. A operação planeja com ações do catálogo
(`OPEN_PROFILE`, `OPEN_POST`, `OPEN_COMMENTS`), e o 31.153 exclui de propósito a etapa com ação do catálogo. O que
reaproveita a etapa do catálogo é a receita pela identidade da etapa, e o caminho para ensiná-la a partir de uma
execução que deu certo é o da seção seguinte.

## O ensino a partir da execução (31.221)

P-014, para a execução que deu CERTO. Medido em 07/10 na onda 1, lido em `mode=ro`:
- a operação do Instagram planeja com ações do catálogo;
- `open_post` e `open_comments` já rodaram sem IA (`driven_by=recipe`), pelas receitas que a IA aprendeu em execuções
  anteriores;
- só `open_profile` e o comentário foram pela IA. No `open_profile`, a IA começou por voltar (`press_back`), que a
  receita não reproduz, e nenhuma receita nasceu.

A receita que a IA aprende numa execução real nasce candidata e só vira ativa depois de `ai.recipes_promote_after`
execuções que concordem (2 no central; fica assim nesta prova). Agora a pessoa olha a execução que deu certo e promove
num gesto as candidatas das etapas de leitura dela, sem tempo de aparelho:

- `GET /api/aprendizado/execucao/{run_id}/ensino` (adendo v1.120): por etapa, a candidata ou o motivo fechado
  (`domain/ensino_da_execucao.Motivo`):
  - `execucao_simulada`: a simulada não publica;
  - `com_efeito`: efeito externo ou trava de commit, que seguem pela aprovação;
  - `nao_concluida`;
  - `ja_por_receita`;
  - `sem_ator`;
  - `caminho_nao_reproduzivel`, com `ferramentas_nao_reproduziveis` (`press_back`, `press_home`, `drag`,
    `type_secret`, `open_url`, a lista do executor);
  - `sem_receita`: a IA conduziu e a loja não gravou, por outro motivo;
  - `receita_ja_vale`;
  - `receita_fora_de_circulacao`.
- `POST` na mesma rota: a pessoa promove as candidatas pelo caminho do Livro (candidate → validated → published, com a
  trilha), com o motivo `ensino_da_execucao:<run> persona:<id>`. O que o Livro recusar volta em `recusadas`; o resto
  segue.

**Escopo.** A receita não tem escopo por persona, então a persona que executou fica na trilha como proveniência, não
como trava: a receita promovida vale para o app, como as que já rodam no catálogo. Uma trava por persona pediria
migração.

**Achado para a onda 2.** Se a IA começar o `open_profile` por voltar, de novo não nasce candidata, e o GET mostra
`caminho_nao_reproduzivel` com `press_back`. O primeiro gesto do ator numa tela já certa não deveria ser voltar; isso
fica para o 31.223 ou para um item próprio.

**Prova.** `simulated`: `backend/tests/test_ensino_da_execucao.py`. `real`: a segunda execução com
`open_profile` `driven_by=recipe` depois do POST sobre uma execução real da onda 2; `not_run` até o deploy e a onda.

## A semelhança não derruba a ação final (31.222)

Achado do 31.219. A escolha por semelhança (31.151, que executa direto pelo 31.210) troca o plano INTEIRO pelo do
fluxo e só confere se os valores estão no comando. Um fluxo de leitura escolhido para o comando de uma operação que
também comenta deixaria o comentário de fora.

Agora, antes de trocar, o código compara o plano livre (o planejador o produz junto da escolha) com o do fluxo
(`habilidades.acoes_finais_fora`). Toda etapa do plano livre com efeito externo ou trava de commit tem de estar no
fluxo, pela ação do catálogo ou, sem ela, pela chave. Faltando uma, a escolha é recusada e fica o plano livre, com a
ação final. A trilha diz "recusada (31.222): o fluxo não cobre a ação final <ação>". `runs.flow_id` e o uso do fluxo
não andam. Sem ação final no plano livre, nada muda.

**Prova.** `simulated`: `backend/tests/test_semelhanca_sem_acao_final.py` (3). `real`: `not_run`; aparece na primeira
operação com fluxo parecido depois do deploy.

## O modelo forte só no commit (31.223)

Custo por alvo, medido na onda 1 (07/10, lido em `mode=ro`, custo por `planning.costs`):
- as leituras já decidiam no Sonnet;
- o Opus decidia os 2 passos da etapa de comentário: US$ 0,081 com imagem e 0,031, ou 0,112 de 0,279 do alvo;
- a causa é que `strong_model_for_side_effect=by_risk` sobe a etapa INTEIRA.

Com `ai.strong_model_only_on_commit` (padrão `true`, adendo v1.122), a etapa com efeito começa no modelo de ação, que
abre o campo e digita. A primeira decisão que dispararia o efeito (`is_commit_action`, o seletor ou o verbo) é
descartada antes de agir e refeita no forte, que segue até o fim da tentativa, como no LT-12 da nova tentativa. A
refeita não é a primeira decisão, então vai sem imagem quando a árvore basta.

Não mudam: a trava de commit, a política de risco por app e o rejulgamento do "sim" com efeito.

**Estimativa.** A etapa de comentário cai de cerca de 0,112 para cerca de 0,084: Sonnet com imagem 0,040, Sonnet 0,013 (o
commit descartado) e Opus 0,031. O alvo executado vai de cerca de 0,28 para cerca de 0,25.

**Registro.** Sem campo novo; é o que a Jev lê no 31.229. A decisão descartada é `decide` tier 0 sem ação ligada
(`actions.ai_call_id`). A refeita é tier 1 com `escalate=efeito`, e a ação dela tem `side_effect`.

**Prova.** `simulated`: `backend/tests/test_forte_so_no_commit.py` (2). A navegação fica no tier 0; o commit do tier 0
não age e é refeito no tier 1; desligado, a etapa inteira vai ao tier 1. `real`: `not_run`; o custo por alvo da
primeira operação depois do deploy 61, contra a onda 2.

## A receita ativa que diverge ensina a candidata (31.233)

Achado da onda 2 (07/10, leitura real, só leitura): a receita 111 (`open_post`, a 1ª publicação da grade, aprendida no
perfil nosso) rolou no perfil de terceiro, não achou o alvo, e a IA terminou a etapa nos 3 alvos (US$ 0,1415). Na 3ª
divergência seguida, ela foi à quarentena. O executor só aprendia de uma divergência quando a receita era CANDIDATA,
então o caminho pago se perdia e a próxima operação pagava de novo.

Agora (`StepExecutor._after_step`, `ai.candidata_da_ativa_que_divergiu`, padrão `true`):
- **Quando nasce.** A receita ATIVA divergiu, caiu em quarentena NESTA tentativa e a IA completou a etapa comprovada.
  Antes da quarentena, a ativa segura a chave (uma só receita viva por chave) e nada nasce.
- **O que vira receita.** O caminho que rodou: as ações da receita feitas (`done`) e as da IA, destiladas juntas
  (`distill(com_trecho_da_receita=True)`). O gesto da receita que não chegou ao aparelho (`rejected`) fica fora;
  qualquer outro estado recusa, como antes. As regras de seletor, segredo e efeito não mudam.
- **Como fica.** É candidata, em prova até `recipes_promote_after` execuções seguidas, como as outras; a trilha diz "a
  partir da vN ativa, que divergiu e foi à quarentena (31.233)". A quarentenada vira `superseded`.

**Prova.** `simulated`: `backend/tests/test_candidata_da_ativa_que_divergiu.py` (5). `real`: `not_run`; a 1ª receita
ativa que divergir até a quarentena depois do deploy.

## A receita só reproduz no escopo do alvo em que nasceu (31.249)

Medido em 07/10 (só leitura): a receita 111 (`open_post_1`) foi aprendida em 03/10 num post da PRÓPRIA conta da
persona. As etapas `open_post_1` da onda 2 e da rodada iam a post de terceiro, com o MESMO `template_hash`, porque a
etapa não cita o autor na pós-condição. Na onda 2 a 111 divergiu nos 3 alvos e foi à quarentena: US$ 0,1415, 39 % da
onda.

Só uma receita viva cabe por chave. Se o escopo entrasse na identidade, as receitas de um dos lados ficariam órfãs, e a
identidade é calculada em vários lugares. Por isso ele entra na CONSULTA (`RecipeStore.find(..., escopo=)`):
- **Regra** (`recipes.escopo_do_alvo`): a conta-alvo da etapa (`post_author` ou `username`) pode ser uma conta da
  persona, sem caixa nem arroba, ou o marcador dela (31.113 F3); nesse caso o escopo é `proprio`. Outra conta dá
  `terceiro`. Sem conta-alvo, `None`.
- **Receita**: o escopo sai da etapa em que ela foi aprendida (`learned_from_step`, com as contas da persona daquela
  execução em `profile_accounts`) e fica em memória (não muda). Sem a etapa, ou vinda do treino, `None`.
- **Consulta**: o executor passa o escopo da etapa da vez, pelas contas da persona e o `account_label`. A receita de
  outro escopo não reproduz: `receita.consulta{resultado=outro_escopo}`, e a IA decide a etapa, sem herança nem chave
  genérica. Com `None` de um dos lados, a consulta é a de sempre.

No dado real de 07/10, a regra dá: 111 `proprio`; 222 e 223 (`open_profile` de terceiro) `terceiro`; 91
(`open_comments`, sem conta-alvo) `None`.

Limite: a chave segue com uma receita viva só. O escopo que perder a vaga vai à IA, e não diverge pagando.

**Prova.** `simulated`: `backend/tests/test_escopo_da_receita.py` (5: a regra; o escopo da etapa aprendida; a consulta
no mesmo escopo e no outro, com a métrica; sem escopo conhecido vale nos dois; o executor passa o escopo). `real`:
`not_run`. A prova é a 1ª operação em post de terceiro depois do deploy, com `outro_escopo` no lugar da divergência da
111.

## O roteiro de prova real dos deploys 60 e 61 (31.268)

`scripts/prova-real-aprendizado.py --operacao OP` fecha as provas reais dos itens do aprendizado em minutos depois da
operação (tabela em `docs/operacao.md`). Por item, primeiro o commit (o do `feat` dele estava no central quando a
operação começou?), depois a leitura com o achado:

| Item | Evidência esperada |
|---|---|
| 31.231 | log "pesquisa reaproveitada do Livro" (sem chamada paga) |
| 31.232 | decisão de commit no forte (`decide`, `escalate=efeito`, tier ≥ 1) sem imagem, salvo o alvo fora da árvore |
| 31.236 (Jev) | operação com parâmetro fixo sem execução em `needs_input` |
| 31.237 | o 1º plano grava o cache e os irmãos leem (`cache_read > 0`) |
| 31.238 | evento `rejulgamento_dispensado` |
| 31.239 | decision "comentário comprovado pela árvore local" |
| 31.242 / 31.243 | notas; eventos, ações e `status_detail` sem o usuário da conta da persona (contagem, nunca o valor) |
| 31.244 | receita aprendida na operação com o marcador da persona e sem o valor |
| 31.248 | operação sem assunto com "pesquisa com o assunto da leitura do alvo" |
| 31.249 | etapa com receita de outro escopo na mesma chave conduzida pela IA, nunca pela receita |
| 31.250 | decision "pelo marcador do catálogo" na etapa livre com nível |
| 31.262 | evento `receita_nao_aplicavel` com `em_prova` |

Só o `presente` vira `real` em `resultados`. O `divergente` é prova real de defeito e fica em `divergencias`, para
quem corrige. `ausente`, `sem_caso` e `nao_no_ar` são `not_run` e nunca entram no `aplicar`.

Ensaio na rodada de 07/10 (`op-20261007125539-22ef67`, central `8552b160`, deploy 59): os 13 itens saem `nao_no_ar`.
Com `--commit` forçado na ponta da integ-62, só como diagnóstico das leituras, a onda 2 (`op-20261007100755-096a28`)
mostra o defeito de antes: 31.249 divergente nas 3 etapas `open_post_1` (a 111 de outro escopo), 31.242 e 31.243
divergentes (usuário sem máscara), 31.232 divergente (imagem no commit do forte). A rodada mostra 31.237 divergente
(os 2 planos irmãos frios).

**Prova.** `simulated`: `scripts/tests/test_prova_real_aprendizado.py` (6). `real`: o ensaio acima.

## A candidata que não se aplica na partida segue em prova (31.262)

Medido em 07/10 (só leitura): a receita 222 (`open_profile` de perfil de terceiro, chave genérica) não reproduziu na
rodada das 12:55, e não podia: era candidata, nascida às 10:08 na onda 2, com 0 concordâncias, e candidata não
reproduz (são 2 seguidas para promover). A onda 2 também foi conduzida pela IA (9 decisões, 3 por aparelho). A rodada
pagou 16 decisões e 1 julgamento: os 3 aparelhos começaram com a folha de comentários que a operação anterior deixou
aberta (2 a 5 decisões para voltar ao perfil), e o replanejamento do android-06 refez a etapa (4 decisões).

O defeito do aprendizado estava na sombra. A divergência por tela de partida diferente zerava a prova e trocava a
candidata pelo caminho da IA: a 222 virou a 223 (o mesmo caminho com um `open_app` na frente) numa etapa que começou
fora do app. A chave passou por 118, 166, 222 e 223 sem nunca ficar ativa. Agora, como o 30.80 na reprodução:
- o alvo da AÇÃO 1 ausente na tela de partida (`AlvoAusente` antes de comparar qualquer ação) encerra a comparação, sem
  veredito (`_RecipeRun.partida_diferente`);
- com a etapa comprovada, a candidata segue em prova (a sequência não zera), o caminho da IA não a substitui, e a série
  `nao_aplicavel_seguidas` sobe (`RecipeStore.nao_aplicavel_em_prova`, métrica `receita.sombra`); o evento é
  `receita_nao_aplicavel` com `em_prova`;
- a concordância e a divergência zeram a série; a 3ª seguida conta como divergência, como antes.

A folha que ficou aberta entre operações não é do aprendizado: é do preparo da etapa (quem devolve o app ao estado
conhecido antes do alvo).

**Prova.** `simulated`: `backend/tests/test_sombra_partida_diferente.py` (3). `real`: o diagnóstico acima (leitura do
central). A correção é `not_run` até a próxima etapa com candidata em prova que comece fora do estado dela.

## A receita sem o "voltar" inicial (31.230)

Achado da onda 1 (07/10): a IA começou o `open_profile` por voltar (`press_back`). Como o voltar depende da tela de
quem aprendeu, o destilador recusava a tentativa inteira, e nenhuma receita nascia. Na onda 2, sem isto, o POST do
31.221 podia não ter candidata para promover.

Agora:
- **Anotação.** Para cada ação da IA, o executor anota se a tela de onde ela partiu era o estado conhecido DECLARADO do
  app (`conhecimento/apps/<pacote>/telas.yaml`, `estado_conhecido.telas`; no Instagram, `feed` e `profile`). Só a
  anotação fica em memória, nunca a tela. As telas aprendidas ficam de fora.
- **Destilação.** O prefixo de `press_back` antes da 1ª ação gravada é descartado quando essa ação partiu do estado
  conhecido. Ela leva a marca `ancora: estado_conhecido`. Fora do estado conhecido, sem conhecimento do app ou com
  `press_back` no meio do caminho, a tentativa segue recusada como antes.
- **Reprodução.** Antes da 1ª ação, a receita ancorada confere a tela. Fora do estado conhecido, ou sem quem confira,
  é "alvo ausente": não se aplicou (30.80), e a IA assume. Nunca reproduz às cegas a partir de uma tela errada.

A receita nascida assim é candidata como qualquer outra. A sombra a prova, ou a pessoa a promove pelo 31.221.

**Prova.** `simulated`: `backend/tests/test_receita_sem_voltar_inicial.py` (6, com o `telas.yaml` real do Instagram).
`real`: `not_run`; a 1ª operação depois do deploy 61 com `open_profile` que comece por voltar.

## A pesquisa reaproveita o Livro (31.231)

Achado da onda 1 (07/10): a pesquisa da operação (31.158) custou US$ 0,043 no alvo. Os fatos confirmados que ela deixa
viram itens do Livro com o assunto canônico no escopo (31.190, 31.200), mas a 2ª operação do MESMO assunto pagava de
novo pelo que o Livro já sabia.

Agora, depois da lacuna e antes do gasto, a pesquisa consulta o Livro:
- **Leitura** (`learning/infrastructure/fatos_do_livro_sql.py`, lado do Aprendizado): pelo id da operação, os itens
  `operation_fact` do assunto canônico E do pacote do app dela (`scope_app`; o mesmo assunto em outro app não cobre),
  vivos (`candidate`, `validated`, `published`), com o texto do conteúdo e o frescor e os domínios da proveniência
  v1.117. O rejeitado e o desligado ficam fora. O `candidate` conta porque o minerador do 31.190 só faz nascer item de
  descoberta confirmada.
- **Critério** (`learning/domain/reaproveitamento_da_pesquisa.py`, puro): cobre o pedido com pelo menos
  `ai.pesquisa.reaproveitar_min_fatos` (padrão 2) fatos vivos e dentro do frescor (sem frescor não conta; só o fato
  confirmado nasce no Livro). Havendo fontes indicadas, cada domínio indicado tem de estar entre os desses fatos. `0`
  desliga.
- **Registro** (`pedidos/infrastructure/pesquisa_da_operacao.py`, lado da Jev): cobrindo, os fatos entram na memória
  da operação como `livro.<item>` (descoberta, confirmada, origem `pesquisa`, com o frescor do Livro), e a
  `pesquisa.estado` diz "reaproveitado do Livro (31.231)" com os itens, o menor frescor e o critério por extenso. Nenhuma
  chamada de IA; o log da operação diz "pesquisa reaproveitada do Livro". Não cobrindo, ou se a leitura falha, a
  pesquisa paga roda como antes, com o motivo no log. Nunca é um pulo silencioso.

A chave `livro.` não volta ao Livro: o minerador do 31.190 só lê `pesquisa.*`.

**Prova.** `simulated`: `backend/tests/test_pesquisa_reaproveita_o_livro.py` (4: critério, serviço que não paga e
registra, serviço que paga quando não cobre ou quando a leitura falha, leitor do Livro no harness). `real`: `not_run`;
a 1ª operação de assunto repetido depois do deploy. Contrato com a Jev aceito em 07/10 com dois pontos (filtro por app;
o `candidate` só por ter vindo confirmado), os dois aplicados.

## O assunto da operação nasce da leitura do alvo (31.248)

Medido na onda 2 (07/10): a operação não tinha `assunto` nem `fontes`. `pesquisar_se_preciso` voltava `None` sem
assunto, e a lacuna (o critério 5 do dono, que existe desde o 31.158) nem era consultada. A pesquisa (critério 6) e o
reaproveitamento do 31.231 não tinham onde agir.

Agora, sem assunto guardado (`ai.pesquisa.assunto_da_leitura`, padrão `true`), o assunto vem da leitura do alvo:
- **Regra** (`learning/domain/reaproveitamento_da_pesquisa.assunto_da_leitura`, pura): o recorte PÚBLICO da
  publicação (`alvo.conteudo`, gravado pelo primeiro agente que a leu, com frescor de 6 h), em uma linha, com três
  limpezas:
  - sem menção a conta, porque o nome de um terceiro não vai à busca externa;
  - sem endereço;
  - a hashtag vira palavra.

  O resultado é cortado na palavra (160). Com menos de 3 palavras, não há assunto.
- **Quando**: a pesquisa da criação (31.169) não acha assunto nem leitura e não roda. Ela também não deixa marca, então
  a lacuna fica aberta. A 1ª leitura de uma operação sem assunto, na porta de escrita, agenda a pesquisa com a mesma
  trava: o 1º agente escreve sem os fatos, e os seguintes já os leem. Dois alvos pagam uma vez.
- **Livro**: o 31.231 é consultado com esse assunto antes de pagar (`fatos_do_livro(operacao, assunto)`).
- **Minerador**: o fato que a operação deixa leva o MESMO assunto ao Livro (a mesma regra sobre a mesma leitura, em
  `fatos_da_operacao_sql._operacoes`), para a próxima operação da mesma publicação o reusar. A coluna
  `operacoes.assunto` (da Jev) não é escrita.

O log diz "pesquisa com o assunto da leitura do alvo".

Limites:
- o assunto é o texto da publicação, sem resumo por IA;
- duas publicações diferentes dão assuntos diferentes, e o Livro só cobre a mesma publicação (ou o mesmo texto).

**Prova.** `simulated`: `backend/tests/test_assunto_da_leitura_do_alvo.py` (6: a regra; o serviço sem assunto, sem
marca antes da leitura e com o Livro consultado pelo assunto da leitura; o assunto guardado vence e o desligado é o de
antes; o Livro cobrindo não paga; o minerador; a porta com dois alvos e uma pesquisa). `real`: `not_run`. A 1ª
operação sem assunto depois do deploy faz uma chamada paga, até o teto de US$ 0,25 por operação; ela pede o sim do
dono para a validação.

## A exploração e o que ela deixa (31.273, ADR-084)

O pedido que nenhuma ação do catálogo do app cobre deixou de ser recusa. O planejador com catálogo segue devolvendo
`fora_do_catalogo` (app e pedido, no infinitivo); o serviço de planejamento (`_explorar_ou_recusar`) o transforma em UMA
etapa livre de exploração por pedido, com `PlanStep.exploratoria` (coluna `steps.exploratoria`, migração 130).

- **Decisão (`planning/exploracao.py`, puro).** Verbo de leitura ou navegação explora; verbo que não está em nenhuma lista
  explora também, com a ordem escrita no objetivo de não mudar nada; verbo de EFEITO (lista fechada) segue recusado, porque
  efeito sem ação do catálogo passaria por fora da política do perfil (porta do 13.2). Também recusam: app desconhecido,
  `limits.exploracao_ligada: false` e o teto do dia por app (`exploracao_max_por_dia`), que conta só a exploração NOVA
  (com IA; o evento `exploracao.iniciada` leva em `app_ids` o app do pedido sem receita descoberta). A recusa por teto diz o
  motivo (`plan.refused` com `teto_de_exploracao_por_dia`, que o Telegram também conhece).
- **Tetos (ao vivo, `limits.exploracao_*`).** 25 ações do executor na etapa; 30 chamadas de IA e US$ 0,60 (tokens x
  preço, planejamento incluído) por execução, conferidos em `Executor._ai` antes de cada chamada; 5 explorações por dia por
  app. Estourar não é falha calada: a etapa fecha como `budget` com a frase do que foi gasto.
- **A chave é vocabulário.** `explorar_<verbo>_<objeto…>` só com palavras das listas do módulo: a mesma exploração, pedida de
  outro jeito, dá a mesma chave. Sem objeto reconhecido a chave leva um sufixo de letras do hash do pedido e nunca é
  oferecida. Quem amplia o vocabulário é pessoa (arquivo versionado), nunca o modelo.
- **Achar de novo (`FlowStore.etapas_descobertas`).** Receita `active` cuja etapa de origem é exploratória, sem efeito, com o
  molde refeito só com a chave (`molde_da_exploracao`): o título e o objetivo da execução levam o pedido (que pode ter um
  nome) e nunca saem dela. Piso `ai.descobertas_sem_uso_dias` (90). A versão do app já está na chave da receita: versão nova
  não a acha e a exploração roda de novo. Dois caminhos usam o molde: o bloco `<etapas_descobertas>` do planejador livre
  (apps sem catálogo) e a própria exploração (a segunda vez do mesmo pedido, em app com catálogo), que reaproveita o molde e
  roda por receita, sem IA. O `value` da pós-condição da etapa e o do molde são o mesmo (a frase da chave; o pedido vai só
  na `description`, que o hash não lê), então a receita aprendida na exploração é achada pelo molde. Provado: o hash da etapa
  e o do molde coincidem, e (31.311) o ciclo inteiro pelo executor: a 1ª execução aprende a receita `candidate`, a 2ª a promove a
  `active` pela prova sombra (outro texto de pedido, mesma chave, mesmo hash), o leitor passa a oferecer a etapa pelo nome e a 3ª, com o
  molde, é conduzida por receita (`driven_by = recipe`, 0 decisões da IA na etapa). `simulated`, no aparelho falso do QA Messenger
  (`tests/test_replay_da_etapa_descoberta.py`). **Não provado**: o mesmo no Outlook real (`not_run`, pede o sim do dono, P-043).
- **Degrau do catálogo.** A etapa exploratória já cai em `acoes_livres` (sem ação do catálogo). O limiar é 2 execuções reais
  comprovadas (`ACAO_EXECUCOES_EXPLORADA`), a regra do Livro, e o fragmento sai preenchido com a chave, a prova por tela e
  `side_effect: false`; política, risco e tela de partida ficam `A_DEFINIR`. Só chave e contagens vão ao texto.
- **Medida.** `saude.exploracoes.por_conducao` e `pct_sem_ia`: das explorações que terminaram, quantas entraram por receita
  ou atalho (0 chamadas de IA). É a medida de que o sistema aprende o que descobre.
- **Prova.** `simulated`: `tests/test_exploracao_fora_do_catalogo.py`, `test_etapas_descobertas.py`, `test_migracao_130.py`,
  `test_learning_backlog.py`. `real`: `not_run` (exploração real no Outlook: gasta API, pede o sim do dono). O pedido misto (31.298) mantém a parte do catálogo: o prompt pede as ações do catálogo em `steps` E o resto
  em `fora_do_catalogo`, e a exploração entra depois da última etapa do catálogo; o aviso no Telegram ao começar e ao concluir
  está em [Canais](canais.md). Falta o selo/campo `exploracao` no painel (Portal, 31.299), e o prompt novo não foi validado
  com o modelo real (API paga, `not_run`).

## A exploração que parou vira pedido de ensino (31.312)

- A etapa exploratória (31.273) que termina `failed`/`uncertain` ou `parou_no_teto` já abria o "Ensinar a corrigir" (31.111). Agora o
  ensino sabe que veio de exploração: a intenção sugerida é «Ensinar à IA como fazer: <frase da chave>» (`ensino_da_falha.intencao_da_exploracao`,
  vocabulário fechado, nunca o pedido), a pergunta é a da exploração e a sessão carrega `origin.exploracao`. A pessoa mostra o caminho
  uma vez e o que ela ensina vale para todas as personas (o ensino entra no Livro como qualquer outro; que a etapa ensinada case com a
  chave `explorar_…` e seja oferecida pelo nome **não foi verificado**: `not_run`). Contrato: [v1.138](../api-contract.md#adendo-v1138-10102026-número-da-orquestradora-item-31312--a-exploração-que-parou-vira-pedido-de-ensino).
- **Limite conhecido:** a receita `candidate` que a própria exploração deixou, se houver, não é retirada pelo ensino; o ensino da pessoa nasce
  ativo e a substitui pela regra de sempre (`test_ensino_da_pessoa_segue_nascendo_ativa_e_substitui_a_candidata`). O botão no painel é do Portal.

## A exploração de EFEITO pela política do perfil (31.297, ADR-091)

- **O que muda:** com `limits.exploracao_efeito_ligada` (desligado de fábrica) o pedido de efeito não é mais recusado: vira uma etapa
  exploratória `side_effect` (`planning/exploracao.py`: `passo_da_exploracao` ramo de efeito), cuja ordem manda fazer SÓ o pedido e não digitar
  senha (ADR-040). A chave é `explorar_<verbo canônico>_<objeto…>` (`_EFEITO_CANONICO`: sinônimos num verbo só) e é o que a política casa.
- **A porta:** `gates.vereditos_da_porta` troca a falta de ação do catálogo pela `capability_da_exploracao` (sintética, risco alto, padrão
  `approval_required`) quando a etapa é exploração de efeito montada pelo sistema (`e_exploracao_de_efeito`: marca `exploratoria`, `side_effect`
  e prefixo `explorar_`). Daí em diante é o caminho de sempre: `PolicyEngine.check` (chave do pedido antes da genérica `explorar_efeito`, perfil
  antes do grupo), aprovação, teto `preparar`; sem perfil não passa. Ao liberar a porta emite `exploracao.efeito_liberado`.
- **Nunca explora:** `e_credencial` (lista única `FORMAS_DE_CREDENCIAL` em `app/contracts/credencial_e_sessao.py`; `Exploracao.de_credencial`) casa por CONJUNTO de formas (entrar/entre, logar/login/logout, sair/saia, autenticar, cadastrar/cadastre, registrar, inscrever, conectar/desconectar, senha, password, credencial, token, 2FA/MFA/OTP; "entre" só como imperativo antes de conta/app/perfil/senha/e-mail e "código" só com contexto de verificação/acesso/SMS/e-mail) em qualquer posição, e "criar/adicionar/abrir/trocar/alternar/mudar" logo antes de "conta"; vale também para o pedido que a classificação de efeito não pegaria ("fazer login", "redefinir a senha"). Seguem recusados com o interruptor ligado OU desligado (ADR-040, ADR-087; muda a recusa do 31.273, que antes deixava "fazer login" explorar em leitura) e não têm chave de política. "escolha entre as fotos", "aplicar o código do cupom" e "ler o código de barras" seguem exploráveis (casam por contexto, não sozinhos). "Caixa de entrada" e "configurações da conta" continuam leitura.
- **Fora da sintética:** sem balde de limite nem contraparte, então limite diário, frota (ADR-083) e 30.62 não se aplicam; é do dono liberar. A chave do efeito leva palavras inteiras (sem cortar objeto no meio).
- **O que NÃO muda:** a leitura exploratória segue livre; o efeito livre que o modelo escreve sem a marca do sistema segue recusado pela 13.2; a
  receita de efeito descoberta não é oferecida a outras execuções (`molde_da_exploracao` devolve `None`); o executor mantém o caminho livre com
  efeito (guarda do commit, não repetir, comprovar) e os tetos `exploracao_*`.
- **Prova:** `simulated`, `backend/tests/test_exploracao_de_efeito.py` (99 casos: chave canônica, validação das chaves, política em dois níveis,
  porta com aprovação/recusa/autonomia/grupo, planejamento ligado e desligado, aviso). `real`: `not_run`. Contrato: adendo v1.140 do
  [api-contract](../api-contract.md).
- **Limites conhecidos:** o aviso `exploracao.efeito_liberado` sai a cada passagem da porta pela mesma etapa (a chave de dedup colapsa no Telegram, mas `events` pode ganhar linhas repetidas); a receita candidata que a execução de efeito deixa, achada por `step_hash` em outra execução, não foi testada com `driven_by=recipe` (a porta roda antes do executor, então a política julga do mesmo jeito); o executor rodando a etapa de efeito contra aparelho real não foi exercido (só a porta e o planejamento); o painel (Portal)
  ainda não tem linha para `explorar_efeito` em Política (aparece na resposta do `GET` só quando escolhida); a pergunta da aprovação mostra o
  título da ação sintética, sem o pedido (que pode ter nome).

## O padrão da exploração de efeito é por verbo (31.325, ADR-091)

- **Ordem das chaves** (`planning/exploracao.chaves_da_politica`): `explorar_<verbo>_<objeto…>` (pedido), `explorar_<verbo>` (verbo) e `explorar_efeito`
  (genérica, só para o verbo comum). Perfil antes do grupo dentro de cada nível de busca (`PolicyEngine._escolha`).
- **Padrão** (`capability_da_exploracao`): destrutivo (`contracts/efeito_destrutivo.VERBOS_DESTRUTIVOS`: apagar, comprar, transferir, encerrar,
  desinstalar, resetar; sinônimos pelo verbo canônico) = `approval_required` e risco alto; o resto = `autonomous`, risco médio. A genérica não libera o
  destrutivo: liberar tudo de uma vez nunca libera apagar nem comprar.
- **Para ligar o P-046:** interruptor + `explorar_efeito: autonomous` (só alcança os comuns, que já rodam por padrão; vale para o dono poder apertar para
  `approval_required` num só passo). Os destrutivos só se liberam por `explorar_<verbo>` ou pelo pedido.
- **Prova:** `simulated`, `backend/tests/test_exploracao_politica_por_verbo.py`. `real`: `not_run`. Contrato: adendo v1.141 do [api-contract](../api-contract.md).

## A receita da exploração parte do estado conhecido e a sombra compara pelo alvo (31.327)

- **Achado da prova real do P-043 (10/10, Outlook, `android-01`):** a receita de "abrir a pasta de lixo eletrônico" nasceu só com "tocar em Junk" (a partida era
  onde o app estava: a gaveta aberta) e respondeu `nao_aplicavel` em qualquer outra tela; com a gaveta aberta, a IA tocou no "Junk" certo e a sombra contou "a IA
  escolheu outra ação".
- **Partida:** `StepExecutor._partir_do_estado_conhecido` leva o app ao `estado_conhecido` do `telas.yaml` (`voltar_ao_estado_conhecido`: só "voltar" e, no máximo uma vez,
  reabrir; sem efeito externo) antes da 1ª decisão de toda etapa EXPLORATÓRIA, na IA e na reprodução. `distill(exploratoria=True)`: em app com estado conhecido, a receita só
  nasce se a 1ª ação partiu dele, e leva a âncora `estado_conhecido` (a reprodução só age na caixa de entrada; fora dela é "não se aplicou"); partida desconhecida não vira
  receita. App sem estado conhecido declarado: como antes.
- **Comparação:** `recipes.mesmo_alvo` (o menor clicável no centro de cada elemento é o mesmo) substitui a igualdade de `element_id` em `_shadow_compare`; o rótulo da receita
  (filho sem clique) e a linha clicável da IA são o mesmo alvo. O motivo da divergência traz os dois (`a IA escolheu outra ação (IA: tap e73 <classe> [limites]; receita: ...)`),
  sem o texto da tela; a classe do retorno (`receita.retorno_ia`) não muda.
- **Prova:** `simulated`, `backend/tests/test_receita_da_exploracao_parte_do_estado_conhecido.py` (12). `real`: `not_run` (o replay por receita no Outlook real segue por provar).

## O teto da exploração conta só a exploração (31.331)

- **Achados da prova real do P-046 (10/10, 2 execuções do mesmo pedido de efeito no Outlook):** US$ 0,1075 contra o teto 0,10 e US$ 0,2615 contra 0,25 (a checagem era
  `gasto >= teto` antes de cada chamada: a que cruza o teto passa); o planejamento (US$ 0,038, sempre antes) comia o teto; a etapa exploratória falhou pelo orçamento
  genérico da etapa livre (normal 3 a 8 chamadas, para em 16) antes de achar a linha; o aviso `efeito_liberado` saía a cada versão do plano.
- **Regra:** `Executor._teto_da_exploracao` soma só as chamadas das etapas exploratórias (`costs.spent_usd(..., so_exploratorias=True)`) e para quando gasto + média por
  chamada já feita não cabe no teto (a 1ª chamada não tem média e passa). `_ai` não aplica `_conferir_orcamento_da_etapa` à etapa exploratória: ela tem o teto próprio
  (chamadas, US$ e ações). `gates._avisar_exploracao_de_efeito` avisa uma vez por execução, instância e chave (a recuperação do plano refaz a etapa e a porta passa de novo).
- **Prova:** `simulated`, `backend/tests/test_teto_da_exploracao_31_331.py`. `real`: `not_run` (a 3ª prova do P-046 precisa de mensagem não lida e do sim do dono).

## A partida da exploração, a pasta que não é a caixa e a linha pelo remetente (31.338, 31.339, 31.340)

Achados da prova real do 11/10/2026 (deploy 74, Outlook do `android-01`): o replay por receita foi provado (receita 228: candidata, ativa por concordância, reproduzida com 0
decisões de IA), e a 3ª prova do P-046 não achou a linha de Bruno Ferreira. Três defeitos, três itens, todos `simulated` (`real`: `not_run`):

- **31.338, esperar a tela depois do reabrir.** Com o app de frio, `voltar_ao_estado_conhecido` dava `[reabrir, voltar]`: a tela de abertura ainda era desconhecida e o "voltar"
  tirou o Outlook da frente (a IA começou pela tela inicial do Android e a destilação recusou a partida desconhecida, 31.327). Agora, depois de cada reabrir, a tela é relida
  (`leituras_apos_reabrir`=3, `espera_apos_reabrir_s`=1,5; só leitura) enquanto o app está na frente e a tela é desconhecida. O resto não muda (voltar até `voltar_max`, nada de
  voltar em login/desafio). Também vale para o motor de sessão, que usa a mesma função. Teste: `test_voltar_ao_estado_conhecido_espera_31_338.py`.
- **31.339, a Junk não é a caixa de entrada.** A lista é o mesmo `conversation_list` em toda pasta, então a Junk contava como `caixa_de_entrada`: o preparo não saía dela (a IA via a
  pasta aberta e nada era ensinado) e a receita ancorada partia dela e divergia no 2º toque. O `telas.yaml` do Outlook ganha o sinal `pasta_de_email` (título da barra: Junk, Sent,
  Drafts, Archive, Deleted, Outbox e os nomes em português) e a tela `pasta_de_email`, que NÃO é estado conhecido: dela o preparo volta uma vez (o "voltar" do Outlook leva à
  Inbox) antes de explorar. Título que não casa (pasta própria, outro idioma) segue `caixa_de_entrada`, o comportamento de antes: errar nunca faz o preparo apertar "voltar" na
  Inbox. A região visual (`conteudo_de_terceiros`) e o `sessao.yaml` cobrem a tela nova. Teste: `test_pasta_de_email_nao_e_estado_conhecido_31_339.py`.
- **31.340, `find_row(sender)`.** A lista da caixa é um `ComposeView` sem texto na árvore; na 3ª prova o ator gastou 5 imagens e um `find_element` sem ligar a mensagem ao
  `element_id` (US$ 0,17). Ferramenta só de leitura, tratada no executor (`taskqueue/linha_por_remetente.py`): cada linha candidata (clicável, sem texto em toda a subárvore,
  larga e alta como uma linha, dentro do contêiner da região declarada para `remetente`, no máximo 8) é lida pelo caminho da `read_value` visual (`ler_valor_visual`: recorte,
  leitor às cegas que não conhece o nome, concordância, triagem do ADR-009, a mesma linha uma vez por tela) e o ator recebe SÓ os `element_id` das que concordam. A transcrição,
  o remetente das outras linhas e o assunto nunca voltam; a ação grava só o tamanho do nome. Linha com código ou ilegível é pulada e contada; barreira de tela inteira (leitura
  visual desligada, tela sensível, sem leitor) encerra a busca com o motivo. Custo: uma leitura barata por linha (4 a 5 na caixa observada). Testes:
  `test_find_row_por_remetente_31_340.py`.
- **Limites:** nada disto foi exercido em aparelho real; a 4ª prova do P-046 (P-052) é do dono e é quem mede se `find_row` basta para achar a mensagem dentro do teto.

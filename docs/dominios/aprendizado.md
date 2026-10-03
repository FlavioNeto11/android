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

- `requires_owner = side_effect OR human_origin` é derivado: nenhuma rota o edita. O repositório confere de novo no
  próprio `UPDATE` e, com `conferir_nascimento`, no item que o sistema já cria num estado (segunda camada).
- **Veto.** O conteúdo desligado por uma pessoa não volta pelo sistema (mesmo `content_hash`, mesmo escopo). Desligado
  pelo sistema, fica vetado por 90 dias ou até a versão do app mudar.
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
executor caia fora de `outro`. A camada e o "onde alterar" saem do tipo na hora da leitura.

Quando a etapa termina por erro de IA, o tipo vem dele e não do texto (RA-22, migração 081). O executor põe o
`AIError.kind` no `StepOutcome.ai_error_kind` (o `desfecho_de_ia`, a verificação, e o `_run_guarded` do scheduler para o
erro que escapou), e o scheduler o passa ao `finish_attempt` (que grava `attempts.error_kind`) e ao `transition_step`.
Os dois classificam por `classificar_falha(texto, status, error_kind)`, o mesmo classificador puro da releitura:
`budget` → `ia_orcamento`, `billing`/`balance` → `ia_saldo`, `refusal` → `ia_recusa`, `not_configured`, `error` e
`invalid_output` → `ia_indisponivel` (`pelo_erro_que_encerrou`). `step_deadline` não decide: o ANR anotado no texto
continua ganhando do prazo. O status vem antes (`interrupted` segue `interrompida`). Com isso a mensagem de IA do
executor deixa de ser contrato; as REGRAS de texto ficam para o legado sem `error_kind`.

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
  sombra (`recipes_promote_after`); com `commit`, para em `validated`. `ai.recipes_heranca: false` só mede a causa, e
  com `recipes_promote_after: 0` não há herança (a aprendida já nasce ativa). Quando a herdeira vira `active`, a legada
  ativa da mesma etapa e versão sai (`superseded`, "provou-se na chave completa").
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
2. **Texto fechado.** Modelos fixos por tipo de falha. As lacunas só aceitam: ação do catálogo, tipo de pós-condição,
   contagem, sufixo de resource-id, `{parâmetro}` e rótulo curto que se repetiu, idêntico, em 2 execuções. Nunca
   `attempts.error` cru, texto de tela, nome de terceiro, valor de parâmetro ou segredo. A nota de um voto vira
   candidata `human_origin`, que só o dono publica.
3. **Nunca viram lição:** autenticação, desafio, 2FA, CAPTCHA, conta, IA e infraestrutura; a etapa de sessão ou login,
   pela ação do catálogo ou pela chave da etapa livre (`ACAO_DE_SESSAO`); a tentativa que parou em login ou desafio.
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
5. **Desligam a regra:** o primeiro conflito (login, desafio, 2FA ou conta errada no mesmo aparelho até 2 min de um
   uso), o modo fora de `on`, uma pessoa, e 30 dias sem casar numa versão nova do app.
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
- `scripts/aprendizado-backlog.py` grava o md em `data/aprendizado/` e imprime o topo.
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
  `attempts.failure_screen` no mesmo UPDATE de `failure_kind`, só quando há tipo de falha. O valor é o nome de uma
  regra declarada, ou o tipo do motor nas telas protegidas (`desafio`, `dois_fatores`, `login`, alinhado a
  `licoes.TELAS_EXCLUIDAS`); NULL quando a tela é desconhecida, outro app está na frente, o app não tem conhecimento
  ou não houve observação nesta tentativa. Nunca texto da tela. A trava achada dentro de uma ferramenta devolve a tela
  pelo próprio executor, porque `quick_tree` não atualiza `rt.last_tree`. As telas aprendidas ficam fora, para a
  chave do grupo não depender do modo do livro.
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
  da rota: habilidades ligadas (`features.skills`), status em `CORRECTABLE_STEP` e a origem do passo (`origin`, lida
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
  - **Sinais:** os votos e os gestos.
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
    "Digitar a mensagem · etapa fill_message (v1)". O nome do catálogo vence; sem os dois, fica a chave.
  - O `title` gravado não muda, e o dossiê do curador não leva `etapa`, porque o título de uma etapa pode citar um @
    ou um contato.
  - A ocorrência de falha diz "android-05 · etapa open_app · tentativa 1".
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

## Evento `learning.needs_person` (30.21)

Quando um item entra na fila "Para aprovar" (ou sai dela) o Livro publica o evento, no padrão de `session.needs_person`. Quem
decide é `application/espera.py::AvisadorDeEspera` (compara o item antes e depois, memória do último aviso por `kind:ref`); a
faixa e o motivo saem de `domain/espera.py::classificar_espera`, tradução da política de risco (30.10). A porta é `PortaDeEventos`
(`ports.py`), o adaptador sobre o `EventBus` é `infrastructure/eventos.py`, e o catálogo entra como fatos de risco
(`CatalogoDeRisco`, nunca texto de ação). Os pontos de chamada: `mudar_estado`, `propor`, `avisar_item` (a tela absorvida) e
`avisar_mudanca_nativa` (os ouvintes das lojas de receita e fluxo). Contrato do payload: `api-contract.md`, adendo v0.49.
Um gesto da pessoa que passa por dois estados (`mudar_status_nativo`: candidata, validada, publicada) pode publicar entrada e saída
na mesma ação. `state.py` passa `eventos=self.bus`.

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
- **A evidência `conflict` não vira relação:** na tela ela aponta para um aparelho (login, desafio), não para outro item.
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

  Divergência conhecida, que já existia para a receita: o aviso `learning.needs_person` da transição nativa
  (`application/espera.py`) não recebe a capability nem as etapas e segue dizendo B para esses fluxos. O parecer, o
  Revisar e o gesto usam o dossiê (C).
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
  `LearningService.lacos` (uma linha em `state.py`). Modo e intervalo são lidos a cada volta.
- **Modos**: `off` (padrão) não roda; `shadow` revisa, grava em `learning_reviews` e publica `learning.needs_person` com
  `motivo = parecer_da_ia` quando um parecer B ou C novo e válido fica pronto para item que JÁ espera o dono; `on` revisa igual e,
  desde o 30.17, mostra o parecer na fila e no detalhe e abre o aceite da pessoa (seção abaixo). A IA nunca decide: nada
  transiciona no curador (`conferir_aceite`).
- **Gatilhos ligados**: `nova_pendencia_do_dono` (fila "Para aprovar"), `a_revisar`, `degradando` e `obsoleto_provavel` (saúde do
  publicado), `conflito` (publicado com relação `contradiz`) e, desde o 30.17, `pedido_da_pessoa`. `versao_nova` e
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
- Fica para depois: o alerta do pico como evento + Problem em `/api/health` (hoje só log), o aviso a 80 % de `B_W`, as fontes dos três
  gatilhos sem fonte, e o `resultado_posterior`.

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

## Métricas (30.8)

`GET /api/aprendizado/metricas?app=&dias=` e `GET /api/aprendizado/revisoes` (adendo v0.89 do contrato). A aplicação é
`application/metricas.py`: contas puras sobre uma porta de leitura (`infrastructure/metricas_sql.py`), sem contador
novo em memória. Cada bloco da resposta é uma linha da tabela do §10 do desenho. O que vale para quem lê:

- a composição e a saúde saem das mesmas funções da visão por app (`servico.livro`, `servico.publicados`); a métrica
  não tem rótulo próprio. A economia é a do `taskqueue/aproveitamento.py`, injetada pela montagem (só leitura);
- ausente é `null`, com o `n` ao lado; simulado fica fora, e a revisão simulada é contada à parte;
- "falhas evitadas" é um PROXY rotulado: taxa de falha com receita × só IA nas etapas que tiveram as duas conduções;
- a receita não tem evidência datada, então o "sucesso depois de promovido" é de fluxo e lição;
- o orçamento do curador usa a conta da volta (`janela_do_orcamento`, extraída de `curador.py` para as duas servirem)
  com as revisões já gravadas: o B_W é um piso, o `uso` é um teto e o aviso, a 80 %, sai cedo. Sem `usd` medido
  (antes do #144-B no central), o c̄ é a média das estimativas; sem ela o B_W cairia a zero, defeito que o ensaio pegou;
- a série quebra no deploy 8 (LT-6, acima): compare janelas do mesmo lado de 03/10 09:06:28Z.

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
  contra de voto, sem olhar o braço; a exposição guarda o desfecho do primeiro assentamento; todas as lições de etapa
  livre dividem a chave `(app, '*', papel)`; o SQL novo só rodou em SQLite.
- **A8:** nove mutações sobreviveram, entre elas a árvore sensível do parque na coleta e o "zero contra" depois do
  nascimento (lacunas de teste, sem vazar segredo); a transição pelo livro vale para a sessão só depois do cache de 30
  s; a evidência vinda da conferência de sessão é gravada como real mesmo num parque simulado.
- **A9:** com o modo `off`, `escolheu_habilidade` nem é gravado, e as escolhas se perdem com a retenção dos eventos; o
  teto de 150 tokens vale só para os pares (o bloco passa de ~220); `destemplatizar` troca substring sem fronteira de
  palavra; a varredura olha só 2 dias; o painel não consome as sugestões.
- **Todos:** a suíte em PostgreSQL para o SQL de A2–A9 é `not_run`.

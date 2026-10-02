# Domínio: aprendizado contínuo

O sistema aprende com as execuções que dão certo e com as que dão errado, e com o que quem monitora faz ou diz. O
conhecimento fica num **livro** com ciclo de vida, e publicar sozinho só vale para o que não tem efeito externo e se
repetiu (D1). A decisão e as alternativas estão no
[ADR-054](../decisoes.md#adr-054--aprendizado-contínuo-livro-de-aprendizado-com-ciclo-de-vida-publicação-sozinha-só-sem-efeito-externo-d1-feedback-implícito-com-botão-opcional-d2-lições-medidas-e-backlog-do-que-mais-falha);
o contrato HTTP, nos adendos [v0.37 e v0.38](../api-contract.md); as tabelas (migração 055), em
[banco.md](../banco.md); a lição no prompt, em [ia.md §15](../ia.md).

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
   execução real; a etapa confirmada à mão entra como `unverified`, nunca como sucesso. O veredito exige 8 unidades por
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
    validada se decide ali pela rota das habilidades. Embaixo, **Revisar**: o legado ativo com efeito, que só se
    rebaixa ("Rebaixar selecionados");
  - **Aprendido:** o catálogo unificado por tipo e estado, com desligar, aposentar e reativar, sempre com motivo;
  - **O que mais falha:** o relatório do A3, com as três colunas de custo e o falso positivo no topo;
  - **Sinais:** os votos e os gestos.
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

## Evento `learning.needs_person` (30.21)

Quando um item entra na fila "Para aprovar" (ou sai dela) o Livro publica o evento, no padrão de `session.needs_person`. Quem
decide é `application/espera.py::AvisadorDeEspera` (compara o item antes e depois, memória do último aviso por `kind:ref`); a
faixa e o motivo saem de `domain/espera.py::classificar_espera` (mínima; o 30.10 a estende). A porta é `PortaDeEventos`
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
- **A6:** "Revisar" só rebaixa (manter o legado pede um verbo novo no backend); a página lê `/pendentes` duas vezes; o
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

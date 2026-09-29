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
receita e fluxo, `attempts.failure_kind`, `steps.failure_kind` e `attempts.failure_screen` (este ainda sem escritor).

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

**Dívida aceita:** `devices/manager.py` importa `taskqueue.costuras`, o primeiro import `devices` → `taskqueue`. Sem
ciclo, e as catracas passam. A saída é mover `CosturaDeControle` para `devices/` ou `app/shared`.

## Sinais: o que a pessoa já faz e o botão opcional (D2)

Os gestos viram `learning_signals`, idempotentes por `(kind, source_ref, created_by)`. Sinal de execução simulada não
promove nada. Nota com cara de credencial não é gravada (`note_refused=1`, ou 409 `note_looks_secret` no voto).

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
| `cancelou_execucao`, `comando_incerto_resolvido`, `correcao_de_ensino` | vocabulário, ainda sem escritor | — |

`tomou_controle`, `confirmou_a_mao` e `tela_desconhecida_chamou_pessoa` contam como intervenção humana na régua
diária.

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
  desta execução" mostra os votos e os sinais.

## Configuração

Bloco `aprendizado` do `config.example.yaml`; os modos vão entre aspas, porque `off`/`on` sem aspas viram booleano no
YAML. Padrões e significado na tabela do [adendo v0.38](../api-contract.md). Nenhum modo passa a `on` sem prova real e
decisão do dono.

## Pendências conhecidas

Dos revisores dos pacotes (29/09); nenhuma bloqueou o merge.

- **A2:** `failure_screen` sem escritor (quando for gravado, a linha do backlog aberta com a tela vazia pode cair a zero
  e parecer corrigida); o operador não chega ao sinal (todo gesto sai `panel`); `takeover_gravar` e
  `cancelou_execucao` não implementados; `revise_plan` pula a etapa sem limpar `failure_kind`; `respondeu_pergunta`
  grava o sha256 do comando inteiro da sucessora em cada campo.
- **A3:** `PATCH … fixed_pending_proof` aceita commit que não está no ar; falso zero de custo quando não há `ai_calls`
  nem régua diária na janela; o CAS do backlog só confere o estado; reabrir só vai ao log; a seção de saúde não traz
  "chamadas por etapa antes e depois de cada publicação"; o top 5 na skill `retomar` não foi feito; a e31953 sai como
  `prazo_da_etapa`, não `app_anr` (o classificador do A1 e os dados divergem), então a prova barata do desenho não bate
  como escrita.
- **A4:** o voto trocado continua contando como refutação; o `total` de `/sinais` é o das linhas devolvidas (até 500);
  os efeitos rodam fora de uma transação única; o voto da execução pode trazer efeito com `ref` vazio; duas lacunas de
  teste (mutações sobreviventes: voto "errado" em item que falhou; filtro por objetivo).
- **A5:** `PUT /api/flows` e `PUT /api/recipes` não gravam a trilha (o que uma pessoa desliga por ali não veta);
  `scheduler._learn_flow` ainda escreve "Fluxo salvo: … reaproveitam este plano" para um candidato inerte;
  `aprendizado.fluxo.com_prova: false` e `ai.recipes_promote_after: 0` levam o sistema a publicar o que tem efeito;
  o veto das receitas falha aberto; no PostgreSQL, a trilha dentro da transação da loja derrubaria o save; o
  `FLOW_STATUS` de `frontend/src/lib/status.ts` só conhece `active`/`disabled`, e o candidato aparece cru em
  Aplicativos e na guia Habilidades.
- **A6:** "Aprendizado desta execução" lê um bloco `aprendizado` de `GET /api/runs/{id}/feedback` que nenhum pacote
  emite; "Revisar" só rebaixa (manter o legado pede um verbo novo no backend); a página lê `/pendentes` duas vezes; o
  limite de "outro" está fixo em 15% no painel; o painel não lê `GET /api/runs/{id}/projection`, então o rótulo da
  janela efetiva não tem onde aparecer.
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

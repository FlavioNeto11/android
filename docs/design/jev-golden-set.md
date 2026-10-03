# Golden set e limiares pré-registrados do Jev (31.7)

**Estado:** PRÉ-REGISTRADO em 02/10/2026 (UTC; texto dos limiares no commit `ae2e5370`, branch `feat/31-8-curador-sombra`), antes de qualquer resultado do Jev (nenhuma chamada real feita; envio fechado no
código, `JEV_RUNTIME_SEND_APPROVED = False`). Frente Jev com a frente Aprendizado (resposta dela em
`.claude/handoffs/aprendizado/resposta-golden-31-7.md`). Base: ADR-069 item 6 ("`on` só por consumidor, com GO pré-registrado:
limiares escritos antes do primeiro resultado"). Mudar um número aqui depois do primeiro resultado exige registro datado e
diz por quê; o número antigo continua no histórico do Git. (Correção de 02/10 ~22:46Z: a primeira versão dizia "03/10"
por erro de data da sessão; os números não mudaram.)

**Revisão de 03/10/2026 (~01:20Z), ainda antes de qualquer resultado** (nenhuma chamada real ao Jev): RA-2 da reavaliação de
03/10 (`.claude/handoffs/reavaliacao-2026-10-03.md`) e I2 da revisão do 31.9. Entraram no §3 a ordem dos estratos da
intenção (1º qa-messenger; Instagram sem data), a métrica principal do 31.10, o teto de cobertura da R2 (4 de 31 fluxos) e a
contagem de `AMBIGUOUS`; no §2, a concordância passou a vir do registro do curador, sem casamento na hora. Nenhum limiar
numérico mudou.

## 1. Regras que valem para todo consumidor

- **Fallback nunca conta como acerto** (ADR-069 item 6). Toda linha com `fallback_reason` entra no denominador da COBERTURA e
  fica fora do de acerto.
- **`nenhuma` é abstenção:** não é acerto nem erro; entra na cobertura.
- **Rótulo antes de veredito:** nenhum consumidor tem veredito com menos de N rótulos no estrato (abaixo). Abaixo de N, o
  relatório diz "sem amostra", nunca uma taxa.
- **Braço de controle local e gratuito** em todo consumidor: o Jev só ganha GO se vencer a regra local na mesma amostra.
- **Estratos:** por `kind` (curador) e por app (intenção). Um GO vale por estrato; um estrato sem amostra fica `off`. O
  relatório do 31.10 dá, por estrato, a data prevista do GO (o ritmo de rótulos medido até o mínimo do estrato), menos
  onde este documento diz "sem data" (§3).
- **Português × inglês (D-J7):** as instruções do pedido estão em inglês e o estado em português. Antes de qualquer `on` na
  intenção, a mesma amostra roda nos dois idiomas de instrução (31.11, offline) e o idioma que perder sai.

## 2. Curador do Livro (R1, 31.8)

**Rótulos, por prioridade:**

1. **Decisão do dono** sobre o item, em `learning_transitions` (`decided_by` entre os autores declarados ao relatório
   com `--autor-dono`, `decided_at`, `from_state` → `to_state`), mapeada para a triagem: publicar ou manter → `manter`;
   rebaixar → `rebaixar`; desligar → `descartar`. Até o 31.19 valia toda pessoa (`decided_by` ≠ `sistema`); ver o registro
   abaixo. Medido em 02/10 no central (só leitura): 48 transições, 2 de pessoa. O golden por decisão humana é quase vazio e
   cresce com a fila "Para aprovar" (30.17).
2. **Desfecho medido** depois da triagem (`learning_reviews.resultado_posterior`, 14 e 30 dias): receita reativada que
   reproduz bem → `manter` era certo; de volta à quarentena → `rebaixar`/`descartar` era certo; item rebaixado pelo sistema
   que a pessoa reativou → contra-exemplo.
3. **Concordância com o curador principal**: o parecer do Claude que VALEU, lido de `learning_reviews` (mesmo
   `dossie_hash` = `ref` da sombra, `validade = 'ok'`, `simulated = 0`) e posto na régua da triagem por
   `decisao_real_da_triagem` (`TRIAGEM_DO_PARECER`), pelo relatório do 31.10. Nada se casa na hora (I2 da revisão do 31.9,
   03/10): a validade só existe depois do `revisar`, e parecer inválido, recusado ou simulado não é decisão real. Só métrica
   de acompanhamento, NUNCA critério de GO sozinha. Mede se o Jev concorda com o Claude, não se acerta.

**Controle:** a regra do adaptador simulado (mais evidência contra que a favor → `revisar`; senão `manter`), sobre o mesmo
estado C0.

**GO para `on` (por `kind`, `licao` e `receita`), todas juntas:**

| Critério | Limiar | Origem |
|---|---|---|
| Rótulos (1 ou 2) no estrato | ≥ 30 | D-3 do dono (curador) |
| Acordo com o rótulo, entre as respostas válidas | ≥ 90 % | D-3 do dono |
| Erro grave: Jev diz `rebaixar`/`descartar` e o rótulo é `manter` | ≤ 5 % das válidas | proposta Jev |
| Cobertura (respostas válidas / pedidos) | ≥ 80 % | proposta Jev |
| Vantagem sobre o controle, em acordo | ≥ 5 pontos percentuais | proposta Jev |

`on` no curador significa só ORDENAR a fila (o Claude vê primeiro o que a triagem sinalizou); a triagem nunca transiciona
item nem aceita parecer (ADR-069 item 2).

*Registro de 03/10 (~15:05Z), 31.19, a pedido da orquestradora.* Nenhum limiar da tabela mudou.
- **O limiar da porta fica em 0,85** (`contrato.pergunta_choice`). As 4 primeiras respostas reais (13:10Z, deploy 9)
  voltaram `abaixo_do_limiar`, com confiança 0,50–0,52 e maior probabilidade `revisar` 0,60–0,62. As 4 levaram o MESMO
  estado C0 (1 estado distinto), e o curador principal decidiu 3× `revisar` e 1× `rebaixar` sobre ele. A entrada não
  distingue os casos, e nenhum limiar separa o que a entrada não distingue. Contrafactual (INFERRED, só acompanhamento):
  com limiar ≤ 0,50, as 4 passariam com `revisar` e concordância 3/4 com o curador principal. Isso não é GO: há 0 rótulos.
  Mexer no limiar exige rótulos (≥ 30 no `kind`) e outro registro datado.
- **O rótulo 1 passa a valer só do dono.** No central (só leitura, 03/10), as 11 transições com `decided_by` ≠ `sistema`
  eram 7 da `orquestradora`, 2 de sessão Claude do Aprendizado e 2 `panel` de 29/09. `panel` é o último recurso de
  `api.quem`: ninguém se identificou. Nenhuma era do dono, e todas são anteriores à sombra. Uma decisão de sessão Claude
  não é rótulo independente do Jev. Nenhuma configuração declara o dono, por isso o nome vem de quem roda o relatório
  (`--autor-dono`, repetível), e `panel` só conta se declarado. Sem nome, o rótulo 1 fica desligado (falha fechada).
  O nome do dono é `Flavio`, confirmado pela orquestradora em `panel_sessions` (operador `Flavio`, leitura em modo só
  leitura, 03/10). Ressalvas: o nome é declarado atrás de um token compartilhado, e um agente de validação que use o Chrome do
  dono também aparece como `Flavio`. Por regra, esses agentes só leem e não geram transição.
- **A porta passa a seguir o contrato** (decisão da orquestradora, 03/10 ~15:15Z). A `Pergunta` diz que o limiar vale
  sobre a probabilidade devolvida, mas a porta comparava a `confianca`. Agora, no `choice`, o limiar vale sobre a
  probabilidade da opção escolhida, que precisa ser a maior (com o Jev coerente, é a maior probabilidade). A escolha
  sem probabilidade, ou que não é a maior, falha fechado. O limiar segue em 0,85 e nada muda hoje: nas 4 respostas, a
  maior probabilidade foi 0,60–0,62. A `confianca` segue gravada na sombra, e o relatório mostra as duas colunas
  (`cobertura_por_limiar`). `noul` e `score`, sem produtor, seguem na `confianca`.

## 3. Intenção (R2 e R3, 31.9)

**Estratos (RA-2; execuções de 7 dias até 03/10 no central):** qa-messenger 58 (+3), instagram 15, outlook 5.

- **1º estrato: qa-messenger.** É o volume que chega ao mínimo de rótulos abaixo.
- **Instagram: GO sem data.** O relatório conta os rótulos do estrato e não projeta data; o GO do Instagram se decide
  no relatório do 1º estrato. Na reavaliação, 13 de 15 comandos tinham `@` e 11 de 15, número de 3 ou mais dígitos, e a
  remoção da primeira redação recusava esses comandos inteiros por construção.
  - *Registro de 03/10 (~02:45Z), a pedido da orquestradora:* a base "13/15 recusados por construção" caiu.
  - Com o filtro corrigido ainda em lista de permissão, 15 de 20 comandos de 7 dias sairiam mascarados.
  - Com o filtro sensato do ADR-069 item 10 (`@` e número viram marcador, nome passa), saem 19 de 20; a recusa que sobra
    é C7.
  - Medição só leitura, por contagem. Nenhum limiar muda.
- **Outlook e os demais:** data pelo ritmo medido, como no §1.

**Rótulos:** o desfecho da execução (`casar_desfecho`, 31.10: a habilidade resolvida que terminou em sucesso comprovado) e,
no empate, a escolha da pessoa. Numa execução SEM fluxo, o rótulo é o **fluxo que o desfecho confirma**: o fluxo ativo que
faz o que a execução fez com sucesso comprovado, apontado pela pessoa no parecer do 30.17 (`learning_reviews`, o único
produtor de rótulo humano, da frente Aprendizado; o campo se combina com ela), com o id da execução, sem caminho paralelo
(orquestradora, 03/10).
Nunca é a escolha do Jev nem a concordância com a cadeia; sem esse rótulo, a execução fica só na cobertura. A decisão real
que a sombra casa hoje (o que a cadeia resolveu) é acompanhamento.

O rótulo de intenção do Aprendizado (30.25) é uma linha de `learning_reviews` com `template_id='intencao'`. Ela lê a
execução por `RunService.dados_da_intencao` (o comando sem destinos, as personas e o app principal) e o catálogo por
`AppState.catalogo_da_cadeia`: os MESMOS da sombra da intenção. Assim rótulo e sombra medem o mesmo comando contra o mesmo
catálogo. O contrato vem da orquestradora (03/10); o fix do 31.9 entra antes do 30.25 na suíte 7.

**Métrica principal do 31.10 (RA-2):** entre as execuções sem fluxo do estrato (`sem_casamento`: o planejador fez o
trabalho), quantas o Jev teria casado ao fluxo que o desfecho confirma. Por quê: as execuções com fluxo já não chamam o
planejador (0 chamadas `plan` em 51 de 51, 7 dias), e 168 de 172 sem fluxo chamam. O ganho em latência e custo só existe
onde o casador determinístico erra; concordar com a cadeia onde ela já casou não é ganho.

**Teto de cobertura da R2 (RA-2):** uma `choice` só resolve SEM pergunta um fluxo ou habilidade sem `{parâmetro}`: a etapa
semântica exige `achado.complete` (`intent_resolver.py:214`), e com parâmetro vira `needs_input`. No central em 03/10, 4 de
31 fluxos cadastrados não tinham parâmetro; entre os 25 ativos, que são os que a cadeia vê, eram 3 (GET local de
`/api/flows`, ~01:40Z). O relatório traz os dois denominadores do dia, e o PRINCIPAL é o dos ativos (fluxos ativos sem
parâmetro sobre fluxos ativos; 3/25 em 03/10), confirmado pela orquestradora. Um acerto num fluxo com parâmetro conta à
parte, porque ainda pede o parâmetro à pessoa.

**Ambiguidade da cadeia (RA-2):** cada linha da intenção grava quantas etapas da RESOLVE terminaram em
`StageOutcome.AMBIGUOUS` (`decisao_fechada_sombra.ambiguos`, migração 079). A coluna fica NULA nas linhas de outra origem e
nas anteriores à migração. O relatório dá, por estrato, a distribuição dessa contagem e as execuções com ao menos uma etapa
ambígua: só ali a R3 (desempate) tem o que medir.

**Controle:** a própria cadeia de hoje (`intent_resolver`), que é gratuita e já roda.

**GO para `on` (só sugerir, D-J7), por app:**

| Critério | Limiar | Origem |
|---|---|---|
| Comandos rotulados no estrato | ≥ 50 | proposta Jev |
| "Aceite errado" (sugestão do Jev que a pessoa aceitaria e o rótulo desmente) | ≤ 2 % | proposta Jev (D-J7) |
| Precisão das escolhas diferentes de `nenhuma` | ≥ 95 % | proposta Jev |
| Recusas por privacidade (entidade que sobrou) | relatadas; não entram no acerto | ADR-069 C3 |
| Métrica principal: execuções sem fluxo que o Jev casa ao fluxo que o desfecho confirma | > 0, com a precisão acima | proposta Jev (RA-2) |

## 4. O que fica fora

- Memória (conteúdo nunca sai), fluxo (C2, F2) e a DECISÃO por persona (D-J5; desde o ADR-069 item 10, o dado pessoal pode
  ir com filtro sensato, C7 nunca).
- Qualquer chamada: o 31.7 não chama nada. A primeira medição real é o 31.10, nos tetos do ADR-069 (sem troca de chave: item 9).

## 5. O relatório do 31.10

`scripts/jev-relatorio-31-10.py` mede a sombra contra os limiares acima. É só leitura e não chama IA:
- o banco abre em modo só leitura (`PRAGMA query_only` ou `READ ONLY`), também na reconexão;
- fora do banco, só faz o GET local de `/api/flows`, para o teto da R2.

Uso, a partir da raiz do checkout:

    backend/.venv/Scripts/python.exe scripts/jev-relatorio-31-10.py [--db data/poc.sqlite3 | --dsn postgresql://...] \
        [--desde <ISO-8601 UTC>] [--sem-flows] [--json saida.json] [--md saida.md] [--autor-dono NOME ...]

- **Curador, por `kind`** (o `item_kind` da revisão do mesmo dossiê):
  - rótulo 1: a primeira transição do DONO no item depois da linha da sombra (`decided_by` entre os `--autor-dono`; sem
    eles, desligado), pela direção (`from_state` e `to_state`): `disabled` dá `descartar`; `deprecated` ou descer na
    escada (`draft` < `candidate` < `validated` < `published`) dá `rebaixar`; subir, ficar ou reativar dá `manter`. A
    fonte sai como `dono`, e `rotulo_1.transicoes_fora_do_dono` conta as de pessoa que ficaram de fora (31.19).
  - rótulo 2: o `resultado_posterior`.
  - controle: a regra do adaptador simulado sobre o mesmo dossiê (mais evidência contra que a favor dá `revisar`).
  - `estados_distintos`: quantos estados C0 diferentes a triagem mandou. Com 1, nenhum limiar separa as respostas.
  - `cobertura_por_limiar`: a fração dos pedidos que passaria em 0,50, 0,60, 0,70 e 0,85, pela `confianca` e pela maior
    probabilidade. É contrafactual (INFERRED): o limiar da porta não muda.
- **Intenção, por app** (o primeiro de `runs.app_ids`):
  - rótulo: a escolha da pessoa no rótulo do 30.25 (`learning_reviews`, `decidido_por` diferente de `sistema`) ou o
    desfecho, isto é, a habilidade que a cadeia resolveu numa execução de sucesso comprovado (`sucesso_comprovado`, o
    `casar_desfecho` feito na leitura). O rótulo pelo desfecho é INFERRED;
  - medidas: precisão, aceite errado, a métrica principal, os ambíguos, a privacidade por motivo (fora do acerto) e os
    acertos em fluxo com parâmetro;
  - controle: a cadeia, medida só contra o rótulo da pessoa (pelo desfecho, o rótulo É a decisão da cadeia).
- Abaixo do mínimo do estrato, o relatório diz "sem amostra" e a data prevista do GO, nunca uma taxa.
- Custo: chamadas, US$, tokens, latência p50 e p95 e o maior dia contra a fatia de US$ 0,50. Os percentis são pelo posto
  mais próximo (`ceil(pct·n/100)`, em aritmética inteira), o método do `sombra._p95` e da prova do 31.17 (31.19).
  Antes, o relatório interpolava a mediana e a prova arredondava `q·(n−1)`: com as 4 chamadas de 03/10, o p50 dava
  473,8 ms num e 510,2 ms no outro; agora dá 437,3 ms nos dois, e o p95, 552,8 ms.
- Níveis: contagens `PROVED`; taxas, rótulo pelo desfecho e controle `INFERRED`. O relatório nunca liga nada: `on` é
  decisão registrada (ADR-069 item 6).

**Limites conhecidos** (03/10):
- O rótulo 2 do curador ainda não tem produtor na main: nenhum código grava `resultado_posterior`. O 30.35 gravará
  `manter`, `rebaixar`, `descartar` ou `sem_desfecho`; os três primeiros estão na régua da triagem, e `sem_desfecho` não
  rotula.
- O rótulo 1 do dono depende do nome declarado em `--autor-dono` (nenhuma configuração declara o dono).
- A métrica principal só se mede com rótulo da pessoa. A execução sem fluxo não tem habilidade resolvida que o desfecho
  confirme.
- O "aceite errado" é a escolha diferente de `nenhuma` que o rótulo desmente, sobre os comandos rotulados.
- A R3 (desempate) só é contada (`pedidos_r3`): a sombra da R3 não tem decisão real, e o veredito por app é o da R2.

Prova: `simulated` (`scripts/tests/test_jev_relatorio_31_10.py`). Execução no banco do central: `not_run` (roda depois
do merge da suíte 7 e do deploy).

## 6. A prova real do 31.17

`scripts/jev-prova-31-17.py` confere, no banco do central, a sombra C0–C1 do curador desde a partida com o envio aberto
(T_on do deploy 9, `2026-10-03T11:04:15Z`). É só leitura e não chama IA: o SQLite abre por URI `mode=ro`, com
`PRAGMA query_only` por cima.

    backend/.venv/Scripts/python.exe scripts/jev-prova-31-17.py [--db data/poc.sqlite3] [--desde <ISO-8601 UTC>] \
        [--classes C0,C1] [--json saida.json]

- **Linhas da sombra (PROVED):** só a origem `curador`, só em `shadow`, só nas classes liberadas; a pergunta da triagem,
  escolha e probabilidades só entre as opções `opt:*`, e o `ref` como sha256 do dossiê.
- **O corpo que saiu (INFERRED):** o corpo não é guardado (074, ADR-069 item 5). O dossiê é, em `learning_reviews` do
  mesmo `dossie_hash`. Quando o `content_hash` dele bate com o `ref` (PROVED), o corpo é remontado com o código do
  checkout, pelo caminho da porta (`pedido` → `validar` → `redigir` → `{state, model, questions}`). Por isso o script
  roda no commit implantado (`GET /api/health`). Do corpo, confere:
  - só as três chaves e o `state` só com os `CAMPOS` do curador;
  - nenhum texto do dossiê fora do vocabulário: conteúdo, motivo de voto, ids, execuções, app, capability, aparelho;
  - nem o `dossie_hash`, o `item_ref`, data, uuid ou hex longo.
- **Custo e latência (PROVED):** as linhas do Jev em `ai_calls` (`provider='jev'`, `origem='decisao_fechada'`), com
  US$, tokens e ms (p50, p95 e máximo; os percentis pelo posto mais próximo, o mesmo método do relatório do 31.10 desde o
  31.19), falhas por motivo. Cruza com as chamadas da sombra que chegaram ao POST (as
  recusas por privacidade, orçamento e desligado não viram linha).
- Saída: um resumo curto e, com `--json`, só contagens, ids opacos e números. Código 0 quando tudo confere, 1 com
  violação, 2 sem linha no período.

Prova: `simulated` (`scripts/tests/test_jev_prova_31_17.py`). Execução no banco do central: `not_run` (roda depois da
primeira volta do curador com o Jev em sombra, quando a orquestradora mandar, junto do relatório do 31.10).

## 7. A leitura da sombra de intenção (R2 e R3) depois do deploy 11

`scripts/jev-leitura-intencao.py` é irmão do §6 para a origem `intencao`. Ele lê a sombra desde a partida do deploy 11
(`c8304e85`, intenção em sombra com C3). É só leitura e não chama IA (URI `mode=ro` e `PRAGMA query_only`). O corpo da
intenção é o comando filtrado e não é guardado, por isso não se remonta: aqui o que se confere é a linha.

    backend/.venv/Scripts/python.exe scripts/jev-leitura-intencao.py [--db data/poc.sqlite3] [--desde <ISO-8601 UTC>] \
        [--json saida.json]

O padrão de `--desde` é `2026-10-03T15:30:00Z`, a partida aproximada; passe o instante exato que a orquestradora der.

Todas as medidas abaixo são PROVED:

- **Contagens:** linhas por origem/classe/modo. Na intenção, por pergunta (R2 = `intencao_catalogo`, R3 =
  `intencao_desempate`): pedidos, respondidas, abstenções (`nenhuma`), fallbacks e comandos (`run_id` distintos).
- **Recusas:** por `fallback_reason` e, nas de privacidade, por `motivo_privacidade`. Só os motivos do vocabulário
  fechado aparecem; um valor fora dele sai como `(fora do vocabulário)` e conta como violação.
- **Contra o limiar:** o limiar é o padrão de `contrato.pergunta_choice`, lido do código. Há três distribuições, todas
  com p50, p95, faixas fixas e a contagem acima do limiar:
  - da `confianca`;
  - de `probabilidades[escolha]` nas respondidas;
  - da maior probabilidade em todas as linhas.

  Resposta abaixo do limiar, ou sem a probabilidade da escolha, é defeito da porta (31.19, item 20).
- **Custo e latência:** as linhas do Jev em `ai_calls` com `ref` `intencao:<run_id>` (chamadas, falhas por motivo, US$
  total e por chamada, tokens, ms p50/p95 pelo posto mais próximo). O cruzamento com as chamadas da sombra que chegaram
  ao POST deixa de fora as recusas por privacidade, orçamento e desligado, que não viram linha. Esse cruzamento informa e
  não reprova, porque a `rede` do prazo esgotado antes do POST também não vira linha e a sombra não a distingue.
- **Zero texto (falha fechado):** cada coluna só aceita o formato dela, e um valor fora do formato vira violação pelo
  nome, nunca pelo conteúdo. O formato de cada coluna é:
  - ids opacos `opt:<12 hex>` ou `opt:nenhuma`;
  - `probabilidades` como JSON {id opaco: número em [0, 1]};
  - vocabulário fechado;
  - números;
  - `ref` e `run_id` sem espaço.

  Coluna fora do esquema de 074 e 079 também é violação.
- Saída: dez linhas e, com `--json`, só contagens, motivos e números. Código 0 quando tudo confere, 1 com violação
  (mesmo sem amostra), 2 sem linha da intenção no período.

Prova: `simulated` (`scripts/tests/test_jev_leitura_intencao.py`, com linhas falsas). Execução no banco do central:
`not_run` (roda quando a orquestradora avisar, com 10 ou mais comandos depois do T_on).

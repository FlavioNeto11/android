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
- **A porta passa a seguir o contrato** (decisão da orquestradora, 03/10 ~15:15Z; ADR-069 item 20, com o rótulo 1). A `Pergunta` diz que o limiar vale
  sobre a probabilidade devolvida, mas a porta comparava a `confianca`. Agora, no `choice`, o limiar vale sobre a
  probabilidade da opção escolhida, que precisa ser a maior (com o Jev coerente, é a maior probabilidade). A escolha
  sem probabilidade, ou que não é a maior, falha fechado. O limiar segue em 0,85 e nada muda hoje: nas 4 respostas, a
  maior probabilidade foi 0,60–0,62. A `confianca` segue gravada na sombra, e o relatório mostra as duas colunas
  (`cobertura_por_limiar`). `noul` e `score`, sem produtor, seguem na `confianca`.

*Registro de 03/10 (~18:45Z), 31.11, a pedido da orquestradora.* Nenhum limiar da tabela mudou.
- **O braço offline (31.11) usa o `DecisorJev` pela `Porta`**, o mesmo caminho do runtime, sem o adaptador MIT e sem
  dependência nova de terceiro (decisão da orquestradora, ~18:40Z).
- **A "pergunta 4" do roteiro já está respondida** pelos itens 7 e 9 do ADR-069: a prova real paga em sombra (31.10
  e 31.11) está autorizada com a fatia do Jev e o teto registrado, sem troca de chave. A dependência do 31.11 é o
  31.7, que está feito. A C3 da R2, da R3 e da R5 espera o sim do dono à emenda do item 4.
- **Contagem de rótulos do curador, real, só de contagem:** 03/10 18:33:11Z, WIN-7S2UASNLFOP, banco do central em
  `mode=ro`, deploy 12 (`d5a1c3a9`).
  - São 57 revisões (`learning_reviews`, template `curador`), todas válidas e reais: 29 de `fluxo`, 28 de `receita`
    e nenhuma de `licao`.
  - O escopo da R1 (`KINDS_F1`) são as 28 de `receita`, com 9 estados C0 distintos. O fluxo é C2 e fica fora.
  - Rótulo 1: 0. Não há transição do dono.
  - Rótulo 2: 0. O gravador do 30.35 usa uma janela de 14 dias, então o primeiro rótulo possível é de 17/10 (INFERRED).
  - Concordância com o curador principal, só acompanhamento: 28 de 28 (25 `revisar`, 2 `manter`, 1 `rebaixar`).
  - Conclusão: nenhum `kind` tem amostra para GO. A rodada offline da R1 (teto US$ 0,05) é acompanhamento: ela diz se
    a entrada carrega sinal e dá a grade de sensibilidade.

*Registro de 03/10 (18:53Z), 31.11, a rodada real da R1 no braço offline.* É acompanhamento: nenhum número vale para
GO (rótulos 1 e 2 = 0). Nenhum limiar mudou.
- **Execução:** `scripts/jev-braco-offline.py` no checkout central @ `926b4f6f`, WIN-7S2UASNLFOP, `--enviar --teto
  0.05`, uma vez.
- **Volume e custo:** 29 casos de receita, 29 chamadas ok, US$ 0,000764. Essas chamadas não estão em `ai_calls`.
- **Sinal:** os 9 estados C0 distintos deram a MESMA resposta de maior probabilidade (`revisar`), e nenhum estado
  repetido mudou de resposta. Só a probabilidade varia com o estado, de 0,57 a 0,87. O estado C0 de hoje não separa
  os casos.
- **Cobertura no limiar 0,85:** 3 de 29.
- **Concordâncias (acompanhamento):** o controle concorda com o curador em 2 de 29. Nenhum estado tem mais evidência
  contra que a favor, então as contagens de evidência não explicam o parecer do curador.
- **Proposta enviada à orquestradora:** campos fechados do dossiê que podem dar sinal (versão viva testada, uso,
  idade da evidência, motivos de saúde e de risco, trilha).

*Registro de 03/10 (~19:10Z), 31.23, pré-registro do estado `v2` antes de qualquer resultado dele.* Nenhum limiar
da tabela mudou.
- **O que muda:** só a entrada. O `v2` soma ao `v1` campos fechados de sinal (`curador.CAMPOS_DE_SINAL`). A sombra
  do runtime segue no `v1`.
- **Medição:** o braço offline com `--estado v2`, nos MESMOS 29 casos de receita da rodada de 18:53Z, com teto de
  US$ 0,05, na janela da orquestradora, depois do deploy.
- **Critério para o `v2` virar o estado da sombra:** os estados `v2` com resposta precisam dar 2 ou mais respostas
  distintas de maior probabilidade, e os estados instáveis (respostas diferentes no MESMO estado) não podem passar de
  10 % dos estados com resposta. Sem isso, o `v2` não entra, e a próxima tentativa é a pergunta com o `noul` (31.13).
- **Seco de 03/10 (19:02Z, central em `mode=ro`, nada enviado):** os 29 casos dão 19 estados `v2` distintos (eram 9 no
  `v1`), e a privacidade aceitou os 29 pedidos.
  - Nenhum dossiê de receita traz evidência na lista. A evidência da receita vai aos contadores, e por isso a idade da
    evidência a favor é `nunca` nos 29.

*Complemento de 03/10 (~19:40Z), com a resposta da Aprendizado e a decisão da orquestradora.* Nenhum limiar da
tabela mudou.
- **A lista vazia é lacuna, não desenho** (`docs/dominios/aprendizado.md`). Os contadores da receita não têm data,
  aparelho, versão nem marca de real ou simulado. Vira o 30.39 (frente Aprendizado): evidência datada por tentativa
  conduzida pela receita, e o dossiê da receita passa a ler a lista como o do fluxo.
- **Duas medições do `v2`, cada uma com o "vai" da orquestradora e o mesmo teto (US$ 0,05):** uma logo depois do
  deploy 14 e outra depois do 30.39, nos mesmos casos.
  - O critério acima (2 ou mais respostas distintas, no máximo 10 % de estados instáveis) vale para cada medição,
    separadamente.
  - Antes do 30.39, `evidencia_a_favor_idade` é constante (`nunca` nos 29) e não conta como sinal: a primeira
    medição é lida sem esse campo.
- **2º controle, "regra da saúde", só de acompanhamento** (fora do critério de GO; o controle pré-registrado não
  muda). A Aprendizado leu os 29 pareceres: o curador segue o rótulo de saúde do dossiê, não as contagens cruas
  (pedir evidência 17, observar 9, manter 2, rebaixar 1).
  - O braço mapeia o rótulo pela tabela `CONTROLE_DA_SAUDE` do script: `saudavel` → `manter`; `pouca_amostra`,
    `em_prova`, `sem_evidencia`, `parado` e `degradando` → `revisar`. Na triagem, pedir evidência e observar são
    `revisar`.
  - Rótulo fora da tabela (inativo, obsoleto provável, indeterminado ou ausente) fica sem resposta de controle e é
    contado à parte.
  - O JSON traz a concordância dele com o curador ao lado da do controle pré-registrado: quanto do parecer se
    explica só pela saúde.

*Nota de 03/10 (~18:55Z), PROPOSTA, NÃO VIGENTE: a validação automática como rótulo "2v".* Fica pré-registrada
antes de existir qualquer rótulo e não muda o GO da tabela. A orquestradora reavalia depois do deploy 14, com o P4
de volta e volume real, e só então decide o contrato com a Aprendizado.
- **Condições duras.** Só conta a validação `feita` (`learning_validations`) cuja execução deixou uma DIREÇÃO legível
  pelo `run_id`, com `simulated = 0` e noutro aparelho que o de origem.
  - `for` vira `manter`; `against` de etapa reprovada vira `rebaixar`. Uma execução só nunca rotula `descartar`.
  - Não rotulam: `sem_evidencia`, `forma` (30.36), `recusada`, `expirada` e falha de infraestrutura.
  - Na receita, a direção por execução ainda não existe: a evidência vai aos contadores. No central, a única
    validação `feita` de receita não tinha linha em `learning_evidence` com o `run_id` dela (03/10 18:40Z, só leitura).
    Isso pede o contrato "direção por execução na receita (stance + `run_id`)" com a Aprendizado.
- **O resultado posterior vence o 2v** quando os dois existem para o mesmo item.
- **Corte:** com 10 ou mais pares 2v × posterior, a fonte 2v sai do GO se a concordância entre os dois ficar
  abaixo de 80 %.
- **Fonte separada:** o relatório mostra `fonte=validacao` à parte das fontes 1 e 2. Os rótulos 2v contariam para os
  30 por `kind`; os demais limiares não mudam.
- **Viés conhecido:** só ganha validação o item em que o curador principal pediu evidência (`opt:revisar`).
- **Pergunta futura, sem ação:** incluir o fluxo na triagem (C2 na R1). A prova de fluxo do 30.37 seria o rótulo
  mais limpo, e 29 das 57 revisões de 03/10 são de fluxo.

*Registro de 04/10 (~16:05Z), 31.11, decisão da orquestradora sobre a fonte do rótulo 1.* Nenhum limiar da tabela mudou.
- **Ninguém registra transição como se fosse o dono.** A confirmação do dono a uma lista de rótulos proposta (ficha
  `.claude/handoffs/jev/rotulos-31-11.md`: 30 receitas, a regra pelo histórico de replay) vira uma **segunda fonte**
  do rótulo 1, `confirmacao_em_bloco`. Ela vive num arquivo próprio (`--rotulos-em-bloco ARQ`, JSON
  `{"confirmacoes": [{item_ref, rotulo, data, frase, autor}]}`, com a frase literal do dono) e vale só depois da
  linha da sombra.
- **Não se mistura:**
  - a transição do dono item a item vence;
  - as linhas em bloco ficam fora de `rotulos`, `acordo`, `erro_grave` e do veredito principal, com medidas próprias
    em `medidas.confirmacao_em_bloco`;
  - `rotulo_1.confirmacao_em_bloco` mostra a data, o autor e a frase.
- **GO do 31.11:** diz por extenso de que fonte veio cada rótulo. A ficha é regra de sessão Claude confirmada em bloco,
  não rótulo independente do Jev (registro de 03/10).
- **Arquivo fora da forma** (rótulo fora de manter, rebaixar ou descartar; data fora do ISO; campo vazio; item
  repetido): o relatório encerra com erro. Falha fechada.
- Prova `simulated`: `scripts/tests/test_jev_relatorio_31_10.py`.

*Registro de 04/10 (21:45Z a 22:00Z), 31.55, a confiança do curador R1 (receita).* Nada do runtime mudou:
`ESTADO_DA_SOMBRA` segue `v1` e o limiar da porta segue 0,85.
- **Sombra no central (só leitura, 21:45Z):** 41 linhas de `curador_triagem`, 38 abaixo do limiar. A maior probabilidade
  tem mediana 0,79 e máximo 0,86; o topo é `revisar` em 39 linhas. Contra os rótulos do dono (39 linhas), a concordância
  é de 2 em 39, e baixar o limiar não ajuda: em 0,5 a cobertura iria a 41 de 41 com o mesmo `revisar` contra o rótulo.
  Nove linhas têm o mesmo `estado_hash` e rótulos diferentes (o achado do 31.19).
- **Braço offline (`scripts/jev-braco-offline.py`, pago, autorizado pela orquestradora, 21:51Z a 21:56Z):** três rodadas
  sobre a entrada `v2`, cada uma com a chave `lote:jev:31.55-<rodada>` só no registro. Critério pré-registrado: concordância com o dono a 0,85 em 70 % ou
  mais.

  | Rodada | Respostas a 0,85 | Acertos |
  |---|---|---|
  | `v2` (a pergunta do runtime) | 11 | 1 (9 %) |
  | `P1` (quando `revisar` cabe; escrita depois de ver a amostra) | 48 | 0 |
  | `P2` (sem `revisar`) | 6 | 2 (33 %) |

  Custo do item: US$ 0,006948 nas três, dentro do teto de US$ 0,15 e fora do livro-caixa do ADR-051.
- **Ressalva que vale para tudo acima:** os rótulos são a confirmação em bloco de uma lista proposta pela própria sessão
  (registro do 31.11), então qualquer acordo com a regra é circular. O perfil `degradando` e `falhas_seguidas` aparece nos
  4 itens rebaixados e em nenhum dos 60 mantidos, mas isso mede a regra da ficha, não o dono. Nenhuma das quatro
  variantes reproduz a política "manter até degradar".
- **Decisão (orquestradora, 21:59Z):** nada muda no runtime e não há rodada nova nesta amostra. As variantes
  pré-registradas ficam no braço offline (`--pergunta`, `PERGUNTAS_DO_31_55`). Prova `simulated` do código:
  `scripts/tests/test_jev_braco_offline.py`; a medida é `real` (central, só leitura e chamadas pagas registradas acima).

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

**Viés conhecido do estado (registrado em 03/10 20:17:10Z, a pedido da orquestradora, no commit 85777835, que era
42a63c5b antes do rebase sobre a suíte 14; a pesar no GO do 31.10).**
- O que acontece: o estado da intenção leva o `app` da execução. A sombra roda depois do `_plan`, e nessa hora o `app` é
  o app PRINCIPAL do plano (`save_plan` grava `runs.app_ids`).
- Efeito na R2: é uma dica para a escolha do catálogo (as entradas daquele app). INFERRED: o viés é menor que na R5,
  porque o rótulo da R2 é o desfecho da cadeia, que resolve antes do plano.
- A R2 não muda agora. Na R5 o mesmo campo seria o próprio rótulo, e por isso a R5 manda só o comando (§9).

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
- **Marca de POST (desde a 083, 31.21):** cada chamada da sombra diz se chegou ao POST (`postado`) e qual linha de
  `ai_calls` gerou (`ai_call_id`; `docs/ia.md` §16). A leitura:
  - conta as chamadas por marca (1, 0, sem marca);
  - separa a `rede` em antes do POST, depois e sem marca;
  - conta a régua cega (POST sem linha de gasto);
  - cruza por id: cada `ai_call_id` tem de ser uma linha do Jev da intenção em `ai_calls`, procurada pelo id e não pela
    janela do `--desde`. Se não for, REPROVA. Por isso o `--desde` precisa caber na retenção de `ai_calls` (14 dias de
    fábrica).

  Marca incoerente também é violação: id sem POST, resposta sem POST ou a mesma chamada com marcas diferentes. As linhas
  anteriores à 083 ficam sem marca e só entram no cruzamento por contagem, e um banco sem as colunas é lido do mesmo
  jeito.
- **Zero texto (falha fechado):** cada coluna só aceita o formato dela, e um valor fora do formato vira violação pelo
  nome, nunca pelo conteúdo. O formato de cada coluna é:
  - ids opacos `opt:<12 hex>` ou `opt:nenhuma`;
  - `probabilidades` como JSON {id opaco: número em [0, 1]};
  - vocabulário fechado;
  - números;
  - `ref` e `run_id` sem espaço.

  Coluna fora do esquema de 074, 079 e 083 também é violação.
- Saída: dez linhas e, com `--json`, só contagens, motivos e números. Código 0 quando tudo confere, 1 com violação
  (mesmo sem amostra), 2 sem linha da intenção no período.

Prova: `simulated` (`scripts/tests/test_jev_leitura_intencao.py`, com linhas falsas). O critério de fechamento é a
leitura com 10 ou mais linhas de intenção depois do T_on.

*Registro de 03/10 (17:19:44Z), leitura PRELIMINAR (`real`, parcial):*
- **Onde:** central WIN-7S2UASNLFOP, deploy 12 (`d5a1c3a9`, migração 082), script da main, mode=ro, `--desde
  2026-10-03T15:29:51Z` (o T_on exato do deploy 11).
- **Por que preliminar:** o P4 foi pausado às 17:18Z (K-086). A sombra ficou com 6 linhas = 6 chamadas = 6 comandos.
- **Resultado:** veredito OK, zero violação de formato.
  - R2: 4 respondidas (1 `nenhuma`), 1 `privacidade` (`c7_gatilho`) e 1 `abaixo_do_limiar` (confiança 0,56, maior
    probabilidade 0,59). R3: 0.
  - P(escolha) das respondidas: mínimo 0,88, todas acima do limiar.
  - Custo: 5 chamadas, US$ 0,000255; latência p50 445 ms e p95 520 ms. O cruzamento bate (5 × 5).
- O 31.10 segue `partial`.

## 8. O lote offline da intenção (R2 e R3 do 31.11)

*Pré-registro de 03/10 (~19:40Z), antes de qualquer rodada do lote.* Nenhum limiar da §3 muda, e nenhum número do lote
vale para GO. O código é `scripts/jev-braco-offline-intencao.py`, com `backend/app/taskqueue/lote_intencao.py`.

    backend/.venv/Scripts/python.exe scripts/jev-braco-offline-intencao.py [--db data/poc.sqlite3] \
        [--desde 2026-10-03T15:29:51Z] [--commits-da-janela <c1,c2,...>] [--json saida.json] [--md saida.md] \
        [--enviar --teto 0.05]

- **Amostra.** Entra a execução com linha de sombra de intenção desde 15:29:51Z de 03/10 (ADR-069 item 21; o
  `--desde` não pode ser anterior) e com prova de POST:
  - desde a 083: `postado=1`;
  - antes da 083: escolha preenchida, ou fallback `abaixo_do_limiar`, `unknown_choice` ou `parse`.

  Privacidade, `desligado`, `orcamento` e `rede` sem marca não provam POST.
- **Remontagem.** O código é o do runtime: `RunService.dados_da_sombra` sobre a foto da execução, a composição de
  habilidades do `AppState` e `ConsumidorDeIntencao.pedido`. Ele roda sobre o banco aberto só para leitura. A R3 é a
  RESOLVE de hoje sobre o catálogo de hoje, porque o empate não é gravado na sombra.
- **Só o hash libera envio** (orquestradora, 03/10 19:33Z).
  - O caso vai ao Jev só se o hash do estado remontado e redigido bate com o `estado_hash` da linha (31.22, migração
    086). A igualdade do texto fica PROVED caso a caso.
  - O hash que a porta registra no envio é conferido de novo; se for diferente, a rodada para.
  - Linha anterior à 086 fica fora, contada como `anterior_a_086`.
- **A salvaguarda "b" é só relatada e nunca libera envio.** Para a linha sem hash, o relatório diz quantas
  passariam, por três conferências:
  - os arquivos que decidem o texto (`ARQUIVOS_DO_FILTRO`: os 5 do filtro, mais `service.py`, `alvos.py`, o registro
    de apps e os `app.yaml`) iguais entre cada commit de `--commits-da-janela` e a árvore do lote;
  - nenhum nome saído do catálogo de destinos depois da linha: aparelho aposentado, lápide, persona ou conta
    removida, persona ou conta alterada;
  - os eventos persistidos alcançando a linha.

  Por que não libera: identidade de arquivos não prova igualdade do estado. A leitura da execução, o extrator e os
  nomes de app também decidem o texto.
- **Português × inglês (D-J7).** Cada caso vai duas vezes, intercalado (inglês, depois português), com o mesmo estado
  e as mesmas opções. As instruções em português ficam fixadas antes de qualquer rodada em
  `lote_intencao.INSTRUCOES_PT`. São a tradução literal das inglesas de `intencao.py`, que fica intocado:
  - R2: "O estado é um comando escrito pelo dono de um parque de aparelhos, em português, com nomes e números
    mascarados. Escolha a entrada do catálogo que este comando pede para executar, ou nenhuma se nenhuma entrada
    servir claramente."
  - R3: "O estado é um comando escrito pelo dono de um parque de aparelhos, em português, com nomes e números
    mascarados. Várias entradas do catálogo casam com ele igualmente. Escolha a que o comando pede, ou nenhuma se
    não der para saber."
  - A descrição da opção `nenhuma` não muda entre os idiomas.
- **O que o relatório traz.**
  - Por idioma e app, as medidas da §3, com as mesmas contas e os rótulos do relatório do 31.10.
  - Inglês × português: mesma escolha e mesma maior probabilidade.
  - O lote em inglês × a linha viva da mesma execução (estabilidade).
  - **Total e por origem do caso** (pedido da orquestradora, 03/10): `pessoa` ou `validacao` (a re-execução da
    validação do Aprendizado, `learning_validations.run_id`, que repete o comando da execução de origem). Cada origem
    traz casos, comandos distintos (por `estado_hash`) e as mesmas medidas; o `--r5` também.

  O idioma que perder sai antes de qualquer `on` (§1). "Perder" se decide sobre rótulos, com amostra, e não neste
  lote de acompanhamento.
- **Portões da rodada paga.**
  - `--enviar --teto` de no máximo US$ 0,05, numa rodada só, sem repetição.
  - Ao menos 10 COMANDOS DISTINTOS enviáveis (`MIN_COMANDOS_REAIS`, por `estado_hash`, das origens do piso; ver
    "Origem do caso" abaixo). Abaixo disso, a recusa vem antes de qualquer chamada.
  - O custo fica no JSON e no registro do estado, não em `ai_calls`.

**Origem do caso** (texto aprovado pela orquestradora às 20:28Z de 03/10, com 0 casos `c` existentes: é pré-registro;
gravado no commit que traz esta seção).
- Conta como `c` toda execução que a sombra da intenção mandou com hash, seja do dono (`pessoa`), seja re-execução da
  validação do Aprendizado (`validacao`, `learning_validations.run_id`), que repete o comando do dono.
- O piso de 10 (`MIN_COMANDOS_REAIS`) conta COMANDOS DISTINTOS por `estado_hash`, não execuções: a validação repete o
  texto, e repetir não prova o filtro de novo.
- Uma bateria neutra, se houver, é origem própria (`bateria`): entra no lote e no relatório separado, mas fica FORA do
  piso e do GO do 31.10, porque não é comando do dono.
- Comando resolvido por fluxo, com POST e hash, é `c` válido: a §8 não depende de o planejador ter decidido. Ele
  conta para o piso e para a precisão da R2 (rótulo = o fluxo que terminou em sucesso comprovado). Pela §3, NÃO
  entra na métrica principal do 31.10 (RA-2: só execuções sem fluxo, `sem_casamento`).
- A origem vem hoje de `lote_intencao.origem_da_execucao`. Passa a vir do contrato do 32.3 (`app/contracts/origem.py`)
  quando ele estiver na main, com um mapa para este vocabulário.

Prova `simulated`:
- `backend/tests/test_lote_intencao.py`, inclusive o `AppState` do harness: a sombra do runtime manda, e o lote remonta
  o mesmo estado e as mesmas perguntas;
- `scripts/tests/test_jev_braco_offline_intencao.py`.

*Registro de 03/10 (19:33:48Z), seco no central (`real`, só leitura, nada enviado).*
- **Onde:** worktree do ramo C, banco do central em `mode=ro`, `--commits-da-janela c8304e85,d5a1c3a9,1c54a7bb`.
- **Resultado:** 6 execuções lidas, 5 `anterior_a_086` e 1 `nao_enviado`; 0 enviáveis.
- **Salvaguarda b:** daria `diferente` nos três commits. O `privacidade.py` mudou com o 31.23 e o `service.py` depois
  do deploy 11. Nenhuma remoção desde 15:29:51Z; eventos desde 19/09.
- A rodada espera 10 comandos reais depois do deploy 14, que passa a gravar o hash.

## 9. Apps do comando (R5, 31.13)

*Pré-registro gravado em 03/10 20:17:10Z (commit 85777835, que era 42a63c5b antes do rebase sobre a suíte 14),
antes de qualquer rodada, com o sim da orquestradora ao desenho (19:48Z) e à opção A (resposta dela à inconsistência
do `app` no estado).* Nenhuma chamada paga foi feita para isto. O código é
`backend/app/planning/decisao_fechada/apps.py`, ligado pela sombra da intenção
(`taskqueue/sombra_intencao.py::ligar_apps`).

- **A pergunta.** É um `noul` por app do cadastro (`apps`): "cumprir este comando no aparelho exige o app X?". Os
  critérios são `true`/`false`, cada um nomeando o app.
  - O id é opaco por app (`app:` + sha1 do id do cadastro, 12 hex), e o nome nunca vai no id.
  - O nome (C2, o cadastro do dono) é o do cadastro mais o rótulo do manifesto quando difere ("Microsoft Outlook /
    Outlook"). São os mesmos nomes que a regex do caminho atual casa (`apps_do_comando.nomes_do_app`).
  - O nome passa por `motivo_c7` e por `mascarar_catalogo(redact(...))` antes do corte em 200 caracteres, como a
    descrição da R2. Nome C7, ou que o filtro esvazia, deixa o app fora do pedido.
  - Teto: 16 apps (`apps.MAX_APPS`). Acima dele, a R5 não vai, para não medir o que o Jev não viu; o central tem 7.
    O valor foi aceito pela orquestradora (03/10) e é revisável pelo custo medido: o relatório do `--r5` traz o custo
    por comando (uma chamada com N perguntas) e por app (a chamada rateada pelas N). Não muda sem avisá-la.
- **O estado é só o `comando`** (`CAMPOS_POR_ORIGEM["apps"] = {comando}`; opção A da orquestradora, 03/10). É o mesmo
  comando da intenção, com a mesma C7 e o mesmo filtro (`intencao.pedido_c3`). O `app` fica de fora porque, na hora da
  sombra, é o app PRINCIPAL do plano (`save_plan` → `runs.app_ids[0]`), que é o rótulo abaixo: com ele no estado, a
  métrica mediria um eco. O estado da R5 está contido no da intenção.
- **Emenda de 04/10/2026 (31.13, pela tabela do 29.75, aceita pela orquestradora às 21:05Z): na sombra, o limiar
  passa a 0,5.** A sintética deu 4 de 16 paráfrases pegas em 0,85 e 8 de 16 em 0,5, sem falso `sim`; a maior
  probabilidade de um `nao` foi 0,18. Não há caso real em 427 execuções. A linha da sombra grava a probabilidade, e o
  relatório do braço offline (`jev-braco-offline-intencao.py --r5`) mede nos dois limiares: as colunas `_0_85` contam o
  `sim` pelo `p_sim` de cada linha, e a métrica pré-registrada segue sendo a delas. Um `on` pede decisão e limiar
  próprios. O texto abaixo é o pré-registro original.
- **Limiar: 0,85 sobre a probabilidade devolvida do "verdadeiro".** Abaixo dele, a resposta conta como SEM resposta. O
  complemento nunca vira `nao` (B7 do roteiro: P(noul) ≠ 1 − P(não-noul)). Na linha da sombra, a escolha só pode ser
  `sim` ou vazia, e a probabilidade fica gravada mesmo abaixo do limiar.
- **Decisão real na sombra (acompanhamento):** a regex do caminho atual (`apps_citados`) sobre o comando ORIGINAL, o
  texto que o roteamento real lê (`RunService._app_do_comando`). Dá `sim` para o app citado e `nao` para os demais. O
  Jev vê o comando sem destinos e filtrado. Essa decisão real é o CONTROLE, não o rótulo.
- **Rótulo:** os `required_apps` do plano gravado (`parsing.apps_do_plano`: os apps em que as etapas rodam). Vale só em
  execução de sucesso comprovado, pela regra do 30.25 (`sucesso_comprovado`). É `sim` para os apps exigidos e `nao`
  para os demais. Sem plano, com a lista vazia ou sem sucesso comprovado, não há rótulo e a pergunta fica só na
  cobertura.
- **Métricas (por idioma, no lote; por estrato, na sombra):**
  - **precisão do `sim`:** dos `sim` do Jev com rótulo, quantos o rótulo confirma (na sombra, as colunas `acima` e
    `errado` do resumo);
  - **paráfrases pegas:** das perguntas com rótulo `sim` e controle `nao` (o que a regex perde, como "meu e-mail" →
    Outlook), quantas o Jev respondeu `sim`;
  - para comparar, a precisão e a cobertura do controle e a cobertura do Jev.

  A concordância (`escolha = decisao_real`) não se aplica ao `noul`: uma linha com real `nao` nunca casa.
- **Sem GO pré-registrado para `on`.** A R5 é candidatura (`Plan.required_apps` segue do planejador), e um `on` pediria
  decisão própria. O 31.13 só mede em sombra.
- **Travada no código:** `privacidade.R5_LIBERADA = False` até o GO do 31.10 (ADR-069 item 21: o filtro provado com 10
  comandos reais).
  - Virar é um commit, com suíte e deploy. O YAML `consumidores.apps` sozinho não liga nada.
  - Travada, a sombra não lê o cadastro nem monta pedido, e `/api/ai` não anuncia a R5.
  - O relatório do 31.10 ainda não lê a origem `apps`; ele passa a lê-la quando a R5 for destravada.
- **No lote offline (`--r5` do 31.11).**
  - Entram os mesmos casos `c` da §8. O hash provado é o da INTENÇÃO, igual ao `estado_hash` da linha.
  - O pedido da R5 só sai se o estado dele estiver contido no da intenção, com o MESMO comando: nada novo sai. O que
    não fecha fica em `fora_r5`.
  - O hash que a porta registra no envio da R5 é conferido contra o do pedido dela.
  - Ordem por caso: intenção e R5 em inglês, depois as duas em português.
  - As frases em português, fixadas antes de qualquer rodada em `apps.py`, são a tradução literal das inglesas:
    - instrução: "O estado é um comando escrito pelo dono de um parque de aparelhos, em português, com nomes e números
      mascarados. Responda verdadeiro se cumprir este comando no aparelho exige o app {nome}, e falso caso
      contrário.";
    - verdadeiro: "Cumprir o comando exige o app {nome}: abri-lo, agir nele ou ler algo nele.";
    - falso: "O comando pode ser cumprido sem o app {nome}.".

Prova `simulated`:
- `backend/tests/test_decisao_fechada_apps.py`, inclusive o `AppState` do harness, travado e destravado só no teste;
- `backend/tests/test_decisao_fechada_jev.py`: o `noul` no fio e o limiar na porta;
- `backend/tests/test_lote_intencao.py`: a sombra do runtime manda, e o lote remonta o mesmo pedido da R5;
- `scripts/tests/test_jev_braco_offline_intencao.py`.

Chamada real: `not_run` (incidente do parque desde ~19:00Z: nada pago nem real até a orquestradora liberar).

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

1. **Decisão da pessoa** sobre o item, em `learning_transitions` (`decided_by` ≠ `sistema`, `decided_at`, `from_state` →
   `to_state`), mapeada para a triagem: publicar ou manter → `manter`; rebaixar → `rebaixar`; desligar → `descartar`.
   Medido em 02/10 no central (só leitura): 48 transições, 2 de pessoa. O golden por decisão humana é quase vazio e cresce
   com a fila "Para aprovar" (30.17).
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

## 3. Intenção (R2 e R3, 31.9)

**Estratos (RA-2; execuções de 7 dias até 03/10 no central):** qa-messenger 58 (+3), instagram 15, outlook 5.

- **1º estrato: qa-messenger.** É o volume que chega ao mínimo de rótulos abaixo.
- **Instagram: GO sem data.** O relatório conta os rótulos do estrato e não projeta data. Na reavaliação, 13 de 15
  comandos tinham `@` e 11 de 15, número de 3 ou mais dígitos: a remoção anterior recusava esses comandos inteiros por
  construção. A corrigida (31.9, 03/10) mascara o `@` e o número em vez de recusar; a fração recusada passa a ser medida.
- **Outlook e os demais:** data pelo ritmo medido, como no §1.

**Rótulos:** o desfecho da execução (`casar_desfecho`, 31.10: a habilidade resolvida que terminou em sucesso comprovado) e,
no empate, a escolha da pessoa. Numa execução SEM fluxo, o rótulo é o **fluxo que o desfecho confirma**: o fluxo ativo que
faz o que a execução fez com sucesso comprovado, apontado pela pessoa ou pela revisão do relatório, com o id da execução.
Nunca é a escolha do Jev nem a concordância com a cadeia; sem esse rótulo, a execução fica só na cobertura. A decisão real
que a sombra casa hoje (o que a cadeia resolveu) é acompanhamento.

**Métrica principal do 31.10 (RA-2):** entre as execuções sem fluxo do estrato (`sem_casamento`: o planejador fez o
trabalho), quantas o Jev teria casado ao fluxo que o desfecho confirma. Por quê: as execuções com fluxo já não chamam o
planejador (0 chamadas `plan` em 51 de 51, 7 dias), e 168 de 172 sem fluxo chamam. O ganho em latência e custo só existe
onde o casador determinístico erra; concordar com a cadeia onde ela já casou não é ganho.

**Teto de cobertura da R2 (RA-2):** uma `choice` só resolve SEM pergunta um fluxo ou habilidade sem `{parâmetro}`: a etapa
semântica exige `achado.complete` (`intent_resolver.py:214`), e com parâmetro vira `needs_input`. No central em 03/10, 4 de
31 fluxos não tinham parâmetro. A cobertura da R2 usa esse denominador, e o relatório traz o teto do dia (fluxos sem
parâmetro sobre fluxos ativos). Um acerto num fluxo com parâmetro conta à parte, porque ainda pede o parâmetro à pessoa.

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

- Memória (conteúdo nunca sai), fluxo (C2, F2) e social/persona (D-J5).
- Qualquer chamada: o 31.7 não chama nada. A primeira medição real é o 31.10, nos tetos do ADR-069 (sem troca de chave: item 9).

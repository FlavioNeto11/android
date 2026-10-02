# Golden set e limiares pré-registrados do Jev (31.7)

**Estado:** PRÉ-REGISTRADO em 02/10/2026 (UTC; texto dos limiares no commit `ae2e5370`, branch `feat/31-8-curador-sombra`), antes de qualquer resultado do Jev (nenhuma chamada real feita; envio fechado no
código, `JEV_RUNTIME_SEND_APPROVED = False`). Frente Jev com a frente Aprendizado (resposta dela em
`.claude/handoffs/aprendizado/resposta-golden-31-7.md`). Base: ADR-069 item 6 ("`on` só por consumidor, com GO pré-registrado:
limiares escritos antes do primeiro resultado"). Mudar um número aqui depois do primeiro resultado exige registro datado e
diz por quê; o número antigo continua no histórico do Git. (Correção de 02/10 ~22:46Z: a primeira versão dizia "03/10"
por erro de data da sessão; os números não mudaram.)

## 1. Regras que valem para todo consumidor

- **Fallback nunca conta como acerto** (ADR-069 item 6). Toda linha com `fallback_reason` entra no denominador da COBERTURA e
  fica fora do de acerto.
- **`nenhuma` é abstenção:** não é acerto nem erro; entra na cobertura.
- **Rótulo antes de veredito:** nenhum consumidor tem veredito com menos de N rótulos no estrato (abaixo). Abaixo de N, o
  relatório diz "sem amostra", nunca uma taxa.
- **Braço de controle local e gratuito** em todo consumidor: o Jev só ganha GO se vencer a regra local na mesma amostra.
- **Estratos:** por `kind` (curador) e por app (intenção). Um GO vale por estrato; um estrato sem amostra fica `off`.
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
3. **Concordância com o curador principal** (a decisão real que a sombra casa hoje): só métrica de acompanhamento, NUNCA
   critério de GO sozinha. Mede se o Jev concorda com o Claude, não se acerta.

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

**Rótulos:** o desfecho da execução (`casar_desfecho`, 31.10: a habilidade resolvida que terminou em sucesso comprovado) e,
no empate, a escolha da pessoa. A decisão real que a sombra casa hoje (o que a cadeia resolveu) é acompanhamento.

**Controle:** a própria cadeia de hoje (`intent_resolver`), que é gratuita e já roda.

**GO para `on` (só sugerir, D-J7), por app:**

| Critério | Limiar | Origem |
|---|---|---|
| Comandos rotulados no estrato | ≥ 50 | proposta Jev |
| "Aceite errado" (sugestão do Jev que a pessoa aceitaria e o rótulo desmente) | ≤ 2 % | proposta Jev (D-J7) |
| Precisão das escolhas diferentes de `nenhuma` | ≥ 95 % | proposta Jev |
| Recusas por privacidade (entidade que sobrou) | relatadas; não entram no acerto | ADR-069 C3 |
| Ganho sobre a cadeia: comandos que ela deixa sem casamento e o Jev resolve certo | > 0, com a precisão acima | proposta Jev |

## 4. O que fica fora

- Memória (conteúdo nunca sai), fluxo (C2, F2) e social/persona (D-J5).
- Qualquer chamada: o 31.7 não chama nada. A primeira medição real é o 31.10, depois da troca da chave pelo dono.

# Hub de IA fora de execução: curador do Livro (30.12) e rubrica única de gasto — PROPOSTA

**Estado:** PROPOSTA com as decisões P1–P6 anotadas no §4 (orquestradora, 02/10); implementação do 30.12 PARADA até o roteiro do Jev e a D0. Autoria: frente Jev, dona do hub de IA (`planning/`, `routing`, `config.ai`), em 02/10/2026. **Nada
implementado.** Pedido da orquestradora (mapa de amarração M1, §3 "30.12" e "30.11, 28.6 e ADR-051"). Duas decisões
abertas podem mudar o desenho:

- a D0 do dono, sobre o sentido de "Jev para o fluxo de navegação";
- o roteiro do Jev-retrieval (`choice`), que traz a emenda ao ADR-063 e as classes de dado que podem sair da máquina.

Vocabulário: **hub de IA** = `planning/routing.py`, `config.ai`, `ai_calls`; **Jev-retrieval** = o provedor remoto de
`modules/context_retrieval` (ADR-063).

## 1. O que já existe (PROVED no código)

- Métodos do hub fora de execução usam o papel `plan` emprestado e `run_id=None`, como `generalize` (13.2),
  `orchestrate_targets` (ADR-050) e `refine_command` (ADR-047). Ver `routing.py:345-355`
  (`self._call("plan", None, …)`).
- `_budget` com `run_id=None` só aplica o teto **do dia** (`ai_max_usd_per_day`). O teto por execução fica de fora, e o
  orçamento do pedido (28.6) também, porque é por `run_id` (`routing.py:190-210`).
- `ai_calls` não tem coluna de origem. Uma chamada fora de execução fica com `run_id`, `objective_id` e `step_id` nulos
  e só se distingue pelo `role` (`001_init.sql`; colunas acrescentadas em 032, 033, 045, 055 e 064).

## 2. Proposta para o 30.12: adaptador do curador no hub

1. **Método novo no hub:** `AIRouter.review_knowledge(req) -> (Revisao, Usage)`, pelo precedente:
   `self._call("plan", None, …)`.
   - **Papel `plan` emprestado, sem papel novo.** Um papel novo se propaga a `AI_ROLES`, à validação de `config.py` e
     aos perfis do 17.7, que exigem todas as funções (risco 3 do mapa). Se o custo do `plan` (Opus) pesar, a troca
     de modelo se faz por **perfil de IA** (17.7: `ai.profiles.curador`), não por papel.
   - O pedido e a resposta são tipos do hub (`planning/schemas.py`). O Aprendizado entrega a **porta** `CuradorDeIA`
     e o adaptador simulado (30.11). O adaptador real mora em `modules/learning/infrastructure/` e chama o
     `AIRouter`, sem caminho paralelo.
2. **Marcador de origem em `ai_calls`:** coluna nova `origem TEXT`, migração com número a pedir à orquestradora.
   - Valores: `execucao` (NULL nas linhas antigas), `curador`, `ensino`, `orquestracao`, `assistente`, `persona` e
     `pedido` (28.10).
   - `ref TEXT` opcional, com o id do item de origem (para o curador, o `learning_reviews.id`).
   - Alternativa sem migração: codificar em `step_id` (`curador:<id>`). É REJEITÁVEL, porque mistura o significado da
     coluna e quebra quem agrupa por `step_id`.
   - `costs.spent_usd` ganha o filtro `origem=` para o relatório do Livro (M5), que hoje lê `ai_calls` por esquema.
3. **Teto próprio sem `run_id`:** `ai.limits.curador_max_usd_per_day`, uma fatia do teto do dia aplicada por
   `_budget` quando `origem='curador'`.
   - A fatia é `α × saldo disponível` (α = 10 %, decisão do dono, 30.11), limitada pelo teto do dia.
   - Erro `AIError(kind="budget")`, que a 30.13 já sabe tratar por `kind`, nunca pelo texto.
4. **Jev-retrieval como motor barato da triagem** (orientação da orquestradora, pendente do roteiro e da emenda ao
   ADR-063):
   - A triagem do curador é decisão por conjunto fechado (`manter / revisar / rebaixar / descartar`), o caso do
     `choice` (US$ 0,042/M de entrada, saída grátis).
   - O Claude (papel `plan`) fica só para o que exige TEXTO: a justificativa da revisão e a reescrita de lição.
   - No hub, a peça comum é uma **porta única de decisão por conjunto fechado**, com modo `shadow`, linha em
     `ai_calls` (`provider='jev'`, `origem`), orçamento, filtro de privacidade e fallback para o hub.
   - Só entra depois do roteiro e das classes de dado autorizadas a sair. Hoje, privado e sintético estão NEGADOS
     (ADR-063).
5. **Prova:** `simulated` com o provedor falso do hub, mais uma chamada real pontual (`[A]`, com teto e custo
   registrado) quando a 30.18 abrir o `shadow`.

## 3. Rubrica única de gasto (30.11, 28.6, 17.12, teto do dia e ADR-051)

Hoje há cinco réguas que não se conhecem:

- o teto do dia (`ai_max_usd_per_day`);
- o teto por execução (`ai_max_usd_per_run`);
- o orçamento do pedido (28.6, por ocorrência e total);
- o teto de chamadas por item (17.12, em chamadas, não em US$);
- o saldo da conta (ADR-051, que adia pedido abaixo do mínimo).

O 30.11 acrescentaria uma sexta, o α do curador. Proposta, numa ordem só e avaliada em `_budget`:

1. **Saldo da conta** (ADR-051): é o teto físico. Abaixo do mínimo, nada fora de execução roda (curador, pedido
   adiado). A execução pedida pela pessoa continua; ela vê o saldo no painel.
2. **Teto do dia**: o mesmo número para todos. As fatias abaixo são PARTES dele, nunca somas por fora:
   - curador: α × min(saldo, teto do dia) por dia (α = 10 %);
   - pedidos (28.6): o orçamento de cada pedido continua por pedido; a soma dos pedidos não tem fatia própria (decisão
     a confirmar com o dono: deveria ter?).
3. **Teto por execução** e **orçamento da ocorrência**: o menor dos dois, como já é hoje (`teto_usd_da_execucao`).
4. **Teto de chamadas** (17.12): continua em chamadas, porque protege contra laço, não contra preço. Não vira US$.

Implementação: um `_budget` só, com a ordem acima, e `AIError(kind="budget", motivo=<régua>)`. O campo `motivo` é novo
e fechado: `saldo | dia | fatia_curador | execucao | pedido`. A 30.13, o painel e o aviso leem o motivo, nunca a
frase. Precisa estar fechado ANTES de o 30.11 gravar `learning_reviews.usd`.

## 4. Decisões que esta proposta pede

| # | Decisão | De quem | Recomendação | Decidido |
|---|---|---|---|---|
| P1 | Papel do curador: `plan` emprestado ou papel novo | frente Jev, com o Aprendizado | `plan` emprestado, com perfil 17.7 para baratear | OK (orquestradora, 02/10) |
| P2 | Marcador em `ai_calls`: coluna `origem` (migração) ou `step_id` codificado | orquestradora (número) | coluna | OK; número na implementação (tende à 073) |
| P3 | Fatia do curador = α × min(saldo, teto do dia) | dono (α já decidido) | sim | OK (aplica o α=10 % do dono) |
| P4 | Fatia própria para a soma dos pedidos | dono | não por ora; medir no 28.12 | NÃO por ora; medir no 28.12 (orquestradora) |
| P5 | Triagem pelo `choice` do Jev-retrieval | dono (roteiro, ADR-063) | sim, em `shadow` primeiro | **APROVADO pelo dono (~21:00Z, 02/10), em `shadow` primeiro**; implementação espera o roteiro geral do Jev (porta comum de decisão por conjunto fechado e regra de dados) |
| P6 | `AIError.motivo` fechado | frente Jev | sim, junto com a rubrica | OK, junto com a rubrica |

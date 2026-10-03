# IA — hub, custo, modelo local

> Fonte principal para "como a IA é configurada, roteada e paga". Para o que foi medido em cada rodada, ver
> [docs/relatorio-validacao.md](relatorio-validacao.md); para o plano que originou o hub, `docs/plano-100.md`
> fases 0 e 7 (não editar); para produto e conceitos, [docs/produto.md](produto.md).

## 1. As seis funções

`AI_ROLES = ("plan", "decide", "verify", "escalation", "social", "persona")` (`backend/app/config.py`). Cada função tem
provedor, modelo, prazo e concorrência próprios, roteados por `RoutingProvider`
(`backend/app/planning/routing.py`):

| Função | O que faz | Volume típico |
|---|---|---|
| `plan` | Interpreta o objetivo em uma chamada estruturada: app, parâmetros, critérios de sucesso, etapas com dependência e pós-condição | 1 chamada por comando |
| `decide` | Observa a tela (screenshot + hierarquia) e escolhe UMA ferramenta tipada | ~90% das chamadas, junto com `verify` |
| `verify` | Confere a pós-condição da etapa por visão, quando a checagem determinística não basta | idem |
| `escalation` | Assume quando o modelo barato tropeça, em nova tentativa e em etapa com efeito externo (`strong_model_for_side_effect`) | minoria, mas mais caro por chamada |
| `social` | Escreve a mensagem na voz da persona; nunca recebe imagem nem credencial | 1 por interação social |
| `persona` | Gera e completa a persona (rascunho em texto); sem `ai.roles.persona` é o `social` (item 17.8) | 1 por persona gerada ou completada |

**Função opcional `leitura` (item 12.5, ADR-070)** fica FORA de `AI_ROLES`: só existe com `ai.roles.leitura` escrito, sem herança de nenhuma outra (§17).

**`generalize` (modo treinamento, item 13.2) não é uma sexta função registrada** — despacha no provedor/modelo do
papel `plan` (mesmo hub, sem `ai.roles.generalize` dedicado): `backend/app/planning/training.py` monta o pedido e
chama o provedor resolvido para `plan`.

**Geração de persona é a função `persona` (item 17.8; até 02/10 era o `social`).** `POST /api/personas/generate` e
`POST /api/personas/{id}/enrich` despacham pelo papel `persona`, que **sem `ai.roles.persona` herda o `social` por
inteiro** — provedor, modelo, prazo, vagas (o MESMO semáforo), fallback e esforço, inclusive nos perfis do 17.7 (a camada
`social` do perfil vale para ela) —, então nenhuma instalação muda sem mexer na configuração. Com bloco próprio ela
passa a ser outra função e o bloco do `social` deixa de valer para ela (campo que ela não escreve cai nos padrões, não
no social: herdar o modelo do social ao trocar de provedor daria um modelo que o destino não tem). Usa-se
`RoutingProvider.generate_persona` (`backend/app/planning/routing.py::RoutingProvider.generate_persona`, sem `run_id`:
vale o teto do dia, não o da execução), com `AIProvider.generate_persona` em todos os provedores
(`anthropic_provider.py`, `openai_provider.py`, `simulated_provider.py::persona_simulada`). O que sai da máquina é só
texto: o pedido do dono, as restrições e, no enriquecimento, o que a persona já tem; nunca tela, memória ou
credencial ([persona](dominios/persona.md#geração-por-ia-post-apipersonasgenerate)).

**O assistente do comando também não é função nova** (ADR-047). `POST /api/commands/refine` despacha pelo papel
`plan` (`RoutingProvider.refine_command`, sem `run_id` no roteamento: vale o teto do dia), com `refine_command` nos
três provedores. Prompt, esquema (pequeno, sem união: cabe na saída estruturada) e o refinamento simulado ficam em
`modules/execution/domain/command_refinement.py`. Sai da máquina: o comando, as respostas, os nomes dos apps, os
NOMES dos dados da persona dos alvos e as perguntas do planejador — nunca valor de credencial (o serviço recusa texto
com senha antes da chamada e redige a saída).

**O modo Automático também não é função nova** (ADR-050). `POST /api/runs/targets/suggest` despacha pelo papel
`plan` (`RoutingProvider.orchestrate_targets`) só quando a escolha depende do perfil das personas; prompt, esquema e o
simulado ficam em `modules/execution/domain/orquestracao.py`. Sai da máquina: o pedido sem destinos e o cartão de
até 20 personas candidatas (identidade, cidade, profissão, interesses, voz, crenças e disponibilidade) — nunca conta,
handle, credencial ou aparelho.

**O gerador de IMAGEM da persona fica fora de `AI_ROLES`** (`backend/app/config.py::ImageCfg`, bloco `ai.image`;
porta `modules/identity/application/ports.py::ImageGenerator`). Motivo: o saldo da Anthropic não compra imagem, então
é outro provedor (`simulated` por omissão, `openai` = `gpt-image-2` desde a Fase 17; o `gpt-image-1-mini` sai da API
em 01/12/2026), outra chave (`OPENAI_API_KEY`, lida como `SecretStr` em `EnvSettings`) e custo pelo `usage` da
resposta × `ai.image.price_per_mtok` (texto, imagem de entrada e saída). O `price_per_image[quality]` **declarado**
ficou como estimativa: confere o teto do dia antes de gerar e vale quando a resposta não traz `usage` (sem preço
para a qualidade, o mais caro da tabela, nunca zero). `_ia_coerente` não o
conhece e o hub não o roteia; `GET /api/ai` o expõe em `image` (`AiImageStatus`). O que sai da máquina no pago: o
prompt de atributos (nunca o nome da persona) e, da segunda imagem em diante, a imagem principal como referência.
**Sem fallback silencioso**, como no hub: pago sem chave responde 409 `image_not_configured`; recusa do filtro vira
`refused` e falha vira `failed`, nunca uma imagem simulada no lugar
([persona](dominios/persona.md#imagens-persona_images-migração-048)).

**O que o planejador e o ator recebem da persona (ADR-040): nomes, nunca valores.** Desde a onda B da segunda
evolução, `PlanRequest.available_data` e `StepContext.available_data` (`backend/app/planning/provider.py`) levam a
lista de "Dados da persona disponíveis" (`planning/prompts.py::dados_block`): para cada dado, nome lógico, rótulo
e tipo. Dado não sigiloso (`perfil_nome`, `perfil_email`, `conta_<app>_usuario`) é variável `{nome}` que o executor
resolve por aparelho na materialização; dado sigiloso (`conta_<app>_senha`) aparece só como **nome** para
`type_secret(name=…)`, e o valor sai do cofre direto para o campo pelo canal sensível. O planejador (livre e por
catálogo) recebe a lista **comum** a todos os aparelhos da execução (`available_data.common_data`); o ator e o
verificador, a do aparelho da etapa. App com `SessionProvider` (Instagram) não oferece senha ao modelo: entra
sozinho antes da tarefa. Quem monta a lista é `modules/identity/domain/available_data.py`, sem ler valor de
segredo. O campo `credentials` da execução (ADR-025) não existe mais; nenhum valor de credencial passa pelo
provedor de IA ([execução](dominios/execution.md), [persona](dominios/persona.md#contas-e-acesso)).

## 2. Como se configura modelo por função

Duas camadas, com precedência clara (`Config.ai_roles()`, `backend/app/config.py:716-746`):

1. **`.env`** — `AI_MODEL_PLANNER`, `AI_MODEL_ACTOR` (mapeia para `decide`), `AI_MODEL_VERIFIER`,
   `AI_MODEL_ESCALATION`, `AI_MODEL_SOCIAL`; vazio cai em `AI_MODEL`. É o suficiente para trocar modelo por
   função **sem tocar o YAML** — continua valendo mesmo depois do hub existir (`config.py:736-738`: "Sem bloco
   `ai.roles` no YAML o resultado é EXATAMENTE o de antes do hub").
2. **`config.yaml` → `ai.providers` / `ai.roles` / `ai.models`** — só entra em jogo se declarado. `ai.providers`
   nomeia endpoints (`kind: anthropic|openai|simulated`, `base_url`, `api_key_env`, `sends_data_externally`);
   `ai.roles.<papel>` aponta `provider`, `model`, `fallback_provider`, `timeout_s`, `concurrency`,
   `refusal_fallback`; `ai.models.<modelo>` declara capacidade (vision/tools/strict_tools/structured_output/
   thinking/effort/min_cache_tokens).

Herança quando `ai.roles.<papel>` falta um campo: `ROLE_DEFAULTS` fecha `timeout_s`/`concurrency`
(`config.py::ROLE_DEFAULTS` — plan 120s/4, decide 45s/8, verify 30s/8, escalation 60s/4, social 60s/4, persona 60s/4). Provedor não
declarado em `ai.providers` cai no do `.env` (`AI_PROVIDER`, padrão `anthropic`).

## 3. Capacidades declaradas (não descobertas)

Regra do hub (`routing.py`, comentário de topo): "capacidade é declarada, não descoberta — uma função apontada
para um modelo `vision: false` é erro de CONFIGURAÇÃO, o backend recusa subir". Antes disso, era aprendida por
erro HTTP 400 e reaprendida a cada reinício (17 recusas em 3 dias de log real, citado em
`scripts/probe-models.py`). Modelo ausente de `ai.models` é tratado de forma conservadora: sem strict, sem
thinking, sem effort, JSON por `json_object` (`config.py:762-776`, `model_caps`) — o certo para um modelo local
pequeno não declarado.

## 4. Fallback de provedor × fallback de recusa

Dois mecanismos diferentes, não confundir:

- **Fallback de provedor (explícito por papel).** Só existe se `ai.roles.<papel>.fallback_provider` for
  declarado; sem isso, a falha do endpoint sobe como erro — "sem fallback pago silencioso" é a primeira regra do
  hub. Cada queda vira evento na execução e linha própria em `ai_calls` (`requested_model`, `fallback`,
  `provider` — migração `032_hub_de_ia.sql`).
- **Fallback de recusa (`AI_REFUSAL_FALLBACK`).** É da Anthropic: reexecuta no servidor do provedor, em outro
  modelo, uma requisição recusada por classificador de conteúdo (beta `fallbacks: "default"`, destino
  documentado `claude-opus-4-8`). Só existe com `kind: anthropic`. Pode ser desligado por função
  (`ai.roles.<papel>.refusal_fallback: false`; recomendado para `social`).
  **Decisão registrada em 24/09/2026 (item 0.10 do plano-100):** o padrão global **fica ligado**, porque desde o
  item 7.2 ele é visível — linha na execução, colunas `requested_model`/`fallback` em `ai_calls`, aviso na aba
  IA. `.env.example:39-45` documenta a mesma decisão.

## 5. Cache de prompt

`backend/app/planning/anthropic_provider.py:151-156`: o bloco de sistema sempre carrega
`cache_control: {"type": "ephemeral"}` — corrigido em 24/09 (achado #100): antes só entrava se
`len(system)//4 >= min_cache_tokens`, uma subcontagem (o prefixo real de tools+system mede ≈6091 tokens) que
deixava Sonnet 5 e Haiku 4.5 sem cache (46 decisões medidas em 24/09 com `cache_read=cache_write=0`).
`min_cache_tokens` é declarado por modelo (`config.py:401-405`; `config.example.yaml` linhas 180-185): Opus 5.5/5
= 512, Sonnet 5/Opus 4.8 = 1024, Haiku 4.5 = 4096 — não é monótono entre gerações. Conclusão registrada em
`docs/relatorio-validacao.md §11`: com o prefixo do verificador ≈1 mil tokens, o cache no verificador em Haiku
4.5 é hoje **inerte** (abaixo do mínimo declarado do modelo) — decisão consciente de não mover o prefixo, porque
só compensaria acima de ~4096 tokens e o Haiku sem cache ainda sai mais barato que Opus com cache neste tamanho.

**Verificador (J9, 02/10):** o ponto de cache já era pedido no caminho do verificador e o custo já lê `cache_read`/`cache_creation`; o que faltava era prova. `simulated`: `backend/tests/test_anthropic_provider.py::test_verificador_pede_o_ponto_de_cache_e_o_uso_le_os_campos_de_cache`, `::test_uso_com_cache_vai_para_ai_calls_sem_contar_o_cache_como_entrada_nova` e `::test_prefixo_do_verificador_fica_abaixo_do_minimo_do_haiku`.

**Prova `real` (02/10, máquina central, `scripts/verifier-cache-probe.py`, base `origin/main` 5a50f80 + este PR):** o mesmo `VERIFIER_SYSTEM` e o ponto de cache do `AnthropicProvider`, tela sintética sem imagem (nenhum código do repositório saiu), `max_retries=0`, duas chamadas seguidas por modelo, teto US$ 0,50, gasto US$ 0,0241. O prefixo cacheável medido por `count_tokens` (system + esquema da saída) é de **~1 290 tokens**; a conta `len//4` do system sozinho dava ≈ 560 e subcontava, e a primeira versão deste parágrafo concluía errado que nem o Sonnet 5 alcançava o mínimo.

| modelo | mínimo cacheável | 1ª chamada | 2ª chamada | custo 1ª / 2ª |
|---|---|---|---|---|
| `claude-haiku-4-5` (padrão do verificador) | 4096 | cache 0 / 0 | cache 0 / 0 | US$ 0,001625 / 0,001615 |
| `claude-sonnet-5` | 1024 | gravou 1 290 | **leu 1 290** | US$ 0,004855 / 0,002028 |
| `claude-opus-5-5` | 512 | gravou 1 290 | **leu 1 290** | US$ 0,010238 / 0,003706 |

Conclusão `real`: **o cache do verificador funciona** (`cache_read > 0` na 2ª chamada) em Sonnet 5 e Opus 5.5 e é **inerte só no Haiku 4.5**, cujo mínimo (4096) é maior que o prefixo (~1 290). As 49 verificações de 25/09 com `cache_read=0` eram Haiku. Mesmo assim o Haiku sem cache (US$ 0,0016 por verificação) custa menos que o Sonnet com cache (US$ 0,0020 a partir da 2ª): trocar o modelo do verificador só para cachear não compensa neste tamanho; a medição não muda o padrão. Os ids das requisições não são expostos pelo caminho `verify` (só o uso de tokens).

## 6. Limites

| Limite | Onde | Nível |
|---|---|---|
| `ai_max_usd_per_run` / `ai_max_usd_per_day` | `backend/app/config.py:262-263` (padrão 15.0 / 0.0 — dia sem teto por padrão) | por execução / por dia, em US$, conferido em `_ai` no executor (cobre também planejamento e prévia de persona) |
| `ai_slots` | `backend/app/taskqueue/ai_slots.py` — uma LINHA por vaga, tomada por compare-and-swap no banco | teto global de concorrência de IA, entre backends (lease, não por processo) |
| `_RoleGate` | `backend/app/planning/routing.py` (`asyncio.Semaphore` por papel, dentro do limite de `ai_slots`) | prioridade relativa entre funções (não deixar `social` encher a fila do `verify`) |
| Disjuntor de conta | `backend/app/taskqueue/executor.py:83-233` (classifica cobrança/credencial; primeira falha represa a etapa sem gastar tentativa, pausa a execução, acusa em `/api/health` e na aba IA) | por execução, com liberação em `clear_ai_breaker` |
| Teto do dia antes da imagem paga | `backend/app/modules/identity/application/persona_images.py::PersonaImageService.conferir_orcamento` (só o gerador pago; o simulado não gasta e não confere) | por dia, o mesmo `ai_max_usd_per_day`; 409 `ai_budget` na rota antes de aceitar o pedido |
| Saldo da conta (ADR-051) | `backend/app/planning/saldos.py` + `RoutingProvider._saldo` (antes de cada chamada, também no destino do fallback) e `PersonaImageService.conferir_orcamento` | por conta (Anthropic, OpenAI, Gemini): `block_below` barra com `kind="balance"`; `warn_below` só avisa; ver §14 |
| Por objetivo | teto de tokens e de chamadas por objetivo (achado #99); a recusa por estouro agora grava linha em `ai_calls` mesmo sem chamada real (`_registrar_orcamento_estourado`). **Item 17.12:** o teto de chamadas é proporcional aos itens do `for_each`: `min(ai_max_calls_absolute, ai_max_calls_per_objective + ai_max_calls_per_item × (itens − 1))` (padrões 60 / 12 / 300; 8 itens = 144; sem lista ou com 1 item, 60 como antes). Os itens saem das etapas gravadas (`item_index` em `steps.variables`, `executor.py::_teto_de_chamadas`), o rejulgamento continua contando e a mensagem diz de onde veio o teto ("60 + 12 × 7 itens do for_each") | por objetivo |
| Por etapa, medido (item 18.3) | `taskqueue/projecao.py` + `executor.py::_conferir_orcamento_da_etapa`: mediana e p90 de chamadas por (app, ação) nas etapas concluídas da janela; acima do p90, aviso na linha do tempo; acima de `max(p90 × ai.step_budget.p90_factor, p90 + slack)`, a etapa para (`budget`). Ação sem `min_samples` amostras não tem orçamento próprio. A mesma medição projeta o plano (`GET /api/runs/{id}/projection`) | por etapa |

### Rubrica única de gasto e fatias por origem (31.2 e 31.6)

Toda recusa por dinheiro do hub sai como `AIError(kind="budget", motivo=<régua>)`. O `motivo` é um vocabulário FECHADO
(`planning/provider.py::MOTIVOS_DE_ORCAMENTO`) e só existe com `kind="budget"`: o painel, o aviso e a 30.13 leem o
motivo, nunca a frase. A ordem em que `RoutingProvider._budget` confere (a primeira que estourar vence) e o que cada
motivo quer dizer:

| Ordem | `motivo` | Régua | Aplica a |
|---|---|---|---|
| 1 | `pedido` | orçamento do pedido persistente (28.6), por ocorrência | só execução nascida de pedido com orçamento |
| 2 | `execucao` | `ai_max_usd_per_run` | só chamada com `run_id` |
| 3 | `dia` | `ai_max_usd_per_day`, o mesmo número para todos | toda chamada |
| 4 | `fatia_curador` | `ai.limits.curador_max_usd_per_day`; sem valor, `curador_fracao_do_dia` (α = 0,10) × teto do dia | `origem='curador'` |
| 4 | `fatia_jev` | `ai.limits.jev_max_usd_per_day` (padrão US$ 0,50; D-J3 aprovada pelo dono no ADR-069) | `origem='decisao_fechada'` |
| (fora de `_budget`) | `saldo` | saldo da conta (ADR-051), em `RoutingProvider._saldo` | hoje sai com `kind="balance"`; o valor já está no vocabulário para a etapa que o unificar |

- A fatia é PARTE do teto do dia, nunca soma por fora: a chamada passa no dia E na fatia da própria origem, e uma fatia
  estourada barra só aquela origem. O gasto da fatia é o do dia filtrado por origem (`costs.spent_today_usd(origem=)`,
  `ai_calls.origem`, migração 073). `0` desliga a fatia; curador sem valor explícito e sem teto do dia fica sem fatia.
- O vocabulário de `origem` (`ORIGENS_DE_IA`): `execucao` (padrão com `run_id`), `ensino` (`generalize`),
  `orquestracao`, `assistente` (`refine_command`), `social`, `persona`, `curador` (`review_knowledge`, 30.12) e `decisao_fechada`. Linha antiga
  fica NULL. Quem pede passa `origem=`/`ref=` a `RoutingProvider._call`; o `Usage` leva os dois e `add_usage` grava.
- Os tetos de CHAMADAS e de tokens do executor (17.12 e 18.3) continuam em `kind="budget"` sem `motivo`: protegem
  contra laço, não contra preço, e não são uma régua de dinheiro.
- Passos seguintes, não feitos aqui: levar o saldo da conta (ADR-051) para dentro da mesma ordem, com `motivo="saldo"`,
  e α sobre `min(saldo, teto do dia)`. Desenho: `docs/design/hub-de-ia-fora-de-execucao.md` §3.

## 7. Receitas, fluxos e provas locais

Ver [docs/produto.md §2](produto.md) para os conceitos. Mecanismo de custo, resumido:

- **Receitas** (`ai.recipes: replay`): a IA aprende uma etapa uma vez; repetição por seletor (resource-id/texto)
  sem nova chamada. Seletor que não casa exatamente um elemento → divergência (só aquela etapa volta para a IA).
  3 falhas seguidas → quarentena. `shadow` aprende e compara sem agir. Desde 29/09 a receita da IA nasce `candidate` e
  sobe por `ai.recipes_promote_after` (2) concordâncias em sombra; com ação `commit`, para em `validated` e espera o
  dono (D1 do [ADR-054](decisoes.md#adr-054--aprendizado-contínuo-livro-de-aprendizado-com-ciclo-de-vida-publicação-sozinha-só-sem-efeito-externo-d1-feedback-implícito-com-botão-opcional-d2-lições-medidas-e-backlog-do-que-mais-falha)).
- **Herança da receita** (`ai.recipes_heranca: true`, RA-20 / item 29.40): a etapa sem receita na chave atual (versão,
  assinatura e variante) herda, como candidata, a receita provada da mesma etapa noutra versão do app, noutra variante
  ou na legada de 17/09 (sem assinatura nem variante). A herdeira não age: a IA decide a etapa e ela só é comparada até
  concordar `recipes_promote_after` vezes; com `commit`, para em `validated`. Assinatura diferente nunca doa; com
  `recipes_promote_after: 0` não herda. A herdeira que passa a agir aposenta a legada ativa da mesma etapa e versão. A
  causa de cada "ausente" é medida em `receita.ausente{causa}` ([dominios/aprendizado.md](dominios/aprendizado.md)).
- **Fluxos** (`ai.flows: true`): execução 100% comprovada vira plano congelado; comando repetido com outros
  parâmetros pula o planejador. Desde 29/09 o fluxo aprendido nasce `candidate`, inerte: o comando seguinte ainda chama
  o planejador, e o plano dele é comparado ao do candidato. Com `aprendizado.fluxo.concordancias` (1) concordância real
  e sem etapa de efeito, o sistema o publica; com efeito, ele espera o dono. Ou seja, um comando novo sem efeito paga o
  planejador duas vezes antes de o fluxo valer ([dominios/aprendizado.md](dominios/aprendizado.md)).
- **Funil medido** (evolução de desempenho, ADR-027): consulta de receita (`receita.consulta{encontrada|candidata|
  herdada|ausente|quarentena}`, com a causa do "ausente" em `receita.ausente{causa}`), reprodução (`receita.reproducao{ok|divergiu}`) e retorno à IA (`receita.retorno_ia{motivo}`),
  em `GET /api/desempenho`. `GET /api/flows/cobertura` traz `aproveitamento` por fluxo e app: etapas elegíveis, por
  receita, receita + IA e só IA, "sem cobertura" separado de "receita de outra chave ou em quarentena", e chamadas de
  IA evitadas estimadas (`taskqueue/aproveitamento.py`).
- **Receita divergida não escala de modelo sozinha**: a IA assume a etapa no modelo de ação, e só sobe pelos
  controles de sempre (erros seguidos, repetição, efeito externo). O código nunca escalou; o comentário que
  prometia isso foi corrigido. Escalar na divergência é **decisão do dono pendente**, com o custo medido no
  [relatório](relatorio-desempenho.md): 22 etapas `recipe+ai` em 7 dias.
- **App de prova no tier 0 do `by_risk`** (item 29.31, RA-8 da reavaliação de 03/10): a etapa SEM capability do app de prova
  (`builtin` e `apps.category='qa'`: o QA Messenger embutido, que nasce `qa` no seed e na migração 041) não escala por efeito externo.
  `category='qa'` sozinho não basta, porque `POST/PUT /apps` o aceitam em qualquer app.
  Sem catálogo, "risco desconhecido" mandava toda etapa de envio ao modelo forte: 64 a 66 escalonamentos em 7 dias,
  43 % das chamadas do Opus no tier 1, cerca de US$ 0,20 por dia, e uma bateria de prova distorcida. A regra lê o dado
  do app, nunca o nome (ADR-052). `strong_model_for_side_effect: true` continua subindo tudo (escolha explícita), etapa com
  capability segue o risco do catálogo, e app real sem catálogo continua no tier 1. Retentativa, erros seguidos e ciclo
  escalam em qualquer app.
- **Desbravador** (`ai.pathfinder_wait_s`): visível (`wait_reason: pathfinder`), medido, agrupado por
  compatibilidade do app e solto na hora quando o líder falha ou sai do ar
  ([`dominios/parque.md`](dominios/parque.md#escalonamento)).
- **Imagem sob demanda**: com `image_policy: auto`, a observação lê a árvore primeiro e só captura imagem quando
  a decisão, o julgamento, a evidência ou a divergência pedem (ADR-027). Antes, o screencap e a codificação
  aconteciam mesmo quando a imagem não ia ao modelo.
- **Provas locais** (item 7.5): `backend/app/taskqueue/proofs.py` — conferência determinística pela árvore local
  (`==` exato em `find_selector`) antes de chamar o modelo, no catálogo do Instagram. Medido: Instagram 2/3→3/3
  de sucesso comprovado, US$0,20→0,08 por caso.

## 8. Avaliação — todos gastam API

| Script | O que faz | Gasta |
|---|---|---|
| `scripts/eval-run.ps1` | Bateria congelada (`config/eval-set.yaml`): sucesso comprovado × US$ por configuração | sim, pede `-Yes` |
| `scripts/eval-rejudge.ps1` | Rejulga com o modelo caro (Opus 5), por imagem, capturas que o barato já julgou; mede concordância. Com `scripts/eval_rejudge.py --sobrepor <yaml>` (Fase 17), julga as MESMAS capturas com o provedor do papel `verify` do arquivo sobreposto e compara com o Opus de `data/eval-rejudge.jsonl` (falso positivo e negativo), sem escrever no `config.yaml` | sim, pede `-Yes`/`--yes` |
| `scripts/probe-models.py` | Sonda o que cada modelo aceita e se o cache pega; com `--yaml` já imprime o bloco `ai.models` pronto | sim (poucos centavos), pede `--yes` |

Nenhum dos três foi rodado na rodada de 23-24/09 por regra da chamada (sem gasto pago sem autorização).

**Linha de base da configuração atual (item 7.4, `real`, 02/10/2026):** central, deploy `25624c4` (cascata e rejulgamento do
17.10 ligados), ator `claude-sonnet-5`, verificador `claude-haiku-4-5`, plano e escalonamento `claude-opus-5-5`, receitas em
`replay` e fluxos ligados; android-05 sem conta real; os 14 casos do QA Messenger (os `ig-*` ficam fora: conta real).
**13/14 corretos por US$ 1,449** (≈US$ 0,10 por caso; o mais caro, `msg-todos-os-contatos`, US$ 0,59). A falha é
`msg-todos-os-contatos` (`r-20261002181642-eff15b`): 6 de 8 envios comprovados e "Limite de 60 chamadas de IA por objetivo"
no 7º — o rejulgamento do 17.10 soma uma verificação do modelo forte por envio, e o teto por objetivo não acompanha o tamanho
do `for_each` (decidido no item 17.12: teto proporcional aos itens, com o rejulgamento DENTRO do teto e um absoluto; ver §6). A referência de
25/09 (16/17, US$ 1,43) rodou com o ator em fallback. O rejulgamento das 56 capturas não foi repetido (mesmas capturas, mesmo
modelo: vale o de 25/09, 41/56, US$ 1,14). HTTP 500 do verificador: 2 de 423 chamadas desde 19/09 (0,5%); os "~10 %" do
achado não se confirmam nos dados.

## 9. Observabilidade

- `ai_calls` — uma linha por chamada de IA (papel, modelo, tier, tokens, `ms`, `ok`, `error_kind`,
  `requested_model`/`fallback`/`provider`).
- `ai_calls.usd` (migração 048): custo **declarado** de uma chamada cobrada por unidade; hoje só as imagens da persona
  (`role='image'`, `modules/identity/infrastructure/persona_images.py::AiCallsAccounting.record`). `planning/costs.py::spent_usd`
  soma `usd` onde existe e tokens × preço onde é nulo; `provider='simulated'` segue fora do gasto. `GET /api/usage` e
  `GET /api/desempenho` ainda não leem `usd` (desvio relatado na onda A).
- `GET /api/desempenho` (ADR-027): métricas agregadas do processo (captura, observação, receitas, desbravador,
  reserva) e, com `?dias=N`, o histórico com p50/p95/n por papel e por **modelo efetivamente executado**, com
  fallback por motivo, tokens (novo, cache lido, cache gravado, saída) e taxas de sucesso, falha, incerto e espera
  humana por objetivo (`backend/app/desempenho.py`). É o que `scripts/bench.py leitura` lê.
- `GET /api/usage` (`backend/app/api.py:414`) — custo por papel/modelo/tier dos últimos N dias (padrão 7),
  chamadas por execução/objetivo, erros por tipo, linhas de fallback. Não chama o provedor, só lê o já gasto —
  mesma fonte que `scripts/usage-report.ps1`.
- Aba IA do painel mostra o mesmo por execução, incluindo cache ativo/inativo por papel.
- Estimativa por fluxo antes de rodar: `GET /api/flows/cobertura` ganhou `estimated_usd` (item 7.7) — etapas sem
  receita × custo mediano por etapa só-IA dos últimos 7 dias, por papel; sem histórico, `null` ("sem base").

## 10. Modelo local — Ollama

> **Desde 25/09/2026 o ator de produção é o Sonnet 5** ([ADR-023](decisoes.md)): o local economizava ~US$ 0,02 por
> caso, com o dobro de escalonamento para o Opus e fragilidade operacional, e estava fora do ar sem aviso. O
> provedor `local` continua definido no `config.yaml`; o texto abaixo é o registro de 24/09 e o caminho de volta.

**Estado em 24/09/2026 (fora do que está em `config.example.yaml`, que é o exemplo neutro — isto é produção; o
`config.yaml` não é versionado, e o que roda de fato se confere em `GET /api/ai` e `GET /api/health` → `ai`):** o ator de produção (`decide`) foi ligado no Ollama nativo do
Windows no host central, modelo `qwen3-vl:4b-instruct-16k` (variante **`-instruct`**; a tag sem esse sufixo é
"thinking" e devolve `content` vazio gastando a saída em raciocínio — `think:false`/`reasoning_effort` são
ignorados por ela), com fallback explícito para Anthropic. `ai.providers.<nome>.kind: openai` é o mecanismo
genérico (`backend/app/planning/openai_provider.py`) — qualquer servidor `/v1/chat/completions` serve, Ollama é
só o alvo escolhido.

**O que muda no provedor `openai`, em relação ao Anthropic** (comentário de topo de `openai_provider.py`):
ferramentas traduzidas para o formato `tools` do OpenAI; saída estruturada degrada por declaração
(`structured_output: json_schema|json_object|none`, nunca descoberta por erro); imagem por `image_url`
(data URI base64); modelo sem `vision: true` declarado nem chega a receber imagem. **O que não existe:**
`cache_control`, `thinking`, `output_config.effort` — são da Anthropic; um endpoint compatível recusaria a
requisição. `cache_read_tokens` vem de `prompt_tokens_details.cached_tokens` quando o servidor reporta, zero
quando não.

**Medido (24/09, execução real `r-20260924161755-555261`, caso QA):** 18 decisões locais + 6 escalonadas ao
Opus = US$ 0,18 — **empate de custo** com o ator em Sonnet, não economia; "o 4B vagueia" é a leitura registrada.
Economia de verdade exigiria escalonar o ator local para Sonnet (não Opus) ou testar um modelo 8B, decisão ainda
não tomada.

**Notebook da LAN (worker remoto) é inadequado para IA**, medido 24/09: GPU de 4 GB (Quadro T1000) roda o mesmo
modelo 60-85% em CPU, ~11-22s por tela quente contra ~2s no central, chegando a 100% de CPU e derrubando
emuladores (reparo automático reiniciou/resetou aparelhos do parque durante o teste). O worker do parque nunca
deve rodar IA local enquanto hospeda emuladores.

Preparação sem ligar nada (item 7.8, código já existe, decisão de ligar é do dono):
`ProviderCfg.extra_body` repassa opções livres ao corpo da requisição (Ollama: `options.num_ctx` — o tamanho de
contexto fica no **servidor**, `OLLAMA_CONTEXT_LENGTH` ou o Modelfile; sem um dos dois o padrão do Ollama trunca
o prompt em silêncio); piso de conteúdo em provedor local tier 0 (decisão cujo `element_id` não existe em
`obs.tree` não age, sobe para tier 1); `scripts/ollama-local.ps1` só confere ambiente e imprime o bloco de
config — nunca instala ou baixa nada sozinho.

## 10b. Cascata para ator barato (item 17.10)

Duas regras que tornam seguro um ator ou verificador baratos (Haiku, `gpt-6-luna`, modelo local), ambas em `backend/app/taskqueue/executor.py`, ambas ligadas por padrão e com chave em `ai.*`:

1. **`cascade_blocked_to_tier1`:** um `step_blocked` do tier 0 (kinds `unexpected_screen`, `missing_info`, `app_incompatible`, `other`) não pede uma pessoa na hora: o modelo de escalonamento olha a MESMA tela, **uma vez por tentativa**; se ele também relatar o bloqueio, vale o caminho de sempre (`waiting_user` ou falha). `challenge`, `auth_required` e `wrong_account` **nunca sobem**: dependem de pessoa ou do autenticador (ADR-009, ADR-055). Não age com efeito já disparado (continua `uncertain`), nem em decisão de receita, nem quando a etapa já decide no tier 1. A linha da execução diz o motivo ("bloqueio relatado pelo modelo de ação").
2. **`rejudge_yes_on_side_effect`:** o "sim" do verificador barato numa etapa com efeito externo (ou que confirma o nível de entrega dele) é conferido UMA vez pelo modelo de escalonamento, e o veredito do mais forte é o que vale (discordou, não conta como prova). É a outra metade do B14/7.10, que já rejulgava o "não". Só age quando o modelo do verificador é DIFERENTE do de escalonamento.

- **Custo:** a regra 1 só custa quando o ator barato bloqueou (antes era uma pessoa); a 2 soma uma verificação do modelo forte (US$ 0,004 a 0,010 pelos preços observados em 02/10) por etapa com efeito aprovada. Os dois botões existem para a bateria poder medir com e sem.
- **Prova:** `simulated`, `backend/tests/test_cascata_ator_barato.py` (12): sobe ao tier 1 e resolve, uma subida só, os três kinds de pessoa não sobem, chave desligada, forte concordando fecha, forte discordando não conta, mesmo modelo não rejulga. Mutações das duas regras derrubam os testes.
- **`real` (02/10/2026 17:49–17:54Z, central WIN-7S2UASNLFOP, deploy `25624c4`, android-05 sem conta real, `eval_run.py --instances android-05`, US$ 0,2955 no total, teto 0,50; ator `claude-sonnet-5`, verificador `claude-haiku-4-5`, escalonamento `claude-opus-5-5`):**
  - **Regra 2 provada:** `r-20261002175209-1a252b` (`msg-qa001`, `succeeded`, US$ 0,0467): na etapa "Enviar a mensagem", "o verificador aprovou uma etapa com efeito externo; conferindo com o modelo de escalonamento"; `ai_calls`: `verify` Haiku ×2 e Opus ×2.
  - **Regra 1 `not_run`:** `r-20261002175004-ea377e` (`perfil-campo-inexistente`, `waiting_user`, US$ 0,1926) — o tier 0 declarou `step_done` em "Abrir a tela de Perfil", o verificador reprovou e a 2ª tentativa subiu ao tier 1 pela regra ANTIGA (nova tentativa da mesma etapa); quem relatou `step_blocked` foi o tier 1. A cascata não foi acionada porque o tier 0 não bloqueou. Não se cacou outro gatilho com chamada paga.
  - **Negativo:** `r-20261002175257-0e9362` (`sessao-expirada`, `waiting_user`, US$ 0,0562): nenhuma chamada de escalonamento, mas quem barrou foi o detector determinístico de tela sensível (campo de senha), antes da IA — o `auth_required` vindo da IA não foi exercitado.
  - A medição do `gpt-6-luna` como ator continua por fazer (agora com `eval_run.py --profile`, item 17.7).

## 10c. Perfil de IA por execução e canário (item 17.7)

Trocar o modelo de uma função para **algumas** execuções, sem reiniciar o central e sem mexer no que as outras usam:

- **`ai.profiles.<nome>.roles.<função>`** vale por cima de `ai.roles`, **campo a campo**: o perfil que só troca o
  modelo do `decide` deixa as outras quatro funções e os outros campos do `decide` iguais ao padrão. A diferença entre
  os braços do A/B é só o que está escrito no perfil.
- **Escolha por execução:** `POST /api/runs` com `ai_profile` (nome de `ai.profiles`; desconhecido = `422
  ai_profile_desconhecido`, antes de criar a execução). Na bateria, `scripts/eval_run.py --profile <nome>` (grava
  `ai_profile` em `data/eval-results.jsonl`).
- **Canário:** `ai.canary: {profile, fraction}` sorteia, por execução, a fração das execuções SEM perfil escolhido. O
  sorteio é injetável (`RunService.sorteio`) e o resultado fica na execução.
- **A execução guarda o perfil e a origem** (`runs.ai_profile`, `runs.ai_profile_source` = `explicit`|`canary`,
  migração 064; também em `RunSummary`). A comparação junta `ai_calls` por `run_id`.
- **Um hub só** (`planning/routing.py`): os perfis são outras linhas de função dentro do mesmo `RoutingProvider`, com o
  mesmo teto em US$ por execução e por dia, as mesmas vagas por função (divididas entre perfis) e as mesmas instâncias
  de provedor quando a combinação coincide. O perfil vale para TODA chamada que leva o `run_id` (plano, ação,
  verificação, escalonamento); a prévia de persona e o refino do comando não têm execução e ficam no padrão.
- **Conferido na partida:** função desconhecida, provedor ausente, modelo sem capacidade declarada e canário apontando
  para perfil inexistente recusam a configuração, como em `ai.roles`. Perfil gravado numa execução que **sumiu** da
  configuração depois não cai no padrão em silêncio: a chamada falha com `not_configured` dizendo o nome do perfil
  (cair no padrão faria a execução dizer que rodou no candidato sem ter rodado).
- **Aviso de dados:** um perfil que manda função para fora desta máquina entra no aviso do `GET /api/ai`
  ("Perfis de IA que enviam dados para fora desta máquina: …") e liga `sends_data_externally`.
- **O que NÃO muda:** o modelo que a aba IA mostra é o do padrão (o `/api/ai` não conhece a execução); a do perfil está
  em `ai_calls.model`. O painel não tem seletor de perfil ainda (só API e bateria).
- **Prova:** `simulated`, `backend/tests/test_perfil_de_ia.py` (16) e `scripts/tests/test_eval_run.py`. **`real`:
  `not_run`** — o primeiro uso real é a bateria do 17.10 (`gpt-6-luna` como ator), que depende de saldo e autorização.

## 11. Tabela: `config.example.yaml` × padrão do código

O exemplo é a POC "neutra"; os valores entre parênteses no próprio arquivo já documentam o padrão de código.
Confirmado em `backend/app/config.py` (`AiCfg`, `LimitsCfg`). Os números de linha mudam a cada edição; procure pelo
nome da chave. A produção roda com os valores do exemplo (lidos em `GET /api/health` `features`, 26/09/2026):

| Alavanca | `config.example.yaml` | Padrão do código (`config.py`) |
|---|---|---|
| `ai.screenshot_max_side` | 768 | 1280 (`config.py`) |
| `ai.max_hierarchy_elements` | 60 | 140 (`config.py`) |
| `ai.image_policy` | `auto` | `always` (`config.py`) |
| `ai.recipes` | `replay` | `off` (`config.py`) |
| `ai.recipes_heranca` (RA-20) | `true` | `true` (`config.py`) |
| `ai.image.provider` / `model` / `per_persona` / `on_create` | `simulated` / `gpt-image-2` / 1 / `true` | os mesmos (`config.py::ImageCfg`); `quality: medium`, `price_per_image` (estimativa) low 0,02 / medium 0,06 / high 0,2 US$, `price_per_mtok` 5 / 8 / 30 US$ |
| `ai.limits` (fatias por origem, 31.6) | comentado, com os padrões | `curador_max_usd_per_day` vazio (= 0,10 × teto do dia), `curador_fracao_do_dia` 0,10, `jev_max_usd_per_day` 0,50 (`config.py::AiLimitsCfg`) |
| `ai.flows` | `true` | `false` (`config.py`) |
| `ai.pathfinder_wait_s` | 240 | 0 (`config.py`) |
| `android.auto_start_devices` | `true` | `false` (`config.py`) |
| `android.max_online_devices` | 2 — comentado como vagas desta máquina | 10 (`config.py`, teto do host; cada worker traz o próprio `max_slots`) |

## 12. O que foi MEDIDO (não confundir com configuração prevista)

- **Bateria de 25/09/2026** (`relatorio-validacao.md` §11.1): 16/17 casos corretos, US$ 0,084 por caso; rejulgamento
  Opus 5.5 × Haiku em 41/56 (73 %). **O ator estava no fallback** (Sonnet 5), porque o Ollama não estava no ar, e a
  saúde não acusou. Cache do verificador em Haiku: zero em 49 chamadas.

- `docs/relatorio-validacao.md §5` — validação com o provedor real (`claude-opus-5`): mensagem em 1 e 3
  aparelhos, formulário, app nunca visto, falhas injetadas, controle manual + retomada, `kill` do backend em
  execução.
- `docs/relatorio-validacao.md §7` — otimização de RAM e de custo de IA, segunda rodada (17/09).
- `docs/relatorio-validacao.md §11` — item 7.4, bateria de avaliação: o que foi corrigido e testado sem gasto, e
  o que segue bloqueado por decisão/orçamento (linha de base × configuração atual nunca rodou).
- `docs/claude-plano-100.md` — custo de **executar o plano** (orquestração de IA para fazer as fases, não custo
  de produção): três economias (pacote auto-contido por item, sem carregar conversa inteira, modelo por
  complexidade do item) — `scripts/plano-100-custo.py` lê o já gasto, nenhum dos scripts de orquestração chama
  IA diretamente.

**Crenças no bloco `<persona>` (28/09, ADR-048).** Depois da biografia curta vêm as crenças, quando existem: uma
seção para religião e outra para política, um campo por linha, valores fechados em português, tudo por
`sem_marcacao`; em seguida a linha fixa "conduta sobre crenças" (`CONDUTA_DAS_CRENCAS`: coerência de valores e tom,
nunca propaganda, pedido de voto ou adesão, desinformação ou ataque a grupo). `SOCIAL_SYSTEM` manda usar as crenças
como coerência, não como assunto. A geração de persona pede crenças ricas e variadas, com teto de 10000 tokens.

## 13. Provedores em nuvem por papel (Fase 17)

Pesquisa e plano: [pesquisa-provedores-ia-2026-09-28.md](pesquisa-provedores-ia-2026-09-28.md) e
[plano-provedores-ia-2026-09-28.md](plano-provedores-ia-2026-09-28.md). Decisão: ADR-049.

- **Chave pelo `.env`.** `ai.providers.<nome>.api_key_env` é resolvido por `EnvSettings.chave`: campo declarado
  (`OPENAI_API_KEY`, `GEMINI_API_KEY` ou o apelido `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`), que o
  pydantic lê do `.env`; nome não declarado cai em `os.environ`. Antes só valia `os.environ`, que não vê o `.env`:
  a primeira chamada a um provedor em nuvem voltaria 401 (o Ollama não usa chave e escondeu isso).
- **Parâmetros por modelo** (`ai.models.<m>`): `max_tokens_field` (`max_completion_tokens` na OpenAI, que dá
  `max_tokens` como obsoleto) e `extra_body`, aplicado depois do `extra_body` do provedor. O `gpt-6-luna` só chama
  ferramenta no Chat Completions com `reasoning_effort: none`; o Gemini 3 não desliga o raciocínio pela camada
  compatível (`minimal` é o menor); o `deepseek-flash` desliga com `thinking: {type: disabled}`.
- **Saída estruturada por `json_object`** nos três: o esquema vai no texto e o Pydantic garante (K-042). O strict da
  OpenAI e o `json_schema` da camada do Gemini não foram provados com os esquemas deste projeto.
- **Candidatos declarados em `config.example.yaml`** (capacidade e preço de lista de 28/09/2026). Ligar um papel é
  `ai.providers` + `ai.roles`, sempre com `fallback_provider: anthropic` na adoção (o hub não cai para pago sem
  isso escrito).
- **Flex para trabalho offline (item 17.8).** `service_tier: flex` é um campo do corpo, então basta uma entrada de
  provedor (`ai.providers.openai-flex: {kind: openai, base_url, api_key_env, extra_body: {service_tier: flex}}`) e um
  papel que aponte para ela com `timeout_s` longo e `max_retries`. Dois detalhes do código: (1) o
  `OpenAICompatProvider` agora **honra `max_retries`** (antes só o provedor da Anthropic o usava): repete 429 e 5xx
  com espera de 5 s dobrando até 60 s ou o `Retry-After` do servidor (até 300 s), e **nunca** `insufficient_quota`
  (ADR-051); o padrão é 0, então o caminho interativo e os provedores já declarados não mudam; (2) `timeout_s` não
  tem teto de validação e, no roteador, é o prazo **total** do papel, esperas incluídas — uma fila de flex sem
  capacidade termina em "passou de N s", não pendura a vaga. **Uso hoje:** o rejulgamento offline
  (`eval_rejudge.py --sobrepor`, bloco comentado em `config.example.yaml`; o custo impresso usa o preço de lista de
  `ai.prices`, ~2x o do flex, então superestima). **Persona no flex (papel `persona`, decisão do dono de 02/10, implementado):** a geração de persona
  (`generate_persona`) e a resposta social (`generate_social_response`) eram o MESMO papel `social`, e a segunda roda
  dentro da execução (comentário, resposta de DM, via `runner` do executor), então apontar `social` para o flex
  deixaria a execução lenta e sujeita a 429. Agora a persona é função própria: `ai.roles.persona: {provider:
  openai-flex, model: <declarado em ai.models>, timeout_s: 900, max_retries: 5}` leva SÓ a geração e o enriquecimento ao
  flex; o `social` da execução continua onde estava. Sem essa linha nada muda (herda o `social`). `ai_calls` e o
  relatório de uso passam a ter `role='persona'` para essas chamadas (linhas antigas seguem `social`); a aba IA mostra a
  função, o saldo da conta entra por `saldos.conta_do_papel` como as demais, e `ai.profiles.<perfil>.roles.persona`
  também vale. Concorrência: só se separa das vagas do `social` se `ai.roles.persona.concurrency` for escrito.
  **Prova `simulated`:** `backend/tests/test_papel_persona.py`; chamada real ao flex `not_run`.
  **Prova `simulated`:** `backend/tests/test_openai_provider.py` (`test_flex_*`, `MockTransport` + `sleep` falso) e
  `scripts/tests/test_eval_rejudge.py`; chamada real ao flex `not_run` (sem chamada paga nesta rodada).
- **Medição:** `eval_rejudge.py --sobrepor` para o `verify` (sem aparelho), `eval-run.ps1` para o ator (troca do
  `config.yaml` e reinício do central por braço). O resultado real fica em `relatorio-validacao.md`.
- **Medido em 28/09 (§18):**
  - O `gpt-6-luna` custa ~US$ 0,00024 por decisão, contra ~0,0078 do Sonnet 5, e é mais rápido (p95 3,2 s contra
    4,2 s). Mas perdeu qualidade sem raciocínio: um falso positivo de envio e um bloqueio falso. **Não adotado.**
  - A imagem real é o `gpt-image-2` médio.
  - Com o ator barato, o plano no Opus 5.5 (~US$ 0,05 por plano, ~1,9 mil tokens de saída) vira o maior custo.
  - Depois de testar um modelo, mantenha o preço dele declarado (K-046).

## 14. Saldo das contas de IA — livro-caixa (ADR-051)

Três contas pré-pagas pagam a IA: Anthropic, OpenAI e Google AI Studio (Gemini, em R$). **Nenhuma publica o saldo por
API** (pesquisa de 28/09). Por isso a plataforma mantém um livro-caixa:

```text
saldo = âncora − consumo desde a âncora
âncora = saldo inicial (uma vez) | recarga (saldo de agora + valor comprado) | fechamento diário | erro de cobrança (0)
```

- **Consumo** (`planning/conciliacao.py`, adaptador `modules/billing/adapters/relatorios_de_custo.py`). Todo o
  consumo vem do provedor ou do registro de cada chamada; nada depende de ler tela.
  - **Anthropic:** relatório oficial de uso (`/v1/organizations/usage_report/messages`, baldes de 1 h por modelo),
    precificado com `ai.prices`. Cobre até a última hora cheia. Validado em 28/09, das 12h às 18h UTC: US$ 5,4946
    pelo relatório × US$ 5,4693 em `ai_calls`. O cache de 1 h é cobrado a 2× a entrada.
  - **OpenAI:** relatório oficial de custo (`/v1/organization/costs`, diário, com o dia corrente).
  - **Google (Gemini):** o consumo medido em cada chamada (`usageMetadata` → `ai_calls`). A chave é só da plataforma,
    então isso é todo o consumo. O Google não publica consumo nem saldo do pré-pago por API.
  - Com chave de administrador (`ANTHROPIC_ADMIN_KEY`, `OPENAI_ADMIN_KEY` no `.env`), o consumo de fora da plataforma
    (console, Claude Code, scripts) entra como `external_usd` = (provedor − base) − (`ai_calls` − base) na mesma
    janela. A **linha de base** (migração 053) é o que o provedor e `ai_calls` já tinham no instante da âncora.
- **Laço de 10 min** (`AppState._saldos_loop`): concilia com o provedor e fecha o dia. O roteador e a saúde leem o
  resultado da última conciliação; nenhuma chamada de modelo espera HTTP de relatório.
- **Fechamento diário** (`saldos.fechar_dia`): uma âncora com mais de 24 h vira uma nova, com o saldo estimado
  (`source: "fechamento"`).
  - Mantém a janela local curta, porque a retenção apaga `ai_calls` com mais de `log_retention_days`.
  - Absorve o atraso do relatório: o que o provedor informar depois entra na delta do dia seguinte.
  - Não fecha conta sem crédito nem conta com conciliação falhando.
- **Recarga** (o único evento humano): `POST /api/ai/balances/{conta}/recharge {"amount": 10}`, ou "Registrar
  recarga" no cartão. A âncora nova é o saldo de agora + o valor. Uma conta sem crédito recomeça de 0.
- **Saldo inicial / correção:** `POST /api/ai/balances/{conta}` com `{"balance": 9.25, "source": "console"}`. É
  necessário uma vez por conta; depois é só conferência opcional.
- **Moeda:** o livro fica na moeda da conta. Valor em US$ numa conta em R$ é convertido pelo câmbio dela; valor em R$
  numa conta em US$ é recusado (400).
- **Limites:** `PUT /api/ai/balances/{conta}` com `warn_below`, `block_below` (na moeda da conta; `null` desliga) e
  `units_per_usd`. Padrão (`saldos.PADRAO`, o mesmo do central):
  - bloqueio em US$ 0,50 (R$ 2,50 no Gemini);
  - aviso em US$ 3 na Anthropic, US$ 2 na OpenAI e R$ 10 no Gemini.
- **Estados:** `unknown` (sem âncora), `ok`, `low` (abaixo do aviso), `blocked` (abaixo do bloqueio: a IA daquela
  conta para) e `exhausted` (erro de cobrança do provedor). `stale` = conta com chave de administrador sem
  conciliação nos últimos 30 min (ou com erro): o consumo de fora deixou de entrar.
- **Saúde:** `ai_balance_blocked`, `ai_balance_low`, `ai_balance_unknown` e `ai_balance_stale`, só de conta em uso.
- **Painel:**
  - chips do cabeçalho;
  - popover "IA em uso", com a conta de cada função;
  - Configuração › IA: o cartão de cada conta, com a recarga como ação principal, a Situação e a coluna "Conta ·
    saldo";
  - azulejo "Saldo de IA" no Diagnóstico;
  - US$ por conta no custo da semana e da execução (`UsageReport.by_account`);
  - aviso no Comando.
- **A IDE** lê em `GET /api/ai/balances` (`?refresh=1` concilia na hora).
- `ai.balance_consoles` (config.yaml) fixa o link "Abrir console" do AI Studio na conta pré-paga (`?billing=<ID>`).

**Biografia inteira e "o pedido manda" (28/09, decisão do dono).** O bloco `<persona>` passou a levar a biografia
inteira (`PERSONA_BIO_FIELDS`, 16 campos em ordem de prioridade, orçamento de 350 tokens e no máximo 6 itens por lista)
e abre com a linha "como usar esta persona" (`USO_DA_PERSONA`): o pedido de quem opera manda no QUE fazer e dizer; a
persona só dá o jeito — voz, palavras, referências e reações —, sem contrariar nem ampliar o pedido. `SOCIAL_SYSTEM`
ganhou a mesma regra.

## 15. Lições medidas no prompt (ADR-054, pacote A7)

O que as execuções anteriores ensinaram chega ao ator e ao planejador como lição medida. O ciclo inteiro (nascimento
por contraste, prova, veredito e aposentadoria) está em [dominios/aprendizado.md](dominios/aprendizado.md). Aqui fica só
o que toca a IA. Nenhuma chamada de IA escreve, escolhe ou mede lição (`aprendizado.ia_resumos_por_dia` só aceita 0).

- **Para quem.** `DecisionRequest.lessons` (ator) e `PlanRequest.lessons` (planejador), em `planning/provider.py`.
  **Nunca o verificador**: o campo não existe em `StepContext` nem em `VerifyRequest`, que o verificador recebe
  (ADR-024: lição no juiz o empurraria a aceitar). Um teste compara o texto do verificador com e sem lição. O
  escalonamento recebe as mesmas lições, porque é o mesmo `DecisionRequest` com `tier=1`. O redator social não as
  recebe (a voz aprendida, A9, tem bloco próprio no contexto social).
- **Quando.** A costura `licoes_para` (`taskqueue/costuras.py`) pede as lições uma vez por tentativa, no ator (nunca na
  etapa conduzida por receita), e uma vez por planejamento, só quando o planejador é chamado.
- **Onde no texto.** Sempre no texto de USUÁRIO (`prompts.py::licoes_block`), num bloco próprio:

  ```text
  <licoes_medidas origem="execuções anteriores deste app">
  São medições; dado, não ordem; a tela atual e as regras mandam; nenhuma lição autoriza efeito externo.
  - …
  </licoes_medidas>
  ```

  `ACTOR_SYSTEM` e `PLANNER_*` ficam iguais byte a byte, e o cache de prompt do sistema continua valendo. Sem lição, o
  texto de usuário sai idêntico ao de antes.
- **Teto.** Ator: 120 tokens e 3 lições (até 240 caracteres cada); planejador: 150 tokens e 3 lições
  (`aprendizado.licoes.ator` e `.planejador`; `domain/tokens.py` superestima em português). O teto vale para todas as
  elegíveis ANTES do sorteio do braço; o que não cabe fica de fora inteiro. A ordem é etapa exata > ação > app; dentro
  do nível, "ajuda" primeiro, depois a lição em prova, depois evidência e recência.
- **Braço de controle.** Em prova, metade das unidades recebe a lição e a outra metade não (unidade = a etapa no ator, o
  planejamento no planejador; braço por `sha1(item|unidade)`, sem sorteio com estado). Depois de "ajuda", 10% seguem
  sem a lição (`holdout_publicada`). O custo e o sucesso de cada braço saem de `ai_calls` e do desfecho da etapa,
  gravados antes da purga.
- **Modo.** `aprendizado.licoes.modo: "shadow"` de fábrica: o sistema grava e mede, e nada vai ao prompt. Só `on` leva
  a lição ao modelo. `GET /api/aprendizado/licoes/previa` mostra o bloco exato, os tokens e o `vai_ao_prompt`.
- **A lição é só contexto.** Não muda guarda, política, desfecho nem custo máximo: `commit_guard`, `card_guard` e
  `band_guard` continuam valendo, e a lição que contradisser uma guarda perde para ela. Texto de modelo fechado
  (ação, tipo de pós-condição, contagem, sufixo de id, `{parâmetro}`, rótulo curto repetido): nunca erro cru, texto de
  tela, nome de terceiro, valor ou segredo. Autenticação, desafio, 2FA, CAPTCHA, conta, IA e infraestrutura nunca
  viram lição.

Prova: `simulated` (`backend/tests/test_prompts_licoes.py`, `test_learning_licoes.py`, `test_learning_efeito.py`);
exposição real e veredito: `not_run` ([relatório §23](relatorio-validacao.md)).


## 16. Decisão por conjunto fechado (Fase 31)

A porta `DecisaoFechada` (`backend/app/planning/decisao_fechada/`, item 31.4) é o ÚNICO caminho do hub para o Jev (TypeSafe
System One) em runtime, e só para escolher entre opções fechadas (`choice`, `noul`, `score`). **Está desligada e sem decisor
real**: o decisor padrão é o `DecisorNulo`, o provedor que fala com a TypeSafe vem no 31.8 e `JEV_RUNTIME_SEND_APPROVED`
continua `False` até o 31.10 (sem troca de chave: emenda do ADR-069, item 9). Decisão e classes de dado: ADR-069.

- **Contrato** (`contrato.py`, tipos puros): `PedidoDeDecisao(origem, classe, estado, perguntas, modo, marcadores, run_id,
  step_id, ref)`, `Pergunta(id, tipo, instrucoes, opcoes, limiar)` e `RespostaDeDecisao(escolha, probabilidades, confianca,
  fallback_reason, tokens, usd, ms)`. Origens: `curador`, `intencao`, `desempate`, `apps`. Até 255 opções, sempre com
  `nenhuma` (`pergunta_choice` a acrescenta; o adaptador de retrieval aplica a mesma trava e recusa localmente acima de 255).
  Um estado, N perguntas, **uma** chamada; o custo mora no `ResultadoDeDecisao`.
- **Privacidade que falha fechada**, antes de montar corpo (`privacidade.py`, ADR-069 itens 3 e 4):
  - `JEV_RUNTIME_SEND_APPROVED = False` recusa tudo;
  - `JEV_ALLOWED_CLASSES` é o teto de código (C0 e C1 em F1 para todos, C2 em F2, C3 em F3), e **a C3 só vale na origem
    `intencao` e só em `shadow`** (em outra origem, ou em `on`, é recusada);
  - C4 em diante não existe no vocabulário;
  - qualquer marcador de C7 no pedido (tela sensível ou protegida, aparelho-loja, segredo, credencial, desafio/2FA/CAPTCHA)
    recusa o pedido INTEIRO, na sombra também; `social_persona` e qualquer origem fora das quatro (D-J5) também recusam;
  - o estado só leva campos nomeados da lista da origem (`CAMPOS_POR_ORIGEM`, vazia até cada consumidor registrar os seus);
  - toda string do corpo passa por `security.redaction.redact` (cobre segredo, não nome de terceiro: por isso a classe é
    limitada).
- **Modos.** `ai.decisao_fechada.enabled: false` vence tudo; `ai.decisao_fechada.consumidores.<origem>: off|shadow|on`
  (ausente = `off`); o modo efetivo é o MENOR entre o do pedido e o do consumidor. `ai.decisao_fechada.classes_permitidas`
  só estreita o teto de código. `shadow` roda fora do caminho crítico e nada usa a resposta (só o `observador`, que recebe
  ids e categorias, nunca o estado); `on` só por consumidor, com GO pré-registrado.
- **Recurso ao caminho atual.** Timeout de 1 s no `on` e 5 s na sombra, sem retentativa. Falha vira `RespostaDeDecisao` com
  `fallback_reason` fechado (`401`, `422`, `429`, `529`, `rede`, `parse`, `unknown_choice`, `abaixo_do_limiar`,
  `privacidade`, `desligado`), sempre com `escolha=None`: **um fallback nunca conta como acerto**. A porta reconfere cada
  resposta contra a pergunta enviada (opção desconhecida, limiar) em vez de confiar no decisor.
- **Cliente único.** `backend/tests/test_decisao_fechada.py::test_cliente_unico_so_o_adaptador_de_retrieval_conhece_o_host_da_typesafe`
  varre `backend/app` e prova que só `modules/context_retrieval/adapters/jev.py` contém o host.

- **Registro da sombra (31.5, migração 074).** A porta, em `shadow` e em `on`, entrega ao `observador` um `RegistroDeDecisao`
  por chamada, e `observador_de_sombra(RepositorioDeSombra(db))` (`decisao_fechada/sombra.py`) grava UMA linha por pergunta
  respondida ou por fallback em `decisao_fechada_sombra`, inclusive a recusa de privacidade (mostra o que o envio fechado deixou
  de decidir). A linha tem só ids opacos, categorias dos vocabulários fechados e números: **nunca o estado enviado nem o texto
  das opções**, e um id fora do formato vira NULL ou `unknown_choice`, nunca texto livre. `usd` e `tokens` da chamada única
  ficam só na primeira linha.
  - **Depois da gravação**: `casar_decisao_real({pergunta: id}, ref=...|step_id=...)` e `casar_desfecho(desfecho, ref=...|step_id=...)`
    (vocabulário `DESFECHOS`) preenchem o caminho atual e o desfecho; só preenchem o que está vazio. É a entrada do 31.8 e do 31.9.
  - **Agregado diário** `decisao_fechada_diario` por (dia, origem, pergunta): `n`, `concordancia`, `acima_do_limiar`,
    **`aceite_errado`** (acima do limiar, com decisão real casada e diferente: a métrica que veta o `on`), `fallbacks` (à parte:
    nunca acerto), `usd` e `ms_p95`. Recalculado por inteiro nos 7 dias recentes e uma última vez antes da purga.
  - **Retenção** `ai.decisao_fechada.retencao_dias` (padrão 180), no laço de retenção geral: purga dias inteiros; o agregado fica.
  - **Preço e livro-caixa.** `ai.prices` ganha `jev-1.13.0: [0.042, 0, 0, 0]` (US$ 0,042 por milhão de entrada, saída grátis); a
    coluna `usd` declarada de `ai_calls` vence os tokens (`costs.spent_usd`, precedente 048). A conta `typesafe` (provedor `jev`,
    modelo `jev*`) entra em `GET /api/ai/balances` como conta conhecida **sem âncora** (`Sem âncora: o dono registra a
    recarga`), sem leitura automática, sem chave de administrador e sem chamada de rede; a primeira recarga registrada vira a
    âncora (base 0). Sem limites de fábrica: o teto do Jev é a fatia dele dentro do teto do dia (31.6). `console` vazio: o dono
    informa a página em `ai.balance_consoles.typesafe`.
  - **Transparência** (`transparencia.py`). Com `ai.decisao_fechada.enabled` e algum consumidor em `shadow` ou `on`, o `notice`
    de `GET /api/ai` nomeia a TypeSafe, os consumidores e as classes que podem sair, e `/api/ai` ganha o bloco
    `decisao_fechada` (consumidores, classes, `send_approved`, `key`). A chave é só "configurada" ou "não configurada", pela
    PRESENÇA (`typesafe_api_key is not None`); o valor nunca é lido para isso. Enquanto `JEV_RUNTIME_SEND_APPROVED` é `False`, o
    aviso diz que o envio está FECHADO e que nada sai.

Prova: `simulated` (`backend/tests/test_decisao_fechada.py`, `test_decisao_fechada_sombra.py`, `test_context_retrieval_semantic.py`:
decisores nulo e falso, banco de teste e relógio falso). Chamada real ao Jev: `not_run`.

### Triagem do curador em sombra (31.8, R1)

`planning/decisao_fechada/curador.py`. `CuradorComTriagemEmSombra` decora o curador principal (porta `CuradorDeIA` do
aprendizado, por estrutura): devolve o parecer DELE intacto e só depois entrega o item a `TriagemDoCurador`, que pergunta
ao Jev em `shadow` uma `choice` entre `manter`, `revisar`, `rebaixar`, `descartar` e `nenhuma` (`curador_triagem`).

- **Dado F1, classe C0:** só `licao` e `receita`, e só os campos de `CAMPOS_POR_ORIGEM["curador"]`: tipo, estado, origem,
  efeito, origem humana, classe e política de risco, rótulo de saúde e contagens (evidência a favor, contra e simulada,
  falhas e ocorrências, votos, intervenções, execuções). Cada valor é rótulo de vocabulário ou número; conteúdo (inclusive
  o da lição), app, capability, ids e datas não saem. Memória, fluxo (C2), tela, voz e preferência não vão.
- **Decisão real = o parecer do curador principal** (`TRIAGEM_DO_PARECER`, combinado com a frente Aprendizado: `manter` →
  manter; `observar`/`pedir_evidencia` → revisar; `rebaixar` → rebaixar; `desativar` → descartar; `aprovar`,
  `possivelmente_obsoleto`, `substituir` e `fundir` ficam fora da comparação), casada pelo `ref` = `dossie_hash`. Sem voto
  da pessoa, mede CONCORDÂNCIA com o curador, não acerto.
- **Sem GO:** os limiares de `on` são os pré-registrados no 31.7 ([design/jev-golden-set.md](design/jev-golden-set.md):
  rótulo da pessoa ou desfecho medido, 30 ou mais por `kind`, 90 % de acordo e vantagem sobre a regra local); até lá a
  sombra só registra. A falha do curador principal sobe como antes, sem sombra.
- **Ligação (suíte 5):** a porta `DecisaoFechada` nasce antes do aprendizado no `AppState`, e o curador do hub (30.12) vai
  ao aprendizado embrulhado por `CuradorComTriagemEmSombra`, com a `TriagemDoCurador` sobre a porta e a sombra do próprio
  `AppState`. Inerte de fábrica (consumidor `curador` em `off`).

Prova: `simulated` (`backend/tests/test_decisao_fechada_curador.py`). Chamada real: `not_run` (31.10).

### Intenção em sombra (31.9, R2 e R3)

- **Consumidor da intenção em sombra (31.9, R2 e R3, ADR-069).** `decisao_fechada/intencao.py` (`ConsumidorDeIntencao`), ligado por
  `taskqueue/sombra_intencao.py` e por UMA linha em `RunService._spawn_planning`: um `add_done_callback` que agenda a sombra
  DEPOIS que o `_plan` termina. **Fora da cadeia**: `intent_ports.py` e `intent_resolver.py` não mudam; a sombra só observa, e
  nada do que o Jev responde volta ao plano. Origem `intencao`, classe C3, sempre `shadow` (consumidor `on` na config continua
  sombra aqui).
  - **Quando roda**: só depois de um plano bem-sucedido. Plano cancelado, com exceção ou recusado pelo provedor (`refusal`) não
    vira sombra, e a execução que já está `failed` ou `cancelled` quando a thread a lê também não. No laço de eventos fica só o
    agendamento (`asyncio.to_thread`): ler a execução, a RESOLVE, o catálogo e a porta rodam numa thread. O `stop()` do
    `AppState` cancela as soltas e espera até 6 s as que já chamaram a porta, antes de fechar o banco.
  - **Desligado custa zero**: `ativo()` é falso, e então nada é lido, resolvido, montado ou gravado (nem linha de recusa), quando
    o envio não está aprovado no código (`privacidade.JEV_RUNTIME_SEND_APPROVED`, lido na hora; hoje `False`), quando a porta não
    está em `shadow` para a intenção (padrão: `enabled=false` ou consumidor `off`) ou quando a C3 não está nas classes efetivas
    (teto do código ∩ `classes_permitidas`).
  - **Pedido**: um só, com duas perguntas (uma chamada). Estado: `comando` (já sem destinos, depois de `redact` e de
    `remover_entidades`) e `app` (id do app da execução, quando há). R2 `intencao_catalogo`: `choice` sobre o catálogo inteiro,
    habilidades publicadas (se `skills.enabled`) e fluxos ativos (se `ai.flows`), como ids opacos (`opt:` + sha1 do id da
    habilidade, 12 hex) com descrição C2 (nome e descrição do dono; fluxo legado só o nome) mais `nenhuma`. Só vai com 1 a 254
    entradas: truncar mediria o que o Jev não viu; acima do teto, um WARNING por processo diz que a R2 saiu da medição. R3
    `intencao_desempate`: `choice` entre as habilidades que a cadeia registrou como empatadas (2 ou mais).
  - **C3 por lista de permissão** (`entidades.py`, função pura `remover_entidades(texto, *, vocabulario=()) -> str | None`). Só
    sai palavra que está num vocabulário comum de comandos (PT e EN, sem palavra que também seja nome de pessoa e sem nome de app, ADR-052) ou no
    vocabulário do catálogo do dono e do id do app (`vocabulario_de`). Qualquer outra palavra, em qualquer caixa, vira `[termo]`;
    palavra com dígito ou `_` também. Por forma: `[link]`, `[email]`, `[usuario]`, `[telefone]`, `[texto]` (entre aspas) e
    `[numero]` (TODO número, até o de 2 dígitos). Recusa (`None`): endereço (rua, avenida, CEP, bairro, apto…), e-mail ofuscado
    (`arroba`, `ponto com`, `(at)`/`(dot)`), 3 ou mais algarismos por extenso, dígito que sobrou, e mais de 6 palavras
    desconhecidas ou metade ou mais do texto desconhecida. Com `None`, o estado vai vazio, a porta recusa e a sombra grava
    `fallback_reason='privacidade'`: o pedido não sai. **A sombra não reduz o risco**: em `shadow` o corpo sai para o decisor
    igual ao de `on`, e a única proteção da C3 é esta remoção. A conferência é a própria lista (o que não é conhecido não sai),
    não um detector "mais largo" depois da troca.
  - **C7 nunca sai, em prosa ou não**: comando que fala de senha, código, 2FA, PIN, OTP, token, captcha, verificação ou desafio
    (`menciona_c7`: `mentions_credential`, `looks_secret` e o assunto em qualquer formato) vai com estado vazio e marcador
    `credencial`; a porta recusa o pedido inteiro (zero chamadas) e grava `privacidade`.
  - **Comando social**: o catálogo social é C2 e entra (a exclusão social/persona proposta na revisão foi refutada: D-J5 veta o
    Jev decidir conteúdo social ou de persona, não ler o catálogo de habilidades). O nome de terceiro dentro do comando é que
    não sai: vira `[termo]` pela lista.
  - **Casamento**: a porta chama `ao_registrar` na mesma thread, logo depois de gravar a linha (também na recusa por
    privacidade); sem polling e sem espera fixa. `casar_decisao_real` recebe o que a cadeia real resolveu (a RESOLVE refeita sem
    efeito, `resolve_intent`, com o catálogo de agora). R2: a habilidade resolvida (ou a única de que fala, quando falta
    parâmetro) ou `opt:nenhuma` se nada casou; empate sem desfecho fica vazio. **R3 não tem decisão real na sombra**: a cadeia
    que termina em empate não escolhe (`AMBIGUOUS` volta para a pessoa), e a que desempata não devolve os candidatos. O rótulo
    da R3 é a escolha da pessoa ou o desfecho, casados no 31.10. **`casar_desfecho` não é chamado**: não há gancho de fim de
    execução sem mexer no núcleo, e fica para o 31.10.
  - Prova `simulated`: `backend/tests/test_decisao_fechada_intencao.py` (`DecisorFalso`, banco de teste, RESOLVE de verdade sobre
    habilidades de teste, `_plan` pelo harness com decisor segurado por evento). Chamada real ao Jev: `not_run`.

## 17. Leitura visual: o papel `leitura` (item 12.5, ADR-070)

Quando a árvore do app não expõe o texto de uma linha (passo 0 do 12.5: a caixa do Outlook é um `ComposeView` cega), o valor lido
na imagem conta como saída de etapa se um **segundo leitor**, que não vê o valor do ator, transcreve o mesmo no recorte da mesma
captura. O papel `leitura` é esse leitor. É **roteamento comum do hub**, nunca a porta `DecisaoFechada` nem as sombras do Jev
(ADR-069 §4).

**`ai.roles.leitura` (contrato de configuração).**

- Opcional e **sem herança**: sem o bloco com `provider` e `model` escritos o papel fica DESLIGADO (`Config.ai_leitura()` é
  `None`, `Config.ai_role("leitura")` levanta `KeyError`), não entra em `AI_ROLES`, em `ai_roles()` nem em `ai.profiles.<p>.roles`
  (escrever `leitura` num perfil recusa a partida). Sem o papel, a leitura visual recusa com `sem_leitor`.
- Na partida (`Config.validar_leitura`, chamada por `Config.__init__` e por `RoutingProvider`): exige modelo **declarado em
  `ai.models` com `vision: true`** (modelo ausente da tabela é recusado, porque o `ModelCaps()` padrão presume visão; a mensagem diz
  a linha `ai.models.<modelo>` a escrever) e recusa o MESMO MODELO do `decide` e do `escalation` — o de base e o de cada perfil —,
  dizendo a linha a corrigir; o provedor simulado passa direto. Compara o modelo, e não só o par (provedor, modelo): é mais estrito
  que o par, de propósito (o mesmo modelo por dois endpoints erra junto). O nome é normalizado antes de comparar e de procurar em
  `ai.models` (caixa, prefixo de gateway `vendor/` e sufixo `-AAAAMMDD` saem, como no `model_caps`): "openai/GPT-6-Luna-20261001" e
  "gpt-6-luna" são o mesmo modelo. **O código exige modelo DIFERENTE e com visão declarada; a família não é checada.**
- Não aceita `fallback_provider` nem `refusal_fallback`: o recorte vai exatamente para o provedor que o aviso de privacidade nomeia.
- Padrões: prazo 30 s, 2 vagas (`ROLE_DEFAULTS["leitura"]`). Aceita provedor `anthropic`, `openai` ou compatível (Gemini pelo
  endpoint OpenAI-compatível, como os outros papéis). O padrão decidido pelo dono é a OpenAI (`gpt-6-luna`), com o Gemini
  (`gemini-3.1-flash-lite`) de reserva. O Haiku é da MESMA família do ator e só entra com nova decisão do dono.
- **A leitura nunca cai no modelo do ator** (`provider.modelo_do_papel_leitura`): o `transcribe` de cada provedor usa o modelo
  explícito do papel `leitura` (a instância do hub tem de ser a do papel; sem hub, o de `ai.roles.leitura`) e, sem ele, levanta
  `AIError(kind="not_configured")` (o executor recusa com `sem_leitor`), em vez de usar `self.model`.
- Opção `ai.leitura_visual.enabled` (padrão `false`) liga o uso; o exemplo está comentado em `config.example.yaml`.

**`transcribe` (contrato de provedor).** `async transcribe(req: LeituraRequest) -> tuple[Transcricao, Usage]`, nos três
provedores (Anthropic, `OpenAICompatProvider`, simulado) e no `RoutingProvider`.

- `LeituraRequest(recorte: bytes, saidas: dict[str, str], run_id: str | None)`: SÓ o JPEG do recorte e os nomes e descrições das
  saídas pedidas (nomes `^[a-z][a-z0-9_]{0,39}$`, no máximo 20). Nunca o valor do ator, o comando, os fatos ou a tela inteira; o
  `run_id` serve ao teto e à contabilidade, e não vai ao prompt.
- `Transcricao(linhas: list[str], campos: dict[str, str | None], legivel: bool, truncado: bool)`. No fio o modelo devolve
  `campos` como lista de pares `{nome, valor}` (`TranscricaoWire`, gramática estrita).
- **Parse estrito** (`provider.transcricao_from_json`): JSON inválido, chave fora do esquema, tipo errado ou **campo pedido
  repetido com valores diferentes** é `AIError(kind="invalid_output")` — nunca `legivel=False` (isso é o MODELO dizendo que não
  leu) e nunca sucesso. O erro é levantado com `from None`: a `ValidationError` do pydantic traz `input_value=` com o texto do
  modelo, e o executor loga a falha com `exc_info=True`. Só os campos pedidos entram, e o pedido que não veio fica `None`. Passou de 12 linhas ou de 400 caracteres: o excesso é cortado e marca `truncado=True`.
- `Transcricao` não se imprime (`__repr__` oculta o conteúdo): o texto transcrito é dado de terceiro, pode trazer injeção de prompt
  ou um código, e nunca entra no `plan`, no `decide`, em log, evento ou mensagem de erro.
- Contabilidade: `Usage.role="leitura"`, `ai_calls.origem="leitura"` (`ORIGENS_DE_IA`), `with_image=True`. A chamada passa pelo
  `_budget` com o `run_id` da execução (valem os tetos do pedido, da execução e do dia), pelo `_saldo` da conta do provedor e
  entra no teto de chamadas do objetivo (`Executor._ai`). Fatia opcional `ai.limits.leitura_max_usd_per_day` (0, padrão,
  desliga; motivo `fatia_leitura`).
- Imagem: só o recorte (`devices.codificacao.recortar_jpeg`): lado maior até 1600 px, no máximo **0,2 da altura da imagem e 320 px**
  (a linha da caixa do teste mede 162 px: uma linha cabe, duas não) e até 400 KB; passou de qualquer um, a âncora não é uma linha
  e a leitura recusa (`sem_ancora`). O recorte é dado de terceiros: metade da tela seriam várias mensagens.

**Recusas da leitura visual (vocabulário fechado).** `desligado`, `elemento_com_texto`, `regiao_nao_declarada`, `arvore_truncada`,
`tela_sensivel`, `fora_do_app`, `sem_ancora`, `captura_mudou`, `repetida`, `sem_leitor`, `leitor_falhou`, `ilegivel`, `truncado`,
`nao_confere` e `triagem:<motivo>`. O ator recebe SÓ o código. Três regras do caminho visual:

- **Triagem (ADR-009):** além da triagem da árvore, o valor com FORMA de código (4 a 8 dígitos, com ou sem espaço ou hífen) é recusado
  mesmo sem palavra de contexto, e o recorte inteiro é triado linha a linha (`codigo_na_linha`: número de 4 a 8 dígitos E palavra de
  código ou verificação em inglês, português ou espanhol na mesma linha; data, hora, decimal e telefone não contam). A triagem NÃO
  é um erro de chamada: leva a etapa a `waiting_user`, como no caminho da árvore, sem nova tentativa do ator e sem lhe dizer que a
  linha tem código.
- **Forma de código, em qualquer grafia (limiar):** `forma_de_codigo` lê o texto em NFKC e com dígitos de outros alfabetos em ASCII,
  ignora os separadores `espaço . , · _ / -` e os de largura zero, e recusa (a) só número de 4 a 8 dígitos, fora data plausível
  ("12/10", "02/10/26"); número com separador de milhar ("1.234") também é recusado; (b) token de 4 a 10 caracteres sem espaço com
  letra e ao menos 3 dígitos ("G-482913", "ABC123"), fora a hora ("14h30"). Vale para o valor e para CADA linha do recorte, e a
  triagem do recorte roda ANTES da conferência (um recorte com código vai sempre para a pessoa). O mesmo conserto, com palavra de
  código na linha, vale para a árvore (`codigo_na_linha`: "Your code is 482.913", "Your code is ABC123").
- **Valor gravado é o do leitor**, limpo (`limpar`): a concordância é no normalizado, e o que está na imagem é o que se grava.
- **Orçamento, prazo, crédito e recusa por política** na chamada do leitor NÃO viram `leitor_falhou`: seguem o desfecho do ator
  (`desfecho_de_ia`). Só a falha do provedor e a saída inválida viram `leitor_falhou`.
- **Receita:** as saídas de origem `visual` ficam fora das variáveis de receita (`variaveis_da_receita`): o valor lido da imagem não
  chega a uma reprodução sem a pessoa.

**Limites conhecidos da v1.** (a) A retomada de um `waiting_user` com valor visual não avança: não há confirmação do valor, isso é item
posterior, e as saídas são abandonar ou refazer o comando. (b) Uma falha passageira do provedor gasta a tentativa daquele par: a chave
`repetida` é gravada antes da chamada ao leitor. (c) Com `leitura_visual.enabled` desligado o ator ainda vê `source` no esquema da
ferramenta (o esquema é estático) e pode gastar 1 das 4 recusas com `desligado`.

**`/api/ai`.** Com o papel escrito aparece em `roles` e em `models`, e o `notice` ganha a frase da leitura visual (ligada ou
desligada) nomeando o provedor, o endpoint, o modelo e os apps que declaram a região (`declaram_leitura_visual`, pelo rótulo do
dado do app, `AppDefinition.label`, e não pelo pacote cru); a chave
aparece só como "configurada". Telas sensíveis e de verificação nunca são recortadas.

Prova: `simulated` (`tests/test_leitura_visual.py`, `tests/test_leitura_visual_papel.py`).

**Bancada do leitor (`real`, 03/10/2026, ~04:20Z, central 1ab8e767, `scripts/bancada-leitor.py`).** É o portão para ligar a
opção, e ela REPROVOU. A opção segue desligada.
- Material: 16 recortes da linha do e-mail de teste em capturas guardadas (01 e 03), com o gabarito pelo lado de quem enviou,
  guardado em `data/bancada-12-5/`, fora do Git.
- Cada recorte foi lido 2 vezes, uma por campo (remetente e assunto), como faz o `ler_valor_visual`, e cada leitura foi
  conferida contra 4 valores do ator: 1 verdadeiro e 3 controles (linha errada, letra trocada, campos invertidos). São 128
  pares por leitor.

| Leitor | `ai_calls` | Custo estimado | Concordância nos verdadeiros | Concordância falsa nos controles | Sem o truncado da prévia (diagnóstico) |
|---|---|---|---|---|---|
| openai/gpt-6-luna | 2944–2975 | US$ 0,0032 | 0/32 (todas `truncado`) | 0/96 | 30/32 (2 com o campo `assunto` vazio) |
| gemini/gemini-3.1-flash-lite | 2976–3007 | US$ 0,017 | 0/32 (todas `truncado`) | 0/96 | 32/32 |

- Causa: a 3ª linha da linha da caixa (a prévia do corpo) SEMPRE termina em "…". O `conferir_transcricao` recusa quando
  QUALQUER linha transcrita, ou o `truncado` do leitor, indica corte. Os dois leitores marcaram `truncado`, corretamente.
  Como está, a leitura visual nunca concorda numa linha da caixa do Outlook.
- Nenhuma concordância falsa nos 192 controles.
- A correção (escopo do "truncado": o campo, o valor e a linha que o contém, não a linha vizinha) muda a regra do ADR-070
  §4 e precisa de decisão antes de entrar.

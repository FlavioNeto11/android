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
- **RA-10 (migração 080): o porquê de cada chamada.** `ai_calls` ganha quatro colunas, gravadas pelo executor a partir
  da `MarcaDaChamada` que acompanha `_ai` (vocabulários fechados em `planning/provider.py`; sem CHECK no banco):
  - `motivo` (para que a chamada foi feita): `plan` → `plano` ou `refinamento` (o assistente do comando, que grava
    na execução respondida); `decide` → `decisao` ou `cascata` (a decisão no modelo
    forte depois do bloqueio do barato, 17.10); `verify` → `julgamento`, `vazio` (a prova de coleta sem item, 12.4) ou
    `rejulgamento` (7.10 e 17.10); `leitura` → `leitura` (12.5). Ficam com `motivo` nulo as chamadas fora de
    execução (curador, persona, decisão fechada: quem as distingue é `origem`) e as dos papéis sem subdivisão, ainda
    que dentro de uma execução: `social` (o texto que a etapa digita).
  - `escalate` (por que subiu ao modelo de escalonamento; nulo = não subiu). No `decide`, na ordem do executor:
    `efeito` (política do efeito externo, inclusive `by_risk`) · `nova_tentativa` · `erros_seguidos` · `piso` (alvo
    inexistente, 7.8) · `bloqueio` (a cascata) · `ciclo`. No `verify`: `nivel` (7.10) e `sim_com_efeito` (17.10). A
    linha com `escalate` é sempre tier ≥ 1: o rejulgamento era gravado como `verify` tier 0.
  - `verdict` (o desfecho): `yes`/`no`/`uncertain`/`unprovable` no `verify`; o nome da ferramenta no `decide` (fora
    da lista de ferramentas, `desconhecida`); `plano` ou `pergunta` no `plan`; nulo na leitura e na linha de erro.
  - `image_reason` (por que a imagem foi junto, ou não), na ordem de `_motivo_da_imagem`. Sem imagem: `sensivel`,
    `politica_nunca`, `arvore_rica`. Com imagem: `politica_sempre`, `pedida`, `problema`, `primeira_julgada`,
    `arvore_pobre`. `with_image` continua dizendo se ela de fato foi.

  A linha de erro e a de orçamento recusado passam a ter `provider` e modelo da função que a chamada usaria (a de
  escalonamento quando a marca diz que subiu); a imagem da persona grava `origem='persona'`; a etapa que a IA conduziu
  grava `driven_by='ai'` também com receitas desligadas e sem veredito sobre a receita. `GET /api/usage` agrupa por
  isso (`by_origin`, `escalations`, `rejudges` com discordância por app, `cascades`, `image_reasons`,
  `steps_driven_by_null`; adendo v0.75 de `api-contract.md`), no preço de `spent_usd` (`costs.usd_por`, que lê `usd`).

  **Conferência depois do deploy** (prova real do RA-10; `:deploy` = o instante do deploy em ISO-8601 UTC). As três
  devem dar zero linhas, ou só as explicadas:

  ```sql
  -- 1. linha nova de execução sem provedor ou sem origem; sem motivo nos papéis que o executor marca
  SELECT role, ok, COUNT(*) n FROM ai_calls WHERE ts > :deploy AND run_id IS NOT NULL
     AND (provider IS NULL OR origem IS NULL
          OR (motivo IS NULL AND role IN ('plan','decide','verify','leitura'))) GROUP BY role, ok;
  -- 2. etapa terminada com decisão de IA e sem condutor (o mesmo número de steps_driven_by_null)
  SELECT COUNT(DISTINCT s.id) n FROM steps s JOIN ai_calls c ON c.step_id = s.id AND c.role = 'decide'
   WHERE c.ts > :deploy AND s.driven_by IS NULL
     AND s.status IN ('succeeded','failed','uncertain','waiting_user');
  -- 3. rejulgamento fora do tier 1, ou chamada escalada sem motivo de escalonamento
  SELECT role, tier, motivo, escalate, COUNT(*) n FROM ai_calls WHERE ts > :deploy
     AND ((motivo = 'rejulgamento' AND (tier < 1 OR escalate IS NULL)) OR (role = 'decide' AND tier >= 1 AND escalate IS NULL))
   GROUP BY role, tier, motivo, escalate;
  ```
- Aba IA do painel mostra o mesmo por execução, incluindo cache ativo/inativo por papel.
- Estimativa por fluxo antes de rodar: `GET /api/flows/cobertura` ganhou `estimated_usd` (item 7.7) — etapas sem
  receita × custo mediano por etapa só-IA dos últimos 7 dias, por papel; sem histórico, `null` ("sem base").
- **Antes × depois de um deploy** (03/10): `scripts/jev-leitura-cache-latencia.py --corte <instante do deploy, ISO UTC>`.
  É a régua do RA-17 e do caminho rápido nas execuções reais, sem A/B pago.
  - Lê `ai_calls` e `runs.ai_profile`. As quatro colunas do RA-10 (migração 080) entram quando existem; sem elas, a
    quebra por motivo é `not_run`.
  - Agrupa por função (`role`; o ator grava `decide`, e `tier` ≥ 1 vira `escalation`) e por perfil (NULO = `padrão`).
  - Mede:
    - o cache lido e escrito sobre a entrada TOTAL (`input_tokens` já é só a fresca);
    - a entrada p50, total e fresca;
    - a latência p50 e p95 das chamadas ok;
    - o custo por etapa e por execução (o plano não tem `step_id`).
  - O custo segue a regra de `planning/costs.py`: o `usd` declarado, senão tokens × `ai.prices` do `config.yaml`. Só
    essa chave é lida; o `.env` não é aberto.
  - Só leitura em toda conexão, sem IA. O hash de commit no lugar do instante vale a data do commit, não a do deploy.
    A causa de uma diferença é INFERRED: antes × depois não é A/B.
  - Por cima do deploy 7: o rejulgamento do `verify` passou a tier 1 na suíte 7 (`executor.py`, "tier 1 também
    no `verify`"). Antes era `verify` tier 0, depois cai em `escalation`. Nesse corte, compare pelos totais `(todas)` ou
    por `--por-motivo`; entre dois deploys posteriores, `verify` e `escalation` comparam direto.
  - Prova `simulated`: `scripts/tests/test_jev_leitura_cache_latencia.py`.
  - Leitura `real` no banco do central em 03/10 ~07:18Z (só leitura, esquema 078): roda, com o RA-10 `not_run`.

## 10. Modelo local — Ollama

> **Desde 25/09/2026 o ator de produção é o Sonnet 5** ([ADR-023](decisoes.md)): o local economizava ~US$ 0,02 por
> caso, com o dobro de escalonamento para o Opus e fragilidade operacional, e estava fora do ar sem aviso. O
> provedor `local` continua definido no `config.yaml`; o texto abaixo é o registro de 24/09 e o caminho de volta.
>
> **Revisão de 03/10/2026 (RA-24).** O gatilho do ADR-023 para rever ("algumas centenas de casos" por dia) não foi
> atingido: 90 execuções de 26/09 a 02/10, ≈ 13 por dia (central, banco em `mode=ro`). O Ollama teve 17 chamadas
> em 24/09 e nenhuma desde então. O único uso candidato do modelo local hoje é uma triagem em SOMBRA, como a do
> curador (31.8), e só depois de medida contra o golden set (`design/jev-golden-set.md`).

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

### Esforço, thinking e dieta do contexto por função e por perfil (17.14 e RA-17)

Para medir latência e custo sem mexer no padrão, a função (em `ai.roles` ou num perfil) e o perfil ganharam campos.
**Nada escrito = a requisição de hoje, byte a byte** (imagem primeiro, um ponto de cache só no system, `thinking`
quando o modelo declara, esforço do `.env`).

- **`ai.roles.<f>.effort`** (`low`|`medium`|`high`|`xhigh`|`max`): o esforço DESTA função, no lugar do
  `AI_EFFORT_PLANNER`/`ACTOR`/`VERIFIER` do `.env`. A aba IA mostra o efetivo.
- **`ai.roles.<f>.thinking: false`**: não manda `thinking` (o ator sem pensamento adaptativo). `true` ou vazio = a
  capacidade declarada do modelo decide, como hoje; não liga thinking num modelo declarado sem ele.
- **`ai.roles.<f>.cache_da_etapa: true`** (RA-17, só na decisão do ator e na escalada): o bloco estável da etapa (o
  passo e as lições, iguais em toda decisão da tentativa) vai ANTES da imagem e leva o 2º ponto de cache; a imagem, o
  histórico e os elementos vêm depois. Com a imagem primeiro, nada depois do system podia ser cacheado: o prefixo
  cacheável termina no primeiro byte que muda, e a imagem muda a cada decisão. Os dois textos juntos são exatamente o
  texto de antes (`prompts.actor_user_partes`). O ponto é sempre marcado, como o do system: abaixo do mínimo do
  modelo a API o ignora sem erro. No ator, tools e system já passam de 6 mil tokens (≈ 6 091 medidos em 24/09, §5), então o mínimo (512 no Opus 5.5 e
  no Sonnet 5.5) está sempre alcançado e o ganho é o tamanho do bloco estável, lido a 0,1× a partir da 2ª decisão
  da tentativa (a 1ª grava a 1,25×). São 2 dos 4 pontos que a API aceita.
- **Perfil: `ai.profiles.<p>.screenshot_max_side` e `rich_tree_min_elements`** valem por cima dos globais só nas
  execuções do perfil (`Config.ai_da_execucao`, lido de `runs.ai_profile` pelo executor). A imagem já é capturada nesse
  lado (`observe(lado_max=…)`), e os limites e o x,y dos elementos seguem a mesma escala. Sem perfis na configuração, o
  executor nem consulta o banco.
- **Só a Anthropic** tem `thinking`, esforço e ponto de cache. No provedor OpenAI esses campos não mudam a requisição
  (o esforço só aparece no registro).
- **Instância própria:** uma função com ajuste ganha instância de provedor só dela (a chave inclui a função e os
  ajustes). O provedor aplica o ajuste só às chamadas da função dona da instância; `decide` e `escalation` com o
  mesmo ajuste numa instância só perderiam o da segunda. Sem ajuste, a chave e o compartilhamento são os de antes.
- **Sonda "o ator pensa?":** `GET /api/ai` → `roles[].thinking` = `adaptive` (vai em toda chamada),
  `desligado_na_funcao` (`thinking: false`), `nao_declarado` (`ai.models.<m>.thinking` falso), `recusado_pelo_modelo`
  (um 400 desligou nesta instância, até reiniciar) ou vazio (provedor sem thinking). A linha `uso[...]` do log ganhou
  `pensou=N`, o número de blocos de pensamento da resposta. O adaptativo pode não pensar num turno fácil; `pensou=0`
  em TODAS as chamadas de uma função com `adaptive` é que diz que o pensamento não está acontecendo.
- **Exemplo:** perfil `img-768` (RA-17), com `screenshot_max_side: 768`, `rich_tree_min_elements: 12` e
  `roles.decide.cache_da_etapa: true`; perfil de ator sem pensamento, com `roles.decide: {thinking: false, effort:
  low}`. Os dois estão comentados em `config/config.example.yaml`.
- **Prova:** `simulated`, `backend/tests/test_perfil_esforco_e_dieta.py` (16 testes): padrão byte a byte, ordem e
  ponto de cache, os dois textos iguais ao de antes, ajuste na escalada pelo despacho do hub e no rejulgamento,
  instâncias, sonda, imagem e árvore do perfil no executor. **`real`: `not_run`.** Sem A/B pago, por decisão da
  orquestradora. A medição é a bateria pareada com `--profile`, que depende de autorização e saldo.

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
| `ai.esquema_do_plano` (LT-4b, 17.13) | `longo` | `longo` (`config.py`); `curto` = etapa livre sem `description`, `precondition` e `max_attempts` (o backend preenche), com título e objetivo curtos |
| `ai.flows` | `true` | `false` (`config.py`) |
| `ai.pathfinder_wait_s` | 240 | 0 (`config.py`) |
| `android.auto_start_devices` | `true` | `false` (`config.py`) |
| `android.max_online_devices` | 2 — comentado como vagas desta máquina | 10 (`config.py`, teto do host; cada worker traz o próprio `max_slots`) |

## 12. O que foi MEDIDO (não confundir com configuração prevista)

- **Bateria de 25/09/2026** (`relatorio-validacao.md` §11.1): 16/17 casos corretos, US$ 0,084 por caso; rejulgamento
  Opus 5.5 × Haiku em 41/56 (73 %). **O ator estava no fallback** (Sonnet 5), porque o Ollama não estava no ar, e a
  saúde não acusou. Cache do verificador em Haiku: zero em 49 chamadas.

- **Latência do planejador (LT-4 e LT-4b, 03/10/2026).** O plano é a espera inicial inteira. Nas 89 chamadas `plan`
  medidas, o tempo segue a saída: ms ≈ 5.173 + 6,6 × tokens de saída (correlação 0,91).
  - LT-4, A/B REAL do esforço: 03/10, 02:44–02:54Z, claude-opus-5-5, os 14 casos QA pelo caminho entre apps, 28
    chamadas, US$ 1,39.
    - Resultado: `low` p50 15,7 s contra `medium` 18,5 s; saída −13 %; forma do plano igual em 13/14.
    - O pensamento do planejador é ≈ 0 (2 tokens): quase toda a saída é o JSON do plano, ≈ 350 tokens por etapa.
  - Sonda REAL do texto bruto (03/10, 03:49:55–03:50:22Z, `low`, caso msg-todos-os-contatos, 8 etapas, formato de
    hoje, US$ 0,09 da sobra do LT-4): 2.495 tokens de saída.
    - O texto é JSON compacto (0 quebras de linha) e é 100 % da saída; pensamento ≈ 2 tokens.
    - Espaço em branco não é alavanca.
  - LT-4b, `ai.esquema_do_plano: curto`: estimativa GRÁTIS por count_tokens em 2 planos reais do QA (6 e 8 etapas,
    formato entre apps), reconstruídos do plano guardado: 102 % e 83 % da saída real. O plano guardado não carrega
    tudo o que o modelo escreveu, então as porcentagens abaixo valem sobre a reconstrução.
    - −13 % sem `description` e `precondition`;
    - −20 % com a brevidade de título e objetivo (simulada por corte);
    - −22 a −24 % sem `max_attempts`, que é o formato curto.
  - Metade da saída é prosa, e o formato não a encolhe: o p50 esperado é ≈ 13 s, não os ≤ 11 s do aceite do LT-4.
  - No `model_judged` curto, o verificador lê o `value`. É o critério que o planejador escreve para ele; antes, ele
    lia a descrição curta.
  - LT-4b (17.13), A/B REAL de 3 braços: 03/10, ~04:31–04:39Z, central, código de `feat/lt-4b-esquema-curto` @
    `2bed3d6c`. Só o planejador, offline (nenhum aparelho, nenhuma execução), esforço `low`, os 14 casos QA: 42
    chamadas, 0 erros, US$ 1,36 (teto US$ 3,00; preço pela tabela `ai.prices` do central).
    - longo, claude-opus-5-5: p50 15,9 s, p90 17,2 s, saída p50 1.806 tokens, US$ 0,0428 por plano;
    - curto, claude-opus-5-5: p50 13,3 s, p90 15,9 s, saída p50 1.468, US$ 0,0359;
    - curto, claude-sonnet-5-5: p50 7,8 s, p90 8,7 s, saída p50 1.389, US$ 0,0181 (preço INFERRED: o do Sonnet 5
      na tabela).
    - Curto × longo no Opus, pareado por caso: a saída fica em 0,79 da do longo (mediana) e o plano sai 2,75 s mais
      cedo. A forma é igual em 14/14: nº de etapas, chaves, etapas com efeito e `missing`. Confirma a estimativa
      grátis (≈ 13 s) e não alcança os ≤ 11 s.
    - Sonnet 5.5 curto: 7,9 s mais cedo (pareado) e metade do custo. Etapas com efeito e `missing` iguais em 14/14,
      nº de etapas em 12/14, chaves em 4/14 (os nomes das etapas mudam).
    - Recomendação (03/10): ligar `curto` com o Opus. O Sonnet no planejador troca o modelo da função (ADR-005:
      planejador Opus) e passa antes por rodada QA ou perfil canário (17.7); este A/B mede forma, não sucesso.
  - Sucesso de execução com o formato curto (rodada QA): `not_run`.
  - **Deploy 7 (decisão da orquestradora, 03/10):** `curto` passa a valer no central, e o Sonnet 5.5 entra como PERFIL
    (17.7), não como padrão. O perfil `planejador-sonnet` troca só o modelo do `plan` para `claude-sonnet-5-5`: o
    esforço segue o global (`AI_EFFORT_PLANNER`, `low` no central, lido em `GET /api/ai` em 03/10) e o esquema segue
    `ai.esquema_do_plano`. Uma execução o escolhe com `POST /api/runs {"ai_profile": "planejador-sonnet"}` ou
    `scripts/eval_run.py --profile planejador-sonnet`, e ele fica em `runs.ai_profile`. A rodada QA pareada é da
    Aprendizado (teto US$ 10). O aceite para trocar o padrão é sucesso ≥ Opus e p50 ≤ 11 s, com emenda datada do
    ADR-005.
    - Preço e capacidade do Sonnet 5.5 (páginas de preço e de prompt caching, 03/10): `[2.0, 0.2, 2.5, 10.0]`, igual
      ao Sonnet 5, e cache mínimo de 512 tokens, contra 1024 no Sonnet 5. As duas linhas entram no padrão do código e
      no exemplo. Antes, o modelo casava o prefixo `claude-sonnet-5`: o preço saía igual por acaso e o cache mínimo
      herdava 1024.
    - **No `config.yaml` do central, a Android acrescenta as linhas DENTRO dos blocos `ai.prices` e `ai.models` que já
      existem lá.** O YAML troca a tabela padrão inteira, não soma (medido em 03/10), e o central declara os dois
      blocos sem o Sonnet 5.5. As linhas são as do exemplo (`claude-sonnet-5-5` nos dois), mais
      `ai.esquema_do_plano: curto` e o bloco
      `ai.profiles: {planejador-sonnet: {note: ..., roles: {plan: {model: claude-sonnet-5-5}}}}`. Prova `simulated`:
      `test_perfil_de_ia.py::test_planejador_sonnet_so_troca_o_modelo_do_planejador`.

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
System One) em runtime, e só para escolher entre opções fechadas (`choice`, `noul`, `score`). **Está desligada**: o decisor
padrão é o `DecisorNulo`; o real (`DecisorJev`, 31.14) existe e só entra com `ai.decisao_fechada.decisor: jev`; e `JEV_RUNTIME_SEND_APPROVED`
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
  `privacidade`, `desligado`, `orcamento`), sempre com `escolha=None`: **um fallback nunca conta como acerto**. A porta reconfere cada
  resposta contra a pergunta enviada (opção desconhecida, limiar) em vez de confiar no decisor.
- **Decisor real (31.14, `decisores.py::DecisorJev`).** Fala pelo transporte do adaptador de retrieval
  (`JevSemanticProvider.consultar`: `{state, model, questions}`, a chave lida do ambiente na hora do POST). Ordem:
  - só `choice` vai ao fio (`criteria` = as opções enviadas); `noul` e `score` respondem `desligado` sem sair, até o
    primeiro consumidor deles;
  - **gasto conferido ANTES do POST** por `RoutingProvider.conferir_gasto` (a mesma rubrica de `_budget`: pedido,
    execução, dia e a fatia `jev_max_usd_per_day`, mais o bloqueio de saldo da conta `typesafe`). Barrado, sem hub que
    confira ou com a leitura quebrada: `orcamento`, e nada sai;
  - o POST usa o prazo que sobra da conferência; erro do transporte vira o motivo fechado (`401` para 401/403, `422`,
    `429`, `529` para 503/529, `parse`, `rede`); a resposta só tem o FORMATO conferido ali (a porta reconfere opção e
    limiar);
  - **toda chamada tentada vira linha em `ai_calls`** (`RepositorioDeSombra.registrar_chamada`): provedor `jev` (conta
    `typesafe` no livro-caixa), origem `decisao_fechada` (a fatia soma por ela), papel `decisao_fechada`, `usd` declarado,
    `ref` = `<consumidor>:<ref>`, `step_id` NULL (a sombra não é chamada de IA da etapa) e sem somar em
    `runs.ai_input_tokens`; a falha também (`ok=0`, `error_kind` = o motivo). Chave ausente não é chamada: `desligado`,
    sem linha. É esta linha que tira a fatia de US$ 0,50 da cegueira e que move o saldo estimado da TypeSafe.
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
    `decisao_fechada` (consumidores, classes, `send_approved`, `key` e, desde o 31.14, `decider`: `nulo` ou `jev`). A chave é só "configurada" ou "não configurada", pela
    PRESENÇA (`typesafe_api_key is not None`); o valor nunca é lido para isso. Desde o 31.17 o aviso diz o decisor da porta
    ("Decisor na porta: nulo|jev") e só afirma "Envio ATIVO" com as quatro condições juntas: `JEV_RUNTIME_SEND_APPROVED`
    aberto, decisor `jev`, um consumidor em `shadow` ou `on` e a chave configurada. Faltando uma, diz "Nada sai agora: …" com
    o primeiro motivo (envio fechado no código, decisor nulo ou chave ausente). O bloco ganha `sending` (as quatro juntas).
  - **Envio aberto no código (31.17, ADR-069 item 15).** `JEV_RUNTIME_SEND_APPROVED = True`, a sombra C0–C1 do 31.10. De
    fábrica nada sai (`enabled: false`, decisor `nulo`). O YAML do deploy 9 liga só o curador: `enabled: true`,
    `decisor: jev`, `consumidores: {curador: shadow, intencao: "off"}`; o exemplo traz também `classes_permitidas: [C0, C1]`.
    O `"off"` vai entre aspas: sem elas, o YAML lê `off` e `on` como booleanos e a configuração não carrega. A intenção (C3)
    só liga com o GO do portão do 31.9 e o sim do dono. O teste de payload (`test_decisao_fechada_payload.py`) prova o que
    vai no fio: `{state, model, questions}` e nada do lado de cá (`run_id`, `ref`, original, destinos, ids crus, hash).

Prova: `simulated` (`backend/tests/test_decisao_fechada.py`, `test_decisao_fechada_sombra.py`, `test_decisao_fechada_jev.py`,
`test_decisao_fechada_payload.py`, `test_context_retrieval_semantic.py`:
decisores nulo e falso, o real sobre `httpx.MockTransport`, banco de teste e relógio falso). Chamada real ao Jev: `not_run`.

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
  `possivelmente_obsoleto`, `substituir` e `fundir` ficam fora da comparação). **Nada se casa na hora** (I2 da revisão do
  31.9, 03/10): a validade do parecer só existe depois do `revisar`, e o relatório do 31.10 lê a linha de `learning_reviews`
  do mesmo `dossie_hash` (= `ref` da sombra) e aplica `decisao_real_da_triagem`, que só devolve decisão com `validade = 'ok'`
  e `simulated = 0`. Sem voto da pessoa, mede CONCORDÂNCIA com o curador, não acerto. A triagem respeita
  `JEV_RUNTIME_SEND_APPROVED` (M2): com o envio fechado, nem monta o pedido (aberto desde o 31.17; o teste fecha por
  `monkeypatch`).
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
    `AppState` (I1 da revisão do 31.9), antes de fechar o banco e com UM prazo de 6 s para tudo (`ESPERA_DE_SOMBRAS_S`),
    espera nesta ordem: a volta do curador (que para entre itens), as threads da sombra da intenção (esperar vem ANTES de
    cancelar: cancelar só solta o `Task`, e a thread seguiria no banco), as sombras da porta, `Porta.encerrar` (nada novo
    entra) e uma última rodada da porta.
  - **Desligado custa zero**: `ativo()` é falso, e então nada é lido, resolvido, montado ou gravado (nem linha de recusa), quando
    o envio não está aprovado no código (`privacidade.JEV_RUNTIME_SEND_APPROVED`, lido na hora; hoje `False`), quando a porta não
    está em `shadow` para a intenção (padrão: `enabled=false` ou consumidor `off`) ou quando a C3 não está nas classes efetivas
    (teto do código ∩ `classes_permitidas`).
  - **Pedido**: um só, com duas perguntas (uma chamada). Estado: `comando` (já sem destinos, depois de `redact` e de
    `remover_entidades`) e `app` (id do app da execução, quando há). R2 `intencao_catalogo`: `choice` sobre o catálogo inteiro,
    habilidades publicadas (se `skills.enabled`) e fluxos ativos (se `ai.flows`), como ids opacos (`opt:` + sha1 do id da
    habilidade, 12 hex) com descrição C2 (nome e descrição do dono; fluxo legado só o nome) mais `nenhuma`. A descrição passa
    por `mascarar_catalogo` ANTES do corte em 200 caracteres: as mesmas máscaras de forma da C3, sem recusa (a opção precisa
    existir); nome passa (ADR-069 item 10). Só vai com 1 a 254
    entradas: truncar mediria o que o Jev não viu; acima do teto, um WARNING por processo diz que a R2 saiu da medição. R3
    `intencao_desempate`: `choice` entre as habilidades que a cadeia registrou como empatadas (2 ou mais).
  - **C3 pelo filtro SENSATO** (`entidades.py`, função pura `remover_entidades(texto) -> str | None`; ADR-069 item 10, dono
    em 03/10 ~00:15Z: dado pessoal pode ir "desde que faça sentido no filtro"). Lista de BLOQUEIO sobre o piso: o que não
    ajuda a escolher a habilidade vira marcador, o que esconde e-mail, telefone ou documento recusa, e o resto passa. Passos:
    0. `normalizar`: NFKC, sem marca combinante nem caractere invisível, todo traço como `-` (letra de largura cheia,
       circulada ou matemática vira a comum; `s<ZWSP>enha` vira `senha`, que a C7 pega). Letra fora do alfabeto latino em
       qualquer palavra recusa (`escrita_nao_latina`; até a rodada C, só a palavra que misturava alfabetos): é o jeito de
       esconder C7.
    1. Forma conhecida vira marcador: `[texto]` (entre aspas; a aspa que abre e não fecha leva o resto do texto),
       `[link]` (link e domínio), `[email]`, `[usuario]` (`@handle`), `[telefone]`, `[numero]` (todo número) e `[termo]`
       (palavra com `_`: handle sem `@`, identificador). Símbolo (emoji, braille...) vira `[texto]`; colado entre letras,
       recusa ("s★enha" passaria pela conferência da C7).
    2. Recusa (`None`) o que esconde e-mail, telefone ou documento: endereço (rua, avenida, calle, quadra...), documento
       (CPF, RG, CNH, passaporte, SSN...), e-mail por extenso ou ofuscado (`arroba`, `(at)`, `{dot}`, `ponto com`), sobra de
       `@` ou `://`, dois ou mais numerais por extenso SEGUIDOS ("nove oito", "dez, dez", "sete-sete") e três ou mais
       ligados por "e", "y" ou "and" (numeral solto vira `[numero]`).
    3. O resto passa como está: palavra comum, nome de pessoa (nossa ou de terceiro), nome de app.

    Com `None`, o estado vai vazio, a porta recusa e a sombra grava `fallback_reason='privacidade'`: o pedido não sai. **A
    sombra não reduz o risco**: em `shadow` o corpo sai para o decisor igual ao de `on`; a proteção é o filtro. Até a
    emenda, o passo 3 era uma lista de PERMISSÃO com recusa por proporção de palavras desconhecidas (a remoção que falha
    fechada do item 4). Medido em 03/10 sobre os 90 comandos reais de 7 dias (só leitura, contagens): as 17 recusas que não
    eram C7 vinham todas da proporção, e a lista apagava cerca de 8 palavras por comando no qa-messenger. Com o filtro
    sensato, 89 dos 90 sairiam (a recusa que sobra é C7), nenhum com e-mail ou telefone.
    Portão (`simulated`, 03/10): `ataque.py` da reverificação, 109 casos, zero vazamento de C7, e-mail completo ou ofuscado
    e telefone. Nome e handle de terceiro deixam de contar (item 10). O caso "escreva para ali no gmail" sai com o nome e o
    nome do provedor, sem endereço: barrar "nome no provedor" barraria também "mande para a Ali no Outlook".
  - **Reverificação B (03/10, NO-GO em 97f35fac; `.claude/handoffs/reverificacao-31-9b.md` §7).** 240 casos novos e 20
    sondas dos céticos acharam 47 vazamentos de portão, e as correções foram:
    - **Duas passadas.** A recusa por forma escondida roda ANTES das máscaras, no texto sem acento, em casefold e com o
      leet desfeito dentro da palavra (`arr0ba`). Ela cobre: e-mail ofuscado (`at`/`(a)`/`(a t)`/`at-sign`/arroba
      soletrada ou hifenizada, `ponto|dot|punto` + domínio de topo, `@` separado da parte local); caixa postal; cartão e
      CVV com o número perto; título de eleitor; endereço em inglês (número + palavras com maiúscula + Terrace/Drive/Way…).
    - **A máscara do token misto vem antes da do número.** Letra e dígito no mesmo token, também ligado por hífen, viram UM
      `[termo]`. Antes, `limao77` virava `limao[numero]` e a palavra da senha saía (15 dos 22 vazamentos de C7).
    - **C7 é recusa do pedido inteiro** (decisão (a) da orquestradora: a máscara não basta). `motivo_c7` acrescenta:
      - eufemismos ("a de sempre", "o que você digita", "the one I always use", "lo de siempre", "segundo campo", "tela
        de acesso", "a outra parte é", "entra com X / Y"…);
      - pergunta de segurança e frase de recuperação;
      - leet (`3→e 4→a 0→o 1→i $→s`) e palavra invertida (`ahnes`, `drowssap`);
      - separadores entre todas as letras (`s/e/n/h/a`) e controle de direção (bidi) no texto cru;
      - Passwort, Kennwort, wachtwoord, mot de passe, parola d'ordine (também em `redaction._CREDENCIAL`);
      - o código pedido pela quantidade de dígitos ("os seis dígitos") ou "destravar";
      - prefixo de token de acesso de qualquer tamanho.
    - **O motivo da recusa** vai para a linha da sombra (`motivo_privacidade`, migração 079; `c7_*` ou o motivo do filtro).
      O `fallback_reason` continua `privacidade`, e `validar` continua devolvendo `c7` ou `pedido_vazio`.
    - **Placa e nome com cidade passam** (decisão (d)). "Dois numerais por extenso recusam" fica (decisão (c)): nos 90
      comandos reais de 7 dias (só leitura, contagens) essa regra não recusou nenhum, e a recusa total ficou em 1/90 (a
      mesma C7 de antes). Na rodada C ela virou a regra da SEQUÊNCIA (abaixo).
    - **Portão local** (`simulated`, 260 casos: os 240 e as 20 sondas, harness da orquestradora copiado): 0 vazamentos
      (eram 45), 0 passagens indevidas (eram 56) e todo C7 recusado (eram 85 sem recusa).
      - As recusas que contrariam o rótulo do harness são as C7 rotuladas "máscara", que a decisão (a) manda recusar, e
        mais 3 casos: dois numerais em nomes ("Ze Sete e Maria Onze"), "duas fotos … três pessoas" e um telefone ditado
        misto que já recusava antes.
      - Prova: `backend/tests/test_decisao_fechada_reverificacao_b.py`.
  - **Rodada C (03/10, NO-GO em 8e1d7a9c; `.claude/handoffs/reverificacao-31-9c.md` §7).** A suíte de 240 deu 0
    vazamentos, mas 27 casos fora dela vazaram pelo caminho de produção (20 de C7, 7 de e-mail). As correções, uma por
    causa:
    - **`sem_destinos` recorta sempre do original** (`target_extractor.py`, mapa de posições do texto normalizado para o
      original). Quando o `_normal` mudava o comprimento ("ﬁ", "ß", acento decomposto), o comando saía em minúsculas e sem
      acento, e o que depende da caixa passava: a chave `AKIA…`, o endereço em inglês. O prefixo de token também é
      conferido sem caixa, com o comprimento de verdade (`akia`/`asia` + 16, `eyj` + 10; "asiático" passa).
    - **Outra escrita** (decisão da orquestradora: "alfabetos misturados" vale para a FRASE): letra não latina em qualquer
      palavra recusa (`c7_alfabetos` na C7, `alfabetos` no filtro), e a lista de palavras-chave ganhou пароль, 密码,
      パスワード, 비밀번호, κωδικός, סיסמה, hasło, parola, lösenord, şifre e outras. A lista de outras escritas casa como
      substring, depois da mesma normalização do texto.
    - **Palavra colada, abreviada ou em leet**: "senha" dentro de outra palavra (menos "resenha" e "desenha"), "password" e
      afins idem; `pw` e `psw`; leet com `5→s 7→t 8→b`.
    - **O par sem verbo de entrar**: "login: x / y", "usuário x, acesso y" (`c7_eufemismo`).
    - **E-mail soletrado**: "at", "chez" ou "bei"; o ponto colado, com espaço antes ou por extenso (ponto, dot, punto,
      punkt, point); qualquer domínio de topo de 2 a 6 letras com o ponto; sem o ponto, só domínio que não é palavra comum
      em inglês ("look at this app" passa); e o provedor conhecido sem domínio ("zilda at gmail", "arroba hotmail").
    - **Importantes**: numerais por extenso só recusam SEGUIDOS (decisão da orquestradora; "Ze Sete e Maria Onze" e "duas
      fotos … três pessoas" passam mascarados); `@handle` com hífen vira `[usuario]` inteiro; PIN tecla a tecla ("toque 4,
      depois 8, depois 2") e fechado por `#` ("2580#") são `c7_digitos`.
    - **Portão local** (`simulated`, 267 casos: os 240 e as 27 sondas da rodada C, harness da orquestradora passando por
      `sem_destinos`): 0 vazamentos (eram 27 em 8e1d7a9c), 0 C7 ou e-mail sem recusa (eram 27) e 0 passagens indevidas.
      As recusas contra o rótulo do harness são as C7 rotuladas "máscara" (decisão (a)), o PIN dos n=183 e n=223, o
      e-mail com domínio cirílico (n=45, agora pela regra da frase) e o base64 com "campo de acesso" (n=155). Nos 92
      comandos reais de 7 dias (03/10, só leitura, contagens), a recusa ficou igual: 1, a mesma C7.
    - Prova: `backend/tests/test_decisao_fechada_reverificacao_c.py` (os 27 pelo caminho de produção até o decisor falso,
      e 23 controles que não podem recusar).
    - **Decisões da orquestradora sobre os efeitos** (03/10, registradas no ADR-069 item 11): o texto entre aspas em outra
      escrita NÃO fica isento ('comente "ありがとう"' recusa: só a sombra perde o comando, a execução não muda, 0 dos 92
      reais); a C2 segue a regra por palavra (escopo); os falsos positivos "at" + provedor ou arquivo ("check the inbox at
      outlook", "look at photo.jpg") e "pw" isolado são aceitos (só recusa, só sombra).
  - **Rodada E (03/10, NO-GO em 963f9d7b; `.claude/handoffs/reverificacao-31-9d.md`, § Decisões da orquestradora).** Zero
    no corpus de 267, mas o cético construiu 25 variações que chegavam em claro. A causa de fundo: a C7 era uma lista
    fechada, e a senha só de letras dita sem a palavra passava. As correções:
    - **E-A(1), "senha" em outras línguas de escrita latina** (catalão, estoniano, indonésio, vietnamita, suaíli, lituano,
      galês, islandês e outras; 56 traduções, 40 novas, as da especificação e outras de memória, não conferidas uma a uma
      no Wiktionary): entram na lista na MESMA normalização do texto. "mật khẩu", pré-composto
      ou decomposto, vira "matkhau"; as longas casam também coladas a outra palavra.
    - **E-A(2), eufemismos**: palavra secreta ou mágica, "magic word", lema e "a de acesso", credencial pelo nome, "pswd",
      "a mesma de ontem", "os números que chegaram", "o que combinamos pelo telefone", "embaixo do usuário" e "a
      combinação é X". "Combinação" e "passe" sozinhos são palavra comum ("a combinação de cores", "passe para o próximo
      post"): só contam com verbo de entrar sem objeto de navegação na frase. Desvio declarado: a especificação punha
      "passe" na lista sem condição.
    - **E-A(3), a regra ESTRUTURAL** (`intencao._login_valor`, `_par_credencial`), independente da lista:
      - `c7_login_valor`: verbo de entrar (entrar, logar, login, log in, sign in, acessar, autenticar, iniciar sessão e
        os de outras línguas) ligado a um valor por conector, até três tokens depois do verbo ("entre com girassol", "faça
        login usando x", "entra no insta com x"), por ":", "/" ou "=" ("pra entrar: girassol") ou antes ("use girassol pra
        entrar").
      - Depois de um objeto de navegação, o conector só liga valor se o objeto é onde se entra com credencial (app, conta,
        site, insta). Na conversa ou no chat, o "com" é a pessoa: "entre na conversa com qa-001" eram 12 dos 92 comandos
        reais.
      - Depois do conector, artigo, pronome, objeto, provedor de entrada ("com o Google") e modo ("com calma") não são
        valor. A busca para em vírgula e conjunção ("entre e comente com parabéns" passa).
      - `c7_par_credencial`: o par com barra até quatro tokens depois do verbo ("entre com a conta Lucas / girassol"); o
        campo de usuário com dois valores (com barra, sempre; com "e" ou vírgula, só com verbo de entrar na frase:
        "usuário lucas e girassol, entra"; "siga o usuário marina e curta" passa); e "lucas, girassol, entra" no começo da
        oração.
      - Desvio declarado: o filtro não conhece o catálogo de personas, então a regra "nome do catálogo, valor, entra" vale
        para quaisquer dois tokens. E "entre com lucas" (nome sem artigo) recusa como "entre com girassol"; "entre com o
        lucas" passa.
    - **E-A(4), o texto ORIGINAL**: a sombra recebe também o comando com destinos (`RunService.dados_da_sombra`, 4º item;
      `dados_da_intencao`, que o 30.25 lê, segue com 3) e a C7 é conferida nos dois. O `sem_destinos` parte o par: "entre
      com a conta Lucas / girassol" vira "entre / girassol". No original, a forma "com <valor>" fica de fora, porque o que
      vem depois de "com" pode ser o destino. Nada do original entra no estado.
    - **E-B, e-mail ditado em peças em português** (`email_ofuscado`):
      - "zilda no gmail", "zilda, no icloud", "zilda no live", "mande para zilda do outlook", "mande para zilda em
        correio.net" e "o usuário é zilda e o domínio é correio.net".
      - O nome antes do provedor não pode ser verbo, objeto ou pasta: "entra no outlook", "comenta no live", "a caixa de
        entrada do outlook" e "a foto da terra" passam.
      - "correio" só conta com pista de destinatário ("para", "usuário", "e-mail").
    - **E-C, a máscara inteira**:
      - a parte local do e-mail é a corrida sem espaço antes do "@" ("abcdef#zilda@", "o'brien@"), sem o que abre ou separa
        trecho;
      - `mailto:`, `?subject=…` e o domínio de topo solto ("zilda@correio .net") entram no `[email]`;
      - `tel:+55…` vira `[telefone]` inteiro.
    - **E-D**: os conflitos de rótulo (n=45, 151, 155, 183, 223, 226) recusam, como a orquestradora relabelou, e a C7 que
      ia mascarada (limao77, 7319, 7730, abc123) agora recusa pelos eufemismos e pelo par.
    - **Portão local** (`simulated`; harness e corpus de 360 casos da orquestradora em `reverificacao-31-9e/`, rodado
      sobre o worktree sem commit):
      - 0 vazamentos de C7 (eram 47 no baseline em 963f9d7b), 0 C7 mascarada (eram 7) e 0 passagens indevidas (eram 66);
        nenhum dos contrastes que devem passar recusou.
      - Sobram os residuais de nome já aceitos ("nome passa"): n=192, 236 e 238, o nome da pessoa ao lado do `[email]`.
        Sobram também "Ze Sete", que é utilidade, e o `)` do link em markdown (n=100, classe link).
      - As 28 mutações e as 20 sondas da rodada D: 0 vazamentos.
      - Os 92 comandos reais de 7 dias (03/10, só leitura): 1 recusa, a mesma C7 de antes.
    - Prova: `backend/tests/test_decisao_fechada_reverificacao_e.py`, com os 25 vazamentos da rodada D, as 4 C7 que iam
      mascaradas, 28 controles que não podem recusar, as formas novas, o e-mail em peças, a máscara inteira e o original
      chegando à sombra.
    - **Decisões da orquestradora** (03/10, registradas no ADR-069 item 12): os cinco desvios aceitos; o limite (d) fica
      registrado ("entre com lucas" recusa a sombra, não o comando); o residual de nome vira classe documentada ("quando
      a parte local é o nome, o nome revela a parte local"), isenta no harness e sem mudança no filtro.
  - **Rodada F (03/10; a fase 2 da rodada E deu NO-GO em db45d4fd; `.claude/handoffs/reverificacao-31-9e.md`, § Decisões
    da orquestradora).** O cético construiu 49 variações novas que chegavam em claro: valor depois de separador, par com o
    destino do catálogo, diminutivo, pergunta de segurança, letras soltas, e-mail sem preposição e CPF nu. A estratégia
    muda: barrar pela INTENÇÃO de entrar, e não só pelo valor.
    - **F-A, a intenção de entrar** (`c7_intencao_de_entrar`): o verbo de entrar (lista multilíngue de uma, duas ou três
      palavras: "zaloguj się", "log into", "faça o acesso") SEM objeto de navegação faz a sombra pular o comando, haja
      valor ou não. Vale no comando ORIGINAL; no texto sem destinos fica desligada, porque tirar "com a conta Lucas" deixa
      "entre e curta".
      - Custo: "abre o insta, entra e curte" e "entre e comente com parabéns", controles da rodada E, agora pulam a sombra.
      - Nos 122 comandos reais de 7 dias, a F-A sozinha pulou 0: 35 têm verbo de entrar, todos com navegação.
    - **F-B, o "com X" pelo catálogo real**: o original é conferido COM o conector, e `RunService.dados_da_sombra` passa,
      no 5º item, os nomes do catálogo de destinos real: personas (também aposentadas e bloqueadas), handles e aparelhos
      (`intencao.nomes_de_destino`).
      - X é destino quando o extrator o tirou ou quando é nome do catálogo; senão é valor.
      - O artigo não isenta ("entre com a girassol" recusa), e ",", "-", ":", "=" e "/" valem igual depois do verbo e do
        conector. "Com a conta do X" é navegação.
      - "Entre com lucas" passa (cai o desvio (d) da rodada E); sem o catálogo, recusaria.
    - **"entre" preposição**: depois de palavra de conteúdo, "entre" é preposição quando o que vem depois não é do verbo
      (conector, separador, lugar, objeto, advérbio, conjunção); diante de número, só em data ou faixa.
      - "As fotos postadas entre 10/05 e 12/05" passa: era um falso positivo real da primeira versão.
      - "No instagram entre com girassol" e "no insta entre 4471 e curte" recusam.
      - No começo da oração é sempre verbo: "entre 3 e 5 fotos" pula a sombra.
    - **F-C**: o campo de usuário ganhou conta, persona, nome, perfil, login e user, e o par vale com quaisquer dois tokens
      ("conta zilda girassol", "persona zilda / girassol"). O primeiro precisa ser valor: "qual conta está conectada"
      passa, outro falso positivo real da primeira versão.
    - **F-D**: diminutivos de senha, chave, segredo, código e PIN (senhinha, chavinha, codiguinho, clavecita…) e "codigo"
      colado a outra palavra.
    - **F-E**: a pergunta de segurança ("a de sempre é X", "aquela que só eu sei é X", "o nome do meu primeiro cachorro é
      X").
    - **F-F**: cinco letras ou mais soltas ("g i r a s s o l", "z-i-l-d-a") viram `[termo]` na C3 e recusam como
      `c7_ofuscado` na C7.
      - Desvio: é mais estrito que a especificação, que pedia só `[termo]`.
      - Limite: uma letra solta logo antes entra na máscara ("dela é z . i . l . d . a" vira "dela [termo]").
    - **F-G**: nome + provedor só de e-mail sem preposição ("zilda gmail", "mande para a Ana gmail") e "point" antes de
      domínio de topo recusam (`email_ofuscado`).
      - Desvios: o provedor antes do nome só conta com pista de destinatário e só se não é app; "point" só diante de
        domínio de topo que não é palavra inglesa.
      - "Da" entrou na lista do que vem antes do provedor: "a foto da terra" passa.
    - **F-H**: o CPF nu recusa como `documento`, formatado ou com 11 dígitos depois de documento, doc, identidade ou
      cadastro.
    - **Portão local** (`simulated`): harness e corpus de 427 casos da orquestradora, no worktree sem commit, com o
      catálogo stub e com o REAL, com e sem os nomes no 5º item.
      - Os quatro modos dão o mesmo resultado: ok 422, recusa indevida 5; 0 vazamentos de C7, e-mail e telefone; 0 C7
        mascarada; 0 passagens indevidas; `erros` vazio. Com os nomes, só muda o motivo de n=392 (intenção → par).
      - As 5 recusas indevidas:
        - n=73 é a própria F-H (o corpus ainda espera máscara);
        - n=427 é a máscara de sempre do token misto (qa-001 → `[termo]`);
        - n=134, 135 e 348 são "Ze Sete", utilidade.
      - A F-A é o único motivo de 5 recusas do corpus, 4 com os nomes do catálogo: é portão, não só custo.
      - Os 122 comandos reais de 7 dias (03/10, só leitura, pelo caminho de produção com o catálogo real) dão 1 recusa, a
        mesma C7 da rodada E.
    - Prova: `backend/tests/test_decisao_fechada_reverificacao_f.py`, com:
      - as 49 entradas da síntese e os pares com destino real;
      - os contrastes e os controles;
      - cada regra de F-A a F-H;
      - a ligação dos nomes até o consumidor.
    - A coluna `motivo_privacidade` (migração 079) é texto sem CHECK: `c7_intencao_de_entrar` entra sem DDL.
    - **Plano B descartado por ora** (decisão da orquestradora, 03/10): enviar só um esqueleto de vocabulário fechado.
      Medido a olho em 20 dos 39 comandos reais distintos: 12 inteiros e 2 colisões danosas. Não é candidato hoje.
  - **Rodada G (03/10; a fase 2 da rodada F deu NO-GO em 7c8f58c8 com 30 achados que contam;
    `.claude/handoffs/reverificacao-31-9f.md`, § Decisões da orquestradora).** Os achados eram de forma, com causa
    concreta; a estratégia da F (barrar pela intenção de entrar) fica.
    - **G-1, o usuário como @handle ou e-mail**: "entre com @zilda.prado e girassol" e "entre com lucas@outlook.com e
      girassol" recusam como par (`c7_par_credencial`).
      - O "@" solto deixou de ser destino: só o "@" diante de handle do catálogo é.
      - Depois do conector do verbo de entrar, "@handle", "local@domínio" ou o nome do catálogo é o usuário, e o "@" e o
        domínio não quebram o par.
      - Com dígito no valor ("acesse com @zilda.prado e Girassol2024"), o par recusa; antes saía `[termo]`.
      - Custo: "entre com @<handle fora do catálogo> e curta" pula a sombra (F-A).
    - **G-2**:
      - a soletração com vírgula e barra ("g, i, r, a…", "g/i/r/a/…") e pelo nome das letras ("ge, i, erre, a, esse…",
        com pontuação entre eles; só com espaço não conta);
      - o verbo com hífen ("log-in with", "sign-in with");
      - os eufemismos "la de siempre", "a palavrinha é", "a de todo dia é", "acesso: X" e "para acesso use X" (diante de
        artigo é instrução: "para acesso use o menu" passa);
      - "usuário X, Y." com vírgula vale sem verbo de entrar, mas só depois de usuário, user ou login: "na conta lucas,
        comente" e "veja o perfil Marina, Zilda" passam.
    - **"entre" preposição só com faixa (G-2 e G-5)**: depois de palavra de conteúdo, "entre" só é preposição diante de
      faixa ou de "os"/"as" e pronome.
      - Faixa é número, hora ("08:00"), data ("12/09") ou mês e dia da semana por extenso, dos dois lados de "e", "a",
        "-" ou "/".
      - O "é" verbo chega aos tokens como "eh" (`_tokens_de`): sem o acento, "é entre" lia "e entre", o imperativo.
      - Passam "a entrega é entre 8 e 12", "entre 08:00 e 12:00", "entre 12/09 e 15/09", "é entre os melhores" e "fotos
        entre março e abril".
      - "No insta entre girassol e curta" recusa.
      - Custo: "escolha entre a Marina e a Ana" pula a sombra (era controle da F).
    - **G-3, e-mail em peças**:
      - o nome antes do provedor com hífen ou parêntese ("zilda - hotmail", "zilda (hotmail)") e "lá" antes da
        preposição ("zilda, lá no gmail"); o dois-pontos não, porque é o do rótulo ("site: outlook", 1 dos 122 comandos
        reais);
      - "at" entre hífens ou sublinhados ("zilda-at-correio-net", "zilda_at_gmail_dot_com") só com provedor ou domínio
        de topo depois: "@cafe_at_home" e "look-at-me" passam;
      - "-dot-" como ponto do domínio;
      - os provedores fastmail, laposte, web.de, mail.ru e me.com (protonmail, zoho, gmx, yandex e aol já estavam).
    - **G-4, o catálogo real**: os nomes chegam INTEIROS (`nomes_de_destino`: "lucas almeida", não "lucas" e "almeida"
      soltos), e o nome solto só é destino depois da palavra de conta ou do "@" (`_Destinos`).
      - Nunca na posição de valor: uma persona "Girassol" não isenta a senha "girassol", nem em "entre com girassol" nem
        como segundo do par ("entre com a conta Lucas e girassol").
      - O destino pela sintaxe continua isento: o que o extrator tira ("com a conta Lucas", "pela Lucas", "como @lucas").
      - O nome de duas palavras é UMA menção e "André girassol" são duas: o par vê o segundo.
      - Desvio declarado: "entre com o Lucas e curta" volta a pular a sombra, porque tem a forma de "entre com girassol". O
        "Entre com lucas passa" da F cai. Custo medido: nos 122 comandos reais de 7 dias, a isenção pelo catálogo não
        mudou nenhuma decisão.
    - **G-6, decisão**: a palavra-chave da C7 sem valor ("lembre de trocar a senha depois") continua recusando. É o lado
      seguro: a palavra segura o valor irreconhecível. Revê-se se as recusas nos comandos reais passarem de 5 %.
      - Hoje há 1 recusa em 122 (0,8 %), e ela tem a forma "senha: **‹palavra›**", palavra com valor aparente
        (INFERRED pela forma, sem ler o texto).
      - Recusas por palavra sem valor: 0.
    - **Residual de outro idioma** (síntese da F, item 11, recomendação não bloqueante; feito por ser barato, para o teste
      das 37 entradas afirmar recusa em todas): "logga in" (sueco), "inicia sessió" (catalão), "the usual is", e os
      algarismos ditados em alemão, italiano e francês no `_DITADO` (sem "sei" e "un", que são palavras do português e
      artigo).
    - **Portão local** (`simulated`): o harness da orquestradora (`ataque_b.py`, que espelha o original e os nomes)
      com o corpus de 492 casos da rodada G e o catálogo stub com a persona "Girassol", no worktree.
      - ok 488, recusa indevida 4 (as 4 antigas de utilidade: n=134, 135, 348 e 427);
      - 0 vazamentos de portão, 0 C7 mascarada, 0 passagens indevidas; `erros` vazio; sondas da rodada C 27/27.
      - Contra a linha de base da G em 7ea3aa18: 90 casos mudam, todos para ok ou só de motivo. Inclui os 37 da síntese,
        os 29 antigos que vazavam pela persona "Girassol" e os 5 contrastes de "entre".
      - Os 122 comandos reais de 7 dias (03/10, só leitura, catálogo real): 1 recusa, a mesma.
    - Prova: `backend/tests/test_decisao_fechada_reverificacao_g.py`, com:
      - as 37 entradas;
      - os 12 pares com @handle ou e-mail, os 8 separadores de e-mail e os 6 contrastes de "entre";
      - os casos da persona "Girassol" e os controles.
      Os testes da E e da F que a G-4 e a G-5 mudam de propósito foram reescritos para a regra nova.
    - **Catraca do ADR-052** (a suíte 8 a pegou na rodada F; decisão da orquestradora: vocabulário por dado): os nomes
      dos apps da plataforma saíram das listas do filtro.
      - Saíram de `_OBJETO_DE_NAVEGACAO`, `_ONDE_SE_ENTRA` e `_NAO_VALOR` em `intencao.py`, e do `_NAO_DONO` em
        `entidades.py`.
      - Agora vêm do `app.yaml`: nome, rótulo e o campo novo `apelidos` (o Instagram declara `[insta]`), lidos do
        registro na consulta (`entidades.nomes_dos_apps`). O filtro não importa o registro: a fila registra a fonte na
        subida (`registrar_fonte_dos_apps(registry.nomes_e_apelidos)` em `taskqueue/service.py`; sem registro, nenhum
        nome). O import tardio que fazia isso furava a catraca de `test_arquitetura` (suíte 9).
      - A lista fixa guarda o vocabulário genérico ("app", "conta", "perfil", "feed", "site") e, à parte, os serviços
        de terceiros SEM pacote na plataforma (gmail, facebook, tiktok, whatsapp, twitter, chrome), em
        `_SERVICOS_SEM_PACOTE`. Desvio declarado da decisão "só termos genéricos": sem eles em `_ONDE_SE_ENTRA`,
        "entra no facebook com girassol" passaria (há navegação, e a F-A não pega).
  - **Rodada H (03/10; a fase 2 da G teve 47 vazamentos em 9a99a8d8, corpus de 579;
    `.claude/handoffs/reverificacao-31-9g.md`, § Decisões da orquestradora; ADR-069 item 16).** É a camada ESTRUTURAL,
    segunda passada sobre as listas e ainda lista de bloqueio: sem lista de permissão e sem recusa por proporção (item 10).
    - **H-1 (a), o conector depois de qualquer objeto** (`_login_valor`):
      - Até a G, o objeto de navegação isentava o conector, salvo o lugar onde se entra ("no insta com x"). Agora só o
        objeto PESSOA ou conversa isenta (`_OBJETO_PESSOA`: conversa, chat, dm, grupo, live, chamada, sala, contato…).
        Recusam "entra aqui com girassol", "entre no feed com girassol" e "entre no perfil com girassol"; passam "entre na
        conversa com qa-001" e "entre em contato com a Ana".
      - Depois do conector:
        - o objeto de navegação não é valor ("acesse o perfil da Marina usando o navegador");
        - a palavra de conta com nome que o catálogo não conhece é valor ("entre com a conta girassol"). O "conta" de
          `_ONDE_SE_ENTRA` a isentava: era vazamento da base, que a sonda achou.
      - O passado e o particípio (`_ENTRAR_PASSADO`: entrei, loguei, logado, acessou…) só ligam VALOR. Fora disso, "veja
        se o lucas está logado" é pergunta de estado, e a F-A a recusaria.
      - Línguas novas:
        - verbos: "logg inn", "log ind", "intră", "masuk", "přihlas se", "kirjaudu sisään", "giriş yap", "lépj be";
        - conectores: med, cu, dengan, s, tunnuksella;
        - a posposição turca ("girassol ile giriş yap") e o sufixo instrumental húngaro ("girassol-lal").
    - **H-1 (b), o par sem verbo**: com o campo de login (usuário, user, login e o novo "usr") e separador que não é palavra
      (`_SEPARADORES_NAO_ALFABETICOS`: vírgula, ";", "|", ":", "/", "-", "&", "+", "·"), o par vale sem verbo de entrar:
      "usuario lucas; a outra: girassol", "user lucas | girassol". "conta", "perfil", "nome" e "persona" ficam fora,
      porque listam contas ("persona lucas; persona bruno").
    - **H-1 (c), as letras depois de digitar** (`_letras_depois_de_digitar`): depois de digite, use, coloque, escreva,
      type…
      - duas ou mais letras soltas ou nomes de letra, com pontuação entre elas, recusam ("g+i", "g · i", "x/y");
      - só com espaço, três "fortes": a letra que não é palavra (não "a", "e", "o", "y", "u") e o nome de letra que não é
        palavra comum ("ge", "erre", "esse"; não "de", "que", "ele", "te"). Passam "use a e o como exemplo" e "digite que
        ele te ama".
      - A soletração longa (`_SOLETRADO`, nos dois arquivos) aceita qualquer separador que não é letra nem algarismo. No
        filtro vira `[termo]` antes do símbolo virar `[texto]`, e "g [texto] i [texto] r" não sai mais.
    - **H-1 (d), o telefone ditado**: holandês, sueco, norueguês e dinamarquês no `_ALGARISMO_FALADO` (lista de bloqueio).
      Ficam de fora "een"/"en", "to" e "ni", que são palavras de outras línguas.
      - A forma genérica do pedido (5+ palavras curtas DESCONHECIDAS depois de verbo de ligar) exige um vocabulário de
        palavras conhecidas: é lista de permissão, contra o item 10. Decisão da orquestradora (~10:50Z): fica a versão
        de bloqueio, sem exceção ao item 10; o telefone ditado em língua sem lista é residual de outro idioma.
    - **H-1 (e), o e-mail em peças**:
      - o rótulo com dois-pontos e os campos ("e-mail: X, provedor: Y, terminação: Z", "e-mail: X / Y / Z"). Sem
        dois-pontos, "o e-mail do provedor caiu" passa;
      - com pista, o nome, a preposição, a palavra e o domínio de topo como peça (`_TLD_PECA`): "zilda em correio, net",
        "zilda no correio, terminação net", "para zilda em exemplo com br". O "com" sozinho é a preposição: "mande para
        zilda em casa com carinho" passa;
      - o nome e o provedor sem preposição ("para zilda correio net");
      - "bij" e "punt" (holandês) e o "@" trocado por "#", "*", "&", "~" ou "%" colado (`_ARROBA_TROCADA`, sem o leet: o
        "%40" de um link é o "@" codificado que a máscara de link apaga).
    - **H-2, o domínio de topo separado**: o `_EMAIL` leva o domínio de topo depois de ponto com espaço ("zilda@correio.
      net", "zilda@correio. com. br") e sem ponto ("zilda@correio net", com quebra de linha; não o "com", a preposição).
      - Com o espaço só depois do ponto, só o domínio de topo que não é palavra (`_TLD_SEM_PONTO`) entra no e-mail:
        "zilda@correio.net. de manhã" e ". me avise" ficam como estão.
      - O `_SEP_DOMINIO` aceita ". net" diante de domínio de topo que não é palavra: "look at this. Me too" passa.
    - **H-3, o verbo central do produto (decisão)**: o nome INTEIRO do catálogo sozinho depois do conector do verbo de
      entrar é destino (`_destino`): "entre com o lucas", "entre como lucas", "entre no perfil com Lucas" passam. Cai o
      desvio da G-4.
      - Continuam valor, mesmo com o nome no catálogo (G-4):
        - o segundo do par ("com a conta Lucas e girassol");
        - o colado ao usuário ("com o lucas girassol", `_colado`);
        - o conector depois de outro destino ("acesse como lucas com girassol", `catalogo=False`).
      - O tempo e o reforço depois do nome ("hoje", "mesmo", "primeiro") entraram em `_ADVERBIOS`. O determinante da
        conta (`_DETERMINANTES`: outra, a mesma, qualquer, a certa) não é valor ("veja se está logado com outra conta"
        passa; "entre com outra conta" continua pela F-A), e no par é o rótulo do segundo ("a outra: girassol").
      - **Residual aceito** (orquestradora, 03/10): com uma persona "Girassol", "entre com girassol" passa como destino.
        O portão da fase 2 é o catálogo sem ela; com ela, contam só as lentes do par e da utilidade.
    - **H-5, os controles operacionais**:
      - Endereço é logradouro, número e CEP. A palavra de lugar que é palavra comum (bairro, casa, vila, bloco, quadra,
        lote, apartamento, condomínio) só recusa com número logo depois (`_ENDERECO_COM_NUMERO`, lido com os marcadores:
        "casa 3", "quadra dez"). Passam "a padaria do bairro", "a foto da casa" e "o bloco de notas".
      - "e-mail da X" antes do provedor que é app (outlook, live, terra) é a MENSAGEM ("arquive o e-mail da newsletter no
        outlook"), salvo com domínio de topo depois ("no outlook.com") ou com pista ("mande para o e-mail da zilda no
        outlook"). Pela G-3, "zilda no live" sem "e-mail da" continua recusando.
      - O domínio sem esquema só vale com domínio de topo conhecido (`_TLD_DE_LINK`) ou com caminho depois. "siga
        maria.clara" e "p.ex." não saem mais mutilados como `[link]` (casos 540 e 541).
    - Custos declarados, aceitos pela orquestradora como residual documentado (INFERRED: nenhum aparece nos 122
      comandos reais; o G-6 revê se a taxa real passar de 5 %). Pulam a sombra:
      - "entre no perfil com a Ana": nome fora do catálogo depois de objeto que não é pessoa;
      - "arquive o e-mail da Marina Prado no outlook": a isenção da H-5 vale para o nome de UMA palavra, porque o
        lookbehind tem largura fixa;
      - "responda para o e-mail da Marina no outlook": a pista "para" faz do nome o dono do endereço;
      - "escreva a, b e c": duas letras com pontuação depois de verbo de digitar, como a H-1 (c) pede.
    - **Portão local** (`simulated`, no hash da H): `ataque_b.py` com o corpus de 579.
      - Catálogo sem a "Girassol": 0 vazamentos (eram 47) e 0 passagens indevidas.
      - 5 recusas indevidas:
        - as 4 antigas de numeral e termo (n=134, 135, 348 e 427);
        - o n=538, "e-mail: zilda.prado, provedor: petrobras, terminação: com br". O corpus o rotula máscara; a H-1 (e)
          manda recusar campos rotulados. Ficou a recusa, e a orquestradora reetiquetou o caso para recusa no corpus.
      - Com a "Girassol", nas 57 das lentes do par e da utilidade: 0 vazamentos.
      - Contra a linha de base 9a99a8d8, 54 n mudam: os 47 vazamentos, 6 recusas indevidas que saem (540, 541, 556, 558,
        572 e 573) e 1 só de motivo.
      - Os 122 comandos reais de 7 dias (10:30Z, só leitura, catálogo real): 2 recusas (1,6 %), as MESMAS da base
        5f020598.
    - Prova: `backend/tests/test_decisao_fechada_reverificacao_h.py`. Cobre as 40 entradas (27 que contam e 13 de outro
      idioma), os 6 fragmentos, a H-3 nos dois catálogos, o residual aceito, a H-5 e os controles. A G e a E foram
      reescritas para a H-3: os casos da G de valor sozinho contam no catálogo sem a "Girassol", e "entre com lucas" passa.
  - **C7 nunca sai, em prosa ou não**: comando que fala de senha, código, 2FA, PIN, OTP, token, captcha, verificação, chave,
    segredo ou desafio, em PT, EN ou ES (`menciona_c7`: `mentions_credential`, `looks_secret` e o assunto no texto
    normalizado, também com homóglifo, letra de largura cheia, uma letra por vez separada por ponto ou espaço, e letra de
    outra escrita) vai com estado vazio e marcador `credencial`; a porta recusa o pedido inteiro (zero chamadas) e grava
    `privacidade`.
  - **Comando social**: o catálogo social é C2 e entra (a exclusão social/persona proposta na revisão foi refutada), e,
    desde o ADR-069 item 10, o nome no comando social também sai: o D-J5 veta o Jev DECIDIR por persona (origem
    `social_persona` recusada na porta), não o dado.
  - **Casamento**: a porta chama `ao_registrar` na mesma thread, logo depois de gravar a linha (também na recusa por
    privacidade); sem polling e sem espera fixa. `casar_decisao_real` recebe o que a cadeia real resolveu (a RESOLVE refeita sem
    efeito, `resolve_intent`, com o catálogo de agora). R2: a habilidade resolvida (ou a única de que fala, quando falta
    parâmetro) ou `opt:nenhuma` se nada casou; empate sem desfecho fica vazio. **R3 não tem decisão real na sombra**: a cadeia
    que termina em empate não escolhe (`AMBIGUOUS` volta para a pessoa), e a que desempata não devolve os candidatos. O rótulo
    da R3 é a escolha da pessoa ou o desfecho, casados no 31.10. **`casar_desfecho` não é chamado**: não há gancho de fim de
    execução sem mexer no núcleo, e o relatório do 31.10 o faz na leitura ([design/jev-golden-set.md](design/jev-golden-set.md)
    §5). No mesmo `ao_registrar`, `anotar_ambiguos` grava quantas etapas da
    RESOLVE terminaram em `StageOutcome.AMBIGUOUS` (`decisao_fechada_sombra.ambiguos`, migração 079, RA-2): é onde a R3 tem o
    que medir. A métrica principal do 31.10 e os estratos estão em [design/jev-golden-set.md](design/jev-golden-set.md) §3.
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

**Bancada do nível 1.1 (`real`, 03/10/2026, 04:57–04:59Z, conferência do branch `feat/android-lote-0310` sobre o
config, o gabarito e o banco do central).** Emenda do ADR-070 §4: "truncado" vale só para o valor, o campo e a linha que
contém o valor; a marca global do leitor só cai quando uma linha alheia cortada a explica. Mesmo material (16 recortes, 128
pares por leitor). APROVOU nos dois leitores.

| Leitor | `ai_calls` | Custo estimado | Concordância nos verdadeiros | Concordância falsa nos controles |
|---|---|---|---|---|
| gemini/gemini-3.1-flash-lite | 3008–3039 | US$ 0,017 | 31/32 (1 `truncado`: o assunto do item 14) | 0/96 |
| openai/gpt-6-luna | 3040–3071 | US$ 0,0032 | 29/32 (2 `nao_confere` no assunto, itens 6 e 8; 1 `truncado`, item 14) | 0/96 |

- O item 14 é recusado pelos dois: o assunto aparece cortado na linha da tela, e recusar é o certo.
- Leitor escolhido: gemini-3.1-flash-lite principal (mais acertos e mais rápido, ~1,2 s contra ~1,9 s), gpt-6-luna
  alternativo. O custo é irrelevante nos dois.
- A opção liga (`ai.leitura_visual.enabled: true` e `ai.roles.leitura` no gemini) no próximo reinício do central, o
  deploy da suíte 7. Até lá segue desligada.
- Armadilha da medição: rodar a bancada com o `app` de um worktree faz o `.env` ser procurado na raiz do worktree. As
  chaves vêm vazias (401 da OpenAI, "Missing or invalid Authorization header" do Gemini) e parecem revogadas, mas não estão.

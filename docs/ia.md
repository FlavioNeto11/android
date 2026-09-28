# IA — hub, custo, modelo local

> Fonte principal para "como a IA é configurada, roteada e paga". Para o que foi medido em cada rodada, ver
> [docs/relatorio-validacao.md](relatorio-validacao.md); para o plano que originou o hub, `docs/plano-100.md`
> fases 0 e 7 (não editar); para produto e conceitos, [docs/produto.md](produto.md).

## 1. As cinco funções

`AI_ROLES = ("plan", "decide", "verify", "escalation", "social")` (`backend/app/config.py:22`). Cada função tem
provedor, modelo, prazo e concorrência próprios, roteados por `RoutingProvider`
(`backend/app/planning/routing.py`):

| Função | O que faz | Volume típico |
|---|---|---|
| `plan` | Interpreta o objetivo em uma chamada estruturada: app, parâmetros, critérios de sucesso, etapas com dependência e pós-condição | 1 chamada por comando |
| `decide` | Observa a tela (screenshot + hierarquia) e escolhe UMA ferramenta tipada | ~90% das chamadas, junto com `verify` |
| `verify` | Confere a pós-condição da etapa por visão, quando a checagem determinística não basta | idem |
| `escalation` | Assume quando o modelo barato tropeça, em nova tentativa e em etapa com efeito externo (`strong_model_for_side_effect`) | minoria, mas mais caro por chamada |
| `social` | Escreve a mensagem na voz da persona; nunca recebe imagem nem credencial | 1 por interação social |

**`generalize` (modo treinamento, item 13.2) não é uma sexta função registrada** — despacha no provedor/modelo do
papel `plan` (mesmo hub, sem `ai.roles.generalize` dedicado): `backend/app/planning/training.py` monta o pedido e
chama o provedor resolvido para `plan`.

**Geração de persona também não é função nova.** `POST /api/personas/generate` e `POST /api/personas/{id}/enrich`
despacham pelo papel `social` (`backend/app/planning/routing.py::RoutingProvider.generate_persona`, sem `run_id`:
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
(`config.py:339-345` — plan 120s/4, decide 45s/8, verify 30s/8, escalation 60s/4, social 60s/4). Provedor não
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

## 6. Limites

| Limite | Onde | Nível |
|---|---|---|
| `ai_max_usd_per_run` / `ai_max_usd_per_day` | `backend/app/config.py:262-263` (padrão 15.0 / 0.0 — dia sem teto por padrão) | por execução / por dia, em US$, conferido em `_ai` no executor (cobre também planejamento e prévia de persona) |
| `ai_slots` | `backend/app/taskqueue/ai_slots.py` — uma LINHA por vaga, tomada por compare-and-swap no banco | teto global de concorrência de IA, entre backends (lease, não por processo) |
| `_RoleGate` | `backend/app/planning/routing.py` (`asyncio.Semaphore` por papel, dentro do limite de `ai_slots`) | prioridade relativa entre funções (não deixar `social` encher a fila do `verify`) |
| Disjuntor de conta | `backend/app/taskqueue/executor.py:83-233` (classifica cobrança/credencial; primeira falha represa a etapa sem gastar tentativa, pausa a execução, acusa em `/api/health` e na aba IA) | por execução, com liberação em `clear_ai_breaker` |
| Teto do dia antes da imagem paga | `backend/app/modules/identity/application/persona_images.py::PersonaImageService.conferir_orcamento` (só o gerador pago; o simulado não gasta e não confere) | por dia, o mesmo `ai_max_usd_per_day`; 409 `ai_budget` na rota antes de aceitar o pedido |
| Saldo da conta (ADR-051) | `backend/app/planning/saldos.py` + `RoutingProvider._saldo` (antes de cada chamada, também no destino do fallback) e `PersonaImageService.conferir_orcamento` | por conta (Anthropic, OpenAI, Gemini): `block_below` barra com `kind="balance"`; `warn_below` só avisa; ver §14 |
| Por objetivo | teto de tokens e de chamadas por objetivo (achado #99); a recusa por estouro agora grava linha em `ai_calls` mesmo sem chamada real (`_registrar_orcamento_estourado`) | por objetivo |

## 7. Receitas, fluxos e provas locais

Ver [docs/produto.md §2](produto.md) para os conceitos. Mecanismo de custo, resumido:

- **Receitas** (`ai.recipes: replay`): a IA aprende uma etapa uma vez; repetição por seletor (resource-id/texto)
  sem nova chamada. Seletor que não casa exatamente um elemento → divergência (só aquela etapa volta para a IA).
  3 falhas seguidas → quarentena. `shadow` aprende e compara sem agir.
- **Fluxos** (`ai.flows: true`): execução 100% comprovada vira plano congelado; comando repetido com outros
  parâmetros pula o planejador.
- **Funil medido** (evolução de desempenho, ADR-027): consulta de receita (`receita.consulta{encontrada|ausente|
  quarentena}`), reprodução (`receita.reproducao{ok|divergiu}`) e retorno à IA (`receita.retorno_ia{motivo}`),
  em `GET /api/desempenho`. `GET /api/flows/cobertura` traz `aproveitamento` por fluxo e app: etapas elegíveis, por
  receita, receita + IA e só IA, "sem cobertura" separado de "receita de outra chave ou em quarentena", e chamadas de
  IA evitadas estimadas (`taskqueue/aproveitamento.py`).
- **Receita divergida não escala de modelo sozinha**: a IA assume a etapa no modelo de ação, e só sobe pelos
  controles de sempre (erros seguidos, repetição, efeito externo). O código nunca escalou; o comentário que
  prometia isso foi corrigido. Escalar na divergência é **decisão do dono pendente**, com o custo medido no
  [relatório](relatorio-desempenho.md): 22 etapas `recipe+ai` em 7 dias.
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

Nenhum dos três foi rodado na rodada de 23-24/09 por regra da chamada (sem gasto pago sem autorização). A
comparação "linha de base × configuração atual" que o próprio projeto exige antes de adotar uma alavanca de
custo (decisão 7 do plano-100) **segue pendente**.

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
| `ai.image.provider` / `model` / `per_persona` / `on_create` | `simulated` / `gpt-image-2` / 1 / `true` | os mesmos (`config.py::ImageCfg`); `quality: medium`, `price_per_image` (estimativa) low 0,02 / medium 0,06 / high 0,2 US$, `price_per_mtok` 5 / 8 / 30 US$ |
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
- **Medição:** `eval_rejudge.py --sobrepor` para o `verify` (sem aparelho), `eval-run.ps1` para o ator (troca do
  `config.yaml` e reinício do central por braço). O resultado real fica em `relatorio-validacao.md`.
- **Medido em 28/09 (§18):**
  - O `gpt-6-luna` custa ~US$ 0,00024 por decisão, contra ~0,0078 do Sonnet 5, e é mais rápido (p95 3,2 s contra
    4,2 s). Mas perdeu qualidade sem raciocínio: um falso positivo de envio e um bloqueio falso. **Não adotado.**
  - A imagem real é o `gpt-image-2` médio.
  - Com o ator barato, o plano no Opus 5.5 (~US$ 0,05 por plano, ~1,9 mil tokens de saída) vira o maior custo.
  - Depois de testar um modelo, mantenha o preço dele declarado (K-046).

## 14. Saldo das contas de IA (ADR-051)

Três contas pré-pagas pagam a IA: Anthropic, OpenAI e Google AI Studio (Gemini, em R$). Nenhum console publica o
saldo por API, então a plataforma **estima**: a última leitura registrada menos o gasto de `ai_calls` naquela conta
desde ela.

- **Ver:** `GET /api/ai/balances` (também em `AiStatus.balances`). No painel:
  - chips do cabeçalho (só as contas em uso ou barradas);
  - popover "IA em uso": a conta de cada função e o saldo das três;
  - Configuração › IA: cartão "Saldo das contas", saldo na "Situação" e coluna "Conta · saldo" em "Por função";
  - Diagnóstico: azulejo "Saldo de IA" (a conta em uso mais urgente);
  - custo de IA (semana e execução): US$ por conta, a partir de `UsageReport.by_account`;
  - Comando: aviso antes de enviar quando uma conta em uso está baixa, sem leitura ou barrada.
- **Coletor de saldos** ([`tools/coletor-de-saldos`](../tools/coletor-de-saldos/LEIAME.md)): extensão do Chrome do
  dono que lê o saldo real na página de faturamento de cada console, a cada hora e quando ele abre a página, e
  registra com `source: "coletor"`. É o caminho principal de leitura, porque nenhum provedor publica o saldo por API
  (pesquisa de 28/09).
  - Instalação única: `chrome://extensions` → modo do desenvolvedor → "Carregar sem compactação".
  - A lista de páginas vem da plataforma (`console` de cada conta), e `ai.balance_consoles` no `config.yaml` fixa a
    conta de faturamento do AI Studio (`?billing=<ID>`).
  - O backend aceita a origem da extensão só no `POST /api/ai/balances/*`.
  - Uma leitura em US$ numa conta em R$ é convertida pelo câmbio da conta; leitura em R$ numa conta em US$ é recusada
    (400).
- **Registrar leitura:** `POST /api/ai/balances/{anthropic|openai|gemini}` com `{"balance": 9.25, "source":
  "console", "observed_at": "<ISO com fuso>"}`, ou pelo campo "Saldo no console agora" do cartão.
- **Limites:** `PUT /api/ai/balances/{conta}` com `warn_below`, `block_below` (na moeda da conta; `null` desliga),
  `units_per_usd` (câmbio) e `stale_after_h`.
- **Estados:** `unknown` (sem leitura), `ok`, `low` (abaixo do aviso), `blocked` (abaixo do bloqueio: a IA daquela
  conta para) e `exhausted` (o provedor recusou por cobrança e gravou leitura 0). `stale` marca leitura mais velha
  que `stale_after_h`.
- **Saúde:** `ai_balance_blocked`, `ai_balance_low`, `ai_balance_unknown` e `ai_balance_stale`, só de conta em uso.
- **Fora da estimativa:** o gasto que não passa por `ai_calls` (console, playground, scripts de avaliação que não
  gravam ali). Confira o console quando o aviso de leitura antiga aparecer.
- **Conciliação pelo relatório do provedor** (`planning/conciliacao.py`, adaptador
  `modules/billing/adapters/relatorios_de_custo.py`): com `ANTHROPIC_ADMIN_KEY` e `OPENAI_ADMIN_KEY` no `.env`, o
  backend lê o custo da organização (`/v1/organizations/cost_report`, valor em centavos; `/v1/organization/costs`,
  valor em dólares). Gasto externo = (provedor agora − base do provedor) − (local agora − base local), na mesma
  janela; ele sai do saldo como `external_usd`. Cache de 15 min em memória; `GET /api/ai/balances?refresh=1` força.
  - **Linha de base** (migração 053): o registro da leitura concilia no mesmo instante e grava o que o provedor e
    `ai_calls` já tinham na janela. Sem ela, o gasto de fora feito antes da leitura (que o console já descontou) saía
    de novo: medido em 28/09 na OpenAI, 7,96 estimado × 8,25 no console logo depois da leitura.
  - **OpenAI:** a janela começa à meia-noite UTC do dia da leitura e vai até agora (o relatório traz o dia corrente).
  - **A Anthropic só reporta dias FECHADOS** (medido em 28/09: o balde de hoje não sai, e `starting_at` hoje dá 400).
    O dia da leitura não se separa em antes e depois, então a janela dela começa na meia-noite SEGUINTE e termina à
    meia-noite de hoje. O gasto de fora no resto do dia da leitura fica de fora: a estimativa fica otimista em no
    máximo meio dia.
  - O Google AI Studio não publica o crédito pré-pago: o Gemini fica só com a estimativa local.

**Biografia inteira e "o pedido manda" (28/09, decisão do dono).** O bloco `<persona>` passou a levar a biografia
inteira (`PERSONA_BIO_FIELDS`, 16 campos em ordem de prioridade, orçamento de 350 tokens e no máximo 6 itens por lista)
e abre com a linha "como usar esta persona" (`USO_DA_PERSONA`): o pedido de quem opera manda no QUE fazer e dizer; a
persona só dá o jeito — voz, palavras, referências e reações —, sem contrariar nem ampliar o pedido. `SOCIAL_SYSTEM`
ganhou a mesma regra.

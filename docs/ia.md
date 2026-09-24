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
| Por objetivo | teto de tokens e de chamadas por objetivo (achado #99); a recusa por estouro agora grava linha em `ai_calls` mesmo sem chamada real (`_registrar_orcamento_estourado`) | por objetivo |

## 7. Receitas, fluxos e provas locais

Ver [docs/produto.md §2](produto.md) para os conceitos. Mecanismo de custo, resumido:

- **Receitas** (`ai.recipes: replay`): a IA aprende uma etapa uma vez; repetição por seletor (resource-id/texto)
  sem nova chamada. Seletor que não casa exatamente um elemento → divergência (só aquela etapa volta para a IA).
  3 falhas seguidas → quarentena. `shadow` aprende e compara sem agir.
- **Fluxos** (`ai.flows: true`): execução 100% comprovada vira plano congelado; comando repetido com outros
  parâmetros pula o planejador.
- **Provas locais** (item 7.5): `backend/app/taskqueue/proofs.py` — conferência determinística pela árvore local
  (`==` exato em `find_selector`) antes de chamar o modelo, no catálogo do Instagram. Medido: Instagram 2/3→3/3
  de sucesso comprovado, US$0,20→0,08 por caso.

## 8. Avaliação — todos gastam API

| Script | O que faz | Gasta |
|---|---|---|
| `scripts/eval-run.ps1` | Bateria congelada (`config/eval-set.yaml`): sucesso comprovado × US$ por configuração | sim, pede `-Yes` |
| `scripts/eval-rejudge.ps1` | Rejulga com o modelo caro (Opus 5), por imagem, capturas que o barato já julgou; mede concordância | sim, pede `-Yes` |
| `scripts/probe-models.py` | Sonda o que cada modelo aceita e se o cache pega; com `--yaml` já imprime o bloco `ai.models` pronto | sim (poucos centavos), pede `--yes` |

Nenhum dos três foi rodado na rodada de 23-24/09 por regra da chamada (sem gasto pago sem autorização). A
comparação "linha de base × configuração atual" que o próprio projeto exige antes de adotar uma alavanca de
custo (decisão 7 do plano-100) **segue pendente**.

## 9. Observabilidade

- `ai_calls` — uma linha por chamada de IA (papel, modelo, tier, tokens, `ms`, `ok`, `error_kind`,
  `requested_model`/`fallback`/`provider`).
- `GET /api/usage` (`backend/app/api.py:414`) — custo por papel/modelo/tier dos últimos N dias (padrão 7),
  chamadas por execução/objetivo, erros por tipo, linhas de fallback. Não chama o provedor, só lê o já gasto —
  mesma fonte que `scripts/usage-report.ps1`.
- Aba IA do painel mostra o mesmo por execução, incluindo cache ativo/inativo por papel.
- Estimativa por fluxo antes de rodar: `GET /api/flows/cobertura` ganhou `estimated_usd` (item 7.7) — etapas sem
  receita × custo mediano por etapa só-IA dos últimos 7 dias, por papel; sem histórico, `null` ("sem base").

## 10. Modelo local — Ollama

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
Confirmado em `backend/app/config.py` (`AiCfg`, `RotateCfg`):

| Alavanca | `config.example.yaml` | Padrão do código (`config.py`) |
|---|---|---|
| `ai.screenshot_max_side` | 768 (linha 155) | 1280 (`config.py:349`) |
| `ai.max_hierarchy_elements` | 60 (linha 156) | 140 (`config.py:350`) |
| `ai.image_policy` | `auto` (linha 158) | `always` (`config.py:355`) |
| `ai.recipes` | `replay` (linha 164) | `off` (`config.py:378`) |
| `ai.flows` | `true` (linha 165) | `false` (`config.py:379`) |
| `ai.pathfinder_wait_s` | 240 (linha 166) | 0 (`config.py:380`) |
| `android.auto_start_devices` | `true` (linha 146) | `false` (`config.py:271`) |
| `android.max_online_devices` | 2 — comentado como vagas desta máquina (linha 147) | 10 (`config.py:272`, teto do host; cada worker traz o próprio `max_slots`) |

## 12. O que foi MEDIDO (não confundir com configuração prevista)

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

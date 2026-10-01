# JEV-PILOT: pesquisa e piloto isolado do modelo de decisão Jev (TypeSafe AI)

> **Estado (2ª rodada, 2026-10-01):** Piloto A sobre o código real **`BLOCKED_PRIVACY`** ([§35](#35-privacy-due-diligence-2026-10-01-segunda-rodada));
> smoke sintético **preparado e travado** ([§36](#36-smoke-test-sintético-preparado-não-executado)); **nenhuma chamada real ao Jev foi feita**
> (`REAL_JEV_CALLS_RUN = NO`).
> Frente **independente do W8**: não toca aparelho, banco, scheduler, `RoutingProvider`, `config.yaml`, `.env`, deploy nem conta.
> Base: `main` = `origin/main` = `3eba639` no início (2026-10-01). Branch `claude/jev-pilot`. Ao fechar, `origin/main` estava em
> `b395ce6` (+2 commits, só `docs/handoffs/*` do W8, +166 linhas): **não integrado nem rebaseado**; os âncoras do golden não mudam. Tudo se remove apagando
> `experiments/jev/` e este arquivo (ver [§32](#32-reversibilidade)).
>
> **Convenção de fonte** (em todo o texto): `OFFICIAL` (TypeSafe), `COMMUNITY` (terceiros que se declaram não oficiais),
> `INDEPENDENT` (imprensa/gateways), `AUTHOR-REPORTED` (número medido pelo próprio autor), `UNVERIFIED CLAIM`, e
> `UNKNOWN / NOT DOCUMENTED` quando não se achou. `CODE` = lido no código-fonte, nada executado. `MEASURED` = medido por
> este piloto, offline, sem Jev. Provas seguem a política do projeto: `real` / `simulated` / `not_run`.

## 1. Resumo executivo

| Pergunta | Resposta com a evidência de hoje | Prova |
|---|---|---|
| **Q1** Jev reduz contexto no Claude Code sem perder qualidade? | **Plugin publicado (`BorisLeMeec/jev`): NO_GO para instalar agora**, por risco lido no código (hook envia o arquivo inteiro a terceiro sem filtro de caminho; binário ausente no marketplace; sem suporte documentado a Windows). **Pipeline próprio de rerank sobre o nosso repositório: INSUFFICIENT_EVIDENCE**: o laboratório está pronto, o custo previsto é ~US$ 0,01 e o que bloqueia é a aprovação de enviar código-fonte (e a chave/waitlist), não o custo. | CODE; `not_run` |
| **Q2** Jev como "System 1" de decisões pequenas antes do Sonnet/Opus? | **INSUFFICIENT_EVIDENCE; candidato a SHADOW**. O contrato do Jev (resposta tipada, escolha fechada, `confidence`) casa com o desenho. Mas o histórico real tem só 36 casos rotulados que exigiram forte/humano em 587 rotulados (94 % são "barato"); "sempre barato" já acerta 93,9 %. O ganho possível é pequeno e só um shadow mede. | `MEASURED` (histórico) + `not_run` (Jev) |
| **Q3** Escolher uma ação entre um conjunto FECHADO já validado? | **Não avaliar agora.** O ator hoje emite `tool+args`, não `action_id`; construir o conjunto fechado é um componente novo. O único conjunto fechado existente é o catálogo de capabilities do planejador (papel `plan`, texto livre em português). Referência arquitetural útil: `jev-android` (comunitário, não oficial). | CODE; `not_run` |
| **Q4** Custo/latência/privacidade/dependência maiores que o benefício? | **Custo: desprezível** (US$ 0,042 por 1 M de tokens de entrada; piloto A ≈ US$ 0,010–0,013, replay do histórico inteiro ≈ US$ 0,006–0,008, ambos **PROXY**). **Privacidade e fornecedor: dominantes.** Código/estado sai para a TypeSafe (EUA), retenção padrão de entradas da API **UNKNOWN**, sem SLA (MCA: "AS IS"), limites "ajustados dinamicamente", acesso por waitlist, modelo inglês-primário. | `OFFICIAL` |

**Veredito geral: `INSUFFICIENT_EVIDENCE`.** Nenhum dos três usos tem número do Jev. O que existe são: documentação oficial lida
em texto bruto, leitura do código do plugin, um golden set conferido à mão, baselines medidas, limiares escritos **antes**
de qualquer resultado, e um adaptador real **inerte**. A decisão que falta é do dono: [§34](#34-próxima-decisão-do-dono).

## 2. Escopo

- Dentro: pesquisa web, leitura de código de terceiros, mapeamento da nossa arquitetura de IA (somente leitura), laboratório
  `experiments/jev/` (stdlib), golden set, baselines, provedor falso, adaptador real inerte, extração de histórico seguro
  para replay, limiares, threat model.
- Fora, de propósito: instalar plugin (global, usuário, projeto ou hook), qualquer aparelho/emulador/conta, W8
  (android-05, android-09, worker, WireGuard, rede), deploy, reinício, alteração de `routing.py`/`executor.py`/`config.py`,
  migração, IA paga do projeto, chave de API.
- Método de leitura do histórico: o SQLite do central foi aberto `mode=ro` e só 11 colunas de `steps` e 13 de `ai_calls`
  de uma lista permitida (nenhum texto livre). Saída em `data/jev-pilot/` (ignorado pelo Git). O checkout principal ficou
  intocado.

## 3. O que Jev é

`OFFICIAL` ([docs.typesafe.ai/models](https://docs.typesafe.ai/models), [/concepts/system-one](https://docs.typesafe.ai/concepts/system-one),
[blog](https://typesafe.ai/blog/introducing-system-one-models-and-jev)): Jev é o primeiro "System One model" da TypeSafe AI. Recebe
um `state` (texto/objeto JSON/lista) e um mapa de **perguntas tipadas** e devolve respostas tipadas com probabilidades, nunca
texto livre. Todas as perguntas rodam em paralelo sobre o mesmo `state`, numa só requisição. Treinado com "RLCD" (alegação do
fornecedor). Versão atual `jev-1.13.0`; `jev-latest` é alias que **muda sem aviso** (por isso o piloto fixa `jev-1.13.0`).

Lançamento ~15/09/2026 em *early access* com waitlist (`INDEPENDENT`: imprensa; o post oficial menciona waitlist). Aporte de
US$ 40 M: `UNVERIFIED CLAIM` (só resultados de busca).

## 4. O que Jev não é

- **Não gera texto, código nem conversa** e não substitui o LLM de um agente de código: a própria documentação diz isso
  ([/introduction/coding-agents](https://docs.typesafe.ai/introduction/coding-agents), `OFFICIAL`, lida em texto bruto).
- **Não vê imagem/áudio/vídeo** ("text only", `OFFICIAL` models). Nossas decisões do ator hoje usam árvore de UI **e** imagem
  (`ScreenInput.jpeg`); a parte visual ficaria fora.
- **Não se abstém**: baixa confiança é só sinal; rotear é com o chamador (`OFFICIAL` /confidence).
- **Não é bom em** números/contagem/datas, indireção, estado grande cheio de irrelevância, conteúdo adversarial, e lê "de
  forma literal" ([/model-jaggedness/jev-1.13](https://docs.typesafe.ai/model-jaggedness/jev-1.13), `OFFICIAL`, revisado 2026-09-17).
- **Inglês é o idioma principal**; outros idiomas "não igualmente bem; teste no seu conteúdo" (`OFFICIAL` models). O nosso
  código/comentários/estado estão em português: ponto de teste obrigatório.
- A org `jev-ai`/`codaaiteam` no GitHub **não é a TypeSafe** (se declara "unofficial"). O GitHub oficial é `typesafe-ai`
  (`skills`, SDKs).

## 5. Fontes oficiais

Lidas em texto bruto com `curl` em 2026-10-01 (não por resumo): `docs.typesafe.ai/api.md`, `/models.md`, `/confidence.md`,
`/model-jaggedness/jev-1.13.md`, `/legal.md`, `/introduction/coding-agents.md`, `/cookbooks/semantic_find.md` e o índice
`/llms.txt`. Demais (preço no blog, política de privacidade, Termos, SDKs, página `evals`) vieram do relatório de pesquisa
por resumo de ferramenta — **rotuladas no texto quando dependem disso**. Ver [§33](#33-limitações-da-pesquisa).

## 6. API

`OFFICIAL` (`docs.typesafe.ai/api.md`, texto bruto):

```http
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

Request `{state, model, questions}` (todos obrigatórios). Três tipos de pergunta, com `type` e `instructions` (string, objeto
ou lista):

| Tipo | `criteria` | Resposta | `confidence` |
|---|---|---|---|
| `noul` (sim/não) | opcional `{"true": …, "false": …}` | `{"type":"noul","noul":0..1}` | **não** tem |
| `choice` | **obrigatório**, mapa `opção → descrição\|null`, **máx. 255 opções** | `choice`, `probabilities` (somam 1), `confidence` | sim |
| `score` | **obrigatório**, lista de 2 a 10 níveis | `score` (fracionário), `legend`, `probabilities`, `confidence` | sim |

Resposta `{model, answers, usage:{input_tokens, output_tokens}}`; `model` devolve o ID versionado que respondeu. Erros:
`401`, `422`, `429`, `529` (corpo JSON "descrevendo o erro"; formato exato **UNKNOWN**). `GET /v1/models` lista aliases.
Fórmula pública de confiança para Choice: `(N·pico − 1)/(N − 1)` (`OFFICIAL` /confidence; usada no provedor falso).

Pontos relevantes para o desenho:
- A chave da pergunta **não** vai ao modelo; só `instructions`/`criteria` e `state`.
- Para o nosso caso de **escolha fechada**, `choice` com `criteria` = conjunto permitido é exatamente o contrato; o piloto
  valida a resposta contra esse conjunto e trata escolha fora dele como erro (`UnknownChoice`), nunca como ação.
- Idempotência, headers de rate limit, corpo de erro, timeout/retry padrão: **UNKNOWN / NOT DOCUMENTED**.

## 7. Pricing

| Item | Valor | Fonte |
|---|---|---|
| Entrada | **US$ 0,042 / 1 M tokens** (US$ 42 / bilhão) | `OFFICIAL` /models |
| Saída | **grátis** | `OFFICIAL` /models |
| Free tier | **UNKNOWN / NOT DOCUMENTED** (um terceiro afirma que não há: `COMMUNITY`) | — |
| Acesso | waitlist / early access; também via Vercel AI Gateway, OpenRouter, Pydantic AI Gateway, Cloudflare Workers AI | `OFFICIAL`/`INDEPENDENT` |
| Rate limit | 100 K tokens/s e 40 req/s, "ajustados dinamicamente… sem aviso" | `OFFICIAL` /models |
| Contexto | 64 k tokens por requisição (`state` + todas as perguntas); **32 k para `state` + a maior pergunta** | `OFFICIAL` /models |
| Latência | 70–500 ms ponta a ponta; 0,114 s vs 8,566 s em workflow | `AUTHOR-REPORTED` |
| Latência independente | **não encontrada** | — |

Relato de comunidade: conta nova via Vercel AI Gateway recebeu ~98 % de `429` durante ~2 dias (`COMMUNITY`).

## 8. Data handling / privacy

- **Não treina nem faz *fine-tuning* com seus prompts/entradas** (`OFFICIAL`: política de privacidade; reafirmado em
  /models: "not fine-tuned or LoRA-adapted with customer data").
- Existem **DPA**, Master Customer Agreement e política de privacidade, e **retenção zero (ZDR) para clientes enterprise**
  sob contato comercial (`OFFICIAL`, [/legal](https://docs.typesafe.ai/legal)). Na 2ª rodada li os três em texto bruto
  ([§35](#35-privacy-due-diligence-2026-10-01-segunda-rodada)); a retenção **padrão** das entradas da API continua **UNKNOWN**.
- Hospedagem nos EUA (`OFFICIAL` política); região UE não documentada. Subprocessadores: **UNKNOWN**.
- Sem SLA: o MCA fornece o serviço "AS IS" e não garante operação ininterrupta (§9.3). **Correção (§35):** a frase "descontinuar sem
  aviso" da 1ª rodada é dos Termos de Uso do *site*, não da API.
- Via Cloudflare Workers AI a Cloudflare anuncia ZDR (`INDEPENDENT`, parceiro, não é política da TypeSafe) — rota possível
  para reduzir risco de retenção, **não avaliada**.

## 9. SDKs

`OFFICIAL`: Python `typesafe-sdk` (≥ 3.10; `TypeSafeClient`/`AsyncTypeSafeClient`, `RetryPolicy`), JS/TS `@typesafe-ai/sdk`
(Node ≥ 20, MIT), skill de agente `typesafe-ai/skills`. **Go: não documentado.** REST: sim. Variável de ambiente oficial da
chave: **`TYPESAFE_API_KEY`** (também `TYPESAFE_BASE_URL`, `TYPESAFE_LOG_LEVEL`). **`TYPE_SAFE_AI_KEY` não existe na
documentação**; o plugin comunitário aceita `TYPE_SAFE_AI_KEY`, `TYPESAFE_API_KEY` e `TYPESAFE_AI_API_KEY` (`CODE`). O
piloto usa só `TYPESAFE_API_KEY` e **não depende de SDK** (HTTP puro com `urllib`, para zero dependência nova).

## 10. Limitações

- Estado grande e irrelevante degrada a precisão: "filtre primeiro, envie só o que a pergunta precisa" (`OFFICIAL`
  jaggedness #5). Isso pesa a favor de um pipeline **BM25 → Jev** (shortlist), e não de "mandar o repositório inteiro".
- Literalidade, indireção, números, datas, contagem, "gerar" (jaggedness #1–#9).
- `jev-latest` muda sem aviso; limiares ajustados contra uma versão perdem validade → **fixar `jev-1.13.0`**.
- Rate limits dinâmicos e sem SLA → todo uso precisa de *fail-open* para o caminho atual.
- Baixa confiança ≠ abstenção.
- Português não é o idioma principal.

## 11. Ecossistema comunitário

Todos `COMMUNITY` (não oficiais), pesquisados por metadados; código lido só do plugin (§12) e do `jev-android` (§14):
`BorisLeMeec/jev` (33★, plugin Claude Code), `tamaratran/jev-pruner` (154★), `IAmUnbounded/save-token-jev-clean` (79★),
`shitianfang/jev-use` (39★), `jev-compact`, `claude-jev`, `imsukhe/jev` e 5 *forks* do plugin; mobile/Android:
`dougsong/jev-android`, `Friedjof/jev-mobile`, `antiyro/jevdroid`, `SomeshSampat2/jev-android-super`,
`ryacub/jev-android-mcp`, `Doringber/jevii-android`, `ZTRRTUO/Jev-PhoneControl` (licença não comercial), `Aben25/jev-sim`,
`agugliotta/jev-kmp`. Quase tudo tem dias de vida. O único oficial relacionado a agentes é `typesafe-ai/skills`, que **ensina o
agente a escrever integrações com Jev e não reduz contexto**.

## 12. Plugin Claude Code (`BorisLeMeec/jev`)

`COMMUNITY`, **não oficial**: usuário pessoal, 1 contribuidor, 6 commits, criado 2026-09-17, sem releases/CI, MIT. Análise por
`CODE` (HEAD de 2026-09-18, via API do GitHub; **nada instalado nem executado**). Relatório completo com arquivo:linha no
trabalho de pesquisa; os achados que decidem:

| Tema | Achado |
|---|---|
| Instalação | `claude plugin marketplace add` + `install`; o hook chama `${CLAUDE_PLUGIN_ROOT}/bin/jev`, mas `bin/` está no `.gitignore` e **não existe no repositório**: instalar pelo marketplace provavelmente deixa o hook sem binário até alguém compilar com Go. README diz Go 1.22+; `go.mod` pede 1.26.1. Windows: não documentado. |
| Hook | **Só `PreToolUse` com matcher `Read`**, timeout 20 s. **Não há hook para Grep/Search/Glob/Bash**; "searches" no README é a skill mandando o agente chamar `jev find`/`jev ask`. |
| Interceptação | Lê o arquivo **inteiro**; passa (bypass) se `offset`/`limit`, < 400 linhas, > 80 KB, objetivo < 12 caracteres, sem chave, erro, confiança < 0,60, `JEV_HOOK_DISABLE`. Senão devolve janela `max(150, linhas/5)` reescrevendo `offset/limit` do `Read`. |
| "Modos" | `reduce/screen/verify/locate` são **estágios internos do `find`**, não modos. `find`: esqueleto de cada arquivo → Noul por arquivo → conteúdo completo dos 8 melhores → Choice por chunk nos 3 melhores. `ask`: conteúdo completo de todos os arquivos do escopo. |
| API | `POST /v1/systemone`, `jev-latest` (não fixa versão), Bearer da variável de ambiente, até 5 tentativas em 429/5xx. |
| Cache/log | Sem cache local nem arquivos temporários; único log `~/.jev/usage.jsonl` com contadores. Cache remoto: UNKNOWN. |
| Falha | *Fail-open* por desenho; offline pior caso ≈ 20 s por `Read` grande (inferência); `panic` de Go sairia com código 2 e bloquearia o `Read` (inferência, sem `recover`). |
| Reversível | Não altera arquivos do projeto; desligar com `JEV_HOOK_DISABLE=1`; desinstalar não documentado. |
| Possível defeito | `additionalContext` fica na raiz do JSON, não em `hookSpecificOutput`; pela doc de hooks lida por resumo, o aviso "Read estreitado" **talvez nem chegue ao modelo** (inferência, não executado). |

### O que sai da máquina (plugin)

| Origem | Enviado a `api.typesafe.ai` |
|---|---|
| Hook `Read` (automático) | **Conteúdo inteiro** do arquivo em chunks (com comentários), **só o nome-base** do arquivo, e até **600 bytes da última mensagem do usuário**. Aplica-se a **todo** `Read` sem `offset/limit` de arquivo com ≥ 400 linhas e ≤ 80 KB, **sem filtro por caminho, sem detecção de segredo, sem consultar `.gitignore`**. |
| `jev find` | esqueleto (≤ 40 linhas, inclui linhas `const/var`) de **cada** arquivo + conteúdo completo dos 8 melhores |
| `jev ask` | conteúdo completo de **todos** os arquivos do escopo; caminho explícito pula os filtros |

Para o **nosso** repositório isso significa: `config/`, fixtures de teste, SQL, logs e docs ≥ 400 linhas iriam inteiros a
um terceiro, e o goal pode carregar segredo colado no chat. Risco de quebrar sessão: baixo; risco de dados sensíveis: **alto**.
Risco de falso negativo/ocultação: o próprio autor admite que o hook é "a única parte que pode ocultar código".

## 13. Benchmarks publicados

Todos `AUTHOR-REPORTED`; **nenhuma validação independente encontrada**; nenhum reproduzido por nós.

| Alvo | Amostra | Resultado relatado |
|---|---|---|
| `find` recuperação | 30 consultas (24 respondíveis + 6 negativas), 3 repositórios privados Go/Flutter/React, 469 arquivos | P@1 0,96 vs grep 0,08 / BM25 0,33 |
| `find` tokens | 7 pares de execuções de agente, não repetidos | −30 % faturado; menos turnos (12,4 → 9,1) |
| `ask` | 9 perguntas, 217 julgamentos | precisão 1,00, F1 0,97 (limiar **ajustado nas mesmas 9**); −38 % faturado; 1 falso negativo |
| Hook | 52 alvos em 11 arquivos (Hugo, Prometheus) | recall 50/50 quando cabe; −41 % faturado, −78 % de contexto em 3 pares; **+1,25 s por leitura** |
| Tempo de parede | — | mediana pareada do `find` 1,03 (**3 % pior**); o autor diz que economiza tokens, não tempo |

Limitações: amostras minúsculas; "1,00" = nenhum erro observado, não taxa; conjuntos de `find`/`ask` **não publicados**
(irreproduzível); grep-only como único baseline (sem BM25 em todos os casos nem subagente Explore); stacks Go/Flutter/React,
**nada de Python**; ganho de tokens vem de **menos turnos** em buscas puras; contagens internas inconsistentes
(50/50 vs 28+23). **Nada disso prevê o nosso repositório.** Por isso §22–§25 mede o nosso, de forma independente.

## 14. `jev-android`

`dougsong/jev-android`, `COMMUNITY`/**não oficial** (README:339 "not an official … TypeSafe … SDK"; dono pessoal; inspirado
em `browser-use/jev-ultrafast`). Maturidade baixa: criado e último push em 2026-09-20 (11 commits em ~3 h), 7★, 0 forks,
0 *issues*, sem releases/CI, MIT, **v0.3.7 com "API still unstable"**, **não está no Maven Central**. Auto-relata 184 testes
offline + 5 instrumentados num Samsung (não verificado).

**Valor: referência arquitetural, não dependência.** Padrão confirmado (`CODE`): estado (árvore de acessibilidade do pacote
permitido, ≤ 220 elementos, nós de senha pulados) → catálogo de ações permitidas montado localmente → `action_id` opaco
(`a<hash>_<n>`, o hash do *snapshot* invalida IDs de outra tela; o modelo nunca devolve coordenada, pacote ou id de nó) →
**validação local** (`choices.actions[id] ?: reject`; `DecisionRules.validate`; `ActionGate` do hospedeiro pode barrar) →
**executor local** que confere se a tela ainda é a mesma e **nunca repete** ação rejeitada/incerta. Texto a digitar vem de
`text_key`, nunca livre. Sem screenshot; sem senha. Usa Jev (`choice`) e também DeepSeek com função estrita.

Riscos: a árvore de UI visível (rótulos, valores, `resource_id`) vai ao provedor — **sem redação geral de dados pessoais**
(mensagens, nomes, saldos de qualquer app permitido); exige serviço de acessibilidade com poder amplo; autor único.
**Não adicionar Gradle, não copiar SDK, não instalar** (regras desta frente). Compatibilidade com emulador/Instagram e
detecção de automação: **UNKNOWN**; o `CLAUDE.md` proíbe evasão de antibot.

## 15. Arquitetura atual de IA do Android (somente leitura)

`CODE` em `3eba639`. Funções (`config.py:28`): `plan`, `decide`, `verify`, `escalation`, `social` (`image` fica fora delas).

| Peça | Onde | O que faz |
|---|---|---|
| Hub | `planning/routing.py:70` `RoutingProvider` | uma instância de provedor **por função**; porta única de toda chamada de IA |
| Despacho | `routing.py:200` `_call` | confere orçamento (`_budget`, 152), saldo (`_saldo`, 175), prazo por função (`_one`, 246) e vagas |
| Fallback | `routing.py:219-238`, `_com_provedor` 312 | **sem fallback pago silencioso**: só cai se `ai.roles.<papel>.fallback_provider` estiver declarado; a queda vira evento e linha em `ai_calls` |
| Capacidade | `routing.py:331` `_valida_capacidade` | função que precisa de visão apontada a modelo sem visão **recusa subir** |
| Escalonamento | `routing.py:291-299` | `decide` com `tier>0` e `verify` com `escalate` vão ao papel `escalation` |
| Interface | `planning/provider.py` | `AIProvider` (plan/decide/verify/social/persona), `DecisionRequest` (`ctx`, `screen`, `history`, `tier`), `Decision(tool, args)`, `Verdict(satisfied: yes\|no\|uncertain\|unprovable)` |
| Decisão determinística | `taskqueue/executor.py:1378-1409` | **receita primeiro**; na divergência (`RecipeDiverged`, `recipes.py:46`) a IA assume; só retorno contado, sem subir de tier |
| Tier | `executor.py:1422`, `side_effect_tier` 2374 | `tier = 1` se `base_tier` (efeito externo conforme risco, ou nova tentativa), erros seguidos, ação repetida, piso do modelo local |
| Risco/efeito | `side_effect_tier` | `by_risk`: risco alto ou sem catálogo → forte; médio sem `commit_selector` → forte; senão barato |
| Pós-condição | `executor.py:1976` `_postcondition_holds`, `_verify` 1987 | conferência barata sem modelo; depois **prova local** (`_prova_local` 2169 → `proofs.local_proof_holds` 59) **antes** do verificador de modelo |
| Guardas de tela | `executor.py:1296`, 1353 | conta travada, ANR, tela sensível/desafio **antes** da receita e do ator; desafio/2FA/CAPTCHA seguem com a pessoa |
| Aprovação | `social/policy.py:318-331, 378`; `state.py:2155-2172` | política por ação (`autonomous/approval_required/manual_only/disabled`); DM fria **sempre** exige aprovação (ADR-055); objetivo fica `blocked_kind='approval'` |
| Orçamento | `routing.py:152` | teto em US$ por execução e por dia, para **todo** caminho de IA |
| Catálogo | `conhecimento/apps/*/catalogo.yaml` | por capability: `risk`, `side_effect`, `default_policy`, `limit_bucket`, `commit_selector`, `local_proof` |

Duas observações que mudam o fit:
1. A decisão do ator **não** é de conjunto fechado: é `tool + args` livres (alvo resolvido na árvore). Escolha fechada só
   existe quando o planejador seleciona **ações nomeadas do catálogo**.
2. Os guardas (consent, política, orçamento, prova local, pós-condição, travas de conta) já são **código**, a jusante da IA.
   É exatamente a propriedade que o desenho exige que o Jev nunca contorne.

## 16. Fit arquitetural

O Jev encaixa **como componente de decisão tipada dentro do código**, que é como o fornecedor o posiciona, **depois** da
regra determinística e **antes** dos modelos grandes, sempre com `confidence` baixa/erro/timeout caindo no comportamento de
hoje. Não encaixa como substituto do `decide` (precisa de visão, `tool+args` e português) nem como filtro de contexto
embutido no agente (não é o uso endossado).

```
ESTADO → regra determinística (receita, prova local, side_effect_tier) → resolveu? ──sim──▶ executa
                                                     │ não
                                          decisão pequena e FECHADA?
                                           │ sim                    │ não
                                         JEV (shadow) ─ confiança alta? ─ não ─▶ Sonnet/Opus
                                           │ sim                                  (como hoje)
                                  guardas locais (risk/side_effect/approval/budget/consent/…)
                                           ▼
                                    executor → pós-condição
```

## 17. Caso: model routing (Candidato 1)

Rotas: `DETERMINISTIC | LOCAL_MODEL | CHEAP_AI | SONNET | OPUS | HUMAN` (no replay, quatro: `DETERMINISTIC | CHEAP_AI |
STRONG_AI | HUMAN`). Entradas seguras: `role, risk, side_effect, recipe_exists, local_proof, attempt_number, divergence,
previous_model, estimated_cost…`. Saída: `route + confidence` por `choice` fechado.

Avaliação com o histórico real (`MEASURED`, §26–§27): domínio pequeno, 94 % "barato", "sempre barato" = 93,9 %, regra atual =
79,4 %, subescalada perigosa = 6,1 % para os dois. Para ter valor, o Jev precisa capturar parte dos 36 casos fortes/humanos
sem custar o que economiza. **Com 7 atributos e 36 positivos, uma árvore de decisão local faria o mesmo** — o argumento
a favor do Jev é só zero-shot e calibração, ainda por medir. **Candidato real, mas de valor esperado baixo.**

## 18. Caso: action selection (Candidato 2)

Padrão `jev-android`: o software monta `A01…An` (cada um é uma ação **já existente e já validada**), o Jev devolve
`action_id + confidence`, o executor valida e age. Requisitos que **não existem hoje**: um construtor de conjunto fechado a
partir da `UiTree`, `action_id` ligado a *snapshot*, invalidação por tela. O Jev **não pode** inventar seletor, ADB, tool,
comando, shell nem `action_id`; qualquer coisa fora do conjunto é `UnknownChoice` → erro → fallback (já coberto por
`provider.parse_choice` e testes). Mesmo com `confidence = 1.0` ele não altera `risk/side_effect/approval/budget/consent/
session/policy/local_proof/guards/post-condition/ownership/device lock/account rules` — essas conferências ficam depois dele.
**Não implementar agora.** Considerar só depois de routing em shadow e depois de o dono resolver §21 (privacidade da árvore de UI
de contas reais) — o que, no Instagram, inclui conteúdo de mensagens.

## 19. Caso: classificação (Candidato 3)

`technical_error | retryable | needs_input | human_required | approval_required | escalation_required | ambiguous |
safe_to_retry` a partir de `failure_kind` e metadados (sem texto de tela). Classificação **não executa nada**. Já existe
`attempts.failure_kind` (migração 055) e `learning/` para o assunto; o ganho marginal do Jev sobre as regras atuais é
**não medido**. **Segundo melhor candidato para shadow** porque os dados de entrada são só categorias.

## 20. Caso: recuperação de contexto no Claude Code (Candidato 4)

É o **primeiro benchmark implementável**, porque (a) a entrada é só código-fonte do repositório, (b) tem gabarito
verificável, (c) o custo é ≈ centavos. Arquitetura candidata (e **pré-registrada** em `thresholds.json`): pergunta →
**BM25 local** (shortlist) → **Jev reordena por `choice`** (+ `noul` "existe resposta?", padrão do cookbook oficial
*semantic_find*/*Re-ranking*) → entrega dos **k menores trechos** com probabilidade acumulada ≥ 0,80 → Claude. É o desenho que
a documentação do Jev recomenda ("filtre primeiro; envie só o que a pergunta precisa") e que evita enviar o repositório
inteiro. Não é o mecanismo do plugin comunitário (que intercepta `Read` e manda o arquivo inteiro).

## 21. Threat model

Categorias e o que cada integração enviaria:

| Categoria | Plugin (`BorisLeMeec/jev`) | Pipeline A deste piloto (rerank) | Runtime shadow (futuro) | `jev-android`-like (futuro) |
|---|---|---|---|---|
| SOURCE_CODE | **sim**: arquivo inteiro de todo `Read` 400+ linhas ≤ 80 KB; `find` esqueletos + 8 completos; `ask` completo | **sim**: até 30 chunks (≤ 100 KB de payload) de `backend/app` e `backend/tests` por pergunta | não | não |
| SECRETS | **sem filtro**: sem detecção, sem bloqueio de `.env`/`*.pem`/`config.yaml` no hook | redação + **bloqueio** por achado duro; `redact.is_sensitive_path`; chunks com achado duro nunca entram | não (só categorias) | senha pulada; resto sem redação |
| PERSONAL_DATA / CUSTOMER_DATA / ACCOUNT_DATA | podem estar em qualquer arquivo lido | corpus é código; não inclui `data/`, dumps, personas | **não**: só `side_effect, risk, capability, app, recipe_exists` | **sim**: texto visível do app (mensagens, nomes) |
| DEVICE_DATA / SCREENSHOT | — | não | não | árvore de UI (sem screenshot) |
| LOGS / CONFIGURATION | enviados se o `Read` os alcançar | fora do corpus (`.py` de app e testes) | não | — |
| PATHS | só nome-base no hook; caminho relativo em `find` | caminho relativo do chunk (`backend/app/...`) | — | `resource_id`, pacote |
| INTERNAL_DOCS | enviados se lidos | fora do corpus | não | — |
| Última mensagem do usuário | **até 600 bytes** como "goal" | não | não | objetivo da tarefa |

**Nunca deve sair no piloto:** senha, token, cookie, `session_id`, chave privada, chave de API, credencial de conta, conteúdo
real de cliente, conteúdo de conta social real, screenshots. Controles implementados: `redact.py` (achados **duros** = chave
privada, Bearer, chave de API, JWT → **bloqueiam** o payload; **moles** = `senha=`/`token=`/e-mail/caminho de usuário →
redigidos e contados), `RealJevProvider._sanitize`, exclusão de chunk bloqueado do shortlist, e testes. **Achado de
verificação:** o corpus `backend/tests` tem **6 fixtures com padrão de segredo** (valores falsos, conferidos à mão em
2026-10-01: 4 literais `Bearer …` de teste, 1 literal de cabeçalho PEM usado como entrada inválida, 1 JWT de exemplo); nenhum em `backend/app`. Elas continuam
bloqueadas por construção — se o dono aprovar, o shortlist simplesmente as exclui.

Resíduos sem mitigação do nosso lado: retenção padrão na TypeSafe (**UNKNOWN**), subprocessadores, jurisdição (EUA),
limites `429` dinâmicos, serviço "AS IS".

## 22. Piloto A (Claude Code)

`experiments/jev/` (stdlib; **nenhum import de `backend/`**; nada do runtime o importa):

```
experiments/jev/
  README.md  pytest.ini  golden.json  thresholds.json
  benchmark.py  provider.py  fake_provider.py  metrics.py  report.py
  baselines.py  corpus.py  golden.py  textutil.py  redact.py  netguard.py  replay.py
  tests/  (64 testes)
```

- `provider.py`: `DecisionProvider` (`evaluate/choose/noul/retrieve`) falando o **formato de fio oficial**; `RealJevProvider`
  **inerte** (exige `enabled=True` **e** `TYPESAFE_API_KEY`; `max_calls`; redação/bloqueio; backoff só em `429/529`;
  `401/422` falha dura; o erro nunca carrega corpo, cabeçalho nem chave).
- `fake_provider.py`: `FakeJevProvider` com `HIGH_CONFIDENCE, LOW_CONFIDENCE, TIMEOUT, ERROR, INVALID_RESPONSE,
  UNKNOWN_CHOICE`. **Fala o mesmo formato de fio e passa pelo mesmo parser** do real; a pontuação é lexical e **não diz nada
  sobre a qualidade do Jev**.
- `benchmark.py`: padrão = baselines **sem provedor e sem rede** (`no_network()` bloqueia `connect`/`getaddrinfo`);
  `--provider fake` roda o pipeline; `--provider jev` exige `--confirm-external-send` **e** a chave; `--estimate-only`
  estima o custo sem rede.
- `report.py`: serializa e avalia **mecanicamente** os limiares; só emite veredito para rodada `jev` real (fake/baseline →
  `NOT_EVALUATED`).

## 23. Golden set

18 perguntas reais sobre `3eba639`, 10 categorias, `golden.json` (v1, revisão 1.1). Cada item: `id, question, category,
expected_files, expected_symbols, expected_regions, grep_friendly, semantic_only, notes`. **Gabarito conferido à mão**
(grep + leitura dos trechos); o Jev **não** foi usado para gerá-lo. Validação automática contra o working tree
(`golden.validate`: arquivos, regiões dentro do arquivo, símbolo presente).

| Categoria | Itens | | Categoria | Itens |
|---|---|---|---|---|
| EXACT_SYMBOL | G01, G10 | | STATE_MUTATION | G04, G15 |
| EXACT_TEXT | G16 | | TEST_LOCATION | G09, G18 |
| BEHAVIOR_SEARCH | G02, G05, G17 | | EVENT_ORIGIN | G07 |
| CROSS_MODULE_FLOW | G03, G06, G12 | | GUARDRAIL | G08, G14 |
| CONFIGURATION | G13 | | ARCHITECTURE | G11 |

Grupos: `grep_friendly` = 6 (G01, G07, G10, G15, G16, G18), `semantic_only` = 10, mistos = 2 (G04, G08). **Amostra pequena**:
18 itens em um repositório, só Python; qualquer diferença menor que ~15 pontos é ruído.

**Revisão 1.1 (antes de qualquer chamada Jev, registrada no `golden.json`):** G04 reclassificado (misto), G10 e G17
reescritos (ambiguidade teste×produção e aspas aninhadas), regra de escopo `app/tests` e termo de atribuição no extrator
mecânico de termos — todos defeitos observados **nas baselines**, não em resultado do Jev; o gabarito `expected_*` não
mudou. Também corrigido o tokenizador (palavras acentuadas eram partidas ao meio).
`THRESHOLD_CHANGED_AFTER_RESULTS = NO`.

## 24. Baselines

- `BASELINE_RIPGREP`: busca fixa, sem diferenciar maiúsculas, janela ±5 linhas, termos extraídos **mecanicamente** da pergunta
  (crases → atribuição → identificador → aspas → palavras); ranking = termos distintos, depois ocorrências. Usa `rg` real
  (o binário embutido do Claude Code, via `ARGV0=rg`; ou `rg` no PATH; ou `JEV_RG_BIN`) com equivalente em Python como
  reserva — testado igual.
- `BASELINE_CODE_SEARCH`: **definição nossa** (o enunciado não a define): BM25 local sobre *chunks* por função/método
  (`ast`, janelas de 60 linhas se > 80), tokenização com `snake_case/camelCase` e sem acento, escopo `app/tests` por
  pergunta. É o concorrente honesto e **gratuito** do Jev nas perguntas semânticas.
- `JEV` = BM25 (shortlist) → Jev. Mesmas travas de entrega para todos: até **8 trechos e 16 KiB**; o Jev usa k adaptativo (≥ 80 %
  de probabilidade acumulada, 2..8).

## 25. Métricas

`PRECISION_AT_1/3`, `RECALL_AT_3/5` (nível de arquivo, k distintos), `DELIVERED_RECALL`, `REGION_RECALL`, `LATENCY_MS`
(p50/p95), `FILES_EXAMINED`, `BYTES_READ`, `BYTES_RETURNED`, `CONTEXT_BYTES_RETURNED`, `READ_BYTES_AVOIDED`
(= `max(0, NAIVE_READ_BYTES − CONTEXT_BYTES_RETURNED)`, com `NAIVE_READ_BYTES` = tamanho dos arquivos esperados lidos por
inteiro), `FAILURES/TIMEOUTS/INVALID_RESPONSES`, `JEV_REQUESTS`, `JEV_COST`. **Tudo em bytes é `PROXY_METRIC`; nenhum número
aqui é contagem de tokens e nenhuma frase diz "economizou X tokens".**

### Resultado `MEASURED` (baselines, 2026-10-01, base `3eba639`, corpus 554 arquivos / 11 895 chunks / 9,77 MB, **sem Jev, sem rede**)

| Estratégia | Grupo | n | P@1 | P@3 | R@3 | R@5 | Entregue→recall | Regiões | p50 ms | `CONTEXT_BYTES` (med.) | `NAIVE_READ` (med.) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BM25 (`code_search`) | todos | 18 | 0,44 | 0,35 | 0,54 | 0,72 | 0,78 | 0,52 | 16 | 13 814 | 170 713 |
| ripgrep | todos | 18 | 0,39 | 0,33 | 0,56 | 0,70 | 0,44 | 0,34 | 101 | 7 386 | 170 713 |
| BM25 | `grep_friendly` | 6 | 0,67 | 0,28 | 0,44 | 0,67 | 0,83 | 0,71 | 11 | 12 714 | 174 382 |
| ripgrep | `grep_friendly` | 6 | 0,50 | 0,39 | **0,72** | **0,92** | 0,83 | 0,88 | 66 | 4 314 | 174 382 |
| BM25 | `semantic_only` | 10 | 0,40 | 0,43 | **0,65** | 0,75 | 0,75 | 0,43 | 17 | 13 426 | 169 214 |
| ripgrep | `semantic_only` | 10 | 0,40 | 0,33 | 0,53 | 0,62 | 0,28 | 0,09 | 243 | 9 408 | 169 214 |

Leitura honesta: ripgrep ganha em busca exata (R@3 0,72 vs 0,44) e **perde claramente em semântica na entrega** (só 28 % dos
arquivos esperados chegam a ser entregues); o BM25 é bom em semântica e barato (p50 16 ms). **Os dois teriam entregue ~8–14 KiB
contra ~170 KB de leitura inteira (≈ 92–96 % a menos em bytes)**: qualquer retriever com corte de entrega faz isso. Portanto
o ganho do Jev **não** é "reduzir bytes" (já se reduz de graça) e sim **acertar mais** nos itens semânticos com menos trechos.
`precision@k` fica baixo para todos porque há 1–3 arquivos esperados por item.

## 26. Piloto B (runtime offline replay)

**Desenhado e extraído; Jev não foi chamado.** `replay.py` lê o SQLite em `mode=ro`, só colunas da lista permitida
(`safe_select` recusa qualquer outra; teste garante que `args, rationale, error, title, goal, result, status_detail,
failure_screen, bindings, variables, observed_result, saidas, error_message` nunca são consultados; ids viram *hash*). Unidade =
**etapa com desfecho final**. Domínio do Jev = o que a regra determinística não resolveu.

## 27. Replay histórico

`MEASURED` em 2026-10-01 no banco do central (`mode=ro`, só agregados aqui; casos pseudonimizados em `data/jev-pilot/replay/`):

- **1 425** etapas com desfecho final (`succeeded` 1 355 · `failed` 52 · `uncertain` 9 · `waiting_user` 9); 250 execuções; 1 610
  tentativas; 2 479 linhas em `ai_calls` entre 2026-09-17 e 2026-10-01 (não são 500 nem 1 000: é o que existe).
- Efeito externo: 222 etapas; risco por catálogo: alto 37, médio 13, desconhecido 181, sem capability 1 194.
- Provedores nas etapas com IA: anthropic 427, openai 181, local 4.
- **Rótulos derivados do desfecho:** `DETERMINISTIC` 681 · `CHEAP_AI` 551 · `STRONG_AI` (barata falhou, forte resolveu na retentativa)
  27 · `STRONG_AI_UNPROVEN` (forte por política na 1ª tentativa, sem contrafactual) 96 · `HUMAN` 9 · `UNLABELED` 61.
- Pool avaliável (CHEAP/STRONG/HUMAN) = **587**; **positivos (STRONG+HUMAN) = 36**.
- Baselines triviais: **sempre `CHEAP_AI`: concordância 93,9 %, subescalada perigosa 6,1 %**; roteador por regras (espelho do
  `side_effect_tier`): concordância 79,4 %, subescalada 6,1 %, mantém 84,6 % dos baratos como baratos.

Campos do replay: `side_effect, capability, risk (do catálogo), driven_by, final_status, attempts, strategy, failure_kind,
app_id, n_decide_t0/t1, n_verify, models, providers, with_image, calls_ok, usd, ai_ms`. Nada de screenshot, prompt, texto
pessoal, mensagem, credencial.

**Limitações dos rótulos** (também no docstring de `replay.py`): derivados do desfecho, não oráculo; o modelo realmente usado
**não** é o rótulo; `driven_by` vaza `DETERMINISTIC`; só 36 positivos (intervalos largos); `STRONG_AI_UNPROVEN` não tem
contrafactual e fica fora da acurácia; as chamadas só se ligam à etapa (não à tentativa) antes da migração 045; histórico
cobre 14 dias e um parque pequeno (3 contas Instagram vivas). Estimativa de custo de um replay completo com Jev: 1 425
decisões × ~422 B = 600 845 B ≈ **US$ 0,006–0,008 (PROXY, bytes/4..bytes/3)**.

## 28. Shadow mode (futuro, não implementado)

Produção igual; o Jev só calcula `suggested_route + confidence`; **nada é executado com base nele**. Persistência conceitual
futura — **não criar migração agora**:

```
jev_shadow_decisions(
  timestamp, run_id, step_id, decision_type,        -- route | classification | (action, depois)
  choices, selected, confidence,                    -- só rótulos/categorias, nunca texto de tela
  actual_route, actual_outcome, actual_cost, latency_ms,
  provider, model_version, fallback, cost)          -- observabilidade (§31)
```

Regras: chamada **assíncrona e fora do caminho crítico** (timeout curto; falha só incrementa contador); sem payload sensível;
orçamento próprio e *kill switch*; relatório semanal compara `suggested_route` × `actual_route/outcome` e alimenta R1–R6.
**Action shadow** (`allowed_actions, selected_action, confidence`, sem executar) só depois de routing passar.

## 29. Critérios GO / NO-GO (pré-registrados)

Escritos em `experiments/jev/thresholds.json` **antes** de existir qualquer resultado real do Jev (registro de revisão de R2 no
próprio arquivo, também anterior). `THRESHOLD_CHANGED_AFTER_RESULTS = NO`; se mudar, registrar `YES` e o motivo.

**Piloto A (retrieval)** — Jev = BM25 → rerank → k adaptativo:

| # | Critério | Limiar | Justificativa |
|---|---|---|---|
| T1 | Busca exata não regride | R@3 `grep_friendly` ≥ ripgrep − 0,05 | ripgrep é o forte em exato |
| T2 | Ganho semântico | R@3 `semantic_only` ≥ BM25 + 0,10 **e** ≥ ripgrep + 0,10 | sem ganho sobre o BM25 gratuito o Jev só soma custo e envio externo |
| T3 | Contexto menor | mediana de `CONTEXT_BYTES` ≤ 50 % de `NAIVE_READ` **e** ≤ BM25 | é `PROXY`, em bytes |
| T4 | Latência | p50 ≤ 1 500 ms e p95 ≤ 4 000 ms | ordem do +1,25 s/leitura do plugin (`AUTHOR-REPORTED`) |
| T5 | Confiabilidade | falhas/timeouts/inválidas ≤ 5 % e ≥ 18 requisições; toda falha cai no BM25 | fallback não pode ser o caminho comum |
| T6 | Custo | ≤ US$ 1,00 na rodada e ≤ US$ 0,05/consulta | pega erro de payload; o custo esperado é ~US$ 0,01 |
| T7 | Privacidade | aprovação do dono + DPA lido + 0 achado duro | fora do benchmark |
| T8 | Perdas críticas | ≤ 1 item em que o BM25 tinha arquivo esperado no top-3 e o Jev o tirou | rerank não pode esconder contexto |

**Ressalva sobre T3:** a cláusula "≤ BM25" é satisfazível só pela política de entrega (apenas o Jev tem k adaptativo 2..8; as baselines entregam k=8). O k adaptativo foi adicionado **antes** de qualquer resultado real, depois de ver que o provedor FALSO entregava mais bytes que o BM25. Um "passa" em T3 é fraco; **T2 e T8 carregam o peso da evidência**.

`GO` = T1–T8; `PARTIAL_GO` = T1, T5, T7, T8 e (um entre T2/T3, ou T4/T6 por margem); `NO_GO` = T1/T5/T7/T8 falham ou T2 e T3 falham;
`INSUFFICIENT_EVIDENCE` = sem rodada real ou < 18 requisições. Implementado e testado em `report.evaluate`.

**Piloto B (runtime, só shadow)** — `GO_FOR_SHADOW_ONLY` se, com ≥ 300 casos rotulados e ≥ 30 positivos: **R1** subescalada
(STRONG/HUMAN → DETERMINISTIC/CHEAP) ≤ 2 %; **R2** concordância ≥ melhor baseline trivial + 3 pontos (hoje 93,9 % → ≥ 96,9 %);
**R3** acurácia ≥ 90 % quando `confidence ≥ 0,8`; **R4** p95 ≤ 800 ms; **R5** custo ≤ US$ 0,0005/decisão; **R6** ≥ 25 % dos
casos hoje atendidos por IA forte/barata poderiam subir de economia sem erro; e as respostas são fechadas, tipadas e
auditáveis. `NO_GO` se R1, R2 ou R3 falham, decisão livre, custo próximo ao do Sonnet ou ganho marginal. Nunca vira controle de
execução por este critério.

## 30. Custos

`ESTIMATED_*` computados por `benchmark.py --estimate-only` (sem rede) sobre o golden e os shortlists reais:

| Item | Valor |
|---|---|
| `ESTIMATED_JEV_CALLS` | **18** (uma por pergunta; cada uma com 1 `choice` + 1 `noul`) |
| `ESTIMATED_INPUT_BYTES` | **960 073** (maior payload 68 943 B; teto de projeto 100 000 B) |
| `ESTIMATED_INPUT_TOKENS` | **UNKNOWN** (tokenizador do Jev não documentado); `PROXY` bytes/4..bytes/3 = **240 018 – 320 024** |
| `ESTIMATED_COST` | **US$ 0,0101 – 0,0134** (preço OFICIAL × tokens PROXY). Free tier **UNKNOWN**; acesso por waitlist |
| Replay histórico completo (Piloto B, 1 425 decisões) | 600 845 B ≈ US$ 0,006 – 0,008 (PROXY) |

O custo nunca é o obstáculo. **Não chamar o Jev real mesmo com custo estimado desprezível**: o que falta é aprovação
(envio de código, §21), chave e acesso. A estimativa **não** inclui nenhuma API paga do projeto (nenhuma foi usada).

## 31. Riscos

| Risco | Severidade | Mitigação |
|---|---|---|
| Envio de código/estado a terceiro (EUA) com retenção padrão desconhecida | alta | aprovação do dono, DPA, redação+bloqueio, corpus limitado, ZDR enterprise/Cloudflare como opção |
| Dependência de fornecedor novo: sem SLA, "AS IS", limites dinâmicos, waitlist, `jev-latest` mutável | alta | só *shadow*/ferramenta de dev, sempre *fail-open*, versão fixada |
| Plugin oculta contexto (falso negativo) e pode não avisar o modelo | alta (plugin) | **não instalar**; pipeline próprio entrega trechos com caminho e linhas e cai no BM25 |
| Plugin sem filtro de caminho/segredo | alta (plugin) | idem; se um dia testado: hook off + sandbox + repositório descartável |
| Português fora do idioma principal | média | medir no golden e no replay antes de qualquer decisão |
| Confiança do Choice baixa por construção com muitas opções parecidas | média | não usar como porta no retrieval; usar prob. acumulada |
| Contaminação do benchmark (ajustar golden/limiares depois) | média | congelado; `thresholds.json` registra; mudanças pós-resultado exigem `YES` |
| Amostra pequena (18 perguntas, 36 positivos) | média | declarar; decidir com margem; ampliar só com nova versão do golden |
| Estatística do replay: rótulos derivados | média | uso exploratório; shadow é quem mede de verdade |

## 32. Reversibilidade

Tudo se remove com `git rm -r experiments/jev docs/research/jev-pilot.md` (+ a linha no índice `docs/README.md`). Nenhum
import do runtime depende do experimento; nenhuma configuração de produção o conhece; nenhuma migração, dependência nova, hook,
plugin ou binário. `data/jev-pilot/` é ignorado pelo Git (`.gitignore: data/`). Sem a branch, nada muda em `main`.

## 33. Limitações da pesquisa

- A documentação oficial foi lida em texto bruto nas páginas listadas em §5 e, na 2ª rodada, MCA, DPA, política, Termos, AUP e
  Trust Center (§35); **página de `evals`, blog de preço, SDKs e `jev-android` vieram de resumos de ferramenta/`gh api`** e podem ter imprecisão.
- O plugin foi analisado só por **leitura**; nada foi executado: fail-open, espera offline, destino do `additionalContext`,
  Windows, `exit 2` em *panic* são **inferências**.
- Não há medição independente de latência/acurácia do Jev; os números do fornecedor e do autor do plugin são
  `AUTHOR-REPORTED`.
- Golden de 18 itens, um repositório, Python; baselines mecânicas (extração de termos fixa) podem subestimar ripgrep usado por
  uma pessoa. `jev-android` e outros projetos listados: metadados, sem execução.
- Mudança de **datas**: tudo de 2026-09/10, produtos com dias de vida; qualquer item pode mudar em semanas.
- `docs-check` já tinha 1 erro e 1 aviso **fora do escopo** (link de `.claude/handoff-current.md`, local e ausente no
  worktree; vocabulário do plano-100): não corrigidos.

## 34. Próxima decisão do dono

> **Atualizado na 2ª rodada:** o item 2 abaixo está **bloqueado** (`BLOCKED_PRIVACY`, §35). A decisão atual é a de §36 (smoke
> sintético, 6 chamadas) e, separadamente, como destravar o código real (§35, condições A/B/C).

1. **Plugin `BorisLeMeec/jev`:** recomendo **não instalar** (nem global, usuário, projeto ou hook). Se quiser testá-lo mesmo
   assim: repositório descartável, hook desligado por padrão, sem arquivos sensíveis, com o plano de §Plano abaixo.
2. **Autorizar (ou não) UMA rodada real do Piloto A** (≈ US$ 0,01, 18 requisições, `jev-1.13.0`), o que exige: (a) aceitar que
   trechos de `backend/app` e `backend/tests` (sem achados de segredo; fixtures bloqueadas) saiam para a TypeSafe nos EUA,
   (b) ler o DPA e a política de retenção, (c) uma chave de uma conta com acesso, entregue **por variável de ambiente
   `TYPESAFE_API_KEY`** (nunca no chat nem no repositório) — nome oficial; `TYPE_SAFE_AI_KEY` não é o oficial. Comando futuro:
   `python experiments/jev/benchmark.py --provider jev --confirm-external-send --max-calls 40`.
3. **Se o piloto A não passar, parar.** Se passar, só então considerar o Piloto B em shadow (decisão separada, com §28).
4. **Não abrir** action selection antes de routing/classificação em shadow e de decidir sobre enviar árvore de UI de contas
   reais a um terceiro.

### Plano futuro de teste do plugin (não executar agora)

A. Sessão Claude limpa, repositório descartável (clone de `3eba639`, sem `.env`/`config`/dados). B. Conjunto congelado das 18
perguntas do golden. C. Baseline **sem** plugin. D. Sessão equivalente **com** plugin compilado a partir de código lido, hook
com `JEV_HOOK_DEBUG=1`. E. Medir `/usage` e `/context` (tokens reais) e tempo. F. Comparar respostas contra o gabarito (não
contra a impressão do agente). G. Remover o plugin e `~/.jev`. Pré-requisitos: compilar o binário (Go ≥ 1.26.1), confirmar
Windows, confirmar o destino do `additionalContext`, e T7 aprovado.

---
**Fontes principais:** [docs.typesafe.ai/api](https://docs.typesafe.ai/api) · [/models](https://docs.typesafe.ai/models) ·
[/confidence](https://docs.typesafe.ai/confidence) · [/model-jaggedness/jev-1.13](https://docs.typesafe.ai/model-jaggedness/jev-1.13) ·
[/legal](https://docs.typesafe.ai/legal) · [/introduction/coding-agents](https://docs.typesafe.ai/introduction/coding-agents) ·
[/cookbooks/semantic_find](https://docs.typesafe.ai/cookbooks/semantic_find) · [typesafe.ai](https://typesafe.ai/) ·
[github.com/typesafe-ai](https://github.com/typesafe-ai) · [BorisLeMeec/jev](https://github.com/BorisLeMeec/jev) (COMMUNITY) ·
[dougsong/jev-android](https://github.com/dougsong/jev-android) (COMMUNITY). Código do projeto: `backend/app/planning/routing.py`,
`provider.py`, `taskqueue/executor.py`, `proofs.py`, `social/policy.py`, `state.py` (commit `3eba639`).


## 35. Privacy due diligence (2026-10-01, segunda rodada)

> **Status do Piloto A sobre o código real: `BLOCKED_PRIVACY`.** O repositório é privado, a retenção padrão é `UNKNOWN`, ZDR só
> para enterprise. Trava no código: `benchmark.py::PRIVATE_CODE_SEND_APPROVED = False` (com chave e `--confirm-external-send`
> o comando ainda aborta). Só vira `True` num commit próprio citando a autorização **explícita** do dono.

**Fontes (todas lidas em texto bruto em 2026-10-01; cópias em `data/jev-pilot/legal/`, fora do Git):**
`typesafe.ai/legal/mca` (Master Customer Agreement, atualizado **23/09/2026**), `/legal/data-processing` (DPA, **24/04/2026**),
`/legal/privacy-policy` (**19/11/2025**), `/legal/terms`, `/legal/acceptable-use`, `trust.typesafe.ai` (página renderizada no
navegador embutido), `docs.typesafe.ai` (111 páginas varridas por *retention/ZDR/logging/train*; só `/legal` e `/models` tocam
no assunto) e a página do modelo no catálogo da Cloudflare. **Correção ao §8 da rodada anterior:** os "Termos que permitem
descontinuar sem aviso" são os **Termos de Uso do *site*** ("modify or discontinue the *Site*"), não os da API.
Para a API vale o **MCA** (abaixo).

| Item | Achado (fonte) | Estado |
|---|---|---|
| **Retenção padrão da API** | Nenhum prazo em lugar nenhum. MCA §4.1: o direito de processar `Input` vale "**durante o Term**" para prestar o serviço; MCA §10.3: TypeSafe "**não tem obrigação de armazenar ou reter** Customer Data e pode apagá-lo a qualquer momento, a seu critério" (backups podem reter informação confidencial). Política: retém dados "pelo tempo **razoavelmente necessário** para prestar os Serviços, **ou em apoio a seus fins comerciais**". DPA Anexo I.8: "pelo tempo necessário considerando a finalidade". Trust Center (AWS): "informações de clientes para requisições ao vivo são **armazenadas** e processadas em bancos de dados, caches e nós de computação na AWS". | **`STANDARD_API_RETENTION = UNKNOWN`** (não é zero: há armazenamento declarado, sem prazo) |
| **Prazo de retenção** | Não numérico em nenhum documento. | UNKNOWN |
| **Finalidade da retenção** | MCA §4.1(c): "**em perpetuidade**", qualquer Customer Data para (i) derivar **Telemetry**, (ii) **monitorar fraude e abuso**, (iii) cumprir a lei. Política: melhorar/depurar o serviço, analisar uso, "desenvolver novos produtos", gerar dados anonimizados/agregados, prevenir fraude. | declarado |
| **Logs** | MCA §4.3: *Telemetry* = "logs técnicos, *hashes*, estatísticas, **classificações**, métricas e **aprendizados** (*learnings*) relacionados ao seu uso"; TypeSafe pode processá-la "**sem restrição**, inclusive para melhorar o serviço **ou outros produtos**". Retenção/escopo dos logs e o que são "learnings": não definidos. | UNKNOWN (e cláusula ampla) |
| **Abuse monitoring** | Existe e é perpétuo (MCA §4.1(c)(ii); AUP: "pode monitorar a conformidade"). Se envolve revisão humana de conteúdo: não dito. | parcial |
| **Subprocessadores** | Trust Center, **6**, todos **EUA**: AWS (armazena requisições ao vivo), Modal / Nebius / CoreWeave (infra de IA; "prompts processados, **não armazenados**"), Slack e Google Workspace (suporte; dados de cliente "se enviados por ele"). DPA §3: autorização geral, aviso prévio de novos, **15 dias** para objetar. | conhecido |
| **Exclusão (*deletion*)** | Sem mecanismo de apagamento de `Input` sob pedido para dado não pessoal. MCA §10.3 só diz que podem apagar quando quiserem. DPA §4.1 só ajuda em pedidos de titulares (dado **pessoal**). Controle de conformidade do Trust Center: "*Customer data deleted upon leaving*" (nome do controle; relatório não lido). | UNKNOWN |
| **DPA** | Existe, incorporado ao MCA (§4.4). **Só cobre dado pessoal** ("Customer Personal Data"); código-fonte em geral não é. Operador/controlador; instruções documentadas; sem venda/compartilhamento; **aviso de incidente em 72 h**; auditoria 1×/12 meses, **às custas do cliente**; SCCs módulos 2 e 3 (lei da Irlanda) e *UK Addendum*; "dados sensíveis: N/A". | existe, escopo estreito |
| **ZDR** | docs `/legal`: "oferecemos retenção zero para clientes **enterprise**; fale com sales@typesafe.ai". Condições, preço, prazo de ativação, abrangência (inclui *abuse monitoring*/Telemetry?) e disponibilidade para esta conta: **não documentados**. A Cloudflare lista o modelo (terceiro) com "Zero data retention: **Yes**" (`INDEPENDENT`; o que cobre — só a Cloudflare ou também a TypeSafe — **não está definido** na página; contexto 32 k). | só enterprise; condições UNKNOWN |
| **Treino / melhoria** | Política: "não treinamos nem fazemos *fine-tuning* com seus *prompts* ou outro *Input*" e "não divulgamos *Input* a terceiros além de prestadores". MCA §4.1: sem incluir Customer Data em *dataset* de **treino de pesos** "sem consentimento prévio". **Lacuna:** Telemetry (inclui "aprendizados" derivados do uso) é livre para "melhorar o serviço e **outros produtos**" — as duas garantias não fecham essa porta. | parcial; lacuna na Telemetry |
| **Transferência internacional** | Serviços hospedados **nos EUA**; quem usa de fora "transfere dados para os EUA" (Política). DPA §6: SCCs/UK Addendum. Sem região UE/BR. Lei da Califórnia e arbitragem (MCA §15–16). Não há tratamento específico para a **LGPD** (Brasil). | EUA apenas |
| **Termos da API normal** | MCA: aceito "ao usar os Serviços". §5: o **cliente garante** ter todos os direitos e consentimentos para enviar o `Input`. §9.3: "**AS IS**", sem garantia de operação ininterrupta nem de "manter Customer Data sem perda". §6: suspensão imediata em casos listados. §7: integrações de terceiros (gateways) ficam sob o contrato do terceiro. Confidencialidade (§14) cobre informação "razoavelmente entendida como confidencial", mas §14.2(a) a ressalva "**como permitido neste Agreement, incluindo a Seção 4.1**". | aplicável |
| **Certificações** | Trust Center lista **SOC 2 Type II – 2026**. Relatório **não revisado** (requer "Request access"). | `OFFICIAL` (alegação), não verificada |

**Leitura honesta.** Existe estrutura contratual real (DPA, SCCs, SOC 2 listado, sem treino de pesos), mas para o *nosso* caso
— código-fonte privado, não dado pessoal — ela protege **pouco**: o DPA não se aplica, a retenção é indefinida, os direitos
em perpetuidade (Telemetry/abuso) existem, não há apagamento sob pedido e o ZDR é enterprise. Nada disso prova que a TypeSafe
fará algo indevido; prova que **o que está escrito não permite afirmar que o código sairia dos servidores deles**. Não fiz
inferência: onde o texto cala, registrei `UNKNOWN`.

**`UNRESOLVED_PRIVACY_ITEMS`:** (1) prazo de retenção do `Input` e dos logs; (2) por quanto tempo a AWS guarda as "requisições ao vivo";
(3) o que são os "learnings" da Telemetry e se podem conter trechos de `Input`; (4) se o *abuse monitoring* tem revisão humana
e quanto retém; (5) condições, custo, prazo e abrangência do ZDR, e se está disponível para esta conta; (6) mecanismo de
apagamento de `Input` sob pedido; (7) relatório SOC 2 Type II; (8) o que o "Zero data retention: Yes" da Cloudflare cobre;
(9) opção de região fora dos EUA.

**Pergunta pronta para a TypeSafe (rascunho; eu **não** enviei nada — comunicar-se com o fornecedor é decisão do dono):**
> Para o uso por API (`/v1/systemone`) sem contrato enterprise: (1) qual é o prazo padrão de retenção do Input e de logs
> técnicos? (2) por quanto tempo a AWS guarda "live requests"? (3) o que a "Telemetry/learnings" (MCA §4.3) pode conter e pode
> incluir trechos do Input? (4) o *abuse monitoring* envolve revisão humana e qual a retenção? (5) como ativar ZDR, a que preço,
> e ele cobre Telemetry e abuse monitoring? (6) há apagamento de Input sob pedido? (7) podem compartilhar o SOC 2 Type II?
> Contato oficial: sales@typesafe.ai, privacy@typesafe.ai.

**Como destravar o Piloto A (qualquer um):** (A) a TypeSafe esclarecer a retenção padrão de forma que o dono aceite; (B) ZDR
disponível para esta conta (direto ou por Cloudflare, **confirmando por escrito o que cada um cobre**); (C) o dono autorizar
explicitamente o envio apesar do `UNKNOWN`. **Alternativa que dispensa o código privado:** o smoke sintético (§36) valida o
protocolo; e um *benchmark de recuperação* poderia usar um repositório **público** e permissivo como corpus (decisão separada).

## 36. Smoke test sintético (preparado, **não executado**)

Objetivo: validar **autenticação, protocolo, tipos noul/choice/score, confiança, latência, timeout, tratamento de erro e custo
real** sem enviar nada do repositório. Código: `experiments/jev/smoke.py`, corpus fictício `experiments/jev/synthetic_corpus/`
(domínio inventado "Harbor": equipes em espera, relançamento com atraso, assinatura humana, teto de gasto, livro de eventos;
escrito em inglês porque é o idioma principal do Jev). **Nada do corpus vem do projeto**: `tests/test_smoke.py` falha se houver
nome de função/classe igual ao do repositório privado ou sequência informativa de 12 tokens idêntica, e se houver qualquer achado
da redação.

| # | Chamada (1 requisição de rede) | Valida | Esperado |
|---|---|---|---|
| 1 | `noul_auth_latency` | chave/Bearer, formato `noul`, latência de referência | 200, `noul ∈ [0,1]` |
| 2 | `choice_closed_set` | `choice` com conjunto fechado `A01..A05`, probabilidades somam 1, `confidence` | 200, escolha ∈ conjunto |
| 3 | `score_three_levels` | `score` de 3 níveis, `legend`, `probabilities`, `confidence` | 200 |
| 4 | `rerank_shape` | o formato do Piloto A: `choice` sobre 8 ids + `noul` na **mesma** requisição | 200, 2 respostas |
| 5 | `error_422` | corpo e status de erro de validação (score de 1 nível); ausência de *retry* em 422 | 422 |
| 6 | `timeout_probe` | caminho de timeout do cliente (50 ms) sobre a requisição mais simples | timeout (ou 200, se for mais rápido) |

Travas: padrão = **só estima, sem rede**; a rodada real exige `--run --confirm-synthetic-only`, `TYPESAFE_API_KEY` e
`SMOKE_RUN_AUTHORIZED = True` (hoje `False`); `max_calls = 6` conta **cada tentativa de rede**, e a rodada usa `max_retries = 0`
(um 429/529 não é repetido, para não gastar as 6). **Não validado por chamada real:** *retry/backoff* em 429/529 (só simulado
com transporte falso), *rate limits*, carga, português.

**Estimativa (offline, `python experiments/jev/smoke.py`):**

| | |
|---|---|
| `ESTIMATED_CALLS` | **6** |
| `ESTIMATED_INPUT_BYTES` | **4 162** (213 + 500 + 265 + 2 826 + 145 + 213) |
| `ESTIMATED_TOKENS` | **UNKNOWN** (tokenizador do Jev não documentado); `PROXY` bytes/4..bytes/3 = **1 040 – 1 387** |
| `ESTIMATED_COST` | **US$ 0,000044 – 0,000058** (preço oficial × tokens PROXY; saída grátis). Free tier UNKNOWN; se não houver, o custo é o saldo mínimo da conta, não o consumo. |

**Para autorizar:** responder explicitamente "autorizo as 6 chamadas do smoke sintético"; então um commit muda
`SMOKE_RUN_AUTHORIZED` e a chave entra por variável de ambiente (`TYPESAFE_API_KEY`), nunca no chat. Comando:
`python experiments/jev/smoke.py --run --confirm-synthetic-only --max-calls 6`. Resultado em `data/jev-pilot/smoke/` (ignorado).

### 36.1 Autorização recebida e estado da rodada (2026-10-01, terceira rodada)

- O dono autorizou as 6 chamadas **só** do corpus `experiments/jev/synthetic_corpus/`. `SMOKE_RUN_AUTHORIZED = True`;
  `PRIVATE_CODE_SEND_APPROVED` segue `False` (BLOCKED_PRIVACY); `STANDARD_API_RETENTION` segue UNKNOWN.
- Antes de qualquer envio o `--run` roda `isolation.check` (segredos, literais do projeto, nomes e sequências idênticos ao
  repositório privado, payloads incluídos) e aborta se não for PASS. Nos testes: PASS no corpus real e FAIL em violação injetada.
- Registro por chamada: CALL_ID, QUESTION_TYPE, HTTP_STATUS, NETWORK_ATTEMPT, LATENCY_MS, INPUT_BYTES, RESPONSE_BYTES,
  MODEL_RETURNED, PARSE_OK, CONFIDENCE, ERROR_CLASS, COST_ESTIMATE; p50/p95/min/max; `ACTUAL_INPUT_TOKENS` só de `usage`, senão UNKNOWN.
- **Resultado: NÃO EXECUTADO.** `TYPESAFE_API_KEY_PRESENT = NO` no processo desta sessão (bash e PowerShell, escopos Process/User/Machine).
  O comando autorizado abortou antes de qualquer rede ("variável ausente. Nada foi enviado."): `NETWORK_ATTEMPTS = 0`.
  Provas até aqui: `simulated` (`experiments/jev/tests/test_smoke.py`, 79 testes); chamadas reais: `not_run`.
- Para executar: definir a chave **localmente** (variável de ambiente de Usuário e reiniciar a sessão, ou no PowerShell do dono
  `$env:TYPESAFE_API_KEY = ...` e rodar o comando acima) e compartilhar só o JSON de `data/jev-pilot/smoke/`.

## 37. Benchmark PÚBLICO de recuperação (Scrapy 2.19.0) — preparado, não executado (2026-10-01, terceira rodada)

**Estado:** `REAL_JEV_CALLS_RUN = NO`. Preparado enquanto a chave não existe; nada é enviado a ninguém. Pacote em
`experiments/jev/public_bench/` (README próprio).

### 37.1 Escolha do repositório

| Item | Valor |
|---|---|
| Repositório | `scrapy/scrapy` (framework de crawling em Python) |
| Licença | **BSD-3-Clause** (texto do `LICENSE` conferido no checkout; permite redistribuição e uso) |
| Versão fixada | tag `2.19.0`, commit `8026deeaac371a5d9a3edbe4886d58f61139d464` (2026-09-10, "Bump version: 2.18.0 → 2.19.0") |
| Tamanho do corpus | pacote `scrapy/`: 179 arquivos `.py`, ~32 mil linhas, 1,17 MB, 2.178 trechos |
| Por quê | arquitetura real e em camadas (engine, scheduler, downloader, middlewares, extensões, pipelines) com muitos módulos que se parecem entre si; mantido há anos, release estável, licença permissiva, Python como o produto; metadados lidos via `gh api` (sem clonar) antes de escolher. Alternativas descartadas: Flask/httpx (pequenos demais para busca semântica); Celery (metadado de licença `NOASSERTION` no GitHub); Django/SQLAlchemy (grandes demais para conferir o gabarito à mão). |
| Limitação | o corpus fica em **inglês** e o produto privado está em português; `tests/` (302 arquivos) fica fora de propósito (repetem o vocabulário de produção e criariam ambiguidade de gabarito, o problema G10 do piloto privado). |

O clone (`git clone --depth 1 --branch 2.19.0`, rede só para um repositório público) vai para `data/jev-pilot/public/` (ignorado
pelo Git); o código do Scrapy **nunca é executado**, só lido como texto. `benchmark.verify_public_checkout` recusa qualquer
diretório que não seja repositório Git próprio, com a origem e o SHA fixados e árvore rastreada limpa.

### 37.2 Golden: 20 perguntas, 8 EXACT + 12 SEMANTIC

Gabarito conferido **à mão** contra o commit fixado (grep + leitura das regiões); o Jev não foi usado para gerar nem rever.
Cada item tem arquivos, símbolos e regiões `[arquivo, ini, fim]`. As regras de grupo são mecânicas e testadas:
EXACT = a pergunta traz entre crases um literal que aparece verbatim numa região esperada; SEMANTIC = nenhum token da pergunta
(fora stopwords e palavras onipresentes do domínio) é igual a um token dos nomes esperados. Exemplos: P01 "Which code sends the
`request_dropped` signal?" (EXACT, `core/engine.py`); P11 "Where is the pause between consecutive requests to the same server
enforced?" (SEMANTIC, `core/downloader/__init__.py`, sem as palavras `delay`/`download`/`slot`); P13 "How does the framework make
sure the same page is not fetched twice?" (SEMANTIC, 2 arquivos: `dupefilters.py` + `core/scheduler.py`). Dezenove dos vinte
itens têm um único arquivo esperado; P13 é o único multi-arquivo.

### 37.3 Baselines locais (medidas offline, sem Jev)

| Grupo | ripgrep RECALL@3 | BM25 (code_search) RECALL@3 |
|---|---|---|
| EXACT (8) | 1,000 | 1,000 |
| SEMANTIC (12) | 0,083 | 0,000 |
| Todas (20) | 0,450 | 0,400 |

Mesmas métricas, termos mecânicos (`query_terms`), janela ±5 linhas e trava de entrega (8 trechos / 16 KiB) do piloto privado.

### 37.4 Achado que mudou o desenho (antes de qualquer chamada)

O shortlist BM25 de 30 trechos contém o arquivo esperado em **100 % das EXACT e 0 % das SEMANTIC** (com 200 trechos: 71 % dos
arquivos, mas só 5/12 itens com alguma região esperada). O pipeline do piloto privado (BM25 → Jev) **não consegue** ganhar nas
perguntas semânticas por construção: o Jev só reordena o que o BM25 trouxe. Isto vale também como alerta para o piloto privado:
a vantagem do rerank é limitada pelo recall do primeiro estágio. Registrado como medição das baselines, não como ajuste de
gabarito (`expected_*` não mudou). Resposta pré-registrada em `thresholds.json`: duas variantes, **veredito pela `jev_map`**.

- `jev_rerank` (controle): BM25 top-30 → 1 requisição.
- `jev_map` (principal): mapa do repositório (172 arquivos, 1 linha cada com a 1ª frase da docstring e os nomes definidos, 35 KB;
  Choice aceita até 255 opções) → o Jev escolhe arquivos (requisição A) → o Jev escolhe trechos dos 2 primeiros arquivos
  (requisição B, ≤ 60 trechos e ≤ 80.000 bytes por causa do limite de 32 mil tokens de state + maior pergunta) → entrega
  adaptativa (menor k de 2 a 8 com probabilidade acumulada ≥ 0,80). Falhas caem no BM25 ou no ranking da etapa A e contam.

### 37.5 Limiares pré-registrados

Mesmos T1–T8 do piloto privado (T1, T2, T3, T4, T6 idênticos, comparado por teste), com ajustes de escala escritos antes de
qualquer resultado: `min_requests` 20; T8 com teto de 2 itens em 20; T7 troca "aprovação do dono para código privado" por
"checkout fixado e íntegro". Veredito mecânico (`GO`, `PARTIAL_GO`, `NO_GO`, `INSUFFICIENT_EVIDENCE`) calculado por
`report.evaluate` para a variante principal; a outra é reportada como ablação. `THRESHOLD_CHANGED_AFTER_RESULTS = NO`.

### 37.6 Estimativa (sem rede; tokens são PROXY, o tokenizador do Jev é desconhecido)

| | Requisições | Bytes de entrada | Tokens PROXY | Custo (US$ 0,042/Mtok, saída grátis) |
|---|---|---|---|---|
| `jev_rerank` (20 perguntas) | 20 | 547.700 (maior payload 38.959) | 136.925–182.566 | 0,0058–0,0077 |
| `jev_map`, etapa A | 20 | 875.772 (maior 43.837) | — | — |
| `jev_map`, etapa B | 20 | 335.655–1.640.000 (depende do que o Jev escolher) | — | — |
| **Total** | **60** | **1.759.127–3.063.472** | **439.781–1.021.157** | **0,0185–0,0429** |

`max_retries = 0` e teto `--max-calls 60`; um 429/529 não é repetido. O custo real só se conhece pelo `usage` da API
(`ACTUAL_INPUT_TOKENS` = UNKNOWN até lá). Free tier da conta: UNKNOWN.

### 37.7 Trava e o que NÃO foi feito

`PUBLIC_BENCHMARK_AUTHORIZED = False`; `--provider jev --corpus-root …` aborta com `BLOCKED_AUTHORIZATION` mesmo com chave. Para
rodar é preciso: (1) o smoke sintético ter passado, (2) a chave local, (3) autorização explícita do dono para as 60 requisições.
`PRIVATE_CODE_SEND_APPROVED = False` e `SMOKE_RUN_AUTHORIZED = True` não foram tocados. Prova: baselines e estimativa `measured`
offline; harness com provedor falso `simulated` (`tests/test_public_bench.py`, 17 testes; suíte do experimento 96); Jev real `not_run`.

### 36.2 Resultado REAL do smoke sintético (2026-10-01, 18:48Z, branch `claude/jev-pilot`, commit base `dedc9a8`)

`python experiments/jev/smoke.py --run --confirm-synthetic-only --max-calls 6`, com a chave só no ambiente do processo filho.
Corpus: `synthetic_corpus/` (nenhum byte do repositório). `SYNTHETIC_ISOLATION_CHECK = PASS`. Detalhe em `data/jev-pilot/smoke/20261001T184815Z-real.json` (ignorado pelo Git). Prova: **real** (máquina central, esta data, comando acima).

| # | Caso | HTTP | Latência (ms) | Entrada (B) | Resposta (B) | Tokens de entrada (usage) | Resultado |
|---|---|---|---|---|---|---|---|
| 1 | Noul (autenticação) | 200 | 331,8 | 213 | 114 | 297 | `noul` em [0,1], válido |
| 2 | Choice A01–A05 | 200 | 318,2 | 500 | 208 | 416 | escolha dentro do conjunto, probabilidades somam 1, confiança 0,83 |
| 3 | Score, 3 níveis | 200 | 285,2 | 265 | 227 | 314 | score em [0,2], `legend` completa, confiança 0,57 |
| 4 | Choice + Noul (formato do Piloto A) | 200 | 357,1 | 2.826 | 484 | 1.083 | 2 respostas em 1 requisição, confiança do Choice 1,00 |
| 5 | Score com 1 nível (esperado 422) | **200** | 310,3 | 145 | 192 | 285 | **a API aceitou**: devolveu score com confiança 1,0 |
| 6 | Noul com timeout de cliente 50 ms | — | 91,3 | 213 | — | — | `The read operation timed out`; sem retry |

- **Autenticação, modelo e versão:** `Bearer` aceito; `model` devolvido = `jev-1.13.0` (a versão fixada), em todas as respostas 200.
- **Tentativas de rede:** 6 (teto 6, `max_retries = 0`). Latência de todas as 6: min 91,3 / p50 314,2 / p95 350,8 / max 357,1 ms; só as 5 bem-sucedidas: 285,2 / 318,2 / 352,0 / 357,1 ms.
- **Uso real:** `usage.input_tokens` somou **2.395** nas 5 respostas 200 (3.949 B de entrada). Custo a US$ 0,042/Mtok = **US$ 0,000101**. Erro e timeout não trazem `usage`: a cobrança da chamada 6 é UNKNOWN. Cada chamada tem uma sobrecarga fixa de ~260 tokens (a menor, 145 B, já gastou 285), então bytes/4 subestima as requisições pequenas; nas grandes (chamada 4) a razão fica perto de 2,6 B/token.
- **Achado contra a expectativa:** a documentação diz 2–10 níveis para `score`, mas 1 nível **não** deu 422. O erro de validação 422 e o formato do seu corpo **não foram observados** (só simulados). O plano não foi alterado depois da resposta; repetir exigiria nova autorização.
- **Limitação do registro:** o harness gravou tipo, confiança e validação de cada resposta, mas **não a escolha (A0x) nem o valor do score**; portanto a correção do Jev não foi avaliada. 429/529 e backoff não foram exercitados.
- **Veredito:** `PROTOCOL_VALIDATED = PARTIAL` (autenticação, os três tipos, 2 perguntas por requisição, `usage`, timeout de cliente); `MODEL_BEHAVIOR_VALIDATED = NO`; `RETRIEVAL_VALUE_VALIDATED = NO`. Não é conclusão de adoção.

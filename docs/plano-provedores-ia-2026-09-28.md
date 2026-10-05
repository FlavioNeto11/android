# Plano: IA com melhor custo-benefício (28/09/2026)

> **Base.** A [pesquisa de provedores](pesquisa-provedores-ia-2026-09-28.md) (preço de lista, verificação
> adversarial) e uma segunda checagem, também de 28/09, dos links de criação de conta e dos pontos de integração.
> Os números de gasto vêm do ambiente central (`GET /api/usage?days=7`, `real`).
>
> **Estado em 28/09:**
> - Registrado como **Fase 17 do plano-100** (17.1–17.9), com decisão no **ADR-049**. Os itens 17.1–17.4 (código) estão
>   feitos, com prova `simulated`.
> - **Decisões do dono:**
>   - jurisdição delegada ("por onde for melhor"), e ficou EUA;
>   - persona sem declaração de virtual por ora, revisada depois;
>   - chaves já no `.env`, sem projeto dedicado, com saldo de US$ 8,66 na OpenAI, R$ 30 no Google e US$ 10 na
>     Anthropic;
>   - autorizados o rejulgamento offline, o teste de rosto, a bateria com reinícios e a implantação se passar em todos
>     os critérios.

> **Resultado da medição em 28/09 ([relatório §18](relatorio-validacao.md)).**
> - **Imagem adotada:** `gpt-image-2` médio, ~US$ 0,052 por imagem, com o rosto mantido nas variações.
> - **Ator e verificador não adotados.** O `gpt-6-luna` fez 12/14 contra 13/14 da base, a 51% do custo por caso
>   correto; errou um envio (verificador) e deu um bloqueio falso de conta (ator).
> - O Flash-Lite não passou no portão do verificador.
> - **Próximo:** a cascata (17.10) e, depois, medir de novo. O plano no Opus 5.5 é o maior custo que sobra.

## Resumo

**A espinha: um fornecedor novo primeiro, a OpenAI.** Uma conta e uma chave cobrem quatro coisas:

- o ator e o verificador (`gpt-6-luna`, cerca de 13 vezes mais barato que o Sonnet 5 por chamada);
- a imagem da persona (`gpt-image-2`; o adaptador já existe);
- o Batch e o Flex para trabalho offline;
- uma política de dados só, que não treina com a API por padrão.

O Gemini 3.1 Flash-Lite entra como **segundo braço da mesma bateria**, só com faturamento ligado. Todo o resto fica
parado, com gatilho escrito.

| | Hoje | Depois do plano (estimativa) |
|---|---|---|
| Gasto de IA por mês, no ritmo dos últimos 7 dias | cerca de US$ 42 | cerca de US$ 14 a 18 (−56% a −67%) |
| US$ por objetivo | cerca de 0,16 | cerca de 0,06 a 0,07 |
| Imagem da persona | simulada | real, com o mesmo rosto nas variações; cerca de US$ 0,5 a 2 no total para 14 personas |
| Gasto para provar, antes de adotar | — | cerca de US$ 3 a 4 de API, com autorização |
| Crédito pré-pago para começar | — | US$ 10 na OpenAI + US$ 5 no Google |

**O maior risco, e o motivo de a bateria existir:** o `gpt-6-luna` só chama ferramenta sem raciocínio
(`reasoning_effort: none`). É exatamente o formato em que o qwen 4B "vagava" e escalava ao Opus. O preço por chamada
não decide; decide o **US$ por objetivo comprovado, com os escalonamentos incluídos**.

## Etapa 0 — O que você cria (só você; a chave nunca passa pelo chat)

Onde a URL só apareceu no índice de busca, sem uma doc oficial que a cite, vai o caminho no painel.

### OpenAI (obrigatório, primeiro)

| # | O que fazer | Onde |
|---|---|---|
| 1 | Entrar ou criar a conta da plataforma de API (é outra conta que não a da Anthropic) | https://platform.openai.com/ |
| 2 | Criar um **projeto dedicado** (ex.: `central-aparelhos`), que separa chave, uso e limite. Só o dono da organização cria projeto. | painel: Settings → Organization → Projects |
| 3 | Comprar **crédito pré-pago de US$ 10** (mínimo US$ 5) e **desligar a recarga automática**, que vem ligada | painel: Settings → Organization → Billing → "Buy credits" |
| 4 | Pôr um **limite de gasto no projeto** (sugestão: US$ 10 por mês) e marcar **"Enforce a hard limit"**; sem essa marca o limite só avisa. A trava tem um pequeno atraso e pode passar um pouco do valor. | organização: https://platform.openai.com/settings/organization/limits · projeto: Project settings → Limits · [guia](https://developers.openai.com/api/docs/guides/spend-limits) |
| 5 | **Verificar a organização.** A doc diz que "pode ser necessária" para os modelos de imagem; faça antes, para a primeira imagem não travar. | https://platform.openai.com/settings/organization/general → Verify Organization · [guia de imagem](https://developers.openai.com/api/docs/guides/image-generation) |
| 6 | Criar a **chave do projeto** | https://platform.openai.com/settings/organization/api-keys |
| 7 | Ler a política de dados (API sem treino por padrão, registro de abuso por até 30 dias, retenção zero só com aprovação) e a de uso | [seus dados](https://developers.openai.com/api/docs/guides/your-data) · [Usage Policies](https://openai.com/policies/usage-policies/) |
| 8 | Pôr a chave no `.env` do central: `OPENAI_API_KEY=<a chave>`. A imagem já lê esse nome. O provedor de conversa só vai achá-lo depois do item 17.1: hoje ele procura a chave no ambiente do processo, não no `.env`. | `C:\git\android\.env` (a IA não lê este arquivo) |

### Google Gemini (segundo braço da bateria)

**O faturamento tem de estar ligado antes da primeira chamada.** No plano gratuito, os termos permitem usar o
conteúdo "to provide, improve, and develop Google products", e "human reviewers may read" o material. Com capturas
do Instagram das personas, isso desqualifica o plano gratuito.

| # | O que fazer | Onde |
|---|---|---|
| 1 | Criar a chave no AI Studio | https://aistudio.google.com/apikey |
| 2 | **Ligar o faturamento** no projeto da chave ("Set up billing"). Pré-pago de US$ 5 a 5.000, com créditos válidos por 1 ano. | [guia](https://ai.google.dev/gemini-api/docs/billing) · créditos: https://aistudio.google.com/billing |
| 3 | Pôr um **teto de gasto** no projeto (sugestão: US$ 5) | https://aistudio.google.com/spend |
| 4 | Ler os termos: no plano pago, "Google doesn't use your prompts or responses to improve our products" | https://ai.google.dev/gemini-api/terms |
| 5 | `.env`: `GEMINI_API_KEY=<a chave>` | idem |

### Opcional: só se você aceitar a jurisdição (§ Decisões)

| Fornecedor | Por que entraria | Onde os dados ficam | Chave | Observação |
|---|---|---|---|---|
| Alibaba Model Studio (`qwen3-vl-flash`) | Menor preço de lista encontrado: US$ 0,05 de entrada e 0,40 de saída por M em Singapura, cerca de US$ 0,0006 por `decide`. O preço veio de uma leitura, sem votação adversarial. | Singapura ou Virgínia (a chave fica presa à região); Frankfurt não confirmado | https://modelstudio.console.alibabacloud.com/model/settings/api-key · base EUA `https://dashscope-us.aliyuncs.com/compatible-mode/v1` | preço nos EUA não confirmado; o cache explícito exige código |
| DeepSeek (`deepseek-flash`) | Metade do preço fora do pico, que cobre o horário comercial de Brasília | **China**: a política de privacidade diz que os dados são coletados, processados e guardados na República Popular da China | https://platform.deepseek.com/api_keys · recarga: painel → Top up | **não recomendado** para capturas das personas |
| Black Forest Labs (FLUX.2) | Imagem com várias referências, se o rosto falhar no `gpt-image-2` | Alemanha (API regional UE e EUA) | https://dashboard.bfl.ai → API → Keys | API própria: adaptador novo |
| fal.ai (Qwen Image Edit 2511) | Idem, cerca de US$ 0,03 por imagem | EUA | https://fal.ai/dashboard/keys | API própria: adaptador novo |

**O que você me diz depois:** "chaves no `.env`" e quais autorizações dá (Etapa 2). Nunca a chave em si.

## Etapa 1 — Código sem gasto (prova `simulated`)

Isto roda num worktree, com teste direcionado e um provedor falso (`httpx.MockTransport`), e não toca a produção.

| ID (proposta) | O quê | Por quê | Esforço |
|---|---|---|---|
| 17.1 | **Chave e parâmetros por modelo no provedor compatível** (`planning/openai_provider.py`, `config.py::ModelCaps`). Primeiro, **a chave de `api_key_env` passa a ser resolvida também pelo `.env`.** Hoje só vale `os.environ` (`openai_provider.py:73`); o `.env` é lido pelo `EnvSettings`, que não o exporta para o ambiente, e nenhum provedor compatível com chave de verdade rodou até agora (o Ollama não usa chave). Depois: `ai.models.<m>.max_tokens_field: max_completion_tokens`, com `max_tokens` como padrão para Ollama e vLLM. Segundo: `ai.models.<m>.extra_body`, aplicado **depois** do `extra_body` do provedor. Os valores: `gpt-6-luna` → `{reasoning_effort: none}`; `gemini-3.1-flash-lite` → `{reasoning_effort: minimal}`; `deepseek-flash` → `{thinking: {type: disabled}}`. | Sem a chave resolvida pelo `.env`, a primeira chamada volta "credencial recusada" e custa um reinício autorizado à toa. O `max_tokens` está obsoleto na OpenAI, e o corpo hoje só manda ele (`openai_provider.py:116`). Sem `reasoning_effort: none`, o luna não chama ferramenta no Chat Completions (doc do modelo). O `extra_body` de hoje vale para o provedor inteiro, não para cada modelo. | P |
| 17.2 | **Rejulgamento offline com candidato** (`scripts/eval_rejudge.py --provedor <nome> --config <caminho>`). Hoje o script só usa o `AnthropicProvider` e o `config.yaml` de produção. O `--config` deixa declarar o candidato num arquivo à parte, sem tocar o `config.yaml` guardado. | Primeiro portão, quase de graça, **sem parque nem reinício**: os 56 veredictos do Opus já estão em `data/eval-rejudge.jsonl`. Julgar as mesmas capturas com o luna ou o Flash-Lite custa cerca de US$ 0,02 a 0,05 e mede a concordância do `verify`, além de dar um sinal de visão. | P |
| 17.3 | **`gpt-image-2` no adaptador** (`modules/identity/adapters/openai_images.py`). O custo passa a vir de `usage` (a resposta traz os tokens de entrada, de saída e o detalhe texto/imagem), com preço por token declarado: texto de entrada 5, imagem de entrada 8, saída 30 US$ por M. O `price_per_image` fica só para a pré-conferência do teto, com um valor conservador (ex.: 0,15) até ser medido. | O `gpt-image-1-mini` sai da API em 01/12/2026. O preço por imagem do `gpt-image-2` não está publicado, então se mede em vez de declarar. O `input_fidelity` é ignorado no `gpt-image-2`, porque a imagem de entrada é sempre de alta fidelidade: bom para manter o rosto e uma variável a menos. | P |
| 17.4 | **Blocos de exemplo e nomes de chave.** Em `config.example.yaml`: provedores `openai` e `gemini`, comentados, com `ai.models` e `ai.prices` do luna e do Flash-Lite. O Gemini vai com `structured_output: json_object`, porque o `json_schema` literal não foi confirmado na camada compatível; o padrão conservador do projeto já cobre isso. Em `.env.example`: `GEMINI_API_KEY` e `DASHSCOPE_API_KEY`. Em `docs/ia.md`: uma seção nova. | Ligar vira troca de configuração, conferida pelo `docs-check`. | P |

Base URLs verificadas:

- OpenAI: `https://api.openai.com/v1`;
- Gemini (compatível): `https://generativelanguage.googleapis.com/v1beta/openai/`, que aceita `tools` e imagem em data
  URI base64;
- DeepSeek: `https://api.deepseek.com`.

## Etapa 2 — Medição real (cada passo pede autorização em chat)

Nesta ordem, mudando **uma variável por vez**:

| Passo | O quê | Gasto estimado | Autorização |
|---|---|---|---|
| 2.1 | **Rejulgamento offline** (17.2): o luna e o Flash-Lite sobre as 56 capturas já julgadas pelo Opus. Critério: concordância com o Opus ≥ 73%, a do Haiku em 25/09, e menos falsos positivos que ele. | cerca de US$ 0,05 | chamada paga [T] |
| 2.2 | **Bateria de base de hoje** (`scripts/eval-run.ps1 -Label base-2026-09`), a configuração atual, para comparar na mesma janela | cerca de US$ 1,4 | [P] + [T] |
| 2.3 | **Braço luna**: `decide` e `verify` no `gpt-6-luna`, **com o escalonamento ainda no Opus 5.5**. Troca no `config.yaml` e reinício da produção. Roda só os casos do app de QA (`-Cases` sem os `ig-*`): as contas reais do Instagram só entram na base e no braço vencedor. | cerca de US$ 0,5 a 0,7 | reinício [P] + [T] |
| 2.4 | **Braço Flash-Lite**, igual ao 2.3 | cerca de US$ 0,5 a 0,7 | idem |
| 2.5 | **Só se o melhor braço passar:** o mesmo braço com o escalonamento no **Sonnet 5**, em vez do Opus 5.5. Depois, o vencedor roda os casos `ig-*`. | cerca de US$ 0,4 a 0,6 | idem |
| 2.6 | **Rosto da persona**: `gpt-image-2` em qualidade média, 2 personas × (principal + 2 variações) = 6 imagens. Você julga a manutenção do rosto. Só se falhar entram FLUX.2 ou Qwen Edit. | cerca de US$ 0,2 a 0,7 (preço por imagem a medir) | [T] |

**Critérios de aceite, escritos antes de rodar:**

- sucesso comprovado ≥ o da base (em 25/09 foram 16 de 17);
- taxa de escalonamento ≤ 1,5 vez a da base (hoje, cerca de 15% das decisões);
- **US$ por objetivo comprovado ≤ 50% do da base**;
- p95 de latência do `decide` ≤ o da base mais 1 s, lido em `GET /api/desempenho?dias=1` por modelo executado;
- zero ação em tela sensível, com a guarda que já existe.

**Regras da medição:**

- Os braços rodam **sem `fallback_provider`**. Uma falha do provedor aparece como erro do caso, anotada à parte, e
  não infla o custo do braço com chamadas ao Sonnet. Na adoção (Etapa 3), o fallback volta.
- A camada compatível do Gemini pode não informar `prompt_tokens_details.cached_tokens`. Um `cache_read` zerado
  nesse braço pode ser falta de informação, não falta de cache.

`eval_run.py` mede o backend que está no ar. Por isso **cada braço é uma troca de `config.yaml` com reinício da
produção**, e a base e cada braço rodam com o parque livre. O reinício e a bateria tocam o parque e gastam API, e
cada um pede autorização.

## Etapa 3 — Adoção (deploy autorizado)

1. `config.yaml` de produção:
   - `decide` e `verify` no provedor vencedor, **com `fallback_provider: anthropic` e o `fallback_model` declarados**.
     Pela regra do hub (sem fallback pago silencioso), sem isso uma queda da OpenAI para o parque. A saúde já acusa
     o uso do fallback (`ai_fallback_em_uso`).
   - `escalation` no Sonnet 5, se o passo 2.5 passou.
   - `plan` e `social` ficam como estão: `plan` no Opus 5.5, `social` no Sonnet 5 (a qualidade em pt-BR fora da
     Anthropic não foi verificada).
   - `ai.image.provider: openai` com o `gpt-image-2`, se o passo 2.6 passou.
   - As linhas de `ai.prices` e `ai.models` do modelo novo.
2. Documentos:
   - um ADR novo ("provedores por papel"), com a prova `real`;
   - uma seção em `relatorio-validacao.md` com as ids das execuções;
   - `CHANGELOG.md` e `estado-atual.md`.
3. Uma semana olhando `GET /api/usage` e `GET /api/desempenho`. Se o US$ por objetivo comprovado subir ou o sucesso
   cair, a volta é trocar de novo o `config.yaml` (o Sonnet 5 continua configurado como fallback).

## Etapa 4 — Segunda onda e parados (com gatilho)

| ID (proposta) | O quê | Gatilho para fazer | Esforço |
|---|---|---|---|
| 17.5 | **Perfil de IA por execução** (`ai.profiles.<nome>` + `RunCreate.ai_profile` + `eval_run.py --profile`): A/B e canário sem reiniciar a produção, com uma fatia das execuções no candidato | a Etapa 2 provar valor e as comparações virarem rotina | M |
| 17.6 | **Flex para trabalho offline**: a geração de persona e o rejulgamento por uma entrada de provedor com `service_tier: flex` e prazo longo (pode levar minutos e devolver 429) | o volume de geração de persona crescer; hoje o ganho é de centavos | P |
| — | Alibaba `qwen3-vl-flash` como braço da bateria | você aceitar Singapura ou EUA na Alibaba; confirmar o preço na região | P (config) + P (marcador de cache) |
| — | DeepSeek | você aceitar a guarda dos dados na China | P (config) |
| — | Local: GUI-Owl-1.5 2B/4B no RTX 2000 Ada (8 GB) | as capturas não poderem sair da máquina, ou o volume crescer cerca de 10 vezes; antes, provar que cabe na VRAM junto com os emuladores | M |
| — | Destilação ou LoRA a partir de `ai_calls` e das receitas | idem, e o custo do ajuste fino hospedado verificado | G |
| — | `gpt-6-sol` no `plan` ou no `escalation` | nenhum por custo: custa o mesmo que o Sonnet 5 (2/10 por M), que já está integrado e medido | — |

## Custo-benefício por item (ordem de execução)

| Ordem | Item | Esforço | Gasto para provar | Economia estimada por mês* | Autorização |
|---|---|---|---|---|---|
| 1 | Contas, crédito, limites e chaves (Etapa 0) | cerca de 30 min seus | US$ 15 pré-pagos | — | é sua |
| 2 | 17.1 parâmetros por modelo | P | 0 | habilita tudo | não (worktree) |
| 3 | 17.2 + 2.1 rejulgamento offline | P | cerca de US$ 0,05 | portão barato para o `verify` | [T] |
| 4 | 17.3 `gpt-image-2` + 2.6 rosto | P | até cerca de US$ 0,7 | imagem real; resolve o fim do mini | [T] |
| 5 | 17.4 exemplos e nomes | P | 0 | — | não |
| 6 | 2.2 a 2.4 bateria base, luna e Flash-Lite | — | cerca de US$ 2,5 a 3 | **US$ 21 a 23** (`decide` + `verify`) | [P] + [T] |
| 7 | 2.5 escalonamento no Sonnet | — | cerca de US$ 0,4 | **+ US$ 4** | [P] + [T] |
| 8 | Etapa 3 adoção | P | 0 | a soma dos anteriores | deploy [P] |
| 9 | 17.5 perfil por execução | M | 0 | torna o A/B contínuo | depois |

\* Ritmo dos últimos 7 dias. Com o parque cheio (cerca de 10 vezes o volume), multiplique por 10.

## Decisões suas

1. **Jurisdição das capturas.** Recomendado: EUA (OpenAI e Google no plano pago). A Alibaba é opcional, conforme a
   região. A DeepSeek não é recomendada, porque os dados ficam na China.
2. **Política de uso.** O código recusa idade abaixo de 18. Uma violação das políticas de uso pode suspender a
   conta.
3. **Autorizações da Etapa 2**, passo a passo: 2.1 e 2.6 não tocam o parque; 2.2 a 2.5 tocam o parque e reiniciam a
   produção.
4. **Registrar a Fase 17 no plano-100**, se quiser acompanhar pelo mecanismo.

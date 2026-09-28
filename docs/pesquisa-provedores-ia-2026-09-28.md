# Pesquisa: provedores de IA além da Anthropic (28/09/2026)

> **Natureza deste documento.** É uma pesquisa de preço de lista e de documentação pública. Não mede nada neste
> projeto: a qualidade de cada candidato é `not_run`. Nenhuma chamada paga foi feita. Toda troca de modelo depende
> antes da bateria baseline × candidato (decisão 7 do plano-100, `scripts/eval-run.ps1`). Essa bateria gasta API e
> exige autorização em chat.
>
> **Como foi feita.** Workflow `deep-research`, com 101 agentes em 5 ângulos. Ele leu 19 fontes e extraiu 95
> alegações. Das 25 que passaram por verificação adversarial (3 votos cada), 21 foram confirmadas e 4 refutadas.
> Os preços foram consultados em 28/09/2026. Os números de gasto do projeto vêm de `GET /api/usage` (real, lido em
> 28/09). O encaixe no código foi conferido em `07fce91`. Duas alegações de maior consequência foram relidas
> direto na fonte em 28/09: os preços do `gpt-6-luna` e a descontinuação do `gpt-image-1-mini`. As duas se
> confirmaram. O plano de implementação, com os links de criação de conta e chave, está em
> [plano-provedores-ia-2026-09-28.md](plano-provedores-ia-2026-09-28.md).

## Resumo

- **O gasto está no ator.** O `decide` no Sonnet 5 custa cerca de US$ 0,0147 por chamada e responde por metade do
  gasto dos últimos 7 dias. Com o escalonamento ao Opus, o ator chega a 69%.
- **Há três candidatos verificados abaixo desse valor, todos com visão e tool calling:**
  - `gpt-6-luna` (OpenAI): cerca de US$ 0,0011 por chamada, 13 vezes menos;
  - `deepseek-flash` V4.1: US$ 0,0016 a 0,0032;
  - Gemini 3.1 Flash-Lite: cerca de US$ 0,0028.

  Os mesmos modelos levariam o `verify` de cerca de US$ 0,004 para US$ 0,0004 a 0,001.
- **O risco é o escalonamento, não o preço por chamada.** O qwen3-vl 4B local empatou em custo porque vagava e
  escalava ao Opus. A métrica que decide é o **custo por objetivo comprovado, com os escalonamentos incluídos**.
- **Os modos de processamento pesam menos que a troca de modelo.** Batch e Flex dão 50%, mas não servem ao laço
  síncrono. Servem à geração de persona, às baterias e à rotulagem. O cache só pega com um prefixo estático acima
  do mínimo de cada provedor.
- **Imagem:** no volume do projeto (14 a 42 imagens), o preço total fica entre US$ 0,07 e 3,4, então não decide a
  escolha. Decidem a manutenção do rosto, a política de uso e a vida útil do modelo. **O `gpt-image-1-mini`
  planejado sai da API em 01/12/2026** (aviso de 02/06/2026, confirmado na página de descontinuações), com o
  `gpt-image-2` como sucessor, bem mais caro por token.
- **A trilha local, serverless ou de ajuste fino não se paga** no volume atual. Só se justifica se os dados não
  puderem sair da máquina ou se o volume crescer cerca de 10 vezes.
- **Há duas decisões que são do dono:** para onde podem ir as capturas do Instagram das personas (jurisdição e
  política de dados de cada provedor) e se a política de uso do provedor admite o uso pretendido.

## 1. Onde o dinheiro vai hoje (medido, `real`)

`GET /api/usage?days=7`, lido em 28/09/2026. Foram 66 objetivos, com 9,1 chamadas e US$ 0,163 por objetivo. Dos
passos, 172 foram só por IA, 104 por receita e 20 por receita com IA.

| Papel | Modelo | Chamadas | US$ | US$ por chamada | Entrada / saída por chamada | Parte do total |
|---|---|---|---|---|---|---|
| `decide` (tier 0) | claude-sonnet-5 | 367 | 5,38 | 0,0147 | 9,8 mil / 174 | 50% |
| `decide` (tier 1 = escalonamento) | claude-opus-5 e 5-5 | 65 | 2,05 | 0,0315 | 9,9 mil / 220 | 19% |
| `plan` | claude-opus-5 e 5-5 | 70 | 2,70 | 0,039 (5.5: 0,0245) | 3,2 a 4,5 mil / 740 a 1250 | 25% |
| `verify` | claude-haiku-4-5 | 142 | 0,56 | 0,0039 | 3,2 mil / 150 | 5% |
| `social` | claude-sonnet-5 | 8 | 0,08 | 0,0095 | 2,9 mil / 354 | 1% |
| **Total** | | | **10,77** | | | |

Esse ritmo dá cerca de **US$ 42 a 46 por mês** (a faixa menor supõe o `plan` todo no Opus 5.5). O uso vem em
rajadas: nos últimos 3 dias foram US$ 0,46. O saldo da API é pequeno e separado dos créditos da IDE, e isso pesa
mais que o valor absoluto. Um ator mais barato faz o mesmo saldo render várias vezes mais execuções.

## 2. Recomendação por papel

Todas as linhas são **candidatas à bateria**, não trocas prontas.

| Papel | Hoje | Candidatos (preço de lista, 28/09) | Custo estimado por chamada | Encaixe no código | Risco |
|---|---|---|---|---|---|
| `decide` | Sonnet 5, US$ 0,0147 | 1) `gpt-6-luna` · 2) `deepseek-flash` V4.1, sem thinking · 3) Gemini 3.1 Flash-Lite | 0,0011 · 0,0016 a 0,0032 · 0,0028 | `kind: openai` com ajustes (§7) | escalonar mais ao Opus, porque o modelo pequeno vaga (medido com o qwen 4B) |
| `escalation` | Opus 5.5, cerca de US$ 0,031 | Sonnet 5 no lugar do Opus 5.5 (metade do preço, mesma Anthropic), **só junto com a troca do ator**: hoje o ator já é o Sonnet 5, e o tier 1 seria o mesmo modelo repetindo a tentativa. Nenhum candidato externo verificado. | cerca de 0,015 | configuração (`AI_MODEL_ESCALATION`), **depois** da troca do `decide` | perder a rede de segurança nas etapas com efeito externo |
| `verify` | Haiku 4.5, US$ 0,0039 | `gpt-6-luna` · Flash-Lite · `deepseek-flash` | 0,0004 · 0,0009 · 0,0005 a 0,001 | idem `decide` | falso positivo. O Haiku já errou nos dois sentidos (B14); o rejulgamento escalado segue valendo. |
| `plan` | Opus 5.5, US$ 0,0245 | nenhum substituto com qualidade verificada. Pelo preço, o `gpt-6-sol` custa por token metade do Opus 5.5 (o mesmo que o Sonnet 5). O Gemini 3.8 Flash é candidato a qualidade, não a economia (dobra de preço em 01/01/2027). | cerca de 0,013 (`gpt-6-sol`) | `kind: openai` | plano ruim contamina a execução inteira |
| `social` | Sonnet 5 | manter. Não há evidência verificada de qualidade em pt-BR fora da Anthropic; o benchmark Prosa foi refutado por 1 a 2. | — | — | voz da persona e conteúdo |
| Geração de persona (texto) | papel `social` | manter o modelo; mandar por Flex ou Batch quando o provedor tiver, porque é tarefa offline | −50% | o esquema já vai no texto (K-042, `07fce91`), o que torna o papel independente do provedor | limites de esquema fora da Anthropic não verificados |
| Imagem | simulada | ver §5 | US$ 0,005 a 0,08 por imagem | adaptador novo atrás de `ImageGenerator` | política de uso e fim do `gpt-image-1-mini` |

### Cenários por mês, no ritmo dos últimos 7 dias (derivado)

Contas: preço de lista × formato de tokens medido com o tokenizer da Anthropic. Outros tokenizers contam imagem e
texto de outro jeito, então isto é ordem de grandeza.

| Cenário | `decide` | `escalation` | `plan` | `verify` | `social` | Total por mês | Variação |
|---|---|---|---|---|---|---|---|
| Hoje (plan no Opus 5.5) | 23,1 | 8,8 | 7,4 | 2,4 | 0,3 | **cerca de 42** | — |
| A: `decide` e `verify` no `gpt-6-luna` | 1,7 | 8,8 | 7,4 | 0,2 | 0,3 | **cerca de 18** | −56% |
| A, com o escalonamento dobrado (o risco medido com o qwen 4B) | 1,7 | 17,6 | 7,4 | 0,2 | 0,3 | **cerca de 27** | −35% |
| B: A + escalonamento no Sonnet 5 | 1,7 | 4,4 | 7,4 | 0,2 | 0,3 | **cerca de 14** | −67% |
| A com `deepseek-flash` fora do pico | 2,5 | 8,8 | 7,4 | 0,3 | 0,3 | **cerca de 19** | −55% |

A economia absoluta hoje é de US$ 15 a 28 por mês. Com o parque cheio (15 aparelhos, cerca de 10 vezes o volume),
passa a US$ 150 a 280 por mês, e o custo por objetivo cai de cerca de US$ 0,16 para cerca de US$ 0,07.

## 3. Candidatos verificados (preço de lista em 28/09/2026)

US$ por milhão de tokens.

| Modelo | Provedor | Entrada · cache · saída | Batch ou Flex | Visão | Compatível com OpenAI | Cache mínimo | Fonte |
|---|---|---|---|---|---|---|---|
| `gpt-6-luna` | OpenAI (EUA) | 0,10 · 0,01 (escrita 0,125) · 0,50 | Flex e Batch: 0,05 · 0,005 · 0,25 | sim | nativo; tool call no Chat Completions **só com `reasoning_effort: none`** | não verificado | [preços](https://developers.openai.com/api/docs/pricing), [modelo](https://developers.openai.com/api/docs/models/gpt-6-luna) |
| `gpt-6-sol` | OpenAI (EUA) | 2,00 · 0,20 · 10,00 | Flex e Batch: 1,00 · 0,10 · 5,00 | não verificado | nativo | não verificado | [preços](https://developers.openai.com/api/docs/pricing) (leitura direta, 28/09) |
| `deepseek-flash` (V4.1-Flash) | DeepSeek (China, inferido) | pico 0,30 · 0,006 · 1,20; fora do pico, metade | sem Batch; desconto por horário, automático e síncrono | sim, até 1.024 tokens por imagem | não reverificado nesta pesquisa | não verificado | [preços](https://api-docs.deepseek.com/quick_start/pricing), [visão](https://api-docs.deepseek.com/guides/vision/), [repreço 10/09](https://api-docs.deepseek.com/news/news260910) |
| Gemini 3.1 Flash-Lite | Google (EUA) | 0,25 · 0,025 · 1,50 | Batch: 0,125 · 0,75 | sim | não verificado (sem ele, é provedor novo) | não listado (o 3.x é 4.096) | [preços](https://ai.google.dev/gemini-api/docs/pricing) |
| Gemini 3.8 Flash | Google | 0,75 · — · 3,75 até 31/12/2026; **1,50 · 0,15 · 7,50 a partir de 01/01/2027** | Batch e Flex: −50%; Priority: 1,8× | sim | idem | 4.096 | idem |
| Qwen3-VL flash e plus | Alibaba Model Studio (regiões à escolha) | **preço não verificado** | — | sim | **sim** (`compatible-mode/v1`, confirmado) | 1.024 (explícito: escrita 125%, acerto 10%) | [cache](https://www.alibabacloud.com/help/en/model-studio/context-cache) |

Janela de pico da DeepSeek: segunda a sexta, 22h–01h e 03h–07h em Brasília, fora os feriados chineses. Quase todo
o horário comercial brasileiro paga o preço fora de pico. **O thinking vem ligado por padrão** no `deepseek-flash`
e na série Gemini 3.x, e a saída pode passar muito dos 170 tokens medidos. É preciso desligar ou medir.

## 4. Alavancas de processamento, em ordem de economia esperada

1. **Trocar o modelo do `decide`**: de −78% a −92% por chamada, no papel que domina o gasto.
2. **Um prefixo estático grande antes da tela** (sistema e ferramentas primeiro; captura e árvore depois), para o
   cache pegar. O acerto custa 2% da entrada na DeepSeek, 10% no `gpt-6-luna` e 10% a 20% na Alibaba. O Gemini tem
   cache implícito a partir de 4.096 tokens de prefixo **comum**, não de entrada total, e nenhum provedor garante o
   acerto. O projeto já mediu um prefixo de cerca de 6 mil tokens no ator, acima do mínimo; o do verificador, cerca
   de mil, fica abaixo de todos os mínimos, exceto os 1.024 da Alibaba.
3. **O horário fora de pico da DeepSeek**: −50%, automático e síncrono.
4. **Trocar o `verify`**: de −75% a −90% por chamada, mas pouco peso absoluto (5% do gasto).
5. **Batch ou Flex a 50%, só no que é assíncrono:** geração de persona, baterias de avaliação, rotulagem de
   trajetórias e imagens (o Batch da OpenAI aceita imagem). O Flex da OpenAI é síncrono, mas lento (pode levar
   minutos), às vezes devolve 429 sem cobrar, está em beta e **não** oferece residência na UE. A alegação de que o
   Batch sai a 50% em todos os modelos foi refutada: use o preço de cada modelo.
6. **Cache semântico e cascata:** não há evidência externa verificada. As receitas (45% dos passos sem IA) já são
   o cache mais forte que o projeto tem. A cascata de um modelo barato primeiro já foi medida aqui e empatou por
   causa do escalonamento.

## 5. Imagem da persona

| Opção | Preço por imagem (lista, 28/09) | Referência para manter o rosto | Encaixe | Observação |
|---|---|---|---|---|
| `gpt-image-1-mini` (planejado) | 1024²: 0,005 · 0,011 · 0,036 (baixa, média, alta). Retrato 1024×1536: 0,006 · 0,015 · 0,052. A imagem de referência soma US$ 2,50/M tokens de entrada. | `/v1/images/edits` | já existe (`adapters/openai_images.py`) | **sai da API em 01/12/2026** (aviso de 02/06/2026, confirmado em leitura direta; a página de preços não traz o aviso) |
| `gpt-image-2` (sucessor) | por token, confirmado em 28/09: imagem de saída 30,00/M (o mini cobra 8,00), imagem de entrada 8,00/M, texto de entrada 5,00/M; Batch pela metade. O preço **por imagem** depende de quantos tokens cada imagem gasta e não foi verificado. Se gastasse o mesmo que o mini, a qualidade média sairia por cerca de US$ 0,041 (3,75×). | mesma Images API | provavelmente só `ai.image.model` e `price_per_image`; confira se aceita os mesmos `quality` e `output_format` | medir o preço por imagem; a política é a mesma da OpenAI |
| FLUX.2 [pro] ou [max] (BFL, Alemanha) | a partir de 0,03 ou 0,07 por megapixel; a cobrança das referências não está documentada | até 8 ([pro]) ou 10 ([max]) imagens de entrada, "maintaining identity" | adaptador novo (API própria) | O Kontext [pro] ou [max] custa 0,04 ou 0,08 fixo. Votação 2 a 1. |
| Qwen Image Edit 2511 (fal.ai) | cerca de 0,03 por imagem de 1 MP (talvez 0,06, se arredondar o megapixel) | `image_urls` (a documentação do próprio modelo recomenda de 1 a 3) | adaptador novo (fila HTTP da fal) | pesos abertos; não se verificou se cabe em 8 GB |
| Gemini 3.1 Flash Lite Image | 0,0336 (Batch: 0,0168) | **não verificada** | adaptador novo | o Gemini 2.5 Flash Image desliga em 02/10/2026 |

**Recomendação:** uma comparação cega da manutenção do rosto, com a mesma imagem 0 em 2 ou 3 provedores e 3
variações cada. Custa menos de US$ 1 em preço de lista e exige autorização. O caminho de menor esforço é o
sucessor do `gpt-image-1-mini` na mesma Images API. Duas coisas a conferir antes dessa troca: o parâmetro de
fidelidade da imagem de entrada nas edições (`input_fidelity`, que existia para o `gpt-image-1`) e se ele vale
para o sucessor.

**Política de uso (eixo do dono).** As Usage Policies da OpenAI (vigentes desde 29/10/2025, conferidas num snapshot
de novembro de 2025) proíbem "deceit, fraud, scams, spam, or impersonation" e o uso da semelhança de pessoa real
sem consentimento. Os Service Terms proíbem reproduzir a semelhança de qualquer pessoa sem consentimento e a de
menores. Na prática, a imagem 0 tem de ser gerada (adulto fictício) e nunca uma foto de pessoa real; o código já
recusa idade abaixo de 18. Cabe ao dono decidir, provedor por provedor, se a persona conversando numa plataforma
real se enquadra na cláusula de engano. Uma violação pode suspender a conta no provedor. As políticas de Google,
DeepSeek, Alibaba, BFL e fal não foram verificadas.

## 6. Local, serverless e ajuste fino

- **Sem ponto de equilíbrio no volume atual.** Com o `decide` a US$ 0,001–0,003 numa API, o que sobra no papel são
  poucos dólares por mês. Uma GPU serverless aquecida ou um ajuste fino com custo único teria de custar menos que
  isso para empatar.
- **O único candidato local com evidência é o GUI-Owl-1.5** (Tongyi/Alibaba, licença MIT, afinado por RL para
  interface). No AndroidWorld, segundo os próprios autores, o 2B marca 67,9, o 4B 69,8 e o 8B 69,0
  ([arXiv 2602.16855](https://arxiv.org/html/2602.16855v1)). Terceiros reproduziram só o 32B. O benchmark usa só
  captura de tela e o espaço de ações próprio, não a árvore de 60 elementos e a tool call tipada deste projeto.
  Nada se verificou sobre caber em 8 GB quantizado, dividindo a GPU com os emuladores. O qwen3-vl 4B genérico, da
  mesma base, vagou aqui.
- **Destilar das trajetórias gravadas** (`ai_calls`, receitas) com LoRA sobre um modelo da classe Qwen-VL é
  plausível, mas nenhum preço de ajuste fino hospedado nem de GPU serverless (Modal, RunPod, Together) sobreviveu
  à verificação. Se a trilha for retomada, a rotulagem offline pode rodar em Flex ou Batch.
- **O local só se justifica por residência de dados:** é o único ramo em que as capturas não saem da máquina.

## 7. Encaixe no código (conferido em `07fce91`)

- **`kind: openai` (`backend/app/planning/openai_provider.py`)** já manda `tools`, `image_url` e `response_format`
  (`json_schema` ou `json_object`, conforme o declarado). `ProviderCfg.extra_body` (`config.py`) é por **provedor**
  e entra no corpo com `body.update`. Para ter ajustes diferentes por papel, basta declarar duas entradas em
  `ai.providers` com o mesmo `base_url` e `extra_body` diferentes. Com isso dá para mandar:
  - `reasoning_effort: none` para o `gpt-6-luna`;
  - o thinking desligado para o `deepseek-flash`;
  - `service_tier: flex` para trabalho offline.
- **Pendência (checagem de 28/09):** o corpo manda `max_tokens` (`openai_provider.py:116`), que a referência da
  OpenAI dá como obsoleto em favor de `max_completion_tokens`. Não se confirmou se o `gpt-6-luna` ainda aceita o
  campo antigo. O `extra_body` não tira uma chave, só acrescenta. A correção é pequena: declarar por modelo o nome
  do campo (item 17.1 do [plano](plano-provedores-ia-2026-09-28.md)).
- **Cache explícito da Alibaba:** exige o marcador `cache_control` dentro de um elemento de `content`, uma pequena
  mudança no provedor genérico. O implícito é automático.
- **Gemini e DeepSeek (checagem de 28/09):**
  - A camada do Gemini está em `https://generativelanguage.googleapis.com/v1beta/openai/` e aceita `tools` e
    imagem em data URI. Não dá para desligar o raciocínio nos modelos 3; o Flash-Lite aceita
    `reasoning_effort: minimal`. O `json_schema` literal não foi confirmado.
  - A DeepSeek fica em `https://api.deepseek.com`, em formato compatível com OpenAI, e o thinking se desliga com
    `thinking: {type: disabled}`. Não se confirmou ferramenta e imagem na mesma chamada. A política de
    privacidade diz que os dados ficam na China.
- **Capacidade e preço são declarados** (`ai.models`, `ai.prices`). Um candidato novo entra com as capacidades e o
  preço de lista; um modelo sem preço declarado é contado pela tarifa mais cara, nunca como zero.
- **Imagem:** a porta é `modules/identity/application/ports.py::ImageGenerator` e a fábrica é
  `modules/identity/infrastructure/persona_images.py::construir_gerador`. Os adaptadores ficam em
  `modules/identity/adapters/`, e o custo em `ai_calls.usd` com `role='image'`. Provedor novo = adaptador novo +
  valor em `ai.image.provider`, sem mudar a receita nem a tela.

## 8. Eixos de decisão do dono

| Ramo | O que sai da máquina | Jurisdição e política (o que se verificou) | Custo do `decide` por mês (derivado) |
|---|---|---|---|
| API dos EUA barata (`gpt-6-luna`) | captura de 768 px, árvore e texto | EUA; residência na UE para o GPT-6 só no Standard, não no Flex. Treino e retenção **não verificados** nesta pesquisa. | cerca de US$ 2 a 3 |
| API chinesa (`deepseek-flash`) | idem | China (inferido pela tabela de pico); política não verificada | cerca de US$ 2,5 a 5 |
| Alibaba com região escolhida (Frankfurt ou Virgínia) | idem | Singapura, Pequim, Frankfurt, Hong Kong, Tóquio e Virgínia à escolha; política não verificada | não calculado (preço do Qwen-VL não verificado) |
| Google (Gemini) | idem | EUA; política do plano pago não verificada | cerca de US$ 4,4 (Flash-Lite) |
| Local | nada | fica na máquina | US$ 0 marginal; qualidade medida como empate; VRAM dividida |
| Serverless | idem à API, para o host escolhido | depende do host | não verificado |

A credencial nunca vai ao modelo: `type_secret` não passa pela IA (ADR-040), e as telas sensíveis não são
enviadas. O risco fica no conteúdo visual e textual das telas do app da persona.

**O dono decide:**

1. As jurisdições aceitas para as capturas.
2. Se lê e aceita os termos de uso de cada provedor para este uso.
3. Se autoriza a bateria A/B (§9).
4. O provedor de imagem.

## 9. Próximo passo proposto (exige autorização e chaves)

1. **O dono** cria as contas e as chaves nos provedores escolhidos. A IA não cria conta nem digita chave. A chave
   vai no `.env`, que a IA não lê.
2. **A bateria A/B**, com o `config/eval-set.yaml` congelado:
   - O que roda: a linha de base atual contra 1 a 3 candidatos no `decide` e no `verify`, com o `plan` e o
     `escalation` fixos.
   - O que se mede: o sucesso comprovado, a taxa de escalonamento, os tokens por captura em cada provedor e o
     **US$ por objetivo comprovado**.
   - Quanto custa: a bateria de 25/09 custou cerca de US$ 2,57 com rejulgamento. Uma rodada da base sai por cerca de
     US$ 1,4, e cada candidato por cerca de US$ 0,3 a 0,5 em preço de lista.
3. **Imagem:** a comparação cega do rosto (menos de US$ 1), antes de configurar um provedor pago.
4. **Só depois**, a troca na configuração de produção, com ADR e o registro da prova `real`.

## 10. Refutado e lacunas

**Refutado na verificação (não usar):**

- o Batch a 50% em todos os modelos da OpenAI e o Fast mode a 2×;
- o preço do Gemini 3.1 Flash Image (Nano Banana 2), com 560 tokens por referência (0 a 3);
- a edição no FLUX.2 a partir de US$ 0,045 (0 a 3);
- o GPT-5 Mini em 2º lugar no benchmark Prosa de pt-BR (1 a 2).

**Sem alegação verificada:**

- o preço por token do Qwen3-VL, do GLM-V, do Mistral, do Kimi e do Llama;
- a camada compatível com OpenAI do Gemini e da DeepSeek;
- os modelos especialistas de interface além do GUI-Owl (UI-TARS, OS-Atlas, Aguvis, Holo, Gemini computer use,
  OpenAI CUA) e o custo por passo no AndroidWorld;
- o preço de GPU serverless e de ajuste fino hospedado;
- os limites de esquema estruturado fora da Anthropic;
- a qualidade em pt-BR;
- as políticas de treino e de retenção de cada provedor;
- a imagem local em 8 GB;
- a **latência** de cada candidato. Nenhuma sobreviveu à verificação, e o `decide` hoje leva cerca de 2,9 s. A
  bateria precisa medir o p50 e o p95 por papel (`GET /api/desempenho?dias=N` já agrupa por modelo executado).

**Prazos que mudam as contas:**

| Data | O que muda |
|---|---|
| 02/10/2026 | o Gemini 2.5 Flash Image desliga |
| 01/12/2026 | o `gpt-image-1-mini` sai da API (confirmado) |
| 01/01/2027 | o Gemini 3.8 Flash dobra de preço |

A DeepSeek mudou os preços em agosto e em 10/09/2026. O Flex da OpenAI está em beta.

**Fora do escopo, por regra do projeto:** evasão de detecção de IA, remoção de marca d'água ou C2PA, e fazer uma
conta parecer autêntica.

## Fontes primárias

- OpenAI: [preços](https://developers.openai.com/api/docs/pricing),
  [gpt-6-luna](https://developers.openai.com/api/docs/models/gpt-6-luna),
  [Flex](https://developers.openai.com/api/docs/guides/flex-processing),
  [gpt-image-1-mini](https://developers.openai.com/api/docs/models/gpt-image-1-mini),
  [descontinuações](https://developers.openai.com/api/docs/deprecations),
  [Usage Policies](https://openai.com/policies/usage-policies/),
  [Service Terms](https://openai.com/policies/service-terms/)
- Google: [preços do Gemini API](https://ai.google.dev/gemini-api/docs/pricing),
  [cache](https://ai.google.dev/gemini-api/docs/caching)
- DeepSeek: [preços](https://api-docs.deepseek.com/quick_start/pricing),
  [visão](https://api-docs.deepseek.com/guides/vision/),
  [aviso de 10/09/2026](https://api-docs.deepseek.com/news/news260910)
- Alibaba: [cache de contexto no Model Studio](https://www.alibabacloud.com/help/en/model-studio/context-cache)
- Anthropic: [prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- BFL: [preços](https://docs.bfl.ml/quick_start/pricing), [notas de versão](https://docs.bfl.ml/release-notes)
- fal e Qwen: [Qwen Image Edit 2511 na fal](https://fal.ai/models/fal-ai/qwen-image-edit-2511),
  [blog da Qwen](https://qwen.ai/blog?id=qwen-image-edit-2511)
- GUI-Owl-1.5: [arXiv 2602.16855](https://arxiv.org/html/2602.16855v1),
  [model card](https://huggingface.co/mPLUG/GUI-Owl-1.5-2B-Instruct)

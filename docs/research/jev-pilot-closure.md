# JEV-PILOT: encerramento da fase técnica, gate de privacidade e desenhos futuros (2026-10-01)

Resumo executivo do piloto do modelo System One da TypeSafe (`jev-1.13.0`). Detalhes e números brutos: [`jev-pilot.md`](jev-pilot.md)
(§35 privacidade, §36 smoke, §37 Scrapy, §38 híbrido offline, §39 holdout Poetry e correção do avaliador). Branch `claude/jev-pilot`
(experimental; **não mergear**, **não rebase**). Nenhuma chamada ao Jev foi feita nesta etapa (`NEW_NETWORK_ATTEMPTS = 0`).

## 1. Resumo executivo

**`TECHNICAL_PHASE_STATUS = CLOSED`.** Nada mais de benchmark, nenhum terceiro corpus, nenhuma mudança de regra, nenhum envio de código privado.
`PUBLIC_BENCHMARK_AUTHORIZED = False`, `PRIVATE_CODE_SEND_APPROVED = False`.

**`TECHNICAL_VERDICT`:** *lexical safeguard + Jev semantic map* é um **candidato válido para recuperação de contexto top-K** (top-3/top-5). O Jev **não substitui**
grep/BM25 e **não está validado** como roteador de resposta única. Não está autorizado para código privado.

### EVIDENCE (real = máquina central, 2026-10-01; mapeamento exato em `jev-pilot.md`)

| Corpus | Resultado | Veredito |
|---|---|---|
| **Scrapy 2.19.0** (20 perguntas, 60 tentativas, US$ 0,0229) | `jev_map` SEMANTIC R@3 **0,958** (ripgrep 0,083; BM25 0,000), mas EXACT R@3 0,875 < ripgrep 1,000 (T1 −0,125) | **`NO_GO` preservado** (`ORIGINAL_VERDICT_CHANGED = NO`) |
| **Scrapy, híbrido offline** (replay, 0 chamadas) | EXACT 1,000; SEMANTIC 0,958; 0 critical misses | `PROMISING` exploratório, fraco por construção |
| **Poetry 2.5.1** (holdout, 30 perguntas, 60 tentativas, US$ 0,0245) | híbrido ALL R@3 **0,967**; EXACT 1,000; SEMANTIC 0,917; MIXED 1,000; 0 critical misses; p50 844 ms / p95 996 ms por pergunta | veredito histórico `FAIL` (avaliador v1 com defeito no H9); **`PROTOCOL_CORRECTED_REPLAY_VERDICT = PASS`** (H1–H9, replay offline) |

### LIMITATIONS

- **Memorização:** Scrapy e Poetry são públicos e podem estar no treino. Mede-se *utilidade operacional de recuperação* no corpus fixado, não recuperação pura a partir do payload.
- **Avaliador único** (o assistente montou os golden sets), 30 e 20 itens, baselines mecânicas de um disparo; um golden pequeno só confirma ganhos grandes.
- **A regra híbrida foi escrita depois de ver o Scrapy**; o Poetry é a confirmação, mas só uma. O MIXED foi construído para estressar a regra.
- **R@1 piora:** ALL R@1 do híbrido 0,567 contra 0,700 do `jev_map`; no MIXED 0,000 contra 1,000 (a salvaguarda põe o arquivo lexical errado em 1º por construção).
- **Correção pós-hoc do avaliador:** o H9 foi corrigido depois de ver o resultado (limiar e dados intactos, replay sem rede); o `FAIL` histórico está preservado.
- Só código **público em inglês**; só Python; o contrato `score` da API divergiu da documentação (1 nível aceito, `API_CONTRACT_MISMATCH`, §36.2).

### ARCHITECTURE_CANDIDATE

```
consulta → salvaguarda lexical (identificador explícito + grep/BM25)  +  mapa semântico do repositório (Jev) → top-3/top-5 de contexto
```

Recuperação de **contexto top-K**, não roteador de resposta única; não se otimiza para top-1. Desenho de integração futura em §7.

### WHAT_WAS_REJECTED

- Jev **como substituto de grep/BM25**: sozinho (`jev_map`) perdeu itens EXACT nos dois corpora (Scrapy R@3 0,875; Poetry 0,750).
- **`jev_rerank`** (BM25 → Jev) como produto: o shortlist BM25 cobre 0 % das perguntas semânticas do Scrapy; ficou só como controle (`PARTIAL_GO`).
- Jev como **roteador de resposta único / otimização de R@1**.
- **Enviar código privado** (`BLOCKED_PRIVACY`), plugin comunitário, integração no runtime ou no Claude Code real, merge da branch.
- Tratar a flag "Zero data retention: Yes" do catálogo da Cloudflare (terceiro, não contratual) como prova.
- Mudar limiares, regra ou golden depois de ver resultado.

### WHAT_WAS_VALIDATED / NOT VALIDATED

| VALIDATED (dois repos públicos) | NOT VALIDATED |
|---|---|
| recuperação híbrida de contexto **top-K** (R@3/R@5) | R@1 como objetivo |
| ganho semântico sobre ripgrep/BM25 (SEMANTIC R@3 0,92–0,96 contra 0,00–0,54) | código privado |
| proteção lexical (EXACT 1,000, 0 critical misses com a salvaguarda) | português |
| custo (≈ US$ 0,025 por 30 perguntas) | roteamento de modelo |
| latência (p50 844 / p95 996 ms por pergunta, 2 requisições) | seleção de ação |
| protocolo da API (autenticação, Choice/Score/Noul, `usage`) | automação em runtime |
| | segurança de produção, retenção de dados |

## 2. Chave

A chave usada no piloto apareceu no transcript da conversa: **`OLD_KEY_ROTATION_REQUIRED = YES`** (`KEY_ROTATION_RECOMMENDED = YES`). Não foi rotacionada por mim e **nenhuma requisição foi feita** com ela nesta etapa.

## 3. Próximo gate: `PRIVACY_ZDR_CONTRACT`

Pesquisa nova em fontes **oficiais** da TypeSafe, lidas em 2026-10-01 (nenhum blog de terceiro como prova contratual). Marcações: `OFFICIAL_EXPLICIT` (o texto diz), `OFFICIAL_AMBIGUOUS` (o texto toca no tema mas não responde), `NOT_DOCUMENTED`.

Fontes: [MCA](https://typesafe.ai/legal/mca) (atualizado **Sep 23, 2026**, inalterado desde a leitura anterior), [DPA](https://typesafe.ai/legal/data-processing) (**Apr 24, 2026**),
[Privacy Policy](https://typesafe.ai/legal/privacy-policy) (**Nov 19, 2025**), [AUP](https://typesafe.ai/legal/acceptable-use) (**Sep 23, 2026**), [Trust Center](https://trust.typesafe.ai)
e [subprocessadores](https://trust.typesafe.ai/subprocessors) (páginas renderizadas no navegador embutido; o botão "Request access" **não foi acionado**), [`docs.typesafe.ai/legal`](https://docs.typesafe.ai/legal),
[`/models`](https://docs.typesafe.ai/models), [`/api`](https://docs.typesafe.ai/api) e o índice `llms.txt` (sem página de retenção, região, logging ou preço fora dessas).
Primeira leitura detalhada: `jev-pilot.md` §35.

## 4. Matriz de afirmações (resposta às perguntas do dono)

| # | Tema | O que o oficial diz (citação curta) | Marca |
|---|---|---|---|
| 1 | **Retenção padrão do Input** | MCA §10.3: a TypeSafe "will be under no obligation to store or retain Customer Data and may delete Customer Data at any time in its sole discretion". Privacy Policy: dados pessoais "as long as reasonably necessary". **Nenhum prazo.** | `NOT_DOCUMENTED` (prazo); a existência de armazenamento é `OFFICIAL_EXPLICIT` (AWS) |
| 2 | **Onde o Input fica** | Trust Center: AWS "Customer information for live requests is stored and processed on databases, caches and compute nodes within AWS". Modal, Nebius, CoreWeave: "Customer AI prompts are processed, but not stored". | `OFFICIAL_EXPLICIT` (AWS guarda; os 3 não guardam prompt); duração `NOT_DOCUMENTED` |
| 3 | **Logs / traces / telemetria / diagnóstico** | MCA §4.3: Telemetry = "technical logs, hashes, summary statistics and classifications, metrics, and learnings related to Customer's use"; "TypeSafe may Process Telemetry without restriction, including to improve the Services or TypeSafe's other products and services". §4.1(c): "in perpetuity ... to derive and generate Telemetry". | `OFFICIAL_AMBIGUOUS` (existe, sem escopo nem retenção) |
| 4 | **O que são "learnings"** | Só a lista acima; sem definição, sem dizer se contêm trechos do Input, embeddings ou hashes identificáveis. | `NOT_DOCUMENTED` |
| 5 | **Abuse monitoring** | MCA §4.1(c)(ii): "in perpetuity ... to monitor for fraud and abuse of the Services"; AUP: "TypeSafe may monitor compliance with this AUP and investigate any violations". Retenção e alcance: nada. | `OFFICIAL_EXPLICIT` (existe, perpétuo); alcance/retenção `NOT_DOCUMENTED` |
| 6 | **Revisão humana** | Os termos "human" e "review" não aparecem no MCA; também ausentes na Privacy Policy e na AUP. | `NOT_DOCUMENTED` |
| 7 | **Treino** | MCA §4.1: não incluirá Customer Data "in a dataset used to train (i.e., to modify the model weights of)" modelos "without Customer's prior consent". Privacy Policy: "We will not train or fine tune any artificial intelligence or machine learning models on your prompts or other Input." `/models`: "Jev is not trained on customer requests or responses." | `OFFICIAL_EXPLICIT` (pesos). **Lacuna:** a Telemetry/"learnings" fica fora dessa promessa (`OFFICIAL_AMBIGUOUS`) |
| 8 | **ZDR existe?** | `docs.typesafe.ai/legal`: "We also offer zero data retention (ZDR) for enterprise customers"; "Contact sales@typesafe.ai". `/models` repete. | `OFFICIAL_EXPLICIT` (para enterprise); para **esta** conta: `NOT_DOCUMENTED` |
| 9 | **Abrangência do ZDR** | Nada sobre o que cobre (Input, Output, logs, Telemetry, abuse, caches, backups, subprocessadores), condições, preço ou prazo. O termo "zero" não aparece no MCA. | `NOT_DOCUMENTED` |
| 10 | **ZDR × Telemetry §4.3 / direito perpétuo §4.1(c)** | Não tratado. | `NOT_DOCUMENTED` |
| 11 | **Backups** | MCA §10.3: "Customer Confidential Information may be retained in TypeSafe's standard backups notwithstanding any obligation to delete ... but will remain subject to this Agreement's confidentiality restrictions." | `OFFICIAL_EXPLICIT` (backups podem reter); prazo `NOT_DOCUMENTED` |
| 12 | **Subprocessadores** | 6, todos EUA: AWS, Modal, Nebius, CoreWeave, Slack, Google Workspace. DPA §3: autorização geral, "reasonable advance notice" de novos, janela de 15 dias para objeção. O que Modal/Nebius/CoreWeave guardam de logs/caches/derivados: nada. | `OFFICIAL_EXPLICIT` (lista); retenção por subprocessador sob ZDR `NOT_DOCUMENTED` |
| 13 | **DPA** | Escopo: "Customer Personal Data" (§1.2). Incidente em 72 h; auditoria ≤ 1× / 12 meses às custas do cliente; SCCs módulos 2 e 3 e *UK Addendum*. Código-fonte em geral **não** é dado pessoal. Deleção/devolução: não presente; retenção (Schedule I, item 8): "for as long as necessary taking into account the purpose of the Processing". | `OFFICIAL_EXPLICIT` (existe); aplicabilidade ao código `OFFICIAL_AMBIGUOUS`; deleção `NOT_DOCUMENTED` |
| 14 | **Deleção sob pedido** | Privacy Policy: a pedido "we take measures to delete your personal data or keep it in a form that does not permit identifying you" (dado **pessoal**). Para Input: só §10.3 (sem obrigação de reter; podem apagar quando quiserem). Trust Center lista o *nome* do controle "Customer data deleted upon leaving" (relatório não lido). | `OFFICIAL_AMBIGUOUS` |
| 15 | **Região** | Privacy Policy: "The Services are hosted in the United States". Todos os subprocessadores: USA. Docs: um único endpoint `https://api.typesafe.ai/v1/systemone`. Região fora dos EUA, residência de dados: nada. | `OFFICIAL_EXPLICIT` (EUA); alternativas `NOT_DOCUMENTED` |
| 16 | **SOC 2** | Trust Center: "SOC 2 Type II - 2026" listado, com controles como "Data retention procedures established", "Customer data deleted upon leaving", "Data encryption utilized". Relatório atrás de "Request access" (não acionado); período, escopo e exceções desconhecidos. | `OFFICIAL_EXPLICIT` (alegação); conteúdo `NOT_DOCUMENTED` |
| 17 | **Confidencialidade / direitos do cliente** | MCA §14 (informação confidencial); §5: o **cliente** garante ter direitos e consentimentos para enviar o Input; §9.3 "AS IS", sem garantia de "manter Customer Data sem perda". | `OFFICIAL_EXPLICIT` |
| 18 | **Preço e limites** | `/models`: "Charged per input token. Output tokens are free."; limites "100K tokens per second / 40 requests per second", "adjusting dynamically", "can change without notice". Plano gratuito: não documentado. | `OFFICIAL_EXPLICIT` |

**Leitura honesta.** Há estrutura real (DPA, SCCs, SOC 2 listado, promessa de não treinar pesos). Para código-fonte **privado** ela protege pouco: o DPA cobre dado pessoal; a retenção não tem prazo; há direitos
perpétuos sobre Customer Data para Telemetry e abuse monitoring; os "learnings" não têm definição; não há apagamento sob pedido para Input; backups podem reter; o ZDR é "enterprise" e não tem
abrangência escrita. Nada indica conduta indevida; o escrito **não permite afirmar** que o código não ficaria nos servidores da TypeSafe ou da AWS.

`PRIVACY_UNKNOWNS` (a levar ao fornecedor): prazo de retenção do Input e dos logs; duração do armazenamento "live requests" na AWS; definição de "learnings"; se há revisão humana (abuse, suporte, debugging, incidentes);
condições, preço, prazo e **abrangência** do ZDR e se vale para esta conta; efeito do ZDR sobre Telemetry §4.1(c)/§4.3; retenção em Modal/Nebius/CoreWeave sob ZDR; DPA/termo vinculante para código;
relatório SOC 2; região fora dos EUA; mecanismo de deleção de Input. Mensagem pronta (**não enviada**): [`TYPE_SAFE_PRIVACY_REQUEST.md`](TYPE_SAFE_PRIVACY_REQUEST.md).

## 5. Gate explícito: `PRIVATE_CODE_GATE`

Arquivo de estado: `experiments/jev/private_gate.json` (validado por teste; **nunca marca PASS sozinho**). Estado atual: **`BLOCKED`**.

| Estado | Significado | Sai dele quando |
|---|---|---|
| `BLOCKED` | nenhuma pergunta enviada ao fornecedor; retenção `UNKNOWN` | o dono revisa e **envia** `TYPE_SAFE_PRIVACY_REQUEST.md` → `WAITING_VENDOR` |
| `WAITING_VENDOR` | pedido enviado, sem resposta escrita completa | resposta escrita: diz que ZDR existe para a conta → `ZDR_AVAILABLE`; diz que não → permanece `BLOCKED` |
| `ZDR_AVAILABLE` | o fornecedor diz que **pode** ativar ZDR (ainda não ativo, abrangência ainda não escrita) | ZDR ativado **e** abrangência por escrito (Input/Output/logs/Telemetry/abuse/caches/backups/subprocessadores) → `ZDR_CONFIRMED` |
| `ZDR_CONFIRMED` | ZDR ativo para a conta e escopo coberto por escrito (de preferência em DPA/termo vinculante) | evidência anexada ao gate → `OWNER_RISK_ACCEPTANCE_REQUIRED` |
| `OWNER_RISK_ACCEPTANCE_REQUIRED` | evidência completa; resta o risco residual (EUA, telemetria, subprocessadores, SOC 2 não lido) | o dono registra, em chat e em commit, a aceitação **explícita** do risco → `PASS` |
| `PASS` | evidência escrita suficiente **e** risco aceito pelo dono | só então um commit **próprio** pode liberar `PRIVATE_CODE_SEND_APPROVED` para o piloto de §6 |

Regras: **`ZDR_AVAILABLE` ≠ `PASS`**; o estado não avança por inferência minha nem por página de terceiro (Cloudflare incluída); resposta verbal não vale; mudança de contrato (MCA/DPA datados acima) reabre o gate;
um `PASS` do gate autoriza **estudar** o piloto de §6, não executá-lo (a execução exige autorização separada). Hoje: `PRIVATE_CODE_SEND_APPROVED = False`, `PRIVATE_CODE_BENCHMARK_STATUS = BLOCKED_PRIVACY`, `STANDARD_API_RETENTION = UNKNOWN`.

## 6. Piloto privado futuro — apenas DESENHO, não executado

Só se o gate chegar a `PASS`. Tudo abaixo é pré-condição, não ação:

1. **Escopo mínimo:** subconjunto pequeno (≤ 150 arquivos Python, abaixo do teto de 255 opções do Choice), preferindo código **não sensível** e de baixo risco; fora: `config/`, `.env`, `secrets`, credenciais, `data/`, `evidence/`, `backups/`, dados de aparelho, conta, persona ou cliente, logs, screenshots (a lista de caminhos sensíveis do `redact.py` é o mínimo, não o teto).
2. **Defesas locais antes da rede:** redação local de todo texto do payload; **gate de segredo duro** (private_key, bearer, api_key, jwt bloqueiam a requisição e nada sai); `allowed_files` limitando o payload ao subconjunto; teto de requisições no código (`max_retries = 0`); sem `--corpus-root` público.
3. **Shadow only:** o Jev só *sugere*; nada executa ação, nada altera o fluxo, o resultado não chega ao agente.
4. **Medição:** golden de 24–30 perguntas em **português e inglês** escrito e congelado antes de qualquer chamada, com critérios (incluindo avaliação do R@3/R@5, não do R@1) pré-registrados e avaliador testado antes (lição do H9); comparar híbrido × grep × BM25 × `jev_map`; custo, latência e falhas; MIXED incluído.
5. **Governança:** autorização explícita e separada do dono para a execução; chave rotacionada; relatório `real`/`simulated`/`not_run`; nenhum merge.
6. **Estimativa (extrapolada dos dois runs públicos; `simulated`/proxy, não medida em código privado):** 24–30 perguntas × 2 requisições ⇒ **48–60 chamadas** (teto 60). Os runs públicos gastaram ≈ 20 mil tokens de entrada
   por pergunta (`jev_map`: 20,0 mil no Scrapy, 19,5 mil no Poetry) com mapas de 172–174 arquivos; com ≤ 150 arquivos e português (mais tokens por byte, fator 1,0–1,3) estima-se **8–25 mil tokens por pergunta**, ou **0,2–0,75 M tokens ⇒ US$ 0,008–0,032** (esperado ≈ US$ 0,02).
   Teto absoluto do desenho (mapa de 255 linhas + etapa B de 80 KB em todas as perguntas): ≈ 43 mil tokens por pergunta ⇒ **≤ US$ 0,06** para 30 perguntas. Preço oficial: US$ 0,042 por Mtok de entrada, saída grátis.

## 7. Plano de integração futura — desenho, **sem implementar**

Preferir, se um dia adotarmos o Jev, uma implementação **limpa e mínima** baseada nestas conclusões, e **não** mergear esta branch.

```
consulta
   ↓
detector lexical local (identificador explícito: backticks, snake_case, CamelCase, dotted.path)
   ↓
salvaguarda grep/BM25 (local, sempre roda)
   +
mapa do repositório sanitizado (1 linha por arquivo; sem arquivos sensíveis)
   ↓
seleção semântica Jev (etapa A: arquivos; etapa B: trechos)
   ↓
fusão / rerank local (regra híbrida v1: melhor arquivo lexical + ranking do Jev, sem duplicatas)
   ↓
contexto top-3 / top-5
   ↓
Claude
```

Requisitos: **fail-open** (se o Jev falhar, estourar o tempo ou o orçamento, vale só a busca local) e **nunca bloquear o desenvolvimento**; timeout curto (a latência medida foi ≈ 0,36 s por requisição, p95 0,43 s); cache de mapa e de respostas por
(commit, pergunta); orçamento por sessão/dia com contador local; **redação antes da rede**; segredo duro ⇒ requisição bloqueada (nada sai); **nenhum arquivo sensível** vai ao Jev (lista de exclusão aplicada ao mapa e aos trechos);
observabilidade de custo, latência, taxa de falha e recall (amostra com verdade conhecida), registrada localmente; kill-switch por configuração; sem credencial na execução; a decisão de enviar código privado continua sendo do gate de §5.

## 8. Escopo desta etapa

Arquivos: este documento, `TYPE_SAFE_PRIVACY_REQUEST.md`, `experiments/jev/private_gate.json` e seu teste, `docs/research/jev-pilot.md` (§40, ponteiro), `CHANGELOG.md`, `docs/README.md` (índice).
Nenhuma chamada ao Jev; nenhum e-mail ou formulário enviado; nenhum código privado enviado; `main` intocada.

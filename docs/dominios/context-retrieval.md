# Domínio: retrieval de contexto de código

Escolhe, para uma pergunta ou tarefa, os poucos arquivos e trechos de código que valem ser lidos primeiro (top-3 a
top-5), com uma busca **local** (ripgrep e BM25) e, opcionalmente, um **provedor semântico** plugável. A decisão e as
alternativas estão em [ADR-063](../decisoes.md#adr-063--retrieval-de-contexto-de-código-léxico--bm25-locais-semântico-plugável-política-única-de-envio).
O piloto que originou a regra (`claude/jev-pilot`, **não mergeada**, evidência) fica fora da `main`.

Caminhos relativos a `backend/app/`. Código em `modules/context_retrieval/`.

## O que isto é e o que não é

- É **auxílio de leitura** para quem vai implementar ou investigar (pessoa, Claude Code, subagente, skill). Não decide
  ação em aparelho, clique, aprovação nem segurança; o planejador/ator/verificador da plataforma continuam lendo telas.
- Não é RAG persistente: nenhum banco vetorial, grafo ou "lago de embeddings" nesta fatia. Esses backends entram depois como
  mais uma implementação de `SemanticProvider`.
- Está **desligado por padrão**. Com `context_retrieval.enabled: false` nada muda e o serviço nem toca o disco.

## Arquitetura

```
pergunta ─▶ LexicalRetriever (ripgrep / Python) ─┐
            BM25Retriever (stdlib)              ─┴▶ LocalRetriever (fusão) ─┐
                                                                            ├▶ HybridRetriever ─▶ ContextPack
mapa do repositório (sem conteúdo) ─▶ SemanticRetriever ─▶ SemanticProvider ┘   (regra v1)
   etapa A: mapa → arquivos      etapa B: chunks só dos candidatos → regiões
```

| Peça | Arquivo | Responsabilidade |
|---|---|---|
| Tipos e portas | `domain/model.py`, `domain/ports.py` | `ContextSelection`, `ContextPack`, `RepoMap`, `SemanticProvider`, `ProviderLocality` |
| Política de envio | `domain/policy.py` | `ExternalContextPolicy`, `PRIVATE_CODE_SEND_APPROVED = False` |
| Caminho sensível e segredo | `domain/sensitive.py` | filtro de caminho; portão duro; reaproveita `security/redaction.py` |
| Identificador explícito | `domain/identifiers.py` | crases, snake_case, CamelCase, `a.b.c` |
| Léxico, BM25, mapa, chunks | `infrastructure/{workspace,lexical,bm25,repomap,chunker}.py` | tudo local, sem rede |
| Cache, métricas | `infrastructure/{cache,metrics}.py` | JSON em disco; lista fechada de campos |
| Provedores | `infrastructure/providers/{fake,jev,factory}.py` | só serializam, enviam, interpretam, medem e traduzem erro |
| Orçamento, fusão, híbrido, semântico, serviço | `application/*.py` | a regra de produto mora aqui, nunca no adaptador |
| Composição | `wiring.py` | do `context_retrieval:` ao serviço; único lugar que conhece as peças concretas |
| CLI e API | `presentation/cli.py`, `presentation/router.py` | `python -m app.modules.context_retrieval.presentation.cli`; `GET /api/context-retrieval/status` |

## Modos

| Modo | O que entrega | O provedor semântico |
|---|---|---|
| `disabled` (padrão) | nada (`gather` devolve `None`); o pipeline antigo segue | nunca |
| `local_only` | léxico + BM25 | nunca |
| `shadow` | o resultado **local** | roda e é medido (custo, latência, concordância); não altera o entregue |
| `hybrid` | o resultado híbrido | participa da seleção |

`enabled: false` vence o `mode` escrito. Ligar sem escolher o modo também não liga nada.

## Regra híbrida v1

1. Identificador explícito na pergunta **e** achado lexical: o melhor arquivo lexical (`lexical_preserve`, padrão 1, o que o
   piloto mediu) fica no topo; o ranking semântico completa sem duplicar; até 2 janelas lexicais entram antes das semânticas.
2. Sem sinal lexical forte: o ranking semântico é o principal.
3. Semântico falhou ou veio vazio: o resultado do local, com `fallback_used` e `fallback_reason`.

Não se otimiza para top-1; o contrato é top-3/top-5 de contexto.

## Privacidade (política, não bloqueio de desenvolvimento)

- A política responde `can_send_repository/file/chunk/query/payload` e olha **onde o provedor executa** e a **classe do
  repositório**, nunca o nome do provedor. Local: tudo passa. Falso: caminho e segredo valem como se fosse externo.
  Remoto: privado negado; público só com `allow_public`; sintético permitido.
- `PRIVATE_CODE_SEND_APPROVED = False` é constante de **código**. Mudar é decisão do dono, com ADR. Nenhum YAML liga.
- Caminhos que nunca entram em índice, mapa ou chunk: `.env*`, `config.yaml`, `secrets/`, chaves, credenciais, cookies,
  tokens, bancos, logs, `data/`, `evidence/`, `backups/`, `personas/`, `apks/`, mais `context_retrieval.sensitive_paths`.
- Portão duro (chave privada, JWT, bearer, chave de API, DSN com senha) **bloqueia o pedido**. Segredo "mole" (par
  chave/valor que a redação central mascararia) tira só o trecho do payload.
- `TYPESAFE_API_KEY` só no ambiente/`.env`; ausente = provedor indisponível = fallback. Nunca em config, cache, métrica,
  evento, log ou pacote.

## Fail-open, orçamento e payload

Timeout, 429, 529, provedor fora do ar, resposta inválida, orçamento estourado, bloqueio de privacidade e falha de parse caem
no local, com a razão em `fallback_reason` (`timeout`, `rate_limited`, `overloaded`, `provider_offline`, `invalid_response`,
`budget_exceeded`, `privacy_block`, `secret_block`, `key_missing`, `provider_unavailable`, `empty_semantic`, `no_provider`,
`provider_error`). Há teto de chamadas por pedido e por sessão, tokens de entrada, custo e prazo; e todo payload tem teto de
arquivos, chunks e bytes (`PayloadLimits`). Falha só da etapa B devolve a etapa A, sem regiões semânticas.

## Índice BM25 persistente e lote

O índice BM25 mora em disco, em `data/context_retrieval/bm25/bm25-<raiz>-<chave>.json`, e é carregado em vez de reconstruído.
- **Chave**: identidade da raiz (hash do caminho) + revisão do repositório (hash de árvore do git, com resumo do que está sujo) +
  `RETRIEVAL_VERSION` + `INDEX_VERSION` (tokenização, higiene e formato) + resumo do corpus (política de caminhos sensíveis e teto
  de tamanho). O cabeçalho dentro do arquivo repete a identidade e é conferido ao carregar: nome igual com identidade diferente é miss.
- **Seguro por construção**: gravação atômica (`.tmp` + `os.replace`); arquivo corrompido, truncado, adulterado ou com id de
  documento fora da faixa é miss silencioso e o índice é refeito; revisão nova gera outro arquivo e a antiga nunca é carregada; a
  limpeza mantém os 3 mais recentes por raiz.
- **O que vai para o disco**: só vocabulário e contagens (token → documento, frequência), nunca texto. Arquivo de caminho sensível
  nem entra no universo; linha com formato de credencial (duro, ou literal atribuído a nome de segredo) é descartada antes de
  tokenizar, e token alfanumérico longo com cara de chave ou hash não vira termo.
- **Lote**: `service.session()` (ou `gather_many`) congela a revisão e o universo e guarda o texto lido durante o lote (teto de 96 MB),
  de modo que Workspace, revisão, índice, mapa, cache e orçamento de SESSÃO são os mesmos para todos os itens. O orçamento de sessão
  não reinicia por item: no lote o total de chamadas ao provedor nunca passa de `max_calls_per_session`.
- `plano-100-pacotes.py --contexto` usa a API Python com um serviço só (sem subprocesso por item).
- **Motor léxico sem `rg`**: o ranking usa contagens (aditivas por linha) sobre o texto inteiro em minúscula; as linhas casadas, que só
  servem às janelas, são levantadas apenas para os arquivos escolhidos. Com `rg` o resultado é o mesmo (teste de paridade com o
  `rg` real em `test_context_retrieval_local.py`). O `rg` é opcional: configurado (`context_retrieval.lexical.ripgrep_path`),
  `RIPGREP_PATH` ou PATH; nenhum caminho de instalação é presumido. Wrapper `.cmd`/`.bat` recebe lotes menores (cmd.exe corta a linha em 8191).
- Medida (`scripts/bench-context-retrieval.py`, plano-100 inteiro, 273 itens, `local_only`, máquina do ambiente central, 01/10):
  construção a frio do índice 5,8 s; carga a quente do disco 0,27 s; consulta mediana 292 ms (P95 706 ms, máx 1,0 s); índice 4,35 MB;
  273 acertos de cache BM25 e 0 erros; total 96,7 s contra ~1.550 s da linha de base de um subprocesso por item (**16x**).

## Cache da resposta semântica

Chave = revisão do repositório (hash de árvore do git, mais um resumo do que está sujo) + pergunta normalizada + escopo +
`RETRIEVAL_VERSION` + provedor + modelo + etapa + limites de payload + **política de envio vigente** (classe do repositório, padrões
sensíveis, provedor): mudar o que pode sair da máquina nunca reaproveita uma resposta dada sob a política antiga, e uma resposta
guardada é refiltrada contra o mapa permitido de agora (arquivo que passou a ser bloqueado não volta). O mapa tem cache próprio por revisão. Em `data/context_retrieval/`
(fora do Git), JSON, sem migração. Guarda caminho, linha e nota; nunca código nem segredo.

## Observabilidade

Um evento por pedido em `data/context_retrieval/events.jsonl`, gravado só com campos de uma **lista fechada**
(`infrastructure/metrics.py::CAMPOS`): retriever, modo, impressão digital da pergunta, arquivos considerados/escolhidos,
regiões, latência, fallback e razão, provedor, modelo, tokens, custo, cache, razão de bloqueio. Código completo, pergunta crua
e segredo nunca entram. Os contadores também vão para `metricas` (`context_retrieval.*`). `GET /api/context-retrieval/status`
devolve o modo efetivo, a disponibilidade do provedor (sem rede e sem a chave), a decisão de envio e o resumo.

## Como usar

```bash
cd backend && .venv/Scripts/python.exe -m app.modules.context_retrieval.presentation.cli "onde `verify_token` é definido" --mode local_only
```

Sem `--mode`, vale a configuração (desligada = a CLI avisa e não lê nada). `--provider` só aceita `none` e `fake`: o provedor
remoto só liga pela configuração. Primeiro ponto de integração: `python scripts/plano-100-pacotes.py --contexto`, que acrescenta
"Sugestões de contexto" aos pacotes; opt-in, e os pacotes gerados assim **não se commitam** (dependem da revisão do código).

## Regressão contra o piloto

`backend/tests/test_context_retrieval_pilot_regression.py` alimenta a implementação nova com o que o piloto MEDIU no holdout público
Poetry 2.5.1 (30 perguntas: 12 EXACT, 12 SEMANTIC, 6 MIXED) e confere que ela chega ao que a regra v1 do piloto produz. A fixture
(`backend/tests/fixtures/context_retrieval/pilot_regression.json`) é gerada por `scripts/gen-pilot-regression-fixture.py` a partir
dos resultados públicos e da própria regra do piloto (que calcula identificadores, salvaguarda e a saída pura); nada do código do piloto
é importado e a suíte não depende da branch dele.

Resultado: identificadores explícitos iguais em 30/30; salvaguarda igual em 30/30 (18 ativas); arquivos iguais em top-3 e top-5
(60/60); regiões na mesma ordem e no começo em 60/60. Divergências **intencionais**: (1) o piloto entrega até 8 arquivos, aqui o contrato
é `top_k`; (2) o piloto corta regiões por 8 trechos e 16 KiB, aqui as regiões não carregam texto e só entram as de arquivos que ficaram
no `top_k`; (3) o piloto não definia fallback para falha do provedor, aqui cai no local com a razão registrada.

## Prova

| O quê | Nível | Onde |
|---|---|---|
| Política, portão de segredo, caminho sensível, orçamento, cache, métricas, fusão local, híbrido, serviço e modos | `simulated` | `backend/tests/test_context_retrieval_core.py` |
| Workspace, léxico (ripgrep e Python), BM25, mapa, chunker | `simulated` | `backend/tests/test_context_retrieval_local.py` |
| Provedores falso e Jev (transporte simulado), `SemanticRetriever` A/B | `simulated` | `backend/tests/test_context_retrieval_semantic.py` |
| Ponta a ponta num mini-repositório, API de status, CLI, nenhuma rede | `simulated` | `backend/tests/test_context_retrieval_integration.py` |
| Índice BM25 persistente (chave, corrupção, poda, higiene) e lote | `simulated` | `backend/tests/test_context_retrieval_bm25_cache.py` |
| Orçamento de sessão em lote, modo sombra, híbrido EXACT/SEMANTIC/MIXED, cache x política | `simulated` | `backend/tests/test_context_retrieval_hardening.py` |
| Regra híbrida nova x regra v1 do piloto (30 perguntas públicas) | `simulated` | `backend/tests/test_context_retrieval_pilot_regression.py` |
| Integração com o gerador de pacotes (saída idêntica com a flag desligada) | `simulated` | `scripts/tests/test_pacotes_contexto.py` |
| Chamada real ao Jev | `not_run` | `REAL_JEV_NETWORK_CALLS = 0`; sem autorização para código privado |

## Limites conhecidos

- **Primeira consulta de uma revisão nova** paga a construção do índice (alguns segundos no repositório inteiro, 1,4 mil arquivos).
  Reconstrução incremental por arquivo (só o que mudou) fica para depois: hoje qualquer edição muda a revisão e refaz o índice.
- **`rg`**: não é requisito. Descoberta em ordem: `context_retrieval.lexical.ripgrep_path`, variável `RIPGREP_PATH`, PATH
  (`shutil.which`, que no Windows aplica o PATHEXT: acha `rg.exe` e `rg.cmd`). Nenhum caminho de instalação é presumido, e candidato que não
  responde `--version` como ripgrep é descartado. Sem `rg`, o motor é o Python, com o mesmo ranking (testado).
- A revisão não percebe edição que preserva tamanho e data de modificação (o mesmo limite do `git status`).

## Próximas fatias

Índice BM25 incremental por arquivo; tela "Context Retrieval" no painel (o contrato da API está pronto); `shadow` em repositório público para medir qualidade e
custo reais; outros `SemanticProvider` (embeddings locais, Ollama, banco vetorial); consumo do `ContextPack` por skills,
planejamento e subagentes.

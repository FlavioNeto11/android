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
  Remoto (matriz): **privado NEGADO; sintético NEGADO; público só com `allow_public: true` E prova independente de que o
  remoto real é público, de que o HEAD local existe nele e de que o worktree está LIMPO**. Sem prova (privado, desconhecido, erro
  de rede, remoto ausente ou não suportado, HEAD não publicado, worktree sujo) a política falha FECHADA: nenhum mapa, nenhum chunk, nenhuma chamada. FAKE e LOCAL não sofrem o bloqueio de classe nem a verificação.
  `synthetic` é útil para fixtures e testes (com provedor FAKE ou LOCAL); **não** constitui autorização para enviar código
  remoto.
- `PRIVATE_CODE_SEND_APPROVED = False` e `SYNTHETIC_REMOTE_SEND_APPROVED = False` são constantes de **código**. Mudar é decisão do
  dono, com ADR. Nenhum YAML liga.
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
- **Reconstrução incremental por arquivo (J5)**: o índice guarda o digest (blake2b de 128 bits) do texto de cada arquivo. Quando a revisão muda e há um índice anterior COMPATÍVEL (o da memória, ou o mais recente em disco desta raiz: mesmos `INDEX_VERSION`, `RETRIEVAL_VERSION`, corpus e raiz, só a revisão difere), os arquivos de texto igual reaproveitam as contagens do índice anterior e só os alterados ou novos são higienizados e tokenizados; apagado some. O resultado é IDÊNTICO ao do índice cheio (mesmos documentos na mesma ordem, mesmos postings, tamanhos e média, logo a mesma pontuação): os testes comparam o índice incremental com o cheio depois de editar, acrescentar, apagar, renomear, esvaziar, só mexer no mtime e em cadeia. Semente de outro corpus, versão ou raiz, ou corrompida (lixo, truncada, sem digests, postings fora da faixa), nunca é usada: cai na construção completa. Renomear conta como arquivo novo (o caminho entra na pontuação). `INDEX_VERSION` subiu para 3 (o arquivo ganhou `digests`), o que invalida os índices em disco antigos uma vez.
- **Seguro por construção**: gravação atômica (`.tmp` + `os.replace`); arquivo corrompido, truncado, adulterado ou com id de
  documento fora da faixa é miss silencioso e o índice é refeito; revisão nova gera outro arquivo e a antiga nunca é carregada; a
  limpeza mantém os 3 mais recentes por raiz.
- **O que vai para o disco**: só vocabulário e contagens (token → documento, frequência), nunca texto. Arquivo de caminho sensível
  nem entra no universo; linha com formato de credencial (duro, ou literal atribuído a nome de segredo) é descartada antes de
  tokenizar, e token alfanumérico longo com cara de chave ou hash não vira termo.
- **Lote**: `service.session()` (ou `gather_many`) congela a revisão e o universo e guarda o texto lido durante o lote (teto de 96 MB),
  de modo que Workspace, revisão, índice, mapa, cache e orçamento de SESSÃO são os mesmos para todos os itens. O orçamento de sessão
  não reinicia por item: no lote o total de chamadas ao provedor nunca passa de `max_calls_per_session`.
- `plano-100-pacotes.py --contexto` usa a API Python com um serviço só (sem subprocesso por item). **Escopo padrão de código (J7):** a consulta do consumidor é restrita a `backend/app/`, `frontend/src/` e `scripts/`; `--contexto-escopo PREFIXO` (repetível) troca o padrão e `--contexto-sem-escopo` consulta o repositório inteiro. O padrão vale SÓ para este consumidor: o serviço, a CLI do módulo e a API continuam sem escopo por padrão.
- **Motor léxico sem `rg`**: o ranking usa contagens (aditivas por linha) sobre o texto inteiro em minúscula; as linhas casadas, que só
  servem às janelas, são levantadas apenas para os arquivos escolhidos. Com `rg` o resultado é o mesmo (teste de paridade com o
  `rg` real em `test_context_retrieval_local.py`). O `rg` é opcional: configurado (`context_retrieval.lexical.ripgrep_path`),
  `RIPGREP_PATH` ou PATH; nenhum caminho de instalação é presumido. Wrapper `.cmd`/`.bat` recebe lotes menores (cmd.exe corta a linha em 8191).
- Medida (`scripts/bench-context-retrieval.py`, plano-100 inteiro, 273 itens, `local_only`, máquina do ambiente central, 01/10):
  construção a frio do índice 5,8 s; carga a quente do disco 0,27 s; consulta mediana 292 ms (P95 706 ms, máx 1,0 s); índice 4,35 MB;
  273 acertos de cache BM25 e 0 erros; total 96,7 s contra ~1.550 s da linha de base de um subprocesso por item (**16x**).

## Limites conhecidos (revisão final do PR #18)

Registrados, não corrigidos nesta fatia; nenhum deles bloqueia o uso atual (CLI e scripts, uma thread só, desligado por padrão).
- **Uso por mais de uma thread**: `Workspace.pinned()` guarda a revisão congelada na instância, sem trava; duas sessões em
  threads diferentes podem deixá-la congelada. O orçamento (`BudgetLedger`) já é atômico. Antes de ligar o serviço a um
  endpoint, dar a cada thread o seu `Workspace` ou travar o `pinned`.
- **A verificação de visibilidade só conhece o GitHub.** Remoto de outro host (GitLab, servidor próprio) é `UNKNOWN` e fica
  bloqueado; suportar outro host exige outro verificador. A prova vale 15 min e a API anônima tem limite de uso (403 = `UNKNOWN`).
- **`query_fp` é sha256 de 12 hex sem sal**: serve para agrupar eventos, não é anônimo contra força bruta de pergunta curta.
- **Teto de sessão** vale pela vida do serviço e não zera; a estimativa de tokens (bytes/4) pode subestimar o Jev em até ~2x
  (o payload leva cada id duas vezes); o custo que conta é o que o provedor reporta.
- **Fail-open sem rastro**: falha inesperada do semântico vira `fallback_reason` sem traceback no log; só a categoria fica
  nas métricas.
- Poda do índice BM25 é por raiz (3 por raiz); o diretório não tem teto global.
- Fora do módulo: `test_instalacao_do_worker::test_o_instalador_windows_grava_a_versao_derivada_do_commit` falha em qualquer
  `git worktree` (inclusive da `main`): `worker-install.ps1` lê `.git\HEAD` como arquivo. Dívida separada.

## Prova de que o que sai é público

`repository_class: public` no YAML é só uma declaração. O envio a provedor REMOTE exige a prova independente de uma porta do
domínio, `RepositoryVisibilityVerifier` (`verify()` pode ir à rede; `peek()` nunca vai), que devolve um `RepositoryProof`
(`remote`, `head_public`, `worktree_clean`). A política não sabe como se prova: domínio = contrato, `adapters/github_visibility.py`
= git e GitHub, política = decisão. As TRÊS partes autorizam juntas (regra final do público remoto: **config `public` + `allow_public`
+ remoto verificado público + HEAD verificado público + worktree limpo**):

1. **Remoto público** (abaixo).
2. **Worktree limpo**: `git status --porcelain=v1 --untracked-files=all` sem NENHUMA linha (modificado, preparado, removido, renomeado,
   não rastreado). Não se decide arquivo por arquivo: não existe "alteração segura". Lido do git a CADA chamada, nunca de cache: sujar
   bloqueia na hora (`reason: repository_worktree_dirty`), sem rede e sem esperar TTL. Falha do git é `None` e bloqueia. Arquivo
   ignorado não conta, porque o workspace (`git ls-files -co --exclude-standard`) também não o lê. Por isso o diretório de dados do
   retrieval precisa ficar fora do worktree ou ser ignorado (o `data/` do repositório é).
3. **HEAD público**: `GET /repos/{dono}/{repo}/commits/{sha}` anônimo; só 200 com `sha` IGUAL ao pedido, em pelo menos um dos remotos
   já provados públicos (`reason: repository_head_not_public`). 404, 403, 5xx, redirecionamento, rede, resposta incoerente ou SHA
   ilegível bloqueiam. Branch, nome de remoto e clone limpo não bastam. A prova fica ligada à identidade canônica dos remotos MAIS o
   SHA: trocar o HEAD a invalida; limpar de novo o worktree reaproveita a prova do mesmo HEAD ainda vigente.

Implementação do remoto:
- Lê TODOS os remotos do git (`git remote -v`, que aplica `insteadOf`), canoniza cada um para `github.com/dono/repo` (https, ssh,
  `git@`, `git://`; credencial na URL é descartada) e pergunta ao GitHub, **anonimamente** (sem token, sem proxy do ambiente, sem
  seguir redirecionamento), `GET /repos/{dono}/{repo}`. `PUBLIC` só com 200, `private: false`, `visibility` pública se vier e o
  nome da resposta igual ao pedido, para todos os remotos.
- **`UNKNOWN` bloqueia**: remoto ausente, host que não é github.com, URL fora do formato, 404, 301, 403, 5xx, rede, TLS, tempo,
  corpo inesperado. Não presume público porque o clone funciona sem senha nem pelo nome.
- Só roda com provedor REMOTE e a configuração já pedindo `public` + `allow_public`. Desligado, `local_only`, FAKE e LOCAL: nenhuma
  rede e nenhum `git` (o verificador nem é construído). Privado e sintético não têm o que provar e continuam negados pela configuração.
- Cache curto da prova: só `PUBLIC`, TTL de 15 min, na memória e em `data/context_retrieval/visibility.json` (para o status ler
  sem rede); chave = identidade canônica do conjunto de remotos, então trocar o `origin` invalida. `UNKNOWN` nunca vira `PUBLIC` e
  não vai ao disco (só é lembrado por 60 s para um laço de pedidos não bater no GitHub a cada item).
- **Status** (`GET /api/context-retrieval/status`) não faz chamada externa (só o git local): `external_send` traz `configured_for_remote`,
  `visibility_verified` (= `remote_visibility_verified`), `visibility` (`public`, `private`, `unverified`, `not_applicable`),
  `remote_visibility_verified`, `head_public_verified`, `worktree_clean` (`true`, `false`, `null` se não se aplica ou o git não
  respondeu), `allowed` e `reason`. Com `public` no YAML e sem prova vigente: `allowed: false`, `reason:
  repository_visibility_unverified`; remoto provado e HEAD não: `repository_head_not_public`; HEAD provado e worktree sujo:
  `repository_worktree_dirty`, imediatamente.
- O smoke público (`python-poetry/poetry`) passa pela MESMA regra: `public` + `allow_public` + prova de visibilidade (duas idas à
  `api.github.com`: repositório e commit pinado, além das chamadas ao Jev); clone limpo no SHA pinado satisfaz as três partes. Não há atalho nem campo de configuração que dispense a verificação.
- Limites que restam: a conferência é por pedido (uma edição entre a decisão e a leitura dos arquivos, dentro do MESMO pedido, não é
  vista); e um commit que só existe numa
  rede de forks do repositório público conta como público (é o que o GitHub serve a qualquer anônimo).

## Mapa da etapa A e segredo

O mapa (`caminho`, linguagem, tamanho, símbolos, resumo da docstring, títulos de markdown) também é conteúdo derivado do
repositório. Nenhum segredo, duro ou mole, sai nele: a entrada que o contém é OMITIDA (sem trocar por marcador, que viraria termo de
ranking), tanto na construção do mapa (`repomap.py`: nem fica na memória nem no cache em disco) quanto na saída
(`SemanticRetriever._mapa_filtrado`, que confere cada entrada renderizada). Segredo duro numa entrada vinda de outra fonte de mapa
ainda derruba o pedido. Isto é só filtro de SAÍDA externa: BM25 e léxico locais continuam vendo o corpus permitido.

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
| Chamada real ao Jev, só em código PÚBLICO | `real` | 02/10/2026, máquina central, `python-poetry/poetry` @ `94b6e35`, commits `a07ff80` (rodada 1) e `a88d609` (rodada 2, por etapa); `scripts/context-retrieval-public-smoke.py` (rodada 1: `--run`; rodada 2: `--cases H13,H14 --max-calls 4 --run`). 11 de 12 chamadas, todas HTTP 200, 0 fallbacks, US$ 0,0043 + 0,0016 (~0,006). Passou pela MESMA política de produção (remoto público, HEAD público, worktree limpo), sem atalho. Detalhe abaixo |
| Qualidade do retrieval LOCAL neste repositório (40 commits, hit@k e MRR por modo) | `real` | 02/10/2026, central, `scripts/context-retrieval-local-eval.py --ate 40316ba2bf8a2523534ec3c87440b52aab892cf4 --n 40`; teste sem rede em `scripts/tests/test_context_retrieval_local_eval.py` (`simulated`). Tabela abaixo |
| Índice BM25 incremental: idêntico ao cheio e só reanalisa o que mudou | `simulated` | `backend/tests/test_context_retrieval_bm25_incremental.py` (22; mutação conferida) |
| Custo do índice antes e depois do incremental | `real` | 02/10/2026, central, `scripts/context-retrieval-incremental-bench.py`; tabela "Custo do índice" abaixo |
| Chamada real ao Jev em código PRIVADO (este repositório) | `not_run` | negado por constante de código (`PRIVATE_CODE_SEND_APPROVED = False`); sem autorização |

### Smoke real público (02/10/2026)

- **Escopo:** só `python-poetry/poetry` no SHA fixado `94b6e35b9091991887aa54feeb3771a86d3bd692` (clone público, `origin` público, worktree limpo); nenhum código deste repositório saiu. `TYPESAFE_API_KEY` configurada, valor em nenhum arquivo, log, PR ou doc.
- **Rodada 1 (`a07ff80`, 6 perguntas do holdout: H01, H02, H13, H14, H25, H26):** 8 chamadas de rede (7 mais 1 da repetição de cache), todas 200, 0 fallbacks locais, origem `hybrid` nos 6, hit@3 e hit@5 em 6/6, US$ 0,0043. O resumo ainda não separava as etapas.
- **Rodada 2 (`a88d609`, por etapa, `--cases H13,H14 --max-calls 4`, sem repetição de cache):** 3 chamadas, 200, 0 fallbacks, US$ 0,0016.

  | Caso | Etapa A | Etapa B | Chunks enviados | Observação |
  |---|---|---|---|---|
  | H14 | 1 chamada | 1 chamada | 21 | 195 arquivos no mapa |
  | H13 | 1 chamada | 0 | 0 | B bloqueada por `budget_exceeded` (`stage_b_reason`) |

- **Total acumulado: 11 de 12 chamadas.** Isto valida o adaptador e o pipeline, não é benchmark.
- **Leitura:** a etapa B só roda quando o payload cabe no teto de entrada por pedido (`max_input_tokens`, padrão 24.000) e a etapa A já consome cerca de 15,6k tokens; neste repositório a B é a exceção. É o comportamento esperado do orçamento, não bug. Que as 7 chamadas da rodada 1 foram 6 de A e 1 de B (só o H14, que gastou ~23,3k tokens) é inferência, não medição por etapa.

### Medição local neste repositório (02/10/2026, `real`, gratuita, sem rede)

**Efeito do escopo padrão no consumidor `plano-100-pacotes.py --contexto` (J7, `real`):** `scripts/context-retrieval-local-eval.py --consumidor --ate 40316ba2bf8a2523534ec3c87440b52aab892cf4 --n 40` (mesmo conjunto de 40 commits; só o modo `hybrid_local`, que é o `local_only` do produto, com a pergunta cortada em 600 caracteres como o consumidor faz; 224 s):

| Consumidor | hit@3 | hit@5 | MRR@10 | recall@5 |
|---|---|---|---|---|
| antes: sem escopo | 22,5% | 37,5% | 0,195 | 24,0% |
| depois: escopo de código | **70,0%** | **77,5%** | **0,555** | 52,6% |

A diferença contra a tabela completa abaixo (17,5% e 72,5%) vem de cortar a pergunta em 600 caracteres. Os mesmos limites da medição valem (n = 40, pergunta é mensagem de commit, gabarito só de código, então parte do ganho é por construção: um doc relevante conta como erro).

- **Como:** `scripts/context-retrieval-local-eval.py --ate 40316ba2bf8a2523534ec3c87440b52aab892cf4 --n 40` (máquina central, Windows, ripgrep presente; 374 s no total).
  Conjunto: os 40 commits `feat`/`fix` mais recentes sem merge até esse SHA, com 1 a 8 arquivos de código modificados ou apagados. A pergunta é o assunto
  (sem `tipo(escopo):`) mais o corpo do commit, sem `[skip ci]` e trailers; o gabarito são esses arquivos de código (docs, testes, `CHANGELOG`, `.claude/` e arquivo novo ficam de fora).
  O índice é o do estado do PAI do commit (worktree descartável em `--detach <pai>`), então a resposta não vaza. Teste do anti-vazamento: `scripts/tests/test_context_retrieval_local_eval.py` (com o commit no lugar do pai, ele falha).
- **Resultado** (n = 40 em cada linha, 0 erros; top-10 por pedido; só mede, nada foi alterado):

  | Variante da pergunta | Modo | hit@3 | hit@5 | MRR@10 | recall@5 | latência mediana |
  |---|---|---|---|---|---|---|
  | completa, repositório inteiro | lexical | 12,5% | 22,5% | 0,134 | 15,1% | 670 ms |
  | completa, repositório inteiro | bm25 | 30,0% | 40,0% | 0,213 | 26,3% | 5.533 ms (inclui construir o índice) |
  | completa, repositório inteiro | hybrid_local | 17,5% | 35,0% | 0,182 | 23,8% | 423 ms |
  | só o assunto | lexical | 10,0% | 17,5% | 0,096 | 13,8% | 324 ms |
  | só o assunto | bm25 | 32,5% | 47,5% | 0,238 | 28,8% | 46 ms |
  | só o assunto | hybrid_local | 30,0% | 47,5% | 0,229 | 29,0% | 373 ms |
  | completa, escopo de código | lexical | 37,5% | 45,0% | 0,321 | 25,7% | 116 ms |
  | completa, escopo de código | bm25 | **82,5%** | **87,5%** | **0,652** | 60,0% | 44 ms |
  | completa, escopo de código | hybrid_local | 72,5% | 80,0% | 0,559 | 51,0% | 194 ms |

  O escopo de código é `backend/app/`, `frontend/src/` e `scripts/`. Índice BM25: 1.339 arquivos no universo, primeira consulta de cada revisão 5,5 s de mediana (máx. 6,1 s); depois, dezenas de ms.
- **Leitura (o que os números dizem):**
  1. **O maior efeito é o escopo, não o modo:** restringir ao código leva o BM25 de 30% para 82,5% de hit@3. A documentação (1,3 mil arquivos, boa parte `docs/` e `.claude/`) ocupa o topo quando a pergunta fala do mesmo assunto. Como o gabarito é só código, parte dessa diferença é por construção: um doc relevante conta como erro aqui.
  2. **O BM25 vence o léxico** em todas as variantes (o léxico exige o nome exato, e a mensagem de commit quase nunca o traz inteiro).
  3. **O `hybrid_local` perde para o BM25 puro neste conjunto:** 72,5% contra 82,5% de hit@3 no escopo de código; em 4 dos 40 casos o híbrido erra o hit@3 onde o BM25 acerta e em nenhum acontece o contrário. A regra de fusão v1 dá a vez ao léxico quando a pergunta cita um identificador, e aqui isso piorou.
  4. A primeira consulta de uma revisão custa ~5,5 s (construir o índice); é o gargalo medido que motiva o índice incremental (J5).
- **Limites da medição:** a pergunta é a mensagem de commit, que costuma citar o símbolo ou o arquivo, então os números são um teto para o léxico e não um benchmark de perguntas abertas; 19 dos 40 commits são de frontend (a UX recente), e o conjunto inclui os commits do próprio retrieval; n = 40 dá intervalo largo (±12 pontos); só local, então nada diz do semântico (ver o smoke do Jev, que mede outra coisa).
- **Propostas (NÃO aplicadas; exigem decisão e nova medição):** (a) escopo padrão de código, ou peso menor para `docs/`, `.claude/` e `CHANGELOG`, no pedido de tarefa de código; (b) rever a regra "o léxico manda quando há identificador" (a salvaguarda v1) ou medir um híbrido que parta do BM25 e use o léxico só para promover o arquivo que contém o identificador; (c) repetir a medição com perguntas que não sejam mensagem de commit (por exemplo, as do holdout público do piloto) antes de tirar conclusão sobre o modo.

### Custo do índice, antes e depois do incremental (02/10/2026, `real`, sem rede)

- **Como:** `scripts/context-retrieval-incremental-bench.py --backend <backend> --repo <worktree>` num worktree descartável do repositório (1.415 arquivos no índice; 3 repetições; máquina central), o MESMO script contra o código da `main` (`0da61af`, antes) e o do J5 (depois). Tempo da primeira consulta depois da mudança:

  | Cenário | Antes (mediana) | Depois (mediana) | Arquivos reaproveitados / analisados (depois) |
  |---|---|---|---|
  | a frio, sem nenhum índice | 13,3 s | 12,5 s (igual; o hash dos textos custa poucos ms) | 0 / 1.415 |
  | um arquivo editado, mesmo processo | 5,3 s | **1,3 s** | 1.414 / 1 (semente na memória) |
  | outra edição, processo novo (índice só em disco) | 5,3 s | **1,6 s** | 1.414 / 1 (semente em disco) |
  | trocar para `HEAD~10`, mesmo processo | 5,0 s | **1,7 s** | 1.340 / 23 (e 52 arquivos a menos) |

  Os ~1,3 s que sobram, pelo perfil (só a leitura dos textos foi medida: ~0,3 s), são ler e hashear todos os textos, consultar a árvore do git (~0,2 s), inverter os postings e remontar a lista; esta parte final não foi medida em separado. Remontar só o que mudou (postings incrementais) seria o passo seguinte, mas exigiria renumerar documentos; não foi feito.
- **Limites:** mede a primeira consulta de cada cenário, não a vazão; a frio inclui o custo de um worktree recém-criado; o ganho vale quando existe índice anterior compatível (memória ou disco), e não quando o cache em disco está desligado e o processo é novo.

### Proposta: orçamento da etapa B (J6, 02/10/2026; só proposta, nada foi alterado nem habilitado)

**Problema.** Com `max_input_tokens = 24.000` por pedido e a etapa A em ~15,6 mil tokens, a etapa B quase nunca roda.

- **A regra (código):** `BudgetLedger._reservar` barra a chamada quando `tokens já gastos no pedido + estimativa da chamada > max_input_tokens`, e a estimativa é `len(payload) // 4` sobre o texto enviado.
  Depois da A (tokens REAIS informados pelo provedor), a B só passa se a estimativa dela for menor ou igual a `24.000 − gasto da A`, ou seja, cerca de **8,3 mil tokens, ~33 KB de payload**.
  O teto de payload da B, porém, é `max_bytes = 48.000` (~12 mil tokens estimados). Os padrões se contradizem: a A (~15,6 mil) mais a B no teto de payload (~12 mil estimados) dá mais de 27 mil, acima de 24 mil.
- **Medido (smoke real de 02/10, `python-poetry/poetry` @ `94b6e35`):** nos casos em que só a etapa A rodou (H01, H02, H13, H25 e H26) ela gastou 15.652 a 15.663 tokens, US$ 0,000657 a 0,000658. O H14 foi o único a rodar a B (21 chunks, 2 chamadas): 23.283 tokens no total, US$ 0,000978. A divisão A/B do H14 é INFERIDA por subtração (o resumo só tem o total do pedido): a B teria custado ~7,6 mil tokens e US$ 0,000321. O H13 teve a B bloqueada por `budget_exceeded`.
  Preço implícito nos dados do provedor: ~US$ 4,2 por 100 milhões de tokens de entrada (observado em A e em B). Observado, não contratual.
- **A estimativa subestima a A:** o mapa da A é cortado em `max_bytes = 48.000`, que estima ≤ 12 mil tokens, e o provedor contou 15,6 mil: a razão real é de pelo menos 1,3 vez. É um segundo motivo para o teto de 24 mil ser curto.
- **O dinheiro não é o limite:** US$ 0,001 por pergunta com A e B, contra um teto de US$ 0,05 por pedido (50 vezes). O que trava é o teto de TOKENS, e ele foi posto por prudência, não por custo.

**Opções** (custo estimado por pergunta com os números acima; estimativas, não medições; "B" abaixo = a etapa B chamada):

| Opção | Mudança | Tokens por pergunta (A + B) | Custo por pergunta | Risco / o que falta medir |
|---|---|---|---|---|
| 0. Hoje | nenhuma | A 15,7 mil; B só se o payload couber em ~33 KB | US$ 0,00066 (só A) a 0,00098 (A + B) | B quase nunca roda; regiões só em casos de payload pequeno |
| 1. Teto único maior | `max_input_tokens` 24.000 → 32.000 | até ~31 mil no pior caso (A 15,7 + B no teto de payload, que não foi medido em tokens reais) | até ~US$ 0,0013 | simples; o pior caso de B é limite superior, não medido; a B passa a rodar sempre |
| 2. Teto por etapa | `max_input_tokens_a` ~20.000 e `max_input_tokens_b` ~16.000, em vez de um teto por pedido | igual à opção 1 no pior caso | até ~US$ 0,0015 | explícito e sem a contradição entre padrões; é a que melhor documenta a intenção; muda o contrato do orçamento e da configuração |
| 3. Compactar o mapa da A | menos símbolos e resumos por entrada, alvo ~8 mil tokens | A ~8 mil + B 7,6 mil a ~15 mil = 16 a 23 mil | US$ 0,00067 a 0,00097 (MENOR que hoje com B) | risco de qualidade da A (menos informação por arquivo): só decidir depois de medir hit@k no holdout |
| 4. Menos candidatos | `max_candidate_files` 8 → 5 e `max_chunks` 24 → 12 | B cai pela metade (~3,8 mil): A + B ~19,5 mil, cabe no teto atual | ~US$ 0,00082 | menos recall potencial na B; é só configuração, sem código |

**Recomendação (de quem escreveu; a decisão é do dono):** primeiro medir se a B vale o gasto, depois ajustar o orçamento.
1. **Valor da B não está medido.** No smoke, os 6 casos acertaram o arquivo (hit@3 e hit@5 em 6/6) com a B rodando em 1 só, e o H13 acertou com a B bloqueada. A B entrega REGIÕES (faixas de linha), não arquivos, então o ganho dela é custo de leitura a jusante, que o hit@k por arquivo não vê. Falta uma ablação A versus A + B, com métrica de região, antes de pagar para destravá-la.
2. **Se a B for desejada:** a opção 2 (teto por etapa) é a mais honesta, e a 4 é a mais barata de testar, porque é só configuração. A opção 3 só depois de uma medição de qualidade da A.
3. **Verificar a cobertura da A antes de qualquer coisa:** `files_considered = 195` nos casos H13 e H14 com `max_map_files = 400`. Se o escopo `src/poetry/` tem mais de 195 arquivos, o corte em bytes deixa a A sem ver o resto. Conferir contando os arquivos do escopo no clone público no SHA fixado (não feito aqui: exigiria baixar o repositório de novo).
4. **Próxima medição paga (precisa de autorização do dono):** rodada pequena no `python-poetry/poetry` (no máximo 6 perguntas, dentro do teto de 12 chamadas por execução e com `--cases`/`--max-calls`) com a opção escolhida, registrando por etapa.

## Limites conhecidos

- **Primeira consulta de uma revisão nova** paga a construção do índice. Sem índice nenhum (a frio), são vários segundos no repositório inteiro (1,4 mil arquivos). Com um índice anterior compatível, a reconstrução é **incremental por arquivo** (abaixo) e custa cerca de 1,3 a 1,7 s.
- **`rg`**: não é requisito. Descoberta em ordem: `context_retrieval.lexical.ripgrep_path`, variável `RIPGREP_PATH`, PATH
  (`shutil.which`, que no Windows aplica o PATHEXT: acha `rg.exe` e `rg.cmd`). Nenhum caminho de instalação é presumido, e candidato que não
  responde `--version` como ripgrep é descartado. Sem `rg`, o motor é o Python, com o mesmo ranking (testado).
- A revisão não percebe edição que preserva tamanho e data de modificação (o mesmo limite do `git status`).
- **Dívidas para HABILITAR o uso remoto** (nenhuma bloqueou o merge do PR #18; a feature vem desligada por padrão):
  1. **Teto `max_input_tokens`** (24.000 por pedido): com a etapa A em ~15,6k, a B quase nunca roda. Decidir o teto, ou a divisão por etapa, antes de ligar (opções, números e recomendação na seção "Proposta: orçamento da etapa B", acima).
  2. ~~Rótulo `cache_b: miss` também saía quando a B era bloqueada pelo orçamento~~ **Fechada (J3a):** o rótulo só vira `miss` depois de `check_call` aprovar a chamada; bloqueada pelo orçamento fica `skipped`, com `stage_b_reason`.
  3. **Checagem por pedido**: a prova de proveniência é refeita a cada pedido, sem monitoramento contínuo.
  4. ~~`assume-unchanged` / `skip-worktree` escondiam alterações do `git status`~~ **Fechada (J3b):** o gate lê também `git ls-files -v -z` e trata qualquer arquivo marcado (minúscula = `assume-unchanged`, `S` = `skip-worktree`) como worktree sujo, mesmo sem alteração (fail closed); `ls-files` que falha vale `None`. Efeito colateral aceito: um sparse-checkout, que usa `skip-worktree`, também bloqueia o envio remoto.
  5. **Rede de forks**: não há prova de que o commit também não vive só num fork privado.
  6. **Só GitHub**: outro host de remoto é negado.
  7. **Data dir dentro do worktree** pode sujá-lo sozinho.

## Próximas fatias

Tela "Context Retrieval" no painel (o contrato da API está pronto); `shadow` em repositório público para medir qualidade e
custo reais; outros `SemanticProvider` (embeddings locais, Ollama, banco vetorial); consumo do `ContextPack` por skills,
planejamento e subagentes.

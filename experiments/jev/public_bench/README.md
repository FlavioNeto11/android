# Benchmark PÚBLICO de recuperação de contexto (Scrapy 2.19.0) — executado UMA vez em 2026-10-01 (NO_GO mecânico)

Corpus: [`scrapy/scrapy`](https://github.com/scrapy/scrapy), **BSD-3-Clause**, tag `2.19.0`, commit
`8026deeaac371a5d9a3edbe4886d58f61139d464` (2026-09-10). Mesmo contrato do piloto privado (métricas, entrega, limiares T1–T8),
sem código do repositório privado: aqui o que sairia da máquina é código aberto.

| Arquivo | Papel |
|---|---|
| `golden.json` | 20 perguntas (8 EXACT, 12 SEMANTIC) com gabarito **manual**, `source_sha` fixado, regras de grupo |
| `thresholds.json` | limiares pré-registrados + especificação das variantes `jev_rerank` (controle) e `jev_map` (principal) |
| `prepare.py` | `git clone --depth 1 --branch 2.19.0` para `data/jev-pilot/public/scrapy` (ignorado pelo Git) e conferência do SHA |

## Como preparar e medir sem Jev (custo zero, sem chave)

```bash
python experiments/jev/public_bench/prepare.py
python experiments/jev/benchmark.py --golden experiments/jev/public_bench/golden.json \
       --corpus-root data/jev-pilot/public/scrapy                 # baselines ripgrep + BM25
python experiments/jev/benchmark.py --golden experiments/jev/public_bench/golden.json \
       --corpus-root data/jev-pilot/public/scrapy --estimate-only  # requisições, bytes e custo previstos
python experiments/jev/benchmark.py --golden experiments/jev/public_bench/golden.json \
       --corpus-root data/jev-pilot/public/scrapy --provider fake  # valida o harness (não mede o Jev)
```

## Travas

- `benchmark.PUBLIC_BENCHMARK_AUTHORIZED = False` (voltou a False depois da única rodada autorizada): `--provider jev` com
  `--corpus-root` aborta com `BLOCKED_AUTHORIZATION` mesmo com chave. Só muda num commit próprio, citando autorização explícita do dono.
- Resultado e limiares aplicados: `docs/research/jev-pilot.md` §37.8.
- `verify_public_checkout`: o corpus só roda se for um repositório Git **próprio**, com a origem e o SHA fixados e nenhum arquivo
  rastreado modificado. O repositório privado nunca passa por aqui: sem `--corpus-root` o caminho é o privado (`BLOCKED_PRIVACY`).
- Um golden `visibility=public` sem `--corpus-root` é recusado.

## Grupos (regras mecânicas, verificadas por teste)

- **EXACT** (`EXACT_*`, `grep_friendly`): a pergunta traz entre crases um literal que aparece verbatim numa região esperada.
- **SEMANTIC** (`SEMANTIC_*`, `semantic_only`): a pergunta descreve o comportamento e nenhum token seu (fora stopwords e palavras
  onipresentes do domínio) é igual a um token dos nomes de função/classe esperados.

## Achado que moldou o desenho (medido antes de qualquer chamada Jev)

O shortlist BM25 de 30 trechos contém o arquivo esperado em 100 % das perguntas EXACT e em **0 %** das SEMANTIC. Logo a
variante `jev_rerank` (BM25 → Jev) não consegue ganhar nas SEMANTIC por construção; ela fica como controle e a variante
`jev_map` (mapa do repositório → Jev escolhe arquivos → Jev escolhe trechos) é a que decide o veredito. Ver `thresholds.json`.

## O que um resultado deste benchmark NÃO prova

Valor de recuperação **neste corpus público em inglês**. Não prova ganho no código privado (português, outro estilo), nem
adoção do Jev. O código privado segue `BLOCKED_PRIVACY` (retenção padrão da API: UNKNOWN).

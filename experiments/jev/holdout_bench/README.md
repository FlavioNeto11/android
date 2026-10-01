# Holdout CONFIRMATÓRIO do híbrido (Poetry 2.5.1) — PREPARADO, NÃO EXECUTADO

Corpus: [`python-poetry/poetry`](https://github.com/python-poetry/poetry), **MIT**, tag `2.5.1`, commit
`94b6e35b9091991887aa54feeb3771a86d3bd692` (2026-09-20). Pacote de produção `src/poetry/` (193 arquivos `.py`, 174 não vazios no
mapa). Corpus diferente do Scrapy, sem nenhuma pergunta do Scrapy. O experimento original do Scrapy segue `NO_GO` (não é revisto
aqui); isto testa a hipótese pós-hoc **híbrido = salvaguarda lexical + `jev_map`** (regra v1, hash em `../hybrid_rule.lock.json`).

| Arquivo | Papel |
|---|---|
| `golden.json` | 30 perguntas: 12 EXACT, 12 SEMANTIC, 6 MIXED; gabarito **manual**, SHA fixado, regras de grupo |
| `thresholds.json` | critérios H1–H9 pré-registrados (limiares em número de itens) e a especificação do pipeline |
| `freeze.json` / `freeze.py` | hashes de tudo que não pode mudar depois da 1ª chamada real (`freeze.py --check`) |
| `prepare.py` | `git clone --depth 1 --branch 2.5.1` para `data/jev-pilot/public/poetry` (ignorado pelo Git) e conferência do SHA |

## Sem Jev e sem chave (custo zero)

```bash
python experiments/jev/holdout_bench/prepare.py
python experiments/jev/holdout_bench/freeze.py --check
python experiments/jev/benchmark.py --golden experiments/jev/holdout_bench/golden.json \
       --corpus-root data/jev-pilot/public/poetry                 # baselines ripgrep + BM25
python experiments/jev/benchmark.py --golden experiments/jev/holdout_bench/golden.json \
       --corpus-root data/jev-pilot/public/poetry --estimate-only  # 60 requisições, bytes e custo previstos
python experiments/jev/benchmark.py --golden experiments/jev/holdout_bench/golden.json \
       --corpus-root data/jev-pilot/public/poetry --provider fake  # valida o harness (não mede o Jev)
```

## Travas

- `benchmark.PUBLIC_BENCHMARK_AUTHORIZED = False`: a mesma trava do Scrapy. `--provider jev` com este golden aborta com
  `BLOCKED_AUTHORIZATION` mesmo com chave. Só muda num commit próprio, citando autorização explícita do dono.
- Orçamento do arquivo: **60 requisições** (2 por pergunta, só `jev_map`; o híbrido reaproveita a mesma resposta, 0 requisições
  novas); `max_retries = 0`; o CLI nunca passa de 60. `jev_rerank` não roda neste holdout.
- `verify_public_checkout`: repositório Git próprio, origem e SHA fixados, árvore limpa, LICENSE (assinatura MIT em
  `meta.license_signature`), nenhum symlink escapando.
- Um PASS **não** autoriza código privado: `PRIVATE_CODE_SEND_APPROVED = False`, `PRIVATE_CODE_BENCHMARK_STATUS = BLOCKED_PRIVACY`;
  a retenção da API (ZDR/DPA) é outro portão, separado.

## Grupos (regras mecânicas, verificadas por teste)

- **EXACT** (`EXACT_*`): a pergunta traz entre crases um literal que aparece verbatim numa região esperada.
- **SEMANTIC** (`SEMANTIC_*`): sem identificador explícito pela regra do híbrido (a salvaguarda **não** dispara) e nenhum token
  da pergunta, fora stopwords e palavras onipresentes, igual a um token dos símbolos esperados.
- **MIXED** (`MIXED_*`): a pergunta tem identificador explícito, o ripgrep o acha e o melhor arquivo lexical **não** está em
  `expected_files`. É o caso em que a salvaguarda pode piorar o resultado; foi construído para estressar a regra.

## O que um resultado deste benchmark NÃO prova

Valor de recuperação neste corpus público em inglês, com um avaliador único, 30 perguntas e possível memorização do Poetry pelo
modelo (ver `meta.contamination_note`). Não prova ganho no código privado (português, outro estilo) nem adoção do Jev.

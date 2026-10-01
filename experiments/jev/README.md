# experiments/jev — piloto isolado do Jev (TypeSafe AI)

Laboratório **independente do produto**. Objetivo: medir, com gabarito próprio e limiares escritos antes, se o Jev
([docs.typesafe.ai](https://docs.typesafe.ai)) ajuda (A) a recuperar contexto de código para o Claude Code e (B) a rotear
decisões do runtime. Relatório e fontes: [`docs/research/jev-pilot.md`](../../docs/research/jev-pilot.md).

## Isolamento

- Só stdlib. **Nada** de `backend/` é importado; **nada** do runtime importa isto. Sem migração, sem dependência nova, sem hook.
- **Nenhuma chamada real por padrão.** O comando padrão roda só baselines (ripgrep, BM25) dentro de `no_network()`.
- O provedor real (`provider.RealJevProvider`) é **inerte**: exige `enabled=True` **e** `TYPESAFE_API_KEY` no ambiente.
  A chave nunca é argumento, log, exceção, relatório nem arquivo.
- Resultados detalhados vão para `data/jev-pilot/` (ignorado pelo Git). Em chat/PR só resumos.
- Remover: apagar `experiments/jev/` e `docs/research/jev-pilot.md`.

## Rodar (a partir da raiz do repositório; use um Python 3.11+, ex. o venv do backend)

```bash
# 1) Testes do experimento (sem rede, sem chave)
python -m pytest experiments/jev/tests -q

# 2) Baselines (ripgrep + BM25) sobre o golden — o padrão, custo zero
python experiments/jev/benchmark.py

# 3) Harness com o provedor FALSO (modos: HIGH_CONFIDENCE LOW_CONFIDENCE TIMEOUT ERROR INVALID_RESPONSE UNKNOWN_CHOICE)
python experiments/jev/benchmark.py --provider fake --fake-mode LOW_CONFIDENCE

# 4) Estimar o custo de uma rodada Jev SEM rede
python experiments/jev/benchmark.py --estimate-only

# 5) Piloto B: extrair atributos seguros do histórico (SQLite aberto somente-leitura) e avaliar roteadores triviais
python experiments/jev/replay.py --db C:/git/android/data/poc.sqlite3
```

O relatório (`results.json`, `verdict.json`, `report.md`) sai em `data/jev-pilot/<timestamp>-<provedor>/`. Sem `rg` no PATH o
benchmark usa o binário embutido do Claude Code (`ARGV0=rg`), `JEV_RG_BIN`, ou um equivalente em Python.

## O que cada arquivo faz

| Arquivo | Papel |
|---|---|
| `golden.json` | 18 perguntas sobre o commit `3eba639`, gabarito **manual** (não gerado por Jev). Congelado. |
| `thresholds.json` | limiares GO/NO-GO **antes** de qualquer resultado real + especificação do pipeline |
| `benchmark.py` | CLI; BM25 → rerank; entrega com travas; `--estimate-only` |
| `baselines.py`, `corpus.py`, `textutil.py` | ripgrep, BM25 por função/método, tokenização, escopo `app/tests` |
| `provider.py` | interface `DecisionProvider`, formato de fio oficial, `RealJevProvider` inerte |
| `fake_provider.py` | provedor falso (mesmo formato de fio); **não mede o Jev** |
| `redact.py` | redação e bloqueio de segredo antes de qualquer envio |
| `metrics.py`, `report.py` | métricas (bytes = `PROXY_METRIC`) e avaliação mecânica dos limiares |
| `netguard.py` | trava de rede usada pelo benchmark offline |
| `smoke.py`, `synthetic_corpus/` | smoke sintético (protocolo/erro/latência/custo) sem código do projeto; estimativa offline; rodada real travada |
| `replay.py` | Piloto B: lista permitida de colunas, rótulos derivados, roteadores de base |
| `public_bench/`, `repomap.py` | benchmark PÚBLICO (Scrapy 2.19.0, BSD-3, SHA fixado): golden de 20 perguntas, limiares, `prepare.py`; **não executado com o Jev**, travado (`PUBLIC_BENCHMARK_AUTHORIZED = False`). Ver `public_bench/README.md` |

## Estado atual: BLOCKED_PRIVACY e smoke sintético travado

- `--provider jev` sobre o **código real** aborta com `BLOCKED_PRIVACY` (`benchmark.PRIVATE_CODE_SEND_APPROVED = False`): o
  repositório é privado e a retenção padrão da API é UNKNOWN (relatório §35).
- `python experiments/jev/smoke.py` **só estima** (sem rede). A rodada real do smoke (6 chamadas, corpus
  `synthetic_corpus/`, nada do repositório) exige `--run --confirm-synthetic-only`, `TYPESAFE_API_KEY` **e**
  `smoke.SMOKE_RUN_AUTHORIZED = True`, que só muda após autorização explícita do dono.

## Como habilitar o provedor real (FUTURO — só com aprovação do dono)

Pré-requisitos (ver §34 do relatório): aprovação para enviar trechos de código à TypeSafe (EUA), DPA lido, chave de uma conta
com acesso, exposta **só** por variável de ambiente:

```bash
# PowerShell:  $env:TYPESAFE_API_KEY = "<chave>"      (nunca no chat, no repositório ou em arquivo)
python experiments/jev/benchmark.py --provider jev --confirm-external-send --max-calls 40
```

Sem `--confirm-external-send` ou sem a chave o comando aborta **antes** de qualquer requisição. Modelo fixado em `jev-1.13.0`
(`jev-latest` muda sem aviso). Chunks com achado duro de segredo nunca entram no payload.

## Regras do experimento

- Golden e limiares **não** se ajustam depois de ver resultado do Jev (`THRESHOLD_CHANGED_AFTER_RESULTS = NO`). Mudança exige
  nova versão e registro no relatório.
- Métricas em bytes são `PROXY_METRIC`; nunca chamar de tokens.
- Prova: este piloto é `simulated`/`MEASURED-offline` até a primeira rodada autorizada; chamadas reais ao Jev = `not_run`.

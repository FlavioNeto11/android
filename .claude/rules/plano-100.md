---
paths:
  - "docs/plano-100.md"
  - ".claude/plano-100.json"
  - ".claude/plano-100/**"
---

# Plano-100

- **Linha de item exige 4 colunas:** `| id | o que | achados | P/M/G/— |` — sem elas o pacote e o relatório
  quebram.
- **Cabeçalho de fase:** `### Fase N — nome ·` (com travessão), formato que `plano-100-pacotes.py::FASE` casa.
- **Todo ID novo entra num bloco (`batches[].items`) de `.claude/plano-100.json`**, senão `claude-plan-100.py
  carregar()` e `docs-check.py` recusam. Depois de editar o mapa, regenere os pacotes **sem** `--fila`:
  `python scripts/plano-100-pacotes.py`.
- **`.claude/plano-100/estado.json` e `docs/execucao-plano-100-runner.md` só mudam por `aplicar`/`relatorio`** —
  nunca editar os dois à mão.

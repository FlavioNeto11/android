---
paths:
  - "backend/migrations/**"
  - "backend/app/db.py"
---

# Migrações

- **Migração já aplicada não se edita.** Se o esquema mudou, cria-se a **próxima** numerada — mesmo para corrigir
  a anterior. Reescrever um arquivo já aplicado faz o schema do banco já migrado (o do ambiente central) divergir do que o mesmo arquivo geraria
  num banco novo, sem nada detectar (a 008 reescrita para Postgres divergiu da 008 já rodada no ambiente central).
- Nome: `NNN_descricao.sql`, três dígitos sequenciais. `docs/banco.md` deveria citar todo número —
  `scripts/docs-check.py` avisa quando falta.
- Diferença SQLite/PostgreSQL vai no **mesmo arquivo** (marca `{{PK_AUTO}}`/`{{BLOB}}` ou bloco
  `-- @dialect:postgres`), nunca em dois arquivos — ver ADR-003 em `docs/decisoes.md`.
- **Teste nos dois bancos** (SQLite padrão, PostgreSQL com `TEST_DATABASE_URL`), abrindo o banco pela fábrica
  configurada — nunca `Database(cfg.db_path)` direto, que roda em SQLite mesmo dentro de uma corrida "contra
  PostgreSQL" sem avisar.

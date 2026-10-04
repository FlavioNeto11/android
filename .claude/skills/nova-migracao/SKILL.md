---
name: nova-migracao
description: "Criar a próxima migração SQL numerada do backend, no padrão do projeto (dialeto no mesmo arquivo, citação em docs/banco.md, teste nos dois bancos)."
disable-model-invocation: true
argument-hint: "<descricao_em_snake_case>"
---

1. Leia `.claude/rules/migracoes.md` e o ADR-003 (`grep -n "ADR-003" docs/decisoes.md`).
2. O número NÃO se calcula: peça à orquestradora o número reservado para esta tarefa (o maior arquivo em `backend/migrations/` pode não ser o próximo livre, porque há reservas). Sem número reservado, pare e pergunte. Nome: `NNN_$ARGUMENTS.sql`, três dígitos. Nunca edite uma migração existente.
3. Escreva o SQL com as marcas de dialeto do projeto (`{{PK_AUTO}}`, `{{BLOB}}`, `-- @dialect:postgres`) num arquivo só.
4. Cite o número novo em `docs/banco.md`.
5. Rode o teste direcionado em SQLite e, se a mudança tocar tipo ou chave, em PostgreSQL (`TEST_DATABASE_URL`), abrindo o banco pela fábrica configurada.
6. Rode `python scripts/docs-check.py`. Registre a prova como real, simulada ou não executada.

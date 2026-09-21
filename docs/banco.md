# Banco: SQLite por padrão, PostgreSQL por configuração

O projeto nasceu com SQLite e **isso continua certo para quem roda tudo numa máquina**: arquivo único, zero
serviço, backup é copiar. PostgreSQL entra quando os componentes se separam — várias máquinas precisando do mesmo
estado.

A escolha é uma variável:

```bash
# SQLite (padrão): nada a configurar
# PostgreSQL:
DATABASE_URL=postgresql://usuario:senha@host:5432/parque
```

É o **primeiro endereço de serviço configurável do projeto** além do Appium. Até aqui não havia nenhum, e isso
sozinho já impedia separar qualquer coisa.

## O que o resto do código não precisa saber

Três decisões mantêm a diferença contida em `app/db.py`:

1. **A linha é um `dict`.** `sqlite3.Row` só era acessada por nome (conferido: índice numérico existia apenas
   dentro do `db.py`), e `dict` tem `.keys()`. Os dois drivers entregam a mesma coisa.
2. **O marcador continua `?`.** Quem escreve SQL usa `?`; a tradução para `%s` acontece num lugar só.
3. **As migrações são os mesmos arquivos.** O que difere vira marca (`{{PK_AUTO}}`, `{{BLOB}}`) ou bloco
   (`-- @dialect:postgres`). Um arquivo por migração, para os dois não divergirem com o tempo.

Erros de driver também são neutros: `INTEGRITY_ERRORS` e `OPERATIONAL_ERRORS` em vez de `sqlite3.IntegrityError`.
Importa porque a **idempotência** do projeto é chave `UNIQUE` + captura da violação — capturar a exceção errada
transformaria "já existe, devolva o original" em erro 500.

## Onde os bancos divergem de verdade

**Busca textual**, e só ela. SQLite tem FTS5 com `bm25()`; PostgreSQL tem `tsvector` com `ts_rank`. Fingir que é
a mesma coisa custaria mais que admitir:

| | SQLite | PostgreSQL |
|---|---|---|
| Índice | tabela virtual `memory_fts` + 3 gatilhos | coluna **gerada** `busca` + índice GIN |
| Consulta | `memory_fts MATCH ?` … `ORDER BY bm25()` | `busca @@ plainto_tsquery(?)` … `ORDER BY ts_rank DESC` |

No PostgreSQL a coluna gerada dispensa os gatilhos: o banco a mantém sozinho, e some a classe inteira de defeito
"o índice ficou fora de sincronia com a tabela". O ramo fica em `social/repository.search_memories` — um `if`
honesto, no único ponto onde a diferença existe.

## Construções que foram trocadas por portáteis

Não por preciosismo: cada uma quebraria no PostgreSQL.

| Era | Virou | Por quê |
|---|---|---|
| `cursor.lastrowid` | `db.inserted_id(...)` com `RETURNING` | `lastrowid` é do SQLite; `RETURNING` vale nos dois |
| `SUM(status='succeeded')` | `SUM(CASE WHEN … THEN 1 ELSE 0 END)` | comparação devolve boolean, e `SUM` recusa boolean |
| `MAX(attempts-1, 0)` | `CASE WHEN … END` | `MAX` escalar de 2 argumentos é do SQLite; `GREATEST` é do padrão |
| `IS NOT 'approval'` | `IS DISTINCT FROM 'approval'` | `IS NOT <literal>` é extensão do SQLite |
| `datetime('now', ?)` | corte calculado em Python | as colunas guardam ISO-8601, que ordena lexicograficamente |
| `UNIQUE COLLATE NOCASE` | índice único sobre `lower(...)` | `NOCASE` não existe no PostgreSQL |
| `executescript` | divisor de instruções próprio | é do SQLite — e o divisor precisa entender literal e corpo de gatilho |

## Rodar a suíte contra o PostgreSQL

Prova o aplicativo **inteiro** no outro banco, não só as peças conferidas à mão. Cada teste ganha um schema
próprio — isolamento equivalente ao arquivo temporário do SQLite, e barato.

```bash
docker run -d --name farm-pg -e POSTGRES_PASSWORD=teste -e POSTGRES_DB=farm -p 55433:5432 postgres:17-alpine
```

No PowerShell, que é o console deste projeto:

```bash
cd backend; $env:TEST_DATABASE_URL = "postgresql://postgres:teste@127.0.0.1:55433/farm"; .venv\Scripts\python.exe -m pytest -q
```

Sem a variável, a suíte roda em SQLite como sempre. Para voltar: `Remove-Item Env:TEST_DATABASE_URL`.

## Pendências honestas

- **Não há ferramenta de migração de dados.** O esquema nasce igual nos dois, mas copiar meses de histórico de
  um SQLite em produção para um PostgreSQL ainda é trabalho a fazer — e precisa de conferência, não de um
  `INSERT … SELECT` às cegas.
- **Credenciais não atravessam.** O cofre guarda AES-256-GCM com a chave mestra fora do banco, e no Windows ela
  é embrulhada por DPAPI, que é **por usuário e por máquina**. Trocar de banco (ou de máquina) exige
  `INSTAGRAM_CREDENTIALS_MASTER_KEY` e **recadastrar as credenciais** — o `key_id` gravado não abre com outra
  chave, de propósito.
- **A prova de senha em claro só roda no SQLite.** `test_a_senha_nao_fica_em_claro_em_lugar_nenhum_do_banco`
  abre um arquivo SQLite direto (`Database(cfg.db_path)`), porque metade dela confere o **arquivo byte a byte** —
  coisa que não existe no PostgreSQL. A varredura de todas as tabelas valeria nos dois; o arquivo, não. O cofre é
  neutro de dialeto (AES-256-GCM antes de tocar o banco), então o risco é baixo — mas está dito, não suposto.
- **Um processo continua sendo o dono.** `main.py` ainda sobe com `workers=1` e o scheduler não tem noção de
  posse (`claimed_by`), então PostgreSQL ainda **não** autoriza dois backends ao mesmo tempo. É o passo seguinte,
  não este.

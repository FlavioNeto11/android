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

## A cobertura que parecia existir

Vale contar porque é a lição mais cara desta etapa. A suíte passou a rodar contra PostgreSQL com `TEST_DATABASE_URL`,
e o número deu **381 nos dois bancos** — só que **12 pontos em 8 arquivos** abriam `Database(cfg.db_path)`, o
arquivo SQLite, **ignorando a variável**. Aqueles testes rodavam em SQLite *dentro* da corrida do PostgreSQL. O
número era verdadeiro e a conclusão que ele sugeria, não.

Corrigido (`cfg.db_dsn` em vez de `cfg.db_path`), a mesma suíte encontrou **cinco defeitos reais** que o PostgreSQL
tinha e ninguém via:

| Defeito | Por que passava desapercebido |
|---|---|
| `MAX(importance, ?)` — `MAX` escalar de 2 argumentos é do SQLite | erro barulhento, mas só em quem nunca rodava |
| Busca ignorava acento no SQLite e **não** no PostgreSQL | a busca não falhava: só não encontrava |
| `"a" OR "b"` (sintaxe do FTS5) chegava ao `plainto_tsquery` | virava E com a palavra literal "or": nunca casava |
| Normalização do `rank` escrita para `bm25()` (negativo, menor é melhor) aplicada ao `ts_rank` (positivo, maior é melhor) | dividia pelo PIOR e o corte em 1.0 achatava tudo |
| `busca @@ q1 \|\| q2` sem parêntese | `ProgrammingError` **engolido** pelo `except` da busca |

Os quatro primeiros têm a mesma assinatura e é ela que assusta: **nenhum deles dava erro**. A busca devolvia lista
vazia, quem chamou caía no caminho alternativo, e a resposta vinha errada com cara de certa. Só o quinto era um erro
de verdade — e estava sendo capturado por um `except` largo, que agora só vale no SQLite, onde a tolerância tem
motivo (lá o texto da tela É a expressão do índice; no PostgreSQL ele é parâmetro e não pode formar sintaxe).

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
| `MAX(importance, ?)` | `CASE WHEN … END` | o mesmo, e **escapou ao inventário** — ver *A cobertura que parecia existir* |
| `PRAGMA table_info(t)` | `db.columns(t)` | `PRAGMA` é do SQLite e nem aceita marcador de parâmetro |
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

## Dois backends no mesmo banco: o que foi preciso para isso ser seguro

Trocar de banco não basta. Havia um defeito que com um processo só nunca doeu: `interrupted_steps` pegava **toda**
etapa `running`, sem perguntar de quem era. Com dois backends, o segundo a subir devolveria para `ready` as etapas
que o primeiro estava executando **naquele instante** — e o primeiro perderia o trabalho sem saber.

A etapa passou a ter dono (`claimed_by`, `claim_expires_at`), e o dono é a **máquina** (`OWNER_ID`, por omissão o
hostname), não o processo. A escolha tem uma razão concreta: com identidade por PID, um backend reiniciado não
reconheceria as próprias etapas interrompidas e teria de **esperar o lease vencer** para retomá-las — trocaria
reconciliação imediata, que já era provada, por espera. Com identidade por máquina, o reinício retoma o que é dele
na hora, e o backend de outra máquina continua impedido de mexer.

São dois caminhos distintos, com provas distintas de que o dono morreu:

| | O que prova a morte | Quem reconcilia |
|---|---|---|
| Reinício do próprio backend | o processo reiniciou | ele mesmo, na hora (`interrupted_steps`) |
| Backend de outra máquina caiu | parou de renovar por mais de 120 s | quem estiver vivo, depois de **adotar** a etapa |

O lease é longo (120 s, renovado a cada 20 s) de propósito: o preço de demorar a retomar o trabalho de um backend
morto é baixo; o preço de adotar cedo demais é **dois backends operando o mesmo aparelho**. Renovar é uma escrita
por backend a cada 20 s, não por etapa.

**Pré-requisito que isto cria: relógio sincronizado.** O vencimento é gravado com o relógio de quem assumiu a
etapa e comparado com o relógio de quem pergunta. Um backend com o relógio adiantado alguns minutos veria todo lease
vivo como vencido e adotaria etapas em plena execução — o oposto do que o lease existe para fazer. Numa rede
Windows com domínio isso já vem resolvido; em máquinas soltas, confira o serviço de horário antes de ligar o segundo
backend. Os 120 s dão folga para desvio de segundos, não de minutos.

O preço aceito dessa escolha: `uvicorn --workers N` continua proibido. Fork daria N processos com o mesmo hostname,
logo o mesmo dono, e cada um reconciliaria as etapas dos outros. Dois backends na mesma máquina exigem `OWNER_ID`
explícito.

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
- **O limite de IA é por processo, e devia ser global.** `Limiter` usa `asyncio.Condition`, que só existe dentro
  de um processo. Com dois backends, `max_ai_concurrency = 3` passa a valer 3 em CADA um — seis chamadas onde se
  pediu três. Não é defeito de correção, é de orçamento, e importa porque esse é justamente o botão que controla
  gasto. Um limite global precisa do mesmo mecanismo de lease que a posse de etapa usa; não está feito.
  **Atenção ao não "consertar" o vizinho:** `boot_parallelism` usa o mesmo `Limiter` e está **certo** por processo —
  ele protege a RAM e a CPU da máquina local, que são recursos locais. Tornar aquele global seria o erro oposto:
  duas máquinas com 64 GB cada esperariam uma pela outra para ligar emulador.
- **Não há tela de login.** Ver `docs/worker.md` → *Como o worker alcança o central*: a porta de rede é autenticada
  por `API_TOKEN`, o que serve para worker e chamada de máquina, não para um painel servido a outras pessoas.

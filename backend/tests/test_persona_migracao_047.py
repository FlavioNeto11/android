"""Migração 047 — a persona passa a ser a pessoa (a linha de `instagram_profiles`).

O que se prova aqui, no SQLite sempre e no PostgreSQL quando `TEST_DATABASE_URL` existe:
- ATUALIZAÇÃO de um banco em 046 com o retrato de produção (3 perfis com persona, 5 bloqueados sem persona cujo
  nome bate com uma persona órfã — ADR-029 —, 6 personas órfãs): cada grupo é dobrado como o arquivo descreve;
- as tabelas FILHAS de `instagram_profiles` atravessam a reconstrução com as mesmas linhas, as chaves estrangeiras
  continuam apontando para a tabela certa (`PRAGMA foreign_key_check` vazio e a cascata ainda funciona) e a
  integridade do arquivo está intacta;
- o esquema é o MESMO num banco criado do zero e num atualizado;
- `username` ficou opcional (`''` = pessoa sem conta) e o índice único parcial continua recusando conta repetida —
  inclusive sobre o esquema REAL da produção, em que a coluna nasceu `UNIQUE COLLATE NOCASE` (008 antiga);
- rodar as instruções de dados de novo não muda nada (predicado `generation = '{}'`);
- banco vazio continua vazio.

Regra K-029 nos INSERTs à mão: cada valor no tipo da coluna (inteiro em INTEGER, bytes em BLOB) — texto numa
coluna INTEGER passa no SQLite e quebra no PostgreSQL.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from app import db as db_mod
from app.db import INTEGRITY_ERRORS, Database

from .test_db import _banco, _copia_das_migracoes, _Falso, _tem_indice

NOVA = "047_persona_e_a_pessoa"
ORIGEM = Path(db_mod.__file__).resolve().parents[1] / "migrations"
TS = "2026-09-27T12:00:00Z"
#: Tudo que referencia `instagram_profiles` (008, 009, 010, 037) mais as duas tabelas que a dobra lê.
FILHAS = ("instagram_credentials", "instagram_sessions", "device_profile_bindings", "authentication_attempts",
          "social_interactions", "memory_items", "relationship_summaries", "thread_summaries", "pending_approvals",
          "profile_accounts", "secrets", "personas", "policy_groups")
INDICES = ("ux_instagram_profiles_username", "ux_profiles_persona", "ix_instagram_profiles_policy_group")

VOZ = {"personality": "direto", "tone": "calmo", "formality": "informal", "typical_length": "curta",
       "emojis": "raro", "slang": "pouca", "humor": "seco", "interests": ["corrida", "trilha"],
       "dm_style": "curto", "comment_style": "concreto", "with_known": "solto", "with_strangers": "educado",
       "examples": ["fechou"], "common_phrases": ["valeu"], "forbidden_phrases": ["arrasou"]}
VISUAL = {"appearance": "alto, cabelo curto", "visual_style": "esportivo", "photo_scenario": "parque"}

#: (id da persona, nome) — 3 vinculadas, 5 casadas por nome, 6 órfãs. Nomes fictícios do parque (ADR-029).
VINCULADAS = [("persona-l1", "Tadeu Quintela"), ("persona-b1", "Quillon Teixeira"), ("persona-a1", "Ravenna Sampaio")]
#: `name` da persona × (display_name, first_name, last_name) do perfil bloqueado. Caixa e espaços variam de propósito.
CASADAS = [("persona-c1", "Sueli Barreto", ("Sueli Barreto", "Sueli", "Barreto")),
           ("persona-c2", "Gilberto Vasconcelos", (None, "Gilberto", "Vasconcelos")),
           ("persona-c3", "Fabiana Cardoso", ("Fabiana Cardoso", "Fabiana", "Cardoso")),
           ("persona-c4", "  luciana bastos ", ("Luciana Bastos", "Luciana", "Bastos")),
           ("persona-c5", "Osvaldo Guedes", ("Osvaldo Guedes", "Osvaldo", "Guedes"))]
ORFAS = [("persona-o1", "Denise Linhares"), ("persona-o2", "Nelson Pinto"), ("persona-o3", "Rosana Melo"),
         ("persona-o4", "Wagner Santana"), ("persona-o5", "Renata Vieira Lima"), ("persona-o6", "Otávio")]


def _assinatura(db: Database, tabela: str) -> str:
    linhas = db.query(f"SELECT * FROM {tabela} ORDER BY 1")          # noqa: S608 - nome fixo do teste
    return hashlib.sha256(json.dumps(linhas, sort_keys=True, default=str).encode()).hexdigest()


def _persona(db: Database, pid: str, nome: str, *, idade: int | None, visual: bool) -> None:
    traits = {**VOZ, **(VISUAL if visual else {})}
    resumo = f"Pessoa de {idade} anos que fala de corrida." if idade else "Sem idade no resumo."
    db.execute("INSERT INTO personas(id, name, summary, traits, persona_prompt, created_at, updated_at)"
               " VALUES (?,?,?,?,?,?,?)", (pid, nome, resumo, json.dumps(traits), "Responda curto.", TS, TS))


def _perfil(db: Database, pid: str, username: str, *, persona_id: str | None, status: str,
            nomes: tuple[str | None, str, str], grupo: str | None = None) -> None:
    display, primeiro, ultimo = nomes
    db.execute("INSERT INTO instagram_profiles(id, username, display_name, first_name, last_name, email, persona_id,"
               " status, policy_group_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (pid, username, display, primeiro, ultimo, f"{username}@exemplo.com", persona_id, status, grupo,
                TS, TS))


def _filhas(db: Database, pid: str, i: int, *, ativo: bool) -> None:
    db.execute("INSERT INTO secrets(ref, key_id, nonce, ciphertext, created_at, updated_at) VALUES (?,?,?,?,?,?)",
               (f"sec-{i}", "memoria-v1:aabb", b"\x00\x01nonce", b"\xff\xfecifra", TS, TS))
    db.execute("INSERT INTO instagram_credentials(profile_id, login_identifier, secret_ref, key_id, status,"
               " failed_attempts, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
               (pid, f"conta{i}@exemplo.com", f"sec-{i}", "memoria-v1:aabb", "active", 0, TS, TS))
    db.execute("INSERT INTO instagram_sessions(profile_id, instance_id, status, observed_username, verified_at,"
               " updated_at, unknown_streak) VALUES (?,?,?,?,?,?,?)",
               (pid, f"android-{i:02d}", "session_ready", f"user{i}", TS, TS, 0))
    db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at, reason)"
               " VALUES (?,?,?,?,?)", (pid, f"android-{i:02d}", 1 if ativo else 0, TS, "cadastro"))
    db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, status, session_status, created_at,"
               " updated_at) VALUES (?,?,?,?,?,?,?,?)",
               (f"acc-{pid}", pid, "instagram", f"user{i}", "active", "session_ready", TS, TS))
    db.execute("INSERT INTO authentication_attempts(profile_id, instance_id, started_at, outcome)"
               " VALUES (?,?,?,?)", (pid, f"android-{i:02d}", TS, "session_ready"))
    db.execute("INSERT INTO memory_items(id, profile_id, subject, content, source, importance, confidence,"
               " occurrences, fingerprint, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (f"mem-{i}", pid, "@amiga", f"gosta de trilha {i}", "operator", 0.6, 0.9, 1, f"fp-{i}", TS, TS))


def _retrato_de_producao(db: Database) -> None:
    """O que produção tem em 046: 14 personas, 8 perfis (3 ativos com persona, 5 bloqueados sem), 8 de cada filha."""
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram','com.instagram.android',0)")
    db.execute("INSERT INTO policy_groups(id, name, created_at, updated_at) VALUES ('grp-1','Cautela',?,?)", (TS, TS))
    for n, (per, nome) in enumerate(VINCULADAS, start=1):
        _persona(db, per, nome, idade=30 + n, visual=True)
        primeiro, ultimo = nome.split(" ", 1)
        _perfil(db, f"ig-{n}", f"{primeiro.lower()}.{ultimo.lower()}{n}", persona_id=per, status="active",
                nomes=(nome, primeiro, ultimo), grupo="grp-1" if n == 1 else None)
        _filhas(db, f"ig-{n}", n, ativo=True)
    for n, (per, nome, nomes) in enumerate(CASADAS, start=4):
        _persona(db, per, nome, idade=20 + n, visual=True)
        _perfil(db, f"ig-{n}", f"{nomes[1].lower()}.{nomes[2].lower()}{n}", persona_id=None, status="blocked",
                nomes=nomes)
        _filhas(db, f"ig-{n}", n, ativo=False)
    for n, (per, nome) in enumerate(ORFAS, start=1):
        _persona(db, per, nome, idade=None if n == 6 else 40 + n, visual=False)
    # Histórico e aprovação com dono: FILHAS que a reconstrução não pode perder nem soltar.
    db.execute("INSERT INTO social_interactions(id, profile_id, occurred_at, type, direction, counterparty, status,"
               " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
               ("int-1", "ig-1", TS, "dm_sent", "outbound", "@amiga", "confirmed", TS, TS))
    db.execute("INSERT INTO relationship_summaries(profile_id, counterparty, summary, interactions, updated_at)"
               " VALUES (?,?,?,?,?)", ("ig-1", "@amiga", "conversa leve", 1, TS))
    db.execute("INSERT INTO thread_summaries(profile_id, thread_key, counterparty, summary, messages, updated_at)"
               " VALUES (?,?,?,?,?,?)", ("ig-1", "dm:@amiga", "@amiga", "oi", 1, TS))
    db.execute("INSERT INTO pending_approvals(id, profile_id, capability, summary, status, created_at)"
               " VALUES (?,?,?,?,?,?)", ("apr-1", "ig-2", "SEND_MESSAGE", "mandar oi", "pending", TS))


def _instrucoes_de_dados(db: Database) -> list[str]:
    """As instruções da 047 a partir do casamento por nome — as que fazem a dobra; o DDL fica de fora."""
    texto = db.render((ORIGEM / f"{NOVA}.sql").read_text(encoding="utf-8"))
    todas = Database._instrucoes(texto)
    inicio = next(i for i, s in enumerate(todas) if s.startswith("CREATE TEMPORARY TABLE"))
    return todas[inicio:]


@pytest.fixture
def atualizado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Database:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate="046_versao_congelada")
    db = _banco(tmp_path)
    assert "046_versao_congelada" in db.migrate()
    _retrato_de_producao(db)
    shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
    return db


def test_a_047_dobra_os_tres_grupos_e_preserva_as_filhas(atualizado: Database) -> None:
    db = atualizado
    try:
        antes = {t: _assinatura(db, t) for t in FILHAS}
        assert db.migrate() == [NOVA]
        assert db.divergencias() == []
        # As filhas atravessaram a reconstrução byte a byte, e a tabela legada ficou intacta.
        assert {t: _assinatura(db, t) for t in FILHAS} == antes
        assert db.scalar("SELECT COUNT(*) FROM personas") == 14

        # 8 perfis viraram 14 pessoas: 3 + 5 dobradas no lugar, 6 novas sem conta.
        assert db.scalar("SELECT COUNT(*) FROM instagram_profiles") == 14
        assert db.scalar("SELECT COUNT(*) FROM instagram_profiles WHERE username = ''") == 6
        assert db.scalar("SELECT COUNT(*) FROM instagram_profiles WHERE persona_id IS NOT NULL") == 14
        assert db.scalar("SELECT COUNT(*) FROM instagram_profiles WHERE generation = '{}'") == 0

        # (a) vinculada: voz, resumo e prompt na linha do perfil; o visual saiu de `traits`.
        tadeu = db.one("SELECT * FROM instagram_profiles WHERE id='ig-1'")
        assert tadeu is not None and tadeu["persona_id"] == "persona-l1" and tadeu["status"] == "active"
        assert tadeu["summary"].startswith("Pessoa de 31 anos") and tadeu["persona_prompt"] == "Responda curto."
        assert json.loads(tadeu["traits"]) == VOZ
        assert json.loads(tadeu["visual"]) == VISUAL
        assert json.loads(tadeu["biography"]) == {"schema_version": 1, "tastes": {"interests": ["corrida", "trilha"]},
                                                  "approx_age": 31}
        assert json.loads(tadeu["generation"]) == {"source": "legacy_persona", "persona_id": "persona-l1"}
        assert tadeu["policy_group_id"] == "grp-1" and tadeu["email"] == "tadeu.quintela1@exemplo.com"

        # (b) casada por nome: display_name, nome+sobrenome sem display_name, e caixa/espaços diferentes.
        for n, (per, _nome, nomes) in enumerate(CASADAS, start=4):
            linha = db.one("SELECT * FROM instagram_profiles WHERE id=?", (f"ig-{n}",))
            assert linha is not None, n
            assert linha["persona_id"] == per and linha["status"] == "blocked", nomes
            assert linha["display_name"] == nomes[0] and linha["first_name"] == nomes[1]      # o perfil manda no nome
            assert json.loads(linha["traits"]) == VOZ and json.loads(linha["visual"]) == VISUAL
            assert json.loads(linha["biography"])["approx_age"] == 20 + n

        # (c) órfã: linha nova sem conta, nome separado no primeiro espaço, ativa, rastreada até a persona.
        novas = db.query("SELECT * FROM instagram_profiles WHERE username = '' ORDER BY id")
        assert [n["id"] for n in novas] == [f"ig-{per}" for per, _ in sorted(ORFAS)]
        por_persona = {n["persona_id"]: n for n in novas}
        renata = por_persona["persona-o5"]
        assert (renata["display_name"], renata["first_name"], renata["last_name"]) == (
            "Renata Vieira Lima", "Renata", "Vieira Lima")
        otavio = por_persona["persona-o6"]
        assert (otavio["first_name"], otavio["last_name"]) == ("Otávio", None)
        assert json.loads(otavio["biography"]) == {"schema_version": 1, "tastes": {"interests": ["corrida", "trilha"]}}
        assert all(n["status"] == "active" and n["email"] is None and n["birth_date"] is None for n in novas)
        assert all(n["username"] == "" for n in novas)                 # '' = sem conta, nunca NULL
        assert all(json.loads(n["visual"]) == {} and json.loads(n["traits"]) == VOZ for n in novas)
        assert all(json.loads(n["generation"]) == {"source": "legacy_persona", "persona_id": n["persona_id"]}
                   for n in novas)

        # Nada se repete: `migrate()` de novo não aplica nada, e as instruções de dados são inócuas na segunda vez.
        assert db.migrate() == []
        assinatura = _assinatura(db, "instagram_profiles")
        with db.tx():
            for instrucao in _instrucoes_de_dados(db):
                db.execute(instrucao)
        assert _assinatura(db, "instagram_profiles") == assinatura
    finally:
        db.close()


def test_as_chaves_estrangeiras_sobrevivem_a_reconstrucao(atualizado: Database) -> None:
    """A prova de que a marca `@foreign_keys:off` fez o que promete: nenhum filho sumiu no `DROP TABLE`, nenhum
    ficou órfão, e a cascata volta a valer depois — porque as FKs dos filhos apontam para a tabela NOVA."""
    db = atualizado
    try:
        db.migrate()
        if db.dialect == "sqlite":
            assert db.query("PRAGMA foreign_key_check") == []
            assert db.scalar("PRAGMA integrity_check") == "ok"
            assert db.scalar("PRAGMA foreign_keys") == 1                       # religada depois da migração
        assert db.scalar("SELECT COUNT(*) FROM memory_items WHERE profile_id='ig-1'") == 1
        db.execute("DELETE FROM instagram_profiles WHERE id='ig-1'")
        for tabela in ("instagram_credentials", "instagram_sessions", "device_profile_bindings", "memory_items",
                       "profile_accounts", "social_interactions", "relationship_summaries", "thread_summaries",
                       "authentication_attempts"):
            assert db.scalar(f"SELECT COUNT(*) FROM {tabela} WHERE profile_id='ig-1'") == 0, tabela   # noqa: S608
        # Filho de outro perfil segue no lugar: a cascata foi do perfil apagado, não da tabela.
        assert db.scalar("SELECT COUNT(*) FROM memory_items") == 7
        # E a FK para `personas` também sobreviveu: apagar a persona legada zera o rastro, não a pessoa.
        db.execute("DELETE FROM personas WHERE id='persona-b1'")
        assert db.one("SELECT persona_id, summary FROM instagram_profiles WHERE id='ig-2'") == {
            "persona_id": None, "summary": "Pessoa de 32 anos que fala de corrida."}
    finally:
        db.close()


def test_username_opcional_mas_unico_quando_existe(atualizado: Database) -> None:
    db = atualizado
    try:
        db.migrate()
        # Sem `username` no INSERT: o padrão é '' (sem conta), e duas pessoas sem conta convivem.
        db.execute("INSERT INTO instagram_profiles(id, created_at, updated_at) VALUES ('ig-nova', ?, ?)", (TS, TS))
        db.execute("INSERT INTO instagram_profiles(id, created_at, updated_at) VALUES ('ig-outra', ?, ?)", (TS, TS))
        with pytest.raises(INTEGRITY_ERRORS):
            db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at)"
                       " VALUES ('ig-dup', 'TADEU.QUINTELA1', ?, ?)", (TS, TS))
        with pytest.raises(INTEGRITY_ERRORS):
            db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at)"
                       " VALUES ('ig-nula', NULL, ?, ?)", (TS, TS))                      # a coluna segue NOT NULL
        assert db.scalar("SELECT COUNT(*) FROM instagram_profiles WHERE username = ''") == 8
    finally:
        db.close()


def test_a_047_sobre_o_esquema_real_da_producao_aceita_pessoas_sem_conta(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """O banco de produção nasceu com a 008 ANTIGA: `username TEXT NOT NULL UNIQUE COLLATE NOCASE` (lido em
    `sqlite_master` em 27/09; a 028 registra a divergência). Essa UNIQUE de coluna não sai por `ALTER` e recusaria
    a SEGUNDA pessoa com `username = ''` — só em produção, nunca num banco novo. É por isso que a 047 reconstrói a
    tabela no SQLite, e este teste reproduz exatamente esse esquema antes de migrar."""
    if _banco(tmp_path).dialect != "sqlite":
        pytest.skip("o esquema divergente da 008 antiga só existe no SQLite de produção")
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate="046_versao_congelada")
    antiga = destino / "008_instagram_domain.sql"
    texto = antiga.read_text(encoding="utf-8")
    assert "username          TEXT NOT NULL," in texto
    texto = texto.replace("username          TEXT NOT NULL,", "username          TEXT NOT NULL UNIQUE COLLATE NOCASE,")
    texto = texto.replace("CREATE UNIQUE INDEX ux_instagram_profiles_username ON instagram_profiles(lower(username));", "")
    antiga.write_text(texto, encoding="utf-8")
    db = _banco(tmp_path, "producao.sqlite3")
    try:
        db.migrate()
        assert "UNIQUE COLLATE NOCASE" in db.scalar(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='instagram_profiles'")
        _retrato_de_producao(db)
        shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
        assert db.migrate() == [NOVA]
        assert "UNIQUE COLLATE NOCASE" not in db.scalar(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='instagram_profiles'")
        assert db.scalar("SELECT COUNT(*) FROM instagram_profiles WHERE username = ''") == 6
        assert db.query("PRAGMA foreign_key_check") == [] and db.scalar("PRAGMA integrity_check") == "ok"
        for tabela in ("instagram_sessions", "device_profile_bindings", "memory_items", "profile_accounts"):
            assert db.scalar(f"SELECT COUNT(*) FROM {tabela}") == 8, tabela                 # noqa: S608
        with pytest.raises(INTEGRITY_ERRORS):
            db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at)"
                       " VALUES ('ig-dup', 'Tadeu.Quintela1', ?, ?)", (TS, TS))
    finally:
        db.close()


def test_banco_novo_e_banco_atualizado_tem_o_mesmo_esquema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate="046_versao_congelada")
    atualizado = _banco(tmp_path, "atualizado.sqlite3")
    atualizado.migrate()
    _retrato_de_producao(atualizado)
    shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
    atualizado.migrate()
    novo = _banco(tmp_path, "novo.sqlite3")
    try:
        assert novo.migrate()[-1] == NOVA
        colunas = sorted(novo.columns("instagram_profiles"))
        assert colunas == sorted(atualizado.columns("instagram_profiles"))
        assert {"summary", "traits", "persona_prompt", "gender", "locale", "biography", "visual",
                "generation"} <= set(colunas)
        for nome in INDICES:
            assert _tem_indice(novo, nome) and _tem_indice(atualizado, nome), nome
        # Banco vazio continua vazio: a dobra só roda sobre linhas que existem.
        assert novo.scalar("SELECT COUNT(*) FROM instagram_profiles") == 0
        assert novo.divergencias() == [] and atualizado.divergencias() == []
    finally:
        novo.close()
        atualizado.close()


def test_duas_personas_com_o_mesmo_nome_nao_disputam_o_perfil(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empate de nome: a de menor id dobra no perfil; a outra vira pessoa nova. Nunca duas no mesmo perfil."""
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate="046_versao_congelada")
    db = _banco(tmp_path)
    try:
        db.migrate()
        _persona(db, "persona-z2", "Ana Souza", idade=29, visual=False)
        _persona(db, "persona-z1", "ana souza", idade=33, visual=False)
        _perfil(db, "ig-ana", "ana.souza", persona_id=None, status="blocked", nomes=("Ana Souza", "Ana", "Souza"))
        shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
        db.migrate()
        assert db.scalar("SELECT persona_id FROM instagram_profiles WHERE id='ig-ana'") == "persona-z1"
        sobrou = db.one("SELECT id, username, display_name FROM instagram_profiles WHERE persona_id='persona-z2'")
        assert sobrou == {"id": "ig-persona-z2", "username": "", "display_name": "Ana Souza"}
    finally:
        db.close()


@pytest.mark.parametrize("dialeto", ["sqlite", "postgres"])
def test_a_047_renderiza_sem_marca_sobrando_nos_dois_dialetos(dialeto: str) -> None:
    texto = _Falso(dialeto).render((ORIGEM / f"{NOVA}.sql").read_text("utf-8"))
    instrucoes = Database._instrucoes(texto)
    assert "{{" not in texto and instrucoes
    assert sum(1 for s in instrucoes if s.startswith("CREATE TEMPORARY TABLE")) == 1
    if dialeto == "sqlite":
        assert any(s.startswith("CREATE TABLE instagram_profiles_novo") for s in instrucoes)
        assert not any("::jsonb" in s for s in instrucoes)
    else:
        assert any("ALTER COLUMN username SET DEFAULT ''" in s for s in instrucoes)
        assert not any("json_patch" in s or "instagram_profiles_novo" in s for s in instrucoes)

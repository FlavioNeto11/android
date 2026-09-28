"""Migração 049 (contas unificadas): a credencial, o consentimento e a sessão por aparelho passam a viver na CONTA.

O que se prova aqui, nos dois bancos (o PostgreSQL quando `TEST_DATABASE_URL` existe):
- ATUALIZAÇÃO de um banco na migração anterior com o retrato de produção (8 perfis, 8 credenciais com login =
  e-mail, 8 sessões das quais 5 sem vínculo ativo): aplica só a 049, não toca o legado, copia a credencial com o
  MESMO `secret_ref` (nada recifrado), traz só as 3 sessões com vínculo ativo, cria a conta âncora que faltava e
  aponta cada tentativa de autenticação para a conta;
- a carga inicial é idempotente: rodar de novo os INSERT/UPDATE não duplica nada;
- o esquema das tabelas tocadas é o MESMO num banco criado do zero e num atualizado (a lição da 008);
- a unicidade nova é (perfil, app, host) — duas contas do mesmo app no mesmo perfil só com hosts diferentes;
- apagar a conta apaga a sessão dela (FK em cascata);
- a ferramenta de cópia entre bancos acha uma ordem por FK com a tabela nova.

Só os tipos da migração nos INSERT à mão (K-029): TEXT, INTEGER e bytes nas colunas BLOB.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from app import db as db_mod
from app.db import INTEGRITY_ERRORS, Database
from app.tools.migrate_data import _dependencias, ordem_por_fk, tabelas_a_copiar

from .test_db import _banco, _copia_das_migracoes, _Falso, _tem_indice

NOVA = "049_contas_unificadas"
ORIGEM = Path(db_mod.__file__).resolve().parents[1] / "migrations"
#: A última migração ANTES da 049, lida do diretório: outras ondas podem trazer a 047/048 e o teste continua valendo.
ANTERIOR = max(f.stem for f in ORIGEM.glob("*.sql") if f.stem < NOVA)
TABELAS_TOCADAS = ("account_credentials", "profile_accounts", "account_sessions", "authentication_attempts")
LEGADO = ("instagram_profiles", "instagram_credentials", "instagram_sessions", "device_profile_bindings", "secrets",
          "apps")
TS = "2026-09-27T12:00:00.000Z"
IG = "com.instagram.android"


def _assinatura(db: Database, tabela: str) -> str:
    linhas = db.query(f"SELECT * FROM {tabela} ORDER BY 1")          # noqa: S608 - nome fixo do teste
    return hashlib.sha256(json.dumps(linhas, sort_keys=True, default=str).encode()).hexdigest()


def _semear_producao(db: Database) -> None:
    """O retrato de produção em 27/09: 8 perfis (3 ativos com aparelho, 5 bloqueados pelo ADR-029 e desatrelados),
    8 credenciais com login = e-mail, 8 sessões (7 prontas, 1 desconhecida) das quais 5 apontam para aparelho sem
    vínculo ativo, 7 contas Instagram vindas da 037 (a do 8º perfil falta: cadastrado com o app fora do registro) e
    uma conta de app SEM provedor marcada pela pessoa como deslogada."""
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (IG,))
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('qa-messenger','QA','com.pocqa.messenger',0)")
    for i in range(1, 9):
        pid, iid = f"ig-{i}", f"android-0{i}"
        ativo = i <= 3
        db.execute("INSERT INTO instagram_profiles(id, username, email, status, created_at, updated_at)"
                   " VALUES (?,?,?,?,?,?)",
                   (pid, f"perfil{i}", f"perfil{i}@exemplo.test", "active" if ativo else "blocked", TS, TS))
        db.execute("INSERT INTO secrets(ref, key_id, nonce, ciphertext, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                   (f"sec-{i}", "chave-v1", b"\x01" * 12, b"\xff" * 24, TS, TS))
        db.execute("INSERT INTO instagram_credentials(profile_id, login_identifier, secret_ref, key_id, status,"
                   " failed_attempts, created_at, updated_at, last_used_at) VALUES (?,?,?,?,?,?,?,?,?)",
                   (pid, f"perfil{i}@exemplo.test", f"sec-{i}", "chave-v1", "invalid" if i == 8 else "active",
                    2 if i == 2 else 0, TS, TS, TS if ativo else None))
        db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at, unbound_at)"
                   " VALUES (?,?,?,?,?)", (pid, iid, 1 if ativo else 0, TS, None if ativo else TS))
        db.execute("INSERT INTO instagram_sessions(profile_id, instance_id, status, observed_username, verified_at,"
                   " detail, updated_at, unknown_streak) VALUES (?,?,?,?,?,?,?,?)",
                   (pid, iid, "unknown" if i == 3 else "session_ready", None if i == 3 else f"perfil{i}",
                    None if i == 3 else TS, "tela não reconhecida" if i == 3 else "conta lida na tela", TS,
                    2 if i == 3 else 0))
        db.execute("INSERT INTO authentication_attempts(profile_id, instance_id, started_at, finished_at, outcome,"
                   " stage) VALUES (?,?,?,?,?,?)", (pid, iid, TS, TS, "session_ready", "classified"))
        if i < 8:
            # A cópia da 037: `session_status` congelado em `session_ready` para todos — o que a 049 NÃO usa.
            db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, status, session_status,"
                       " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                       (f"acc-{pid}", pid, "instagram", f"perfil{i}", "active", "session_ready", TS, TS))
    db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, status, session_status, session_detail,"
               " session_verified_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("acc-qa-1", "ig-1", "qa-messenger", "qa-user-01", "active", "logged_out", "saiu pelo Foco", None,
                TS, TS))


def _esquema(db: Database) -> dict[str, list[str] | list[bool]]:
    return {t: sorted(db.columns(t)) for t in TABELAS_TOCADAS} | {
        "@indices": [_tem_indice(db, "ux_profile_accounts_app_host"), _tem_indice(db, "ix_account_sessions_instance"),
                     _tem_indice(db, "ux_profile_accounts_app")]}


def _com_a_nova(destino: Path) -> None:
    shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")


def test_atualizacao_traz_credencial_sem_recifrar_e_so_sessao_com_vinculo(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    db = _banco(tmp_path)
    try:
        assert ANTERIOR in db.migrate()
        _semear_producao(db)
        antes = {t: _assinatura(db, t) for t in LEGADO}
        _com_a_nova(destino)
        assert db.migrate() == [NOVA]
        assert db.divergencias() == []
        # O legado atravessa intocado: mesma referência no cofre, nenhum byte recifrado.
        assert {t: _assinatura(db, t) for t in LEGADO} == antes

        # Credencial: as 8, cada uma na conta Instagram do seu perfil, com o MESMO `secret_ref` e os contadores.
        creds = db.query("SELECT a.profile_id, c.* FROM account_credentials c JOIN profile_accounts a ON a.id=c.account_id"
                         " ORDER BY a.profile_id")
        assert len(creds) == 8
        for c in creds:
            i = int(c["profile_id"].split("-")[1])
            assert c["secret_ref"] == f"sec-{i}" and c["login_identifier"] == f"perfil{i}@exemplo.test"
            # Cadastradas pelo dono para o "Conectar" digitá-las: o consentimento vem datado do cadastro e assinado
            # pela migração (design §4.3, decisão 5) — credencial NOVA exige a marca explícita.
            assert c["consent_at"] == TS and c["consent_by"] == "migração 049"
        por_perfil = {c["profile_id"]: c for c in creds}
        assert por_perfil["ig-2"]["failed_attempts"] == 2 and por_perfil["ig-8"]["status"] == "invalid"
        assert por_perfil["ig-1"]["last_used_at"] == TS and por_perfil["ig-5"]["last_used_at"] is None

        # A conta âncora que faltava nasceu com o id da 037; as demais são as mesmas linhas.
        assert db.scalar("SELECT COUNT(*) FROM profile_accounts") == 9
        nova = db.one("SELECT * FROM profile_accounts WHERE id='acc-ig-8'")
        assert nova is not None and nova["handle"] == "perfil8" and nova["app_id"] == "instagram"

        # Sessão: só onde o par (conta, aparelho) ainda existe — 3 do Instagram; as 5 fantasmas não vêm.
        sessoes = {(r["account_id"], r["instance_id"]): r for r in db.query("SELECT * FROM account_sessions")}
        assert set(sessoes) == {("acc-ig-1", "android-01"), ("acc-ig-2", "android-02"), ("acc-ig-3", "android-03"),
                                ("acc-qa-1", "android-01")}
        assert sessoes[("acc-ig-1", "android-01")]["status"] == "session_ready"
        assert sessoes[("acc-ig-1", "android-01")]["observed_handle"] == "perfil1"
        assert sessoes[("acc-ig-3", "android-03")]["status"] == "unknown"
        assert sessoes[("acc-ig-3", "android-03")]["unknown_streak"] == 2
        # A marcação da pessoa num app sem provedor vem no vocabulário único; a cópia velha da 037 não pesa.
        assert sessoes[("acc-qa-1", "android-01")]["status"] == "auth_required"
        assert sessoes[("acc-qa-1", "android-01")]["detail"] == "saiu pelo Foco"

        # Toda tentativa aponta para a conta do perfil.
        tentativas = db.query("SELECT profile_id, account_id FROM authentication_attempts")
        assert len(tentativas) == 8 and all(t["account_id"] == f"acc-{t['profile_id']}" for t in tentativas)
        assert db.migrate() == []
    finally:
        db.close()


def test_a_carga_inicial_e_idempotente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    db = _banco(tmp_path)
    try:
        db.migrate()
        _semear_producao(db)
        _com_a_nova(destino)
        assert db.migrate() == [NOVA]
        contagem = lambda: {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in TABELAS_TOCADAS}  # noqa: E731,S608
        antes = contagem()
        # Só a carga (INSERT/UPDATE) de novo: a DDL já aconteceu, e é a carga que precisa tolerar repetição.
        for instrucao in Database._instrucoes(db.render((ORIGEM / f"{NOVA}.sql").read_text(encoding="utf-8"))):
            if instrucao.lstrip().upper().startswith(("INSERT", "UPDATE")):
                db.execute(instrucao)
        assert contagem() == antes
    finally:
        db.close()


def test_banco_novo_e_banco_atualizado_tem_o_mesmo_esquema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    atualizado = _banco(tmp_path, "atualizado.sqlite3")
    atualizado.migrate()
    _semear_producao(atualizado)
    _com_a_nova(destino)
    atualizado.migrate()
    novo = _banco(tmp_path, "novo.sqlite3")
    try:
        assert novo.migrate()[-1] == NOVA
        esquema = _esquema(novo)
        assert esquema == _esquema(atualizado)
        assert {"status", "failed_attempts", "blocked_until", "created_at", "last_used_at", "consent_at",
                "consent_by"} <= set(esquema["account_credentials"])
        assert "host" in esquema["profile_accounts"] and "account_id" in esquema["authentication_attempts"]
        assert esquema["@indices"] == [True, True, False]          # a unicidade antiga saiu
        assert novo.divergencias() == [] and atualizado.divergencias() == []
    finally:
        novo.close()
        atualizado.close()


@pytest.fixture
def banco(tmp_path: Path) -> Database:
    db = _banco(tmp_path)
    db.migrate()
    db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at) VALUES ('p1','p1',?,?)", (TS, TS))
    return db


def _conta(db: Database, cid: str, app_id: str, host: str | None) -> None:
    db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, host, created_at, updated_at)"
               " VALUES (?,?,?,?,?,?,?)", (cid, "p1", app_id, "x", host, TS, TS))


def test_unicidade_e_por_perfil_app_e_host(banco: Database) -> None:
    try:
        _conta(banco, "c1", "chrome", None)
        with pytest.raises(INTEGRITY_ERRORS):
            _conta(banco, "c2", "chrome", None)                       # sem host, NULL vale como ''
        _conta(banco, "c3", "chrome", "portal.exemplo.test")          # outro site, outra conta
        _conta(banco, "c4", "chrome", "banco.exemplo.test")
        with pytest.raises(INTEGRITY_ERRORS):
            _conta(banco, "c5", "chrome", "portal.exemplo.test")
        assert banco.scalar("SELECT COUNT(*) FROM profile_accounts WHERE profile_id='p1'") == 3
    finally:
        banco.close()


def test_apagar_a_conta_apaga_a_sessao_dela(banco: Database) -> None:
    try:
        _conta(banco, "c1", "chrome", None)
        banco.execute("INSERT INTO account_sessions(account_id, instance_id, status, updated_at) VALUES (?,?,?,?)",
                      ("c1", "android-01", "session_ready", TS))
        with pytest.raises(INTEGRITY_ERRORS):
            banco.execute("INSERT INTO account_sessions(account_id, instance_id, status, updated_at) VALUES (?,?,?,?)",
                          ("c1", "android-01", "unknown", TS))       # uma sessão por (conta, aparelho)
        banco.execute("DELETE FROM profile_accounts WHERE id='c1'")
        assert banco.scalar("SELECT COUNT(*) FROM account_sessions") == 0
    finally:
        banco.close()


def test_a_049_renderiza_sem_marca_sobrando_nos_dois_dialetos() -> None:
    for dialeto in ("sqlite", "postgres"):
        texto = _Falso(dialeto).render((ORIGEM / f"{NOVA}.sql").read_text("utf-8"))
        assert "{{" not in texto
        instrucoes = Database._instrucoes(texto)
        assert len(instrucoes) == 18, dialeto


def test_a_copia_entre_bancos_acha_ordem_por_fk_com_a_tabela_nova(banco: Database) -> None:
    try:
        tabelas = set(tabelas_a_copiar(banco, banco))
        assert {"account_sessions", "account_credentials", "profile_accounts"} <= tabelas
        dependencias = _dependencias(banco, tabelas)
        ordem = ordem_por_fk(banco, tabelas)
        for filho in ("account_sessions", "account_credentials"):
            assert "profile_accounts" in dependencias[filho]
            assert all(ordem.index(pai) < ordem.index(filho) for pai in dependencias[filho] if pai != filho), filho
    finally:
        banco.close()

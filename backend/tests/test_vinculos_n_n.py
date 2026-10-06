"""Migração 051 (persona N:N aparelho) e o repositório de vínculos depois dela (design persona-e-parque §7, §10.5).

O que se prova aqui, nos dois bancos (o PostgreSQL quando `TEST_DATABASE_URL` existe):
- ATUALIZAÇÃO de um banco na 050 com o retrato de produção (8 vínculos, 3 ativos): só a 051 é aplicada; os 3 ativos
  viram principais e ganham o `app_id` da conta única da persona; os 5 inativos ficam como estavam; os dois índices
  1:1 saem e os três novos entram; `runs.targets` nasce nula;
- idempotência: o segundo `migrate()` não aplica nada;
- banco novo = banco atualizado (colunas e índices);
- a 051 renderiza sem marca de dialeto sobrando;
- o N:N de verdade, pelo repositório: uma persona em dois aparelhos (com um principal), dois aparelhos com personas
  diferentes, duas personas de APPS diferentes no mesmo aparelho, e a recusa de duas contas do MESMO app no mesmo
  aparelho (D2-a) — pelo repositório (`BindingConflict`) e pelo índice (INSERT à mão).

K-029: os INSERTs de semente usam só os tipos que as migrações declaram.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from app import db as db_mod
from app.db import INTEGRITY_ERRORS, Database
from app.social.repository import BindingConflict, SocialRepository

from .test_db import _banco, _copia_das_migracoes, _Falso, _tem_indice

NOVA = "051_persona_n_aparelho"
ORIGEM = Path(db_mod.__file__).resolve().parents[1] / "migrations"
ANTERIOR = max(f.stem for f in ORIGEM.glob("*.sql") if f.stem < NOVA)
TS = "2026-09-27T12:00:00.000Z"
INDICES_ANTIGOS = ("idx_binding_profile_ativo", "idx_binding_device_ativo")
INDICES_NOVOS = ("ux_binding_par_ativo", "ux_binding_conta_do_app_no_aparelho", "ux_binding_principal")
#: O retrato de produção em 27/09 (design §2.7): 8 linhas de vínculo, 3 ativas.
PERFIS = ("p-tadeu", "p-quillon", "p-ottilie", "p-luciana", "p-julia", "p-carla", "p-pedro", "p-ana")
ATIVOS = {"p-tadeu": "android-01", "p-quillon": "android-02", "p-ottilie": "android-03"}


def _assinatura(db: Database, sql: str) -> str:
    return hashlib.sha256(json.dumps(db.query(sql), sort_keys=True, default=str).encode()).hexdigest()


def _semear_producao(db: Database) -> None:
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram','com.instagram.android',1)")
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('chrome','Chrome','com.android.chrome',0)")
    for i, pid in enumerate(PERFIS, start=1):
        db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at) VALUES (?,?,?,?)",
                   (pid, pid[2:], TS, TS))
        db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at, unbound_at, reason)"
                   " VALUES (?,?,?,?,?,?)",
                   (pid, ATIVOS.get(pid, f"android-{i:02d}"), 1 if pid in ATIVOS else 0, TS,
                    None if pid in ATIVOS else TS, None if pid in ATIVOS else "bloqueado (ADR-029)"))
    # Contas: Tadeu e Quillon têm só a do Instagram (o vínculo ganha `app_id`); Ravenna tem Instagram E Chrome (fica
    # NULL: não se adivinha de qual app é o vínculo); as demais não têm conta nenhuma.
    for cid, pid, app in (("c-tadeu", "p-tadeu", "instagram"), ("c-quillon", "p-quillon", "instagram"),
                          ("c-ottilie", "p-ottilie", "instagram"), ("c-ottilie-chrome", "p-ottilie", "chrome")):
        db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, status, created_at, updated_at)"
                   " VALUES (?,?,?,?,?,?,?)", (cid, pid, app, pid[2:], "active", TS, TS))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES (?,?,?,?,?,?,?,?)", ("run-antiga", "k-antiga", "abra o app", "execute", "completed", 1,
                                            '["android-01"]', TS))


def _esquema(db: Database) -> dict[str, object]:
    return {"bindings": sorted(db.columns("device_profile_bindings")), "runs": sorted(db.columns("runs")),
            "@indices": [_tem_indice(db, n) for n in INDICES_ANTIGOS + INDICES_NOVOS]}


def test_atualizacao_050_para_051_com_o_retrato_de_producao(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    db = _banco(tmp_path)
    try:
        assert db.migrate()[-1] == ANTERIOR
        assert not {"app_id", "is_primary"} & db.columns("device_profile_bindings")
        assert "targets" not in db.columns("runs")
        _semear_producao(db)
        inativos_antes = _assinatura(db, "SELECT profile_id, instance_id, active, bound_at, unbound_at, reason"
                                         " FROM device_profile_bindings WHERE active=0 ORDER BY id")
        shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
        assert db.migrate() == [NOVA]
        assert db.divergencias() == []
        assert {"app_id", "is_primary"} <= db.columns("device_profile_bindings")
        assert "targets" in db.columns("runs")
        assert [_tem_indice(db, n) for n in INDICES_ANTIGOS] == [False, False]
        assert [_tem_indice(db, n) for n in INDICES_NOVOS] == [True, True, True]
        # 8 linhas, 3 ativas: as ativas viram principais; o app vem da conta ÚNICA da persona.
        assert db.scalar("SELECT COUNT(*) FROM device_profile_bindings") == 8
        ativos = db.query("SELECT profile_id, instance_id, app_id, is_primary FROM device_profile_bindings"
                          " WHERE active=1 ORDER BY profile_id")
        assert ativos == [
            {"profile_id": "p-ottilie", "instance_id": "android-03", "app_id": None, "is_primary": 1},
            {"profile_id": "p-quillon", "instance_id": "android-02", "app_id": "instagram", "is_primary": 1},
            {"profile_id": "p-tadeu", "instance_id": "android-01", "app_id": "instagram", "is_primary": 1}]
        assert db.scalar("SELECT COUNT(*) FROM device_profile_bindings WHERE active=0 AND is_primary=1") == 0
        assert db.scalar("SELECT COUNT(*) FROM device_profile_bindings WHERE active=0 AND app_id IS NOT NULL") == 0
        assert _assinatura(db, "SELECT profile_id, instance_id, active, bound_at, unbound_at, reason"
                               " FROM device_profile_bindings WHERE active=0 ORDER BY id") == inativos_antes
        assert db.scalar("SELECT targets FROM runs WHERE id='run-antiga'") is None
        assert db.migrate() == []                                    # idempotente
    finally:
        db.close()


def test_banco_novo_e_banco_atualizado_tem_o_mesmo_esquema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    atualizado = _banco(tmp_path, "atualizado.sqlite3")
    atualizado.migrate()
    _semear_producao(atualizado)
    shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
    atualizado.migrate()
    novo = _banco(tmp_path, "novo.sqlite3")
    try:
        assert novo.migrate()[-1] == NOVA
        esquema = _esquema(novo)
        assert esquema == _esquema(atualizado)
        assert esquema["@indices"] == [False, False, True, True, True]
        assert novo.divergencias() == [] and atualizado.divergencias() == []
    finally:
        novo.close()
        atualizado.close()


@pytest.mark.parametrize("dialeto", ["sqlite", "postgres"])
def test_a_051_renderiza_sem_marca_sobrando_nos_dois_dialetos(dialeto: str) -> None:
    texto = _Falso(dialeto).render((ORIGEM / f"{NOVA}.sql").read_text("utf-8"))
    assert "{{" not in texto and "@dialect" not in texto
    instrucoes = Database._instrucoes(texto)
    assert sum(i.startswith("DROP INDEX") for i in instrucoes) == 2
    assert sum(i.startswith("CREATE UNIQUE INDEX") for i in instrucoes) == 3
    # O índice de expressão leva a expressão entre parênteses: o PostgreSQL exige, o SQLite aceita.
    assert "(COALESCE(app_id, ''))" in texto


# ============================================================ o N:N pelo repositório
@pytest.fixture
def repo(tmp_path: Path) -> SocialRepository:
    db = _banco(tmp_path)
    db.migrate()
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram','com.instagram.android',1)")
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('chrome','Chrome','com.android.chrome',0)")
    for pid in ("p-ottilie", "p-quillon", "p-tadeu"):
        db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at) VALUES (?,?,?,?)",
                   (pid, pid[2:], TS, TS))
    r = SocialRepository(db)
    r.app_package = "com.instagram.android"
    yield r
    db.close()


def _pares(repo: SocialRepository, pid: str) -> list[tuple[str, str | None, bool]]:
    return [(str(b["instance_id"]), b["app_id"], bool(b["is_primary"])) for b in repo.bindings_of_profile(pid)]


def test_uma_persona_em_dois_aparelhos_com_um_principal(repo: SocialRepository) -> None:
    repo.bind("p-ottilie", "android-01", app_id="instagram")
    repo.bind("p-ottilie", "android-02", app_id="instagram")
    assert _pares(repo, "p-ottilie") == [("android-01", "instagram", True), ("android-02", "instagram", False)]
    assert repo.binding_principal("p-ottilie")["instance_id"] == "android-01"
    with pytest.raises(ValueError):
        repo.binding_row("p-ottilie")                                  # a leitura antiga não escolhe em silêncio
    repo.set_primary("p-ottilie", "android-02")
    assert repo.binding_principal("p-ottilie")["instance_id"] == "android-02"
    assert sum(b["is_primary"] for b in repo.bindings_of_profile("p-ottilie")) == 1
    # Desvincular o principal promove o que sobra: a persona vinculada nunca fica sem aparelho principal.
    repo.unbind("p-ottilie", "android-02")
    assert _pares(repo, "p-ottilie") == [("android-01", "instagram", True)]
    assert repo.binding("p-ottilie", "android-02") is None


def test_dois_aparelhos_com_personas_diferentes_e_duas_personas_de_apps_diferentes_no_mesmo(repo: SocialRepository) -> None:
    repo.bind("p-ottilie", "android-01", app_id="instagram")
    repo.bind("p-quillon", "android-02", app_id="instagram")
    assert [str(b["profile_id"]) for b in repo.profiles_of_instance("android-01")] == ["p-ottilie"]
    assert [str(b["profile_id"]) for b in repo.profiles_of_instance("android-02")] == ["p-quillon"]
    # Quillon usa o Chrome no aparelho do Ravenna: apps diferentes convivem no mesmo aparelho.
    repo.bind("p-quillon", "android-01", app_id="chrome")
    assert [str(b["profile_id"]) for b in repo.profiles_of_instance("android-01")] == ["p-ottilie", "p-quillon"]
    assert [str(b["profile_id"]) for b in repo.profiles_of_instance("android-01", "instagram")] == ["p-ottilie"]
    assert [str(b["profile_id"]) for b in repo.profiles_of_instance("android-01", "chrome")] == ["p-quillon"]
    assert repo.perfil_unico_da_instancia("android-01") is None
    with pytest.raises(ValueError):
        repo.profile_id_for_instance("android-01")
    # `bind` não toma mais o aparelho de ninguém: o Ravenna continua lá.
    assert _pares(repo, "p-ottilie") == [("android-01", "instagram", True)]


def test_duas_contas_do_mesmo_app_no_mesmo_aparelho_sao_recusadas(repo: SocialRepository) -> None:
    repo.bind("p-ottilie", "android-01", app_id="instagram")
    with pytest.raises(BindingConflict) as exc:
        repo.bind("p-quillon", "android-01", app_id="instagram")
    assert exc.value.code == "conta_do_app_ja_no_aparelho" and "p-ottilie" in str(exc.value)
    assert _pares(repo, "p-quillon") == []
    # E o índice segura mesmo quem escreve por fora do repositório (INSERT à mão, só com os tipos da 051).
    with pytest.raises(INTEGRITY_ERRORS):
        repo.db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at, app_id,"
                        " is_primary) VALUES (?,?,1,?,?,0)", ("p-quillon", "android-01", TS, "instagram"))
    # O vínculo repetido do MESMO par também é recusado pelo repositório, sem duplicar linha.
    repo.bind("p-ottilie", "android-01", app_id="instagram")           # idempotente: já existe, nada muda
    assert repo.db.scalar("SELECT COUNT(*) FROM device_profile_bindings WHERE active=1") == 1
    # Sem app (apps sem conta gerenciada) o mesmo aparelho aceita várias personas.
    repo.bind("p-quillon", "android-01")
    repo.bind("p-tadeu", "android-01")
    assert len(repo.profiles_of_instance("android-01")) == 3


def test_vinculo_sem_app_de_quem_tem_conta_no_app_tambem_e_recusado(repo: SocialRepository) -> None:
    """O vínculo sem app de uma persona que TEM conta do Instagram serve ao Instagram (`profiles_of_instance`):
    entrar assim num aparelho que já tem outra conta do Instagram seriam duas — recusado pela mesma regra (D2-a)."""
    repo.db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, status, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?)", ("c-quillon", "p-quillon", "instagram", "quillon", "active", TS, TS))
    repo.bind("p-ottilie", "android-01", app_id="instagram")
    with pytest.raises(BindingConflict) as exc:
        repo.bind("p-quillon", "android-01")
    assert exc.value.app_id == "instagram" and exc.value.other_profile_id == "p-ottilie"
    # E a outra direção: quem entrou sem app, tendo conta, segura o app para si.
    repo.bind("p-quillon", "android-02")
    with pytest.raises(BindingConflict):
        repo.bind("p-ottilie", "android-02", app_id="instagram")
    assert [str(b["profile_id"]) for b in repo.profiles_of_instance("android-02", "instagram")] == ["p-quillon"]

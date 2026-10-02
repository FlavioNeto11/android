"""Backfill único das lições anteriores à 055 (`scripts/aprendizado-backfill-licoes.py`).

O que se prova (nível `simulated`: banco de teste, nenhum aparelho, nenhuma IA, nenhuma rede):
- o ensaio (padrão) relata o que faria e NÃO grava nada no banco dado (roda na cópia);
- `--aplicar` grava a candidata; rodar de novo não duplica item, evidência nem transição (idempotente);
- só as execuções REAIS e anteriores ao `applied_at` da 055 entram em `--antes-da-055`;
- o banco numa migração diferente da do código aborta sem gravar (nos dois sentidos) e o script nunca migra;
- só os mineradores `licoes.contraste` e `licoes.plano` rodam (nem as exposições, nem o digest inteiro);
- o modo é fixo em `shadow` (nada é publicado) e o relatório não traz texto de lição;
- nenhuma conexão de rede é aberta durante o backfill.
"""
from __future__ import annotations

import importlib.util
import socket
from pathlib import Path
from types import ModuleType

import pytest

from app.db import Database
from app.modules.learning.application import licoes as app_licoes
from app.modules.learning.application import servico as app_servico
from app.modules.learning.infrastructure import backfill_licoes as bf

from .test_learning_licoes import Tentativa, Toque, contraste_ciclo, rid, semear

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "aprendizado-backfill-licoes.py"
CORTE_055 = "2026-09-29T03:55:00.000Z"


def _carregar_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("aprendizado_backfill_licoes", SCRIPT)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def script() -> ModuleType:
    return _carregar_script()


def _abrir(caminho: Path) -> Database:
    return Database(caminho)                                  # abrir não migra


@pytest.fixture
def banco(tmp_path: Path) -> Path:
    """Um banco migrado com o corte da 055 fixado e três execuções: duas reais anteriores (uma delas simulada) e
    uma real posterior."""
    caminho = tmp_path / "poc.sqlite3"
    db = Database(caminho)
    db.migrate()
    db.execute("UPDATE schema_migrations SET applied_at=? WHERE version LIKE '055_%'", (CORTE_055,))
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram','com.instagram.android',0)")
    contraste_ciclo(db, "r-antiga", dias_atras=5)                              # real, antes do corte: entra
    contraste_ciclo(db, "r-simulada", instancia="android-07", dias_atras=5, simulated=True)   # nunca é fonte
    contraste_ciclo(db, "r-depois", instancia="android-08", dias_atras=-1)     # depois do corte: o digest cuidou
    db.close()
    return caminho


def _contagens(caminho: Path) -> dict[str, int]:
    db = _abrir(caminho)
    try:
        return {"itens": int(db.scalar("SELECT COUNT(*) FROM learning_items WHERE kind='licao'")),
                "evidencias": int(db.scalar("SELECT COUNT(*) FROM learning_evidence")),
                "transicoes": int(db.scalar("SELECT COUNT(*) FROM learning_transitions")),
                "exposicoes": int(db.scalar("SELECT COUNT(*) FROM learning_exposures"))}
    finally:
        db.close()


def _rodar(script: ModuleType, banco: Path, *args: str) -> int:
    return int(script.main(["--banco", str(banco), *args]))


# ================================================================== ensaio x aplicar
def test_ensaio_nao_grava_e_aplicar_grava_uma_candidata(script: ModuleType, banco: Path,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    assert _rodar(script, banco, "--antes-da-055") == 0
    saida = capsys.readouterr().out
    assert "ENSAIO" in saida and "execuções reais examinadas: 1" in saida
    assert "depois: candidate=1" in saida and "criados: itens=1 evidências=1 transições=1" in saida
    assert _contagens(banco) == {"itens": 0, "evidencias": 0, "transicoes": 0, "exposicoes": 0}   # original intacto

    assert _rodar(script, banco, "--antes-da-055", "--dry-run") == 0                      # --dry-run = o padrão
    assert _contagens(banco)["itens"] == 0
    capsys.readouterr()

    assert _rodar(script, banco, "--antes-da-055", "--aplicar") == 0
    saida = capsys.readouterr().out
    assert "APLICADO" in saida and "kind=licao estado=candidate papel=actor" in saida
    assert _contagens(banco) == {"itens": 1, "evidencias": 1, "transicoes": 1, "exposicoes": 0}


def test_aplicar_de_novo_e_idempotente(script: ModuleType, banco: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _rodar(script, banco, "--antes-da-055", "--aplicar") == 0
    primeiro = _contagens(banco)
    capsys.readouterr()
    assert _rodar(script, banco, "--antes-da-055", "--aplicar") == 0
    assert "criados: itens=0 evidências=0 transições=0" in capsys.readouterr().out
    assert _contagens(banco) == primeiro == {"itens": 1, "evidencias": 1, "transicoes": 1, "exposicoes": 0}
    # o ensaio depois de aplicar também não vê nada a criar
    assert _rodar(script, banco, "--antes-da-055") == 0
    assert "criados: itens=0 evidências=0 transições=0" in capsys.readouterr().out
    db = _abrir(banco)
    try:
        [ev] = db.query("SELECT run_id, simulated FROM learning_evidence")
        assert (ev["run_id"], ev["simulated"]) == ("r-antiga", 0)             # só a execução real anterior
    finally:
        db.close()


def test_so_as_reais_anteriores_ao_corte_entram(banco: Path) -> None:
    db = _abrir(banco)
    try:
        assert bf.antes_da_055(db).run_ids == ("r-antiga",)
        sel = bf.da_lista(db, ["r-antiga", "r-simulada", "nao-existe", "r-antiga"])
        assert sel.run_ids == ("r-antiga",)
        assert sel.puladas == {"r-simulada": "simulada", "nao-existe": "inexistente"}
        db.execute("UPDATE runs SET status='running' WHERE id='r-depois'")
        assert bf.da_lista(db, ["r-depois"]).puladas == {"r-depois": "nao_terminal"}
    finally:
        db.close()


def test_run_id_explicito_minera_so_a_execucao_pedida(script: ModuleType, banco: Path) -> None:
    assert _rodar(script, banco, "--run-id", "r-depois", "--aplicar") == 0
    db = _abrir(banco)
    try:
        [ev] = db.query("SELECT run_id FROM learning_evidence")
        assert ev["run_id"] == "r-depois"
    finally:
        db.close()


# ================================================================== a migração do banco
def test_banco_em_outra_migracao_aborta_sem_gravar(script: ModuleType, banco: Path,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    db = _abrir(banco)
    mais_nova = bf.versao_do_codigo()
    db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES ('999_do_futuro', ?)", (CORTE_055,))
    db.close()
    for aplicar in ((), ("--aplicar",)):
        assert _rodar(script, banco, "--antes-da-055", *aplicar) == 2
        err = capsys.readouterr().err
        assert "ABORTADO" in err and "999_do_futuro" in err and mais_nova in err
    assert _contagens(banco) == {"itens": 0, "evidencias": 0, "transicoes": 0, "exposicoes": 0}

    # o banco ATRÁS do código: também aborta, e o script nunca aplica a migração que falta
    db = _abrir(banco)
    db.execute("DELETE FROM schema_migrations WHERE version IN ('999_do_futuro', ?)", (mais_nova,))
    db.close()
    assert _rodar(script, banco, "--antes-da-055", "--aplicar") == 2
    assert "ABORTADO" in capsys.readouterr().err
    db = _abrir(banco)
    try:
        assert bf.versao_do_banco(db) != bf.versao_do_codigo()
        assert db.one("SELECT 1 AS x FROM schema_migrations WHERE version=?", (mais_nova,)) is None
    finally:
        db.close()
    assert _contagens(banco)["itens"] == 0


def test_sem_a_055_nao_ha_antes_da_055(banco: Path) -> None:
    db = _abrir(banco)
    try:
        db.execute("DELETE FROM schema_migrations WHERE version LIKE '055_%'")
        with pytest.raises(bf.MigracaoAusente):
            bf.antes_da_055(db)
    finally:
        db.close()


# ================================================================== só os mineradores de lição, sem IA, sem rede
def test_so_os_mineradores_de_licao_rodam(banco: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chamados: list[str] = []
    contraste, plano = app_licoes.ServicoDeLicoes.minerar_contrastes, app_licoes.ServicoDeLicoes.minerar_plano

    def espia_contraste(self: app_licoes.ServicoDeLicoes, run_id: str) -> int:
        chamados.append("licoes.contraste")
        return contraste(self, run_id)

    def espia_plano(self: app_licoes.ServicoDeLicoes, run_id: str) -> int:
        chamados.append("licoes.plano")
        return plano(self, run_id)

    def proibido(*_a: object, **_k: object) -> int:
        raise AssertionError("o backfill não pode rodar este minerador nem o digest inteiro")

    monkeypatch.setattr(app_licoes.ServicoDeLicoes, "minerar_contrastes", espia_contraste)
    monkeypatch.setattr(app_licoes.ServicoDeLicoes, "minerar_plano", espia_plano)
    monkeypatch.setattr(app_licoes.ServicoDeLicoes, "preencher", proibido)            # licoes.exposicoes
    monkeypatch.setattr(app_licoes.ServicoDeLicoes, "curar", proibido)
    monkeypatch.setattr(app_servico.LearningService, "digerir_execucao", proibido)
    monkeypatch.setattr(app_servico.LearningService, "curar", proibido)

    db = _abrir(banco)
    try:
        resultado = bf.executar(db, bf.antes_da_055(db))
    finally:
        db.close()
    assert chamados == ["licoes.contraste", "licoes.plano"] and bf.MINERADORES == tuple(chamados)
    assert resultado.itens_criados == 1


def test_licao_do_planejador_pelo_backfill(script: ModuleType, tmp_path: Path) -> None:
    caminho = tmp_path / "plano.sqlite3"
    db = Database(caminho)
    db.migrate()
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram','com.instagram.android',0)")
    semear(db, "r-defeito", [Tentativa("failed", "Defeito do plano — a pós-condição não é comprovável",
                                       "defeito_do_plano")],
           status_da_etapa="failed", pos="model_judged", dias_atras=5, status_da_execucao="failed")
    db.execute("UPDATE steps SET failure_kind='defeito_do_plano' WHERE run_id='r-defeito'")
    semear(db, "r-ok", [Tentativa("succeeded", toques=[Toque(rid("ok"))])], pos="text_visible", dias_atras=1)
    db.close()
    assert _rodar(script, caminho, "--run-id", "r-defeito", "--run-id", "r-ok", "--aplicar") == 0
    db = _abrir(caminho)
    try:
        [item] = db.query("SELECT scope_role, state, source_kind FROM learning_items WHERE kind='licao'")
        assert (item["scope_role"], item["state"], item["source_kind"]) == ("planner", "candidate", "plan_defect")
    finally:
        db.close()
    assert _rodar(script, caminho, "--run-id", "r-defeito", "--run-id", "r-ok", "--aplicar") == 0
    assert _contagens(caminho)["evidencias"] == 1


def test_nunca_publica_e_nao_vaza_texto_de_licao(script: ModuleType, tmp_path: Path,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """Duas execuções reais com a mesma impressão: a repetição valida (regra de produção), mas o modo fixo `shadow`
    nunca publica; e o relatório só tem ids, estados e escopo."""
    caminho = tmp_path / "repete.sqlite3"
    db = Database(caminho)
    db.migrate()
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram','com.instagram.android',0)")
    db.execute("UPDATE schema_migrations SET applied_at=? WHERE version LIKE '055_%'", (CORTE_055,))
    contraste_ciclo(db, "r1", dias_atras=5)
    contraste_ciclo(db, "r2", instancia="android-07", dias_atras=4)
    db.close()
    assert _rodar(script, caminho, "--antes-da-055", "--aplicar") == 0
    saida = capsys.readouterr().out
    db = _abrir(caminho)
    try:
        [item] = db.query("SELECT state, summary, evidence_for FROM learning_items WHERE kind='licao'")
        assert item["state"] == "validated" and item["evidence_for"] == 2
        assert db.scalar("SELECT COUNT(*) FROM learning_items WHERE state='published'") == 0
        assert item["summary"] not in saida and "row_feed" not in saida          # nenhum texto de lição no relatório
    finally:
        db.close()
    assert _contagens(caminho)["evidencias"] == 2


def test_sem_rede_e_sem_provedor_de_ia(script: ModuleType, banco: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def sem_rede(*_a: object, **_k: object) -> None:
        raise AssertionError("o backfill não pode abrir conexão de rede")

    monkeypatch.setattr(socket.socket, "connect", sem_rede)
    monkeypatch.setattr(socket.socket, "connect_ex", sem_rede)
    assert _rodar(script, banco, "--antes-da-055", "--aplicar") == 0
    fonte = Path(bf.__file__).read_text(encoding="utf-8")
    importados = [ln for ln in fonte.splitlines() if ln.startswith(("from ", "import "))]
    assert not [ln for ln in importados if any(p in ln for p in ("app.planning", "anthropic", "openai", "httpx",
                                                                  "app.taskqueue", "app.devices", "urllib"))]


def test_o_script_exige_um_criterio_e_nao_tem_banco_padrao(script: ModuleType, banco: Path) -> None:
    with pytest.raises(SystemExit):
        script.main(["--banco", str(banco)])                                         # nem --antes-da-055 nem --run-id
    with pytest.raises(SystemExit):
        script.main(["--antes-da-055"])                                               # sem --banco
    with pytest.raises(SystemExit):
        script.main(["--banco", str(banco), "--antes-da-055", "--run-id", "x"])      # os dois
    with pytest.raises(SystemExit):
        script.main(["--banco", str(banco), "--antes-da-055", "--aplicar", "--dry-run"])
    assert _rodar(script, banco.parent / "nao-existe.sqlite3", "--antes-da-055") == 2

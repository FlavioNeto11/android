"""Item 28.9 — a caixa de avisos do pedido (`pedido_avisos`, migração 072), SQLite (ou PostgreSQL com `TEST_DATABASE_URL`).

Prova `simulated` (`arquivo::teste`): relógio falso, aparelhos falsos, laço de verdade com o planejamento desligado. NADA
aqui prova o ambiente real.

Cobre: a migração 072 (CHECKs, UNIQUE, cascata, idempotência) e o vocabulário do domínio igual ao CHECK; o aviso GRAVADO
antes do evento e UM evento por fato (o mesmo fato vindo do laço e da API não duplica); `GET /avisos` lendo da tabela com
`lido=`; `POST /avisos/ler` idempotente; `avisos_nao_lidos` real na lista, no detalhe e no snapshot; `pede_atencao` com
aviso não lido; e os pontos de emissão novos: `orcamento_80`, `orcamento_esgotado` e `relatorio_pronto`.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from starlette.testclient import TestClient

from app import db as db_mod
from app.db import INTEGRITY_ERRORS
from app.main import create_app
from app.modules.pedidos.domain import avisos as dominio_avisos
from app.modules.pedidos.domain import tentativas

from .conftest import Harness
from .test_pedidos_api import _cliente, _criar, _eventos
from .test_pedidos_laco import _agora, _assentar, _estados, _horaria, _ocs, _runs
from .test_pedidos_modelo import _banco, _copia_ate, _pedido
from .test_pedidos_orcamento import _chamada, _fechada, _laco, _orcamento
from .test_pedidos_retentativa import _ciclo
from .test_travas import Relogio

UTC = timezone.utc
MIGRACAO = Path(db_mod.__file__).resolve().parents[1] / "migrations" / "072_pedido_avisos.sql"
#: A migração que tem o CHECK de `tipo` em vigor (076, 28.8). O vocabulário do domínio é o DELA.
MIGRACAO_ATUAL = MIGRACAO.parent / "076_pedido_avisos_gatilhos.sql"


def _tipos_do_check(caminho: Path) -> set[str]:
    check = re.search(r"tipo\s+TEXT NOT NULL CHECK \(tipo IN \((.*?)\)\)", caminho.read_text(encoding="utf-8"), re.S)
    assert check, caminho.name
    return set(re.findall(r"'([a-z_0-9]+)'", check.group(1)))
T0 = "2026-10-02T10:00:00.000Z"


@pytest_asyncio.fixture
async def h(harness: Harness, monkeypatch) -> Harness:
    monkeypatch.setattr(harness.state.runs, "_spawn_planning", lambda run_id: None)
    harness.state.pedidos.relogio = Relogio()
    return harness


def _ligar(h: Harness, laco) -> None:
    """O laço de teste com a MESMA fiação do `AppState`: avisa e notifica pela API (ponto único dos avisos)."""
    laco.avisar = h.state.pedidos_api.registrar_aviso
    laco.notificar = h.state.pedidos_api.publicar


def _linhas(h: Harness, **onde: object) -> list[dict]:
    filtro = " AND ".join(f"{k}=?" for k in onde) or "1=1"
    return h.state.db.query(f"SELECT * FROM pedido_avisos WHERE {filtro} ORDER BY criado_em, id", tuple(onde.values()))  # noqa: S608


def _tipos(h: Harness, pid: str = "ped1") -> list[str]:
    return sorted(r["tipo"] for r in _linhas(h, pedido_id=pid))


# ===================================================================== 1. migração 072
def test_migracao_072_aplica_sobre_o_banco_existente_e_o_vocabulario_do_dominio_e_o_do_check(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_ate(tmp_path, monkeypatch, "070_pedidos_memoria")
    db = _banco(tmp_path)
    assert db.migrate()[-1] == "070_pedidos_memoria"
    _pedido(db, "p1")
    shutil.copy2(MIGRACAO, destino / MIGRACAO.name)
    assert db.migrate() == ["072_pedido_avisos"] and db.divergencias() == [] and db.migrate() == []
    assert db.one("SELECT COUNT(*) AS n FROM pedido_avisos")["n"] == 0
    # A 072 trazia nove; a 076 (28.8) acrescentou dois. O domínio é o vocabulário da ÚLTIMA.
    assert _tipos_do_check(MIGRACAO) < set(dominio_avisos.TIPOS)
    assert _tipos_do_check(MIGRACAO_ATUAL) == set(dominio_avisos.TIPOS)
    db.close()


def test_migracao_076_reconstroi_sem_perder_aviso_e_aceita_os_tipos_do_28_8(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_ate(tmp_path, monkeypatch, "075_zzz_inexistente")
    for extra in destino.glob("07[6-9]_*.sql"):
        extra.unlink()
    db = _banco(tmp_path)
    db.migrate()
    _pedido(db, "p1")
    db.execute("INSERT INTO pedido_avisos(id, pedido_id, ocorrencia_id, tipo, nivel, mensagem, dados, requer_pessoa,"
               " chave_dedupe, criado_em, lido_em) VALUES ('a1','p1','o1','pergunta','warn','m','{\"x\": 1}',1,'k1',?,?)",
               (T0, T0))
    with pytest.raises(INTEGRITY_ERRORS):
        db.execute("INSERT INTO pedido_avisos(id, pedido_id, tipo, nivel, mensagem, chave_dedupe, criado_em)"
                   " VALUES ('a2','p1','eventos_perdidos','warn','m','k2',?)", (T0,))
    shutil.copy2(MIGRACAO_ATUAL, destino / MIGRACAO_ATUAL.name)
    assert db.migrate() == ["076_pedido_avisos_gatilhos"] and db.divergencias() == [] and db.migrate() == []
    velho = db.one("SELECT * FROM pedido_avisos WHERE id='a1'")
    assert (velho["ocorrencia_id"], velho["tipo"], velho["dados"], velho["requer_pessoa"], velho["lido_em"]) == (
        "o1", "pergunta", '{"x": 1}', 1, T0), "a reconstrução não perde nada"
    for i, tipo in enumerate(("eventos_perdidos", "condicao_atendida")):
        db.execute("INSERT INTO pedido_avisos(id, pedido_id, tipo, nivel, mensagem, chave_dedupe, criado_em)"
                   " VALUES (?,?,?,?,?,?,?)", (f"n{i}", "p1", tipo, "warn", "m", f"kn{i}", T0))
    with pytest.raises(INTEGRITY_ERRORS):
        db.execute("INSERT INTO pedido_avisos(id, pedido_id, tipo, nivel, mensagem, chave_dedupe, criado_em)"
                   " VALUES ('n9','p1','inventado','warn','m','kn9',?)", (T0,))
    with pytest.raises(INTEGRITY_ERRORS):
        db.execute("INSERT INTO pedido_avisos(id, pedido_id, tipo, nivel, mensagem, chave_dedupe, criado_em)"
                   " VALUES ('n8','p1','pergunta','warn','m','k1',?)", (T0,))        # chave_dedupe segue UNIQUE
    if db.dialect == "sqlite":
        assert db.scalar("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='ix_pedido_avisos_pedido'") == 1
    db.execute("DELETE FROM pedidos WHERE id='p1'")
    assert db.one("SELECT COUNT(*) AS n FROM pedido_avisos")["n"] == 0, "a cascada com o pedido continua"
    db.close()


def test_072_recusa_tipo_nivel_e_chave_repetida_e_morre_com_o_pedido(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    db.migrate()
    _pedido(db, "p1")

    def aviso(chave: str, tipo: str = "encerramento", nivel: str = "info") -> None:
        db.execute("INSERT INTO pedido_avisos(id, pedido_id, tipo, nivel, mensagem, chave_dedupe, criado_em)"
                   " VALUES (?,?,?,?,?,?,?)", (f"id-{chave}", "p1", tipo, nivel, "m", chave, T0))

    aviso("c1")
    with pytest.raises(INTEGRITY_ERRORS):
        aviso("c1")                                  # chave_dedupe UNIQUE
    with pytest.raises(INTEGRITY_ERRORS):
        aviso("c2", tipo="inventado")
    with pytest.raises(INTEGRITY_ERRORS):
        aviso("c3", nivel="grave")
    linha = db.one("SELECT * FROM pedido_avisos WHERE id='id-c1'")
    assert (linha["dados"], linha["requer_pessoa"], linha["lido_em"], linha["ocorrencia_id"]) == ("{}", 0, None, None)
    db.execute("DELETE FROM pedidos WHERE id='p1'")
    assert db.one("SELECT COUNT(*) AS n FROM pedido_avisos")["n"] == 0, "ON DELETE CASCADE"
    db.close()


def test_chaves_do_dominio_sao_estaveis_e_o_orcamento_80_leva_o_teto() -> None:
    assert dominio_avisos.id_do_aviso("x") == dominio_avisos.id_do_aviso("x") != dominio_avisos.id_do_aviso("y")
    assert dominio_avisos.chave_do_orcamento_80("p", 10) != dominio_avisos.chave_do_orcamento_80("p", 12)
    assert dominio_avisos.passou_de_80(8.0, 10.0) and not dominio_avisos.passou_de_80(7.99, 10.0)
    assert not dominio_avisos.passou_de_80(10.0, 10.0), "em 100% vale o esgotado"
    assert not dominio_avisos.passou_de_80(5.0, None) and not dominio_avisos.passou_de_80(1.0, 0.0)
    assert set(tentativas.AVISOS) == set(dominio_avisos.TIPOS), "um vocabulário só"


# ===================================================================== 2. gravar antes de emitir, e dedupe
async def test_o_aviso_e_gravado_e_so_entao_emitido_e_repetir_a_chave_nao_reemite(h: Harness) -> None:
    c = _cliente(h)
    pid = _criar(c).json()["id"]
    caixa = h.state.pedidos_api.caixa
    a = caixa.registrar(pedido_id=pid, tipo="relatorio_pronto", nivel=None, mensagem="Relatório pronto.", chave="k1",
                        dados={"sequencia": 1})
    assert a is not None and a["id"] == dominio_avisos.id_do_aviso("k1") and a["lido_em"] is None
    [linha] = _linhas(h)
    assert (linha["id"], linha["chave_dedupe"], linha["nivel"], linha["requer_pessoa"]) == (a["id"], "k1", "info", 0)
    [ev] = _eventos(h, "pedido.aviso")
    assert json.loads(ev["data"])["aviso"]["id"] == a["id"], "o evento leva o id da linha"
    assert caixa.registrar(pedido_id=pid, tipo="relatorio_pronto", nivel=None, mensagem="de novo", chave="k1") is None
    assert len(_linhas(h)) == 1 and len(_eventos(h, "pedido.aviso")) == 1
    assert caixa.registrar(pedido_id="nao-existe", tipo="encerramento", nivel=None, mensagem="m", chave="k2") is None
    with pytest.raises(ValueError):
        caixa.registrar(pedido_id=pid, tipo="inventado", nivel=None, mensagem="m", chave="k3")


async def test_pausa_vista_pelo_laco_e_pela_api_e_um_aviso_e_um_evento(h: Harness) -> None:
    """Antes da 072 o laço (`avisar`) e `_publicar_pedido` emitiam o MESMO fato duas vezes."""
    r, db = Relogio(), h.state.db
    r.t = datetime(2026, 10, 2, 12, 0, 5, tzinfo=UTC)
    h.state.pedidos_api.laco.relogio = r
    laco = _laco(h, r)
    _ligar(h, laco)
    _horaria(db, dtstart="2026-10-02T12:00:00", max_tentativas=1, pausa_por_falha=3)
    for _ in range(3):
        _ciclo(h, r, laco, "failed")
    assert db.scalar("SELECT estado FROM pedidos WHERE id='ped1'") == "pausado"
    assert _tipos(h) == ["pausa_automatica"], "o laço e a API dizem o mesmo fato com a mesma chave"
    assert len(_eventos(h, "pedido.aviso")) == 1
    laco.uma_volta()
    laco.uma_volta()
    assert len(_eventos(h, "pedido.aviso")) == 1 and len(_linhas(h)) == 1, "volta repetida não reemite"


async def test_ocorrencia_incerta_do_laco_e_gravada_como_requer_pessoa(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco = _laco(h, r)
    _ligar(h, laco)
    _agora(db, "ped1", r, max_tentativas=3)
    laco.uma_volta()
    run = _runs(db)[-1]
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,'uncertain',1)",
               (f"{run['id']}:android-01", run["id"], "android-01"))
    _assentar(db, run["id"], "completed_with_issues")
    laco.uma_volta()
    laco.uma_volta()
    [linha] = _linhas(h, pedido_id="ped1")
    assert (linha["tipo"], linha["requer_pessoa"], linha["ocorrencia_id"]) == ("ocorrencia_incerta", 1, _ocs(db)[0]["id"])
    assert len(_eventos(h, "pedido.aviso")) == 1
    assert h.state.pedidos_api.caixa.nao_lidos() == 0, "o que pede a pessoa mora nas Pendências, não no contador de avisos"


# ===================================================================== 3. rotas
async def test_get_avisos_le_a_tabela_com_lido_e_filtros_e_ler_e_idempotente(h: Harness) -> None:
    c = _cliente(h)
    pid = _criar(c).json()["id"]
    caixa = h.state.pedidos_api.caixa
    for i in range(3):
        caixa.registrar(pedido_id=pid, tipo="ocorrencia_perdida", nivel=None, mensagem=f"perdida {i}", chave=f"p{i}")
    caixa.registrar(pedido_id=pid, tipo="pergunta", nivel=None, mensagem="responda", chave="q")
    lista = c.get(f"/api/pedidos/avisos?pedido_id={pid}&requer_pessoa=0&lido=0").json()
    assert {a["mensagem"] for a in lista["items"]} == {"perdida 0", "perdida 1", "perdida 2"}
    assert lista["nao_lidos"] == 3 and lista["proximo_cursor"] is None
    assert all(a["pedido_titulo"] and a["requer_pessoa"] is False for a in lista["items"])
    assert len(c.get("/api/pedidos/avisos?requer_pessoa=1").json()["items"]) == 1
    pag = c.get("/api/pedidos/avisos?requer_pessoa=0&limit=2").json()
    assert len(pag["items"]) == 2 and pag["proximo_cursor"]
    resto = c.get(f"/api/pedidos/avisos?requer_pessoa=0&limit=2&cursor={pag['proximo_cursor']}").json()
    assert len(resto["items"]) == 1 and resto["proximo_cursor"] is None

    ids = [a["id"] for a in lista["items"]]
    r1 = c.post("/api/pedidos/avisos/ler", json={"ids": ids[:1]})
    assert r1.status_code == 200 and r1.json() == {"lidos": 1, "nao_lidos": 2}
    lido_em = _linhas(h, id=ids[0])[0]["lido_em"]
    assert lido_em
    assert c.post("/api/pedidos/avisos/ler", json={"ids": ids[:1]}).json() == {"lidos": 0, "nao_lidos": 2}
    assert _linhas(h, id=ids[0])[0]["lido_em"] == lido_em, "repetir não muda a data da leitura"
    assert len(c.get("/api/pedidos/avisos?lido=1").json()["items"]) == 1
    assert len(c.get("/api/pedidos/avisos?lido=0&requer_pessoa=0").json()["items"]) == 2

    d = c.get(f"/api/pedidos/{pid}").json()
    assert d["avisos_nao_lidos"] == 2
    assert c.get("/api/snapshot").json()["pedidos"]["avisos_nao_lidos"] == 2
    assert [p["avisos_nao_lidos"] for p in c.get("/api/pedidos").json()["items"]] == [2]

    todos = c.post("/api/pedidos/avisos/ler", json={"todos": True, "pedido_id": pid}).json()
    assert todos == {"lidos": 2, "nao_lidos": 0}
    assert c.post("/api/pedidos/avisos/ler", json={"todos": True}).json() == {"lidos": 0, "nao_lidos": 0}
    assert len(c.get("/api/pedidos/avisos?requer_pessoa=1&lido=0").json()["items"]) == 1, "`todos` não toca o que pede pessoa"
    assert c.get(f"/api/pedidos/{pid}").json()["avisos_nao_lidos"] == 0


async def test_ler_recusa_corpo_vazio_id_inexistente_e_pedido_inexistente_sem_gravar(h: Harness) -> None:
    c = _cliente(h)
    pid = _criar(c).json()["id"]
    a = h.state.pedidos_api.caixa.registrar(pedido_id=pid, tipo="encerramento", nivel=None, mensagem="m", chave="k")
    assert c.post("/api/pedidos/avisos/ler", json={}).status_code == 422
    assert c.post("/api/pedidos/avisos/ler", json={"ids": []}).status_code == 422
    assert c.post("/api/pedidos/avisos/ler", json={"ids": ["x"] * 201}).status_code == 422
    assert c.post("/api/pedidos/avisos/ler", json={"todos": True, "extra": 1}).status_code == 422
    r = c.post("/api/pedidos/avisos/ler", json={"ids": [a["id"], "nao-existe"]})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found" and r.json()["detail"]["kind"] == "aviso"
    assert _linhas(h)[0]["lido_em"] is None, "um id inexistente não deixa gravação pela metade"
    r = c.post("/api/pedidos/avisos/ler", json={"todos": True, "pedido_id": "nao-existe"})
    assert r.status_code == 404 and r.json()["detail"]["kind"] == "pedido"


async def test_pede_atencao_inclui_o_pedido_com_aviso_nao_lido(h: Harness) -> None:
    c = _cliente(h)
    pid = _criar(c).json()["id"]
    assert c.get("/api/pedidos?pede_atencao=1").json()["items"] == []
    h.state.pedidos_api.caixa.registrar(pedido_id=pid, tipo="orcamento_80", nivel=None, mensagem="80%", chave="k")
    assert [p["id"] for p in c.get("/api/pedidos?pede_atencao=1").json()["items"]] == [pid]
    c.post("/api/pedidos/avisos/ler", json={"todos": True})
    assert c.get("/api/pedidos?pede_atencao=1").json()["items"] == []


# ===================================================================== 4. pontos de emissão do 28.6 e do 28.7
async def test_orcamento_80_sai_uma_vez_ate_o_orcamento_subir(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    r.t = datetime(2026, 10, 2, 12, 10, 0, tzinfo=UTC)
    h.state.pedidos_api.laco.relogio = r
    _horaria(db, dtstart="2026-10-02T10:00:00", janela=4 * 3600, coalescer=0)
    laco = _laco(h, r)
    _ligar(h, laco)
    for i in range(8):
        _fechada(db, f"ant{i}", 1.0, minuto=i)                # gasto 8,0: 88,9% de um teto de 9,0 (cada ocorrência: 11%)
    _orcamento(db, total=9.0)
    laco.uma_volta()
    [linha] = _linhas(h, pedido_id="ped1", tipo="orcamento_80")
    assert (linha["nivel"], linha["requer_pessoa"], linha["chave_dedupe"]) == ("warn", 0, "orcamento_80:ped1:9.000000")
    assert json.loads(linha["dados"])["orcamento_total_usd"] == 9.0 and "80%" in linha["mensagem"]
    assert db.scalar("SELECT estado FROM pedidos WHERE id='ped1'") == "ativo"
    laco.uma_volta()
    assert len(_linhas(h, tipo="orcamento_80")) == 1 and len(_eventos(h, "pedido.aviso")) == 1, "uma vez só"
    _orcamento(db, total=10.0)                               # o orçamento subiu e o gasto ainda passa de 80% dele
    laco.uma_volta()
    assert len(_linhas(h, tipo="orcamento_80")) == 2 and len(_eventos(h, "pedido.aviso")) == 2


async def test_sem_chegar_a_80_ou_sem_orcamento_nenhum_aviso_de_orcamento(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    r.t = datetime(2026, 10, 2, 12, 10, 0, tzinfo=UTC)
    _horaria(db, dtstart="2026-10-02T10:00:00", janela=4 * 3600, coalescer=0)
    laco = _laco(h, r)
    _ligar(h, laco)
    _fechada(db, "ant0", 1.0)
    _orcamento(db, total=10.0)                                # 10%
    laco.uma_volta()
    laco.uma_volta()
    assert _linhas(h) == []


async def test_orcamento_esgotado_encerra_e_avisa_so_o_esgotado_sem_encerramento_em_dobro(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    r.t = datetime(2026, 10, 2, 12, 10, 0, tzinfo=UTC)
    h.state.pedidos_api.laco.relogio = r
    _horaria(db, dtstart="2026-10-02T10:00:00", janela=4 * 3600, coalescer=0)
    laco = _laco(h, r)
    _ligar(h, laco)
    _orcamento(db, total=1.0)
    laco.uma_volta()
    [run] = _runs(db)
    _chamada(db, run["id"], 1.20)
    _assentar(db, run["id"], "completed")
    laco.uma_volta()
    assert db.scalar("SELECT encerrado_motivo FROM pedidos WHERE id='ped1'") == "orcamento"
    assert [t for t in _tipos(h) if t != "relatorio_pronto"] == ["orcamento_esgotado"]
    [esg] = _linhas(h, tipo="orcamento_esgotado")
    assert esg["chave_dedupe"] == "orcamento_esgotado:ped1" and esg["nivel"] == "warn"
    laco.uma_volta()
    assert len(_linhas(h, tipo="orcamento_esgotado")) == 1


async def test_relatorio_pronto_sai_com_o_relatorio_de_encerramento_depois_do_commit(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    h.state.pedidos_api.laco.relogio = r
    laco = _laco(h, r)
    _ligar(h, laco)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [run] = _runs(db)
    _assentar(db, run["id"], "completed")
    laco.uma_volta()
    assert db.scalar("SELECT estado FROM pedidos WHERE id='ped1'") == "encerrado"
    [rel] = db.query("SELECT id FROM pedido_relatorios WHERE pedido_id='ped1'")
    assert _tipos(h) == ["encerramento", "relatorio_pronto"]
    [linha] = _linhas(h, tipo="relatorio_pronto")
    assert linha["chave_dedupe"] == f"relatorio_pronto:{rel['id']}" and json.loads(linha["dados"])["gatilho"] == "encerramento"
    assert "conteudo" not in linha["dados"]
    assert len(_eventos(h, "pedido.aviso")) == 2
    laco.uma_volta()
    assert len(_linhas(h)) == 2, "o encerrado não gera de novo"


async def test_gesto_da_pessoa_nao_gera_aviso_de_encerramento_mas_o_relatorio_final_avisa(h: Harness) -> None:
    c = _cliente(h)
    pid = _criar(c).json()["id"]
    assert c.post(f"/api/pedidos/{pid}/cancelar", json={"confirmar": True}).status_code == 200
    assert "encerramento" not in _tipos(h, pid)
    assert set(_tipos(h, pid)) <= {"relatorio_pronto"}

"""Item 28.10, F2 — o laço lê a dependência entre pedidos e desconta a reserva dos filhos do orçamento do pai.

Prova `simulated` (`arquivo::teste`): laço real em SQLite, `RunService` de verdade, relógio falso e provedor de IA
simulado, com os helpers de `test_pedidos_laco.py`. NADA aqui prova o ambiente real (a colaboração segue desligada de
fábrica e o 28.12 é quem liga o laço no central).

Cobre: `precisa_de_resultado` (sem prova segura a `devida`; com o `de` concluído despacha; a conclusão ANTIGA, de antes da
última ocorrência do filho, não vale de novo; falha, incerteza e cancelamento não são sucesso), `depois_de` (qualquer fim),
a espera vencida (`pulada` com o motivo exato e só o id), a retentativa que não é segurada, o orçamento (o pai com filho
vivo não despacha além do saldo livre; o filho terminado libera o que não gastou) e a flag desligada (o laço é o de hoje,
mesmo com a dependência gravada direto no banco).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.config import ColaboracaoCfg
from app.util import to_iso

from .conftest import Harness
from .test_pedidos_laco import _agora, _assentar, _estados, _horaria, _laco, _ocs, _pedido, _runs
from .test_travas import Relogio

UTC = timezone.utc
LIGADA = ColaboracaoCfg(enabled=True)


@pytest_asyncio.fixture
async def h(harness: Harness, monkeypatch) -> Harness:
    monkeypatch.setattr(harness.state.runs, "_spawn_planning", lambda run_id: None)
    return harness


def _colab(h: Harness, r: Relogio, **kw):
    return _laco(h, r, colaboracao=ColaboracaoCfg(enabled=True, **kw))


def _de(db, pid: str, criado: datetime) -> None:
    """Um pedido `de`/pai que só existe para ter ocorrências à mão (sem gatilho: o laço não o toca)."""
    _pedido(db, pid, criado=criado, estado="pausado")


def _oc(db, pid: str, estado: str, terminada: datetime | None, *, n: int = 1, custo: float = 0.0,
        previsto: datetime | None = None) -> None:
    """Uma ocorrência já gravada num estado, com o `terminada_em` que o teste manda."""
    quando = previsto or terminada or datetime(2026, 10, 2, 8, 0, tzinfo=UTC)
    db.execute("INSERT INTO pedido_ocorrencias(id, pedido_id, pedido_versao, previsto_para, chave, origem, estado, tentativa,"
               " custo_usd, criada_em, terminada_em) VALUES (?,?,1,?,?,'manual',?,0,?,?,?)",
               (f"{pid}-oc{n}", pid, to_iso(quando), f"{pid}-chave{n}", estado, custo, to_iso(quando),
                None if terminada is None else to_iso(terminada)))


def _dep(db, de: str, para: str, tipo: str) -> None:
    db.execute("INSERT INTO pedido_dependencias(de, para, tipo, criado_em) VALUES (?,?,?,?)", (de, para, tipo, "2026-10-02T00:00:00Z"))


# =============================================================================================== precisa_de_resultado
async def test_sem_ocorrencia_concluida_do_de_o_filho_fica_devida_e_com_ela_despacha(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _colab(h, r)
    _de(db, "pai", r.t - timedelta(hours=1))
    _agora(db, "ped1", r)
    _dep(db, "pai", "ped1", "precisa_de_resultado")

    res = laco.uma_volta()
    assert (res.despachadas, res.seguradas) == (0, 1)
    assert [o["estado"] for o in _ocs(db)] == ["devida"] and _runs(db) == [], "sem prova: devida e sem execução"
    assert laco.uma_volta().despachadas == 0 and _runs(db) == [], "a volta seguinte também espera"

    _oc(db, "pai", "concluida", r.t)                                   # o pai concluiu (depois da criação do filho)
    res = laco.uma_volta()
    assert (res.despachadas, res.seguradas) == (1, 0)
    assert [o["estado"] for o in _ocs(db)] == ["despachada"] and len(_runs(db)) == 1


@pytest.mark.parametrize("estado", ["falhou", "incerta", "cancelada", "pulada", "perdida"])
async def test_precisa_de_resultado_so_aceita_concluida(h: Harness, estado: str) -> None:
    """Falha e incerteza nunca contam como sucesso: o resultado que o filho usaria não existe."""
    r = Relogio()
    db = h.state.db
    laco = _colab(h, r)
    _de(db, "pai", r.t - timedelta(hours=1))
    _agora(db, "ped1", r)
    _dep(db, "pai", "ped1", "precisa_de_resultado")
    _oc(db, "pai", estado, r.t)
    res = laco.uma_volta()
    assert (res.despachadas, res.seguradas) == (0, 1) and _runs(db) == []
    assert [o["estado"] for o in _ocs(db)] == ["devida"]


async def test_prova_aberta_nao_comprova(h: Harness) -> None:
    """Uma ocorrência do `de` ainda `rodando` (sem `terminada_em`) não é prova de nada."""
    r = Relogio()
    db = h.state.db
    laco = _colab(h, r)
    _de(db, "pai", r.t - timedelta(hours=1))
    _agora(db, "ped1", r)
    _dep(db, "pai", "ped1", "precisa_de_resultado")
    _oc(db, "pai", "rodando", None)
    assert laco.uma_volta().despachadas == 0 and _runs(db) == []


async def test_a_conclusao_antiga_nao_vale_para_a_proxima_ocorrencia_do_filho(h: Harness) -> None:
    """A dependência é por janela: o `de` que concluiu ANTES do fim da última ocorrência do filho já foi usado."""
    r = Relogio()
    r.t = datetime(2026, 10, 2, 10, 10, 0, tzinfo=UTC)
    db = h.state.db
    laco = _colab(h, r)
    _de(db, "pai", r.t - timedelta(hours=3))
    _horaria(db, dtstart="2026-10-02T10:00:00", janela=4 * 3600, coalescer=0)         # filho: ped1, de hora em hora
    _dep(db, "pai", "ped1", "precisa_de_resultado")
    _oc(db, "pai", "concluida", datetime(2026, 10, 2, 10, 5, tzinfo=UTC))             # a conclusão do pai, às 10:05

    assert laco.uma_volta().despachadas == 1                                          # 10:00 usa a conclusão das 10:05
    [run] = _runs(db)
    r.t = datetime(2026, 10, 2, 10, 30, 0, tzinfo=UTC)
    _assentar(db, run["id"], "completed")
    assert laco.uma_volta().fechadas == 1                                             # o filho terminou às 10:30

    r.t = datetime(2026, 10, 2, 11, 10, 0, tzinfo=UTC)
    res = laco.uma_volta()                                                            # 11:00 devida; a prova é de ANTES das 10:30
    assert (res.despachadas, res.seguradas) == (0, 1)
    assert _estados(db)[:2] == [("10:00", "concluida"), ("11:00", "devida")] and len(_runs(db)) == 1

    _oc(db, "pai", "concluida", datetime(2026, 10, 2, 11, 5, tzinfo=UTC), n=2)        # o pai concluiu de novo, às 11:05
    assert laco.uma_volta().despachadas == 1
    assert _estados(db)[:2] == [("10:00", "concluida"), ("11:00", "despachada")] and len(_runs(db)) == 2


async def test_todos_os_de_precisam_estar_comprovados(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _colab(h, r)
    _de(db, "a", r.t - timedelta(hours=1))
    _de(db, "b", r.t - timedelta(hours=1))
    _agora(db, "ped1", r)
    _dep(db, "a", "ped1", "precisa_de_resultado")
    _dep(db, "b", "ped1", "precisa_de_resultado")
    _oc(db, "a", "concluida", r.t)
    assert laco.uma_volta().despachadas == 0, "só o `a` está comprovado"
    _oc(db, "b", "concluida", r.t)
    assert laco.uma_volta().despachadas == 1


# =============================================================================================== depois_de
@pytest.mark.parametrize("estado", ["concluida", "falhou", "incerta", "cancelada", "pulada", "perdida"])
async def test_depois_de_aceita_qualquer_fim_do_de(h: Harness, estado: str) -> None:
    r = Relogio()
    db = h.state.db
    laco = _colab(h, r)
    _de(db, "pai", r.t - timedelta(hours=1))
    _agora(db, "ped1", r)
    _dep(db, "pai", "ped1", "depois_de")
    assert laco.uma_volta().despachadas == 0, "o `de` ainda não terminou nada"
    _oc(db, "pai", estado, r.t)
    assert laco.uma_volta().despachadas == 1 and len(_runs(db)) == 1


# =============================================================================================== espera vencida
async def test_espera_vencida_vira_pulada_com_o_motivo_exato_e_so_o_id(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _colab(h, r)                                                   # espera_dependencia_s = 3600 (padrão)
    _de(db, "pedido-de-origem", r.t - timedelta(hours=1))
    _agora(db, "ped1", r)
    _dep(db, "pedido-de-origem", "ped1", "precisa_de_resultado")
    assert laco.uma_volta().seguradas == 1

    r.avancar(3590)                                                       # ainda dentro da espera
    assert laco.uma_volta().puladas == 0 and _ocs(db)[0]["estado"] == "devida"

    r.avancar(20)                                                         # 3610 s depois do `previsto_para`
    res = laco.uma_volta()
    assert res.puladas == 1 and res.despachadas == 0 and _runs(db) == []
    [o] = _ocs(db)
    assert o["estado"] == "pulada" and o["terminada_em"]
    assert o["motivo"] == "dependência não comprovada: pedido-de-origem", "o id do `de`, e mais nada"
    ped = db.one("SELECT titulo, objetivo FROM pedidos WHERE id='ped1'")
    assert ped["titulo"] not in o["motivo"] and ped["objetivo"] not in o["motivo"]


async def test_espera_configuravel(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _colab(h, r, espera_dependencia_s=60)
    _de(db, "pai", r.t - timedelta(hours=1))
    _agora(db, "ped1", r)
    _dep(db, "pai", "ped1", "depois_de")
    laco.uma_volta()
    r.avancar(120)
    assert laco.uma_volta().puladas == 1 and _ocs(db)[0]["motivo"] == "dependência não comprovada: pai"


async def test_com_varios_de_pendentes_o_motivo_leva_so_o_primeiro_id(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _colab(h, r, espera_dependencia_s=60)
    _de(db, "zeta", r.t - timedelta(hours=1))
    _de(db, "alfa", r.t - timedelta(hours=1))
    _agora(db, "ped1", r)
    _dep(db, "zeta", "ped1", "depois_de")
    _dep(db, "alfa", "ped1", "precisa_de_resultado")
    laco.uma_volta()
    r.avancar(120)
    laco.uma_volta()
    assert _ocs(db)[0]["motivo"] == "dependência não comprovada: alfa"


async def test_a_nova_tentativa_nao_e_segurada_pela_dependencia(h: Harness) -> None:
    """A repetição (28.5) é da ocorrência que já rodou: a dependência autorizou o primeiro despacho e não a segura de novo."""
    r = Relogio()
    db = h.state.db
    laco = _colab(h, r)
    _de(db, "pai", r.t - timedelta(hours=1))
    _agora(db, "ped1", r)
    _dep(db, "pai", "ped1", "precisa_de_resultado")
    _oc(db, "pai", "concluida", r.t)
    assert laco.uma_volta().despachadas == 1
    # a ocorrência falhou e voltou a `devida` como nova tentativa; o `de` não tem prova nova depois do fim da janela
    db.execute("UPDATE pedido_ocorrencias SET estado='devida', tentativa=1, run_id=NULL, terminada_em=? WHERE pedido_id='ped1'",
               (to_iso(r.t),))
    db.execute("UPDATE pedido_ocorrencias SET terminada_em=? WHERE id='pai-oc1'", (to_iso(r.t - timedelta(minutes=5)),))
    res = laco.uma_volta()
    assert (res.despachadas, res.seguradas, res.puladas) == (1, 0, 0)


# =============================================================================================== desligado
async def test_desligado_o_laco_e_identico_mesmo_com_a_dependencia_gravada_no_banco(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    laco = _laco(h, r)                                                    # colaboracao.enabled = False (o padrão)
    assert laco.cfg.colaboracao.enabled is False
    _de(db, "pai", r.t - timedelta(hours=1))
    _agora(db, "ped1", r)                                                 # espera um `de` que nunca concluiu...
    _agora(db, "ped2", r)                                                 # ...e o gêmeo sem dependência nenhuma
    _dep(db, "pai", "ped1", "precisa_de_resultado")

    res = laco.uma_volta()
    assert (res.despachadas, res.seguradas, res.puladas) == (2, 0, 0)
    assert [o["estado"] for o in _ocs(db, "ped1")] == [o["estado"] for o in _ocs(db, "ped2")] == ["despachada"]
    assert len(_runs(db)) == 2

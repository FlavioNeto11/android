"""28.37: cada backend publica os canais que liga; a saúde acusa quando o líder da trava `avisos` não liga um deles.

Dois "backends" aqui são dois `CanaisDaFrota` com donos diferentes sobre o MESMO banco do harness (como os dois
`Lideranca` de test_avisos_servico). O relógio é injetado para a cadência, a frescura e a varredura.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.db import loads
from app.main import create_app
from app.modules.avisos.infrastructure.canais_frota import FRESCA_S, PREFIXO, REGRAVAR_S, VELHA_S, CanaisDaFrota
from app.taskqueue.travas import AVISOS, Lideranca
from app.util import to_iso


def _cfg(avisos: bool, trello: bool) -> Any:
    return SimpleNamespace(file=SimpleNamespace(avisos=SimpleNamespace(enabled=avisos),
                                                trello=SimpleNamespace(enabled=trello)))


class _Relogio:
    def __init__(self, inicio: datetime):
        self.agora = inicio

    def __call__(self) -> datetime:
        return self.agora


def _backend(db: Any, dono: str, avisos: bool, trello: bool, relogio: Any = None) -> CanaisDaFrota:
    return CanaisDaFrota(db, _cfg(avisos, trello), dono=dono, relogio=relogio)


def _lider(db: Any, dono: str) -> None:
    db.execute("DELETE FROM travas WHERE nome=?", (AVISOS,))     # o harness pode ter a trava; aqui o líder é escolhido
    assert Lideranca(db, dono=dono).tomar(AVISOS) is not None


def _codigos(f: CanaisDaFrota) -> list[str]:
    return [p.code for p in f.problemas()]


async def test_configuracoes_iguais_nao_acusam(harness: Any) -> None:
    db = harness.state.db
    a, b = _backend(db, "bk-a", True, True), _backend(db, "bk-b", True, True)
    a.publicar(), b.publicar()
    _lider(db, "bk-a")
    assert _codigos(a) == [] and _codigos(b) == []


async def test_lider_sem_um_canal_que_outro_liga_acusa_dos_dois_lados(harness: Any) -> None:
    """O N1 da revisão do #345: o líder liga só o aviso, o outro só o Trello; o Trello está parado. Acusa no líder e
    no seguidor (o painel pode consultar qualquer um), com o canal certo e sem o nome de nenhum backend."""
    db = harness.state.db
    lider, outro = _backend(db, "maquina-lider", True, False), _backend(db, "maquina-outra", False, True)
    lider.publicar(), outro.publicar()
    _lider(db, "maquina-lider")
    for f in (lider, outro):
        [p] = f.problemas()
        assert p.code == "canais_divergentes" and "não liga o Trello" in p.message
        assert "maquina" not in p.message and "maquina" not in p.hint


async def test_sem_lider_ou_com_um_backend_so_nunca_acusa(harness: Any) -> None:
    db = harness.state.db
    so = _backend(db, "unico", False, True)
    so.publicar()
    db.execute("DELETE FROM travas WHERE nome=?", (AVISOS,))
    assert _codigos(so) == []                                     # sem líder: o laço toma na volta seguinte
    _lider(db, "unico")
    assert _codigos(so) == []                                     # o líder é ele mesmo


async def test_publicacao_velha_nao_conta(harness: Any) -> None:
    db = harness.state.db
    agora = db.agora()
    velho = _backend(db, "bk-velho", False, True, relogio=_Relogio(agora - timedelta(seconds=FRESCA_S + 10)))
    velho.publicar()
    lider = _backend(db, "bk-lider", True, False)
    lider.publicar()
    _lider(db, "bk-lider")
    assert _codigos(lider) == []                                  # o que só o velho ligava não conta: ele sumiu


async def test_cadencia_so_escreve_quando_muda_ou_a_cada_120s(harness: Any) -> None:
    db = harness.state.db
    rel = _Relogio(db.agora())
    f = CanaisDaFrota(db, _cfg(True, False), dono="bk-cad", relogio=rel)
    assert f.publicar() is True
    rel.agora += timedelta(seconds=20)
    assert f.publicar() is False                                   # a volta das travas, sem mudança: nada escreve
    rel.agora += timedelta(seconds=REGRAVAR_S)
    assert f.publicar() is True                                    # passou de 120 s: regrava a hora
    f.cfg = _cfg(True, True)
    assert f.publicar() is True                                    # mudou: escreve na hora


async def test_valor_so_com_booleanos_e_a_hora(harness: Any) -> None:
    db = harness.state.db
    f = _backend(db, "bk-json", True, False)
    f.publicar()
    valor = loads(db.scalar("SELECT value FROM settings WHERE key=?", (PREFIXO + "bk-json",)), {})
    assert set(valor) == {"avisos", "trello", "em"}
    assert valor["avisos"] is True and valor["trello"] is False and isinstance(valor["em"], str)


async def test_varredura_tira_as_publicacoes_de_mais_de_uma_hora(harness: Any) -> None:
    db = harness.state.db
    agora = db.agora()
    _backend(db, "bk-sumiu", True, True, relogio=_Relogio(agora - timedelta(seconds=VELHA_S + 60))).publicar()
    _backend(db, "bk-recente", True, True, relogio=_Relogio(agora - timedelta(seconds=600))).publicar()
    _backend(db, "bk-escreve", True, True).publicar()
    chaves = {r["key"] for r in db.query("SELECT key FROM settings WHERE key LIKE ?", (PREFIXO + "%",))}
    assert PREFIXO + "bk-sumiu" not in chaves and {PREFIXO + "bk-recente", PREFIXO + "bk-escreve"} <= chaves


async def test_encerramento_limpo_retira_a_propria_publicacao(tmp_path: Any) -> None:
    from .conftest import Harness

    hh = Harness(tmp_path, 1)
    await hh.boot()
    st = hh.state
    st._manter_travas()                                           # noqa: SLF001 - a volta do laço das travas
    chave = PREFIXO + hh.cfg.owner_id
    assert st.db.scalar("SELECT 1 FROM settings WHERE key=?", (chave,))
    # O banco fecha no fim do `stop`: a conferência roda logo depois do `retirar`, com ele ainda aberto.
    depois_de_retirar: list[object] = []
    original = st.canais_da_frota.retirar

    def espiao() -> None:
        original()
        depois_de_retirar.append(st.db.scalar("SELECT 1 FROM settings WHERE key=?", (chave,)))

    st.canais_da_frota.retirar = espiao  # type: ignore[method-assign]
    await st.stop()
    assert depois_de_retirar == [None]


async def test_a_chave_nao_aparece_nem_se_escreve_pela_configuracao(harness: Any) -> None:
    """Condição 1 da orquestradora: a tela de Configuração e o PUT de settings não veem nem aceitam a publicação."""
    st = harness.state
    st._manter_travas()                                           # noqa: SLF001
    assert st.db.scalar("SELECT 1 FROM settings WHERE key LIKE ?", (PREFIXO + "%",))
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    cliente = TestClient(app, client=("127.0.0.1", 123))
    lido = cliente.get("/api/settings")
    assert lido.status_code == 200 and "canais" not in lido.text
    escrito = cliente.put("/api/settings", json={PREFIXO + "x": {"avisos": True}})
    assert escrito.status_code == 400 and escrito.json()["detail"]["code"] == "unknown_setting"
    assert not st.db.scalar("SELECT 1 FROM settings WHERE key=?", (PREFIXO + "x",))


async def test_varredura_nao_apaga_a_publicacao_que_o_dono_regravou_depois_da_leitura(harness: Any) -> None:
    """C1 da revisão do #357: B lê a publicação velha de A, A volta e regrava, e só então B apaga. O DELETE vai pelo valor
    lido: a publicação nova de A fica."""
    db = harness.state.db
    agora = db.agora()
    rel_a = _Relogio(agora - timedelta(seconds=VELHA_S + 600))
    a = _backend(db, "bk-volta", True, True, relogio=rel_a)
    a.publicar()
    b = _backend(db, "bk-varre", True, True)
    leitura_velha = b._todas()                                   # noqa: SLF001 - a leitura de antes da regravação
    rel_a.agora = agora
    assert a.publicar() is True                                   # A voltou: regrava com a hora de agora
    b._todas = lambda: leitura_velha                              # type: ignore[method-assign]
    b._varrer(agora)                                              # noqa: SLF001
    valor = loads(db.scalar("SELECT value FROM settings WHERE key=?", (PREFIXO + "bk-volta",)), {})
    assert valor and valor["em"] == to_iso(agora)


async def test_replica_so_de_api_nao_publica_nao_retira_e_nao_se_conta(harness: Any) -> None:
    """C3 da revisão do #357: só quem roda o scheduler roda os laços dos canais. A réplica só de API, com flags diferentes
    das do líder, não acusa no `/health` dela um canal que nunca roda ali, e não apaga a publicação do mesmo dono."""
    db = harness.state.db
    lider = _backend(db, "bk-lider-api", True, False)
    lider.publicar()
    _lider(db, "bk-lider-api")
    replica = CanaisDaFrota(db, _cfg(False, True), dono="bk-replica", roda=False)
    assert replica.publicar() is False
    assert not db.scalar("SELECT 1 FROM settings WHERE key=?", (PREFIXO + "bk-replica",))
    assert _codigos(replica) == [] and _codigos(lider) == []
    mesmo_dono = CanaisDaFrota(db, _cfg(True, False), dono="bk-lider-api", roda=False)
    mesmo_dono.retirar()
    assert db.scalar("SELECT 1 FROM settings WHERE key=?", (PREFIXO + "bk-lider-api",))
    assert harness.state.canais_da_frota.roda is harness.cfg.roda_scheduler


async def test_retirada_sai_mesmo_quando_soltar_as_travas_falha(tmp_path: Any) -> None:
    """C2 da revisão do #357: a retirada tem `try` próprio no `stop()`; a falha das travas não a impede."""
    from .conftest import Harness

    hh = Harness(tmp_path, 1)
    await hh.boot()
    st = hh.state
    st._manter_travas()                                           # noqa: SLF001
    chave = PREFIXO + hh.cfg.owner_id

    def quebra() -> None:
        raise RuntimeError("travas fora")

    st.lideranca.soltar_todas = quebra                            # type: ignore[method-assign]
    depois_de_retirar: list[object] = []
    original = st.canais_da_frota.retirar

    def espiao() -> None:
        original()
        depois_de_retirar.append(st.db.scalar("SELECT 1 FROM settings WHERE key=?", (chave,)))

    st.canais_da_frota.retirar = espiao  # type: ignore[method-assign]
    await st.stop()
    assert depois_de_retirar == [None]

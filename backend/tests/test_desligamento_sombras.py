"""31.9, parte B da correção: o desligamento espera tudo o que ainda grava sombra ANTES do `db.close`, e a limpeza M1 a M3.

Prova `simulated`: portas e consumidores falsos, banco de teste, portões (`threading.Event`) no lugar de `sleep` longo. Nada
aqui toca rede, chave, aparelho ou o ambiente central.

- I1: a thread da sombra da intenção, a sombra da porta, o casamento da triagem e a volta do curador terminam (ou o prazo
  total estoura, sem travar) antes de o banco fechar;
- M1: o plano que falhou é logado (ler `exception()` já o tiraria do "never retrieved");
- M2: a triagem do curador com o envio fechado no código não faz nada;
- M3: a retenção não lê a tabela da sombra com a porta desligada e a tabela vazia.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

import pytest

from app import state as state_mod
from app.config import DecisaoFechadaCfg
from app.db import Database
from app.modules.learning.infrastructure.ligar_curador import LacoDoCurador
from app.modules.skills.domain.intent import IntentResolution, ResolutionStatus
from app.planning.decisao_fechada import privacidade
from app.planning.decisao_fechada.contrato import RespostaDeDecisao
from app.planning.decisao_fechada.curador import PERGUNTA_TRIAGEM, CuradorComTriagemEmSombra, TriagemDoCurador
from app.planning.decisao_fechada.decisores import DecisorFalso
from app.planning.decisao_fechada.porta import Porta
from app.planning.decisao_fechada.sombra import RepositorioDeSombra, observador_de_sombra
from app.taskqueue.sombra_intencao import SombraDaIntencao

from .conftest import Harness
from .test_decisao_fechada_curador import CuradorFalso, Pedido, _dossie


class _ConsumidorComPortao:
    """Segura a thread da sombra da intenção num portão: está "em curso" até o teste liberar."""

    def __init__(self, ao_terminar: Any = None) -> None:
        self.iniciou = threading.Event()
        self.portao = threading.Event()
        self.terminou = threading.Event()
        self._ao_terminar = ao_terminar

    def ativo(self) -> bool:
        return True

    def observar(self, **_: Any) -> None:
        self.iniciou.set()
        self.portao.wait(5.0)
        if self._ao_terminar is not None:
            self._ao_terminar()
        self.terminou.set()


class _DecisorComPortao(DecisorFalso):
    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.iniciou = threading.Event()
        self.portao = threading.Event()

    def decidir(self, pedido, timeout_s):  # type: ignore[no-untyped-def]
        self.iniciou.set()
        self.portao.wait(5.0)
        return super().decidir(pedido, timeout_s)


def _sombra_da_intencao(consumidor: Any) -> SombraDaIntencao:
    return SombraDaIntencao(consumidor, resolver=lambda cmd, p: IntentResolution(status=ResolutionStatus.NO_MATCH),
                            catalogo=lambda: ())


# ------------------------------------------------------------------ SombraDaIntencao.aguardar (I1)
async def test_aguardar_espera_a_thread_em_curso_em_vez_de_so_cancelar() -> None:
    c = _ConsumidorComPortao()
    sombra = _sombra_da_intencao(c)
    sombra.agendar("r-1", lambda: ("abrir o app", [None], None))
    await asyncio.to_thread(c.iniciou.wait, 5.0)
    threading.Timer(0.05, c.portao.set).start()
    await sombra.aguardar(5.0)
    assert c.terminou.is_set()                                                # esperou a thread, não só o `Task`


async def test_aguardar_respeita_o_prazo_e_deixa_o_resto_para_o_cancelar() -> None:
    c = _ConsumidorComPortao()
    sombra = _sombra_da_intencao(c)
    sombra.agendar("r-1", lambda: ("abrir o app", [None], None))
    await asyncio.to_thread(c.iniciou.wait, 5.0)
    t0 = time.monotonic()
    await sombra.aguardar(0.2)
    assert 0.15 <= time.monotonic() - t0 < 2.0 and not c.terminou.is_set()    # estourou o prazo e não cancelou nada
    [solta] = list(sombra._soltas)                                            # noqa: SLF001
    assert not solta.cancelled()
    sombra.cancelar()
    c.portao.set()
    await sombra.aguardar(5.0)


# ------------------------------------------------------------------ AppState.stop (I1)
def _espiar_o_close(st: Any, ao_fechar: Any) -> None:
    fechar = st.db.close

    def close() -> None:
        ao_fechar()
        fechar()

    st.db.close = close


async def test_stop_espera_a_intencao_e_a_porta_antes_de_fechar_o_banco(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)
    # A triagem do curador com uma sombra em curso na porta (o decisor está no portão).
    decisor = _DecisorComPortao({PERGUNTA_TRIAGEM: RespostaDeDecisao(escolha="opt:manter",
                                                                     probabilidades={"opt:manter": 0.9}, confianca=0.9)})
    st.decisao_fechada.decisor = decisor
    st.decisao_fechada.cfg = DecisaoFechadaCfg(enabled=True, consumidores={"curador": "shadow"})
    triagem = st._triagem_do_curador                                         # noqa: SLF001
    CuradorComTriagemEmSombra(CuradorFalso(decisao="rebaixar"), triagem).revisar(Pedido(_dossie()))
    await asyncio.to_thread(decisor.iniciou.wait, 5.0)
    # E uma thread da sombra da intenção que ainda não terminou (e grava ao terminar).
    gravou: list[str] = []
    intencao = _ConsumidorComPortao(ao_terminar=lambda: gravou.append("intencao"))
    st.runs.sombra_intencao = _sombra_da_intencao(intencao)
    st.runs.sombra_intencao.agendar("r-1", lambda: ("abrir o app", [None], None))
    await asyncio.to_thread(intencao.iniciou.wait, 5.0)

    no_close: dict[str, object] = {}

    def ao_fechar() -> None:
        no_close["intencao"] = list(gravou)
        no_close["linhas"] = [dict(r) for r in st.db.query("SELECT escolha, decisao_real FROM decisao_fechada_sombra")]

    _espiar_o_close(st, ao_fechar)
    threading.Timer(0.2, lambda: (decisor.portao.set(), intencao.portao.set())).start()
    await harness.crash()                                                    # `stop()` e solta o estado
    assert no_close["intencao"] == ["intencao"]                              # a thread terminou ANTES do close
    # a sombra foi gravada antes do close; a decisão real não casa na hora (I2: vem de `learning_reviews` no 31.10)
    assert no_close["linhas"] == [{"escolha": "opt:manter", "decisao_real": None}]


async def test_stop_tem_um_prazo_total_e_nao_trava_no_que_nao_termina(harness: Harness,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    monkeypatch.setattr(state_mod, "ESPERA_DE_SOMBRAS_S", 0.4)
    preso = _ConsumidorComPortao()
    st.runs.sombra_intencao = _sombra_da_intencao(preso)
    st.runs.sombra_intencao.agendar("r-1", lambda: ("abrir o app", [None], None))
    await asyncio.to_thread(preso.iniciou.wait, 5.0)
    fechado: list[float] = []
    _espiar_o_close(st, lambda: fechado.append(time.monotonic()))
    t0 = time.monotonic()
    await harness.crash()
    assert fechado and fechado[0] - t0 < 10.0                                # fechou o banco mesmo com a thread presa
    assert not preso.terminou.is_set()
    preso.portao.set()                                                       # libera a thread para o teste não vazá-la


# ------------------------------------------------------------------ a volta do curador (I1)
class _CuradorFalso:
    intervalo_s = 60

    def __init__(self) -> None:
        self.dentro = threading.Event()
        self.portao = threading.Event()
        self.voltas = 0
        self.parado = False

    def uma_volta(self, lider: Any) -> None:
        self.voltas += 1
        self.dentro.set()
        self.portao.wait(5.0)

    def parar(self) -> None:
        self.parado = True


def test_laco_do_curador_espera_a_volta_em_curso_com_prazo_e_nao_inicia_outra() -> None:
    falso = _CuradorFalso()
    laco = LacoDoCurador(falso)                                              # type: ignore[arg-type]
    t = threading.Thread(target=laco._volta, args=(lambda: 1,))              # noqa: SLF001
    t.start()
    assert falso.dentro.wait(5.0)
    t0 = time.monotonic()
    assert laco.parar(0.2) is False and 0.15 <= time.monotonic() - t0 < 2.0   # a volta segue no hub: prazo estourado
    assert falso.parado
    falso.portao.set()
    t.join(5.0)
    assert laco.parar(2.0) is True                                           # ociosa
    assert laco._volta(lambda: 1) is None and falso.voltas == 1             # noqa: SLF001 - volta nova nem começa


# ------------------------------------------------------------------ M1: o plano que falhou é logado
async def test_plano_com_excecao_e_logado_e_nao_observa_nada(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    chamados: list[str] = []
    runs = harness.state.runs
    runs._intencao_em_sombra = chamados.append                                # type: ignore[method-assign]  # noqa: SLF001
    falhou: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    falhou.set_exception(RuntimeError("provedor caiu no plano"))
    with caplog.at_level(logging.ERROR, logger="poc.runs"):
        runs._depois_do_plano(falhou, "r-falhou")                             # noqa: SLF001
    assert chamados == []
    [registro] = [r for r in caplog.records if r.name == "poc.runs" and r.levelno == logging.ERROR]
    assert "r-falhou" in registro.getMessage()
    assert registro.exc_info is not None and "provedor caiu no plano" in str(registro.exc_info[1])


# ------------------------------------------------------------------ M2: o envio fechado desliga a triagem do curador
def test_triagem_do_curador_com_o_envio_fechado_nao_faz_nada(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    db = Database(tmp_path / "triagem-fechada.sqlite3")
    db.migrate()
    repo = RepositorioDeSombra(db)
    decisor = DecisorFalso()
    cfg = DecisaoFechadaCfg(enabled=True, consumidores={"curador": "shadow"})
    porta = Porta(decisor, cfg=cfg, observador=observador_de_sombra(repo))
    triagem = TriagemDoCurador(porta)
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", False)    # o interruptor fechado (aberto desde o 31.17)
    assert not triagem.ativo()
    CuradorComTriagemEmSombra(CuradorFalso(), triagem).revisar(Pedido(_dossie()))
    porta.aguardar_sombras()
    assert decisor.chamadas == [] and db.query("SELECT id FROM decisao_fechada_sombra") == []   # nem recusa gravada
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)
    assert triagem.ativo()                                                    # aprovado o envio, volta a valer a config
    db.close()


# ------------------------------------------------------------------ M3: a retenção não lê a tabela à toa
async def test_retencao_pula_a_sombra_com_a_porta_desligada_e_a_tabela_vazia(harness: Harness,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    chamadas: list[int] = []
    monkeypatch.setattr(st.decisao_sombra, "aplicar_retencao", lambda dias: chamadas.append(dias) or 0)
    assert not state_mod.transparencia.consumidores_ativos(st.cfg.file.ai.decisao_fechada)
    assert st.db.scalar("SELECT COUNT(*) FROM decisao_fechada_sombra") == 0
    st._purgar_demais_tabelas("2026-01-01T00:00:00Z")                         # noqa: SLF001
    assert chamadas == []                                                     # desligada e vazia: nada lido
    # Porta ligada: volta a rodar (o agregado diário e o prazo valem).
    monkeypatch.setattr(st.cfg.file.ai, "decisao_fechada",
                        DecisaoFechadaCfg(enabled=True, consumidores={"curador": "shadow"}))
    st._purgar_demais_tabelas("2026-01-01T00:00:00Z")                         # noqa: SLF001
    assert chamadas == [180]

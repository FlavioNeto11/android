"""31.220: o laço do sistema avança a operação sem leitura externa (`modules/operacoes/infrastructure/laco.py`).

`simulated`: harness na porta 5640, relógio falso (o `dormir` do laço conta as voltas e para o teste). O que se prova:
- desligado (`operacao_laco_s: 0`, o padrão), o laço não lê nada e só confere a configuração de 30 em 30 s;
- ligado, a operação com o estado atrasado fecha sem nenhum GET, com um `operacao.encerrada` só; a volta seguinte não
  grava nem avisa, e sem operação aberta a volta não lê nada;
- o `_anotar` é condicional no SQL: duas leituras com a mesma linha velha (o laço e um GET juntos) avisam uma vez.
"""
from __future__ import annotations

import secrets as pysecrets

import pytest

from app.models import ProfileCreate
from app.modules.operacoes.infrastructure.laco import OCIOSO_S, LacoDasOperacoes, abertas
from app.modules.operacoes.infrastructure.servico import AlvoPedido, PedidoDeOperacao, ServicoDeOperacoes

from .conftest import COMMAND, Harness

pytestmark = pytest.mark.asyncio
APP = "qa-messenger"


class _Parar(Exception):
    pass


def _servico(h: Harness) -> ServicoDeOperacoes:
    st = h.state
    assert st is not None
    return ServicoDeOperacoes(st.db, st.runs, st.social_repo, st.approval_service, st.settings.get, st.bus,
                              st.cfg.file.ai.prices)


def _operacao_atrasada(h: Harness, chave: str) -> str:
    """Uma operação cujo alvo (conta sem sessão: para em `sessao`) ficou gravado como pendente e aberta: a próxima
    leitura tem o estágio e o fechamento a gravar."""
    st = h.state
    assert st is not None
    pid = st.social.create_profile(ProfileCreate(username=f"laco.{pysecrets.token_hex(3)}", first_name="Laco",
                                                 last_name="Teste")).id
    st.social_repo.create_account(pid, app_id=APP, handle=f"qa-laco-{pysecrets.token_hex(2)}")
    op = _servico(h).criar(PedidoDeOperacao(command=COMMAND, app_id=APP, alvos=[AlvoPedido(pid)],
                                            acao_final="preparar", idempotency_key=chave, max_usd=1.0))
    st.db.execute("UPDATE operacao_alvos SET estagio='persona', estado='pendente', motivo=NULL WHERE operacao_id=?",
                  (op["id"],))
    st.db.execute("UPDATE operacoes SET status='em_curso', finished_at=NULL WHERE id=?", (op["id"],))
    return str(op["id"])


def _eventos(h: Harness, kind: str) -> int:
    assert h.state is not None
    return int(h.state.db.scalar("SELECT COUNT(*) FROM events WHERE kind=?", (kind,)) or 0)


def _laco(h: Harness, voltas: int, dormidas: list[float]) -> LacoDasOperacoes:
    st = h.state
    assert st is not None

    async def dormir(s: float) -> None:
        dormidas.append(s)
        if len(dormidas) >= voltas:
            raise _Parar

    return LacoDasOperacoes(st.db, lambda: _servico(h), lambda: int(st.settings.get().operacao_laco_s),
                            dormir=dormir)


async def test_desligado_por_padrao_o_laco_nao_le_nada(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    assert st.settings.get().operacao_laco_s == 0
    op_id = _operacao_atrasada(harness, "teste-laco-desligado")
    antes = dict(st.db.one("SELECT * FROM operacoes WHERE id=?", (op_id,)))
    dormidas: list[float] = []
    laco = _laco(harness, 2, dormidas)
    with pytest.raises(_Parar):
        await laco.laco()
    assert dormidas == [OCIOSO_S, OCIOSO_S] and laco.voltas == 0
    assert dict(st.db.one("SELECT * FROM operacoes WHERE id=?", (op_id,))) == antes


async def test_ligado_fecha_a_operacao_sem_leitura_externa_e_nao_repete(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    op_id = _operacao_atrasada(harness, "teste-laco-ligado")
    encerradas, alvos = _eventos(harness, "operacao.encerrada"), _eventos(harness, "operacao.alvo")
    st.settings.update({"operacao_laco_s": 5})
    dormidas: list[float] = []
    laco = _laco(harness, 1, dormidas)
    with pytest.raises(_Parar):
        await laco.laco()
    op = st.db.one("SELECT * FROM operacoes WHERE id=?", (op_id,))
    assert dormidas == [5] and laco.voltas == 1
    assert op["finished_at"] and op["status"] == "concluida_com_bloqueios"
    assert st.db.scalar("SELECT estado FROM operacao_alvos WHERE operacao_id=?", (op_id,)) == "bloqueado"
    assert _eventos(harness, "operacao.encerrada") == encerradas + 1
    assert _eventos(harness, "operacao.alvo") == alvos + 1
    # Fechada, ela sai das abertas: a volta seguinte não lê nada, não grava nem avisa.
    assert op_id not in abertas(st.db)
    assert laco.uma_volta() == 0
    assert _eventos(harness, "operacao.encerrada") == encerradas + 1


async def test_duas_leituras_com_a_mesma_linha_velha_avisam_uma_vez(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    op_id = _operacao_atrasada(harness, "teste-laco-corrida")
    velha = st.db.one("SELECT * FROM operacao_alvos WHERE operacao_id=?", (op_id,))
    antes = _eventos(harness, "operacao.alvo")
    s1, s2 = _servico(harness), _servico(harness)
    for s in (s1, s2):     # o laço e um GET que leram a linha antes de qualquer um gravar
        s._anotar(op_id, velha, "sessao", "bloqueado", "sem sessão")  # noqa: SLF001
    assert _eventos(harness, "operacao.alvo") == antes + 1


async def test_dois_fechamentos_com_a_mesma_operacao_velha_avisam_uma_vez(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    op_id = _operacao_atrasada(harness, "teste-laco-fecha-junto")
    velha = st.db.one("SELECT * FROM operacoes WHERE id=?", (op_id,))
    antes = _eventos(harness, "operacao.encerrada")
    fins = [s._fechar(velha, "concluida_com_bloqueios", {}) for s in (_servico(harness), _servico(harness))]  # noqa: SLF001
    assert fins[0] and fins[1] == fins[0]      # quem perde a corrida devolve a hora gravada (achado do PR 494)
    assert _eventos(harness, "operacao.encerrada") == antes + 1


async def test_o_fechamento_com_leitura_velha_nao_desfaz_o_cancelamento(harness: Harness) -> None:
    """Achado do Codex no PR 494: o cancelar de outra réplica grava `cancelada` depois que esta leu a operação aberta;
    o fechamento desta não pode trocá-lo por `concluida*`."""
    st = harness.state
    assert st is not None
    op_id = _operacao_atrasada(harness, "teste-laco-cancelada")
    velha = st.db.one("SELECT * FROM operacoes WHERE id=?", (op_id,))
    st.db.execute("UPDATE operacoes SET status='cancelada' WHERE id=?", (op_id,))
    antes = _eventos(harness, "operacao.encerrada")
    assert _servico(harness)._fechar(velha, "concluida_com_bloqueios", {}) is None  # noqa: SLF001
    assert st.db.one("SELECT status, finished_at FROM operacoes WHERE id=?", (op_id,))["status"] == "cancelada"
    assert _eventos(harness, "operacao.encerrada") == antes
    # a leitura seguinte vê o cancelamento e fecha como cancelada
    assert _servico(harness).ler(op_id)["status"] == "cancelada"
    assert st.db.one("SELECT finished_at FROM operacoes WHERE id=?", (op_id,))["finished_at"]


async def test_a_falha_numa_operacao_nao_para_as_outras(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    ruim = _operacao_atrasada(harness, "teste-laco-ruim")
    boa = _operacao_atrasada(harness, "teste-laco-boa")
    original = ServicoDeOperacoes.ler

    def ler(self: ServicoDeOperacoes, op_id: str) -> dict[str, object]:
        if op_id == ruim:
            raise RuntimeError("dado ruim")
        return original(self, op_id)

    monkeypatch.setattr(ServicoDeOperacoes, "ler", ler)
    laco = _laco(harness, 1, [])
    assert laco.uma_volta() == 2
    assert st.db.one("SELECT finished_at FROM operacoes WHERE id=?", (boa,))["finished_at"]
    assert st.db.one("SELECT finished_at FROM operacoes WHERE id=?", (ruim,))["finished_at"] is None

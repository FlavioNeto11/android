"""31.50, lado da Canais: o lembrete `pendencia.vence_em` (produtor da Jev, #313, adendo v1.33) vira aviso no molde do
28.31: o que vence, onde e quando, o que acontece se vencer, "Espera você" e o link. Nível 1, sai na hora, com rajada.

Prova `simulated` (`arquivo::teste`): o montador puro e o serviço com banco de teste e canal falso.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.avisos.application.entrada import rotear
from app.modules.avisos.domain.mensagem import (
    AGORA,
    NIVEL_POR_TIPO,
    PRECISA_DE_VOCE,
    aviso_de_evento,
    entrega_do_tipo,
    titulo_agrupado,
)
from app.modules.avisos.infrastructure.servico import KINDS_QUE_AVISAM
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial

from .test_avisos_servico import AQUI, CanalFalso, Relogio, _backend, _cfg, _run

PAINEL = "https://painel.exemplo/central"
REDIGIR = TriagemDeCredencial().redigir
CHAVE = "vencimento:lembrete:obj-1:2026-10-03T22:30:00.000Z"


def _dados(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {"o_que": "aprovacao", "run_id": "r1", "objective_id": "obj-1", "aparelho": "android-12",
                               "acao": "OPEN_MAIL_INBOX", "etapa": None, "vence_em": "2026-10-04T22:30:00.000Z",
                               "acontece_se_vencer": "cancelado pelo sistema", "chave": CHAVE, "regra": "31.50"}
    base.update(kw)
    return base


def test_o_lembrete_diz_o_que_vence_onde_quando_e_o_que_acontece() -> None:
    a = aviso_de_evento("pendencia.vence_em", _dados(), 7, PAINEL)
    assert a is not None
    assert (a.chave, a.tipo, a.nivel) == (CHAVE, "pendencia.vence_em", PRECISA_DE_VOCE)
    assert entrega_do_tipo(a.tipo) == AGORA
    assert a.titulo == "ANA: ⏳ A aprovação no android-12 vence em até 2 h (22:30Z)"
    # Sem o nome do catálogo, a chave como o produtor manda (`steps.capability`, em maiúsculas).
    assert a.corpo.split("\n") == ["Etapa que espera: OPEN_MAIL_INBOX.", "Se vencer: cancelado pelo sistema.",
                                   "Espera você: decida na caixa de Pendências antes disso."]
    assert a.link is not None and a.link.startswith(PAINEL)


@pytest.mark.parametrize(("o_que", "sujeito", "gesto"), [
    ("objetivo", "O objetivo parado", "responda"), ("execucao", "A pergunta da execução", "responda"),
    ("outra-coisa", "Uma pendência", "responda")])
def test_cada_tipo_de_item_tem_o_seu_sujeito(o_que: str, sujeito: str, gesto: str) -> None:
    a = aviso_de_evento("pendencia.vence_em", _dados(o_que=o_que), 7)
    assert a is not None and a.titulo.startswith(f"ANA: ⏳ {sujeito}")
    assert a.corpo.endswith(f"Espera você: {gesto} na caixa de Pendências antes disso.")


def test_execucao_com_um_aparelho_diz_qual_e_com_varios_nao() -> None:
    um = aviso_de_evento("pendencia.vence_em", _dados(o_que="execucao", aparelho=None, aparelhos=["android-09"]), 1)
    varios = aviso_de_evento("pendencia.vence_em",
                             _dados(o_que="execucao", aparelho=None, aparelhos=["android-09", "android-10"]), 1)
    assert um is not None and " no android-09 " in um.titulo
    assert varios is not None and " no " not in varios.titulo.removeprefix("ANA: ")


def test_com_o_nome_do_catalogo_sai_o_nome_e_nao_a_chave() -> None:
    a = aviso_de_evento("pendencia.vence_em", _dados(acao="SEND_MESSAGE", acao_nome="Mandar mensagem"), 1,
                        redigir=REDIGIR, nomes=["Bruno Lima", "Bruno"])
    assert a is not None and "Etapa que espera: Mandar mensagem." in a.corpo and "SEND_MESSAGE" not in a.corpo
    # O nome que não passa inteiro pelos filtros não sai: fica a chave.
    b = aviso_de_evento("pendencia.vence_em", _dados(acao="SEND_MESSAGE", acao_nome="Falar com o Bruno"), 1,
                        redigir=REDIGIR, nomes=["Bruno Lima", "Bruno"])
    assert b is not None and "Etapa que espera: SEND_MESSAGE." in b.corpo and "Bruno" not in b.corpo
    sujo = aviso_de_evento("pendencia.vence_em", _dados(acao="SEND_MESSAGE", acao_nome="Mandar a @fulano"), 1,
                           redigir=REDIGIR)
    assert sujo is not None and "fulano" not in sujo.corpo


def test_o_servico_poe_o_nome_do_catalogo_e_avisa_sem_a_chave(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    servico._redigir = REDIGIR                                         # noqa: SLF001
    servico.nome_da_capability = {"SEND_MESSAGE": "Mandar mensagem"}.get
    _run(banco, "rc", chave="k-comum")
    assert servico.enfileirar_evento("pendencia.vence_em", _dados(run_id="rc", acao="SEND_MESSAGE"), 1) is True
    assert "Etapa que espera: Mandar mensagem." in str(banco.scalar("SELECT corpo FROM avisos_entregas"))
    with caplog.at_level("WARNING"):
        assert servico.enfileirar_evento("pendencia.vence_em", _dados(run_id="rc", chave=None), 2) is False
    assert "sem a chave do produtor" in caplog.text


def test_sem_a_chave_do_produtor_nao_ha_aviso() -> None:
    assert aviso_de_evento("pendencia.vence_em", _dados(chave=None), 1) is None


def test_so_a_chave_do_catalogo_sai_nunca_texto_livre_nem_a_etapa() -> None:
    a = aviso_de_evento("pendencia.vence_em", _dados(acao="Mandar para @fulano o texto", etapa="titulo com @fulano",
                                                     command="comentar @fulano"), 1)
    assert a is not None
    assert "fulano" not in a.titulo + a.corpo and "Etapa que espera" not in a.corpo
    sem = aviso_de_evento("pendencia.vence_em", _dados(acao=None, vence_em=None), 1)
    assert sem is not None and sem.titulo.endswith("vence em até 2 h")


def test_o_tipo_entra_nos_que_avisam_no_nivel_e_na_rajada() -> None:
    assert "pendencia.vence_em" in KINDS_QUE_AVISAM
    assert NIVEL_POR_TIPO["pendencia.vence_em"] == PRECISA_DE_VOCE
    assert titulo_agrupado("pendencia.vence_em", 3) == "ANA: 3 pendências vencem nas próximas 2 h"


def test_responder_ao_lembrete_nao_vira_pedido() -> None:
    i = rotear("ok, vou ver", fato=CHAVE)
    assert i.tipo == "desconhecida"


def test_lembrete_de_execucao_do_sistema_nao_avisa_e_a_aprovacao_de_lote_avisa(tmp_path: Path) -> None:
    """A mesma regra `_e_de_prova` do `run.updated` e do `approval.pending`: prova, validação e lote calam; a aprovação
    que um lote abre avisa (só o dono decide)."""
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    _run(banco, "rp", chave="k-prova", prova="fluxo-1")
    _run(banco, "rl", chave="lote:canais:31.50")
    _run(banco, "rc", chave="k-comum")

    def lembra(run_id: str, o_que: str, n: int) -> bool:
        return servico.enfileirar_evento("pendencia.vence_em",
                                         _dados(run_id=run_id, o_que=o_que, chave=f"vencimento:lembrete:{run_id}:{n}"), n)

    assert lembra("rp", "execucao", 1) is False
    assert lembra("rp", "aprovacao", 2) is False
    assert lembra("rl", "execucao", 3) is False
    assert banco.scalar("SELECT COUNT(*) FROM avisos_entregas") == 0
    assert lembra("rl", "aprovacao", 4) is True
    assert lembra("rc", "objetivo", 5) is True
    assert banco.scalar("SELECT COUNT(*) FROM avisos_entregas") == 2

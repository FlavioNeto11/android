"""A conta observada que a etapa comprovada grava (`instances.account_evidence`) é a frase que nomeia a conta, nunca
o seletor cru da prova pela árvore local (validação do deploy 8: android-06 "diverge" com `selector:…{username}`) nem a
prova `seletor …: N elemento(s)` (validação do deploy 9, I2)."""
from __future__ import annotations

from app.devices.conta_observada import evidencia_legivel
from app.taskqueue.executor import evidencia_da_conta

from .conftest import Harness

SELETOR = "pós-condição comprovada pela árvore local, sem IA (selector:id=action_bar_title|text=={username})"


def test_prova_por_seletor_cru_grava_a_pos_condicao_que_nomeia_a_conta() -> None:
    assert evidencia_da_conta("lucas.teste", "Perfil de @lucas.teste aberto", SELETOR) == "Perfil de @lucas.teste aberto"


def test_prova_que_nomeia_a_conta_e_a_evidencia() -> None:
    texto = "a tela mostra o perfil @lucas.teste"
    assert evidencia_da_conta("lucas.teste", "perfil aberto", texto) == texto


def test_sem_rotulo_ou_sem_a_conta_nada_e_gravado() -> None:
    assert evidencia_da_conta(None, "Perfil de @lucas.teste aberto", SELETOR) is None
    assert evidencia_da_conta("bruno", "Perfil de @lucas.teste aberto", SELETOR) is None


# ------------------------------------------------------------------ I2 da validação do deploy 9
PROVA_QA = "seletor id=com.pocqa.messenger:id/account_label|text=qa-user-10: 1 elemento(s)"


def test_prova_por_seletor_com_o_rotulo_vira_conta_vista_na_tela() -> None:
    """android-10/12/13 diziam "bate · seletor id=…|text=qa-user-10: 1 elemento(s)" (3 cartões)."""
    pos = "id=com.pocqa.messenger:id/account_label|text=qa-user-10"
    assert evidencia_da_conta("qa-user-10", pos, PROVA_QA) == "qa-user-10 visto na tela"
    assert evidencia_legivel("qa-user-10", PROVA_QA) == "qa-user-10 visto na tela"
    # o seletor cru da pós-condição (a prova não trouxe o rótulo) também não vai para a tela
    assert evidencia_da_conta("andre.c", "id=action_bar_title|text==andre.c", SELETOR) == "andre.c visto na tela"


def test_prova_por_seletor_sem_o_rotulo_nao_e_observacao_de_conta() -> None:
    """android-06: a evidência de 02/10 era só o seletor (sem a conta) e o cartão dizia "conta diferente do rótulo"."""
    assert evidencia_legivel("andre.carvalho9543", SELETOR) is None
    assert evidencia_legivel("qa-user-11", PROVA_QA) is None
    assert evidencia_legivel("qa-user-10", "seletor id=x|text=qa-user-10: 0 elemento(s)") is None


def test_frase_que_nomeia_a_conta_fica_e_a_conta_trocada_segue_diferente() -> None:
    assert evidencia_legivel("lucas.teste", "Perfil de @lucas.teste aberto") == "Perfil de @lucas.teste aberto"
    # o rótulo mudou depois da observação: a frase fica, e o painel a lê como conta diferente do rótulo
    assert evidencia_legivel("bruno", "Perfil de @lucas.teste aberto") == "Perfil de @lucas.teste aberto"
    assert evidencia_legivel("lucas.teste", "Perfil de @lucas.teste aberto (selector:id=x)") == "Perfil de @lucas.teste aberto"
    assert evidencia_legivel("lucas.teste", None) is None and evidencia_legivel("lucas.teste", "  ") is None


async def test_o_cartao_le_a_evidencia_antiga_ja_legivel(harness: Harness) -> None:
    """A evidência gravada crua antes desta correção sai legível pela API, sem migração: a prova com o rótulo vira
    "visto na tela"; a que só tem o seletor vale como não observada (evidência e carimbo nulos)."""
    st = harness.state
    db = st.db
    db.execute("UPDATE instances SET account_label=?, account_evidence=?, account_evidence_ts=? WHERE id=?",
               ("qa-user-10", PROVA_QA, "2026-10-03T11:00:00.000Z", "android-01"))
    db.execute("UPDATE instances SET account_label=?, account_evidence=?, account_evidence_ts=? WHERE id=?",
               ("andre.carvalho9543", SELETOR, "2026-10-02T22:58:00.000Z", "android-02"))
    d1 = st.devices.dto(st.devices.get("android-01"))
    d2 = st.devices.dto(st.devices.get("android-02"))
    assert (d1.account_evidence, d1.account_evidence_ts) == ("qa-user-10 visto na tela", "2026-10-03T11:00:00.000Z")
    assert (d2.account_evidence, d2.account_evidence_ts) == (None, None)
    assert db.scalar("SELECT account_evidence FROM instances WHERE id='android-02'") == SELETOR   # o banco não muda

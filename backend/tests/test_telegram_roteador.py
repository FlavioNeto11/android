"""28.15 (ADR-071): o roteador da conversa do Telegram — texto → intenção, por regra fixa e sem IA."""
from __future__ import annotations

import pytest

from app.modules.avisos.application.entrada import (
    casar_ref,
    rotear,
    sufixo,
    texto_para_o_extrator,
)


@pytest.mark.parametrize("texto,tipo", [
    ("/ajuda", "ajuda"), ("/start", "ajuda"), ("/help@CentralBot", "ajuda"),
    ("/status", "status"), ("/pendencias", "pendencias"), ("/pendências", "pendencias"),
    ("", "vazia"), ("   ", "vazia"), (None, "vazia"),
    ("/qualquer", "desconhecida"),
])
def test_comandos_simples(texto, tipo):
    assert rotear(texto).tipo == tipo


def test_aprovar_vetar_e_responder_levam_o_id_e_o_texto():
    assert rotear("/aprovar 4985a1").ref == "4985a1"
    v = rotear("/vetar apr-abc123 porque não")
    assert (v.tipo, v.ref) == ("vetar", "apr-abc123")
    r = rotear("/responder 4985a1 Para o QA-001, texto oi")
    assert (r.tipo, r.ref, r.texto) == ("responder", "4985a1", "Para o QA-001, texto oi")


@pytest.mark.parametrize("texto", ["/aprovar", "/vetar   ", "/responder 4985a1", "/responder", "/para android-09"])
def test_formato_incompleto_explica_o_formato(texto):
    i = rotear(texto)
    assert i.tipo == "desconhecida"
    assert i.motivo and ("Formato" in i.motivo or "Falta o id" in i.motivo)


def test_para_explicito_e_para_livre():
    i = rotear("/para android-09 abrir o QA Messenger")
    assert (i.tipo, i.alvo, i.texto) == ("para", "android-09", "abrir o QA Messenger")
    j = rotear("para o @lucas.almeida: curtir a última foto")
    assert (j.tipo, j.alvo, j.texto) == ("para", "@lucas.almeida", "curtir a última foto")
    k = rotear("Para a Ana: abrir o Outlook")
    assert (k.tipo, k.alvo, k.texto) == ("para", "Ana", "abrir o Outlook")


def test_texto_livre_e_pedido_sem_destino_explicito():
    i = rotear("abra o Chrome no android-03")
    assert (i.tipo, i.texto) == ("livre", "abra o Chrome no android-03")
    # "mande mensagem para o André" não tem dois-pontos: o destinatário não vira destino.
    assert rotear("mande mensagem para o André dizendo oi").tipo == "livre"


def test_orquestradora_por_prefixo_e_por_reply_a_mensagem_que_a_central_nao_mandou():
    assert rotear("/orq o deploy pode ir").tipo == "orquestradora"
    i = rotear("pode seguir", responde_ao_bot=True, registrada=False)
    assert (i.tipo, i.texto) == ("orquestradora", "pode seguir")
    # Reply a uma mensagem que a Central mandou NÃO é da orquestradora.
    assert rotear("/status", responde_ao_bot=True, registrada=True).tipo == "status"


@pytest.mark.parametrize("texto,tipo", [("sim", "aprovar"), ("Sim!", "aprovar"), ("ok", "aprovar"),
                                        ("não", "vetar"), ("Nao.", "vetar"), ("vetar", "vetar")])
def test_reply_a_aviso_de_aprovacao(texto, tipo):
    i = rotear(texto, responde_ao_bot=True, registrada=True, fato="approval:apr-xyz")
    assert (i.tipo, i.ref) == (tipo, "apr-xyz")


def test_reply_ambiguo_a_aprovacao_nao_decide():
    i = rotear("talvez amanhã", responde_ao_bot=True, registrada=True, fato="approval:apr-xyz")
    assert i.tipo == "desconhecida" and "sim" in (i.motivo or "")


def test_reply_a_pergunta_de_execucao_e_a_resposta():
    i = rotear("QA-001", responde_ao_bot=True, registrada=True, fato="run:r-20261002181523-4985a1:needs_input")
    assert (i.tipo, i.ref, i.texto) == ("responder", "r-20261002181523-4985a1", "QA-001")


def test_comando_vence_o_fato_do_reply():
    i = rotear("/status", responde_ao_bot=True, registrada=True, fato="approval:apr-xyz")
    assert i.tipo == "status"


def test_texto_para_o_extrator_usa_as_frases_do_painel():
    assert texto_para_o_extrator("android-09", "abrir o QA") == "abrir o QA no android-09"
    assert texto_para_o_extrator("@lucas", "curtir") == "curtir como @lucas"
    assert texto_para_o_extrator("Ana", "abrir o Outlook") == "abrir o Outlook com a persona Ana"


def test_casar_ref_aceita_o_id_inteiro_ou_o_fim_com_4_ou_mais():
    ids = ["r-20261002181523-4985a1", "r-20261003075154-6678e6", "apr-0a4985a1"]
    assert casar_ref("r-20261003075154-6678e6", ids) == ["r-20261003075154-6678e6"]
    assert casar_ref("6678e6", ids) == ["r-20261003075154-6678e6"]
    assert casar_ref("4985a1", ids) == ["r-20261002181523-4985a1", "apr-0a4985a1"]   # ambíguo: os dois voltam
    assert casar_ref("a1", ids) == []                                                # curto demais
    assert sufixo("r-20261002181523-4985a1") == "4985a1"

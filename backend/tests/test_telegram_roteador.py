"""28.15 (ADR-071): a gramática comum dos canais de conversa (Telegram hoje, Trello no 32.2) — texto + fato →
intenção, por regra fixa e sem IA."""
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
    ("/status", "status"), ("/estado", "status"), ("/pendencias", "pendencias"), ("/pendências", "pendencias"),
    ("", "vazia"), ("   ", "vazia"), (None, "vazia"),
    ("/qualquer", "desconhecida"),
])
def test_comandos_simples(texto, tipo):
    assert rotear(texto).tipo == tipo


def test_aprovar_vetar_e_responder_levam_o_id_e_o_texto():
    assert rotear("/aprovar 4985a1").ref == "4985a1"
    v = rotear("/vetar apr-abc123 porque não")
    assert (v.tipo, v.ref, v.texto) == ("vetar", "apr-abc123", "porque não")
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


def test_orquestradora_pelo_prefixo():
    # O reply a uma mensagem que a Central não mandou é regra do CANAL (test_telegram_entrada); aqui só o /orq.
    i = rotear("/orq o deploy pode ir")
    assert (i.tipo, i.texto) == ("orquestradora", "o deploy pode ir")


@pytest.mark.parametrize("texto,tipo", [("sim", "aprovar"), ("Sim!", "aprovar"), ("ok", "aprovar"),
                                        ("não", "vetar"), ("Nao.", "vetar"), ("vetar", "vetar")])
def test_reply_a_aviso_de_aprovacao(texto, tipo):
    i = rotear(texto, fato="approval:apr-xyz")
    assert (i.tipo, i.ref) == (tipo, "apr-xyz")


def test_reply_ambiguo_a_aprovacao_nao_decide():
    i = rotear("talvez amanhã", fato="approval:apr-xyz")
    assert i.tipo == "desconhecida" and "sim" in (i.motivo or "")


def test_reply_a_pergunta_de_execucao_e_a_resposta():
    i = rotear("QA-001", fato="run:r-20261002181523-4985a1:needs_input")
    assert (i.tipo, i.ref, i.texto) == ("responder", "r-20261002181523-4985a1", "QA-001")


def test_comando_vence_o_fato_do_reply():
    assert rotear("/status", fato="approval:apr-xyz").tipo == "status"


def test_com_fato_o_id_e_o_do_fato_e_o_resto_e_a_nota_ou_a_resposta():
    # O formato do comentário no cartão do Trello (32.2) e do reply no Telegram: o id não se escreve.
    a = rotear("/aprovar", fato="approval:apr-xyz")
    assert (a.tipo, a.ref, a.texto) == ("aprovar", "apr-xyz", "")
    v = rotear("/vetar o tom ficou agressivo", fato="approval:apr-xyz")
    assert (v.tipo, v.ref, v.texto) == ("vetar", "apr-xyz", "o tom ficou agressivo")
    # 28.26: a primeira palavra vai à parte, para quem decide conferir se é o id de outra pendência.
    assert v.ref_digitado == "o" and a.ref_digitado is None
    r = rotear("/responder QA-001", fato="run:r-1:needs_input")
    assert (r.tipo, r.ref, r.texto) == ("responder", "r-1", "QA-001")
    assert rotear("/responder", fato="run:r-1:needs_input").tipo == "desconhecida"
    # Fato de outro tipo não captura o comando: volta à forma com id.
    assert rotear("/responder 4985a1 oi", fato="approval:apr-xyz").ref == "4985a1"


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


# ---------------------------------------------------------------------------------------------- 28.17: quem é a ANA
@pytest.mark.parametrize("texto", [
    "quem é você?", "Quem é você", "quem e vc", "Oi, quem é você?", "você é uma IA?", "Você é um robô?",
    "você é humana?", "é uma pessoa?", "vc é bot?", "qual é o seu nome?", "como você se chama?", "/quem", "/ana",
])
def test_a_pergunta_pela_identidade_nao_e_pedido(texto):
    assert rotear(texto).tipo == "identidade"


@pytest.mark.parametrize("texto", [
    "você é capaz de postar no android-09?", "quem é você no android-09? abre o perfil", "abre o perfil e diz quem é",
])
def test_pedido_que_comeca_parecido_segue_como_texto_livre(texto):
    assert rotear(texto).tipo == "livre"


def test_com_fato_de_pergunta_a_frase_e_a_resposta_da_execucao():
    """Reply ao aviso de pergunta: "quem é você?" é o que a pessoa respondeu à execução, não a pergunta à ANA."""
    i = rotear("quem é você?", fato="run:r-20261002181523-4985a1:needs_input")
    assert (i.tipo, i.texto) == ("responder", "quem é você?")

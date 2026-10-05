"""Item 28.50 — o aviso do que a pessoa ENSINOU e o sistema rebaixou (30.80 B, combinado com a Aprendizado em 05/10).

Prova `simulated`: o `data` é o do contrato (kind, ref, app, treino, sem_receita_ativa, para, desde), sem Telegram.
Rebaixado com outra receita segurando a etapa é rotina; sem nenhuma ativa, a etapa voltou para a IA e sai na hora. No
texto não vai identificador do item (como no 28.14); o número da receita vai só ao link do detalhe na aba Aprendizado,
e o id do fluxo não sai em lugar nenhum (pode carregar conta ou nome, leitura do 30.80 B).
"""
from __future__ import annotations

import pytest

from app.modules.avisos.domain.mensagem import (AGORA, JANELA, PRECISA_DE_VOCE, ROTINA, ROTULOS, ROTULOS_AGRUPADOS,
                                                aviso_de_evento, corpo_agrupado, entrega_do_tipo, link_agrupado)
from app.modules.avisos.infrastructure.servico import KINDS_QUE_AVISAM

PAINEL = "http://painel.local:8000"
REBAIXADO, SEM_RECEITA = "learning.ensinado_rebaixado", "learning.ensinado_sem_receita"


def _dados(*, kind: str = "receita", ref: object = "194", para: str = "quarantined", sem: bool = False,
           app: object = "com.pocqa.messenger", desde: object = "2026-10-05T14:30:00Z") -> dict[str, object]:
    return {"kind": kind, "ref": ref, "app": app, "treino": "trn-RJXCrrwQlMLumbAy", "sem_receita_ativa": sem,
            "para": para, "desde": desde}


def _sem_filtro(texto: str) -> str:
    return texto


def test_os_dois_tipos_avisam_e_o_nivel_decide_a_entrega() -> None:
    assert {REBAIXADO, SEM_RECEITA} <= KINDS_QUE_AVISAM
    rebaixado = aviso_de_evento(REBAIXADO, _dados(), 1, PAINEL)
    sem = aviso_de_evento(SEM_RECEITA, _dados(sem=True), 2, PAINEL)
    assert rebaixado is not None and sem is not None
    assert (rebaixado.nivel, entrega_do_tipo(REBAIXADO)) == (ROTINA, JANELA)
    assert (sem.nivel, entrega_do_tipo(SEM_RECEITA)) == (PRECISA_DE_VOCE, AGORA)
    assert ROTULOS[REBAIXADO] in rebaixado.titulo and ROTULOS[SEM_RECEITA] in sem.titulo
    assert "Nada a fazer." in rebaixado.corpo
    assert "Espera você: ensine de novo no Modo treinamento" in sem.corpo


def test_nenhum_identificador_do_item_vai_no_texto_e_o_id_so_no_link() -> None:
    aviso = aviso_de_evento(SEM_RECEITA, _dados(sem=True), 3, PAINEL)
    assert aviso is not None
    texto = " ".join((aviso.titulo, aviso.corpo))
    for proibido in ("194", "com.pocqa.messenger", "trn-", "quarantined", "receita:"):
        assert proibido not in texto, f"{proibido!r} foi para fora no texto"
    assert "Uma receita que você ensinou foi para a quarentena depois de falhas seguidas." in aviso.corpo
    assert aviso.link == PAINEL + "/#/aprendizado?aba=aprendido&item=receita:194"


def test_a_mesma_transicao_da_a_mesma_chave_e_outra_transicao_outra() -> None:
    um = aviso_de_evento(REBAIXADO, _dados(), 4, PAINEL)
    reemitido = aviso_de_evento(REBAIXADO, _dados(), 5, PAINEL)
    outra = aviso_de_evento(REBAIXADO, _dados(desde="2026-10-05T15:00:00Z"), 6, PAINEL)
    assert um is not None and reemitido is not None and outra is not None
    assert um.chave == reemitido.chave == "learning:ensinado:receita:194:2026-10-05T14:30:00Z"
    assert outra.chave != um.chave


def test_o_para_vira_frase_fixa_e_o_desconhecido_a_generica() -> None:
    frases = {"disabled": "foi desligado por estar obsoleto", "superseded": "foi substituído pelo sistema",
              "deprecated": "saiu de ativo por decisão do sistema"}
    for para, frase in frases.items():
        aviso = aviso_de_evento(REBAIXADO, _dados(para=para), 7, PAINEL)
        assert aviso is not None and frase in aviso.corpo and para not in aviso.corpo


@pytest.mark.parametrize("ref", ["abrir-conversa-com-qa-001-2", "responder-a-marina-2", "entrar-na-conta-fulano-1987",
                                 "mandar-dm-para-o-perfil-loja-x"])
@pytest.mark.parametrize("kind", [REBAIXADO, SEM_RECEITA])
def test_o_id_do_fluxo_nao_sai_em_lugar_nenhum(ref: str, kind: str) -> None:
    """Orquestradora, leitura do 30.80 B: o id do fluxo é o slug do pedido e pode trazer conta ou nome. Até o 30.83,
    ele não vai ao título, ao corpo, ao link nem à chave, nem com os filtros desligados; o `message` nunca é lido."""
    dados = {**_dados(kind="fluxo", ref=ref, sem=kind == SEM_RECEITA), "message": f"fluxo {ref} rebaixado"}
    for aviso in (aviso_de_evento(kind, dados, 8, PAINEL),
                  aviso_de_evento(kind, dados, 9, PAINEL, redigir=_sem_filtro, nomes=())):
        assert aviso is not None
        assert aviso.link == PAINEL + "/#/aprendizado?aba=aprendido"
        assert "Um fluxo que você ensinou" in aviso.corpo
        tudo = " ".join([aviso.titulo, aviso.corpo, aviso.link or "", aviso.chave])
        assert ref not in tudo
        assert not any(pedaco in tudo.lower() for pedaco in ("marina", "fulano", "loja-x", "qa-001"))


def test_a_chave_do_fluxo_e_estavel_e_a_da_receita_leva_o_numero() -> None:
    a = aviso_de_evento(REBAIXADO, _dados(kind="fluxo", ref="um-fluxo-qualquer"), 1, PAINEL)
    b = aviso_de_evento(REBAIXADO, _dados(kind="fluxo", ref="um-fluxo-qualquer"), 2, PAINEL)
    r = aviso_de_evento(REBAIXADO, _dados(kind="receita", ref="194"), 3, PAINEL)
    assert a is not None and b is not None and r is not None
    assert a.chave == b.chave and "um-fluxo" not in a.chave
    assert ":194:" in r.chave and r.link == PAINEL + "/#/aprendizado?aba=aprendido&item=receita:194"


def test_payload_fora_do_contrato_nao_vira_aviso() -> None:
    ruins = [_dados(kind="recipe"), _dados(ref="19a"), _dados(ref=True), _dados(kind="fluxo", ref="Com Espaço"),
             _dados(kind="fluxo", ref="x" * 65), _dados(app="não é pacote"), _dados(app="semponto"),
             _dados(desde=None), _dados(ref={"id": 1})]
    for dados in ruins:
        assert aviso_de_evento(REBAIXADO, dados, 11, PAINEL) is None, dados
    assert aviso_de_evento(REBAIXADO, None, 12, PAINEL) is None


def test_a_rajada_agrupa_com_o_gesto_e_a_tela_do_aprendizado() -> None:
    assert "{n}" in ROTULOS_AGRUPADOS[REBAIXADO] and "{n}" in ROTULOS_AGRUPADOS[SEM_RECEITA]
    corpo = corpo_agrupado(["a", "b"], SEM_RECEITA)
    assert "Modo treinamento" in corpo and "Pendências" not in corpo
    link = link_agrupado(SEM_RECEITA, [PAINEL + "/#/aprendizado?aba=aprendido&item=receita:194"])
    assert link == PAINEL + "/#/aprendizado?aba=aprendido"

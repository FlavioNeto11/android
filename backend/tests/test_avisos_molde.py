"""28.31: o aviso diz assunto, resultado, o crítico e se espera o dono; o nível decide a entrega; nada de persona, conta,
contato ou IP no texto (queixa do dono de 04/10 19:10Z; desenho e decisões da orquestradora de 04/10 19:22Z)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.avisos.domain.mensagem import (
    AGORA,
    JANELA,
    JANELA_DA_ROTINA_S,
    NIVEL_POR_TIPO,
    aviso_de_evento,
    entrega_do_tipo,
    texto_da_mensagem,
)
from app.modules.avisos.domain.privacidade import menciona_persona, sem_contato, texto_seguro
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.pedidos.domain.avisos import TIPOS as TIPOS_DO_PEDIDO

from .test_avisos_rajada import Cena
from .test_avisos_servico import AQUI, CanalFalso, Relogio, _backend, _cfg

REDIGIR = TriagemDeCredencial().redigir
#: Uma persona (nome de exibição, primeiro, último e @) e um terceiro conhecido, plantados em todo campo de texto livre.
PERSONA = ["Bruno Lima", "Bruno", "Lima", "bruno.qa"]
TERCEIRO = "@maria.souza, maria@exemplo.com, +55 11 98888-7777, 192.168.1.19"
LIVRE = f"Comentar como Bruno Lima (@bruno.qa) para {TERCEIRO}"
PROIBIDOS = ("Bruno", "Lima", "bruno.qa", "maria.souza", "maria@exemplo.com", "98888", "192.168.1.19")
PAINEL = "https://painel.exemplo/central"


def _evento_de_pedido(sub: str) -> dict[str, object]:
    return {"aviso": {"id": f"avs_{sub}", "tipo": sub, "pedido_id": "ped_kUZT1aBcd", "pedido_titulo": LIVRE,
                      "requer_pessoa": TIPOS_DO_PEDIDO[sub][1], "mensagem": f"O pedido «{LIVRE}» mudou.",
                      "dados": {"motivo": LIVRE, "falhas_seguidas": 3, "gasto_usd": 4.02, "orcamento_total_usd": 5.0,
                                "previsto_para": "2026-10-04 14:00", "sequencia": 2, "conflitos": 1,
                                "tentativa": 1}}}


EVENTOS: list[tuple[str, dict[str, object]]] = [
    ("approval.pending", {"approval": {"id": "ap1", "summary": LIVRE, "target": "@loja_x", "content": LIVRE}}),
    ("run.updated", {"run": {"id": "r-20261004-abc123", "short_id": "abc123", "status": "needs_input",
                             "instance_ids": ["android-12"], "command": LIVRE, "status_detail": LIVRE}}),
    ("session.needs_person", {"active": True, "instance_id": "android-03", "status": "auth_challenge",
                              "detail": LIVRE, "profile_id": "p-bruno"}),
    ("learning.needs_person", {"aguardando": True, "faixa": "C", "kind": "receita", "ref": LIVRE,
                               "desde": "2026-10-04T19:00:00+00:00"}),
    *[("pedido.aviso", _evento_de_pedido(sub)) for sub in TIPOS_DO_PEDIDO],
]


def _ids(e: tuple[str, dict[str, object]]) -> str:
    kind, dados = e
    aviso = dados.get("aviso")
    return f"{kind}:{aviso['tipo']}" if isinstance(aviso, dict) else kind


@pytest.mark.parametrize("conversa", [False, True])
@pytest.mark.parametrize("evento", EVENTOS, ids=_ids)
def test_contrato_por_tipo_nada_de_persona_contato_nem_ip(evento: tuple[str, dict[str, object]],
                                                          conversa: bool) -> None:
    """Decisão 5 da orquestradora: por tipo, com persona e terceiro conhecidos nos dados, nem o nome nem o @ saem. A
    única exceção é o alvo da aprovação (ADR-071 (d)), que só sai com a conversa ligada e só na linha "Alvo:"."""
    kind, dados = evento
    aviso = aviso_de_evento(kind, dados, 9, PAINEL, redigir=REDIGIR, nomes=PERSONA, conversa=conversa)
    assert aviso is not None
    texto = texto_da_mensagem(aviso.titulo, aviso.corpo, aviso.link)
    for linha in texto.split("\n"):
        for proibido in PROIBIDOS:
            assert proibido not in linha, (kind, proibido, linha)
        if not linha.startswith("Alvo:"):
            assert "@loja_x" not in linha, linha
    # O molde: até 5 linhas, a primeira com a fala da ANA, e toda mensagem diz se espera o dono ou não.
    linhas = texto.split("\n")
    assert len(linhas) <= 5, linhas
    assert linhas[0].startswith("ANA: ")
    assert any(x.startswith("Espera você") or "Nada a fazer" in x for x in linhas), linhas


def test_rotulo_do_pedido_sai_quando_e_limpo_e_vira_reserva_quando_qualquer_filtro_mudaria() -> None:
    def titulo(pedido_titulo: str, redigir=REDIGIR, nomes=PERSONA) -> str:  # noqa: ANN001
        a = aviso_de_evento("pedido.aviso", {"aviso": {"id": "a1", "tipo": "pausa_automatica",
                                                       "pedido_id": "ped_kUZT1aBcd", "pedido_titulo": pedido_titulo,
                                                       "dados": {"falhas_seguidas": 3}}}, 1,
                            redigir=redigir, nomes=nomes)
        assert a is not None
        return a.titulo

    assert titulo("Preço do Raspberry Pi 5") == "ANA: ⏸️ Pedido «Preço do Raspberry Pi 5» pausado: 3 falhas seguidas"
    # Qualquer troca derruba o rótulo inteiro: persona (mesmo de 2 letras, e "Ana"), contato, segredo, ou sem redator.
    reserva = "ANA: ⏸️ Pedido #kUZT1a pausado: 3 falhas seguidas"
    assert titulo("Posts do Bo", nomes=["Bo"]) == reserva
    assert titulo("Posts da Ana", nomes=["Ana"]) == reserva
    assert titulo("Falar com @fulano") == reserva
    assert titulo("senha: kiwi2024!") == reserva
    assert titulo("Preço do Raspberry Pi 5", redigir=None) == reserva

    def quebra(_t: str) -> str:
        raise RuntimeError("redator fora")

    assert titulo("Preço do Raspberry Pi 5", redigir=quebra) == reserva


def test_privacidade_regua_estrita_e_contato() -> None:
    assert menciona_persona("posts da ana", ["Ana"]) and menciona_persona("do Bo hoje", ["Bo"])
    assert not menciona_persona("Bonito e Analítico", ["Bo", "Ana"])
    assert sem_contato("fale com maria@x.com, @maria.s, 192.168.1.19 ou +55 (11) 98888-7777") == \
        "fale com <contato>, <contato>, <contato> ou <contato>"
    assert sem_contato("US$ 4.02 de US$ 5.00 às 14:00 de 2026") == "US$ 4.02 de US$ 5.00 às 14:00 de 2026"
    # Os títulos reais dos pedidos de 04/10 ficam: o número do item e a data dizem QUAL pedido é.
    for titulo in ("Preço do Raspberry Pi 5 8GB (prova 28.12-05)", "Postagens 2026-10-04 14:00",
                   "[canais 28.10f12] filho A", "US$ 1299.90"):
        assert sem_contato(titulo) == titulo
        assert texto_seguro(titulo, PERSONA, REDIGIR) == titulo
    for fone in ("+55 11 98888-7777", "98888-7777", "(11) 98888-7777", "1198888777"):
        assert sem_contato(f"fone {fone}") == "fone <contato>", fone
    assert texto_seguro("Preço do Pi", [], None) is None and texto_seguro("Preço do Pi", [], REDIGIR) == "Preço do Pi"


def test_todo_tipo_de_aviso_do_pedido_tem_nivel_decidido() -> None:
    """Um tipo novo no vocabulário do pedido (migração 072 e seguintes) obriga a decidir o nível dele aqui."""
    faltam = [t for t in TIPOS_DO_PEDIDO if f"pedido.{t}" not in NIVEL_POR_TIPO]
    assert not faltam, faltam


def test_nivel_decide_a_entrega() -> None:
    assert entrega_do_tipo("approval.pending") == AGORA and entrega_do_tipo("pedido.ocorrencia_incerta") == AGORA
    assert entrega_do_tipo("pedido.pausa_automatica") == AGORA and entrega_do_tipo("pedido.orcamento_esgotado") == AGORA
    assert entrega_do_tipo("pedido.ocorrencia_perdida") == JANELA and entrega_do_tipo("pedido.relatorio_pronto") == JANELA
    assert entrega_do_tipo("learning.needs_person") == JANELA
    # Os avisos montados à mão (convidado, resumo das decisões) saem na hora, como antes.
    assert entrega_do_tipo("telegram.convidado_novo") == AGORA and entrega_do_tipo("decisoes.resumo") == AGORA


def test_rotina_espera_a_janela_e_sai_numa_mensagem_e_o_que_pede_o_dono_passa_na_frente(tmp_path: Path) -> None:
    c = Cena(tmp_path)
    c.chega("pedido:rel1", "pedido.relatorio_pronto")
    c.chega("pedido:enc1", "pedido.encerramento")
    c.chega("learning:x", "learning.needs_person")
    c.chega("approval:ap1", "approval.pending")
    c.volta()
    assert [t for t, _c, _l in c.canal.enviados] == ["t-approval:ap1"], "a rotina saiu sozinha"
    c.avancar(JANELA_DA_ROTINA_S - 5)
    c.volta()
    assert len(c.canal.enviados) == 1, "a rotina saiu antes de a janela fechar"
    c.avancar(10)
    c.volta()
    assert len(c.canal.enviados) == 2
    titulo, corpo, link = c.canal.enviados[1]
    assert titulo == "ANA: 📋 Rotina: 3 novidades desde a última mensagem" and link == "L"
    assert corpo.split("\n") == ["• t-pedido:rel1", "• t-pedido:enc1", "• t-learning:x", "Nada urgente: é rotina."]
    assert c.fila.contagens() == {"enviado": 4}
    fatos = [r["fato"] for r in c.db.query("SELECT fato FROM canal_enviadas ORDER BY ref_mensagem")]
    assert fatos == ["approval:ap1", "grupo:rotina"]


def test_rotina_de_um_item_sai_como_ele_mesmo(tmp_path: Path) -> None:
    c = Cena(tmp_path)
    c.chega("pedido:rel1", "pedido.relatorio_pronto")
    c.avancar(JANELA_DA_ROTINA_S + 1)
    c.volta()
    assert [(t, corpo) for t, corpo, _l in c.canal.enviados] == [("t-pedido:rel1", "c")]


def test_sem_os_nomes_de_persona_o_rotulo_vira_reserva(tmp_path: Path) -> None:
    """A leitura dos nomes falhou: o aviso sai mesmo assim, sem texto da pessoa (rótulo de reserva, sem conteúdo)."""
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())

    def falha() -> list[str]:
        raise RuntimeError("banco fora")

    servico._nomes_de_persona = falha                     # noqa: SLF001 - a porta injetada, trocada no teste
    servico._redigir = REDIGIR                            # noqa: SLF001
    assert servico.enfileirar_evento("pedido.aviso", _evento_de_pedido("pausa_automatica"), 5) is True
    titulo = str(banco.scalar("SELECT titulo FROM avisos_entregas"))
    assert titulo == "ANA: ⏸️ Pedido #kUZT1a pausado: 3 falhas seguidas"

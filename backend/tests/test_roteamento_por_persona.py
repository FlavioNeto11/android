"""`resolver_alvos` em tabela (onda C; design persona-e-parque §7.4, casos A–N). Pura: sem banco, sem aparelho.

O parque desta tabela:

- android-01: Ravenna (Instagram, principal) e Quillon (Chrome) — duas personas de apps diferentes no mesmo aparelho;
- android-02: Ravenna (Instagram, não principal) — a mesma persona em dois aparelhos;
- android-03: Tadeu (vínculo sem app; ele tem conta do Instagram);
- android-04: ninguém (o QA, o caminho antigo);
- android-05: Carla e Dora, ambas sem app no vínculo e sem conta — nada desempata.
"""
from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.modules.execution.application.alvos import (AlvoPedido, DicasDoTexto, MencaoNoTexto, Mundo, PedidoDeAlvos,
                                                     RecusaDeAlvo, Vinculo, resolver_alvos)

VINCULOS = (
    Vinculo("ottilie", "android-01", frozenset({"instagram"}), True),
    Vinculo("quillon", "android-01", frozenset({"chrome"}), True),
    Vinculo("ottilie", "android-02", frozenset({"instagram"}), False),
    Vinculo("tadeu", "android-03", frozenset({"instagram"}), True),
    Vinculo("carla", "android-05", frozenset(), True),
    Vinculo("dora", "android-05", frozenset(), True),
)
TODOS = frozenset({"android-01", "android-02", "android-03", "android-04", "android-05"})


def _ultimo(c: Sequence[str]) -> str | None:
    """Desempate de teste que NÃO é o primeiro da lista: prova que o balanceamento foi mesmo consultado."""
    return c[-1] if c else None


def mundo(*, aptos: frozenset[str] = TODOS, prontas: frozenset[tuple[str, str]] = frozenset()) -> Mundo:
    return Mundo(VINCULOS, aptos, prontas, _ultimo, (("ottilie", "Ravenna"), ("quillon", "Quillon"), ("tadeu", "Tadeu")))


def texto(personas: tuple[tuple[str, ...], ...] = (), aparelhos: tuple[str, ...] = ()) -> DicasDoTexto:
    return DicasDoTexto(tuple(MencaoNoTexto("/".join(p), p) for p in personas),
                        tuple(MencaoNoTexto(a, (a,)) for a in aparelhos))


def alvos(pedido: PedidoDeAlvos, dicas: DicasDoTexto = DicasDoTexto(), m: Mundo | None = None
          ) -> list[tuple[str, str | None, str]]:
    r = resolver_alvos(pedido, dicas, m or mundo())
    assert not r.perguntas, r.perguntas
    return [(a.instance_id, a.profile_id, a.origem) for a in r.alvos]


def perguntas(pedido: PedidoDeAlvos, dicas: DicasDoTexto = DicasDoTexto()) -> list[str]:
    return [p.code for p in resolver_alvos(pedido, dicas, mundo()).perguntas]


# ============================================================ por aparelho (A–D)
@pytest.mark.parametrize("caso, pedido, esperado", [
    ("A: aparelho sem persona", PedidoDeAlvos(instance_ids=("android-04",)), [("android-04", None, "ui")]),
    ("B: aparelho com uma persona", PedidoDeAlvos(instance_ids=("android-03",)), [("android-03", "tadeu", "ui")]),
    ("C: duas personas, o app desempata", PedidoDeAlvos(instance_ids=("android-01",), app_id="chrome"),
     [("android-01", "quillon", "ui")]),
    ("C': idem, pelo Instagram", PedidoDeAlvos(instance_ids=("android-01",), app_id="instagram"),
     [("android-01", "ottilie", "ui")]),
    ("vários aparelhos, cada um com a sua", PedidoDeAlvos(instance_ids=("android-02", "android-03", "android-04")),
     [("android-02", "ottilie", "ui"), ("android-03", "tadeu", "ui"), ("android-04", None, "ui")]),
])
def test_por_aparelho(caso: str, pedido: PedidoDeAlvos, esperado: list[tuple[str, str | None, str]]) -> None:
    assert alvos(pedido) == esperado, caso


def test_d_duas_personas_sem_app_que_desempate_vira_pergunta() -> None:
    r = resolver_alvos(PedidoDeAlvos(instance_ids=("android-01", "android-03")), DicasDoTexto(), mundo())
    assert [(a.instance_id, a.profile_id) for a in r.alvos] == [("android-03", "tadeu")]
    assert [(p.code, p.instance_id, set(p.options)) for p in r.perguntas] == [
        ("persona_no_aparelho", "android-01", {"ottilie", "quillon"})]
    # Nem o app resolve quando nenhum vínculo diz de que app é.
    assert perguntas(PedidoDeAlvos(instance_ids=("android-05",), app_id="instagram")) == ["persona_no_aparelho"]


# ============================================================ por persona (E–H)
def test_e_one_prefere_o_aparelho_com_sessao_pronta_e_o_balanceamento_desempata() -> None:
    p = PedidoDeAlvos(profile_ids=("ottilie",))
    # Sem sessão pronta em lugar nenhum: o principal.
    assert alvos(p) == [("android-01", "ottilie", "vinculo")]
    # Sessão pronta só no android-02: é lá, mesmo não sendo o principal.
    assert alvos(p, m=mundo(prontas=frozenset({("ottilie", "android-02")}))) == [("android-02", "ottilie", "vinculo")]
    # Sessão pronta nos dois: o principal (29.65). Antes o balanceamento decidia, e a mesma persona ia ora a um
    # aparelho, ora a outro (o desempate de teste pega o último, que NÃO é o principal: prova que não foi ele).
    ambos = frozenset({("ottilie", "android-01"), ("ottilie", "android-02")})
    assert alvos(p, m=mundo(prontas=ambos)) == [("android-01", "ottilie", "vinculo")]
    # Sessão pronta num aparelho que não está apto não conta; o principal fora de ar cede ao apto.
    assert alvos(p, m=mundo(aptos=frozenset({"android-02"}), prontas=frozenset({("ottilie", "android-01")}))) == [
        ("android-02", "ottilie", "vinculo")]


def test_e_entre_secundarios_com_sessao_o_balanceamento_ainda_desempata() -> None:
    """29.65: a preferência é pelo principal, não por um secundário qualquer. Principal sem sessão e dois secundários
    com sessão: quem decide continua sendo o balanceamento."""
    vinculos = (*VINCULOS, Vinculo("ottilie", "android-04", frozenset({"instagram"}), False))
    m = Mundo(vinculos, TODOS, frozenset({("ottilie", "android-02"), ("ottilie", "android-04")}), _ultimo,
              (("ottilie", "Ravenna"),))
    assert alvos(PedidoDeAlvos(profile_ids=("ottilie",)), m=m) == [("android-04", "ottilie", "balanceamento")]


def test_e_principal_desligado_com_sessao_cede_ao_secundario_ligado() -> None:
    """29.65: preferir o principal não acorda aparelho à toa. Desligado, com o secundário ligado e com sessão, quem
    decide é o balanceamento; ligado, ganha o principal."""
    ambos = frozenset({("ottilie", "android-01"), ("ottilie", "android-02")})
    p = PedidoDeAlvos(profile_ids=("ottilie",))
    so_o_02 = Mundo(VINCULOS, TODOS, ambos, _ultimo, (("ottilie", "Ravenna"),), ligados=frozenset({"android-02"}))
    assert alvos(p, m=so_o_02) == [("android-02", "ottilie", "balanceamento")]
    os_dois = Mundo(VINCULOS, TODOS, ambos, _ultimo, (("ottilie", "Ravenna"),),
                    ligados=frozenset({"android-01", "android-02"}))
    assert alvos(p, m=os_dois) == [("android-01", "ottilie", "vinculo")]


def test_e_principal_entre_dois_com_sessao_diz_o_motivo() -> None:
    """29.65: "vinculo" vale para a sessão única e para o principal entre dois com sessão pronta; a prévia precisa
    dizer qual dos dois casos foi. O motivo só aparece no segundo: nos outros, a origem basta."""
    p = PedidoDeAlvos(profile_ids=("ottilie",))
    ambos = frozenset({("ottilie", "android-01"), ("ottilie", "android-02")})
    [alvo] = resolver_alvos(p, DicasDoTexto(), mundo(prontas=ambos)).alvos
    assert (alvo.instance_id, alvo.origem) == ("android-01", "vinculo")
    assert alvo.motivo is not None and "principal" in alvo.motivo and "android-02" in alvo.motivo
    assert alvo.as_dict()["motivo"] == alvo.motivo
    [so_um] = resolver_alvos(p, DicasDoTexto(), mundo(prontas=frozenset({("ottilie", "android-02")}))).alvos
    assert (so_um.instance_id, so_um.motivo) == ("android-02", None)


def test_f_primary_e_g_all() -> None:
    pronto2 = mundo(prontas=frozenset({("ottilie", "android-02")}))
    assert alvos(PedidoDeAlvos(profile_ids=("ottilie",), device_policy="primary"), m=pronto2) == [
        ("android-01", "ottilie", "vinculo")]
    assert alvos(PedidoDeAlvos(profile_ids=("ottilie",), device_policy="all")) == [
        ("android-01", "ottilie", "vinculo"), ("android-02", "ottilie", "vinculo")]
    # `all` fica nos aptos quando há algum.
    assert alvos(PedidoDeAlvos(profile_ids=("ottilie",), device_policy="all"),
                 m=mundo(aptos=frozenset({"android-02"}))) == [("android-02", "ottilie", "vinculo")]


def test_o_app_da_tarefa_filtra_os_aparelhos_da_persona() -> None:
    assert alvos(PedidoDeAlvos(targets=(AlvoPedido("quillon", app_id="chrome"),))) == [("android-01", "quillon", "vinculo")]


def test_h_persona_sem_vinculo_recusa() -> None:
    with pytest.raises(RecusaDeAlvo) as exc:
        resolver_alvos(PedidoDeAlvos(profile_ids=("zeca",)), DicasDoTexto(), mundo())
    assert (exc.value.code, exc.value.status) == ("no_binding", 409)


# ============================================================ interseção, alvos explícitos, repetição (I–K)
def test_i_intersecao() -> None:
    assert alvos(PedidoDeAlvos(profile_ids=("ottilie",), instance_ids=("android-02", "android-03"))) == [
        ("android-02", "ottilie", "ui")]
    with pytest.raises(RecusaDeAlvo) as exc:
        resolver_alvos(PedidoDeAlvos(profile_ids=("ottilie",), instance_ids=("android-03",)), DicasDoTexto(), mundo())
    assert exc.value.code == "sem_intersecao"


def test_j_alvo_explicito() -> None:
    assert alvos(PedidoDeAlvos(targets=(AlvoPedido("ottilie", ("android-01", "android-02")),))) == [
        ("android-01", "ottilie", "ui"), ("android-02", "ottilie", "ui")]
    with pytest.raises(RecusaDeAlvo) as exc:
        resolver_alvos(PedidoDeAlvos(targets=(AlvoPedido("ottilie", ("android-03",)),)), DicasDoTexto(), mundo())
    assert exc.value.code == "sem_vinculo"


def test_k_o_mesmo_aparelho_duas_vezes_recusa() -> None:
    with pytest.raises(RecusaDeAlvo) as exc:
        resolver_alvos(PedidoDeAlvos(targets=(AlvoPedido("ottilie", ("android-01",)),
                                              AlvoPedido("quillon", ("android-01",)))), DicasDoTexto(), mundo())
    assert (exc.value.code, exc.value.status) == ("aparelho_repetido_na_execucao", 409)


# ============================================================ o texto (L, M, N)
def test_l_texto_estreita_a_selecao_e_contradicao_vira_pergunta() -> None:
    # Estreita: a seleção tem Ravenna e Tadeu; o texto cita o Tadeu.
    assert alvos(PedidoDeAlvos(profile_ids=("ottilie", "tadeu")), texto((("tadeu",),))) == [
        ("android-03", "tadeu", "texto")]
    # Estreita por aparelho: Ravenna selecionado, o texto diz "no android-02".
    assert alvos(PedidoDeAlvos(profile_ids=("ottilie",)), texto(aparelhos=("android-02",))) == [
        ("android-02", "ottilie", "texto")]
    # Coerente sem estreitar: continua `ui`.
    assert alvos(PedidoDeAlvos(instance_ids=("android-03",)), texto((("tadeu",),), ("android-03",))) == [
        ("android-03", "tadeu", "ui")]
    # O texto escolhe a persona num aparelho com duas.
    assert alvos(PedidoDeAlvos(instance_ids=("android-01",)), texto((("quillon",),))) == [
        ("android-01", "quillon", "texto")]
    # Contradição: a seleção é o Ravenna; o texto fala do Tadeu → pergunta, nunca escolha.
    assert perguntas(PedidoDeAlvos(profile_ids=("ottilie",)), texto((("tadeu",),))) == ["destino_contraditorio"]
    assert perguntas(PedidoDeAlvos(instance_ids=("android-03",)), texto(aparelhos=("android-04",))) == [
        "destino_contraditorio"]
    assert perguntas(PedidoDeAlvos(profile_ids=("ottilie",)), texto(aparelhos=("android-03",))) == [
        "destino_contraditorio"]


def test_m_so_o_texto_decide_com_origem_texto() -> None:
    assert alvos(PedidoDeAlvos(), texto((("ottilie",),))) == [("android-01", "ottilie", "texto")]
    assert alvos(PedidoDeAlvos(), texto((("ottilie",),), ("android-02",))) == [("android-02", "ottilie", "texto")]
    assert alvos(PedidoDeAlvos(), texto(aparelhos=("android-03", "android-04"))) == [
        ("android-03", "tadeu", "texto"), ("android-04", None, "texto")]
    assert alvos(PedidoDeAlvos(device_policy="all"), texto((("ottilie",),))) == [
        ("android-01", "ottilie", "texto"), ("android-02", "ottilie", "texto")]
    # Dois "Ravenna" no catálogo → pergunta com as opções, sem alvo.
    r = resolver_alvos(PedidoDeAlvos(), texto((("ottilie", "andre2"),)), mundo())
    assert not r.alvos and [(p.code, p.options) for p in r.perguntas] == [("persona_ambigua", ("ottilie", "andre2"))]


def test_n_nada_em_lugar_nenhum_recusa_400() -> None:
    with pytest.raises(RecusaDeAlvo) as exc:
        resolver_alvos(PedidoDeAlvos(), DicasDoTexto(), mundo())
    assert (exc.value.code, exc.value.status) == ("sem_alvo", 400)

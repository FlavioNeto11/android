"""Item 28.10, F1 — o domínio puro da colaboração entre pedidos (`modules/pedidos/domain/colaboracao.py`).

Prova `simulated` (`arquivo::teste`): sem banco, sem relógio, sem API. Cobre profundidade, quantidade de filhos, ciclo
direto e indireto, linhagem, porta-voz único, orçamento reservado do pai e dependência fora da família.
"""
from __future__ import annotations

from app.modules.pedidos.domain import colaboracao as c
from app.modules.pedidos.domain.colaboracao import DadosDoPai, Irmao, Limites, Recusa

LIMITES = Limites(max_profundidade=2, max_filhos=5)


def _pai(**kw) -> DadosDoPai:
    base = {"id": "pai", "estado": "ativo", "pai_id": None, "orcamento_total_usd": 10.0, "gasto_usd": 0.0}
    base.update(kw)
    return DadosDoPai(**base)


def _validar(**kw) -> Recusa | None:
    base = dict(novo_id="novo", pai=_pai(), pai_id="pai", pais={"pai": None}, irmaos=[], papeis_da_familia=[None],
                papel=None, dependencias=[], pai_dos_dependidos={"pai": None}, arestas_da_familia=[],
                orcamento_total_usd=1.0, limites=LIMITES)
    base.update(kw)
    return c.validar_filho(**base)


def _codigo(r: Recusa | None) -> str | None:
    return r.codigo if r else None


# ---------------------------------------------------------------- vocabulário e profundidade
def test_vocabulario_fechado() -> None:
    assert c.PAPEIS == ("pesquisador", "checador", "redator", "porta_voz")
    assert c.TIPOS_DE_DEPENDENCIA == ("precisa_de_resultado", "depois_de")


def test_profundidade_conta_os_niveis_com_o_pai() -> None:
    assert c.profundidade(()) == 1
    assert c.profundidade(("pai",)) == 2
    assert c.profundidade(("filho", "pai")) == 3


def test_profundidade_tres_e_recusada_e_dois_passa() -> None:
    assert _validar() is None                                              # filho de raiz: nível 2
    neto = _validar(pai=_pai(id="filho", pai_id="pai"), pai_id="filho", pais={"filho": "pai", "pai": None},
                    pai_dos_dependidos={"filho": "pai"})
    assert _codigo(neto) == "profundidade_excedida" and neto.campo == "pai_id"
    # a config pode abrir um nível a mais: a mesma árvore passa com max_profundidade=3
    assert _validar(pai=_pai(id="filho", pai_id="pai"), pai_id="filho", pais={"filho": "pai", "pai": None},
                    pai_dos_dependidos={"filho": "pai"}, limites=Limites(max_profundidade=3)) is None


# ---------------------------------------------------------------- filhos
def test_o_sexto_filho_e_recusado_e_o_quinto_passa() -> None:
    quatro = [Irmao(f"f{i}", None, 1.0) for i in range(4)]
    assert _validar(irmaos=quatro, pai=_pai(orcamento_total_usd=None)) is None
    cinco = quatro + [Irmao("f4", None, 1.0)]
    assert _codigo(_validar(irmaos=cinco, pai=_pai(orcamento_total_usd=None))) == "filhos_demais"


# ---------------------------------------------------------------- o pai
def test_pai_inexistente_e_pai_terminal() -> None:
    assert _codigo(_validar(pai=None)) == "pai_inexistente"
    for estado in ("concluido", "encerrado", "cancelado"):
        assert _codigo(_validar(pai=_pai(estado=estado))) == "pai_terminal"
    for estado in ("rascunho", "ativo", "pausado", "aguardando_pessoa"):
        assert _validar(pai=_pai(estado=estado)) is None


# ---------------------------------------------------------------- ciclo
def test_ciclo_direto_e_consigo_mesmo() -> None:
    assert c.fecha_ciclo([], ("a", "a")) is True
    assert c.fecha_ciclo([("a", "b")], ("b", "a")) is True        # b depende de a; a passar a depender de b volta
    assert c.fecha_ciclo([("a", "b")], ("a", "c")) is False


def test_ciclo_indireto_por_um_caminho_de_tres() -> None:
    arestas = [("a", "b"), ("b", "c")]                             # c depende de b, que depende de a
    assert c.fecha_ciclo(arestas, ("c", "a")) is True              # a depender de c fecha a-b-c-a
    assert c.fecha_ciclo(arestas, ("a", "c")) is False             # atalho na mesma direção não é ciclo
    assert c.fecha_ciclo(arestas + [("a", "d")], ("d", "a")) is True


def test_dependencia_do_proprio_pedido_novo_e_ciclo() -> None:
    r = _validar(dependencias=[("novo", "depois_de")])
    assert _codigo(r) == "ciclo"


def test_ciclo_pela_aresta_nova_com_arestas_ja_gravadas() -> None:
    # irmão `x` já depende do `novo`?? impossível ainda existir; mas dois `de` que se apontam entre si fecham ciclo
    r = _validar(novo_id="y", irmaos=[Irmao("x", None, 1.0)], pai=_pai(orcamento_total_usd=None),
                 pai_dos_dependidos={"pai": None, "x": "pai"}, arestas_da_familia=[("y", "x")],
                 dependencias=[("x", "depois_de")])
    assert _codigo(r) == "ciclo", "x depende de y e y passaria a depender de x"


# ---------------------------------------------------------------- dependência e família
def test_dependencia_do_pai_e_de_irmao_passam() -> None:
    assert _validar(dependencias=[("pai", "depois_de")]) is None
    r = _validar(irmaos=[Irmao("irmao", None, 1.0)], pai=_pai(orcamento_total_usd=None),
                 pai_dos_dependidos={"pai": None, "irmao": "pai"}, dependencias=[("irmao", "precisa_de_resultado")])
    assert r is None


def test_dependencia_fora_da_familia_e_recusada() -> None:
    # um pedido de outra árvore, um que não existe e um neto (filho de irmão) não são da família
    assert _codigo(_validar(dependencias=[("estranho", "depois_de")],
                            pai_dos_dependidos={"pai": None, "estranho": "outro_pai"})) == "dependencia_fora_da_familia"
    assert _codigo(_validar(dependencias=[("fantasma", "depois_de")])) == "dependencia_fora_da_familia"
    assert _codigo(_validar(dependencias=[("raiz_solta", "depois_de")],
                            pai_dos_dependidos={"pai": None, "raiz_solta": None})) == "dependencia_fora_da_familia"


def test_tipo_invalido_e_dependencia_repetida() -> None:
    assert _codigo(_validar(dependencias=[("pai", "talvez")])) == "tipo_de_dependencia_invalido"
    assert _codigo(_validar(dependencias=[("pai", "depois_de"), ("pai", "precisa_de_resultado")])) == "dependencia_duplicada"


def test_raiz_nao_tem_familia_para_depender() -> None:
    assert c.validar_raiz(papel="redator", dependencias=[]) is None
    assert c.validar_raiz(papel=None, dependencias=[]) is None
    assert _codigo(c.validar_raiz(papel=None, dependencias=[("x", "depois_de")])) == "dependencia_fora_da_familia"
    assert _codigo(c.validar_raiz(papel="chefe", dependencias=[])) == "papel_invalido"


# ---------------------------------------------------------------- linhagem
def test_na_linhagem_e_a_cadeia_de_pai_id() -> None:
    pais = {"neto": "filho", "filho": "pai", "pai": None, "primo": "pai2", "pai2": None}
    assert c.cadeia_de_pais("filho", pais) == ("filho", "pai")
    assert c.na_linhagem("neto", "pai", pais) and c.na_linhagem("pai", "neto", pais), "nos dois sentidos"
    assert c.na_linhagem("pai", "pai", pais), "ele mesmo"
    assert not c.na_linhagem("neto", "primo", pais) and not c.na_linhagem("filho", "pai2", pais)


def test_linhagem_que_da_a_volta_nao_recebe_filho() -> None:
    assert c.cadeia_de_pais("a", {"a": "b", "b": "a"}) is None
    r = _validar(pais={"pai": "avo", "avo": "pai"}, pai=_pai(pai_id="avo"))
    assert _codigo(r) == "linhagem"
    # o id do novo já ser ancestral do pai também é a linhagem dando a volta
    assert _codigo(_validar(novo_id="avo", pais={"pai": "avo", "avo": None}, pai=_pai(pai_id="avo"))) == "linhagem"


# ---------------------------------------------------------------- porta-voz
def test_so_um_porta_voz_por_familia() -> None:
    assert _validar(papel="porta_voz", papeis_da_familia=["pesquisador", None]) is None
    assert _codigo(_validar(papel="porta_voz", papeis_da_familia=["porta_voz"])) == "porta_voz_duplicado"
    assert _codigo(_validar(papel="porta_voz", irmaos=[Irmao("f", "porta_voz", 1.0)],
                            papeis_da_familia=[None, "porta_voz"],
                            pai=_pai(orcamento_total_usd=None))) == "porta_voz_duplicado"
    # os outros papéis podem repetir
    assert _validar(papel="pesquisador", papeis_da_familia=["pesquisador", "pesquisador"]) is None
    assert _codigo(_validar(papel="chefe")) == "papel_invalido"


# ---------------------------------------------------------------- orçamento
def test_orcamento_do_filho_maior_que_o_saldo_do_pai_e_recusado() -> None:
    assert _codigo(_validar(orcamento_total_usd=10.01)) == "orcamento_do_pai"
    assert _validar(orcamento_total_usd=10.0) is None, "reservar o total inteiro cabe"
    # o gasto do próprio pai sai do saldo
    assert _codigo(_validar(pai=_pai(gasto_usd=4.0), orcamento_total_usd=6.01)) == "orcamento_do_pai"
    assert _validar(pai=_pai(gasto_usd=4.0), orcamento_total_usd=6.0) is None
    # o que os irmãos já reservaram também (reserva nunca soma por cima)
    irmaos = [Irmao("a", None, 4.0), Irmao("b", None, 3.0)]
    assert _codigo(_validar(irmaos=irmaos, orcamento_total_usd=3.5)) == "orcamento_do_pai"
    assert _validar(irmaos=irmaos, orcamento_total_usd=3.0) is None


def test_filho_sem_orcamento_declarado_e_recusado_com_motivo_claro() -> None:
    for total_pai in (None, 10.0):
        r = _validar(pai=_pai(orcamento_total_usd=total_pai), orcamento_total_usd=None)
        assert _codigo(r) == "orcamento_do_pai" and "declarar" in r.mensagem and r.campo == "orcamento_total_usd"


def test_pai_sem_total_aceita_qualquer_total_declarado() -> None:
    assert _validar(pai=_pai(orcamento_total_usd=None), orcamento_total_usd=1000.0) is None


def test_motivo_orcamento_e_puro_e_diz_os_numeros() -> None:
    assert c.motivo_orcamento_do_filho(10.0, 1.0, [2.0, None], 3.0) is None
    m = c.motivo_orcamento_do_filho(10.0, 1.0, [2.0, None], 8.0)
    assert m is not None and "10.0000" in m and "8.0000" in m and "1.0000" in m and "2.0000" in m

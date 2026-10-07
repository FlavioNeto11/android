"""A pesquisa no relatório consolidado da operação: o campo `pesquisa` e os critérios 5 e 6 pelo estado que ela escreveu.

Antes, 5 ("Detectar necessidade de informação") e 6 ("Obter informação externa") só olhavam as fontes achadas e o
custo da pesquisa. O reaproveitamento do Livro (31.231: sem fonte nem custo) ficava "não medido", e a tentativa paga
que falhou ou nada achou contava 6 como obtida. Agora o relatório lê `pesquisa.estado` (31.235); sem o campo (central
anterior), vale a regra antiga.

Nível de prova: `simulated` (domínio puro e o serviço no harness, com o resumo da pesquisa trocado no teste).
"""
from __future__ import annotations

import json

from app.modules.operacoes.domain import relatorio as rel
from app.modules.operacoes.infrastructure.servico import AlvoPedido

from .conftest import Harness
from .test_operacoes import _conta, _pedido, _persona, _servico


def _cinco_e_seis(op: dict[str, object]) -> tuple[str, str]:
    c = {x["id"]: x["nesta_operacao"] for x in rel.criterios(op, [], ambiente="simulado", aprendizado_disponivel=False)}
    return str(c["5"]), str(c["6"])


def _op(estado: str | None, *, fontes: list[str] | None = None, custo: float = 0.0) -> dict[str, object]:
    return {"id": "op-x", "alvos": [], "capacidade": {}, "custo": {"pesquisa_usd": custo},
            "fontes_da_pesquisa": fontes or [], "pesquisa": None if estado is None else {"estado": estado}}


def test_os_criterios_5_e_6_seguem_o_estado_da_pesquisa() -> None:
    assert _cinco_e_seis(_op("reaproveitada_do_livro")) == ("sim", "sim")             # sem fonte nem custo
    assert _cinco_e_seis(_op("paga", fontes=["https://exemplo.test/a"], custo=0.2)) == ("sim", "sim")
    assert _cinco_e_seis(_op("paga", custo=0.2)) == ("sim", "nao")                     # pagou e nada achou
    assert _cinco_e_seis(_op("falhou", custo=0.1)) == ("sim", "nao")                   # o custo não vira obtido
    assert _cinco_e_seis(_op("nao_rodou")) == ("nao_medido", "nao_medido")


def test_sem_o_campo_vale_a_regra_antiga() -> None:
    assert _cinco_e_seis(_op(None)) == ("nao_medido", "nao_medido")
    assert _cinco_e_seis(_op(None, custo=0.2)) == ("nao_medido", "sim")
    assert _cinco_e_seis(_op(None, fontes=["https://exemplo.test/a"])) == ("sim", "sim")


def test_o_estado_mostrado_nunca_baixa_da_base() -> None:
    """6 tem base `implementado`: o "não" desta operação não o rebaixa (o `_ORDEM` só sobe)."""
    c = {x["id"]: x for x in rel.criterios(_op("falhou"), [], ambiente="real", aprendizado_disponivel=False)}
    assert (c["6"]["nesta_operacao"], c["6"]["estado"]) == ("nao", "implementado")
    assert (c["5"]["nesta_operacao"], c["5"]["estado"]) == ("sim", "provado_real")


async def test_o_relatorio_traz_a_pesquisa_sem_arroba(harness: Harness) -> None:
    pid = _persona(harness, "Olivia", "android-02")
    _conta(harness, pid, "qa-user-91", sessao_em="android-02")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-pesquisa-no-relatorio"))
    resumo = {"estado": "reaproveitada_do_livro", "criterio": "≥2 fato(s) vivos sobre @pagina.alvo", "minimo_fatos": 2,
              "frescor_ate": "2099-01-01T00:00:00.000Z", "custo_usd": 0.0,
              "fatos": [{"item": "li-0001", "origem": "pesquisa", "frescor_ate": "2099-01-01T00:00:00.000Z",
                         "confianca": "confirmado"}]}
    s._pesquisa = lambda op_, custo_usd: dict(resumo)  # type: ignore[method-assign]
    r = s.relatorio(op["id"], None)
    p = r["pesquisa"]
    assert isinstance(p, dict) and p["estado"] == "reaproveitada_do_livro" and p["fatos"] == resumo["fatos"]
    assert p["criterio"] == "≥2 fato(s) vivos sobre @[omitido]"
    assert "pagina.alvo" not in json.dumps(r, ensure_ascii=False)
    c = {x["id"]: x["nesta_operacao"] for x in r["criterios"]}  # type: ignore[attr-defined]
    assert (c["5"], c["6"]) == ("sim", "sim")
    s._pesquisa = lambda op_, custo_usd: None  # type: ignore[method-assign]
    assert s.relatorio(op["id"], None)["pesquisa"] is None                             # sem assunto: não pediu

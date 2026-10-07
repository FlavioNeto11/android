"""31.235: o campo `pesquisa` de GET /api/operacoes/{id} (reaproveitada do Livro, paga, falhou ou não rodou).

O que estes testes protegem:
* o domínio (`resumo_da_pesquisa.resumo`): os quatro estados, `None` sem assunto, o critério por extenso, o menor
  frescor, e que do fato saem só a referência, a origem, o frescor e a confiança (nem texto nem URL);
* o caminho inteiro no harness: a pesquisa da operação ESCREVE a memória (falha, Livro que cobre, pesquisa paga) e o
  GET do serviço lê o estado certo de cada uma; a rota HTTP traz o campo com a configuração da instalação.

Nível de prova: `simulated` (banco do harness, provedor e Livro falsos; nenhuma chamada paga).
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.config import PesquisaCfg
from app.main import create_app
from app.modules.learning.domain.reaproveitamento_da_pesquisa import FatoDoLivro
from app.modules.operacoes.infrastructure.servico import AlvoPedido, ServicoDeOperacoes
from app.modules.pedidos.domain.memoria import Entrada
from app.modules.pedidos.domain.resumo_da_pesquisa import resumo
from app.modules.pedidos.infrastructure.pesquisa_da_operacao import PesquisaDaOperacao
from app.planning.pesquisa import PesquisaBruta, PesquisaRequest

from .conftest import Harness
from .test_operacoes import _conta, _pedido, _persona
from .test_pesquisa_da_operacao import PRECOS, _bruta

pytestmark = pytest.mark.asyncio
FUTURO = "2099-01-01T00:00:00.000Z"
ASSUNTO = "coleção de outono da loja"


def _e(chave: str, valor: str, *, tipo: str = "descoberta", frescor: str | None = FUTURO,
       origem: str = "pesquisa") -> Entrada:
    return Entrada(chave=chave, tipo=tipo, valor=valor, origem=origem, confianca="confirmado", frescor_ate=frescor)


async def test_o_resumo_de_cada_estado() -> None:
    assert resumo([], pediu=False, ligada=True, minimo_fatos=2, custo_usd=0.0) is None
    nada = resumo([_e("nota", "da ocorrência", origem="ocorrencia")], pediu=True, ligada=True, minimo_fatos=2,
                  custo_usd=0.0)
    assert nada is not None and (nada["estado"], nada["fatos"], nada["frescor_ate"]) == ("nao_rodou", [], None)
    assert "ainda não rodou" in str(nada["criterio"])
    desligada = resumo([], pediu=True, ligada=False, minimo_fatos=2, custo_usd=0.0)
    assert desligada is not None and "desligada" in str(desligada["criterio"])

    livro = [_e("livro.L-1", "a coleção usa tecido reciclado", frescor="2098-01-01T00:00:00.000Z"),
             _e("livro.L-2", "o desfile é em março"),
             _e("pesquisa.estado", "reaproveitado do Livro (31.231): 2 fato(s), itens L-1, L-2; frescor até x; "
                                   "critério: ≥2 fato(s) vivos e confirmados", tipo="progresso")]
    r = resumo(livro, pediu=True, ligada=True, minimo_fatos=2, custo_usd=0.0)
    assert r is not None and r["estado"] == "reaproveitada_do_livro" and r["minimo_fatos"] == 2
    assert r["criterio"] == "≥2 fato(s) vivos e confirmados" and r["frescor_ate"] == "2098-01-01T00:00:00.000Z"
    assert r["fatos"] == [{"item": "L-1", "origem": "pesquisa", "frescor_ate": "2098-01-01T00:00:00.000Z",
                           "confianca": "confirmado"},
                          {"item": "L-2", "origem": "pesquisa", "frescor_ate": FUTURO, "confianca": "confirmado"}]
    assert "tecido" not in json.dumps(r, ensure_ascii=False)              # nada do texto do fato

    paga = [_e("pesquisa.abc", "a coleção usa tecido reciclado", frescor="2097-01-01T00:00:00.000Z"),
            _e("fonte.def", "Loja — https://loja.exemplo.com/outono", tipo="fonte"),
            _e("pesquisa.estado", "1 fato(s), 1 fonte(s), 2 busca(s)", tipo="progresso")]
    p = resumo(paga, pediu=True, ligada=True, minimo_fatos=2, custo_usd=0.052612)
    assert p is not None and (p["estado"], p["custo_usd"], p["fatos"]) == ("paga", 0.0526, [])
    assert p["criterio"] == "o Livro não cobriu o pedido; pesquisa paga: 1 fato(s), 1 fonte(s), 2 busca(s)"
    assert p["frescor_ate"] == "2097-01-01T00:00:00.000Z"
    assert "http" not in json.dumps(p) and "tecido" not in json.dumps(p, ensure_ascii=False)
    sem_livro = resumo(paga, pediu=True, ligada=True, minimo_fatos=0, custo_usd=0.05)
    assert sem_livro is not None and str(sem_livro["criterio"]).startswith("pesquisa paga")

    falhou = resumo([_e("pesquisa.estado", "a pesquisa falhou; nova tentativa depois da espera", tipo="progresso")],
                    pediu=True, ligada=True, minimo_fatos=2, custo_usd=0.0)
    assert falhou is not None and (falhou["estado"], falhou["frescor_ate"]) == ("falhou", None)


def _servico(h: Harness, cfg: PesquisaCfg) -> ServicoDeOperacoes:
    st = h.state
    assert st is not None
    return ServicoDeOperacoes(st.db, st.runs, st.social_repo, st.approval_service, st.settings.get, st.bus,
                              st.cfg.file.ai.prices, pesquisa=cfg)


async def test_o_get_le_o_que_a_pesquisa_da_operacao_escreveu(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    cfg = PesquisaCfg(enabled=True)
    pid = _persona(harness, "Olivia", "android-02")
    _conta(harness, pid, "qa-user-71", sessao_em="android-02")
    s = _servico(harness, cfg)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-31-235", assunto=ASSUNTO))
    sem_assunto = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-31-235-sem"))
    assert s.ler(sem_assunto["id"])["pesquisa"] is None
    lido: Any = s.ler(op["id"])["pesquisa"]
    assert lido["estado"] == "nao_rodou" and lido["minimo_fatos"] == cfg.reaproveitar_min_fatos
    run_id = str(op["alvos"][0]["run_id"])

    async def quebra(req: PesquisaRequest) -> PesquisaBruta:
        raise RuntimeError("provedor")

    async def paga(req: PesquisaRequest) -> PesquisaBruta:
        return _bruta(("a coleção usa tecido reciclado", ["https://loja.exemplo.com/outono"]))

    def limpar() -> None:
        st.db.execute("DELETE FROM pedido_memoria WHERE operacao_id=?", (op["id"],))

    # 1. a pesquisa paga falha: o GET diz `falhou`
    assert await PesquisaDaOperacao(st.db, cfg, PRECOS).pesquisar_se_preciso(
        op["id"], run_id=run_id, contexto="", chamar=quebra) is None
    assert s.ler(op["id"])["pesquisa"]["estado"] == "falhou"  # type: ignore[index]
    # 2. o Livro cobre: `reaproveitada_do_livro`, com os itens e o critério, sem o texto do fato
    limpar()
    livro = [FatoDoLivro(ref=f"li-000{i}", texto=f"fato do livro {i}", estado="published", frescor_ate=FUTURO,
                         dominios=("loja.exemplo.com",), operacao_de_origem="op-0") for i in (1, 2)]
    assert await PesquisaDaOperacao(st.db, cfg, PRECOS, fatos_do_livro=lambda _op: livro).pesquisar_se_preciso(
        op["id"], run_id=run_id, contexto="", chamar=quebra) is not None
    reaproveitada: Any = s.ler(op["id"])["pesquisa"]
    assert reaproveitada["estado"] == "reaproveitada_do_livro"
    assert [f["item"] for f in reaproveitada["fatos"]] == ["li-0001", "li-0002"]
    assert reaproveitada["frescor_ate"] == FUTURO and "critério" not in reaproveitada["criterio"]
    assert reaproveitada["criterio"] and "fato do livro" not in json.dumps(reaproveitada, ensure_ascii=False)
    # 3. o Livro não cobre: a pesquisa paga roda e o GET diz `paga`, sem URL nem texto de fato
    limpar()
    assert await PesquisaDaOperacao(st.db, cfg, PRECOS, fatos_do_livro=lambda _op: []).pesquisar_se_preciso(
        op["id"], run_id=run_id, contexto="", chamar=paga) is not None
    pago: Any = s.ler(op["id"])["pesquisa"]
    assert pago["estado"] == "paga" and pago["fatos"] == [] and pago["frescor_ate"]
    assert "http" not in json.dumps(pago) and "tecido" not in json.dumps(pago, ensure_ascii=False)


async def test_a_rota_traz_o_campo_com_a_configuracao_da_instalacao(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Olivia", "android-02")
    _conta(harness, pid, "qa-user-72", sessao_em="android-02")
    op = _servico(harness, PesquisaCfg()).criar(_pedido([AlvoPedido(pid)], chave="teste-op-31-235-http",
                                                        assunto=ASSUNTO))
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/operacoes/{op['id']}")
    assert r.status_code == 200
    pesquisa = r.json()["pesquisa"]
    instalada = harness.cfg.file.ai.pesquisa
    assert pesquisa["estado"] == "nao_rodou" and pesquisa["minimo_fatos"] == instalada.reaproveitar_min_fatos
    assert ("desligada" in pesquisa["criterio"]) is not instalada.enabled

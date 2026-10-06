"""31.179: a hipótese da pesquisa promovida pela leitura do alvo.

Medido na onda 1 (06/10): a pesquisa deixou 6 de 8 fatos como `hipotese` (uma fonte só), e nada os reavaliava. A
leitura do alvo é fonte independente da web: quando ela traz as âncoras do fato, ele passa a `confirmado`.

O que estes testes protegem:
* a régua das âncoras: números inteiros como aparecem, #tag, @perfil, nome próprio fora do começo da frase; 2 ou mais,
  uma forte; todas presentes, palavra inteira;
* na ordem do 31.169 (pesquisa na criação, leitura depois) e na inversa (pesquisa refeita depois da leitura), a
  hipótese com as âncoras na leitura vira `confirmado`, com a observação da leitura na evidência, a versão + 1, a origem
  `pesquisa` e o frescor da pesquisa; a sem as âncoras fica `hipotese`;
* a leitura divergente de um agente (`incerto`) não promove; a hipótese vencida não é tocada;
* no 31.163, a hipótese da pesquisa diz por quê, e a leitura que sustenta o fato aparece como o fato da leitura.

Nível de prova: `simulated` (banco de teste, provedor falso; nenhuma IA).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import PesquisaCfg
from app.db import Database
from app.modules.pedidos.domain import aprendizado_da_operacao as resp
from app.modules.pedidos.domain.hipoteses import ancoras, confirmada_pela_leitura
from app.modules.pedidos.infrastructure.conhecimento_da_operacao import ConhecimentoDaOperacao
from app.modules.pedidos.infrastructure.pesquisa_da_operacao import PesquisaDaOperacao
from app.planning.pesquisa import PesquisaRequest

from .test_conhecimento_da_operacao import _com_operacao
from .test_pedidos_modelo import _banco, _run
from .test_pesquisa_da_operacao import A, B, PRECOS, _bruta, _operacoes

AURORA = "A coleção Aurora chega em 12/10 com #aurora"
DESCONTO = "Desconto de 15% para Clientes em 2026"
POST = "Chegou a nova coleção Aurora! Dia 12/10 nas lojas #aurora #outono"


@pytest.fixture
def banco(tmp_path: Path) -> Database:
    db = _banco(tmp_path)
    db.migrate()
    for r in ("r-a", "r-b"):
        _run(db, r)
    _com_operacao(db, "r-a", "r-b")
    return db


def test_a_regua_das_ancoras() -> None:
    todas, fortes = ancoras(AURORA)
    assert todas == {"aurora", "12/10", "#aurora"} and fortes == {"12/10", "#aurora"}
    assert confirmada_pela_leitura(AURORA, POST)
    assert not confirmada_pela_leitura(AURORA, "coleção aurora 12/10")             # falta a #aurora
    assert not confirmada_pela_leitura("Paulo visita o Brasil", "paulo brasil")      # sem âncora forte
    assert not confirmada_pela_leitura("chega em 12/10", "chega em 12/10")           # uma âncora só
    assert not confirmada_pela_leitura("Loja Aurora em 12/10", "loja aurora em 112/10")   # número inteiro
    assert confirmada_pela_leitura("Promoção na Lojá até 15/10 #outono", "promocao na loja ate 15/10 #outono")


async def _pesquisar(banco: Database) -> PesquisaDaOperacao:
    servico = PesquisaDaOperacao(banco, PesquisaCfg(enabled=True), PRECOS)

    async def chamar(req: PesquisaRequest) -> object:
        return _bruta((AURORA, [A]), (DESCONTO, [A]), ("tecido reciclado", [A, B]))
    assert await servico.pesquisar_se_preciso("op-1", run_id="r-a", contexto="", chamar=chamar) is not None  # type: ignore[arg-type]
    return servico


def _fatos(servico: PesquisaDaOperacao) -> dict[str, object]:
    return {e.valor: e for e in servico.repo.entradas_da_operacao("op-1") if e.chave.startswith("pesquisa.")}


async def test_a_leitura_depois_da_pesquisa_confirma_so_a_hipotese_com_as_ancoras(banco: Database) -> None:
    _operacoes(banco)
    servico = await _pesquisar(banco)
    antes = _fatos(servico)
    assert antes[AURORA].confianca == "hipotese" and antes[DESCONTO].confianca == "hipotese"   # type: ignore[attr-defined]
    k = ConhecimentoDaOperacao(banco)
    assert k.registrar_leitura("op-1", run_id="r-a", step_id=None, agente="o-a", fonte="ig", texto=POST) == "primeira"
    depois = _fatos(servico)
    leitura = k.repo.observacao_da_operacao("op-1", "leitura_do_alvo")
    aurora, era = depois[AURORA], antes[AURORA]
    assert aurora.confianca == "confirmado" and aurora.versao == era.versao + 1    # type: ignore[attr-defined]
    assert aurora.evidencia == (*era.evidencia, str(leitura["id"]))               # type: ignore[attr-defined]
    assert (aurora.origem, aurora.frescor_ate) == ("pesquisa", era.frescor_ate)   # type: ignore[attr-defined]
    assert depois[DESCONTO].confianca == "hipotese"                               # type: ignore[attr-defined]
    assert depois["tecido reciclado"].versao == antes["tecido reciclado"].versao  # type: ignore[attr-defined]


async def test_a_pesquisa_refeita_depois_da_leitura_ja_se_confere(banco: Database) -> None:
    _operacoes(banco)
    k = ConhecimentoDaOperacao(banco)
    assert k.registrar_leitura("op-1", run_id="r-a", step_id=None, agente="o-a", fonte="ig", texto=POST) == "primeira"
    servico = await _pesquisar(banco)
    assert _fatos(servico)[AURORA].confianca == "confirmado"                      # type: ignore[attr-defined]


async def test_leitura_divergente_nao_promove_e_vencida_nao_e_tocada(banco: Database) -> None:
    _operacoes(banco)
    servico = await _pesquisar(banco)
    k = ConhecimentoDaOperacao(banco)
    assert k.registrar_leitura("op-1", run_id="r-a", step_id=None, agente="o-a", fonte="ig",
                               texto="Outro post qualquer") == "primeira"
    assert k.registrar_leitura("op-1", run_id="r-b", step_id=None, agente="o-b", fonte="ig", texto=POST) == "diferente"
    assert _fatos(servico)[AURORA].confianca == "hipotese"                        # type: ignore[attr-defined]
    banco.execute("UPDATE pedido_observacoes SET valor=? WHERE operacao_id='op-1' AND nome='leitura_do_alvo' AND alvo=''",
                  (POST,))                                                       # a da operação passa a ter as âncoras
    banco.execute("UPDATE pedido_memoria SET frescor_ate='2000-01-01T00:00:00.000Z' WHERE operacao_id='op-1'")
    assert k.confirmar_hipoteses("op-1", run_id="r-b") == 0                       # vencida: fica como está


def test_no_163_a_hipotese_da_pesquisa_diz_por_que_e_a_leitura_sustenta() -> None:
    hip = resp.Item(ref="fato:pesquisa.x", tipo="fato", escopo="operacao", resumo="r", origem="pesquisa",
                    confianca="hipotese")
    assert resp.a_revisar(hip, "2026-10-06T00:00:00.000Z") == "hipótese: uma fonte só, e a leitura do alvo não a confirmou"
    leitura = resp.Item(ref="fato:alvo.conteudo", tipo="fato", escopo="operacao", resumo="o post", origem="leitura",
                        confianca="confirmado", evidencia=("obs-1",))
    fato = resp.Item(ref="fato:pesquisa.y", tipo="fato", escopo="operacao", resumo="r", origem="pesquisa",
                     confianca="confirmado", evidencia=("obs-url", "obs-1"))
    perguntas = resp.responder([leitura, fato], agora="2026-10-06T00:00:00.000Z")["perguntas"]
    [itens] = [p["itens"] for p in perguntas if p["chave"] == "fontes_que_sustentam"]  # type: ignore[union-attr, index]
    sustenta = {s["ref"]: s["fontes"] for s in itens}
    assert sustenta["fato:pesquisa.y"] == [{"ref": "obs-url"},
                                           {"ref": "fato:alvo.conteudo", "resumo": "o post", "observacao": "obs-1"}]
    assert sustenta["fato:alvo.conteudo"] == [{"ref": "obs-1"}]                   # a leitura não sustenta a si mesma

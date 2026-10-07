"""31.231: a pesquisa da operação reaproveita os fatos do Livro do mesmo assunto antes de pagar.

O que estes testes protegem:
* o critério explícito de "cobrir o pedido" (`reaproveitamento_da_pesquisa.cobertura`): pelo menos `min_fatos` fatos
  vivos, confirmados e dentro do frescor; com fontes indicadas, cada domínio indicado entre os dos fatos; `0` desliga;
  o motivo sai por extenso nos dois casos;
* o serviço: cobrindo, nenhuma chamada de IA, os fatos entram na memória como `livro.<item>` (descoberta confirmada,
  origem `pesquisa`, frescor do Livro) e a `pesquisa.estado` registra o reaproveitamento com os itens e o critério; a
  lacuna fecha; não cobrindo, a pesquisa paga roda como antes; a porta que falha não derruba a pesquisa;
* o leitor do Livro (harness): só os fatos do MESMO assunto canônico e do MESMO app da operação (aceite da Jev), com
  texto, frescor e domínios da proveniência; o `candidate` conta porque só a descoberta confirmada vira candidata.

Nível de prova: `simulated` (banco de teste, provedor falso; nenhuma chamada paga).
"""
from __future__ import annotations

import json
from typing import Any

from app.config import PesquisaCfg
from app.db import Database, now_iso
from app.modules.learning.domain.fatos_da_operacao import FatoDaOperacao, candidata
from app.modules.learning.domain.reaproveitamento_da_pesquisa import FatoDoLivro, cobertura
from app.modules.learning.infrastructure.fatos_do_livro_sql import LeitorDeFatosDoLivro
from app.modules.pedidos.domain import conhecimento_da_operacao as dominio
from app.modules.pedidos.infrastructure.pesquisa_da_operacao import CHAVE_DO_ESTADO, PesquisaDaOperacao
from app.planning.pesquisa import PesquisaBruta, PesquisaRequest

from .conftest import Harness
from .test_pesquisa_da_operacao import PRECOS, _bruta, _operacoes, banco  # noqa: F401 - a fixture

AGORA = "2026-10-07T10:00:00.000Z"
FUTURO = "2099-01-01T00:00:00.000Z"


def _f(ref: str, *, frescor: str | None = FUTURO, estado: str = "published",
       dominios: tuple[str, ...] = ("loja.exemplo.com",)) -> FatoDoLivro:
    return FatoDoLivro(ref=ref, texto=f"fato {ref}", estado=estado, frescor_ate=frescor, dominios=dominios,
                       operacao_de_origem="op-0")


def test_o_criterio_de_cobrir_o_pedido() -> None:
    dois = [_f("1"), _f("2", estado="candidate")]
    ok = cobertura(dois, fontes_indicadas=(), agora=AGORA, min_fatos=2)
    assert ok.cobre and [f.ref for f in ok.usados] == ["1", "2"] and "≥2 fato(s)" in ok.motivo
    assert not cobertura(dois, fontes_indicadas=(), agora=AGORA, min_fatos=3).cobre
    vencido = cobertura([_f("1"), _f("2", frescor="2026-10-01T00:00:00.000Z")], fontes_indicadas=(), agora=AGORA,
                        min_fatos=2)
    assert not vencido.cobre and "1 de 2" in vencido.motivo
    assert not cobertura([_f("1"), _f("2", frescor=None)], fontes_indicadas=(), agora=AGORA, min_fatos=2).cobre
    assert not cobertura([_f("1"), _f("2", estado="rejected")], fontes_indicadas=(), agora=AGORA, min_fatos=2).cobre
    com_fonte = cobertura(dois, fontes_indicadas=("https://www.loja.exemplo.com/x",), agora=AGORA, min_fatos=2)
    assert com_fonte.cobre and "fontes indicadas cobertas" in com_fonte.motivo
    sem_fonte = cobertura(dois, fontes_indicadas=("https://jornal.exemplo.org/y",), agora=AGORA, min_fatos=2)
    assert not sem_fonte.cobre and "jornal.exemplo.org" in sem_fonte.motivo
    assert "desligado" in cobertura(dois, fontes_indicadas=(), agora=AGORA, min_fatos=0).motivo


async def test_cobrindo_nao_paga_e_registra(banco: Database) -> None:  # noqa: F811
    _operacoes(banco)
    pedidos: list[str] = []

    async def chamar(req: PesquisaRequest) -> PesquisaBruta:
        pedidos.append(req.assunto)
        return _bruta(("tecido reciclado", ["https://loja.exemplo.com/outono", "https://www.jornal.exemplo.org/m"]))

    operacoes: list[str] = []

    def da_operacao(operacao_id: str, _assunto: str) -> list[FatoDoLivro]:
        operacoes.append(operacao_id)
        return [_f("11"), _f("12", frescor="2098-01-01T00:00:00.000Z")]

    servico = PesquisaDaOperacao(banco, PesquisaCfg(enabled=True), PRECOS, fatos_do_livro=da_operacao)
    feito = await servico.pesquisar_se_preciso("op-1", run_id="r-a", contexto="", chamar=chamar)
    assert feito is not None and (feito.reaproveitados, feito.buscas, feito.fontes) == (2, 0, 0)
    assert pedidos == [] and operacoes == ["op-1"]
    entradas = {e.chave: e for e in servico.repo.entradas_da_operacao("op-1")}
    livro = entradas["livro.11"]
    assert (livro.tipo, livro.origem, livro.confianca, livro.frescor_ate) == ("descoberta", "pesquisa", "confirmado",
                                                                              FUTURO)
    estado = entradas[CHAVE_DO_ESTADO].valor
    assert "reaproveitado do Livro (31.231): 2 fato(s), itens 11, 12" in estado and "critério:" in estado
    assert entradas[CHAVE_DO_ESTADO].frescor_ate == "2098-01-01T00:00:00.000Z"            # o menor frescor dos usados
    assert "[fato] livro.11: fato 11" in dominio.bloco(entradas.values(), agora=banco.agora_iso())
    assert not servico.lacuna("op-1")
    assert await servico.pesquisar_se_preciso("op-1", run_id="r-a", contexto="", chamar=chamar) is None


async def test_nao_cobrindo_ou_falhando_a_pesquisa_paga_roda(banco: Database) -> None:  # noqa: F811
    _operacoes(banco)
    pedidos: list[str] = []

    async def chamar(req: PesquisaRequest) -> PesquisaBruta:
        pedidos.append(req.assunto)
        return _bruta(("tecido reciclado", ["https://loja.exemplo.com/outono", "https://www.jornal.exemplo.org/m"]))

    def quebrado(operacao_id: str, _assunto: str) -> list[FatoDoLivro]:
        raise RuntimeError("banco")

    so_um = PesquisaDaOperacao(banco, PesquisaCfg(enabled=True), PRECOS, fatos_do_livro=lambda _op, _assunto: [_f("11")])
    feito = await so_um.pesquisar_se_preciso("op-1", run_id="r-a", contexto="", chamar=chamar)
    assert feito is not None and feito.reaproveitados == 0 and pedidos == ["coleção de outono da loja"]
    assert not any(e.chave.startswith("livro.") for e in so_um.repo.entradas_da_operacao("op-1"))
    banco.execute("DELETE FROM pedido_memoria WHERE operacao_id='op-1'")
    falhou = PesquisaDaOperacao(banco, PesquisaCfg(enabled=True), PRECOS, fatos_do_livro=quebrado)
    assert (await falhou.pesquisar_se_preciso("op-1", run_id="r-a", contexto="", chamar=chamar)) is not None
    assert len(pedidos) == 2


def test_o_leitor_do_livro_le_so_o_assunto_e_o_app(harness: Harness) -> None:
    st = harness.state
    pacote = "com.pocqa.messenger"                                   # o app `qa-messenger` do harness

    def fato(chave: str, texto: str, assunto: str, *, app: str = pacote, confianca: str = "confirmado") -> Any:
        return candidata(FatoDaOperacao(operacao_id="op-0", chave=chave, tipo="descoberta", texto=texto,
                                        confianca=confianca, frescor_ate=FUTURO, pacote=app,
                                        assunto=assunto, dominios=("loja.exemplo.com",)), AGORA)

    assert fato("pesquisa.h", "talvez em março", "semana de moda", confianca="hipotese") is None   # nunca candidata
    a = st.learning.propor(fato("pesquisa.a", "a coleção usa tecido reciclado", "Coleção de Outono da Loja"))
    st.learning.propor(fato("pesquisa.b", "o desfile é em março", "semana de moda"))
    st.learning.propor(fato("pesquisa.c", "a coleção sai em setembro", "Coleção de Outono da Loja",
                            app="com.instagram.android"))           # mesmo assunto, outro app: não cobre
    agora = now_iso()
    st.db.execute("INSERT INTO operacoes(id, command, app_id, acao_final, max_usd, assunto, fontes, status,"
                  " idempotency_key, corpo_sha256, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("op-9", "comente", "qa-messenger", "preparar", 1.0, "coleção de outono da loja", "[]", "em_curso",
                   "k-op-9", "x", agora, agora))
    lidos = LeitorDeFatosDoLivro(st.db).da_operacao("op-9")
    assert [(f.ref, f.texto, f.frescor_ate, f.dominios, f.operacao_de_origem) for f in lidos] == [
        (a.id, "a coleção usa tecido reciclado", FUTURO, ("loja.exemplo.com",), "op-0")]
    assert LeitorDeFatosDoLivro(st.db).do_assunto("coleção de outono da loja", "") == ()
    assert LeitorDeFatosDoLivro(st.db).do_assunto("", pacote) == ()
    assert LeitorDeFatosDoLivro(st.db).da_operacao("op-inexistente") == ()
    assert json.dumps([f.texto for f in lidos])                                # só texto do fato, sem marca crua

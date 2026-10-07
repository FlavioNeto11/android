"""31.248: a operação sem assunto pesquisa com o assunto que nasce da leitura do alvo.

Medido na onda 2 de 07/10 (op-…096a28, leitura só leitura): a operação não tinha `assunto` nem `fontes`, e a pesquisa
(critérios 5 e 6 do dono) nem chegou a consultar a lacuna: `pesquisar_se_preciso` voltava `None` sem assunto. Agora:
* a regra (`reaproveitamento_da_pesquisa.assunto_da_leitura`): o recorte PÚBLICO da publicação, sem menção a conta
  (nome de terceiro não vai à busca externa), sem endereço, hashtag vira palavra, cortado na palavra; pouco texto não
  vira assunto;
* o serviço: sem assunto guardado e com a leitura válida, a pesquisa roda UMA vez com o assunto da leitura; o Livro
  (31.231) é consultado com esse assunto antes de pagar; com assunto guardado, ele vence; desligado, como antes; sem
  leitura, nada roda e nenhuma marca fecha a lacuna (a pesquisa da criação não a gasta);
* a porta de escrita: a 1ª leitura de uma operação sem assunto agenda a pesquisa, e dois alvos pagam uma vez só;
* o minerador do Livro: o fato da operação sem assunto guardado leva o mesmo assunto da leitura.

Nível de prova: `simulated` (banco de teste e harness com provedor falso; textos inventados; nenhuma chamada paga).
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

from app import gates as gates_mod
from app.config import PesquisaCfg
from app.db import Database
from app.modules.learning.domain.reaproveitamento_da_pesquisa import FatoDoLivro, assunto_da_leitura
from app.modules.learning.infrastructure.fatos_da_operacao_sql import FatosDaOperacaoParaOLivro
from app.modules.pedidos.infrastructure.conhecimento_da_operacao import ConhecimentoDaOperacao
from app.modules.pedidos.infrastructure.pesquisa_da_operacao import CHAVE_DO_ESTADO, PesquisaDaOperacao
from app.planning.capabilities import capability_of
from app.planning.pesquisa import PesquisaBruta, PesquisaRequest

from .apoio_politica import IG
from .test_conhecimento_da_operacao import LEGENDA, _com_operacao
from .test_pesquisa_da_operacao import PRECOS, _bruta, _operacoes, banco  # noqa: F401 - a fixture
from .test_porta_do_plano import _plano
from .test_pesquisa_reaproveita_o_livro import _f


def test_a_regra_do_assunto_da_leitura() -> None:
    assert assunto_da_leitura("Ainda sobre #SetembroAmarelo: cuidar de si com @perfil.inventado https://x.exemplo/y") \
        == "Ainda sobre SetembroAmarelo: cuidar de si com"
    assert assunto_da_leitura("@so.uma.mencao 😀 www.exemplo.com") is None             # pouco texto: sem assunto
    assert assunto_da_leitura(None) is None and assunto_da_leitura("") is None
    longo = assunto_da_leitura("palavra " * 60)
    assert longo is not None and len(longo) <= 160 and not longo.endswith(" ")


def _com_leitura(db: Database, texto: str = LEGENDA) -> None:
    assert ConhecimentoDaOperacao(db).registrar_leitura("op-1", run_id="r-a", step_id="s-a", agente="obj-a",
                                                        fonte="ig · CREATE_COMMENT", texto=texto) == "primeira"


async def test_sem_assunto_a_pesquisa_usa_a_leitura_uma_vez(banco: Database) -> None:  # noqa: F811
    _operacoes(banco, assunto=None)
    pedidos: list[PesquisaRequest] = []

    async def chamar(req: PesquisaRequest) -> PesquisaBruta:
        pedidos.append(req)
        return _bruta(("tecido reciclado", ["https://loja.exemplo.com/outono"]))

    livro: list[tuple[str, str]] = []

    def da_operacao(operacao_id: str, assunto: str) -> list[FatoDoLivro]:
        livro.append((operacao_id, assunto))
        return []                                                       # o Livro não cobre: a pesquisa paga roda

    servico = PesquisaDaOperacao(banco, PesquisaCfg(enabled=True), PRECOS, fatos_do_livro=da_operacao)
    # na criação ainda não há leitura: nada roda e a lacuna continua aberta (nenhuma marca a fecha)
    assert await servico.pesquisar_se_preciso("op-1", run_id="r-a", contexto="", chamar=chamar) is None
    assert pedidos == [] and servico.lacuna("op-1")
    assert not any(e.chave == CHAVE_DO_ESTADO for e in servico.repo.entradas_da_operacao("op-1"))
    _com_leitura(banco)
    feito = await servico.pesquisar_se_preciso("op-1", run_id="r-a", contexto="", chamar=chamar)
    assert feito is not None and feito.assunto_da_leitura and len(pedidos) == 1
    assert pedidos[0].assunto == assunto_da_leitura(LEGENDA) and "@" not in pedidos[0].assunto
    assert livro == [("op-1", assunto_da_leitura(LEGENDA))]            # 31.231 com o assunto da leitura
    assert await servico.pesquisar_se_preciso("op-1", run_id="r-a", contexto="", chamar=chamar) is None
    assert len(pedidos) == 1


async def test_assunto_guardado_vence_e_desligado_e_como_antes(banco: Database) -> None:  # noqa: F811
    _operacoes(banco, assunto="coleção de outono da loja")
    _com_leitura(banco)
    servico = PesquisaDaOperacao(banco, PesquisaCfg(enabled=True), PRECOS)
    assert servico.assunto_e_fontes("op-1") == ("coleção de outono da loja", ())
    banco.execute("UPDATE operacoes SET assunto=NULL WHERE id='op-1'")
    assert servico.assunto_e_fontes("op-1") == (assunto_da_leitura(LEGENDA), ())
    desligado = PesquisaDaOperacao(banco, PesquisaCfg(enabled=True, assunto_da_leitura=False), PRECOS)
    assert desligado.assunto_e_fontes("op-1") is None


async def test_o_livro_cobrindo_o_assunto_da_leitura_nao_paga(banco: Database) -> None:  # noqa: F811
    _operacoes(banco, assunto=None)
    _com_leitura(banco)
    pagas: list[str] = []

    async def chamar(req: PesquisaRequest) -> PesquisaBruta:
        pagas.append(req.assunto)
        return _bruta()

    servico = PesquisaDaOperacao(banco, PesquisaCfg(enabled=True), PRECOS,
                                 fatos_do_livro=lambda _op, _assunto: [_f("11"), _f("12")])
    feito = await servico.pesquisar_se_preciso("op-1", run_id="r-a", contexto="", chamar=chamar)
    assert feito is not None and feito.reaproveitados == 2 and pagas == []


def test_o_minerador_leva_o_assunto_da_leitura_ao_livro(banco: Database) -> None:  # noqa: F811
    _operacoes(banco, assunto=None)
    _com_leitura(banco)
    banco.execute("UPDATE operacoes SET status='concluida' WHERE id='op-1'")
    if "finished_at" not in banco.columns("operacoes"):
        banco.execute("ALTER TABLE operacoes ADD COLUMN finished_at TEXT")
    banco.execute("UPDATE operacoes SET finished_at='2026-10-07T10:00:00.000Z' WHERE id='op-1'")
    minerador = FatosDaOperacaoParaOLivro(None, None, banco)  # type: ignore[arg-type]
    assert [a for _op, _p, a in minerador._operacoes("2026-10-01T00:00:00.000Z")] == [assunto_da_leitura(LEGENDA)]  # noqa: SLF001


async def test_a_primeira_leitura_agenda_a_pesquisa_e_dois_alvos_pagam_uma_vez(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    briefing = {"content": "comente o lançamento", "caption_contains": "outono", "post_author": "@loja.nossa"}
    _plano(state, [{"key": "comentar", "cap": "CREATE_COMMENT", "bindings": briefing}], run_id="run-a")
    _plano(state, [{"key": "comentar", "cap": "CREATE_COMMENT", "bindings": briefing}], aparelho="android-02",
           run_id="run-b")
    _com_operacao(state.db, "run-a", "run-b")
    _operacoes(state.db, assunto=None)
    monkeypatch.setattr(state.cfg.file.ai, "pesquisa", PesquisaCfg(enabled=True))
    monkeypatch.setattr(state.provider, "pesquisar", state.provider.inner.pesquisar, raising=False)
    monkeypatch.setattr(gates_mod, "screen_reader_of",
                        lambda _p: SimpleNamespace(visible_content=lambda arvore: arvore.texto))

    async def ler_tela(_rt: Any, _pacote: Any) -> Any:
        return SimpleNamespace(sensitive=False, texto=LEGENDA, packages={IG})

    async def draft_response(_pid: str, **kw: Any) -> Any:
        return SimpleNamespace(content="texto", refused=False, refusal_reason=None, rationale="r",
                               memory_candidates=[]), None

    monkeypatch.setattr(state.portoes, "_ler_tela", ler_tela)
    monkeypatch.setattr(state.social, "draft_response", draft_response)
    monkeypatch.setitem(sys.modules, "app.modules.operacoes.infrastructure.estagios",
                        SimpleNamespace(registrar_estagio=lambda *_a: None))
    # a da criação: sem assunto e sem leitura, nada
    assert state.portoes.agendar_pesquisa_da_operacao("op-1") is not None
    await next(iter(state.portoes._tarefas_da_pesquisa), _nada())  # noqa: SLF001
    assert state.db.scalar("SELECT COUNT(*) FROM ai_calls WHERE model='web_search'") == 0
    cap = capability_of(IG, "CREATE_COMMENT")
    for run, aparelho in (("run-a", "android-01"), ("run-b", "android-02")):
        obj = state.db.one("SELECT * FROM objectives WHERE run_id=?", (run,))
        etapa = state.db.one("SELECT * FROM steps WHERE id=?", (f"{run}:{aparelho}:v1:comentar",))
        assert await state.portoes._draft_gate(obj, etapa, cap, obj["profile_id"], pacote=IG) is None  # noqa: SLF001
    for tarefa in list(state.portoes._tarefas_da_pesquisa):  # noqa: SLF001
        await tarefa
    assert state.db.scalar("SELECT COUNT(*) FROM ai_calls WHERE model='web_search'") == 1    # UMA pesquisa
    assert not PesquisaDaOperacao(state.db, PesquisaCfg(enabled=True), PRECOS).lacuna("op-1")


async def _nada() -> None:
    return None

"""30.12: o curador do Livro passa pelo hub de IA (`docs/design/hub-de-ia-fora-de-execucao.md` §2).

O que cada bloco prova:

- **O template e o esquema são do hub:** os enums da resposta saem das opções fechadas do aprendizado; todo campo é
  obrigatório (modo estrito) e o opcional vai como null; o hub só garante o objeto JSON.
- **Os provedores** (Anthropic, OpenAI-compatível, simulado) respondem a `review_knowledge` pelo modelo do `plan`, e
  resposta que não é JSON vira `AIError(kind="invalid_output")`.
- **O hub** marca `origem="curador"` e `ref` = `dossie_hash`, e a fatia do curador (31.6) corta ali.
- **O adaptador `CuradorDoHub`** liga o curador do 30.11 ao hub: parecer válido pelo `validar_saida`, custo MEDIDO na
  linha de `ai_calls` (id e US$ lidos de volta), simulado sem custo e sem aviso, orçamento do hub interrompendo o lote,
  sem laço e sem resposta a tempo como recusa.

Prova `simulated`: provedores falsos, banco SQLite migrado, laço próprio numa thread. Nenhuma chamada paga.
"""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.db import Database
from app.events import EventBus
from app.modules.learning.domain.curador import OPCOES_FECHADAS
from app.modules.learning.domain.orcamento_do_curador import MotivoDoCorte
from app.modules.learning.domain.vocabulario import LivroKind, SourceKind
from app.modules.learning.infrastructure.curador_do_hub import CuradorDoHub
from app.planning import costs
from app.planning.curador import (MODELO_SIMULADO, ParecerBruto, PedidoDeParecer, esquema_do_parecer,
                                  parecer_simulado)
from app.planning.provider import AIError, Usage
from app.planning.simulated_provider import SimulatedProvider
from app.taskqueue.repository import Repository

from .test_anthropic_provider import _resp, provider as provedor_anthropic
from .test_learning_curador import PRECOS, Mundo, db  # noqa: F401 - `db` é a fixture do banco do curador
from .test_openai_provider import _resposta, provider as provedor_openai
from .test_origem_e_rubrica_de_ia import ProvedorDeTodosOsUsos, _banco, _gasta, _hub

OPCOES = {**{k: list(v) for k, v in OPCOES_FECHADAS.items()},
          "evidencias_citadas": ["item:licao:L1", "ev:1"], "alvo": ["licao:L2"]}
PEDIDO = PedidoDeParecer(dossie={"item": {"id": "item:licao:L1"}, "evidencias": {"lista": []}}, opcoes=OPCOES,
                         classe="B", ref="hash-1")
PARECER = {"decisao": "observar", "evidencias_citadas": ["ev:1"], "alvo": None, "confianca": "media",
           "conclusao": None, "faixa": "B", "causa": "evidencia_insuficiente", "riscos": [], "inconsistencias": [],
           "falta": ["execucao_real"]}


# ====================================================================== o template e o esquema
def test_esquema_leva_os_enums_das_opcoes_e_todo_campo_e_obrigatorio() -> None:
    e = esquema_do_parecer(OPCOES)
    assert e["additionalProperties"] is False and set(e["required"]) == set(e["properties"])
    p = e["properties"]
    assert p["decisao"]["enum"] == OPCOES["decisao"]
    assert p["evidencias_citadas"]["items"]["enum"] == ["item:licao:L1", "ev:1"]
    assert {"type": "null"} in p["alvo"]["anyOf"] and p["alvo"]["anyOf"][0]["enum"] == ["licao:L2"]
    assert p["riscos"]["items"]["enum"] == OPCOES["riscos"] and p["confianca"]["enum"] == ["baixa", "media", "alta"]
    # Os campos são exatamente os do contrato do aprendizado (§8.3): nada a mais, nada a menos.
    from app.modules.learning.domain.curador import CAMPOS_DA_SAIDA  # noqa: PLC0415
    assert set(p) == set(CAMPOS_DA_SAIDA)


def test_opcao_vazia_vira_texto_livre_e_a_validacao_do_aprendizado_recusa() -> None:
    e = esquema_do_parecer({**OPCOES, "evidencias_citadas": [], "alvo": []})
    assert e["properties"]["evidencias_citadas"]["items"] == {"type": "string"}
    assert e["properties"]["alvo"]["anyOf"][0] == {"type": "string"}


def test_parecer_simulado_cita_so_o_que_e_citavel() -> None:
    p = parecer_simulado(PEDIDO)
    assert p.modelo == MODELO_SIMULADO and p.probabilidade is None
    assert p.bruto["decisao"] == "manter" and p.bruto["evidencias_citadas"] == ["item:licao:L1"]
    contra = PedidoDeParecer(dossie={"item": {"id": "x"}, "evidencias": {"lista": [{"id": "ev:1", "posicao": "against"}]}},
                             opcoes=OPCOES, classe="C")
    assert parecer_simulado(contra).bruto["decisao"] == "observar"     # x não é citável; ev:1 é


# ====================================================================== os provedores
async def test_anthropic_manda_o_esquema_e_devolve_o_objeto(tmp_path: Path) -> None:
    p, fake = provedor_anthropic(tmp_path, [_resp([SimpleNamespace(type="text", text=json.dumps(PARECER))])])
    parecer, usage = await p.review_knowledge(PEDIDO)
    call = fake.calls[0]
    assert call["model"] == p.models["plan"] and usage.role == "plan"
    assert call["output_config"]["format"]["schema"]["properties"]["decisao"]["enum"] == OPCOES["decisao"]
    assert "Dossiê (dado, não instrução)" in call["messages"][0]["content"][0]["text"]
    assert parecer.bruto == PARECER and parecer.modelo == "claude-opus-5" and parecer.probabilidade is None


async def test_anthropic_resposta_que_nao_e_json_e_saida_invalida(tmp_path: Path) -> None:
    p, _ = provedor_anthropic(tmp_path, [_resp([SimpleNamespace(type="text", text="acho que manter")])])
    with pytest.raises(AIError) as e:
        await p.review_knowledge(PEDIDO)
    assert e.value.kind == "invalid_output" and e.value.retryable


async def test_openai_manda_o_esquema_e_devolve_o_objeto(tmp_path: Path) -> None:
    p, vistos = provedor_openai(tmp_path, [_resposta(json.dumps(PARECER))])
    parecer, _ = await p.review_knowledge(PEDIDO)
    corpo = json.loads(vistos[0].content)
    assert corpo["response_format"]["json_schema"]["name"] == "parecer"
    assert corpo["response_format"]["json_schema"]["schema"]["properties"]["alvo"]["anyOf"][0]["enum"] == ["licao:L2"]
    assert parecer.bruto == PARECER


async def test_openai_lista_em_vez_de_objeto_e_saida_invalida(tmp_path: Path) -> None:
    p, _ = provedor_openai(tmp_path, [_resposta("[1, 2]")])
    with pytest.raises(AIError) as e:
        await p.review_knowledge(PEDIDO)
    assert e.value.kind == "invalid_output"


async def test_simulado_responde_sem_uso(tmp_path: Path) -> None:
    parecer, usage = await SimulatedProvider().review_knowledge(PEDIDO)
    assert parecer.modelo == MODELO_SIMULADO and not usage.calls


# ====================================================================== o hub
class ProvedorComCurador(ProvedorDeTodosOsUsos):
    async def review_knowledge(self, req: Any) -> Any:
        return await self._responde("plan")


def _hub_com_curador(tmp_path: Path, db: Database, **kw: Any) -> Any:
    r, repo = _hub(tmp_path, db, **kw)
    fake = ProvedorComCurador("anthropic", "claude-opus-5")
    for chave in list(r._por_chave):                                    # noqa: SLF001
        r._por_chave[chave] = fake                                      # noqa: SLF001
    r.providers = {papel: fake for papel in r.providers}
    return r


def test_hub_marca_origem_curador_e_ref_do_dossie(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    r = _hub_com_curador(tmp_path, db)
    _, usage = asyncio.run(r.review_knowledge(PEDIDO))
    assert (usage.origem, usage.ref, usage.role) == ("curador", "hash-1", "plan")
    (tmp_path / "ev").mkdir()
    Repository(db, EventBus(db), tmp_path / "ev").add_usage(None, None, usage)
    assert [(x["origem"], x["ref"], x["run_id"]) for x in db.query("SELECT origem, ref, run_id FROM ai_calls")] == [
        ("curador", "hash-1", None)]
    db.close()


def test_fatia_do_curador_corta_o_review_knowledge(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    _gasta(db, "curador", 400_000)                      # US$ 2,00 > 10 % de US$ 10
    r = _hub_com_curador(tmp_path, db, teto_dia=10.0)
    with pytest.raises(AIError) as e:
        asyncio.run(r.review_knowledge(PEDIDO))
    assert (e.value.kind, e.value.motivo) == ("budget", "fatia_curador")
    db.close()


# ====================================================================== o adaptador, com o curador do 30.11
@pytest.fixture
def laco() -> Iterator[asyncio.AbstractEventLoop]:
    """O laço do processo, numa thread: a volta do curador chama de outra thread, como no `AppState`."""
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    t.join(5)
    loop.close()


class HubFalso:
    """Um hub "real" de teste: responde pela regra do simulado, mas com uso medido (como o roteador devolve)."""

    simulated = False

    def __init__(self, *, erro: AIError | None = None, demora: float = 0.0) -> None:
        self.erro, self.demora = erro, demora
        self.pedidos: list[PedidoDeParecer] = []

    async def review_knowledge(self, req: PedidoDeParecer) -> tuple[ParecerBruto, Usage]:
        self.pedidos.append(req)
        if self.demora:
            await asyncio.sleep(self.demora)
        if self.erro is not None:
            raise self.erro
        u = Usage(calls=1, role="plan", model="modelo-x", provider="anthropic", input_tokens=20_000, output_tokens=500)
        u.origem, u.ref = "curador", req.ref
        return ParecerBruto(bruto=parecer_simulado(req).bruto, modelo="modelo-x"), u


def _ligar(m: Mundo, hub: Any, laco: asyncio.AbstractEventLoop | None, tmp_path: Path, **kw: Any) -> CuradorDoHub:
    (tmp_path / "ev").mkdir(exist_ok=True)
    fila = Repository(m.db, EventBus(m.db), tmp_path / "ev")
    adaptador = CuradorDoHub(hub, m.db, registrar_uso=lambda u: fila.add_usage(None, None, u), precos=lambda: PRECOS,
                             **kw)
    if laco is not None:
        adaptador.ligar_laco(laco)
    m.curador._curador = adaptador                                     # noqa: SLF001 - troca o simulado pelo do hub
    return adaptador


def test_hub_real_grava_parecer_valido_com_custo_medido_e_avisa(db: Database, laco: Any,  # noqa: F811
                                                                 tmp_path: Path) -> None:
    m = Mundo(db)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)       # classe B: o parecer de IA real avisa o dono
    hub = HubFalso()
    adaptador = _ligar(m, hub, laco, tmp_path)
    r = m.volta()
    assert r.revisadas == (ref,) and r.avisos == 1 and r.invalidas == ()
    [linha] = m.revisoes()
    assert (linha["validade"], linha["provedor"], linha["modelo"], linha["simulated"]) == ("ok", "anthropic",
                                                                                         "modelo-x", 0)
    [pedido] = hub.pedidos
    assert pedido.ref == linha["dossie_hash"] and pedido.classe == "B"
    [chamada] = db.query("SELECT * FROM ai_calls")
    assert (chamada["origem"], chamada["ref"], chamada["run_id"]) == ("curador", linha["dossie_hash"], None)
    # A resposta leva o custo MEDIDO na linha (id e US$), não uma estimativa à parte.
    dossie = m.curador._dossies.dossie(m.servico.entrada(LivroKind.LICAO, ref))  # noqa: SLF001
    assert dossie is not None
    resposta = adaptador.revisar(m.curador._pedido(dossie))                    # noqa: SLF001
    ultima = db.one("SELECT * FROM ai_calls ORDER BY id DESC LIMIT 1")
    assert resposta.ai_call_id == ultima["id"] and resposta.usd == pytest.approx(costs.row_usd(PRECOS, ultima))
    assert resposta.usd and resposta.usd > 0
    assert resposta.provedor == "anthropic"                                   # por resposta, não do estado do adaptador


def test_hub_simulado_grava_simulado_sem_custo_e_sem_aviso(db: Database, laco: Any,  # noqa: F811
                                                          tmp_path: Path) -> None:
    m = Mundo(db)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    adaptador = _ligar(m, SimulatedProvider(), laco, tmp_path)
    r = m.volta()
    assert r.revisadas == (ref,) and r.avisos == 0
    [linha] = m.revisoes()
    assert (linha["validade"], linha["simulated"], linha["modelo"]) == ("ok", 1, MODELO_SIMULADO)
    assert adaptador.simulado and db.query("SELECT id FROM ai_calls") == []


def test_orcamento_do_hub_interrompe_o_lote(db: Database, laco: Any, tmp_path: Path) -> None:  # noqa: F811
    m = Mundo(db)
    a = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    b = m.licao(efeito=False, fonte=SourceKind.MANUAL, sufixo=" 2")
    erro = AIError("fatia do curador esgotada", kind="budget")
    _ligar(m, HubFalso(erro=erro), laco, tmp_path)
    r = m.volta()
    assert r.revisadas == () and m.revisoes() == []
    # O primeiro do lote leva o corte do hub e o resto do lote para ali (o outro pode ter saído antes pela régua da hora).
    assert MotivoDoCorte.LOTE_INTERROMPIDO in {r.cortados[a], r.cortados[b]}


def test_sem_laco_e_sem_resposta_a_tempo_sao_recusa_do_provedor(db: Database, laco: Any,  # noqa: F811
                                                               tmp_path: Path) -> None:
    m = Mundo(db)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    _ligar(m, HubFalso(), None, tmp_path)                              # o AppState ainda não subiu
    assert m.volta().cortados[ref] is MotivoDoCorte.ERRO_DO_PROVEDOR
    _ligar(m, HubFalso(demora=2.0), laco, tmp_path, timeout_s=0.2)
    assert m.volta().cortados[ref] is MotivoDoCorte.ERRO_DO_PROVEDOR
    assert m.revisoes() == []


def test_roteador_misto_com_plan_simulado_grava_simulado_e_nao_avisa(db: Database, laco: Any,  # noqa: F811
                                                                    tmp_path: Path) -> None:
    """Regressão: o roteador diz `simulated = False` quando nem todo papel é simulado; o parecer que o `plan` simulado
    deu não pode passar por real (iria ao dono como aviso). O `simulado` é o da resposta."""
    m = Mundo(db)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)       # classe B: só parecer real avisaria
    r, _ = _hub(tmp_path, db)
    for chave in list(r._por_chave):                                    # noqa: SLF001
        r._por_chave[chave] = SimulatedProvider()                       # noqa: SLF001
    assert r.simulated is False
    adaptador = _ligar(m, r, laco, tmp_path)
    v = m.volta()
    assert v.revisadas == (ref,) and v.avisos == 0
    [linha] = m.revisoes()
    assert (linha["simulated"], linha["modelo"]) == (1, MODELO_SIMULADO) and adaptador.simulado

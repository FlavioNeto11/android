"""Item 31.35 (parte B): até 3 ações por decisão do ator, e a espera depois do `open_url` sem decisão, DESLIGADOS.

Achado real do 28.12 (r-20261004090000-bbfe54): 10 decisões numa etapa só de navegação, 7 delas toque ou rolagem
seguidos, ~US$ 0,0093 cada. Com `ai.acoes_por_decisao` > 1 a decisão de etapa SEM efeito libera chamadas paralelas
de ferramenta; o executor acha o MESMO alvo na tela nova (os ids `eN` mudam depois de cada ação) e descarta o resto
da fila se ele sumiu. Desligado (o padrão), o pedido é o de sempre. Liga só depois do A/B com teto.

Nível de prova: `simulated` (cliente Anthropic falso e árvores escritas à mão; nenhuma chamada de IA).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.automation.hierarchy import parse_hierarchy
from app.config import AiCfg
from app.planning import prompts
from app.planning.provider import Decision, DecisionRequest, Usage
from app.planning.simulated_provider import SimulatedProvider
from app.taskqueue.executor import _fila_encadeada, _proxima_encadeada

from .conftest import CountingProvider, Harness
from .test_anthropic_provider import SCREEN, _resp, ctx, provider


def _no(i: int, texto: str, y: int) -> str:
    return (f'<node index="{i}" text="{texto}" resource-id="" class="android.widget.TextView" package="p"'
            f' content-desc="" clickable="true" enabled="true" bounds="[0,{y}][720,{y + 80}]" />')


def _arvore(*nos: str) -> str:
    return '<hierarchy rotation="0">' + "".join(nos) + "</hierarchy>"


def test_desligado_por_padrao_e_o_texto_do_ator_nao_muda() -> None:
    ai = AiCfg()
    assert ai.acoes_por_decisao == 1 and ai.espera_apos_open_url is False
    req = DecisionRequest(ctx=ctx(), screen=SCREEN)
    assert prompts.actor_user_text(req).endswith("Escolha UMA ferramenta.")
    tres = prompts.actor_user_text(DecisionRequest(ctx=ctx(), screen=SCREEN, encadear=3))
    assert "até 3 chamadas em ordem" in tres and "`scroll`" in tres


async def test_anthropic_so_libera_chamadas_paralelas_quando_pode_encadear(tmp_path: Path) -> None:
    t1 = SimpleNamespace(type="tool_use", name="tap", id="t1", input={"rationale": "a", "element_id": "e1"})
    t2 = SimpleNamespace(type="tool_use", name="tap", id="t2", input={"rationale": "b", "element_id": "e2"})
    p, fake = provider(tmp_path, [_resp([t1, t2], stop="tool_use"), _resp([t1, t2], stop="tool_use")])
    uma, _ = await p.decide(DecisionRequest(ctx=ctx(), screen=SCREEN))
    assert fake.calls[0]["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert uma.tool == "tap" and uma.extras == []           # desligado: a 2ª chamada é ignorada, como sempre
    duas, _ = await p.decide(DecisionRequest(ctx=ctx(), screen=SCREEN, encadear=3))
    assert fake.calls[1]["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": False}
    assert [d.args["element_id"] for d in duas.extras] == ["e2"]
    assert fake.calls[0]["tools"] == fake.calls[1]["tools"]  # a lista de ferramentas (prefixo do cache) é a mesma


def test_fila_para_no_scroll_e_nunca_leva_controle_nem_digitacao() -> None:
    d = [Decision("tap", {"element_id": "e1"}), Decision("scroll", {"direction": "down"}),
         Decision("tap", {"element_id": "e2"})]
    assert [x.tool for x in _fila_encadeada(d)] == ["tap", "scroll"]
    assert _fila_encadeada([Decision("type_text", {"text": "x"}), Decision("tap", {})]) == []
    assert _fila_encadeada([Decision("step_done", {})]) == []


def test_o_alvo_e_achado_na_tela_nova_pelo_elemento_e_nao_pelo_id() -> None:
    antes = parse_hierarchy(_arvore(_no(0, "Frutas", 100), _no(1, "Legumes", 200)))
    depois = parse_hierarchy(_arvore(_no(0, "Cabeçalho novo", 0), _no(1, "Frutas", 100), _no(2, "Legumes", 200)))
    legumes_antes = next(e for e in antes.elements if e.text == "Legumes")
    legumes_depois = next(e for e in depois.elements if e.text == "Legumes")
    assert legumes_antes.id != legumes_depois.id          # o id posicional mudou: usá-lo cru tocaria outro elemento
    history: list[str] = []
    fila = [Decision("tap", {"element_id": legumes_antes.id, "rationale": "r"})]
    d = _proxima_encadeada(fila, antes, depois, history)
    assert d is not None and d.args["element_id"] == legumes_depois.id and history == [] and fila == []


def test_alvo_que_sumiu_ou_por_coordenada_acaba_com_a_fila() -> None:
    antes = parse_hierarchy(_arvore(_no(0, "Frutas", 100), _no(1, "Legumes", 200)))
    depois = parse_hierarchy(_arvore(_no(0, "Outra tela", 100)))
    legumes = next(e for e in antes.elements if e.text == "Legumes")
    history: list[str] = []
    fila = [Decision("tap", {"element_id": legumes.id}), Decision("tap", {"element_id": "e0"})]
    assert _proxima_encadeada(fila, antes, depois, history) is None and fila == []
    assert "descartada" in history[-1]
    fila = [Decision("tap", {"x": 10, "y": 10})]
    assert _proxima_encadeada(fila, antes, antes, history) is None and "coordenada" in history[-1]
    fila = [Decision("scroll", {"direction": "down"})]
    assert _proxima_encadeada(fila, antes, depois, history) is not None   # rolar sem alvo é a última e vale


class _Encadeador(SimulatedProvider):
    """O provedor simulado, com uma espera encadeada atrás de cada decisão que pede encadear."""

    def __init__(self) -> None:
        super().__init__()
        self.pedidos: list[int] = []

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        self.pedidos.append(req.encadear)
        d, uso = await super().decide(req)
        if req.encadear > 1 and d.tool not in ("step_done", "step_blocked"):
            d.extras = [Decision("wait_for", {"rationale": "encadeada", "seconds": 0})]
        return d, uso


async def test_execucao_ligada_executa_a_encadeada_com_a_mesma_chamada_de_ia(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    prov = _Encadeador()
    h.ai = CountingProvider(prov)
    h.cfg.file.ai.acoes_por_decisao = 3
    state = await h.boot()
    try:
        run = h.run(["android-01"])
        await h.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "waiting_user"))
        assert prov.pedidos and set(prov.pedidos) <= {1, 3}
        acoes = state.db.query("SELECT a.tool, a.ai_call_id, a.source FROM actions a JOIN attempts t ON t.id=a.attempt_id"
                               " JOIN steps s ON s.id=t.step_id WHERE s.run_id=? ORDER BY a.intent_at", (run.id,))
        por_decisao: dict[int, list[str]] = {}
        for a in acoes:
            if a["ai_call_id"] is not None:
                por_decisao.setdefault(a["ai_call_id"], []).append(a["tool"])
        # a encadeada aponta para o decide que a escolheu: uma decisão, duas ações, a 2ª é a espera encadeada
        assert any(len(t) >= 2 and t[-1] == "wait_for" for t in por_decisao.values()), por_decisao
    finally:
        await state.stop()

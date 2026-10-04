"""Item 31.37: a etapa que lê valor manda a imagem já na 1ª decisão, e só LER a tela não conta como ciclo.

Achado real (12.3, d62546 no android-01 e e7df7c no 03, US$ 0,14 e 0,12): 7 decisões por execução, uma delas um
`observe_screen(need_image)` pago só para pedir a imagem, e `observe_screen` repetido contou como ciclo e escalou 2 ou 3
decisões para o Opus. A contagem de ciclo segue valendo para ferramenta que age na tela.

Nível de prova: `simulated` (harness na porta 5640 e provedor simulado; nenhuma IA paga).
"""
from __future__ import annotations

from pathlib import Path

from app.automation.hierarchy import parse_hierarchy
from app.config import AiCfg
from app.planning.provider import Decision, DecisionRequest, Usage
from app.planning.simulated_provider import SimulatedProvider
from app.taskqueue.executor import _IMAGEM_VAI, FORA_DO_CICLO

from .conftest import CountingProvider, Harness

RICA = parse_hierarchy('<hierarchy rotation="0">' + "".join(
    f'<node index="{i}" text="item {i}" resource-id="" class="android.widget.TextView" package="p" content-desc=""'
    f' clickable="true" enabled="true" bounds="[0,{i * 90}][720,{i * 90 + 80}]" />' for i in range(12)) + "</hierarchy>")


def test_primeira_decisao_da_etapa_que_le_valor_leva_a_imagem(harness: Harness) -> None:
    executor = harness.state.scheduler.executor                 # type: ignore[union-attr]
    auto = AiCfg(image_policy="auto")
    motivo = lambda **kw: executor._motivo_da_imagem(RICA, judged_step=False, trouble=False, requested=False,  # noqa: E731
                                                       ai=auto, **kw)
    assert motivo(first=True, le_valor=True) == "primeira_da_leitura" and "primeira_da_leitura" in _IMAGEM_VAI
    assert motivo(first=False, le_valor=True) == "arvore_rica"          # só a 1ª decisão
    assert motivo(first=True, le_valor=False) == "arvore_rica"          # etapa que não lê: como sempre


class _OlhaDuasVezes(SimulatedProvider):
    """Na 1ª etapa, antes do que o simulado faria, pede a tela duas vezes seguidas (o padrão da leitura do Outlook)."""

    def __init__(self) -> None:
        super().__init__()
        self.tiers: list[int] = []
        self.olhadas = 0

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        self.tiers.append(req.tier)
        if self.olhadas < 2:
            self.olhadas += 1
            return Decision("observe_screen", {"rationale": "conferir a tela", "need_image": False}), Usage()
        return await super().decide(req)


async def test_ler_a_tela_duas_vezes_nao_e_ciclo_nem_escala(tmp_path: Path) -> None:
    assert FORA_DO_CICLO == {"observe_screen", "read_value"}
    h = Harness(tmp_path, 1)
    prov = _OlhaDuasVezes()
    h.ai = CountingProvider(prov)
    state = await h.boot()
    try:
        run = h.run(["android-01"])
        await h.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "waiting_user"))
        assert prov.olhadas == 2 and len(prov.tiers) >= 3
        assert prov.tiers[:3] == [0, 0, 0], prov.tiers       # antes: a 3ª decisão subia para o modelo de escalonamento
        assert state.repo.run_row(run.id)["status"] == "completed"
    finally:
        await state.stop()

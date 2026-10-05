"""Item 31.73: a recusa por `sobreposicao` só vale quando o elemento citado cobre ao menos 15 % da tela.

Achado real (r-20261005071303-f24955, Chrome, android-09, 05/10): o juiz marcou `sobreposicao` citando o banner "Abra
o app e ganhe frete grátis" ([0,160][720,308], 11,6 % da tela 720 x 1280), mas o próprio texto dele dizia que a causa
principal era o conteúdo errado. A limpeza foi inserida à toa, falhou, e o desfecho escondeu a causa. Na
r-20261004190200-5b56e6 o mesmo banner era modal ([0,160][720,1232], 83,8 %) e cobria de fato.

Duas camadas: o prompt do juiz (outra causa = false) e a regra estrutural (`executor.cobre_a_tela`). Nível de prova:
`simulated`.
"""
from __future__ import annotations

from typing import Any

from app.planning import prompts
from app.planning.provider import Usage, Verdict
from app.taskqueue.executor import FRACAO_DA_SOBREPOSICAO, Cobertura, cobre_a_tela

from .conftest import Harness
from .test_sobreposicao import TERMINAIS, _juiz_com_ref, _limpeza

BANNER_F24955 = Cobertura("", "Abra o app e ganhe frete grátis na sua primeira compra", (0, 160, 720, 308))
MODAL_5B56E6 = Cobertura("", "Abra o app e ganhe frete grátis na sua primeira compra", (0, 160, 720, 1232))


def test_as_duas_arvores_reais_do_banner() -> None:
    assert FRACAO_DA_SOBREPOSICAO == 0.15
    assert not cobre_a_tela(BANNER_F24955, 720, 1280)          # 11,6 %: faixa no topo, não esconde o alvo
    assert cobre_a_tela(MODAL_5B56E6, 720, 1280)               # 83,8 %: cobria de fato
    assert cobre_a_tela(BANNER_F24955, 0, 0)                   # sem o tamanho da tela, vale como antes


def test_o_prompt_do_juiz_diz_que_outra_causa_e_false() -> None:
    assert "OUTRA causa além da cobertura" in prompts.VERIFIER_SYSTEM


def _juiz_citando(inner: Any, chave: str, escolher: Any) -> None:
    """O juiz da etapa `chave` diz uma vez "não, algo cobre", citando o elemento que `escolher(tree)` devolve."""
    verify0 = inner.verify
    vezes: dict[str, int] = {}

    async def verify(req: Any) -> Any:
        vezes[req.ctx.step_key] = vezes.get(req.ctx.step_key, 0) + 1
        if req.ctx.step_key == chave and vezes[chave] == 1:
            return Verdict(satisfied="no", evidence="[simulado] um aviso cobre; e o conteúdo é outro",
                           sobreposicao=True, cobre=escolher(req.screen.tree).id), Usage()
        return await verify0(req)

    inner.verify = verify


def _area(e: Any) -> int:
    return (e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1])


async def _rodar(harness: Harness, escolher: Any) -> Any:
    harness.pular_o_tempo()
    harness.encurtar_verificacao(1.5)
    _juiz_citando(harness.ai.inner, "verify_sent", escolher)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    return _limpeza(harness, run.id)


async def test_pelo_laco_elemento_pequeno_nao_insere_a_limpeza(harness: Harness) -> None:
    """O juiz cita o MENOR elemento da tela (o app de teste não tem nada acima de 4,7 %): a recusa vale como "não"
    comum, sem limpeza."""
    limpeza = await _rodar(harness, lambda tree: min(tree.elements, key=_area))
    assert limpeza is None


async def test_pelo_laco_aviso_que_cobre_segue_inserindo_a_limpeza(harness: Harness) -> None:
    """O juiz cita um aviso que cobre 62,5 % da tela (`AVISO_QUE_COBRE`): a sobreposição vale, como no 31.40."""
    harness.pular_o_tempo()
    harness.encurtar_verificacao(1.5)
    _juiz_com_ref(harness, "verify_sent", cobertas=1)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert _limpeza(harness, run.id) is not None

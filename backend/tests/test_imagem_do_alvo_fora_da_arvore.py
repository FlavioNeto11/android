"""31.232: o modelo forte que confere o efeito recebe a imagem quando o alvo do efeito não está na árvore.

Medido na onda 1 (07/10, `ai_calls` em `mode=ro`): o commit refeito no forte (31.223) e o rejulgamento do "sim" com
efeito (17.10) saíram sem imagem (`arvore_rica`). Com o alvo escolhido na árvore (`element_id`), a árvore diz o que há
ali; com um toque por coordenada, o forte conferia às cegas. A mesma regra explícita vale para os dois juízes:
* alvo na árvore: a régua de sempre decide (aqui, sem imagem);
* alvo fora (coordenada, elemento ausente ou ferramenta sem elemento): imagem, com `image_reason = alvo_fora_da_arvore`;
* `ai.imagem_quando_alvo_fora_da_arvore: false`: como antes. A regra só acrescenta a imagem; a tela sensível e a
  política `never` continuam mandando. O julgamento barato (verify) não muda.

Nível de prova: `simulated` (harness com provedor e aparelho falsos; nenhuma IA).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.planning.provider import MOTIVOS_DA_IMAGEM, Decision
from app.taskqueue.executor import _IMAGEM_VAI, alvo_na_arvore

from .conftest import Harness
from .fake_device import W
from .test_cascata_ator_barato import TERMINAIS, _modelos_diferentes, _verify_com
from .test_forte_so_no_commit import _navega_antes_do_commit
from .test_observabilidade_das_chamadas import _linhas


def _por_coordenada(h: Harness, *, tier: int) -> None:
    """O commit do envio (`send_button`) no `tier` dado sai por coordenada, no espaço do modelo, em vez do element_id."""
    inner = h.ai.inner
    decide0 = inner.decide

    async def decide(req: Any) -> Any:
        decision, usage = await decide0(req)
        if req.ctx.step_key == "send_message" and req.tier == tier and decision.tool == "tap":
            el = req.screen.tree.by_id(decision.args.get("element_id") or "")
            if el is not None and el.resource_id.endswith("send_button"):
                escala = W / req.screen.width
                cx, cy = el.center
                args = dict(decision.args)
                args.update(element_id=None, x=round(cx / escala), y=round(cy / escala))
                return Decision(tool="tap", args=args), usage
        return decision, usage

    inner.decide = decide


def _commit_no_forte(h: Harness) -> list[dict[str, Any]]:
    return [c for c in h.ai.calls if c["role"] == "decide" and c["step"] == "send_message" and c["tier"] == 1]


def test_o_alvo_na_arvore_e_a_regua(harness: Harness) -> None:
    harness.cfg.file.ai.image_policy = "auto"         # o harness manda a imagem sempre; aqui vale a régua
    harness.cfg.file.ai.rich_tree_min_elements = 1    # a árvore do aparelho falso conta como rica (a da onda 1)
    el = SimpleNamespace(id="e1")
    arvore = SimpleNamespace(by_id=lambda i: el if i == "e1" else None)
    assert alvo_na_arvore(SimpleNamespace(element_id="e1", x=None, y=None), arvore)            # type: ignore[arg-type]
    assert not alvo_na_arvore(SimpleNamespace(element_id=None, x=10, y=20), arvore)            # type: ignore[arg-type]
    assert not alvo_na_arvore(SimpleNamespace(element_id="e9", x=None, y=None), arvore)        # type: ignore[arg-type]
    assert not alvo_na_arvore(SimpleNamespace(text="oi"), arvore)                              # type: ignore[arg-type]
    assert "alvo_fora_da_arvore" in MOTIVOS_DA_IMAGEM and "alvo_fora_da_arvore" in _IMAGEM_VAI
    executor = harness.state.scheduler.executor                 # type: ignore[union-attr]
    rica = SimpleNamespace(sensitive=False, elements=[SimpleNamespace(text="x", desc="", clickable=True,
                                                                      editable=False)] * 50)
    base = dict(judged_step=False, first=False, trouble=False, requested=False)
    assert executor._motivo_da_imagem(rica, **base) == "arvore_rica"
    assert executor._motivo_da_imagem(rica, **base, alvo_fora=True) == "alvo_fora_da_arvore"
    harness.cfg.file.ai.image_policy = "never"
    assert executor._motivo_da_imagem(rica, **base, alvo_fora=True) == "politica_nunca"
    harness.cfg.file.ai.image_policy = "auto"
    sensivel = SimpleNamespace(sensitive=True, elements=rica.elements)
    assert executor._motivo_da_imagem(sensivel, **base, alvo_fora=True) == "sensivel"
    harness.cfg.file.ai.imagem_quando_alvo_fora_da_arvore = False
    assert executor._motivo_da_imagem(rica, **base, alvo_fora=True) == "arvore_rica"


async def test_commit_refeito_com_alvo_na_arvore_vai_sem_imagem(harness: Harness) -> None:
    harness.cfg.file.ai.image_policy = "auto"         # o harness manda a imagem sempre; aqui vale a régua
    harness.cfg.file.ai.rich_tree_min_elements = 1    # a árvore do aparelho falso conta como rica (a da onda 1)
    harness.cfg.file.ai.strong_model_for_side_effect = True
    _navega_antes_do_commit(harness)
    run = harness.run(["android-01"])
    assert (await harness.wait_run(run.id)).status == "completed"
    assert [c["image"] for c in _commit_no_forte(harness)] == [False]
    forte = [d for d in _linhas(harness, run.id, "decide") if d["tier"] == 1]
    assert [d["image_reason"] for d in forte] == ["arvore_rica"], forte
    assert len(harness.fakes["android-01"].messages) == 1


async def test_commit_refeito_com_alvo_por_coordenada_vai_com_imagem(harness: Harness) -> None:
    harness.cfg.file.ai.image_policy = "auto"         # o harness manda a imagem sempre; aqui vale a régua
    harness.cfg.file.ai.rich_tree_min_elements = 1    # a árvore do aparelho falso conta como rica (a da onda 1)
    harness.cfg.file.ai.strong_model_for_side_effect = True
    _navega_antes_do_commit(harness)
    _por_coordenada(harness, tier=0)                  # o barato propõe o commit por coordenada; o forte o refaz
    run = harness.run(["android-01"])
    assert (await harness.wait_run(run.id)).status == "completed"
    assert [c["image"] for c in _commit_no_forte(harness)] == [True]
    forte = [d for d in _linhas(harness, run.id, "decide") if d["tier"] == 1]
    assert [(d["image_reason"], d["with_image"]) for d in forte] == [("alvo_fora_da_arvore", 1)]
    assert len(harness.fakes["android-01"].messages) == 1


async def test_commit_por_coordenada_desligado_como_antes(harness: Harness) -> None:
    harness.cfg.file.ai.image_policy = "auto"         # o harness manda a imagem sempre; aqui vale a régua
    harness.cfg.file.ai.rich_tree_min_elements = 1    # a árvore do aparelho falso conta como rica (a da onda 1)
    harness.cfg.file.ai.strong_model_for_side_effect = True
    harness.cfg.file.ai.imagem_quando_alvo_fora_da_arvore = False
    _navega_antes_do_commit(harness)
    _por_coordenada(harness, tier=0)
    run = harness.run(["android-01"])
    assert (await harness.wait_run(run.id)).status == "completed"
    assert [c["image"] for c in _commit_no_forte(harness)] == [False]


def _rejulgamentos(h: Harness, run_id: str) -> list[tuple[str | None, int | None]]:
    return [(v["image_reason"], v["with_image"]) for v in _linhas(h, run_id, "verify")
            if v["motivo"] == "rejulgamento" and v["escalate"] == "sim_com_efeito"]


async def test_rejulgamento_com_alvo_na_arvore_usa_a_tela_do_juiz(harness: Harness) -> None:
    harness.cfg.file.ai.image_policy = "auto"         # o harness manda a imagem sempre; aqui vale a régua
    harness.cfg.file.ai.rich_tree_min_elements = 1    # a árvore do aparelho falso conta como rica (a da onda 1)
    _modelos_diferentes(harness)
    _verify_com(harness, barato="yes", forte="yes", vistos=[])
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    rej = _rejulgamentos(harness, run.id)
    assert rej and all(r != "alvo_fora_da_arvore" for r, _ in rej), rej


async def test_rejulgamento_com_alvo_por_coordenada_vai_com_imagem(harness: Harness) -> None:
    harness.cfg.file.ai.image_policy = "auto"         # o harness manda a imagem sempre; aqui vale a régua
    harness.cfg.file.ai.rich_tree_min_elements = 1    # a árvore do aparelho falso conta como rica (a da onda 1)
    _modelos_diferentes(harness)
    _verify_com(harness, barato="yes", forte="yes", vistos=[])
    _por_coordenada(harness, tier=0)                  # o envio sai por coordenada (sem subida: a etapa fica no barato)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert len(harness.fakes["android-01"].messages) == 1
    rej = _rejulgamentos(harness, run.id)
    assert ("alvo_fora_da_arvore", 1) in rej, rej
    julgados = [v for v in _linhas(harness, run.id, "verify") if v["motivo"] == "julgamento"]
    assert all(v["image_reason"] != "alvo_fora_da_arvore" for v in julgados)      # o juiz barato não muda

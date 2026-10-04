"""Item 31.26 (opção A): a prova local `sent_text` dispensa o PRIMEIRO julgamento da etapa com nível `sent`.

A medida do 31.24 mostrou que cada etapa de envio paga 1 julgamento barato e 1 rejulgamento do modelo de escalonamento,
porque o nível de entrega exigido manda o juiz olhar mesmo quando a árvore já provou o envio (a bolha com o texto e o
campo de escrita sem ele). Agora, na SEND_MESSAGE do Instagram, a prova local substitui SÓ o julgamento barato; o
rejulgamento do 17.10 continua e é quem decide. Sem a prova, com marca pendente, com nível acima de `sent`, sem o
rejulgamento (desligado ou mesmo modelo) ou com a chave desligada, tudo segue como antes.

Nível de prova: `simulated` (árvores montadas com os ids das telas reais e um verificador de mentira). A prova `real`
fica `not_run`: DM entre contas nossas segue barrada até ~02/11 (ADR-055).
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from app.models import DeliveryLevel, Postcondition, StepDTO
from app.planning.provider import Usage, Verdict, VerifyRequest
from app.taskqueue.executor import StepExecutor

from .test_dm_verificador import _conversa, _envio, _executor, _verificar


class _Juizes:
    """O barato e o de escalonamento, contados à parte. O de escalonamento responde `escalonamento`."""

    def __init__(self, escalonamento: str = "yes") -> None:
        self.barato = 0
        self.escalonamento = 0
        self.resposta = escalonamento

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        if req.escalate:
            self.escalonamento += 1
            return Verdict(satisfied=self.resposta, evidence="[teste] escalonamento",  # type: ignore[arg-type]
                           delivery_level=DeliveryLevel.sent), Usage()
        self.barato += 1
        return Verdict(satisfied="yes", evidence="[teste] barato", delivery_level=DeliveryLevel.sent), Usage()


def _com_modelos_diferentes(ex: StepExecutor) -> None:
    """A config de teste usa o mesmo modelo nas duas funções; o rejulgamento do 17.10 só age com modelos diferentes."""
    original = ex.cfg.ai_role

    def papel(role: str, profile: str | None = None) -> Any:
        r = original(role, profile)
        if role != "verify":
            return r
        return r.model_copy(update={"model": "modelo-barato"}) if hasattr(r, "model_copy") else dataclasses.replace(
            r, model="modelo-barato")

    ex.cfg.ai_role = papel  # type: ignore[method-assign]


def _envio_com_nivel(nivel: DeliveryLevel = DeliveryLevel.sent) -> StepDTO:
    etapa = _envio()
    post = Postcondition(kind=etapa.postcondition.kind, value=etapa.postcondition.value,
                         description=etapa.postcondition.description, required_delivery_level=nivel)
    return etapa.model_copy(update={"postcondition": post})


def _pronto(tmp_path: Path, juizes: _Juizes, telas: list[Any] | None = None) -> StepExecutor:
    ex = _executor(tmp_path, telas or [_conversa()], juizes)  # type: ignore[arg-type]
    _com_modelos_diferentes(ex)
    return ex


async def test_sent_text_provado_dispensa_o_barato_e_o_rejulgamento_decide(tmp_path: Path) -> None:
    juizes = _Juizes()
    ok, texto = await _verificar(_pronto(tmp_path, juizes), _envio_com_nivel())
    assert ok, texto
    assert (juizes.barato, juizes.escalonamento) == (0, 1)


async def test_o_rejulgamento_que_discorda_nao_deixa_fechar(tmp_path: Path) -> None:
    juizes = _Juizes(escalonamento="no")
    ok, _texto = await _verificar(_pronto(tmp_path, juizes), _envio_com_nivel())
    assert not ok
    assert juizes.escalonamento >= 1


async def test_texto_ainda_no_campo_vai_ao_juiz_barato(tmp_path: Path) -> None:
    juizes = _Juizes()
    from .test_dm_verificador import CONTEUDO
    await _verificar(_pronto(tmp_path, juizes, [_conversa(no_campo=CONTEUDO)]), _envio_com_nivel())
    assert juizes.barato >= 1                                     # a árvore não provou: o juiz decide, como antes


async def test_nivel_acima_de_sent_nao_e_dispensado(tmp_path: Path) -> None:
    juizes = _Juizes()
    await _verificar(_pronto(tmp_path, juizes), _envio_com_nivel(DeliveryLevel.delivered))
    assert juizes.barato >= 1                                     # "entregue" a árvore não prova


async def test_sem_rejulgamento_nada_muda(tmp_path: Path) -> None:
    juizes = _Juizes()
    ex = _pronto(tmp_path, juizes)
    ex.cfg.file.ai.rejudge_yes_on_side_effect = False
    ok, texto = await _verificar(ex, _envio_com_nivel())
    assert ok, texto
    assert (juizes.barato, juizes.escalonamento) == (1, 0)        # a prova local sozinha não fecha o efeito


async def test_chave_desligada_volta_ao_de_antes(tmp_path: Path) -> None:
    juizes = _Juizes()
    ex = _pronto(tmp_path, juizes)
    ex.cfg.file.ai.sent_text_dispensa_primeiro_juiz = False
    ok, texto = await _verificar(ex, _envio_com_nivel())
    assert ok, texto
    assert (juizes.barato, juizes.escalonamento) == (1, 1)

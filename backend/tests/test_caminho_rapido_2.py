"""Caminho rápido 2 (29.45): LT-5, LT-6 e LT-12 do relatório de latência de 03/10.

- LT-5: a verificação que recebeu "não" numa tela PARADA sai cedo com o mesmo veredito (3 sondagens na mesma
  assinatura), em vez de pagar o orçamento inteiro (15 s; 60 s `patient`). Não sai cedo onde a mudança tem dono fora da
  tela: `patient` com `pending_marks` declaradas (ADR-055) e nível de entrega acima de `sent` (entregue/lida chega sem a
  árvore mudar antes). Assinatura nova reabre a contagem. Nunca converte falha em sucesso.

Nível de prova: `simulated` — aparelho e juiz falsos (o `_verify` chamado direto, como em `test_dm_verificador`). A parede
das "NÃO comprovada" no ambiente real é o aceite, `not_run` até o deploy.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.automation.hierarchy import UiTree, parse_hierarchy
from app.devices.manager import Observation
from app.models import DeliveryLevel, Postcondition, StepDTO, StepStatus
from app.modules.capabilities.domain.definition import CapabilityRef
from app.modules.capabilities.infrastructure.catalog_provider import CatalogCapabilityProvider
from app.modules.capabilities.infrastructure.catalog_registry import CatalogCapabilityRegistry
from app.planning.provider import Usage, Verdict, VerifyRequest
from app.taskqueue.executor import SONDAGENS_DA_TELA_PARADA, StepExecutor

from .conftest import make_config

IID = "android-01"
PKG = "com.pocqa.messenger"


def _tela(*textos: str) -> UiTree:
    corpo = "".join(f'<node class="android.widget.TextView" text="{t}" resource-id="{PKG}:id/t{i}" '
                    f'bounds="[0,{i * 80}][700,{i * 80 + 60}]"/>' for i, t in enumerate(textos))
    return parse_hierarchy(f"<hierarchy>{corpo}</hierarchy>")


class _Aparelho:
    """Devolve as telas na ordem e repete a última: é o `devices` que o `_verify` lê."""

    def __init__(self, telas: list[UiTree]) -> None:
        self.telas = telas
        self.leituras = 0

    async def observe(self, rt: object, *, timeout: float, imagem: bool) -> Observation:
        tela = self.telas[min(self.leituras, len(self.telas) - 1)]
        self.leituras += 1
        return Observation(frame_id=str(self.leituras), ts="2026-10-03T09:00:00Z", width=720, height=1280, jpeg=None,
                           tree=tela, package=PKG, sensitive=False)

    async def completar_imagem(self, rt: object, obs: Observation, *, timeout: float, lado_max: int) -> Observation:
        return obs


class _JuizQueDizNao:
    def __init__(self) -> None:
        self.chamadas = 0

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        self.chamadas += 1
        return Verdict(satisfied="no", evidence="[teste] a conversa certa não está aberta"), Usage()


def _executor(tmp_path: Path, telas: list[UiTree], *, max_chamadas: int = 2) -> tuple[StepExecutor, _Aparelho,
                                                                                         _JuizQueDizNao]:
    ex = object.__new__(StepExecutor)
    ex.cfg = make_config(tmp_path, 1)
    ai = ex.cfg.file.ai
    ai.judge_wait_s = 0.01                     # cada sondagem custa ~nada: o que se conta é quantas houve
    ai.effect_settle_s = 0.0
    ai.verify_budget_min_s = 0.5
    ai.verify_budget_patient_s = 0.6           # o teto de quem NÃO sai cedo: ~60 sondagens de 0,01 s
    ai.verify_max_model_calls = max_chamadas
    ex.repo = SimpleNamespace(decision=lambda *a, **k: None)  # type: ignore[assignment]
    aparelho, juiz = _Aparelho(telas), _JuizQueDizNao()
    ex.devices = aparelho  # type: ignore[assignment]
    ex.provider = juiz  # type: ignore[assignment]
    ex.capabilities = CatalogCapabilityProvider(CatalogCapabilityRegistry(lambda _app: None))

    async def _ai(run_id: str, objective_id: str | None, fabrica: object, **_kw: object) -> Verdict:
        resultado, _uso = await fabrica()  # type: ignore[operator]
        return resultado  # type: ignore[no-any-return]

    ex._ai = _ai  # type: ignore[method-assign]
    return ex, aparelho, juiz


def _etapa(*, side_effect: bool = False, nivel: DeliveryLevel | None = None) -> StepDTO:
    return StepDTO(id=f"r-lt5:{IID}:v1:abrir", run_id="r-lt5", objective_id=f"r-lt5:{IID}", instance_id=IID,
                   plan_version=1, seq=1, key="abrir", title="Abrir a conversa com a Ana", goal="abrir a conversa",
                   depends_on=[], side_effect=side_effect,
                   postcondition=Postcondition(kind="model_judged", value="conversa com a Ana aberta",
                                               description="A conversa com a Ana está aberta.",
                                               required_delivery_level=nivel),
                   timeout_s=60, max_attempts=2, attempts=1, status=StepStatus.verifying)


async def _verificar(ex: StepExecutor, etapa: StepDTO, *, patient: bool,
                     capability: CapabilityRef | None = None) -> tuple[bool, str, float]:
    t0 = time.monotonic()
    ok, texto, _nivel, _obs, _nao = await ex._verify(  # noqa: SLF001
        SimpleNamespace(id=IID), etapa, lambda: SimpleNamespace(step_key=etapa.key, instance_id=IID),  # type: ignore[arg-type]
        "r-lt5", f"r-lt5:{IID}", time.monotonic() + 60.0, 5.0, patient=patient, facts=[], capability=capability,
        pacote=None)
    return ok, texto, time.monotonic() - t0


# ==================================================================== LT-5
async def test_lt5_tela_parada_depois_do_nao_sai_cedo_com_o_mesmo_veredito(tmp_path: Path) -> None:
    ex, aparelho, juiz = _executor(tmp_path, [_tela("Conversas", "Bruno")])
    ex.cfg.file.ai.verify_budget_s = 15.0      # o orçamento de hoje: antes, a falha esperava os 15 s inteiros
    ok, texto, dt = await _verificar(ex, _etapa(), patient=False)
    assert ok is False
    assert juiz.chamadas == 1                                      # o juiz não é consultado de novo na mesma tela
    assert aparelho.leituras == 1 + SONDAGENS_DA_TELA_PARADA       # o julgamento e as 3 sondagens paradas
    assert "a tela não mudou em 3 sondagens" in texto and "[teste]" in texto
    assert dt < 5.0


async def test_lt5_tela_que_muda_reabre_a_contagem(tmp_path: Path) -> None:
    a, b = _tela("Conversas", "Bruno"), _tela("Conversas", "Carla")
    ex, aparelho, juiz = _executor(tmp_path, [a, a, b], max_chamadas=3)
    ok, _texto, _dt = await _verificar(ex, _etapa(), patient=False)
    # A julgada; A parada (1); B julgada (a contagem volta a zero); B parada 3 vezes
    assert ok is False and juiz.chamadas == 2
    assert aparelho.leituras == 3 + SONDAGENS_DA_TELA_PARADA


async def test_lt5_efeito_disparado_sem_marca_e_nivel_appeared_tambem_sai_cedo(tmp_path: Path) -> None:
    """Os três casos de 55–60 s da janela medida: efeito, `appeared`, ação sem catálogo (sem `pending_marks`)."""
    ex, aparelho, _juiz = _executor(tmp_path, [_tela("Conversas", "Bruno")])
    ok, texto, _dt = await _verificar(ex, _etapa(side_effect=True, nivel=DeliveryLevel.appeared), patient=True)
    assert ok is False and aparelho.leituras == 1 + SONDAGENS_DA_TELA_PARADA
    assert "a tela não mudou" in texto


async def test_lt5_patient_com_marca_pendente_declarada_espera_ate_o_teto(tmp_path: Path) -> None:
    """ADR-055: o SEND_MESSAGE declara "Sending…". Com o efeito disparado, a tela parada ainda pode sair de "enviando"
    — a verificação espera o orçamento inteiro, como antes."""
    ex, aparelho, juiz = _executor(tmp_path, [_tela("Conversas", "Bruno")])
    envio = CapabilityRef("com.instagram.android", "SEND_MESSAGE")
    ok, texto, dt = await _verificar(ex, _etapa(side_effect=True), patient=True, capability=envio)
    assert ok is False and juiz.chamadas == 1
    assert aparelho.leituras > 1 + SONDAGENS_DA_TELA_PARADA * 3
    assert "a tela não mudou" not in texto
    assert dt >= 0.5


@pytest.mark.parametrize("nivel", [DeliveryLevel.delivered, DeliveryLevel.read])
async def test_lt5_nivel_que_depende_do_outro_lado_espera_ate_o_teto(tmp_path: Path, nivel: DeliveryLevel) -> None:
    ex, aparelho, _juiz = _executor(tmp_path, [_tela("Conversas", "Bruno")])
    ok, texto, _dt = await _verificar(ex, _etapa(side_effect=True, nivel=nivel), patient=True)
    assert ok is False and aparelho.leituras > 1 + SONDAGENS_DA_TELA_PARADA * 3
    assert "a tela não mudou" not in texto


async def test_lt5_uma_rodada_continua_com_uma_leitura_so(tmp_path: Path) -> None:
    ex, aparelho, juiz = _executor(tmp_path, [_tela("Conversas", "Bruno")])
    etapa = _etapa()
    ok, _texto, _nivel, _obs, _nao = await ex._verify(  # noqa: SLF001
        SimpleNamespace(id=IID), etapa, lambda: SimpleNamespace(step_key=etapa.key, instance_id=IID),  # type: ignore[arg-type]
        "r-lt5", f"r-lt5:{IID}", time.monotonic() + 60.0, 5.0, patient=False, facts=[], pacote=None, uma_rodada=True)
    assert ok is False and aparelho.leituras == 1 and juiz.chamadas == 1

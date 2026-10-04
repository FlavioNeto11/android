"""Caminho rápido 2 (29.45): LT-5, LT-6 e LT-12 do relatório de latência de 03/10.

- LT-5: a verificação que recebeu "não" numa tela PARADA sai cedo com o mesmo veredito (3 sondagens na mesma
  assinatura), em vez de pagar o orçamento inteiro (15 s; 60 s `patient`). Não sai cedo onde a mudança tem dono fora da
  tela: `patient` com `pending_marks` declaradas (ADR-055) e nível de entrega acima de `sent` (entregue/lida chega sem a
  árvore mudar antes). Assinatura nova reabre a contagem. Nunca converte falha em sucesso.
- LT-6: a etapa `app_foreground` abre o app pelo executor (estratégia `deterministic`), sem decide, uma vez por tentativa
  e só quando a receita não conduz; o pedido que falha cai no ator NA MESMA tentativa. O foco é sondado a 0,5 s nos
  primeiros 5 s (era um ciclo fixo de 2 s: a mediana do `open_app` medida em 7 d era exatamente 2,2 s).
- LT-12: a nova tentativa começa no tier 0 (antes, inteira no modelo de escalonamento) e sobe, até o fim dela, na 1ª
  decisão que repetir na mesma tela estrutural a última ação da anterior ou que dispararia o efeito; essa decisão é
  descartada antes de agir e refeita no tier 1 (`ai_calls.escalate='nova_tentativa'`).

Nível de prova: `simulated` — aparelho e juiz falsos (o `_verify` chamado direto, como em `test_dm_verificador`) e o
Harness da porta 5640 com o provedor simulado. A parede das "NÃO comprovada", a mediana do `open_app` e as etapas
`app_foreground` com decide = 0 no ambiente real são o aceite, `not_run` até o deploy.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from types import SimpleNamespace

import pytest

from app.automation import tools
from app.automation.driver import DriverError
from app.automation.hierarchy import UiTree, parse_hierarchy
from app.devices.manager import Observation
from app.models import DeliveryLevel, Postcondition, StepDTO, StepStatus
from app.modules.capabilities.domain.definition import CapabilityRef
from app.modules.capabilities.infrastructure.catalog_provider import CatalogCapabilityProvider
from app.modules.capabilities.infrastructure.catalog_registry import CatalogCapabilityRegistry
from app.planning.provider import Decision, Usage, Verdict, VerifyRequest
from app.taskqueue import executor as executor_mod
from app.taskqueue.executor import SONDAGENS_DA_TELA_PARADA, StepExecutor

from .conftest import Harness, make_config

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
    ai.judge_wait_estavel_s = 0.0              # 31.27: espera fixa; a adaptativa também lê a árvore e mudaria a conta
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


# ==================================================================== LT-6
TERMINAIS = ("completed", "completed_with_issues", "failed", "waiting_user")


def _etapa_open_app(h: Harness, run_id: str) -> dict[str, Any]:
    db = h.state.db
    etapa = db.one("SELECT id, status, driven_by, attempts FROM steps WHERE run_id=? AND key='open_app'", (run_id,))
    tentativas = db.query("SELECT strategy FROM attempts WHERE step_id=? ORDER BY number", (etapa["id"],))
    return {"status": etapa["status"], "driven_by": etapa["driven_by"], "attempts": etapa["attempts"],
            "estrategias": [t["strategy"] for t in tentativas]}


async def test_lt6_etapa_app_foreground_abre_o_app_sem_decide(harness: Harness) -> None:
    harness.cfg.file.ai.recipes = "replay"
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    etapa = _etapa_open_app(harness, run.id)
    assert harness.ai.count("decide", step="open_app") == 0
    assert etapa["status"] == "succeeded" and etapa["attempts"] == 1
    assert etapa["estrategias"] == ["deterministic"]
    assert etapa["driven_by"] == "sem_ator"            # quem conduziu não foi a IA, e nada a aprender como receita
    linha = harness.state.db.one("SELECT COUNT(*) AS n FROM events WHERE run_id=? AND message LIKE ?",  # type: ignore[union-attr]
                                 (run.id, "%aberto pelo executor, sem IA — em primeiro plano%"))
    assert linha["n"] == 1
    assert len(harness.fakes["android-01"].messages) == 1               # o resto do plano seguiu normalmente


async def test_lt6_pedido_de_abertura_que_falha_cai_no_ator_na_mesma_tentativa(harness: Harness) -> None:
    falhas = {"n": 0}
    # O aparelho falso nasce no boot do harness: o defeito entra nele antes da execução, sem corrida com o executor.
    aparelho = harness.fakes["android-01"]
    abrir0 = aparelho.open_app

    def abrir(package: str, activity: str | None) -> None:
        if falhas["n"] == 0:
            falhas["n"] += 1
            raise DriverError("am start recusado (teste)", effect_possible=False)
        abrir0(package, activity)

    aparelho.open_app = abrir  # type: ignore[method-assign]
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    etapa = _etapa_open_app(harness, run.id)
    assert falhas["n"] == 1
    assert harness.ai.count("decide", step="open_app") >= 1           # o ator assumiu
    assert etapa["status"] == "succeeded" and etapa["attempts"] == 1    # na mesma tentativa, sem retry
    assert etapa["estrategias"] == ["deterministic>ai_actor"]


async def test_lt6_desligado_volta_ao_ator(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_mod, "OPEN_APP_SEM_IA", False)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert harness.ai.count("decide", step="open_app") >= 1
    assert _etapa_open_app(harness, run.id)["estrategias"] == ["ai_actor"]


async def test_lt6_etapa_com_efeito_nunca_abre_pelo_executor(harness: Harness) -> None:
    plano0 = harness.ai.inner.plan

    async def plan(req: Any) -> Any:
        plano, uso = await plano0(req)
        for s in plano.steps:
            if s.key == "open_app":
                s.side_effect = True
        return plano, uso

    harness.ai.inner.plan = plan
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert harness.ai.count("decide", step="open_app") >= 1
    assert "deterministic" not in (_etapa_open_app(harness, run.id)["estrategias"][0] or "")


def _leitor(respostas: list[str | None]) -> tuple[Any, list[float]]:
    momentos: list[float] = []

    async def ler() -> tuple[str | None, str | None]:
        momentos.append(time.monotonic())
        return respostas[min(len(momentos) - 1, len(respostas) - 1)], None

    return ler, momentos


async def test_lt6_foco_e_sondado_a_meio_segundo_no_comeco() -> None:
    ler, momentos = _leitor([None, PKG])
    t0 = time.monotonic()
    assert await tools.esperar_foco(ler, PKG) is True
    assert len(momentos) == 2 and time.monotonic() - t0 < 1.5           # antes: o 2º olhar só depois de 2 s


async def test_lt6_depois_da_janela_volta_ao_intervalo_largo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "INTERVALO_DO_FOCO_S", 0.2)
    monkeypatch.setattr(tools, "INTERVALO_INICIAL_DO_FOCO_S", 0.01)
    monkeypatch.setattr(tools, "JANELA_INICIAL_DO_FOCO_S", 0.05)
    ler, momentos = _leitor([None] * 30 + [PKG])
    assert await tools.esperar_foco(ler, PKG, ate=time.monotonic() + 0.6) is False
    intervalos = [b - a for a, b in zip(momentos, momentos[1:])]
    assert min(intervalos[:2]) < 0.05 and intervalos[-2] >= 0.15        # fino no começo, largo depois


async def test_lt6_intervalo_encurtado_pelos_testes_continua_valendo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "INTERVALO_DO_FOCO_S", 0.02)
    ler, momentos = _leitor([None, None, PKG])
    t0 = time.monotonic()
    assert await tools.esperar_foco(ler, PKG) is True
    assert time.monotonic() - t0 < 0.3                                  # nunca o 0,5 s da janela fina


@pytest.fixture
def espera_curta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "ESPERA_DO_FOCO_S", 0.3)
    monkeypatch.setattr(tools, "INTERVALO_DO_FOCO_S", 0.02)


async def test_lt6_abertura_sem_ia_que_morre_por_anr_segue_a_regra_de_uma_reabertura(harness: Harness,
                                                                                      espera_curta: None) -> None:
    """A abertura do LT-6 entra na contagem do ANR como a da IA entrava: a 1ª morte é reaberta uma vez pelo executor,
    e a etapa fecha sem nenhum decide."""
    fake = harness.fakes["android-01"]
    fake.anr_ao_abrir = 1
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert detail.status == "completed", [(s.key, s.status, s.status_detail) for s in detail.steps]
    assert fake.calls.count("open_app") == 2                  # a do executor (LT-6) e a reabertura do ANR
    assert harness.ai.count("decide", step="open_app") == 0


async def test_lt6_segunda_morte_por_anr_para_a_etapa_sem_decide(harness: Harness, espera_curta: None) -> None:
    fake = harness.fakes["android-01"]
    fake.anr_ao_abrir = 4
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    abrir = [s for s in detail.steps if s.key == "open_app"]
    assert abrir and all(s.status == "failed" for s in abrir), [(s.key, s.status) for s in detail.steps]
    erros = [a.error or "" for a in detail.attempts if a.step_id in {s.id for s in abrir}]
    assert erros and all("parou de responder (ANR)" in e for e in erros), erros
    assert fake.calls.count("open_app") == 4                  # nenhuma 5ª partida a frio
    assert harness.ai.count("decide", step="open_app") == 0


# ==================================================================== LT-12
def _bloqueia(motivo: str = "falha forçada pelo teste") -> Any:
    return Decision(tool="step_blocked", args={"rationale": "teste", "kind": "other", "reason": motivo,
                                               "needs_user": False}), Usage()


def _roteiro(h: Harness, etapa: str, roteiro: list[Any]) -> list[int]:
    """As N primeiras decisões de `etapa` saem do `roteiro` (`None` = a do provedor simulado); devolve o tier de cada
    decisão pedida para a etapa, na ordem. A cascata do 17.10 fica desligada: o bloqueio aqui é o jeito de o teste
    forçar a nova tentativa (`fail_or_retry`), não um bloqueio a reavaliar."""
    h.cfg.file.ai.cascade_blocked_to_tier1 = False
    inner = h.ai.inner
    decide0 = inner.decide
    tiers: list[int] = []

    async def decide(req: Any) -> Any:
        if req.ctx.step_key != etapa:
            return await decide0(req)
        tiers.append(req.tier)
        i = len(tiers) - 1
        if i < len(roteiro) and roteiro[i] is not None:
            return roteiro[i]() if callable(roteiro[i]) else roteiro[i]
        return await decide0(req)

    inner.decide = decide
    return tiers


def _tentativas(h: Harness, run_id: str, etapa: str) -> int:
    db = h.state.db                                                         # type: ignore[union-attr]
    return int(db.scalar("SELECT a.n FROM (SELECT COUNT(*) AS n FROM attempts t JOIN steps s ON s.id = t.step_id "
                         "WHERE s.run_id=? AND s.key=?) a", (run_id, etapa)))


async def test_lt12_nova_tentativa_comeca_e_fica_no_tier_0(harness: Harness) -> None:
    tiers = _roteiro(harness, "open_conversation", [_bloqueia])
    run = await harness.wait_run(harness.run(["android-01"]).id, timeout=60)
    assert run.status == "completed" and _tentativas(harness, run.id, "open_conversation") == 2
    assert tiers[0] == 0 and len(tiers) >= 2
    assert all(t == 0 for t in tiers[1:]), tiers            # antes: a 2ª tentativa inteira no modelo de escalonamento


async def test_lt12_repetir_onde_a_anterior_parou_sobe_ao_tier_1_e_descarta_a_decisao(harness: Harness) -> None:
    esperar = (Decision(tool="wait_for", args={"rationale": "teste", "seconds": 0.5}), Usage())
    # tentativa 1: espera (a tela não muda) e desiste; tentativa 2: o barato pede a MESMA espera na mesma tela
    tiers = _roteiro(harness, "open_conversation", [esperar, _bloqueia, esperar])
    run = await harness.wait_run(harness.run(["android-01"]).id, timeout=60)
    assert run.status == "completed" and _tentativas(harness, run.id, "open_conversation") == 2
    assert tiers[:3] == [0, 0, 0] and len(tiers) >= 4
    assert all(t == 1 for t in tiers[3:]), tiers            # sobe e fica até o fim da tentativa
    db = harness.state.db                                                   # type: ignore[union-attr]
    esperas = db.scalar("SELECT COUNT(*) FROM actions a JOIN attempts t ON t.id = a.attempt_id JOIN steps s ON "
                        "s.id = t.step_id WHERE s.run_id=? AND s.key='open_conversation' AND a.tool='wait_for'", (run.id,))
    assert esperas == 1                                     # a repetida foi descartada antes de agir (nem gravada)
    motivos = [r["escalate"] for r in db.query(
        "SELECT c.escalate FROM ai_calls c JOIN steps s ON s.id = c.step_id WHERE s.run_id=? AND "
        "s.key='open_conversation' AND c.role='decide' AND c.tier=1", (run.id,))]
    assert motivos and set(motivos) == {"nova_tentativa"}


async def test_lt12_o_efeito_na_nova_tentativa_e_decidido_no_tier_1(harness: Harness) -> None:
    """O QA é app de prova: com o `by_risk` padrão o envio fica no tier 0 (29.31). Na nova tentativa, a decisão do
    barato que dispararia o efeito é descartada e refeita no modelo forte — e a mensagem sai uma vez só."""
    plano0 = harness.ai.inner.plan

    async def plan(req: Any) -> Any:
        plano, uso = await plano0(req)
        for s in plano.steps:
            if s.key == "send_message":
                s.max_attempts = 2                      # no plano simulado é 1: a falha viraria replano, não nova tentativa
        return plano, uso

    harness.ai.inner.plan = plan
    tiers = _roteiro(harness, "send_message", [_bloqueia])
    run = await harness.wait_run(harness.run(["android-01"]).id, timeout=60)
    assert run.status == "completed" and _tentativas(harness, run.id, "send_message") == 2
    versoes = harness.state.db.scalar(                                      # type: ignore[union-attr]
        "SELECT COUNT(DISTINCT plan_version) FROM steps WHERE run_id=? AND key='send_message'", (run.id,))
    assert versoes == 1                                     # a MESMA etapa, na 2ª tentativa (sem replano)
    assert tiers[0] == 0 and 0 in tiers[1:] and tiers[-1] == 1, tiers
    assert len(harness.fakes["android-01"].messages) == 1
    db = harness.state.db                                                   # type: ignore[union-attr]
    assert db.scalar("SELECT COUNT(*) FROM ai_calls c JOIN steps s ON s.id = c.step_id WHERE s.run_id=? AND "
                     "s.key='send_message' AND c.escalate='nova_tentativa'", (run.id,)) >= 1


async def test_lt12_a_primeira_tentativa_nao_muda(harness: Harness) -> None:
    tiers = _roteiro(harness, "open_conversation", [])
    run = await harness.wait_run(harness.run(["android-01"]).id, timeout=60)
    assert run.status == "completed" and _tentativas(harness, run.id, "open_conversation") == 1
    assert tiers and all(t == 0 for t in tiers)

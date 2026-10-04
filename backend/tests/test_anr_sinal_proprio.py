"""Pacote "anr": o app que morre por ANR tem sinal próprio, e lentidão do convidado deixa de virar laço de reabertura.

Execuções reais r-20260928165254-e31953 e r-20260928195344-02ee9e (android-06, 2 vCPU saturadas): a IA ocupou 4,5 % do
tempo. Com `hide_error_dialogs=1` (preparo do aparelho), todo ANR em primeiro plano vira morte SILENCIOSA do app
(`exit-info` reason=6, "user request after error") e o launcher volta; a IA só via "launcher", reabria o app, a partida
a frio (28–51 s) dava outro ANR, e o laço comia o orçamento (5 mortes do Instagram na 02ee9e, 6 na e31953 v3). Pior: a
leitura do foco pegava o PRIMEIRO `mCurrentFocus` do `dumpsys window`, que no Android 14 fica na seção congelada
`WINDOW MANAGER LAST ANR` — "comprovava" o Instagram na frente com o launcher na tela.

O que cada bloco prova (nível `simulated`: `_run` do adb, aparelho e provedor falsos):

- o foco, o diálogo do sistema, a prontidão e a confirmação do toque leem a seção VIVA, nunca a cópia do último ANR;
- `dumpsys activity exit-info` vira lista de mortes com a idade medida no relógio do CONVIDADO;
- `open_app` espera o foco e diz `focused=true/false`, em vez de dormir 1,5 s;
- o executor reabre UMA vez sem chamar a IA e, na segunda morte, para a etapa com o motivo e avisa no aparelho — tudo
  contado pela ETAPA, atravessando tentativas; sem prazo para uma partida a frio ele não reabre, e o prazo vencido
  depois de um ANR diz o ANR;
- o motor de sessão faz o mesmo (launcher com morte recente não é "tela não reconhecida");
- o prazo da etapa vencido dentro da chamada de IA é "prazo da etapa", não "IA indisponível".
"""
from __future__ import annotations

import asyncio
import subprocess
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.automation import tools
from app.automation.hierarchy import parse_hierarchy
from app.automation.tools import ToolContext, execute_tool, validate_call
from app.devices.adb import Adb, AdbError
from app.integrations.app_declarado.sessao import Outcome as SessaoOutcome
from app.models import Plan
from app.planning.provider import AIError, PlanRequest, Usage
from app.planning.simulated_provider import SimulatedProvider
from app.taskqueue import executor as executor_mod

from .conftest import CountingProvider, Harness
from .fake_device import FakeQaDevice
from .fake_instagram import FakeInstagram
from .test_instagram_auth import SENHA, FakeDevices, FakeRt, build, cadastrar

IG = "com.instagram.android"
LAUNCHER = "com.google.android.apps.nexuslauncher"

# ---------------------------------------------------------------- `dumpsys window` com um ANR no passado
#: Forma medida no Android 14: a seção do último ANR vem ANTES de tudo e guarda o foco daquele instante — inclusive
#: uma cópia das "display contents" (depois de "Last ANR continued"). O foco de AGORA está na seção viva, depois.
DUMPSYS_COM_ULTIMO_ANR = f"""
WINDOW MANAGER LAST ANR (dumpsys window lastanr)
  ANR time: 28 de set. de 2026 19:58:10
  Application at fault: ActivityRecord{{1b2c3d4 u0 {IG}/.activity.MainTabActivity t42}}
  Reason: Input dispatching timed out
  mCurrentFocus=Window{{9f8e7d6 u0 {IG}/com.instagram.android.activity.MainTabActivity}}
    isKeyguardShowing=true

Last ANR continued
WINDOW MANAGER DISPLAY CONTENTS (dumpsys window displays)
  Display: mDisplayId=0 rootTasks=2
  mCurrentFocus=Window{{9f8e7d6 u0 {IG}/com.instagram.android.activity.MainTabActivity}}

WINDOW MANAGER POLICY STATE (dumpsys window policy)
    isKeyguardShowing=false
WINDOW MANAGER DISPLAY CONTENTS (dumpsys window displays)
  Display: mDisplayId=0 rootTasks=2
  mCurrentFocus=Window{{1a2b3c4 u0 {LAUNCHER}/{LAUNCHER}.NexusLauncherActivity}}
  mFocusedApp=ActivityRecord{{5e6f7a8 u0 {LAUNCHER}/.NexusLauncherActivity t1}}
WINDOW MANAGER WINDOWS (dumpsys window windows)
"""


def _adb_com_saida(texto: str) -> tuple[Adb, list[str]]:
    """Adb cujo `_run` devolve sempre `texto` (a saída inteira, como se o `grep` do aparelho não filtrasse nada)."""
    comandos: list[str] = []
    adb = Adb(SimpleNamespace(), "emulator-5640")      # type: ignore[arg-type]  - `_run` é o dublê: nada sai daqui

    def run(args: list[str], **_k: Any) -> subprocess.CompletedProcess[str]:
        comandos.append(args[1] if args[:1] == ["shell"] and len(args) > 1 else " ".join(args))
        return subprocess.CompletedProcess(args, 0, texto, "")

    adb._run = run                                        # type: ignore[method-assign]
    return adb, comandos


def test_foco_vem_da_secao_viva_e_nao_da_copia_do_ultimo_anr() -> None:
    """C9: o PRIMEIRO `mCurrentFocus` é o do ANR (o Instagram); o launcher é que está na frente."""
    adb, comandos = _adb_com_saida(DUMPSYS_COM_ULTIMO_ANR)
    assert adb.current_focus() == (LAUNCHER, f"{LAUNCHER}.NexusLauncherActivity")
    # No aparelho, o `tail -n 1` já corta a leitura na última linha; o Python confere o mesmo na saída inteira.
    assert "tail -n 1" in comandos[0]


def test_dialogo_do_sistema_e_o_de_agora() -> None:
    """A cópia do ANR mostra o diálogo daquele instante; a seção viva mostra o launcher: não há diálogo agora."""
    anr = DUMPSYS_COM_ULTIMO_ANR.replace(
        f"mCurrentFocus=Window{{9f8e7d6 u0 {IG}/com.instagram.android.activity.MainTabActivity}}",
        f"mCurrentFocus=Window{{4d5e6f u0 Application Not Responding: {IG}}}")
    adb, _ = _adb_com_saida(anr)
    assert adb.system_dialog() is None
    # E o contrário: diálogo VIVO depois de uma cópia de ANR sem diálogo é diálogo.
    vivo = DUMPSYS_COM_ULTIMO_ANR.replace(
        f"mCurrentFocus=Window{{1a2b3c4 u0 {LAUNCHER}/{LAUNCHER}.NexusLauncherActivity}}",
        f"mCurrentFocus=Window{{4d5e6f u0 Application Not Responding: {IG}}}")
    adb, _ = _adb_com_saida(vivo)
    assert adb.system_dialog() == f"Application Not Responding: {IG}"


def test_prontidao_le_o_foco_e_o_keyguard_de_agora() -> None:
    """Um ANR durante o boot (FallbackHome em foco, keyguard na tela) não pode deixar o aparelho "não pronto" para
    sempre — a cópia do ANR não muda até o próximo ANR."""
    boot = DUMPSYS_COM_ULTIMO_ANR.replace(
        f"mCurrentFocus=Window{{9f8e7d6 u0 {IG}/com.instagram.android.activity.MainTabActivity}}",
        "mCurrentFocus=Window{9f8e7d6 u0 com.android.settings/com.android.settings.FallbackHome}")
    adb, _ = _adb_com_saida(boot)
    assert adb.ui_ready() is True
    # E a seção viva ainda manda: launcher com keyguard AGORA não está pronto.
    trancado = boot.replace("    isKeyguardShowing=false", "    isKeyguardShowing=true")
    adb, _ = _adb_com_saida(trancado)
    assert adb.ui_ready() is False


class GuestComDialogo:
    """O diálogo está vivo na leitura; na hora do toque a seção viva já é o launcher, e a cópia do ANR ainda o mostra.

    Encena o pipe do aparelho: `grep -F mCurrentFocus` pega todas as linhas; com `tail -n 1`, só a última (a viva)."""

    DIALOGO = f"Application Not Responding: {IG}"

    def __init__(self) -> None:
        self.comandos: list[str] = []
        self.vivo = f"Window{{4d5e6f u0 {self.DIALOGO}}}"

    def dump(self) -> str:
        return (f"WINDOW MANAGER LAST ANR (dumpsys window lastanr)\n  mCurrentFocus=Window{{4d5e6f u0 {self.DIALOGO}}}\n"
                f"WINDOW MANAGER POLICY STATE (dumpsys window policy)\n"
                f"WINDOW MANAGER DISPLAY CONTENTS (dumpsys window displays)\n  mCurrentFocus={self.vivo}\n")

    def __call__(self, args: list[str], **_k: Any) -> subprocess.CompletedProcess[str]:
        cmd = args[1] if args[:1] == ["shell"] and len(args) > 1 else " ".join(args)
        self.comandos.append(cmd)
        if "input tap" in cmd:
            linhas = [ln for ln in self.dump().splitlines() if "mCurrentFocus" in ln]
            if "tail -n 1" in cmd:
                linhas = linhas[-1:]
            alvo = cmd.split("grep -qF ", 1)[1].split(" && ", 1)[0].strip("'")
            if any(alvo in ln for ln in linhas):
                return subprocess.CompletedProcess(args, 0, cmd.rsplit("echo ", 1)[1] + "\n", "")
            return subprocess.CompletedProcess(args, 1, "", "")
        if cmd.startswith("dumpsys window"):
            return subprocess.CompletedProcess(args, 0, self.dump(), "")
        if cmd.startswith("uiautomator dump"):
            self.vivo = f"Window{{1a2b3c4 u0 {LAUNCHER}/{LAUNCHER}.NexusLauncherActivity}}"   # sumiu antes do toque
            return subprocess.CompletedProcess(
                args, 0, '<hierarchy><node text="Wait" bounds="[300,1000][420,1080]"/></hierarchy>', "")
        return subprocess.CompletedProcess(args, 0, "", "")


def test_confirmacao_do_toque_no_dialogo_le_so_a_secao_viva() -> None:
    """K-031 + C9: a confirmação na MESMA chamada do toque não pode se contentar com a cópia do último ANR."""
    guest = GuestComDialogo()
    adb = Adb(SimpleNamespace(), "emulator-5640")      # type: ignore[arg-type]
    adb._run = guest                                      # type: ignore[method-assign]
    assert adb.dismiss_system_dialog() is None
    toques = [c for c in guest.comandos if "input tap" in c]
    assert len(toques) == 1 and "tail -n 1" in toques[0], toques


# ---------------------------------------------------------------- `dumpsys activity exit-info`
EXIT_INFO = f"""2026-09-28 19:59:00
ACTIVITY MANAGER PROCESS EXIT INFO (dumpsys activity exit-info)
Last Timestamp of Persistence Into Persistent Storage: 2026-09-28 19:40:00.000
  package: {IG}
    Historical Process Exit for uid=10155
        ApplicationExitInfo #0:
          timestamp=2026-09-28 19:58:12.345 pid=12345 realUid=10155 packageUid=10155 definingUid=10155 user=0
          process={IG} reason=6 (ANR) subreason=0 (UNKNOWN) status=0
          importance=100 pss=180MB rss=260MB description=user request after error: Input dispatching timed out state=empty trace=null
        ApplicationExitInfo #1:
          timestamp=2026-09-28 19:57:40.001 pid=12001 realUid=10155 packageUid=10155 definingUid=10155 user=0
          process={IG}:mqtt reason=6 (ANR) subreason=0 (UNKNOWN) status=0
          importance=300 pss=20MB rss=40MB description=user request after error state=empty trace=null
        ApplicationExitInfo #2:
          timestamp=2026-09-28 19:56:30.500 pid=11800 realUid=10155 packageUid=10155 definingUid=10155 user=0
          process={IG} reason=4 (APP CRASH(EXCEPTION)) subreason=0 (UNKNOWN) status=0
          importance=100 pss=170MB rss=250MB description=crash state=empty trace=/data/anr/anr_2026-09-28-19-56-30-400
        ApplicationExitInfo #3:
          timestamp=2026-09-28 19:55:00.000 pid=11000 realUid=10155 packageUid=10155 definingUid=10155 user=0
          process={IG} reason=4 (APP CRASH(EXCEPTION)) subreason=0 (UNKNOWN) status=0
          importance=100 pss=170MB rss=250MB description=crash state=empty trace=null
        ApplicationExitInfo #4:
          timestamp=2026-09-28 18:00:00.000 pid=9000 realUid=10155 packageUid=10155 definingUid=10155 user=0
          process={IG} reason=10 (USER REQUESTED) subreason=0 (UNKNOWN) status=0
          importance=100 pss=0 rss=0 description=stop {IG} due to from pid 1234 state=empty trace=null
        ApplicationExitInfo #5:
          timestamp=2026-09-28 17:00:00.000 pid=8000 realUid=10155 packageUid=10155 definingUid=10155 user=0
          process={IG} reason=6 (ANR) subreason=0 (UNKNOWN) status=0
          importance=100 pss=0 rss=0 description=user request after error state=empty trace=null
"""


def test_exit_info_devolve_as_mortes_com_a_idade_no_relogio_do_convidado() -> None:
    """reason=6 é ANR; reason=4 com trace `anr_*` também. Idade contra a hora do CONVIDADO (a 1ª linha): depois de
    acordar de um snapshot ele fica 26–31 s atrás do host, e comparar com a hora daqui erraria a janela."""
    adb, comandos = _adb_com_saida(EXIT_INFO)
    mortes = adb.app_deaths(IG)
    assert f"dumpsys activity exit-info {IG}" in comandos[0] and "date" in comandos[0]
    # só o processo PRINCIPAL (o `:mqtt` não é a tela) e só morte (pedido do usuário não é o app travando)
    assert [m.pid for m in mortes] == [12345, 11800, 11000, 8000]
    assert [m.motivo for m in mortes] == [6, 4, 4, 6]
    assert [m.anr for m in mortes] == [True, True, False, True]
    assert mortes[0].quando == "2026-09-28 19:58:12.345"
    assert mortes[0].idade_s == pytest.approx(47.655, abs=0.01)
    assert "user request after error" in mortes[0].descricao
    # "depois de T": só o que morreu nos últimos N segundos do relógio do convidado
    assert [m.pid for m in adb.app_deaths(IG, within_s=180)] == [12345, 11800]


def test_exit_info_sem_a_hora_do_convidado_nao_e_nenhuma_morte() -> None:
    """Sem a hora do convidado não se sabe a idade de nada: é "não sei" (erro), nunca lista vazia."""
    adb, _ = _adb_com_saida(EXIT_INFO.split("\n", 1)[1])
    with pytest.raises(AdbError):
        adb.app_deaths(IG, within_s=60)
    with pytest.raises(AdbError):
        adb.app_deaths("com.x; reboot")                 # o pacote entra no shell: só nome válido


# ---------------------------------------------------------------- `open_app` espera o foco
async def _abrir(fake: FakeQaDevice, *, deadline: float | None = None) -> dict[str, Any]:
    async def call(fn: Any, *a: Any) -> Any:
        return fn(*a)

    ctx = ToolContext(io=fake, call=call, tree=parse_hierarchy(fake.page_source()), width=720, height=1280,
                      image_scale=1.0, app_package="com.pocqa.messenger", app_activity=None,
                      allowed_packages={"com.pocqa.messenger"}, deadline=deadline)
    out = await execute_tool(ctx, "open_app", validate_call("open_app", {"rationale": "abrir", "package": None}))
    return out.result


async def test_open_app_com_o_launcher_na_frente_devolve_focused_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "ESPERA_DO_FOCO_S", 0.3)
    monkeypatch.setattr(tools, "INTERVALO_DO_FOCO_S", 0.02)
    morre = FakeQaDevice(account="qa", anr_ao_abrir=1)
    assert await _abrir(morre) == {"opened": "com.pocqa.messenger", "focused": False}
    assert morre.screen == "launcher"
    abre = FakeQaDevice(account="qa")
    assert await _abrir(abre) == {"opened": "com.pocqa.messenger", "focused": True}


async def test_espera_do_foco_nao_passa_do_prazo_da_etapa(monkeypatch: pytest.MonkeyPatch) -> None:
    """Achado #96: nada que a etapa espera no aparelho sobrevive ao prazo dela — nem os 60 s do foco."""
    monkeypatch.setattr(tools, "INTERVALO_DO_FOCO_S", 0.02)
    inicio = time.monotonic()
    out = await _abrir(FakeQaDevice(account="qa", anr_ao_abrir=1), deadline=inicio + 0.2)
    assert out["focused"] is False and time.monotonic() - inicio < 5


# ---------------------------------------------------------------- executor: uma reabertura, e só uma
@pytest.fixture(autouse=True)
def abertura_pela_ia(monkeypatch: pytest.MonkeyPatch) -> None:
    """Os cenários deste arquivo têm como gancho a decisão da IA que abre o app ("uma decisão da IA, a que abriu"). O
    LT-6 (29.45) abre o app da etapa `app_foreground` sem IA; a regra do ANR com essa abertura está provada em
    `test_caminho_rapido_2.py`."""
    monkeypatch.setattr(executor_mod, "OPEN_APP_SEM_IA", False)


@pytest.fixture
def espera_curta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "ESPERA_DO_FOCO_S", 0.3)
    monkeypatch.setattr(tools, "INTERVALO_DO_FOCO_S", 0.02)


async def test_morte_por_anr_reabre_uma_vez_sem_chamar_a_ia(harness: Harness, espera_curta: None) -> None:
    fake = harness.fakes["android-01"]
    fake.anr_ao_abrir = 1
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert detail.status == "completed", [(s.key, s.status, s.status_detail) for s in detail.steps]
    assert fake.calls.count("open_app") == 2                  # a da IA, e a reabertura do executor
    # a IA decidiu abrir; depois da reabertura o app já está à frente e a pós-condição vale na tela lida, então o
    # "pronto" também não passa por ela (caminho rápido 1, LT-1): a reabertura nunca passou
    assert harness.ai.count("decide", step="open_app", instance="android-01") == 1
    linhas = [r["message"] for r in harness.state.db.query(          # type: ignore[union-attr]
        "SELECT message FROM events WHERE kind='decision' AND run_id=? ORDER BY id", (run.id,))]
    assert any("parou de responder (ANR)" in m and "reaberto uma vez, sem IA" in m for m in linhas), linhas


async def test_segunda_morte_por_anr_para_a_etapa_com_o_motivo(harness: Harness, espera_curta: None) -> None:
    """Quatro partidas a frio que morrem: a etapa para na 2ª morte (uma reabertura só) e a recuperação automática do
    plano, a única que existe, para na 4ª. Sem isto a IA reabria até a 5ª e seguia como se nada fosse."""
    fake = harness.fakes["android-01"]
    fake.anr_ao_abrir = 4
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    motivo = "o QA Messenger parou de responder (ANR) e foi fechado no android-01: convidado sem CPU"
    abrir = [s for s in detail.steps if s.key == "open_app"]
    assert abrir and all(s.status == "failed" for s in abrir), [(s.key, s.status) for s in detail.steps]
    erros = [a.error for a in detail.attempts if a.step_id in {s.id for s in abrir}]
    assert erros and all(e == motivo for e in erros), erros
    assert detail.objectives[0].status == "failed"
    assert fake.calls.count("open_app") == 4                  # nenhuma 5ª partida a frio
    # por etapa, UMA decisão da IA (a que abriu o app); a reabertura e a parada não custam chamada
    assert harness.ai.count("decide", step="open_app", instance="android-01") == len(abrir)
    rt = harness.state.devices.devices["android-01"]          # type: ignore[union-attr]
    assert rt.attention and "parou de responder (ANR)" in rt.attention and "convidado sem CPU" in rt.attention


def test_janela_das_mortes_comeca_no_inicio_da_etapa() -> None:
    """`steps.started_at` (1ª tentativa) vira o início da janela no relógio monotônico; sem ele, o da tentativa; e
    nunca depois do início da tentativa (hora no futuro é relógio adiantado de outro processo)."""
    from app.taskqueue.executor import _inicio_monotonico
    from app.util import now, to_iso

    agora = time.monotonic()
    assert _inicio_monotonico(to_iso(now() - timedelta(seconds=400)), agora) == pytest.approx(agora - 400, abs=1)
    assert _inicio_monotonico(None, agora) == agora
    assert _inicio_monotonico(to_iso(now() + timedelta(seconds=30)), agora) == agora


class SimuladoComPrazoCurto(SimulatedProvider):
    """O plano do simulado com a etapa "Abrir" curta e com tentativas: é o que o banco mostra na
    r-20260928195344-02ee9e, em escala — tentativas que acabam pelo prazo e são repetidas (max_attempts=3)."""

    def __init__(self, prazo_s: int) -> None:
        super().__init__()
        self.prazo_s = prazo_s

    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        plano, uso = await super().plan(req)
        passos = [s.model_copy(update={"timeout_s": self.prazo_s, "max_attempts": 3}) if s.key == "open_app" else s
                  for s in plano.steps]
        return plano.model_copy(update={"steps": passos}), uso


@pytest.mark.parametrize("prazo_s", [2, 3])
async def test_mortes_por_anr_contam_pela_etapa_e_nao_pela_tentativa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                      prazo_s: int) -> None:
    """Revisão do pacote: na r-20260928195344-02ee9e cada tentativa acabava por "Tempo da etapa esgotado (180s)" e a
    seguinte recomeçava a contagem do zero — a morte da tentativa N não era vista na N+1, "2ª morte → falha" nunca
    disparava, cada tentativa gastava a sua reabertura e o motivo final era opaco.

    Em escala: cada chamada ao aparelho leva 0,8 s (a abertura morre por ANR ao fim dela), a partida a frio "leva" 0,6
    s e a etapa tem 2 ou 3 s. Com 2 s nunca sobra, depois da observação, tempo para reabrir: toda tentativa depois da
    1ª sai pelo ANR sem reabrir. Com 3 s a 1ª morte cai na tentativa 1, a reabertura na 2 e a 2ª morte é contada na 3
    — atravessando tentativas. Em qualquer ordem que o relógio der, por VERSÃO da etapa: no máximo uma reabertura
    determinística, uma decisão da IA (a que abriu), e a última tentativa sai com o motivo do ANR — e o aviso vai para
    o aparelho. Antes da correção, nos dois prazos: 6 partidas a frio e 6 decisões da IA, nenhuma morte contada, toda
    tentativa saindo por "Tempo da etapa esgotado" e nenhum aviso (com 5 s e 1,3 s por chamada: 3 reaberturas e 3
    decisões por versão)."""
    monkeypatch.setattr(tools, "ESPERA_DO_FOCO_S", 0.6)
    monkeypatch.setattr(tools, "INTERVALO_DO_FOCO_S", 0.05)
    h = Harness(tmp_path, 1)
    h.ai = CountingProvider(SimuladoComPrazoCurto(prazo_s))
    await h.boot()
    await h.medir_a_internet()   # T.2: a sonda do monitor agora; o Instagram não espera os 6 s da 1ª volta
    assert h.state is not None
    try:
        fake = h.fakes["android-01"]
        fake.anr_ao_abrir = 30
        fake.action_delay_s = 0.8
        run = h.run(["android-01"])
        detail = await h.wait_run(run.id, timeout=150)
        abrir = [s for s in detail.steps if s.key == "open_app"]
        assert abrir and all(s.status == "failed" for s in abrir), [(s.key, s.status) for s in detail.steps]
        reaberturas = {s.id: h.state.db.scalar(
            "SELECT COUNT(*) FROM events WHERE kind='decision' AND step_id=? AND message LIKE ?",
            (s.id, "%reaberto uma vez, sem IA%")) for s in abrir}
        assert all(n <= 1 for n in reaberturas.values()), reaberturas
        for s in abrir:
            tentativas = sorted((a for a in detail.attempts if a.step_id == s.id), key=lambda a: a.number)
            assert tentativas and "parou de responder (ANR)" in (tentativas[-1].error or ""), \
                [(a.number, a.error) for a in tentativas]
        assert h.ai.count("decide", step="open_app", instance="android-01") == len(abrir)
        assert fake.calls.count("open_app") <= 2 * len(abrir)
        rt = h.state.devices.devices["android-01"]
        assert rt.attention and "parou de responder (ANR)" in rt.attention
    finally:
        await h.state.stop()


# ---------------------------------------------------------------- motor de sessão
class RtComAtencao(FakeRt):
    def __init__(self, app: FakeInstagram) -> None:
        super().__init__(app)
        self.attention: str | None = None


class DevicesComAviso(FakeDevices):
    def __init__(self, app: FakeInstagram) -> None:
        super().__init__(app)
        self.avisos: list[str] = []

    def marcar_atencao(self, rt: Any, texto: str) -> None:
        rt.attention = texto
        self.avisos.append(texto)


async def test_sessao_com_duas_mortes_por_anr_nao_e_tela_desconhecida(tmp_path: Path) -> None:
    """Launcher com morte recente é o app morrendo, e o motivo diz isso; nada é digitado e nada conta no teto de
    tentativas de login (a credencial nem chegou a ser usada)."""
    app = FakeInstagram(stored_password=SENHA, anr_ao_abrir=2)
    auth, repo, social, db = build(tmp_path, app)
    devices = DevicesComAviso(app)
    auth.devices = devices                                    # type: ignore[assignment]
    try:
        pid = cadastrar(social)
        rt = RtComAtencao(app)
        r = await auth.ensure_session(rt, pid)                # type: ignore[arg-type]
        motivo = "o Instagram parou de responder (ANR) e foi fechado no android-02: convidado sem CPU"
        assert r.outcome is SessaoOutcome.RETRYABLE and r.detail == motivo, r.detail
        assert app.typed == [] and app.anr_ao_abrir == 0      # duas partidas, nenhuma terceira
        assert (repo.credential_row(pid)["failed_attempts"] or 0) == 0
        assert rt.attention and "parou de responder (ANR)" in rt.attention
    finally:
        db.close()


async def test_sessao_com_uma_morte_por_anr_reabre_e_entra(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA, anr_ao_abrir=1)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is SessaoOutcome.SESSION_READY, r.detail
    finally:
        db.close()


# ---------------------------------------------------------------- prazo da etapa dentro da chamada de IA
async def test_prazo_vencido_dentro_da_chamada_e_step_deadline() -> None:
    from app.taskqueue.executor import _com_prazo

    async def pendurado() -> str:
        await asyncio.sleep(5)
        return "tarde demais"

    with pytest.raises(AIError) as e:
        await _com_prazo(pendurado(), time.monotonic() + 0.05, "decide")
    assert e.value.kind == "step_deadline" and "prazo restante da etapa" in str(e.value)


async def test_prazo_esgotado_antes_da_chamada_e_step_deadline(harness: Harness) -> None:
    run = harness.run(["android-01"], mode="plan")
    executor = harness.state.scheduler.executor               # type: ignore[union-attr]
    chamadas: list[str] = []

    async def nunca() -> Any:
        chamadas.append("chamou")
        raise AssertionError("não devia chamar o provedor")

    with pytest.raises(AIError) as e:
        await executor._ai(run.id, None, nunca, role="decide", deadline=time.monotonic() - 1)
    assert e.value.kind == "step_deadline" and not chamadas


class DecisaoPendurada:
    """Plano do simulado com prazo curto por etapa; a decisão fica pendurada além do prazo da etapa."""

    def __init__(self, inner: SimulatedProvider) -> None:
        self.inner = inner
        self.name, self.model, self.simulated = inner.name, inner.model, inner.simulated

    def status(self) -> Any:
        return self.inner.status()

    async def plan(self, req: Any) -> Any:
        plano, uso = await self.inner.plan(req)
        passos = [s.model_copy(update={"timeout_s": 2, "max_attempts": 1}) for s in plano.steps]
        return plano.model_copy(update={"steps": passos}), uso

    async def decide(self, req: Any) -> Any:
        await asyncio.sleep(30)
        return await self.inner.decide(req)

    async def verify(self, req: Any) -> Any:
        return await self.inner.verify(req)

    async def generate_social_response(self, req: Any) -> Any:
        raise NotImplementedError


async def test_prazo_da_etapa_na_chamada_de_ia_nao_e_ia_indisponivel(tmp_path: Path) -> None:
    """C11(b): o motivo gravado é o prazo da etapa; "IA indisponível" mandava procurar defeito no provedor."""
    h = Harness(tmp_path, 1)
    h.ai = DecisaoPendurada(SimulatedProvider())             # type: ignore[assignment]
    await h.boot()
    await h.medir_a_internet()   # T.2: a sonda do monitor agora; o Instagram não espera os 6 s da 1ª volta
    assert h.state is not None
    try:
        run = h.run(["android-01"])
        detail = await h.wait_run(run.id, timeout=40)
        erros = [a.error or "" for a in detail.attempts]
        assert erros, [(s.key, s.status, s.status_detail) for s in detail.steps]
        assert "prazo da etapa" in erros[0].lower(), erros
        assert not any("IA indisponível" in e for e in erros), erros
    finally:
        await h.state.stop()

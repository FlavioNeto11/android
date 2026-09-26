"""Prontidão sem efeito tardio não idempotente (K-031) e os achados da revisão pós-merge do PR #7.

Um `AdbTimeout` encerra só o cliente adb local: a transação binder já entregue a um `system_server` congelado executa
DEPOIS, mesmo com o cliente morto. Quase todo o preparo é idempotente (`settings put` com constante, `svc power
stayon`, `wm dismiss-keyguard`) e o efeito tardio dele é inofensivo. Dois não eram: o `input tap` do
`dismiss_system_dialog` (cai na tela que estiver aberta) e o `cmd alarm set-time <absoluto>` do `sync_clock` (aplicado
atrasado, ATRASA o relógio pelo tempo em que ficou preso). Os dois saíram do caminho de prontidão:

- o preparo não toca mais na tela; o toque no diálogo confirma o MESMO diálogo na MESMA chamada do `adb shell`, e só
  roda fora do portão (entrada no ar, antes da sessão de automação; instalador);
- o relógio virou condição própria do central: medir (só leitura) → corrigir (`cmd alarm set-time`) → conferir, na
  entrada no ar e em reconferência periódica, que desfaz um set-time que tenha caído atrasado.

Nenhum teste aqui fala com aparelho real: dublês de `adb` (`_run` gravado) e o harness (`base_console_port` 5640).
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from app.devices import adb as adb_mod
from app.devices import manager as manager_mod
from app.devices import prontidao
from app.devices.adb import Adb, AdbTimeout
from app.models import InstanceState
from app.worker.executor import VerbUncertain

from .conftest import Harness
from .test_prontidao_subsistemas import _externo, _readocao_local, _worker
from .test_worker_executor import AdbFalso

ANR = "Application Not Responding: com.android.systemui"
FOCO_ANR = f"  mCurrentFocus=Window{{4f1c2a u0 {ANR}}}\n"
FOCO_APP = "  mCurrentFocus=Window{9b2e11 u0 com.instagram.android/com.instagram.mainactivity.MainActivity}\n"
XML_ANR = ('<hierarchy><node index="0" text="Close app" class="android.widget.Button" bounds="[40,900][680,980]" />'
           '<node index="1" text="Wait" class="android.widget.Button" bounds="[40,1000][680,1080]" /></hierarchy>')


class GuestFalso:
    """O `adb` visto do host: cada `adb shell` vira uma linha gravada, e a resposta imita o convidado — inclusive o
    `grep -qF` da confirmação, avaliado sobre o foco que o convidado tem NO MOMENTO em que o shell roda."""

    def __init__(self, foco: str = FOCO_ANR, *, foco_na_hora_do_toque: str | None = None) -> None:
        self.foco = foco
        self.foco_na_hora_do_toque = foco_na_hora_do_toque
        self.comandos: list[str] = []

    def __call__(self, args: list[str], *, timeout: float = 30, binary: bool = False,
                 entrada: str | None = None) -> subprocess.CompletedProcess:
        cmd = args[1] if args[:1] == ["shell"] and len(args) > 1 else " ".join(args)
        self.comandos.append(cmd)
        if "input tap" in cmd and "grep" in cmd:            # confirmação e toque no MESMO shell
            if self.foco_na_hora_do_toque is not None:
                self.foco = self.foco_na_hora_do_toque
            alvo = cmd.split("grep -qF ", 1)[1].split(" && ", 1)[0].strip("'")
            if alvo in self.foco:
                return subprocess.CompletedProcess(args, 0, cmd.rsplit("echo ", 1)[1] + "\n", "")
            return subprocess.CompletedProcess(args, 1, "", "")
        if cmd.startswith("dumpsys window"):
            return subprocess.CompletedProcess(args, 0, self.foco, "")
        if cmd.startswith("uiautomator dump"):
            return subprocess.CompletedProcess(args, 0, XML_ANR, "")
        return subprocess.CompletedProcess(args, 0, "", "")


def _adb(guest: Callable[..., subprocess.CompletedProcess]) -> Adb:
    adb = Adb(SimpleNamespace(), "emulator-5640")     # type: ignore[arg-type]  - `_run` é o dublê: nada sai daqui
    adb._run = guest                                     # type: ignore[method-assign]
    return adb


# ---------------------------------------------------------------- efeito 1: o toque saiu do portão
def test_preparo_nao_toca_na_tela_nem_com_dialogo_aberto() -> None:
    """O preparo é o que o portão roda (worker e central). Com o toque dentro dele, um `input tap` estourado podia
    cair DEPOIS, na tela que uma tentativa seguinte já tivesse declarado pronta."""
    guest = GuestFalso()
    _adb(guest).prepare_for_automation()
    assert not any("input tap" in c for c in guest.comandos), guest.comandos
    assert not any("uiautomator" in c for c in guest.comandos), "o preparo não procura botão nenhum"
    assert any("hide_error_dialogs 1" in c for c in guest.comandos), "o preparo segue impedindo diálogos FUTUROS"


def test_toque_no_dialogo_confirma_o_mesmo_dialogo_na_mesma_chamada() -> None:
    guest = GuestFalso()
    assert _adb(guest).dismiss_system_dialog() == "Wait"
    toques = [c for c in guest.comandos if "input tap" in c]
    assert len(toques) == 1, guest.comandos
    assert "dumpsys window" in toques[0] and f"grep -qF ' {ANR}}}'" in toques[0]
    assert "&& input tap 360 1040" in toques[0]


def test_dialogo_que_sumiu_antes_do_toque_nao_e_tocado() -> None:
    """O diálogo estava lá na leitura e no dump, e saiu antes do toque: o `input tap` não pode acontecer."""
    guest = GuestFalso(foco_na_hora_do_toque=FOCO_APP)
    assert _adb(guest).dismiss_system_dialog() is None
    assert not any(c.startswith("input tap") for c in guest.comandos), "nada de toque solto, sem confirmação"


def test_dialogo_com_descricao_fora_do_padrao_nao_e_tocado() -> None:
    """A descrição vem do aparelho e entraria no shell: fora do padrão, nada de toque (nunca texto livre)."""
    guest = GuestFalso(foco="  mCurrentFocus=Window{4f1c2a u0 Aviso: '$(reboot)'}\n")
    assert _adb(guest).dismiss_system_dialog() is None
    assert not any("input tap" in c for c in guest.comandos)


# ---------------------------------------------------------------- efeito 2: o relógio saiu do portão
class RelogioFalso:
    """`date +%s` do convidado e o relógio do host, com o adb levando `latencia_s` para responder."""

    def __init__(self, desvio_s: int, *, latencia_s: float = 0.0, set_time_corrige: bool = True) -> None:
        self.host = 1_000_000.0
        self.desvio_s = desvio_s
        self.latencia_s = latencia_s
        self.set_time_corrige = set_time_corrige
        self.comandos: list[str] = []

    def time(self) -> float:
        return self.host

    def run(self, args: list[str], *, timeout: float = 30, binary: bool = False,
            entrada: str | None = None) -> subprocess.CompletedProcess:
        cmd = args[1] if args[:1] == ["shell"] and len(args) > 1 else " ".join(args)
        self.comandos.append(cmd)
        meio = self.host + self.latencia_s / 2        # o convidado executa no meio da ida e volta
        self.host += self.latencia_s
        if cmd == "date +%s":
            return subprocess.CompletedProcess(args, 0, f"{int(meio + self.desvio_s)}\n", "")
        if cmd.startswith("cmd alarm set-time") and self.set_time_corrige:
            self.desvio_s = 0
        return subprocess.CompletedProcess(args, 0, "", "")


def test_desvio_desconta_a_ida_e_volta_do_adb(monkeypatch: pytest.MonkeyPatch) -> None:
    """Relógio certo com adb lento (6 s de ida e volta) lia −3 s e disparava um `set-time` à toa."""
    r = RelogioFalso(0, latencia_s=6.0)
    monkeypatch.setattr(adb_mod, "time", SimpleNamespace(time=r.time))
    adb = Adb(SimpleNamespace(), "emulator-5640")        # type: ignore[arg-type]
    adb._run = r.run                                     # type: ignore[method-assign]
    assert adb.clock_skew_s() == 0


def test_acerto_do_relogio_nao_reinicia_o_adbd_nem_usa_hora_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fora do portão (aparelho já no ar), `adb root` reinicia o adbd e derruba túnel, Appium e captura; e o
    `date MMDDhhmm` usava a hora LOCAL do host no fuso do convidado. O acerto é só `cmd alarm set-time`, conferido."""
    r = RelogioFalso(-30, set_time_corrige=False)
    monkeypatch.setattr(adb_mod, "time", SimpleNamespace(time=r.time))
    adb = Adb(SimpleNamespace(), "emulator-5640")        # type: ignore[arg-type]
    adb._run = r.run                                     # type: ignore[method-assign]
    assert adb.sync_clock() == (-30, -30), "não convergiu: quem chama vê e diz"
    assert not any(c in ("root", "wait-for-device") or c.startswith("date ") and "%s" not in c for c in r.comandos), \
        r.comandos


@pytest.mark.parametrize("verbo", ["start", "wake"])
async def test_worker_start_e_wake_nao_mexem_no_relogio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                        verbo: str) -> None:
    """Antes: um `sync_clock` estourado depois da escada deixava o verbo `uncertain` (e o `set-time` atrasado caía
    no aparelho que o central readotasse). O relógio é do central, depois de o aparelho entrar no ar."""
    adb = AdbFalso(pronto_depois_de=1)
    chamou: list[bool] = []

    def relogio_travado() -> tuple[int, int]:
        chamou.append(True)
        raise AdbTimeout("adb shell excedeu 10s")
    adb.sync_clock = relogio_travado                     # type: ignore[method-assign]
    ex = _worker(tmp_path, monkeypatch, adb, hibernacao=verbo == "wake")
    saida = await ex.run(verbo, ex.settings.devices[0], {"boot_timeout_s": 5})
    assert saida["started"] is True and chamou == []


async def _wait_boot_saudavel(harness: Harness, monkeypatch: pytest.MonkeyPatch, *, warm: bool,
                              relogio: Any = None) -> tuple[Any, bool]:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.booting, "emulador iniciado")
    rt.pid = 4242
    monkeypatch.setattr(manager_mod.emu, "is_our_emulator", lambda *_a, **_k: True)
    monkeypatch.setattr(manager_mod, "RESPOSTA_POS_BOOT_S", 0.05)
    monkeypatch.setattr(manager_mod, "RESPOSTA_MIN_S", 0.05)
    monkeypatch.setattr(rt.adb, "boot_completed", lambda *a, **k: True)
    monkeypatch.setattr(rt.adb, "ui_ready", lambda *a, **k: True)
    monkeypatch.setattr(rt.adb, "prepare_for_automation", lambda *a, **k: None)
    if relogio is not None:
        monkeypatch.setattr(rt.adb, "sync_clock", relogio, raising=False)
    monkeypatch.setattr(s.devices, "_snapshot_verdict", lambda _rt: True)
    monkeypatch.setattr(s.devices, "_start_online_tasks", lambda _rt: None)
    s.cfg.file.limits.boot_poll_s = 0.01
    ok = await s.devices._wait_boot(rt, time.monotonic(), warm=warm)
    return rt, ok


async def test_achado_1_wake_local_com_relogio_travado_fica_online_e_nao_perde_o_snapshot(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Achado 1: com o relógio no portão, um `sync_clock` estourado (o fallback `adb root` → `wait-for-device` 20 s
    passa do prazo) devolvia False e `_boot` DESCARTAVA o snapshot de um aparelho bom para bootar a frio."""
    chamou: list[bool] = []

    def relogio_travado(*_a: Any, **_k: Any) -> tuple[int, int]:
        chamou.append(True)
        raise AdbTimeout("adb wait-for-device excedeu 20s")
    rt, ok = await _wait_boot_saudavel(harness, monkeypatch, warm=True, relogio=relogio_travado)
    assert ok is True and rt.state == InstanceState.online and chamou == []


def _relogio_no_central(harness: Harness, monkeypatch: pytest.MonkeyPatch, desvios: list[int],
                        acerto: Callable[..., tuple[int, int]] | None = None) -> tuple[Any, list[str]]:
    """Aparelho `online` com o `adb` real trocado por dublês — só assim o central sai do desvio do harness."""
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.online, "teste")
    monkeypatch.setattr(s.devices, "io_factory", None)
    trilhas: list[str] = []
    fila = list(desvios)

    def medir(*_a: Any, **_k: Any) -> int:
        trilhas.append("medir")
        return fila.pop(0) if len(fila) > 1 else fila[0]

    def acertar(*_a: Any, **_k: Any) -> tuple[int, int]:
        trilhas.append("acertar")
        antes = fila.pop(0) if len(fila) > 1 else fila[0]
        return antes, 0
    monkeypatch.setattr(rt.adb, "clock_skew_s", medir)
    monkeypatch.setattr(rt.adb, "sync_clock", acerto or acertar)
    return rt, trilhas


async def test_relogio_certo_so_e_medido(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    rt, trilhas = _relogio_no_central(harness, monkeypatch, [1])
    await harness.state.devices.conferir_relogio_do_convidado(rt)       # type: ignore[union-attr]
    assert trilhas == ["medir"] and rt.clock_state == "ok" and rt.clock_skew_s == 1


async def test_relogio_atrasado_e_corrigido_conferido_e_medido(harness: Harness,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt, trilhas = _relogio_no_central(harness, monkeypatch, [-30, -30])
    await s.devices.conferir_relogio_do_convidado(rt)
    assert trilhas == ["medir", "acertar"] and rt.clock_state == "ok" and rt.clock_skew_s == 0
    linha = s.db.query("SELECT data FROM measurements WHERE kind='clock'")[-1]
    assert json.loads(linha["data"])["clock_skew_before_after_s"] == [-30, 0]


async def test_acerto_que_estoura_fica_incerto_e_a_reconferencia_vem_cedo(harness: Harness,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Estouro no acerto é efeito incerto — e NÃO mexe na prontidão (o aparelho segue `online`/`ready`). A
    reconferência é antecipada: é ela que mede o que tiver caído atrasado."""
    s = harness.state
    assert s is not None

    def travado(*_a: Any, **_k: Any) -> tuple[int, int]:
        raise AdbTimeout("adb shell excedeu 10s")
    rt, _ = _relogio_no_central(harness, monkeypatch, [-30], acerto=travado)
    await s.devices.conferir_relogio_do_convidado(rt)
    assert rt.clock_state == "incerto" and rt.state == InstanceState.online and rt.readiness_phase == "ready"
    agora = time.monotonic()
    assert not s.devices._deve_conferir_relogio(rt, agora)
    assert s.devices._deve_conferir_relogio(rt, agora + manager_mod.INTERVALO_DO_RELOGIO_PENDENTE_S + 1)
    assert manager_mod.INTERVALO_DO_RELOGIO_PENDENTE_S < manager_mod.INTERVALO_DO_RELOGIO_S


async def test_set_time_atrasado_e_desfeito_na_reconferencia(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reconciliação: relógio certo; um `set-time` que tinha estourado cai DEPOIS e atrasa o convidado em 25 s; a
    reconferência periódica mede e corrige de novo."""
    s = harness.state
    assert s is not None
    rt, trilhas = _relogio_no_central(harness, monkeypatch, [0, -25, -25])
    await s.devices.conferir_relogio_do_convidado(rt)
    assert rt.clock_state == "ok" and trilhas == ["medir"]
    assert s.devices._deve_conferir_relogio(rt, time.monotonic() + manager_mod.INTERVALO_DO_RELOGIO_S + 1)
    await s.devices.conferir_relogio_do_convidado(rt)                   # o set-time atrasado já caiu: −25 s
    assert trilhas == ["medir", "medir", "acertar"] and rt.clock_state == "ok" and rt.clock_skew_s == 0


async def test_relogio_que_nao_converge_vira_aviso_proprio_sem_derrubar_a_prontidao(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt, _ = _relogio_no_central(harness, monkeypatch, [-30], acerto=lambda *a, **k: (-30, -30))
    await s.devices.conferir_relogio_do_convidado(rt)
    assert rt.clock_state == "fora" and rt.attention and rt.attention.startswith(manager_mod.RELOGIO_PREFIXO)
    assert rt.state == InstanceState.online and rt.readiness_phase == "ready"
    monkeypatch.setattr(rt.adb, "clock_skew_s", lambda *a, **k: 0)
    await s.devices.conferir_relogio_do_convidado(rt)
    assert rt.clock_state == "ok" and rt.attention is None, "o aviso do relógio some quando ele volta ao certo"


async def test_relogio_nao_apaga_aviso_de_outro_assunto(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt, _ = _relogio_no_central(harness, monkeypatch, [0])
    rt.attention = "Convidado sob pressão: load 9"
    await s.devices.conferir_relogio_do_convidado(rt)
    assert rt.attention == "Convidado sob pressão: load 9"


async def test_relogio_no_harness_nao_chama_adb(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.online, "teste")

    def proibido(*_a: Any, **_k: Any) -> int:
        raise AssertionError("o harness nunca fala com adb real")
    monkeypatch.setattr(rt.adb, "clock_skew_s", proibido)
    await s.devices.conferir_relogio_do_convidado(rt)
    assert not s.devices._deve_conferir_relogio(rt, time.monotonic() + 10 * manager_mod.INTERVALO_DO_RELOGIO_S)


async def test_arrumacao_de_entrada_vem_antes_da_sessao_de_automacao(harness: Harness,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """O diálogo é dispensado (e o relógio conferido) DEPOIS da prontidão e ANTES da sessão: um `uiautomator dump`
    com a sessão do UiAutomator2 aberta derruba a sessão."""
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.online, "teste")
    monkeypatch.setattr(s.devices, "io_factory", None)
    ordem: list[str] = []
    monkeypatch.setattr(rt.adb, "system_dialog", lambda *a, **k: (ordem.append("ler diálogo"), ANR)[1])
    monkeypatch.setattr(rt.adb, "dismiss_system_dialog", lambda *a, **k: (ordem.append("dispensar"), "Wait")[1])

    async def relogio(_rt: Any) -> None:
        ordem.append("relógio")

    async def sessao(_rt: Any) -> bool:
        ordem.append("sessão")
        return True
    monkeypatch.setattr(s.devices, "conferir_relogio_do_convidado", relogio)
    monkeypatch.setattr(s.devices, "ensure_automation", sessao)
    assert await s.devices._automacao_depois_de_arrumar(rt) is True
    assert ordem == ["ler diálogo", "dispensar", "relógio", "sessão"]


async def test_arrumacao_com_toque_travado_nao_mexe_na_prontidao(harness: Harness,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.online, "teste")
    monkeypatch.setattr(s.devices, "io_factory", None)
    monkeypatch.setattr(rt.adb, "system_dialog", lambda *a, **k: ANR)

    def travado(*_a: Any, **_k: Any) -> str:
        raise AdbTimeout("adb shell excedeu 15s")
    monkeypatch.setattr(rt.adb, "dismiss_system_dialog", travado)
    monkeypatch.setattr(rt.adb, "clock_skew_s", lambda *a, **k: 0)
    await s.devices._arrumar_depois_de_entrar(rt)
    assert rt.state == InstanceState.online and rt.readiness_phase == "ready"


# ---------------------------------------------------------------- achado 2: `ready` guarda o detalhe da escada
async def test_achado_2_readiness_detail_de_ready_e_o_da_escada(harness: Harness,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    await s.devices._adopt_external(rt)
    assert rt.state == InstanceState.online and rt.readiness_phase == "ready"
    assert rt.readiness_detail == prontidao.Prontidao("ok").detalhe()


async def test_achado_2_boot_local_tambem(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    rt, ok = await _wait_boot_saudavel(harness, monkeypatch, warm=False)
    assert ok and rt.readiness_phase == "ready" and rt.readiness_detail == prontidao.Prontidao("ok").detalhe()


# ---------------------------------------------------------------- achado 3: display lento, mas vivo
class DisplayLento:
    """Display que produz o quadro, mas só com prazo ≥ 15 s (convidado sob pressão)."""

    def framework_alive(self, *, timeout: float = 25) -> bool:
        return True

    def system_server_alive(self, *, timeout: float = 8) -> bool:
        return True

    def display_alive(self, *, timeout: float = 12) -> bool:
        if timeout < 15:
            raise AdbTimeout(f"adb shell excedeu {timeout}s")
        return True


def test_achado_3_display_lento_mas_vivo_passa_na_escada() -> None:
    assert prontidao.avaliar(DisplayLento()).pronto
    assert prontidao.PRAZO_S["display"] <= 20, "nunca mais estrito do que o `screencap_png` (20 s) do stream"


def test_achado_3_piso_do_orcamento_cobre_uma_rodada_inteira() -> None:
    assert manager_mod.RESPOSTA_MIN_S >= prontidao.prazo_da_rodada()


# ---------------------------------------------------------------- achados 4 e 7: readoção depois de tentativa incerta
def _preparo_contado(erro: Exception | None = None) -> tuple[Callable[..., None], list[float]]:
    chamadas: list[float] = []

    def preparo(*_a: Any, **_k: Any) -> None:
        chamadas.append(time.monotonic())
        if erro is not None and len(chamadas) == 1:
            raise erro
    return preparo, chamadas


async def test_achado_4_readocao_incerta_nao_recomeca_no_mesmo_pid_na_hora(harness: Harness,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Observado no android-04: preparo estourado às 17:31:02 e `online` às 17:31:15 — a nova tentativa começava
    ~1 s depois, na janela em que o efeito atrasado cai e em que a forense viu 3 s de recuperação parcial."""
    s = harness.state
    assert s is not None
    rt = _readocao_local(harness, monkeypatch)
    monkeypatch.setattr(rt.adb, "ui_ready", lambda *a, **k: True)
    monkeypatch.setattr(manager_mod, "ESPERA_APOS_TENTATIVA_INCERTA_S", 0.6)
    s.cfg.file.limits.boot_poll_s = 0.01
    preparo, chamadas = _preparo_contado(AdbTimeout("adb shell excedeu 40s"))
    monkeypatch.setattr(rt.adb, "prepare_for_automation", preparo)
    try:
        await s.devices._adopt(rt)
        await asyncio.sleep(0.3)
        assert len(chamadas) == 1, "nenhuma tentativa nova no mesmo guest dentro do espaçamento"
        assert rt.state == InstanceState.booting and "próxima tentativa" in rt.readiness_detail
        await asyncio.sleep(0.8)
        assert len(chamadas) == 2, "passado o espaçamento, a próxima tentativa acontece"
    finally:
        boot = rt.tasks.pop("boot", None)
        if boot:
            boot.cancel()


async def test_achado_7_adb_device_sem_processo_nosso_nao_vira_stopped(harness: Harness,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """adb `device` no NOSSO serial = há emulador na nossa porta de console. Marcar `stopped` e zerar o PID deixava
    a próxima subida colidir na porta."""
    s = harness.state
    assert s is not None
    rt = _readocao_local(harness, monkeypatch)
    monkeypatch.setattr(manager_mod.emu, "is_our_emulator", lambda *_a, **_k: False)
    monkeypatch.setattr(manager_mod, "ESPERA_APOS_TENTATIVA_INCERTA_S", 30.0)
    rt.pid = 999_999                                         # PID velho, de outro processo
    preparo, _ = _preparo_contado(AdbTimeout("adb shell excedeu 40s"))
    monkeypatch.setattr(rt.adb, "prepare_for_automation", preparo)
    try:
        await s.devices._adopt(rt)
        assert rt.state == InstanceState.booting, "há um emulador no ar nesta porta"
        assert rt.pid is None, "o PID velho não é deste emulador"
        assert "boot" in rt.tasks
    finally:
        boot = rt.tasks.pop("boot", None)
        if boot:
            boot.cancel()


# ---------------------------------------------------------------- achado 5: erro de programação não é "mudo"
class SemSystemServer:
    """Um `DeviceIO` antigo, sem os métodos novos da escada."""

    def framework_alive(self, *, timeout: float = 25) -> bool:
        return True


def test_achado_5_erro_de_codigo_na_sonda_e_erro_logado_nao_mudo(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="poc.devices.prontidao"):
        p = prontidao.avaliar(SemSystemServer(), rotulo="android-01")
    assert p.estado == "erro" and p.falta == "system_server" and not p.pronto
    assert "falha de código" in p.detalhe() and "AttributeError" in p.detalhe()
    assert any(r.exc_info for r in caplog.records), "com a pilha no log"


async def test_achado_5_central_nao_repete_rodada_de_erro_de_codigo(harness: Harness,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    monkeypatch.setattr(rt, "io", SemSystemServer())
    t0 = time.monotonic()
    p = await s.devices._esperar_prontidao(rt, 8.0)
    assert p.estado == "erro" and time.monotonic() - t0 < 3.0, "erro de código não vira espera até o prazo"


async def test_achado_5_worker_devolve_uncertain_na_hora(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adb = AdbFalso(pronto_depois_de=1)

    def sem_metodo(*_a: Any, **_k: Any) -> bool:
        raise AttributeError("'AdbFalso' object has no attribute 'system_server_alive'")
    adb.system_server_alive = sem_metodo                 # type: ignore[method-assign]
    ex = _worker(tmp_path, monkeypatch, adb)
    t0 = time.monotonic()
    with pytest.raises(VerbUncertain) as saida:
        await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5})
    assert time.monotonic() - t0 < 2.0 and "falha de código" in str(saida.value)


# ---------------------------------------------------------------- achado 6: o preparo não come a escada do worker
async def test_achado_6_preparo_lento_devolve_o_tempo_a_escada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O preparo respondeu (lento) depois do prazo do verbo: a escada rodava com sondas de 1 s e o display, vivo mas
    precisando de mais que isso, virava `uncertain`."""
    adb = AdbFalso(pronto_depois_de=1)
    adb.prepare_for_automation = lambda: time.sleep(1.4)  # type: ignore[method-assign]

    def display(*, timeout: float = 12) -> bool:
        adb.prazos.append(("display", timeout))
        if timeout < 1.05:                           # antes: a escada recebia exatamente 1,0 s
            raise AdbTimeout(f"screencap excedeu {timeout}s")
        return True
    adb.display_alive = display                          # type: ignore[method-assign]
    ex = _worker(tmp_path, monkeypatch, adb)
    assert (await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 0.3}))["started"] is True


# ---------------------------------------------------------------- achado 8: quando cada degrau respondeu
class SystemServerMudo(SemSystemServer):
    def system_server_alive(self, *, timeout: float = 8) -> bool:
        raise AdbTimeout(f"settings get excedeu {timeout}s")


def test_achado_8_uma_linha_info_por_rodada_com_o_tempo_de_cada_degrau(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="poc.devices.prontidao"):
        prontidao.avaliar(DisplayLento(), rotulo="android-07")
        prontidao.avaliar(SystemServerMudo(), rotulo="android-08")
    linhas = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert len(linhas) == 2, linhas
    assert "android-07" in linhas[0]
    assert all(f"{d} " in linhas[0] for d in ("servicemanager", "system_server", "display"))
    assert "pronto" in linhas[0] and " s" in linhas[0]
    assert "android-08" in linhas[1] and "servicemanager" in linhas[1] and "system_server" in linhas[1]
    assert "mudo" in linhas[1]


def test_prazo_da_rodada_nao_soma_minutos() -> None:
    assert prontidao.prazo_da_rodada() < 60


async def test_aparelho_que_sai_do_ar_durante_a_medida_nao_tem_a_hora_acertada(harness: Harness,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisão do PR #12: a tarefa do relógio passava pelo `online` no começo, media, e acertava a hora mesmo com o
    aparelho já parando/hibernando — o `set-time` caía no stop/snapshot, e o aviso ia para o cartão de um aparelho
    parado. Agora a medida que termina com o aparelho fora do ar não corrige nem avisa."""
    s = harness.state
    assert s is not None
    rt, trilhas = _relogio_no_central(harness, monkeypatch, [-30])

    def medir_enquanto_para(*_a: Any, **_k: Any) -> int:
        trilhas.append("medir")
        s.devices._set_state(rt, InstanceState.stopped, "parado no meio da medida")
        return -30
    monkeypatch.setattr(rt.adb, "clock_skew_s", medir_enquanto_para)
    await s.devices.conferir_relogio_do_convidado(rt)
    assert trilhas == ["medir"], "com o aparelho fora do ar, a hora não é acertada"
    s.devices._aviso_do_relogio(rt, -30)
    assert not (rt.attention or "").startswith(manager_mod.RELOGIO_PREFIXO)


@pytest.mark.parametrize("saida", ["perdido", "soltar", "parar"])
async def test_toda_saida_do_ar_cancela_as_tarefas_do_relogio_e_da_arrumacao(
        harness: Harness, monkeypatch: pytest.MonkeyPatch, saida: str) -> None:
    """Revisão do PR #12: `stop_instance`, `_on_device_lost` e `_soltar_do_painel` cancelavam só captura/automação
    (e boot); as tarefas novas `clock` e `arrumacao` seguiam vivas depois de o aparelho sair do ar."""
    s = harness.state
    assert s is not None
    devs = s.devices
    rt = devs.get("android-01")
    await devs.start_instance(rt)
    await harness.wait(lambda: rt.state == InstanceState.online, what="android-01 no ar")
    presas = {nome: asyncio.create_task(asyncio.sleep(3600)) for nome in ("clock", "arrumacao")}
    rt.tasks.update(presas)
    if saida == "perdido":
        devs._on_device_lost(rt, "adb sumiu")
    elif saida == "soltar":
        await devs._soltar_do_painel(rt, "o worker soltou")
    else:
        await devs.stop_instance(rt)
    await asyncio.sleep(0)
    assert all(t.cancelled() or t.done() for t in presas.values()), {n: t.done() for n, t in presas.items()}
    assert not ({"clock", "arrumacao"} & set(rt.tasks))

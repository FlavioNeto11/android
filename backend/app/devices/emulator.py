"""Processo do emulador: inicia destacado (sobrevive a reinícios do backend), readota pelo PID
gravado e encerra SOMENTE processos que este projeto iniciou."""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from ..config import AndroidCfg, Config
from .adb import Adb
from .sdk import IS_WINDOWS, NEW_GROUP, NO_WINDOW, SdkTools


class EmulatorError(RuntimeError):
    pass


SNAPSHOT_NAME = "poc_hib"


def build_args(tools: SdkTools, avd_name: str, console_port: int, a: AndroidCfg, *, wipe_data: bool,
               from_snapshot: bool = False) -> list[str]:
    """Snapshots: sem hibernação, tudo desligado (`-no-snapshot`), como sempre. Com hibernação, o salvamento
    AUTOMÁTICO ao sair continua desligado (só o snapshot explícito do `hibernate` existe) e o boot a frio nunca
    carrega snapshot; acordar carrega exatamente o snapshot nomeado. `-wipe-data` e snapshot nunca andam juntos."""
    if from_snapshot and wipe_data:
        raise EmulatorError("reset (-wipe-data) e snapshot são incompatíveis")
    if not a.hibernation:
        snap = ["-no-snapshot"]
    elif from_snapshot:
        snap = ["-snapshot", SNAPSHOT_NAME, "-no-snapshot-save"]
    else:
        snap = ["-no-snapshot-load", "-no-snapshot-save"]
    # `-no-window` não tem flag que o desfaça, então ele só pode ser omitido AQUI — anexar algo em
    # `extra_emulator_args` não adiantaria. Com janela é o aparelho-loja: o usuário digita a conta Google direto
    # nela, e a tecla nunca passa por este processo.
    janela = [] if a.window else ["-no-window"]
    args = [str(tools.emulator), "-avd", avd_name, "-port", str(console_port), *janela, "-no-audio",
            "-no-boot-anim", *snap, "-gpu", a.gpu_mode, "-accel", "on", "-no-metrics"]
    if wipe_data:
        args.append("-wipe-data")
    if a.dns_servers:
        args.extend(["-dns-server", ",".join(a.dns_servers)])
    args.extend(a.args_extras_efetivos())
    return args


def _rotate_log(path: Path, limite_bytes: int = 8 * 1024 * 1024) -> None:
    """`emulator-<avd>.log` era aberto em append e crescia para sempre entre boots (achado #144): um aparelho
    de tarefa de longa duração — ou um laço de falha de sessão reiniciando o Android repetidas vezes — nunca via
    o arquivo encolher. Mesmo gatilho e mesmo limite do Appium (`automation/appium_server.py::_rotate_log`): só
    corta na PRÓXIMA subida (o processo em execução mantém o descritor antigo), e o `.log.1` anterior é
    substituído, nunca acumulado."""
    try:
        if path.exists() and path.stat().st_size > limite_bytes:
            previous = path.with_suffix(".log.1")
            previous.unlink(missing_ok=True)
            os.replace(path, previous)
    except OSError:
        pass  # rotação é higiene, nunca motivo para recusar o boot


def start_process(cfg: Config, tools: SdkTools, avd_name: str, console_port: int, a: AndroidCfg,
                  *, wipe_data: bool = False, from_snapshot: bool = False) -> int:
    if not tools.emulator.exists():
        raise EmulatorError(f"emulator não encontrado em {tools.emulator}")
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = cfg.logs_dir / f"emulator-{avd_name}.log"
    _rotate_log(log_path)
    logf = open(log_path, "ab", buffering=0)  # noqa: SIM115 - herdado pelo processo filho
    try:
        # `start_new_session` é o NEW_GROUP do mundo POSIX, e sem ele o "inicia destacado" da primeira linha
        # deste arquivo valia só no Windows: no Linux o emulador herdava a sessão e o grupo de processos do
        # agente, e uma unidade systemd com o `KillMode=control-group` padrão derrubaria TODOS os emuladores a
        # cada `systemctl restart farm-worker` — o mesmo acoplamento que o projeto mediu e evitou no Windows
        # ("processo em sessão SSH morre no logout"). `setsid` também tira o emulador do grupo de terminal, então
        # um Ctrl+C no agente rodando em primeiro plano deixa de matar os aparelhos junto.
        proc = subprocess.Popen(build_args(tools, avd_name, console_port, a, wipe_data=wipe_data, from_snapshot=from_snapshot),
                                stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=tools.env(), creationflags=NO_WINDOW | NEW_GROUP, close_fds=True,
                                start_new_session=not IS_WINDOWS)
    finally:
        logf.close()
    return proc.pid


def is_our_emulator(pid: int | None, avd_name: str) -> bool:
    """Confere se o PID gravado ainda é o emulador DESTE AVD (PIDs são reciclados pelo SO)."""
    if not pid:
        return False
    try:
        p = psutil.Process(pid)
        cmd = p.cmdline()
        name = p.name().lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    return ("emulator" in name or "qemu" in name) and avd_name in cmd


def qemu_child(pid: int) -> psutil.Process | None:
    try:
        for ch in psutil.Process(pid).children(recursive=True):
            if "qemu" in ch.name().lower():
                return ch
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    return None


#: Quanto tempo um lançador pode ficar sem ser lido antes de o cache de uso o esquecer. O laço de métricas lê a
#: cada 3 s, então 60 s são 20 voltas perdidas: aparelho que parou, hibernou ou trocou de pid não deixa objeto para trás.
USO_TTL_S = 60.0


@dataclass
class _UsoDoEmulador:
    """Os objetos `psutil.Process` de um emulador, mantidos entre leituras (o `cpu_percent` é um delta por objeto)."""
    launcher: psutil.Process
    filhos: dict[int, psutil.Process]
    lido_em: float


class MedidorDeUso:
    """Mede RSS e CPU do emulador (lançador + filhos) com objetos `psutil.Process` que DURAM entre leituras.

    `Process.cpu_percent(interval=None)` devolve o uso desde a chamada anterior NO MESMO objeto; num objeto recém-criado
    a primeira leitura é sempre 0,0. Recriar `psutil.Process(pid)` e os filhos a cada chamada (como era até o 14.11)
    fazia `resources.cpu_percent` valer 0,0 o tempo todo, com o emulador ocioso gastando de 1,1 a 1,2 núcleo.

    Primeira leitura de um objeto novo: contribui com 0,0 (não há intervalo para medir). Um aparelho que acabou de ligar
    mostra 0,0 por uma volta (3 s) e da segunda em diante o valor é o real; um filho que nasce no meio da vida soma 0,0 só
    na volta em que aparece. Mantemos 0,0 (e não "sem medida") para não mudar a assinatura nem o contrato do painel."""

    def __init__(self, ttl_s: float = USO_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._por_pid: dict[int, _UsoDoEmulador] = {}
        # A chamada vem de uma thread do pool (`asyncio.to_thread`): o lock cobre a leitura inteira, porque duas leituras
        # simultâneas do mesmo pid medem a CPU uma contra a outra e estragam o delta.
        self._lock = threading.Lock()

    def tamanho(self) -> int:
        with self._lock:
            return len(self._por_pid)

    def esquecer(self, pid: int) -> None:
        with self._lock:
            self._por_pid.pop(pid, None)

    def _purgar(self, agora: float) -> None:
        for pid in [p for p, u in self._por_pid.items() if agora - u.lido_em > self._ttl_s]:
            del self._por_pid[pid]

    def _launcher(self, pid: int, agora: float) -> _UsoDoEmulador:
        novo = psutil.Process(pid)
        uso = self._por_pid.get(pid)
        # pid reaproveitado pelo SO: mesmo número, outro `create_time` -> outro processo, tudo de novo.
        if uso is None or uso.launcher.create_time() != novo.create_time():
            uso = _UsoDoEmulador(launcher=novo, filhos={}, lido_em=agora)
            self._por_pid[pid] = uso
        return uso

    def ler(self, pid: int) -> tuple[float, float] | None:
        """(rss_mb, cpu_percent) somando o lançador e os filhos; None se o processo sumiu."""
        with self._lock:
            agora = time.monotonic()
            self._purgar(agora)
            try:
                uso = self._launcher(pid, agora)
                uso.lido_em = agora
                vivos: dict[int, psutil.Process] = {}
                for ch in uso.launcher.children(recursive=True):
                    antigo = uso.filhos.get(ch.pid)
                    # reaproveita o objeto do filho já conhecido; filho novo entra; pid de filho reciclado troca.
                    vivos[ch.pid] = antigo if antigo is not None and antigo.create_time() == ch.create_time() else ch
                uso.filhos = vivos  # o que não apareceu mais sai do cache
                rss = cpu = 0.0
                for p in [uso.launcher, *vivos.values()]:
                    try:
                        rss += p.memory_info().rss
                        cpu += p.cpu_percent(interval=None)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        if p is uso.launcher:
                            raise
                        uso.filhos.pop(p.pid, None)   # filho que morreu entre a listagem e a leitura
                return round(rss / (1024 * 1024), 1), round(cpu, 1)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self._por_pid.pop(pid, None)
                return None


_MEDIDOR = MedidorDeUso()


def process_usage(pid: int) -> tuple[float, float] | None:
    """(rss_mb, cpu_percent) somando o launcher e o qemu filho; ver `MedidorDeUso` (a 1ª leitura de um pid devolve CPU 0,0)."""
    return _MEDIDOR.ler(pid)


#: Teto do `sync` antes de desligar. Normalmente leva menos de 1 s; um convidado travado não pode segurar o
#: desligamento além disso — ele segue para o `emu kill` do mesmo jeito.
SYNC_ANTES_DE_DESLIGAR_S = 20.0


def stop_process(adb: Adb, pid: int | None, avd_name: str, *, grace_s: float = 25) -> str:
    """Pede desligamento pelo console (`emu kill`); só força o término do PID que nós iniciamos.

    Antes, um `sync` no convidado. O `emu kill` é um corte de energia do Android: o que estava no cache de páginas do
    convidado se perde. Medido em 29/09 (25.1, android-05): o perfil de VPN importado 26 s antes do `restart` sumiu
    (`profiles.db` sem linhas, `configs/1.json` com 0 B). Vale para qualquer dado recém-gravado — sessão de app
    logado, preferência, arquivo de configuração —, então o `sync` é de todo desligamento, e não só do da rede. O
    `hibernate` já fazia (`Adb.snapshot_save`); este é o caminho do `stop`, do `restart` e do rodízio, local e no
    agente do worker. Falha ou demora do `sync` não impede o desligamento."""
    try:
        adb.shell("sync", timeout=SYNC_ANTES_DE_DESLIGAR_S)
    except Exception:  # noqa: BLE001 - o sync é proteção de dado, não condição para desligar
        pass
    try:
        adb.emu_kill()
    except Exception:  # noqa: BLE001 - segue para a verificação por PID
        pass
    if not is_our_emulator(pid, avd_name):
        return "solicitado via console (processo não iniciado por este projeto: não forçado)"
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if not is_our_emulator(pid, avd_name):
            return "encerrado via console"
        time.sleep(1)
    try:
        parent = psutil.Process(pid)  # type: ignore[arg-type]
        for ch in parent.children(recursive=True):
            ch.terminate()
        parent.terminate()
        psutil.wait_procs([parent], timeout=10)
    except psutil.NoSuchProcess:
        pass
    return "encerrado à força após tempo limite"


def read_log_tail(path: Path, max_bytes: int = 4000) -> str:
    try:
        data = path.read_bytes()[-max_bytes:]
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------------- renderizador selecionado (29.11)
#: Os renderizadores que o emulador 37.1.11 SELECIONA de fato, pelo nome canônico. Medido em 30/09/2026: `-gpu host`
#: e `-gpu swiftshader_indirect` (ou `swiftshader`) chegam ao que pedem; `-gpu angle_indirect` é recusado ("not
#: valid, switching to 'auto'") e `-gpu swangle` é aceito e acaba em `gles_mode_selected:swiftshader`. É o conjunto
#: que o `renderizador_recusado` de um `app.yaml` pode citar.
RENDERIZADORES = ("host", "swiftshader")

#: Como cada um aparece numa frase para a pessoa.
NOME_DO_RENDERIZADOR = {"host": "da GPU do host", "swiftshader": "SwiftShader"}

#: A linha que o emulador escreve a cada subida. **Argumento aceito não é renderizador usado**: o `-gpu` que foi
#: passado só diz o que se PEDIU; o que ele selecionou está aqui, e em nenhum outro lugar.
_EMUGL = re.compile(rb"emuglConfig_init:\s*vulkan_mode_selected:(\S+)\s+gles_mode_selected:(\S+)")


@dataclass(frozen=True, slots=True)
class Renderizador:
    """O que o emulador SELECIONOU, como ele escreveu. `vulkan` nulo = quem declarou só sabia do GLES."""

    gles: str
    vulkan: str | None = None


def normalizar_renderizador(modo: str | None) -> str | None:
    """O nome canônico de um modo de GPU: `swiftshader_indirect` e `swiftshader` são o mesmo renderizador (o
    sufixo diz só como o convidado fala com ele). Vazio vira `None`, que é "não se sabe"."""
    limpo = (modo or "").strip().lower()
    return limpo.removesuffix("_indirect") or None


def houve_fallback(configurado: str | None, gles: str | None) -> bool:
    """O emulador selecionou outro renderizador que não o pedido? Sem selecionado não há o que comparar, e `auto`
    é pedir "o que houver" — nenhum dos dois é fallback."""
    pedido, usado = normalizar_renderizador(configurado), normalizar_renderizador(gles)
    return pedido is not None and usado is not None and pedido != "auto" and pedido != usado


def _renderizador_da_linha(achado: re.Match[bytes] | None) -> Renderizador | None:
    if achado is None:
        return None
    return Renderizador(gles=achado.group(2).decode("utf-8", "replace"),
                        vulkan=achado.group(1).decode("utf-8", "replace"))


def renderizador_do_log(texto: str) -> Renderizador | None:
    """O renderizador da ÚLTIMA subida registrada em `texto`, ou `None` quando a linha não está lá.

    O log é aberto em append (`start_process`): cada subida escreve a sua linha, e as anteriores descrevem processos
    que já morreram. Ausência da linha é "não se sabe" — nunca "o que foi pedido".
    """
    ultimo: re.Match[bytes] | None = None
    for ultimo in _EMUGL.finditer(texto.encode("utf-8", "replace")):
        pass
    return _renderizador_da_linha(ultimo)


def ler_renderizador(logs_dir: Path, avd_name: str) -> Renderizador | None:
    """`renderizador_do_log` sobre o `emulator-<avd>.log` daquela pasta, do começo ao fim.

    A linha fica no INÍCIO de cada subida, então a cauda não serve; e o arquivo só é cortado na subida seguinte
    (`_rotate_log`), então num aparelho de longa duração ele passa dos 8 MB. Por isso é lido linha a linha, em
    bytes, sem carregar nem decodificar o arquivo inteiro. Quem chama faz isto uma vez por subida, fora do laço de
    eventos. Arquivo ausente ou ilegível é "não se sabe".
    """
    ultimo: re.Match[bytes] | None = None
    try:
        with (logs_dir / f"emulator-{avd_name}.log").open("rb") as fh:
            for linha in fh:
                if b"emuglConfig_init" in linha:
                    ultimo = _EMUGL.search(linha) or ultimo
    except OSError:
        return None
    return _renderizador_da_linha(ultimo)


#: Quanto do log viaja no desfecho de um comando. A cauda é o que interessa (a falha está no fim) e o teto existe
#: porque isto atravessa o canal do worker e vai parar no `result` de um comando, que é lido pelo painel.
LOG_MAX_BYTES = 8000

#: O log do emulador é saída de processo: ninguém DEVERIA escrever segredo nele, e é justamente por isso que a
#: redação mora aqui — o que sai da máquina passa por um filtro, em vez de depender de ninguém nunca errar.
_SEGREDO = re.compile(
    r"(?i)\b(token|password|passwd|senha|secret|credential|credencial|api[_-]?key|authorization)\b"
    r"\s*[:=]\s*\S+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")


def redigir(texto: str) -> str:
    """Troca o que parece segredo por uma marca. Conservador de propósito: prefere apagar demais a vazar.

    A ordem importa: `Bearer` primeiro. Em `Authorization: Bearer <token>`, a regra de rótulo casaria
    `Authorization: Bearer` e pararia ali — o token ficaria inteiro na linha seguinte ao "«removido»".
    """
    return _SEGREDO.sub(lambda m: f"{m.group(1)}=«removido»", _BEARER.sub("Bearer «removido»", texto))


def log_do_emulador(logs_dir: Path, avd_name: str, max_bytes: int = LOG_MAX_BYTES) -> dict[str, Any]:
    """A cauda do log daquele AVD, pronta para viajar no `result.data` de um comando.

    Mesma forma nas duas máquinas (`workers/local.py` e `worker/executor.py`): quem lê o desfecho de um `start`
    que falhou não precisa saber onde o emulador morava. Era exatamente o que faltava — no aparelho remoto o
    operador recebia uma frase e nada mais, e o log ficava na outra máquina, fora de alcance.
    """
    caminho = logs_dir / f"emulator-{avd_name}.log"
    cauda = redigir(read_log_tail(caminho, max_bytes))
    return {"emulator_log": cauda, "emulator_log_path": str(caminho),
            "emulator_log_bytes": len(cauda.encode("utf-8", "replace"))}

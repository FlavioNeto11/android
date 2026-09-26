"""ADB sempre direcionado a um serial específico (`adb -s <serial> …`). Chamadas bloqueantes:
devem rodar na thread do executor do dispositivo, nunca no event loop."""
from __future__ import annotations

import re
import subprocess
import time

from ..security.redaction import redact
from .sdk import NO_WINDOW, SdkTools

PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
ACTIVITY_RE = re.compile(r"^[A-Za-z0-9_.$]+$")
# O que `pm path` devolve: /data/app/~~<aleatório>==/<pacote>-<aleatório>==/base.apk (ou split_config.*.apk).
REMOTE_APK_RE = re.compile(r"^/data/app/[A-Za-z0-9_.=~/\-]+\.apk$")

KEYCODES = {"back": 4, "home": 3, "recents": 187, "enter": 66, "delete": 67, "wakeup": 224, "menu": 82}


class AdbError(RuntimeError):
    pass


class AdbTimeout(AdbError):
    """O comando de ADB estourou o prazo — o que NÃO é o mesmo que ter falhado.

    Um `adb install` ou um `am start` que estoura o prazo num aparelho remoto lento continua correndo no
    aparelho: o `pm install` termina depois, e quem reinstalasse por cima estaria repetindo um efeito que deu
    certo. Por isso é uma subclasse de `AdbError` (ninguém que já tratava erro de adb deixa de tratar) com
    identidade própria: quem decide desfecho de comando a traduz para `uncertain`, e não para `failed`.
    """


class Adb:
    def __init__(self, tools: SdkTools, serial: str):
        self.tools = tools
        self.serial = serial

    # -- base -----------------------------------------------------------------
    def _run(self, args: list[str], *, timeout: float = 30, binary: bool = False,
             entrada: str | None = None) -> subprocess.CompletedProcess:
        """`entrada` vai pelo STDIN do processo. Existe por um motivo de seguranca, nao de conveniencia: o que
        entra por `args` vira linha de comando do `adb.exe` DESTA maquina, legivel por qualquer processo local que
        saiba ler argv, enquanto o processo roda. Ver `input_text_ascii`."""
        cmd = [str(self.tools.adb), "-s", self.serial, *args]
        try:
            return subprocess.run(cmd, capture_output=True, timeout=timeout, env=self.tools.env(),
                                  creationflags=NO_WINDOW, text=not binary, input=entrada,
                                  **({} if binary else {"encoding": "utf-8", "errors": "replace"}))
        except subprocess.TimeoutExpired as exc:
            # Só o subcomando entra na mensagem: os argumentos podem carregar conteúdo digitado, e esta mensagem
            # vira evento, log e corpo de resposta HTTP.
            raise AdbTimeout(f"adb {args[0] if args else '?'} excedeu {timeout}s em {self.serial}") from exc

    def shell(self, command: str, *, timeout: float = 30) -> str:
        """`command` é montado apenas a partir de constantes e valores validados (nunca texto livre)."""
        res = self._run(["shell", command], timeout=timeout)
        if res.returncode != 0:
            raise AdbError((res.stderr or res.stdout or "").strip() or f"adb shell falhou ({res.returncode})")
        return res.stdout

    # -- estado -----------------------------------------------------------------
    def state(self) -> str | None:
        res = self._run(["get-state"], timeout=8)
        return res.stdout.strip() if res.returncode == 0 else None

    def boot_completed(self) -> bool:
        res = self._run(["shell", "getprop sys.boot_completed"], timeout=8)
        return res.returncode == 0 and res.stdout.strip() == "1"

    # Serviços do `system_server` sem os quais NADA acontece no aparelho: sem `activity` não se abre app, sem
    # `package` não se instala nem se lista o que está instalado. `settings` entra porque é ele que o Appium usa
    # (`settings delete global hidden_api_policy`) ao abrir a sessão — foi o "exited with code 20" medido em campo.
    SERVICOS_DO_CONVIDADO = ("activity", "package", "settings")

    def framework_alive(self, *, timeout: float = 25) -> bool:
        """O Android do convidado está VIVO, e não só o adb respondendo?

        `sys.boot_completed` continua `1` depois de o `system_server` morrer: o aparelho responde `device` ao
        `adb get-state`, o painel o mostra `online` e toda tarefa despachada para ele falha lá dentro. Medido em
        21/09/2026 em dois emuladores remotos no ar há mais de um dia: `service check settings/activity/package`
        respondia `not found` com `boot_completed=1`.

        Levanta `AdbError`/`AdbTimeout` quando não dá para SABER (adb não respondeu) — "não sei" nunca é "morto":
        quem chama decide, e degradar por um adb lento seria trocar uma mentira por outra.
        """
        res = self._run(["shell", "; ".join(f"service check {s}" for s in self.SERVICOS_DO_CONVIDADO)],
                        timeout=timeout)
        out = (res.stdout or "") + (res.stderr or "")
        if res.returncode != 0 and "Service" not in out:
            raise AdbError((out.strip() or f"service check falhou ({res.returncode})")[:200])
        linhas = [ln for ln in out.splitlines() if ln.strip().startswith("Service ")]
        if len(linhas) < len(self.SERVICOS_DO_CONVIDADO):
            raise AdbError("o aparelho não respondeu a `service check` por inteiro")
        return all("not found" not in ln for ln in linhas)

    def system_server_alive(self, *, timeout: float = 8) -> bool:
        """Leitura que ATRAVESSA o `system_server` (o SettingsProvider mora nele), sem mudar nada. `service check`
        só prova o `servicemanager`: no wake de 25/09/2026 ele respondia enquanto `settings put` ficava 40 s mudo.
        Tempo esgotado levanta `AdbTimeout` — quem chama trata como "não se sabe", nunca como pronto."""
        res = self._run(["shell", "settings get global window_animation_scale"], timeout=timeout)
        if res.returncode != 0:
            raise AdbError((res.stderr or res.stdout or "").strip()[:200] or f"settings get falhou ({res.returncode})")
        return bool((res.stdout or "").strip())

    def display_alive(self, *, timeout: float = 12) -> bool:
        """O SurfaceFlinger produz um quadro? A imagem vai para `/dev/null`: nada é gravado, o conteúdo não importa.
        No wake de 25/09/2026 foi o que NUNCA respondeu depois do restore."""
        res = self._run(["shell", "screencap > /dev/null && echo ok"], timeout=timeout)
        return res.returncode == 0 and "ok" in (res.stdout or "")

    def guest_pressure(self, *, timeout: float = 8) -> dict[str, float]:
        """Pressão DENTRO do convidado: load average e memória, lidos de `/proc`.

        É o que faltava para o android-12: `framework_alive` dizia "vivo" (os serviços existiam), mas o convidado
        estava em thrash — load 22 com 2 vCPUs, 87 MB livres, swap em uso — e cada comando levava 20–40 s. A
        sonda de saúde lia isso como "não deu para saber" e nunca como doença.
        """
        res = self._run(["shell", "cat /proc/loadavg; grep -E 'MemTotal|MemAvailable' /proc/meminfo; nproc"],
                        timeout=timeout)
        out = (res.stdout or "")
        if res.returncode != 0 or "MemTotal" not in out:
            raise AdbError((out.strip() or f"leitura de /proc falhou ({res.returncode})")[:200])
        linhas = [ln.strip() for ln in out.splitlines() if ln.strip()]
        dados: dict[str, float] = {}
        for ln in linhas:
            partes = ln.split()
            if ln.startswith("MemTotal:"):
                dados["mem_total_mb"] = float(partes[1]) / 1024
            elif ln.startswith("MemAvailable:"):
                dados["mem_available_mb"] = float(partes[1]) / 1024
            elif "load1" not in dados and len(partes) >= 3 and partes[0].replace(".", "", 1).isdigit():
                dados["load1"] = float(partes[0])
            elif ln.isdigit():
                dados["ncpu"] = float(ln)
        dados.setdefault("ncpu", 1.0)
        return dados

    def connectivity_probe(self, *, timeout: float = 60) -> dict[str, bool]:
        """Internet DENTRO do convidado (rota, DNS, TCP 443, rede validada) — ver `devices/conectividade.py`.

        Levanta `AdbError` quando não dá para saber: adb mudo não é "sem internet"."""
        from .conectividade import comando_sonda, ler_sonda  # noqa: PLC0415 - conectividade importa models
        res = self._run(["shell", comando_sonda()], timeout=timeout)
        try:
            return ler_sonda(res.stdout or "")
        except ValueError as exc:
            raise AdbError(str(exc)) from exc

    def ui_ready(self) -> bool:
        """Launcher no ar (não FallbackHome) e sem keyguard — antes disso o screenshot sai preto."""
        out = self._run(["shell", "dumpsys window | grep -E 'mCurrentFocus|isKeyguardShowing'"], timeout=10).stdout
        if "FallbackHome" in out or "mCurrentFocus=null" in out or "mCurrentFocus" not in out:
            return False
        return "isKeyguardShowing=true" not in out

    def current_focus(self) -> tuple[str | None, str | None]:
        out = self._run(["shell", "dumpsys window | grep -E 'mCurrentFocus'"], timeout=10).stdout
        m = re.search(r"mCurrentFocus=Window\{[^ ]+ [^ ]+ ([^/\s}]+)/([^\s}]+)\}", out)
        if m:
            return m.group(1), m.group(2)
        return None, None

    def system_dialog(self) -> str | None:
        """Descrição da janela de SISTEMA em foco (ANR, "parou de funcionar"), ou None.

        `current_focus` não enxerga essas janelas: o título delas não tem a forma `pacote/atividade`, então a
        regex de lá falha e ela devolve `(None, None)` — que quem lê vira "nenhuma janela". Foi exatamente isso
        que escondeu um ANR do SystemUI travando a entrega de um app (medido em 19/09/2026).
        """
        out = self._run(["shell", "dumpsys window | grep -E 'mCurrentFocus'"], timeout=10).stdout
        m = re.search(r"mCurrentFocus=Window\{\S+ \S+ ([^}]+)\}", out)
        if not m:
            return None
        desc = m.group(1).strip()
        # `pacote/atividade` é janela normal de app; o que sobra sem barra é diálogo do sistema.
        return None if not desc or "/" in desc else desc

    # Rótulos dos botões do diálogo, na ordem de preferência: "aguardar" MANTÉM o app vivo; fechar mata.
    _BOTOES_DIALOGO = (
        re.compile(r"^(wait|aguardar|esperar)$", re.I),
        re.compile(r"^(ok|fechar|close|close app|fechar app|fechar o app)$", re.I),
    )

    def dismiss_system_dialog(self, *, timeout: float = 25) -> str | None:
        """Dispensa o diálogo de sistema em foco tocando no botão. Devolve o rótulo tocado, ou None.

        `settings put global hide_error_dialogs 1` (em `prepare_for_automation`) impede diálogos FUTUROS e não
        remove um que já está na tela — por isso este toque existe.
        """
        if not self.system_dialog():
            return None
        try:
            xml = self.shell("uiautomator dump /sdcard/_dlg.xml >/dev/null 2>&1; cat /sdcard/_dlg.xml; "
                             "rm -f /sdcard/_dlg.xml", timeout=timeout)
        except AdbError:
            return None
        nos = re.findall(r'text="([^"]*)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
        for padrao in self._BOTOES_DIALOGO:
            for texto, x1, y1, x2, y2 in nos:
                if padrao.match(texto.strip()):
                    self.tap((int(x1) + int(x2)) // 2, (int(y1) + int(y2)) // 2)
                    return texto.strip()
        return None

    def prepare_for_automation(self) -> None:
        """Ajustes idempotentes pós-boot: sem animações, tela sempre ligada, sem keyguard, sem diálogos de ANR."""
        self.shell(
            "settings put global window_animation_scale 0; settings put global transition_animation_scale 0; "
            "settings put global animator_duration_scale 0; settings put system screen_off_timeout 2147483647; "
            "svc power stayon true; settings put global hide_error_dialogs 1; "
            # com teclado físico presente (hw.keyboard=yes) o teclado virtual não cobre botões da tela
            "settings put secure show_ime_with_hard_keyboard 0; "
            "locksettings set-disabled true; input keyevent 224; wm dismiss-keyguard",
            timeout=40,
        )
        # `hide_error_dialogs` acima só vale para o PRÓXIMO diálogo. Se um já estiver na tela — o caso do ANR do
        # SystemUI no primeiro boot de um AVD novo — ele fica lá, sem dono de janela reconhecível, e trava a
        # abertura de qualquer app. Dispensar aqui é idempotente: sem diálogo, não faz nada.
        self.dismiss_system_dialog()

    def wm_size(self) -> tuple[int, int] | None:
        m = re.search(r"(\d+)x(\d+)", self._run(["shell", "wm size"], timeout=8).stdout)
        return (int(m.group(1)), int(m.group(2))) if m else None

    # -- tela -------------------------------------------------------------------
    def screencap_png(self, *, timeout: float = 20) -> bytes:
        res = self._run(["exec-out", "screencap", "-p"], timeout=timeout, binary=True)
        if res.returncode != 0 or not res.stdout.startswith(b"\x89PNG"):
            raise AdbError(f"screencap falhou em {self.serial}")
        return res.stdout

    # -- entrada -------------------------------------------------------------------
    def tap(self, x: int, y: int) -> None:
        self.shell(f"input tap {int(x)} {int(y)}", timeout=15)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}", timeout=20)

    def long_press(self, x: int, y: int, duration_ms: int = 800) -> None:
        self.swipe(x, y, x, y, duration_ms)

    def keyevent(self, key: str) -> None:
        self.shell(f"input keyevent {KEYCODES[key]}", timeout=15)

    def input_text_ascii(self, text: str) -> None:
        """Fallback sem Appium: apenas ASCII imprimível. O comando vai pelo STDIN, nunca pelos argumentos.

        Achado #129. Este é o caminho que sobra quando não há sessão de automação — comum nos aparelhos remotos e
        depois de uma falha do Appium — e é por ele que a senha de um perfil passa quando o operador digita pelo
        painel. Montado como argumento, o texto ficava no `argv` do `adb.exe` DESTA máquina enquanto o comando
        rodava, legível por qualquer processo local que leia a lista de processos. `adb shell -T`, sem comando,
        lê do stdin o que fazer — e stdin não é legível de fora do processo.

        O escape continua sendo o do shell do aparelho: aspas simples, `%` literal e espaço como `%s` (é assim que
        `input text` recebe espaço). O que mudou foi POR ONDE a linha chega ao adb.
        """
        if not text.isascii() or any(ord(c) < 32 for c in text):
            raise AdbError("Sem sessão de automação, só é possível digitar texto ASCII simples.")
        escaped = text.replace("\\", "\\\\").replace("'", "'\\''").replace("%", "\\%").replace(" ", "%s")
        # `-T` desliga o pty: sem isso o adb pode abrir terminal interativo e o que vem pelo stdin vira eco.
        res = self._run(["shell", "-T"], timeout=20, entrada=f"input text '{escaped}'\nexit\n")
        if res.returncode != 0:
            # O shell do aparelho repete a linha que falhou na mensagem de erro — e a linha tem o texto digitado.
            # Esta é a mensagem que vira evento, log e corpo de resposta HTTP: passa pela redação antes.
            raise AdbError(redact((res.stderr or res.stdout or "").strip()) or f"adb shell falhou ({res.returncode})")

    # -- apps ---------------------------------------------------------------------
    def install(self, apk_path: str, *, timeout: float = 240, allow_downgrade: bool = False) -> str:
        res = self._run(["install", "-r", "-g", *(["-d"] if allow_downgrade else []), apk_path], timeout=timeout)
        out = (res.stdout or "") + (res.stderr or "")
        if res.returncode != 0 or "Success" not in out:
            raise AdbError(_explain_install_failure(out))
        return out.strip()

    def install_multiple(self, apk_paths: list[str], *, timeout: float = 600,
                         allow_downgrade: bool = False) -> str:
        """Conjunto de splits é unidade atômica: ou entra inteiro, ou o `pm` recusa e nada é aplicado."""
        if not apk_paths:
            raise AdbError("nenhum APK informado para instalar")
        res = self._run(["install-multiple", "-r", "-g", *(["-d"] if allow_downgrade else []), *apk_paths],
                        timeout=timeout)
        out = (res.stdout or "") + (res.stderr or "")
        if res.returncode != 0 or "Success" not in out:
            raise AdbError(_explain_install_failure(out))
        return out.strip()

    def uninstall(self, package: str, *, timeout: float = 120) -> None:
        _check_package(package)
        res = self._run(["uninstall", package], timeout=timeout)
        out = (res.stdout or "") + (res.stderr or "")
        if res.returncode != 0 or "Success" not in out:
            raise AdbError((out.strip() or "desinstalação falhou")[:300])

    def clear_data(self, package: str) -> None:
        """Apaga os dados do app — inclusive a sessão. Nunca é efeito colateral de outra operação."""
        _check_package(package)
        out = self.shell(f"pm clear {package}", timeout=60)
        if "Success" not in out:
            raise AdbError((out.strip() or "pm clear falhou")[:300])

    def pm_path(self, package: str) -> list[str]:
        """Caminhos do base e de cada split instalado. Lista vazia = pacote ausente."""
        _check_package(package)
        res = self._run(["shell", f"pm path {package}"], timeout=20)
        return sorted(ln.split(":", 1)[1].strip() for ln in (res.stdout or "").splitlines() if ln.startswith("package:"))

    def pull(self, remote: str, local: str, *, timeout: float = 600) -> None:
        """Copia UM arquivo de APK do aparelho para o host.

        Só aceita caminho de APK instalado (`/data/app/…/*.apk`), exatamente o que `pm path` devolve. Isto não é um
        `adb pull` genérico de propósito: a única coisa que o projeto tem motivo para tirar de um aparelho é o pacote
        que o usuário instalou pela loja — nunca dados de app, nunca armazenamento do usuário.
        """
        if not REMOTE_APK_RE.match(remote) or ".." in remote:
            raise AdbError("caminho remoto inválido: só se copia APK instalado em /data/app")
        res = self._run(["pull", remote, local], timeout=timeout)
        if res.returncode != 0:
            # Sem o caminho na mensagem: ele vira evento, log e corpo de resposta HTTP.
            raise AdbError(f"adb pull falhou em {self.serial} ({res.returncode})")

    def open_store_listing(self, package: str) -> None:
        """Abre a página do app na Play Store DESTE aparelho. O toque em Instalar/Atualizar é sempre do usuário."""
        _check_package(package)
        # Entre aspas simples: `?` é curinga para o shell do aparelho e sumiria com o parâmetro.
        out = self.shell(f"am start -a android.intent.action.VIEW -d 'market://details?id={package}'", timeout=30)
        if "Error" in out or "unable to resolve" in out.lower():
            raise AdbError("Não foi possível abrir a Play Store neste aparelho (a imagem tem loja?).")

    def package_info(self, package: str) -> dict[str, object] | None:
        """Estado REALMENTE instalado, lido do aparelho. `None` quando o pacote não está lá."""
        _check_package(package)
        out = self._run(["shell", f"dumpsys package {package}"], timeout=30).stdout or ""
        version_name = re.search(r"versionName=(\S+)", out)
        version_code = re.search(r"versionCode=(\d+)", out)
        if not (version_name or version_code):
            return None
        splits = re.search(r"splits=\[([^\]]*)\]", out)
        first = re.search(r"firstInstallTime=(.+)", out)
        last = re.search(r"lastUpdateTime=(.+)", out)
        return {
            "version_name": version_name.group(1) if version_name else None,
            "version_code": int(version_code.group(1)) if version_code else None,
            "splits": [s.strip() for s in splits.group(1).split(",") if s.strip()] if splits else [],
            "first_install_time": first.group(1).strip() if first else None,
            "last_update_time": last.group(1).strip() if last else None,
            "paths": self.pm_path(package),
        }

    def getprop(self, name: str) -> str:
        if not re.match(r"^[A-Za-z0-9._]{1,60}$", name):
            raise AdbError("propriedade inválida")
        return self._run(["shell", f"getprop {name}"], timeout=10).stdout.strip()

    def wm_density(self) -> int | None:
        out = self._run(["shell", "wm density"], timeout=10).stdout or ""
        m = re.search(r"Physical density:\s*(\d+)", out)
        override = re.search(r"Override density:\s*(\d+)", out)
        chosen = override or m
        return int(chosen.group(1)) if chosen else None

    def is_installed(self, package: str) -> bool:
        _check_package(package)
        return f"package:{package}" in self._run(["shell", f"pm list packages {package}"], timeout=20).stdout.split()

    def list_packages(self, third_party_only: bool = False) -> list[str]:
        """Pacotes instalados. Levanta `AdbError` quando o `pm` não está no ar — nunca devolve lista vazia por isso.

        Com o `system_server` morto o `pm` responde `cmd: Can't find service: package` e saía daqui uma lista
        vazia, que a API repassava como HTTP 200 `{"packages": []}` — "este aparelho não tem nada instalado", dito
        com a mesma cara de um sucesso. Medido em 21/09/2026 no android-09.

        Lista vazia SÓ é verdade em `-3` (aparelho recém-criado, nenhum app de terceiro): a lista completa de um
        Android vivo tem centenas de pacotes do sistema, então vazia ali é sempre o `pm` fora do ar.
        """
        flag = " -3" if third_party_only else ""
        res = self._run(["shell", f"pm list packages{flag}"], timeout=30)
        out, err = res.stdout or "", res.stderr or ""
        pacotes = sorted(ln.split(":", 1)[1].strip() for ln in out.splitlines() if ln.startswith("package:"))
        if pacotes:
            return pacotes
        junto = (out + err).strip()
        if res.returncode != 0 or "Can't find service" in junto or "Failure" in junto or not third_party_only:
            raise AdbError(f"não foi possível listar os pacotes de {self.serial}"
                           + (f": {junto.splitlines()[0][:160]}" if junto else " (o `pm` não respondeu)"))
        return pacotes

    def start_app(self, package: str, activity: str | None = None) -> None:
        _check_package(package)
        if activity:
            if not ACTIVITY_RE.match(activity):
                raise AdbError("activity inválida")
            comp = f"{package}/{activity}"
            out = self.shell(f"am start -n {comp}", timeout=30)
        else:
            out = self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1", timeout=30)
        if "Error" in out or "No activities found" in out:
            raise AdbError(f"Não foi possível abrir {package}: {out.strip()[:200]}")

    def app_version(self, package: str) -> str:
        """versionName(versionCode) do pacote instalado — chave das receitas aprendidas para este app."""
        _check_package(package)
        out = self._run(["shell", f"dumpsys package {package} | grep -E 'versionName|versionCode' | head -n 2"], timeout=20).stdout
        name = re.search(r"versionName=(\S+)", out)
        code = re.search(r"versionCode=(\d+)", out)
        if not (name or code):
            raise AdbError(f"{package} não está instalado")
        return f"{name.group(1) if name else '?'}({code.group(1) if code else '?'})"

    def connect(self) -> str:
        """`adb connect host:porta` para aparelhos externos por rede. Serial USB não precisa."""
        if not re.match(r"^[A-Za-z0-9_.\-]+:\d{2,5}$", self.serial):
            return "serial USB"
        res = subprocess.run([str(self.tools.adb), "connect", self.serial], capture_output=True, text=True, timeout=20,
                             env=self.tools.env(), creationflags=NO_WINDOW)
        return ((res.stdout or "") + (res.stderr or "")).strip()[:160]

    def force_stop(self, package: str) -> None:
        _check_package(package)
        self.shell(f"am force-stop {package}", timeout=20)

    def remove_forward(self, port: int) -> None:
        """Remove um `adb forward` antigo desta porta (fica preso quando o Appium morre sem fechar a sessão).
        A porta é exclusiva desta instância, então não há outro dono legítimo; ausência do forward não é erro."""
        self._run(["forward", "--remove", f"tcp:{int(port)}"], timeout=10)

    def abi_list(self) -> str:
        return self._run(["shell", "getprop ro.product.cpu.abilist"], timeout=8).stdout.strip()

    def emu_kill(self) -> None:
        self._run(["emu", "kill"], timeout=15)

    def snapshot_save(self, name: str, *, timeout: float = 300) -> None:
        """Snapshot explícito pelo console do emulador. Só vale se o console responder OK."""
        self._run(["shell", "sync"], timeout=30)
        res = self._run(["emu", "avd", "snapshot", "save", name], timeout=timeout)
        out = ((res.stdout or "") + (res.stderr or "")).strip()
        if res.returncode != 0 or "OK" not in out.upper().split():
            raise AdbError(f"snapshot save falhou: {out[:200] or res.returncode}")

    def clock_skew_s(self) -> int:
        return int(self.shell("date +%s", timeout=10).strip()) - int(time.time())

    def sync_clock(self) -> tuple[int, int]:
        """Depois de acordar de um snapshot o relógio do guest continua no passado (medido: −31 s após 20 s
        hibernado, sem autocorreção). Acerta pelo host. Devolve (desvio_antes, desvio_depois) em segundos."""
        before = self.clock_skew_s()
        if abs(before) <= 2:
            return before, before
        self._run(["shell", f"cmd alarm set-time {int(time.time() * 1000)}"], timeout=10)
        after = self.clock_skew_s()
        if abs(after) > 2:                                  # imagens sem `cmd alarm set-time`: via root (google_apis permite)
            self._run(["root"], timeout=15)
            self._run(["wait-for-device"], timeout=20)
            now = time.localtime()
            self._run(["shell", time.strftime("date %m%d%H%M%Y.%S", now)], timeout=10)
            after = self.clock_skew_s()
        return before, after


def _check_package(package: str) -> None:
    if not PACKAGE_RE.match(package):
        raise AdbError("package name inválido")


def _explain_install_failure(out: str) -> str:
    hints = {
        "INSTALL_FAILED_NO_MATCHING_ABIS": "o APK não contém bibliotecas nativas para x86_64 (a imagem do emulador). "
                                           "Use um APK universal/x86_64 ou uma imagem compatível.",
        "INSTALL_FAILED_OLDER_SDK": "o APK exige uma versão de Android mais nova que a da imagem.",
        "INSTALL_FAILED_MISSING_SHARED_LIBRARY": "o APK depende de bibliotecas ausentes (ex.: Google Play Services). "
                                                 "Use uma imagem google_apis/google_apis_playstore.",
        "INSTALL_FAILED_INSUFFICIENT_STORAGE": "sem espaço na partição de dados do AVD.",
        "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "já existe uma versão com assinatura diferente; desinstale antes.",
        "INSTALL_FAILED_VERSION_DOWNGRADE": "o aparelho recusou instalar por cima uma versão mais antiga. "
                                            "Voltar exige desinstalar antes, o que apaga os dados do app "
                                            "— inclusive a sessão.",
    }
    for code, hint in hints.items():
        if code in out:
            return f"{code}: {hint}"
    tail = out.strip().splitlines()[-1] if out.strip() else "sem saída"
    return f"Instalação falhou: {tail[:300]}"

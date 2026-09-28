"""Servidor Appium local (um processo, N sessões), preso a 127.0.0.1. O backend só encerra o
servidor que ele mesmo iniciou; se já houver um Appium respondendo na porta, ele é reutilizado."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil

from ..config import Config
from ..devices.sdk import NEW_GROUP, NO_WINDOW, SdkTools

log = logging.getLogger("poc.appium")


# Mascaramento aplicado pelo PRÓPRIO Appium, antes de escrever qualquer linha. É a defesa na origem: redigir
# depois que o processo já gravou a credencial no arquivo seria tarde. Vale para TODA digitação, não só para senha —
# perde-se depuração de texto digitado e ganha-se a garantia de que nada sensível é escrito por descuido.
LOG_FILTER_RULES: list[dict[str, str]] = [
    {  # {"script":"mobile: type","args":[{"text":"…"}]}
        "pattern": r'("script"\s*:\s*"mobile:\s*type"\s*,\s*"args"\s*:\s*\[\s*\{[^}]*?"text"\s*:\s*")[^"]*',
        "flags": "g",
        "replacer": "$1**SECURE**",
    },
    {  # envio de teclas do WebDriver: {"text":"…","value":[…]}
        "pattern": r'("text"\s*:\s*")[^"]*("\s*,\s*"value"\s*:\s*\[)[^\]]*(\])',
        "flags": "g",
        "replacer": '$1**SECURE**$2"**SECURE**"$3',
    },
    {  # {"script":"mobile: replaceElementValue","args":[{"elementId":"…","text":"…"}]} — a ferramenta `type_text`
        # define o texto por aqui desde r-20260928165254-e31953, e o corpo da requisição sai no log em nível info.
        # O valor para na aspa NÃO escapada: `[^"]*` pararia em `\"` e deixaria o resto do texto em claro.
        "pattern": r'("script"\s*:\s*"mobile:\s*replaceElementValue"\s*,\s*"args"\s*:\s*\[\s*\{[^}]*?"text"\s*:\s*")'
                   r'(?:[^"\\]|\\.)*',
        "flags": "g",
        "replacer": "$1**SECURE**",
    },
]
LOADED_RULES_MARKER = "filtering rule"     # o Appium registra "Loaded N filtering rule(s)" quando aceita as regras


class AppiumServer:
    def __init__(self, cfg: Config, tools: SdkTools):
        self.cfg = cfg
        self.tools = tools
        self.pid: int | None = None
        self.detail: str | None = None
        # Só vira True quando ESTE backend subiu o servidor com as regras e viu a confirmação no log.
        # Servidor reutilizado de fora conta como não comprovado: o canal sensível se recusa a operar.
        self.log_masking_active: bool = False

    @property
    def url(self) -> str:
        a = self.cfg.file.appium
        return f"http://{a.host}:{a.port}"

    def is_up(self, timeout: float = 2.0) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/status", timeout=timeout) as resp:  # noqa: S310 - loopback
                return bool(json.loads(resp.read()).get("value", {}).get("ready", False))
        except (urllib.error.URLError, OSError, ValueError):
            return False

    @property
    def _pid_file(self) -> Path:
        return self.cfg.data_dir / "appium.pid"

    def _own_orphan(self) -> int | None:
        """Appium deixado por um backend anterior deste projeto que morreu sem desligar: só é readotado se o
        PID gravado ainda existir E a linha de comando apontar para o Appium de tools/appium."""
        try:
            pid = int(self._pid_file.read_text(encoding="ascii").strip())
            cmd = " ".join(psutil.Process(pid).cmdline()).lower()
        except (OSError, ValueError, psutil.Error):
            return None
        entry = str(self.cfg.path(self.cfg.file.appium.dir) / "node_modules" / "appium").lower()
        return pid if entry in cmd else None

    def start(self, wait_s: float = 60) -> bool:
        if self.is_up():
            self.pid = self._own_orphan()
            if self.pid:
                self.log_masking_active = self._prove_masking(self.pid)
                self.detail = (f"readotado: iniciado por este projeto (pid {self.pid}) — "
                               + ("mascaramento comprovado pela linha de comando e pelas regras em disco"
                                  if self.log_masking_active else
                                  "mascaramento de log não comprovado nesta sessão"))
            else:
                self.log_masking_active = False
                self.detail = ("reutilizando servidor externo já em execução (não será encerrado por este "
                               "projeto) — mascaramento de log não comprovado nesta sessão")
            return True
        a = self.cfg.file.appium
        appium_dir = self.cfg.path(a.dir)
        entry = appium_dir / "node_modules" / "appium" / "index.js"
        node = shutil.which("node")
        if not node or not entry.exists():
            self.detail = (f"Appium não instalado em {appium_dir}. Rode scripts/install-prereqs.ps1 "
                           "(ou `npm ci` em tools/appium).")
            return False
        self.cfg.logs_dir.mkdir(parents=True, exist_ok=True)
        env = self.tools.env()
        env["APPIUM_HOME"] = str(appium_dir)
        filters_path = self._write_log_filters()
        log_path = self._rotate_log()
        offset = log_path.stat().st_size if log_path.exists() else 0
        logf = open(log_path, "ab", buffering=0)  # noqa: SIM115
        try:
            proc = subprocess.Popen(
                [node, str(entry), "server", "--address", a.host, "--port", str(a.port),
                 "--log-level", "info", "--log-timestamp", "--local-timezone",
                 "--log-filters", str(filters_path)],
                cwd=str(appium_dir), env=env, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=NO_WINDOW | NEW_GROUP)
        finally:
            logf.close()
        self.pid = proc.pid
        self._pid_file.write_text(str(proc.pid), encoding="ascii")
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self.is_up():
                self.log_masking_active = self._confirm_masking(log_path, offset)
                self.detail = f"iniciado por este projeto (pid {self.pid})"
                if not self.log_masking_active:
                    self.detail += " — ATENÇÃO: mascaramento de log não confirmado"
                return True
            if proc.poll() is not None:
                self.detail = f"Appium encerrou ao iniciar (código {proc.returncode}); veja data/logs/appium.log"
                self.pid = None
                return False
            time.sleep(1)
        self.detail = "Appium não respondeu a tempo; veja data/logs/appium.log"
        return False

    # ------------------------------------------------------------------ mascaramento de log
    def _write_log_filters(self) -> Path:
        """Grava o arquivo de regras. Regra inválida faz o Appium RECUSAR subir (ele lança na inicialização),
        então um arquivo malformado vira falha visível, nunca silêncio."""
        path = self.cfg.data_dir / "appium-log-filters.json"
        path.write_text(json.dumps(LOG_FILTER_RULES, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _rotate_log(self) -> Path:
        """O log do Appium era aberto em modo append e crescia para sempre, fora de qualquer retenção."""
        path = self.cfg.logs_dir / "appium.log"
        try:
            if path.exists() and path.stat().st_size > 8 * 1024 * 1024:
                previous = path.with_suffix(".log.1")
                previous.unlink(missing_ok=True)
                os.replace(path, previous)
        except OSError:
            log.warning("não foi possível rotacionar appium.log")
        return path

    def _prove_masking(self, pid: int) -> bool:
        """Comprova o mascaramento de um Appium READOTADO (órfão do próprio projeto), sem reiniciá-lo.

        Não dá para usar `_confirm_masking`: não há offset confiável no log de um processo que este backend não
        acabou de iniciar, e o arquivo pode já ter rotacionado. A prova que sobra é suficiente: o cmdline do PID
        tem `--log-filters` apontando para o arquivo esperado, E o conteúdo do arquivo é exatamente
        `LOG_FILTER_RULES` — o Appium recusa subir com regra inválida (`_write_log_filters`), então um arquivo
        com o conteúdo certo, associado a um processo vivo com essa flag, é prova de que ELE subiu com elas.
        """
        expected = str((self.cfg.data_dir / "appium-log-filters.json").resolve())
        try:
            cmdline = psutil.Process(pid).cmdline()
        except psutil.Error:
            return False
        try:
            idx = cmdline.index("--log-filters")
            given = str(Path(cmdline[idx + 1]).resolve())
        except (ValueError, IndexError):
            return False
        if os.path.normcase(given) != os.path.normcase(expected):
            return False
        try:
            on_disk = json.loads(Path(expected).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return on_disk == LOG_FILTER_RULES

    def _confirm_masking(self, log_path: Path, offset: int) -> bool:
        """Confirma no próprio log que o Appium aceitou as regras ("Loaded N filtering rule(s)")."""
        try:
            with open(log_path, "rb") as fh:
                fh.seek(offset)
                return LOADED_RULES_MARKER in fh.read().decode("utf-8", "replace")
        except OSError:
            return False

    def stop(self) -> None:
        """Encerra apenas o processo que este backend iniciou."""
        pid, self.pid = self.pid, None
        self.log_masking_active = False
        if not pid:
            return
        try:
            parent = psutil.Process(pid)
            for ch in parent.children(recursive=True):
                ch.terminate()
            parent.terminate()
            psutil.wait_procs([parent], timeout=8)
        except psutil.NoSuchProcess:
            pass
        self._pid_file.unlink(missing_ok=True)

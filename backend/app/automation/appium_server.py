"""Servidor Appium local (um processo, N sessões), preso a 127.0.0.1. O backend só encerra o
servidor que ele mesmo iniciou, ou o órfão de um backend anterior DESTE projeto que ele não consegue provar
mascarado; outro Appium respondendo na porta é reutilizado."""
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
from ..supervisor import e_emulador

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
#: 29.132: a linha em que o Appium diz que LIGOU a porta. Quem responde na porta pode ser outro servidor (o anterior,
#: que o `is_up` de 2 s não viu com a máquina saturada); só esta linha, no log deste processo, diz que é ele.
LISTENER_MARKER = "listener started on"


def _spared(proc: psutil.Process) -> bool:
    """Filho do órfão que fica: emulador, ou processo cujo nome não dá para ler (não se encerra o que não se sabe)."""
    try:
        return e_emulador(proc.name())
    except psutil.Error:
        return True


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

    @property
    def _package(self) -> Path:
        """`<appium.dir>/node_modules/appium`: a linha de comando que aponta para cá é a do Appium DESTE projeto."""
        return self.cfg.path(self.cfg.file.appium.dir) / "node_modules" / "appium"

    def _is_ours(self, proc: psutil.Process) -> bool:
        return str(self._package).lower() in " ".join(proc.cmdline()).lower()

    def _dono_da_porta(self) -> int | None:
        """O PID que escuta na porta do Appium, ou `None` (ninguém, ou o sistema não deixa ver)."""
        porta = self.cfg.file.appium.port
        try:
            for c in psutil.net_connections(kind="tcp"):
                if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == porta and c.pid:
                    return int(c.pid)
        except (psutil.Error, OSError):
            return None
        return None

    def _own_orphan(self) -> int | None:
        """Appium deixado por um backend anterior deste projeto que morreu sem desligar: o processo cuja linha de
        comando aponta para o Appium de tools/appium.

        29.132: primeiro o DONO DA PORTA, depois o PID gravado. Só o arquivo não bastava: em 05/10 13:10Z um start
        sobrescreveu o `appium.pid` com o PID de um Appium que morreu sem ligar a porta, e o que seguia respondendo
        (nosso, com as regras) passou a ser tratado como "servidor externo", sem mascaramento comprovado."""
        candidatos: list[int] = []
        dono = self._dono_da_porta()
        if dono is not None:
            candidatos.append(dono)
        try:
            gravado = int(self._pid_file.read_text(encoding="ascii").strip())
            if gravado not in candidatos:
                candidatos.append(gravado)
        except (OSError, ValueError):
            pass
        for pid in candidatos:
            try:
                if self._is_ours(psutil.Process(pid)):
                    return pid
            except psutil.Error:
                continue
        return None

    def start(self, wait_s: float = 60) -> bool:
        """Reaproveita o que responde na porta (`_reuse_running`) ou sobe um Appium com as regras.

        29.132: se o novo morre sem ligar a porta e alguém responde nela, quem responde é o anterior; a decisão
        volta ao `_reuse_running` (uma vez) em vez de o novo ser dado como "subiu"."""
        for _ in range(2):
            if self.is_up() and self._reuse_running():
                return True
            subiu = self._subir(wait_s)
            if subiu is not None:
                return subiu
        self.detail = ("o Appium novo não ligou a porta e o servidor que responde nela não foi reaproveitado; veja "
                       "data/logs/appium.log")
        return False

    def _subir(self, wait_s: float) -> bool | None:
        """`True` subiu (este processo ligou a porta), `False` falhou, `None` morreu sem ligar e outro responde."""
        a = self.cfg.file.appium
        appium_dir = self.cfg.path(a.dir)
        entry = self._package / "index.js"
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
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            # 29.132: "responde na porta" não diz QUEM responde. Só conta a linha do próprio log deste processo; o
            # `appium.pid` só é gravado aqui, para não apontar para um processo que nunca ligou a porta.
            texto = self._log_desde(log_path, offset)
            if LISTENER_MARKER in texto and self.is_up():
                self._pid_file.write_text(str(proc.pid), encoding="ascii")
                self.log_masking_active = LOADED_RULES_MARKER in texto
                self.detail = f"iniciado por este projeto (pid {self.pid})"
                if not self.log_masking_active:
                    self.detail += " — ATENÇÃO: mascaramento de log não confirmado"
                return True
            if proc.poll() is not None:
                self.pid = None
                if self.is_up():
                    log.warning("o Appium novo (pid %s) saiu sem ligar a porta %s, e outro servidor responde nela: "
                                "a decisão volta ao reaproveitamento", proc.pid, a.port)
                    return None
                self.detail = f"Appium encerrou ao iniciar (código {proc.returncode}); veja data/logs/appium.log"
                return False
            time.sleep(1)
        self.detail = "Appium não respondeu a tempo; veja data/logs/appium.log"
        return False

    # ------------------------------------------------------------------ o que já responde na porta
    def _reuse_running(self) -> bool:
        """Já há um Appium respondendo na porta: reutilizá-lo (True) ou liberar a porta para `start` subir outro.

        O órfão DESTE projeto sem mascaramento comprovado é trocado, não readotado (K-039 fora do deploy). Um backend
        que morre, sozinho (crash, Windows Update) ou pelo supervisor (que só mata o backend, 29.125), deixa na porta
        o Appium que ele subiu, e readotá-lo sem prova subia o backend seguinte `degraded`, com o preenchimento
        de credencial bloqueado. Trocar aqui, e não no supervisor, cobre todo caminho até esta subida (supervisor,
        `start.ps1`, `deploy.ps1` cujo `stop.ps1` não leu a linha de comando). E é seguro por construção: `main()`
        liga a porta da Farm ANTES do lifespan, então nenhum outro backend desta árvore está vivo usando esse Appium.
        """
        orphan = self._own_orphan()
        if orphan is None:
            # Não é deste projeto (outro `node`, outra árvore, PID gravado que não confere): nunca se encerra aqui.
            self.pid = None
            self.log_masking_active = False
            self.detail = ("reutilizando servidor externo já em execução (não será encerrado por este "
                           "projeto) — mascaramento de log não comprovado nesta sessão")
            return True
        if self._prove_masking(orphan):
            self.pid = orphan
            self.log_masking_active = True
            self.detail = (f"readotado: iniciado por este projeto (pid {orphan}) — mascaramento comprovado pela "
                           "linha de comando e pelas regras em disco")
            return True
        why = self._kill_orphan(orphan)
        if why is None and not self.is_up():
            log.warning("Appium órfão deste projeto (pid %s) sem mascaramento comprovado: encerrado; subindo outro "
                        "com as regras (K-039)", orphan)
            return False
        self.log_masking_active = False
        if why is None:
            self.pid = None
            self.detail = (f"Appium órfão deste projeto (pid {orphan}) encerrado, mas outro servidor responde na "
                           "porta — reutilizando-o; mascaramento de log não comprovado nesta sessão")
        else:
            self.pid = orphan
            self.detail = (f"readotado: iniciado por este projeto (pid {orphan}) — mascaramento de log não "
                           f"comprovado nesta sessão; não foi trocado: {why}")
        return True

    def _kill_orphan(self, pid: int, timeout_s: float = 10.0) -> str | None:
        """Encerra o órfão deste projeto e confere que ele saiu. `None` = saiu; texto = por que ficou.

        Três travas antes do tiro. Há Appium instalado para subir outro: trocar um servidor degradado por nenhum
        derrubaria a automação inteira. A linha de comando é conferida de novo no MESMO objeto que vai ser
        encerrado, porque entre `_own_orphan` e aqui o PID pode ter sido reciclado. E filho que for emulador fica,
        pelo mesmo critério do supervisor (o backend readota emulador vivo pelo PID).
        """
        if not shutil.which("node") or not (self._package / "index.js").exists():
            return "não há Appium instalado para subir outro no lugar"
        try:
            proc = psutil.Process(pid)
            if not self._is_ours(proc):
                return "o PID gravado já não é o Appium deste projeto"
            targets = [ch for ch in proc.children(recursive=True) if not _spared(ch)] + [proc]
        except psutil.NoSuchProcess:
            self._pid_file.unlink(missing_ok=True)       # saiu sozinho entre a conferência e aqui
            return None
        except psutil.Error as exc:
            return f"sem acesso ao processo ({type(exc).__name__})"
        for p in targets:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as exc:
                if p is proc:
                    return f"sem permissão para encerrá-lo ({type(exc).__name__})"
        _, alive = psutil.wait_procs([proc], timeout=timeout_s)
        if alive:
            return f"não saiu em {timeout_s:.0f} s"
        self._pid_file.unlink(missing_ok=True)
        return None

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
        return LOADED_RULES_MARKER in self._log_desde(log_path, offset)

    @staticmethod
    def _log_desde(log_path: Path, offset: int) -> str:
        """O que este processo escreveu no log desde que subiu (o arquivo é dele: `_rotate_log` + append)."""
        try:
            with open(log_path, "rb") as fh:
                fh.seek(offset)
                return fh.read().decode("utf-8", "replace")
        except OSError:
            return ""

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

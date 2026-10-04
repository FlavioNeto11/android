"""ADB sempre direcionado a um serial específico (`adb -s <serial> …`). Chamadas bloqueantes:
devem rodar na thread do executor do dispositivo, nunca no event loop."""
from __future__ import annotations

import logging
import re
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from ..security.redaction import redact
from ..util import url_abrivel
from . import apps_de_fundo
from .apps_de_fundo import AjusteDosApps
from .sdk import NO_WINDOW, SdkTools
# `sonda_rede`, e não `conectividade`: esta importa `models`, que não vai para o agente do worker.
from .sonda_rede import comando_sonda, ler_sonda

#: Prazo do `shell` dos ajustes de sempre do preparo (`Adb.prepare_for_automation`), com a máquina sem carga. O prazo do
#: preparo inteiro, no executor, é `PRAZO_DO_PREPARO_S` (60 s) — 40 + 12 dos apps de fundo fica abaixo de 60.
PRAZO_DO_AJUSTE_S = 40.0
#: Acima desta CPU da máquina, o preparo ganha tempo: os `settings put`/`svc`/`wm` são processos do convidado, e com o
#: host saturado (vários boots em voo) um preparo SADIO passa de 60 s — era o degrau de reparo por prazo (29.33, RA-4).
CPU_DO_PREPARO_ESCALA_DESDE = 50.0
#: Teto do fator: 100 % de CPU triplica o prazo (60 → 180 s). Passou disso, o aparelho está de fato sem resposta.
FATOR_MAXIMO_DO_PREPARO = 3.0


def fator_de_carga_do_preparo(cpu_percent: float | None) -> float:
    """Quanto esticar o prazo do preparo pela CPU da máquina: 1 até `CPU_DO_PREPARO_ESCALA_DESDE`, linear até
    `FATOR_MAXIMO_DO_PREPARO` em 100 %. CPU desconhecida (`None`) não estica: "não sei" não é "carregada", e o
    prazo de sempre é o que valia antes."""
    if cpu_percent is None or cpu_percent <= CPU_DO_PREPARO_ESCALA_DESDE:
        return 1.0
    fracao = min(1.0, (cpu_percent - CPU_DO_PREPARO_ESCALA_DESDE) / (100.0 - CPU_DO_PREPARO_ESCALA_DESDE))
    return 1.0 + fracao * (FATOR_MAXIMO_DO_PREPARO - 1.0)


PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
ACTIVITY_RE = re.compile(r"^[A-Za-z0-9_.$]+$")
# O que `pm path` devolve: /data/app/~~<aleatório>==/<pacote>-<aleatório>==/base.apk (ou split_config.*.apk).
REMOTE_APK_RE = re.compile(r"^/data/app/[A-Za-z0-9_.=~/\-]+\.apk$")
#: Onde um arquivo privado (hoje, só o segredo de rede do ADR-056 §5) pousa no convidado. Pasta ÚNICA e fixa: o
#: `rm` da limpeza nunca recebe caminho de fora, e quem procura o que sobrou num aparelho sabe onde olhar. O nome
#: segue a mesma regra de "nada de shell" dos outros valores montados em comando. O 25.4 estende se o cliente VPN
#: só ler de outro lugar — e, se estender, estende AQUI, não num `shell` montado por quem chama.
DIR_PRIVADO_NO_CONVIDADO = "/data/local/tmp/rede"
NOME_PRIVADO_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,59}$")
#: Galeria do aparelho onde a mídia de uma publicação própria é colocada (29.30), e a regra do nome do arquivo.
DIR_DA_GALERIA = "/sdcard/Pictures/Central"
NOME_DE_MIDIA_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,59}$")

KEYCODES = {"back": 4, "home": 3, "recents": 187, "enter": 66, "delete": 67, "wakeup": 224, "menu": 82}

log = logging.getLogger("poc.devices.adb")


class AdbError(RuntimeError):
    pass


class AdbTimeout(AdbError):
    """O comando de ADB estourou o prazo — o que NÃO é o mesmo que ter falhado.

    Um `adb install` ou um `am start` que estoura o prazo num aparelho remoto lento continua correndo no
    aparelho: o `pm install` termina depois, e quem reinstalasse por cima estaria repetindo um efeito que deu
    certo. Por isso é uma subclasse de `AdbError` (ninguém que já tratava erro de adb deixa de tratar) com
    identidade própria: quem decide desfecho de comando a traduz para `uncertain`, e não para `failed`.
    """


@dataclass(frozen=True, slots=True)
class MorteDoApp:
    """Uma morte do processo PRINCIPAL de um app, lida de `dumpsys activity exit-info` (ver `Adb.app_deaths`)."""

    quando: str          # horário no relógio do CONVIDADO, como o `dumpsys` imprime (fuso dele)
    pid: int
    motivo: int          # `ApplicationExitInfo.REASON_*`: 4 crash, 5 crash nativo, 6 ANR
    anr: bool            # reason=6, ou crash (4) com o trace de ANR (`/data/anr/anr_*`)
    idade_s: float       # quanto antes da leitura, medido no relógio do convidado
    descricao: str = ""

    @property
    def chave(self) -> tuple[str, int]:
        """A mesma morte volta em toda leitura seguinte: quem conta mortes não conta a mesma duas vezes."""
        return self.quando, self.pid


#: `ApplicationExitInfo.REASON_*` que são o app MORRENDO: crash (4), crash nativo (5) e ANR (6). Os outros (pedido do
#: usuário, `force-stop`, pouca memória em segundo plano, o próprio app saindo) não são o app travando na frente.
MOTIVOS_DE_MORTE = frozenset({4, 5, 6})
#: Morte com horário depois da hora lida (o `date` roda antes do `dumpsys` e trunca o segundo) é desta mesma leitura;
#: além disto, o relógio ou o fuso do convidado não batem com os do `dumpsys` e a idade não se sabe.
_FOLGA_DA_HORA_S = 5.0
_HORA_DO_CONVIDADO = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
#: O trecho que identifica o aviso de ANR no cartão do aparelho: quem o escreve só ocupa o cartão vazio ou o seu.
AVISO_DE_ANR = "parou de responder (ANR)"


def motivo_de_anr(rotulo: str, aparelho: str) -> str:
    """O motivo da 2ª morte por ANR, igual no executor e no motor de sessão (e no aviso do aparelho)."""
    return f"o {rotulo} {AVISO_DE_ANR} e foi fechado no {aparelho}: convidado sem CPU"


def ler_mortes(saida: str, pacote: str, *, within_s: float | None = None) -> list[MorteDoApp]:
    """As mortes de `pacote` na saída de `date; dumpsys activity exit-info <pacote>`, da mais nova à mais velha.

    A idade é medida contra a PRIMEIRA linha (a hora do convidado, no mesmo fuso em que o `dumpsys` imprime), e nunca
    contra o relógio daqui: depois de acordar de um snapshot o convidado fica 26–31 s atrás do host
    (`relatorio-validacao.md` §7.3), e a janela "desde o início da etapa" erraria justo as mortes que importam.
    Sem essa linha a idade de nada se sabe: levanta `AdbError` — "não sei" nunca é "nenhuma morte".
    """
    hora = next((ln.strip() for ln in saida.splitlines() if _HORA_DO_CONVIDADO.match(ln.strip())), None)
    agora = _instante(hora) if hora else None
    if agora is None:
        raise AdbError("o exit-info veio sem a hora do aparelho: não dá para saber quando o app morreu")
    mortes: list[MorteDoApp] = []
    for bloco in re.split(r"ApplicationExitInfo #\d+:", saida)[1:]:
        ts = re.search(r"timestamp=(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)", bloco)
        processo = re.search(r"\bprocess=(\S+)", bloco)
        motivo = re.search(r"\breason=(\d+)", bloco)
        # Só o processo principal: um `:mqtt` ou `:remote` que morre não tira a tela de ninguém.
        if (not (ts and processo and motivo) or processo.group(1) != pacote
                or int(motivo.group(1)) not in MOTIVOS_DE_MORTE):
            continue
        quando = ts.group(1)
        instante = _instante(quando)
        if instante is None:
            continue
        idade = (agora - instante).total_seconds()
        if within_s is not None and not (-_FOLGA_DA_HORA_S <= idade <= within_s):
            continue
        codigo = int(motivo.group(1))
        pid = re.search(r"\bpid=(\d+)", bloco)
        trace = re.search(r"\btrace=(\S+)", bloco)
        descricao = re.search(r"\bdescription=(.*?)(?=\s+state=|\s+trace=|$)", bloco, re.M)
        mortes.append(MorteDoApp(quando=quando, pid=int(pid.group(1)) if pid else 0, motivo=codigo,
                                 anr=codigo == 6 or (codigo == 4 and bool(trace) and "anr_" in trace.group(1)),
                                 idade_s=idade, descricao=(descricao.group(1).strip() if descricao else "")[:160]))
    return mortes


def _instante(texto: str) -> datetime | None:
    try:
        return datetime.strptime(texto, "%Y-%m-%d %H:%M:%S.%f" if "." in texto else "%Y-%m-%d %H:%M:%S")
    except ValueError:                      # data impossível (mês 13…): não é hora que se possa comparar
        return None


def _ultima(saida: str, chave: str) -> str | None:
    """O resto da ÚLTIMA linha com `chave=` na saída de um `dumpsys window`, ou None.

    A última, e não a primeira: no Android 14 o `dumpsys window` abre com `WINDOW MANAGER LAST ANR`, a fotografia
    CONGELADA do último ANR — com o `mCurrentFocus` daquele instante e até uma cópia das "display contents" (depois de
    "Last ANR continued"). O foco de agora só aparece depois, na seção viva. Ler a primeira ocorrência "comprovava" o
    Instagram na frente com o launcher na tela (execuções r-20260928165254-e31953 e r-20260928195344-02ee9e).
    Delimitar a seção pelo cabeçalho seguinte não serve: a cópia congelada tem cabeçalho de "display contents" também.
    """
    achados = re.findall(rf"{chave}=([^\r\n]*)", saida)
    return achados[-1].strip() if achados else None


class Adb:
    def __init__(self, tools: SdkTools, serial: str, *, apps_de_fundo: Sequence[str] | None = None):
        self.tools = tools
        self.serial = serial
        #: Apps de fundo que `prepare_for_automation` mantém desativados (`devices/apps_de_fundo.py`). `None` = este
        #: preparo não toca em app nenhum (nem lê): é o padrão, e o do agente do worker — quem decide a lista de um
        #: aparelho do worker é o central, que o prepara pelo túnel. Uma tupla, mesmo vazia, é "aparelho da
        #: automação": desativa a lista e reativa o que saiu dela. Quem chama pode trocá-la antes de cada preparo.
        self.apps_de_fundo: tuple[str, ...] | None = None if apps_de_fundo is None else tuple(apps_de_fundo)
        #: Prazo do `shell` dos ajustes de sempre do preparo. Quem chama o troca antes de cada preparo, com o fator
        #: da carga (`fator_de_carga_do_preparo`), junto do prazo do executor: o interno que estourasse primeiro
        #: continuaria sendo o degrau de reparo, só que escondido atrás do prazo novo.
        self.prazo_do_ajuste_s: float = PRAZO_DO_AJUSTE_S

    # -- base -----------------------------------------------------------------
    def _run(self, args: list[str], *, timeout: float = 30, binary: bool = False,
             entrada: str | bytes | None = None) -> subprocess.CompletedProcess:
        """`entrada` vai pelo STDIN do processo. Existe por um motivo de seguranca, nao de conveniencia: o que
        entra por `args` vira linha de comando do `adb.exe` DESTA maquina, legivel por qualquer processo local que
        saiba ler argv, enquanto o processo roda. Ver `input_text_ascii`. Com `binary`, `entrada` vai em bytes:
        em modo texto o Windows troca `\\n` por `\\r\\n` no stdin, e um arquivo de configuracao chegaria alterado."""
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

    def uptime_s(self) -> float | None:
        """Segundos desde o boot do convidado (`/proc/uptime`); `None` se não deu para ler. Num wake, o snapshot
        carregado traz o uptime de antes da hibernação (29.34): é o sinal de "carregou" quando o log do emulador
        ainda não chegou ao arquivo (a saída dele é bufferizada e a linha só aparece depois do boot)."""
        res = self._run(["shell", "cat /proc/uptime"], timeout=8)
        if res.returncode != 0:
            return None
        try:
            return float(res.stdout.split()[0])
        except (IndexError, ValueError):
            return None

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
        # `head -1 /proc/stat` (a linha `cpu` agregada) vem na MESMA chamada: quem compara duas leituras sabe que
        # fração do tempo o convidado passou em interrupção (irq + softirq). Medido em 28/09: ocioso, 21% no
        # android-06 (68 h no ar) e 90% no android-04 (44 h), contra 0% no android-01 (7,7 h) e ~2% depois do
        # reinício a frio — o substrato das falhas r-20260928165254-e31953 e r-20260928195344-02ee9e.
        res = self._run(["shell", "cat /proc/loadavg; grep -E 'MemTotal|MemAvailable' /proc/meminfo; nproc; "
                                  "head -1 /proc/stat"], timeout=timeout)
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
            elif partes[0] == "cpu" and len(partes) >= 8 and all(x.isdigit() for x in partes[1:8]):
                # user nice system idle iowait irq softirq [steal...]: o total e a parte em interrupção.
                ticks = [float(x) for x in partes[1:] if x.isdigit()]
                dados["cpu_total_ticks"] = sum(ticks[:8])
                dados["cpu_irq_ticks"] = ticks[5] + ticks[6]
        dados.setdefault("ncpu", 1.0)
        return dados

    def connectivity_probe(self, *, timeout: float = 60) -> dict[str, bool]:
        """Internet DENTRO do convidado (rota, DNS, TCP 443, rede validada) — ver `devices/conectividade.py`.

        Levanta `AdbError` quando não dá para saber: adb mudo não é "sem internet"."""
        res =self._run(["shell", comando_sonda()], timeout=timeout)
        try:
            return ler_sonda(res.stdout or "")
        except ValueError as exc:
            raise AdbError(str(exc)) from exc

    def ui_ready(self) -> bool:
        """Launcher no ar (não FallbackHome) e sem keyguard — antes disso o screenshot sai preto. Foco e keyguard da
        seção VIVA (`_ultima`): um ANR no boot deixava a cópia congelada dizendo FallbackHome para sempre."""
        out = self._run(["shell", "dumpsys window | grep -E 'mCurrentFocus|isKeyguardShowing'"], timeout=10).stdout
        foco = _ultima(out, "mCurrentFocus")
        if foco is None or foco == "null" or "FallbackHome" in foco:
            return False
        return not (_ultima(out, "isKeyguardShowing") or "").startswith("true")

    def current_focus(self) -> tuple[str | None, str | None]:
        """(pacote, atividade) da janela em foco AGORA — a última linha, nunca a cópia do último ANR (`_ultima`)."""
        out = self._run(["shell", "dumpsys window | grep -E 'mCurrentFocus' | tail -n 1"], timeout=10).stdout
        m = re.match(r"Window\{[^ ]+ [^ ]+ ([^/\s}]+)/([^\s}]+)\}", _ultima(out, "mCurrentFocus") or "")
        if m:
            return m.group(1), m.group(2)
        return None, None

    def system_dialog(self) -> str | None:
        """Descrição da janela de SISTEMA em foco (ANR, "parou de funcionar"), ou None.

        `current_focus` não enxerga essas janelas: o título delas não tem a forma `pacote/atividade`, então a
        regex de lá falha e ela devolve `(None, None)` — que quem lê vira "nenhuma janela". Foi exatamente isso
        que escondeu um ANR do SystemUI travando a entrega de um app (medido em 19/09/2026). Também da seção viva
        (`_ultima`): a cópia congelada do último ANR mostraria um diálogo que já não existe.
        """
        out = self._run(["shell", "dumpsys window | grep -E 'mCurrentFocus' | tail -n 1"], timeout=10).stdout
        m = re.match(r"Window\{\S+ \S+ ([^}]+)\}", _ultima(out, "mCurrentFocus") or "")
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

    #: O que a descrição do diálogo (texto que vem do APARELHO) pode ter para entrar no shell da confirmação. Fora
    #: disto não se toca: o comando é montado só de constantes e valores validados, nunca de texto livre.
    _DESCRICAO_DIALOGO = re.compile(r"^[\w .:\-]{1,120}$")
    _TOQUE_CONFIRMADO = "toque-no-dialogo-confirmado"

    def dismiss_system_dialog(self, *, timeout: float = 25) -> str | None:
        """Dispensa o diálogo de sistema em foco tocando no botão. Devolve o rótulo tocado, ou None.

        `settings put global hide_error_dialogs 1` (em `prepare_for_automation`) impede diálogos FUTUROS e não
        remove um que já está na tela — por isso este toque existe.

        O toque NÃO é idempotente: se o `adb` estourar o prazo, a injeção pode cair depois, na tela que estiver
        aberta (K-031). Por isso ele nunca roda no portão de prontidão, e a confirmação de que o MESMO diálogo segue
        em foco vai na MESMA chamada do `adb shell` do toque: o convidado confere o foco e só então toca. Isso
        estreita a janela (o dump pode levar segundos, e antes o toque saía numa chamada separada, sem conferir
        nada); não a elimina — uma injeção já entregue ao `system_server` ainda pode ser aplicada atrasada.
        """
        descricao = self.system_dialog()
        if not descricao or not self._DESCRICAO_DIALOGO.match(descricao):
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
                    x, y = (int(x1) + int(x2)) // 2, (int(y1) + int(y2)) // 2
                    # `grep -F`: a descrição é texto fixo (validado acima), não expressão regular. O ` ` e o `}`
                    # ancoram a descrição INTEIRA na linha `mCurrentFocus=Window{<id> <usuário> <descrição>}`. O
                    # `tail -n 1` fica só com o foco de AGORA: a cópia congelada do último ANR, no topo do dump,
                    # confirmaria um diálogo que já saiu e o toque cairia na tela que estiver aberta (`_ultima`).
                    res = self._run(["shell", "dumpsys window | grep -F mCurrentFocus | tail -n 1 | "
                                              f"grep -qF ' {descricao}}}' && input tap {x} {y} && "
                                              f"echo {self._TOQUE_CONFIRMADO}"], timeout=15)
                    return texto.strip() if self._TOQUE_CONFIRMADO in (res.stdout or "") else None
        return None

    def app_deaths(self, package: str, *, within_s: float | None = None, timeout: float = 20) -> list[MorteDoApp]:
        """Mortes do app (crash e ANR) segundo `dumpsys activity exit-info`; com `within_s`, só as dos últimos N
        segundos do relógio do CONVIDADO. Só leitura: pode repetir à vontade.

        É o sinal que faltava. `hide_error_dialogs=1` (preparo) faz todo ANR em primeiro plano virar morte SILENCIOSA
        do app ("user request after error", reason=6): o launcher volta e quem olha a tela vê só "launcher". Nas
        execuções r-20260928165254-e31953 e r-20260928195344-02ee9e (android-06, 2 vCPU saturadas) a IA reabria o
        app, a partida a frio (28–51 s) dava outro ANR, e o laço comia o orçamento. Não usa `logcat -b events`: o
        buffer roda e some, e o `exit-info` guarda horário, motivo e o trace de cada morte.

        A hora do convidado vai na MESMA chamada (`ler_mortes` mede a idade contra ela). Levanta `AdbError` quando não
        dá para saber.
        """
        _check_package(package)
        res = self._run(["shell", f"date '+%Y-%m-%d %H:%M:%S'; dumpsys activity exit-info {package}"],
                        timeout=timeout)
        return ler_mortes(res.stdout or "", package, within_s=within_s)

    def prepare_for_automation(self) -> AjusteDosApps | None:
        """Ajustes IDEMPOTENTES pós-boot: sem animações, tela sempre ligada, sem keyguard, sem diálogos de ANR — e,
        num aparelho da automação (`apps_de_fundo` não `None`), os apps de fundo desativados. Devolve o registro do
        que foi feito com os apps, ou `None` quando não se mexeu neles.

        Roda no portão de prontidão (worker e central). Só entra aqui o que, aplicado atrasado por um `adb` que
        estourou o prazo, repete o que já vale (K-031). Dispensar um diálogo que JÁ está na tela é toque — não
        idempotente — e saiu daqui: roda depois da prontidão (`DeviceManager._arrumar_depois_de_entrar`) e na
        abertura de app pelo instalador (`launch_probe`). `pm disable-user`/`pm enable` são idempotentes."""
        self.shell(
            "settings put global window_animation_scale 0; settings put global transition_animation_scale 0; "
            "settings put global animator_duration_scale 0; settings put system screen_off_timeout 2147483647; "
            "svc power stayon true; settings put global hide_error_dialogs 1; "
            # com teclado físico presente (hw.keyboard=yes) o teclado virtual não cobre botões da tela
            "settings put secure show_ime_with_hard_keyboard 0; "
            "locksettings set-disabled true; input keyevent 224; wm dismiss-keyguard",
            timeout=self.prazo_do_ajuste_s,
        )
        if self.apps_de_fundo is None:
            return None
        return self.ajustar_apps_de_fundo(self.apps_de_fundo)

    def ajustar_apps_de_fundo(self, lista: Sequence[str], *,
                              prazo_s: float = apps_de_fundo.PRAZO_S) -> AjusteDosApps:
        """Desativa (`pm disable-user --user 0`) o que está na `lista` e habilitado, e reativa (`pm enable --user 0`)
        o que ESTE preparo tinha desativado e saiu dela. Nunca levanta: o que não deu vira registro.

        O estouro de prazo AQUI não é o estouro do preparo. No portão, um preparo estourado deixa a tentativa incerta
        porque o efeito atrasado dos ajustes (keyguard, animações) cairia depois das sondas. O efeito atrasado de um
        `pm disable-user` é matar um app de fundo da lista — nunca o alvo, o launcher, o SystemUI nem o teclado, que
        são protegidos (`apps_de_fundo.planejar`) —, então ele vira `incerto` no registro, e o preparo seguinte, que
        lê antes de escrever, termina o que faltou. Os ajustes de sempre, que vêm antes, seguem levantando.

        Chamadas: uma leitura (habilitados, desativados, marcador); a escrita só se algo muda; o marcador só se ele
        muda. Na segunda passagem é só a leitura. Tudo dentro de `prazo_s`.
        """
        fim = time.monotonic() + prazo_s

        def restante() -> float:
            return fim - time.monotonic()

        if restante() < 1.0:
            return self._registrar_apps(AjusteDosApps(incerto="sem tempo no preparo; fica para o próximo"))
        try:
            leitura = self._run(["shell", apps_de_fundo.COMANDO_DE_LEITURA], timeout=min(6.0, restante()))
            estado = apps_de_fundo.ler_estado(leitura.stdout or "")
        except AdbTimeout as exc:
            return self._registrar_apps(AjusteDosApps(incerto=f"a leitura dos pacotes não respondeu ({exc})"))
        except ValueError as exc:
            return self._registrar_apps(AjusteDosApps(falhas=(f"não deu para ler os pacotes: {exc}",)))
        plano = apps_de_fundo.planejar(lista, estado)
        base = AjusteDosApps(ja_desativados=plano.ja_desativados, ausentes=plano.ausentes, recusados=plano.recusados)
        desativados: tuple[str, ...] = ()
        reativados: tuple[str, ...] = ()
        falhas: tuple[str, ...] = ()
        if plano.desativar or plano.reativar:
            if restante() < 1.0:
                return self._registrar_apps(replace(base, incerto="sem tempo para o `pm`; fica para o próximo"))
            try:
                escrita = self._run(["shell", apps_de_fundo.comando_de_escrita(plano)], timeout=restante())
            except AdbTimeout as exc:
                # Não se sabe quais chegaram a mudar: nada vai para o marcador; o próximo preparo lê e confere.
                return self._registrar_apps(replace(base, incerto=f"o `pm` não respondeu a tempo ({exc})"))
            desativados, reativados, falhas = apps_de_fundo.ler_escrita(escrita.stdout or "", plano)
        ajuste = replace(base, desativados=desativados, reativados=reativados, falhas=falhas)
        marcador = apps_de_fundo.marcador_depois(estado, plano, desativados, reativados)
        if set(marcador) == estado.marcados:
            return self._registrar_apps(ajuste)
        # Sem marcador gravado, o que foi desativado agora e está na lista entra nele no preparo seguinte (está na
        # lista e desativado: `marcador_depois`); o que foi reativado não volta a ser tentado (já está habilitado).
        if restante() < 0.5:
            return self._registrar_apps(replace(ajuste, incerto="sem tempo para gravar o marcador; fica para depois"))
        try:
            gravou = self._run(["shell", apps_de_fundo.comando_do_marcador(marcador)],
                               timeout=min(5.0, restante()))
        except AdbTimeout as exc:
            return self._registrar_apps(replace(ajuste, incerto=f"o marcador não foi gravado a tempo ({exc})"))
        if gravou.returncode != 0:
            ajuste = replace(ajuste, falhas=(*ajuste.falhas, "o marcador não foi gravado"))
        return self._registrar_apps(ajuste)

    def _registrar_apps(self, ajuste: AjusteDosApps) -> AjusteDosApps:
        """O registro no log DESTA máquina (central ou worker), com o serial: o que mudou em `info`, o que falhou ou
        ficou incerto em `warning`. Passagem sem mudança não escreve nada — é o caso de todo boot depois do primeiro."""
        if ajuste.falhas or ajuste.incerto or ajuste.recusados:
            log.warning("%s: %s", self.serial, ajuste.resumo())
        elif ajuste.mudou:
            log.info("%s: %s", self.serial, ajuste.resumo())
        return ajuste

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

    # -- arquivo privado no convidado (segredo de rede, ADR-056 §5) ---------------------------------------------
    # Os três abaixo não sabem o que carregam, de propósito: quem resolve o segredo é o consumidor restrito do
    # cofre (`security/segredo_de_rede.py`). Aqui só se garante o CAMINHO: conteúdo nunca em argumento de processo,
    # permissão 600 no convidado, pasta fixa, e mensagem de erro que não repete o que o subprocesso ecoou.
    def _caminho_privado(self, nome: str) -> str:
        if not NOME_PRIVADO_RE.match(nome) or ".." in nome:
            raise AdbError("nome de arquivo privado inválido")
        return f"{DIR_PRIVADO_NO_CONVIDADO}/{nome}"

    def _conferir_tamanho(self, remoto: str, esperado: int) -> None:
        """`exec-in` não devolve o código de saída do comando no convidado (o serviço `exec:` do adbd não tem
        protocolo de shell): o único jeito de saber que o `cat` gravou tudo é reler o tamanho. O tamanho não vai à
        mensagem: comprimento de segredo também é informação sobre ele."""
        try:
            gravado = int(self.shell(f"stat -c %s {remoto}", timeout=15).strip())
        except (AdbError, ValueError):
            gravado = -1
        if gravado != esperado:
            raise AdbError(f"o arquivo privado não chegou inteiro ao convidado de {self.serial}")

    def gravar_arquivo_privado(self, nome: str, conteudo: bytes, *, timeout: float = 30) -> str:
        """Grava `conteudo` em `DIR_PRIVADO_NO_CONVIDADO/<nome>` pelo STDIN do `adb exec-in` e devolve o caminho.

        Nenhum arquivo nesta máquina, nenhum byte em argumento: `exec-in` repassa o stdin cru ao comando do
        convidado (sem pty, então sem eco nem tradução de fim de linha). `umask 077` antes do `cat`: o arquivo nasce
        600, sem janela em que outro usuário do convidado o leia."""
        remoto = self._caminho_privado(nome)
        cmd = f"umask 077 && mkdir -p {DIR_PRIVADO_NO_CONVIDADO} && cat > {remoto}"
        res = self._run(["exec-in", cmd], timeout=timeout, binary=True, entrada=conteudo)
        if res.returncode != 0:
            # stderr em bytes, e o `cat` não ecoa o que leu; mesmo assim a mensagem é fixa: é ela que vira evento.
            raise AdbError(f"adb exec-in falhou em {self.serial} ({res.returncode})")
        self._conferir_tamanho(remoto, len(conteudo))
        return remoto

    def enviar_arquivo_privado(self, local: str, nome: str, *, tamanho: int, timeout: float = 60) -> str:
        """`adb push` de um arquivo do host para `DIR_PRIVADO_NO_CONVIDADO/<nome>`, com `chmod 600` em seguida.

        A alternativa ao stdin para quando o cliente precisar do arquivo por outro caminho de cópia; apagar o
        arquivo do HOST é de quem chama (ele o criou). O argumento é o caminho do arquivo, nunca o conteúdo. O
        `push` leva o modo do arquivo de origem, então há uma janela curta até o `chmod`: por isso o stdin é o
        padrão do consumidor, e este caminho fica para quando ele não servir."""
        remoto = self._caminho_privado(nome)
        self.shell(f"umask 077 && mkdir -p {DIR_PRIVADO_NO_CONVIDADO}", timeout=15)
        res = self._run(["push", local, remoto], timeout=timeout)
        if res.returncode != 0:
            raise AdbError(f"adb push do arquivo privado falhou em {self.serial} ({res.returncode})")
        self.shell(f"chmod 600 {remoto}", timeout=15)
        self._conferir_tamanho(remoto, tamanho)
        return remoto

    def apagar_arquivo_privado(self, nome: str) -> None:
        """`rm -f` do arquivo privado. Só na pasta fixa: o nome passa pela mesma regra de quem gravou."""
        remoto = self._caminho_privado(nome)
        res = self._run(["shell", f"rm -f {remoto}"], timeout=15)
        if res.returncode != 0:
            raise AdbError(f"não foi possível apagar o arquivo privado em {self.serial} ({res.returncode})")

    # -- mídia na galeria (29.30) -------------------------------------------------------------------------------
    def enviar_midia_para_galeria(self, local: str, nome: str, *, timeout: float = 60) -> str:
        """`adb push` de um JPEG do host para `/sdcard/Pictures/Central/<nome>.jpg` e a indexação da galeria; devolve o
        caminho no aparelho. É a ÚNICA gravação em armazenamento do usuário que o projeto faz, e só com imagem que a
        própria persona tem (quem confere isso é o despacho, não esta camada).

        O mesmo `Adb` serve aparelho local e remoto: a central alcança o remoto pelo túnel (`docs/worker.md`), então não
        há verbo de agente para isto. A prova real num aparelho remoto está `not_run`. O `nome` passa por regra fixa
        (sem `/`, `..` nem espaço): ele entra na linha de comando do convidado."""
        if not NOME_DE_MIDIA_RE.match(nome) or ".." in nome:
            raise AdbError("nome de mídia inválido para a galeria")
        remoto = f"{DIR_DA_GALERIA}/{nome}.jpg"
        self.shell(f"mkdir -p {DIR_DA_GALERIA}", timeout=15)
        res = self._run(["push", local, remoto], timeout=timeout)
        if res.returncode != 0:
            # Sem o caminho do host na mensagem: ela vira evento, log e corpo de resposta HTTP.
            raise AdbError(f"adb push da mídia falhou em {self.serial} ({res.returncode})")
        # Sem a indexação o arquivo existe e a galeria do editor não o mostra. O broadcast não devolve código de saída
        # confiável: o que se confere é o arquivo no destino.
        self.shell(f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://{remoto}", timeout=20)
        try:
            presente = self.shell(f"ls {remoto}", timeout=15).strip() == remoto
        except AdbError:
            presente = False
        if not presente:
            raise AdbError(f"a mídia não chegou à galeria de {self.serial}")
        return remoto

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

    def open_url(self, url: str) -> None:
        """Abre o endereço no navegador padrão (intent VIEW). Entre aspas simples na linha do shell do aparelho: o `#`
        de rota (`…/#/`) seria comentário e o `&` separaria comandos. Por isso aspa e espaço são recusados antes."""
        if not url_abrivel(url):
            raise AdbError("endereço inválido para abrir no navegador")
        out = self.shell(f"am start -a android.intent.action.VIEW -d '{url}'", timeout=30)
        if "Error" in out:
            raise AdbError(f"Não foi possível abrir o endereço: {out.strip()[:200]}")

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

    def reverse(self, port: int, *, timeout: float = 15) -> None:
        """`adb reverse tcp:P tcp:P`: o `127.0.0.1:P` DO CONVIDADO passa a chegar ao `127.0.0.1:P` da máquina do
        servidor adb. É o caminho do perfil de rede (25.4) para o aparelho de outra máquina, que não tem o `10.0.2.2`
        deste host: o túnel do adb já o alcança. Só a porta (inteiro) entra no argumento."""
        porta = int(port)
        res = self._run(["reverse", f"tcp:{porta}", f"tcp:{porta}"], timeout=timeout)
        if res.returncode != 0:
            raise AdbError(f"adb reverse falhou em {self.serial} ({res.returncode})")

    def remove_reverse(self, port: int, *, timeout: float = 15) -> None:
        """Desfaz o `reverse` da porta. Ausência do mapeamento não é erro (o aparelho pode ter reiniciado)."""
        self._run(["reverse", "--remove", f"tcp:{int(port)}"], timeout=timeout)

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

    def clock_skew_s(self, *, timeout: float = 10) -> int:
        """Desvio do relógio do convidado em relação ao host, em segundos. Só leitura: `date` lê o relógio do kernel,
        sem passar pelo `system_server`.

        O convidado lê a hora em algum ponto da ida e volta do `adb`; a conta usa o MEIO dela. Antes era "hora do
        convidado − hora do host no FIM da chamada": um relógio certo com 6 s de ida e volta lia −3 s e disparava
        um `set-time` à toa — um efeito não idempotente sem necessidade nenhuma."""
        antes = time.time()
        convidado = int(self.shell("date +%s", timeout=timeout).strip())
        return convidado - round((antes + time.time()) / 2)

    def sync_clock(self, *, tolerancia_s: int = 2) -> tuple[int, int]:
        """Acerta o relógio do convidado pelo host e CONFERE. Devolve (desvio_antes, desvio_depois) em segundos.

        Depois de acordar de um snapshot o relógio do guest continua no passado (medido: −26 a −31 s; depois do
        acerto, −1/−2 s — `relatorio-validacao.md` §7.3). O `cmd alarm set-time` leva um instante ABSOLUTO calculado
        aqui: se o `adb` estourar o prazo e a transação cair atrasada, ele ATRASA o convidado pelo tempo em que ficou
        presa (K-031). Por isso isto não roda no portão de prontidão: é a condição própria do relógio
        (`DeviceManager.conferir_relogio_do_convidado`), que reconfere periodicamente e desfaz um acerto que tenha
        caído tarde.

        Sem o antigo recurso `adb root` → `wait-for-device` → `date MMDDhhmm`: com o aparelho já no ar ele reinicia o
        `adbd` (derruba túnel, sessão do Appium e captura) e usava a hora LOCAL do host no fuso do convidado. Quem não
        converge com `set-time` aparece como não convergido — quem chama diz isso, não esconde."""
        before = self.clock_skew_s()
        if abs(before) <= tolerancia_s:
            return before, before
        self._run(["shell", f"cmd alarm set-time {int(time.time() * 1000)}"], timeout=10)
        return before, self.clock_skew_s()


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

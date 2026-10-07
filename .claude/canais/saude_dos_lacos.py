"""28.75: saúde dos processos de segundo plano da Canais (o vigia do dono e o laço de avisos de aparelho) e UM aviso ao dono.

Se a sessão da Canais cai, o vigia (`.claude/handoffs/canais/vigia_dono.py <id>`) e o laço (`avisos_de_aparelho.py --laco`, 28.73)
morrem em silêncio. Esta checagem é SÓ LEITURA: lista os processos Python do Windows, confere o ritmo do laço, olha a saúde da
central e, só com `--avisar`, manda UM aviso ao Telegram do dono quando algo parou. Nunca mata nem relança nada.

O que confere:
  - vigia: existe processo `vigia_dono.py`? O vigia SAI de propósito quando chega recado novo do dono; "ausente" só é
    problema se não houver recado novo pendente (id mais novo de `canal_entradas` do_dono=1 > a base do último vigia visto);
    sem saber a base ou sem conseguir ler o banco, é "não verificado" (incerteza nunca conta como saudável);
  - laço de aparelhos: o carimbo `atualizado_em` do arquivo de estado do laço (o `avisos_de_aparelho.py` o regrava a cada
    ciclo, até quando a leitura da central falha) mais novo que 3 x o intervalo = ativo; senão "parado ou preso";
  - central: `GET /api/health` com estado 200; qualquer outra coisa é "não respondeu".

Aviso (`--avisar`): molde dos avisos (assunto, uma linha por item, `Crítico:` = o que parou, `Espera você: nada`; a Canais
religa). SEM caminho de arquivo, PID, linha de comando nem nome de persona no texto. Estado em `--estado` (JSON fora do Git):
cooldown (padrão 30 min) entre avisos de problema e UM aviso "voltou" quando tudo normaliza. Quem nunca foi avisado não
recebe "voltou". Só o aviso de problema renova o cooldown.

`--religar` NÃO executa nada: só IMPRIME os comandos exatos para a Canais relançar o que parou.

GANCHO para a tarefa do host do 29.186 (NÃO criada aqui): o agendador do Windows rodaria `saude_dos_lacos.py --avisar` a cada
5 minutos e relançaria o laço. O ponto de encaixe é `comandos_de_religar()`, que devolve os `argv` exatos (puro, sem
subprocess); quem decidir executá-los é a tarefa do 29.186, nunca este arquivo.

Uso (python do backend/.venv):
  saude_dos_lacos.py                  ENSAIO: lê (só leitura) e imprime; não grava estado nem envia
  saude_dos_lacos.py --json           o mesmo em JSON
  saude_dos_lacos.py --avisar         grava o estado e ENVIA o aviso (problema ou "voltou") pelo `telegram_status.py`;
                                      só com o sinal da orquestradora
  saude_dos_lacos.py --religar        imprime os comandos para relançar o que parou (não executa)
  --estado <json>  --cooldown-min 30  --central http://127.0.0.1:8000  --estado-laco <json>  --intervalo-s 120
  --base <id>                         base do vigia (id de `canal_entradas`), para a pendência e para o `--religar`

Código de saída: 0 = a checagem rodou (os problemas estão no texto); 1 = `--avisar` não conseguiu enviar.
Última linha impressa: `itens <n>; com problema <n>`.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

#: o central (config, banco, python do backend): como o `avisos_de_aparelho.py`, não o checkout onde este arquivo está
RAIZ = Path(r"C:\git\android")
SCRIPTS = Path(__file__).resolve().parent
PY = RAIZ / "backend" / ".venv" / "Scripts" / "python.exe"
CENTRAL = "http://127.0.0.1:8000"
ESTADO_PADRAO = RAIZ / ".claude" / "handoffs" / "canais" / "estado-saude-dos-lacos.json"
ESTADO_LACO_PADRAO = RAIZ / ".claude" / "handoffs" / "canais" / "estado-avisos-de-aparelho.json"
SCRIPT_LACO = RAIZ / ".claude" / "canais" / "avisos_de_aparelho.py"
SCRIPT_VIGIA = RAIZ / ".claude" / "handoffs" / "canais" / "vigia_dono.py"
COOLDOWN_PADRAO_MIN = 30
INTERVALO_LACO_PADRAO_S = 120
#: o laço está vivo se o último ciclo é mais novo que este múltiplo do intervalo
FATOR_DO_INTERVALO = 3

VIGIA = "vigia_dono.py"
LACO = "avisos_de_aparelho.py"
_SCRIPT = re.compile(r"(?:^|[\\/\s\"'])(vigia_dono|avisos_de_aparelho)\.py(?=$|[\s\"'])", re.I)
_INTERVALO = re.compile(r"--intervalo-s(?:\s+|=)([0-9]+(?:\.[0-9]+)?)")

sys.path.insert(0, str(SCRIPTS.parent / "trello"))
sys.path.insert(0, str(SCRIPTS))
import redacao  # noqa: E402
from avisos_de_aparelho import _hora, _iso, enviar, gravar_estado  # noqa: E402  (o mesmo envio e a mesma gravação atômica)
from redacao import redigir  # noqa: E402
from resumo_laco import _sem_contato  # noqa: E402


class FalhaDeLeitura(Exception):
    """Não deu para listar os processos (só o tipo vai à saída)."""


# ------------------------------------------------------------------------------------------------ processos
@dataclass(frozen=True)
class Processo:
    """Só o que a checagem usa: o nome do script, o PID e dois parâmetros. A linha de comando NÃO é guardada."""
    pid: int
    script: str                 # VIGIA ou LACO
    base: int | None = None     # vigia: o id que ele recebeu
    intervalo_s: float | None = None
    laco: bool = False          # LACO: foi lançado com --laco (um ciclo avulso não é o laço)


def _processo(linha: str, pid: int) -> Processo | None:
    m = _SCRIPT.search(linha)
    if m is None:
        return None
    resto = linha[m.end():]
    if m.group(1).lower() == "vigia_dono":
        achado = re.search(r"\s(\d+)(?=$|\s|\")", resto)
        return Processo(pid, VIGIA, base=int(achado.group(1)) if achado else None)
    if not re.search(r"(?:^|\s)--laco(?=$|\s|\")", resto):
        return None
    achado = _INTERVALO.search(resto)
    return Processo(pid, LACO, intervalo_s=float(achado.group(1)) if achado else None, laco=True)


def parse_processos(bruto: object, *, ignorar_pid: int | None = None) -> list[Processo]:
    """Do JSON do `Get-CimInstance Win32_Process` (lista de objetos, ou um objeto só) para a lista de processos dos laços.

    O `python.exe` de um venv no Windows costuma ser um lançador que gera um filho com a MESMA linha de comando: o filho
    cujo pai também casa não conta, senão "1 processo" apareceria como 2."""
    if isinstance(bruto, dict):
        bruto = [bruto]
    if not isinstance(bruto, list):
        return []
    achados: list[tuple[Processo, int | None]] = []
    for item in bruto:
        if not isinstance(item, dict):
            continue
        linha, pid = item.get("CommandLine"), item.get("ProcessId")
        if not isinstance(linha, str) or not isinstance(pid, int) or pid == ignorar_pid:
            continue
        p = _processo(linha, pid)
        if p is not None:
            pai = item.get("ParentProcessId")
            achados.append((p, pai if isinstance(pai, int) else None))
    pids = {(p.pid, p.script) for p, _ in achados}
    return [p for p, pai in achados if pai is None or (pai, p.script) not in pids]


#: o filtro de nome é o único no PowerShell: o casamento com os scripts é aqui, em Python (senão a própria consulta casaria)
_PS = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
       "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress")


def listar_processos() -> list[Processo]:
    """Leitura real (só consulta): `Get-CimInstance Win32_Process` por subprocess."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS],
                           capture_output=True, timeout=60)
        if r.returncode != 0:
            raise FalhaDeLeitura(f"powershell saiu com {r.returncode}")
        texto = r.stdout.decode("utf-8", "replace").strip()
        bruto = json.loads(texto) if texto else []
    except FalhaDeLeitura:
        raise
    except Exception as erro:  # noqa: BLE001 - só o tipo do erro sai
        raise FalhaDeLeitura(type(erro).__name__) from None
    return parse_processos(bruto, ignorar_pid=os.getpid())


# ------------------------------------------------------------------------------------------------ leituras
def ler_carimbo_do_laco(caminho: Path) -> datetime | None:
    """`atualizado_em` do estado do laço; ausente ou ilegível = None."""
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _hora(bruto.get("atualizado_em")) if isinstance(bruto, dict) else None


def status_da_central(base: str) -> int | None:
    """Estado HTTP de `GET /api/health` (só GET, loopback); sem resposta = None."""
    try:
        with urllib.request.urlopen(f"{base.rstrip('/')}/api/health", timeout=10) as r:  # noqa: S310 - loopback
            return int(r.status)
    except urllib.error.HTTPError as erro:
        return int(erro.code)
    except Exception:  # noqa: BLE001 - a mensagem pode trazer a URL
        return None


def ler_ultima_entrada() -> int | None:
    """Maior id de `canal_entradas` do dono (como o `vigia_dono.py`, pelo banco do central; só SELECT). Falha = None."""
    try:
        sys.path.insert(0, str(RAIZ / "backend"))
        from app.config import load_config
        from app.db import Database
        db = Database(load_config().db_dsn)
        linhas = db.query("SELECT MAX(id) AS m FROM canal_entradas WHERE canal IN ('telegram','trello') AND do_dono=1")
        valor = dict(linhas[0]).get("m") if linhas else None
        return int(valor) if valor is not None else 0
    except Exception:  # noqa: BLE001 - sem o banco não há como saber
        return None


# ------------------------------------------------------------------------------------------------ avaliação (pura)
@dataclass(frozen=True)
class Item:
    chave: str       # "vigia" | "laco" | "central"
    curto: str       # rótulo da linha impressa
    rotulo: str      # como o aviso o chama
    estado: str      # "ativo" | "saiu" | "parado" | "desconhecido"
    texto: str
    problema: bool


def _min(delta: timedelta) -> int:
    return max(0, int(delta.total_seconds() // 60))


def _plural(n: int) -> str:
    return f"{n} processo" + ("" if n == 1 else "s")


def avaliar_vigia(procs: list[Processo] | None, base_salva: int | None, base_arg: int | None,
                  ler_ultima: Callable[[], int | None]) -> tuple[Item, int | None]:
    """(item, a base do vigia visto vivo, para o estado). `ler_ultima` só é chamada com o vigia ausente."""
    curto, rotulo = "vigia", "o vigia das respostas do dono"
    if procs is None:
        return Item("vigia", curto, rotulo, "desconhecido", "não verificado (não consegui listar os processos)", True), None
    vivos = [p for p in procs if p.script == VIGIA]
    if vivos:
        bases = [p.base for p in vivos if p.base is not None]
        return Item("vigia", curto, rotulo, "ativo", f"ativo ({_plural(len(vivos))})", False), (max(bases) if bases else None)
    ref = base_arg if base_arg is not None else base_salva
    ultima = ler_ultima() if ref is not None else None
    if ref is None or ultima is None:
        return Item("vigia", curto, rotulo, "desconhecido",
                    "ausente, e não sei se há recado novo do dono (sem base conhecida ou sem ler o banco)", True), None
    if ultima > ref:
        return Item("vigia", curto, rotulo, "saiu",
                    "saiu de propósito: há recado novo do dono esperando leitura (a Canais lê e relança)", False), None
    return Item("vigia", curto, rotulo, "parado", "parado: não há processo e não há recado novo pendente", True), None


def avaliar_laco(procs: list[Processo] | None, carimbo: datetime | None, agora: datetime,
                 intervalo_padrao_s: float) -> Item:
    curto, rotulo = "laço de aparelhos", "o laço de avisos de aparelho"
    vivos = [p for p in procs if p.script == LACO and p.laco] if procs is not None else []
    intervalo = next((p.intervalo_s for p in vivos if p.intervalo_s), None) or intervalo_padrao_s
    idade = max(timedelta(0), agora - carimbo) if carimbo is not None else None
    if idade is not None and idade <= timedelta(seconds=FATOR_DO_INTERVALO * intervalo):
        return Item("laco", curto, rotulo, "ativo", f"ativo, último ciclo há {_min(idade)} min", False)
    quando = f"último ciclo há {_min(idade)} min" if idade is not None else "sem registro de ciclo"
    sem = ", sem processo" if procs is not None and not vivos else ""
    return Item("laco", curto, rotulo, "parado", f"parado ou preso ({quando}{sem})", True)


def avaliar_central(codigo: int | None) -> Item:
    curto, rotulo = "central", "a central"
    if codigo == 200:
        return Item("central", curto, rotulo, "ativo", "200", False)
    extra = f" (estado {codigo})" if codigo is not None else ""
    return Item("central", curto, rotulo, "parado", f"não respondeu{extra}", True)


# ------------------------------------------------------------------------------------------------ texto do aviso
def _e(texto: object) -> str:
    return html.escape(redigir(_sem_contato(str(texto))), quote=False)


def montar_aviso(problemas: list[Item]) -> str:
    """HTML do Telegram no molde dos avisos. Só rótulos e vocabulário fixo: nenhum PID, caminho, comando ou nome."""
    n = len(problemas)
    verbo = "item parou" if n == 1 else "itens pararam"
    linhas = [f"<b>Canais em segundo plano: {n} {verbo}</b> (não espera nada de você)", ""]
    for i in problemas:
        linhas.append(f"• {_e(i.curto)}: {_e(i.texto)}.")
    linhas.append("• <b>Crítico:</b> " + ", ".join(_e(i.rotulo) for i in problemas) + ".")
    linhas.append("• <b>Espera você:</b> nada (a Canais é quem religa).")
    return redigir(_sem_contato("\n".join(linhas)))


def montar_voltou(rotulos: list[str]) -> str:
    n = len(rotulos)
    linhas = [
        "<b>Canais em segundo plano: voltou ao normal</b> (não espera nada de você)", "",
        f"• Voltou a responder: {', '.join(_e(r) for r in rotulos)}." if n else "• Tudo voltou a responder.",
        "• <b>Crítico:</b> nada.",
        "• <b>Espera você:</b> nada.",
    ]
    return redigir(_sem_contato("\n".join(linhas)))


# ------------------------------------------------------------------------------------------------ religar (só texto)
def comandos_de_religar(itens: list[Item], *, intervalo_s: float, base_vigia: int | None) -> list[tuple[str, list[str] | None, str]]:
    """GANCHO do 29.186: (rótulo, argv exato ou None, nota) do que parou. Puro: não executa nada."""
    saida: list[tuple[str, list[str] | None, str]] = []
    for i in itens:
        if not i.problema:
            continue
        if i.chave == "laco":
            saida.append((i.curto, [str(PY), str(SCRIPT_LACO), "--laco", "--intervalo-s", f"{intervalo_s:g}"],
                          "em segundo plano; grava o carimbo a cada ciclo"))
        elif i.chave == "vigia":
            if base_vigia is None:
                saida.append((i.curto, None, "base desconhecida: passe --base <último id lido de canal_entradas>"))
            else:
                saida.append((i.curto, [str(PY), str(SCRIPT_VIGIA), str(base_vigia)], "em segundo plano; sai no próximo recado"))
        else:
            saida.append((i.curto, None, "sem comando daqui: subir a central é o procedimento de docs/operacao.md"))
    return saida


# ------------------------------------------------------------------------------------------------ estado e decisão
def ler_estado(caminho: Path) -> tuple[dict, str | None]:
    if not caminho.exists():
        return {}, None
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
        if not isinstance(bruto, dict) or not isinstance(bruto.get("avisados", []), list):
            raise ValueError("formato")
        return bruto, None
    except (OSError, ValueError):
        return {}, "arquivo de estado ilegível; é refeito sem avisar o que já passou"


def decidir(salvo: dict, problemas: list[Item], agora: datetime, cooldown: timedelta) -> str | None:
    """"problema" (avisa agora), "voltou" (avisa que normalizou) ou None (nada, ou seguro pelo cooldown)."""
    if problemas:
        ultimo = _hora(salvo.get("ultimo_aviso_em"))
        if ultimo is not None and agora - ultimo < cooldown:
            return None
        return "problema"
    return "voltou" if salvo.get("avisados") else None


# ------------------------------------------------------------------------------------------------ uma checagem
def checar(*, listar: Callable[[], list[Processo]], carimbo: Callable[[], datetime | None], central: Callable[[], int | None],
           ultima_entrada: Callable[[], int | None], salvo: dict, agora: datetime, intervalo_s: float,
           base_arg: int | None) -> tuple[list[Item], list[Processo] | None, int | None, str | None]:
    """(itens, processos, base do vigia visto, causa da falha de listagem). Só leituras."""
    causa: str | None = None
    try:
        procs: list[Processo] | None = listar()
    except FalhaDeLeitura as erro:
        procs, causa = None, str(erro)
    vigia, base_vista = avaliar_vigia(procs, salvo.get("vigia_base"), base_arg, ultima_entrada)
    laco = avaliar_laco(procs, carimbo(), agora, intervalo_s)
    cen = avaliar_central(central())
    return [vigia, laco, cen], procs, base_vista, causa


def rodar(*, listar: Callable[[], list[Processo]], carimbo: Callable[[], datetime | None], central: Callable[[], int | None],
          ultima_entrada: Callable[[], int | None], estado_arq: Path, agora: datetime, cooldown: timedelta,
          intervalo_s: float = INTERVALO_LACO_PADRAO_S, base_arg: int | None = None, avisar: bool = False,
          enviar_fn: Callable[[str], int | None] | None = None, como_json: bool = False, religar: bool = False,
          imprimir: Callable[[str], None] = print) -> int:
    salvo, nota = ler_estado(estado_arq)
    if nota:
        imprimir(nota)
    itens, procs, base_vista, causa = checar(listar=listar, carimbo=carimbo, central=central, ultima_entrada=ultima_entrada,
                                             salvo=salvo, agora=agora, intervalo_s=intervalo_s, base_arg=base_arg)
    problemas = [i for i in itens if i.problema]
    if como_json:
        imprimir(json.dumps({
            "agora": _iso(agora),
            "itens": [{"chave": i.chave, "estado": i.estado, "texto": i.texto, "problema": i.problema} for i in itens],
            "processos": [{"script": p.script, "pid": p.pid} for p in (procs or [])],
            "falha_na_listagem": causa,
            "com_problema": len(problemas),
        }, ensure_ascii=False))
    else:
        for i in itens:
            imprimir(f"{i.curto}: {i.texto}")
        if procs:
            imprimir("processos vistos: " + ", ".join(f"{p.script} (pid {p.pid})" for p in procs))
        if causa:
            imprimir(f"listagem de processos falhou: {_e(causa)}")
        imprimir(f"itens {len(itens)}; com problema {len(problemas)}")

    if religar:
        base = base_arg
        if base is None and any(i.chave == "vigia" and i.problema for i in itens):
            base = ultima_entrada()           # o último id lido (como o vigia); sem o banco, a nota pede o --base
        for rotulo, argv, nota_cmd in comandos_de_religar(itens, intervalo_s=intervalo_s, base_vigia=base):
            imprimir(f"religar {rotulo}: " + (subprocess.list2cmdline(argv) if argv else "(sem comando)") + f"  # {nota_cmd}")
        imprimir("[--religar só imprime: nada foi executado]")

    acao = decidir(salvo, problemas, agora, cooldown)
    saiu = 0
    novo = dict(salvo)
    novo["versao"] = 1
    novo["atualizado_em"] = _iso(agora)
    if base_vista is not None:
        novo["vigia_base"] = base_vista
    if acao is None:
        if problemas:
            imprimir(f"aviso: em cooldown (último aviso há {_min(agora - (_hora(salvo.get('ultimo_aviso_em')) or agora))} min)")
        elif not como_json:
            imprimir("aviso: nada a avisar")
    else:
        texto = montar_aviso(problemas) if acao == "problema" else montar_voltou(
            [r for r in salvo.get("avisados", []) if isinstance(r, str)])
        if not como_json:
            imprimir(texto)
        if avisar and enviar_fn is not None:
            mid = enviar_fn(texto)
            if mid is None:
                saiu = 1                      # não enviou: o estado não muda e o próximo ciclo tenta de novo
            else:
                imprimir(f"enviado message_id={mid}")
                if acao == "problema":
                    novo["avisados"] = [i.rotulo for i in problemas]
                    novo["ultimo_aviso_em"] = _iso(agora)
                else:
                    novo["avisados"] = []
        elif not como_json:
            imprimir("[nada foi enviado]")
    if avisar:
        gravar_estado(estado_arq, novo)
    return saiu


# ------------------------------------------------------------------------------------------------ comando
def main(argv: list[str] | None = None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--central", default=CENTRAL, help="central (padrão: o loopback do central)")
    ap.add_argument("--estado", type=Path, default=ESTADO_PADRAO, help="estado deste aviso (padrão: %(default)s)")
    ap.add_argument("--estado-laco", type=Path, default=ESTADO_LACO_PADRAO, help="retrato do laço de aparelhos (padrão: %(default)s)")
    ap.add_argument("--cooldown-min", type=float, default=COOLDOWN_PADRAO_MIN, help="cooldown entre avisos de problema")
    ap.add_argument("--intervalo-s", type=float, default=INTERVALO_LACO_PADRAO_S,
                    help="intervalo do laço quando o processo não o informa")
    ap.add_argument("--base", type=int, help="base do vigia (id de canal_entradas): pendência e --religar")
    ap.add_argument("--json", action="store_true", help="saída em JSON")
    ap.add_argument("--avisar", action="store_true", help="grava o estado e ENVIA o aviso ao Telegram do dono")
    ap.add_argument("--religar", action="store_true", help="IMPRIME os comandos para relançar o que parou (não executa)")
    a = ap.parse_args(argv)
    if a.cooldown_min < 0 or a.intervalo_s <= 0:
        ap.error("--cooldown-min não pode ser negativo e --intervalo-s tem de ser maior que zero")
    redacao.recarregar(RAIZ)
    return rodar(listar=listar_processos, carimbo=lambda: ler_carimbo_do_laco(a.estado_laco),
                 central=lambda: status_da_central(a.central), ultima_entrada=ler_ultima_entrada, estado_arq=a.estado,
                 agora=datetime.now(UTC), cooldown=timedelta(minutes=a.cooldown_min), intervalo_s=a.intervalo_s,
                 base_arg=a.base, avisar=a.avisar, enviar_fn=enviar if a.avisar else None, como_json=a.json,
                 religar=a.religar)


if __name__ == "__main__":
    raise SystemExit(main())

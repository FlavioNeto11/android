"""28.73: aviso AGRUPADO no Telegram do dono quando um aparelho de conta real MUDA de estado de sessão.

Estados (os do 28.69, `trello/cartoes_de_aparelho.py`, lidos pela mesma API da central, só GET): pronta, vencida, com erro,
em pausa de reparo. Aparelho sem conta real é ignorado. Um ciclo lê a central, compara com o último estado AVISADO de cada
aparelho (guardado em `--estado`, JSON fora do Git) e monta UMA mensagem HTML do Telegram, no molde dos avisos (assunto,
uma linha por aparelho, `Crítico:` e `Espera você:`), com todos os aparelhos que mudaram no ciclo.

Regras:
  - o PRIMEIRO ciclo (sem arquivo de estado) só grava o retrato e não avisa; aparelho que aparece depois também só entra
    no retrato;
  - cooldown por aparelho (`--cooldown-min`, padrão 10): a mudança dentro do cooldown do último aviso DAQUELE aparelho não
    avisa agora e fica pendente; passado o cooldown ela entra no próximo aviso SE o estado ainda diferir do último avisado
    (a oscilação que voltou ao mesmo estado não avisa, porque a comparação é sempre com o último avisado);
  - leitura da central que falha: não avisa e não mexe no retrato; na 3ª falha seguida manda UM aviso "não consegui ler" e
    não repete até a leitura voltar. Falha na leitura de UM aparelho (personas) só o pula: o retrato dele fica como estava;
  - Crítico: aparelho que estava pronto e passou a ter erro (sessão caiu). Espera você: nada (a Central e a escada de
    reparo cuidam).

Texto: SÓ o rótulo do aparelho (o id da API, `android-06`) e o vocabulário fixo do 28.69. Nunca handle, nome de persona,
e-mail, telefone, IP nem serial (nada disso é lido); o corpo ainda passa por `_sem_contato` e `redacao.redigir`.

Uso (python do backend/.venv):
  avisos_de_aparelho.py                     ENSAIO de um ciclo: lê a central (só GET), compara e IMPRIME o que enviaria;
                                            não grava o estado nem envia
  avisos_de_aparelho.py --ciclo             um ciclo que GRAVA o estado e imprime (não envia)
  avisos_de_aparelho.py --enviar            um ciclo que grava o estado e ENVIA o aviso agrupado pelo `telegram_status.py`
                                            (imprime o message_id); só com o sinal da orquestradora
  avisos_de_aparelho.py --laco --intervalo-s 120   repete `--ciclo --enviar` até ser interrompido (Ctrl+C)
  --estado <arquivo.json>  --cooldown-min 10  --base http://127.0.0.1:8000  --arquivo <instancias.json> (ensaio offline)

Última linha impressa: `aparelhos <n>; com conta real <n>; mudaram <n>; pendentes <n>; falhas <n|nenhuma>`.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

#: o central (config, banco dos nomes, python do backend): como o `resumo_rodada.py`, não o checkout onde este arquivo está
RAIZ = Path(r"C:\git\android")
SCRIPTS = Path(__file__).resolve().parent
PY = RAIZ / "backend" / ".venv" / "Scripts" / "python.exe"
CENTRAL = "http://127.0.0.1:8000"
ESTADO_PADRAO = RAIZ / ".claude" / "handoffs" / "canais" / "estado-avisos-de-aparelho.json"
COOLDOWN_PADRAO_MIN = 10
INTERVALO_PADRAO_S = 120
FALHAS_PARA_AVISAR = 3
#: linhas de aparelho além do teto viram "e mais N": o aviso nunca passa de 12 linhas (o Telegram corta em 4096)
MAX_APARELHOS = 7

sys.path.insert(0, str(SCRIPTS.parent / "trello"))
sys.path.insert(0, str(SCRIPTS))
import redacao  # noqa: E402
from cartoes_de_aparelho import (  # noqa: E402
    ERRO,
    PRONTA,
    SEM_CONTA,
    Estado,
    FalhaDeLeitura,
    estados_dos_aparelhos,
    ler_da_central,
    ler_do_arquivo,
)
from redacao import redigir  # noqa: E402
from resumo_laco import _sem_contato  # noqa: E402


# ------------------------------------------------------------------------------------------------ puro: texto
def _e(texto: object) -> str:
    """Redige e escapa: tudo o que é dinâmico passa por aqui antes de virar HTML do Telegram."""
    return html.escape(redigir(_sem_contato(str(texto))), quote=False)


@dataclass(frozen=True)
class Mudanca:
    rotulo: str
    de: str          # estado do último aviso (ou do retrato inicial)
    para: str
    motivo: str      # vocabulário fixo do 28.69

    @property
    def critica(self) -> bool:
        """Sessão caiu: estava pronta e passou a ter erro."""
        return self.de == PRONTA and self.para == ERRO


def montar(mudancas: list[Mudanca]) -> str:
    """HTML do Telegram no molde dos avisos. `mudancas` não vazio; ordem por rótulo."""
    ms = sorted(mudancas, key=lambda m: m.rotulo)
    n = len(ms)
    criticas = [m.rotulo for m in ms if m.critica]
    verbo = "mudou" if n == 1 else "mudaram"
    cab = f"<b>Aparelhos de conta real: {n} {verbo} de estado</b> " + (
        "(tem ponto crítico; não espera nada de você)" if criticas else "(nada crítico; não espera nada de você)")
    linhas = [cab, ""]
    for m in ms[:MAX_APARELHOS]:
        linhas.append(f"• {_e(m.rotulo)}: de {_e(m.de)} para {_e(m.para)} ({_e(m.motivo)})")
    if n > MAX_APARELHOS:
        linhas.append(f"• e mais {n - MAX_APARELHOS} aparelho(s)")
    if criticas:
        mostrados = ", ".join(_e(r) for r in criticas[:MAX_APARELHOS])
        resto = f" e mais {len(criticas) - MAX_APARELHOS}" if len(criticas) > MAX_APARELHOS else ""
        linhas.append(f"• <b>Crítico:</b> a sessão caiu em {mostrados}{resto}.")
    else:
        linhas.append("• <b>Crítico:</b> nada.")
    linhas.append("• <b>Espera você:</b> nada (a Central e a escada de reparo cuidam).")
    return redigir(_sem_contato("\n".join(linhas)))


def montar_falha(causa: str, vezes: int) -> str:
    linhas = [
        "<b>Aparelhos de conta real: não consegui ler os aparelhos</b> (não espera nada de você)",
        "",
        f"• A leitura da central falhou {vezes} vezes seguidas ({_e(causa)}); sem ela não há aviso de mudança de estado.",
        "• <b>Crítico:</b> os avisos de aparelho ficam parados até a leitura voltar.",
        "• <b>Espera você:</b> nada (a Canais tenta de novo a cada ciclo e não repete este aviso).",
    ]
    return redigir(_sem_contato("\n".join(linhas)))


# ------------------------------------------------------------------------------------------------ puro: comparação
def _hora(valor: object) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def comparar(avisado: dict[str, dict] | None, estados: dict[str, Estado | None], agora: datetime,
             cooldown: timedelta) -> tuple[list[Mudanca], dict[str, dict], int]:
    """(mudanças a avisar AGORA, novo `avisado` supondo o aviso enviado, quantas ficaram pendentes pelo cooldown).

    `avisado`: rótulo → {estado, motivo, em}, o último estado comunicado ao dono (`em` = quando; `None` no retrato
    inicial). `None` = primeiro ciclo: só grava o retrato. Aparelho sem conta real sai do retrato; o que não deu para
    ler (`None`) mantém o que tinha."""
    novo: dict[str, dict] = {}
    mudancas: list[Mudanca] = []
    pendentes = 0
    for rotulo in sorted(estados):
        e = estados[rotulo]
        antes = (avisado or {}).get(rotulo)
        if e is None:                      # não deu para ler este aparelho: nada se conclui
            if antes is not None:
                novo[rotulo] = antes
            continue
        if e.estado == SEM_CONTA:
            continue
        if antes is None:                  # primeiro ciclo, ou aparelho novo: só entra no retrato
            novo[rotulo] = {"estado": e.estado, "motivo": e.motivo, "em": None}
            continue
        if antes.get("estado") == e.estado:
            novo[rotulo] = {**antes, "motivo": e.motivo}
            continue
        ultimo = _hora(antes.get("em"))
        if ultimo is not None and agora - ultimo < cooldown:
            pendentes += 1                 # segura: o retrato fica como estava e a próxima leitura decide
            novo[rotulo] = antes
            continue
        mudancas.append(Mudanca(rotulo, str(antes.get("estado")), e.estado, e.motivo))
        novo[rotulo] = {"estado": e.estado, "motivo": e.motivo, "em": _iso(agora)}
    return mudancas, novo, pendentes


# ------------------------------------------------------------------------------------------------ arquivo de estado
def ler_estado(caminho: Path) -> tuple[dict, str | None]:
    """(estado, nota). Sem arquivo: estado vazio (primeiro ciclo). Arquivo ilegível: também vazio, com a nota (o
    retrato é refeito sem avisar; a nota vai à saída, nunca some em silêncio)."""
    if not caminho.exists():
        return {}, None
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
        if not isinstance(bruto, dict):
            raise ValueError("formato")
        av = bruto.get("avisado")
        if av is not None and not (isinstance(av, dict) and all(isinstance(v, dict) for v in av.values())):
            raise ValueError("formato")
        return bruto, None
    except (OSError, ValueError):
        return {}, "arquivo de estado ilegível; o retrato é refeito sem avisar"


def gravar_estado(caminho: Path, estado: dict) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_name(caminho.name + ".tmp")
    tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(caminho)


# ------------------------------------------------------------------------------------------------ envio
def enviar(texto: str) -> int | None:
    """Manda pelo `telegram_status.py` (token e chat só no `.env` dele). Devolve o message_id, ou None se falhar."""
    with tempfile.TemporaryDirectory() as pasta:
        arquivo = Path(pasta) / "aviso_de_aparelho.html"
        arquivo.write_text(texto, encoding="utf-8")
        exe = PY if PY.exists() else Path(sys.executable)
        r = subprocess.run([str(exe), str(SCRIPTS / "telegram_status.py"), str(arquivo)], cwd=RAIZ, capture_output=True,
                           timeout=90)
    saida = r.stdout.decode("utf-8", "replace").strip()
    m = re.search(r"message_id=(\d+)", saida)
    if r.returncode == 0 and m:
        return int(m.group(1))
    print(f"falhou: {saida.splitlines()[0] if saida else r.returncode}")  # a saída do telegram_status já é limpa
    return None


# ------------------------------------------------------------------------------------------------ um ciclo
Ler = Callable[[], tuple[list[dict], dict[str, list | None], list[str]]]


def rodar_ciclo(ler: Ler, estado_arq: Path, agora: datetime, cooldown: timedelta, *, gravar: bool,
                enviar_fn: Callable[[str], int | None] | None = None,
                imprimir: Callable[[str], None] = print) -> int:
    """Um ciclo. Devolve 0 (ciclo ok, inclusive "nada mudou"), 1 (leitura falhou ou o envio falhou)."""
    salvo, nota = ler_estado(estado_arq)
    if nota:
        imprimir(nota)
    novo = dict(salvo)
    novo["versao"] = 1
    novo["atualizado_em"] = _iso(agora)
    try:
        instancias, personas, falhas = ler()
        if not instancias:
            raise FalhaDeLeitura("a central não listou aparelho nenhum")
    except FalhaDeLeitura as erro:
        seguidas = int(salvo.get("falhas_seguidas") or 0) + 1
        novo["falhas_seguidas"] = seguidas
        if seguidas >= FALHAS_PARA_AVISAR and not salvo.get("aviso_de_falha_enviado"):
            texto = montar_falha(erro.causa, seguidas)
            imprimir(texto)
            if enviar_fn is not None:
                mid = enviar_fn(texto)
                if mid is not None:          # sem message_id a flag não sobe: o próximo ciclo tenta de novo
                    novo["aviso_de_falha_enviado"] = True
                    imprimir(f"enviado message_id={mid}")
            else:
                imprimir("[nada foi enviado]")
        imprimir(f"leitura falhou: {_e(erro.causa)} ({seguidas} seguida(s)); retrato mantido")
        imprimir("aparelhos 0; com conta real 0; mudaram 0; pendentes 0; falhas leitura da central")
        if gravar:
            gravar_estado(estado_arq, novo)
        return 1

    estados, fora = estados_dos_aparelhos(instancias, personas)
    primeiro = salvo.get("avisado") is None
    mudancas, avisado, pendentes = comparar(salvo.get("avisado"), estados, agora, cooldown)
    novo["avisado"] = avisado
    novo["falhas_seguidas"] = 0
    novo["aviso_de_falha_enviado"] = False
    saiu = 0
    if mudancas:
        texto = montar(mudancas)
        imprimir(texto)
        if enviar_fn is not None:
            mid = enviar_fn(texto)
            if mid is None:
                saiu = 1
                for m in mudancas:            # não enviou: o retrato volta ao anterior e o próximo ciclo tenta de novo
                    anterior = (salvo.get("avisado") or {}).get(m.rotulo)
                    if anterior is not None:
                        novo["avisado"][m.rotulo] = anterior
            else:
                imprimir(f"enviado message_id={mid}")
        else:
            imprimir("[nada foi enviado]")
    elif primeiro:
        imprimir("primeiro ciclo: só o retrato" + (" é gravado" if gravar else " seria gravado") + "; nada a avisar")
    else:
        imprimir("nada mudou de estado" + (f"; {pendentes} em cooldown" if pendentes else ""))
    if gravar:
        gravar_estado(estado_arq, novo)
    com_conta = sum(1 for e in estados.values() if e is not None and e.estado != SEM_CONTA)
    falhas_txt = ", ".join([*falhas, *fora]) or "nenhuma"
    imprimir(f"aparelhos {len(estados)}; com conta real {com_conta}; mudaram {len(mudancas)}; pendentes {pendentes}; "
             f"falhas {falhas_txt}")
    return saiu


# ------------------------------------------------------------------------------------------------ comando
def main(argv: list[str] | None = None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=CENTRAL, help="central (padrão: o loopback do central)")
    ap.add_argument("--arquivo", type=Path, help="JSON de /api/instances, para ensaio offline")
    ap.add_argument("--estado", type=Path, default=ESTADO_PADRAO, help="arquivo do retrato (padrão: %(default)s)")
    ap.add_argument("--cooldown-min", type=float, default=COOLDOWN_PADRAO_MIN, help="cooldown por aparelho, em minutos")
    ap.add_argument("--ciclo", action="store_true", help="um ciclo que grava o estado (não envia)")
    ap.add_argument("--enviar", action="store_true", help="grava o estado e ENVIA o aviso ao Telegram do dono")
    ap.add_argument("--laco", action="store_true", help="repete --ciclo --enviar até ser interrompido")
    ap.add_argument("--intervalo-s", type=float, default=INTERVALO_PADRAO_S, help="intervalo do laço, em segundos")
    a = ap.parse_args(argv)
    if (a.enviar or a.laco) and a.arquivo:
        ap.error("--enviar e --laco só valem com a central lida ao vivo, não com --arquivo")
    if a.cooldown_min < 0 or a.intervalo_s <= 0:
        ap.error("--cooldown-min não pode ser negativo e --intervalo-s tem de ser maior que zero")
    gravar = a.ciclo or a.enviar or a.laco
    mandar = a.enviar or a.laco
    cooldown = timedelta(minutes=a.cooldown_min)
    # os nomes de persona a esconder vêm do banco do central; o texto só leva o rótulo, então a falha não bloqueia
    redacao.recarregar(RAIZ)

    def ler() -> tuple[list[dict], dict[str, list | None], list[str]]:
        return ler_do_arquivo(a.arquivo) if a.arquivo else ler_da_central(a.base)

    if not a.laco:
        rc = rodar_ciclo(ler, a.estado, datetime.now(UTC), cooldown, gravar=gravar, enviar_fn=enviar if mandar else None)
        if not gravar:
            print("\n[ensaio: nada foi gravado nem enviado]")
        return rc
    print(f"laço: um ciclo a cada {a.intervalo_s:g} s, cooldown {a.cooldown_min:g} min; Ctrl+C para parar")
    try:
        while True:
            try:
                rodar_ciclo(ler, a.estado, datetime.now(UTC), cooldown, gravar=True, enviar_fn=enviar)
            except Exception as erro:  # noqa: BLE001 - o laço não morre; só o tipo do erro sai (a mensagem pode trazer URL)
                print(f"ciclo falhou: {type(erro).__name__}")
            time.sleep(a.intervalo_s)
    except KeyboardInterrupt:
        print("laço interrompido")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

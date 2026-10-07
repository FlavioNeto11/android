"""Resumo diário ao dono pelo Telegram, para as 07:00 de Brasília: UM texto curto (HTML do Telegram, até 12 linhas, no
molde dos avisos: assunto, resultado com números, `Crítico:` e `Espera você:`).

Quatro leituras, cada uma independente das outras. Leitura que falha aparece como "não consegui ler"; nunca vira zero nem
é inventada, e "Espera você: nada" só sai quando as perguntas foram lidas e não há nenhuma.

1. Plano-100: `scripts/claude-plan-100.py check` para o total (como o `resumo_laco.py`) e `.claude/plano-100/estado.json`
   só para contar implementados, parciais e bloqueados. Nada de IA.
2. Deploys do dia: as entradas `## <data> — Deploy NN` mais recentes do CHANGELOG (as 5 de maior número) cuja hora, a do
   commit do Git que a escreveu (`git log -G`, como o `reconciliar.horas_dos_deploys`), cai nas últimas 24 h. Só número e
   hora de Brasília; o título da entrada nunca entra no texto.
3. Perguntas abertas ao dono: a lista "Perguntas para você" do Trello (`LISTA_PERGUNTAS`), lida pela API com o
   `ClienteTrello` do backend. Dos cartões saem só a contagem e os ids `P-NNN`; nome inteiro e descrição ficam de fora.
4. Cartões movidos em 24 h nos 3 quadros, por lista de destino. Escolha: as ACTIONS `updateCard:idList` do quadro
   (`acoes_do_quadro`, `since` = 24 h atrás), com `data.listAfter.name`. É a única fonte que diz PARA ONDE o cartão foi;
   `dateLastActivity` também muda por comentário e etiqueta e não traz a lista de origem nem a de destino da mudança.
   Cada cartão conta uma vez só, pelo último movimento da janela; mover para a mesma lista não conta.

O corpo inteiro passa por `_sem_contato` e por `redacao.redigir` (o filtro do Trello, com os nomes relidos do banco do
central), como o `resumo_laco.py` e o `resumo_rodada.py`. O envio é o `telegram_status.py`, que lê token e chat do `.env`
e nunca imprime nada deles.

Uso (python do backend/.venv):
  resumo_diario.py                          ensaio: lê plano, Git e Trello e IMPRIME o HTML (não envia nada)
  resumo_diario.py --arquivo situacao.json  ensaio offline, sem Trello, Git nem plano (formato em `montar`)
  resumo_diario.py --enviar                 envia pelo `telegram_status.py` e imprime o message_id (só com o sinal da
                                            orquestradora; não vale com --arquivo)
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: o central (config, banco dos nomes, python do backend): como o `resumo_laco.py`, não o checkout onde este arquivo está
RAIZ = Path(r"C:\git\android")
SCRIPTS = Path(__file__).resolve().parent
PY = RAIZ / "backend" / ".venv" / "Scripts" / "python.exe"
BRASILIA = timezone(timedelta(hours=-3))
JANELA = timedelta(hours=24)
MAX_LINHAS = 12
#: o Telegram corta em 4096; o resumo é curto
LIMITE = 3800
MAX_IDS = 8
MAX_LISTAS = 5
#: quantos dos deploys mais recentes do CHANGELOG se confere no Git (um `git log -G` por deploy)
DEPLOYS_CONFERIDOS = 5

#: lista "Perguntas para você" do quadro Execução
LISTA_PERGUNTAS = "6ac3c209ab485e2957580b09"
#: Execução, Programa, Histórico (os mesmos do `espelho_do_deploy.py`)
QUADROS = ("6ac13aeda5570365d020f8e2", "6ac13aeffc0ac80f9dc4edb3", "6ac13af1b3229189f1741536")
BACKEND_CENTRAL = RAIZ / "backend"

_ID_PERGUNTA = re.compile(r"\bP-\d{1,4}\b")
_DEPLOY = re.compile(r"^## \d{4}-\d{2}-\d{2} — Deploy (\d+)\b", re.M)

sys.path.insert(0, str(SCRIPTS.parent / "trello"))
sys.path.insert(0, str(SCRIPTS))
import redacao  # noqa: E402
from redacao import redigir  # noqa: E402
from resumo_laco import _sem_contato  # noqa: E402


class Recusa(Exception):
    """A entrada não serve para um resumo (arquivo ilegível, formato de outro tipo)."""


def _e(texto: object) -> str:
    """Redige e escapa: tudo o que é dinâmico passa por aqui antes de virar HTML do Telegram."""
    return html.escape(redigir(_sem_contato(str(texto))), quote=False)


def _num(valor: object) -> int:
    try:
        return max(0, int(valor))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def _hora(valor: object) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------------------------------- leituras
def ler_plano(raiz: Path = RAIZ) -> dict | None:
    """Total pelo `check` (como o `resumo_laco`), contagens pelo `estado.json`. `None` = não consegui ler."""
    try:
        exe = PY if PY.exists() else Path(sys.executable)
        out = subprocess.run([str(exe), str(raiz / "scripts" / "claude-plan-100.py"), "check"], cwd=raiz,
                             capture_output=True, timeout=120).stdout.decode("utf-8", "replace")
        m1, m2 = re.search(r"(\d+) itens no plano", out), re.search(r"Implementados: (\d+)", out)
        if not (m1 and m2):
            return None
        itens = json.loads((raiz / ".claude" / "plano-100" / "estado.json").read_text(encoding="utf-8"))["itens"]
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None
    contagem = {"parciais": sum(1 for v in itens.values() if v.get("status") == "partial"),
                "bloqueados": sum(1 for v in itens.values() if v.get("status") == "blocked")}
    return {"total": int(m1.group(1)), "implementados": int(m2.group(1)), **contagem}


def deploys_do_dia(agora: datetime, raiz: Path = RAIZ) -> list[dict] | None:
    """Os deploys do CHANGELOG da raiz cujo commit foi escrito nas últimas 24 h: `[{"n": 57, "hora": "<ISO UTC>"}]`.
    `[]` = nenhum no dia; `None` = não consegui ler o CHANGELOG ou o Git."""
    try:
        texto = (raiz / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return None
    numeros = sorted({int(n) for n in _DEPLOY.findall(texto)}, reverse=True)[:DEPLOYS_CONFERIDOS]
    achados = []
    for n in numeros:
        r = subprocess.run(["git", "-C", str(raiz), "log", "--format=%cI", f"-G^## [0-9-]*[0-9] — Deploy {n}[^0-9]", "--",
                            "CHANGELOG.md"], capture_output=True, text=True, encoding="utf-8", check=False)
        if r.returncode != 0:
            return None
        horas = r.stdout.split()
        quando = _hora(horas[-1]) if horas else None  # o commit mais velho: o que criou a entrada
        if quando is not None and agora - JANELA <= quando <= agora:
            achados.append({"n": n, "hora": _iso(quando)})
    return sorted(achados, key=lambda d: d["hora"])


async def ler_perguntas(cliente: Any) -> dict | None:
    """Cartões abertos da lista "Perguntas para você": total e ids `P-NNN` (só o nome do cartão é olhado)."""
    try:
        cartoes = await cliente.cartoes_da_lista(LISTA_PERGUNTAS)
    except Exception:  # noqa: BLE001 - rede, credencial ou formato: em qualquer caso o resumo diz que não leu
        return None
    ids: list[str] = []
    for c in cartoes:
        achado = _ID_PERGUNTA.search(str(c.get("name") or "")) if isinstance(c, dict) else None
        if achado and achado.group(0) not in ids:
            ids.append(achado.group(0))
    return {"n": len(cartoes), "ids": ids}


async def ler_movidos(cliente: Any, agora: datetime) -> dict[str, int] | None:
    """Cartões movidos de lista nas últimas 24 h, nos 3 quadros, contados por lista de destino."""
    desde = agora - JANELA
    por_lista: dict[str, int] = {}
    try:
        for quadro in QUADROS:
            acoes = await cliente.acoes_do_quadro(quadro, desde=_iso(desde), filtro="updateCard:idList", limite=1000)
            if not isinstance(acoes, list):
                return None
            vistos: set[str] = set()
            for a in acoes:  # da mais nova para a mais velha: o primeiro de cada cartão é o último movimento
                dados = a.get("data") if isinstance(a, dict) else None
                if not isinstance(dados, dict) or not isinstance(dados.get("listAfter"), dict):
                    continue
                quando = _hora(a.get("date"))
                if quando is None or not desde <= quando <= agora:
                    continue
                antes = dados.get("listBefore")
                if isinstance(antes, dict) and antes.get("id") == dados["listAfter"].get("id"):
                    continue
                cartao = str((dados.get("card") or {}).get("id") or a.get("id") or "")
                if cartao in vistos:
                    continue
                vistos.add(cartao)
                nome = " ".join(str(dados["listAfter"].get("name") or "outra lista").split())
                por_lista[nome] = por_lista.get(nome, 0) + 1
    except Exception:  # noqa: BLE001
        return None
    return por_lista


def _novo_cliente() -> Any:
    sys.path.insert(0, str(BACKEND_CENTRAL))
    from app.config import EnvSettings  # noqa: PLC0415 - só no modo de rede
    from app.modules.avisos.adapters.trello import ClienteTrello  # noqa: PLC0415

    e = EnvSettings()
    return ClienteTrello(e.trello_api_key.get_secret_value().strip(), e.trello_token.get_secret_value().strip())


def ler_situacao(agora: datetime, raiz: Path = RAIZ, cliente: Any = None) -> dict:
    """O retrato de agora, medido na hora. É a única parte com leitura de fora; `montar` é pura."""
    try:
        cliente = cliente or _novo_cliente()
    except Exception:  # noqa: BLE001 - sem chave ou token: as duas leituras do Trello falham, o resto segue
        cliente = None

    async def _trello() -> tuple[dict | None, dict | None]:
        if cliente is None:
            return None, None
        return await ler_perguntas(cliente), await ler_movidos(cliente, agora)

    perguntas, movidos = asyncio.run(_trello())
    return {"plano": ler_plano(raiz), "deploys": deploys_do_dia(agora, raiz), "perguntas": perguntas, "movidos": movidos}


# --------------------------------------------------------------------------------------------------------- texto
def _linha_plano(p: object) -> tuple[str, int]:
    if not isinstance(p, dict) or not p.get("total"):
        return "• <b>Plano:</b> não consegui ler o estado do plano.", 0
    bloq = _num(p.get("bloqueados"))
    return (f"• <b>Plano:</b> {_num(p['total'])} itens, {_num(p.get('implementados'))} implementados, "
            f"{_num(p.get('parciais'))} parciais, {bloq} bloqueados.", bloq)


def _linha_deploys(d: object) -> str:
    if not isinstance(d, list):
        return "• <b>Deploys em 24 h:</b> não consegui ler o CHANGELOG."
    if not d:
        return "• <b>Deploys em 24 h:</b> nenhum."
    partes = []
    for x in d:
        quando = _hora(x.get("hora")) if isinstance(x, dict) else None
        partes.append(f"{_num(x.get('n'))}" + (f" às {quando.astimezone(BRASILIA):%H:%M}" if quando else ""))
    return f"• <b>Deploys em 24 h:</b> {len(partes)} ({_e('; '.join(partes))})."


def _ids(ids: list[str]) -> str:
    valido = [i for i in ids if _ID_PERGUNTA.fullmatch(i)]
    resto = len(valido) - MAX_IDS
    return ", ".join(valido[:MAX_IDS]) + (f" e mais {resto}" if resto > 0 else "")


def _linha_perguntas(q: object) -> tuple[str, str]:
    """A linha do corpo e o texto do `Espera você:`."""
    if not isinstance(q, dict):
        return ("• <b>Perguntas abertas:</b> não consegui ler o Trello.", "não consegui ler as perguntas do Trello")
    n = _num(q.get("n"))
    if not n:
        return "• <b>Perguntas abertas:</b> nenhuma.", "nada"
    ids = _ids([str(i) for i in q.get("ids") or []])
    return (f"• <b>Perguntas abertas:</b> {n}" + (f" ({ids})" if ids else "") + ".",
            f"{n} {'pergunta' if n == 1 else 'perguntas'} no Trello" + (f" ({ids})" if ids else ""))


def _linha_movidos(m: object) -> str:
    if not isinstance(m, dict):
        return "• <b>Cartões movidos em 24 h:</b> não consegui ler o Trello."
    por_lista = {str(k): _num(v) for k, v in m.items() if _num(v)}
    if not por_lista:
        return "• <b>Cartões movidos em 24 h:</b> nenhum."
    itens = sorted(por_lista.items(), key=lambda kv: (-kv[1], kv[0]))
    partes = [f"{n} para {k}" for k, n in itens[:MAX_LISTAS]]
    resto = sum(n for _, n in itens[MAX_LISTAS:])
    if resto:
        partes.append(f"{resto} para outras listas")
    return f"• <b>Cartões movidos em 24 h:</b> {sum(por_lista.values())} ({_e('; '.join(partes))})."


def montar(situacao: dict, agora: datetime) -> str:
    """O HTML do Telegram. Pura. `situacao` (o mesmo formato do `--arquivo`):

        {"plano": {"total", "implementados", "parciais", "bloqueados"} | null,
         "deploys": [{"n": 57, "hora": "2026-10-06T21:00:00Z"}] | null,
         "perguntas": {"n": 3, "ids": ["P-026"]} | null,
         "movidos": {"Concluído": 5} | null}

    `null` (ou a chave ausente) é leitura que falhou: sai "não consegui ler", nunca zero."""
    if not isinstance(situacao, dict):
        raise Recusa("a situação não é um objeto JSON")
    plano, bloqueados = _linha_plano(situacao.get("plano"))
    perguntas, espera = _linha_perguntas(situacao.get("perguntas"))
    movidos = _linha_movidos(situacao.get("movidos"))
    deploys = _linha_deploys(situacao.get("deploys"))
    falhas = []
    if not isinstance(situacao.get("plano"), dict) or not situacao["plano"].get("total"):
        falhas.append("plano")
    if not isinstance(situacao.get("deploys"), list):
        falhas.append("CHANGELOG")
    if not isinstance(situacao.get("perguntas"), dict) or not isinstance(situacao.get("movidos"), dict):
        falhas.append("Trello")
    criticos = []
    if bloqueados:
        criticos.append(f"{bloqueados} {'item bloqueado' if bloqueados == 1 else 'itens bloqueados'} no plano")
    if falhas:
        criticos.append("sem leitura de " + ", ".join(falhas))
    quando = agora.astimezone(BRASILIA)
    linhas = [f"<b>Resumo diário da Central</b> ({quando:%d/%m}, {quando:%H:%M} de Brasília)", plano, deploys, perguntas,
              movidos, "• <b>Crítico:</b> " + ("; ".join(criticos) if criticos else "nada") + ".",
              "• <b>Espera você:</b> " + espera + "."]
    corpo = redigir(_sem_contato("\n".join(linhas)))  # defesa em profundidade, sobre o corpo inteiro
    if len(corpo.splitlines()) > MAX_LINHAS or len(corpo) > LIMITE:
        raise Recusa("o resumo passou do limite de linhas ou de tamanho")
    return corpo


# ---------------------------------------------------------------------------------------------------------- entrada
def ler_do_arquivo(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Recusa(f"arquivo ilegível ({type(exc).__name__})") from exc


def enviar(texto: str) -> int | None:
    """Manda pelo `telegram_status.py` (token e chat só no `.env` dele). Devolve o message_id, ou None se falhar."""
    with tempfile.TemporaryDirectory() as pasta:
        arquivo = Path(pasta) / "resumo_diario.html"
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


def main(argv: list[str] | None = None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arquivo", type=Path, help="JSON da situação, para ensaio offline")
    ap.add_argument("--raiz", type=Path, default=RAIZ, help="checkout de onde vêm o plano, o CHANGELOG e o Git")
    ap.add_argument("--enviar", action="store_true", help="envia ao Telegram do dono (só com o sinal da orquestradora)")
    a = ap.parse_args(argv)
    if a.enviar and a.arquivo:
        ap.error("--enviar só vale com a situação lida ao vivo, não com --arquivo")
    agora = datetime.now(timezone.utc)
    try:
        redacao.recarregar(RAIZ)  # os nomes de persona atuais, do banco do central (sem ele, valem os da reserva)
        situacao = ler_do_arquivo(a.arquivo) if a.arquivo else ler_situacao(agora, a.raiz)
        texto = montar(situacao, agora)
    except Recusa as exc:
        print(f"nada a enviar: {exc}")
        return 2
    if not a.enviar:
        print(texto)
        print("\n[ensaio: nada foi enviado]")
        return 0
    mid = enviar(texto)
    if mid is None:
        return 1
    print(f"enviado message_id={mid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""28.71: da resposta do dono por app à confirmação em bloco, num comando só (ensaio por padrão).

A regra do dono: a resposta a um cartão de pergunta (P-NNN) dada pelo app do Trello só vale depois de UM "ok" dele no
Telegram ou no chat, e esse "ok" cobre todas as pendentes. Antes a Canais fazia isso à mão (vigia, leitura do banco,
um `registrar_resposta.py` por cartão). Este script:

  1. lê as entradas do dono em `canal_entradas` com id maior que `--base`;
  2. liga cada resposta de app (`responde_a` = `pergunta:P-NNN;autoria=app_do_dono`) ao cartão P-NNN ABERTO da lista
     "Perguntas para você" (a mais recente por pergunta; P-NNN já em "Perguntas respondidas" é ignorada; sem cartão
     aberto vira "sem cartão", nada se inventa);
  3. imprime, por pendente, o COMANDO PRONTO do `registrar_resposta.py` (em ensaio);
  4. detecta a confirmação em bloco: uma entrada do dono no Telegram (mensagem solta, sem alvo), POSTERIOR à resposta,
     de até 60 caracteres e só com as frases do conjunto abaixo; `--ok-no-chat "<texto>"` traz o "ok" dado no chat da
     sessão e vale pelas mesmas regras. Sem confirmação imprime "aguardando ok dele" e nunca aplica;
  5. com `--aplicar`, só as confirmadas, uma por uma, pela função `registrar` do `registrar_resposta.py` (idempotente);
     uma linha por pendente: registrada, já registrada ou faltou.

Nunca comenta em cartão. A saída não ecoa mais que 80 caracteres do literal do dono e os passa por `redacao.redigir`
(o literal COMPLETO só vai ao cartão, por `registrar_resposta`, como a palavra dele).

Uso (da raiz):
  backend/.venv/Scripts/python.exe .claude/trello/resposta_pronta.py --base 3570 [--ok-no-chat "ok"] [--aplicar]
"""
from __future__ import annotations

import argparse
import asyncio
import re
import shlex
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
BACKEND_CENTRAL = Path(r"C:\git\android\backend")

import registrar_resposta as rr  # noqa: E402
from redacao import redigir  # noqa: E402

LISTA_PERGUNTAS = "6ac3c209ab485e2957580b09"       # Execução › Perguntas para você
LIMITE_LITERAL_NA_SAIDA = 80
LIMITE_DA_CONFIRMACAO = 60                          # texto maior que isto não é um "ok": é uma mensagem
FRASES_DE_OK = ("ok", "ja respondi", "respondido", "confirmo", "sim", "pode registrar")  # já sem acento, minúsculas
_SO_FRASES = re.compile(r"^(?:(?:" + "|".join(re.escape(f) for f in FRASES_DE_OK) + r")(?: |$))+$")
_PERGUNTA = re.compile(r"pergunta:(P-\d+);autoria=app_do_dono\b")
_NOME_P = re.compile(r"^\s*(P-\d+)\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_TELEFONE = re.compile(r"(?<!\w)\+?\d[\d ().-]{7,}\d")
_ROTULOS = {"registrada": "registrada", "ja_registrada": "já registrada"}


@dataclass
class Pendente:
    entrada: int
    canal: str
    quando: str
    literal: str
    pergunta: str
    cartao: dict[str, object] | None = None
    situacao: str = "aberta"                 # "aberta" | "sem_cartao" | "ja_respondida"
    confirmada_por: str = ""                 # "entrada 3576" | "chat"
    confirmacao_texto: str = ""
    resultado: str = ""                      # "registrada" | "ja_registrada" | "faltou" | "" (ainda não aplicada)
    substituida_por: int | None = field(default=None)


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento nem pontuação/emoji, espaços colapsados."""
    sem = "".join(c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sem.lower()).split())


def eh_confirmacao(texto: str | None) -> bool:
    """Só as frases do conjunto explícito, em qualquer ordem/repetição, e só em texto curto."""
    if not texto or len(texto.strip()) > LIMITE_DA_CONFIRMACAO:
        return False
    return bool(_SO_FRASES.match(normalizar(texto)))


def quando_de(recebida_em: object) -> str:
    """"DD/MM HH:MMZ" (UTC) do `recebida_em` ISO; vazio se não der (o registro recusa e a linha vira "faltou")."""
    try:
        d = datetime.fromisoformat(str(recebida_em).replace("Z", "+00:00"))
    except ValueError:
        return ""
    d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%d/%m %H:%MZ")


def ler_entradas(db, base: int) -> list[dict[str, object]]:  # noqa: ANN001
    linhas = db.query("SELECT id, canal, tipo, responde_a, alvo, texto, estado, recebida_em FROM canal_entradas "
                      "WHERE canal IN ('telegram','trello') AND do_dono=1 AND id>? ORDER BY id", (base,))
    return [dict(r) for r in linhas]


def respostas_de_app(entradas: list[dict[str, object]]) -> list[tuple[dict[str, object], str]]:
    out = []
    for e in entradas:
        m = _PERGUNTA.search(str(e.get("responde_a") or "")) if e.get("canal") == "trello" else None
        if m and str(e.get("texto") or "").strip():
            out.append((e, m.group(1)))
    return out


def confirmacoes_do_telegram(entradas: list[dict[str, object]]) -> list[dict[str, object]]:
    """Mensagem solta do dono (não é resposta a aviso com alvo, nem botão) que bate o conjunto de frases."""
    return [e for e in entradas if e.get("canal") == "telegram" and e.get("tipo", "mensagem") == "mensagem"
            and not e.get("alvo") and eh_confirmacao(str(e.get("texto") or ""))]


def _por_pergunta(cartoes: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for c in cartoes:
        m = _NOME_P.match(str(c.get("name") or ""))
        if m:
            out.setdefault(m.group(1), c)
    return out


def planejar(entradas: list[dict[str, object]], abertos: list[dict[str, object]], respondidos: list[dict[str, object]],
             ok_no_chat: str = "") -> list[Pendente]:
    """Puro: liga resposta a cartão e marca a confirmação. Uma resposta por pergunta (a mais recente); as anteriores
    saem como substituídas. A confirmação precisa ser POSTERIOR (id maior) à resposta."""
    cartoes, ja = _por_pergunta(abertos), _por_pergunta(respondidos)
    ultima: dict[str, Pendente] = {}
    pendentes: list[Pendente] = []
    for e, p in respostas_de_app(entradas):
        pend = Pendente(entrada=int(e["id"]), canal=str(e["canal"]), quando=quando_de(e.get("recebida_em")),
                        literal=str(e["texto"]).strip(), pergunta=p)
        if p in cartoes:
            pend.cartao = cartoes[p]
        else:
            pend.situacao = "ja_respondida" if p in ja else "sem_cartao"
        if p in ultima:
            ultima[p].substituida_por = pend.entrada
        ultima[p] = pend
        pendentes.append(pend)
    confirmacoes = confirmacoes_do_telegram(entradas)
    chat_ok = eh_confirmacao(ok_no_chat)
    for pend in pendentes:
        if pend.situacao != "aberta" or pend.substituida_por is not None:
            continue
        depois = [c for c in confirmacoes if int(c["id"]) > pend.entrada]
        if depois:
            pend.confirmada_por = f"entrada {depois[0]['id']}"
            pend.confirmacao_texto = f"Telegram, entrada {depois[0]['id']}, '{str(depois[0]['texto']).strip()}'"
        elif chat_ok:
            pend.confirmada_por = "chat"
            pend.confirmacao_texto = f"chat, '{ok_no_chat.strip()}'"
    return pendentes


def trecho(literal: str) -> str:
    """O pedaço do literal que pode ir ao console: redigido (o redator mais e-mail e telefone), numa linha, até 80
    caracteres. Redige ANTES de cortar, para um handle cortado ao meio não escapar."""
    t = " ".join(_TELEFONE.sub("[telefone]", _EMAIL.sub("[e-mail]", redigir(literal))).split())
    return t if len(t) <= LIMITE_LITERAL_NA_SAIDA else t[:LIMITE_LITERAL_NA_SAIDA - 1].rstrip() + "…"


def comando(p: Pendente) -> str:
    """O comando do `registrar_resposta.py` (ensaio) para colar. Literal cortado na saída: o `--aplicar` daqui usa o inteiro."""
    cartao = str(p.cartao["id"]) if p.cartao else "<cartao>"
    partes = ["backend/.venv/Scripts/python.exe .claude/trello/registrar_resposta.py", f"--cartao {cartao}",
              f"--entrada {p.entrada}", f"--canal {p.canal}", f"--quando {shlex.quote(p.quando)}",
              f"--literal {shlex.quote(trecho(p.literal))}"]
    if p.confirmada_por:
        partes.append(f"--confirmacao {shlex.quote(redigir(p.confirmacao_texto))}")
    return " ".join(partes)


def linha(p: Pendente) -> str:
    cab = f"{p.pergunta} · entrada {p.entrada} ({p.canal}, {p.quando or 'sem data'}) · \"{trecho(p.literal)}\""
    if p.substituida_por is not None:
        return f"{cab} -> substituída pela entrada {p.substituida_por}"
    if p.situacao == "sem_cartao":
        return f"{cab} -> sem cartão aberto em Perguntas para você (nada inventado)"
    if p.situacao == "ja_respondida":
        return f"{cab} -> ignorada: a pergunta já está em Perguntas respondidas"
    if p.resultado:
        return f"{cab} -> {_ROTULOS.get(p.resultado, p.resultado)}"
    if not p.confirmada_por:
        return f"{cab} -> aguardando ok dele"
    return f"{cab} -> confirmada por {p.confirmada_por}"


async def aplicar(cl, pendentes: list[Pendente]) -> None:  # noqa: ANN001
    """Só as confirmadas, uma por uma; uma falha não derruba as outras (a linha diz "faltou"; sem a mensagem de erro)."""
    for p in pendentes:
        if p.situacao != "aberta" or p.substituida_por is not None or not p.confirmada_por or p.cartao is None:
            continue
        try:
            r = await rr.registrar(cl, cartao=str(p.cartao["id"]), entrada=p.entrada, canal=p.canal, quando=p.quando,
                                   literal=p.literal, confirmacao=p.confirmacao_texto, aplicar=True, redigir=redigir,
                                   falar=lambda _t: None)
            p.resultado = "registrada" if r == "registrada" else "ja_registrada"
        except Exception as exc:  # noqa: BLE001 - uma linha "faltou" por pendente; o texto do erro pode ter dado sensível
            p.resultado = f"faltou ({type(exc).__name__})"


def relatorio(pendentes: list[Pendente], *, aplicando: bool) -> list[str]:
    if not pendentes:
        return ["nenhuma resposta de app do dono nas entradas novas"]
    saida = []
    for p in pendentes:
        saida.append(linha(p))
        if not aplicando and p.cartao is not None and p.substituida_por is None and not p.resultado:
            saida.append("    comando: " + comando(p))
    confirmadas = sum(1 for p in pendentes if p.confirmada_por and p.situacao == "aberta" and p.substituida_por is None)
    aguardando = sum(1 for p in pendentes if not p.confirmada_por and p.situacao == "aberta" and p.substituida_por is None)
    saida.append(f"resumo: {len(pendentes)} resposta(s), {confirmadas} confirmada(s), {aguardando} aguardando ok dele"
                 + ("" if aplicando else " (ensaio: nada foi escrito; use --aplicar)"))
    return saida


async def _principal(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(BACKEND_CENTRAL))
    from app.config import EnvSettings, load_config  # noqa: PLC0415 - só no modo de rede
    from app.db import Database  # noqa: PLC0415
    from app.modules.avisos.adapters.trello import ClienteTrello  # noqa: PLC0415
    entradas = ler_entradas(Database(load_config().db_dsn), args.base)
    e = EnvSettings()
    cl = ClienteTrello(e.trello_api_key.get_secret_value().strip(), e.trello_token.get_secret_value().strip())
    pendentes = planejar(entradas, await cl.cartoes_da_lista(LISTA_PERGUNTAS),
                         await cl.cartoes_da_lista(rr.LISTA_RESPONDIDAS), args.ok_no_chat)
    if args.aplicar:
        await aplicar(cl, pendentes)
    for t in relatorio(pendentes, aplicando=args.aplicar):
        print(t)
    return 1 if any(p.resultado.startswith("faltou") for p in pendentes) else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--base", required=True, type=int, help="só entradas com id maior que este")
    p.add_argument("--ok-no-chat", default="", help='o "ok" dado no chat da sessão (vale pelas mesmas frases curtas)')
    p.add_argument("--aplicar", action="store_true", help="registra as confirmadas (sem isto é ensaio, só leitura)")
    args = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]  # console do Windows é cp1252
    return asyncio.run(_principal(args))


if __name__ == "__main__":
    raise SystemExit(main())

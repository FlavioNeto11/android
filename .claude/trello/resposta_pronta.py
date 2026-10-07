"""28.71/28.77: da resposta do dono por app ou web ao registro no cartão, num comando só (ensaio por padrão).

Regra do dono (07/10, 28.77): a resposta a um cartão de pergunta (P-NNN), pelo app OU pela web do Trello, vale como dada.
NÃO há confirmação em bloco no Telegram nem no chat: a resposta é registrada e o cartão movido na hora. A autoria
(`app_do_dono`, `digitado`) é só informação no registro. As condições escritas no cartão continuam valendo (a Canais as
lê); só a resposta AMBÍGUA volta como pergunta nova no Trello. Este script:

  1. lê as entradas do dono em `canal_entradas` com id maior que `--base`;
  2. liga cada resposta do dono (`responde_a` = `pergunta:P-NNN` com `autoria=app_do_dono`, `digitado` ou ausente) ao cartão
     P-NNN ABERTO da lista "Perguntas para você" (a mais recente por pergunta; P-NNN já em "Perguntas respondidas" é
     ignorada; sem cartão aberto vira "sem cartão", nada se inventa; `autoria=app` e `nao_confirmada` não são o dono);
  3. classifica o texto (regra simples, abaixo): clara, livre ou ambígua;
  4. imprime, por pendente pronta, o COMANDO PRONTO do `registrar_resposta.py` (em ensaio);
  5. com `--aplicar`, TODAS as prontas (claras e livres), uma por uma, pela função `registrar` do
     `registrar_resposta.py` (idempotente); uma linha por pendente: registrada, já registrada ou faltou. A ambígua nunca
     é aplicada: sai "ambígua: voltar como pergunta nova".

Regra do texto (`classificar`, sobre o texto sem acento nem pontuação):
  - ambígua: vazio; com "?"; que cita outra P-NNN além da do cartão (ou mais de uma); sim e não juntos; texto curto (até
    60 caracteres) que não começa por sim/não/ok e não é escolha reconhecível;
  - clara: só "sim", "não/nao", "ok", "pode", "siga/segue/pode seguir a recomendação" (em combinação), ou uma escolha
    explícita ("A", "opção B", "letra C", "B fica");
  - livre: texto longo (mais de 60 caracteres) sem "?", ou curto que começa por sim/não/ok mas traz algo mais ("sim, mas só
    depois de conferir"). É registrado LITERALMENTE e marcado "ler (Canais)" no relatório: as condições dele são lidas pela
    Canais antes de agir.

Nunca comenta em cartão. A saída não ecoa mais que 80 caracteres do literal do dono e os passa por `redacao.redigir`
(o literal COMPLETO só vai ao cartão, por `registrar_resposta`, como a palavra dele).

Uso (da raiz):
  backend/.venv/Scripts/python.exe .claude/trello/resposta_pronta.py --base 3570 [--aplicar]
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
LIMITE_TEXTO_CURTO = 60                             # até aqui um texto sem sim/não/escolha é ambíguo; acima é "livre"
AUTORIAS_DO_DONO = ("app_do_dono", "digitado", "")  # só informação (28.77); `app` e `nao_confirmada` não são ele
_PERGUNTA = re.compile(r"pergunta:(P-\d+)(?:;autoria=(\w*))?")
_ID_P = re.compile(r"\bp (\d+)\b")                  # "P-034" normalizado vira "p 034"
_PALAVRA_CLARA = r"(?:sim|nao|ok|pode|siga|segue|seguir|a recomendacao)"
_SO_CLARA = re.compile(rf"^{_PALAVRA_CLARA}(?: {_PALAVRA_CLARA})*$")
_ESCOLHA = re.compile(r"^(?:(?:opcao|letra) )?[a-e](?: fica| vale| mesmo)?$|\b(?:opcao|letra) [a-e]\b")
_AFIRMA = ("sim", "ok", "siga", "segue", "seguir")
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
    autoria: str = ""                        # informação (app_do_dono, digitado, ""); nunca trava
    classe: str = "clara"                    # "clara" | "livre" | "ambigua" (ver `classificar`)
    motivo: str = ""                         # por que é ambígua
    resultado: str = ""                      # "registrada" | "ja_registrada" | "faltou" | "" (ainda não aplicada)
    substituida_por: int | None = field(default=None)

    @property
    def pronta(self) -> bool:
        """Entra no `--aplicar`: cartão aberto, a resposta mais recente, e não ambígua."""
        return (self.situacao == "aberta" and self.substituida_por is None and self.cartao is not None
                and self.classe != "ambigua")


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento nem pontuação/emoji, espaços colapsados."""
    sem = "".join(c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sem.lower()).split())


def classificar(texto: str | None, pergunta: str = "") -> tuple[str, str]:
    """(classe, motivo) do texto da resposta: "clara", "livre" ou "ambigua". A regra está no docstring do módulo."""
    bruto = (texto or "").strip()
    if not bruto:
        return "ambigua", "texto vazio"
    if "?" in bruto:
        return "ambigua", "o texto é uma pergunta"
    norm = normalizar(bruto)
    citadas = {f"P-{n}" for n in _ID_P.findall(norm)}
    if len(citadas) > 1 or (citadas and pergunta and citadas != {pergunta}):
        return "ambigua", "cita mais de uma pergunta"
    palavras = norm.split()
    if not palavras:
        return "ambigua", "texto sem palavras"
    if _SO_CLARA.match(norm):
        if "nao" in palavras and any(w in palavras for w in _AFIRMA):
            return "ambigua", "sim e não juntos"
        return "clara", ""
    if _ESCOLHA.search(norm):
        return "clara", ""
    if len(bruto) > LIMITE_TEXTO_CURTO or palavras[0] in ("sim", "nao", "ok"):
        return "livre", ""
    return "ambigua", "texto curto sem sim, não ou escolha reconhecível"


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


def respostas_do_dono(entradas: list[dict[str, object]]) -> list[tuple[dict[str, object], str, str]]:
    """(entrada, P-NNN, autoria) das respostas do dono em cartão de pergunta, pelo app ou pela web. Texto vazio entra (sai
    como ambígua, não some em silêncio); autoria que não é dele (`app`, `nao_confirmada`) fica de fora."""
    out = []
    for e in entradas:
        m = _PERGUNTA.search(str(e.get("responde_a") or "")) if e.get("canal") == "trello" else None
        if m and (m.group(2) or "") in AUTORIAS_DO_DONO:
            out.append((e, m.group(1), m.group(2) or ""))
    return out


def _por_pergunta(cartoes: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for c in cartoes:
        m = _NOME_P.match(str(c.get("name") or ""))
        if m:
            out.setdefault(m.group(1), c)
    return out


def planejar(entradas: list[dict[str, object]], abertos: list[dict[str, object]],
             respondidos: list[dict[str, object]]) -> list[Pendente]:
    """Puro: liga resposta a cartão e classifica o texto. Uma resposta por pergunta (a mais recente); as anteriores saem
    como substituídas. Sem confirmação: toda resposta ligada a um cartão aberto e não ambígua está pronta (28.77)."""
    cartoes, ja = _por_pergunta(abertos), _por_pergunta(respondidos)
    ultima: dict[str, Pendente] = {}
    pendentes: list[Pendente] = []
    for e, p, autoria in respostas_do_dono(entradas):
        literal = str(e.get("texto") or "").strip()
        classe, motivo = classificar(literal, p)
        pend = Pendente(entrada=int(e["id"]), canal=str(e["canal"]), quando=quando_de(e.get("recebida_em")),
                        literal=literal, pergunta=p, autoria=autoria, classe=classe, motivo=motivo)
        if p in cartoes:
            pend.cartao = cartoes[p]
        else:
            pend.situacao = "ja_respondida" if p in ja else "sem_cartao"
        if p in ultima:
            ultima[p].substituida_por = pend.entrada
        ultima[p] = pend
        pendentes.append(pend)
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
    if p.autoria:
        partes.append(f"--autoria {shlex.quote(p.autoria)}")
    return " ".join(partes)


def linha(p: Pendente) -> str:
    cab = f"{p.pergunta} · entrada {p.entrada} ({p.canal}, {p.quando or 'sem data'}) · \"{trecho(p.literal)}\""
    if p.substituida_por is not None:
        return f"{cab} -> substituída pela entrada {p.substituida_por}"
    if p.situacao == "sem_cartao":
        return f"{cab} -> sem cartão aberto em Perguntas para você (nada inventado)"
    if p.situacao == "ja_respondida":
        return f"{cab} -> ignorada: a pergunta já está em Perguntas respondidas"
    if p.classe == "ambigua":
        return f"{cab} -> ambígua ({p.motivo}): voltar como pergunta nova no Trello"
    marca = " · texto livre: ler (Canais)" if p.classe == "livre" else ""
    if p.resultado:
        return f"{cab} -> {_ROTULOS.get(p.resultado, p.resultado)}{marca}"
    return f"{cab} -> pronta para registrar{marca}"


async def aplicar(cl, pendentes: list[Pendente]) -> None:  # noqa: ANN001
    """Todas as prontas, uma por uma, sem confirmação; uma falha não derruba as outras (a linha diz "faltou"; sem a
    mensagem de erro). A ambígua nunca é aplicada."""
    for p in pendentes:
        if not p.pronta:
            continue
        try:
            r = await rr.registrar(cl, cartao=str(p.cartao["id"]), entrada=p.entrada, canal=p.canal, quando=p.quando,  # type: ignore[index]
                                   literal=p.literal, autoria=p.autoria, aplicar=True, redigir=redigir,
                                   falar=lambda _t: None)
            p.resultado = "registrada" if r == "registrada" else "ja_registrada"
        except Exception as exc:  # noqa: BLE001 - uma linha "faltou" por pendente; o texto do erro pode ter dado sensível
            p.resultado = f"faltou ({type(exc).__name__})"


def relatorio(pendentes: list[Pendente], *, aplicando: bool) -> list[str]:
    if not pendentes:
        return ["nenhuma resposta do dono em cartão de pergunta nas entradas novas"]
    saida = []
    for p in pendentes:
        saida.append(linha(p))
        if not aplicando and p.pronta and not p.resultado:
            saida.append("    comando: " + comando(p))
    prontas = sum(1 for p in pendentes if p.pronta)
    ambiguas = sum(1 for p in pendentes if p.situacao == "aberta" and p.substituida_por is None and p.classe == "ambigua")
    livres = sum(1 for p in pendentes if p.pronta and p.classe == "livre")
    saida.append(f"resumo: {len(pendentes)} resposta(s), {prontas} pronta(s) para registrar ({livres} de texto livre para "
                 f"a Canais ler), {ambiguas} ambígua(s) a devolver como pergunta nova"
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
                         await cl.cartoes_da_lista(rr.LISTA_RESPONDIDAS))
    if args.aplicar:
        await aplicar(cl, pendentes)
    for t in relatorio(pendentes, aplicando=args.aplicar):
        print(t)
    return 1 if any(p.resultado.startswith("faltou") for p in pendentes) else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--base", required=True, type=int, help="só entradas com id maior que este")
    p.add_argument("--aplicar", action="store_true", help="registra as prontas (sem isto é ensaio, só leitura)")
    args = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]  # console do Windows é cp1252
    return asyncio.run(_principal(args))


if __name__ == "__main__":
    raise SystemExit(main())

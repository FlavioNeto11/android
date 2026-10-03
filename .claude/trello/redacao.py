"""Redação do que NÃO vai ao Trello nem ao Telegram (servidores de terceiro): handles de conta, nomes das personas,
IPs, credencial dentro de URL ou atribuição, nome em campo de autoria e persona de teste com data de nascimento.

Os nomes e handles das personas são lidos do banco do central em modo ro quando ele existe (nunca ficam no
repositório); há uma lista fixa de reserva. Hash de commit com "@" (ex.: @ea1df281) é referência de código e fica.

Calibração de 03/10 (achados da limpeza do Trello): sobrenome que também é palavra comum ("fontes", "marcos") virava
`[persona]` em texto normal, e "@usuário" virava "@[conta]ário". Por isso:
- o nome inteiro e o handle valem sem diferença de maiúscula; uma PARTE solta do nome só vale com inicial maiúscula e
  fora da lista de palavras comuns (`_COMUNS`): "três fontes" e `fontes.py` ficam, "Fontes" num nome sai junto do
  nome inteiro;
- o @handle não pode terminar colado numa letra acentuada, e palavras de código (`@container`, `@dataclass`) ficam.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
_RESERVA = {"lucas", "bruno", "andre", "andré", "felipe", "juliana", "beatriz", "carla", "zilda"}
#: sobrenomes de persona que também são palavra comum: só saem como parte do nome inteiro
_COMUNS = {"fontes", "marcos", "costa", "moreira", "rocha", "campos", "flores", "rosa", "lima", "ramos", "torres",
           "neves", "porto", "cruz", "reis", "luz", "paz", "vale", "prado", "dias", "rios", "matos", "barros"}
#: @palavra que é código ou português, não conta de rede social
_ARROBA_LIVRE = {"container", "dataclass", "property", "staticmethod", "classmethod", "pytest", "usuario", "usuário",
                 "media", "import", "override", "abstractmethod", "cached_property", "router", "app", "todos",
                 "dono", "orquestradora", "canais"}


def _nomes_sensiveis() -> tuple[list[str], list[str]]:
    """(nomes inteiros e handles: casam sem diferença de maiúscula; partes soltas: só com inicial maiúscula)."""
    inteiros: set[str] = set(_RESERVA)
    partes: set[str] = set()
    try:
        caminho = str(RAIZ / "data" / "poc.sqlite3").replace("\\", "/")
        con = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
        for (nome,) in con.execute("select name from personas"):
            if isinstance(nome, str) and nome.strip():
                inteiros.add(nome.strip())
                partes.update(p for p in nome.split() if len(p) > 2 and p.lower() not in _COMUNS)
        for (handle,) in con.execute("select handle from profile_accounts"):
            if isinstance(handle, str) and handle.strip():
                inteiros.add(handle.strip().lstrip("@"))
        con.close()
    except Exception:  # noqa: BLE001 - sem banco, fica a reserva
        pass
    # Conta que saiu da plataforma some do banco (ADR-068), mas o nome dela continua no plano e nas evidências antigas
    # (24.9, 03/10). A lista local fica em data/, fora do Git; uma linha por nome ou handle.
    try:
        extras = (RAIZ / "data" / "redacao_nomes_extras.txt").read_text(encoding="utf-8").splitlines()
        inteiros.update(x.strip().lstrip("@") for x in extras if x.strip())
    except OSError:
        pass
    partes -={p for p in partes if p.lower() in {i.lower() for i in inteiros}}
    ordem = lambda xs: sorted(xs, key=len, reverse=True)  # noqa: E731 - o mais longo primeiro
    return ordem(inteiros), ordem(partes)


def _alternativa(nomes: list[str]) -> str:
    return "|".join(re.escape(n) for n in nomes) or r"(?!x)x"


_INTEIROS, _PARTES = _nomes_sensiveis()
_PERSONA = re.compile(r"(?<!\w)(" + _alternativa(_INTEIROS) + r")(?!\w)", re.I)
#: parte solta só com a inicial maiúscula do jeito que está no nome (sem re.I)
_PERSONA_PARTE = re.compile(r"(?<!\w)(" + _alternativa(_PARTES) + r")(?!\w)")

#: depois de "]" é o host de uma URL cuja senha já saiu ("[senha]@localhost"), não uma conta
_HANDLE = re.compile(r"(?<![\w/\]])@(?![0-9a-f]{7,8}\b)([A-Za-z0-9_.]{3,})(?![\w])")
#: nome.sobrenome seguido de 4+ dígitos é handle de persona mesmo sem o "@"
_HANDLE_SEM_ARROBA = re.compile(r"\b[a-z]+\.[a-z]+\d{4,}\b", re.I)
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
#: slug de perfil de terceiro nas evidências (ex.: perfil-nomedapessoa-3): nunca vai ao Trello
_PERFIL_SLUG = re.compile(r"\bperfil-[a-z0-9_.]+-\d+\b", re.I)
#: credencial dentro de URL ou string de conexão: esquema://usuário:SENHA@host
_URL_COM_SENHA = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+@", re.I)
#: atribuição de segredo: senha=..., password: ..., token=..., api_key=...
_ATRIBUICAO = re.compile(r"\b(senha|password|passwd|pwd|secret|token|api[_-]?key)(\s*[=:]\s*)(['\"]?)[^\s'\",;)]{4,}\3",
                         re.I)
#: nome de pessoa em campo de autoria: requested_by = 'Fulano'
_AUTORIA = re.compile(r"\b((?:requested|decided|resolved|created|approved)_by|autor|author)(\s*[=:]\s*)(['\"])"
                      r"(?!telegram:|trello:|dono\b|system\b|sistema\b|ia\b|ai\b)[^'\"]{2,60}\3", re.I)
#: persona de teste com data de nascimento: "Nome Sobrenome, 1995-04-12"
_PESSOA_COM_NASCIMENTO = re.compile(r"\b[A-ZÀ-Ý][a-zà-ÿ]+(?: [A-ZÀ-Ý][a-zà-ÿ]+)+,\s*(?:19\d\d|200\d)-\d\d-\d\d\b")
_NASCIMENTO = re.compile(r"\b(?:19\d\d|200\d)-[01]\d-[0-3]\d\b")


def _handle(m: re.Match[str]) -> str:
    return m.group(0) if m.group(1).lower() in _ARROBA_LIVRE else "@[conta]"


def redigir(texto: str) -> str:
    t = texto or ""
    t = _URL_COM_SENHA.sub(r"\1[senha]@", t)
    t = _ATRIBUICAO.sub(lambda m: f"{m.group(1)}{m.group(2)}[segredo]", t)
    t = _AUTORIA.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[nome]{m.group(3)}", t)
    t = _PESSOA_COM_NASCIMENTO.sub("[persona de teste], [data]", t)
    t = _NASCIMENTO.sub("[data]", t)
    t = _PERFIL_PARA_CONFERIR.sub(r"\1[conta]", t)
    t = _HANDLE.sub(_handle, t)
    t = _HANDLE_SEM_ARROBA.sub("[conta]", t)
    t = _PERSONA.sub("[persona]", t)
    t = _PERSONA_PARTE.sub("[persona]", t)
    t = _PERFIL_SLUG.sub("perfil-[terceiro]", t)
    return _IP.sub("[ip]", t)


def lista(valor: object, n: int) -> list[str]:
    """`arquivos`/`testes` do estado vêm como lista OU como string. Uma string só se parte nas vírgulas quando cada
    pedaço é um identificador (caminho, teste) sem espaço; frase ("suíte 7 inteira (-n 8, Idle) ...") fica inteira."""
    if isinstance(valor, str):
        partes = [x.strip() for x in re.split(r"[,;\n]+", valor) if x.strip()]
        if any(" " in p for p in partes):
            partes = [" ".join(valor.split())]
    elif isinstance(valor, (list, tuple)):
        partes = [str(x).strip() for x in valor if str(x).strip()]
    else:
        partes = []
    return partes[:n]

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


def _nomes_sensiveis(raiz: Path = RAIZ) -> tuple[list[str], list[str], bool]:
    """(nomes inteiros e handles: casam sem diferença de maiúscula; partes soltas: só com inicial maiúscula; se o banco
    foi lido)."""
    inteiros: set[str] = set(_RESERVA)
    partes: set[str] = set()
    lido = False
    try:
        caminho = str(raiz / "data" / "poc.sqlite3").replace("\\", "/")
        con = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
        for (nome,) in con.execute("select name from personas"):
            if isinstance(nome, str) and nome.strip():
                inteiros.add(nome.strip())
                partes.update(p for p in nome.split() if len(p) > 2 and p.lower() not in _COMUNS)
        for (handle,) in con.execute("select handle from profile_accounts"):
            if isinstance(handle, str) and handle.strip():
                inteiros.add(handle.strip().lstrip("@"))
        con.close()
        lido = True
    except Exception:  # noqa: BLE001 - sem banco, fica a reserva
        pass
    # Conta que saiu da plataforma some do banco (ADR-068), mas o nome dela continua no plano e nas evidências antigas
    # (24.9, 03/10). A lista local fica em data/, fora do Git; uma linha por nome ou handle.
    try:
        extras = (raiz / "data" / "redacao_nomes_extras.txt").read_text(encoding="utf-8").splitlines()
        inteiros.update(x.strip().lstrip("@") for x in extras if x.strip())
    except OSError:
        pass
    partes -={p for p in partes if p.lower() in {i.lower() for i in inteiros}}
    ordem = lambda xs: sorted(xs, key=len, reverse=True)  # noqa: E731 - o mais longo primeiro
    return ordem(inteiros), ordem(partes), lido


def _hosts() -> list[str]:
    """O nome das máquinas é identificador de infraestrutura: num servidor de terceiro vira "máquina central". Lido do
    ambiente na hora (nunca fixo no repositório); o nome do notebook pode vir na lista extra local."""
    import os
    import socket
    nomes = {os.environ.get("COMPUTERNAME", ""), socket.gethostname()}
    return sorted((n.strip() for n in nomes if n and len(n.strip()) >= 4), key=len, reverse=True)


def _alternativa(nomes: list[str]) -> str:
    return "|".join(re.escape(n) for n in nomes) or r"(?!x)x"


def _compilar(inteiros: list[str], partes: list[str]) -> tuple[re.Pattern[str], re.Pattern[str]]:
    # parte solta só com a inicial maiúscula do jeito que está no nome (sem re.I)
    return (re.compile(r"(?<!\w)(" + _alternativa(inteiros) + r")(?!\w)", re.I),
            re.compile(r"(?<!\w)(" + _alternativa(partes) + r")(?!\w)"))


_INTEIROS, _PARTES, _ = _nomes_sensiveis()
_HOST = re.compile(r"(?<![\w-])(" + _alternativa(_hosts()) + r")(?![\w-])", re.I)
_PERSONA, _PERSONA_PARTE = _compilar(_INTEIROS, _PARTES)


def recarregar(raiz: Path = RAIZ) -> bool:
    """Relê os nomes do banco de `raiz`. Os nomes são lidos uma vez na importação; um processo longo (o laço do resumo,
    28.31) não enxerga a persona criada depois de subir, e um worktree não tem o banco. Devolve se o banco foi lido
    (`False`: só a reserva e a lista extra)."""
    global _INTEIROS, _PARTES, _PERSONA, _PERSONA_PARTE  # noqa: PLW0603 - os padrões são do módulo
    _INTEIROS, _PARTES, lido = _nomes_sensiveis(raiz)
    _PERSONA, _PERSONA_PARTE = _compilar(_INTEIROS, _PARTES)
    return lido

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
#: assunto do e-mail de teste do comando entre apps (24.9, 27.2): "Perfil para conferir: <conta>" é conta de terceiro
_PERFIL_PARA_CONFERIR = re.compile(r"(?i)(perfil para conferir:\s*)@?[A-Za-z0-9_.]{2,30}")


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
    t = _HOST.sub("máquina central", t)
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

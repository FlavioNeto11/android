"""A gramática dos comandos que chegam por um canal de conversa (item 28.15, ADR-071): texto → intenção, por regra
fixa e sem IA.

É pura e comum aos canais: hoje o Telegram (28.15), depois o Trello (32.2). Nada de canal mora aqui. O adaptador de
cada canal traduz o que chegou (update, comentário) em texto + o FATO a que ele se refere (o aviso respondido, o
cartão comentado), e a resposta de volta. Quem decide se a mensagem é para a orquestradora pelo jeito do canal (o
reply a uma mensagem que a Central não mandou) também é o canal; aqui só existe o `/orq`.

O roteador não executa nada; ele só diz o que a mensagem pede. Quem age é o serviço de entrada, pelos MESMOS
serviços das rotas do painel.

Sem fato (a mensagem solta):
- `/ajuda` (e `/start`), `/status` (e `/estado`), `/pendencias`;
- `/aprovar <id> [nota]`, `/vetar <id> [nota]`, `/responder <id> <texto>`. O `<id>` é o id inteiro ou o fim dele,
  como a `/pendencias` mostra;
- `/para <aparelho|persona> <objetivo>`, ou "para o X: <objetivo>";
- texto livre: um objetivo, com o destino tirado do texto pelo extrator do painel;
- `/orq <texto>`: recado para a orquestradora, guardado e não executado.

Com fato (a resposta a um aviso, o comentário num cartão), o id é o do fato e não se escreve:
- aprovação: "sim" ou `/aprovar [nota]` aprova; "não" ou `/vetar [nota]` veta;
- execução que espera resposta: o texto, ou `/responder <texto>`, é a resposta.

A triagem de credencial também não mora aqui: o domínio e a aplicação não enxergam `app.security`. O serviço a faz
antes de gravar o texto.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: O que cada intenção é. `desconhecida` responde com a ajuda; `vazia` não responde.
INTENCOES = frozenset({"ajuda", "status", "pendencias", "aprovar", "vetar", "responder", "para", "livre",
                       "orquestradora", "desconhecida", "vazia"})

AJUDA = (
    "Comandos da Central:\n"
    "/status: o parque e o que está rodando\n"
    "/pendencias: o que espera você (aprovações e perguntas)\n"
    "/aprovar <id> e /vetar <id> [nota]: decide uma aprovação\n"
    "/responder <id> <texto>: responde à pergunta de uma execução\n"
    "/para <aparelho ou persona> <objetivo>: um pedido com destino\n"
    "Texto livre também é um pedido; antes de rodar, a Central mostra a prévia e espera Executar.\n"
    "Respondendo a um aviso, o id é o dele: \"sim\" aprova, \"não\" veta, e o texto responde a uma pergunta.\n"
    "Senha e código não passam por aqui: grave no painel.")

_SIM = frozenset({"sim", "s", "aprovar", "aprova", "aprovo", "ok", "pode", "confirmo", "\U0001f44d"})
_NAO = frozenset({"nao", "n", "vetar", "veta", "vetado", "rejeitar", "rejeita", "recusar", "recuso", "\U0001f44e"})
_ANDROID = re.compile(r"^android-\d+$", re.IGNORECASE)
#: "para o X: objetivo", "para a X: objetivo", "para X: objetivo" (os dois-pontos são o que separa o destino).
_PARA_LIVRE = re.compile(r"^\s*para\s+(?:o\s+|a\s+)?(?P<alvo>[^:\n]{1,60}?)\s*:\s*(?P<objetivo>\S.*)$",
                         re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True, slots=True)
class Intencao:
    tipo: str
    #: O id (inteiro ou o fim dele) da aprovação ou da execução. Com fato, o id do fato.
    ref: str | None = None
    #: O objetivo, a resposta, a nota do veto/aprovação, o recado ou o texto livre.
    texto: str = ""
    #: O destino do `/para`, como a pessoa escreveu.
    alvo: str | None = None
    #: O que dizer quando o formato não serve (só em `desconhecida`).
    motivo: str | None = None


@dataclass(frozen=True, slots=True)
class Fato:
    """A que a mensagem se refere: `approval:<id>` ou `run:<id>:needs_input` (a chave do aviso, `avisos_entregas`)."""

    tipo: str
    ident: str
    detalhe: str = ""

    @classmethod
    def de(cls, chave: str | None) -> Fato | None:
        if not chave:
            return None
        tipo, _, resto = chave.partition(":")
        ident, _, detalhe = resto.partition(":")
        return cls(tipo, ident, detalhe) if tipo and ident else None

    @property
    def aprovacao(self) -> bool:
        return self.tipo == "approval"

    @property
    def pergunta(self) -> bool:
        return self.tipo == "run" and self.detalhe == "needs_input"


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def _comando(texto: str) -> tuple[str, str]:
    """`/cmd@NomeDoBot resto` → (`cmd`, `resto`). O `@Nome` é o que o Telegram põe nos grupos."""
    cabeca, _, resto = texto.strip().partition(" ")
    return _sem_acento(cabeca[1:].split("@", 1)[0]), resto.strip()


def _palavra(texto: str) -> str:
    return _sem_acento(texto.strip().strip(".!").strip())


def rotear(texto: str | None, *, fato: str | Fato | None = None) -> Intencao:
    """A intenção de uma mensagem do operador. `fato`: a chave do aviso respondido ou do cartão comentado."""
    t = (texto or "").strip()
    if not t:
        return Intencao("vazia")
    f = fato if isinstance(fato, Fato) else Fato.de(fato)
    if t.startswith("/"):
        return _rotear_comando(t, f)
    if f is not None:
        por_fato = _rotear_resposta(t, f)
        if por_fato is not None:
            return por_fato
    m = _PARA_LIVRE.match(t)
    if m:
        return Intencao("para", alvo=m.group("alvo").strip(), texto=m.group("objetivo").strip())
    return Intencao("livre", texto=t)


def _rotear_comando(t: str, f: Fato | None) -> Intencao:
    cmd, resto = _comando(t)
    if cmd in ("ajuda", "start", "help"):
        return Intencao("ajuda")
    if cmd in ("status", "estado"):
        return Intencao("status")
    if cmd == "pendencias":
        return Intencao("pendencias")
    if cmd == "orq":
        return Intencao("orquestradora", texto=resto)
    if cmd in ("aprovar", "vetar"):
        if f is not None and f.aprovacao:
            return Intencao(cmd, ref=f.ident, texto=resto)
        ref, _, nota = resto.partition(" ")
        if not ref:
            return Intencao("desconhecida", motivo=f"Falta o id: /{cmd} <id> (a /pendencias mostra os ids).")
        return Intencao(cmd, ref=ref, texto=nota.strip())
    if cmd == "responder":
        if f is not None and f.pergunta:
            if not resto:
                return Intencao("desconhecida", motivo="Formato: /responder <texto>.")
            return Intencao("responder", ref=f.ident, texto=resto)
        ref, _, resposta = resto.partition(" ")
        if not ref or not resposta.strip():
            return Intencao("desconhecida", motivo="Formato: /responder <id> <texto>.")
        return Intencao("responder", ref=ref, texto=resposta.strip())
    if cmd == "para":
        alvo, _, objetivo = resto.partition(" ")
        if not alvo or not objetivo.strip():
            return Intencao("desconhecida", motivo="Formato: /para <aparelho ou persona> <objetivo>.")
        return Intencao("para", alvo=alvo.strip(), texto=objetivo.strip())
    return Intencao("desconhecida", motivo=f"Não conheço /{cmd}.")


def _rotear_resposta(t: str, f: Fato) -> Intencao | None:
    if f.aprovacao:
        palavra = _palavra(t)
        if palavra in _SIM:
            return Intencao("aprovar", ref=f.ident)
        if palavra in _NAO:
            return Intencao("vetar", ref=f.ident)
        return Intencao("desconhecida", motivo="Para decidir esta aprovação, responda \"sim\" ou \"não\".")
    if f.pergunta:
        return Intencao("responder", ref=f.ident, texto=t)
    return None


def texto_para_o_extrator(alvo: str, objetivo: str) -> str:
    """O `/para` vira uma frase que o extrator de destinos do painel reconhece (`TargetExtractor`): "no android-09",
    "como @fulano", "com a persona Ana". Assim o destino passa pelo MESMO casamento com o catálogo, e um nome que
    não casa com ninguém volta como pergunta, sem adivinhação."""
    a = alvo.strip()
    if _ANDROID.match(a):
        return f"{objetivo} no {a.lower()}"
    if a.startswith("@"):
        return f"{objetivo} como {a}"
    return f"{objetivo} com a persona {a}"


def casar_ref(ref: str, ids: list[str]) -> list[str]:
    """Os ids que o `ref` indica: o id inteiro, ou o FIM dele (4+ caracteres), como a `/pendencias` mostra."""
    r = ref.strip().lower()
    if not r:
        return []
    exatos = [i for i in ids if i.lower() == r]
    if exatos:
        return exatos
    if len(r) < 4:
        return []
    return [i for i in ids if i.lower().endswith(r)]


def sufixo(ident: str, n: int = 6) -> str:
    """O fim do id que a `/pendencias` mostra (`r-20261002181523-4985a1` → `4985a1`)."""
    return ident[-n:]

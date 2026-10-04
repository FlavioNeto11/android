"""O contato do site público (item 28.32, para o portal do 29.77): a mensagem de um visitante que chega ao Telegram do
dono. Domínio puro: a validação de defesa, a higiene dos campos e a montagem do aviso; a fila, o canal e a rota ficam
fora daqui.

O visitante é um desconhecido da internet, e o texto dele vai parar no chat em que o dono dá ordens à ANA. Por isso a
mensagem chega rotulada e desarmada:
- o título fixo diz que ela não é verificada;
- nome, empresa e telefone viram uma linha só, para ninguém forjar uma linha rotulada (um "Espera você:" falso);
- todo campo perde os caracteres de direção (bidi) e os de largura zero, que escondem ou invertem texto na tela;
- cada linha da mensagem começa com `│ `: um "ANA:" escrito pelo visitante aparece como citação, não como fala nossa;
- endereço, domínio, IP e comando de bot (`/aprovar`) deixam de ser tocáveis (`desarmar_links`). Um toque do dono num
  `/comando` escrito pelo visitante seria uma ordem DELE à Central.

Exceção do ADR-075: os campos NÃO passam por `texto_seguro` nem pelo redator. O nome e o telefone do visitante chegam
inteiros ao dono, porque é o pedido dele (o contato serve para ele responder).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .mensagem import Aviso, chave_do_fato, titulo_do_aviso

#: O tipo do aviso (fora da rajada e do espelho do Trello; nível 1 na amarração).
TIPO_DO_CONTATO = "portal.contato"
TITULO_DO_CONTATO = titulo_do_aviso("🌐 Mensagem de visitante do site (não verificada)")
#: Os tetos que a rota valida; a função repete a defesa (contrato do 28.32).
NOME_MAX, EMPRESA_MAX, TELEFONE_MAX, MENSAGEM_MAX = 80, 80, 30, 1500
#: O marcador de citação no começo de cada linha da mensagem.
CITACAO = "│ "

CAMPO_INVALIDO = "campo_invalido"
CANAL_DESLIGADO = "canal_desligado"
FALHA_INTERNA = "falha_interna"


@dataclass(frozen=True, slots=True)
class ContatoDoPortal:
    """O contato já validado pela rota e gravado na tabela do Portal (migração 107). `contato_id` é o id dessa linha:
    o mesmo contato, o mesmo id, e por isso a mesma chave."""

    contato_id: int
    nome: str
    empresa: str | None
    telefone: str | None
    mensagem: str


@dataclass(frozen=True, slots=True)
class ContatoAvisado:
    """`enfileirado`: a linha entrou na fila (ou já estava, pela chave). Quando é `False`, `motivo` é `campo_invalido`,
    `canal_desligado` ou `falha_interna`, e nada foi gravado."""

    enfileirado: bool
    motivo: str | None = None


#: Direção (U+202A–202E, U+2066–2069, U+200E, U+200F, U+061C) e largura zero (U+200B–200D, U+2060, U+FEFF).
_INVISIVEIS = re.compile("[‪-‮⁦-⁩‎‏؜​-‍⁠﻿]")
#: Quebras que o Unicode conhece além do `\n`: viram `\n` na mensagem e espaço nos campos de uma linha.
_QUEBRAS = re.compile("\r\n|[\r  \u0085\x0b\x0c]")
_TELEFONE_PERMITIDO = re.compile(r"[^0-9+()\- ]")
_ESPACOS = re.compile(r"[ \t]+")

#: `http(s)://` vira `hxxp(s)://`; outro esquema (`tg://`, `ftp://`) perde o `:`.
_HTTP = re.compile(r"(?i)\bh(tt)(ps?)://")
_ESQUEMA = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*)://")
#: domínio solto (letras de qualquer alfabeto: um domínio com letra cirílica também vira link), com o TLD só de letras
_DOMINIO = re.compile(r"(?<![^\W_])((?:[^\W_](?:[^\W_]|-)*\.)+[^\W\d_]{2,})(?![^\W_]|-)")
_IP = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
#: comando de bot no começo de palavra: `/aprovar` vira `⁄aprovar` (barra de fração), que o Telegram não toca
_COMANDO = re.compile(r"(?<!\S)/(?=[A-Za-z])")


def _sem_controle(texto: str, *, quebra: str) -> str:
    """Tira os invisíveis, troca as quebras por `quebra` e os demais caracteres de controle por espaço."""
    texto = _QUEBRAS.sub("\n", _INVISIVEIS.sub("", texto))
    return "".join(c if c == "\n" and quebra == "\n" else (" " if c == "\n" or unicodedata.category(c) == "Cc" else c)
                   for c in texto)


def uma_linha(valor: str | None) -> str:
    """Nome e empresa: uma linha só, sem invisíveis nem controle, com os espaços juntados."""
    return _ESPACOS.sub(" ", _sem_controle(valor or "", quebra=" ")).strip()


def telefone_limpo(valor: str | None) -> str:
    """Só dígitos, `+ ( ) -` e espaço."""
    return _ESPACOS.sub(" ", _TELEFONE_PERMITIDO.sub("", uma_linha(valor))).strip()


def desarmar_links(texto: str) -> str:
    """Nada do visitante fica tocável no chat do dono: `https://` → `hxxps://`, outro `esquema://` → `esquema[:]//`,
    o ponto de domínio e de IP → `[.]` (o `www.` e o `t.me/` caem aí) e `/comando` → `⁄comando`. Desarmar a mais é o
    lado seguro: "fim.Depois" sem espaço vira "fim[.]Depois"."""
    texto = _HTTP.sub(lambda m: f"hxx{m.group(2).lower()}://", texto)
    texto = _ESQUEMA.sub(lambda m: m.group(1) + "[:]//" if not m.group(1).lower().startswith("hxxp") else m.group(0),
                         texto)
    texto = _DOMINIO.sub(lambda m: m.group(1).replace(".", "[.]"), texto)
    texto = _IP.sub(lambda m: m.group(1).replace(".", "[.]"), texto)
    return _COMANDO.sub("⁄", texto)


def citar(mensagem: str) -> str:
    """A mensagem com as quebras dela, cada linha com o marcador de citação; duas linhas em branco seguidas viram uma."""
    linhas = [_ESPACOS.sub(" ", x).rstrip() for x in _sem_controle(mensagem, quebra="\n").split("\n")]
    saida: list[str] = []
    for linha in linhas:
        if not linha and saida and not saida[-1]:
            continue
        saida.append(linha)
    while saida and not saida[-1]:
        saida.pop()
    while saida and not saida[0]:
        saida.pop(0)
    return "\n".join(CITACAO + desarmar_links(x) for x in saida)


def chave_do_contato(contato_id: int) -> str:
    return chave_do_fato("portal", str(contato_id))


def motivo_de_recusa(contato: ContatoDoPortal) -> str | None:
    """A defesa repete os tetos e o obrigatório da rota: `campo_invalido`, ou `None` quando o contato serve. O nome e a
    mensagem que ficam vazios depois da higiene (só invisíveis, só controle) também não servem."""
    cid = contato.contato_id
    if isinstance(cid, bool) or not isinstance(cid, int) or cid <= 0:
        return CAMPO_INVALIDO
    campos = ((contato.nome, NOME_MAX, True), (contato.empresa, EMPRESA_MAX, False),
              (contato.telefone, TELEFONE_MAX, False), (contato.mensagem, MENSAGEM_MAX, True))
    for valor, teto, obrigatorio in campos:
        if valor is not None and not isinstance(valor, str):
            return CAMPO_INVALIDO
        if len(valor or "") > teto or (obrigatorio and not (valor or "").strip()):
            return CAMPO_INVALIDO
    if not uma_linha(contato.nome) or not citar(contato.mensagem):
        return CAMPO_INVALIDO
    return None


def corpo_do_contato(contato: ContatoDoPortal) -> str:
    """As linhas rotuladas fixas: `Nome:`, `Empresa:` e `Telefone:` (só se houver), `Mensagem:` e o texto citado."""
    linhas = [f"Nome: {desarmar_links(uma_linha(contato.nome))}"]
    empresa = desarmar_links(uma_linha(contato.empresa))
    if empresa:
        linhas.append(f"Empresa: {empresa}")
    telefone = telefone_limpo(contato.telefone)
    if telefone:
        linhas.append(f"Telefone: {telefone}")
    linhas += ["Mensagem:", citar(contato.mensagem)]
    return "\n".join(linhas)


def aviso_do_contato(contato: ContatoDoPortal) -> Aviso | None:
    """O aviso pronto para a fila, ou `None` quando o contato não serve (`motivo_de_recusa`). Sem link: o contato não
    tem página no painel."""
    if motivo_de_recusa(contato) is not None:
        return None
    return Aviso(chave=chave_do_contato(contato.contato_id), tipo=TIPO_DO_CONTATO, titulo=TITULO_DO_CONTATO,
                 corpo=corpo_do_contato(contato), link=None)

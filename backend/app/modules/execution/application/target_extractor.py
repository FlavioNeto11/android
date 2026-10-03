"""`TargetExtractor`: os destinos que o TEXTO do comando cita (onda C; design persona-e-parque §7.5).

"Peça para o André curtir a última foto da @nasa", "responda as DMs como @lucas.almeida", "abra o Chrome no
android-03" — quem e onde estão no texto. Esta etapa acha esses trechos, casa com o catálogo (nomes e @ das personas,
ids dos aparelhos) e devolve as DICAS e o comando SEM os destinos (`command_sem_destinos`), que é o que vai ao
casamento de habilidade e ao planejador: sem isto, um `{nome}` de fluxo capturaria "André" como parâmetro.

Determinística e sem IA de propósito: o nome da persona não é instrução, e extração por modelo não é repetível. Só
padrões explícitos contam — "com a persona X", "como @user", "pelo/pela X", "peça para o/a X", "no(s) aparelho(s)
Y e Z", `android-NN` —, e só o que casa com o catálogo vira dica. "Mande mensagem para o André" NÃO é destino (o
André ali é o destinatário), e um nome que não casa com ninguém deixa o texto inteiro. Dica nunca executa sozinha:
a origem `texto` obriga a prévia (§7.6, risco R13).

Roda ANTES do `TemplateStage` da RESOLVE: `RunService` a chama e passa `command_sem_destinos` adiante;
`runs.command` guarda o texto como veio.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from app.modules.execution.application.alvos import DicasDoTexto, MencaoNoTexto


@dataclass(frozen=True, slots=True)
class PersonaNomeavel:
    """Como uma persona pode ser citada: nomes (primeiro, completo, de exibição) e @ (usuário e contas)."""

    profile_id: str
    nomes: tuple[str, ...] = ()
    handles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogoDeDestinos:
    personas: tuple[PersonaNomeavel, ...] = ()
    aparelhos: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DestinosNoTexto:
    dicas: DicasDoTexto
    command_sem_destinos: str


def _normal(texto: str) -> str:
    """Minúsculas e sem acento: "André" e "andre" são a mesma citação."""
    return "".join(c for c in unicodedata.normalize("NFD", texto.casefold()) if unicodedata.category(c) != "Mn")


def _normal_com_origem(texto: str) -> tuple[str, list[int]]:
    """O `_normal` letra a letra, com a posição no ORIGINAL de onde veio cada letra do normalizado.

    `casefold` e a decomposição não dependem do vizinho, então juntar o `_normal` de cada letra dá o `_normal` do texto
    (a reordenação canônica só mexe em marca combinante, e a marca sai). Uma letra pode virar duas ("ß" -> "ss") ou nenhuma
    (o acento decomposto)."""
    partes: list[str] = []
    origem: list[int] = []
    for i, c in enumerate(texto):
        n = _normal(c)
        partes.append(n)
        origem.extend([i] * len(n))
    return "".join(partes), origem


#: Os começos que anunciam uma PERSONA. Cada um é seguido de um nome (ou @) que precisa casar com o catálogo.
_PREFIXOS_PERSONA = (
    r"\bcom\s+(?:a\s+persona|o\s+perfil|a\s+conta)\s+",
    r"\bcomo\s+(?=@)",
    r"\bpel[oa]\s+",
    r"^\s*(?:pe[çc]a|pe[çc]o|pede|pedir)\s+(?:para\s+(?:[oa]\s+)?|pr[oa]\s+)",
)
_PREFIXO_APARELHO = r"\b(?:n[oa]s?|em|d[oa]s?)\s+(?:aparelhos?|emuladore?s?|inst[aâ]ncias?)\s+"
_ANDROID = re.compile(r"(?:\b(?:n[oa]|em)\s+)?\bandroid-\d+\b", re.IGNORECASE)
_SEPARADOR = re.compile(r"\s*(?:,|\be\b)\s*", re.IGNORECASE)


class TargetExtractor:
    """Acha os destinos citados no texto e os tira dele. Uma instância por catálogo (o serviço monta a cada pedido)."""

    def __init__(self, catalogo: CatalogoDeDestinos) -> None:
        # (forma normalizada, é @?, profile_id): mais longa primeiro, para "André Carvalho" ganhar de "André".
        formas: list[tuple[str, bool, str]] = []
        for p in catalogo.personas:
            formas += [(_normal(n.strip()), False, p.profile_id) for n in p.nomes if n and n.strip()]
            formas += [("@" + _normal(h.strip().lstrip("@")), True, p.profile_id) for h in p.handles
                       if h and h.strip().lstrip("@")]
        self._formas = sorted(set(formas), key=lambda f: (-len(f[0]), f[0], f[2]))
        self._aparelhos = {_normal(a): a for a in catalogo.aparelhos}

    # ------------------------------------------------------------------ casar no ponto
    def _persona_em(self, normal: str, inicio: int, so_handle: bool) -> tuple[int, tuple[str, ...]] | None:
        """A citação de persona que começa em `inicio`: a forma MAIS LONGA que casa, com os ids de TODAS as formas
        desse comprimento (dois "André" = dois ids = pergunta). O "@" é opcional antes do usuário ("pela
        lucas.almeida" também é o @)."""
        melhor = 0
        ids: list[str] = []
        for forma, e_handle, pid in self._formas:
            if so_handle and not e_handle:
                continue
            opcoes = (forma, forma[1:]) if e_handle else (forma,)
            for f in opcoes:
                fim = inicio + len(f)
                if not normal.startswith(f, inicio) or not _termina(normal, fim):
                    continue
                if fim > melhor:
                    melhor, ids = fim, [pid]
                elif fim == melhor and pid not in ids:
                    ids.append(pid)
        return (melhor, tuple(ids)) if ids else None

    def _aparelhos_em(self, normal: str, inicio: int) -> tuple[int, list[str]] | None:
        """A lista "Y, Z e W" de ids de aparelho a partir de `inicio`; para no primeiro que não é aparelho."""
        achados: list[str] = []
        pos = inicio
        while True:
            m = re.match(r"[\w.-]+", normal[pos:])
            if m is None or m.group(0).rstrip(".") not in self._aparelhos:
                break
            achados.append(self._aparelhos[m.group(0).rstrip(".")])
            pos += len(m.group(0).rstrip("."))
            sep = _SEPARADOR.match(normal, pos)
            if sep is None or sep.end() == pos:
                break
            prox = re.match(r"[\w.-]+", normal[sep.end():])
            if prox is None or prox.group(0).rstrip(".") not in self._aparelhos:
                break
            pos = sep.end()
        return (pos, achados) if achados else None

    # ------------------------------------------------------------------ a extração
    def extrair(self, command: str) -> DestinosNoTexto:
        # Acha no texto normalizado e recorta SEMPRE do original, pelo mapa de posições (31.9, rodada C de 03/10). Antes,
        # quando `_normal` mudava o comprimento (acento decomposto, "ß", ligadura "ﬁ"), o comando sem destinos saía
        # normalizado — em minúsculas e sem acento —, e o filtro da C3 que vem depois perdia o que depende da caixa (a chave
        # "AKIA…" em minúsculas passava). Com o mapa, o texto só perde os trechos de destino.
        normal, origem = _normal_com_origem(command)
        cortes: list[tuple[int, int]] = []
        personas: list[MencaoNoTexto] = []
        aparelhos: list[MencaoNoTexto] = []

        def no_original(a: int, b: int) -> tuple[int, int]:
            """A faixa `[a, b)` do normalizado, em posições do original (com o acento decomposto que fecha a última letra)."""
            ini = origem[a] if a < len(origem) else len(command)
            fim = origem[b - 1] + 1 if 0 < b <= len(origem) else ini
            while fim < len(command) and unicodedata.category(command[fim]) == "Mn":
                fim += 1
            return ini, fim

        def trecho(a: int, b: int) -> str:
            ini, fim = no_original(a, b)
            return command[ini:fim].strip()

        for prefixo in _PREFIXOS_PERSONA:
            for m in re.finditer(prefixo, normal, re.IGNORECASE):
                if _sobrepoe(cortes, m.start(), m.end()):
                    continue
                achado = self._persona_em(normal, m.end(), so_handle=prefixo.startswith(r"\bcomo"))
                if achado is None:
                    continue
                fim, ids = achado
                personas.append(MencaoNoTexto(trecho(m.end(), fim), ids))
                cortes.append((m.start(), fim))
        for m in re.finditer(_PREFIXO_APARELHO, normal, re.IGNORECASE):
            if _sobrepoe(cortes, m.start(), m.end()):
                continue
            lista = self._aparelhos_em(normal, m.end())
            if lista is None:
                continue
            fim, citados = lista
            aparelhos.append(MencaoNoTexto(trecho(m.end(), fim), tuple(citados)))
            cortes.append((m.start(), fim))
        for m in _ANDROID.finditer(normal):
            ident = re.search(r"android-\d+", m.group(0), re.IGNORECASE)
            if ident is None or ident.group(0) not in self._aparelhos or _sobrepoe(cortes, m.start(), m.end()):
                continue
            aparelhos.append(MencaoNoTexto(trecho(m.start() + ident.start(), m.end()),
                                           (self._aparelhos[ident.group(0)],)))
            cortes.append((m.start(), m.end()))
        return DestinosNoTexto(DicasDoTexto(tuple(personas), tuple(aparelhos)),
                               _sem_trechos(command, [no_original(a, b) for a, b in cortes]))


def _palavra(c: str) -> bool:
    return c.isalnum() or c in "_.@-"


def _termina(texto: str, fim: int) -> bool:
    """A citação acaba em `fim`? Fronteira de palavra — e o ponto final da frase ("com a persona Lucas.") também
    é fronteira, embora "." seja letra de @ ("lucas.almeida")."""
    if fim >= len(texto) or not _palavra(texto[fim]):
        return True
    return texto[fim] == "." and (fim + 1 == len(texto) or not _palavra(texto[fim + 1]))


def _sobrepoe(cortes: Iterable[tuple[int, int]], a: int, b: int) -> bool:
    return any(a < fim and inicio < b for inicio, fim in cortes)


def _sem_trechos(texto: str, cortes: list[tuple[int, int]]) -> str:
    """O comando sem os trechos de destino, com espaço e pontuação de emenda arrumados."""
    if not cortes:
        return texto
    partes: list[str] = []
    pos = 0
    for a, b in sorted(cortes):
        partes.append(texto[pos:a])
        pos = b
    partes.append(texto[pos:])
    saida = re.sub(r"\s+", " ", " ".join(p.strip() for p in partes if p.strip()))
    saida = re.sub(r"\s+([,.;:!?])", r"\1", saida)
    saida = re.sub(r"[,;:]+([.!?])", r"\1", saida)
    saida = re.sub(r"^[\s,;:]+|[\s,;:]+$", "", saida)
    return saida or texto.strip()

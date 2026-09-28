"""Leituras de tela para o rascunho, como DADO (ADR-052): o que está escrito na tela, o que UMA pessoa comentou e a
última fala dela numa conversa. O app declara os padrões; este motor não conhece app nenhum.

É o `ScreenReader` do registro de apps (`modules/applications/infrastructure/registry.py`): o núcleo pede "o que
está visível" e "o que fulano disse", e cada app diz, em dado, como isso aparece na árvore dele.

Três regras valem mais que o formato, e são do motor, não do app:

* **tela sensível devolve vazio** — dali não sai nada, nem para o modelo;
* **fala só com autor** — sem o autor casado na MESMA linha, vazio; vazio quer dizer "escreva sem isto", nunca
  "invente" (atribuir a fala do vizinho produz resposta sem sentido e memória falsa no nome errado);
* **o que sai daqui é dado de terceiro** — quem monta o prompt marca como tal, nunca como instrução.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .hierarchy import UiTree

AUTOR = "{autor}"


class LeituraInvalida(ValueError):
    """A seção `leitura` do pacote do app não se sustenta: recusa na carga."""


@dataclass(frozen=True, slots=True)
class RegraDeFala:
    """Como achar a fala de `{autor}`: padrões com um grupo (o texto), onde procurar e qual ocorrência vale."""

    padroes: tuple[str, ...]
    campos: tuple[str, ...] = ("text",)
    escolher: str = "primeira"          # primeira | ultima

    def compilar(self, autor: str) -> tuple[re.Pattern[str], ...]:
        arroba = re.escape(autor.strip().lstrip("@"))
        return tuple(re.compile(p.replace(AUTOR, arroba), re.IGNORECASE | re.DOTALL) for p in self.padroes)


@dataclass(frozen=True, slots=True)
class LeituraDeclarada:
    """`ScreenReader` montado do pacote do app."""

    ignorar: re.Pattern[str] | None
    minimo: int
    comentario: RegraDeFala | None
    mensagem: RegraDeFala | None

    def visible_content(self, tree: UiTree, *, limite: int = 600, max_linhas: int = 8) -> str:
        """O que está ESCRITO na tela: sem os rótulos de interface declarados e sem texto curto demais para ser
        conteúdo. Heurística de propósito: ids de app mudam a cada versão; rótulo e tamanho envelhecem melhor."""
        if tree.sensitive:
            return ""
        linhas: list[str] = []
        vistos: set[str] = set()
        for e in tree.elements:
            texto = (e.text or "").strip()
            if len(texto) < self.minimo or (self.ignorar is not None and self.ignorar.match(texto)):
                continue
            if texto.lower() in vistos:
                continue
            vistos.add(texto.lower())
            linhas.append(texto)
            if len(linhas) >= max_linhas:
                break
        saida = "\n".join(linhas)
        return saida[:limite].rstrip() if len(saida) > limite else saida

    def comment_of(self, tree: UiTree, author: str, *, limite: int = 400) -> str:
        return self._fala(self.comentario, tree, author, limite)

    def message_of(self, tree: UiTree, author: str, *, limite: int = 400) -> str:
        return self._fala(self.mensagem, tree, author, limite)

    @staticmethod
    def _fala(regra: RegraDeFala | None, tree: UiTree, author: str, limite: int) -> str:
        # Testa o valor JÁ sem a arroba: "@" sozinho produziria um padrão que casa com qualquer linha.
        if regra is None or tree.sensitive or not (author or "").strip().lstrip("@"):
            return ""
        padroes = regra.compilar(author)
        achado = ""
        for e in tree.elements:
            for campo in regra.campos:
                bruto = (str(getattr(e, campo, "") or "")).strip()
                if not bruto:
                    continue
                for padrao in padroes:
                    if (m := padrao.match(bruto)) and (texto := m.group(1).strip()):
                        if regra.escolher == "primeira":
                            return texto[:limite].rstrip() if len(texto) > limite else texto
                        achado = texto
                        break
        return achado[:limite].rstrip() if len(achado) > limite else achado


def _regra(bruto: object, onde: str) -> RegraDeFala | None:
    if bruto is None:
        return None
    if not isinstance(bruto, dict):
        raise LeituraInvalida(f"{onde}: esperava um mapa")
    padroes = bruto.get("padroes")
    if not isinstance(padroes, list) or not padroes or not all(isinstance(p, str) and AUTOR in p for p in padroes):
        raise LeituraInvalida(f"{onde}.padroes: lista de expressões com {AUTOR}")
    campos = tuple(str(c) for c in (bruto.get("campos") or ["text"]))
    if any(c not in ("text", "desc") for c in campos):
        raise LeituraInvalida(f"{onde}.campos: só text e desc")
    escolher = str(bruto.get("escolher") or "primeira")
    if escolher not in ("primeira", "ultima"):
        raise LeituraInvalida(f"{onde}.escolher: primeira ou ultima")
    for p in padroes:
        try:
            if re.compile(p.replace(AUTOR, "x")).groups < 1:
                raise LeituraInvalida(f"{onde}: o padrão precisa de um grupo (o texto): {p}")
        except re.error as exc:
            raise LeituraInvalida(f"{onde}: expressão inválida ({exc})") from exc
    return RegraDeFala(padroes=tuple(padroes), campos=campos, escolher=escolher)


def de_dados(dados: object) -> LeituraDeclarada:
    """A seção `leitura` do pacote: `conteudo.ignorar` (regex de rótulos), `conteudo.minimo`, `comentario` e
    `mensagem` (cada um com `padroes`, `campos` e `escolher`)."""
    if not isinstance(dados, dict):
        raise LeituraInvalida("`leitura` precisa ser um mapa")
    conteudo = dados.get("conteudo") or {}
    if not isinstance(conteudo, dict):
        raise LeituraInvalida("`leitura.conteudo` precisa ser um mapa")
    ignorar_bruto = conteudo.get("ignorar")
    try:
        ignorar = re.compile(str(ignorar_bruto), re.IGNORECASE) if ignorar_bruto else None
    except re.error as exc:
        raise LeituraInvalida(f"leitura.conteudo.ignorar: expressão inválida ({exc})") from exc
    minimo = conteudo.get("minimo", 15)
    if not isinstance(minimo, int) or minimo < 1:
        raise LeituraInvalida("leitura.conteudo.minimo: inteiro positivo")
    return LeituraDeclarada(ignorar=ignorar, minimo=minimo, comentario=_regra(dados.get("comentario"), "comentario"),
                            mensagem=_regra(dados.get("mensagem"), "mensagem"))

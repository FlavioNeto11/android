"""O registro de provedores de sessão POR PACOTE, de uma composição (fase K1; design §2.5, §16).

O registro de apps (`modules/applications/infrastructure/registry.py`) é global e guarda a FÁBRICA do provedor que
cada app declara; o provedor em si precisa do que só a composição tem (aparelhos, repositório do perfil, cofre,
canal sensível, eventos). Este registro faz a ponte: na primeira pergunta por um pacote, fabrica o provedor com as
dependências desta composição e o guarda enquanto a fábrica registrada for a mesma. Registrar o app de novo (outra
fábrica) faz a próxima pergunta fabricar outro — nunca serve um provedor velho.

É a MESMA instância para todo mundo que pergunta pelo mesmo pacote: a porta de sessão, a reobservação depois do
controle manual e as rotas do painel falam com o mesmo provedor, e um teste que troca um método dele troca para
todos.

Genérico na fábrica de propósito: o tipo dela menciona o que a composição entrega (config, aparelhos, cofre...),
que é legado e o nível de aplicação não enxerga (regra D3). Quem compõe diz como fabricar (`build`).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

from .ports import SessionProvider

F = TypeVar("F")


class SessionProviders(Generic[F]):
    def __init__(self, factory_of: Callable[[str], F | None], build: Callable[[F], SessionProvider]) -> None:
        self._factory_of = factory_of
        self._build = build
        self._cache: dict[str, tuple[F, SessionProvider]] = {}

    def for_package(self, package: str | None) -> SessionProvider | None:
        """O provedor de sessão deste pacote, ou `None` quando o app não tem conta gerenciada (ou não é conhecido)."""
        if not package:
            return None
        fabrica = self._factory_of(package)
        if fabrica is None:
            self._cache.pop(package, None)
            return None
        guardado = self._cache.get(package)
        if guardado is not None and guardado[0] is fabrica:
            return guardado[1]
        provedor = self._build(fabrica)
        self._cache[package] = (fabrica, provedor)
        return provedor

    def has(self, package: str | None) -> bool:
        """Este pacote tem provedor de sessão? Pergunta ao registro, sem fabricar nada."""
        return bool(package) and self._factory_of(package or "") is not None

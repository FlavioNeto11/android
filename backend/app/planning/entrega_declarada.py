"""Item 31.250: as marcas de entrega que um app SEM catálogo declara como dado (`conhecimento/apps/<pacote>/entrega.yaml`).

O marcador do 31.57 (`delivery_marks`) mora no catálogo, na ação. O QA Messenger não tem catálogo, e não pode ganhar
um só por isso: com catálogo, a porta do item 13.2 (`efeito_fora_do_catalogo`) recusaria toda etapa com efeito sem
ação dele, e o planejador passaria a ver outra lista de apps. Este arquivo é conhecimento da tela, como as dicas do
`telas.yaml`: o executor o lê só na etapa sem capability, e o planejamento não o vê.

Arquivo inválido levanta `EntregaInvalida` (o executor registra e segue sem marca: o juiz julga, como antes).
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import yaml

from .capabilities import _PACOTE_ANDROID, CONHECIMENTO_DE_APPS, marcas_de_entrega

ARQUIVO = "entrega.yaml"
_CHAVES = frozenset({"app", "delivery_marks", "pending_marks", "failure_marks"})


class EntregaInvalida(ValueError):
    """O `entrega.yaml` não se sustenta."""


@dataclass(frozen=True)
class EntregaDeclarada:
    app: str
    delivery_marks: tuple[str, ...]
    pending_marks: tuple[str, ...] = ()
    failure_marks: tuple[str, ...] = ()


def _textos(dados: dict[str, object], chave: str, onde: str) -> tuple[str, ...]:
    valor = dados.get(chave) or []
    if not isinstance(valor, list) or not all(isinstance(v, str) and v.strip() for v in valor):
        raise EntregaInvalida(f"{onde}: `{chave}` é uma lista de textos")
    return tuple(v.strip() for v in valor)


def carregar_entrega(caminho: Path) -> EntregaDeclarada:
    try:
        dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise EntregaInvalida(f"{caminho}: YAML inválido ({exc})") from exc
    if not isinstance(dados, dict):
        raise EntregaInvalida(f"{caminho}: o arquivo é um mapa")
    if sobra := set(dados) - _CHAVES:
        raise EntregaInvalida(f"{caminho}: chave desconhecida {sorted(map(str, sobra))}")
    marcas = _textos(dados, "delivery_marks", str(caminho))
    if not marcas:
        raise EntregaInvalida(f"{caminho}: `delivery_marks` vazio")
    try:
        marcas_de_entrega(marcas)
    except ValueError as exc:
        raise EntregaInvalida(f"{caminho}: delivery_marks: {exc}") from exc
    return EntregaDeclarada(app=str(dados.get("app") or "").strip(), delivery_marks=marcas,
                            pending_marks=_textos(dados, "pending_marks", str(caminho)),
                            failure_marks=_textos(dados, "failure_marks", str(caminho)))


@functools.cache
def entrega_do_pacote(pacote: str | None, raiz: Path = CONHECIMENTO_DE_APPS) -> EntregaDeclarada | None:
    """As marcas que o app declara; `None` sem arquivo (o caso de todo app com catálogo: a marca dele fica na ação).
    Cacheado como o catálogo: o arquivo é lido uma vez por processo, e o `app` dele precisa ser o da pasta."""
    if not _PACOTE_ANDROID.match(pacote or ""):
        return None
    caminho = raiz / str(pacote) / ARQUIVO
    if not caminho.is_file():
        return None
    entrega = carregar_entrega(caminho)
    if entrega.app != pacote:
        raise EntregaInvalida(f"{caminho}: `app` é {entrega.app!r}, mas a pasta é {pacote!r}")
    return entrega

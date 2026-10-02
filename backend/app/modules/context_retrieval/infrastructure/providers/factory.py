"""Fábrica de provedores semânticos por nome. Não conhece configuração: quem chama traduz o YAML em argumentos."""
from __future__ import annotations

from collections.abc import Mapping

from ...adapters.jev import JevSemanticProvider
from ...domain.ports import SemanticProvider
from .fake import FakeSemanticProvider


def build_provider(name: str, *, model: str = "", env: Mapping[str, str] | None = None,
                   **kw: object) -> SemanticProvider | None:
    """`"none"`/`""` → `None` (sem provedor: o retriever cai no local). Nome desconhecido é erro de configuração."""
    nome = (name or "").strip().lower()
    if nome in ("", "none"):
        return None
    if nome == "fake":
        return FakeSemanticProvider(**({"model": model} if model else {}), **kw)
    if nome == "jev":
        return JevSemanticProvider(model=model, env=env, **kw)
    raise ValueError(f"provedor semantico desconhecido: {name!r}")

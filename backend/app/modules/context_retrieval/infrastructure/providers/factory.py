"""Fábrica de provedores semânticos por nome. Não conhece configuração: quem chama traduz o YAML em argumentos."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...domain.ports import SemanticProvider


def build_provider(name: str, *, model: str = "", env: Mapping[str, str] | None = None,
                   **kw: Any) -> SemanticProvider | None:
    """`"none"`/`""` → `None` (sem provedor: o retriever cai no local). Nome desconhecido é erro de configuração."""
    nome = (name or "").strip().lower()
    if nome in ("", "none"):
        return None
    # Imports tardios: quem só usa um provedor não carrega o outro (o Jev puxa o httpx).
    if nome == "fake":
        from .fake import FakeSemanticProvider
        return FakeSemanticProvider(**({"model": model} if model else {}), **kw)
    if nome == "jev":
        from .jev import JevSemanticProvider
        return JevSemanticProvider(model=model, env=env, **kw)
    raise ValueError(f"provedor semantico desconhecido: {name!r}")

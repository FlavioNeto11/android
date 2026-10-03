"""Os nomes em português que o painel mostra no lugar dos códigos (validação do deploy 3, P2 e P3).

O do app é o mesmo do agrupamento do Aprendido (`VisaoPorApp.nomes`: o declarado, depois o da loja, depois o pacote); o
da capability, o do catálogo do app sem as lacunas (`LearningService.nome_da_capability`). Só no JSON do painel: o
Markdown do relatório, o `grupo_json` dos scripts e os votos de uma execução seguem com o código.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.modules.learning.application.apps import VisaoPorApp
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.backlog import QUALQUER


def nomear(linhas: Iterable[object], servico: LearningService | None, *, app: str = "app",
           capability: str = "capability") -> None:
    """Põe `app_nome` e `capability_nome` em cada linha (dict) pelo pacote em `app` e a capability em `capability`;
    uma leitura de nomes de app e uma consulta por par. Sem serviço, sem visão por app, app `*` ou capability
    desconhecida, o nome sai nulo e o painel cai no código."""
    dicts = [x for x in linhas if isinstance(x, dict)]
    if not dicts:
        return
    visao = servico.extensao(VisaoPorApp) if servico is not None else None
    pacotes = {p for x in dicts if isinstance(p := x.get(app), str) and p and p != QUALQUER}
    apps = visao.nomes(pacotes) if visao is not None and pacotes else {}
    vistos: dict[tuple[str, str], str | None] = {}
    for x in dicts:
        pacote, cap = x.get(app), x.get(capability)
        x["app_nome"] = apps.get(pacote) if isinstance(pacote, str) else None
        par = (pacote, cap) if isinstance(pacote, str) and isinstance(cap, str) else None
        if par is not None and par not in vistos:
            vistos[par] = servico.nome_da_capability(*par) if servico is not None else None
        x["capability_nome"] = vistos.get(par) if par is not None else None


__all__ = ["nomear"]

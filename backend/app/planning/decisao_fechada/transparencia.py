"""Transparência da porta `DecisaoFechada` em `GET /api/ai` (item 31.5, ADR-069 item 8; roteiro §3 item 9).

Com `ai.decisao_fechada.enabled` e algum consumidor em `shadow` ou `on`, o `notice` passa a NOMEAR a TypeSafe e as classes de
dado que podem sair, e `/api/ai` lista o Jev. A chave aparece só como configurada ou não configurada, e quem chama passa um
booleano de PRESENÇA (`typesafe_api_key is not None`): este módulo nunca vê o valor.

Verdade antes de conforto: enquanto `JEV_RUNTIME_SEND_APPROVED` é falso no código, o aviso diz que o envio está fechado e que
nada sai, em vez de prometer uma exposição que ainda não existe nem de esconder a que o YAML anuncia.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import privacidade

if TYPE_CHECKING:
    from ...config import DecisaoFechadaCfg

NOME = "Jev (TypeSafe System One)"


def consumidores_ativos(cfg: DecisaoFechadaCfg | None) -> dict[str, str]:
    """Origem → modo, só dos consumidores em `shadow` ou `on`; vazio se a porta está desligada."""
    if cfg is None or not cfg.enabled:
        return {}
    return {o: m for o, m in sorted(cfg.consumidores.items()) if m in ("shadow", "on")}


def classes_que_podem_sair(cfg: DecisaoFechadaCfg | None) -> list[str]:
    """Teto de código restrito pelo YAML (interseção), em ordem. É o que PODERIA sair com o envio aprovado."""
    liberadas = privacidade.JEV_ALLOWED_CLASSES
    if cfg is not None and cfg.classes_permitidas is not None:
        liberadas = liberadas & frozenset(cfg.classes_permitidas)
    return sorted(liberadas)


def status(cfg: DecisaoFechadaCfg | None, *, chave_configurada: bool) -> dict[str, object] | None:
    """Bloco do Jev em `/api/ai`; `None` quando nenhum consumidor está em `shadow` ou `on` (nada a declarar)."""
    ativos = consumidores_ativos(cfg)
    if not ativos:
        return None
    return {"provider": "typesafe", "name": NOME, "consumers": ativos, "classes": classes_que_podem_sair(cfg),
            "send_approved": privacidade.JEV_RUNTIME_SEND_APPROVED,
            "key": "configurada" if chave_configurada else "não configurada",
            # 31.14: qual decisor está montado (`nulo` nunca chama; `jev` é o real), para o 31.10 ver sem ler a config
            "decider": "nulo" if cfg is None else cfg.decisor,
            "retention_days": None if cfg is None else cfg.retencao_dias}


def aviso(cfg: DecisaoFechadaCfg | None, *, chave_configurada: bool) -> str | None:
    """Frase que se soma ao `notice` de `/api/ai`; `None` sem consumidor em `shadow` ou `on`."""
    ativos = consumidores_ativos(cfg)
    if not ativos:
        return None
    consumidores = ", ".join(f"{o} ({m})" for o, m in ativos.items())
    classes = ", ".join(classes_que_podem_sair(cfg))
    chave = "configurada" if chave_configurada else "não configurada"
    frase = (f"Provedor externo {NOME}: decisões por conjunto fechado dos consumidores {consumidores}; dados das classes "
             f"{classes} podem sair para a TypeSafe (ids e categorias, nunca texto livre de persona nem tela sensível). "
             f"Chave da TypeSafe: {chave}.")
    if not privacidade.JEV_RUNTIME_SEND_APPROVED:
        frase += " Envio ainda FECHADO no código (JEV_RUNTIME_SEND_APPROVED): nada sai enquanto o dono não aprovar."
    return frase

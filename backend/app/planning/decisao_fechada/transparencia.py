"""Transparência da porta `DecisaoFechada` em `GET /api/ai` (item 31.5, ADR-069 item 8; roteiro §3 item 9).

Com `ai.decisao_fechada.enabled` e algum consumidor em `shadow` ou `on`, o `notice` passa a NOMEAR a TypeSafe e as classes de
dado que podem sair, e `/api/ai` lista o Jev. A chave aparece só como configurada ou não configurada, e quem chama passa um
booleano de PRESENÇA (`typesafe_api_key is not None`): este módulo nunca vê o valor.

Verdade antes de conforto (31.17): o aviso diz qual decisor está na porta (`nulo` ou `jev`) e só afirma que algo SAI quando
as quatro condições valem juntas: o envio aprovado no código (`JEV_RUNTIME_SEND_APPROVED`), o decisor real (`jev`), um
consumidor em `shadow` ou `on` e a chave configurada. Faltando uma, diz que nada sai e por quê, em vez de prometer uma
exposição que não acontece ou de esconder a que o YAML anuncia.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import privacidade

if TYPE_CHECKING:
    from ...config import DecisaoFechadaCfg

NOME = "Jev (TypeSafe System One)"
#: Quem manda mais que ids e categorias, e o quê: a intenção (31.9) leva o catálogo do dono (nomes e descrições, C2) e o
#: comando já sanitizado (C3). A C3 sai só de quem a privacidade libera (`C3_ORIGENS`, em `shadow`); as demais origens
#: (curador e os consumidores futuros) mandam C0/C1.
_ORIGENS_C2 = frozenset({"intencao"})


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


def o_que_sai(cfg: DecisaoFechadaCfg | None) -> list[str]:
    """O que de fato sai, em palavras, para os consumidores LIGADOS: sempre "ids e categorias" (C0 e C1) e, quando as classes
    efetivas (teto ∩ YAML) incluem C2 ou C3 e um consumidor ligado as manda, o catálogo do dono (C2) e o comando
    filtrado (C3: e-mail, telefone, @handle, link e número mascarados; o nome fica, ADR-069 item 10). O aviso não
    promete menos do que sai."""
    ativos = consumidores_ativos(cfg)
    classes = set(classes_que_podem_sair(cfg))
    saidas = ["ids e categorias"]
    if "C2" in classes and any(o in _ORIGENS_C2 for o in ativos):
        saidas.append("nomes e descrições do catálogo do dono")
    if "C3" in classes and any(o in privacidade.C3_ORIGENS and m in privacidade.C3_MODOS for o, m in ativos.items()):
        saidas.append("o comando do dono filtrado (e-mail, telefone, @handle, link e número mascarados; nome fica)")
    return saidas


def por_que_nada_sai(cfg: DecisaoFechadaCfg | None, *, chave_configurada: bool) -> str | None:
    """O primeiro motivo de nada sair, em palavras; `None` quando sai (as quatro condições do aviso valem)."""
    if not consumidores_ativos(cfg):
        return "nenhum consumidor em shadow ou on"
    if not privacidade.JEV_RUNTIME_SEND_APPROVED:
        return "envio FECHADO no código (JEV_RUNTIME_SEND_APPROVED)"
    if cfg is None or cfg.decisor != "jev":
        return "o decisor da porta é o nulo, que nunca chama a TypeSafe (ai.decisao_fechada.decisor: nulo)"
    if not chave_configurada:
        return "a chave da TypeSafe não está configurada"
    return None


def enviando(cfg: DecisaoFechadaCfg | None, *, chave_configurada: bool) -> bool:
    """Sai alguma coisa para a TypeSafe com esta configuração? Só com as quatro condições juntas (ADR-069 item 8, 31.17)."""
    return por_que_nada_sai(cfg, chave_configurada=chave_configurada) is None


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
            # 31.17: sai alguma coisa AGORA (código aberto, decisor `jev`, consumidor ligado e chave configurada)?
            "sending": enviando(cfg, chave_configurada=chave_configurada),
            "retention_days": None if cfg is None else cfg.retencao_dias}


#: Como o aviso nomeia a origem e o modo para quem lê o painel (o bloco `consumers` segue com os valores do YAML).
_ORIGEM_LEGIVEL = {"curador": "curador", "intencao": "intenção", "desempate": "desempate", "apps": "apps"}
_MODO_LEGIVEL = {"shadow": "em sombra", "on": "ligado"}


def aviso(cfg: DecisaoFechadaCfg | None, *, chave_configurada: bool) -> str | None:
    """Frase que se soma ao `notice` de `/api/ai`; `None` sem consumidor em `shadow` ou `on`."""
    ativos = consumidores_ativos(cfg)
    if not ativos:
        return None
    consumidores = ", ".join(f"{_ORIGEM_LEGIVEL.get(o, o)} {_MODO_LEGIVEL.get(m, m)}" for o, m in ativos.items())
    classes = ", ".join(classes_que_podem_sair(cfg))
    chave = "configurada" if chave_configurada else "não configurada"
    saidas = ", ".join(o_que_sai(cfg))
    decisor = "nulo" if cfg is None else cfg.decisor
    frase = (f"Provedor externo {NOME}: decisões por conjunto fechado ({consumidores}); dados das classes "
             f"{classes} podem sair para a TypeSafe ({saidas}; nunca texto livre de persona nem tela sensível). "
             f"Chave da TypeSafe: {chave}. Decisor na porta: {decisor}.")
    motivo = por_que_nada_sai(cfg, chave_configurada=chave_configurada)
    if motivo is None:
        frase += " Envio ATIVO: cada pedido que passa pela privacidade sai para a TypeSafe."
    else:
        frase += f" Nada sai agora: {motivo}."
    return frase

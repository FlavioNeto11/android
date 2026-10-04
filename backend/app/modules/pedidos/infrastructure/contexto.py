"""Item 28.10, F5 — o contexto de família que a porta de política lê (`PolicyEngine.check`, item 30.62).

A execução nasce de uma ocorrência de pedido (`runs.pedido_id`, 28.4). Daqui a porta sabe a que família ela pertence: a raiz,
as personas dos pedidos (a raiz e os filhos diretos; a profundidade máxima é 2) e quem é o porta-voz. Com isso ela trata a
família como UMA conta por alvo e exige aprovação para pessoa real sem conversa prévia (§9 "para fora").

Só leitura, sem texto livre: ids de pedido e de persona, nada de objetivo, título nem comando. O id de persona é o
`instagram_profiles.id`, o mesmo de `device_profile_bindings.profile_id` e de `objectives.profile_id` (o `profile_id` da
política). Execução sem pedido devolve `None`.

NÃO está ligado ao `AppState` ainda: a ligação vem depois do 30.62 (a porta ganha o parâmetro `pedido`). Enquanto isso, o
dataclass é local, com os MESMOS campos e nomes do `ContextoDoPedido` que o 30.62 põe em `app/social/policy.py`; depois dele,
isto vira `from app.social.policy import ContextoDoPedido`.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.db import Database, Row, loads

PORTA_VOZ = "porta_voz"


@dataclass(frozen=True)
class ContextoDoPedido:
    """`raiz`: o id do pedido raiz. `familia`: as personas (profile_id) de toda a família. `porta_voz`: a persona do porta-voz,
    ou `None` (sem porta-voz, ou com mais de uma persona nele: a porta que decida pelo mais restrito)."""
    raiz: str
    familia: frozenset[str]
    porta_voz: str | None


def _personas(db: Database, alvos_json: str | None) -> set[str]:
    """As personas de um pedido: a do alvo, ou a ÚNICA vinculada ao aparelho. Aparelho com zero ou várias fica de fora: nenhuma
    persona é inventada (a porta de frota olha o perfil real que agiu)."""
    v = loads(alvos_json, None)
    lista = (v.get("alvos") or v.get("targets") or []) if isinstance(v, dict) else (v if isinstance(v, list) else [])
    saida: set[str] = set()
    for a in lista:
        if not isinstance(a, dict):
            continue
        if a.get("profile_id"):
            saida.add(str(a["profile_id"]))
        elif a.get("instance_id"):
            ids = {str(r["profile_id"]) for r in db.query(
                "SELECT profile_id FROM device_profile_bindings WHERE instance_id=? AND active=1", (a["instance_id"],))}
            if len(ids) == 1:
                saida |= ids
    return saida


def _pedido_da_execucao(db: Database, run_id: str) -> Row | None:
    run = db.one("SELECT pedido_id, ocorrencia_id FROM runs WHERE id=?", (run_id,))
    if run is None:
        return None
    pedido_id = run["pedido_id"]
    if not pedido_id and run["ocorrencia_id"]:
        pedido_id = db.scalar("SELECT pedido_id FROM pedido_ocorrencias WHERE id=?", (run["ocorrencia_id"],))
    return db.one("SELECT id, pai_id FROM pedidos WHERE id=?", (pedido_id,)) if pedido_id else None


def contexto_do_pedido(db: Database, run_id: str) -> ContextoDoPedido | None:
    """A família da execução `run_id`, ou `None` se ela não nasceu de um pedido (ou o pedido sumiu)."""
    p = _pedido_da_execucao(db, run_id)
    if p is None:
        return None
    raiz = str(p["pai_id"] or p["id"])
    membros = db.query("SELECT id, alvos, papel, estado FROM pedidos WHERE id=? OR pai_id=? ORDER BY id", (raiz, raiz))
    familia: set[str] = set()
    porta_voz: set[str] = set()
    for m in membros:
        pessoas = _personas(db, m["alvos"])
        familia |= pessoas
        if m["papel"] == PORTA_VOZ and m["estado"] != "cancelado":
            porta_voz |= pessoas
    return ContextoDoPedido(raiz=raiz, familia=frozenset(familia),
                            porta_voz=next(iter(porta_voz)) if len(porta_voz) == 1 else None)

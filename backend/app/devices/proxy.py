"""Proxy HTTP do aparelho, distribuído como as versões de app (decisão do dono, 26/09).

Um proxy nomeado (`proxy_profiles`) é pedido para os aparelhos escolhidos, ou para todos. Quem está ligado recebe
agora, pela fila do aparelho e como comando acompanhável (`device.proxy`); quem está desligado recebe quando ligar,
no mesmo trabalho de reobservação que entrega os apps secundários.

O mecanismo é o proxy global do Android: `settings put global http_proxy host:porta`, e `:0` para tirar. A prova é
ler de volta (`settings get global http_proxy`) e comparar com o que foi pedido. **Essa leitura prova a
configuração, não o tráfego**: app que ignora o proxy do sistema continua saindo direto, e isso não aparece aqui.

Fora desta entrega, de propósito: proxy com usuário e senha (o proxy global do Android não tem campo de
autenticação, e credencial seria segredo, que não mora em coluna de texto) e a loja (Play Store), que não é destino.
Falha não é repetida sozinha: repetir é pedir de novo.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from ..commands import despacho
from ..models import InstanceState
from ..util import novo_id_de_app, now_iso

if TYPE_CHECKING:
    from ..state import AppState

log = logging.getLogger(__name__)

#: Nome de host ou IPv4. Nada de espaço, aspas ou `;`: o valor vai para dentro de um comando de shell no aparelho.
_HOST = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
#: O que o Android devolve quando não há proxy global (depende da versão e de como foi tirado).
_SEM_PROXY = {"", ":0", "null"}


class ProxyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    host: str = Field(pattern=_HOST, max_length=253)
    port: int = Field(ge=1, le=65535)


class ProxyApplyBody(BaseModel):
    """`proxy_id` nulo = tirar o proxy. Para quem: `instance_ids`, OU `all: true` para o parque inteiro (fora a
    loja). O parque inteiro nunca é inferido da falta de lista: um proxy fora do ar derruba a internet de TODAS as
    contas de uma vez, e isso tem de ser dito de propósito."""

    model_config = ConfigDict(extra="forbid")
    proxy_id: str | None = Field(default=None, max_length=80)
    instance_ids: list[str] | None = Field(default=None, max_length=200)
    all: bool = False
    dry_run: bool = False


class ProxyError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# ============================================================================ perfis
def _perfil_dto(row: Any, em_uso: int) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "host": row["host"], "port": row["port"],
            "created_at": row["created_at"], "created_by": row["created_by"], "devices": em_uso}


def _valor(perfil: Any | None) -> str:
    return f"{perfil['host']}:{perfil['port']}" if perfil is not None else ":0"


def listar(state: AppState) -> dict[str, Any]:
    """Perfis e o estado de cada aparelho do parque (a loja fica de fora)."""
    db = state.db
    uso = {r["desired_proxy_id"]: r["n"] for r in db.query(
        "SELECT desired_proxy_id, COUNT(*) AS n FROM device_proxy_state WHERE desired_proxy_id IS NOT NULL"
        " GROUP BY desired_proxy_id")}
    perfis = [_perfil_dto(r, uso.get(r["id"], 0)) for r in db.query("SELECT * FROM proxy_profiles ORDER BY name")]
    linhas = {r["instance_id"]: r for r in db.query("SELECT * FROM device_proxy_state")}
    aparelhos = []
    for rt in sorted(state.devices.devices.values(), key=lambda r: r.id):
        if rt.store:
            continue
        r = linhas.get(rt.id)
        aparelhos.append({
            "instance_id": rt.id, "worker_id": rt.worker_id, "device_state": rt.state.value,
            "managed": r is not None,
            "desired_proxy_id": r["desired_proxy_id"] if r else None,
            "observed_value": r["observed_value"] if r else None,
            "state": r["state"] if r else None,
            "detail": r["detail"] if r else None,
            "verified_at": r["verified_at"] if r else None,
        })
    return {"profiles": perfis, "devices": aparelhos}


def criar(state: AppState, body: ProxyInput, quem: str | None) -> dict[str, Any]:
    pid = novo_id_de_app(state.db, f"proxy {body.name}", tabela="proxy_profiles")
    state.db.execute("INSERT INTO proxy_profiles(id, name, host, port, created_at, created_by) VALUES (?,?,?,?,?,?)",
                     (pid, body.name.strip(), body.host, body.port, now_iso(), quem))
    return _perfil_dto(state.db.one("SELECT * FROM proxy_profiles WHERE id=?", (pid,)), 0)


def remover(state: AppState, proxy_id: str) -> None:
    if state.db.one("SELECT id FROM proxy_profiles WHERE id=?", (proxy_id,)) is None:
        raise ProxyError(404, "not_found", "Proxy não encontrado.")
    em_uso = [r["instance_id"] for r in state.db.query(
        "SELECT instance_id FROM device_proxy_state WHERE desired_proxy_id=?", (proxy_id,))]
    if em_uso:
        # Apagar o perfil deixaria os aparelhos com um proxy que ninguém sabe qual é: primeiro troque ou tire.
        raise ProxyError(409, "proxy_in_use", "Este proxy está pedido para " + ", ".join(sorted(em_uso))
                         + ". Tire-o desses aparelhos (ou troque por outro) antes de apagar.")
    state.db.execute("DELETE FROM proxy_profiles WHERE id=?", (proxy_id,))


# ============================================================================ pedir e aplicar
def _gravar(st: AppState, instance_id: str, **campos: Any) -> None:
    # `st`, não `state`: `state` é também uma COLUNA gravada por aqui (`_gravar(..., state="applied")`).
    campos["updated_at"] = now_iso()
    if st.db.one("SELECT instance_id FROM device_proxy_state WHERE instance_id=?", (instance_id,)) is None:
        cols = ["instance_id", *campos]
        st.db.execute(f"INSERT INTO device_proxy_state({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         (instance_id, *campos.values()))
    else:
        st.db.execute(f"UPDATE device_proxy_state SET {', '.join(f'{k}=?' for k in campos)} WHERE instance_id=?",
                         (*campos.values(), instance_id))
    st.bus.emit("proxy.updated", f"{instance_id}: proxy {campos.get('state', '')}".strip(), instance_id=instance_id,
                   data={"instance_id": instance_id})


def aplicar(state: AppState, body: ProxyApplyBody) -> list[dict[str, Any]]:
    """Pede o proxy (ou a ausência dele) para os aparelhos. Com `dry_run`, só diz o que aconteceria."""
    perfil = None
    if body.proxy_id is not None:
        perfil = state.db.one("SELECT * FROM proxy_profiles WHERE id=?", (body.proxy_id,))
        if perfil is None:
            raise ProxyError(404, "not_found", "Proxy não encontrado.")
    parque = {rt.id: rt for rt in state.devices.devices.values() if not rt.store}
    if (body.instance_ids is None) == (not body.all):
        raise ProxyError(400, "target_required", "Diga para quem: os aparelhos (`instance_ids`) ou o parque inteiro "
                                                 "(`all: true`) — um dos dois, nunca os dois nem nenhum.")
    if body.instance_ids is not None:
        pedidos = list(dict.fromkeys(body.instance_ids))
        fora = [i for i in pedidos if i not in parque]
        if not pedidos or fora:
            raise ProxyError(400, "unknown_instance", "Não dá para configurar o proxy em "
                             + (", ".join(fora) if fora else "nenhum aparelho")
                             + ": escolha aparelhos do parque (a loja não entra).")
        alvos = [parque[i] for i in pedidos]
    else:
        alvos = sorted(parque.values(), key=lambda r: r.id)
    descricao = f"proxy {perfil['name']} ({_valor(perfil)})" if perfil is not None else "sem proxy"
    saida: list[dict[str, Any]] = []
    for rt in alvos:
        online = rt.state == InstanceState.online
        if body.dry_run:
            if online:
                saida.append({"id": rt.id, "outcome": "would_start", "reason": f"ligado: recebe {descricao} agora"})
            elif rt.external:
                saida.append({"id": rt.id, "outcome": "pending",
                              "reason": f"em outro servidor e {rt.state.value}: recebe quando ele voltar"})
            else:
                saida.append({"id": rt.id, "outcome": "pending", "reason": f"{rt.state.value}: recebe quando ligar"})
            continue
        _gravar(state, rt.id, desired_proxy_id=body.proxy_id, state="pending",
                detail=f"{descricao} pedido; ainda não aplicado")
        if not online:
            saida.append({"id": rt.id, "outcome": "pending", "reason": f"{rt.state.value}: recebe quando ligar"})
            continue
        cmd = despacho._despachar_trabalho(state, rt, "device.proxy", lambda rt=rt: aplicar_no_aparelho(state, rt),
                                           label="configuração do proxy", params={"proxy_id": body.proxy_id},
                                           recusar_ocupado=False,
                                           ocupado="ocupado agora: recebe na próxima vez que ligar")
        if cmd.get("accepted"):
            saida.append({"id": rt.id, "outcome": "started", "reason": "aplicando agora",
                          "command_id": cmd["command_id"]})
        else:
            saida.append({"id": rt.id, "outcome": "pending", "reason": cmd["reason"], "command_id": cmd["command_id"]})
    if not body.dry_run:
        state.bus.emit("log", f"Proxy: {descricao} pedido para " + ", ".join(f"{d['id']}={d['outcome']}" for d in saida))
    return saida


def proxy_pendente(state: AppState, rt: Any) -> bool:
    """Há proxy pedido e ainda não aplicado neste aparelho? `applying` sem desfecho (o backend caiu no meio) conta:
    gravar e reler a configuração é idempotente. `failed` não: repetir é decisão de uma pessoa."""
    if rt.store:
        return False
    row = state.db.one("SELECT state FROM device_proxy_state WHERE instance_id=?", (rt.id,))
    return row is not None and row["state"] in ("pending", "applying")


async def aplicar_no_aparelho(state: AppState, rt: Any) -> dict[str, Any]:
    """Grava o proxy pedido e lê de volta. Só `applied` quando o que o aparelho responde é o que foi pedido."""
    row = state.db.one("SELECT * FROM device_proxy_state WHERE instance_id=?", (rt.id,))
    if row is None:
        raise ProxyError(409, "nothing_requested", f"{rt.id}: nenhum proxy foi pedido para este aparelho.")
    perfil = (state.db.one("SELECT * FROM proxy_profiles WHERE id=?", (row["desired_proxy_id"],))
              if row["desired_proxy_id"] else None)
    if row["desired_proxy_id"] and perfil is None:
        _gravar(state, rt.id, state="failed", detail="o proxy pedido não existe mais")
        raise ProxyError(409, "proxy_missing", f"{rt.id}: o proxy pedido não existe mais.")
    valor = _valor(perfil)
    aplicando = row["desired_proxy_id"]
    _gravar(state, rt.id, state="applying", detail=f"gravando {valor if perfil else 'sem proxy'}")
    try:
        await rt.executor.run(rt.adb.shell, f"settings put global http_proxy {valor}", timeout=40,
                              label="gravar o proxy")
        lido = str(await rt.executor.run(rt.adb.shell, "settings get global http_proxy", timeout=40,
                                         label="ler o proxy") or "").strip()
    except Exception as exc:
        _fechar(state, rt.id, aplicando, state="failed", detail=f"o aparelho não respondeu: {exc}"[:300])
        raise
    confere = (lido in _SEM_PROXY) if perfil is None else (lido == valor)
    if not confere:
        _fechar(state, rt.id, aplicando, state="failed", observed_value=lido,
                detail=f"pedido {valor}, o aparelho responde '{lido or 'vazio'}'")
        raise ProxyError(502, "proxy_not_applied", f"{rt.id}: pedido {valor}, o aparelho responde '{lido}'.")
    _fechar(state, rt.id, aplicando, state="applied", observed_value=lido, verified_at=now_iso(),
            detail=("configuração lida de volta do aparelho" if perfil is None
                    else f"{valor} lido de volta do aparelho (prova a configuração, não o tráfego)"))
    return {"instance_id": rt.id, "proxy": valor if perfil else None, "observed": lido}


def _fechar(st: AppState, instance_id: str, aplicado: str | None, **campos: Any) -> None:
    """Grava o desfecho de UMA aplicação — a menos que o pedido tenha mudado enquanto ela rodava.

    Pedir o proxy B com o A sendo aplicado grava B como desejado e fica `pending` (o aparelho está ocupado). Se o
    fim do trabalho do A gravasse `applied` por cima, a linha diria "aplicado" com B pedido e A no aparelho, e
    ninguém mais aplicaria o B (a varredura só retoma `pending`/`applying`). Então: pedido mudou → a linha volta a
    `pending`, com o que o aparelho respondeu, e a próxima passada aplica o pedido novo. Sem `await` entre a leitura
    e a escrita: no laço de eventos, nenhum outro pedido entra no meio."""
    atual = st.db.one("SELECT desired_proxy_id FROM device_proxy_state WHERE instance_id=?", (instance_id,))
    if atual is not None and atual["desired_proxy_id"] != aplicado:
        observado = {"observed_value": campos["observed_value"]} if "observed_value" in campos else {}
        _gravar(st, instance_id, state="pending", **observado,
                detail="o pedido mudou enquanto o anterior era aplicado; o novo entra na próxima passada")
        return
    _gravar(st, instance_id, **campos)

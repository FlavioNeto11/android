"""A rede por aparelho (`/api/network/*`, ADR-056, item 25.2): os perfis de VPN e de proxy, a atribuição a cada aparelho, aplicar,
reaplicar e verificar, e o servidor (o endereço da LAN e a conferência do Firewall). Saíram de `api.py` no 15.15 F4 (corte 6, F4f)
sem mudar caminho, método, corpo nem resposta.

Montado em `main.py` no MESMO lugar em que `api.router` entra. Este módulo não importa `app.api` (ciclo): o estado é
`request.app.state.poc` (`AppState` só em `TYPE_CHECKING`) e o "quem" vem de `comum.py`. O segredo de um perfil chega UMA vez em
`POST /network/profiles`, vai ao cofre e não volta (a resposta diz só `has_secret`); o 422 sai sem `input` nem `ctx`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError

from app.devices import rede
from app.modules.fleet.presentation.comum import quem

if TYPE_CHECKING:
    from app.state import AppState

router = APIRouter(prefix="/api")


def _st(request: Request) -> AppState:
    state: AppState = request.app.state.poc
    return state


def _err(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def rede_error(exc: rede.RedeError) -> HTTPException:
    return _err(exc.status, exc.code, exc.message, **exc.extra)


@router.get("/network/profiles")
async def list_network_profiles(request: Request) -> dict[str, list[dict[str, object]]]:
    return rede.listar_perfis(_st(request))


@router.post("/network/profiles", status_code=201)
async def create_network_profile(request: Request) -> rede.NetworkProfileDTO:
    """Perfil de VPN ou de proxy. O segredo chega aqui UMA vez, vai ao cofre e nunca volta: a resposta diz só
    `has_secret`. O corpo é lido à mão (`rede.ler_cadastro`) para o segredo sair antes da validação — o 422 padrão
    devolveria o corpo inteiro como `input` num campo faltando. Os erros saem sem `input` nem `ctx`."""
    try:
        corpo = await request.json()
    except ValueError:
        raise _err(422, "invalid_body", "O corpo precisa ser JSON.") from None
    try:
        body = rede.ler_cadastro(corpo)
    except ValidationError as exc:
        erros = [{"type": e["type"], "loc": ["body", *e["loc"]], "msg": e["msg"]} for e in exc.errors()]
        raise HTTPException(status_code=422, detail=erros) from None
    except ValueError as exc:
        raise _err(422, "invalid_secret" if "segredo" in str(exc) else "invalid_body", str(exc)) from None
    try:
        return rede.criar_perfil(_st(request), body, quem(request))
    except rede.RedeError as exc:
        raise rede_error(exc) from exc


@router.put("/network/profiles/{profile_id}")
async def update_network_profile(request: Request, profile_id: str, body: rede.NetworkProfileSaidaBody) -> rede.NetworkProfileDTO:
    """A única edição de perfil: a saída esperada (`egress_esperado`, `egress_esperado_ipv6`; `null` tira). Emite
    `network.updated` `perfil_atualizado` com o antes e o depois. 409 `egress_esperado_protegido` no perfil de uma conta
    do igfarm em uso, a não ser que o corpo traga `motivo`. Nome, endpoint, protocolo e segredo não mudam por aqui."""
    try:
        return rede.atualizar_saida_esperada(_st(request), profile_id, body, quem(request))
    except rede.RedeError as exc:
        raise rede_error(exc) from exc


@router.delete("/network/profiles/{profile_id}", status_code=204)
async def delete_network_profile(request: Request, profile_id: str) -> Response:
    """409 `network_profile_in_use` com os aparelhos que o usam: troque ou tire o perfil deles antes."""
    try:
        rede.remover_perfil(_st(request), profile_id)
    except rede.RedeError as exc:
        raise rede_error(exc) from exc
    return Response(status_code=204)


@router.get("/network/devices")
async def list_network_devices(request: Request) -> dict[str, object]:
    """Desejado × observado por aparelho, com o proxy legado da 041 rebaixado a `configurado` no máximo, `egress_home`
    por aparelho e a saída medida do central (`central_egress`, item 29.20)."""
    return rede.listar_aparelhos(_st(request))


@router.post("/network/assign")
async def assign_network(request: Request, body: rede.NetworkAssignBody) -> dict[str, object]:
    """Atribui em lote (ou a um aparelho). `dry_run` = prévia, nada gravado; sem ele, tudo ou nada (409 com a prévia)."""
    try:
        return rede.atribuir(_st(request), body, quem(request))
    except rede.RedeError as exc:
        raise rede_error(exc) from exc


@router.post("/network/devices/{instance_id}/verify", status_code=202)
async def verify_network(request: Request, instance_id: str) -> dict[str, object]:
    """Registra o pedido de medir de novo (202, `executed: false`): a sonda de saída (25.5) mede de dentro do
    aparelho no próximo ponto seguro dele (varredura, porta da tarefa) ou já, por `POST …/apply`."""
    try:
        return _st(request).rede_convergencia.pedir_verificacao(instance_id, quem(request))
    except rede.RedeError as exc:
        raise rede_error(exc) from exc


@router.post("/network/devices/{instance_id}/reapply", status_code=202)
async def reapply_network(request: Request, instance_id: str) -> dict[str, object]:
    """Registra a reaplicação como revisão nova (202). Quem aplica é a convergência (25.4), no próximo ponto seguro;
    para aplicar já, `POST …/apply`."""
    try:
        return rede.pedir_reaplicacao(_st(request), instance_id, quem(request))
    except rede.RedeError as exc:
        raise rede_error(exc) from exc


@router.post("/network/devices/{instance_id}/apply", status_code=202)
async def apply_network(request: Request, instance_id: str) -> dict[str, object]:
    """O passo que falta à rede deste aparelho (aplicar, reiniciar e conectar, medir, conferir ou desfazer), JÁ, pela fila
    do aparelho e como comando `device.network` (25.4). Fora do ar: aplica quando ligar. Ocupado: 409 `device_busy`."""
    try:
        return _st(request).rede_convergencia.aplicar_agora(instance_id, quem(request))
    except rede.RedeError as exc:
        raise rede_error(exc) from exc


@router.get("/network/server")
async def network_server(request: Request) -> dict[str, object]:
    """O servidor sing-box do central (25.4): se roda, os pares (aparelho, endereço no túnel, chave PÚBLICA, última
    conexão no log) e os usuários do proxy. Sem segredo nenhum: nem chave privada, nem senha, nem a configuração."""
    return _st(request).rede_servidor.status()


@router.post("/network/server/firewall-check")
async def network_server_firewall_check(request: Request) -> dict[str, object]:
    """Relê JÁ o Firewall do Windows do central para os aparelhos de outra máquina (25.7) e devolve o
    `remote_access`: endpoint da LAN, aparelhos remotos, estado e o comando que o DONO roda. Só leitura — a plataforma
    nunca cria, muda ou desliga regra."""
    servidor = _st(request).rede_servidor
    await servidor.conferir_acesso_remoto(forcar=True)
    return servidor.acesso_remoto()

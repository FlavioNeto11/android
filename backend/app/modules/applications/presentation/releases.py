"""As releases de aplicativo (`/api/releases*`): listar, importar da pasta de entrada, o ícone, para onde cada versão pode ir,
receber um arquivo do conjunto, aprovar a assinatura e o ciclo de vida (promover, voltar). Saíram de `api.py` no 15.15 F4
(corte 4, F4d) sem mudar caminho, método, corpo nem resposta.

Montado em `main.py` no MESMO lugar em que `api.router` entra. Este módulo não importa `app.api` (ciclo): o estado é
`request.app.state.poc` (`AppState` só em `TYPE_CHECKING`) e o aparelho e a recusa da loja vêm de `comum.py`.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.commands.despacho import _despachar_trabalho
from app.devices.compatibilidade import (capacidades_de, motivo_do_renderizador, motivo_incompativel,
                                         requisitos_de_release)
from app.models import InstanceState, ReleaseChannel, ReleaseImportBody, ReleaseLifecycleBody, SignatureApprovalBody
from app.modules.applications.presentation.comum import device, recusa_loja_como_alvo
from app.releases.catalog import ReleaseValidationError
from app.vitrine import _apps_changed, convergir_o_parque

if TYPE_CHECKING:
    from app.state import AppState

log = logging.getLogger("poc.api")
router = APIRouter(prefix="/api")


def _st(request: Request) -> AppState:
    state: AppState = request.app.state.poc
    return state


def _err(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


@router.get("/releases", response_model=None)
async def list_releases(request: Request, package: str | None = None) -> object:
    return _st(request).releases.list_releases(package)


@router.post("/releases/import", status_code=202, response_model=None)
async def import_releases(request: Request, body: ReleaseImportBody | None = None) -> object:
    """Varre a pasta de entrada, inspeciona cada conjunto com as ferramentas do SDK e cataloga os aprovados.
    O nome do arquivo não decide nada: pacote, versão, splits e assinatura vêm do próprio pacote."""
    s = _st(request)
    body = body or ReleaseImportBody()
    try:
        results = await asyncio.to_thread(
            s.releases.import_inbox, source_reference=body.source_reference, expected_package=body.expected_package)
    except ReleaseValidationError as exc:
        raise _err(400, "import_failed", str(exc)) from exc
    return {"imported": [r.to_dict() for r in results]}


@router.get("/releases/{release_id}/icon", response_model=None)
async def release_icon(request: Request, release_id: str) -> object:
    """O ícone do launcher extraído do próprio APK. 404 quando a release não tem ícone servível.

    É o que faz o catálogo mostrar o aplicativo em vez de mostrar uma string de pacote. `immutable`: a pasta da
    release é imutável por construção (o caminho vem do hash do conjunto), então o navegador pode guardá-lo.
    """
    dados = _st(request).releases.icon_bytes(release_id)
    if dados is None:
        raise _err(404, "sem_icone", "Esta versão não tem ícone extraído.")
    conteudo, tipo = dados
    return Response(content=conteudo, media_type=tipo, headers={"Cache-Control": "private, max-age=86400, immutable"})


@router.get("/releases/{release_id}/targets", response_model=None)
async def release_targets(request: Request, release_id: str) -> object:
    """Para onde ESTA versão pode ir, aparelho por aparelho, com o motivo de quem não pode.

    A incompatibilidade é decidida AQUI, com a mesma função que recusa a instalação (`motivo_incompativel`), e
    não reimplementada na interface: um segundo julgamento em TypeScript ficaria desatualizado no primeiro
    ajuste de regra, e a tela prometeria o que o backend recusa. Serve o diálogo "Instalar em…".
    """
    s = _st(request)
    release = s.release_repo.release_row(release_id)
    if release is None:
        raise _err(404, "not_found", "Release não encontrada.")
    requisitos = requisitos_de_release(release)
    package = release["package_name"]
    alvos: list[dict[str, object]] = []
    for rt in s.devices.devices.values():
        if rt.store:
            continue                       # a loja é a FONTE do aplicativo, nunca destino — mesma regra de `distribute`
        porque = motivo_incompativel(requisitos, capacidades_de(rt), aparelho=rt.id)
        linha = s.release_repo.app_state(rt.id, package)
        alvos.append({
            "id": rt.id,
            #: Onde o aparelho está. É por isto que o diálogo agrupa por servidor: "instalar em 6 aparelhos" com
            #: 243 MB indo pelo túnel para outra máquina não é a mesma decisão que instalar nos daqui.
            "worker_id": rt.worker_id,
            "state": rt.state.value,
            "compatible": porque is None,
            "reason": porque,
            "app_state": linha["state"] if linha else None,
            "installed_release_id": linha["installed_release_id"] if linha else None,
            "installed_version_name": linha["observed_version_name"] if linha else None,
            "already": bool(linha and linha["installed_release_id"] == release_id
                            and linha["state"] in ("ready", "installed")),
        })
    return {"release_id": release_id, "package": package, "targets": sorted(alvos, key=lambda a: a["id"])}


#: Teto de um arquivo enviado pelo painel. O conjunto do Instagram passa de 240 MB somando os splits, e cada
#: arquivo vem numa requisição: 512 MB dá folga para o maior base.apk sem deixar um POST solto encher o disco.
UPLOAD_MAX_BYTES = 512 * 1024 * 1024
#: Nome de conjunto aceito na URL. O arquivo vai para `apks/inbox/<conjunto>/`, então isto é o que impede
#: `../` de virar escrita em qualquer lugar do disco.
_NOME_DE_CONJUNTO = re.compile(r"^[A-Za-z0-9._-]{1,60}$")
#: `.xapk`/`.apks`/`.apkm` desde a loja de apps (26/09): é o formato em que o dono costuma ter o arquivo. O
#: contêiner é extraído e passa pela mesma inspeção de um `.apk`.
_NOME_DE_ARQUIVO_APK = re.compile(r"^[A-Za-z0-9._-]{1,120}\.(apk|apks|xapk|apkm)$", re.IGNORECASE)


@router.post("/releases/upload", status_code=201, response_model=None)
async def upload_release(request: Request, filename: str = Query(..., min_length=5, max_length=120),
                         set_id: str = Query(..., min_length=1, max_length=60),
                         final: bool = False, source_reference: str | None = None) -> object:
    """Recebe UM arquivo do conjunto e, com `final=true`, importa a pasta inteira.

    Existe porque até aqui a única entrada de APK era largar arquivo na pasta do servidor — quem abre o painel de
    outra máquina não tinha caminho nenhum. O corpo é o arquivo cru (`application/octet-stream`), não multipart:
    um conjunto de splits chega arquivo a arquivo, com o mesmo `set_id`, e só o último manda importar.

    O arquivo enviado passa exatamente pela MESMA inspeção da pasta de entrada — pacote, versão, splits,
    assinatura e ABIs saem do próprio APK, e a assinatura continua precisando de aprovação explícita. Enviar não
    instala nada.

    Um envio que nunca recebe o `final=true` (a aba fechou no meio) deixa `apks/inbox/upload-<set_id>/` no
    servidor. É de propósito: a pasta de entrada é exatamente onde um conjunto incompleto deve ficar esperando —
    "Importar da pasta" o encontra e o reprova com o motivo, em vez de o arquivo sumir em silêncio.
    """
    s = _st(request)
    if not _NOME_DE_CONJUNTO.match(set_id):
        raise _err(400, "set_id_invalido", "O identificador do conjunto aceita letras, números, ponto, hífen e _.")
    # O nome é validado COMO VEIO, não reduzido ao básico: aceitar `../../x.apk` e gravar `x.apk` em silêncio
    # esconderia de quem chamou que o caminho foi ignorado. `_NOME_DE_ARQUIVO_APK` não admite barra nenhuma.
    nome = filename.strip()
    if not _NOME_DE_ARQUIVO_APK.match(nome) or nome != PurePosixPath(nome).name:
        raise _err(400, "nome_invalido",
                  f"'{filename}' não é um nome de APK aceito (.apk, .apks, .xapk ou .apkm, sem caminho).")
    pasta = s.cfg.apk_inbox / f"upload-{set_id}"
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / nome
    escrito = 0
    try:
        with open(destino, "wb") as fh:
            async for pedaco in request.stream():
                escrito += len(pedaco)
                if escrito > UPLOAD_MAX_BYTES:
                    raise _err(413, "arquivo_grande",
                              f"O arquivo passou de {UPLOAD_MAX_BYTES // (1024 * 1024)} MB.")
                fh.write(pedaco)
    except HTTPException:
        destino.unlink(missing_ok=True)
        raise
    if escrito == 0:
        destino.unlink(missing_ok=True)
        raise _err(400, "arquivo_vazio", "O corpo da requisição veio vazio.")
    if not final:
        return {"stored": nome, "size_bytes": escrito, "set_id": set_id, "imported": None}
    try:
        resultado = await asyncio.to_thread(
            s.releases.import_dir, pasta, source_type="upload",
            source_reference=source_reference or f"enviado pelo painel ({set_id})", expected_package=None)
    except ReleaseValidationError as exc:
        raise _err(400, "import_failed", str(exc)) from exc
    finally:
        shutil.rmtree(pasta, ignore_errors=True)
    return {"stored": nome, "size_bytes": escrito, "set_id": set_id, "imported": resultado.to_dict()}


@router.post("/releases/{release_id}/approve-signature", response_model=None)
async def approve_signature(request: Request, release_id: str, body: SignatureApprovalBody | None = None) -> object:
    """Aprovação explícita do operador. Depois dela, release com assinatura diferente é bloqueada sozinha."""
    try:
        return _st(request).releases.approve_signature(release_id, note=(body.note if body else None))
    except ReleaseValidationError as exc:
        raise _err(404, "not_found", str(exc)) from exc


@router.post("/releases/{release_id}/lifecycle", response_model=None)
async def release_lifecycle(request: Request, release_id: str, body: ReleaseLifecycleBody) -> object:
    """Canário, promoção, quarentena e rollback numa rota só, com um verbo por chamada.

    `promote` e `quarantine` são decisões de banco e respondem na hora. `canary` e `rollback` mexem no aparelho:
    são aceitos aqui, rodam pela fila do aparelho e o resultado aparece em `GET /api/app-state`.
    """
    s = _st(request)
    release = s.release_repo.release_row(release_id)
    if release is None:
        raise _err(404, "not_found", "Release não encontrada.")
    try:
        # Promover e quarentenar mudam a versão que "Instalar <app> <versão>" mostra: a lista de apps é republicada.
        if body.verb == "promote":
            feito = s.releases.promote(release_id, note=body.note)
            _apps_changed(s)
            # ADR-026 ("todos devem ficar atualizados sempre"): cada aparelho que TEM o app passa a perseguir a
            # promovida — o ligado e livre instala já, o ocupado na varredura, o desligado quando ligar. Não liga
            # ninguém. `devices` diz, aparelho por aparelho, o que vai acontecer; `target_release_id` é a versão que
            # o parque persegue (promover uma versão MENOR que a promovida não muda o alvo).
            try:
                convergencia = convergir_o_parque(s, release["package_name"])
            except Exception as exc:  # noqa: BLE001 - a promoção JÁ valeu no banco: um 500 aqui faria quem chamou
                # repetir e levar 409 ("só promove quem está em canário"). A varredura de 60 s e o "entrou no ar"
                # adotam a promovida do mesmo jeito; a resposta diz que a convergência imediata não aconteceu.
                log.exception("convergência do parque depois de promover %s", release_id)
                convergencia = {"target_release_id": None, "devices": [],
                                "convergence_error": f"a convergência imediata falhou ({exc}); a varredura de 60 s "
                                                     "e a entrada no ar entregam a versão do mesmo jeito"}
            return {"accepted": True, "release": feito, **convergencia}
        if body.verb == "quarantine":
            feito = s.releases.quarantine(release_id, reason=body.note)
            _apps_changed(s)
            return {"accepted": True, "release": feito}
        if body.verb == "distribute":
            # Sem alvo, vale para o parque inteiro. A resposta diz, aparelho por aparelho, se a instalação começou já
            # ou ficou pendente para quando ele entrar em serviço. `dry_run` é a prévia: nada é gravado.
            devices = s.distribute(release_id, eager=body.eager, instance_ids=body.instance_ids, count=body.count,
                                   dry_run=body.dry_run)
            return {"accepted": not body.dry_run, "dry_run": body.dry_run, "eager": body.eager, "devices": devices}
    except ReleaseValidationError as exc:
        raise _err(409, "lifecycle_refused", str(exc)) from exc

    if not body.instance_id:
        raise _err(400, "instance_required", f"O verbo '{body.verb}' precisa do aparelho (`instance_id`).")
    rt = device(s, body.instance_id)
    recusa_loja_como_alvo(rt)
    if rt.state != InstanceState.online:
        raise _err(409, "not_online", "O aparelho precisa estar online.")
    package = release["package_name"]
    # Requisito de renderizador do app (29.11), para o canário e para a volta: os dois instalam e ABREM o app, e o
    # trabalho roda em segundo plano — a recusa do serviço viraria um 202 sem motivo. Mesmo código da instalação.
    if (porque := motivo_do_renderizador(requisitos_de_release(release), capacidades_de(rt), aparelho=rt.id)):
        raise _err(409, "app_incompativel", f"{porque[:1].upper()}{porque[1:]}.")

    if body.verb == "canary":
        # A mesma regra do serviço, conferida aqui: o trabalho roda em segundo plano, então uma recusa lá dentro
        # devolveria 202 e quem chamou nunca saberia por quê.
        if release["channel"] == ReleaseChannel.promoted.value:
            raise _err(409, "lifecycle_refused",
                      "Esta versão já foi promovida: instale-a normalmente. Para prová-la outra vez, coloque-a em "
                      "quarentena antes — assim a decisão de desfazer a promoção fica explícita.")
        trabalho = lambda: s.releases.start_canary(rt, release_id, s.installer)  # noqa: E731
        rotulo = "canário de APK"
        verbo = "app.canary"
    else:
        estado = s.release_repo.app_state(rt.id, package)
        if not (estado and estado["previous_release_id"]):
            raise _err(409, "no_previous_release",
                      f"{rt.id} não tem versão anterior registrada para {package}; não há para onde voltar.")
        # Preservar os dados é o padrão. Reinstalar apaga a sessão, então só acontece se quem chamou disser isso
        # de propósito — a API nunca escolhe esse caminho sozinha.
        preserve = not body.confirm_reinstall

        async def trabalho() -> object:
            resultado = await s.releases.rollback(rt, package, s.installer, preserve=preserve, note=body.note)
            # ADR-026: a versão de onde este aparelho saiu virou "substituída" para o parque inteiro. Quem está nela
            # (ou a esperava) volta para a promovida anterior já, em vez de esperar a varredura de 60 s.
            try:
                convergir_o_parque(s, package)
            except Exception:  # noqa: BLE001 - a volta deste aparelho já aconteceu; a varredura cobre o resto
                log.exception("convergência do parque depois da volta de %s em %s", package, rt.id)
            return resultado

        rotulo = "rollback de APK"
        verbo = "app.rollback"

    # Canário e rollback passam a ser COMANDOS: id acompanhável, estado honesto e `uncertain` quando o adb não
    # responde. Antes eram `202 {"accepted": true}` e o desfecho só aparecia recarregando `GET /api/app-state`.
    return {**_despachar_trabalho(s, rt, verbo, trabalho, label=rotulo,
                                  params={"release_id": release_id, "package": package},
                                  idempotency_key=body.idempotency_key),
            "release_id": release_id, "verb": body.verb}

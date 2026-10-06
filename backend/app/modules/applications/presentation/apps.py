"""Os aplicativos (`/api/apps*`, `/api/apps-overview`, `/api/app-store`, `/api/app-catalog`, `/api/app-state`): o cadastro, a visão por
app, o conhecimento declarado por pacote, a vitrine, o catálogo do registro e o estado por aparelho. Saíram de `api.py` no 15.15 F4
(corte 10, F4j) sem mudar caminho, método, corpo nem resposta.

Montado em `main.py` no MESMO lugar em que `api.router` entra, depois do das rotas de Instagram. Este módulo não importa `app.api` (ciclo): o estado é
`request.app.state.poc` (`AppState` só em `TYPE_CHECKING`).
"""
from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.apps_overview import app_detail, apps_overview
from app.integrations.app_declarado.prova import prova_do_pacote
from app.models import AppInput, AppPatch
from app.modules.applications.infrastructure.app_repository import AppRow, CamposDeApp
from app.planning.catalog import registered
from app.vitrine import _apps_changed, app_dto, apps_list, vitrine

if TYPE_CHECKING:
    from app.state import AppState

router = APIRouter(prefix="/api")


def _st(request: Request) -> AppState:
    state: AppState = request.app.state.poc
    return state


def _err(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


@router.get("/apps-overview", response_model=None)
async def apps_overview_route(request: Request, days: int = Query(7, ge=1, le=90)) -> object:
    """Item 12.2: um resumo por aplicativo — contas, aparelhos, execuções, custo de IA, receitas, fluxos, versões."""
    return apps_overview(_st(request), days)


@router.get("/apps/{app_id}/overview", response_model=None)
async def app_overview_route(request: Request, app_id: str, days: int = Query(30, ge=1, le=180)) -> object:
    detalhe = app_detail(_st(request), app_id, days)
    if detalhe is None:
        raise _err(404, "not_found", "Aplicativo não encontrado.")
    return detalhe


@router.get("/apps/{pacote}/conhecimento")
async def app_conhecimento_route(pacote: str) -> dict[str, object]:
    """RA-24: os YAML do conhecimento do app (`app/conhecimento/apps/<pacote>/`) com sha256 e o hash de blob do Git,
    que confere com `git rev-parse <commit>:<caminho>` sem abrir a máquina. O parâmetro é o PACOTE, e não o id do app."""
    prova = prova_do_pacote(pacote)
    if prova is None:
        raise _err(404, "not_found", "Nenhum conhecimento declarado para este pacote.")
    return asdict(prova)


@router.get("/apps", response_model=None)
async def list_apps(request: Request) -> object:
    return apps_list(_st(request))


def _validate_apk(state: AppState, apk_path: str | None) -> None:
    if apk_path:
        try:
            state.devices.resolve_apk(apk_path)
        except ValueError as exc:
            raise _err(400, "invalid_apk_path", str(exc)) from exc


@router.post("/apps", response_model=None)
async def create_app(request: Request, body: AppInput) -> object:
    s = _st(request)
    _validate_apk(s, body.apk_path)
    if s.apps.id_do_pacote(body.package) is not None:
        # Loja de apps: o pacote é a identidade que as versões, o estado por aparelho e a vitrine usam. Dois
        # cadastros do mesmo pacote dividiriam as contagens em dois cartões que falam do mesmo aplicativo.
        raise _err(409, "package_exists", f"O pacote {body.package} já está cadastrado.")
    app_id = s.apps.criar(name=body.name, package=body.package, activity=body.activity or None,
                          apk_path=body.apk_path or None, nav_hints=body.nav_hints or None,
                          known_selectors=body.known_selectors, category=body.category)
    _apps_changed(s)
    return app_dto(cast(AppRow, s.apps.obter(app_id)), s)      # acabou de ser gravado: a linha existe


@router.put("/apps/{app_id}", response_model=None)
async def update_app(request: Request, app_id: str, body: AppPatch) -> object:
    s = _st(request)
    if s.apps.obter(app_id) is None:
        raise _err(404, "not_found", "App não encontrado.")
    data = body.model_dump(exclude_unset=True)
    if data.get("package") and s.apps.id_do_pacote(data["package"], exceto=app_id) is not None:
        # Mesma regra do cadastro: dois apps com o mesmo pacote dividiriam a vitrine em dois cartões do mesmo app.
        raise _err(409, "package_exists", f"O pacote {data['package']} já está cadastrado em outro app.")
    _validate_apk(s, data.get("apk_path"))
    # Texto vazio vindo do formulário quer dizer "sem valor" (o repositório grava o que recebe).
    for k in ("activity", "apk_path", "nav_hints"):
        if k in data and not data[k]:
            data[k] = None
    s.apps.atualizar(app_id, cast(CamposDeApp, data))
    _apps_changed(s)
    return app_dto(cast(AppRow, s.apps.obter(app_id)), s)      # acabou de ser gravado: a linha existe


@router.delete("/apps/{app_id}", status_code=204)
async def delete_app(request: Request, app_id: str) -> Response:
    s = _st(request)
    row = s.apps.obter(app_id)
    if row is None:
        raise _err(404, "not_found", "App não encontrado.")
    if row["builtin"]:
        raise _err(409, "builtin", "O app de QA embutido não pode ser removido.")
    s.apps.remover(app_id)
    _apps_changed(s)
    for rt in s.devices.devices.values():
        s.devices.publish(rt)
    return Response(status_code=204)


@router.get("/app-store", response_model=None)
async def app_store(request: Request) -> object:
    """A vitrine da loja de apps: por app, ícone, versão promovida, aparelhos por versão e o que pede atenção."""
    return vitrine(_st(request))


@router.get("/app-catalog", response_model=None)
async def app_catalog(request: Request) -> object:
    """Os aplicativos que o registro conhece: quem tem catálogo, quem provê conta, quem exige perfil.

    É o que a interface usa para deixar de assumir um pacote por omissão — a loja, as capacidades e o painel de
    contas passam a perguntar "qual app?" em vez de cair no Instagram. `profile_anchor` (23.10) é o que o painel
    usa para achar o app da conta de cadastro da persona sem comparar nome ou pacote (`ehInstagram` fixo).
    """
    return [{"package": c.package, "name": c.name, "label": c.label, "has_catalog": c.has_catalog,
             "session_provider": c.session_provider, "needs_profile": c.needs_profile,
             "profile_anchor": c.profile_anchor}
            for c in registered()]


@router.get("/app-state", response_model=None)
async def app_state(request: Request, package: str | None = None) -> object:
    return _st(request).release_repo.list_app_state(package)

"""Os perfis do Instagram (`/api/instagram/*`): cadastro e edição, sessão (iniciar, encerrar, verificar), a conta âncora e as contas por perfil,
credencial, memória, grupos de política, a política por perfil, o avatar e o que cada uma dessas rotas usa só aqui (o job de logout e de início de
sessão, a recusa pelo portão, a exigência de internet no aparelho). Saíram de `api.py` no 15.15 F4 (corte 9, F4i) sem mudar caminho, método, corpo
nem resposta.

Montado em `main.py` no MESMO lugar em que `api.router` entra, depois do das personas. Este módulo não importa `app.api` (ciclo): o estado é
`request.app.state.poc` (`AppState` só em `TYPE_CHECKING`); o erro de social, o aparelho pelo id, o mime da chave e servir do storage vêm de
`comum.py` ao lado, que `api.py` também usa. Ficam em `api.py` as rotas que dividiam o mesmo bloco mas não são do Instagram (a loja, os proxies, as
exceções de política, as aprovações, o catálogo de apps e as capacidades).
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.commands.despacho import _despachar_trabalho
from app.contexto import contexto_do_aparelho
from app.devices import conectividade
from app.devices.manager import DeviceRuntime
from app.models import (
    CredentialClone,
    CredentialPrepare,
    CredentialUpdate,
    InstagramProfileDTO,
    InstanceState,
    MemoryCreate,
    PlannedAccountCreate,
    PolicyGroupCreate,
    PolicyGroupPatch,
    ProfileAccountCreate,
    ProfileAccountDTO,
    ProfileAccountPatch,
    ProfileCreate,
    ProfilePatch,
    ProfilePolicyPatch,
    ProvisioningEventBody,
    SessionStatus,
)
from app.modules.identity.application.ponte_igfarm import ErroDaPonte
from app.modules.identity.domain.ponte_igfarm import SENHAS_MASCARADAS, ComandoDeRegistro, PersonaPendente
from app.modules.identity.infrastructure.cadastro_guiado import CadastroGuiado
from app.modules.identity.infrastructure.ponte_igfarm import compor_ponte_igfarm
from app.modules.identity.presentation.schemas import (CabecalhoDaCaixaDTO, CabecalhosDaContaDTO, CicloDaContaDTO, CodigoDaContaDTO, ContatoDaContaDTO, ContaIgfarmBody, ContaRegistradaDTO,
                                                       EgressoDoDeviceDTO, PersonaPendenteDTO, SignupBody)
from app.modules.identity.presentation.comum import device, mime_da_chave, quem, servir_do_storage, social_error
from app.social.capacidades import capacidades_do_perfil
from app.social.policy import DEFAULT_LIMITS
from app.social.service import SocialError

if TYPE_CHECKING:
    from app.state import AppState

router = APIRouter(prefix="/api")


def _st(request: Request) -> AppState:
    state: AppState = request.app.state.poc
    return state


def _err(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


@router.get("/instagram/profiles", response_model=None)
async def list_profiles(request: Request) -> object:
    return _st(request).social.list_profiles()


@router.post("/instagram/profiles", status_code=201, response_model=None)
async def create_profile(request: Request, body: ProfileCreate) -> object:
    """Cadastro pelo portal. A senha entra aqui e vai direto para o cofre: nenhuma rota a devolve."""
    try:
        return _st(request).social.create_profile(body)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.get("/instagram/profiles/{profile_id}", response_model=None)
async def get_profile(request: Request, profile_id: str) -> object:
    try:
        return _st(request).social.get_profile(profile_id)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.patch("/instagram/profiles/{profile_id}", response_model=None)
async def patch_profile(request: Request, profile_id: str, body: ProfilePatch) -> object:
    try:
        return _st(request).social.update_profile(profile_id, body)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.delete("/instagram/profiles/{profile_id}", status_code=204)
async def delete_profile(request: Request, profile_id: str) -> Response:
    try:
        _st(request).social.delete_profile(profile_id)
    except SocialError as exc:
        raise social_error(exc) from exc
    return Response(status_code=204)


@router.get("/instagram/profiles/{profile_id}/avatar", response_model=None)
async def profile_avatar(request: Request, profile_id: str) -> object:
    """Foto do perfil. 404 quando não há. O painel só chama com `has_avatar` verdadeiro no DTO (29.26) e, sem ele, mostra
    as iniciais sem requisição; o 404 fica para quem chama sem olhar o campo."""
    s = _st(request)
    try:
        perfil = s.social.get_profile(profile_id)
    except SocialError as exc:
        raise social_error(exc) from exc
    # A imagem PRINCIPAL da pessoa (048) quando há; senão o jpg legado de `data/avatars/`. A chave sai do id JÁ
    # VALIDADO no banco (ou da linha da imagem), nunca do texto da URL: chave não se monta com entrada crua.
    principal = s.persona_images.principal(perfil.id)
    chave = principal.storage_key if principal is not None and principal.storage_key else f"avatars/{perfil.id}.jpg"
    return servir_do_storage(s.avatares, chave, mime_da_chave(chave),
                              ausente=("sem_foto", "Este perfil não tem foto cadastrada."))


# A Conta é a entidade única: as rotas de credencial, conectar, verificar, sair e tentativas são da CONTA
# (`/accounts/{account_id}/…`). As antigas, por perfil, viram apelidos que resolvem a conta âncora — a do app que
# provê a conta do perfil (`social_repo.app_package`) — e continuam devolvendo o perfil, como sempre.
def _conta_ancora_ou_409(s: AppState, profile_id: str) -> str:
    try:
        s.social.get_profile(profile_id)
    except SocialError as exc:
        raise social_error(exc) from exc
    # Leitura: não cria conta (um GET que insere linha seria uma surpresa); quem cria é guardar a senha.
    conta = s.social_repo.conta_ancora(profile_id)
    if conta is None:
        raise _err(409, "no_account", "Este perfil não tem conta no aplicativo que provê a conta dele.")
    return str(conta["id"])


@router.put("/instagram/profiles/{profile_id}/accounts/{account_id}/credential")
async def put_account_credential(request: Request, profile_id: str, account_id: str,
                                 body: CredentialUpdate) -> ProfileAccountDTO:
    """Só escrita, com o consentimento por conta (`consent: true`, senão 409 `consentimento_de_credencial`). O
    painel mostra apenas que existe uma credencial e quem consentiu, nunca o valor."""
    try:
        return _st(request).social.set_account_credential(profile_id, account_id, body, by=quem(request))
    except SocialError as exc:
        raise social_error(exc) from exc


@router.delete("/instagram/profiles/{profile_id}/accounts/{account_id}/credential")
async def delete_account_credential(request: Request, profile_id: str, account_id: str) -> ProfileAccountDTO:
    try:
        return _st(request).social.delete_account_credential(profile_id, account_id)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.post("/instagram/profiles/{profile_id}/accounts/{account_id}/credential/clone")
async def clone_account_credential(request: Request, profile_id: str, account_id: str,
                                   body: CredentialClone) -> ProfileAccountDTO:
    """Usar a senha de outra conta DESTA persona (ADR-057, D1): o cofre a copia para uma entrada própria desta
    conta, sem o valor sair dele. Outra persona → 409 `credencial_de_outra_persona`; origem sem senha → 409
    `no_credential`. O consentimento não é clonado: a conta que não tinha continua sem."""
    try:
        return _st(request).social.clone_account_credential(profile_id, account_id, body, by=quem(request))
    except SocialError as exc:
        raise social_error(exc) from exc


@router.post("/instagram/profiles/{profile_id}/accounts/{account_id}/credential/consent")
async def consent_account_credential(request: Request, profile_id: str, account_id: str) -> ProfileAccountDTO:
    """Consentir sem redigitar: marca `consent_at`/`consent_by` numa senha já guardada."""
    try:
        return _st(request).social.consent_account_credential(profile_id, account_id, by=quem(request))
    except SocialError as exc:
        raise social_error(exc) from exc


@router.post("/instagram/profiles/{profile_id}/accounts/{account_id}/session/connect", status_code=202)
async def connect_account(request: Request, profile_id: str, account_id: str,
                          instance_id: str | None = None) -> dict[str, object]:
    """Abre o app da conta no aparelho vinculado, reaproveita a sessão ou autentica, e verifica a conta. Só para
    app com provedor de sessão (409 `no_session_provider` nos demais). 202: o resultado aparece na conta (`session`).
    `?instance_id=`: outro aparelho vinculado à persona (N:N, 051); sem ele, o principal."""
    return await _start_session_job(request, profile_id, force_login=False, label="autenticação da conta",
                                    account_id=account_id, instance_id=instance_id)


@router.post("/instagram/profiles/{profile_id}/accounts/{account_id}/session/verify", status_code=202)
async def verify_account(request: Request, profile_id: str, account_id: str,
                         instance_id: str | None = None) -> dict[str, object]:
    """Relê do aparelho qual conta está aberta. Não digita senha: só observa."""
    return await _start_session_job(request, profile_id, force_login=False, observe_only=True,
                                    label="verificação da conta", account_id=account_id, instance_id=instance_id)


@router.post("/instagram/profiles/{profile_id}/accounts/{account_id}/session/logout", status_code=202)
async def logout_account(request: Request, profile_id: str, account_id: str,
                         instance_id: str | None = None) -> dict[str, object]:
    """Encerra a sessão da conta no aparelho apagando os dados do app dela — o jeito determinístico de sair."""
    return await _logout_job(request, profile_id, account_id=account_id, instance_id=instance_id)


@router.get("/instagram/profiles/{profile_id}/accounts/{account_id}/auth-attempts")
async def account_auth_attempts(request: Request, profile_id: str, account_id: str,
                                limit: int = 20) -> list[dict[str, object]]:
    """Tentativas de autenticação DESTA conta: quando, em qual aparelho e com que desfecho."""
    try:
        return _st(request).social.auth_attempts(profile_id, min(max(limit, 1), 100), account_id=account_id)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.put("/instagram/profiles/{profile_id}/credential", response_model=None)
async def put_credential(request: Request, profile_id: str, body: CredentialUpdate) -> object:
    """Apelido por perfil: a credencial da conta âncora. Só escrita; nunca o valor."""
    try:
        return _st(request).social.set_credential(profile_id, body, by=quem(request))
    except SocialError as exc:
        raise social_error(exc) from exc


@router.delete("/instagram/profiles/{profile_id}/credential", response_model=None)
async def delete_credential(request: Request, profile_id: str) -> object:
    try:
        return _st(request).social.delete_credential(profile_id)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.post("/instagram/profiles/{profile_id}/connect", status_code=202, response_model=None)
async def connect_profile(request: Request, profile_id: str, instance_id: str | None = None) -> object:
    """Apelido por perfil de `…/accounts/{conta âncora}/session/connect`.

    202 porque leva dezenas de segundos: o resultado aparece no próprio perfil (`session`).
    """
    return await _start_session_job(request, profile_id, force_login=False, label="autenticação da conta",
                                    instance_id=instance_id)


@router.post("/instagram/profiles/{profile_id}/verify", status_code=202, response_model=None)
async def verify_profile(request: Request, profile_id: str, instance_id: str | None = None) -> object:
    """Apelido por perfil de `…/session/verify`: relê do aparelho qual conta está aberta, sem digitar senha.

    `observe_only` faz a promessa valer: num aparelho deslogado, para na tela de login em vez de autenticar.
    """
    return await _start_session_job(request, profile_id, force_login=False, observe_only=True,
                                    label="verificação da conta", instance_id=instance_id)


@router.post("/instagram/profiles/{profile_id}/logout", status_code=202, response_model=None)
async def logout_profile(request: Request, profile_id: str, instance_id: str | None = None) -> object:
    """Apelido por perfil de `…/session/logout`: apaga os dados do app da conta âncora naquele aparelho.

    Apaga também cache e preferências do app; por isso é uma ação explícita, nunca efeito colateral de outra
    operação.
    """
    return await _logout_job(request, profile_id, account_id=None, instance_id=instance_id)


async def _logout_job(request: Request, profile_id: str, *, account_id: str | None,
                      instance_id: str | None = None) -> dict[str, object]:
    s = _st(request)
    rt, profile = _profile_device(s, profile_id, instance_id)
    conta = _conta_da_sessao(s, profile_id, account_id, rt.id)
    _exigir_confirmada(conta)
    _recusa_pelo_portao(conta, "logout")
    # "Sair da conta" APAGA os dados do app: é a operação mais destrutiva desta tela e era a que menos registro
    # tinha. Agora é um comando, com id, desfecho e `uncertain` quando o adb não responde.
    pacote = conta.package or s.social_repo.app_package or ""
    return {**_despachar_trabalho(s, rt, "session.logout", lambda: _do_logout(s, rt, profile_id, conta.id, pacote),
                                  label=f"logout de {conta.app_name or pacote}",
                                  params={"profile_id": profile_id, "account_id": conta.id}),
            "profile_id": profile_id, "account_id": conta.id}


def _exigir_confirmada(conta: ProfileAccountDTO) -> None:
    """31.281 (ADR-087): conta planejada ou em cadastro não é conta real logada; não há o que conectar, verificar ou sair."""
    if conta.provisioning.state != "confirmada":
        raise _err(409, "conta_nao_confirmada", "Esta conta ainda não foi confirmada no provedor "
                   f"({conta.provisioning.state}): conclua o cadastro antes de conectar, verificar ou sair.")


def _conta_da_sessao(s: AppState, profile_id: str, account_id: str | None, instance_id: str) -> ProfileAccountDTO:
    """A conta que a rota opera (a dita, ou a âncora nos apelidos por perfil), vista NO aparelho da operação: é a
    sessão e o app de lá que o portão confere."""
    try:
        return s.social.get_account(profile_id, account_id or _conta_ancora_ou_409(s, profile_id), instance_id)
    except SocialError as exc:
        raise social_error(exc) from exc


async def _do_logout(s: AppState, rt: DeviceRuntime, profile_id: str, account_id: str, package: str) -> None:
    await rt.executor.run(rt.adb.clear_data, package, timeout=120, label="apagar dados do app")
    rt.app_versions.clear()
    s.social_repo.set_account_session(profile_id, account_id, rt.id, status=SessionStatus.unknown,
                                      detail="Dados do app apagados neste aparelho; é preciso entrar de novo.")
    s.bus.emit("log", f"{rt.id}: sessão da conta encerrada ({package}: dados do app apagados)", instance_id=rt.id)


def _profile_device(s: AppState, profile_id: str, instance_id: str | None = None) -> tuple[DeviceRuntime, InstagramProfileDTO]:
    """O aparelho onde a operação da persona acontece: o dito (`?instance_id=`, que precisa estar vinculado a ela —
    409 `sem_vinculo`) ou o PRINCIPAL (N:N, migração 051)."""
    try:
        profile = s.social.get_profile(profile_id)
    except SocialError as exc:
        raise social_error(exc) from exc
    if instance_id is not None:
        if s.social_repo.binding(profile_id, instance_id) is None:
            raise _err(409, "sem_vinculo", f"Esta persona não está vinculada a {instance_id}.")
        return device(s, instance_id), profile
    if not profile.instance_id:
        raise _err(409, "no_binding", "Este perfil não está vinculado a nenhum aparelho.")
    rt = device(s, profile.instance_id)
    return rt, profile


def _recusa_pelo_portao(profile: InstagramProfileDTO | ProfileAccountDTO, acao: str) -> None:
    """Recusa pela MESMA regra que decide o botão (`social/sessao_gate.py`), com o código da fase.

    Sem isto, "Conectar" num aparelho sem o Instagram virava um 202 e uma tentativa de abrir um app que não existe.
    """
    acoes = profile.session_actions
    if acoes is None:
        return
    portao = getattr(acoes, acao)
    if not portao.allowed:
        codigo = {"app_missing": "app_not_installed", "app_unknown": "app_not_verified",
                  "app_installing": "app_busy", "authenticating": "session_busy"}.get(acoes.phase, "not_allowed")
        raise _err(409, codigo, portao.reason or acoes.detail)


async def _exigir_internet(s: AppState, rt: DeviceRuntime) -> None:
    """Conectar precisa de internet DENTRO do aparelho. `online` não prova isso (android-06, 25/09/2026: online,
    sem DNS, e o login virava "An unexpected error occurred"). Resultado velho ou desconhecido → sonda agora; e o
    que não se confirma recusa — incerteza não vira tentativa de login numa conta real."""
    info = rt.connectivity
    if info.state == "unknown" or time.monotonic() - rt.connectivity_mono > conectividade.VALIDADE_S:
        info = await s.devices.conferir_conectividade(rt)
    if info.state != "healthy":
        raise _err(409, "device_no_internet", info.detail)


async def _start_session_job(request: Request, profile_id: str, *, force_login: bool, label: str,
                             observe_only: bool = False, account_id: str | None = None,
                             instance_id: str | None = None) -> dict[str, object]:
    s = _st(request)
    rt, profile = _profile_device(s, profile_id, instance_id)
    conta = _conta_da_sessao(s, profile_id, account_id, rt.id)
    _exigir_confirmada(conta)
    if conta.host:
        # Conta de portal (um site, pelo navegador): o login gerenciado é o da conta do app inteiro, a única que a
        # porta de sessão e o despacho acham (item 23.4). O provedor também recusa; aqui a recusa é HTTP e imediata.
        raise _err(409, "conta_de_site", f"Esta conta é de site ({conta.host}), usada pelo navegador: o login "
                                        "gerenciado do app é o da conta do app, sem site.")
    if not conta.credential.configured and not observe_only:
        raise _err(409, "no_credential", "Guarde a senha desta conta antes de conectar.")
    if conta.credential.configured and conta.credential.consent_at is None and not observe_only:
        raise _err(409, "consentimento_de_credencial", "A senha desta conta está guardada sem o consentimento para a "
                                                      "automação digitá-la; marque-o na conta antes de conectar.")
    _recusa_pelo_portao(conta, "verify" if observe_only else "connect")
    if rt.state not in (InstanceState.online, InstanceState.booting, InstanceState.stopped,
                        InstanceState.hibernated, InstanceState.absent):
        raise _err(409, "device_unavailable", f"O aparelho está em '{rt.state.value}'.")
    if rt.state != InstanceState.online:
        s.devices.request_start(rt, f"conectar a conta de {conta.app_name or conta.app_id}")
        raise _err(409, "device_starting", "O aparelho está sendo ligado; tente novamente em instantes.")
    if not observe_only:
        await _exigir_internet(s, rt)
    verbo = "session.verify" if observe_only else "session.connect"
    # O provedor de sessão do PACOTE da conta (registro por pacote, fase K1), e a CONTA que a rota opera (item 23.4):
    # a credencial, a tentativa e a sessão que o provedor lê e grava são as dela, não as da conta âncora do perfil.
    provedor = s.sessoes.for_package(conta.package)
    if provedor is None:
        raise _err(409, "no_session_provider", f"O aplicativo desta conta ({conta.app_name or conta.app_id}) não tem "
                                              "login gerenciado pelo sistema: entre pelo aparelho e marque a sessão.")
    return {**_despachar_trabalho(
        s, rt, verbo,
        lambda: provedor.ensure_session(rt, profile_id, account_id=conta.id, force_login=force_login,
                                        observe_only=observe_only),
        label=label, params={"profile_id": profile_id, "account_id": conta.id}),
        "profile_id": profile_id, "account_id": conta.id}


@router.get("/instagram/profiles/{profile_id}/memory", response_model=None)
async def list_memory(request: Request, profile_id: str, subject: str | None = None, limit: int = 100,
                      app_id: str | None = None) -> object:
    try:
        return _st(request).social.list_memories(profile_id, subject=subject, limit=min(max(limit, 1), 500),
                                                app_id=app_id or None)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.post("/instagram/profiles/{profile_id}/memory", status_code=201, response_model=None)
async def add_memory(request: Request, profile_id: str, body: MemoryCreate) -> object:
    try:
        return _st(request).social.add_memory(profile_id, body)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.delete("/instagram/profiles/{profile_id}/memory/{memory_id}", status_code=204)
async def delete_memory(request: Request, profile_id: str, memory_id: str) -> None:
    try:
        _st(request).social.delete_memory(profile_id, memory_id)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.get("/instagram/profiles/{profile_id}/capacidades", response_model=None)
async def profile_capabilities(request: Request, profile_id: str) -> object:
    """O que esta persona já fez e quanto disso roda sem IA — fluxos concluídos com cobertura de receitas,
    etapas por origem (receita / IA), interações confirmadas por tipo. Leitura pura, sem custo de modelo."""

    s = _st(request)
    try:
        s.social.get_profile(profile_id)
    except SocialError as exc:
        raise social_error(exc) from exc
    return capacidades_do_perfil(s, profile_id)


@router.get("/instagram/profiles/{profile_id}/interactions", response_model=None)
async def list_interactions(request: Request, profile_id: str, counterparty: str | None = None,
                            thread_key: str | None = None, limit: int = 30, app_id: str | None = None) -> object:
    try:
        return _st(request).social.list_interactions(profile_id, counterparty=counterparty, thread_key=thread_key,
                                                    limit=min(max(limit, 1), 200), app_id=app_id or None)
    except SocialError as exc:
        raise social_error(exc) from exc


class SocialContextBody(BaseModel):
    """Corpo de `POST /instagram/profiles/{id}/context` (29.26): `content` é a mensagem recebida, texto livre de
    terceiro que pode trazer nome e e-mail; não vai para a URL (query string vira linha de log de acesso)."""
    model_config = ConfigDict(extra="forbid")
    counterparty: str | None = Field(default=None, max_length=200)
    thread_key: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=4000)


@router.post("/instagram/profiles/{profile_id}/context", response_model=None)
async def social_context(request: Request, profile_id: str, body: SocialContextBody | None = None) -> object:
    """Exatamente o que o modelo veria deste perfil. Serve para conferir persona, memória — e a ausência de senha.
    Sem efeito: é POST só para o texto da mensagem ir no corpo (29.26); corpo ausente = contexto sem interlocutor."""
    corpo = body or SocialContextBody()
    try:
        return _st(request).social.context(profile_id, counterparty=corpo.counterparty, thread_key=corpo.thread_key,
                                          current_content=corpo.content)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.get("/instagram/profiles/{profile_id}/policy", response_model=None)
async def get_policy(request: Request, profile_id: str, package: str | None = None) -> object:
    """`package` (23.10): sem ele, o app âncora, como sempre; com ele, o catálogo do app escolhido no painel —
    quem decide qual app é o âncora não é mais o painel adivinhando "o primeiro com login gerenciado"."""
    try:
        return _st(request).social.get_policy(profile_id, package=package)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.put("/instagram/profiles/{profile_id}/policy", response_model=None)
async def put_policy(request: Request, profile_id: str, body: ProfilePolicyPatch, package: str | None = None) -> object:
    try:
        return _st(request).social.set_policy(profile_id, body, package=package)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.get("/instagram/profiles/{profile_id}/accounts", response_model=None)
async def list_profile_accounts(request: Request, profile_id: str) -> object:
    try:
        return _st(request).social.list_accounts(profile_id)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.post("/instagram/profiles/{profile_id}/accounts", status_code=201, response_model=None)
async def add_profile_account(request: Request, profile_id: str, body: ProfileAccountCreate) -> object:
    try:
        return _st(request).social.add_account(profile_id, body, by=quem(request))
    except SocialError as exc:
        raise social_error(exc) from exc


@router.post("/instagram/profiles/{profile_id}/accounts/planned", response_model=None)
async def plan_profile_account(request: Request, profile_id: str, body: PlannedAccountCreate,
                               response: Response) -> object:
    """31.281 (ADR-087, v1.132): a conta que ainda NÃO existe no provedor. 201 na primeira vez; 200 idempotente se a
    conta planejada deste (perfil, app, host) já existe (um `desired_handle` diferente só a edita)."""
    try:
        conta, criada = _st(request).social.provisionamento.planejar(profile_id, body, by=quem(request))
    except SocialError as exc:
        raise social_error(exc) from exc
    response.status_code = 201 if criada else 200
    return conta


@router.get("/instagram/profiles/{profile_id}/accounts/handle-suggestions", response_model=None)
async def suggest_account_handles(request: Request, profile_id: str, app_id: str = Query(min_length=1)) -> object:
    """Endereços sugeridos pelos dados da persona (local, sem IA e sem rede; não afirma que estão livres no provedor)."""
    try:
        return {"suggestions": _st(request).social.provisionamento.sugestoes(profile_id, app_id)}
    except SocialError as exc:
        raise social_error(exc) from exc


@router.post("/instagram/profiles/{profile_id}/accounts/{account_id}/credential/prepare", response_model=None)
async def prepare_account_credential(request: Request, profile_id: str, account_id: str,
                                     body: CredentialPrepare) -> object:
    """Gerar (o servidor), digitar ou reutilizar a senha de uma conta planejada. Nenhuma resposta devolve a senha."""
    try:
        return _st(request).social.provisionamento.preparar_credencial(profile_id, account_id, body, by=quem(request))
    except SocialError as exc:
        raise social_error(exc) from exc


@router.post("/instagram/profiles/{profile_id}/accounts/{account_id}/provisioning", response_model=None)
async def provision_account_event(request: Request, profile_id: str, account_id: str,
                                  body: ProvisioningEventBody) -> object:
    """Um evento do ciclo da conta planejada, com o `estado_esperado` (comparar e trocar). Idempotente."""
    try:
        return _st(request).social.provisionamento.transicao(profile_id, account_id, body, by=quem(request))
    except SocialError as exc:
        raise social_error(exc) from exc


@router.post("/instagram/profiles/{profile_id}/accounts/{account_id}/provisioning/signup", status_code=202,
             response_model=None)
async def signup_account(request: Request, profile_id: str, account_id: str, body: SignupBody | None = None) -> object:
    """31.310 (v1.137): o cadastro guiado da conta planejada. Valida (409 com código) e despacha o comando `session.cadastrar`:
    preenche o formulário declarado pelo app, lê o código da caixa da conta e comprova a conta pela sessão observada. Para e
    chama uma pessoa em CAPTCHA, telefone, @ indisponível e tela desconhecida. Uma conta por vez; nunca liga o aparelho."""
    try:
        return CadastroGuiado(_st(request)).iniciar(profile_id, account_id,
                                                    instance_id=body.instance_id if body else None, by=quem(request))
    except SocialError as exc:
        raise social_error(exc) from exc


@router.patch("/instagram/profiles/{profile_id}/accounts/{account_id}", response_model=None)
async def patch_profile_account(request: Request, profile_id: str, account_id: str, body: ProfileAccountPatch) -> object:
    try:
        return _st(request).social.update_account(profile_id, account_id, body)
    except SocialError as exc:
        raise social_error(exc) from exc


class RetirarContaBody(BaseModel):
    """Corpo opcional de `…/accounts/{id}/retire`: o que a pessoa viu (o @ é cortado do evento)."""
    evidencia: str | None = Field(default=None, max_length=500)


@router.post("/instagram/profiles/{profile_id}/accounts/{account_id}/retire")
async def retire_profile_account(request: Request, profile_id: str, account_id: str,
                                 body: RetirarContaBody | None = None) -> dict[str, object]:
    """Bloqueio confirmado (29.23, ADR-068): a conta SAI na hora (credencial, cofre, sessão, vínculo, linha) e a
    persona fica. Vale também para a âncora, que a remoção comum recusa. Idempotente: a conta que já saiu é 200 com
    `retirada: false`. O aparelho não é tocado."""
    try:
        return _st(request).social.retirar_conta_bloqueada(
            profile_id, account_id, origem="declarado", autor=quem(request),
            evidencia=body.evidencia if body else None)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.delete("/instagram/profiles/{profile_id}/accounts/{account_id}", status_code=204)
async def delete_profile_account(request: Request, profile_id: str, account_id: str) -> None:
    try:
        _st(request).social.delete_account(profile_id, account_id)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.get("/instagram/policy-groups", response_model=None)
async def list_policy_groups(request: Request, package: str | None = None) -> object:
    """`package` (23.10): contra que catálogo `loosened` é calculado; `capabilities`/`limits` seguem completos."""
    return _st(request).social.list_policy_groups(package=package)


@router.get("/instagram/policy-defaults", response_model=None)
async def policy_defaults(request: Request) -> object:
    """Os limites-padrão (o que vale sem grupo e sem escolha própria) — o editor de grupo parte deles."""
    return {"limits": DEFAULT_LIMITS}


@router.post("/instagram/policy-groups", status_code=201, response_model=None)
async def create_policy_group(request: Request, body: PolicyGroupCreate, package: str | None = None) -> object:
    try:
        return _st(request).social.create_policy_group(body, package=package)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.get("/instagram/policy-groups/{group_id}", response_model=None)
async def get_policy_group(request: Request, group_id: str, package: str | None = None) -> object:
    try:
        return _st(request).social.get_policy_group(group_id, package=package)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.put("/instagram/policy-groups/{group_id}", response_model=None)
async def put_policy_group(request: Request, group_id: str, body: PolicyGroupPatch, package: str | None = None) -> object:
    try:
        return _st(request).social.update_policy_group(group_id, body, package=package)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.delete("/instagram/policy-groups/{group_id}", status_code=204)
async def delete_policy_group(request: Request, group_id: str) -> None:
    try:
        _st(request).social.delete_policy_group(group_id)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.get("/instagram/profiles/{profile_id}/auth-attempts", response_model=None)
async def auth_attempts(request: Request, profile_id: str, limit: int = 20) -> object:
    """Apelido por perfil: as tentativas de autenticação da conta âncora (as anteriores à 049 apontam para ela)."""
    s = _st(request)
    try:
        return s.social.auth_attempts(profile_id, min(max(limit, 1), 100), account_id=_conta_ancora_ou_409(s, profile_id))
    except SocialError as exc:
        raise social_error(exc) from exc


@router.get("/instagram/profiles/{profile_id}/runs", response_model=None)
async def profile_runs(request: Request, profile_id: str, limit: int = 20) -> object:
    """Execuções que passaram por este perfil. O vínculo vem do objetivo, que guarda o dono fotografado."""
    s = _st(request)
    try:
        s.social.get_profile(profile_id)
    except SocialError as exc:
        raise social_error(exc) from exc
    rows = s.db.query(
        "SELECT r.* FROM runs r WHERE EXISTS (SELECT 1 FROM objectives o WHERE o.run_id=r.id AND o.profile_id=?)"
        " ORDER BY r.created_at DESC LIMIT ?", (profile_id, min(max(limit, 1), 100)))
    return s.repo.run_summaries(rows)


@router.get("/instagram/profiles/{profile_id}/operational-context", response_model=None)
async def profile_context(request: Request, profile_id: str, instance_id: str | None = None) -> object:
    """O MESMO contexto, chegando pelo perfil: do Beltrano Souza ao aparelho dele sem trocar de tela. O aparelho é
    o principal da persona, ou o dito em `?instance_id=` (precisa estar vinculado a ela)."""
    s = _st(request)
    rt, _perfil = _profile_device(s, profile_id, instance_id)
    return contexto_do_aparelho(s, rt.id)


# ------------------------------------------------------------------ ponte android ⇄ igfarm (migração 132)
def _erro_da_ponte(exc: ErroDaPonte) -> HTTPException:
    return _err(exc.status, exc.code, exc.message)


def _pendente_dto(p: PersonaPendente) -> PersonaPendenteDTO:
    f = p.ficha
    return PersonaPendenteDTO(
        persona_id=f.persona_id, nome=f.nome, primeiro_nome=f.primeiro_nome, sobrenome=f.sobrenome,
        nome_exibicao=f.nome_exibicao, birth_date=f.birth_date or "", genero=f.genero, biografia=f.biografia,
        visual=f.visual, resumo=f.resumo, email_sugerido=p.email_sugerido, username_sugerido=p.username_sugerido,
        imagem_perfil=p.imagem.url if p.imagem else None, imagem_pendente=p.imagem_pendente)


@router.get("/instagram/personas-pendentes", response_model=None)
async def personas_pendentes(request: Request, dominio: str, limite: int = Query(10, ge=1, le=50),
                             locale: str | None = None, com_imagem: bool = True,
                             reservar: bool = False) -> list[PersonaPendenteDTO]:
    """As pessoas que ainda não têm conta, com e-mail e @ sugeridos (persistentes) e a foto. A foto só é GERADA com
    `reservar=true`; `reservar=true` também tira a pessoa das outras chamadas por 24 h. Quem consome chama sempre com
    `reservar=true` e um `limite` pequeno."""
    try:
        achadas = await compor_ponte_igfarm(_st(request)).pendentes(
            dominio=dominio, limite=limite, locale=locale, com_imagem=com_imagem, reservar=reservar)
    except ErroDaPonte as exc:
        raise _erro_da_ponte(exc) from exc
    return [_pendente_dto(p) for p in achadas]


@router.post("/instagram/contas", response_model=None)
async def registrar_conta_igfarm(request: Request, body: ContaIgfarmBody) -> JSONResponse:
    """Registra a conta que o igfarm criou: 201 na primeira vez, 200 com `idempotente: true` na repetição (mesma persona
    e mesmo @). As senhas vão ao cofre; a resposta só mostra a máscara."""
    try:
        r = compor_ponte_igfarm(_st(request)).registrar(ComandoDeRegistro(
            persona_id=body.persona_id.strip(), dominio=body.dominio, email=body.email,
            email_senha=body.email_senha.get_secret_value(), instagram_username=body.instagram_username,
            instagram_senha=body.instagram_senha.get_secret_value(), igfarm_account_id=body.igfarm_account_id,
            criada_em=body.criada_em.isoformat(), por="igfarm",
            proxy_url=body.proxy_url.get_secret_value() if body.proxy_url else None,
            ip_criacao=body.ip_criacao))
    except ErroDaPonte as exc:
        raise _erro_da_ponte(exc) from exc
    dto = ContaRegistradaDTO(persona_id=r.persona_id, account_id=r.account_id, igfarm_account_id=r.igfarm_account_id,
                             email=r.email, instagram_username=r.instagram_username, criada_em=r.criada_em,
                             registrada_em=r.registrada_em, senhas=SENHAS_MASCARADAS, criada=not r.idempotente,
                             idempotente=r.idempotente,
                             egresso=[EgressoDoDeviceDTO(instance_id=e.instance_id, estado=e.estado, motivo=e.motivo)
                                      for e in r.egresso])
    return JSONResponse(status_code=200 if r.idempotente else 201, content=dto.model_dump())


@router.get("/instagram/contas/{conta_id}/ciclo", response_model=None)
async def ciclo_da_conta(request: Request, conta_id: str) -> CicloDaContaDTO:
    """31.333: criada, registrada, cada contato com o app (minutos desde a criação, desfecho) e retirada. Só leitura, sem
    segredo; `conta_id` é o da central ou o do igfarm."""
    try:
        c = compor_ponte_igfarm(_st(request)).ciclo(conta_id)
    except ErroDaPonte as exc:
        raise _erro_da_ponte(exc) from exc
    return CicloDaContaDTO(
        account_id=c.account_id, igfarm_account_id=c.igfarm_account_id,
        criada_em=c.criada_em, registrada_em=c.registrada_em, estado=c.estado, retirada_em=c.retirada_em,
        minutos_ate_o_primeiro_contato=c.minutos_ate_o_primeiro_contato, ultimo_desfecho=c.ultimo_desfecho,
        contatos=[ContatoDaContaDTO(iniciado_em=t.iniciado_em, minutos_desde_a_criacao=t.minutos_desde_a_criacao,
                                    desfecho=t.desfecho, etapa=t.etapa, detalhe=t.detalhe) for t in c.contatos])


@router.get("/instagram/contas/{conta_id}/cabecalhos", response_model=None)
async def cabecalhos_da_conta(request: Request, conta_id: str, horas: int = Query(48, ge=1, le=336),
                              limite: int = Query(20, ge=1, le=50)) -> CabecalhosDaContaDTO:
    """31.336: os cabeçalhos (SEM corpo) das mensagens que chegaram à caixa da conta nas últimas `horas`: remetente, assunto
    (seis dígitos mascarados), data, SPF/DKIM/DMARC anotados pelo servidor e se é aviso de devolução. Só leitura de e-mail
    real: 503 `email_indisponivel` sem IMAP."""
    try:
        achadas = await compor_ponte_igfarm(_st(request)).cabecalhos(conta_id, horas=horas, limite=limite)
        conta = compor_ponte_igfarm(_st(request)).ciclo(conta_id)
    except ErroDaPonte as exc:
        raise _erro_da_ponte(exc) from exc
    return CabecalhosDaContaDTO(
        account_id=conta.account_id, horas=horas, total=len(achadas),
        mensagens=[CabecalhoDaCaixaDTO(recebida_em=c.recebida_em.isoformat(), remetente=c.remetente, assunto=c.assunto,
                                       autenticacao=dict(c.autenticacao), devolucao=c.devolucao) for c in achadas])


@router.get("/instagram/contas/{conta_id}/codigo", response_model=None)
async def codigo_da_conta(request: Request, conta_id: str) -> CodigoDaContaDTO:
    """O código de 6 dígitos mais recente na caixa da conta (leitura de e-mail real: 503 `email_indisponivel` sem IMAP)."""
    try:
        c = await compor_ponte_igfarm(_st(request)).codigo(conta_id)
    except ErroDaPonte as exc:
        raise _erro_da_ponte(exc) from exc
    return CodigoDaContaDTO(codigo=c.codigo, recebido_em=c.recebido_em, remetente=c.remetente)

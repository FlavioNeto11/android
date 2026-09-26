"""O que a tela pode oferecer para a sessão de um perfil — decidido aqui, uma vez, e não em flags soltas no painel.

O defeito que motivou: "Conectar" e "Verificar conta" apareciam no perfil do android-06 antes de o Instagram
existir naquele aparelho. Cada botão olhava uma coisa só (senha guardada, aparelho vinculado), e nenhuma delas
diz se o app está lá. As camadas são independentes e nenhuma implica a seguinte:

    vínculo com aparelho  ≠  app instalado  ≠  credencial guardada  ≠  sessão autenticada

Função pura: recebe o que o banco sabe e devolve o portão de cada ação com o motivo. O mesmo resultado vai no DTO
do perfil (a tela mostra) e na rota (o backend recusa) — os dois lados contam a mesma história.
"""
from __future__ import annotations

from typing import Any

from ..models import ActionGate, AppOnDevice, SessionActions, SessionStatus

#: Estados de `device_app_state` em que o pacote EXISTE no aparelho (observado). `verify_failed` entra: o app
#: está lá, só não passou na prova de abertura — a sessão é outro assunto.
APP_PRESENTE = frozenset({"installed", "ready", "version_drift", "verify_failed"})
#: Observado ausente, ou a instalação terminou sem o app.
APP_AUSENTE = frozenset({"missing", "install_failed", "incompatible"})
#: Operação em curso: não se sabe ainda como vai terminar.
APP_EM_CURSO = frozenset({"installing", "verifying"})

_OK = ActionGate(allowed=True)


def _nega(motivo: str) -> ActionGate:
    return ActionGate(allowed=False, reason=motivo)


def app_on_device(row: Any | None, package: str) -> AppOnDevice:
    """A linha de `device_app_state` vira o resumo que a tela mostra. Sem linha = nunca inspecionado."""
    if row is None:
        return AppOnDevice(package=package)
    return AppOnDevice(package=package, state=row["state"], version_name=row["observed_version_name"],
                       version_code=row["observed_version_code"], verified_at=row["verified_at"],
                       pending_op=row["pending_op"], detail=row["detail"])


def acoes_de_sessao(*, instance_id: str | None, app: AppOnDevice | None, app_name: str,
                    credential_configured: bool, session_status: SessionStatus | str | None,
                    session_open: bool = False) -> SessionActions:
    """Portões de Conectar / Verificar conta / Sair e a fase em que a cadeia parou.

    `session_open` = há um `session.connect`/`session.verify` em voo neste aparelho: é o único jeito honesto de
    dizer "autenticando" — clicar em Entrar não é estar autenticado, e o comando só fecha com a conta lida na tela.
    """
    status = SessionStatus(session_status) if session_status else SessionStatus.unknown
    if not instance_id:
        motivo = "Vincule um aparelho a este perfil."
        return SessionActions(phase="no_device", detail="Perfil sem aparelho vinculado.", connect=_nega(motivo),
                              verify=_nega(motivo), logout=_nega(motivo), inspect_app=_nega(motivo))

    estado = app.state if app else None
    inspecionar = _OK
    if estado is None or estado in APP_AUSENTE or estado in APP_EM_CURSO:
        if estado is None:
            fase, detalhe = "app_unknown", (f"Não se sabe se o {app_name} está instalado em {instance_id}: o aparelho "
                                            "nunca foi inspecionado. Verifique o app antes de conectar.")
        elif estado in APP_EM_CURSO:
            fase, detalhe = "app_installing", f"{app_name} em {estado} em {instance_id}; aguarde o desfecho."
            inspecionar = _nega("Há uma operação do app em curso neste aparelho.")
        else:
            fase, detalhe = "app_missing", (f"{app_name} não está instalado em {instance_id} ({estado}). Instale a "
                                            "versão promovida antes de conectar a conta.")
        motivo = detalhe
        return SessionActions(phase=fase, detail=detalhe, connect=_nega(motivo), verify=_nega(motivo),
                              logout=_nega(motivo), inspect_app=inspecionar)

    # Daqui para baixo o app ESTÁ no aparelho (observado).
    verificar, sair = _OK, _OK
    if session_open:
        ocupado = "Há uma conexão/verificação em andamento neste aparelho; espere o desfecho."
        return SessionActions(phase="authenticating", detail="Autenticando: aguardando a conta aparecer na tela.",
                              connect=_nega(ocupado), verify=_nega(ocupado), logout=_nega(ocupado),
                              inspect_app=_nega(ocupado))
    conectar = _OK if credential_configured else _nega("Guarde a senha deste perfil (aba Autenticação) antes de "
                                                      "conectar.")
    if status is SessionStatus.session_ready:
        fase, detalhe = "authenticated", "Conta confirmada na tela do aparelho."
    elif status is SessionStatus.auth_challenge:
        fase, detalhe = "challenge", ("O app pediu confirmação (desafio/2FA). Só uma pessoa resolve; depois use "
                                      "Verificar conta.")
    elif status is SessionStatus.wrong_account:
        fase, detalhe = "wrong_account", "Outra conta está aberta neste aparelho."
    elif not credential_configured:
        fase, detalhe = "no_credential", "App instalado, mas o perfil não tem senha guardada."
    elif status is SessionStatus.auth_required:
        fase, detalhe = "logged_out", "App instalado e deslogado: Conectar inicia o login."
    else:
        fase, detalhe = "unknown", "App instalado; a sessão ainda não foi observada. Verifique a conta."
    return SessionActions(phase=fase, detail=detalhe, connect=conectar, verify=verificar, logout=sair,
                          inspect_app=_OK)

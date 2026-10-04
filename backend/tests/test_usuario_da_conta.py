"""Item 29.71: `conta_<app>_usuario` entrega o NOME da conta no app, não o e-mail de login.

Visto na prova real do 8.3 (04/10, execução f85a37): `{conta_instagram_usuario}` resolveu para o e-mail de login da
conta (o `login_identifier`, que vencia o handle) e entrou no binding, no título e no objetivo da etapa de abrir o
perfil. No app de login GERENCIADO (o Instagram, o Outlook) quem entra é o provedor de sessão, que lê o identificador
da credencial; a variável é o @. No app de login por formulário (o Chrome de um portal) a variável segue sendo o
identificador, que é o que o formulário pede: a contraprova do login.

Nível de prova: `simulated`, puro (o domínio de `available_data`, contas montadas à mão; valores fictícios).
"""
from __future__ import annotations

from app.modules.identity.domain.available_data import (AccountRecord, account_data, available_data,
                                                        profile_variables, resolve_secret)

EMAIL = "lucas.login@exemplo.test"


def _conta(app_id: str, *, handle: str, login: str | None, managed: bool, credencial: bool = True,
           host: str | None = None) -> AccountRecord:
    return AccountRecord(account_id=f"acc-{app_id}", app_id=app_id, package=f"pkg.{app_id}", app_label=app_id.title(),
                         handle=handle, host=host, login_identifier=login, has_credential=credencial,
                         credential_status="active" if credencial else None,
                         consent_at="2026-10-04T00:00:00Z" if credencial else None,
                         secret_ref="ref" if credencial else None, managed=managed)


def test_login_gerenciado_entrega_o_handle_e_nunca_o_email() -> None:
    ig = _conta("instagram", handle="lucas.teste", login=EMAIL, managed=True)
    valores = profile_variables(None, [ig])
    assert valores == {"conta_instagram_usuario": "lucas.teste"}
    assert EMAIL not in repr(valores) and EMAIL not in repr(available_data(None, [ig]))
    # A senha do app gerenciado continua fora do modelo (ADR-040): só o usuário é dado disponível.
    assert [d.name for d in account_data([ig])] == ["conta_instagram_usuario"]


def test_login_gerenciado_sem_handle_nao_oferece_variavel() -> None:
    """Sem o @, a variável não existe: o e-mail de login não entra no lugar dele."""
    ig = _conta("instagram", handle="", login=EMAIL, managed=True)
    assert profile_variables(None, [ig]) == {} and account_data([ig]) == ()


def test_contraprova_login_por_formulario_segue_com_o_identificador() -> None:
    chrome = _conta("chrome", handle="Portal do Lucas", login="lucas.portal", managed=False, host="portal.exemplo.test")
    valores = profile_variables(None, [chrome])
    assert valores == {"conta_chrome_portal_exemplo_test_usuario": "lucas.portal"}
    nomes = [d.name for d in account_data([chrome])]
    assert nomes == ["conta_chrome_portal_exemplo_test_usuario", "conta_chrome_portal_exemplo_test_senha"]
    # Os nomes das variáveis seguem os mesmos para o canal sensível (a contagem de colisões é a do `account_data`).
    assert resolve_secret([chrome], "conta_chrome_portal_exemplo_test_senha").secret is not None


def test_os_nomes_nao_mudam_entre_apps_com_e_sem_handle() -> None:
    """Gerenciado sem handle some da lista sem empurrar o nome de quem vem depois (a colisão conta igual)."""
    ig = _conta("instagram", handle="", login=EMAIL, managed=True, credencial=False)
    chrome = _conta("chrome", handle="", login="lucas.portal", managed=False)
    assert profile_variables(None, [ig, chrome]) == {"conta_chrome_usuario": "lucas.portal"}
    assert resolve_secret([ig, chrome], "conta_chrome_senha").secret is not None

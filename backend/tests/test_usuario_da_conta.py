"""Item 29.71: `conta_<app>_usuario` entrega o NOME da conta no app, não o e-mail de login.

Visto na prova real do 8.3 (04/10, execução f85a37): `{conta_instagram_usuario}` resolveu para o e-mail de login da
conta (o `login_identifier`, que vencia o handle) e entrou no binding, no título e no objetivo da etapa de abrir o
perfil. No app de login GERENCIADO (o Instagram, o Outlook) quem entra é o provedor de sessão, que lê o identificador
da credencial; a variável é o @. No app de login por formulário (o Chrome de um portal) a variável segue sendo o
identificador, que é o que o formulário pede: a contraprova do login.

Nível de prova: `simulated`. Puro (o domínio de `available_data`, contas montadas à mão) e, no último teste, o harness
(a materialização e o planejador lendo o mesmo banco; valores fictícios).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.identity.domain.available_data import (AccountRecord, account_data, available_data,
                                                        profile_variables, resolve_secret)

if TYPE_CHECKING:
    from .conftest import Harness

EMAIL = "tadeu.login@exemplo.test"


def _conta(app_id: str, *, handle: str, login: str | None, managed: bool, credencial: bool = True,
           host: str | None = None) -> AccountRecord:
    return AccountRecord(account_id=f"acc-{app_id}", app_id=app_id, package=f"pkg.{app_id}", app_label=app_id.title(),
                         handle=handle, host=host, login_identifier=login, has_credential=credencial,
                         credential_status="active" if credencial else None,
                         consent_at="2026-10-04T00:00:00Z" if credencial else None,
                         secret_ref="ref" if credencial else None, managed=managed)


def test_login_gerenciado_entrega_o_handle_e_nunca_o_email() -> None:
    ig = _conta("instagram", handle="tadeu.teste", login=EMAIL, managed=True)
    valores = profile_variables(None, [ig])
    assert valores == {"conta_instagram_usuario": "tadeu.teste"}
    assert EMAIL not in repr(valores) and EMAIL not in repr(available_data(None, [ig]))
    # A senha do app gerenciado continua fora do modelo (ADR-040): só o usuário é dado disponível.
    assert [d.name for d in account_data([ig])] == ["conta_instagram_usuario"]


def test_login_gerenciado_sem_handle_nao_oferece_variavel() -> None:
    """Sem o @, a variável não existe: o e-mail de login não entra no lugar dele."""
    ig = _conta("instagram", handle="", login=EMAIL, managed=True)
    assert profile_variables(None, [ig]) == {} and account_data([ig]) == ()


def test_contraprova_login_por_formulario_segue_com_o_identificador() -> None:
    chrome = _conta("chrome", handle="Portal do Tadeu", login="tadeu.portal", managed=False, host="portal.exemplo.test")
    valores = profile_variables(None, [chrome])
    assert valores == {"conta_chrome_portal_exemplo_test_usuario": "tadeu.portal"}
    nomes = [d.name for d in account_data([chrome])]
    assert nomes == ["conta_chrome_portal_exemplo_test_usuario", "conta_chrome_portal_exemplo_test_senha"]
    # Os nomes das variáveis seguem os mesmos para o canal sensível (a contagem de colisões é a do `account_data`).
    assert resolve_secret([chrome], "conta_chrome_portal_exemplo_test_senha").secret is not None


def test_os_nomes_nao_mudam_entre_apps_com_e_sem_handle() -> None:
    """Gerenciado sem handle some da lista sem empurrar o nome de quem vem depois (a colisão conta igual)."""
    ig = _conta("instagram", handle="", login=EMAIL, managed=True, credencial=False)
    chrome = _conta("chrome", handle="", login="tadeu.portal", managed=False)
    assert profile_variables(None, [ig, chrome]) == {"conta_chrome_usuario": "tadeu.portal"}
    assert resolve_secret([ig, chrome], "conta_chrome_senha").secret is not None


def test_a_materializacao_e_o_planejador_veem_o_mesmo_valor_do_instagram(harness: "Harness") -> None:
    """Revisão independente do deploy 29 (achado 4): o `Repository` montava as contas com "tem provedor de sessão?"
    sempre falso, então a variável da MATERIALIZAÇÃO (binding, título e objetivo da etapa) seguia com o e-mail de
    login do Instagram, embora a lista do planejador (`service.dados`) já desse o @. Com o predicado real nos dois, os
    valores são iguais e o e-mail não aparece. No harness: Instagram com provedor de sessão, valores fictícios."""
    from app.models import ProfileCreate
    from app.util import now_iso

    st = harness.state
    assert st is not None
    pid = st.social.create_profile(ProfileCreate(username="tadeu.teste", instance_id="android-01", first_name="Tadeu",
                                                 last_name="Teste")).id
    conta = st.social_repo.conta_ancora(pid)
    assert conta is not None and conta["app_id"] == "instagram"
    st.db.execute("INSERT INTO account_credentials(account_id, login_identifier, secret_ref, key_id, updated_at)"
                  " VALUES (?,?,?,?,?)", (conta["id"], EMAIL, "ref-ficticia", "k1", now_iso()))

    da_materializacao = st.repo._variaveis_da_persona(pid)                      # noqa: SLF001
    do_planejador = profile_variables_do_servico(st, pid)
    assert da_materializacao["conta_instagram_usuario"] == "tadeu.teste"
    assert da_materializacao == do_planejador
    assert EMAIL not in repr(da_materializacao)


def profile_variables_do_servico(st: object, pid: str) -> dict[str, str]:
    from app.modules.identity.application.available_data import profile_variables as variaveis

    return variaveis(st.runs.dados, pid)                                         # type: ignore[attr-defined]

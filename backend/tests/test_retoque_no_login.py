"""Item 29.64: UM re-toque verificado em Entrar quando o envio fica incerto com o formulário intacto, e a credencial em
`review` que se reconcilia quando a conta conferida está aberta no aparelho.

Visto no android-13 em 04/10 (c-20261004001548-03701b): o toque em Entrar se perdeu, o formulário ficou preenchido, o
botão habilitado, sem erro nem carregando, e o login fechou `uncertain`; um toque manual, sem redigitar, entrou. Depois
disso a sessão era `session_ready` e a credencial seguia em `review`, travando o login automático da persona.
"""
from __future__ import annotations

from pathlib import Path

from app.integrations.app_declarado.sessao import Outcome
from app.modules.identity.application.session_rules import CREDENCIAL_EM_REVISAO

from .fake_instagram import FakeInstagram
from .test_instagram_auth import SENHA, USUARIO, FakeRt, build, cadastrar


def _envios(app: FakeInstagram) -> int:
    return app.calls.count("submit")


def _senhas_digitadas(app: FakeInstagram) -> int:
    """Quantas vezes o campo da senha foi preenchido: cada preenchimento começa limpando o campo."""
    return app.calls.count("type:password:clear")


async def test_toque_ignorado_com_formulario_intacto_ganha_um_retoque_e_entra(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA, envios_ignorados=1)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.SESSION_READY, r.detail          # antes ficava `uncertain` no formulário
        assert app.account == USUARIO
        assert _envios(app) == 2                                     # o envio e UM re-toque
        assert _senhas_digitadas(app) == 1                           # a senha não foi digitada de novo
        assert repo.credential_row(pid)["status"] == "active"
        etapas = [a["stage"] for a in repo.auth_attempts(pid)]
        assert etapas and etapas[0] != "resubmitting"                # a tentativa fechou com o desfecho, não no meio
    finally:
        db.close()


async def test_o_retoque_e_um_so(tmp_path: Path) -> None:
    """Dois toques ignorados: o login para incerto depois do re-toque, sem um terceiro envio."""
    app = FakeInstagram(stored_password=SENHA, envios_ignorados=2)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.UNCERTAIN
        assert _envios(app) == 2
        assert repo.credential_row(pid)["status"] == CREDENCIAL_EM_REVISAO     # ADR-055: o login para
    finally:
        db.close()


async def test_carregando_nao_ganha_retoque(tmp_path: Path) -> None:
    """Carregando é o app processando o envio: tocar de novo seria um segundo envio de verdade."""
    app = FakeInstagram(stored_password=SENHA, envios_ignorados=1, tela_ao_ignorar="carregando")
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.UNCERTAIN
        assert _envios(app) == 1
    finally:
        db.close()


async def test_senha_limpa_pelo_app_nao_ganha_retoque_nem_redigitacao(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA, envios_ignorados=1, tela_ao_ignorar="senha_limpa")
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.UNCERTAIN
        assert _envios(app) == 1
        assert _senhas_digitadas(app) == 1
    finally:
        db.close()


async def test_conta_conferida_aberta_tira_a_credencial_de_revisao(tmp_path: Path) -> None:
    app = FakeInstagram(account=USUARIO, screen="feed", stored_password=SENHA)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        repo.mark_credential(pid, status=CREDENCIAL_EM_REVISAO, failed_attempts=1)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.ready and not r.attempted_login                       # ninguém digitou nada
        cred = repo.credential_row(pid)
        assert cred["status"] == "active" and cred["failed_attempts"] == 0
    finally:
        db.close()


async def test_credencial_recusada_nao_se_reconcilia_pela_sessao_aberta(tmp_path: Path) -> None:
    """`invalid` é a senha guardada recusada: a sessão aberta à mão não a conserta."""
    app = FakeInstagram(account=USUARIO, screen="feed", stored_password=SENHA)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        repo.mark_credential(pid, status="invalid")
        await auth.ensure_session(FakeRt(app), pid)
        assert repo.credential_row(pid)["status"] == "invalid"
    finally:
        db.close()

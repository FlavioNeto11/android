"""31.332: o diálogo "Can't find account" do login é um desfecho próprio (`conta_nao_encontrada`), não "tela desconhecida".

A tela vem da hierarquia real de 10/10/2026 (H2, android-08; `.claude/handoffs/bifurcacao/h2-retry-tela-final.json`): um
diálogo de dois botões (TRY AGAIN, SIGN UP) sobre o login. Não é bloqueio (a conta não é retirada), não é senha errada (a
credencial não vai a `invalid`), a sessão fica `auth_required`, o login automático para até uma pessoa conferir o
identificador, e o motivo traz o identificador usado MASCARADO. Os botões do diálogo nunca são tocados.

Nível de prova: `simulated` (hierarquia copiada da captura real, com identificador trocado; aparelho falso).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.automation.hierarchy import UiTree, parse_hierarchy
from app.integrations.app_declarado.conhecimento import SessaoInvalida, do_app
from app.integrations.app_declarado.sessao import Outcome, classificar_depois_do_envio, mascarar_identificador
from app.models import SessionStatus

from .fake_instagram import PKG, FakeInstagram
from .test_instagram_auth import SENHA, USUARIO, FakeRt, build, cadastrar

IDENTIFICADOR = "fulano.teste@exemplo.com.br"


def _dialogo(identificador: str = IDENTIFICADOR, *, titulo: str = "Can't find account",
             corpo: str | None = None) -> UiTree:
    """Os quatro elementos da captura real (ids e bounds iguais), com o identificador trocado."""
    corpo = corpo or (f"We can't find an account with {identificador}. Try another mobile number or email, or if you "
                      "don't have an account, you can sign up.")
    return parse_hierarchy(
        f'<hierarchy><node package="{PKG}" bounds="[0,0][720,1280]">'
        f'<node class="android.widget.TextView" text="{titulo}" package="{PKG}" bounds="[50,436][670,521]"/>'
        f'<node class="android.widget.TextView" text="{corpo}" resource-id="android:id/message" package="{PKG}" '
        'bounds="[50,537][670,732]"/>'
        f'<node class="android.widget.Button" text="TRY AGAIN" resource-id="android:id/button2" clickable="true" '
        f'package="{PKG}" bounds="[291,740][487,836]"/>'
        f'<node class="android.widget.Button" text="SIGN UP" resource-id="android:id/button1" clickable="true" '
        f'package="{PKG}" bounds="[487,740][646,836]"/>'
        '</node></hierarchy>')


# ------------------------------------------------------------------ a classificação
def test_o_dialogo_medido_vira_conta_nao_encontrada() -> None:
    v = classificar_depois_do_envio(do_app(PKG), _dialogo(), package=PKG, expected_username="fulano.teste")
    assert v.outcome is Outcome.CONTA_NAO_ENCONTRADA
    assert v.outcome.terminal, "nunca repete sozinho"
    assert IDENTIFICADOR not in v.detail and "fulano" not in v.detail, "o texto da tela não vai para o motivo"


def test_so_o_titulo_ou_so_o_corpo_ja_bastam() -> None:
    so_titulo = parse_hierarchy(f'<hierarchy><node package="{PKG}" bounds="[0,0][720,1280]">'
                                f'<node text="Can&apos;t find account" package="{PKG}" bounds="[50,436][670,521]"/>'
                                '</node></hierarchy>')
    assert classificar_depois_do_envio(do_app(PKG), so_titulo, package=PKG,
                                       expected_username="x").outcome is Outcome.CONTA_NAO_ENCONTRADA
    v = classificar_depois_do_envio(do_app(PKG), _dialogo(titulo="Aviso"), package=PKG, expected_username="x")
    assert v.outcome is Outcome.CONTA_NAO_ENCONTRADA


@pytest.mark.parametrize(("texto", "esperado"), [
    ("Incorrect password. Please try again.", Outcome.INVALID_CREDENTIAL),
    ("Help us confirm it's you", Outcome.AUTH_CHALLENGE),
    ("Unable to log in", Outcome.UNCERTAIN),
])
def test_as_outras_telas_de_erro_seguem_como_antes(texto: str, esperado: Outcome) -> None:
    tree = parse_hierarchy(f'<hierarchy><node package="{PKG}" bounds="[0,0][720,1280]">'
                           f'<node text="{texto}" package="{PKG}" bounds="[0,0][720,60]"/></node></hierarchy>')
    assert classificar_depois_do_envio(do_app(PKG), tree, package=PKG, expected_username="x").outcome is esperado


# ------------------------------------------------------------------ o identificador mascarado
@pytest.mark.parametrize(("entrada", "saida"), [
    ("fulano.teste@exemplo.com.br", "f***@exemplo.com.br"),
    ("@luciana.bastos73519", "l***"),
    ("+5511999990000", "+***"),
    ("   ", "(identificador vazio)"),
])
def test_identificador_mascarado(entrada: str, saida: str) -> None:
    assert mascarar_identificador(entrada) == saida


def test_o_desfecho_so_existe_na_tabela_declarada() -> None:
    """O carregador aceita o desfecho novo e continua recusando um nome que o motor não conhece."""
    from app.integrations.app_declarado import conhecimento
    assert "conta_nao_encontrada" in conhecimento.DESFECHOS_DEPOIS_DO_ENVIO
    assert "conta_inexistente" not in conhecimento.DESFECHOS_DEPOIS_DO_ENVIO
    assert issubclass(SessaoInvalida, Exception)


# ------------------------------------------------------------------ o motor de sessão, ponta a ponta (aparelho falso)
async def test_login_no_dialogo_nao_retira_a_conta_nem_marca_senha_recusada(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA, conta_inexistente=True)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.CONTA_NAO_ENCONTRADA
        assert r.session_status is SessionStatus.auth_required
        assert f"identificador {USUARIO[0]}***" in r.detail
        assert USUARIO not in r.detail and SENHA not in r.detail

        sessao = repo.session_row(pid)
        assert sessao["status"] == SessionStatus.auth_required.value
        # Não é senha errada: a credencial vai a `review` (o login automático para), nunca a `invalid`.
        assert repo.credential_row(pid)["status"] == "review"
        # Não é bloqueio: nenhuma lápide, e a conta segue na persona.
        assert db.query("SELECT 1 FROM contas_retiradas") == []
        assert db.query("SELECT 1 FROM events WHERE kind='profile.account_retired'") == []
        assert len(repo.list_accounts(pid)) == 1, "a conta segue na persona"
        # Os botões do diálogo (TRY AGAIN, SIGN UP) nunca foram tocados.
        assert app.screen == "conta_nao_encontrada"
        assert db.query("SELECT outcome FROM authentication_attempts WHERE profile_id=?", (pid,))[0]["outcome"] \
            == Outcome.CONTA_NAO_ENCONTRADA.value
    finally:
        db.close()


async def test_nova_chamada_nao_digita_de_novo(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA, conta_inexistente=True)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        await auth.ensure_session(FakeRt(app), pid)
        digitado = len(app.typed)
        app.calls.clear()
        # O login AUTOMÁTICO parou (credencial em `review`); o "Conectar" manual continua podendo tentar de novo.
        r2 = await auth.ensure_session(FakeRt(app), pid, automatic=True)
        assert not r2.ready
        assert len(app.typed) == digitado, "um envio só: o login automático parou"
        assert "submit" not in app.calls
    finally:
        db.close()

"""Item 29.64: UM re-toque verificado em Entrar quando o envio fica incerto com o formulário intacto, e a credencial em
`review` que se reconcilia quando a conta conferida está aberta no aparelho.

Visto no android-13 em 04/10 (c-20261004001548-03701b): o toque em Entrar se perdeu, o formulário ficou preenchido, o
botão habilitado, sem erro nem carregando, e o login fechou `uncertain`; um toque manual, sem redigitar, entrou. Depois
disso a sessão era `session_ready` e a credencial seguia em `review`, travando o login automático da persona.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.integrations.app_declarado.sessao import Outcome, Verdict
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


# ---------------------------------------------------------------- negativos da releitura (revisão do #191)
def _incerto_na_primeira_observacao(auth, depois=None):  # type: ignore[no-untyped-def]
    """A observação depois do envio termina incerta ANTES de a tela mudar (o prazo venceu); `depois` muda a tela
    entre a observação e a releitura. É o caso em que só a conferência explícita da releitura protege."""
    original = auth._watch_after_submit
    chamadas = [0]

    async def observar(*a, **kw):  # type: ignore[no-untyped-def]
        chamadas[0] += 1
        if chamadas[0] == 1:
            if depois is not None:
                depois()
            return Verdict(Outcome.UNCERTAIN, "a tela não mudou depois do envio")
        return await original(*a, **kw)

    auth._watch_after_submit = observar
    return chamadas


@pytest.mark.parametrize("tela", ["desabilitado", "identificador_trocado"])
async def test_formulario_que_nao_esta_intacto_nao_ganha_retoque(tmp_path: Path, tela: str) -> None:
    app = FakeInstagram(stored_password=SENHA, envios_ignorados=1, tela_ao_ignorar=tela)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.UNCERTAIN
        assert _envios(app) == 1
    finally:
        db.close()


@pytest.mark.parametrize("tela", ["erro", "desafio", "feed"])
async def test_tela_que_mudou_depois_da_observacao_nao_ganha_retoque(tmp_path: Path, tela: str) -> None:
    """Erro de credencial (com a senha ainda no campo), desafio ou outra tela na RELEITURA: nada de tocar."""
    app = FakeInstagram(stored_password=SENHA, envios_ignorados=1, tela_ao_ignorar=tela)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        chamadas = _incerto_na_primeira_observacao(auth)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.UNCERTAIN
        assert _envios(app) == 1 and chamadas[0] == 1                 # nem re-toque nem segunda observação
    finally:
        db.close()


async def test_outro_pacote_na_frente_na_releitura_nao_ganha_retoque(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA, envios_ignorados=1)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        _incerto_na_primeira_observacao(auth, depois=lambda: setattr(app, "pacote_forcado", "com.android.chrome"))
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.UNCERTAIN
        assert _envios(app) == 1
    finally:
        db.close()


async def test_conta_parada_no_meio_nao_ganha_retoque(tmp_path: Path) -> None:
    """A mesma pessoa em outro aparelho viu um desafio enquanto este esperava: a conta parou, nada mais se envia."""
    app = FakeInstagram(stored_password=SENHA, envios_ignorados=1)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        original = auth._parada_no_meio
        vistas = [0]

        def parada(conta, *, automatic):  # type: ignore[no-untyped-def]
            vistas[0] += 1
            return ("a conta parou em outro aparelho", "desafio") if vistas[0] > 2 else original(conta,
                                                                                              automatic=automatic)

        auth._parada_no_meio = parada
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.UNCERTAIN
        assert _envios(app) == 1 and vistas[0] == 3                   # as duas de antes do envio e a do re-toque
    finally:
        db.close()


# ---------------------------------------------------------------- reconciliação (revisão do #191)
async def test_reconciliacao_nao_tira_a_pausa_em_vigor(tmp_path: Path) -> None:
    app = FakeInstagram(account=USUARIO, screen="feed", stored_password=SENHA)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        ate = "2099-01-01T00:00:00.000Z"
        repo.mark_credential(pid, status=CREDENCIAL_EM_REVISAO, failed_attempts=2, blocked_until=ate)
        conta = auth._resolver_conta(pid, None)
        auth._reconciliar_revisao(conta, "android-02")
        cred = repo.credential_row(pid)
        assert cred["status"] == "active" and cred["failed_attempts"] == 0
        assert cred["blocked_until"] == ate                          # a pausa é proteção e fica
    finally:
        db.close()


async def test_reconciliacao_vale_na_leitura_sem_login(tmp_path: Path) -> None:
    """`observe_only` não age no aparelho, mas a conta conferida aberta é um fato: a credencial sai de `review`."""
    app = FakeInstagram(account=USUARIO, screen="feed", stored_password=SENHA)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        repo.mark_credential(pid, status=CREDENCIAL_EM_REVISAO, failed_attempts=1)
        r = await auth.ensure_session(FakeRt(app), pid, observe_only=True)
        assert r.ready and not r.attempted_login and app.typed == []
        assert repo.credential_row(pid)["status"] == "active"
    finally:
        db.close()


async def test_a_senha_nao_vaza_no_retoque(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """O campo da senha é lido só como "está vazio?": o valor não vai a log, evento, tentativa, veredito nem exceção."""
    caplog.set_level(logging.DEBUG)
    app = FakeInstagram(stored_password=SENHA, envios_ignorados=1)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.ready and _envios(app) == 2
        despejo = repr(r)
        for t in sorted(db.tables()):
            for row in db.query(f"SELECT * FROM {t}"):               # noqa: S608 - nomes vêm do esquema
                despejo += str(dict(row))
        assert SENHA not in despejo                                   # banco, eventos, tentativas e o resultado
        assert SENHA not in caplog.text                               # nenhuma linha de log
        assert app.typed.count(SENHA) == 1                            # chegou ao aparelho uma vez só
    finally:
        db.close()

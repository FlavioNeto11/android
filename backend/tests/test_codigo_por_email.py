"""ADR-090: o código de confirmação que o Instagram manda por e-mail entra no login automático.

Visto no android-07 em 10/10/2026: depois do envio o app mostrou "Check your email", e o login parava em
`auth_challenge` à espera de uma pessoa. Agora, com a caixa da conta registrada pela ponte, o motor lê o código MAIS NOVO
que o envio, digita pelo canal sensível, toca em "continuar" uma vez e deixa a tabela de desfechos julgar a tela que vem.
Provas `simulated`: Instagram e caixa de e-mail de mentira (`FakeInstagram`, `CaixaFalsa`).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.automation import conhecimento_de_telas as telas_
from app.integrations.app_declarado import conhecimento as declarado
from app.integrations.app_declarado.conhecimento import SessaoInvalida
from app.integrations.app_declarado.sessao import Outcome
from app.modules.identity.application.session_rules import CREDENCIAL_EM_REVISAO

from .fake_instagram import FakeInstagram
from .test_instagram_auth import PKG, SENHA, build, cadastrar

CODIGO = "654321"


class CaixaFalsa:
    """A porta `CodigoDeEmail`: devolve o código se ele chegou DEPOIS do instante pedido (como a real)."""

    def __init__(self, codigo: str | None = CODIGO, *, recebido_em: datetime | None = None) -> None:
        self.codigo = codigo
        self.recebido_em = recebido_em
        self.pedidos: list[tuple[str, datetime, float]] = []

    async def codigo_depois_de(self, account_id: str, desde: datetime, *, espera_s: float) -> str | None:
        self.pedidos.append((account_id, desde, espera_s))
        if self.codigo is None:
            return None
        if self.recebido_em is not None and self.recebido_em < desde:
            return None                                          # o código velho da tentativa anterior não serve
        return self.codigo


def _app(**kw: Any) -> FakeInstagram:
    return FakeInstagram(stored_password=SENHA, two_factor_email_on_login=True, codigo_certo=CODIGO, **kw)


async def test_codigo_novo_da_caixa_e_digitado_e_o_login_conclui(tmp_path: Path) -> None:
    app = _app()
    auth, repo, social, db = build(tmp_path, app)
    try:
        caixa = CaixaFalsa()
        auth.codigo_de_email = caixa
        pid = cadastrar(social)
        r = await auth.ensure_session(app_rt(app), pid)
        assert r.outcome is Outcome.SESSION_READY, r.detail
        assert app.calls.count("submit_code") == 1                # um toque em "continuar", nunca dois
        assert "new_code" not in app.calls                        # nenhum reenvio
        assert len(caixa.pedidos) == 1 and caixa.pedidos[0][0] != ""
        assert repo.credential_row(pid)["status"] == "active"
    finally:
        db.close()


async def test_codigo_recusado_volta_para_a_pessoa_sem_segunda_tentativa(tmp_path: Path) -> None:
    app = _app()
    auth, repo, social, db = build(tmp_path, app)
    try:
        auth.codigo_de_email = CaixaFalsa("000000")               # a caixa entrega um código que o app não aceita
        pid = cadastrar(social)
        r = await auth.ensure_session(app_rt(app), pid)
        assert r.outcome is Outcome.AUTH_CHALLENGE, r.detail
        assert app.calls.count("submit_code") == 1
        assert "new_code" not in app.calls
        assert repo.credential_row(pid)["status"] == CREDENCIAL_EM_REVISAO     # ADR-055: o login automático para
    finally:
        db.close()


@pytest.mark.parametrize("caixa", [CaixaFalsa(None), None], ids=["sem_codigo_no_prazo", "sem_porta"])
async def test_sem_codigo_a_tela_vai_para_a_pessoa_e_nada_e_digitado(tmp_path: Path, caixa: CaixaFalsa | None) -> None:
    app = _app()
    auth, _repo, social, db = build(tmp_path, app)
    try:
        auth.codigo_de_email = caixa
        pid = cadastrar(social)
        r = await auth.ensure_session(app_rt(app), pid)
        assert r.outcome is Outcome.AUTH_CHALLENGE
        assert "submit_code" not in app.calls and not [x for x in app.typed if x.isdigit()]
    finally:
        db.close()


async def test_codigo_mais_velho_que_o_envio_nunca_serve(tmp_path: Path) -> None:
    app = _app()
    auth, _repo, social, db = build(tmp_path, app)
    try:
        auth.codigo_de_email = CaixaFalsa(recebido_em=datetime.now(UTC) - timedelta(hours=1))
        pid = cadastrar(social)
        r = await auth.ensure_session(app_rt(app), pid)
        assert r.outcome is Outcome.AUTH_CHALLENGE
        assert "submit_code" not in app.calls
    finally:
        db.close()


async def test_verificacao_humana_depois_do_codigo_nao_e_tocada(tmp_path: Path) -> None:
    """O código confere e o app responde "confirme que é você": é conta travada (ADR-055) — a tela é só lida."""
    app = _app(apos_codigo="challenge")
    auth, _repo, social, db = build(tmp_path, app)
    try:
        auth.codigo_de_email = CaixaFalsa()
        pid = cadastrar(social)
        r = await auth.ensure_session(app_rt(app), pid)
        assert r.outcome is Outcome.AUTH_CHALLENGE
        assert app.calls.count("submit_code") == 1
        assert app.screen == "challenge"
        depois = app.calls[app.calls.index("submit_code") + 1:]
        assert not [c for c in depois if c.startswith("tap:")], "nada é tocado na tela de verificação"
    finally:
        db.close()


async def test_codigo_por_sms_ou_autenticador_segue_para_a_pessoa(tmp_path: Path) -> None:
    """A tela "6-digit security code" (sem e-mail) não casa o sinal: não há caixa a ler."""
    app = FakeInstagram(stored_password=SENHA, two_factor_on_login=True)
    auth, _repo, social, db = build(tmp_path, app)
    try:
        caixa = CaixaFalsa()
        auth.codigo_de_email = caixa
        pid = cadastrar(social)
        r = await auth.ensure_session(app_rt(app), pid)
        assert r.outcome is Outcome.AUTH_CHALLENGE
        assert caixa.pedidos == []
    finally:
        db.close()


# ---------------------------------------------------------------- o conhecimento declarado
def _dados() -> tuple[dict[str, Any], Any]:
    pasta = Path(__file__).resolve().parents[1] / "app" / "conhecimento" / "apps" / PKG
    return (yaml.safe_load((pasta / "sessao.yaml").read_text(encoding="utf-8")),
            telas_.carregar(pasta / "telas.yaml"))


def test_instagram_declara_o_codigo_por_email() -> None:
    dados, telas = _dados()
    k = declarado.de_dados(dados, telas)
    assert k.codigo_por_email is not None and k.codigo_por_email.tela == "two_factor"
    assert 5 <= k.codigo_por_email.espera_s <= 300


@pytest.mark.parametrize("troca, trecho", [
    ({"tela": "login"}, "dois_fatores"),                           # a tela do código tem de ser de código
    ({"tela": "challenge"}, "dois_fatores"),                       # conta travada nunca
    ({"sinal_da_tela": "nao_existe"}, "sinal"),
    ({"espera_s": 0}, "espera_s"),
    ({"extra": 1}, "extra"),
])
def test_bloco_invalido_e_recusado_na_carga(troca: dict[str, Any], trecho: str) -> None:
    dados, telas = _dados()
    dados["codigo_por_email"] = {**dados["codigo_por_email"], **troca}
    with pytest.raises(SessaoInvalida, match=trecho):
        declarado.de_dados(dados, telas)


def app_rt(app: FakeInstagram) -> Any:
    from .test_instagram_auth import FakeRt
    return FakeRt(app)

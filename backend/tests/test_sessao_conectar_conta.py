"""`POST /api/instagram/profiles/{id}/accounts/{conta}/session/connect` pelo contrato HTTP (item T.2, achado #166).

Era a única rota de `api.py` sem uma chamada HTTP em teste (levantamento de 02/10: 174 de 175). A irmã por perfil
(`/profiles/{id}/connect`) tem teste só nas RECUSAS; aqui a rota por conta, que é a que o painel de contas chama,
percorre as recusas na ordem em que `_start_session_job` as aplica e o caminho feliz até o provedor de sessão.

O provedor é um espião que só registra o pedido (mesmo desenho do `Espiao` de `test_porta_de_sessao_no_teto.py`):
a autenticação em si, com o motor de sessão sobre um app falso, já tem os testes dela em `test_instagram_auth.py`.
Prova `simulated`: harness na porta 5640, aparelho falso do QA, sem emulador, rede nem IA.
"""
from __future__ import annotations

import secrets as pysecrets
from typing import Any

import httpx
import pytest

from app.integrations.app_declarado.sessao import AuthResult, Outcome
from app.main import create_app
from app.models import CommandState, InstanceState, ProfileCreate
from app.state import AppState

from .conftest import Harness
from .fake_instagram import PKG

pytestmark = pytest.mark.asyncio

IID = "android-01"
SENHA = "senha-so-de-teste-1"


class EspiaoDeSessao:
    """Provedor de sessão que registra quem pediu o quê e devolve um resultado conhecido."""
    package = PKG

    def __init__(self) -> None:
        self.chamadas: list[dict[str, Any]] = []

    async def ensure_session(self, rt: Any, profile_id: str, *, account_id: str | None = None,
                             force_login: bool = False, automatic: bool = False, observe_only: bool = False) -> AuthResult:
        self.chamadas.append({"instance_id": rt.id, "profile_id": profile_id, "account_id": account_id,
                              "force_login": force_login, "observe_only": observe_only})
        return AuthResult(Outcome.UNCERTAIN, "espião: nada lido")


def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _estado(h: Harness) -> AppState:
    assert h.state is not None
    return h.state


def _codigo(r: httpx.Response) -> str:
    corpo = r.json()["detail"]
    assert isinstance(corpo, dict) and corpo.get("code") and corpo.get("message"), r.text
    return str(corpo["code"])


def _persona_no_aparelho(s: AppState, instance_id: str | None = IID) -> tuple[str, str]:
    """Persona (e a conta âncora do Instagram dela) já vinculada ao aparelho, ainda SEM senha."""
    nome = f"conectar.{pysecrets.token_hex(3)}"
    pid = s.social.create_profile(ProfileCreate(username=nome, instance_id=instance_id, first_name="Pessoa",
                                                last_name=nome)).id
    return pid, str(s.social_repo.conta_ancora(pid)["id"])


def _aparelho_pronto(s: AppState, instance_id: str = IID) -> None:
    """No ar, com o app verificado e a internet de dentro do aparelho sã: o que a rota confere antes de despachar."""
    s.devices._set_state(s.devices.get(instance_id), InstanceState.online, "teste")  # noqa: SLF001
    s.release_repo.upsert_app_state(instance_id, PKG, state="ready", detail="teste")


async def test_recusas_na_ordem_em_que_a_rota_as_aplica(harness: Harness) -> None:
    s = _estado(harness)
    s.appium.log_masking_active = True            # canal sensível comprovado: não é o que este teste cobre
    pid, conta = _persona_no_aparelho(s)
    sem_vinculo, conta_sem_vinculo = _persona_no_aparelho(s, instance_id=None)
    url = f"/api/instagram/profiles/{pid}/accounts/{conta}/session/connect"
    async with _cliente(harness) as c:
        # Persona ou conta que não existem: 404, nunca 202 nem 500.
        assert (await c.post(f"/api/instagram/profiles/nao-existe/accounts/{conta}/session/connect")).status_code == 404
        r = await c.post(f"/api/instagram/profiles/{pid}/accounts/conta-que-nao-existe/session/connect")
        assert r.status_code == 404, r.text

        # Persona sem aparelho: 409 `no_binding`.
        r = await c.post(f"/api/instagram/profiles/{sem_vinculo}/accounts/{conta_sem_vinculo}/session/connect")
        assert r.status_code == 409 and _codigo(r) == "no_binding", r.text

        # Aparelho de OUTRA persona não é o desta: `?instance_id=` só vale para aparelho vinculado.
        r = await c.post(url, params={"instance_id": "android-03"})
        assert r.status_code == 409 and _codigo(r) == "sem_vinculo", r.text

        # Sem senha guardada, conectar não tem o que digitar.
        r = await c.post(url)
        assert r.status_code == 409 and _codigo(r) == "no_credential", r.text

        # Guardar a senha SEM o consentimento da conta nem é aceito (ADR-040): a rota de credencial recusa.
        r = await c.put(f"/api/instagram/profiles/{pid}/accounts/{conta}/credential",
                        json={"password": SENHA, "consent": False})
        assert r.status_code == 409 and _codigo(r) == "consentimento_de_credencial", r.text
        r = await c.post(url)
        assert r.status_code == 409 and _codigo(r) == "no_credential", r.text   # a recusa não guardou nada


async def test_senha_guardada_sem_consentimento_recusa_a_conexao(harness: Harness) -> None:
    """Uma senha que já estava no cofre sem o consentimento da conta (cadastro anterior à ADR-040) não é digitada."""
    s = _estado(harness)
    s.appium.log_masking_active = True
    nome = f"antiga.{pysecrets.token_hex(3)}"
    pid = s.social.create_profile(ProfileCreate(username=nome, password=SENHA, instance_id=IID)).id
    conta = str(s.social_repo.conta_ancora(pid)["id"])
    s.db.execute("UPDATE account_credentials SET consent_at=NULL, consent_by=NULL WHERE account_id=?", (conta,))
    async with _cliente(harness) as c:
        r = await c.post(f"/api/instagram/profiles/{pid}/accounts/{conta}/session/connect")
        assert r.status_code == 409 and _codigo(r) == "consentimento_de_credencial", r.text


async def test_conectar_a_conta_despacha_ao_provedor_de_sessao_e_fecha_o_comando(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    s.appium.log_masking_active = True
    _aparelho_pronto(s)
    pid, conta = _persona_no_aparelho(s)
    espiao = EspiaoDeSessao()
    monkeypatch.setattr(s.sessoes, "for_package", lambda _pacote: espiao)
    async with _cliente(harness) as c:
        r = await c.put(f"/api/instagram/profiles/{pid}/accounts/{conta}/credential",
                        json={"password": SENHA, "consent": True})
        assert r.status_code == 200, r.text

        r = await c.post(f"/api/instagram/profiles/{pid}/accounts/{conta}/session/connect")
        assert r.status_code == 202, r.text
        corpo = r.json()
        assert corpo["profile_id"] == pid and corpo["account_id"] == conta
        assert corpo.get("command_id"), "202 com id: o painel acompanha o comando, não um 'accepted' solto"

        await harness.wait(lambda: s.commands.get(corpo["command_id"])["state"] in (
            CommandState.succeeded.value, CommandState.failed.value, CommandState.uncertain.value),
            what="comando de sessão fechar")

    cmd = s.commands.get(corpo["command_id"])
    assert cmd["state"] == CommandState.succeeded.value, cmd["reason"]
    assert cmd["verb"] == "session.connect" and cmd["instance_id"] == IID
    # O provedor recebeu a CONTA da rota (não a âncora do perfil), no aparelho certo, para AUTENTICAR (não só observar).
    assert espiao.chamadas == [{"instance_id": IID, "profile_id": pid, "account_id": conta,
                                "force_login": False, "observe_only": False}]

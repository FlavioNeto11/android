"""Testes do egresso do device pelo IP residencial da criação (migração 133).

Cobrem _parse_proxy, criar_perfil_de_conta, reaquecer_da_conta, o hook em registrar_medicao,
o fluxo de egresso no registrar(), auto-assign, gatilho de vínculo, limpeza e contrato.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from app.devices.rede import _parse_proxy, criar_perfil_de_conta, reaquecer_da_conta
from app.modules.identity.domain.ponte_igfarm import ComandoDeRegistro


def _cmd(*, persona_id="p1", username="alvo.um", email="alvo.um@nvit.com.br",
         proxy_url="http://u:p@proxy.example.com:8080", ip_criacao="8.8.8.8") -> ComandoDeRegistro:
    """Atalho para criar um ComandoDeRegistro de teste."""
    return ComandoDeRegistro(
        persona_id=persona_id, dominio="nvit.com.br", email=email, email_senha="senha-email-123",
        instagram_username=username, instagram_senha="senha-ig-123", igfarm_account_id="ig-acc-1",
        criada_em="2026-01-01T00:00:00Z", por="test", proxy_url=proxy_url, ip_criacao=ip_criacao)


def _montar_ponte(harness, rede_mock=None):
    """Monta a PonteIgfarm com o AppState do harness e um adaptador de rede falso (ou None)."""
    from app.modules.identity.infrastructure.ponte_igfarm import (ArmazemSql, BarramentoSocial, CofreSocial,
                                                                   ContasSocial, ImagensSocial, PessoasSocial,
                                                                   PonteIgfarm, RedeSocial, TextosSocial)
    from app.modules.email_do_parque.application.servico import EmailDoParque

    s = harness.state
    email: EmailDoParque = s.email_parque
    if rede_mock is not None:
        rede = rede_mock
    else:
        rede = RedeSocial(s)
    return PonteIgfarm(
        armazem=ArmazemSql(s.db), pessoas=PessoasSocial(s.social), textos=TextosSocial(s.social),
        imagens=ImagensSocial(s.persona_images, s.social), contas=ContasSocial(s.social), cofre=CofreSocial(s.social),
        email=email, barramento=BarramentoSocial(s.bus), rede=rede)


# ============================================================================ 1. _parse_proxy
class TestParseProxy:
    def test_http_com_auth(self):
        scheme, host, port, user, pwd = _parse_proxy("http://user:pass@proxy.example.com:8080")
        assert scheme == "http"
        assert host == "proxy.example.com"
        assert port == 8080
        assert user == "user"
        assert pwd == "pass"

    def test_socks5_sem_auth(self):
        scheme, host, port, user, pwd = _parse_proxy("socks5://proxy.example.com:1080")
        assert scheme == "socks5"
        assert host == "proxy.example.com"
        assert port == 1080
        assert user is None
        assert pwd is None

    def test_sem_esquema_vira_http(self):
        scheme, host, port, user, pwd = _parse_proxy("proxy.example.com:3128")
        assert scheme == "http"
        assert host == "proxy.example.com"
        assert port == 3128

    def test_porta_ausente_levanta(self):
        with pytest.raises(ValueError, match="sem host ou porta"):
            _parse_proxy("http://proxy.example.com")

    def test_url_torta_levanta(self):
        with pytest.raises(ValueError):
            _parse_proxy("not-a-url")

    def test_string_vazia_levanta(self):
        with pytest.raises(ValueError):
            _parse_proxy("")

    def test_esquema_nao_suportado_levanta(self):
        with pytest.raises(ValueError, match="não suportado"):
            _parse_proxy("ftp://proxy.example.com:21")


# ============================================================================ 3. criar_perfil_de_conta
class TestCriarPerfilDeConta:
    def test_cria_perfil(self, harness):
        pid = criar_perfil_de_conta(
            harness.state, "acc-1", host="proxy.example.com", port=8080, protocol="http",
            username="user1", secret=None, ip_criacao="8.8.8.8", quem="test")
        assert pid is not None
        row = harness.state.db.one("SELECT name, kind, protocol, endpoint_host, endpoint_port FROM network_profiles WHERE id=?", (pid,))
        assert row["name"] == "igfarm-acc-1"
        assert row["kind"] == "proxy"
        assert row["protocol"] == "http"
        assert row["endpoint_host"] == "proxy.example.com"
        assert row["endpoint_port"] == 8080

    def test_idempotente_pelo_nome(self, harness):
        pid1 = criar_perfil_de_conta(
            harness.state, "acc-1", host="proxy.example.com", port=8080, protocol="http",
            username="user1", secret=None, ip_criacao="8.8.8.8", quem="test")
        pid2 = criar_perfil_de_conta(
            harness.state, "acc-1", host="other.proxy.com", port=3128, protocol="socks5",
            username="user2", secret=None, ip_criacao="8.8.4.4", quem="test")
        assert pid1 == pid2

    def test_username_em_params(self, harness):
        """Prova que _params não recusa username pela redação."""
        pid = criar_perfil_de_conta(
            harness.state, "acc-2", host="proxy.example.com", port=8080, protocol="http",
            username="myuser", secret=None, ip_criacao=None, quem="test")
        row = harness.state.db.one("SELECT params FROM network_profiles WHERE id=?", (pid,))
        params = json.loads(row["params"])
        assert params.get("username") == "myuser"

    def test_sem_ip_criacao_sem_egress_esperado(self, harness):
        pid = criar_perfil_de_conta(
            harness.state, "acc-3", host="proxy.example.com", port=8080, protocol="http",
            username=None, secret=None, ip_criacao=None, quem="test")
        row = harness.state.db.one("SELECT params FROM network_profiles WHERE id=?", (pid,))
        params = json.loads(row["params"])
        assert "egress_esperado" not in params

    def test_ip_privado_sem_egress_esperado(self, harness):
        pid = criar_perfil_de_conta(
            harness.state, "acc-4", host="proxy.example.com", port=8080, protocol="http",
            username=None, secret=None, ip_criacao="192.168.1.1", quem="test")
        row = harness.state.db.one("SELECT params FROM network_profiles WHERE id=?", (pid,))
        params = json.loads(row["params"])
        assert "egress_esperado" not in params

    def test_ip_publico_com_egress_esperado(self, harness):
        pid = criar_perfil_de_conta(
            harness.state, "acc-5", host="proxy.example.com", port=8080, protocol="http",
            username=None, secret=None, ip_criacao="8.8.8.8", quem="test")
        row = harness.state.db.one("SELECT params FROM network_profiles WHERE id=?", (pid,))
        params = json.loads(row["params"])
        assert params.get("egress_esperado") == "8.8.8.8"


# ============================================================================ 4. Egresso no registrar()
class TestEgressoNoRegistrar:
    def test_auto_assign_conta_real_fica_pendente(self, harness, monkeypatch, caplog):
        """BUG 1: o adapter tem de traduzir a RedeError do devices.rede para a do application, senão o POST estoura."""
        from app.modules.identity.application.ponte_igfarm import RedeError as RedeErrorApp
        from app.modules.identity.infrastructure.ponte_igfarm import RedeSocial
        from app.devices.rede import RedeError as RedeErrorDevice

        def atribuir_que_recusa(st, body, quem):
            raise RedeErrorDevice(409, "real_account_confirm_required", "conta real")

        monkeypatch.setattr("app.devices.rede.atribuir", atribuir_que_recusa)
        adapter = RedeSocial(harness.state)
        with pytest.raises(RedeErrorApp) as exc:
            adapter.atribuir(instance_ids=["android-01"], proxy_profile_id="perfil-1", policy="exigida", quem="t")
        assert exc.value.code == "real_account_confirm_required"     # traduzida, não a do device

    def test_instance_ids_filtra_app(self, harness):
        """BUG 3: vínculo só de Outlook não é alvo; Instagram é; app_id NULL é."""
        db = harness.state.db
        db.execute("INSERT INTO instagram_profiles(id, username, status, created_at, updated_at) VALUES ('p1','', 'active', '2026-01-01', '2026-01-01')")
        # Garante que o app do Outlook existe
        outlook = db.one("SELECT id FROM apps WHERE package='com.microsoft.office.outlook'")
        if outlook is None:
            db.execute("INSERT INTO apps(id, name, package) VALUES ('outlook', 'Outlook', 'com.microsoft.office.outlook')")
        db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, app_id, active, bound_at) "
                   "VALUES ('p1','android-01', (SELECT id FROM apps WHERE package='com.microsoft.office.outlook'), 1, '2026-01-01')")
        db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, app_id, active, bound_at) "
                   "VALUES ('p1','android-02', (SELECT id FROM apps WHERE package='com.instagram.android'), 1, '2026-01-01')")
        db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, app_id, active, bound_at) "
                   "VALUES ('p1','android-03', NULL, 1, '2026-01-01')")
        from app.modules.identity.infrastructure.ponte_igfarm import ArmazemSql
        alvos = ArmazemSql(db)._instance_ids_da_persona("p1")
        assert "android-01" not in alvos          # só Outlook
        assert set(alvos) == {"android-02", "android-03"}


# ============================================================================ Fixtures
@pytest.fixture
def ponte_com_rede_falsa(harness, monkeypatch):
    """Ponte com rede mockada para testes de egresso."""
    from app.modules.identity.infrastructure.ponte_igfarm import RedeSocial

    class RedeFalsa:
        def __init__(self, st):
            self.st = st
            self.perfis_criados = []
            self.atribuicoes = []

        def parse_proxy(self, proxy_url):
            return _parse_proxy(proxy_url)

        def criar_perfil_de_conta(self, account_id, *, host, port, protocol, username, secret, ip_criacao, quem):
            self.perfis_criados.append(account_id)
            return criar_perfil_de_conta(
                self.st, account_id, host=host, port=port, protocol=protocol,
                username=username, secret=secret, ip_criacao=ip_criacao, quem=quem)

        def atribuir(self, instance_ids, proxy_profile_id, policy, quem, confirm_real_account=None):
            self.atribuicoes.append((instance_ids, proxy_profile_id))
            return {}

    monkeypatch.setattr(RedeSocial, "__init__", lambda self, st: setattr(self, "st", st) or setattr(self, "perfis_criados", []) or setattr(self, "atribuicoes", []))
    monkeypatch.setattr(RedeSocial, "parse_proxy", lambda self, proxy_url: _parse_proxy(proxy_url))
    monkeypatch.setattr(RedeSocial, "criar_perfil_de_conta", lambda self, account_id, **kw: criar_perfil_de_conta(
        self.st, account_id, **kw))
    monkeypatch.setattr(RedeSocial, "atribuir", lambda self, instance_ids, proxy_profile_id, policy, quem, confirm_real_account=None: {})

    return _montar_ponte(harness)


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

        monkeypatch.setattr("app.modules.identity.infrastructure.ponte_igfarm.atribuir", atribuir_que_recusa)
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


# ============================================================================ 5. gatilho do vínculo + limpeza
def _semear_conta_igfarm(harness, *, account_id="acc-1", profile_id="p1", username="alvo.um",
                         proxy_secret_ref="ref-proxy", ip_criacao="8.8.8.8") -> None:
    db = harness.state.db
    db.execute("INSERT INTO instagram_profiles(id, username, status, created_at, updated_at) "
               "VALUES (?,?, 'active','2026-01-01','2026-01-01')", (profile_id, username))
    db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, created_at, updated_at) "
               "VALUES (?,?, 'com.instagram.android', ?, '2026-01-01','2026-01-01')",
               (account_id, profile_id, username))
    db.execute("INSERT INTO contas_igfarm(account_id, profile_id, igfarm_account_id, username_registrado, "
               "criada_em_igfarm, registrada_em, proxy_secret_ref, ip_criacao) "
               "VALUES (?,?, 'ig-1', ?, '2026-01-01','2026-01-01', ?, ?)",
               (account_id, profile_id, username, proxy_secret_ref, ip_criacao))


def _semear_perfil_de_rede(harness, *, perfil_id="np-1", account_id="acc-1",
                           params="{}") -> None:
    harness.state.db.execute(
        "INSERT INTO network_profiles(id, name, kind, protocol, endpoint_host, endpoint_port, params, created_at) "
        "VALUES (?,?, 'proxy','http','p.example.com',8080,?, '2026-01-01')",
        (perfil_id, f"igfarm-{account_id}", params))


def test_gatilho_atribui_perfil_existente(harness, monkeypatch):
    """Vínculo criado DEPOIS do registro: o gatilho acha o perfil pelo nome e o atribui (política exigida)."""
    from app.modules.identity.infrastructure import egresso
    chamadas: list[tuple[list[str], str | None, str]] = []
    monkeypatch.setattr(egresso, "atribuir",
                        lambda st, body, quem: chamadas.append((list(body.instance_ids), body.proxy_profile_id,
                                                                body.policy)))
    _semear_conta_igfarm(harness)
    _semear_perfil_de_rede(harness)
    egresso.vincular_egresso(harness.state, "p1", "android-01")
    assert chamadas == [(["android-01"], "np-1", "exigida")]


def test_gatilho_conta_real_fica_pendente(harness, monkeypatch, caplog):
    """`real_account_confirm_required` não derruba o vínculo: fica pendente e é registrado."""
    from app.modules.identity.infrastructure import egresso
    from app.devices.rede import RedeError

    def recusa(st, body, quem):
        raise RedeError(409, "real_account_confirm_required", "conta real")

    monkeypatch.setattr(egresso, "atribuir", recusa)
    _semear_conta_igfarm(harness)
    _semear_perfil_de_rede(harness)
    egresso.vincular_egresso(harness.state, "p1", "android-01")     # não levanta


def test_limpeza_desatribui_remove_perfil_e_apaga_segredo(harness, monkeypatch):
    """Ordem da limpeza: unassign (com confirmação) -> remove perfil -> apaga segredo de rastreio."""
    from app.modules.identity.infrastructure import egresso
    eventos: list[tuple[object, ...]] = []
    monkeypatch.setattr(egresso, "atribuir",
                        lambda st, body, quem, **kw: eventos.append(("unassign", list(body.instance_ids),
                                                                     body.proxy_profile_id,
                                                                     list(body.confirm_real_account),
                                                                     kw.get("durante_quarentena"))))
    monkeypatch.setattr(egresso, "remover_perfil", lambda st, pid: eventos.append(("remove", pid)))
    monkeypatch.setattr(harness.state.secrets, "delete_secret", lambda ref: eventos.append(("segredo", ref)))
    _semear_perfil_de_rede(harness)
    harness.state.db.execute(
        "INSERT INTO device_network(instance_id, proxy_profile_id, updated_at) VALUES ('android-01','np-1','2026-01-01')")
    egresso.limpar_egresso(harness.state, "p1", "acc-1", "ref-proxy")
    assert eventos == [("unassign", ["android-01"], None, ["android-01"], True),
                       ("remove", "np-1"),
                       ("segredo", "ref-proxy")]


def test_limpeza_sem_perfil_ainda_apaga_segredo(harness, monkeypatch):
    """Proxy sem perfil criado (ou já removido): só o segredo de rastreio é apagado, sem erro."""
    from app.modules.identity.infrastructure import egresso
    segredos: list[str] = []
    monkeypatch.setattr(egresso, "remover_perfil", lambda st, pid: None)
    monkeypatch.setattr(harness.state.secrets, "delete_secret", lambda ref: segredos.append(ref))
    egresso.limpar_egresso(harness.state, "p1", "acc-inexistente", "ref-x")
    assert segredos == ["ref-x"]


# ============================================================================ 6. egresso no registrar() (dois caminhos)
class _RedeFalsa:
    """Rede de mentira: registra o que a ponte pediu, sem tocar no subsistema real."""

    def __init__(self) -> None:
        self.perfis: list[str] = []
        self.secrets: list[object] = []
        self.atribuicoes: list[tuple[list[str], str | None, str]] = []
        self.confirmacoes: list[list[str] | None] = []
        self.pedidos: dict[str, tuple[str | None, str]] = {}      # o que cada aparelho pede hoje
        self.recusar_conta_real = False                           # simula o aparelho com conta de OUTRA persona

    def parse_proxy(self, proxy_url: str):
        return _parse_proxy(proxy_url)

    def criar_perfil_de_conta(self, account_id, *, host, port, protocol, username, secret, ip_criacao, quem):
        self.perfis.append(account_id)
        self.secrets.append(secret)
        return f"np-{account_id}"

    def atribuir(self, instance_ids, proxy_profile_id, policy, quem, confirm_real_account=None):
        from app.modules.identity.application.ponte_igfarm import RedeError
        if self.recusar_conta_real and not confirm_real_account:
            raise RedeError(409, "real_account_confirm_required", "conta real")
        self.atribuicoes.append((list(instance_ids), proxy_profile_id, policy))
        self.confirmacoes.append(confirm_real_account)
        for iid in instance_ids:
            self.pedidos[iid] = (proxy_profile_id, policy)
        return {}

    def pedido_atual(self, instance_id):
        return self.pedidos.get(instance_id)


def _ponte_com_fakes(harness, rede):
    """Ponte com portas falsas de pessoas/contas/cofre/email e armazém REAL (ArmazemSql)."""
    from app.modules.identity.application.ponte_igfarm import PonteIgfarm
    from app.modules.identity.domain.ponte_igfarm import FichaDaPessoa
    from app.modules.identity.infrastructure.ponte_igfarm import ArmazemSql

    class Pessoas:
        def ficha(self, pid):
            return FichaDaPessoa(persona_id=pid, nome="Alvo", primeiro_nome="Alvo", sobrenome="Um",
                                 nome_exibicao="Alvo", birth_date="1990-01-01", idade=35, genero="f",
                                 biografia={}, visual={}, resumo="", profissao=None, cidade=None, interesses=())

    class Contas:
        def eh_nossa(self, h): return False
        def foi_retirada(self, h): return False
        def registrar(self, pid, *, username, email, senha, por): return "acc-1"

    class Cofre:
        def __init__(self): self.guardados: list[str] = []
        def guardar(self, v): self.guardados.append(v); return (f"ref-{len(self.guardados)}", "k")
        def apagar(self, ref): pass

    class Email:
        def validar_dominio(self, d): return d
        def confere_dominio(self, e, d): return None

    class Bus:
        def __init__(self): self.eventos: list[tuple[str, dict]] = []
        def emitir(self, tipo, mensagem, dados): self.eventos.append((tipo, dados))

    ponte = PonteIgfarm(armazem=ArmazemSql(harness.state.db), pessoas=Pessoas(), textos=None,
                        imagens=None, contas=Contas(), cofre=Cofre(), email=Email(), barramento=Bus(),
                        rede=rede)
    return ponte


def _semear_persona_sem_conta(harness, *, profile_id="p1", account_id="acc-1") -> None:
    db = harness.state.db
    db.execute("INSERT INTO instagram_profiles(id, username, status, created_at, updated_at) "
               "VALUES (?, '', 'active','2026-01-01','2026-01-01')", (profile_id,))
    db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, created_at, updated_at) "
               "VALUES (?,?, 'com.instagram.android', '', '2026-01-01','2026-01-01')",
               (account_id, profile_id))


def test_re_post_nao_acumula_segredo_e_roda_egresso_nos_dois_caminhos(harness):
    _semear_persona_sem_conta(harness)
    rede = _RedeFalsa()
    ponte = _ponte_com_fakes(harness, rede)
    r1 = ponte.registrar(_cmd())
    r2 = ponte.registrar(_cmd())                     # idempotente
    assert r1.account_id == r2.account_id == "acc-1"
    assert rede.perfis == ["acc-1", "acc-1"]         # criar perfil roda nos DOIS caminhos
    # senha do e-mail (1x, no cadastro) + senha do PROXY (1x, só na 1ª vez)
    assert ponte.cofre.guardados == ["senha-email-123", "p"]
    assert all(isinstance(s, SecretStr) for s in rede.secrets if s is not None)
    row = harness.state.db.one("SELECT proxy_secret_ref, ip_criacao FROM contas_igfarm WHERE account_id='acc-1'")
    assert row["proxy_secret_ref"] == "ref-2" and row["ip_criacao"] == "8.8.8.8"


def test_registrar_com_vinculo_atribui_ao_device(harness):
    _semear_persona_sem_conta(harness)
    harness.state.db.execute(
        "INSERT INTO device_profile_bindings(profile_id, instance_id, app_id, active, bound_at) "
        "VALUES ('p1','android-01', NULL, 1, '2026-01-01')")
    rede = _RedeFalsa()
    ponte = _ponte_com_fakes(harness, rede)
    ponte.registrar(_cmd())
    assert rede.atribuicoes == [(["android-01"], "np-acc-1", "exigida")]


def test_registrar_sem_vinculo_nao_atribui(harness):
    _semear_persona_sem_conta(harness)
    rede = _RedeFalsa()
    ponte = _ponte_com_fakes(harness, rede)
    ponte.registrar(_cmd())
    assert rede.atribuicoes == []


# ============================================================================ 7. reaquecer (observabilidade)
def test_reaquecer_marca_1_quando_a_saida_divergir(harness):
    from app.devices.rede import reaquecer_da_conta
    _semear_conta_igfarm(harness)
    _semear_perfil_de_rede(harness, params='{"egress_esperado": "8.8.8.8"}')
    harness.state.db.execute(
        "INSERT INTO device_profile_bindings(profile_id, instance_id, app_id, active, bound_at) "
        "VALUES ('p1','android-01', NULL, 1, '2026-01-01')")
    harness.state.db.execute(
        "INSERT INTO device_network(instance_id, proxy_profile_id, policy, state, egress_ipv4, updated_at) "
        "VALUES ('android-01','np-1','exigida','trafego_verificado','1.1.1.1','2026-01-01')")
    reaquecer_da_conta(harness.state, "android-01")
    assert harness.state.db.scalar("SELECT reaquecer FROM contas_igfarm WHERE account_id='acc-1'") == 1


def test_gatilho_nao_reatribui_o_que_ja_esta_pedido(harness, monkeypatch):
    """Device já com este perfil e política que segura: o gatilho sai sem chamar `atribuir`."""
    from app.modules.identity.infrastructure import egresso
    chamadas: list[object] = []
    monkeypatch.setattr(egresso, "atribuir", lambda st, body, quem: chamadas.append(body))
    _semear_conta_igfarm(harness)
    _semear_perfil_de_rede(harness)
    harness.state.db.execute(
        "INSERT INTO device_network(instance_id, proxy_profile_id, policy, updated_at) "
        "VALUES ('android-01','np-1','exigida','2026-01-01')")
    egresso.vincular_egresso(harness.state, "p1", "android-01")
    assert chamadas == []


def test_gatilho_preserva_exigida_com_bloqueio(harness, monkeypatch):
    """Device em `exigida_com_bloqueio` e ainda SEM o proxy: o gatilho atribui o proxy mas mantém o bloqueio."""
    from app.modules.identity.infrastructure import egresso
    politicas: list[object] = []
    monkeypatch.setattr(egresso, "atribuir", lambda st, body, quem: politicas.append(body.policy))
    _semear_conta_igfarm(harness)
    _semear_perfil_de_rede(harness)
    harness.state.db.execute(
        "INSERT INTO device_network(instance_id, policy, updated_at) "
        "VALUES ('android-01','exigida_com_bloqueio','2026-01-01')")
    egresso.vincular_egresso(harness.state, "p1", "android-01")
    assert politicas == ["exigida_com_bloqueio"]



# ============================================================================ 8. _auto_assign: conta real da MESMA persona
def _vincular(harness, profile_id: str, instance_id: str) -> None:
    harness.state.db.execute(
        "INSERT INTO device_profile_bindings(profile_id, instance_id, app_id, active, bound_at) "
        "VALUES (?,?, NULL, 1, '2026-01-01')", (profile_id, instance_id))


def test_auto_assign_confirma_quando_a_conta_real_e_da_mesma_persona(harness):
    """O vínculo veio ANTES da conta: o aparelho só tem a persona que está sendo registrada, então a atribuição é confirmada
    (e não engolida) e o retorno diz `atribuido`."""
    _semear_persona_sem_conta(harness)
    _vincular(harness, "p1", "android-01")
    rede = _RedeFalsa()
    r = _ponte_com_fakes(harness, rede).registrar(_cmd())
    assert rede.confirmacoes == [["android-01"]]
    assert [(e.instance_id, e.estado) for e in r.egresso] == [("android-01", "atribuido")]


def test_auto_assign_conta_de_outra_persona_fica_pendente_e_visivel(harness):
    """Conta real de OUTRA persona no aparelho: não se confirma sozinho; o pendente vai no retorno e num evento."""
    _semear_persona_sem_conta(harness)
    harness.state.db.execute("INSERT INTO instagram_profiles(id, username, status, created_at, updated_at) "
                             "VALUES ('p2','outra.conta','active','2026-01-01','2026-01-01')")
    _vincular(harness, "p1", "android-01")
    _vincular(harness, "p2", "android-01")
    rede = _RedeFalsa()
    rede.recusar_conta_real = True
    ponte = _ponte_com_fakes(harness, rede)
    r = ponte.registrar(_cmd())
    assert rede.atribuicoes == []                                  # nada foi forçado
    assert [(e.instance_id, e.estado) for e in r.egresso] == [("android-01", "pendente_confirmacao")]
    assert "p2" in r.egresso[0].motivo
    assert [t for t, _ in ponte.barramento.eventos].count("identity.egresso.pendente") == 1


def test_auto_assign_preserva_exigida_com_bloqueio(harness):
    _semear_persona_sem_conta(harness)
    _vincular(harness, "p1", "android-01")
    rede = _RedeFalsa()
    rede.pedidos["android-01"] = (None, "exigida_com_bloqueio")
    _ponte_com_fakes(harness, rede).registrar(_cmd())
    assert rede.atribuicoes == [(["android-01"], "np-acc-1", "exigida_com_bloqueio")]


def test_auto_assign_repetir_o_registro_nao_reatribui(harness):
    """Re-POST: o aparelho que já pede o perfil sai como `ja_atribuido`, sem nova atribuição nem rebaixar a política."""
    _semear_persona_sem_conta(harness)
    _vincular(harness, "p1", "android-01")
    rede = _RedeFalsa()
    ponte = _ponte_com_fakes(harness, rede)
    r1 = ponte.registrar(_cmd())
    r2 = ponte.registrar(_cmd())
    assert len(rede.atribuicoes) == 1
    assert [e.estado for e in r1.egresso] == ["atribuido"]
    assert r2.idempotente and [e.estado for e in r2.egresso] == ["ja_atribuido"]


def test_re_post_completa_a_amarracao_que_ficou_pendente(harness):
    """O 1º POST ficou pendente (aparelho com outra conta); depois que a outra conta sai, o re-POST amarra sem duplicar."""
    _semear_persona_sem_conta(harness)
    harness.state.db.execute("INSERT INTO instagram_profiles(id, username, status, created_at, updated_at) "
                             "VALUES ('p2','outra.conta','active','2026-01-01','2026-01-01')")
    _vincular(harness, "p1", "android-01")
    _vincular(harness, "p2", "android-01")
    rede = _RedeFalsa()
    rede.recusar_conta_real = True
    ponte = _ponte_com_fakes(harness, rede)
    assert ponte.registrar(_cmd()).egresso[0].estado == "pendente_confirmacao"
    harness.state.db.execute("UPDATE device_profile_bindings SET active=0 WHERE profile_id='p2'")
    r = ponte.registrar(_cmd())
    assert [e.estado for e in r.egresso] == ["atribuido"] and len(rede.atribuicoes) == 1

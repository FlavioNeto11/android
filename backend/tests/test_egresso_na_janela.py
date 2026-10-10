"""31.329: o login mede o egresso DENTRO da janela do login e aborta se a saída não for a esperada (simulated).

Visto em 10/10/2026: a sessão sticky do proxy do igfarm girou entre uma medição de 18:48Z e o login de 18:58Z, e ninguém soube se o
egresso ainda casava quando a senha foi digitada. Agora o motor de sessão pede uma medição nova ANTES de qualquer digitação; se a
saída medida não for a que o perfil de rede espera (`egress_esperado`), o login aborta com o motivo "egresso não casou", sem
tocar na tela e sem abrir tentativa, e a medição (IP, hora, distância em segundos) vai ao evento `session.egresso_na_janela`.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.devices import rede_convergencia
from app.integrations.app_declarado.sessao import Outcome
from app.shared.egresso import EgressoNaJanela

from .fake_instagram import FakeInstagram
from .test_instagram_auth import SENHA, build, cadastrar
from .test_codigo_por_email import app_rt

ESPERADO = "58.225.220.174"


def _janela(medido: str | None, *, distancia: float = 1.0) -> EgressoNaJanela:
    return EgressoNaJanela(esperado=ESPERADO, medido=medido, medido_em="2026-10-10T19:02:00.000Z", distancia_s=distancia,
                           casou=medido == ESPERADO and distancia <= 30.0, medicao_id=7)


def _eventos(db: Any, kind: str) -> list[Any]:
    return list(db.query("SELECT message, data FROM events WHERE kind=?", (kind,)))


async def test_egresso_que_nao_casa_aborta_o_login_sem_digitar_nem_abrir_tentativa(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA)
    auth, _repo, social, db = build(tmp_path, app)
    try:
        async def medir(rt: Any) -> EgressoNaJanela:
            return _janela("38.211.146.161")
        auth.devices.egresso_na_janela = medir
        pid = cadastrar(social)
        r = await auth.ensure_session(app_rt(app), pid)
        assert r.outcome is Outcome.UNCERTAIN
        assert "egresso não casou" in r.detail and ESPERADO in r.detail and "38.211.146.161" in r.detail
        assert "submit" not in app.calls and not any(c.startswith("type") for c in app.calls)    # nada digitado
        assert db.scalar("SELECT COUNT(*) FROM authentication_attempts") == 0          # nenhuma tentativa aberta
        ev = _eventos(db, "session.egresso_na_janela")
        assert len(ev) == 1 and "egresso não casou" in ev[0]["message"]
        assert ESPERADO in str(ev[0]["data"]) and "distancia_s" in str(ev[0]["data"])
    finally:
        db.close()


async def test_medicao_sem_ip_tambem_aborta(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA)
    auth, _repo, social, db = build(tmp_path, app)
    try:
        async def medir(rt: Any) -> EgressoNaJanela:
            return _janela(None)
        auth.devices.egresso_na_janela = medir
        r = await auth.ensure_session(app_rt(app), cadastrar(social))
        assert r.outcome is Outcome.UNCERTAIN and "não obteve IP" in r.detail
    finally:
        db.close()


async def test_egresso_que_casa_deixa_o_login_seguir_e_registra_a_medicao(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA)
    auth, _repo, social, db = build(tmp_path, app)
    try:
        async def medir(rt: Any) -> EgressoNaJanela:
            return _janela(ESPERADO, distancia=2.5)
        auth.devices.egresso_na_janela = medir
        r = await auth.ensure_session(app_rt(app), cadastrar(social))
        assert r.outcome is Outcome.SESSION_READY, r.detail
        ev = _eventos(db, "session.egresso_na_janela")
        assert len(ev) == 1 and "casou" in ev[0]["message"] and "2.5" in str(ev[0]["data"])
    finally:
        db.close()


async def test_sem_perfil_esperado_ou_sem_gancho_o_login_segue_como_sempre(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA)
    auth, _repo, social, db = build(tmp_path, app)
    try:
        r = await auth.ensure_session(app_rt(app), cadastrar(social))          # FakeDevices sem o gancho
        assert r.outcome is Outcome.SESSION_READY, r.detail
        assert _eventos(db, "session.egresso_na_janela") == []
    finally:
        db.close()


async def test_medicao_com_distancia_maior_que_a_janela_nao_casa(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA)
    auth, _repo, social, db = build(tmp_path, app)
    try:
        async def medir(rt: Any) -> EgressoNaJanela:
            return EgressoNaJanela(esperado=ESPERADO, medido=ESPERADO, medido_em="x", distancia_s=45.0, casou=False)
        auth.devices.egresso_na_janela = medir
        r = await auth.ensure_session(app_rt(app), cadastrar(social))
        assert r.outcome is Outcome.UNCERTAIN and "janela de 30 s" in r.detail
    finally:
        db.close()


# ---------------------------------------------------------------- a medição na convergência de rede
def _semear(harness: Any, *, esperado: str | None = ESPERADO, proxy: bool = True) -> Any:
    db = harness.state.db
    params = '{"egress_esperado": "%s"}' % esperado if esperado else "{}"
    db.execute("INSERT INTO network_profiles(id, name, kind, protocol, endpoint_host, endpoint_port, params, created_at) "
               "VALUES ('np-j','igfarm-acc-j','proxy','http','p.example.com',8080,?, '2026-01-01')", (params,))
    db.execute("INSERT INTO device_network(instance_id, policy, desired_rev, applied_rev, state, proxy_profile_id, updated_at) "
               "VALUES ('android-01','exigida',1,1,'trafego_verificado',?, '2026-01-01')", ("np-j" if proxy else None,))
    conv = harness.state.rede_convergencia
    conv._aparelho = lambda st, rt: None
    return SimpleNamespace(id="android-01")


async def test_convergencia_mede_registra_e_compara(harness: Any, monkeypatch: Any) -> None:
    rt = _semear(harness)

    async def saida(ap: Any, cfg: Any) -> tuple[str | None, str | None, str]:
        return ESPERADO, None, "IPv4 via teste"
    monkeypatch.setattr(rede_convergencia, "medir_saida", saida)
    j = await harness.state.rede_convergencia.medir_para_o_login(rt)
    assert j is not None and j.casou and j.medido == ESPERADO and j.medicao_id
    linha = harness.state.db.one("SELECT method, egress_ipv4 FROM network_measurements WHERE id=?", (j.medicao_id,))
    assert linha["egress_ipv4"] == ESPERADO and "janela do login" in linha["method"]


async def test_convergencia_ip_diferente_e_falha_de_sonda_nao_casam(harness: Any, monkeypatch: Any) -> None:
    rt = _semear(harness)

    async def outro(ap: Any, cfg: Any) -> tuple[str | None, str | None, str]:
        return "131.161.219.173", None, "x"

    async def quebra(ap: Any, cfg: Any) -> tuple[str | None, str | None, str]:
        raise RuntimeError("adb caiu")
    monkeypatch.setattr(rede_convergencia, "medir_saida", outro)
    assert not (await harness.state.rede_convergencia.medir_para_o_login(rt)).casou
    monkeypatch.setattr(rede_convergencia, "medir_saida", quebra)
    j = await harness.state.rede_convergencia.medir_para_o_login(rt)
    assert not j.casou and j.medido is None and "não concluiu" in j.detalhe


async def test_convergencia_sem_proxy_ou_sem_esperado_nao_mede(harness: Any, monkeypatch: Any) -> None:
    rt = _semear(harness, proxy=False)

    async def nunca(ap: Any, cfg: Any) -> tuple[str | None, str | None, str]:
        raise AssertionError("não devia medir")
    monkeypatch.setattr(rede_convergencia, "medir_saida", nunca)
    assert await harness.state.rede_convergencia.medir_para_o_login(rt) is None
    harness.state.db.execute("UPDATE device_network SET proxy_profile_id='np-j' WHERE instance_id='android-01'")
    harness.state.db.execute("UPDATE network_profiles SET params='{}' WHERE id='np-j'")
    assert await harness.state.rede_convergencia.medir_para_o_login(rt) is None

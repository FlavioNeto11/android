"""31.322: o bloqueio guarda o MOTIVO como dado e a fila o chama de "bloqueada" (simulated: harness, nenhum aparelho).

A conta que cai na tela humana sai da plataforma na hora (ADR-068). Antes, só ficava "caiu". Agora a lápide
(`contas_retiradas.motivo_do_bloqueio`, migração 136) e o evento `profile.account_retired` dizem o padrão:
egresso esperado x medido, IPs distintos desde a criação, minutos até o primeiro login e o trecho da tela.
"""
from __future__ import annotations

import json

from app.modules.identity.application.session_rules import (ROTULO_AGUARDANDO, ROTULO_BLOQUEADA,
                                                            emit_needs_person_change, rotulo_da_fila)
from app.modules.identity.infrastructure.motivo_bloqueio import motivo_do_bloqueio

CRIADA = "2026-10-10T01:00:00.000Z"


def _semear(harness, *, ip_criacao="50.72.136.246", handle="alvo.bloq", medidas=(), tentativa="2026-10-10T01:12:00.000Z"):
    db = harness.state.db
    db.execute("INSERT INTO instagram_profiles(id, username, status, created_at, updated_at) "
               "VALUES ('p-bloq', ?, 'active','2026-01-01','2026-01-01')", (handle,))
    db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, created_at, updated_at) "
               "VALUES ('acc-bloq','p-bloq','com.instagram.android', ?, '2026-01-01','2026-01-01')", (handle,))
    db.execute("INSERT INTO contas_igfarm(account_id, profile_id, igfarm_account_id, username_registrado, "
               "criada_em_igfarm, registrada_em, ip_criacao) VALUES ('acc-bloq','p-bloq','ig-1', ?, ?, ?, ?)",
               (handle, CRIADA, CRIADA, ip_criacao))
    db.execute("INSERT INTO account_sessions(account_id, instance_id, status, updated_at, status_since) "
               "VALUES ('acc-bloq','android-07','auth_challenge','2026-10-10T01:13:00.000Z','2026-10-10T01:13:00.000Z')")
    for quando, ip in medidas:
        db.execute("INSERT INTO network_measurements(instance_id, measured_at, method, egress_ipv4) "
                   "VALUES ('android-07', ?, 'probe', ?)", (quando, ip))
    if tentativa:
        db.execute("INSERT INTO authentication_attempts(profile_id, instance_id, started_at, outcome, account_id) "
                   "VALUES ('p-bloq','android-07', ?, 'auth_challenge','acc-bloq')", (tentativa,))


def test_motivo_monta_o_padrao_de_egresso_divergente(harness):
    _semear(harness, medidas=[("2026-10-10T01:05:00.000Z", "50.72.136.246"), ("2026-10-10T01:11:00.000Z", "38.211.146.161")])
    m = motivo_do_bloqueio(harness.state.db, "p-bloq", "acc-bloq", "conta_travada: “Confirm you're human”")
    assert m["egresso_esperado"] == "50.72.136.246" and m["egresso_medido"] == "38.211.146.161"
    assert m["egresso_divergente"] is True
    assert m["ips_distintos_desde_criacao"] == 2
    assert m["minutos_ate_o_primeiro_login"] == 12.0
    assert "Confirm" in str(m["trecho_da_tela"])


def test_motivo_sem_medida_nao_inventa(harness):
    _semear(harness, ip_criacao="", tentativa="")
    m = motivo_do_bloqueio(harness.state.db, "p-bloq", "acc-bloq", None)
    assert m["egresso_medido"] is None and m["egresso_divergente"] is None
    assert m["minutos_ate_o_primeiro_login"] is None and m["trecho_da_tela"] is None


def test_retirada_grava_o_motivo_na_lapide_e_no_evento(harness):
    _semear(harness, medidas=[("2026-10-10T01:11:00.000Z", "38.211.146.161")])
    harness.state.social.motivo_do_bloqueio = lambda pid, aid, ev: motivo_do_bloqueio(harness.state.db, pid, aid, ev)
    r = harness.state.social.retirar_conta_bloqueada("p-bloq", "acc-bloq", origem="observado", autor="teste",
                                                     evidencia="conta_travada: “Confirm you're human”")
    assert r["retirada"] is True
    linha = harness.state.db.one("SELECT motivo_do_bloqueio FROM contas_retiradas WHERE profile_id='p-bloq'")
    motivo = json.loads(linha["motivo_do_bloqueio"])
    assert motivo["egresso_divergente"] is True and motivo["egresso_medido"] == "38.211.146.161"


def test_falha_ao_montar_o_motivo_nao_impede_a_retirada(harness):
    _semear(harness)

    def quebra(*_a):
        raise RuntimeError("falha")
    harness.state.social.motivo_do_bloqueio = quebra
    r = harness.state.social.retirar_conta_bloqueada("p-bloq", "acc-bloq", origem="observado", autor="teste")
    assert r["retirada"] is True
    linha = harness.state.db.one("SELECT motivo_do_bloqueio FROM contas_retiradas WHERE profile_id='p-bloq'")
    assert linha["motivo_do_bloqueio"] is None


def test_rotulo_da_fila_distingue_bloqueada_de_aguardando():
    assert rotulo_da_fila("conta_travada: “Confirm you're human”") == ROTULO_BLOQUEADA
    assert rotulo_da_fila("conta retirada por bloqueio") == ROTULO_BLOQUEADA
    assert rotulo_da_fila("codigo_de_email: “Enter confirmation code”") == ROTULO_AGUARDANDO
    assert rotulo_da_fila(None) == ROTULO_AGUARDANDO


class _Barramento:
    def __init__(self) -> None:
        self.eventos: list[tuple[str, str, dict]] = []

    def emit(self, kind, message, **kw):
        self.eventos.append((kind, message, kw.get("data") or {}))


def test_evento_da_fila_leva_o_rotulo():
    b = _Barramento()
    emit_needs_person_change(b, profile_id="p", instance_id="android-07", status="auth_challenge",
                             anterior_status="unknown", detail="x [conta_travada: “Confirm you're human”]")
    emit_needs_person_change(b, profile_id="p", instance_id="android-07", status="auth_challenge",
                             anterior_status="unknown", detail="código por e-mail")
    assert [d["rotulo"] for _k, _m, d in b.eventos] == [ROTULO_BLOQUEADA, ROTULO_AGUARDANDO]
    assert "bloqueada" in b.eventos[0][1]

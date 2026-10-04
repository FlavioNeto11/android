"""30.65: exceção de uso único à regra de uma conta por alvo (ADR-055), criada por pessoa, com prazo.

O caso: o dono autorizou UMA DM de prova (31.26) entre duas contas nossas, e a porta de frota recusa sem caminho de
aprovação porque outra conta já falou com o alvo em 30 dias. A exceção tira só essa recusa, só para o perfil, o alvo e a
ação dela; a etapa casada passa por aprovação e a exceção se gasta quando o efeito sai.

Nível de prova: `simulated` (banco de teste, perfis de teste, rota pelo TestClient). Nada real.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import InteractionType
from app.planning.capabilities import capability_of
from app.social.excecoes import ExcecaoInvalida, ExcecoesDePolitica
from app.util import now, to_iso

from .test_capabilities import IG
from .test_protecao_de_frota import ALVO, _execucao_em_duas_contas, _fez, _frota, _porta

AUTORIZACAO = "dono, Telegram, 04/10 19:02:26Z, entrada 1189"


def _excecoes(repo: Any) -> tuple[ExcecoesDePolitica, list[tuple[str, dict[str, object]]]]:
    eventos: list[tuple[str, dict[str, object]]] = []
    return ExcecoesDePolitica(repo.db, lambda tipo, _msg, dados: eventos.append((tipo, dados))), eventos


def _criar(excecoes: ExcecoesDePolitica, pid: str, *, acao: str = "SEND_MESSAGE", alvo: str = ALVO,
           horas: float = 24) -> str:
    return excecoes.criar(profile_id=pid, alvo=alvo, capability=acao, motivo="prova do 31.26",
                          autorizacao=AUTORIZACAO, autor="orquestradora",
                          expira_em=to_iso(now() + timedelta(hours=horas))).id


def test_sem_excecao_recusa_e_com_ela_pede_aprovacao_nunca_autonomo(tmp_path: Path) -> None:
    svc, repo, policies, contas = _frota(tmp_path)
    _fez(svc, contas["lucas"], InteractionType.dm_sent)
    dm = capability_of(IG, "SEND_MESSAGE")
    antes = policies.check(contas["mariana"], dm, run_id="r-2", counterparty=ALVO, step_id="r-2:x")
    assert not antes.allowed and antes.retry_at is None and antes.excecao is None
    excecoes, eventos = _excecoes(repo)
    exc = _criar(excecoes, contas["mariana"])
    assert [t for t, _ in eventos] == ["politica.excecao_criada"]
    veredito = policies.check(contas["mariana"], dm, run_id="r-2", counterparty="Anarabottinipsicopedagoga",
                              step_id="r-2:x")
    assert veredito.allowed and veredito.needs_approval and veredito.policy == "approval_required"
    assert veredito.excecao == exc and "30.65" in veredito.reason and "30 dias" in veredito.reason


def test_excecao_de_outro_perfil_alvo_ou_acao_nao_vale(tmp_path: Path) -> None:
    """A exceção da DM não libera comentário nem seguir para o mesmo alvo, nem a DM de outra conta ou a outro alvo."""
    svc, repo, policies, contas = _frota(tmp_path)
    _fez(svc, contas["lucas"], InteractionType.dm_sent)
    _fez(svc, contas["lucas"], InteractionType.dm_sent, "@outra.pessoa")
    excecoes, _ = _excecoes(repo)
    # a de outro perfil: a exceção é do perfil de ORIGEM que ela nomeia
    _criar(excecoes, contas["lucas"])
    assert not policies.check(contas["mariana"], capability_of(IG, "SEND_MESSAGE"), counterparty=ALVO,
                              step_id="r-2:x").allowed
    _criar(excecoes, contas["mariana"])
    for acao in ("CREATE_COMMENT", "FOLLOW"):
        v = policies.check(contas["mariana"], capability_of(IG, acao), counterparty=ALVO, step_id="r-2:x")
        assert not v.allowed and v.excecao is None, acao
    outro_alvo = policies.check(contas["mariana"], capability_of(IG, "SEND_MESSAGE"), counterparty="@outra.pessoa",
                                step_id="r-2:x")
    assert not outro_alvo.allowed


def test_presa_a_uma_etapa_nao_vale_para_outra_e_gasta_volta_a_recusar(tmp_path: Path) -> None:
    svc, repo, policies, contas = _frota(tmp_path)
    _fez(svc, contas["lucas"], InteractionType.dm_sent)
    excecoes, eventos = _excecoes(repo)
    exc = _criar(excecoes, contas["mariana"])
    dm = capability_of(IG, "SEND_MESSAGE")
    excecoes.prender(exc, "r-2:etapa-a")
    assert policies.check(contas["mariana"], dm, counterparty=ALVO, step_id="r-2:etapa-a").excecao == exc
    assert not policies.check(contas["mariana"], dm, counterparty=ALVO, step_id="r-3:etapa-b").allowed
    excecoes.gastar("r-2:etapa-a", "int-1")
    assert excecoes.obter(exc).estado == "usada"                                   # type: ignore[union-attr]
    assert eventos[-1][0] == "politica.excecao_usada"
    assert not policies.check(contas["mariana"], dm, counterparty=ALVO, step_id="r-2:etapa-a").allowed


def test_vencida_recusa_e_vira_evento(tmp_path: Path) -> None:
    svc, repo, policies, contas = _frota(tmp_path)
    _fez(svc, contas["lucas"], InteractionType.dm_sent)
    excecoes, eventos = _excecoes(repo)
    exc = _criar(excecoes, contas["mariana"])
    repo.db.execute("UPDATE excecoes_de_politica SET expira_em=? WHERE id=?",
                    (to_iso(now() - timedelta(minutes=1)), exc))
    assert not policies.check(contas["mariana"], capability_of(IG, "SEND_MESSAGE"), counterparty=ALVO).allowed
    assert excecoes.vencer() == 1 and excecoes.vencer() == 0
    assert eventos[-1][0] == "politica.excecao_vencida" and excecoes.obter(exc).estado == "vencida"  # type: ignore[union-attr]


def test_a_criacao_recusa_prazo_longo_alvo_vazio_e_perfil_desconhecido(tmp_path: Path) -> None:
    _svc, repo, _policies, contas = _frota(tmp_path)
    excecoes, eventos = _excecoes(repo)
    for kw, motivo in (({"horas": 73}, "72 h"), ({"horas": -1}, "já passou"), ({"alvo": "  "}, "alvo")):
        try:
            _criar(excecoes, contas["mariana"], **kw)                             # type: ignore[arg-type]
        except ExcecaoInvalida as exc:
            assert motivo in str(exc)
        else:
            raise AssertionError(kw)
    try:
        _criar(excecoes, "perfil-que-nao-existe")
    except ExcecaoInvalida as exc:
        assert "não existe" in str(exc)
    assert eventos == []


async def test_pela_rota_e_pela_porta_do_despacho_ate_o_gasto(harness: Any) -> None:
    """Criada pela rota (autor da sessão), a exceção faz a porta do despacho abrir PEDIDO em Pendências com o porquê,
    prende-se à etapa e se gasta no `open_effect`. Sem ela, a mesma etapa é recusada."""
    state = harness.state
    pids = _execucao_em_duas_contas(state, "SEND_MESSAGE", {"username": ALVO, "content": "oi", "content_verbatim": "true"})
    state.social_repo.record_interaction(pids["android-02"], type=InteractionType.dm_sent.value, direction="outbound",
                                         status="confirmed", counterparty=ALVO, app_id="ig", run_id="r-antiga",
                                         occurred_at=to_iso(now() - timedelta(days=1)))
    recusa = await _porta(state, "android-01")
    assert recusa is not None and not recusa.allowed and recusa.retry_at is None

    app = create_app(harness.cfg, state=state)
    app.state.poc = state
    cliente = TestClient(app, client=("127.0.0.1", 123))
    corpo = {"profile_id": pids["android-01"], "alvo": ALVO, "capability": "SEND_MESSAGE",
             "motivo": "prova do 31.26", "autorizacao": AUTORIZACAO,
             "expira_em": to_iso(now() + timedelta(hours=80))}
    assert cliente.post("/api/politica/excecoes", json=corpo).status_code == 422
    corpo["expira_em"] = to_iso(now() + timedelta(hours=24))
    criada = cliente.post("/api/politica/excecoes", json=corpo)
    assert criada.status_code == 201, criada.text
    exc = criada.json()["excecao"]
    assert exc["estado"] == "ativa" and exc["autor"] and exc["autorizacao"] == AUTORIZACAO

    parada = await _porta(state, "android-01")
    assert parada is not None and parada.policy == "approval_required", parada
    [pedido] = state.approval_service.list()
    assert "30.65" in pedido["summary"] and "uma conta por alvo" in pedido["summary"]
    etapa = "run-f:android-01:v1:efeito"
    assert state.excecoes.obter(exc["id"]).step_id == etapa

    state.social.open_effect(pids["android-01"], capability="SEND_MESSAGE",
                             interaction_type=InteractionType.dm_sent.value, bindings={"username": ALVO, "content": "oi"},
                             run_id="run-f", step_id=etapa, app_id="ig", counterparty=ALVO)
    lista = cliente.get("/api/politica/excecoes", params={"profile_id": pids["android-01"]}).json()["excecoes"]
    assert [e["estado"] for e in lista] == ["usada"]
    tipos = [r["kind"] for r in state.db.query("SELECT kind FROM events WHERE kind LIKE 'politica.excecao_%' ORDER BY id")]
    assert tipos == ["politica.excecao_criada", "politica.excecao_usada"]

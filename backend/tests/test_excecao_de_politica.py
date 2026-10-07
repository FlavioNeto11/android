"""30.65: exceção de uso único à regra de uma conta por alvo (ADR-055), criada por pessoa, com prazo.

O caso: o dono autorizou UMA DM de prova (31.26) entre duas contas nossas, e a porta de frota recusa sem caminho de
aprovação porque outra conta já falou com o alvo em 30 dias. A exceção tira só essa recusa, só para o perfil, o alvo e a
ação dela; a etapa casada passa por aprovação e a exceção se gasta quando o efeito sai. O alvo é sempre conta nossa viva.

Nível de prova: `simulated` (banco de teste, perfis de teste, rota pelo TestClient). Nada real.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import InteractionType, ProfileCreate
from app.planning.capabilities import capability_of
from app.social.excecoes import ExcecaoInvalida, ExcecoesDePolitica
from app.util import now, to_iso

from .apoio_politica import IG, SENHA
from .apoio_politica import ALVO as PESSOA_REAL
from .apoio_politica import _execucao_em_duas_contas, _fez, _frota, _porta
from .test_alvo_por_legenda import por_posicao  # noqa: F401 - fixture do Instagram falso (ponta a ponta)

#: O alvo da exceção é uma conta NOSSA (a do 31.26 é a DM entre duas contas nossas). Perfil sem aparelho.

# Testes que só cobriam regra removida pelo refactor do dono de 07/10 (31.272).
SAIU_NO_ADR_083 = pytest.mark.skip(reason="ADR-083: sem a recusa por alvo, a exceção 30.65 não libera nada")
ALVO = "@nossa.alvo40517"
#: Hora sem segundos: "19:02:26Z" cai na triagem de nota (formato de par chave:valor) e a rota recusa com 409.
AUTORIZACAO = "dono pelo Telegram em 04/10 às 19:02 UTC, entrada 1189"


def _excecoes(repo: Any) -> tuple[ExcecoesDePolitica, list[tuple[str, dict[str, object]]]]:
    eventos: list[tuple[str, dict[str, object]]] = []
    return ExcecoesDePolitica(repo.db, lambda tipo, _msg, dados: eventos.append((tipo, dados)), perfis=repo), eventos


def _frota_com_alvo_nosso(tmp_path: Path) -> tuple[Any, Any, Any, dict[str, str]]:
    svc, repo, policies, contas = _frota(tmp_path)
    svc.create_profile(ProfileCreate(username=ALVO.lstrip("@"), password=SENHA))
    return svc, repo, policies, contas


def _criar(excecoes: ExcecoesDePolitica, pid: str, *, acao: str = "SEND_MESSAGE", alvo: str = ALVO,
           horas: float = 24, com_sessao: bool = False) -> str:
    return excecoes.criar(profile_id=pid, alvo=alvo, capability=acao, motivo="prova do 31.26",
                          autorizacao=AUTORIZACAO, autor="orquestradora", autor_com_sessao=com_sessao,
                          expira_em=to_iso(now() + timedelta(hours=horas))).id


@SAIU_NO_ADR_083
def test_sem_excecao_recusa_e_com_ela_pede_aprovacao_nunca_autonomo(tmp_path: Path) -> None:
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["tadeu"], InteractionType.dm_sent, ALVO)
    dm = capability_of(IG, "SEND_MESSAGE")
    antes = policies.check(contas["luciana"], dm, run_id="r-2", counterparty=ALVO, step_id="r-2:x")
    assert not antes.allowed and antes.retry_at is None and antes.excecao is None
    excecoes, eventos = _excecoes(repo)
    exc = _criar(excecoes, contas["luciana"])
    assert [t for t, _ in eventos] == ["politica.excecao_criada"]
    veredito = policies.check(contas["luciana"], dm, run_id="r-2", counterparty="Nossa.Alvo40517", step_id="r-2:x")
    assert veredito.allowed and veredito.needs_approval and veredito.policy == "approval_required"
    assert veredito.excecao == exc and "30.65" in veredito.reason and "30 dias" in veredito.reason


@SAIU_NO_ADR_083
def test_o_cartao_diz_quem_criou_e_so_atesta_o_dono_com_sessao(tmp_path: Path) -> None:
    """Revisão do #309, item 1: sem sessão (o loopback aceita), o cartão diz quem criou e quando e cita a autorização
    como texto; "autorizada pelo dono" só quando quem criou era operador com sessão."""
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["tadeu"], InteractionType.dm_sent, ALVO)
    excecoes, _ = _excecoes(repo)
    dm = capability_of(IG, "SEND_MESSAGE")
    sem_sessao = _criar(excecoes, contas["luciana"])
    motivo = policies.check(contas["luciana"], dm, counterparty=ALVO, step_id="r-2:x").reason
    assert "criada por orquestradora em " in motivo and "sem sessão de operador" in motivo
    assert f"autorização citada: {AUTORIZACAO}" in motivo and "autorizada pelo dono" not in motivo
    excecoes.revogar(sem_sessao, por="orquestradora")
    _criar(excecoes, contas["luciana"], com_sessao=True)
    motivo = policies.check(contas["luciana"], dm, counterparty=ALVO, step_id="r-2:x").reason
    assert "autorizada pelo dono (operador com sessão)" in motivo and "autorização citada:" in motivo


@SAIU_NO_ADR_083
def test_so_vale_entre_contas_nossas(tmp_path: Path) -> None:
    """Revisão do #309, item 3: o dono autorizou uma DM entre contas nossas; abrir para pessoa real é decisão dele."""
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    excecoes, eventos = _excecoes(repo)
    try:
        _criar(excecoes, contas["luciana"], alvo=PESSOA_REAL)
    except ExcecaoInvalida as exc:
        assert "conta nossa" in str(exc)
    else:
        raise AssertionError("aceitou pessoa real")
    # nem uma linha gravada à mão passa: a porta só usa exceção para conta nossa viva
    _fez(svc, contas["tadeu"], InteractionType.dm_sent, PESSOA_REAL)
    repo.db.execute("INSERT INTO excecoes_de_politica(id, regra, profile_id, alvo, capability, motivo, autorizacao,"
                    " autor, criada_em, expira_em) VALUES ('exc-mao','uma_conta_por_alvo',?,?,'SEND_MESSAGE','m','a',"
                    "'x',?,?)", (contas["luciana"], PESSOA_REAL, to_iso(now()), to_iso(now() + timedelta(hours=1))))
    v = policies.check(contas["luciana"], capability_of(IG, "SEND_MESSAGE"), counterparty=PESSOA_REAL, step_id="s")
    assert not v.allowed and v.excecao is None and eventos == []


@SAIU_NO_ADR_083
def test_excecao_de_outro_perfil_alvo_ou_acao_nao_vale(tmp_path: Path) -> None:
    """A exceção da DM não libera comentário nem seguir para o mesmo alvo, nem a DM de outra conta ou a outro alvo."""
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["tadeu"], InteractionType.dm_sent, ALVO)
    _fez(svc, contas["tadeu"], InteractionType.dm_sent, "@outra.pessoa")
    excecoes, _ = _excecoes(repo)
    # a de outro perfil: a exceção é do perfil de ORIGEM que ela nomeia
    _criar(excecoes, contas["tadeu"])
    assert not policies.check(contas["luciana"], capability_of(IG, "SEND_MESSAGE"), counterparty=ALVO,
                              step_id="r-2:x").allowed
    _criar(excecoes, contas["luciana"])
    for acao in ("CREATE_COMMENT", "FOLLOW"):
        v = policies.check(contas["luciana"], capability_of(IG, acao), counterparty=ALVO, step_id="r-2:x")
        assert not v.allowed and v.excecao is None, acao
    outro_alvo = policies.check(contas["luciana"], capability_of(IG, "SEND_MESSAGE"), counterparty="@outra.pessoa",
                                step_id="r-2:x")
    assert not outro_alvo.allowed


@SAIU_NO_ADR_083
def test_presa_a_uma_etapa_nao_vale_para_outra_e_gasta_volta_a_recusar(tmp_path: Path) -> None:
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["tadeu"], InteractionType.dm_sent, ALVO)
    excecoes, eventos = _excecoes(repo)
    exc = _criar(excecoes, contas["luciana"])
    dm = capability_of(IG, "SEND_MESSAGE")
    excecoes.prender(exc, "r-2:etapa-a")
    assert policies.check(contas["luciana"], dm, counterparty=ALVO, step_id="r-2:etapa-a").excecao == exc
    assert not policies.check(contas["luciana"], dm, counterparty=ALVO, step_id="r-3:etapa-b").allowed
    assert excecoes.reservar("r-2:etapa-a") is None                    # o executor, logo antes do gesto
    assert excecoes.obter(exc).estado == "em_uso"                                  # type: ignore[union-attr]
    excecoes.disparou("r-2:etapa-a", "int-1")                           # o `open_effect`
    excecoes.liquidar("int-1", houve_efeito=True)                       # o `settle_effect`
    assert excecoes.obter(exc).estado == "usada"                                   # type: ignore[union-attr]
    assert eventos[-1][0] == "politica.excecao_usada"
    assert not policies.check(contas["luciana"], dm, counterparty=ALVO, step_id="r-2:etapa-a").allowed


@SAIU_NO_ADR_083
def test_vencida_recusa_vira_evento_e_a_reserva_falha(tmp_path: Path) -> None:
    """Releitura do #309, O2: a exceção que vence depois da porta continua apontando a etapa, e a reserva no commit falha
    com o motivo próprio ("venceu antes do efeito"); o gesto não acontece."""
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["tadeu"], InteractionType.dm_sent, ALVO)
    excecoes, eventos = _excecoes(repo)
    exc = _criar(excecoes, contas["luciana"])
    excecoes.prender(exc, "r-2:etapa-a")
    repo.db.execute("UPDATE excecoes_de_politica SET expira_em=? WHERE id=?",
                    (to_iso(now() - timedelta(minutes=1)), exc))
    assert not policies.check(contas["luciana"], capability_of(IG, "SEND_MESSAGE"), counterparty=ALVO).allowed
    assert excecoes.vencer() == 1 and excecoes.vencer() == 0
    vencida = excecoes.obter(exc)
    assert eventos[-1][0] == "politica.excecao_vencida" and vencida is not None
    assert vencida.estado == "vencida" and vencida.step_id == "r-2:etapa-a"
    motivo = excecoes.reservar("r-2:etapa-a")
    assert motivo is not None and "venceu antes do efeito" in motivo and "não foi disparado" in motivo
    assert excecoes.obter(exc).em_uso_em is None                                   # type: ignore[union-attr]


@SAIU_NO_ADR_083
def test_duplicata_em_aberto_recusada_revogar_encerra_e_o_gasto_nao_pega_encerrada(tmp_path: Path) -> None:
    """Revisão do #309, itens 4 e 5: uma em aberto por trio; revogada não volta a valer nem é gasta."""
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["tadeu"], InteractionType.dm_sent, ALVO)
    excecoes, eventos = _excecoes(repo)
    exc = _criar(excecoes, contas["luciana"])
    try:
        _criar(excecoes, contas["luciana"])
    except ExcecaoInvalida as e:
        assert exc in str(e)
    else:
        raise AssertionError("aceitou duplicata em aberto")
    excecoes.prender(exc, "r-2:etapa-a")
    revogada = excecoes.revogar(exc, por="orquestradora")
    assert revogada.estado == "revogada" and revogada.encerrada_por == "orquestradora"
    assert eventos[-1][0] == "politica.excecao_revogada"
    motivo = excecoes.reservar("r-2:etapa-a")                          # revogar antes da reserva: a reserva falha
    assert motivo is not None and "foi revogada antes do efeito" in motivo and "orquestradora" not in motivo
    assert excecoes.obter(exc).usada_em is None                                    # type: ignore[union-attr]
    assert not policies.check(contas["luciana"], capability_of(IG, "SEND_MESSAGE"), counterparty=ALVO,
                              step_id="r-2:etapa-a").allowed
    try:
        excecoes.revogar(exc, por="orquestradora")
    except ExcecaoInvalida as e:
        assert "já terminou" in str(e)
    else:
        raise AssertionError("revogou duas vezes")
    _criar(excecoes, contas["luciana"])                         # encerrada não conta como em aberto


def test_a_criacao_recusa_prazo_longo_alvo_vazio_e_perfil_desconhecido(tmp_path: Path) -> None:
    _svc, repo, _policies, contas = _frota_com_alvo_nosso(tmp_path)
    excecoes, eventos = _excecoes(repo)
    for kw, motivo in (({"horas": 73}, "72 h"), ({"horas": -1}, "já passou"), ({"alvo": "  "}, "alvo")):
        try:
            _criar(excecoes, contas["luciana"], **kw)                             # type: ignore[arg-type]
        except ExcecaoInvalida as exc:
            assert motivo in str(exc)
        else:
            raise AssertionError(kw)
    try:
        _criar(excecoes, "perfil-que-nao-existe")
    except ExcecaoInvalida as exc:
        assert "não existe" in str(exc)
    assert eventos == []


# ------------------------------------------------------------------ porta do despacho, rota e cartão
def _cenario(harness: Any) -> tuple[dict[str, str], TestClient]:
    state = harness.state
    # ADR-081: com a configuração de fábrica (até 10 contas por alvo, conta nossa viva fora da contagem) a exceção não
    # teria o que tirar. Ela existe para a instalação que volta à regra do ADR-055 de 02/10: uma conta por alvo, conta
    # nossa dentro.
    state.settings.update({"frota_conta_nossa_fora_da_regra": False, "frota_max_contas_por_alvo": 1})
    pids = _execucao_em_duas_contas(state, "SEND_MESSAGE", {"username": ALVO, "content": "oi", "content_verbatim": "true"})
    state.social.create_profile(ProfileCreate(username=ALVO.lstrip("@"), password=SENHA))
    state.social_repo.record_interaction(pids["android-02"], type=InteractionType.dm_sent.value, direction="outbound",
                                         status="confirmed", counterparty=ALVO, app_id="ig", run_id="r-antiga",
                                         occurred_at=to_iso(now() - timedelta(days=1)))
    app = create_app(harness.cfg, state=state)
    app.state.poc = state
    return pids, TestClient(app, client=("127.0.0.1", 123))


def _corpo(pid: str, **over: object) -> dict[str, object]:
    corpo: dict[str, object] = {"profile_id": pid, "alvo": ALVO, "capability": "SEND_MESSAGE",
                                "motivo": "prova do 31.26", "autorizacao": AUTORIZACAO,
                                "expira_em": to_iso(now() + timedelta(hours=24))}
    corpo.update(over)
    return corpo


@SAIU_NO_ADR_083
async def test_pela_rota_e_pela_porta_do_despacho_ate_o_gasto(harness: Any) -> None:
    """Criada pela rota (loopback sem sessão: `autor_com_sessao` falso), a exceção faz a porta do despacho abrir PEDIDO
    em Pendências com o porquê, prende-se à etapa e se gasta no `open_effect`. Sem ela, a mesma etapa é recusada."""
    state = harness.state
    pids, cliente = _cenario(harness)
    recusa = await _porta(state, "android-01")
    assert recusa is not None and not recusa.allowed and recusa.retry_at is None

    assert cliente.post("/api/politica/excecoes",
                        json=_corpo(pids["android-01"], expira_em=to_iso(now() + timedelta(hours=80)))).status_code == 422
    assert cliente.post("/api/politica/excecoes", json=_corpo(pids["android-01"], alvo=PESSOA_REAL)).status_code == 422
    criada = cliente.post("/api/politica/excecoes", json=_corpo(pids["android-01"]))
    assert criada.status_code == 201, criada.text
    exc = criada.json()["excecao"]
    assert exc["estado"] == "ativa" and exc["autor"] and exc["autorizacao"] == AUTORIZACAO
    assert exc["autor_com_sessao"] is False

    parada = await _porta(state, "android-01")
    assert parada is not None and parada.policy == "approval_required", parada
    [pedido] = state.approval_service.list()
    assert "30.65" in pedido["summary"] and "uma conta por alvo" in pedido["summary"]
    assert "autorizada pelo dono" not in pedido["summary"] and "autorização citada" in pedido["summary"]
    etapa = "run-f:android-01:v1:efeito"
    assert state.excecoes.obter(exc["id"]).step_id == etapa

    assert state.excecoes.reservar(etapa) is None
    iid = state.social.open_effect(pids["android-01"], capability="SEND_MESSAGE",
                                   interaction_type=InteractionType.dm_sent.value,
                                   bindings={"username": ALVO, "content": "oi"}, run_id="run-f", step_id=etapa,
                                   app_id="ig", counterparty=ALVO)
    state.social.settle_effect(pids["android-01"], iid, outcome="succeeded")
    lista = cliente.get("/api/politica/excecoes", params={"profile_id": pids["android-01"]}).json()["excecoes"]
    assert [e["estado"] for e in lista] == ["usada"]
    tipos = [r["kind"] for r in state.db.query("SELECT kind FROM events WHERE kind LIKE 'politica.excecao_%' ORDER BY id")]
    assert tipos == ["politica.excecao_criada", "politica.excecao_usada"]


@SAIU_NO_ADR_083
async def test_recusar_o_cartao_encerra_a_excecao_e_a_rota_revoga(harness: Any) -> None:
    """Revisão do #309, item 4: a recusa do dono encerra a exceção presa (evento próprio); ela não volta a valer."""
    state = harness.state
    pids, cliente = _cenario(harness)
    exc = cliente.post("/api/politica/excecoes", json=_corpo(pids["android-01"])).json()["excecao"]
    parada = await _porta(state, "android-01")
    assert parada is not None and parada.policy == "approval_required"
    [pedido] = state.approval_service.list()
    # como o executor deixa o objetivo parado na aprovação (a tabela de estados não tem running → pending)
    state.db.execute("UPDATE objectives SET status='waiting_user' WHERE id='run-f:android-01'")
    state.approval_service.decide(pedido["id"], "reject")
    assert state.excecoes.obter(exc["id"]).estado == "recusada"
    assert not state.excecoes.ativa_para(pids["android-01"], ALVO, "SEND_MESSAGE", "outra-etapa")
    # a rota revoga só o que está em aberto: a recusada responde 409; inexistente, 404
    assert cliente.post(f"/api/politica/excecoes/{exc['id']}/revogar").status_code == 409
    assert cliente.post("/api/politica/excecoes/exc-nao-existe/revogar").status_code == 404
    nova = cliente.post("/api/politica/excecoes", json=_corpo(pids["android-01"])).json()["excecao"]
    revogada = cliente.post(f"/api/politica/excecoes/{nova['id']}/revogar")
    assert revogada.status_code == 200 and revogada.json()["excecao"]["estado"] == "revogada"
    tipos = [r["kind"] for r in state.db.query("SELECT kind FROM events WHERE kind LIKE 'politica.excecao_%' ORDER BY id")]
    assert tipos == ["politica.excecao_criada", "politica.excecao_recusada", "politica.excecao_criada",
                     "politica.excecao_revogada"]


@SAIU_NO_ADR_083
async def test_com_a_excecao_o_aprovado_de_outra_versao_nao_e_reaproveitado(harness: Any, monkeypatch: Any) -> None:
    """Revisão do #309, item 2: com a exceção casada, a porta não procura o aprovado de uma versão anterior com o mesmo
    texto e alvo; sem isso a DM sairia sem o dono ver o cartão da exceção."""
    state = harness.state
    pids, cliente = _cenario(harness)
    chamadas: list[str] = []
    monkeypatch.setattr(state.approvals, "acompanhar_revisao", lambda step_id, **_kw: chamadas.append(step_id))
    assert cliente.post("/api/politica/excecoes", json=_corpo(pids["android-01"])).status_code == 201
    parada = await _porta(state, "android-01")
    assert parada is not None and not parada.allowed and parada.policy == "approval_required"
    assert chamadas == [] and len(state.approval_service.list()) == 1


async def test_motivo_ou_autorizacao_com_cara_de_segredo_nao_grava_nada(harness: Any) -> None:
    state = harness.state
    pids, cliente = _cenario(harness)
    for campo in ("motivo", "autorizacao"):
        r = cliente.post("/api/politica/excecoes", json=_corpo(pids["android-01"], **{campo: "senha: Xq7!pLm2#Rt9"}))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "note_looks_secret", r.text
    assert state.db.scalar("SELECT COUNT(*) FROM excecoes_de_politica") == 0


async def test_falha_ao_gastar_nao_derruba_o_open_effect(harness: Any, monkeypatch: Any) -> None:
    """O efeito saiu: a interação tem de ficar registrada (é ela que alimenta a janela do ADR-055) mesmo se marcar a
    exceção como usada falhar."""
    state = harness.state
    pids, _cliente = _cenario(harness)

    def _quebra(*_a: object, **_k: object) -> None:
        raise RuntimeError("banco indisponível")

    monkeypatch.setattr(state.social.excecoes, "disparou", _quebra)
    iid = state.social.open_effect(pids["android-01"], capability="SEND_MESSAGE",
                                   interaction_type=InteractionType.dm_sent.value,
                                   bindings={"username": ALVO, "content": "oi"}, run_id="run-f",
                                   step_id="run-f:android-01:v1:efeito", app_id="ig", counterparty=ALVO)
    assert iid and state.db.scalar("SELECT COUNT(*) FROM social_interactions WHERE id=?", (iid,)) == 1


# ------------------------------------------------------------------ releitura do #309: revogar
@SAIU_NO_ADR_083
async def test_revogada_depois_da_porta_a_reserva_falha_e_a_rota_nao_revoga_em_uso(harness: Any) -> None:
    """Releitura do #309: a etapa já passou da porta (aprovada, antes do commit). Revogar antes da reserva faz a reserva
    falhar (nada sai); revogar depois da reserva devolve 409 e nunca grava "revogada" por cima de "em uso"."""
    from types import SimpleNamespace

    from app.modules.learning.domain.falhas import FailureKind, classificar_texto

    state = harness.state
    pids, cliente = _cenario(harness)
    etapa = SimpleNamespace(id="run-f:android-01:v1:efeito")
    executor = state.scheduler.executor

    exc = cliente.post("/api/politica/excecoes", json=_corpo(pids["android-01"])).json()["excecao"]
    await _porta(state, "android-01")
    [pedido] = state.approval_service.list()
    state.db.execute("UPDATE objectives SET status='waiting_user' WHERE id='run-f:android-01'")
    state.approval_service.decide(pedido["id"], "approve")
    assert cliente.post(f"/api/politica/excecoes/{exc['id']}/revogar").status_code == 200
    motivo = executor._reservar_excecao(etapa)                                     # noqa: SLF001
    assert motivo is not None and exc["id"] in motivo and "revogada" in motivo and "não foi disparado" in motivo
    assert classificar_texto(motivo) == FailureKind.INTERROMPIDA                  # decisão de pessoa: nunca vira lição

    # a reserva ganha primeiro: revogar perde com 409 e o estado segue coerente até a liquidação
    state.db.execute("DELETE FROM excecoes_de_politica")
    nova = cliente.post("/api/politica/excecoes", json=_corpo(pids["android-01"])).json()["excecao"]
    state.excecoes.prender(nova["id"], etapa.id)
    assert executor._reservar_excecao(etapa) is None                               # noqa: SLF001
    r = cliente.post(f"/api/politica/excecoes/{nova['id']}/revogar")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "excecao_em_uso", r.text
    assert "o efeito pode ter saído" in r.json()["detail"]["message"]
    assert state.excecoes.obter(nova["id"]).estado == "em_uso"
    assert executor._reservar_excecao(etapa) is not None                           # noqa: SLF001 - uso único
    iid = state.social.open_effect(pids["android-01"], capability="SEND_MESSAGE",
                                   interaction_type=InteractionType.dm_sent.value,
                                   bindings={"username": ALVO, "content": "oi"}, run_id="run-f", step_id=etapa.id,
                                   app_id="ig", counterparty=ALVO)
    state.social.settle_effect(pids["android-01"], iid, outcome="succeeded")
    assert state.excecoes.obter(nova["id"]).estado == "usada"


@SAIU_NO_ADR_083
def test_duas_reservas_so_uma_ganha_e_sem_efeito_fecha_fechado(tmp_path: Path) -> None:
    """Releitura do #309: a reserva é um UPDATE condicional; a segunda perde. O gesto sem efeito encerra a exceção
    `sem_efeito`, que não volta a aberta (uso único fecha fechado)."""
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["tadeu"], InteractionType.dm_sent, ALVO)
    excecoes, eventos = _excecoes(repo)
    outra, _ = _excecoes(repo)                                         # outro processo, o mesmo banco
    exc = _criar(excecoes, contas["luciana"])
    excecoes.prender(exc, "r-2:etapa-a")
    assert excecoes.reservar("r-2:etapa-a") is None
    perdeu = outra.reservar("r-2:etapa-a")
    assert perdeu is not None and "já está em uso" in perdeu
    excecoes.vencer()
    assert excecoes.obter(exc).estado == "em_uso"                     # type: ignore[union-attr] - fora do vencer
    excecoes.disparou("r-2:etapa-a", "int-1")
    excecoes.liquidar("int-1", houve_efeito=False)
    assert excecoes.obter(exc).estado == "sem_efeito" and eventos[-1][0] == "politica.excecao_sem_efeito"  # type: ignore[union-attr]
    assert not policies.check(contas["luciana"], capability_of(IG, "SEND_MESSAGE"), counterparty=ALVO,
                              step_id="r-2:etapa-b").allowed


async def test_reserva_que_levanta_nao_deixa_o_efeito_sair(harness: Any, monkeypatch: Any) -> None:
    from types import SimpleNamespace

    state = harness.state

    def _quebra(*_a: object, **_k: object) -> None:
        raise RuntimeError("banco indisponível")

    monkeypatch.setattr(state.social.excecoes, "reservar", _quebra)
    motivo = state.scheduler.executor._reservar_excecao(SimpleNamespace(id="x"))   # noqa: SLF001
    assert motivo is not None and "não foi disparado" in motivo


@SAIU_NO_ADR_083
async def test_revogar_com_o_cartao_pendente_expira_o_cartao(harness: Any) -> None:
    """Releitura do #309, item 2: o cartão não fica órfão em Pendências (nem no Telegram, que recusa o vencido)."""
    state = harness.state
    pids, cliente = _cenario(harness)
    exc = cliente.post("/api/politica/excecoes", json=_corpo(pids["android-01"])).json()["excecao"]
    await _porta(state, "android-01")
    [pedido] = state.approval_service.list()
    state.db.execute("UPDATE objectives SET status='waiting_user' WHERE id='run-f:android-01'")
    assert cliente.post(f"/api/politica/excecoes/{exc['id']}/revogar").status_code == 200
    assert state.approval_service.list() == []
    assert state.approvals.get(pedido["id"]).status == "expired"
    # o objetivo volta à porta, que agora recusa sem a exceção
    recusa = await _porta(state, "android-01")
    assert recusa is not None and not recusa.allowed and recusa.retry_at is None


@SAIU_NO_ADR_083
async def test_decisao_da_propria_etapa_anterior_a_excecao_nao_vale(harness: Any) -> None:
    """Releitura do #309, R1: o `for_step` da mesma etapa só vale se o pedido nasceu depois de a exceção ser presa."""
    state = harness.state
    pids, cliente = _cenario(harness)
    etapa = "run-f:android-01:v1:efeito"
    antigo = state.approvals.open(profile_id=pids["android-01"], capability="SEND_MESSAGE", summary="antes",
                                  target=ALVO, content="oi", run_id="run-f", objective_id="run-f:android-01",
                                  step_id=etapa)
    state.db.execute("UPDATE pending_approvals SET status='approved', created_at=? WHERE id=?",
                     (to_iso(now() - timedelta(hours=1)), antigo.id))
    assert cliente.post("/api/politica/excecoes", json=_corpo(pids["android-01"])).status_code == 201
    parada = await _porta(state, "android-01")
    assert parada is not None and not parada.allowed and parada.policy == "approval_required"
    [novo] = state.approval_service.list()
    assert novo["id"] != antigo.id and "30.65" in novo["summary"]


@SAIU_NO_ADR_083
def test_o_cartao_cita_no_maximo_80_caracteres_da_autorizacao(tmp_path: Path) -> None:
    """Releitura do #309, R2: o aviso do Telegram corta em 500; "Alvo" e "Texto" não podem sair do corte."""
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["tadeu"], InteractionType.dm_sent, ALVO)
    excecoes, _ = _excecoes(repo)
    longa = "dono pelo Telegram em 04/10 às 19:02 UTC, entrada 1189, " + "com contexto " * 20
    excecoes.criar(profile_id=contas["luciana"], alvo=ALVO, capability="SEND_MESSAGE", motivo="prova",
                   autorizacao=longa.strip(), autor="orquestradora", expira_em=to_iso(now() + timedelta(hours=1)))
    motivo = policies.check(contas["luciana"], capability_of(IG, "SEND_MESSAGE"), counterparty=ALVO, step_id="s").reason
    citada = motivo.split("autorização citada: ", 1)[1].split(";", 1)[0]
    assert len(citada) <= 80 and citada.endswith("…") and longa.strip() not in motivo


# ------------------------------------------------------------------ ponta a ponta: aparelho falso até o commit
async def _curtir_com_excecao_que_termina(h: Any, monkeypatch: Any, como: str) -> tuple[Any, list[int]]:
    """Corre "curtir a primeira publicação" no Instagram falso. Logo antes do gesto (na reserva do executor), uma pessoa
    revoga a exceção presa à etapa, ou ela vence: o que a rota faria entre a porta e o commit. A reserva real roda e o
    executor decide; o aparelho falso conta os corações tocados."""
    from .test_alvo_por_legenda import IID, _aparelho

    state = h.state
    excecoes = state.social.excecoes
    real = excecoes.reservar

    def reservar_depois_da_pessoa(step_id: str | None) -> str | None:
        if step_id and step_id.endswith(":curtir") and not state.db.scalar(
                "SELECT 1 FROM excecoes_de_politica WHERE step_id=?", (step_id,)):
            agora = to_iso(now())
            campos = ("encerrada_em=?, encerrada_por='orquestradora', encerramento='revogada'" if como == "revogada"
                      else "vencida_em=?")
            state.db.execute("INSERT INTO excecoes_de_politica(id, regra, profile_id, alvo, capability, motivo,"
                             " autorizacao, autor, criada_em, expira_em, step_id, presa_em) VALUES ('exc-e2e',"
                             "'uma_conta_por_alvo','p','@anarabottinipsicopedagoga','LIKE_POST','m','a','x',?,?,?,?)",
                             (agora, to_iso(now() + timedelta(hours=1)), step_id, agora))
            state.db.execute(f"UPDATE excecoes_de_politica SET {campos} WHERE id='exc-e2e'", (agora,))
        return real(step_id)

    monkeypatch.setattr(excecoes, "reservar", reservar_depois_da_pessoa)
    run = h.run([IID], command="curta a primeira publicação de @anarabottinipsicopedagoga")

    def curtir() -> Any:
        return state.db.one("SELECT * FROM steps WHERE run_id=? AND key='curtir' ORDER BY plan_version DESC LIMIT 1",
                            (run.id,))

    await h.wait(lambda: (c := curtir()) is not None and c["status"] in ("failed", "succeeded", "uncertain"),
                 timeout=90, what="a etapa de curtir terminar")
    return curtir(), _aparelho(h).coracoes_tocados


async def test_ponta_a_ponta_revogada_no_commit_o_gesto_nao_acontece(por_posicao: Any, monkeypatch: Any) -> None:
    etapa, tocados = await _curtir_com_excecao_que_termina(por_posicao, monkeypatch, "revogada")
    assert tocados == [] and etapa["status"] == "failed", (tocados, etapa["status"], etapa["status_detail"])
    erro = por_posicao.state.db.scalar("SELECT error FROM attempts WHERE step_id=? ORDER BY number DESC LIMIT 1",
                                       (etapa["id"],))
    assert "foi revogada antes do efeito" in (erro or "") and "não foi disparado" in (erro or "")


async def test_ponta_a_ponta_vencida_no_commit_o_gesto_nao_acontece(por_posicao: Any, monkeypatch: Any) -> None:
    etapa, tocados = await _curtir_com_excecao_que_termina(por_posicao, monkeypatch, "vencida")
    assert tocados == [] and etapa["status"] == "failed", (tocados, etapa["status"], etapa["status_detail"])
    erro = por_posicao.state.db.scalar("SELECT error FROM attempts WHERE step_id=? ORDER BY number DESC LIMIT 1",
                                       (etapa["id"],))
    assert "venceu antes do efeito" in (erro or "")


# ------------------------------------------------------------------ releitura 6c do #309
@SAIU_NO_ADR_083
async def test_reservada_sem_interacao_nao_vale_para_a_etapa_revisada_e_fecha_incerta(harness: Any) -> None:
    """Releitura 6c, itens 1 e 2: a etapa S reserva a exceção e o `open_effect` não grava interação (queda, ação sem
    `interaction_type`); S termina `failed`. A revisada S′ não casa a exceção (ela está `em_uso`): a porta recusa, e a
    reservada órfã fecha `incerta` (o efeito pode ter saído), nunca reaberta."""
    state = harness.state
    pids, cliente = _cenario(harness)
    exc = cliente.post("/api/politica/excecoes", json=_corpo(pids["android-01"])).json()["excecao"]
    await _porta(state, "android-01")
    etapa = "run-f:android-01:v1:efeito"
    assert state.excecoes.reservar(etapa) is None and state.excecoes.obter(exc["id"]).estado == "em_uso"
    state.db.execute("UPDATE steps SET status='failed' WHERE id=?", (etapa,))
    assert state.excecoes.ativa_para(pids["android-01"], ALVO, "SEND_MESSAGE", "run-f:android-01:v2:efeito") is None
    veredito = state.policies.check(pids["android-01"], capability_of(IG, "SEND_MESSAGE"), counterparty=ALVO,
                                    step_id="run-f:android-01:v2:efeito")
    assert not veredito.allowed and veredito.excecao is None
    state.excecoes.vencer()
    assert state.excecoes.obter(exc["id"]).estado == "incerta"
    assert cliente.post(f"/api/politica/excecoes/{exc['id']}/revogar").status_code == 409


@SAIU_NO_ADR_083
async def test_prender_que_perde_a_corrida_faz_a_porta_recusar(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    pids, cliente = _cenario(harness)
    assert cliente.post("/api/politica/excecoes", json=_corpo(pids["android-01"])).status_code == 201
    monkeypatch.setattr(state.excecoes, "prender", lambda *_a, **_k: False)
    recusa = await _porta(state, "android-01")
    assert recusa is not None and not recusa.allowed and recusa.retry_at is None and "deixou de estar em aberto" in recusa.reason
    assert state.approval_service.list() == []


def test_porta_sem_excecao_solta_a_vencida_e_o_commit_segue(tmp_path: Path) -> None:
    """Releitura 6c, item 1 (fim): a etapa que passa pela porta SEM exceção não falha no commit por uma vencida antiga
    presa a ela; a porta a solta."""
    _svc, repo, _policies, contas = _frota_com_alvo_nosso(tmp_path)
    excecoes, _ = _excecoes(repo)
    exc = _criar(excecoes, contas["luciana"])
    excecoes.prender(exc, "r-2:etapa-a")
    repo.db.execute("UPDATE excecoes_de_politica SET expira_em=? WHERE id=?", (to_iso(now() - timedelta(minutes=1)), exc))
    excecoes.vencer()
    assert excecoes.reservar("r-2:etapa-a") is not None                 # presa e vencida: o commit não sai
    excecoes.soltar_da_etapa("r-2:etapa-a")                              # a porta passou a etapa sem exceção
    assert excecoes.reservar("r-2:etapa-a") is None


def test_eventos_levam_so_ids_e_estado(tmp_path: Path) -> None:
    """Releitura 6c, item 4: alvo, motivo e autorização ficam no GET, nunca no barramento."""
    _svc, repo, _policies, contas = _frota_com_alvo_nosso(tmp_path)
    excecoes, eventos = _excecoes(repo)
    exc = _criar(excecoes, contas["luciana"])
    excecoes.prender(exc, "r-2:etapa-a")
    excecoes.reservar("r-2:etapa-a")
    excecoes.disparou("r-2:etapa-a", "int-1")
    excecoes.liquidar("int-1", houve_efeito=False)
    assert [t for t, _ in eventos] == ["politica.excecao_criada", "politica.excecao_sem_efeito"]
    for _tipo, dados in eventos:
        assert set(dados) <= {"excecao_id", "estado", "encerrada_por", "step_id", "run_id", "interaction_id"}
        assert ALVO not in str(dados) and AUTORIZACAO not in str(dados) and "prova do 31.26" not in str(dados)


@SAIU_NO_ADR_083
async def test_a_trilha_guarda_para_sempre_a_etapa_e_a_execucao_do_uso(harness: Any) -> None:
    """A exceção a uma regra de ADR diz para sempre qual etapa e execução a usaram, mesmo se a mesma etapa voltar à porta
    sem exceção (que solta a ligação viva `step_id`)."""
    state = harness.state
    pids, cliente = _cenario(harness)
    exc = cliente.post("/api/politica/excecoes", json=_corpo(pids["android-01"])).json()["excecao"]
    await _porta(state, "android-01")
    etapa = "run-f:android-01:v1:efeito"
    assert state.excecoes.reservar(etapa) is None
    iid = state.social.open_effect(pids["android-01"], capability="SEND_MESSAGE",
                                   interaction_type=InteractionType.dm_sent.value,
                                   bindings={"username": ALVO, "content": "oi"}, run_id="run-f", step_id=etapa,
                                   app_id="ig", counterparty=ALVO)
    state.social.settle_effect(pids["android-01"], iid, outcome="succeeded")
    state.excecoes.soltar_da_etapa(etapa)                 # a mesma etapa voltou à porta sem exceção
    [lida] = cliente.get("/api/politica/excecoes", params={"profile_id": pids["android-01"]}).json()["excecoes"]
    assert lida["id"] == exc["id"] and lida["estado"] == "usada"
    assert (lida["etapa_do_uso"], lida["run_do_uso"]) == (etapa, "run-f")
    usada = state.db.one("SELECT data FROM events WHERE kind='politica.excecao_usada'")
    assert usada is not None and etapa in usada["data"] and "run-f" in usada["data"]

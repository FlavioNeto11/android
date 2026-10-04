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

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import InteractionType, ProfileCreate
from app.planning.capabilities import capability_of
from app.social.excecoes import ExcecaoInvalida, ExcecoesDePolitica
from app.util import now, to_iso

from .test_capabilities import IG, SENHA
from .test_protecao_de_frota import ALVO as PESSOA_REAL
from .test_protecao_de_frota import _execucao_em_duas_contas, _fez, _frota, _porta

#: O alvo da exceção é uma conta NOSSA (a do 31.26 é a DM entre duas contas nossas). Perfil sem aparelho.
ALVO = "@nossa.alvo91182"
#: Hora sem segundos: "19:02:26Z" cai na triagem de nota (formato de par chave:valor) e a rota recusa com 409.
AUTORIZACAO = "dono pelo Telegram em 04/10 às 19:02 UTC, entrada 1189"


def _excecoes(repo: Any) -> tuple[ExcecoesDePolitica, list[tuple[str, dict[str, object]]]]:
    eventos: list[tuple[str, dict[str, object]]] = []
    return ExcecoesDePolitica(repo.db, lambda tipo, _msg, dados: eventos.append((tipo, dados))), eventos


def _frota_com_alvo_nosso(tmp_path: Path) -> tuple[Any, Any, Any, dict[str, str]]:
    svc, repo, policies, contas = _frota(tmp_path)
    svc.create_profile(ProfileCreate(username=ALVO.lstrip("@"), password=SENHA))
    return svc, repo, policies, contas


def _criar(excecoes: ExcecoesDePolitica, pid: str, *, acao: str = "SEND_MESSAGE", alvo: str = ALVO,
           horas: float = 24, com_sessao: bool = False) -> str:
    return excecoes.criar(profile_id=pid, alvo=alvo, capability=acao, motivo="prova do 31.26",
                          autorizacao=AUTORIZACAO, autor="orquestradora", autor_com_sessao=com_sessao,
                          expira_em=to_iso(now() + timedelta(hours=horas))).id


def test_sem_excecao_recusa_e_com_ela_pede_aprovacao_nunca_autonomo(tmp_path: Path) -> None:
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["lucas"], InteractionType.dm_sent, ALVO)
    dm = capability_of(IG, "SEND_MESSAGE")
    antes = policies.check(contas["mariana"], dm, run_id="r-2", counterparty=ALVO, step_id="r-2:x")
    assert not antes.allowed and antes.retry_at is None and antes.excecao is None
    excecoes, eventos = _excecoes(repo)
    exc = _criar(excecoes, contas["mariana"])
    assert [t for t, _ in eventos] == ["politica.excecao_criada"]
    veredito = policies.check(contas["mariana"], dm, run_id="r-2", counterparty="Nossa.Alvo91182", step_id="r-2:x")
    assert veredito.allowed and veredito.needs_approval and veredito.policy == "approval_required"
    assert veredito.excecao == exc and "30.65" in veredito.reason and "30 dias" in veredito.reason


def test_o_cartao_diz_quem_criou_e_so_atesta_o_dono_com_sessao(tmp_path: Path) -> None:
    """Revisão do #309, item 1: sem sessão (o loopback aceita), o cartão diz quem criou e quando e cita a autorização
    como texto; "autorizada pelo dono" só quando quem criou era operador com sessão."""
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["lucas"], InteractionType.dm_sent, ALVO)
    excecoes, _ = _excecoes(repo)
    dm = capability_of(IG, "SEND_MESSAGE")
    sem_sessao = _criar(excecoes, contas["mariana"])
    motivo = policies.check(contas["mariana"], dm, counterparty=ALVO, step_id="r-2:x").reason
    assert "criada por orquestradora em " in motivo and "sem sessão de operador" in motivo
    assert f"autorização citada: {AUTORIZACAO}" in motivo and "autorizada pelo dono" not in motivo
    excecoes.revogar(sem_sessao, por="orquestradora")
    _criar(excecoes, contas["mariana"], com_sessao=True)
    motivo = policies.check(contas["mariana"], dm, counterparty=ALVO, step_id="r-2:x").reason
    assert "autorizada pelo dono (operador com sessão)" in motivo and "autorização citada:" in motivo


def test_so_vale_entre_contas_nossas(tmp_path: Path) -> None:
    """Revisão do #309, item 3: o dono autorizou uma DM entre contas nossas; abrir para pessoa real é decisão dele."""
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    excecoes, eventos = _excecoes(repo)
    try:
        _criar(excecoes, contas["mariana"], alvo=PESSOA_REAL)
    except ExcecaoInvalida as exc:
        assert "conta nossa" in str(exc)
    else:
        raise AssertionError("aceitou pessoa real")
    # nem uma linha gravada à mão passa: a porta só usa exceção para conta nossa viva
    _fez(svc, contas["lucas"], InteractionType.dm_sent, PESSOA_REAL)
    repo.db.execute("INSERT INTO excecoes_de_politica(id, regra, profile_id, alvo, capability, motivo, autorizacao,"
                    " autor, criada_em, expira_em) VALUES ('exc-mao','uma_conta_por_alvo',?,?,'SEND_MESSAGE','m','a',"
                    "'x',?,?)", (contas["mariana"], PESSOA_REAL, to_iso(now()), to_iso(now() + timedelta(hours=1))))
    v = policies.check(contas["mariana"], capability_of(IG, "SEND_MESSAGE"), counterparty=PESSOA_REAL, step_id="s")
    assert not v.allowed and v.excecao is None and eventos == []


def test_excecao_de_outro_perfil_alvo_ou_acao_nao_vale(tmp_path: Path) -> None:
    """A exceção da DM não libera comentário nem seguir para o mesmo alvo, nem a DM de outra conta ou a outro alvo."""
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["lucas"], InteractionType.dm_sent, ALVO)
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
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["lucas"], InteractionType.dm_sent, ALVO)
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


def test_vencida_recusa_vira_evento_e_solta_a_etapa(tmp_path: Path) -> None:
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["lucas"], InteractionType.dm_sent, ALVO)
    excecoes, eventos = _excecoes(repo)
    exc = _criar(excecoes, contas["mariana"])
    excecoes.prender(exc, "r-2:etapa-a")
    repo.db.execute("UPDATE excecoes_de_politica SET expira_em=? WHERE id=?",
                    (to_iso(now() - timedelta(minutes=1)), exc))
    assert not policies.check(contas["mariana"], capability_of(IG, "SEND_MESSAGE"), counterparty=ALVO).allowed
    assert excecoes.vencer() == 1 and excecoes.vencer() == 0
    vencida = excecoes.obter(exc)
    assert eventos[-1][0] == "politica.excecao_vencida" and vencida is not None
    assert vencida.estado == "vencida" and vencida.step_id is None
    excecoes.gastar("r-2:etapa-a", "int-1")                     # o gasto não pega a vencida
    assert excecoes.obter(exc).usada_em is None                                    # type: ignore[union-attr]


def test_duplicata_em_aberto_recusada_revogar_encerra_e_o_gasto_nao_pega_encerrada(tmp_path: Path) -> None:
    """Revisão do #309, itens 4 e 5: uma em aberto por trio; revogada não volta a valer nem é gasta."""
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["lucas"], InteractionType.dm_sent, ALVO)
    excecoes, eventos = _excecoes(repo)
    exc = _criar(excecoes, contas["mariana"])
    try:
        _criar(excecoes, contas["mariana"])
    except ExcecaoInvalida as e:
        assert exc in str(e)
    else:
        raise AssertionError("aceitou duplicata em aberto")
    excecoes.prender(exc, "r-2:etapa-a")
    revogada = excecoes.revogar(exc, por="orquestradora")
    assert revogada.estado == "revogada" and revogada.encerrada_por == "orquestradora"
    assert eventos[-1][0] == "politica.excecao_revogada"
    excecoes.gastar("r-2:etapa-a", "int-1")
    assert excecoes.obter(exc).usada_em is None                                    # type: ignore[union-attr]
    assert not policies.check(contas["mariana"], capability_of(IG, "SEND_MESSAGE"), counterparty=ALVO,
                              step_id="r-2:etapa-a").allowed
    try:
        excecoes.revogar(exc, por="orquestradora")
    except ExcecaoInvalida as e:
        assert "já terminou" in str(e)
    else:
        raise AssertionError("revogou duas vezes")
    _criar(excecoes, contas["mariana"])                         # encerrada não conta como em aberto


def test_a_criacao_recusa_prazo_longo_alvo_vazio_e_perfil_desconhecido(tmp_path: Path) -> None:
    _svc, repo, _policies, contas = _frota_com_alvo_nosso(tmp_path)
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


# ------------------------------------------------------------------ porta do despacho, rota e cartão
def _cenario(harness: Any) -> tuple[dict[str, str], TestClient]:
    state = harness.state
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

    state.social.open_effect(pids["android-01"], capability="SEND_MESSAGE",
                             interaction_type=InteractionType.dm_sent.value, bindings={"username": ALVO, "content": "oi"},
                             run_id="run-f", step_id=etapa, app_id="ig", counterparty=ALVO)
    lista = cliente.get("/api/politica/excecoes", params={"profile_id": pids["android-01"]}).json()["excecoes"]
    assert [e["estado"] for e in lista] == ["usada"]
    tipos = [r["kind"] for r in state.db.query("SELECT kind FROM events WHERE kind LIKE 'politica.excecao_%' ORDER BY id")]
    assert tipos == ["politica.excecao_criada", "politica.excecao_usada"]


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

    monkeypatch.setattr(state.social.excecoes, "gastar", _quebra)
    iid = state.social.open_effect(pids["android-01"], capability="SEND_MESSAGE",
                                   interaction_type=InteractionType.dm_sent.value,
                                   bindings={"username": ALVO, "content": "oi"}, run_id="run-f",
                                   step_id="run-f:android-01:v1:efeito", app_id="ig", counterparty=ALVO)
    assert iid and state.db.scalar("SELECT COUNT(*) FROM social_interactions WHERE id=?", (iid,)) == 1


# ------------------------------------------------------------------ releitura do #309: revogar
async def test_revogada_depois_da_porta_o_commit_nao_dispara(harness: Any) -> None:
    """Releitura do #309, item 1: a etapa já passou da porta (aprovada, antes do commit) e a exceção é revogada. A porta
    não roda de novo no meio da etapa; o executor confere no commit e a etapa falha fechada, com o motivo literal."""
    from types import SimpleNamespace

    from app.modules.learning.domain.falhas import FailureKind, classificar_texto

    state = harness.state
    pids, cliente = _cenario(harness)
    exc = cliente.post("/api/politica/excecoes", json=_corpo(pids["android-01"])).json()["excecao"]
    await _porta(state, "android-01")
    etapa = SimpleNamespace(id="run-f:android-01:v1:efeito")
    executor = state.scheduler.executor
    assert executor._excecao_encerrada(etapa) is None                              # noqa: SLF001
    [pedido] = state.approval_service.list()
    state.db.execute("UPDATE objectives SET status='waiting_user' WHERE id='run-f:android-01'")
    state.approval_service.decide(pedido["id"], "approve")
    assert cliente.post(f"/api/politica/excecoes/{exc['id']}/revogar").status_code == 200
    motivo = executor._excecao_encerrada(etapa)                                    # noqa: SLF001
    assert motivo is not None and exc["id"] in motivo and "revogada" in motivo and "não foi disparado" in motivo
    assert classificar_texto(motivo) == FailureKind.INTERROMPIDA                  # decisão de pessoa: nunca vira lição


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


def test_o_cartao_cita_no_maximo_80_caracteres_da_autorizacao(tmp_path: Path) -> None:
    """Releitura do #309, R2: o aviso do Telegram corta em 500; "Alvo" e "Texto" não podem sair do corte."""
    svc, repo, policies, contas = _frota_com_alvo_nosso(tmp_path)
    _fez(svc, contas["lucas"], InteractionType.dm_sent, ALVO)
    excecoes, _ = _excecoes(repo)
    longa = "dono pelo Telegram em 04/10 às 19:02 UTC, entrada 1189, " + "com contexto " * 20
    excecoes.criar(profile_id=contas["mariana"], alvo=ALVO, capability="SEND_MESSAGE", motivo="prova",
                   autorizacao=longa.strip(), autor="orquestradora", expira_em=to_iso(now() + timedelta(hours=1)))
    motivo = policies.check(contas["mariana"], capability_of(IG, "SEND_MESSAGE"), counterparty=ALVO, step_id="s").reason
    citada = motivo.split("autorização citada: ", 1)[1].split(";", 1)[0]
    assert len(citada) <= 80 and citada.endswith("…") and longa.strip() not in motivo

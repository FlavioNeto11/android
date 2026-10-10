"""31.273 (ADR-084): a etapa que a IA DESCOBRIU numa exploração e a receita comprovou é achada de novo.

O pedido fora do catálogo do app explora em vez de recusar. A etapa exploratória comprovada e sem efeito já nasce como
receita candidata (executor); sem este leitor, a segunda vez explorava de novo, porque a chave de uma etapa livre muda
de texto a cada plano. Aqui a receita ATIVA de uma etapa marcada `steps.exploratoria` (migração 130) é oferecida ao
planejador pelo nome, num bloco à parte (ninguém a demonstrou), e a etapa do plano com esse nome vira o molde.

O que estes testes protegem:
* só a receita ativa, de etapa exploratória, sem efeito e sem valor de pessoa no molde vira oferta;
* a ensinada por pessoa vence a descoberta de mesmo nome, e os dois blocos do prompt ficam separados;
* a receita sem uso há mais que o piso (`ai.descobertas_sem_uso_dias`) sai da oferta, e 0 desliga o piso;
* a etapa do plano livre com o nome da descoberta ganha o molde, e a trilha diz que foi DESCOBERTA.

Nível de prova: `simulated` (harness com provedor falso; nenhuma IA, nenhum aparelho real).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.presentation.livro import _entrada
from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.planning import etapas_ensinadas as ens
from app.planning.prompts import planner_user
from app.planning.provider import PlanRequest
from app.util import now, now_iso, to_iso

from .conftest import Harness

POST = Postcondition(kind="model_judged", value="feito", description="d")


def _passo(key: str, **kw: Any) -> PlanStep:
    return PlanStep(**{"key": key, "title": f"Etapa {key}", "goal": f"fazer {key}", "postcondition": POST, **kw})


def _descoberta(passo: PlanStep, receita: int = 1, reproducoes: int = 1) -> ens.EtapaEnsinada:
    return ens.EtapaEnsinada(passo.key, "qa-messenger", passo, receita=receita, reproducoes=reproducoes,
                             origem="r:o:v1:" + passo.key, descoberta=True)


# ------------------------------------------------------------------ puro
def test_o_molde_descoberto_nao_leva_valor_de_pessoa() -> None:
    assert ens.oferecivel_descoberta(_passo("abrir_configuracoes"), ["@joao"])
    # o planejador livre escreveu o valor no título, no objetivo ou na pós-condição: nunca vai de molde a outra persona
    assert not ens.oferecivel_descoberta(_passo("abrir_perfil", title="Abrir o perfil de @joao"), ["@joao"])
    assert not ens.oferecivel_descoberta(_passo("abrir_perfil", goal="achar Maria Souza"), ["maria souza"])
    assert not ens.oferecivel_descoberta(
        _passo("abrir_perfil", postcondition=Postcondition(kind="model_judged", value="perfil de joao aberto", description="d")),
        ["@joao"])
    assert ens.oferecivel_descoberta(_passo("abrir_perfil", title="Abrir o perfil de {alvo}"), ["@joao"])
    # digitação, cópia de bloco e efeito ficam de fora, como na ensinada
    assert not ens.oferecivel_descoberta(_passo("buscar_termo", bindings={"texto": "{termo}"}), [])
    assert not ens.oferecivel_descoberta(_passo("buscar_termo", for_each="x"), [])
    assert not ens.oferecivel_descoberta(_passo("enviar_mensagem", side_effect=True), [])
    assert not ens.oferecivel_descoberta(_passo("abrir_3"), [])                      # chave fora do formato


def test_a_ensinada_por_pessoa_vence_e_os_blocos_ficam_separados() -> None:
    ensinada = ens.EtapaEnsinada("abrir_ajustes", "qa-messenger", _passo("abrir_ajustes"), receita=7, reproducoes=1,
                                 origem="training:s1")
    descoberta = _descoberta(_passo("abrir_ajustes"), receita=9, reproducoes=8)
    outra = _descoberta(_passo("ver_notificacoes"), receita=10)
    escolhidas = ens.escolher([descoberta, outra, ensinada], ["qa-messenger"])
    assert [(e.nome, e.descoberta) for e in escolhidas] == [("abrir_ajustes", False), ("ver_notificacoes", True)]
    texto = ens.bloco(escolhidas)
    assert texto.index("<etapas_ensinadas") < texto.index("</etapas_ensinadas>") < texto.index("<etapas_descobertas")
    assert "nenhuma pessoa as demonstrou" in texto and texto.count("- nome: abrir_ajustes") == 1
    assert "- nome: ver_notificacoes" in texto.split("<etapas_descobertas")[1]
    assert ens.bloco([ensinada]).count("<etapas_descobertas") == 0                  # sem descoberta, o bloco de antes
    assert ens.bloco([]) == ""


# ------------------------------------------------------------------ banco (migração 130 + leitor)
def _semear(st: Any, rid: str, passo: PlanStep, *, parametros: dict[str, str] | None = None, exploratoria: bool = True,
            status: str = "active", usada_ha_dias: int | None = 1, treino: bool = False) -> int:
    """Uma execução com a etapa gravada e a receita dela. `runs.plan` guarda o plano como o planejador o escreveu."""
    db = st.db
    plano = Plan(summary="s", app_id="qa-messenger", planner=PlannerInfo(provider="t", model="t", simulated=True),
                 steps=[passo], parameters=parametros or {})
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, plan)"
               " VALUES (?,?,?,?,?,?,?,?)", (rid, f"k-{rid}", "c", "execute", "succeeded", '["android-01"]', now_iso(),
                                             plano.model_dump_json()))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,1)",
               (f"{rid}:o", rid, "android-01", "succeeded"))
    sid = f"{rid}:android-01:v1:{passo.key}"
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status, app_id, exploratoria)"
               " VALUES (?,?,?,?,1,1,?,?,?,?,60,3,'succeeded','qa-messenger',?)",
               (sid, rid, f"{rid}:o", "android-01", passo.key, passo.title, passo.goal,
                passo.postcondition.model_dump_json(), 1 if exploratoria else None))
    uso = None if usada_ha_dias is None else to_iso(now() - timedelta(days=usada_ha_dias))
    criada = to_iso(now() - timedelta(days=(usada_ha_dias or 0) + 1))
    db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version,"
               " status, actions, learned_from_step, replay_ok, shadow_agree, created_at, last_used_at)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("com.pocqa.messenger", "1", "sig", "v", f"h-{rid}", passo.key, 1, status, '[{"tool": "tap"}]',
                "training:s9" if treino else sid, 0, 2, criada, uso))
    return int(db.scalar("SELECT id FROM recipes WHERE step_hash=?", (f"h-{rid}",)))


async def test_o_leitor_acha_so_a_receita_ativa_de_etapa_exploratoria_sem_valor(harness: Harness) -> None:
    st = harness.state
    boa = _semear(st, "r-20261010100000-aaaaaa", _passo("abrir_configuracoes"))
    _semear(st, "r-20261010100001-bbbbbb", _passo("ver_ajuda"), status="candidate")           # ainda em prova
    _semear(st, "r-20261010100002-cccccc", _passo("ver_sobre"), exploratoria=False)           # etapa de sempre
    _semear(st, "r-20261010100003-dddddd", _passo("ver_conta", title="Abrir a conta de @joao"),
            parametros={"alvo": "@joao"})                                                      # valor no molde
    _semear(st, "r-20261010100004-eeeeee", _passo("ver_idioma"), status="quarantined")
    _semear(st, "r-20261010100005-ffffff", _passo("ver_tema"), treino=True)                   # origem em treino
    achadas = st.runs.flows.etapas_descobertas()
    assert [(e.nome, e.receita, e.descoberta, e.app_id) for e in achadas] == [
        ("abrir_configuracoes", boa, True, "qa-messenger")]
    assert achadas[0].reproducoes == 2                                                          # as 2 concordâncias


async def test_o_piso_de_dias_sem_uso_tira_da_oferta_e_zero_desliga(harness: Harness) -> None:
    st = harness.state
    _semear(st, "r-20261010100010-aaaaaa", _passo("abrir_configuracoes"), usada_ha_dias=120)
    _semear(st, "r-20261010100011-bbbbbb", _passo("ver_ajuda"), usada_ha_dias=10)
    _semear(st, "r-20261010100012-cccccc", _passo("ver_sobre"), usada_ha_dias=None)           # nunca usada, nasceu agora
    assert sorted(e.nome for e in st.runs.flows.etapas_descobertas(sem_uso_dias=90)) == ["ver_ajuda", "ver_sobre"]
    assert sorted(e.nome for e in st.runs.flows.etapas_descobertas(sem_uso_dias=5)) == ["ver_sobre"]
    assert len(st.runs.flows.etapas_descobertas(sem_uso_dias=0)) == 3                          # 0 = sem piso


async def test_a_marca_exploratoria_e_gravada_na_etapa(harness: Harness) -> None:
    """O insert de `steps` leva a marca do `PlanStep` (migração 130); a de sempre fica nula e fora do JSON do plano."""
    st = harness.state
    assert "exploratoria" in st.db.columns("steps")
    assert "exploratoria" not in _passo("abrir_x").model_dump(mode="json")                      # fora da serialização
    assert _passo("abrir_x", exploratoria=True).model_dump(mode="json")["exploratoria"] is True


async def test_o_plano_livre_com_o_nome_da_descoberta_ganha_o_molde(harness: Harness,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    receita = _semear(st, "r-20261010100020-aaaaaa", _passo("abrir_configuracoes", title="Abrir {tela}",
                                                           goal="chegar em {tela}"),
                      parametros={"tela": "Bluetooth"})
    pedidos: list[PlanRequest] = []
    original = st.runs.provider.plan

    async def planejador(req: PlanRequest) -> Any:
        pedidos.append(req)
        _, uso = await original(req)
        return Plan(summary="abrir", app_id="qa-messenger", parameters={"tela": "Bluetooth"},
                    planner=PlannerInfo(provider="t", model="t", simulated=True),
                    steps=[_passo("abrir_app"), _passo("abrir_configuracoes", title="outro texto",
                                                       depends_on=["abrir_app"])]), uso
    monkeypatch.setattr(st.runs.provider, "plan", planejador)
    run = harness.run(["android-01"], mode="plan", command="abra as configurações no QA Messenger")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=30)
    (req,) = pedidos
    assert [(e.nome, e.descoberta) for e in req.etapas_ensinadas] == [("abrir_configuracoes", True)]
    texto = planner_user(req, 10)
    assert "<etapas_descobertas" in texto and "- nome: abrir_configuracoes | app: qa-messenger | parâmetros: tela" in texto
    assert "<etapas_ensinadas" not in texto
    etapa = st.db.one("SELECT title FROM steps WHERE run_id=? AND key='abrir_configuracoes'", (run.id,))
    assert etapa is not None and etapa["title"] == "Abrir Bluetooth"                            # o molde, já com o valor
    assert st.db.scalar("SELECT COUNT(*) FROM events WHERE run_id=? AND message LIKE ?",
                        (run.id, f"%abrir_configuracoes pela etapa DESCOBERTA pela IA%receita {receita},%")) == 1


async def test_o_livro_marca_a_receita_que_nasceu_de_exploracao(harness: Harness) -> None:
    """O selo `nasceu_de_exploracao` (adendo v1.130): só a receita de etapa marcada; a de sempre e a de treino, não."""
    st = harness.state
    explorada = _semear(st, "r-20261010100030-aaaaaa", _passo("abrir_configuracoes"))
    comum = _semear(st, "r-20261010100031-bbbbbb", _passo("ver_ajuda"), exploratoria=False)
    ensinada = _semear(st, "r-20261010100032-cccccc", _passo("ver_sobre"), exploratoria=False, treino=True)
    entradas = {e.ref: e for e in FontesSql(st.db).receitas()}
    assert [entradas[str(i)].nasceu_de_exploracao for i in (explorada, comum, ensinada)] == [True, False, False]
    assert _entrada(entradas[str(explorada)])["nasceu_de_exploracao"] is True
    assert _entrada(entradas[str(comum)])["nasceu_de_exploracao"] is False
    unica = FontesSql(st.db).receita(str(explorada))
    assert unica is not None and unica.nasceu_de_exploracao

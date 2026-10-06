"""O plano da execução de uma operação (31.154, adendo v1.95): parâmetros fixos e chaves normalizadas.

O que se prova:
- o parâmetro do planejador com o MESMO valor de um fixo é renomeado (no plano e no texto das etapas) e o fixo vale;
  `{{saida:x}}` e `{nome_mais_longo}` não se tocam;
- as etapas das capabilities que o app declara no bloco `operacao` viram `<capability>_<n>`, com `depends_on` e
  `for_each` remapeados; colisão = nada renomeado, com o motivo; aplicar duas vezes dá o mesmo plano;
- a identidade da etapa planejada fica IGUAL à da etapa ensinada (`step_template_hash` e `hash_generico_da_etapa`) e a
  receita ensinada com `{username}`/`{caption_contains}` se reproduz com `objective.parameters`; sem os fixos, o mesmo
  plano dá `RecipeDiverged` "parâmetro ausente" (o que a orquestradora quer evitar na rodada);
- pela operação, no harness: `parametros` gravados, devolvidos no GET, fixados no objetivo de cada alvo; a execução fora
  de operação não muda.

Nível de prova: `simulated` (planos montados no teste; harness na porta 5640 com o provedor simulado).
"""
from __future__ import annotations

import pytest

from app.db import loads
from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.modules.operacoes.infrastructure.servico import AlvoPedido, OperacaoError
from app.taskqueue import plano_da_operacao as pdo
from app.taskqueue.recipes import (RecipeDiverged, hash_generico_da_etapa, para_hash, retemplate,
                                   step_template_hash)

from .conftest import COMMAND, Harness
from .test_operacoes import _alvo, _conta, _pedido, _persona, _servico

CAPS = ("OPEN_PROFILE", "OPEN_POST", "OPEN_COMMENTS")
LOJA, TRECHO = "loja.exemplo", "Coleção de primavera"
PLANEJADOR = PlannerInfo(provider="teste", model="teste", simulated=True)


def _etapa(key: str, cap: str | None, post: str, kind: str = "model_judged", **kw: object) -> PlanStep:
    return PlanStep(key=key, title=f"etapa {key}", goal=kw.pop("goal", "fazer"),  # type: ignore[arg-type]
                    capability=cap, postcondition=Postcondition(kind=kind, value=post, description="d"),  # type: ignore[arg-type]
                    **kw)


def _plano_do_planejador() -> Plan:
    """Como o planejador escreve quando nomeia por conta própria: `perfil_alvo`, `trecho` e chaves em português."""
    return Plan(summary="comentar", planner=PLANEJADOR, parameters={"perfil_alvo": "@" + LOJA, "trecho": TRECHO}, steps=[
        _etapa("abrir_app", None, "app aberto"),
        _etapa("abrir_perfil", "OPEN_PROFILE", "perfil de {perfil_alvo} aberto", depends_on=["abrir_app"],
               bindings={"username": "{perfil_alvo}"}),
        _etapa("abrir_post", "OPEN_POST", "publicação com {trecho} aberta", depends_on=["abrir_perfil"],
               bindings={"target": "a mais recente", "caption_contains": "{trecho}"}),
        _etapa("abrir_comentarios", "OPEN_COMMENTS", "comentários abertos", depends_on=["abrir_post"],
               goal="abrir {perfil_alvo_extra} e {{saida:legenda}}"),
    ])


def _etapa_ensinada() -> PlanStep:
    """A etapa como o ensino a grava: chave do catálogo e o parâmetro com o nome do vínculo do catálogo."""
    return _etapa("open_profile_1", "OPEN_PROFILE", "perfil de {username} aberto", bindings={"username": "{username}"})


FIXOS = {"username": LOJA, "caption_contains": TRECHO}


def test_parametro_com_o_mesmo_valor_e_renomeado_e_o_fixo_vale() -> None:
    p = pdo.fixar_parametros(_plano_do_planejador(), FIXOS)
    assert p.parameters == FIXOS                                   # sem `@`, sem caixa: o mesmo valor
    perfil, post, coments = p.steps[1], p.steps[2], p.steps[3]
    assert perfil.postcondition.value == "perfil de {username} aberto" and perfil.bindings["username"] == "{username}"
    assert post.bindings["caption_contains"] == "{caption_contains}"
    assert coments.goal == "abrir {perfil_alvo_extra} e {{saida:legenda}}"     # nome mais longo e saída intactos


def test_chaves_normalizadas_pelo_catalogo_com_dependencias_e_idempotente() -> None:
    p, motivo = pdo.ajustar(_plano_do_planejador(), FIXOS, CAPS)
    assert motivo is None
    assert [s.key for s in p.steps] == ["abrir_app", "open_profile_1", "open_post_1", "open_comments_1"]
    assert [s.depends_on for s in p.steps] == [[], ["abrir_app"], ["open_profile_1"], ["open_post_1"]]
    assert pdo.ajustar(p, FIXOS, CAPS) == (p, None)


def test_for_each_e_remapeado() -> None:
    plano = Plan(summary="x", planner=PLANEJADOR, steps=[
        _etapa("levantar", "OPEN_PROFILE", "lista", kind="items_collected"),
        _etapa("cada", None, "feito", goal="abrir {item}", for_each="levantar", depends_on=["levantar"])])
    p, _ = pdo.normalizar_chaves(plano, CAPS)
    assert (p.steps[1].for_each, p.steps[1].depends_on) == ("open_profile_1", ["open_profile_1"])


def test_colisao_nao_renomeia_nada_e_diz_por_que() -> None:
    plano = Plan(summary="x", planner=PLANEJADOR, steps=[_etapa("open_profile_1", None, "outra"), _etapa("abrir", "OPEN_PROFILE", "p")])
    p, motivo = pdo.normalizar_chaves(plano, CAPS)
    assert p == plano and motivo is not None and "open_profile_1" in motivo


def test_a_identidade_fica_a_da_etapa_ensinada_e_a_receita_se_reproduz() -> None:
    ensinada = _etapa_ensinada()
    p, _ = pdo.ajustar(_plano_do_planejador(), FIXOS, CAPS)
    planejada = p.steps[1]
    assert step_template_hash(para_hash(planejada, p.parameters)) == step_template_hash(para_hash(ensinada, FIXOS))
    assert hash_generico_da_etapa(planejada) == hash_generico_da_etapa(ensinada) is not None
    # A ação gravada pelo ensino digita `{username}`: com os parâmetros do objetivo da operação, ela se reproduz.
    assert retemplate("{username}", dict(p.parameters)) == LOJA
    assert retemplate("{caption_contains}", dict(p.parameters)) == TRECHO
    # Controle: o mesmo plano SEM os fixos fica com os nomes do planejador, e a receita ensinada diverge.
    cru, _ = pdo.ajustar(_plano_do_planejador(), {}, CAPS)
    with pytest.raises(RecipeDiverged, match="parâmetro ausente"):
        retemplate("{username}", dict(cru.parameters))


async def test_operacao_com_parametros_fixa_o_objetivo_de_cada_alvo(harness: Harness) -> None:
    """Pelo harness: o provedor simulado chama o contato de `recipient`; a operação o fixa como `contato`."""
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Nina", "android-01")
    _conta(harness, pid, "qa-user-01", sessao_em="android-01")
    op = _servico(harness).criar(_pedido([AlvoPedido(pid)], chave="teste-op-parametros",
                                         parametros={"contato": "QA-001"}))
    assert op["parametros"] == {"contato": "QA-001"}
    run_id = _alvo(op, pid)["run_id"]
    await harness.wait(lambda: st.db.one("SELECT id FROM objectives WHERE run_id=?", (run_id,)) is not None,
                       what="plano da operação materializado")
    obj = st.db.one("SELECT parameters FROM objectives WHERE run_id=?", (run_id,))
    params = loads(obj["parameters"], {})
    assert params.get("contato") == "QA-001" and "recipient" not in params
    plano = loads(st.db.scalar("SELECT plan FROM runs WHERE id=?", (run_id,)), {})
    textos = " ".join(s["title"] + s["goal"] for s in plano["steps"])
    assert "{contato}" in textos and "{recipient}" not in textos


async def test_execucao_fora_de_operacao_nao_muda(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    from app.models import RunCreate
    run = st.runs.create(RunCreate(command=COMMAND, idempotency_key="teste-fora-de-operacao", instance_ids=["android-02"]))
    await harness.wait(lambda: st.db.one("SELECT id FROM objectives WHERE run_id=?", (run.id,)) is not None,
                       what="plano materializado")
    params = loads(st.db.scalar("SELECT parameters FROM objectives WHERE run_id=?", (run.id,)), {})
    assert "recipient" in params


@pytest.mark.parametrize(("parametros", "trecho"), [
    ({"run_id": "x"}, "não aceito"), ({"perfil_email": "x"}, "não aceito"), ({"Username": "x"}, "não aceito"),
    ({"username": "{run_id}"}, "sem chaves"), ({"username": ""}, "de 1 a 300"),
    ({f"p{i}": f"x{i}" for i in range(11)}, "No máximo 10"), ({"username": "a.b", "perfil": "@A.B"}, "mesmo valor"),
])
async def test_parametros_invalidos_sao_recusados(harness: Harness, parametros: dict[str, str], trecho: str) -> None:
    with pytest.raises(OperacaoError, match=trecho) as exc:
        _servico(harness).criar(_pedido([AlvoPedido("p-x")], chave="teste-op-param-inv", parametros=parametros))
    assert exc.value.code == "pedido_invalido"


async def test_mesma_chave_com_outros_parametros_e_outro_corpo(harness: Harness) -> None:
    pid = _persona(harness, "Olga")
    s = _servico(harness)
    s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-param-chave", parametros={"username": "a.b.c"}))
    with pytest.raises(OperacaoError) as exc:
        s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-param-chave", parametros={"username": "d.e.f"}))
    assert exc.value.code == "chave_em_uso"


@pytest.mark.parametrize("parametros", [
    {"senha": "qualquer.coisa"}, {"codigo_de_acesso": "abc.def"}, {"token": "abc.def"},
    {"username": "senha=Hunter2xy"}, {"username": "483920"}, {"username": "Hunter2!xy"},
])
async def test_credencial_em_parametro_e_recusada_pelo_nome_ou_pelo_formato(harness: Harness,
                                                                            parametros: dict[str, str]) -> None:
    """Credencial nunca vai a parâmetro (ADR-040): nem pelo nome, nem pelo par `nome=valor`, nem pelo formato sozinho."""
    st = harness.state
    assert st is not None
    with pytest.raises(OperacaoError) as exc:
        _servico(harness).criar(_pedido([AlvoPedido("p-x")], chave="teste-op-param-cred", parametros=parametros))
    assert exc.value.code == "credencial_no_comando"
    assert st.db.scalar("SELECT COUNT(*) FROM operacoes WHERE idempotency_key='teste-op-param-cred'") == 0


def test_dado_da_persona_e_nome_sensivel_do_plano_nao_sao_renomeados() -> None:
    plano = Plan(summary="x", planner=PLANEJADOR, parameters={"perfil_email": LOJA, "codigo": "Coleção de primavera"},
                 steps=[_etapa("ab", None, "perfil {perfil_email} e {codigo}")])
    p = pdo.fixar_parametros(plano, FIXOS)
    assert {"perfil_email", "codigo"} <= set(p.parameters)
    assert p.steps[0].postcondition.value == "perfil {perfil_email} e {codigo}"


def test_chave_normalizada_fora_do_formato_nao_renomeia_nada() -> None:
    plano = Plan(summary="x", planner=PLANEJADOR, steps=[_etapa("abrir", "ABRIR_" + "X" * 40, "p")])
    p, motivo = pdo.normalizar_chaves(plano, ["ABRIR_" + "X" * 40])
    assert p == plano and motivo is not None and "fora do formato" in motivo

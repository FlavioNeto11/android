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


async def test_nome_fixo_em_conflito_recusa_o_alvo_e_a_operacao_le_acao_bloqueada(harness: Harness) -> None:
    """Achado da revisão do PR 479: o planejador chama o contato de `recipient` (QA-001) e a operação fixa `recipient`
    com OUTRO valor. Seguir com o do planejador mandaria a ação ao alvo errado: a execução do alvo termina recusada no
    planejamento, sem objetivo, e a operação lê o alvo em `acao_bloqueada` com "parâmetro em conflito", sem os valores."""
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Nina", "android-01")
    _conta(harness, pid, "qa-user-01", sessao_em="android-01")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-param-conflito", parametros={"recipient": "QA-999"}))
    run_id = _alvo(op, pid)["run_id"]
    await harness.wait(lambda: st.db.scalar("SELECT status FROM runs WHERE id=?", (run_id,)) == "failed",
                       what="alvo recusado no planejamento")
    assert st.db.one("SELECT id FROM objectives WHERE run_id=?", (run_id,)) is None
    eventos = [loads(r["data"], {}) for r in st.db.query("SELECT data FROM events WHERE kind='plan.refused' AND run_id=?",
                                                         (run_id,))]
    assert eventos and eventos[-1] == {"motivo": "parametro_em_conflito", "parametros": ["recipient"]}
    lida = s.ler(op["id"])
    alvo = lida["alvos"][0]
    assert (alvo["estado"], alvo["estagio"], alvo["parou_em"]) == ("bloqueado", "acao_bloqueada", "acao_bloqueada")
    assert alvo["motivo"].startswith("parâmetro em conflito: recipient")
    assert "QA-999" not in alvo["motivo"] and "QA-001" not in alvo["motivo"]
    assert lida["status"] == "concluida_com_bloqueios" and lida["finished_at"]
    assert lida["capacidade"]["bloqueadas"] == 1


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


@pytest.mark.parametrize("parametros", [
    {"senha_Xk9mP2q": "loja.exemplo"}, {"Hunter2xyQ": "loja.exemplo"}, {"username": "loja.exemplo", "tok3n_A": "{x}"},
])
async def test_a_recusa_nao_devolve_o_nome_do_parametro(harness: Harness, parametros: dict[str, str]) -> None:
    """Achado do Copilot no PR 487: a credencial pode estar no NOME da chave, e a recusa devolvia o nome no corpo do
    erro (que volta ao cliente e vai ao log). A recusa diz a posição, nunca o nome nem o valor."""
    with pytest.raises(OperacaoError) as exc:
        _servico(harness).criar(_pedido([AlvoPedido("p-x")], chave="teste-op-param-nome", parametros=parametros))
    for nome in parametros:
        assert nome not in exc.value.message and nome.lower() not in exc.value.message.lower()
    assert "parâmetro" in exc.value.message


def test_o_valor_comparavel_nao_tem_espaco_nenhum() -> None:
    """Achado do Copilot no PR 487: o contrato diz "sem espaços"; `strip` só tirava as pontas."""
    assert pdo.normal(" @ Loja .Exemplo ") == pdo.normal("loja.exemplo") == "loja.exemplo"
    plano = Plan(summary="x", planner=PLANEJADOR, parameters={"perfil_alvo": "@ loja . exemplo"},
                 steps=[_etapa("ab", "OPEN_PROFILE", "perfil de {perfil_alvo}")])
    assert pdo.fixar_parametros(plano, {"username": LOJA}).parameters == {"username": LOJA}


def test_dado_da_persona_e_nome_sensivel_do_plano_nao_sao_renomeados() -> None:
    plano = Plan(summary="x", planner=PLANEJADOR, parameters={"perfil_email": LOJA, "codigo": "Coleção de primavera"},
                 steps=[_etapa("ab", None, "perfil {perfil_email} e {codigo}")])
    p = pdo.fixar_parametros(plano, FIXOS)
    assert {"perfil_email", "codigo"} <= set(p.parameters)
    assert p.steps[0].postcondition.value == "perfil {perfil_email} e {codigo}"


def test_nome_fixo_ja_usado_com_outro_valor_nao_e_sobrescrito() -> None:
    """Achado da revisão do PR 478: o plano já tem `username` = outro perfil e um segundo parâmetro com o valor do fixo.
    Fixar sobrescreveria `username`, e as duas referências apontariam para o mesmo perfil (ação na conta errada)."""
    plano = Plan(summary="x", planner=PLANEJADOR, parameters={"username": "outro.perfil", "perfil_alvo": "@" + LOJA},
                 steps=[_etapa("ab", "OPEN_PROFILE", "perfil de {username} e de {perfil_alvo}")])
    p, motivo = pdo.ajustar(plano, {"username": LOJA}, CAPS)
    assert p.parameters == {"username": "outro.perfil", "perfil_alvo": "@" + LOJA}
    assert p.steps[0].postcondition.value == "perfil de {username} e de {perfil_alvo}"
    assert motivo is not None and "username" in motivo
    # o mesmo valor (sem @ e sem caixa) não é colisão: o fixo vale, sem motivo
    igual = plano.model_copy(update={"parameters": {"username": "@" + LOJA.upper()}})
    p2, motivo2 = pdo.ajustar(igual, {"username": LOJA}, CAPS)
    assert p2.parameters == {"username": LOJA} and motivo2 is None


def test_chave_normalizada_fora_do_formato_nao_renomeia_nada() -> None:
    plano = Plan(summary="x", planner=PLANEJADOR, steps=[_etapa("abrir", "ABRIR_" + "X" * 40, "p")])
    p, motivo = pdo.normalizar_chaves(plano, ["ABRIR_" + "X" * 40])
    assert p == plano and motivo is not None and "fora do formato" in motivo


# ------------------------------------------------------------------ 31.224: o parâmetro confere com o app
@pytest.mark.parametrize(("parametros", "motivo", "posicao"), [
    ({"usernmae": "loja.exemplo"}, "parametro_desconhecido", 1),
    ({"caption_contains": "Setembro Amarelo", "username": "@loja.exemplo"}, "username_com_arroba", 2),
    ({"username": "loja exemplo"}, "username_com_espaco", 1),
])
async def test_parametro_que_nao_casa_com_o_app_e_recusado_antes_de_qualquer_execucao(
        harness: Harness, parametros: dict[str, str], motivo: str, posicao: int) -> None:
    """31.224: um erro de digitação na prova não pode custar chamada paga. No app com catálogo (o Instagram), a chave
    fora dele é recusada com a lista dos aceitos; `username` vai sem arroba e sem espaço. Nada é gravado e nenhuma
    execução nasce. A recusa diz a posição e o motivo, nunca o nome que veio."""
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Prova")
    runs, ops = st.db.scalar("SELECT COUNT(*) FROM runs"), st.db.scalar("SELECT COUNT(*) FROM operacoes")
    with pytest.raises(OperacaoError) as exc:
        _servico(harness).criar(_pedido([AlvoPedido(pid)], chave="teste-op-param-app", app_id="instagram",
                                        parametros=parametros))
    assert (exc.value.code, exc.value.status) == ("pedido_invalido", 422)
    assert exc.value.extra["motivo"] == motivo and exc.value.extra["posicao"] == posicao
    if motivo == "parametro_desconhecido":
        assert "usernmae" not in exc.value.message
        assert {"username", "caption_contains"} <= set(exc.value.extra["aceitos"])  # type: ignore[arg-type]
    else:
        assert exc.value.extra["campo"] == "username"
    assert st.db.scalar("SELECT COUNT(*) FROM runs") == runs and st.db.scalar("SELECT COUNT(*) FROM operacoes") == ops


async def test_parametros_do_catalogo_passam_e_o_app_sem_catalogo_segue_livre(harness: Harness) -> None:
    pid = _persona(harness, "Livre")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-param-ok", app_id="instagram",
                         parametros={"username": "loja.exemplo", "caption_contains": "Setembro Amarelo"}))
    assert op["parametros"] == {"username": "loja.exemplo", "caption_contains": "Setembro Amarelo"}
    # o QA Messenger não tem catálogo: a chave livre segue aceita, e só o username tem a regra dele
    assert s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-param-livre", parametros={"contato": "QA-001"}))["id"]
    with pytest.raises(OperacaoError) as exc:
        s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-param-livre2", parametros={"username": "@qa"}))
    assert exc.value.extra["motivo"] == "username_com_arroba"


async def test_rota_devolve_o_motivo_e_a_posicao_no_422(harness: Harness) -> None:
    import httpx

    from app.main import create_app

    st = harness.state
    assert st is not None
    pid = _persona(harness, "Rota")
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    corpo = {"command": COMMAND, "app_id": "instagram", "alvos": [{"profile_id": pid}],
             "idempotency_key": "teste-op-param-http", "max_usd": 0.5, "parametros": {"nome_do_perfil": "loja.exemplo"}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/operacoes", json=corpo)
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert (d["code"], d["motivo"], d["posicao"]) == ("pedido_invalido", "parametro_desconhecido", 1)
    assert "username" in d["aceitos"] and "nome_do_perfil" not in d["message"]

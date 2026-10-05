"""Item 31.87, fatia F1: pré-voo do dado da persona ausente.

O defeito: o plano (de fluxo reaproveitado ou novo) cita `{perfil_sobrenome}` e a persona do aparelho não tem o dado.
`resolve_templates` deixava o `{perfil_sobrenome}` escrito por extenso no objetivo da etapa; a receita divergia e a IA
assumia com o texto cru (digitava lixo ou gastava decisão à toa).

O que estes testes protegem:

* a execução vira pedido de resposta (`needs_input`) ANTES de materializar: zero objetivos, zero etapas, nada
  despachado, e a mensagem diz por aparelho o que falta, só com id do aparelho e rótulo do campo;
* com dois aparelhos, a mensagem cita só o que falta e NENHUM dos dois despacha;
* aparelho sem persona vinculada: todo `{perfil_*}` falta;
* o caminho feliz (todos os dados) materializa e resolve igual a antes;
* a materialização recusa sozinha, sem valor, quando sobra variável da persona sem resolver.

Nível de prova: `simulated` (provedor por regras, aparelhos falsos, valores sintéticos, banco de teste).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.models import Plan, PlanStep, PlannerInfo, Postcondition, ProfileCreate, RunCreate
from app.taskqueue.dado_da_persona import (DadoDaPersonaAusente, exigir_resolvido, faltas_por_aparelho, nomes_citados,
                                           perguntas, rotulo)

from .conftest import Harness
from .test_for_each import ALL

# Valores SINTÉTICOS: nenhum deles pode aparecer na mensagem de falta.
NOME = "Zelda"
SOBRENOME = "Sintetica"
EMAIL = "zelda.sintetica@exemplo.test"
USUARIO_SEM_SOBRENOME = "pessoa.sem.sobrenome"


def _plano(inner: Any, texto: str, **extra: Any) -> None:
    """O planejador simulado passa a devolver o plano de sempre com `texto` no objetivo da 1ª etapa."""
    plan0 = inner.plan

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        primeira = p.steps[0].model_copy(update={"goal": texto})
        return p.model_copy(update={"steps": [primeira, *p.steps[1:]], **extra}), u

    inner.plan = plan


def _persona(h: Harness, iid: str, username: str, **campos: str | None) -> str:
    assert h.state is not None
    return h.state.social.create_profile(ProfileCreate(username=username, instance_id=iid, **campos)).id


def _linhas(h: Harness, run_id: str, tabela: str) -> int:
    assert h.state is not None
    return int(h.state.db.scalar(f"SELECT COUNT(*) FROM {tabela} WHERE run_id=?", (run_id,)))  # noqa: S608


def _tudo_da_execucao(h: Harness, run_id: str) -> str:
    """O que a pessoa e o painel leem da execução: a linha, os eventos e as decisões, em texto."""
    assert h.state is not None
    partes = [json.dumps(dict(h.state.repo.run_row(run_id)), default=str)]
    partes += [json.dumps(dict(r), default=str) for r in h.state.db.query("SELECT * FROM events WHERE run_id=?", (run_id,))]
    return "\n".join(partes)


def _campos_das_perguntas(h: Harness, run_id: str) -> list[str]:
    """O `field` de cada pergunta que o painel lê do evento `log` (`data.questions`)."""
    assert h.state is not None
    campos: list[str] = []
    for r in h.state.db.query("SELECT data FROM events WHERE run_id=? AND kind='log'", (run_id,)):
        campos += [str(q["field"]) for q in (json.loads(r["data"] or "null") or {}).get("questions", [])]
    return campos


def _detalhe(h: Harness, run_id: str) -> str:
    assert h.state is not None
    return str(h.state.repo.run_row(run_id)["status_detail"])


# ------------------------------------------------------------ 1. persona sem o dado: pede resposta e não materializa
async def test_persona_sem_sobrenome_pede_resposta_e_nao_materializa(harness: Harness) -> None:
    _persona(harness, "android-01", USUARIO_SEM_SOBRENOME, first_name=NOME, email=EMAIL)
    _plano(harness.ai.inner, "Digitar o sobrenome {perfil_sobrenome} no campo de nome.")
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=("needs_input", "failed", "completed", "awaiting_person"))
    assert harness.state.repo.run_row(run.id)["status"] == "needs_input"        # type: ignore[union-attr]
    # Nada materializado, nada despachado: o objetivo e as etapas nem existem.
    assert _linhas(harness, run.id, "objectives") == 0 and _linhas(harness, run.id, "steps") == 0
    assert harness.state.db.scalar("SELECT COUNT(*) FROM attempts WHERE step_id LIKE ?",   # type: ignore[union-attr]
                                   (run.id + ":%",)) == 0
    detalhe = _detalhe(harness, run.id)
    assert "android-01" in detalhe and "sobrenome" in detalhe
    # C1: falta de DADO não é pergunta de destino (o painel esconderia a orientação certa), e a frase só promete o que
    # funciona: cadastrar na persona e pedir de novo (dizer o valor no comando não vira parâmetro do fluxo).
    assert _campos_das_perguntas(harness, run.id) == ["persona_data"]
    assert "cadastre o dado na persona e peça de novo" in detalhe and "diga o valor" not in detalhe
    # Só id e rótulo: nenhum valor, nome de persona, e-mail nem handle em lugar nenhum da execução.
    tudo = _tudo_da_execucao(harness, run.id)
    for proibido in (NOME, EMAIL, USUARIO_SEM_SOBRENOME):
        assert proibido not in tudo
    assert harness.ai.count("decide") == 0


async def test_dois_aparelhos_a_mensagem_cita_so_o_que_falta_e_nenhum_despacha(harness: Harness) -> None:
    _persona(harness, "android-01", "pessoa.completa", first_name=NOME, last_name=SOBRENOME, email=EMAIL)
    _persona(harness, "android-02", USUARIO_SEM_SOBRENOME, first_name="Aldo", email="aldo@exemplo.test")
    _plano(harness.ai.inner, "Digitar {perfil_nome} {perfil_sobrenome} no campo de nome.")
    run = harness.run(["android-01", "android-02"])
    await harness.wait_run(run.id, statuses=("needs_input", "failed", "completed", "awaiting_person"))
    assert harness.state.repo.run_row(run.id)["status"] == "needs_input"        # type: ignore[union-attr]
    detalhe = _detalhe(harness, run.id)
    assert "android-02" in detalhe and "sobrenome" in detalhe
    assert "android-01" not in detalhe          # o que tem o dado não entra na mensagem
    assert "nome e " not in detalhe             # o nome da persona do android-02 existe: só o sobrenome falta
    # Nenhum dos dois despacha, nem o que estava completo: o plano é um só.
    assert _linhas(harness, run.id, "objectives") == 0 and _linhas(harness, run.id, "steps") == 0
    tudo = _tudo_da_execucao(harness, run.id)
    for proibido in (NOME, SOBRENOME, EMAIL, "Aldo", "aldo@exemplo.test", "pessoa.completa", USUARIO_SEM_SOBRENOME):
        assert proibido not in tudo


async def test_aparelho_sem_persona_vinculada_falta_tudo(harness: Harness) -> None:
    _plano(harness.ai.inner, "Digitar {perfil_nome} {perfil_sobrenome} e {perfil_email}.")
    run = harness.run(["android-03"])
    await harness.wait_run(run.id, statuses=("needs_input", "failed", "completed", "awaiting_person"))
    assert harness.state.repo.run_row(run.id)["status"] == "needs_input"        # type: ignore[union-attr]
    detalhe = _detalhe(harness, run.id)
    assert "android-03" in detalhe and "não tem persona vinculada" in detalhe
    for rotulo in ("nome", "sobrenome", "e-mail"):
        assert rotulo in detalhe
    # Sem persona vinculada falta o DESTINO: aí a orientação de destino do painel é a certa.
    assert _campos_das_perguntas(harness, run.id) == ["profile_id"] and "diga o valor" not in detalhe
    assert _linhas(harness, run.id, "objectives") == 0 and _linhas(harness, run.id, "steps") == 0


async def test_usuario_da_conta_que_a_persona_nao_tem_tambem_pede_resposta(harness: Harness) -> None:
    """`{conta_<app>_usuario}` é variável de persona como as `{perfil_*}`: persona sem conta do Outlook não resolve."""
    _persona(harness, "android-01", "pessoa.sem.outlook", first_name=NOME, last_name=SOBRENOME)
    _plano(harness.ai.inner, "Abrir o e-mail de {conta_outlook_usuario}.")
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=("needs_input", "failed", "completed", "awaiting_person"))
    assert harness.state.repo.run_row(run.id)["status"] == "needs_input"        # type: ignore[union-attr]
    detalhe = _detalhe(harness, run.id)
    assert "android-01" in detalhe and "usuário da conta outlook" in detalhe
    assert _linhas(harness, run.id, "objectives") == 0


async def test_parametro_com_valor_padrao_da_persona_tambem_conta(harness: Harness) -> None:
    """`plan.parameters` com `{perfil_email}` também é texto que a materialização resolve: persona sem e-mail não passa."""
    _persona(harness, "android-01", "pessoa.sem.email", first_name=NOME, last_name=SOBRENOME)
    _plano(harness.ai.inner, "Abrir o app.", parameters={"contato": "{perfil_email}"})
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=("needs_input", "failed", "completed", "awaiting_person"))
    assert harness.state.repo.run_row(run.id)["status"] == "needs_input"        # type: ignore[union-attr]
    assert "e-mail" in _detalhe(harness, run.id)
    assert _linhas(harness, run.id, "objectives") == 0


# ------------------------------------------------------------ 2. caminho feliz: igual a antes
async def test_caminho_feliz_com_todos_os_dados_materializa_e_resolve(harness: Harness) -> None:
    _persona(harness, "android-01", "pessoa.completa", first_name=NOME, last_name=SOBRENOME, email=EMAIL)
    _plano(harness.ai.inner, "Conferir o sobrenome {perfil_sobrenome} do cadastro.")
    run = harness.run(["android-01"], mode="plan")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed", "completed"))
    assert harness.state.repo.run_row(run.id)["status"] == "planned"            # type: ignore[union-attr]
    assert _linhas(harness, run.id, "objectives") == 1 and _linhas(harness, run.id, "steps") > 0
    primeira = harness.state.db.one(                                            # type: ignore[union-attr]
        "SELECT goal FROM steps WHERE run_id=? ORDER BY seq LIMIT 1", (run.id,))
    assert primeira["goal"] == f"Conferir o sobrenome {SOBRENOME} do cadastro."
    assert "{perfil_" not in primeira["goal"]


async def test_plano_sem_variavel_da_persona_nao_muda_com_persona_ausente(harness: Harness) -> None:
    """O pré-voo só olha o que o plano cita: sem `{perfil_*}`, aparelho sem persona segue como sempre."""
    run = harness.run(["android-03"], mode="plan")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed", "completed"))
    assert harness.state.repo.run_row(run.id)["status"] == "planned"            # type: ignore[union-attr]
    assert _linhas(harness, run.id, "objectives") == 1


# ------------------------------------------------------------ 3. defesa em profundidade: a materialização
def _plano_puro(goal: str, **extra: Any) -> Plan:
    passo = PlanStep(key="digitar", title="Digitar", goal=goal,
                     postcondition=Postcondition(kind="text_visible", value="ok", description="d"), **extra)
    return Plan(summary="s", steps=[passo], planner=PlannerInfo(provider="t", model="t", simulated=True))


def test_materializacao_recusa_perfil_sobrando_sem_gravar_nada(harness: Harness) -> None:
    repo = harness.state.repo                                                   # type: ignore[union-attr]
    run, _ = repo.create_run(RunCreate(command="teste de defesa", instance_ids=["android-01"], idempotency_key="k-defesa"),
                             simulated=True)
    plano = _plano_puro("Digitar {perfil_nome} {perfil_sobrenome}.")
    with pytest.raises(DadoDaPersonaAusente) as erro:
        repo.materialize(run["id"], plano, [{"instance_id": "android-01", "variables": {"perfil_nome": NOME}}])
    assert erro.value.faltam == ("perfil_sobrenome",)
    assert "perfil_sobrenome" in str(erro.value) and NOME not in str(erro.value)
    assert harness.state.db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=?", (run["id"],)) == 0  # type: ignore[union-attr]
    assert harness.state.db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=?", (run["id"],)) == 0       # type: ignore[union-attr]


def test_materializacao_recusa_valor_vazio_e_conta_sem_usuario() -> None:
    plano = _plano_puro("Abrir {conta_instagram_usuario} com {perfil_email}.")
    with pytest.raises(DadoDaPersonaAusente) as erro:
        exigir_resolvido(plano, {"perfil_email": "   ", "conta_instagram_usuario": ""})   # vazio não é valor
    assert erro.value.faltam == ("conta_instagram_usuario", "perfil_email")
    exigir_resolvido(plano, {"perfil_email": EMAIL, "conta_instagram_usuario": "u.sintetico"})   # completo passa


async def test_sem_o_prevoo_a_materializacao_derruba_a_execucao_com_mensagem_clara(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Com o pré-voo cego, a recusa da materialização não pode deixar a execução presa em `planning`: ela falha com o
    motivo (só nomes de variável) e nada é gravado."""
    from app.taskqueue import service as service_mod

    monkeypatch.setattr(service_mod, "faltas_por_aparelho", lambda *_a, **_k: {})
    _persona(harness, "android-01", USUARIO_SEM_SOBRENOME, first_name=NOME, email=EMAIL)
    _plano(harness.ai.inner, "Digitar o sobrenome {perfil_sobrenome}.")
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=("needs_input", "failed", "completed", "awaiting_person"))
    assert harness.state.repo.run_row(run.id)["status"] == "failed"             # type: ignore[union-attr]
    detalhe = _detalhe(harness, run.id)
    assert "perfil_sobrenome" in detalhe and NOME not in _tudo_da_execucao(harness, run.id)
    assert _linhas(harness, run.id, "objectives") == 0 and _linhas(harness, run.id, "steps") == 0


# ------------------------------------------------------------ 4. a varredura (pura)
def test_a_varredura_olha_todo_texto_que_vai_ao_aparelho() -> None:
    passo = PlanStep(key="digitar", title="t {perfil_nome}", goal="g", precondition="p {perfil_sobrenome}",
                     postcondition=Postcondition(kind="text_visible", value="v {perfil_nascimento}",
                                                 description="d {perfil_nome_exibicao}"),
                     commit_guard=["c {perfil_email}"], band_guard=["b {conta_chrome_portal_x_usuario}"],
                     bindings={"alvo": "{conta_instagram_usuario}"})
    plano = Plan(summary="s", parameters={"p": "{conta_outlook_usuario_2}"}, steps=[passo],
                 planner=PlannerInfo(provider="t", model="t", simulated=True))
    assert set(nomes_citados(plano)) == {
        "perfil_nome", "perfil_sobrenome", "perfil_nascimento", "perfil_nome_exibicao", "perfil_email",
        "conta_chrome_portal_x_usuario", "conta_instagram_usuario", "conta_outlook_usuario_2"}


def test_o_que_nao_e_dado_da_persona_nao_conta() -> None:
    """`{perfil_alvo}` é parâmetro do comando, `{instance_id}` e `{{saida:x}}` são outras coisas, e a senha nunca é
    variável: nada disso entra no pré-voo."""
    plano = _plano_puro("Abrir {perfil_alvo} em {instance_id} com {{saida:perfil_citado}} e {conta_chrome_senha}.")
    assert nomes_citados(plano) == []
    assert faltas_por_aparelho(plano, [{"instance_id": "android-01", "variables": {}}]) == {}


def test_parametro_do_plano_com_o_mesmo_nome_resolve() -> None:
    """Se o plano traz `perfil_sobrenome` como parâmetro com valor, `materialize` o resolve (`{**params, **base}`): o
    pré-voo não pode recusar o que hoje funciona."""
    plano = _plano_puro("Digitar {perfil_sobrenome}.")
    plano.parameters = {"perfil_sobrenome": "Do Comando"}
    assert faltas_por_aparelho(plano, [{"instance_id": "android-01", "variables": {}}]) == {}


def test_a_pergunta_so_leva_id_e_rotulo() -> None:
    faltas = {"android-12": ["perfil_sobrenome"], "android-13": ["perfil_nome", "perfil_email"]}
    q = perguntas(faltas, sem_persona=frozenset({"android-13"}))
    assert [p["instance_id"] for p in q] == ["android-12", "android-13"]
    assert q[0]["question"] == ("A persona do android-12 não tem sobrenome cadastrado: cadastre o dado na persona e "
                                "peça de novo.")
    assert [p["field"] for p in q] == ["persona_data", "profile_id"]
    assert "não tem persona vinculada" in str(q[1]["question"]) and "nome e e-mail" in str(q[1]["question"])


def test_conta_com_host_e_sufixo_numerado_casa_com_a_expressao() -> None:
    """`conta_<app>_<host>_usuario` e `conta_<app>_usuario_2` (colisão de slug) são variáveis; a senha e o app sem o
    sufixo `_usuario` não."""
    plano = _plano_puro("{conta_chrome_portal_exemplo_test_usuario} {conta_instagram_usuario_2} {conta_chrome_senha} "
                        "{conta_instagram}")
    assert nomes_citados(plano) == ["conta_chrome_portal_exemplo_test_usuario", "conta_instagram_usuario_2"]
    assert rotulo("conta_chrome_portal_exemplo_test_usuario") == "usuário da conta chrome portal exemplo test"
    assert rotulo("conta_instagram_usuario_2") == "usuário da conta instagram"


# ------------------------------------------------------------ 5. R1: o replano não grava a variável crua
def _objetivo_sem_persona(harness: Harness, chave: str) -> tuple[str, str, Plan]:
    """Objetivo materializado com o dado (foto do planejamento) mas SEM persona hoje: o replano relê a persona e a
    variável deixa de resolver."""
    repo = harness.state.repo                                                   # type: ignore[union-attr]
    run, _ = repo.create_run(RunCreate(command="teste de replano", instance_ids=["android-01"], idempotency_key=chave),
                             simulated=True)
    plano = _plano_puro("Digitar {perfil_nome}.")
    repo.save_plan(run["id"], plano)
    repo.materialize(run["id"], plano, [{"instance_id": "android-01", "variables": {"perfil_nome": NOME}}])
    return run["id"], f"{run['id']}:android-01", plano


def test_revise_plan_recusa_dado_ausente_antes_de_gravar(harness: Harness) -> None:
    repo = harness.state.repo                                                   # type: ignore[union-attr]
    run_id, oid, plano = _objetivo_sem_persona(harness, "k-replano-1")
    antes = repo.objective_row(oid)["plan_version"]
    assert repo.faltas_do_replano(oid, plano.steps) == ["perfil_nome"]
    with pytest.raises(DadoDaPersonaAusente) as erro:
        repo.revise_plan(oid, "teste", plano.steps)
    assert erro.value.faltam == ("perfil_nome",) and NOME not in str(erro.value)
    assert repo.objective_row(oid)["plan_version"] == antes                    # nada gravado
    assert harness.state.db.scalar("SELECT COUNT(*) FROM plan_versions WHERE objective_id=?", (oid,)) == 1  # type: ignore[union-attr]


def test_recuperacao_automatica_e_recusada_com_motivo_so_com_nomes(harness: Harness) -> None:
    run_id, oid, plano = _objetivo_sem_persona(harness, "k-replano-2")
    sched = harness.state.scheduler                                             # type: ignore[union-attr]
    motivo = sched._revisao_condenada(oid, harness.state.repo.run_row(run_id), list(plano.steps))  # noqa: SLF001
    assert motivo is not None and "perfil_nome" in motivo and "não foi tentada" in motivo and NOME not in motivo


def test_retomar_item_com_dado_ausente_vira_recusa_clara(harness: Harness) -> None:
    from app.taskqueue.service import RunError

    run_id, oid, _plano = _objetivo_sem_persona(harness, "k-replano-3")
    obj = harness.state.repo.objective_row(oid)                                 # type: ignore[union-attr]
    with pytest.raises(RunError) as erro:
        harness.state.runs._requeue(obj, "teste")                               # type: ignore[union-attr]  # noqa: SLF001
    assert erro.value.code == "dado_da_persona_ausente" and "perfil_nome" in erro.value.message
    assert NOME not in erro.value.message


# ------------------------------------------------------------ 6. D1: o bloqueio da expansão não se desfaz
async def test_expansao_bloqueada_pelo_dado_da_persona_nao_deixa_o_objetivo_preso_em_running(harness: Harness) -> None:
    """A persona tinha o sobrenome quando o plano foi materializado e o perdeu antes da coleta terminar: a expansão do
    `for_each` relê `runs.plan`, vê `{perfil_sobrenome}` sem valor e BLOQUEIA o item. O worker não pode seguir para a
    próxima etapa pronta (refaria `running` e deixaria as etapas-modelo pending, sem worker)."""
    pid = _persona(harness, "android-01", "pessoa.completa", first_name=NOME, last_name=SOBRENOME, email=EMAIL)
    plan0 = harness.ai.inner.plan

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        passos = [s.model_copy(update={"goal": s.goal + " {perfil_sobrenome}"}) if s.for_each else s for s in p.steps]
        independente = PlanStep(key="verificar_depois", title="Verificar depois", goal="Conferir a tela.",
                                depends_on=[passos[0].key],
                                postcondition=Postcondition(kind="text_visible", value="ok", description="d"))
        return p.model_copy(update={"steps": [*passos, independente]}), u

    harness.ai.inner.plan = plan
    run = harness.run(["android-01"], command=ALL, mode="plan")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"))
    assert harness.state.repo.run_row(run.id)["status"] == "planned"            # type: ignore[union-attr]
    db = harness.state.db                                                       # type: ignore[union-attr]
    db.execute("UPDATE instagram_profiles SET last_name=NULL WHERE id=?", (pid,))   # a persona perde o dado
    harness.state.runs.start(run.id)                                            # type: ignore[union-attr]
    oid = f"{run.id}:android-01"
    await harness.wait(lambda: harness.state.repo.objective_row(oid)["status"] == "waiting_user",   # type: ignore[union-attr]
                       timeout=60, what="objetivo bloqueado pela expansão")
    await harness.ticks(3)                                                      # o laço olhou de novo e nada o desfez
    obj = harness.state.repo.objective_row(oid)                                 # type: ignore[union-attr]
    assert obj["status"] == "waiting_user" and "perfil_sobrenome" in str(obj["blocked_reason"])
    assert SOBRENOME not in str(obj["blocked_reason"])
    # Nada depois da coleta rodou: nem as etapas-modelo, nem a independente; nenhuma ficou presa em running.
    linhas = db.query("SELECT id, key, status, postcondition FROM steps WHERE objective_id=? AND plan_version=1"
                      " ORDER BY seq", (oid,))
    depois = [r for i, c in enumerate(linhas) if "items_collected" in str(c["postcondition"]) for r in linhas[i + 1:]]
    assert depois and "verificar_depois" in {r["key"] for r in depois}
    assert all(r["status"] in ("pending", "ready") for r in depois), [(r["key"], r["status"]) for r in depois]
    assert not db.query("SELECT 1 FROM steps WHERE objective_id=? AND status='running'", (oid,))
    for r in depois:
        assert db.scalar("SELECT COUNT(*) FROM attempts WHERE step_id=?", (r["id"],)) == 0
    assert db.scalar("SELECT COUNT(*) FROM plan_versions WHERE objective_id=?", (oid,)) == 1   # a expansão não gravou v2


# ------------------------------------------------------------ 7. D2: dado da persona não vai ao livro de aprendizado
async def test_resposta_por_texto_so_com_persona_data_e_recusada_sem_sinal(harness: Harness) -> None:
    from app.taskqueue.assistente import ComandoAssistido, RunSuccessorBody
    from app.taskqueue.service import RunError

    _persona(harness, "android-01", USUARIO_SEM_SOBRENOME, first_name=NOME, email=EMAIL)
    _plano(harness.ai.inner, "Digitar {perfil_sobrenome}.")
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=("needs_input", "failed", "completed"))
    assert _campos_das_perguntas(harness, run.id) == ["persona_data"]
    with pytest.raises(RunError) as erro:
        ComandoAssistido(harness.state.runs).sucessora(                           # type: ignore[union-attr]
            run.id, RunSuccessorBody(command=ALL + " O sobrenome é Qualquer.", mode="plan"))
    assert erro.value.code == "dado_da_persona_ausente" and "cadastre o dado na persona" in erro.value.message
    db = harness.state.db                                                       # type: ignore[union-attr]
    assert harness.state.repo.run_row(run.id)["status"] == "needs_input"        # type: ignore[union-attr]   # nada cancelado
    assert db.scalar("SELECT COUNT(*) FROM runs WHERE idempotency_key LIKE 'sucessora-%'") == 0
    assert db.scalar("SELECT COUNT(*) FROM learning_signals WHERE kind='respondeu_pergunta'") == 0


async def test_refinar_recusa_resposta_a_persona_data(harness: Harness) -> None:
    from app.taskqueue.assistente import CommandRefineBody, ComandoAssistido
    from app.taskqueue.service import RunError

    body = CommandRefineBody(command=ALL, answers=[{"field": "persona_data", "question": "Qual o sobrenome?",
                                                    "answer": "Qualquer"}])
    with pytest.raises(RunError) as erro:
        await ComandoAssistido(harness.state.runs).refinar(body)                  # type: ignore[union-attr]
    assert erro.value.code == "dado_da_persona_ausente"


async def test_execucao_mista_destino_mais_persona_data_mantem_o_comportamento_de_destino(harness: Harness) -> None:
    """Só `persona_data` é recusada. Com uma pergunta de destino junto, a sucessora nasce como antes e o sinal
    `respondeu_pergunta` não leva nenhum dos dois campos (nenhum é resposta de texto)."""
    from app.taskqueue.assistente import ComandoAssistido, RunSuccessorBody

    runs = harness.state.runs                                                   # type: ignore[union-attr]
    row, _ = runs.repo.create_run(RunCreate(command=ALL, instance_ids=["android-01"], idempotency_key="k-mista-31-87"),
                                  simulated=True)
    runs._pedir_resposta(row["id"], [                                           # noqa: SLF001
        {"code": "persona_no_aparelho", "question": "Qual persona?", "field": "profile_id", "options": [],
         "instance_id": "android-01", "profile_id": None},
        {"code": "dado_da_persona_ausente", "question": "Falta dado.", "field": "persona_data", "options": [],
         "instance_id": "android-01", "profile_id": None}])
    nova, criada = ComandoAssistido(runs).sucessora(row["id"], RunSuccessorBody(command=ALL, mode="plan"))
    assert criada and nova.id != row["id"]
    sinais = harness.state.db.query("SELECT data FROM learning_signals WHERE kind='respondeu_pergunta'")  # type: ignore[union-attr]
    # Como sempre foi com destino: sem campo de texto, o sinal leva o campo vazio; nenhum dos dois nomes vai ao livro.
    assert {json.loads(r["data"])["campo"] for r in sinais} == {""}


def test_o_conjunto_unico_cobre_destino_e_dado_da_persona() -> None:
    from app.taskqueue.perguntas import CAMPOS_DE_DESTINO, CAMPOS_SEM_RESPOSTA_POR_TEXTO

    assert CAMPOS_SEM_RESPOSTA_POR_TEXTO == CAMPOS_DE_DESTINO | {"persona_data"}
    assert CAMPOS_DE_DESTINO == {"profile_id", "instance_id"}                   # o destino segue como era


def test_o_livro_nao_guarda_nem_sugere_dado_da_persona(tmp_path: Any) -> None:
    """`respondeu_pergunta` com `persona_data` não vira observação de preferência; as perguntas abertas o omitem. O campo
    de uma pergunta comum segue observado (contraprova)."""
    from app.modules.learning.infrastructure.preferencias_sql import PreferenciasSql

    from .fake_skills import banco
    from .test_learning_preferencias import Mundo

    m = Mundo(banco(tmp_path))
    m.responder("persona_data")
    m.responder("message")
    campos = {o.campo for o in PreferenciasSql(m.db).respostas()}
    assert campos == {"message"}
    antiga, _ = m.responder("persona_data")
    m.db.execute("UPDATE runs SET status='needs_input' WHERE id=?", (antiga,))
    assert PreferenciasSql(m.db).perguntas_abertas(antiga).campos == ()         # type: ignore[union-attr]
    mista, _ = m.responder("message")
    m.db.execute("UPDATE runs SET status='needs_input' WHERE id=?", (mista,))
    assert PreferenciasSql(m.db).perguntas_abertas(mista).campos == ("message",)  # type: ignore[union-attr]

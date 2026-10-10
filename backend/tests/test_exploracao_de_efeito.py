"""31.297 (ADR-091): a exploração de EFEITO passa pela porta 13.2 e pela política do perfil, nunca por fora delas.

O que estes testes protegem:
* a chave do efeito tem a forma da de leitura (`explorar_<verbo>_<objeto…>`), com o verbo canônico: sinônimos dão a mesma
  chave, e é por ela (ou pela genérica `explorar_efeito`) que o dono configura a política do perfil;
* o passo de efeito é `side_effect`, manda não digitar senha e nunca vira molde oferecido a outra execução;
* a política vale em dois níveis (chave do pedido, depois a genérica; perfil antes do grupo) e a configuração só aceita as
  chaves que o dono consegue escrever;
* a porta: sem escolha do dono pede aprovação ANTES de agir; `disabled` e `manual_only` recusam; `autonomous` e o grupo
  sem aprovação liberam e AVISAM; sem perfil não passa; a leitura exploratória e o efeito livre não ganham a ação sintética;
* o planejamento só monta a etapa de efeito com `exploracao_efeito_ligada` (desligada de fábrica: a recusa de antes).

Nível de prova: `simulated` (provedor roteirizado, aparelho falso, banco de teste). `real`: `not_run`: o efeito numa conta
real depende do dono ligar o interruptor e escolher a política.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.db import loads
from app.models import ProfileCreate
from app.modules.avisos.domain.mensagem import AGORA, entrega_do_tipo, aviso_de_evento
from app.modules.avisos.infrastructure.servico import KINDS_QUE_AVISAM
from app.planning import exploracao as ex
from app.social.service import SocialError

from .apoio_politica import IG, SENHA
from .conftest import CountingProvider, Harness
from .test_exploracao_fora_do_catalogo import _fora, _plano
from .test_recusa_no_planejamento import Roteiro, _eventos, _parque

PAINEL = "http://painel.local:8000"


# ------------------------------------------------------------------ puro
@pytest.mark.parametrize(("pedido", "chave"), [
    ("enviar um e-mail", None),
    ("mandar uma mensagem", "explorar_enviar_mensagem"),
    ("encaminhar a mensagem", "explorar_enviar_mensagem"),
    ("Enviar mensagem para a Ana", "explorar_enviar_mensagem"),
    ("postar uma foto no feed", "explorar_publicar_feed"),
    ("excluir a pasta de spam", "explorar_apagar_pasta_spam"),
    ("quero apagar as mensagens da lixeira", "explorar_apagar_mensagens_lixeira"),
])
def test_sinonimos_de_efeito_dao_a_mesma_chave_canonica(pedido: str, chave: str | None) -> None:
    e = ex.classificar(pedido)
    assert e.destino is ex.Destino.EFEITO and not e.de_leitura
    if chave is not None:
        assert e.chave == chave
    assert e.chave.startswith("explorar_") and len(e.chave) <= 40 and e.chave.isascii()
    assert not any(c.isdigit() for c in e.chave)


def test_o_nome_e_o_valor_nao_entram_na_chave_do_efeito() -> None:
    e = ex.classificar("enviar para joao.silva123 o boleto 4455 por e-mail")
    assert e.chave == "explorar_enviar_mail" and ex.chaves_da_politica(e.chave) == ("explorar_enviar_mail", "explorar_efeito")
    sem_objeto = ex.classificar("enviar para o zunzum")
    assert sem_objeto.chave.startswith("explorar_enviar_") and sem_objeto.chave.isalpha() is False
    assert not ex.chave_de_politica_valida(sem_objeto.chave)           # o sufixo de letras o dono não sabe: para isso há a genérica


@pytest.mark.parametrize(("chave", "valida"), [
    ("explorar_efeito", True), ("explorar_enviar_email", True), ("explorar_apagar", True),
    ("explorar_publicar_feed", True), ("explorar_apagar_mensagens_caixa_lixo", True),
    ("explorar_ver_caixa_lixo", False),            # leitura não tem política: é livre
    ("explorar_enviar_zumbido", False),            # objeto fora do vocabulário
    ("explorar_enviar_abcdef", False), ("LIKE_POST", False), ("explorar_", False), ("enviar_email", False),
    ("explorar_enviar_email_email_email_email", False),
])
def test_so_a_chave_que_o_dono_escreve_vale_como_politica(chave: str, valida: bool) -> None:
    assert ex.chave_de_politica_valida(chave) is valida


def test_a_leitura_so_tem_a_propria_chave_de_politica() -> None:
    assert ex.chaves_da_politica("explorar_ver_caixa_lixo") == ("explorar_ver_caixa_lixo",)
    assert ex.chaves_da_politica("LIKE_POST") == ("LIKE_POST",)


def test_o_passo_de_efeito_manda_nao_digitar_senha_e_nunca_vira_molde() -> None:
    e = ex.classificar("enviar um e-mail para ana@x.com")
    passo = ex.passo_da_exploracao("enviar um e-mail para ana@x.com", e, app_id=None, nome_do_app="Microsoft Outlook")
    assert passo.exploratoria and passo.side_effect and passo.max_attempts == 1
    assert "ana@x.com" in passo.goal and "não digite senha" in passo.goal and "SÓ o que o pedido manda" in passo.goal
    assert ex.molde_da_exploracao(passo) is None                    # o efeito descoberto não é oferecido a outra execução
    assert ex.e_exploracao_de_efeito(passo.key, passo.exploratoria, passo.side_effect)
    # o plano que o modelo escreveu não ganha a ação sintética só por trazer o campo
    assert not ex.e_exploracao_de_efeito("enviar", True, True) and not ex.e_exploracao_de_efeito(passo.key, False, True)
    assert not ex.e_exploracao_de_efeito(passo.key, True, False)


def test_a_acao_sintetica_pede_aprovacao_por_padrao_e_e_de_risco_alto() -> None:
    cap = ex.capability_da_exploracao("explorar_enviar_email")
    assert cap.side_effect and cap.risk == "high" and cap.default_policy == "approval_required"
    assert not cap.needs_draft and cap.limit_bucket is None and cap.counterparty is None


def test_o_aviso_vai_na_hora_e_leva_so_ids_e_codigos() -> None:
    assert "exploracao.efeito_liberado" in KINDS_QUE_AVISAM and entrega_do_tipo("exploracao.efeito_liberado") == AGORA
    run = "r-20261010160000-abc123"
    base = {"run_id": run, "chave": "explorar_enviar_email", "politica": "autonomous", "origem": "own", "aprovada": False}
    sozinho = aviso_de_evento("exploracao.efeito_liberado", base, 3, PAINEL)
    assert sozinho is not None and sozinho.titulo.endswith("⚠️ Exploração com efeito liberada")
    assert "sem pedir o sim" in sozinho.corpo and "a política do perfil" in sozinho.corpo
    assert sozinho.link == f"{PAINEL}/#/execucoes/{run}" and "explorar_efeito" in sozinho.corpo
    sim = aviso_de_evento("exploracao.efeito_liberado", {**base, "aprovada": True, "origem": "default"}, 3, PAINEL)
    assert sim is not None and "Você aprovou" in sim.corpo
    grupo = aviso_de_evento("exploracao.efeito_liberado", {**base, "dispensada_pelo_grupo": True}, 3, PAINEL)
    assert grupo is not None and "grupo sem aprovação" in grupo.corpo
    # id ruim ou chave que não é identificador: nada sai (nunca o texto livre do pedido)
    assert aviso_de_evento("exploracao.efeito_liberado", {**base, "run_id": "r-1"}, 3, PAINEL) is None
    assert aviso_de_evento("exploracao.efeito_liberado", {**base, "chave": "enviar para Ana: oi"}, 3, PAINEL) is None


def test_o_aviso_de_inicio_e_de_fim_dizem_que_ha_efeito() -> None:
    run = "r-20261010160000-abc123"
    ini = aviso_de_evento("exploracao.iniciada", {"run_id": run, "app_ids": ["outlook"], "com_efeito": True,
                                                 "tetos": {"acoes": 25, "chamadas_ia": 30, "usd": 0.6}}, 3, PAINEL)
    assert ini is not None and "COM efeito" in ini.corpo and "Só leitura" not in ini.corpo
    fim = aviso_de_evento("exploracao.concluida", {"run_id": run, "resultado": "concluida", "com_efeito": True,
                                                  "custo_usd": 0.02, "chamadas": 3}, 3, PAINEL)
    assert fim is not None and "Houve etapa com efeito" in fim.corpo and "Só leitura" not in fim.corpo
    leitura = aviso_de_evento("exploracao.concluida", {"run_id": run, "resultado": "concluida", "custo_usd": 0.02,
                                                      "chamadas": 3}, 3, PAINEL)
    assert leitura is not None and "Só leitura e navegação: nada foi alterado" in leitura.corpo


# As frases que o modelo pode devolver além do infinitivo (achado da revisão da Jev): imperativo, substantivo, inglês, a senha em si.
CREDENCIAL = ["entrar na conta", "fazer logout: sair da conta", "enviar e entrar na conta", "cadastrar uma conta nova",
              "registrar um usuário", "logar com a senha", "fazer login no app", "entre na conta", "fazer logout",
              "redefinir a senha", "cadastre-se no app", "autentique-se", "desconectar a conta", "inscrever-se", "login",
              "digitar a senha", "alterar a senha", "criar uma conta nova", "adicionar conta", "abrir a conta",
              "ver o código de verificação", "copiar o token"]
LEITURA_PARECIDA = ["ver a caixa de entrada", "abrir as configurações da conta", "ver o perfil da conta", "listar as contas do app"]


@pytest.mark.parametrize("pedido", CREDENCIAL)
def test_credencial_e_sessao_nunca_exploram_nem_com_o_efeito_ligado(pedido: str) -> None:
    e = ex.classificar(pedido)
    assert e.destino is ex.Destino.EFEITO and e.de_credencial, pedido


@pytest.mark.parametrize("pedido", LEITURA_PARECIDA + ["enviar uma mensagem", "apagar a pasta de spam"])
def test_o_que_nao_e_credencial_continua_como_antes(pedido: str) -> None:
    assert not ex.classificar(pedido).de_credencial, pedido
    assert (ex.classificar(pedido).destino is ex.Destino.EXPLORAR) == (pedido in LEITURA_PARECIDA)


def test_verbo_de_credencial_nao_tem_chave_de_politica() -> None:
    for chave in ("explorar_entrar_conta", "explorar_sair_conta", "explorar_cadastrar_conta", "explorar_login_conta"):
        assert not ex.chave_de_politica_valida(chave)
    assert ex.chaves_da_politica("explorar_sair_conta") == ("explorar_sair_conta",)


def test_a_chave_do_efeito_nunca_e_cortada_no_meio_de_uma_palavra() -> None:
    e = ex.classificar("apagar " + " ".join(sorted(ex.OBJETOS)))
    assert len(e.chave) <= 40 and all(p in ex.VOCABULARIO or p in ("explorar", "apagar") for p in e.chave.split("_"))
    assert ex.chave_de_politica_valida(e.chave)


# ------------------------------------------------------------------ política e porta (estado do harness)
OID = "run-x:android-01"


def _semear(state: Any, *, chave: str = "explorar_enviar_mensagem", exploratoria: bool = True, efeito: bool = True,
            com_perfil: bool = True, politica: dict[str, Any] | None = None, run_id: str = "run-x") -> str | None:
    """Uma execução em Instagram com UMA etapa exploratória (`chave`) pronta, e o perfil com a `politica`."""
    db = state.db
    if not db.scalar("SELECT 1 FROM apps WHERE id='ig'"):
        db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id='android-01'")
    pid = None
    if com_perfil:
        existente = state.social_repo.profile_by_username("tadeu.quintela4821")
        pid = existente["id"] if existente else state.social.create_profile(
            ProfileCreate(username="tadeu.quintela4821", password=SENHA, instance_id="android-01")).id
        state.social_repo.update_profile(pid, {"automation_policy": json.dumps(politica or {})})
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at, plan,"
               " pause_requested) VALUES (?,?,'x','execute','running',1,'[\"android-01\"]','2026-10-10T16:00:00Z',NULL,1)",
               (run_id, f"k-{run_id}"))
    oid = f"{run_id}:android-01"
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
               " VALUES (?,?,'android-01','running',1,'{}',?)", (oid, run_id, pid))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, bindings, exploratoria)"
        " VALUES (?,?,?,'android-01',1,1,?,'Explorar com efeito','enviar uma mensagem','[]',?,'[]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',300,1,'ready',NULL,'{}',?)",
        (f"{oid}:v1:{chave}", run_id, oid, chave, int(efeito), 1 if exploratoria else None))
    return pid


def _passo_id(chave: str = "explorar_enviar_mensagem") -> str:
    return f"{OID}:v1:{chave}"


async def _porta(state: Any, chave: str = "explorar_enviar_mensagem") -> Any:
    return await state._policy_gate(state.repo.objective_row(OID), state.repo.step_row(_passo_id(chave)),
                                    state.repo.run_row("run-x"))


def _avisos(state: Any) -> list[dict[str, Any]]:
    return [json.loads(r["data"]) for r in state.db.query(
        "SELECT data FROM events WHERE kind='exploracao.efeito_liberado' ORDER BY id")]


async def test_sem_escolha_do_dono_a_exploracao_de_efeito_pede_aprovacao_antes_de_agir(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    _semear(state)
    veredito = await _porta(state)
    assert veredito is not None and not veredito.allowed and veredito.policy == "approval_required"
    assert "aprovação" in veredito.reason
    assert len(state.approval_service.list()) == 1
    assert _avisos(state) == []                                      # ainda não liberou: nada de aviso
    # o dono aprova: a porta libera e avisa, dizendo que foi ele
    state.scheduler._hold(state.repo.objective_row(OID), state.repo.step_row(_passo_id()), veredito)  # noqa: SLF001
    state.approval_service.decide(state.approval_service.list()[0]["id"], "approve")
    assert await _porta(state) is None
    (aviso,) = _avisos(state)
    assert aviso["chave"] == "explorar_enviar_mensagem" and aviso["aprovada"] is True and aviso["origem"] == "default"
    assert aviso["step_id"] == _passo_id() and aviso["run_id"] == "run-x" and "pedido" not in json.dumps(aviso)


@pytest.mark.parametrize(("politica", "libera", "origem"), [
    ({"capabilities": {"explorar_efeito": "autonomous"}}, True, "own"),                  # a genérica
    ({"capabilities": {"explorar_enviar_mensagem": "autonomous"}}, True, "own"),         # a do pedido
    # a do pedido vence a genérica nos dois sentidos
    ({"capabilities": {"explorar_efeito": "autonomous", "explorar_enviar_mensagem": "manual_only"}}, False, "own"),
    ({"capabilities": {"explorar_efeito": "disabled"}}, False, "own"),
    ({"capabilities": {"explorar_efeito": "manual_only"}}, False, "own"),
    ({"capabilities": {"explorar_efeito": "manual_only", "explorar_enviar_mensagem": "autonomous"}}, True, "own"),
])
async def test_a_politica_do_perfil_decide_com_a_chave_do_pedido_antes_da_generica(
        harness: Harness, politica: dict[str, Any], libera: bool, origem: str) -> None:
    state = harness.state
    assert state is not None
    _semear(state, politica=politica)
    veredito = await _porta(state)
    if libera:
        assert veredito is None
        (aviso,) = _avisos(state)
        assert aviso["politica"] == "autonomous" and aviso["origem"] == origem and aviso["aprovada"] is False
    else:
        assert veredito is not None and not veredito.allowed and veredito.policy in ("disabled", "manual_only")
        assert _avisos(state) == []                                  # recusado: nada foi liberado, nada é avisado


async def test_o_grupo_sem_aprovacao_dispensa_o_sim_e_o_aviso_diz_isso(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    pid = _semear(state)
    state.db.execute("INSERT INTO policy_groups(id, name, capabilities, limits, created_at, updated_at)"
                     " VALUES ('liberado','Liberado','{}','{}','2026-10-10T16:00:00Z','2026-10-10T16:00:00Z')")
    state.db.execute("UPDATE instagram_profiles SET policy_group_id='liberado' WHERE id=?", (pid,))
    state.scheduler.get_settings().grupo_sem_aprovacao = "liberado"
    assert await _porta(state) is None
    (aviso,) = _avisos(state)
    assert aviso["dispensada_pelo_grupo"] is True and aviso["aprovada"] is False and aviso["politica"] == "autonomous"
    assert state.approval_service.list() == []


async def test_a_politica_do_grupo_vale_e_o_perfil_a_sobrepoe(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    pid = _semear(state, politica={"capabilities": {"explorar_efeito": "manual_only"}})
    state.db.execute("INSERT INTO policy_groups(id, name, capabilities, limits, created_at, updated_at)"
                     " VALUES ('g1','G1',?,'{}','2026-10-10T16:00:00Z','2026-10-10T16:00:00Z')",
                     (json.dumps({"explorar_efeito": "autonomous"}),))
    state.db.execute("UPDATE instagram_profiles SET policy_group_id='g1' WHERE id=?", (pid,))
    veredito = await _porta(state)
    assert veredito is not None and veredito.policy == "manual_only"        # o do perfil vence o do grupo
    state.social_repo.update_profile(pid, {"automation_policy": "{}"})
    assert await _porta(state) is None                                        # sem a do perfil, vale a do grupo
    assert _avisos(state)[-1]["origem"] == "group"


async def test_sem_perfil_a_exploracao_de_efeito_nao_passa(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    _semear(state, com_perfil=False)
    veredito = await _porta(state)
    assert veredito is not None and not veredito.allowed and "perfil" in veredito.reason
    assert _avisos(state) == []


async def test_o_teto_preparar_exige_o_sim_mesmo_com_a_politica_autonoma(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    _semear(state, politica={"capabilities": {"explorar_efeito": "autonomous"}})
    state.db.execute("UPDATE runs SET teto_de_autonomia='preparar' WHERE id='run-x'")
    veredito = await _porta(state)
    assert veredito is not None and not veredito.allowed and veredito.policy == "approval_required"   # parou na aprovação
    assert state.approval_service.list() and _avisos(state) == []


async def test_a_leitura_exploratoria_e_o_efeito_livre_nao_ganham_a_acao_sintetica(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    _semear(state, chave="explorar_ver_caixa_lixo", efeito=False)
    assert await _porta(state, "explorar_ver_caixa_lixo") is None                # leitura: livre, como sempre
    assert _avisos(state) == [] and state.approval_service.list() == []
    # efeito de um plano do modelo (sem o campo `exploratoria`): segue recusado pela 13.2 num app com catálogo
    _semear(state, chave="explorar_enviar_mensagem", exploratoria=False, run_id="run-y")
    recusa = await state._policy_gate(state.repo.objective_row("run-y:android-01"),
                                      state.repo.step_row("run-y:android-01:v1:explorar_enviar_mensagem"),
                                      state.repo.run_row("run-y"))
    assert recusa is not None and recusa.policy == "manual_only" and "sem a ação do catálogo" in recusa.reason
    # a chave que o sistema não montou (sem o prefixo `explorar_`), mesmo marcada exploratória: também recusada
    _semear(state, chave="enviar", exploratoria=True, run_id="run-z")
    recusa = await state._policy_gate(state.repo.objective_row("run-z:android-01"),
                                      state.repo.step_row("run-z:android-01:v1:enviar"), state.repo.run_row("run-z"))
    assert recusa is not None and recusa.policy == "manual_only"


async def test_a_politica_so_aceita_as_chaves_que_o_dono_escreve(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    pid = _semear(state)
    assert pid is not None

    class _Corpo:
        def __init__(self, caps: dict[str, Any]) -> None:
            self.capabilities, self.limits = caps, None

    dto = state.social.set_policy(pid, _Corpo({"explorar_efeito": "autonomous", "explorar_enviar_email": "manual_only"}))
    assert dto.capabilities["explorar_efeito"] == "autonomous" and dto.capabilities["explorar_enviar_email"] == "manual_only"
    assert dto.own["explorar_efeito"] == "autonomous" and dto.origin["explorar_efeito"] == "own"
    assert dto.defaults["explorar_efeito"] == "approval_required"
    for ruim in ("explorar_ver_caixa_lixo", "explorar_enviar_abcdef", "NAO_EXISTE"):
        with pytest.raises(SocialError) as erro:
            state.social.set_policy(pid, _Corpo({ruim: "autonomous"}))
        assert erro.value.code == "unknown_capability"
    # afrouxar abaixo do padrão uma ação de risco alto continua aceito, mas nunca calado
    avisos = state.db.query("SELECT message FROM events WHERE message LIKE '%explorar_efeito%afrouxado%'")
    assert len(list(avisos)) == 1
    # apagar (None) volta a herdar: some do perfil e a porta pede aprovação de novo
    state.social.set_policy(pid, _Corpo({"explorar_efeito": None, "explorar_enviar_email": None}))
    assert "explorar_efeito" not in loads(state.social_repo.profile_row(pid)["automation_policy"], {}).get("capabilities", {})
    veredito = await _porta(state)
    assert veredito is not None and veredito.policy == "approval_required"


# ------------------------------------------------------------------ o planejamento (Outlook)
async def _planejar(tmp_path: Any, pedido: str, *, ligada: bool) -> tuple[dict[str, Any], Any, Harness]:
    h = Harness(tmp_path, 1)
    h.ai = CountingProvider(Roteiro(_plano(pedido)))
    state = await _parque(h, app_do_aparelho="outlook")
    setattr(state.scheduler.get_settings(), "exploracao_efeito_ligada", ligada)
    run = h.run(["android-01"], mode="plan", command="faça isso no Outlook")
    await h.wait_run(run.id, ("needs_input", "failed", "planned", "running", "completed", "completed_with_issues"))
    return dict(state.repo.run_row(run.id)), state, h


async def test_desligada_de_fabrica_o_pedido_de_efeito_segue_recusado(tmp_path: Any) -> None:
    assert Harness(tmp_path, 1).cfg.file.limits.exploracao_efeito_ligada is False
    linha, state, _ = await _planejar(tmp_path, "enviar um e-mail para a Ana", ligada=False)
    try:
        assert linha["status"] == "failed"
        (recusa,) = _eventos(state, linha["id"], "plan.refused")
        assert recusa["data"]["motivo"] == "sem_acao_do_catalogo"
        assert _eventos(state, linha["id"], "exploracao.iniciada") == []
    finally:
        await state.stop()


async def test_ligada_o_pedido_de_efeito_vira_etapa_exploratoria_com_efeito_para_a_porta(tmp_path: Any) -> None:
    linha, state, h = await _planejar(tmp_path, "enviar um e-mail para a Ana", ligada=True)
    try:
        assert linha["status"] == "planned", linha["status_detail"]
        (passo,) = json.loads(linha["plan"])["steps"]
        assert passo["key"] == "explorar_enviar_mail" and passo["exploratoria"] is True and passo["side_effect"] is True
        assert "não digite senha" in passo["goal"]
        assert _eventos(state, linha["id"], "plan.refused") == []        # a 13.2 do planejamento não a recusa: a porta decide
        (evento,) = _eventos(state, linha["id"], "exploracao.iniciada")
        assert evento["data"]["com_efeito"] is True and evento["data"]["etapas"] == ["explorar_enviar_mail"]
        assert h.ai.count("decide") == 0                                  # nada foi tocado: só planejado
        assert state.db.scalar("SELECT side_effect FROM steps WHERE run_id=? AND key='explorar_enviar_mail'",
                               (linha["id"],)) == 1
    finally:
        await state.stop()


async def test_ligada_o_efeito_livre_de_um_plano_do_modelo_continua_recusado(tmp_path: Any) -> None:
    """O interruptor liga a EXPLORAÇÃO de efeito, não o efeito sem ação do catálogo: uma etapa `side_effect` que o modelo
    escreveu sem a marca do sistema segue recusada no planejamento (RA-7)."""
    from app.models import Plan, PlannerInfo, PlanStep, Postcondition

    h = Harness(tmp_path, 1)
    livre = Plan(summary="s", app_id="outlook", planner=PlannerInfo(provider="roteiro", model="t", simulated=True),
                 steps=[PlanStep(key="mandar_email", title="Mandar e-mail", goal="mandar", side_effect=True,
                                 postcondition=Postcondition(kind="model_judged", value="x", description="y"))])
    h.ai = CountingProvider(Roteiro(livre))
    state = await _parque(h, app_do_aparelho="outlook")
    setattr(state.scheduler.get_settings(), "exploracao_efeito_ligada", True)
    try:
        run = h.run(["android-01"], mode="plan", command="mande um e-mail no Outlook")
        await h.wait_run(run.id, ("needs_input", "failed", "planned"))
        (recusa,) = _eventos(state, run.id, "plan.refused")
        assert recusa["data"]["motivo"] == "efeito_fora_do_catalogo"
    finally:
        await state.stop()


@pytest.mark.parametrize("pedido", ["entrar na conta do Outlook", "fazer login no Outlook", "redefinir a senha do Outlook"])
async def test_ligada_e_liberada_a_credencial_ainda_e_recusada(tmp_path: Any, pedido: str) -> None:
    """O pior caso do ADR-040: interruptor ligado e o pedido de entrar na conta. A recusa de antes, sem etapa nenhuma."""
    linha, state, _ = await _planejar(tmp_path, pedido, ligada=True)
    try:
        assert linha["status"] == "failed"
        (recusa,) = _eventos(state, linha["id"], "plan.refused")
        assert recusa["data"]["motivo"] == "sem_acao_do_catalogo"
        assert _eventos(state, linha["id"], "exploracao.iniciada") == []
        assert state.db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=?", (linha["id"],)) == 0
    finally:
        await state.stop()

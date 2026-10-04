"""LT-4b (latência do planejador): o formato CURTO da etapa livre, atrás de `ai.esquema_do_plano: curto`.

O que se prova:
- o esquema curto não pede `postcondition.description`, `precondition` nem `max_attempts` (plano livre e etapa livre
  do plano entre apps), e o longo segue pedindo tudo;
- o backend preenche o que o modelo não escreveu: a descrição vem do `value` (no `model_judged`, o próprio
  critério), nenhuma pré-condição, 1 tentativa no efeito e 3 nas demais; o prazo continua do modelo, com os limites
  de sempre;
- a identidade da receita (`step_template_hash`) é a mesma nos dois formatos;
- os prompts curtos são os de sempre com dois trechos trocados (e a regra de textos curtos), e os de sempre não mudam;
- os provedores Anthropic e OpenAI (sem rede) mandam o sistema e o esquema curtos só com a chave, e o padrão é o
  longo.

Nível de prova: `simulated` (cliente falso; nenhuma chamada de IA). O ganho de tempo é estimado por count_tokens em
planos reais (handoff da Jev, 03/10); o A/B pago e o sucesso na rodada QA: `not_run`.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
import pytest

from app.config import ModelCaps, ProviderCfg, RoleCfg
from app.models import Postcondition
from app.planning import prompts
from app.planning.anthropic_provider import AnthropicProvider, strict_schema
from app.planning.capabilities import CapabilityCatalog, load_catalog
from app.planning.openai_provider import OpenAICompatProvider
from app.planning.parsing import (PISO_DA_ETAPA_COM_IA_S, TENTATIVAS_DO_FORMATO_CURTO, _MultiPlanCurtoOut,
                                  _MultiPlanOut, _PlanCurtoOut, _PlanOut, catalog_plan_from_json, plan_from_json)
from app.planning.provider import AIError, AppContext, PlanRequest
from app.taskqueue.recipes import step_template_hash

from .conftest import make_config

QA_APP = AppContext("qa-messenger", "QA Messenger", "com.pocqa.messenger", ".MainActivity", None, None)
INSTAGRAM = AppContext("instagram", "Instagram", "com.instagram.android", None, None, None)
NO_QA = [{"instance_id": "android-05", "account_label": "qa-user-05", "app_id": "qa-messenger"}]
NO_IG = [{"instance_id": "android-05", "account_label": "qa-user-05", "app_id": "instagram"}]


def _pos(kind: str, value: str, nivel: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "value": value, "required_delivery_level": nivel}


def _livre_curta(titulo: str, pos: dict[str, Any], *, efeito: bool = False, guarda: list[str] | None = None,
                 prazo: int = 60, saidas: list[str] | None = None) -> dict[str, Any]:
    return {"title": titulo, "goal": f"{titulo}.", "side_effect": efeito, "commit_guard": guarda or [],
            "postcondition": pos, "timeout_s": prazo, "saidas": saidas or []}


def _etapa_curta(key: str, titulo: str, pos: dict[str, Any], **kw: Any) -> dict[str, Any]:
    livre = _livre_curta(titulo, pos, **kw)
    return {"key": key, "title": livre["title"], "goal": livre["goal"], "depends_on": [],
            "side_effect": livre["side_effect"], "commit_guard": livre["commit_guard"], "postcondition": pos,
            "timeout_s": livre["timeout_s"], "for_each": None, "app_id": None, "saidas": livre["saidas"]}


def _plano_livre_curto() -> dict[str, Any]:
    return {"summary": "Enviar mensagem", "app_id": "qa-messenger",
            "parameters": [{"name": "recipient", "value": "QA-001"}],
            "success_criteria": ["Mensagem entregue"], "missing": [], "steps": [
                _etapa_curta("open_app", "Abrir o app", _pos("app_foreground", "com.pocqa.messenger"), prazo=5),
                _etapa_curta("open_conversation", "Abrir a conversa",
                             _pos("element_present", "id=chat_title|text={recipient}")),
                _etapa_curta("fill_message", "Preencher a mensagem", _pos("text_visible", "Bom dia {run_id}"),
                             saidas=["Rascunho"]),
                _etapa_curta("send_message", "Enviar",
                             _pos("model_judged", "o balão novo mostra o texto enviado", "sent"),
                             efeito=True, guarda=["QA-001", "Bom dia {run_id}"], prazo=900),
                _etapa_curta("list_contacts", "Ler os contatos", _pos("items_collected", "nome de cada contato"))]}


def _montar_livre(dados: dict[str, Any], *, curto: bool = True) -> Any:
    req = PlanRequest(command="No QA Messenger, envie Bom dia para QA-001", run_id="r", instances=NO_QA, apps=[QA_APP])
    return plan_from_json(json.dumps(dados), req, provider="p", model="m", max_steps=12, curto=curto)


def _catalogo_ig() -> CapabilityCatalog:
    catalogo = load_catalog("com.instagram.android")
    assert catalogo is not None
    return catalogo


def _plano_entre_apps_curto() -> str:
    return json.dumps({"summary": "entre apps", "app_id": "qa-messenger", "parameters": [], "success_criteria": ["ok"],
                       "missing": [], "steps": [
                           {"key": "open_app", "app_id": "qa-messenger", "capability": None, "bindings": [],
                            "livre": _livre_curta("Abrir o app", _pos("app_foreground", "com.pocqa.messenger")),
                            "depends_on": [], "for_each": None, "saidas": []},
                           {"key": "send", "app_id": "qa-messenger", "capability": None, "bindings": [],
                            "livre": _livre_curta("Enviar", _pos("model_judged", "o balão novo aparece"), efeito=True,
                                                  guarda=["QA-001"]),
                            "depends_on": ["open_app"], "for_each": None, "saidas": []},
                           {"key": "perfil", "app_id": "instagram", "capability": "OPEN_PROFILE",
                            "bindings": [{"name": "username", "value": "@ana"}], "livre": None, "depends_on": [],
                            "for_each": None, "saidas": []}]})


def _pedido_entre_apps() -> PlanRequest:
    return PlanRequest(command="No QA Messenger envie oi e no Instagram abra o perfil de @ana", run_id="r",
                       instances=NO_IG, apps=[QA_APP, INSTAGRAM], catalogs={"instagram": _catalogo_ig()})


def _propriedades(esquema: dict[str, Any], *caminho: str) -> dict[str, Any]:
    """Desce no esquema estrito pelos nomes de propriedade, atravessando lista (`items`) e anulável (`anyOf`)."""
    no = esquema
    for nome in caminho:
        no = no["properties"][nome]
        if "anyOf" in no:
            no = next(o for o in no["anyOf"] if o.get("type") != "null")
        if no.get("type") == "array":
            no = no["items"]
    return no


# ================================================================== esquema
def test_formato_curto_nao_pede_o_que_o_backend_preenche() -> None:
    curto_livre = _propriedades(strict_schema(_PlanCurtoOut), "steps")
    curto_entre = _propriedades(strict_schema(_MultiPlanCurtoOut), "steps", "livre")
    for etapa in (curto_livre, curto_entre):
        assert {"precondition", "max_attempts"}.isdisjoint(etapa["properties"])
        assert "timeout_s" in etapa["required"] and "commit_guard" in etapa["required"]
        assert set(etapa["properties"]["postcondition"]["required"]) == {"kind", "value", "required_delivery_level"}
    # o longo segue pedindo tudo (o formato de sempre, byte a byte)
    for etapa in (_propriedades(strict_schema(_PlanOut), "steps"),
                  _propriedades(strict_schema(_MultiPlanOut), "steps", "livre")):
        assert {"precondition", "max_attempts", "timeout_s"} <= set(etapa["required"])
        assert "description" in etapa["properties"]["postcondition"]["required"]


# ================================================================== o backend preenche
def test_plano_livre_curto_recebe_descricao_precondicao_e_tentativas_do_backend() -> None:
    plano = _montar_livre(_plano_livre_curto())
    assert not plano.missing
    por_chave = {s.key: s for s in plano.steps}
    assert {k: s.postcondition.description for k, s in por_chave.items()} == {
        "open_app": "O app com.pocqa.messenger está em primeiro plano.",
        "open_conversation": "A tela mostra o elemento id=chat_title|text={recipient}.",
        "fill_message": 'O texto "Bom dia {run_id}" está visível na tela.',
        # no model_judged, o verificador lê o próprio critério que o planejador escreveu para ele
        "send_message": "o balão novo mostra o texto enviado",
        "list_contacts": "A lista está visível e foi lida até o fim (item: nome de cada contato)."}
    assert all(s.precondition is None for s in plano.steps)
    assert {k: s.max_attempts for k, s in por_chave.items()} == {
        "open_app": TENTATIVAS_DO_FORMATO_CURTO, "open_conversation": 3, "fill_message": 3, "send_message": 1,
        "list_contacts": 3}
    # o prazo segue do modelo, com os limites do backend: piso de 120 s na etapa com IA (29.75; era 30) e teto de 600
    assert (por_chave["open_app"].timeout_s, por_chave["send_message"].timeout_s,
            por_chave["open_conversation"].timeout_s) == (PISO_DA_ETAPA_COM_IA_S, 600, PISO_DA_ETAPA_COM_IA_S)
    enviar = por_chave["send_message"]
    assert enviar.side_effect and enviar.commit_guard == ["QA-001", "Bom dia {run_id}"]
    nivel = enviar.postcondition.required_delivery_level
    assert nivel is not None and nivel.value == "sent"
    assert por_chave["fill_message"].saidas == ["rascunho"]


def test_plano_entre_apps_curto_monta_a_etapa_livre_e_a_do_catalogo() -> None:
    plano = catalog_plan_from_json(_plano_entre_apps_curto(), _pedido_entre_apps(), provider="p", model="m",
                                   max_steps=12, curto=True)
    assert not plano.missing
    abrir, enviar, perfil = plano.steps
    assert abrir.postcondition.description == "O app com.pocqa.messenger está em primeiro plano."
    assert (enviar.max_attempts, enviar.precondition, enviar.postcondition.description) == (
        1, None, "o balão novo aparece")
    # a etapa de catálogo é montada pelo catálogo, como sempre: idêntica à do formato longo
    assert perfil.capability == "OPEN_PROFILE" and perfil.app_id == "instagram"
    longo = json.loads(_plano_entre_apps_curto())
    for etapa in longo["steps"]:
        if etapa["livre"] is not None:
            etapa["livre"] = {**etapa["livre"], "precondition": None, "max_attempts": 2,
                              "postcondition": {**etapa["livre"]["postcondition"], "description": "d"}}
    como_longo = catalog_plan_from_json(json.dumps(longo), _pedido_entre_apps(), provider="p", model="m", max_steps=12)
    assert como_longo.steps[2].model_dump() == perfil.model_dump()


def test_chave_e_formato_andam_juntos() -> None:
    """O formato curto lido como longo falta `description`: é saída inválida, nunca etapa sem descrição. O longo
    lido como curto perde só o que o backend preenche (o pydantic ignora campo a mais)."""
    with pytest.raises(AIError) as erro:
        _montar_livre(_plano_livre_curto(), curto=False)
    assert erro.value.kind == "invalid_output"
    longo = _plano_livre_curto()
    for etapa in longo["steps"]:
        etapa["precondition"] = "o app está aberto"
        etapa["max_attempts"] = 5
        etapa["postcondition"] = {**etapa["postcondition"], "description": "texto do modelo"}
    lido = _montar_livre(longo, curto=True)
    assert all(s.precondition is None and s.postcondition.description != "texto do modelo" for s in lido.steps)
    como_longo = _montar_livre(longo, curto=False)
    assert all(s.precondition == "o app está aberto" for s in como_longo.steps)


def test_identidade_da_receita_e_a_mesma_nos_dois_formatos() -> None:
    """`step_template_hash` (chave, efeito, tipo e valor da pós-condição, nível, guardas) não lê descrição,
    pré-condição nem tentativas: uma receita aprendida no formato longo serve à etapa planejada no curto."""
    curto = _montar_livre(_plano_livre_curto())
    longo = _plano_livre_curto()
    for etapa in longo["steps"]:
        etapa.update(precondition="pré", max_attempts=2)
        etapa["postcondition"] = {**etapa["postcondition"], "description": "outra descrição"}
    longo_montado = _montar_livre(longo, curto=False)
    assert [step_template_hash(s) for s in curto.steps] == [step_template_hash(s) for s in longo_montado.steps]
    assert isinstance(curto.steps[0].postcondition, Postcondition)


# ================================================================== prompts
def test_prompts_curtos_sao_os_de_sempre_com_dois_trechos_trocados() -> None:
    regra = prompts._REGRA_DE_TEXTOS_CURTOS  # noqa: SLF001
    for curto in (prompts.PLANNER_SYSTEM_CURTO, prompts.PLANNER_MULTIAPP_SYSTEM_CURTO):
        assert "max_attempts" not in curto and "precondition" not in curto
        assert curto.count(regra) == 1 and "A pós-condição NÃO encurta" in curto
    assert (prompts.PLANNER_SYSTEM_CURTO.replace(regra, "")
            == prompts.PLANNER_SYSTEM.replace(" max_attempts dessa etapa = 1.", ""))
    # a etapa livre do entre apps traz as regras do livre CURTO (uma regra, um texto)
    trecho = prompts._trecho(prompts.PLANNER_SYSTEM_CURTO, "- Etapas são OBJETIVOS",  # noqa: SLF001
                             "- Se o comando envolver MAIS DE UM app")
    assert trecho in prompts.PLANNER_MULTIAPP_SYSTEM_CURTO
    assert "side_effect, commit_guard, timeout_s)" in prompts.PLANNER_MULTIAPP_SYSTEM_CURTO
    # os de sempre não mudam
    assert "max_attempts dessa etapa = 1." in prompts.PLANNER_SYSTEM and regra not in prompts.PLANNER_SYSTEM
    assert "commit_guard, precondition, timeout_s, max_attempts)" in prompts.PLANNER_MULTIAPP_SYSTEM


def test_troca_de_trecho_falha_na_importacao_se_o_marcador_sumir_ou_repetir() -> None:
    with pytest.raises(ValueError, match="ausente ou repetido"):
        prompts._trocar("abc", "x", "y")  # noqa: SLF001
    with pytest.raises(ValueError, match="ausente ou repetido"):
        prompts._trocar("x e x", "x", "y")  # noqa: SLF001
    assert prompts._trocar("a x b", "x", "y") == "a y b"  # noqa: SLF001


# ================================================================== provedores, sem rede
def _anthropic_falso(cfg: Any, saida: str) -> tuple[AnthropicProvider, list[dict[str, Any]]]:
    p = AnthropicProvider(cfg)
    chamadas: list[dict[str, Any]] = []

    async def criar(**kwargs: Any) -> Any:
        chamadas.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=saida)], stop_reason="end_turn",
                               model="claude-opus-5",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0))
    falso = SimpleNamespace(create=criar)
    p.configured = True
    p._client = SimpleNamespace(messages=falso, beta=SimpleNamespace(messages=falso))  # noqa: SLF001
    return p, chamadas


async def test_anthropic_manda_o_formato_curto_so_com_a_chave(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.env.ai_provider = "anthropic"
    assert cfg.file.ai.esquema_do_plano == "longo"            # de fábrica, o formato de sempre
    cfg.file.ai.esquema_do_plano = "curto"
    livre_req = PlanRequest(command="No QA Messenger, envie Bom dia para QA-001", run_id="r", instances=NO_QA,
                            apps=[QA_APP])
    p, chamadas = _anthropic_falso(cfg, json.dumps(_plano_livre_curto()))
    plano, _ = await p.plan(livre_req)
    assert chamadas[0]["system"][0]["text"] == prompts.PLANNER_SYSTEM_CURTO
    etapa = _propriedades(chamadas[0]["output_config"]["format"]["schema"], "steps")
    assert "max_attempts" not in etapa["properties"] and "precondition" not in etapa["properties"]
    assert plano.steps[0].postcondition.description == "O app com.pocqa.messenger está em primeiro plano."

    p, chamadas = _anthropic_falso(cfg, _plano_entre_apps_curto())
    plano, _ = await p.plan(_pedido_entre_apps())
    assert chamadas[0]["system"][0]["text"] == prompts.PLANNER_MULTIAPP_SYSTEM_CURTO
    livre = _propriedades(chamadas[0]["output_config"]["format"]["schema"], "steps", "livre")
    assert "precondition" not in livre["properties"]
    assert [s.capability for s in plano.steps] == [None, None, "OPEN_PROFILE"] and not plano.missing


async def test_openai_manda_o_formato_curto_so_com_a_chave(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.file.ai.models["qwen-vl"] = ModelCaps(vision=True, tools=True, strict_tools=False,
                                               structured_output="json_schema", thinking=False, effort=False)
    cfg.file.ai.providers["local"] = ProviderCfg(kind="openai", base_url="http://127.0.0.1:8001/v1",
                                                 sends_data_externally=False)
    cfg.file.ai.roles["plan"] = RoleCfg(provider="local", model="qwen-vl")
    cfg.file.ai.esquema_do_plano = "curto"
    p = OpenAICompatProvider(cfg, role=cfg.ai_role("plan"))
    p.models = {papel: "qwen-vl" for papel in ("plan", "decide", "verify", "escalation", "social")}
    vistos: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        mensagem = {"role": "assistant", "content": json.dumps(_plano_livre_curto())}
        return httpx.Response(200, json={"model": "qwen-vl", "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                                         "choices": [{"index": 0, "finish_reason": "stop", "message": mensagem}]})
    p.set_client(httpx.AsyncClient(transport=httpx.MockTransport(responder)))
    try:
        plano, _ = await p.plan(PlanRequest(command="No QA Messenger, envie Bom dia para QA-001", run_id="r",
                                            instances=NO_QA, apps=[QA_APP]))
    finally:
        await p.aclose()
    corpo = json.loads(vistos[0].content)
    assert corpo["messages"][0] == {"role": "system", "content": prompts.PLANNER_SYSTEM_CURTO}
    etapa = _propriedades(corpo["response_format"]["json_schema"]["schema"], "steps")
    assert "max_attempts" not in etapa["properties"]
    assert [s.max_attempts for s in plano.steps] == [3, 3, 3, 1, 3]

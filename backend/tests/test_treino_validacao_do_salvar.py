"""Item 31.83: o `save` do modo treinamento confere a proposta ANTES de escrever (fluxo, escopo, receita).

Simulado (aparelho falso do harness, sem IA): cada recusa tem o código e o status 400, e nenhuma deixa rastro."""
from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from app.training.recorder import TrainingError
from app.training.skills import validar_proposta_para_salvar

from .conftest import Harness
from .test_modo_treinamento import _no_controle

COMANDO = "responda a DM de {contato} com {mensagem}"


def _etapa(chave: str, entradas: list[int], **extra: Any) -> dict[str, Any]:
    return {"key": chave, "title": f"Etapa {chave}", "goal": f"fazer {chave}", "inputs": entradas, "side_effect": False,
            "capability": None, "bindings": [], "app_id": None,
            "postcondition": {"kind": "model_judged", "value": "feito", "description": f"{chave} concluída"}, **extra}


def _valida() -> dict[str, Any]:
    return {"summary": "responder DM", "command_template": COMANDO, "app_id": "qa-messenger",
            "parameters": [{"name": "contato", "example": "QA-001", "description": ""},
                           {"name": "mensagem", "example": "oi", "description": ""}],
            "steps": [_etapa("abrir", [1]), _etapa("escrever", [2, 3])], "discarded": [{"seq": 4, "why": "engano"}],
            "questions": []}


# Cada caso: (código esperado, função que estraga a proposta válida).
def _param_fora(p: dict[str, Any]) -> None:
    p["parameters"].append({"name": "assunto", "example": "x", "description": ""})


def _param_nao_declarado(p: dict[str, Any]) -> None:
    p["command_template"] = COMANDO + " sobre {assunto}"


def _sem_chave(p: dict[str, Any]) -> None:
    p["steps"][1]["key"] = ""


def _chave_repetida(p: dict[str, Any]) -> None:
    p["steps"][1]["key"] = "abrir"


def _sem_titulo_nem_objetivo(p: dict[str, Any]) -> None:
    p["steps"][1].update(title="", goal="")


def _so_parametros(p: dict[str, Any]) -> None:
    p["command_template"] = "{contato} - {mensagem}"       # nenhuma palavra fixa


def _pouco_texto_fixo(p: dict[str, Any]) -> None:
    p["command_template"] = "abra {contato} e {mensagem}"  # 2 palavras fixas, mas só 5 letras: ainda genérico


def _efeito_sem_poscondicao(p: dict[str, Any]) -> None:
    p["steps"][1].update(side_effect=True, postcondition={"kind": "model_judged", "value": "", "description": ""})


def _entrada_orfa(p: dict[str, Any]) -> None:
    p["discarded"] = []                                    # a #4 não está em etapa nem em descarte


def _duplicada_em_descartada(p: dict[str, Any]) -> None:
    p["discarded"].append({"seq": 2, "why": "também descartada"})   # a #2 está na etapa 2 e em `discarded`


def _duplicada_em_duas_etapas(p: dict[str, Any]) -> None:
    p["steps"][0]["inputs"] = [1, 2]                                 # a #2 também está na etapa 2


def _parametro_com_maiuscula(p: dict[str, Any]) -> None:
    p["command_template"] = "responda a DM de {Contato} com {mensagem}"


def _parametro_com_acento(p: dict[str, Any]) -> None:
    p["command_template"] = "responda a DM de {endereço} com {mensagem}"


def _comeca_por_parametro(p: dict[str, Any]) -> None:
    p["command_template"] = "{contato} para o cliente {mensagem}"       # 4 palavras fixas, mas começa por parâmetro


def _seq_inexistente_na_etapa(p: dict[str, Any]) -> None:
    p["steps"][1]["inputs"] = [2, 3, 99]                             # a #99 não foi gravada


def _seq_inexistente_no_descarte(p: dict[str, Any]) -> None:
    p["discarded"].append({"seq": 99, "why": "x"})


def _seq_repetida_no_descarte(p: dict[str, Any]) -> None:
    p["discarded"].append({"seq": 4, "why": "de novo"})              # a #4 duas vezes em `discarded`


def _side_effect_em_texto(p: dict[str, Any]) -> None:
    p["steps"][1]["side_effect"] = "false"                           # verdadeiro para o `bool()` do salvar


def _chave_aberta(p: dict[str, Any]) -> None:
    p["command_template"] = "responda a DM de {contato com {mensagem}"


def _chave_fechada(p: dict[str, Any]) -> None:
    p["command_template"] = "responda a DM de contato} com {mensagem}"


CASOS: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    ("entrada_inexistente", _seq_inexistente_na_etapa),
    ("entrada_inexistente", _seq_inexistente_no_descarte),
    ("entrada_duplicada", _seq_repetida_no_descarte),
    ("etapa_invalida", _side_effect_em_texto),
    ("parametro_invalido", _chave_aberta),
    ("parametro_invalido", _chave_fechada),
    ("entrada_duplicada", _duplicada_em_descartada),
    ("entrada_duplicada", _duplicada_em_duas_etapas),
    ("parametro_invalido", _parametro_com_maiuscula),
    ("parametro_invalido", _parametro_com_acento),
    ("comando_generico", _comeca_por_parametro),
    ("parametro_fora_do_comando", _param_fora),
    ("parametro_nao_declarado", _param_nao_declarado),
    ("etapa_invalida", _sem_chave),
    ("etapa_invalida", _chave_repetida),
    ("etapa_invalida", _sem_titulo_nem_objetivo),
    ("comando_generico", _so_parametros),
    ("comando_generico", _pouco_texto_fixo),
    ("pos_condicao_vazia", _efeito_sem_poscondicao),
    ("entradas_sem_etapa", _entrada_orfa),
]


async def _sessao_gravada(harness: Harness, entradas: int = 4) -> tuple[Any, str]:
    st, rt, lease = await _no_controle(harness)
    s = st.training.start("android-01", intent="Responder uma DM no QA Messenger", lease_id=lease, app_id="qa-messenger")
    for _ in range(entradas):
        st.training.record(rt, {"type": "open_app", "app_id": "qa-messenger"}, None)
    st.training.stop(s["id"])
    return st, s["id"]


def _rastro(st: Any, sid: str) -> tuple[int, int, str]:
    return (st.db.scalar("SELECT COUNT(*) FROM flows"), st.db.scalar("SELECT COUNT(*) FROM recipes"),
            st.training.get(sid)["status"])


@pytest.mark.parametrize(("codigo", "estraga"), CASOS, ids=[f"{c}-{f.__name__}" for c, f in CASOS])
async def test_save_recusa_com_400_e_sem_escrever_nada(harness: Harness, codigo: str, estraga: Callable[..., None]) -> None:
    st, sid = await _sessao_gravada(harness)
    antes = _rastro(st, sid)
    proposta = copy.deepcopy(_valida())
    estraga(proposta)
    with pytest.raises(TrainingError) as erro:
        await st.skills.save(sid, proposal=proposta, profile_ids=[], group_ids=[])
    assert erro.value.code == codigo and erro.value.status == 400, (erro.value.code, erro.value.message)
    assert erro.value.message.strip()
    assert erro.value.message.endswith("Peça uma nova proposta à IA.") == (codigo not in ("comando_generico", "parametro_invalido"))
    assert _rastro(st, sid) == antes                      # nem fluxo, nem receita, nem a sessão virou "saved"
    assert st.db.scalar("SELECT COUNT(*) FROM flow_required_apps") == 0


async def test_entrada_orfa_lista_os_numeros(harness: Harness) -> None:
    st, sid = await _sessao_gravada(harness, entradas=5)
    proposta = _valida()
    proposta["discarded"] = []
    with pytest.raises(TrainingError) as erro:
        await st.skills.save(sid, proposal=proposta, profile_ids=[], group_ids=[])
    assert erro.value.code == "entradas_sem_etapa" and "#4" in erro.value.message and "#5" in erro.value.message


async def test_proposta_valida_continua_salvando_e_avisa_etapa_sem_efeito_sem_poscondicao(harness: Harness) -> None:
    st, sid = await _sessao_gravada(harness)
    proposta = _valida()
    proposta["steps"][0]["postcondition"] = {"kind": "model_judged", "value": "", "description": ""}   # sem efeito: aceita
    salvo = await st.skills.save(sid, proposal=proposta, profile_ids=[], group_ids=[])
    assert salvo["flow_id"] and st.training.get(sid)["status"] == "saved"
    assert len(salvo["warnings"]) == 1 and "Etapa abrir" in salvo["warnings"][0]
    plano = st.db.one("SELECT plan FROM flows WHERE id=?", (salvo["flow_id"],))["plan"]
    assert "fazer abrir" in plano                          # o objetivo serviu de descrição, como antes


async def test_rota_devolve_400_com_o_codigo(harness: Harness) -> None:
    from .test_perfil_bloqueado_e_capacidades import _cliente
    _, sid = await _sessao_gravada(harness)
    proposta = _valida()
    proposta["parameters"].append({"name": "assunto", "example": "x", "description": ""})
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/save", json={"proposal": proposta})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "parametro_fora_do_comando", r.text


def test_validador_aceita_marcador_reservado_e_chave_de_catalogo() -> None:
    p = _valida()
    p["command_template"] = "responda a DM de {contato} com {mensagem} em {account_label}"
    p["steps"][1]["capability"] = "SEND_DM"
    p["steps"][1]["side_effect"] = True
    p["steps"][1]["postcondition"] = {"kind": "model_judged", "value": "", "description": ""}
    saida, avisos = validar_proposta_para_salvar(p, {1, 2, 3, 4}, etapa_do_catalogo=lambda st: st.get("capability") == "SEND_DM")
    assert avisos == [] and saida["steps"][1]["inputs"] == [2, 3] and saida["discarded"][0]["seq"] == 4


def test_padrao_da_chave_e_o_do_plan_step() -> None:
    from app.models import PlanStep
    from app.training.skills import _CHAVE_DE_ETAPA
    assert _CHAVE_DE_ETAPA.pattern == next(m.pattern for m in PlanStep.model_fields["key"].metadata if hasattr(m, "pattern"))


def _com(**mudancas: Any) -> dict[str, Any]:
    p = _valida()
    p.update(mudancas)
    return p


def _sem_marca_de_etapa(**campos: Any) -> dict[str, Any]:
    p = _valida()
    p["steps"][1].update(campos)
    return p


@pytest.mark.parametrize("comando", ["{pedido} no instagram", "{acao} para o cliente", "siga {perfil}", "abra {app}",
                                     "{mensagem}", "  "])
def test_comando_que_sequestra_pedido_alheio_e_recusado(comando: str) -> None:
    nomes = sorted({m for m in __import__("re").findall(r"\{([a-z]+)\}", comando)})
    p = _com(command_template=comando, parameters=[{"name": n, "example": "x"} for n in nomes])
    with pytest.raises(TrainingError) as erro:
        validar_proposta_para_salvar(p, {1, 2, 3, 4})
    assert erro.value.code == "comando_generico" and "comece pelo verbo" in erro.value.message


@pytest.mark.parametrize(("comando", "nomes"), [("envie {mensagem} para {contato}", ["mensagem", "contato"]),
                                                ("ligue para {contato}", ["contato"])])
def test_molde_curto_e_legitimo_passa(comando: str, nomes: list[str]) -> None:
    p = _com(command_template=comando, parameters=[{"name": n, "example": "x"} for n in nomes])
    validar_proposta_para_salvar(p, {1, 2, 3, 4})


def test_mensagem_da_duplicada_lista_os_numeros() -> None:
    p = _valida()
    p["steps"][0]["inputs"] = [1, 2]
    p["discarded"].append({"seq": 3, "why": "x"})
    with pytest.raises(TrainingError) as erro:
        validar_proposta_para_salvar(p, {1, 2, 3, 4})
    assert erro.value.code == "entrada_duplicada" and "#2" in erro.value.message and "#3" in erro.value.message
    assert erro.value.message.endswith("Peça uma nova proposta à IA.")


# Tipos errados: antes davam KeyError/AttributeError/TypeError (500) ou viravam outra coisa calados (`inputs: "12"`).
MALFORMADAS: list[tuple[str, dict[str, Any]]] = [
    ("etapa_invalida", _sem_marca_de_etapa(title=7)),
    ("etapa_invalida", _sem_marca_de_etapa(goal=["x"])),
    ("etapa_invalida", _sem_marca_de_etapa(postcondition={"kind": "model_judged", "value": 5, "description": "d"})),
    ("etapa_invalida", _sem_marca_de_etapa(postcondition={"kind": "model_judged", "value": "v", "description": {}})),
    ("etapa_invalida", _sem_marca_de_etapa(bindings=["username"])),
    ("etapa_invalida", _sem_marca_de_etapa(bindings={"username": "x"})),
    ("etapa_invalida", _sem_marca_de_etapa(inputs="23")),
    ("etapa_invalida", _sem_marca_de_etapa(inputs=["2", "3"])),
    ("etapa_invalida", _sem_marca_de_etapa(inputs=[2, True])),
    ("etapa_invalida", _sem_marca_de_etapa(capability=["LIKE_POST"])),
    ("proposta_invalida", _com(discarded="4")),
    ("proposta_invalida", _com(discarded=[{"why": "sem seq"}])),
    ("proposta_invalida", _com(discarded=["4"])),
    ("proposta_invalida", _com(parameters="contato")),
    ("proposta_invalida", _com(parameters=[{"name": ["x"]}])),
    ("proposta_invalida", _com(summary=3)),
    ("proposta_invalida", _com(app_id=["qa"])),
]


@pytest.mark.parametrize(("codigo", "proposta"), MALFORMADAS, ids=[f"{c}-{i}" for i, (c, _) in enumerate(MALFORMADAS)])
def test_tipo_errado_vira_400_e_nao_500(codigo: str, proposta: dict[str, Any]) -> None:
    with pytest.raises(TrainingError) as erro:
        validar_proposta_para_salvar(proposta, {1, 2, 3, 4})
    assert erro.value.code == codigo and erro.value.status == 400, (erro.value.code, erro.value.message)


async def test_comando_que_nao_e_texto_vira_400(harness: Harness) -> None:
    st, sid = await _sessao_gravada(harness)
    with pytest.raises(TrainingError) as erro:
        await st.skills.save(sid, proposal=_com(command_template=12), profile_ids=[], group_ids=[])
    assert erro.value.code == "invalid_command" and erro.value.status == 400

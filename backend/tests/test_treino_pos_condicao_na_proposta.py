"""31.148: a IA não propõe a pós-condição que já vale na partida.

Achado das provas reais de 06/10: 2 de 2 propostas (ai_calls 4939 e 4942) puseram em "abrir a busca" um texto que já
estava na tela de partida, e o 31.122 e o 31.142 só pegavam o erro depois. Agora o `propose` manda, por entrada, os
textos da tela (a inteira, dos `screen_elements`) e os que apareceram depois dela, com todo dado da persona como
marcador; e, se a IA ainda propuser uma que já vale, a proposta volta com `pos_condicoes_ja_valem` e as sugestões
prontas, sem trocar nada sozinha.

Nível de prova: `simulated` (funções puras e harness com aparelho e provedor falsos; nenhuma IA).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.planning.training import TRAINER_SYSTEM, TrainingRequest, linha_da_entrada, trainer_user
from app.training import partida

from .conftest import Harness
from .test_modo_treinamento import _no_controle
from .test_treino_partida_f2_e_sequencia import BUSCANDO, INICIO
from .test_treino_segredo_na_gravacao import _arvore, _el

PKG = "com.pocqa.messenger"
BUSCA = "com.pocqa.busca"


# ------------------------------------------------------------------ função pura
def test_cada_entrada_leva_a_tela_inteira_e_o_que_apareceu_depois() -> None:
    textos = partida.textos_da_proposta([INICIO, BUSCANDO], {})
    assert "Search settings" in textos[1]["partida"]          # o texto de id de interface que as screen_lines escondiam
    assert textos[1]["depois"] == ["Back", "No results"]       # o que a etapa faz aparecer, sem o que já estava
    assert "depois" not in textos[2]                           # a última entrada: a tela final não é gravada
    sem_tela = {"seq": 3, "type": "key"}
    assert 3 not in partida.textos_da_proposta([INICIO, BUSCANDO, sem_tela], {})


def test_o_dado_da_persona_vira_marcador_e_texto_longo_e_cortado() -> None:
    longa = "x" * 300
    um = {"seq": 1, "type": "tap", "screen_elements": [{"t": "Conta de Ana Souza"}, {"t": longa}, {"t": "ok"}]}
    dois = {"seq": 2, "type": "tap", "screen_elements": [{"t": "Mensagem para Souza"}]}
    textos = partida.textos_da_proposta([um, dois], {"perfil_nome": "Ana Souza", "perfil_sobrenome": "Souza"})
    tudo = json.dumps(textos, ensure_ascii=False)
    assert "Ana" not in tudo and "Souza" not in tudo and "{perfil_" in tudo
    assert len(textos[1]["partida"][1]) == partida.CARACTERES_NA_PROPOSTA
    assert "ok" not in textos[1]["partida"]                    # abaixo do piso de 3 caracteres


def test_o_prompt_mostra_a_tela_e_o_depois_no_lugar_das_linhas_filtradas() -> None:
    textos = partida.textos_da_proposta([INICIO, BUSCANDO], {})
    linha = linha_da_entrada({**INICIO, "textos_da_tela": textos[1]})
    assert "textos na tela: " in linha and "Search settings" in linha and "apareceram depois: Back · No results" in linha
    assert "tela mostrava" not in linha
    assert "apareceram depois" not in linha_da_entrada({**BUSCANDO, "textos_da_tela": textos[2]})
    antiga = linha_da_entrada({"seq": 1, "type": "tap", "screen_lines": ["Network & internet"]})
    assert "tela mostrava: Network & internet" in antiga      # sessão sem elementos: como antes
    assert "NÃO pode estar na tela da 1ª entrada da etapa" in TRAINER_SYSTEM
    req = TrainingRequest(intent="buscar", app_id=None, apps=[], inputs=[{**INICIO, "textos_da_tela": textos[1]}])
    assert "apareceram depois" in trainer_user(req)


# ------------------------------------------------------------------ no propose (harness)
async def _gravada(harness: Harness) -> tuple[Any, str]:
    st, rt, lease = await _no_controle(harness)
    s = st.training.start("android-01", intent="Buscar no app", lease_id=lease, app_id="qa-messenger")
    barra = _el("e1", text="Buscar no app", rid=f"{PKG}:id/barra", clickable=True, bounds=(0, 0, 100, 100))
    st.training.record(rt, {"type": "tap", "x": 50, "y": 50},
                       _arvore(_el("e0", text="Conta de Ana Souza"), barra, _el("e9", text="segredo", password=True)))
    campo = _el("e2", rid=f"{BUSCA}:id/campo", editable=True, focused=True)
    st.training.record(rt, {"type": "text", "text": "wifi"},
                       _arvore(campo, _el("e3", text="Nenhum resultado"), _el("e4", desc="Voltar")))
    st.training.stop(s["id"], lease_id=lease)
    return st, s["id"]


def _resposta(pos_abrir: dict[str, str]) -> dict[str, Any]:
    def etapa(chave: str, entradas: list[int], pos: dict[str, str]) -> dict[str, Any]:
        return {"key": chave, "title": chave, "goal": chave, "inputs": entradas, "side_effect": False,
                "capability": None, "bindings": [], "app_id": None, "postcondition": pos}
    return {"summary": "buscar", "command_template": "busque {termo} no app", "app_id": None,
            "parameters": [{"name": "termo", "example": "wifi", "description": "o termo"}],
            "steps": [etapa("abrir_busca", [1], pos_abrir),
                      etapa("buscar", [2], {"kind": "text_visible", "value": "{termo}", "description": "o termo"})],
            "discarded": [], "questions": []}


async def test_o_propose_manda_a_tela_marcada_e_devolve_o_alerta_quando_a_ia_insiste(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st, sid = await _gravada(harness)
    monkeypatch.setattr(st.repo, "variaveis_da_persona", lambda _pid: {"perfil_nome": "Ana Souza"})
    vistos: list[str] = []

    async def ia(req: TrainingRequest) -> tuple[dict[str, Any], SimpleNamespace]:
        vistos.append(trainer_user(req))
        return _resposta({"kind": "text_visible", "value": "Buscar no app", "description": "a busca abriu"}), SimpleNamespace()

    monkeypatch.setattr(st.provider, "generalize", ia)
    proposta = (await st.skills.propose(sid))["proposal"]
    (prompt,) = vistos
    assert "textos na tela: " in prompt and "apareceram depois: " in prompt and "Nenhum resultado" in prompt
    assert "Voltar" in prompt                                   # a descrição também é texto da tela
    assert "Ana" not in prompt and "Souza" not in prompt and "{perfil_nome}" in prompt
    assert "segredo" not in prompt                              # o campo de senha nunca foi guardado na tela
    (item,) = proposta["pos_condicoes_ja_valem"]                # a IA insistiu: o alerta já vem na proposta
    assert item["etapa"] == "abrir_busca" and item["valor"] == "Buscar no app"
    assert {"kind": "text_visible", "value": "Nenhum resultado", "texto": "Nenhum resultado"} in item["sugestoes_prontas"]
    assert proposta["steps"][0]["postcondition"]["value"] == "Buscar no app"   # nada trocado sozinho
    assert "Ana" not in json.dumps(proposta, ensure_ascii=False)


async def test_sem_pos_condicao_que_ja_vale_a_proposta_vem_sem_o_alerta(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st, sid = await _gravada(harness)

    async def ia(req: TrainingRequest) -> tuple[dict[str, Any], SimpleNamespace]:
        return _resposta({"kind": "text_visible", "value": "Nenhum resultado", "description": "a busca abriu"}), SimpleNamespace()

    monkeypatch.setattr(st.provider, "generalize", ia)
    proposta = (await st.skills.propose(sid))["proposal"]
    assert "pos_condicoes_ja_valem" not in proposta

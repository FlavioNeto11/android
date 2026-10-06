"""31.121 e 31.122: a etapa ensinada começa onde a reprodução começa e só passa quando agiu.

Achados das execuções reais de fluxos ensinados no central (06/10): 3 de 7 divergiram na 1ª etapa com "tela de partida
diferente", porque a gravação começou com o app já aberto; e a pós-condição "Internet", que já estava na tela inicial do
app, deixou a 1ª etapa da prova do 31.87 passar sem agir.

- 31.121: a receita da etapa que contém a 1ª entrada ganha `open_app` do app da sessão quando a gravação começou DENTRO
  dele, sem abri-lo. A gravação não muda; a outra etapa, e a gravação que já abre o app ou começa em outro, também não.
- 31.122 (adendo v1.83): a `text_visible` que já vale nos `screen_lines` da 1ª entrada da etapa (a regra do verificador: contém, sem
  caixa) entra num aviso da prévia, com até três textos da tela seguinte, e o `save` recusa (400 `pos_condicao_ja_vale`)
  antes de qualquer escrita. Marcador da persona, texto curto e etapa sem tela gravada ficam de fora; o dado da persona
  nunca é sugerido, e o que sobrar no título ou no valor sai com o marcador.

Nível de prova: `simulated` (funções puras e harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.models import PlanStep, Postcondition
from app.training import partida
from app.training.recorder import TrainingError
from app.training.skills import _destilar

from .conftest import Harness
from .test_modo_treinamento import _no_controle
from .test_treino_segredo_na_gravacao import _arvore, _el
from .test_treino_validacao_do_salvar import _etapa

PKG = "com.pocqa.messenger"


# ------------------------------------------------------------------ 31.121, a função pura
def _toque(seq: int, pacote: str = PKG) -> dict[str, Any]:
    return {"seq": seq, "type": "tap", "package": pacote, "target": {"text": "x"}}


def test_a_abertura_entra_so_na_etapa_da_primeira_entrada_que_comecou_dentro_do_app() -> None:
    primeira = _toque(1)
    etapa1, etapa2 = [primeira], [_toque(2)]
    com = partida.com_abertura(etapa1, primeira, "qa-messenger", PKG)
    assert [e["type"] for e in com] == ["open_app", "tap"] and com[0]["app_id"] == "qa-messenger"
    assert partida.com_abertura(etapa2, primeira, "qa-messenger", PKG) == etapa2          # outra etapa: igual
    assert etapa1 == [primeira]                                                           # a gravação não muda


def test_sem_abertura_quando_ja_abre_comeca_fora_ou_nao_ha_app() -> None:
    abre = {"seq": 1, "type": "open_app", "app_id": "qa-messenger", "package": None}
    assert partida.com_abertura([abre], abre, "qa-messenger", PKG) == [abre]
    fora = _toque(1, "com.google.android.apps.nexuslauncher")
    assert partida.com_abertura([fora], fora, "qa-messenger", PKG) == [fora]              # começou no lançador
    assert partida.com_abertura([_toque(1)], _toque(1), None, None) == [_toque(1)]
    assert partida.com_abertura([], None, "qa-messenger", PKG) == []


# ------------------------------------------------------------------ 31.122, a função pura
def _entrada(seq: int, linhas: list[str]) -> dict[str, Any]:
    return {"seq": seq, "type": "tap", "screen_lines": linhas}


INICIO = ["Settings", "Network & internet", "Mobile, Wi-Fi, hotspot", "Apps"]
REDE = ["Navigate up", "Internet", "AndroidWifi", "Calls & SMS", "SIMs"]
LISTA = ["Navigate up", "Wi-Fi", "AndroidWifi", "Add network"]


def _passo(chave: str, entradas: list[int], valor: str, kind: str = "text_visible") -> dict[str, Any]:
    return {"key": chave, "title": chave, "inputs": entradas, "postcondition": {"kind": kind, "value": valor}}


def test_a_pos_condicao_que_ja_vale_na_partida_e_achada_com_sugestoes_da_tela_seguinte() -> None:
    entradas = [_entrada(1, INICIO), _entrada(2, REDE), _entrada(3, LISTA)]
    passos = [_passo("abrir_rede", [1], "Internet"), _passo("abrir_internet", [2], "Add network")]
    (achado,) = partida.ja_valem(passos, entradas)
    assert achado["key"] == "abrir_rede" and achado["valor"] == "Internet"      # "internet" está em "Network & internet"
    assert achado["sugestoes"] == ["Navigate up", "AndroidWifi", "Calls & SMS"]   # da tela seguinte, fora da partida
    assert "“abrir_rede”" in partida.aviso([achado])[0] and "“AndroidWifi”" in partida.aviso([achado])[0]


def test_ficam_de_fora_marcador_texto_curto_outro_tipo_sem_tela_e_dado_da_persona() -> None:
    entradas = [_entrada(1, INICIO + ["Ana Lopes"]), _entrada(2, ["Ana Lopes", "Internet", "Calls & SMS"])]
    assert partida.ja_valem([_passo("a", [1], "{perfil_nome}")], entradas) == []
    assert partida.ja_valem([_passo("a", [1], "Ap")], entradas) == []                       # curto demais
    assert partida.ja_valem([_passo("a", [1], "Apps", "model_judged")], entradas) == []
    assert partida.ja_valem([_passo("a", [1], "Apps")], [{"seq": 1, "type": "tap"}]) == []  # sem tela gravada
    (achado,) = partida.ja_valem([_passo("a", [1], "Apps")], entradas, evitar=["Ana Lopes"])
    assert achado["sugestoes"] == ["Calls & SMS"]                  # nem o dado da persona, nem o que já estava lá
    assert partida.ja_valem([_passo("a", [1], "Apps")], entradas, descartadas=[1]) == []


def test_o_aviso_leva_o_marcador_e_nunca_o_dado_da_persona() -> None:
    """Pedido da orquestradora (08:09Z): o título e o valor que citam a persona saem com o marcador (31.87 F2)."""
    achado = {"key": "a", "titulo": "falar como Ana", "valor": "conversa com Ana Lopes", "sugestoes": ["Lopes respondeu"]}
    (linha,) = partida.aviso([achado], {"perfil_nome": "Ana", "perfil_sobrenome": "Lopes"})
    assert "Ana" not in linha and "Lopes" not in linha
    assert "falar como {perfil_nome}" in linha and "{perfil_nome} {perfil_sobrenome}" in linha
    assert partida.aviso([achado]) != [linha]                        # sem persona, o texto fica como veio


# ------------------------------------------------------------------ no ensino (harness)
async def _gravada_dentro_do_app(harness: Harness) -> tuple[Any, str]:
    """A pessoa abre a gravação com o app já na frente: a 1ª entrada é um toque DENTRO dele, sem `open_app`."""
    st, rt, lease = await _no_controle(harness)
    s = st.training.start("android-01", intent="Abrir uma conversa nova", lease_id=lease, app_id="qa-messenger")
    aba = _el("e1", text="Conversas", rid=f"{PKG}:id/tab_conversas", clickable=True, bounds=(0, 0, 100, 100))
    st.training.record(rt, {"type": "tap", "x": 50, "y": 50}, _arvore(_el("e0", text="Entrar"), aba))
    nova = _el("e2", text="Nova conversa", rid=f"{PKG}:id/nova", clickable=True, bounds=(0, 200, 100, 300))
    st.training.record(rt, {"type": "tap", "x": 50, "y": 250}, _arvore(_el("e3", text="Lista de conversas"), nova))
    st.training.stop(s["id"], lease_id=lease)
    return st, s["id"]


def _proposta(pos_abrir: dict[str, Any]) -> dict[str, Any]:
    return {"summary": "abrir conversa nova", "command_template": "abra uma conversa nova no app",
            "app_id": "qa-messenger", "parameters": [], "discarded": [], "questions": [],
            "steps": [_etapa("abrir", [1], postcondition=pos_abrir), _etapa("nova", [2])]}


async def test_a_receita_da_primeira_etapa_comeca_abrindo_o_app(harness: Harness) -> None:
    st, sid = await _gravada_dentro_do_app(harness)
    sess = st.training.get(sid)
    assert sess["inputs"][0]["type"] == "tap" and sess["inputs"][0]["package"] == PKG
    p = _proposta({"kind": "model_judged", "value": "", "description": "conversas abertas"})
    passos = [PlanStep(key=k, title=k, goal=k, postcondition=Postcondition(kind="model_judged", value="", description=k))
              for k in ("abrir", "nova")]
    apps = {"qa-messenger": {"id": "qa-messenger", "name": "QA", "package": PKG}}
    abrir, nova = _destilar(sess, p, passos, {}, apps)
    assert abrir.acoes is not None and [a["tool"] for a in abrir.acoes] == ["open_app", "tap"], abrir.motivo
    assert abrir.acoes[0]["args"] == {"package": PKG}
    assert nova.acoes is not None and [a["tool"] for a in nova.acoes] == ["tap"]
    assert [e["type"] for e in st.training.get(sid)["inputs"]] == ["tap", "tap"]   # a gravação não muda


async def test_a_previa_avisa_e_o_save_recusa_a_pos_condicao_que_ja_vale(harness: Harness) -> None:
    st, sid = await _gravada_dentro_do_app(harness)
    ja_estava = _proposta({"kind": "text_visible", "value": "Entrar", "description": "x"})
    previa = await st.skills.preview(sid, proposal=ja_estava, profile_ids=[], group_ids=[])
    (aviso,) = [a for a in previa["warnings"] if "já aparece na tela em que ela começa" in a]
    assert "“Entrar”" in aviso and "“Lista de conversas”" in aviso                 # a sugestão vem da tela seguinte
    fluxos = st.db.scalar("SELECT COUNT(*) FROM flows")
    with pytest.raises(TrainingError) as exc:
        await st.skills.save(sid, proposal=ja_estava, profile_ids=[], group_ids=[])
    assert exc.value.code == "pos_condicao_ja_vale" and exc.value.status == 400
    assert st.db.scalar("SELECT COUNT(*) FROM flows") == fluxos and st.training.get(sid)["status"] != "saved"
    certa = _proposta({"kind": "text_visible", "value": "Lista de conversas", "description": "x"})
    salvo = await st.skills.save(sid, proposal=certa, profile_ids=[], group_ids=[])
    assert salvo["flow_id"] and not any("já aparece" in a for a in salvo["warnings"])

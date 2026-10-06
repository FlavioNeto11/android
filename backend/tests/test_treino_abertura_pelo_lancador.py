"""31.139: a gravação que começa no lançador (gaveta, ícone) e entra no app vira `open_app` na receita.

Achado da leitura de 06/10: 2 de 9 fluxos ensinados começam na tela inicial; a receita 198 guardava o toque no ícone,
no layout do lançador daquele aparelho, e nunca reproduziu.

Nível de prova: `simulated` (funções puras e harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

from typing import Any

from app.automation.hierarchy import UiTree
from app.models import PlanStep, Postcondition
from app.training import lancador
from app.training.skills import _destilar

from .conftest import Harness
from .test_modo_treinamento import _no_controle
from .test_treino_segredo_na_gravacao import _el
from .test_treino_validacao_do_salvar import _etapa

PKG, LANCADOR, OUTRO = "com.pocqa.messenger", "com.google.android.apps.nexuslauncher", "com.android.settings"


def _e(seq: int, pacote: str, tipo: str = "tap") -> dict[str, Any]:
    return {"seq": seq, "type": tipo, "package": pacote}


def test_o_trecho_do_lancador_so_conta_quando_entra_no_app_da_sessao() -> None:
    gravacao = [_e(1, LANCADOR, "swipe"), _e(2, LANCADOR), _e(3, PKG), _e(4, LANCADOR)]
    assert lancador.abertura_pelo_lancador(gravacao, PKG) == [1, 2]
    assert lancador.abertura_pelo_lancador([_e(1, LANCADOR), _e(2, OUTRO), _e(3, PKG)], PKG) == []   # levou a outro app
    assert lancador.abertura_pelo_lancador([_e(1, PKG), _e(2, LANCADOR)], PKG) == []                 # começou no app
    assert lancador.abertura_pelo_lancador([_e(1, LANCADOR), _e(2, LANCADOR)], PKG) == []            # nunca entrou
    assert lancador.abertura_pelo_lancador(gravacao, None) == []


def test_a_etapa_da_primeira_entrada_troca_o_trecho_pela_abertura_e_as_outras_so_perdem_o_trecho() -> None:
    primeira = _e(1, LANCADOR, "swipe")
    etapa1, etapa2 = [primeira, _e(2, LANCADOR)], [_e(3, PKG)]
    com = lancador.sem_o_lancador(etapa1, [1, 2], primeira, "qa-messenger", PKG)
    assert com == [{"seq": 0, "type": "open_app", "app_id": "qa-messenger", "package": PKG}]
    assert lancador.sem_o_lancador(etapa2, [1, 2], primeira, "qa-messenger", PKG) == etapa2
    assert lancador.sem_o_lancador([_e(2, LANCADOR), _e(3, PKG)], [1, 2], primeira, "qa-messenger", PKG) == [_e(3, PKG)]
    assert lancador.sem_o_lancador(etapa1, [], primeira, "qa-messenger", PKG) == etapa1
    assert etapa1 == [primeira, _e(2, LANCADOR)]                                    # a gravação não muda


async def test_a_receita_abre_o_app_pelo_pacote_e_nao_pelo_icone(harness: Harness) -> None:
    st, rt, lease = await _no_controle(harness)
    s = st.training.start("android-01", intent="Abrir uma conversa nova", lease_id=lease, app_id="qa-messenger")
    tela_inicial = UiTree(elements=[_el("l1", desc="QA Messenger", rid=f"{LANCADOR}:id/icone", clickable=True,
                                        bounds=(0, 0, 100, 100))], packages=[LANCADOR], sensitive=False)
    st.training.record(rt, {"type": "swipe", "x": 360, "y": 1000, "x2": 360, "y2": 300}, tela_inicial)
    st.training.record(rt, {"type": "tap", "x": 50, "y": 50}, tela_inicial)
    nova = _el("e2", text="Nova conversa", rid=f"{PKG}:id/nova", clickable=True, bounds=(0, 200, 100, 300))
    st.training.record(rt, {"type": "tap", "x": 50, "y": 250},
                       UiTree(elements=[nova], packages=[PKG], sensitive=False))
    st.training.stop(s["id"], lease_id=lease)
    sess = st.training.get(s["id"])
    assert [e["package"] for e in sess["inputs"]] == [LANCADOR, LANCADOR, PKG]
    p = {"summary": "abrir conversa nova", "command_template": "abra uma conversa nova no app", "app_id": "qa-messenger",
         "parameters": [], "discarded": [], "questions": [], "steps": [_etapa("abrir", [1, 2]), _etapa("nova", [3])]}
    passos = [PlanStep(key=k, title=k, goal=k, postcondition=Postcondition(kind="model_judged", value="", description=k))
              for k in ("abrir", "nova")]
    apps = {"qa-messenger": {"id": "qa-messenger", "name": "QA", "package": PKG}}
    abrir, conversa = _destilar(sess, p, passos, {}, apps)
    assert abrir.acoes is not None and [a["tool"] for a in abrir.acoes] == ["open_app"], abrir.motivo
    assert abrir.acoes[0]["args"] == {"package": PKG}
    assert conversa.acoes is not None and [a["tool"] for a in conversa.acoes] == ["tap"]
    assert [e["type"] for e in st.training.get(s["id"])["inputs"]] == ["swipe", "tap", "tap"]    # a gravação não muda

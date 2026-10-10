"""31.327: a receita da exploração parte do estado conhecido do app, e a sombra compara pelo ALVO do toque, não pelo id.

Prova real do P-043 (10/10/2026, Outlook, `android-01`): a receita da exploração "abrir a pasta de lixo eletrônico" nasceu
só com "tocar em Junk", sem a partida (`nao_aplicavel` em duas tentativas), e, com a tela de partida certa (a gaveta
aberta), a IA tocou no mesmo "Junk" e a sombra contou "a IA escolheu outra ação": o rótulo da receita e a linha clicável da
IA têm `element_id` diferentes. Dois defeitos, duas correções:

1. `distill(exploratoria=True)`: com estado conhecido declarado, a receita só nasce se a 1ª ação PARTIU dele (âncora na
   ação 1); partida desconhecida não vira receita. O executor leva o app ao estado conhecido antes de explorar
   (`_partir_do_estado_conhecido`), na IA e na reprodução, então a receita é o caminho desde a caixa de entrada.
2. `mesmo_alvo`: quem recebe o toque é o menor clicável no ponto; a comparação usa isso, e o motivo da divergência traz os
   dois alvos (id, classe e limites, nunca o texto da tela).

Nível de prova: `simulated` (árvores sintéticas do Outlook; nenhuma IA; nenhum aparelho). `real`: `not_run`.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.automation.hierarchy import parse_hierarchy
from app.planning.provider import Decision
from app.taskqueue.recipes import (ANCORA_ESTADO_CONHECIDO, AlvoAusente, Replayer, distill, filhos_rotulados, mesmo_alvo,
                                   motivo_do_retorno, unique_selectors)

from .conftest import Harness

OL = "com.microsoft.office.outlook"


def _no(i: str, cls: str, bounds: str, *, clickable: bool = False, text: str = "", desc: str = "") -> str:
    rid = f' resource-id="{OL}:id/{i}"' if i else ""
    return (f'<node package="{OL}" class="{cls}"{rid} text="{text}" content-desc="{desc}" enabled="true" '
            f'clickable="{str(clickable).lower()}" bounds="{bounds}"/>')


#: A caixa de entrada (estado conhecido: a lista `conversation_list`) e a gaveta aberta por cima dela.
CAIXA = ('<hierarchy>' + _no("conversation_list", "android.view.View", "[0,160][720,1115]")
         + _no("nav", "android.widget.ImageButton", "[24,64][104,144]", clickable=True, desc="Open Navigation Drawer") + '</hierarchy>')
# Na gaveta o rótulo "Junk" é um filho SEM clique dentro da linha clicável (a receita toca o rótulo; a IA, a linha).
GAVETA = ('<hierarchy>' + _no("conversation_list", "android.view.View", "[0,160][720,1115]")
          + _no("drawer_folder_composable", "android.view.View", "[0,0][600,1232]")
          + _no("", "android.view.View", "[96,1100][600,1180]", clickable=True)                  # a linha
          + _no("", "android.view.View", "[150,1120][260,1160]", text="Junk")                    # o rótulo
          + _no("", "android.view.View", "[96,400][600,480]", clickable=True)                    # outra linha
          + '</hierarchy>')


def _ids(arvore: Any) -> dict[str, str]:
    return {"linha": next(e.id for e in arvore.elements if tuple(e.bounds) == (96, 1100, 600, 1180)),
            "rotulo": next(e.id for e in arvore.elements if e.text == "Junk"),
            "outra": next(e.id for e in arvore.elements if tuple(e.bounds) == (96, 400, 600, 480))}


# ------------------------------------------------------------------ mesmo_alvo
def test_o_rotulo_e_a_linha_que_o_contem_sao_o_mesmo_alvo_do_toque() -> None:
    arvore = parse_hierarchy(GAVETA)
    i = _ids(arvore)
    assert mesmo_alvo(arvore, i["rotulo"], i["linha"]) and mesmo_alvo(arvore, i["linha"], i["rotulo"])
    assert mesmo_alvo(arvore, i["linha"], i["linha"])


def test_outra_linha_ou_alvo_ausente_nao_e_o_mesmo_alvo() -> None:
    arvore = parse_hierarchy(GAVETA)
    i = _ids(arvore)
    assert not mesmo_alvo(arvore, i["rotulo"], i["outra"])
    assert not mesmo_alvo(arvore, i["rotulo"], None) and not mesmo_alvo(arvore, None, i["linha"])
    assert not mesmo_alvo(arvore, i["rotulo"], "e999")
    assert mesmo_alvo(arvore, None, None)                       # ações sem alvo (voltar, rolar) seguem iguais


# ------------------------------------------------------------------ a sombra
def _comparar(harness: Harness, escolha: str) -> Any:
    executor = harness.state.scheduler.executor                        # type: ignore[union-attr]
    arvore = parse_hierarchy(GAVETA)
    i = _ids(arvore)
    acao = [{"tool": "tap", "commit": False, "why": "Abrir a pasta Junk", "args": {},
             "selectors": [{"kind": "text", "text": "Junk", "via": "filho", "conteiner": "android.view.View"}]}]
    rr = SimpleNamespace(replayer=Replayer(recipe_id=1, version=1, actions=acao, variables={}), diverged=None,
                         partida_diferente=False, antes_da_comparacao=None)
    decisao = Decision(tool="tap", args={"element_id": i[escolha]})
    executor._shadow_compare(rr, SimpleNamespace(tree=arvore), decisao)    # noqa: SLF001
    return rr


def test_a_ia_toca_a_linha_do_mesmo_rotulo_e_a_sombra_concorda(harness: Harness) -> None:
    assert _comparar(harness, "linha").diverged is None


def test_a_ia_toca_outra_linha_e_a_divergencia_diz_os_dois_alvos_sem_o_texto(harness: Harness) -> None:
    rr = _comparar(harness, "outra")
    assert rr.diverged and rr.diverged.startswith("a IA escolheu outra ação (IA: tap ")
    assert "; receita: tap " in rr.diverged and "android.view.View" in rr.diverged and "[96, 400, 600, 480]" in rr.diverged
    assert "Junk" not in rr.diverged                                    # o texto da tela nunca vai ao motivo
    assert motivo_do_retorno(rr.diverged) == "outro"                    # a classe do retorno não muda


# ------------------------------------------------------------------ distill
def _linhas(*, voltar_antes: bool = False) -> list[dict[str, Any]]:
    arvore = parse_hierarchy(CAIXA)
    nav = next(e for e in arvore.elements if e.resource_id.endswith("nav"))
    gaveta = parse_hierarchy(GAVETA)
    i = _ids(gaveta)
    base = {"status": "done", "source": "ai", "side_effect": 0, "rationale": "r", "target": None}
    menu = {**base, "id": 2, "tool": "tap", "args": json.dumps({"element_id": nav.id}),
            "target": json.dumps({**nav.to_dict(), "unique": unique_selectors(arvore, nav)})}
    linha = gaveta.by_id(i["linha"])
    toque = {**base, "id": 3, "tool": "tap", "args": json.dumps({"element_id": i["linha"]}),
             "target": json.dumps({**linha.to_dict(), "unique": unique_selectors(gaveta, linha),
                                  "filhos": filhos_rotulados(gaveta, linha)})}
    voltar = {**base, "id": 1, "tool": "press_back", "args": "{}"}
    return ([voltar] if voltar_antes else []) + [menu, toque]


def test_a_exploracao_que_partiu_do_estado_conhecido_vira_receita_ancorada() -> None:
    acoes, motivo = distill(_linhas(), {}, em_casa_antes={2: True, 3: False}, exploratoria=True)   # type: ignore[arg-type]
    assert acoes is not None, motivo
    assert [a["tool"] for a in acoes] == ["tap", "tap"]
    assert acoes[0]["ancora"] == ANCORA_ESTADO_CONHECIDO and "ancora" not in acoes[1]


def test_a_exploracao_que_partiu_de_outra_tela_nao_vira_receita() -> None:
    acoes, motivo = distill(_linhas(), {}, em_casa_antes={2: False, 3: False}, exploratoria=True)  # type: ignore[arg-type]
    assert acoes is None and "não partiu do estado conhecido" in motivo
    acoes, motivo = distill(_linhas(), {}, em_casa_antes={3: False}, exploratoria=True)            # type: ignore[arg-type]
    assert acoes is None and "não partiu do estado conhecido" in motivo


def test_sem_estado_conhecido_declarado_ou_fora_da_exploracao_nada_muda() -> None:
    # app sem estado conhecido (`em_casa_antes` None): como antes, sem âncora
    acoes, motivo = distill(_linhas(), {}, em_casa_antes=None, exploratoria=True)                  # type: ignore[arg-type]
    assert acoes is not None and "ancora" not in acoes[0], motivo
    # etapa que não é de exploração: o comportamento de sempre, mesmo partindo de fora de casa
    acoes, motivo = distill(_linhas(), {}, em_casa_antes={2: False, 3: False})                     # type: ignore[arg-type]
    assert acoes is not None and "ancora" not in acoes[0], motivo


def test_o_voltar_inicial_da_ia_na_exploracao_segue_a_regra_do_31_230() -> None:
    acoes, motivo = distill(_linhas(voltar_antes=True), {}, em_casa_antes={2: True},               # type: ignore[arg-type]
                            exploratoria=True)
    assert acoes is not None, motivo
    assert [a["tool"] for a in acoes] == ["tap", "tap"] and acoes[0]["ancora"] == ANCORA_ESTADO_CONHECIDO


def test_a_receita_ancorada_da_exploracao_so_age_na_caixa_de_entrada() -> None:
    acoes, _ = distill(_linhas(), {}, em_casa_antes={2: True}, exploratoria=True)                  # type: ignore[arg-type]
    assert acoes is not None
    gaveta, caixa = parse_hierarchy(GAVETA), parse_hierarchy(CAIXA)
    rep = Replayer(recipe_id=1, version=1, actions=acoes, variables={}, em_casa=lambda t: t is caixa)
    with pytest.raises(AlvoAusente, match="estado conhecido"):
        rep.next(gaveta)                                                # fora da caixa: "não se aplicou", nunca às cegas
    assert rep.next(caixa) is not None


# ------------------------------------------------------------------ o executor leva o app ao estado conhecido
class _Aparelho:
    """Um Outlook que só sabe voltar: o 1º `back` fecha a gaveta e cai na caixa de entrada."""

    def __init__(self, tela: str) -> None:
        self.tela, self.teclas = tela, []                               # type: str, list[str]

    async def observe(self, rt: Any, *, timeout: float, imagem: bool) -> Any:
        return SimpleNamespace(tree=parse_hierarchy(CAIXA if self.tela == "caixa" else GAVETA))

    async def press_key(self, tecla: str) -> None:
        self.teclas.append(tecla)
        self.tela = "caixa"


async def _partir(harness: Harness, tela: str, monkeypatch: pytest.MonkeyPatch,
                  pacote: str = OL) -> tuple[list[str], _Aparelho]:
    executor = harness.state.scheduler.executor                        # type: ignore[union-attr]
    aparelho = _Aparelho(tela)

    async def run(fn: Any, *args: Any, **_kw: Any) -> Any:
        return await fn(*args)

    rt = SimpleNamespace(id="android-01", executor=SimpleNamespace(run=run), io=aparelho)
    monkeypatch.setattr(executor, "devices", SimpleNamespace(observe=aparelho.observe))
    monkeypatch.setattr(executor.cfg.file.ai, "action_settle_s", 0)
    app = SimpleNamespace(package=pacote, activity=None)
    passos = await executor._partir_do_estado_conhecido(                # noqa: SLF001
        rt, app, run_id="r-x", iid="android-01", step=SimpleNamespace(id="s", title="Explorar"), call_timeout=5.0)
    return passos, aparelho


async def test_da_gaveta_o_app_volta_a_caixa_de_entrada_antes_de_explorar(harness: Harness,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    passos, aparelho = await _partir(harness, "gaveta", monkeypatch)
    assert passos == ["voltar"] and aparelho.teclas == ["back"] and aparelho.tela == "caixa"


async def test_ja_na_caixa_de_entrada_nada_e_feito(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    passos, aparelho = await _partir(harness, "caixa", monkeypatch)
    assert passos == [] and aparelho.teclas == []


async def test_app_sem_estado_conhecido_declarado_segue_de_onde_esta(harness: Harness,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    passos, aparelho = await _partir(harness, "gaveta", monkeypatch, pacote="com.app.sem.conhecimento")
    assert passos == [] and aparelho.teclas == [] and aparelho.tela == "gaveta"

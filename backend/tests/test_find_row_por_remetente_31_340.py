"""31.340: `find_row` acha a linha de uma lista cega pelo REMETENTE, lido da imagem às cegas.

Prova real do P-046 (11/10/2026, Outlook): a lista da caixa é um `ComposeView` sem texto; o ator pediu 5 imagens e um `find_element`
e não ligou "a mensagem de Bruno Ferreira" ao `element_id` da linha (US$ 0,17 e o teto da exploração). Agora o executor lê o remetente
de cada linha candidata pelo caminho da `read_value` visual (recorte, leitor que não conhece o nome, concordância, triagem) e devolve
ao ator só os `element_id` das linhas que concordam. A transcrição, o remetente das outras linhas e o assunto nunca voltam.

Nível de prova: `simulated` (árvores, imagem e leitor falsos; nenhum aparelho, nenhuma IA). `real`: `not_run`.
"""
from __future__ import annotations

import json
from typing import Any

from app.automation.hierarchy import UiTree
from app.automation.tools import READ_ONLY, TOOLS, FindRow, tool_definitions, validate_call
from app.planning.provider import Transcricao
from app.taskqueue.linha_por_remetente import (ALTURA_MIN_DA_LINHA, LINHAS_MAX, achar_linhas_por_remetente,
                                               linhas_candidatas)
from app.taskqueue.saidas import LeituraVisual, ler_valor_visual

from .conftest import Harness
from .test_leitura_visual import (ASSUNTO, LISTA, QA, REMETENTE, _ator, _concluir, _conhecimento, _el, _etapa,  # noqa: F401
                                  _jpeg, _juiz, _plano, _termina, _tudo_do_run, caixa_cega)

COMANDO = "Abra o QA Messenger e ache a mensagem de Flavio Padilha."


def _lista(*linhas: tuple[int, int, int, int], extras: tuple[Any, ...] = ()) -> UiTree:
    """Uma lista cega: o contêiner, as linhas clicáveis sem texto e, a gosto, chips/botões/linhas com texto."""
    els = [_el("e1", rid=LISTA, bounds=(0, 160, 720, 1115))]
    for i, b in enumerate(linhas):
        els.append(_el(f"r{i}", bounds=b, clickable=True))
    els.extend(extras)
    return UiTree(elements=els, packages=[QA], sensitive=False)


LINHAS = [(0, 323, 720, 485), (0, 550, 720, 712), (0, 712, 720, 874), (0, 939, 720, 1101)]


# ------------------------------------------------------------------ as linhas candidatas
def test_so_as_linhas_inteiras_e_largas_da_lista_cega_sao_candidatas() -> None:
    chip = _el("c1", bounds=(32, 160, 336, 256), clickable=True)                    # chip de filtro: estreito
    fatia = _el("f1", bounds=(0, 1101, 720, 1115), clickable=True)                   # 14 px: linha cortada
    nao_clicavel = _el("n1", bounds=(0, 256, 720, 320), clickable=False)
    com_texto = _el("t1", bounds=(0, 150, 720, 250), clickable=True, text="Filtrar")
    fora = _el("o1", bounds=(0, 1120, 720, 1230), clickable=True)                    # fora do contêiner
    arvore = _lista(*LINHAS, extras=(chip, fatia, nao_clicavel, com_texto, fora))
    ids = [e.id for e in linhas_candidatas(arvore, _conhecimento(), "caixa_de_entrada")]
    assert ids == ["r0", "r1", "r2", "r3"]
    assert ALTURA_MIN_DA_LINHA > 14


def test_sem_conhecimento_ou_sem_regiao_para_a_tela_nao_ha_candidata() -> None:
    arvore = _lista(*LINHAS)
    assert linhas_candidatas(arvore, None, "caixa_de_entrada") == []
    assert linhas_candidatas(arvore, _conhecimento(), None) == []
    assert linhas_candidatas(arvore, _conhecimento(), "outra_tela") == []
    assert linhas_candidatas(arvore, _conhecimento(saidas=("assunto",)), "caixa_de_entrada") == []   # sem `remetente`


# ------------------------------------------------------------------ a busca, com o leitor às cegas de verdade
class _Leitor:
    """Devolve uma transcrição por chamada, na ordem em que as linhas são lidas; guarda o que recebeu."""

    def __init__(self, *respostas: Transcricao) -> None:
        self.respostas = list(respostas)
        self.recebidos: list[tuple[bytes, dict[str, str]]] = []

    async def __call__(self, recorte: bytes, saidas: dict[str, str]) -> Transcricao:
        self.recebidos.append((recorte, dict(saidas)))
        return self.respostas.pop(0)


def _t(remetente: str, assunto: str = "Reunião de segunda") -> Transcricao:
    return Transcricao(linhas=[remetente, assunto, "10:21"], campos={"remetente": remetente, "assunto": assunto})


async def _buscar(arvore: UiTree, leitor: _Leitor, sender: str = "Bruno Ferreira", *, teto: int = LINHAS_MAX,
                  habilitado: bool = True, policy: str = "auto") -> Any:
    tentativas: set[Any] = set()

    async def obter() -> Any:
        return arvore, _jpeg(), 720, 1280

    async def ler(linha: Any) -> LeituraVisual:
        return await ler_valor_visual(
            habilitado=habilitado, arvore=arvore, element_id=linha.id, nome="remetente", valor_do_ator=sender,
            conhecimento=_conhecimento(), tela="caixa_de_entrada", image_policy=policy, fora_do_app=None,
            largura=720, altura=1280, obter_imagem=obter, tentativas=tentativas, transcrever=leitor,
            tipo_da_tela="autenticada")

    return await achar_linhas_por_remetente(arvore, _conhecimento(), "caixa_de_entrada", ler=ler, teto=teto)


async def test_devolve_so_a_linha_cujo_remetente_o_leitor_confirma() -> None:
    leitor = _Leitor(_t("Ana Souza"), _t("Bruno Ferreira"), _t("Carla Dias"), _t("Diego Lima"))
    r = await _buscar(_lista(*LINHAS), leitor)
    assert [e.id for e in r.linhas] == ["r1"] and r.lidas == 4 and r.candidatas == 4 and r.puladas == 0
    # o leitor recebeu SÓ o recorte e o nome do campo: nunca o nome procurado
    assert len(leitor.recebidos) == 4
    assert all(saidas == {"remetente": "remetente"} for _recorte, saidas in leitor.recebidos)
    assert all(b"Bruno" not in recorte for recorte, _ in leitor.recebidos)


async def test_duas_mensagens_do_mesmo_remetente_voltam_de_cima_para_baixo() -> None:
    leitor = _Leitor(_t("Bruno Ferreira"), _t("Ana Souza"), _t("bruno ferreira"), _t("Carla Dias"))
    r = await _buscar(_lista(*LINHAS), leitor)
    assert [e.id for e in r.linhas] == ["r0", "r2"]                                  # caixa e pontuação das pontas não contam


async def test_nenhuma_linha_do_remetente_nao_e_erro() -> None:
    r = await _buscar(_lista(*LINHAS), _Leitor(*[_t("Ana Souza")] * 4))
    assert r.linhas == [] and r.lidas == 4 and r.puladas == 0 and r.indisponivel is None


async def test_linha_com_codigo_na_imagem_e_pulada_e_so_contada() -> None:
    codigo = Transcricao(linhas=["Instagram", "482913 is your Instagram code"], campos={"remetente": "Instagram"})
    leitor = _Leitor(codigo, _t("Bruno Ferreira"), _t("Ana Souza"), _t("Carla Dias"))
    r = await _buscar(_lista(*LINHAS), leitor)
    assert [e.id for e in r.linhas] == ["r1"] and r.puladas == 1
    # o resultado não carrega a transcrição: só ids e contagens
    assert "482913" not in json.dumps({"linhas": [e.id for e in r.linhas], "lidas": r.lidas, "puladas": r.puladas})


async def test_o_teto_de_linhas_lidas_limita_o_custo() -> None:
    muitas = [(0, 200 + i * 100, 720, 290 + i * 100) for i in range(9)]
    leitor = _Leitor(*[_t("Ana Souza")] * 9)
    r = await _buscar(_lista(*muitas), leitor, teto=LINHAS_MAX)
    assert len(leitor.recebidos) == LINHAS_MAX and r.candidatas == 9 and r.lidas == LINHAS_MAX


async def test_barreira_da_tela_inteira_encerra_a_busca_sem_ler_mais() -> None:
    leitor = _Leitor(*[_t("Bruno Ferreira")] * 4)
    r = await _buscar(_lista(*LINHAS), leitor, habilitado=False)
    assert r.linhas == [] and r.indisponivel == "desligado" and leitor.recebidos == []
    r2 = await _buscar(_lista(*LINHAS), leitor, policy="never")
    assert r2.indisponivel == "tela_sensivel" and leitor.recebidos == []


async def test_sem_linha_cega_na_tela_a_busca_diz_que_nao_ha_o_que_ler() -> None:
    r = await _buscar(UiTree(elements=[_el("e1", rid=LISTA, bounds=(0, 160, 720, 1115))], packages=[QA], sensitive=False), _Leitor())
    assert r.indisponivel and r.candidatas == 0


# ------------------------------------------------------------------ a ferramenta
def test_a_ferramenta_existe_e_e_somente_leitura() -> None:
    assert "find_row" in TOOLS and "find_row" in READ_ONLY
    args = validate_call("find_row", {"rationale": "achar a mensagem", "sender": "Bruno Ferreira"})
    assert isinstance(args, FindRow) and args.sender == "Bruno Ferreira"
    d = next(x for x in tool_definitions() if x["name"] == "find_row")
    assert "strict" not in d and "sender" in d["input_schema"]["properties"]


# ------------------------------------------------------------------ no executor (QA Messenger falso, linhas cegas)
def _acha(nome: str) -> Any:
    return {"tool": "find_row", "args": {"rationale": "achar a linha pelo remetente", "sender": nome}}


async def test_o_ator_recebe_so_os_element_id_e_o_leitor_nunca_o_nome(harness: Harness, caixa_cega: None) -> None:
    harness.state.cfg.file.ai.leitura_visual.enabled = True                # type: ignore[union-attr]
    inner, vistos = harness.ai.inner, []
    inner.leitura = Transcricao(linhas=[REMETENTE, ASSUNTO], campos={"remetente": REMETENTE, "assunto": ASSUNTO})
    _plano(inner, _etapa(capability="QA_IR_PARA_CAIXA"))
    _ator(inner, [_acha(REMETENTE), _concluir()], vistos)
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    assert (await _termina(harness, run.id)).status == "completed"
    historia = "\n".join(vistos)
    assert "find_row → linha(s) do remetente pedido, de cima para baixo:" in historia
    assert ASSUNTO not in historia                                           # o assunto lido nunca chega ao ator
    assert ASSUNTO not in _tudo_do_run(harness, run.id)                      # nem à ação, ao evento ou à evidência
    assert all(c.get("saidas") == ["remetente"] for c in harness.ai.calls if c["role"] == "leitura")
    assert harness.ai.count("leitura") == 3                                  # uma leitura barata por linha cega
    acao = harness.state.db.one("SELECT args, result, status FROM actions WHERE tool='find_row'")      # type: ignore[union-attr]
    assert acao["status"] == "done" and REMETENTE not in acao["args"]        # só o tamanho do nome na ação
    res = json.loads(acao["result"])
    assert res["candidatas"] == 3 and res["lidas"] == 3 and len(res["achadas"]) == 3


async def test_outro_remetente_devolve_nenhuma_e_o_ator_segue(harness: Harness, caixa_cega: None) -> None:
    harness.state.cfg.file.ai.leitura_visual.enabled = True                # type: ignore[union-attr]
    inner, vistos = harness.ai.inner, []
    inner.leitura = Transcricao(linhas=["Outra Pessoa", "Assunto qualquer"], campos={"remetente": "Outra Pessoa"})
    _plano(inner, _etapa(capability="QA_IR_PARA_CAIXA"))
    _ator(inner, [_acha(REMETENTE), _concluir()], vistos)
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    await _termina(harness, run.id)
    assert any("find_row → nenhuma das 3 linhas lidas é desse remetente" in v for v in vistos)
    assert "Outra Pessoa" not in "\n".join(vistos) and "Outra Pessoa" not in _tudo_do_run(harness, run.id)


async def test_com_a_leitura_visual_desligada_a_ferramenta_recusa_sem_chamar_o_leitor(harness: Harness,
                                                                                      caixa_cega: None) -> None:
    inner, vistos = harness.ai.inner, []
    _plano(inner, _etapa(capability="QA_IR_PARA_CAIXA", max_attempts=1))
    _ator(inner, [_acha(REMETENTE), _concluir()], vistos)
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)                       # leitura visual desligada (padrão)
    await _termina(harness, run.id)
    assert any("find_row REJEITADA: desligado" in v for v in vistos)
    assert harness.ai.count("leitura") == 0

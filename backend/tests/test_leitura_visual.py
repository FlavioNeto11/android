"""Item 12.5, nível 1 (ADR-070) — a leitura visual de uma saída de etapa, conferida às cegas por outro leitor.

O defeito de origem (r-20261002204347-8c3f6e e 178742, Outlook no android-01): a linha da caixa é um `ComposeView` cujos
filhos não têm texto nem descrição na árvore (passo 0, `real`, 02/10/2026), então `read_value` não tinha de onde tirar o
remetente e o assunto. A regra nova: onde o app DECLARA a região, o valor que o ator leu na imagem conta como saída se um
segundo leitor, que não vê o valor do ator, transcreve o mesmo no recorte da mesma captura.

Nível de prova: `simulated` — árvore, captura e leitor falsos (`SimulatedProvider.leitura`), sem aparelho nem IA reais.

O que se prova aqui:
- `ler_valor_visual`, uma barreira por vez, na ordem: cada recusa levanta o código fechado e NUNCA devolve valor;
- no executor (QA Messenger falso com linha cega): o sucesso grava `origem=visual` com leitor, sha256 do recorte e evidência,
  a ação sai sem o valor, o juiz recebe a imagem; a recusa não grava nada e a transcrição do leitor não aparece no histórico
  do ator, nas ações nem nos eventos; elemento com texto e `source=visual` grava `origem=arvore`;
- o consumidor: etapa com efeito que usa valor visual espera a pessoa; navegação segue;
- o que vai junto: `step_blocked.reason` redigido nos quatro destinos e a conta própria de recusas.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.automation.conhecimento_de_telas import de_dados
from app.automation.hierarchy import UiElement, UiTree
from app.automation.tools import ToolValidationError, validate_call
from app.models import PlanStep, Postcondition
from app.planning.capabilities import Capability, CapabilityCatalog
from app.planning.catalog import register, unregister
from app.planning.provider import Decision, Transcricao, Usage, Verdict
from app.taskqueue.saidas import (RECUSAS_VISUAIS, LeituraVisualRecusada, ler_valor_visual, razao_sem_segredo)

from .conftest import Harness
from .fake_device import PKG as QA, FakeQaDevice, Node

LISTA = "com.pocqa.messenger:id/conversation_list"
ASSUNTO = "Perfil para conferir: natgeo"
REMETENTE = "Flavio Padilha"
TRANSCRICAO_BOA = Transcricao(linhas=[REMETENTE, ASSUNTO, "10:21"],
                              campos={"remetente": REMETENTE, "assunto": ASSUNTO})
CODIGO = "482913"


# ================================================================== árvore, imagem e leitor falsos
def _el(eid: str, *, rid: str = "", text: str = "", desc: str = "", bounds: tuple[int, int, int, int] = (0, 0, 10, 10),
        clickable: bool = False) -> UiElement:
    return UiElement(id=eid, text=text, desc=desc, resource_id=rid, class_name="android.view.View", package=QA,
                     bounds=bounds, clickable=clickable, enabled=True, focused=False, scrollable=False,
                     editable=False, checked=False, password=False)


def _arvore(*, texto_no_filho: str = "", texto_na_ancora: str = "", sensitive: bool = False,
            truncada: bool = False, ancora: tuple[int, int, int, int] = (0, 323, 720, 485),
            na_lista: bool = True) -> UiTree:
    """A caixa do passo 0: a lista (ComposeView), a linha clicável e quatro filhos SEM texto."""
    els = [_el("e1", rid=LISTA, bounds=(0, 160, 720, 1115) if na_lista else (0, 160, 720, 300)),
           _el("e2", bounds=ancora, clickable=True, text=texto_na_ancora)]
    x1, y1, x2, y2 = ancora
    for i in range(4):
        els.append(_el(f"e{3 + i}", bounds=(x1 + 10 + i * 100, y1 + 10, x1 + 90 + i * 100, y2 - 10),
                       text=texto_no_filho if i == 3 else ""))
    return UiTree(elements=els, packages=[QA], sensitive=sensitive, truncada=truncada)


def _jpeg(cor: tuple[int, int, int] = (30, 40, 50), tamanho: tuple[int, int] = (576, 1024)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", tamanho, cor).save(buf, "JPEG")
    return buf.getvalue()


def _conhecimento(*, saidas: tuple[str, ...] = ("remetente", "assunto"), tela: str = "caixa_de_entrada"):
    return de_dados({
        "app": QA, "versao": 1, "idioma_padrao": "en", "sinais": {"en": {"nunca": "nunca_casa_xyz"}},
        "telas": [{"tela": "caixa_de_entrada", "tipo": "autenticada", "autenticada": True, "ids": ["conversation_list"]}],
        "estado_conhecido": {"telas": ["caixa_de_entrada"]},
        "leitura_visual": {"regioes": [{"tela": tela if tela == "caixa_de_entrada" else "caixa_de_entrada",
                                        "dentro_de": [LISTA], "saidas": list(saidas)}]}})


class _Leitor:
    """O leitor falso: guarda o que recebeu (para provar que veio SÓ o recorte e os nomes) e devolve o programado."""

    def __init__(self, resposta: Transcricao | None = TRANSCRICAO_BOA) -> None:
        self.resposta = resposta
        self.recebidos: list[tuple[bytes, dict[str, str]]] = []

    async def __call__(self, recorte: bytes, saidas: dict[str, str]) -> Transcricao:
        self.recebidos.append((recorte, dict(saidas)))
        assert self.resposta is not None
        return self.resposta


async def _ler(*, arvore: UiTree | None = None, valor: str = ASSUNTO, nome: str = "assunto", leitor: Any = None,
               habilitado: bool = True, tela: str | None = "caixa_de_entrada", conhecimento: Any = "padrao",
               policy: str = "auto", fora: str | None = None, imagem: Any = "padrao",
               tentativas: set[Any] | None = None, sem_leitor: bool = False, element_id: str = "e2") -> Any:
    arvore = arvore or _arvore()
    leitor = leitor or _Leitor()

    async def obter() -> Any:
        return (arvore, _jpeg(), 720, 1280) if imagem == "padrao" else imagem

    return await ler_valor_visual(
        habilitado=habilitado, arvore=arvore, element_id=element_id, nome=nome, valor_do_ator=valor,
        conhecimento=_conhecimento() if conhecimento == "padrao" else conhecimento, tela=tela, image_policy=policy,
        fora_do_app=fora, largura=720, altura=1280, obter_imagem=obter,
        tentativas=set() if tentativas is None else tentativas, transcrever=None if sem_leitor else leitor)


async def _recusa(codigo: str, **kw: Any) -> LeituraVisualRecusada:
    leitor = kw.setdefault("leitor", _Leitor())
    with pytest.raises(LeituraVisualRecusada) as exc:
        await _ler(**kw)
    assert exc.value.codigo == codigo, exc.value.rotulo
    return exc.value


# ================================================================== o vocabulário e o caso feliz
def test_o_vocabulario_de_recusa_e_fechado() -> None:
    with pytest.raises(ValueError):
        LeituraVisualRecusada("qualquer_coisa")
    assert LeituraVisualRecusada("triagem", "senha").rotulo == "triagem:senha"
    assert LeituraVisualRecusada("ilegivel").rotulo == "ilegivel"
    assert {"desligado", "elemento_com_texto", "regiao_nao_declarada", "arvore_truncada", "tela_sensivel", "fora_do_app",
            "sem_ancora", "captura_mudou", "repetida", "sem_leitor", "ilegivel", "truncado", "nao_confere",
            "triagem"} <= set(RECUSAS_VISUAIS)


async def test_concordancia_devolve_o_valor_e_o_leitor_recebe_so_o_recorte_e_os_nomes() -> None:
    leitor = _Leitor()
    lido = await _ler(leitor=leitor)
    assert lido.valor == ASSUNTO and len(lido.sha256) == 64
    [(recorte, pedidas)] = leitor.recebidos
    assert pedidas == {"assunto": "assunto"}               # só o nome e a descrição: nem o valor, nem a tela
    com = Image.open(io.BytesIO(recorte))
    assert com.size == (576, 130)                            # 720x162 do aparelho × 0,8 da imagem do modelo, sem margem
    assert lido.recorte == recorte


async def test_acentos_contam_e_caixa_pontuacao_das_pontas_e_espacos_nao() -> None:
    t = Transcricao(linhas=["  “Mãe   chegou! ”"], campos={"assunto": "MÃE chegou!"})
    # concorda no normalizado, mas grava a forma do LEITOR (a que está na imagem), não a do ator
    assert (await _ler(valor="mãe chegou", leitor=_Leitor(t))).valor == "MÃE chegou!"
    com_acento = Transcricao(linhas=["mae chegou"], campos={"assunto": "mae chegou"})
    await _recusa("nao_confere", valor="mãe chegou", leitor=_Leitor(com_acento))


# ================================================================== as barreiras, na ordem
async def test_0_opcao_desligada() -> None:
    leitor = _Leitor()
    await _recusa("desligado", habilitado=False, leitor=leitor)
    assert leitor.recebidos == []


async def test_1_elemento_com_texto_ou_descendente_com_texto() -> None:
    await _recusa("elemento_com_texto", arvore=_arvore(texto_na_ancora="algo"))
    await _recusa("elemento_com_texto", arvore=_arvore(texto_no_filho="algo"))
    await _recusa("elemento_com_texto", element_id="e99")                          # âncora que a árvore não tem


async def test_2_regiao_nao_declarada() -> None:
    await _recusa("regiao_nao_declarada", conhecimento=None)                      # app sem `leitura_visual`
    await _recusa("regiao_nao_declarada", conhecimento=_conhecimento(saidas=("remetente",)))   # saída fora da região
    await _recusa("regiao_nao_declarada", tela="conta_aberta")                    # outra tela (a gaveta por cima)
    await _recusa("regiao_nao_declarada", arvore=_arvore(na_lista=False))         # âncora fora do contêiner declarado


async def test_3_arvore_truncada() -> None:
    await _recusa("arvore_truncada", arvore=_arvore(truncada=True))


async def test_4_tela_sensivel_politica_de_imagem_e_fora_do_app() -> None:
    await _recusa("tela_sensivel", arvore=_arvore(sensitive=True))
    await _recusa("tela_sensivel", policy="never")
    await _recusa("fora_do_app", fora="com.android.settings")


async def test_5_ancora_sem_limites() -> None:
    await _recusa("sem_ancora", arvore=_arvore(ancora=(100, 400, 100, 400)))        # área zero


async def test_6_captura_mudou() -> None:
    await _recusa("captura_mudou", imagem=None)                                      # a captura nova não veio
    await _recusa("captura_mudou", imagem=(_arvore(texto_no_filho=""), None, 720, 1280))        # sem imagem
    outra = _arvore()
    outra.elements[2].desc = ""                                                       # mesma tela...
    outra.elements[5].text = ""
    outra.elements.append(_el("e9", text="chegou e-mail novo", bounds=(0, 900, 720, 960)))       # ...mais um texto
    await _recusa("captura_mudou", imagem=(outra, _jpeg(), 720, 1280))               # assinatura diferente
    deslocada = _arvore(ancora=(0, 330, 720, 492))
    await _recusa("captura_mudou", imagem=(deslocada, _jpeg(), 720, 1280))           # a âncora andou


async def test_7_segunda_tentativa_na_mesma_tela_mesmo_com_bytes_novos() -> None:
    tentativas: set[Any] = set()
    ruim = Transcricao(linhas=["outra coisa"], campos={"assunto": "outra coisa"})
    await _recusa("nao_confere", tentativas=tentativas, leitor=_Leitor(ruim))
    arvore = _arvore()

    async def captura_nova() -> Any:                       # o relógio da barra de status mudou os bytes do JPEG
        return arvore, _jpeg(cor=(31, 40, 50)), 720, 1280

    with pytest.raises(LeituraVisualRecusada) as exc:
        await ler_valor_visual(habilitado=True, arvore=arvore, element_id="e2", nome="assunto", valor_do_ator=ASSUNTO,
                               conhecimento=_conhecimento(), tela="caixa_de_entrada", image_policy="auto", fora_do_app=None,
                               largura=720, altura=1280, obter_imagem=captura_nova, tentativas=tentativas,
                               transcrever=_Leitor())
    assert exc.value.codigo == "repetida"
    assert (await _ler(tentativas=tentativas, nome="remetente", valor=REMETENTE)).valor == REMETENTE   # outro nome: outro par


async def test_8_sem_leitor() -> None:
    await _recusa("sem_leitor", sem_leitor=True)


async def test_9_ilegivel() -> None:
    await _recusa("ilegivel", leitor=_Leitor(Transcricao(linhas=[], campos={"assunto": None}, legivel=False)))


async def test_10_truncado_pela_marca_do_leitor_ou_pelas_reticencias() -> None:
    cortado = Transcricao(linhas=[ASSUNTO], campos={"assunto": ASSUNTO}, truncado=True)
    await _recusa("truncado", leitor=_Leitor(cortado))
    reticencias = Transcricao(linhas=["Perfil para conferir: nat…"], campos={"assunto": "Perfil para conferir: nat…"})
    await _recusa("truncado", valor="Perfil para conferir: nat…", leitor=_Leitor(reticencias))
    await _recusa("truncado", valor="Perfil para conferir...", leitor=_Leitor())     # o valor do ator já vem cortado


async def test_11_o_valor_nao_confere_com_o_do_leitor() -> None:
    # campos trocados: o remetente com o valor do assunto
    trocados = Transcricao(linhas=[REMETENTE, ASSUNTO], campos={"remetente": ASSUNTO, "assunto": REMETENTE})
    await _recusa("nao_confere", leitor=_Leitor(trocados))
    # linha errada: o leitor viu outra linha da caixa
    outra_linha = Transcricao(linhas=["Outra pessoa", "Outro assunto"], campos={"assunto": "Outro assunto"})
    await _recusa("nao_confere", leitor=_Leitor(outra_linha))
    # uma letra trocada
    uma_letra = Transcricao(linhas=[REMETENTE, "Perfil para conferir: natgeu"],
                            campos={"assunto": "Perfil para conferir: natgeu"})
    await _recusa("nao_confere", leitor=_Leitor(uma_letra))
    # o leitor devolve um campo que não aparece em nenhuma linha transcrita
    inventado = Transcricao(linhas=[REMETENTE, "10:21"], campos={"assunto": ASSUNTO})
    await _recusa("nao_confere", leitor=_Leitor(inventado))
    # o campo não veio, ou veio só parte de uma palavra
    await _recusa("nao_confere", leitor=_Leitor(Transcricao(linhas=[ASSUNTO], campos={"assunto": None})))
    parte = Transcricao(linhas=["Perfil para conferir: natgeoxyz"], campos={"assunto": "natgeo"})
    await _recusa("nao_confere", valor="natgeo", leitor=_Leitor(parte))


async def test_12_triagem_sobre_a_transcricao_e_o_valor() -> None:
    codigo = f"Your Instagram code is {CODIGO}"
    t = Transcricao(linhas=[REMETENTE, codigo], campos={"assunto": codigo})
    erro = await _recusa("triagem", valor=codigo, leitor=_Leitor(t))
    assert erro.motivo and CODIGO not in erro.rotulo
    # o valor é inofensivo, mas a transcrição da linha traz o código: a linha toda é recusada
    mista = Transcricao(linhas=[REMETENTE, f"Perfil para conferir: natgeo {codigo} is your verification code"],
                        campos={"assunto": "Perfil para conferir: natgeo"})
    erro = await _recusa("triagem", leitor=_Leitor(mista))
    assert CODIGO not in erro.rotulo


async def test_grava_o_valor_do_leitor_e_nao_o_do_ator() -> None:
    t = Transcricao(linhas=["flavio padilha"], campos={"remetente": "flavio padilha"})
    lido = await _ler(valor="FLAVIO PADILHA!", nome="remetente", leitor=_Leitor(t))
    assert lido.valor == "flavio padilha"                        # o "!" a mais e a caixa do ator não são gravados
    t2 = Transcricao(linhas=["  Flavio    Padilha "], campos={"remetente": "Flavio    Padilha"})
    assert (await _ler(valor="flavio padilha", nome="remetente", leitor=_Leitor(t2))).valor == "Flavio Padilha"  # `limpar`


async def test_valor_com_forma_de_codigo_e_recusado_na_leitura_visual_mesmo_sem_palavra_de_contexto() -> None:
    sozinho = Transcricao(linhas=[CODIGO], campos={"assunto": CODIGO})
    erro = await _recusa("triagem", valor=CODIGO, leitor=_Leitor(sozinho))
    assert erro.motivo == "código de verificação" and CODIGO not in erro.rotulo
    for forma in ("482 913", "4829-1357", "1234"):
        t = Transcricao(linhas=[forma], campos={"assunto": forma})
        await _recusa("triagem", valor=forma, leitor=_Leitor(t))
    # texto comum, com número pequeno ou longo demais para ser código, passa
    for comum in ("Reunião 14h", "Pedido 123", "Fatura 123456789012"):
        t = Transcricao(linhas=[comum], campos={"assunto": comum})
        assert (await _ler(valor=comum, leitor=_Leitor(t))).valor == comum


FORMAS_DE_CODIGO = ["482913", "４８２９１３", "٤٨٢٩١٣", "482.913", "482,913", "482/913", "482_913", "482·913", "482\u200b913",
                    "482\u00a0913", "482\u2009913", "48-29-13", "4829 13", "4829-1300", "G-482913", "ABC123",
                    "Your code is 482.913", "Seu código é 482.913", "Your code is ４８２９１３", "Your code is G-482913",
                    "Your code is ABC123", "Your code: 4829 13"]


@pytest.mark.parametrize("forma", FORMAS_DE_CODIGO)
async def test_d1_d2_valor_com_forma_de_codigo_em_qualquer_grafia_e_recusado_na_leitura_visual(forma: str) -> None:
    t = Transcricao(linhas=[forma], campos={"assunto": forma})
    erro = await _recusa("triagem", valor=forma, leitor=_Leitor(t))
    assert erro.motivo == "código de verificação" and "482" not in erro.rotulo


@pytest.mark.parametrize("comum", ["Reunião 14h", "Pedido 123", "Fatura 123456789012", "12/10", "02/10/26", "14h30", "14:30",
                                   "natgeo", "Flavio Padilha", "Joao_Silva2024", "482913123"])
async def test_d2_texto_comum_data_e_hora_nao_sao_codigo_na_leitura_visual(comum: str) -> None:
    # limiar: só número de 4 a 8 dígitos (fora data plausível) ou token de 4 a 10 caracteres com letra e 3+ dígitos
    t = Transcricao(linhas=[comum], campos={"assunto": comum})
    assert (await _ler(valor=comum, leitor=_Leitor(t))).valor == comum


@pytest.mark.parametrize("linhas", [["Instagram", "482913"], ["Instagram", "４８２９１３"], ["Instagram", "Your code is 482.913"],
                                    ["Instagram", "G-482913"], ["Instagram", "٤٨٢٩١٣"], ["Instagram 482913 is your code"],
                                    ["Flavio Padilha", "482913", "is your verification code"]])
async def test_d3_qualquer_linha_do_recorte_com_forma_de_codigo_leva_a_triagem(linhas: list[str]) -> None:
    t = Transcricao(linhas=linhas, campos={"remetente": linhas[0]})
    erro = await _recusa("triagem", valor=linhas[0], nome="remetente", leitor=_Leitor(t))
    assert "482" not in erro.rotulo
    # o leitor que não viu a linha do código não torna o recorte suspeito
    ok = Transcricao(linhas=["Instagram"], campos={"remetente": "Instagram"})
    assert (await _ler(valor="Instagram", nome="remetente", leitor=_Leitor(ok))).valor == "Instagram"


async def test_d4_a_triagem_do_recorte_roda_antes_da_conferencia() -> None:
    # o ator leu outra coisa (`nao_confere`) e o recorte traz um código: vai para a triagem, e não para uma nova tentativa
    t = Transcricao(linhas=["Instagram", "482913"], campos={"remetente": "Instagram"})
    await _recusa("triagem", valor="outra coisa", nome="remetente", leitor=_Leitor(t))
    cortado = Transcricao(linhas=["Instagram…", "482913"], campos={"remetente": "Instagram…"}, truncado=True)
    await _recusa("triagem", valor="Instagram", nome="remetente", leitor=_Leitor(cortado))
    # sem código, a conferência continua valendo
    sem = Transcricao(linhas=["Instagram"], campos={"remetente": "Instagram"})
    await _recusa("nao_confere", valor="outra coisa", nome="remetente", leitor=_Leitor(sem))


async def test_codigo_numa_linha_do_recorte_que_nao_e_a_do_valor_tambem_recusa() -> None:
    t = Transcricao(linhas=[REMETENTE, f"Use {CODIGO} to confirm your identity"], campos={"remetente": REMETENTE})
    erro = await _recusa("triagem", valor=REMETENTE, nome="remetente", leitor=_Leitor(t))
    assert CODIGO not in erro.rotulo


def test_a_recusa_nunca_carrega_o_texto_da_transcricao() -> None:
    for codigo in RECUSAS_VISUAIS:
        assert str(LeituraVisualRecusada(codigo)) == codigo


# ================================================================== a ferramenta
def test_read_value_visual_exige_o_valor_e_so_texto() -> None:
    base = {"rationale": "r", "name": "assunto", "element_id": "e2"}
    ok = validate_call("read_value", {**base, "value": "x", "source": "visual", "value_kind": "text"})
    assert ok.source == "visual"                                              # type: ignore[attr-defined]
    assert validate_call("read_value", {**base, "value": None, "value_kind": "text"}).source == "tree"  # type: ignore[attr-defined]
    for ruim in ({"source": "visual", "value": None},                          # sem o valor do ator
                 {"source": "visual", "value": ""},
                 {"source": "visual", "value": "12", "value_kind": "number"},   # número lido da imagem é formato de código
                 {"source": "visual", "value": "http://x.co", "value_kind": "url"},
                 {"source": "visual", "value": "a", "value_kind": "list"}):
        with pytest.raises(ToolValidationError):
            validate_call("read_value", {**base, **ruim})


def test_razao_do_bloqueio_triada() -> None:
    assert razao_sem_segredo("a tela pede outro app") == "a tela pede outro app"
    omitida = razao_sem_segredo(f"Your Instagram code is {CODIGO}, não consigo seguir")
    assert CODIGO not in omitida and omitida.startswith("motivo omitido (triagem: ")


# ================================================================== no executor (QA Messenger falso, linha cega)
COMANDO = "Abra o QA Messenger e leia o remetente da primeira linha."
EVIDENCIA = "A tela exibe a caixa de entrada com a lista de conversas."
LINHA_CEGA = "conversation_row"


@pytest.fixture
def caixa_cega(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A tela inicial do QA com as linhas CEGAS (clicáveis, sem texto), o dado do app com a região declarada e o catálogo."""
    build0 = FakeQaDevice._build

    def build(self: FakeQaDevice) -> list[Node]:
        nodes = build0(self)
        if self.screen != "home":
            return nodes
        fixos = [n for n in nodes if n.rid != "conversation_name"]
        linhas = [Node("android.view.View", (0, 200 + i * 120, 720, 300 + i * 120), rid=LINHA_CEGA, clickable=True)
                  for i in range(3)]
        return [*fixos, *linhas]

    monkeypatch.setattr(FakeQaDevice, "_build", build)
    pasta = tmp_path / "apps" / QA
    pasta.mkdir(parents=True)
    (pasta / "telas.yaml").write_text(
        "app: com.pocqa.messenger\nversao: 1\nidioma_padrao: en\nsinais:\n  en:\n    nunca: 'nunca_casa_xyz'\n"
        "telas:\n  - {tela: caixa_de_entrada, tipo: autenticada, autenticada: true, ids: [conversation_list]}\n"
        "estado_conhecido:\n  telas: [caixa_de_entrada]\n"
        "leitura_visual:\n  regioes:\n    - tela: caixa_de_entrada\n      dentro_de: [conversation_list]\n"
        "      saidas: [remetente, assunto]\n", encoding="utf-8")
    monkeypatch.setattr("app.taskqueue.executor.CONHECIMENTO_DE_APPS", tmp_path / "apps")
    register(QA, CapabilityCatalog(QA, [
        Capability(key="QA_ABRIR_CAIXA", title="Abrir a caixa", goal="Ir para a caixa de entrada, sem abrir mensagem.",
                   post_kind="model_judged", post_value="caixa de entrada aberta",
                   post_description="A tela mostra a caixa de entrada.", saidas=("remetente", "assunto"),
                   max_attempts=2),
        Capability(key="QA_IR_PARA_CAIXA", title="Ir para a caixa", goal="Ir para a caixa de entrada.",
                   post_kind="model_judged", post_value="caixa de entrada aberta",
                   post_description="A tela mostra a caixa de entrada.", max_attempts=2)]))
    try:
        yield
    finally:
        unregister(QA)


def _plano(inner: Any, *passos: PlanStep) -> None:
    plan0 = inner.plan

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        base = [PlanStep(key="open_app", title="Abrir o QA Messenger", goal="Trazer o QA Messenger para o primeiro plano.",
                         postcondition=Postcondition(kind="app_foreground", value=QA, description="Em primeiro plano."),
                         timeout_s=90),
                PlanStep(key="confirm_account", title="Confirmar a conta conectada", depends_on=["open_app"],
                         goal="Confirmar na tela inicial que a conta conectada é {account_label}.",
                         postcondition=Postcondition(kind="text_visible", value="Conta: {account_label}",
                                                     description="A tela inicial mostra a conta."), timeout_s=90)]
        return p.model_copy(update={"steps": [*base, *passos]}), u

    inner.plan = plan


def _etapa(chave: str = "listar", capability: str = "QA_ABRIR_CAIXA", *, depende: str = "confirm_account",
           **extra: Any) -> PlanStep:
    return PlanStep(key=chave, title=f"Etapa {chave}", goal="g", depends_on=[depende], capability=capability,
                    max_attempts=extra.pop("max_attempts", 2),
                    postcondition=Postcondition(kind="model_judged", value="v", description="d"), **extra)


def _ator(inner: Any, roteiro: list[Any], vistos: list[str], chave: str = "listar") -> None:
    """O ator da etapa `chave`: cada item do roteiro é `dict(tool, args)` ou um callable `req -> Decision`; o último repete."""
    decide0 = inner.decide
    fila = list(roteiro)

    async def decide(req: Any) -> Any:
        if req.ctx.step_key != chave:
            return await decide0(req)
        vistos.append("\n".join(req.history))
        item = fila.pop(0) if len(fila) > 1 else fila[0]
        d = item(req) if callable(item) else item
        return Decision(tool=d["tool"], args=d["args"]), Usage()

    inner.decide = decide


def _le_visual(nome: str, valor: str) -> Any:
    def f(req: Any) -> dict[str, Any]:
        linha = next(e for e in req.screen.tree.elements if e.resource_id.endswith(LINHA_CEGA))
        return {"tool": "read_value", "args": {"rationale": "o valor da linha", "name": nome, "element_id": linha.id,
                                               "value": valor, "value_kind": "text", "source": "visual"}}
    return f


def _concluir() -> dict[str, Any]:
    return {"tool": "step_done", "args": {"rationale": "tela certa", "evidence": EVIDENCIA, "delivery_level": None}}


def _juiz(inner: Any) -> None:
    verify0 = inner.verify

    async def verify(req: Any) -> Any:
        if req.ctx.step_key == "open_app" or req.ctx.step_key == "confirm_account":
            return await verify0(req)
        return Verdict(satisfied="yes", evidence=EVIDENCIA), Usage()

    inner.verify = verify


async def _termina(h: Harness, run_id: str) -> Any:
    return await h.wait_run(run_id, timeout=90)


def _saidas(h: Harness, run_id: str) -> list[Any]:
    return list(h.state.db.query("SELECT * FROM step_outputs WHERE run_id=? ORDER BY name", (run_id,)))  # type: ignore[union-attr]


def _tudo_do_run(h: Harness, run_id: str) -> str:
    """Todo texto gravado da execução que o ator, a pessoa ou um evento poderiam ler: ações, tentativas, etapas, evidências,
    eventos e o histórico entregue ao ator (separado)."""
    db = h.state.db                                                        # type: ignore[union-attr]
    partes: list[str] = []
    for sql in ("SELECT args, result, error FROM actions WHERE attempt_id IN (SELECT id FROM attempts WHERE run_id=?)",
                "SELECT error, observed FROM attempts WHERE run_id=?", "SELECT status_detail FROM steps WHERE run_id=?",
                "SELECT note FROM evidence WHERE run_id=?", "SELECT message, data FROM events WHERE run_id=?"):
        try:
            partes += [json.dumps(dict(r), ensure_ascii=False, default=str) for r in db.query(sql, (run_id,))]
        except Exception:                                                  # noqa: BLE001 - coluna que o esquema não tem
            continue
    return "\n".join(partes)


async def test_sucesso_grava_origem_visual_sem_o_valor_na_acao_e_o_juiz_recebe_a_imagem(
        harness: Harness, caixa_cega: None) -> None:
    harness.state.cfg.file.ai.leitura_visual.enabled = True                # type: ignore[union-attr]
    inner, vistos = harness.ai.inner, []
    inner.leitura = Transcricao(linhas=[REMETENTE, ASSUNTO], campos={"remetente": REMETENTE, "assunto": ASSUNTO})
    _plano(inner, _etapa())
    _ator(inner, [_le_visual("remetente", REMETENTE), _le_visual("assunto", ASSUNTO), _concluir()], vistos)
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    assert (await _termina(harness, run.id)).status == "completed"
    linhas = {r["name"]: r for r in _saidas(harness, run.id)}
    assert set(linhas) == {"remetente", "assunto"}
    for nome, valor in (("remetente", REMETENTE), ("assunto", ASSUNTO)):
        r = linhas[nome]
        assert r["value"] == valor and r["origem"] == "visual" and r["value_kind"] == "text"
        assert r["leitor"] and len(r["frame_sha256"]) == 64 and r["evidence_id"]
    ev = harness.state.db.one("SELECT * FROM evidence WHERE id=?", (linhas["assunto"]["evidence_id"],))  # type: ignore[union-attr]
    assert ev["kind"] == "screenshot" and ev["path"] and "lido da imagem" in ev["note"]
    assert ASSUNTO not in ev["note"] and linhas["assunto"]["frame_sha256"][:8] in ev["note"]
    # a ação registra nome, tipo, tamanho, origem e ids — e `args.value` fica omitido, sem o valor em nenhum lugar
    acoes = harness.state.db.query("SELECT args, result FROM actions WHERE tool='read_value' AND status='done'")  # type: ignore[union-attr]
    assert len(acoes) == 2
    for a in acoes:
        assert "**OMITIDO**" in a["args"] and ASSUNTO not in a["args"] and REMETENTE not in a["args"]
        res = json.loads(a["result"])
        assert res["origem"] == "visual" and res["evidence_id"] and res["leitor"] and "frame_id" in res
        assert "value" not in res
    # o juiz recebeu a imagem à força na etapa com saída visual
    assert harness.ai.count("verify", step="listar", image=True) >= 1
    assert harness.ai.count("leitura") == 2                                 # contado no hub, como as outras chamadas
    # a transcrição do leitor não aparece no histórico do ator nem nos eventos; o valor do ator não vai ao leitor
    assert all(c.get("saidas") in (["remetente"], ["assunto"]) for c in harness.ai.calls if c["role"] == "leitura")
    rel = harness.state.service.report(run.id) if hasattr(harness.state, "service") else None  # type: ignore[union-attr]
    if rel is not None:
        assert "lido da imagem; conferido às cegas por" in rel["markdown"]
        assert {v["origem"] for v in rel["per_instance"][0]["values_read"]} == {"visual"}


async def test_recusa_nao_grava_e_a_transcricao_do_leitor_nunca_chega_ao_ator_nem_aos_eventos(
        harness: Harness, caixa_cega: None) -> None:
    harness.state.cfg.file.ai.leitura_visual.enabled = True                # type: ignore[union-attr]
    inner, vistos = harness.ai.inner, []
    segredo = "TRANSCRICAO-QUE-O-ATOR-NAO-PODE-VER"
    inner.leitura = Transcricao(linhas=[segredo, "outra linha"], campos={"remetente": segredo})   # discorda do ator
    _plano(inner, _etapa())
    _ator(inner, [_le_visual("remetente", REMETENTE)], vistos)             # tenta de novo, sempre igual
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    await _termina(harness, run.id)
    assert _saidas(harness, run.id) == []                                   # nada gravado
    assert harness.state.db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=? AND key='listar' "     # type: ignore[union-attr]
                                   "AND status='succeeded'", (run.id,)) == 0
    rejeitadas = harness.state.db.query("SELECT error FROM actions WHERE tool='read_value' AND status='rejected'")  # type: ignore[union-attr]
    assert rejeitadas and rejeitadas[0]["error"] == "nao_confere"
    assert {r["error"] for r in rejeitadas} <= {"nao_confere", "repetida"}   # só códigos do vocabulário fechado
    assert any("read_value REJEITADA: nao_confere" in v for v in vistos)    # o ator recebeu SÓ o código...
    assert not any(segredo in v for v in vistos)                            # ...e nunca a transcrição
    assert segredo not in _tudo_do_run(harness, run.id)
    assert harness.state.db.scalar("SELECT COUNT(*) FROM evidence WHERE run_id=? AND kind='screenshot' "  # type: ignore[union-attr]
                                   "AND note LIKE '%lido da imagem%'", (run.id,)) == 0   # o recorte recusado não é guardado


async def test_sem_leitor_e_opcao_desligada_recusam_com_o_codigo(harness: Harness, caixa_cega: None,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    inner, vistos = harness.ai.inner, []
    _plano(inner, _etapa(max_attempts=1))
    _ator(inner, [_le_visual("remetente", REMETENTE)], vistos)
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)                       # opção desligada (padrão)
    await _termina(harness, run.id)
    assert _saidas(harness, run.id) == []
    assert any("read_value REJEITADA: desligado" in v for v in vistos)
    assert harness.ai.count("leitura") == 0                                  # nenhuma chamada paga ao leitor desligado
    # ligada, mas sem leitor (provedor sem `transcribe`): sem_leitor
    harness.state.cfg.file.ai.leitura_visual.enabled = True                # type: ignore[union-attr]
    monkeypatch.delattr(type(harness.ai), "transcribe")
    vistos.clear()
    run2 = harness.run(["android-01"], command=COMANDO)
    await _termina(harness, run2.id)
    assert any("read_value REJEITADA: sem_leitor" in v for v in vistos)


async def test_elemento_com_texto_e_source_visual_grava_com_origem_arvore(harness: Harness, caixa_cega: None,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    harness.state.cfg.file.ai.leitura_visual.enabled = True                # type: ignore[union-attr]
    build1 = FakeQaDevice._build

    def com_texto(self: FakeQaDevice) -> list[Node]:
        return [Node(n.cls, n.bounds, text=("QA-002" if n.rid == LINHA_CEGA else n.text), rid=n.rid, desc=n.desc,
                     clickable=n.clickable, action=n.action, scrollable=n.scrollable) for n in build1(self)]

    monkeypatch.setattr(FakeQaDevice, "_build", com_texto)
    inner, vistos = harness.ai.inner, []
    _plano(inner, _etapa(saidas=["remetente"]))
    _ator(inner, [_le_visual("remetente", "QA-002"), _concluir()], vistos)
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    assert (await _termina(harness, run.id)).status == "completed"
    [r] = _saidas(harness, run.id)
    assert r["value"] == "QA-002" and r["origem"] == "arvore" and r["leitor"] is None and r["frame_sha256"] is None
    assert harness.ai.count("leitura") == 0                                  # a árvore tem o texto: o leitor nem é chamado


async def test_valor_visual_numa_etapa_com_efeito_espera_a_pessoa_e_navegacao_segue(harness: Harness,
                                                                                    caixa_cega: None) -> None:
    harness.state.cfg.file.ai.leitura_visual.enabled = True                # type: ignore[union-attr]
    inner, vistos = harness.ai.inner, []
    inner.leitura = Transcricao(linhas=[REMETENTE], campos={"remetente": REMETENTE})
    efeito = PlanStep(key="agir", title="Agir com o valor", goal="Escrever para {{saida:remetente}}.",
                      depends_on=["listar"], side_effect=True, commit_guard=["{{saida:remetente}}"],
                      postcondition=Postcondition(kind="model_judged", value="v", description="d"), max_attempts=1)
    _plano(inner, _etapa(saidas=["remetente"]), efeito)
    _ator(inner, [_le_visual("remetente", REMETENTE), _concluir()], vistos)
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    await _termina(harness, run.id)
    obj = harness.state.db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))   # type: ignore[union-attr]
    assert obj["status"] == "waiting_user"
    assert "valor lido da imagem precisa da sua confirmação" in obj["blocked_reason"]
    assert REMETENTE not in obj["blocked_reason"]                            # o motivo cita o nome, nunca o valor
    agir = harness.state.db.one("SELECT status, attempts FROM steps WHERE run_id=? AND key='agir'", (run.id,))  # type: ignore[union-attr]
    assert agir["status"] == "ready" and agir["attempts"] == 0              # nenhuma tentativa gasta, nada executado
    assert harness.state.db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=? AND key='listar' "      # type: ignore[union-attr]
                                   "AND status='succeeded'", (run.id,)) == 1      # a leitura em si foi comprovada


async def test_navegacao_que_usa_valor_visual_segue_sem_esperar(harness: Harness, caixa_cega: None) -> None:
    harness.state.cfg.file.ai.leitura_visual.enabled = True                # type: ignore[union-attr]
    inner, vistos = harness.ai.inner, []
    inner.leitura = Transcricao(linhas=[REMETENTE], campos={"remetente": REMETENTE})
    busca = PlanStep(key="ir", title="Ir para a caixa de {{saida:remetente}}", goal="Voltar à caixa.",
                     depends_on=["listar"], capability="QA_IR_PARA_CAIXA",
                     postcondition=Postcondition(kind="model_judged", value="v", description="d"), max_attempts=1)
    _plano(inner, _etapa(saidas=["remetente"]), busca)
    _ator(inner, [_le_visual("remetente", REMETENTE), _concluir()], vistos)
    _ator(inner, [_concluir()], [], chave="ir")
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    await _termina(harness, run.id)
    obj = harness.state.db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))   # type: ignore[union-attr]
    assert harness.state.db.one("SELECT status FROM objectives WHERE id=?", (obj["id"],))["status"] != "waiting_user"  # type: ignore[union-attr]
    assert harness.state.db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=? AND key='ir' AND status='succeeded'",   # type: ignore[union-attr]
                                   (run.id,)) == 1


async def test_triagem_na_leitura_visual_para_a_etapa_sem_nova_tentativa_e_sem_dizer_ao_ator(
        harness: Harness, caixa_cega: None) -> None:
    """A3 (ADR-009): o leitor viu código de verificação na linha. Como no caminho da árvore, a etapa vai para
    `waiting_user`, o ator NÃO tenta de novo e o histórico dele não diz que a linha tem código."""
    harness.state.cfg.file.ai.leitura_visual.enabled = True                # type: ignore[union-attr]
    inner, vistos = harness.ai.inner, []
    linha = f"Use {CODIGO} to confirm your identity"
    inner.leitura = Transcricao(linhas=[REMETENTE, linha], campos={"remetente": REMETENTE})
    _plano(inner, _etapa(max_attempts=1))
    _ator(inner, [_le_visual("remetente", REMETENTE)], vistos)           # tentaria de novo, sempre igual
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    await _termina(harness, run.id)
    db = harness.state.db                                                  # type: ignore[union-attr]
    assert _saidas(harness, run.id) == []
    assert harness.ai.count("leitura") == 1                                # uma leitura só: nada de nova tentativa
    lidos = db.query("SELECT status, error FROM actions WHERE tool='read_value'")
    assert len(lidos) == 1 and lidos[0]["status"] == "rejected"
    assert lidos[0]["error"].startswith("valor recusado pela triagem")     # o mesmo texto do caminho da árvore
    assert len(vistos) == 1                                                # o ator decidiu uma vez: não houve retorno a ele
    # o histórico do ator só traz a orientação fixa do executor: nenhuma recusa, nenhum motivo, nenhum código
    assert not any("REJEITADA" in v or "triagem" in v or CODIGO in v for v in vistos)
    assert db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=? AND key='listar' AND status='waiting_user'",
                     (run.id,)) == 1
    assert CODIGO not in _tudo_do_run(harness, run.id)


async def test_a_barreira_fora_do_app_recebe_o_valor_real_do_executor(
        harness: Harness, caixa_cega: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A7: o executor passava `fora_do_app=None` fixo. Aqui a primeira checagem (a do `elif`) diz "dentro" e a seguinte diz
    outro app: só a barreira de `ler_valor_visual` pode recusar, e ela recusa com `fora_do_app`."""
    from app.taskqueue import executor as ex

    harness.state.cfg.file.ai.leitura_visual.enabled = True                # type: ignore[union-attr]
    inner, vistos = harness.ai.inner, []
    inner.leitura = Transcricao(linhas=[REMETENTE], campos={"remetente": REMETENTE})
    estado = {"lendo": False, "depois": False}
    real = ex.StepExecutor._tela_fora_do_app                                    # noqa: SLF001

    def fora(step: Any, obs: Any, pacote: Any) -> Any:
        if estado["depois"]:
            return "com.outro.app"
        if estado["lendo"]:
            estado["lendo"], estado["depois"] = False, True                 # a checagem do `elif`: ainda dentro
            return None
        return real(step, obs, pacote)

    monkeypatch.setattr(ex.StepExecutor, "_tela_fora_do_app", staticmethod(fora))
    recebidos: list[Any] = []
    de_verdade = ex.ler_valor_visual

    async def espia(**kw: Any) -> Any:
        recebidos.append(kw["fora_do_app"])
        return await de_verdade(**kw)

    monkeypatch.setattr(ex, "ler_valor_visual", espia)
    ler = _le_visual("remetente", REMETENTE)

    def armado(req: Any) -> Any:
        estado["lendo"] = True
        return ler(req)

    _plano(inner, _etapa(max_attempts=1))
    _ator(inner, [armado], vistos)
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    await _termina(harness, run.id)
    assert recebidos and recebidos[0] == "com.outro.app"
    rejeitadas = harness.state.db.query("SELECT error FROM actions WHERE tool='read_value' AND status='rejected'")  # type: ignore[union-attr]
    assert rejeitadas and rejeitadas[0]["error"] == "fora_do_app"
    assert harness.ai.count("leitura") == 0                                 # a barreira barata fechou antes do leitor
    assert _saidas(harness, run.id) == []


async def test_orcamento_do_leitor_segue_o_desfecho_do_ator_e_so_a_falha_do_provedor_vira_leitor_falhou(
        harness: Harness, caixa_cega: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """C4: `kind=budget` na chamada do leitor propaga (etapa falha como no caminho do ator); `invalid_output` e erro de
    provedor viram a recusa `leitor_falhou`, que o ator vê só como código."""
    from app.planning.provider import AIError

    harness.state.cfg.file.ai.leitura_visual.enabled = True                # type: ignore[union-attr]
    inner, vistos = harness.ai.inner, []

    def falha(kind: str) -> Any:
        async def transcribe(req: Any) -> Any:
            raise AIError("falha simulada do leitor", kind=kind)
        return transcribe

    for kind, esperado in (("invalid_output", "leitor_falhou"), ("error", "leitor_falhou")):
        inner.transcribe = falha(kind)
        vistos.clear()
        _plano(inner, _etapa(max_attempts=1))
        _ator(inner, [_le_visual("remetente", REMETENTE)], vistos)
        _juiz(inner)
        run = harness.run(["android-01"], command=COMANDO)
        await _termina(harness, run.id)
        assert any(f"read_value REJEITADA: {esperado}" in v for v in vistos), (kind, vistos)
    # orçamento: NÃO é `leitor_falhou`; a etapa falha com o motivo do orçamento e o ator não recebe recusa nenhuma
    inner.transcribe = falha("budget")
    vistos.clear()
    _plano(inner, _etapa(max_attempts=1))
    _ator(inner, [_le_visual("remetente", REMETENTE)], vistos)
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    await _termina(harness, run.id)
    db = harness.state.db                                                  # type: ignore[union-attr]
    assert not any("leitor_falhou" in v for v in vistos)
    linha = db.one("SELECT status, status_detail FROM steps WHERE run_id=? AND key='listar'", (run.id,))
    assert linha["status"] == "failed" and "falha simulada do leitor" in (linha["status_detail"] or "")


# ================================================================== o que vai junto
async def test_step_blocked_com_codigo_vai_redigido_aos_quatro_destinos(harness: Harness, caixa_cega: None) -> None:
    inner, vistos = harness.ai.inner, []
    razao = f"A tela mostra o código de verificação {CODIGO} e não consigo continuar"
    bloqueio = {"tool": "step_blocked", "args": {"rationale": "x", "kind": "other", "reason": razao, "needs_user": True}}
    _plano(inner, _etapa("listar", "QA_IR_PARA_CAIXA", max_attempts=1))
    _ator(inner, [bloqueio], vistos)
    run = harness.run(["android-01"], command=COMANDO)
    await _termina(harness, run.id)
    db = harness.state.db                                                    # type: ignore[union-attr]
    linha = db.one("SELECT status, status_detail FROM steps WHERE run_id=? AND key='listar'", (run.id,))
    assert "motivo omitido (triagem:" in (linha["status_detail"] or "")
    assert CODIGO not in _tudo_do_run(harness, run.id)                       # nem na etapa, nem na tentativa, nem na
    #                                                                          evidência, nem no evento `decision`, nem na ação
    assert db.scalar("SELECT COUNT(*) FROM events WHERE run_id=? AND message LIKE '%motivo omitido%'", (run.id,)) >= 1
    assert db.scalar("SELECT COUNT(*) FROM evidence WHERE run_id=? AND note LIKE '%motivo omitido%'", (run.id,)) >= 1


async def test_conta_propria_de_recusas_leva_a_fail_or_retry_mesmo_com_observe_e_find_no_meio(
        harness: Harness, caixa_cega: None) -> None:
    """r-…-178742: o ator alternava `step_done` recusado com `observe_screen` e `find_element`, que zeram `errors_in_row`,
    e só parava em 14 chamadas. Com 4 recusas da barreira de saídas, a etapa vai para `fail_or_retry`."""
    inner, vistos = harness.ai.inner, []
    observar = {"tool": "observe_screen", "args": {"rationale": "olhar", "need_image": False}}
    achar = {"tool": "find_element", "args": {"rationale": "achar", "query": "caixa"}}
    _plano(inner, _etapa(max_attempts=1))
    _ator(inner, [_concluir(), observar, _concluir(), achar, _concluir(), observar, _concluir(), observar, _concluir()], vistos)
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    await _termina(harness, run.id)
    linha = harness.state.db.one("SELECT status, status_detail, attempts FROM steps WHERE run_id=? AND key='listar'",  # type: ignore[union-attr]
                                 (run.id,))
    assert linha["status"] == "failed" and "remetente" in (linha["status_detail"] or "")
    # por tentativa: exatamente 4 `step_done` recusados (e `observe_screen`/`find_element` no meio), e a etapa para
    contas = [r["n"] for r in harness.state.db.query(                       # type: ignore[union-attr]
        "SELECT COUNT(*) n FROM actions WHERE tool='step_done' AND status='rejected' GROUP BY attempt_id")]
    assert contas and max(contas) == 4
    assert harness.state.db.scalar("SELECT COUNT(*) FROM actions WHERE tool IN ('observe_screen','find_element')") >= 3  # type: ignore[union-attr]
    assert _saidas(harness, run.id) == []

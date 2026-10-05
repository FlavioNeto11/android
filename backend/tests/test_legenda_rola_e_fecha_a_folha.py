"""29.87: na tela da legenda do Instagram, a linha "Add AI label" fica abaixo da dobra e, na 1ª vez, a folha "Sharing
posts" abre por cima. Achados da leitura real do android-13 (05/10, 01:24Z–01:30Z, sem Share e sem tocar no
interruptor; capturas em `data/diag-29-79-pos-33`, fora do Git).

Duas regras do executor, sem IA:
- a folha DECLARADA no conhecimento do app (`telas.yaml`, `fechar: toque_fora`) fecha com um toque no fundo
  escurecido acima dela, nunca em "OK" nem em "Manage settings" (aceitar aviso numa conta real é do dono); se ela não
  fecha, a etapa para numa pessoa sem mais toque;
- a linha do interruptor exigido e AUSENTE é trazida à tela em passos de 25 % da área rolável, para o ator vê-la e
  ligá-la e para a guarda do Share (29.79) não ler "ausente". O passo não tira a legenda da tela (a guarda `{content}`).

As funções puras batem nas árvores REAIS do 13 (`tests/fixtures/instagram_legenda/`, só ids, textos de interface e
coordenadas; a sugestão de música trocada por "Música sugerida"). O laço do executor roda no Harness (porta base 5640)
com um Instagram falso de geometria igual à medida. Nível `simulated`; nada foi publicado.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import pytest
import yaml

from app.automation import conhecimento_de_telas as telas
from app.automation.hierarchy import UiElement, UiTree
from app.planning.provider import Decision, DecisionRequest, Usage
from app.taskqueue.executor import (LIMITE_DE_FOLHAS, estado_do_interruptor, rejeicao_do_interruptor,
                                    rolagem_ate_o_interruptor)

from .conftest import CountingProvider, Harness
from .fake_instagram import PKG, Node
from .test_rotulo_ia_no_executor import (IID, LEGENDA, AtorQueTocaNoShare, InstagramComLegenda, _acoes, _estado,
                                         _publicar)

FIXTURES = Path(__file__).parent / "fixtures" / "instagram_legenda"
TELAS_DO_INSTAGRAM = Path(__file__).resolve().parents[1] / "app" / "conhecimento" / "apps" / PKG / "telas.yaml"
SWITCH = ["rotulo_ia:text==Add AI label"]
SHARE = "id=share_footer_button"
_TV, _BT, _SW, _FL = "android.widget.TextView", "android.widget.Button", "android.view.View", "android.widget.FrameLayout"


def _arvore(nome: str) -> UiTree:
    dados = json.loads((FIXTURES / f"{nome}.json").read_text(encoding="utf-8"))
    campos = UiElement.__dataclass_fields__
    return UiTree(elements=[UiElement(**{k: (tuple(v) if k == "bounds" else v) for k, v in e.items() if k in campos})
                            for e in dados["elements"]], packages=[PKG], sensitive=False)


def _conhecimento() -> telas.ConhecimentoDeTelas:
    return telas.carregar(TELAS_DO_INSTAGRAM)


# ==================================================================== as árvores reais do android-13
def test_as_arvores_reais_nao_trazem_dado_de_persona() -> None:
    for arquivo in FIXTURES.glob("*.json"):
        texto = arquivo.read_text(encoding="utf-8")
        assert "@" not in texto.replace("@2131955697", ""), arquivo.name      # o desc sem resolver do fundo, só
        assert "Seduce" not in texto and "Carvalho" not in texto, arquivo.name


def test_a_folha_real_e_reconhecida_e_fecha_fora_dela_longe_do_ok() -> None:
    k = _conhecimento()
    folha = _arvore("legenda_com_folha_sharing_posts")
    vista = telas.classificar(k, folha, package=PKG)
    assert vista.tela == "aviso_de_compartilhar" and vista.tipo == "intersticial"
    regra = k.regra(vista.tela)
    assert regra is not None and regra.fechar_fora == ("layout_container_bottom_sheet", "background_dimmer")
    ponto = telas.toque_fora_da_folha(regra, folha)
    assert ponto == (360, 154)                              # o fundo vai de y=48; a folha começa em y=260
    x, y = ponto
    no_ponto = [e for e in folha.elements if e.bounds[0] <= x <= e.bounds[2] and e.bounds[1] <= y <= e.bounds[3]]
    assert any(e.resource_id.endswith("background_dimmer") for e in no_ponto)
    assert not any((e.text or e.desc).strip().lower() in ("ok", "manage settings") for e in no_ponto)
    for nome in ("legenda_linha_abaixo_da_dobra", "legenda_linha_visivel"):
        assert telas.classificar(k, _arvore(nome), package=PKG).tela != "aviso_de_compartilhar", nome


def test_a_folha_real_em_portugues_tambem_casa_pelos_ids_e_pelo_texto_de_hoje() -> None:
    assert telas.classificar(_conhecimento(), _arvore("legenda_com_folha_sharing_posts"), package=PKG,
                             locale="pt").tela == "aviso_de_compartilhar"


def test_a_sessao_nao_acharia_botao_para_dispensar_a_folha() -> None:
    """A leitura da conta (`sessao.yaml`) dispensa telas com "ok" entre os rótulos globais: na folha real o "OK" é
    um TextView NÃO clicável, então nada é escolhido — e assim tem de seguir."""
    from app.integrations.app_declarado import conhecimento as app_declarado
    k = app_declarado.do_app(PKG)
    botao = k.botao_de_dispensa(_arvore("legenda_com_folha_sharing_posts"))
    assert botao is None or (botao.text or botao.desc).strip().lower() not in ("ok", "manage settings")


def test_rolagem_so_na_tela_do_share_com_o_rotulo_pedido_e_a_linha_ausente() -> None:
    abaixo, visivel = _arvore("legenda_linha_abaixo_da_dobra"), _arvore("legenda_linha_visivel")
    assert estado_do_interruptor(abaixo, "text==Add AI label") == "ausente"
    # Sem rolar, a guarda do 29.79 recusa o Share: é o que travaria a 1ª publicação real.
    assert rejeicao_do_interruptor(SWITCH, {"rotulo_ia": "true"}, abaixo)
    arrasto = rolagem_ate_o_interruptor(SWITCH, SHARE, {"rotulo_ia": "true"}, abaixo)
    assert arrasto is not None
    x1, y1, x2, y2 = arrasto
    assert x1 == x2 and 0 < y1 - y2 <= 0.26 * (1115 - 160)               # um passo de 25 % da área rolável real
    assert not any((e.clickable or e.editable) and e.bounds[0] <= x1 <= e.bounds[2] and e.bounds[1] <= y1 <= e.bounds[3]
                   for e in abaixo.elements)                              # começa onde nada se toca
    assert rolagem_ate_o_interruptor(SWITCH, SHARE, {"rotulo_ia": "false"}, abaixo) is None
    assert rolagem_ate_o_interruptor(SWITCH, "id=nao_existe", {"rotulo_ia": "true"}, abaixo) is None
    # Na árvore real depois da rolagem a linha está à vista: nada a rolar, e o interruptor é lido "desligado".
    assert rolagem_ate_o_interruptor(SWITCH, SHARE, {"rotulo_ia": "true"}, visivel) is None
    assert estado_do_interruptor(visivel, "text==Add AI label") == "desligado"


# ==================================================================== a carga do conhecimento
def _com_regra(extra: dict[str, object]) -> dict[str, object]:
    dados = yaml.safe_load(TELAS_DO_INSTAGRAM.read_text(encoding="utf-8"))
    dados["telas"].insert(0, {"tela": "x", "tipo": "intersticial", "ids_todos": ["a", "b"], "razao": "x", **extra})
    return dados


@pytest.mark.parametrize("extra, motivo", [
    ({"nunca": ["OK"]}, "`nunca` exige `fechar`"),
    ({"fechar": {"toque_fora": {"folha": "f", "fundo": "g"}}}, "`fechar` exige `nunca`"),
    ({"fechar": {"voltar": True}, "nunca": ["OK"]}, "toque_fora"),
    ({"fechar": {"toque_fora": {"folha": "f"}}, "nunca": ["OK"]}, "folha"),
])
def test_a_carga_recusa_fechamento_mal_declarado(extra: dict[str, object], motivo: str) -> None:
    with pytest.raises(telas.ConhecimentoInvalido, match=motivo):
        telas.de_dados(_com_regra(extra))


def test_fechar_so_em_tela_intermediaria() -> None:
    dados = _com_regra({"fechar": {"toque_fora": {"folha": "f", "fundo": "g"}}, "nunca": ["OK"]})
    dados["telas"][0]["tipo"] = "autenticada"
    with pytest.raises(telas.ConhecimentoInvalido, match="intersticial"):
        telas.de_dados(dados)


# ==================================================================== o laço do executor, com a geometria medida
#: A área rolável e as posições da tela da legenda do android-13 (05/10), antes de rolar.
AREA = (0, 160, 720, 1115)
LEGENDA_Y = (704, 800)
LINHA_Y = (1409, 1447)
SWITCH_Y = (1399, 1495)


@dataclass
class InstagramComDobra(InstagramComLegenda):
    """A tela da legenda com a geometria medida: a linha do rótulo só existe na árvore quando cabe na área rolável
    (como o uiautomator a dá), o arrasto move o conteúdo, e a folha "Sharing posts" pode estar aberta por cima.
    `teimosa`: a folha não fecha com o toque fora. `toques_na_folha` registra todo toque que caiu NA folha."""

    rolado: int = 0
    folha: bool = False
    teimosa: bool = False
    toques_fora: list[str] = field(default_factory=list)
    toques_na_folha: list[str] = field(default_factory=list)
    arrastos: list[int] = field(default_factory=list)

    def _visivel(self, y1: int, y2: int) -> bool:
        return y1 - self.rolado >= AREA[1] and y2 - self.rolado <= AREA[3]

    def _build(self) -> list[Node]:
        if self.screen != "legenda":
            return super()._build()
        r = self.rolado
        nos = [Node(_FL, AREA, rid="scroll_view", scrollable=True)]
        if self._visivel(*LEGENDA_Y):
            nos.append(Node(_TV, (32, LEGENDA_Y[0] - r, 688, LEGENDA_Y[1] - r), text=LEGENDA,
                            rid="caption_input_text_view", clickable=True, editable=True))
        if self._visivel(*SWITCH_Y):
            nos += [Node(_TV, (104, LINHA_Y[0] - r, 282, LINHA_Y[1] - r), text="Add AI label"),
                    Node(_SW, (584, SWITCH_Y[0] - r, 688, SWITCH_Y[1] - r), clickable=True, action="rotulo")]
        nos.append(Node(_BT, (32, 1144, 688, 1232), desc="Share", rid="share_footer_button", clickable=True,
                        action="share"))
        if self.folha:
            nos += [Node(_FL, (0, 48, 720, 1232), rid="bottom_sheet_container"),
                    Node(_BT, (0, 48, 720, 1232), rid="background_dimmer", clickable=True, action="fundo"),
                    Node(_FL, (0, 260, 720, 1232), rid="layout_container_bottom_sheet", clickable=True),
                    Node(_TV, (64, 378, 357, 431), text="Sharing posts"),
                    Node(_TV, (341, 968, 379, 1002), text="OK", rid="ig_text"),
                    Node(_TV, (250, 1055, 471, 1089), text="Manage settings", rid="ig_text")]
        return nos

    def tap(self, x: int, y: int) -> None:
        if self.screen == "legenda" and self.folha:
            if y >= 260:                                       # por cima de tudo: o toque é da folha
                self.toques_na_folha.append(f"{x},{y}")
                return
            self.toques_fora.append(f"{x},{y}")
            self.folha = self.teimosa
            return
        if self.screen == "legenda":
            switch = next((n for n in self._build() if n.action == "rotulo"), None)
            if switch is not None and switch.bounds[0] <= x <= switch.bounds[2] \
                    and switch.bounds[1] <= y <= switch.bounds[3]:
                self.calls.append(f"tap:{x},{y}")
                self.rotulo_ligado = not self.rotulo_ligado
                return
        super().tap(x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        super().swipe(x1, y1, x2, y2, duration_ms)
        if self.screen == "legenda" and not self.folha:
            self.arrastos.append(y1 - y2)
            self.rolado = max(0, min(700, self.rolado + (y1 - y2)))


class AtorQueLigaORotulo(AtorQueTocaNoShare):
    """Liga o interruptor quando o vê desligado, e só então toca no Share. Anota se algum dia viu a folha."""

    name = "ator-que-liga-o-rotulo"

    def __init__(self) -> None:
        super().__init__("true")
        self.viu_a_folha = False

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        tree: UiTree = req.screen.tree
        if tree.contains_text("Sharing posts"):
            self.viu_a_folha = True
        if not req.ctx.commit_done and estado_do_interruptor(tree, "text==Add AI label") == "desligado":
            linha = tree.find_selector("text==Add AI label")[0]
            switch = next(e for e in tree.elements if e.clickable and e.bounds[0] >= linha.bounds[2]
                          and e.bounds[1] < linha.bounds[3] and e.bounds[3] > linha.bounds[1])
            return Decision(tool="tap", args={"rationale": "[roteiro] ligar o rótulo", "element_id": switch.id,
                                              "x": None, "y": None, "is_commit_action": False}), Usage()
        return await super().decide(req)


@asynccontextmanager
async def _parque_com_dobra(tmp_path: Path, *, folha: bool, teimosa: bool = False,
                            ator: AtorQueLigaORotulo) -> AsyncIterator[Harness]:
    """O `_parque` do 29.79, com a tela da legenda medida no android-13."""
    h = Harness(tmp_path, 1, factory=lambda rt: InstagramComDobra(account="eu.teste", screen="legenda",
                                                                  folha=folha, teimosa=teimosa))
    h.ai = CountingProvider(ator)
    h.encurtar_verificacao()
    await h.boot()
    await h.medir_a_internet()
    s = _estado(h)
    s.db.execute("UPDATE instances SET app_id='instagram', account_label='eu.teste' WHERE id=?", (IID,))
    s.devices.get(IID).app_id = "instagram"
    s.scheduler.policy_gate = None          # a aprovação do CREATE_POST não é o assunto (como no 29.79)
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


def _fake(h: Harness) -> InstagramComDobra:
    fake = h.fakes[IID]
    assert isinstance(fake, InstagramComDobra)
    return fake


async def test_a_linha_abaixo_da_dobra_aparece_pela_regra_e_o_share_sai_com_o_rotulo(tmp_path: Path) -> None:
    ator = AtorQueLigaORotulo()
    async with _parque_com_dobra(tmp_path, folha=False, ator=ator) as h:
        etapa = await _publicar(h)
        fake = _fake(h)
        assert fake.arrastos == [238, 238]                   # dois passos de 25 % até a linha caber na área
        assert fake.rotulo_ligado and len(fake.shares) == 1   # o ator ligou o interruptor e o Share saiu
        acoes = _acoes(h, etapa["id"])
        assert [a["source"] for a in acoes][:2] == ["regra", "regra"]    # a rolagem não gastou o modelo
        assert not any(a["status"] == "rejected" for a in acoes)         # a guarda não precisou recusar
        notas = [r["note"] for r in _estado(h).db.query("SELECT note FROM evidence WHERE step_id=?", (etapa["id"],))]
        assert any("Conferência antes do efeito externo" in (n or "") for n in notas)   # a legenda seguiu à vista


async def test_a_folha_fecha_fora_dela_sem_ok_e_sem_o_ator_a_ver(tmp_path: Path) -> None:
    ator = AtorQueLigaORotulo()
    async with _parque_com_dobra(tmp_path, folha=True, ator=ator) as h:
        etapa = await _publicar(h)
        fake = _fake(h)
        assert len(fake.toques_fora) == 1 and fake.toques_na_folha == []          # nenhum toque em OK nem na folha
        x, y = (int(v) for v in fake.toques_fora[0].split(","))
        assert 48 <= y < 260                                                        # no fundo, acima da folha
        assert not ator.viu_a_folha                                                 # o modelo nunca a recebeu
        assert fake.rotulo_ligado and len(fake.shares) == 1
        assert _acoes(h, etapa["id"])[0]["source"] == "regra"


async def test_a_folha_que_nao_fecha_para_numa_pessoa_sem_mais_toque(tmp_path: Path) -> None:
    ator = AtorQueLigaORotulo()
    async with _parque_com_dobra(tmp_path, folha=True, teimosa=True, ator=ator) as h:
        etapa = await _publicar(h)
        fake = _fake(h)
        assert etapa["status"] == "waiting_user", (etapa["status"], etapa["status_detail"])
        assert "não fechou" in (etapa["status_detail"] or "")
        assert len(fake.toques_fora) == LIMITE_DE_FOLHAS and fake.toques_na_folha == []
        assert fake.shares == [] and not fake.rotulo_ligado
        assert h.ai.count("decide") == 0 and not ator.viu_a_folha

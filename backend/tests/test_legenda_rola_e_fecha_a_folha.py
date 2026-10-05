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
coordenadas; as sugestões de música trocadas por "Artista · Faixa"). O laço do executor roda no Harness (porta base 5640)
com um Instagram falso de geometria igual à medida. Nível `simulated`; nada foi publicado.
"""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, AsyncIterator

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
        for fora in ("Seduce", "Drake", "God's Plan", "Carvalho", "Andr"):       # música sugerida e conta do 13
            assert fora not in texto, (arquivo.name, fora)


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


def test_a_sessao_nao_toca_no_ok_da_folha_nem_quando_ele_e_clicavel() -> None:
    """D1 da revisão: numa variante da folha real em que o "OK" é um botão CLICÁVEL, a dispensa não o toca (a regra da
    tela declara `nunca`). 29.90: "ok" saiu dos rótulos globais de recusa, então fora da folha ele também não é
    dispensa (é aceite)."""
    from dataclasses import replace
    from app.integrations.app_declarado import conhecimento as app_declarado
    k = app_declarado.do_app(PKG)
    folha = _arvore("legenda_com_folha_sharing_posts")
    com_ok_clicavel = UiTree(elements=[replace(e, clickable=True) if e.text == "OK" else e for e in folha.elements],
                             packages=folha.packages, sensitive=False)
    assert any(e.text == "OK" and e.clickable for e in com_ok_clicavel.elements)
    assert k.botao_de_dispensa(com_ok_clicavel) is None
    so_o_ok = UiTree(elements=[e for e in com_ok_clicavel.elements if e.text == "OK"], packages=folha.packages,
                     sensitive=False)
    assert k.botao_de_dispensa(so_o_ok) is None          # sem a folha reconhecida: "ok" não é rótulo de recusa


def test_o_nunca_vale_em_qualquer_idioma_declarado() -> None:
    """29.90 (D1b): a folha em português só casa na tabela `pt`; quem pergunta sem idioma (a dispensa da sessão) cairia
    na `en` e não a reconheceria. "Nunca tocar" vale em todas as tabelas."""
    from dataclasses import replace
    traducao = {"Sharing posts": "Compartilhamento de publicações", "Manage settings": "Gerenciar configurações"}
    folha = _arvore("legenda_com_folha_sharing_posts")
    em_pt = UiTree(elements=[replace(e, text=traducao.get(e.text, e.text)) for e in folha.elements],
                   packages=folha.packages, sensitive=False)
    assert telas.classificar(_conhecimento(), em_pt, package=PKG).tela != "aviso_de_compartilhar"   # só a `en`
    gerenciar = next(e for e in em_pt.elements if e.text == "Gerenciar configurações")
    assert telas.proibido_na_tela(_conhecimento(), em_pt, gerenciar)
    assert not telas.proibido_na_tela(_conhecimento(), em_pt, next(e for e in em_pt.elements if e.text == "Share"))


def test_a_sessao_nao_acharia_botao_para_dispensar_a_folha() -> None:
    """A leitura da conta (`sessao.yaml`) dispensa telas pelos rótulos globais de recusa; na folha real nada é
    escolhido (o "OK" é um TextView não clicável e, desde o 29.90, nem é rótulo de recusa) — e assim tem de seguir."""
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


def test_telas_invalido_avisa_uma_vez_por_modificacao(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """29.90 (L2): o executor pergunta a cada volta; o `telas.yaml` inválido devolve `None` e avisa no log uma vez por
    modificação do arquivo, não a cada volta. Arquivo novo (outra data) avisa de novo."""
    import os
    arquivo = tmp_path / "telas.yaml"
    arquivo.write_text("telas: [", encoding="utf-8")
    with caplog.at_level("WARNING", logger=telas.__name__):
        assert [telas.da_pasta_por_data(tmp_path) for _ in range(3)] == [None, None, None]
        assert len([r for r in caplog.records if "inválido" in r.getMessage()]) == 1
        os.utime(arquivo, ns=(arquivo.stat().st_atime_ns, arquivo.stat().st_mtime_ns + 1_000_000_000))
        assert telas.da_pasta_por_data(tmp_path) is None
        assert len([r for r in caplog.records if "inválido" in r.getMessage()]) == 2
    assert telas.da_pasta_por_data(tmp_path / "nenhuma") is None


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
                            ator: AtorQueLigaORotulo, classe: type[InstagramComDobra] | None = None,
                            ) -> AsyncIterator[Harness]:
    """O `_parque` do 29.79, com a tela da legenda medida no android-13."""
    fabrica = classe or InstagramComDobra
    h = Harness(tmp_path, 1, factory=lambda rt: fabrica(account="eu.teste", screen="legenda",
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
        # 29.90: a parada sai num `step.updated` com o tipo de falha estável, para a regra de aviso consumir
        import json
        tipos = [json.loads(r["data"] or "{}").get("failure_kind")
                 for r in _estado(h).db.query("SELECT data FROM events WHERE kind='step.updated' AND step_id=?",
                                              (etapa["id"],))]
        assert "aviso_do_app" in tipos, tipos


class AtorQueSoObserva(AtorQueLigaORotulo):
    """Nunca conclui: só olha a tela (`observe_screen`, fora do detector de ciclo), gastando as ações da etapa."""

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        return Decision(tool="observe_screen", args={"rationale": "[roteiro] só olhar"}), Usage()


async def test_o_teto_de_acoes_do_ator_falha_a_etapa_sem_ir_a_verificacao(tmp_path: Path) -> None:
    """E1 da revisão do 29.87: o laço ganhou voltas para as ações da regra, e o `break` do teto do ator saía dele sem
    passar pelo `else`. A etapa que esgota as ações tem de falhar com o motivo do teto, sem verificação nenhuma."""
    ator = AtorQueSoObserva()
    async with _parque_com_dobra(tmp_path, folha=False, ator=ator) as h:
        etapa = await _publicar(h)
        assert etapa["status"] in ("failed", "waiting_user"), (etapa["status"], etapa["status_detail"])
        assert re.search(r"Limite de \d+ ações por etapa", etapa["status_detail"] or ""), etapa["status_detail"]
        assert h.ai.count("verify") == 0                         # nada de juiz para a etapa que não disse "pronto"
        assert _fake(h).shares == []


class AtorQueVeAFolhaAbrirNoShare(AtorQueLigaORotulo):
    """No 1º Share que decide, a folha "Sharing posts" abre no aparelho DEPOIS da leitura que a guarda confere: o
    instante entre a conferência e o toque (29.90)."""

    def __init__(self) -> None:
        super().__init__()
        self.fake: InstagramComDobra | None = None
        self.abriu = False

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        decisao, uso = await super().decide(req)
        if decisao.args.get("is_commit_action") and not self.abriu and self.fake is not None:
            self.abriu = True
            self.fake.folha = True
        return decisao, uso


async def test_a_folha_que_abre_entre_a_guarda_e_o_share_nao_recebe_o_toque(tmp_path: Path) -> None:
    """29.90: a releitura logo antes do toque de efeito vê a folha por cima do Share; o toque não sai, a regra fecha a
    folha fora dela e o Share sai depois, uma vez. Sem a releitura, o toque nas coordenadas do Share cairia na folha."""
    ator = AtorQueVeAFolhaAbrirNoShare()
    async with _parque_com_dobra(tmp_path, folha=False, ator=ator) as h:
        ator.fake = _fake(h)
        etapa = await _publicar(h)
        fake = _fake(h)
        assert ator.abriu
        assert fake.toques_na_folha == []                           # nada caiu na folha (nem no OK)
        assert len(fake.toques_fora) == 1 and len(fake.shares) == 1 and fake.rotulo_ligado
        recusadas = [a for a in _acoes(h, etapa["id"]) if a["status"] == "rejected"]
        assert len(recusadas) == 1 and "tela mudou antes do toque [cobertura]" in (recusadas[0]["error"] or "")


def test_cobertura_nova_no_ponto_e_pela_arvore_medida() -> None:
    """A folha real por cima do Share: o acerto por área ainda acharia o texto "Share" embaixo; o critério é o
    clicável que apareceu. A mesma árvore relida, sem nada novo, deixa tocar."""
    from dataclasses import replace
    from app.taskqueue.executor import MUDANCA_FORA_DO_LUGAR, MUDANCA_POR_CIMA, cobertura_nova_no_ponto
    antes = _arvore("legenda_linha_visivel")
    depois = _arvore("legenda_com_folha_sharing_posts")
    share = next(e for e in antes.elements if e.resource_id.endswith("share_footer_button"))
    assert cobertura_nova_no_ponto(antes, antes, share.center, share) is None
    assert cobertura_nova_no_ponto(antes, depois, share.center, share)[0] == MUDANCA_POR_CIMA
    # D2-B1: toque por x,y sem elemento no ponto: a cobertura nova ainda segura o toque
    assert cobertura_nova_no_ponto(antes, depois, share.center, None)[0] == MUDANCA_POR_CIMA
    assert cobertura_nova_no_ponto(antes, antes, share.center, None) is None
    # o alvo que só mudou de lugar (1 px) tem motivo próprio, para a contagem da janela do deploy 35
    x1, y1, x2, y2 = share.bounds
    movido = UiTree(elements=[replace(e, bounds=(x1, y1 + 1, x2, y2 + 1)) if e is share else e
                              for e in antes.elements], packages=antes.packages, sensitive=False)
    assert cobertura_nova_no_ponto(antes, movido, share.center, share)[0] == MUDANCA_FORA_DO_LUGAR


_EM_PORTUGUES = {"Sharing posts": "Compartilhamento de publicações", "Manage settings": "Gerenciar configurações"}


@dataclass
class InstagramComFolhaEmPortugues(InstagramComDobra):
    """A mesma folha, com os textos em português: só a tabela `pt` do `telas.yaml` a reconhece."""

    def _build(self) -> list[Node]:
        return [replace(n, text=_EM_PORTUGUES.get(n.text, n.text)) for n in super()._build()]


def test_a_regra_de_fechar_reconhece_a_folha_em_qualquer_idioma() -> None:
    """D1c: quem fecha a folha não sabe o idioma da tela; a tabela padrão (`en`) não reconhece a folha em português."""
    folha = _arvore("legenda_com_folha_sharing_posts")
    em_pt = UiTree(elements=[replace(e, text=_EM_PORTUGUES.get(e.text, e.text)) for e in folha.elements],
                   packages=folha.packages, sensitive=False)
    regra = telas.regra_de_fechar(_conhecimento(), em_pt, package=PKG)
    assert regra is not None and regra.tela == "aviso_de_compartilhar"
    assert telas.regra_de_fechar(_conhecimento(), _arvore("legenda_linha_visivel"), package=PKG) is None


async def test_a_folha_em_portugues_fecha_pela_regra_sem_o_ator_a_ver(tmp_path: Path) -> None:
    """D1c no laço: a folha em português fecha com o toque fora dela, antes da receita e do ator."""
    ator = AtorQueLigaORotulo()
    async with _parque_com_dobra(tmp_path, folha=True, ator=ator, classe=InstagramComFolhaEmPortugues) as h:
        await _publicar(h)
        fake = _fake(h)
        assert len(fake.toques_fora) == 1 and fake.toques_na_folha == []
        assert fake.rotulo_ligado and len(fake.shares) == 1


async def _tres_recusas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tipo: str) -> tuple[Any, InstagramComDobra]:
    """A releitura antes do toque acha SEMPRE a mesma mudança (`tipo`): a 3ª recusa decide o desfecho (D5)."""
    from app.taskqueue import executor as modulo
    monkeypatch.setattr(modulo, "cobertura_nova_no_ponto", lambda antes, depois, ponto, alvo: (tipo, f"[simulado] {tipo}"))
    ator = AtorQueLigaORotulo()
    async with _parque_com_dobra(tmp_path, folha=False, ator=ator) as h:
        etapa = await _publicar(h)
        return etapa, _fake(h)


async def test_tres_coberturas_novas_param_numa_pessoa_como_aviso_do_app(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """D5: um clicável NOVO por cima do botão de efeito, três vezes, é um aviso que o app não declara. A etapa para em
    `waiting_user` sem nova navegação, com o tipo `aviso_do_app`; nada foi tocado."""
    from app.taskqueue.executor import MUDANCA_POR_CIMA
    etapa, fake = await _tres_recusas(tmp_path, monkeypatch, MUDANCA_POR_CIMA)
    assert etapa["status"] == "waiting_user", (etapa["status"], etapa["status_detail"])
    assert "Um aviso cobre o botão de efeito" in (etapa["status_detail"] or "")
    assert fake.shares == []


async def test_tres_alvos_movidos_falham_e_repetem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D5: só o alvo fora do lugar, três vezes, é tela se mexendo: `fail_or_retry`, como antes; nada foi tocado."""
    from app.taskqueue.executor import MUDANCA_FORA_DO_LUGAR
    etapa, fake = await _tres_recusas(tmp_path, monkeypatch, MUDANCA_FORA_DO_LUGAR)
    assert etapa["status"] in ("failed", "uncertain"), (etapa["status"], etapa["status_detail"])
    assert "A tela mudou entre a conferência e o toque de efeito" in (etapa["status_detail"] or "")
    assert fake.shares == []

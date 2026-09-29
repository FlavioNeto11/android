"""Identidade da publicação alvo (C10): curtir e comentar só no post cuja legenda o pedido citou.

Execuções reais r-20260928165254-e31953 e r-20260928195344-02ee9e (android-06). O pedido era "no post do perfil
@anarabottinipsicopedagoga que contém o texto 'Ainda sobre Setembro Amarelo 2024'"; o plano guardou o texto só em
`target` (prosa para o ator). A pós-condição de OPEN_POST (título "Posts") vale para QUALQUER publicação aberta,
LIKE_POST tocava o primeiro `desc==Like` da tela (a receita v1 repetiu o toque em e26) e a prova `desc==Liked`
aceitava qualquer cartão curtido. Com `caption_contains`:

- OPEN_POST e OPEN_COMMENTS só se comprovam com a legenda na tela;
- OPEN_COMMENTS só toca o balão (`card_control`) do CARTÃO da legenda — pelo ator ou pela receita: com a folha
  aberta a legenda do fundo continua na árvore, e a pós-condição sozinha aceitaria a folha do post vizinho;
- LIKE_POST só dispara no coração do CARTÃO da legenda, e só se comprova por ele;
- CREATE_COMMENT exige a legenda na tela antes de publicar;
- sem a publicação na tela a etapa não se comprova, e a curtida nunca roda.

Sem `caption_contains`, tudo segue como antes (post por posição).

Nível de prova: `simulated` — árvores do Instagram falso de dois cartões (geometria de 720x1280, a do android-06:
coração em [24,455][72,547] na execução e31953) e o Harness (porta base 5640) com um ator por regras que repete o erro
do modelo real. A prova `real`, numa conta do Instagram, fica `not_run` (curtir é efeito numa conta de terceiros).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest_asyncio

from app.automation.hierarchy import UiElement, UiTree, parse_hierarchy
from app.db import Row
from app.models import Plan, PlannerInfo
from app.modules.capabilities.domain.definition import CapabilityRef
from app.modules.capabilities.domain.verification import Observation, StepView, VerifyOutcome
from app.modules.capabilities.infrastructure.catalog_provider import CatalogCapabilityProvider
from app.modules.capabilities.infrastructure.catalog_registry import CatalogCapabilityRegistry
from app.planning.capabilities import CapabilityNode, capability_of, compose, load_catalog
from app.planning.provider import Decision, DecisionRequest, PlanRequest, Usage, Verdict, VerifyRequest
from app.state import AppState
from app.taskqueue.proofs import local_proof_holds

from .conftest import CountingProvider, Harness
from .fake_instagram import PKG, AtorDoInstagram, FakeInstagram, Node

IID = "android-01"
LEGENDA = "Ainda sobre Setembro Amarelo 2024"
_ALVO = f"anarabottinipsicopedagoga {LEGENDA} 🟡💛"
_OUTRA = "anarabottinipsicopedagoga Semana de acolhimento na escola, com as famílias"
_TV, _IV, _BT = "android.widget.TextView", "android.widget.ImageView", "android.widget.Button"


# ==================================================================== o Instagram falso com a tela "Posts"
@dataclass
class InstagramComPublicacoes(FakeInstagram):
    """A tela "Posts" de um perfil (aberta pela grade): dois cartões, cada um com cabeçalho, mídia, coração, balão de
    comentário e legenda — nessa ordem, de cima para baixo, como no app. `alt_da_segunda` é a descrição de
    acessibilidade da mídia do 2º cartão: o Instagram costuma pôr ali o texto da IMAGEM ("may be an image of text
    that says…"), e é exatamente o que não pode fazer o coração do 1º cartão passar pela guarda."""

    legendas: tuple[str, ...] = (_OUTRA, _ALVO)
    alt_da_segunda: str = "Photo by Ana Rabottini"
    curtidas: list[bool] = field(default_factory=lambda: [False, False])
    coracoes_tocados: list[int] = field(default_factory=list)
    # A tela rolada: tudo sobe `deslocamento` px e o que passa para trás da barra do título sai da árvore, como no app.
    # Com 410, do cartão de cima só sobra a legenda — e o único balão da tela é o do cartão de baixo.
    deslocamento: int = 0
    # Legenda da publicação de cada folha "Comments" aberta, na ordem: é o que diz em que post um comentário cairia.
    folhas_abertas: list[str] = field(default_factory=list)
    # Convidado saturado: o toque no coração volta sem erro, mas o app não registra a curtida.
    perde_curtida: bool = False

    def _build(self) -> list[Node]:
        if self.screen == "posts":
            return self._publicacoes()
        if self.screen == "comments":
            # Medido em r-20260928165254-e31953 (`draft_meta.screen_seen` da comment_1): com a folha aberta, a
            # publicação do fundo — legenda inclusive — continua na árvore. A folha em si não diz de que post é.
            return self._publicacoes() + [
                Node(_TV, (250, 700, 470, 750), text="Comments", rid="title_text_view"),
                Node(_TV, (40, 800, 680, 850), text="No comments yet"),
            ]
        nos = super()._build()
        if self.screen == "feed":
            nos.append(Node(_IV, (0, 400, 240, 640), desc="Photo by Ana Rabottini at row 1, column 1",
                            rid="image_button", clickable=True, action="posts"))
        return nos

    def _publicacoes(self) -> list[Node]:
        nos = [Node(_TV, (100, 60, 300, 120), text="Posts", rid="action_bar_title")]
        for i, legenda in enumerate(self.legendas):
            y = 480 * i - self.deslocamento
            cartao = [
                Node(_TV, (100, 140 + y, 500, 190 + y), text="anarabottinipsicopedagoga",
                     rid="row_feed_photo_profile_name"),
                Node(_IV, (0, 200 + y, 720, 420 + y), desc="Photo by Ana Rabottini" if i == 0 else self.alt_da_segunda,
                     rid="row_feed_photo_imageview"),
                Node(_BT, (24, 430 + y, 72, 522 + y), desc="Liked" if self.curtidas[i] else "Like",
                     rid="row_feed_button_like", clickable=True, action=f"like:{i}"),
                Node(_BT, (131, 430 + y, 179, 522 + y), desc="Comment", rid="row_feed_button_comment", clickable=True,
                     action=f"comments:{i}"),
                Node(_TV, (24, 560 + y, 700, 620 + y), text=legenda, rid="row_feed_comment_textview_layout"),
            ]
            nos += [n for n in cartao if n.bounds[3] > 130]
        return nos + self._tab_bar()

    def tap(self, x: int, y: int) -> None:
        hit = next((n for n in self._nodes if n.clickable and n.bounds[0] <= x <= n.bounds[2]
                    and n.bounds[1] <= y <= n.bounds[3]), None)
        if hit is not None and hit.action == "posts":
            self.calls.append(f"tap:{x},{y}")
            self.screen = "posts"
            return
        if hit is not None and hit.action.startswith("comments:"):
            self.calls.append(f"tap:{x},{y}")
            self.folhas_abertas.append(self.legendas[int(hit.action.split(":", 1)[1])])
            self.screen = "comments"
            return
        if hit is not None and hit.action.startswith("like:"):
            self.calls.append(f"tap:{x},{y}")
            i = int(hit.action.split(":", 1)[1])
            self.coracoes_tocados.append(i)
            if not self.perde_curtida:
                self.curtidas[i] = not self.curtidas[i]
            return
        super().tap(x, y)


def _tela(**campos: object) -> UiTree:
    fake = InstagramComPublicacoes(account="eu.teste", screen="posts", **campos)  # type: ignore[arg-type]
    return parse_hierarchy(fake.page_source())


def _coracoes(tree: UiTree) -> list[UiElement]:
    return tree.find(resource_id="row_feed_button_like")


def _baloes(tree: UiTree) -> list[UiElement]:
    return tree.find(resource_id="row_feed_button_comment")


# ==================================================================== composição (o plano)
def test_sem_legenda_as_etapas_do_post_continuam_as_de_hoje() -> None:
    """Post por posição ("a primeira publicação"): nenhuma guarda nova nasce, nem vira `{caption_contains}` literal —
    uma guarda com a variável crua exigiria o texto "{caption_contains}" na tela e travaria toda curtida."""
    steps, missing = compose(load_catalog(PKG), [
        CapabilityNode(key="post", capability="OPEN_POST", bindings={"target": "primeira publicação da grade"}),
        CapabilityNode(key="curtir", capability="LIKE_POST", depends_on=["post"]),
        CapabilityNode(key="coment", capability="OPEN_COMMENTS", depends_on=["curtir"]),
        CapabilityNode(key="c1", capability="CREATE_COMMENT", depends_on=["coment"],
                       bindings={"content_brief": "elogiar"})])
    assert not missing
    por_chave = {s.key: s for s in steps}
    assert por_chave["post"].postcondition.value == "id=action_bar_title|text==Posts"
    for s in steps:
        assert s.commit_guard == [] and s.band_guard == [], s.key
        assert "{caption_contains}" not in f"{s.title} {s.goal} {s.postcondition.description}", s.key


def test_com_legenda_curtir_e_comentar_exigem_o_texto_antes_do_efeito() -> None:
    steps, missing = compose(load_catalog(PKG), [
        CapabilityNode(key="post", capability="OPEN_POST",
                       bindings={"target": f"publicação com o texto \"{LEGENDA}\"", "caption_contains": LEGENDA}),
        CapabilityNode(key="curtir", capability="LIKE_POST", depends_on=["post"],
                       bindings={"caption_contains": LEGENDA}),
        CapabilityNode(key="c1", capability="CREATE_COMMENT", depends_on=["curtir"],
                       bindings={"content_brief": "elogiar", "caption_contains": LEGENDA})])
    assert not missing
    por_chave = {s.key: s for s in steps}
    assert por_chave["curtir"].commit_guard == [LEGENDA]
    # o texto do comentário ainda não existe (é escrito por perfil no gate); a legenda já trava o envio
    assert por_chave["c1"].commit_guard == [LEGENDA]
    assert por_chave["post"].bindings["caption_contains"] == LEGENDA
    for chave in ("OPEN_POST", "LIKE_POST", "OPEN_COMMENTS", "CREATE_COMMENT"):
        cap = capability_of(PKG, chave)
        assert cap is not None and "caption_contains" in cap.optional_bindings, chave
    # o planejador fica sabendo do argumento pela linha do catálogo
    assert "OPEN_POST(target, caption_contains?)" in load_catalog(PKG).prompt_block()


def test_o_planejador_e_instruido_a_extrair_o_texto_do_post() -> None:
    from app.planning.prompts import PLANNER_CAPABILITY_SYSTEM

    assert "caption_contains" in PLANNER_CAPABILITY_SYSTEM
    # sem a legenda na etapa dos comentários, o toque que abre a folha não teria cartão a conferir
    assert "abrir os comentários dela" in " ".join(PLANNER_CAPABILITY_SYSTEM.split())


# ==================================================================== o cartão do coração
def test_o_cartao_do_coracao_vai_ate_o_meio_do_caminho_para_o_seguinte() -> None:
    """A legenda fica ABAIXO dos botões do próprio cartão; a faixa simétrica de uma linha de lista (`text_in_band`) não
    a alcança com segurança. O cartão vai do topo do coração até metade do caminho para o próximo coração (mesmo id) —
    a mídia do cartão seguinte, cuja descrição pode repetir o texto da imagem, fica de fora."""
    tree = _tela(alt_da_segunda=f"Photo by Ana Rabottini. May be an image of text that says '{LEGENDA}'")
    de_cima, de_baixo = _coracoes(tree)
    assert tree.text_in_card(LEGENDA, de_baixo)
    assert not tree.text_in_card(LEGENDA, de_cima)                 # nem pela descrição da mídia de baixo
    assert tree.text_in_card("Semana de acolhimento", de_cima)
    assert not tree.text_in_card("Semana de acolhimento", de_baixo)  # a legenda de CIMA não é do cartão de baixo
    assert not tree.text_in_card("", de_baixo)


def test_curtida_com_legenda_rejeita_o_coracao_do_outro_cartao() -> None:
    from app.taskqueue.executor import rejeicao_do_commit

    tree = _tela()
    de_cima, de_baixo = _coracoes(tree)
    cartao = (LEGENDA,)
    motivo = rejeicao_do_commit([LEGENDA], [], cartao, tree, de_cima)
    assert motivo is not None and "cartão" in motivo and LEGENDA in motivo
    assert rejeicao_do_commit([LEGENDA], [], cartao, tree, de_baixo) is None
    assert rejeicao_do_commit([LEGENDA], [], cartao, tree, None) is not None      # toque sem alvo não prova cartão
    # sem legenda na tela: a guarda de texto recusa antes de olhar cartão
    sem = _tela(legendas=(_OUTRA, "anarabottinipsicopedagoga Outubro Rosa"))
    motivo = rejeicao_do_commit([LEGENDA], [], cartao, sem, _coracoes(sem)[1])
    assert motivo is not None and "visíveis" in motivo
    # sem caption_contains (post por posição): nenhuma guarda nova — qualquer coração passa, como hoje
    assert rejeicao_do_commit([], [], (), tree, de_cima) is None
    assert rejeicao_do_commit([], [], (), tree, de_baixo) is None


def test_a_guarda_de_linha_de_hoje_continua_igual_na_funcao_extraida() -> None:
    """A conferência antes do efeito saiu do laço para uma função pura; a guarda de linha (`band_guard`) é a mesma."""
    from app.taskqueue.executor import rejeicao_do_commit

    linhas = "".join(
        f'<node class="android.widget.TextView" text="{nome}" resource-id="app:id/username" '
        f'bounds="[20,{200 + i * 200}][400,{280 + i * 200}]"/>'
        f'<node class="android.widget.Button" text="Confirm" resource-id="app:id/confirm" clickable="true" '
        f'bounds="[420,{200 + i * 200}][700,{280 + i * 200}]"/>' for i, nome in enumerate(("ana", "bruno")))
    tree = parse_hierarchy(f"<hierarchy>{linhas}</hierarchy>")
    da_ana, do_bruno = tree.find_selector("text=Confirm")
    assert rejeicao_do_commit(["@ana"], ["@ana"], (), tree, da_ana) is None
    motivo = rejeicao_do_commit(["@ana"], ["@ana"], (), tree, do_bruno)
    assert motivo is not None and "mesma linha" in motivo
    motivo = rejeicao_do_commit(["@carla"], [], (), tree, da_ana)
    assert motivo is not None and "@carla" in motivo


# ==================================================================== o toque que abre a folha (sem efeito)
def test_o_balao_de_comentarios_e_o_controle_do_cartao_declarado_no_catalogo() -> None:
    """OPEN_COMMENTS não tem efeito nem `commit_selector`: sem um controle declarado, a guarda de cartão nunca olharia
    o toque que abre a folha. Medido em r-20260928165254-e31953 (ação e29 da open_comments_1): o balão é
    `row_feed_button_comment`, desc "Comment", [131,455][179,547] no android-06."""
    cap = capability_of(PKG, "OPEN_COMMENTS")
    assert cap is not None and not cap.side_effect and cap.commit_selector is None
    assert cap.card_control == "id=row_feed_button_comment" and cap.card_guard == ("{caption_contains}",)
    # só OPEN_COMMENTS declara: a curtida já é conferida pelo `commit_selector` no caminho do efeito
    catalogo = load_catalog(PKG)
    assert catalogo is not None
    assert [c.key for c in catalogo.capabilities if c.card_control] == ["OPEN_COMMENTS"]


def test_toque_no_balao_do_outro_cartao_e_recusado() -> None:
    from app.taskqueue.executor import rejeicao_do_controle

    tree = _tela()                                     # cartão de cima: outra publicação; de baixo: a do pedido
    de_cima, de_baixo = _baloes(tree)
    controle, cartao = "id=row_feed_button_comment", (LEGENDA,)
    motivo = rejeicao_do_controle(controle, cartao, tree, de_cima.center)
    assert motivo is not None and "cartão" in motivo and LEGENDA in motivo and "step_blocked" in motivo
    assert rejeicao_do_controle(controle, cartao, tree, de_baixo.center) is None
    # por coordenada, dentro do balão errado (não no centro): o toque cai nele do mesmo jeito
    assert rejeicao_do_controle(controle, cartao, tree, (de_cima.bounds[0] + 2, de_cima.bounds[3] - 2)) is not None
    # toque que não acerta o controle do cartão (a aba de perfil, um "Not now"): navegação comum, sem guarda nova
    perfil = tree.find(resource_id="profile_tab")[0]
    assert rejeicao_do_controle(controle, cartao, tree, perfil.center) is None
    # sem a legenda na tela, nenhum balão é o dela
    sem = _tela(legendas=(_OUTRA, "anarabottinipsicopedagoga Outubro Rosa"))
    for balao in _baloes(sem):
        assert rejeicao_do_controle(controle, cartao, sem, balao.center) is not None
    # tela rolada: da publicação alvo só a legenda aparece, no topo; o único balão é o da publicação de baixo
    rolada = _tela(legendas=(_ALVO, _OUTRA), deslocamento=410)
    assert len(_baloes(rolada)) == 1 and rolada.contains_text(LEGENDA)
    assert rejeicao_do_controle(controle, cartao, rolada, _baloes(rolada)[0].center) is not None
    # sem caption_contains (post por posição): qualquer balão, como hoje
    assert rejeicao_do_controle(controle, (), tree, de_cima.center) is None


# ==================================================================== prova local e pós-condição
def test_prova_local_da_curtida_com_legenda_so_vale_no_cartao_certo() -> None:
    cap = capability_of(PKG, "LIKE_POST")
    assert cap is not None and cap.local_proof == "selector:desc==Liked"      # a prova declarada não mudou
    curtiu_o_errado = _tela(curtidas=[True, False])
    curtiu_o_certo = _tela(curtidas=[False, True])
    com = SimpleNamespace(bindings={"caption_contains": LEGENDA}, band_guard=[], card_guard=[LEGENDA])
    sem = SimpleNamespace(bindings={}, band_guard=[])
    assert local_proof_holds(cap.local_proof, com, curtiu_o_errado) is False
    assert local_proof_holds(cap.local_proof, com, curtiu_o_certo) is True
    assert local_proof_holds(cap.local_proof, sem, curtiu_o_errado) is True    # sem legenda: como hoje


async def test_a_prova_pela_porta_do_provider_tambem_so_vale_no_cartao_certo() -> None:
    provider = CatalogCapabilityProvider(CatalogCapabilityRegistry(lambda _app: None))
    ref = CapabilityRef(PKG, "LIKE_POST")

    async def veredito(tree: UiTree, bindings: tuple[tuple[str, str], ...]) -> VerifyOutcome:
        return (await provider.verify(StepView(node_id="curtir", capability=ref, bindings=bindings),
                                      Observation(tree, PKG))).outcome

    legenda = (("caption_contains", LEGENDA),)
    assert await veredito(_tela(curtidas=[True, False]), legenda) is VerifyOutcome.unknown
    assert await veredito(_tela(curtidas=[False, True]), legenda) is VerifyOutcome.proved
    assert await veredito(_tela(curtidas=[True, False]), ()) is VerifyOutcome.proved


def test_textos_do_cartao_so_valem_com_o_argumento_preenchido() -> None:
    from app.planning.capabilities import guardas_do_cartao

    assert guardas_do_cartao(("{caption_contains}",), {"caption_contains": LEGENDA}) == (LEGENDA,)
    assert guardas_do_cartao(("{caption_contains}",), {}) == ()
    assert guardas_do_cartao(("{caption_contains}",), {"caption_contains": "  "}) == ()
    assert guardas_do_cartao(("{caption_contains}",), None) == ()
    # o valor é literal: um `|` ou `&` na legenda não vira operador de seletor
    assert guardas_do_cartao(("{caption_contains}",), {"caption_contains": "a | b & c"}) == ("a | b & c",)
    for chave in ("OPEN_POST", "LIKE_POST", "OPEN_COMMENTS"):
        cap = capability_of(PKG, chave)
        assert cap is not None and cap.card_guard == ("{caption_contains}",), chave


def test_folha_de_comentarios_so_se_comprova_com_a_legenda_na_tela() -> None:
    """Medido em r-20260928165254-e31953 (`draft_meta.screen_seen` da comment_1): com a folha "Comments" aberta, a
    legenda do post ("anarabottinipsicopedagoga Ainda sobre Setembro Amarelo 2024 🟡💛") continua na árvore."""
    from app.taskqueue.executor import textos_do_cartao_ausentes

    folha = parse_hierarchy(
        '<hierarchy>'
        '<node class="android.widget.TextView" text="Comments" resource-id="com.instagram.android:id/title_text_view"'
        ' bounds="[250,300][470,350]"/>'
        '<node class="android.widget.TextView" text="No comments yet" bounds="[40,400][680,450]"/>'
        f'<node class="android.widget.TextView" text="{_ALVO}" bounds="[40,150][680,200]"/>'
        '</hierarchy>')
    de_outro = parse_hierarchy(
        '<hierarchy>'
        '<node class="android.widget.TextView" text="Comments" resource-id="com.instagram.android:id/title_text_view"'
        ' bounds="[250,300][470,350]"/>'
        f'<node class="android.widget.TextView" text="{_OUTRA}" bounds="[40,150][680,200]"/>'
        '</hierarchy>')
    assert textos_do_cartao_ausentes((LEGENDA,), folha) == []
    assert textos_do_cartao_ausentes((LEGENDA,), de_outro) == [LEGENDA]
    assert textos_do_cartao_ausentes((), de_outro) == []                  # sem legenda: só o título, como hoje


# ==================================================================== de ponta a ponta, no executor de verdade
class AtorDePublicacoes(AtorDoInstagram):
    """Ator por regras que repete o erro do modelo real: dá por aberta QUALQUER publicação com título "Posts" e toca o
    coração de CIMA primeiro (o e26 da receita v1 em e31953). Na segunda tentativa de abrir, obedece ao objetivo e
    chama `step_blocked`. Quem impede o toque errado e a prova errada é o executor, não o roteiro."""

    name = "ator-de-publicacoes"
    model = "roteiro-da-tela-posts"

    def __init__(self, legenda: str | None) -> None:
        self.legenda = legenda
        self.toques_de_curtir = 0

    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        catalogo = load_catalog(PKG)
        assert catalogo is not None
        extra = {"caption_contains": self.legenda} if self.legenda else {}
        nos = [CapabilityNode(key="abrir_post", capability="OPEN_POST",
                              bindings={"target": f"publicação com o texto \"{LEGENDA}\"", **extra}),
               CapabilityNode(key="curtir", capability="LIKE_POST", depends_on=["abrir_post"], bindings=dict(extra))]
        etapas, faltando = compose(catalogo, nos)
        return Plan(summary="[roteiro] curtir o post pela legenda", app_id="instagram", app_package=PKG,
                    parameters={}, steps=etapas, missing=faltando,
                    planner=PlannerInfo(provider=self.name, model=self.model, simulated=True)), Usage()

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        tree: UiTree = req.screen.tree
        objetivo = req.ctx.step_goal.casefold()

        def d(tool: str, **args: object) -> tuple[Decision, Usage]:
            return Decision(tool=tool, args={"rationale": "[roteiro]", **args}), Usage()

        if objetivo.startswith("abrir a publicação"):
            if not tree.find(resource_id="action_bar_title", text="Posts", exact=True):
                grade = tree.find(resource_id="image_button")
                if not grade:
                    return d("step_blocked", kind="other", reason="sem grade", needs_user=False)
                return d("tap", element_id=grade[0].id, x=None, y=None, is_commit_action=False, expect_done=False)
            if any("tentativa anterior" in h for h in req.history):
                return d("step_blocked", kind="other", needs_user=True,
                         reason="a publicação com o texto pedido não aparece nesta tela")
            return d("step_done", evidence="título Posts", delivery_level=None)
        if objetivo.startswith("curtir a publicação"):
            if req.ctx.commit_done:
                return d("step_done", evidence="coração marcado", delivery_level=None)
            coracoes = _coracoes(tree)
            self.toques_de_curtir += 1
            alvo = coracoes[min(self.toques_de_curtir - 1, len(coracoes) - 1)]
            return d("tap", element_id=alvo.id, x=None, y=None, is_commit_action=True, expect_done=False)
        return d("step_blocked", kind="other", needs_user=False, reason=f"etapa desconhecida: {req.ctx.step_goal}")

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        # Só a prova local pode fechar a curtida; o "sim" genérico do modelo seria justamente a prova fraca.
        return Verdict(satisfied="uncertain", evidence="[roteiro] não julga publicação"), Usage()


class AtorDosComentarios(AtorDePublicacoes):
    """Abre a publicação pela legenda e depois os comentários, tocando os balões de CIMA para BAIXO: o primeiro da tela
    é o que o ator real tocou em r-20260928165254-e31953 (e29, `row_feed_button_comment`) e o que uma receita repetiria.
    A cada toque recusado pelo executor tenta o balão seguinte; sem balão sobrando, `step_blocked`. O roteiro não sabe
    qual balão é o certo — quem impede a folha do post errado é o executor."""

    name = "ator-dos-comentarios"

    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        catalogo = load_catalog(PKG)
        assert catalogo is not None
        extra = {"caption_contains": self.legenda} if self.legenda else {}
        nos = [CapabilityNode(key="abrir_post", capability="OPEN_POST",
                              bindings={"target": f"publicação com o texto \"{LEGENDA}\"", **extra}),
               CapabilityNode(key="comentarios", capability="OPEN_COMMENTS", depends_on=["abrir_post"],
                              bindings=dict(extra))]
        etapas, faltando = compose(catalogo, nos)
        return Plan(summary="[roteiro] abrir os comentários do post pela legenda", app_id="instagram",
                    app_package=PKG, parameters={}, steps=etapas, missing=faltando,
                    planner=PlannerInfo(provider=self.name, model=self.model, simulated=True)), Usage()

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        if not req.ctx.step_goal.casefold().startswith("abrir a lista de comentários"):
            return await super().decide(req)
        tree: UiTree = req.screen.tree

        def d(tool: str, **args: object) -> tuple[Decision, Usage]:
            return Decision(tool=tool, args={"rationale": "[roteiro]", **args}), Usage()

        if tree.find(resource_id="title_text_view", text="Comments", exact=True):
            return d("step_done", evidence="folha Comments", delivery_level=None)
        baloes = _baloes(tree)
        recusados = sum(1 for h in req.history if "REJEITADA" in h)
        if recusados >= len(baloes):
            return d("step_blocked", kind="other", needs_user=True,
                     reason="o balão da publicação pedida não está nesta tela")
        return d("tap", element_id=baloes[recusados].id, x=None, y=None, is_commit_action=False, expect_done=True)


async def _parque(tmp_path: Path, legenda: str | None, ator: AtorDePublicacoes | None = None,
                  **tela: object) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 1, factory=lambda rt: InstagramComPublicacoes(account="eu.teste", screen="feed",
                                                                        **tela))  # type: ignore[arg-type]
    h.ai = CountingProvider(ator or AtorDePublicacoes(legenda))
    await h.boot()
    s = _estado(h)
    s.db.execute("UPDATE instances SET app_id='instagram' WHERE id=?", (IID,))
    s.devices.get(IID).app_id = "instagram"
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


def _estado(h: Harness) -> AppState:
    assert h.state is not None
    return h.state


def _aparelho(h: Harness) -> InstagramComPublicacoes:
    fake = h.fakes[IID]
    assert isinstance(fake, InstagramComPublicacoes)
    return fake


def _etapas(h: Harness, run_id: str) -> dict[str, Any]:
    return {r["key"]: r for r in _estado(h).db.query("SELECT * FROM steps WHERE run_id=? ORDER BY seq", (run_id,))}


@pytest_asyncio.fixture
async def com_legenda(tmp_path: Path) -> AsyncIterator[Harness]:
    async for h in _parque(tmp_path, LEGENDA):
        yield h


@pytest_asyncio.fixture
async def sem_o_post(tmp_path: Path) -> AsyncIterator[Harness]:
    async for h in _parque(tmp_path, LEGENDA, legendas=(_OUTRA, "anarabottinipsicopedagoga Outubro Rosa")):
        yield h


@pytest_asyncio.fixture
async def por_posicao(tmp_path: Path) -> AsyncIterator[Harness]:
    async for h in _parque(tmp_path, None):
        yield h


async def test_curtir_com_legenda_toca_so_o_coracao_do_cartao_certo(com_legenda: Harness) -> None:
    run = com_legenda.run([IID], command=f"curta o post de @anarabottinipsicopedagoga com o texto \"{LEGENDA}\"")
    detalhe = await com_legenda.wait_run(run.id, timeout=60)
    assert detalhe.status == "completed", (detalhe.status, detalhe.status_detail)
    fake = _aparelho(com_legenda)
    assert fake.coracoes_tocados == [1] and fake.curtidas == [False, True]
    passos = _etapas(com_legenda, run.id)
    curtir = passos["curtir"]
    assert curtir["status"] == "succeeded"
    assert "árvore local" in (curtir["status_detail"] or "")
    rejeitadas = _estado(com_legenda).db.query(
        "SELECT a.error FROM actions a JOIN attempts t ON t.id = a.attempt_id"
        " WHERE t.step_id=? AND a.status='rejected'", (curtir["id"],))
    assert rejeitadas and "cartão" in (rejeitadas[0]["error"] or "")
    assert com_legenda.ai.count("verify") == 0               # comprovado pela árvore, no cartão certo


async def test_sem_a_legenda_na_tela_a_publicacao_nao_se_comprova_e_nada_e_curtido(sem_o_post: Harness) -> None:
    run = sem_o_post.run([IID], command=f"curta o post de @anarabottinipsicopedagoga com o texto \"{LEGENDA}\"")
    s = _estado(sem_o_post)

    def aberta() -> str | None:
        row = s.db.one("SELECT status FROM steps WHERE run_id=? AND key='abrir_post'"
                       " ORDER BY plan_version DESC LIMIT 1", (run.id,))
        return row["status"] if row else None

    await sem_o_post.wait(lambda: aberta() in ("waiting_user", "failed", "succeeded"), timeout=90,
                          what="OPEN_POST parar")
    assert aberta() == "waiting_user"                            # step_blocked: parou sem abrir outra
    passos = _etapas(sem_o_post, run.id)
    assert passos["curtir"]["status"] == "pending"
    assert _aparelho(sem_o_post).coracoes_tocados == []
    falha = s.db.one("SELECT error FROM attempts WHERE step_id=? AND number=1",
                     (passos["abrir_post"]["id"],))
    assert falha is not None and LEGENDA in (falha["error"] or "")


async def test_sem_caption_contains_o_post_por_posicao_segue_como_hoje(por_posicao: Harness) -> None:
    run = por_posicao.run([IID], command="curta a primeira publicação de @anarabottinipsicopedagoga")
    detalhe = await por_posicao.wait_run(run.id, timeout=60)
    assert detalhe.status == "completed", (detalhe.status, detalhe.status_detail)
    fake = _aparelho(por_posicao)
    assert fake.coracoes_tocados == [0]                          # o primeiro coração, sem guarda nova
    assert "árvore local" in (_etapas(por_posicao, run.id)["curtir"]["status_detail"] or "")


async def test_a_evidencia_da_pos_condicao_cita_a_legenda_conferida(com_legenda: Harness) -> None:
    """A pós-condição de OPEN_POST é o seletor "Posts", e a evidência só dizia isso — parecia que a legenda não tinha
    sido conferida. Ela foi (presente na tela; na curtida, no cartão do coração marcado), e a evidência passa a
    dizer."""
    run = com_legenda.run([IID], command=f"curta o post de @anarabottinipsicopedagoga com o texto \"{LEGENDA}\"")
    detalhe = await com_legenda.wait_run(run.id, timeout=60)
    assert detalhe.status == "completed", (detalhe.status, detalhe.status_detail)
    passos = _etapas(com_legenda, run.id)
    abrir = passos["abrir_post"]["status_detail"] or ""
    assert "seletor id=action_bar_title|text==Posts: 1 elemento(s)" in abrir
    assert f'legenda "{LEGENDA}" presente na tela' in abrir
    assert f'no cartão da legenda "{LEGENDA}"' in (passos["curtir"]["status_detail"] or "")
    notas = [r["note"] for r in _estado(com_legenda).db.query(
        "SELECT note FROM evidence WHERE step_id=? AND note LIKE 'Pós-condição%'", (passos["abrir_post"]["id"],))]
    assert notas and all(LEGENDA in n for n in notas)


class AtorQueConfirmaQualquerCoracao(AtorDePublicacoes):
    """O verificador perigoso: vê um coração marcado na tela "Posts" e diz "sim" — mesmo que seja o de OUTRA
    publicação. A etapa de curtir tem prazo curto: sem prova, a verificação espera o prazo inteiro."""

    name = "ator-que-confirma-qualquer-coracao"

    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        plano, uso = await super().plan(req)
        etapas = [e.model_copy(update={"timeout_s": 10}) if e.key == "curtir" else e for e in plano.steps]
        return plano.model_copy(update={"steps": etapas}), uso

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        return Verdict(satisfied="yes", evidence="[roteiro] o ícone de curtir aparece marcado"), Usage()


@pytest_asyncio.fixture
async def curtida_perdida(tmp_path: Path) -> AsyncIterator[Harness]:
    # A publicação de CIMA (outra) já estava curtida; o toque no coração da publicação alvo não é registrado.
    async for h in _parque(tmp_path, LEGENDA, AtorQueConfirmaQualquerCoracao(LEGENDA), curtidas=[True, False],
                           perde_curtida=True):
        yield h


async def test_com_legenda_so_a_prova_local_fecha_a_curtida_e_o_modelo_nao_e_perguntado(
        curtida_perdida: Harness) -> None:
    """A prova local procura o coração marcado NO CARTÃO da legenda e não acha: a curtida não foi registrada. O modelo
    veria o coração marcado da publicação de cima e diria "sim" — com a legenda de cartão ele não é perguntado, e a
    curtida disparada sem prova fica incerta, nunca sucesso."""
    h = curtida_perdida
    run = h.run([IID], command=f"curta o post de @anarabottinipsicopedagoga com o texto \"{LEGENDA}\"")
    detalhe = await h.wait_run(run.id, timeout=60)
    fake = _aparelho(h)
    assert fake.coracoes_tocados == [1] and fake.curtidas == [True, False]   # tocou o certo; não registrou
    curtir = _etapas(h, run.id)["curtir"]
    assert curtir["status"] == "uncertain", (curtir["status"], curtir["status_detail"])
    assert h.ai.count("verify") == 0                             # o "sim" do modelo nunca foi pedido
    motivo = curtir["status_detail"] or ""
    assert LEGENDA in motivo and "modelo não é consultado" in motivo
    assert detalhe.status == "completed_with_issues"


# ==================================================================== a folha de comentários, de ponta a ponta
def _rejeitadas(h: Harness, step_id: str) -> list[Row]:
    return _estado(h).db.query("SELECT a.error, a.source FROM actions a JOIN attempts t ON t.id = a.attempt_id"
                               " WHERE t.step_id=? AND a.status='rejected' ORDER BY a.seq", (step_id,))


@pytest_asyncio.fixture
async def comentarios_com_legenda(tmp_path: Path) -> AsyncIterator[Harness]:
    async for h in _parque(tmp_path, LEGENDA, AtorDosComentarios(LEGENDA)):
        yield h


@pytest_asyncio.fixture
async def comentarios_por_posicao(tmp_path: Path) -> AsyncIterator[Harness]:
    async for h in _parque(tmp_path, None, AtorDosComentarios(None)):
        yield h


@pytest_asyncio.fixture
async def comentarios_com_receita(tmp_path: Path) -> AsyncIterator[Harness]:
    async for h in _parque(tmp_path, LEGENDA, AtorDosComentarios(LEGENDA), legendas=(_ALVO,)):
        h.cfg.file.ai.recipes = "replay"
        yield h


async def test_abrir_comentarios_com_legenda_recusa_o_balao_do_outro_cartao(comentarios_com_legenda: Harness) -> None:
    """Dois cartões na tela "Posts", a publicação do pedido embaixo. O ator toca primeiro o balão de CIMA: a folha que
    abriria é a da outra publicação, e a pós-condição passaria assim mesmo (título "Comments" e a legenda certa no
    fundo) — o comentário seguinte sairia no post errado. O executor recusa esse toque; o do cartão certo passa."""
    h = comentarios_com_legenda
    run = h.run([IID], command=f"abra os comentários do post de @anarabottinipsicopedagoga com o texto \"{LEGENDA}\"")
    detalhe = await h.wait_run(run.id, timeout=60)
    assert detalhe.status == "completed", (detalhe.status, detalhe.status_detail)
    assert _aparelho(h).folhas_abertas == [_ALVO]                # nunca a folha da outra publicação
    passo = _etapas(h, run.id)["comentarios"]
    assert passo["status"] == "succeeded"
    rejeitadas = _rejeitadas(h, passo["id"])
    assert len(rejeitadas) == 1 and rejeitadas[0]["source"] == "ai"
    assert "cartão" in (rejeitadas[0]["error"] or "") and LEGENDA in (rejeitadas[0]["error"] or "")


async def test_sem_caption_contains_os_comentarios_seguem_pelo_primeiro_balao(comentarios_por_posicao: Harness) -> None:
    h = comentarios_por_posicao
    run = h.run([IID], command="abra os comentários da primeira publicação de @anarabottinipsicopedagoga")
    detalhe = await h.wait_run(run.id, timeout=60)
    assert detalhe.status == "completed", (detalhe.status, detalhe.status_detail)
    assert _aparelho(h).folhas_abertas == [_OUTRA]               # o primeiro balão, sem guarda nova
    assert _rejeitadas(h, _etapas(h, run.id)["comentarios"]["id"]) == []


async def test_receita_que_repete_o_balao_na_publicacao_errada_e_recusada(comentarios_com_receita: Harness) -> None:
    """A receita de OPEN_COMMENTS é a mesma com ou sem legenda (`step_template_hash` não olha argumentos) e repete o
    toque no balão pelo seletor. Aprendida numa tela com um cartão só, ela é reproduzida numa tela rolada em que da
    publicação alvo só sobrou a legenda, no topo, e o único balão é o da publicação de baixo: sem a guarda, a receita
    abriria a folha errada e a etapa se comprovaria (legenda na tela). Com a guarda, o toque da receita é recusado, a
    IA assume e, sem o balão certo na tela, para (step_blocked) em vez de abrir outra."""
    h = comentarios_com_receita
    s = _estado(h)
    fake = _aparelho(h)
    run = h.run([IID], command=f"abra os comentários do post de @anarabottinipsicopedagoga com o texto \"{LEGENDA}\"")
    detalhe = await h.wait_run(run.id, timeout=60)
    assert detalhe.status == "completed", (detalhe.status, detalhe.status_detail)
    assert fake.folhas_abertas == [_ALVO]
    receita = s.db.one("SELECT actions FROM recipes WHERE step_key='comentarios' AND status='active'")
    assert receita is not None and "row_feed_button_comment" in receita["actions"]   # a receita existe mesmo

    fake.screen, fake.legendas, fake.deslocamento = "feed", (_ALVO, _OUTRA), 410
    fake.curtidas = [False, False]
    run2 = h.run([IID], command=f"de novo: os comentários da publicação que diz \"{LEGENDA}\"")

    def comentarios() -> Row | None:
        return s.db.one("SELECT id, status FROM steps WHERE run_id=? AND key='comentarios'"
                        " ORDER BY plan_version DESC LIMIT 1", (run2.id,))

    await h.wait(lambda: (comentarios() or {"status": None})["status"] in ("waiting_user", "failed", "succeeded"),
                 timeout=90, what="OPEN_COMMENTS parar")
    passo = comentarios()
    assert passo is not None and passo["status"] == "waiting_user"
    assert fake.folhas_abertas == [_ALVO]                        # nenhuma folha nova: a de baixo não foi aberta
    rejeitadas = _rejeitadas(h, passo["id"])
    assert rejeitadas and rejeitadas[0]["source"] == "recipe" and "cartão" in (rejeitadas[0]["error"] or "")

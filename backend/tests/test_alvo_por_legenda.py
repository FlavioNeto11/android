"""Identidade da publicação alvo (C10): curtir e comentar só no post cuja legenda o pedido citou.

Execuções reais r-20260928165254-e31953 e r-20260928195344-02ee9e (android-06). O pedido era "no post do perfil
@anarabottinipsicopedagoga que contém o texto 'Ainda sobre Setembro Amarelo 2024'"; o plano guardou o texto só em
`target` (prosa para o ator). A pós-condição de OPEN_POST (título "Posts") vale para QUALQUER publicação aberta,
LIKE_POST tocava o primeiro `desc==Like` da tela (a receita v1 repetiu o toque em e26) e a prova `desc==Liked`
aceitava qualquer cartão curtido. Com `caption_contains`:

- OPEN_POST e OPEN_COMMENTS só se comprovam com a legenda na tela;
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

    legendas: tuple[str, str] = (_OUTRA, _ALVO)
    alt_da_segunda: str = "Photo by Ana Rabottini"
    curtidas: list[bool] = field(default_factory=lambda: [False, False])
    coracoes_tocados: list[int] = field(default_factory=list)

    def _build(self) -> list[Node]:
        if self.screen == "posts":
            return self._publicacoes()
        nos = super()._build()
        if self.screen == "feed":
            nos.append(Node(_IV, (0, 400, 240, 640), desc="Photo by Ana Rabottini at row 1, column 1",
                            rid="image_button", clickable=True, action="posts"))
        return nos

    def _publicacoes(self) -> list[Node]:
        nos = [Node(_TV, (100, 60, 300, 120), text="Posts", rid="action_bar_title")]
        for i, legenda in enumerate(self.legendas):
            y = 480 * i
            nos += [
                Node(_TV, (100, 140 + y, 500, 190 + y), text="anarabottinipsicopedagoga",
                     rid="row_feed_photo_profile_name"),
                Node(_IV, (0, 200 + y, 720, 420 + y), desc="Photo by Ana Rabottini" if i == 0 else self.alt_da_segunda,
                     rid="row_feed_photo_imageview"),
                Node(_BT, (24, 430 + y, 72, 522 + y), desc="Liked" if self.curtidas[i] else "Like",
                     rid="row_feed_button_like", clickable=True, action=f"like:{i}"),
                Node(_BT, (131, 430 + y, 179, 522 + y), desc="Comment", rid="row_feed_button_comment", clickable=True),
                Node(_TV, (24, 560 + y, 700, 620 + y), text=legenda, rid="row_feed_comment_textview_layout"),
            ]
        return nos + self._tab_bar()

    def tap(self, x: int, y: int) -> None:
        hit = next((n for n in self._nodes if n.clickable and n.bounds[0] <= x <= n.bounds[2]
                    and n.bounds[1] <= y <= n.bounds[3]), None)
        if hit is not None and hit.action == "posts":
            self.calls.append(f"tap:{x},{y}")
            self.screen = "posts"
            return
        if hit is not None and hit.action.startswith("like:"):
            self.calls.append(f"tap:{x},{y}")
            i = int(hit.action.split(":", 1)[1])
            self.coracoes_tocados.append(i)
            self.curtidas[i] = not self.curtidas[i]
            return
        super().tap(x, y)


def _tela(**campos: object) -> UiTree:
    fake = InstagramComPublicacoes(account="eu.teste", screen="posts", **campos)  # type: ignore[arg-type]
    return parse_hierarchy(fake.page_source())


def _coracoes(tree: UiTree) -> list[UiElement]:
    return tree.find(resource_id="row_feed_button_like")


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


async def _parque(tmp_path: Path, legenda: str | None, **tela: object) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 1, factory=lambda rt: InstagramComPublicacoes(account="eu.teste", screen="feed",
                                                                        **tela))  # type: ignore[arg-type]
    h.ai = CountingProvider(AtorDePublicacoes(legenda))
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

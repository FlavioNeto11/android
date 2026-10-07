"""29.79: o executor de verdade não toca no Share de CREATE_POST sem o interruptor "Add AI label" ligado na tela.

`test_rotulo_ia.py` prova as funções puras (`rejeicao_do_interruptor`); aqui o bloco `is_commit` do laço (`_run_step`)
roda inteiro, no Harness (porta base 5640), com um Instagram falso da tela da legenda e um ator por regras que tenta
tocar em Share. Nível `simulated`: a tela falsa usa o que as capturas da publicação manual do 8.3 mediram (android-01,
03/10 04:43Z: `share_footer_button`, "Add AI label" e o interruptor na mesma linha); a conferência na versão atual do
app segue `not_run`, e nada foi publicado em conta nenhuma.

O que o teste isola: a etapa nasce já com `rotulo_ia` (a central o grava pela origem da imagem, coberto em
`test_rotulo_ia.py`) e a porta de política (aprovação do CREATE_POST) é desligada no harness — o que está em prova é só
o que o executor faz com o toque no Share.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from app.automation.hierarchy import UiTree
from app.db import Row
from app.models import Plan, PlannerInfo, ProfileCreate
from app.planning.capabilities import CapabilityNode, compose, load_catalog
from app.planning.provider import Decision, DecisionRequest, PlanRequest, Usage, Verdict, VerifyRequest
from app.state import AppState
from app.models import ResolveBody
from app.taskqueue import executor
from app.taskqueue.service import RunError
from app.taskqueue.repository import Repository

from .conftest import CountingProvider, Harness
from .fake_instagram import PKG, AtorDoInstagram, FakeInstagram, Node
from .test_capabilities import SENHA

IID = "android-01"
LEGENDA = "Fim de tarde na praia"
_TV, _BT, _SW = "android.widget.TextView", "android.widget.Button", "android.view.View"
TERMINAIS = ("succeeded", "waiting_user", "failed", "uncertain", "cancelled")


# ==================================================================== o Instagram falso com a tela da legenda
@dataclass
class InstagramComLegenda(FakeInstagram):
    """A tela final do editor de publicação: a legenda, a linha "Add AI label" com o interruptor ao lado (irmãos, na
    mesma faixa vertical, como no app) e o Share. `rotulo_ligado` é o estado do interruptor; `shares` conta os toques
    que chegaram ao Share (o que a guarda existe para impedir)."""

    rotulo_ligado: bool = False
    shares: list[str] = field(default_factory=list)
    #: 29.79 (d): None = depois do Share a tela de antes (sem o post); True/False = o feed com o post novo da conta,
    #: com ou sem "AI info" no cabeçalho (como o 8.3 mediu).
    marca_no_post: bool | None = None

    def _build(self) -> list[Node]:
        if self.screen == "feed_apos_share":
            cabecalho = [Node(_BT, (98, 395, 632, 446), text=self.account, desc=self.account,
                              rid="row_feed_photo_profile_name", clickable=True)]
            if self.marca_no_post:
                cabecalho.append(Node(_BT, (98, 445, 632, 499), text="AI info", desc="AI info", rid="secondary_label"))
            return [*cabecalho, Node(_TV, (0, 1150, 720, 1200), text=LEGENDA, rid="row_feed_comment_textview_layout")]
        if self.screen != "legenda":
            return super()._build()
        # Medido em 03/10: o texto não tem estado; o interruptor é um View clicável MAIS ALTO que o texto (as faixas
        # só se sobrepõem) e é ele quem leva `checked`. O Share é o botão `share_footer_button` (desc "Share").
        return [Node(_TV, (40, 200, 680, 260), text=LEGENDA, rid="caption_text_view"),
                Node(_TV, (104, 505, 282, 543), text="Add AI label", rid="ai_label_row"),
                Node(_SW, (584, 495, 688, 591), rid="ai_label_switch", clickable=True, action="rotulo"),
                Node(_BT, (40, 1100, 680, 1160), desc="Share", rid="share_footer_button", clickable=True,
                     action="share")]

    def page_source(self) -> str:
        # O `Node` do dublê não carrega `checked`; o interruptor ligado é marcado no XML, como o uiautomator o dá.
        xml = super().page_source()
        marcado = str(self.rotulo_ligado).lower()
        return xml.replace(f'class="{_SW}" package=', f'class="{_SW}" checked="{marcado}" package=')

    def open_app(self, package: str, activity: str | None) -> None:
        self.screen = "legenda"          # reabrir o app (porta de sessão do despacho) volta à tela do editor

    def tap(self, x: int, y: int) -> None:
        hit = next((n for n in self._nodes if n.clickable and n.bounds[0] <= x <= n.bounds[2]
                    and n.bounds[1] <= y <= n.bounds[3]), None)
        if hit is not None and hit.action == "share":
            self.calls.append(f"tap:{x},{y}")
            self.shares.append(f"{x},{y}")
            self.screen = "perfil_apos_share" if self.marca_no_post is None else "feed_apos_share"
            return
        if hit is not None and hit.action == "rotulo":
            self.calls.append(f"tap:{x},{y}")
            self.rotulo_ligado = not self.rotulo_ligado
            return
        super().tap(x, y)


# ==================================================================== o ator por regras
class AtorQueTocaNoShare(AtorDoInstagram):
    """Faz o que um modelo apressado faria: toca no Share sem ligar interruptor nenhum, e de novo a cada recusa. Quem
    impede o toque é o executor, não o roteiro. Depois de um Share que o executor deixou passar, só dá a etapa por
    feita (a comprovação da publicação não é o assunto deste arquivo)."""

    name = "ator-que-toca-no-share"
    model = "roteiro-do-editor-de-publicacao"

    def __init__(self, rotulo: str | None, image_id: str | None = None) -> None:
        self.rotulo = rotulo
        self.image_id = image_id          # None: a etapa sem imagem; senão a imagem que a etapa publica
        self.toques = 0

    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        catalogo = load_catalog(PKG)
        assert catalogo is not None
        etapas, faltando = compose(catalogo, [CapabilityNode(
            key="publicar", capability="CREATE_POST",
            bindings={"image_id": "img-1", "content": LEGENDA, "content_verbatim": "true"})])
        assert not faltando
        # Só a etapa de efeito: a galeria (`preparo`) é do aparelho real. Com `image_id`, a central regrava o
        # `rotulo_ia` pela origem (`_com_rotulo_ia`); sem ele, o argumento fica como o roteiro o pôs.
        publicar = next(e for e in etapas if e.capability == "CREATE_POST")
        assert publicar.commit_guard == [LEGENDA]
        bindings = {k: v for k, v in publicar.bindings.items() if k != "image_id"}
        if self.image_id is not None:
            bindings["image_id"] = self.image_id
        if self.rotulo is not None:
            bindings["rotulo_ia"] = self.rotulo
        etapa = publicar.model_copy(update={"depends_on": [], "bindings": bindings})
        return Plan(summary="[roteiro] publicar com a legenda", app_id="instagram", app_package=PKG, parameters={},
                    steps=[etapa], missing=[],
                    planner=PlannerInfo(provider=self.name, model=self.model, simulated=True)), Usage()

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        tree: UiTree = req.screen.tree

        def d(tool: str, **args: object) -> tuple[Decision, Usage]:
            return Decision(tool=tool, args={"rationale": "[roteiro]", **args}), Usage()

        if req.ctx.commit_done:
            return d("step_done", evidence="Share tocado", delivery_level=None)
        share = tree.find(resource_id="share_footer_button")
        if not share:
            return d("step_blocked", kind="other", needs_user=False, reason="sem Share na tela")
        self.toques += 1
        return d("tap", element_id=share[0].id, x=None, y=None, is_commit_action=True, expect_done=False)

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        return Verdict(satisfied="uncertain", evidence="[roteiro] não julga publicação"), Usage()


# ==================================================================== o parque
@asynccontextmanager
async def _parque(tmp_path: Path, rotulo: str | None, *, ligado: bool, ator: AtorQueTocaNoShare | None = None,
                  marca_no_post: bool | None = None) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 1, factory=lambda rt: InstagramComLegenda(account="eu.teste", screen="legenda",
                                                                    rotulo_ligado=ligado, marca_no_post=marca_no_post))
    h.ai = CountingProvider(ator or AtorQueTocaNoShare(rotulo))
    h.encurtar_verificacao()          # depois do Share a verificação não acha a publicação: sem esperar os 60 s
    await h.boot()
    await h.medir_a_internet()
    s = _estado(h)
    s.db.execute("UPDATE instances SET app_id='instagram', account_label='eu.teste' WHERE id=?", (IID,))
    s.devices.get(IID).app_id = "instagram"
    # CREATE_POST nasce `approval_required`: a porta de política seguraria a etapa antes do executor. Fora de prova aqui.
    s.scheduler.policy_gate = None
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


def _estado(h: Harness) -> AppState:
    assert h.state is not None
    return h.state


def _aparelho(h: Harness) -> InstagramComLegenda:
    fake = h.fakes[IID]
    assert isinstance(fake, InstagramComLegenda)
    return fake


def _etapa(h: Harness, run_id: str) -> Row:
    row = _estado(h).db.one("SELECT * FROM steps WHERE run_id=? AND key='publicar' ORDER BY plan_version DESC LIMIT 1",
                            (run_id,))
    assert row is not None
    return row


async def _publicar(h: Harness) -> Row:
    """Roda o pedido e espera a etapa `publicar` parar (por qualquer desfecho)."""
    run = h.run([IID], command=f"publique a imagem com a legenda \"{LEGENDA}\"")
    await h.wait(lambda: (_etapa_ou_none(h, run.id) or {"status": None})["status"] in TERMINAIS, timeout=90,
                 what="a etapa publicar parar")
    return _etapa(h, run.id)


def _etapa_ou_none(h: Harness, run_id: str) -> Row | None:
    return _estado(h).db.one("SELECT status FROM steps WHERE run_id=? AND key='publicar'"
                             " ORDER BY plan_version DESC LIMIT 1", (run_id,))


def _acoes(h: Harness, step_id: str) -> list[Row]:
    return _estado(h).db.query("SELECT a.status, a.error, a.source FROM actions a JOIN attempts t ON t.id = a.attempt_id"
                               " WHERE t.step_id=? ORDER BY a.seq", (step_id,))


# ==================================================================== o interruptor desligado
async def test_com_o_rotulo_pedido_e_o_interruptor_desligado_o_share_nao_e_tocado(tmp_path: Path) -> None:
    """Duas tentativas de tocar em Share com "Add AI label" desligado: a 1ª vira REJEITADA no histórico do ator e o laço
    segue; a 2ª para a etapa numa pessoa. Nenhum toque chega ao Share e nada é dado por publicado."""
    async with _parque(tmp_path, "true", ligado=False) as h:
        etapa = await _publicar(h)
        assert _aparelho(h).shares == []                                   # o toque no Share nunca chegou ao aparelho
        assert etapa["status"] == "waiting_user", (etapa["status"], etapa["status_detail"])
        assert "Add AI label" in (etapa["status_detail"] or "")
        acoes = _acoes(h, etapa["id"])
        assert len(acoes) == 2 and all(a["status"] == "rejected" for a in acoes)
        assert all("Add AI label" in (a["error"] or "") and "LIGADO" in (a["error"] or "") for a in acoes)
        notas = [r["note"] for r in _estado(h).db.query("SELECT note FROM evidence WHERE step_id=?", (etapa["id"],))]
        assert sum(1 for n in notas if (n or "").startswith("Efeito recusado antes do toque")) == 2
        assert not any("Conferência antes do efeito externo" in (n or "") for n in notas)   # a guarda não foi dada
        # 31.223: o commit do modelo de ação é descartado antes da guarda e refeito no forte (1 decisão a mais); a 2ª
        # recusa continua sem consultar o modelo de novo
        assert h.ai.count("decide") == 3


async def test_a_primeira_recusa_entra_no_historico_do_ator(tmp_path: Path) -> None:
    """A 1ª recusa devolve o motivo ao ator (REJEITADA pelo executor) para ele ligar o interruptor; só a 2ª para."""
    historicos: list[list[str]] = []

    class Espiao(AtorQueTocaNoShare):
        async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
            historicos.append(list(req.history))
            return await super().decide(req)

    async with _parque(tmp_path, "true", ligado=False, ator=Espiao("true")) as h:
        etapa = await _publicar(h)
        assert etapa["status"] == "waiting_user"
        assert len(historicos) >= 3
        assert not any("REJEITADA" in x for x in historicos[0])
        # 31.223: a 2ª consulta é o commit refeito no modelo forte; a recusa da guarda chega na seguinte
        assert any("sobe ao modelo de escalonamento" in x for x in historicos[1])
        assert not any("REJEITADA" in x for x in historicos[1])
        assert any("REJEITADA pelo executor" in x and "Add AI label" in x for x in historicos[2])


# ==================================================================== o interruptor ligado e o rótulo não pedido
async def test_com_o_interruptor_ligado_o_toque_no_share_passa_pela_guarda(tmp_path: Path) -> None:
    """Mesma tela, interruptor marcado na mesma linha do texto: o Share é tocado (uma vez) e a guarda de texto da
    legenda registra a conferência de sempre."""
    async with _parque(tmp_path, "true", ligado=True) as h:
        etapa = await _publicar(h)
        fake = _aparelho(h)
        assert len(fake.shares) == 1
        acoes = _acoes(h, etapa["id"])
        assert [a["status"] for a in acoes][0] != "rejected", acoes
        assert not any("Add AI label" in (a["error"] or "") for a in acoes)
        notas = [r["note"] for r in _estado(h).db.query("SELECT note FROM evidence WHERE step_id=?", (etapa["id"],))]
        assert any("Conferência antes do efeito externo" in (n or "") for n in notas)
        assert not any((n or "").startswith("Efeito recusado antes do toque") for n in notas)


def _imagem_da_persona(h: Harness, image_id: str, source: str) -> None:
    """Uma imagem pronta de uma persona (a FK de `persona_images` exige o perfil), antes do pedido."""
    s = _estado(h)
    pid = s.social.create_profile(ProfileCreate(username="persona.da.imagem", password=SENHA)).id
    s.db.execute("INSERT INTO persona_images(id, persona_id, source, status, is_primary, created_at, bytes_sha256)"
                 " VALUES (?, ?, ?, 'ready', 0, '2026-10-04T10:00:00Z', ?)", (image_id, pid, source, "c" * 64))


async def test_em_duvida_o_interruptor_desligado_impede_o_share(tmp_path: Path) -> None:
    """Revisão R1 (o teste antigo dizia o contrário): etapa sem o argumento e sem imagem legível, e o "false" sem upload
    conhecido que o sustente — a guarda exige o rótulo, o Share não é tocado e a etapa para numa pessoa."""
    for rotulo in (None, "false"):
        async with _parque(tmp_path / str(rotulo), rotulo, ligado=False) as h:
            etapa = await _publicar(h)
            assert _aparelho(h).shares == [], rotulo
            assert etapa["status"] == "waiting_user" and "Add AI label" in (etapa["status_detail"] or ""), rotulo


async def test_etapa_antiga_sem_o_argumento_com_imagem_gerada_nao_publica(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisão R1, o caso realista: a etapa criada ANTES do 29.79 (sem `rotulo_ia`; aqui, a central sem o
    `_com_rotulo_ia`) e retomada depois, publicando imagem GERADA — a origem decide e o Share sem rótulo não sai."""
    monkeypatch.setattr(Repository, "_com_rotulo_ia", lambda self, bindings: bindings)
    ator = AtorQueTocaNoShare(None, image_id="img-gerada")
    async with _parque(tmp_path, None, ligado=False, ator=ator) as h:
        _imagem_da_persona(h, "img-gerada", "generated")
        etapa = await _publicar(h)
        assert "rotulo_ia" not in (json.loads(etapa["bindings"]) or {})        # de fato sem o argumento
        assert _aparelho(h).shares == []
        assert etapa["status"] == "waiting_user" and "Add AI label" in (etapa["status_detail"] or "")


async def test_o_upload_conhecido_dispensa_o_interruptor(tmp_path: Path) -> None:
    """Imagem enviada pelo dono (sem marca de IA): a central grava "false" e a origem confirma — o Share sai com o
    interruptor desligado, e o item da aprovação já diz "sem rótulo de IA (imagem enviada por você)"."""
    ator = AtorQueTocaNoShare(None, image_id="img-enviada")
    async with _parque(tmp_path, None, ligado=False, ator=ator) as h:
        _imagem_da_persona(h, "img-enviada", "upload")
        etapa = await _publicar(h)
        assert json.loads(etapa["bindings"])["rotulo_ia"] == "false"
        assert len(_aparelho(h).shares) == 1
        assert not any("Add AI label" in (a["error"] or "") for a in _acoes(h, etapa["id"]))


# ==================================================================== 29.79 (d): a marca depois do Share
class AtorQueVePublicado(AtorQueTocaNoShare):
    """O juiz diz que a publicação saiu (a contagem é outro assunto): o que está em prova é a conferência da marca."""

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        # S3: o texto do juiz traz o que a tela mostrou, inclusive URL com token; o motivo do incerto não o carrega.
        return Verdict(satisfied="yes", evidence="[roteiro] publicação nova no feed; link https://x.test/p?token=SEGREDO1"
                       ), Usage()


async def test_com_a_marca_no_post_a_publicacao_rotulada_e_sucesso(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Depois do Share, o feed mostra o post da conta com "AI info" colado no nome: a etapa fecha comprovada e a
    evidência diz que a marca foi vista."""
    monkeypatch.setattr(executor, "ESPERA_DA_MARCA_S", 0.01)
    async with _parque(tmp_path, "true", ligado=True, ator=AtorQueVePublicado("true"), marca_no_post=True) as h:
        etapa = await _publicar(h)
        assert len(_aparelho(h).shares) == 1
        assert etapa["status"] == "succeeded", (etapa["status"], etapa["status_detail"])
        assert "vista no cartão do topo, junto do nome da conta" in (etapa["status_detail"] or "")
        # condição da orquestradora (D2): no sucesso a tela da conferência também fica como evidência
        notas = [r["note"] for r in _estado(h).db.query("SELECT note FROM evidence WHERE step_id=?", (etapa["id"],))]
        assert any((n or "").startswith("Conferência da marca depois do efeito") for n in notas)


async def test_sem_a_marca_no_post_publicado_rotulo_nao_confirmado(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisão (d): o post saiu (o juiz o viu) mas sem "AI info": a etapa NÃO é sucesso — fica incerta com "publicado,
    rótulo não confirmado", o Share não se repete e a conferência só leu a tela (uma releitura, nenhum toque)."""
    monkeypatch.setattr(executor, "ESPERA_DA_MARCA_S", 0.01)
    async with _parque(tmp_path, "true", ligado=True, ator=AtorQueVePublicado("true"), marca_no_post=False) as h:
        etapa = await _publicar(h)
        fake = _aparelho(h)
        assert len(fake.shares) == 1                                           # nada se repete
        assert etapa["status"] == "uncertain", (etapa["status"], etapa["status_detail"])
        assert (etapa["status_detail"] or "").startswith("publicado; o rótulo de IA não foi confirmado. Abra a "
                                                         "publicação")
        assert json.loads(etapa["result"])["efeito_comprovado"] is True
        assert "SEGREDO1" not in (etapa["status_detail"] or "")                 # S3: o texto do juiz fica de fora
        assert "SEGREDO1" not in json.loads(etapa["result"])["evidence_text"]
        notas = [r["note"] or "" for r in _estado(h).db.query("SELECT note FROM evidence WHERE step_id=?", (etapa["id"],))]
        assert all("SEGREDO1" not in n for n in notas)       # S3 com o U1b do #325: também a nota do verificador
        s = _estado(h)
        objetivo_bruto = s.db.one("SELECT status_detail, blocked_reason, needs FROM objectives WHERE id=?",
                                  (etapa["objective_id"],))
        assert objetivo_bruto is not None and all("SEGREDO1" not in (v or "") for v in tuple(objetivo_bruto))
        eventos = s.db.query("SELECT message, data FROM events WHERE run_id=?", (etapa["run_id"],))
        assert eventos and all("SEGREDO1" not in f"{e['message']} {e['data'] or ''}" for e in eventos)
        objetivo = _estado(h).db.one("SELECT needs FROM objectives WHERE id=?", (etapa["objective_id"],))
        assert objetivo is not None and "repetir" not in (objetivo["needs"] or "").lower().split("repetir faria")[0]
        assert "confirmar (com o print) ou abandonar" in (objetivo["needs"] or "")
        toques = [c for c in fake.calls if c.startswith("tap:")]
        assert toques == [f"tap:{fake.shares[0]}"]                             # só o Share foi tocado

        # Revisão D1: "repetir" o item incerto refaria a etapa com id novo, e o Share sairia de novo. Recusado; e nem a
        # retomada (`recovery_steps`) põe a etapa de volta no plano.
        s = _estado(h)
        with pytest.raises(RunError) as erro:
            s.runs.resolve(etapa["run_id"], etapa["objective_id"], ResolveBody(resolution="retry"))
        assert erro.value.code == "efeito_comprovado"
        run = s.db.one("SELECT * FROM runs WHERE id=?", (etapa["run_id"],))
        assert "publicar" not in [p.key for p in s.scheduler.recovery_steps(run, etapa["objective_id"])]
        assert len(fake.shares) == 1


async def test_sem_rotulo_pedido_a_marca_nao_e_conferida(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upload conhecido ("false"): a marca não é exigida e a publicação sem "AI info" é sucesso como antes."""
    monkeypatch.setattr(executor, "ESPERA_DA_MARCA_S", 0.01)
    ator = AtorQueVePublicado(None, image_id="img-enviada")
    async with _parque(tmp_path, None, ligado=False, ator=ator, marca_no_post=False) as h:
        _imagem_da_persona(h, "img-enviada", "upload")
        etapa = await _publicar(h)
        assert etapa["status"] == "succeeded", (etapa["status"], etapa["status_detail"])
        assert "AI info" not in (etapa["status_detail"] or "")

"""29.58 (A): o efeito externo é marcado pela AÇÃO, não pela etapa.

Na execução 5f2de5 a IA, numa etapa SEM efeito declarado, digitou e tocou em enviar. O toque saiu gravado sem efeito:
sem a trava de não repetir, sem a guarda e sem a aprovação da etapa que declara o envio, e a etapa de envio mandou de
novo. Agora o executor pergunta, numa etapa sem efeito, se a ação PARECE disparar um (`efeito_fora_da_etapa`) e, se
sim, recusa antes de tocar: `actions.side_effect=1`, `status=rejected`, métrica `executor.efeito_fora_da_etapa`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest_asyncio

from app.automation.hierarchy import UiTree, parse_hierarchy
from app.automation.tools import LongPress, Tap, TypeText
from app.db import loads
from app.metricas import metricas
from app.models import Plan, PlanStep, Postcondition
from app.planning.provider import Decision, DecisionRequest, PlanRequest, Usage, Verdict, VerifyRequest
from app.planning.simulated_provider import SimulatedProvider
from app.state import AppState
from app.taskqueue.executor import REJEICAO_EFEITO_FORA_DA_ETAPA, efeito_fora_da_etapa

from .conftest import CountingProvider, Harness
from .fake_device import FakeQaDevice
from .fake_instagram import PKG, Node
from .test_alvo_por_legenda import LEGENDA, AtorDosComentarios, InstagramComPublicacoes

_TV, _BT, _ET = "android.widget.TextView", "android.widget.Button", "android.widget.EditText"
QA = "com.pocqa.messenger"


def _arvore(*nos: str) -> UiTree:
    return parse_hierarchy("<hierarchy>" + "".join(nos) + "</hierarchy>")


def _ctx(tree: UiTree) -> Any:
    return SimpleNamespace(tree=tree, image_scale=1.0, width=720, height=1280)


def _toque(tree: UiTree, el_id: str, *, is_commit_action: bool = False) -> Tap:
    return Tap(rationale="t", element_id=el_id, x=None, y=None, is_commit_action=is_commit_action)


def _por_rid(tree: UiTree, rid: str) -> str:
    return next(e.id for e in tree.elements if e.resource_id.endswith("/" + rid))


def _por_texto(tree: UiTree, texto: str) -> str:
    return next(e.id for e in tree.elements if e.text == texto or e.desc == texto)


# ==================================================================== a função pura
def _perfil_do_instagram() -> UiTree:
    """Cabeçalho de um perfil: os rótulos dos contadores ("followers", "following") abrem listas — leitura. Os botões
    "Follow" e "Following" são os gatilhos de FOLLOW e UNFOLLOW. Por substring sem caixa, `text=Follow` casaria
    "followers" e `text=Following` o rótulo "following": abrir a lista numa leitura seria recusado."""
    p = "com.instagram.android:id/"
    return _arvore(
        f'<node class="{_TV}" text="followers" resource-id="{p}row_profile_header_textview_followers_title"'
        ' clickable="true" bounds="[200,300][340,340]"/>',
        f'<node class="{_TV}" text="following" resource-id="{p}row_profile_header_textview_following_title"'
        ' clickable="true" bounds="[360,300][500,340]"/>',
        f'<node class="{_BT}" text="Follow" resource-id="{p}profile_header_follow_button" clickable="true"'
        ' bounds="[40,400][340,470]"/>',
        f'<node class="{_BT}" text="Following" resource-id="{p}profile_header_unfollow_button" clickable="true"'
        ' bounds="[380,400][680,470]"/>',
        f'<node class="{_BT}" content-desc="New post" resource-id="{p}creation_tab" clickable="true"'
        ' bounds="[300,1200][420,1260]"/>',
        f'<node class="{_BT}" content-desc="Post" resource-id="{p}layout_comment_thread_post_button_icon"'
        ' clickable="true" bounds="[620,1100][700,1160]"/>',
    )


def test_no_catalogo_o_gatilho_e_o_commit_selector_de_uma_capacidade_com_efeito() -> None:
    tree = _perfil_do_instagram()
    seguir = efeito_fora_da_etapa("tap", _toque(tree, _por_texto(tree, "Follow")), _ctx(tree), tree, PKG)
    assert seguir is not None and "FOLLOW" in seguir
    deixar = efeito_fora_da_etapa("tap", _toque(tree, _por_texto(tree, "Following")), _ctx(tree), tree, PKG)
    assert deixar is not None and "UNFOLLOW" in deixar
    comentar = efeito_fora_da_etapa("tap", _toque(tree, _por_rid(tree, "layout_comment_thread_post_button_icon")),
                                    _ctx(tree), tree, PKG)
    assert comentar is not None and "COMMENT" in comentar
    pressao = LongPress(rationale="t", element_id=_por_texto(tree, "Follow"), x=None, y=None)
    assert efeito_fora_da_etapa("long_press", pressao, _ctx(tree), tree, PKG) is not None


def test_no_catalogo_rotulo_parecido_e_new_post_nao_sao_gatilho() -> None:
    """O seletor casa EXATO aqui: "followers" e "following" (rótulos dos contadores) não são "Follow" nem
    "Following"; "New post" só abre a criação, não publica."""
    tree = _perfil_do_instagram()
    for alvo in ("followers", "following", "New post"):
        assert efeito_fora_da_etapa("tap", _toque(tree, _por_texto(tree, alvo)), _ctx(tree), tree, PKG) is None, alvo


def _conversa_do_qa(*extra: str) -> UiTree:
    p = f"{QA}:id/"
    return _arvore(
        f'<node class="{_TV}" text="QA-001" resource-id="{p}chat_title" bounds="[100,60][600,120]"/>',
        f'<node class="{_ET}" text="" resource-id="{p}message_input" clickable="true" focusable="true"'
        ' bounds="[20,1160][560,1240]"/>',
        f'<node class="{_BT}" text="Enviar" content-desc="Enviar" resource-id="{p}send_button" clickable="true"'
        ' bounds="[580,1160][700,1240]"/>',
        f'<node class="{_ET}" text="Buscar conversa" resource-id="{p}search_input" clickable="true"'
        ' bounds="[20,140][700,200]"/>',
        *extra,
    )


def test_sem_catalogo_vale_o_vocabulario_do_efeito() -> None:
    tree = _conversa_do_qa()
    enviar = efeito_fora_da_etapa("tap", _toque(tree, _por_rid(tree, "send_button")), _ctx(tree), tree, QA)
    assert enviar is not None and "Enviar" in enviar
    assert efeito_fora_da_etapa("tap", _toque(tree, _por_rid(tree, "chat_title")), _ctx(tree), tree, QA) is None


def test_a_decisao_que_se_declara_efeito_conta_sempre() -> None:
    tree = _conversa_do_qa()
    toque = _toque(tree, _por_rid(tree, "chat_title"), is_commit_action=True)
    assert efeito_fora_da_etapa("tap", toque, _ctx(tree), tree, QA) is not None
    assert efeito_fora_da_etapa("tap", toque, _ctx(tree), tree, PKG) is not None


def test_enter_no_campo_de_composicao_envia_e_no_de_busca_nao() -> None:
    tree = _conversa_do_qa()
    no_campo = TypeText(rationale="t", text="oi", element_id=_por_rid(tree, "message_input"), press_enter=True)
    motivo = efeito_fora_da_etapa("type_text", no_campo, _ctx(tree), tree, QA)
    assert motivo is not None and "Enter" in motivo
    sem_enter = TypeText(rationale="t", text="oi", element_id=_por_rid(tree, "message_input"), press_enter=False)
    assert efeito_fora_da_etapa("type_text", sem_enter, _ctx(tree), tree, QA) is None
    na_busca = TypeText(rationale="t", text="QA-001", element_id=_por_rid(tree, "search_input"), press_enter=True)
    assert efeito_fora_da_etapa("type_text", na_busca, _ctx(tree), tree, QA) is None


def test_alvo_que_nao_existe_nao_e_recusado_aqui() -> None:
    """O `element_id` inexistente falha adiante, na ferramenta, sem chegar ao aparelho; não é efeito."""
    tree = _conversa_do_qa()
    assert efeito_fora_da_etapa("tap", _toque(tree, "e999"), _ctx(tree), tree, QA) is None


# ==================================================================== o caso 5f2de5, de ponta a ponta
IID = "android-01"


@dataclass
class QaQueEnviaNoEnter(FakeQaDevice):
    """O QA Messenger com Enter que envia, como na maioria dos chats: é o gesto da 5f2de5 por outro caminho (digitar
    e mandar dentro de uma etapa sem efeito). O fake padrão ignora o Enter."""

    def press_key(self, key: str) -> None:
        if key == "enter" and self.screen == "chat":
            self._enter(f"key:{key}")
            try:
                self._send()
            finally:
                self._leave()
            return
        super().press_key(key)


class AtorQueEnviaAntesDaHora(SimulatedProvider):
    """Repete a 5f2de5: ainda na etapa SEM efeito (`compose_message`), digita e manda (Enter no campo da mensagem) sem
    se declarar efeito. Na etapa de envio, como o modelo real fez, envia de novo sem olhar a conversa (redigita se o
    campo esvaziou). Sem a recusa saíam DUAS mensagens."""

    name = "ator-que-envia-antes-da-hora"

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        tree: UiTree = req.screen.tree
        etapa = req.ctx.step_key
        campo = next(iter(tree.find(resource_id="message_input")), None)
        botao = next(iter(tree.find(resource_id="send_button")), None)
        mensagem = req.ctx.parameters.get("message", "")
        ja_mandou = any("type_text" in h for h in req.history)
        if etapa == "compose_message" and campo is not None and not ja_mandou:
            return Decision(tool="type_text", args={"rationale": "[roteiro] digito e já mando", "text": mensagem,
                                                    "element_id": campo.id, "clear_first": True, "press_enter": True,
                                                    "is_commit_action": False}), Usage()
        if etapa == "send_message" and not req.ctx.commit_done and campo is not None and botao is not None:
            if campo.text.strip() != mensagem.strip():
                return Decision(tool="type_text", args={"rationale": "[roteiro] redigitar", "text": mensagem,
                                                        "element_id": campo.id, "clear_first": True,
                                                        "press_enter": False, "is_commit_action": False}), Usage()
            return Decision(tool="tap", args={"rationale": "[roteiro] enviar", "element_id": botao.id, "x": None,
                                              "y": None, "is_commit_action": True}), Usage()
        return await super().decide(req)


def _estado(h: Harness) -> AppState:
    assert h.state is not None
    return h.state


def _recusas_por_efeito(h: Harness, run_id: str) -> list[Any]:
    return _estado(h).db.query(
        "SELECT s.key, a.tool, a.side_effect, a.source, a.error FROM actions a JOIN attempts t ON t.id = a.attempt_id"
        " JOIN steps s ON s.id = t.step_id WHERE s.run_id=? AND a.status='rejected' AND a.error LIKE ?"
        " ORDER BY a.id", (run_id, f"{REJEICAO_EFEITO_FORA_DA_ETAPA}%"))


async def test_o_envio_numa_etapa_sem_efeito_e_recusado_e_sai_uma_mensagem_so(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1, factory=lambda rt: QaQueEnviaNoEnter(account=f"qa-user-{rt.index:02d}"))
    h.ai = CountingProvider(AtorQueEnviaAntesDaHora())
    await h.boot()
    antes = metricas.valor("executor.efeito_fora_da_etapa", origem="ai", ferramenta="type_text")
    try:
        run = h.run([IID])
        detalhe = await h.wait_run(run.id, timeout=60)
        assert detalhe.status == "completed", (detalhe.status, detalhe.status_detail)
        fake = h.fakes[IID]
        assert len(fake.messages) == 1, [m.text for m in fake.messages]
        recusas = _recusas_por_efeito(h, run.id)
        assert [(r["key"], r["tool"], r["side_effect"], r["source"]) for r in recusas] == [
            ("compose_message", "type_text", 1, "ai")]
        assert "Enter" in recusas[0]["error"] and "etapa que o declara" in recusas[0]["error"]
        assert metricas.valor("executor.efeito_fora_da_etapa", origem="ai", ferramenta="type_text") - antes == 1
    finally:
        if h.state is not None:
            await h.state.stop()


# ==================================================================== Instagram: o "Post" da folha de comentários
@dataclass
class InstagramComCampoDeComentario(InstagramComPublicacoes):
    """A folha "Comments" com o campo de escrita e o botão "Post" (`layout_comment_thread_post_button_icon`, medido no
    447). `postagens` conta os toques no botão: é o que chegaria ao Instagram."""

    postagens: list[int] = field(default_factory=list)

    def _build(self) -> list[Node]:
        nos = super()._build()
        if self.screen == "comments":
            nos += [Node(_ET, (40, 1100, 600, 1160), text="Add a comment…", rid="layout_comment_thread_edittext",
                         clickable=True, editable=True),
                    Node(_BT, (620, 1100, 700, 1160), desc="Post", rid="layout_comment_thread_post_button_icon",
                         clickable=True, action="postar")]
        return nos

    def tap(self, x: int, y: int) -> None:
        hit = next((n for n in self._nodes if n.clickable and n.bounds[0] <= x <= n.bounds[2]
                    and n.bounds[1] <= y <= n.bounds[3]), None)
        if hit is not None and hit.action == "postar":
            self.calls.append(f"tap:{x},{y}")
            self.postagens.append(1)
            return
        super().tap(x, y)


class AtorQuePostaAoLerAFolha(AtorDosComentarios):
    """Abre o post e os comentários (como `AtorDosComentarios`) e acrescenta uma etapa SEM efeito na folha aberta
    ("ler os comentários"). Nela toca "Post" sem se declarar efeito. Comentar (CREATE_COMMENT) é `approval_required`
    por padrão: sem a recusa, este toque passava ao largo da aprovação, da guarda e da trava de não repetir."""

    name = "ator-que-posta-ao-ler-a-folha"

    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        plano, uso = await super().plan(req)
        ler = PlanStep(key="ler_folha", title="Ler os comentários", goal="Ler os comentários da folha aberta.",
                       depends_on=["comentarios"],
                       postcondition=Postcondition(kind="model_judged", value="comentários lidos",
                                                   description="Os comentários da folha foram lidos."))
        return plano.model_copy(update={"steps": [*plano.steps, ler]}), uso

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        if req.ctx.step_key != "ler_folha":
            return await super().decide(req)
        post = req.screen.tree.find(resource_id="layout_comment_thread_post_button_icon")
        if post and not any(REJEICAO_EFEITO_FORA_DA_ETAPA in h for h in req.history):
            return Decision(tool="tap", args={"rationale": "[roteiro] publicar", "element_id": post[0].id, "x": None,
                                              "y": None, "is_commit_action": False}), Usage()
        return Decision(tool="step_done", args={"rationale": "[roteiro] lidos", "evidence": "folha Comments",
                                                "delivery_level": None}), Usage()

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        if req.ctx.step_key == "ler_folha":
            return Verdict(satisfied="yes", evidence="[roteiro] folha lida"), Usage()
        return await super().verify(req)


@pytest_asyncio.fixture
async def folha_com_post(tmp_path: Path) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 1, factory=lambda rt: InstagramComCampoDeComentario(account="eu.teste", screen="feed"))
    h.ai = CountingProvider(AtorQuePostaAoLerAFolha(LEGENDA))
    await h.boot()
    s = _estado(h)
    s.db.execute("UPDATE instances SET app_id='instagram' WHERE id=?", (IID,))
    s.devices.get(IID).app_id = "instagram"
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_post_tocado_numa_etapa_sem_efeito_da_folha_e_recusado_sem_toque_nem_aprovacao(folha_com_post: Harness) -> None:
    h = folha_com_post
    antes = metricas.valor("executor.efeito_fora_da_etapa", origem="ai", ferramenta="tap")
    run = h.run([IID], command=f"abra os comentários do post de @anarabottinipsicopedagoga com o texto \"{LEGENDA}\"")
    detalhe = await h.wait_run(run.id, timeout=60)
    assert detalhe.status == "completed", (detalhe.status, detalhe.status_detail)
    fake = h.fakes[IID]
    assert isinstance(fake, InstagramComCampoDeComentario)
    assert fake.postagens == []                                   # nada chegou ao botão "Post"
    recusas = _recusas_por_efeito(h, run.id)
    assert [(r["key"], r["tool"], r["side_effect"]) for r in recusas] == [("ler_folha", "tap", 1)]
    assert "CREATE_COMMENT" in recusas[0]["error"] or "COMMENT" in recusas[0]["error"]
    assert _estado(h).db.scalar("SELECT COUNT(*) FROM pending_approvals") == 0
    assert metricas.valor("executor.efeito_fora_da_etapa", origem="ai", ferramenta="tap") - antes == 1


# ==================================================================== (C) efeito repetido fecha incerto
class VerificadorQueContaDuasCopias(SimulatedProvider):
    """O verificador vê a mensagem DESTA execução duas vezes na conversa (o que a 5f2de5 deixou na tela)."""

    name = "verificador-que-conta-duas-copias"

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        veredito, uso = await super().verify(req)
        if req.ctx.step_key == "send_message":
            veredito = veredito.model_copy(update={"copias": 2})
        return veredito, uso


async def test_duas_copias_vistas_pelo_verificador_fecham_incerto_com_o_resultado(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    h.ai = CountingProvider(VerificadorQueContaDuasCopias())
    await h.boot()
    try:
        run = h.run([IID])
        detalhe = await h.wait_run(run.id, timeout=60,
                                   statuses=("completed", "completed_with_issues", "cancelled", "failed", "waiting_user"))
        etapa = _estado(h).db.one("SELECT status, status_detail, result FROM steps WHERE run_id=? AND key='send_message'",
                                  (run.id,))
        assert etapa is not None and etapa["status"] == "uncertain", (detalhe.status, etapa and dict(etapa))
        assert (etapa["status_detail"] or "").startswith("efeito repetido (2)")
        resultado = loads(etapa["result"], {})
        assert resultado["efeito_repetido"] == {"copias": 2, "fonte": "verificador"}
        assert resultado["verified"] is False
        objetivo = _estado(h).db.one("SELECT status FROM objectives WHERE run_id=?", (run.id,))
        assert objetivo is not None and objetivo["status"] == "uncertain"
        assert len(h.fakes[IID].messages) == 1                 # nada é reenviado
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_sem_repeticao_o_resultado_nao_leva_o_campo(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    h.ai = CountingProvider(SimulatedProvider())
    await h.boot()
    try:
        run = h.run([IID])
        detalhe = await h.wait_run(run.id, timeout=60)
        assert detalhe.status == "completed"
        etapa = _estado(h).db.one("SELECT result FROM steps WHERE run_id=? AND key='send_message'", (run.id,))
        assert etapa is not None and "efeito_repetido" not in loads(etapa["result"], {})
    finally:
        if h.state is not None:
            await h.state.stop()


def _clonar(db: Any, tabela: str, linha: Any, **troca: Any) -> None:
    dados = {**dict(linha), **troca}
    colunas = ", ".join(dados)
    db.execute(f"INSERT INTO {tabela}({colunas}) VALUES ({', '.join('?' for _ in dados)})", tuple(dados.values()))


async def test_copias_pelas_acoes_contam_a_mesma_etapa_modelo_e_alvo_na_mesma_versao(tmp_path: Path) -> None:
    """Duas etapas da mesma etapa-modelo e do mesmo `item`, na mesma versão do plano, com ação de efeito `done`: 2.
    Em outra versão do plano ("repetir" é gesto da pessoa), com outro item ou com a ação recusada: não conta."""
    h = Harness(tmp_path, 1)
    h.ai = CountingProvider(SimulatedProvider())
    await h.boot()
    try:
        run = h.run([IID])
        assert (await h.wait_run(run.id, timeout=60)).status == "completed"
        s = _estado(h)
        envio = s.db.one("SELECT * FROM steps WHERE run_id=? AND key='send_message'", (run.id,))
        assert envio is not None
        tentativa = s.db.one("SELECT * FROM attempts WHERE step_id=?", (envio["id"],))
        acao = s.db.one("SELECT * FROM actions WHERE attempt_id=? AND side_effect=1", (tentativa["id"],))
        assert acao is not None and acao["status"] == "done"
        assert s.repo.copias_pelas_acoes(envio["id"]) == 1

        def copia(sufixo: str, *, status: str = "done", **etapa: Any) -> None:
            _clonar(s.db, "steps", envio, id=f"{envio['id']}-{sufixo}", key=f"send_message_{sufixo}",
                    template_key="send_message", seq=envio["seq"] + 100 + len(sufixo), **etapa)
            _clonar(s.db, "attempts", tentativa, id=f"{tentativa['id']}-{sufixo}", step_id=f"{envio['id']}-{sufixo}")
            dados = {k: v for k, v in dict(acao).items() if k != "id"}
            _clonar(s.db, "actions", dados, attempt_id=f"{tentativa['id']}-{sufixo}", status=status)

        copia("outraversao", plan_version=envio["plan_version"] + 1)
        copia("outroitem", variables='{"item": "QA-002"}')
        copia("recusada", status="rejected")
        assert s.repo.copias_pelas_acoes(envio["id"]) == 1
        copia("repetida")
        assert s.repo.copias_pelas_acoes(envio["id"]) == 2
    finally:
        if h.state is not None:
            await h.state.stop()

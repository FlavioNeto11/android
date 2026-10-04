"""29.30 (PR-A): publicar no PRÓPRIO feed como capability do catálogo do Instagram. Nível `simulated`.

Cobre a carga das três ações novas (READ_POSTS_COUNT, PUT_MEDIA_IN_GALLERY, CREATE_POST), o balde `posts` sem alvo, a
prova local `count_gt`, o teto de uma publicação por hora e por dia e o despacho da `internal` com o dublê. Os seletores
do editor de publicação do Instagram 447 NÃO foram medidos num aparelho: a prova real é `not_run`.
"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.automation.hierarchy import UiTree, parse_hierarchy
from app.models import InteractionStatus, InteractionType
from app.planning.capabilities import (BALDES_SEM_ALVO, CapabilityNode, compose, counterparty_error, load_catalog,
                                       local_proof_error, preparo_error)
from app.social.policy import BUCKET_TYPES, DEFAULT_LIMITS, UMA_CONTA_POR_ALVO
from app.taskqueue.executor import Outcome, StepExecutor
from app.taskqueue.midia_galeria import MidiaRecusada, colocar_midia_na_galeria
from app.taskqueue.proofs import local_proof_holds
from app.util import now, to_iso

from .fake_instagram import FakeInstagram
from .test_capabilities import IG, build, perfil

SELETOR = "id=row_profile_header_textview_post_count"
PROVA = f"count_gt:posts_antes:{SELETOR}"


def _perfil_com_contagem(texto: str | None) -> UiTree:
    no = "" if texto is None else (
        f'<node class="android.widget.TextView" text="{texto}" '
        'resource-id="com.instagram.android:id/row_profile_header_textview_post_count" bounds="[0,0][100,50]"/>')
    return parse_hierarchy("<hierarchy>" + no + "</hierarchy>")


def _etapa(**bindings: str) -> Any:
    return SimpleNamespace(bindings=dict(bindings), card_guard=[], band_guard=[])


# ============================================================================================ 1. o catálogo
def test_o_catalogo_carrega_as_tres_acoes_novas() -> None:
    cat = load_catalog(IG)
    assert cat is not None
    leitura, midia, post = cat.get("READ_POSTS_COUNT"), cat.get("PUT_MEDIA_IN_GALLERY"), cat.get("CREATE_POST")
    assert not leitura.side_effect and leitura.risk == "low" and leitura.default_policy == "autonomous"
    assert leitura.saidas == ("posts_antes",)
    assert midia.internal and not midia.side_effect and midia.bindings == ("image_id",)
    assert post.side_effect and post.risk == "high" and post.default_policy == "approval_required"
    assert post.needs_draft and post.interaction_type == InteractionType.post_published.value
    assert post.limit_bucket == "posts" and post.counterparty is None
    assert post.commit_selector and post.commit_guard == ("{content}",)
    assert post.local_proof == PROVA and local_proof_error(post.local_proof) is None
    # nenhum argumento que o `open_effect` tomaria por alvo (`username` ou `target`)
    assert not {"username", "target"} & {*post.bindings, *post.optional_bindings}
    # a interna não é oferecida ao planejador; as outras duas são
    assert "PUT_MEDIA_IN_GALLERY" not in {c.key for c in cat.offered}
    assert {"READ_POSTS_COUNT", "CREATE_POST"} <= {c.key for c in cat.offered}


def test_o_compose_poe_a_midia_na_galeria_antes_do_create_post_com_o_mesmo_image_id() -> None:
    """A interna não é oferecida ao planejador: quem a pede é o `preparo` de CREATE_POST (29.30, PR-B)."""
    cat = load_catalog(IG)
    etapas, faltas = compose(cat, [
        CapabilityNode(key="contar", capability="READ_POSTS_COUNT"),
        CapabilityNode(key="post", capability="CREATE_POST", depends_on=["contar"],
                       bindings={"image_id": "img-1", "content": "legenda"})])
    assert not faltas
    assert [e.capability for e in etapas] == ["READ_POSTS_COUNT", "PUT_MEDIA_IN_GALLERY", "CREATE_POST"]
    midia, post = etapas[1], etapas[2]
    assert midia.bindings == {"image_id": "img-1"} and midia.depends_on == ["contar"]
    assert post.depends_on == ["contar", "post__put_media_in_gallery"]
    assert post.commit_selector and post.max_attempts == 1


def test_sem_image_id_o_create_post_vira_pergunta_antes_de_qualquer_efeito() -> None:
    etapas, faltas = compose(load_catalog(IG), [
        CapabilityNode(key="post", capability="CREATE_POST", bindings={"content": "legenda"})])
    assert not any(e.capability == "CREATE_POST" for e in etapas) and faltas


def test_preparo_invalido_e_recusado_na_carga() -> None:
    cat = load_catalog(IG)
    post = cat.get("CREATE_POST")
    por_chave = {c.key: c for c in cat.capabilities}
    assert preparo_error(post, por_chave) is None
    assert "não existe" in (preparo_error(replace(post, preparo=("NADA",)), por_chave) or "")
    assert "não é `internal`" in (preparo_error(replace(post, preparo=("LIKE_POST",)), por_chave) or "")
    assert "exige image_id" in (preparo_error(replace(post, bindings=()), por_chave) or "")


# ====================================================================================== 2. balde sem alvo
def test_counterparty_error_isenta_so_o_balde_posts() -> None:
    post = load_catalog(IG).get("CREATE_POST")
    assert BALDES_SEM_ALVO == frozenset({"posts"}) and counterparty_error(post) is None
    for balde in ("likes", "comments", "follows", "dms"):
        erro = counterparty_error(replace(post, limit_bucket=balde))
        assert erro is not None and "counterparty" in erro, balde


def test_posts_nao_e_uma_conta_por_alvo() -> None:
    assert "posts" not in UMA_CONTA_POR_ALVO
    assert BUCKET_TYPES["posts"] == (InteractionType.post_published.value,)


# ============================================================================================ 3. count_gt
@pytest.mark.parametrize("valor, esperado", [
    ("count_gt:posts_antes:id=x", None),
    ("count_gt:Posts:id=x", "binding"),
    ("count_gt::id=x", "binding"),
    ("count_gt:posts_antes:", "sem seletor"),
    ("count_gt:posts_antes:   ", "sem seletor"),
    ("count_gt:posts_antes:id=a&id=b", "`&`"),
])
def test_local_proof_error_do_count_gt(valor: str, esperado: str | None) -> None:
    erro = local_proof_error(valor)
    assert (erro is None) if esperado is None else (erro is not None and esperado in erro)


@pytest.mark.parametrize("texto, antes, esperado", [
    ("13", "12", True),
    ("1,234", "999", True),               # separador de milhar
    ("1.234", "1233", True),
    ("12", "12", False),                  # igual: nenhuma publicação nova
    ("11", "12", False),
    ("12", None, None),                   # sem o binding
    ("12", "doze", None),                 # binding não numérico
    ("1.2K", "1", None),                  # abreviado: não dá para afirmar
    ("1 mil", "0", None),
    ("sem número", "3", None),
])
def test_count_gt_pela_arvore(texto: str, antes: str | None, esperado: bool | None) -> None:
    etapa = _etapa(**({} if antes is None else {"posts_antes": antes}))
    assert local_proof_holds(PROVA, etapa, _perfil_com_contagem(texto)) is esperado


def test_count_gt_sem_o_elemento_nao_reprova() -> None:
    assert local_proof_holds(PROVA, _etapa(posts_antes="3"), _perfil_com_contagem(None)) is None


# =================================================================================== 4. o teto de posts
def _publicou(svc: Any, pid: str, *, horas_atras: float = 0) -> None:
    iid = svc.record_interaction(pid, type=InteractionType.post_published.value, direction="outbound",
                                 status=InteractionStatus.confirmed.value, counterparty=None, run_id="run-0").id
    if horas_atras:
        svc.repo.update_interaction(pid, iid, occurred_at=to_iso(now() - timedelta(hours=horas_atras)))


def test_os_limites_de_posts_sao_um_por_hora_e_um_por_dia() -> None:
    assert DEFAULT_LIMITS["posts_per_hour"] == 1 and DEFAULT_LIMITS["posts_per_day"] == 1


def test_o_aquecimento_nao_leva_o_teto_de_posts_a_zero(tmp_path: Path) -> None:
    """`max(1, 1*34//100)`: perfil em aquecimento (padrão) continua com UMA publicação, nunca zero."""
    svc, repo, policies, _db = build(tmp_path)
    pid = perfil(svc)
    repo.update_profile(pid, {"automation_policy": '{"limits": {"cooldown_between_external_actions_s": 0}}'})
    assert policies._aquecendo(pid, policies.limits_for(pid), now())
    assert policies._teto_com_aquecimento(1, policies.limits_for(pid), True) == 1
    post = load_catalog(IG).get("CREATE_POST")
    assert policies.check(pid, post).allowed


def test_o_segundo_post_no_mesmo_dia_e_recusado(tmp_path: Path) -> None:
    svc, repo, policies, _db = build(tmp_path)
    pid = perfil(svc)
    repo.update_profile(pid, {"automation_policy": '{"limits": {"cooldown_between_external_actions_s": 0}}'})
    post = load_catalog(IG).get("CREATE_POST")
    primeiro = policies.check(pid, post, run_id="run-1")
    assert primeiro.allowed and primeiro.needs_approval            # nasce em approval_required
    _publicou(svc, pid)
    # na mesma hora: o teto por hora
    segundo = policies.check(pid, post, run_id="run-2")
    assert not segundo.allowed and "posts por hora" in segundo.reason and segundo.retry_at


def test_o_teto_por_dia_vale_mesmo_passada_a_hora(tmp_path: Path) -> None:
    svc, repo, policies, _db = build(tmp_path)
    pid = perfil(svc)
    repo.update_profile(pid, {"automation_policy": '{"limits": {"cooldown_between_external_actions_s": 0}}'})
    _publicou(svc, pid, horas_atras=3)
    veredito = policies.check(pid, load_catalog(IG).get("CREATE_POST"), run_id="run-3")
    assert not veredito.allowed and "posts por dia" in veredito.reason


def test_post_de_outra_conta_nao_gasta_o_teto_desta(tmp_path: Path) -> None:
    """Sem alvo, não há "uma conta por alvo": o teto é por perfil."""
    svc, repo, policies, _db = build(tmp_path)
    a, b = perfil(svc, "ana.silva91182"), perfil(svc, "bia.souza91182", "android-01")
    for p in (a, b):
        repo.update_profile(p, {"automation_policy": '{"limits": {"cooldown_between_external_actions_s": 0}}'})
    _publicou(svc, a)
    assert policies.check(b, load_catalog(IG).get("CREATE_POST"), run_id="run-4").allowed


# ============================================================================== 5. PUT_MEDIA_IN_GALLERY
class _Imagens:
    """Dublê de `PersonaImageService`: `obter` consulta pelo par (persona, imagem), como o real."""

    def __init__(self) -> None:
        self.linhas = {("p1", "img-ok"): SimpleNamespace(id="img-ok", status="ready", storage_key="k1"),
                       ("p1", "img-gerando"): SimpleNamespace(id="img-gerando", status="pending", storage_key=None),
                       ("p1", "img-sem-arquivo"): SimpleNamespace(id="img-sem-arquivo", status="ready",
                                                                    storage_key="k2"),
                       ("p2", "img-da-outra"): SimpleNamespace(id="img-da-outra", status="ready", storage_key="k3")}
        self.blobs = {"k1": b"\xff\xd8JPEG-DA-P1", "k3": b"\xff\xd8JPEG-DA-P2"}

    def obter(self, persona_id: str, image_id: str) -> Any:
        return self.linhas.get((persona_id, image_id))

    def bytes_de(self, registro: Any) -> bytes | None:
        return self.blobs.get(registro.storage_key)


def test_a_imagem_pronta_da_persona_vira_push_mais_indexacao() -> None:
    aparelho = FakeInstagram()
    caminho = colocar_midia_na_galeria(_Imagens(), "p1", "img-ok", aparelho.enviar_midia_para_galeria)
    assert caminho == "/sdcard/Pictures/Central/img_img-ok.jpg"
    assert aparelho.midias_na_galeria == [{"nome": "img_img-ok", "bytes": b"\xff\xd8JPEG-DA-P1", "indexada": True}]


@pytest.mark.parametrize("persona, imagem, motivo", [
    ("p1", "img-da-outra", "não é desta persona"),       # imagem de outra pessoa
    ("p1", "nao-existe", "não é desta persona"),
    ("p1", "img-gerando", "não está pronta"),
    ("p1", "img-sem-arquivo", "sem arquivo"),
    ("p1", "", "image_id"),
    (None, "img-ok", "não tem persona"),
])
def test_imagem_de_outra_persona_ou_nao_pronta_e_recusada_sem_push(persona: str | None, imagem: str,
                                                                    motivo: str) -> None:
    aparelho = FakeInstagram()
    with pytest.raises(MidiaRecusada) as erro:
        colocar_midia_na_galeria(_Imagens(), persona, imagem, aparelho.enviar_midia_para_galeria)
    assert motivo in str(erro.value)
    assert aparelho.midias_na_galeria == []                        # nada tocou o aparelho


def test_sem_o_servico_de_imagens_recusa() -> None:
    with pytest.raises(MidiaRecusada):
        colocar_midia_na_galeria(None, "p1", "img-ok", FakeInstagram().enviar_midia_para_galeria)


class _RepoDeMentira:
    def __init__(self) -> None:
        self.evidencias: list[str] = []
        self.transicoes: list[str] = []
        self.tentativas: list[str] = []
        self.db = SimpleNamespace(tx=nullcontext)

    async def add_evidence_async(self, **kw: Any) -> None:
        self.evidencias.append(kw["note"])

    def transition_step(self, step_id: str, status: Any, **kw: Any) -> None:
        self.transicoes.append(str(status))

    def finish_attempt(self, attempt_id: str, status: Any, **kw: Any) -> None:
        self.tentativas.append(str(status))


def _executor(imagens: Any, aparelho: FakeInstagram) -> tuple[StepExecutor, Any, _RepoDeMentira]:
    ex = StepExecutor.__new__(StepExecutor)
    repo = _RepoDeMentira()
    ex.repo, ex.persona_images, ex.social = repo, imagens, None
    ex.devices = SimpleNamespace(io_factory=lambda rt: aparelho)      # aparelho falso: o dublê tem o método

    async def rodar(fn: Any, *args: Any, timeout: float, label: str = "") -> Any:
        return fn(*args)

    rt = SimpleNamespace(id="android-02", io=aparelho, executor=SimpleNamespace(run=rodar))
    return ex, rt, repo


def _despachar(ex: StepExecutor, rt: Any, image_id: str, profile_id: str | None = "p1") -> Any:
    step = SimpleNamespace(id="s1", title="Colocar a imagem", bindings={"image_id": image_id}, timeout_s=30,
                           attempts=1, max_attempts=3)
    return asyncio.run(ex._run_interna(run={"id": "r1"}, objective={"profile_id": profile_id},
                                       step=step, attempt_id="a1", rt=rt))


def test_o_executor_despacha_a_interna_e_comprova_a_etapa() -> None:
    aparelho = FakeInstagram()
    ex, rt, repo = _executor(_Imagens(), aparelho)
    desfecho = _despachar(ex, rt, "img-ok")
    assert desfecho.outcome == Outcome.succeeded and "galeria" in (desfecho.detail or "")
    assert len(aparelho.midias_na_galeria) == 1
    assert repo.transicoes and repo.tentativas


def test_o_executor_recusa_imagem_de_outra_persona_sem_tocar_no_aparelho() -> None:
    aparelho = FakeInstagram()
    ex, rt, repo = _executor(_Imagens(), aparelho)
    desfecho = _despachar(ex, rt, "img-da-outra")
    assert desfecho.outcome == Outcome.failed and "não é desta persona" in (desfecho.detail or "")
    assert aparelho.midias_na_galeria == [] and not repo.transicoes


# ====================================================================================== aprovação com a imagem (PR-B)
def test_a_aprovacao_leva_o_image_id_da_etapa_e_so_dela(tmp_path: Path) -> None:
    """Quem aprova a legenda de CREATE_POST vê a imagem: o `image_id` vem dos argumentos da etapa (sem coluna nova)."""
    from app.social.approvals import ApprovalStore

    from .fake_skills import banco

    db = banco(tmp_path, "aprovacao.sqlite3")
    try:
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
                   " VALUES ('r-1', 'k-1', 'publicar', 'execute', 'running', 0, '[\"android-01\"]',"
                   " '2026-10-04T15:00:00Z')")
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version)"
                   " VALUES ('r-1:o', 'r-1', 'android-01', 'running', 1)")
        for sid, argumentos in (("r-1:post", '{"image_id": "img-9", "content": "Fim de tarde"}'),
                                ("r-1:coment", '{"content": "Que lindo"}'), ("r-1:torto", "não é json")):
            db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                       " side_effect, postcondition, timeout_s, max_attempts, status, bindings)"
                       " VALUES (?, 'r-1', 'r-1:o', 'android-01', 1, 1, ?, 't', 'g', 1, '{}', 60, 1, 'pending', ?)",
                       (sid, sid, argumentos))
        store = ApprovalStore(db)
        post = store.open(profile_id=None, capability="CREATE_POST", summary="Publicar", step_id="r-1:post")
        coment = store.open(profile_id=None, capability="CREATE_COMMENT", summary="Comentar", step_id="r-1:coment")
        torto = store.open(profile_id=None, capability="CREATE_POST", summary="Publicar", step_id="r-1:torto")
        solta = store.open(profile_id=None, capability="FOLLOW", summary="Seguir")
        assert post.image_id == "img-9" and post.to_dict()["image_id"] == "img-9"
        assert coment.image_id is None and torto.image_id is None and solta.image_id is None
    finally:
        db.close()


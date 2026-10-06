"""31.53 (lado Jev do 28.10 F5): a família de um pedido entre personas também se compara pelo OBJETO, e o texto que cita
outra conta da família passa por aprovação.

(1) O efeito sem pessoa como alvo (`CREATE_POST`, `objeto_alvo: [image_id]`) escapava da regra por pessoa do 30.62: duas
personas do mesmo pedido publicavam a mesma imagem. Agora a mesma imagem é recusada (não adiada); o objeto ambíguo passa
por aprovação; sem pedido, nada muda.
(2) O rascunho que cita o @ de OUTRA persona do mesmo pedido passa por aprovação, e o motivo não leva o @ nem o texto.

Nível de prova: `simulated` (banco de teste, perfis de teste). Nada real.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.models import InteractionStatus, ProfileCreate
from app.planning.capabilities import capability_of
from app.social.approvals import ApprovalStore
from app.social.policy import MOTIVO_CITA_A_FAMILIA, ContextoDoPedido, PolicyEngine

from .test_capabilities import IG, SENHA, perfil
from .test_repetido_entre_execucoes import _SERVICOS, _conta, _etapa

POST_A = {"image_id": "img-1", "content": "Fim de tarde"}


def _familia(tmp_path: Path) -> tuple[Any, PolicyEngine, Any, str, str]:
    repo, policies, db, a = _conta(tmp_path)                       # a: tadeu.quintela4821, android-01
    b = perfil(_SERVICOS[a], "bia.souza40517", "android-02")
    repo.update_profile(b, {"automation_policy": '{"limits": {"warmup_days": 0, '
                                                 '"cooldown_between_external_actions_s": 0}}'})
    return repo, policies, db, a, b


def _pedido_aberto(db: Any, pid: str, argumentos: dict[str, str]) -> str:
    sid = _etapa(db, "r-a", "CREATE_POST", argumentos)
    return ApprovalStore(db).open(profile_id=pid, capability="CREATE_POST", summary="Publicar",
                                  content=argumentos.get("content", ""), run_id="r-a", objective_id="r-a:android-01",
                                  step_id=sid).id


# ------------------------------------------------------------------------------------------- (1) pelo objeto
def test_a_mesma_imagem_de_outra_persona_do_pedido_e_recusada_nao_adiada(tmp_path: Path) -> None:
    _repo, policies, db, a, b = _familia(tmp_path)
    _pedido_aberto(db, a, POST_A)
    pedido = ContextoDoPedido(raiz="r-b", familia=frozenset({a, b}))
    veredito = policies.check(b, capability_of(IG, "CREATE_POST"), step_id="r-b:x", pedido=pedido,
                              bindings={"image_id": "img-1", "content": "Outra legenda"})
    assert not veredito.allowed and veredito.retry_at is None
    assert "31.53" in veredito.reason and "recusado, não adiado" in veredito.reason


def test_a_mesma_imagem_ja_publicada_por_outra_persona_do_pedido_tambem(tmp_path: Path) -> None:
    repo, policies, db, a, b = _familia(tmp_path)
    publicar = capability_of(IG, "CREATE_POST")
    sid = _etapa(db, "r-a", "CREATE_POST", POST_A)
    repo.record_interaction(a, type=publicar.interaction_type, direction="outbound",
                            status=InteractionStatus.confirmed.value, counterparty=None, thread_key=None, target=None,
                            outgoing_content=None, app_id="instagram", run_id="r-a", step_id=sid)
    pedido = ContextoDoPedido(raiz="r-b", familia=frozenset({a, b}))
    veredito = policies.check(b, publicar, step_id="r-b:x", pedido=pedido, bindings={"image_id": "img-1"})
    assert not veredito.allowed and "31.53" in veredito.reason


def test_a_mesma_imagem_na_etapa_em_curso_da_irma_sem_pedido_de_aprovacao_tambem(tmp_path: Path) -> None:
    """31.64: com `publicar_sem_aprovacao` a etapa da irmã passa a porta sem pedido e sem saída até o commit; enquanto
    ela roda ou verifica, a mesma imagem é recusada. Pronta (ainda não passou a porta) ou já falha, não conta."""
    _repo, policies, db, a, b = _familia(tmp_path)
    publicar = capability_of(IG, "CREATE_POST")
    sid = _etapa(db, "r-a", "CREATE_POST", POST_A)
    db.execute("UPDATE objectives SET profile_id=? WHERE id='r-a:android-01'", (a,))
    pedido = ContextoDoPedido(raiz="r-b", familia=frozenset({a, b}))
    for status, recusa in (("ready", False), ("running", True), ("verifying", True), ("failed", False)):
        db.execute("UPDATE steps SET status=? WHERE id=?", (status, sid))
        veredito = policies.check(b, publicar, step_id="r-b:x", pedido=pedido, bindings={"image_id": "img-1"})
        assert ("31.53" in veredito.reason and not veredito.allowed) is recusa, (status, veredito.reason)
    db.execute("UPDATE steps SET status='running' WHERE id=?", (sid,))
    outra = policies.check(b, publicar, step_id="r-b:x", pedido=pedido, bindings={"image_id": "img-2"})
    assert "31.53" not in outra.reason


def _etapa_tomada(db: Any, run: str, pid: str, quando: str, aparelho: str) -> str:
    """Uma etapa de publicação com a mesma imagem, já TOMADA (`running`, `started_at`), da persona `pid` (um aparelho por
    etapa em curso: o banco não deixa duas `running` no mesmo)."""
    sid = _etapa(db, run, "CREATE_POST", POST_A)
    db.execute("UPDATE objectives SET profile_id=? WHERE id=?", (pid, f"{run}:android-01"))
    db.execute("UPDATE steps SET status='running', started_at=?, instance_id=? WHERE id=?", (quando, aparelho, sid))
    return sid


def test_irmas_tomadas_juntas_com_a_mesma_imagem_exatamente_uma_passa(tmp_path: Path) -> None:
    """F1 da revisão do #350: a etapa vira `running` na tomada, antes da porta. Duas irmãs em `running` com a mesma
    imagem: a mais antiga passa e a outra é recusada (antes do conserto, as duas eram recusadas). Mesmo `started_at`:
    o `id` desempata."""
    _repo, policies, db, a, b = _familia(tmp_path)
    publicar = capability_of(IG, "CREATE_POST")
    pedido = ContextoDoPedido(raiz="r-a", familia=frozenset({a, b}))
    for quando_a, quando_b in (("2026-10-05T01:00:00Z", "2026-10-05T01:00:05Z"),
                               ("2026-10-05T01:00:00Z", "2026-10-05T01:00:00Z")):
        db.execute("DELETE FROM steps")
        sa = _etapa_tomada(db, "r-a", a, quando_a, "android-01")
        sb = _etapa_tomada(db, "r-b", b, quando_b, "android-02")
        recusadas = [sid for pid, sid in ((a, sa), (b, sb))
                     if "31.53" in policies.check(pid, publicar, step_id=sid, pedido=pedido,
                                                  bindings={"image_id": "img-1"}).reason]
        assert recusadas == [sb], (quando_a, quando_b, recusadas)       # "r-a…" < "r-b…" no desempate


def test_tres_irmas_tomadas_juntas_so_a_mais_antiga_passa(tmp_path: Path) -> None:
    repo, policies, db, a, b = _familia(tmp_path)
    c = _SERVICOS[a].create_profile(ProfileCreate(username="carla.dias7781", password=SENHA)).id   # sem aparelho
    repo.update_profile(c, {"automation_policy": '{"limits": {"warmup_days": 0, '
                                                 '"cooldown_between_external_actions_s": 0}}'})
    publicar = capability_of(IG, "CREATE_POST")
    pedido = ContextoDoPedido(raiz="r-a", familia=frozenset({a, b, c}))
    etapas = {c: _etapa_tomada(db, "r-c", c, "2026-10-05T01:00:01Z", "android-03"),
              a: _etapa_tomada(db, "r-a", a, "2026-10-05T01:00:03Z", "android-01"),
              b: _etapa_tomada(db, "r-b", b, "2026-10-05T01:00:02Z", "android-02")}
    passaram = [pid for pid, sid in etapas.items()
                if "31.53" not in policies.check(pid, publicar, step_id=sid, pedido=pedido,
                                                 bindings={"image_id": "img-1"}).reason]
    assert passaram == [c]


@pytest.mark.parametrize("parada", ["retry_wait", "waiting_user"])
def test_s1_a_que_volta_com_a_tomada_antiga_nao_passa_a_irma_que_ja_passou(tmp_path: Path, parada: str) -> None:
    """S1 da revisão do #350: A é tomada primeiro e para (`retry_wait` ou `waiting_user`) antes da porta; B é tomada
    depois, PASSA a porta e publica; A volta a `ready`, é retomada com o `started_at` da primeira tomada e chega à porta.
    Pela ordem das tomadas A seria a mais antiga e as duas publicariam; com a marca de B, A é recusada."""
    _repo, policies, db, a, b = _familia(tmp_path)
    publicar = capability_of(IG, "CREATE_POST")
    pedido = ContextoDoPedido(raiz="r-a", familia=frozenset({a, b}))
    sa = _etapa_tomada(db, "r-a", a, "2026-10-05T01:00:00Z", "android-01")
    db.execute("UPDATE steps SET status=? WHERE id=?", (parada, sa))
    sb = _etapa_tomada(db, "r-b", b, "2026-10-05T01:00:30Z", "android-02")
    assert "31.53" not in policies.check(b, publicar, step_id=sb, pedido=pedido, bindings={"image_id": "img-1"}).reason
    db.execute("UPDATE steps SET passou_a_porta=1 WHERE id=?", (sb,))            # o que a porta grava ao liberar B
    db.execute("UPDATE steps SET status='ready' WHERE id=?", (sa,))
    db.execute("UPDATE steps SET status='running' WHERE id=?", (sa,))            # retomada: `started_at` fica o de t0
    veredito = policies.check(a, publicar, step_id=sa, pedido=pedido, bindings={"image_id": "img-1"})
    assert not veredito.allowed and "31.53" in veredito.reason


def test_s1_a_irma_que_passou_e_falhou_nao_conta_mais(tmp_path: Path) -> None:
    _repo, policies, db, a, b = _familia(tmp_path)
    publicar = capability_of(IG, "CREATE_POST")
    pedido = ContextoDoPedido(raiz="r-a", familia=frozenset({a, b}))
    sb = _etapa_tomada(db, "r-b", b, "2026-10-05T01:00:00Z", "android-02")
    db.execute("UPDATE steps SET passou_a_porta=1, status='failed' WHERE id=?", (sb,))
    sa = _etapa_tomada(db, "r-a", a, "2026-10-05T01:00:30Z", "android-01")
    assert "31.53" not in policies.check(a, publicar, step_id=sa, pedido=pedido, bindings={"image_id": "img-1"}).reason


def test_s2_marcas_antigas_concluidas_nao_tiram_a_irma_em_curso_do_lote(tmp_path: Path) -> None:
    """S2 da revisão do #350: 201 etapas da irmã já concluídas (`succeeded`) e marcadas, com outras imagens, e 1 em curso
    marcada com a mesma imagem. Antes, as marcas concluídas entravam sem janela e o lote (`ORDER BY id LIMIT 200`) trazia
    as mais antigas: a em curso ficava de fora e a mesma imagem passava. Agora a recusa sai."""
    _repo, policies, db, a, b = _familia(tmp_path)
    publicar = capability_of(IG, "CREATE_POST")
    pedido = ContextoDoPedido(raiz="r-a", familia=frozenset({a, b}))
    for n in range(201):
        run = f"r-b{n:03d}"
        sid = _etapa(db, run, "CREATE_POST", {"image_id": f"img-velha-{n}"})
        db.execute("UPDATE objectives SET profile_id=? WHERE id=?", (b, f"{run}:android-01"))
        db.execute("UPDATE steps SET status='succeeded', passou_a_porta=1 WHERE id=?", (sid,))
    sb = _etapa_tomada(db, "r-zz", b, "2026-10-05T01:00:00Z", "android-02")
    db.execute("UPDATE steps SET passou_a_porta=1 WHERE id=?", (sb,))
    sa = _etapa_tomada(db, "r-a", a, "2026-10-05T01:00:30Z", "android-01")
    veredito = policies.check(a, publicar, step_id=sa, pedido=pedido, bindings={"image_id": "img-1"})
    assert not veredito.allowed and "31.53" in veredito.reason


def test_sem_pedido_ou_com_outra_imagem_nada_muda(tmp_path: Path) -> None:
    _repo, policies, db, a, b = _familia(tmp_path)
    _pedido_aberto(db, a, POST_A)
    publicar = capability_of(IG, "CREATE_POST")
    sem_pedido = policies.check(b, publicar, step_id="r-b:x", bindings={"image_id": "img-1"})
    assert "31.53" not in sem_pedido.reason
    pedido = ContextoDoPedido(raiz="r-b", familia=frozenset({a, b}))
    outra = policies.check(b, publicar, step_id="r-b:x", pedido=pedido, bindings={"image_id": "img-2"})
    assert "31.53" not in outra.reason


def test_persona_fora_da_familia_nao_conta(tmp_path: Path) -> None:
    _repo, policies, db, a, b = _familia(tmp_path)
    _pedido_aberto(db, a, POST_A)
    so_b = ContextoDoPedido(raiz="r-b", familia=frozenset({b}))
    assert "31.53" not in policies.check(b, capability_of(IG, "CREATE_POST"), step_id="r-b:x", pedido=so_b,
                                         bindings={"image_id": "img-1"}).reason


def test_objeto_ambiguo_passa_por_aprovacao_e_nao_recusa(tmp_path: Path) -> None:
    _repo, policies, db, a, b = _familia(tmp_path)
    _pedido_aberto(db, a, {"image_id": "", "content": "Fim de tarde"})
    pedido = ContextoDoPedido(raiz="r-b", familia=frozenset({a, b}))
    veredito = policies.check(b, capability_of(IG, "CREATE_POST"), step_id="r-b:x", pedido=pedido,
                              bindings={"image_id": ""})
    assert "31.53" in veredito.reason and "passa por aprovação" in veredito.reason
    assert veredito.allowed or veredito.retry_at is not None        # nunca recusa; no máximo espera o teto


# ------------------------------------------------------------------------------------------- (2) pelo texto
def test_texto_que_cita_outra_persona_do_pedido_pede_aprovacao_sem_o_arroba_no_motivo(tmp_path: Path) -> None:
    _repo, policies, _db, a, b = _familia(tmp_path)
    comentar = capability_of(IG, "CREATE_COMMENT")
    pedido = ContextoDoPedido(raiz="r-b", familia=frozenset({a, b}))
    texto = "Concordo com o @Tadeu.Quintela4821, que lugar lindo"
    motivo = policies.cita_a_familia(b, comentar, {"content": texto}, pedido)
    assert motivo == MOTIVO_CITA_A_FAMILIA
    assert "@" not in motivo and "tadeu" not in motivo.lower() and texto not in motivo
    # sem o @ também é citar; dentro de outro nome, não
    assert policies.cita_a_familia(b, comentar, {"content": "foto do tadeu.quintela4821 ontem"}, pedido) is not None
    assert policies.cita_a_familia(b, comentar, {"content": "@tadeu.quintela48215 e @xtadeu.quintela4821"}, pedido) is None


def test_o_check_ja_pede_aprovacao_pelo_texto_literal_e_a_previa_ve_o_mesmo(tmp_path: Path) -> None:
    """A prévia do plano só chama o `check`: o texto literal que cita outra conta do pedido tem de aparecer nela com o
    mesmo selo da execução (31.53, revisão; a regra da aprovação no planejamento)."""
    _repo, policies, _db, a, b = _familia(tmp_path)
    comentar = capability_of(IG, "CREATE_COMMENT")
    argumentos = {"post_author": "@fulana.real", "content": "Olha o que o @tadeu.quintela4821 achou"}
    pedido = ContextoDoPedido(raiz="r-b", familia=frozenset({a, b}))
    com = policies.check(b, comentar, counterparty="@fulana.real", pedido=pedido, bindings=argumentos)
    assert MOTIVO_CITA_A_FAMILIA in com.reason and (com.needs_approval or not com.allowed)
    sem = policies.check(b, comentar, counterparty="@fulana.real", bindings=argumentos)
    assert MOTIVO_CITA_A_FAMILIA not in sem.reason


def test_citar_a_si_mesma_quem_esta_fora_ou_sem_pedido_nao_pede(tmp_path: Path) -> None:
    _repo, policies, _db, a, b = _familia(tmp_path)
    comentar = capability_of(IG, "CREATE_COMMENT")
    texto = {"content": "@bia.souza40517 e @tadeu.quintela4821"}
    assert policies.cita_a_familia(b, comentar, texto, None) is None                       # sem pedido
    so_b = ContextoDoPedido(raiz="r-b", familia=frozenset({b}))
    assert policies.cita_a_familia(b, comentar, texto, so_b) is None                       # a própria e quem está fora
    assert policies.cita_a_familia(b, comentar, {"image_id": "x"}, ContextoDoPedido(raiz="r", familia=frozenset({a, b}))
                                   ) is None                                               # sem texto

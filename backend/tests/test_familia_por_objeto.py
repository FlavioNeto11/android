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

from app.models import InteractionStatus
from app.planning.capabilities import capability_of
from app.social.approvals import ApprovalStore
from app.social.policy import MOTIVO_CITA_A_FAMILIA, ContextoDoPedido, PolicyEngine

from .test_capabilities import IG, perfil
from .test_repetido_entre_execucoes import _SERVICOS, _conta, _etapa

POST_A = {"image_id": "img-1", "content": "Fim de tarde"}


def _familia(tmp_path: Path) -> tuple[Any, PolicyEngine, Any, str, str]:
    repo, policies, db, a = _conta(tmp_path)                       # a: lucas.almeida9484, android-01
    b = perfil(_SERVICOS[a], "bia.souza91182", "android-02")
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
    texto = "Concordo com o @Lucas.Almeida9484, que lugar lindo"
    motivo = policies.cita_a_familia(b, comentar, {"content": texto}, pedido)
    assert motivo == MOTIVO_CITA_A_FAMILIA
    assert "@" not in motivo and "lucas" not in motivo.lower() and texto not in motivo
    # sem o @ também é citar; dentro de outro nome, não
    assert policies.cita_a_familia(b, comentar, {"content": "foto do lucas.almeida9484 ontem"}, pedido) is not None
    assert policies.cita_a_familia(b, comentar, {"content": "@lucas.almeida94845 e @xlucas.almeida9484"}, pedido) is None


def test_citar_a_si_mesma_quem_esta_fora_ou_sem_pedido_nao_pede(tmp_path: Path) -> None:
    _repo, policies, _db, a, b = _familia(tmp_path)
    comentar = capability_of(IG, "CREATE_COMMENT")
    texto = {"content": "@bia.souza91182 e @lucas.almeida9484"}
    assert policies.cita_a_familia(b, comentar, texto, None) is None                       # sem pedido
    so_b = ContextoDoPedido(raiz="r-b", familia=frozenset({b}))
    assert policies.cita_a_familia(b, comentar, texto, so_b) is None                       # a própria e quem está fora
    assert policies.cita_a_familia(b, comentar, {"image_id": "x"}, ContextoDoPedido(raiz="r", familia=frozenset({a, b}))
                                   ) is None                                               # sem texto

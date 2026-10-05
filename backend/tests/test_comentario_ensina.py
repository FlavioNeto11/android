"""Item 30.57 (a): a resposta a comentário ensina o perfil pelo mesmo caminho da mensagem direta.

O caminho real: o rascunho da etapa `REPLY_COMMENT` traz em `draft_meta` a fala de quem comentou (`incoming`) e os
candidatos a memória; `SocialService.open_effect` grava a intenção `pending` com eles; a confirmação do efeito
(`confirm_interaction`) chama `MemoryStore.learn_from`, e só então vira memória. Em 03/10/2026 o caminho rodou no
real (`int-fPuCkX3vCt7WnmsL`), mas o elogio não dizia nada de quem comentou e a lista de candidatos veio vazia; a
prova real com fato segue `not_run` (ADR-055 até ~02/11, ou um comentário orgânico de terceiro).

Nível de prova: `simulated` (banco de teste, perfil de teste). Nada real.
"""
from __future__ import annotations

from pathlib import Path

from app.models import InteractionStatus, InteractionType

from .test_social_memory import build, dois_perfis

BRUNO = "@valdir.teixeira6352"
COMENTARIO = "Que foto boa! Também toco guitarra nas horas vagas"
FATO = {"subject": BRUNO, "content": "toca guitarra nas horas vagas", "importance": 0.6, "confidence": 0.7}


def _resposta(svc, pid: str, candidatos: list[dict[str, object]]) -> str:
    return svc.open_effect(pid, capability="REPLY_COMMENT", interaction_type=InteractionType.comment_replied.value,
                           bindings={"username": BRUNO, "content": "Valeu, Bruno!"}, run_id="r-1",
                           step_id="r-1:android-01:v1:reply_1", instance_id="android-01", app_id="instagram",
                           counterparty=BRUNO, draft_meta={"incoming": COMENTARIO, "memory_candidates": candidatos,
                                                           "rationale": "agradecer curto"})


def test_resposta_confirmada_vira_memoria_ligada_a_interacao(tmp_path: Path) -> None:
    svc, _repo, _db = build(tmp_path)
    lucas, _mariana = dois_perfis(svc)
    interacao = _resposta(svc, lucas, [FATO])
    assert svc.list_memories(lucas) == []                            # pendente não ensina nada
    svc.confirm_interaction(lucas, interacao, evidence="resposta visível sob o comentário")
    [memoria] = [m for m in svc.list_memories(lucas) if m.interaction_id == interacao]
    assert memoria.source == "interaction" and memoria.subject == BRUNO and "guitarra" in memoria.content
    gravada = svc.get_interaction(lucas, interacao)
    assert gravada.incoming_content == COMENTARIO and gravada.counterparty == BRUNO


def test_resposta_que_nao_se_confirma_nao_ensina(tmp_path: Path) -> None:
    svc, _repo, _db = build(tmp_path)
    lucas, _mariana = dois_perfis(svc)
    interacao = _resposta(svc, lucas, [FATO])
    svc.close_interaction(lucas, interacao, status=InteractionStatus.uncertain, evidence="sem prova na tela")
    assert svc.list_memories(lucas) == []


def test_elogio_sem_fato_nao_inventa_memoria(tmp_path: Path) -> None:
    """O caso real de 03/10: o comentário era só elogio, a lista veio vazia e nada foi aprendido."""
    svc, _repo, _db = build(tmp_path)
    lucas, _mariana = dois_perfis(svc)
    interacao = _resposta(svc, lucas, [])
    svc.confirm_interaction(lucas, interacao, evidence="resposta visível sob o comentário")
    assert svc.list_memories(lucas) == []
    assert svc.get_interaction(lucas, interacao).incoming_content == COMENTARIO

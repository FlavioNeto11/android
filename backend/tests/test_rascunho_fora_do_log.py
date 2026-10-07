"""31.63: texto de rascunho não vai a log, evento nem motivo de recusa; só tamanho, ids e resultado.

Antes: o evento "texto escrito na voz do perfil — <60 caracteres do rascunho>" (painel, aviso, resumo), o log da
reescrita com o trecho que atribuía fala a terceiro (`%r`) e o motivo da recusa com o trecho entre aspas (vai ao `hint`
da espera). O texto continua onde quem decide o vê: na etapa e no pedido de aprovação.

Nível de prova: `simulated` (harness e serviço social com provedor falso). Nada real.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from .apoio_politica import build, perfil
from .test_porta_do_plano import DM, _gate, _plano, _sem_iniciar
from .apoio_politica import ALVO, _ProvedorQueAtribui

ESCRITO = "Oi! Passando pra desejar uma ótima semana r3163"


async def test_o_evento_do_rascunho_leva_o_tamanho_e_nao_o_texto(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": {"username": DM["username"],
                                                                     "content": "cumprimente a pessoa"}}])

    async def draft_response(*_a: Any, **_k: Any) -> Any:
        return SimpleNamespace(content=ESCRITO, refused=False, refusal_reason=None, rationale="r",
                               memory_candidates=[]), None

    monkeypatch.setattr(state.social, "draft_response", draft_response)
    await _gate(state, "dm")
    mensagens = [r["message"] or "" for r in state.db.query("SELECT message FROM events")]
    assert any(f"texto escrito na voz do perfil ({len(ESCRITO)} caracteres)" in m for m in mensagens)
    assert not [m for m in mensagens if ESCRITO[:20] in m]


async def test_a_reescrita_e_a_recusa_por_fala_de_terceiro_nao_levam_o_trecho(tmp_path: Path,
                                                                              caplog: pytest.LogCaptureFixture) -> None:
    svc, _repo, _pol, _db = build(tmp_path)
    svc.provider = _ProvedorQueAtribui(corrige=False)
    pid = perfil(svc)
    with caplog.at_level(logging.INFO):
        draft, _i = await svc.draft_response(pid, kind="dm_initiate", brief="dizer que o marido dela mandou um oi",
                                             counterparty=ALVO, persist=False)
    assert draft.refused and "terceiro" in (draft.refusal_reason or "")
    assert "marido" not in (draft.refusal_reason or "")
    assert "trecho de" in caplog.text and "marido" not in caplog.text


MARCA = "MARCA-r3163-rascunho"
#: Saída inválida do modelo com o rascunho dentro: `content` deveria ser texto e veio objeto.
INVALIDA = '{"content": {"texto": "' + MARCA + '"}, "rationale": "' + MARCA + '", "refused": "talvez"}'


def test_saida_social_invalida_vira_erro_sem_a_entrada() -> None:
    """31.63 (V1): o `str` da `ValidationError` do pydantic v2 traz `input_value=`; o `AIError` fica com lugar e tipo."""
    from app.planning.parsing import social_from_json
    from app.planning.provider import AIError
    with pytest.raises(AIError) as caught:
        social_from_json(INVALIDA, 500)
    texto = str(caught.value)
    assert MARCA not in texto and "input_value" not in texto
    assert "content: string_type" in texto and "refused: bool_parsing" in texto
    # V1a: levantado FORA do `except`: nem `__cause__` nem `__context__` guardam a `ValidationError` com a entrada.
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_proposta_de_treinamento_invalida_tambem_sai_sem_a_entrada() -> None:
    """31.63 (V3): `proposal_from_json` usava `{exc}` com `from exc` sobre a saída do modelo."""
    from app.planning.provider import AIError
    from app.planning.training import proposal_from_json
    with pytest.raises(AIError) as caught:
        proposal_from_json('{"steps": "' + MARCA + '", "memoria": {"x": "' + MARCA + '"}}', None)  # type: ignore[arg-type]
    assert MARCA not in str(caught.value) and "input_value" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


async def test_saida_social_invalida_nao_leva_o_rascunho_ao_motivo_ao_evento_nem_ao_log(
        harness: Any, monkeypatch: Any, caplog: pytest.LogCaptureFixture) -> None:
    from app.planning.parsing import social_from_json
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": {"username": DM["username"],
                                                                     "content": "cumprimente a pessoa"}}])

    async def generate_social_response(*_a: Any, **_k: Any) -> Any:
        return social_from_json(INVALIDA, 500), None

    monkeypatch.setattr(state.social.provider, "generate_social_response", generate_social_response)
    with caplog.at_level(logging.DEBUG):
        veredito = await _gate(state, "dm")
    assert veredito is not None and not veredito.allowed
    assert "não foi possível escrever o texto desta etapa" in (veredito.reason or "")
    mensagens = [str(r["message"] or "") + str(r["data"] or "") for r in state.db.query("SELECT message, data FROM events")]
    for onde in (veredito.reason or "", veredito.hint or "", caplog.text, *mensagens):
        assert MARCA not in onde, onde


# ----------------------------------------------------------------------- 31.67: a causa não guarda o texto (V1b, V4)
SEGREDO_DO_MODELO = "texto-do-modelo-que-nao-pode-ficar-na-causa"


def _sem_causa(exc: BaseException) -> None:
    assert exc.__cause__ is None and exc.__context__ is None
    assert SEGREDO_DO_MODELO not in str(exc)


@pytest.mark.parametrize("ler", ["loads_json", "persona_draft_from_json", "proposal_from_json"])
def test_json_ilegivel_do_modelo_vira_erro_sem_o_documento_na_causa(ler: str) -> None:
    from app.planning import parsing, provider, training
    from app.planning.provider import AIError
    bruto = '{"conteudo": "' + SEGREDO_DO_MODELO + '" '                     # JSON cortado: JSONDecodeError
    chamada = {"loads_json": lambda: parsing.loads_json(bruto, "Plano"),
               "persona_draft_from_json": lambda: provider.persona_draft_from_json(bruto),
               "proposal_from_json": lambda: training.proposal_from_json(bruto, SimpleNamespace())}[ler]
    with pytest.raises(AIError) as erro:
        chamada()
    _sem_causa(erro.value)


def test_argumentos_invalidos_da_ferramenta_sem_os_argumentos_na_causa() -> None:
    from app.automation.tools import ToolValidationError, validate_call
    with pytest.raises(ToolValidationError) as erro:
        validate_call("type_text", {"text": 123, "extra": SEGREDO_DO_MODELO})
    _sem_causa(erro.value)

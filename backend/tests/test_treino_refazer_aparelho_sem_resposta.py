"""31.110: "Refazer receitas" com o aparelho que parece no ar mas não responde ao ADB (ligando, ou parado sem o estado
mudar). O salvar tinha gravado a receita; o reparo respondia "sem receita, versão do app excedeu 25 s" por etapa. Agora:
uma leitura por app (não uma por etapa), a etapa que já tem receita diz isso, e a que não tem diz que o aparelho não
respondeu.

Nível de prova: `simulated` (aparelho falso do harness, leitura que estoura por `DriverTimeout`; nenhuma IA)."""
from __future__ import annotations

from typing import Any

from app.devices.executor import DriverTimeout
from app.models import InstanceState
from app.training.skills import JA_HAVIA_RECEITA

from .conftest import Harness
from .test_treino_previa_e_refazer_receitas import _por_chave, _proposta, _sessao_mista

PACOTE_DO_APP = "qa-messenger"


def _sem_resposta(st: Any, rt: Any) -> list[int]:
    """O aparelho segue `online`, mas a leitura da versão estoura. Devolve a lista que conta as chamadas."""
    chamadas: list[int] = []

    async def estoura(_rt: Any, _pacote: str) -> str:
        chamadas.append(1)
        raise DriverTimeout("versão do app excedeu 25s")

    rt.app_versions.clear()
    rt.state = InstanceState.online
    st.devices.app_version = estoura                                  # type: ignore[method-assign]
    return chamadas


async def test_refazer_diz_que_a_etapa_ja_tem_receita_e_le_a_versao_uma_vez(harness: Harness) -> None:
    st, rt, _lease, sid = await _sessao_mista(harness)
    await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])           # no ar: grava
    gravadas = st.db.scalar("SELECT COUNT(*) FROM recipes")
    assert gravadas >= 2
    chamadas = _sem_resposta(st, rt)

    refeito = await st.skills.refazer_receitas(sid)
    por = _por_chave(refeito["steps"])
    for chave in ("abrir", "conversa"):                               # as que o salvar gravou
        razao = por[chave]["reason"]
        assert razao.startswith(JA_HAVIA_RECEITA) and "não respondeu" in razao and "sem receita" not in razao, razao
        assert por[chave]["recipe"] is False
    assert refeito["created"] == 0 and st.db.scalar("SELECT COUNT(*) FROM recipes") == gravadas
    assert len(chamadas) == 1                                         # uma leitura por app, não uma por etapa (3 x 25 s)


async def test_etapa_sem_receita_diz_que_o_aparelho_nao_respondeu(harness: Harness) -> None:
    st, rt, _lease, sid = await _sessao_mista(harness)
    rt.state = InstanceState.stopped                                  # o salvar não grava nada (fora do ar, sem leitura)
    rt.app_versions.clear()
    rt.ui_variant = None
    await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert st.db.scalar("SELECT COUNT(*) FROM recipes") == 0
    chamadas = _sem_resposta(st, rt)

    refeito = await st.skills.refazer_receitas(sid)
    razao = _por_chave(refeito["steps"])["abrir"]["reason"]
    assert "não respondeu" in razao and "ligando, parado ou sem ADB" in razao and "Refaça as receitas" in razao, razao
    assert JA_HAVIA_RECEITA not in razao and refeito["created"] == 0
    assert st.db.scalar("SELECT COUNT(*) FROM recipes") == 0 and len(chamadas) == 1

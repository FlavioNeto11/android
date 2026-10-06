"""31.140 (adendo v1.89): `pacotes_aceitos` (31.123) por etapa na prévia, no `save` e na etapa do fluxo no Livro.

Antes, ele só existia no plano salvo e nas etapas da execução: a prévia trazia só a linha de `warnings`, e o Livro não
o levava. A Portal não o achou em nenhuma das 16 sessões do android-04 (06/10).

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

from app.modules.learning.domain import conteudo as dominio

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_treino_partida_f2_e_sequencia import BUSCA, _gravada, _proposta

POS = {"kind": "text_visible", "value": "Nenhum resultado", "description": "x"}


async def test_a_previa_o_save_e_o_livro_trazem_os_pacotes_por_etapa(harness: Harness) -> None:
    st, sid = await _gravada(harness)
    previa = await st.skills.preview(sid, proposal=_proposta(POS), profile_ids=[], group_ids=[])
    assert {s["key"]: s["pacotes_aceitos"] for s in previa["steps"]} == {
        "abrir": [], "abrir_busca": [BUSCA], "buscar": [BUSCA]}
    salvo = await st.skills.save(sid, proposal=_proposta(POS), profile_ids=[], group_ids=[])
    assert {s["key"]: s["pacotes_aceitos"] for s in salvo["steps"]} == {
        "abrir": [], "abrir_busca": [BUSCA], "buscar": [BUSCA]}
    async with _cliente(harness) as c:
        r = await c.get(f"/api/aprendizado/fluxo/{salvo['flow_id']}")
    assert r.status_code == 200, r.text
    assert [(e["chave"], e["pacotes_aceitos"]) for e in r.json()["conteudo"]["etapas"]] == [
        ("abrir", []), ("abrir_busca", [BUSCA]), ("buscar", [BUSCA])]


def test_a_etapa_do_livro_sem_o_campo_ou_quebrada_traz_lista_vazia() -> None:
    assert dominio.etapa_de_fluxo(0, {"key": "a"})["pacotes_aceitos"] == []
    assert dominio.etapa_de_fluxo(0, {"key": "a", "pacotes_aceitos": [BUSCA, 7, None]})["pacotes_aceitos"] == [BUSCA]
    assert dominio.etapa_de_fluxo(0, {"key": "a", "pacotes_aceitos": "x"})["pacotes_aceitos"] == []
    assert dominio.etapa_de_fluxo(0, "quebrada")["pacotes_aceitos"] == []

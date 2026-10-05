"""31.89 F4 e F5: o comando que "parece com o fluxo tal" (só pergunta) e o aviso de colisão ao salvar (só avisa).

O casamento de verdade não mudou (continua exato, e o mais específico vence, F1). Aqui:
- `parecidos`: as palavras fixas do molde aparecem no comando, na mesma ordem, com pequenas variações; nunca lista o
  fluxo que já casa; nunca devolve o nome do fluxo (o resumo do treino pode trazer o valor demonstrado);
- `colisoes`: o molde novo contra os ativos e candidatos, nas duas direções, dizendo quem passa na frente;
- as rotas: `POST /api/flows/similar` e o `warnings` do salvar e da prévia do treino.

Nível de prova: `simulated` (banco migrado pela fábrica da suíte; nenhuma IA)."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db import Database
from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.taskqueue.flows import FlowStore
from app.taskqueue.parecidos import LIMIAR, MINIMO_DE_PALAVRAS, nota_do_molde, palavras, parecidos

from .conftest import Harness
from .fake_skills import banco
from .test_perfil_bloqueado_e_capacidades import _cliente


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco(tmp_path, "parecidos.sqlite3")
    yield d
    d.close()


def _plano(resumo: str = "Curtir") -> Plan:
    passo = PlanStep(key="curtir", title="Curtir", goal="curtir o post", capability="LIKE_POST",
                     bindings={"username": "{username}"},
                     postcondition=Postcondition(kind="text_visible", value="Curtido", description="curtido"))
    return Plan(summary=resumo, app_id="instagram", parameters={"username": "{username}"}, steps=[passo],
                planner=PlannerInfo(provider="anthropic", model="claude", simulated=False))


def _fluxo(db: Database, molde: str, *, resumo: str = "Curtir") -> str:
    return FlowStore(db).learn_from_plan(_plano(resumo), molde, source="training:trn-x")


# ------------------------------------------------------------------ o módulo puro
def test_as_palavras_saem_sem_acento_caixa_pontuacao_e_artigo() -> None:
    assert palavras("Curta o MEU post, por favor!", so_fixo=False) == ["curta", "post", "favor"]
    assert palavras("curtir o post de {username}", so_fixo=True) == ["curtir", "post"]
    assert palavras("Ação rápida", so_fixo=False) == ["acao", "rapida"]


def test_a_nota_e_a_media_e_a_ordem_importa() -> None:
    comando = palavras("curta o post da ana", so_fixo=False)
    assert nota_do_molde(["curtir", "post"], comando) >= LIMIAR                  # "curta" parece "curtir"
    assert nota_do_molde(["post", "curtir"], comando) < LIMIAR                   # fora de ordem, não vale
    assert nota_do_molde(["seguir", "post"], comando) < LIMIAR                   # uma palavra que não está


def test_molde_com_uma_palavra_fixa_nunca_entra() -> None:
    assert MINIMO_DE_PALAVRAS == 2
    assert parecidos("abrir o instagram agora", ["abrir {app}"]) == []


def test_a_lista_vem_do_mais_parecido_e_de_mais_palavras() -> None:
    moldes = ["curtir post {p}", "curtir o ultimo post de {p}", "mandar mensagem a {p}"]
    achados = parecidos("curtir o ultimo post da ana hoje", moldes)
    assert [a.indice for a in achados] == [1, 0]                                 # empate de nota: o de mais palavras


# ------------------------------------------------------------------ FlowStore.parecidos
def test_parecidos_nao_lista_o_que_ja_casa_e_nao_devolve_o_nome(db: Database) -> None:
    _fluxo(db, "curtir o post de {username}", resumo="Curtir o post de @maria_souza")
    flows = FlowStore(db)
    assert flows.parecidos("curtir o post de ana") == []                         # já casa por inteiro
    achados = flows.parecidos("curta o post da ana hoje cedo")
    assert len(achados) == 1
    assert set(achados[0]) == {"ref", "template", "score"}
    assert achados[0]["template"] == "curtir o post de {username}" and achados[0]["ref"].startswith("f-")
    assert "maria" not in repr(achados)


def test_parecidos_respeita_o_escopo_e_ignora_desligado(db: Database) -> None:
    fid = _fluxo(db, "curtir o post de {username}")
    db.execute("INSERT INTO flow_scope(flow_id, profile_id) VALUES (?,?)", (fid, "p-ana"))
    flows = FlowStore(db)
    assert flows.parecidos("curta o post da bia", ["p-bia"]) == []               # fora do escopo
    assert len(flows.parecidos("curta o post da bia", None)) == 1                # prévia sem aparelhos
    db.execute("UPDATE flows SET status='disabled' WHERE id=?", (fid,))
    assert flows.parecidos("curta o post da bia") == []


# ------------------------------------------------------------------ FlowStore.colisoes
def test_colisao_em_duas_direcoes_diz_quem_passa_na_frente(db: Database) -> None:
    _fluxo(db, "curtir {x}")
    flows = FlowStore(db)
    # (a) o novo, preenchido com o exemplo, já é casado pelo genérico; o novo é mais específico e passa na frente
    avisos = flows.colisoes("curtir o post de {p}", {"p": "ana"})
    assert len(avisos) == 1 and "curtir {x}" in avisos[0] and "o novo passa na frente" in avisos[0]
    # (b) o novo genérico engole o específico que já existe, que continua vencendo
    outro = _fluxo(db, "curtir o story de {y}")
    avisos = flows.colisoes("curtir {z}", {"z": "tudo"})
    assert any("curtir o story de {y}" in a and "o que já existe passa na frente" in a for a in avisos)
    assert outro


def test_sem_colisao_com_comando_diferente_nem_com_o_mesmo_comando(db: Database) -> None:
    _fluxo(db, "curtir o post de {username}")
    flows = FlowStore(db)
    assert flows.colisoes("mandar mensagem a {p}", {"p": "ana"}) == []
    assert flows.colisoes("curtir o post de {username}", {"username": "ana"}) == []   # o mesmo é o 409, não colisão


def test_o_aviso_nao_escreve_nada(db: Database) -> None:
    _fluxo(db, "curtir {x}")
    antes = db.scalar("SELECT COUNT(*) FROM flows"), db.scalar("SELECT COUNT(*) FROM flow_scope")
    FlowStore(db).colisoes("curtir o post de {p}", {"p": "ana"})
    assert (db.scalar("SELECT COUNT(*) FROM flows"), db.scalar("SELECT COUNT(*) FROM flow_scope")) == antes


# ------------------------------------------------------------------ as rotas
async def test_a_rota_similar_pergunta_e_nao_cria_execucao(harness: Harness) -> None:
    s = harness.state
    _fluxo(s.db, "curtir o post de {username}")
    execucoes = s.db.scalar("SELECT COUNT(*) FROM runs")
    async with _cliente(harness) as c:
        r = await c.post("/api/flows/similar", json={"command": "curta o post da ana hoje cedo"})
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["matches"] is False and len(corpo["suggestions"]) == 1
        assert corpo["suggestions"][0]["template"] == "curtir o post de {username}"
        r = await c.post("/api/flows/similar", json={"command": "curtir o post de ana"})
        assert r.json() == {"matches": True, "suggestions": []}
        assert (await c.post("/api/flows/similar", json={"command": ""})).status_code == 422
        r = await c.post("/api/flows/match", json={"command": "curta o post da ana hoje cedo"})
        assert r.json() is None                                                   # o match segue exato
    assert s.db.scalar("SELECT COUNT(*) FROM runs") == execucoes


async def test_o_salvar_e_a_previa_do_treino_avisam_a_colisao_sem_recusar(harness: Harness) -> None:
    from .test_treino_previa_e_refazer_receitas import _proposta, _sessao_mista

    st, _rt, _lease, sid = await _sessao_mista(harness)
    generico = _fluxo(st.db, "responda a {x}")
    previa = await st.skills.preview(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    colisoes = [w for w in previa["warnings"] if "colide" in w]
    assert len(colisoes) == 1 and "responda a {x}" in colisoes[0] and "o novo passa na frente" in colisoes[0]
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert [w for w in salvo["warnings"] if "colide" in w] == colisoes            # o mesmo aviso, e o salvar seguiu
    assert salvo["flow_id"] != generico and st.db.scalar("SELECT COUNT(*) FROM flows WHERE status='active'") == 2

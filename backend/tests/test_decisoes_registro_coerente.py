"""Item 28.29 — o registro das decisões automáticas diz a verdade sobre o item e se lê fácil.

Achados das duas revisões independentes do deploy 30 e do passeio da orquestradora pela aba (04/10):
- o Desligar da tela do Aprendizado usa a rota do livro, e o registro seguia "não desfeita", oferecendo o gesto de novo;
- no PostgreSQL o id de sequência fica visível fora de ordem, e o cursor pulava a linha confirmada atrasada;
- o cartão não dizia QUAL item foi decidido, o texto vinha "publicado(a)" e cortado no meio da palavra;
- o resumo do Telegram dizia "Dá para desfazer" também para o que não reabre, e calava as aprovações vencidas junto.

Prova `simulated` (`arquivo::teste`): `AppState` do harness (SQLite) com o livro de aprendizado de verdade, ou o banco
migrado puro para o adaptador e o resumo. Nenhum aparelho, nenhuma IA.
"""
from __future__ import annotations

from pathlib import Path

import pytest


from app.db import Database, dumps
from app.decisoes_inversas import InversaDoAprendizado, SemInversaDaExecucao
from app.modules.decisoes.domain.decisao import Decisao
from app.modules.decisoes.domain.leitura import (efeito_do_aprendizado, efeito_legivel, fatos_legiveis, texto_curto,
                                                 decisao_de_evento)
from app.modules.decisoes.domain.resumo import DecisaoParaResumir, corpo_do_resumo
from app.modules.decisoes.infrastructure.adaptador_sql import AdaptadorDeDecisoes
from app.modules.decisoes.infrastructure.estado_sql import EstadoDasDecisoes
from app.modules.decisoes.infrastructure.registro_sql import RegistroSql
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.vocabulario import LivroKind

from .conftest import Harness, make_config
from .test_decisoes_desfazer import _cliente, _decidir, _publicar_licao, _trilha

VENC = {"regra": "31.43", "horas": 24, "desde": "2026-10-03T10:00:00.000Z"}


# ===================================================================== (a) o registro reflete o estado de agora
async def test_desligado_pela_tela_do_aprendizado_aparece_desfeito_com_quem_quando_e_motivo(harness: Harness) -> None:
    lic = _publicar_licao(harness)
    did = _decidir(harness, "aprendizado", lic, regra="auto:qa_revisar v1", fatos={"kind": "licao", "para": "published"})
    # O dono desliga pela rota do livro (a do Desligar da seção do Aprendizado), não pela do registro.
    harness.state.learning.mudar_estado(LivroKind.LICAO, lic, SkillState.DISABLED, by="panel", reason="não confio")
    n = len(_trilha(harness, lic))
    async with _cliente(harness) as c:
        [item] = (await c.get("/api/decisoes-automaticas")).json()["itens"]
        assert item["desfeita"] is True and item["pode_desfazer"] is False
        assert item["desfeita_por"] == "panel" and item["motivo_do_desfazer"] == "não confio"
        trilha = harness.state.db.one("SELECT decided_at FROM learning_transitions WHERE item_ref=? AND to_state=?"
                                      " ORDER BY id DESC LIMIT 1", (lic, "disabled"))
        assert item["desfeita_em"] == trilha["decided_at"]                  # a hora do gesto, não a da leitura
        # O segundo gesto pela aba do registro não grava outro autor nem outro motivo, e não mexe no livro.
        r = await c.post(f"/api/decisoes-automaticas/{did}/desfazer", json={"confirmar": True, "motivo": "outro"})
        assert r.status_code == 200 and r.json()["desfeita_agora"] is False
        assert r.json()["motivo_do_desfazer"] == "não confio"
        assert (await c.get("/api/decisoes-automaticas?desfeitas=nao")).json()["itens"] == []
    assert len(_trilha(harness, lic)) == n


async def test_o_desfazer_pelo_registro_grava_o_motivo_sem_o_prefixo_da_trilha(harness: Harness) -> None:
    lic = _publicar_licao(harness)
    did = _decidir(harness, "aprendizado", lic, fatos={"kind": "licao", "para": "published"})
    async with _cliente(harness) as c:
        r = await c.post(f"/api/decisoes-automaticas/{did}/desfazer", json={"confirmar": True, "motivo": "errado"})
        assert r.status_code == 200 and r.json()["desfeita_agora"] is True and r.json()["motivo_do_desfazer"] == "errado"
        [item] = (await c.get("/api/decisoes-automaticas")).json()["itens"]
    assert item["motivo_do_desfazer"] == "errado"                         # a releitura não troca pelo texto da trilha


async def test_item_que_mudou_de_estado_nao_oferece_o_botao(harness: Harness) -> None:
    lic = _publicar_licao(harness)
    _decidir(harness, "aprendizado", lic, fatos={"kind": "licao", "para": "published"})
    harness.state.learning.mudar_estado(LivroKind.LICAO, lic, SkillState.DEPRECATED, by="painel:dono", reason="aposentei")
    async with _cliente(harness) as c:
        [item] = (await c.get("/api/decisoes-automaticas")).json()["itens"]
    assert item["pode_desfazer"] is False and item["desfeita"] is False
    assert "já mudou de estado" in item["por_que_nao"] and "deprecated" in item["por_que_nao"]


# ===================================================================== (b) janela de releitura dos cursores
@pytest.fixture
def banco(tmp_path: Path) -> Database:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    return db


def _evento(db: Database, dados: dict, *, ident: int | None = None, run_id: str = "r-x") -> None:
    colunas = "id, " if ident is not None else ""
    valores = (ident,) if ident is not None else ()
    db.execute(f"INSERT INTO events({colunas}ts, kind, level, run_id, objective_id, message, data)"
               f" VALUES ({'?,' if ident is not None else ''}?,?,?,?,?,?,?)",
               (*valores, "2026-10-04T12:00:00.000Z", "run.updated", "info", run_id, None, "m", dumps(dados)))


def test_evento_confirmado_depois_do_cursor_passar_entra_na_volta_seguinte(banco: Database) -> None:
    registro = RegistroSql(banco)
    adaptador = AdaptadorDeDecisoes(banco, registro, EstadoDasDecisoes(banco))
    for i in range(1, 11):
        _evento(banco, {"run": {"id": f"r-{i}"}}, ident=i)                  # nada de vencimento: o cursor anda até 10
    assert adaptador.varrer() == 0 and EstadoDasDecisoes(banco).inteiro("cursor:eventos") == 10
    # O id 5 só fica visível agora (a transação longa do PostgreSQL), com o vencimento.
    banco.execute("DELETE FROM events WHERE id=5")
    _evento(banco, {"run": {"id": "r-5"}, "vencimento": VENC}, ident=5, run_id="r-5")
    assert adaptador.varrer() == 1
    assert [d.item_ref for d in registro.listar()] == ["r-5"]
    assert adaptador.varrer() == 0                                          # a releitura não duplica


def test_transicao_confirmada_depois_do_cursor_tambem_entra(banco: Database) -> None:
    registro = RegistroSql(banco)
    adaptador = AdaptadorDeDecisoes(banco, registro, EstadoDasDecisoes(banco))

    def transicao(ident: int, por: str) -> None:
        banco.execute("INSERT INTO learning_transitions(id, item_ref, item_kind, scope_key, from_state, to_state, reason,"
                      " decided_by, decided_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (ident, f"licao-{ident}", "licao", "", "validated", "published",
                       "auto:qa_revisar v1 — classe B", por, "2026-10-04T12:30:00.000Z"))

    transicao(1, "plataforma")
    transicao(3, "plataforma")
    assert adaptador.varrer() == 2
    transicao(2, "plataforma")                                              # confirmou depois do 3
    assert adaptador.varrer() == 1
    assert sorted(d.item_ref for d in registro.listar()) == ["licao-1", "licao-2", "licao-3"]


# ===================================================================== textos legíveis
@pytest.mark.parametrize("kind,para,confirmacao,frase", [
    ("receita", "published", False, "Receita publicada pela plataforma"),
    ("fluxo", "published", True, "Fluxo confirmado pela plataforma"),
    ("fluxo", "disabled", False, "Fluxo desligado pela plataforma"),
    ("licao", "candidate", False, "Lição devolvida à prova pela plataforma"),
])
def test_efeito_com_genero_e_sem_parenteses(kind: str, para: str, confirmacao: bool, frase: str) -> None:
    efeito = efeito_do_aprendizado(kind, para, confirmacao=confirmacao)
    assert efeito.startswith(frase) and "(a)" not in efeito


def test_linha_antiga_e_lida_legivel() -> None:
    # As 18 gravadas no deploy 30 têm "publicado(a)" e o texto cortado no meio da palavra.
    fatos = {"texto": "classe B; app com.pocqa.messenger (qa); 2 a favor, 0 contra; 0 falhas de reprodu",
             "kind": "receita", "para": "published", "de": "validated"}
    assert efeito_legivel("aprendizado", "Receita publicado(a) pela plataforma, sem esperar o dono.", fatos) == \
        "Receita publicada pela plataforma, sem esperar o dono."
    assert efeito_legivel("aprendizado", "Fluxo confirmado(a) pela plataforma, sem esperar o dono.",
                          {"kind": "fluxo", "para": "published"}).startswith("Fluxo confirmado ")
    texto = fatos_legiveis(fatos)["texto"]
    assert isinstance(texto, str) and texto.endswith("0 falhas de…") and "reprodu" not in texto
    assert efeito_legivel("objetivo", "O objetivo … foi encerrado.", {}) == "O objetivo … foi encerrado."


def test_texto_novo_corta_na_palavra() -> None:
    longo = "classe B; app com.pocqa.messenger (qa); 10 a favor, 0 contra; 0 falhas de reprodução; saúde saudavel"
    curto = texto_curto(longo)
    assert len(curto) <= 80 and curto.endswith("…") and longo.startswith(curto[:-1])
    assert texto_curto("cabe inteiro") == "cabe inteiro"


def test_objetivo_vencido_guarda_a_execucao_nos_fatos() -> None:
    d = decisao_de_evento("objective.updated", {"objective": {"id": "o-1"}, "vencimento": VENC},
                          ts="2026-10-04T12:00:00Z", run_id="r-9")
    assert d is not None and d.fatos["run_id"] == "r-9"


# ===================================================================== qual item: nome e execução
def _decisao(fila: str, item_ref: str, fatos: dict) -> Decisao:
    return Decisao(1, fila, item_ref, "o", "r", "e", fatos, "2026-10-04T12:00:00Z")


async def test_a_lista_diz_o_nome_do_item_do_aprendizado(harness: Harness) -> None:
    lic = _publicar_licao(harness)
    _decidir(harness, "aprendizado", lic, fatos={"kind": "licao", "para": "published"})
    async with _cliente(harness) as c:
        [item] = (await c.get("/api/decisoes-automaticas")).json()["itens"]
    titulo = harness.state.learning.entrada(LivroKind.LICAO, lic).title
    assert titulo and item["item_nome"] == " ".join(titulo.split()) and item["run_id"] is None


def test_pergunta_e_objetivo_dizem_a_execucao_mesmo_na_linha_antiga() -> None:
    pergunta = SemInversaDaExecucao("não reabre")
    assert pergunta.descrever(_decisao("pergunta", "r-1", {})).run_id == "r-1"   # type: ignore[union-attr]
    objetivo = SemInversaDaExecucao("não reabre", run_do_objetivo=lambda oid: {"o-7": "r-7"}.get(oid))
    assert objetivo.descrever(_decisao("objetivo", "o-7", {})).run_id == "r-7"   # type: ignore[union-attr]
    assert objetivo.descrever(_decisao("objetivo", "o-8", {"run_id": "r-8"})).run_id == "r-8"  # type: ignore[union-attr]


def test_item_do_aprendizado_que_sumiu_fica_sem_nome() -> None:
    class Livro:
        def entrada(self, kind: object, ref: str) -> object:
            from app.modules.learning.domain.ciclo import NaoEncontrado
            raise NaoEncontrado("não há")

    inversa = InversaDoAprendizado(Livro())  # type: ignore[arg-type]
    assert inversa.descrever(_decisao("aprendizado", "receita:1", {"kind": "receita", "para": "published"})) is None
    assert "não existe mais" in (inversa.por_que_nao(_decisao("aprendizado", "receita:1",
                                                              {"kind": "receita", "para": "published"})) or "")


# ===================================================================== o resumo só promete o que se desfaz
def test_resumo_so_oferece_desfazer_ao_que_tem_volta_e_conta_as_aprovacoes() -> None:
    venc = [DecisaoParaResumir(i, "objetivo", "31.43") for i in range(3)]
    corpo = corpo_do_resumo(venc, 7, aprovacoes_encerradas=2) or ""
    assert "Dá para desfazer" not in corpo and "desfazem" not in corpo
    assert "incluem 2 aprovações pendentes, que venceram junto" in corpo and "não reabre" in corpo
    misto = corpo_do_resumo([*venc, DecisaoParaResumir(9, "aprendizado", "auto:qa_revisar v1")], 7) or ""
    assert "desligando o item pelo painel, em até 7 dias" in misto and "não reabre" in misto
    so_aprendizado = corpo_do_resumo([DecisaoParaResumir(9, "aprendizado", "auto:qa_revisar v1")], 1) or ""
    assert "não reabre" not in so_aprendizado and "em até 1 dia." in so_aprendizado
    assert "aprovaç" not in (corpo_do_resumo(venc, 7) or "")

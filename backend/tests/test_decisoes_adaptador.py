"""Item 28.25 — o adaptador: lê os eventos de vencimento (31.43) e as transições `decided_by='plataforma'` do aprendizado.

Prova `simulated` (`arquivo::teste`): os produtores AINDA NÃO existem na main, então os testes gravam, à mão, os eventos e
as linhas de `learning_transitions` no formato combinado com as frentes. Quando eles chegarem, o contrato está aqui.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database, dumps
from app.modules.decisoes.domain.leitura import (decisao_de_evento, decisao_de_transicao, fatos_do_texto,
                                                 motivo_da_plataforma)
from app.modules.decisoes.infrastructure.adaptador_sql import LOTE, AdaptadorDeDecisoes
from app.modules.decisoes.infrastructure.estado_sql import EstadoDasDecisoes
from app.modules.decisoes.infrastructure.registro_sql import RegistroSql

from .conftest import make_config

#: O motivo real que o Aprendizado grava (confirmado pela frente): sem arroba, com espaço.
MOTIVO_REAL = ("auto:qa_revisar v1 — classe B; app com.pocqa.messenger (qa); 10 a favor, 0 contra; 0 falhas de "
               "reprodução; saúde saudavel; parecer observar (lr-b12b089e7784b5c7)")
MOTIVO_COM_PREFIXO = "confirmado que fica: " + MOTIVO_REAL


class Cena:
    def __init__(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path)
        cfg.ensure_dirs()
        self.db = Database(cfg.db_dsn)
        self.db.migrate()
        self.registro = RegistroSql(self.db)
        self.estado = EstadoDasDecisoes(self.db)
        self.adaptador = AdaptadorDeDecisoes(self.db, self.registro, self.estado)

    def evento(self, kind: str, dados: dict, *, run_id: str | None = None, objective_id: str | None = None,
               ts: str = "2026-10-04T12:00:00.000Z") -> None:
        self.db.execute("INSERT INTO events(ts, kind, level, run_id, objective_id, message, data) VALUES (?,?,?,?,?,?,?)",
                        (ts, kind, "info", run_id, objective_id, "m", dumps(dados)))

    def transicao(self, item_ref: str, reason: str, *, por: str = "plataforma", para: str = "published",
                  de: str = "validated", kind: str = "licao") -> None:
        self.db.execute(
            "INSERT INTO learning_transitions(item_ref, item_kind, scope_key, from_state, to_state, reason, decided_by,"
            " decided_at) VALUES (?,?,?,?,?,?,?,?)",
            (item_ref, kind, "", de, para, reason, por, "2026-10-04T12:30:00.000Z"))


@pytest.fixture
def cena(tmp_path: Path) -> Cena:
    return Cena(tmp_path)


VENC = {"regra": "31.43-pergunta-24h", "horas": 24, "desde": "2026-10-03T10:00:00.000Z"}


# ===================================================================== (a) eventos de vencimento
def test_evento_de_vencimento_da_pergunta_vira_decisao(cena: Cena) -> None:
    cena.evento("run.updated", {"run": {"id": "r-1", "status": "cancelled"}, "motivo": "vencido_sem_resposta",
                                "vencimento": VENC}, run_id="r-1")
    assert cena.adaptador.varrer() == 1
    (d,) = cena.registro.listar()
    assert (d.fila, d.item_ref, d.regra) == ("pergunta", "r-1", "31.43-pergunta-24h")
    assert d.origem_ref == "run:r-1:31.43-pergunta-24h"
    assert d.fatos == {"horas": 24, "desde": "2026-10-03T10:00:00.000Z", "estado_final": "cancelled"}
    assert d.efeito == "A pergunta sem resposta havia 24 h foi encerrada."
    assert d.decidida_em == "2026-10-04T12:00:00.000Z"


def test_evento_de_vencimento_do_objetivo_vira_decisao_de_objetivo(cena: Cena) -> None:
    cena.evento("objective.updated", {"objective": {"id": "o-9", "status": "failed"},
                                      "vencimento": {"regra": "31.43-objetivo-24h", "horas": 24}}, objective_id="o-9")
    assert cena.adaptador.varrer() == 1
    (d,) = cena.registro.listar()
    assert (d.fila, d.item_ref, d.origem_ref) == ("objetivo", "o-9", "objetivo:o-9:31.43-objetivo-24h")
    assert d.fatos["estado_final"] == "failed"


def test_o_mesmo_fato_em_dois_eventos_e_o_mesmo_evento_relido_dao_uma_linha(cena: Cena) -> None:
    dados = {"run": {"id": "r-1"}, "vencimento": VENC}
    cena.evento("run.updated", dados, run_id="r-1")
    cena.evento("run.updated", dados, run_id="r-1", ts="2026-10-04T12:00:05.000Z")      # o fato reemitido
    assert cena.adaptador.varrer() == 1
    cena.estado.gravar_inteiro("cursor:eventos", 0)                                      # cursor perdido: relê tudo
    assert cena.adaptador.varrer() == 0
    assert len(cena.registro.listar()) == 1


@pytest.mark.parametrize("kind,dados", [
    ("run.updated", {"run": {"id": "r-1", "status": "needs_input"}}),                                  # evento comum
    ("run.updated", {"run": {"id": "r-1"}, "expirada": {"motivo": "sem_resposta", "horas": 24}}),      # forma do 29.50
    ("run.updated", {"run": {"id": "r-1"}, "vencimento": {"horas": 24}}),                              # sem regra
    ("run.updated", {"run": {"id": "r-1"}, "motivo": "outro", "vencimento": VENC}),                    # motivo errado
    ("step.updated", {"vencimento": VENC}),                                                           # outro tipo
])
def test_so_o_vencimento_da_politica_entra(cena: Cena, kind: str, dados: dict) -> None:
    cena.evento(kind, dados, run_id="r-1")
    assert cena.adaptador.varrer() == 0
    assert cena.registro.listar() == []


def test_cursor_dos_eventos_anda_mesmo_sem_casamento_e_pega_o_que_vem_depois(cena: Cena) -> None:
    cena.evento("run.updated", {"run": {"id": "r-0"}}, run_id="r-0")
    assert cena.adaptador.varrer() == 0
    assert cena.estado.inteiro("cursor:eventos") == cena.db.scalar("SELECT MAX(id) FROM events")
    cena.evento("run.updated", {"run": {"id": "r-1"}, "vencimento": VENC}, run_id="r-1")
    assert cena.adaptador.varrer() == 1


def test_varredura_em_lotes_nao_perde_nenhum(cena: Cena) -> None:
    for i in range(LOTE + 30):
        cena.evento("run.updated", {"run": {"id": f"r-{i}"}, "vencimento": VENC}, run_id=f"r-{i}")
    assert cena.adaptador.varrer() == LOTE + 30
    assert len(cena.registro.listar(limite=500)) == LOTE + 30


# ===================================================================== (b) transições do aprendizado
@pytest.mark.parametrize("motivo,confirmacao", [(MOTIVO_REAL, False), (MOTIVO_COM_PREFIXO, True)])
def test_transicao_da_plataforma_entra_com_e_sem_o_prefixo_da_confirmacao(cena: Cena, motivo: str,
                                                                            confirmacao: bool) -> None:
    cena.transicao("licao-1", motivo)
    assert cena.adaptador.varrer() == 1
    (d,) = cena.registro.listar()
    assert d.fila == "aprendizado" and d.item_ref == "licao-1" and d.regra == "auto:qa_revisar v1"
    assert d.fatos["kind"] == "licao" and d.fatos["para"] == "published" and d.fatos["de"] == "validated"
    # 28.29: a confirmação fica nos fatos e o efeito sai legível, com o gênero do tipo ("Lição confirmada"), sem "(a)".
    assert (d.fatos.get("confirmacao") is True) is confirmacao and "(a)" not in d.efeito
    assert d.efeito.startswith("Lição confirmada" if confirmacao else "Lição publicada")
    assert "lr-b12b" not in d.efeito
    assert cena.adaptador.varrer() == 0                                  # reler não duplica


def test_contraprova_so_a_plataforma_entra(cena: Cena) -> None:
    cena.transicao("licao-1", MOTIVO_REAL, por="sistema")                # o D1 (repetição) não é a política
    cena.transicao("licao-2", MOTIVO_REAL, por="painel:dono")            # uma pessoa
    assert cena.adaptador.varrer() == 0
    assert cena.registro.listar() == []


@pytest.mark.parametrize("motivo", [
    "auto:qa_revisar@v1 — classe B",              # o formato antigo, com arroba, NÃO casa
    "autopublicacao_b: repetiu",                  # outra marca do livro
    "o dono mandou: auto:qa_revisar v1 — x",      # `auto:` depois de outro prefixo qualquer
    "auto:QA v1 — x",                              # a regra é minúscula
    "auto:qa_revisar v1x",                         # versão colada em letra
    "plataforma decidiu",
])
def test_motivo_que_nao_e_da_politica_nao_entra_mesmo_com_decided_by_plataforma(cena: Cena, motivo: str) -> None:
    cena.transicao("licao-1", motivo)
    assert cena.adaptador.varrer() == 0
    assert motivo_da_plataforma(motivo) is None


def test_motivo_sem_fatos_e_aceito_e_a_versao_vem_separada() -> None:
    assert motivo_da_plataforma("auto:qa_para_aprovar v2") == ("qa_para_aprovar", "2", "", False)
    assert motivo_da_plataforma(MOTIVO_COM_PREFIXO)[0:2] == ("qa_revisar", "1")  # type: ignore[index]
    assert motivo_da_plataforma(MOTIVO_COM_PREFIXO)[3] is True                   # type: ignore[index]


def test_fatos_chave_valor_e_texto_livre_curto() -> None:
    assert fatos_do_texto("usos=5, taxa=0.8; nome=abc") == {"usos": 5, "taxa": 0.8, "nome": "abc"}
    livre = fatos_do_texto("classe B; app com.pocqa.messenger (qa); " + "x" * 300)
    assert set(livre) == {"texto"} and len(str(livre["texto"])) == 80


def test_o_redator_age_sobre_o_texto_dos_fatos_antes_de_gravar() -> None:
    d = decisao_de_transicao(transicao_id=3, item_ref="licao-1", item_kind="licao", de="validated", para="published",
                             reason="auto:qa_revisar v1 — contato fulano@x.com", decided_by="plataforma",
                             decided_at="2026-10-04T12:00:00.000Z", redigir=lambda t: t.replace("fulano@x.com", "[oculto]"))
    assert d is not None and "fulano" not in str(d.fatos) and "[oculto]" in str(d.fatos)


def test_evento_sem_id_de_run_nao_vira_decisao() -> None:
    assert decisao_de_evento("run.updated", {"vencimento": VENC}, ts="2026-10-04T12:00:00.000Z") is None

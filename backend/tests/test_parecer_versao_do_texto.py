"""30.76: o parecer do curador grava a versão do texto que a IA leu (migração 117, `learning_reviews.instrucao_versao`).

Da leitura do 30.73: a `template_versao` da revisão é a FORMA do dossiê (`dossie-v1`) e não muda de sentido, para não
mexer no hash nem na elegibilidade. A versão da instrução (`VERSAO_DO_TEMPLATE`) ia só no código. Agora quem mandou o
texto à IA (o adaptador do hub) a diz na resposta, e ela vai à coluna nova. Sem texto mandado a uma IA (curador
simulado, recusas, linhas antigas), fica NULL.

Nível de prova: `simulated` (banco de teste e hub falso; nenhuma IA). PostgreSQL pela fábrica quando
`TEST_DATABASE_URL` existe.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.db import MIGRATIONS_DIR as MIGRACOES, Database
from app.modules.learning.application.curador import TEMPLATE_VERSAO
from app.modules.learning.application.ports import NovaRevisao
from app.modules.learning.domain.vocabulario import SourceKind
from app.planning.curador import VERSAO_DO_TEMPLATE

from .test_curador_do_hub import HubFalso, _ligar, laco  # noqa: F401 - `laco` é fixture
from .test_db import _banco
from .test_learning_curador import Mundo, db  # noqa: F401 - `db` é a fixture do banco do curador

NOME = "117_versao_do_texto_do_parecer"
TS = "2026-10-05T12:00:00Z"


def test_migracao_117_coluna_nulavel_e_impressao(tmp_path: Path) -> None:
    banco = _banco(tmp_path)
    try:
        banco.migrate()
        assert "instrucao_versao" in set(banco.columns("learning_reviews"))
        for id_, versao in (("com", "curador-v2"), ("sem", None)):
            banco.execute(
                "INSERT INTO learning_reviews(id, created_at, item_ref, item_kind, scope_app, gatilho, dossie_hash,"
                " template_id, template_versao, simulated, usd, validade, instrucao_versao)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (id_, TS, f"li-{id_}", "licao", "com.x", "a_revisar", f"h-{id_}", "curador", "dossie-v1", 0, 0.0,
                 "ok", versao))
        linhas = {str(r["id"]): r["instrucao_versao"]
                  for r in banco.query("SELECT id, instrucao_versao FROM learning_reviews")}
        assert linhas == {"com": "curador-v2", "sem": None}
        linha = banco.one("SELECT checksum FROM schema_migrations WHERE version=?", (NOME,))
        assert linha is not None                                   # a impressão (sha256 do texto renderizado) ficou
        texto = (MIGRACOES / f"{NOME}.sql").read_text(encoding="utf-8")
        assert linha["checksum"] == banco._impressao(banco.render(texto))   # noqa: SLF001
        assert len(linha["checksum"]) == 64
    finally:
        banco.close()


def test_o_parecer_pelo_hub_grava_a_versao_do_texto_e_a_forma_do_dossie_nao_muda(
        db: Database, laco: Any, tmp_path: Path) -> None:  # noqa: F811
    m = Mundo(db)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    _ligar(m, HubFalso(), laco, tmp_path)
    assert m.volta().revisadas == (ref,)
    [linha] = m.revisoes()
    assert linha["instrucao_versao"] == VERSAO_DO_TEMPLATE == "curador-v2"
    assert linha["template_versao"] == TEMPLATE_VERSAO == "dossie-v1"          # a forma do dossiê segue a mesma


def test_sem_texto_mandado_a_uma_ia_a_versao_fica_nula(db: Database) -> None:  # noqa: F811
    """O curador do `Mundo` é o falso da porta (não manda texto a uma IA): a linha nasce sem versão."""
    m = Mundo(db)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    assert m.volta().revisadas == (ref,)
    [linha] = m.revisoes()
    assert linha["instrucao_versao"] is None


def test_as_recusas_e_o_rotulo_nao_dizem_versao() -> None:
    """As recusas (custo, triagem) e o rótulo de intenção montam a `NovaRevisao` sem a versão: o padrão é nulo."""
    nova = NovaRevisao(item_ref="x", item_kind="licao", scope_app="", gatilho="a_revisar", dossie_hash="h", dossie={},
                       template_id="curador", template_versao=TEMPLATE_VERSAO, provedor="", modelo="", simulated=False,
                       validade="recusada:custo", saida=None, classe_de_risco=None, politica=None)
    assert nova.instrucao_versao is None

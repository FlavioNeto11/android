"""Atribuição do aprendizado por ETAPA (e não por execução): uma execução com dois apps (Instagram + Outlook) conta
cada etapa no app dela, e a tela da falha gravada na tentativa chega à chave do backlog.

Medido no central em 02/10 (só leitura): `steps.app_id` é NULL em 2471 de 2493 etapas, e isso é o desenho — o plano só
grava o app da etapa quando ele DIFERE do app do plano (`planning/parsing.py::app_da_etapa`), e `runs.app_ids` leva o
app do plano primeiro. Nas 7 execuções com dois apps, as etapas do segundo app têm `steps.app_id` gravado e as NULL são
as do app do plano: a atribuição por etapa já bate. Estes testes a TRAVAM, para que uma mudança futura em
`app_da_etapa`, em `recalcular_diario` ou no relatório de falhas não volte a atribuir a execução inteira a um app.

Nível de prova: `simulated` (banco migrado de teste, sem aparelho e sem provedor).
"""
from __future__ import annotations

from datetime import timedelta

from app.db import Database
from app.modules.learning.domain.falhas import FailureKind
from app.modules.learning.infrastructure.relatorio_sql import FontesDeFalhaSql

from .test_learning_repositorio import AGORA, PRECOS, iso, repo, semear_execucao
from .test_learning_repositorio import db  # noqa: F401  (fixture)

INSTAGRAM = "com.instagram.android"
OUTLOOK = "com.microsoft.office.outlook"
POS = "Pós-condição não comprovada: x"


def _execucao_com_dois_apps(db: Database, *, tela_do_outlook: str | None = "caixa_de_entrada") -> None:
    semear_execucao(db, "r-dois", dias_atras=2, etapas=[
        ("abrir_perfil", "OPEN_PROFILE", "failed", [("failed", POS)], 0),
        ("ler_caixa", "READ_MAIL", "failed", [("failed", POS)], 0)])
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES (?,?,?,0)", ("outlook", "Outlook", OUTLOOK))
    db.execute("UPDATE runs SET app_ids=? WHERE id=?", ('["instagram","outlook"]', "r-dois"))
    # A etapa do Outlook carrega o app; a do Instagram (o app do plano) fica NULL, como o planejador a grava.
    db.execute("UPDATE steps SET app_id=? WHERE key=?", ("outlook", "ler_caixa"))
    for chave, tela in (("abrir_perfil", None), ("ler_caixa", tela_do_outlook)):
        db.execute("UPDATE attempts SET failure_kind=?, failure_screen=? WHERE step_id LIKE ?",
                   (FailureKind.POS_CONDICAO_NAO_COMPROVADA.value, tela, f"%:{chave}"))


def test_a_regua_diaria_conta_cada_etapa_no_app_dela(db: Database) -> None:  # noqa: F811
    _execucao_com_dois_apps(db)
    dia = iso(2)[:10]
    repo(db).recalcular_diario(dia, (AGORA + timedelta(days=1)).strftime("%Y-%m-%d"))
    linhas = {(x["app_package"], x["capability"]): x["attempts"] for x in db.query("SELECT * FROM learning_daily")}
    assert linhas == {(INSTAGRAM, "OPEN_PROFILE"): 1, (OUTLOOK, "READ_MAIL"): 1}


def test_o_relatorio_de_falhas_agrupa_pelo_app_da_etapa_e_leva_a_tela(db: Database) -> None:  # noqa: F811
    _execucao_com_dois_apps(db)
    ocorrencias = FontesDeFalhaSql(db, precos=lambda: PRECOS).ocorrencias(
        iso(3), to_iso_futuro(), simulados=False, retroativo=False, corte_de_custo=iso(30))
    chaves = {(o.chave.app, o.chave.capability, o.chave.tipo, o.chave.tela) for o in ocorrencias}
    assert chaves == {(INSTAGRAM, "OPEN_PROFILE", "pos_condicao_nao_comprovada", ""),
                      (OUTLOOK, "READ_MAIL", "pos_condicao_nao_comprovada", "caixa_de_entrada")}


def test_a_execucao_so_do_segundo_app_nao_vira_do_primeiro(db: Database) -> None:  # noqa: F811
    """O app do plano é o Outlook (`app_ids` = [outlook]): as etapas NULL são do Outlook, nunca do aparelho."""
    _execucao_com_dois_apps(db)
    db.execute("UPDATE runs SET app_ids=? WHERE id=?", ('["outlook"]', "r-dois"))
    db.execute("UPDATE steps SET app_id=NULL")
    repo(db).recalcular_diario(iso(2)[:10], (AGORA + timedelta(days=1)).strftime("%Y-%m-%d"))
    assert {x["app_package"] for x in db.query("SELECT app_package FROM learning_daily")} == {OUTLOOK}


def to_iso_futuro() -> str:
    return iso(-1)

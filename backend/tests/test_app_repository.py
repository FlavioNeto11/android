"""`AppRepository`: o dono único da escrita na tabela `apps` (fase A da evolução arquitetural).

O que cada teste protege, em uma frase:

* o id nasce legível do nome, sem acento e sem colidir, e a lista sai na ordem que o painel sempre mostrou;
* o que sai do repositório é `AppRow` — um dicionário com as colunas da tabela, nunca a linha crua do banco;
* a edição só toca o que veio, só em coluna editável, e `known_selectors` vira JSON (vazio vira `NULL`);
* o cadastro automático (versão importada de pacote novo) respeita o pacote já cadastrado;
* o gerador de id legível serve também a outra tabela (os perfis de proxy), agora que mora no kernel.

O banco vem de `make_config(...).db_dsn`: roda em SQLite e, com `TEST_DATABASE_URL`, no PostgreSQL.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import Database
from app.modules.applications.infrastructure.app_repository import AppRepository
from app.util import novo_id_de_app

from .conftest import make_config


@pytest.fixture
def banco(tmp_path: Path) -> Database:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    return db


@pytest.fixture
def apps(banco: Database) -> AppRepository:
    return AppRepository(banco)


def test_id_legivel_sem_acento_e_sem_colidir(apps: AppRepository) -> None:
    primeiro = apps.criar(name="Configurações", package="com.android.settings")
    segundo = apps.criar(name="Configurações", package="com.android.settings.outro")
    assert (primeiro, segundo) == ("configuracoes", "configuracoes-2")
    # Nome sem nenhum caractere aproveitável ainda vira um id válido.
    assert apps.criar(name="!!!", package="com.exemplo.vazio") == "app"


def test_criar_grava_o_que_recebe_e_obter_devolve_approw(apps: AppRepository) -> None:
    app_id = apps.criar(name="Outlook", package="com.microsoft.office.outlook", activity=".Main",
                        known_selectors={"caixa": "id/inbox"}, category="email")
    linha = apps.obter(app_id)
    assert linha is not None
    assert type(linha) is dict                       # TypedDict: um dicionário comum, não a linha do driver
    seletores = linha.pop("known_selectors")         # type: ignore[misc]
    assert json.loads(seletores or "null") == {"caixa": "id/inbox"}      # a coluna guarda JSON
    assert linha == {"id": "outlook", "name": "Outlook", "package": "com.microsoft.office.outlook",
                     "activity": ".Main", "apk_path": None, "nav_hints": None, "builtin": 0, "category": "email"}
    assert apps.obter("nao-existe") is None


def test_id_explicito_e_builtin_vem_do_config(apps: AppRepository) -> None:
    """A subida pelo `config.yaml` traz id e `builtin`; nada é normalizado (o vazio fica como veio)."""
    assert apps.criar(app_id="qa-messenger", name="QA Messenger", package="com.poc.qa", activity="",
                      builtin=True) == "qa-messenger"
    linha = apps.obter("qa-messenger")
    assert linha is not None and linha["builtin"] == 1 and linha["activity"] == ""


def test_listar_poe_os_embutidos_primeiro_e_depois_por_nome(apps: AppRepository) -> None:
    apps.criar(name="Zeta", package="com.z")
    apps.criar(name="Alfa", package="com.a")
    apps.criar(app_id="qa", name="QA", package="com.qa", builtin=True)
    assert [a["id"] for a in apps.listar()] == ["qa", "alfa", "zeta"]


def test_id_do_pacote_com_e_sem_excecao(apps: AppRepository) -> None:
    app_id = apps.criar(name="Instagram", package="com.instagram.android")
    assert apps.id_do_pacote("com.instagram.android") == app_id
    assert apps.id_do_pacote("com.instagram.android", exceto=app_id) is None
    assert apps.id_do_pacote("com.outro") is None


def test_atualizar_so_toca_o_que_veio(apps: AppRepository) -> None:
    app_id = apps.criar(name="Outlook", package="com.microsoft.office.outlook", nav_hints="abra a caixa",
                        known_selectors={"a": "b"}, category="email")
    apps.atualizar(app_id, {"name": "Outlook Web", "known_selectors": {}})
    linha = apps.obter(app_id)
    assert linha is not None
    assert (linha["name"], linha["known_selectors"]) == ("Outlook Web", None)      # dicionário vazio vira NULL
    assert (linha["nav_hints"], linha["category"]) == ("abra a caixa", "email")     # o resto ficou como estava
    apps.atualizar(app_id, {"known_selectors": {"x": "y"}, "category": None})
    linha = apps.obter(app_id)
    assert linha is not None and json.loads(linha["known_selectors"] or "null") == {"x": "y"}
    assert linha["category"] is None
    apps.atualizar(app_id, {})                                                    # nada a mudar: nada muda
    assert apps.obter(app_id) == linha


def test_atualizar_recusa_coluna_que_nao_se_edita(apps: AppRepository, banco: Database) -> None:
    """O nome da coluna entra no texto do `UPDATE`: chave fora da lista é recusada antes de chegar ao banco."""
    app_id = apps.criar(name="QA", package="com.qa")
    with pytest.raises(ValueError, match="builtin"):
        apps.atualizar(app_id, {"builtin": 1, "name": "outro"})  # type: ignore[typeddict-unknown-key]
    with pytest.raises(ValueError):
        apps.atualizar(app_id, {"name=name, builtin": "1"})  # type: ignore[typeddict-unknown-key]
    assert banco.one("SELECT name, builtin FROM apps WHERE id=?", (app_id,)) == {"name": "QA", "builtin": 0}


def test_remover(apps: AppRepository) -> None:
    app_id = apps.criar(name="Temporário", package="com.tmp")
    apps.remover(app_id)
    assert apps.obter(app_id) is None and apps.listar() == []


def test_cadastro_automatico_respeita_o_pacote_ja_cadastrado(apps: AppRepository) -> None:
    """Decisão do dono (26/09): a versão importada de um pacote que ninguém cadastrou cadastra o app sozinha."""
    criado = apps.cadastrar_se_novo("com.microsoft.office.outlook", "  Microsoft Outlook  ")
    assert criado == "microsoft-outlook"
    linha = apps.obter(criado)
    assert linha is not None
    assert (linha["name"], linha["builtin"], linha["category"]) == ("Microsoft Outlook", 0, None)
    # O mesmo pacote de novo (outra versão, outro rótulo): nada é criado.
    assert apps.cadastrar_se_novo("com.microsoft.office.outlook", "Outlook") is None
    assert len(apps.listar()) == 1
    # Sem rótulo no APK, o nome é o pacote; rótulo enorme é cortado em 80.
    nomes = {}
    for pacote, rotulo in (("com.sem.rotulo", None), ("com.longo", "x" * 200)):
        novo = apps.cadastrar_se_novo(pacote, rotulo)
        assert novo is not None
        nomes[pacote] = (apps.obter(novo) or {"name": ""})["name"]
    assert nomes == {"com.sem.rotulo": "com.sem.rotulo", "com.longo": "x" * 80}


def test_gerador_de_id_serve_a_outra_tabela(banco: Database) -> None:
    """Os perfis de proxy usam o mesmo gerador, noutra tabela — por isso ele mora em `util`, e não na vitrine."""
    banco.execute("INSERT INTO proxy_profiles(id, name, host, port, created_at) VALUES (?,?,?,?,?)",
                  ("proxy-casa", "Casa", "10.0.0.1", 3128, "2026-09-27T00:00:00.000Z"))
    assert novo_id_de_app(banco, "proxy Casa", tabela="proxy_profiles") == "proxy-casa-2"
    assert novo_id_de_app(banco, "proxy Casa") == "proxy-casa"                    # em `apps` o id está livre

"""30.32: a classe de risco do FLUXO é a da etapa mais restritiva.

O fluxo não tem UMA capability: o dossiê o classificava sem fatos do catálogo, e todo fluxo com efeito caía em
`commit_sem_fatos_da_etapa` (B), inclusive comentar, mandar mensagem e seguir, que o catálogo do Instagram diz C. Agora
cada etapa leva os fatos do catálogo do app DELA, e as razões são a união:

- os fatos de uma etapa só acrescentam razão; a classe nunca desce;
- a etapa de efeito sem fatos mantém a lacuna de sempre (B, no mínimo);
- a etapa de efeito que o catálogo diz sem efeito é a divergência (C);
- sem etapas (receita, lição, tela), os dados e a classe são os de antes, e o hash do dossiê não muda.

Nível de prova: `simulated` (domínio puro, o YAML real do catálogo e o banco migrado com catálogo falso).
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.db import Database
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.politica_de_risco import (ClasseDeRisco, EtapaDeRisco, FatosDeRisco, FatosDoCatalogo,
                                                           Razao, classificar)
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure.dossies import DossiesSql
from app.modules.learning.infrastructure.eventos import RiscoDoRegistro
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.montagem import GuardaDoFluxo
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository

from .fake_skills import ValidadorFalso
from .fake_skills import banco as banco_migrado
from .fake_skills import fluxo

A, B, C = ClasseDeRisco.A, ClasseDeRisco.B, ClasseDeRisco.C
INSTAGRAM = "com.instagram.android"
OUTLOOK = "com.microsoft.office.outlook"
LER = FatosDoCatalogo()
CURTIR = FatosDoCatalogo(risco="medium", efeito_externo=True)
COMENTAR = FatosDoCatalogo(risco="high", politica="approval_required", precisa_rascunho=True, efeito_externo=True)
E = EtapaDeRisco


def _fluxo(*etapas: EtapaDeRisco, efeito: bool = True):  # noqa: ANN202 - Classificacao
    return classificar(FatosDeRisco(side_effect=efeito, human_origin=False, tem_catalogo=True, etapas=etapas))


# ------------------------------------------------------------------ domínio
def test_sem_etapas_o_fluxo_com_efeito_fica_na_lacuna_de_sempre_e_os_dados_nao_mudam() -> None:
    c = classificar(FatosDeRisco(side_effect=True, human_origin=False, tem_catalogo=True))
    assert (c.classe, c.razoes) == (B, (Razao.COMMIT_SEM_FATOS_DA_ETAPA,))
    dados = FatosDeRisco(side_effect=True, human_origin=False, tem_catalogo=True, catalogo=CURTIR).como_dados()
    assert set(dados) == {"side_effect", "human_origin", "tem_catalogo", "sessao_ou_autenticacao", "catalogo"}
    # Etapas sem nenhum fato do catálogo: a classe é a de antes, e o dossiê (o hash) também.
    sem_fatos = FatosDeRisco(side_effect=True, human_origin=False, tem_catalogo=True,
                             etapas=(E("X", False, None), E("Y", True, None)))
    assert "etapas" not in sem_fatos.como_dados() and classificar(sem_fatos) == c


def test_a_etapa_mais_restritiva_da_a_classe_do_fluxo() -> None:
    comentar = _fluxo(E("OPEN_PROFILE", False, LER), E("OPEN_POST", False, LER), E("CREATE_COMMENT", True, COMENTAR))
    assert comentar.classe is C
    assert {Razao.RISCO_ALTO, Razao.TEXTO_PARA_OUTRA_PESSOA, Razao.EFEITO_DECLARADO} <= set(comentar.razoes)
    assert Razao.COMMIT_SEM_FATOS_DA_ETAPA not in comentar.razoes
    curtir = _fluxo(E("OPEN_POST", False, LER), E("LIKE_POST", True, CURTIR))
    assert (curtir.classe, curtir.razoes) == (B, (Razao.RISCO_MEDIO, Razao.EFEITO_DECLARADO))


def test_os_fatos_de_uma_etapa_so_sobem_a_classe() -> None:
    base = _fluxo(E("CREATE_COMMENT", True, COMENTAR))
    # Leitura fora do catálogo não muda nada.
    assert _fluxo(E("DESCONHECIDA", False, None), E("CREATE_COMMENT", True, COMENTAR)) == base
    # Efeito sem fatos: a lacuna volta, e a C das outras etapas fica.
    junto = _fluxo(E("CREATE_COMMENT", True, COMENTAR), E("DESCONHECIDA", True, None))
    assert junto.classe is C and Razao.COMMIT_SEM_FATOS_DA_ETAPA in junto.razoes
    so = _fluxo(E("OPEN_PROFILE", False, LER), E("DESCONHECIDA", True, None))
    assert (so.classe, so.razoes) == (B, (Razao.COMMIT_SEM_FATOS_DA_ETAPA,))
    # A etapa de efeito que o catálogo diz sem efeito: a divergência é C.
    assert _fluxo(E("OPEN_PROFILE", True, LER)).razoes == (Razao.COMMIT_FORA_DO_CATALOGO,)
    assert _fluxo(E("OPEN_PROFILE", True, LER)).classe is C
    # Sem efeito no fluxo, etapas de leitura não fazem razão: continua A.
    assert _fluxo(E("OPEN_PROFILE", False, LER), efeito=False).classe is A


def test_as_etapas_entram_nos_dados_do_dossie() -> None:
    f = FatosDeRisco(side_effect=True, human_origin=False, tem_catalogo=True,
                     etapas=(E("OPEN_PROFILE", False, LER), E("DESCONHECIDA", True, None)))
    assert f.como_dados()["etapas"] == [
        {"capability": "OPEN_PROFILE", "efeito": False,
         "catalogo": {"risco": "low", "politica": "autonomous", "precisa_rascunho": False, "efeito_externo": False,
                      "familia_do_efeito": None, "interacao": None}},
        {"capability": "DESCONHECIDA", "efeito": True, "catalogo": None}]


#: As etapas (capability, efeito) dos 9 fluxos do Instagram que a 1ª volta do curador de 03/10 viu em B.
NOVE_FLUXOS_REAIS = [
    [("OPEN_PROFILE", False), ("OPEN_POST", False), ("OPEN_COMMENTS", False), ("CREATE_COMMENT", True)],
    [("OPEN_PROFILE", False), ("OPEN_POST", False), ("LIKE_POST", True), ("CREATE_COMMENT", True)],
    [("OPEN_PROFILE", False), ("OPEN_POST", False), ("CREATE_COMMENT", True)],
    [("OPEN_PROFILE", False), ("OPEN_POST", False), ("OPEN_COMMENTS", False), ("REPLY_COMMENT", True)],
    [("OPEN_PROFILE", False), ("OPEN_THREAD", False), ("SEND_MESSAGE", True)],
    [("OPEN_PROFILE", False), ("FOLLOW", True), ("OPEN_INBOX", False), ("OPEN_THREAD", False), ("SEND_MESSAGE", True)],
    [("OPEN_PROFILE", False), ("OPEN_POST", False), ("LIKE_POST", True), ("OPEN_COMMENTS", False),
     ("CREATE_COMMENT", True)],
    [("OPEN_PROFILE", False), ("FOLLOW", True)],
]


@pytest.mark.parametrize("etapas", NOVE_FLUXOS_REAIS)
def test_os_fluxos_reais_do_instagram_vao_a_c_pelo_catalogo_do_repositorio(etapas: list[tuple[str, bool]]) -> None:
    risco = RiscoDoRegistro()
    c = _fluxo(*(E(cap, efeito, risco.da_capability(INSTAGRAM, cap)) for cap, efeito in etapas))
    assert c.classe is C and Razao.RISCO_ALTO in c.razoes and Razao.COMMIT_SEM_FATOS_DA_ETAPA not in c.razoes


# ------------------------------------------------------------------ o dossiê
class CatalogoPorApp:
    """O catálogo falso que confere o PACOTE pedido (a etapa noutro app vem pelo `app_id` do plano)."""

    def __init__(self, fatos: dict[tuple[str, str], FatosDoCatalogo]) -> None:
        self._fatos = fatos
        self.pedidos: list[tuple[str, str]] = []

    def tem_catalogo(self, app: str) -> bool:
        return app in {a for a, _ in self._fatos}

    def da_capability(self, app: str, capability: str) -> FatosDoCatalogo | None:
        self.pedidos.append((app, capability))
        return self._fatos.get((app, capability))


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "classe_do_fluxo.sqlite3")
    d.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('outlook','Outlook',?,0)", (OUTLOOK,))
    yield d
    d.close()


PLANO = {"summary": "Ler e comentar", "app_id": "instagram", "parameters": {},
         "steps": [{"key": "abrir", "capability": "OPEN_PROFILE", "goal": "abrir"},
                   {"key": "ler", "app_id": "outlook", "capability": "READ_EMAIL", "goal": "ler o e-mail"},
                   {"key": "comentar", "capability": "CREATE_COMMENT", "side_effect": True, "goal": "comentar"}],
         "planner": {"provider": "fluxo", "model": "m", "simulated": True}}


def test_o_dossie_do_fluxo_le_cada_etapa_no_catalogo_do_app_dela(db: Database) -> None:
    fluxo(db, "comentar-1", "Comente no post de {perfil}", plano=PLANO, status="candidate")
    catalogo = CatalogoPorApp({(INSTAGRAM, "OPEN_PROFILE"): LER, (INSTAGRAM, "CREATE_COMMENT"): COMENTAR,
                               (OUTLOOK, "READ_EMAIL"): LER})
    repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, SqlSkillRepository(db, ValidadorFalso())),
                                 precos=dict)
    servico = LearningService(repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                              relogio=lambda: datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
                              retencao_de_logs_dias=lambda: 14, catalogo_de_risco=catalogo)
    d = DossiesSql(db, servico, repo, catalogo).dossie(servico.entrada(LivroKind.FLUXO, "comentar-1"))
    assert d is not None
    assert d.risco.classe is C and Razao.COMMIT_SEM_FATOS_DA_ETAPA not in d.risco.razoes
    pedidos = set(catalogo.pedidos)
    assert {(INSTAGRAM, "OPEN_PROFILE"), (OUTLOOK, "READ_EMAIL"), (INSTAGRAM, "CREATE_COMMENT")} <= pedidos
    assert [(x.capability, x.efeito) for x in d.fatos_de_risco.etapas] == [
        ("OPEN_PROFILE", False), ("READ_EMAIL", False), ("CREATE_COMMENT", True)]
    assert "etapas" in d.fatos_de_risco.como_dados()

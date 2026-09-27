"""`app/models.py` fatiado por contexto (design §16, fase K): os corpos de requisição moraram para a apresentação de
cada contexto, e `app.models` os reexporta.

O que se prova aqui:

- o reexport entrega o MESMO objeto, não uma cópia (`is`): uma classe redefinida faria `isinstance` e o esquema do
  OpenAPI divergirem em silêncio, como já se registrou no shim do protocolo do worker;
- a classe mora de fato no módulo novo (`__module__`), e não o contrário (o módulo novo reexportando `app.models`);
- nenhum corpo NOVO nasce em `app.models`: a lista dos que ficaram é exata — sair dela exige tirar daqui também,
  entrar nela reprova.
"""
from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

import app.models as models

APP = Path(__file__).resolve().parents[1] / "app"

MOVIDOS: dict[str, tuple[str, ...]] = {
    "app.modules.fleet.presentation.schemas": (
        "AdoptDeviceBody", "WorkerEnrollBody", "WorkerMaintenanceBody", "WorkerRemoveBody", "InstancePatch",
        "CommandResolveBody", "CommandCancelBody", "ReleaseBody", "ServerLimitsPatch"),
    "app.modules.identity.presentation.schemas": (
        "ProfileCreate", "CredentialUpdate", "PersonaPreviewBody", "MemoryCreate", "ProfilePolicyPatch",
        "ProfileAccountCreate", "ProfileAccountPatch", "PolicyGroupCreate", "PolicyGroupPatch"),
    "app.modules.execution.presentation.schemas": (
        "ApprovalDecision", "ApprovalDecisionItem", "ApprovalBatchBody"),
    "app.modules.applications.presentation.schemas": (
        "ReleaseImportBody", "SignatureApprovalBody", "AppInstallBody", "AppVerifyBody", "StoreBody",
        "ReleaseLifecycleBody", "AppInput", "AppPatch"),
}
#: Vocabulário que só os corpos movidos usavam e foi junto (reexportado do mesmo jeito).
MOVIDOS_JUNTO: dict[str, tuple[str, ...]] = {
    "app.modules.identity.presentation.schemas": ("PolicyName",),
    "app.modules.applications.presentation.schemas": ("APP_CATEGORIES", "AppCategory"),
}
#: Corpos que continuam em `app.models`, cada um pelo motivo do docstring de lá (tipo de domínio que ainda mora lá,
#: importador fora da API, ou contexto sem casa na apresentação). Lista EXATA.
FICARAM = frozenset({"ProfilePatch", "PersonaCreate", "PersonaPatch", "InstanceActionBody", "BulkBody",
                     "ManualInput", "RunCreate", "DistributeSpec", "ResolveBody", "TrainingStartBody",
                     "TrainingSaveBody", "LoginBody"})
_CORPO = re.compile(r"(Body|Create|Patch|Input|Request|Spec)$")


def _casos() -> list[tuple[str, str]]:
    return [(mod, nome) for tabela in (MOVIDOS, MOVIDOS_JUNTO) for mod, nomes in tabela.items() for nome in nomes]


@pytest.mark.parametrize(("modulo", "nome"), _casos())
def test_reexport_entrega_o_mesmo_objeto(modulo: str, nome: str) -> None:
    novo = importlib.import_module(modulo)
    assert getattr(models, nome) is getattr(novo, nome)


@pytest.mark.parametrize(("modulo", "nome"), [(m, n) for m, ns in MOVIDOS.items() for n in ns])
def test_a_classe_mora_no_modulo_novo(modulo: str, nome: str) -> None:
    assert getattr(models, nome).__module__ == modulo


def test_foram_41_corpos_29_moveram() -> None:
    """A conta do relatório de classificação (41 corpos de requisição): 29 moveram, 12 ficaram com motivo."""
    movidos = {n for ns in MOVIDOS.values() for n in ns}
    assert len(movidos) == 29 and len(FICARAM) == 12
    assert not movidos & FICARAM


def test_nenhum_corpo_novo_nasce_em_models() -> None:
    arvore = ast.parse((APP / "models.py").read_text(encoding="utf-8"))
    corpos = {n.name for n in arvore.body if isinstance(n, ast.ClassDef) and _CORPO.search(n.name)}
    corpos |= {n.name for n in arvore.body if isinstance(n, ast.ClassDef) and n.name.startswith("Approval")}
    assert corpos - FICARAM == set(), "corpo de requisição novo em app.models: nasce na apresentação do contexto"
    assert FICARAM - corpos == set(), "corpo saiu de app.models: tire-o de FICARAM e ponha em MOVIDOS"


@pytest.mark.parametrize("modulo", sorted(MOVIDOS))
def test_apresentacao_nao_importa_models(modulo: str) -> None:
    """O caminho de volta (`schemas` → `app.models`) fecharia um ciclo de import de topo com o reexport."""
    caminho = APP.parent / (modulo.replace(".", "/") + ".py")
    for n in ast.walk(ast.parse(caminho.read_text(encoding="utf-8"))):
        if isinstance(n, ast.ImportFrom):
            assert not (n.module or "").endswith("models"), f"{modulo} importa {n.module}"

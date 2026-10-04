"""Fase C da evolução arquitetural (docs/design/evolucao-arquitetural.md §5, §7, §15.1, §18): o catálogo legado lido
como `CapabilityDefinition`, e o primeiro `CapabilityProvider` real, que verifica pela prova local de sempre.

Tudo puro: sem banco, sem aparelho, sem IA.
"""
from __future__ import annotations

import ast
import inspect
import typing
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest

from app.automation.hierarchy import parse_hierarchy
from app.modules.capabilities.domain.definition import (CapabilityDefinition, CapabilityRef, CollectOutput,
                                                        PostconditionContract, PostconditionKind)
from app.modules.capabilities.domain.strategy import StrategyContext, StrategyKind
from app.modules.capabilities.domain.verification import Observation, StepView, VerifyOutcome
from app.modules.capabilities.infrastructure.catalog_provider import CatalogCapabilityProvider
from app.modules.capabilities.infrastructure.catalog_registry import (CAMPOS, SEM_CONSUMIDOR,
                                                                      CatalogCapabilityRegistry, definicao)
from app.modules.execution.application.ports import CapabilityProvider
from app.planning.capabilities import Capability, catalogo_do_pacote

APP = Path(__file__).resolve().parents[1] / "app"
PACKAGE = "com.instagram.android"
#: O catálogo do Instagram é dado (ADR-052, fatia 2): as ações vêm do arquivo, todas e na ordem declarada.
_CATALOGO = catalogo_do_pacote(PACKAGE)
assert _CATALOGO is not None
CAPABILITIES = _CATALOGO.capabilities
APPS = {"instagram": PACKAGE, "qa": "com.example.qa"}


def _registro() -> CatalogCapabilityRegistry:
    return CatalogCapabilityRegistry(APPS.get)


def _no_destino(definicao_: CapabilityDefinition, caminho: str) -> object:
    valor: object = definicao_
    for parte in caminho.split("."):
        valor = getattr(valor, parte)
    return valor


# ---------------------------------------------------------------- mapeamento 1:1
def test_todo_campo_do_catalogo_tem_destino_na_definicao() -> None:
    """Campo novo em `Capability` sem destino aqui reprova: o contrato não pode crescer por fora do domínio."""
    campos = {f.name for f in fields(Capability)}
    assert campos - set(CAMPOS) == set(), f"campo do catálogo sem mapeamento em CAMPOS: {campos - set(CAMPOS)}"
    assert set(CAMPOS) - campos == set(), f"CAMPOS mapeia campo que não existe mais: {set(CAMPOS) - campos}"
    assert len(set(CAMPOS.values())) == len(CAMPOS), "dois campos do catálogo no mesmo destino"


def test_as_26_capabilities_do_instagram_chegam_inteiras() -> None:
    assert len(CAPABILITIES) == 26      # 23 + as três do 29.30 (publicar no próprio feed)
    for cap in CAPABILITIES:
        d = definicao(cap, PACKAGE)
        assert d.ref == CapabilityRef(app=PACKAGE, key=cap.key, contract_version=1)
        for campo, destino in CAMPOS.items():
            origem = getattr(cap, campo)
            esperado = tuple(origem) if isinstance(origem, (list, tuple)) else origem
            assert _no_destino(d, destino) == esperado, f"{cap.key}.{campo} → {destino}"


def test_estrategias_por_capability_e_o_que_se_oferece() -> None:
    reg = _registro()
    internas = {c.key for c in CAPABILITIES if c.internal}
    assert internas == {"AUTHENTICATE_INSTAGRAM", "VERIFY_ACCOUNT", "PUT_MEDIA_IN_GALLERY"}
    for cap in CAPABILITIES:
        d = reg.definition("instagram", cap.key)
        assert d is not None
        if cap.internal:
            assert d.execution.strategies == (StrategyKind.deterministic,)
        else:
            assert d.execution.strategies == (StrategyKind.recipe, StrategyKind.ai_actor, StrategyKind.human)
    assert {d.key for d in reg.offered("instagram")} == {c.key for c in CAPABILITIES} - internas
    # a etapa que escreve exige a intenção ou o texto, a mesma regra do `build_step`
    envio = reg.definition("instagram", "SEND_MESSAGE")
    assert envio is not None and envio.parameters.one_of == ("content_brief", "content")
    assert envio.requirements.session_provider == "instagram" and envio.requirements.needs_profile


def test_campos_sem_consumidor_estao_sinalizados_e_continuam_sem_consumidor() -> None:
    """`reconciliation` e `collect` não têm quem os leia (§2.6). Se alguém passar a ler, a sinalização é que está
    errada: tire de SEM_CONSUMIDOR no mesmo commit — como a exceção órfã de `test_cobertura_de_rotas.py`."""
    assert set(SEM_CONSUMIDOR) <= set(CAMPOS)
    fora = (APP / "planning" / "capabilities.py", APP / "planning" / "catalog", APP / "modules" / "capabilities")
    leitores = []
    for arq in sorted(APP.rglob("*.py")):
        if any(arq == f or f in arq.parents for f in fora):
            continue
        arvore = ast.parse(arq.read_text(encoding="utf-8"))
        # `diagnostics.collect` é função de um módulo importado, não o campo de uma capability
        importados = {(a.asname or a.name).split(".")[0] for n in ast.walk(arvore)
                      if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        for n in ast.walk(arvore):
            if (isinstance(n, ast.Attribute) and n.attr in SEM_CONSUMIDOR
                    and not (isinstance(n.value, ast.Name) and n.value.id in importados)):
                leitores.append(f"{arq.relative_to(APP)}:{n.lineno} lê .{n.attr}")
    assert not leitores, f"campo marcado sem consumidor ganhou leitor: {leitores}"


def test_definicao_e_imutavel_e_coleta_anda_com_a_pos_condicao() -> None:
    d = definicao(next(c for c in CAPABILITIES if c.key == "READ_MESSAGES"), PACKAGE)
    assert d.output.collects and d.postcondition.kind is PostconditionKind.items_collected
    with pytest.raises(FrozenInstanceError):
        d.title = "outro"  # type: ignore[misc]
    with pytest.raises(ValueError, match="andar juntos"):
        replace(d, output=CollectOutput(collects=False))
    with pytest.raises(ValueError, match="andar juntos"):
        replace(d, postcondition=PostconditionContract(PostconditionKind.model_judged, "x", "y"))


def test_registro_por_id_de_app_e_por_referencia() -> None:
    reg = _registro()
    assert reg.app_known("instagram") and reg.has_catalog("instagram")
    assert reg.app_known("qa") and not reg.has_catalog("qa")          # app conhecido, caminho livre
    assert not reg.app_known("whatsapp") and reg.definition("whatsapp", "OPEN_THREAD") is None
    assert reg.definition("instagram", "NAO_EXISTE") is None and reg.offered("qa") == []
    assert reg.definition("instagram", "open_thread") == reg.definition("instagram", "OPEN_THREAD")
    ref = CapabilityRef(PACKAGE, "OPEN_THREAD")
    assert reg.by_ref(ref) == reg.definition("instagram", "OPEN_THREAD")
    assert reg.by_ref(CapabilityRef(PACKAGE, "OPEN_THREAD", contract_version=2)) is None


# ---------------------------------------------------------------- provider
def _tela(*nos: tuple[str, str, str]) -> Observation:
    corpo = "".join(f'<node class="{c}" text="{t}" resource-id="{PACKAGE}:id/{r}" '
                    f'bounds="[0,{i * 60}][700,{i * 60 + 50}]"/>' for i, (c, t, r) in enumerate(nos))
    return Observation(screen=parse_hierarchy(f"<hierarchy>{corpo}</hierarchy>"), package=PACKAGE)


def _etapa(chave: str, **bindings: str) -> StepView:
    return StepView(node_id=chave.lower(), capability=CapabilityRef(PACKAGE, chave),
                    bindings=tuple(sorted(bindings.items())))


CONVERSA = _tela(("android.widget.TextView", "ana", "header_title"),
                 ("android.widget.EditText", "Message…", "row_thread_composer_edittext"))
CAIXA = _tela(("android.widget.EditText", "Search", "search_edit_text"),
              ("android.widget.TextView", "ana", "row_inbox_username"))


async def test_verify_prova_a_conversa_aberta_pela_arvore() -> None:
    prov = CatalogCapabilityProvider(_registro())
    r = await prov.verify(_etapa("OPEN_THREAD", username="@ana"), CONVERSA)
    assert r.outcome is VerifyOutcome.proved and "árvore local" in r.detail


async def test_verify_negativa_da_prova_local_nunca_reprova() -> None:
    """A caixa de entrada com a linha do usuário (G0) não prova a conversa — e também não a desmente."""
    prov = CatalogCapabilityProvider(_registro())
    assert (await prov.verify(_etapa("OPEN_THREAD", username="@ana"), CAIXA)).outcome is VerifyOutcome.unknown
    assert (await prov.verify(_etapa("OPEN_THREAD", username="@bia"), CONVERSA)).outcome is VerifyOutcome.unknown
    # variável sem valor: não há o que provar
    assert (await prov.verify(_etapa("OPEN_THREAD"), CONVERSA)).outcome is VerifyOutcome.unknown


async def test_verify_marca_de_falha_desmente_o_envio() -> None:
    prov = CatalogCapabilityProvider(_registro())
    tela = _tela(("android.widget.TextView", "oi", "direct_text_message_text_view"),
                 ("android.widget.TextView", "Not delivered", "message_status"),
                 ("android.widget.EditText", "", "row_thread_composer_edittext"))
    r = await prov.verify(_etapa("SEND_MESSAGE", username="@ana", content="oi"), tela)
    assert r.outcome is VerifyOutcome.not_proved and "Not delivered" in r.detail


async def test_verify_nao_afirma_fora_do_alcance_da_prova_local() -> None:
    prov = CatalogCapabilityProvider(_registro())
    exigente = replace(_etapa("OPEN_THREAD", username="@ana"), required_delivery_level="delivered")
    casos = [
        (exigente, CONVERSA),                                                    # nível de entrega: só o modelo
        (_etapa("OPEN_FEED"), CONVERSA),                                          # pós-condição determinística
        (_etapa("OPEN_INBOX"), CONVERSA),                                         # sem prova local declarada
        (StepView(node_id="livre", capability=None), CONVERSA),                   # etapa sem capability
        (_etapa("OPEN_THREAD", username="@ana"), Observation(screen="<xml/>")),  # tela que não é árvore
        (_etapa("OPEN_THREAD", username="@ana"), Observation(screen=parse_hierarchy("<hierarchy/>"))),  # vazia
        (StepView(node_id="x", capability=CapabilityRef(PACKAGE, "NAO_EXISTE")), CONVERSA),
    ]
    for etapa, obs in casos:
        assert (await prov.verify(etapa, obs)).outcome is VerifyOutcome.unknown, etapa


async def test_verify_prova_por_faixa_usa_as_guardas_da_etapa() -> None:
    xml = ('<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][720,1280]">'
           '<node class="android.widget.TextView" text="bia said oi" bounds="[40,400][500,460]"/>'
           '<node class="android.widget.ImageView" content-desc="Like" bounds="[600,400][660,460]"/>'
           '<node class="android.widget.TextView" text="ana said oi" bounds="[40,600][500,660]"/>'
           '<node class="android.widget.ImageView" content-desc="Liked" bounds="[600,600][660,660]"/>'
           '</node></hierarchy>')
    obs = Observation(screen=parse_hierarchy(xml))
    prov = CatalogCapabilityProvider(_registro())
    da_ana = replace(_etapa("LIKE_COMMENT", username="@ana"), band_guard=("@ana",))
    da_bia = replace(_etapa("LIKE_COMMENT", username="@bia"), band_guard=("@bia",))
    assert (await prov.verify(da_ana, obs)).outcome is VerifyOutcome.proved
    assert (await prov.verify(da_bia, obs)).outcome is VerifyOutcome.unknown


async def test_operacoes_que_tocariam_o_aparelho_falham_alto() -> None:
    prov = CatalogCapabilityProvider(_registro())
    ctx = StrategyContext(run_id="r", objective_id="o", instance_id="android-01", attempt_id=None,
                          app_package=PACKAGE)
    etapa = _etapa("OPEN_INBOX")
    with pytest.raises(NotImplementedError, match="executor"):
        await prov.observe(ctx)
    with pytest.raises(NotImplementedError, match="estratégias"):
        await prov.execute(etapa, ctx)
    with pytest.raises(NotImplementedError, match="hospedeiro"):
        await prov.reconcile(etapa, ctx)
    assert prov.supports(CapabilityRef(PACKAGE, "OPEN_INBOX"))
    assert not prov.supports(CapabilityRef(PACKAGE, "NAO_EXISTE"))
    assert not prov.supports(CapabilityRef("com.example.qa", "OPEN_INBOX"))


def test_provider_cumpre_a_porta_por_estrutura() -> None:
    """A infraestrutura de capabilities não importa a porta (o DAG proíbe), então o mypy não confere a ligação.
    Confere-se aqui, membro a membro: mesmos parâmetros, mesmos tipos, mesma natureza (async ou não)."""
    membros = [n for n, v in vars(CapabilityProvider).items() if inspect.isfunction(v) and not n.startswith("_")]
    assert sorted(membros) == ["execute", "observe", "reconcile", "supports", "verify"]
    for nome in membros:
        porta, impl = getattr(CapabilityProvider, nome), getattr(CatalogCapabilityProvider, nome)
        assert inspect.iscoroutinefunction(porta) == inspect.iscoroutinefunction(impl), nome
        assert list(inspect.signature(porta).parameters) == list(inspect.signature(impl).parameters), nome
        assert typing.get_type_hints(porta) == typing.get_type_hints(impl), nome

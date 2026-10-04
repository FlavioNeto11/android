"""Item 24.1 (ADR-058, decisão 1): o planejador com os catálogos de vários apps.

O que se prova:
- os apps citados no comando saem pelo nome do cadastro e pelo rótulo do manifesto, fora das aspas, na ordem;
- citar outro app ou pedir um site não derruba mais o catálogo: `RunService._catalogos` escolhe livre, por catálogo
  (como antes) ou ENTRE APPS, e no entre apps vão os catálogos de todos os candidatos que têm um;
- `catalog_plan_from_json` monta cada etapa pelo `app_id` dela: ação do catálogo daquele app, ou etapa livre num app
  sem catálogo; etapa livre num app com catálogo, app desconhecido e ação que não existe viram pergunta e zeram as
  etapas; a herança de argumento não atravessa apps;
- `required_apps` sempre preenchido: plano livre, por catálogo, entre apps e simulado — os apps em que as etapas rodam;
- o prompt entre apps traz os dois tipos de app e as regras de sempre, e os provedores Anthropic e OpenAI (sem rede)
  mandam o sistema e o esquema entre apps;
- a execução (harness, porta 5640, provedor simulado) planeja entre apps e grava o app de cada etapa.

Nível de prova: `simulated` (provedor simulado ou cliente falso; nenhuma chamada de IA). O esquema estrito novo
aceito pela API real: `not_run`.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
import pytest

from app.config import ModelCaps, ProviderCfg, RoleCfg
from app.models import Plan
from app.planning import prompts
from app.planning.anthropic_provider import AnthropicProvider
from app.planning.apps_do_comando import apps_citados, pede_site
from app.planning.capabilities import Capability, CapabilityCatalog, load_catalog
from app.planning.openai_provider import OpenAICompatProvider
from app.planning.parsing import apps_do_plano, catalog_plan_from_json, plan_from_json
from app.planning.provider import AIError, AppContext, PlanRequest
from app.planning.simulated_provider import SimulatedProvider
from app.taskqueue.repository import _dependencias_das_saidas
from app.taskqueue.saidas import referencias, resolver
from app.taskqueue.service import RunService

from .conftest import Harness, make_config

IG = "com.instagram.android"
OUTLOOK = "com.microsoft.office.outlook"
# A mecânica do plano entre apps (etapa livre, segunda trava, ordem dos apps) precisa de um app SEM catálogo. O Outlook
# ganhou catálogo só de leitura (12.3, ADR-065) e é coberto pelos testes `*_outlook_real_*` / "do catálogo" no fim do
# arquivo; para a mecânica, este e-mail de pacote qualquer segue com o MESMO id e rótulo ("no Outlook" nos comandos).
EMAIL_LIVRE = "com.exemplo.email.livre"
QA = "com.pocqa.messenger"
LEITOR = "com.exemplo.leitor"

INSTAGRAM = AppContext("instagram", "Instagram", IG, None, "dicas do IG", {"feed": "id=feed"})
# O cadastro diz "Microsoft Outlook"; quem escreve o comando diz "no Outlook" (o rótulo do manifesto).
OUTLOOK_APP = AppContext("outlook", "Microsoft Outlook", OUTLOOK, None, "caixa de entrada na aba Email", None)
OUTLOOK_LIVRE = AppContext("outlook", "Microsoft Outlook", EMAIL_LIVRE, None, "caixa de entrada na aba Email", None)
QA_APP = AppContext("qa-messenger", "QA Messenger", QA, ".MainActivity", None, None)
CHROME = AppContext("chrome", "Chrome", "com.android.chrome", None, None, None)
LEITOR_APP = AppContext("leitor", "Leitor", LEITOR, None, None, None)
TODOS = [INSTAGRAM, OUTLOOK_APP, QA_APP, CHROME]
NO_IG = [{"instance_id": "android-01", "account_label": "eu.teste", "app_id": "instagram"}]
NO_QA = [{"instance_id": "android-01", "account_label": "qa-user-01", "app_id": "qa-messenger"}]


def _catalogo_ig() -> CapabilityCatalog:
    catalogo = load_catalog(IG)
    assert catalogo is not None
    return catalogo


def _catalogo_outlook() -> CapabilityCatalog:
    catalogo = load_catalog(OUTLOOK)
    assert catalogo is not None
    return catalogo


def _catalogo_leitor() -> CapabilityCatalog:
    """Um segundo app COM catálogo, que declara um argumento de mesmo nome que o do Instagram (e uma ação de mesmo
    nome): é onde uma herança global levaria a legenda de um app para o outro."""
    post = dict(post_kind="model_judged", post_value="aberto", post_description="aberto")
    return CapabilityCatalog(LEITOR, [
        Capability(key="OPEN_POST", title="Abrir item", goal="Abrir o item.", bindings=("target",),
                   optional_bindings=("caption_contains",), **post),
        Capability(key="LER", title="Ler", goal="Ler o item.", optional_bindings=("caption_contains",),
                   inherited_bindings=("caption_contains",), **post)])


def _livre(titulo: str, *, efeito: bool = False) -> dict[str, Any]:
    return {"title": titulo, "goal": f"{titulo}.", "side_effect": efeito, "commit_guard": [], "precondition": None,
            "postcondition": {"kind": "model_judged", "value": titulo, "description": f"{titulo} na tela",
                              "required_delivery_level": None},
            "timeout_s": 60, "max_attempts": 2}


def _etapa(key: str, app_id: str, *, capability: str | None = None, bindings: dict[str, str] | None = None,
           livre: dict[str, Any] | None = None, depends_on: list[str] | None = None,
           saidas: list[str] | None = None) -> dict[str, Any]:
    return {"key": key, "app_id": app_id, "capability": capability,
            "bindings": [{"name": k, "value": v} for k, v in (bindings or {}).items()], "livre": livre,
            "depends_on": depends_on or [], "for_each": None, "saidas": saidas or []}


def _bruto(steps: list[dict[str, Any]], app_id: str | None = "instagram", missing: list[Any] | None = None) -> str:
    return json.dumps({"summary": "entre apps", "app_id": app_id, "parameters": [], "success_criteria": ["ok"],
                       "steps": steps, "missing": missing or []})


def _pedido(catalogs: dict[str, Any], apps: list[AppContext] | None = None,
            command: str = "Leia o último e-mail no Outlook e curta o post de @ana") -> PlanRequest:
    return PlanRequest(command=command, run_id="r-entre", instances=NO_IG,
                       apps=apps if apps is not None else [INSTAGRAM, OUTLOOK_LIVRE, CHROME], catalogs=catalogs)


def _montar(raw: str, req: PlanRequest) -> Plan:
    return catalog_plan_from_json(raw, req, provider="p", model="m", max_steps=12)


# ================================================================== apps citados
def test_apps_citados_pelo_nome_e_pelo_rotulo_fora_das_aspas() -> None:
    comando = 'No Instagram, curta o post de @ana; antes, leia o último e-mail no Outlook e abra o Chrome'
    assert [a.id for a in apps_citados(comando, TODOS)] == ["instagram", "outlook", "chrome"]
    assert [a.id for a in apps_citados("abra o outlook e depois o INSTAGRAM", TODOS)] == ["outlook", "instagram"]
    # entre aspas é conteúdo, não pedido; "e-mail" não é o nome de app nenhum
    assert apps_citados('envie "veja no Outlook" para @ana', TODOS) == []
    assert apps_citados("responda o e-mail da ana", TODOS) == []
    # um app sem manifesto: o rótulo seria o pacote, que não é nome falado
    banco = AppContext("banco", "Meu Banco", "com.exemplo.app", None, None, None)
    assert apps_citados("abra com.exemplo.app", [banco]) == []
    assert apps_citados("abra o meu banco", [banco]) == [banco]
    assert pede_site("entre no site https://exemplo.test") and pede_site("abra o navegador")
    assert not pede_site('envie "veja o site https://exemplo.test" para @ana')



def test_apelido_de_app_de_sistema_sem_acento_e_sem_caixa_31_29() -> None:
    """31.29: "Configurações" sozinho não casava o app cadastrado como "Configurações do Android" (11 de 12 na linha de
    base de 04/10 caíram no Instagram). Apelidos de sistema de MAIS DE UMA palavra, sem acento e sem caixa; uma palavra
    só ("ajustes", "configurações") não é apelido, porque sequestraria comando do Instagram (revisão da Android)."""
    ajustes = AppContext("configura-es-do-android", "Configurações do Android", "com.android.settings", None, None, None)
    apps = [*TODOS, ajustes]
    for comando in ("Abra as Configurações do aparelho e mostre a bateria", "abra os ajustes do telefone",
                    "open Android Settings", "abra as configuracoes do celular", "Abra as Configurações do Android",
                    "ABRA AS CONFIGURAÇÕES DO SISTEMA", "abra as configuracoes do android"):
        assert [a.id for a in apps_citados(comando, apps)] == ["configura-es-do-android"], comando
    # uma palavra só segue no app padrão: nenhum app citado
    for comando in ("faça uns ajustes na legenda", "mude as configurações de privacidade",
                    "abra as Configurações e mostre a bateria", "abra os settings"):
        assert apps_citados(comando, apps) == [], comando
    assert [a.id for a in apps_citados("abra as configurações do Instagram", apps)] == ["instagram"]
    assert [a.id for a in apps_citados("abra o instagram", apps)] == ["instagram"]       # sem acento nos de sempre
    assert apps_citados('envie "veja nas Configurações do aparelho" para @ana', apps) == []   # entre aspas é conteúdo


# ================================================================== o que vai ao planejador
def test_um_app_com_catalogo_sem_outro_app_segue_no_planejamento_por_catalogo() -> None:
    catalog, catalogs, apps, pacote = RunService._catalogos("curta o post de @ana", TODOS, NO_IG)  # noqa: SLF001
    assert catalog is not None and catalog.package == IG and catalogs == {} and apps == TODOS and pacote == IG
    # citar o próprio app do aparelho não muda nada
    catalog, catalogs, _, _ = RunService._catalogos("curta o post de @ana no Instagram", TODOS, NO_IG)  # noqa: SLF001
    assert catalog is not None and catalogs == {}
    # aparelho sem app definido e comando que cita o Instagram: o catálogo dele (antes, o plano ia livre)
    sem_app = [{"instance_id": "android-01", "account_label": None, "app_id": None}]
    catalog, catalogs, _, _ = RunService._catalogos("curta o post de @ana no Instagram", TODOS, sem_app)  # noqa: SLF001
    assert catalog is not None and catalog.package == IG and catalogs == {}


def test_sem_catalogo_nenhum_o_plano_e_livre_com_todos_os_apps() -> None:
    comando = 'Abra o QA Messenger e depois o Chrome'
    assert RunService._catalogos(comando, TODOS, NO_QA) == (None, {}, TODOS, None)  # noqa: SLF001
    # Com o Outlook (12.3: catálogo só de leitura) o comando deixa de ser livre: é entre apps, e o Outlook vai com o
    # catálogo dele. Antes ele era o app de etapa livre desta frase.
    catalog, catalogs, _, pacote = RunService._catalogos(  # noqa: SLF001
        'Abra o QA Messenger e depois o Outlook', TODOS, NO_QA)
    assert catalog is None and list(catalogs) == ["outlook"] and pacote is None
    # Um app só, sem catálogo: o plano é livre, mas as lições do planejador são as DELE (o pacote vai junto).
    assert RunService._catalogos("envie oi para a ana", TODOS, NO_QA) == (None, {}, TODOS, QA)  # noqa: SLF001
    assert RunService._catalogos("abra o site https://exemplo.test", TODOS, NO_QA)[3] is None  # noqa: SLF001


def test_citar_outro_app_ou_um_site_nao_derruba_o_catalogo() -> None:
    """R1/R2: antes, os dois comandos iam para o planejador LIVRE, e o efeito no Instagram ficava sem ação."""
    # O Outlook (12.3) entra com o catálogo dele, na ordem em que o comando o cita (antes, só o Instagram).
    # No comando do site o Outlook, que agora tem catálogo e não é candidato, fica fora da lista de apps (senão entraria
    # como app de etapa LIVRE, por fora da política dele).
    for comando, esperados, ids in (
            ("Leia o último e-mail no Outlook e curta o post de @ana", ["outlook", "instagram"],
             ["instagram", "outlook", "qa-messenger", "chrome"]),
            ("Entre no site https://exemplo.test e depois curta o post de @ana", ["instagram"],
             ["instagram", "qa-messenger", "chrome"])):
        catalog, catalogs, apps, pacote = RunService._catalogos(comando, TODOS, NO_IG)  # noqa: SLF001
        assert catalog is None and pacote is None
        assert list(catalogs) == esperados and catalogs["instagram"].package == IG
        assert [a.id for a in apps] == ids
    # aparelho do QA e comando que cita o Instagram: o catálogo do Instagram vai junto
    catalog, catalogs, _, _ = RunService._catalogos("Abra o QA Messenger e curta no Instagram o post de @ana",  # noqa: SLF001
                                                    TODOS, NO_QA)
    assert catalog is None and list(catalogs) == ["instagram"]


def test_app_com_catalogo_que_nao_e_candidato_fica_fora_da_lista(monkeypatch: pytest.MonkeyPatch) -> None:
    """Senão ele entraria como app de etapa LIVRE — por fora da política e dos limites dele."""
    leitor = _catalogo_leitor()
    monkeypatch.setattr("app.taskqueue.service.load_catalog",
                        lambda pacote: leitor if pacote == LEITOR else load_catalog(pacote))
    catalog, catalogs, apps, _ = RunService._catalogos(  # noqa: SLF001
        "Leia o último e-mail no Outlook e curta o post de @ana", [*TODOS, LEITOR_APP], NO_IG)
    assert catalog is None and list(catalogs) == ["outlook", "instagram"]       # o Outlook tem catálogo (12.3)
    assert "leitor" not in {a.id for a in apps} and "outlook" in {a.id for a in apps}
    # citado, ele é candidato: vão os dois catálogos
    _, catalogs, apps, _ = RunService._catalogos(  # noqa: SLF001
        "No Leitor abra o item e no Instagram curta o post de @ana", [*TODOS, LEITOR_APP], NO_IG)
    assert list(catalogs) == ["leitor", "instagram"] and "leitor" in {a.id for a in apps}


# ================================================================== composição pelo app da etapa
def test_cada_etapa_e_montada_pelo_catalogo_do_app_dela_ou_livre() -> None:
    req = _pedido({"instagram": _catalogo_ig()})
    plano = _montar(_bruto([
        _etapa("ler_email", "outlook", livre=_livre("Abrir o último e-mail")),
        _etapa("Open-Profile", "instagram", capability="open_profile", bindings={"username": "@ana"},
               depends_on=["ler_email"]),
        _etapa("like_post", "instagram", capability="LIKE_POST", bindings={"post_author": "@ana"},
               depends_on=["open_profile"]),
    ]), req)
    assert not plano.missing and plano.app_id == "instagram" and plano.app_package == IG
    ler, abrir, curtir = plano.steps
    # etapa do Outlook: livre, no app dela; as do Instagram: ações do catálogo, no app do plano (None)
    assert (ler.app_id, ler.capability, ler.title) == ("outlook", None, "Abrir o último e-mail")
    assert (abrir.key, abrir.capability, abrir.app_id, abrir.depends_on) == ("open_profile", "OPEN_PROFILE", None,
                                                                          ["ler_email"])
    assert curtir.capability == "LIKE_POST" and curtir.side_effect and curtir.max_attempts == 1
    assert curtir.title == _catalogo_ig().get("LIKE_POST").title          # texto do backend, não do modelo
    assert plano.required_apps == ["outlook", "instagram"]


def test_etapa_livre_num_app_com_catalogo_vira_pergunta_e_zera_as_etapas() -> None:
    req = _pedido({"instagram": _catalogo_ig()})
    plano = _montar(_bruto([_etapa("ler_email", "outlook", livre=_livre("Abrir o e-mail")),
                            _etapa("curtir", "instagram", livre=_livre("Curtir o post", efeito=True))]), req)
    assert plano.steps == []
    assert [m.field for m in plano.missing] == ["capability"]
    assert "curtir" in plano.missing[0].question and "LIKE_POST" in plano.missing[0].question


def test_app_desconhecido_acao_inexistente_e_etapa_sem_descricao_viram_pergunta() -> None:
    req = _pedido({"instagram": _catalogo_ig()})
    plano = _montar(_bruto([_etapa("abrir_tiktok", "tiktok", livre=_livre("Abrir o TikTok")),
                            _etapa("voar", "instagram", capability="VOAR"),
                            _etapa("ler_email", "outlook")]), req)
    assert plano.steps == []
    campos = [m.field for m in plano.missing]
    assert campos == ["app", "capability", "step"]
    assert "tiktok" in plano.missing[0].question
    assert plano.missing[1].question.startswith("No Instagram: A ação 'VOAR' não existe")
    assert "ler_email" in plano.missing[2].question


def test_app_com_catalogo_nao_oferecido_nao_vira_etapa_livre() -> None:
    """A segunda trava: mesmo que `apps` traga o Instagram, sem o catálogo dele no pedido a etapa não vira livre."""
    req = _pedido({"leitor": _catalogo_leitor()}, apps=[INSTAGRAM, LEITOR_APP])
    plano = _montar(_bruto([_etapa("curtir", "instagram", livre=_livre("Curtir", efeito=True))], app_id="leitor"), req)
    assert plano.steps == [] and plano.missing[0].field == "app" and "Instagram" in plano.missing[0].question


def test_heranca_de_argumento_nao_atravessa_apps() -> None:
    req = _pedido({"instagram": _catalogo_ig(), "leitor": _catalogo_leitor()},
                  apps=[INSTAGRAM, LEITOR_APP])
    plano = _montar(_bruto([
        _etapa("abrir_post", "instagram", capability="OPEN_POST",
               bindings={"target": "primeira", "caption_contains": "Bom dia"}),
        # o Leitor também tem OPEN_POST e LER herda `caption_contains`: numa herança global, levaria a legenda
        _etapa("ler", "leitor", capability="LER"),
        _etapa("curtir", "instagram", capability="LIKE_POST", bindings={"post_author": "@ana"}),
    ]), req)
    assert not plano.missing
    abrir, ler, curtir = plano.steps
    assert "caption_contains" not in ler.bindings and ler.app_id == "leitor"
    assert curtir.bindings["caption_contains"] == "Bom dia"                 # dentro do Instagram, herda como sempre
    assert plano.required_apps == ["instagram", "leitor"]


def test_app_do_plano_e_um_app_em_que_o_plano_roda() -> None:
    """O modelo disse Chrome como principal, mas nenhuma etapa roda nele: o app do plano é o da primeira etapa (é o
    padrão de `_app_context` para etapa sem app)."""
    req = _pedido({"instagram": _catalogo_ig()})
    plano = _montar(_bruto([_etapa("ler_email", "outlook", livre=_livre("Abrir o e-mail")),
                            _etapa("feed", "instagram", capability="OPEN_FEED")], app_id="chrome"), req)
    assert plano.app_id == "outlook" and plano.app_package == EMAIL_LIVRE
    assert [s.app_id for s in plano.steps] == [None, "instagram"]
    assert plano.required_apps == ["outlook", "instagram"]


def test_chave_repetida_entre_apps_e_saida_invalida() -> None:
    req = _pedido({"instagram": _catalogo_ig()})
    with pytest.raises(AIError) as erro:
        _montar(_bruto([_etapa("abrir", "outlook", livre=_livre("Abrir o e-mail")),
                        _etapa("abrir", "instagram", capability="OPEN_FEED")]), req)
    assert erro.value.kind == "invalid_output"


# ================================================================== required_apps sempre preenchido
def _livre_de_sempre(key: str, app_id: str | None) -> dict[str, Any]:
    return {"key": key, **_livre(key), "depends_on": [], "for_each": None, "app_id": app_id}


def test_plano_livre_e_por_catalogo_declaram_os_apps() -> None:
    """R6: o plano livre não declarava `required_apps`; agora todo plano diz em que apps roda."""
    apps = [QA_APP, INSTAGRAM]
    um = {"summary": "s", "app_id": "qa-messenger", "parameters": [], "success_criteria": [], "missing": [],
          "steps": [_livre_de_sempre("abrir", None)]}
    req = PlanRequest(command="c", run_id="r", instances=NO_QA, apps=apps)
    assert plan_from_json(json.dumps(um), req, provider="p", model="m", max_steps=5).required_apps == ["qa-messenger"]
    dois = {**um, "steps": [_livre_de_sempre("abrir", None), _livre_de_sempre("usar", "instagram")]}
    plano = plan_from_json(json.dumps(dois), req, provider="p", model="m", max_steps=5)
    assert plano.required_apps == ["qa-messenger", "instagram"]
    # sem app no plano nem na etapa: roda no app do aparelho (`_app_context`)
    sem = {**um, "app_id": None}
    assert plan_from_json(json.dumps(sem), req, provider="p", model="m", max_steps=5).required_apps == ["qa-messenger"]
    # por catálogo, um app só
    cat = json.dumps({"summary": "s", "parameters": [], "success_criteria": [], "missing": [],
                      "steps": [{"key": "feed", "capability": "OPEN_FEED", "depends_on": [], "bindings": [],
                                 "for_each": None}]})
    req_cat = PlanRequest(command="c", run_id="r", instances=NO_IG, apps=[INSTAGRAM], catalog=_catalogo_ig())
    assert catalog_plan_from_json(cat, req_cat, provider="p", model="m", max_steps=5).required_apps == ["instagram"]


def test_apps_do_plano_sem_etapa_fica_com_o_app_do_plano() -> None:
    from app.models import PlannerInfo
    info = PlannerInfo(provider="p", model="m", simulated=True)
    assert apps_do_plano(Plan(summary="s", app_id="instagram", planner=info), NO_QA) == ["instagram"]
    assert apps_do_plano(Plan(summary="s", planner=info), NO_QA) == []


def test_etapa_sem_app_em_aparelhos_de_apps_diferentes_nao_exige_o_app_do_outro() -> None:
    """A etapa sem app roda no app DE CADA aparelho (`Scheduler._app_context`): numa seleção QA + Notas, cada aparelho
    precisa só do seu. A união faria o pré-voo, o fluxo aprendido e as próximas execuções exigirem os dois apps em
    todo aparelho — e recusar o do QA por não ter as Notas, que ele nunca vai abrir."""
    from app.models import PlannerInfo, PlanStep, Postcondition
    info = PlannerInfo(provider="p", model="m", simulated=True)
    etapa = PlanStep(key="abrir", title="a", goal="a",
                     postcondition=Postcondition(kind="model_judged", value="x", description="y"))
    misto = [{"app_id": "qa-messenger"}, {"app_id": "notes"}]
    assert apps_do_plano(Plan(summary="x", steps=[etapa], planner=info), misto) == []
    # etapa com app explícito continua declarada; a sem app não acrescenta o de nenhum aparelho
    outra = etapa.model_copy(update={"key": "usar", "app_id": "instagram"})
    assert apps_do_plano(Plan(summary="x", steps=[etapa, outra], planner=info), misto) == ["instagram"]
    # todos os aparelhos no MESMO app: aí é o app em que a etapa roda
    assert apps_do_plano(Plan(summary="x", steps=[etapa], planner=info), [*NO_QA, *NO_QA]) == ["qa-messenger"]


# ================================================================== prompt
def test_prompt_entre_apps_traz_os_dois_tipos_de_app_e_as_regras_de_sempre() -> None:
    req = _pedido({"instagram": _catalogo_ig()}, apps=[INSTAGRAM, OUTLOOK_LIVRE, CHROME])
    texto = prompts.planner_multiapp_user(req, 9)
    com, sem = texto.split("Apps SEM catálogo")
    assert "Apps COM catálogo" in com and "- id: instagram | nome: Instagram | package: com.instagram.android" in com
    assert "  - LIKE_POST(" in com and "OPEN_PROFILE(username)" in com
    assert "dicas do IG" not in texto and "id=feed" not in texto          # quem escolhe ação não precisa de toque
    assert "- id: outlook | nome: Microsoft Outlook" in sem and "caixa de entrada na aba Email" in sem
    assert "- id: chrome" in sem and "- id: instagram" not in sem
    assert "android-01: conta=eu.teste app=instagram" in texto and "Limite de etapas: 9" in texto
    sistema = prompts.PLANNER_MULTIAPP_SYSTEM
    # as regras de cada tipo de etapa são os MESMOS trechos dos dois planejadores de sempre
    assert prompts._REGRAS_DA_ETAPA_LIVRE in sistema and prompts._REGRAS_DO_CATALOGO in sistema  # noqa: SLF001
    assert prompts._REGRAS_DA_ETAPA_LIVRE in prompts.PLANNER_SYSTEM  # noqa: SLF001
    assert prompts._REGRAS_DO_CATALOGO in prompts.PLANNER_CAPABILITY_SYSTEM  # noqa: SLF001
    # o exemplo vedado do plano livre (código lido no Outlook, 24.8) não vem para cá; a vedação vem
    assert "ler um código no Outlook" not in sistema and "Código de verificação, senha ou token" in sistema
    assert prompts.UNTRUSTED_RULE in sistema and prompts.CONDUCT_RULE in sistema


def test_planejador_livre_nao_ensina_mais_a_levar_codigo_de_um_app_a_outro() -> None:
    """Item 24.8 (ADR-058 d3, ADR-009): o exemplo do PLANNER_SYSTEM era justamente o vedado ("pegar um código no
    Outlook e usá-lo no Instagram"). Agora o exemplo é permitido e a vedação está escrita, no mesmo item de regra."""
    sistema = prompts.PLANNER_SYSTEM
    assert "código no Outlook" not in sistema and "usá-lo no Instagram" not in sistema
    regra = sistema[sistema.index("- Se o comando envolver MAIS DE UM app"):sistema.index("- Site ou endereço web")]
    assert "ler o assunto do último e-mail e procurar no Instagram o perfil" in regra
    assert "Código de verificação, senha ou token lido" in regra and "NUNCA é usado em outro" in regra


# ================================================================== provedores, sem rede
def _saida_entre_apps() -> str:
    return _bruto([_etapa("ler_email", "outlook", livre=_livre("Abrir o último e-mail")),
                   _etapa("perfil", "instagram", capability="OPEN_PROFILE", bindings={"username": "@ana"})])


async def test_anthropic_manda_o_sistema_e_o_esquema_entre_apps(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.env.ai_provider = "anthropic"
    p = AnthropicProvider(cfg)
    chamadas: list[dict[str, Any]] = []

    async def criar(**kwargs: Any) -> Any:
        chamadas.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=_saida_entre_apps())], stop_reason="end_turn",
                               model="claude-opus-5",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0))
    falso = SimpleNamespace(create=criar)
    p.configured = True
    p._client = SimpleNamespace(messages=falso, beta=SimpleNamespace(messages=falso))  # noqa: SLF001
    plano, _ = await p.plan(_pedido({"instagram": _catalogo_ig()}))
    chamada = chamadas[0]
    assert chamada["system"][0]["text"] == prompts.PLANNER_MULTIAPP_SYSTEM and chamada["max_tokens"] >= 8000
    etapa = chamada["output_config"]["format"]["schema"]["properties"]["steps"]["items"]
    assert {"app_id", "capability", "livre"} <= set(etapa["required"])
    assert "Apps COM catálogo" in chamada["messages"][0]["content"][0]["text"]
    assert [(s.app_id, s.capability) for s in plano.steps] == [("outlook", None), (None, "OPEN_PROFILE")]
    assert plano.app_id == "instagram" and plano.required_apps == ["outlook", "instagram"]


# ================================================================== valor lido entre apps (24.3)
def _ler_assunto(citada: str = "assunto") -> str:
    """Outlook (sem catálogo): a etapa livre LÊ o assunto do último e-mail; Instagram (catálogo): abre o perfil citado."""
    ler = {**_livre("Abrir o último e-mail e ler o assunto"), "saidas": ["Assunto"]}
    return _bruto([_etapa("ler_email", "outlook", livre=ler),
                   _etapa("perfil", "instagram", capability="OPEN_PROFILE",
                          bindings={"username": "{{saida:" + citada + "}}"})])


async def test_planejador_declara_a_saida_no_outlook_e_o_instagram_a_usa(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.env.ai_provider = "anthropic"
    p = AnthropicProvider(cfg)
    chamadas: list[dict[str, Any]] = []

    async def criar(**kwargs: Any) -> Any:
        chamadas.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=_ler_assunto())], stop_reason="end_turn",
                               model="claude-opus-5",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0))
    falso = SimpleNamespace(create=criar)
    p.configured = True
    p._client = SimpleNamespace(messages=falso, beta=SimpleNamespace(messages=falso))  # noqa: SLF001
    plano, _ = await p.plan(_pedido({"instagram": _catalogo_ig()},
                                    command="Leia o assunto do último e-mail no Outlook e abra no Instagram o perfil "
                                            "citado nele"))
    # o planejador aprende a declarar (`livre.saidas`) e a citar ({{saida:…}}, a chave DUPLA já renderizada)
    sistema = chamadas[0]["system"][0]["text"]
    assert "livre.saidas" in sistema and "{{saida:<nome>}}" in sistema and "{{saida:assunto}}" in sistema
    livre = chamadas[0]["output_config"]["format"]["schema"]["properties"]["steps"]["items"]["properties"]["livre"]
    assert "saidas" in next(o for o in livre["anyOf"] if o.get("type") == "object")["required"]

    assert not plano.missing
    ler, perfil = plano.steps
    assert (ler.app_id, ler.saidas) == ("outlook", ["assunto"])          # nome normalizado
    assert perfil.capability == "OPEN_PROFILE" and referencias(perfil) == ["assunto"]
    # a referência liga a etapa do Instagram à leitura do Outlook e resolve pelo valor lido
    ligada = {s.key: s for s in _dependencias_das_saidas(plano.steps)}["perfil"]
    assert "ler_email" in ligada.depends_on
    assert resolver(perfil.bindings["username"], {"assunto": "@ciclano"}) == ("@ciclano", [])
    assert plano.required_apps == ["outlook", "instagram"]


def test_referencia_sem_leitura_anterior_vira_pergunta_e_zera_as_etapas() -> None:
    # entre apps: cita um nome que ninguém lê
    plano = _montar(_ler_assunto(citada="outro"), _pedido({"instagram": _catalogo_ig()}))
    assert plano.steps == [] and [m.field for m in plano.missing] == ["saida"]
    assert "'perfil'" in plano.missing[0].question and "outro" in plano.missing[0].question
    # plano livre: a leitura vem DEPOIS de quem usa (para a frente)
    usa = {**_livre_de_sempre("abrir_perfil", None), "goal": "Abrir {{saida:perfil}}."}
    le = {**_livre_de_sempre("ler_perfil", None), "saidas": ["perfil"]}
    livre = {"summary": "s", "app_id": "qa-messenger", "parameters": [], "success_criteria": [], "missing": [],
             "steps": [usa, le]}
    req = PlanRequest(command="c", run_id="r", instances=NO_QA, apps=[QA_APP])
    fora_de_ordem = plan_from_json(json.dumps(livre), req, provider="p", model="m", max_steps=5)
    assert fora_de_ordem.steps == [] and [m.field for m in fora_de_ordem.missing] == ["saida"]
    # na ordem certa passa, com a saída declarada
    certo = plan_from_json(json.dumps({**livre, "steps": [le, usa]}), req, provider="p", model="m", max_steps=5)
    assert [s.saidas for s in certo.steps] == [["perfil"], []] and not certo.missing


def test_ator_sabe_quando_usar_read_value() -> None:
    ator = prompts.ACTOR_SYSTEM
    assert "read_value" in ator and "step_done não substitui a leitura" in ator
    assert "antes de qualquer toque de efeito" in ator and "Código de verificação, senha e token nunca" in ator
    assert "{{saida:assunto}}" in prompts.PLANNER_SYSTEM and "NUNCA são saída" in prompts.PLANNER_SYSTEM


async def test_openai_manda_o_sistema_e_o_esquema_entre_apps(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.file.ai.models["qwen-vl"] = ModelCaps(vision=True, tools=True, strict_tools=False,
                                               structured_output="json_schema", thinking=False, effort=False)
    cfg.file.ai.providers["local"] = ProviderCfg(kind="openai", base_url="http://127.0.0.1:8001/v1",
                                                 sends_data_externally=False)
    cfg.file.ai.roles["plan"] = RoleCfg(provider="local", model="qwen-vl")
    p = OpenAICompatProvider(cfg, role=cfg.ai_role("plan"))
    p.models = {papel: "qwen-vl" for papel in ("plan", "decide", "verify", "escalation", "social")}
    vistos: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        return httpx.Response(200, json={"model": "qwen-vl", "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": _saida_entre_apps()}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    p.set_client(httpx.AsyncClient(transport=httpx.MockTransport(responder)))
    try:
        plano, _ = await p.plan(_pedido({"instagram": _catalogo_ig()}))
    finally:
        await p.aclose()
    corpo = json.loads(vistos[0].content)
    assert corpo["messages"][0] == {"role": "system", "content": prompts.PLANNER_MULTIAPP_SYSTEM}
    assert "Apps COM catálogo" in json.dumps(corpo["messages"][1], ensure_ascii=False)
    esquema = corpo["response_format"]["json_schema"]["schema"]
    assert "livre" in esquema["properties"]["steps"]["items"]["properties"]
    assert [(s.app_id, s.capability) for s in plano.steps] == [("outlook", None), (None, "OPEN_PROFILE")]
    assert plano.required_apps == ["outlook", "instagram"]


# ================================================================== simulado
async def test_simulado_entre_apps_compoe_o_plano_de_cada_app_citado() -> None:
    req = PlanRequest(command="Abra o QA Messenger, depois o Outlook e, no Instagram, curtir o post de @ana",
                      run_id="r", instances=NO_QA, apps=[QA_APP, OUTLOOK_APP, INSTAGRAM],
                      catalogs={"instagram": _catalogo_ig()})
    plano, _ = await SimulatedProvider().plan(req)
    assert [(s.key, s.app_id, s.capability) for s in plano.steps] == [
        ("open_app", None, None), ("confirm_account", None, None), ("abrir_outlook", "outlook", None),
        ("open_profile", "instagram", "OPEN_PROFILE"), ("like_post", "instagram", "LIKE_POST")]
    assert plano.app_id == "qa-messenger" and plano.required_apps == ["qa-messenger", "outlook", "instagram"]


async def test_simulado_com_um_app_citado_da_o_plano_de_antes() -> None:
    """O QA pedido num aparelho do Instagram (22d65f): o mesmo plano do QA, sem app por etapa."""
    from .conftest import COMMAND
    antes, _ = await SimulatedProvider().plan(PlanRequest(command=COMMAND, run_id="r", instances=NO_IG,
                                                          apps=[QA_APP, INSTAGRAM]))
    agora, _ = await SimulatedProvider().plan(PlanRequest(command=COMMAND, run_id="r", instances=NO_IG,
                                                          apps=[QA_APP, INSTAGRAM],
                                                          catalogs={"instagram": _catalogo_ig()}))
    assert agora.model_dump() == antes.model_dump()
    assert agora.app_id == "qa-messenger" and agora.required_apps == ["qa-messenger"]
    assert all(s.app_id is None for s in agora.steps)


# ================================================================== na execução
async def test_execucao_planeja_entre_apps_e_grava_o_app_de_cada_etapa(harness: Harness,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    s.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES (?,?,?,?,?)",
                 ("outlook", "Microsoft Outlook", OUTLOOK, ".Main", 0))
    pedidos: list[PlanRequest] = []
    original = s.runs.provider.plan

    async def espiao(req: PlanRequest) -> Any:
        pedidos.append(req)
        return await original(req)
    monkeypatch.setattr(s.runs.provider, "plan", espiao)
    run = harness.run(["android-01"], mode="plan",
                      command="Abra o QA Messenger, depois o Outlook e, no Instagram, curtir o post de @ana")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=30)
    assert s.repo.run_row(run.id)["status"] == "planned"
    req = pedidos[0]
    # O Outlook entra com o catálogo dele (12.3), na ordem em que o comando o cita
    assert req.catalog is None and list(req.catalogs) == ["outlook", "instagram"]
    assert {a.id for a in req.apps} == {"qa-messenger", "instagram", "outlook"}
    plano = Plan.model_validate_json(s.repo.run_row(run.id)["plan"])
    assert plano.required_apps == ["qa-messenger", "outlook", "instagram"]
    etapas = {r["key"]: (r["app_id"], r["capability"]) for r in s.db.query(
        "SELECT key, app_id, capability FROM steps WHERE run_id=?", (run.id,))}
    # a etapa do Outlook agora é AÇÃO do catálogo dele (a entrada do app, no simulador), e não mais a etapa livre
    # `abrir_outlook`
    assert etapas == {"open_app": (None, None), "confirm_account": (None, None),
                      "open_mail_inbox": ("outlook", "OPEN_MAIL_INBOX"),
                      "open_profile": ("instagram", "OPEN_PROFILE"), "like_post": ("instagram", "LIKE_POST")}


# ================================================================== o Outlook real: catálogo só de leitura (12.3)
def _ler_pelo_catalogo(citada: str = "assunto", saidas: list[str] | None = None,
                       capability: str = "OPEN_MAIL_INBOX") -> str:
    """O C1 do dono com o catálogo REAL do Outlook: a ação do catálogo entrega o assunto e o Instagram o usa."""
    return _bruto([_etapa("ler_email", "outlook", capability=capability,
                          saidas=["Assunto"] if saidas is None else saidas),
                   _etapa("perfil", "instagram", capability="OPEN_PROFILE",
                          bindings={"username": "{{saida:" + citada + "}}"})])


def _pedido_outlook(command: str = "Leia o assunto do último e-mail no Outlook e abra no Instagram o perfil citado"
                    ) -> PlanRequest:
    return _pedido({"outlook": _catalogo_outlook(), "instagram": _catalogo_ig()},
                   apps=[INSTAGRAM, OUTLOOK_APP, CHROME], command=command)


def test_c1_ler_no_outlook_pelo_catalogo_e_usar_no_instagram() -> None:
    """C1 (item 24.3) com o Outlook COM catálogo: a ação OPEN_MAIL_INBOX declara entregar o assunto (o nome vai
    normalizado), a etapa do Instagram o cita, e o valor liga as duas — o que antes só a etapa livre fazia."""
    plano = _montar(_ler_pelo_catalogo(), _pedido_outlook())
    assert not plano.missing
    ler, perfil = plano.steps
    assert (ler.app_id, ler.capability, ler.saidas, ler.side_effect) == ("outlook", "OPEN_MAIL_INBOX", ["assunto"],
                                                                        False)
    assert perfil.capability == "OPEN_PROFILE" and referencias(perfil) == ["assunto"]
    ligada = {s.key: s for s in _dependencias_das_saidas(plano.steps)}["perfil"]
    assert "ler_email" in ligada.depends_on
    assert resolver(perfil.bindings["username"], {"assunto": "@ciclano"}) == ("@ciclano", [])
    assert plano.required_apps == ["outlook", "instagram"] and plano.app_id == "instagram"


def test_ler_e_citar_so_o_que_a_acao_do_catalogo_declara() -> None:
    # nome que a ação não declara (um código, até): vira pergunta, o plano zera e nada é lido
    plano = _montar(_ler_pelo_catalogo(saidas=["codigo"], citada="codigo"), _pedido_outlook())
    # duas perguntas: a ação não entrega "codigo" e, sem a etapa, ninguém lê o que o Instagram cita
    assert plano.steps == [] and [m.field for m in plano.missing] == ["open_mail_inbox", "saida"]
    assert "codigo" in plano.missing[0].question and "remetente, assunto" in plano.missing[0].question
    # ação que não entrega valor nenhum (a coleta)
    plano = _montar(_ler_pelo_catalogo(capability="COLLECT_MAIL_HEADERS"), _pedido_outlook())
    assert plano.steps == [] and "não entrega valor" in plano.missing[0].question
    # citar o valor sem declarar a leitura: a pergunta de sempre (24.3), agora também para a ação de catálogo
    plano = _montar(_ler_pelo_catalogo(saidas=[]), _pedido_outlook())
    assert plano.steps == [] and [m.field for m in plano.missing] == ["saida"]


def test_acao_de_catalogo_sem_saidas_na_etapa_nao_le_nada() -> None:
    """Sem `saidas` na etapa a ação de catálogo segue sem ler nada (a receita continua valendo): é o padrão."""
    plano = _montar(_bruto([_etapa("abrir", "outlook", capability="OPEN_MAIL_INBOX")], app_id="outlook"),
                    _pedido_outlook("Abra a caixa de entrada do Outlook"))
    assert not plano.missing and plano.steps[0].saidas == []


def test_etapa_livre_no_outlook_continua_vedada_e_enviar_nao_e_acao() -> None:
    """Efeito no Outlook não ganha caminho novo: etapa livre num app com catálogo vira pergunta (T19), e enviar não é
    ação do catálogo."""
    plano = _montar(_bruto([_etapa("enviar", "outlook", livre=_livre("Enviar o e-mail", efeito=True))],
                           app_id="outlook"), _pedido_outlook("Envie um e-mail pelo Outlook"))
    assert plano.steps == [] and [m.field for m in plano.missing] == ["capability"]
    assert not any(c.side_effect for c in _catalogo_outlook().capabilities)


async def test_planejador_ensina_o_valor_entregue_pela_acao_do_catalogo(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.env.ai_provider = "anthropic"
    p = AnthropicProvider(cfg)
    chamadas: list[dict[str, Any]] = []

    async def criar(**kwargs: Any) -> Any:
        chamadas.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=_ler_pelo_catalogo())],
                               stop_reason="end_turn", model="claude-opus-5",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0))
    falso = SimpleNamespace(create=criar)
    p.configured = True
    p._client = SimpleNamespace(messages=falso, beta=SimpleNamespace(messages=falso))  # noqa: SLF001
    plano, _ = await p.plan(_pedido_outlook())
    chamada = chamadas[0]
    assert "[entrega em `saidas`: remetente, assunto]" in chamada["messages"][0]["content"][0]["text"]
    assert "`saidas` da etapa" in chamada["system"][0]["text"]
    etapa = chamada["output_config"]["format"]["schema"]["properties"]["steps"]["items"]
    assert "saidas" in etapa["required"]
    assert not plano.missing and plano.steps[0].saidas == ["assunto"]

"""Catálogo de ações como DADO (ADR-052, fatia 2): o Instagram sai de `planning/catalog/instagram.py` para
`app/conhecimento/apps/com.instagram.android/catalogo.yaml`, lido por um carregador genérico que recusa o que não se
sustenta na carga.

A equivalência é conferida contra `fixtures/catalogo_instagram_antes.json`, o instantâneo (`dataclasses.asdict` de
cada `Capability`, na ordem) tirado do módulo Python ANTES de ele ser apagado; o que mudou de propósito depois fica
declarado em `CAMPOS_NOVOS` e `MUDANCAS`, com o motivo. Nível de prova: `simulated`.
"""
from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from dataclasses import asdict, fields
from pathlib import Path

import pytest
import yaml

from app.modules.capabilities.domain.definition import LEGACY_CONTRACT_VERSION
from app.planning import capabilities
from app.planning.capabilities import (VERSOES_DE_CONTRATO, Capability, CapabilityCatalog, CatalogoInvalido,
                                       carregar_catalogo, catalogo_do_pacote, load_catalog)

PACKAGE = "com.instagram.android"
BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
ANTES = Path(__file__).with_name("fixtures") / "catalogo_instagram_antes.json"
ARQUIVO = APP / "conhecimento" / "apps" / PACKAGE / "catalogo.yaml"


@pytest.fixture
def sem_cache() -> Iterator[None]:
    """`catalogo_do_pacote` é cacheado por processo; teste que troca a pasta não pode deixar resposta velha."""
    catalogo_do_pacote.cache_clear()
    yield
    catalogo_do_pacote.cache_clear()


def _normalizado(cap: Capability) -> dict[str, object]:
    """`asdict` como o JSON guarda (tupla vira lista); o tipo tupla é conferido à parte."""
    valor: dict[str, object] = json.loads(json.dumps(asdict(cap), ensure_ascii=False))
    return valor


#: O instantâneo (`ANTES`) é a prova de que a MIGRAÇÃO para YAML não mudou nada, e fica como foi tirado. O que o
#: catálogo mudou DEPOIS, de propósito, entra aqui — campo novo com o valor de quem não o declara, e cada mudança de
#: valor com a execução que a motivou. O teste confere que cada entrada é mudança de fato (difere do instantâneo) e
#: que todo o resto continua idêntico: nada muda calado, e nada fica declarado sem ter mudado.
CAMPOS_NOVOS: dict[str, object] = {
    # C10 (r-20260928165254-e31953, r-20260928195344-02ee9e): a legenda que identifica a publicação alvo.
    "card_guard": [],
    # C10, revisão: o controle do cartão tocado SEM efeito (o balão que abre a folha "Comments").
    "card_control": None,
}
MUDANCAS: dict[tuple[str, str], object] = {
    # C10 — o pedido citava "o post que contém 'Ainda sobre Setembro Amarelo 2024'", mas "Posts", a folha "Comments"
    # e `desc==Liked` valiam para QUALQUER publicação, e a curtida tocava o primeiro coração da tela. Com o argumento
    # opcional `caption_contains`, a legenda passa a ser exigida (sem ele, tudo segue como antes).
    ("OPEN_POST", "optional_bindings"): ["caption_contains"],
    ("OPEN_POST", "goal"): ("Abrir a publicação identificada por {target}. Se ela não aparecer nem depois de rolar, "
                            "chame step_blocked em vez de abrir outra no lugar."),
    ("OPEN_POST", "post_description"): ('A publicação está aberta (título "Posts"), com curtidas e comentários '
                                        "visíveis; quando a etapa traz um texto da legenda, esse texto aparece na "
                                        "tela."),
    ("OPEN_POST", "card_guard"): ["{caption_contains}"],
    ("OPEN_COMMENTS", "optional_bindings"): ["caption_contains"],
    ("OPEN_COMMENTS", "card_guard"): ["{caption_contains}"],
    # A folha "Comments" é igual para qualquer publicação e, aberta, deixa a legenda do fundo na árvore
    # (r-20260928165254-e31953): só o toque que a abre distingue o cartão. Medido: e29, `row_feed_button_comment`.
    ("OPEN_COMMENTS", "card_control"): "id=row_feed_button_comment",
    ("LIKE_POST", "optional_bindings"): ["caption_contains"],
    ("LIKE_POST", "commit_guard"): ["{caption_contains}"],
    ("LIKE_POST", "card_guard"): ["{caption_contains}"],
    ("CREATE_COMMENT", "optional_bindings"): ["content_brief", "content", "content_verbatim", "caption_contains"],
    ("CREATE_COMMENT", "commit_guard"): ["{content}", "{caption_contains}"],
}


# ---------------------------------------------------------------------------------------------------- equivalência
def test_o_yaml_do_instagram_carrega_igual_ao_catalogo_em_python_que_substituiu() -> None:
    antes = json.loads(ANTES.read_text(encoding="utf-8"))
    catalogo = carregar_catalogo(ARQUIVO)
    assert catalogo.package == antes["package"] == PACKAGE
    assert catalogo.contract_version == 1
    agora = catalogo.capabilities
    assert [c.key for c in agora] == [c["key"] for c in antes["capabilities"]]      # mesma ordem
    assert len(agora) == 23
    # nenhuma mudança órfã: ação e campo existem (um nome de campo errado nunca seria comparado)
    assert set(MUDANCAS) <= {(c.key, campo) for c in agora for campo in _normalizado(c)}
    for cap, esperado in zip(agora, antes["capabilities"], strict=True):
        obtido = _normalizado(cap)
        assert not set(CAMPOS_NOVOS) & set(esperado), "campo novo já estava no instantâneo"
        assert obtido.keys() == esperado.keys() | CAMPOS_NOVOS.keys(), cap.key
        for campo in obtido:
            de_antes = esperado[campo] if campo in esperado else CAMPOS_NOVOS[campo]
            if (cap.key, campo) in MUDANCAS:
                novo = MUDANCAS[(cap.key, campo)]
                assert novo != de_antes, f"{cap.key}.{campo} declarado como mudança, mas é igual ao instantâneo"
                assert obtido[campo] == novo, f"{cap.key}.{campo}"
            else:
                assert obtido[campo] == de_antes, f"{cap.key}.{campo}"
        # O JSON não distingue tupla de lista; o dado precisa voltar a ser TUPLA (a dataclass é congelada e as
        # guardas são comparadas e concatenadas como tupla no motor).
        for f in fields(Capability):
            if str(f.type) == "tuple[str, ...]":
                assert isinstance(getattr(cap, f.name), tuple), f"{cap.key}.{f.name}"
    assert [c.key for c in catalogo.offered] == [c["key"] for c in antes["capabilities"] if not c["internal"]]


def test_o_registro_de_apps_entrega_ao_instagram_o_catalogo_do_arquivo() -> None:
    """Sem o arquivo, o Instagram cairia calado no planejamento livre (sem prova local, sem guardas de commit): o
    manifesto precisa entregar ao registro exatamente o catálogo lido do YAML."""
    registrado = load_catalog(PACKAGE)
    assert registrado is not None and registrado.package == PACKAGE
    # Por conteúdo, não por identidade: outro teste pode limpar o cache de `catalogo_do_pacote` depois que o registro
    # já guardou o objeto da primeira leitura.
    assert [asdict(c) for c in registrado.capabilities] == [asdict(c) for c in carregar_catalogo(ARQUIVO).capabilities]
    assert len(registrado.capabilities) == 23 and registrado.has("SEND_MESSAGE")


def test_a_versao_aceita_e_a_que_o_registro_de_capabilities_entende() -> None:
    """Aceitar uma versão que o registro não lê seria subir a versão no arquivo sem nada mudar de fato."""
    assert VERSOES_DE_CONTRATO == (LEGACY_CONTRACT_VERSION,)
    assert CapabilityCatalog("com.exemplo.x", []).contract_version == LEGACY_CONTRACT_VERSION


# ---------------------------------------------------------------------------------------------------- recusas
def _acao(**mudancas: object) -> dict[str, object]:
    base: dict[str, object] = {"key": "ABRIR", "title": "Abrir", "goal": "Abrir a tela.", "post_kind": "model_judged",
                               "post_value": "tela aberta", "post_description": "A tela está aberta."}
    base.update(mudancas)
    return base


def _arquivo(tmp_path: Path, dados: object) -> Path:
    caminho = tmp_path / "catalogo.yaml"
    caminho.write_text(yaml.safe_dump(dados, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return caminho


def _doc(*acoes: dict[str, object], **raiz: object) -> dict[str, object]:
    doc: dict[str, object] = {"app": "com.exemplo.email", "contract_version": 1, "acoes": list(acoes)}
    doc.update(raiz)
    return doc


def test_um_catalogo_minimo_de_outro_app_carrega_so_com_dado(tmp_path: Path) -> None:
    enviar_bruta = _acao(key="ENVIAR", bindings=["para"], side_effect=True, risk="high", commit_selector="desc==Send")
    catalogo = carregar_catalogo(_arquivo(tmp_path, _doc(_acao(), enviar_bruta)))
    assert catalogo.package == "com.exemplo.email"
    enviar = catalogo.get("ENVIAR")
    assert enviar.bindings == ("para",) and enviar.side_effect and enviar.timeout_s == 180   # o resto é o padrão


@pytest.mark.parametrize(("dados", "motivo"), [
    (_doc(_acao(commit_selectr="text=Send")), "campo desconhecido commit_selectr"),
    (_doc(_acao(), versao=2), "campo desconhecido versao"),
    ({"contract_version": 1, "acoes": [_acao()]}, "falta `app`"),
    (_doc(_acao(), app="  "), "falta `app`"),
    (_doc({k: v for k, v in _acao().items() if k != "goal"}), "falta goal"),
    (_doc(_acao(side_effect="sim")), "side_effect: esperava true/false"),
    (_doc(_acao(timeout_s=True)), "timeout_s: esperava um inteiro"),
    (_doc(_acao(timeout_s="180")), "timeout_s: esperava um inteiro"),
    (_doc(_acao(bindings="username")), "bindings: esperava uma lista"),
    (_doc(_acao(commit_guard=["{username}", 3])), r"commit_guard\[1\]: esperava texto"),
    (_doc(_acao(title=None)), "title: esperava texto"),
    (_doc(_acao(), _acao()), "ação repetida ABRIR"),
    (_doc(_acao(key="abrir")), "MAIÚSCULAS"),
    (_doc(_acao(risk="altissimo")), "risk: 'altissimo' fora de"),
    (_doc(_acao(default_policy="sempre")), "default_policy: 'sempre' fora de"),
    (_doc(_acao(post_kind="achismo")), "post_kind: 'achismo' fora de"),
    (_doc(_acao(item_key="(sem fechar")), "item_key: expressão regular inválida"),
    (_doc(_acao(local_proof="selector:")), "local_proof"),
    # Controle de cartão sem legenda a conferir nunca seria conferido: declará-lo seria uma guarda de mentira.
    (_doc(_acao(card_control="id=row_feed_button_comment")), "card_control — sem card_guard"),
    (_doc(_acao(card_control="  ", card_guard=["{caption_contains}"], optional_bindings=["caption_contains"])),
     "card_control — seletor vazio"),
    (_doc(_acao(card_control="id=botao|", card_guard=["{caption_contains}"], optional_bindings=["caption_contains"])),
     "card_control — seletor vazio"),
    (_doc(_acao(), contract_version=2), "contract_version: 2 não é entendida"),
    (_doc(_acao(), contract_version=None), "contract_version: esperava um inteiro"),
    (_doc(), "ao menos uma ação"),
    (["não", "é", "mapa"], "esperava um mapa"),
])
def test_o_carregador_recusa_o_que_nao_se_sustenta(tmp_path: Path, dados: object, motivo: str) -> None:
    with pytest.raises(CatalogoInvalido, match=motivo):
        carregar_catalogo(_arquivo(tmp_path, dados))


def test_chave_repetida_no_yaml_e_recusada_em_vez_de_ficar_com_a_ultima(tmp_path: Path) -> None:
    caminho = tmp_path / "catalogo.yaml"
    caminho.write_text("app: com.exemplo.email\ncontract_version: 1\nacoes:\n"
                       "  - key: ENVIAR\n    title: Enviar\n    goal: Enviar.\n    post_kind: model_judged\n"
                       "    post_value: enviado\n    post_description: Enviado.\n"
                       "    commit_selector: desc==Send\n    commit_selector: desc==Delete\n", encoding="utf-8")
    with pytest.raises(CatalogoInvalido, match="chave repetida 'commit_selector' na linha 11"):
        carregar_catalogo(caminho)


def test_catalogo_do_pacote_le_da_pasta_do_app_e_confere_o_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                               sem_cache: None) -> None:
    monkeypatch.setattr(capabilities, "CONHECIMENTO_DE_APPS", tmp_path)
    (tmp_path / "com.exemplo.email").mkdir()
    _arquivo(tmp_path / "com.exemplo.email", _doc(_acao()))
    catalogo = catalogo_do_pacote("com.exemplo.email")
    assert catalogo is not None and catalogo.has("ABRIR")
    assert catalogo_do_pacote("com.exemplo.email") is catalogo                      # cacheado
    assert catalogo_do_pacote("com.exemplo.sem_catalogo") is None                   # sem arquivo: caminho livre
    for invalido in ("", "..", "com/../exemplo", "com.exemplo.email/../x", "semponto"):
        assert catalogo_do_pacote(invalido) is None, invalido
    (tmp_path / "com.exemplo.outro").mkdir()
    _arquivo(tmp_path / "com.exemplo.outro", _doc(_acao()))                        # `app` é com.exemplo.email
    with pytest.raises(CatalogoInvalido, match="a pasta é 'com.exemplo.outro'"):
        catalogo_do_pacote("com.exemplo.outro")


# ---------------------------------------------------------------------------------------------------- catraca
def test_nenhum_app_guarda_catalogo_em_python() -> None:
    """A catraca desta fatia: o catálogo saiu do Python. `planning/catalog/` só guarda o shim do registro, e nenhum
    módulo do backend monta `Capability(...)` fora do carregador — voltar a escrevê-lo em código é a regressão que o
    ADR-052 existe para impedir."""
    assert sorted(p.name for p in (APP / "planning" / "catalog").glob("*.py")) == ["__init__.py"]
    montam: list[str] = []
    importam: list[str] = []
    for caminho in sorted([*APP.rglob("*.py"), *(BACKEND / "tests").rglob("*.py")]):
        if "__pycache__" in caminho.parts:
            continue
        relativo = caminho.relative_to(BACKEND).as_posix()
        arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=relativo)
        for no in ast.walk(arvore):
            if (relativo.startswith("app/") and relativo != "app/planning/capabilities.py"
                    and isinstance(no, ast.Call) and isinstance(no.func, ast.Name) and no.func.id == "Capability"):
                montam.append(f"{relativo}:{no.lineno}")
            if isinstance(no, ast.ImportFrom) and (no.module or "").endswith("catalog.instagram"):
                importam.append(f"{relativo}:{no.lineno}")
    assert not montam, f"Capability montada em código do app: {montam}"
    assert not importam, f"import do catálogo Python apagado: {importam}"

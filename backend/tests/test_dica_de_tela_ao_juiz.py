"""Item 31.46: a dica de tela do app chega ao juiz (e só dele), sem mexer no `VERIFIER_SYSTEM`.

Contexto (r-…-e7bc42, 01/10): a etapa livre `verify_sent` foi recusada com "a lista de e-mails está vazia" porque a
linha da lista do Outlook é um `ComposeView` sem texto nem descrição. O `telas.yaml` do app agora declara isso em
`dicas_ao_juiz`; o executor passa o texto em `VerifyRequest.dicas_da_tela` e os provedores o põem no CONTEÚDO do pedido
(bloco `<dicas_da_tela>`), nunca no sistema (snapshot de `test_prompts_licoes`).

O que se prova:
- o carregador lê a dica do Outlook e recusa, na carga, campo desconhecido, texto vazio ou grande e tela não declarada;
- `dicas_da_tela` devolve a dica na tela do Outlook e `[]` para outro app, para pacote fora do alfabeto e sem declaração;
- o texto do juiz sem dica é IDÊNTICO ao de antes, e com dica traz o bloco depois dos fatos;
- o provedor Anthropic envia a dica no conteúdo, e nada dela no sistema.

Nível de prova: `simulated` (arquivo real do app; árvore sintética; provedor com cliente falso; nenhuma chamada de IA).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.automation import conhecimento_de_telas as telas
from app.automation.hierarchy import parse_hierarchy
from app.planning import prompts
from app.planning.anthropic_provider import AnthropicProvider
from app.planning.capabilities import CONHECIMENTO_DE_APPS
from app.planning.provider import AppContext, ScreenInput, StepContext, VerifyRequest

from .conftest import make_config

OUTLOOK = "com.microsoft.office.outlook"
INSTAGRAM = "com.instagram.android"


def _lista_do_outlook(pacote: str = OUTLOOK) -> Any:
    """A caixa como observada em 02/10: o contêiner `conversation_list` e uma linha sem texto nem descrição."""
    return parse_hierarchy(
        "<hierarchy>"
        f'<node class="androidx.compose.ui.platform.ComposeView" package="{pacote}" text="" content-desc="" '
        f'resource-id="{pacote}:id/conversation_list" clickable="false" enabled="true" bounds="[0,160][720,1115]">'
        f'<node class="android.view.View" package="{pacote}" text="" content-desc="" resource-id="" '
        'clickable="true" enabled="true" bounds="[0,160][720,300]" />'
        "</node></hierarchy>")


def _yaml_minimo(extra: str) -> dict[str, Any]:
    import yaml
    base = """
app: com.exemplo.app
versao: 1
idioma_padrao: en
sinais: {en: {}}
telas:
  - {tela: lista, tipo: autenticada, autenticada: true, ids: [lista]}
estado_conhecido: {telas: [lista]}
"""
    return yaml.safe_load(base + extra)


# ------------------------------------------------------------------ o carregador
def test_o_telas_do_outlook_declara_a_dica_da_lista_sem_texto() -> None:
    k = telas.da_pasta(CONHECIMENTO_DE_APPS / OUTLOOK)
    assert k is not None and len(k.dicas_ao_juiz) == 1
    [texto] = k.dicas_para(None)              # sem `telas`: vale para o app inteiro, reconhecida a tela ou não
    assert "ComposeView" in texto and "remetente" in texto and "assunto" in texto
    assert "leitura visual" in texto            # o conteúdo continua sendo da imagem (ADR-070), não da árvore
    assert len(texto) <= 600 and "\n" not in texto


def test_sem_o_campo_nao_ha_dica() -> None:
    assert telas.de_dados(_yaml_minimo("")).dicas_ao_juiz == ()
    assert telas.de_dados(_yaml_minimo("dicas_ao_juiz: []\n")).dicas_para("lista") == []


def test_dica_por_tela_vale_so_na_tela_citada_e_nao_se_repete() -> None:
    k = telas.de_dados(_yaml_minimo(
        "dicas_ao_juiz:\n  - {texto: só na lista, telas: [lista]}\n  - {texto: sempre}\n  - {texto: sempre}\n"))
    assert k.dicas_para("lista") == ["só na lista", "sempre"]
    assert k.dicas_para("outra") == ["sempre"] and k.dicas_para(None) == ["sempre"]


@pytest.mark.parametrize("bloco, trecho", [
    ("dicas_ao_juiz:\n  - {texto: x, regra: aceitar}\n", "campo desconhecido regra"),
    ("dicas_ao_juiz:\n  - {texto: ''}\n", "`texto` precisa ter"),
    ("dicas_ao_juiz:\n  - {}\n", "`texto` precisa ter"),
    (f"dicas_ao_juiz:\n  - {{texto: {'a' * 601}}}\n", "`texto` precisa ter"),
    ("dicas_ao_juiz:\n  - {texto: x, telas: [nao_existe]}\n", "não está declarada em `telas`"),
    ("dicas_ao_juiz: texto solto\n", "esperava uma lista"),
    ("dicas_ao_juiz: [solto]\n", "esperava um mapa"),
])
def test_dica_malformada_falha_fechada_na_carga(bloco: str, trecho: str) -> None:
    with pytest.raises(telas.ConhecimentoInvalido, match=trecho):
        telas.de_dados(_yaml_minimo(bloco))


# ------------------------------------------------------------------ o que o executor pede
def test_na_tela_do_outlook_a_dica_vem_e_em_outro_app_nao() -> None:
    pasta = CONHECIMENTO_DE_APPS / OUTLOOK
    dicas = telas.dicas_da_tela(pasta, _lista_do_outlook(), package=OUTLOOK)
    assert len(dicas) == 1 and "ComposeView" in dicas[0]
    # Outro app na frente: nenhuma dica do Outlook, mesmo que a árvore pareça a lista.
    assert telas.dicas_da_tela(pasta, _lista_do_outlook(INSTAGRAM), package=INSTAGRAM) == []
    # O Instagram não declara dica: o pedido do juiz fica como era.
    assert telas.dicas_da_tela(CONHECIMENTO_DE_APPS / INSTAGRAM, _lista_do_outlook(INSTAGRAM), package=INSTAGRAM) == []


@pytest.mark.parametrize("pacote", [None, "", "..", "com/outro", "com.microsoft.office.outlook; x", "com.nao.existe"])
def test_pacote_fora_do_alfabeto_ou_sem_pasta_nao_da_dica_nem_erro(pacote: str | None) -> None:
    assert telas.dicas_da_tela(CONHECIMENTO_DE_APPS / (pacote or ""), _lista_do_outlook(), package=pacote) == []


def test_arquivo_invalido_nao_derruba_a_verificacao(tmp_path: Path) -> None:
    pasta = tmp_path / "com.exemplo.app"
    pasta.mkdir()
    (pasta / "telas.yaml").write_text("app: com.exemplo.app\nversao: 9\n", encoding="utf-8")
    assert telas.dicas_da_tela(pasta, _lista_do_outlook("com.exemplo.app"), package="com.exemplo.app") == []


# ------------------------------------------------------------------ o texto do juiz
def _ctx() -> StepContext:
    app = AppContext(id="outlook", name="Outlook", package=OUTLOOK, activity=None, nav_hints=None, known_selectors=None)
    return StepContext(run_id="r1", instance_id="android-01", objective_summary="conferir o e-mail enviado",
                       parameters={}, step_key="verify_sent", step_title="Conferir Enviados", step_goal="Abrir Enviados",
                       side_effect=False, commit_done=False, commit_guard=[], precondition=None,
                       postcondition_description="A pasta Enviados mostra mensagens.", remaining_steps=[], app=app,
                       account_label="conta")


def test_sem_dica_o_texto_do_juiz_e_identico_ao_de_antes() -> None:
    base = prompts.verifier_user_text(_ctx(), "tela", ["e1 View"], None, ["tap(e1) → ok"])
    assert prompts.verifier_user_text(_ctx(), "tela", ["e1 View"], None, ["tap(e1) → ok"], None) == base
    assert prompts.verifier_user_text(_ctx(), "tela", ["e1 View"], None, ["tap(e1) → ok"], []) == base
    assert "dicas_da_tela" not in base


def test_com_dica_o_bloco_vem_depois_dos_fatos_e_antes_da_observacao() -> None:
    texto = prompts.verifier_user_text(_ctx(), "tela", ["e1 View"], None, ["tap(e1) → ok"], ["linha sem texto"])
    assert texto.index("</fatos_do_executor>") < texto.index("<dicas_da_tela>") < texto.index("OBSERVAÇÃO ATUAL")
    assert "  - linha sem texto\n</dicas_da_tela>" in texto


async def test_o_provedor_poe_a_dica_no_conteudo_e_nunca_no_sistema(tmp_path: Path) -> None:
    resposta = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps({"satisfied": "yes", "evidence": "ok",
                                                               "delivery_level": None}))],
        stop_reason="end_turn", model="claude-sonnet-5",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0))

    class Falso:
        def __init__(self) -> None:
            self.chamadas: list[dict[str, Any]] = []

        async def create(self, **kw: Any) -> Any:
            self.chamadas.append(kw)
            return resposta

    cfg = make_config(tmp_path)
    cfg.env.ai_provider = "anthropic"
    p = AnthropicProvider(cfg)
    falso = Falso()
    p.configured = True
    p._client = SimpleNamespace(messages=falso, beta=SimpleNamespace(messages=falso))  # noqa: SLF001
    tela = ScreenInput(width=720, height=1280, jpeg=None, elements=["e1 View"], package=OUTLOOK, sensitive=False)
    dicas = telas.dicas_da_tela(CONHECIMENTO_DE_APPS / OUTLOOK, _lista_do_outlook(), package=OUTLOOK)

    await p.verify(VerifyRequest(ctx=_ctx(), screen=tela))
    await p.verify(VerifyRequest(ctx=_ctx(), screen=tela, dicas_da_tela=dicas))
    sem, com = (c["messages"][0]["content"][-1]["text"] for c in falso.chamadas)
    assert "dicas_da_tela" not in sem and "<dicas_da_tela>" in com and dicas[0] in com
    assert falso.chamadas[0]["system"] == falso.chamadas[1]["system"]          # o sistema não muda por causa da dica
    assert "ComposeView" not in json.dumps(falso.chamadas[1]["system"])

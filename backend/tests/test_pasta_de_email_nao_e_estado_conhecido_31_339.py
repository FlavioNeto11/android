"""31.339: a Junk (e as outras pastas de sistema) não é a caixa de entrada para o estado conhecido do Outlook.

Prova real do P-043 (11/10/2026): a lista de mensagens é o MESMO `conversation_list` na Inbox e na Junk, então a Junk contava como
`caixa_de_entrada`. O preparo da exploração não saía dela, a IA via a pasta já aberta e nada era ensinado (A2 vazio), e a receita
ancorada partindo da Junk divergia no 2º toque (o item Junk é o selecionado da gaveta). Agora o título da barra distingue as pastas de
sistema (`pasta_de_email`); título que não casa segue `caixa_de_entrada`, o comportamento de antes.

Nível de prova: `simulated` (árvores montadas à mão, a mesma forma observada no `android-01`). `real`: `not_run`.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.automation import conhecimento_de_telas as telas
from app.automation.hierarchy import parse_hierarchy
from app.integrations.app_declarado import conhecimento

OUTLOOK = "com.microsoft.office.outlook"


def _barra(titulo: str, *, gaveta: bool = False) -> Any:
    def no(classe: str, rid: str, texto: str = "", desc: str = "", y: int = 0) -> str:
        return (f'<node class="{classe}" package="{OUTLOOK}" text="{texto}" resource-id="{OUTLOOK}:id/{rid}" '
                f'content-desc="{desc}" clickable="true" enabled="true" focused="false" password="false" '
                f'scrollable="false" bounds="[0,{y}][720,{y + 60}]" />')
    nos = [no("android.widget.TextView", "toolbar_title", titulo, y=77),
           no("android.view.View", "conversation_list", y=160),
           no("android.widget.ImageButton", "fab", desc="New mail", y=1003)]
    if gaveta:
        nos.append(no("android.view.View", "drawer_folder_composable", y=200))
    return parse_hierarchy(f'<hierarchy rotation="0">{"".join(nos)}</hierarchy>')


@pytest.mark.parametrize(("titulo", "tela", "casa"), [
    ("Inbox", "caixa_de_entrada", True),
    ("Junk", "pasta_de_email", False), ("Junk Email", "pasta_de_email", False), ("Sent", "pasta_de_email", False),
    ("Drafts", "pasta_de_email", False), ("Archive", "pasta_de_email", False), ("Deleted Items", "pasta_de_email", False),
    ("Lixo eletrônico", "pasta_de_email", False), ("Itens enviados", "pasta_de_email", False),
    ("Pasta do Flavio", "caixa_de_entrada", True),                 # título que não casa: como antes (nunca aperta "voltar" na Inbox)
    ("Caixa de entrada", "caixa_de_entrada", True),
])
@pytest.mark.parametrize("locale", ["en-US", "pt-BR"])
def test_o_titulo_da_barra_distingue_a_pasta_de_sistema_da_caixa(titulo: str, tela: str, casa: bool, locale: str) -> None:
    k = conhecimento.do_app(OUTLOOK)
    r = k.reconhecer(_barra(titulo), package=OUTLOOK, locale=locale)
    assert r.tela == tela and k.telas.em_casa(r.tela) is casa


def test_a_gaveta_aberta_continua_conta_aberta_mesmo_na_junk() -> None:
    k = conhecimento.do_app(OUTLOOK)
    assert k.reconhecer(_barra("Junk", gaveta=True), package=OUTLOOK, locale="en-US").tela == "conta_aberta"


def test_a_leitura_visual_vale_tambem_nas_pastas_de_sistema() -> None:
    k = conhecimento.do_app(OUTLOOK)
    por_tela = {r.tela: r for r in k.telas.regioes_visuais}
    assert por_tela["pasta_de_email"].conteudo_de_terceiros and por_tela["caixa_de_entrada"].conteudo_de_terceiros
    assert por_tela["pasta_de_email"].dentro_de == por_tela["caixa_de_entrada"].dentro_de


async def test_a_exploracao_que_comeca_na_junk_volta_uma_vez_e_chega_na_inbox() -> None:
    """O "voltar" do Outlook leva da Junk à Inbox (observado no `android-01`): o preparo dá 1 passo e termina em casa."""
    k = conhecimento.do_app(OUTLOOK).telas
    pilha = ["Junk", "Inbox"]
    passos: list[str] = []

    async def observar() -> tuple[Any, str | None]:
        return _barra(pilha[0]), OUTLOOK

    async def voltar() -> None:
        passos.append("voltar")
        pilha.pop(0)

    async def reabrir() -> None:
        passos.append("reabrir")

    _t, _p, final, dados = await telas.voltar_ao_estado_conhecido(
        k, observar=observar, voltar=voltar, reabrir=reabrir,
        reconhecer=lambda t, p: telas.classificar(k, t, package=p, locale="en-US"), espera_apos_reabrir_s=0.0)
    assert dados == ["voltar"] and passos == ["voltar"] and final.tela == "caixa_de_entrada"

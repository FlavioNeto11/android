"""Conhecimento de telas como DADO (ADR-052, fatia 1): o motor genérico, o conhecimento do Instagram e a volta ao
estado conhecido que a execução r-20260928165254-e31953 não tinha (conversa aberta → "nenhum sinal" → pessoa)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.automation import conhecimento_de_telas as telas
from app.automation.hierarchy import parse_hierarchy
from app.models import SessionStatus

from .fake_instagram import PKG, FakeInstagram
from . import pacote_instagram as ig
from .test_instagram_auth import USUARIO, FakeRt, build, cadastrar

RAIZ = Path(__file__).resolve().parents[1]         # backend/


def _tela(*nos: tuple[str, str, str], pacote: str = PKG) -> Any:
    """Monta uma árvore com nós (classe, id sem pacote, texto); a barra de navegação fica de fora de propósito."""
    linhas = "".join(
        f'<node class="{c}" package="{pacote}" text="{t}" resource-id="{pacote}:id/{r}" content-desc="" '
        f'clickable="true" enabled="true" focused="false" password="false" scrollable="false" '
        f'bounds="[0,{100 + i * 80}][720,{160 + i * 80}]" />'
        for i, (c, r, t) in enumerate(nos))
    return parse_hierarchy(f'<hierarchy rotation="0">{linhas}</hierarchy>')


@pytest.mark.parametrize(("no", "esperada"), [
    (("android.widget.EditText", "row_thread_composer_edittext", "Message…"), "thread"),
    (("android.widget.EditText", "layout_comment_thread_edittext", "Add a comment…"), "comments"),
    (("android.widget.ImageView", "row_feed_button_like", ""), "post"),
    (("android.widget.EditText", "action_bar_search_edit_text", "Search"), "search"),
])
def test_telas_de_dentro_do_app_sao_logadas_e_nao_sao_casa(no: tuple[str, str, str], esperada: str) -> None:
    """Antes caíam em UNKNOWN ("nenhum sinal conhecido na tela"), e a checagem de sessão chamava uma pessoa."""
    c = ig.reconhecer(_tela(no), package=PKG, locale="en-US")
    assert c.tela == esperada
    assert ig.CONHECIMENTO.autenticada(c.tela) and not ig.CONHECIMENTO.em_casa(c.tela)


def test_conhecimento_do_instagram_e_dado_e_nao_ha_python_do_instagram() -> None:
    """A catraca desta fatia, ampliada na integração das fatias 2–4: o módulo do Instagram deixou de existir, e o
    conhecimento dele (sinais, ids de tela, leitura da conta) é o dado do pacote. Voltar a escrevê-lo em Python é
    exatamente a regressão que o ADR-052 existe para impedir (a catraca geral é `test_apps_fora_do_nucleo`)."""
    assert not (RAIZ / "app" / "integrations" / "instagram").exists()
    assert ig.CONHECIMENTO.app == PKG and ig.CONHECIMENTO.em_casa("feed")
    assert ig.SESSAO.telas is ig.CONHECIMENTO


async def test_sessao_que_comeca_numa_conversa_volta_ao_feed_e_confirma_a_conta(tmp_path: Path) -> None:
    """Execução e31953: o app, reaberto, retomou uma conversa. Agora a checagem volta ao estado conhecido (voltar do
    Android: conversa → caixa → feed) e lê a conta, sem digitar nada e sem chamar pessoa."""
    app = FakeInstagram(account=USUARIO, screen="thread", thread_with="ana", retoma_tela_ao_abrir=True)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.ready and not r.attempted_login and app.typed == []
        assert "key:back" in app.calls
        sess = repo.session_row(pid)
        assert sess["status"] == SessionStatus.session_ready.value and sess["observed_username"] == USUARIO
    finally:
        db.close()


# ------------------------------------------------------------------ o motor não conhece app nenhum
_EMAIL = {
    "app": "com.exemplo.email", "versao": 1, "idioma_padrao": "pt",
    "sinais": {"pt": {"entrar": r"^\s*entrar\s*$"}},
    "telas": [
        {"tela": "caixa", "tipo": "autenticada", "autenticada": True, "ids": ["message_list"], "razao": "caixa"},
        {"tela": "mensagem", "tipo": "autenticada", "autenticada": True, "ids": ["reply_button"], "razao": "lendo"},
        {"tela": "rascunho", "tipo": "autenticada", "autenticada": True, "ids": ["compose_body"], "razao": "escrevendo"},
    ],
    "estado_conhecido": {"telas": ["caixa"], "voltar_max": 3, "reabrir": True},
}


async def test_um_app_novo_ganha_classificacao_e_volta_ao_estado_conhecido_so_com_dados() -> None:
    """Nenhuma linha de Python do app: o mesmo motor classifica as telas de um cliente de e-mail declarado em dado e
    o leva do rascunho à caixa com "voltar"."""
    k = telas.de_dados(_EMAIL)
    pilha = ["caixa", "mensagem", "rascunho"]
    ids = {"caixa": "message_list", "mensagem": "reply_button", "rascunho": "compose_body"}
    passos: list[str] = []

    async def observar() -> tuple[Any, str | None]:
        return _tela(("android.widget.View", ids[pilha[-1]], ""), pacote="com.exemplo.email"), "com.exemplo.email"

    async def voltar() -> None:
        passos.append("voltar")
        pilha.pop()

    async def reabrir() -> None:
        passos.append("reabrir")

    _, _, final, dados = await telas.voltar_ao_estado_conhecido(
        k, observar=observar, voltar=voltar, reabrir=reabrir,
        reconhecer=lambda t, p: telas.classificar(k, t, package=p))
    assert final.tela == "caixa" and dados == ["voltar", "voltar"] and passos == ["voltar", "voltar"]


async def test_login_desafio_e_intersticial_nunca_sao_deixados_para_tras() -> None:
    """"Voltar" num login ou num desafio esconderia a tela que precisa de tratamento (ou de pessoa)."""
    k = telas.de_dados({**_EMAIL, "telas": [
        {"tela": "senha", "tipo": "login", "ids": ["password_box"], "razao": "login"},
        *_EMAIL["telas"]]})
    chamadas: list[str] = []

    async def observar() -> tuple[Any, str | None]:
        return _tela(("android.widget.EditText", "password_box", ""), pacote="com.exemplo.email"), "com.exemplo.email"

    async def nada() -> None:
        chamadas.append("x")

    _, _, final, passos = await telas.voltar_ao_estado_conhecido(
        k, observar=observar, voltar=nada, reabrir=nada, reconhecer=lambda t, p: telas.classificar(k, t, package=p))
    assert final.tipo == "login" and passos == [] and chamadas == []


@pytest.mark.parametrize(("mexe", "trecho"), [
    (lambda d: d["telas"][0].update(tipo="qualquer"), "tipo"),
    (lambda d: d["telas"][0].update(sinal="inexistente"), "sinal"),
    (lambda d: d["estado_conhecido"].update(telas=["nao_existe"]), "estado_conhecido"),
    (lambda d: d.update(idioma_padrao="en"), "idioma_padrao"),
    (lambda d: d["sinais"]["pt"].update(entrar="(sem fechar"), "expressão regular"),
])
def test_conhecimento_invalido_e_recusado_na_carga(mexe: Any, trecho: str) -> None:
    import copy
    dados = copy.deepcopy(_EMAIL)
    mexe(dados)
    with pytest.raises(telas.ConhecimentoInvalido, match=trecho):
        telas.de_dados(dados)

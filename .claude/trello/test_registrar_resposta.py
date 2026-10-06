"""28.65: registrar a resposta do dono (funções puras, dados fictícios, sem rede).

Rodar: backend/.venv/Scripts/python.exe -m pytest -q .claude/trello/test_registrar_resposta.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import registrar_resposta as rr  # noqa: E402

REGISTRO = rr.montar_registro(quando="06/10 19:38Z", entrada=3573, canal="trello", literal="sim, pode")
CARTAO = {"name": "P-099 · Regra de teste", "desc": "Corpo da pergunta", "idList": "lista-perguntas", "shortLink": "abcd1234"}


def test_descricao_no_topo_com_separador():
    [descrever, *_] = rr.decidir(CARTAO, [], REGISTRO, "06/10")
    assert descrever.tipo == "descrever"
    assert descrever.desc.startswith("**RESPOSTA DO DONO (06/10 19:38Z, entrada 3573, digitada por ele no app do Trello")
    assert descrever.desc.endswith("\n\n---\n\nCorpo da pergunta")
    assert '"sim, pode"' in descrever.desc


def test_ações_completas_numa_primeira_vez():
    tipos = [a.tipo for a in rr.decidir(CARTAO, [], REGISTRO, "06/10")]
    assert tipos == ["descrever", "mover", "criar_decisao"]
    mover = rr.decidir(CARTAO, [], REGISTRO, "06/10")[1]
    assert mover.nome == "P-099 · Regra de teste · respondida em 06/10"
    decisao = rr.decidir(CARTAO, [], REGISTRO, "06/10")[2]
    assert decisao.nome == "⚖️ P-099 · Regra de teste · respondida em 06/10"
    assert decisao.desc.startswith("**RESPOSTA DO DONO") and decisao.desc.endswith("https://trello.com/c/abcd1234")


def test_ja_registrada_nao_reescreve_e_nao_remove_nada():
    registrado = {**CARTAO, "desc": REGISTRO + rr.SEPARADOR + "Corpo", "idList": rr.LISTA_RESPONDIDAS}
    assert rr.ja_registrada(registrado["desc"])
    acoes = rr.decidir(registrado, ["⚖️ P-099 · Regra de teste"], "OUTRO TEXTO", "07/10")
    assert acoes == []


def test_ja_registrada_mas_sem_decisao_cria_so_a_decisao_com_o_texto_gravado():
    registrado = {**CARTAO, "desc": REGISTRO + rr.SEPARADOR + "Corpo", "idList": rr.LISTA_RESPONDIDAS}
    [a] = rr.decidir(registrado, [], "OUTRO TEXTO", "06/10")
    assert a.tipo == "criar_decisao" and "OUTRO TEXTO" not in a.desc and '"sim, pode"' in a.desc


def test_decisao_existente_nao_duplica_mesmo_com_data_ou_sem():
    for existente in ("⚖️ P-099 · Regra de teste", "⚖️ P-099 · Regra de teste · respondida em 05/10"):
        assert "criar_decisao" not in [a.tipo for a in rr.decidir(CARTAO, [existente], REGISTRO, "06/10")]


def test_data_no_titulo_nao_duplica():
    assert rr.nome_com_data("P-1 · x", "06/10") == "P-1 · x · respondida em 06/10"
    assert rr.nome_com_data("P-1 · x · respondida em 06/10", "07/10") == "P-1 · x · respondida em 06/10"
    assert rr.titulo_decisao("⚖️ P-1 · x · respondida em 05/10", "06/10") == "⚖️ P-1 · x · respondida em 06/10"


def test_cartao_ja_na_lista_sem_registro_so_renomeia():
    c = {**CARTAO, "idList": rr.LISTA_RESPONDIDAS}
    assert [a.tipo for a in rr.decidir(c, ["⚖️ P-099 · Regra de teste"], REGISTRO, "06/10")] == ["descrever", "renomear"]


def test_literal_intocado_e_leitura_redigida():
    marcar = lambda t: t.replace("segredo", "[x]")  # noqa: E731
    r = rr.montar_registro(quando="06/10 19:38Z", entrada=1, canal="telegram", literal="meu segredo é este",
                           leitura="o segredo vai embora", confirmacao="Telegram, entrada 2, 'respondido'",
                           redigir=marcar)
    assert '"meu segredo é este"' in r                      # o literal é a palavra dele
    assert "Lido como: o [x] vai embora" in r              # a leitura é da Canais
    assert "digitada por ele no Telegram" in r
    assert "Confirmada em bloco: Telegram, entrada 2, 'respondido'." in r


def test_data_curta_e_canal_invalidos():
    assert rr.data_curta("06/10 19:38Z") == "06/10"
    with pytest.raises(ValueError):
        rr.data_curta("ontem")
    with pytest.raises(ValueError):
        rr.montar_registro(quando="06/10", entrada=1, canal="zap", literal="x")


def test_ensaio_sem_aplicar_nao_chama_a_rede(monkeypatch, capsys):
    """Sem --aplicar a `_principal` só imprime; a rede é trocada por um cliente falso que falha em qualquer escrita."""
    import types

    escritas: list[str] = []

    class Falso:
        def __init__(self, *_a: object) -> None: ...
        async def _pedir(self, metodo: str, caminho: str, **_k: object):  # noqa: ANN202
            if metodo != "GET":
                escritas.append(metodo)
            return CARTAO | {"id": "x"} if "/cards/" in caminho else []
        async def atualizar_cartao(self, *_a: object, **_k: object) -> None:
            escritas.append("PUT")
        async def criar_cartao(self, *_a: object, **_k: object) -> dict[str, str]:
            escritas.append("POST")
            return {}

    class Seg:
        def get_secret_value(self) -> str:
            return "x"

    cfg = types.ModuleType("app.config")
    cfg.EnvSettings = lambda: types.SimpleNamespace(trello_api_key=Seg(), trello_token=Seg())  # type: ignore[attr-defined]
    ad = types.ModuleType("app.modules.avisos.adapters.trello")
    ad.ClienteTrello = Falso  # type: ignore[attr-defined]
    for nome, mod in (("app", types.ModuleType("app")), ("app.config", cfg), ("app.modules", types.ModuleType("m")),
                      ("app.modules.avisos", types.ModuleType("a")), ("app.modules.avisos.adapters", types.ModuleType("d")),
                      ("app.modules.avisos.adapters.trello", ad)):
        monkeypatch.setitem(sys.modules, nome, mod)
    import asyncio
    args = rr.argparse.Namespace(cartao="abcd1234", entrada=1, canal="trello", quando="06/10 19:38Z", literal="sim",
                                 leitura="", confirmacao="", aplicar=False)
    assert asyncio.run(rr._principal(args)) == 0
    saida = capsys.readouterr().out
    assert "ensaio" in saida and "descrever" in saida and "criar_decisao" in saida
    assert escritas == []

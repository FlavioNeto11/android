"""28.67: o espelho do deploy num comando só. Dados FICTÍCIOS (plano, nomes e contagens inventados), nenhuma rede, nenhum Trello.

Rodar: backend/.venv/Scripts/python.exe -m pytest -q .claude/trello/test_espelho_do_deploy.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from espelho_do_deploy import (  # noqa: E402
    LISTA_PROXIMAS,
    ItemDoPlano,
    PassoFalhou,
    Passos,
    Resumo,
    contar_acoes,
    criar_cartoes_que_faltam,
    ids_com_cartao,
    ids_sem_cartao,
    itens_do_plano,
    linha_final,
    montar_cartao,
    orquestrar,
    raiz_em_origin_main,
)
from redacao import redigir  # noqa: E402

PLANO = """# Plano

| ID | O que | Origem | Esforço |
|---|---|---|---|
| 90.1 | **Tela nova de teste: mostra o painel** — detalhe do item um no android-13 e no emulator-5560. | auditoria | M |
| 90.2 | **Outro item** — sem mais detalhe. | pedido | S |
| 90.3 | **Item já com cartão** — x. | pedido | S |
| T.7 | **Item do tipo T** — y. | pedido | S |
| 90.1 | **Linha repetida do 90.1** — z. | pedido | S |
| texto solto que não é linha de tabela |
"""


def test_itens_do_plano_le_so_as_linhas_de_tabela():
    itens = itens_do_plano(PLANO)
    assert [i.pid for i in itens] == ["90.1", "90.2", "90.3", "T.7", "90.1"]
    assert itens[1].origem == "pedido" and itens[1].esforco == "S"


def test_ids_com_cartao_aceita_marca_do_dono_e_ignora_o_que_nao_e_item():
    nomes = ["90.3 · Item já com cartão", "[A] 90.2 · Outro", "📅 Deploy 90 · marco", "90.30 · outro id", "T.7 · tipo T",
             "Como ler este quadro"]
    assert ids_com_cartao(nomes) == {"90.3", "90.2", "90.30", "T.7"}


def test_ids_sem_cartao_ignora_quem_ja_tem_e_nao_repete():
    faltam = ids_sem_cartao(itens_do_plano(PLANO), {"90.3", "T.7"})
    assert [i.pid for i in faltam] == ["90.1", "90.2"]
    # "90.30" com cartão não esconde o "90.3": o ID é inteiro, não prefixo
    assert [i.pid for i in ids_sem_cartao(itens_do_plano(PLANO), {"90.30"})][:3] == ["90.1", "90.2", "90.3"]


def test_cartao_novo_nao_traz_aparelho_nem_handle():
    item = ItemDoPlano("90.1", "**Tela nova: mostra o painel do android-13** — viu no emulator-5560 e no worker-02; "
                               "conta @fulano_teste e senha=segredo123.", "auditoria do worker-01", "M")
    nome, desc = montar_cartao(item, 91, redigir)
    assert nome == "90.1 · Tela nova"
    for texto in (nome, desc):
        assert not any(t in texto for t in ("android-13", "emulator-5560", "worker-02", "worker-01", "fulano_teste", "segredo123"))
    assert "aparelho" in desc and "@[conta]" in desc and "[segredo]" in desc
    assert "ao fechar o deploy 91" in desc and "**Esforço:** M" in desc


def test_cartao_novo_sem_negrito_no_corpo_usa_o_corpo_como_titulo():
    nome, desc = montar_cartao(ItemDoPlano("90.2", "texto simples sem negrito", "pedido", ""), 91, lambda t: t)
    assert nome == "90.2 · texto simples sem negrito" and "**Esforço:** ?" in desc


class ClienteFalso:
    def __init__(self, cai_na: int | None = None) -> None:
        self.criados: list[tuple[str, str, str]] = []
        self.cai_na = cai_na

    async def criar_cartao(self, lista: str, nome: str, desc: str) -> dict:
        if self.cai_na is not None and len(self.criados) == self.cai_na:
            raise ConnectionError("rede caiu")
        self.criados.append((lista, nome, desc))
        return {"id": "x"}


def test_criar_so_em_proximas_e_ensaio_nao_grava():
    faltam = ids_sem_cartao(itens_do_plano(PLANO), {"90.3", "T.7"})
    cl = ClienteFalso()
    nomes = asyncio.run(criar_cartoes_que_faltam(cl, faltam, 91, False, redigir))
    assert len(nomes) == 2 and cl.criados == []
    nomes = asyncio.run(criar_cartoes_que_faltam(cl, faltam, 91, True, redigir))
    assert len(nomes) == 2 and {c[0] for c in cl.criados} == {LISTA_PROXIMAS}


def test_queda_no_meio_deixa_os_criados_e_a_proxima_rodada_cria_so_o_resto():
    itens = itens_do_plano(PLANO)
    cl = ClienteFalso(cai_na=1)
    with pytest.raises(ConnectionError):
        asyncio.run(criar_cartoes_que_faltam(cl, ids_sem_cartao(itens, set()), 91, True, redigir))
    assert len(cl.criados) == 1                                   # o 90.1 nasceu; o 90.2 não
    com = ids_com_cartao([c[1] for c in cl.criados])
    assert [i.pid for i in ids_sem_cartao(itens, com)] == ["90.2", "90.3", "T.7"]


def test_raiz_em_origin_main():
    assert raiz_em_origin_main("abc123\n", "abc123")
    assert not raiz_em_origin_main("abc123", "def456")
    assert not raiz_em_origin_main("", "")


def test_contar_acoes():
    acoes = [("mover", "concluido"), ("mover", "concluido"), ("mover", "em_validacao"), ("marcar", None), ("marcar", None),
             ("marcar", None), ("listar", None), ("mover", "bloqueado")]
    assert contar_acoes(acoes) == {"concluido": 2, "em_validacao": 1, "so_linha": 3, "outros": 1}


def _passos(chamadas: list[str], **falhas: Exception) -> Passos:
    def passo(nome: str, retorno: dict):
        def f(*_a):
            chamadas.append(nome)
            if nome in falhas:
                raise falhas[nome]
            return retorno
        return f
    return Passos(
        conferir=passo("conferir", {"deploy": 58}),
        cartoes=passo("cartoes", {"plano": 1400, "com_cartao": 1395, "sem_cartao": 5, "nasceram": 5}),
        reconciliar=passo("reconciliar", {"acoes": [("mover", "concluido")] * 12 + [("mover", "em_validacao")] * 3
                                          + [("marcar", None)] * 7 + [("listar", None)] * 4, "falhas": 0}),
        marco=passo("marco", {"marco": "criado https://trello.com/c/AbCd1234", "m8": "9 online de 9", "m9": "6.700 + 400"}),
    )


def test_linha_final_com_as_contagens():
    feitos: list[str] = []
    r = orquestrar(_passos([]), None, feitos)
    assert r.deploy == 58 and len(feitos) == 4
    assert linha_final(r, ensaio=False) == (
        "deploy 58: plano 1400 IDs, 1395 cartões, 5 sem cartão; nasceram 5; para Concluído 12; para Em validação 3; "
        "só linha 7; marco criado https://trello.com/c/AbCd1234; M8 9 online de 9; M9 6.700 + 400; falhas nenhuma")


def test_linha_final_no_ensaio_e_com_falhas_e_outros_movimentos():
    r = Resumo(deploy=57, plano=10, com_cartao=10, acoes=[("mover", "bloqueado")], falhas=2,
               marco="ensaio: atualizaria 6ac1", m8="não tocado (x)", m9="ensaio 1 + 2")
    linha = linha_final(r, ensaio=True)
    assert "nasceriam 0" in linha and "outros movimentos 1" in linha and linha.endswith("falhas 2")
    assert "\n" not in linha


def test_deploy_pedido_vale_sobre_o_mais_recente():
    assert orquestrar(_passos([]), 57, []).deploy == 57


@pytest.mark.parametrize("quebra, numero", [("conferir", 0), ("cartoes", 1), ("reconciliar", 2), ("marco", 3)])
def test_para_no_passo_com_erro_e_nao_roda_os_seguintes(quebra, numero):
    chamadas: list[str] = []
    feitos: list[str] = []
    ordem = ["conferir", "cartoes", "reconciliar", "marco"]
    with pytest.raises(PassoFalhou) as ex:
        orquestrar(_passos(chamadas, **{quebra: ConnectionError("https://api.exemplo/?key=SEGREDO")}), None, feitos)
    assert ex.value.numero == numero
    assert chamadas == ordem[: numero + 1]                    # nada depois do que falhou
    assert len(feitos) == numero                              # e o relato diz o que já estava feito
    assert ex.value.causa == "ConnectionError"                # só o tipo: a mensagem (URL, chave) não sobe
    assert "SEGREDO" not in str(ex.value)


def test_raiz_desatualizada_para_no_passo_zero_sem_tocar_o_trello():
    chamadas: list[str] = []
    desatualizada = PassoFalhou(0, "conferir a raiz", "raiz desatualizada (HEAD aaaa, origin/main bbbb)")
    with pytest.raises(PassoFalhou) as ex:
        orquestrar(_passos(chamadas, conferir=desatualizada), None, [])
    assert chamadas == ["conferir"] and ex.value.numero == 0 and "desatualizada" in ex.value.causa


def test_reconciliar_le_o_plano_da_raiz_que_recebe(tmp_path, monkeypatch):
    """`reconciliar._principal(raiz=...)`: o estado do plano e o CHANGELOG vêm da raiz dada, não do checkout do script."""
    import reconciliar as R

    (tmp_path / ".claude" / "plano-100").mkdir(parents=True)
    (tmp_path / ".claude" / "plano-100" / "estado.json").write_text(
        json.dumps({"itens": {"90.1": {"status": "blocked", "blocker": "falta algo fictício"}}}), encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Registro", encoding="utf-8")

    class SegredoFalso:
        def get_secret_value(self) -> str:
            return "x"

    class EnvFalso:
        trello_api_key = trello_token = SegredoFalso()

    class ClienteTrelloFalso:
        def __init__(self, *_a) -> None: ...

        async def _pedir(self, _metodo: str, caminho: str, **_kw):
            if caminho.endswith("/lists"):
                return [{"id": "l1", "name": "Próximas"}, {"id": "l2", "name": "Bloqueado"}]
            return [{"id": "c1", "name": "90.1 · item fictício", "idList": "l1", "desc": ""}]

    config = types.ModuleType("app.config")
    config.EnvSettings = EnvFalso
    trello = types.ModuleType("app.modules.avisos.adapters.trello")
    trello.ClienteTrello = ClienteTrelloFalso
    for nome, mod in (("app", types.ModuleType("app")), ("app.config", config), ("app.modules", types.ModuleType("app.modules")),
                      ("app.modules.avisos", types.ModuleType("app.modules.avisos")),
                      ("app.modules.avisos.adapters", types.ModuleType("app.modules.avisos.adapters")),
                      ("app.modules.avisos.adapters.trello", trello)):
        monkeypatch.setitem(sys.modules, nome, mod)
    resultado: dict = {}
    assert asyncio.run(R._principal(False, tmp_path, resultado)) == 0
    assert resultado["acoes"] == [("mover", "bloqueado")] and resultado["falhas"] == 0 and resultado["cartoes"] == 1

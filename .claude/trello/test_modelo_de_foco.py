"""Modelo de foco do quadro Execução (10/10/2026): posições lógicas, cliente que grava lista + etiqueta de estado e a
leitura dos cartões de aparelho. Tudo com cliente falso: nenhuma chamada de rede.

Rodar: backend/.venv/Scripts/python.exe -m pytest -q .claude/trello/test_modelo_de_foco.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import modelo_de_foco as MF  # noqa: E402
from cartoes_de_aparelho import LISTA_CONCLUIDO, LISTA_EM_EXECUCAO, LISTA_EM_VALIDACAO, ler_cartoes  # noqa: E402

FRENTE = "6ac13aeda5570365d020f911"
E = MF.ETIQUETA_DE_ESTADO


class TrelloFalso:
    """Só o `_pedir` do ClienteTrello: guarda cartões {id: {idList, idLabels, name, desc}} e registra as chamadas."""

    def __init__(self, cartoes: dict[str, dict] | None = None) -> None:
        self.cartoes = cartoes or {}
        self.chamadas: list[tuple[str, str, dict]] = []

    async def _pedir(self, metodo, caminho, *, params=None, corpo=None):  # noqa: ANN001
        self.chamadas.append((metodo, caminho, dict(corpo or {})))
        if metodo == "POST" and caminho == "/1/cards":
            cid = f"c{len(self.cartoes) + 1}"
            self.cartoes[cid] = {"id": cid, "idList": corpo["idList"], "name": corpo["name"], "desc": corpo["desc"],
                                 "idLabels": [i for i in corpo.get("idLabels", "").split(",") if i]}
            return self.cartoes[cid]
        if metodo == "GET" and caminho == "/1/boards/" + MF.QUADRO_EXECUCAO + "/cards":
            return list(self.cartoes.values())
        cid = caminho.rsplit("/", 1)[-1]
        if metodo == "GET":
            return self.cartoes[cid]
        if metodo == "PUT":
            c = self.cartoes[cid]
            for k, v in corpo.items():
                c["idLabels" if k == "idLabels" else k] = [i for i in v.split(",") if i] if k == "idLabels" else v
            return c
        raise AssertionError((metodo, caminho))

    async def atualizar_cartao(self, card, *, nome=None, desc=None, lista=None):  # noqa: ANN001
        return await self._pedir("PUT", f"/1/cards/{card}", corpo={k: v for k, v in
                                                                   {"name": nome, "desc": desc, "idList": lista}.items() if v})


def test_posicao_do_cartao_por_lista_e_etiqueta():
    assert MF.posicao_do_cartao(MF.LISTA_FEITO, []) == LISTA_CONCLUIDO
    assert MF.posicao_do_cartao(MF.LISTA_EM_CURSO, [E["em_execucao"], FRENTE]) == LISTA_EM_EXECUCAO
    for estado in ("em_validacao", "proximas", "bloqueado", "espera_voce", "aguardando"):
        assert MF.posicao_do_cartao(MF.LISTA_EM_CURSO, [E[estado]]) == LISTA_EM_VALIDACAO
    assert MF.posicao_do_cartao(MF.LISTA_EM_CURSO, []) == LISTA_EM_VALIDACAO          # cartão novo, sem etiqueta
    assert MF.posicao_do_cartao(MF.LISTA_FOCO, [E["em_execucao"]]) == MF.LISTA_FOCO     # lista à mão volta como veio


def test_criar_grava_em_curso_com_a_etiqueta_do_estado():
    t = TrelloFalso()
    asyncio.run(MF.ClienteDePosicoes(t).criar_cartao(MF.POS_PROXIMAS, "31.1 · x", "d"))
    c = t.cartoes["c1"]
    assert (c["idList"], c["idLabels"]) == (MF.LISTA_EM_CURSO, [E["proximas"]])
    asyncio.run(MF.ClienteDePosicoes(t).criar_cartao(MF.POS_CONCLUIDO, "31.2 · y", "d"))
    assert (t.cartoes["c2"]["idList"], t.cartoes["c2"]["idLabels"]) == (MF.LISTA_FEITO, [])


def test_atualizar_troca_a_etiqueta_de_estado_e_guarda_a_de_frente():
    t = TrelloFalso({"c1": {"id": "c1", "idList": MF.LISTA_EM_CURSO, "name": "n", "desc": "d",
                            "idLabels": [FRENTE, E["em_execucao"]]}})
    cl = MF.ClienteDePosicoes(t)
    asyncio.run(cl.atualizar_cartao("c1", lista=MF.POS_EM_VALIDACAO))
    assert (t.cartoes["c1"]["idList"], sorted(t.cartoes["c1"]["idLabels"])) == (MF.LISTA_EM_CURSO, sorted([FRENTE, E["em_validacao"]]))
    asyncio.run(cl.atualizar_cartao("c1", nome="novo", lista=MF.POS_CONCLUIDO))
    assert (t.cartoes["c1"]["idList"], t.cartoes["c1"]["idLabels"], t.cartoes["c1"]["name"]) == (MF.LISTA_FEITO, [FRENTE], "novo")


def test_atualizar_sem_lista_nao_mexe_em_etiqueta_nem_lista():
    t = TrelloFalso({"c1": {"id": "c1", "idList": MF.LISTA_EM_CURSO, "name": "n", "desc": "d", "idLabels": [E["bloqueado"]]}})
    asyncio.run(MF.ClienteDePosicoes(t).atualizar_cartao("c1", desc="outra"))
    assert t.cartoes["c1"]["desc"] == "outra" and t.cartoes["c1"]["idLabels"] == [E["bloqueado"]]
    assert all(m != "GET" for m, _, _ in t.chamadas)


def test_ler_cartoes_de_aparelho_devolve_a_posicao_logica():
    t = TrelloFalso({
        "a": {"id": "a", "idList": MF.LISTA_EM_CURSO, "name": "android-01 · x", "desc": "Aparelho: android-01", "idLabels": [E["em_execucao"]]},
        "b": {"id": "b", "idList": MF.LISTA_FEITO, "name": "android-02 · x", "desc": "Aparelho: android-02", "idLabels": []},
        "c": {"id": "c", "idList": MF.LISTA_FOCO, "name": "android-03 · x", "desc": "Aparelho: android-03", "idLabels": []},
    })
    por_id = {c.id: c.lista for c in asyncio.run(ler_cartoes(t))}
    assert por_id == {"a": LISTA_EM_EXECUCAO, "b": LISTA_CONCLUIDO, "c": MF.LISTA_FOCO}


def test_destino_dos_estados():
    assert MF.destino("concluido") == (MF.LISTA_FEITO, None)
    assert MF.destino("bloqueado") == (MF.LISTA_EM_CURSO, "bloqueado")
    assert MF.destino("espera_voce") == (MF.LISTA_EM_CURSO, "espera_voce")


def test_estado_pela_etiqueta_ignora_etiqueta_de_frente():
    assert MF.estado_pela_etiqueta([FRENTE]) is None
    assert MF.estado_pela_etiqueta([FRENTE, E["aguardando"]]) == "aguardando"

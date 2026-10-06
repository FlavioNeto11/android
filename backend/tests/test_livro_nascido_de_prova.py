"""31.143 (adendo v1.92): a lista do Livro (`GET /api/aprendizado`) traz `nascido_de_prova` em cada item e honra o
filtro `?nascido_de_prova=true|false`.

Achado do percurso 52 da Portal: a marca do 31.130 só saía em `conteudo.origem` do detalhe, e o selo e o filtro "Prova"
da lista (31.131) ficavam sem dado.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

from .conftest import Harness
from .test_fluxo_nascido_de_prova import _ensinar
from .test_perfil_bloqueado_e_capacidades import _cliente


async def test_a_lista_do_livro_traz_a_marca_e_filtra_nos_dois_valores(harness: Harness) -> None:
    async with _cliente(harness) as c:
        _, f_prova = await _ensinar(harness, c, prova=True, comando="abra a conversa de prova no app")
        _, f_real = await _ensinar(harness, c, prova=None, comando="abra a conversa real no app")

        r = await c.get("/api/aprendizado", params={"rotulo": "todos"})       # o QA Messenger sai da lista padrão
        assert r.status_code == 200, r.text
        itens = r.json()["itens"]
        assert all(isinstance(i["nascido_de_prova"], bool) for i in itens)   # em todo item, de todo tipo
        fluxos = {i["ref"]: i["nascido_de_prova"] for i in itens if i["kind"] == "fluxo"}
        assert fluxos[f_prova] is True and fluxos[f_real] is False
        assert not any(i["nascido_de_prova"] for i in itens if i["kind"] != "fluxo")   # só o fluxo tem a marca

        so_prova = (await c.get("/api/aprendizado", params={"rotulo": "todos", "nascido_de_prova": "true"})).json()
        assert [i["ref"] for i in so_prova["itens"]] == [f_prova] and so_prova["total"] == 1
        assert sum(n for v in so_prova["contagem"].values() for n in v.values()) == 1   # a contagem já filtrada

        sem_prova = (await c.get("/api/aprendizado", params={"rotulo": "todos", "nascido_de_prova": "false"})).json()
        refs = [i["ref"] for i in sem_prova["itens"]]
        assert f_prova not in refs and f_real in refs and sem_prova["total"] == len(itens) - 1

        # o filtro soma com os outros (kind) e o valor inválido é 422, como nos outros filtros
        com_tipo = (await c.get("/api/aprendizado", params={"rotulo": "todos", "kind": "fluxo",
                                                            "nascido_de_prova": "false"})).json()
        assert [i["ref"] for i in com_tipo["itens"]] == [f_real]
        assert (await c.get("/api/aprendizado", params={"nascido_de_prova": "talvez"})).status_code == 422

        detalhe = (await c.get(f"/api/aprendizado/fluxo/{f_prova}")).json()
        assert detalhe["item"]["nascido_de_prova"] is True                 # o item do detalhe também

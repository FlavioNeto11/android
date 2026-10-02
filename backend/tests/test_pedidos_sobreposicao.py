"""Item 28.4 — sobreposição pura: as três políticas, o teto por autonomia, a ordem e a pausa (pedidos-laco.md §3).

Prova `simulated`: funções puras, sem banco.
"""
from __future__ import annotations

import pytest

from app.modules.pedidos.domain.sobreposicao import Devida, decidir, politica_efetiva


def _d(n: int) -> Devida:
    return Devida(f"o{n}", f"2026-10-02T12:0{n}:00Z", f"ped:p:g:2026-10-02T12:0{n}:00Z")


UMA, DUAS, TRES = [_d(1)], [_d(1), _d(2)], [_d(1), _d(2), _d(3)]


def test_pular_com_aberta_pula_todas_e_sem_aberta_despacha_so_a_mais_antiga() -> None:
    p = decidir("pular", "observar", ["rodando-1"], TRES)
    assert p.despachar == () and [i for i, _ in p.pular] == ["o1", "o2", "o3"]
    assert all(m == "a anterior ainda roda (rodando-1)" for _, m in p.pular)
    p = decidir("pular", "observar", [], TRES)
    assert p.despachar == ("o1",) and p.pular == (("o2", "a anterior ainda roda (o1)"),
                                                  ("o3", "a anterior ainda roda (o1)"))


def test_guardar_uma_guarda_a_mais_antiga_e_descarta_as_que_chegam_com_uma_guardada() -> None:
    com = decidir("guardar_uma", "observar", ["rodando-1"], TRES)
    assert com.despachar == () and com.pular == (("o2", "já há uma guardada (o1)"), ("o3", "já há uma guardada (o1)"))
    # a guardada fica `devida`: nem despacha nem aparece em `pular`
    assert "o1" not in com.despachar and "o1" not in dict(com.pular)
    sem = decidir("guardar_uma", "observar", [], TRES)
    assert sem.despachar == ("o1",) and sem.pular == (("o3", "já há uma guardada (o2)"),)
    assert decidir("guardar_uma", "observar", [], UMA).despachar == ("o1",)
    assert decidir("guardar_uma", "observar", ["x"], UMA).pular == ()


def test_permitir_todas_despacha_tudo_na_ordem() -> None:
    p = decidir("permitir_todas", "observar", ["rodando-1"], list(reversed(TRES)))
    assert p.despachar == ("o1", "o2", "o3") and p.pular == ()


def test_teto_por_autonomia_agir_so_pula_preparar_nao_permite_todas() -> None:
    assert politica_efetiva("pular", "agir") == ("pular", False)
    assert politica_efetiva("guardar_uma", "agir") == ("pular", True)
    assert politica_efetiva("permitir_todas", "agir") == ("pular", True)
    assert politica_efetiva("guardar_uma", "preparar") == ("guardar_uma", False)
    assert politica_efetiva("permitir_todas", "preparar") == ("pular", True)
    assert politica_efetiva("permitir_todas", "observar") == ("permitir_todas", False)
    # desconhecido (banco editado à mão): o lado seguro
    assert politica_efetiva("qualquer", "observar") == ("pular", True)
    assert politica_efetiva("permitir_todas", "autonomia-inventada") == ("pular", True)


def test_pedido_incoerente_age_como_pular_e_avisa() -> None:
    p = decidir("permitir_todas", "agir", ["x"], DUAS)
    assert p.politica == "pular" and p.incoerente and p.despachar == ()
    assert not decidir("pular", "agir", [], UMA).incoerente


def test_ordem_e_por_instante_depois_por_chave() -> None:
    mesmo = [Devida("b", "2026-10-02T12:00:00Z", "ped:p:g:b"), Devida("a", "2026-10-02T12:00:00Z", "ped:p:g:a"),
             Devida("c", "2026-10-02T11:59:00Z", "ped:p:g:z")]
    assert decidir("permitir_todas", "observar", [], mesmo).despachar == ("c", "a", "b")


def test_pedido_pausado_pula_tudo_e_sem_devidas_nao_faz_nada() -> None:
    p = decidir("permitir_todas", "observar", [], DUAS, pedido_pausado=True)
    assert p.despachar == () and p.pular == (("o1", "pedido pausado"), ("o2", "pedido pausado"))
    vazio = decidir("pular", "observar", ["x"], [])
    assert vazio.despachar == () and vazio.pular == ()

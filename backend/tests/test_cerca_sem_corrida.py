"""Cerca sem corrida (frente F4, item A2): dois pedidos simultâneos no mesmo aparelho nunca dividem a cerca.

`CommandStore.create` lia `MAX(fence) + 1` FORA da transação: duas threads (ou duas réplicas no mesmo banco) liam
o mesmo máximo e gravavam a mesma cerca em dois comandos. Cerca repetida é autoridade dupla — o agente não tem
como saber qual das duas ordens é a atual. `elevar_cerca` lia dentro, mas no PostgreSQL (`READ COMMITTED`) isso
não serializa nada.

Para a corrida acontecer SEMPRE (e não por sorte de escalonamento), a leitura do máximo é seguida de um encontro
entre as duas threads, com prazo curto: sem a trava, as duas leem e se encontram; com a trava, a segunda espera a
primeira terminar, o encontro vence o prazo e cada uma segue sozinha.

Roda nos dois bancos pela fábrica configurada (`make_config` → `cfg.db_dsn`): SQLite aqui, PostgreSQL no job do
CI com `TEST_DATABASE_URL`. `duas` conexões é o caso de duas réplicas (ou dois processos); `mesma`, o de duas
threads de um processo.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from app.commands.store import CommandStore
from app.db import Database

from .conftest import make_config

#: Prazo do encontro. Curto porque, com a trava certa, ele SEMPRE vence (a segunda thread está bloqueada).
ENCONTRO_S = 0.5


def _bancos(tmp_path: Path, conexoes: str) -> tuple[Database, Database]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db1 = Database(cfg.db_dsn)
    db1.migrate()
    return db1, (db1 if conexoes == "mesma" else Database(cfg.db_dsn))


def _encontro_depois_do_maximo(bancos: tuple[Database, ...], encontro: threading.Barrier) -> None:
    """Toda leitura de `MAX(fence)` espera a outra thread chegar ao mesmo ponto (ou o prazo vencer)."""
    for db in {id(b): b for b in bancos}.values():
        original = db.scalar

        def scalar(sql: str, params: Any = (), _original: Any = original) -> Any:
            valor = _original(sql, params)
            if "MAX(fence)" in sql:
                try:
                    encontro.wait()
                except threading.BrokenBarrierError:
                    pass            # a outra está esperando a trava: é o comportamento certo
            return valor

        db.scalar = scalar  # type: ignore[method-assign]


def _em_paralelo(*acoes: Any) -> list[BaseException]:
    erros: list[BaseException] = []

    def rodar(acao: Any) -> None:
        try:
            acao()
        except BaseException as exc:  # noqa: BLE001 - o teste relata, não esconde
            erros.append(exc)

    threads = [threading.Thread(target=rodar, args=(a,)) for a in acoes]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "uma thread ficou presa: a trava virou impasse"
    return erros


def _cercas(db: Database, instance_id: str) -> list[int]:
    return sorted(int(r["fence"]) for r in db.query("SELECT fence FROM commands WHERE instance_id=?",
                                                     (instance_id,)))


@pytest.mark.parametrize("conexoes", ["mesma", "duas"])
def test_dois_pedidos_simultaneos_no_mesmo_aparelho_nao_repetem_a_cerca(tmp_path: Path, conexoes: str) -> None:
    db1, db2 = _bancos(tmp_path, conexoes)
    loja1, loja2 = CommandStore(db1), CommandStore(db2)
    _encontro_depois_do_maximo((db1, db2), threading.Barrier(2, timeout=ENCONTRO_S))

    erros = _em_paralelo(
        lambda: loja1.create(command_id="c-a", instance_id="android-01", verb="start", idempotency_key="k-a"),
        lambda: loja2.create(command_id="c-b", instance_id="android-01", verb="stop", idempotency_key="k-b"))

    assert erros == []
    assert _cercas(db1, "android-01") == [1, 2], "dois comandos do mesmo aparelho saíram com a mesma cerca"


@pytest.mark.parametrize("conexoes", ["mesma", "duas"])
def test_aparelhos_diferentes_nao_se_esperam_nem_se_misturam(tmp_path: Path, conexoes: str) -> None:
    """A cerca é POR APARELHO: a trava de um não pode mudar a sequência do outro."""
    db1, db2 = _bancos(tmp_path, conexoes)
    loja1, loja2 = CommandStore(db1), CommandStore(db2)
    erros = _em_paralelo(
        lambda: loja1.create(command_id="c-a", instance_id="android-01", verb="start", idempotency_key="k-a"),
        lambda: loja2.create(command_id="c-b", instance_id="android-02", verb="start", idempotency_key="k-b"))
    assert erros == []
    assert _cercas(db1, "android-01") == [1] and _cercas(db1, "android-02") == [1]


@pytest.mark.parametrize("conexoes", ["mesma", "duas"])
def test_elevar_a_cerca_e_criar_ao_mesmo_tempo_nao_empatam(tmp_path: Path, conexoes: str) -> None:
    """Banco restaurado (K-004): o despacho sobe a cerca de um comando ainda em `created` enquanto outro pedido
    do mesmo aparelho está sendo aceito. As duas leituras de `MAX` disputam a mesma próxima cerca."""
    db1, db2 = _bancos(tmp_path, conexoes)
    loja1, loja2 = CommandStore(db1), CommandStore(db2)
    loja1.create(command_id="c-velho", instance_id="android-01", verb="stop", idempotency_key="k-velho")
    loja1.create(command_id="c-1", instance_id="android-01", verb="start", idempotency_key="k-1")
    db1.execute("UPDATE commands SET fence=1 WHERE id='c-1'")       # cerca que o agente já viu (piso 1)
    db1.execute("UPDATE commands SET fence=3 WHERE id='c-velho'")   # e um MAX acima do piso no banco
    _encontro_depois_do_maximo((db1, db2), threading.Barrier(2, timeout=ENCONTRO_S))

    erros = _em_paralelo(
        lambda: loja1.elevar_cerca("c-1", 1),
        lambda: loja2.create(command_id="c-novo", instance_id="android-01", verb="stop", idempotency_key="k-2"))

    assert erros == []
    cercas = _cercas(db1, "android-01")
    assert len(cercas) == len(set(cercas)) == 3, f"cerca repetida: {cercas}"
    assert int(db1.scalar("SELECT fence FROM commands WHERE id='c-1'")) > 1


def test_chave_repetida_continua_devolvendo_o_original_sem_gastar_cerca(tmp_path: Path) -> None:
    """A leitura do `MAX` entrou na transação; a idempotência por chave não pode ter mudado com isso."""
    db, _ = _bancos(tmp_path, "mesma")
    loja = CommandStore(db)
    primeiro, repetido = loja.create(command_id="c-1", instance_id="android-01", verb="start", idempotency_key="k")
    de_novo, repetido2 = loja.create(command_id="c-2", instance_id="android-01", verb="start", idempotency_key="k")
    assert (repetido, repetido2) == (False, True)
    assert de_novo["id"] == primeiro["id"] == "c-1"
    assert _cercas(db, "android-01") == [1]
    novo, _ = loja.create(command_id="c-3", instance_id="android-01", verb="stop", idempotency_key="k-3")
    assert novo["fence"] == 2

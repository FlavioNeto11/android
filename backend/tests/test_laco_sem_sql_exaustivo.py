"""31.343 (ponto 10 do 31.307): o exame que faltava, agora EXAUSTIVO sobre o laço que roda de verdade.

O `test_laco_sem_sql_sincrono.py` prova ponto a ponto (cada chamador que o vigia flagrou ganhou um teste). O ponto 10 escapou porque o
despejo de 02:07Z de 11/10 pegou o `Scheduler._tick` (`_manter_posse` → `adotar_abandonadas` → `abandoned_steps`) em SQL síncrono na thread do
laço, e nenhum teste girava o `_tick` do laço com a posse VENCIDA e olhava de qual thread vinha o SQL. Aqui o banco é espiado por
INTEIRO: toda chamada ao banco feita da thread do laço, no scheduler girando de verdade (`Harness.boot`), é registrada com a pilha, e:

1. a posse e a adoção NÃO aparecem entre elas (e a adoção rodou, numa thread);
2. o que aparece é exatamente a DÍVIDA CONHECIDA do `_tick` (lista nomeada abaixo): chamada nova na thread do laço quebra o teste, e a
   dívida paga também (a lista só encolhe). É uma catraca, como a de `Any`.

Prova `simulated` (SQLite; o harness gira o scheduler com tick curto). `real`: `not_run`.
"""
from __future__ import annotations

import asyncio
import threading
import traceback
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from app import db as db_mod
from app.taskqueue import scheduler as scheduler_mod

from .conftest import Harness

#: A dívida conhecida do `Scheduler._tick` (idle, 1 aparelho): SQL síncrono na thread do laço. **Zerada no 31.348**: as seis leituras por volta
#: (contas bloqueadas, execuções ativas, objetivos despacháveis duas vezes, capacidade e limites do worker, `settings.get` vencido) vêm de uma
#: foto tirada numa thread antes do `_tick` (`Scheduler._tirar_a_foto_do_tick`). Medido neste arquivo, 3 s de tick ocioso no harness:
#: antes 728 chamadas em 121 voltas (6 por volta); depois 0. Acrescentar uma chamada nova ao tick ocioso quebra o teste.
DIVIDA_DO_TICK: dict[str, str] = {}
#: As funções do scheduler que a posse e a adoção percorrem: NENHUMA pode aparecer na thread do laço.
POSSE_E_ADOCAO = {"_manter_posse", "adotar_abandonadas", "abandoned_steps", "renew_claims", "renovar", "take_over", "_reconciliar"}


@contextmanager
def espiar_o_laco(laco: int) -> Iterator[list[tuple[str, list[str]]]]:
    """Registra (R|W, funções do `backend/app` na pilha, da mais interna para a mais externa) de cada chamada ao banco vinda de `laco`."""
    vistas: list[tuple[str, list[str]]] = []
    original = db_mod.Database._com_reconexao

    def espia(self, operacao, *, escrita=False):  # type: ignore[no-untyped-def]
        if threading.get_ident() == laco:
            quadros = [f for f in traceback.extract_stack()[:-1]
                       if "/backend/app/" in f.filename.replace(chr(92), "/") and not f.filename.endswith("db.py")]
            vistas.append(("W" if escrita else "R", [f.name for f in reversed(quadros)]))
        return original(self, operacao, escrita=escrita)

    db_mod.Database._com_reconexao = espia  # type: ignore[method-assign]
    try:
        yield vistas
    finally:
        db_mod.Database._com_reconexao = original  # type: ignore[method-assign]


async def test_a_posse_e_a_adocao_rodam_numa_thread_e_nao_na_do_laco(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        assert h.state is not None
        sched = h.state.scheduler
        monkeypatch.setattr(scheduler_mod, "RENOVAR_POSSE_S", 0.05)     # a posse vence a cada volta do tick curto do harness
        sched._posse_renovada = 0.0
        laco = threading.get_ident()
        onde: list[int] = []
        original = sched.repo.abandoned_steps

        def abandoned_espiada():  # type: ignore[no-untyped-def]
            onde.append(threading.get_ident())
            return original()
        sched.repo.abandoned_steps = abandoned_espiada                  # type: ignore[method-assign]
        with espiar_o_laco(laco) as vistas:
            await asyncio.sleep(1.5)
        assert onde, "a adoção das etapas abandonadas não rodou"
        assert all(t != laco for t in onde), "a leitura de etapas abandonadas rodou na thread do laço"
        na_posse = [q for _, q in vistas if POSSE_E_ADOCAO & set(q)]
        assert na_posse == [], f"SQL da posse/adoção na thread do laço: {na_posse[:3]}"
    finally:
        await h.state.stop()  # type: ignore[union-attr]


async def test_o_tick_do_laco_so_deve_ao_que_esta_na_divida_conhecida(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        assert h.state is not None
        await asyncio.sleep(1.5)                                        # a subida (faxinas, avisos, conectividade) assenta
        laco = threading.get_ident()
        ticks0 = h.state.scheduler.ticks
        with espiar_o_laco(laco) as vistas:
            await asyncio.sleep(3)
        assert h.state.scheduler.ticks > ticks0, "o scheduler não girou durante a janela do espião"
        # A chave de cada chamada = a função mais interna do app; a do `_tick` é o que sobra depois de tirar o que é do laço.
        funcoes = Counter(q[0] for _, q in vistas if q)
        novas = sorted(set(funcoes) - set(DIVIDA_DO_TICK))
        assert novas == [], f"chamada NOVA ao banco na thread do laço (mova para asyncio.to_thread): {novas} {dict(funcoes)}"
        pagas = sorted(set(DIVIDA_DO_TICK) - set(funcoes))
        assert pagas == [], f"dívida paga, tire de DIVIDA_DO_TICK: {pagas}"
        assert vistas == [], f"o tick ocioso voltou a ler o banco na thread do laço: {dict(funcoes)}"
        escritas = [q for t, q in vistas if t == "W"]
        assert escritas == [], f"ESCRITA na thread do laço num tick ocioso: {escritas[:3]}"
    finally:
        await h.state.stop()  # type: ignore[union-attr]


async def test_a_foto_do_tick_e_tirada_numa_thread_e_o_tick_a_consome_no_laco(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        assert h.state is not None
        sched = h.state.scheduler
        laco = threading.get_ident()
        fotos: list[int] = []
        consumidas: list[bool] = []
        tirar = sched._tirar_a_foto_do_tick
        volta = sched._volta

        def tirar_espiada(maquinas):  # type: ignore[no-untyped-def]
            fotos.append(threading.get_ident())
            return tirar(maquinas)

        def volta_espiada(**kw):  # type: ignore[no-untyped-def]
            consumidas.append(sched._foto is not None and sched._foto.laco == threading.get_ident() == laco)
            return volta(**kw)
        sched._tirar_a_foto_do_tick = tirar_espiada                       # type: ignore[method-assign]
        sched._volta = volta_espiada                                      # type: ignore[method-assign]
        await asyncio.sleep(1.0)
        assert fotos and all(t != laco for t in fotos), "a foto das leituras não foi tirada numa thread"
        assert consumidas and all(consumidas), "o tick do laço não consumiu a foto"
        # Fora do laço (o teste que gira o tick à mão) a volta lê ao vivo e não deixa foto para trás.
        sched._tick()
        assert sched._foto is None
    finally:
        await h.state.stop()  # type: ignore[union-attr]

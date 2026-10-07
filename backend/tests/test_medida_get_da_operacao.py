"""31.194: medida do GET da operação com 30 alvos, em consultas ao banco e tempo de parede por leitura (a Portal lê em
laço). Mede também a leitura de um alvo por vez (`com_lote=False`), que é o antes, e confere que as duas respostas são
iguais.

Medida, não regra: roda só com `MEDIR_GET_OP=1` (o tempo de parede varia com a máquina). Harness na porta 5640, 30
aparelhos falsos, banco temporário e o provedor simulado (nenhuma IA paga). Com `MEDIR_GET_OP_RODAR=1` as execuções
rodam até o fim antes de medir (cada alvo com objetivo, etapas e pedido); sem ele, mede-se com um aparelho ativo por
vez, quase todos os alvos parados. Nível de prova: `simulated`.
"""
from __future__ import annotations

import asyncio
import os
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from app.modules.operacoes.infrastructure.servico import AlvoPedido, ServicoDeOperacoes

from .conftest import Harness
from .test_operacoes import _conta, _pedido, _persona, _servico

ALVOS = int(os.environ.get("MEDIR_GET_OP_ALVOS", "30"))
RODAR = os.environ.get("MEDIR_GET_OP_RODAR") == "1"

pytestmark = pytest.mark.skipif(os.environ.get("MEDIR_GET_OP") != "1", reason="medida opt-in (MEDIR_GET_OP=1)")


def _medir(s: ServicoDeOperacoes, op_id: str) -> tuple[dict[str, object], Counter[str], Counter[str], list[float]]:
    contagem: Counter[str] = Counter()
    por_sql: Counter[str] = Counter()
    db: Any = s.db
    originais = {n: getattr(db, n) for n in ("query", "one", "scalar", "execute")}

    def contando(nome: str) -> Any:
        def f(sql: str, params: Any = ()) -> Any:
            contagem[nome] += 1
            por_sql[" ".join(sql.split())[:90]] += 1
            return originais[nome](sql, params)
        return f
    for nome in originais:
        setattr(db, nome, contando(nome))
    tempos = []
    try:
        for _ in range(10):
            t0 = time.perf_counter()
            lida = s.ler(op_id)
            tempos.append((time.perf_counter() - t0) * 1000)
    finally:
        for nome, f in originais.items():
            setattr(db, nome, f)
    return lida, contagem, por_sql, tempos


async def test_medida_do_get_da_operacao(tmp_path: Path) -> None:
    h = Harness(tmp_path, ALVOS)
    h.cfg.file.limits.max_active_devices = 6 if RODAR else 1
    await h.boot()
    try:
        st = h.state
        assert st is not None
        alvos = []
        for i in range(1, ALVOS + 1):
            pid = _persona(h, f"Medida{i}", f"android-{i:02d}")
            _conta(h, pid, f"qa-user-{i:02d}", sessao_em=f"android-{i:02d}")
            alvos.append(AlvoPedido(pid))
        s = _servico(h)
        op = s.criar(_pedido(alvos, chave="medida-get-op", max_usd=10.0))
        if RODAR:
            prazo = time.monotonic() + 300
            while time.monotonic() < prazo:
                lida = s.ler(op["id"])
                if all(a["estado"] not in ("pendente", "em_curso") for a in lida["alvos"]):  # type: ignore[attr-defined]
                    break
                await asyncio.sleep(0.5)
        s.ler(op["id"])                              # aquece (a 1ª anota os estágios)
        linhas = []
        respostas = []
        for com_lote in (False, True):
            s.com_lote = com_lote
            lida, contagem, por_sql, tempos = _medir(s, op["id"])
            respostas.append(lida)
            por_leitura = sum(contagem.values()) / 10
            linhas.append(f"{'depois (lote)' if com_lote else 'antes (um por vez)'}: {por_leitura:.0f} consultas por"
                          f" leitura; parede p50 {statistics.median(tempos):.1f} ms, máx {max(tempos):.1f} ms")
            for sql, n in por_sql.most_common(6):
                linhas.append(f"    {n / 10:6.1f}  {sql}")
        assert respostas[0] == respostas[1]
        com_etapas = sum(1 for a in respostas[1]["alvos"] if len(a["estagios"]) > 4)  # type: ignore[attr-defined]
        print(f"\n{ALVOS} alvos ({'execuções terminadas' if RODAR else 'um aparelho por vez'}; {com_etapas} com"
              f" estágios de app):\n" + "\n".join(linhas))
    finally:
        if h.state is not None:
            await h.state.stop()

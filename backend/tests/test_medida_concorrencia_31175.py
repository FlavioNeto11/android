"""31.175, parte simulada: a latência por etapa de uma operação de 6 alvos com 4 e com 6 vagas de `max_ai_concurrency`.

Medida, não regra: roda só com `MEDIR_31175=1` (o tempo real de parede varia com a máquina, e a suíte não pode depender
disso). Harness na porta 5640, aparelhos falsos (`FakeQaDevice`) e o provedor simulado com uma latência ARTIFICIAL fixa
por chamada (`LATENCIA_S`), para a vaga de IA ser o gargalo que se quer ver. O resultado vai para
`.claude/handoffs/jev-31-175-medida.md` (a tabela é impressa pelo teste).

Nível de prova: `simulated`. A leitura real fica para a onda 2 (só leitura, sem gasto).
"""
from __future__ import annotations

import asyncio
import os
import statistics
import time
from pathlib import Path
from typing import Any

import pytest

from app.modules.operacoes.infrastructure.servico import AlvoPedido

from .conftest import Harness
from .test_operacoes import _conta, _pedido, _persona, _servico

LATENCIA_S = float(os.environ.get("MEDIR_31175_LATENCIA", "0.5"))
ALVOS = 6

pytestmark = pytest.mark.skipif(os.environ.get("MEDIR_31175") != "1", reason="medida opt-in (MEDIR_31175=1)")


class _ComLatencia:
    """O provedor do harness com `LATENCIA_S` a mais em toda chamada assíncrona (a ida e volta de um modelo de verdade)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, nome: str) -> Any:
        alvo = getattr(self._inner, nome)
        if not asyncio.iscoroutinefunction(alvo):
            return alvo

        async def com_latencia(*args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(LATENCIA_S)
            return await alvo(*args, **kwargs)
        return com_latencia


def _pct(valores: list[float], p: float) -> float:
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, max(0, round(p * (len(ordenados) - 1))))]


async def _medir(tmp: Path, vagas: int) -> dict[str, Any]:
    h = Harness(tmp, ALVOS)
    h.cfg.file.limits.max_ai_concurrency = vagas
    h.cfg.file.limits.max_active_devices = ALVOS
    h.ai = _ComLatencia(h.ai)  # type: ignore[assignment]
    await h.boot()
    try:
        st = h.state
        assert st is not None
        alvos = []
        for i in range(1, ALVOS + 1):
            pid = _persona(h, f"Medida{i}", f"android-{i:02d}")
            _conta(h, pid, f"qa-user-{i:02d}", sessao_em=f"android-{i:02d}")
            alvos.append(AlvoPedido(pid))
        inicio = time.monotonic()
        op = _servico(h).criar(_pedido(alvos, chave=f"medida-31175-{vagas}", max_usd=10.0))
        prazo = inicio + 300
        while time.monotonic() < prazo:
            lida = _servico(h).ler(op["id"])
            if lida["status"] != "em_curso" and all(a["estado"] != "pendente" for a in lida["alvos"]):
                break
            await asyncio.sleep(0.2)
        total = time.monotonic() - inicio
        etapas: dict[str, list[float]] = {}
        for s in st.db.query("SELECT s.key, s.started_at, s.finished_at FROM steps s JOIN runs r ON r.id=s.run_id"
                             " WHERE r.operacao_id=? AND s.started_at IS NOT NULL AND s.finished_at IS NOT NULL",
                             (op["id"],)):
            ini = time.mktime(time.strptime(s["started_at"][:19], "%Y-%m-%dT%H:%M:%S")) + float("0" + s["started_at"][19:23])
            fim = time.mktime(time.strptime(s["finished_at"][:19], "%Y-%m-%dT%H:%M:%S")) + float("0" + s["finished_at"][19:23])
            etapas.setdefault(str(s["key"]), []).append(fim - ini)
        vagas_ms = [float(r["vaga_ms"]) for r in st.db.query(
            "SELECT a.vaga_ms FROM ai_calls a JOIN runs r ON r.id=a.run_id WHERE r.operacao_id=? AND a.vaga_ms IS NOT NULL",
            (op["id"],))]
        estados = [(a["estado"], a["estagio"], a["motivo"]) for a in lida["alvos"]]
        return {"vagas": vagas, "total_s": total, "etapas": etapas, "espera_vaga_ms": vagas_ms, "estados": estados,
                "status": lida["status"]}
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_medida_da_concorrencia_de_ia(tmp_path: Path) -> None:
    linhas = ["| vagas | etapa | n | p50 (s) | p95 (s) |", "|---|---|---|---|---|"]
    resumo = []
    for vagas in (4, 6):
        m = await _medir(tmp_path / f"v{vagas}", vagas)
        for chave, duracoes in sorted(m["etapas"].items()):
            linhas.append(f"| {vagas} | {chave} | {len(duracoes)} | {_pct(duracoes, 0.5):.2f} | {_pct(duracoes, 0.95):.2f} |")
        espera = m["espera_vaga_ms"]
        resumo.append(f"vagas {vagas}: total {m['total_s']:.1f} s; status {m['status']}; estados {m['estados']}; "
                      f"espera pela vaga p50 {statistics.median(espera) if espera else 0:.0f} ms, "
                      f"p95 {_pct(espera, 0.95) if espera else 0:.0f} ms, máx {max(espera) if espera else 0:.0f} ms "
                      f"em {len(espera)} chamadas")
        assert all(e[0] != "pendente" for e in m["estados"]), m
    print("\n".join(["", *linhas, "", *resumo]))

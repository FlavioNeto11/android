"""31.237: o 1º plano de uma operação sai sozinho e os planos irmãos esperam por ele para ler o cache do prompt.

Medido na onda 2 (07/10, `ai_calls` em `mode=ro`): na operação cancelada, os 3 planos saíram no mesmo instante e cada um
GRAVOU o mesmo prefixo (`cache_write` 3.243, `cache_read` 0), a US$ 0,018 o plano; na onda válida, 7,5 min depois, os 3
leram o prefixo (`cache_read` 3.243) a US$ 0,0095. O cache é do provedor; aqui ele é imitado: um plano que começa depois
de outro já TERMINADO lê; o que começa sem nenhum terminado grava.

O que estes testes protegem:
* a vez (`VezDoPlano`): o 1º da operação é o primeiro; os que chegam enquanto ele roda esperam e saem depois dele (com
  sucesso ou com erro); fora de operação ou com espera 0, ninguém espera; vencido o teto, o irmão segue;
* a operação de 3 alvos no harness: 1 gravação e 2 leituras com a espera ligada; 3 gravações com ela desligada.

Nível de prova: `simulated` (harness com provedor e aparelhos falsos; o cache é imitado).
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.taskqueue.vez_do_plano import VezDoPlano

from .conftest import Harness
from .test_operacoes import AlvoPedido, _conta, _pedido, _persona, _servico


class CacheImitado:
    """Grava quem começa sem nenhum plano terminado antes; lê quem começa depois de um terminado."""

    def __init__(self) -> None:
        self.terminados = 0
        self.registro: list[str] = []

    async def plano(self, demora: float = 0.05) -> None:
        self.registro.append("read" if self.terminados else "write")
        await asyncio.sleep(demora)
        self.terminados += 1


async def _irmaos(vez: VezDoPlano, cache: CacheImitado, operacao: str | None, espera: float, n: int = 3,
                  falha_no_primeiro: bool = False) -> list[str]:
    papeis: list[str] = []

    async def um(i: int) -> None:
        async with vez.vez(operacao, espera) as papel:
            papeis.append(papel)
            await cache.plano()
            if falha_no_primeiro and i == 0:
                raise RuntimeError("o provedor caiu")

    resultados = await asyncio.gather(*(um(i) for i in range(n)), return_exceptions=True)
    assert sum(isinstance(r, RuntimeError) for r in resultados) == int(falha_no_primeiro)
    return papeis


async def test_a_vez_do_plano() -> None:
    cache = CacheImitado()
    assert await _irmaos(VezDoPlano(), cache, "op-1", 5.0) == ["primeiro", "irmao", "irmao"]
    assert cache.registro == ["write", "read", "read"]
    sem_espera = CacheImitado()
    assert await _irmaos(VezDoPlano(), sem_espera, "op-1", 0) == ["sozinho"] * 3
    assert sem_espera.registro == ["write"] * 3
    fora = CacheImitado()
    assert await _irmaos(VezDoPlano(), fora, None, 5.0) == ["sozinho"] * 3
    caiu = CacheImitado()
    assert await _irmaos(VezDoPlano(), caiu, "op-1", 5.0, falha_no_primeiro=True) == ["primeiro", "irmao", "irmao"]
    assert caiu.registro == ["write", "read", "read"]                   # o erro do 1º solta os irmãos do mesmo jeito


async def test_vencido_o_teto_o_irmao_segue() -> None:
    vez = VezDoPlano()
    solta = asyncio.Event()
    papeis: list[str] = []

    async def primeiro() -> None:
        async with vez.vez("op-1", 0.05) as p:
            papeis.append(p)
            await solta.wait()

    tarefa = asyncio.create_task(primeiro())
    await asyncio.sleep(0)
    async with vez.vez("op-1", 0.05) as p:
        papeis.append(p)
    solta.set()
    await tarefa
    assert papeis == ["primeiro", "irmao_sem_esperar"]


@pytest.mark.parametrize("espera, esperado", [(60.0, ["write", "read", "read"]), (0.0, ["write"] * 3)])
async def test_a_operacao_de_tres_alvos(harness: Harness, espera: float, esperado: list[str]) -> None:
    harness.cfg.file.ai.espera_do_plano_irmao_s = espera
    cache = CacheImitado()
    inner = harness.ai.inner
    plano0 = inner.plan

    async def plan(req: Any) -> Any:
        await cache.plano(0.2)
        return await plano0(req)

    inner.plan = plan
    alvos = []
    for n, aparelho in enumerate(("android-01", "android-02", "android-03"), start=1):
        pid = _persona(harness, f"Pessoa{n}", aparelho)
        _conta(harness, pid, f"qa-user-0{n}", sessao_em=aparelho)
        alvos.append(AlvoPedido(pid))
    op = _servico(harness).criar(_pedido(alvos, chave=f"teste-op-237-{int(espera)}"))
    runs = [a["run_id"] for a in op["alvos"]]
    assert len(runs) == 3 and all(runs)
    for _ in range(200):
        if len(cache.registro) == 3 and cache.terminados == 3:
            break
        await asyncio.sleep(0.05)
    assert cache.registro == esperado

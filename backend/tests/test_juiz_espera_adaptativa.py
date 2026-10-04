"""31.27: a espera do juiz ANTES do primeiro julgamento acompanha a tela (`judge_wait_estavel_s`), com teto no `judge_wait_s`.

Nível de prova: `simulated` — um aparelho falso que devolve assinaturas de árvore programadas; o relógio é o de verdade,
com tempos curtos. Prova-se que a espera:
- sai cedo quando a tela fica igual pelo tempo de estabilidade, e devolve a última leitura (o verificador não relê);
- espera até o teto quando a tela ainda muda, ou quando uma marca pendente do catálogo está na tela;
- volta à espera fixa com `judge_wait_estavel_s = 0`, sem `rt` (a espera entre duas sondagens), ou quando a leitura falha;
- soma o tempo em `attempts.juiz_espera_ms` (31.24, C-4) nos dois modos.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import cast

import pytest

from app.automation.driver import DriverError
from app.devices.manager import DeviceRuntime
from app.taskqueue import executor as executor_mod
from app.taskqueue.executor import StepExecutor
from app.taskqueue.latencia import TemposDaTentativa


@dataclass
class _Arvore:
    assinatura: str
    textos: tuple[str, ...] = ()

    def signature(self) -> str:
        return self.assinatura

    def contains_text(self, texto: str) -> bool:
        return texto in self.textos


@dataclass
class _Aparelho:
    """`observe` devolve a próxima árvore da lista (a última se repete); `falha` levanta na primeira leitura."""
    arvores: list[_Arvore]
    falha: bool = False
    leituras: int = 0

    async def observe(self, rt: object, *, timeout: float, imagem: bool) -> SimpleNamespace:
        assert imagem is False                                  # a espera só lê a árvore
        self.leituras += 1
        if self.falha:
            raise DriverError("leitura falhou")
        return SimpleNamespace(tree=self.arvores[min(self.leituras - 1, len(self.arvores) - 1)])


@dataclass
class _Executor:
    """O mínimo de `StepExecutor` que os dois métodos da espera usam."""
    aparelho: _Aparelho
    teto: float
    estavel: float
    tempos: dict[str, TemposDaTentativa] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cfg = SimpleNamespace(file=SimpleNamespace(ai=SimpleNamespace(judge_wait_s=self.teto,
                                                                         judge_wait_estavel_s=self.estavel)))
        self.devices = self.aparelho

    def _tempos(self, attempt_id: str | None) -> TemposDaTentativa | None:
        return self.tempos.get(attempt_id) if attempt_id else None

    async def _esperar_a_tela_parar(self, *args: object) -> object:
        return await StepExecutor._esperar_a_tela_parar(cast(StepExecutor, self), *args)  # type: ignore[arg-type]


RT = cast(DeviceRuntime, SimpleNamespace(id="android-qa"))


@pytest.fixture(autouse=True)
def passo_curto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_mod, "PASSO_DA_ESPERA_DO_JUIZ_S", 0.02)


def _esperar(ex: _Executor, *, rt: DeviceRuntime | None = RT, marcas: tuple[str, ...] = ()
             ) -> tuple[object, float]:
    inicio = time.monotonic()
    obs = asyncio.run(StepExecutor._esperar_o_juiz(cast(StepExecutor, ex), "a1", rt=rt, call_timeout=1.0,
                                                   marcas=marcas))
    return obs, time.monotonic() - inicio


def test_tela_parada_sai_cedo_e_devolve_a_ultima_leitura() -> None:
    ex = _Executor(_Aparelho([_Arvore("x")]), teto=1.0, estavel=0.1)
    ex.tempos["a1"] = TemposDaTentativa()
    obs, gasto = _esperar(ex)
    assert obs is not None and obs.tree.signature() == "x"     # type: ignore[attr-defined]
    assert 0.1 <= gasto < 0.6                                   # bem antes do teto de 1 s
    assert ex.aparelho.leituras >= 2
    assert 90 <= ex.tempos["a1"].juiz_espera_ms < 600           # C-4: o tempo da espera adaptativa é contado


def test_tela_que_muda_espera_ate_o_teto() -> None:
    ex = _Executor(_Aparelho([_Arvore(str(i)) for i in range(500)]), teto=0.3, estavel=0.1)
    obs, gasto = _esperar(ex)
    assert obs is not None and gasto >= 0.29                    # nunca ficou igual: espera o teto, como antes
    assert gasto < 0.6


def test_marca_pendente_na_tela_nao_conta_como_assentada() -> None:
    ex = _Executor(_Aparelho([_Arvore("x", textos=("Enviando…",))]), teto=0.3, estavel=0.05)
    obs, gasto = _esperar(ex, marcas=("Enviando…",))
    assert obs is not None and gasto >= 0.29                    # "Enviando…" parado espera até o teto


def test_tela_que_assenta_depois_de_mudar_sai_quando_para() -> None:
    ex = _Executor(_Aparelho([_Arvore("a"), _Arvore("b"), _Arvore("c")]), teto=2.0, estavel=0.1)
    obs, gasto = _esperar(ex)
    assert obs is not None and obs.tree.signature() == "c"     # type: ignore[attr-defined]
    assert gasto < 1.0


@pytest.mark.parametrize(("estavel", "rt"), [(0.0, RT), (0.1, None)])
def test_sem_estabilidade_ou_sem_aparelho_e_a_espera_fixa(estavel: float, rt: DeviceRuntime | None) -> None:
    ex = _Executor(_Aparelho([_Arvore("x")]), teto=0.15, estavel=estavel)
    ex.tempos["a1"] = TemposDaTentativa()
    obs, gasto = _esperar(ex, rt=rt)
    assert obs is None and ex.aparelho.leituras == 0 and gasto >= 0.14
    assert ex.tempos["a1"].juiz_espera_ms >= 140


def test_leitura_que_falha_cai_na_espera_fixa() -> None:
    ex = _Executor(_Aparelho([_Arvore("x")], falha=True), teto=0.2, estavel=0.05)
    obs, gasto = _esperar(ex)
    assert obs is None and gasto >= 0.19                        # o verificador lê como sempre depois

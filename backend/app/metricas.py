"""Métricas de desempenho agregadas: contadores e distribuições em memória, gravadas no banco em janelas.

Existe para responder "onde o tempo e o custo são gastos" (evolução de desempenho, frente F1) sem transformar a
medição em gargalo nem em vazamento:

- **Nada por frame no banco.** Captura, codificação e observação acontecem várias vezes por segundo no parque;
  gravar cada uma empurraria o que importa para fora da retenção. Aqui se soma em memória e se grava UMA linha
  agregada por janela (`measurements`, `kind='metricas'`), que a purga de `log_retention_days` já apaga.
- **Cardinalidade limitada.** Rótulo é valor curto de conjunto pequeno (origem, motivo, papel, modelo,
  resultado, worker). Nunca id de execução, texto de tela, conversa, pacote de pessoa ou credencial. Passado o
  teto de séries, a observação vai para a série `_excedente` do mesmo nome, e o descarte é contado — perder a
  divisão é aceitável; perder a contagem, não.
- **Ausência é desconhecido.** Uma distribuição sem amostra não tem p50; quem lê recebe `None`, não zero.
- **Percentil por amostra.** Cada distribuição guarda uma amostra uniforme (reservoir) de tamanho fixo; p50/p95
  saem dela e vêm com o `n` da amostra, para quem lê julgar se a conclusão se sustenta.

Thread-safe: captura e codificação rodam em `asyncio.to_thread`.
"""
from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field
from fractions import Fraction
from datetime import datetime, timezone
from typing import Any

#: Teto de séries (nome + rótulos) por processo. O parque tem dezenas de aparelhos e meia dúzia de papéis;
#: passar disto é sinal de rótulo com valor livre, que é exatamente o que este teto existe para barrar.
MAX_SERIES = 400
#: Tamanho da amostra por distribuição. 256 dá p95 com erro de poucos pontos percentuais — suficiente para
#: comparar antes/depois; não é telemetria de precisão.
AMOSTRA = 256
#: Valor de rótulo mais longo que isto é cortado: rótulo não é lugar de texto.
MAX_ROTULO = 48


def _agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class _Distribuicao:
    n: int = 0
    soma: float = 0.0
    minimo: float | None = None
    maximo: float | None = None
    amostra: list[float] = field(default_factory=list)

    def observar(self, valor: float, rng: random.Random) -> None:
        self.n += 1
        self.soma += valor
        self.minimo = valor if self.minimo is None else min(self.minimo, valor)
        self.maximo = valor if self.maximo is None else max(self.maximo, valor)
        if len(self.amostra) < AMOSTRA:
            self.amostra.append(valor)
        else:  # reservoir: cada observação tem a mesma chance de estar na amostra
            j = rng.randrange(self.n)
            if j < AMOSTRA:
                self.amostra[j] = valor

    def resumo(self) -> dict[str, Any]:
        ordenada = sorted(self.amostra)
        return {"n": self.n, "soma": round(self.soma, 3), "min": self.minimo, "max": self.maximo,
                "media": round(self.soma / self.n, 3) if self.n else None,
                "p50": percentil(ordenada, 50), "p95": percentil(ordenada, 95), "amostra_n": len(ordenada)}


def percentil(ordenada: list[float], p: float) -> float | None:
    """Percentil pelo posto mais próximo, `ceil(p·n/100)`, numa lista JÁ ordenada; `None` sem amostra (desconhecido ≠
    zero). Sem interpolação: o valor é uma amostra que aconteceu.

    K-085: o posto antes era `round(p/100·n + 0,5)`, e o `round` do Python leva o ,5 ao par (arredondamento de banqueiro).
    Quando `p·n/100` dava inteiro ímpar, o posto subia um: 13 de 160 casos com n ≤ 40 (n=2 no p50 dava o maior dos dois).
    O teto vem em aritmética exata (`Fraction`), porque `0.95 * n` em ponto flutuante pode cair logo abaixo do inteiro.
    É o mesmo posto da sombra da decisão fechada (`sombra._p95`) e dos scripts do Jev."""
    if not ordenada:
        return None
    k = max(0, min(len(ordenada) - 1, math.ceil(Fraction(str(p)) * len(ordenada) / 100) - 1))
    return round(ordenada[k], 3)


Chave = tuple[str, tuple[tuple[str, str], ...]]


class _Janela:
    def __init__(self) -> None:
        self.desde = _agora_iso()
        self.contadores: dict[Chave, float] = {}
        self.distribuicoes: dict[Chave, _Distribuicao] = {}

    def vazia(self) -> bool:
        return not self.contadores and not self.distribuicoes

    def exportar(self) -> dict[str, Any]:
        return {
            "desde": self.desde, "ate": _agora_iso(),
            "contadores": [{"nome": n, "rotulos": dict(r), "valor": v}
                           for (n, r), v in sorted(self.contadores.items())],
            "distribuicoes": [{"nome": n, "rotulos": dict(r), **d.resumo()}
                              for (n, r), d in sorted(self.distribuicoes.items(), key=lambda kv: kv[0])],
        }


class Metricas:
    """Registro de métricas do processo: um acumulado desde a partida e uma janela que o gravador esvazia."""

    def __init__(self, *, max_series: int = MAX_SERIES, seed: int | None = None) -> None:
        self._lock = threading.Lock()
        self._max_series = max_series
        self._rng = random.Random(seed)
        self._acumulado = _Janela()
        self._janela = _Janela()
        self._partida_mono = time.monotonic()
        self.descartadas = 0

    # ------------------------------------------------------------------ escrita
    def _chave(self, nome: str, rotulos: dict[str, Any], existentes: dict[Chave, Any], *,
               conta_descarte: bool) -> Chave:
        chave: Chave = (nome, tuple(sorted((str(k), str(v)[:MAX_ROTULO]) for k, v in rotulos.items()
                                           if v is not None)))
        if chave in existentes or len(existentes) < self._max_series:
            return chave
        # Cada observação passa pelo acumulado E pela janela: só o acumulado conta o descarte, senão uma observação
        # fora do teto virava dois descartes (achado da revisão F8).
        if conta_descarte:
            self.descartadas += 1
        return (nome, (("_excedente", "sim"),))

    def contar(self, nome: str, n: float = 1, **rotulos: Any) -> None:
        with self._lock:
            for j in (self._acumulado, self._janela):
                chave = self._chave(nome, rotulos, j.contadores, conta_descarte=j is self._acumulado)
                j.contadores[chave] = j.contadores.get(chave, 0) + n

    def observar(self, nome: str, valor: float, **rotulos: Any) -> None:
        if valor is None:
            return
        with self._lock:
            for j in (self._acumulado, self._janela):
                chave = self._chave(nome, rotulos, j.distribuicoes, conta_descarte=j is self._acumulado)
                j.distribuicoes.setdefault(chave, _Distribuicao()).observar(float(valor), self._rng)

    # ------------------------------------------------------------------ leitura
    def snapshot(self) -> dict[str, Any]:
        """Acumulado desde a partida do processo (não esvazia nada)."""
        with self._lock:
            out = self._acumulado.exportar()
            out["uptime_s"] = round(time.monotonic() - self._partida_mono, 1)
            out["series_descartadas"] = self.descartadas
            return out

    def fechar_janela(self) -> dict[str, Any] | None:
        """Devolve a janela corrente e abre outra. `None` quando nada foi medido — não se grava linha vazia."""
        with self._lock:
            j, self._janela = self._janela, _Janela()
        return None if j.vazia() else j.exportar()

    def valor(self, nome: str, **rotulos: Any) -> float:
        """Contador acumulado de UMA série (0 se nunca contada). Para teste e para a saúde."""
        chave: Chave = (nome, tuple(sorted((str(k), str(v)[:MAX_ROTULO]) for k, v in rotulos.items()
                                           if v is not None)))
        with self._lock:
            return self._acumulado.contadores.get(chave, 0)

    def total(self, nome: str) -> float:
        """Soma de um contador em todas as séries (todos os rótulos)."""
        with self._lock:
            return sum(v for (n, _), v in self._acumulado.contadores.items() if n == nome)


    def limpar(self) -> None:
        """Esvazia tudo NO LUGAR (teste). Não troca o objeto: quem fez `from .metricas import metricas` continua
        apontando para o mesmo registro — trocar a referência global deixaria esses módulos gravando no antigo."""
        with self._lock:
            self._acumulado, self._janela = _Janela(), _Janela()
            self._partida_mono = time.monotonic()
            self.descartadas = 0


#: Registro do processo. Módulos fazem `from ..metricas import metricas` e chamam `metricas.contar(...)`;
#: teste que precisa partir do zero chama `metricas.limpar()`.
metricas = Metricas()

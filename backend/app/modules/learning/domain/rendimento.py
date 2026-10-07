"""Rendimento do ensino: o que UMA sessão de ensino gerou e quanto disso foi usado. Puro: sem banco.

Medido em 06/10 (reposição do ensino): das 275 execuções desde 01/10, 8 foram planejadas por fluxo ensinado, e as 8
eram lote de prova das frentes; 0 uso real. O número era de uma consulta à mão. Aqui ele vira dado por sessão, com a
mesma régua:

* `real`: execução não simulada, sem `prova_fluxo_id` e sem a chave `lote:` das provas das frentes;
* `prova`: a prova de fluxo (30.37) ou o lote de prova de uma frente;
* `simulada`: o provedor ou o aparelho falso.

O que se conta é o que está medido, nada de contrafactual:

* a receita: as tentativas que ela conduziu SEM IA e comprovaram; as que caíram na IA na mesma tentativa
  (`recipe>…`); as outras; o US$ da IA nessas tentativas, dentro da retenção de `ai_calls` (`None` quando a chamada já
  foi apagada); se ela está liberada fora da persona que ensinou (30.81);
* o fluxo: estado, usos, o selo de uso real (31.150) e as execuções que ele planejou;
* as lições que a sessão gerou (31.149, caminho alternativo);
* os pacotes vizinhos (31.152): quantas etapas de planos livres os aceitaram.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from app.contracts.origem import PREFIXO_LOTE

Uso = Literal["real", "prova", "simulada"]


def tipo_de_uso(*, simulated: object, prova_fluxo_id: object, idempotency_key: object) -> Uso:
    """A régua única do "uso real" do ensino (a mesma da medida de 06/10)."""
    if simulated:
        return "simulada"
    if prova_fluxo_id or str(idempotency_key or "").startswith(PREFIXO_LOTE):
        return "prova"
    return "real"


@dataclass
class Contagem:
    real: int = 0
    prova: int = 0
    simulada: int = 0

    def somar(self, uso: Uso) -> None:
        setattr(self, uso, getattr(self, uso) + 1)

    @property
    def total(self) -> int:
        return self.real + self.prova + self.simulada

    def como_dict(self) -> dict[str, int]:
        return {"real": self.real, "prova": self.prova, "simulada": self.simulada}


@dataclass(frozen=True, slots=True)
class TentativaDaReceita:
    recipe_id: int
    uso: Uso
    status: str
    strategy: str
    #: O US$ da IA nesta tentativa, das chamadas ainda em `ai_calls`; `None` quando não há chamada guardada.
    usd: float | None = None


@dataclass
class RendimentoDaReceita:
    id: int
    step_key: str
    app: str
    status: str
    liberada: bool
    sem_ia: Contagem = field(default_factory=Contagem)
    caiu_na_ia: Contagem = field(default_factory=Contagem)
    outras: Contagem = field(default_factory=Contagem)
    usd_da_ia_na_retencao: float = 0.0

    def como_dict(self) -> dict[str, object]:
        return {"id": self.id, "step_key": self.step_key, "app": self.app, "status": self.status,
                "liberada": self.liberada, "sem_ia": self.sem_ia.como_dict(), "caiu_na_ia": self.caiu_na_ia.como_dict(),
                "outras": self.outras.como_dict(), "usd_da_ia_na_retencao": round(self.usd_da_ia_na_retencao, 6)}


def da_receita(r: RendimentoDaReceita, tentativas: Iterable[TentativaDaReceita]) -> RendimentoDaReceita:
    """Soma as tentativas desta receita: sem IA (só `recipe` e comprovou), caiu na IA (`recipe>…`) e o resto."""
    for t in tentativas:
        if t.recipe_id != r.id:
            continue
        if t.strategy == "recipe" and t.status == "succeeded":
            r.sem_ia.somar(t.uso)
        elif t.strategy.startswith("recipe>"):
            r.caiu_na_ia.somar(t.uso)
        else:
            r.outras.somar(t.uso)
        r.usd_da_ia_na_retencao += t.usd or 0.0
    return r


@dataclass(frozen=True, slots=True)
class VizinhoUsado:
    """Um pacote vizinho que a sessão ensinou e as etapas de planos livres que o aceitaram (31.152)."""

    app: str
    pacote: str
    etapas_em_planos_livres: Contagem

    def como_dict(self) -> dict[str, object]:
        return {"app": self.app, "pacote": self.pacote,
                "etapas_em_planos_livres": self.etapas_em_planos_livres.como_dict()}


@dataclass(frozen=True, slots=True)
class RendimentoDoEnsino:
    sessao: str
    fluxo: dict[str, object] | None
    execucoes_do_fluxo: Contagem
    receitas: tuple[RendimentoDaReceita, ...]
    licoes: tuple[dict[str, object], ...]
    vizinhos: tuple[VizinhoUsado, ...]

    def resumo(self) -> dict[str, object]:
        sem_ia = Contagem()
        for r in self.receitas:
            for uso in ("real", "prova", "simulada"):
                setattr(sem_ia, uso, getattr(sem_ia, uso) + getattr(r.sem_ia, uso))
        return {"receitas": len(self.receitas), "receitas_liberadas": sum(1 for r in self.receitas if r.liberada),
                "etapas_sem_ia": sem_ia.como_dict(), "execucoes_do_fluxo": self.execucoes_do_fluxo.como_dict(),
                "licoes": len(self.licoes), "vizinhos": len(self.vizinhos),
                "usado_de_verdade": sem_ia.real > 0 or self.execucoes_do_fluxo.real > 0
                or any(v.etapas_em_planos_livres.real > 0 for v in self.vizinhos)}

    def como_dict(self) -> dict[str, object]:
        return {"sessao": self.sessao, "resumo": self.resumo(), "fluxo": self.fluxo,
                "execucoes_do_fluxo": self.execucoes_do_fluxo.como_dict(),
                "receitas": [r.como_dict() for r in self.receitas], "licoes": list(self.licoes),
                "vizinhos": [v.como_dict() for v in self.vizinhos]}

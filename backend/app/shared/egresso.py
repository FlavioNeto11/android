"""A medição de egresso feita DENTRO da janela do login (31.329).

O egresso do aparelho gira (a sessão sticky do proxy do igfarm girou entre 18:48Z e 19:13Z em 10/10/2026), e uma medição de
minutos antes não prova o IP no instante em que a senha é digitada. Antes de digitar, o motor de sessão pede uma medição
nova; este objeto é o que ela devolve, para o motor decidir (casou ou não) e para o evento e o JSON de prova registrarem.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

#: Medição de antes de digitar mais velha que isto não vale (o prazo da regra; a medição é feita na hora, então a distância
#: normal é de poucos segundos).
JANELA_DO_LOGIN_S = 30.0


@dataclass(frozen=True)
class EgressoNaJanela:
    esperado: str
    medido: str | None
    medido_em: str
    #: Segundos entre o fim da medição e a decisão de digitar (ou de abortar).
    distancia_s: float
    #: `medido == esperado` E a distância dentro de `JANELA_DO_LOGIN_S`.
    casou: bool
    medicao_id: int | None = None
    detalhe: str = ""

    def como_dado(self) -> dict[str, object]:
        return asdict(self)

    def motivo(self) -> str:
        """O texto da recusa, sem segredo (IP público de saída e IP esperado)."""
        if self.medido is None:
            return f"egresso não casou: a medição não obteve IP de saída (esperado {self.esperado})"
        if self.medido != self.esperado:
            return f"egresso não casou: saída {self.medido}, esperado {self.esperado}"
        return (f"egresso não casou: a medição de {self.medido_em} ficou {self.distancia_s:.0f} s antes do login "
                f"(janela de {JANELA_DO_LOGIN_S:.0f} s)")

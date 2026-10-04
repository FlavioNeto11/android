"""Vistas das ocorrências e das observações, lidas pelo relatório (`relatorio.py`) e pela consolidação dos filhos
(`consolidacao.py`). Ficam aqui, e não no relatório, para que o relatório importe a consolidação sem ciclo (28.10 F4).

Puro: stdlib.
"""
from __future__ import annotations

from dataclasses import dataclass

#: O que cada estado de ocorrência diz ao relatório. `concluida` é a única que cobre; as demais terminais NÃO cobrem.
NAO_COBREM = ("perdida", "pulada", "incerta", "falhou", "cancelada")
EM_ABERTO = ("prevista", "devida", "despachada", "rodando")


@dataclass(frozen=True)
class OcorrenciaVista:
    id: str
    previsto_para: str
    estado: str
    motivo: str | None = None
    custo_usd: float = 0.0
    origem: str = "agenda"


@dataclass(frozen=True)
class ObservacaoVista:
    id: str
    ocorrencia_id: str
    alvo: str
    nome: str
    situacao: str                      # observado | incerto | ausente (como foi GRAVADA)
    valor: str | None
    tipo: str = "text"
    fonte: str = ""
    trecho: str | None = None
    sha256: str | None = None
    capturado_em: str = ""

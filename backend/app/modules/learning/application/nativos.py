"""Pacote A5 do ADR-054 (a preencher): o D1 nos conhecimentos nativos — fluxo candidato validado por sombra no
digest, receita com commit parada em `validated` (fila do dono) e o primeiro escritor real de
`skill_validation_results`. As transições passam por `LearningService.mudar_estado(by='sistema')`."""
from __future__ import annotations

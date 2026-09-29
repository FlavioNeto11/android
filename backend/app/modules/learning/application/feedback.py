"""Pacote A4 do ADR-054 (a preencher): o botão do D2 ("deu certo / deu errado + motivo") e os efeitos do voto —
rebaixar o que o item usou e o que aprendeu, com trilha, veto e reativação em um clique. A nota passa por
`LearningService.registrar_sinal(recusar_nota=True)` (409 quando parece credencial)."""
from __future__ import annotations

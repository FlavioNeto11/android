"""Quem decide PUBLICAR o aviso do ensinado rebaixado (30.80 parte B): compara o item antes e depois de uma mudança de
status, nos dois caminhos por onde o sistema tira uma receita ou um fluxo de uso — a loja (quarentena por falhas
seguidas, substituição; `LearningService.avisar_mudanca_nativa`) e o Livro (a obsolescência e todo
`mudar_estado(by='sistema')`; `LearningService._mover_nativo`). A regra e o payload moram em `domain/ensinado.py`.

Chamado DEPOIS da trilha, na mesma transação: `desde` é o instante gravado nela, estável se o evento for reemitido.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from app.modules.learning.application.ports import LeitorDoEnsinado, PortaDoEnsinado
from app.modules.learning.domain.ensinado import AvisoDoEnsinado, rebaixado_pelo_sistema
from app.modules.learning.domain.livro import EntradaDoLivro
from app.util import to_iso

log = logging.getLogger(__name__)


class AvisadorDoEnsinado:
    def __init__(self, porta: PortaDoEnsinado | None, leitor: LeitorDoEnsinado | None,
                 relogio: Callable[[], datetime]) -> None:
        """Sem a porta ou sem o leitor, nada é publicado (o central liga os dois; os testes do Livro, nenhum)."""
        self._porta = porta
        self._leitor = leitor
        self._relogio = relogio

    def mudou(self, antes: EntradaDoLivro | None, depois: EntradaDoLivro, *, por_sistema: bool) -> None:
        """PROPAGA a falha da porta e do leitor: a loja chama isto dentro do `savepoint` dela (PostgreSQL)."""
        if self._porta is None or self._leitor is None:
            return
        if not rebaixado_pelo_sistema(antes, depois, por_sistema=por_sistema):
            return
        treino = self._leitor.sessao_de_treino(depois.kind, depois.ref)
        if treino is None:                  # a origem diz treino, mas a fonte não tem a sessão: nada a citar
            return
        desde = self._leitor.instante_da_transicao(depois.kind, depois.ref) or to_iso(self._relogio())
        self._porta.ensinado_rebaixado(AvisoDoEnsinado(
            kind=depois.kind.value, ref=depois.ref, app=depois.app or "", treino=treino,
            sem_receita_ativa=not self._leitor.tem_ativo_no_lugar(depois.kind, depois.ref),
            para=depois.native_status or "", desde=desde))

    def mudou_sem_falhar(self, antes: EntradaDoLivro | None, depois: EntradaDoLivro, *, por_sistema: bool) -> None:
        try:
            self.mudou(antes, depois, por_sistema=por_sistema)
        except Exception:  # noqa: BLE001 - o aviso informa; a transição já foi gravada e não cai por causa dele
            log.exception("aprendizado: aviso do ensinado de %s %s", depois.kind.value, depois.ref)


__all__ = ["AvisadorDoEnsinado"]

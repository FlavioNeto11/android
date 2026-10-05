"""Quem decide PUBLICAR o aviso do ensinado rebaixado (30.80 parte B): compara o item antes e depois de uma mudança de
status, nos dois caminhos por onde o sistema tira uma receita ou um fluxo de uso — a loja (quarentena por falhas
seguidas, substituição; `LearningService.avisar_mudanca_nativa`) e o Livro (a obsolescência e todo
`mudar_estado(by='sistema')`; `LearningService._mover_nativo`). A regra e o payload moram em `domain/ensinado.py`.

Chamado DEPOIS da trilha: `desde` é o instante gravado nela, estável se o evento for reemitido. Na loja, dentro da
transação dela (num savepoint próprio, `mudou_isolado`: a falha do aviso não desfaz a trilha). No Livro, depois do
commit de `transicionar_nativo` (salvo transação externa), e por isso `mudou_sem_falhar` é seguro ali.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime

from app.modules.learning.application.ports import LeitorDoEnsinado, PortaDoEnsinado
from app.modules.learning.domain.ensinado import (AvisoDoEnsinado, DecisaoDoEnsinado, EsperaDoEnsinado,
                                                  rebaixado_pelo_sistema)
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.vocabulario import LivroKind, Origem
from app.util import to_iso

log = logging.getLogger(__name__)


class AvisadorDoEnsinado:
    def __init__(self, porta: PortaDoEnsinado | None, leitor: LeitorDoEnsinado | None,
                 relogio: Callable[[], datetime],
                 isolar: Callable[[], AbstractContextManager[object]] | None = None) -> None:
        """Sem a porta ou sem o leitor, nada é publicado (o central liga os dois; os testes do Livro, nenhum).
        `isolar`: o savepoint do banco (`db.savepoint`), só em volta do aviso que a loja pede."""
        self._porta = porta
        self._leitor = leitor
        self._relogio = relogio
        self._isolar = isolar

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

    def mudou_isolado(self, antes: EntradaDoLivro | None, depois: EntradaDoLivro, *, por_sistema: bool) -> None:
        """O caminho da loja (N1 da leitura do 30.80 B): o aviso num savepoint PRÓPRIO, aninhado no da loja. A falha das
        leituras ou do `emit` desfaz só ele, e a trilha da transição e o aviso do 30.21 ficam. Sem `isolar`, propaga
        (no PostgreSQL, engolir sem savepoint deixaria a transação da loja abortada)."""
        if self._isolar is None:
            self.mudou(antes, depois, por_sistema=por_sistema)
            return
        try:
            with self._isolar():
                self.mudou(antes, depois, por_sistema=por_sistema)
        except Exception:  # noqa: BLE001 - o savepoint já desfez o aviso; a transição e a trilha ficam
            log.exception("aprendizado: aviso do ensinado de %s", _no_log(depois))

    def mudou_sem_falhar(self, antes: EntradaDoLivro | None, depois: EntradaDoLivro, *, por_sistema: bool) -> None:
        try:
            self.mudou(antes, depois, por_sistema=por_sistema)
        except Exception:  # noqa: BLE001 - o aviso informa; a transição já foi gravada e não cai por causa dele
            log.exception("aprendizado: aviso do ensinado de %s", _no_log(depois))

    # ------------------------------------------------------------------ 30.81: o ensinado que espera a pessoa
    def espera_decisao(self, aviso: EsperaDoEnsinado) -> None:
        """Publica `learning.ensinado_espera_decisao`. PROPAGA a falha da porta (quem chama decide)."""
        if self._porta is not None:
            self._porta.ensinado_espera_decisao(aviso)

    def espera_da_pessoa(self, e: EntradaDoLivro) -> str | None:
        """O `desde` do fluxo ensinado que espera a decisão de uma pessoa, ou `None` (lido antes da decisão)."""
        if self._leitor is None or e.kind is not LivroKind.FLUXO or e.origin is not Origem.TREINO:
            return None
        try:
            return self._leitor.espera_da_pessoa(e.kind, e.ref)
        except Exception:  # noqa: BLE001 - sem a leitura, a decisão segue; só o evento dela não sai
            log.exception("aprendizado: espera do ensinado de %s", _no_log(e))
            return None

    def motivo_da_espera(self, e: EntradaDoLivro) -> str | None:
        """O motivo do ensinado que espera a pessoa, para o Livro; só lê no fluxo ensinado ativo."""
        if (self._leitor is None or e.kind is not LivroKind.FLUXO or e.origin is not Origem.TREINO
                or e.native_status != "active"):
            return None
        return self._leitor.motivo_da_espera(e.kind, e.ref)

    def decidiu_sem_falhar(self, e: EntradaDoLivro, desde: str, decisao: str) -> None:
        """Publica `learning.ensinado_decidido` depois da linha da pessoa na trilha; a decisão não cai pelo aviso."""
        if self._porta is None or self._leitor is None:
            return
        try:
            em = self._leitor.instante_da_transicao(e.kind, e.ref) or to_iso(self._relogio())
            self._porta.ensinado_decidido(DecisaoDoEnsinado(kind=e.kind.value, ref=e.ref, desde=desde,
                                                            decisao=decisao, decidido_em=em))
        except Exception:  # noqa: BLE001 - o aviso informa; a decisão já foi gravada
            log.exception("aprendizado: aviso da decisão do ensinado de %s", _no_log(e))


def _no_log(e: EntradaDoLivro) -> str:
    """O id do fluxo não vai ao backend.log (o slug do resumo literal pode trazer nome; leitura do 28.50); o da receita
    é só dígitos."""
    return e.kind.value if e.kind is LivroKind.FLUXO else f"{e.kind.value} {e.ref}"


__all__ = ["AvisadorDoEnsinado"]

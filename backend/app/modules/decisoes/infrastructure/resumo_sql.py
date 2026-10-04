"""O resumo agrupado das decisões automáticas (item 28.25): no máximo UMA mensagem por janela, só com decisão nova.

Quem chama é o líder da trava `avisos` (uma réplica só). Duas cercas contra a duplicata, mesmo assim:
- a chave do aviso é o intervalo de ids que ele cobre, e a fila de avisos é `UNIQUE (chave)`: repetir o mesmo resumo
  (queda entre enfileirar e marcar) é a mesma linha;
- `ultimo_resumo_em` (em `decisoes_automaticas_estado`) segura a janela através de reinício.

Decisão já desfeita pelo dono antes do resumo não conta (ele sabe), e decisão mais velha que `avisos.validade_h` é
notícia velha: sai da fila do resumo sem mensagem, como a fila de avisos faz com o pendente que venceu.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from app.db import Database, loads
from app.modules.avisos.domain.mensagem import Aviso, link_da_caixa, titulo_do_aviso
from app.modules.decisoes.domain.resumo import (CONSULTA_DA_ABA, TIPO_DO_AVISO, TITULO, DecisaoParaResumir,
                                                chave_do_resumo, corpo_do_resumo, horas_dos_fatos)
from app.modules.decisoes.infrastructure.estado_sql import EstadoDasDecisoes
from app.util import parse_iso, to_iso

log = logging.getLogger("poc.decisoes")

ULTIMO_RESUMO = "ultimo_resumo_em"
LIMITE_POR_RESUMO = 1000


class ResumoDasDecisoes:
    def __init__(self, db: Database, estado: EstadoDasDecisoes, *, enfileirar: Callable[[Aviso], bool],
                 pode_avisar: Callable[[], bool], relogio: Callable[[], datetime] | None = None,
                 redigir: Callable[[str], str] | None = None):
        self.db = db
        self.estado = estado
        self._enfileirar = enfileirar
        self._pode_avisar = pode_avisar
        self.relogio: Callable[[], datetime] = relogio if relogio is not None else db.agora
        self._redigir = redigir

    def resumir(self, *, janela_min: float, desfazer_dias: float, validade_h: float,
                url_painel: str | None) -> Aviso | None:
        """Uma volta: devolve o aviso enfileirado, ou `None` (fora da janela, sem decisão nova, canal indisponível)."""
        if not self._pode_avisar():
            return None                                   # nada é marcado: o que ficou continua esperando o canal
        agora = self.relogio()
        ultimo = parse_iso(self.estado.texto(ULTIMO_RESUMO))
        if ultimo is not None and agora - ultimo < timedelta(minutes=janela_min):
            return None
        linhas = self.db.query(
            "SELECT id, fila, regra, fatos, decidida_em, desfeita_em FROM decisoes_automaticas"
            " WHERE resumida_em IS NULL ORDER BY id LIMIT ?", (LIMITE_POR_RESUMO,))
        if not linhas:
            return None
        corte = to_iso(agora - timedelta(hours=validade_h))
        frescas = [r for r in linhas if str(r["decidida_em"]) >= corte]
        contaveis = [DecisaoParaResumir(int(r["id"]), str(r["fila"]), str(r["regra"]),
                                        horas_dos_fatos(loads(r["fatos"], {}) or {}))
                     for r in frescas if r["desfeita_em"] is None]
        primeiro, ultimo_id = int(linhas[0]["id"]), int(linhas[-1]["id"])
        corpo = corpo_do_resumo(contaveis, desfazer_dias)
        aviso: Aviso | None = None
        if corpo is not None:
            if self._redigir is not None:
                corpo = self._redigir(corpo)
            base = link_da_caixa(url_painel)
            chave = chave_do_resumo(int(frescas[0]["id"]) if frescas else primeiro, ultimo_id)
            aviso = Aviso(chave=chave, tipo=TIPO_DO_AVISO, titulo=titulo_do_aviso(TITULO), corpo=corpo,
                          link=base + CONSULTA_DA_ABA if base else None)
            self._enfileirar(aviso)
            # `False` pode ser duplicata (já estava na fila) ou erro de gravação: só a linha na fila prova que o resumo
            # existe, e sem ela NADA é marcado (a próxima volta tenta de novo).
            if self.db.one("SELECT 1 FROM avisos_entregas WHERE chave=?", (chave,)) is None:
                log.warning("decisoes: o resumo %s não entrou na fila de avisos; tento de novo na próxima volta", chave)
                return None
        quando = to_iso(agora)
        self.db.execute("UPDATE decisoes_automaticas SET resumida_em=? WHERE id >= ? AND id <= ? AND resumida_em IS NULL",
                        (quando, primeiro, ultimo_id))
        if aviso is not None:
            self.estado.gravar(ULTIMO_RESUMO, quando)
        return aviso

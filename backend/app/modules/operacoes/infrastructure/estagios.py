"""A marca de um estágio que a execução não grava sozinha (31.154): quem sabe que ele aconteceu chama `registrar_estagio`.

É por aqui que a porta do rascunho (frente de aprendizado) diz "o conteúdo foi lido" e "o conhecimento da operação entrou
no texto". Sem operação, não faz nada: a execução avulsa segue como sempre. Nome e assinatura são contrato com a outra
frente (combinado em 06/10): não mudam sem aviso.
"""
from __future__ import annotations

import json
import logging

from app.db import OPERATIONAL_ERRORS, Database
from app.modules.operacoes.domain.estagios import ESTAGIOS_MARCAVEIS
from app.util import now_iso

log = logging.getLogger("poc")


def operacao_da_execucao(db: Database, run_id: str) -> str | None:
    """A operação desta execução, ou `None` (execução avulsa, ou banco sem a migração 124)."""
    try:
        row = db.one("SELECT operacao_id FROM runs WHERE id=?", (run_id,))
    except OPERATIONAL_ERRORS:
        # Só a coluna ausente num banco antigo vira "sem operação". Outro erro (transação abortada, conexão caída)
        # propaga: engoli-lo apagaria em silêncio a marca de estágio de um alvo que É de operação.
        return None
    return str(row["operacao_id"]) if row is not None and row["operacao_id"] else None


def registrar_estagio(db: Database, run_id: str, estagio: str) -> bool:
    """Marca `estagio` no alvo da operação desta execução, na primeira vez (a hora da primeira prova fica). Devolve se
    marcou. Estágio fora dos marcáveis é recusado em silêncio no log: quem chama não deve derrubar a etapa por isto."""
    if estagio not in ESTAGIOS_MARCAVEIS:
        log.warning("registrar_estagio: %r não é marcável de fora (aceitos: %s)", estagio, sorted(ESTAGIOS_MARCAVEIS))
        return False
    if operacao_da_execucao(db, run_id) is None:
        return False
    row = db.one("SELECT operacao_id, profile_id, marcas FROM operacao_alvos WHERE run_id=?", (run_id,))
    if row is None:
        return False
    marcas = json.loads(row["marcas"] or "{}")
    if estagio in marcas:
        return False
    marcas[estagio] = now_iso()
    db.execute("UPDATE operacao_alvos SET marcas=?, updated_at=? WHERE operacao_id=? AND profile_id=?",
               (json.dumps(marcas, ensure_ascii=False), marcas[estagio], row["operacao_id"], row["profile_id"]))
    return True

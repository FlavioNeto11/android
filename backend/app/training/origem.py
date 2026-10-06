"""31.111 F1: de qual etapa que falhou nasce uma sessão de ensino.

Só LÊ `steps` e `attempts` (nada se copia): a sessão guarda três ids opacos (migração 119) e o motivo literal do executor
é lido de volta da etapa quando a sessão é exibida. Não decide nada sobre o aparelho nem sobre a conta: as travas da
gravação (controle da pessoa, aparelho que não é a loja) seguem em `TrainingRecorder.start`.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..db import Database



class OrigemRecusada(Exception):
    """A etapa não serve de origem. Mesma forma de `TrainingError` (código, mensagem e status HTTP): o recorder a converte,
    e este módulo não importa o recorder (o recorder importa este)."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


#: Etapa que "não deu certo": falhou, ou ficou incerta (incerteza nunca conta como sucesso, e a pessoa pode corrigi-la).
STATUS_ENSINAVEIS = ("failed", "uncertain")
MAXIMO_DO_MOTIVO = 400


@dataclass(frozen=True)
class OrigemDaFalha:
    run_id: str
    step_id: str
    step_key: str
    attempt_id: str | None
    instance_id: str
    titulo: str
    motivo: str


def origem_da_falha(db: Database, run_id: str, step_id: str) -> OrigemDaFalha:
    etapa = db.one("SELECT id, run_id, key, title, instance_id, status, status_detail FROM steps WHERE id=? AND run_id=?",
                   (step_id, run_id))
    if etapa is None:
        raise OrigemRecusada("step_not_found", "Etapa não encontrada nesta execução.", 404)
    if etapa["status"] not in STATUS_ENSINAVEIS:
        raise OrigemRecusada("step_not_failed", "Só se ensina a partir de uma etapa que falhou ou ficou incerta.", 409)
    tentativa = db.one("SELECT id, error FROM attempts WHERE step_id=? ORDER BY number DESC LIMIT 1", (step_id,))
    motivo = str(etapa["status_detail"] or (tentativa["error"] if tentativa else "") or "")
    return OrigemDaFalha(run_id=run_id, step_id=step_id, step_key=str(etapa["key"]),
                         attempt_id=str(tentativa["id"]) if tentativa else None, instance_id=str(etapa["instance_id"]),
                         titulo=str(etapa["title"]), motivo=motivo[:MAXIMO_DO_MOTIVO])


def origin_da_linha(db: Database, linha: dict[str, object]) -> dict[str, object] | None:
    """Tira as três colunas da linha da sessão e devolve `origin` (ou `None` se a sessão não veio de uma falha). A chave e o
    motivo são lidos da etapa AGORA: se a limpeza a apagou, ficam `None` e os ids seguem como rótulo."""
    run_id = linha.pop("origin_run_id", None)
    step_id = linha.pop("origin_step_id", None)
    attempt_id = linha.pop("origin_attempt_id", None)
    if not run_id:
        return None
    etapa = db.one("SELECT key, status_detail FROM steps WHERE id=?", (step_id,)) if step_id else None
    motivo = (str(etapa["status_detail"])[:MAXIMO_DO_MOTIVO] if etapa and etapa["status_detail"] else None)
    return {"run_id": run_id, "step_id": step_id, "step_key": str(etapa["key"]) if etapa else None,
            "attempt_id": attempt_id, "motivo": motivo}

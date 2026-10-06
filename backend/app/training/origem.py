"""31.111 F1: de qual etapa que falhou nasce uma sessão de ensino.

Só LÊ `steps` e `attempts` (nada se copia): a sessão guarda três ids opacos (migração 119) e o motivo literal do executor
é lido de volta da etapa quando a sessão é exibida. Não decide nada sobre o aparelho nem sobre a conta: as travas da
gravação (controle da pessoa, aparelho que não é a loja) seguem em `TrainingRecorder.start`.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..db import Database, loads
from ..security.redaction import redact



class OrigemRecusada(Exception):
    """A etapa não serve de origem. Mesma forma de `TrainingError` (código, mensagem e status HTTP): o recorder a converte,
    e este módulo não importa o recorder (o recorder importa este)."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


#: Etapa que "não deu certo": falhou, ficou incerta (incerteza nunca conta como sucesso) ou PAROU num bloqueio esperando uma
#: pessoa (`waiting_user`: a IA concluiu que não dá e o executor não aceitou; a prova real do 31.111, 06/10, caiu aqui). A
#: pessoa pode corrigir qualquer das três.
STATUS_ENSINAVEIS = ("failed", "uncertain", "waiting_user")
MAXIMO_DO_MOTIVO = 400
MAXIMO_DA_TRILHA = 60
MAXIMO_DE_EVIDENCIAS = 10


@dataclass(frozen=True)
class OrigemDaFalha:
    run_id: str
    step_id: str
    step_key: str
    attempt_id: str | None
    instance_id: str
    titulo: str
    motivo: str
    #: O estado da etapa (um de `STATUS_ENSINAVEIS`): a sugestão do ensino pergunta diferente quando ela só parou esperando
    #: a pessoa (31.116, adendo v1.82).
    status: str = ""


def _limpo(texto: object, limite: int = MAXIMO_DO_MOTIVO) -> str | None:
    """Texto do executor ou da etapa que vai para a tela: sem segredo reconhecível e com tamanho limitado."""
    if texto is None or texto == "":
        return None
    return str(redact(str(texto)))[:limite]


def origem_da_falha(db: Database, run_id: str, step_id: str) -> OrigemDaFalha:
    etapa = db.one("SELECT id, run_id, key, title, instance_id, status, status_detail FROM steps WHERE id=? AND run_id=?",
                   (step_id, run_id))
    if etapa is None:
        raise OrigemRecusada("step_not_found", "Etapa não encontrada nesta execução.", 404)
    if etapa["status"] not in STATUS_ENSINAVEIS:
        raise OrigemRecusada("step_not_failed", "Só se ensina a partir de uma etapa que falhou, ficou incerta ou parou esperando uma pessoa.", 409)
    tentativa = db.one("SELECT id, error FROM attempts WHERE step_id=? ORDER BY number DESC LIMIT 1", (step_id,))
    motivo = _limpo(etapa["status_detail"] or (tentativa["error"] if tentativa else "")) or ""
    return OrigemDaFalha(run_id=run_id, step_id=step_id, step_key=str(etapa["key"]),
                         attempt_id=str(tentativa["id"]) if tentativa else None, instance_id=str(etapa["instance_id"]),
                         titulo=str(etapa["title"]), motivo=motivo, status=str(etapa["status"]))


def origin_da_linha(db: Database, linha: dict[str, object]) -> dict[str, object] | None:
    """Tira as três colunas da linha da sessão e devolve `origin` (ou `None` se a sessão não veio de uma falha). A chave e o
    motivo são lidos da etapa AGORA: se a limpeza a apagou, ficam `None` e os ids seguem como rótulo."""
    run_id = linha.pop("origin_run_id", None)
    step_id = linha.pop("origin_step_id", None)
    attempt_id = linha.pop("origin_attempt_id", None)
    if not run_id:
        return None
    etapa = db.one("SELECT key, status_detail FROM steps WHERE id=?", (step_id,)) if step_id else None
    motivo = _limpo(etapa["status_detail"]) if etapa else None
    return {"run_id": run_id, "step_id": step_id, "step_key": str(etapa["key"]) if etapa else None,
            "attempt_id": attempt_id, "motivo": motivo}


def contexto_da_falha(db: Database, run_id: str, step_id: str, attempt_id: str | None) -> dict[str, object]:
    """31.111 F2: o que a pessoa precisa ver para corrigir: a trilha da execução naquele aparelho, o que a etapa esperava
    (a pós-condição), a tentativa que falhou e as evidências dela. SÓ LEITURA, sem IA, sem copiar nada: o que mudou ou foi
    apagado depois some daqui (a limpeza de execuções velhas), e `disponivel` fica falso. A tela em si não vai no JSON:
    cada evidência traz o `id` que `GET /api/evidence/{id}` serve (e recusa quando a imagem foi redigida)."""
    etapa = db.one("SELECT instance_id, plan_version, postcondition FROM steps WHERE id=? AND run_id=?", (step_id, run_id))
    if etapa is None:
        return {"disponivel": False}
    trilha = [{"step_id": r["id"], "step_key": r["key"], "titulo": _limpo(r["title"], 200), "status": r["status"],
               "motivo": _limpo(r["status_detail"]) if r["status"] in STATUS_ENSINAVEIS else None,
               "falhou": r["id"] == step_id}
              for r in db.query("SELECT id, key, title, status, status_detail FROM steps WHERE run_id=? AND instance_id=?"
                                " AND plan_version=? ORDER BY seq LIMIT ?",
                                (run_id, etapa["instance_id"], etapa["plan_version"], MAXIMO_DA_TRILHA))]
    pos = loads(etapa["postcondition"], {})
    esperado = {k: _limpo(pos.get(k), 300) for k in ("kind", "value", "description")} if isinstance(pos, dict) else None
    tentativa = db.one("SELECT number, status, error, failure_kind, failure_screen, strategy FROM attempts WHERE id=?",
                       (attempt_id,)) if attempt_id else None
    evidencias = [{"id": r["id"], "kind": r["kind"], "nota": _limpo(r["note"], 200),
                   "disponivel": bool(r["path"]) and not r["redacted"]}
                  for r in db.query("SELECT id, kind, note, path, redacted FROM evidence WHERE attempt_id=? ORDER BY id LIMIT ?",
                                    (attempt_id, MAXIMO_DE_EVIDENCIAS))] if attempt_id else []
    return {"disponivel": True, "trilha": trilha, "esperado": esperado,
            "tentativa": ({"number": tentativa["number"], "status": tentativa["status"], "erro": _limpo(tentativa["error"]),
                           "failure_kind": tentativa["failure_kind"], "failure_screen": _limpo(tentativa["failure_screen"], 200),
                           "strategy": tentativa["strategy"]} if tentativa else None),
            "evidencias": evidencias}

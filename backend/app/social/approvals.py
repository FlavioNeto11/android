"""Aprovação humana de ações com efeito externo.

O §17 pede três verbos — **aprovar**, **editar** e **rejeitar** — e eles existem porque "confirmar concluído" não
serve para isso: aquele verbo marca a etapa como feita SEM executar, o que faria o relatório mentir. Aqui a decisão
da pessoa muda o que vai acontecer, e não o que já aconteceu:

* **aprovar** libera a etapa exatamente como está;
* **editar** troca o conteúdo que será digitado (o original fica guardado ao lado, para auditoria);
* **rejeitar** cancela só as etapas daquele alvo; o resto do objetivo segue.

A aprovação é criada ANTES de qualquer digitação, e é por isso que ela cabe na porta de despacho: uma etapa que já
digitou não teria como voltar atrás — `succeeded` é estado terminal na máquina de etapas.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..db import Database, dumps, loads
from ..util import new_token, now_iso

STATUSES = ("pending", "approved", "edited", "rejected", "expired")


@dataclass(slots=True)
class Approval:
    id: str
    profile_id: str | None
    run_id: str | None
    objective_id: str | None
    step_id: str | None
    capability: str
    target: str | None
    summary: str
    generated_content: str | None
    approved_content: str | None
    status: str
    created_at: str
    decided_at: str | None = None
    decided_note: str | None = None
    interaction_id: str | None = None                        # preenchido no commit: o efeito que esta decisão liberou

    @property
    def content(self) -> str | None:
        """O que de fato vai ser digitado: o texto editado, quando houve edição."""
        return self.approved_content if self.status == "edited" and self.approved_content else self.generated_content

    def to_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in
             ("id", "profile_id", "run_id", "objective_id", "step_id", "capability", "target", "summary",
              "generated_content", "approved_content", "status", "created_at", "decided_at", "decided_note",
              "interaction_id")}
        d["content"] = self.content
        return d


class ApprovalStore:
    def __init__(self, db: Database):
        self.db = db

    def _dto(self, row: Any) -> Approval:
        return Approval(id=row["id"], profile_id=row["profile_id"], run_id=row["run_id"],
                        objective_id=row["objective_id"], step_id=row["step_id"], capability=row["capability"],
                        target=row["target"], summary=row["summary"], generated_content=row["generated_content"],
                        approved_content=row["approved_content"], status=row["status"], created_at=row["created_at"],
                        decided_at=row["decided_at"], decided_note=row["decided_note"],
                        interaction_id=row["interaction_id"])

    def get(self, approval_id: str) -> Approval | None:
        row = self.db.one("SELECT * FROM pending_approvals WHERE id=?", (approval_id,))
        return self._dto(row) if row else None

    def for_step(self, step_id: str) -> Approval | None:
        row = self.db.one("SELECT * FROM pending_approvals WHERE step_id=? ORDER BY created_at DESC LIMIT 1",
                          (step_id,))
        return self._dto(row) if row else None

    def list(self, *, status: str | None = "pending", profile_id: str | None = None, run_id: str | None = None,
             limit: int = 50) -> list[Approval]:
        onde, args = ["1=1"], []
        if status:
            onde.append("status=?")
            args.append(status)
        if profile_id:
            onde.append("profile_id=?")
            args.append(profile_id)
        if run_id:
            # Por EXECUÇÃO: é assim que se lê os N textos de uma tacada, um por perfil, em vez de caçar perfil a
            # perfil. Ordem crescente aqui — a lista de uma execução se lê na ordem em que os aparelhos entraram.
            onde.append("run_id=?")
            args.append(run_id)
        args.append(limit)
        ordem = "ASC" if run_id else "DESC"
        return [self._dto(r) for r in self.db.query(
            f"SELECT * FROM pending_approvals WHERE {' AND '.join(onde)} ORDER BY created_at {ordem} LIMIT ?",
            tuple(args))]

    def open(self, *, profile_id: str | None, capability: str, summary: str, target: str | None = None,
             content: str | None = None, run_id: str | None = None, objective_id: str | None = None,
             step_id: str | None = None, interaction_id: str | None = None) -> Approval:
        approval_id = f"apr-{new_token()}"
        self.db.execute(
            "INSERT INTO pending_approvals(id, profile_id, run_id, objective_id, step_id, interaction_id, capability,"
            " target, summary, generated_content, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?)",
            (approval_id, profile_id, run_id, objective_id, step_id, interaction_id, capability, target, summary,
             content, now_iso()))
        row = self.db.one("SELECT * FROM pending_approvals WHERE id=?", (approval_id,))
        return self._dto(row)

    def decide(self, approval_id: str, *, status: str, content: str | None = None,
               note: str | None = None) -> Approval | None:
        """Decisão é definitiva: só uma aprovação `pending` pode ser decidida, e só uma vez."""
        with self.db.tx():
            row = self.db.one("SELECT * FROM pending_approvals WHERE id=? AND status='pending'", (approval_id,))
            if row is None:
                return None
            self.db.execute(
                "UPDATE pending_approvals SET status=?, approved_content=COALESCE(?, approved_content),"
                " decided_at=?, decided_note=? WHERE id=?",
                (status, content, now_iso(), note, approval_id))
        return self.get(approval_id)

    def link_interaction(self, step_id: str, interaction_id: str) -> None:
        """Fecha o rastro rascunho → aprovação → efeito.

        A aprovação nasce ANTES de existir interação (é o que impede o envio), então a coluna só pode ser
        preenchida no instante do commit, quando o efeito é aberto. Sem isso não há como sair de um texto
        aprovado e chegar ao que de fato foi publicado — nem o contrário.
        """
        self.db.execute("UPDATE pending_approvals SET interaction_id=? WHERE step_id=? AND interaction_id IS NULL",
                        (interaction_id, step_id))

    def expire_for_objective(self, objective_id: str, *, reason: str) -> int:
        cur = self.db.execute(
            "UPDATE pending_approvals SET status='expired', decided_at=?, decided_note=? WHERE objective_id=?"
            " AND status='pending'", (now_iso(), reason, objective_id))
        return int(cur.rowcount or 0)


def definir_texto(db: Database, step_id: str, texto: str) -> None:
    """Grava o texto desta etapa: argumento, guarda de commit e parâmetros do objetivo passam a falar dele.

    Usado por quem ESCREVE o rascunho (a geração com a persona do perfil) e por quem o EDITA na aprovação — os
    dois precisam exatamente do mesmo efeito, e duplicar isso deixaria a guarda falando de um texto e o argumento
    de outro.

    A guarda é o que impede o efeito de acontecer com outra coisa na tela. Enquanto o texto não existia ela ficava
    de fora do plano, então aqui ela pode precisar ser CRIADA, não apenas trocada.
    """
    row = db.one("SELECT objective_id, bindings, commit_guard FROM steps WHERE id=?", (step_id,))
    if row is None:
        return
    bindings = loads(row["bindings"], {}) or {}
    antigo = (bindings.get("content") or "").strip()
    bindings["content"] = texto
    guardas = [g for g in (loads(row["commit_guard"], []) or []) if not antigo or g != antigo]
    if texto not in guardas:
        guardas.append(texto)
    db.execute("UPDATE steps SET bindings=?, commit_guard=? WHERE id=?",
               (dumps(bindings), dumps(guardas), step_id))
    # O ator também lê "Parâmetros já resolvidos". Deixar o texto ANTIGO ali fazia o modelo ver uma coisa no
    # parâmetro e outra na guarda — e só a guarda é imposta.
    if antigo and antigo != texto and row["objective_id"]:
        obj = db.one("SELECT parameters FROM objectives WHERE id=?", (row["objective_id"],))
        if obj is not None:
            params = loads(obj["parameters"], {}) or {}
            novos = {k: (texto if v == antigo else v) for k, v in params.items()}
            if novos != params:
                db.execute("UPDATE objectives SET parameters=? WHERE id=?", (dumps(novos), row["objective_id"]))


def apply_edit(db: Database, step_id: str, novo_conteudo: str) -> None:
    """Troca o conteúdo aprovado na etapa.

    Só o conteúdo muda. O título e o objetivo da etapa dizem "o conteúdo aprovado" justamente para continuarem
    verdadeiros depois de uma edição — foi assim que o catálogo foi escrito.
    """
    definir_texto(db, step_id, novo_conteudo)


class ApprovalService:
    """Os três verbos, e o que cada um faz com a fila.

    Aprovar e editar devolvem o item à fila; rejeitar cancela só as etapas daquele alvo. Em nenhum caso a etapa é
    marcada como concluída: o que a pessoa decide é o que VAI acontecer, não o que aconteceu.
    """

    def __init__(self, store: ApprovalStore, repo: Any, scheduler: Any = None):
        self.store = store
        self.repo = repo
        self.scheduler = scheduler

    def list(self, *, status: str | None = "pending", profile_id: str | None = None, run_id: str | None = None,
             limit: int = 50) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self.store.list(status=status, profile_id=profile_id, run_id=run_id,
                                                     limit=limit)]

    def decide_many(self, decisoes: list[Any]) -> dict[str, Any]:
        """Decide várias de uma vez — é como se lê uma execução: os N textos juntos, um por perfil.

        Cada decisão é independente e definitiva: uma que falhe (já decidida, texto vazio) não desfaz nem impede
        as outras. A resposta diz, item a item, o que aconteceu — aprovar em lote não pode virar "deu ruim em
        alguma, descubra qual".
        """
        from .service import SocialError

        decididas, recusadas = [], []
        for d in decisoes:
            try:
                decididas.append(self.decide(d.id, d.verb, content=d.content, note=d.note))
            except SocialError as exc:
                recusadas.append({"id": d.id, "reason": str(exc)})
        return {"decided": decididas, "refused": recusadas}

    def decide(self, approval_id: str, verb: str, *, content: str | None = None,
               note: str | None = None) -> dict[str, Any]:
        from .service import SocialError

        pedido = self.store.get(approval_id)
        if pedido is None:
            raise SocialError("not_found", "Aprovação não encontrada.", 404)
        if pedido.status != "pending":
            raise SocialError("already_decided", f"Esta aprovação já foi decidida ({pedido.status}).")
        if verb == "edit" and not (content or "").strip():
            raise SocialError("empty_content", "Escreva o texto que deve ser enviado.", 400)

        if verb == "edit" and pedido.step_id:
            apply_edit(self.repo.db, pedido.step_id, content.strip())          # type: ignore[union-attr]
        decidido = self.store.decide(approval_id, status={"approve": "approved", "edit": "edited",
                                                          "reject": "rejected"}[verb],
                                     content=content.strip() if content else None, note=note)
        if decidido is None:
            raise SocialError("already_decided", "Esta aprovação já foi decidida.", 409)

        if pedido.objective_id:
            if verb == "reject":
                item = None
                if pedido.step_id:
                    row = self.repo.db.one("SELECT variables FROM steps WHERE id=?", (pedido.step_id,))
                    item = (loads(row["variables"], {}) or {}).get("item") if row else None
                self.repo.cancel_target_steps(pedido.objective_id, pedido.step_id or "", item=item,
                                              reason="rejeitado por quem aprova" + (f": {note}" if note else ""))
            self.repo.resume_objective(pedido.objective_id,
                                       {"approve": "Aprovado; a etapa segue como planejada.",
                                        "edit": "Conteúdo editado e aprovado; a etapa segue com o texto novo.",
                                        "reject": "Rejeitado; as etapas deste alvo foram canceladas."}[verb])
            self.repo.recompute_run(pedido.run_id) if pedido.run_id else None
        if self.scheduler is not None:
            self.scheduler.wake()
        return decidido.to_dict()

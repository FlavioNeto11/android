"""As portas da conversa do Telegram para os serviços do painel (item 28.15, ADR-071).

Cada método chama o MESMO serviço que a rota do painel chama, com os mesmos objetos de pedido:
- `previa`: `RunService.previa_de_alvos` (`POST /runs/targets/resolve`);
- `criar`: `RunService.create`, ecoando em `targets`/`instance_ids` o que a prévia devolveu. É o eco que o painel faz
  para confirmar o destino tirado do texto (§7.6), e sem ele a criação recusa (`alvos_nao_confirmados`);
- `decidir`: `ApprovalService.decide` (`POST /approvals/{id}/decide`);
- `responder`: `ComandoAssistido.sucessora` (`POST /runs/{id}/successor`), com o modo da execução respondida.

O autor de tudo isto é o operador do ContextVar (`telegram:dono`), que o serviço de entrada põe antes de chamar.
`RunError` e `SocialError` viram `RecusaDaCentral`, com a mesma frase que o painel mostraria.
"""
from __future__ import annotations

from collections.abc import Callable

from app.db import Database
from app.models import Health, RunCreate, RunStatus, RunTarget, RunTargetsResolveBody
from app.modules.avisos.infrastructure.entrada import Pendencia, Previa, RecusaDaCentral
from app.security.sessions import operador_atual
from app.shared.costuras import autor_do_gesto
from app.social.approvals import ApprovalService
from app.social.service import SocialError
from app.taskqueue.assistente import ComandoAssistido, RunSuccessorBody
from app.taskqueue.service import RunError, RunService

#: Como o status de uma execução terminada se lê na conversa.
_DESFECHO = {RunStatus.completed: "concluída", RunStatus.completed_with_issues: "concluída com problemas",
             RunStatus.failed: "falhou", RunStatus.cancelled: "cancelada"}
_ATIVAS = ("planning", "running", "paused", "cancelling")


class PortasReais:
    def __init__(self, *, db: Database, runs: RunService, aprovacoes: ApprovalService, saude: Callable[[], Health],
                 online: Callable[[], list[str]]):
        self.db = db
        self.runs = runs
        self.aprovacoes = aprovacoes
        self._saude = saude
        self._online = online

    # ------------------------------------------------------------------ leitura
    def status(self) -> str:
        h = self._saude()
        online = self._online()
        marcas = ",".join("?" * len(_ATIVAS))
        ativas = int(self.db.scalar(f"SELECT COUNT(*) FROM runs WHERE status IN ({marcas})", _ATIVAS) or 0)
        esperando = int(self.db.scalar("SELECT COUNT(*) FROM runs WHERE status='needs_input'") or 0)
        aprovar = len(self.aprovacoes_pendentes())
        problemas = len(h.problems)
        linhas = [f"Central: {'ok' if h.status == 'ok' else h.status}"
                  + (f", {problemas} problema(s) no painel" if problemas else ""),
                  f"Aparelhos online: {len(online)}" + (f" ({', '.join(online[:10])})" if online else ""),
                  f"Execuções em andamento: {ativas}",
                  f"Esperando você: {aprovar} aprovação(ões) e {esperando} pergunta(s). /pendencias mostra."]
        return "\n".join(linhas)

    def aprovacoes_pendentes(self) -> list[str]:
        return [str(a["id"]) for a in self.aprovacoes.list(status="pending", limit=200)]

    def execucoes_esperando(self) -> list[str]:
        return [str(r["id"]) for r in self.db.query(
            "SELECT id FROM runs WHERE status='needs_input' ORDER BY created_at DESC LIMIT 200")]

    def pendencias(self) -> list[Pendencia]:
        itens = [Pendencia("aprovacao", str(a["id"]), _resumo_da_aprovacao(a))
                 for a in self.aprovacoes.list(status="pending", limit=50)]
        itens += [Pendencia("pergunta", str(r["id"]), str(r["status_detail"] or "a execução espera uma resposta"))
                  for r in self.db.query("SELECT id, status_detail FROM runs WHERE status='needs_input'"
                                         " ORDER BY created_at DESC LIMIT 50")]
        return itens

    def online(self) -> list[str]:
        return self._online()

    def desfecho(self, run_id: str) -> str | None:
        row = self.runs.repo.run_row(run_id)
        if row is None:
            return f"A execução {run_id[-6:]} não existe mais (purgada)."
        status = RunStatus(row["status"])
        if status not in _DESFECHO:
            return None
        resumo = self.runs.repo.run_summary(row)
        c = resumo.counts
        total = c.succeeded + c.failed + c.waiting_user + c.uncertain + c.cancelled + c.running + c.pending
        linhas = [f"Execução {resumo.short_id}: {_DESFECHO[status]}."]
        if total:
            linhas.append(f"Objetivos: {c.succeeded} de {total} com sucesso"
                          + (f", {c.failed} com falha" if c.failed else "")
                          + (f", {c.uncertain} sem confirmação" if c.uncertain else "") + ".")
        evidencia = self.db.scalar("SELECT status_detail FROM objectives WHERE run_id=? AND status_detail IS NOT NULL"
                                   " ORDER BY finished_at DESC LIMIT 1", (run_id,))
        detalhe = evidencia or resumo.status_detail
        if detalhe:
            linhas.append(f"Evidência: {str(detalhe)[:300]}")
        return "\n".join(linhas)

    # ------------------------------------------------------------------ ação (os serviços das rotas)
    def previa(self, texto: str, instance_ids: list[str] | None = None) -> Previa:
        try:
            p = self.runs.previa_de_alvos(RunTargetsResolveBody(command=texto, instance_ids=instance_ids or []))
        except RunError as exc:
            raise RecusaDaCentral(exc.message) from None
        return Previa(alvos=[t.model_dump() for t in p.targets],
                      perguntas=[str(q.get("question") or "") for q in p.questions if q.get("question")],
                      comando=p.command_sem_destinos, avisos=list(p.warnings))

    def criar(self, texto: str, alvos: list[dict[str, object]], chave: str) -> tuple[str, str]:
        targets = [RunTarget(profile_id=str(a["profile_id"]), instance_ids=[str(a["instance_id"])],
                             app_id=str(a["app_id"]) if a.get("app_id") else None)
                   for a in alvos if a.get("profile_id")]
        aparelhos = [str(a["instance_id"]) for a in alvos if not a.get("profile_id") and a.get("instance_id")]
        try:
            run = self.runs.create(RunCreate(command=texto, targets=targets, instance_ids=aparelhos,
                                             idempotency_key=chave, mode="execute"))
        except RunError as exc:
            raise RecusaDaCentral(exc.message) from None
        return run.id, run.short_id

    def decidir(self, approval_id: str, verbo: str) -> str:
        try:
            self.aprovacoes.decide(approval_id, verbo)
        except SocialError as exc:
            raise RecusaDaCentral(exc.message) from None
        return "Aprovado: a execução segue." if verbo == "approve" else "Vetado: nada é enviado."

    def responder(self, run_id: str, texto: str) -> tuple[str, str]:
        row = self.runs.repo.run_row(run_id)
        if row is None:
            raise RecusaDaCentral("Execução não encontrada.")
        modo = "plan" if row["mode"] == "plan" else "execute"
        comando = f"{row['command']}\n{texto}"
        try:
            nova, _ = ComandoAssistido(self.runs).sucessora(
                run_id, RunSuccessorBody(command=comando[:4000], mode=modo), por=autor_do_gesto(operador_atual()))
        except RunError as exc:
            raise RecusaDaCentral(exc.message) from None
        return nova.id, nova.short_id


def _resumo_da_aprovacao(a: dict[str, object]) -> str:
    """O que se aprova (decisão (d)): o que a etapa faz e o texto que sairia. O serviço redige e corta antes de
    mandar; aqui só se escolhe o campo."""
    capacidade = str(a.get("capability") or "ação")
    alvo = str(a.get("target") or "")
    conteudo = str(a.get("content") or a.get("generated_content") or a.get("summary") or "")
    partes = [capacidade + (f" em {alvo}" if alvo else "")]
    if conteudo:
        partes.append(f"\"{conteudo[:500]}\"")
    return ": ".join(partes)

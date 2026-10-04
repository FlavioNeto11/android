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
from typing import Any, Callable

from ..db import Database, Row, dumps, loads
from ..planning.capabilities import Capability, objeto_da_acao
from ..security.sessions import operador_atual
from ..util import new_token, now, now_iso, parse_iso
from .excecoes import ExcecoesDePolitica

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
    #: QUEM decidiu (item 9.1). Nulo nas aprovações decididas antes de existir sessão — e nulo continua
    #: querendo dizer "não dá para saber", que é mais honesto do que carimbar `panel` em todas elas.
    decided_by: str | None = None
    interaction_id: str | None = None                        # preenchido no commit: o efeito que esta decisão liberou
    #: 29.30: a imagem da persona que a etapa vai publicar (`image_id` dos argumentos da etapa), para quem aprova VER
    #: o que sai, e não só a legenda. Lida da etapa, sem coluna nova: a aprovação aponta para ela por `step_id`.
    image_id: str | None = None
    #: 30.61: `plano` (o sim dado na prévia da porta, antes de iniciar) ou `execucao` (a porta do despacho, como sempre).
    #: A de origem `plano` só vale na execução para o item IDÊNTICO (`chave_sha256`, recalculada no despacho), dentro de
    #: `expires_at` e antes de o efeito sair; senão conta como ausente e a porta pergunta de novo.
    origem: str = "execucao"
    expires_at: str | None = None
    chave_sha256: str | None = None
    chave_v: int | None = None
    plan_version: int | None = None
    midia_sha256: str | None = None

    @property
    def content(self) -> str | None:
        """O que de fato vai ser digitado: o texto editado, quando houve edição."""
        return self.approved_content if self.status == "edited" and self.approved_content else self.generated_content

    def to_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in
             ("id", "profile_id", "run_id", "objective_id", "step_id", "capability", "target", "summary",
              "generated_content", "approved_content", "status", "created_at", "decided_at", "decided_note",
              "decided_by", "interaction_id", "image_id", "origem", "expires_at", "plan_version")}
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
                        decided_by=row["decided_by"], interaction_id=row["interaction_id"],
                        image_id=self._imagem_da_etapa(row["step_id"]), origem=row.get("origem") or "execucao",
                        expires_at=row.get("expires_at"), chave_sha256=row.get("chave_sha256"),
                        chave_v=row.get("chave_v"), plan_version=row.get("plan_version"),
                        midia_sha256=row.get("midia_sha256"))

    def objeto_da_etapa(self, step_id: str | None, acao: Capability | None) -> dict[str, str] | None:
        """30.64: o OBJETO da ação nos argumentos da etapa, pelo que o catálogo declara em `objeto_alvo` (fonte única,
        a mesma da chave da aprovação no plano). O alvo da aprovação é só a PESSOA (`@ana`); o post, o comentário ou a
        mídia moram aqui. Sem isto, aprovar "comentar no post A de @ana" valia para o post B dela depois de uma
        revisão do plano. `None` (sem declaração, argumento por resolver, etapa ilegível) falha fechado."""
        if not step_id:
            return None
        argumentos = loads(self.db.scalar("SELECT bindings FROM steps WHERE id=?", (step_id,)), {})
        return objeto_da_acao(acao, argumentos) if isinstance(argumentos, dict) else None

    def _imagem_da_etapa(self, step_id: str | None) -> str | None:
        """O `image_id` dos argumentos da etapa (29.30), ou `None`. Argumento ilegível não derruba a lista."""
        if not step_id:
            return None
        bruto = self.db.scalar("SELECT bindings FROM steps WHERE id=?", (step_id,))
        argumentos = loads(bruto, {})
        valor = argumentos.get("image_id") if isinstance(argumentos, dict) else None
        return valor if isinstance(valor, str) and valor else None

    def get(self, approval_id: str) -> Approval | None:
        row = self.db.one("SELECT * FROM pending_approvals WHERE id=?", (approval_id,))
        return self._dto(row) if row else None

    def for_step(self, step_id: str) -> Approval | None:
        row = self.db.one("SELECT * FROM pending_approvals WHERE step_id=? ORDER BY created_at DESC LIMIT 1",
                          (step_id,))
        return self._dto(row) if row else None

    def acompanhar_revisao(self, step_id: str, *, profile_id: str | None, acao: Capability | None, target: str | None,
                           content: str | None, disparou: Callable[[str], bool]) -> Approval | None:
        """A decisão já tomada sobre ESTA etapa numa versão anterior do plano, quando ela vale para a etapa revisada.

        A revisão do plano (recuperação automática, “Tentar novamente”) recria a etapa com id novo, e `for_step`
        procura pelo id: um CREATE_COMMENT aprovado na v1 abria pedido novo na v2 e o objetivo voltava a esperar
        alguém — a pessoa aprovava a mesma frase duas vezes, e na espera o prazo corria.

        Vale só o que foi de fato aprovado: a decisão mais recente sobre a mesma chave do mesmo objetivo, aprovada
        ou editada (uma rejeição posterior encerra o assunto), para o mesmo perfil, a mesma ação, o mesmo alvo e
        o MESMO texto (o editado, quando houve edição). Texto diferente é pedido novo. Chave diferente também, mesmo
        com texto e alvo iguais: são duas publicações. E uma aprovação cujo efeito já saiu (interação aberta no
        commit, ou etapa antiga com efeito disparado — `disparou`, a mesma regra de `Repository.commit_state`) foi
        gasta: repetir o envio pede decisão de novo.

        Quando vale, a aprovação passa a apontar para a etapa revisada, em vez de ser copiada: é a etapa que vai
        rodar, e é por ela que o commit liga a interação (`link_interaction`). Uma cópia poria dois cartões da
        mesma decisão na aba de aprovações.
        """
        etapa = self.db.one("SELECT objective_id, key, plan_version FROM steps WHERE id=?", (step_id,))
        objeto = self.objeto_da_etapa(step_id, acao)
        if etapa is None or not etapa["objective_id"] or acao is None or objeto is None:
            return None
        capability = acao.key
        row = self.db.one(
            "SELECT a.* FROM pending_approvals a JOIN steps s ON s.id=a.step_id WHERE s.objective_id=? AND s.key=?"
            " AND s.plan_version<? AND a.status IN ('approved','edited','rejected')"
            # 30.61: o sim do plano não migra para a etapa revisada; ele vale só pela chave, no `_approval_gate`.
            " AND a.origem<>'plano'"
            " ORDER BY s.plan_version DESC, a.created_at DESC LIMIT 1",
            (etapa["objective_id"], etapa["key"], etapa["plan_version"]))
        if row is None:
            return None
        anterior = self._dto(row)
        vale = (anterior.status in ("approved", "edited") and anterior.interaction_id is None
                and anterior.profile_id == profile_id and anterior.capability == capability
                and (anterior.target or None) == (target or None)
                and (anterior.content or "").strip() == (content or "").strip()
                # 30.60 (N1): a mesma legenda com OUTRA imagem é outra publicação; aprovar uma não aprova a outra.
                and anterior.image_id == self._imagem_da_etapa(step_id)
                # 30.64: o mesmo texto para a mesma pessoa em OUTRO post (outra legenda, outro autor) é outra ação.
                # declarado no catálogo (`objeto_alvo`); sem declaração não há reuso (acima).
                and self.objeto_da_etapa(anterior.step_id, acao) == objeto)
        if not vale or anterior.step_id is None or disparou(anterior.step_id):
            return None
        self.db.execute("UPDATE pending_approvals SET step_id=? WHERE id=? AND step_id=?",
                        (step_id, anterior.id, anterior.step_id))
        return self.for_step(step_id)

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

    def aprovar_no_plano(self, *, profile_id: str, capability: str, summary: str, target: str | None,
                         content: str | None, run_id: str, objective_id: str, step_id: str, chave_sha256: str,
                         chave_v: int, plan_version: int, expires_at: str, midia_sha256: str | None,
                         decided_by: str | None) -> Approval:
        """30.61: o sim dado na prévia da porta, já decidido (`approved`, origem `plano`), num INSERT só, dentro da
        transação do gesto. Vale na execução só pelo `_approval_gate`, que recalcula a chave da etapa relida."""
        approval_id = f"apr-{new_token()}"
        agora = now_iso()
        self.db.execute(
            "INSERT INTO pending_approvals(id, profile_id, run_id, objective_id, step_id, capability, target, summary,"
            " generated_content, status, created_at, decided_at, decided_note, decided_by, origem, expires_at,"
            " chave_sha256, chave_v, plan_version, midia_sha256)"
            " VALUES (?,?,?,?,?,?,?,?,?,'approved',?,?,?,?,'plano',?,?,?,?,?)",
            (approval_id, profile_id, run_id, objective_id, step_id, capability, target, summary, content, agora, agora,
             "aprovado na prévia da porta (30.61)", decided_by or operador_atual(), expires_at, chave_sha256, chave_v,
             plan_version, midia_sha256))
        row = self.db.one("SELECT * FROM pending_approvals WHERE id=?", (approval_id,))
        return self._dto(row)

    def decide(self, approval_id: str, *, status: str, content: str | None = None,
               note: str | None = None, decided_by: str | None = None,
               na_mesma_transacao: Callable[[Row], None] | None = None) -> Approval | None:
        """Decisão é definitiva: só uma aprovação `pending` pode ser decidida, e só uma vez.

        `decided_by` sai da sessão do painel quando não é informado. Lido AQUI, e não empurrado por parâmetro
        desde a rota, porque o caminho entre as duas passa por `ApprovalService.decide_many` — e um parâmetro a
        mais em cada degrau seria uma chance a mais de alguém esquecer de repassá-lo justamente no lote.
        """
        autor = decided_by or operador_atual()
        with self.db.tx():
            row = self.db.one("SELECT * FROM pending_approvals WHERE id=? AND status='pending'", (approval_id,))
            if row is None:
                return None
            if na_mesma_transacao is not None:
                # 30.60 (N3): o que acompanha a decisão (o texto editado na etapa) só se grava se ESTA decisão venceu
                # a corrida pelo `pending`; antes, o texto da edição perdedora já estava na etapa.
                na_mesma_transacao(row)
            self.db.execute(
                "UPDATE pending_approvals SET status=?, approved_content=COALESCE(?, approved_content),"
                " decided_at=?, decided_note=?, decided_by=? WHERE id=?",
                (status, content, now_iso(), note, autor, approval_id))
        return self.get(approval_id)

    def link_interaction(self, step_id: str, interaction_id: str) -> None:
        """Fecha o rastro rascunho → aprovação → efeito.

        A aprovação nasce ANTES de existir interação (é o que impede o envio), então a coluna só pode ser
        preenchida no instante do commit, quando o efeito é aberto. Sem isso não há como sair de um texto
        aprovado e chegar ao que de fato foi publicado — nem o contrário.
        """
        self.db.execute("UPDATE pending_approvals SET interaction_id=? WHERE step_id=? AND interaction_id IS NULL",
                        (interaction_id, step_id))

    def descartar_do_plano(self, approval_id: str, *, motivo: str) -> bool:
        """30.61: o sim dado no plano que não cobre a etapa sai como `expired`, com o porquê (condicional: só o aprovado
        de origem `plano` ainda sem efeito)."""
        cur = self.db.execute(
            "UPDATE pending_approvals SET status='expired', decided_note=? WHERE id=? AND origem='plano'"
            " AND status='approved' AND interaction_id IS NULL", (f"sim do plano descartado: {motivo}", approval_id))
        return bool(cur.rowcount)

    def expire_for_objective(self, objective_id: str, *, reason: str) -> int:
        cur = self.db.execute(
            "UPDATE pending_approvals SET status='expired', decided_at=?, decided_note=? WHERE objective_id=?"
            " AND status='pending'", (now_iso(), reason, objective_id))
        # 30.61: o objetivo encerrado (cancelado em `planned`, vencido pelo 31.43, abandonado) leva junto o sim do plano
        # que não saiu; ele não vale mais e não deve reservar alvo nem teto.
        plano = self.db.execute(
            "UPDATE pending_approvals SET status='expired', decided_note=? WHERE objective_id=? AND origem='plano'"
            " AND status='approved' AND interaction_id IS NULL", (f"sim do plano encerrado: {reason}", objective_id))
        return int(cur.rowcount or 0) + int(plano.rowcount or 0)

    def vencer_do_plano(self, agora: str, *, run_id: str | None = None) -> int:
        """30.61, faxina: o sim do plano cuja validade passou sai como `expired` (a porta já o trataria como ausente; aqui
        ele deixa também de reservar alvo e teto numa prévia abandonada em `planned`). A execução fica como está."""
        cur = self.db.execute(
            "UPDATE pending_approvals SET status='expired', decided_note='sim do plano vencido (validade)'"
            " WHERE origem='plano' AND status='approved' AND interaction_id IS NULL AND expires_at<?"
            + (" AND run_id=?" if run_id else ""), (agora, *((run_id,) if run_id else ())))
        return int(cur.rowcount or 0)

    def renovar_do_plano(self, run_id: str, *, expires_at: str) -> int:
        """30.61 "Renovar": estende a validade dos sins do plano ainda em aberto desta execução, sem reabrir os itens (a
        chave segue a mesma; o despacho a confere de novo). Os já gastos, vencidos por descarte ou encerrados ficam."""
        # Só o que AINDA vale: o sim vencido que a faxina não marcou (janela de até um ciclo) não volta a valer sem o dono
        # rever (revisão da parte 17); o resultado não pode depender do relógio da faxina.
        cur = self.db.execute(
            "UPDATE pending_approvals SET expires_at=? WHERE run_id=? AND origem='plano' AND status='approved'"
            " AND interaction_id IS NULL AND expires_at>=?", (expires_at, run_id, now_iso()))
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
    # Normaliza na ESCRITA para que argumento, guarda e parâmetros falem exatamente do mesmo texto. Sem isto, um
    # "…lindo!\n" virava guarda crua e a edição seguinte não a reconhecia: a etapa passava a exigir o texto velho
    # E o novo visíveis ao mesmo tempo, o commit era rejeitado e ela morria — depois de aprovada, sem pista.
    texto = (texto or "").strip()
    bindings = loads(row["bindings"], {}) or {}
    antigo = (bindings.get("content") or "").strip()
    bindings["content"] = texto
    guardas = [g for g in (loads(row["commit_guard"], []) or []) if not antigo or (g or "").strip() != antigo]
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


def guardar_rascunho(db: Database, step_id: str, meta: dict[str, Any]) -> None:
    """Guarda na etapa o que o rascunho descobriu além do texto.

    São os candidatos a memória (fatos que a contraparte afirmou) e a justificativa. Ficam fora de `bindings` de
    propósito: `bindings` é o que o ator vê no prompt, e isto não é argumento de ação — é o que o COMMIT vai
    anexar à interação, para `memory.learn_from` ter o que aprender quando o efeito for confirmado.

    Precisa ser durável: entre escrever e commitar pode haver horas de espera por aprovação e um reinício.
    """
    db.execute("UPDATE steps SET draft_meta=? WHERE id=?", (dumps(meta), step_id))


def ler_rascunho(db: Database, step_id: str) -> dict[str, Any]:
    row = db.one("SELECT draft_meta FROM steps WHERE id=?", (step_id,))
    return (loads(row["draft_meta"], {}) or {}) if row is not None else {}


def textos_irmaos(db: Database, run_id: str, step_id: str, limit: int = 16) -> list[str]:
    """O que as OUTRAS contas desta execução já escreveram para a mesma tarefa.

    É o espelho do defeito que originou tudo isto: o mesmo comando em oito aparelhos saía como a mesma frase.
    Cada perfil escreve depois dos irmãos que já passaram pela porta, então aqui ele vê o que não pode repetir.
    """
    linhas = db.query(
        "SELECT bindings FROM steps WHERE run_id=? AND id<>? AND bindings LIKE '%\"content\"%' LIMIT ?",
        (run_id, step_id, limit))
    textos = []
    for row in linhas:
        texto = ((loads(row["bindings"], {}) or {}).get("content") or "").strip()
        if texto:
            textos.append(texto)
    return textos


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

    def __init__(self, store: ApprovalStore, repo: Any, scheduler: Any = None, *,
                 excecoes: ExcecoesDePolitica | None = None):
        self.store = store
        self.repo = repo
        self.scheduler = scheduler
        #: 30.65: rejeitar o cartão da etapa presa encerra a exceção de política dela.
        self.excecoes = excecoes

    def list(self, *, status: str | None = "pending", profile_id: str | None = None, run_id: str | None = None,
             limit: int = 50) -> list[dict[str, Any]]:
        itens = self.store.list(status=status, profile_id=profile_id, run_id=run_id, limit=limit)
        # 31.50: a aprovação pendente vence junto com o objetivo que ela bloqueia (`_expirar_aprovacoes`). Uma consulta
        # para a lista inteira. Decidida, sem objetivo ou com o vencimento desligado: `None`.
        pendentes = [a.objective_id for a in itens if a.status == "pending" and a.objective_id]
        prazos = self.repo.vence_em_dos_objetivos(pendentes) if hasattr(self.repo, "vence_em_dos_objetivos") else {}
        return [{**a.to_dict(), "vence_em": prazos.get(a.objective_id) if a.status == "pending" and a.objective_id
                 else None} for a in itens]

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

    def expirar_da_etapa(self, step_id: str | None, *, motivo: str) -> int:
        """30.65: a exceção da etapa foi revogada com o cartão ainda pendente. O cartão expira (sai de Pendências; o
        reply no Telegram passa a ser recusado como vencido) e o objetivo volta à porta, que agora recusa sem a exceção."""
        if not step_id:
            return 0
        pedidos = [a for a in self.store.list(status="pending", limit=500) if a.step_id == step_id]
        for a in pedidos:
            self.store.db.execute("UPDATE pending_approvals SET status='expired', decided_at=?, decided_note=?"
                                  " WHERE id=? AND status='pending'", (now_iso(), motivo, a.id))
            if a.objective_id:
                self.repo.resume_objective(a.objective_id, f"Cartão expirado: {motivo}.")
                self.repo.recompute_run(a.run_id) if a.run_id else None
        if pedidos and self.scheduler is not None:
            self.scheduler.wake()
        return len(pedidos)

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
        if verb != "edit" and (content or "").strip():
            # Aceitar calado era pior do que recusar: o texto ia para `approved_content`, a resposta 200 o
            # devolvia, e o aparelho digitava o outro. Quem chama pela API acreditaria ter aprovado o que mandou.
            raise SocialError("content_not_allowed",
                              "Só `edit` recebe texto; aprovar ou rejeitar não trocam o que será enviado.", 400)

        editar: Callable[[Row], None] | None = None
        if verb == "edit" and pedido.step_id:
            texto, etapa = content.strip(), pedido.step_id                     # type: ignore[union-attr]
            editar = lambda _row: apply_edit(self.repo.db, etapa, texto)
        decidido = self.store.decide(approval_id, status={"approve": "approved", "edit": "edited",
                                                          "reject": "rejected"}[verb],
                                     content=content.strip() if content else None, note=note,
                                     na_mesma_transacao=editar)
        if decidido is None:
            raise SocialError("already_decided", "Esta aprovação já foi decidida.", 409)
        if verb == "reject" and self.excecoes is not None:
            # 30.65: a recusa do dono encerra a exceção presa a esta etapa; ela não volta a valer para outra etapa do
            # mesmo perfil, alvo e ação, nem de outra execução.
            self.excecoes.recusar_da_etapa(pedido.step_id, por=decidido.decided_by)

        if pedido.objective_id:
            if verb == "reject":
                item = None
                if pedido.step_id:
                    row = self.repo.db.one("SELECT variables FROM steps WHERE id=?", (pedido.step_id,))
                    item = (loads(row["variables"], {}) or {}).get("item") if row else None
                self.repo.cancel_target_steps(pedido.objective_id, pedido.step_id or "", item=item, note=note)
            # O prazo do objetivo não pode correr contra quem está decidindo: o pedido nasce na porta e o objetivo
            # fica parado desde então, de modo que o tempo entre abrir e decidir é exatamente a espera humana.
            esperou = int((now() - (parse_iso(pedido.created_at) or now())).total_seconds())
            self.repo.resume_objective(pedido.objective_id,
                                       {"approve": "Aprovado; a etapa segue como planejada.",
                                        "edit": "Conteúdo editado e aprovado; a etapa segue com o texto novo.",
                                        "reject": "Rejeitado; as etapas deste alvo foram canceladas."}[verb],
                                       esperou_s=esperou)
            self.repo.recompute_run(pedido.run_id) if pedido.run_id else None
        if self.scheduler is not None:
            self.scheduler.wake()
        return decidido.to_dict()

"""As portas da conversa do Telegram para os serviços do painel (item 28.15, ADR-071).

Cada método chama o MESMO serviço que a rota do painel chama, com os mesmos objetos de pedido:
- `previa`: `RunService.previa_de_alvos` (`POST /runs/targets/resolve`);
- `criar`: `RunService.create`, ecoando em `targets`/`instance_ids` o que a prévia devolveu. É o eco que o painel faz
  para confirmar o destino tirado do texto (§7.6), e sem ele a criação recusa (`alvos_nao_confirmados`);
- `decidir`: `ApprovalService.decide` (`POST /approvals/{id}/decide`);
- `responder`: `ComandoAssistido.sucessora` (`POST /runs/{id}/successor`), com o modo da execução respondida;
- `porta` e `aprovar_plano` (28.27): `porta_do_plano.previa_da_porta` e `aprovar_plano` (`GET /runs/{id}/porta` e
  `POST /runs/{id}/aprovar-plano`, 30.61), injetados pelo `AppState` como funções;
- `iniciar` e `cancelar`: `RunService.start` e `cancel` (`POST /runs/{id}/start` e `/cancel`).

O autor de tudo isto é o operador do ContextVar (`telegram:dono`), que o serviço de entrada põe antes de chamar.
`RunError` e `SocialError` viram `RecusaDaCentral`, com a mesma frase que o painel mostraria.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from app.db import Database, loads
from app.models import Health, RunCreate, RunStatus, RunTarget, RunTargetsResolveBody
from app.modules.avisos.application.entrada import casar_ref
from app.modules.avisos.infrastructure.anexos_leitura import LeitorDeAnexo, LeituraRecusada
from app.modules.avisos.infrastructure.entrada import Captura, Pendencia, PlanoMudou, Previa, RecusaDaCentral
from app.porta_do_plano import PortaIndisponivel
from app.security.sessions import operador_atual
from app.shared.costuras import autor_do_gesto
from app.social.approvals import ApprovalService
from app.social.chave_da_aprovacao import ARGUMENTO_DA_IMAGEM
from app.social.service import SocialError
from app.taskqueue.assistente import ComandoAssistido, RunSuccessorBody
from app.taskqueue.perguntas import (
    pergunta_sensivel_aberta,
    pergunta_sensivel_da_execucao,
    perguntas_abertas,
    tipo_sensivel,
)
from app.taskqueue.service import RunError, RunService

#: Como o status de uma execução terminada se lê na conversa.
_DESFECHO = {RunStatus.completed: "concluída", RunStatus.completed_with_issues: "concluída com problemas",
             RunStatus.failed: "falhou", RunStatus.cancelled: "cancelada"}
_ATIVAS = ("planning", "running", "paused", "cancelling")


class PortasReais:
    def __init__(self, *, db: Database, runs: RunService, aprovacoes: ApprovalService, saude: Callable[[], Health],
                 online: Callable[[], list[str]],
                 capturar: Callable[[str], Awaitable[tuple[bytes | None, str | None]]] | None = None,
                 leitor_de_anexos: LeitorDeAnexo | None = None,
                 previa_da_porta: Callable[[str], dict[str, object]] | None = None,
                 aprovar_plano: Callable[[str, list[tuple[str, str]], str, str | None], dict[str, object]]
                 | None = None,
                 ler_imagem: Callable[[str], bytes | None] | None = None):
        self.db = db
        # 28.27: as duas da porta (30.61) e a leitura dos bytes da imagem da persona (`storage_key`), injetadas.
        self._previa_da_porta = previa_da_porta
        self._aprovar_plano = aprovar_plano
        self._ler_imagem = ler_imagem
        self._capturar = capturar
        self._leitor_de_anexos = leitor_de_anexos
        self.runs = runs
        self.aprovacoes = aprovacoes
        self._saude = saude
        self._online = online

    async def captura(self, instance_id: str) -> Captura:
        """A tela do aparelho pedida pelo dono (28.24, exceção (a)): a prévia do painel, sem caminho novo até o aparelho."""
        if self._capturar is None:
            return Captura(motivo="A captura de tela não está disponível nesta Central.")
        jpeg, motivo = await self._capturar(instance_id)
        return Captura(conteudo=jpeg, motivo=motivo)

    async def ler_anexo(self, anexo_id: int) -> str:
        """A descrição da imagem do dono pela IA (28.24, F3), pela MESMA porta da rota `POST /api/canais/anexos/{id}/ler`."""
        if self._leitor_de_anexos is None:
            raise RecusaDaCentral("A leitura de imagem pela IA não está disponível nesta Central.")
        try:
            leitura = await self._leitor_de_anexos.ler(anexo_id)
        except LeituraRecusada as recusa:
            raise RecusaDaCentral(recusa.motivo, recusa.codigo) from None
        rodape = ("Já tinha lido esta imagem: sem custo novo." if leitura.do_cache
                  else f"Li com {leitura.modelo}; custo US$ {leitura.custo_usd:.4f}.")
        return f"{leitura.descricao}\n\n{rodape}"

    # ------------------------------------------------------------------ leitura
    def nomes_de_persona(self) -> list[str]:
        return nomes_de_persona(self.db)

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

    def status_para_convidado(self) -> str:
        """O `/status` de quem não é o dono (28.18, C-09): só contagens. Nenhum id de aparelho, nenhuma pendência, nada de
        persona ou conta: o convidado vê se a Central está no ar, e só."""
        h = self._saude()
        marcas = ",".join("?" * len(_ATIVAS))
        ativas = int(self.db.scalar(f"SELECT COUNT(*) FROM runs WHERE status IN ({marcas})", _ATIVAS) or 0)
        return "\n".join([f"Central: {'no ar' if h.status == 'ok' else 'no ar, com alertas'}",
                          f"Aparelhos online: {len(self._online())}",
                          f"Execuções em andamento: {ativas}"])

    def aprovacoes_pendentes(self) -> list[str]:
        return [str(a["id"]) for a in self.aprovacoes.list(status="pending", limit=200)]

    def ids_de_aprovacoes(self) -> list[str]:
        """As aprovações em qualquer estado (as 500 mais novas): o id digitado num reply pode ser de uma já decidida ou
        vencida (28.26, revisão da suíte 31), e aí também é "outro item"."""
        return [str(a["id"]) for a in self.aprovacoes.list(status=None, limit=500)]

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

    def pergunta_sensivel(self, ref: str | None) -> str | None:
        """As leituras públicas do 29.52 (`taskqueue/perguntas.py`), e nada mais: sem `ref`, alguma pergunta sensível
        aberta agora; com `ref` (o id inteiro ou o fim dele entre as que esperam resposta), a da execução indicada. Só o
        tipo volta, nunca o texto da pergunta. Erro de leitura sobe: quem chama recusa na dúvida."""
        if ref is None:
            return pergunta_sensivel_aberta(self.db)
        ids = set(casar_ref(ref, self.execucoes_esperando()))
        if self.runs.repo.run_row(ref) is not None:
            ids.add(ref)
        for rid in sorted(ids):
            tipo = pergunta_sensivel_da_execucao(self.db, rid)
            if tipo is None and rid == ref:
                # A resposta ATRASADA (a execução venceu ou foi cancelada antes): a leitura pública dá None fora do
                # `needs_input`, e a linha guardaria a senha com o texto. Pelo id inteiro, vale a pergunta que ela fez.
                row = self.runs.repo.run_row(rid)
                tipo = tipo_sensivel(perguntas_abertas(self.db, row)) if row is not None else None
            if tipo is not None:
                return tipo
        return None

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

    def criar(self, texto: str, alvos: list[dict[str, object]], chave: str, modo: str = "execute") -> tuple[str, str]:
        targets = [RunTarget(profile_id=str(a["profile_id"]), instance_ids=[str(a["instance_id"])],
                             app_id=str(a["app_id"]) if a.get("app_id") else None)
                   for a in alvos if a.get("profile_id")]
        aparelhos = [str(a["instance_id"]) for a in alvos if not a.get("profile_id") and a.get("instance_id")]
        if not (targets or aparelhos):
            # O `RunCreate` exige uma seleção; sem alvo confirmado, a prévia é que deveria ter perguntado.
            raise RecusaDaCentral("Sem aparelho nem persona confirmados: mande o pedido de novo com o destino.")
        try:
            run = self.runs.create(RunCreate(command=texto, targets=targets, instance_ids=aparelhos,
                                             idempotency_key=chave, mode="plan" if modo == "plan" else "execute"))
        except RunError as exc:
            raise RecusaDaCentral(exc.message) from None
        return run.id, run.short_id

    # ------------------------------------------------------------------ a porta do plano (28.27)
    def estado_da_execucao(self, run_id: str) -> str | None:
        row = self.runs.repo.run_row(run_id)
        return str(row["status"]) if row is not None else None

    def execucao_da_chave(self, chave: str) -> str | None:
        valor = self.db.scalar("SELECT id FROM runs WHERE idempotency_key=?", (chave,))
        return str(valor) if valor else None

    def porta(self, run_id: str) -> dict[str, object]:
        if self._previa_da_porta is None:
            raise RecusaDaCentral("A prévia da porta não está disponível nesta Central.")
        try:
            return self._previa_da_porta(run_id)
        except PortaIndisponivel as exc:
            raise RecusaDaCentral(exc.mensagem, exc.codigo) from None

    def aprovar_plano(self, run_id: str, aprovar: list[tuple[str, str]], *,
                      vista_em: str | None = None) -> dict[str, object]:
        if self._aprovar_plano is None:
            raise RecusaDaCentral("A aprovação no plano não está disponível nesta Central.")
        try:
            return self._aprovar_plano(run_id, aprovar, autor_do_gesto(operador_atual()), vista_em)
        except PortaIndisponivel as exc:
            if exc.codigo == "plano_mudou":
                previa = exc.extra.get("previa")
                mudaram = exc.extra.get("mudaram")
                raise PlanoMudou(exc.mensagem, dict(previa) if isinstance(previa, Mapping) else {},
                                 [dict(m) for m in mudaram if isinstance(m, Mapping)]
                                 if isinstance(mudaram, list) else []) from None
            raise RecusaDaCentral(exc.mensagem, exc.codigo) from None

    def iniciar(self, run_id: str) -> None:
        try:
            self.runs.start(run_id, por=autor_do_gesto(operador_atual()))
        except RunError as exc:
            raise RecusaDaCentral(exc.message) from None

    def cancelar(self, run_id: str, *, gesto: bool = True) -> None:
        """Só a execução ainda `planned` (compare-and-set no serviço): a que outro gesto já iniciou segue. `gesto=False`
        (a faxina do plano esquecido, 28.38): `por=None`, e o serviço não grava o sinal `cancelou_execucao`."""
        try:
            self.runs.cancel(run_id, por=autor_do_gesto(operador_atual()) if gesto else None, so_se_planejada=True)
        except RunError as exc:
            raise RecusaDaCentral(exc.message) from None

    def imagem_da_etapa(self, run_id: str, step_id: str) -> tuple[bytes, str] | None:
        """A imagem que a etapa publica (`image_id` nos argumentos), lida do armazém dos avatares. O mime não é declarado:
        quem envia confere a assinatura dos bytes; quem chama confere o sha256 contra o da prévia."""
        if self._ler_imagem is None:
            return None
        bindings = loads(self.db.scalar("SELECT bindings FROM steps WHERE id=? AND run_id=?", (step_id, run_id)), {})
        imagem = str((bindings or {}).get(ARGUMENTO_DA_IMAGEM) or "").strip()
        if not imagem:
            return None
        chave = self.db.scalar("SELECT storage_key FROM persona_images WHERE id=? AND status='ready'", (imagem,))
        conteudo = self._ler_imagem(str(chave)) if chave else None
        return (conteudo, "application/octet-stream") if conteudo else None

    def decidir(self, approval_id: str, verbo: str, nota: str | None = None) -> str:
        try:
            self.aprovacoes.decide(approval_id, verbo, note=(nota or None) and nota[:400])
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
            raise RecusaDaCentral(exc.message, exc.code) from None
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


def nomes_de_persona(db: Database) -> list[str]:
    """Nome de exibição, primeiro e último nome e @ das personas: o que a conversa (28.28) e o aviso (28.31) tiram de
    todo texto que mandam pelo canal. Inclui as aposentadas: o nome continua sendo de uma pessoa da plataforma."""
    nomes: list[str] = []
    for r in db.query("SELECT display_name, first_name, last_name, username FROM instagram_profiles"):
        nomes.extend(str(v) for v in (r["display_name"], r["first_name"], r["last_name"], r["username"]) if v)
    return nomes

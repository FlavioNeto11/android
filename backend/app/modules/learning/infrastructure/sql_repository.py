"""`SqlLearningRepository`: as tabelas da 055 (ADR-054), nos dois dialetos.

Toda mudança de estado é UMA transação com CAS no estado (`UPDATE … WHERE id=? AND state=?`), como no
`SqlSkillRepository`: duas sessões decidindo ao mesmo tempo não decidem as duas. E o D1 tem aqui a segunda camada,
nas duas portas por onde um estado entra: quando quem decide é o sistema e o destino é `published`, o próprio `UPDATE`
exige `side_effect=0 AND human_origin=0`; e o item que o sistema CRIA já num estado passa por `conferir_nascimento`
antes do `INSERT` (nem publicado com efeito ou texto de pessoa, nem validado com texto de pessoa) — mesmo que o
domínio deixasse passar, o banco não publica o que só o dono publica.

Receita e fluxo só têm o `status` tocado (o precedente é a adoção das habilidades), sempre com a trilha na mesma
transação. As guardas que valiam na rota antiga continuam valendo aqui: nunca duas receitas ativas na mesma chave,
e fluxo adotado por habilidade publicada (ou com o mesmo comando publicado) não se religa.

Escritas idempotentes por chave única: sinal (`kind, source_ref, created_by`), evidência (`item_ref, origin_ref,
stance`) e o agregado diário, recalculado por inteiro (DELETE do intervalo + INSERT) numa transação.
"""
from __future__ import annotations

import secrets
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.db import INTEGRITY_ERRORS, Database, Row
from app.modules.learning.application.ports import (MudancaNativa, NovaEvidencia, NovoSinal, PrimeiraChamada,
                                                    Retencao)
from app.modules.learning.domain.ciclo import (SYSTEM_ACTOR, ConflitoDeEstado, Desligamento, EntradaInvalida,
                                               ExigeODono, NaoEncontrado, SkillState, conferir_nascimento)
from app.modules.learning.domain.efeito import Exposicao
from app.modules.learning.domain.falhas import classificar_falha
from app.modules.learning.domain.livro import (Escopo, ItemDeAprendizado, NovoItem, Transicao, fluxo_tem_efeito,
                                               receita_tem_efeito, ref_da_trilha)
from app.modules.learning.domain.promocao import Evidencia
from app.modules.learning.domain.vocabulario import (SINAIS_DE_INTERVENCAO, Braco, LivroKind, Polaridade, Posicao,
                                                     SignalKind, SourceKind)
from app.modules.learning.infrastructure import linhas
from app.modules.skills.domain.document import canonical_json
from app.planning import costs
from app.taskqueue.projecao import app_da_etapa
from app.util import now_iso, parse_iso, to_iso

_VIVOS = ("candidate", "validated", "published")
#: Status de etapa que fecham o desfecho: a última tentativa de uma etapa assim conta a etapa na régua diária.
_ETAPA_FECHADA = frozenset({"succeeded", "failed", "uncertain", "waiting_user", "cancelled", "skipped"})


class SqlLearningRepository:
    def __init__(self, db: Database, *, clock: Callable[[], str] | None = None,
                 guarda_do_fluxo: Callable[[str], str | None] | None = None,
                 precos: Callable[[], dict[str, list[float]]] | None = None) -> None:
        """`guarda_do_fluxo(flow_id)`: por que o fluxo NÃO pode ser religado agora (adotado por habilidade publicada,
        comando publicado em outra), ou `None`. A composição a monta sobre o repositório das habilidades; conferida
        dentro da transação. `precos`: a tabela de `ai.prices`, para o US$ das chamadas antigas sem `usd` gravado."""
        self._db = db
        self._clock = clock if clock is not None else now_iso
        self._guarda_do_fluxo = guarda_do_fluxo
        self._precos = precos if precos is not None else dict

    # ================================================================== itens
    def item(self, item_id: str) -> ItemDeAprendizado | None:
        row = self._db.one("SELECT * FROM learning_items WHERE id=?", (item_id,))
        return _item(row) if row else None

    def itens(self, *, kind: LivroKind | None = None, state: SkillState | None = None) -> list[ItemDeAprendizado]:
        sql, params = "SELECT * FROM learning_items WHERE 1=1", []
        if kind is not None:
            sql += " AND kind=?"
            params.append(kind.value)
        if state is not None:
            sql += " AND state=?"
            params.append(state.value)
        return [_item(r) for r in self._db.query(sql + " ORDER BY created_at, id", tuple(params))]

    def item_vivo(self, novo: NovoItem) -> ItemDeAprendizado | None:
        e = novo.escopo
        row = self._db.one(
            "SELECT * FROM learning_items WHERE kind=? AND scope_app=? AND scope_capability=? AND scope_step_hash=?"
            " AND scope_role=? AND scope_profile_id=? AND content_hash=? AND state IN (?,?,?)",
            (novo.kind.value, e.app, e.capability, e.step_hash, e.role, e.profile_id, novo.content_hash, *_VIVOS))
        return _item(row) if row else None

    def criar_item(self, novo: NovoItem, *, by: str, estado: SkillState, detalhe: str | None, reason: str,
                   run_id: str | None = None) -> ItemDeAprendizado:
        if not by.strip():
            raise EntradaInvalida("O item precisa dizer quem o criou ('sistema' ou a pessoa).")
        # D1, segunda camada no nascimento: a porta expõe `estado`, e um minerador não publica passando por aqui.
        conferir_nascimento(estado, by, side_effect=novo.side_effect, human_origin=novo.human_origin)
        agora = self._clock()
        item_id = f"li-{secrets.token_hex(8)}"
        e = novo.escopo
        try:
            with self._db.tx():
                self._db.execute(
                    "INSERT INTO learning_items(id, kind, state, state_detail, scope_app, scope_capability,"
                    " scope_step_hash, scope_role, scope_profile_id, app_version, side_effect, human_origin, content,"
                    " content_hash, summary, tokens, source_kind, provenance, parent_id, created_by, created_at,"
                    " updated_at, state_at, state_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item_id, novo.kind.value, estado.value, detalhe, e.app, e.capability, e.step_hash, e.role,
                     e.profile_id, novo.app_version, int(novo.side_effect), int(novo.human_origin),
                     canonical_json(novo.content), novo.content_hash, novo.summary, novo.tokens,
                     novo.source_kind.value, canonical_json(novo.provenance), novo.parent_id, by, agora, agora, agora,
                     by))
                self._registrar(item_id, novo.kind, novo.content_hash, e.chave(novo.kind), novo.app_version, None,
                                estado, reason, by, agora, run_id)
        except INTEGRITY_ERRORS as exc:
            raise ConflitoDeEstado("O mesmo conteúdo já está vivo neste escopo (outra sessão chegou antes).") from exc
        criado = self.item(item_id)
        assert criado is not None
        return criado

    def transicionar_item(self, item: ItemDeAprendizado, para: SkillState, *, by: str, reason: str,
                          detalhe: str | None = None, run_id: str | None = None) -> ItemDeAprendizado:
        if not by.strip() or not reason.strip():
            raise EntradaInvalida("Transição sem quem decidiu ou sem motivo não entra na trilha.")
        agora = self._clock()
        sql = ("UPDATE learning_items SET state=?, state_detail=?, state_at=?, state_by=?, updated_at=?"
               " WHERE id=? AND state=?")
        if by == SYSTEM_ACTOR and para is SkillState.PUBLISHED:
            sql += " AND side_effect=0 AND human_origin=0"      # D1, segunda camada: o banco não publica pelo sistema
        try:
            with self._db.tx():
                cur = self._db.execute(sql, (para.value, detalhe, agora, by, agora, item.id, item.state.value))
                if int(cur.rowcount or 0) != 1:
                    atual = self.item(item.id)
                    if atual is not None and atual.state is item.state and atual.requires_owner:
                        raise ExigeODono(f"{item.id}: publicar é decisão do dono (D1); o sistema não publica.")
                    raise ConflitoDeEstado(f"{item.id} mudou de estado durante a transição; releia e tente de novo.")
                self._registrar(item.id, item.kind, item.content_hash, item.escopo.chave(item.kind),
                                item.app_version, item.state, para, reason, by, agora, run_id)
        except INTEGRITY_ERRORS as exc:
            raise ConflitoDeEstado(f"{item.id}: já há um item vivo com o mesmo conteúdo neste escopo.") from exc
        movido = self.item(item.id)
        assert movido is not None
        return movido

    def mudar_detalhe(self, item: ItemDeAprendizado, detalhe: str, *, by: str, reason: str,
                      app_version: str | None = None, run_id: str | None = None) -> ItemDeAprendizado:
        """Muda só o `state_detail` (em prova, fila, medida…), sem mudar o estado — CAS no estado E no detalhe lido,
        com a trilha (`de = para`, o motivo carrega o detalhe). `state_at` passa a ser este instante: é o começo da
        prova (as exposições de antes não entram no veredito) e o "há quantos dias" da medida. `app_version`, quando
        vem, é a versão observada que abriu a prova de novo."""
        if not by.strip() or not reason.strip():
            raise EntradaInvalida("Mudança de detalhe sem quem decidiu ou sem motivo não entra na trilha.")
        agora = self._clock()
        with self._db.tx():
            cur = self._db.execute(
                "UPDATE learning_items SET state_detail=?, state_at=?, state_by=?, updated_at=?,"
                " app_version=COALESCE(?, app_version) WHERE id=? AND state=? AND COALESCE(state_detail, '')=?",
                (detalhe, agora, by, agora, app_version, item.id, item.state.value, item.state_detail or ""))
            if int(cur.rowcount or 0) != 1:
                raise ConflitoDeEstado(f"{item.id} mudou durante a mudança de detalhe; releia e tente de novo.")
            self._registrar(item.id, item.kind, item.content_hash, item.escopo.chave(item.kind),
                            app_version or item.app_version, item.state, item.state, f"{detalhe}: {reason.strip()}",
                            by, agora, run_id)
        movido = self.item(item.id)
        assert movido is not None
        return movido

    # ================================================================== receita e fluxo (só o status)
    def transicionar_nativo(self, mudanca: MudancaNativa, *, by: str, reason: str, run_id: str | None = None) -> None:
        if not by.strip() or not reason.strip():
            raise EntradaInvalida("Transição sem quem decidiu ou sem motivo não entra na trilha.")
        m = mudanca
        agora = self._clock()
        with self._db.tx():
            if m.kind is LivroKind.RECEITA:
                self._mover_receita(m, by=by)
            elif m.kind is LivroKind.FLUXO:
                self._mover_fluxo(m, by=by)
            else:
                raise EntradaInvalida(f"{m.kind.value} não tem status movido pelo livro.")
            self._registrar(ref_da_trilha(m.kind, m.ref), m.kind, m.content_hash, m.scope_key, m.app_version,
                            m.de_estado, m.para_estado, reason, by, agora, run_id)

    def _mover_receita(self, m: MudancaNativa, *, by: str) -> None:
        try:
            recipe_id = int(m.ref)
        except ValueError as exc:
            raise NaoEncontrado(f"Receita '{m.ref}' não existe.") from exc
        row = self._db.one("SELECT * FROM recipes WHERE id=?", (recipe_id,))
        if row is None:
            raise NaoEncontrado(f"Receita {recipe_id} não existe.")
        if m.para_status == "active":
            if by == SYSTEM_ACTOR and receita_tem_efeito(linhas.json_legado(linhas.texto(row, "actions"))):
                raise ExigeODono(f"Receita {recipe_id} tem ação de efeito externo: ativar é decisão do dono (D1).")
            outra = self._db.one(
                "SELECT id FROM recipes WHERE app_package=? AND app_version=? AND app_signature=? AND variant=?"
                " AND step_hash=? AND status='active' AND id<>?",
                (row["app_package"], row["app_version"], row["app_signature"], row["variant"], row["step_hash"],
                 recipe_id))
            if outra is not None:
                raise ConflitoDeEstado(f"A receita {outra['id']} já está ativa nesta etapa: nunca duas ativas por "
                                       "chave. Desligue a outra antes.")
        sql = "UPDATE recipes SET status=?" + (", consecutive_fail=0" if m.para_status == "active" else "")
        cur = self._db.execute(sql + " WHERE id=? AND status=?", (m.para_status, recipe_id, m.de_status))
        if int(cur.rowcount or 0) != 1:
            raise ConflitoDeEstado(f"Receita {recipe_id} mudou de status durante a transição; releia e tente de novo.")

    def _mover_fluxo(self, m: MudancaNativa, *, by: str) -> None:
        row = self._db.one("SELECT * FROM flows WHERE id=?", (m.ref,))
        if row is None:
            raise NaoEncontrado(f"Fluxo '{m.ref}' não existe.")
        if m.para_status == "active":
            if by == SYSTEM_ACTOR and fluxo_tem_efeito(linhas.json_legado(linhas.texto(row, "plan"))):
                raise ExigeODono(f"Fluxo {m.ref} tem etapa de efeito externo: publicar é decisão do dono (D1).")
            if self._guarda_do_fluxo is not None and (motivo := self._guarda_do_fluxo(m.ref)) is not None:
                raise ConflitoDeEstado(motivo)
        cur = self._db.execute("UPDATE flows SET status=? WHERE id=? AND status=?", (m.para_status, m.ref, m.de_status))
        if int(cur.rowcount or 0) != 1:
            raise ConflitoDeEstado(f"Fluxo {m.ref} mudou de status durante a transição; releia e tente de novo.")

    # ================================================================== trilha
    def _registrar(self, item_ref: str, kind: LivroKind, content_hash: str | None, scope_key: str,
                   app_version: str | None, de: SkillState | None, para: SkillState, reason: str, by: str, at: str,
                   run_id: str | None) -> None:
        if not by.strip():
            raise EntradaInvalida("A trilha nunca fica sem quem decidiu.")
        self._db.execute(
            "INSERT INTO learning_transitions(item_ref, item_kind, content_hash, scope_key, app_version, from_state,"
            " to_state, reason, decided_by, decided_at, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (item_ref, kind.value, content_hash, scope_key, app_version, de.value if de else None, para.value,
             reason.strip()[:500], by, at, run_id))

    def trilha(self, item_ref: str) -> list[Transicao]:
        return [_transicao(r) for r in self._db.query(
            "SELECT * FROM learning_transitions WHERE item_ref=? ORDER BY id", (item_ref,))]

    def desligamentos(self, content_hash: str, scope_key: str) -> list[Desligamento]:
        """TODAS as decisões sobre o mesmo conteúdo no mesmo escopo: o veto olha a mais recente."""
        return [Desligamento(to_state=SkillState(linhas.texto(r, "to_state")),
                             decided_by=linhas.texto(r, "decided_by"), decided_at=linhas.texto(r, "decided_at"),
                             app_version=linhas.texto_ou_nulo(r, "app_version"))
                for r in self._db.query("SELECT to_state, decided_by, decided_at, app_version FROM learning_transitions"
                                        " WHERE content_hash=? AND scope_key=? ORDER BY id", (content_hash, scope_key))]

    def refs_decididas_por_pessoa(self, kinds: Sequence[LivroKind]) -> frozenset[str]:
        if not kinds:
            return frozenset()
        marcas = ",".join("?" for _ in kinds)
        return frozenset(linhas.texto(r, "item_ref") for r in self._db.query(
            f"SELECT DISTINCT item_ref FROM learning_transitions WHERE item_kind IN ({marcas}) AND decided_by<>?",
            (*(k.value for k in kinds), SYSTEM_ACTOR)))

    # ================================================================== evidência
    def evidencias(self, item_ref: str, *, limite: int = 200) -> list[Evidencia]:
        return [_evidencia(r) for r in self._db.query(
            "SELECT * FROM learning_evidence WHERE item_ref=? ORDER BY id DESC LIMIT ?", (item_ref, int(limite)))]

    def registrar_evidencia(self, nova: NovaEvidencia) -> bool:
        """Idempotente pela chave (item, origem, posição). No item do livro, soma os contadores em cache — o total
        sobrevive à retenção, que guarda só as linhas mais recentes."""
        agora = self._clock()
        with self._db.tx():
            novo_run = novo_aparelho = False
            conta = nova.item_ref.startswith("li-") and not nova.simulated
            if conta and nova.stance is Posicao.FOR:
                novo_run = bool(nova.run_id) and self._db.one(
                    "SELECT 1 AS x FROM learning_evidence WHERE item_ref=? AND stance='for' AND simulated=0"
                    " AND run_id=?", (nova.item_ref, nova.run_id)) is None
                novo_aparelho = bool(nova.instance_id) and self._db.one(
                    "SELECT 1 AS x FROM learning_evidence WHERE item_ref=? AND stance='for' AND simulated=0"
                    " AND instance_id=?", (nova.item_ref, nova.instance_id)) is None
            inserida = self._db.one(
                "INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, instance_id, app_version,"
                " simulated, detail, observed_at) VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT (item_ref, origin_ref, stance) DO NOTHING RETURNING id",
                (nova.item_ref, nova.stance.value, nova.origin_ref, nova.run_id, nova.instance_id, nova.app_version,
                 int(nova.simulated), (nova.detail or "")[:200] or None, agora))
            if inserida is None:
                return False
            if conta:
                favor = int(nova.stance is Posicao.FOR)
                self._db.execute(
                    "UPDATE learning_items SET evidence_for=evidence_for+?, evidence_against=evidence_against+?,"
                    " distinct_runs=distinct_runs+?, distinct_devices=distinct_devices+?, updated_at=? WHERE id=?",
                    (favor, 1 - favor, int(novo_run), int(novo_aparelho), agora, nova.item_ref))
        return True

    # ================================================================== exposições (lições, A7)
    def exposicoes(self, item_id: str, *, desde: str | None = None, limite: int = 500) -> list[Exposicao]:
        """As exposições de uma lição, as mais antigas primeiro (a ordem do veredito); `desde`: o começo da prova."""
        sql, params = "SELECT * FROM learning_exposures WHERE item_id=?", [item_id]
        if desde is not None:
            sql += " AND created_at >= ?"
            params.append(desde)
        return [exposicao_da_linha(r) for r in self._db.query(sql + " ORDER BY created_at, unit_id LIMIT ?",
                                                                (*params, int(limite)))]

    # ================================================================== sinais
    def registrar_sinal(self, sinal: NovoSinal, *, substituir: bool = False,
                        um_por_evento: bool = False) -> int | None:
        """INSERT idempotente pela chave `(kind, source_ref, created_by)`. `substituir`: o voto troca (upsert por
        pessoa e por item); sem ele, a varredura sem cursor não duplica nada e devolve `None` no repetido.

        `um_por_evento` (o gesto de uma pessoa, ADR-054): a chave é `(kind, source_ref)`, qualquer que seja o autor —
        o `source_ref` do gesto é a identidade do EVENTO, e o mesmo evento feito de novo por outra pessoa não vira
        segunda linha (a régua conta linhas); o primeiro autor fica e o repetido devolve `None`. Leitura e escrita na
        mesma transação, sob a trava do `Database`: duas threads do processo não gravam as duas. Entre PROCESSOS só
        um índice único parcial em `(kind, source_ref)` garantiria — hoje o backend é um processo só."""
        s = sinal
        conflito = ("DO UPDATE SET polarity=excluded.polarity, verdict=excluded.verdict, reason=excluded.reason,"
                    " note=excluded.note, note_refused=excluded.note_refused, data=excluded.data,"
                    " step_verified=excluded.step_verified, updated_at=excluded.created_at"
                    if substituir else "DO NOTHING")
        if um_por_evento:
            with self._db.tx():
                if self._db.one("SELECT 1 FROM learning_signals WHERE kind=? AND source_ref=? LIMIT 1",
                                (s.kind.value, s.source_ref)) is not None:
                    return None
                return self._inserir_sinal(s, conflito)
        return self._inserir_sinal(s, conflito)

    def _inserir_sinal(self, s: NovoSinal, conflito: str) -> int | None:
        row = self._db.one(
            "INSERT INTO learning_signals(kind, polarity, verdict, reason, note, note_refused, source_ref, created_by,"
            " run_id, objective_id, step_id, attempt_id, instance_id, profile_id, app_package, capability, step_hash,"
            " failure_kind, step_verified, data, simulated, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            f" ON CONFLICT (kind, source_ref, created_by) {conflito} RETURNING id",
            (s.kind.value, s.polarity.value, s.verdict, s.reason, s.note, int(s.note_refused), s.source_ref,
             s.created_by, s.run_id, s.objective_id, s.step_id, s.attempt_id, s.instance_id, s.profile_id,
             s.app_package, s.capability, s.step_hash, s.failure_kind,
             None if s.step_verified is None else int(s.step_verified), canonical_json(s.data), int(s.simulated),
             self._clock()))
        return linhas.inteiro(row, "id") if row else None

    # ================================================================== régua diária durável
    def recalcular_diario(self, desde_dia: str, ate_dia: str) -> int:
        """Recalcula `learning_daily` POR INTEIRO em [desde_dia, ate_dia). Só execuções reais. Quem chama garante
        que os dias ainda estão intactos (nenhuma chamada deles purgada)."""
        grupos = self._agregar(desde_dia, ate_dia)
        agora = self._clock()
        with self._db.tx():
            self._db.execute("DELETE FROM learning_daily WHERE day>=? AND day<?", (desde_dia, ate_dia))
            for (dia, pacote, acao, tipo, conduzida), g in sorted(grupos.items()):
                self._db.execute(
                    "INSERT INTO learning_daily(day, app_package, capability, failure_kind, driven_by, attempts, steps,"
                    " ai_calls, usd, seconds, interventions, human_negative, computed_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (dia, pacote, acao, tipo, conduzida, g.attempts, g.steps, g.ai_calls, round(g.usd, 6),
                     round(g.seconds, 3), g.interventions, g.human_negative, agora))
        return len(grupos)

    def dias_com_diario(self, desde_dia: str, ate_dia: str) -> frozenset[str]:
        return frozenset(linhas.texto(r, "day") for r in self._db.query(
            "SELECT DISTINCT day FROM learning_daily WHERE day>=? AND day<?", (desde_dia, ate_dia)))

    def primeira_chamada(self) -> PrimeiraChamada | None:
        """A chamada de IA mais antiga que sobrou, e se há sinal de purga antes dela: uma tentativa REAL que terminou
        antes dela. Sem esse sinal, nada foi purgado ainda (a primeira chamada é a primeira de verdade).

        Heurística conservadora: num banco nunca purgado cuja primeira tentativa real terminou SEM chamada de IA (ex.:
        sessão de automação indisponível no primeiro dia), o dia da primeira chamada fica de fora da régua — no máximo
        um dia, e só até ele sair da janela de retenção."""
        row = self._db.one("SELECT MIN(ts) AS primeira FROM ai_calls")
        bruto = linhas.texto_ou_nulo(row, "primeira") if row else None
        quando = parse_iso(bruto) if bruto else None
        if bruto is None or quando is None:
            return None
        antes = self._db.one("SELECT 1 AS x FROM attempts a JOIN steps s ON s.id = a.step_id JOIN runs r"
                             " ON r.id = s.run_id WHERE r.simulated = 0 AND a.finished_at < ? LIMIT 1", (bruto,))
        return PrimeiraChamada(quando, purgada_antes=antes is not None)

    def _agregar(self, desde: str, ate: str) -> dict[tuple[str, str, str, str, str], _Grupo]:
        pacotes = {linhas.texto(r, "id"): linhas.texto_ou_nulo(r, "package") or linhas.texto(r, "id")
                   for r in self._db.query("SELECT id, package FROM apps")}
        tentativas = self._db.query(
            "SELECT a.id, a.step_id, a.number, a.status, a.error, a.failure_kind, a.started_at, a.finished_at,"
            " s.capability, s.app_id, s.driven_by, s.status AS step_status, r.app_ids,"
            " (SELECT MAX(x.number) FROM attempts x WHERE x.step_id = a.step_id) AS ultima"
            " FROM attempts a JOIN steps s ON s.id = a.step_id JOIN runs r ON r.id = s.run_id"
            " WHERE r.simulated = 0 AND a.finished_at >= ? AND a.finished_at < ?", (desde, ate))
        chamadas = self._chamadas(tentativas, desde, ate)
        grupos: dict[tuple[str, str, str, str, str], _Grupo] = defaultdict(_Grupo)
        for t in tentativas:
            fim = linhas.texto(t, "finished_at")
            app = app_da_etapa(t["app_id"], t["app_ids"])
            tipo = linhas.texto_ou_nulo(t, "failure_kind") or classificar_falha(
                linhas.texto_ou_nulo(t, "error"), linhas.texto_ou_nulo(t, "status"))
            chave = (fim[:10], pacotes.get(app, app), linhas.texto_ou_nulo(t, "capability") or "*",
                     str(tipo) if tipo else "", linhas.texto_ou_nulo(t, "driven_by") or "")
            g = grupos[chave]
            g.attempts += 1
            n, usd = chamadas.get(linhas.texto(t, "id"), (0, 0.0))
            g.ai_calls += n
            g.usd += usd
            g.seconds += _duracao(linhas.texto_ou_nulo(t, "started_at"), fim)
            if t["ultima"] is not None and linhas.inteiro(t, "number") == int(t["ultima"]):
                estado_da_etapa = linhas.texto(t, "step_status")
                g.steps += int(estado_da_etapa in _ETAPA_FECHADA)
                g.interventions += int(estado_da_etapa == "waiting_user")
        for s in self._db.query(
                "SELECT kind, polarity, created_by, app_package, capability, failure_kind, created_at"
                " FROM learning_signals WHERE simulated=0 AND created_at >= ? AND created_at < ?", (desde, ate)):
            intervencao = linhas.texto(s, "kind") in {k.value for k in SINAIS_DE_INTERVENCAO}
            negativo = (linhas.texto(s, "polarity") == Polaridade.NEGATIVE.value
                        and linhas.texto(s, "created_by") != SYSTEM_ACTOR)
            if not (intervencao or negativo):
                continue
            chave = (linhas.texto(s, "created_at")[:10], linhas.texto(s, "app_package") or "*",
                     linhas.texto(s, "capability") or "*", linhas.texto_ou_nulo(s, "failure_kind") or "", "")
            g = grupos[chave]
            g.interventions += int(intervencao)
            g.human_negative += int(negativo)
        return dict(grupos)

    def _chamadas(self, tentativas: list[Row], desde: str, ate: str) -> dict[str, tuple[int, float]]:
        """Chamadas de IA por tentativa. A dona de cada chamada é a MESMA qualquer que seja o intervalo calculado, e
        ela só conta se terminou neste intervalo — senão o dia recalculado em separado do vizinho somava de novo a
        chamada de uma tentativa que terminou no dia anterior (etapa atravessando 00:00 UTC), e `learning_daily` não
        se corrige depois que `ai_calls` é purgado:
        - com `ai_calls.attempt_id` (045): aquela tentativa, e nunca a etapa;
        - chamada antiga, só com `step_id` (anterior à 045): a ÚLTIMA tentativa da etapa (`MAX(number)` de todas, não
          a última da janela, que muda com o intervalo).
        US$ gravado (048) quando há; senão, pelos tokens e a tabela de preços."""
        ids = {linhas.texto(t, "id") for t in tentativas}
        ultima_da_etapa = {linhas.texto(t, "step_id"): linhas.texto(t, "id") for t in tentativas
                           if linhas.inteiro(t, "number") == linhas.inteiro_ou_nulo(t, "ultima")}
        inicio = (datetime.strptime(desde, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        precos = self._precos()
        saida: dict[str, tuple[int, float]] = {}
        for c in self._db.query(
                "SELECT attempt_id, step_id, model, input_tokens, cache_read, cache_write, output_tokens, usd"
                " FROM ai_calls WHERE ts >= ? AND ts < ?", (inicio, ate)):
            dona = linhas.texto_ou_nulo(c, "attempt_id")
            if dona is None:                            # só a chamada anterior à 045 cai na etapa
                etapa = linhas.texto_ou_nulo(c, "step_id")
                dona = ultima_da_etapa.get(etapa) if etapa is not None else None
            if dona is None or dona not in ids:
                continue                                # a tentativa é de outro intervalo: conta lá, não aqui também
            if c["usd"] is not None:
                usd = linhas.real(c, "usd")
            else:
                usd = costs.usd(precos, linhas.texto(c, "model"),
                                [linhas.real(c, k) for k in ("input_tokens", "cache_read", "cache_write",
                                                             "output_tokens")])
            n, total = saida.get(dona, (0, 0.0))
            saida[dona] = (n + 1, total + usd)
        return saida

    # ================================================================== retenção
    def aplicar_retencao(self, retencao: Retencao, agora: datetime) -> int:
        """`aprendizado.retencao`. Nunca purgados: itens publicados, aposentados e desligados, a trilha e o backlog."""
        def antes(dias: int) -> str:
            return to_iso(agora - timedelta(days=dias))

        total = 0
        with self._db.tx():
            total += _n(self._db.execute("DELETE FROM learning_signals WHERE kind<>? AND created_at < ?",
                                         (SignalKind.FEEDBACK.value, antes(retencao.sinais_dias))))
            total += _n(self._db.execute("DELETE FROM learning_signals WHERE kind=? AND created_at < ?",
                                         (SignalKind.FEEDBACK.value, antes(retencao.feedback_dias))))
            total += _n(self._db.execute("DELETE FROM learning_exposures WHERE COALESCE(filled_at, created_at) < ?",
                                         (antes(retencao.exposicoes_dias),)))
            total += _n(self._db.execute("DELETE FROM learning_daily WHERE day < ?",
                                         (antes(retencao.diario_dias)[:10],)))
            total += _n(self._db.execute(
                "DELETE FROM learning_evidence WHERE id IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER"
                " (PARTITION BY item_ref ORDER BY id DESC) AS posicao FROM learning_evidence) mais_antigas"
                " WHERE posicao > ?)", (int(retencao.evidencias_por_item),)))
            velhas = [linhas.texto(r, "id") for r in self._db.query(
                "SELECT i.id FROM learning_items i WHERE i.state='candidate' AND COALESCE((SELECT MAX(e.observed_at)"
                " FROM learning_evidence e WHERE e.item_ref = i.id), i.created_at) < ?",
                (antes(retencao.candidata_sem_evidencia_dias),))]
            for item_id in velhas:            # a trilha fica: é ela que guarda que a candidata existiu
                self._db.execute("DELETE FROM learning_evidence WHERE item_ref=?", (item_id,))
                total += _n(self._db.execute("DELETE FROM learning_items WHERE id=? AND state='candidate'",
                                             (item_id,)))
        return total


# ------------------------------------------------------------------ apoio
@dataclass(slots=True)
class _Grupo:
    attempts: int = 0
    steps: int = 0
    ai_calls: int = 0
    usd: float = 0.0
    seconds: float = 0.0
    interventions: int = 0
    human_negative: int = 0


def _n(cursor: object) -> int:
    contagem = getattr(cursor, "rowcount", 0)
    return contagem if isinstance(contagem, int) and contagem > 0 else 0


def _duracao(inicio: str | None, fim: str) -> float:
    if not inicio:
        return 0.0
    try:
        a = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
        b = datetime.fromisoformat(fim.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (b - a).total_seconds())


def _item(row: Row) -> ItemDeAprendizado:
    kind = LivroKind(linhas.texto(row, "kind"))
    return ItemDeAprendizado(
        id=linhas.texto(row, "id"), kind=kind, state=SkillState(linhas.texto(row, "state")),
        state_detail=linhas.texto_ou_nulo(row, "state_detail"),
        escopo=Escopo(app=linhas.texto(row, "scope_app"), capability=linhas.texto(row, "scope_capability"),
                      step_hash=linhas.texto(row, "scope_step_hash"), role=linhas.texto(row, "scope_role"),
                      profile_id=linhas.texto(row, "scope_profile_id")),
        app_version=linhas.texto_ou_nulo(row, "app_version"), side_effect=bool(linhas.inteiro(row, "side_effect")),
        human_origin=bool(linhas.inteiro(row, "human_origin")), content=linhas.json_objeto(row, "content"),
        content_hash=linhas.texto(row, "content_hash"), summary=linhas.texto(row, "summary"),
        tokens=linhas.inteiro_ou_nulo(row, "tokens"), source_kind=SourceKind(linhas.texto(row, "source_kind")),
        provenance=linhas.json_objeto(row, "provenance"), evidence_for=linhas.inteiro(row, "evidence_for"),
        evidence_against=linhas.inteiro(row, "evidence_against"), distinct_runs=linhas.inteiro(row, "distinct_runs"),
        distinct_devices=linhas.inteiro(row, "distinct_devices"), parent_id=linhas.texto_ou_nulo(row, "parent_id"),
        created_by=linhas.texto(row, "created_by"), created_at=linhas.texto(row, "created_at"),
        updated_at=linhas.texto_ou_nulo(row, "updated_at"), state_at=linhas.texto_ou_nulo(row, "state_at"),
        state_by=linhas.texto_ou_nulo(row, "state_by"), last_used_at=linhas.texto_ou_nulo(row, "last_used_at"))


#: O mapeamento de linha de `learning_items`, para os repositórios dos pacotes (A7) que consultam a mesma tabela.
item_da_linha = _item


def exposicao_da_linha(row: Row) -> Exposicao:
    replanejou = linhas.inteiro_ou_nulo(row, "replanned")
    return Exposicao(item_id=linhas.texto(row, "item_id"), unit_id=linhas.texto(row, "unit_id"),
                     role=linhas.texto(row, "role"), arm=Braco(linhas.texto(row, "arm")),
                     tokens=linhas.inteiro(row, "tokens"), run_id=linhas.texto_ou_nulo(row, "run_id"),
                     objective_id=linhas.texto_ou_nulo(row, "objective_id"),
                     app_package=linhas.texto_ou_nulo(row, "app_package"),
                     capability=linhas.texto_ou_nulo(row, "capability"), created_at=linhas.texto(row, "created_at"),
                     outcome=linhas.texto_ou_nulo(row, "outcome"),
                     failure_kind=linhas.texto_ou_nulo(row, "failure_kind"),
                     ai_calls=linhas.inteiro_ou_nulo(row, "ai_calls"),
                     usd=None if row["usd"] is None else linhas.real(row, "usd"),
                     seconds=None if row["seconds"] is None else linhas.real(row, "seconds"),
                     replanned=None if replanejou is None else bool(replanejou),
                     filled_at=linhas.texto_ou_nulo(row, "filled_at"))


def _transicao(row: Row) -> Transicao:
    de = linhas.texto_ou_nulo(row, "from_state")
    return Transicao(id=linhas.inteiro(row, "id"), item_ref=linhas.texto(row, "item_ref"),
                     item_kind=LivroKind(linhas.texto(row, "item_kind")),
                     content_hash=linhas.texto_ou_nulo(row, "content_hash"), scope_key=linhas.texto(row, "scope_key"),
                     app_version=linhas.texto_ou_nulo(row, "app_version"), from_state=SkillState(de) if de else None,
                     to_state=SkillState(linhas.texto(row, "to_state")), reason=linhas.texto(row, "reason"),
                     decided_by=linhas.texto(row, "decided_by"), decided_at=linhas.texto(row, "decided_at"),
                     run_id=linhas.texto_ou_nulo(row, "run_id"))


def _evidencia(row: Row) -> Evidencia:
    return Evidencia(item_ref=linhas.texto(row, "item_ref"), stance=Posicao(linhas.texto(row, "stance")),
                     origin_ref=linhas.texto(row, "origin_ref"), run_id=linhas.texto_ou_nulo(row, "run_id"),
                     instance_id=linhas.texto_ou_nulo(row, "instance_id"),
                     app_version=linhas.texto_ou_nulo(row, "app_version"),
                     simulated=bool(linhas.inteiro(row, "simulated")), detail=linhas.texto_ou_nulo(row, "detail"),
                     observed_at=linhas.texto(row, "observed_at"))


__all__ = ["SqlLearningRepository", "exposicao_da_linha", "item_da_linha"]

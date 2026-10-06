"""A leitura do aprendizado de uma OPERAÇÃO (prova30 A3): junta pelas execuções dela (`runs.operacao_id`, 124) o que o
Livro, a memória da persona e a memória da operação guardaram. Só leitura; as regras ficam em
`domain/aprendizado_da_operacao.py`.

Como cada fonte se liga às execuções `R` da operação:
- direto por `run_id`: `learning_evidence`, `learning_transitions`, `learning_signals`, `social_interactions`;
  `flows.source_run_id`;
- pelo prefixo: `recipes.learned_from_step` começa por `<run_id>:`;
- pelo texto do JSON: `learning_items.provenance` cita a execução (`"execucoes": [...]` ou `"execucao": "..."`);
- `memory_items` pela interação (`interaction_id` → `social_interactions.run_id`);
- `learning_backlog` não guarda a execução: casa por (app, ação, tipo de falha) dos sinais e sai `inferida`.

Texto livre (memória, motivo, comando) sai redigido (`security/redaction.py`) e cortado em `RESUMO_MAX`, como o
relatório de falhas. O simulado fica de fora por padrão. Não lê `operacoes` nem `operacao_alvos` (da Jev): a persona de
cada execução sai de `objectives.profile_id`.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

from app.db import Database, Row
from app.modules.pedidos.domain.aprendizado_da_operacao import Item, confianca_da_persona, confianca_do_livro
from app.modules.pedidos.domain.memoria import Entrada
from app.modules.pedidos.infrastructure.repositorio_memoria import RepositorioDeMemoria
from app.security.redaction import redact

RESUMO_MAX = 200
#: Transições que dizem que algo falhou e foi posto de lado por causa da execução.
QUEDAS = frozenset({"quarantined", "disabled", "deprecated"})


#: `pedido_memoria.tipo` → tipo do item: o fato é conhecimento, a fonte o sustenta; progresso, decisão e pendência são
#: registro da operação (o estado da pesquisa, por exemplo), não conhecimento.
TIPO_DA_MEMORIA = {"descoberta": "fato", "fonte": "fonte"}
#: (a favor, contra, última, execuções) de um item do Livro nas execuções da operação.
Evid = tuple[int, int, str, tuple[str, ...]]


def _resumo(texto: object) -> str:
    return (redact(" ".join(str(texto or "").split())) or "")[:RESUMO_MAX]


def _marcas(n: int) -> str:
    return ",".join("?" * n)


class LeitorDoAprendizadoDaOperacao:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.memoria = RepositorioDeMemoria(db)

    def execucoes(self, operacao_id: str) -> dict[str, str | None] | None:
        """`{run_id: profile_id}`; None se a 124 ainda não chegou ao banco (sem `runs.operacao_id`)."""
        if "operacao_id" not in self.db.columns("runs"):
            return None
        linhas = self.db.query("SELECT r.id, MIN(o.profile_id) AS persona FROM runs r LEFT JOIN objectives o"
                               " ON o.run_id = r.id WHERE r.operacao_id=? GROUP BY r.id ORDER BY r.id", (operacao_id,))
        return {str(r["id"]): (str(r["persona"]) if r["persona"] else None) for r in linhas}

    def ler(self, operacao_id: str, *, simulados: bool = False) -> list[Item] | None:
        """Os itens da operação; None se ela é desconhecida (sem execução e sem memória)."""
        runs = self.execucoes(operacao_id)
        if runs is None:
            return None
        fatos = self.memoria.entradas_da_operacao(operacao_id)
        observacoes = self.memoria.observacoes_da_operacao(operacao_id)
        if not runs and not fatos and not observacoes:
            return None
        itens: list[Item] = []
        itens += self._da_operacao(fatos, observacoes, runs)
        if runs:
            evid = self._evidencias(list(runs), simulados)
            quedas = self._quedas(list(runs))
            itens += self._receitas(runs, evid, quedas)
            itens += self._fluxos(runs, evid, quedas)
            itens += self._licoes(runs, evid, quedas)
            itens += self._persona(runs)
            itens += self._falhas(runs, quedas, simulados)
        return itens

    # ------------------------------------------------------------------ operação
    def _da_operacao(self, fatos: Sequence[Entrada], observacoes: Sequence[Row],
                     runs: dict[str, str | None]) -> list[Item]:
        """Os fatos, as fontes e os registros da memória da operação, e as leituras incertas. A observação `url` da
        pesquisa e a leitura `observado` já estão na entrada que elas sustentam (pela `evidencia`)."""
        itens = [Item(ref=f"{TIPO_DA_MEMORIA.get(e.tipo, 'registro')}:{e.chave}",
                      tipo=TIPO_DA_MEMORIA.get(e.tipo, "registro"), escopo="operacao", resumo=_resumo(e.valor),
                      origem=e.origem, confianca=e.confianca, estado=e.tipo, evidencia=e.evidencia,
                      observado_em=e.atualizada_em or None, frescor_ate=e.frescor_ate) for e in fatos]
        itens += [Item(ref=f"observacao:{o['id']}", tipo="observacao", escopo="operacao", resumo=_resumo(o["trecho"]),
                       origem="leitura", confianca="hipotese", estado=str(o["situacao"]),
                       evidencia=tuple(str(x) for x in (o["id"], o["run_id"]) if x),
                       persona=runs.get(str(o["run_id"])) if o["run_id"] else None, observado_em=o["capturado_em"])
                  for o in observacoes if o["situacao"] != "observado"]
        return itens

    # ------------------------------------------------------------------ Livro
    def _evidencias(self, run_ids: list[str], simulados: bool) -> dict[str, Evid]:
        """Por `item_ref`, o que as execuções da operação disseram dele: a favor, contra, a última e quais execuções."""
        sim = "" if simulados else " AND simulated=0"
        linhas = self.db.query(f"SELECT item_ref, stance, run_id, observed_at FROM learning_evidence WHERE run_id IN"
                               f" ({_marcas(len(run_ids))}){sim}", tuple(run_ids))
        favor: Counter[str] = Counter()
        contra: Counter[str] = Counter()
        ultima: dict[str, str] = {}
        execs: dict[str, set[str]] = defaultdict(set)
        for r in linhas:
            ref = str(r["item_ref"])
            favor[ref] += r["stance"] == "for"
            contra[ref] += r["stance"] == "against"
            ultima[ref] = max(ultima.get(ref, ""), str(r["observed_at"] or ""))
            execs[ref].add(str(r["run_id"]))
        return {ref: (favor[ref], contra[ref], ultima[ref], tuple(sorted(rs))) for ref, rs in execs.items()}

    def _quedas(self, run_ids: list[str]) -> dict[str, Row]:
        """A última transição de cada item decidida numa execução da operação."""
        linhas = self.db.query(f"SELECT item_ref, to_state, reason, run_id, decided_at FROM learning_transitions"
                               f" WHERE run_id IN ({_marcas(len(run_ids))}) ORDER BY decided_at, id", tuple(run_ids))
        return {str(r["item_ref"]): r for r in linhas}

    @staticmethod
    def _favor(ref: str, evid: dict[str, Evid]) -> tuple[int, int, str | None, tuple[str, ...]]:
        a, c, ult, runs = evid.get(ref, (0, 0, "", ()))
        return a, c, ult or None, runs

    def _receitas(self, runs: dict[str, str | None], evid: dict[str, Evid],
                  quedas: dict[str, Row]) -> list[Item]:
        ids = [r[len("receita:"):] for r in evid if r.startswith("receita:")]
        conds = " OR ".join(["learned_from_step LIKE ?"] * len(runs))
        args: list[object] = [f"{r}:%" for r in runs]
        sql = f"SELECT * FROM recipes WHERE ({conds})"
        if ids:
            sql += f" OR CAST(id AS TEXT) IN ({_marcas(len(ids))})"
            args += ids
        itens = []
        for r in self.db.query(sql, tuple(args)):
            ref = f"receita:{r['id']}"
            origem_run = str(r["learned_from_step"] or "").split(":", 1)[0]
            nasceu_aqui = origem_run in runs
            a, c, ult, ev = self._favor(ref, evid)
            estado = str(quedas[ref]["to_state"]) if ref in quedas else str(r["status"])
            itens.append(Item(ref=ref, tipo="receita", escopo="app",
                              resumo=_resumo(f"{r['step_key']} em {r['app_package']} (v{r['version']}, "
                                             f"{r['replay_ok']} ok / {r['replay_fail']} falhas)"),
                              origem="execucao" if nasceu_aqui else "reforco", confianca=confianca_do_livro(estado),
                              estado=estado, evidencia=(origem_run,) if nasceu_aqui and not ev else ev,
                              persona=runs.get(origem_run) if nasceu_aqui else None,
                              observado_em=ult or r["created_at"], a_favor=a, contra=c))
        return itens

    def _fluxos(self, runs: dict[str, str | None], evid: dict[str, Evid],
                quedas: dict[str, Row]) -> list[Item]:
        ids = [r[len("fluxo:"):] for r in evid if r.startswith("fluxo:")]
        sql = f"SELECT * FROM flows WHERE source_run_id IN ({_marcas(len(runs))})"
        args: list[object] = list(runs)
        if ids:
            sql += f" OR id IN ({_marcas(len(ids))})"
            args += ids
        itens = []
        for f in self.db.query(sql, tuple(args)):
            ref = f"fluxo:{f['id']}"
            nasceu_aqui = f["source_run_id"] in runs
            a, c, ult, ev = self._favor(ref, evid)
            estado = str(quedas[ref]["to_state"]) if ref in quedas else str(f["status"])
            itens.append(Item(ref=ref, tipo="fluxo", escopo="processo", resumo=_resumo(f["command_template"]),
                              origem="execucao" if nasceu_aqui else "reforco", confianca=confianca_do_livro(estado),
                              estado=estado, evidencia=(str(f["source_run_id"]),) if nasceu_aqui and not ev else ev,
                              persona=runs.get(str(f["source_run_id"])) if nasceu_aqui else None,
                              observado_em=ult or f["created_at"], a_favor=a, contra=c))
        return itens

    def _licoes(self, runs: dict[str, str | None], evid: dict[str, Evid],
                quedas: dict[str, Row]) -> list[Item]:
        conds = " OR ".join(["provenance LIKE ?"] * len(runs))
        itens = []
        for li in self.db.query(f"SELECT * FROM learning_items WHERE {conds}", tuple(f'%"{r}"%' for r in runs)):
            ref = f"{li['kind']}:{li['id']}"
            a, c, ult, ev = self._favor(ref, evid)
            citadas = tuple(r for r in runs if f'"{r}"' in str(li["provenance"] or ""))
            estado = str(quedas[ref]["to_state"]) if ref in quedas else str(li["state"])
            personas = {runs[r] for r in citadas}
            itens.append(Item(ref=ref, tipo=str(li["kind"]),
                              escopo="processo" if li["scope_capability"] or li["scope_step_hash"] else "app",
                              resumo=_resumo(li["summary"]), origem=str(li["source_kind"] or "execucao"),
                              confianca=confianca_do_livro(estado), estado=estado, evidencia=ev or citadas,
                              persona=personas.pop() if len(personas) == 1 else None,
                              observado_em=ult or li["updated_at"], a_favor=a or int(li["evidence_for"] or 0),
                              contra=c))
        return itens

    # ------------------------------------------------------------------ persona
    def _persona(self, runs: dict[str, str | None]) -> list[Item]:
        inter = self.db.query(f"SELECT id, profile_id, run_id, type, direction, status, occurred_at, app_id FROM"
                              f" social_interactions WHERE run_id IN ({_marcas(len(runs))}) ORDER BY occurred_at, id",
                              tuple(runs))
        itens = [Item(ref=f"interacao:{i['id']}", tipo="interacao", escopo="persona",
                      resumo=_resumo(f"{i['type']} ({i['direction']}, {i['status']}) em {i['app_id'] or 'app'}"),
                      origem="execucao", confianca="confirmado" if i["status"] == "confirmed" else "hipotese",
                      estado=str(i["status"]), evidencia=(str(i["run_id"]),), persona=i["profile_id"],
                      observado_em=i["occurred_at"]) for i in inter]
        por_inter = {str(i["id"]): i for i in inter}
        if por_inter:
            for m in self.db.query(f"SELECT id, profile_id, subject, content, source, interaction_id, confidence,"
                                   f" expires_at, updated_at FROM memory_items WHERE interaction_id IN"
                                   f" ({_marcas(len(por_inter))}) ORDER BY seq", tuple(por_inter)):
                conf = float(m["confidence"]) if m["confidence"] is not None else None
                itens.append(Item(ref=f"memoria:{m['id']}", tipo="memoria", escopo="persona",
                                  resumo=_resumo(m["content"]), origem=str(m["source"] or "observation"),
                                  confianca=confianca_da_persona(conf), estado=f"{conf:.2f}" if conf is not None else "",
                                  evidencia=(f"interacao:{m['interaction_id']}",
                                             str(por_inter[str(m["interaction_id"])]["run_id"])),
                                  persona=m["profile_id"], observado_em=m["updated_at"], frescor_ate=m["expires_at"]))
        return itens

    # ------------------------------------------------------------------ falhas
    def _falhas(self, runs: dict[str, str | None], quedas: dict[str, Row], simulados: bool) -> list[Item]:
        itens = [Item(ref=f"queda:{ref}", tipo="falha", escopo="falha",
                      resumo=_resumo(f"{ref} foi a {t['to_state']}: {t['reason'] or ''}"), origem="transicao",
                      confianca="confirmado", estado=str(t["to_state"]), evidencia=(str(t["run_id"]),),
                      persona=runs.get(str(t["run_id"])), observado_em=t["decided_at"])
                 for ref, t in quedas.items() if t["to_state"] in QUEDAS]
        sim = "" if simulados else " AND simulated=0"
        sinais = self.db.query(f"SELECT id, run_id, profile_id, app_package, capability, failure_kind, reason, kind,"
                               f" created_at FROM learning_signals WHERE run_id IN ({_marcas(len(runs))})"
                               f" AND failure_kind IS NOT NULL{sim} ORDER BY created_at, id", tuple(runs))
        chaves: set[tuple[str, str, str]] = set()
        for s in sinais:
            itens.append(Item(ref=f"sinal:{s['id']}", tipo="falha", escopo="falha",
                              resumo=_resumo(f"{s['capability'] or s['kind']}: {s['failure_kind']}"
                                             f"{' (' + str(s['reason']) + ')' if s['reason'] else ''}"),
                              origem=str(s["kind"]), confianca="confirmado", estado=str(s["failure_kind"]),
                              evidencia=(str(s["run_id"]),), persona=s["profile_id"] or runs.get(str(s["run_id"])),
                              observado_em=s["created_at"]))
            chaves.add((str(s["app_package"] or ""), str(s["capability"] or ""), str(s["failure_kind"])))
        for app, cap, tipo in sorted(chaves):
            for b in self.db.query("SELECT id, title, state, last_seen FROM learning_backlog WHERE app_package=? AND"
                                   " capability=? AND failure_kind=? ORDER BY id", (app, cap, tipo)):
                itens.append(Item(ref=f"backlog:{b['id']}", tipo="falha", escopo="falha", resumo=_resumo(b["title"]),
                                  origem="backlog", confianca="hipotese", estado=str(b["state"]),
                                  observado_em=b["last_seen"], inferida=True))
        return itens

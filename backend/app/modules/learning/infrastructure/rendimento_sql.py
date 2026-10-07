"""O leitor do rendimento do ensino (`GET /api/training/{id}/rendimento`): uma leitura, sem escrita e sem IA.

As regras (o que é uso real, sem IA, caiu na IA) moram em `domain/rendimento.py`; aqui só as consultas. Todas por
igualdade ou `IN`, nos dois dialetos. A régua da liberação (30.81) é a da loja de receitas, recebida pronta.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from app.db import Database, Row, loads
from app.modules.learning.domain.rendimento import (Contagem, RendimentoDaReceita, RendimentoDoEnsino,
                                                    TentativaDaReceita, Uso, VizinhoUsado, da_receita,
                                                    tipo_de_uso)
from app.planning.costs import row_usd

PREFIXO_DO_TREINO = "training:"
#: Quantas tentativas recentes conduzidas pela IA, na mesma etapa, dão o custo médio que a receita evita (31.191).
AMOSTRA_DO_CUSTO = 200


def _uso(r: Row) -> Uso:
    return tipo_de_uso(simulated=r["simulated"], prova_fluxo_id=r["prova_fluxo_id"], idempotency_key=r["idempotency_key"])


def _marcas(n: int) -> str:
    return ",".join("?" * n)


class LeitorDoRendimento:
    def __init__(self, db: Database, *, liberada: Callable[[Row], bool],
                 em_uso_real_desde: Callable[[list[str]], dict[str, str]],
                 precos: Callable[[], dict[str, list[float]]]) -> None:
        self._db = db
        self._liberada = liberada
        self._em_uso_real_desde = em_uso_real_desde
        self._precos = precos

    def ler(self, sessao: str) -> RendimentoDoEnsino | None:
        if self._db.one("SELECT id FROM training_sessions WHERE id=?", (sessao,)) is None:
            return None
        origem = PREFIXO_DO_TREINO + sessao
        fluxo = self._db.one("SELECT id, status, uses, nascido_de_prova, plan, app_id FROM flows WHERE source=?"
                             " ORDER BY created_at DESC, id DESC LIMIT 1", (origem,))
        execucoes = Contagem()
        dados_do_fluxo: dict[str, object] | None = None
        if fluxo is not None:
            for r in self._db.query("SELECT simulated, prova_fluxo_id, idempotency_key FROM runs"
                                    " WHERE flow_id=? OR prova_fluxo_id=?", (fluxo["id"], fluxo["id"])):
                execucoes.somar(_uso(r))
            dados_do_fluxo = {"id": fluxo["id"], "status": fluxo["status"], "uses": int(fluxo["uses"] or 0),
                              "nascido_de_prova": bool(fluxo["nascido_de_prova"]),
                              "em_uso_real_desde": self._em_uso_real_desde([str(fluxo["id"])]).get(str(fluxo["id"]))}
        receitas = self._receitas(origem)
        licoes = tuple({"id": r["id"], "estado": r["state"], "papel": r["scope_role"], "texto": r["summary"]}
                       for r in self._db.query("SELECT id, state, scope_role, summary, provenance FROM learning_items"
                                               " WHERE kind='licao' AND provenance LIKE ? ORDER BY id",
                                               (f'%"{origem}"%',))
                       if loads(r["provenance"], {}).get("sessao") == origem)
        return RendimentoDoEnsino(sessao=sessao, fluxo=dados_do_fluxo, execucoes_do_fluxo=execucoes,
                                  receitas=receitas, licoes=licoes, vizinhos=self._vizinhos(fluxo))

    def da_receita(self, recipe_id: int) -> dict[str, object] | None:
        """31.191 (`GET /api/aprendizado/receitas/{id}/rendimento`): o rendimento de UMA receita, do ensino ou de
        execução, com as reproduções, o último uso e o custo de IA evitado. None: a receita não existe."""
        linha = self._db.one("SELECT * FROM recipes WHERE id=?", (recipe_id,))
        if linha is None:
            return None
        (r,) = self._das([linha])
        origem = str(linha["learned_from_step"] or "")
        medio = self._custo_medio_da_ia(str(linha["step_hash"] or ""))
        return {**r.como_dict(),
                "origem": "ensino" if origem.startswith(PREFIXO_DO_TREINO) else "execucao",
                "sessao": origem[len(PREFIXO_DO_TREINO):] if origem.startswith(PREFIXO_DO_TREINO) else None,
                "reproducoes": {"ok": int(linha["replay_ok"] or 0), "falha": int(linha["replay_fail"] or 0)},
                "custo_medio_ia_por_etapa_usd": None if medio is None else round(medio, 6),
                "custo_evitado_usd": None if medio is None else round(medio * r.sem_ia.real, 6),
                "ultimo_uso_em": linha["last_used_at"] or None}

    def _custo_medio_da_ia(self, step_hash: str) -> float | None:
        """O US$ médio de IA por tentativa conduzida pela IA (sem receita) numa etapa com a MESMA identidade da receita
        (`steps.template_hash`), das chamadas ainda em `ai_calls`, em execução não simulada. As mais recentes
        (`AMOSTRA_DO_CUSTO`). Sem nenhuma: None (sem referência, o custo evitado não é inventado)."""
        if not step_hash:
            return None
        tentativas = [str(t["id"]) for t in self._db.query(
            "SELECT a.id FROM attempts a JOIN steps s ON s.id = a.step_id JOIN runs r ON r.id = s.run_id"
            " WHERE s.template_hash=? AND r.simulated=0 AND (a.strategy IS NULL OR a.strategy NOT LIKE 'recipe%')"
            " ORDER BY a.id DESC LIMIT ?", (step_hash, AMOSTRA_DO_CUSTO))]
        usd = self._usd_por_tentativa(tentativas)
        com_ia = [v for v in usd.values() if v > 0]
        return sum(com_ia) / len(com_ia) if com_ia else None

    def _receitas(self, origem: str) -> tuple[RendimentoDaReceita, ...]:
        return self._das(self._db.query("SELECT * FROM recipes WHERE learned_from_step=? ORDER BY id", (origem,)))

    def _das(self, linhas: Sequence[Row]) -> tuple[RendimentoDaReceita, ...]:
        if not linhas:
            return ()
        ids = [int(r["id"]) for r in linhas]
        tentativas = self._db.query(
            "SELECT a.id, a.recipe_id, a.status, a.strategy, r.simulated, r.prova_fluxo_id, r.idempotency_key"
            " FROM attempts a JOIN steps s ON s.id = a.step_id JOIN runs r ON r.id = s.run_id"
            f" WHERE a.recipe_id IN ({_marcas(len(ids))})", tuple(ids))
        usd = self._usd_por_tentativa([str(t["id"]) for t in tentativas])
        vistas = [TentativaDaReceita(recipe_id=int(t["recipe_id"]), uso=_uso(t), status=str(t["status"] or ""),
                                     strategy=str(t["strategy"] or ""), usd=usd.get(str(t["id"])))
                  for t in tentativas]
        return tuple(da_receita(RendimentoDaReceita(id=int(r["id"]), step_key=str(r["step_key"]),
                                                    app=str(r["app_package"]), status=str(r["status"]),
                                                    liberada=self._liberada(r)), vistas) for r in linhas)

    def _usd_por_tentativa(self, tentativas: Sequence[str]) -> dict[str, float]:
        """O US$ da IA de cada tentativa, das chamadas ainda em `ai_calls` (a regra de preço de `spent_usd`)."""
        if not tentativas:
            return {}
        precos = self._precos()
        saida: dict[str, float] = {}
        for c in self._db.query("SELECT attempt_id, model, provider, input_tokens, cache_read, cache_write,"
                                " cache_write_1h, output_tokens, usd FROM ai_calls"
                                f" WHERE attempt_id IN ({_marcas(len(tentativas))})", tuple(tentativas)):
            valor = 0.0 if (c["provider"] or "") == "simulated" else (
                float(c["usd"]) if c["usd"] is not None else row_usd(precos, c))
            saida[str(c["attempt_id"])] = saida.get(str(c["attempt_id"]), 0.0) + valor
        return saida

    def _vizinhos(self, fluxo: Row | None) -> tuple[VizinhoUsado, ...]:
        """Os vizinhos do plano do fluxo da sessão e as etapas de planos LIVRES (sem fluxo e sem prova de fluxo) que os
        aceitaram: o registro durável é `steps.pacotes_aceitos`, não o texto da trilha."""
        if fluxo is None:
            return ()
        plano = loads(fluxo["plan"], {}) or {}
        pares: list[tuple[str, str]] = []
        for passo in plano.get("steps") or []:
            app = str(passo.get("app_id") or plano.get("app_id") or fluxo["app_id"] or "")
            for pacote in passo.get("pacotes_aceitos") or []:
                if (app, str(pacote)) not in pares:
                    pares.append((app, str(pacote)))
        saida = []
        for app, pacote in pares:
            etapas = Contagem()
            for r in self._db.query(
                    "SELECT s.pacotes_aceitos, r.simulated, r.prova_fluxo_id, r.idempotency_key FROM steps s"
                    " JOIN runs r ON r.id = s.run_id WHERE s.pacotes_aceitos LIKE ? AND r.flow_id IS NULL"
                    " AND r.prova_fluxo_id IS NULL", (f'%"{pacote}"%',)):
                if pacote in (loads(r["pacotes_aceitos"], []) or []):
                    etapas.somar(_uso(r))
            saida.append(VizinhoUsado(app=app, pacote=pacote, etapas_em_planos_livres=etapas))
        return tuple(saida)

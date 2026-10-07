"""31.221: o leitor do ensino a partir da execução. Só leitura; quatro consultas por execução (a execução, as etapas,
as ferramentas das tentativas que deram certo e as receitas nascidas das etapas). Regras em
`domain/ensino_da_execucao.py`."""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.db import Database
from app.modules.learning.domain.ensino_da_execucao import EtapaDaExecucao, Motivo, ReceitaDaEtapa, motivo


@dataclass(frozen=True, slots=True)
class EtapaLida:
    etapa: EtapaDaExecucao
    receita: ReceitaDaEtapa | None
    motivo: Motivo | None


@dataclass(frozen=True, slots=True)
class ExecucaoLida:
    run_id: str
    status: str
    simulada: bool
    etapas: tuple[EtapaLida, ...]

    @property
    def ensinaveis(self) -> tuple[EtapaLida, ...]:
        return tuple(e for e in self.etapas if e.motivo is None and e.receita is not None)


def _trava(texto: object) -> bool:
    """`commit_guard` não vazio; o conteúdo (o texto que o efeito vai escrever) nunca é lido para fora daqui."""
    try:
        valor = json.loads(str(texto or "[]"))
    except ValueError:
        return bool(str(texto or "").strip())
    return bool(valor)


class LeitorDoEnsinoDaExecucao:
    def __init__(self, db: Database):
        self.db = db

    def ler(self, run_id: str) -> ExecucaoLida | None:
        run = self.db.one("SELECT id, status, simulated FROM runs WHERE id=?", (run_id,))
        if run is None:
            return None
        ferramentas: dict[str, list[str]] = {}
        for r in self.db.query("SELECT t.step_id, a.tool FROM actions a JOIN attempts t ON t.id = a.attempt_id"
                               " JOIN steps s ON s.id = t.step_id WHERE s.run_id=? AND t.status='succeeded'"
                               " ORDER BY t.step_id, t.number, a.seq", (run_id,)):
            ferramentas.setdefault(str(r["step_id"]), []).append(str(r["tool"]))
        receitas: dict[str, ReceitaDaEtapa] = {}
        for r in self.db.query("SELECT id, status, replay_ok, learned_from_step FROM recipes WHERE learned_from_step IN"
                               " (SELECT id FROM steps WHERE run_id=?) ORDER BY id", (run_id,)):
            # a mais nova da etapa: a candidata que divergiu e foi trocada fica `superseded` atrás dela
            receitas[str(r["learned_from_step"])] = ReceitaDaEtapa(id=int(r["id"]), status=str(r["status"]),
                                                                  replay_ok=int(r["replay_ok"] or 0))
        simulada = bool(run["simulated"])
        etapas: list[EtapaLida] = []
        for s in self.db.query("SELECT s.id, s.key, s.capability, s.status, s.driven_by, s.side_effect, s.commit_guard,"
                               " o.profile_id FROM steps s LEFT JOIN objectives o ON o.id = s.objective_id"
                               " WHERE s.run_id=? ORDER BY s.objective_id, s.plan_version, s.seq, s.id", (run_id,)):
            sid = str(s["id"])
            etapa = EtapaDaExecucao(step_id=sid, key=str(s["key"]), capability=str(s["capability"] or ""),
                                    status=str(s["status"]), driven_by=str(s["driven_by"] or ""),
                                    side_effect=bool(s["side_effect"]), trava=_trava(s["commit_guard"]),
                                    profile_id=str(s["profile_id"] or ""), ferramentas=tuple(ferramentas.get(sid, ())))
            receita = receitas.get(sid)
            etapas.append(EtapaLida(etapa=etapa, receita=receita, motivo=motivo(etapa, receita, simulada=simulada)))
        return ExecucaoLida(run_id=str(run["id"]), status=str(run["status"]), simulada=simulada, etapas=tuple(etapas))


__all__ = ["EtapaLida", "ExecucaoLida", "LeitorDoEnsinoDaExecucao"]

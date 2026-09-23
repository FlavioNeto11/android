"""Fluxos: o plano de um comando repetível, congelado em forma de template.

Por quê: o planejador real reescreve chaves, títulos e pós-condições a cada execução; sem um plano estável as
receitas (taskqueue/recipes.py) nunca casariam de novo. Quando uma execução termina com TODOS os aparelhos
comprovados, o comando vira um modelo — os valores dos parâmetros dão lugar a {nome} — e o plano é guardado do mesmo
jeito. Um comando novo que case com o modelo (mesmo texto, outros valores) reaproveita o plano sem chamar o planejador.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from ..db import Database, Row
from ..models import Plan, PlannerInfo
from ..util import now_iso

RESERVED = {"instance_id", "run_id", "account_label"}
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().casefold()


def _sub_values(text: str | None, values: dict[str, str]) -> str | None:
    if not text:
        return text
    for name, value in sorted(values.items(), key=lambda kv: -len(kv[1])):
        text = text.replace(value, "{" + name + "}")
    return text


class FlowStore:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ aprender
    def learn_from_run(self, run: Row) -> str | None:
        """Chamado quando a execução termina `completed` (todos comprovados). Devolve o id do fluxo criado."""
        if not run["plan"] or run["flow_id"]:
            return None
        plan = Plan.model_validate_json(run["plan"])
        if plan.missing or not plan.steps:
            return None
        command: str = run["command"]
        # parâmetros cujo valor aparece literalmente no comando viram {nome}; o run_id nunca é parâmetro de modelo
        values = {k: v for k, v in plan.parameters.items()
                  if k not in RESERVED and v and len(v) >= 3 and v in command and run["id"] not in v}
        template = _sub_values(command, values) or command
        key = _norm(template)
        if self.db.one("SELECT id FROM flows WHERE match_key=?", (key,)):
            return None
        tpl = plan.model_copy(deep=True)
        tpl.parameters = {k: ("{" + k + "}" if k in values else v) for k, v in plan.parameters.items() if run["id"] not in v}
        for s in tpl.steps:                       # o planejador às vezes escreve o valor em vez da variável: normaliza
            s.title, s.goal = _sub_values(s.title, values) or s.title, _sub_values(s.goal, values) or s.goal
            s.precondition = _sub_values(s.precondition, values)
            s.commit_guard = [_sub_values(g, values) or g for g in s.commit_guard]
            s.postcondition.value = _sub_values(s.postcondition.value, values) or s.postcondition.value
            s.postcondition.description = _sub_values(s.postcondition.description, values) or s.postcondition.description
        tpl.summary = _sub_values(tpl.summary, values) or tpl.summary
        base = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", plan.summary).encode("ascii", "ignore")
                      .decode().lower()).strip("-")[:40] or "fluxo"
        flow_id, n = base, 2
        while self.db.one("SELECT id FROM flows WHERE id=?", (flow_id,)):
            flow_id, n = f"{base}-{n}", n + 1
        self.db.execute(
            "INSERT INTO flows(id, name, match_key, command_template, plan, app_id, source_run_id, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (flow_id, plan.summary[:120], key, template, tpl.model_dump_json(), plan.app_id, run["id"], now_iso()))
        self.set_required_apps(flow_id, tpl.required_apps or ([plan.app_id] if plan.app_id else []))
        return flow_id

    # ------------------------------------------------------------------ apps exigidos
    def set_required_apps(self, flow_id: str, app_ids: list[str]) -> None:
        """Declara de que apps o fluxo precisa. Só entra app que EXISTE: exigir o que não há não ajuda ninguém."""
        self.db.execute("DELETE FROM flow_required_apps WHERE flow_id=?", (flow_id,))
        for app_id in dict.fromkeys(a for a in app_ids if a):
            if self.db.one("SELECT id FROM apps WHERE id=?", (app_id,)) is None:
                continue
            self.db.execute("INSERT INTO flow_required_apps(flow_id, app_id) VALUES (?,?)", (flow_id, app_id))

    def required_apps(self, flow_id: str) -> list[str]:
        return [r["app_id"] for r in self.db.query(
            "SELECT app_id FROM flow_required_apps WHERE flow_id=? ORDER BY app_id", (flow_id,))]

    # ------------------------------------------------------------------ casar
    def match(self, command: str) -> tuple[Row, Plan] | None:
        """Comando novo × modelos conhecidos. Casa o texto inteiro; cada {nome} captura o valor novo."""
        for row in self.db.query("SELECT * FROM flows WHERE status='active' ORDER BY uses DESC, created_at"):
            values = self._extract(row["command_template"], command)
            if values is None:
                continue
            plan = Plan.model_validate_json(row["plan"])
            plan.parameters = {k: (values.get(k, v) if v == "{" + k + "}" else v) for k, v in plan.parameters.items()}
            if any(v == "{" + k + "}" for k, v in plan.parameters.items()):
                continue                              # faltou valor para algum parâmetro: não é este fluxo
            plan.planner = PlannerInfo(provider="fluxo", model=f"fluxo:{row['id']}", simulated=plan.planner.simulated)
            # O que o fluxo EXIGE vem da tabela, não do JSON congelado: assim um fluxo aprendido antes desta
            # mudança passa a declarar o que precisa assim que alguém o declarar, sem reescrever plano nenhum.
            plan.required_apps = self.required_apps(row["id"]) or plan.required_apps
            return row, plan
        return None

    @staticmethod
    def _extract(template: str, command: str) -> dict[str, str] | None:
        names: list[str] = []
        pattern = ""
        pos = 0
        for m in PLACEHOLDER.finditer(template):
            pattern += re.escape(_squash(template[pos:m.start()]))
            if m.group(1) in RESERVED:
                pattern += re.escape(m.group(0))      # {instance_id}/{run_id} são texto literal do comando
            elif m.group(1) in names:
                pattern += f"(?P={m.group(1)})"
            else:
                names.append(m.group(1))
                pattern += f"(?P<{m.group(1)}>.+?)"
            pos = m.end()
        pattern += re.escape(_squash(template[pos:]))
        got = re.fullmatch(pattern, _squash(command), flags=re.IGNORECASE | re.DOTALL)
        if got is None:
            return None
        values = {k: v.strip() for k, v in got.groupdict().items()}
        return values if all(values.values()) and all(len(v) <= 500 for v in values.values()) else None

    def used(self, flow_id: str) -> None:
        self.db.execute("UPDATE flows SET uses=uses+1, last_used_at=? WHERE id=?", (now_iso(), flow_id))

    def list(self) -> list[dict[str, Any]]:
        return [dict(r) | {"plan": None} for r in self.db.query(
            "SELECT id, name, command_template, app_id, source_run_id, status, uses, created_at, last_used_at FROM flows"
            " ORDER BY last_used_at DESC, created_at DESC")]


def _squash(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(str.maketrans("“”„‟‘’", "\"\"\"\"''"))
    return re.sub(r"\s+", " ", text).strip()

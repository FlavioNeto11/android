"""31.181: a leitura do alcance por persona num app. Só leitura, sem IA.

As regras vêm de quem decide na execução, por injeção: `receita_presa(linha, persona)` é o
`RecipeStore._restrita_ao_ensino` (30.81) e `fluxo_no_escopo(fluxo, persona)` é o `FlowStore._no_escopo`. Assim a
resposta não diverge do que o executor faz, e este módulo não importa o `taskqueue`.
"""
from __future__ import annotations

from collections.abc import Callable

from app.db import Database, Row
from app.modules.learning.domain.alcance import ItemDoAlcance, resumo, veredito_da_receita, veredito_do_fluxo

#: A origem do ensino nas receitas e nos fluxos (`training:<sessão>`).
_DO_ENSINO = "training:"


class LeitorDoAlcance:
    def __init__(self, db: Database, *, receita_presa: Callable[[Row, str], bool],
                 fluxo_no_escopo: Callable[[str, str], bool], ref_do_fluxo: Callable[[str], str]) -> None:
        self.db = db
        self._receita_presa = receita_presa
        self._fluxo_no_escopo = fluxo_no_escopo
        #: `taskqueue.flows.ref_publica_do_fluxo` (30.83): o id interno do fluxo nunca sai (o legado é o slug do
        #: comando e pode ter nome de pessoa); sem `ref_publico`, ela preenche na hora.
        self._ref_do_fluxo = ref_do_fluxo

    def personas(self, app_id: str) -> dict[str, list[str]]:
        """`{profile_id: [aparelhos]}` das personas vinculadas (ativas) ao app."""
        saida: dict[str, list[str]] = {}
        for r in self.db.query("SELECT profile_id, instance_id FROM device_profile_bindings WHERE active=1 AND app_id=?"
                               " AND profile_id IS NOT NULL ORDER BY profile_id, instance_id", (app_id,)):
            saida.setdefault(str(r["profile_id"]), []).append(str(r["instance_id"]))
        return saida

    def ler(self, app_id: str, pacote: str | None) -> dict[str, object]:
        personas = self.personas(app_id)
        itens: list[ItemDoAlcance] = []
        if pacote:
            for r in self.db.query("SELECT * FROM recipes WHERE status='active' AND app_package=?"
                                   " ORDER BY replay_ok DESC, id", (pacote,)):
                itens.append(ItemDoAlcance(
                    tipo="receita", id=str(r["id"]), chave=str(r["step_key"]),
                    origem="ensino" if str(r["learned_from_step"] or "").startswith(_DO_ENSINO) else "execucao",
                    estado=str(r["status"]),
                    detalhe={"reproducoes_ok": int(r["replay_ok"] or 0),
                             "reproducoes_falha": int(r["replay_fail"] or 0)},
                    por_persona={p: veredito_da_receita(self._receita_presa(r, p)) for p in personas}))
        for f in self.db.query("SELECT id, status, source, nascido_de_prova, uses FROM flows"
                               " WHERE app_id=? AND status IN ('active','candidate') ORDER BY status, created_at, id",
                               (app_id,)):
            estado = str(f["status"])
            itens.append(ItemDoAlcance(
                tipo="fluxo", id=self._ref_do_fluxo(str(f["id"])), chave="",
                origem="ensino" if str(f["source"] or "").startswith(_DO_ENSINO) else "execucao", estado=estado,
                detalhe={"usos": int(f["uses"] or 0), "nascido_de_prova": bool(f["nascido_de_prova"])},
                por_persona={p: veredito_do_fluxo(estado, self._fluxo_no_escopo(str(f["id"]), p)) for p in personas}))
        return {"app_id": app_id, "pacote": pacote,
                "personas": [{"profile_id": p, "aparelhos": a} for p, a in personas.items()],
                "resumo": resumo(itens, list(personas)), "itens": [i.como_dict() for i in itens]}


__all__ = ["LeitorDoAlcance"]

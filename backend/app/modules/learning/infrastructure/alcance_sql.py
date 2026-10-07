"""31.181: a leitura do alcance por persona num app. Só leitura, sem IA.

As regras vêm de quem decide na execução, por injeção: `receita_presa(linha, persona)` é o
`RecipeStore._restrita_ao_ensino` (30.81) e `fluxo_no_escopo(fluxo, persona)` é o `FlowStore._no_escopo`. Assim a
resposta não diverge do que o executor faz, e este módulo não importa o `taskqueue`.

31.218 (adendo v1.118): cada persona traz também as `correcoes`, as lições do planejador que nasceram de uma falha
DELA e da correção que uma pessoa ensinou (31.149): o que essa persona erra e como se corrige. A lição nova é da
persona (`scope_profile_id`); a anterior ao 31.218 é do app, e a persona dela se acha pela etapa que falhou.
"""
from __future__ import annotations

from collections.abc import Callable

from app.db import Database, Row, loads
from app.modules.learning.domain.vocabulario import SourceKind
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
        correcoes = self.correcoes(pacote) if pacote else {}
        return {"app_id": app_id, "pacote": pacote,
                "personas": [{"profile_id": p, "aparelhos": a, "correcoes": correcoes.get(p, [])}
                             for p, a in personas.items()],
                "resumo": resumo(itens, list(personas)), "itens": [i.como_dict() for i in itens]}


    def correcoes(self, pacote: str) -> dict[str, list[dict[str, object]]]:
        """31.218: `{profile_id: [lição]}` das lições vivas do planejador nascidas de correção ensinada no app. Cada uma
        com `id`, `estado`, `acao` (a etapa que falhou), `caminho` (o que a pessoa ensinou), `texto` (o do prompt) e
        `escopo`: `persona` (vale só para ela) ou `app` (anterior ao 31.218: vale para todas)."""
        saida: dict[str, list[dict[str, object]]] = {}
        for r in self.db.query(
                "SELECT id, state, scope_profile_id, content, summary, provenance FROM learning_items"
                " WHERE kind='licao' AND source_kind=? AND scope_app=? AND state IN ('candidate', 'validated', 'published')"
                " ORDER BY created_at, id", (SourceKind.CORRECAO_ENSINADA.value, pacote)):
            dona = str(r["scope_profile_id"] or "")
            persona = dona or self._persona_da_etapa(str((loads(r["provenance"], {}) or {}).get("etapa") or ""))
            if not persona:
                continue
            conteudo = loads(r["content"], {}) or {}
            caminho = conteudo.get("caminho")
            saida.setdefault(persona, []).append({
                "id": str(r["id"]), "estado": str(r["state"]), "acao": str(conteudo.get("acao") or ""),
                "caminho": [str(c) for c in caminho] if isinstance(caminho, list) else [],
                "texto": str(r["summary"]), "escopo": "persona" if dona else "app"})
        return saida

    def _persona_da_etapa(self, step_id: str) -> str:
        if not step_id:
            return ""
        return str(self.db.scalar("SELECT o.profile_id FROM steps s JOIN objectives o ON o.id = s.objective_id"
                                  " WHERE s.id=?", (step_id,)) or "")


__all__ = ["LeitorDoAlcance"]

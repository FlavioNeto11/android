"""31.118, reparo único: as gravações SALVAS antes do 31.118 recebem o marcador da persona no texto digitado.

Desde o 31.118 o `save` troca, na gravação, o dado da persona que a habilidade usa pelo marcador. As sessões salvas
antes seguem com o valor em claro em `training_inputs.text`. Este passe aplica a MESMA regra a elas: sessão `saved` com
persona, entrada de texto cujo campo inteiro é um dado da persona demonstrado (`dado_da_persona.demonstrados`) e cujo
marcador está no plano do fluxo salvo (ou, sem o fluxo, na proposta guardada). Idempotente: a entrada já marcada não
casa de novo. Quem chama é `scripts/gravacao-com-marcador.py` (ensaio numa cópia, ou `--aplicar` com backup).

O relatório leva só ids de sessão e contagens: nenhum valor da persona, nenhum id de persona.
"""
from __future__ import annotations

from collections.abc import Callable

from ..db import Database
from . import dado_da_persona


def marcar_gravacoes_salvas(db: Database, variaveis: Callable[[str | None], dict[str, str]], *,
                            escrever: bool) -> dict[str, object]:
    """`variaveis(profile_id)`: os dados não sigilosos da persona (`Repository.variaveis_da_persona`). Com
    `escrever=False` só conta. Devolve `{sessoes_lidas, sessoes_com_marca, entradas_marcadas, sessoes}` (os ids das
    sessões que mudam, em ordem)."""
    lidas, entradas, sessoes = 0, 0, []
    for s in db.query("SELECT t.id, t.profile_id, t.proposal, f.plan FROM training_sessions t"
                      " LEFT JOIN flows f ON f.id = t.flow_id WHERE t.status='saved' AND t.profile_id IS NOT NULL"
                      " ORDER BY t.id"):
        lidas += 1
        digitadas = [dict(r) for r in db.query(
            "SELECT seq, type, text FROM training_inputs WHERE session_id=? AND type='text' ORDER BY seq", (s["id"],))]
        persona = dado_da_persona.demonstrados(variaveis(str(s["profile_id"])), digitadas)
        plano = str(s["plan"] or s["proposal"] or "")
        marcas = dado_da_persona.marcas_das_entradas(
            digitadas, {n: v for n, v in persona.items() if "{" + n + "}" in plano})
        if not marcas:
            continue
        sessoes.append(str(s["id"]))
        entradas += len(marcas)
        if escrever:
            for seq, marca in marcas:
                db.execute("UPDATE training_inputs SET text=? WHERE session_id=? AND seq=? AND type='text'",
                           (marca, s["id"], seq))
    return {"sessoes_lidas": lidas, "sessoes_com_marca": len(sessoes), "entradas_marcadas": entradas,
            "sessoes": sessoes}


__all__ = ["marcar_gravacoes_salvas"]

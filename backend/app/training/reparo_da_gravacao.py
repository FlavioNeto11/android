"""31.118, reparo único: as gravações SALVAS antes do 31.118 recebem o marcador da persona no texto digitado.

Desde o 31.118 o `save` troca, na gravação, o dado da persona que a habilidade usa pelo marcador. As sessões salvas
antes seguem com o valor em claro em `training_inputs.text`. Este passe aplica a MESMA regra a elas: sessão `saved` com
persona, entrada de texto cujo campo inteiro é um dado da persona demonstrado (`dado_da_persona.demonstrados`) e cujo
marcador está no plano do fluxo salvo (ou, sem o fluxo, na proposta guardada). Idempotente: a entrada já marcada não
casa de novo. Quem chama é `scripts/gravacao-com-marcador.py` (ensaio numa cópia, ou `--aplicar` com backup).

31.118 F2: a tela gravada (`screen_lines`, `screen_title`) também mostra o dado (o campo preenchido logo depois da
digitação). O mesmo passe troca, por palavra, todo dado não sigiloso da persona pelo marcador nessas linhas
(`marcar_telas`, que o `save` também chama). Idempotente: a linha já marcada não casa de novo.

O relatório leva só ids de sessão e contagens: nenhum valor da persona, nenhum id de persona.
"""
from __future__ import annotations

from collections.abc import Callable

from collections.abc import Mapping

from ..db import Database, dumps, loads
from . import dado_da_persona


def marcar_telas(db: Database, session_id: str, persona: Mapping[str, str], *, escrever: bool = True) -> int:
    """31.118 F2: as entradas da sessão cuja tela gravada (`screen_lines`, `screen_title` e, desde o 31.122 F2,
    `screen_elements`: texto e descrição) cita um dado da persona passam a citar o marcador
    (`dado_da_persona.com_marcador`, por palavra). Devolve quantas entradas mudam."""
    if not persona:
        return 0
    mudadas = 0
    for r in db.query("SELECT seq, screen_lines, screen_title, screen_elements FROM training_inputs WHERE session_id=?"
                      " ORDER BY seq", (session_id,)):
        linhas = loads(r["screen_lines"], []) or []
        novas = [dado_da_persona.com_marcador(x, persona) if isinstance(x, str) else x for x in linhas]
        titulo = r["screen_title"]
        novo_titulo = dado_da_persona.com_marcador(titulo, persona) if isinstance(titulo, str) else titulo
        elementos = loads(r["screen_elements"], []) or []
        novos = [{k: (dado_da_persona.com_marcador(v, persona) if k in ("t", "d") and isinstance(v, str) else v)
                  for k, v in x.items()} if isinstance(x, dict) else x for x in elementos]
        if novas == linhas and novo_titulo == titulo and novos == elementos:
            continue
        mudadas += 1
        if escrever:
            db.execute("UPDATE training_inputs SET screen_lines=?, screen_title=?, screen_elements=? WHERE session_id=?"
                       " AND seq=?", (dumps(novas) if r["screen_lines"] is not None else None, novo_titulo,
                                      dumps(novos) if r["screen_elements"] is not None else None, session_id, r["seq"]))
    return mudadas


def marcar_gravacoes_salvas(db: Database, variaveis: Callable[[str | None], dict[str, str]], *,
                            escrever: bool) -> dict[str, object]:
    """`variaveis(profile_id)`: os dados não sigilosos da persona (`Repository.variaveis_da_persona`). Com
    `escrever=False` só conta. Devolve `{sessoes_lidas, sessoes_com_marca, entradas_marcadas, telas_marcadas, sessoes}`
    (os ids das sessões que mudam, em ordem)."""
    lidas, entradas, telas, sessoes = 0, 0, 0, []
    for s in db.query("SELECT t.id, t.profile_id, t.proposal, f.plan FROM training_sessions t"
                      " LEFT JOIN flows f ON f.id = t.flow_id WHERE t.status='saved' AND t.profile_id IS NOT NULL"
                      " ORDER BY t.id"):
        lidas += 1
        digitadas = [dict(r) for r in db.query(
            "SELECT seq, type, text FROM training_inputs WHERE session_id=? AND type='text' ORDER BY seq", (s["id"],))]
        dados = variaveis(str(s["profile_id"]))
        persona = dado_da_persona.demonstrados(dados, digitadas)
        plano = str(s["plan"] or s["proposal"] or "")
        marcas = dado_da_persona.marcas_das_entradas(
            digitadas, {n: v for n, v in persona.items() if "{" + n + "}" in plano})
        n_telas = marcar_telas(db, str(s["id"]), dados, escrever=escrever)       # F2
        if not marcas and not n_telas:
            continue
        sessoes.append(str(s["id"]))
        entradas += len(marcas)
        telas += n_telas
        if escrever:
            for seq, marca in marcas:
                db.execute("UPDATE training_inputs SET text=? WHERE session_id=? AND seq=? AND type='text'",
                           (marca, s["id"], seq))
    return {"sessoes_lidas": lidas, "sessoes_com_marca": len(sessoes), "entradas_marcadas": entradas,
            "telas_marcadas": telas, "sessoes": sessoes}


__all__ = ["marcar_gravacoes_salvas", "marcar_telas"]

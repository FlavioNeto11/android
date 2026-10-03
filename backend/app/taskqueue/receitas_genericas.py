"""RA-20 fatia B, o passe ÚNICO: semeia a chave genérica com as receitas que já existem (`scripts/ra20b-receitas-genericas.py`).

Não destrutivo (emenda aceita pela Android, 03/10/2026): as receitas atuais ficam onde estão, sem renumerar e sem
`replaces`. Por chave genérica entra UMA cópia CANDIDATA da ativa elegível com mais evidência (reproduções certas, uso
mais recente); ela se prova em sombra nos valores novos, como a herdeira do RA-20 A. Uma específica mal classificada
custa só uma divergência em sombra (candidata não entra em quarentena e é trocada pelo caminho da IA), e a receita do
valor original nunca cai.

Fica de fora:
- a etapa sem chave genérica (`hash_generico_da_linha` None) e a receita específica (`eh_generica` falso);
- `open_app`: desde o 29.45 (LT-6) a etapa de app em frente abre o app sem IA e não gera comparação de sombra, então
  a candidata nunca seria promovida;
- a chave genérica que já tem receita viva (ativa, candidata ou validada) — o passe é idempotente por isso;
- a receita do treino (`training:`), que não tem etapa de origem: o treino fica específico.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from ..db import Database, Row, loads
from .recipes import RecipeStore, eh_generica, hash_generico_da_linha

#: Etapas que nunca semeiam (ver o docstring do módulo).
FORA = ("open_app",)


@dataclass
class Grupo:
    """Uma chave genérica: as ativas elegíveis que caem nela e o que o passe faz."""

    chave: tuple[str, str, str, str, str]          # pacote, versão, assinatura, variante, hash genérico
    receitas: list[Row] = field(default_factory=list)
    escolhida: int | None = None
    semeada: int | None = None
    motivo: str = ""


@dataclass
class Relatorio:
    grupos: list[Grupo]
    contagem: dict[str, int]


def _evidencia(r: Row) -> tuple[int, int, str, int]:
    return (int(r["replay_ok"] or 0), -int(r["replay_fail"] or 0), str(r["last_used_at"] or ""), int(r["id"]))


def semear(db: Database, store: RecipeStore, *, gravar: bool) -> Relatorio:
    """Monta os grupos e, com `gravar`, semeia uma candidata por grupo pela loja (veto da pessoa, trilha do sistema)."""
    contagem = {"ativas": 0, "sem_chave_generica": 0, "especificas": 0, "fora": 0, "ja_na_generica": 0}
    grupos: dict[tuple[str, str, str, str, str], Grupo] = {}
    for r in db.query(
            "SELECT r.id, r.app_package, r.app_version, r.app_signature, r.variant, r.step_hash, r.step_key, r.actions,"
            " r.learned_from_step, r.replay_ok, r.replay_fail, r.last_used_at, s.key, s.template_key, s.side_effect, s.title,"
            " s.postcondition, s.commit_guard, o.parameters FROM recipes r JOIN steps s ON s.id = r.learned_from_step"
            " LEFT JOIN objectives o ON o.id = s.objective_id WHERE r.status='active' ORDER BY r.id"):
        contagem["ativas"] += 1
        generico = hash_generico_da_linha(r)
        if generico is None:
            contagem["sem_chave_generica"] += 1
            continue
        if str(r["step_key"]).startswith(FORA) or str(r["template_key"] or r["key"]).startswith(FORA):
            contagem["fora"] += 1
            continue
        if generico == r["step_hash"]:
            contagem["ja_na_generica"] += 1
            continue
        post = loads(r["postcondition"], {}) or {}
        if not eh_generica(loads(r["actions"], []) or [], post.get("value"), loads(r["parameters"], {}) or {},
                           titulo=r["title"]):
            contagem["especificas"] += 1
            continue
        chave = (str(r["app_package"]), str(r["app_version"]), str(r["app_signature"] or ""), str(r["variant"] or ""),
                 generico)
        grupos.setdefault(chave, Grupo(chave)).receitas.append(r)
    contagem.update({"grupos": len(grupos), "semeadas": 0, "grupos_com_viva": 0, "nao_gravadas": 0})
    for g in grupos.values():
        pacote, versao, assinatura, variante, generico = g.chave
        viva = db.one("SELECT id FROM recipes WHERE app_package=? AND app_version=? AND app_signature=? AND variant=?"
                      " AND step_hash=? AND status IN ('active','candidate','validated') LIMIT 1", g.chave)
        if viva is not None:
            g.motivo = f"a chave genérica já tem a receita {viva['id']} viva"
            contagem["grupos_com_viva"] += 1
            continue
        dona = max(g.receitas, key=_evidencia)
        g.escolhida = int(dona["id"])
        if not gravar:
            g.motivo = "seria semeada (candidata)"
            continue
        novo = store.save(package=pacote, app_version=versao, step_hash=generico, step_key=str(dona["step_key"]),
                          actions=loads(dona["actions"], []), learned_from=str(dona["learned_from_step"] or ""),
                          signature=assinatura, variant=variante, candidate=True,
                          heranca=f"semeada da receita {dona['id']} (RA-20 B: o caminho não depende do valor da etapa);"
                                  " em prova (sombra)")
        if novo:
            g.semeada, g.motivo = novo, "semeada (candidata)"
            contagem["semeadas"] += 1
        else:
            g.motivo = "a loja não gravou (veto da pessoa ou receita viva)"
            contagem["nao_gravadas"] += 1
    return Relatorio(list(grupos.values()), contagem)

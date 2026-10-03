"""O que cada perfil (persona) já fez, e quanto disso roda sem chamar a IA.

Receitas e fluxos são do PARQUE: chave por app, versão e etapa, sem perfil nem aparelho. O dono não tinha como
responder "o que esta persona sabe fazer?" nem "que caminhos já estão mapeados para não custar token de novo?".
Este módulo junta o que já existe — `objectives.profile_id` → `runs.flow_id` → `flows`, `steps.driven_by`,
`recipes.step_hash` — e responde às duas perguntas em leitura pura, sem custo de modelo.

Cobertura de um fluxo = etapas do plano-modelo cujo `step_template_hash` tem receita ATIVA para a versão-alvo do
app (a promovida). Zero receitas = a IA planeja e age em tudo ("custo total"); todas = só reprodução ("zero").
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from ..db import Database, loads
from ..models import Plan
from ..planning import costs
from ..taskqueue.aproveitamento import aproveitamento
from ..taskqueue.recipes import para_hash, step_template_hash
from ..util import now, to_iso

#: Janela de histórico para o custo mediano por etapa (item 7.7): recente o bastante para refletir o modelo
#: em uso hoje, longa o bastante para não ficar vazia entre baterias.
JANELA_CUSTO_DIAS = 7


def _mediana(valores: list[float]) -> float:
    ordenados = sorted(valores)
    n = len(ordenados)
    meio = n // 2
    return ordenados[meio] if n % 2 else (ordenados[meio - 1] + ordenados[meio]) / 2


def _custo_mediano_por_papel(s: Any, role: str, desde: str) -> float | None:
    """Custo mediano de UMA etapa que passou por este papel (`decide` ou `verify`) nos últimos
    `JANELA_CUSTO_DIAS` dias — soma as chamadas da mesma etapa (uma etapa pode julgar mais de uma vez) e tira a
    mediana entre etapas, não a média: poucas etapas caras (retry, imagem) não podem puxar a estimativa toda.
    `None` sem nenhuma etapa no período — "sem base", nunca um número fantasioso."""
    prices = s.cfg.file.ai.prices
    linhas = s.db.query(
        "SELECT step_id, model, SUM(input_tokens) input_tokens, SUM(cache_read) cache_read,"
        " SUM(cache_write) cache_write, SUM(output_tokens) output_tokens FROM ai_calls"
        " WHERE role=? AND ts>=? AND step_id IS NOT NULL AND COALESCE(provider,'')<>'simulated'"
        " GROUP BY step_id, model", (role, desde))
    por_etapa: dict[str, float] = {}
    for linha in linhas:
        por_etapa[linha["step_id"]] = por_etapa.get(linha["step_id"], 0.0) + costs.row_usd(prices, linha)
    return _mediana(list(por_etapa.values())) if por_etapa else None


def medianas_de_custo(s: Any) -> tuple[float | None, float | None]:
    """(custo mediano de `decide`, custo mediano de `verify`) nos últimos `JANELA_CUSTO_DIAS` dias — UMA consulta
    por papel, reaproveitada por todos os fluxos de uma listagem (evita repetir a varredura de `ai_calls` por
    fluxo, que é o caso comum ao listar o parque inteiro)."""
    desde = to_iso(now() - timedelta(days=JANELA_CUSTO_DIAS))
    return _custo_mediano_por_papel(s, "decide", desde), _custo_mediano_por_papel(s, "verify", desde)


def estimated_usd(s: Any, *, steps_total: int, steps_with_recipe: int,
                   medianas: tuple[float | None, float | None] | None = None) -> float | None:
    """US$ esperado por aparelho ao repetir o fluxo (item 6/7.7, "quanto vai custar?" do Osintgram): etapas SEM
    receita × custo mediano de uma etapa só-IA (papel `decide`) + verificações previstas (todas as etapas passam
    pelo verificador quando a pós-condição exige julgamento) × custo mediano de `verify`. `None` sem histórico
    nos últimos 7 dias — "sem base" é mais honesto que um número inventado. `medianas` deixa o chamador que já
    calculou (uma lista inteira de fluxos) reaproveitar, em vez de consultar `ai_calls` de novo por fluxo."""
    sem_receita = max(0, steps_total - steps_with_recipe)
    custo_decide, custo_verify = medianas if medianas is not None else medianas_de_custo(s)
    if custo_decide is None and custo_verify is None:
        return None
    total = sem_receita * (custo_decide or 0.0) + steps_total * (custo_verify or 0.0)
    return round(total, 4)


def _versao_alvo(s: Any, package: str | None) -> str | None:
    """`versionName(versionCode)` da versão promovida — a MESMA chave que o executor usa ao procurar receita."""
    if not package:
        return None
    rel = s.releases.promoted_release(package)
    return f"{rel.version_name}({rel.version_code})" if rel is not None else None


def _receitas_ativas(s: Any, package: str | None, versao: str | None) -> set[str]:
    if not package or not versao:
        return set()
    return {r["step_hash"] for r in s.db.query(
        "SELECT DISTINCT step_hash FROM recipes WHERE app_package=? AND app_version=? AND status='active'",
        (package, versao))}


def _package_do_app(s: Any, app_id: str | None) -> str | None:
    return s.db.scalar("SELECT package FROM apps WHERE id=?", (app_id,)) if app_id else None


def cobertura_do_fluxo(s: Any, fluxo: Any, *, receitas: set[str] | None = None,
                        medianas: tuple[float | None, float | None] | None = None) -> dict[str, Any]:
    """Quantas etapas do plano-modelo têm receita ativa para a versão promovida do app do fluxo."""
    package = _package_do_app(s, fluxo["app_id"])
    versao = _versao_alvo(s, package)
    ativas = _receitas_ativas(s, package, versao) if receitas is None else receitas
    try:
        plano = Plan.model_validate_json(fluxo["plan"]) if fluxo["plan"] else None
    except Exception:  # noqa: BLE001 - plano-modelo antigo ou corrompido: cobertura desconhecida, não erro
        plano = None
    etapas = list(plano.steps) if plano else []
    parametros = dict(plano.parameters) if plano else {}
    cobertas = sum(1 for e in etapas if step_template_hash(para_hash(e, parametros)) in ativas)
    total = len(etapas)
    custo = "desconhecido" if total == 0 else "zero" if cobertas == total else "total" if cobertas == 0 else "parcial"
    return {"flow_id": fluxo["id"], "package": package, "target_version": versao, "steps_total": total,
            "steps_with_recipe": cobertas, "ai_cost": custo,
            "estimated_usd": estimated_usd(s, steps_total=total, steps_with_recipe=cobertas, medianas=medianas)}


def cobertura_dos_fluxos(s: Any) -> list[dict[str, Any]]:
    """Visão de parque: cada fluxo com a sua cobertura para a versão-alvo. É a resposta a "quais caminhos já estão
    mapeados" sem abrir perfil nenhum."""
    fluxos = s.db.query("SELECT id, name, command_template, app_id, plan, status, uses FROM flows"
                        " ORDER BY last_used_at DESC, created_at DESC")
    cache: dict[tuple[str | None, str | None], set[str]] = {}
    medianas = medianas_de_custo(s)
    # Cobertura diz quantas etapas TÊM receita; aproveitamento diz quantas a USARAM na última semana, quantas voltaram
    # para a IA e por quê (frente F3). Uma leitura para o parque inteiro, repartida por fluxo.
    uso = aproveitamento(s.db)["fluxos"]
    saida = []
    for f in fluxos:
        package = _package_do_app(s, f["app_id"])
        versao = _versao_alvo(s, package)
        chave = (package, versao)
        if chave not in cache:
            cache[chave] = _receitas_ativas(s, package, versao)
        saida.append({**cobertura_do_fluxo(s, f, receitas=cache[chave], medianas=medianas), "name": f["name"],
                      "command_template": f["command_template"], "status": f["status"], "uses": f["uses"],
                      "aproveitamento": [g for g in uso if g["flow_id"] == f["id"]]})
    return saida


def capacidades_do_perfil(s: Any, profile_id: str) -> dict[str, Any]:
    """O que ESTE perfil já fez (fluxos concluídos, etapas por origem, interações) e quanto roda sem IA."""
    fluxos = s.db.query(
        "SELECT f.id, f.name, f.command_template, f.app_id, f.plan, f.status, f.uses,"
        " COUNT(DISTINCT o.id) AS vezes, MAX(r.created_at) AS ultimo"
        " FROM objectives o JOIN runs r ON r.id = o.run_id"
        " JOIN flows f ON (f.id = r.flow_id OR f.source_run_id = r.id)"
        " WHERE o.profile_id = ? AND o.status = 'succeeded'"
        " GROUP BY f.id ORDER BY ultimo DESC", (profile_id,))
    etapas = {r["driven_by"] or "ai": int(r["n"]) for r in s.db.query(
        "SELECT s.driven_by, COUNT(*) AS n FROM steps s JOIN objectives o ON o.id = s.objective_id"
        " WHERE o.profile_id = ? AND s.status = 'succeeded' GROUP BY s.driven_by", (profile_id,))}
    interacoes = {r["type"]: int(r["n"]) for r in s.db.query(
        "SELECT type, COUNT(*) AS n FROM social_interactions WHERE profile_id = ? AND status = 'confirmed'"
        " GROUP BY type ORDER BY n DESC", (profile_id,))}
    # Item 13.3: habilidades ENSINADAS no modo treinamento que valem para este perfil — sem escopo (todos), com o
    # perfil no escopo, ou com o grupo de acesso dele no escopo. Aparecem mesmo antes do primeiro uso.
    grupo = s.db.scalar("SELECT policy_group_id FROM instagram_profiles WHERE id=?", (profile_id,))
    treinadas = []
    for f in s.db.query("SELECT id, name, command_template, uses, status, created_at, last_used_at, source FROM flows"
                        " WHERE source LIKE 'training:%' ORDER BY created_at DESC"):
        esc = s.db.query("SELECT profile_id, group_id FROM flow_scope WHERE flow_id=?", (f["id"],))
        if esc and not any(e["profile_id"] == profile_id or (grupo and e["group_id"] == grupo) for e in esc):
            continue
        treinadas.append({"flow_id": f["id"], "name": f["name"], "command_template": f["command_template"],
                          "uses": int(f["uses"] or 0), "status": f["status"], "last_at": f["last_used_at"],
                          "scope": "todos os perfis" if not esc else "escolhido para este perfil ou grupo"})
    total_etapas = sum(etapas.values())
    # "Roda sem IA": a etapa da receita E a que fechou sem o ator (`sem_ator`, caminho rápido 1, LT-1) — nenhuma
    # das duas pediu decisão ao modelo. Fora dela, `sem_ator` entrava no total e não no numerador, e a fração caía.
    por_receita = etapas.get("recipe", 0) + etapas.get("sem_ator", 0)
    medianas = medianas_de_custo(s)
    habilidades = _habilidades_executadas(s.db, profile_id,
                                          lambda modelo: cobertura_do_fluxo(s, modelo, medianas=medianas))
    return {
        "profile_id": profile_id,
        "flows": [{**cobertura_do_fluxo(s, f, medianas=medianas), "name": f["name"],
                   "command_template": f["command_template"],
                   "times": int(f["vezes"]), "last_at": f["ultimo"]} for f in fluxos],
        "steps_driven_by": etapas,
        "recipe_share": round(por_receita / total_etapas, 3) if total_etapas else None,
        "interactions": interacoes,
        "trained": treinadas,
        "skills": habilidades,
    }


def _habilidades_executadas(db: Database, profile_id: str,
                            cobertura: Callable[[dict[str, str | None]], dict[str, object]]) -> list[dict[str, object]]:
    """Fase J: o que ESTE perfil concluiu por habilidade (`runs.skill_id`, trilha da 045). Até aqui a visão só via
    `runs.flow_id`, e a execução de uma habilidade não aparecia em lugar nenhum do perfil.

    A execução da versão que ADOTOU um fluxo grava os dois (`skill_id` e o `flow_id` do fluxo cujo plano ela é):
    aparece aqui e em `flows`, e `legacy_flow_id` diz que é a mesma coisa. A cobertura de receitas sai do plano da
    execução mais recente — a mesma conta dos fluxos (`para_hash` devolve os valores aos nomes)."""
    saida: list[dict[str, object]] = []
    for h in db.query(
            "SELECT r.skill_id, COUNT(DISTINCT o.id) AS vezes, MAX(r.created_at) AS ultimo"
            " FROM objectives o JOIN runs r ON r.id = o.run_id"
            " WHERE o.profile_id = ? AND o.status = 'succeeded' AND r.skill_id IS NOT NULL"
            " GROUP BY r.skill_id ORDER BY ultimo DESC", (profile_id,)):
        ultima = db.one(
            "SELECT r.skill_version, r.plan FROM objectives o JOIN runs r ON r.id = o.run_id"
            " WHERE o.profile_id = ? AND o.status = 'succeeded' AND r.skill_id = ?"
            " ORDER BY r.created_at DESC, r.id DESC LIMIT 1", (profile_id, h["skill_id"]))
        definicao = db.one("SELECT name, app_id, legacy_flow_id FROM skill_definitions WHERE id=?", (h["skill_id"],))
        plano = loads(ultima["plan"], {}) if ultima and ultima["plan"] else {}
        modelo = {"id": h["skill_id"], "app_id": plano.get("app_id") or (definicao["app_id"] if definicao else None),
                  "plan": ultima["plan"] if ultima else None}
        coberta = {k: v for k, v in cobertura(modelo).items() if k != "flow_id"}
        saida.append({"skill_id": h["skill_id"], "version": ultima["skill_version"] if ultima else None,
                      "name": definicao["name"] if definicao else h["skill_id"],
                      "legacy_flow_id": definicao["legacy_flow_id"] if definicao else None, **coberta,
                      "times": int(h["vezes"]), "last_at": h["ultimo"]})
    return saida

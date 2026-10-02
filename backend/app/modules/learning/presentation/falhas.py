"""Rotas do "o que mais falha" e do backlog da plataforma (ADR-054, decisão 7; pacote A3).

- `GET   /api/aprendizado/falhas?dias=14&app=&camada=&limite=20&simulados=0&retroativo=1&formato=json|md`: o relatório.
  O md traz a chave estável `fk-*` na primeira coluna de cada linha do topo (é o que `scripts/aprendizado-backlog.py`
  grava e a sessão de desenvolvimento lê). `retroativo=1` (padrão) inclui o legado classificado na LEITURA — nunca
  gravado; `retroativo=0` conta só o que a execução classificou.
- `GET   /api/aprendizado/backlog/{id}`: a linha (gravada, ou ainda só no relatório) com o grupo de hoje.
- `PATCH /api/aprendizado/backlog/{id} {state, plan_item?, fixed_in_commit?, notes?}`: o que a pessoa ou a sessão de
  desenvolvimento marca. `fixed` e `reopened` são medida (409); `fixed_pending_proof` exige o commit (422); nota com
  cara de credencial é 409 e nada é gravado.

Entram ANTES do livro em `router.py`: a rota genérica `{kind}/{ref}` casaria `backlog/{id}` e recusaria o `kind`.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from app.modules.learning.application.falhas import (DetalheDoBacklog, LinhaDaProposta, LinhaDoRelatorio,
                                                     RelatorioDeFalhas, ServicoDeFalhas)
from app.modules.learning.domain.backlog import GrupoDeFalha, LinhaDoBacklog, Proposta, RegrasDoBacklog
from app.modules.learning.domain.diagnostico import Diagnostico
from app.modules.learning.domain.falhas import Camada
from app.modules.learning.domain.vocabulario import EstadoDoBacklog
from app.modules.learning.presentation.livro import _chamar, _quem, _servico
from app.modules.skills.domain.document import JsonObject

router = APIRouter(prefix="/api/aprendizado")

#: O md é para ler: as propostas mais apoiadas (as de ação vêm em ordem de execuções), e o fragmento só das primeiras.
PROPOSTAS_NO_MD = 10
FRAGMENTOS_NO_MD = 3


def _falhas(request: Request) -> ServicoDeFalhas:
    falhas = _servico(request).extensao(ServicoDeFalhas)
    if falhas is None:
        raise HTTPException(503, detail={"code": "not_ready", "message": "O backlog do aprendizado não foi composto."})
    return falhas


# ------------------------------------------------------------------ JSON
def _r(valor: float | None, casas: int) -> float | None:
    return None if valor is None else round(valor, casas)


def grupo_json(g: GrupoDeFalha, regras: RegrasDoBacklog) -> JsonObject:
    c, onde = g.chave, g.onde_alterar
    return {
        "id": g.id, "cluster_key": c.cluster_key, "app": c.app, "capability": c.capability, "tipo": c.tipo,
        "tela": c.tela, "camada": g.camada.value, "titulo": g.titulo, "ocorrencias": g.ocorrencias,
        "retroativas": g.retroativas, "elegiveis": g.elegiveis, "taxa": _r(g.taxa, 4), "etapas": g.etapas,
        "execucoes": g.execucoes, "aparelhos": g.aparelhos, "usd_perdido": round(g.usd_perdido, 4),
        "min_perdidos": round(g.min_perdidos, 1), "intervencoes": g.intervencoes,
        "custo_total": round(g.custo_total(regras), 4), "custo_parcial": g.custo_parcial,
        "tendencia": {"ultimos_7d": g.tendencia.ultimos_7d, "anteriores_7d": g.tendencia.anteriores_7d,
                      "direcao": g.tendencia.direcao},
        "exemplos": [{"run_id": e.run_id, "attempt_id": e.attempt_id, "quando": e.quando, "erro": e.erro}
                     for e in g.exemplos],
        "erros_de_ia": dict(g.erros_de_ia), "primeira": g.primeira, "ultima": g.ultima,
        "onde_alterar": {"arquivos": list(onde.arquivos), "doc": onde.doc, "prova": onde.prova},
    }


def diagnostico_json(d: Diagnostico | None) -> JsonObject | None:
    """O diagnóstico determinístico de um grupo (item 30.13). `indeterminada` é dado, não erro: o curador a lê."""
    if d is None:
        return None
    return {
        "causa": d.causa.value, "indeterminada": d.indeterminada, "amostra": d.amostra,
        "fatos": [{"codigo": f.codigo, "valor": f.valor} for f in d.fatos],
        "conhecimento_envolvido": [{"ref": k.ref, "kind": k.kind, "papel": k.papel.value, "etapas": k.etapas,
                                    "aproximado": k.aproximado, "estado": k.estado} for k in d.conhecimento],
        "proposta": ({"tipo": d.proposta.tipo.value, "alvo": d.proposta.alvo, "causa": d.proposta.causa.value}
                     if d.proposta else None),
    }


def _item(x: LinhaDoRelatorio, regras: RegrasDoBacklog) -> JsonObject:
    return {**grupo_json(x.grupo, regras), "estado": x.estado.value, "registrado": x.registrado,
            "plan_item": x.plan_item, "licoes_ativas": x.licoes_ativas, "diagnostico": diagnostico_json(x.diagnostico)}


def _proposta(p: Proposta) -> JsonObject:
    return {"id": p.id, "cluster_key": p.cluster_key, "tipo": p.tipo.value, "ref": p.ref, "app": p.app,
            "titulo": p.titulo, "detalhe": p.detalhe, "fragmento": p.fragmento, "alvo": p.alvo, "causa": p.causa,
            "parent_id": p.parent_id}


def _linha_da_proposta(x: LinhaDaProposta) -> JsonObject:
    return {**_proposta(x.proposta), "estado": x.estado.value, "registrado": x.registrado}


def linha_json(x: LinhaDoBacklog) -> JsonObject:
    return {"id": x.id, "category": x.category.value, "cluster_key": x.cluster_key, "title": x.title,
            "state": x.state.value, "app": x.app_package, "capability": x.capability, "tipo": x.failure_kind,
            "tela": x.failure_screen, "plan_item": x.plan_item, "fixed_in_commit": x.fixed_in_commit,
            "fixed_at": x.fixed_at, "baseline": x.baseline, "verification": x.verification,
            "reopened_count": x.reopened_count, "first_seen": x.first_seen, "last_seen": x.last_seen,
            "notes": x.notes, "updated_by": x.updated_by, "updated_at": x.updated_at}


def relatorio_json(rel: RelatorioDeFalhas) -> JsonObject:
    j, f, s, regras = rel.janela, rel.filtros, rel.saude, rel.regras
    return {
        "gerado_em": rel.gerado_em, "commit": rel.commit,
        "janela": {"dias": j.dias, "desde": j.desde, "ate": j.ate, "corte_de_custo": j.corte_de_custo,
                   "custo_parcial": j.custo_parcial},
        "filtros": {"app": f.app, "camada": f.camada.value if f.camada else None, "limite": f.limite,
                    "simulados": f.simulados, "retroativo": f.retroativo},
        "regras": {"minimo_ocorrencias": regras.minimo_ocorrencias, "pessoa_usd": regras.pessoa_usd,
                   "aparelho_usd_min": regras.aparelho_usd_min, "prova_minimo": regras.prova_minimo,
                   "prova_fator": regras.prova_fator},
        "itens": [_item(x, regras) for x in rel.itens], "total_de_grupos": rel.total_de_grupos,
        "abaixo_do_minimo": rel.abaixo_do_minimo,
        "verificacao": [grupo_json(g, regras) for g in rel.verificacao],
        "telas": [{"app": t.app, "tela": t.tela, "chamadas_de_pessoa": t.chamadas_de_pessoa, "falhas": t.falhas,
                   "ultima": t.ultima} for t in rel.telas],
        "propostas": [_linha_da_proposta(p) for p in rel.propostas],
        "outro": {"ocorrencias": rel.outro.ocorrencias, "total": rel.outro.total, "pct": _r(rel.outro.pct, 4),
                  "alerta": rel.outro.alerta},
        "saude": {"itens_por_tipo": {k: dict(v) for k, v in s.itens_por_tipo.items()}, "execucoes": s.execucoes,
                  "execucoes_com_fluxo": s.execucoes_com_fluxo, "fluxos_distintos": s.fluxos_distintos,
                  "etapas_por_conducao": dict(s.etapas_por_conducao), "pct_por_receita": _r(s.pct_por_receita, 4),
                  "intervencoes": s.intervencoes, "intervencoes_por_10_execucoes": s.intervencoes_por_10_execucoes},
        "em_andamento": [linha_json(x) for x in rel.em_andamento],
    }


def _detalhe(d: DetalheDoBacklog, regras: RegrasDoBacklog) -> JsonObject:
    return {"linha": linha_json(d.linha), "registrado": d.registrado,
            "grupo": grupo_json(d.grupo, regras) if d.grupo else None,
            "proposta": _proposta(d.proposta) if d.proposta else None,
            "diagnostico": diagnostico_json(d.diagnostico)}


# ------------------------------------------------------------------ Markdown
def _cel(texto: object) -> str:
    return str(texto).replace("|", "\\|").replace("\n", " ").strip()


def _usd(valor: float) -> str:
    return f"{valor:.2f}".replace(".", ",")


def _pct(valor: float | None) -> str:
    return "—" if valor is None else f"{valor:.0%}"


def relatorio_md(rel: RelatorioDeFalhas) -> str:
    """O relatório para a sessão de desenvolvimento. A primeira coluna do topo é a chave `fk-*` do PATCH."""
    j, f, r = rel.janela, rel.filtros, rel.regras
    out = [f"# O que mais falha — {rel.gerado_em[:10]}", ""]
    out.append(f"Janela: {j.dias} dias ({j.desde} → {j.ate}) · commit implantado: {rel.commit or 'desconhecido'} · "
               "sem IA." + (f" App: {f.app}." if f.app else "") + (f" Camada: {f.camada.value}." if f.camada else "")
               + (" Inclui execuções simuladas." if f.simulados else ""))
    if f.retroativo:
        out.append("Inclui o legado classificado na leitura (retroativo): tentativas antigas sem `failure_kind` "
                   "gravado, nunca gravadas por este relatório.")
    else:
        out.append("Só o que a execução classificou ao gravar (sem o legado retroativo).")
    out.append(f"Custo: `ai_calls` das tentativas iniciadas a partir de {j.corte_de_custo[:16].replace('T', ' ')}"
               " UTC; antes, a média por tentativa na régua durável (`learning_daily`)."
               + (" **Custo parcial** (`*`): há tentativa na janela sem nenhuma das duas." if j.custo_parcial else ""))
    out.append(f"Regras: mínimo {r.minimo_ocorrencias} ocorrências no topo · intervenção = US$ {_usd(r.pessoa_usd)} · "
               f"minuto de aparelho = US$ {_usd(r.aparelho_usd_min)} · prova: ≥{r.prova_minimo} tentativas "
               f"elegíveis com taxa ≤ {r.prova_fator:.0%} da base. O custo total só ordena; o falso positivo do "
               "verificador fica sempre no topo.")
    out += ["", f"## 1. O que mais falha (top {f.limite}; {rel.total_de_grupos} grupos, {rel.abaixo_do_minimo} "
                "abaixo do mínimo)", ""]
    if rel.itens:
        out += ["| chave | camada | o quê | ocorrências (retroativas) | taxa | US$ | min | intervenções | "
                "7 d × 7 d | estado | onde alterar |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for x in rel.itens:
            g = x.grupo
            usd = _usd(g.usd_perdido) + ("*" if g.custo_parcial else "")
            out.append(f"| {g.id} | {g.camada.value} | {_cel(g.titulo)} | {g.ocorrencias} ({g.retroativas}) | "
                       f"{_pct(g.taxa)} | {usd} | {g.min_perdidos:.1f} | {g.intervencoes} | "
                       f"{g.tendencia.ultimos_7d}×{g.tendencia.anteriores_7d} {g.tendencia.direcao} | "
                       f"{x.estado.value}{' ' + x.plan_item if x.plan_item else ''} | "
                       f"{_cel(', '.join(g.onde_alterar.arquivos))} |")
        out.append("")
        for x in rel.itens:
            g = x.grupo
            out.append(f"- **{g.id}** — prova sugerida: {g.onde_alterar.prova} (doc: {g.onde_alterar.doc})."
                       + (f" Lições publicadas no escopo: {x.licoes_ativas}." if x.licoes_ativas else "")
                       + (f" Erros do provedor: {dict(g.erros_de_ia)}." if g.erros_de_ia else ""))
            d = x.diagnostico
            if d is not None:
                aproximados = sum(1 for k in d.conhecimento if k.aproximado)
                out.append(f"  - causa provável: **{d.causa.value}**"
                           + (" (indeterminada: o curador decide se pede a IA)" if d.indeterminada else "")
                           + f" — {_cel('; '.join(f'{f.codigo}: {f.valor}' for f in d.fatos))}."
                           + (f" Conhecimento: {', '.join(k.ref + ('~' if k.aproximado else '') for k in d.conhecimento[:6])}"
                              + (f" (~ = junção aproximada, {aproximados})" if aproximados else "") + "."
                              if d.conhecimento else "")
                           + (f" Proposta: {d.proposta.tipo.value} → {d.proposta.alvo}." if d.proposta else ""))
            for e in g.exemplos:
                out.append(f"  - `{e.run_id}` / `{e.attempt_id}` em {e.quando}: {_cel(e.erro or '(sem texto)')}")
    else:
        out.append("Nenhum grupo passou do mínimo na janela.")
    out += ["", "## 2. Verificação (falso positivo, falso negativo, confirmações à mão)", ""]
    if rel.verificacao:
        out += ["| chave | o quê | ocorrências | intervenções | última |", "|---|---|---|---|---|"]
        out += [f"| {g.id} | {_cel(g.titulo)} | {g.ocorrencias} | {g.intervencoes} | {g.ultima} |"
                for g in rel.verificacao]
    else:
        out.append("Nenhum sinal de pessoa desmentindo o verificador na janela.")
    out += ["", "## 3. Telas que chamaram uma pessoa", ""]
    if rel.telas:
        out += ["| app | tela | chamou pessoa | falhas | última |", "|---|---|---|---|---|"]
        out += [f"| {_cel(t.app)} | {_cel(t.tela)} | {t.chamadas_de_pessoa} | {t.falhas} | {t.ultima} |"
                for t in rel.telas]
    else:
        out.append("Nenhuma na janela.")
    out += ["", f"## 4. Propostas (sempre decisão de pessoa; {len(rel.propostas)})", ""]
    if rel.propostas:
        for n, p in enumerate(rel.propostas[:PROPOSTAS_NO_MD]):
            out.append(f"- **{p.proposta.id}** ({p.proposta.tipo.value}, {p.estado.value}): {_cel(p.proposta.titulo)} "
                       f"— {p.proposta.detalhe}")
            if n < FRAGMENTOS_NO_MD and p.proposta.fragmento:
                out +=["", "```yaml", p.proposta.fragmento, "```", ""]
        if len(rel.propostas) > PROPOSTAS_NO_MD:
            out.append(f"- … e mais {len(rel.propostas) - PROPOSTAS_NO_MD} no JSON (`formato=json`, chave `propostas`).")
    else:
        out.append("Nenhuma.")
    o = rel.outro
    out += ["", "## 5. Porcentagem de 'outro'", "",
            f"{o.ocorrencias} de {o.total} tentativas com falha ({_pct(o.pct)})."
            + (" **Acima de 15%: o classificador precisa de regra nova** (`modules/learning/domain/falhas.py`)."
               if o.alerta else "")]
    s = rel.saude
    itens = "; ".join(f"{k}: " + ", ".join(f"{e} {n}" for e, n in sorted(v.items()))
                      for k, v in sorted(s.itens_por_tipo.items())) or "nenhum"
    conducao = ", ".join(f"{k} {n}" for k, n in sorted(s.etapas_por_conducao.items())) or "nenhuma etapa"
    por_10 = "—" if s.intervencoes_por_10_execucoes is None else f"{s.intervencoes_por_10_execucoes:.1f}"
    out += ["", "## 6. Saúde do aprendizado", "",
            f"- Itens do livro por tipo e estado: {itens}.",
            f"- Etapas por condução: {conducao} ({_pct(s.pct_por_receita)} só por receita, entre as que registraram "
            "quem conduziu; `-` = sem registro).",
            f"- Execuções: {s.execucoes}; com fluxo reaproveitado: {s.execucoes_com_fluxo} "
            f"({s.fluxos_distintos} fluxos distintos).",
            f"- Intervenções: {s.intervencoes} ({por_10} a cada 10 execuções)."]
    out += ["", "## 7. Backlog em andamento", ""]
    if rel.em_andamento:
        out += ["| chave | estado | item do plano | commit | o quê | prova |", "|---|---|---|---|---|---|"]
        for b in rel.em_andamento:
            v = b.verification or {}
            prova = v.get("motivo") or v.get("faltam") or ""
            out.append(f"| {b.id} | {b.state.value} | {b.plan_item or ''} | {b.fixed_in_commit or ''} | "
                       f"{_cel(b.title)} | {_cel(prova)} |")
    else:
        out.append("Nada em andamento.")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ rotas
class CorpoDoBacklog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: EstadoDoBacklog
    plan_item: str | None = Field(None, max_length=60)
    fixed_in_commit: str | None = Field(None, max_length=40)
    notes: str | None = Field(None, max_length=500)


@router.get("/falhas", response_model=None)
async def o_que_mais_falha(request: Request, dias: int = Query(14, ge=1, le=90), app: str | None = None,
                           camada: Camada | None = None, limite: int = Query(20, ge=1, le=200),
                           simulados: bool = False, retroativo: bool = True,
                           formato: Literal["json", "md"] = "json") -> JsonObject | PlainTextResponse:
    falhas = _falhas(request)
    rel = falhas.relatorio(dias=dias, app=app or None, camada=camada, limite=limite, simulados=simulados,
                           retroativo=retroativo)
    if formato == "md":
        return PlainTextResponse(relatorio_md(rel), media_type="text/markdown; charset=utf-8")
    return relatorio_json(rel)


@router.get("/backlog/{backlog_id}", response_model=None)
async def ler_backlog(request: Request, backlog_id: str) -> JsonObject:
    falhas = _falhas(request)
    return _detalhe(_chamar(lambda: falhas.linha(backlog_id)), falhas.regras)


@router.patch("/backlog/{backlog_id}", response_model=None)
async def alterar_backlog(request: Request, backlog_id: str, corpo: CorpoDoBacklog) -> JsonObject:
    falhas = _falhas(request)
    quem = _quem(request)
    _chamar(lambda: falhas.alterar(backlog_id, estado=corpo.state, by=quem, plan_item=corpo.plan_item,
                                   fixed_in_commit=corpo.fixed_in_commit, notes=corpo.notes))
    return _detalhe(_chamar(lambda: falhas.linha(backlog_id)), falhas.regras)

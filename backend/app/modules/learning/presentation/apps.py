"""Rotas da visão por app (30.1), só leitura: o aprendizado COMPOSTO por app (declarado, aprendido, absorvido e a
camada de uso), sem tabela nova.

- `GET /api/aprendizado/apps`: a lista (registro de apps ∪ loja ∪ pacotes do livro), o balde `nao_resolvido` (30.2) e o
  que não tem eixo de app;
- `GET /api/aprendizado/apps/{pacote}`: o detalhe de um app (ou do balde `nao_resolvido`); 404 se o app não está em
  nenhuma das três fontes.

Entram em `router.py` ANTES do livro: `{kind}/{ref}` casaria com `apps/<pacote>` e recusaria o `kind` com 422."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.modules.learning.application.apps import (DetalheDoApp, ItemDeclarado, LinhaDoAprendido, ResumoDoApp,
                                                   VisaoDeApps, VisaoPorApp)
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.camada import ModosDeUso, Uso
from app.modules.learning.domain.saude import Saude
from app.modules.learning.infrastructure.montagem import modos_de_runtime
from app.modules.learning.presentation.livro import _chamar, _entrada, _servico
from app.modules.skills.domain.document import JsonObject, JsonValue

router = APIRouter(prefix="/api/aprendizado")


def _visao(request: Request) -> VisaoPorApp:
    visao = _servico(request).extensao(VisaoPorApp)
    if visao is None:
        raise HTTPException(503, detail={"code": "not_ready", "message": "A visão por app ainda não foi composta."})
    return visao


def _cfg(request: Request) -> object:
    return getattr(getattr(request.app.state, "poc", None), "cfg", None)


def _uso(u: Uso | None) -> JsonObject | None:
    return None if u is None else {"camada": u.camada.value, "porque": u.porque}


def _modos(m: ModosDeUso) -> JsonObject:
    return {"receitas": m.receitas, "fluxos": m.fluxos, "habilidades": m.habilidades,
            "licoes": m.licoes.value if m.licoes else None, "telas": m.telas.value if m.telas else None}


def _contagem(c: dict[str, dict[str, int]]) -> JsonObject:
    return {k: {estado: n for estado, n in v.items()} for k, v in c.items()}


def _resumo(r: ResumoDoApp) -> JsonObject:
    d, lj = r.declarado, r.loja
    declarado: JsonValue = None if d is None else {
        "arquivos": {"app": d.tem_app, "catalogo": d.tem_catalogo, "telas": d.tem_telas, "sessao": d.tem_sessao},
        "acoes": d.acoes, "telas": d.telas, "login_gerenciado": d.login_gerenciado}
    loja: JsonValue = None if lj is None else {"nome": lj.name, "nav_hints": lj.nav_hints,
                                               "known_selectors": lj.known_selectors}
    return {"pacote": r.pacote, "nome": r.nome, "existencia": r.existencia.value if r.existencia else None,
            "declarado": declarado, "loja": loja,
            "aprendido": {"total": r.total_aprendido, "contagem": _contagem(r.contagem)},
            "absorvido": r.absorvido, "uso": _contagem(r.uso)}


def _lista(v: VisaoDeApps) -> JsonObject:
    return {"apps": [_resumo(r) for r in v.apps], "total": len(v.apps), "nao_resolvido": _resumo(v.nao_resolvido),
            "fora_do_eixo": _contagem(v.fora_do_eixo), "modos": _modos(v.modos)}


def _item_declarado(i: ItemDeclarado) -> JsonObject:
    return {"tipo": i.arquivo.value, "arquivo": i.nome_do_arquivo, "presente": i.presente,
            "quantidade": i.quantidade, "uso": _uso(i.uso)}


def _linha(x: LinhaDoAprendido, servico: LearningService, saudes: dict[str, Saude],
           capabilities: dict[str, str | None]) -> JsonObject:
    return {**_entrada(x.entrada, servico, saudes.get(x.entrada.trail_ref), capabilities.get(x.entrada.trail_ref)),
            "origem_na_visao": x.origem.value,
            "uso": _uso(x.uso), "absorvida_em": x.absorvida_em}


def _detalhe(d: DetalheDoApp, servico: LearningService) -> JsonObject:
    # A saúde sai da MESMA função da lista do Livro (30.4): o painel não a recalcula nem a completa por outra rota.
    entradas = [x.entrada for x in (*d.aprendido, *d.absorvido)]
    saudes = servico.saudes(entradas)
    capabilities = servico.capabilities(entradas)       # em lote, como a saúde: nunca uma consulta por linha
    return {"app": _resumo(d.resumo), "declarado": [_item_declarado(i) for i in d.declarado],
            "aprendido": [_linha(x, servico, saudes, capabilities) for x in d.aprendido],
            "absorvido": [_linha(x, servico, saudes, capabilities) for x in d.absorvido], "modos": _modos(d.modos)}


@router.get("/apps", response_model=None)
async def listar_apps(request: Request) -> JsonObject:
    return _lista(_visao(request).apps(modos_de_runtime(_cfg(request))))


@router.get("/apps/{pacote}", response_model=None)
async def ler_app(request: Request, pacote: str) -> JsonObject:
    visao = _visao(request)
    runtime = modos_de_runtime(_cfg(request))
    return _detalhe(_chamar(lambda: visao.detalhe(pacote, runtime)), _servico(request))

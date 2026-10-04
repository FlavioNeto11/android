"""30.61: a prévia da porta do despacho sobre uma execução `planned` (`GET /api/runs/{id}/porta`).

O dono vê, ANTES de iniciar, o que cada etapa com efeito vai encontrar na porta: liberada, pede o aval dele, espera até
uma hora, não será feita, ou só se decide na execução. É a MESMA conta do despacho (`AppState.vereditos_da_porta`), só
lendo: não grava decisão, não prende exceção, não abre pedido de aprovação, não escreve rascunho, não chama IA. A prévia
abandonada não ocupa teto nem marca alvo.

O selo é uma ESTIMATIVA: a execução é paralela entre aparelhos e a porta roda de novo no despacho. A passada é cumulativa
na ordem do plano só para o que a prévia consegue ver (o mesmo efeito, sobre o mesmo objeto, duas vezes no plano); os
tetos por balde não somam os itens anteriores do plano (o `check` conta o que já aconteceu), e o despacho corrige.

Falha parcial: o item cuja porta não pôde ser calculada sai `na_execucao` com `falhou: true` e sem chave (nunca ganha
Aprovar). Falha de todos: `total: true`, e sobra "Iniciar e decidir na execução" (o fluxo de hoje).
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import TYPE_CHECKING

from .db import Row, loads
from .models import InteractionType
from .planning.capabilities import contraparte, objeto_da_acao, texto_a_gerar
from .social.chave_da_aprovacao import chave_da_aprovacao, midia_da_etapa, texto_exato
from .util import now, to_iso

if TYPE_CHECKING:
    from .state import AppState, PortaDaEtapa

log = logging.getLogger("poc.porta_do_plano")

#: Selos da prévia (desenho 30.61, §4).
PERMITIDO, APROVACAO, ADIADO, RECUSADO, NA_EXECUCAO = "permitido", "aprovacao", "adiado", "recusado", "na_execucao"
#: Validade padrão da aprovação antecipada, em horas (`LimitsCfg.aprovacao_no_plano_validade_h`).
VALIDADE_PADRAO_H = 24
#: O que sempre pede a pessoa na execução, qualquer que seja o plano (ADR-009).
SEMPRE_NA_EXECUCAO = ("desafio", "2FA", "CAPTCHA")


class PortaIndisponivel(Exception):
    """A prévia não se aplica (execução inexistente, fora de `planned`, sem plano). `codigo` e `status` vão à rota."""

    def __init__(self, codigo: str, mensagem: str, status: int = 409):
        super().__init__(mensagem)
        self.codigo, self.mensagem, self.status = codigo, mensagem, status


def validade_h(state: AppState) -> int:
    return int(getattr(state.settings.get(), "aprovacao_no_plano_validade_h", VALIDADE_PADRAO_H) or VALIDADE_PADRAO_H)


def hash_do_plano(run: Row, etapas: list[Row]) -> str:
    """Atalho para "o plano mudou?": o plano gravado e, por etapa, id, versão e argumentos. Não é prova (não cobre perfil
    nem aparelho, que moram no objetivo): o gesto recalcula a chave de cada item."""
    partes = [str(run["plan"] or "")] + [f"{e['id']}|{e['plan_version']}|{e['bindings'] or ''}" for e in etapas]
    return hashlib.sha256("\n".join(partes).encode("utf-8")).hexdigest()


def _dependentes(etapas_do_objetivo: list[Row]) -> dict[str, list[str]]:
    """Para cada etapa, as etapas do MESMO objetivo que dependem dela, direta ou indiretamente (tirar uma tira as outras)."""
    por_chave = {str(e["key"]): str(e["id"]) for e in etapas_do_objetivo}
    diretos: dict[str, set[str]] = {str(e["id"]): set() for e in etapas_do_objetivo}
    for e in etapas_do_objetivo:
        for chave in loads(e["depends_on"], []) or []:
            pai = por_chave.get(str(chave))
            if pai is not None:
                diretos[pai].add(str(e["id"]))
    saida: dict[str, list[str]] = {}
    for inicio in diretos:
        vistos: set[str] = set()
        pilha = list(diretos[inicio])
        while pilha:
            atual = pilha.pop()
            if atual not in vistos:
                vistos.add(atual)
                pilha.extend(diretos.get(atual, ()))
        saida[inicio] = sorted(vistos)
    return saida


def _selo(porta: PortaDaEtapa, alvo_por_resolver: bool, repetida: str) -> tuple[str, str, str, str | None]:
    """`(selo, motivo, dica, retry_at)` do veredito da porta, com a regra do que fica para a execução. Espelha o fim do
    `_policy_gate`: aprovação por política, por DM fria (o porquê vem no `reason` do veredito que libera), pela
    confirmação do mesmo pedido a várias contas, pelo teto `preparar` ou por mensagem repetida."""
    v = porta.veredito
    if v is None:
        return PERMITIDO, "", "", None
    if alvo_por_resolver:
        # Alvo `{item}`, `{{saida:…}}` ou não dito: hoje a porta de frota RECUSA o alvo `None`; na prévia, o alvo ainda
        # pode vir da tela (uma coleta), e o veredito é do despacho.
        return NA_EXECUCAO, "o alvo só se conhece na execução", "", None
    if not v.allowed:
        if v.retry_at:
            return ADIADO, v.reason, v.hint, v.retry_at
        return RECUSADO, v.reason, v.hint, None
    if v.excecao is not None:
        # 30.65: o efeito com exceção de política sempre pede decisão nova, com a exceção presa; o sim do plano é
        # anterior a ela e nunca o cobre.
        return NA_EXECUCAO, "; ".join(m for m in (v.reason, f"usa a exceção de política {v.excecao}: a decisão é "
                                                            "pedida na execução, com a exceção presa") if m), v.hint, None
    pelo_teto = ("teto de autonomia preparar: o efeito precisa da sua aprovação"
                 if porta.teto == "preparar" and porta.cap is not None and porta.cap.side_effect else "")
    if v.needs_approval or porta.confirmacao or pelo_teto or repetida:
        return APROVACAO, "; ".join(m for m in (v.reason, porta.confirmacao, pelo_teto, repetida) if m), v.hint, None
    return PERMITIDO, v.reason, v.hint, None


def previa_da_porta(state: AppState, run_id: str) -> dict[str, object]:
    run = state.repo.run_row(run_id)
    if run is None:
        raise PortaIndisponivel("not_found", "Execução não encontrada.", 404)
    if run["status"] != "planned":
        raise PortaIndisponivel("invalid_state", f"A prévia da porta é da execução com plano pronto (`planned`); esta "
                                                 f"está em '{run['status']}'.")
    objetivos = {str(o["id"]): o for o in state.db.query(
        "SELECT * FROM objectives WHERE run_id=? ORDER BY instance_id, id", (run_id,))}
    etapas = [e for e in state.db.query("SELECT * FROM steps WHERE run_id=? AND status<>'cancelled'"
                                        " ORDER BY objective_id, seq", (run_id,))
              if (o := objetivos.get(str(e["objective_id"]))) is not None and e["plan_version"] == o["plan_version"]]
    if not etapas:
        raise PortaIndisponivel("no_plan", "A execução ainda não tem plano materializado.")
    por_objetivo: dict[str, list[Row]] = {}
    for e in etapas:
        por_objetivo.setdefault(str(e["objective_id"]), []).append(e)
    dependentes = {k: v for lista in por_objetivo.values() for k, v in _dependentes(lista).items()}
    rotulos = {str(r["id"]): f"@{r['username']}" if r["username"] else str(r["id"])
               for r in state.db.query("SELECT id, username FROM instagram_profiles")}

    itens: list[dict[str, object]] = []
    vistos: set[tuple[str, str, str]] = set()          # (perfil, ação, objeto): o mesmo efeito duas vezes no plano
    modelos_for_each = 0
    for e in etapas:
        if e["for_each"]:
            modelos_for_each += 1                       # etapa-modelo: os itens nascem da coleta, na execução
            continue
        obj = objetivos[str(e["objective_id"])]
        item = _item(state, run, obj, e, dependentes.get(str(e["id"]), []), rotulos, vistos)
        if item is not None:
            itens.append(item)

    falhas = sum(1 for i in itens if i.get("falhou"))
    return {
        "run_id": run_id, "hash_do_plano": hash_do_plano(run, etapas),
        "validade_ate": to_iso(now() + timedelta(hours=validade_h(state))), "custo_rascunhos_usd": 0.0,
        "estimativa": True, "parcial": 0 < falhas < len(itens), "total": bool(itens) and falhas == len(itens),
        "itens": itens,
        "na_execucao": {"textos_da_tela": sum(1 for i in itens if i.get("texto_na_execucao")),
                        "itens_for_each": modelos_for_each, "sempre": list(SEMPRE_NA_EXECUCAO)},
    }


def _item(state: AppState, run: Row, obj: Row, e: Row, dependentes: list[str], rotulos: Mapping[str, str],
          vistos: set[tuple[str, str, str]]) -> dict[str, object] | None:
    """Uma etapa com efeito (ou com ação do catálogo) na prévia; `None` para a etapa que a porta nem olha."""
    base: dict[str, object] = {"objective_id": obj["id"], "step_id": e["id"], "aparelho": obj["instance_id"],
                               "titulo": e["title"], "dependentes": dependentes}
    try:
        porta = state.vereditos_da_porta(obj, e, run)
    except Exception as exc:  # noqa: BLE001 - a falha de UM item não derruba a prévia; ele não ganha Aprovar
        log.exception("30.61: a porta da etapa %s não pôde ser calculada na prévia", e["id"])
        return {**base, "selo": NA_EXECUCAO, "falhou": True, "chave": None,
                "motivo": f"a porta não pôde ser calculada para este item ({type(exc).__name__}): ela decide na execução",
                "dica": "", "retry_at": None}
    cap = porta.cap
    sem_aparelho = state.devices.devices.get(obj["instance_id"]) is None
    if sem_aparelho and (e["capability"] or e["side_effect"]):
        # Sem o aparelho no gerenciador não há app da etapa, nem catálogo, nem política: a porta decide no despacho.
        return {**base, "selo": NA_EXECUCAO, "falhou": False, "chave": None, "acao": e["capability"],
                "motivo": "o aparelho não está no gerenciador agora: a porta decide no despacho", "dica": "",
                "retry_at": None}
    if cap is None and porta.veredito is None and not e["side_effect"]:
        return None                                     # sem ação do catálogo e sem efeito: a porta não tem o que dizer
    bindings = loads(e["bindings"], {}) or {}
    alvo = contraparte(cap, bindings) if cap is not None else None
    alvo_por_resolver = cap is not None and bool(cap.counterparty) and alvo is None
    repetida = ""
    if cap is not None and porta.profile_id and porta.veredito is not None and porta.veredito.allowed:
        repetida = state.policies.mensagem_repetida(str(porta.profile_id), cap, bindings, app_id=porta.app_id,
                                                    step_id=e["id"]) or ""
    selo, motivo, dica, retry_at = _selo(porta, alvo_por_resolver, repetida)
    objeto = objeto_da_acao(cap, bindings) if cap is not None else None
    fechado, texto = texto_exato(cap, bindings) if cap is not None else (True, None)
    texto_na_execucao = cap is not None and cap.needs_draft and texto_a_gerar(bindings) is not None
    tem_imagem, imagem_sha = midia_da_etapa(state.db, bindings)
    chave = None
    if selo in (APROVACAO, PERMITIDO) and cap is not None and cap.side_effect and objeto is not None:
        assinatura = (str(porta.profile_id), cap.key, json.dumps(objeto, sort_keys=True))
        if assinatura in vistos:
            if cap.limit_bucket == "dms" or cap.interaction_type == InteractionType.dm_sent.value:
                # A mensagem repetida não é recusada na porta: vira confirmação depois que a primeira sai (30.64). A
                # chave seria a MESMA da primeira; sem chave, a pergunta acontece na execução.
                selo, motivo, dica = NA_EXECUCAO, ("repete uma mensagem anterior deste plano ao mesmo alvo: a porta "
                                                   "pergunta na execução, depois da primeira"), ""
            else:
                selo, motivo, dica = RECUSADO, "o mesmo efeito sobre o mesmo objeto já está neste plano para este perfil", ""
        vistos.add(assinatura)
    if selo == APROVACAO:
        if texto_na_execucao:
            # Rascunho na prévia é a fatia seguinte: o texto é escrito na execução, e aprovação sem texto nunca libera
            # escrita. O item fica para a execução, sem Aprovar.
            selo, motivo = NA_EXECUCAO, "; ".join(m for m in (motivo, "o texto é escrito na execução") if m)
        elif cap is not None and porta.profile_id:
            chave = chave_da_aprovacao(bindings, cap, perfil=str(porta.profile_id), aparelho=str(obj["instance_id"]),
                                       pacote=porta.pacote, run_id=str(run["id"]), objective_id=str(obj["id"]),
                                       tem_imagem=tem_imagem, midia_sha256=imagem_sha)
            if chave is None:
                selo, motivo = NA_EXECUCAO, "; ".join(
                    m for m in (motivo, "o item ainda não está fechado (objeto, alvo, texto ou imagem): ele se decide "
                                        "na execução") if m)
    return {**base, "persona_rotulo": rotulos.get(str(porta.profile_id), None) if porta.profile_id else None,
            "profile_id": porta.profile_id, "app": porta.app_id or e["app_id"], "acao": cap.key if cap else e["capability"],
            "alvo": alvo, "objeto_alvo": objeto, "selo": selo, "motivo": motivo, "dica": dica, "retry_at": retry_at,
            "texto": texto if fechado else None, "texto_na_execucao": texto_na_execucao, "tem_imagem": tem_imagem,
            "imagem_sha256": imagem_sha, "chave": chave, "falhou": False}

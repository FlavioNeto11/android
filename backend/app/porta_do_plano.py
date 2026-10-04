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

from pydantic import BaseModel, Field

from .db import Row, loads
from .models import RUN_TERMINAL, InteractionType, StepStatus
from .planning.capabilities import contraparte, objeto_da_acao, texto_a_gerar
from .social.approvals import apply_edit
from .social.chave_da_aprovacao import VERSAO_DA_CHAVE, chave_da_aprovacao, midia_da_etapa, tem_variavel, texto_exato
from .taskqueue.repository import MOTIVO_REJEICAO
from .util import now, now_iso, to_iso

if TYPE_CHECKING:
    from .state import AppState, PortaDaEtapa

log = logging.getLogger("poc.porta_do_plano")

#: Selos da prévia (desenho 30.61, §4).
PERMITIDO, APROVACAO, ADIADO, RECUSADO, NA_EXECUCAO = "permitido", "aprovacao", "adiado", "recusado", "na_execucao"
#: Validade padrão da aprovação antecipada, em horas (`LimitsCfg.aprovacao_no_plano_validade_h`).
VALIDADE_PADRAO_H = 24
#: O maior texto editado aceito no cartão do plano (o do comentário e da legenda do Instagram).
LIMITE_DO_TEXTO = 2200
#: O que sempre pede a pessoa na execução, qualquer que seja o plano (ADR-009).
SEMPRE_NA_EXECUCAO = ("desafio", "2FA", "CAPTCHA")


class PortaIndisponivel(Exception):
    """A prévia ou o gesto não se aplicam (execução inexistente, fora de `planned`, sem plano, item que mudou). `codigo`,
    `status` e `extra` vão à rota."""

    def __init__(self, codigo: str, mensagem: str, status: int = 409, **extra: object):
        super().__init__(mensagem)
        self.codigo, self.mensagem, self.status, self.extra = codigo, mensagem, status, extra


class ItemAprovado(BaseModel):
    """Um item 🔒 da prévia que o dono aprova: a etapa e a chave que ele VIU."""

    step_id: str = Field(min_length=1, max_length=300)
    chave: str = Field(min_length=64, max_length=64)
    #: O texto EDITADO no cartão do plano (`None`: o da prévia). A chave conferida é a que o dono viu (a do texto da
    #: prévia); a gravada é a do texto editado, recalculada da etapa relida: a chave é a do texto que vai sair.
    texto: str | None = Field(default=None, max_length=20_000)   # o limite real (LIMITE_DO_TEXTO) tem mensagem própria


class AprovarPlanoBody(BaseModel):
    """`POST /runs/{id}/aprovar-plano`: os itens aprovados (com a chave vista) e as etapas tiradas ("Não fazer esta").
    As dependentes das tiradas saem junto, pela conta do servidor, nunca pela lista do cliente."""

    aprovar: list[ItemAprovado] = Field(default_factory=list, max_length=500)
    tirar: list[str] = Field(default_factory=list, max_length=500)


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


def _plano(state: AppState, run_id: str) -> tuple[Row, dict[str, Row], list[Row], dict[str, list[str]]]:
    """`(run, objetivos, etapas da versão atual não canceladas, dependentes transitivos)` de uma execução `planned`."""
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
    return run, objetivos, etapas, dependentes


def previa_da_porta(state: AppState, run_id: str) -> dict[str, object]:
    run, objetivos, etapas, dependentes = _plano(state, run_id)
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


def _texto(valor: object) -> str | None:
    return None if valor is None else str(valor)


def _itens(previa: Mapping[str, object]) -> list[dict[str, object]]:
    itens = previa.get("itens")
    return [i for i in itens if isinstance(i, dict)] if isinstance(itens, list) else []


#: O `detail` da etapa tirada. Começa por `MOTIVO_REJEICAO` porque é DECISÃO, não lacuna: é esse prefixo que faz o
#: `recovery_steps` ("Tentar novamente", recuperação automática) não recriar a etapa que o dono tirou.
MOTIVO_TIRADA = f"{MOTIVO_REJEICAO}: tirada na prévia da porta (30.61), o dono escolheu não fazer"


def aprovar_plano(state: AppState, run_id: str, corpo: AprovarPlanoBody, *, por: str) -> dict[str, object]:
    """30.61: o gesto "Aprovar N e iniciar". Recalcula a prévia AGORA (a mesma conta do `GET`) e compara item a item com
    o que o dono viu: qualquer item que não é mais 🔒 com a MESMA chave devolve 409 `plano_mudou`, com a lista e a
    prévia nova, e nada é gravado. Senão, numa transação, grava cada sim como aprovação `approved` de origem `plano`
    (chave, versão do plano, validade, sha256 da mídia) e cancela as tiradas e as dependentes delas; depois inicia a
    execução. O sim só vale na execução pelo `_approval_gate`, que recalcula a chave da etapa relida."""
    previa = previa_da_porta(state, run_id)
    _run, objetivos, etapas, dependentes = _plano(state, run_id)
    itens = {str(i["step_id"]): i for i in _itens(previa)}
    do_plano = {str(e["id"]) for e in etapas}
    pedidos = list(dict.fromkeys((i.step_id, i.chave) for i in corpo.aprovar))
    editados = {i.step_id: i.texto.strip() for i in corpo.aprovar if i.texto is not None}
    if len({sid for sid, _c in pedidos}) != len(pedidos):
        raise PortaIndisponivel("invalid_body", "A mesma etapa veio com duas chaves.", 422)
    if set(corpo.tirar) & {sid for sid, _c in pedidos}:
        raise PortaIndisponivel("invalid_body", "A mesma etapa veio para aprovar e para tirar.", 422)
    mudaram: list[dict[str, object]] = []
    for sid, chave in pedidos:
        item = itens.get(sid)
        if item is None or item["selo"] != APROVACAO or not item["chave"] or item["chave"] != chave:
            mudaram.append({"step_id": sid, "selo": item["selo"] if item else None,
                            "motivo": (item["motivo"] if item else "a etapa não está mais no plano")
                            or "a chave mudou: o item não é mais o que você viu"})
    mudaram += [{"step_id": sid, "selo": None, "motivo": "a etapa não está mais no plano"}
                for sid in corpo.tirar if sid not in do_plano]
    for sid, texto in editados.items():
        item = itens.get(sid)
        if item is None or sid in {m["step_id"] for m in mudaram}:
            continue
        if item.get("texto") is None:
            raise PortaIndisponivel("invalid_body", f"A etapa {sid} não escreve texto: não há o que editar.", 422)
        if not texto:
            raise PortaIndisponivel("invalid_body", "O texto editado está vazio.", 422)
        if len(texto) > LIMITE_DO_TEXTO:
            raise PortaIndisponivel("texto_longo", f"O texto editado tem {len(texto)} caracteres; o limite é "
                                                   f"{LIMITE_DO_TEXTO}.", 422)
        if tem_variavel(texto):
            raise PortaIndisponivel("invalid_body", "O texto editado tem variável por resolver ({nome}): escreva o "
                                                    "texto final.", 422)
    if mudaram:
        raise PortaIndisponivel("plano_mudou", f"{len(mudaram)} item(ns) mudaram desde a prévia; nada foi gravado.",
                                mudaram=mudaram, previa=previa)
    tirados = sorted({*corpo.tirar, *(d for sid in corpo.tirar for d in dependentes.get(sid, []))})
    aprovar = [(sid, chave) for sid, chave in pedidos if sid not in tirados]   # a dependente de uma tirada sai junto
    validade = to_iso(now() + timedelta(hours=validade_h(state)))
    etapa_por_id = {str(e["id"]): e for e in etapas}
    gravadas: list[str] = []
    with state.db.tx():
        # Dois gestos ao mesmo tempo: a transação serializa, e o segundo vê o sim do primeiro (ou a execução iniciada).
        if state.db.scalar("SELECT status FROM runs WHERE id=?", (run_id,)) != "planned" or state.db.scalar(
                "SELECT 1 FROM pending_approvals WHERE run_id=? AND origem='plano' LIMIT 1", (run_id,)):
            raise PortaIndisponivel("invalid_state", "O plano desta execução já foi aprovado ou iniciado.")
        for sid, chave in aprovar:
            item, e = itens[sid], etapa_por_id[sid]
            obj = objetivos[str(e["objective_id"])]
            novo = editados.get(sid)
            if novo is not None and novo != str(item.get("texto") or "").strip():
                apply_edit(state.db, sid, novo)
                # O selo se refaz com o texto editado (revisão do painel, B1): há regra que depende do texto (o
                # comentário repetido é recusado, a DM repetida pede confirmação). Se o item editado não é mais um 🔒
                # com chave, nada se grava (o raise dentro da transação desfaz a edição) e o dono ouve o porquê.
                refeito = _item(state, _run, obj, state.repo.step_row(sid), [], {}, set())
                if refeito is None or refeito["selo"] != APROVACAO or not refeito["chave"]:
                    motivo = str(refeito["motivo"] if refeito else "") or "o item não fecha mais uma aprovação"
                    raise PortaIndisponivel(
                        "plano_mudou", "Com o texto editado, o item não é mais o que se aprova no plano; nada foi "
                                       "gravado.", previa=previa,
                        mudaram=[{"step_id": sid, "selo": refeito["selo"] if refeito else None,
                                  "motivo": f"com o texto editado: {motivo}"}])
                chave = str(refeito["chave"])
                item = {**item, "texto": novo}
            gravadas.append(state.approvals.aprovar_no_plano(
                profile_id=str(item["profile_id"]), capability=str(item["acao"]),
                summary=f"{e['title']} — aprovado na prévia da porta", target=_texto(item.get("alvo")),
                content=_texto(item.get("texto")), run_id=run_id, objective_id=str(obj["id"]), step_id=sid,
                chave_sha256=chave, chave_v=VERSAO_DA_CHAVE, plan_version=int(obj["plan_version"]), expires_at=validade,
                midia_sha256=_texto(item.get("imagem_sha256")), decided_by=por).id)
        for sid in tirados:
            state.repo.transition_step(sid, StepStatus.cancelled, detail=MOTIVO_TIRADA)
        state.repo.decision(f"plano aprovado na prévia da porta por {por}: {len(gravadas)} item(ns) aprovado(s) até "
                            f"{validade}, {len(tirados)} etapa(s) tirada(s)", run_id=run_id)
    resumo = state.runs.start(run_id, por=por)
    return {"run": resumo.model_dump(mode="json"), "aprovacoes": gravadas, "tiradas": tirados, "validade_ate": validade}


def renovar_plano(state: AppState, run_id: str) -> dict[str, object]:
    """30.61 "Renovar": a validade dos sins do plano ainda em aberto volta a contar de agora (`validade_h`), sem reabrir os
    itens. Só em execução viva; a chave segue a mesma e o despacho a confere de novo."""
    run = state.repo.run_row(run_id)
    if run is None:
        raise PortaIndisponivel("not_found", "Execução não encontrada.", 404)
    if run["status"] in {s.value for s in RUN_TERMINAL}:
        raise PortaIndisponivel("invalid_state", f"A execução está em '{run['status']}': não há o que renovar.")
    validade = to_iso(now() + timedelta(hours=validade_h(state)))
    with state.db.tx():
        renovadas = state.approvals.renovar_do_plano(run_id, expires_at=validade)
        # O que já venceu não se renova: sai agora (como a faxina faria) e a decisão volta ao dono.
        vencidas = state.approvals.vencer_do_plano(now_iso(), run_id=run_id)
    if renovadas:
        state.repo.decision(f"validade dos sins do plano renovada até {validade}: {renovadas} item(ns)", run_id=run_id)
    if vencidas:
        # Só a contagem (sem alvo nem texto): o que venceu volta para o dono rever, e o log diz quantos.
        state.repo.decision(f"{vencidas} sim(ns) do plano já vencido(s) não se renovaram: voltam para você rever",
                            run_id=run_id)
    if vencidas and not renovadas:
        raise PortaIndisponivel("sim_vencido", f"{vencidas} sim(ns) do plano já tinham vencido e não se renovam: "
                                               "reveja a prévia (ou a porta pergunta na execução).", vencidas=vencidas)
    return {"run_id": run_id, "renovadas": renovadas, "vencidas": vencidas, "validade_ate": validade}

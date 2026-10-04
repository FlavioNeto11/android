"""Formatos de saída estruturada e a conversão JSON → objeto do domínio, comuns a TODOS os provedores.

Por que saiu de `anthropic_provider.py`: com um segundo provedor real (o compatível com OpenAI), a tradução de
"o que o modelo devolveu" para `Plan`/`Verdict`/`SocialDraftDTO` passou a ter dois chamadores. Copiada, ela
viraria duas verdades — e a diferença só apareceria como plano válido num provedor e inválido no outro.

O que fica com cada provedor: o TRANSPORTE (como se pede saída estruturada, como se mandam ferramentas e imagem)
e o tratamento de erro da API. Isso sim é específico de cada um.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from ..models import (DeliveryLevel, ForaDoCatalogo, MissingInfo, Plan, PlannerInfo, PlanStep, Postcondition,
                      SocialDraftDTO)
from ..taskqueue.saidas import referencias
from .capabilities import (CapabilityCatalog, CapabilityNode, compose, herdar_argumentos, load_catalog,
                           montar_etapa)
from .provider import AIError, PlanRequest, Verdict


# ---- formatos de saída estruturada (compatíveis com strict) -------------------
class _ParamOut(BaseModel):
    name: str
    value: str


class _PostOut(BaseModel):
    kind: Literal["text_visible", "app_foreground", "element_present", "model_judged", "items_collected"]
    value: str
    description: str
    required_delivery_level: DeliveryLevel | None


class _StepOut(BaseModel):
    key: str
    title: str
    goal: str
    depends_on: list[str]
    side_effect: bool
    commit_guard: list[str]
    precondition: str | None
    postcondition: _PostOut
    timeout_s: int
    max_attempts: int
    for_each: str | None
    # Item 12.1: app desta etapa quando o comando atravessa apps; null = o app do plano.
    app_id: str | None = None
    # Item 24.3 (contrato C2): os nomes dos valores que esta etapa LÊ para as seguintes (`{{saida:<nome>}}`).
    saidas: list[str] = []
    opcional: bool = False            # item 31.36: etapa que só limpa a tela (o parsing confere a regra)


class _PlanOut(BaseModel):
    summary: str
    app_id: str | None
    parameters: list[_ParamOut]
    success_criteria: list[str]
    steps: list[_StepOut]
    missing: list[MissingInfo]


# Formato do planejamento COM catálogo: por etapa, só o que o modelo realmente decide. Esquema pequeno é esquema
# que valida; a etapa em si é montada pelo backend, com texto revisado por gente.
class _BindingOut(BaseModel):
    name: str
    value: str


class _CapStepOut(BaseModel):
    key: str
    capability: str
    depends_on: list[str]
    bindings: list[_BindingOut]
    for_each: str | None


class _ForaOut(BaseModel):
    """Item 31.33: o pedido que nenhuma ação do catálogo do app cobre. `pedido` é a ação pedida em poucas palavras."""
    app_id: str | None
    pedido: str


class _CapPlanOut(BaseModel):
    summary: str
    parameters: list[_ParamOut]
    success_criteria: list[str]
    steps: list[_CapStepOut]
    missing: list[MissingInfo]
    fora_do_catalogo: list[_ForaOut] = []


# Formato do planejamento ENTRE APPS (item 24.1, ADR-058): cada etapa diz o app dela e é AÇÃO do catálogo (app com
# catálogo) ou LIVRE (app sem catálogo). Um objeto aninhado e anulável para a parte livre, e não oito campos anuláveis
# soltos: esquema estrito com menos uniões, e a etapa de catálogo gasta um `null` em vez de oito.
class _LivreOut(BaseModel):
    title: str
    goal: str
    side_effect: bool
    commit_guard: list[str]
    precondition: str | None
    postcondition: _PostOut
    timeout_s: int
    max_attempts: int
    saidas: list[str] = []            # item 24.3: o valor que a etapa livre lê (na de catálogo, `saidas` da etapa)
    opcional: bool = False            # item 31.36: etapa que só limpa a tela (o parsing confere a regra)


class _MultiStepOut(BaseModel):
    key: str
    app_id: str
    capability: str | None
    bindings: list[_BindingOut]
    livre: _LivreOut | None
    depends_on: list[str]
    for_each: str | None
    # Item 24.3 (ADR-065): os valores que a etapa de CATÁLOGO lê para as seguintes — só os que a ação declara poder
    # entregar (`Capability.saidas`). Na etapa livre o nome vai em `livre.saidas`, como sempre.
    saidas: list[str] = []


class _MultiPlanOut(BaseModel):
    summary: str
    app_id: str | None
    parameters: list[_ParamOut]
    success_criteria: list[str]
    steps: list[_MultiStepOut]
    missing: list[MissingInfo]
    fora_do_catalogo: list[_ForaOut] = []


# Formato CURTO da etapa livre (LT-4b, `ai.esquema_do_plano: curto`): o plano custa ~6,6 ms por token de saída, e
# metade da saída era estrutura. Saem os campos que o backend sabe preencher (`description` vem do `value`;
# `precondition` fica nula; `max_attempts` é 1 no efeito e 3 nas demais). Classes à parte, e não herança: a ordem
# dos campos é a do esquema estrito, e o formato longo tem de seguir idêntico.
class _PostCurtoOut(BaseModel):
    kind: Literal["text_visible", "app_foreground", "element_present", "model_judged", "items_collected"]
    value: str
    required_delivery_level: DeliveryLevel | None


class _StepCurtoOut(BaseModel):
    key: str
    title: str
    goal: str
    depends_on: list[str]
    side_effect: bool
    commit_guard: list[str]
    postcondition: _PostCurtoOut
    timeout_s: int
    for_each: str | None
    app_id: str | None = None
    saidas: list[str] = []
    opcional: bool = False            # item 31.36: etapa que só limpa a tela (o parsing confere a regra)


class _PlanCurtoOut(BaseModel):
    summary: str
    app_id: str | None
    parameters: list[_ParamOut]
    success_criteria: list[str]
    steps: list[_StepCurtoOut]
    missing: list[MissingInfo]


class _LivreCurtoOut(BaseModel):
    title: str
    goal: str
    side_effect: bool
    commit_guard: list[str]
    postcondition: _PostCurtoOut
    timeout_s: int
    saidas: list[str] = []
    opcional: bool = False            # item 31.36: etapa que só limpa a tela (o parsing confere a regra)


class _MultiStepCurtoOut(BaseModel):
    key: str
    app_id: str
    capability: str | None
    bindings: list[_BindingOut]
    livre: _LivreCurtoOut | None
    depends_on: list[str]
    for_each: str | None
    saidas: list[str] = []


class _MultiPlanCurtoOut(BaseModel):
    summary: str
    app_id: str | None
    parameters: list[_ParamOut]
    success_criteria: list[str]
    steps: list[_MultiStepCurtoOut]
    missing: list[MissingInfo]
    fora_do_catalogo: list[_ForaOut] = []


#: Tentativas da etapa livre sem efeito no formato curto: o padrão de `PlanStep`. No formato longo o modelo escolhia
#: 2 ou 3 sem regra (na última semana do QA, metade de cada), e a etapa com efeito já era forçada a 1.
TENTATIVAS_DO_FORMATO_CURTO = 3


def _descricao_derivada(kind: str, value: str) -> str:
    """A `description` que o formato curto não pede. É o que o ator e o verificador leem como "pós-condição a
    comprovar": no `model_judged`, o próprio `value` (que o planejador escreve para o verificador com visão); nos
    demais, uma frase com o `value`, que é o que a conferência local confere."""
    if kind == "model_judged":
        return value
    if kind == "text_visible":
        return f'O texto "{value}" está visível na tela.'
    if kind == "element_present":
        return f"A tela mostra o elemento {value}."
    if kind == "app_foreground":
        return f"O app {value} está em primeiro plano."
    return f"A lista está visível e foi lida até o fim (item: {value})."


def norm_key(key: str) -> str:
    """O schema estrito não carrega o `pattern` da chave; normaliza 'Open-App' → 'open_app' em vez de rejeitar o plano."""
    k = re.sub(r"[^a-z0-9_]+", "_", key.strip().lower()).strip("_")[:40]
    return k if re.match(r"^[a-z]", k) and len(k) >= 2 else f"step_{k or 'x'}"


def loads_json(raw: str, what: str) -> Any:
    """JSON do modelo. Um provedor sem `json_schema` costuma embrulhar em cerca de código — desembrulhar aqui é
    mais barato e muito mais previsível do que pedir de novo."""
    texto = (raw or "").strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```[a-zA-Z]*\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto).strip()
    try:
        return json.loads(texto)
    except json.JSONDecodeError as exc:
        raise AIError(f"{what} inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc


def apps_do_plano(plan: Plan, instances: Iterable[Mapping[str, object]] = ()) -> list[str]:
    """`Plan.required_apps` (item 24.1): os apps em que as etapas RODAM, na ordem. É a mesma regra do compilador de
    skills (todo app em que algum nó roda) e de `Repository.save_plan`: a lista de candidatos que foi ao planejador
    não entra — o app que o modelo recebeu e não usou não é exigido.

    Etapa sem app e plano sem app rodam no app do aparelho (`Scheduler._app_context`): aí o app é o dos aparelhos —
    só quando TODOS estão no mesmo. A lista vale para cada aparelho (pré-voo, fluxo aprendido, conjunto do 24.5), e
    numa seleção QA + Notas a etapa sem app roda no QA num e nas Notas no outro: a união exigiria das Notas no
    aparelho do QA, que nunca as abre. Sem app comum, a etapa não declara nenhum; o app de cada aparelho decide.
    Plano sem etapa (pergunta em `missing`) fica com o app do plano, quando há."""
    usados: list[str | None] = [s.app_id or plan.app_id for s in plan.steps] or [plan.app_id]
    if plan.steps and None in usados:
        dos_aparelhos = {str(i.get("app_id") or "") for i in instances}
        if len(dos_aparelhos) == 1:
            usados += list(dos_aparelhos)
    return [a for a in dict.fromkeys(usados) if a]


def _norm_saida(nome: str) -> str:
    """Como `norm_key`, sem inventar prefixo: um nome que não vira identificador válido reprova no `PlanStep`."""
    return re.sub(r"[^a-z0-9_]+", "_", nome.strip().lower()).strip("_")[:40]


#: Piso do prazo da etapa livre, que é a etapa conduzida pelo ator de IA (item 29.75). Era 30: o modelo pedia 60 s e
#: a etapa morria por prazo com a IA ainda pensando (7 dias até 04/10: 18 tentativas esgotadas, 12 delas de 60 s, 8
#: com a chamada de IA passando do prazo restante). Uma observação, uma decisão e a verificação já passam de 60 s com o
#: host ocupado. O teto (600) não muda.
PISO_DA_ETAPA_COM_IA_S = 120


def _etapa_livre(key: str, e: _StepOut | _LivreOut | _StepCurtoOut | _LivreCurtoOut, *, depends_on: list[str],
                 for_each: str | None, app_id: str | None) -> PlanStep:
    """A etapa escrita pelo modelo, com os limites do backend (prazo, uma tentativa no efeito externo). A mesma no
    plano livre e na parte livre do plano entre apps. No formato curto (LT-4b), o que ele não pede vem daqui: a
    descrição derivada do `value`, nenhuma pré-condição e as tentativas pelo padrão."""
    if isinstance(e, (_StepCurtoOut, _LivreCurtoOut)):
        pos = e.postcondition
        postcondicao = Postcondition(kind=pos.kind, value=pos.value,
                                     description=_descricao_derivada(pos.kind, pos.value),
                                     required_delivery_level=pos.required_delivery_level)
        precondicao, tentativas = None, TENTATIVAS_DO_FORMATO_CURTO
    else:
        postcondicao = Postcondition(**e.postcondition.model_dump())
        precondicao, tentativas = e.precondition, e.max_attempts
    return PlanStep(key=norm_key(key), title=e.title, goal=e.goal, depends_on=[norm_key(d) for d in depends_on],
                    side_effect=e.side_effect, commit_guard=e.commit_guard, precondition=precondicao,
                    postcondition=postcondicao, timeout_s=max(PISO_DA_ETAPA_COM_IA_S, min(e.timeout_s, 600)),
                    max_attempts=1 if e.side_effect else max(1, min(tentativas, 5)),
                    for_each=norm_key(for_each) if for_each else None, app_id=app_id,
                    saidas=list(dict.fromkeys(n for n in map(_norm_saida, e.saidas) if n)),
                    # Item 31.36: opcional só a etapa que não pode deixar marca: sem efeito, sem saída, sem for_each e
                    # sem commit_guard. Fora disso o campo é ignorado (a etapa é a de sempre).
                    opcional=bool(getattr(e, "opcional", False)) and not e.side_effect and not e.commit_guard
                    and not for_each and not e.saidas)


def saidas_sem_leitura(steps: Iterable[PlanStep]) -> list[MissingInfo]:
    """Item 24.3: cada `{{saida:<nome>}}` citado tem de ser lido por uma etapa ANTERIOR (`saidas`). Sem isso o plano
    chegaria ao despacho e pararia lá como defeito (`_dependencias_das_saidas` não liga referência para a frente) —
    aqui vira pergunta, com as etapas zeradas, como a ação que não existe."""
    lidas: set[str] = set()
    faltas: list[MissingInfo] = []
    for s in steps:
        sem = [n for n in referencias(s) if n not in lidas]
        if sem:
            faltas.append(MissingInfo(field="saida", question=(
                f"A etapa '{s.key}' usa {', '.join(sem)}, que nenhuma etapa anterior lê na tela. Qual etapa deve "
                "ler esse valor, ou de onde ele vem?")))
        lidas.update(s.saidas)
    return faltas


def plan_from_json(raw: str, req: PlanRequest, *, provider: str, model: str, max_steps: int,
                   curto: bool = False) -> Plan:
    """Planejamento LIVRE (app sem catálogo). `curto` = o formato do LT-4b (`ai.esquema_do_plano`)."""
    try:
        out = (_PlanCurtoOut if curto else _PlanOut).model_validate(loads_json(raw, "Plano"))
    except ValidationError as exc:
        raise AIError(f"Plano inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc
    app = next((a for a in req.apps if a.id == out.app_id), None)
    conhecidos = {a.id for a in req.apps}
    desconhecidos = sorted({s.app_id for s in out.steps if s.app_id and s.app_id not in conhecidos})

    def app_da_etapa(s: _StepOut | _StepCurtoOut) -> str | None:
        # Só vale guardar quando DIFERE do app do plano: etapa sem app é "a do plano", e isso mantém os planos
        # de um app só idênticos aos de antes (e as receitas com a mesma identidade).
        return s.app_id if s.app_id in conhecidos and s.app_id != (app.id if app else None) else None
    try:
        plan = Plan(
            summary=out.summary, app_id=app.id if app else None, app_package=app.package if app else None,
            parameters={p.name: p.value for p in out.parameters}, success_criteria=out.success_criteria,
            steps=[_etapa_livre(s.key, s, depends_on=s.depends_on, for_each=s.for_each, app_id=app_da_etapa(s))
                   for s in out.steps[:max_steps]],
            missing=out.missing, planner=PlannerInfo(provider=provider, model=model, simulated=False))
    except (ValidationError, ValueError) as exc:
        raise AIError(f"Plano inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc
    for desconhecido in desconhecidos:
        plan.missing.append(MissingInfo(field="app", question=f"O app '{desconhecido}' (de uma das etapas) não está "
                                                              "configurado. Qual aplicativo configurado deve ser usado?"))
    if out.app_id and app is None:
        plan.missing.append(MissingInfo(field="app", question=f"O app '{out.app_id}' não está configurado. "
                                                              "Qual aplicativo configurado deve ser usado?"))
    if faltas := saidas_sem_leitura(plan.steps):
        plan.missing.extend(faltas)
        plan.steps = []
    # R6: o plano livre também declara os apps de que precisa (antes, só a skill compilada declarava).
    plan.required_apps = apps_do_plano(plan, getattr(req, "instances", ()))
    return plan


def _titulo_limpo(titulo: str) -> str:
    """O título da ação sem os marcadores de argumento ("Abrir a conversa com {username}" → "Abrir a conversa com …")."""
    return re.sub(r"\s+", " ", re.sub(r"\{[^}]*\}", "…", titulo)).strip()


def fora_do_catalogo(itens: Iterable[_ForaOut], catalogos: Mapping[str, CapabilityCatalog],
                     nomes: Mapping[str, str]) -> list[ForaDoCatalogo]:
    """Item 31.33: o sinal fechado do modelo com os dados do catálogo (ADR-052). O app e as ações disponíveis vêm do
    backend, nunca do modelo: o que ele diz é só QUAL app e O QUE foi pedido. App que o modelo cita e não está entre
    os do comando cai no único app com catálogo, se houver um só; senão vai pelo nome que veio, sem lista."""
    saida: list[ForaDoCatalogo] = []
    for item in itens:
        pedido_em = item.app_id or ""
        app_id = pedido_em if pedido_em in catalogos else next(iter(catalogos)) if len(catalogos) == 1 else pedido_em
        catalogo = catalogos.get(app_id)
        disponiveis = list(dict.fromkeys(_titulo_limpo(c.title) for c in catalogo.offered)) if catalogo else []
        saida.append(ForaDoCatalogo(app_id=app_id or None, app=nomes.get(app_id) or app_id or "app",
                                    pedido=item.pedido.strip(), disponiveis=disponiveis))
    return saida


#: A frase vai ao painel e ao Telegram: o catálogo do Instagram tem ~30 ações e viraria um parágrafo.
MAX_TITULOS_NA_RECUSA = 8


def texto_fora_do_catalogo(pedido: str, app: str, disponiveis: list[str]) -> str:
    """Item 31.33: a frase da recusa, só com dados (o pedido dito pelo modelo, o nome do app e os títulos do catálogo
    dele). Nada de app ou ação fixos aqui: serve a qualquer app declarado (ADR-052)."""
    pedido = pedido.strip().rstrip(".")
    sujeito = (pedido[:1].upper() + pedido[1:]) if pedido else "Isso"
    if not disponiveis:
        return f"{sujeito} não está disponível no {app}. Faça essa parte você mesmo."
    itens = [d[:1].lower() + d[1:] for d in disponiveis[:MAX_TITULOS_NA_RECUSA]]
    if len(disponiveis) > MAX_TITULOS_NA_RECUSA:
        itens.append(f"mais {len(disponiveis) - MAX_TITULOS_NA_RECUSA}")
    lista = itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]
    return (f"{sujeito} não está disponível no {app}: o catálogo dele só tem {lista}. Faça essa parte você mesmo ou "
            "peça só o que está nessa lista.")


def catalog_plan_from_json(raw: str, req: PlanRequest, *, provider: str, model: str, max_steps: int,
                           curto: bool = False) -> Plan:
    """Planejamento COM catálogo: o modelo escolhe ações e argumentos; o backend monta as etapas. Com
    `req.catalogs` (entre apps, item 24.1), cada etapa é montada pelo catálogo do app DELA. `curto` (LT-4b) só muda
    a etapa LIVRE do plano entre apps: a de catálogo já é curta."""
    if getattr(req, "catalogs", None):
        return _plano_entre_apps(raw, req, provider=provider, model=model, max_steps=max_steps, curto=curto)
    try:
        out = _CapPlanOut.model_validate(loads_json(raw, "Plano"))
    except ValidationError as exc:
        raise AIError(f"Plano inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc
    app = next((a for a in req.apps if a.package == req.catalog.package), None)
    nodes = [CapabilityNode(key=norm_key(s.key), capability=s.capability,
                            depends_on=[norm_key(d) for d in s.depends_on],
                            bindings={b.name: b.value for b in s.bindings},
                            for_each=norm_key(s.for_each) if s.for_each else None)
             for s in out.steps[:max_steps]]
    steps, missing = compose(req.catalog, nodes)
    plan = Plan(summary=out.summary, app_id=app.id if app else None,
                app_package=app.package if app else req.catalog.package,
                parameters={p.name: p.value for p in out.parameters},
                success_criteria=out.success_criteria, steps=[] if missing else steps,
                missing=out.missing + missing,
                planner=PlannerInfo(provider=provider, model=model, simulated=False))
    fora = fora_do_catalogo(out.fora_do_catalogo, {plan.app_id or "": req.catalog},
                            {plan.app_id or "": (app.name or app.id or "") if app else req.catalog.package})
    if fora:
        # A recusa substitui o plano inteiro: um pedaço montado ou uma pergunta não servem a quem pediu o impossível.
        plan.steps, plan.missing, plan.fora_do_catalogo = [], [], fora
    plan.required_apps = apps_do_plano(plan, getattr(req, "instances", ()))
    return plan


def _plano_entre_apps(raw: str, req: PlanRequest, *, provider: str, model: str, max_steps: int,
                      curto: bool = False) -> Plan:
    """Item 24.1 (ADR-058, decisão 1): o plano de um comando que atravessa apps, etapa por etapa pelo app dela.

    Etapa num app com catálogo é AÇÃO dele, montada pelo catálogo desse app (texto, guardas, política e limite são do
    backend, como no planejamento por catálogo); etapa num app sem catálogo é LIVRE, escrita pelo modelo. Etapa livre
    num app com catálogo NÃO existe: passaria por fora da política e dos limites (a porta de política a recusaria no
    efeito, T19), então vira pergunta, como a ação que não existe. Qualquer pergunta zera as etapas: um plano meio
    montado seria pior que nenhum (a mesma regra do `compose`).
    """
    try:
        out = (_MultiPlanCurtoOut if curto else _MultiPlanOut).model_validate(loads_json(raw, "Plano"))
    except ValidationError as exc:
        raise AIError(f"Plano inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc
    conhecidos = {a.id: a for a in req.apps if a.id}
    catalogos: Mapping[str, CapabilityCatalog] = req.catalogs
    etapas = out.steps[:max_steps]
    # Herança de argumento POR APP: `herdar_argumentos` anda na ordem do plano, e a legenda de uma publicação do
    # Instagram não pode virar guarda de uma etapa do Outlook que por acaso declare um argumento de mesmo nome.
    nos: dict[int, CapabilityNode] = {}
    for app_id, catalogo in catalogos.items():
        indices = [i for i, s in enumerate(etapas) if s.app_id == app_id and s.capability]
        crus = [CapabilityNode(key=norm_key(etapas[i].key), capability=etapas[i].capability or "",
                               depends_on=[norm_key(d) for d in etapas[i].depends_on],
                               bindings={b.name: b.value for b in etapas[i].bindings},
                               for_each=norm_key(etapas[i].for_each) if etapas[i].for_each else None,
                               saidas=tuple(dict.fromkeys(n for n in map(_norm_saida, etapas[i].saidas) if n)))
                for i in indices]
        nos.update(zip(indices, herdar_argumentos(catalogo, crus), strict=True))
    faltas: list[MissingInfo] = []
    montadas: list[tuple[str, PlanStep]] = []
    try:
        for i, s in enumerate(etapas):
            chave = norm_key(s.key)
            app = conhecidos.get(s.app_id)
            if app is None or app.id is None:
                faltas.append(MissingInfo(field="app", question=f"O app '{s.app_id}' (etapa '{chave}') não está entre "
                                                                "os apps deste comando. Qual aplicativo configurado "
                                                                "deve ser usado?"))
                continue
            nome = app.name or app.id
            catalogo = catalogos.get(app.id)
            if catalogo is None and load_catalog(app.package) is not None:
                # App com catálogo que não foi oferecido (quem chama filtra `apps`; isto é a segunda trava).
                faltas.append(MissingInfo(field="app", question=f"A etapa '{chave}' usa o {nome}, que não está entre "
                                                                "os apps deste comando. Cite o app no comando."))
                continue
            if catalogo is not None:
                if i not in nos:
                    disponiveis = ", ".join(c.key for c in catalogo.offered)
                    faltas.append(MissingInfo(
                        field="capability",
                        question=(f"A etapa '{chave}' no {nome} precisa ser uma ação do catálogo dele. As disponíveis "
                                  f"são: {disponiveis}. Como devo fazer isso?")))
                    continue
                etapa, falta = montar_etapa(catalogo, nos[i])
                if falta is not None:
                    faltas.append(MissingInfo(field=falta.field, question=f"No {nome}: {falta.question}"))
                if etapa is None:
                    continue
            elif s.livre is None:
                faltas.append(MissingInfo(field="step", question=f"A etapa '{chave}' no {nome} não tem ação de "
                                                                 "catálogo: descreva o objetivo e como comprovar."))
                continue
            else:
                etapa = _etapa_livre(chave, s.livre, depends_on=s.depends_on, for_each=s.for_each, app_id=None)
                # O nome posto em `saidas` da etapa, e não em `livre.saidas`, vale igual: perder a leitura aqui só
                # viraria, logo abaixo, a pergunta "quem lê esse valor?" por um erro de lugar do modelo.
                etapa.saidas = list(dict.fromkeys([*etapa.saidas, *(n for n in map(_norm_saida, s.saidas) if n)]))
            montadas.append((app.id, etapa))
        faltas += saidas_sem_leitura(etapa for _, etapa in montadas)
        usados = list(dict.fromkeys(app_id for app_id, _ in montadas))
        # O app do PLANO é o principal que o modelo disse, se alguma etapa roda nele; senão o da primeira etapa. É o
        # padrão de `_app_context` para etapa sem app, então tem de ser um app em que o plano de fato roda.
        principal_id = (out.app_id if out.app_id in usados else usados[0] if usados
                        else out.app_id if out.app_id in conhecidos else None)
        principal = conhecidos.get(principal_id) if principal_id else None
        for app_id, etapa in montadas:
            # Igual ao do plano vira None, como no plano livre e no compilador: a identidade da receita não muda.
            etapa.app_id = app_id if app_id != principal_id else None
        plan = Plan(summary=out.summary, app_id=principal.id if principal else None,
                    app_package=principal.package if principal else None,
                    parameters={p.name: p.value for p in out.parameters},
                    success_criteria=out.success_criteria,
                    steps=[] if faltas else [etapa for _, etapa in montadas],
                    missing=out.missing + faltas,
                    planner=PlannerInfo(provider=provider, model=model, simulated=False))
        fora = fora_do_catalogo(out.fora_do_catalogo, catalogos,
                                {k: a.name or k for k, a in conhecidos.items()})
        if fora:
            plan.steps, plan.missing, plan.fora_do_catalogo = [], [], fora
    except (ValidationError, ValueError) as exc:
        raise AIError(f"Plano inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc
    plan.required_apps = apps_do_plano(plan, getattr(req, "instances", ()))
    return plan


def verdict_from_json(raw: str) -> Verdict:
    try:
        return Verdict.model_validate(loads_json(raw, "Veredito"))
    except ValidationError as exc:
        raise AIError(f"Veredito inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc


def social_from_json(raw: str, max_length: int) -> SocialDraftDTO:
    try:
        draft = SocialDraftDTO.model_validate(loads_json(raw, "Resposta social"))
    except ValidationError as exc:
        raise AIError(f"Resposta social inválida devolvida pelo modelo: {exc}", kind="invalid_output") from exc
    if len(draft.content) > max_length:
        # Cortar aqui é mais barato e mais previsível do que pedir de novo; o limite é do app, não do modelo.
        draft.content = draft.content[:max_length].rstrip()
    return draft

"""O plano de uma execução de OPERAÇÃO (31.154, adendo v1.95): parâmetros fixos e chaves de etapa normalizadas.

A receita ensinada (ensino do 31.160) é achada pela identidade da etapa — a CHAVE e a pós-condição com os valores
trocados pelos NOMES dos parâmetros (`recipes.para_hash`) — e reproduzida com `objective.parameters`
(`recipes.retemplate`). O planejador nomeia livremente: o mesmo perfil vira `perfil_alvo` num plano e `username` noutro,
e a etapa vira `abrir_perfil` ou `open_profile`. Cada variação é uma identidade nova, e a receita ensinada dá
"parâmetro ausente" (`RecipeDiverged`). Na operação, os dois deixam de ser escolha do planejador:

- `parametros` da operação: o parâmetro do plano com o MESMO valor (sem `@`, sem espaços, sem caixa) é renomeado para o
  nome fixo, em `plan.parameters` e em toda ocorrência `{antigo}` do texto das etapas; depois, o fixo vale. Dois nomes
  para o mesmo valor deixariam a identidade dependente da ordem da troca.
- chaves: a etapa cuja `capability` é uma das que o app declara no bloco `operacao` (`AppDefinition.operation_stages`)
  passa a se chamar `<capability minúscula>_<n>` (`open_profile_1`), com `depends_on` e `for_each` remapeados. Se a
  chave nova já for de outra etapa, nada é renomeado (duas etapas com a mesma chave quebrariam o id da etapa).

Função pura sobre o `Plan`: aplicada duas vezes, dá o mesmo plano.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from ..models import Plan, PlanStep
from ..modules.identity.domain.available_data import ROTULOS_DO_PERFIL, SUFIXO_USUARIO

#: Nomes que a materialização põe POR CIMA dos parâmetros (`{**params, **base}`): um fixo com esses nomes seria engolido.
NOMES_RESERVADOS = frozenset({"instance_id", "run_id", "account_label", "item", "item_index"})


#: A chave de etapa que o `PlanStep` aceita: a normalizada que sair dela não entra (o plano relido quebraria).
_CHAVE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
#: Nomes que o plano não perde numa renomeação: o dado da persona (`{perfil_email}`, `{conta_<app>_usuario}`, que a
#: materialização resolve pela persona) e o que as defesas por nome tratam como segredo (`recipes.SENSITIVE_PARAM`: nunca
#: vira molde nem literal de receita). `perfil_alvo` não é dado da persona: é o nome que o planejador dá ao alvo.
_DADO_DA_PERSONA = re.compile(r"^(" + "|".join(sorted(ROTULOS_DO_PERFIL)) + r"|conta_[a-z0-9_]+?_" + SUFIXO_USUARIO
                              + r"(?:_\d+)?)$")
_SENSIVEL = re.compile(r"pass|senha|pin\b|otp|token|secret|segredo|c[oó]digo|code", re.IGNORECASE)


def _nao_renomeia(nome: str) -> bool:
    return bool(_DADO_DA_PERSONA.match(nome) or _SENSIVEL.search(nome))


def normal(valor: str) -> str:
    return valor.strip().lstrip("@").strip().casefold()


def _trocar_nome(texto: str | None, antigo: str, novo: str) -> str | None:
    """`{antigo}` → `{novo}`, sem tocar em `{{saida:…}}` nem em `{antigo_mais_longo}`."""
    if not texto:
        return texto
    return re.sub(r"(?<!\{)\{" + re.escape(antigo) + r"\}(?!\})", "{" + novo + "}", texto)


def _renomear_na_etapa(s: PlanStep, antigo: str, novo: str) -> PlanStep:
    def t(x: str | None) -> str | None:
        return _trocar_nome(x, antigo, novo)

    post = s.postcondition.model_copy(update={"value": t(s.postcondition.value) or "",
                                              "description": t(s.postcondition.description)})
    return s.model_copy(update={
        "title": t(s.title) or "", "goal": t(s.goal) or "", "precondition": t(s.precondition), "postcondition": post,
        "commit_guard": [t(g) or "" for g in s.commit_guard], "band_guard": [t(g) or "" for g in s.band_guard],
        "bindings": {k: t(v) or "" for k, v in s.bindings.items()}})


def fixar_parametros(plan: Plan, fixos: Mapping[str, str]) -> Plan:
    params = dict(plan.parameters)
    steps = list(plan.steps)
    for nome, valor in fixos.items():
        alvo = normal(valor)
        for antigo in [k for k, v in params.items()
                       if k != nome and k not in fixos and not _nao_renomeia(k) and normal(str(v)) == alvo]:
            params.pop(antigo)
            steps = [_renomear_na_etapa(s, antigo, nome) for s in steps]
        params[nome] = valor
    return plan.model_copy(update={"parameters": params, "steps": steps})


def normalizar_chaves(plan: Plan, capabilities: Iterable[str]) -> tuple[Plan, str | None]:
    """(plano, motivo de não normalizar). O motivo vem só quando havia o que normalizar e uma colisão (ou uma chave fora do formato) impediu."""
    caps = {c.upper() for c in capabilities}
    contagem: dict[str, int] = {}
    mapa: dict[str, str] = {}
    for s in plan.steps:
        cap = (s.capability or "").upper()
        if cap not in caps or s.template_key:
            continue
        contagem[cap] = contagem.get(cap, 0) + 1
        mapa[s.key] = f"{cap.lower()}_{contagem[cap]}"
    mapa = {k: v for k, v in mapa.items() if k != v}
    if not mapa:
        return plan, None
    if invalida := sorted(v for v in mapa.values() if not _CHAVE.match(v)):
        return plan, f"chave {invalida[0]} fora do formato das chaves; as chaves do plano ficaram as do planejador"
    ficam = {s.key for s in plan.steps if s.key not in mapa}
    if colisao := sorted(set(mapa.values()) & ficam):
        return plan, f"chave {colisao[0]} já é de outra etapa; as chaves do plano ficaram as do planejador"

    def k(x: str | None) -> str | None:
        return mapa.get(x, x) if x is not None else None

    steps = [s.model_copy(update={"key": k(s.key) or s.key, "depends_on": [k(d) or d for d in s.depends_on],
                                  "for_each": k(s.for_each)}) for s in plan.steps]
    return plan.model_copy(update={"steps": steps}), None


def ajustar(plan: Plan, fixos: Mapping[str, str], capabilities: Iterable[str]) -> tuple[Plan, str | None]:
    return normalizar_chaves(fixar_parametros(plan, fixos), capabilities)

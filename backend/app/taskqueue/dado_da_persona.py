"""Pré-voo do dado da persona (item 31.87, F1): o plano que cita `{perfil_*}` ou `{conta_<app>_usuario}` só chega à
materialização quando a persona de CADA aparelho-alvo tem o dado.

O defeito: `resolve_templates` deixa a variável que não conhece como está. Persona sem sobrenome ⇒ `{perfil_sobrenome}`
escrito por extenso no objetivo da etapa; a receita diverge, a IA assume com o texto cru e a execução digita lixo ou
gasta decisão à toa. Aqui o plano é varrido ANTES de qualquer etapa existir, e a falta vira pergunta à pessoa.

Lógica pura (sem banco, sem relógio): `RunService._plan` chama `faltas_por_aparelho`, e `Repository.materialize` chama
`exigir_resolvido` como defesa em profundidade. Nada aqui carrega VALOR de dado da persona: só nomes de variável,
rótulos de campo e id de aparelho.
"""
from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence

from ..models import Plan, PlanStep
from ..modules.identity.domain.available_data import PROFILE_FIELDS, SUFIXO_USUARIO

#: Só os campos que a persona de fato expõe (lista FECHADA em `PROFILE_FIELDS`) e o usuário da conta. Não é
#: `perfil_\w+`: um parâmetro de fluxo como `{perfil_alvo}` é do comando, não da persona.
_PERFIL = {nome: rotulo for nome, _coluna, rotulo, _tipo in PROFILE_FIELDS}
_CITACAO = re.compile(r"\{(" + "|".join(sorted(_PERFIL)) + r"|conta_[a-z0-9_]+?_" + SUFIXO_USUARIO + r"(?:_\d+)?)\}")


class DadoDaPersonaAusente(ValueError):
    """A materialização recusou: sobrou variável da persona sem valor. `faltam` são NOMES de variável, nunca valores."""

    def __init__(self, faltam: Sequence[str]) -> None:
        self.faltam = tuple(faltam)
        super().__init__("o plano usa dado da persona que o aparelho não tem: " + ", ".join(f"{{{n}}}" for n in faltam)
                         + "; nada foi materializado")


def _textos_do_passo(passo: PlanStep) -> Iterator[str]:
    """Todo texto da etapa que vai ao aparelho (ou à conferência dele), os mesmos que `_insert_steps` resolve."""
    yield passo.title
    yield passo.goal
    if passo.precondition:
        yield passo.precondition
    yield passo.postcondition.value
    yield passo.postcondition.description
    yield from passo.commit_guard
    yield from passo.band_guard
    yield from passo.bindings.values()          # os argumentos da ação/receita (alvo, conteúdo)


def _textos(parametros: Mapping[str, str], passos: Sequence[PlanStep]) -> Iterator[str]:
    # Limite conhecido (N1): `PlanStep.variables` (item/item_index do `for_each`) não é varrido; são valores lidos da tela.
    yield from parametros.values()
    for passo in passos:
        yield from _textos_do_passo(passo)


def nomes_citados(plano: Plan) -> list[str]:
    """Os `{perfil_*}` e `{conta_*_usuario}` que o plano cita, sem repetir, na ordem em que aparecem."""
    return _nomes(plano.parameters, plano.steps)


def _nomes(parametros: Mapping[str, str], passos: Sequence[PlanStep]) -> list[str]:
    achados: dict[str, None] = {}
    for texto in _textos(parametros, passos):
        for m in _CITACAO.finditer(texto):
            achados.setdefault(m.group(1), None)
    return list(achados)


def _resolvidos_de(variaveis: Mapping[str, str] | None, parametros: Mapping[str, str]) -> set[str]:
    return ({k for k, v in (variaveis or {}).items() if str(v).strip()}
            | {k for k, v in parametros.items() if str(v).strip()})


def _resolvidos(variaveis: Mapping[str, str] | None, plano: Plan) -> set[str]:
    """O que `materialize` resolve: as variáveis NÃO vazias da persona e os parâmetros do plano que se chamam assim
    (`{**params, **base}` em `_insert_steps`)."""
    return _resolvidos_de(variaveis, plano.parameters)


def faltas_por_aparelho(plano: Plan, instancias: Sequence[Mapping[str, object]]) -> dict[str, list[str]]:
    """`{aparelho: [nomes que a persona dele não resolve]}`, só dos aparelhos com falta. Aparelho sem persona vinculada
    não tem variável nenhuma: todo `{perfil_*}` do plano falta nele."""
    citados = nomes_citados(plano)
    if not citados:
        return {}
    faltas: dict[str, list[str]] = {}
    for inst in instancias:
        variaveis = inst.get("variables")
        resolvidos = _resolvidos(variaveis if isinstance(variaveis, Mapping) else None, plano)
        faltam = [n for n in citados if n not in resolvidos]
        if faltam:
            faltas[str(inst["instance_id"])] = faltam
    return faltas


def faltas_dos_passos(passos: Sequence[PlanStep], parametros: Mapping[str, str],
                      variaveis: Mapping[str, str] | None) -> list[str]:
    """O mesmo pré-voo para etapas soltas (o replano de `Repository.revise_plan`): os nomes que `variaveis` e os
    parâmetros do objetivo não resolvem."""
    resolvidos = _resolvidos_de(variaveis, parametros)
    return [n for n in _nomes(parametros, passos) if n not in resolvidos]


def exigir_resolvido(plano: Plan, variaveis: Mapping[str, str] | None) -> None:
    """Defesa em profundidade da materialização: levanta `DadoDaPersonaAusente` quando o plano cita uma variável da
    persona que este conjunto de variáveis não resolve. Nunca valor vazio, nunca o texto de exemplo."""
    faltam = [n for n in nomes_citados(plano) if n not in _resolvidos(variaveis, plano)]
    if faltam:
        raise DadoDaPersonaAusente(faltam)


def rotulo(nome: str) -> str:
    """O nome do dado em linguagem de gente: `perfil_sobrenome` → "sobrenome"; `conta_instagram_usuario` → "usuário da
    conta instagram"."""
    if nome in _PERFIL:
        return _PERFIL[nome]
    achou = re.fullmatch(rf"conta_(.+?)_{SUFIXO_USUARIO}(?:_\d+)?", nome)
    return "usuário da conta " + (achou.group(1) if achou else nome).replace("_", " ")


def _lista(rotulos: Sequence[str]) -> str:
    if len(rotulos) <= 1:
        return "".join(rotulos)
    return ", ".join(rotulos[:-1]) + " e " + rotulos[-1]


def perguntas(faltas: Mapping[str, Sequence[str]], sem_persona: frozenset[str] = frozenset()) -> list[dict[str, object]]:
    """Uma pergunta por aparelho, no formato das da RESOLVE. Só id do aparelho e rótulo do campo: nenhum valor, nome
    de persona, e-mail ou handle."""
    saida: list[dict[str, object]] = []
    for iid, nomes in faltas.items():
        rotulos = [rotulo(n) for n in nomes]
        if iid in sem_persona:
            # Falta o destino (quem faz): o painel mostra a orientação de destino, que aqui é a certa.
            texto = (f"O {iid} não tem persona vinculada, e este pedido usa {_lista(rotulos)} dela: vincule uma persona "
                     "ao aparelho e peça de novo.")
            campo = "profile_id"
        else:
            # Falta o DADO: não é pergunta de destino. A saída é cadastrar o dado e criar outra execução (esta só sai para
            # `cancelled`); "dizer o valor no comando" não vale com fluxo reaproveitado, que não mapeia o valor ao parâmetro.
            cadastrado = "cadastrado" if len(rotulos) == 1 else "cadastrados"
            texto = (f"A persona do {iid} não tem {_lista(rotulos)} {cadastrado}: cadastre o dado na persona e peça "
                     "de novo.")
            campo = "persona_data"
        saida.append({"code": "dado_da_persona_ausente", "question": texto, "field": campo, "options": [],
                      "instance_id": iid, "profile_id": None})
    return saida

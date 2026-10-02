"""Privacidade da porta `DecisaoFechada`: falha FECHADA, antes de montar qualquer corpo (item 31.4, ADR-069).

Duas constantes de CÓDIGO, ao lado das do ADR-063 (`context_retrieval/domain/policy.py`: `PRIVATE_CODE_SEND_APPROVED` e
`SYNTHETIC_REMOTE_SEND_APPROVED`, que continuam False). O YAML só restringe, nunca libera: nenhuma configuração lida aqui
abre o que o código fechou, e cada classe liberada tem linha no ADR-069 com a data da decisão do dono.

`JEV_RUNTIME_SEND_APPROVED` continua False: ele só vira True no item 31.10, depois que o dono trocar a chave TypeSafe
(ADR-069 item 7). Enquanto for False, `validar` recusa TODO pedido e o decisor nunca é chamado.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final

from ...security.redaction import redact
from .contrato import CLASSES, MARCADORES, MODOS, ORIGENS, PedidoDeDecisao

#: O envio do Jev em runtime está aprovado? Falso até o 31.10 (chave trocada pelo dono). Constante de código.
JEV_RUNTIME_SEND_APPROVED: bool = False

#: Teto de código das classes que podem sair (ADR-069 item 4, dono, 02/10/2026): C0 e C1 em F1 para todos os consumidores,
#: C2 em F2 e C3 em F3. A C3 só vale para a origem `intencao` e só em `shadow` (`CLASSES_POR_ORIGEM_E_MODO`). C4 em diante
#: (persona, UI, conta real) nem existe no vocabulário e é recusada. A liberação acima não abre o envio sozinha:
#: `JEV_RUNTIME_SEND_APPROVED` continua False.
JEV_ALLOWED_CLASSES: frozenset[str] = frozenset({"C0", "C1", "C2", "C3"})

#: Regra por origem e modo, em cima do teto: a C3 só na intenção (31.9) e só na sombra. Qualquer outra combinação com C3,
#: inclusive `on`, é recusada.
C3_ORIGENS: Final[frozenset[str]] = frozenset({"intencao"})
C3_MODOS: Final[frozenset[str]] = frozenset({"shadow"})

#: Campos nomeados que cada origem pode mandar em `estado` (jaggedness: só o que importa). Vazio de propósito: cada consumidor
#: (31.5 em diante) registra os seus no próprio item, num diff que o revisor veja. Campo fora da lista recusa o pedido.
CAMPOS_POR_ORIGEM: dict[str, frozenset[str]] = {o: frozenset() for o in ORIGENS}
#: Curador (31.8): só METADADOS e CONTAGENS do dossiê (C0, F1), a lista fechada de `curador.CAMPOS`. Repetida aqui de
#: propósito: o que sai é decidido neste arquivo, num diff que o revisor veja (o teste confere que as duas batem).
CAMPOS_POR_ORIGEM["curador"] = frozenset({
    "kind", "estado", "origem", "side_effect", "human_origin", "classe_de_risco", "politica",
    "evidencias_total", "evidencias_a_favor", "evidencias_contra", "evidencias_simuladas",
    "falhas", "falhas_ocorrencias", "votos", "intervencoes", "execucoes", "saude"})
#: Intenção (31.9): `comando` (C3, já sem destinos, sem segredo e sem entidades: `entidades.remover_entidades`) e `app` (id do
#: app do comando, quando há). O consumidor é `intencao.py`.
CAMPOS_POR_ORIGEM["intencao"] = frozenset({"comando", "app"})


@dataclass(frozen=True)
class Veredito:
    permitido: bool
    #: Rótulo curto e fechado da recusa (nunca texto do estado).
    motivo: str = ""


def validar(pedido: PedidoDeDecisao, *, classes_yaml: frozenset[str] | None = None) -> Veredito:
    """Recusa o pedido INTEIRO, sem montar corpo, se o envio não está aprovado, a classe/origem/modo não está liberada,
    há marcador de C7 (ou de social/persona, D-J5) ou o estado traz campo fora da lista da origem.

    `classes_yaml` só pode ESTREITAR o teto de código (interseção): uma classe que o YAML liste e o código não libere
    continua recusada."""
    if not JEV_RUNTIME_SEND_APPROVED:
        return Veredito(False, "envio_nao_aprovado")
    if pedido.origem not in ORIGENS:
        return Veredito(False, "origem_desconhecida")  # inclui qualquer origem nova de social/persona (D-J5)
    if pedido.modo not in MODOS or pedido.modo == "off":
        return Veredito(False, "modo_invalido")
    if pedido.classe not in CLASSES:
        return Veredito(False, "classe_desconhecida")
    liberadas = JEV_ALLOWED_CLASSES if classes_yaml is None else JEV_ALLOWED_CLASSES & classes_yaml
    if pedido.classe not in liberadas:
        return Veredito(False, "classe_nao_liberada")
    if pedido.classe == "C3" and (pedido.origem not in C3_ORIGENS or pedido.modo not in C3_MODOS):
        return Veredito(False, "c3_so_intencao_em_shadow")
    if pedido.marcadores:
        if not pedido.marcadores <= frozenset(MARCADORES):
            return Veredito(False, "marcador_desconhecido")  # na dúvida, recusa: marcador novo não passa em silêncio
        return Veredito(False, "social_persona" if "social_persona" in pedido.marcadores else "c7")
    if not pedido.perguntas or not pedido.estado:
        return Veredito(False, "pedido_vazio")
    if not set(pedido.estado) <= CAMPOS_POR_ORIGEM.get(pedido.origem, frozenset()):
        return Veredito(False, "campo_fora_da_lista")
    return Veredito(True)


def _red(texto: str) -> str:
    return redact(texto) or ""


def redigir(pedido: PedidoDeDecisao) -> PedidoDeDecisao:
    """Cópia do pedido com TODA string que iria no corpo passada por `security.redaction.redact`: valores do estado, instruções
    e descrições das opções. Isso cobre segredo, NÃO nome de terceiro, legenda nem crença: por isso a classe é limitada."""
    estado: Mapping[str, str] = {k: _red(v) for k, v in pedido.estado.items()}
    perguntas = tuple(replace(p, instrucoes=_red(p.instrucoes), opcoes={i: _red(d) for i, d in p.opcoes.items()})
                      for p in pedido.perguntas)
    return replace(pedido, estado=estado, perguntas=perguntas)


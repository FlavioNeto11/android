"""Listas e etiquetas do quadro Execução no modelo de foco (10/10/2026, decisão do dono, opção A da orquestradora).

O quadro tem quatro listas de trabalho curadas e uma de pausa:

- FOCO e DÍVIDA DE PROVA: à mão (trilha crítica da persona). Nenhum script move cartão para dentro nem para fora delas.
- EM CURSO: a lista ÚNICA de trabalho. O estado do plano (Próximas, Em execução, Em validação, Espera você,
  Aguardando, Bloqueado) deixou de ser lista e virou ETIQUETA "Estado · …" no cartão.
- FEITO: concluído e implantado.
- PAUSADO: adiado pelo dono ou sem dono (nenhuma frente responde por ele).

Os ids vivem aqui, num lugar só: `reconciliar`, `espelho_do_deploy`, `achados`, `cartoes_de_aparelho` e
`cartoes_por_alvo` importam daqui em vez de repetir literais.
"""
from __future__ import annotations

QUADRO_EXECUCAO = "6ac13aeda5570365d020f8e2"

LISTA_FOCO = "6aca74679f2e50aab1c5bd22"
LISTA_EM_CURSO = "6aca7468f4fe2466d7ebb2fc"
LISTA_DIVIDA_DE_PROVA = "6aca746a48c6f3640c670c89"
LISTA_FEITO = "6aca746c53ea1707875cfdef"
LISTA_PAUSADO = "6aca7620a1767fe9e8bc1586"

#: estado do plano → id da etiqueta "Estado · …" (as mesmas chaves que `reconciliar` usa como papel)
ETIQUETA_DE_ESTADO = {
    "proximas": "6aca761cea9d3019b4bc1e99",
    "em_execucao": "6aca761dcbf7a65b03ed3b65",
    "em_validacao": "6aca761da13185360f40de72",
    "espera_voce": "6aca761ebb8d5f9672165875",
    "aguardando": "6aca761e5e157b5432feb673",
    "bloqueado": "6aca761f248d543e2f843adb",
}
ESTADO_DA_ETIQUETA = {v: k for k, v in ETIQUETA_DE_ESTADO.items()}
ESTADOS_DE_TRABALHO = tuple(ETIQUETA_DE_ESTADO)
#: estado usado quando o cartão está em EM CURSO sem etiqueta de estado (cartão novo, ainda não classificado)
ESTADO_PADRAO = "proximas"

#: listas que nenhum script mexe (curadas à mão, ou pausa deliberada)
LISTAS_A_MAO = ("foco", "divida_de_prova", "pausado")


def estado_pela_etiqueta(ids_de_etiqueta: list[str] | tuple[str, ...]) -> str | None:
    """Estado do plano que o cartão carrega (a primeira etiqueta de estado achada), ou `None` se não tem."""
    for i in ids_de_etiqueta:
        if i in ESTADO_DA_ETIQUETA:
            return ESTADO_DA_ETIQUETA[i]
    return None


def etiquetas_com_estado(ids_de_etiqueta: list[str] | tuple[str, ...], estado: str | None) -> list[str]:
    """Etiquetas do cartão trocando só as de estado: mantém as de frente e as demais, tira as de estado antigas e põe a
    de `estado` (None = sem etiqueta de estado, como no FEITO). `PUT /cards` com `idLabels` SUBSTITUI todas, por isso o
    resultado leva as outras junto."""
    mantidas = [i for i in ids_de_etiqueta if i not in ESTADO_DA_ETIQUETA]
    return mantidas + ([ETIQUETA_DE_ESTADO[estado]] if estado else [])


def destino(estado: str) -> tuple[str, str | None]:
    """Para onde vai um cartão cujo estado no plano é `estado`: (id da lista, estado a gravar como etiqueta ou None).

    `concluido` vai ao FEITO sem etiqueta de estado; os estados de trabalho vão ao EM CURSO com a etiqueta."""
    if estado == "concluido":
        return LISTA_FEITO, None
    if estado in ETIQUETA_DE_ESTADO:
        return LISTA_EM_CURSO, estado
    raise KeyError(estado)


# ---- posições lógicas: o que os escritores de cartão de aparelho, de alvo e de achado entendem por "lista" -------------
# Antes do modelo de foco cada estado era uma lista; esses scripts raciocinam por "em que lista o cartão deve estar". Em vez
# de reescrever a lógica deles, o que eles chamam de lista passou a ser uma POSIÇÃO lógica; a tradução para (lista real +
# etiqueta de estado) acontece só nas bordas: `posicao_do_cartao` na leitura e `ClienteDePosicoes` na escrita.
POS_PROXIMAS = "posicao:proximas"
POS_EM_EXECUCAO = "posicao:em_execucao"
POS_EM_VALIDACAO = "posicao:em_validacao"
POS_CONCLUIDO = "posicao:concluido"
POSICAO_REAL = {
    POS_PROXIMAS: (LISTA_EM_CURSO, "proximas"),
    POS_EM_EXECUCAO: (LISTA_EM_CURSO, "em_execucao"),
    POS_EM_VALIDACAO: (LISTA_EM_CURSO, "em_validacao"),
    POS_CONCLUIDO: (LISTA_FEITO, None),
}


def posicao_do_cartao(id_lista: str, ids_de_etiqueta: list[str] | tuple[str, ...]) -> str:
    """Posição lógica do cartão. Em EM CURSO o estado vem da etiqueta: Em execução é a sua; qualquer outro estado
    (Próximas, Em validação, Bloqueado, Espera você, Aguardando, ou sem etiqueta) conta como Em validação, o balde de "pede
    atenção". Lista que não é EM CURSO nem FEITO volta como veio, e quem filtra por posição a ignora."""
    if id_lista == LISTA_FEITO:
        return POS_CONCLUIDO
    if id_lista == LISTA_EM_CURSO:
        return POS_EM_EXECUCAO if estado_pela_etiqueta(ids_de_etiqueta) == "em_execucao" else POS_EM_VALIDACAO
    return id_lista


class ClienteDePosicoes:
    """Embrulha o `ClienteTrello`: aceita posição lógica onde o cliente pede lista e grava a lista real com a etiqueta de
    estado, mantendo as etiquetas de frente. Nunca comenta em cartão (só nome, descrição, lista e etiquetas)."""

    def __init__(self, cliente) -> None:  # noqa: ANN001 - ClienteTrello do backend do central
        self._cl = cliente

    async def criar_cartao(self, posicao: str, nome: str, desc: str) -> dict:
        lista, estado = POSICAO_REAL[posicao]
        corpo = {"idList": lista, "name": nome, "desc": desc}
        if estado:
            corpo["idLabels"] = ETIQUETA_DE_ESTADO[estado]
        return await self._cl._pedir("POST", "/1/cards", corpo=corpo)  # noqa: SLF001

    async def atualizar_cartao(self, card: str, *, nome: str | None = None, desc: str | None = None,
                               lista: str | None = None) -> dict:
        if lista is None:
            return await self._cl.atualizar_cartao(card, nome=nome, desc=desc)
        real, estado = POSICAO_REAL[lista]
        atual = await self._cl._pedir("GET", f"/1/cards/{card}", params={"fields": "idLabels"})  # noqa: SLF001
        corpo = {"idList": real,
                 "idLabels": ",".join(etiquetas_com_estado(atual.get("idLabels", []), estado))}
        if nome is not None:
            corpo["name"] = nome
        if desc is not None:
            corpo["desc"] = desc
        return await self._cl._pedir("PUT", f"/1/cards/{card}", corpo=corpo)  # noqa: SLF001

    def __getattr__(self, nome: str):  # noqa: ANN204 - o resto (arquivar, anexar, …) é do cliente de verdade
        return getattr(self._cl, nome)

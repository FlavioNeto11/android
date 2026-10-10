"""31.310 (ADR-087, adendo v1.137): os códigos fechados do cadastro guiado, sem I/O.

O cadastro guiado devolve a conta a uma pessoa quando chega numa tela que só ela resolve. O motivo é SEMPRE um destes
códigos, nunca texto: ele vai ao `detail` da conta em `falha`, ao `proximo_passo` do DTO e ao evento. Texto livre
(do app, da tela) não passa por aqui.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Literal, cast, get_args


class Parada(StrEnum):
    """Por que o comando parou e chamou uma pessoa."""

    CAPTCHA = "captcha"
    DESAFIO = "desafio"                                  # "confirme que você é humano" (ADR-055): nada toca nela
    TELEFONE = "telefone"
    USUARIO_INDISPONIVEL = "usuario_indisponivel"        # o provedor recusou o @ desejado
    TELA_DESCONHECIDA = "tela_desconhecida"              # inclui o formulário recusado sem motivo declarado
    CODIGO_NAO_CHEGOU = "codigo_nao_chegou"              # não chegou no prazo, ou foi recusado pelo app
    CONTA_NAO_LIDA = "conta_nao_lida"                    # o cadastro terminou, mas a conta não foi lida igual ao desejado
    APP_FORA_DO_AR = "app_fora_do_ar"                    # outro app na frente, ou o app caiu
    FALHA_INTERNA = "falha_interna"                      # erro nosso (cofre, caixa de e-mail...): nunca com a mensagem do erro


class Passo(StrEnum):
    """Em que passo do cadastro o evento aconteceu (campo `passo` do evento; nunca o conteúdo da tela)."""

    INICIO = "inicio"
    FORMULARIO = "formulario"
    ENVIO = "envio"
    CODIGO = "codigo"
    CONFIRMACAO = "confirmacao"


#: As paradas que um `cadastro.yaml` pode declarar numa tela: as que se reconhecem pelo texto. As outras o motor decide.
PARADAS_DECLARAVEIS = frozenset({Parada.CAPTCHA, Parada.DESAFIO, Parada.TELEFONE, Parada.USUARIO_INDISPONIVEL})

PREFIXO_DA_PARADA = "aguardando_pessoa:"

ProximoPasso = Literal[
    "aguardando_pessoa:captcha", "aguardando_pessoa:desafio", "aguardando_pessoa:telefone",
    "aguardando_pessoa:usuario_indisponivel", "aguardando_pessoa:tela_desconhecida",
    "aguardando_pessoa:codigo_nao_chegou", "aguardando_pessoa:conta_nao_lida", "aguardando_pessoa:app_fora_do_ar",
    "aguardando_pessoa:falha_interna"]
_PROXIMOS_PASSOS: frozenset[str] = frozenset(get_args(ProximoPasso))


def detalhe_da_parada(parada: Parada) -> str:
    """O `detail` que a conta guarda em `falha`."""
    return f"{PREFIXO_DA_PARADA}{parada.value}"


def proximo_passo(estado: str, detalhe: str | None) -> ProximoPasso | None:
    """`proximo_passo` do DTO: derivado de `falha` + `detail`, só quando o detalhe é um dos códigos fechados."""
    if estado != "falha" or detalhe is None or detalhe not in _PROXIMOS_PASSOS:
        return None
    return cast(ProximoPasso, detalhe)

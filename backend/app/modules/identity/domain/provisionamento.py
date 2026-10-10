"""Provisionamento de uma conta externa (31.281, ADR-087, adendo v1.132): a máquina de estados, sem I/O.

A conta de uma persona num app pode existir só como PLANO: o endereço que se quer, a credencial já preparada no cofre e
o cadastro no provedor ainda por fazer. Este módulo decide, por função pura, qual transição um evento permite. Quem lê e
grava o banco, mexe no cofre e emite evento é o serviço (`social/provisionamento.py`); aqui não há segredo nem relógio.

Regras (todas vêm do ADR-087):

- O evento leva o `estado_esperado` (comparar e trocar): outra aba ou outra sessão que mexeu antes dá `EstadoInesperado`
  em vez de sobrescrever em silêncio.
- Repetir o evento quando a conta já está no estado de destino dele é idempotente: devolve `mudou=False`.
- `falhar` guarda de onde veio (`resume_state`) e `retomar` volta para lá, sem reiniciar nada.
- `cancelar` só antes de `confirmada`; depois disso a conta é real e sai pela rota de retirada.
- `confirmar` só com evidência: essa regra é do serviço (precisa ler a sessão observada), mas o estado de partida é daqui.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Estado(StrEnum):
    PLANEJADA = "planejada"
    CREDENCIAL_PREPARADA = "credencial_preparada"
    AGUARDANDO_CADASTRO_EXTERNO = "aguardando_cadastro_externo"
    AGUARDANDO_VERIFICACAO = "aguardando_verificacao"
    CONFIRMADA = "confirmada"
    FALHA = "falha"


class Evento(StrEnum):
    INICIAR_CADASTRO = "iniciar_cadastro"
    ENVIADO = "enviado"
    CONFIRMAR = "confirmar"
    FALHAR = "falhar"
    RETOMAR = "retomar"
    CANCELAR = "cancelar"


#: Os estados em que a conta ainda é só plano (nem `confirmada` nem `falha`): é de onde `falhar` e `cancelar` partem.
ATIVOS: tuple[Estado, ...] = (Estado.PLANEJADA, Estado.CREDENCIAL_PREPARADA, Estado.AGUARDANDO_CADASTRO_EXTERNO,
                              Estado.AGUARDANDO_VERIFICACAO)

#: Estados em que a senha ainda pode ser preparada ou trocada (antes de o cadastro no provedor começar).
ACEITAM_CREDENCIAL: tuple[Estado, ...] = (Estado.PLANEJADA, Estado.CREDENCIAL_PREPARADA)

_DE_PARA: dict[Evento, tuple[tuple[Estado, ...], Estado]] = {
    Evento.INICIAR_CADASTRO: ((Estado.CREDENCIAL_PREPARADA,), Estado.AGUARDANDO_CADASTRO_EXTERNO),
    Evento.ENVIADO: ((Estado.AGUARDANDO_CADASTRO_EXTERNO,), Estado.AGUARDANDO_VERIFICACAO),
    Evento.CONFIRMAR: ((Estado.AGUARDANDO_VERIFICACAO,), Estado.CONFIRMADA),
    Evento.FALHAR: (ATIVOS, Estado.FALHA),
}


class ErroDeProvisionamento(Exception):
    codigo = "provisionamento"


class EstadoInesperado(ErroDeProvisionamento):
    """O estado gravado não é o que o chamador esperava (e também não é o destino do evento)."""

    codigo = "estado_inesperado"

    def __init__(self, atual: Estado):
        super().__init__(f"A conta está em '{atual.value}'.")
        self.atual = atual


class TransicaoInvalida(ErroDeProvisionamento):
    codigo = "transicao_invalida"

    def __init__(self, evento: Evento, atual: Estado):
        super().__init__(f"O evento '{evento.value}' não vale com a conta em '{atual.value}'.")
        self.evento, self.atual = evento, atual


@dataclass(frozen=True)
class Resultado:
    """O que o serviço grava: o estado novo, o `resume_state` (só em `falha`), e se a conta some (`cancelar`)."""

    estado: Estado
    mudou: bool
    resume_state: Estado | None = None
    remover: bool = False


def destino(evento: Evento, resume_state: Estado | None = None) -> Estado | None:
    """O estado em que o evento deixa a conta. `cancelar` não deixa nenhum (a conta some); `retomar` volta ao guardado."""
    if evento is Evento.CANCELAR:
        return None
    if evento is Evento.RETOMAR:
        return resume_state
    return _DE_PARA[evento][1]


def aplicar(evento: Evento, *, atual: Estado, esperado: Estado, resume_state: Estado | None = None) -> Resultado:
    """A transição de `evento` com a conta em `atual` (e o chamador esperando `esperado`).

    `EstadoInesperado` quando `esperado != atual` e o evento não é um repeteco; `TransicaoInvalida` quando o estado
    não aceita o evento. Repetir o evento no estado de destino dele devolve `mudou=False`, nos dois casos de `esperado`.
    """
    alvo = destino(evento, resume_state)
    if alvo is not None and alvo is atual and evento is not Evento.RETOMAR:
        return Resultado(atual, mudou=False, resume_state=resume_state if atual is Estado.FALHA else None)
    if evento is Evento.RETOMAR and esperado is Estado.FALHA and atual is not Estado.FALHA and atual is resume_state:
        return Resultado(atual, mudou=False)           # a retomada já foi feita (o repeteco da mesma chamada)
    if esperado is not atual:
        raise EstadoInesperado(atual)
    if evento is Evento.CANCELAR:
        if atual not in ATIVOS and atual is not Estado.FALHA:
            raise TransicaoInvalida(evento, atual)
        return Resultado(atual, mudou=True, remover=True)
    if evento is Evento.RETOMAR:
        if atual is not Estado.FALHA or resume_state is None:
            raise TransicaoInvalida(evento, atual)
        return Resultado(resume_state, mudou=True, resume_state=resume_state)
    partidas, para = _DE_PARA[evento]
    if atual not in partidas:
        raise TransicaoInvalida(evento, atual)
    return Resultado(para, mudou=True, resume_state=atual if para is Estado.FALHA else None)


def eventos_aceitos(atual: Estado) -> list[str]:
    """Os `evento` que a rota de transição aceita AGORA (`provisioning.actions` do DTO)."""
    saida: list[str] = []
    for evento, (partidas, _para) in _DE_PARA.items():
        if atual in partidas:
            saida.append(evento.value)
    if atual is Estado.FALHA:
        saida.append(Evento.RETOMAR.value)
    if atual in ATIVOS or atual is Estado.FALHA:
        saida.append(Evento.CANCELAR.value)
    return saida

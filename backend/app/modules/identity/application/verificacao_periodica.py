"""31.302: a releitura PERIÓDICA e leve da sessão da conta âncora (regras puras, sem banco nem aparelho).

Por que existe. A validade da sessão (`contas.session_max_age_s`) só reverifica ANTES de uma tarefa. Uma persona que
ninguém usa fica `session_ready` para sempre, mesmo que o app já mostre "Confirm you're human": a conta bloqueada só é
vista por observação (ADR-055, ADR-068). Esta volta olha a tela, sem IA e sem digitar (`observe_only`), a cada N horas.

O que decide aqui é só QUEM olhar e QUANDO PULAR. Tocar na conta é do motor de sessão (`ensure_session(observe_only=True)`), e
é ele que, vendo o desafio, bloqueia a persona e põe o aparelho em quarentena (`session_rules.bloquear_por_desafio`): este
módulo não tem um segundo caminho para isso.

Cada pulo tem um motivo de vocabulário fechado, para o evento e o relatório contarem sem texto livre.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

#: Os motivos de pulo (vão ao evento `session.verificacao_periodica` como `motivo`).
NAO_E_ANCORA = "nao_e_ancora"
SEM_PROVEDOR = "sem_provedor_de_sessao"
APARELHO_FORA_DO_AR = "aparelho_fora_do_ar"          # nunca liga aparelho para verificar
CONTROLE_MANUAL = "controle_manual"                  # a pessoa (ou a IA) está com o aparelho: o 409 device_busy
APARELHO_OCUPADO = "aparelho_ocupado"                # trabalho ou comando de ciclo de vida em voo
QUARENTENA = "quarentena"                            # conta travada logada (ADR-055): nada toca
PAUSA_DE_REPARO = "pausa_de_reparo"                  # o dono pausou o aparelho (funil, onda)
HOST_CARREGADO = "host_carregado"                    # CPU do host acima do limiar (funil, suíte)
WORKER_EM_MANUTENCAO = "worker_em_manutencao"
PORTAO = "portao_da_sessao"                          # app ausente, em instalação... (a regra do botão "Verificar")
TENTADA_HA_POUCO = "tentada_ha_pouco"                # a tentativa anterior não leu a tela: espera, não martela
UMA_POR_VEZ = "uma_por_vez"                          # outra verificação desta volta ainda está em curso

MOTIVOS = frozenset({NAO_E_ANCORA, SEM_PROVEDOR, APARELHO_FORA_DO_AR, CONTROLE_MANUAL, APARELHO_OCUPADO, QUARENTENA,
                     PAUSA_DE_REPARO, HOST_CARREGADO, WORKER_EM_MANUTENCAO, PORTAO, TENTADA_HA_POUCO, UMA_POR_VEZ})


@dataclass(frozen=True)
class Alvo:
    """Uma conta numa sessão pronta, vencida para releitura."""

    profile_id: str
    account_id: str
    instance_id: str
    verified_at: str | None = None


def escolher(alvos: Iterable[Alvo], motivo_de_pulo: Callable[[Alvo], str | None]) -> tuple[Alvo | None, list[tuple[Alvo, str]]]:
    """O PRIMEIRO alvo sem motivo de pulo (a ordem é a da fila: o mais antigo sem leitura primeiro) e os pulados antes dele.

    UMA conta por volta: o que vem depois do escolhido espera a próxima volta, sem ser avaliado (o aparelho dele pode
    nem estar no mesmo estado daqui a pouco, e a avaliação lê o host). Nenhum escolhido: todos pulados com seu motivo.
    """
    pulados: list[tuple[Alvo, str]] = []
    for alvo in alvos:
        motivo = motivo_de_pulo(alvo)
        if motivo is None:
            return alvo, pulados
        pulados.append((alvo, motivo))
    return None, pulados

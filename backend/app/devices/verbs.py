"""Que verbos cada aparelho suporta — e, quando não suporta, a frase que explica por quê.

Existe porque o painel oferecia a TODO aparelho os botões do emulador local. Em aparelho de outra máquina isso
produziu quatro defeitos de uma vez: "Parar" era desfeito pelo monitor em ≤36 s, "Hibernar" ignorava o argumento
em silêncio, "Resetar dados" era recusado no fundo depois do 202, e "Criar AVD" materializava um AVD fantasma
sobre um aparelho que estava online.

A regra é a do pedido: quando a operação não é suportada, a plataforma explica a limitação ANTES de agendar.
Um único ponto de verdade, consultado pelo pré-voo da ação única e pelo do lote.

O VOCABULÁRIO (os nomes dos verbos, os prazos, `sem_hibernacao`) é contrato com o agente do worker e mora em
`app/contracts/worker/verbos.py`; daqui ele é reexportado — os mesmos objetos, não cópias. Aqui fica só a política
do central: loja, aparelho externo e as frases de recusa.
"""
from __future__ import annotations

from typing import Any

from ..contracts.worker.verbos import (CICLO_DE_VIDA, EXIGEM_SNAPSHOT, PRAZO_PADRAO_S, PRAZO_POR_VERBO, SO_ADB,
                                       TODOS, VERBOS_QUE_ESPERAM_O_BOOT, prazo_de, sem_hibernacao)

__all__ = ["CICLO_DE_VIDA", "EXIGEM_SNAPSHOT", "EXTERNO_SEM_WORKER", "LOJA_NEGA", "PRAZO_PADRAO_S",
           "PRAZO_POR_VERBO", "SO_ADB", "TODOS", "VERBOS_QUE_ESPERAM_O_BOOT", "motivo_nao_suportado", "prazo_de",
           "sem_hibernacao", "verbos_suportados"]

#: Aparelho externo SEM worker: o projeto não controla o processo dele, então só o que passa por ADB funciona.
#: `start` entra porque ali ele significa "reconectar o ADB e readotar" — operação real e útil.
EXTERNO_SEM_WORKER = SO_ADB | {"start"}

#: A loja é a fonte do aplicativo, nunca destino: não recebe app do parque nem opera app de tarefa.
LOJA_NEGA = frozenset({"install_apk", "open_app"})


def _kind(rt: Any) -> str:
    if getattr(rt, "store", False):
        return "store"
    return "external" if getattr(rt, "external", False) else "emulator"


def verbos_suportados(rt: Any) -> frozenset[str]:
    """O que este aparelho aceita hoje.

    Quando existir um worker declarando capacidades (`rt.worker_verbs`), ele manda: é o worker que sabe se
    consegue ligar, hibernar e resetar aquele aparelho. Sem worker, deduz-se do tipo.
    """
    kind = _kind(rt)
    declarado = getattr(rt, "worker_verbs", None)
    if declarado:
        # O worker traz o CICLO DE VIDA (criar, ligar, desligar, hibernar, resetar); os verbos de ADB continuam
        # saindo do central pelo túnel, porque aquele caminho está provado e evita mandar o catálogo de APK para
        # cada máquina. A união é o que o aparelho realmente aceita.
        #
        # A negativa da LOJA sobrevive à declaração do worker, e isto passou a importar quando o central virou um
        # worker (`LocalWorker`): a loja é um emulador DESTE servidor, então ela ganhou `worker_verbs` e a união
        # devolvia `install_apk`/`open_app` para o único aparelho que nunca os aceita. Capacidade de máquina não
        # revoga o papel do aparelho.
        oferecido = (frozenset(declarado) | SO_ADB) & TODOS
        return oferecido - LOJA_NEGA if kind == "store" else oferecido
    if kind == "store":
        return TODOS - LOJA_NEGA
    if kind == "external":
        return EXTERNO_SEM_WORKER
    return TODOS


#: Por que cada verbo não existe num aparelho externo sem worker. Texto para humano, não código de erro.
_PORQUE_EXTERNO = {
    "create": "o AVD deste aparelho vive na outra máquina; criar um aqui produziria um AVD que nunca será usado",
    "stop": "o painel não desliga aparelho de outra máquina — e o monitor o reconectaria em segundos, "
            "fazendo o 'desligado' virar mentira",
    "hibernate": "hibernar exige salvar snapshot no emulador, e o emulador não é gerido por este servidor",
    "wake": "este aparelho nunca hiberna pelo painel, então não há de que acordá-lo; use Iniciar para reconectar",
    "restart": "reiniciar exige desligar o emulador na outra máquina; use Iniciar para reconectar o ADB",
    "reset": "apagar os dados exige controlar o emulador na outra máquina; nada seria apagado a partir daqui",
}

_PORQUE_LOJA = {
    "install_apk": "este aparelho é a loja (Play Store): ele é a FONTE do aplicativo, nunca o destino",
    "open_app": "este aparelho é a loja (Play Store): ele não opera aplicativo de tarefa",
}


def motivo_nao_suportado(rt: Any, verb: str) -> str | None:
    """Frase que explica a limitação, ou `None` quando o verbo é suportado.

    Devolve texto porque é ele que chega a quem clicou. Um código de erro sem frase deixaria o operador com um
    botão que não funciona e nenhuma explicação — foi exatamente o que aconteceu até aqui.
    """
    if verb not in TODOS:
        return "ação desconhecida"
    if verb in verbos_suportados(rt):
        return None
    onde = getattr(rt, "serial", None)
    # Com worker conectado, quem decide é a declaração dele. Hibernar/acordar ausentes ali quase sempre é
    # `android.hibernation: false` no worker.yaml (o padrão) — e dizer "não é gerido por este servidor" mandava
    # o operador procurar defeito de roteamento que não existe (android-09, 25/09/2026).
    if verb in ("hibernate", "wake") and getattr(rt, "worker_verbs", None):
        quem = getattr(rt, "worker_id", None) or "o worker"
        return (f"{quem} não declarou hibernação: ligue `android.hibernation` no config dele (snapshot ocupa "
                "~1,5 GB de disco por aparelho) e reinicie o agente")
    if _kind(rt) == "store":
        return _PORQUE_LOJA.get(verb, "este aparelho é a loja (Play Store) e não aceita esta operação")
    if _kind(rt) == "external":
        porque = _PORQUE_EXTERNO.get(verb)
        if porque:
            return f"{porque} (aparelho externo {onde})" if onde else porque
        return f"aparelho externo {onde} não aceita '{verb}'" if onde else f"aparelho externo não aceita '{verb}'"
    return f"este aparelho não aceita '{verb}'"

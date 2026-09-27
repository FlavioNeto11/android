"""O vocabulário de verbos que o central e o agente do worker compartilham.

`Hello.verbs` e `Dispatch.verb` levam estes nomes pelo fio; o agente filtra o que declara com `sem_hibernacao`, e
o central filtra de novo o que recebe. Mora no contrato porque os dois lados precisam do MESMO conjunto: antes o
agente importava `devices/verbs.py`, que mistura o vocabulário com a política do painel (loja, aparelho externo,
as frases de recusa) — política que é só do central.

A política continua em `app/devices/verbs.py`, que reexporta daqui os mesmos objetos.
"""
from __future__ import annotations

#: Tudo que `POST /instances/{id}/actions/{action}` aceita. Espelha `api.LIFECYCLE_ACTIONS`.
TODOS = frozenset({"create", "start", "stop", "hibernate", "wake", "restart", "reset", "install_apk", "open_app",
                   "home", "back", "recents"})

#: Só ADB, sem ciclo de vida: vale em qualquer aparelho que o ADB alcance, esteja onde estiver.
SO_ADB = frozenset({"install_apk", "open_app", "home", "back", "recents"})

#: O ciclo de vida — o que um worker (o local ou o da outra máquina) executa. É o complemento de `SO_ADB`
#: dentro de `TODOS`, e é o que o `LocalWorker` declara no registro do central.
CICLO_DE_VIDA = TODOS - SO_ADB

#: Prazo do desfecho de cada verbo de ciclo de vida, em segundos — UM só para os dois caminhos. Mora aqui, e não
#: na API, porque quem precisa dele é quem EXECUTA o verbo: o despacho ao agente da outra máquina e o
#: `LocalWorker` deste servidor. Enquanto a tabela morava só no caminho remoto, o mesmo verbo tinha dois
#: significados de sucesso conforme onde o aparelho morava (#155).
PRAZO_POR_VERBO: dict[str, float] = {"start": 540.0, "wake": 180.0, "restart": 600.0, "reset": 600.0,
                                     "create": 240.0, "hibernate": 400.0}
PRAZO_PADRAO_S = 300.0

#: Verbos que só terminam quando o Android está no ar. `succeeded` aqui significa "o aparelho ligou", e não
#: "o pedido foi aceito" — a diferença que o achado #155 cobrava.
VERBOS_QUE_ESPERAM_O_BOOT = ("start", "wake", "restart", "reset")


def prazo_de(verb: str) -> float:
    """Quanto tempo esperar pelo desfecho daquele verbo. Uma pergunta, uma resposta, os dois caminhos."""
    return PRAZO_POR_VERBO.get(verb, PRAZO_PADRAO_S)


#: Os dois verbos que só existem onde a máquina SALVA snapshot (`android.hibernation`).
EXIGEM_SNAPSHOT = frozenset({"hibernate", "wake"})


def sem_hibernacao(verbs: "list[str] | tuple[str, ...]", hiberna: bool) -> list[str]:
    """Os verbos que um worker pode OFERECER, dada a hibernação da máquina dele.

    Medido no painel: o worker anunciava `hibernate` com `android.hibernation` desligado lá, o botão "Hibernar"
    aparecia, e o clique morria num 409 do pré-voo ("o worker que hospeda este aparelho não salva snapshot").
    A verdade viajava em `Hello.hibernation`, que nunca chegava a `supported_verbs` — o campo que o painel lê.
    Aplicado nos DOIS lados: o agente já declara filtrado, e o central filtra o que recebe, para que um agente
    antigo (que declara tudo) não devolva o botão.
    """
    return list(verbs) if hiberna else [v for v in verbs if v not in EXIGEM_SNAPSHOT]

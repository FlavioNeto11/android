"""Sobreposição: o que despachar e o que pular quando a anterior ainda roda (docs/design/pedidos-laco.md §3; §7.4 do
`pedidos-persistentes.md`).

Ocorrência ABERTA = `despachada` ou `rodando` do mesmo pedido. A política escolhe o que acontece com as `devida`:

| política         | com aberta                                                        | sem aberta                  |
| `pular`          | todas as devidas viram `pulada`                                   | despacha a mais antiga      |
| `guardar_uma`    | a mais antiga FICA `devida` (guardada); as outras viram `pulada`  | despacha a mais antiga e    |
|                  |                                                                   | guarda a seguinte           |
| `permitir_todas` | despacha todas                                                    | despacha todas              |

Depois de despachar a primeira, as demais já enxergam uma aberta: por isso o "sem aberta" das duas primeiras linhas
aplica a mesma regra às que sobram (`pular` pula, `guardar_uma` guarda UMA e pula o resto). A semântica de
`guardar_uma` é a do BufferOne do Temporal: a que chega com uma já guardada é descartada, e o descarte fica registrado.

Teto por autonomia (§7.4), conferido AQUI além de na criação do pedido (28.9): `agir` só `pular`; `preparar` aceita
`pular`/`guardar_uma`; `observar` aceita as três. Pedido incoerente (editado no banco, por exemplo) é tratado como
`pular`, e `Plano.incoerente` avisa o laço para registrar em log.

Pedido pausado pula tudo (`pedido pausado`). A ordem é sempre `previsto_para` crescente, depois `chave`. Uma nova
tentativa da mesma ocorrência (`falhou → devida`, 28.5) não é sobreposição consigo mesma: quem chama não a passa como
aberta.

Puro: stdlib. Recebe ids e textos, devolve ids e motivos.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: Políticas que cada autonomia aceita (§7.4). Fora disto, `pular`.
POLITICAS_POR_AUTONOMIA: dict[str, frozenset[str]] = {
    "agir": frozenset({"pular"}),
    "preparar": frozenset({"pular", "guardar_uma"}),
    "observar": frozenset({"pular", "guardar_uma", "permitir_todas"}),
}


@dataclass(frozen=True)
class Devida:
    id: str
    previsto_para: str       # `formatar_instante`: texto comparável (segundo cheio, `Z`)
    chave: str


@dataclass(frozen=True)
class Plano:
    despachar: tuple[str, ...]                 # ids, na ordem do despacho
    pular: tuple[tuple[str, str], ...]         # (id, motivo)
    politica: str                              # a efetivamente aplicada
    incoerente: bool = False                   # a política do pedido passava do teto da autonomia


def politica_efetiva(politica: str, autonomia: str) -> tuple[str, bool]:
    """(política aplicada, incoerente). Política ou autonomia desconhecida também cai em `pular`."""
    aceitas = POLITICAS_POR_AUTONOMIA.get(autonomia, frozenset({"pular"}))
    if politica in aceitas:
        return politica, False
    return "pular", True


def decidir(politica: str, autonomia: str, abertas: Sequence[str], devidas: Sequence[Devida], *,
            pedido_pausado: bool = False) -> Plano:
    """O despacho e os descartes desta volta, para UM pedido."""
    efetiva, incoerente = politica_efetiva(politica, autonomia)
    ordem = sorted(devidas, key=lambda d: (d.previsto_para, d.chave))
    if pedido_pausado:
        return Plano((), tuple((d.id, "pedido pausado") for d in ordem), efetiva, incoerente)
    if not ordem:
        return Plano((), (), efetiva, incoerente)
    if efetiva == "permitir_todas":
        return Plano(tuple(d.id for d in ordem), (), efetiva, incoerente)
    if efetiva == "pular":
        if abertas:
            return Plano((), tuple((d.id, f"a anterior ainda roda ({abertas[0]})") for d in ordem), efetiva, incoerente)
        primeira = ordem[0]
        return Plano((primeira.id,), tuple((d.id, f"a anterior ainda roda ({primeira.id})") for d in ordem[1:]),
                     efetiva, incoerente)
    # guardar_uma
    if abertas:
        guardada, resto = ordem[0], ordem[1:]
        despachar: tuple[str, ...] = ()
    else:
        despachar = (ordem[0].id,)
        if len(ordem) == 1:
            return Plano(despachar, (), efetiva, incoerente)
        guardada, resto = ordem[1], ordem[2:]
    return Plano(despachar, tuple((d.id, f"já há uma guardada ({guardada.id})") for d in resto), efetiva, incoerente)

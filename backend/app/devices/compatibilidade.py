"""`motivo_incompativel(app, aparelho)` — um ponto de verdade sobre "este aplicativo roda neste aparelho?".

Existe pelo mesmo motivo que `verbs.motivo_nao_suportado`: a regra do pedido é explicar a limitação ANTES de
agendar. O que havia era o contrário — o aparelho aceitava a tarefa, a instalação ia até o fim e o erro aparecia
no meio, como `INSTALL_FAILED_NO_MATCHING_ABIS` ou um app que abre e fecha porque a imagem é AOSP e o fluxo
precisa de Play Services.

Três incompatibilidades, três frases:

* **nível de API** — o pacote exige um Android mais novo do que o do aparelho;
* **ABI** — o pacote só traz biblioteca nativa que este aparelho não executa (nem por tradução);
* **Play Services** — o fluxo precisa de GMS e a imagem do aparelho é AOSP.

`None` em qualquer capacidade significa **não se sabe**, e o que não se sabe nunca vira recusa: seria o mesmo
tipo de afirmação vaga que esta fase existe para eliminar. Quem não declarou nada passa, e a prova continua
sendo a instalação.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..db import loads


@dataclass(slots=True)
class Requisitos:
    """O que o aplicativo EXIGE. Vem do inspetor de APK (min_sdk, ABIs) e da declaração da release (GMS)."""

    min_sdk: int | None = None
    abis: list[str] = field(default_factory=list)
    requires_gms: bool = False
    rotulo: str = "o aplicativo"


@dataclass(slots=True)
class Capacidades:
    """O que o aparelho TEM. Lido dele por ADB (adoção) ou declarado pelo worker que o hospeda."""

    device_kind: str | None = None
    system_image: str | None = None
    api_level: int | None = None
    abis: list[str] = field(default_factory=list)
    play_store: bool | None = None

    def conhecidas(self) -> bool:
        return any((self.api_level is not None, self.abis, self.play_store is not None, self.system_image))


def requisitos_de_release(row: Any, *, rotulo: str | None = None) -> Requisitos:
    """Constrói os requisitos a partir de uma linha de `app_releases`."""
    if row is None:
        return Requisitos(rotulo=rotulo or "o aplicativo")
    abis = row["supported_abis"]
    return Requisitos(min_sdk=row["min_sdk"],
                      abis=list(loads(abis, []) or []) if isinstance(abis, str) else list(abis or []),
                      requires_gms=bool(_col(row, "requires_gms")),
                      rotulo=rotulo or f"{row['package_name']} {row['version_name']}")


def capacidades_de(rt: Any) -> Capacidades:
    """Constrói as capacidades a partir do aparelho em memória."""
    return Capacidades(device_kind=getattr(rt, "device_kind", None),
                       system_image=getattr(rt, "system_image", None),
                       api_level=getattr(rt, "api_level", None),
                       abis=list(getattr(rt, "abis", None) or []),
                       play_store=getattr(rt, "play_store", None))


def motivo_incompativel(req: Requisitos, cap: Capacidades, *, aparelho: str | None = None) -> str | None:
    """A frase que explica por que não roda, ou `None` quando não há impedimento conhecido.

    Devolve TEXTO pelo mesmo motivo de `motivo_nao_suportado`: é ele que chega a quem clicou. Um código de erro
    sem frase deixaria o operador com uma recusa e nenhuma explicação.
    """
    onde = f" ({aparelho})" if aparelho else ""
    if req.min_sdk is not None and cap.api_level is not None and req.min_sdk > cap.api_level:
        return (f"{req.rotulo} exige Android com nível de API {req.min_sdk} e este aparelho tem "
                f"{cap.api_level}{onde}")
    if req.abis and cap.abis and not any(a in cap.abis for a in req.abis):
        return (f"{req.rotulo} traz biblioteca nativa só para {', '.join(req.abis)} e este aparelho executa "
                f"{', '.join(cap.abis)}{onde}")
    if req.requires_gms and cap.play_store is False:
        imagem = f" (imagem {cap.system_image})" if cap.system_image else ""
        return (f"{req.rotulo} precisa do Google Play Services e a imagem deste aparelho é AOSP, sem "
                f"GMS{imagem}{onde}")
    return None


def _col(row: Any, nome: str) -> Any:
    """Coluna que pode não existir na linha (release antiga, consulta parcial). Ausência não é `False`: é nada."""
    try:
        return row[nome]
    except (KeyError, IndexError):
        return None

"""`motivo_incompativel(app, aparelho)` — um ponto de verdade sobre "este aplicativo roda neste aparelho?".

Existe pelo mesmo motivo que `verbs.motivo_nao_suportado`: a regra do pedido é explicar a limitação ANTES de
agendar. O que havia era o contrário — o aparelho aceitava a tarefa, a instalação ia até o fim e o erro aparecia
no meio, como `INSTALL_FAILED_NO_MATCHING_ABIS` ou um app que abre e fecha porque a imagem é AOSP e o fluxo
precisa de Play Services.

Quatro incompatibilidades, quatro frases:

* **nível de API** — o pacote exige um Android mais novo do que o do aparelho;
* **ABI** — o pacote só traz biblioteca nativa que este aparelho não executa (nem por tradução);
* **Play Services** — o fluxo precisa de GMS e a imagem do aparelho é AOSP;
* **renderizador do emulador** (29.11) — o app declara, no `app.yaml`, o renderizador em que ele derruba o
  emulador (`renderizador_recusado`), e o deste aparelho é esse.

`None` em qualquer capacidade significa **não se sabe**, e o que não se sabe nunca vira recusa: seria o mesmo
tipo de afirmação vaga que esta fase existe para eliminar. Quem não declarou nada passa, e a prova continua
sendo a instalação.

A exceção é o renderizador, e é de propósito: ali a "prova pela instalação" é a queda do processo do emulador, com a
sessão de quem estiver logado no aparelho. Para o app que DECLAROU o requisito, renderizador desconhecido é recusa
("renderizador desconhecido"), com o que fazer para ele passar a ser conhecido. App sem a chave não pergunta nada.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..db import loads
from ..modules.applications.infrastructure.registry import capabilities_of
from .emulator import NOME_DO_RENDERIZADOR, RENDERIZADORES

#: O `gpu_mode` que pede cada renderizador, para a frase dizer o que escrever na configuração.
_GPU_MODE = {"swiftshader": "swiftshader_indirect"}
#: Aparelho que não é emulador não tem renderizador de emulador: o requisito não se aplica a ele.
_SEM_RENDERIZADOR = ("physical", "container")


@dataclass(slots=True)
class Requisitos:
    """O que o aplicativo EXIGE. Vem do inspetor de APK (min_sdk, ABIs), da declaração da release (GMS) e do
    `app.yaml` do app (renderizador)."""

    min_sdk: int | None = None
    abis: list[str] = field(default_factory=list)
    requires_gms: bool = False
    rotulo: str = "o aplicativo"
    #: Como chamar o APP (e não a versão) numa frase: o requisito de renderizador é do app, em qualquer versão.
    app: str = ""
    #: Renderizadores do emulador em que o app não roda, pelo nome canônico (`AppDefinition.refused_renderers`).
    renderizador_recusado: tuple[str, ...] = ()


@dataclass(slots=True)
class Capacidades:
    """O que o aparelho TEM. Lido dele por ADB (adoção) ou declarado pelo worker que o hospeda."""

    device_kind: str | None = None
    system_image: str | None = None
    api_level: int | None = None
    abis: list[str] = field(default_factory=list)
    play_store: bool | None = None
    #: O renderizador do emulador, canônico: o SELECIONADO (lido do log do emulador) quando conhecido; senão o
    #: PEDIDO (`gpu_mode`). `renderizador_pedido` é só o pedido — os dois diferem quando houve fallback, e aí a frase
    #: não pode mandar "configurar" o que já está configurado.
    renderizador: str | None = None
    renderizador_pedido: str | None = None

    def conhecidas(self) -> bool:
        return any((self.api_level is not None, self.abis, self.play_store is not None, self.system_image))


def requisitos_do_app(package: str | None, *, rotulo: str | None = None) -> Requisitos:
    """O que o APP declara, sem versão nenhuma em mãos: é a pergunta de quem vai ABRIR o app (a porta do despacho),
    e não instalá-lo. Pacote sem registro devolve requisito nenhum."""
    definicao = capabilities_of(package)
    return Requisitos(rotulo=rotulo or definicao.label, app=definicao.label,
                      renderizador_recusado=definicao.refused_renderers)


def requisitos_de_release(row: Any, *, rotulo: str | None = None) -> Requisitos:
    """Constrói os requisitos a partir de uma linha de `app_releases`."""
    if row is None:
        return Requisitos(rotulo=rotulo or "o aplicativo")
    abis = row["supported_abis"]
    do_app = requisitos_do_app(_col(row, "package_name"))
    return Requisitos(min_sdk=row["min_sdk"],
                      abis=list(loads(abis, []) or []) if isinstance(abis, str) else list(abis or []),
                      requires_gms=bool(_col(row, "requires_gms")),
                      rotulo=rotulo or f"{row['package_name']} {row['version_name']}",
                      app=do_app.app, renderizador_recusado=do_app.renderizador_recusado)


def capacidades_de(rt: Any) -> Capacidades:
    """Constrói as capacidades a partir do aparelho em memória."""
    return Capacidades(device_kind=getattr(rt, "device_kind", None),
                       system_image=getattr(rt, "system_image", None),
                       api_level=getattr(rt, "api_level", None),
                       abis=list(getattr(rt, "abis", None) or []),
                       play_store=getattr(rt, "play_store", None),
                       renderizador=getattr(rt, "renderizador_efetivo", None),
                       renderizador_pedido=getattr(rt, "renderizador_pedido", None))


def motivo_do_renderizador(req: Requisitos, cap: Capacidades, *, aparelho: str | None = None) -> str | None:
    """A recusa por renderizador do emulador, ou `None`. Separada de `motivo_incompativel` porque há quem só tenha
    o APP em mãos (abrir, entregar), sem versão para perguntar por API ou ABI.

    Medido em 30/09/2026: o Outlook derruba o processo do emulador com o SwiftShader e roda com a GPU do host. Quem
    decide é o renderizador em USO; o pedido só vale enquanto o emulador não disse o que selecionou.
    """
    if not req.renderizador_recusado or cap.device_kind in _SEM_RENDERIZADOR:
        return None
    quem = req.app or req.rotulo
    onde = f" ({aparelho})" if aparelho else ""
    aceitos = [r for r in RENDERIZADORES if r not in req.renderizador_recusado]
    configure = f"`gpu_mode: {_GPU_MODE.get(aceitos[0], aceitos[0])}`" if aceitos else "outro `gpu_mode`"
    if cap.renderizador is None:
        recusados = " e ".join(NOME_DO_RENDERIZADOR.get(r, r) for r in req.renderizador_recusado)
        return (f"{quem} derruba o emulador com o renderizador {recusados}, e este aparelho está com o renderizador "
                f"desconhecido{onde}: declare {configure} na configuração dele; se ele é de outro servidor, quem "
                "informa é o agente de lá (atualize-o)")
    if cap.renderizador not in req.renderizador_recusado:
        return None
    nome = NOME_DO_RENDERIZADOR.get(cap.renderizador, cap.renderizador)
    if cap.renderizador_pedido is not None and cap.renderizador_pedido not in (cap.renderizador, "auto"):
        return (f"{quem} derruba o emulador com o renderizador {nome}, e foi esse que o emulador deste aparelho "
                f"selecionou{onde}, apesar do `gpu_mode: {cap.renderizador_pedido}` pedido; veja a linha "
                "`emuglConfig_init` no log do emulador")
    return (f"{quem} derruba o emulador com o renderizador {nome}, que é o deste aparelho{onde}; configure "
            f"{configure} neste aparelho e reinicie-o")


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
    return motivo_do_renderizador(req, cap, aparelho=aparelho)


def _col(row: Any, nome: str) -> Any:
    """Coluna que pode não existir na linha (release antiga, consulta parcial). Ausência não é `False`: é nada."""
    try:
        return row[nome]
    except (KeyError, IndexError):
        return None

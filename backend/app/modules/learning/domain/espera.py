"""A espera do dono (item 30.21, `aprendizado-vivo.md` §8.11): quando um item do livro passa a esperar a pessoa, em
que FAIXA da política de risco (§8.4) e por quê. Puro: sem relógio, sem banco, sem barramento.

Só as faixas que esperam alguém existem aqui. A faixa A (o sistema decide pela regra determinística) nunca aguarda a
pessoa, e o item "fora das três" (origem humana, sem efeito) entra como B (D-2, decidido pelo dono em 02/10).

A classificação é a da política de risco (30.10, `domain/politica_de_risco.py`, a fonte única): `classificar_espera`
só a traduz para a faixa do evento (a classe A não espera ninguém). `FatosDoCatalogo` e `MotivoDeEntrada` moram lá e
são reexportados aqui. O que NÃO é derivado ainda: a família de efeito (envio/publicação/exclusão) que o catálogo de
cada app ainda não declara (§8.4) e `sessao_ou_autenticacao` por conteúdo de tela (hoje só o item nascido de
`session_unknown` o carrega).

O payload é um contrato de PRIVACIDADE: `CAMPOS_DO_PAYLOAD` é a lista fechada, e `AvisoDeEspera.como_dados` só sabe
montar estes campos. Nunca conteúdo de receita ou fluxo, seletor, texto digitado, parâmetro, texto de persona, nota
ou conclusão de IA: quem quer o detalhe abre o `href` com a autenticação do painel.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.modules.learning.domain.politica_de_risco import (ClasseDeRisco, FatosDeRisco, FatosDoCatalogo,
                                                           MotivoDeEntrada, classificar)


class Faixa(StrEnum):
    """A classe de risco de quem ESPERA a pessoa. Enum próprio (sem A) para o evento não poder dizer faixa A."""

    B = "B"                     # o dono decide, em lote, com a recomendação da IA
    C = "C"                     # o dono decide item a item


class MotivoDeSaida(StrEnum):
    DECIDIDO_POR_PESSOA = "decidido_por_pessoa"
    REBAIXADO_PELO_SISTEMA = "rebaixado_pelo_sistema"
    SUBSTITUIDO = "substituido"


#: A lista fechada do que o evento carrega. Um campo fora dela é um vazamento por construção.
CAMPOS_DO_PAYLOAD = frozenset({"kind", "ref", "app", "faixa", "aguardando", "motivo", "href", "desde"})


def classificar_espera(*, side_effect: bool, human_origin: bool, tem_catalogo: bool,
                       catalogo: FatosDoCatalogo | None = None,
                       sessao_ou_autenticacao: bool = False) -> tuple[Faixa, MotivoDeEntrada] | None:
    """A faixa e o motivo de um item que ESPERA a pessoa; `None` se a política o põe na classe A. A regra (a mais
    restritiva, §8.4) é a de `politica_de_risco.classificar`; aqui só a tradução para o evento."""
    c = classificar(FatosDeRisco(side_effect=side_effect, human_origin=human_origin, tem_catalogo=tem_catalogo,
                                 catalogo=catalogo, sessao_ou_autenticacao=sessao_ou_autenticacao))
    if c.classe is ClasseDeRisco.A or c.motivo is None:
        return None
    return Faixa(c.classe.value), c.motivo


def motivo_de_saida(*, por_sistema: bool, para_aposentado: bool) -> MotivoDeSaida:
    """Por que o item deixou a espera. O sistema aposentar (`deprecated`) é a substituição pela versão nova; qualquer
    outra saída feita por ele é um rebaixamento; a pessoa decide."""
    if not por_sistema:
        return MotivoDeSaida.DECIDIDO_POR_PESSOA
    return MotivoDeSaida.SUBSTITUIDO if para_aposentado else MotivoDeSaida.REBAIXADO_PELO_SISTEMA


def href_do_item(kind: str, ref: str) -> str:
    # Com a aba: o link sai no aviso externo (Telegram) e tem de abrir o item mesmo num painel que ainda não trate
    # `item` sozinho (o painel novo também aceita sem a aba).
    return f"#/aprendizado?aba=aprendido&item={kind}:{ref}"


@dataclass(frozen=True, slots=True)
class AvisoDeEspera:
    kind: str
    ref: str
    app: str
    faixa: Faixa
    aguardando: bool
    motivo: str                             # `MotivoDeEntrada` (aguardando) ou `MotivoDeSaida` (saiu)
    desde: str                              # ISO: quando ENTROU na espera (também no aviso de saída)

    @property
    def href(self) -> str:
        return href_do_item(self.kind, self.ref)

    def como_dados(self) -> dict[str, object]:
        return {"kind": self.kind, "ref": self.ref, "app": self.app, "faixa": self.faixa.value,
                "aguardando": self.aguardando, "motivo": self.motivo, "href": self.href, "desde": self.desde}

    def mensagem(self) -> str:
        """Só identificadores e vocabulário fechado: a mensagem também é persistida e transmitida."""
        quem = f"{self.kind} {self.ref}" + (f" ({self.app})" if self.app else "")
        if self.aguardando:
            return f"Conhecimento aguardando a pessoa, faixa {self.faixa.value}: {quem} [{self.motivo}]"
        return f"Conhecimento saiu da espera da pessoa, faixa {self.faixa.value}: {quem} [{self.motivo}]"

    @property
    def nivel(self) -> str:
        return "warn" if self.aguardando and self.faixa is Faixa.C else "info"


__all__ = ["CAMPOS_DO_PAYLOAD", "AvisoDeEspera", "Faixa", "FatosDoCatalogo", "MotivoDeEntrada", "MotivoDeSaida",
           "classificar_espera", "href_do_item", "motivo_de_saida"]

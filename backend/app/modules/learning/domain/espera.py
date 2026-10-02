"""A espera do dono (item 30.21, `aprendizado-vivo.md` §8.11): quando um item do livro passa a esperar a pessoa, em
que FAIXA da política de risco (§8.4) e por quê. Puro: sem relógio, sem banco, sem barramento.

Só as faixas que esperam alguém existem aqui. A faixa A (o sistema decide pela regra determinística) nunca aguarda a
pessoa, e o item "fora das três" (origem humana, sem efeito) entra como B (D-2, decidido pelo dono em 02/10).

A classificação é a MÍNIMA do que já existe hoje (`side_effect`, `human_origin`, catálogo do app); o item 30.10
(`politica_de_risco.py`) a estende com a política completa e deve reaproveitar `classificar_espera` em vez de a
duplicar. O que NÃO é derivado ainda: o mapa de `interaction_type` para envio/publicação/exclusão (§8.4: dado do
catálogo de cada app, a conferir) e `sessao_ou_autenticacao` por conteúdo de tela (hoje só o item nascido de
`session_unknown` o carrega).

O payload é um contrato de PRIVACIDADE: `CAMPOS_DO_PAYLOAD` é a lista fechada, e `AvisoDeEspera.como_dados` só sabe
montar estes campos. Nunca conteúdo de receita ou fluxo, seletor, texto digitado, parâmetro, texto de persona, nota
ou conclusão de IA: quem quer o detalhe abre o `href` com a autenticação do painel.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Faixa(StrEnum):
    B = "B"                     # o dono decide, em lote, com a recomendação da IA
    C = "C"                     # o dono decide item a item


class MotivoDeEntrada(StrEnum):
    EFEITO_EXTERNO = "efeito_externo"
    TEXTO_DE_PESSOA = "texto_de_pessoa"
    COMMIT_SEM_CATALOGO = "commit_sem_catalogo"
    ALTO_RISCO = "alto_risco"
    SESSAO_OU_AUTENTICACAO = "sessao_ou_autenticacao"
    PARECER_DA_IA = "parecer_da_ia"         # publicado pelo curador (30.11); a porta está pronta, ninguém a chama ainda


class MotivoDeSaida(StrEnum):
    DECIDIDO_POR_PESSOA = "decidido_por_pessoa"
    REBAIXADO_PELO_SISTEMA = "rebaixado_pelo_sistema"
    SUBSTITUIDO = "substituido"


#: A lista fechada do que o evento carrega. Um campo fora dela é um vazamento por construção.
CAMPOS_DO_PAYLOAD = frozenset({"kind", "ref", "app", "faixa", "aguardando", "motivo", "href", "desde"})


@dataclass(frozen=True, slots=True)
class FatosDoCatalogo:
    """O que o catálogo do app diz da capability da etapa (`governance` e `side_effect`); texto de ação nunca entra."""

    risco: str = "low"                      # low | medium | high
    politica: str = "autonomous"            # autonomous | approval_required | manual_only | disabled
    precisa_rascunho: bool = False          # `needs_draft`: a ação envia conteúdo escrito (mensagem, comentário)
    efeito_externo: bool = False


def classificar_espera(*, side_effect: bool, human_origin: bool, tem_catalogo: bool,
                       catalogo: FatosDoCatalogo | None = None,
                       sessao_ou_autenticacao: bool = False) -> tuple[Faixa, MotivoDeEntrada] | None:
    """A faixa e o motivo de um item que ESPERA a pessoa; `None` se nada o põe nas faixas B ou C. Vale a mais
    restritiva (§8.4): alto risco, sessão e autenticação vencem o efeito, que vence o texto de pessoa."""
    if sessao_ou_autenticacao:
        return Faixa.C, MotivoDeEntrada.SESSAO_OU_AUTENTICACAO
    if catalogo is not None and (catalogo.risco == "high" or catalogo.politica == "manual_only"
                                 or catalogo.precisa_rascunho):
        return Faixa.C, MotivoDeEntrada.ALTO_RISCO
    if side_effect or (catalogo is not None and (catalogo.efeito_externo or catalogo.risco == "medium")):
        return Faixa.B, MotivoDeEntrada.EFEITO_EXTERNO if tem_catalogo else MotivoDeEntrada.COMMIT_SEM_CATALOGO
    if human_origin:
        return Faixa.B, MotivoDeEntrada.TEXTO_DE_PESSOA
    return None


def motivo_de_saida(*, por_sistema: bool, para_aposentado: bool) -> MotivoDeSaida:
    """Por que o item deixou a espera. O sistema aposentar (`deprecated`) é a substituição pela versão nova; qualquer
    outra saída feita por ele é um rebaixamento; a pessoa decide."""
    if not por_sistema:
        return MotivoDeSaida.DECIDIDO_POR_PESSOA
    return MotivoDeSaida.SUBSTITUIDO if para_aposentado else MotivoDeSaida.REBAIXADO_PELO_SISTEMA


def href_do_item(kind: str, ref: str) -> str:
    return f"#/aprendizado?item={kind}:{ref}"


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

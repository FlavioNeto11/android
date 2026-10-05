"""O ensinado rebaixado (30.80 parte B, B2 do mapa do ensino): a receita ou o fluxo que a PESSOA demonstrou no modo
treinamento saiu de uso por decisão do SISTEMA (quarentena por falhas seguidas, substituição pelo sistema, a
obsolescência). Antes isso acontecia em silêncio: quem ensinou não sabia que a etapa tinha voltado para a IA. Puro:
sem relógio, sem banco, sem barramento.

Dois tipos de evento, porque a criticidade muda (o mapa dos avisos é da frente Canais, item 28.50):
- `learning.ensinado_rebaixado`: outra receita ou fluxo ativo ainda segura a etapa (rotina, na linha do resumo);
- `learning.ensinado_sem_receita`: nada ativo ficou; a IA volta a conduzir a etapa (precisa de você, na hora).
Cada transição publica UM dos dois, nunca os dois.

Não dispara quando quem tirou o item foi uma pessoa (painel, Livro) nem quando outra demonstração o substituiu (30.79:
o autor é a sessão de treino, não o sistema). O payload é um contrato de PRIVACIDADE, como o do 30.21:
`CAMPOS_DO_PAYLOAD` é a lista fechada. Nunca conteúdo da receita ou do fluxo, seletor, conta ou texto de tela.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.conteudo import PREFIXO_DE_TREINO
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.vocabulario import LivroKind, Origem

TIPO_REBAIXADO = "learning.ensinado_rebaixado"
#: O começo do motivo com que a prova real que falha desliga o fluxo ensinado (30.81). O 30.84 o lê na trilha para
#: deixar a pessoa ensinar o mesmo comando de novo: só esse desligamento, e só se ele ainda for a última linha.
MOTIVO_DA_PROVA_DO_ENSINADO = "a prova do fluxo ensinado falhou"
TIPO_SEM_RECEITA = "learning.ensinado_sem_receita"

#: A lista fechada do `data` do evento (combinada com a Canais em 05/10; nome ou campo novo: avisar a frente antes).
CAMPOS_DO_PAYLOAD = ("kind", "ref", "app", "treino", "sem_receita_ativa", "para", "desde")

def sessao_de_treino(origem: str | None) -> str | None:
    """`training:<id>` → `<id>` (o id inteiro da sessão, `trn-…`); o que não veio do treino → `None`."""
    if not origem or not origem.startswith(PREFIXO_DE_TREINO):
        return None
    return origem[len(PREFIXO_DE_TREINO):].strip() or None


def rebaixado_pelo_sistema(antes: EntradaDoLivro | None, depois: EntradaDoLivro, *, por_sistema: bool) -> bool:
    """O ensinado estava EM USO (`published`) e o sistema o tirou de uso. Nascimento, promoção e o gesto de uma pessoa
    (ou de outra demonstração) não contam."""
    return (por_sistema and antes is not None and depois.kind in (LivroKind.RECEITA, LivroKind.FLUXO)
            and antes.origin is Origem.TREINO and antes.state is SkillState.PUBLISHED
            and depois.state is not SkillState.PUBLISHED)


@dataclass(frozen=True, slots=True)
class AvisoDoEnsinado:
    kind: str
    ref: str
    app: str
    treino: str                         # o id inteiro da sessão de treino (`trn-…`)
    sem_receita_ativa: bool             # nada ativo ficou na chave da receita (ou no `match_key` do fluxo)
    para: str                           # o status NATIVO de destino: `quarantined`, `superseded`, `disabled`
    desde: str                          # ISO: o instante da transição, lido da trilha (estável numa reemissão)

    @property
    def tipo(self) -> str:
        return TIPO_SEM_RECEITA if self.sem_receita_ativa else TIPO_REBAIXADO

    def como_dados(self) -> dict[str, object]:
        return {"kind": self.kind, "ref": self.ref, "app": self.app, "treino": self.treino,
                "sem_receita_ativa": self.sem_receita_ativa, "para": self.para, "desde": self.desde}

    def mensagem(self) -> str:
        """Só identificadores e vocabulário fechado: a mensagem também é persistida, transmitida e vai ao backend.log
        (`EventBus.emit` loga "kind | message" no `warn`). O id do FLUXO fica fora: hoje é o slug do resumo literal, que
        pode trazer nome de pessoa (leitura do 28.50 pela Reload; o id opaco é o 30.83). Ele segue em `data.ref`."""
        quem = self.kind if self.kind == "fluxo" else f"{self.kind} {self.ref}"
        quem += f" ({self.app})" if self.app else ""
        if self.sem_receita_ativa:
            return f"Conhecimento ensinado rebaixado pelo sistema, a etapa voltou para a IA: {quem} [{self.para}]"
        return f"Conhecimento ensinado rebaixado pelo sistema: {quem} [{self.para}]"

    @property
    def nivel(self) -> str:
        return "warn" if self.sem_receita_ativa else "info"


# ------------------------------------------------------------------ o ensinado que espera a pessoa (30.81, classe C)
#: O fluxo ensinado que a prova automática não cobre (a classe C, a recusa ao nascer do pedido, as tentativas esgotadas)
#: só vale para a persona que ensinou até uma pessoa decidir. A Canais transforma o evento em cartão na lista de
#: perguntas do Trello (combinado em 05/10, 15:31Z); o cartão não decide (C-13).
TIPO_ESPERA_DECISAO = "learning.ensinado_espera_decisao"
TIPO_DECIDIDO = "learning.ensinado_decidido"
CAMPOS_DA_ESPERA = ("kind", "ref", "app", "treino", "persona", "desde")
CAMPOS_DA_DECISAO = ("kind", "ref", "desde", "decisao", "decidido_em")
#: "liberado" = "Confirmar que fica" (30.24, estendido ao ensinado que espera); "desligado" = a pessoa desliga no Livro.
DECISOES = ("liberado", "desligado", "outro")


@dataclass(frozen=True, slots=True)
class EsperaDoEnsinado:
    kind: str                           # sempre "fluxo" hoje
    ref: str
    app: str
    treino: str                         # o id inteiro da sessão de treino (`trn-…`)
    persona: str | None                 # a persona que ensinou (`training_sessions.profile_id`), ou nenhuma
    desde: str                          # ISO: o nascimento do fluxo (`flows.created_at`), o mesmo na decisão

    def como_dados(self) -> dict[str, object]:
        return {"kind": self.kind, "ref": self.ref, "app": self.app, "treino": self.treino, "persona": self.persona,
                "desde": self.desde}

    def mensagem(self) -> str:
        """Nem persona nem o identificador do fluxo: a mensagem é persistida e transmitida (combinado com a Canais)."""
        return ("Conhecimento ensinado espera a decisão de uma pessoa: a prova automática não o cobre"
                + (f" ({self.app})" if self.app else ""))


@dataclass(frozen=True, slots=True)
class DecisaoDoEnsinado:
    kind: str
    ref: str
    desde: str                          # o mesmo `desde` da espera
    decisao: str                        # um de `DECISOES`
    decidido_em: str                    # ISO: o `decided_at` da linha da pessoa na trilha

    def como_dados(self) -> dict[str, object]:
        return {"kind": self.kind, "ref": self.ref, "desde": self.desde, "decisao": self.decisao,
                "decidido_em": self.decidido_em}

    def mensagem(self) -> str:
        return f"Conhecimento ensinado decidido por uma pessoa: {self.decisao}"


def decisao_da_pessoa(para: SkillState | None, *, confirmou: bool) -> str:
    """O vocabulário fechado da decisão: confirmar que fica libera; desligar desliga; o resto é `outro`."""
    if confirmou:
        return "liberado"
    return "desligado" if para is SkillState.DISABLED else "outro"


__all__ = ["CAMPOS_DA_DECISAO", "CAMPOS_DA_ESPERA", "CAMPOS_DO_PAYLOAD", "DECISOES", "MOTIVO_DA_PROVA_DO_ENSINADO",
           "TIPO_DECIDIDO",
           "TIPO_ESPERA_DECISAO", "TIPO_REBAIXADO", "TIPO_SEM_RECEITA", "AvisoDoEnsinado", "DecisaoDoEnsinado",
           "EsperaDoEnsinado", "decisao_da_pessoa", "rebaixado_pelo_sistema", "sessao_de_treino"]

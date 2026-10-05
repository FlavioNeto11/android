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
        """Só identificadores e vocabulário fechado: a mensagem também é persistida e transmitida."""
        quem = f"{self.kind} {self.ref}" + (f" ({self.app})" if self.app else "")
        if self.sem_receita_ativa:
            return f"Conhecimento ensinado rebaixado pelo sistema, a etapa voltou para a IA: {quem} [{self.para}]"
        return f"Conhecimento ensinado rebaixado pelo sistema: {quem} [{self.para}]"

    @property
    def nivel(self) -> str:
        return "warn" if self.sem_receita_ativa else "info"


__all__ = ["CAMPOS_DO_PAYLOAD", "TIPO_REBAIXADO", "TIPO_SEM_RECEITA", "AvisoDoEnsinado",
           "rebaixado_pelo_sistema", "sessao_de_treino"]

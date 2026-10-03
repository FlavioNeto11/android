"""Por que a consulta de receita deu "ausente" e de quem a etapa pode herdar (RA-20, item 29.40: o vocabulário e a regra).

Medido em 03/10 (reavaliação, RA-20): 52 % das consultas de receita dão "ausente", mas quase nenhuma por falta de
receita. A chave da receita exige a versão completa do app, a assinatura e a variante de interface; uma receita viva
noutra variante (as 19 legadas de 17/09, com `variant=''` e `app_signature=''`), noutra versão ou noutra assinatura não
casa, e a etapa vai à IA. Contar tudo isso como "ausente" esconde o que dá para herdar do que só se aprende de novo.

Quem consulta (`taskqueue/recipes.py::RecipeStore.find`) lê as receitas do mesmo pacote e da mesma etapa (`step_hash`)
com UMA consulta a mais, só no ramo do "ausente", e pergunta aqui a causa e a doadora. Fatos, nunca texto de tela.

**A causa**, vocabulário fechado, na ordem em que a regra decide (a primeira que vale ganha):

- `espera_o_dono`: a receita da chave existe e está `validated` (D1: concordou e tem efeito; espera o dono no Livro);
- `desligada`: a receita da chave existe, mas foi posta de lado (`superseded`) e nada a substitui;
- a diferença da receita viva (`active` ou `candidate`) mais próxima, nesta ordem:
  - `variante`: mesma versão e assinatura, outra variante de interface;
  - `legada`: mesma versão, gravada antes de a chave ter variante e assinatura (`''`);
  - `versao`: outra versão do app, com a mesma assinatura (ou a legada, `''`);
  - `assinatura`: só noutra assinatura (outro APK que diz a mesma coisa; nunca doa);
- `sem_receita`: a etapa nunca foi aprendida neste pacote (ou só sobrou receita em quarentena).

A quarentena na própria chave não chega aqui: `find` já a conta como `quarentena`.

**A doadora** é uma receita `active` (provada) da mesma etapa, com a assinatura igual ou desconhecida (`''`), a mais
próxima pela mesma ordem e, no empate, a mais nova. Quem herda nasce `candidate`: a IA continua decidindo a etapa e a
herdeira só é comparada, como qualquer receita em prova (`recipes_promote_after`); com efeito externo, a promoção para
em `validated` e espera o dono (D1). Uma assinatura DIFERENTE nunca doa: pode ser outro app com o mesmo nome e versão.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

#: Os estados de `recipes.status` em que a receita AGE ou está em prova: as que a chave poderia ter casado.
VIVAS = frozenset({"active", "candidate"})


class CausaDoAusente(StrEnum):
    ESPERA_O_DONO = "espera_o_dono"
    DESLIGADA = "desligada"
    VARIANTE = "variante"
    LEGADA = "legada"
    VERSAO = "versao"
    ASSINATURA = "assinatura"
    SEM_RECEITA = "sem_receita"


#: Da diferença mais próxima para a mais distante: decide a causa e a doadora.
_PROXIMIDADE = (CausaDoAusente.VARIANTE, CausaDoAusente.LEGADA, CausaDoAusente.VERSAO, CausaDoAusente.ASSINATURA)


@dataclass(frozen=True, slots=True)
class ChaveDaReceita:
    """A parte da chave que pode divergir entre receitas do mesmo pacote e da mesma etapa."""

    app_version: str
    assinatura: str
    variante: str


@dataclass(frozen=True, slots=True)
class ReceitaVizinha:
    """Uma receita do mesmo pacote e da mesma etapa (`step_hash`), qualquer chave e qualquer estado."""

    id: int
    chave: ChaveDaReceita
    status: str


def _diferenca(de: ChaveDaReceita, alvo: ChaveDaReceita) -> CausaDoAusente:
    if de.assinatura not in ("", alvo.assinatura):
        return CausaDoAusente.ASSINATURA
    if de.app_version != alvo.app_version:
        return CausaDoAusente.VERSAO
    if de.assinatura == "" or de.variante == "":
        return CausaDoAusente.LEGADA
    return CausaDoAusente.VARIANTE


def causa_do_ausente(alvo: ChaveDaReceita, vizinhas: Iterable[ReceitaVizinha]) -> CausaDoAusente:
    """A causa de uma consulta "ausente" na chave `alvo`, pelas receitas do mesmo pacote e da mesma etapa."""
    lidas = list(vizinhas)
    na_chave = {r.status for r in lidas if r.chave == alvo}
    if "validated" in na_chave:
        return CausaDoAusente.ESPERA_O_DONO
    if "superseded" in na_chave:
        return CausaDoAusente.DESLIGADA
    diferencas = {_diferenca(r.chave, alvo) for r in lidas if r.status in VIVAS and r.chave != alvo}
    return next((c for c in _PROXIMIDADE if c in diferencas), CausaDoAusente.SEM_RECEITA)


def doadora(alvo: ChaveDaReceita, vizinhas: Iterable[ReceitaVizinha]) -> ReceitaVizinha | None:
    """A receita `active` de quem a chave `alvo` herda, ou `None`. A causa decide antes: com a chave esperando o dono
    ou desligada, ninguém herda (o dono decide ali; o que foi posto de lado não volta por outra chave)."""
    lidas = list(vizinhas)
    if causa_do_ausente(alvo, lidas) in (CausaDoAusente.ESPERA_O_DONO, CausaDoAusente.DESLIGADA):
        return None
    aptas = [r for r in lidas if r.status == "active" and r.chave != alvo
             and _diferenca(r.chave, alvo) is not CausaDoAusente.ASSINATURA]
    if not aptas:
        return None
    return min(aptas, key=lambda r: (_PROXIMIDADE.index(_diferenca(r.chave, alvo)), -r.id))


__all__ = ["VIVAS", "CausaDoAusente", "ChaveDaReceita", "ReceitaVizinha", "causa_do_ausente", "doadora"]

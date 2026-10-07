"""31.202: o rendimento da receita ensinada sugere, em SOMBRA, liberar ou prender de volta. Nada se aplica.

Hoje (30.81) a receita do ensino vale só para a persona que ensinou até o "Confirmar que fica" ou a prova real, e o uso
comum de quem ensinou não conta; a quarentena (3 falhas seguidas) vale para todas as personas de uma vez. Este parecer
lê o uso REAL de cada receita ensinada e diz o que faria, sem fazer:

* `liberaria`: ainda presa a quem ensinou, e rendeu no uso real dela: `USOS_PARA_LIBERAR` usos sem IA em
  `EXECUCOES_PARA_LIBERAR` execuções distintas, e nenhuma falha nas últimas `JANELA_SEM_FALHA` tentativas dela;
* `prenderia_de_volta`: já liberada, e as últimas `FALHAS_PARA_PRENDER` tentativas reais FORA de quem ensinou falharam
  (a receita não conduziu sozinha);
* `nenhuma`: o resto, com o motivo.

Nunca sugere liberar a receita com efeito externo (ação `commit`) nem a que mira a conta da PRÓPRIA persona (o
parâmetro `{conta_<app>_usuario}`, o aviso do 31.182): a primeira segue no catálogo e na aprovação, e a segunda não serve
a outra persona como foi gravada. Ligar de verdade é a pergunta do 31.202 ao dono.

Só contagens e ids: nada de nome, @ ou texto de tela.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.modules.skills.domain.document import JsonObject

VERSAO_DA_REGRA = "31.202-v1"
USOS_PARA_LIBERAR = 3
EXECUCOES_PARA_LIBERAR = 2
JANELA_SEM_FALHA = 3
FALHAS_PARA_PRENDER = 2
#: O parâmetro da conta da própria persona (o mesmo molde do aviso do 31.182, `training/conta_propria.py`).
CONTA_PROPRIA = re.compile(r"\{conta_\w+?_usuario(?:_\d+)?\}")


class Sugestao(StrEnum):
    LIBERARIA = "liberaria"
    PRENDERIA_DE_VOLTA = "prenderia_de_volta"
    NENHUMA = "nenhuma"


@dataclass(frozen=True, slots=True)
class TentativaReal:
    """Uma tentativa da receita numa execução REAL (nem simulada, nem de prova, nem de lote), em ordem cronológica."""

    run_id: str
    persona: str                 # `objectives.profile_id`; '' quando a etapa não tem objetivo
    sem_ia: bool                 # a receita conduziu sozinha e a etapa comprovou


@dataclass(frozen=True, slots=True)
class ReceitaEnsinada:
    id: int
    app: str
    quem_ensinou: str            # `training_sessions.profile_id`; '' quando a sessão não diz
    liberada: bool               # 30.81: a régua da loja de receitas (`liberada_fora_do_ensino`)
    com_efeito: bool
    propria_conta: bool
    tentativas: tuple[TentativaReal, ...]


@dataclass(frozen=True, slots=True)
class Parecer:
    sugestao: Sugestao
    motivo: str
    contagem: JsonObject


def mira_a_propria_conta(acoes: str) -> bool:
    return CONTA_PROPRIA.search(acoes or "") is not None


def _contagem(de_quem: Sequence[TentativaReal], fora: Sequence[TentativaReal]) -> JsonObject:
    bons = [t for t in de_quem if t.sem_ia]
    return {"de_quem_ensinou": len(de_quem), "sem_ia_de_quem_ensinou": len(bons),
            "execucoes_sem_ia": len({t.run_id for t in bons}), "fora": len(fora),
            "sem_ia_fora": sum(t.sem_ia for t in fora)}


def parecer(r: ReceitaEnsinada) -> Parecer:
    de_quem = [t for t in r.tentativas if r.quem_ensinou and t.persona == r.quem_ensinou]
    fora = [t for t in r.tentativas if not (r.quem_ensinou and t.persona == r.quem_ensinou)]
    contagem = _contagem(de_quem, fora)
    if r.liberada:
        ultimas = fora[-FALHAS_PARA_PRENDER:]
        if len(ultimas) == FALHAS_PARA_PRENDER and not any(t.sem_ia for t in ultimas):
            return Parecer(Sugestao.PRENDERIA_DE_VOLTA,
                           f"as últimas {FALHAS_PARA_PRENDER} tentativas reais fora de quem ensinou falharam", contagem)
        return Parecer(Sugestao.NENHUMA, "liberada e sem falhas seguidas fora de quem ensinou", contagem)
    if r.com_efeito:
        return Parecer(Sugestao.NENHUMA, "tem efeito externo: nunca liberada por esta regra", contagem)
    if r.propria_conta:
        return Parecer(Sugestao.NENHUMA, "mira a conta da própria persona: não serve a outra", contagem)
    if not r.quem_ensinou:
        return Parecer(Sugestao.NENHUMA, "a sessão não diz quem ensinou", contagem)
    bons = [t for t in de_quem if t.sem_ia]
    execucoes = len({t.run_id for t in bons})
    ultimas = de_quem[-JANELA_SEM_FALHA:]
    if len(bons) < USOS_PARA_LIBERAR or execucoes < EXECUCOES_PARA_LIBERAR:
        return Parecer(Sugestao.NENHUMA, f"{len(bons)} de {USOS_PARA_LIBERAR} usos reais sem IA, em {execucoes} de"
                                         f" {EXECUCOES_PARA_LIBERAR} execuções", contagem)
    if not all(t.sem_ia for t in ultimas):
        return Parecer(Sugestao.NENHUMA, f"falhou numa das últimas {JANELA_SEM_FALHA} tentativas de quem ensinou",
                       contagem)
    return Parecer(Sugestao.LIBERARIA, f"{len(bons)} usos reais sem IA em {execucoes} execuções de quem ensinou, sem"
                                       f" falha nas últimas {JANELA_SEM_FALHA}", contagem)

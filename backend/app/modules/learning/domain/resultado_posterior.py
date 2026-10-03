"""O desfecho medido de uma revisão do curador 14 dias depois (item 30.35): o rótulo 2 do golden set do Jev
(`docs/design/jev-golden-set.md` §2), gravado em `learning_reviews.resultado_posterior` (coluna da 069). Puro: sem
relógio, sem banco.

A regra só lê fatos que já existem: as transições do item na janela, e o uso dele (a tentativa conduzida pela receita;
a exposição da lição no braço `with`). Sem IA. Vale o mais grave, nesta ordem (contrato com a orquestradora, 03/10):

1. o item foi desligado na janela (quarentena, pelo sistema ou por uma pessoa) → `descartar`;
2. desceu na escada (published → validated → candidate → draft) → `rebaixar`;
3. o uso na janela reprova no degrau da saúde D-5 do dono (2 falhas seguidas, ou sucesso < 0,8 com ≥ 5 usos) →
   `rebaixar`: o curador disse que o item ficava e o fato desmentiu. A saúde é só leitura, então o sistema não desliga
   sozinho, e sem este degrau a linha viraria `sem_desfecho`;
4. ≥ 1 uso e sucesso ≥ 0,8 → `manter`;
5. o resto → `sem_desfecho`.

`deprecated` não é desfecho: a receita substituída por versão nova é absorção (a mesma regra das métricas, §10). E
`sem_desfecho` fica fora da régua da triagem: o relatório do 31.10 o ignora, e a linha não é reavaliada.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.saude import LimiaresDeSaude

#: A janela do rótulo, contada da criação da revisão. Um campo, uma janela: a de 30 dias do golden set fica fora.
JANELA_DIAS = 14

#: A escada do ciclo: descer nela é rebaixar (a mesma direção do `_rotulo_da_transicao` do relatório do 31.10).
ESCADA = {SkillState.DRAFT: 0, SkillState.CANDIDATE: 1, SkillState.VALIDATED: 2, SkillState.PUBLISHED: 3}


class ResultadoPosterior(StrEnum):
    MANTER = "manter"
    REBAIXAR = "rebaixar"
    DESCARTAR = "descartar"
    SEM_DESFECHO = "sem_desfecho"           # fora da régua da triagem: não é rótulo


@dataclass(frozen=True, slots=True)
class MudancaNaJanela:
    de: SkillState | None
    para: SkillState


@dataclass(frozen=True, slots=True)
class FatosDaJanela:
    mudancas: tuple[MudancaNaJanela, ...] = ()
    usos: tuple[bool, ...] = ()             # em ordem de tempo; True = sucesso


def fim_da_janela(criada_em: datetime) -> datetime:
    return criada_em + timedelta(days=JANELA_DIAS)


def desligou(m: MudancaNaJanela) -> bool:
    """Entrou em `disabled`. A linha `disabled → disabled` só reclassifica um desligamento antigo (30.23): não conta."""
    return m.para is SkillState.DISABLED and m.de is not SkillState.DISABLED


def desceu(m: MudancaNaJanela) -> bool:
    return m.de in ESCADA and m.para in ESCADA and ESCADA[m.para] < ESCADA[m.de]


def falhas_seguidas(usos: Sequence[bool]) -> int:
    maior = atual = 0
    for ok in usos:
        atual = 0 if ok else atual + 1
        maior = max(maior, atual)
    return maior


def saude_reprovada(usos: Sequence[bool], limiares: LimiaresDeSaude) -> bool:
    """O degrau D-5 do dono (02/10) sobre o uso da janela: os mesmos limiares da saúde (`aprendizado.saude`)."""
    if falhas_seguidas(usos) >= limiares.falhas_seguidas:
        return True
    return len(usos) >= limiares.amostra_minima and sum(usos) / len(usos) < limiares.taxa_minima


def resultado(f: FatosDaJanela, limiares: LimiaresDeSaude = LimiaresDeSaude()) -> ResultadoPosterior:
    if any(desligou(m) for m in f.mudancas):
        return ResultadoPosterior.DESCARTAR
    if any(desceu(m) for m in f.mudancas):
        return ResultadoPosterior.REBAIXAR
    if saude_reprovada(f.usos, limiares):
        return ResultadoPosterior.REBAIXAR
    if f.usos and sum(f.usos) / len(f.usos) >= limiares.taxa_minima:
        return ResultadoPosterior.MANTER
    return ResultadoPosterior.SEM_DESFECHO


__all__ = ["ESCADA", "JANELA_DIAS", "FatosDaJanela", "MudancaNaJanela", "ResultadoPosterior", "desceu", "desligou",
           "falhas_seguidas", "fim_da_janela", "resultado", "saude_reprovada"]

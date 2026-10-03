"""30.39: a evidência datada da receita.

A receita sempre teve contadores (`recipes.replay_ok/replay_fail`), mas contador não tem data, aparelho, versão nem a
marca de real ou simulado: o curador não tinha o que citar e pedia `execucao_real` mesmo com dezenas de reproduções
boas. Aqui cada execução assentada vira linhas de `learning_evidence` de `receita:<id>`, uma por (receita, execução,
posição), lidas do que o executor já grava por etapa (`steps.driven_by` e `attempts.recipe_id`):

- **a favor**: a etapa foi conduzida só pela receita (`driven_by = 'recipe'`) e comprovada;
- **contra**: a receita foi consultada e a etapa não terminou por ela (`recipe+ai`: divergiu e a IA assumiu; ou
  `sem_ator`). É o ponto onde o executor soma `replay_fail` (`taskqueue/executor.py::_after_step`).

Só grava evidência. Não decide nada sobre a receita: o D1 e a quarentena seguem donos do estado dela, e a saúde dela
lê os contadores da loja (`LearningService.saude_de`).

Dois caminhos, o mesmo leitor e a mesma chave única (idempotente):

- `EvidenciaDaReceita`: minerador do digest, a execução que acabou de assentar;
- `RetrocargaDaReceita`: passo da curadoria que completa o que o digest não viu (as tentativas de antes do 30.39, um
  digest que falhou). Respeita a retenção: não enche uma receita além de `evidencias_por_item`, senão a purga e o passo
  ficariam se desfazendo um ao outro.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.modules.learning.application.ports import NovaEvidencia, RepositorioDeAprendizado
from app.modules.learning.domain.livro import ref_da_trilha
from app.modules.learning.domain.promocao import ORIGEM_DA_REPRODUCAO
from app.modules.learning.domain.prova import MotivoDaInvalida, detalhe_da_invalida
from app.modules.learning.domain.vocabulario import LivroKind, Posicao


@dataclass(frozen=True, slots=True)
class ReproducaoDaReceita:
    """O que uma receita fez numa execução, numa posição: todas as etapas dela na execução, juntas."""

    receita: int
    run_id: str
    posicao: Posicao                  # FOR | AGAINST
    simulada: bool
    detalhe: str
    aparelho: str | None = None       # o aparelho da primeira etapa do grupo
    app_version: str | None = None    # a versão do app em que a receita casou (`recipes.app_version`)
    em: str | None = None             # o fim da última etapa do grupo; `None`: o relógio do repositório


class LeituraDeReproducoes(Protocol):
    def da_execucao(self, run_id: str) -> list[ReproducaoDaReceita]:
        """As reproduções da execução (qualquer estado dela), uma por (receita, posição)."""
        ...

    def faltantes(self) -> list[ReproducaoDaReceita]:
        """As de execuções já terminadas que ainda não têm a linha de evidência, das mais novas para as mais antigas."""
        ...

    def linhas_por_receita(self) -> dict[int, int]:
        """Quantas linhas de evidência cada receita já tem (para o passo respeitar a retenção)."""
        ...


def _gravar(repo: RepositorioDeAprendizado, x: ReproducaoDaReceita) -> bool:
    return repo.registrar_evidencia(NovaEvidencia(
        item_ref=ref_da_trilha(LivroKind.RECEITA, str(x.receita)), stance=x.posicao, origin_ref=f"{ORIGEM_DA_REPRODUCAO}{x.run_id}",
        simulated=x.simulada, run_id=x.run_id, instance_id=x.aparelho, app_version=x.app_version, detail=x.detalhe,
        observed_at=x.em))


class EvidenciaDaReceita:
    """Minerador do digest: as reproduções da execução viram evidência da receita que as conduziu."""

    nome = "receitas_evidencia"

    def __init__(self, repo: RepositorioDeAprendizado, leitura: LeituraDeReproducoes) -> None:
        self._repo = repo
        self._leitura = leitura

    def minerar(self, run_id: str) -> int:
        return sum(1 for x in self._leitura.da_execucao(run_id) if _gravar(self._repo, x))


class RetrocargaDaReceita:
    """Passo da curadoria: completa a evidência das reproduções que o digest não gravou. Idempotente pela chave única;
    nunca chama IA nem toca estado de receita."""

    nome = "receitas_evidencia_retrocarga"

    def __init__(self, repo: RepositorioDeAprendizado, leitura: LeituraDeReproducoes, *,
                 limite_por_receita: Callable[[], int]) -> None:
        self._repo = repo
        self._leitura = leitura
        self._limite = limite_por_receita

    def executar(self, agora: datetime) -> int:
        faltantes = self._leitura.faltantes()
        if not faltantes:
            return 0
        tem = self._leitura.linhas_por_receita()
        por_receita: dict[int, list[ReproducaoDaReceita]] = defaultdict(list)
        for x in faltantes:
            por_receita[x.receita].append(x)
        feito = 0
        for receita, lista in por_receita.items():
            folga = max(0, self._limite() - tem.get(receita, 0))      # as mais novas primeiro: é a ordem da leitura
            feito += sum(1 for x in lista[:folga] if _gravar(self._repo, x))
        return feito


# ------------------------------------------------------------------ 30.43: a reprodução que repetiu o efeito
@dataclass(frozen=True, slots=True)
class ReproducaoAConferir:
    """Uma linha `reproducao:` (for/against) ainda sem a `invalida` irmã, de execução onde o efeito repetido se mede:
    execução de VALIDAÇÃO (`contracts.origem.eh_execucao_de_validacao`) ou com o `efeito_repetido` do 29.58."""

    item_ref: str
    origin_ref: str
    run_id: str
    simulada: bool
    aparelho: str | None
    de_validacao: bool


class LeituraDoEfeito(Protocol):
    def reproducoes_a_conferir(self, run_id: str | None = None) -> list[ReproducaoAConferir]: ...
    def efeito_repetido_da_execucao(self, run_id: str, *, regra_propria: bool = True) -> int | None: ...


class InvalidaDaReproducao:
    """30.43: a reprodução da receita numa execução que repetiu o efeito não vale: ganha a linha `invalida` irmã (mesma
    origem `reproducao:<run>`, motivo `efeito_repetido`). Caso do P4 de 03/10 (6f459c, receita:82): a IA abriu o app
    dentro da conversa e enviou já na abertura, e a receita enviou de novo na etapa dela.

    A regra é a do 30.42 (`domain.prova.efeito_repetido`): o 29.58 vence; a regra própria, sobre o diário, só na
    execução de validação (a orgânica tem a IA livre e mais ruído). O pedido de validação da receita fecha
    `recusada/efeito_repetido` pela `invalida` (o mesmo ramo da prova de fluxo), e não `feita`. Os contadores
    `replay_ok/replay_fail` não mudam: a receita conduziu a etapa dela, e a linha `reproducao:` já fica fora das
    contagens (`promocao.efetivas`). Serve ao dossiê e ao fechamento do pedido.

    Dois papéis, a mesma regra: minerador do digest (a execução que acabou de assentar, antes do fechamento do pedido)
    e passo da curadoria (a reclassificação POR REGRA do que já foi gravado, o 6f459c incluído). Idempotente pelo
    índice único; nunca UPDATE, nunca IA. O pedido já `feita` não reabre."""

    nome = "receitas_efeito_repetido"

    def __init__(self, repo: RepositorioDeAprendizado, leitura: LeituraDoEfeito) -> None:
        self._repo = repo
        self._leitura = leitura

    def minerar(self, run_id: str) -> int:
        return self._marcar(self._leitura.reproducoes_a_conferir(run_id))

    def executar(self, agora: datetime) -> int:
        return self._marcar(self._leitura.reproducoes_a_conferir())

    def _marcar(self, linhas: list[ReproducaoAConferir]) -> int:
        copias_por_run: dict[str, int | None] = {}
        feito = 0
        for x in linhas:
            if x.run_id not in copias_por_run:
                copias_por_run[x.run_id] = self._leitura.efeito_repetido_da_execucao(
                    x.run_id, regra_propria=x.de_validacao)
            copias = copias_por_run[x.run_id]
            if copias is None:
                continue
            feito += int(self._repo.registrar_evidencia(NovaEvidencia(
                item_ref=x.item_ref, stance=Posicao.INVALIDA, origin_ref=x.origin_ref, simulated=x.simulada,
                run_id=x.run_id, instance_id=x.aparelho,
                detail=detalhe_da_invalida(MotivoDaInvalida.EFEITO_REPETIDO,
                                           f"o efeito saiu {copias} vezes nesta execução"))))
        return feito


__all__ = ["EvidenciaDaReceita", "InvalidaDaReproducao", "LeituraDeReproducoes", "LeituraDoEfeito",
           "ReproducaoAConferir", "ReproducaoDaReceita", "RetrocargaDaReceita"]

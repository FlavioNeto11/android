"""O rótulo de intenção (item 30.25), o DOMÍNIO: qual execução vira pergunta, o que o dossiê guarda e que resposta vale.

Para quê: o decisor fechado da intenção (31.x, da Jev) precisa de um gabarito humano. A cadeia de resolução (RESOLVE)
não casou o comando com nenhuma habilidade, ou empatou entre várias, e mesmo assim a execução deu certo com prova. A pessoa
diz qual habilidade do catálogo era aquela intenção, ou que nenhuma era. É um rótulo CEGO: não há parecer de IA nem
sugestão na tela, e nada aqui chama IA.

- **Só sucesso comprovado.** A execução real (`simulated=0`), `completed` (não `completed_with_issues`), com pelo menos
  uma etapa e TODAS `succeeded` com a pós-condição provada (`result.verified`). Falha provada, etapa incerta, pulada ou
  confirmada à mão ficam fora: o gabarito é "o que o comando pedia e foi feito", não "o que se tentou".
- **Só a cadeia que não resolveu.** `sem_casamento` (NO_MATCH) ou `empate` (candidatos e nenhuma resolvida). A cadeia
  que resolveu, ainda que depois de um empate, não tem pergunta a fazer.
- **O dossiê guarda ids, nunca texto.** O catálogo inteiro que a cadeia enxergava (os `skill_id`, em ordem canônica,
  mesmo num empate), os empatados à parte e a cadeia. O comando NÃO entra: o painel o lê da execução na hora de mostrar,
  e o nome de cada habilidade também sai do catálogo de agora.
- **A resposta é do catálogo gravado.** Um dos candidatos do dossiê, ou `nenhum`. O que não está lá é 422.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.modules.learning.domain.ciclo import EntradaInvalida
from app.modules.skills.domain.document import JsonObject, JsonValue, content_hash

#: `learning_reviews.template_id` do rótulo. O curador grava `curador`; os leitores dele filtram por isso.
TEMPLATE_DO_ROTULO = "intencao"
#: `learning_reviews.template_versao`: a forma do dossiê. Muda quando os campos mudarem.
VERSAO_DO_ROTULO = "rotulo-v1"
#: `learning_reviews.item_kind` do rótulo.
KIND_DO_ROTULO = "execucao"
#: A resposta "nenhuma habilidade do catálogo era esta intenção". O Jev a mapeia para o `ID_NENHUMA` dele.
NENHUM = "nenhum"


class Cadeia(StrEnum):
    """Por que a execução virou pergunta. Vai ao dossiê (`cadeia`)."""

    SEM_CASAMENTO = "sem_casamento"
    EMPATE = "empate"


class GatilhoDoRotulo(StrEnum):
    """`learning_reviews.gatilho` do rótulo. Vocabulário fechado, à parte do curador (`orcamento_do_curador.Gatilho`)."""

    EXECUCAO_SEM_INTENCAO = "execucao_sem_intencao"


@dataclass(frozen=True, slots=True)
class FatosDaExecucao:
    """O que se lê da execução para decidir se ela entra. `etapas_comprovadas`: `succeeded` com `result.verified`.
    `resolvida_no_plano`: o planejamento já casou uma habilidade (`runs.skill_id`); não há pergunta a fazer."""

    status: str
    simulated: bool
    etapas: int
    etapas_comprovadas: int
    resolvida_no_plano: bool = False


def sucesso_comprovado(f: FatosDaExecucao) -> bool:
    return f.status == "completed" and not f.simulated and f.etapas > 0 and f.etapas_comprovadas == f.etapas


def entra_no_rotulo(f: FatosDaExecucao) -> bool:
    """A execução pode virar pergunta: sucesso comprovado e real, sem habilidade casada no plano. A cadeia de agora
    ainda decide (`cadeia_a_rotular`)."""
    return sucesso_comprovado(f) and not f.resolvida_no_plano


def cadeia_a_rotular(*, resolvida: str | None, sem_casamento: bool, empatados: Sequence[str]) -> Cadeia | None:
    """`None`: a cadeia resolveu (nada a perguntar) ou não disse nada que sirva."""
    if resolvida is not None:
        return None
    if sem_casamento:
        return Cadeia.SEM_CASAMENTO
    return Cadeia.EMPATE if len(set(empatados)) >= 2 else None


def item_ref_da_execucao(run_id: str) -> str:
    return f"run:{run_id}"


def run_id_do_item(item_ref: str) -> str | None:
    return item_ref[4:] if item_ref.startswith("run:") and len(item_ref) > 4 else None


@dataclass(frozen=True, slots=True)
class DossieDoRotulo:
    cadeia: Cadeia
    candidatos: tuple[str, ...]
    empatados: tuple[str, ...] = ()

    @staticmethod
    def montar(cadeia: Cadeia, candidatos: Iterable[str], empatados: Iterable[str] = ()) -> DossieDoRotulo:
        """Ordem canônica (o hash não depende da ordem do catálogo) e sem repetição. Empatado fora do catálogo não
        entra: a resposta só pode ser um candidato."""
        cands = tuple(sorted({c for c in candidatos if c}))
        return DossieDoRotulo(cadeia, cands, tuple(sorted({e for e in empatados if e in cands})))

    def como_dados(self) -> JsonObject:
        dados: JsonObject = {"cadeia": self.cadeia.value, "candidatos": list(self.candidatos)}
        if self.empatados:
            dados["empatados"] = list(self.empatados)
        return dados

    @property
    def dossie_hash(self) -> str:
        return content_hash(self.como_dados())

    @staticmethod
    def de_dados(dados: JsonObject) -> DossieDoRotulo | None:
        """O dossiê gravado de volta. `None` quando a forma não é a do rótulo."""
        try:
            cadeia = Cadeia(str(dados.get("cadeia")))
        except ValueError:
            return None
        return DossieDoRotulo(cadeia, _ids(dados.get("candidatos")), _ids(dados.get("empatados")))


def _ids(valor: JsonValue | None) -> tuple[str, ...]:
    return tuple(v for v in valor if isinstance(v, str)) if isinstance(valor, list) else ()


def conferir_escolha(escolha: str, dossie: DossieDoRotulo) -> str:
    """A resposta da pessoa: um candidato do dossiê GRAVADO ou `nenhum`. O resto é 422."""
    e = escolha.strip()
    if e == NENHUM or e in dossie.candidatos:
        return e
    raise EntradaInvalida(f"'{e}' não é uma habilidade do catálogo que a cadeia via nesta execução, nem '{NENHUM}'.")


__all__ = ["KIND_DO_ROTULO", "NENHUM", "TEMPLATE_DO_ROTULO", "VERSAO_DO_ROTULO", "Cadeia", "DossieDoRotulo",
           "FatosDaExecucao", "GatilhoDoRotulo", "cadeia_a_rotular", "conferir_escolha", "entra_no_rotulo",
           "item_ref_da_execucao",
           "run_id_do_item", "sucesso_comprovado"]

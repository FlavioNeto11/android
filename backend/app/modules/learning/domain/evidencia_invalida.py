"""Evidência inválida (item 30.23, decisão do orquestrador de 02/10): o tipo PRÓPRIO de desligamento do que foi
aprendido de um sucesso falso — a execução de origem terminou como sucesso sem comprovar o que fez. O caso que o abriu:
a receita 109 e o fluxo da leitura do Outlook, aprendidos da r-20261002204347-8c3f6e, que fechou `completed` sem as
saídas pedidas (antes do 12.4).

Três regras, puras (sem banco, sem relógio):

- o MOTIVO na trilha é estruturado e de vocabulário fechado: exatamente `evidencia_invalida:<run>`, com `<run>` no
  formato de `new_run_id` (`r-AAAAMMDDhhmmss-xxxxxx`). Só a ação própria do livro o escreve
  (`LearningService.invalidar_evidencia`); a rota genérica de estado recusa motivo com o prefixo (`reservado`), para
  ninguém forjar o tipo num texto livre;
- o VETO desse tipo barra só renascer da MESMA execução (`ciclo.motivo_do_veto`, com o `Renascimento`): outra execução
  REAL pode ensinar o mesmo conteúdo de novo. Execução desconhecida ou simulada não passa (o lado seguro);
- o REAPRENDIDO (`reaprendizado`, derivado da trilha, nunca gravado) espera o dono: classe B forçada
  (`politica_de_risco`), e o sistema não o publica (`ciclo.exige_o_dono`). É o que (re)nasce no MESMO escopo depois da
  evidência inválida, até uma PESSOA publicar algo nesse escopo: com a decisão dela, a confiança no escopo volta.

O escopo é o `scope_key` do livro: na receita, a chave exata da etapa (pacote, versão do app, assinatura da tela,
variante, etapa); no fluxo, o `match_key`, que é ÚNICO — o fluxo renasce na mesma linha, então o "item novo" do fluxo é
a mesma linha reaprendida, e a relação aponta para ela mesma (o painel mostra a marca, não a relação). Se a versão do
app mudar antes de reaprender, a receita nova é outro escopo: sem relação e sem classe forçada (o D1 de sempre).
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.modules.skills.domain.lifecycle import Actor, SkillState, actor_of

#: O prefixo do tipo na trilha. Reservado: só a ação própria o escreve.
PREFIXO = "evidencia_invalida"
#: O id de execução de `app.util.new_run_id`: `r-` + data e hora em 14 dígitos + 6 hexadecimais.
_RUN = r"r-\d{14}-[0-9a-f]{6}"
_RUN_ID = re.compile(_RUN)
_MOTIVO = re.compile(rf"{PREFIXO}:({_RUN})")
#: O id de etapa (`steps.id`): `<run>:<aparelho>:v<versão do plano>:<chave>` (001_init).
_ETAPA = re.compile(rf"({_RUN}):.+")


def run_valida(run_id: str) -> bool:
    return _RUN_ID.fullmatch(run_id) is not None


def motivo_de_evidencia_invalida(run_id: str) -> str:
    """O motivo estruturado que a trilha grava. Recusa o que não é id de execução: o tipo não aceita texto livre."""
    if not run_valida(run_id):
        raise ValueError(f"'{run_id}' não é um id de execução (r-AAAAMMDDhhmmss-xxxxxx)")
    return f"{PREFIXO}:{run_id}"


def run_invalidada(reason: str | None) -> str | None:
    """A execução de um motivo `evidencia_invalida:<run>` (casamento EXATO), ou `None`: qualquer outro texto, inclusive
    o "Invalidado (...)" escrito à mão antes deste tipo existir, não é o tipo."""
    m = _MOTIVO.fullmatch(reason or "")
    return m.group(1) if m else None


def reservado(reason: str) -> bool:
    """O motivo que a rota genérica recusa: começa com o prefixo do tipo (sem diferenciar maiúsculas). "Evidência
    inválida: ..." escrito por extenso, com acento e espaço, segue livre: não casa com o tipo."""
    return reason.strip().casefold().startswith(PREFIXO)


def run_da_etapa(step_id: str | None) -> str | None:
    """A execução de um `steps.id` (`learned_from_step` da receita), ou `None` (treino, vazio, formato desconhecido)."""
    m = _ETAPA.fullmatch(step_id or "")
    return m.group(1) if m else None


@dataclass(frozen=True, slots=True)
class Renascimento:
    """Quem tenta trazer o conteúdo de volta: a execução que o ensinou agora e se ela foi real (`runs.simulated=0`)."""

    run_id: str
    real: bool


@dataclass(frozen=True, slots=True)
class Reaprendizado:
    """O item (re)nasceu no escopo de uma evidência inválida: espera o dono (classe B forçada)."""

    #: A execução do sucesso falso (a do motivo `evidencia_invalida:<run>`).
    run_invalidada: str
    #: A ref da trilha do item desligado por ela (`receita:109`; no fluxo, a própria linha).
    item_invalidado: str


class LinhaDaTrilha(Protocol):
    """O que `reaprendizado` lê de cada linha de `learning_transitions` (a `livro.Transicao` cumpre)."""

    @property
    def id(self) -> int: ...
    @property
    def item_ref(self) -> str: ...
    @property
    def from_state(self) -> SkillState | None: ...
    @property
    def to_state(self) -> SkillState: ...
    @property
    def reason(self) -> str: ...
    @property
    def decided_by(self) -> str: ...


def nascimento(t: LinhaDaTrilha) -> bool:
    """A linha que (re)abre um item: o nascimento (`de` vazio) ou a linha desligada que a loja do fluxo reaproveita
    (`disabled → candidate`: só a loja escreve esse passo; a tabela do ciclo não o tem)."""
    return t.from_state is None or (t.from_state is SkillState.DISABLED and t.to_state is SkillState.CANDIDATE)


def marca_do_escopo(trilha_do_escopo: Sequence[LinhaDaTrilha]) -> LinhaDaTrilha | None:
    """A evidência inválida mais recente do escopo (a trilha vem na ordem do `id`)."""
    marca: LinhaDaTrilha | None = None
    for t in trilha_do_escopo:
        if t.to_state is SkillState.DISABLED and run_invalidada(t.reason) is not None:
            marca = t
    return marca


def reaprendizado(trilha_do_escopo: Sequence[LinhaDaTrilha], item_ref: str) -> Reaprendizado | None:
    """O item `item_ref` é reaprendido? Sim quando o (re)nascimento mais recente dele veio DEPOIS da evidência inválida
    mais recente do escopo e nenhuma pessoa publicou nada no escopo entre as duas. `trilha_do_escopo`: todas as linhas
    com o mesmo `scope_key`, na ordem do `id` (o relógio empata no mesmo milissegundo)."""
    marca = marca_do_escopo(trilha_do_escopo)
    if marca is None:
        return None
    depois = [t for t in trilha_do_escopo if t.id > marca.id]
    nascimentos = [t for t in depois if t.item_ref == item_ref and nascimento(t)]
    if not nascimentos:
        return None
    ultimo = nascimentos[-1]
    if any(t.id < ultimo.id and t.to_state is SkillState.PUBLISHED and actor_of(t.decided_by) is Actor.PERSON
           for t in depois):
        return None                                    # a pessoa já decidiu no escopo: a confiança voltou
    run = run_invalidada(marca.reason)
    return None if run is None else Reaprendizado(run_invalidada=run, item_invalidado=marca.item_ref)


def ja_invalidada(trilha_do_item: Sequence[LinhaDaTrilha], run_id: str) -> bool:
    """A trilha do item já tem a evidência inválida desta execução depois do (re)nascimento mais recente dele: marcar
    de novo não muda nada (a ação é idempotente)."""
    ultimo_nascimento = max((t.id for t in trilha_do_item if nascimento(t)), default=0)
    return any(t.id > ultimo_nascimento and run_invalidada(t.reason) == run_id for t in trilha_do_item)


__all__ = ["PREFIXO", "LinhaDaTrilha", "Reaprendizado", "Renascimento", "ja_invalidada", "marca_do_escopo",
           "motivo_de_evidencia_invalida", "nascimento", "reaprendizado", "reservado", "run_da_etapa",
           "run_invalidada", "run_valida"]

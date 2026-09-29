"""Ciclo de vida do livro de aprendizado com o D1 (ADR-054, decisão 1 do dono): o sistema publica sozinho só o que
não tem efeito externo e se repetiu.

Reaproveita `SkillState`, `Actor`, `actor_of` e `SYSTEM_ACTOR` das habilidades (DAG `learning → skills`), mas a
tabela é outra: aqui o sistema também publica (sem efeito, com o modo do tipo em `on`) e rebaixa sozinho. Como em
`skills/domain/lifecycle.py`, a regra vive num dicionário fechado que um teste percorre estado × estado × ator ×
side_effect × human_origin — o que não está nele é proibido.

Três regras em volta da tabela:
- `requires_owner = side_effect OR human_origin` é DERIVADO (nunca gravado nem editado por rota): o sistema nunca
  publica o que exige o dono. O repositório confere de novo no próprio `UPDATE` e, com `conferir_nascimento`, no
  item que já nasce num estado (segunda camada);
- rebaixar é automático; promover algo com efeito ou com texto de pessoa é do dono;
- conteúdo desligado por uma PESSOA não volta pelo sistema (veto por `content_hash` no mesmo escopo). Desligado pelo
  sistema, fica vetado por `VETO_DO_SISTEMA_DIAS` ou até mudar a versão do app. A pessoa sempre pode reativar.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.modules.skills.domain.lifecycle import SYSTEM_ACTOR, Actor, SkillState, actor_of
from app.util import parse_iso

__all__ = ["SYSTEM_ACTOR", "Actor", "SkillState", "actor_of", "ESTADOS", "TRANSICOES", "VETO_DO_SISTEMA_DIAS",
           "Desligamento", "conferir_nascimento", "conferir_transicao", "exige_o_dono", "motivo_do_veto", "permitido"]

#: O livro não tem rascunho: o item nasce congelado (mudar é criar outro com `parent_id`).
ESTADOS = frozenset(s for s in SkillState if s is not SkillState.DRAFT)

#: Dias em que o conteúdo desligado PELO SISTEMA não é recriado nem promovido pelo sistema (mesma versão do app).
VETO_DO_SISTEMA_DIAS = 90

_S = SkillState
_P = frozenset({Actor.PERSON})
_PS = frozenset({Actor.PERSON, Actor.SYSTEM})

#: (de, para) → quem pode. `validated → published` pelo sistema tem condição (D1 + modo), conferida em
#: `conferir_transicao`; na tabela ele aparece porque é permitido em princípio.
TRANSICOES: Mapping[tuple[SkillState, SkillState], frozenset[Actor]] = {
    (_S.CANDIDATE, _S.VALIDATED): _PS,     # sistema pela repetição; pessoa valida à mão, com motivo
    (_S.VALIDATED, _S.PUBLISHED): _PS,     # sistema SÓ sem efeito e sem texto de pessoa, com o modo em 'on'
    (_S.CANDIDATE, _S.DISABLED): _PS,      # sistema: contradição, expirou; pessoa: rejeitar
    (_S.VALIDATED, _S.DISABLED): _PS,
    (_S.PUBLISHED, _S.DEPRECATED): _PS,    # sistema: neutra, sem uso, absorvida, versão nova sem suporte
    (_S.PUBLISHED, _S.DISABLED): _PS,      # sistema: atrapalha, conflito, refutação
    (_S.DEPRECATED, _S.PUBLISHED): _P,     # reativar é sempre de pessoa
    (_S.DISABLED, _S.PUBLISHED): _P,
}


# ------------------------------------------------------------------ recusas
class ErroDeAprendizado(Exception):
    """Recusa de uma regra do aprendizado. `code` é estável (vai para a API); a mensagem é para a pessoa."""

    code = "learning_error"


class NaoEncontrado(ErroDeAprendizado):
    code = "not_found"


class EntradaInvalida(ErroDeAprendizado):
    code = "invalid"


class TransicaoProibida(ErroDeAprendizado):
    code = "transition_forbidden"


class ExigeODono(TransicaoProibida):
    """D1: o sistema tentou publicar (ou validar texto de pessoa) o que só o dono decide."""

    code = "owner_required"


class ConflitoDeEstado(ErroDeAprendizado):
    """A linha mudou entre a leitura e a escrita (outra sessão chegou antes), ou a chave já tem outro vivo."""

    code = "state_conflict"


class Vetado(ErroDeAprendizado):
    """O conteúdo foi desligado antes e o sistema não o traz de volta (a pessoa pode)."""

    code = "vetoed"


class UseARotaDasHabilidades(ErroDeAprendizado):
    """Habilidade tem ciclo próprio (mais estrito que o D1): o livro só a lê e aponta para a rota dela."""

    code = "use_skills_route"

    def __init__(self, message: str, href: str) -> None:
        super().__init__(message)
        self.href = href


class NotaComCaraDeSegredo(ErroDeAprendizado):
    """Texto de pessoa com formato ou assunto de credencial: não é gravado (segredo nunca no livro)."""

    code = "note_looks_secret"


# ------------------------------------------------------------------ regras
def exige_o_dono(side_effect: bool, human_origin: bool) -> bool:
    """`requires_owner` do D1. Derivado, nunca gravado."""
    return side_effect or human_origin


def permitido(frm: SkillState, to: SkillState, actor: Actor, *, side_effect: bool = False,
              human_origin: bool = False, modo_publica: bool = True) -> bool:
    """A mesma resposta de `conferir_transicao`, sem a mensagem (é o que o teste da tabela percorre)."""
    try:
        conferir_transicao(frm, to, SYSTEM_ACTOR if actor is Actor.SYSTEM else "painel", side_effect=side_effect,
                           human_origin=human_origin, modo_publica=modo_publica)
    except TransicaoProibida:
        return False
    return True


def conferir_transicao(frm: SkillState, to: SkillState, by: str, *, side_effect: bool, human_origin: bool,
                       modo_publica: bool) -> Actor:
    """Confere a tabela e o D1 e devolve quem está movendo. Recusa com a razão legível.

    `modo_publica`: o modo do tipo está em `on` (lição, tela...) — sem isso o sistema grava e mede, mas não publica.
    Para a pessoa o modo não importa: ela sempre pode publicar.
    """
    if not by.strip():
        raise TransicaoProibida("Toda transição precisa dizer quem decidiu.")
    if frm not in ESTADOS or to not in ESTADOS:
        raise TransicaoProibida(f"O livro de aprendizado não tem rascunho: {frm} → {to}.")
    actor = actor_of(by)
    quem = TRANSICOES.get((frm, to))
    if quem is None:
        raise TransicaoProibida(f"Transição proibida: {frm} → {to}.")
    if actor not in quem:
        raise TransicaoProibida(f"{frm} → {to} é decisão de uma pessoa, não do sistema.")
    if actor is Actor.SYSTEM and to is SkillState.PUBLISHED:
        if exige_o_dono(side_effect, human_origin):
            motivo = "tem efeito externo" if side_effect else "tem texto de pessoa"
            raise ExigeODono(f"O item {motivo}: publicar é decisão do dono (D1). Ele fica em 'validated', na fila "
                             "Para aprovar.")
        if not modo_publica:
            raise TransicaoProibida("O modo deste tipo não está em 'on': o sistema grava e mede, mas não publica.")
    if actor is Actor.SYSTEM and to is SkillState.VALIDATED and human_origin:
        raise ExigeODono("Texto de pessoa é validado pela pessoa (D1), não pela repetição.")
    return actor


def conferir_nascimento(estado: SkillState, by: str, *, side_effect: bool, human_origin: bool) -> None:
    """O D1 no item que já NASCE num estado: o sistema não cria direto o que não alcançaria por transição.

    Sem isto, `criar_item(by='sistema', estado=published)` pulava a tabela inteira — um minerador que chamasse o
    repositório publicaria com efeito ou com texto de pessoa sem nenhum `UPDATE` para recusar. As recusas são as
    mesmas de `conferir_transicao` (publicar o que exige o dono; validar texto de pessoa); o modo do tipo não entra
    aqui, porque quem confere o modo é o serviço, e o serviço só cria `candidate`.
    """
    if actor_of(by) is not Actor.SYSTEM:
        return
    if estado is SkillState.PUBLISHED and exige_o_dono(side_effect, human_origin):
        motivo = "tem efeito externo" if side_effect else "tem texto de pessoa"
        raise ExigeODono(f"O item {motivo}: nascer publicado é decisão do dono (D1); o sistema não publica.")
    if estado is SkillState.VALIDATED and human_origin:
        raise ExigeODono("Texto de pessoa nasce validado só pela pessoa (D1), não pelo sistema.")


@dataclass(frozen=True, slots=True)
class Desligamento:
    """Uma transição para `disabled` (ou `deprecated`) do mesmo conteúdo no mesmo escopo, lida da trilha."""

    to_state: SkillState
    decided_by: str
    decided_at: str
    app_version: str | None


def motivo_do_veto(historico: Sequence[Desligamento], *, agora: datetime, app_version: str | None,
                   dias_do_sistema: int = VETO_DO_SISTEMA_DIAS) -> str | None:
    """Por que o SISTEMA não pode recriar nem promover este conteúdo agora — ou `None` quando pode.

    `historico` vem na ORDEM DA TRILHA (a mais antiga primeiro; é o `id` da transição, não o relógio: duas decisões no
    mesmo milissegundo empatam no horário). Vale a mais recente: uma pessoa que reativou depois de desligar desfaz o
    veto dela; o sistema que desligou há mais de `dias_do_sistema` dias, ou numa versão do app que já não é a de
    agora, não trava mais nada. Só `disabled` veta (`deprecated` é aposentadoria, não refutação).
    """
    if not historico:
        return None
    ultimo = historico[-1]
    if ultimo.to_state is not SkillState.DISABLED:
        return None
    if actor_of(ultimo.decided_by) is Actor.PERSON:
        return f"desligado por uma pessoa ({ultimo.decided_by}) em {ultimo.decided_at[:10]}: só uma pessoa o reativa"
    try:
        quando = parse_iso(ultimo.decided_at)
    except ValueError:
        quando = None
    if quando is None:
        return None
    if app_version is not None and ultimo.app_version is not None and app_version != ultimo.app_version:
        return None
    if agora - quando >= timedelta(days=dias_do_sistema):
        return None
    return (f"desligado pelo sistema em {ultimo.decided_at[:10]}: vetado por {dias_do_sistema} dias ou até mudar a "
            "versão do app")

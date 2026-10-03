"""O parecer da IA diante da pessoa, a camada de APLICAÇÃO (item 30.17, `aprendizado-vivo.md` §8.8, §8.9 e §11.2): o
que o painel mostra de `learning_reviews`, o rótulo que cada decisão da pessoa deixa e o pedido de revisão.

Quem transiciona continua sendo o Livro (`LearningService.mudar_estado`, com o D1 e o veto); aqui só se decide o que
o parecer pode fazer (`domain/parecer.py`) e se grava a decisão:

- **Pelo `/status` (e pelas rotas legadas e pela evidência inválida do 30.23)**, a pessoa decide sozinha e o parecer
  nunca a trava. Depois da transição, a revisão válida e sem decisão sobre o estado de antes recebe o rótulo: com o
  parecer à vista (o `review_id` veio, ou o curador está em `on`), `aceitou`/`recusou`; oculto (`shadow`, `off`, rota
  legada), o rótulo da própria ação, às cegas. O modo decide, e não só o `review_id`: em `on` o painel mostra o parecer
  em toda parte, e uma decisão que o viu gravada como cega inflaria a concordância que tira o curador do `shadow`. É
  ACESSÓRIO: falhar aqui não desfaz a decisão da pessoa (fica no log).
- **O gesto sobre o parecer** (`responder`: aceitar ou recusar, um ou em lote) confere a classe mais restritiva entre a
  do registro e a de agora (`conferir_gesto`) e grava a decisão ANTES da transição, na mesma transação: o CAS de
  `decisao_final` recusa o segundo gesto antes de qualquer coisa mudar, e a transição recusada desfaz a decisão.
- **O pedido de revisão** é um sinal (`pediu_revisao`), que o laço do curador lê como o gatilho `pedido_da_pessoa`: só
  pula o cooldown; o orçamento, o modo e a regra "uma revisão por (item, dossiê)" continuam valendo.

Toda decisão gravada deixa também o sinal `parecer_decidido`: `learning_reviews` não tem coluna para a data da decisão,
e a métrica do §8.9 precisa dela.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.modules.learning.application.curador import KINDS_REVISADOS
from app.modules.learning.application.ports import FonteDeDossies, NovoSinal, RegistroDePareceres, TriagemDeTexto
from app.modules.learning.domain.ciclo import (SYSTEM_ACTOR, ConflitoDeEstado, EntradaInvalida, NaoEncontrado,
                                               NotaComCaraDeSegredo, SkillState, caminho_da_pessoa)
from app.modules.learning.domain.evidencia_invalida import motivo_de_evidencia_invalida
from app.modules.learning.domain.curador import Decisao
from app.modules.learning.domain.livro import (ROTULO_DA_CONFIRMACAO, AcaoPermitida, EntradaDoLivro, acoes_da_pessoa,
                                               rotulo_do_passo)
from app.modules.learning.domain.parecer import (DecisaoDaPessoa, DecisaoFinal, GestoRecusado, RecusaDoGesto,
                                                 RevisaoGravada, acao_do_aceite, conferir_gesto,
                                                 decisao_pela_transicao, mais_restritiva, parecer_visivel)
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco
from app.modules.learning.domain.vocabulario import LivroKind, Modo, Polaridade, SignalKind

log = logging.getLogger("poc.aprendizado")

#: Quantas revisões o detalhe mostra (a mais recente primeiro); o resto fica no banco, nunca purgado.
REVISOES_NO_DETALHE = 5
#: O motivo do override, como o da trilha.
MOTIVO_MAX = 500


class LivroDosPareceres(Protocol):
    """O que os pareceres usam do serviço do Livro. O `LearningService` o cumpre (tipagem estrutural)."""

    def entrada(self, kind: LivroKind, ref: str) -> EntradaDoLivro: ...
    def mudar_estado(self, kind: LivroKind, ref: str, para: SkillState, *, by: str, reason: str) -> EntradaDoLivro: ...
    def mudar_status_nativo(self, kind: LivroKind, ref: str, status: str, *, by: str,
                            reason: str) -> EntradaDoLivro: ...
    def invalidar_evidencia(self, kind: LivroKind, ref: str, run_id: str, *, by: str) -> EntradaDoLivro: ...
    def em_revisar(self, e: EntradaDoLivro) -> bool: ...
    def confirmar_que_fica(self, kind: LivroKind, ref: str, *, by: str, motivo: str | None = None) -> EntradaDoLivro: ...
    def registrar_sinal(self, sinal: NovoSinal) -> int | None: ...


@dataclass(frozen=True, slots=True)
class PareceresDoItem:
    """O bloco "IA" do detalhe. `pendente`: o parecer que uma decisão de agora responde, só quando aparece (`on`);
    `acao`: o passo que aceitá-lo dá (`None` = aceitar é concordar); `recusa`: por que o gesto não vale (simulado,
    classe A...), `None` quando vale. `ocultos`: os pendentes que o modo esconde (aparecem depois da decisão)."""

    modo: Modo
    revisoes: tuple[RevisaoGravada, ...]
    pendente: RevisaoGravada | None = None
    acao: AcaoPermitida | None = None
    recusa: str | None = None
    ocultos: int = 0
    pode_pedir: bool = False


@dataclass(frozen=True, slots=True)
class ParecerNaFila:
    """O parecer pendente de um item da fila (`on`). `recusa_no_lote`: por que ele não entra no aceite em lote."""

    revisao: RevisaoGravada
    acao: AcaoPermitida | None
    recusa: str | None
    recusa_no_lote: str | None


@dataclass(frozen=True, slots=True)
class RespostaAoPedido:
    """`registrado`: o pedido entrou (o curador o pega na próxima volta, dentro do orçamento). `revisao`: o estado de
    agora do item JÁ tem revisão (de qualquer validade); um pedido novo não faria outra até o item mudar."""

    registrado: bool
    revisao: RevisaoGravada | None = None


class ServicoDePareceres:
    def __init__(self, livro: LivroDosPareceres, registro: RegistroDePareceres, dossies: FonteDeDossies,
                 triagem: TriagemDeTexto, *, modo: Callable[[], Modo]) -> None:
        self._livro = livro
        self._registro = registro
        self._dossies = dossies
        self._triagem = triagem
        self._modo = modo

    @property
    def modo(self) -> Modo:
        return self._modo()

    # ================================================================== leitura
    def do_item(self, e: EntradaDoLivro) -> PareceresDoItem:
        modo = self._modo()
        todas = self._registro.do_item(e.trail_ref, REVISOES_NO_DETALHE)
        estado = e.state.value if e.state is not None else None
        visiveis = tuple(r for r in todas if parecer_visivel(modo, decidido=r.decidida))
        ocultos = sum(1 for r in todas if not parecer_visivel(modo, decidido=r.decidida) and r.pendente_em(estado))
        pendente = next((r for r in visiveis if r.pendente_em(estado)), None)
        acao, recusa = (None, None)
        if pendente is not None and pendente.parecer is not None:
            acao = acao_do_aceite(pendente.parecer.decisao, acoes_da_pessoa(e))
            recusa = conferir_gesto(pendente, estado_atual=estado, modo=modo,
                                    classe=pendente.classe_efetiva or ClasseDeRisco.C, em_lote=False)
        return PareceresDoItem(modo=modo, revisoes=visiveis, pendente=pendente, acao=acao, recusa=recusa,
                               ocultos=ocultos, pode_pedir=modo is Modo.ON and e.kind in KINDS_REVISADOS)

    def na_fila(self, entradas: Sequence[EntradaDoLivro]) -> dict[str, ParecerNaFila]:
        """O parecer pendente de cada item da fila, pela ref da trilha. Fora do `on`, nada (e nada é lido)."""
        modo = self._modo()
        if modo is not Modo.ON:
            return {}
        por_ref = {e.trail_ref: e for e in entradas if e.state is not None}
        saida: dict[str, ParecerNaFila] = {}
        for ref, revisoes in self._registro.sem_decisao(sorted(por_ref)).items():
            e = por_ref.get(ref)
            if e is None or e.state is None:
                continue
            r = next((x for x in revisoes if x.pendente_em(e.state.value)), None)
            if r is None or r.parecer is None:
                continue
            classe = r.classe_efetiva or ClasseDeRisco.C
            saida[ref] = ParecerNaFila(
                revisao=r, acao=acao_do_aceite(r.parecer.decisao, acoes_da_pessoa(e)),
                recusa=conferir_gesto(r, estado_atual=e.state.value, modo=modo, classe=classe, em_lote=False),
                recusa_no_lote=conferir_gesto(r, estado_atual=e.state.value, modo=modo, classe=classe, em_lote=True))
        return saida

    # ================================================================== a pessoa decide pelo item (o rótulo)
    def mudar_estado(self, kind: LivroKind, ref: str, para: SkillState, *, by: str, reason: str,
                     review_id: str | None = None) -> EntradaDoLivro:
        """O `/status`: a transição é a da pessoa, com ou sem parecer; depois, o rótulo (acessório)."""
        antes = self._livro.entrada(kind, ref)
        marca = self._registro.ultima_transicao(antes.trail_ref)
        depois = self._livro.mudar_estado(kind, ref, para, by=by, reason=reason)
        if antes.state is not None:
            self._rotular(antes, rotulo_do_passo(antes.state, para), by=by, reason=reason, review_id=review_id,
                          marca=marca, viu=review_id is not None or self._modo() is Modo.ON)
        return depois

    def mudar_status_nativo(self, kind: LivroKind, ref: str, status: str, *, by: str, reason: str) -> EntradaDoLivro:
        """As rotas legadas (`PUT /api/flows|recipes/{id}`): o rótulo é o do PRIMEIRO passo (a decisão sobre o estado
        que o parecer viu), às cegas: a página delas não mostra parecer."""
        antes = self._livro.entrada(kind, ref)
        marca = self._registro.ultima_transicao(antes.trail_ref)
        depois = self._livro.mudar_status_nativo(kind, ref, status, by=by, reason=reason)
        if antes.state is not None and depois.state is not None and depois.state is not antes.state:
            passos = caminho_da_pessoa(antes.state, depois.state)
            if passos:
                self._rotular(antes, rotulo_do_passo(antes.state, passos[0]), by=by, reason=reason, review_id=None,
                              marca=marca, viu=False)
        return depois

    def invalidar_evidencia(self, kind: LivroKind, ref: str, run_id: str, *, by: str) -> EntradaDoLivro:
        """A evidência inválida (30.23) é decisão de pessoa como o `/status`: desligar o vivo rotula o parecer pendente
        do estado de antes, visto em `on` (o detalhe mostra o parecer ao lado do botão) e às cegas fora dele.
        Reclassificar o já desligado não muda o estado e não rotula: o parecer de `disabled` segue pendente."""
        antes = self._livro.entrada(kind, ref)
        marca = self._registro.ultima_transicao(antes.trail_ref)
        depois = self._livro.invalidar_evidencia(kind, ref, run_id, by=by)
        if antes.state is not None and depois.state is not None and depois.state is not antes.state:
            self._rotular(antes, rotulo_do_passo(antes.state, depois.state), by=by,
                          reason=motivo_de_evidencia_invalida(run_id.strip()), review_id=None, marca=marca,
                          viu=self._modo() is Modo.ON)
        return depois

    def confirmar_que_fica(self, kind: LivroKind, ref: str, *, by: str, motivo: str | None = None,
                           review_id: str | None = None) -> EntradaDoLivro:
        """"Confirmar que fica" (30.24) é decisão de pessoa como o `/status`: o parecer pendente do publicado ganha o
        rótulo `confirmar`, que concorda com manter, observar e pedir evidência e recusa desativar e rebaixar."""
        antes = self._livro.entrada(kind, ref)
        marca = self._registro.ultima_transicao(antes.trail_ref)
        depois = self._livro.confirmar_que_fica(kind, ref, by=by, motivo=motivo)
        self._rotular(antes, ROTULO_DA_CONFIRMACAO, by=by, reason=motivo or "", review_id=review_id, marca=marca,
                      viu=review_id is not None or self._modo() is Modo.ON)
        return depois

    def _rotular(self, antes: EntradaDoLivro, rotulo: str | None, *, by: str, reason: str, review_id: str | None,
                 marca: int, viu: bool) -> None:
        if rotulo is None or antes.state is None or by == SYSTEM_ACTOR:
            return
        try:
            estado = antes.state.value
            candidatas = self._registro.sem_decisao([antes.trail_ref]).get(antes.trail_ref, [])
            if review_id is not None:
                r = next((c for c in candidatas if c.id == review_id and c.pendente_em(estado)), None)
                if r is None:
                    # A tela tinha um parecer que já não vale (decidido, outro estado): a decisão fica, sem rótulo.
                    log.info("aprendizado: review_id %s não responde ao estado %s de %s; decisão sem rótulo",
                             review_id, estado, antes.trail_ref)
                    return
            else:
                r = next((c for c in candidatas if c.pendente_em(estado)), None)
                if r is None:
                    return
            if r.parecer is None:
                return
            d = decisao_pela_transicao(r.parecer.decisao, rotulo, viu=viu)
            motivo = self._motivo_ou_nulo(reason) if d.override and viu else None
            with self._registro.transacao():
                transicao = self._registro.transicao_depois(antes.trail_ref, marca)
                if self._registro.decidir(r.id, decisao_final=d.decisao_final, decidido_por=by,
                                          transicao_id=transicao, override=d.override, override_motivo=motivo):
                    self._sinal(r, d, by=by, viu=viu, app=antes.app)
        except Exception:  # noqa: BLE001 - o rótulo é acessório: a decisão da pessoa já está gravada
            log.exception("aprendizado: rótulo do parecer de %s", antes.trail_ref)

    # ================================================================== o gesto sobre o parecer
    def responder(self, kind: LivroKind, ref: str, review_id: str, *, aceitar: bool, motivo: str, by: str,
                  em_lote: bool = False) -> EntradaDoLivro:
        """Aceitar (o passo do lado sugerido, ou só concordar) ou recusar (override com motivo). 409 com a razão quando
        o gesto não vale; 404 quando a revisão não é deste item."""
        texto = motivo.strip()
        if not texto:
            raise EntradaInvalida("Diga o motivo: aceitar ou recusar o parecer fica na trilha e no registro.")
        if not aceitar and self._triagem.recusa(texto):
            raise NotaComCaraDeSegredo("O motivo tem formato ou assunto de credencial e não foi gravado.")
        modo = self._modo()
        with self._registro.transacao():
            e = self._livro.entrada(kind, ref)
            r = self._registro.uma(review_id)
            if r is None or r.item_ref != e.trail_ref:
                raise RevisaoDeOutroItem(review_id, e)
            estado = e.state.value if e.state is not None else None
            recusa = conferir_gesto(r, estado_atual=estado, modo=modo, classe=self._classe_de_agora(r, e),
                                    em_lote=em_lote)
            if recusa is not None:
                raise GestoRecusado(recusa)
            assert r.parecer is not None                    # `conferir_gesto` já recusou o inválido
            acao = acao_do_aceite(r.parecer.decisao, acoes_da_pessoa(e)) if aceitar else None
            # A6 (30.24): aceitar "manter" num item de "Revisar" é confirmar que ele fica; fora dela, só concordar.
            manter = aceitar and acao is None and r.parecer.decisao is Decisao.MANTER and self._livro.em_revisar(e)
            d = DecisaoDaPessoa(DecisaoFinal.ACEITOU.value if aceitar else DecisaoFinal.RECUSOU.value,
                                override=not aceitar)
            # A decisão ANTES da transição: o segundo gesto perde aqui, antes de qualquer coisa mudar.
            if not self._registro.decidir(r.id, decisao_final=d.decisao_final, decidido_por=by, transicao_id=None,
                                          override=d.override, override_motivo=None if aceitar else texto[:MOTIVO_MAX]):
                raise GestoRecusado(RecusaDoGesto.JA_DECIDIDO)
            if acao is not None or manter:
                marca = self._registro.ultima_transicao(e.trail_ref)
                e = (self._livro.mudar_estado(kind, ref, acao.to, by=by, reason=texto) if acao is not None else
                     self._livro.confirmar_que_fica(kind, ref, by=by, motivo=texto))
                transicao = self._registro.transicao_depois(e.trail_ref, marca)
                self._registro.decidir_transicao(r.id, transicao)
            self._sinal(r, d, by=by, viu=True, app=e.app)
        return e

    def _classe_de_agora(self, r: RevisaoGravada, e: EntradaDoLivro) -> ClasseDeRisco:
        """A mais restritiva entre a do registro (endurecida pela IA) e a do dossiê de agora: um catálogo que mudou
        depois do parecer endurece, nunca afrouxa. Sem dossiê de agora, C (sem saber, item a item)."""
        d = self._dossies.dossie(e)
        return mais_restritiva(r.classe_efetiva, d.classe if d is not None else ClasseDeRisco.C)

    # ================================================================== o pedido de revisão
    def pedir_revisao(self, kind: LivroKind, ref: str, *, by: str) -> RespostaAoPedido:
        e = self._livro.entrada(kind, ref)
        if e.kind not in KINDS_REVISADOS:
            raise EntradaInvalida(f"{kind.value} não passa pelo curador: não há revisão a pedir.")
        if self._modo() is not Modo.ON:
            raise GestoRecusado("curador_fora_do_on")
        d = self._dossies.dossie(e)
        if d is None:
            raise ConflitoDeEstado(f"Não há como montar o dossiê de {kind.value} {ref} agora.")
        ja = self._registro.do_dossie(e.trail_ref, d.dossie_hash)
        if ja is not None:
            return RespostaAoPedido(registrado=False, revisao=ja)
        self._livro.registrar_sinal(NovoSinal(
            kind=SignalKind.PEDIU_REVISAO, source_ref=f"pedido_de_revisao:{e.trail_ref}@{d.dossie_hash}",
            created_by=by, polarity=Polaridade.NEUTRAL, app_package=e.app or "",
            data={"kind": e.kind.value, "ref": e.ref, "item_ref": e.trail_ref, "dossie_hash": d.dossie_hash}))
        return RespostaAoPedido(registrado=True)

    # ================================================================== apoio
    def _motivo_ou_nulo(self, texto: str) -> str | None:
        """O motivo da trilha como motivo do override; com cara de credencial, não entra (a trilha já o tem)."""
        t = texto.strip()
        return None if not t or self._triagem.recusa(t) else t[:MOTIVO_MAX]

    def _sinal(self, r: RevisaoGravada, d: DecisaoDaPessoa, *, by: str, viu: bool, app: str | None) -> None:
        self._livro.registrar_sinal(NovoSinal(
            kind=SignalKind.PARECER_DECIDIDO, source_ref=f"parecer:{r.id}", created_by=by,
            polarity=Polaridade.NEUTRAL, app_package=app or "",
            data={"review_id": r.id, "item_ref": r.item_ref, "decisao_final": d.decisao_final,
                  "override": d.override, "viu": viu}))


class RevisaoDeOutroItem(NaoEncontrado):
    """A revisão não existe ou é de outro item: o `review_id` não serve para este (404)."""

    code = "review_not_found"

    def __init__(self, review_id: str, e: EntradaDoLivro) -> None:
        super().__init__(f"A revisão '{review_id}' não é de {e.kind.value} {e.ref}.")


__all__ = ["MOTIVO_MAX", "REVISOES_NO_DETALHE", "LivroDosPareceres", "ParecerNaFila", "PareceresDoItem",
           "RespostaAoPedido", "RevisaoDeOutroItem", "ServicoDePareceres"]

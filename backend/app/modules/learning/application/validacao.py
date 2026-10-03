"""A validação automática do "pedir evidência" do curador (item 30.31), a parte de APLICAÇÃO. As regras puras estão em
`domain/validacao.py`; aqui ficam as três entradas do laço, todas sem IA própria (a execução de validação é uma
execução comum, e o custo dela é o da operação):

- `ao_parecer`: o curador acabou de gravar um parecer válido; se ele pede evidência que uma execução produz, nasce o
  pedido (ou o registro de por que não nasceu);
- `uma_volta`: o despachante, sob a trava de líder, expira os pedidos velhos e, se o central está quieto, há fôlego
  na janela e um aparelho ocioso serve, enfileira UMA execução de validação (o comando de origem, noutro aparelho);
- `minerar` (minerador do digest): quando a execução de validação assenta, o pedido fecha — `feita` se o item ganhou
  evidência dela, `recusada` com o motivo se não — e o curador revê o item com o gatilho `evidencia_chegou`.

Nada daqui transiciona o item: a evidência entra pelos caminhos de sempre (a sombra do fluxo, os contadores da
receita) e quem decide continua sendo o ciclo do livro. `modo=off` de fábrica: nada nasce, nada roda.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.modules.learning.domain.curador import Parecer
from app.modules.learning.domain.livro import EntradaDoLivro, apps_do_item
from app.modules.learning.domain.politica_de_risco import Classificacao, Razao
from app.modules.learning.domain.validacao import (VALIDADE_DO_PEDIDO_H, Ambiente, AparelhoCandidato, EstadoDoPedido,
                                                   FatosDoParecer, Folego, Grupo, Motivo, escolher_aparelho,
                                                   pedido_do_parecer, pode_despachar)
from app.modules.learning.domain.vocabulario import LivroKind, Modo
from app.util import parse_iso, to_iso

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AjustesDaValidacao:
    modo: Modo = Modo.OFF
    beta: float = 0.05
    maximo_por_hora: int = 4
    intervalo_s: int = 600
    janela_dias: int = 7
    #: A verba única (P4 do desenho): US$ somados ao β até `extra_ate` (ISO; vazio = sem verba).
    extra_usd: float = 0.0
    extra_ate: str = ""
    #: O custo estimado de uma execução de validação antes de haver medida (mediana das execuções reais do QA, 03/10).
    custo_estimado_usd: float = 0.07


@dataclass(frozen=True, slots=True)
class NovoPedido:
    review_id: str
    item_ref: str
    item_kind: str
    scope_app: str
    grupo: str
    falta: tuple[str, ...]
    run_origem: str | None
    comando: str
    aparelho_excluido: str | None
    estado: str
    motivo: str | None
    expira_em: str


@dataclass(frozen=True, slots=True)
class PedidoVivo:
    id: str
    item_ref: str
    item_kind: str
    scope_app: str
    grupo: Grupo
    comando: str
    aparelho_excluido: str | None
    estado: EstadoDoPedido
    run_id: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class Origem:
    """O que a execução que ensinou o item diz: o comando como foi pedido e o aparelho em que rodou."""

    comando: str
    aparelho: str | None


class RegistroDeValidacoes(Protocol):
    """`learning_validations` (082)."""

    def criar(self, novo: NovoPedido, agora: datetime) -> str | None: ...     # None = já há pedido vivo do item
    def pendentes(self) -> list[PedidoVivo]: ...
    def por_execucao(self, run_id: str) -> PedidoVivo | None: ...
    def comecar(self, pedido_id: str, run_id: str, aparelho: str, agora: datetime) -> bool: ...
    def fechar(self, pedido_id: str, estado: EstadoDoPedido, motivo: Motivo | None, usd: float,
               agora: datetime) -> bool: ...
    def expirar(self, agora: datetime) -> int: ...
    def gasto_desde(self, desde: datetime) -> float: ...
    def comecados_desde(self, desde: datetime) -> int: ...
    def chegadas(self) -> list[PedidoVivo]: ...          # `feita` sem a revisão nova
    def revisado(self, pedido_id: str, review_id: str) -> None: ...


class FontesDaValidacao(Protocol):
    """O que o pedido lê do mundo (só leitura)."""

    def origem(self, run_id: str) -> Origem | None: ...
    def app_de_qa(self, pacote: str | None) -> bool: ...
    def apps_do_item(self, item_ref: str) -> tuple[str, ...]: ...     # os pacotes exigidos (fluxo; 30.33-C)
    def fluxo_ativo_para(self, comando: str) -> bool: ...
    def vetado(self, e: EntradaDoLivro) -> bool: ...
    def evidencia_da_execucao(self, item_ref: str, run_id: str) -> bool: ...
    def desfecho(self, run_id: str) -> tuple[str, float] | None: ...    # (status, usd) quando assentou


class DespachoDeValidacao(Protocol):
    """O parque e a fila de execuções (o lado do `taskqueue`)."""

    def ambiente(self) -> Ambiente: ...
    def aparelhos(self, pacotes: Sequence[str]) -> Sequence[AparelhoCandidato]: ...  # com TODOS prontos
    def gasto_da_operacao(self, agora: datetime, dias: int) -> float: ...
    def enfileirar(self, comando: str, aparelho: str, chave: str) -> str: ...  # o run_id


class ServicoDeValidacao:
    nome = "validacao"

    def __init__(self, registro: RegistroDeValidacoes, fontes: FontesDaValidacao, despacho: DespachoDeValidacao,
                 *, triagem: Callable[[str], bool], ajustes: Callable[[], AjustesDaValidacao],
                 relogio: Callable[[], datetime]) -> None:
        self._registro = registro
        self._fontes = fontes
        self._despacho = despacho
        self._triagem = triagem
        self._ajustes = ajustes
        self._relogio = relogio

    # ------------------------------------------------------------------ 1. o parecer vira pedido
    def ao_parecer(self, e: EntradaDoLivro, review_id: str, parecer: Parecer, risco: Classificacao) -> str | None:
        """Chamado pelo curador depois de gravar um parecer válido. Devolve o id do pedido gravado (vivo ou recusado),
        ou `None` quando não há o que pedir, o modo está `off`, ou o item já tem pedido vivo."""
        if self._ajustes().modo is Modo.OFF:
            return None
        origem = self._fontes.origem(e.nasceu_de) if e.nasceu_de else None
        comando = origem.comando if origem is not None else None
        fatos = FatosDoParecer(
            decisao=parecer.decisao, falta=tuple(parecer.falta), kind=e.kind, estado=e.state,
            vetado=self._fontes.vetado(e), toca_sessao=Razao.SESSAO_OU_AUTENTICACAO in risco.razoes,
            efeito=e.side_effect, app_qa=self._todos_de_qa(e), comando=comando,
            comando_com_credencial=bool(comando) and self._triagem(comando or ""),
            fluxo_ativo=bool(comando) and e.kind is LivroKind.RECEITA and self._fontes.fluxo_ativo_para(comando or ""))
        pedido = pedido_do_parecer(fatos)
        if pedido is None:
            return None
        agora = self._relogio()
        return self._registro.criar(NovoPedido(
            review_id=review_id, item_ref=e.trail_ref, item_kind=e.kind.value, scope_app=e.app or "",
            grupo=pedido.grupo.value, falta=tuple(f.value for f in pedido.falta), run_origem=e.nasceu_de,
            comando=comando or "", aparelho_excluido=origem.aparelho if origem is not None else None,
            estado=pedido.estado.value, motivo=pedido.motivo.value if pedido.motivo else None,
            expira_em=to_iso(agora + timedelta(hours=VALIDADE_DO_PEDIDO_H))), agora)

    def _todos_de_qa(self, e: EntradaDoLivro) -> bool:
        """O item é de QA só se TODOS os apps dele forem (30.33-C, a regra "mais restritivo" do dono): um fluxo que
        passa pelo QA Messenger e pelo Instagram não é QA. Sem app, não é."""
        apps = apps_do_item(e)
        return bool(apps) and all(self._fontes.app_de_qa(a) for a in apps)

    # ------------------------------------------------------------------ 2. o despachante
    @property
    def intervalo_s(self) -> int:
        return max(60, int(self._ajustes().intervalo_s))

    def uma_volta(self, lider: Callable[[], int | None]) -> str | None:
        """Uma execução de validação por volta, no máximo. Devolve o `run_id` enfileirado, ou `None` (com o motivo no
        log: o pedido continua pendente e tenta na volta seguinte)."""
        aj = self._ajustes()
        if aj.modo is Modo.OFF or lider() is None:
            return None
        agora = self._relogio()
        self._registro.expirar(agora)
        pendentes = self._registro.pendentes()
        if not pendentes:
            return None
        ate = parse_iso(aj.extra_ate)                           # sem fuso = UTC (`parse_iso`)
        extra = aj.extra_usd if ate is not None and agora <= ate else 0.0
        folego = Folego(g_w=self._despacho.gasto_da_operacao(agora, aj.janela_dias),
                        gasto_w=self._registro.gasto_desde(agora - timedelta(days=aj.janela_dias)),
                        na_ultima_hora=self._registro.comecados_desde(agora - timedelta(hours=1)),
                        beta=aj.beta, extra_usd=extra, maximo_por_hora=aj.maximo_por_hora)
        motivo = pode_despachar(self._despacho.ambiente(), folego, custo_estimado=aj.custo_estimado_usd)
        if motivo is not None:
            log.info("aprendizado: validação espera (%s; %d pedido(s) pendente(s))", motivo.value, len(pendentes))
            return None
        for p in pendentes:                                      # o mais antigo que tiver aparelho
            # 30.33-C: o aparelho precisa de TODOS os apps do item prontos, não só do principal (`scope_app`).
            pacotes = tuple(dict.fromkeys(a for a in (p.scope_app, *self._fontes.apps_do_item(p.item_ref)) if a))
            aparelho = escolher_aparelho(p.grupo, self._despacho.aparelhos(pacotes), excluido=p.aparelho_excluido)
            if aparelho is None:
                continue
            run_id = self._despacho.enfileirar(p.comando, aparelho, chave=f"validacao:{p.id}")
            if self._registro.comecar(p.id, run_id, aparelho, agora):
                log.info("aprendizado: validação %s de %s em %s (execução %s)", p.id, p.item_ref, aparelho, run_id)
                return run_id
        log.info("aprendizado: validação espera (%s; %d pedido(s) pendente(s))", Motivo.SEM_APARELHO.value,
                 len(pendentes))
        return None

    # ------------------------------------------------------------------ 3. o minerador do digest
    def minerar(self, run_id: str) -> int:
        """A execução assentou: se é de validação, o pedido fecha. `1` quando fechou um pedido."""
        p = self._registro.por_execucao(run_id)
        if p is None or p.estado is not EstadoDoPedido.RODANDO:
            return 0
        desfecho = self._fontes.desfecho(run_id)
        if desfecho is None:
            return 0                                             # ainda não assentou de verdade
        status, usd = desfecho
        if self._fontes.evidencia_da_execucao(p.item_ref, run_id):
            estado, motivo = EstadoDoPedido.FEITA, None
        else:
            estado = EstadoDoPedido.RECUSADA
            motivo = Motivo.SEM_EVIDENCIA if status.startswith("completed") else Motivo.EXECUCAO_FALHOU
        return int(self._registro.fechar(p.id, estado, motivo, usd, self._relogio()))

    # ------------------------------------------------------------------ 4. o gatilho `evidencia_chegou` do curador
    def chegadas(self) -> list[PedidoVivo]:
        return self._registro.chegadas() if self._ajustes().modo is not Modo.OFF else []

    def revisado(self, pedido_id: str, review_id: str) -> None:
        self._registro.revisado(pedido_id, review_id)


__all__ = ["AjustesDaValidacao", "DespachoDeValidacao", "FontesDaValidacao", "NovoPedido", "Origem", "PedidoVivo",
           "RegistroDeValidacoes", "ServicoDeValidacao"]

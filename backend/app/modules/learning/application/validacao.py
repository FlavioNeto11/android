"""A validação automática do "pedir evidência" do curador (item 30.31), a parte de APLICAÇÃO. As regras puras estão em
`domain/validacao.py`; aqui ficam as três entradas do laço, todas sem IA própria (a execução de validação é uma
execução comum, e o custo dela é o da operação):

- `ao_parecer`: o curador acabou de gravar um parecer válido; se ele pede evidência que uma execução produz, nasce o
  pedido (ou o registro de por que não nasceu);
- `uma_volta`: o despachante, sob a trava de líder, expira os pedidos velhos e, se o central está quieto, há fôlego
  na janela e um aparelho ocioso serve, enfileira UMA execução de validação (o comando de origem, noutro aparelho);
- `minerar` (minerador do digest): quando a execução de validação assenta, o pedido fecha pela evidência que ela
  deixou — `feita` (a favor), `recusada/evidencia_contra`, `recusada/divergencia_de_forma` (30.36) ou
  `recusada/sem_evidencia` — e o curador revê o item com o gatilho `evidencia_chegou` (a favor e contra);
- `executar` (passo da curadoria, 30.36): o pedido fechado `sem_evidencia` cuja execução ganhou evidência depois (a
  reclassificação da sombra) passa ao motivo dela. Sem IA e idempotente. Com a validação ligada, reabre (30.37) o
  pedido de FLUXO fechado antes da execução de prova.

Receita sem caminho (30.36): o pedido da receita cujo plano do fluxo ativo não chega à etapa dela nasce, ou fecha ao
despachar, `recusada/sem_caminho`, sem execução; o curador volta ao item com a marca "variante sem caminho".

Fluxo (30.37): a execução de validação é uma EXECUÇÃO DE PROVA (roda o plano do próprio fluxo, `prova=<fluxo>`), com teto
de gasto por pedido (`teto_usd`); o fluxo cujo molde não cabe no comando de origem fecha `sem_caminho` como a receita.

Nada daqui transiciona o item: a evidência entra pelos caminhos de sempre (a sombra do fluxo, os contadores da
receita) e quem decide continua sendo o ciclo do livro. `modo=off` de fábrica: nada nasce, nada roda.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol

from app.contracts.origem import PREFIXO_VALIDACAO
from app.modules.learning.domain.curador import Falta, Parecer
from app.modules.learning.domain.livro import EntradaDoLivro, apps_do_item
from app.modules.learning.domain.politica_de_risco import Classificacao, Razao
from app.modules.learning.domain.validacao import (VALIDADE_DO_PEDIDO_H, Ambiente, AparelhoCandidato, EstadoDoPedido,
                                                   FatosDoParecer, Folego, Grupo, Motivo, ProvaAnterior,
                                                   escolher_aparelho, excluidos_da_validacao,
                                                   limite_de_provas_atingido, motivo_da_prova_invalida,
                                                   pedido_do_parecer, pode_despachar, sobra_aparelho_novo,
                                                   teto_da_prova)
from app.modules.learning.domain.vocabulario import LivroKind, Modo, Posicao
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
    #: 30.37: o teto de gasto de IA de UM pedido; vai à coluna `teto_usd` e o roteador barra a chamada além dele.
    teto_por_pedido_usd: float = 0.10


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
    teto_usd: float | None = None


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
    #: 30.42: os rótulos da falta do pedido (`Falta.value`); o despachante lê `reproducao_em_outro_aparelho` daqui.
    falta: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Origem:
    """O que a execução que ensinou o item diz: o comando como foi pedido e o aparelho em que rodou."""

    comando: str
    aparelho: str | None


#: 30.38 (b): o comando vai ao painel cortado aqui (o resto fica na execução de origem).
COMANDO_NA_LISTA = 200
#: A página da listagem, no máximo.
LISTA_MAX = 200


@dataclass(frozen=True, slots=True)
class PedidoListado:
    """Um pedido como o painel (Aprendizado › Validação) e o estudo 32.1 o leem. Só leitura; `comando` já cortado em
    `COMANDO_NA_LISTA`."""

    id: str
    estado: str
    motivo: str | None
    item_ref: str
    item_kind: str
    scope_app: str
    grupo: str
    run_id: str | None
    run_origem: str | None
    aparelho: str | None
    usd: float
    teto_usd: float | None
    created_at: str
    feito_em: str | None
    expira_em: str
    revisao_nova_id: str | None
    comando: str


class RegistroDeValidacoes(Protocol):
    """`learning_validations` (082)."""

    def criar(self, novo: NovoPedido, agora: datetime) -> str | None: ...     # None = já há pedido vivo do item
    def pendentes(self) -> list[PedidoVivo]: ...
    def por_execucao(self, run_id: str) -> PedidoVivo | None: ...
    #: 30.40: `teto_usd` só preenche o pedido que não tem (o legado); o teto gravado não muda.
    def comecar(self, pedido_id: str, run_id: str, aparelho: str, agora: datetime,
                teto_usd: float | None = None, teto_da_prova: float | None = None) -> bool: ...
    def fechar(self, pedido_id: str, estado: EstadoDoPedido, motivo: Motivo | None, usd: float,
               agora: datetime) -> bool: ...
    def recusar(self, pedido_id: str, motivo: Motivo, agora: datetime) -> bool: ...  # `pendente` → `recusada`, sem execução
    def expirar(self, agora: datetime) -> int: ...
    def gasto_desde(self, desde: datetime) -> float: ...
    def comecados_desde(self, desde: datetime) -> int: ...
    #: `feita`, `recusada/evidencia_contra` e `recusada/sem_caminho` sem a revisão nova (30.36: as três respondem)
    def chegadas(self) -> list[PedidoVivo]: ...
    def revisado(self, pedido_id: str, review_id: str) -> None: ...
    def sem_evidencia(self) -> list[PedidoVivo]: ...    # `recusada/sem_evidencia` com execução (30.36)
    def remotivar(self, pedido_id: str, motivo: Motivo, agora: datetime) -> bool: ...  # só de `sem_evidencia`
    #: 30.37: o pedido de FLUXO fechado `sem_evidencia`/`divergencia_de_forma` cuja execução rodou ANTES da prova (sem
    #: `prova_fluxo_id`), com o fluxo ainda ligado e sem pedido posterior do mesmo item, devolvido como pedido novo
    #: (`pendente`, sem `expira_em` nem `teto_usd`: o serviço completa).
    def para_reabrir(self) -> list[NovoPedido]: ...
    #: 30.38 (b): a listagem, das mais novas para as mais antigas (`antes`: o `created_at` do último da página
    #: anterior), e a contagem por estado de TODOS os pedidos.
    def listar(self, estado: EstadoDoPedido | None, limite: int, antes: str | None, *, item: str | None = None,
               run: str | None = None) -> list[PedidoListado]: ...
    def contagens(self) -> dict[str, int]: ...


class FontesDaValidacao(Protocol):
    """O que o pedido lê do mundo (só leitura)."""

    def origem(self, run_id: str) -> Origem | None: ...
    def app_de_qa(self, pacote: str | None) -> bool: ...
    def apps_do_item(self, item_ref: str) -> tuple[str, ...]: ...     # os pacotes exigidos (fluxo; 30.33-C)
    def fluxo_ativo_para(self, comando: str) -> bool: ...
    def vetado(self, e: EntradaDoLivro) -> bool: ...
    #: 30.36: o plano do fluxo ATIVO do comando chega à etapa da receita? Sem fluxo ativo, `True` (quem recusa é o
    #: `sem_fluxo_ativo`); fora da receita, `True`.
    def caminho_da_receita(self, item_ref: str, comando: str) -> bool: ...
    #: 30.37: o comando de origem cabe no molde do FLUXO (a execução de prova roda o plano dele com os parâmetros do
    #: comando)? Fora do fluxo, `True`.
    def molde_do_fluxo(self, item_ref: str, comando: str) -> bool: ...
    #: A posição da evidência que DESTA execução ficou no item (30.36): a favor vence; depois a forma; depois o contra
    #: efetivo; `None` sem evidência dela. A receita só tem a favor (a tentativa conduzida por ela que deu certo).
    def posicao_da_execucao(self, item_ref: str, run_id: str) -> Posicao | None: ...
    #: 30.42: o `detail` da linha `invalida` que ESTA execução deixou no item (a prova que não vale: efeito repetido,
    #: ponto de partida, ator sem ação), ou `None` sem a linha. Quando há, VENCE a posição (`posicao_da_execucao`): o
    #: pedido fecha com o motivo dela (`domain.validacao.motivo_da_prova_invalida`). `""` = linha sem detalhe.
    def invalida_da_execucao(self, item_ref: str, run_id: str) -> str | None: ...
    #: 30.42: a marca `content_hash(plano do fluxo)[:12]` de agora (a mesma `[xxxxxxxxxxxx]` do `detail` das linhas da
    #: prova); `None` fora do fluxo ou sem plano legível.
    def versao_do_conteudo(self, item_ref: str) -> str | None: ...
    #: 30.42: as execuções de PROVA que o item já teve (pedido com `run_id` cuja execução tem `prova_fluxo_id`), com o
    #: aparelho e a marca de cada linha de evidência que deixaram. Só do fluxo; nos outros tipos, vazio.
    def provas_do_item(self, item_ref: str) -> list[ProvaAnterior]: ...
    #: 30.41: as etapas que a execução de validação rodaria, com o `for_each` expandido pelo tamanho da lista que a
    #: execução de origem do fluxo coletou. Fluxo: o plano do próprio fluxo com o comando (a prova); receita: o fluxo
    #: ATIVO do comando. `None`: sem plano (quem responde é o `sem_caminho` ou o `sem_fluxo_ativo`) ou `for_each` de
    #: tamanho desconhecido.
    def etapas_da_execucao(self, item_ref: str, comando: str) -> int | None: ...
    def desfecho(self, run_id: str) -> tuple[str, float] | None: ...    # (status, usd) quando assentou


class DespachoDeValidacao(Protocol):
    """O parque e a fila de execuções (o lado do `taskqueue`)."""

    def ambiente(self) -> Ambiente: ...
    def aparelhos(self, pacotes: Sequence[str]) -> Sequence[AparelhoCandidato]: ...  # com TODOS prontos
    def gasto_da_operacao(self, agora: datetime, dias: int) -> float: ...
    #: `prova` (30.37): o id do fluxo que a execução prova (o pedido de fluxo roda o plano do próprio fluxo).
    def enfileirar(self, comando: str, aparelho: str, chave: str, prova: str | None = None) -> str: ...  # o run_id


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
        aj = self._ajustes()
        if aj.modo is Modo.OFF:
            return None
        origem = self._fontes.origem(e.nasceu_de) if e.nasceu_de else None
        comando = origem.comando if origem is not None else None
        fatos = FatosDoParecer(
            decisao=parecer.decisao, falta=tuple(parecer.falta), kind=e.kind, estado=e.state,
            vetado=self._fontes.vetado(e), toca_sessao=Razao.SESSAO_OU_AUTENTICACAO in risco.razoes,
            efeito=e.side_effect, app_qa=self._todos_de_qa(e), comando=comando,
            comando_com_credencial=bool(comando) and self._triagem(comando or ""),
            fluxo_ativo=bool(comando) and e.kind is LivroKind.RECEITA and self._fontes.fluxo_ativo_para(comando or ""),
            caminho=self._caminho(e.kind.value, e.trail_ref, comando))
        pedido = pedido_do_parecer(fatos)
        if pedido is None:
            return None
        agora = self._relogio()
        return self._registro.criar(NovoPedido(
            review_id=review_id, item_ref=e.trail_ref, item_kind=e.kind.value, scope_app=e.app or "",
            grupo=pedido.grupo.value, falta=tuple(f.value for f in pedido.falta), run_origem=e.nasceu_de,
            comando=comando or "", aparelho_excluido=origem.aparelho if origem is not None else None,
            estado=pedido.estado.value, motivo=pedido.motivo.value if pedido.motivo else None,
            expira_em=to_iso(agora + timedelta(hours=VALIDADE_DO_PEDIDO_H)),
            teto_usd=aj.teto_por_pedido_usd), agora)

    def _caminho(self, kind: str, item_ref: str, comando: str | None) -> bool:
        """30.36/30.37: a execução de validação chega ao item? Receita: o plano do fluxo ativo passa pela etapa dela;
        fluxo: o comando de origem cabe no molde dele (a prova roda o plano do próprio fluxo); o resto, ou sem comando,
        `True` (quem recusa sem comando é o `sem_origem`)."""
        if not comando:
            return True
        if kind == LivroKind.RECEITA.value:
            return self._fontes.caminho_da_receita(item_ref, comando)
        if kind == LivroKind.FLUXO.value:
            return self._fontes.molde_do_fluxo(item_ref, comando)
        return True

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
        pendentes = self._sem_os_sem_caminho(self._registro.pendentes(), agora)
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
            aparelho = escolher_aparelho(p.grupo, self._despacho.aparelhos(self._pacotes(p)),
                                         excluido=self._excluidos(p))
            if aparelho is None:
                continue
            prova = p.item_ref.partition(":")[2] if p.item_kind == LivroKind.FLUXO.value else None
            chave = f"{PREFIXO_VALIDACAO}{p.id}"
            # `prova` só vai quando há (a receita segue com a chamada de antes).
            run_id = (self._despacho.enfileirar(p.comando, aparelho, chave, prova=prova) if prova
                      else self._despacho.enfileirar(p.comando, aparelho, chave))
            # 30.40: o pedido sem teto (legado) herda o da config aqui, sem depender de UPDATE à mão.
            # 30.41: a prova de fluxo leva o teto proporcional ao plano (vence o gravado); a receita, o fixo do 30.40.
            proporcional = (teto_da_prova(self._fontes.etapas_da_execucao(p.item_ref, p.comando)) if prova
                            else None)
            if self._registro.comecar(p.id, run_id, aparelho, agora, teto_usd=aj.teto_por_pedido_usd,
                                      teto_da_prova=proporcional):
                log.info("aprendizado: validação %s de %s em %s (execução %s)", p.id, p.item_ref, aparelho, run_id)
                return run_id
        log.info("aprendizado: validação espera (%s; %d pedido(s) pendente(s))", Motivo.SEM_APARELHO.value,
                 len(pendentes))
        return None

    def _pacotes(self, p: PedidoVivo) -> tuple[str, ...]:
        """30.33-C: o aparelho precisa de TODOS os apps do item prontos, não só do principal (`scope_app`)."""
        return tuple(dict.fromkeys(a for a in (p.scope_app, *self._fontes.apps_do_item(p.item_ref)) if a))

    def _excluidos(self, p: PedidoVivo) -> frozenset[str]:
        """30.42: a origem; e, com `reproducao_em_outro_aparelho` na falta, também os aparelhos das provas anteriores."""
        if Falta.REPRODUCAO_EM_OUTRO_APARELHO.value not in p.falta:
            return excluidos_da_validacao(p.falta, origem=p.aparelho_excluido, provas=())
        provas = (a.aparelho for a in self._fontes.provas_do_item(p.item_ref))
        return excluidos_da_validacao(p.falta, origem=p.aparelho_excluido, provas=provas)

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
        # A sombra do fluxo (`fluxos_d1`) é registrada antes deste minerador e o digest roda em ordem: a evidência
        # desta execução já está no item quando o pedido fecha.
        # 30.42: a linha `invalida` da execução VENCE (antes do `for`): a prova que repetiu o efeito, que não chegou ao
        # ponto de partida ou em que o ator não agiu não vale nem a favor nem contra. Esses motivos NÃO são chegada do
        # curador (`chegadas()`) nem se reabrem (`para_reabrir()`): fechar sem evidência de verdade é o ponto.
        invalida = self._fontes.invalida_da_execucao(p.item_ref, run_id)
        posicao = None if invalida is not None else self._fontes.posicao_da_execucao(p.item_ref, run_id)
        if invalida is not None:
            estado, motivo = EstadoDoPedido.RECUSADA, motivo_da_prova_invalida(invalida)
        elif posicao is Posicao.FOR:
            estado, motivo = EstadoDoPedido.FEITA, None
        else:
            estado = EstadoDoPedido.RECUSADA
            if posicao is not None:
                motivo = _MOTIVO_DA_POSICAO[posicao]
            elif p.item_kind == LivroKind.FLUXO.value:
                # 30.37 (ajuste c): a prova de fluxo só deixa evidência a favor ou contra DO FLUXO; o que não é nenhuma
                # das duas é infra (aparelho, teto, etapa que pediria pessoa, cancelamento pelo sistema) e não diz
                # nada sobre o fluxo: `sem_evidencia`, nunca `execucao_falhou`.
                motivo = Motivo.SEM_EVIDENCIA
            else:
                motivo = Motivo.SEM_EVIDENCIA if status.startswith("completed") else Motivo.EXECUCAO_FALHOU
        return int(self._registro.fechar(p.id, estado, motivo, usd, self._relogio()))

    def _sem_os_sem_caminho(self, pendentes: list[PedidoVivo], agora: datetime) -> list[PedidoVivo]:
        """30.36/30.37: o pedido que a execução já não alcança fecha `sem_caminho` antes de gastar uma execução (o fluxo
        pode ter mudado depois do nascimento): na receita, o plano do fluxo ativo não chega à etapa dela; no fluxo, o
        comando de origem não cabe mais no molde dele. Roda em toda volta, antes do fôlego: não custa."""
        vivos: list[PedidoVivo] = []
        for p in pendentes:
            if p.item_kind in (LivroKind.RECEITA.value, LivroKind.FLUXO.value) and not self._caminho(
                    p.item_kind, p.item_ref, p.comando):
                if self._registro.recusar(p.id, Motivo.SEM_CAMINHO, agora):
                    log.info("aprendizado: validação %s de %s sem caminho (%s)", p.id, p.item_ref,
                             "o fluxo ativo não chega à etapa" if p.item_kind == LivroKind.RECEITA.value
                             else "o comando de origem não cabe no molde do fluxo")
                continue
            motivo = self._recusa_de_prova(p, agora)
            if motivo is not None:
                if self._registro.recusar(p.id, motivo, agora):
                    log.info("aprendizado: validação %s de %s recusada ao despachar (%s)", p.id, p.item_ref,
                             motivo.value)
                continue
            vivos.append(p)
        return vivos

    def _recusa_de_prova(self, p: PedidoVivo, agora: datetime) -> Motivo | None:
        """30.42: o pedido de FLUXO que gastaria uma prova inútil fecha antes de gastar. (1) `limite_de_provas`: o item já
        teve `MAXIMO_DE_PROVAS` provas da mesma versão do conteúdo na janela; (2) `sem_aparelho_novo`: a falta pede
        OUTRO aparelho e nenhum que serve (tem os apps, sem conta real, ligado ou não, ocupado ou não) ficou fora dos já
        usados. Se sobra um ocupado ou fora do ar, o pedido espera (`sem_aparelho`), como sempre."""
        if p.item_kind in (LivroKind.FLUXO.value, LivroKind.RECEITA.value) and p.comando:
            # 30.41: o plano que a execução rodaria passa do teto máximo (ou o tamanho não se sabe). Na receita, só
            # quando o comando resolve num fluxo ativo (sem ele, o `sem_fluxo_ativo`/`sem_caminho` já responde).
            etapas = self._fontes.etapas_da_execucao(p.item_ref, p.comando)
            if (etapas is not None or p.item_kind == LivroKind.FLUXO.value) and teto_da_prova(etapas) is None:
                return Motivo.PLANO_ACIMA_DO_TETO
        if p.item_kind != LivroKind.FLUXO.value:
            return None
        provas = self._fontes.provas_do_item(p.item_ref)
        if limite_de_provas_atingido(provas, self._fontes.versao_do_conteudo(p.item_ref), agora):
            return Motivo.LIMITE_DE_PROVAS
        if Falta.REPRODUCAO_EM_OUTRO_APARELHO.value in p.falta:
            fora = excluidos_da_validacao(p.falta, origem=p.aparelho_excluido, provas=(a.aparelho for a in provas))
            if not sobra_aparelho_novo(p.grupo, self._despacho.aparelhos(self._pacotes(p)), fora):
                return Motivo.SEM_APARELHO_NOVO
        return None

    # ------------------------------------------------------------------ 3b. o motivo segue a evidência (30.36)
    def executar(self, agora: datetime) -> int:
        """Passo da curadoria: o pedido fechado `sem_evidencia` passa ao motivo que hoje se sabe — o da evidência que a
        execução ganhou depois (a reclassificação da sombra acrescenta a `forma` ao lado do `against` antigo) ou, na
        receita que o plano do fluxo ativo não alcança, `sem_caminho` (o que rodou antes do 30.36 também ganha a marca
        para o curador). Nunca vira `feita` (a favor que chega depois não é desta validação) e nunca sai de outro
        motivo."""
        n = 0
        for p in self._registro.sem_evidencia():
            invalida = self._fontes.invalida_da_execucao(p.item_ref, p.run_id) if p.run_id else None
            if invalida is not None:                # 30.42: a linha `invalida` (a reclassificação, depois) vence a posição
                motivo = motivo_da_prova_invalida(invalida)
                if motivo is not Motivo.SEM_EVIDENCIA and self._registro.remotivar(p.id, motivo, agora):
                    n += 1
                continue
            posicao = self._fontes.posicao_da_execucao(p.item_ref, p.run_id) if p.run_id else None
            motivo = _MOTIVO_DA_POSICAO.get(posicao) if posicao is not None else None
            if (motivo is None and p.item_kind == LivroKind.RECEITA.value and p.comando
                    and not self._fontes.caminho_da_receita(p.item_ref, p.comando)):
                motivo = Motivo.SEM_CAMINHO
            if motivo is not None and self._registro.remotivar(p.id, motivo, agora):
                n += 1
        return n + self._reabrir(agora)

    def _reabrir(self, agora: datetime) -> int:
        """30.37 (item 8): o pedido de fluxo que fechou `sem_evidencia`/`divergencia_de_forma` numa execução ANTERIOR à
        prova (o planejador livre, que media o planejador e não o fluxo) volta como pedido novo, uma vez: a execução
        de prova é a que mede o fluxo. Só com a validação ligada (o pedido novo gasta). O pedido novo é posterior ao
        antigo e o tira da lista; o índice parcial de um vivo por item recusa a duplicata. Conta junto com o resto do
        passo: o consumidor (`servico._rodar`) só soma o que o passo fez."""
        aj = self._ajustes()
        if aj.modo is Modo.OFF:
            return 0
        n = 0
        for antigo in self._registro.para_reabrir():
            novo = replace(antigo, expira_em=to_iso(agora + timedelta(hours=VALIDADE_DO_PEDIDO_H)),
                           teto_usd=aj.teto_por_pedido_usd)
            pid = self._registro.criar(novo, agora)
            if pid is not None:
                n += 1
                log.info("aprendizado: validação %s reaberta para %s com a execução de prova", pid, novo.item_ref)
        return n

    # ------------------------------------------------------------------ 4. o gatilho `evidencia_chegou` do curador
    def chegadas(self) -> list[PedidoVivo]:
        """Independe do `modo` da validação, que é do DESPACHANTE: a evidência já foi paga, e o gasto da revisão é do modo
        e do orçamento do curador. Antes, a pausa do P4 (03/10 17:18Z, `modo: "off"`) prendia o `feita` das 16:37:59Z."""
        return self._registro.chegadas()

    def revisado(self, pedido_id: str, review_id: str) -> None:
        self._registro.revisado(pedido_id, review_id)

    # ------------------------------------------------------------------ 5. a leitura do painel (30.38 b)
    def listar(self, estado: EstadoDoPedido | None = None, limite: int = 50, antes: str | None = None, *,
               item: str | None = None, run: str | None = None) -> list[PedidoListado]:
        """Só leitura e independente do `modo`: com a validação pausada, o painel ainda mostra o que já se pediu.
        30.43: `item` e `run` filtram (o rosto da validação no item e no Resumo da execução)."""
        return self._registro.listar(estado, max(1, min(limite, LISTA_MAX)), antes, item=item, run=run)

    def contagens(self) -> dict[str, int]:
        """Um número por estado do vocabulário (zero quando não há), para os filtros do painel."""
        contadas = self._registro.contagens()
        return {e.value: int(contadas.get(e.value, 0)) for e in EstadoDoPedido}

    def modo(self) -> Modo:
        """O modo do despachante agora (`off` pausa: nada nasce e nada roda; o painel explica a pausa)."""
        return self._ajustes().modo


#: A evidência que não é a favor, e o motivo do pedido que ela fecha (30.36).
_MOTIVO_DA_POSICAO: dict[Posicao, Motivo] = {Posicao.AGAINST: Motivo.EVIDENCIA_CONTRA,
                                             Posicao.CONFLICT: Motivo.EVIDENCIA_CONTRA,
                                             Posicao.FORMA: Motivo.DIVERGENCIA_DE_FORMA}


__all__ = ["COMANDO_NA_LISTA", "LISTA_MAX", "AjustesDaValidacao", "DespachoDeValidacao", "FontesDaValidacao",
           "NovoPedido", "Origem", "PedidoListado", "PedidoVivo", "RegistroDeValidacoes", "ServicoDeValidacao"]

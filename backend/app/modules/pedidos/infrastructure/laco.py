"""O laço de pedidos (item 28.4): materializa, despacha e fecha as ocorrências (docs/design/pedidos-laco.md).

Uma volta (`uma_volta`, SÍNCRONA: o banco é síncrono e `RunService.create` precisa do laço de eventos, A3):

    1. `fechar`        varre as ocorrências `despachada`/`rodando` e fecha as que a execução já encerrou (A1: o gancho
                       `on_run_settled` não vê todo fim; a varredura é o caminho normal de falha no planejamento e de
                       cancelamento sem worker) ou cancela a que passou do prazo de início;
    2. `materializar`  gera os instantes devidos de cada gatilho desde o cursor, aplica janela e coalescência
                       (`domain/materializar.py`) e insere com `ON CONFLICT DO NOTHING`;
    3. `despachar`     sobreposição (`domain/sobreposicao.py`), reserva, e `RunService.create` com a chave
                       `chave:t<n>` (idempotente: a execução da chave é PROCURADA antes de criar, A2);
    4. `agendar`       recalcula `proxima_em` e encerra o pedido esgotado.

**Só o líder age.** Cada volta começa pela trava `pedidos` (28.1); sem o mandato, a volta é pulada sem erro. As
escritas de estado (inserção + avanço do cursor, CAS `devida → despachada`, fechamento) rodam em
`Lideranca.cercada`: o líder velho, que acordou de uma pausa depois de outro assumir, é recusado com `TravaPerdida` e
a volta termina sem escrever. A CORREÇÃO, porém, vem das chaves únicas: dois laços, duas voltas ou um reinício no meio
produzem uma ocorrência e uma execução. `RunService.create` fica FORA da cerca (abre a própria transação e resolve
alvos; não deve segurar a escrita do banco).

Nada de estado em memória é fonte de verdade: o cursor mora em `pedido_gatilhos.cursor`, a reserva em
`pedido_ocorrencias.dono/prazo_posse` e a ligação execução→ocorrência em `runs.pedido_id/ocorrencia_id`. Depois de um
reinício, a primeira volta é imediata e refaz tudo isso a partir do banco.

O relógio é injetado (`relogio`, o `db.agora` em produção): nada aqui lê `datetime.now()`.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from app.config import PedidosCfg
from app.db import Database, Row, loads
from app.models import RunCreate, RunTarget
from app.modules.pedidos.infrastructure.acoes import AcoesDePedidos
from app.modules.pedidos.infrastructure.relatorios import ServicoDeRelatorios
from app.modules.pedidos.infrastructure.repositorio import RepositorioDePedidos
from app.modules.pedidos.domain.resumo import ResumidorDeRelatorio
from app.modules.pedidos.domain import avisos as dominio_avisos
from app.modules.pedidos.domain import colaboracao, gatilhos, gatilhos_dinamicos, tentativas
from app.modules.pedidos.domain.chave import chave_da_ocorrencia, chave_da_tentativa, formatar_instante
from app.modules.pedidos.domain.estados import OCORRENCIA_TERMINAIS, transicionar_ocorrencia, transicionar_pedido
from app.modules.pedidos.domain.memoria import MemoriaInvalida
from app.modules.pedidos.domain.fechamento import (INTENCAO_PRAZO_DE_INICIO, ObjetivoVisto, fechar,
                                                   prazo_de_inicio_vencido)
from app.modules.pedidos.domain.materializar import janela_padrao_s, materializar, truncar
from app.modules.pedidos.domain.orcamento import (ULTIMAS_PARA_ESTIMAR, custo_estimado, motivo_sem_orcamento,
                                                  quantas_cabem)
from app.modules.pedidos.domain.sobreposicao import Devida, decidir
from app.modules.pedidos.domain.tentativas import ACAO_INCERTA, ACAO_REPETIR, Decisao
from app.taskqueue.service import RunError
from app.taskqueue.travas import PEDIDOS, Lideranca, TravaPerdida
from app.util import parse_iso, to_iso

log = logging.getLogger("poc.pedidos")

_UM_SEGUNDO = timedelta(seconds=1)
#: O gatilho de evento só lê eventos com `ts` até `agora - MARGEM_DOS_EVENTOS`: dá tempo de a transação que gravou um id
#: menor terminar (no PostgreSQL a ordem do COMMIT não é a dos ids). Atraso aceito de poucos segundos (§14.2).
MARGEM_DOS_EVENTOS = timedelta(seconds=5)
#: Quantos eventos um gatilho lê por volta (o resto fica para a seguinte; coalescem do mesmo jeito).
LOTE_DE_EVENTOS = 500
#: Estados em que a visita da persona ainda não acabou (a seguinte espera).
_ABERTAS = ("prevista", "devida", "despachada", "rodando")


def _observada(x: Row) -> gatilhos_dinamicos.Observada:
    return gatilhos_dinamicos.Observada(x["tipo"], x["situacao"], x["valor"], x["sha256"])


class _Colidiu(Exception):
    """A ocorrência do evento colidiu com a de outra volta no mesmo segundo: desfaz o avanço do cursor."""

#: Prioridade da execução de uma ocorrência (`runs.prioridade`, 28.6). A 067 não deu campo de prioridade ao pedido, então
#: todas valem 0 (o que o comando de hoje também vale) e nada muda de ordem; `_prioridade` é a costura para quando o
#: pedido ganhar o campo (o desenho do §10 quer a interativa à frente da de fundo).
PRIORIDADE_PADRAO = 0


@dataclass
class Resumo:
    """O que uma volta fez (para teste e log): contadores, nunca conteúdo do pedido."""
    materializadas: int = 0
    despachadas: int = 0
    fechadas: int = 0
    adiadas: int = 0
    seguradas: int = 0          # `devida` que espera a dependência (28.10 F2); não conta como adiada (essa é do saldo)
    encerrados: int = 0
    puladas: int = 0
    perdidas: int = 0
    retentadas: int = 0
    aguardando: int = 0
    pausados: int = 0
    buracos: int = 0
    condicoes: int = 0
    erros: int = 0
    lider: bool = True


def _instante_legivel(instante: datetime) -> str:
    """O instante como a pessoa lê no motivo de uma ocorrência (`02/10 22:17 UTC`); o valor gravado segue ISO."""
    return f"{instante.astimezone(timezone.utc):%d/%m %H:%M} UTC"


class LacoDePedidos:
    def __init__(self, db: Database, runs, lideranca: Lideranca, cfg: PedidosCfg, *,
                 relogio: Callable[[], datetime] | None = None, lider: Callable[[], int | None] | None = None,
                 custo_da_execucao: Callable[[str], float] | None = None,
                 adiar_por_saldo: Callable[[], str | None] | None = None,
                 resumidor: ResumidorDeRelatorio | None = None,
                 avisar: Callable[[Mapping[str, object]], None] | None = None):
        self.db = db
        self.runs = runs
        self.lideranca = lideranca
        self.cfg = cfg
        #: US$ gastos pela execução (`ai_calls`, pela mesma conta do painel de uso: `costs.spent_usd`). Injetado por
        #: `AppState` (precisa de `ai.prices`, que o laço não conhece); sem ele o fechamento grava custo 0 (28.6).
        self.custo_da_execucao: Callable[[str], float] | None = custo_da_execucao
        #: Devolve o motivo de ADIAR o despacho (saldo da conta de IA abaixo do mínimo, ADR-051) ou `None` (28.6). Sem
        #: ele o laço nunca adia por saldo. Lido uma vez por volta, só quando há o que despachar.
        self.adiar_por_saldo: Callable[[], str | None] | None = adiar_por_saldo
        self._adiamento: str | None = None
        #: Entrega o `AvisoDTO` do `pedido.aviso` (contrato 28.9) a quem o publica: `AppState` o liga ao barramento, que o
        #: 28.11 assina. Sem ele nada é emitido (a mudança de estado do pedido vale igual). Chamado FORA da cerca.
        self.avisar: Callable[[Mapping[str, object]], None] | None = avisar
        #: Atributo (e não só parâmetro) para o teste trocar o relógio de um laço já de pé, como em `Lideranca`.
        self.relogio: Callable[[], datetime] = relogio if relogio is not None else db.agora
        self._lider = lider
        self.repo = RepositorioDePedidos(db)
        #: Observações do fechamento, memória e relatório (28.7). O resumo por IA só existe com `resumo_ia` ligado E um
        #: `resumidor` injetado; sem os dois, nada de IA é chamado.
        self.relatorios = ServicoDeRelatorios(db, lambda: self.relogio(), resumidor=resumidor, resumo_ia=cfg.resumo_ia,
                                              resumo_ia_teto_usd=cfg.resumo_ia_teto_usd, marcar=self.repo.marcar)
        self.acoes = AcoesDePedidos(self.repo, runs, lambda: self.relogio(), self.acordar, relatorios=self.relatorios)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._evento: asyncio.Event | None = None
        #: Publica as mudanças marcadas no repositório (eventos `pedido.*`, 28.9). Injetado por `AppState`; sem ele o
        #: laço não emite nada (teste, laço isolado).
        self.notificar: Callable[[list[tuple[str, str, bool]]], None] | None = None

    # ------------------------------------------------------------------ acordar e ritmo
    def acordar(self) -> None:
        """Pede uma volta já. Seguro de outra thread (`call_soon_threadsafe`: `asyncio.Event` não é)."""
        loop, evento = self._loop, self._evento
        if loop is None or evento is None:
            return                      # o laço ainda não subiu (ou está desligado): a primeira volta lê o banco
        try:
            loop.call_soon_threadsafe(evento.set)
        except RuntimeError:            # laço de eventos fechado (encerramento)
            pass

    def ao_assentar(self, run_id: str) -> None:
        """Gancho de fim de execução (`AppState._execucao_assentada`): só ACORDA o laço. Não fecha ali dentro: o gancho
        roda no `finally` do worker e não deve escrever no banco do pedido fora da trava."""
        try:
            if self.db.scalar("SELECT pedido_id FROM runs WHERE id=?", (run_id,)):
                self.acordar()
        except Exception:  # noqa: BLE001 - o gancho nunca derruba o fim de uma execução; a varredura fecha
            log.exception("pedidos: gancho de fim da execução %s", run_id)

    async def laco(self) -> None:
        """O laço em si: uma volta imediata (a retomada depois de reinício), depois `tick_s` ou o próximo instante, o
        que vier antes, ou um `acordar()`."""
        self._loop = asyncio.get_running_loop()
        self._evento = asyncio.Event()
        while True:
            try:
                self.uma_volta()
            except Exception:  # noqa: BLE001 - um erro numa volta nunca mata o laço
                log.exception("laço de pedidos")
            try:
                espera = self._espera()
            except Exception:  # noqa: BLE001
                espera = self.cfg.tick_s
            try:
                await asyncio.wait_for(self._evento.wait(), timeout=espera)
            except asyncio.TimeoutError:
                pass
            self._evento.clear()

    def _espera(self) -> float:
        """`min(tick_s, próxima materialização - agora)`, nunca abaixo de meio segundo."""
        proxima = self.repo.menor_proxima_em()
        if not proxima:
            return self.cfg.tick_s
        falta = (parse_iso(proxima) - self.relogio()).total_seconds()
        return max(0.5, min(self.cfg.tick_s, falta))

    # ------------------------------------------------------------------ a volta
    def _token(self) -> int | None:
        if self._lider is not None:
            return self._lider()
        try:
            return self.lideranca.tomar(PEDIDOS)
        except Exception:  # noqa: BLE001 - sem banco não há como saber quem é o líder: pular é o lado seguro
            log.exception("trava %s: não foi possível conferir o líder", PEDIDOS)
            return None

    def uma_volta(self) -> Resumo | None:
        """`None` = outro backend é o líder (volta pulada). Síncrona: roda na thread do laço de eventos."""
        token = self._token()
        if token is None:
            return None
        agora = self.relogio()
        r = Resumo()
        try:
            self._fechar(token, agora, r)
            self._fechar_dos_parados(token, agora, r)
            self._materializar(token, agora, r)
            self._gatilhos_dinamicos(token, agora, r)
            self._orcamentos(token, agora, r)       # DEPOIS de materializar: a `devida` nascida agora também é barrada
            self._despachar(token, agora, r)
            self._agendar(token, agora, r)
        except TravaPerdida as e:
            log.warning("pedidos: o mandato acabou no meio da volta (%s); nada mais é escrito", e)
            r.lider = False
        finally:
            self._publicar()
        return r

    def _publicar(self) -> None:
        marcas = self.repo.descarregar()
        if marcas and self.notificar is not None:
            try:
                self.notificar(marcas)
            except Exception:  # noqa: BLE001 - evento é espelho: nunca derruba a volta
                log.exception("pedidos: publicação dos eventos da volta")

    # ------------------------------------------------------------------ 1. fechamento
    def _fechar(self, token: int, agora: datetime, r: Resumo) -> None:
        for o in self.repo.para_fechar():
            try:
                self._fechar_uma(o, token, agora, r, checar_prazo=True)
            except TravaPerdida:
                raise
            except Exception:  # noqa: BLE001 - uma ocorrência com defeito não para as outras
                log.exception("pedidos: fechamento da ocorrência %s", o["id"])
                r.erros += 1

    def _fechar_uma(self, o: Row, token: int, agora: datetime, r: Resumo, *, checar_prazo: bool) -> None:
        run_status = o["run_status"] if o["run_existe"] else None
        objetivos = ([ObjetivoVisto(x["id"], x["status"], bool(x["started_at"])) for x in self.repo.objetivos(o["run_id"])]
                     if run_status is not None else [])
        intencao = o["resumo"]
        if checar_prazo and run_status is not None and o["run_criada"]:
            if prazo_de_inicio_vencido(estado_atual=o["estado"], run_status=run_status, objetivos=objetivos,
                                       despachada_em=parse_iso(o["run_criada"]), agora=agora,
                                       prazo_inicio_s=self.cfg.prazo_inicio_s, intencao=intencao):
                self._cancelar_por_prazo(o, token)
                # A execução cancelada antes de iniciar já assentou: fecha agora, sem esperar a volta seguinte.
                nova = self.repo.para_fechar(o["id"])
                if nova:
                    self._fechar_uma(nova[0], token, agora, r, checar_prazo=False)
                return
        fech = fechar(estado_atual=o["estado"], run_id=o["run_id"] or "-", run_status=run_status, objetivos=objetivos,
                      status_detail=o["run_detalhe"], started_at=o["run_iniciada"], intencao=intencao,
                      pedido_cancelado=o["pedido_estado"] == "cancelado", prazo_inicio_s=self.cfg.prazo_inicio_s,
                      motivo_de_espera=self.repo.espera_da_execucao(o["run_id"]) if o["run_id"] else None)
        if fech is None:
            return
        # O custo é lido ANTES de fechar e gravado no mesmo UPDATE do fechamento (28.6): a retenção apaga `ai_calls`
        # por `ts`, mas não toca as de execução de ocorrência ainda aberta (`state._purgar_demais_tabelas`), então
        # esta leitura sempre vê as chamadas inteiras. Só o fechamento terminal soma (uma vez por tentativa).
        custo = self._custo_da_tentativa(o) if fech.terminal else None
        p = self.repo.pedido(o["pedido_id"]) if fech.estado in ("falhou", "incerta") else None
        if p is not None and fech.estado == "falhou":
            decisao = self._decidir_tentativa(o, p, fech.estado, agora, custo or 0.0, run_status)
            if decisao.acao == ACAO_REPETIR and decisao.repetir_em is not None and o["pedido_estado"] == "ativo":
                self._retentar(o, fech.motivo, decisao.repetir_em, token, custo or 0.0, r)
                return
            if decisao.acao == ACAO_INCERTA:
                # Falha com efeito possível: o mundo está incerto (§7.6). A ocorrência fecha `incerta` (qualquer que seja o
                # estado do pedido), e o laço mais abaixo leva um pedido `ativo` a `aguardando_pessoa`.
                fech = replace(fech, estado="incerta", motivo=f"{fech.motivo}; {decisao.complemento}"[:500])
            elif decisao.complemento:
                fech = replace(fech, motivo=f"{fech.motivo}; {decisao.complemento}")
        transicionar_ocorrencia(o["estado"], fech.estado, motivo=fech.motivo)
        preparo = self._observar(o, fech) if fech.terminal else None
        avisos: list[Mapping[str, object]] = []
        with self.lideranca.cercada(PEDIDOS, token):
            moveu = self.repo.mover(o["id"], o["estado"], fech.estado, motivo=fech.motivo,
                                    iniciada_em=fech.iniciada_em, terminada_em=to_iso(agora) if fech.terminal else None,
                                    custo_usd=custo)
            if moveu and preparo is not None:
                # Na MESMA transação do fechamento: a ocorrência fecha com as observações dela, ou nada muda e a varredura
                # repete (a observação é reentrante). Depois do fechamento a execução pode ser purgada; a observação fica.
                self.relatorios.gravar_do_fechamento(preparo, pedido_id=o["pedido_id"], ocorrencia_id=o["id"])
            if moveu and p is not None and o["pedido_estado"] == "ativo":
                # Também na mesma transação: sem isto, uma queda entre o fechamento e a mudança do pedido deixaria o pedido
                # `ativo` com a ocorrência `incerta` já fora da varredura, e nada o levaria a esperar a pessoa.
                avisos = self._efeitos_no_pedido(o, p, fech.estado, agora, r)
        if moveu and fech.terminal:
            r.fechadas += 1
        for aviso in avisos:
            self._emitir(aviso)

    # ------------------------------------------------------------------ 1a. tentativas e efeito (28.5)
    def _decidir_tentativa(self, o: Row, p: Row, estado: str, agora: datetime, custo: float,
                           run_status: str | None) -> Decisao:
        """A decisão do domínio para a ocorrência que falhou. Efeito possível = a execução sumiu, ou alguma ação dela pode
        ter chegado ao aparelho. O orçamento (28.6) também vale para a nova tentativa: sem verba para outra, a falha é
        definitiva e o motivo diz por quê (mais claro que deixá-la `devida` e depois `pulada`)."""
        efeito = run_status is None or self.repo.efeito_possivel(o["run_id"])
        sem_orcamento: str | None = None
        if p["orcamento_total_usd"] is not None:
            total, gasto, necessario = self._situacao_do_orcamento(p)
            sem_orcamento = motivo_sem_orcamento(total, gasto + custo, necessario)
        teto = p["orcamento_ocorrencia_usd"]
        if sem_orcamento is None and teto is not None and float(o["custo_usd"] or 0.0) + custo >= float(teto):
            sem_orcamento = f"o teto da ocorrência (US$ {float(teto):.4f}) já foi gasto"
        # 28.20: a execução que parou no teto de orçamento sem nenhuma ação de efeito declarado (commit) falha POR
        # ORÇAMENTO. Não é `incerta`: os toques de navegação contam como efeito possível, mas o que interrompeu foi a
        # verba, e mandar o pedido esperar a pessoa travaria a recorrência por um motivo que não é do mundo. Não repete
        # (sem verba nesta ocorrência); a pausa por falhas seguidas continua valendo.
        if run_status is not None and self.repo.parou_no_orcamento_sem_efeito_declarado(o["run_id"]):
            efeito = False
            sem_orcamento = sem_orcamento or "a execução parou no teto de orçamento"
        return tentativas.decidir(estado=estado, tentativa=max(1, int(o["tentativa"] or 1)),
                                  max_tentativas=int(p["max_tentativas"] or self.cfg.max_tentativas),
                                  efeito_possivel=efeito, agora=agora, base_s=self.cfg.retentativa_base_s,
                                  teto_s=self.cfg.retentativa_teto_s, sem_orcamento=sem_orcamento)

    def _retentar(self, o: Row, motivo_da_falha: str | None, em: datetime, token: int, custo: float, r: Resumo) -> None:
        """A MESMA ocorrência volta a `devida` (`falhou → devida`, §7.6) com a falha como motivo e o custo da tentativa
        somado; o despacho a pega depois de `em` e usa `chave:t<n+1>`. Não grava observação: a da última tentativa é a
        que vale (a chave `(ocorrência, alvo, nome)` é única e a da tentativa anterior a tomaria)."""
        quando = to_iso(em)
        # `terminada_em` (a coluna) continua ISO; só o TEXTO do motivo, que a pessoa lê, ganha o instante legível em UTC.
        motivo = f"tentativa {o['tentativa']} falhou ({motivo_da_falha}); nova tentativa a partir de {_instante_legivel(em)}"[:500]
        transicionar_ocorrencia(o["estado"], "falhou", motivo=motivo)
        transicionar_ocorrencia("falhou", "devida")
        with self.lideranca.cercada(PEDIDOS, token):
            if self.repo.retentar(o["id"], o["estado"], motivo=motivo, nao_antes_de=quando, custo_usd=custo):
                r.retentadas += 1

    def _efeitos_no_pedido(self, o: Row, p: Row, estado: str, agora: datetime, r: Resumo) -> list[Mapping[str, object]]:
        """O que o fechamento de `estado` faz ao PEDIDO `ativo`; devolve os avisos a emitir DEPOIS da transação.

        `incerta` leva o pedido a `aguardando_pessoa` (nunca repete sozinha: a pessoa resolve). `falhou` definitiva conta
        as falhas seguidas e, no limite do pedido, o PAUSA (ator `sistema`, com motivo). O CAS `WHERE estado='ativo'` fecha
        a corrida com a pessoa que pausou ou cancelou no meio."""
        em = to_iso(agora)
        avisos: list[Mapping[str, object]] = []
        titulo = p["titulo"] or p["id"]
        if estado == "incerta":
            transicionar_pedido("ativo", "aguardando_pessoa", ator="sistema")
            if self.repo.mudar_estado_do_pedido(p["id"], "ativo", "aguardando_pessoa", em):
                r.aguardando += 1
                avisos.append(tentativas.aviso(
                    tipo="ocorrencia_incerta", aviso_id=f"{o['id']}:ocorrencia_incerta", pedido_id=p["id"],
                    pedido_titulo=titulo, ocorrencia_id=o["id"], criado_em=em,
                    mensagem=f"O pedido «{titulo}» tem uma ocorrência incerta: confira o que aconteceu e resolva.",
                    dados={"tentativa": int(o["tentativa"] or 0)}))
        elif estado == "falhou":
            limite = int(p["pausa_por_falha"] or self.cfg.falhas_para_pausar)
            seguidas = tentativas.falhas_seguidas(["falhou", *self.repo.desfechos_recentes(p["id"], o["id"], limite)])
            if tentativas.deve_pausar(seguidas, limite):
                motivo = f"{seguidas} falhas seguidas"
                transicionar_pedido("ativo", "pausado", ator="sistema", motivo=motivo)
                if self.repo.mudar_estado_do_pedido(p["id"], "ativo", "pausado", em, pausado_motivo=motivo):
                    r.pausados += 1
                    avisos.append(tentativas.aviso(
                        tipo="pausa_automatica", aviso_id=dominio_avisos.chave_da_pausa(p["id"], em), pedido_id=p["id"],
                        pedido_titulo=titulo, ocorrencia_id=o["id"], criado_em=em,
                        mensagem=f"O pedido «{titulo}» foi pausado depois de {seguidas} falhas seguidas.",
                        dados={"falhas_seguidas": seguidas}))
        return avisos

    def _emitir(self, aviso: Mapping[str, object]) -> None:
        """Falha ao avisar nunca desfaz o que já foi gravado: o estado do pedido é a fonte da verdade."""
        if self.avisar is None:
            return
        try:
            self.avisar(aviso)
        except Exception:  # noqa: BLE001
            log.exception("pedidos: aviso %s", aviso.get("tipo"))

    def _custo_da_tentativa(self, o: Row) -> float:
        """US$ das chamadas de IA da execução desta tentativa; 0 sem execução ou sem a leitura injetada. Falha na
        leitura não impede o fechamento (a ocorrência não pode ficar aberta por causa de um número): registra e grava 0."""
        if not o["run_id"] or self.custo_da_execucao is None:
            return 0.0
        try:
            return max(0.0, float(self.custo_da_execucao(o["run_id"])))
        except Exception:  # noqa: BLE001
            log.exception("pedidos: custo da execução %s", o["run_id"])
            return 0.0

    def _observar(self, o: Row, fech):
        """O que a ocorrência observou (28.7), lido ANTES de fechar. Falha aqui não trava o fechamento: a ocorrência fecha sem
        observação e o relatório a lista como `sem_observacao` em "não coberto" (falta de prova nunca vira sucesso)."""
        try:
            return self.relatorios.observacoes_do_fechamento(o, estado=fech.estado, motivo=fech.motivo,
                                                             versao_do_pedido=int(o["pedido_versao"]))
        except Exception:  # noqa: BLE001
            log.exception("pedidos: observações do fechamento da ocorrência %s", o["id"])
            return None

    def _cancelar_por_prazo(self, o: Row, token: int) -> None:
        """Cancela a execução que não começou a tempo e grava a INTENÇÃO; o estado final só sai quando ela assenta."""
        try:
            self.runs.cancel(o["run_id"])
        except RunError as e:       # assentou entre a leitura e aqui: a varredura fecha como estiver
            log.info("pedidos: execução %s não pôde ser cancelada por prazo de início (%s)", o["run_id"], e.code)
        with self.lideranca.cercada(PEDIDOS, token):
            self.repo.gravar_resumo(o["id"], INTENCAO_PRAZO_DE_INICIO)

    def _fechar_dos_parados(self, token: int, agora: datetime, r: Resumo) -> None:
        """`prevista`/`devida` de pedido pausado ou cancelado: a ação da pessoa pode ter caído no meio, e o laço completa."""
        for linha in self.repo.de_pedido_parado():
            para, motivo = (("pulada", "pedido pausado") if linha["pedido_estado"] == "pausado"
                            else ("cancelada", "pedido cancelado"))
            transicionar_ocorrencia(linha["estado"], para, motivo=motivo)
            with self.lideranca.cercada(PEDIDOS, token):
                if self.repo.mover(linha["id"], linha["estado"], para, motivo=motivo, terminada_em=to_iso(agora)):
                    r.puladas += 1

    # ------------------------------------------------------------------ 1b. orçamento total (28.6)
    def _situacao_do_orcamento(self, p: Row) -> tuple[float | None, float, float]:
        """`(total, gasto, necessario)`: o orçamento total do pedido, o que as ocorrências fechadas já gastaram e o que
        a próxima deve custar (`domain/orcamento.py::custo_estimado`)."""
        total = p["orcamento_total_usd"]
        necessario = custo_estimado(self.repo.ultimos_custos(p["id"], ULTIMAS_PARA_ESTIMAR), p["orcamento_ocorrencia_usd"])
        return (None if total is None else float(total)), self.repo.custo_total(p["id"]), necessario

    def _orcamentos(self, token: int, agora: datetime, r: Resumo) -> None:
        """Pedido cujo orçamento total não cobre outra ocorrência: o que ainda não virou execução é `pulada`
        (`orçamento: …`) e, sem execução aberta, o pedido ENCERRA com `encerrado_motivo='orcamento'` (§6.5). Com uma
        aberta, espera ela fechar (o custo dela é o que decide) e a volta seguinte encerra. Pedido sem orçamento total
        nunca passa por aqui: nada muda para ele."""
        for p in self.repo.com_orcamento_total():
            try:
                self._conferir_orcamento(p, token, agora, r)
            except TravaPerdida:
                raise
            except Exception:  # noqa: BLE001 - um pedido com defeito não para os outros
                log.exception("pedidos: orçamento do pedido %s", p["id"])
                r.erros += 1

    def _conferir_orcamento(self, p: Row, token: int, agora: datetime, r: Resumo) -> bool:
        """`True` = sem orçamento para outra ocorrência."""
        total, gasto, necessario = self._situacao_do_orcamento(p)
        motivo = motivo_sem_orcamento(total, gasto, necessario)
        if motivo is None:
            if dominio_avisos.passou_de_80(gasto, total):
                self._avisar_orcamento_80(p, float(total), gasto, agora)
            return False
        for linha in self.repo.ids_prevista_devida(p["id"]):
            self._pular([(linha["id"], f"orçamento: {motivo}")], token, agora, r, de=linha["estado"])
        if self.repo.quantas_em_aberto(p["id"]) == 0:
            transicionar_pedido("ativo", "encerrado", ator="sistema", motivo="orcamento")
            with self.lideranca.cercada(PEDIDOS, token):
                if self.repo.mudar_estado_do_pedido(p["id"], "ativo", "encerrado", to_iso(agora), versao=p["versao"],
                                                    encerrado_motivo="orcamento"):
                    r.encerrados += 1
        return True

    def _avisar_orcamento_80(self, p: Row, total: float, gasto: float, agora: datetime) -> None:
        """`orcamento_80`: uma vez por pedido ATÉ o teto subir (a chave leva o teto). O `esgotado` não nasce aqui: quem
        encerra o pedido é a mudança de estado acima, e a API o avisa a partir do `encerrado_motivo` (um só caminho)."""
        titulo = p["titulo"] or p["id"]
        self._emitir(tentativas.aviso(
            tipo="orcamento_80", aviso_id=dominio_avisos.chave_do_orcamento_80(p["id"], total), pedido_id=p["id"],
            pedido_titulo=titulo, ocorrencia_id=None, criado_em=to_iso(agora),
            mensagem=f"O pedido «{titulo}» já gastou 80% do orçamento (US$ {gasto:.4f} de US$ {total:.4f}).",
            dados={"gasto_usd": round(gasto, 6), "orcamento_total_usd": total}))

    # ------------------------------------------------------------------ 2. materialização
    def _materializar(self, token: int, agora: datetime, r: Resumo) -> None:
        horizonte = formatar_instante(agora + timedelta(seconds=self.cfg.horizonte_s))
        for p in self.repo.pedidos_para_cuidar(horizonte):
            try:
                self._materializar_pedido(p, token, agora, r)
            except TravaPerdida:
                raise
            except Exception:  # noqa: BLE001 - pedido com `spec` quebrada não para os outros
                log.exception("pedidos: materialização do pedido %s", p["id"])
                r.erros += 1

    def _janela(self, p: Row, g: Row | None, spec: dict) -> int:
        if p["janela_recuperacao_s"] is not None:
            return int(p["janela_recuperacao_s"])
        if g is None:
            return self.cfg.janela_padrao_s
        return janela_padrao_s(g["tipo"], autonomia=p["autonomia"], periodo_s=gatilhos.periodo_s(g["tipo"], spec),
                               padrao_s=self.cfg.janela_padrao_s)

    def _materializar_pedido(self, p: Row, token: int, agora: datetime, r: Resumo) -> None:
        if p["max_ocorrencias"] is not None and self.repo.executadas(p["id"]) >= p["max_ocorrencias"]:
            return                      # nada mais vira execução: `agendar` pula o que sobrou e encerra
        ate = truncar(agora) + timedelta(seconds=self.cfg.horizonte_s)
        if p["fim_em"]:
            ate = min(ate, parse_iso(p["fim_em"]))
        for g in self.repo.gatilhos_ativos(p["id"]):
            if g["tipo"] not in gatilhos.SUPORTADOS:
                continue
            try:
                self._materializar_gatilho(p, g, token, agora, ate, r)
            except TravaPerdida:
                raise
            except gatilhos.ErroDeGatilho as e:
                log.warning("pedidos: gatilho %s do pedido %s com spec inválida: %s", g["id"], p["id"], e)
                r.erros += 1

    def _materializar_gatilho(self, p: Row, g: Row, token: int, agora: datetime, ate: datetime, r: Resumo) -> None:
        spec = loads(g["spec"], {}) or {}
        novos = gatilhos.instantes(g["tipo"], spec, fuso=p["fuso"], criado_em=parse_iso(g["criado_em"]),
                                   depois_de=self.repo.cursor(g), ate=ate, limite=self.cfg.lote_max)
        # As `prevista` de voltas anteriores cuja hora chegou passam pelas mesmas regras (§2.2 regra 6).
        existentes = {parse_iso(x["previsto_para"]): x for x in self.repo.previstas_ate(g["id"], formatar_instante(agora))}
        todos = sorted(set(novos) | set(existentes))
        if not todos:
            return
        decisoes = materializar(todos, agora=agora, janela_s=self._janela(p, g, spec), coalescer=bool(p["coalescer"]),
                                tick_s=self.cfg.tick_s)
        em = to_iso(agora)
        with self.lideranca.cercada(PEDIDOS, token):
            atual = self.repo.pedido(p["id"])
            if atual is None or atual["estado"] != "ativo" or atual["versao"] != p["versao"]:
                return                  # pausado, cancelado ou editado no meio: a volta seguinte relê
            for d in decisoes:
                fim_de_linha = d.estado in ("pulada", "perdida")
                linha = existentes.get(d.instante)
                if linha is not None:
                    if d.estado != "prevista":
                        transicionar_ocorrencia("prevista", d.estado, motivo=d.motivo)
                        self.repo.mover(linha["id"], "prevista", d.estado, motivo=d.motivo,
                                        terminada_em=em if fim_de_linha else None)
                    continue
                chave = chave_da_ocorrencia(p["id"], g["id"], d.instante, origem=d.origem)
                if self.repo.inserir_ocorrencia(pedido_id=p["id"], versao=atual["versao"], gatilho_id=g["id"],
                                                previsto_para=formatar_instante(d.instante), chave=chave,
                                                origem=d.origem, estado=d.estado, motivo=d.motivo, token=token,
                                                criada_em=em, terminada_em=em if fim_de_linha else None):
                    r.materializadas += 1
            if novos:
                self.repo.avancar_cursor(g["id"], formatar_instante(max(novos)))

    # ------------------------------------------------------------------ 2a. evento, persona e condição (28.8, §14)
    def _gatilhos_dinamicos(self, token: int, agora: datetime, r: Resumo) -> None:
        """O passo 4 do §7.2, sobre TODOS os pedidos `ativo` (e não só os de `pedidos_para_cuidar`): um pedido com
        recorrência tem `proxima_em` da recorrência, e o evento ou a visita da persona não podem esperar por ela."""
        for tipo, passo in (("evento", self._gatilho_de_evento), ("persona", self._gatilho_de_persona),
                            ("condicao", self._gatilho_de_condicao)):
            for g in self.repo.gatilhos_dinamicos_ativos(tipo):
                try:
                    passo(g, token, agora, r)
                except TravaPerdida:
                    raise
                except (gatilhos_dinamicos.SpecInvalida, KeyError, TypeError, ValueError) as e:
                    log.warning("pedidos: gatilho %s (%s) do pedido %s: %s", g["id"], tipo, g["pedido_id"], e)
                    r.erros += 1
                except Exception:  # noqa: BLE001 - um gatilho com defeito não para os outros
                    log.exception("pedidos: gatilho %s (%s) do pedido %s", g["id"], tipo, g["pedido_id"])
                    r.erros += 1

    def _sem_mais_ocorrencias(self, g: Row, agora: datetime) -> bool:
        if g["pedido_fim_em"] and agora >= parse_iso(g["pedido_fim_em"]):
            return True
        return g["pedido_max_ocorrencias"] is not None and self.repo.executadas(g["pedido_id"]) >= g["pedido_max_ocorrencias"]

    def _gatilho_de_evento(self, g: Row, token: int, agora: datetime, r: Resumo) -> None:
        spec = gatilhos_dinamicos.normalizar("evento", loads(g["spec"], {}) or {})
        cursor = gatilhos_dinamicos.cursor_de_evento(g["cursor"])
        if cursor is None:
            # Sem linha de base (gatilho anterior ao 28.8, ou cursor estragado): o log de agora é a base; nada dispara.
            base = gatilhos_dinamicos.formatar_cursor_de_evento(self.repo.maior_evento())
            with self.lideranca.cercada(PEDIDOS, token):
                self.repo.cas_cursor(g["id"], g["cursor"], base)
            return
        maior = self.repo.maior_evento()
        if cursor > maior:
            # O log voltou para trás (banco restaurado de backup: a sequência regride). Sem refazer a base, `teto <= cursor`
            # calaria o gatilho para sempre, sem sinal. Refaz e registra; o que houve no meio não dispara.
            em = to_iso(agora)
            with self.lideranca.cercada(PEDIDOS, token):
                if self.repo.cas_cursor(g["id"], g["cursor"], gatilhos_dinamicos.formatar_cursor_de_evento(maior)):
                    self._lembrar(g, f"evento.base.{g['id']}", "pendencia",
                                  f"o log de eventos voltou de #{cursor} para #{maior} (banco restaurado?); o gatilho "
                                  f"{g['id']} recomeçou dali e nada foi disparado pelo intervalo", em)
            return
        antes = g["cursor"]
        buraco = gatilhos_dinamicos.buraco(cursor, self.repo.menor_evento())
        if buraco is not None:
            self._registrar_buraco(g, buraco, token, agora, r)
            antes = gatilhos_dinamicos.formatar_cursor_de_evento(buraco[1])
            cursor = buraco[1]
        teto = self.repo.teto_dos_eventos(to_iso(agora - MARGEM_DOS_EVENTOS))
        if teto <= cursor:
            return
        kinds = [str(k) for k in spec["kinds"]] if isinstance(spec["kinds"], list) else []
        niveis = [str(n) for n in spec["niveis"]] if isinstance(spec.get("niveis"), list) else None
        casados = self.repo.eventos_depois(cursor, teto, kinds, niveis, g["pedido_id"], LOTE_DE_EVENTOS)
        # Lote cheio: o cursor para no último lido (o resto vem na volta seguinte). Senão vai ao teto: os eventos que não
        # casam também ficam para trás (sem isso, a purga deles pareceria um buraco).
        novo = int(casados[-1]["id"]) if len(casados) >= LOTE_DE_EVENTOS else teto
        depois = gatilhos_dinamicos.formatar_cursor_de_evento(novo)
        if not casados or self._sem_mais_ocorrencias(g, agora):
            with self.lideranca.cercada(PEDIDOS, token):
                self.repo.cas_cursor(g["id"], antes, depois)
            return
        ultima = self.repo.ultima_do_gatilho(g["id"])
        piso = self.cfg.piso_agir_s if g["pedido_autonomia"] == "agir" else self.cfg.piso_observar_s
        if ultima is not None and parse_iso(ultima["previsto_para"]) + timedelta(seconds=piso) > agora:
            return              # dentro do piso da autonomia: os eventos esperam (e coalescem) sem mover o cursor
        instante = truncar(agora)
        ids = [int(e["id"]) for e in casados]
        motivo = gatilhos_dinamicos.motivo_do_evento(ids, (e["kind"] for e in casados))
        em = to_iso(agora)
        try:
            with self.lideranca.cercada(PEDIDOS, token):
                atual = self.repo.pedido(g["pedido_id"])
                if atual is None or atual["estado"] != "ativo" or not self.repo.cas_cursor(g["id"], antes, depois):
                    return      # pausado/cancelado no meio, ou outro laço já leu estes eventos
                chave = chave_da_ocorrencia(g["pedido_id"], g["id"], instante, origem="evento")
                if not self.repo.inserir_ocorrencia(pedido_id=g["pedido_id"], versao=atual["versao"], gatilho_id=g["id"],
                                                    previsto_para=formatar_instante(instante), chave=chave,
                                                    origem="evento", estado="devida", motivo=motivo, token=token,
                                                    criada_em=em, terminada_em=None):
                    raise _Colidiu()
                r.materializadas += 1
        except _Colidiu:
            pass                # outra ocorrência do gatilho neste segundo: o cursor volta e a volta seguinte as pega

    def _registrar_buraco(self, g: Row, faixa: tuple[int, int], token: int, agora: datetime, r: Resumo) -> None:
        """A retenção apagou eventos que o gatilho não leu: grava o FATO (memória `pendencia` e aviso), leva o cursor ao
        começo do que existe e NÃO dispara nada pelo buraco (§7.8)."""
        de, ate = faixa
        em = to_iso(agora)
        texto = (f"eventos #{de} a #{ate} foram apagados pela retenção antes de o gatilho {g['id']} os ler; nada foi "
                 "disparado por eles")
        with self.lideranca.cercada(PEDIDOS, token):
            if not self.repo.cas_cursor(g["id"], g["cursor"], gatilhos_dinamicos.formatar_cursor_de_evento(ate)):
                return
            self._lembrar(g, f"evento.buraco.{g['id']}", "pendencia", texto, em)
        r.buracos += 1
        log.warning("pedidos: gatilho %s do pedido %s perdeu os eventos #%s a #%s para a retenção", g["id"],
                    g["pedido_id"], de, ate)
        self._avisar_do_gatilho(g, "eventos_perdidos", f"eventos_perdidos:{g['id']}:{ate}", em,
                                lambda titulo: f"Parte dos eventos que o pedido «{titulo}» acompanha foi apagada antes "
                                "de ser lida; nada foi disparado por eles.", {"de_id": de, "ate_id": ate})

    def _lembrar(self, g: Row, chave: str, tipo: str, valor: str, em: str, ocorrencia_id: str | None = None) -> None:
        """Grava o fato na memória do pedido sem nunca travar o gatilho: memória recusada (tamanho, formato) fica no log,
        e o cursor avança do mesmo jeito (senão o mesmo buraco seria redetectado em toda volta)."""
        try:
            self.relatorios.gravar_memoria(g["pedido_id"], chave, tipo, valor, agora=em, ocorrencia_id=ocorrencia_id)
        except MemoriaInvalida as e:
            log.warning("pedidos: memória %s do gatilho %s recusada: %s", chave, g["id"], e)

    def _avisar_do_gatilho(self, g: Row, tipo: str, chave: str, em: str, mensagem: Callable[[str], str],
                           dados: Mapping[str, object], ocorrencia_id: str | None = None) -> None:
        """O aviso só existe com o tipo no CHECK de `pedido_avisos` (migração do 28.8); antes dela, a memória e o log já
        guardam o fato. `mensagem` recebe o título (texto da pessoa: nunca passa por `str.format`)."""
        if tipo not in dominio_avisos.TIPOS:
            return
        p = self.repo.pedido(g["pedido_id"])
        titulo = (p["titulo"] if p is not None else None) or g["pedido_id"]
        self._emitir(tentativas.aviso(tipo=tipo, aviso_id=chave, pedido_id=g["pedido_id"], pedido_titulo=titulo,
                                      ocorrencia_id=ocorrencia_id, criado_em=em,
                                      mensagem=mensagem(titulo), dados=dados))

    def _gatilho_de_persona(self, g: Row, token: int, agora: datetime, r: Resumo) -> None:
        """A próxima visita nasce quando a anterior FECHOU (§14.3). Uma `prevista` cuja hora chegou passa pelas regras
        de janela de `materializar`, como as da agenda."""
        spec = gatilhos_dinamicos.normalizar("persona", loads(g["spec"], {}) or {})
        p = self.repo.pedido(g["pedido_id"])
        if p is None:
            return
        anterior = self.repo.ultima_do_gatilho(g["id"])
        if anterior is not None and anterior["estado"] in _ABERTAS:
            if anterior["estado"] == "prevista" and parse_iso(anterior["previsto_para"]) <= agora:
                self._decidir_visita(p, g, spec, parse_iso(anterior["previsto_para"]), anterior, token, agora, r)
            return
        if self._sem_mais_ocorrencias(g, agora):
            return
        proposta = None
        if anterior is not None:
            obs = self.repo.observacao(anterior["id"], gatilhos_dinamicos.NOME_DA_PROPOSTA)
            if obs is not None:
                proposta = gatilhos_dinamicos.proposta_valida(obs["tipo"], obs["situacao"], obs["valor"])
        fechou = parse_iso(anterior["terminada_em"]) if anterior is not None and anterior["terminada_em"] else None
        if anterior is not None and fechou is None:
            fechou = parse_iso(anterior["previsto_para"])
        quando = gatilhos_dinamicos.proxima_visita(
            anterior_terminada_em=fechou, criado_em=parse_iso(g["criado_em"]), proposta_s=proposta, spec=spec,
            fim_em=parse_iso(p["fim_em"]) if p["fim_em"] else None)
        if quando is None:
            return
        if anterior is not None and quando <= parse_iso(anterior["previsto_para"]):
            quando = parse_iso(anterior["previsto_para"]) + _UM_SEGUNDO      # o UNIQUE (gatilho, instante) não repete
        self._decidir_visita(p, g, spec, quando, None, token, agora, r)

    def _decidir_visita(self, p: Row, g: Row, spec: dict, quando: datetime, linha: Row | None, token: int,
                        agora: datetime, r: Resumo) -> None:
        d = materializar([quando], agora=agora, janela_s=self._janela(p, g, spec), coalescer=False,
                         tick_s=self.cfg.tick_s)[0]
        em = to_iso(agora)
        fim_de_linha = d.estado in ("pulada", "perdida")
        with self.lideranca.cercada(PEDIDOS, token):
            atual = self.repo.pedido(p["id"])
            if atual is None or atual["estado"] != "ativo":
                return
            if linha is not None:
                if d.estado != "prevista":
                    transicionar_ocorrencia("prevista", d.estado, motivo=d.motivo)
                    self.repo.mover(linha["id"], "prevista", d.estado, motivo=d.motivo,
                                    terminada_em=em if fim_de_linha else None)
                return
            chave = chave_da_ocorrencia(p["id"], g["id"], d.instante, origem="persona")
            if self.repo.inserir_ocorrencia(pedido_id=p["id"], versao=atual["versao"], gatilho_id=g["id"],
                                            previsto_para=formatar_instante(d.instante), chave=chave, origem="persona",
                                            estado=d.estado, motivo=d.motivo, token=token, criada_em=em,
                                            terminada_em=em if fim_de_linha else None):
                r.materializadas += 1
                self.repo.avancar_cursor(g["id"], formatar_instante(d.instante))

    def _gatilho_de_condicao(self, g: Row, token: int, agora: datetime, r: Resumo) -> None:
        """Avalia a condição na observação mais nova (de qualquer ocorrência do pedido) que ainda não deu veredito.
        Borda: só falso → verdadeiro avisa (§14.4). Não cria ocorrência."""
        spec = gatilhos_dinamicos.normalizar("condicao", loads(g["spec"], {}) or {})
        linhas = self.repo.observacoes_recentes(g["pedido_id"], str(spec["observacao"]))
        if not linhas:
            return
        por_ocorrencia: dict[str, list[Row]] = {}
        for x in linhas:
            por_ocorrencia.setdefault(x["ocorrencia_id"], []).append(x)
        ordem = list(por_ocorrencia)                      # já vem da mais nova para a mais velha
        atual_id = ordem[0]
        cursor = gatilhos_dinamicos.cursor_de_condicao(g["cursor"])
        if cursor is not None and cursor[1] == atual_id:
            return                                         # esta ocorrência já deu o veredito
        anteriores = {x["alvo"]: x for x in por_ocorrencia[ordem[1]]} if len(ordem) > 1 else {}
        vereditos = []
        for x in por_ocorrencia[atual_id]:
            ant = anteriores.get(x["alvo"])
            vereditos.append(gatilhos_dinamicos.avaliar(spec, _observada(x), _observada(ant) if ant else None))
        veredito = True if True in vereditos else (False if False in vereditos else None)
        if veredito is None:
            return                                         # incerto/ausente: sem veredito, o cursor fica
        dispara = gatilhos_dinamicos.disparou(veredito, cursor)
        em = to_iso(agora)
        with self.lideranca.cercada(PEDIDOS, token):
            if not self.repo.cas_cursor(g["id"], g["cursor"],
                                        gatilhos_dinamicos.formatar_cursor_de_condicao(veredito, atual_id)):
                return
            if dispara:
                self._lembrar(g, f"condicao.{g['id']}", "descoberta",
                              f"{gatilhos_dinamicos.descrever('condicao', spec)}: atendida na ocorrência {atual_id}", em,
                              ocorrencia_id=atual_id)
        if dispara:
            r.condicoes += 1
            regra = gatilhos_dinamicos.descrever("condicao", spec).removeprefix("Avisa quando ")
            self._avisar_do_gatilho(g, "condicao_atendida", f"condicao_atendida:{g['id']}:{atual_id}", em,
                                    lambda titulo: f"A condição do pedido «{titulo}» foi atendida: {regra}.",
                                    {"gatilho_id": g["id"], "observacao": spec["observacao"], "op": spec["op"]},
                                    ocorrencia_id=atual_id)

    # ------------------------------------------------------------------ 3. despacho
    def _despachar(self, token: int, agora: datetime, r: Resumo) -> None:
        por_pedido: dict[str, list[Row]] = {}
        for o in self.repo.devidas():
            por_pedido.setdefault(o["pedido_id"], []).append(o)
        self._adiamento = self._motivo_de_saldo() if por_pedido else None
        criacoes = 0
        for pedido_id, devidas in por_pedido.items():
            p = self.repo.pedido(pedido_id)
            if p is None or p["estado"] != "ativo":
                continue                # pausado/cancelado: `_fechar_dos_parados`; os outros esperam a pessoa
            try:
                criacoes += self._despachar_pedido(p, devidas, token, agora, r, self.cfg.lote_max - criacoes)
            except TravaPerdida:
                raise
            except Exception:  # noqa: BLE001
                log.exception("pedidos: despacho do pedido %s", pedido_id)
                r.erros += 1
            if criacoes >= self.cfg.lote_max:
                break                   # o resto fica `devida` para a volta seguinte (R3: o laço de eventos é um só)

    def _despachar_pedido(self, p: Row, devidas: list[Row], token: int, agora: datetime, r: Resumo,
                          orcamento: int) -> int:
        devidas = self._liberadas_pela_dependencia(p, devidas, token, agora, r)
        if not devidas:
            return 0
        plano = decidir(p["sobreposicao"], p["autonomia"], self.repo.ids_das_abertas(p["id"]),
                        [Devida(o["id"], o["previsto_para"], o["chave"]) for o in devidas])
        if plano.incoerente:
            log.warning("pedidos: pedido %s pede a sobreposição %s com autonomia %s; aplicada: pular",
                        p["id"], p["sobreposicao"], p["autonomia"])
        despachar, pular = list(plano.despachar), list(plano.pular)
        if p["max_ocorrencias"] is not None:
            # Uma nova tentativa (28.5) já virou execução: `executadas` a conta, e o máximo não a barra.
            retentativas = {o["id"] for o in devidas if int(o["tentativa"] or 0) > 0}
            restantes = max(0, int(p["max_ocorrencias"]) - self.repo.executadas(p["id"]))
            motivo = f"máximo de ocorrências atingido ({p['max_ocorrencias']})"
            novas = [i for i in despachar if i not in retentativas]
            pular += [(i, motivo) for i in novas[restantes:]]
            cortadas = set(novas[restantes:])
            despachar = [i for i in despachar if i not in cortadas]
            if restantes == 0:          # a guardada também: nada mais vira execução
                ja = {i for i, _ in pular}
                pular += [(o["id"], motivo) for o in devidas if o["id"] not in ja and o["id"] not in retentativas]
        self._pular(pular, token, agora, r)
        if p["orcamento_total_usd"] is not None:
            # O que sobra do orçamento total deixa despachar só `cabem` ocorrências nesta volta; o resto fica `devida`
            # e a volta seguinte, já com o custo das fechadas, decide (ou encerra o pedido: `_orcamentos`).
            cabem = quantas_cabem(*self._situacao_do_orcamento(p))
            if cabem is not None:
                despachar = despachar[:cabem]
        criou = 0
        por_id = {o["id"]: o for o in devidas}
        for oid in despachar:
            if criou >= orcamento:
                break
            if self._em_espera(por_id[oid], agora):
                continue                # a nova tentativa (28.5) ainda não chegou na hora: segura o lugar, sem gastar nada
            if self._adiamento is not None:
                self._adiar(p, por_id[oid], self._adiamento, token, agora, r)
                continue
            if self._despachar_uma(p, por_id[oid], token, agora, r):
                criou += 1
        return criou

    def _liberadas_pela_dependencia(self, p: Row, devidas: list[Row], token: int, agora: datetime, r: Resumo) -> list[Row]:
        """28.10 F2: das `devida` do pedido, as que já podem ir à sobreposição e ao despacho.

        Pedido que é `para` em `pedido_dependencias` só despacha uma ocorrência quando cada `de` está comprovado NA JANELA
        dela: a janela abre no fim da última ocorrência terminada do próprio pedido (ou na criação dele) e a prova é uma
        ocorrência do `de` que terminou DEPOIS disso e num estado que comprova o tipo (`colaboracao.ESTADOS_QUE_COMPROVAM`:
        `precisa_de_resultado` só `concluida`; `depois_de` qualquer fim). Sem prova a ocorrência fica `devida`, sem
        execução e sem gastar tentativa, e passada `espera_dependencia_s` desde o `previsto_para` vira `pulada` com o
        motivo `dependência não comprovada: <id do de>` (só o id: nunca título nem objetivo).

        Fora desta função o laço é o de sempre: com `colaboracao.enabled` desligada, nem a tabela é lida (a dependência
        gravada direto no banco não segura nada). A nova tentativa (28.5, `tentativa > 0`) passa direto: o despacho dela já
        foi autorizado pela dependência, e segurar a repetição de uma ocorrência que já rodou só a faria expirar."""
        if not self.cfg.colaboracao.enabled:
            return devidas
        dependencias = self.repo.dependencias_do_pedido(p["id"])
        novas = [o for o in devidas if int(o["tentativa"] or 0) == 0]
        if not dependencias or not novas:
            return devidas
        ultimo = self.repo.fim_mais_recente(p["id"], OCORRENCIA_TERMINAIS)
        inicio = colaboracao.inicio_da_janela(parse_iso(ultimo) if ultimo else None, parse_iso(p["criado_em"]))
        fins: dict[str, dict[str, datetime | None]] = {}
        for d in dependencias:
            estados = colaboracao.ESTADOS_QUE_COMPROVAM.get(d["tipo"])
            fim = self.repo.fim_mais_recente(d["de"], estados) if estados else None
            fins.setdefault(d["de"], {})[d["tipo"]] = parse_iso(fim) if fim else None
        faltam = colaboracao.pendentes([(d["de"], d["tipo"]) for d in dependencias], fins, inicio)
        if not faltam:
            return devidas
        espera = self.cfg.colaboracao.espera_dependencia_s
        vencidas = [o for o in novas if colaboracao.espera_vencida(parse_iso(o["previsto_para"]), agora, espera)]
        self._pular([(o["id"], colaboracao.motivo_da_dependencia(faltam[0])) for o in vencidas], token, agora, r)
        fora = {o["id"] for o in novas}
        r.seguradas += len(novas) - len(vencidas)
        return [o for o in devidas if o["id"] not in fora]

    def _motivo_de_saldo(self) -> str | None:
        """O saldo adia, nunca falha: erro ao ler o saldo não segura o despacho (a execução já tem a própria barreira
        no `AIRouter`, que recusa a chamada de conta bloqueada)."""
        if self.adiar_por_saldo is None:
            return None
        try:
            return self.adiar_por_saldo()
        except Exception:  # noqa: BLE001
            log.exception("pedidos: leitura do saldo das contas de IA")
            return None

    @staticmethod
    def _em_espera(o: Row, agora: datetime) -> bool:
        """`devida` com `tentativa > 0`: é a nova tentativa do 28.5, e `terminada_em` guarda o `nao_antes_de`
        (`RepositorioDePedidos.retentar`). Antes dele ninguém a despacha, adia ou perde."""
        return int(o["tentativa"] or 0) > 0 and bool(o["terminada_em"]) and parse_iso(o["terminada_em"]) > agora

    def _limite_da_janela(self, p: Row, o: Row) -> datetime:
        """Até quando a ocorrência `devida` pode esperar para virar execução: `previsto_para + J` (J = a janela, no
        mínimo `tick_s`). Numa nova tentativa a conta parte do `nao_antes_de`, não do instante previsto (que já passou)."""
        g = next((x for x in self.repo.gatilhos_ativos(p["id"]) if x["id"] == o["gatilho_id"]), None)
        try:
            janela = self._janela(p, g, loads(g["spec"], {}) if g else {})
        except gatilhos.ErroDeGatilho:
            janela = self.cfg.janela_padrao_s
        base = parse_iso(o["previsto_para"])
        if int(o["tentativa"] or 0) > 0 and o["terminada_em"]:
            base = max(base, parse_iso(o["terminada_em"]))
        return base + timedelta(seconds=max(janela, self.cfg.tick_s))

    def _adiar(self, p: Row, o: Row, motivo: str, token: int, agora: datetime, r: Resumo) -> None:
        """A ocorrência FICA `devida` (sem reserva, sem execução, sem tentativa gasta: adiar não é falha) e o motivo vai
        em `resumo`, só quando mudou, para a volta de 15 s não reescrever a mesma linha. Passou da janela
        (`previsto_para + J`, §10 do desenho), vira `perdida` COM o motivo do saldo: esperar para sempre esconderia o
        atraso, e a `perdida` é visível e diz por quê (§7.5)."""
        if truncar(agora) > self._limite_da_janela(p, o):
            perdida = f"adiada por saldo além da janela: {motivo}"[:500]
            transicionar_ocorrencia("devida", "perdida", motivo=perdida)
            with self.lideranca.cercada(PEDIDOS, token):
                if self.repo.mover(o["id"], "devida", "perdida", motivo=perdida, terminada_em=to_iso(agora)):
                    r.perdidas += 1
            return
        texto = f"adiada: {motivo}"[:500]
        if o["resumo"] != texto:
            self.repo.soltar_reserva(o["id"], texto)
            log.info("pedidos: ocorrência %s segue devida (%s)", o["id"], texto)
        r.adiadas += 1

    def _pular(self, pular: list[tuple[str, str]], token: int, agora: datetime, r: Resumo, *,
               de: str = "devida") -> None:
        for oid, motivo in pular:
            transicionar_ocorrencia(de, "pulada", motivo=motivo)
            antes = self.repo.ocorrencia(oid)
            if antes is not None and antes["origem"] == "evento" and antes["motivo"]:
                # O motivo da ocorrência de evento é a faixa de eventos que a gerou (28.8): a razão do pulo se ANEXA a
                # ela, senão a pessoa vê que pulou mas não o que tinha acontecido.
                motivo = f"{motivo}; eventos: {antes['motivo']}"[:500]
            with self.lideranca.cercada(PEDIDOS, token):
                if self.repo.mover(oid, de, "pulada", motivo=motivo, terminada_em=to_iso(agora)):
                    r.puladas += 1

    def _requisicao(self, p: Row, chave: str) -> RunCreate:
        """Os alvos do pedido (`pedidos.alvos`, foto no formato de `runs.targets`) viram os de `RunCreate`. Alvo com
        persona vai em `targets`; sem persona, em `instance_ids`. Chegam explícitos: nenhum tem origem `texto`."""
        alvos = loads(p["alvos"], None)
        politica = "one"
        if isinstance(alvos, dict):
            politica = alvos.get("device_policy") or "one"
            alvos = alvos.get("alvos")
        instance_ids: list[str] = []
        targets: list[RunTarget] = []
        for a in alvos or []:
            iid, perfil = a.get("instance_id"), a.get("profile_id")
            if perfil:
                targets.append(RunTarget(profile_id=perfil, instance_ids=[iid] if iid else [], app_id=a.get("app_id")))
            elif iid:
                instance_ids.append(iid)
        if not (instance_ids or targets):
            raise ValueError("o pedido não tem alvos")
        return RunCreate(command=p["objetivo"], instance_ids=instance_ids, targets=targets, device_policy=politica,
                         mode="execute", idempotency_key=chave)

    def _despachar_uma(self, p: Row, o: Row, token: int, agora: datetime, r: Resumo) -> bool:
        if not self.repo.reservar(o["id"], self.lideranca.dono, to_iso(agora + timedelta(seconds=self.cfg.posse_s)),
                                  to_iso(agora)):
            return False                # outro laço tem a posse
        n = int(o["tentativa"]) + 1     # a coluna só muda no CAS abaixo: uma queda antes dele repete a MESMA chave
        chave_exec = chave_da_tentativa(o["chave"], n)
        existente = self.repo.execucao_pela_chave(chave_exec)
        if existente is not None:
            run_id = existente["id"]    # A2: nunca chamar `create` de novo (o pré-voo poderia recusar uma execução que existe)
        else:
            try:
                run_id = self.runs.create(self._requisicao(p, chave_exec), origem=(p["id"], o["id"]),
                                          prioridade=self._prioridade(p)).id
            except RunError as e:
                self._criacao_falhou(p, o, token, agora, r, f"{e.code}: {e.message}")
                return False
            except ValueError as e:
                self._criacao_falhou(p, o, token, agora, r, f"pedido_invalido: {e}")
                return False
            except Exception as e:  # noqa: BLE001 - erro inesperado: a ocorrência fica `devida` e a próxima volta tenta
                log.exception("pedidos: criação da execução da ocorrência %s", o["id"])
                self._criacao_falhou(p, o, token, agora, r, f"erro_inesperado: {type(e).__name__}")
                return False
        transicionar_ocorrencia("devida", "despachada")
        with self.lideranca.cercada(PEDIDOS, token):
            if self.repo.marcar_despachada(o["id"], run_id, n):
                r.despachadas += 1
        return True

    @staticmethod
    def _prioridade(p: Row) -> int:
        """`runs.prioridade` da execução desta ocorrência: o padrão, até o pedido ter o campo (a 067 não o criou)."""
        return PRIORIDADE_PADRAO

    def _criacao_falhou(self, p: Row, o: Row, token: int, agora: datetime, r: Resumo, texto: str) -> None:
        """A ocorrência fica `devida`, solta a reserva e grava o último motivo; passou de `previsto_para + J` (J = a
        janela, no mínimo `tick_s`), vira `perdida`."""
        texto = texto[:500]
        if truncar(agora) > self._limite_da_janela(p, o):
            motivo = f"não foi possível criar a execução: {texto}"
            transicionar_ocorrencia("devida", "perdida", motivo=motivo)
            with self.lideranca.cercada(PEDIDOS, token):
                self.repo.mover(o["id"], "devida", "perdida", motivo=motivo, terminada_em=to_iso(agora))
            r.erros += 1
            return
        self.repo.soltar_reserva(o["id"], texto)
        log.info("pedidos: ocorrência %s segue devida (%s)", o["id"], texto)

    # ------------------------------------------------------------------ 4. agenda e encerramento
    def _agendar(self, token: int, agora: datetime, r: Resumo) -> None:
        horizonte = formatar_instante(agora + timedelta(seconds=self.cfg.horizonte_s))
        for p in self.repo.pedidos_para_cuidar(horizonte):
            try:
                self._agendar_pedido(p, token, agora, r)
            except TravaPerdida:
                raise
            except Exception:  # noqa: BLE001
                log.exception("pedidos: agenda do pedido %s", p["id"])
                r.erros += 1

    def _agendar_pedido(self, p: Row, token: int, agora: datetime, r: Resumo) -> None:
        atingiu = p["max_ocorrencias"] is not None and self.repo.executadas(p["id"]) >= p["max_ocorrencias"]
        if atingiu:
            motivo = f"máximo de ocorrências atingido ({p['max_ocorrencias']})"
            for linha in self.repo.ids_prevista_devida(p["id"], sem_retentativas=True):
                self._pular([(linha["id"], motivo)], token, agora, r, de=linha["estado"])
        proximos: list[datetime] = []
        motivos: list[str] = []
        suportados = esgotados = 0
        vivo_fora_do_28_4 = False
        fim = parse_iso(p["fim_em"]) if p["fim_em"] else None
        passou_do_fim = fim is not None and agora >= fim
        for g in self.repo.gatilhos_ativos(p["id"]):
            if g["tipo"] not in gatilhos.SUPORTADOS:
                # Evento e persona (28.8) não têm "próximo instante" de regra: vivem até `fim_em` (ou o máximo de
                # ocorrências). A condição só avisa e não segura o pedido sozinha.
                if passou_do_fim:
                    suportados += 1
                    esgotados += 1
                    motivos.append("prazo")
                elif g["tipo"] != "condicao":
                    vivo_fora_do_28_4 = True
                continue
            suportados += 1
            spec = loads(g["spec"], {}) or {}
            try:
                prox = gatilhos.proximo(g["tipo"], spec, fuso=p["fuso"], criado_em=parse_iso(g["criado_em"]),
                                        depois_de=self.repo.cursor(g))
                por = gatilhos.esgota_por(g["tipo"], spec)
            except gatilhos.ErroDeGatilho:
                vivo_fora_do_28_4 = True        # spec inválida: não encerra o pedido por isso
                continue
            if prox is not None and fim is not None and prox > fim:
                prox, por = None, "prazo"
            if prox is None:
                esgotados += 1
                motivos.append(por)
            else:
                proximos.append(prox)
        previsto = self.repo.menor_prevista(p["id"])
        candidatos = [formatar_instante(x) for x in proximos] + ([previsto] if previsto else [])
        proxima_em = None if atingiu or not candidatos else min(candidatos)
        esgotado = atingiu or (suportados > 0 and not vivo_fora_do_28_4 and esgotados == suportados)
        if esgotado and self.repo.quantas_em_aberto(p["id"]) == 0:
            motivo_fim = "contagem" if atingiu else ("prazo" if "prazo" in motivos else "contagem")
            transicionar_pedido("ativo", "encerrado", ator="sistema", motivo=motivo_fim)
            relatorio = self._relatorio_final(p)
            with self.lideranca.cercada(PEDIDOS, token):
                if self.repo.mudar_estado_do_pedido(p["id"], "ativo", "encerrado", to_iso(agora), versao=p["versao"],
                                                    encerrado_motivo=motivo_fim) and relatorio is not None:
                    self.relatorios.gravar_relatorio(relatorio)       # encerrar gera o relatório final (§6.5), atômico
            return
        with self.lideranca.cercada(PEDIDOS, token):
            self.repo.definir_proxima_em(p["id"], p["versao"], proxima_em, to_iso(agora))

    def _relatorio_final(self, p: Row):
        """O relatório de encerramento, montado antes de a transação abrir. Falha não impede o encerramento: o relatório
        sai depois, sob demanda (`ServicoDeRelatorios.gerar`)."""
        try:
            return self.relatorios.preparar_relatorio(p, gatilho="encerramento")
        except Exception:  # noqa: BLE001
            log.exception("pedidos: relatório de encerramento do pedido %s", p["id"])
            return None

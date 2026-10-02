"""Gestos da pessoa sobre o pedido: pausar, retomar, cancelar e editar (docs/design/pedidos-laco.md §6).

A API do 28.9 chama estas funções; aqui só estão as REGRAS. Cada uma confere a transição no domínio
(`transicionar_pedido`, com o ator), faz o CAS do estado do pedido e as escritas das ocorrências na MESMA transação, e
acorda o laço. Uma queda no meio não deixa estado impossível: o laço (`_fechar_dos_parados`) completa o que sobrou
(pedido `cancelado` com ocorrência ainda `devida`, por exemplo).

* **Pausar** (`ativo → pausado`, motivo obrigatório): `prevista`/`devida` → `pulada` (`pedido pausado`); as abertas
  terminam. O laço só materializa `estado='ativo'`.
* **Retomar** (`pausado → ativo`, só a pessoa): `daqui` (padrão) materializa o que a pausa atravessou como `pulada`
  (`pausado: retomado daqui`) e põe o cursor em agora; `recuperar` só zera `proxima_em` e deixa o laço aplicar janela
  e coalescência normalmente.
* **Cancelar** (pessoa): `prevista`/`devida` → `cancelada`; cada aberta recebe `RunService.cancel` e fecha pela
  varredura como `cancelada` (ou `incerta`).
* **Editar** (`versao + 1`, D5/A6): campos do pedido mudam e as `prevista`/`devida` passam à versão nova NA MESMA LINHA
  (nunca cancelar e recriar: a chave não tem versão e o `ON CONFLICT DO NOTHING` engoliria a nova). Mudar a
  recorrência ou o horário NÃO edita a `spec`: desativa o gatilho (as `prevista`/`devida` dele → `cancelada`, motivo
  `edição`) e cria um gatilho NOVO, com id novo e, portanto, chaves novas. Despachadas terminam na versão em que nasceram.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING

from app.db import Row, loads
from app.modules.pedidos.infrastructure.relatorios import ServicoDeRelatorios
from app.modules.pedidos.infrastructure.repositorio import RepositorioDePedidos, novo_id
from app.modules.pedidos.domain import gatilhos
from app.modules.pedidos.domain.chave import chave_da_ocorrencia, formatar_instante
from app.modules.pedidos.domain.estados import ATOR_PESSOA, ATOR_SISTEMA, transicionar_ocorrencia, transicionar_pedido
from app.modules.pedidos.domain.materializar import truncar
from app.util import parse_iso, to_iso

if TYPE_CHECKING:  # só para a anotação: o serviço de execuções vem de quem monta o laço
    from app.taskqueue.service import RunService

log = logging.getLogger("poc.pedidos")

#: Campos do pedido que a pessoa edita sem mexer em gatilho. Alvo e autonomia ficam de fora do 28.4 (a prévia dos alvos
#: e o teto de autonomia são do 28.9).
CAMPOS_EDITAVEIS: frozenset[str] = frozenset({"titulo", "objetivo", "contexto", "sobreposicao", "janela_recuperacao_s",
                                              "coalescer", "fim_em", "max_ocorrencias",
                                              # 28.9: a API valida (prévia dos alvos, teto de autonomia, piso) ANTES de chamar
                                              "criterios_sucesso", "alvos", "autonomia", "fuso", "inicio_em",
                                              "orcamento_total_usd", "orcamento_ocorrencia_usd", "max_tentativas",
                                              "pausa_por_falha"})
MODOS_DE_RETOMADA = ("daqui", "recuperar")


class AcaoInvalida(ValueError):
    """O pedido não existe, mudou de estado no meio ou o campo não é editável: a mensagem é para a pessoa."""


class AcoesDePedidos:
    def __init__(self, repo: RepositorioDePedidos, runs: RunService, relogio: Callable[[], datetime],
                 acordar: Callable[[], None], relatorios: ServicoDeRelatorios | None = None):
        self.repo = repo
        self.relatorios = relatorios
        self.runs = runs
        self.relogio = relogio
        self.acordar = acordar

    def _pedido(self, pedido_id: str) -> Row:
        p = self.repo.pedido(pedido_id)
        if p is None:
            raise AcaoInvalida(f"pedido {pedido_id} não existe")
        return p

    # ------------------------------------------------------------------ pausar
    def pausar(self, pedido_id: str, motivo: str, *, ator: str = ATOR_PESSOA) -> None:
        p = self._pedido(pedido_id)
        transicionar_pedido(p["estado"], "pausado", ator=ator, motivo=motivo)
        em = to_iso(self.relogio())
        with self.repo.db.tx():
            if not self.repo.mudar_estado_do_pedido(pedido_id, p["estado"], "pausado", em, pausado_motivo=motivo,
                                                pessoa=ator == ATOR_PESSOA):
                raise AcaoInvalida(f"pedido {pedido_id} mudou de estado no meio")
            for linha in self.repo.ids_prevista_devida(pedido_id):
                transicionar_ocorrencia(linha["estado"], "pulada", motivo="pedido pausado")
                self.repo.mover(linha["id"], linha["estado"], "pulada", motivo="pedido pausado", terminada_em=em)
        self.acordar()

    # ------------------------------------------------------------------ retomar
    def retomar(self, pedido_id: str, modo: str = "daqui") -> None:
        if modo not in MODOS_DE_RETOMADA:
            raise AcaoInvalida(f"modo de retomada desconhecido: {modo!r} (esperado um de {list(MODOS_DE_RETOMADA)})")
        p = self._pedido(pedido_id)
        transicionar_pedido(p["estado"], "ativo", ator=ATOR_PESSOA)
        agora = self.relogio()
        em = to_iso(agora)
        with self.repo.db.tx():
            if not self.repo.mudar_estado_do_pedido(pedido_id, p["estado"], "ativo", em, pessoa=True):
                raise AcaoInvalida(f"pedido {pedido_id} mudou de estado no meio")
            if modo == "daqui":
                for g in self.repo.gatilhos_ativos(pedido_id):
                    self._pular_o_da_pausa(p, g, agora, em)
                # Evento (28.8): pausa = não observar; o que aconteceu durante ela não dispara na retomada `daqui`.
                self.repo.base_dos_eventos(pedido_id)
        self.acordar()

    def _pular_o_da_pausa(self, p: Row, g: Row, agora: datetime, em: str) -> None:
        if g["tipo"] not in gatilhos.SUPORTADOS:
            return
        spec = loads(g["spec"], {}) or {}
        try:
            atravessados = gatilhos.instantes(g["tipo"], spec, fuso=p["fuso"], criado_em=parse_iso(g["criado_em"]),
                                              depois_de=self.repo.cursor(g), ate=truncar(agora), limite=1000)
        except gatilhos.ErroDeGatilho as e:
            log.warning("pedidos: gatilho %s com spec inválida na retomada: %s", g["id"], e)
            return
        for i in atravessados:
            self.repo.inserir_ocorrencia(pedido_id=p["id"], versao=p["versao"], gatilho_id=g["id"],
                                         previsto_para=formatar_instante(i),
                                         chave=chave_da_ocorrencia(p["id"], g["id"], i), origem="agenda",
                                         estado="pulada", motivo="pausado: retomado daqui", token=None,
                                         criada_em=em, terminada_em=em)
        self.repo.avancar_cursor(g["id"], formatar_instante(agora))

    # ------------------------------------------------------------------ cancelar
    def cancelar(self, pedido_id: str, *, por: str | None = None) -> None:
        p = self._pedido(pedido_id)
        transicionar_pedido(p["estado"], "cancelado", ator=ATOR_PESSOA)
        em = to_iso(self.relogio())
        with self.repo.db.tx():
            if not self.repo.mudar_estado_do_pedido(pedido_id, p["estado"], "cancelado", em, pessoa=True):
                raise AcaoInvalida(f"pedido {pedido_id} mudou de estado no meio")
            for linha in self.repo.ids_prevista_devida(pedido_id):
                transicionar_ocorrencia(linha["estado"], "cancelada", motivo="pedido cancelado")
                self.repo.mover(linha["id"], linha["estado"], "cancelada", motivo="pedido cancelado", terminada_em=em)
            abertas = self.repo.execucoes_abertas(pedido_id)
        self._relatorio_final(pedido_id)
        for run_id in abertas:          # fora da transação: `cancel` abre a própria; a varredura fecha a ocorrência
            try:
                self.runs.cancel(run_id, por=por)
            except Exception as e:  # noqa: BLE001 - já terminou, ou outra aba cancelou: a varredura fecha como estiver
                log.info("pedidos: execução %s não pôde ser cancelada com o pedido (%s)", run_id, e)
        self.acordar()

    def _relatorio_final(self, pedido_id: str) -> None:
        """Cancelar também encerra (§6.5): o relatório final sai logo depois do cancelamento, já com as ocorrências
        `prevista`/`devida` canceladas. O que ainda roda aparece em "não coberto" como em aberto; o que essas execuções
        ainda observarem entra nos relatórios sob demanda seguintes. Falha aqui nunca desfaz o cancelamento."""
        if self.relatorios is None:
            return
        try:
            self.relatorios.gerar(pedido_id, gatilho="encerramento")
        except Exception:  # noqa: BLE001
            log.exception("pedidos: relatório de encerramento do pedido %s", pedido_id)

    # ------------------------------------------------------------------ editar
    def editar(self, pedido_id: str, campos: Mapping[str, object] | None = None, *, gatilho: tuple[str, Mapping] | None = None,
               ator: str = ATOR_PESSOA) -> int:
        """Devolve a versão nova. `gatilho=(tipo, spec)` troca a recorrência/horário (gatilho novo, D5)."""
        campos = dict(campos or {})
        fora = set(campos) - CAMPOS_EDITAVEIS
        if fora:
            raise AcaoInvalida(f"campo(s) que a edição não muda: {sorted(fora)}")
        if gatilho is not None and gatilho[0] not in gatilhos.SUPORTADOS:
            raise AcaoInvalida(f"tipo de gatilho fora do 28.4: {gatilho[0]}")
        p = self._pedido(pedido_id)
        if p["estado"] in ("concluido", "encerrado", "cancelado"):
            raise AcaoInvalida(f"pedido {p['estado']} não se edita")
        agora = self.relogio()
        em = to_iso(agora)
        versao = int(p["versao"]) + 1
        with self.repo.db.tx():
            sets = ", ".join(f"{c}=?" for c in campos)
            cur = self.repo.db.execute(
                f"UPDATE pedidos SET {sets + ', ' if sets else ''}versao=?, atualizado_em=?, proxima_em=NULL"
                " WHERE id=? AND versao=?", (*campos.values(), versao, em, pedido_id, p["versao"]))
            if (cur.rowcount or 0) != 1:
                raise AcaoInvalida(f"pedido {pedido_id} foi editado por outro gesto; releia e tente de novo")
            if gatilho is not None:
                for velho in self.repo.gatilhos_ativos(pedido_id):
                    if velho["tipo"] not in gatilhos.SUPORTADOS:
                        continue
                    for linha in self.repo.ids_prevista_devida(pedido_id, velho["id"]):
                        transicionar_ocorrencia(linha["estado"], "cancelada", motivo="edição")
                        self.repo.mover(linha["id"], linha["estado"], "cancelada", motivo="edição", terminada_em=em)
                    self.repo.db.execute("UPDATE pedido_gatilhos SET ativo=0 WHERE id=?", (velho["id"],))
                # O gatilho novo nasce com o cursor em agora: o que a regra nova teria pedido ANTES da edição não é
                # recuperado (nunca existiu para a pessoa).
                self.repo.inserir_gatilho(novo_id("g"), pedido_id, gatilho[0], json.dumps(dict(gatilho[1])),
                                          formatar_instante(agora), em)
            self.repo.trocar_versao_das_abertas(pedido_id, versao)
            self.repo.marcar("pedido", pedido_id, pessoa=True)
        self.acordar()
        return versao


__all__ = ["AcaoInvalida", "AcoesDePedidos", "CAMPOS_EDITAVEIS", "MODOS_DE_RETOMADA", "ATOR_SISTEMA"]

"""Convergência da rede por aparelho (ADR-056, item 25.4): QUANDO aplicar, conferir, reiniciar e desfazer.

O modelo (`devices/rede.py`) guarda o desejado × observado e só muda de estado com evidência; a receita no aparelho
(`devices/rede_aplicacao.py`) sabe O QUE fazer; o servidor do central (`devices/rede_servidor.py`) sabe quem são os
pares. Este módulo decide quando, sempre num ponto seguro (o aparelho livre, pela fila dele) e sempre deixando
rastro (comando `device.network` no histórico do aparelho; reinício como comando `restart`):

| estado da linha | o que a convergência faz |
|---|---|
| `pendente`, pedido com VPN/proxy | **aplicar**: plano, chave e par no servidor, cliente pela loja, receita; → `configurado` e pede o reinício |
| `pendente`, pedido vazio (tirou tudo) | **desfazer**: tira always-on e bloqueio; → `configurado` e pede o reinício |
| `configurado` | **conectar**: lê como uid 2000; túnel no ar → `conectado`; sem reinício desde a configuração → pede o reinício; reiniciou e o túnel não subiu → tenta de novo; passados `rede.reinicios_max` reinícios PEDIDOS na revisão (aceitos ou recusados, com boot detectado ou não), `pendente` com erro. No desfazer: removido → a linha sai |
| `conectado`, `parcial` | **verificar** (25.5; ao ligar, a pedido, pela porta da tarefa, e na varredura quando vence `rede.deriva_s` ou, no `parcial`, `rede.sonda.reverificar_s`): relê como o conferir e, com o túnel no ar, roda a sonda de saída (`rede_medicao.medir`) e grava a medição — só ela leva a `trafego_verificado` ou `parcial`. Com bloqueio, antes, o teste de vazamento da revisão (`rede_medicao.sondar_vazamento`: o cliente VPN parado); sem o túnel de volta, → `configurado` e reinicia (o boot religa o cliente), e a medição vem depois |
| `trafego_verificado` | **conferir** (ao ligar, ao acordar, depois do reinício do backend e a cada `rede.deriva_s`): configuração que sumiu → `pendente` (reaplica); túnel caído com a configuração no lugar → `configurado` (reinicia). Com a verificação pedida (`POST …/verify`), **verificar** no próximo ponto seguro. Com política exigida, **verificar** também quando a medição vence (`rede.validade_verificacao_s`, item 25.6) ou não cobre um app exigido hoje (conta vinculada depois): a varredura mede de novo um pouco antes do vencimento, com o aparelho livre; a porta da tarefa, se já não vale |

Quem chama:
- `vitrine.trabalho_ao_ligar` (aparelho que entrou no ar: boot, wake, readoção depois do reinício do backend ou do
  worker, e a varredura de 60 s dos aparelhos ligados e livres);
- a porta da rede do scheduler (`motivo_de_espera`, contrato C4, ligada no item 25.6): com política exigida, a
  tarefa espera até `trafego_verificado` dentro da validade, e o que falta é disparado antes dela. O scheduler a
  pergunta no despacho e ENTRE AS ETAPAS de um objetivo em curso: a queda observada no meio (deriva, wipe, reatribuição,
  validade vencida) suspende o objetivo no próximo ponto seguro, sem gastar tentativa, até a rede voltar;
- os ganchos de wipe (`invalidar`): disco apagado ou outro aparelho físico atrás do id = a rede aplicada deixou de
  existir;
- `POST /api/network/devices/{id}/apply`: aplica já, se o aparelho estiver livre.

Loja e quarentena ficam fora (nada toca neles); aparelho de conta real só chega aqui depois da confirmação POR
APARELHO na atribuição (ADR-056 §7) e, como todos, só num ponto seguro. Falha não é repetida às cegas: há espera
crescente em memória (5, 15, 45 e 60 min); um reinício do backend zera a memória e dá mais uma chance — é o
"reaplicar depois do reinício do backend".
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from ..commands import despacho
from ..models import ControlOwner, InstanceState
from ..util import now, now_iso, parse_iso
from ..vitrine import objetivo_em_andamento
from . import rede
from .rede_aplicacao import (AparelhoDaRede, AparelhoPeloAdb, Observacao, RedeAplicacaoError, apagar_relatorios_de_falha,
                             desfazer, endereco_no_tunel, montar_plano, observar, provisionar)
from .rede_medicao import Vazamento, contabilidade, medir, sondar_vazamento
from .sonda_rede import Contabilidade

if TYPE_CHECKING:
    from ..config import RedeCfg
    from ..db import Row
    from ..state import AppState
    from .manager import DeviceRuntime

log = logging.getLogger(__name__)

Motivo = Literal["ligou", "varredura", "tarefa", "pedido"]
Acao = Literal["aplicar", "desfazer", "conectar", "conferir", "verificar"]
VERBO = "device.network"
QUEM = "rede"
#: Folga do relógio entre "configurado em" e "ligado há": o `uptime` é em segundos inteiros e o banco em ms.
_FOLGA_S = 5.0
#: A porta da rede é perguntada a cada volta do scheduler (~1 s) por objetivo; a leitura do aparelho, não.
_INTERVALO_DA_PORTA_S = 30.0
#: Depois de uma leitura que falhou ou de um reinício recusado, quanto esperar antes de a varredura tentar de novo.
_ESPERA_DA_RELEITURA_S = 300.0
#: Fração final da validade de um `trafego_verificado` em que a varredura (aparelho livre) já mede de novo: a tarefa
#: que chega depois encontra a medição renovada em vez de esperar por ela.
_ANTECEDENCIA_DA_VALIDADE = 0.1


@dataclass
class _Memoria:
    """O que a convergência lembra de um aparelho ENTRE passadas. Em memória de propósito: o durável é a linha de
    `device_network`; perder isto num reinício só significa conferir de novo e dar mais uma chance à falha."""

    falhas: dict[int, int] = field(default_factory=dict)          # rev → falhas de aplicação
    espera_ate: float = 0.0                                       # monotonic: próxima tentativa automática
    reinicios: dict[int, int] = field(default_factory=dict)       # rev → reinícios pedidos (comando aberto)
    configurado_em: float | None = None                           # epoch da gravação da configuração (ou da queda)
    ultima_conferencia: float | None = None                       # monotonic
    ultima_tentativa: float | None = None                         # monotonic: última passada disparada pela porta
    # A sonda de saída (25.5): o pedido de verificar (POST …/verify), a última medição, a contabilidade de quando o
    # túnel conectou (o começo da janela dos apps, SÓ da mesma revisão) e o teste de vazamento já feito nesta revisão
    # (ele para o cliente VPN e custa um reinício do aparelho: uma vez por revisão, inclusive quando não concluiu —
    # repetir a cada medição reiniciaria o aparelho em laço).
    verificacao_pedida: bool = False
    ultima_verificacao: float | None = None                       # monotonic
    linha_de_base: tuple[int, Contabilidade] | None = None        # (rev, contabilidade)
    vazamento: dict[int, Vazamento] = field(default_factory=dict)  # rev → teste feito com o cliente parado
    # A última medição foi feita SEM abrir os apps exigidos (varredura, ligou): a tarefa que a porta segura mede de
    # novo já, abrindo-os, em vez de esperar `reverificar_s` por uma medição que não podia provar o app parado.
    medida_sem_abrir: bool = False


def _vazio(row: Row) -> bool:
    return not row["vpn_profile_id"] and not row["proxy_profile_id"]


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return parse_iso(str(iso)).timestamp()
    except (ValueError, TypeError):
        return None


class ConvergenciaDeRede:
    """Uma por `AppState`. Síncrona onde é pergunta (porta, ganchos), assíncrona no trabalho no aparelho."""

    def __init__(self, st: AppState, *, aparelho: Callable[[AppState, DeviceRuntime], AparelhoDaRede] | None = None,
                 relogio: Callable[[], float] = time.monotonic) -> None:
        self.st = st
        self._aparelho = aparelho or AparelhoPeloAdb
        self._agora = relogio
        self._mem: dict[str, _Memoria] = {}
        self._reinicio_agendado: set[str] = set()
        #: Quando pedir o reinício depois do trabalho que aplicou (o trabalho precisa ter soltado o aparelho) e de
        #: quanto em quanto tempo tentar de novo enquanto ele segue ocupado.
        self.atraso_do_reinicio_s = 1.0
        self.intervalo_do_reinicio_s = 5.0
        #: Entre uma leitura da tela e a próxima, na importação do perfil (o SFA leva um instante para trocar de tela).
        self.pausa_da_tela_s = 1.5

    # ------------------------------------------------------------------ utilidades
    @property
    def cfg(self) -> RedeCfg:
        return self.st.cfg.file.rede

    def memoria(self, instance_id: str) -> _Memoria:
        return self._mem.setdefault(instance_id, _Memoria())

    def _linha(self, instance_id: str) -> Row | None:
        return self.st.db.one("SELECT * FROM device_network WHERE instance_id=?", (instance_id,))

    def _elegivel(self, rt: DeviceRuntime) -> bool:
        """Loja e quarentena nunca; fora do ar, não há o que fazer agora; com um comando de ciclo de vida em voo (o
        reinício que a própria convergência pediu, por exemplo), espera o desfecho."""
        if rt.store or rt.state != InstanceState.online or self.st.quarentena(rt.id) is not None:
            return False
        return self.st.commands.open_for_instance(rt.id, verbs=despacho.VERBOS_EXCLUSIVOS) is None

    def _acao(self, row: Row, motivo: Motivo, *, limitar: bool = True) -> Acao | None:
        """O passo que a linha pede. `limitar=False` é a releitura de dentro do trabalho já disparado: o intervalo
        da porta vale para DISPARAR, não para quem já foi disparado."""
        mem = self.memoria(str(row["instance_id"]))
        agora = self._agora()
        estado = str(row["state"])
        if limitar and motivo == "tarefa" and mem.ultima_tentativa is not None \
                and agora - mem.ultima_tentativa < _INTERVALO_DA_PORTA_S:
            return None
        if estado == "pendente":
            if motivo != "pedido" and agora < mem.espera_ate:
                return None
            return "desfazer" if _vazio(row) else "aplicar"
        if estado == "configurado":
            # Ao ligar (o boot é justamente o que se espera) e a pedido, sempre; na varredura e pela porta, não com um
            # reinício já agendado nem durante a espera de um reinício recusado ou de uma leitura que falhou — senão
            # cada passada abriria mais um comando para ouvir a mesma resposta.
            if motivo not in ("ligou", "pedido") and (str(row["instance_id"]) in self._reinicio_agendado
                                                      or agora < mem.espera_ate):
                return None
            return "conectar"
        deriva = float(self.cfg.deriva_s or 0)
        venceu_deriva = deriva > 0 and (mem.ultima_conferencia is None or agora - mem.ultima_conferencia >= deriva)
        if estado in ("conectado", "parcial"):
            # Túnel no ar e tráfego ainda não provado: a sonda de saída (25.5). Ela relê como o conferir antes de
            # medir, então é também a conferência destes dois estados.
            if motivo in ("ligou", "pedido") or mem.verificacao_pedida:
                return "verificar"
            if motivo == "tarefa" and estado == "parcial" and mem.medida_sem_abrir:
                # O `parcial` veio de uma medição que não podia abrir o app parado; a tarefa segurada é o que o abre
                # (a sonda abre com a porta como motivo). Uma vez: medida assim, a espera volta a valer.
                return "verificar"
            if agora < mem.espera_ate:
                return None                        # a última medição não verificou: espera `rede.sonda.reverificar_s`
            if motivo == "tarefa":
                return "verificar"
            reverificar = float(self.cfg.sonda.reverificar_s)
            if estado == "parcial" and (mem.ultima_verificacao is None
                                        or agora - mem.ultima_verificacao >= reverificar):
                return "verificar"
            return "verificar" if venceu_deriva else None
        if mem.verificacao_pedida:
            return "verificar"                     # `trafego_verificado` com o POST …/verify: medir de novo
        if self._validade_pede_medicao(row, motivo):
            # A medição venceu (ou está para vencer) com política exigida. A espera de uma medição que falhou vale
            # aqui também: sem ela, a porta dispararia a mesma sonda a cada 30 s. "A pedido" passa por cima.
            if motivo != "pedido" and agora < mem.espera_ate:
                return None
            return "verificar"
        if motivo in ("ligou", "pedido"):
            return "conferir"
        return "conferir" if venceu_deriva else None

    def vencida(self, row: Row) -> bool:
        """`trafego_verificado` com política exigida e a medição fora da validade: para a porta, não vale."""
        return rede.verificacao_vencida(row, float(self.cfg.validade_verificacao_s))

    def invalida(self, row: Row) -> str | None:
        """`vencida`, `apps` (conta vinculada depois da medição) ou `None`: o `trafego_verificado` vale para a porta."""
        return rede.verificacao_invalida(self.st, row)

    def _validade_pede_medicao(self, row: Row, motivo: Motivo) -> bool:
        """A porta (`tarefa`) só mede de novo o que JÁ não vale; quem acha o aparelho livre (varredura, ligou, pedido)
        adianta a remedição para o fim da validade, e a tarefa seguinte não espera."""
        if row["state"] != "trafego_verificado" or row["policy"] == "livre":
            return False
        if self.invalida(row) is not None:
            return True
        validade = float(self.cfg.validade_verificacao_s)
        return motivo != "tarefa" and rede.verificacao_vencida(row, validade * (1 - _ANTECEDENCIA_DA_VALIDADE))

    # ------------------------------------------------------------------ entradas
    def trabalho(self, rt: DeviceRuntime, *, motivo: Motivo = "ligou") -> Callable[[], Awaitable[None]] | None:
        """O trabalho de rede que este aparelho precisa AGORA, ou `None`. Uma consulta quando não há linha — é
        perguntado a cada aparelho que entra no ar e a cada varredura."""
        row = self._linha(rt.id)
        if row is None or not self._elegivel(rt) or self._acao(row, motivo) is None:
            return None
        if motivo == "tarefa":
            self.memoria(rt.id).ultima_tentativa = self._agora()

        async def trabalho() -> None:
            await self.executar(rt, motivo=motivo)

        return trabalho

    async def executar(self, rt: DeviceRuntime, *, motivo: Motivo) -> None:
        """Roda o passo que a linha pede AGORA (relida: entre a pergunta e o trabalho, a atribuição pode ter mudado).
        Falha fica registrada no comando e na linha; aqui só não derruba quem chamou (o trabalho de ligar segue para
        a entrega dos apps)."""
        row = self._linha(rt.id)
        if row is None or not self._elegivel(rt):
            return
        acao = self._acao(row, motivo, limitar=False)
        if acao is None:
            return
        passo = {"aplicar": self._aplicar, "desfazer": self._desfazer, "conectar": self._conectar,
                 "conferir": self._conferir, "verificar": self._verificar}[acao]
        try:
            if acao == "conferir":
                # Leitura, sem efeito: não vira comando. Se achar deriva, a regressão fica na linha e no evento, e o
                # passo seguinte (aplicar ou conectar) é o comando.
                await passo(rt, row, motivo)
                return
            await despacho.comando_no_trabalho(
                self.st, rt, VERBO, lambda: passo(rt, row, motivo),
                params={"acao": acao, "rev": int(row["desired_rev"]), "motivo": motivo}, requested_by=QUEM)
        except Exception as exc:  # noqa: BLE001 - o desfecho já está no comando e na linha
            log.info("%s: rede (%s) não concluiu — %s", rt.id, acao, exc)
            if acao in ("conectar", "verificar"):
                # Leitura que falhou (adb em root, aparelho que não respondeu, sonda truncada): espera antes de
                # repetir. A falha que desistiu de vez já pôs a sua espera em `_falhou`; esta não encurta aquela.
                mem = self.memoria(rt.id)
                mem.espera_ate = max(mem.espera_ate, self._agora() + _ESPERA_DA_RELEITURA_S)

    def motivo_de_espera(self, instance_id: str) -> str | None:
        """A porta da rede do scheduler (contrato C4, item 25.6). `None` = pode seguir. Com política `livre` a
        tarefa não depende da rede; com `exigida`/`exigida_com_bloqueio`, só `trafego_verificado` DENTRO DA VALIDADE
        libera (ADR-056 §3; `rede.validade_verificacao_s`), e o que falta (aplicar, reiniciar, conectar, medir de novo)
        é disparado AQUI, antes da tarefa, se o aparelho estiver livre.

        O scheduler pergunta no despacho e entre as etapas de um objetivo em curso (de dentro do worker: o aparelho
        está em `workers` e nada é disparado daqui — a pergunta é só leitura). A resposta é a mesma nos dois: a queda
        observada (a linha regrediu), a validade vencida ou um app exigido que a medição não provou (conta vinculada
        depois dela, `rede.apps_sem_prova`) seguram a etapa seguinte até a rede voltar. O pacote da TAREFA não chega
        aqui (o C4 recebe só o aparelho): o "por app" é o dos apps das contas vinculadas ao aparelho."""
        row = self._linha(instance_id)
        if row is None or row["policy"] == "livre" \
                or (row["state"] == "trafego_verificado" and self.invalida(row) is None):
            return None
        rt = self.st.devices.devices.get(instance_id)
        if rt is None or rt.store or self.st.quarentena(instance_id) is not None:
            return None                              # a loja não é destino; a quarentena tem a porta dela
        if rt.id not in self.st.scheduler.workers and rt.control == ControlOwner.none:
            trabalho = self.trabalho(rt, motivo="tarefa")
            if trabalho is not None:
                self.st.scheduler.run_device_job(rt, trabalho, label="rede do aparelho antes da tarefa")
        return self.frase_de_espera(row)

    def frase_de_espera(self, row: Row) -> str:
        estado, rev, politica = str(row["state"]), int(row["desired_rev"]), str(row["policy"])
        if estado == "trafego_verificado":
            if self.invalida(row) == "apps":
                apps = ", ".join(rede.apps_sem_prova(self.st, row))
                frase = (f"rede exigida ({politica}): a medição que verificou o aparelho não cobre {apps} (conta "
                         "vinculada depois dela); ")
            else:
                validade_h = float(self.cfg.validade_verificacao_s) / 3600
                idade = rede.idade_da_verificacao(row)
                quando = (f"a última é de {row['verified_at']} (~{idade / 3600:.1f} h)" if idade is not None
                          else "sem data da última medição")
                frase = (f"rede exigida ({politica}): a verificação do tráfego venceu ({quando}; validade "
                         f"{validade_h:g} h); ")
            mem = self.memoria(str(row["instance_id"]))
            if mem.espera_ate > self._agora():
                minutos = max(1, round((mem.espera_ate - self._agora()) / 60))
                return frase + f"a última remedição não concluiu; nova tentativa automática em ~{minutos} min"
            return frase + "medindo de novo de dentro do aparelho antes da tarefa"
        if estado == "pendente":
            mem = self.memoria(str(row["instance_id"]))
            if row["error"] and mem.espera_ate > self._agora():
                minutos = max(1, round((mem.espera_ate - self._agora()) / 60))
                return (f"rede exigida ({politica}): a aplicação da rev {rev} falhou ({row['error']}); nova tentativa "
                        f"automática em ~{minutos} min, ou peça Reaplicar")
            return f"rede exigida ({politica}): aplicando a rede pedida (rev {rev}) antes da tarefa"
        if estado == "configurado":
            return (f"rede exigida ({politica}): rev {rev} aplicada; aguardando o reinício que ativa a VPN e o túnel "
                    "conectar")
        frase = (f"rede exigida ({politica}): {estado}; aguardando a medição do tráfego de dentro do aparelho, por app "
                 "(só `trafego_verificado` libera a tarefa)")
        if estado == "parcial" and row["detail"]:
            # O porquê da última medição (app parado, servidor externo sem teste de vazamento): é o que a pessoa vê
            # na tarefa que espera.
            frase += f"; última medição: {str(row['detail'])[:200]}"
        return frase

    def invalidar(self, instance_id: str, motivo: str) -> None:
        """Wipe, reset ou outro aparelho físico atrás do id: a configuração aplicada deixou de existir. A linha
        regride a `pendente` (reaplicar no próximo ponto seguro); o pedido vazio (tirar a rede) é dado por feito."""
        row = self._linha(instance_id)
        if row is None:
            return
        self._mem.pop(instance_id, None)
        if _vazio(row):
            self._apagar_linha(instance_id, f"dados do aparelho apagados ({motivo}): a rede antiga saiu junto")
            return
        if row["state"] == "pendente" and row["applied_rev"] is None:
            return                                     # nunca aplicada: não há o que invalidar
        rede.registrar_observacao(self.st, instance_id, rev=int(row["desired_rev"]), estado="pendente",
                                  evidencia=f"dados do aparelho apagados ({motivo}): o cliente VPN e a configuração "
                                            "aplicada deixaram de existir; reaplica no próximo ponto seguro")

    def aplicar_agora(self, instance_id: str, quem: str | None) -> dict[str, object]:
        """`POST /api/network/devices/{id}/apply`: o passo que falta, já, pela fila do aparelho (e como comando)."""
        rede._alvo_de_pedido(self.st, instance_id)        # 404, loja, quarentena, nada pedido: as mesmas recusas
        rt = self.st.devices.devices[instance_id]
        row = self._linha(instance_id)
        assert row is not None
        base: dict[str, object] = {"instance_id": instance_id, "state": row["state"],
                                   "desired_rev": int(row["desired_rev"]), "applied_rev": row["applied_rev"]}
        if rt.state != InstanceState.online:
            return {**base, "accepted": True, "executed": False,
                    "reason": f"{instance_id} está {rt.state.value}: a rede é aplicada quando ele entrar no ar"}
        if self.st.commands.open_for_instance(instance_id, verbs=despacho.VERBOS_EXCLUSIVOS) is not None:
            raise rede.RedeError(409, "device_busy", f"{instance_id} tem um comando de ciclo de vida em andamento; "
                                                     "a convergência segue quando ele terminar")
        trabalho = self.trabalho(rt, motivo="pedido")
        if trabalho is None:
            return {**base, "accepted": True, "executed": False, "reason": "nada a aplicar agora"}
        if not self.st.scheduler.run_device_job(rt, trabalho, label="rede do aparelho (pedido)"):
            raise rede.RedeError(409, "device_busy", f"{instance_id} está ocupado; a rede é aplicada no próximo ponto "
                                                     "seguro (antes da tarefa que dependa dela ou na varredura)")
        self.st.bus.emit("log", f"{instance_id}: rede aplicada a pedido de {quem or 'painel'}", instance_id=instance_id)
        return {**base, "accepted": True, "executed": True,
                "reason": "em andamento; o comando device.network do aparelho registra o desfecho"}

    # ------------------------------------------------------------------ passos
    async def _garantir_cliente(self, rt: DeviceRuntime, ap: AparelhoDaRede) -> str:
        """O cliente VPN instalado pelo fluxo de releases (a versão PROMOVIDA na loja; nunca APK de fora)."""
        pkg = self.cfg.cliente_pacote
        if "package:" in await ap.shell(f"pm path {pkg}", timeout=30):
            return f"{pkg} já instalado"
        alvo = self.st.releases.promoted_release(pkg)
        if alvo is None or alvo.status.value != "installable":
            raise RedeAplicacaoError(f"o cliente VPN {pkg} não está no aparelho e não há versão promovida dele na loja "
                                     "(item 25.10): promova-a para a rede poder ser aplicada")
        await self.st._entregar(rt, pkg, alvo.id)
        if "package:" not in await ap.shell(f"pm path {pkg}", timeout=30):
            raise RedeAplicacaoError(f"a entrega de {pkg} terminou sem o pacote no aparelho")
        return f"{pkg} instalado pela loja ({alvo.version_name})"

    async def _aplicar(self, rt: DeviceRuntime, row: Row, motivo: Motivo) -> dict[str, object]:
        iid, rev = rt.id, int(row["desired_rev"])
        ap = self._aparelho(self.st, rt)
        try:
            plano = montar_plano(self.st, row, self.st.rede_servidor, rt=rt)
            aviso_do_firewall = ""
            if plano.gerenciado and plano.remoto:
                aviso_do_firewall = await self._firewall_para_remoto(plano.remoto)
            if plano.gerenciado:
                # O servidor precisa conhecer a chave do aparelho ANTES de o aparelho reiniciar com ela.
                await self.st.rede_servidor.garantir()
            cliente = await self._garantir_cliente(rt, ap)
            evidencia = await provisionar(ap, self.st.db, self.st.secrets, plano, self.cfg, pausa_s=self.pausa_da_tela_s)
        except Exception as exc:
            self._falhou(iid, rev, exc)
            raise
        novo = rede.registrar_observacao(self.st, iid, rev=rev, estado="configurado",
                                         evidencia=f"{cliente}; {evidencia}{aviso_do_firewall}; falta o reinício "
                                                   "(always-on só vale no boot)")
        mem = self.memoria(iid)
        mem.falhas.pop(rev, None)
        mem.espera_ate = 0.0
        mem.configurado_em = time.time()
        if novo.state == "configurado" and novo.applied_rev == rev:
            self._agendar_reinicio(iid, rev, f"rede rev {rev} aplicada: o always-on só vale no boot")
        return {"instance_id": iid, "rev": rev, "state": novo.state, "evidence": novo.detail}

    async def _firewall_para_remoto(self, remoto: str) -> str:
        """O aparelho de outra máquina só fecha o túnel se o UDP da LAN passar pelo Firewall do Windows daqui (25.7).
        A plataforma não mexe no firewall: lê de novo e, fechado (`bloqueado`/`sem_regra`), RECUSA a aplicação com o
        comando exato que o dono roda — aplicar assim deixaria o aparelho com o túnel "no ar" e sem handshake (e, com
        bloqueio, sem rede nenhuma). Não lido (`desconhecido`) segue: não prova nem nega, e a conexão do par no log do
        servidor e a sonda decidem depois. Devolve o que vai na evidência."""
        leitura = await self.st.rede_servidor.firewall.conferir(forcar=True)
        porta = self.cfg.servidor.porta_wireguard
        if leitura.fechado:
            # Curto na frente e o comando inteiro (cabe nos 500 do `error` da linha); o porquê longo está no
            # `remote_access` do GET /api/network/server.
            raise RedeAplicacaoError(
                f"aparelho de {remoto}: firewall do central {leitura.estado} para o UDP {porta} da LAN (perfil "
                f"{leitura.perfil or 'Any'}). O dono roda, num PowerShell de administrador do central, e pede "
                f"Reaplicar: {' ; '.join(leitura.comandos)}")
        return f"; firewall do central para a LAN: {leitura.estado} ({leitura.detalhe[:160]})"

    async def _desfazer(self, rt: DeviceRuntime, row: Row, motivo: Motivo) -> dict[str, object]:
        iid, rev = rt.id, int(row["desired_rev"])
        ap = self._aparelho(self.st, rt)
        try:
            evidencia = await desfazer(ap, self.cfg)
        except Exception as exc:
            self._falhou(iid, rev, exc)
            raise
        novo = rede.registrar_observacao(self.st, iid, rev=rev, estado="configurado",
                                         evidencia=f"{evidencia}; falta o reinício (o bloqueio em memória só sai no boot)")
        # O par sai do servidor: o pedido deste aparelho já não é o servidor do central.
        await self._garantir_servidor()
        mem = self.memoria(iid)
        mem.configurado_em = time.time()
        if novo.state == "configurado" and novo.applied_rev == rev:
            self._agendar_reinicio(iid, rev, f"rede tirada (rev {rev}): o bloqueio em memória só sai no boot")
        return {"instance_id": iid, "rev": rev, "state": novo.state, "evidence": novo.detail}

    def _reiniciou_depois(self, row: Row, obs: Observacao) -> bool:
        """O aparelho passou por um boot DEPOIS de a configuração ser gravada? Sem isso o always-on não vale."""
        if obs.uptime_s is None:
            return False
        mem = self.memoria(str(row["instance_id"]))
        desde = mem.configurado_em or _epoch(row["updated_at"])
        if desde is None:
            return False
        return obs.uptime_s + _FOLGA_S < time.time() - desde

    async def _observar_depois_do_boot(self, ap: AparelhoDaRede, row: Row, pkg: str) -> Observacao:
        obs = await observar(ap, pkg)
        espera = float(self.cfg.espera_tun_s)
        if _vazio(row):
            return obs                                 # desfazer: ninguém espera túnel nenhum
        if not obs.tun and obs.uptime_s is not None and obs.uptime_s < espera and self._reiniciou_depois(row, obs):
            # Acabou de subir: o SFA leva ~40 s sob carga para o tun0 aparecer (medido). Espera até o prazo contado do
            # boot, não desta leitura.
            obs = await observar(ap, pkg, esperar_tun_s=espera - obs.uptime_s)
        return obs

    async def _conectar(self, rt: DeviceRuntime, row: Row, motivo: Motivo) -> dict[str, object]:
        iid, rev = rt.id, int(row["desired_rev"])
        pkg = self.cfg.cliente_pacote
        ap = self._aparelho(self.st, rt)
        obs = await self._observar_depois_do_boot(ap, row, pkg)
        reiniciou = self._reiniciou_depois(row, obs)
        mem = self.memoria(iid)
        if _vazio(row):
            if obs.removida() and reiniciou:
                self._apagar_linha(iid, f"rede tirada e conferida depois do reinício ({obs.descrever(pkg)})")
                await self._garantir_servidor()
                return {"instance_id": iid, "rev": rev, "state": None, "evidence": obs.descrever(pkg)}
            return self._reiniciar_ou_desistir(rt, row, obs, reiniciou, "o always-on ou o bloqueio continuam")
        plano_bloqueio = row["policy"] == "exigida_com_bloqueio"
        if not obs.configuracao_ok(pkg, plano_bloqueio):
            novo = rede.registrar_observacao(self.st, iid, rev=rev, estado="pendente",
                                             evidencia=f"a configuração não está no aparelho ({obs.descrever(pkg)}); "
                                                       "reaplica")
            return {"instance_id": iid, "rev": rev, "state": novo.state, "evidence": obs.descrever(pkg)}
        if obs.relatorios_de_falha:
            await apagar_relatorios_de_falha(ap, pkg)      # a queda do cliente grava a chave ali
        if obs.conectada(plano_bloqueio):
            evidencia = f"túnel no ar, lido como uid 2000: {obs.descrever(pkg)}" + self._par_no_servidor(iid)
            novo = rede.registrar_observacao(self.st, iid, rev=rev, estado="conectado", evidencia=evidencia)
            mem.reinicios.pop(rev, None)
            mem.espera_ate = 0.0
            mem.ultima_conferencia = self._agora()
            await self._guardar_linha_de_base(ap, iid, rev)
            return {"instance_id": iid, "rev": rev, "state": novo.state, "evidence": evidencia}
        return self._reiniciar_ou_desistir(rt, row, obs, reiniciou, "o túnel não subiu")

    def _reiniciar_ou_desistir(self, rt: DeviceRuntime, row: Row, obs: Observacao, reiniciou: bool,
                               o_que: str) -> dict[str, object]:
        iid, rev = rt.id, int(row["desired_rev"])
        pkg = self.cfg.cliente_pacote
        mem = self.memoria(iid)
        # O teto conta os reinícios PEDIDOS (aceitos ou recusados), não os boots vistos: um aparelho que a plataforma
        # não reinicia de verdade (o celular sem worker, em que o `restart` só solta e readota a sessão, 25.7) ou cujo
        # uptime não veio nunca teria o boot detectado, e cada passada pediria mais um.
        pedidos = mem.reinicios.get(rev, 0)
        if pedidos >= int(self.cfg.reinicios_max):
            if reiniciou:
                erro = f"{o_que} depois de {pedidos} reinício(s) ({obs.descrever(pkg)})"
            else:
                uptime = "não lido" if obs.uptime_s is None else f"ligado há {obs.uptime_s} s"
                erro = (f"{o_que}: o reinício foi pedido {pedidos} vez(es) e nenhum boot foi detectado depois da "
                        f"configuração (uptime {uptime}). Aparelho que a plataforma não reinicia de verdade (celular "
                        f"sem worker): reinicie-o por fora e peça Reaplicar ({obs.descrever(pkg)})")
            self._falhou(iid, rev, RedeAplicacaoError(erro))
            raise RedeAplicacaoError(erro)
        detalhe = (f"{o_que} depois do boot ({obs.descrever(pkg)}); novo reinício pedido" if reiniciou
                   else f"configurado, sem reinício desde então ({obs.descrever(pkg)}); reinício pedido")
        rede.registrar_observacao(self.st, iid, rev=rev, estado="configurado", evidencia=detalhe)
        self._agendar_reinicio(iid, rev, detalhe[:200])
        return {"instance_id": iid, "rev": rev, "state": "configurado", "evidence": detalhe}

    async def _conferir(self, rt: DeviceRuntime, row: Row, motivo: Motivo) -> None:
        """A deriva: relê e só REGRIDE (nunca avança por aqui). Configuração que sumiu → `pendente`; túnel caído com
        a configuração no lugar → `configurado` (o passo seguinte reinicia)."""
        iid = rt.id
        pkg = self.cfg.cliente_pacote
        ap = self._aparelho(self.st, rt)
        mem = self.memoria(iid)
        try:
            obs = await self._observar_depois_do_boot(ap, row, pkg)
        except RedeAplicacaoError as exc:
            log.info("%s: conferência da rede não leu o aparelho — %s", iid, exc)
            mem.ultima_conferencia = self._agora()
            return
        mem.ultima_conferencia = self._agora()
        if await self._regredir_se_derivou(ap, row, obs, motivo):
            return
        rev = int(row["desired_rev"])
        if mem.linha_de_base is None or mem.linha_de_base[0] != rev:
            # Backend reiniciado depois da conexão: sem a contabilidade de agora, a remedição da validade (item 25.6)
            # teria só a janela da própria sonda, e um app sem tráfego naqueles segundos derrubaria o verificado.
            await self._guardar_linha_de_base(ap, iid, rev)

    async def _regredir_se_derivou(self, ap: AparelhoDaRede, row: Row, obs: Observacao, motivo: str) -> bool:
        """A deriva que a releitura achou, registrada como regressão. `True` = regrediu (nada mais a fazer agora)."""
        iid, rev = str(row["instance_id"]), int(row["desired_rev"])
        pkg = self.cfg.cliente_pacote
        bloqueio = row["policy"] == "exigida_com_bloqueio"
        if obs.relatorios_de_falha:
            await apagar_relatorios_de_falha(ap, pkg)
        if not obs.configuracao_ok(pkg, bloqueio):
            rede.registrar_observacao(self.st, iid, rev=rev, estado="pendente",
                                      evidencia=f"deriva ({motivo}): a configuração saiu do aparelho "
                                                f"({obs.descrever(pkg)}); reaplica")
            return True
        if not obs.conectada(bloqueio):
            self.memoria(iid).configurado_em = time.time()
            rede.registrar_observacao(self.st, iid, rev=rev, estado="configurado",
                                      evidencia=f"deriva ({motivo}): configuração no lugar, túnel caído "
                                                f"({obs.descrever(pkg)}); reinicia")
            return True
        return False

    # ------------------------------------------------------------------ a sonda de saída (25.5)
    def pedir_verificacao(self, instance_id: str, quem: str | None) -> dict[str, object]:
        """`POST /api/network/devices/{id}/verify`: registra o pedido (as mesmas recusas e o mesmo 202 de antes) e
        marca a sonda para o próximo ponto seguro deste aparelho. Um pedido novo refaz tudo, inclusive o teste de
        vazamento da revisão — que, com bloqueio, para o cliente VPN e reinicia o aparelho (o `reason` diz). Em
        memória: um reinício do backend perde o pedido, mas o `ligou` da readoção mede de novo quem ainda não está
        verificado."""
        resposta = rede.pedir_verificacao(self.st, instance_id, quem)
        mem = self.memoria(instance_id)
        mem.verificacao_pedida = True
        mem.vazamento.clear()
        mem.espera_ate = 0.0
        row = self._linha(instance_id)
        if row is not None and row["policy"] == "exigida_com_bloqueio":
            resposta = {**resposta, "reason": f"{resposta.get('reason', '')} Com a política exigida_com_bloqueio, o "
                                              "teste de vazamento é refeito: o cliente VPN é parado para a sonda e o "
                                              "aparelho REINICIA para religá-lo (a medição vem depois do boot)."}
        return resposta

    async def _guardar_linha_de_base(self, ap: AparelhoDaRede, instance_id: str, rev: int) -> None:
        """A contabilidade por UID de quando o túnel conectou: a janela da primeira medição dos apps começa aqui (o
        tráfego desde a conexão conta, o de antes não). Sem ela (leitura que falhou), a janela é só a da medição."""
        try:
            self.memoria(instance_id).linha_de_base = (rev, await contabilidade(ap))
        except Exception as exc:  # noqa: BLE001 - só encurta a janela da primeira medição; o `conectado` vale
            log.info("%s: contabilidade da conexão não lida — %s", instance_id, exc)

    async def _tunel_de_volta(self, ap: AparelhoDaRede, pkg: str, bloqueio: bool) -> bool:
        """Depois de parar o cliente para o teste de vazamento: o túnel voltou sozinho (o always-on religou)? No 25.1
        não voltava (18:07); uma leitura que falha conta como não — religar pelo boot é o lado seguro."""
        try:
            return (await observar(ap, pkg)).conectada(bloqueio)
        except Exception as exc:  # noqa: BLE001
            log.info("%s: leitura depois do teste de vazamento falhou — %s", getattr(ap, "id", "?"), exc)
            return False

    def _vazamento_adiado(self, rt: DeviceRuntime) -> str | None:
        """Por que o teste de vazamento NÃO pode rodar agora (ou `None`): ele para o cliente VPN, e só o boot o religa.
        Sem o reinício garantido logo depois, o aparelho ficaria sem rede — então o teste nem começa:
        - objetivo no meio (rodando, esperando uma pessoa ou incerto; a mesma regra de `_pedir_reinicio`): a tela dele
          é a evidência de que o operador precisa, e o reinício seria adiado até ele terminar;
        - aparelho que a plataforma não reinicia de verdade (o celular sem worker: o `restart` só solta e readota a
          sessão, 25.7): o cliente parado só voltaria com alguém reiniciando o celular por fora."""
        if objetivo_em_andamento(self.st, rt.id, exceto_quem_espera_a_rede=True):
            return ("teste de vazamento adiado: há um objetivo no meio neste aparelho, e o reinício que religa o "
                    "cliente VPN depois do teste não pode sair agora; a próxima medição tenta de novo")
        if getattr(rt, "external", False) and not getattr(rt, "worker_id", None):
            return ("teste de vazamento não feito: aparelho que a plataforma não reinicia de verdade (celular sem "
                    "worker), e o cliente VPN parado para o teste só voltaria com o celular reiniciado por fora")
        return None

    def _religar_pelo_boot(self, iid: str, rev: int, vazamento: Vazamento) -> dict[str, object]:
        """O teste de vazamento deixou o cliente VPN parado: o aparelho está sem VPN (e, com bloqueio, sem rede). O
        caminho medido para religar sem toque é o boot (always-on com o perfil selecionado, 25.1 18:21): regride a
        `configurado`, e o passo seguinte (`conectar`) confere o túnel depois do reinício. A medição vem depois, com o
        teste desta revisão já guardado."""
        self.memoria(iid).configurado_em = time.time()
        novo = rede.registrar_observacao(
            self.st, iid, rev=rev, estado="configurado",
            evidencia=f"teste de vazamento ({vazamento.texto[:220]}); o cliente VPN foi parado para o teste e o "
                      "always-on não o religa sem boot: reinicia, e a medição vem depois")
        # O desfecho do teste vai no motivo: a evidência do reinício substitui o `detail` da linha.
        self._agendar_reinicio(iid, rev, f"religar o cliente VPN depois do teste de vazamento da rev {rev} "
                                         f"({vazamento.texto[:200]})")
        return {"instance_id": iid, "rev": rev, "state": novo.state, "measured": False,
                "leak_blocked": vazamento.bloqueado, "evidence": novo.detail}

    async def _verificar(self, rt: DeviceRuntime, row: Row, motivo: Motivo) -> dict[str, object]:
        """Relê como o conferir (deriva regride, e aí não há o que medir) e, com o túnel no ar lido como uid 2000,
        mede a saída de dentro do aparelho e grava a medição. `trafego_verificado`/`parcial` saem SÓ de
        `rede.registrar_medicao`, pelas regras dele; aqui só se escolhe a janela, se os apps são abertos e o teste de
        vazamento.

        O teste de vazamento (política com bloqueio) vem ANTES da medição e uma vez por revisão: ele para o cliente VPN
        (`rede_medicao.sondar_vazamento`), e o que se mede depois é o aparelho com o túnel de volta. Os apps exigidos
        sem tráfego na janela são abertos quando uma tarefa está segurada pela porta (`motivo='tarefa'`: é o que ela
        faria, e sem isso o app nunca aberto seguraria a tarefa para sempre) ou com `rede.sonda.abrir_apps`."""
        iid, rev = rt.id, int(row["desired_rev"])
        pkg = self.cfg.cliente_pacote
        ap = self._aparelho(self.st, rt)
        mem = self.memoria(iid)
        mem.verificacao_pedida = False
        agora = self._agora()
        mem.ultima_verificacao = mem.ultima_conferencia = agora
        obs = await self._observar_depois_do_boot(ap, row, pkg)
        if await self._regredir_se_derivou(ap, row, obs, f"verificação, {motivo}"):
            novo = self._linha(iid)
            return {"instance_id": iid, "rev": rev, "state": novo["state"] if novo else None,
                    "evidence": obs.descrever(pkg), "measured": False}
        bloqueio = row["policy"] == "exigida_com_bloqueio"
        vazamento = mem.vazamento.get(rev) if bloqueio else None
        adiado = self._vazamento_adiado(rt) if bloqueio and vazamento is None else None
        if adiado is not None:
            # Sem guardar: a próxima medição tenta de novo. O `parcial` que sai daqui diz o porquê.
            vazamento = Vazamento(None, adiado, now_iso())
        elif bloqueio and vazamento is None:
            try:
                teste = await sondar_vazamento(ap, self.cfg.sonda, pkg)
            except Exception:
                # Antes de parar o cliente (a sonda com o túnel no ar não leu): nada foi tocado.
                mem.espera_ate = max(mem.espera_ate, self._agora() + float(self.cfg.sonda.reverificar_s))
                raise
            vazamento = teste.vazamento
            if teste.cliente_parado:
                # Guardado mesmo sem conclusão (`bloqueado=None`): refazer a cada medição reiniciaria o aparelho em
                # laço. Um POST …/verify limpa e refaz.
                mem.vazamento[rev] = vazamento
                if not await self._tunel_de_volta(ap, pkg, bloqueio):
                    return self._religar_pelo_boot(iid, rev, vazamento)
        base = mem.linha_de_base[1] if mem.linha_de_base is not None and mem.linha_de_base[0] == rev else None
        abrir = motivo == "tarefa" or bool(self.cfg.sonda.abrir_apps)
        try:
            r = await medir(ap, self.cfg.sonda, exigidos=rede.apps_exigidos(self.st, iid), linha_de_base=base,
                            bloqueio=bloqueio, vazamento=vazamento, abrir=abrir)
        except Exception:
            mem.espera_ate = max(mem.espera_ate, self._agora() + float(self.cfg.sonda.reverificar_s))
            raise
        mem.medida_sem_abrir = not abrir
        if base is None:
            # Só quando não havia (backend reiniciado depois da conexão): a janela é ACUMULADA desde a conexão, e
            # avançá-la a cada medição faria um app parado desde a última sonda derrubar um `trafego_verificado`.
            mem.linha_de_base = (rev, r.base)
        mid, novo = rede.registrar_medicao(self.st, iid, r.medicao, rev=rev)
        estado = novo.state if novo is not None else None
        atual = self._linha(iid)
        if estado != "trafego_verificado" or (atual is not None and self.invalida(atual) is not None):
            # Não verificou, ou mediu sem IP e o `trafego_verificado` vencido ficou como estava (o estado não muda
            # sem saída medida, e `verified_at` também não): espera `reverificar_s`, senão a porta dispararia a mesma
            # sonda a cada volta.
            mem.espera_ate = max(mem.espera_ate, self._agora() + float(self.cfg.sonda.reverificar_s))
        return {"instance_id": iid, "rev": rev, "state": estado, "measurement_id": mid, "measured": True,
                "opened": list(r.abertos), "evidence": r.medicao.detail}

    async def reler_entre_etapas(self, rt: DeviceRuntime) -> None:
        """A queda do túnel NO MEIO de um objetivo (item 25.6): o scheduler chama isto de dentro do worker, entre uma
        etapa e a seguinte (`Scheduler.rede_releitura`), antes da porta. Com o aparelho ocupado nada mais o relê — a
        varredura e a porta só agem com ele livre —, e com a política `exigida` (sem bloqueio) um túnel caído deixaria
        os apps saírem pela física até o objetivo acabar. Uma leitura só, como uid 2000 (sem esperar o `tun0`); a
        deriva regride a linha como no conferir, e a porta, logo depois, segura a etapa seguinte.

        Só com política exigida e a rede já conectada (`livre` não depende da rede; antes de conectar, a porta já
        segura); no máximo uma a cada `_INTERVALO_DA_PORTA_S`, pelo relógio da convergência."""
        row = self._linha(rt.id)
        if row is None or row["policy"] == "livre" or row["state"] not in ("conectado", "parcial", "trafego_verificado"):
            return
        mem = self.memoria(rt.id)
        agora = self._agora()
        if mem.ultima_conferencia is not None and agora - mem.ultima_conferencia < _INTERVALO_DA_PORTA_S:
            return
        mem.ultima_conferencia = agora
        pkg = self.cfg.cliente_pacote
        ap = self._aparelho(self.st, rt)
        obs = await observar(ap, pkg)
        await self._regredir_se_derivou(ap, row, obs, "releitura entre etapas de um objetivo")

    # ------------------------------------------------------------------ reinício, falha, servidor
    def _agendar_reinicio(self, instance_id: str, rev: int, motivo: str) -> None:
        """O reinício só pode ser pedido quando o trabalho soltar o aparelho (`_precheck` recusa com a IA no
        controle). Tenta logo depois e, com o aparelho ainda ocupado, de 5 em 5 s por até 2 min; depois disso a
        varredura (ou a porta) encontra `configurado` sem boot e pede de novo."""
        if instance_id in self._reinicio_agendado:
            return
        self._reinicio_agendado.add(instance_id)
        try:
            asyncio.get_running_loop().call_later(self.atraso_do_reinicio_s, self._pedir_reinicio, instance_id, rev,
                                                  motivo, 24)
        except RuntimeError:
            self._reinicio_agendado.discard(instance_id)

    def _pedir_reinicio(self, instance_id: str, rev: int, motivo: str, restantes: int) -> None:
        row = self._linha(instance_id)
        rt = self.st.devices.devices.get(instance_id)
        if row is None or int(row["desired_rev"]) != rev or row["state"] != "configurado" or rt is None \
                or rt.state != InstanceState.online or self.st.quarentena(instance_id) is not None:
            self._reinicio_agendado.discard(instance_id)
            return
        # O objetivo suspenso entre etapas pela porta da rede (25.6) espera ESTE reinício: não conta como ocupado (o
        # worker, que é quem mexe no aparelho, é conferido à parte). Contá-lo travaria o objetivo e a rede juntos.
        ocupado = (rt.id in self.st.scheduler.workers or rt.control != ControlOwner.none
                   or objetivo_em_andamento(self.st, instance_id, exceto_quem_espera_a_rede=True)
                   or self.st.commands.open_for_instance(instance_id, verbs=despacho.VERBOS_EXCLUSIVOS) is not None)
        if ocupado:
            if restantes > 0:
                asyncio.get_running_loop().call_later(self.intervalo_do_reinicio_s, self._pedir_reinicio,
                                                      instance_id, rev, motivo, restantes - 1)
            else:
                # Ocupado o tempo todo (tarefas seguidas com política livre): a varredura volta a tentar depois.
                self._reinicio_agendado.discard(instance_id)
                self.memoria(instance_id).espera_ate = self._agora() + _ESPERA_DA_RELEITURA_S
            return
        self._reinicio_agendado.discard(instance_id)
        cid = despacho.pedir_ciclo_de_vida(self.st, instance_id, "restart", f"rede: {motivo}", requested_by=QUEM)
        mem = self.memoria(instance_id)
        # Recusado também conta para o teto (`rede.reinicios_max`): um worker sem o verbo recusaria para sempre.
        mem.reinicios[rev] = mem.reinicios.get(rev, 0) + 1
        if cid is None:
            # Recusado (verbo que o worker não tem, manutenção, quarentena): a recusa já está no histórico do aparelho.
            log.info("%s: o reinício da rede não foi aceito agora; a varredura pede de novo", instance_id)
            mem.espera_ate = self._agora() + _ESPERA_DA_RELEITURA_S
            return
        # O que foi importado e relido fica no desfecho do comando `device.network`; aqui, o porquê e o id do reinício.
        rede.registrar_observacao(self.st, instance_id, rev=rev, estado="configurado",
                                  evidencia=f"{motivo[:300]}; reinício {cid} pedido")

    def _falhou(self, instance_id: str, rev: int, exc: BaseException) -> None:
        mem = self.memoria(instance_id)
        n = mem.falhas[rev] = mem.falhas.get(rev, 0) + 1
        espera = min(3600.0, 300.0 * 3 ** (n - 1))
        mem.espera_ate = self._agora() + espera
        mem.reinicios.pop(rev, None)
        quando = datetime.fromtimestamp(now().timestamp() + espera, tz=timezone.utc).strftime("%H:%M")
        try:
            rede.registrar_observacao(self.st, instance_id, rev=rev, estado="pendente",
                                      evidencia=f"a rev {rev} não se aplicou ({n}ª falha); nova tentativa automática "
                                                f"às {quando} UTC, ou peça Reaplicar", erro=str(exc)[:500])
        except rede.RedeError:
            log.info("%s: a linha de rede sumiu antes de registrar a falha", instance_id)

    def _par_no_servidor(self, instance_id: str) -> str:
        """Evidência do lado do servidor, quando ele é o do central: a última conexão do par no log."""
        chave = self.st.db.one("SELECT address FROM network_keys WHERE owner=?", (instance_id,))
        if chave is None or not chave["address"]:
            return ""
        quando = self.st.rede_servidor.ultima_conexao(endereco_no_tunel(str(chave["address"])))
        if quando:
            return f"; o servidor do central viu o par {chave['address']} às {quando}"
        texto = f"; o log do servidor ainda não mostra o par {chave['address']}"
        if self.st.rede_servidor.eh_remoto(instance_id):
            # Túnel "no ar" no aparelho e nenhuma conexão no servidor: no remoto, o suspeito é o caminho pela LAN.
            leitura = self.st.rede_servidor.firewall.ultima()
            texto += (f" (aparelho de outra máquina: endpoint {self.cfg.servidor.endpoint_lan or 'não configurado'}; "
                      f"firewall do central {leitura.estado if leitura else 'ainda não lido'})")
        return texto

    async def _garantir_servidor(self) -> None:
        try:
            await self.st.rede_servidor.garantir()
        except Exception as exc:  # noqa: BLE001 - o servidor tem o próprio `detail`; a convergência do aparelho segue
            log.info("servidor de rede: %s", exc)

    def _apagar_linha(self, instance_id: str, motivo: str) -> None:
        """Nada pedido e nada aplicado: a linha só diria "pendente" à toa (a mesma regra de `rede._gravar_desejado`)."""
        self.st.db.execute("DELETE FROM device_network WHERE instance_id=?", (instance_id,))
        self._mem.pop(instance_id, None)
        self.st.bus.emit("network.updated", f"Rede de {instance_id}: sem rede gerenciada ({motivo[:160]})",
                         instance_id=instance_id, data={"instance_id": instance_id, "acao": "removida", "state": None})

    async def laco(self, intervalo_s: float = 60.0) -> None:
        """O servidor do central acompanha o banco: sobe com o primeiro par, reinicia quando o conjunto muda, para
        quando ninguém mais o pede. A primeira volta é na partida (o órfão de uma queda é reconhecido ali)."""
        while True:
            await self._garantir_servidor()
            try:
                # Com aparelho de outra máquina no servidor, o estado do firewall fica fresco para o painel (a leitura
                # tem validade; sem par remoto, não roda).
                await self.st.rede_servidor.conferir_acesso_remoto()
            except Exception as exc:  # noqa: BLE001 - leitura de apoio; o servidor segue
                log.info("firewall do central: %s", exc)
            await asyncio.sleep(intervalo_s)

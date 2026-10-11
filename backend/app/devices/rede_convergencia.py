"""Convergência da rede por aparelho (ADR-056, item 25.4): QUANDO aplicar, conferir, reiniciar e desfazer.

O modelo (`devices/rede.py`) guarda o desejado × observado e só muda de estado com evidência; a receita no aparelho
(`devices/rede_aplicacao.py`) sabe O QUE fazer; o servidor do central (`devices/rede_servidor.py`) sabe quem são os
pares. Este módulo decide quando, sempre num ponto seguro (o aparelho livre, pela fila dele) e sempre deixando
rastro (comando `device.network` no histórico do aparelho; reinício como comando `restart`):

| estado da linha | o que a convergência faz |
|---|---|
| `pendente`, pedido com VPN/proxy | **aplicar**: plano, chave e par no servidor, cliente pela loja, receita; → `configurado` e pede o reinício |
| `pendente`, pedido vazio (tirou tudo) | **desfazer**: tira always-on e bloqueio; → `configurado` e pede o reinício |
| `configurado` | **conectar**: lê como uid 2000 (depois de um boot, esperando o `tun0` até `rede.espera_tun_s` contados do boot); túnel no ar → `conectado`; sem reinício desde a configuração → pede o reinício; a configuração valendo e só o túnel faltando → o Start da interface do cliente (`rede.cliente_atividade`, W8: o tile não recalcula o `serviceMode` do SFA) e, se ele não religar, outro reinício; passados `rede.reinicios_max` reinícios PEDIDOS na revisão (aceitos ou recusados, com boot detectado ou não), `pendente` com erro. No desfazer: removido → a linha sai |
| `conectado`, `parcial` | **verificar** (25.5; ao ligar, a pedido, pela porta da tarefa, e na varredura quando vence `rede.deriva_s` ou, no `parcial`, `rede.sonda.reverificar_s`): relê como o conferir e, com o túnel no ar, roda a sonda de saída (`rede_medicao.medir`) e grava a medição — só ela leva a `trafego_verificado` ou `parcial`. Com bloqueio, antes, a prova de vazamento: a que a linha guarda para a revisão e para a instalação do cliente VPN (colunas `leak_*`, item 29.2) ou, sem ela, o teste (`rede_medicao.sondar_vazamento`: o cliente VPN parado, com a intenção gravada antes); sem o túnel de volta, → `configurado` e reinicia (o boot religa o cliente), e a medição vem depois |
| `trafego_verificado` | **túnel morto** (25.12): a medição da verificação sem IP de saída tira a linha de `trafego_verificado` (`conectado`, motivo e evento `tunel_morto`), religa o cliente VPN no aparelho (parar e deixar o always-on subir, ou o Start da interface; até 2 vezes na passada) e, sem volta, `configurado` e reinício sem wipe. **conferir** (ao ligar, ao acordar, depois do reinício do backend e a cada `rede.deriva_s`): configuração que sumiu → `pendente` (reaplica); túnel caído com a configuração no lugar → `configurado` (reinicia). Com a verificação pedida (`POST …/verify`), **verificar** no próximo ponto seguro. Com política exigida, **verificar** também quando a medição vence (`rede.validade_verificacao_s`, item 25.6), quando o aparelho subiu depois dela (boot a frio ou acordar, item 29.22: a prova é do Android de antes), quando a política com bloqueio está sem prova de vazamento que valha, ou quando a medição não cobre um app exigido hoje (conta vinculada depois): a varredura mede de novo um pouco antes do vencimento, com o aparelho livre; a porta da tarefa, se já não vale |

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

O que NÃO mora na memória: a prova de vazamento. O teste para o cliente VPN e custa um reinício do aparelho; com a
prova só em memória, cada reinício do backend a perdia e a remedição seguinte (a 90% da validade) refazia o teste em
todos os aparelhos com bloqueio. Ela fica na linha (`rede.prova_de_vazamento`), e o relógio da medição não a apaga:
quem a invalida é revisão nova, outra instalação do cliente VPN, wipe ou identidade, e o `POST …/verify`.
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
from ..db import loads
from ..shared.egresso import JANELA_DO_LOGIN_S, EgressoNaJanela
from ..util import now, now_iso, parse_iso
from ..vitrine import objetivo_em_andamento, objetivo_esperando_a_rede, objetivo_que_segura
from . import rede
from .rede_aplicacao import (CLASSE_ERRADA_PARA_TUN, AparelhoDaRede, AparelhoPeloAdb, Observacao, RedeAplicacaoError,
                             apagar_relatorios_de_falha, desfazer, endereco_no_tunel, instalado_em, montar_plano,
                             observar, parar_cliente_solto, provisionar, religar_pela_interface)
from .rede_medicao import (ResultadoDaSonda, TesteDeVazamento, Vazamento, contabilidade, medir, medir_saida,
                           sondar_vazamento)
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
#: Tentativas de 5 s do reinício pedido com o aparelho ocupado (2 min) antes de o pedido esgotar (29.21).
_TENTATIVAS_DO_REINICIO = 24
#: Quanto o relógio do aparelho pode estar longe do servidor para a data do APK do cliente servir de prova na adoção
#: da transição (item 29.2). O emulador acerta o relógio pelo host; mais que isto é relógio quebrado.
_DESVIO_DE_RELOGIO_S = 120.0
#: Fração final da validade de um `trafego_verificado` em que a varredura (aparelho livre) já mede de novo: a tarefa
#: que chega depois encontra a medição renovada em vez de esperar por ela.
_ANTECEDENCIA_DA_VALIDADE = 0.1
#: Túnel morto (25.12): quantas vezes, na mesma passada, o cliente VPN é parado e religado antes de o aparelho ser
#: reiniciado (sem wipe). Dentro da passada de propósito: entre passadas a espera da medição (`reverificar_s`, 10 min)
#: faria a segunda tentativa demorar 10 min com a tarefa esperando.
_RELIGADAS_DO_TUNEL_MORTO = 2


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
    # A sonda de saída (25.5): o pedido de verificar (POST …/verify), a última medição e a contabilidade de quando o
    # túnel conectou (o começo da janela dos apps, SÓ da mesma revisão). O teste de vazamento NÃO fica aqui: a prova é
    # da linha do aparelho (`rede.prova_de_vazamento`), para um reinício do backend não a perder.
    verificacao_pedida: bool = False
    ultima_verificacao: float | None = None                       # monotonic
    linha_de_base: tuple[int, Contabilidade] | None = None        # (rev, contabilidade)
    # Um ensaio de vazamento está rodando AGORA neste processo. Separa o `leak_pending` de quem está no meio do
    # ensaio do `leak_pending` que sobrou de um backend que reiniciou (esse é fechado como inconclusivo).
    ensaio_em_curso: bool = False
    # A última medição foi feita SEM abrir os apps exigidos (varredura, ligou): a tarefa que a porta segura mede de
    # novo já, abrindo-os, em vez de esperar `reverificar_s` por uma medição que não podia provar o app parado.
    medida_sem_abrir: bool = False
    # Por que o último Start da interface do cliente não religou o túnel (código e detalhe, sem segredo); vazio se
    # não foi tentado ou se religou. Só enfeita a evidência de quem cai no reinício.
    religar_motivo: str = ""
    religar_codigo: str = ""
    # A sonda de IP do aparelho sem rede pedida (29.20): monotonic da próxima vez em que a varredura pode disparar uma.
    # Separada de `espera_ate` de propósito: uma rede atribuída depois não pode esperar por causa da sonda.
    sonda_sem_rede_ate: float = 0.0
    # O reinício pedido que o aparelho ocupado não deixou sair (29.21): o termo de `_quem_segura_o_reinicio` já
    # registrado na linha (só se grava de novo quando o termo muda) e se o esgotamento das tentativas já foi dito.
    # Em memória como o resto: zera no restart do central, e a linha `configurado` persistida reabre o ciclo.
    reinicio_segurado: str = ""
    reinicio_esgotado: bool = False


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
        #: Esgotadas as tentativas com um objetivo parado esperando a rede NESTE aparelho (29.21), quanto esperar para
        #: tentar de novo. Os 300 s da releitura (`_ESPERA_DA_RELEITURA_S`) deixavam a porta e a varredura mudas por 5
        #: min enquanto a execução esperava, e a execução é quem paga por cada minuto.
        self.retentativa_do_reinicio_s = 30.0
        #: Entre uma leitura da tela e a próxima, na importação do perfil (o SFA leva um instante para trocar de tela).
        self.pausa_da_tela_s = 1.5
        #: Quanto esperar o túnel depois do Start da interface do cliente (medido no android-09, 01/10: tun0 em menos de 1 s).
        self.espera_da_interface_s = 25.0
        #: Quanto esperar o botão Start aparecer na árvore depois de abrir o app, e o intervalo entre as leituras.
        self.prazo_do_botao_s = 15.0
        self.pausa_da_interface_s = 1.0
        #: Túnel morto (25.12): a pausa depois do `force-stop` do cliente, quanto esperar o always-on religá-lo (medido no
        #: android-05: menos de 1 s; com folga para o convidado sob carga) e o intervalo entre as leituras dessa espera.
        self.pausa_do_force_stop_s = 2.0
        self.espera_do_religar_s = 12.0
        self.intervalo_do_religar_s = 3.0
        #: O teto de cada Start da interface dentro do túnel morto. Medido em 03/10 (log do central, 06:09Z, android-06): a
        #: interface do cliente falhou com `WebDriverException ... Timed out ... waiting for the root AccessibilityNodeInfo`;
        #: o gesto que trava não pode segurar o aparelho nem a passada: estoura o teto, conta como tentativa falha, e
        #: depois da segunda vem o reinício.
        self.prazo_da_interface_no_tunel_morto_s = 60.0

    # ------------------------------------------------------------------ utilidades
    @property
    def cfg(self) -> RedeCfg:
        return self.st.cfg.file.rede

    def memoria(self, instance_id: str) -> _Memoria:
        mem = self._mem.get(instance_id)
        if mem is None:
            mem = self._mem[instance_id] = _Memoria()
            # 25.11 (RA-12): o teto de reinícios da revisão em curso volta da linha do aparelho (migração 093). Sem
            # isto, cada reinício do backend zerava a conta e o mesmo aparelho ganhava `reinicios_max` reinícios novos.
            row = self._linha(instance_id)
            if row is not None and row["restart_rev"] is not None and int(row["restarts_requested"] or 0) > 0:
                mem.reinicios[int(row["restart_rev"])] = int(row["restarts_requested"])
        return mem

    def _gravar_reinicios(self, instance_id: str, rev: int, pedidos: int) -> None:
        """A conta de reinícios pedidos da revisão `rev` na linha do aparelho (25.11). Zero apaga a conta."""
        self.st.db.execute("UPDATE device_network SET restart_rev=?, restarts_requested=? WHERE instance_id=?",
                           (rev if pedidos > 0 else None, max(0, pedidos), instance_id))

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
            if estado == "parcial" and self._bloqueio_sem_saida(row):
                # O teste de vazamento desta revisão já tem desfecho e ele não aprova (vazou ou não concluiu): nenhuma
                # medição muda isso, e o teste não se refaz sozinho. Medir de novo só para a saída medida não
                # envelhecer; quem destrava é `POST …/verify`, revisão nova ou cliente novo. A espera da medição que
                # não verificou vale aqui: sem IP medido o `verified_at` não anda, e sem ela cada passada mediria de
                # novo (um comando e uma sonda por minuto enquanto o eco de IP ou o servidor estivessem fora).
                return "verificar" if self._medicao_envelhecida(row) and agora >= mem.espera_ate else None
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

    def _bloqueio_sem_saida(self, row: Row) -> bool:
        """Política com bloqueio e um desfecho de teste de vazamento, desta revisão, que não aprova (lido só do banco)."""
        if row["policy"] != "exigida_com_bloqueio":
            return False
        return rede.prova_de_vazamento(row).situacao(int(row["desired_rev"])) in ("vazou", "inconclusiva")

    def _medicao_envelhecida(self, row: Row) -> bool:
        """A última saída medida está para sair da validade (ou nunca houve): a mesma antecedência da remedição."""
        idade = rede.idade_da_verificacao(row)
        validade = float(self.cfg.validade_verificacao_s)
        return idade is None or idade >= validade * (1 - _ANTECEDENCIA_DA_VALIDADE)

    def vencida(self, row: Row) -> bool:
        """`trafego_verificado` com política exigida e a medição fora da validade: para a porta, não vale."""
        return rede.verificacao_vencida(row, float(self.cfg.validade_verificacao_s))

    def invalida(self, row: Row) -> str | None:
        """`vencida`, `boot` (o aparelho subiu depois da medição, 29.22), `bloqueio`, `apps` (conta vinculada depois da
        medição) ou `None`: o `trafego_verificado` vale para a porta."""
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
        if (motivo in ("ligou", "varredura") and rede.sem_rede_pedida(row)
                and (row is None or self._acao(row, motivo) is None)):
            # Sem rede pedida e nada a desfazer: só a sonda de IP de leitura (29.20), nunca um passo da convergência
            # (a porta da tarefa e o pedido manual não passam por aqui: não há rede a esperar nem a aplicar).
            return self._trabalho_sem_rede(rt)
        if row is None or not self._elegivel(rt) or self._acao(row, motivo) is None:
            return None
        if motivo == "tarefa":
            self.memoria(rt.id).ultima_tentativa = self._agora()

        async def trabalho() -> None:
            await self.executar(rt, motivo=motivo)

        return trabalho

    def so_confere(self, rt: DeviceRuntime, motivo: Motivo) -> bool:
        """29.163: o passo que a linha pede agora é só LER a rede (`conferir`, `verificar`), sem aplicar, conectar nem desfazer nada."""
        row = self._linha(rt.id)
        return row is not None and self._acao(row, motivo) in ("conferir", "verificar")

    def _trabalho_sem_rede(self, rt: DeviceRuntime) -> Callable[[], Awaitable[None]] | None:
        """A sonda de IP do aparelho SEM rede pedida (item 29.20): só os IPs v4/v6 de saída, como o uid 2000, com o
        aparelho ligado e livre, a cada `rede.sonda.reverificar_s` (a falha espera menos: 5 min). NÃO é convergência: não
        há linha em `device_network`, nada muda de estado, política ou revisão, e nada dispara reaplicação, reinício,
        bloqueio de tarefa ou comando — o resultado só vira uma linha em `network_measurements` que a listagem lê."""
        if not self.cfg.sonda.medir_sem_rede or not self._elegivel(rt):
            return None
        mem = self.memoria(rt.id)
        agora = self._agora()
        if agora < mem.sonda_sem_rede_ate or not rede.sonda_sem_rede_devida(self.st, rt.id, now().timestamp()):
            return None
        # Reserva a vez já na pergunta (a varredura pergunta a cada 60 s, e o trabalho só roda no ponto seguro).
        mem.sonda_sem_rede_ate = agora + _ESPERA_DA_RELEITURA_S

        async def trabalho() -> None:
            try:
                ipv4, ipv6, detalhe = await medir_saida(self._aparelho(self.st, rt), self.cfg.sonda)
                rede.registrar_saida_sem_rede(self.st, rt.id, ipv4, ipv6, detalhe)
            except Exception as exc:  # noqa: BLE001 - leitura: falha vira "não medida", nunca derruba o trabalho de ligar
                log.info("%s: sonda de IP sem rede pedida não concluiu — %s", rt.id, exc)
                rede.registrar_saida_sem_rede(self.st, rt.id, None, None, f"a sonda não concluiu ({str(exc)[:200]})")

        return trabalho

    async def medir_para_o_login(self, rt: DeviceRuntime) -> EgressoNaJanela | None:
        """31.329: a saída do aparelho AGORA, para o motor de sessão decidir antes de digitar a senha. `None` quando não há
        o que conferir (sem proxy pedido, ou o perfil não declara `egress_esperado`): o login segue como sempre. Medição que
        falhou (sem IP) NÃO é "casou": devolve `casou=False` com o motivo. Nunca levanta."""
        row = self._linha(rt.id)
        if row is None or not row["proxy_profile_id"]:
            return None
        perfil = self.st.db.one("SELECT params FROM network_profiles WHERE id=?", (row["proxy_profile_id"],))
        esperado = (loads(perfil["params"], {}) or {}).get("egress_esperado") if perfil is not None else None
        if not esperado:
            return None
        esperado = str(esperado)
        try:
            ipv4, ipv6, detalhe = await medir_saida(self._aparelho(self.st, rt), self.cfg.sonda)
        except Exception as exc:  # noqa: BLE001 - leitura: falha vira "não casou", nunca derruba o login
            ipv4, ipv6, detalhe = None, None, f"a sonda não concluiu ({str(exc)[:200]})"
        fim = time.monotonic()
        medido_em = now_iso()
        mid = rede.registrar_saida_do_login(self.st, rt.id, ipv4, ipv6, detalhe)
        # A distância é até a decisão de digitar: a medição acaba de terminar, então é o tempo de agora até aqui.
        distancia = max(0.0, time.monotonic() - fim)
        return EgressoNaJanela(esperado=esperado, medido=ipv4, medido_em=medido_em, distancia_s=round(distancia, 3),
                               casou=bool(ipv4) and ipv4 == esperado and distancia <= JANELA_DO_LOGIN_S,
                               medicao_id=mid, detalhe=detalhe[:300])

    async def medir_para_o_cadastro(self, rt: DeviceRuntime, account_id: str) -> EgressoNaJanela | None:
        """31.337: a saída do aparelho AGORA, antes do primeiro toque do cadastro no app. `None` quando o aparelho não tem o
        proxy planejado DESTA conta (perfil `igfarm-<conta>`): o cadastro segue como sempre. Com o perfil e sem
        `egress_esperado` ainda, a primeira medição com IPv4 público vira o esperado (é o IP de criação da conta, medido
        dentro da janela e gravado com rastro); com `egress_esperado`, vale a mesma regra do login (31.329): casar e estar
        dentro dos 30 s. Medição sem IP NÃO é "casou". Nunca levanta."""
        row = self._linha(rt.id)
        if row is None or not row["proxy_profile_id"]:
            return None
        perfil = self.st.db.one("SELECT id, name, params FROM network_profiles WHERE id=?", (row["proxy_profile_id"],))
        if perfil is None or str(perfil["name"]) != f"igfarm-{account_id}":
            return None
        esperado = (loads(perfil["params"], {}) or {}).get("egress_esperado")
        try:
            ipv4, ipv6, detalhe = await medir_saida(self._aparelho(self.st, rt), self.cfg.sonda)
        except Exception as exc:  # noqa: BLE001 - leitura: falha vira "não casou", nunca derruba o cadastro
            ipv4, ipv6, detalhe = None, None, f"a sonda não concluiu ({str(exc)[:200]})"
        fim = time.monotonic()
        medido_em = now_iso()
        mid = rede.registrar_saida_do_login(self.st, rt.id, ipv4, ipv6, detalhe)
        distancia = max(0.0, time.monotonic() - fim)
        if not esperado:
            if not ipv4:
                return EgressoNaJanela(esperado="(a fixar na primeira medição)", medido=None, medido_em=medido_em,
                                       distancia_s=round(distancia, 3), casou=False, medicao_id=mid, detalhe=detalhe[:300])
            try:
                rede.atualizar_saida_esperada(
                    self.st, str(perfil["id"]), rede.NetworkProfileSaidaBody(egress_esperado=ipv4), "cadastro")
            except rede.RedeError as exc:       # IP não público (ex.: 10.x): não vira esperado, e o cadastro não segue
                return EgressoNaJanela(esperado="(a fixar na primeira medição)", medido=ipv4, medido_em=medido_em,
                                       distancia_s=round(distancia, 3), casou=False, medicao_id=mid,
                                       detalhe=f"saída {ipv4} não pôde ser fixada: {exc.message}"[:300])
            return EgressoNaJanela(esperado=ipv4, medido=ipv4, medido_em=medido_em, distancia_s=round(distancia, 3),
                                   casou=True, medicao_id=mid,
                                   detalhe="primeira medição na janela do cadastro: fixada como egress_esperado")
        esperado = str(esperado)
        return EgressoNaJanela(esperado=esperado, medido=ipv4, medido_em=medido_em, distancia_s=round(distancia, 3),
                               casou=bool(ipv4) and ipv4 == esperado and distancia <= JANELA_DO_LOGIN_S,
                               medicao_id=mid, detalhe=detalhe[:300])

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
            invalida = self.invalida(row)
            if invalida == "apps":
                apps = ", ".join(rede.apps_sem_prova(self.st, row))
                frase = (f"rede exigida ({politica}): a medição que verificou o aparelho não cobre {apps} (conta "
                         "vinculada depois dela); ")
            elif invalida == "boot":
                frase = (f"rede exigida ({politica}): o aparelho subiu depois da medição que o verificou (a prova de "
                         f"tráfego é de {row['verified_at']}, do Android de antes); ")
            elif invalida == "bloqueio":
                porque = str(row["leak_detail"] or "sem prova gravada para esta revisão")[:160]
                frase = (f"rede exigida ({politica}): o bloqueio fora da VPN não tem prova que valha para a rev {rev} "
                         f"({porque}); o teste de vazamento para o cliente VPN e pode reiniciar o aparelho; ")
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
        if estado == "parcial" and self._bloqueio_sem_saida(row):
            # Não adianta esperar: o teste já tem desfecho e ele não aprova. Dizer o que destrava.
            frase += (f"; teste de vazamento da rev {rev}: {str(row['leak_detail'] or 'sem detalhe')[:160]} — não é "
                      "refeito sozinho (para o cliente VPN e reinicia o aparelho): peça Verificar")
        return frase

    def invalidar(self, instance_id: str, motivo: str) -> None:
        """Wipe, reset ou outro aparelho físico atrás do id: a configuração aplicada deixou de existir. A linha
        regride a `pendente` (reaplicar no próximo ponto seguro); o pedido vazio (tirar a rede) é dado por feito."""
        row = self._linha(instance_id)
        if row is None:
            return
        self._mem.pop(instance_id, None)
        self._gravar_reinicios(instance_id, 0, 0)        # aparelho novo atrás do id: a conta da linha recomeça (25.11)
        if _vazio(row):
            self._apagar_linha(instance_id, f"dados do aparelho apagados ({motivo}): a rede antiga saiu junto")
            return
        if row["state"] == "pendente" and row["applied_rev"] is None:
            return                                     # nunca aplicada: não há o que invalidar
        # A prova de vazamento era do que estava no disco: outro disco (ou outro aparelho atrás do id), outra prova.
        rede.apagar_prova_de_vazamento(self.st, instance_id, f"dados do aparelho apagados ({motivo})")
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
        # `pm path` sai com código 1 quando o pacote não existe, e o shell da plataforma trata saída diferente de 0 como
        # falha: no android-02 resetado (29/09) a aplicação morria aqui ("adb shell falhou (1)") antes de instalar.
        consulta = f"pm path {pkg} 2>/dev/null; true"
        if "package:" in await ap.shell(consulta, timeout=30):
            return f"{pkg} já instalado"
        alvo = self.st.releases.promoted_release(pkg)
        if alvo is None or alvo.status.value != "installable":
            raise RedeAplicacaoError(f"o cliente VPN {pkg} não está no aparelho e não há versão promovida dele na loja "
                                     "(item 25.10): promova-a para a rede poder ser aplicada")
        await self.st._entregar(rt, pkg, alvo.id)
        if "package:" not in await ap.shell(consulta, timeout=30):
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
            conectividade = getattr(rt, "connectivity", None)
            if "não baixou o perfil" in str(exc) and getattr(conectividade, "state", None) == "unavailable":
                # android-03 (30/09): o aparelho estava sem internet desde antes da troca e o erro dizia só "não baixou
                # o perfil". Não se recusa a aplicação por isto — com o bloqueio ativo e a VPN caída a sonda de internet
                # também dá `unavailable`, e a reaplicação funciona —, só se diz o que a plataforma já mediu.
                exc = RedeAplicacaoError(f"{exc}; a plataforma mede este aparelho SEM internet "
                                         f"({getattr(conectividade, 'detail', '') or 'sonda de internet'}): confira a "
                                         "rede do aparelho — um restart costuma resolver — e peça Reaplicar")
            self._falhou(iid, rev, exc)
            raise exc from None
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
        mem.religar_motivo = mem.religar_codigo = ""   # o de uma passada anterior não enfeita a evidência desta
        if _vazio(row):
            if obs.removida() and reiniciou:
                self._apagar_linha(iid, f"rede tirada e conferida depois do reinício ({obs.descrever(pkg)})")
                await self._garantir_servidor()
                return {"instance_id": iid, "rev": rev, "state": None, "evidence": obs.descrever(pkg)}
            if reiniciou and obs.cliente_solto():
                # Rede tirada e o boot já passou, mas o cliente VPN religou SOZINHO (auto-início): um `tun0` para um par que saiu do
                # servidor. Reiniciar de novo o religaria outra vez (W8, r2: 2º reinício do rollback); o remédio é parar o cliente.
                await parar_cliente_solto(ap, pkg)
                obs = await observar(ap, pkg)
                if obs.removida():
                    self._apagar_linha(iid, f"rede tirada; o cliente religou sozinho no boot e foi parado ({obs.descrever(pkg)})")
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
        religado = ""
        if not obs.conectada(plano_bloqueio) and (reiniciou or (plano_bloqueio and obs.regras_de_bloqueio)):
            # A configuração está no lugar e VALENDO (houve boot depois dela, ou o bloqueio já está em vigor): só o
            # túnel falta — o cliente não subiu neste boot ou morreu depois. Antes de reiniciar (e rolar o mesmo dado
            # de novo: em 30/09 a primeira tentativa do always-on falhou em 5 de 7 boots), o tile do cliente.
            nova = await self._religar_sem_reinicio(ap, iid, plano_bloqueio)
            if nova is not None:
                obs, religado = nova, "religado pelo Start da interface do cliente, sem reinício; "
        if obs.conectada(plano_bloqueio):
            evidencia = f"{religado}túnel no ar, lido como uid 2000: {obs.descrever(pkg)}" + self._par_no_servidor(iid)
            novo = rede.registrar_observacao(self.st, iid, rev=rev, estado="conectado", evidencia=evidencia)
            if mem.reinicios.pop(rev, None) is not None:
                self._gravar_reinicios(iid, rev, 0)
            mem.espera_ate = 0.0
            mem.ultima_conferencia = self._agora()
            # A primeira medição da conexão sai no próximo ponto seguro, não na deriva (`rede.deriva_s`, 15 min): no
            # android-05 (29/09) o `conectado` esperou 16 min para medir. A prova de vazamento da linha não é
            # refeita por isto (o que a invalida é revisão, cliente, wipe ou o `POST …/verify`).
            mem.verificacao_pedida = True
            await self._guardar_linha_de_base(ap, iid, rev)
            return {"instance_id": iid, "rev": rev, "state": novo.state, "evidence": evidencia}
        return self._reiniciar_ou_desistir(rt, row, obs, reiniciou, "o túnel não subiu")

    async def _religar_sem_reinicio(self, ap: AparelhoDaRede, iid: str, bloqueio: bool) -> Observacao | None:
        """O túnel de volta pelo Start da interface do cliente (`rede.cliente_atividade`), sem reiniciar o aparelho;
        `None` se não deu (ou se o gesto está desligado). Quem chama garante que só o túnel falta: a configuração, o
        always-on e o bloqueio ficam como estão, e a releitura final é a de sempre (uid 2000, com as regras de bloqueio
        conferidas). O motivo do desfecho fica em `memoria(iid).religar_motivo` (para a evidência de quem cair no
        reinício). NÃO há fallback para o tile: ele não recalcula o `serviceMode` do SFA (W8) e, com o motivo da
        interface desconhecido, tentá-lo só repetiria o ProxyService; a recuperação que sobra é a de sempre, o reinício
        (always-on), dentro de `rede.reinicios_max`."""
        mem = self.memoria(iid)
        mem.religar_motivo = mem.religar_codigo = ""
        atividade = str(self.cfg.cliente_atividade or "").strip()
        if not atividade:
            return None
        try:
            r = await religar_pela_interface(ap, self.cfg.cliente_pacote, atividade, espera_s=self.espera_da_interface_s,
                                             prazo_do_botao_s=self.prazo_do_botao_s, pausa_s=self.pausa_da_interface_s)
        except Exception as exc:  # noqa: BLE001 - o gesto é uma tentativa: sem ele, vale o reinício de sempre
            mem.religar_codigo = "erro_inesperado"
            mem.religar_motivo = f"religar pela interface: {type(exc).__name__}: {str(exc)[:160]}"
            log.info("%s: o Start da interface do cliente VPN não religou o túnel — %s", iid, exc)
            return None
        log.info("%s: %s (%s)", iid, r.detalhe, r.codigo)
        if not r.religado or r.obs is None or not r.obs.conectada(bloqueio):
            mem.religar_codigo = r.codigo
            mem.religar_motivo = f"religar pela interface: {r.codigo}: {r.detalhe}"
            if r.religado:                                  # o túnel subiu, mas sem as regras de bloqueio da política
                mem.religar_codigo = "sem_regras_de_bloqueio"
                mem.religar_motivo = "religar pela interface: túnel no ar sem as regras de bloqueio da política"
            if r.codigo == CLASSE_ERRADA_PARA_TUN:          # guard D: dito alto, não consumido como timeout genérico
                self.st.bus.emit("log", f"{iid}: {r.detalhe}", instance_id=iid)
            return None
        self.st.bus.emit("log", f"{iid}: {r.detalhe}", instance_id=iid)
        return r.obs

    def _motivo_da_interface(self, iid: str) -> str:
        """Por que o Start da interface não religou (se foi tentado nesta passada): vai na evidência, sem segredo."""
        motivo = self.memoria(iid).religar_motivo
        return f" ({motivo})" if motivo else ""

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
            erro += self._motivo_da_interface(iid)
            self._falhou(iid, rev, RedeAplicacaoError(erro))
            raise RedeAplicacaoError(erro)
        detalhe = (f"{o_que} depois do boot ({obs.descrever(pkg)}); novo reinício pedido" if reiniciou
                   else f"configurado, sem reinício desde então ({obs.descrever(pkg)}); reinício pedido")
        detalhe += self._motivo_da_interface(iid)
        # O motivo do reinício (200 caracteres) SUBSTITUI o `detail` da linha: o código da interface vai na frente.
        codigo = self.memoria(iid).religar_codigo
        motivo_do_reinicio = (f"[interface: {codigo}] " if codigo else "") + detalhe
        rede.registrar_observacao(self.st, iid, rev=rev, estado="configurado", evidencia=detalhe)
        self._agendar_reinicio(iid, rev, motivo_do_reinicio[:200])
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
        if row["policy"] == "exigida_com_bloqueio":
            # Ensaio que sobrou de um backend reiniciado, ou cliente VPN de outra instalação: a linha passa a dizer
            # isso já na conferência, e a porta da tarefa (que só lê o banco) deixa de aceitar a prova.
            self._prova_conferida(iid, rev, obs.cliente)
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
        vazamento — que, com bloqueio, para o cliente VPN e reinicia o aparelho (o `reason` diz). A marca da sonda é
        em memória (um reinício do backend a perde); a prova de vazamento apagada é do banco (`rede.pedir_verificacao`),
        e é a falta dela que dispara o teste, com ou sem reinício do backend no meio."""
        resposta = rede.pedir_verificacao(self.st, instance_id, quem)
        mem = self.memoria(instance_id)
        mem.verificacao_pedida = True
        mem.espera_ate = 0.0
        row = self._linha(instance_id)
        if row is not None and row["policy"] == "exigida_com_bloqueio":
            resposta = {**resposta, "reason": f"{resposta.get('reason', '')} Com a política exigida_com_bloqueio, o "
                                              "teste de vazamento é refeito: o cliente VPN é parado para a sonda e o "
                                              "aparelho REINICIA para religá-lo (a medição vem depois do boot). A "
                                              "prova anterior foi apagada: até a nova, a tarefa com rede exigida "
                                              "espera."}
        return resposta

    def verificar_ao_subir(self) -> list[str]:
        """O backend acabou de subir (25.12): marca a verificação do tráfego de todo aparelho com política exigida
        (`exigida` e `exigida_com_bloqueio`) e rede já conectada (`conectado`, `parcial`, `trafego_verificado`) para o
        próximo ponto seguro de cada um (readoção ao ligar ou varredura de 60 s, com o aparelho livre). Todo reinício
        do backend refaz o servidor do túnel e derruba os túneis; sem isto, o `trafego_verificado` seguia valendo até a
        medição vencer (6 h) e o `conferir` só vê `tun0`/`CONNECTED`, que o cliente morto ainda mostra (android-03, 03/10).

        NÃO usa `pedir_verificacao`/`POST …/verify`: com `exigida_com_bloqueio` aquele pedido APAGA a prova de vazamento
        da linha e refaz o teste, que para o cliente VPN e reinicia o aparelho — e o reinício do backend não mudou o
        cliente do aparelho nem o bloqueio (a prova segue valendo; o que caiu foi o túnel). Aqui só se marca a MEDIÇÃO
        de tráfego (a marca de memória que o `verify` também deixa): a prova fica, o `detail` não é reescrito, nenhum
        evento é emitido. A medição sem IP é o túnel morto (`_tunel_morto`). Devolve os aparelhos marcados."""
        marcados: list[str] = []
        for row in self.st.db.query(
                "SELECT instance_id FROM device_network WHERE policy IN ('exigida','exigida_com_bloqueio')"
                " AND state IN ('conectado','parcial','trafego_verificado')"):
            iid = str(row["instance_id"])
            mem = self.memoria(iid)
            mem.verificacao_pedida = True
            mem.espera_ate = 0.0
            marcados.append(iid)
        if marcados:
            log.info("rede: verificação do tráfego marcada ao subir para %s", ", ".join(sorted(marcados)))
        return marcados

    async def _guardar_linha_de_base(self, ap: AparelhoDaRede, instance_id: str, rev: int) -> None:
        """A contabilidade por UID de quando o túnel conectou: a janela da primeira medição dos apps começa aqui (o
        tráfego desde a conexão conta, o de antes não). Sem ela (leitura que falhou), a janela é só a da medição."""
        try:
            self.memoria(instance_id).linha_de_base = (rev, await contabilidade(ap))
        except Exception as exc:  # noqa: BLE001 - só encurta a janela da primeira medição; o `conectado` vale
            log.info("%s: contabilidade da conexão não lida — %s", instance_id, exc)

    async def _tunel_de_volta(self, ap: AparelhoDaRede, pkg: str, bloqueio: bool) -> bool:
        """Depois de parar o cliente para o teste de vazamento: o túnel voltou sozinho (o always-on religou)? No 25.1
        não voltava (18:07); no android-05 (29/09), com always-on e lockdown, voltava em menos de um segundo — os dois
        acontecem, por isso se lê. Uma leitura que falha conta como não: religar pelo boot é o lado seguro."""
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
        desfecho do teste já gravado na linha."""
        self.memoria(iid).configurado_em = time.time()
        novo = rede.registrar_observacao(
            self.st, iid, rev=rev, estado="configurado",
            evidencia=f"teste de vazamento ({vazamento.texto[:220]}); o cliente VPN foi parado para o teste e o "
                      f"túnel não voltou sozinho sem boot: reinicia, e a medição vem depois{self._motivo_da_interface(iid)}")
        # O desfecho do teste vai no motivo: a evidência do reinício substitui o `detail` da linha.
        self._agendar_reinicio(iid, rev, f"religar o cliente VPN depois do teste de vazamento da rev {rev} "
                                         f"({vazamento.texto[:200]})")
        return {"instance_id": iid, "rev": rev, "state": novo.state, "measured": False,
                "leak_blocked": vazamento.bloqueado, "evidence": novo.detail}

    def _prova_conferida(self, iid: str, rev: int, cliente: str) -> rede.ProvaDeVazamento:
        """A prova de vazamento da linha, depois de conferida contra o que acabou de ser lido do aparelho:
        - ensaio marcado e sem desfecho, com ninguém o rodando neste processo (o backend reiniciou no meio): fecha como
          inconclusivo. O cliente VPN NÃO é parado de novo — o túnel caído, se for o caso, é deriva, e o caminho de
          sempre (`configurado` → reinício) o religa;
        - prova desta revisão feita com OUTRA instalação do cliente VPN (atualizado ou reinstalado): apagada."""
        row = self._linha(iid)
        assert row is not None
        prova = rede.prova_de_vazamento(row)
        if prova.pendente and not self.memoria(iid).ensaio_em_curso:
            rede.fechar_ensaio_interrompido(self.st, iid)
            prova = rede.prova_de_vazamento(self._linha(iid) or row)
        if prova.situacao(rev, cliente) == "outro_cliente":
            rede.apagar_prova_de_vazamento(
                self.st, iid, f"o cliente VPN instalado mudou (a prova era de {prova.cliente or 'instalação não lida'}; "
                              f"agora {cliente})")
            prova = rede.prova_de_vazamento(self._linha(iid) or row)
        return prova

    async def _adotar_prova_anterior(self, ap: AparelhoDaRede, iid: str, rev: int, cliente: str) -> Vazamento | None:
        """A transição da migração 063 (`rede.prova_anterior`): a prova que o backend anterior fez e guardou só em
        memória é adotada quando o histórico a sustenta E o cliente VPN instalado é anterior ao teste — lido agora, do
        aparelho. `None` = não há o que adotar (segue para o ensaio). A leitura que falha levanta: sem saber de quando
        é o cliente não se adota, e também não se para o cliente às cegas; a passada seguinte tenta de novo (o comando
        que falhou não conta como evidência em `prova_anterior`)."""
        row = self._linha(iid)
        anterior = rede.prova_anterior(self.st, row) if row is not None else None
        if anterior is None:
            return None
        gravado, relogio = await instalado_em(ap, self.cfg.cliente_pacote)
        teste = _epoch(anterior.testado_em)
        # A data do APK é do relógio do aparelho e a do teste é do servidor: com os relógios separados a comparação
        # não prova nada (um aparelho atrasado faria um cliente novo parecer anterior ao teste).
        desvio = abs(relogio - time.time())
        if teste is None or gravado >= teste or desvio > _DESVIO_DE_RELOGIO_S:
            log.info("%s: prova de vazamento anterior NÃO adotada (cliente VPN gravado em %s, teste a partir de %s, "
                     "relógio do aparelho a %.0f s do servidor)", iid, gravado, anterior.testado_em, desvio)
            return None
        quando = datetime.fromtimestamp(gravado, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not rede.adotar_prova_de_vazamento(self.st, iid, rev=rev, cliente=cliente, anterior=anterior,
                                              instalado_em=quando):
            return None
        novo = self._linha(iid)
        return Vazamento(True, str(novo["leak_detail"]) if novo is not None else "prova adotada do histórico",
                         anterior.testado_em)

    async def _prova_ou_ensaio(self, rt: DeviceRuntime, ap: AparelhoDaRede, iid: str, rev: int,
                               cliente: str) -> Vazamento | dict[str, object]:
        """O que a medição desta passada leva como teste de vazamento, ou o desfecho da passada quando o ensaio deixou
        o cliente parado (`dict`: reinicia e mede depois do boot).

        Com desfecho gravado para esta revisão e esta instalação do cliente — provou, vazou ou não concluiu —, é ele,
        sem tocar no aparelho: refazer a cada medição reiniciaria o aparelho em laço, e só o que provou aprova. Sem
        desfecho (nunca testado, revisão nova, cliente novo, prova apagada), o ensaio: a intenção é gravada ANTES do
        `force-stop` e o resultado depois, com CAS pela revisão; o que acontecer no meio (o backend caindo) fica como
        ensaio interrompido, e não como teste a repetir."""
        pkg = self.cfg.cliente_pacote
        mem = self.memoria(iid)
        prova = self._prova_conferida(iid, rev, cliente)
        if prova.situacao(rev, cliente) == "ausente":
            # Só na linha que o mecanismo novo nunca escreveu: a prova que o código anterior à 063 deixou no histórico.
            adotada = await self._adotar_prova_anterior(ap, iid, rev, cliente)
            if adotada is not None:
                return adotada
        elif prova.situacao(rev, cliente) in ("vale", "vazou", "inconclusiva"):
            return Vazamento(prova.resultado, prova.detalhe or "teste de vazamento desta revisão, sem detalhe gravado",
                             prova.quando or now_iso())
        adiado = self._vazamento_adiado(rt)
        if adiado is not None:
            # Sem gravar: a próxima medição tenta de novo. O `parcial` que sai daqui diz o porquê.
            return Vazamento(None, adiado, now_iso())
        if not rede.marcar_ensaio_de_vazamento(self.st, iid, rev=rev, cliente=cliente):
            return Vazamento(None, "teste de vazamento não iniciado: a revisão pedida mudou durante a verificação",
                             now_iso())
        mem.ensaio_em_curso = True
        teste: TesteDeVazamento
        try:
            try:
                teste = await sondar_vazamento(ap, self.cfg.sonda, pkg)
            except Exception as exc:
                # Antes de parar o cliente (a sonda com o túnel no ar não leu): nada foi tocado.
                rede.desmarcar_ensaio_de_vazamento(self.st, iid, rev=rev, motivo=str(exc)[:200])
                mem.espera_ate = max(mem.espera_ate, self._agora() + float(self.cfg.sonda.reverificar_s))
                raise
            if not teste.cliente_parado:
                # A sonda nem saiu com o túnel no ar: o cliente não foi parado e nada foi provado.
                rede.desmarcar_ensaio_de_vazamento(self.st, iid, rev=rev, motivo=teste.vazamento.texto)
                return teste.vazamento
            # Gravado mesmo sem conclusão (`bloqueado=None`): refazer a cada medição reiniciaria o aparelho em laço.
            # Um POST …/verify apaga e refaz.
            rede.gravar_prova_de_vazamento(self.st, iid, rev=rev, cliente=cliente, resultado=teste.vazamento.bloqueado,
                                           quando=teste.vazamento.medido_em, detalhe=teste.vazamento.texto)
        finally:
            # Inclusive no cancelamento (o backend parando): a marca em memória sai, e o `leak_pending` que ficou no
            # banco é o que diz, a quem vier depois, que o ensaio não terminou.
            mem.ensaio_em_curso = False
        if not await self._tunel_de_volta(ap, pkg, True):
            # O cliente ficou parado. Primeiro o Start da interface (sem reinício); só se ele não religar, o boot.
            if await self._religar_sem_reinicio(ap, iid, True) is None:
                return self._religar_pelo_boot(iid, rev, teste.vazamento)
        return teste.vazamento

    async def _verificar(self, rt: DeviceRuntime, row: Row, motivo: Motivo) -> dict[str, object]:
        """Relê como o conferir (deriva regride, e aí não há o que medir) e, com o túnel no ar lido como uid 2000,
        mede a saída de dentro do aparelho e grava a medição. `trafego_verificado`/`parcial` saem SÓ de
        `rede.registrar_medicao`, pelas regras dele; aqui só se escolhe a janela, se os apps são abertos e o teste de
        vazamento.

        A prova de vazamento (política com bloqueio) vem ANTES da medição (`_prova_ou_ensaio`): a da linha, se vale para
        esta revisão e esta instalação do cliente VPN; senão o teste, que para o cliente (`rede_medicao.sondar_vazamento`),
        e o que se mede depois é o aparelho com o túnel de volta. Os apps exigidos sem tráfego na janela são abertos
        quando uma tarefa está segurada pela porta (`motivo='tarefa'`: é o que ela faria, e sem isso o app nunca aberto
        seguraria a tarefa para sempre) ou com `rede.sonda.abrir_apps`."""
        iid, rev = rt.id, int(row["desired_rev"])
        pkg = self.cfg.cliente_pacote
        ap = self._aparelho(self.st, rt)
        mem = self.memoria(iid)
        a_pedido = motivo == "pedido" or mem.verificacao_pedida
        mem.verificacao_pedida = False
        agora = self._agora()
        mem.ultima_verificacao = mem.ultima_conferencia = agora
        obs = await self._observar_depois_do_boot(ap, row, pkg)
        if await self._regredir_se_derivou(ap, row, obs, f"verificação, {motivo}"):
            novo = self._linha(iid)
            return {"instance_id": iid, "rev": rev, "state": novo["state"] if novo else None,
                    "evidence": obs.descrever(pkg), "measured": False}
        bloqueio = row["policy"] == "exigida_com_bloqueio"
        vazamento: Vazamento | None = None
        if bloqueio:
            desfecho = await self._prova_ou_ensaio(rt, ap, iid, rev, obs.cliente)
            if isinstance(desfecho, dict):
                return desfecho                    # o cliente ficou parado: reinicia, e a medição vem depois do boot
            vazamento = desfecho
            if vazamento.bloqueado is not True and row["state"] == "parcial" and not a_pedido \
                    and not self._medicao_envelhecida(row) and not rede.parcial_so_de_app_parado(self.st, iid):
                # Sem prova do bloqueio, nenhuma medição leva a `trafego_verificado`, e esta linha já foi medida
                # dentro da validade: medir de novo não muda nada. No android-05 (30/09), com o teste adiado por um
                # objetivo parado, a sonda rodou 34 vezes em seis horas para escrever o mesmo `parcial`. A pedido
                # (`POST …/apply`, `…/verify`) mede; vencida a validade, mede (a saída medida não fica velha). O
                # `parcial` só de app parado (29.44) mede: o app pode ter trafegado, e é isso que a medição relê.
                mem.medida_sem_abrir = False
                mem.espera_ate = max(mem.espera_ate, self._agora() + float(self.cfg.sonda.reverificar_s))
                return {"instance_id": iid, "rev": rev, "state": "parcial", "measured": False,
                        "leak_blocked": vazamento.bloqueado,
                        "evidence": f"medição dispensada: o bloqueio fora da VPN segue sem prova ({vazamento.texto[:300]})"}
        base = mem.linha_de_base[1] if mem.linha_de_base is not None and mem.linha_de_base[0] == rev else None
        abrir = motivo == "tarefa" or bool(self.cfg.sonda.abrir_apps)
        try:
            r = await medir(ap, self.cfg.sonda, exigidos=rede.apps_exigidos(self.st, iid), linha_de_base=base,
                            bloqueio=bloqueio, vazamento=vazamento, abrir=abrir)
        except Exception:
            mem.espera_ate = max(mem.espera_ate, self._agora() + float(self.cfg.sonda.reverificar_s))
            raise
        sem_saida = not (r.medicao.egress_ipv4 or r.medicao.egress_ipv6)
        if sem_saida and row["state"] == "trafego_verificado" and row["policy"] != "livre":
            # Verificado e a sonda não sai mais: o túnel está morto (com o backend do central reiniciado, o servidor
            # do túnel é refeito e o cliente do aparelho segue "no ar" sem handshake). Religar, ou reiniciar.
            refeita = await self._tunel_morto(rt, ap, row, r, lambda: medir(
                ap, self.cfg.sonda, exigidos=rede.apps_exigidos(self.st, iid), linha_de_base=base, bloqueio=bloqueio,
                vazamento=vazamento, abrir=abrir))
            if isinstance(refeita, dict):
                return refeita
            r = refeita
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

    async def _tunel_morto(self, rt: DeviceRuntime, ap: AparelhoDaRede, row: Row, r: ResultadoDaSonda,
                           refazer: Callable[[], Awaitable[ResultadoDaSonda]]) -> ResultadoDaSonda | dict[str, object]:
        """A sonda de um `trafego_verificado` com política exigida não mediu IP de saída (25.12): o túnel está morto,
        e antes isto só escrevia "o estado não muda" e deixava a tarefa passar por uma rede sem saída (android-03,
        03/10: túnel caído depois do reinício do backend, internet de volta só com reinício manual; medição #216).

        1. a medição fica no histórico e a linha SAI de `trafego_verificado` (→ `conectado`, com o motivo e o evento
           `tunel_morto`): a porta da tarefa segura até uma medição nova com IP;
        2. até `_RELIGADAS_DO_TUNEL_MORTO` vezes: `force-stop` do cliente (o always-on o religa) e, se o `tun0` não
           voltar, o Start da interface (só sem objetivo no meio: ele abre a tela do cliente); com o túnel de volta, a
           sonda de IP confere. Voltou → devolve a medição COMPLETA refeita (o que sai dali é a medição normal);
        3. sem volta: `configurado` e reinício do aparelho pelo caminho de sempre (`restart`, nunca wipe), que passa
           pelas mesmas guardas de objetivo no meio e de teto de reinícios; o `conectar` confere depois do boot.

        A prova de vazamento não é tocada em nenhum ramo (o cliente é o mesmo; o teste de vazamento é outro gesto).
        Só `trafego_verificado` entra aqui: `conectado`/`parcial` sem IP seguem como eram (não liberam tarefa, e a
        medição que não verificou espera `reverificar_s`), e o que acabou de voltar de um reinício não reinicia de novo."""
        iid, rev = rt.id, int(row["desired_rev"])
        pkg = self.cfg.cliente_pacote
        bloqueio = row["policy"] == "exigida_com_bloqueio"
        mem = self.memoria(iid)
        mid, _ = rede.registrar_medicao(self.st, iid, r.medicao, rev=rev)
        # Curto: o `detail` da linha tem 500 caracteres e o que importa (o desfecho das tentativas) vem depois.
        sintoma = f"túnel morto: a sonda não mediu IP de saída (medição #{mid})"
        rede.registrar_observacao(self.st, iid, rev=rev, estado="conectado",
                                  evidencia=f"{sintoma}; sai de trafego_verificado e tenta religar o cliente VPN")
        self.st.bus.emit("network.updated", f"Rede de {iid}: {sintoma}: {r.medicao.detail[:160]}", level="warn",
                         instance_id=iid,
                         data={"instance_id": iid, "acao": "tunel_morto", "measurement_id": mid, "desired_rev": rev,
                               "state": "conectado"})
        ocupado = objetivo_em_andamento(self.st, iid, exceto_quem_espera_a_rede=True)
        passos: list[str] = []
        for n in range(1, _RELIGADAS_DO_TUNEL_MORTO + 1):
            try:
                await parar_cliente_solto(ap, pkg, espera_s=self.pausa_do_force_stop_s)
                obs = await observar(ap, pkg, esperar_tun_s=self.espera_do_religar_s,
                                     intervalo_s=self.intervalo_do_religar_s)
                if not obs.conectada(bloqueio) and not ocupado:
                    try:
                        nova = await asyncio.wait_for(self._religar_sem_reinicio(ap, iid, bloqueio),
                                                      self.prazo_da_interface_no_tunel_morto_s)
                    except asyncio.TimeoutError:
                        nova = None
                        mem.religar_codigo = "prazo_estourado"
                        mem.religar_motivo = (f"religar pela interface: passou de "
                                              f"{self.prazo_da_interface_no_tunel_morto_s:g} s")
                    obs = nova if nova is not None else obs
                if not obs.conectada(bloqueio):
                    passos.append(f"{n}ª: o cliente não voltou (tun0 {'no ar' if obs.tun else 'ausente'}, VPN "
                                  f"{'conectada' if obs.vpn_conectada else 'não conectada'}){self._motivo_da_interface(iid)[:110]}")
                    continue
                ipv4, ipv6, detalhe = await medir_saida(ap, self.cfg.sonda)
            except Exception as exc:  # noqa: BLE001 - cada tentativa é um gesto: a falha vira passo, e o reinício é o fim
                passos.append(f"{n}ª: {type(exc).__name__}: {str(exc)[:80]}")
                continue
            if ipv4 or ipv6:
                self.st.bus.emit("log", f"{iid}: túnel religado sem reinício (cliente parado e religado, {n}ª tentativa); "
                                        "medindo de novo", instance_id=iid)
                mem.verificacao_pedida = True              # se a medição completa falhar, a seguinte sai logo
                refeita = await refazer()
                mem.verificacao_pedida = False
                return refeita
            passos.append(f"{n}ª: cliente religado, a sonda segue sem IP")
        resumo = "; ".join(passos)
        if ocupado:
            resumo += "; Start da interface não tentado (objetivo no meio neste aparelho)"
        texto = f"{sintoma}; religar o cliente VPN não bastou em {_RELIGADAS_DO_TUNEL_MORTO} tentativas ({resumo})"
        mem.configurado_em = time.time()
        novo = rede.registrar_observacao(self.st, iid, rev=rev, estado="configurado",
                                         evidencia=f"{texto}; reinicia o aparelho (sem apagar dados)")
        self._agendar_reinicio(iid, rev, f"túnel morto da rev {rev}: religar não bastou ({resumo})"[:200])
        return {"instance_id": iid, "rev": rev, "state": novo.state, "measurement_id": mid, "measured": True,
                "evidence": novo.detail}

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
                                                  motivo, _TENTATIVAS_DO_REINICIO)
            # Ciclo novo: o `detail` foi reescrito por quem pediu (`registrar_observacao`), diga de novo o que segurar.
            self._limpar_reinicio_segurado(instance_id)
        except RuntimeError:
            self._reinicio_agendado.discard(instance_id)

    def _pedir_reinicio(self, instance_id: str, rev: int, motivo: str, restantes: int) -> None:
        row = self._linha(instance_id)
        rt = self.st.devices.devices.get(instance_id)
        if row is None or int(row["desired_rev"]) != rev or row["state"] != "configurado" or rt is None \
                or self.st.quarentena(instance_id) is not None:
            # Pedido velho (a revisão mudou, a linha saiu de `configurado`, o aparelho sumiu): nada a dizer na linha.
            log.info("%s: reinício da rede descartado: o pedido da rev %s já não vale", instance_id, rev)
            self._reinicio_agendado.discard(instance_id)
            if row is not None and rt is not None:
                self._limpar_reinicio_segurado(instance_id)
            return
        if rt.state != InstanceState.online:
            self._reinicio_agendado.discard(instance_id)
            self._dizer_reinicio_segurado(instance_id, rev, f"estado {rt.state.value}",
                                          f"reinício da rede não saiu: aparelho {rt.state.value}, não online; "
                                          "pede de novo quando ligar")
            self._limpar_reinicio_segurado(instance_id)
            return
        segura = self._quem_segura_o_reinicio(rt)
        if segura is not None:
            if restantes > 0:
                # Não ruidoso: só a PRIMEIRA recusa e cada mudança de termo vão para a linha (tentativa é de 5 em 5 s).
                self._dizer_reinicio_segurado(instance_id, rev, segura, f"reinício da rede aguarda o aparelho: {segura}")
                asyncio.get_running_loop().call_later(self.intervalo_do_reinicio_s, self._pedir_reinicio,
                                                      instance_id, rev, motivo, restantes - 1)
                return
            esperando = objetivo_esperando_a_rede(self.st, instance_id)
            if esperando is not None:
                # Um objetivo espera ESTE reinício (29.21): 5 min de mudez deixavam a execução presa (android-05,
                # 02/10). Fica agendado e tenta de novo logo, enquanto o objetivo seguir esperando a rede.
                self._dizer_reinicio_segurado(
                    instance_id, rev, segura, f"reinício da rede não saiu em {_TENTATIVAS_DO_REINICIO + 1} tentativas: "
                    f"{segura}; o objetivo {esperando} espera a rede, nova tentativa em "
                    f"{round(self.retentativa_do_reinicio_s)} s", esgotou=True)
                asyncio.get_running_loop().call_later(self.retentativa_do_reinicio_s, self._pedir_reinicio,
                                                      instance_id, rev, motivo, _TENTATIVAS_DO_REINICIO)
                return
            # Ocupado o tempo todo (tarefas seguidas com política livre): a varredura volta a tentar depois.
            self._dizer_reinicio_segurado(
                instance_id, rev, segura, f"reinício da rede não saiu em {_TENTATIVAS_DO_REINICIO + 1} tentativas: "
                f"{segura}; a varredura tenta de novo em {round(_ESPERA_DA_RELEITURA_S / 60)} min", esgotou=True)
            self._reinicio_agendado.discard(instance_id)
            self.memoria(instance_id).espera_ate = self._agora() + _ESPERA_DA_RELEITURA_S
            return
        self._reinicio_agendado.discard(instance_id)
        cid = despacho.pedir_ciclo_de_vida(self.st, instance_id, "restart", f"rede: {motivo}", requested_by=QUEM)
        mem = self.memoria(instance_id)
        # Recusado também conta para o teto (`rede.reinicios_max`): um worker sem o verbo recusaria para sempre.
        mem.reinicios[rev] = mem.reinicios.get(rev, 0) + 1
        self._gravar_reinicios(instance_id, rev, mem.reinicios[rev])
        if cid is None:
            # Recusado (verbo que o worker não tem, manutenção, quarentena): a recusa já está no histórico do aparelho,
            # EXCETO o verbo ausente e a pausa do reparo, que não abrem linha (`pedir_ciclo_de_vida` devolve None).
            causa = ("o worker do aparelho não tem o verbo restart" if "restart" not in (rt.worker_verbs or [])
                     else "recusado no pré-voo ou pela pausa de reparo; veja o histórico do aparelho")
            log.info("%s: o reinício da rede não foi aceito agora (%s); a varredura pede de novo", instance_id, causa)
            self._dizer_reinicio_segurado(instance_id, rev, f"recusa: {causa}",
                                          f"reinício da rede não foi aceito: {causa}")
            self._limpar_reinicio_segurado(instance_id)
            mem.espera_ate = self._agora() + _ESPERA_DA_RELEITURA_S
            return
        self._limpar_reinicio_segurado(instance_id)
        # O que foi importado e relido fica no desfecho do comando `device.network`; aqui, o porquê e o id do reinício.
        rede.registrar_observacao(self.st, instance_id, rev=rev, estado="configurado",
                                  evidencia=f"{motivo[:300]}; reinício {cid} pedido")

    def _quem_segura_o_reinicio(self, rt: DeviceRuntime) -> str | None:
        """Por que o reinício ainda não pode sair (None: livre). O objetivo suspenso pela porta da rede espera ESTE
        reinício: não conta (25.6, `exceto_quem_espera_a_rede`); o worker, que é quem mexe no aparelho, é conferido à
        parte. Contá-lo travaria o objetivo e a rede juntos. Cada termo diz o que o segurou (29.21)."""
        iid = rt.id
        if iid in self.st.scheduler.workers:
            do_objetivo = getattr(self.st.scheduler, "_objetivo_do_worker", {}).get(iid)
            return "worker no scheduler" + (f" (objetivo {do_objetivo})" if do_objetivo else "")
        if rt.control != ControlOwner.none:
            return "controle da IA" if rt.control == ControlOwner.ai else "controle de uma pessoa"
        if (oid := objetivo_que_segura(self.st, iid, exceto_quem_espera_a_rede=True)) is not None:
            return f"objetivo {oid} em andamento"
        if (aberto := self.st.commands.open_for_instance(iid, verbs=despacho.VERBOS_EXCLUSIVOS)) is not None:
            return f"comando exclusivo aberto {aberto['id']} ({aberto['verb']})"
        return None

    def _dizer_reinicio_segurado(self, instance_id: str, rev: int, termo: str, texto: str, *,
                                 esgotou: bool = False) -> None:
        """Grava na linha (`detail`) por que o reinício não saiu, UMA vez por termo (e uma para o esgotamento): a
        tentativa é de 5 em 5 s e o `detail` é o que o painel mostra."""
        mem = self.memoria(instance_id)
        if mem.reinicio_segurado == termo and (mem.reinicio_esgotado or not esgotou):
            return
        mem.reinicio_segurado, mem.reinicio_esgotado = termo, esgotou
        log.info("%s: %s", instance_id, texto)
        try:
            rede.registrar_observacao(self.st, instance_id, rev=rev, estado="configurado", evidencia=texto)
        except (rede.RedeError, ValueError):
            log.info("%s: a linha de rede sumiu antes de registrar o reinício parado", instance_id)

    def _limpar_reinicio_segurado(self, instance_id: str) -> None:
        mem = self.memoria(instance_id)
        mem.reinicio_segurado, mem.reinicio_esgotado = "", False

    def _falhou(self, instance_id: str, rev: int, exc: BaseException) -> None:
        mem = self.memoria(instance_id)
        n = mem.falhas[rev] = mem.falhas.get(rev, 0) + 1
        espera = min(3600.0, 300.0 * 3 ** (n - 1))
        mem.espera_ate = self._agora() + espera
        if mem.reinicios.pop(rev, None) is not None:
            self._gravar_reinicios(instance_id, rev, 0)     # a desistência fecha a conta, aqui e na linha (25.11)
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

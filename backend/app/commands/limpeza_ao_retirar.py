"""Limpeza automática do app ao retirar uma conta bloqueada (item 29.27, emenda do ADR-068).

A regra do dono (02/10): conta do app âncora confirmada bloqueada e RETIRADA da plataforma leva os dados do app embora
dos aparelhos onde estava logada. Só isso: `pm clear` do PACOTE que o `app.yaml` declara (`limpar_ao_retirar`), com
captura de tela antes e depois, nenhum toque na tela (nem na de bloqueio) e nenhum outro pacote. Em seguida a quarentena
(ADR-055) é resolvida pelo mesmo caminho da rota manual do 29.24 (`resolver_conta_travada`), com a nota que diz quem
decidiu. Retroativo continua sendo decisão de pessoa (a rota manual); nada aqui roda na subida.

O canal é o que o produto já usa para apagar dados de app: o comando `session.logout` (o `pm clear` por `Adb.clear_data`
do `api._do_logout`), aberto por `comando_no_trabalho` DENTRO de um trabalho de aparelho (`run_device_job`), que é a
exclusividade que o resto do produto respeita. `Adb` vale também para aparelho de worker remoto (o túnel é o transporte),
então não há segundo caminho. Não usa `_despachar_trabalho`: ele recusa verbo de sessão com a conta travada logada, e
é exatamente esse aparelho que a limpeza precisa tocar. A confirmação de quarentena que a pessoa daria
(`confirm_locked_account`) só é dada pelo SISTEMA aqui, para acordar o aparelho e para mais nada.

Por aparelho, uma vez, em sequência: acorda se estiver hibernado ou parado (só se todo marcador aberto dali é do pedido)
→ captura antes → trava "outra conta" → `pm clear` → captura depois → resolve a quarentena → devolve a energia ao que era. Falha em qualquer passo: a quarentena continua aberta, sai o
evento `device.account_cleanup` em erro (o aviso de atenção) e NADA é repetido: uma tentativa por retirada. Nem o evento
nem o log carregam o @ da conta (29.24): só ids, o pacote e o aparelho.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..models import InstanceActionBody, InstanceState
from ..social.limpeza_de_conta import AparelhoDaLimpeza, PedidoDeLimpeza
from ..storage import put_async
from ..util import now_iso
from . import despacho

if TYPE_CHECKING:  # pragma: no cover - só para o verificador de tipos
    from ..devices.manager import DeviceRuntime
    from ..state import AppState

log = logging.getLogger("poc.limpeza_ao_retirar")

#: O verbo do comando que apaga os dados do app: o mesmo do "sair da conta" (`session.logout`, `api._do_logout`).
VERBO = "session.logout"
#: Quem pede, no histórico do comando e no marcador resolvido.
QUEM = "sistema:limpeza-ao-retirar"
#: A nota que a resolução da quarentena carrega: a decisão é do dono, e é a regra, não uma pessoa clicando.
NOTA = "limpeza automática autorizada pelo dono em 02/10"
#: Verbo de energia para acordar, por estado de partida; e o de volta.
_ACORDAR = {InstanceState.hibernated: "wake", InstanceState.stopped: "start"}
_DEVOLVER = {InstanceState.hibernated: "hibernate", InstanceState.stopped: "stop"}


class LimpezaFalhou(Exception):
    """Um passo da limpeza de UM aparelho não se completou; `passo` diz qual (vai para o evento)."""

    def __init__(self, passo: str, motivo: str) -> None:
        super().__init__(motivo)
        self.passo, self.motivo = passo, motivo


@dataclass
class Desfecho:
    """O que aconteceu num aparelho: `concluida`, `falhou` ou `dispensada` (a quarentena já tinha sido resolvida por
    uma pessoa, ou a conta nem chegou a sair: nada a fazer)."""

    instance_id: str
    resultado: str
    passo: str | None = None
    motivo: str | None = None
    antes: str | None = None
    depois: str | None = None
    energia: str = "nao_alterada"
    resolvidos: int = 0
    comando: dict[str, object] = field(default_factory=dict)


class LimpezaAoRetirar:
    """A tarefa de fundo da retirada. Uma instância por `AppState`; `agendar` é o gancho que o `SocialService` chama."""

    def __init__(self, s: AppState, *, dormir: Callable[[float], Awaitable[object]] = asyncio.sleep,
                 espera_aparelho_s: float = 300.0, espera_online_s: float = 420.0, passo_s: float = 2.0) -> None:
        self.s = s
        self._dormir = dormir
        #: Quanto esperar o aparelho ficar livre (outro trabalho, a pessoa no controle) e o boot depois de acordar.
        self.espera_aparelho_s, self.espera_online_s, self.passo_s = espera_aparelho_s, espera_online_s, passo_s
        self.desfechos: list[Desfecho] = []              # o último pedido, para teste e diagnóstico

    # ------------------------------------------------------------------ entrada
    def agendar(self, pedido: PedidoDeLimpeza) -> None:
        """Gancho síncrono do serviço social: põe a limpeza em tarefa de fundo (`_bg`, que o `stop()` cancela). Sem laço
        de eventos rodando não há onde agendar, e isso VAI ao serviço como erro (vira evento), não some."""
        tarefa = asyncio.get_running_loop().create_task(self.executar(pedido), name=f"limpeza-{pedido.account_id}")
        self.s._bg.append(tarefa)
        tarefa.add_done_callback(lambda t: self.s._bg.remove(t) if t in self.s._bg else None)

    async def executar(self, pedido: PedidoDeLimpeza) -> list[Desfecho]:
        """Todos os aparelhos do pedido, UM por vez. Nunca levanta (a conta já saiu): cada falha vira desfecho e evento."""
        self.desfechos = []
        for aparelho in pedido.aparelhos:
            try:
                desfecho = await self._um_aparelho(pedido, aparelho)
            except asyncio.CancelledError:
                raise
            except LimpezaFalhou as exc:
                desfecho = Desfecho(aparelho.instance_id, "falhou", passo=exc.passo, motivo=exc.motivo)
            except Exception as exc:  # noqa: BLE001 - não derruba os outros aparelhos nem a tarefa
                log.exception("limpeza de %s em %s", pedido.account_id, aparelho.instance_id)
                desfecho = Desfecho(aparelho.instance_id, "falhou", passo="inesperado", motivo=type(exc).__name__)
            self.desfechos.append(desfecho)
            self._anunciar(pedido, desfecho)
        return self.desfechos

    # ------------------------------------------------------------------ um aparelho
    async def _um_aparelho(self, pedido: PedidoDeLimpeza, aparelho: AparelhoDaLimpeza) -> Desfecho:
        s = self.s
        iid = aparelho.instance_id
        if s.social_repo.account_row(pedido.profile_id, pedido.account_id) is not None:
            # A retirada foi desfeita (a transação de fora voltou atrás): sem conta retirada não há o que limpar.
            return Desfecho(iid, "dispensada", motivo="a conta não saiu da plataforma")
        if aparelho.marcadores and not self._abertos(iid, aparelho):
            # Idempotência: uma pessoa (a rota do 29.24) já resolveu a quarentena enquanto a tarefa esperava a vez.
            return Desfecho(iid, "dispensada", motivo="a quarentena já foi resolvida por uma pessoa")
        rt = s.devices.devices.get(iid)
        if rt is None:
            raise LimpezaFalhou("aparelho", "o aparelho não está neste backend")
        partida = rt.state
        desfecho = Desfecho(iid, "falhou")
        acordou = False
        try:
            if partida != InstanceState.online:
                self._so_marcadores_do_pedido(iid, aparelho)      # antes de acordar: ver abaixo
                acordou = await self._pedir_energia_de_partida(rt, partida)
            await self._esperar_online(rt)
            resultado = await self._no_aparelho(
                rt, lambda: self._limpar(rt, pedido, aparelho), params={"package": pedido.package, "origem": "retirada_de_conta",
                                                              "account_id": pedido.account_id})
            assert isinstance(resultado, dict)
            desfecho.antes, desfecho.depois = resultado.get("antes"), resultado.get("depois")
            desfecho.comando = {"verbo": VERBO}
            # Só aqui, com o app limpo e as duas capturas feitas, a quarentena é da história.
            if aparelho.marcadores:
                desfecho.resolvidos = s.social_repo.resolver_conta_travada(
                    iid, por=QUEM, nota=NOTA, marcadores=aparelho.marcadores)
            desfecho.resultado = "concluida"
        except LimpezaFalhou as exc:
            desfecho.passo, desfecho.motivo = exc.passo, exc.motivo
        finally:
            if acordou:
                desfecho.energia = await self._devolver_energia(rt, partida)
        return desfecho

    def _abertos(self, instance_id: str, aparelho: AparelhoDaLimpeza) -> bool:
        """Algum dos marcadores capturados ainda está aberto neste aparelho?"""
        return any(int(m["id"]) in aparelho.marcadores for m in self.s.social_repo.contas_travadas_abertas()
                   if str(m["instance_id"]) == instance_id)

    def _so_marcadores_do_pedido(self, instance_id: str, aparelho: AparelhoDaLimpeza) -> None:
        """Trava de energia: acordar/ligar vai com `confirm_locked_account=True`, que passa por cima de QUALQUER marcador
        do aparelho, e não só dos da conta retirada. Se há marcador aberto de OUTRA conta ali, quem confirmou a
        quarentena (o dono, ao mandar retirar) não falou dessa: não acorda, e a quarentena segue para uma pessoa."""
        alheios = [m for m in self.s.social_repo.contas_travadas_abertas()
                   if str(m["instance_id"]) == instance_id and int(m["id"]) not in aparelho.marcadores]
        if alheios:
            raise LimpezaFalhou("outra_conta", "o aparelho tem quarentena aberta de outra conta: não acordei nem limpei")

    # ------------------------------------------------------------------ energia
    async def _pedir_energia_de_partida(self, rt: DeviceRuntime, partida: InstanceState) -> bool:
        """Acorda (hibernado) ou liga (parado) com a confirmação de quarentena do SISTEMA. `True` = pediu, e depois
        devolve. Estado que não é nenhum dos dois nem `online` (subindo, erro, ausente) só é esperado/recusado."""
        verbo = _ACORDAR.get(partida)
        if verbo is None:
            if partida == InstanceState.booting:
                return False                              # já subindo: só espera ficar online, e não mexe na energia
            raise LimpezaFalhou("acordar", f"o aparelho está '{partida.value}' e não dá para limpar o app agora")
        await self._pedir_energia(rt, verbo, confirmar_quarentena=True)
        return True

    async def _devolver_energia(self, rt: DeviceRuntime, partida: InstanceState) -> str:
        """Volta ao estado de energia de antes (hibernado hiberna, parado para). Falhar aqui não desfaz a limpeza: fica
        dito no desfecho e o aparelho segue ligado, como estava para quem o acordou."""
        try:
            await self._pedir_energia(rt, _DEVOLVER[partida], confirmar_quarentena=False)
        except LimpezaFalhou as exc:
            log.warning("limpeza em %s: não devolveu a energia (%s)", rt.id, exc.motivo)
            return f"nao_devolvida: {exc.motivo}"
        return "restaurada"

    async def _pedir_energia(self, rt: DeviceRuntime, verbo: str, *, confirmar_quarentena: bool) -> None:
        """Um verbo de ciclo de vida pelo mesmo despacho do painel (`abrir_e_despachar`: pré-voo, outbox, o worker que
        hospeda o aparelho — local ou remoto)."""
        corpo = InstanceActionBody(confirm=True, confirm_locked_account=confirmar_quarentena)
        try:
            await despacho.abrir_e_despachar(self.s, rt, verbo, corpo, requested_by=QUEM)
        except despacho.DespachoRecusado as exc:
            raise LimpezaFalhou("acordar" if confirmar_quarentena else "devolver_energia", exc.message) from exc

    async def _esperar_online(self, rt: DeviceRuntime) -> None:
        """Espera o aparelho ficar `online` (já o é, quando ninguém o acordou). Um prazo e fim: sem repetição."""
        espera = 0.0
        while rt.state != InstanceState.online:
            if rt.state in (InstanceState.error, InstanceState.absent) or espera >= self.espera_online_s:
                raise LimpezaFalhou("esperar_online", f"o aparelho não ficou online (está '{rt.state.value}')")
            await self._dormir(self.passo_s)
            espera += self.passo_s

    # ------------------------------------------------------------------ o trabalho
    async def _no_aparelho(self, rt: DeviceRuntime, trabalho: Callable[[], Awaitable[object]], *,
                           params: dict[str, object]) -> object:
        """Roda `trabalho` com o aparelho inteiro (`run_device_job`: exclusividade, rodízio, manutenção) como comando
        `session.logout`, e devolve o resultado. Aparelho ocupado (outro trabalho, a pessoa no controle, ou o próprio
        fluxo que detectou o bloqueio e ainda não soltou o aparelho) espera até `espera_aparelho_s` — a CHEGADA da vez,
        não uma nova tentativa do que falhou."""
        futuro: asyncio.Future[object] = asyncio.get_running_loop().create_future()

        async def no_trabalho() -> None:
            try:
                resultado = await despacho.comando_no_trabalho(
                    self.s, rt, VERBO, trabalho, params=params, requested_by=QUEM)
                futuro.set_result(resultado)
            except asyncio.CancelledError:
                futuro.cancel()
                raise
            except Exception as exc:  # noqa: BLE001 - o desfecho vai a quem espera; o comando já registrou a falha
                futuro.set_exception(exc)

        espera = 0.0
        while not self.s.scheduler.run_device_job(rt, no_trabalho, label="limpeza de app da conta retirada"):
            if espera >= self.espera_aparelho_s:
                raise LimpezaFalhou("aparelho_ocupado", "o aparelho não ficou livre a tempo")
            await self._dormir(self.passo_s)
            espera += self.passo_s
        try:
            return await futuro
        except LimpezaFalhou:
            raise
        except Exception as exc:  # noqa: BLE001 - qualquer falha do comando é falha do passo
            raise LimpezaFalhou("limpar", f"{type(exc).__name__}: {str(exc)[:200]}") from exc

    async def _limpar(self, rt: DeviceRuntime, pedido: PedidoDeLimpeza,
                      aparelho: AparelhoDaLimpeza) -> dict[str, str | None]:
        """Captura antes, `pm clear` do pacote do pedido (e de nenhum outro), captura depois. Sem toque na tela.
        A captura de antes que falha PARA aqui: não se apaga o que não ficou provado."""
        antes = await self._capturar(rt, "antes")
        # A trava, DENTRO do trabalho exclusivo do aparelho e logo antes do `pm clear`: o pedido foi montado na hora da
        # retirada e o aparelho pode ter passado a servir OUTRA conta desde então (vínculo novo, sessão, marcador). O
        # `pm clear` apaga o app todo, inclusive a conta viva de outra persona, e isso não se desfaz. Dúvida = recusa.
        if (pista := self.s.social_repo.outra_conta_no_aparelho(
                rt.id, account_id=pedido.account_id, app_id=pedido.app_id, package=pedido.package,
                marcadores_do_pedido=aparelho.marcadores)) is not None:
            raise LimpezaFalhou("outra_conta", f"o aparelho serve a outra conta do mesmo app ({pista}): não limpei; "
                                               "confira e resolva pela rota do aparelho")
        await rt.executor.run(rt.adb.clear_data, pedido.package, timeout=120, label="apagar dados do app")
        rt.app_versions.clear()
        depois = await self._capturar(rt, "depois")
        return {"antes": antes, "depois": depois}

    async def _capturar(self, rt: DeviceRuntime, quando: str) -> str:
        """Um screenshot (só leitura, pelo IO do aparelho) guardado no armazém de evidências; devolve a chave."""
        try:
            png = await rt.executor.run(rt.io.screenshot_png, timeout=30, label=f"captura {quando} da limpeza")
            chave = f"limpeza-de-conta/{rt.id}/{now_iso().replace(':', '').replace('.', '')}_{quando}.png"
            return await put_async(self.s.storage, chave, png, content_type="image/png")
        except Exception as exc:  # noqa: BLE001 - sem a captura não há prova: o passo falha
            raise LimpezaFalhou(f"captura_{quando}", f"{type(exc).__name__}: {str(exc)[:200]}") from exc

    # ------------------------------------------------------------------ aviso
    def _anunciar(self, pedido: PedidoDeLimpeza, d: Desfecho) -> None:
        """Um evento por aparelho. Em erro é o aviso de ATENÇÃO (a quarentena segue aberta); sem o @ da conta."""
        dados: dict[str, object] = {
            "profile_id": pedido.profile_id, "account_id": pedido.account_id, "package": pedido.package,
            "instance_id": d.instance_id, "resultado": d.resultado, "passo": d.passo, "motivo": d.motivo,
            "antes": d.antes, "depois": d.depois, "energia": d.energia, "resolvidos": d.resolvidos}
        if d.resultado == "falhou":
            self.s.bus.emit("device.account_cleanup",
                            f"{d.instance_id}: a limpeza do app da conta retirada NÃO se completou ({d.passo}: "
                            f"{d.motivo}). A quarentena continua aberta: confira o aparelho e resolva pela rota.",
                            level="error", instance_id=d.instance_id, data=dados)
        elif d.resultado == "dispensada":
            self.s.bus.emit("device.account_cleanup", f"{d.instance_id}: limpeza dispensada ({d.motivo}).",
                            level="info", instance_id=d.instance_id, data=dados)
        else:
            self.s.bus.emit("device.account_cleanup",
                            f"{d.instance_id}: app da conta retirada limpo ({pedido.package}); quarentena resolvida "
                            f"({NOTA}); energia: {d.energia}.", level="warn", instance_id=d.instance_id, data=dados)

"""31.302: o laço da releitura PERIÓDICA da sessão da conta âncora (as regras estão em
`application/verificacao_periodica.py`; a causa e o desenho, lá).

O que este arquivo faz é só ligar a seleção ao parque: lê as sessões prontas vencidas, avalia os pulos (aparelho livre,
host folgado, sem quarentena) e despacha UMA releitura `observe_only` pelo mesmo caminho da rota "Verificar conta"
(`pedir_trabalho_de_app`, verbo `session.verify`, o comando fica no histórico com autor `verificacao-periodica`). O desafio
visto na tela cai em `session_rules.bloquear_por_desafio`/`aplicar_desafio` dentro do motor de sessão (ADR-068): nada aqui
bloqueia conta.

Nunca liga aparelho, nunca digita, nunca chama IA. Com `contas.verificacao_periodica_h: 0` (o padrão) o laço só dorme.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING

from app.commands.despacho import VERBOS_EXCLUSIVOS, pedir_trabalho_de_app
from app.models import ControlOwner, InstanceState, SessionStatus
from app.modules.identity.application import verificacao_periodica as regras
from app.modules.identity.application.verificacao_periodica import Alvo
from app.planning.catalog import pacote_ancora
from app.util import now, to_iso

if TYPE_CHECKING:  # pragma: no cover - só para o verificador de tipos
    from app.devices.manager import DeviceRuntime
    from app.state import AppState

log = logging.getLogger(__name__)

EVENTO = "session.verificacao_periodica"
AUTOR = "verificacao-periodica"


class VerificacaoPeriodica:
    def __init__(self, s: AppState):
        self.s = s
        #: (conta, aparelho) → quando (monotônico) a releitura foi despachada. A tentativa que não leu a tela (erro do
        #: driver) não volta a cada tique: espera `_espera_s`.
        self._tentadas: dict[tuple[str, str], float] = {}
        #: (conta, aparelho) → o último motivo de pulo já registrado: só a MUDANÇA vira evento (o tique é de minutos).
        self._ultimo_pulo: dict[tuple[str, str], str] = {}
        #: A releitura em curso (a volta seguinte não despacha outra enquanto o aparelho dela ainda trabalha).
        self._em_voo: str | None = None

    # ------------------------------------------------------------------ configuração viva
    def horas(self) -> int:
        return int(self.s.cfg.file.contas.verificacao_periodica_h)

    def _espera_s(self) -> float:
        """Depois de despachar, a mesma conta só volta em meia janela (no máximo 1 h): uma releitura que falha não vira
        martelada a cada tique, e uma que leu avança o `verified_at`, que já a tira da fila por `horas()`."""
        return min(3600.0, self.horas() * 3600.0 / 2)

    # ------------------------------------------------------------------ o laço
    async def laco(self) -> None:
        while True:
            try:
                await self.uma_volta()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - uma volta quebrada nunca derruba o laço
                log.exception("verificação periódica da sessão: volta falhou")
            await asyncio.sleep(max(30, int(self.s.cfg.file.contas.verificacao_periodica_tique_s)))

    async def uma_volta(self) -> Alvo | None:
        """Uma volta: devolve o alvo despachado, ou `None` (desligada, nada vencido, tudo pulado ou uma releitura em curso)."""
        horas = self.horas()
        if horas <= 0:
            return None
        if self._em_voo is not None:
            if self._em_voo in self.s.scheduler.workers:
                return None                                              # uma por vez, no parque inteiro
            self._em_voo = None
        antes_de = to_iso(now() - timedelta(hours=horas))
        vencidas = await asyncio.to_thread(self.s.social_repo.sessoes_prontas_a_reler, antes_de)
        alvos = [Alvo(str(r["profile_id"]), str(r["account_id"]), str(r["instance_id"]), r["verified_at"]) for r in vencidas]
        escolhido, pulados = regras.escolher(alvos, self._motivo_de_pulo)
        for alvo, motivo in pulados:
            self._registrar_pulo(alvo, motivo)
        if escolhido is None:
            return None
        return escolhido if self._despachar(escolhido) else None

    # ------------------------------------------------------------------ quando pular
    def _motivo_de_pulo(self, alvo: Alvo) -> str | None:
        s = self.s
        pacote = s.social_repo.pacote_da_conta(alvo.profile_id, alvo.account_id)
        if pacote is None or pacote != pacote_ancora():
            return regras.NAO_E_ANCORA
        if s.sessoes.for_package(pacote) is None:
            return regras.SEM_PROVEDOR
        rt = s.devices.devices.get(alvo.instance_id)
        if rt is None or rt.state != InstanceState.online:
            return regras.APARELHO_FORA_DO_AR                            # nunca liga aparelho para verificar
        if rt.control != ControlOwner.none:
            return regras.CONTROLE_MANUAL
        if rt.id in s.scheduler.workers or s.commands.open_for_instance(rt.id, verbs=set(VERBOS_EXCLUSIVOS)) is not None:
            return regras.APARELHO_OCUPADO
        if s.quarentena(rt.id) is not None:
            return regras.QUARENTENA
        if s.devices.pausa_de_reparo(rt) is not None:
            return regras.PAUSA_DE_REPARO
        if rt.worker_id and s.scheduler.worker_gate and s.scheduler.worker_gate(rt.worker_id) is not None:
            return regras.WORKER_EM_MANUTENCAO
        if s.scheduler.cpu_do_host_acima(rt, float(s.cfg.file.contas.verificacao_periodica_cpu_max_percent)) is not None:
            return regras.HOST_CARREGADO
        if (feita := self._tentadas.get((alvo.account_id, alvo.instance_id))) is not None \
                and time.monotonic() - feita < self._espera_s():
            return regras.TENTADA_HA_POUCO
        return self._motivo_do_portao(alvo, rt)

    def _motivo_do_portao(self, alvo: Alvo, rt: DeviceRuntime) -> str | None:
        """A MESMA regra que decide o botão "Verificar conta" (`session_actions.verify`): app ausente, em instalação, sessão
        em andamento. A conta de site não passa (a releitura do app não a vê)."""
        try:
            conta = self.s.social.get_account(alvo.profile_id, alvo.account_id, rt.id)
        except Exception:  # noqa: BLE001 - sem ler a conta não há como saber o portão: pular é o lado seguro
            return regras.PORTAO
        if conta.host:
            return regras.NAO_E_ANCORA
        acoes = conta.session_actions
        if acoes is not None and not acoes.verify.allowed:
            return regras.PORTAO
        return None

    # ------------------------------------------------------------------ despacho e eventos
    def _despachar(self, alvo: Alvo) -> bool:
        s = self.s
        rt = s.devices.devices[alvo.instance_id]
        pacote = s.social_repo.pacote_da_conta(alvo.profile_id, alvo.account_id)
        provedor = s.sessoes.for_package(pacote)
        assert provedor is not None                      # o `_motivo_de_pulo` acabou de conferir
        chave = (alvo.account_id, alvo.instance_id)

        async def reler() -> None:
            antes = self._status(alvo)
            try:
                await provedor.ensure_session(rt, alvo.profile_id, account_id=alvo.account_id, observe_only=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._emitir(alvo, "erro", anterior=antes, atual=self._status(alvo))
                raise
            atual = self._status(alvo)
            self._ultimo_pulo.pop(chave, None)
            self._emitir(alvo, "verificada" if atual == SessionStatus.session_ready.value else "mudou",
                         anterior=antes, atual=atual)

        resposta = pedir_trabalho_de_app(
            s, rt, "session.verify", reler, label="verificação periódica da sessão",
            params={"profile_id": alvo.profile_id, "account_id": alvo.account_id},
            idempotency_key=f"{AUTOR}:{alvo.account_id}:{alvo.instance_id}:{int(time.time() // max(600, self._espera_s()))}",
            requested_by=AUTOR)
        if not resposta.get("accepted"):
            self._registrar_pulo(alvo, regras.APARELHO_OCUPADO)
            return False
        self._tentadas[chave] = time.monotonic()
        self._em_voo = rt.id
        return True

    def _status(self, alvo: Alvo) -> str | None:
        linha = self.s.social_repo.account_session_row(alvo.profile_id, alvo.account_id, alvo.instance_id)
        return str(linha["status"]) if linha is not None else None

    def _registrar_pulo(self, alvo: Alvo, motivo: str) -> None:
        chave = (alvo.account_id, alvo.instance_id)
        if self._ultimo_pulo.get(chave) == motivo:
            return
        self._ultimo_pulo[chave] = motivo
        self._emitir(alvo, "pulada", motivo=motivo)

    def _emitir(self, alvo: Alvo, resultado: str, *, motivo: str | None = None, anterior: str | None = None,
                atual: str | None = None) -> None:
        """Só ids e códigos fechados: nunca o @, o nome da pessoa nem texto da tela."""
        dados: dict[str, object] = {"profile_id": alvo.profile_id, "account_id": alvo.account_id,
                                    "instance_id": alvo.instance_id, "resultado": resultado}
        if motivo is not None:
            dados["motivo"] = motivo
        if anterior is not None:
            dados["anterior"] = anterior
        if atual is not None:
            dados["status"] = atual
        if resultado == "mudou":
            dados["perfil"] = self.s.db.scalar("SELECT status FROM instagram_profiles WHERE id=?", (alvo.profile_id,))
        frase = {"verificada": "sessão relida, segue pronta", "mudou": f"a releitura achou a sessão em {atual}",
                 "erro": "a releitura não terminou", "pulada": f"releitura pulada ({motivo})"}[resultado]
        try:
            self.s.bus.emit(EVENTO, f"{alvo.instance_id}: verificação periódica da sessão — {frase}",
                            level="warn" if resultado in ("mudou", "erro") else "info",
                            instance_id=alvo.instance_id, data=dados)
        except Exception:  # noqa: BLE001 - o evento nunca derruba a releitura
            log.exception("verificação periódica: evento não emitido")

"""O serviço do aviso fora do painel: ouve o barramento, enfileira e entrega (item 28.11).

Dois papéis no mesmo laço, com garantias diferentes:

- **Enfileirar** roda em todo backend que vê o evento (o `EventBus` entrega também o que as outras réplicas
  publicaram). É seguro porque a chave da linha é a do FATO e a fila é `UNIQUE (chave)`: o mesmo fato visto por dois
  backends é uma linha.
- **Entregar** só roda no líder da trava `avisos` (migração 066). O envio não é idempotente; a trava é o que impede
  dois backends de mandarem a mesma mensagem, e o token dela cerca a reivindicação da linha (ver `fila_sql`).

Desligado (`avisos.enabled: false`) ou sem segredo, o serviço não enfileira nem envia: um aviso que ficasse na fila
até o dono configurar sairia horas depois como notícia velha. Segredo faltando com o canal ligado é um Problem na
saúde (`avisos_sem_segredo`), não uma exceção.

Limite conhecido: o que acontece enquanto o backend está parado não é reenviado na partida (o laço começa no presente
do barramento). A caixa de Pendências do painel continua sendo a fonte da verdade; o aviso é o empurrão.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from app.config import Config
from app.events import EventBus
from app.models import Problem
from app.modules.avisos.adapters.telegram import CanalTelegram
from app.modules.avisos.application.entrega import Canal, Resultado, entregar
from app.modules.avisos.domain.mensagem import aviso_de_evento
from app.modules.avisos.infrastructure.fila_sql import FilaDeAvisos
from app.taskqueue.travas import AVISOS, Lideranca, TravaPerdida

log = logging.getLogger("poc.avisos")

#: Eventos que podem virar aviso. O filtro barato antes de montar a mensagem.
KINDS_QUE_AVISAM = frozenset({"approval.pending", "run.updated", "session.needs_person", "pedido.aviso",
                              "learning.needs_person"})
#: De quanto em quanto tempo o laço varre incertos, vencidos e purga (a entrega roda a cada volta).
FAXINA_S = 3600.0


class ServicoDeAvisos:
    def __init__(self, cfg: Config, bus: EventBus, fila: FilaDeAvisos, lideranca: Lideranca, *,
                 canal: Canal | None = None, lider: Callable[[str], int | None] | None = None,
                 redigir: Callable[[str], str] | None = None):
        self.cfg = cfg
        self.bus = bus
        self.fila = fila
        self.lideranca = lideranca
        self._canal = canal
        #: O redator do conteúdo do aviso (28.15, decisão (d)): só vale com a conversa de volta ligada.
        self._redigir = redigir
        #: Quem diz se sou o líder: o `AppState._lider` (que tolera banco fora do ar). Injetável nos testes.
        self._lider = lider or self._tomar
        self._esperar_ate = 0.0
        self._faxina_em = 0.0

    # ------------------------------------------------------------------ configuração e saúde
    def _segredos(self) -> tuple[str, str]:
        env = self.cfg.env
        token = env.telegram_bot_token.get_secret_value() if env.telegram_bot_token else ""
        chat = env.telegram_chat_id.get_secret_value() if env.telegram_chat_id else ""
        return token.strip(), chat.strip()

    @property
    def ligado(self) -> bool:
        return bool(self.cfg.file.avisos.enabled)

    def canal(self) -> Canal | None:
        """O canal pronto para enviar, ou `None` (desligado ou segredo faltando). Criado sob demanda: o `.env` só é
        relido na partida, e um canal montado antes do token existir não pode ficar preso ao vazio."""
        if not self.ligado:
            return None
        if self._canal is not None:
            return self._canal
        token, chat = self._segredos()
        if not token or not chat:
            return None
        return CanalTelegram(token, chat, timeout_s=self.cfg.file.avisos.timeout_s)

    def problemas(self) -> list[Problem]:
        if not self.ligado or self._canal is not None:
            return []
        token, chat = self._segredos()
        faltam = [nome for nome, valor in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat)) if not valor]
        if not faltam:
            return []
        return [Problem(
            code="avisos_sem_segredo",
            message=f"Aviso fora do painel ligado (avisos.enabled), mas falta no .env: {', '.join(faltam)}.",
            hint="Siga o procedimento de docs/operacao.md (Aviso fora do painel) e reinicie a tarefa farm-central. "
                 "Enquanto faltar, nenhum aviso é enviado; as pendências continuam na caixa do painel.")]

    # ------------------------------------------------------------------ entrada
    def enfileirar_evento(self, kind: str, data: dict[str, object] | None, evento_id: int | None) -> bool:
        """Transforma um evento em aviso e o enfileira. Devolve se entrou linha nova."""
        if kind not in KINDS_QUE_AVISAM or not self.ligado or self.canal() is None:
            return False
        cfg = self.cfg.file.avisos
        # Com a conversa de volta ligada, a aprovação e a pergunta levam o conteúdo redigido (decisão (d) do ADR-071):
        # o dono responde ali mesmo. Desligada, a mensagem segue a menor possível (28.11).
        redigir = self._redigir if cfg.entrada.enabled else None
        aviso = aviso_de_evento(kind, data, evento_id, cfg.url_painel, frozenset(cfg.aprendizado_faixas), redigir)
        if aviso is None or self._e_de_prova(kind, data):
            return False
        try:
            return self.fila.enfileirar(aviso)
        except Exception:  # noqa: BLE001 - o aviso nunca derruba o emissor nem o laço
            log.exception("avisos: não foi possível enfileirar %s", aviso.chave)
            return False

    def _e_de_prova(self, kind: str, data: dict[str, object] | None) -> bool:
        """30.37: a execução de PROVA de fluxo (a validação do curador) é do sistema, não de uma pessoa: nada dela vira
        aviso ao dono, nem a pergunta (`run.updated` em `needs_input`) nem a aprovação que ela abriria. A prova se
        reconhece pelo fluxo que prova (`runs.prova_fluxo_id`) ou pela chave de idempotência da validação
        (`validacao:`). Só consulto o banco para o evento que AVISARIA; falha na consulta deixa o aviso seguir (o dono
        recebe um aviso a mais, nunca perde um de pessoa)."""
        if kind not in ("run.updated", "approval.pending"):
            return False
        filho = (data or {}).get("run" if kind == "run.updated" else "approval")
        run_id = filho.get("id" if kind == "run.updated" else "run_id") if isinstance(filho, dict) else None
        if isinstance(filho, dict) and filho.get("prova_fluxo_id"):
            return True
        if not isinstance(run_id, str) or not run_id:
            return False
        try:
            return self.fila.db.one(
                "SELECT 1 FROM runs WHERE id=? AND (prova_fluxo_id IS NOT NULL OR idempotency_key LIKE 'validacao:%')",
                (run_id,)) is not None
        except Exception:  # noqa: BLE001 - ver a docstring: na dúvida, avisa
            log.exception("avisos: não foi possível conferir se a execução %s é de prova", run_id)
            return False

    # ------------------------------------------------------------------ saída (líder)
    def _tomar(self, nome: str) -> int | None:
        try:
            return self.lideranca.tomar(nome)
        except Exception:  # noqa: BLE001 - sem banco não há como saber quem é o líder: pular é o lado seguro
            log.exception("trava %s: não foi possível conferir o líder", nome)
            return None

    async def entregar_uma_vez(self) -> Resultado | None:
        """Uma volta de entrega, só no líder e com canal pronto. `None` quando pulou."""
        canal = self.canal()
        if canal is None or time.monotonic() < self._esperar_ate:
            return None
        token = self._lider(AVISOS)
        if token is None:
            return None
        cfg = self.cfg.file.avisos

        def cerca():  # noqa: ANN202 - context manager do mandato
            return self.lideranca.cercada(AVISOS, token)

        try:
            self._faxina(cerca)
            resultado = await entregar(self.fila, canal, cerca=cerca, agora=self.fila.relogio, limite=cfg.lote,
                                       max_tentativas=cfg.max_tentativas, backoff_s=cfg.backoff_s)
        except TravaPerdida as exc:
            log.warning("avisos: entrega recusada, %s", exc)
            return None
        except Exception:  # noqa: BLE001 - o canal fora do ar não derruba o processo
            log.exception("avisos: volta de entrega")
            return None
        if resultado.esperar_s is not None:
            self._esperar_ate = time.monotonic() + resultado.esperar_s
        if resultado.falharam:
            log.warning("avisos: %d aviso(s) falharam de vez nesta volta", resultado.falharam)
        return resultado

    def _faxina(self, cerca: Callable[[], object]) -> None:
        if time.monotonic() < self._faxina_em:
            return
        cfg = self.cfg.file.avisos
        incertos = self.fila.varrer_incertos(cerca=cerca, parado_ha_s=cfg.incerto_apos_s)      # type: ignore[arg-type]
        vencidos = self.fila.vencer(cerca=cerca, validade_h=cfg.validade_h)                    # type: ignore[arg-type]
        purgados = self.fila.purgar(cerca=cerca, retencao_dias=cfg.retencao_dias)              # type: ignore[arg-type]
        if incertos:
            log.warning("avisos: %d envio(s) interrompido(s) por queda ficaram como incertos (não reenviados)", incertos)
        if vencidos or purgados:
            log.info("avisos: %d vencido(s) e %d antigo(s) purgado(s)", vencidos, purgados)
        self._faxina_em = time.monotonic() + FAXINA_S

    # ------------------------------------------------------------------ laço
    async def laco(self) -> None:
        """Escuta o barramento e entrega a cada `intervalo_s`. Assinatura que o barramento descartou (consumidor lento)
        é refeita lendo o que ficou para trás por `since`: o dedupe por chave torna a releitura inofensiva."""
        fila_de_eventos = self.bus.subscribe()
        ultimo = self.bus.last_id()
        while True:
            try:
                if not self.bus.is_subscribed(fila_de_eventos):
                    fila_de_eventos = self.bus.subscribe()
                    for rec in self.bus.since(ultimo):
                        self.enfileirar_evento(rec.kind, rec.data, rec.id)
                        ultimo = max(ultimo, rec.id or 0)
                intervalo = float(self.cfg.file.avisos.intervalo_s)
                try:
                    rec = await asyncio.wait_for(fila_de_eventos.get(), timeout=intervalo)
                except asyncio.TimeoutError:
                    rec = None
                while rec is not None:
                    self.enfileirar_evento(rec.kind, rec.data, rec.id)
                    ultimo = max(ultimo, rec.id or 0)
                    rec = fila_de_eventos.get_nowait() if not fila_de_eventos.empty() else None
                await self.entregar_uma_vez()
            except asyncio.CancelledError:
                self.bus.unsubscribe(fila_de_eventos)
                raise
            except Exception:  # noqa: BLE001 - o laço de aviso nunca morre
                log.exception("avisos: laço")
                await asyncio.sleep(5)

